"""Turns a carousel's slides into a 9:16 Reel (MP4, H.264 + AAC) with music.

Each slide sits as a rounded card in the middle of a dark 1080x1920 frame
(MISFIT black), zooms in very slightly while it's on screen, and swipes to the
next one like a carousel. A row of progress bars at the bottom shows how far along
the viewer is. Slides stay on screen long enough to read (based on their word
count).

Music: if assets/music/ has .mp3/.m4a/.wav files (tracks you have the right to
use, for example from YouTube's Audio Library), one is picked per post.
Otherwise an original lo-fi loop is generated in code, so there are never any
licence problems. Instagram's own music library can't be added through the API.

Uses the ffmpeg that comes with the imageio-ffmpeg package (in
requirements.txt), or the system ffmpeg if that isn't installed."""
import hashlib
import math
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .common import ASSETS

W, H, FPS = 1080, 1920, 30
SR = 48000
SWIPE = 0.38  # seconds per swipe between slides


# ------------------------------------------------------------------ timing

def slide_seconds(words: int, kind: str) -> float:
    if kind == "cover":
        return 2.8
    secs = 1.6 + 0.24 * words
    if kind == "takeaway":
        secs += 0.8
    return max(2.6, min(6.0, secs))


# ------------------------------------------------------------------ music

def _ep_note(freq, dur, sr=SR):
    """A soft electric-piano-ish note: a few harmonics with a gentle decay."""
    t = np.arange(int(dur * sr)) / sr
    env = np.exp(-t * 1.6) * (1 - np.exp(-t * 60))
    wave_ = (np.sin(2 * np.pi * freq * t) + 0.35 * np.sin(2 * np.pi * 2 * freq * t)
             + 0.12 * np.sin(2 * np.pi * 3 * freq * t + 0.3))
    trem = 1 + 0.06 * np.sin(2 * np.pi * 4.2 * t)
    return wave_ * env * trem


def _add(mix, sample, start):
    if start >= len(mix):
        return
    end = min(len(mix), start + len(sample))
    mix[start:end] += sample[: end - start]


def _lowpass(x, alpha):
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # small and simple; fine for a 30 s track
        acc += alpha * (x[i] - acc)
        y[i] = acc
    return y


def lofi_track(seconds: float, seed: int = 0) -> np.ndarray:
    """An original lo-fi loop: jazzy chords, soft drums, a little vinyl crackle."""
    rng = np.random.default_rng(seed)
    bpm = 78 + int(rng.integers(0, 10))
    beat = 60 / bpm
    n = int((seconds + 1) * SR)
    mix = np.zeros(n)
    progressions = [
        [(65.41, [0, 4, 7, 11]), (55.00, [0, 3, 7, 10]), (58.27, [0, 4, 7, 11]), (49.00, [0, 4, 7, 10])],  # Cmaj7 Am7 Bbmaj7 G7
        [(43.65, [0, 4, 7, 11]), (41.20, [0, 3, 7, 10]), (36.71, [0, 3, 7, 10]), (32.70, [0, 4, 7, 11])],  # Fmaj7 Em7 Dm7 Cmaj7
        [(73.42, [0, 3, 7, 10]), (49.00, [0, 4, 7, 10]), (65.41, [0, 4, 7, 11]), (55.00, [0, 3, 7, 10])],  # Dm7 G7 Cmaj7 Am7
    ]
    prog = progressions[int(rng.integers(0, len(progressions)))]
    bar = beat * 4
    t0 = 0.0
    k = 0
    while t0 < seconds + 1:
        root, ivs = prog[k % len(prog)]
        for i, iv in enumerate(ivs):  # chord, lightly strummed
            f = root * 4 * 2 ** (iv / 12)
            note = _ep_note(f, bar * 1.05) * 0.16
            _add(mix, note, int((t0 + i * 0.012) * SR))
        bass = _ep_note(root * 2, beat * 1.8) * 0.28  # bass on beats 1 and 3
        for b in (0, 2):
            _add(mix, bass, int((t0 + b * beat) * SR))
        t0 += bar
        k += 1
    # drums
    tt = np.arange(int(0.35 * SR)) / SR
    kick = np.sin(2 * np.pi * (45 + 70 * np.exp(-tt * 30)) * tt) * np.exp(-tt * 9) * 0.55
    sn = rng.normal(0, 1, int(0.22 * SR)) * np.exp(-np.arange(int(0.22 * SR)) / SR * 22) * 0.13
    hat = rng.normal(0, 1, int(0.05 * SR)) * np.exp(-np.arange(int(0.05 * SR)) / SR * 90) * 0.05
    hat = hat - _lowpass(hat, 0.5)  # keep the hiss bright
    step = beat / 2
    for i in range(int((seconds + 1) / step)):
        s = int(i * step * SR + (0.018 * SR if i % 2 else 0))  # a little swing
        for sample, on in ((kick, i % 8 in (0, 5)), (sn, i % 8 in (2, 6)), (hat, True)):
            if on:
                _add(mix, sample, s)
    # vinyl crackle and hiss
    crackle = np.zeros(n)
    idx = rng.integers(0, n, int(seconds * 18))
    crackle[idx] = rng.normal(0, 0.12, len(idx))
    mix += crackle + rng.normal(0, 0.004, n)
    mix = _lowpass(mix, 0.32)  # warm it up
    mix = mix[: int(seconds * SR)]
    fade_in, fade_out = int(0.4 * SR), int(1.6 * SR)
    mix[:fade_in] *= np.linspace(0, 1, fade_in)
    mix[-fade_out:] *= np.linspace(1, 0, fade_out)
    return mix / (np.max(np.abs(mix)) + 1e-9) * 0.82


