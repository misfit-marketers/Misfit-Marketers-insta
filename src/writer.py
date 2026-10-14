"""Writes a carousel with Google's Gemini API (free tier)."""
import json
import re
import time

import requests

from .common import CONTENT, DRY_RUN, clean_text, env, load_json

API = "https://generativelanguage.googleapis.com/v1beta"


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
                  "TOPIC: choose a fresh, specific marketing psychology principle that is NOT in the recent list.")

    prompt = f"""You write Instagram carousels for MISFIT, a page that teaches marketers and entrepreneurs the psychology underneath marketing.

SERIES: {series_name}. {series['brief']}
{topic_line}

VOICE: {w['voice']}
SPELLING: {w['spelling']}

HOUSE RULES:
- Structure: curiosity hook, then a relatable story, everyday observation or real study, then name the principle, explain WHY it happens in the mind, show how it appears in marketing with a concrete example, end with an actionable takeaway.
- Between 5 and {max_slides} body slides. One idea per slide. Each body slide is at most 45 words.
- Mark 1 to 3 key phrases per slide in **double asterisks**. Never bold a whole slide.
- The hook is a short question or provocative line, at most 9 words, no asterisks.
- The takeaway is one or two practical sentences (do not start it with the word 'Takeaway').
- Accuracy matters more than drama. Only cite studies, researchers, years and numbers you are confident are real. If unsure, describe the finding generally without names or figures. Never invent quotes, statistics or experiments. Never claim MISFIT ran an experiment.
- Use examples that make sense to an Indian and global audience.
{rules}

RECENTLY POSTED (do not repeat these principles):
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


def validate(post: dict, max_slides: int) -> dict:
    for k in ("hook", "slides", "takeaway", "caption"):
        if not post.get(k):
            raise ValueError(f"writer output missing '{k}'")
    if not isinstance(post["slides"], list) or len(post["slides"]) < 3:
        raise ValueError("writer returned too few slides")
    post["slides"] = [clean_text(s) for s in post["slides"][:max_slides] if str(s).strip()]
    post["hook"] = clean_text(post["hook"]).replace("**", "")
    post["takeaway"] = clean_text(re.sub(r"^\**\s*takeaway\s*:?\s*\**\s*", "", post["takeaway"], flags=re.I))
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

    key = env("GEMINI_API_KEY")
    prompt = build_prompt(cfg, series_name, topic, recent_topics, feedback, previous)
    errors = []
    for model in _pick_models(key, cfg.get("gemini_model", "auto"))[:4]:
        for attempt in range(2):
            try:
                text = _call(key, model, prompt)
                text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                post = validate(json.loads(text), max_slides)
                post["model"] = model
                return post
            except Exception as e:  # try again, then the next model
                errors.append(str(e)[:300])
                time.sleep(5 if attempt == 0 else 1)
    raise RuntimeError("All Gemini attempts failed:\n" + "\n".join(errors[-6:]))
