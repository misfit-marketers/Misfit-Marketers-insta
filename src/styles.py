"""The four MISFIT slide styles: poster, editorial, essay and notebook.

render(post, design, style, tag, out_dir, ...) draws a whole carousel and
returns the saved JPEG paths. Each series is matched to a style in
config.json ("design" -> "styles"). Everything is drawn with Pillow, so it
runs on GitHub Actions for free: fonts and the emoji font live in
assets/fonts, textures and hand-drawn marks are generated in code."""
import math
import random
import re
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .common import ASSETS
from .design import split_label

W, H = 1080, 1350
FONTS = ASSETS / "fonts"
FILES = {
    "anton": "Anton-Regular.ttf", "serif": "DMSerifDisplay-Regular.ttf", "serif-i": "DMSerifDisplay-Italic.ttf",
    "quote": "InstrumentSerif-Italic.ttf", "hand": "Caveat-Variable.ttf", "inter": "Inter-Variable.ttf",
    "mono": "SpaceMono-Regular.ttf", "mono-b": "SpaceMono-Bold.ttf", "kumbh": "KumbhSans-Bold.ttf",
}
EMOJI_FONT = FONTS / "NotoColorEmoji.ttf"

_fonts = {}


def F(kind, size, weight=None):
    size = int(size)
    key = (kind, size, weight)
    if key not in _fonts:
        f = ImageFont.truetype(str(FONTS / FILES[kind]), size)
        if weight:
            f.set_variation_by_name(weight)
        _fonts[key] = f
    return _fonts[key]


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))


# ------------------------------------------------------------------ safe text

_charset = None
SWAPS = {"—": ", ", "―": ", ", "–": "-", "…": "...", "₹": "Rs ", "×": "x",
         "′": "'", "″": '"', " ": " ", " ": " ", " ": " "}


def charset():
    global _charset
    if _charset is None:
        from fontTools.ttLib import TTFont
        sets = [set(TTFont(str(FONTS / FILES[k])).getBestCmap()) for k in ("anton", "serif", "inter", "hand", "quote")]
        _charset = set.intersection(*sets)
    return _charset


def safe(text):
    ok = charset()
    out = []
    for ch in text or "":
        ch = SWAPS.get(ch, ch)
        if len(ch) == 1 and ord(ch) not in ok and ch != "\n":
            base = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            ch = base if base and all(ord(c) in ok for c in base) else ""
        out.append(ch)
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# ------------------------------------------------------------------ textures

def grain(img, amount=7, seed=1):
    a = np.asarray(img).astype(np.int16)
    n = np.random.default_rng(seed).normal(0, amount, a.shape[:2])[..., None]
    return Image.fromarray(np.clip(a + n, 0, 255).astype(np.uint8))


def paper(img, seed=2):
    rng = np.random.default_rng(seed)
    small = rng.normal(0, 1, (H // 30, W // 30))
    blot = Image.fromarray(((small - small.min()) / np.ptp(small) * 255).astype(np.uint8))
    blot = np.asarray(blot.resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(25))).astype(np.int16) - 128
    a = np.asarray(img).astype(np.int16) + (blot[..., None] * 0.06).astype(np.int16)
    return grain(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)), 7, seed)


