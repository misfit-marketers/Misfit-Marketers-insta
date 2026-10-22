"""Writes a carousel with a free AI API: Groq (key starts with gsk_) or
Google Gemini (key starts with AIza). Put either key in the GEMINI_API_KEY
secret (or GROQ_API_KEY) and the right service is picked automatically."""
import os
import json
import re
import time

import requests

from .common import CONTENT, DRY_RUN, clean_text, env, load_json

API = "https://generativelanguage.googleapis.com/v1beta"
GROQ_API = "https://api.groq.com/openai/v1"
# Best first. Anything not listed is only used if none of these exist.
GROQ_PREFERRED = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "moonshotai/kimi-k2-instruct",
                  "meta-llama/llama-4-maverick-17b-128e-instruct", "openai/gpt-oss-20b"]


def providers() -> list:
    """[(service, key)] for every key that is set, Groq first."""
    keys = [os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GEMINI_API_KEY", "").strip()]
    out = []
    for k in keys:
        if not k:
            continue
        service = "groq" if k.startswith("gsk_") else "gemini"
        if (service, k) not in out:
            out.append((service, k))
    return sorted(out, key=lambda p: p[0] != "groq")


def _groq_models(key: str, preferred: str) -> list:
    if preferred and preferred != "auto":
        return [preferred]
    r = requests.get(f"{GROQ_API}/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    available = [m["id"] for m in r.json().get("data", []) if m.get("active", True)]
    ordered = [m for m in GROQ_PREFERRED if m in available]
    skip = ("whisper", "guard", "tts", "playai", "orpheus", "compound", "prompt-guard", "safeguard")
    ordered += [m for m in available if m not in ordered and not any(x in m.lower() for x in skip)]
    return ordered


def _groq_call(key: str, model: str, prompt: str) -> str:
    body = {"model": model, "temperature": 0.9, "max_tokens": 3500,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"}}
    if model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"
    r = requests.post(f"{GROQ_API}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                      json=body, timeout=120)
    if r.status_code == 400 and "response_format" in r.text:
        body.pop("response_format")
        r = requests.post(f"{GROQ_API}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"groq {model}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"]


def describe_writer(cfg: dict) -> str:
    """Used by the setup check: which service and model will write posts."""
    ps = providers()
    if not ps:
        raise RuntimeError("no GROQ_API_KEY or GEMINI_API_KEY secret is set")
    service, key = ps[0]
    if service == "groq":
        models = _groq_models(key, cfg.get("groq_model", "auto"))
        if not models:
            raise RuntimeError("Groq key works but lists no usable models")
        return f"Groq, {models[0]}"
    return f"Gemini, {_pick_models(key, cfg.get('gemini_model', 'auto'))[0]}"


def _pick_models(key: str, preferred: str) -> list:
    """Return model names to try, best first. 'auto' picks the newest
    plain Flash model the key can use, then Flash-Lite as a fallback."""
    if preferred and preferred != "auto":
        return [preferred]
    r = requests.get(f"{API}/models", params={"key": key, "pageSize": 200}, timeout=30)
    r.raise_for_status()
    flash, lite = [], []
    for m in r.json().get("models", []):
        name = m["name"].replace("models/", "")
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        hit = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?", name)
        if not hit:
            continue
        (lite if hit.group(2) else flash).append((float(hit.group(1)), name))
    ordered = [n for _, n in sorted(flash, reverse=True)] + [n for _, n in sorted(lite, reverse=True)]
    return ordered or ["gemini-flash-latest"]


def _call(key: str, model: str, prompt: str) -> str:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "responseMimeType": "application/json"},
    }
    r = requests.post(f"{API}/models/{model}:generateContent", params={"key": key}, json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"{model}: HTTP {r.status_code} {r.text[:300]}")
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def build_prompt(cfg: dict, series_name: str, topic: str, recent_topics: list, feedback: str = None,
                 previous: dict = None) -> str:
    series = cfg["series"][series_name]
    w = cfg["writing"]
    examples = (CONTENT / "past_posts.md").read_text(encoding="utf-8")
    max_slides = int(cfg.get("max_body_slides", 7))
    rules = "\n".join(f"- {r}" for r in w.get("extra_rules", []))
    recent = "\n".join(f"- {t}" for t in recent_topics[-40:]) or "- (none yet)"
    topic_line = (f"TOPIC: {topic}" if topic else
                  "TOPIC: choose a fresh, specific topic that fits this series and is NOT in the recent list.")
    structure = series.get("structure") or ("Curiosity hook, then a relatable story, everyday observation or real study, "
                                            "then name the principle, explain WHY it happens in the mind, show how it "
                                            "appears in marketing with a concrete example, end with an actionable takeaway.")
    label = series.get("takeaway_label", "Takeaway")
    aud = cfg.get("audience") or {}
    fmt = cfg.get("format") or {}
    if fmt.get("style") == "bullets":
        return build_bullet_prompt(cfg, series_name, topic, recent_topics, feedback, previous)

    prompt = f"""You write Instagram carousels for MISFIT, a page that teaches marketers and entrepreneurs the psychology underneath marketing.

SERIES: {series_name}. {series['brief']}
{topic_line}

VOICE: {w['voice']}
SPELLING: {w['spelling']}

HOUSE RULES:
- Structure for this series: {structure} These are beats, not slides: several beats can share one slide, and a beat can be skipped if it adds nothing.
- Length: use as few body slides as the idea needs, anywhere from 2 to {max_slides}. Short carousels (2 to 4 body slides) are welcome when the idea is simple; go longer only when every extra slide adds something new. Never restate a point, pad, or repeat the hook. Respect the reader's time.
- One idea per slide, written like a poster, not a paragraph. Each body slide is at most 20 words: one short sentence, or two very short ones. Cut every word that isn't doing work. If an idea needs more words, split it across two slides instead of cramming. A designer adds a short heading, an emoji and highlights to every slide later, so do not add headings, emoji or labels yourself (except the labels the series structure asks for, such as 'Before:' or 'The belief:'). Labels don't count toward the 20 words.
- Mark 1 or 2 key phrases per slide in **double asterisks** (short ones, 1 to 3 words). Never bold a whole slide.
- The hook is a short question or provocative line, at most 9 words, no asterisks.
- The "takeaway" field is the final slide: one practical sentence of at most 18 words. It is shown after the label "{label}:", so do not start it with that word.
- Accuracy matters more than drama. Only cite studies, researchers, years and numbers you are confident are real. If unsure, describe the finding generally without names or figures. Never invent quotes, statistics or experiments. Never claim MISFIT ran an experiment.
- {aud.get('examples', 'Use examples that make sense to a US audience (US brands, apps, and everyday life), while staying understandable worldwide.')}
- {aud.get('money', 'Write every price and money amount in US dollars with the $ sign (for example $29 or $1,200). Never use rupees, ₹, Rs or INR.')}
{rules}

RECENTLY POSTED OR ALREADY DRAFTED (do not repeat these topics or principles):
{recent}

STYLE REFERENCE (past posts):
{examples}
"""
    if previous and feedback:
        prompt += f"""
REVISION REQUEST. Here is the previous draft:
{json.dumps(previous, ensure_ascii=False, indent=1)}
Rewrite it following this feedback from the editor: "{feedback}"
"""
    elif previous:
        prompt += "\nThe editor rejected the previous draft on this topic. Write a clearly different take with a new hook and new example.\n"

    prompt += """
Return ONLY a JSON object with exactly these keys:
{
  "topic": "short topic name",
  "principle": "name of the psychological principle",
  "hook": "...",
  "slides": ["body slide 1", "body slide 2", "..."],
  "takeaway": "...",
  "caption": "Instagram caption: 2 to 4 short paragraphs separated by blank lines, ending with a question for the comments. No hashtags here.",
  "hashtags": ["#marketingpsychology", "... 6 to 8 relevant hashtags in total"],
  "fact_check": [{"claim": "each factual claim, study or number used", "source": "author, year, publication if known", "confidence": "high|medium|low"}]
}"""
    return prompt


BULLET_EXAMPLES = """EXAMPLE 1 (MarketingTip):
{"hook": "Stop showing your cheapest price first",
 "slides": ["- The first number sets the scale\\n- Every price after is judged against it\\n- Show **Rs 4,999** first. Rs 1,999 feels light",
            "- Lead with your **premium plan**\\n- Put the one you want sold right after\\n- It looks fair, not cheap",
            "- No premium plan? Show the **full price**\\n- \\"Was Rs 2,499, now Rs 1,799\\" sells better",
            "- Too high and it backfires\\n- Keep the anchor **real**\\n- One fake MRP kills trust"],
 "save_reason": "for your next pricing page"}

EXAMPLE 2 (CopyClinic):
{"hook": "Your button is asking for work",
 "slides": ["- **Before:** \\"Sign up\\"\\n- It asks for effort\\n- It promises nothing",
            "- **After:** \\"Get 1 pricing trick every Sunday\\"\\n- Specific. Small. Worth it",
            "- People don't want actions\\n- They want **outcomes**\\n- Put the outcome on the button",
            "- Read your button out loud\\n- Sounds like a form? **Rewrite it**"],
 "save_reason": "before you write your next CTA"}"""


def build_bullet_prompt(cfg: dict, series_name: str, topic: str, recent_topics: list, feedback: str = None,
                        previous: dict = None) -> str:
    """The save-first format (Sachin, 2026-10-22): a tension hook, ultra-short
    bullet slides that each stand alone, and a final slide built by the code
    with a reason to save and a comment keyword."""
    series = cfg["series"][series_name]
    w = cfg["writing"]
    max_slides = int(cfg.get("max_body_slides", 7))
    rules = "\n".join(f"- {r}" for r in w.get("extra_rules", []))
    recent = "\n".join(f"- {t}" for t in recent_topics[-40:]) or "- (none yet)"
    topic_line = (f"TOPIC: {topic}" if topic else
                  "TOPIC: choose a fresh, specific topic that fits this series and is NOT in the recent list.")
    structure = series.get("structure") or "Hook, the principle, why it works, how to use it, the takeaway."
    label = series.get("takeaway_label", "")
    aud = cfg.get("audience") or {}
    prompt = f"""You write Instagram carousels for MISFIT, a page that teaches marketers and founders the psychology underneath marketing.

What performs: posts people SAVE and linger on. Strong tension hooks, ruthless text minimalism, a stark psychological arc. Every slide must feel like an isolated, high-value nugget someone would save for later.

SERIES: {series_name}. {series['brief']}
{topic_line}

TONE: authoritative, direct, punchy, zero fluff. It reads like a text message to a peer, not an announcement to a crowd. {w['voice']}
SPELLING: {w['spelling']}

FORMAT RULES:
- hook: the first slide. Creates immediate tension or challenges the status quo. At most 7 words. No asterisks, no question unless it stings.
- slides: the body, {3} to {max_slides} slides, following this arc: {structure} The arc's final takeaway{(' (the ' + label + ')') if label else ''} is the LAST body slide{(', starting with ' + chr(39) + label + ':' + chr(39)) if label else ''}.
- Each body slide is ONE clear idea, as 2 or 3 bullet lines. Each line starts with "- " and is at most 8 words. Lines are separated by "\\n". Short, sharp sentences. No filler, no paragraphs, no intro lines like "Here's the thing".
- Each slide must make sense on its own, as if someone screenshotted only that slide.
- Labels the series needs (such as "Before:", "After:", "The belief:") go at the start of a bullet in **double asterisks**, for example "- **Before:** \\"Sign up\\"".
- Mark 1 key phrase per slide in **double asterisks** (1 to 3 words). Never bold a whole line.
- Do NOT write the final call-to-action slide; it is added automatically. Instead give "save_reason": a short phrase (at most 8 words) finishing the sentence "Save this ...", tied to when the reader will need it, for example "for your next pricing page".
- A designer adds headings and emoji later, so do not add them.
- Accuracy matters more than drama. Only cite studies, researchers, years and numbers you are confident are real. If unsure, describe the finding without names or figures. Never invent quotes, statistics or experiments. Before/after copy examples are invented illustrations, so never present them as real results.
- {aud.get('examples', 'Use examples that make sense to a US audience, while staying understandable worldwide.')}
- {aud.get('money', 'Write every price in US dollars with the $ sign.')}
{rules}

RECENTLY POSTED OR ALREADY DRAFTED (do not repeat these topics or principles):
{recent}

FORMAT EXAMPLES (copy the shape and tone, not the topics):
{BULLET_EXAMPLES}
"""
    if previous and feedback:
        prompt += f"""
REVISION REQUEST. Here is the previous draft:
{json.dumps(previous, ensure_ascii=False, indent=1)}
Rewrite it following this feedback from the editor: "{feedback}"
"""
    elif previous:
        prompt += "\nThe editor rejected the previous draft on this topic. Write a clearly different take with a new hook and new example.\n"
    prompt += """
Return ONLY a JSON object with exactly these keys:
{
  "topic": "short topic name",
  "principle": "name of the psychological principle",
  "hook": "...",
  "slides": ["- line\\n- line\\n- line", "..."],
  "save_reason": "...",
  "caption": "Instagram caption: 2 or 3 very short paragraphs separated by blank lines, same punchy tone. Do not add a question or a call to action at the end; one is added automatically. No hashtags here.",
  "hashtags": ["#marketingpsychology", "... 6 to 8 relevant hashtags in total"],
  "fact_check": [{"claim": "each factual claim, study or number used", "source": "author, year, publication if known", "confidence": "high|medium|low"}]
}"""
    return prompt


def bulletise(text: str) -> str:
    """Every line of a body slide as a '- ' bullet, however the AI sent it."""
    if isinstance(text, list):
        text = "\n".join(str(x) for x in text)
    lines = [l.strip() for l in re.split(r"\n|\\n", str(text)) if l.strip()]
    if len(lines) == 1 and not re.match(r"^[-•*]\s", lines[0]):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", lines[0]) if p.strip()]
        lines = parts if 2 <= len(parts) <= 3 else lines
    lines = [re.sub(r"^(?:[-•*]|\d+[.)])\s*", "", l) for l in lines]
    lines = [re.sub(r"(?<![.])\.$", "", l) for l in lines]  # bullets end without a full stop, like the examples
    return "\n".join("- " + l for l in lines)


def apply_format(post: dict, cfg: dict) -> dict:
    """Bullet format: tidy the slides and build the final save + comment slide
    and the caption's last line, so the keyword is always exactly right."""
    fmt = cfg.get("format") or {}
    if fmt.get("style") != "bullets":
        return post
    cta = fmt.get("cta") or {}
    key, offer = (cta.get("keyword") or "PSYCHOLOGY").strip().upper(), cta.get("offer") or "my marketing playbook"
    post["slides"] = [bulletise(x) for x in post["slides"]]
    reason = clean_text(str(post.get("save_reason") or "for later")).rstrip(".")
    reason = re.sub(r"^\**\s*save this\s*", "", reason, flags=re.I).strip() or "for later"
    post["save_reason"] = reason
    post["takeaway"] = f"- **Save this** {reason}\n- Comment **{key}** and I'll DM you {offer}"
    line = f"Save this {reason}. Comment {key} and I'll DM you {offer}."
    if key.lower() not in post["caption"].lower():
        post["caption"] = post["caption"].rstrip() + "\n\n" + line
    post["format"] = "bullets"
    return post


CURRENCY = "USD"  # set from config.json "audience" when the writer runs
RUPEE = re.compile(r"₹|\bRs\.?\s?\d|\bINR\b|\brupees?\b", re.I)


def validate(post: dict, max_slides: int) -> dict:
    if post.get("save_reason") and not post.get("takeaway"):
        post["takeaway"] = str(post["save_reason"])  # bullet format: the final slide is built by apply_format
    for k in ("hook", "slides", "takeaway", "caption"):
        if not post.get(k):
            raise ValueError(f"writer output missing '{k}'")
    visible = " ".join([str(post.get("hook", "")), str(post.get("takeaway", "")), str(post.get("caption", ""))]
                       + [str(x) for x in post.get("slides", [])])
    if CURRENCY == "USD" and RUPEE.search(visible):
        raise ValueError("writer used rupees; asking again for dollars")
    if not isinstance(post["slides"], list) or len(post["slides"]) < 2:
        raise ValueError("writer returned too few slides")
    post["slides"] = [clean_text(s if isinstance(s, str) else "\n".join(map(str, s)))
                      for s in post["slides"][:max_slides] if str(s).strip()]
    post["hook"] = clean_text(post["hook"]).replace("**", "")
    post["takeaway"] = clean_text(re.sub(r"^\**\s*(takeaway|verdict|remember)\s*:\s*\**\s*", "", post["takeaway"], flags=re.I))
    post["caption"] = clean_text(post["caption"]).replace(", \n", "\n")
    tags = post.get("hashtags") or []
    post["hashtags"] = [t if t.startswith("#") else "#" + t for t in (str(x).strip().replace(" ", "") for x in tags) if t][:10]
    post.setdefault("fact_check", [])
    post.setdefault("topic", post.get("principle", "Untitled"))
    return post


SLIDE_WORDS, TAKEAWAY_WORDS = 20, 18


def overrun(post: dict) -> int:
    """How many words past the limits the writer went (0 = within limits).
    Labels such as 'The belief:' don't count. A little slack is allowed."""
    from .design import split_label
    n = lambda t: len(re.sub(r"\*\*|(^|\n)\s*-\s", " ", t).split())
    if post.get("format") == "bullets":  # at most 3 lines of about 8 words, with a little slack
        over = sum(max(0, n(x) - 24) for x in post["slides"])
        over += sum(max(0, len(post["slides"][i].split("\n")) - 3) * 4 for i in range(len(post["slides"])))
        return over
    over = sum(max(0, n(split_label(x)[1]) - (SLIDE_WORDS + 6)) for x in post["slides"])
    return over + max(0, n(post["takeaway"]) - (TAKEAWAY_WORDS + 6))


def write_post(cfg: dict, series_name: str, topic: str, recent_topics: list, feedback: str = None,
               previous: dict = None) -> dict:
    global CURRENCY
    CURRENCY = (cfg.get("audience") or {}).get("currency", "USD")
    max_slides = int(cfg.get("max_body_slides", 7))
    if DRY_RUN:
        return apply_format(validate(load_json(CONTENT / "sample_post.json", {}), max_slides), cfg)

    if not providers():
        env("GEMINI_API_KEY")  # raises the usual "missing secret" message
    prompt = build_prompt(cfg, series_name, topic, recent_topics, feedback, previous)
    errors, wordy, wordy_tries = [], None, 0  # wordy: the shortest too-long draft, used only if nothing shorter comes back
    for service, key in providers():
        try:
            if service == "groq":
                models, call = _groq_models(key, cfg.get("groq_model", "auto"))[:3], _groq_call
            else:
                models, call = _pick_models(key, cfg.get("gemini_model", "auto"))[:3], _call
        except Exception as e:
            errors.append(f"{service}: could not list models: {str(e)[:250]}")
            continue
        for model in models:
            for attempt in range(2):
                try:
                    text = call(key, model, prompt)
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                    post = apply_format(validate(json.loads(text), max_slides), cfg)
                    post["model"] = f"{service}:{model}"
                    extra = overrun(post)
                    if not extra:
                        return post
                    if wordy is None or extra < wordy[0]:
                        wordy = (extra, post)
                    wordy_tries += 1
                    if wordy_tries >= 3:
                        return wordy[1]  # three tries is enough; use the tightest one
                    raise ValueError(f"slides too wordy ({extra} words over the limit); asking again")
                except Exception as e:  # try again, then the next model
                    errors.append(str(e)[:300])
                    if "403" in str(e) or "401" in str(e):
                        break  # access problems won't fix themselves on retry
                    time.sleep(5 if attempt == 0 else 1)
    if wordy:
        return wordy[1]
    raise RuntimeError("All writer attempts failed:\n" + "\n".join(errors[-6:]))
