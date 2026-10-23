"""Scripts for the format Reels (slot 2): the AI writes a short script in the
shape each format needs, the code checks it, and reelfx.py draws it.

pick(...) chooses a format at random (never the same one twice in a row, and
only formats that can run here), write(...) asks the writer for the script."""
import json
import os
import random
import re
import time

from . import writer
from .common import DRY_RUN, clean_text

NAMES = {"chat": "Text chat", "quiz": "Guess and reveal", "rewrite": "Live rewrite", "kinetic": "Big-text story",
         "mockui": "Mock app screen", "flow": "Workflow diagram", "list": "Quick list", "loop": "7-second loop",
         "voice": "Voice-over", "stock": "Stock footage"}

SPECS = {
    "chat": ("A text-message conversation between a marketer who has a problem and a sharp peer who explains the "
             "psychology. 6 to 10 messages, alternating naturally (a sender can send two in a row). Each message at most "
             "14 words, casual like real texts. 'them' is the person with the problem (they text first), 'me' is the peer. "
             "End with a one-line lesson (at most 9 words).",
             {"contact": "first name of the person texting", "role": "their job in 2 or 3 words",
              "messages": [{"from": "them", "text": "..."}, {"from": "me", "text": "..."}],
              "lesson": "..."}),
    "quiz": ("A 'guess first' quiz. A short question (at most 8 words) and 2 options (rarely 3), each at most 10 words, "
             "where one option is the one that usually wins according to well-known psychology. Then 2 or 3 'why' lines, "
             "each at most 9 words. Only use findings you are confident about; say 'usually' rather than inventing numbers.",
             {"question": "...", "options": ["...", "..."], "answer": 1, "why": ["...", "..."]}),
    "rewrite": ("A live copy rewrite. 1 or 2 pairs: a context (what the copy is, at most 4 words, e.g. 'Landing page "
                "headline'), a weak 'before' (at most 12 words) and a better 'after' (at most 14 words). Then 2 'why' lines "
                "(at most 8 words each). The examples are invented illustrations, never claims of real results.",
                {"pairs": [{"context": "...", "before": "...", "after": "..."}], "why": ["...", "..."]}),
    "kinetic": ("A big-text story: 5 to 8 lines shown one at a time, building a tension-and-release arc. Line 1 is a hook "
                "that challenges the status quo. Each line at most 7 words. Mark 1 key word per line in **double "
                "asterisks**.",
                {"lines": ["...", "..."]}),
    "mockui": ("A made-up app screen with the psychological tricks circled. Choose the screen that fits the topic: "
               "'pricing' (3 plans; give one a tag like 'Most popular'; fields name, price, 2 short features), 'product' "
               "(keys: product, price, was, emoji for the product picture, button, and up to 3 of stock / viewers / timer / "
               "badge, each a short line such as 'Only 3 left in your size'), or 'notifications' (a lock screen; key "
               "notes: 3 items with app, emoji, title, text, each at most 6 words). Invent the brand and app names; never use real brands. 2 or 3 callouts, each "
               "pointing at a target and naming the trick in at most 3 words. Targets: pricing 'plan:0'..'plan:2', "
               "'price:0'..'price:2', 'tag'; product 'price', 'stock', 'viewers', 'timer', 'badge', 'button'; "
               "notifications 'note:0'..'note:2'. Then 2 closing lines, at most 8 words each.",
               {"screen": "pricing", "brand": "...", "heading": "Choose your plan",
                "plans": [{"name": "...", "price": "$9", "features": ["...", "..."], "tag": ""}],
                "callouts": [{"target": "price:2", "text": "..."}], "lines": ["...", "..."]}),
    "flow": ("A workflow diagram: a title (at most 7 words) and 4 or 5 steps, each with one emoji, a label (at most 4 "
             "words) and a sub line (at most 5 words, e.g. the tool or detail), ending with a result line (at most 9 "
             "words). Name tools generically (Zapier, Make, n8n, a CRM, an email tool). For a psychology topic, show the "
             "steps a customer's mind goes through.",
             {"title": "...", "steps": [{"emoji": "📝", "label": "...", "sub": "..."}], "result": "..."}),
    "list": ("A quick numbered list: a title promising the list (at most 7 words, e.g. '3 pricing mistakes killing your "
             "sales') and 3 to 5 items, each with a head (at most 4 words) and a sub line (at most 10 words, mark 1 key "
             "word in **double asterisks**).",
             {"title": "...", "items": [{"head": "...", "sub": "..."}]}),
    "loop": ("One sharp, quotable line (at most 12 words) that makes people watch twice. Mark 1 or 2 key words in "
             "**double asterisks**.",
             {"line": "..."}),
    "voice": ("A spoken mini-lesson read by a voice: a title (at most 7 words) and 5 to 7 short sentences, 60 to 80 "
              "words in total, written to be heard (simple words, no symbols or abbreviations, numbers written the way "
              "they are said).",
              {"title": "...", "sentences": ["...", "..."]}),
    "stock": ("Captions over free stock footage: a search query for the footage (2 to 4 plain words describing a "
              "scene, e.g. 'busy office meeting', 'person shopping online') and 4 to 6 caption lines, each at most 7 "
              "words, mark 1 key word per line in **double asterisks**.",
              {"query": "...", "lines": ["...", "..."]}),
}

