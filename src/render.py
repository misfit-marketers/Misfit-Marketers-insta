"""Turns a post (hook, body slides, takeaway) into 1080x1350 (4:5) JPEG slides
that match the MISFIT look: solid black or light-grey background, logo top
left, letter-spaced series tag at the top, centred text with **bold** words."""
import re
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .common import ASSETS

W, H = 1080, 1350   # Instagram's recommended portrait carousel size (4:5)
THEMES = {
    "light": {"bg": (217, 217, 217), "fg": (0, 0, 0), "logo": "logo_light.png"},
    "dark": {"bg": (0, 0, 0), "fg": (255, 255, 255), "logo": "logo_dark.png"},
}

FONT_DIR = ASSETS / "fonts"


def _font_path(kind: str) -> Path:
    """Prefer Glacial Indifference (the original Canva font) if you add it to
    assets/fonts, otherwise use the bundled Kumbh Sans look-alike."""
    options = {
        "body": ["GlacialIndifference-Regular.otf", "GlacialIndifference-Regular.ttf", "KumbhSans-Light.ttf"],
        "bold": ["GlacialIndifference-Bold.otf", "GlacialIndifference-Bold.ttf", "KumbhSans-Bold.ttf"],
        "hook": ["OpenSans-ExtraBold.ttf"],
        "tag": ["GlacialIndifference-Regular.otf", "GlacialIndifference-Regular.ttf", "KumbhSans-Light.ttf"],
    }[kind]
    for name in options:
        p = FONT_DIR / name
        if p.exists():
            return p
    raise FileNotFoundError(f"No font found for {kind} in {FONT_DIR}")


_font_cache = {}
_charset = None

# Look-alike swaps for characters AI writers like to use but fonts often lack.
SWAPS = {
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-", "\u00ad": "",
    "\u2014": ", ", "\u2015": ", ",
    "\u00a0": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ", "\u202f": " ", "\u2002": " ", "\u2003": " ",
    "\u200b": "", "\u200c": "", "\u200d": "", "\u2060": "", "\ufeff": "",
    "\u20b9": "Rs ", "\u2026": "...", "\u2032": "'", "\u2033": '"', "\u00d7": "x",
}


def supported_chars() -> set:
    """Characters every font we draw with actually contains."""
    global _charset
    if _charset is None:
        try:
            from fontTools.ttLib import TTFont
            sets = [set(TTFont(str(_font_path(k))).getBestCmap()) for k in ("body", "bold", "hook")]
            _charset = set.intersection(*sets)
        except Exception:
            _charset = set(range(32, 127))  # plain ASCII if fontTools is missing
    return _charset


def safe_text(text: str) -> str:
    """Replace or drop any character the fonts can't draw, so no empty boxes appear."""
    ok = supported_chars()
    out = []
    for ch in text:
        if ch in SWAPS:
            ch = SWAPS[ch]
        elif ord(ch) not in ok and ch not in "\n":
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            ch = base if base and all(ord(c) in ok for c in base) else ""
        out.append(ch)
    return re.sub(r" {2,}", " ", "".join(out))


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(str(_font_path(kind)), size)
    return _font_cache[key]


# ---------------------------------------------------------------- rich text

