"""Entry point. Run with:  python -m src.main <command>

Commands
  generate        write + render a new draft and send it to Telegram (daily job)
  check           read Telegram taps/messages and act on them (every 30 min)
  post-due        publish the approved draft at the daily post time (5 pm IST job)
  refresh-token   renew the 60-day Instagram token (monthly job)
  setup-check     test every key and send a hello message to Telegram
  preview FILE    render a post JSON locally into posts/preview/ (no APIs)
"""
import html
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import instagram, telegram, writer
from .common import (DRY_RUN, POSTS, ROOT, add_topic_to_backlog, clean_text, load_config, load_history,
                     load_state, now_iso, remove_topic_from_backlog, save_history, save_state, topic_backlog)
from .render import render_post

HELP = (
    "<b>MISFIT autopost bot</b>\n\n"
    "Buttons under each draft: <b>Approve</b> schedules it for the daily post time, <b>Redo</b> writes a new version, "
    "<b>Skip</b> drops it. After approving you also get <b>Post now</b>.\n"
    "Reply with any text to revise the current draft using your feedback.\n\n"
    "/new  make a draft now from the next topic\n"
    "/new <i>idea</i>  make a draft now about your idea\n"
    "/topic <i>idea</i>  add an idea to the topic list\n"
    "/topics  show the next topics\n"
    "/status  what is pending\n\n"
    "I act on your taps and replies at the next check, which runs every 30 minutes."
)


# ------------------------------------------------------------------ helpers

def log(msg: str):
    print(msg, flush=True)


def parse_topic(line: str):
    """'[MarketersAssemble] idea' -> ('MarketersAssemble', 'idea')"""
    m = re.match(r"^\[(\w+)\]\s*(.+)$", line.strip())
    return (m.group(1), m.group(2)) if m else (None, line.strip())


def choose_series(cfg: dict, history: list) -> str:
    """Keeps the mix close to the configured weights (default 4 tips : 1 mindset post)."""
    names = list(cfg["series"])
    counts = {n: 0 for n in names}
    for h in history:
        if h.get("status") == "posted" and h.get("series") in counts:
            counts[h["series"]] += 1
    weights = {n: max(float(cfg["series"][n].get("weight", 1)), 0.0) for n in names}
    total_w = sum(weights.values()) or 1.0
    upcoming = sum(counts.values()) + 1
    return max(names, key=lambda n: (weights[n] * upcoming / total_w - counts[n], -names.index(n)))


def next_theme(cfg: dict, state: dict) -> str:
    mode = cfg.get("theme", "alternate")
    if mode in ("light", "dark"):
        return mode
    return "light" if state.get("last_theme") == "dark" else "dark"


def series_number(cfg: dict, state: dict, series: str) -> int:
    return int(state["series_counters"].get(series, cfg["series"][series].get("next_number", 1)))


def build_caption(post: dict) -> str:
    caption = post["caption"].strip()
    tags = " ".join(post.get("hashtags", [])[:30])
    full = f"{caption}\n\n{tags}".strip()
    return full[:2200]


def image_urls(draft: dict, cfg: dict) -> list:
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    rels = [Path(p).as_posix() for p in draft["images"]]
    if cfg.get("image_host") == "jsdelivr":
        return [f"https://cdn.jsdelivr.net/gh/{repo}@{branch}/{r}" for r in rels]
    return [f"https://raw.githubusercontent.com/{repo}/{branch}/{r}" for r in rels]


def remove_draft_files(draft: dict):
    d = POSTS / draft["id"]
    if d.exists():
        shutil.rmtree(d)


def describe(draft: dict) -> str:
    return f"{draft['tag']} ({html.escape(draft['post'].get('principle') or draft['topic'])})"


IST = timezone(timedelta(hours=5, minutes=30))


def post_time(cfg: dict):
    hh, mm = (int(x) for x in str(cfg.get("post_time_ist", "17:00")).split(":"))
    return hh, mm