ALL = list(SPECS)


def available(cfg: dict) -> list:
    """Formats that can run in this job: voice needs the voice model package,
    stock needs a free Pixabay or Pexels key (PIXABAY_API_KEY or PEXELS_API_KEY secret)."""
    from . import reelfx
    allowed = [f for f in ((cfg.get("format_reels") or {}).get("formats") or ALL) if f in SPECS]
    out = []
    for f in allowed:
        if f == "voice" and not reelfx.tts_available():
            continue
        if f == "stock" and not (os.environ.get("PEXELS_API_KEY") or os.environ.get("PIXABAY_API_KEY")):
            continue
        out.append(f)
    return out or ["kinetic"]


def pick(cfg: dict, state: dict, history: list, exclude=()) -> str:
    """Random, but never the format used last (his ask: 'all of them, in random')."""
    last = [d.get("reel_format") for d in state.get("drafts", []) if d.get("kind") == "reel"]
    last += [h.get("reel_format") for h in history[-3:] if h.get("reel_format")]
    pool = [f for f in available(cfg) if f not in set(exclude)]
    fresh = [f for f in pool if f not in set(last[-2:])]
    return random.choice(fresh or pool or ["kinetic"])


def build_prompt(cfg, series_name, fmt, topic, recent, feedback=None, previous=None) -> str:
    series = cfg["series"][series_name]
    w = cfg["writing"]
    aud = cfg.get("audience") or {}
    rules = "\n".join(f"- {r}" for r in w.get("extra_rules", []))
    spec, shape = SPECS[fmt]
    topic_line = (f"TOPIC: {topic}" if topic else
                  "TOPIC: choose a fresh, specific topic that fits this series and is NOT in the recent list.")
    recent_txt = "\n".join(f"- {t}" for t in recent[-40:]) or "- (none yet)"
    prompt = f"""You write short Instagram Reels for MISFIT, a page that teaches marketers and founders the psychology underneath marketing (and, in the Autopilot series, how marketing work gets automated).

This Reel is a "{NAMES[fmt]}" video. What performs: strong tension, ruthless minimalism, something worth saving, and a reason to watch to the end.

SERIES: {series_name}. {series['brief']}
{topic_line}

TONE: authoritative, direct, punchy, zero fluff, like a text message to a peer. {w['voice']}
SPELLING: {w['spelling']}

FORMAT: {spec}

RULES:
- Accuracy matters more than drama. Only mention studies, researchers, years and numbers you are confident are real. Never invent statistics, quotes or experiments. Never claim MISFIT ran an experiment.
- {aud.get('examples', 'Use examples that make sense to a US audience.')}
- {aud.get('money', 'Write prices in US dollars with the $ sign.')}
- Never use real brand names on mock screens.
{rules}

{writer.series_facts(series)}RECENTLY POSTED OR ALREADY DRAFTED (do not repeat these topics or principles):
{recent_txt}
"""
    if previous and feedback:
        prompt += f"""
REVISION REQUEST. The previous script:
{json.dumps(previous, ensure_ascii=False, indent=1)}
Rewrite it following this feedback from the editor: "{feedback}"
"""
    elif previous:
        prompt += "\nThe editor rejected the previous script on this topic. Write a clearly different take.\n"
    prompt += f"""
Return ONLY a JSON object with exactly these keys:
{{
  "topic": "short topic name",
  "principle": "name of the principle (or the automation idea)",
  "script": {json.dumps(shape, ensure_ascii=False)},
  "save_reason": "a short phrase (at most 8 words) finishing the sentence 'Save this ...'",
  "caption": "Instagram caption: 2 or 3 very short paragraphs, same punchy tone, no call to action at the end (one is added), no hashtags",
  "hashtags": ["#marketingpsychology", "... 6 to 8 relevant hashtags in total"],
  "fact_check": [{{"claim": "each factual claim used", "source": "author, year, publication if known", "confidence": "high|medium|low"}}]
}}"""
    return prompt


def _t(x, n=None):
    s = clean_text(str(x or "")).strip()
    if n:
        s = " ".join(s.split()[: n])
    return s


