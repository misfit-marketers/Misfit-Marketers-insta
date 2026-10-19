"""Art direction for the new slide styles.

After the writer has the words, a second, small AI call decides how each
slide should look: which words to highlight, a short heading per slide, an
emoji sticker or two, and an optional handwritten aside. The answer is saved
in post["design"] together with a fingerprint of the words, so an edit to
the text automatically gets fresh art direction the next time it's drawn.

If the AI call fails for any reason, a plain design is worked out from the
**bold** words instead, so a draft is never blocked by this step."""
import hashlib
import json
import re

from .common import DRY_RUN, clean_text

# Every emoji here is a single character that the bundled Noto Color Emoji
# font can draw. The AI may only pick from this list.
EMOJI = (
    "🧠 💡 🎯 📈 📉 💸 💰 💵 🛒 🛍 🏷 ⏳ ⌛ ⏰ 🔥 ⚡ ✨ 🚀 👀 👉 👆 👇 ✅ ❌ ⚠ ❓ ❗ 💬 🗣 📣 📢 🤔 🤯 😬 😅 🙃 😍 🥲 🤝 💪 "
    "🙌 👏 🫶 ❤ 💔 ⭐ 🏆 🥇 🎁 🎉 📌 📍 🔑 🔒 🔓 🧲 🪝 🎣 🧩 🔍 🔎 📊 🧪 🔬 📚 📖 ✍ ✏ 📝 📱 💻 🖥 📺 🎬 🍿 🎧 🎵 ☕ 🍔 "
    "🍕 🍫 🥤 🛎 🚪 🏪 🏬 🧾 💳 🎰 🎲 🧭 🗺 ⚖ 🪞 🎭 🦄 🐑 🦋 🌱 🌊 ❄ ☀ 🌙 ⛔ 🚫 💯 🔁 ➕ ➖ 🆕 🆓"
).split()
EMOJI_SET = set(EMOJI)

SERIES_HINTS = {
    "poster": "Bold poster look: huge capitals, 1 or 2 playful emoji stickers on the cover, an emoji on most slides.",
    "editorial": "Calm editorial magazine look: elegant serif headings, one highlighted phrase, a handwritten aside with an arrow. Use emoji sparingly (cover none, slides optional).",
    "essay": "Bold essay poster look: capital headings with one word on a red band, a section label, and a short pull quote. Emoji are rarely needed.",
    "notebook": "Handwritten notebook look: bold headline, words underlined in pen, handwritten asides with arrows, emoji used like stickers.",
}


# Bump when the art-direction rules change, so drafts that haven't posted yet
# get redesigned with the new rules (main.refresh_old_designs).
# 2: headings never repeat the slide text (Sachin, 2026-10-16).
DESIGN_VERSION = 2

STOP = set("a an the and or but of to in on for with at by from is are was be it its this that these those "
           "your you we our their they his her as so if not no do does did can will just than then".split())


def _words_of(text):
    return [w for w in re.sub(r"[^a-z0-9$%' ]", " ", plain(text).lower()).split() if w]


def heading_prefix(heading: str, body: str) -> int:
    """How many words at the start of body repeat the heading (0 if it doesn't start with it)."""
    h, b = _words_of(heading), _words_of(body)
    if len(h) >= 2 and b[:len(h)] == h:
        return len(h)
    return 0


def repeats(heading: str, body: str) -> bool:
    """True when the heading mostly restates words that are already in the slide text."""
    key = [w for w in _words_of(heading) if w not in STOP and len(w) > 2]
    if len(key) < 2:
        return False  # one shared word (the topic itself) is fine
    found = set(_words_of(body))
    return sum(w in found for w in key) / len(key) >= 0.6


def fingerprint(post: dict) -> str:
    raw = json.dumps([post.get("hook"), post.get("slides"), post.get("takeaway")], ensure_ascii=False)
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def plain(text: str) -> str:
    return re.sub(r"\*\*", "", text or "").strip()


def first_bold(text: str) -> str:
    m = re.search(r"\*\*(.+?)\*\*", text or "")
    return m.group(1).strip() if m else ""


# "The belief: ..." or "**The belief:** ...". The closing ** is only eaten when
# the label itself was bold, so a bold phrase right after the colon survives.
LABEL = re.compile(r"^\s*(\*\*)?\s*([A-Z][A-Za-z' ]{1,24}?)\s*:\s*(?(1)\*\*)\s*")


def split_label(text: str):
    """'The belief: Marketers assume...' -> ('The belief', 'Marketers assume...')."""
    m = LABEL.match(text or "")
    if m and len(m.group(2).split()) <= 4:
        rest = text[m.end():].strip()
        k = 2 if rest.startswith("**") else 0
        return m.group(2).strip(), rest[:k] + rest[k:k + 1].upper() + rest[k + 1:]
    return "", (text or "").strip()


def _words(text, n):
    w = plain(text).split()
    return " ".join(w[:n])