def next_slot(cfg: dict, now: datetime = None) -> datetime:
    """Today's post time in IST, or tomorrow's once today's has passed.
    Approvals up to 15 minutes late still count for today."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    hh, mm = post_time(cfg)
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now >= slot + timedelta(minutes=15):
        slot += timedelta(days=1)
    return slot


def slot_text(slot_iso: str) -> str:
    slot = datetime.fromisoformat(slot_iso).astimezone(IST)
    today = datetime.now(IST).date()
    day = "today" if slot.date() == today else "tomorrow" if slot.date() == today + timedelta(days=1) \
        else slot.strftime("%a %d %b")
    return f"{slot.strftime('%-I:%M %p').lower()} IST {day}"


# ------------------------------------------------------------------ generate

def generate(topic: str = None, series: str = None, feedback: str = None, redo_of: dict = None,
             scheduled: bool = False) -> None:
    cfg, state, history = load_config(), load_state(), load_history()
    pending = state.get("pending")

    if pending and not redo_of:
        if pending.get("status") == "approved":
            telegram.send_message(f"{describe(pending)} is already scheduled for {slot_text(pending['publish_at'])}, "
                                  "so I did not write a new draft. Skip it first if you want a different post.",
                                  buttons=telegram.scheduled_buttons(pending["id"]))
            return
        if scheduled:
            telegram.send_message(f"Reminder: {describe(pending)} is still waiting for your review, "
                                  "so I did not write a new one today.", buttons=telegram.review_buttons(pending["id"]))
            return
        # A manual /new replaces whatever was pending.
        history.append({**_hist(pending), "status": "replaced"})
        remove_draft_files(pending)
        state["pending"] = None

    from_backlog = None
    if redo_of:
        series, topic = redo_of["series"], redo_of["topic"]
        theme, number = redo_of["theme"], redo_of["number"]
    else:
        if not topic:
            backlog = topic_backlog()
            if backlog:
                from_backlog = backlog[0]
                series_hint, topic = parse_topic(from_backlog)
                if series_hint in cfg["series"]:
                    series = series_hint
        else:
            series_hint, topic = parse_topic(topic)
            if series_hint in cfg["series"]:
                series = series_hint
        if series not in cfg["series"]:
            # A named topic without a [Series] tag is a regular tip; the
            # weighted mix only decides when the writer picks the topic itself.
            series = list(cfg["series"])[0] if topic else choose_series(cfg, history)
        theme = next_theme(cfg, state)
        number = series_number(cfg, state, series)

    recent = [h.get("principle") or h.get("topic") for h in history if h.get("status") == "posted"]
    log(f"Writing {series} #{number} on: {topic or '(writer chooses)'}")
    post = writer.write_post(cfg, series, topic, recent, feedback=feedback,
                             previous=redo_of["post"] if redo_of else None)

    draft_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    while (POSTS / draft_id).exists() or (redo_of and redo_of["id"] == draft_id):
        draft_id += "b"
    tag = cfg["series"][series]["tag"].format(n=number)
    closing = None
    if cfg.get("closing_slide", {}).get("enabled"):
        closing = cfg["closing_slide"]["text"].format(handle=cfg.get("instagram_handle", ""))
    paths = render_post(post, theme, tag, POSTS / draft_id, closing=closing)

    if redo_of:
        history.append({**_hist(redo_of), "status": "redone", "feedback": feedback or ""})
        remove_draft_files(redo_of)
    if from_backlog:
        remove_topic_from_backlog(from_backlog)

    draft = {
        "id": draft_id, "series": series, "number": number, "tag": tag, "theme": theme,
        "topic": topic or post.get("topic"), "post": post, "caption": build_caption(post),
        "images": [str(p.relative_to(ROOT)) for p in paths], "created_at": now_iso(),
        "status": "awaiting",
    }
    state["pending"] = draft
    save_state(state)
    save_history(history)
    telegram.send_review({**draft, "images": [str(ROOT / p) for p in draft["images"]]})
    log(f"Draft {draft_id} sent for review ({len(paths)} slides)")


def _hist(draft: dict) -> dict:
    return {k: draft.get(k) for k in ("id", "series", "number", "tag", "topic")} | {
        "principle": draft.get("post", {}).get("principle"), "at": now_iso()}


# ------------------------------------------------------------------ publish

def publish(draft: dict) -> bool:
    cfg, state, history = load_config(), load_state(), load_history()
    urls = image_urls(draft, cfg)
    # Make sure the images are reachable before handing them to Instagram.
    if not DRY_RUN:
        for u in urls:
            r = requests.get(u, timeout=30)
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                telegram.send_message(f"Could not publish yet: an image is not reachable at\n{html.escape(u)}\n"
                                      f"(HTTP {r.status_code}). Is the repository public? I'll keep the draft.",
                                      buttons=telegram.scheduled_buttons(draft["id"]))
                return False
    try:
        result = instagram.publish_carousel(urls, draft["caption"], cfg.get("instagram_api_version", "v25.0"), log)
    except Exception as e:
        hint = ""
        if "token" in str(e).lower() or "OAuth" in str(e):
            hint = "\n\nThis looks like an access token problem. Generate a new token in the Meta app dashboard and update the IG_ACCESS_TOKEN secret."
        telegram.send_message(f"Instagram publishing failed for {describe(draft)}:\n<code>{html.escape(str(e)[:900])}</code>{hint}\n\n"
                              "Tap Post now to try again.", buttons=telegram.scheduled_buttons(draft["id"]))
        return False

    history.append({**_hist(draft), "status": "posted", "posted_at": now_iso(),
                    "ig_media_id": result["id"], "permalink": result.get("permalink", "")})
    state["series_counters"][draft["series"]] = int(draft["number"]) + 1
    state["last_theme"] = draft["theme"]
    state["pending"] = None
    remove_draft_files(draft)
    save_state(state)
    save_history(history)
    telegram.send_message(f"Posted {describe(draft)}\n{result.get('permalink', '')}")
    return True


# ------------------------------------------------------------------ check

def check() -> None:
    state = load_state()
    authorised = str(telegram.chat_id())
    updates = telegram.get_updates(int(state["last_update_id"]) + 1)
    log(f"{len(updates)} new Telegram update(s)")

    for upd in updates:
        state = load_state()
        state["last_update_id"] = upd["update_id"]
        save_state(state)
        pending = state.get("pending")

        if "callback_query" in upd:
            cq = upd["callback_query"]
            if str(cq.get("message", {}).get("chat", {}).get("id")) != authorised:
                continue
            action, _, draft_id = cq.get("data", "").partition(":")
            if not pending or pending["id"] != draft_id:
                telegram.answer_callback(cq["id"], "This draft is no longer active.")
                continue
            if action == "approve":
                telegram.answer_callback(cq["id"], "Approved")
                approve(pending)
            elif action == "postnow":
                telegram.answer_callback(cq["id"], "Publishing now...")
                publish(pending)
            elif action == "redo":
                telegram.answer_callback(cq["id"], "Writing a new version...")
                generate(redo_of=pending)
            elif action == "skip":
                telegram.answer_callback(cq["id"], "Skipped.")
                skip(pending)
            continue

        msg = upd.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != authorised:
            continue
        text = (msg.get("text") or "").strip()
        if not text:
            continue
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            telegram.send_message(HELP)
        elif cmd == "/status":
            if pending and pending.get("status") == "approved":
                telegram.send_message(f"Scheduled: {describe(pending)} goes live at {slot_text(pending['publish_at'])}.",
                                      buttons=telegram.scheduled_buttons(pending["id"]))
            elif pending:
                telegram.send_message(f"Waiting for review: {describe(pending)}",
                                      buttons=telegram.review_buttons(pending["id"]))
            else:
                telegram.send_message("Nothing pending. The next draft arrives on the schedule, or send /new.")
        elif cmd == "/topic":
            if arg.strip():
                add_topic_to_backlog(clean_text(arg))
                telegram.send_message(f"Added to the topic list: {html.escape(arg.strip())}")
            else:
                telegram.send_message("Usage: /topic your idea")
        elif cmd == "/topics":
            nxt = topic_backlog()[:10]
            telegram.send_message("<b>Next topics</b>\n" + "\n".join(f"{i+1}. {html.escape(t)}" for i, t in enumerate(nxt))
                                  if nxt else "The topic list is empty, so the writer will pick fresh topics itself.")
        elif cmd == "/new":
            if pending and pending.get("status") == "approved":
                telegram.send_message(f"{describe(pending)} is already scheduled. Skip it first if you want a new draft.",
                                      buttons=telegram.scheduled_buttons(pending["id"]))
            else:
                telegram.send_message("Writing a new draft...")
                generate(topic=arg.strip() or None)
        elif text.startswith("/"):
            telegram.send_message(HELP)
        elif pending:
            telegram.send_message("Revising with your feedback...")
            generate(redo_of=pending, feedback=text)
        else:
            telegram.send_message("There is no draft to revise right now. Send /new to make one, or /help.")

    # Safety net: if the 5 pm job was skipped or delayed by GitHub, post here.
    state = load_state()
    pending = state.get("pending")
    if pending and pending.get("status") == "approved":
        if datetime.now(timezone.utc) >= datetime.fromisoformat(pending["publish_at"]):
            log("Approved draft is overdue, publishing")
            publish(pending)


def approve(draft: dict):
    cfg, state = load_config(), load_state()
    slot = next_slot(cfg)
    draft = state["pending"]
    draft["status"] = "approved"
    draft["approved_at"] = now_iso()
    draft["publish_at"] = slot.astimezone(timezone.utc).isoformat()
    save_state(state)
    telegram.send_message(f"Approved. {describe(draft)} goes live at {slot_text(draft['publish_at'])}.\n"
                          "Tap Post now if you want it up straight away.",
                          buttons=telegram.scheduled_buttons(draft["id"]))


def post_due():
    """Runs a few minutes before the post time, waits for it, then publishes."""
    import time
    cfg, state = load_config(), load_state()
    hh, mm = post_time(cfg)
    now = datetime.now(IST)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= 25 * 60 and not DRY_RUN:
        log(f"Waiting {int(wait)}s for the post time")
        time.sleep(wait)
    # Pick up any Approve taps sent while this job was waiting. An approval
    # for today is published right here by check()'s overdue step.
    check()
    state = load_state()
    pending = state.get("pending")
    if not pending:
        today = datetime.now(IST).date()
        posted_today = any(h.get("status") == "posted" and h.get("posted_at") and
                           datetime.fromisoformat(h["posted_at"]).astimezone(IST).date() == today
                           for h in load_history())
        log("Nothing to post")
        if not posted_today:
            telegram.send_message("There was no draft for today's post, so nothing went live. Send /new to make one.")
        return
    if pending.get("status") == "approved":
        if datetime.now(timezone.utc) >= datetime.fromisoformat(pending["publish_at"]) - timedelta(minutes=1):
            publish(pending)
        else:
            log("Approved draft is scheduled for a later day")
        return
    # Awaiting review at post time.
    if cfg.get("auto_post_if_not_reviewed"):
        telegram.send_message(f"You didn't review {describe(pending)}, so I'm posting it as planned.")
        publish(pending)
    else:
        pending["status"] = "awaiting"
        save_state(state)
        telegram.send_message(f"It's post time but {describe(pending)} isn't approved, so nothing went live today. "
                              "Approve it to schedule it for tomorrow, or tap Post now.",
                              buttons=[telegram.review_buttons(pending["id"])[0],
                                       [{"text": "Post now", "callback_data": f"postnow:{pending['id']}"}]])


def skip(draft: dict):
    state, history = load_state(), load_history()
    history.append({**_hist(draft), "status": "skipped"})
    state["pending"] = None
    remove_draft_files(draft)
    save_state(state)
    save_history(history)
    telegram.send_message(f"Skipped {describe(draft)}. Send /new for another draft now, or wait for the next scheduled one.")


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
    post = writer.validate(json.loads(Path(path).read_text(encoding="utf-8")), int(cfg.get("max_body_slides", 7)))
    for theme in ("light", "dark"):
        out = POSTS / "preview" / theme
        paths = render_post(post, theme, cfg["series"]["MarketingTip"]["tag"].format(n=0), out)
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
        # Tell the user on Telegram instead of failing silently on GitHub.
        try:
            telegram.send_message(f"Something went wrong ({html.escape(cmd)}):\n"
                                  f"<code>{html.escape(str(e)[-900:])}</code>")
        except Exception:
            pass
        raise


def run(cmd: str):
    if cmd == "generate":
        topic = os.environ.get("TOPIC", "").strip() or None
        generate(topic=topic, scheduled=os.environ.get("GITHUB_EVENT_NAME") == "schedule")
    elif cmd == "check":
        check()
    elif cmd == "post-due":
        post_due()
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