def validate(fmt: str, data: dict) -> dict:
    """Checks and tidies a script; raises ValueError when it can't be used."""
    sc = data.get("script") if isinstance(data.get("script"), dict) else data
    if fmt == "chat":
        msgs = [m for m in sc.get("messages") or [] if isinstance(m, dict) and _t(m.get("text"))]
        if len(msgs) < 4:
            raise ValueError("chat needs at least 4 messages")
        sc["messages"] = [{"from": "me" if str(m.get("from")).lower() == "me" else "them", "text": _t(m["text"], 18)}
                          for m in msgs[:12]]
        sc["contact"] = _t(sc.get("contact") or "Sam", 2)
        sc["role"] = _t(sc.get("role") or "", 4)
        sc["lesson"] = _t(sc.get("lesson"), 12)
    elif fmt == "quiz":
        opts = [_t(o, 14) for o in sc.get("options") or [] if _t(o)]
        if not 2 <= len(opts) <= 3:
            raise ValueError("quiz needs 2 or 3 options")
        sc["options"] = opts
        sc["question"] = _t(sc.get("question"), 10)
        try:
            sc["answer"] = max(0, min(len(opts) - 1, int(sc.get("answer", 0))))
        except (TypeError, ValueError):
            sc["answer"] = 0
        sc["why"] = [_t(x, 12) for x in sc.get("why") or [] if _t(x)][:3]
        if not sc["question"] or not sc["why"]:
            raise ValueError("quiz needs a question and a why")
    elif fmt == "rewrite":
        pairs = [p for p in sc.get("pairs") or [] if isinstance(p, dict) and _t(p.get("before")) and _t(p.get("after"))]
        if not pairs:
            raise ValueError("rewrite needs a before and an after")
        sc["pairs"] = [{"context": _t(p.get("context"), 5), "before": _t(p["before"], 16), "after": _t(p["after"], 18)}
                       for p in pairs[:2]]
        sc["why"] = [_t(x, 10) for x in sc.get("why") or [] if _t(x)][:2]
    elif fmt in ("kinetic", "stock"):
        lines = [_t(x, 9) for x in sc.get("lines") or [] if _t(x)]
        if len(lines) < 3:
            raise ValueError("needs at least 3 lines")
        sc["lines"] = lines[:8]
        if fmt == "stock":
            sc["query"] = _t(sc.get("query") or "office work", 5)
    elif fmt == "mockui":
        kind = str(sc.get("screen") or "pricing").lower()
        if kind not in ("pricing", "product", "notifications"):
            kind = "pricing"
        sc["screen"] = kind
        if kind == "pricing" and len(sc.get("plans") or []) < 2:
            raise ValueError("pricing needs plans")
        if kind == "notifications" and len(sc.get("notes") or []) < 2:
            raise ValueError("notifications need notes")
        sc["callouts"] = [{"target": str(c.get("target")), "text": _t(c.get("text"), 4)}
                          for c in sc.get("callouts") or [] if isinstance(c, dict)][:3]
        if not sc["callouts"]:
            raise ValueError("mock screen needs callouts")
        sc["lines"] = [_t(x, 10) for x in sc.get("lines") or [] if _t(x)][:2]
    elif fmt == "flow":
        steps = [s for s in sc.get("steps") or [] if isinstance(s, dict) and _t(s.get("label"))]
        if not 3 <= len(steps) <= 6:
            raise ValueError("flow needs 3 to 6 steps")
        sc["steps"] = [{"emoji": str(s.get("emoji") or "⚙")[:2], "label": _t(s["label"], 5), "sub": _t(s.get("sub"), 6)}
                       for s in steps]
        sc["title"] = _t(sc.get("title"), 8)
        sc["result"] = _t(sc.get("result"), 11)
    elif fmt == "list":
        items = [i for i in sc.get("items") or [] if isinstance(i, dict) and _t(i.get("head"))]
        if not 3 <= len(items) <= 5:
            raise ValueError("list needs 3 to 5 items")
        sc["items"] = [{"head": _t(i["head"], 5), "sub": _t(i.get("sub"), 12)} for i in items]
        sc["title"] = _t(sc.get("title"), 8)
    elif fmt == "loop":
        sc["line"] = _t(sc.get("line"), 14)
        if len(sc["line"].split()) < 3:
            raise ValueError("loop needs a line")
    elif fmt == "voice":
        sents = [_t(x, 22) for x in sc.get("sentences") or [] if _t(x)]
        if len(sents) < 3:
            raise ValueError("voice needs sentences")
        sc["sentences"] = sents[:8]
        sc["title"] = _t(sc.get("title"), 8)
    data["script"] = sc
    for k in ("topic", "principle", "save_reason", "caption"):
        data[k] = _t(data.get(k))
    if not data["caption"]:
        raise ValueError("missing caption")
    data["save_reason"] = re.sub(r"^save this\s*", "", data["save_reason"], flags=re.I).rstrip(".") or "for later"
    tags = data.get("hashtags") or []
    data["hashtags"] = [t if t.startswith("#") else "#" + t for t in (str(x).strip().replace(" ", "") for x in tags) if t][:10]
    data.setdefault("fact_check", [])
    if writer.CURRENCY == "USD" and writer.RUPEE.search(json.dumps(data, ensure_ascii=False)):
        raise ValueError("used rupees; asking again for dollars")
    return data