def parse_runs(text: str):
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]"""
    runs = []
    for i, part in enumerate(re.split(r"\*\*", text)):
        if part:
            runs.append((part, i % 2 == 1))
    return runs


def words_from_runs(runs):
    """Split runs into words; a word is a list of (text, bold) pieces so that
    punctuation hugging a bold phrase stays attached to it."""
    words, current = [], []
    for text, bold in runs:
        pieces = text.split(" ")
        for j, piece in enumerate(pieces):
            if j > 0:  # a space separated this piece from the previous one
                if current:
                    words.append(current)
                current = []
            if piece:
                current.append((piece, bold))
    if current:
        words.append(current)
    return words


def word_width(word, size):
    return sum(font("bold" if b else "body", size).getlength(t) for t, b in word)


def wrap_paragraph(text, size, max_width):
    words = words_from_runs(parse_runs(text))
    space = font("body", size).getlength(" ")
    lines, line, width = [], [], 0.0
    for w in words:
        ww = word_width(w, size)
        extra = ww if not line else space + ww
        if line and width + extra > max_width:
            lines.append((line, width))
            line, width = [w], ww
        else:
            line.append(w)
            width += extra
    if line:
        lines.append((line, width))
    return lines


def layout_body(text, size, max_width):
    """Returns list of lines; an empty paragraph gives a blank spacer line."""
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            out.append(([], 0.0))
            continue
        out.extend(wrap_paragraph(para, size, max_width))
    return out


# ---------------------------------------------------------------- drawing

def _base(theme_name, tag):
    theme = THEMES[theme_name]
    img = Image.new("RGB", (W, H), theme["bg"])
    logo = Image.open(ASSETS / theme["logo"]).convert("RGB")
    img.paste(logo, (18, 25))
    d = ImageDraw.Draw(img)
    # Letter-spaced tag, centred, like the originals.
    f = font("tag", 17)
    tracking = 6.5
    chars = list(tag.upper())
    total = sum(f.getlength(c) for c in chars) + tracking * (len(chars) - 1)
    x = (W - total) / 2
    for c in chars:
        d.text((x, 93), c, font=f, fill=theme["fg"], anchor="ls")
        x += f.getlength(c) + tracking
    return img, d, theme


def draw_hook(text, theme_name, tag):
    img, d, theme = _base(theme_name, tag)
    text = safe_text(text).replace("**", "")
    max_w = 880
    for size in range(112, 60, -4):
        f = font("hook", size)
        words, lines, line = text.split(), [], ""
        for w in words:
            trial = f"{line} {w}".strip()
            if line and f.getlength(trial) > max_w:
                lines.append(line)
                line = w
            else:
                line = trial
        lines.append(line)
        if len(lines) <= 5:
            break
    lh = int(size * 1.2)
    top = H / 2 - lh * len(lines) / 2 + size * 0.85
    for i, ln in enumerate(lines):
        d.text((W / 2, top + i * lh), ln, font=f, fill=theme["fg"], anchor="ms")
    return img


def draw_body(text, theme_name, tag):
    img, d, theme = _base(theme_name, tag)
    text = safe_text(text)
    max_w, max_h = 930, 980
    for size in range(46, 27, -2):
        lines = layout_body(text, size, max_w)
        lh = int(size * 1.55)
        if lh * len(lines) <= max_h:
            break
    space = font("body", size).getlength(" ")
    block_h = lh * len(lines)
    baseline = H / 2 - block_h / 2 + size * 1.05
    for i, (line, width) in enumerate(lines):
        x = (W - width) / 2
        y = baseline + i * lh
        for k, word in enumerate(line):
            if k:
                x += space
            for piece, bold in word:
                f = font("bold" if bold else "body", size)
                d.text((x, y), piece, font=f, fill=theme["fg"], anchor="ls")
                x += f.getlength(piece)
    return img


def render_post(post: dict, theme_name: str, tag: str, out_dir: Path, closing: str = None,
                label: str = "Takeaway") -> list:
    """Writes 01.jpg, 02.jpg ... and returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    slides = [draw_hook(post["hook"], theme_name, tag)]
    for body in post["slides"]:
        slides.append(draw_body(body, theme_name, tag))
    takeaway = post["takeaway"].strip()
    if not takeaway.lower().lstrip("*").startswith(label.lower()):
        takeaway = f"**{label}:** " + takeaway
    slides.append(draw_body(takeaway, theme_name, tag))
    if closing:
        slides.append(draw_body(closing, theme_name, tag))
    paths = []
    for i, img in enumerate(slides[:10], start=1):
        p = out_dir / f"{i:02d}.jpg"
        img.save(p, "JPEG", quality=92, optimize=True)
        paths.append(p)
    return paths