def write_wav(path: Path, mono: np.ndarray):
    stereo = np.repeat((mono * 32767).astype(np.int16)[:, None], 2, axis=1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return "ffmpeg"


def instagram_safe(src: Path) -> Path:
    """A copy that meets Instagram's Reels rules (AAC at 128 kbit/s or less, no
    MP4 edit lists, moov at the front). The picture is copied as it is, so
    this takes a second or two. Older Reels were made before these rules."""
    import tempfile
    out = Path(tempfile.gettempdir()) / (Path(src).parent.name + "_ig.mp4")  # never committed
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", str(SR),
           "-movflags", "+faststart", "-use_editlist", "0", str(out)]
    if subprocess.run(cmd).returncode != 0:
        raise RuntimeError("ffmpeg could not prepare the Reel for Instagram")
    return out


def pick_music(seed: str):
    folder = ASSETS / "music"
    tracks = sorted(p for p in folder.glob("*") if p.suffix.lower() in (".mp3", ".m4a", ".wav", ".aac")) if folder.exists() else []
    if not tracks:
        return None
    return tracks[int(hashlib.sha1(seed.encode()).hexdigest(), 16) % len(tracks)]


# ------------------------------------------------------------------ frames

def _ease(x):
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, x)))


BG = (17, 17, 17)
CARD_W = 1000
CARD_H = int(CARD_W * 1350 / 1080)
CARD_X = (W - CARD_W) // 2
CARD_Y = (H - CARD_H) // 2 - 10


