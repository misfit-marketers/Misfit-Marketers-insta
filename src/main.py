"""Entry point. Run with:  python -m src.main <command>

Commands
  generate        write drafts for every empty posting slot (today and tomorrow, see days_ahead) (morning job)
  check           read Telegram taps/messages and act on them (every 10 min)
  post-due        publish approved drafts at their posting slot (slot jobs)
  post-now        read Telegram, then post approved drafts immediately (manual job)
  refresh-token   renew the 60-day Instagram token (monthly job)
  setup-check     test every key and send a hello message to Telegram
  preview FILE    render a post JSON locally into posts/preview/ (no APIs)

How posting works
  The config lists daily slots (by default 6 pm IST), each with a
  back-up time one hour later. Every draft belongs to one upcoming slot. Approve
  it and it goes live at that slot; if it's missed or Instagram refuses it, it
  moves to the back-up time, then to the same slot the next day, until it's live.
"""
import html
import os
import re
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import instagram, telegram, writer
from .common import (CONTENT, DRY_RUN, POSTS, ROOT, add_topic_to_backlog, clean_text, load_config, load_history,
                     load_state, now_iso, remove_topic_from_backlog, save_history, save_state, topic_backlog)
from .reel import REEL_VERSION
from .render import render_post

IST = timezone(timedelta(hours=5, minutes=30))
GRACE = timedelta(minutes=15)

HELP = (
    "<b>MISFIT autopost bot</b>\n\n"
    "Each morning you get one draft per posting slot. Buttons under each draft:\n"
    "<b>Approve</b> schedules it for its slot. <b>Redo</b> writes a new version. <b>Skip</b> sets this version "
    "aside (the topic stays first in line). After approving you also get <b>Post now</b>.\n"
    "To change a draft, <b>reply to its message</b> with your feedback.\n"
    "A topic only leaves the list once it is live on Instagram.\n\n"
    "/status  what's waiting and when it goes live\n"
    "/new  fill an empty slot now with the next topic\n"
    "/new <i>idea</i>  fill an empty slot now with your idea\n"
    "/topic <i>idea</i>  add an idea to the end of the topic list (start with [BrandBreakdown] etc. to pick a series)\n"
    "/next <i>idea</i>  put an idea at the front of the topic list\n"
    "/topics  show the next topics\n"
    "/drop  (as a reply to a draft) remove that draft's topic for good\n"
    "/rerender  redraw the slides (same text); reply to one draft, or send on its own to redraw all\n\n"
    "I act on your taps and replies at the next check, which runs every 10 minutes (GitHub sometimes runs a few minutes late). "
    "To post an approved draft right away, run <b>5. Go ahead and post</b> on GitHub."
)


# ------------------------------------------------------------------ small helpers

def log(msg: str):
    print(msg, flush=True)


def parse_topic(line: str):
    """'[BrandBreakdown] idea' -> ('BrandBreakdown', 'idea')"""
    m = re.match(r"^\[(\w+)\]\s*(.+)$", line.strip())
    return (m.group(1), m.group(2)) if m else (None, line.strip())


def default_series(cfg: dict) -> str:
    return list(cfg["series"])[0]


def line_series(cfg: dict, line: str) -> str:
    s, _ = parse_topic(line)
    return s if s in cfg["series"] else default_series(cfg)


def build_caption(post: dict) -> str:
    caption = post["caption"].strip()
    tags = " ".join(post.get("hashtags", [])[:30])
    return f"{caption}\n\n{tags}".strip()[:2200]


def image_urls(draft: dict, cfg: dict) -> list:
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    rels = [Path(p).as_posix() for p in draft["images"]]
    if cfg.get("image_host") == "jsdelivr":
        return [f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{r}" for r in rels]
    return [f"https://raw.githubusercontent.com/{repo}/{branch}/{r}" for r in rels]


def reels_cfg(cfg: dict) -> dict:
    r = cfg.get("reels") or {}
    return {"make": bool(r.get("make")) or r.get("post", "off") != "off", "post": r.get("post", "off"),
            "share_to_feed": r.get("share_to_feed", True) is not False}


def build_reel(post: dict, series: str, tag: str, out_dir: Path, cfg: dict, style: str = None):
    """A Reel version of the slides, if Reels are switched on. Never blocks a draft."""
    if not reels_cfg(cfg)["make"]:
        return None
    try:
        from . import reel
        from .design import split_label
        from .render import style_for
        imgs = sorted(out_dir.glob("[0-9][0-9].jpg"))
        n = lambda t: len(re.sub(r"\*\*|(^|\n)\s*-\s", " ", t or "").split())
        words = [n(post["hook"])] + [n(split_label(x)[1]) for x in post["slides"]] + [n(post["takeaway"])]
        words = (words + [12] * len(imgs))[:len(imgs)]
        pick = style_for(cfg, series, style)
        accent = tuple(int(pick[1].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)) if pick else (255, 77, 46)
        path = reel.make_reel(imgs, out_dir / "reel.mp4", words=words, accent=accent,
                              handle=cfg.get("instagram_handle", "misfit_marketers").lstrip("@"), seed=tag, log=log)
        return str(path.relative_to(ROOT))
    except Exception as e:
        log(f"Couldn't make the Reel: {e}")
        return None


def video_url(path: str) -> list:
    """Public links Instagram can fetch the video from, used only if the direct
    upload fails: jsDelivr (right content type), then raw GitHub."""
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    ref = os.environ.get("GITHUB_SHA") or os.environ.get("GITHUB_REF_NAME", "main")
    p = Path(path).as_posix()
    return [f"https://cdn.jsdelivr.net/gh/{repo}@{ref}/{p}", f"https://raw.githubusercontent.com/{repo}/{ref}/{p}"]


def offered_parts(cfg: dict, d: dict) -> list:
    """What can go live for this draft: the carousel, the Reel, or both
    (config reels.post: off = carousel, both = carousel and Reel, reel = Reel)."""
    mode = reels_cfg(cfg)["post"]
    has_reel = bool(d.get("reel"))
    if d.get("kind") == "reel":
        return ["reel"]  # a format Reel has no carousel
    if mode == "reel" and has_reel:
        return ["reel"]
    if mode == "both" and has_reel:
        return ["carousel", "reel"]
    return ["carousel"]


def approved_parts(d: dict) -> list:
    """Each part is approved on its own (his rule, 2026-10-20). Older drafts
    without the field count as fully approved when their status says so."""
    a = d.get("approved_parts")
    if a is None:
        return ["carousel", "reel"] if d.get("status") == "approved" else []
    return list(a)


def part_names(parts: list) -> str:
    names = {"carousel": "the carousel", "reel": "the Reel"}
    p = [names[x] for x in ("carousel", "reel") if x in parts]
    return " and ".join(p) if p else "nothing"


def remove_draft_files(draft: dict):
    d = POSTS / draft["id"]
    if d.exists():
        shutil.rmtree(d)


def describe(draft: dict) -> str:
    return f"{draft['tag']} ({html.escape(draft['post'].get('principle') or draft['topic'] or '')})"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def when_text(iso: str) -> str:
    dt = datetime.fromisoformat(iso).astimezone(IST)
    today = now_utc().astimezone(IST).date()
    if dt.date() == today:
        day = "today"
    elif dt.date() == today + timedelta(days=1):
        day = "tomorrow"
    else:
        day = dt.strftime("%a %d %b")
    t = dt.strftime("%I:%M %p").lstrip("0").lower()
    if dt.hour < 6 and day in ("today", "tomorrow"):
        # 3:30 am "tomorrow" reads better as "tonight".
        day = "tonight" if dt.date() == today + timedelta(days=1) else day
    return f"{t} IST {day}"


def closing_text(cfg: dict):
    if cfg.get("closing_slide", {}).get("enabled"):
        return cfg["closing_slide"]["text"].format(handle=cfg.get("instagram_handle", ""))
    return None


def series_label(cfg: dict, series: str, post: dict = None) -> str:
    """The small label on the final slide. Bullet-format posts end with the
    save + comment slide, labelled "Save this"."""
    if post and post.get("format") == "bullets":
        return ((cfg.get("format") or {}).get("cta") or {}).get("label", "Save this")
    return cfg["series"].get(series, {}).get("takeaway_label", "Takeaway")


# ------------------------------------------------------------------ slots

def _hm(text: str):
    hh, mm = (int(x) for x in str(text).split(":"))
    return hh, mm


def slot_defs(cfg: dict) -> list:
    """[{'name', 'time': (h, m), 'backup': (h, m)}]. Falls back to the older
    post_times_ist setting (first time = slot, second = back-up)."""
    if cfg.get("slots"):
        out = []
        for i, s in enumerate(cfg["slots"]):
            out.append({"name": s["name"], "time": _hm(s["time_ist"]),
                        "backup": _hm(s.get("backup_ist") or s["time_ist"]),
                        "kind": s.get("kind") or "carousel"})
        return out
    times = cfg.get("post_times_ist") or ["18:00", "20:00"]
    return [{"name": "daily", "time": _hm(times[0]), "backup": _hm(times[-1]), "kind": "carousel"}]


def slot_kind(cfg: dict, slot_name: str) -> str:
    """'carousel' (a carousel and its Reel) or 'reel' (a Reel in one of the special formats)."""
    return next((s["kind"] for s in slot_defs(cfg) if s["name"] == slot_name), "carousel")


def occurrence(sdef: dict, day) -> tuple:
    """(primary, backup) datetimes in IST for a slot on a calendar day."""
    hh, mm = sdef["time"]
    primary = datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)
    bh, bm = sdef["backup"]
    backup = primary.replace(hour=bh, minute=bm)
    if backup < primary:
        backup += timedelta(days=1)
    return primary, backup


