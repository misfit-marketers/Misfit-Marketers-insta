"""Reels in their own formats, drawn frame by frame in code (no AI video).

Each format takes a short script written by the AI (see formats.py) and
returns frame_at(t), the length in seconds and a good moment for the cover:

  chat      two marketers texting, bubbles popping in with typing dots
  quiz      "which one wins?", a countdown, then the reveal and why
  rewrite   weak copy types out, gets crossed through, the better version types in
  kinetic   one punchy line at a time, words landing on the beat
  mockui    a made-up pricing page, product page or lock screen, tricks circled
  flow      a workflow diagram that lights up step by step
  list      a quick numbered list
  loop      one sharp line, 7 seconds, made to be watched again
  voice     a voice-over (free, offline voice) with word-by-word captions
  stock     free stock footage (Pexels or Pixabay) with captions on top

Everything stays inside Instagram's safe area (the top bar, the caption and
the buttons on the right cover parts of the screen). Every format except loop
ends on the same card: save this, and comment the keyword."""
import math
import random
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import reel as R
from . import styles
from .styles import F, Type, body, emoji_img, logo_tile, rgb, safe

W, H, FPS = 1080, 1920, 30
INK, PAPER, CREAM = (17, 17, 17), (241, 235, 223), (246, 241, 231)
LIGHT = (241, 238, 232)
TOP, BOTTOM, LEFT, RIGHT = 260, 1540, 80, 960  # Instagram's overlays sit outside this box
END = 3.2  # seconds of the closing card


# ------------------------------------------------------------------ helpers

def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    x = clamp(x)
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ease_out(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def pop(x):
    """0 -> 1 with a small overshoot, for things that pop in."""
    x = clamp(x)
    c1 = 1.70158
    return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2


def plain(t):
    return re.sub(r"\*\*", "", t or "")


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def on_colour(c):
    """Readable text colour on top of c."""
    return INK if lum(c) > 150 else (255, 255, 255)


def wrap(text, font, maxw):
    """Plain word wrap -> list of lines."""
    lines, cur = [], ""
    for para in (text or "").split("\n"):
        cur = ""
        for w in para.split():
            test = (cur + " " + w).strip()
            if cur and font.getlength(test) > maxw:
                lines.append(cur)
                cur = w
            else:
                cur = test
        if cur:
            lines.append(cur)
    return lines


def fit_font(text, maker, sizes, maxw, maxh, lh=1.15, max_lines=99):
    """Largest font whose wrapped text fits. -> (font, lines, line_height)."""
    for size in sizes:
        f = maker(size)
        lines = wrap(text, f, maxw)
        if len(lines) * size * lh <= maxh and len(lines) <= max_lines:
            return f, lines, int(size * lh)
    return f, lines, int(size * lh)


def rich(text, maker, sizes, maxw, maxh, lh=1.15, upper=False, max_lines=99):
    """Wrapped text with **bold** spans, using the slide renderer's layout."""
    mk = lambda z: Type(maker(z, False), maker(z, True), int(z * lh), upper=upper)
    return styles.fit(safe(text), mk, sizes, maxw, maxh, max_lines)


def text_layer(lines, ty, colors, align="left", boxw=None):
    """Draws wrapped rich lines onto a transparent layer."""
    w = int(boxw or max([wd for _, wd in lines] + [10]) + 4)
    h = max(10, len(lines) * ty.lh + 20)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    styles.draw_text(ImageDraw.Draw(im), lines, ty, 0, 0, colors, align=align, boxw=w)
    return im


def paste(dst, im, xy, alpha=1.0, scale=1.0, anchor="lt"):
    """Pastes an RGBA image with fade and scale (scaled around its centre for anchor 'c')."""
    if im is None or alpha <= 0.01:
        return
    if abs(scale - 1) > 0.005:
        nw, nh = max(1, int(im.width * scale)), max(1, int(im.height * scale))
        im2 = im.resize((nw, nh), Image.BILINEAR)
    else:
        im2 = im
    x, y = xy
    if anchor == "c":
        x, y = x - im2.width // 2, y - im2.height // 2
    elif anchor == "ct":
        x = x - im2.width // 2
    elif abs(scale - 1) > 0.005:  # keep the visual centre where it was
        x, y = x + (im.width - im2.width) // 2, y + (im.height - im2.height) // 2
    if alpha < 0.999:
        a = im2.getchannel("A").point(lambda v: int(v * alpha))
        im2 = im2.copy()
        im2.putalpha(a)
    dst.alpha_composite(im2, (int(x), int(y)))


def rounded(w, h, r, fill, outline=None, width=0):
    im = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=fill, outline=outline, width=width)
    return im


def shadowed(im, blur=18, offset=10, strength=70):
    pad = blur * 2
    sh = Image.new("RGBA", (im.width + pad * 2, im.height + pad * 2), (0, 0, 0, 0))
    a = im.getchannel("A").point(lambda v: int(v * strength / 255))
    shadow = Image.new("RGBA", im.size, (0, 0, 0, 255))
    shadow.putalpha(a)
    sh.alpha_composite(shadow, (pad, pad + offset))
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    sh.alpha_composite(im, (pad, pad))
    return sh, pad


def dot_bg(colour, dot, step=44, r=2):
    im = Image.new("RGBA", (W, H), colour + (255,))
    d = ImageDraw.Draw(im)
    for y in range(0, H, step):
        for x in range(0, W, step):
            d.ellipse((x - r, y - r, x + r, y + r), fill=dot)
    return im