def _round_mask(w, h, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return m


def _frame_base(logo_img, handle_font, handle):
    f = Image.new("RGB", (W, H), BG)
    if logo_img is not None:
        f.paste(logo_img, (CARD_X, CARD_Y - 120))
    if handle and handle_font is not None:
        ImageDraw.Draw(f).text((CARD_X + 76 + 20, CARD_Y - 120 + 38), handle, font=handle_font, fill=(241, 235, 223),
                               anchor="lm")
    return f


def make_reel(slide_paths, out_path: Path, words=None, kinds=None, accent=(255, 77, 46), handle="misfit_marketers",
              seed="misfit", log=print) -> Path:
    from . import styles  # fonts and the logo
    slides = [Image.open(p).convert("RGB") for p in slide_paths]
    n = len(slides)
    words = words or [12] * n
    kinds = kinds or (["cover"] + ["body"] * (n - 2) + ["takeaway"])
    durs = [slide_seconds(words[i], kinds[i]) for i in range(n)]
    total = sum(durs)
    starts = np.cumsum([0] + durs[:-1])
    logo_img = styles.logo_tile(76, box=(241, 235, 223), ink=(17, 17, 17))
    hfont = styles.body(34, "SemiBold")
    bars_y = CARD_Y + CARD_H + 60
    mask = _round_mask(CARD_W, CARD_H, 28)
    slides = [s.resize((CARD_W, CARD_H), Image.LANCZOS) for s in slides]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    music = pick_music(seed)
    wav = out_path.with_suffix(".wav")
    if music is None:
        write_wav(wav, lofi_track(total, seed=int(hashlib.sha1(seed.encode()).hexdigest(), 16) % 10_000))
        audio_in = ["-i", str(wav)]
    else:
        audio_in = ["-stream_loop", "-1", "-i", str(music)]
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", *audio_in, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-maxrate", "2M", "-bufsize", "4M",
           "-pix_fmt", "yuv420p", "-profile:v", "high", "-g", "60", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", str(SR),
           "-af", f"afade=t=out:st={max(0, total - 1.6):.2f}:d=1.6", "-t", f"{total:.2f}", "-movflags", "+faststart",
           "-use_editlist", "0", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames = int(total * FPS)
    cache = {}

    def zoomed(i, z):
        key = (i, round(z, 3))
        if key not in cache:
            img = slides[i]
            if z > 1.0005:
                zw, zh = int(CARD_W * z), int(CARD_H * z)
                img = img.resize((zw, zh), Image.BILINEAR).crop(((zw - CARD_W) // 2, (zh - CARD_H) // 2,
                                                                   (zw - CARD_W) // 2 + CARD_W, (zh - CARD_H) // 2 + CARD_H))
            cache[key] = img
            if len(cache) > 80:
                cache.pop(next(iter(cache)))
        return cache[key]

    for fi in range(frames):
        t = fi / FPS
        i = int(np.searchsorted(starts, t, side="right") - 1)
        local = t - starts[i]
        z = 1.0 + 0.035 * (local / durs[i])
        swipe = None
        if i < n - 1 and local > durs[i] - SWIPE:
            swipe = _ease((local - (durs[i] - SWIPE)) / SWIPE)
        frame = _frame_base(logo_img, hfont, handle)
        if swipe is None:
            frame.paste(zoomed(i, z), (CARD_X, CARD_Y), mask)
        else:
            dx = int((CARD_W + 40) * swipe)
            frame.paste(zoomed(i, z), (CARD_X - dx, CARD_Y), mask)
            frame.paste(zoomed(i + 1, 1.0), (CARD_X + CARD_W + 40 - dx, CARD_Y), mask)
        # progress bars, like stories
        d = ImageDraw.Draw(frame)
        gap, x0 = 10, CARD_X
        seg = (CARD_W - gap * (n - 1)) / n
        track = (70, 66, 62)
        for k in range(n):
            xa = x0 + k * (seg + gap)
            d.rounded_rectangle((xa, bars_y, xa + seg, bars_y + 8), radius=4, fill=track)
            done = 1.0 if k < i else (min(1.0, local / durs[i]) if k == i else 0.0)
            if done > 0:
                d.rounded_rectangle((xa, bars_y, xa + max(8, seg * done), bars_y + 8), radius=4, fill=accent)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg could not build the Reel")
    if wav.exists():
        wav.unlink()
    mb = out_path.stat().st_size / 1e6
    if mb > 19:  # jsDelivr, which hands the video to Instagram, serves files up to 20 MB
        raise RuntimeError(f"The Reel came out at {mb:.0f} MB, too big to post")
    log(f"Reel ready: {out_path.name}, {total:.1f}s, {n} slides, music: {music.name if music else 'original lo-fi loop'}")
    return out_path