SAMPLES = {
    "chat": {"contact": "Jordan", "role": "Growth lead", "messages": [
        {"from": "them", "text": "We cut our price to $19 and sales dropped??"},
        {"from": "me", "text": "Classic. Cheap made it look worse"},
        {"from": "them", "text": "How is cheaper worse"},
        {"from": "me", "text": "People use price as a **quality** signal"},
        {"from": "me", "text": "Go to $39 and add a $79 plan next to it"}], "lesson": "A low price can say low quality"},
    "kinetic": {"lines": ["Your cheapest plan is **costing you**", "Buyers read price as **quality**",
                          "Raise it. Then **prove it**"]},
}


def write(cfg, series_name, fmt, topic, recent, feedback=None, previous=None, log=print) -> dict:
    """-> validated dict with script. Tries a couple of times, then gives up (the caller picks another format)."""
    writer.CURRENCY = (cfg.get("audience") or {}).get("currency", "USD")
    if DRY_RUN:  # test runs only have sample scripts for two formats
        use = fmt if fmt in SAMPLES else "kinetic"
        sc = SAMPLES[use]
        return {"fmt": use, **validate(use, {
            "topic": topic or "Price as a quality signal", "principle": "Price-quality heuristic", "script": json.loads(json.dumps(sc)),
            "save_reason": "before your next price change", "caption": "Cheap can look worse.\n\nPrice is a signal.",
            "hashtags": ["#marketingpsychology", "#pricing"], "fact_check": []})}
    prompt = build_prompt(cfg, series_name, fmt, topic, recent, feedback, previous)
    errors = []
    for service, key in writer.providers():
        try:
            if service == "groq":
                models, call = writer._groq_models(key, cfg.get("groq_model", "auto"))[:2], writer._groq_call
            else:
                models, call = writer._pick_models(key, cfg.get("gemini_model", "auto"))[:2], writer._call
        except Exception as e:  # noqa: BLE001
            errors.append(f"{service}: {str(e)[:200]}")
            continue
        for model in models:
            for attempt in range(2):
                try:
                    text = call(key, model, prompt)
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                    out = validate(fmt, json.loads(text))
                    out["model"] = f"{service}:{model}"
                    out["fmt"] = fmt
                    return out
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e)[:200])
                    if "403" in str(e) or "401" in str(e):
                        break
                    time.sleep(3)
    raise RuntimeError(f"Couldn't write a {NAMES[fmt]} script: " + " | ".join(errors[-3:]))


def display(fmt: str, sc: dict) -> list:
    """The script as plain lines, for the app's post page."""
    if fmt == "chat":
        return [f"{sc.get('contact') if m['from'] == 'them' else 'Us'}: {m['text']}" for m in sc["messages"]] + \
               [f"Lesson: {sc.get('lesson', '')}"]
    if fmt == "quiz":
        return [sc["question"]] + [f"{'ABC'[i]}. {o}" + ("  (answer)" if i == sc["answer"] else "")
                                   for i, o in enumerate(sc["options"])] + [f"Why: {w}" for w in sc["why"]]
    if fmt == "rewrite":
        out = []
        for p in sc["pairs"]:
            out += [f"{p['context']}", f"Before: {p['before']}", f"After: {p['after']}"]
        return out + [f"Why: {w}" for w in sc.get("why", [])]
    if fmt in ("kinetic", "stock"):
        return ([f"Footage: {sc['query']}"] if fmt == "stock" else []) + sc["lines"]
    if fmt == "mockui":
        return [f"Screen: {sc['screen']}"] + [f"Circle {c['target']}: {c['text']}" for c in sc["callouts"]] + sc.get("lines", [])
    if fmt == "flow":
        return [sc["title"]] + [f"{s['emoji']} {s['label']}: {s['sub']}" for s in sc["steps"]] + [f"Result: {sc.get('result', '')}"]
    if fmt == "list":
        return [sc["title"]] + [f"{i + 1}. {it['head']}: {it['sub']}" for i, it in enumerate(sc["items"])]
    if fmt == "loop":
        return [sc["line"]]
    if fmt == "voice":
        return [sc["title"]] + sc["sentences"]
    return []