def fallback(post: dict, style: str) -> dict:
    """A sensible design with no AI: highlight the first bold phrase."""
    slides = []
    for s in post.get("slides", []):
        label, body = split_label(s)
        slides.append({"heading": "", "emoji": "", "note": "", "quote": ""})  # no heading beats a repeated one
    hook = plain(post.get("hook", ""))
    words = hook.split()
    hl = " ".join(words[len(words) // 3: len(words) // 3 + 2]) if len(words) > 3 else ""
    return {"cover": {"highlight": hl, "emoji": [], "subtitle": post.get("principle", ""), "note": ""},
            "slides": slides, "takeaway": {"heading": "", "emoji": "", "note": ""}}


def _clean_emoji(e):
    e = (e or "").replace("️", "").strip()
    return e if e in EMOJI_SET else ""


def _short(text, words, chars=60):
    t = clean_text(plain(str(text or "")))
    t = " ".join(t.split()[:words])
    return t[:chars].strip(" ,;:-")


BIG_MAX_WORDS = 18
QUOTE_STYLES = ("editorial", "essay")


def validate(raw: dict, post: dict, style: str = "") -> dict:
    n = len(post.get("slides", []))
    hook = plain(post.get("hook", ""))
    cover = raw.get("cover") or {}
    hl = _short(cover.get("highlight"), 4)
    if hl and hl.lower() not in hook.lower():
        hl = ""  # must be words that really are in the hook
    ems = cover.get("emoji") or []
    if isinstance(ems, str):
        ems = [ems]
    cover_out = {"highlight": hl, "emoji": [e for e in (_clean_emoji(x) for x in ems) if e][:2],
                 "subtitle": _short(cover.get("subtitle"), 12, 80), "note": _short(cover.get("note"), 7, 40)}
    slides_in = raw.get("slides") or []
    slides = []
    for i in range(n):
        s = slides_in[i] if i < len(slides_in) and isinstance(slides_in[i], dict) else {}
        head = clean_text(str(s.get("heading") or "")).replace("*", "")
        hw = (s.get("heading_highlight") or "").strip()
        head = " ".join(head.split()[:5])[:44]
        if hw and hw.lower() in head.lower():
            idx = head.lower().index(hw.lower())
            head = head[:idx] + "**" + head[idx:idx + len(hw)] + "**" + head[idx + len(hw):]
        body = split_label(post["slides"][i])[1]
        # The heading sits right above the slide text, so it must not say the same
        # thing twice. If the text starts with the heading, the slide is drawn as
        # one continuous line (heading, then the rest of the sentence); otherwise a
        # heading that just restates the text is dropped.
        if head and not heading_prefix(head, body) and repeats(head, body):
            head = ""
        layout = str(s.get("layout") or "standard").strip().lower()
        words = len(plain(split_label(post["slides"][i])[1]).split())
        if layout != "big" or words > BIG_MAX_WORDS:
            layout = "standard"
        slides.append({"heading": head, "emoji": _clean_emoji(s.get("emoji")), "layout": layout,
                       "note": _short(s.get("note"), 7, 40), "quote": _short(s.get("quote"), 14, 95)})
    # Variety, not clutter: at most 2 big statement slides and at most 1 pull
    # quote per carousel (quotes only where the look uses them, and never on a
    # big slide or a slide that is already short).
    bigs = [k for k, x in enumerate(slides) if x["layout"] == "big"]
    for k in bigs[2:]:
        slides[k]["layout"] = "standard"
    seen_quote = False
    for k, x in enumerate(slides):
        short = len(plain(split_label(post["slides"][k])[1]).split()) < 14
        if x["quote"] and (seen_quote or style not in QUOTE_STYLES or x["layout"] == "big" or short):
            x["quote"] = ""
        seen_quote = seen_quote or bool(x["quote"])
    t = raw.get("takeaway") or {}
    take = {"heading": _short(t.get("heading"), 4, 30), "emoji": _clean_emoji(t.get("emoji")),
            "note": _short(t.get("note"), 7, 40)}
    return {"cover": cover_out, "slides": slides, "takeaway": take}


def build_prompt(post: dict, series: str, style: str, note: str = "", spelling: str = "American English") -> str:
    slides = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(post["slides"]))
    note_line = (f"\nEDITOR'S NOTE (follow it as long as it fits the rules below): {note.strip()}\n"
                 if (note or "").strip() else "")
    return f"""You are the art director for MISFIT, an Instagram page about marketing psychology.
A writer finished this carousel. Do NOT change the wording. Decide how it should look.

SERIES: {series}
LOOK: {SERIES_HINTS.get(style, '')}
{note_line}
HOOK (cover): {post['hook']}
BODY SLIDES:
{slides}
FINAL SLIDE: {post['takeaway']}

Rules:
- cover.highlight: 1 to 3 consecutive words copied exactly from the hook, the words that create the curiosity.
- cover.subtitle: one short line (at most 10 words) that teases what the carousel teaches. Plain, no hype.
- cover.emoji: 0 to 2 emoji stickers that fit the topic. Choose ONLY from this list: {' '.join(EMOJI)}
- cover.note: a tiny handwritten aside, at most 6 words, lower case, friendly (for example "your brain does this daily"), or "".
- slides: exactly {len(post['slides'])} objects, one per body slide, in order. Each has:
  - heading: 2 to 4 punchy words shown in big type right above the slide's text. The text is printed in full underneath, so the heading must NEVER repeat or paraphrase it. Either frame it with a fresh angle (for example heading "The $5 latte" over "A latte at the top of the menu makes every other drink look cheap") or use the slide's own opening words so the text reads as the continuation (for example heading "Logic dominates when" over "Logic dominates when risk is high"; the repeated words are then shown only once). No colon.
  - heading_highlight: one word from the heading to emphasise.
  - emoji: one emoji from the list that fits the slide, or "" if nothing fits. Never repeat the same emoji twice in a row.
  - note: a tiny handwritten aside for at most 2 of the slides (at most 6 words), otherwise "".
  - layout: "big" or "standard". "big" shows only the slide's words, very large, like a poster line; use it for 1 or 2 short, punchy slides (the ones that land the idea), so the carousel has rhythm. Everything else is "standard".
  - quote: usually "". At most ONE slide in the whole carousel may have a pull quote (at most 10 words restating that slide's point), and only if it adds punch. Never add facts, numbers or names that the slide does not contain.
- takeaway: {{"heading": 1 to 3 words for the final slide, "emoji": one emoji from the list, "note": "" or at most 6 words}}.
- Spelling: {spelling}. Never use em dashes. Avoid "it's not X, it's Y" phrasing.

Return ONLY JSON:
{{"cover": {{"highlight": "", "subtitle": "", "emoji": [], "note": ""}},
 "slides": [{{"heading": "", "heading_highlight": "", "emoji": "", "note": "", "quote": ""}}],
 "takeaway": {{"heading": "", "emoji": "", "note": ""}}}}"""


def _ask_ai(cfg: dict, prompt: str) -> dict:
    from . import writer
    errors = []
    for service, key in writer.providers():
        try:
            if service == "groq":
                models, call = writer._groq_models(key, cfg.get("groq_model", "auto"))[:2], writer._groq_call
            else:
                models, call = writer._pick_models(key, cfg.get("gemini_model", "auto"))[:2], writer._call
        except Exception as e:
            errors.append(str(e)[:200])
            continue
        for model in models:
            try:
                text = call(key, model, prompt)
                text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                return json.loads(text)
            except Exception as e:
                errors.append(str(e)[:200])
    raise RuntimeError("; ".join(errors[-3:]) or "no AI key set")


def ensure(post: dict, series: str, style: str, cfg: dict) -> dict:
    """Returns post['design'], creating it (or refreshing it after an edit).
    post["design_note"] (set by the app's Redesign) steers the art director."""
    fp = fingerprint(post)
    note = str(post.get("design_note") or "")[:300]
    d = post.get("design")
    if (isinstance(d, dict) and d.get("for") == fp and d.get("style") == style and d.get("note", "") == note
            and d.get("v") == DESIGN_VERSION):
        return d
    if DRY_RUN:
        raw, source = sample_design(post), "sample"
    else:
        try:
            raw, source = _ask_ai(cfg, build_prompt(post, series, style, note, ((cfg.get("writing") or {}).get("spelling") or "American English").split(" (")[0])), "ai"
        except Exception as e:
            print(f"Design step fell back to plain highlights: {e}")
            raw, source = fallback(post, style), "fallback"
    d = validate(raw, post, style)
    d.update({"for": fp, "style": style, "source": source, "note": note, "v": DESIGN_VERSION})
    post["design"] = d
    return d


def sample_design(post: dict) -> dict:
    """Used in DRY_RUN tests: a rich design built without any AI."""
    pool = ["☕", "🧠", "🎯", "💸", "📈", "👀", "🛒", "🔥"]
    hook = plain(post.get("hook", "")).split()
    hl = " ".join(hook[2:4]) if len(hook) > 4 else (hook[-1] if hook else "")
    slides = []
    for i, s in enumerate(post.get("slides", [])):
        label, body = split_label(s)
        words = (first_bold(body) or _words(body, 3)).split()[:3]
        slides.append({"heading": " ".join(words).title(), "heading_highlight": words[-1] if words else "",
                       "emoji": pool[i % len(pool)], "note": "you've felt this" if i == 0 else "",
                       "quote": _words(body, 10) if i == 1 else "", "layout": "big" if i == 2 else "standard"})
    return {"cover": {"highlight": hl, "emoji": ["💸", "🧠"], "subtitle": post.get("principle", ""),
                      "note": "your brain does this daily"},
            "slides": slides, "takeaway": {"heading": "Try this", "emoji": "✅", "note": "save this one"}}