def slot_backup(cfg: dict, draft: dict) -> datetime:
    primary = datetime.fromisoformat(draft["slot_at"]).astimezone(IST)
    sdef = next((s for s in slot_defs(cfg) if s["name"] == draft.get("slot")), slot_defs(cfg)[0])
    return occurrence(sdef, primary.date())[1]


def days_ahead(cfg: dict) -> int:
    """How many upcoming days each slot keeps a draft ready for ("days_ahead" in
    config.json). 2 means today's and tomorrow's posts are always written, so a
    day you don't open the app still has a draft you approved the day before."""
    try:
        return max(1, min(7, int(cfg.get("days_ahead", 1))))
    except (TypeError, ValueError):
        return 1


def upcoming_slots(cfg: dict, now: datetime) -> list:
    """The next few occurrences of each slot (days_ahead per slot), soonest first."""
    now = now.astimezone(IST)
    ahead = days_ahead(cfg)
    out = []
    for sdef in slot_defs(cfg):
        found = 0
        for d in range(ahead + 3):
            primary, _ = occurrence(sdef, now.date() + timedelta(days=d))
            if primary > now + timedelta(minutes=20):
                out.append((sdef["name"], primary))
                found += 1
                if found >= ahead:
                    break
    return sorted(out, key=lambda x: x[1])


def taken(drafts: list) -> set:
    return {d.get("slot_at") for d in drafts}


def free_slots(cfg: dict, drafts: list, now: datetime) -> list:
    t = taken(drafts)
    return [(name, p) for name, p in upcoming_slots(cfg, now) if p.astimezone(timezone.utc).isoformat() not in t]


def next_free_same_slot(cfg: dict, drafts: list, draft: dict, after: datetime) -> datetime:
    """The next occurrence of this draft's slot after `after` that no other draft holds."""
    sdef = next((s for s in slot_defs(cfg) if s["name"] == draft.get("slot")), slot_defs(cfg)[0])
    others = taken([d for d in drafts if d["id"] != draft["id"]])
    day = after.astimezone(IST).date() - timedelta(days=1)
    for i in range(60):
        primary, _ = occurrence(sdef, day + timedelta(days=i))
        if primary > after and primary.astimezone(timezone.utc).isoformat() not in others:
            return primary
    raise RuntimeError("no free slot found")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ------------------------------------------------------------------ drafts in state

def drafts_of(state: dict) -> list:
    return sorted(state["drafts"], key=lambda d: d.get("slot_at") or "")


def find(state: dict, draft_id: str):
    return next((d for d in state["drafts"] if d["id"] == draft_id), None)


def find_by_message(state: dict, message_id) -> dict:
    return next((d for d in state["drafts"] if message_id in (d.get("msg_ids") or [])), None)


def remove(state: dict, draft_id: str):
    state["drafts"] = [d for d in state["drafts"] if d["id"] != draft_id]


def _hist(draft: dict) -> dict:
    return {k: draft.get(k) for k in ("id", "series", "number", "tag", "topic", "slot")} | {
        "principle": draft.get("post", {}).get("principle"), "at": now_iso()}


# ------------------------------------------------------------------ topics