def dot_grid(d, color, step=36, r=2):
    for y in range(step // 2, H, step):
        for x in range(step // 2, W, step):
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)


def wavy_grid(d, color, step=44, amp=3):
    for y in range(0, H + step, step):
        d.line([(x, y + amp * math.sin(x / 40)) for x in range(0, W + 10, 10)], fill=color, width=1)
    for x in range(0, W + step, step):
        d.line([(x + amp * math.sin(y / 40), y) for y in range(0, H + 10, 10)], fill=color, width=1)


def ruled(d, line, margin, step=54, top=150):
    for y in range(top, H, step):
        d.line((0, y, W, y), fill=line, width=2)
    d.line((110, 0, 110, H), fill=margin, width=3)


# ------------------------------------------------------------------ emoji

def emoji_img(ch, size, angle=0):
    if not ch or not EMOJI_FONT.exists():
        return None
    try:
        f = ImageFont.truetype(str(EMOJI_FONT), 109)
        im = Image.new("RGBA", (180, 180), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((20, 20), ch, font=f, embedded_color=True)
        box = im.getbbox()
        if not box:
            return None
        im = im.crop(box)
        im = im.resize((int(size), int(size * im.height / im.width)), Image.LANCZOS)
        return im.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else im
    except Exception:
        return None


def sticker(img, ch, xy, size, angle=0, shadow=True):
    e = emoji_img(ch, size, angle)
    if e is None:
        return False
    x, y = int(xy[0]), int(xy[1])
    if shadow:
        sh = Image.new("RGBA", e.size, (0, 0, 0, 0))
        sh.putalpha(e.getchannel("A").point(lambda v: int(v * 0.22)))
        sh = sh.filter(ImageFilter.GaussianBlur(8))
        img.paste(sh, (x + 6, y + 10), sh)
    img.paste(e, (x, y), e)
    return True


# ------------------------------------------------------------------ marks

def bezier(p0, p1, p2, p3, n=40):
    out = []
    for i in range(n + 1):
        t = i / n
        out.append(tuple((1 - t) ** 3 * a + 3 * (1 - t) ** 2 * t * b + 3 * (1 - t) * t ** 2 * c + t ** 3 * e
                         for a, b, c, e in zip(p0, p1, p2, p3)))
    return out


def hand_arrow(d, p0, p1, p2, p3, color, width=5, head=22, seed=3):
    rnd = random.Random(seed)
    pts = [(x + rnd.uniform(-0.8, 0.8), y + rnd.uniform(-0.8, 0.8)) for x, y in bezier(p0, p1, p2, p3)]
    d.line(pts, fill=color, width=width, joint="curve")
    (ax, ay), (bx, by) = pts[-4], pts[-1]
    ang = math.atan2(by - ay, bx - ax)
    for s in (2.6, -2.6):
        d.line((bx, by, bx + head * math.cos(ang + s), by + head * math.sin(ang + s)), fill=color, width=width)


def scribble(d, x0, x1, y, color, width=7, seed=4):
    rnd = random.Random(seed)
    for k in range(2):
        pts = [(x, y + k * 9 + 4 * math.sin((x - x0) / 55 + k) + rnd.uniform(-1, 1))
               for x in range(int(x0), int(x1) + 1, 8)]
        if len(pts) > 1:
            d.line(pts, fill=color, width=max(2, width - 2 * k), joint="curve")


def arrow(d, x, y, length, color, width=6, head=15):
    d.line((x, y, x + length, y), fill=color, width=width)
    d.line((x + length - head, y - head, x + length, y), fill=color, width=width)
    d.line((x + length - head, y + head, x + length, y), fill=color, width=width)


def progress(d, x, y, w, frac, bg, fg, h=4):
    d.rectangle((x, y, x + w, y + h), fill=bg)
    d.rectangle((x, y, x + int(w * frac), y + h), fill=fg)


def page_dots(d, cx, y, n, cur, on, off, r=7, gap=26):
    n = max(1, min(n, 10))
    x0 = cx - (n - 1) * gap / 2
    for i in range(n):
        x = x0 + i * gap
        d.ellipse((x - r, y - r, x + r, y + r), fill=on if i == cur else off)


def spaced(d, xy, text, font, fill, spacing=4, right=False, centre=False):
    total = sum(font.getlength(c) for c in text) + spacing * max(0, len(text) - 1)
    x, y = xy
    if right:
        x -= total
    elif centre:
        x -= total / 2
    for c in text:
        d.text((x, y), c, font=font, fill=fill)
        x += font.getlength(c) + spacing
    return total


def logo(img, xy, size, box=(0, 0, 0), ink=(255, 255, 255)):
    d = ImageDraw.Draw(img)
    x, y = xy
    d.rectangle((x, y, x + size, y + size), fill=box)
    f = F("kumbh", size * 0.36)
    for i, word in enumerate(("MIS", "FIT")):
        d.text((x + (size - f.getlength(word)) / 2, y + size * (0.19 + i * 0.36)), word, font=f, fill=ink)


# ------------------------------------------------------------------ rich text

def runs(text):
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]"""
    return [(p, i % 2 == 1) for i, p in enumerate(re.split(r"\*\*", text or "")) if p]


def mark(text, phrase):
    """Wrap the first case-insensitive match of phrase in ** **."""
    if not phrase:
        return text
    i = text.lower().find(phrase.lower())
    if i < 0:
        return text
    return text[:i] + "**" + text[i:i + len(phrase)] + "**" + text[i + len(phrase):]


def words_of(text):
    words, cur = [], []
    for t, e in runs(text):
        for j, piece in enumerate(t.split(" ")):
            if j > 0 and cur:
                words.append(cur)
                cur = []
            if piece:
                cur.append((piece, e))
    if cur:
        words.append(cur)
    return words


class Type:
    """A pair of fonts (normal, emphasis) at one size, plus line height."""
    def __init__(self, normal, emph, lh, upper=False):
        self.n, self.e, self.lh, self.upper = normal, emph, lh, upper

    def f(self, e):
        return self.e if e else self.n

    def tx(self, t):
        return t.upper() if self.upper else t


def layout(text, ty, maxw):
    """-> list of lines; each line is [(word_pieces, width)], plus line width."""
    lines = []
    space = ty.n.getlength(" ")
    for para in (text or "").split("\n"):
        line, width = [], 0.0
        for w in words_of(para.strip()):
            ww = sum(ty.f(e).getlength(ty.tx(p)) for p, e in w)
            add = ww if not line else space + ww
            if line and width + add > maxw:
                lines.append((line, width))
                line, width = [w], ww
            else:
                line.append(w)
                width += add
        if line:
            lines.append((line, width))
    return lines


def fit(text, maker, sizes, maxw, maxh, max_lines=99):
    """Largest size whose wrapped text fits the box. maker(size) -> Type."""
    for size in sizes:
        ty = maker(size)
        lines = layout(text, ty, maxw)
        if (len(lines) * ty.lh <= maxh and len(lines) <= max_lines
                and all(wd <= maxw + 1 for _, wd in lines)):
            return ty, lines
    return ty, lines


def draw_text(d, lines, ty, x, y, colors, align="left", deco=None, boxw=None):
    """Draws wrapped lines with their top at y. colors=(normal, emph).
    deco(d, x0, x1, baseline, ty) is called for each emphasised span before
    the text is drawn (for highlights, bands and underlines).
    Returns the y just below the last line."""
    space = ty.n.getlength(" ")
    asc = ty.n.getmetrics()[0]
    for i, (line, width) in enumerate(lines):
        base = y + i * ty.lh + asc
        cx = x if align == "left" else x + ((boxw or 0) - width) / 2
        placed = []  # (x0, x1, text, emph)
        for k, w in enumerate(line):
            if k:
                cx += space
            for p, e in w:
                pw = ty.f(e).getlength(ty.tx(p))
                placed.append((cx, cx + pw, p, e))
                cx += pw
        if deco:
            spans = []
            for x0, x1, _, e in placed:
                if not e:
                    continue
                if spans and x0 - spans[-1][1] <= space + 1:
                    spans[-1][1] = x1
                else:
                    spans.append([x0, x1])
            for x0, x1 in spans:
                deco(d, x0, x1, base, ty)
        for x0, _, p, e in placed:
            d.text((x0, base), ty.tx(p), font=ty.f(e), fill=colors[1] if e else colors[0], anchor="ls")
    return y + len(lines) * ty.lh



def stack(blocks, top, bottom, bias=0.4):
    """blocks = [(height, draw(y))]. Draws them one under another, with the
    group nudged down into the free space (bias 0 = top, 0.5 = centred)."""
    total = sum(h for h, _ in blocks)
    y = top + max(0, (bottom - top - total) * bias)
    for h, fn in blocks:
        fn(y)
        y += h
    return y


def line_ends(lines, ty, x, y, align="left", boxw=0):
    """(right_x, top_y, bottom_y) for each line, to place stickers beside text."""
    out = []
    for i, (_, width) in enumerate(lines):
        lx = x if align == "left" else x + (boxw - width) / 2
        out.append((lx + width, y + i * ty.lh, y + (i + 1) * ty.lh))
    return out


def inter(size, w="Regular"):
    return F("inter", size, w)


# ------------------------------------------------------------------ the carousel model

def pieces(post, design, label):
    """Turns a post + design into a list of slide specs."""
    out = [{"kind": "cover", "text": mark(post["hook"].replace("**", ""), design["cover"].get("highlight")),
            **design["cover"]}]
    ds = design.get("slides", [])
    for i, s in enumerate(post["slides"]):
        lab, body = split_label(s)
        extra = ds[i] if i < len(ds) else {}
        out.append({"kind": "body", "label": lab, "text": body, "heading": extra.get("heading", ""),
                    "emoji": extra.get("emoji", ""), "note": extra.get("note", ""), "quote": extra.get("quote", ""),
                    "index": i + 1})
    t = design.get("takeaway", {})
    out.append({"kind": "takeaway", "label": label, "text": post["takeaway"], "heading": t.get("heading", ""),
                "emoji": t.get("emoji", ""), "note": t.get("note", "")})
    return out


class Style:
    name = "base"

    def __init__(self, accent, tag, handle, total, series_name):
        self.accent, self.tag, self.handle, self.total, self.series = rgb(accent), tag, handle, total, series_name

    def cover(self, s, i):
        raise NotImplementedError

    def body(self, s, i):
        raise NotImplementedError

    def takeaway(self, s, i):
        return self.body(s, i)

    def closing(self, text, i):
        return self.takeaway({"kind": "takeaway", "label": "", "text": text, "heading": "", "emoji": "", "note": ""}, i)


# ================================================================== POSTER

class Poster(Style):
    name = "poster"
    bg, ink, dot = rgb("#F1EEE8"), rgb("#111111"), (214, 209, 200)

    def base(self, i, small=False):
        img = Image.new("RGB", (W, H), self.bg)
        d = ImageDraw.Draw(img)
        dot_grid(d, self.dot)
        logo(img, (70, 70), 72 if small else 88)
        spaced(d, (W - 70, 96 if not small else 92), self.tag.replace("#", " #"), F("mono-b", 22), self.ink, 5, right=True)
        return img, d

    def cover(self, s, i):
        img, d = self.base(i)
        text = safe(s["text"])
        maker = lambda z: Type(F("anton", z), F("anton", z), int(z * 1.02), upper=True)
        ty, lines = fit(text, maker, range(210, 90, -6), W - 140, 800, 5)
        top = 250 + max(0, (800 - len(lines) * ty.lh) // 3)
        draw_text(d, lines, ty, 70, top, (self.ink, self.accent))
        # stickers beside the shortest lines
        ems = [e for e in s.get("emoji", []) if e][:2]
        ends = sorted(line_ends(lines, ty, 70, top), key=lambda t: t[0])
        size = int(min(200, ty.lh * 0.95))
        used, below = [], top + len(lines) * ty.lh + 30
        for e in ems:
            ang = random.Random(e).choice([-12, -8, 8, 12])
            for rx, t, b in ends:
                if rx + size + 40 < W - 40 and all(abs(t - u) > ty.lh * 0.9 for u in used):
                    sticker(img, e, (rx + 30, t + (b - t - size) / 2), size, angle=ang)
                    used.append(t)
                    break
            else:  # no room beside any line: use the space under the headline
                small = min(170, H - 190 - below)
                if small >= 100:
                    sticker(img, e, (W - 110 - small - len(used) * (small + 30), below), small, angle=ang)
                    used.append(-999 - len(used))
        sub = safe(s.get("subtitle") or "")
        if sub:
            f = inter(34, "SemiBold")
            sl = layout(sub, Type(f, f, 44), 600)[:2]
            draw_text(d, sl, Type(f, f, 44), 70, H - 100 - 44 * len(sl), (self.ink, self.ink))
        d.rounded_rectangle((W - 290, H - 140, W - 70, H - 70), radius=35, fill=self.ink)
        d.text((W - 262, H - 128), "Swipe", font=inter(36, "Bold"), fill=self.bg)
        arrow(d, W - 140, H - 105, 46, self.bg, width=6, head=14)
        return img

    def body(self, s, i, number=None):
        img, d = self.base(i, small=True)
        num = number or f"{s['index']:02d}"
        d.text((66, 190), num, font=F("anton", 200 if len(num) < 4 else 130), fill=self.accent)
        sticker(img, s.get("emoji"), (W - 270, 200), 190, angle=8)
        top, bottom = 440, H - (250 if s.get("note") else 200)
        blocks = []
        if s.get("label"):
            lab, lf = safe(s["label"]).upper(), F("mono-b", 24)
            blocks.append((52, lambda y: spaced(d, (72, y), lab, lf, self.ink, 5)))
        head = safe(s.get("heading") or "")
        if head:
            mk = lambda z: Type(F("anton", z), F("anton", z), int(z * 1.02), upper=True)
            hty, hl = fit(head, mk, range(130, 60, -6), W - 140, 270, 2)
            blocks.append((len(hl) * hty.lh + 48, lambda y: draw_text(d, hl, hty, 70, y, (self.ink, self.accent))))
        tint = mix(self.accent, self.bg, 0.68)

        def marker(d, x0, x1, base, ty):
            d.rectangle((x0 - 6, base - ty.n.size * 0.38, x1 + 6, base + ty.n.size * 0.12), fill=tint)

        mk = lambda z: Type(inter(z, "Medium"), inter(z, "ExtraBold"), int(z * 1.4))
        used = sum(h for h, _ in blocks)
        ty, lines = fit(safe(s["text"]), mk, range(60, 30, -2), W - 150, bottom - top - used)
        blocks.append((len(lines) * ty.lh, lambda y: draw_text(d, lines, ty, 70, y, (self.ink, self.ink), deco=marker)))
        stack(blocks, top, bottom, 0.35)
        note = safe(s.get("note") or "")
        if note:
            ok = sticker(img, "👉", (70, H - 190), 76, shadow=False)
            d.text((170 if ok else 70, H - 176), note, font=inter(34, "SemiBold"), fill=self.ink)
        page_dots(d, W - 170, H - 150, self.total, i, self.ink, (190, 185, 176))
        return img

    def takeaway(self, s, i):
        label = (s.get("label") or "Takeaway").upper()
        s = dict(s, heading=s.get("heading") or "", label="")
        return self.body(s, i, number=label)


# ================================================================== EDITORIAL

class Editorial(Style):
    name = "editorial"
    bg, ink, grid, mute = rgb("#F6F1E7"), rgb("#1B1B1B"), (232, 225, 211), rgb("#5A544B")

    @property
    def pen(self):
        return rgb("#C8452B")

    def base(self, kicker, i):
        img = Image.new("RGB", (W, H), self.bg)
        wavy_grid(ImageDraw.Draw(img), self.grid)
        img = paper(img, seed=i + 2)
        d = ImageDraw.Draw(img)
        spaced(d, (70, 92), kicker.upper(), F("mono-b", 23), self.pen if i else self.ink, 6)
        logo(img, (W - 150, 70), 80)
        spaced(d, (W - 70, H - 92), f"{i + 1:02d} / {self.total:02d}", F("mono", 24), self.ink, 4, right=True)
        return img, d

    def hl(self, d, x0, x1, base, ty):
        d.rectangle((x0 - 18, base - ty.n.size * 0.62, x1 + 18, base + ty.n.size * 0.16), fill=self.accent)

    def note(self, d, text, x, y, target=None):
        if not text:
            return
        f = F("hand", 54)
        d.text((x, y), text, font=f, fill=self.pen)
        if target:
            tx, ty_ = target
            sx, sy = x - 12, y + 30
            hand_arrow(d, (sx, sy), (sx - 80, sy), (tx + 10, ty_ + 90), (tx, ty_), self.pen, width=4, head=20)

    def cover(self, s, i):
        img, d = self.base("Marketing psychology, decoded", i)
        maker = lambda z: Type(F("serif", z), F("serif-i", z), int(z * 1.08))
        ty, lines = fit(safe(s["text"]), maker, range(156, 80, -4), W - 180, 700, 5)
        top = 230 + (760 - len(lines) * ty.lh) // 2
        y = draw_text(d, lines, ty, 90, top, (self.ink, self.ink), align="centre", deco=self.hl, boxw=W - 180)
        sub = safe(s.get("subtitle") or "")
        if sub:
            f = F("quote", 48)
            sl = layout(sub, Type(f, f, 58), W - 220)[:2]
            draw_text(d, sl, Type(f, f, 58), 110, y + 40, (self.mute, self.mute), align="centre", boxw=W - 220)
        note = safe(s.get("note") or "") or "swipe to see why"
        f = F("hand", 52)
        d.text((70, H - 180), note, font=f, fill=self.pen)
        x = 70 + f.getlength(note) + 30
        if x < W - 260:
            hand_arrow(d, (x, H - 150), (x + 50, H - 180), (x + 100, H - 150), (x + 150, H - 160), self.pen, width=4, head=20)
        return img

    def body(self, s, i):
        kicker = f"{s.get('index', i):02d}  /  {s.get('label') or 'the idea'}"
        img, d = self.base(kicker, i)
        if s.get("emoji"):
            sticker(img, s["emoji"], (W - 210, 210), 130, angle=6)
        top, bottom = 240, H - (300 if s.get("note") else 180)
        blocks = []
        head = safe(s.get("heading") or "")
        if head:
            mk = lambda z: Type(F("serif", z), F("serif-i", z), int(z * 1.1))
            hty, hl = fit(head, mk, range(112, 64, -4), W - 260 if s.get("emoji") else W - 140, 380, 3)
            blocks.append((len(hl) * hty.lh + 50, lambda y: draw_text(d, hl, hty, 70, y, (self.ink, self.ink), deco=self.hl)))
        quote = safe(s.get("quote") or "")
        qf = F("quote", 52)
        qt = Type(qf, qf, 62)
        ql = layout(f"\u201c{quote}\u201d", qt, W - 220)[:2] if quote else []
        qh = (50 + 62 * len(ql)) if ql else 0
        mk = lambda z: Type(inter(z, "Regular"), inter(z, "SemiBold"), int(z * 1.45))
        used = sum(h for h, _ in blocks)
        ty, lines = fit(safe(s["text"]), mk, range(52, 30, -2), W - 160, bottom - top - used - qh)
        blocks.append((len(lines) * ty.lh, lambda y: draw_text(d, lines, ty, 70, y, (self.ink, self.ink))))
        if ql:
            def q(y):
                d.line((70, y + 46, 70, y + 46 + 62 * len(ql)), fill=self.pen, width=4)
                draw_text(d, ql, qt, 100, y + 40, (self.ink, self.ink))
            blocks.append((qh, q))
        end = stack(blocks, top, bottom, 0.3)
        if s.get("note"):
            self.note(d, safe(s["note"]), 560, H - 250, target=(440, min(end + 25, H - 310)))
        return img

    def takeaway(self, s, i):
        img, d = self.base(s.get("label") or "Takeaway", i)
        head = safe(s.get("heading") or "")
        y = 260
        if head:
            ty = Type(F("serif-i", 84), F("serif-i", 84), 92)
            y = draw_text(d, layout(head, ty, W - 140)[:2], ty, 70, y, (self.pen, self.pen)) + 40
        text = safe(s["text"])
        if "**" not in text:
            words = text.split()
            text = mark(text, " ".join(words[:3])) if len(words) > 4 else text
        mk = lambda z: Type(F("serif", z), F("serif-i", z), int(z * 1.18))
        ty, lines = fit(text, mk, range(80, 44, -4), W - 160, H - 300 - y)
        draw_text(d, lines, ty, 70, y, (self.ink, self.ink), deco=self.hl)
        if s.get("emoji"):
            sticker(img, s["emoji"], (W - 210, H - 330), 120, angle=-6)
        if s.get("note"):
            self.note(d, safe(s["note"]), 70, H - 200)
        return img


# ================================================================== ESSAY

class Essay(Style):
    name = "essay"
    light = {"bg": rgb("#EFE9DD"), "ink": rgb("#151413"), "mute": rgb("#4A463F"), "soft": rgb("#7A746B"), "track": rgb("#CFC7B8")}
    dark = {"bg": rgb("#1E1C1A"), "ink": rgb("#F1EBDF"), "mute": rgb("#A59E92"), "soft": rgb("#8A8378"), "track": rgb("#3A3733")}

    def base(self, i):
        p = self.light if i % 2 == 0 else self.dark
        img = Image.new("RGB", (W, H), p["bg"])
        img = paper(img, seed=i + 5) if i % 2 == 0 else grain(img, 6, seed=i)
        d = ImageDraw.Draw(img)
        spaced(d, (70, 92), self.handle.upper(), F("mono", 22), p["soft"], 5)
        logo(img, (W - 146, 66), 76, box=p["ink"], ink=p["bg"])
        tag = self.tag.replace("#", "  -  ")
        spaced(d, (70, H - 100), tag, F("mono", 22), p["soft"] if i else p["ink"], 5)
        right = "SWIPE  >" if i == 0 else f"{i + 1} / {self.total}"
        spaced(d, (W - 70, H - 100), right, F("mono-b" if i == 0 else "mono", 22), p["soft"] if i else p["ink"], 5, right=True)
        progress(d, 70, H - 60, W - 140, (i + 1) / self.total, p["track"], p["ink"])
        return img, d, p

    def band(self, p):
        def deco(d, x0, x1, base, ty):
            top = base - ty.n.size * 0.80
            d.rectangle((x0 - 14, top, x1 + 14, base + ty.n.size * 0.10), fill=self.accent)
        return deco

    def cover(self, s, i):
        img, d, p = self.base(i)
        mk = lambda z: Type(F("anton", z), F("anton", z), int(z * 1.08), upper=True)
        ty, lines = fit(safe(s["text"]), mk, range(196, 90, -6), W - 160, 720, 5)
        top = 270 + (720 - len(lines) * ty.lh) // 2
        y = draw_text(d, lines, ty, 84, top, (p["ink"], p["bg"]), deco=self.band(p))
        sub = safe(s.get("subtitle") or "")
        if sub:
            f = inter(36, "Medium")
            sl = layout(sub, Type(f, f, 48), 760)[:2]
            draw_text(d, sl, Type(f, f, 48), 70, y + 70, (p["mute"], p["mute"]))
        return img

    def body(self, s, i, label=None):
        img, d, p = self.base(i)
        lab = label or (f"\u00a7 {s['label']}" if s.get("label") else f"\u00a7 {s.get('index', i):02d}")
        spaced(d, (70, 250), lab.upper(), F("mono-b", 26), self.accent, 5)
        if s.get("emoji"):
            sticker(img, s["emoji"], (W - 200, 230), 110, angle=6, shadow=i % 2 == 0)
        top, bottom = 320, H - 170
        blocks = []
        head = safe(s.get("heading") or "")
        if head:
            mk = lambda z: Type(F("anton", z), F("anton", z), int(z * 1.12), upper=True)
            hty, hl = fit(head, mk, range(170, 70, -6), W - 180, 400, 2)
            blocks.append((len(hl) * hty.lh + 40, lambda y: draw_text(d, hl, hty, 84, y, (p["ink"], p["ink"]), deco=self.band(p))))
        blocks.append((44, lambda y: d.line((70, y, 160, y), fill=p["mute"], width=3)))
        quote = safe(s.get("quote") or "")
        qf = F("quote", 52)
        qt = Type(qf, qf, 62)
        ql = layout(f"\u201c{quote}\u201d", qt, W - 220)[:2] if quote else []
        qh = (56 + 62 * len(ql)) if ql else 0
        mk = lambda z: Type(inter(z, "Regular"), inter(z, "SemiBold"), int(z * 1.48))
        used = sum(h for h, _ in blocks)
        ty, lines = fit(safe(s["text"]), mk, range(48, 28, -2), W - 160, bottom - top - used - qh)
        blocks.append((len(lines) * ty.lh, lambda y: draw_text(d, lines, ty, 70, y, (p["mute"], p["ink"]))))
        if ql:
            def q(y):
                d.line((70, y + 52, 70, y + 52 + 62 * len(ql)), fill=self.accent, width=4)
                draw_text(d, ql, qt, 100, y + 46, (p["ink"], p["ink"]))
            blocks.append((qh, q))
        stack(blocks, top, bottom, 0.3)
        return img

    def takeaway(self, s, i):
        text = s["text"].strip()
        m = re.match(r"^\**\s*(myth|magic|it depends)\b[\s,.!:;–-]*\**\s*", text, re.I)
        heading = s.get("heading") or ""
        if m:
            heading, text = m.group(1), text[m.end():]
            text = text[:1].upper() + text[1:]
        heading = f"**{heading.replace('*', '')}**" if heading else ""
        return self.body(dict(s, heading=heading, text=text, quote=""), i,
                         label=f"§ {s.get('label') or 'Takeaway'}")


# ================================================================== NOTEBOOK

class Notebook(Style):
    name = "notebook"
    bg, ink, grey, line, margin = rgb("#F7F0DC"), rgb("#121212"), rgb("#8C877C"), (226, 216, 190), (239, 180, 170)

    def base(self, i):
        img = Image.new("RGB", (W, H), self.bg)
        ruled(ImageDraw.Draw(img), self.line, self.margin)
        img = paper(img, seed=i + 9)
        d = ImageDraw.Draw(img)
        logo(img, (150, 70), 70)
        spaced(d, (W - 70, 92), self.tag.replace("#", " #"), F("mono-b", 22), self.ink, 5, right=True)
        return img, d

    def under(self, d, x0, x1, base, ty):
        scribble(d, x0, x1, base + ty.n.size * 0.16, self.accent, width=max(5, int(ty.n.size / 16)), seed=int(x0))

    def note(self, d, text, x, y, target=None, size=58):
        if not text:
            return
        f = F("hand", size)
        lines = layout(text, Type(f, f, int(size * 1.05)), 380)[:2]
        draw_text(d, lines, Type(f, f, int(size * 1.05)), x, y, (self.accent, self.accent))
        if target:
            tx, ty_ = target
            hand_arrow(d, (x + 100, y - 10), (x + 140, y - 90), (tx + 60, ty_ + 80), (tx, ty_), self.accent, width=5, head=22)

    def cover(self, s, i):
        img, d = self.base(i)
        mk = lambda z: Type(inter(z, "Black"), inter(z, "Black"), int(z * 1.16))
        ty, lines = fit(safe(s["text"]), mk, range(132, 66, -4), W - 230, 760, 5)
        top = 300
        y = draw_text(d, lines, ty, 150, top, (self.ink, self.ink), deco=self.under)
        if s.get("emoji"):
            sticker(img, s["emoji"][0], (W - 250, 190), 150, angle=-10)
        if len(s.get("emoji", [])) > 1 and y < H - 420:
            sticker(img, s["emoji"][1], (150, y + 40), 110, angle=8)
        note = safe(s.get("note") or s.get("subtitle") or "")
        if note:
            self.note(d, f"({note})" if not note.startswith("(") else note, 600, min(y + 70, H - 330),
                      target=(560, y - 20))
        d.ellipse((150, H - 165, 220, H - 95), fill=self.ink)
        f = F("kumbh", 28)
        d.text((185 - f.getlength("MF") / 2, H - 146), "MF", font=f, fill=self.bg)
        d.text((240, H - 148), self.handle, font=inter(32, "SemiBold"), fill=self.ink)
        d.text((W - 230, H - 148), "Swipe", font=inter(32, "SemiBold"), fill=self.ink)
        arrow(d, W - 120, H - 128, 46, self.ink, width=5, head=14)
        return img

    def body(self, s, i):
        img, d = self.base(i)
        label = (s.get("label") or "").strip()
        kind = label.lower()
        if s.get("emoji"):
            sticker(img, s["emoji"], (W - 230, 200), 140, angle=8)
        top, bottom = 240, H - (310 if s.get("note") else 190)
        blocks = []
        if label:
            stamp, sf = label.upper(), F("mono-b", 26)

            def st(y):
                wdt = spaced(d, (150, y), stamp, sf, self.grey if kind == "before" else self.accent, 5)
                if kind in ("before", "after"):
                    sticker(img, "❌" if kind == "before" else "✅", (150 + wdt + 20, y - 12), 52, shadow=False)
            blocks.append((72, st))
        head = safe(s.get("heading") or "")
        if head:
            mk = lambda z: Type(inter(z, "Black"), inter(z, "Black"), int(z * 1.15))
            hty, hl = fit(head, mk, range(88, 50, -4), W - 230 - (170 if s.get("emoji") else 0), 300, 3)
            blocks.append((len(hl) * hty.lh + 50, lambda y: draw_text(d, hl, hty, 150, y, (self.ink, self.ink), deco=self.under)))
        mk = lambda z: Type(inter(z, "Medium"), inter(z, "Bold"), int(z * 1.3))
        used = sum(h for h, _ in blocks)
        ty, lines = fit(safe(s["text"]), mk, range(54, 30, -2), W - 230, bottom - top - used)
        if kind == "before":
            blocks.append((len(lines) * ty.lh, lambda y: draw_text(d, lines, ty, 150, y, (self.grey, self.grey))))
        else:
            blocks.append((len(lines) * ty.lh, lambda y: draw_text(d, lines, ty, 150, y, (self.ink, self.ink), deco=self.under)))
        end = stack(blocks, top, bottom, 0.3)
        if s.get("note"):
            self.note(d, safe(s["note"]), 560, H - 270, target=(470, min(end + 20, H - 310)))
        page_dots(d, W / 2 + 40, H - 110, self.total, i, self.ink, (205, 196, 172))
        return img

    def takeaway(self, s, i):
        img, d = self.base(i)
        label = (s.get("label") or "Takeaway").upper()
        f = F("mono-b", 26)
        spaced(d, (150, 240), label, f, self.accent, 5)
        head = safe(s.get("heading") or "")
        y = 310
        if head:
            ty = Type(inter(90, "Black"), inter(90, "Black"), 104)
            y = draw_text(d, layout(head, ty, W - 400)[:2], ty, 150, y, (self.ink, self.ink)) + 40
        sticker(img, s.get("emoji") or "✅", (W - 250, 230), 150, angle=-8)
        mk = lambda z: Type(inter(z, "SemiBold"), inter(z, "Black"), int(z * 1.3))
        ty, lines = fit(safe(s["text"]), mk, range(60, 34, -2), W - 230, H - 320 - y)
        y = draw_text(d, lines, ty, 150, y, (self.ink, self.ink), deco=self.under)
        if s.get("note"):
            self.note(d, safe(s["note"]), 560, H - 260)
        page_dots(d, W / 2 + 40, H - 110, self.total, i, self.ink, (205, 196, 172))
        return img


STYLES = {"poster": Poster, "editorial": Editorial, "essay": Essay, "notebook": Notebook}


def render(post, design, style, accent, tag, out_dir: Path, handle="@misfit_marketers", label="Takeaway",
           closing=None, series_name=""):
    out_dir.mkdir(parents=True, exist_ok=True)
    specs = pieces(post, design, label)
    total = min(10, len(specs) + (1 if closing else 0))
    st = STYLES[style](accent, tag, handle, total, series_name)
    images = []
    for i, s in enumerate(specs):
        fn = {"cover": st.cover, "body": st.body, "takeaway": st.takeaway}[s["kind"]]
        images.append(fn(s, i))
    if closing:
        images.append(st.closing(closing, len(images)))
    paths = []
    for i, img in enumerate(images[:10], start=1):
        p = out_dir / f"{i:02d}.jpg"
        img.convert("RGB").save(p, "JPEG", quality=92, optimize=True)
        paths.append(p)
    return paths