class Ctx:
    """What every format needs: colours, the brand header and the closing card."""

    def __init__(self, accent, handle, cta, seed, dark=True):
        self.accent = rgb(accent) if isinstance(accent, str) else tuple(accent)
        self.handle, self.cta, self.seed = (handle or "misfit_marketers").lstrip("@"), cta, seed
        self.rng = random.Random(seed)
        self.bpm = R.track_bpm(seed)
        self._head = {}
        self._end = None

    def header(self, dark=True):
        """Logo and handle at the top left, below Instagram's own top bar."""
        if dark not in self._head:
            im = Image.new("RGBA", (W, 120), (0, 0, 0, 0))
            tile = logo_tile(64, box=PAPER if dark else INK, ink=INK if dark else PAPER)
            im.paste(tile, (LEFT, 28))
            ImageDraw.Draw(im).text((LEFT + 82, 60), self.handle, font=body(32, "SemiBold"),
                                    fill=PAPER if dark else INK, anchor="lm")
            self._head[dark] = im
        return self._head[dark]

    def end_card(self, t):
        """The closing card: save this, then the comment keyword."""
        if self._end is None:
            base = Image.new("RGBA", (W, H), INK + (255,))
            d = ImageDraw.Draw(base)
            d.text((W // 2, 560), "SAVE THIS", font=F("anton", 150), fill=PAPER, anchor="mm")
            reason = self.cta.get("reason") or "for later"
            f, lines, lh = fit_font(reason, lambda z: body(z, "Medium"), range(54, 30, -2), 820, 200)
            for i, ln in enumerate(lines):
                d.text((W // 2, 680 + i * lh), ln, font=f, fill=(200, 196, 188), anchor="mm")
            self._end = base
            key = self.cta.get("keyword", "PSYCHOLOGY")
            pill_f = F("anton", 92)
            pw = int(pill_f.getlength(key)) + 120
            self._pill = rounded(pw, 150, 75, self.accent + (255,))
            ImageDraw.Draw(self._pill).text((pw // 2, 78), key, font=pill_f, fill=on_colour(self.accent), anchor="mm")
            self._comment = Image.new("RGBA", (W, 80), (0, 0, 0, 0))
            ImageDraw.Draw(self._comment).text((W // 2, 40), "Comment", font=body(52, "SemiBold"), fill=PAPER, anchor="mm")
            offer = f"and I'll DM you {self.cta.get('offer', 'my marketing playbook')}"
            self._offer = Image.new("RGBA", (W, 140), (0, 0, 0, 0))
            f2, l2, lh2 = fit_font(offer, lambda z: body(z), range(44, 28, -2), 820, 130)
            for i, ln in enumerate(l2):
                ImageDraw.Draw(self._offer).text((W // 2, 34 + i * lh2), ln, font=f2, fill=(200, 196, 188), anchor="mm")
        im = self._end.copy()
        k = ease_out(t / 0.5)
        paste(im, self._comment, (0, 900 + int(30 * (1 - k))), alpha=k)
        paste(im, self._pill, (W // 2, 1050), alpha=ease_out((t - 0.25) / 0.4), scale=0.85 + 0.15 * pop((t - 0.25) / 0.45),
              anchor="c")
        paste(im, self._offer, (0, 1150), alpha=ease_out((t - 0.5) / 0.5))
        im.alpha_composite(self.header(True), (0, 140))
        return im


def with_end(ctx, main, main_total):
    """Joins a format's frames with the closing card (a short cross-fade)."""
    total = main_total + END

    def frame(t):
        if t < main_total:
            return main(t)
        e = ctx.end_card(t - main_total)
        if t - main_total < 0.3:
            last = main(main_total - 1 / FPS).convert("RGBA")
            return Image.blend(last, e, ease((t - main_total) / 0.3))
        return e
    return frame, total


# ================================================================== CHAT

def chat(script, ctx):
    """Two people texting. script: contact, role, messages [{from: them|me, text}], lesson."""
    msgs = script["messages"]
    bg = (12, 12, 14)
    them_c, me_c = (44, 44, 48), ctx.accent
    me_ink = on_colour(me_c)
    font = body(50)
    maxw = 720
    pad_x, pad_y, gap, gap_same = 34, 24, 22, 10
    bubbles, y = [], 0
    for i, m in enumerate(msgs):
        lines = wrap(safe(plain(m["text"])), font, maxw - 2 * pad_x)
        lh = 64
        bw = int(max(font.getlength(ln) for ln in lines)) + 2 * pad_x
        bh = len(lines) * lh + 2 * pad_y - 6
        mine = m["from"] == "me"
        im = rounded(bw, bh, 36, (me_c if mine else them_c) + (255,))
        d = ImageDraw.Draw(im)
        for k, ln in enumerate(lines):
            d.text((pad_x, pad_y + k * lh + lh // 2 - 4), ln, font=font, fill=me_ink if mine else (240, 240, 240), anchor="lm")
        if i and msgs[i - 1]["from"] == m["from"]:
            y += gap_same
        elif i:
            y += gap
        words = len(plain(m["text"]).split())
        bubbles.append({"im": im, "y": y, "mine": mine, "words": words})
        y += bh
    # timeline
    t = 0.9
    for b in bubbles:
        b["typing"] = (t, t + (0.35 if b["mine"] else 0.55 + min(0.8, b["words"] * 0.05)))
        b["at"] = b["typing"][1]
        t = b["at"] + 0.55 + 0.22 * b["words"]
    lesson_at = t + 0.2
    main_total = lesson_at + 3.6
    view_top, view_bottom = 420, 1480
    # header bar
    head = Image.new("RGBA", (W, 170), (22, 22, 25, 255))
    hd = ImageDraw.Draw(head)
    name = safe(script.get("contact") or "Sam")
    hd.ellipse((LEFT, 35, LEFT + 100, 135), fill=ctx.accent)
    hd.text((LEFT + 50, 85), name[:1].upper(), font=F("anton", 56), fill=on_colour(ctx.accent), anchor="mm")
    hd.text((LEFT + 130, 70), name, font=body(44, "Bold"), fill=(245, 245, 245), anchor="lm")
    role = safe(script.get("role") or "")
    lesson_text = safe(script.get("lesson") or "")
    lesson_card = rounded(900, 10, 40, PAPER + (255,))
    f, ll, lh = fit_font(plain(lesson_text), lambda z: F("anton", z), range(96, 44, -4), 780, 520, lh=1.08)
    lesson_card = rounded(900, 170 + len(ll) * lh + 60, 44, PAPER + (255,))
    ld = ImageDraw.Draw(lesson_card)
    ld.text((60, 70), "THE LESSON", font=F("mono-b", 30), fill=ctx.accent if lum(ctx.accent) < 200 else INK, anchor="lm")
    for k, ln in enumerate(ll):
        ld.text((60, 150 + k * lh), ln.upper(), font=f, fill=INK, anchor="lt")
    card_s, card_pad = shadowed(lesson_card, blur=24, offset=14, strength=120)

    def scroll_at(tt):
        # keep the newest visible bubble above view_bottom, easing between steps
        target = 0.0
        for b in bubbles:
            if tt >= b["typing"][0]:
                need = b["y"] + b["im"].height + (90 if tt < b["at"] else 0) - (view_bottom - view_top)
                prev_target = target
                target = max(target, need)
                if target != prev_target:
                    k = ease((tt - b["typing"][0]) / 0.35)
                    target = prev_target + (target - prev_target) * k
        return target

    def frame(tt):
        im = Image.new("RGBA", (W, H), bg + (255,))
        im.alpha_composite(ctx.header(True), (0, 140))
        hd2 = head.copy()
        typing = any(b["typing"][0] <= tt < b["at"] and not b["mine"] for b in bubbles)
        ImageDraw.Draw(hd2).text((LEFT + 130, 118), "typing..." if typing else role, font=body(30),
                                 fill=(140, 200, 140) if typing else (150, 150, 155), anchor="lm")
        im.alpha_composite(hd2, (0, 250))
        off = scroll_at(tt)
        layer = Image.new("RGBA", (W, view_bottom - view_top + 200), (0, 0, 0, 0))
        for b in bubbles:
            if tt < b["typing"][0]:
                break
            yy = b["y"] - off
            if yy + b["im"].height < -10:
                continue
            x = RIGHT - 20 - b["im"].width if b["mine"] else LEFT
            if tt < b["at"]:
                if not b["mine"]:  # typing dots
                    dots = rounded(150, 84, 42, (44, 44, 48, 255))
                    dd = ImageDraw.Draw(dots)
                    for k in range(3):
                        ph = (tt * 3.2 - k * 0.25) % 1
                        r = 9 + 3 * max(0, math.sin(ph * math.pi))
                        dd.ellipse((40 + k * 34 - r, 42 - r, 40 + k * 34 + r, 42 + r), fill=(170, 170, 175))
                    paste(layer, dots, (LEFT, yy), alpha=ease_out((tt - b["typing"][0]) / 0.2))
                continue
            k = (tt - b["at"]) / 0.22
            paste(layer, b["im"], (x, yy + int(14 * (1 - ease_out(k)))), alpha=ease_out(k), scale=0.9 + 0.1 * pop(k))
        im.alpha_composite(layer.crop((0, 0, W, view_bottom - view_top + 60)), (0, view_top))
        if tt >= lesson_at:
            k = ease_out((tt - lesson_at) / 0.45)
            dim = Image.new("RGBA", (W, H), (0, 0, 0, int(150 * k)))
            im.alpha_composite(dim)
            paste(im, card_s, (W // 2 - card_s.width // 2, int(1700 - (1700 - 620) * k) - card_pad), alpha=k)
        return im
    return with_end(ctx, frame, main_total), lesson_at - 1.0


# ================================================================== QUIZ

def quiz(script, ctx):
    """Guess first, then the reveal. script: question, options [..], answer (index), why [..]."""
    bg = dot_bg(LIGHT, (214, 209, 200))
    q_f, q_lines, q_lh = fit_font(plain(script["question"]).upper(), lambda z: F("anton", z), range(120, 60, -4), 860, 360, lh=1.02)
    opts = script["options"]
    cards = []
    for i, o in enumerate(opts):
        f, lines, lh = fit_font(safe(plain(o)), lambda z: body(z, "SemiBold"), range(58, 32, -2), 680, 240)
        h = max(200, len(lines) * lh + 90)
        im = rounded(900, h, 34, (255, 255, 255, 255), outline=(225, 220, 212), width=3)
        d = ImageDraw.Draw(im)
        d.ellipse((34, h // 2 - 46, 126, h // 2 + 46), fill=INK)
        d.text((80, h // 2), "ABC"[i], font=F("anton", 56), fill=PAPER, anchor="mm")
        top = h // 2 - len(lines) * lh // 2
        for k, ln in enumerate(lines):
            d.text((160, top + k * lh + lh // 2), ln, font=f, fill=INK, anchor="lm")
        win = rounded(900, h, 34, (255, 255, 255, 255), outline=ctx.accent, width=9)
        wd = ImageDraw.Draw(win)
        wd.ellipse((34, h // 2 - 46, 126, h // 2 + 46), fill=ctx.accent)
        wd.text((80, h // 2), "ABC"[i], font=F("anton", 56), fill=on_colour(ctx.accent), anchor="mm")
        for k, ln in enumerate(lines):
            wd.text((160, top + k * lh + lh // 2), ln, font=f, fill=INK, anchor="lm")
        tick = emoji_img("✅", 74)
        if tick:
            win.alpha_composite(tick, (900 - 110, h // 2 - tick.height // 2))
        cards.append((im, win, h))
    why = [safe(w) for w in script.get("why", [])][:3]
    why_ims = []
    for w in why:
        ty, lines = rich("- " + w, lambda z, e: body(z, "Bold" if e else "Regular"), range(50, 30, -2), 880, 220)
        why_ims.append(text_layer(lines, ty, (INK, INK), boxw=900))
    content = len(q_lines) * q_lh + 60 + sum(h + 26 for _, _, h in cards) + max(330, 120 + sum(w.height + 6 for w in why_ims))
    q_top = TOP + 40 + max(0, (BOTTOM - TOP - 40 - content) // 2)
    y0 = q_top + len(q_lines) * q_lh + 60
    ys, y = [], y0
    for _, _, h in cards:
        ys.append(y)
        y += h + 26
    count_at = 0.8 + 0.45 * len(cards) + 0.4
    reveal_at = count_at + 3.0
    why_at = reveal_at + 1.0
    why_total = sum(1.6 + 0.12 * len(w.split()) for w in why)
    main_total = why_at + why_total + 1.2
    ans = int(script.get("answer", 0))

    def frame(t):
        im = bg.copy()
        im.alpha_composite(ctx.header(False), (0, 140))
        d = ImageDraw.Draw(im)
        k = ease_out(t / 0.5)
        for i, ln in enumerate(q_lines):
            d.text((LEFT + 10 - int(30 * (1 - k)), q_top + i * q_lh), ln, font=q_f, fill=INK, anchor="lt")
        for i, (c, win, h) in enumerate(cards):
            a = ease_out((t - 0.8 - 0.45 * i) / 0.35)
            if t >= reveal_at:
                r = ease_out((t - reveal_at) / 0.35)
                if i == ans:
                    paste(im, win, (W // 2, ys[i] + h // 2), alpha=1, scale=1 + 0.03 * pop(r), anchor="c")
                else:
                    paste(im, c, (W // 2, ys[i] + h // 2), alpha=1 - 0.6 * r, anchor="c")
            else:
                paste(im, c, (W // 2, ys[i] + h // 2 + int(40 * (1 - a))), alpha=a, anchor="c")
        cy = ys[-1] + cards[-1][2] + 150
        if count_at <= t < reveal_at:
            n = 3 - int(t - count_at)
            ph = (t - count_at) % 1
            d.text((W // 2, cy - 110), "Pick one before it's revealed", font=body(38, "SemiBold"), fill=(110, 106, 100), anchor="mm")
            d.arc((W // 2 - 90, cy - 50, W // 2 + 90, cy + 130), -90, -90 + 360 * (1 - ph), fill=ctx.accent, width=14)
            d.text((W // 2, cy + 40), str(n), font=F("anton", 120), fill=INK, anchor="mm")
        if t >= why_at:
            yy = ys[-1] + cards[-1][2] + 70
            tt = why_at
            d.text((LEFT + 10, yy), "WHY", font=F("mono-b", 30), fill=ctx.accent if lum(ctx.accent) < 200 else INK, anchor="lt")
            yy += 60
            for w, wim in zip(why, why_ims):
                a = ease_out((t - tt) / 0.35)
                paste(im, wim, (LEFT + 10, yy + int(20 * (1 - a))), alpha=a)
                yy += wim.height + 6
                tt += 1.6 + 0.12 * len(w.split())
        return im
    return with_end(ctx, frame, main_total), reveal_at + 0.6


# ================================================================== REWRITE

def rewrite(script, ctx):
    """Weak copy types out and gets crossed through; the better version types in.
    script: pairs [{context, before, after}] (1 or 2), why [..]."""
    pairs = script["pairs"][:2]
    bg = (16, 16, 18)
    f = body(70, "SemiBold")
    lh = 90
    CPS = 24  # typing speed, characters per second
    plan, t = [], 0.6
    for p in pairs:
        b, a = safe(plain(p["before"])), safe(plain(p["after"]))
        bl, al = wrap(b, f, 840), wrap(a, f, 840)
        step = {"context": safe(p.get("context") or ""), "b": bl, "a": al, "t0": t}
        step["b_end"] = t + 0.5 + len(b) / CPS
        step["strike"] = step["b_end"] + 0.8
        step["a0"] = step["strike"] + 0.9
        step["a_end"] = step["a0"] + len(a) / CPS
        t = step["a_end"] + 1.6
        plan.append(step)
    why = [safe(w) for w in script.get("why", [])][:2]
    why_at = t
    main_total = why_at + sum(1.8 + 0.12 * len(w.split()) for w in why) + 1.0 if why else t + 0.5

    def typed(d, lines, n, x, y, colour, caret_on):
        left = n
        cy = y
        end_x, last_y = x, y
        for ln in lines:
            if left <= 0:
                break
            part = ln[: left]
            d.text((x, cy), part, font=f, fill=colour, anchor="lt")
            end_x = x + f.getlength(part)
            left -= len(ln) + 1
            last_y = cy
            cy += lh
        if caret_on and n > 0:
            d.rectangle((end_x + 6, last_y + 8, end_x + 12, last_y + lh - 14), fill=colour)
        return y + max(1, len(lines)) * lh

    def frame(tt):
        im = Image.new("RGBA", (W, H), bg + (255,))
        im.alpha_composite(ctx.header(True), (0, 140))
        d = ImageDraw.Draw(im)
        step = plan[0]
        for s in plan:
            if tt >= s["t0"]:
                step = s
        block = (70 if step["context"] else 0) + 100 + len(step["b"]) * lh + 80 + 100 + len(step["a"]) * lh
        y = TOP + 40 + max(0, (BOTTOM - 300 - TOP - 40 - block) // 2)
        if step["context"]:
            d.text((LEFT + 10, y), step["context"].upper(), font=F("mono-b", 30), fill=(150, 150, 150), anchor="lt")
            y += 70
        d.rounded_rectangle((LEFT + 10, y, LEFT + 210, y + 64), radius=32, fill=(60, 60, 64))
        d.text((LEFT + 110, y + 32), "BEFORE", font=F("mono-b", 28), fill=(220, 220, 220), anchor="mm")
        y += 100
        nb = int(max(0, tt - step["t0"] - 0.5) * CPS)
        blink = (tt * 2) % 1 < 0.6
        grey = (150, 150, 150) if tt >= step["strike"] else (235, 235, 235)
        y_after_b = typed(d, step["b"], nb, LEFT + 10, y, grey, tt < step["b_end"] + 0.6 and blink)
        if tt >= step["strike"]:
            k = ease((tt - step["strike"]) / 0.5)
            for i, ln in enumerate(step["b"]):
                lw = f.getlength(ln)
                part = clamp(k * len(step["b"]) - i)
                if part > 0:
                    yy = y + i * lh + lh // 2 - 4
                    d.line((LEFT + 4, yy, LEFT + 16 + lw * part, yy), fill=(230, 70, 60), width=8)
        if tt >= step["a0"] - 0.4:
            ya = y_after_b + 80
            d.rounded_rectangle((LEFT + 10, ya, LEFT + 190, ya + 64), radius=32, fill=ctx.accent)
            d.text((LEFT + 100, ya + 32), "AFTER", font=F("mono-b", 28), fill=on_colour(ctx.accent), anchor="mm")
            na = int(max(0, tt - step["a0"]) * CPS)
            typed(d, step["a"], na, LEFT + 10, ya + 100, (255, 255, 255), tt < step["a_end"] + 0.6 and blink)
        if tt >= why_at and why:
            k = ease_out((tt - why_at) / 0.4)
            panel_h = 120 + sum(len(wrap(w, body(44), 800)) * 58 + 30 for w in why)
            py = BOTTOM - panel_h
            panel = rounded(900, panel_h, 36, PAPER + (255,))
            pd = ImageDraw.Draw(panel)
            pd.text((50, 60), "WHY IT WORKS", font=F("mono-b", 30), fill=INK, anchor="lm")
            yy, t0 = 110, why_at
            for w in why:
                al = ease_out((tt - t0) / 0.35)
                if al > 0:
                    for ln in wrap(w, body(44), 800):
                        pd.text((50, yy), ln, font=body(44), fill=INK + (int(255 * al),), anchor="lt")
                        yy += 58
                    yy += 30
                t0 += 1.8 + 0.12 * len(w.split())
            paste(im, panel, (W // 2 - 450, py + int(120 * (1 - k))), alpha=k)
        return im
    cover = plan[0]["a_end"] + 0.5
    return with_end(ctx, frame, main_total), cover


# ================================================================== KINETIC (big-text story)

def kinetic(script, ctx):
    """One punchy line at a time, words landing on the beat. script: lines [..] (**word** = accent)."""
    lines_in = [safe(x) for x in script["lines"]][:10]
    beat = 60 / ctx.bpm
    step = beat / 2
    bg = dot_bg(INK, (40, 40, 42), step=48, r=2)
    shots = []
    t = 0.4
    for ln in lines_in:
        ty, wrapped = rich(ln.upper(), lambda z, e: F("anton", z), range(170, 70, -6), 900, 900, lh=1.02, max_lines=5)
        words = []  # (image, x, y)
        space = ty.n.getlength(" ")
        total_h = len(wrapped) * ty.lh
        y0 = (TOP + BOTTOM) // 2 - total_h // 2 - 40
        for i, (line, width) in enumerate(wrapped):
            x = (W - width) / 2
            for k, w in enumerate(line):
                if k:
                    x += space
                text = "".join(p for p, _ in w)
                emph = any(e for _, e in w)
                ww = sum(ty.f(e).getlength(ty.tx(p)) for p, e in w)
                im = Image.new("RGBA", (int(ww) + 8, ty.lh + 30), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((2, 4), ty.tx(text), font=ty.n, fill=ctx.accent if emph else PAPER)
                words.append((im, x, y0 + i * ty.lh))
                x += ww
        n = len(words)
        dur = max(1.8, n * step + 1.1 + 0.05 * n)
        shots.append({"t0": t, "words": words, "dur": dur})
        t += dur
    main_total = t + 0.3

    def frame(tt):
        im = bg.copy()
        im.alpha_composite(ctx.header(True), (0, 140))
        for s in shots:
            if not (s["t0"] <= tt < s["t0"] + s["dur"]):
                continue
            local = tt - s["t0"]
            out = clamp((local - (s["dur"] - 0.25)) / 0.25)
            for k, (w, x, y) in enumerate(s["words"]):
                a = (local - k * step) / 0.18
                if a <= 0:
                    continue
                paste(im, w, (x, y + int(30 * (1 - ease_out(a))) - int(60 * ease(out))), alpha=ease_out(a) * (1 - out),
                      scale=0.85 + 0.15 * pop(a))
        # progress line at the bottom of the safe area
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((LEFT, BOTTOM + 40, LEFT + (RIGHT - LEFT) * clamp(tt / main_total), BOTTOM + 48), radius=4,
                            fill=ctx.accent)
        return im
    cover = shots[0]["t0"] + shots[0]["dur"] - 0.4 if shots else 1
    return with_end(ctx, frame, main_total), cover


# ================================================================== LIST

def listicle(script, ctx):
    """A quick numbered list. script: title, items [{head, sub}] (3 to 5)."""
    bg = dot_bg(LIGHT, (214, 209, 200))
    items = script["items"][:5]
    tf, tl, tlh = fit_font(plain(script["title"]).upper(), lambda z: F("anton", z), range(130, 60, -4), 880, 380, lh=1.02)
    title_im = Image.new("RGBA", (W, len(tl) * tlh + 20), (0, 0, 0, 0))
    for i, ln in enumerate(tl):
        ImageDraw.Draw(title_im).text((LEFT + 10, i * tlh), ln, font=tf, fill=INK)
    sf, sl2, slh = fit_font(plain(script["title"]).upper(), lambda z: F("anton", z), range(64, 36, -2), 880, 150, lh=1.02)
    small_im = Image.new("RGBA", (W, len(sl2) * slh + 20), (0, 0, 0, 0))
    for i, ln in enumerate(sl2):
        ImageDraw.Draw(small_im).text((LEFT + 10, i * slh), ln, font=sf, fill=(90, 86, 80))
    cards = []
    for i, it in enumerate(items):
        hf, hl, hlh = fit_font(plain(it["head"]).upper(), lambda z: F("anton", z), range(96, 50, -4), 860, 300, lh=1.02)
        ty, sl = rich(safe(it.get("sub", "")), lambda z, e: body(z, "Bold" if e else "Regular"), range(56, 32, -2), 860, 300)
        num = F("anton", 260)
        h = 280 + len(hl) * hlh + 30 + len(sl) * ty.lh
        im = Image.new("RGBA", (W, h + 20), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.text((LEFT + 4, 0), f"{i + 1:02d}", font=num, fill=ctx.accent)
        for k, ln in enumerate(hl):
            d.text((LEFT + 10, 280 + k * hlh), ln, font=hf, fill=INK)
        styles.draw_text(d, sl, ty, LEFT + 10, 280 + len(hl) * hlh + 30, ((70, 66, 62), INK))
        words = len((it["head"] + " " + it.get("sub", "")).split())
        cards.append({"im": im, "dur": 2.6 + 0.16 * words})
    t, title_dur = 0.3, 1.6
    t += title_dur
    for c in cards:
        c["t0"] = t
        t += c["dur"]
    main_total = t + 0.2
    title_y = TOP + 40

    def frame(tt):
        im = bg.copy()
        im.alpha_composite(ctx.header(False), (0, 140))
        k = ease_out((tt - 0.3) / 0.4)
        switch = ease((tt - (0.3 + title_dur - 0.3)) / 0.3)
        mid = (TOP + BOTTOM) // 2 - title_im.height // 2 - 60
        if switch < 1:
            paste(im, title_im, (0, mid + int(30 * (1 - k)) - int(80 * switch)), alpha=k * (1 - switch))
        if switch > 0:
            paste(im, small_im, (0, title_y), alpha=switch)
        d = ImageDraw.Draw(im)
        for c in cards:
            local = tt - c["t0"]
            if 0 <= local < c["dur"]:
                a = ease_out(local / 0.35)
                out = clamp((local - (c["dur"] - 0.3)) / 0.3)
                cy = max(title_y + small_im.height + 60, (TOP + BOTTOM) // 2 - c["im"].height // 2)
                paste(im, c["im"], (int(80 * (1 - a)) - int(120 * ease(out)), cy), alpha=a * (1 - out))
        # progress dots
        n = len(cards)
        cur = sum(1 for c in cards if tt >= c["t0"])
        for i in range(n):
            x = W // 2 - (n - 1) * 22 + i * 44
            d.ellipse((x - 9, BOTTOM - 9, x + 9, BOTTOM + 9), fill=INK if i < cur else (200, 196, 188))
        return im
    cover = cards[0]["t0"] + 1.2 if cards else 1
    return with_end(ctx, frame, main_total), cover


# ================================================================== FLOW (workflow diagram)

def flow(script, ctx):
    """A workflow that lights up step by step. script: title, steps [{emoji, label, sub}] (3 to 6), result."""
    bg = (14, 15, 18)
    steps = script["steps"][:6]
    n = len(steps)
    tf, tl, tlh = fit_font(plain(script["title"]).upper(), lambda z: F("anton", z), range(110, 56, -4), 880, 260, lh=1.02)
    result = safe(script.get("result") or "")
    rf, rl, rlh = fit_font(plain(result), lambda z: body(z, "Bold"), range(54, 32, -2), 820, 200)
    pill_h = (len(rl) * rlh + 70 + 50) if result else 0
    avail = BOTTOM - (TOP + 60 + len(tl) * tlh + 60) - pill_h
    gap = 70
    box_h = min(170, int((avail - gap * (n - 1)) / n))
    y0 = TOP + 60 + len(tl) * tlh + 60
    boxes = []
    for i, s in enumerate(steps):
        y = y0 + i * (box_h + gap)
        def make(on):
            im = rounded(880, box_h, 30, ((34, 36, 42) if not on else (255, 255, 255)) + (255,),
                         outline=ctx.accent if on else (60, 62, 70), width=5 if on else 2)
            d = ImageDraw.Draw(im)
            e = emoji_img(s.get("emoji") or "⚙", int(box_h * 0.5))
            if e:
                im.alpha_composite(e, (36, box_h // 2 - e.height // 2))
            lab_f = body(min(50, int(box_h * 0.33)), "Bold")
            sub_f = body(min(36, int(box_h * 0.22)))
            ink = INK if on else (240, 240, 240)
            d.text((36 + int(box_h * 0.5) + 34, box_h // 2 - (16 if s.get("sub") else 0)), safe(plain(s["label"])), font=lab_f,
                   fill=ink, anchor="lm")
            if s.get("sub"):
                d.text((36 + int(box_h * 0.5) + 34, box_h // 2 + int(box_h * 0.22)), safe(plain(s["sub"])), font=sub_f,
                       fill=(110, 110, 115) if on else (150, 150, 158), anchor="lm")
            return im
        boxes.append({"off": make(False), "on": make(True), "y": y})
    t0, per = 1.0, 1.9
    for i, b in enumerate(boxes):
        b["t"] = t0 + i * per
    res_at = t0 + n * per + 0.2
    main_total = res_at + (3.0 if result else 0.8)

    def frame(tt):
        im = Image.new("RGBA", (W, H), bg + (255,))
        im.alpha_composite(ctx.header(True), (0, 140))
        d = ImageDraw.Draw(im)
        k = ease_out(tt / 0.5)
        for i, ln in enumerate(tl):
            d.text((LEFT + 10, TOP + 60 + i * tlh + int(20 * (1 - k))), ln, font=tf, fill=PAPER)
        cx = W // 2
        for i, b in enumerate(boxes):
            a = ease_out((tt - b["t"] + 0.6) / 0.35)
            if a <= 0:
                continue
            lit = tt >= b["t"] and (i == n - 1 or tt < boxes[i + 1]["t"] + 0.2 or tt >= res_at)
            paste(im, b["on"] if lit else b["off"], (cx - 440, b["y"] + int(30 * (1 - a))), alpha=a)
            if i < n - 1:
                y1, y2 = b["y"] + box_h + 6, boxes[i + 1]["y"] - 6
                p = ease((tt - b["t"] - 0.3) / 0.6)
                if p > 0:
                    yy = y1 + (y2 - y1) * p
                    d.line((cx, y1, cx, yy), fill=ctx.accent, width=7)
                    if p < 1:
                        d.ellipse((cx - 14, yy - 14, cx + 14, yy + 14), fill=ctx.accent)
                    else:
                        d.polygon([(cx - 18, y2 - 20), (cx + 18, y2 - 20), (cx, y2 + 2)], fill=ctx.accent)
        if result and tt >= res_at:
            r = ease_out((tt - res_at) / 0.4)
            pill = rounded(900, len(rl) * rlh + 70, 36, ctx.accent + (255,))
            pd = ImageDraw.Draw(pill)
            for i, ln in enumerate(rl):
                pd.text((450, 35 + i * rlh + rlh // 2), ln, font=rf, fill=on_colour(ctx.accent), anchor="mm")
            paste(im, pill, (W // 2 - 450, BOTTOM - pill.height + int(60 * (1 - r))), alpha=r)
        return im
    return with_end(ctx, frame, main_total), res_at + 0.8


# ================================================================== LOOP (7 seconds)

def loop(script, ctx):
    """One sharp line that loops. 7 seconds, no closing card, nothing fades so the loop is seamless."""
    total = 7.0
    ty, wrapped = rich(safe(script["line"]).upper(), lambda z, e: F("anton", z), range(180, 70, -6), 900, 1000, lh=1.0,
                       max_lines=6)
    layer = text_layer(wrapped, ty, (PAPER, ctx.accent), align="center", boxw=920)
    bg = dot_bg(INK, (38, 38, 40), step=48, r=2)

    def frame(tt):
        im = bg.copy()
        im.alpha_composite(ctx.header(True), (0, 140))
        ph = tt / total * 2 * math.pi
        sc = 1 + 0.025 * math.sin(ph)
        paste(im, layer, (W // 2, (TOP + BOTTOM) // 2 - 40), scale=sc, anchor="c")
        d = ImageDraw.Draw(im)
        x = LEFT + (RIGHT - LEFT) * ((tt / total) % 1)
        d.ellipse((x - 10, BOTTOM + 30, x + 10, BOTTOM + 50), fill=ctx.accent)
        return im
    return (frame, total), 2.0


# ================================================================== MOCK APP SCREENS

def _hand_ellipse(d, box, colour, width=8, seed=1, part=1.0):
    """A hand-drawn circle around box, drawn up to `part` of the way."""
    x0, y0, x1, y1 = box
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2 + 26, (y1 - y0) / 2 + 22
    rnd = random.Random(seed)
    j = [rnd.uniform(-6, 6) for _ in range(6)]
    pts = []
    steps = int(80 * part)
    for i in range(max(2, steps)):
        a = -math.pi / 2 + i / 80 * 2 * math.pi * 1.08
        wob = 1 + 0.03 * math.sin(3 * a + j[0])
        pts.append((cx + rx * wob * math.cos(a) + j[1] * math.sin(a), cy + ry * wob * math.sin(a) + j[2] * math.cos(a)))
    d.line(pts, fill=colour, width=width, joint="curve")


def mockui(script, ctx):
    """A made-up screen with its tricks circled one by one.
    script: screen (pricing | product | notifications), brand, fields for that screen, callouts [{target, text}], lines [..]."""
    kind = script.get("screen", "pricing")
    brand = safe(script.get("brand") or "Brewly")
    phone_w, phone_h = 900, 1180
    px, py = (W - phone_w) // 2, TOP + 40
    screen = rounded(phone_w, phone_h, 48, (255, 255, 255, 255))
    d = ImageDraw.Draw(screen)
    targets = {}  # name -> box in screen coordinates
    d.text((50, 64), brand, font=body(44, "Bold"), fill=INK, anchor="lm")
    d.ellipse((phone_w - 100, 40, phone_w - 52, 88), fill=(235, 235, 235))
    if kind == "pricing":
        d.text((50, 150), safe(script.get("heading") or "Choose your plan"), font=F("anton", 64), fill=INK, anchor="lm")
        plans = script.get("plans", [])[:3]
        y = 230
        for i, p in enumerate(plans):
            h = 230
            pop_tag = bool(p.get("tag"))
            fill = (255, 248, 240) if pop_tag else (247, 247, 247)
            d.rounded_rectangle((40, y, phone_w - 40, y + h), radius=30, fill=fill,
                                outline=ctx.accent if pop_tag else (225, 225, 225), width=5 if pop_tag else 2)
            d.text((80, y + 56), safe(p.get("name", "")), font=body(44, "Bold"), fill=INK, anchor="lm")
            pf = F("anton", 80)
            price = safe(p.get("price", ""))
            d.text((phone_w - 80, y + 70), price, font=pf, fill=INK, anchor="rm")
            targets[f"price:{i}"] = (phone_w - 80 - pf.getlength(price), y + 26, phone_w - 80, y + 116)
            feats = [safe(x) for x in p.get("features", [])][:2]
            for k, ftxt in enumerate(feats):
                d.text((80, y + 130 + k * 46), "- " + ftxt, font=body(34), fill=(90, 90, 90), anchor="lm")
            if pop_tag:
                tag = safe(p["tag"]).upper()
                tw = body(28, "Bold").getlength(tag) + 40
                d.rounded_rectangle((phone_w - 80 - tw, y + 150, phone_w - 80, y + 200), radius=25, fill=ctx.accent)
                d.text((phone_w - 80 - tw / 2, y + 175), tag, font=body(28, "Bold"), fill=on_colour(ctx.accent), anchor="mm")
                targets["tag"] = (phone_w - 80 - tw, y + 150, phone_w - 80, y + 200)
            targets[f"plan:{i}"] = (40, y, phone_w - 40, y + h)
            y += h + 34
    elif kind == "product":
        d.rounded_rectangle((40, 130, phone_w - 40, 470), radius=30, fill=(246, 241, 231))
        e = emoji_img(script.get("emoji") or "👟", 230)
        if e:
            screen.alpha_composite(e, (phone_w // 2 - e.width // 2, 300 - e.height // 2))
        d.text((50, 530), safe(script.get("product") or "Product"), font=body(50, "Bold"), fill=INK, anchor="lm")
        price, was = safe(script.get("price") or "$49"), safe(script.get("was") or "")
        pf = F("anton", 96)
        d.text((50, 625), price, font=pf, fill=INK, anchor="lm")
        pw = pf.getlength(price)
        targets["price"] = (50, 575, 50 + pw, 675)
        if was:
            wf = body(48)
            d.text((80 + pw, 629), was, font=wf, fill=(150, 150, 150), anchor="lm")
            ww = wf.getlength(was)
            d.line((80 + pw, 629, 80 + pw + ww, 629), fill=(150, 150, 150), width=4)
            targets["price"] = (50, 575, 80 + pw + ww, 675)
        y = 720
        rows = [("stock", script.get("stock"), (220, 50, 40)), ("viewers", script.get("viewers"), (90, 90, 90)),
                ("timer", script.get("timer"), (30, 120, 60)), ("badge", script.get("badge"), INK)]
        for key, txt, col in [r for r in rows if r[1]][:3]:
            txt = safe(txt)
            f = body(40, "SemiBold")
            d.text((50, y + 30), txt, font=f, fill=col, anchor="lm")
            targets[key] = (50, y, 50 + f.getlength(txt), y + 60)
            y += 84
        d.rounded_rectangle((40, phone_h - 150, phone_w - 40, phone_h - 50), radius=50, fill=INK)
        d.text((phone_w // 2, phone_h - 100), safe(script.get("button") or "Add to cart"), font=body(44, "Bold"),
               fill=(255, 255, 255), anchor="mm")
        targets["button"] = (40, phone_h - 150, phone_w - 40, phone_h - 50)
    else:  # notifications on a lock screen
        screen = Image.new("RGBA", (phone_w, phone_h), (0, 0, 0, 0))
        grad = Image.linear_gradient("L").resize((phone_w, phone_h))
        top_c, bot_c = (40, 44, 80), (150, 70, 110)
        lock = Image.composite(Image.new("RGBA", (phone_w, phone_h), bot_c + (255,)),
                               Image.new("RGBA", (phone_w, phone_h), top_c + (255,)), grad)
        m = Image.new("L", (phone_w, phone_h), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, phone_w - 1, phone_h - 1), radius=48, fill=255)
        screen.paste(lock, (0, 0), m)
        d = ImageDraw.Draw(screen)
        d.text((phone_w // 2, 120), safe(script.get("date") or "Friday, 14 November"), font=body(36, "SemiBold"),
               fill=(235, 235, 245), anchor="mm")
        d.text((phone_w // 2, 250), safe(script.get("time") or "9:41"), font=F("anton", 200), fill=(250, 250, 255), anchor="mm")
        y = 420
        for i, n in enumerate(script.get("notes", [])[:4]):
            h = 170
            card = rounded(phone_w - 80, h, 32, (255, 255, 255, 215))
            cd = ImageDraw.Draw(card)
            e = emoji_img(n.get("emoji") or "🔔", 64)
            if e:
                card.alpha_composite(e, (28, 28))
            cd.text((110, 50), safe(n.get("app") or "App").upper(), font=body(26, "Bold"), fill=(90, 90, 100), anchor="lm")
            cd.text((phone_w - 120, 50), "now", font=body(26), fill=(120, 120, 130), anchor="rm")
            cd.text((110, 96), safe(n.get("title") or ""), font=body(36, "Bold"), fill=INK, anchor="lm")
            cd.text((110, 140), safe(n.get("text") or "")[:44], font=body(32), fill=(60, 60, 70), anchor="lm")
            targets[f"note:{i}"] = (40, y, phone_w - 40, y + h)
            n["_card"], n["_y"] = card, y
            y += h + 24
    notes = script.get("notes", []) if kind == "notifications" else []
    phone, pad = shadowed(screen, blur=26, offset=16, strength=90)
    callouts = []
    for i, c in enumerate(script.get("callouts", [])[:3]):
        box = targets.get(str(c.get("target")))
        if box is None and targets:
            box = list(targets.values())[min(i, len(targets) - 1)]
        if box is None:
            continue
        callouts.append({"box": box, "text": safe(c.get("text") or "")})
    appear = 0.9 + (0.5 * len(notes))
    t = appear + 0.6
    for c in callouts:
        c["t"] = t
        t += 2.6
    lines = [safe(x) for x in script.get("lines", [])][:2]
    lines_at = t
    main_total = lines_at + (sum(1.8 + 0.12 * len(x.split()) for x in lines) + 1.0 if lines else 0.6)
    bg = dot_bg(LIGHT, (214, 209, 200))
    hand = F("hand", 64, "Bold")

    def frame(tt):
        im = bg.copy()
        im.alpha_composite(ctx.header(False), (0, 140))
        k = ease_out(tt / 0.5)
        layer = phone.copy()
        if kind == "notifications":
            for i, n in enumerate(notes):
                a = ease_out((tt - 0.6 - 0.5 * i) / 0.35)
                if a > 0:
                    paste(layer, n["_card"], (pad + 40, pad + n["_y"] - int(40 * (1 - a))), alpha=a)
        ld = ImageDraw.Draw(layer)
        for i, c in enumerate(callouts):
            if tt >= c["t"]:
                p = ease((tt - c["t"]) / 0.6)
                bx = tuple(v + pad for v in c["box"])
                _hand_ellipse(ld, bx, ctx.accent if lum(ctx.accent) < 200 else (220, 60, 50), width=9, seed=i + 3, part=p)
        paste(im, layer, (px - pad, py - pad + int(60 * (1 - k))), alpha=k)
        current = [c for c in callouts if tt >= c["t"]]
        if current and tt < lines_at:
            c = current[-1]
            a = ease_out((tt - c["t"] - 0.3) / 0.35)
            if a > 0 and c["text"]:
                tw = hand.getlength(c["text"])
                lbl = rounded(int(tw) + 60, 100, 50, INK + (255,))
                ImageDraw.Draw(lbl).text((30, 50), c["text"], font=hand, fill=PAPER, anchor="lm")
                bx0, by0, bx1, by1 = c["box"]
                mid = py + (by0 + by1) / 2 - lbl.height / 2
                if bx1 < phone_w * 0.6 and px + bx1 + 50 + lbl.width < W - 20:
                    pos = (px + bx1 + 50, mid)  # beside a small target
                elif bx0 > phone_w * 0.4 and px + bx0 - 50 - lbl.width > 20:
                    pos = (px + bx0 - 50 - lbl.width, mid)
                else:
                    pos = (min(max(LEFT, px + bx0), RIGHT - lbl.width), min(py + by1 + 34, py + phone_h - 40))
                paste(im, lbl, pos, alpha=a)
        if lines and tt >= lines_at:
            k2 = ease_out((tt - lines_at) / 0.4)
            panel_h = 100 + sum(len(wrap(x, body(46, "SemiBold"), 820)) * 60 + 24 for x in lines)
            panel = rounded(900, panel_h, 36, INK + (255,))
            pd = ImageDraw.Draw(panel)
            yy, t0 = 50, lines_at
            for x in lines:
                al = ease_out((tt - t0) / 0.35)
                for ln in wrap(x, body(46, "SemiBold"), 820):
                    pd.text((50, yy), ln, font=body(46, "SemiBold"), fill=PAPER + (int(255 * al),), anchor="lt")
                    yy += 60
                yy += 24
                t0 += 1.8 + 0.12 * len(x.split())
            paste(im, panel, (W // 2 - 450, BOTTOM - panel_h + int(100 * (1 - k2))), alpha=k2)
        return im
    cover = callouts[0]["t"] + 1.5 if callouts else 2
    return with_end(ctx, frame, main_total), cover


# ================================================================== VOICE-OVER

KOKORO = {"model": "kokoro-v1.0.int8.onnx", "voices": "voices-v1.0.bin",
          "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"}


def tts_available() -> bool:
    try:
        import kokoro_onnx
        return bool(kokoro_onnx)
    except Exception:  # noqa: BLE001
        return False


def _kokoro():
    """Kokoro (Apache 2.0), a small open voice model that runs on GitHub's CPU.
    The model files (about 120 MB) are downloaded once per run into a cache."""
    import os
    import urllib.request
    from kokoro_onnx import Kokoro
    cache = Path(os.environ.get("MISFIT_TTS_CACHE") or (Path.home() / ".cache" / "misfit-tts"))
    cache.mkdir(parents=True, exist_ok=True)
    for key in ("model", "voices"):
        p = cache / KOKORO[key]
        if not p.exists() or p.stat().st_size < 1_000_000:
            urllib.request.urlretrieve(KOKORO["url"] + KOKORO[key], p)
    return Kokoro(str(cache / KOKORO["model"]), str(cache / KOKORO["voices"]))


def voice(script, ctx):
    """A spoken mini-lesson with big word-by-word captions. script: title, sentences [..], voice."""
    k = _kokoro()
    who = script.get("voice") or ctx.rng.choice(["am_michael", "af_heart", "am_adam", "af_bella"])
    parts, words, t = [], [], 0.6
    for snt in [safe(plain(x)) for x in script["sentences"]][:8]:
        samples, sr = k.create(snt, voice=who, speed=1.08, lang="en-us")
        samples = np.asarray(samples, dtype=np.float64)
        dur = len(samples) / sr
        parts.append((t, samples, sr))
        ws = snt.split()
        weights = [len(w) + 2 for w in ws]
        tot = sum(weights)
        acc = 0.08 * dur
        span = 0.86 * dur
        for w, wt in zip(ws, weights):
            words.append((t + acc, w))
            acc += span * wt / tot
        t += dur + 0.28
    main_total = t + 0.6
    total = main_total + END
    vo = np.zeros(int((total + 1) * R.SR))
    for start, samples, sr in parts:
        x = np.interp(np.arange(0, len(samples) * R.SR / sr) * sr / R.SR, np.arange(len(samples)), samples)
        i = int(start * R.SR)
        vo[i:i + len(x)] += x[: len(vo) - i]
    chunks, cur = [], []
    for i, (wt, w) in enumerate(words):  # groups of up to 3 words, broken at sentence ends
        cur.append((wt, w))
        if len(cur) == 3 or w.endswith((".", "?", "!")) or i == len(words) - 1:
            chunks.append(cur)
            cur = []
    title = safe(plain(script.get("title") or ""))
    tf, tl, tlh = fit_font(title.upper(), lambda z: F("anton", z), range(90, 50, -4), 880, 220, lh=1.02)
    bg = dot_bg(INK, (38, 38, 40), step=48, r=2)
    cf = F("anton", 150)

    def frame(tt):
        im = bg.copy()
        im.alpha_composite(ctx.header(True), (0, 140))
        d = ImageDraw.Draw(im)
        for i, ln in enumerate(tl):
            d.text((LEFT + 10, TOP + 40 + i * tlh), ln, font=tf, fill=(170, 166, 158))
        cur = None
        for c in chunks:
            if c[0][0] <= tt + 0.05:
                cur = c
        if cur is not None:
            txt = [w for _, w in cur]
            widths = [cf.getlength(w.upper()) for w in txt]
            space = cf.getlength(" ")
            lines, line, lw = [], [], 0
            for w, wd in zip(txt, widths):  # wrap the chunk to the screen
                if line and lw + space + wd > 900:
                    lines.append(line)
                    line, lw = [], 0
                line.append((w, wd))
                lw += wd + (space if len(line) > 1 else 0)
            if line:
                lines.append(line)
            y = (TOP + BOTTOM) // 2 - len(lines) * 160 // 2
            idx = 0
            for ln in lines:
                lw = sum(wd for _, wd in ln) + space * (len(ln) - 1)
                x = (W - lw) / 2
                for w, wd in ln:
                    said = cur[idx][0] <= tt
                    d.text((x, y), w.upper(), font=cf, fill=ctx.accent if said and idx == max(
                        j for j in range(len(cur)) if cur[j][0] <= tt) else (PAPER if said else (90, 88, 84)))
                    x += wd + space
                    idx += 1
                y += 160
        d.rounded_rectangle((LEFT, BOTTOM + 40, LEFT + (RIGHT - LEFT) * clamp(tt / main_total), BOTTOM + 48), radius=4,
                            fill=ctx.accent)
        return im
    frame_all, _ = with_end(ctx, frame, main_total)
    return (frame_all, total), 2.5, vo


# ================================================================== STOCK FOOTAGE (Pexels)

def pexels_video(query: str, key: str, out: Path, log=print) -> dict:
    """Downloads a portrait clip from Pexels (free to use, no attribution
    required; we credit the creator in the caption anyway)."""
    import requests
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key}, timeout=30,
                     params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15})
    r.raise_for_status()
    vids = r.json().get("videos") or []
    for v in vids:
        if not 8 <= int(v.get("duration") or 0) <= 60:
            continue
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("height")
                 and f["height"] >= f.get("width", 0) and 960 <= f["height"] <= 2200]
        if not files:
            continue
        f = sorted(files, key=lambda x: abs(x["height"] - 1920))[0]
        data = requests.get(f["link"], timeout=120).content
        out.write_bytes(data)
        return {"path": out, "by": (v.get("user") or {}).get("name", ""), "url": v.get("url", ""), "duration": v["duration"]}
    raise RuntimeError(f"No portrait clip found on Pexels for '{query}'")


def pixabay_video(query: str, key: str, out: Path, log=print) -> dict:
    """Downloads a clip from Pixabay (Pixabay Content License: free to use, no
    attribution required; we credit the creator in the caption anyway). Most
    Pixabay clips are landscape; the middle is cropped to fit a Reel."""
    import requests
    r = requests.get("https://pixabay.com/api/videos/", timeout=30,
                     params={"key": key, "q": query, "safesearch": "true", "per_page": 20, "video_type": "film"})
    r.raise_for_status()
    hits = r.json().get("hits") or []
    def score(h):  # portrait first, then the size closest to 1080 px high
        v = (h.get("videos") or {}).get("large") or (h.get("videos") or {}).get("medium") or {}
        return (0 if v.get("height", 0) >= v.get("width", 1) else 1, abs(v.get("height", 0) - 1080))
    for h in sorted(hits, key=score):
        if not 6 <= int(h.get("duration") or 0) <= 90:
            continue
        vids = h.get("videos") or {}
        v = next((vids[k] for k in ("large", "medium", "small") if (vids.get(k) or {}).get("url")
                  and 20_000_000 >= int((vids.get(k) or {}).get("size") or 0)), None)
        if not v:
            continue
        out.write_bytes(requests.get(v["url"], timeout=120).content)
        return {"path": out, "by": h.get("user", ""), "url": h.get("pageURL", ""), "duration": h["duration"],
                "site": "Pixabay"}
    raise RuntimeError(f"No clip found on Pixabay for '{query}'")


def stock_clip(query: str, out: Path, log=print) -> dict:
    """Pexels if a PEXELS_API_KEY is set, otherwise Pixabay (PIXABAY_API_KEY)."""
    import os
    errors = []
    if os.environ.get("PEXELS_API_KEY"):
        try:
            return {**pexels_video(query, os.environ["PEXELS_API_KEY"], out, log), "site": "Pexels"}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:150])
    if os.environ.get("PIXABAY_API_KEY"):
        try:
            return pixabay_video(query, os.environ["PIXABAY_API_KEY"], out, log)
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:150])
    raise RuntimeError("No stock clip: " + (" | ".join(errors) or "no PEXELS_API_KEY or PIXABAY_API_KEY secret"))


def stock(script, ctx, clip: dict):
    """Captions over free stock footage. script: query, lines [..]. clip from pexels_video()."""
    import subprocess
    lines_in = [safe(x) for x in script["lines"]][:8]
    t, shots = 0.6, []
    for ln in lines_in:
        ty, wrapped = rich(ln.upper(), lambda z, e: F("anton", z), range(140, 60, -6), 900, 700, lh=1.02, max_lines=4)
        layer, _ = shadowed(text_layer(wrapped, ty, (PAPER, ctx.accent), align="center", boxw=920), blur=12, offset=4,
                            strength=230)
        dur = 1.4 + 0.3 * len(ln.split())
        shots.append((t, dur, layer))
        t += dur
    main_total = t + 0.4
    cmd = [R.ffmpeg_exe(), "-loglevel", "error", "-stream_loop", "-1", "-i", str(clip["path"]), "-t", f"{main_total:.2f}",
           "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    ctx.procs = getattr(ctx, "procs", []) + [proc]  # closed by build() when done
    state = {"i": -1, "img": None}
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 140))

    def frame(tt):
        want = int(tt * FPS)
        while state["i"] < want:
            raw = proc.stdout.read(W * H * 3)
            if len(raw) < W * H * 3:
                break
            state["img"] = Image.frombytes("RGB", (W, H), raw)
            state["i"] += 1
        im = (state["img"] or Image.new("RGB", (W, H), INK)).convert("RGBA")
        im.alpha_composite(shade)
        im.alpha_composite(ctx.header(True), (0, 140))
        for t0, dur, layer in shots:
            if t0 <= tt < t0 + dur:
                a = ease_out((tt - t0) / 0.25) * (1 - clamp((tt - t0 - dur + 0.2) / 0.2))
                paste(im, layer, (W // 2, (TOP + BOTTOM) // 2 + int(30 * (1 - a))), alpha=a, scale=0.94 + 0.06 * pop(
                    (tt - t0) / 0.3), anchor="c")
        return im
    return with_end(ctx, frame, main_total), shots[0][0] + 0.8 if shots else 1


# ================================================================== build

RENDERERS = {"chat": chat, "quiz": quiz, "rewrite": rewrite, "kinetic": kinetic, "mockui": mockui, "flow": flow,
             "list": listicle, "loop": loop}


def build(fmt, script, out_dir: Path, accent, handle, cta, seed, log=print):
    """Renders one format Reel. -> (reel path, cover path, extra info)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(accent, handle, cta, seed)
    extra, voice_track = {}, None
    if fmt == "voice":
        (frame, total), cover_t, voice_track = voice(script, ctx)
    elif fmt == "stock":
        clip = stock_clip(script["query"], out_dir / "_clip.mp4", log)
        (frame, total), cover_t = stock(script, ctx, clip)
        extra = {"credit": clip.get("by", ""), "credit_url": clip.get("url", ""), "site": clip.get("site", "")}
    else:
        (frame, total), cover_t = RENDERERS[fmt](script, ctx)
    cover = out_dir / "01.jpg"
    frame(min(cover_t, total - 0.1)).convert("RGB").save(cover, quality=88)
    def close():
        for p in getattr(ctx, "procs", []):
            if p.poll() is None:
                p.kill()
            p.wait()
        ctx.procs = []
    if fmt == "stock":  # the clip reader moves forward only, so start the stream again for the video
        close()
        (frame, total), _ = stock(script, ctx, {"path": out_dir / "_clip.mp4"})
    try:
        path = R.encode_frames(frame, total, out_dir / "reel.mp4", seed=seed, log=log, voice=voice_track,
                               fade=0.3 if fmt == "loop" else 1.6)
    finally:
        close()
        clip_file = out_dir / "_clip.mp4"
        if clip_file.exists():
            clip_file.unlink()
    return path, cover, extra