def merge_topic_seeds(cfg: dict):
    """New topic lists ship as content/topic_seeds/*.txt. Each file is merged
    into topics.txt once (files named front_*.txt go to the front)."""
    seed_dir = CONTENT / "topic_seeds"
    if not seed_dir.exists():
        return
    state = load_state()
    done = set(state.get("merged_seeds", []))
    posted = {(h.get("topic") or "").strip().lower() for h in load_history() if h.get("status") == "posted"}
    changed = False
    for f in sorted(seed_dir.glob("*.txt")):
        if f.name in done:
            continue
        lines = [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        existing = set(topic_backlog())
        fresh = [ln for ln in lines if ln not in existing and parse_topic(ln)[1].lower() not in posted]
        if f.name.startswith("front_"):
            for ln in reversed(fresh):
                add_topic_to_backlog(ln, front=True)
        else:
            for ln in fresh:
                add_topic_to_backlog(ln)
        done.add(f.name)
        changed = True
        log(f"Merged {len(fresh)} topics from {f.name}")
    if changed:
        state = load_state()
        state["merged_seeds"] = sorted(done)
        save_state(state)


def next_topic_line(cfg: dict, series: str, exclude: set):
    for line in topic_backlog():
        if line in exclude:
            continue
        if line_series(cfg, line) == series:
            return line
    return None


def keep_topic(cfg: dict, draft: dict):
    """Make sure a set-aside draft's topic stays first in line."""
    line = draft.get("backlog_line")
    if line and line in topic_backlog():
        return
    topic = draft.get("topic") or draft.get("post", {}).get("topic")
    if not topic:
        return
    line = topic if draft.get("series") == default_series(cfg) else f"[{draft['series']}] {topic}"
    if line not in topic_backlog():
        add_topic_to_backlog(line, front=True)


# ------------------------------------------------------------------ choosing series, numbers, themes

def choose_series(cfg: dict, history: list, drafts: list) -> str:
    """Keeps the mix close to the configured weights, and avoids giving two
    open drafts the same series when it can."""
    names = list(cfg["series"])
    counts = {n: 0 for n in names}
    for h in history:
        if h.get("status") == "posted" and h.get("series") in counts:
            counts[h["series"]] += 1
    for d in drafts:
        if d.get("series") in counts:
            counts[d["series"]] += 1
    weights = {n: max(float(cfg["series"][n].get("weight", 1)), 0.0) for n in names}
    total_w = sum(weights.values()) or 1.0
    upcoming = sum(counts.values()) + 1
    ranked = sorted(names, key=lambda n: (-(weights[n] * upcoming / total_w - counts[n]), names.index(n)))
    open_series = {d.get("series") for d in drafts}
    for n in ranked:
        if n not in open_series and weights[n] > 0:
            return n
    return ranked[0]


def series_number(cfg: dict, state: dict, series: str) -> int:
    n = int(state["series_counters"].get(series, cfg["series"][series].get("next_number", 1)))
    used = [int(d["number"]) for d in state["drafts"] if d.get("series") == series]
    return max([n] + [u + 1 for u in used])


def next_theme(cfg: dict, state: dict) -> str:
    mode = cfg.get("theme", "alternate")
    if mode in ("light", "dark"):
        return mode
    theme = "light" if state.get("last_theme") == "dark" else "dark"
    state["last_theme"] = theme
    return theme


# ------------------------------------------------------------------ writing drafts

def write_draft(cfg: dict, slot_name: str, slot_at: datetime, series: str = None, topic: str = None,
                redo_of: dict = None, feedback: str = None) -> dict:
    """Writes, renders and sends one draft for a slot; saves it to state."""
    kind = (redo_of or {}).get("kind") or (slot_kind(cfg, slot_name) if not redo_of else "carousel")
    if kind == "reel":
        return write_format_draft(cfg, slot_name, slot_at, series=series, topic=topic, redo_of=redo_of,
                                  feedback=feedback)
    state, history = load_state(), load_history()
    backlog_line = None
    if redo_of:
        series, topic, backlog_line = redo_of["series"], redo_of["topic"], redo_of.get("backlog_line")
        theme, number = redo_of["theme"], redo_of["number"]
        style = redo_of.get("style")
    else:
        if topic:
            hint, topic = parse_topic(topic)
            series = hint if hint in cfg["series"] else (series or default_series(cfg))
        else:
            series = series or choose_series(cfg, history, state["drafts"])
            exclude = {d.get("backlog_line") for d in state["drafts"]}
            backlog_line = next_topic_line(cfg, series, exclude)
            if backlog_line:
                topic = parse_topic(backlog_line)[1]
        theme = next_theme(cfg, state)
        number = series_number(cfg, state, series)
        style = None

    recent = [h.get("principle") or h.get("topic") for h in history if h.get("status") == "posted"]
    recent += [d["post"].get("principle") or d.get("topic") for d in state["drafts"]
               if not redo_of or d["id"] != redo_of["id"]]
    log(f"Writing {series} #{number} for {slot_name} slot on: {topic or '(writer chooses)'}")
    post = writer.write_post(cfg, series, topic, [r for r in recent if r], feedback=feedback,
                             previous=redo_of["post"] if redo_of else None)

    draft_id = now_utc().strftime("%Y%m%d-%H%M%S")
    while (POSTS / draft_id).exists() or find(state, draft_id):
        draft_id += "b"
    tag = cfg["series"][series]["tag"].format(n=number)
    paths = render_post(post, theme, tag, POSTS / draft_id, closing=closing_text(cfg),
                        label=series_label(cfg, series, post), series=series, cfg=cfg, style=style)
    reel_path = build_reel(post, series, tag, POSTS / draft_id, cfg, style)

    state = load_state()  # reload: the writer call can take a while
    if redo_of:
        history = load_history()
        history.append({**_hist(redo_of), "status": "redone", "feedback": feedback or ""})
        save_history(history)
        remove_draft_files(redo_of)
        remove(state, redo_of["id"])
    if not redo_of and cfg.get("theme", "alternate") == "alternate":
        state["last_theme"] = theme

    draft = {
        "id": draft_id, "series": series, "number": number, "tag": tag, "theme": theme, "style": style,
        "topic": topic or post.get("topic"), "post": post, "caption": build_caption(post),
        "images": [str(p.relative_to(ROOT)) for p in paths], "created_at": now_iso(), "reel": reel_path,
        "reel_v": REEL_VERSION,
        "status": "awaiting", "approved_parts": [], "backlog_line": backlog_line,
        "slot": slot_name, "slot_at": iso(slot_at), "publish_at": None, "reminded": False,
    }
    state["drafts"].append(draft)
    save_state(state)
    ids = telegram.send_review({**draft, "images": [str(ROOT / p) for p in draft["images"]]},
                               when=when_text(draft["slot_at"]))
    state = load_state()
    d = find(state, draft_id)
    if d is not None:
        d["msg_ids"] = ids
        save_state(state)
    log(f"Draft {draft_id} sent for review ({len(paths)} slides)")
    maybe_auto_approve(draft_id, cfg)
    return draft


def reel_number(state: dict) -> int:
    n = int(state.get("reel_counter", 1))
    used = [int(d["number"]) for d in state["drafts"] if d.get("kind") == "reel"]
    return max([n] + [u + 1 for u in used])


def write_format_draft(cfg: dict, slot_name: str, slot_at: datetime, series: str = None, topic: str = None,
                       redo_of: dict = None, feedback: str = None, fmt: str = None, new_format: bool = False) -> dict:
    """Slot 2: a Reel in one of the special formats (chat, quiz, rewrite, big text,
    mock screen, workflow, list, loop, voice-over, stock footage), picked at random.
    The AI writes the script, reelfx.py draws and encodes it."""
    from . import formats, reelfx
    from .render import style_for
    state, history = load_state(), load_history()
    backlog_line = None
    if redo_of:
        series, topic, backlog_line = redo_of["series"], redo_of["topic"], redo_of.get("backlog_line")
        number = redo_of["number"]
        fmt = fmt or (None if new_format else redo_of.get("reel_format"))
    else:
        if topic:
            hint, topic = parse_topic(topic)
            series = hint if hint in cfg["series"] else (series or default_series(cfg))
        else:
            series = series or choose_series(cfg, history, state["drafts"])
            exclude = {d.get("backlog_line") for d in state["drafts"]}
            backlog_line = next_topic_line(cfg, series, exclude)
            if backlog_line:
                topic = parse_topic(backlog_line)[1]
        number = reel_number(state)
    recent = [h.get("principle") or h.get("topic") for h in history if h.get("status") == "posted"]
    recent += [d["post"].get("principle") or d.get("topic") for d in state["drafts"]
               if not redo_of or d["id"] != redo_of["id"]]
    recent = [r for r in recent if r]
    tried = [redo_of.get("reel_format")] if (redo_of and new_format) else []
    data, err = None, ""
    for _ in range(3):  # a format that fails (script or drawing) gives way to another one
        fmt = fmt or formats.pick(cfg, state, history, exclude=tried)
        log(f"Writing a {formats.NAMES[fmt]} Reel ({series}) for {slot_name} slot on: {topic or '(writer chooses)'}")
        try:
            data = formats.write(cfg, series, fmt, topic, recent, feedback=feedback,
                                 previous=(redo_of or {}).get("script") if redo_of else None, log=log)
            fmt = data.get("fmt", fmt)
            draft_id = now_utc().strftime("%Y%m%d-%H%M%S")
            while (POSTS / draft_id).exists() or find(load_state(), draft_id):
                draft_id += "b"
            pick = style_for(cfg, series, None)
            accent = pick[1] if pick else "#FF4D2E"
            fcfg = cfg.get("format") or {}
            cta = {**(fcfg.get("cta") or {}), "reason": data["save_reason"], "pexels_key": os.environ.get("PEXELS_API_KEY", "")}
            tag = f"REEL#{number}"
            reel_path, cover, extra = reelfx.build(fmt, data["script"], POSTS / draft_id, accent,
                                                   cfg.get("instagram_handle", "misfit_marketers"), cta, seed=tag + fmt,
                                                   log=log)
            break
        except Exception as e:  # noqa: BLE001
            err = str(e)[:300]
            log(f"The {fmt} Reel didn't work ({err}); trying another format")
            tried.append(fmt)
            if redo_of:
                remove_draft_files({"id": locals().get("draft_id", "_none")})
            fmt, data = None, None
    if data is None:
        raise RuntimeError(f"Couldn't make a format Reel: {err}")
    key = ((cfg.get("format") or {}).get("cta") or {}).get("keyword", "PSYCHOLOGY").upper()
    offer = ((cfg.get("format") or {}).get("cta") or {}).get("offer", "my marketing playbook")
    caption = data["caption"].rstrip() + f"\n\nSave this {data['save_reason']}. Comment {key} and I'll DM you {offer}."
    if extra.get("credit"):
        caption += f"\n\nVideo: {extra['credit']} on {extra.get('site') or 'Pexels'}"
    post = {"topic": data.get("topic"), "principle": data.get("principle"), "hook": formats.display(fmt, data["script"])[0],
            "slides": formats.display(fmt, data["script"]), "takeaway": "", "caption": caption,
            "hashtags": data.get("hashtags", []), "fact_check": data.get("fact_check", []), "kind": "reel",
            "format": "reel:" + fmt, "model": data.get("model", "")}
    state = load_state()
    if redo_of:
        history = load_history()
        history.append({**_hist(redo_of), "status": "redone", "feedback": feedback or ""})
        save_history(history)
        remove_draft_files(redo_of)
        remove(state, redo_of["id"])
    draft = {
        "id": draft_id, "kind": "reel", "reel_format": fmt, "series": series, "number": number, "tag": tag,
        "theme": "dark", "style": None, "topic": topic or data.get("topic"), "post": post, "script": data["script"],
        "caption": build_caption(post), "images": [str(cover.relative_to(ROOT))], "created_at": now_iso(),
        "reel": str(reel_path.relative_to(ROOT)), "reel_v": REEL_VERSION, "status": "awaiting", "approved_parts": [],
        "backlog_line": backlog_line, "slot": slot_name, "slot_at": iso(slot_at), "publish_at": None, "reminded": False,
        **({"credit": extra} if extra else {}),
    }
    state["drafts"].append(draft)
    save_state(state)
    telegram.send_message(f"New {formats.NAMES[fmt]} Reel ready: {describe(draft)} for {when_text(draft['slot_at'])}. "
                          "Watch it in the app and approve it.")
    log(f"Draft {draft_id}: {formats.NAMES[fmt]} Reel ready")
    maybe_auto_approve(draft_id, cfg)
    return draft


def generate(scheduled: bool = False, topic: str = None) -> None:
    """Morning job: one draft per empty upcoming slot. Drafts still waiting
    from earlier keep their slot (and get a reminder) instead of being replaced."""
    cfg = load_config()
    merge_topic_seeds(cfg)
    process_due(notify=True)
    state = load_state()
    now = now_utc()
    free = free_slots(cfg, state["drafts"], now)

    waiting = [d for d in drafts_of(state) if d["status"] == "awaiting"]
    if waiting and scheduled:
        lines = [f"• {describe(d)} for {when_text(d['slot_at'])}" for d in waiting]
        telegram.send_message("Still waiting for your review:\n" + "\n".join(lines))
    if not free:
        msg = "Every upcoming slot already has a draft, so I didn't write new ones."
        if not scheduled:
            msg += " Skip a draft to free its slot, then try again."
        telegram.send_message(msg)
        return
    for i, (name, slot_at) in enumerate(free):
        write_draft(cfg, name, slot_at, topic=topic if i == 0 else None)


# ------------------------------------------------------------------ approving and publishing

def approve(draft_id: str, parts: list = None):
    """Approves the carousel, the Reel, or both (default: everything on offer).
    Approvals add up: approving the Reel later keeps the carousel approved."""
    cfg, state = load_config(), load_state()
    d = find(state, draft_id)
    if not d:
        return
    offer = offered_parts(cfg, d)
    want = [p for p in (parts or offer) if p in offer] or offer
    d["approved_parts"] = [p for p in offer if p in set(approved_parts(d)) | set(want)]
    now = now_utc()
    primary = datetime.fromisoformat(d["slot_at"])
    backup = slot_backup(cfg, d)
    if now < primary + GRACE:
        at = primary
    elif now < backup + GRACE:
        at = backup
    else:
        new_slot = next_free_same_slot(cfg, state["drafts"], d, now)
        d["slot_at"], at = iso(new_slot), new_slot
    d["status"] = "approved"
    d["approved_at"] = now_iso()
    d.pop("unapproved_by_change", None)
    d["publish_at"] = iso(at)
    d["note"] = ("" if at == primary else
                 f"Approved after its {when_text(iso(primary))} slot, so it goes live at {when_text(d['publish_at'])}.")
    save_state(state)
    telegram.send_message(f"Approved {part_names(d['approved_parts'])} of {describe(d)}. Goes live at "
                          f"{when_text(d['publish_at'])}.\n"
                          "Tap Post now if you want it up straight away.",
                          buttons=telegram.scheduled_buttons(d["id"]))


def auto_approve_on(cfg: dict = None) -> bool:
    return bool((cfg or load_config()).get("auto_approve"))


def maybe_auto_approve(draft_id: str, cfg: dict = None) -> bool:
    """With Auto-approve on (Status tab), a waiting draft is approved straight
    away, carousel and Reel alike, and goes live at its slot."""
    cfg = cfg or load_config()
    if not auto_approve_on(cfg):
        return False
    d = find(load_state(), draft_id)
    if not d or d.get("status") == "approved":
        return False
    approve(draft_id)
    state = load_state()
    d = find(state, draft_id)
    if d:
        d["note"] = (d.get("note") + " " if d.get("note") and "after its" in d.get("note", "") else "") + \
            "Approved automatically (Auto-approve is on)."
        d["auto_approved"] = True
        save_state(state)
    return True


def unapprove(draft_id: str, parts: list = None):
    """Takes back the approval of some parts (default: all). With nothing
    approved, the draft waits for review again."""
    state = load_state()
    d = find(state, draft_id)
    if not d:
        return
    gone = set(parts or ["carousel", "reel"])
    d["approved_parts"] = [p for p in approved_parts(d) if p not in gone]
    if not d["approved_parts"]:
        d["status"], d["publish_at"], d["note"] = "awaiting", None, ""
        d["reminded"] = False
    save_state(state)
    return d


def publish(draft_id: str) -> bool:
    cfg, state = load_config(), load_state()
    draft = find(state, draft_id)
    if not draft:
        return False
    urls = image_urls(draft, cfg)
    if not DRY_RUN and draft.get("kind") != "reel":
        for u in urls:
            r = requests.get(u, timeout=30)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                retry_later(draft_id, f"An image is not reachable at\n{html.escape(u)}\n(HTTP {r.status_code}). "
                                      "Is the repository public?")
                return False
    version = cfg.get("instagram_api_version", "v25.0")
    has_reel = bool(draft.get("reel")) and (ROOT / draft["reel"]).exists()
    offer = offered_parts(cfg, draft)
    usable = [p for p in offer if p != "reel" or has_reel]
    if draft.get("status") != "approved":  # Post now on a draft that wasn't approved: everything on offer
        ok = usable
    else:
        ok = [p for p in approved_parts(draft) if p in usable]
    if not ok:  # only the Reel was approved and its file went missing
        unapprove(draft_id)
        telegram.send_message(f"Couldn't post {describe(draft)}: the Reel file is missing. Approve the carousel "
                              "in the app to post it instead.")
        return False
    share = reels_cfg(cfg)["share_to_feed"]
    reel_result, reel_error = None, ""
    reel_file = None
    if "reel" in ok:
        try:
            from . import reel as reel_mod
            reel_file = str(reel_mod.instagram_safe(ROOT / draft["reel"]))
        except Exception as e:  # fall back to the file as it is
            log(f"Couldn't prepare the Reel file: {e}")
            reel_file = str(ROOT / draft["reel"])
    try:
        if "carousel" in ok:
            result = instagram.publish_carousel(urls, draft["caption"], version, log)
            if "reel" in ok:
                try:
                    reel_result = instagram.publish_reel(video_url(draft["reel"]), draft["caption"], version, log,
                                                         share_to_feed=share, video_path=reel_file)
                except Exception as e:  # the carousel is live; the Reel just gets reported
                    reel_error = str(e)[:300]
        else:
            result = instagram.publish_reel(video_url(draft["reel"]), draft["caption"], version, log,
                                            share_to_feed=share, video_path=reel_file)
            reel_result = result
    except Exception as e:
        hint = ""
        if "token" in str(e).lower() or "OAuth" in str(e):
            hint = ("\n\nThis looks like an access token problem. Generate a new token in the Meta app dashboard "
                    "and update the IG_ACCESS_TOKEN secret.")
        retry_later(draft_id, f"Instagram said:\n<code>{html.escape(str(e)[:900])}</code>{hint}")
        return False

    state, history = load_state(), load_history()
    entry = {**_hist(draft), "status": "posted", "posted_at": now_iso(),
             "ig_media_id": result["id"], "permalink": result.get("permalink", ""),
             "format": "reel" if reel_result is result else ("both" if reel_result else "carousel")}
    if reel_result and reel_result is not result:
        entry["reel_media_id"], entry["reel_permalink"] = reel_result["id"], reel_result.get("permalink", "")
    history.append(entry)
    if draft.get("kind") == "reel":
        state["reel_counter"] = max(int(state.get("reel_counter", 1)), int(draft["number"]) + 1)
        entry["reel_format"] = draft.get("reel_format")
        entry["format"] = "reel"
    else:
        counters = state["series_counters"]
        counters[draft["series"]] = max(int(counters.get(draft["series"], 0)), int(draft["number"]) + 1)
    remove(state, draft_id)
    remove_draft_files(draft)
    if draft.get("backlog_line"):
        remove_topic_from_backlog(draft["backlog_line"])  # a topic is done only once it's live
    save_state(state)
    save_history(history)
    extra = ""
    if reel_result and reel_result is not result:
        extra = f"\nReel: {reel_result.get('permalink', '')}"
    elif reel_error:
        extra = f"\nThe Reel didn't go up: {html.escape(reel_error)}"
    elif "reel" in offer and has_reel and "reel" not in ok:
        extra = "\nThe Reel wasn't approved, so only the carousel went up."
    elif reel_result is result and "carousel" in offer:
        extra = " (Reel)\nThe carousel wasn't approved, so only the Reel went up."
    telegram.send_message(f"Live on Instagram: {describe(draft)}\n{result.get('permalink', '')}{extra}", live=True)
    return True


def retry_later(draft_id: str, reason: str):
    """A failed post moves to its back-up time, then to the same slot the next day."""
    cfg, state = load_config(), load_state()
    d = find(state, draft_id)
    if not d:
        return
    now = now_utc()
    backup = slot_backup(cfg, d)
    if now < backup - timedelta(minutes=5):
        d["publish_at"] = iso(backup)
    else:
        new_slot = next_free_same_slot(cfg, state["drafts"], d, now)
        d["slot_at"] = d["publish_at"] = iso(new_slot)
    d["status"] = "approved"
    d["failed_attempts"] = int(d.get("failed_attempts", 0)) + 1
    d["last_error"] = plain(reason)[:600]
    d["note"] = f"Couldn't post last time, so it will try again at {when_text(d['publish_at'])}."
    save_state(state)
    telegram.send_message(f"Couldn't post {describe(d)} yet. {reason}\n\n"
                          f"I'll try again automatically at {when_text(d['publish_at'])}, or tap Post now to retry sooner.",
                          buttons=telegram.scheduled_buttons(d["id"]))


def normalise_drafts(cfg: dict):
    """Drafts from older versions have no slot yet; give them the next free one."""
    state = load_state()
    changed = False
    for d in state["drafts"]:
        if not d.get("slot_at"):
            free = free_slots(cfg, state["drafts"], now_utc())
            name, at = free[0] if free else (slot_defs(cfg)[0]["name"],
                                             next_free_same_slot(cfg, state["drafts"], {"id": d["id"], "slot": slot_defs(cfg)[0]["name"]}, now_utc()))
            d["slot"], d["slot_at"], d["reminded"] = name, iso(at), False
            if d.get("status") == "approved":
                d["publish_at"] = d["slot_at"]
            changed = True
    # The posting times changed (for example 7:30 pm and 3:30 am became 6 pm):
    # drafts still sitting at an old time move to the next free new slot,
    # keeping their approval.
    defs = {sd["name"]: sd for sd in slot_defs(cfg)}
    moved = []
    for d in sorted(state["drafts"], key=lambda x: (x.get("status") != "approved", x.get("slot_at") or "")):
        if not d.get("slot_at"):
            continue
        sdef = defs.get(d.get("slot"))
        at = datetime.fromisoformat(d["slot_at"]).astimezone(IST)
        if sdef and (at.hour, at.minute) == sdef["time"]:
            continue
        same = next((x for x in defs.values() if x["time"] == (at.hour, at.minute)), None)
        if same:  # a slot was only renamed: same time, nothing moves
            d["slot"] = same["name"]
            changed = True
            continue
        d["slot"] = (sdef or slot_defs(cfg)[0])["name"]
        d["slot_at"] = iso(next_free_same_slot(cfg, state["drafts"], d, now_utc() + timedelta(minutes=20)))
        d["reminded"] = False
        if d.get("status") == "approved":
            d["publish_at"] = d["slot_at"]
        d["note"] = f"The posting time changed, so it moved to {when_text(d['slot_at'])}."
        moved.append(f"• {describe(d)}: {when_text(d['slot_at'])}")
        changed = True
    if changed:
        save_state(state)
    if moved:
        try:
            telegram.send_message("The posting time changed, so these drafts moved (approvals are kept):\n"
                                  + "\n".join(moved))
        except Exception as e:  # noqa: BLE001
            log(f"Couldn't send the move notice: {e}")
    return moved


def process_due(notify: bool = True):
    """Posts approved drafts whose time has come; reminds about unapproved
    drafts at their slot; moves drafts that missed their back-up to the next day."""
    cfg = load_config()
    normalise_drafts(cfg)
    if auto_approve_on(cfg):
        for d in list(load_state()["drafts"]):
            maybe_auto_approve(d["id"], cfg)
    now = now_utc()
    state = load_state()
    for d in drafts_of(state):
        state = load_state()
        d = find(state, d["id"])
        if not d:
            continue
        primary = datetime.fromisoformat(d["slot_at"])
        backup = slot_backup(cfg, d)
        if d["status"] == "approved":
            if d.get("publish_at") and now >= datetime.fromisoformat(d["publish_at"]) - timedelta(minutes=1):
                log(f"Publishing {d['id']}")
                publish(d["id"])
            continue
        # Awaiting review.
        if now >= backup + GRACE:
            new_slot = next_free_same_slot(cfg, state["drafts"], d, now)
            d["slot_at"], d["reminded"] = iso(new_slot), False
            d["note"] = f"It wasn't approved in time, so it moved to {when_text(d['slot_at'])}."
            save_state(state)
            if notify:
                telegram.send_message(f"{describe(d)} wasn't approved in time, so it moved to "
                                      f"{when_text(d['slot_at'])}. Approve it whenever you're ready.",
                                      buttons=telegram.review_buttons(d["id"]))
        elif now >= primary - timedelta(minutes=1) and not d.get("reminded"):
            if cfg.get("auto_post_if_not_reviewed"):
                telegram.send_message(f"You didn't review {describe(d)}, so I'm posting it as planned.")
                publish(d["id"])
                continue
            d["reminded"] = True
            save_state(state)
            if notify:
                b = backup.astimezone(IST).strftime("%I:%M %p").lstrip("0").lower()
                telegram.send_message(f"It's post time but {describe(d)} isn't approved yet. Approve it and it goes "
                                      f"live at {b} IST instead.",
                                      buttons=[telegram.review_buttons(d["id"])[0],
                                               [{"text": "Post now", "callback_data": f"postnow:{d['id']}"}]])


def post_due():
    """Slot job: starts ~30 minutes early (GitHub often runs timers late),
    waits for the nearest slot or back-up time, then posts what's due."""
    cfg = load_config()
    now = now_utc().astimezone(IST)
    times = []
    for sdef in slot_defs(cfg):
        for day in (now.date() - timedelta(days=1), now.date(), now.date() + timedelta(days=1)):
            times += list(occurrence(sdef, day))
    upcoming = sorted(t for t in times if 0 < (t - now).total_seconds() <= 45 * 60)
    if upcoming and not DRY_RUN and os.environ.get("NO_WAIT") != "1":
        wait = (upcoming[0] - now).total_seconds()
        log(f"Waiting {int(wait)}s for {upcoming[0].isoformat()}")
        time.sleep(wait)
    os.environ["CHECK_REPORT"] = "0"
    check()  # picks up last-minute taps, then process_due() posts what's due


def post_now_cmd():
    """Manual 'Go ahead and post': read Telegram, then post approved drafts
    straight away. POST_WHICH=all posts every approved draft."""
    which = os.environ.get("POST_WHICH", "next").strip().lower()
    before = {d["id"] for d in load_state()["drafts"]}
    os.environ["CHECK_REPORT"] = "0"
    check()
    state = load_state()
    approved = [d for d in drafts_of(state) if d["status"] == "approved"]
    posted_now = [h for h in load_history() if h.get("status") == "posted" and h.get("id") in before]
    if not approved:
        if posted_now:
            return  # check() already posted what was due
        waiting = [d for d in drafts_of(state) if d["status"] == "awaiting"]
        if waiting:
            telegram.send_message("Go ahead and post: nothing is approved yet. Tap Approve on a draft in Telegram, "
                                  "then run this again.")
        else:
            telegram.send_message("Go ahead and post: there are no drafts, so there's nothing to post.")
        return
    targets = approved if which.startswith("all") else approved[:1]
    for d in targets:
        telegram.send_message(f"Posting {describe(d)} now...")
        publish(d["id"])


# ------------------------------------------------------------------ other draft actions

def skip(draft_id: str):
    cfg, state, history = load_config(), load_state(), load_history()
    d = find(state, draft_id)
    if not d:
        return
    history.append({**_hist(d), "status": "skipped"})
    keep_topic(cfg, d)
    remove(state, draft_id)
    remove_draft_files(d)
    save_state(state)
    save_history(history)
    telegram.send_message(f"Set aside this version of {describe(d)}. Its topic stays first in line.\n"
                          f"The {when_text(d['slot_at'])} slot is now empty: send /new to fill it now, or the next "
                          "morning batch will.\nTo remove the topic for good instead, reply /drop to a draft.")


def drop(draft_id: str = None):
    state, history = load_state(), load_history()
    d = find(state, draft_id) if draft_id else None
    if d:
        if d.get("backlog_line"):
            remove_topic_from_backlog(d["backlog_line"])
        history.append({**_hist(d), "status": "dropped"})
        remove(state, d["id"])
        remove_draft_files(d)
        save_state(state)
        save_history(history)
        telegram.send_message(f"Dropped the topic of {describe(d)} for good. Its slot is free: send /new to fill it.")
        return
    telegram.send_message("Reply /drop to the draft whose topic you want to remove.")


def rerender(draft_id: str, reason: str = "Redrawn"):
    """Fresh slides for a draft. Any change to an approved draft (new words, new
    design, new look) takes its approval back: Sachin approves what goes live,
    so a changed post needs his approval again (his rule, 2026-10-18)."""
    cfg, state = load_config(), load_state()
    d = find(state, draft_id)
    if not d:
        return
    if d.get("kind") == "reel":  # a format Reel is redrawn by writing it again in its format
        new = write_format_draft(cfg, d["slot"], datetime.fromisoformat(d["slot_at"]), redo_of=d,
                                 fmt=d.get("reel_format"), feedback=None)
        return new["id"]
    new_id = now_utc().strftime("%Y%m%d-%H%M%S")
    while (POSTS / new_id).exists() or find(state, new_id):
        new_id += "b"
    paths = render_post(d["post"], d["theme"], d["tag"], POSTS / new_id, closing=closing_text(cfg),
                        label=series_label(cfg, d["series"], d["post"]), series=d["series"], cfg=cfg, style=d.get("style"))
    d["reel"] = build_reel(d["post"], d["series"], d["tag"], POSTS / new_id, cfg, d.get("style"))
    d["reel_v"] = REEL_VERSION
    remove_draft_files(d)
    d["id"] = new_id
    d["images"] = [str(p.relative_to(ROOT)) for p in paths]
    if d["status"] == "approved":
        d["status"], d["publish_at"], d["reminded"] = "awaiting", None, False
        d["approved_parts"] = []
        d["note"] = f"{reason} after you approved it, so it needs your approval again."
        d["unapproved_by_change"] = True
    save_state(state)
    full = {**d, "images": [str(ROOT / p) for p in d["images"]]}
    ids = telegram.send_review(full, when=when_text(d["slot_at"]))
    state = load_state()
    d = find(state, new_id)
    if d:
        d["msg_ids"] = (d.get("msg_ids") or []) + ids
        save_state(state)
    maybe_auto_approve(new_id, cfg)
    return new_id


def redo(draft_id: str, feedback: str = None):
    cfg, state = load_config(), load_state()
    d = find(state, draft_id)
    if not d:
        return
    return write_draft(cfg, d["slot"], datetime.fromisoformat(d["slot_at"]), redo_of=d, feedback=feedback)


def status_text(state: dict) -> str:
    ds = drafts_of(state)
    if not ds:
        return "No drafts right now. The next batch arrives in the morning, or send /new."
    lines = []
    for d in ds:
        if d["status"] == "approved":
            lines.append(f"• {describe(d)}: approved, goes live {when_text(d['publish_at'])}")
        else:
            lines.append(f"• {describe(d)}: waiting for review, slot {when_text(d['slot_at'])}")
    return "<b>Drafts</b>\n" + "\n".join(lines)


# ------------------------------------------------------------------ Telegram

def target_draft(state: dict, msg: dict, text: str):
    """Which draft a message is about: the one it replies to, else the only
    draft waiting for review."""
    reply = (msg.get("reply_to_message") or {}).get("message_id")
    if reply:
        d = find_by_message(state, reply)
        if d:
            return d
    waiting = [d for d in drafts_of(state) if d["status"] == "awaiting"]
    if len(waiting) == 1:
        return waiting[0]
    if len(state["drafts"]) == 1:
        return state["drafts"][0]
    return None


def refresh_old_designs():
    """Drafts drawn with older art-direction rules get redesigned once, as long
    as they aren't about to go live. Approvals and slots are kept."""
    from . import design
    state = load_state()
    soon = now_utc() + timedelta(minutes=20)
    for d in list(state.get("drafts", [])):
        dz = (d.get("post") or {}).get("design")
        if not isinstance(dz, dict) or dz.get("v") == design.DESIGN_VERSION:
            continue
        when = d.get("publish_at") or d.get("slot_at")
        if when and datetime.fromisoformat(when) <= soon:
            continue
        log(f"Redesigning {describe(d)} with the latest slide rules")
        try:
            rerender(d["id"], reason="Redesigned with the latest slide rules")
        except Exception as e:
            log(f"Couldn't redesign {d['id']}: {e}")


def add_missing_reels(cfg: dict, limit: int = 2):
    """Drafts written before Reels were switched on get one. If such a draft was
    already approved, the approval is taken back (a Reel is something new going
    live), and Sachin approves the carousel and the Reel again, each on its own."""
    if not reels_cfg(cfg)["make"]:
        return
    state = load_state()
    soon = now_utc() + timedelta(minutes=20)
    done = 0
    for d in state.get("drafts", []):
        if d.get("kind") == "reel":
            continue  # format Reels are made whole when they're written
        if done >= limit or datetime.fromisoformat(d.get("publish_at") or d["slot_at"]) <= soon:
            continue
        if d.get("reel") and d.get("status") == "awaiting" and int(d.get("reel_v", 1)) < REEL_VERSION:
            # Not approved yet, so a better Reel (new timing or look) can replace the old one.
            path = build_reel(d["post"], d["series"], d["tag"], POSTS / d["id"], cfg, d.get("style"))
            d["reel_v"] = REEL_VERSION
            done += 1
            if path:
                d["reel"] = path
                log(f"Rebuilt the Reel of {describe(d)}")
            continue
        if d.get("reel") or d.get("reel_tried"):
            continue
        path = build_reel(d["post"], d["series"], d["tag"], POSTS / d["id"], cfg, d.get("style"))
        d["reel_tried"] = True  # one attempt per draft, so a failing build doesn't repeat every 10 minutes
        done += 1
        if not path:
            continue
        d["reel"], d["reel_v"] = path, REEL_VERSION
        if d.get("status") == "approved":
            d["status"], d["publish_at"], d["reminded"] = "awaiting", None, False
            d["approved_parts"] = []
            d["note"] = "A Reel was added, so it needs your approval again: approve the carousel and the Reel."
            d["unapproved_by_change"] = True
        log(f"Added a Reel to {describe(d)}")
    if done:
        save_state(state)


def check() -> None:
    cfg = load_config()
    merge_topic_seeds(cfg)
    refresh_old_designs()
    try:
        add_missing_reels(cfg)
    except Exception as e:  # a Reel is extra; never block posting
        log(f"Couldn't add Reels: {e}")
    try:
        from . import stats
        stats.refresh(cfg, log)
    except Exception as e:  # numbers are nice to have; never block posting
        log(f"Stats refresh failed: {e}")
    if not telegram.enabled():
        log("Telegram is switched off in config.json; only posting what's due")
        process_due(notify=True)
        return
    state = load_state()
    authorised = str(telegram.chat_id())
    seen = [int(x) for x in state.get("seen_updates", [])]
    # Peek first (this confirms nothing), handle every update not handled before,
    # then confirm. Earlier versions asked only for ids above the last one seen,
    # which silently threw away taps if Telegram's numbering ever went backwards.
    updates = sorted(telegram.get_updates(), key=lambda u: u["update_id"])
    fresh = [u for u in updates if u["update_id"] not in seen]
    report = {"read": len(updates), "fresh": len(fresh), "handled": 0, "other_chat": 0}
    ids = [u["update_id"] for u in updates]
    log(f"Telegram: {len(updates)} waiting update(s), {len(fresh)} new; ids {ids[:1]}..{ids[-1:]}; "
        f"last handled {state['last_update_id']}")
    if not updates:
        try:
            info = telegram.webhook_info()
            log(f"Webhook info: url={info.get('url') or 'none'} pending={info.get('pending_update_count')} "
                f"last_error={info.get('last_error_message')}")
            report["webhook"] = info.get("url") or ""
        except Exception as e:
            log(f"Webhook info failed: {e}")

    for upd in fresh:
        state = load_state()
        state["last_update_id"] = max(int(state["last_update_id"]), upd["update_id"])
        state["seen_updates"] = (state.get("seen_updates", []) + [upd["update_id"]])[-300:]
        save_state(state)

        if "callback_query" in upd:
            cq = upd["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != authorised:
                report["other_chat"] += 1
                continue
            report["handled"] += 1
            action, _, draft_id = cq.get("data", "").partition(":")
            if not find(state, draft_id):
                telegram.answer_callback(cq["id"], "This draft is no longer active.")
                continue
            if action == "approve":
                telegram.answer_callback(cq["id"], "Approved")
                approve(draft_id)
            elif action == "postnow":
                telegram.answer_callback(cq["id"], "Publishing now...")
                publish(draft_id)
            elif action == "redo":
                telegram.answer_callback(cq["id"], "Writing a new version...")
                redo(draft_id)
            elif action == "skip":
                telegram.answer_callback(cq["id"], "Skipped.")
                skip(draft_id)
            continue

        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != authorised:
            report["other_chat"] += 1
            continue
        report["handled"] += 1
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        target = target_draft(state, msg, text)
        replied = find_by_message(state, (msg.get("reply_to_message") or {}).get("message_id"))

        if cmd in ("/start", "/help"):
            telegram.send_message(HELP, log_event=False)
        elif cmd == "/status":
            telegram.send_message(status_text(state), log_event=False)
        elif cmd == "/topic":
            if arg.strip():
                add_topic_to_backlog(clean_text(arg))
                telegram.send_message(f"Added to the topic list: {html.escape(arg.strip())}")
            else:
                telegram.send_message("Usage: /topic your idea")
        elif cmd == "/next":
            if arg.strip():
                add_topic_to_backlog(clean_text(arg), front=True)
                telegram.send_message(f"Next in line: {html.escape(arg.strip())}")
            else:
                telegram.send_message("Usage: /next your idea")
        elif cmd == "/topics":
            nxt = topic_backlog()[:12]
            telegram.send_message("<b>Next topics</b>\n" + "\n".join(f"{i + 1}. {html.escape(t)}" for i, t in enumerate(nxt))
                                  if nxt else "The topic list is empty, so the writer will pick fresh topics itself.",
                                  log_event=False)
        elif cmd == "/drop":
            drop((replied or (target if len(state["drafts"]) == 1 else None) or {}).get("id"))
        elif cmd == "/rerender":
            ids = [replied["id"]] if replied else [d["id"] for d in drafts_of(state)]
            if not ids:
                telegram.send_message("There are no drafts to redraw right now.")
            for i in ids:
                rerender(i)
        elif cmd == "/new":
            free = free_slots(cfg, state["drafts"], now_utc())
            if not free:
                telegram.send_message("Every upcoming slot already has a draft. Skip one first to make room, "
                                      "then send /new again.")
            else:
                telegram.send_message("Writing a new draft...")
                name, slot_at = free[0]
                write_draft(cfg, name, slot_at, topic=arg.strip() or None)
        elif text.startswith("/"):
            telegram.send_message(HELP, log_event=False)
        elif target:
            telegram.send_message(f"Revising {describe(target)} with your feedback...")
            redo(target["id"], feedback=text)
        elif state["drafts"]:
            telegram.send_message("Which draft is that for? Reply to the draft's message with your feedback, "
                                  "so I know which one to change.")
        else:
            telegram.send_message("There is no draft to revise right now. Send /new to make one, or /help.")

    if updates:  # tell Telegram these are done, so they aren't sent again
        telegram.get_updates(offset=updates[-1]["update_id"] + 1, limit=1)
    process_due(notify=True)
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" and os.environ.get("CHECK_REPORT", "1") == "1":
        send_check_report(report)


def send_check_report(report: dict):
    """Sent when you run '2. Check Telegram for approvals' by hand, so you can see it working."""
    state = load_state()
    lines = ["<b>Manual check</b>",
             f"Taps and messages I found in Telegram: {report['fresh']} new"
             + (f" ({report['read'] - report['fresh']} already handled)" if report["read"] > report["fresh"] else "")]
    if report["other_chat"]:
        lines.append(f"{report['other_chat']} came from a different chat than TELEGRAM_CHAT_ID, so I ignored them. "
                     "Check that secret.")
    if report.get("webhook"):
        lines.append("Another service is connected to this bot (a webhook), and it takes your taps before GitHub "
                     "can read them.")
    if report["fresh"] == 0:
        lines.append("If you tapped a button or sent a message in the last day and I found nothing, another app or "
                     "website is using this bot's token and reading your messages first. Fix: in BotFather send "
                     "/revoke, pick this bot, and put the new token in the TELEGRAM_BOT_TOKEN secret on GitHub.")
    lines.append("")
    lines.append(status_text(state))
    telegram.send_message("\n".join(lines), log_event=False)


# ------------------------------------------------------------------ maintenance

def refresh_token_cmd():
    try:
        body = instagram.refresh_token()
    except Exception as e:
        telegram.send_message(f"I could not renew the Instagram token:\n<code>{html.escape(str(e)[:800])}</code>\n"
                              "Generate a new token in the Meta app dashboard and update the IG_ACCESS_TOKEN secret.")
        raise
    days = int(body.get("expires_in", 0)) // 86400
    out = os.environ.get("TOKEN_OUT")
    if out:
        Path(out).write_text(body["access_token"], encoding="utf-8")
    log(f"Token refreshed, valid for about {days} days")


def setup_check():
    cfg = load_config()
    results = []
    try:
        results.append(f"Writer: OK ({writer.describe_writer(cfg)})")
    except Exception as e:
        results.append(f"Writer: FAILED {e}")
    try:
        me = instagram.whoami(cfg.get("instagram_api_version", "v25.0"))
        results.append(f"Instagram: OK (@{me.get('username')}, id {me.get('user_id')})")
    except Exception as e:
        results.append(f"Instagram: FAILED {e}")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}", timeout=30)
        results.append("Repository is public: OK" if r.status_code == 200 and not r.json().get("private")
                       else "Repository is private: Instagram cannot fetch images from it. Make it public, or set image_host.")
    except Exception as e:
        results.append(f"Repository check skipped: {e}")
    slots = ", ".join(f"{s['name']} {s['time'][0]:02d}:{s['time'][1]:02d} (back-up {s['backup'][0]:02d}:{s['backup'][1]:02d})"
                      for s in slot_defs(cfg))
    results.append(f"Posting slots (IST): {slots}")
    text = "\n".join(results)
    log(text)
    try:
        telegram.send_message("<b>Setup check</b>\n" + html.escape(text) + "\n\nTelegram: OK (you are reading this)")
    except Exception as e:
        log(f"Telegram: FAILED {e}")
        raise SystemExit(1)
    if "FAILED" in text:
        raise SystemExit(1)


def preview(path: str):
    import json
    cfg = load_config()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    series = data.get("series", default_series(cfg))
    post = writer.apply_format(writer.validate(data, int(cfg.get("max_body_slides", 7))), cfg)
    for theme in ("light", "dark"):
        out = POSTS / "preview" / theme
        paths = render_post(post, theme, cfg["series"][series]["tag"].format(n=0), out,
                            label=series_label(cfg, series, post), series=series, cfg=cfg)
        log(f"{theme}: {len(paths)} slides in {out}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    try:
        run(cmd)
    except SystemExit:
        raise
    except Exception as e:
        # Tell the user (Telegram and the app) instead of failing silently on GitHub.
        try:
            telegram.send_message(f"Something went wrong ({html.escape(cmd)}):\n"
                                  f"<code>{html.escape(str(e)[-900:])}</code>")
        except Exception:
            pass
        raise
    finally:
        save_events(cmd)


def plain(text: str) -> str:
    """Telegram HTML -> plain text for the app."""
    return html.unescape(re.sub(r"<[^>]+>", "", str(text or ""))).strip()


def save_events(cmd: str):
    if not telegram.EVENTS or cmd == "preview" or DRY_RUN and os.environ.get("KEEP_EVENTS") != "1":
        return
    try:
        state = load_state()
        job = os.environ.get("GITHUB_WORKFLOW", cmd)
        state["events"] = (state.get("events", []) + [{"at": now_iso(), "job": job, "text": plain(t)[:700]}
                                                      for t in telegram.EVENTS])[-80:]
        save_state(state)
        telegram.EVENTS.clear()
    except Exception as e:
        log(f"Could not save events: {e}")


def run(cmd: str):
    if cmd == "generate":
        topic = os.environ.get("TOPIC", "").strip() or None
        generate(scheduled=os.environ.get("GITHUB_EVENT_NAME") == "schedule", topic=topic)
    elif cmd == "check":
        check()
    elif cmd == "app":
        from .app_actions import app_cmd
        app_cmd()
    elif cmd == "post-due":
        post_due()
    elif cmd == "post-now":
        post_now_cmd()
    elif cmd == "refresh-token":
        refresh_token_cmd()
    elif cmd == "setup-check":
        setup_check()
    elif cmd == "preview":
        preview(sys.argv[2] if len(sys.argv) > 2 else "content/sample_post.json")
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
