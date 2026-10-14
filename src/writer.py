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

    prompt = f"""You write Instagram carousels for MISFIT, a page that teaches marketers and entrepreneurs the psychology underneath marketing.

SERIES: {series_name}. {series['brief']}
{topic_line}

VOICE: {w['voice']}
SPELLING: {w['spelling']}

HOUSE RULES:
- Structure for this series: {structure} These are beats, not slides: several beats can share one slide, and a beat can be skipped if it adds nothing.
- Length: use as few body slides as the idea needs, anywhere from 2 to {max_slides}. Short carousels (2 to 4 body slides) are welcome when the idea is simple; go longer only when every extra slide adds something new. Never restate a point, pad, or repeat the hook. Respect the reader's time.
- One idea per slide. Each body slide is at most 45 words.
- Mark 1 to 3 key phrases per slide in **double asterisks**. Never bold a whole slide.
- The hook is a short question or provocative line, at most 9 words, no asterisks.
- The "takeaway" field is the final slide: one or two practical sentences. It is shown after the label "{label}:", so do not start it with that word.
- Accuracy matters more than drama. Only cite studies, researchers, years and numbers you are confident are real. If unsure, describe the finding generally without names or figures. Never invent quotes, statistics or experiments. Never claim MISFIT ran an experiment.
- Use examples that make sense to a US audience (US brands, apps, and everyday life), while staying understandable worldwide.
- Write every price and money amount in US dollars with the $ sign (for example $29 or $1,200). Never use rupees, ₹, Rs or INR.
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


RUPEE = re.compile(r"₹|\bRs\.?\s?\d|\bINR\b|\brupees?\b", re.I)


def validate(post: dict, max_slides: int) -> dict:
    for k in ("hook", "slides", "takeaway", "caption"):
        if not post.get(k):
            raise ValueError(f"writer output missing '{k}'")
    visible = " ".join([str(post.get("hook", "")), str(post.get("takeaway", "")), str(post.get("caption", ""))]
                       + [str(x) for x in post.get("slides", [])])
    if RUPEE.search(visible):
        raise ValueError("writer used rupees; asking again for dollars")
    if not isinstance(post["slides"], list) or len(post["slides"]) < 2:
        raise ValueError("writer returned too few slides")
    post["slides"] = [clean_text(s) for s in post["slides"][:max_slides] if str(s).strip()]
    post["hook"] = clean_text(post["hook"]).replace("**", "")
    post["takeaway"] = clean_text(re.sub(r"^\**\s*(takeaway|verdict|remember)\s*:\s*\**\s*", "", post["takeaway"], flags=re.I))
    post["caption"] = clean_text(post["caption"]).replace(", \n", "\n")
    tags = post.get("hashtags") or []
    post["hashtags"] = [t if t.startswith("#") else "#" + t for t in (str(x).strip().replace(" ", "") for x in tags) if t][:10]
    post.setdefault("fact_check", [])
    post.setdefault("topic", post.get("principle", "Untitled"))
    return post


def write_post(cfg: dict, series_name: str, topic: str, recent_topics: list, feedback: str = None,
               previous: dict = None) -> dict:
    max_slides = int(cfg.get("max_body_slides", 7))
    if DRY_RUN:
        return validate(load_json(CONTENT / "sample_post.json", {}), max_slides)

    if not providers():
        env("GEMINI_API_KEY")  # raises the usual "missing secret" message
    prompt = build_prompt(cfg, series_name, topic, recent_topics, feedback, previous)
    errors = []
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
                    post = validate(json.loads(text), max_slides)
                    post["model"] = f"{service}:{model}"
                    return post
                except Exception as e:  # try again, then the next model
                    errors.append(str(e)[:300])
                    if "403" in str(e) or "401" in str(e):
                        break  # access problems won't fix themselves on retry
                    time.sleep(5 if attempt == 0 else 1)
    raise RuntimeError("All writer attempts failed:\n" + "\n".join(errors[-6:]))
