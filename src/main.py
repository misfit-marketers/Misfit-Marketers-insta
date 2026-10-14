"""Entry point. Run with:  python -m src.main <command>

Commands
  generate        write + render a new draft and send it to Telegram (daily job)
  check           read Telegram taps/messages and act on them (every 30 min)
  post-due        publish the approved draft at a posting slot (6 pm and 8 pm IST jobs)
  post-now        read Telegram, then post the approved draft immediately (manual job)
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
    "<b>Skip</b> sets this version aside (the topic stays first in line). After approving you also get <b>Post now</b>.\n"
    "A topic only leaves the list once it is live on Instagram.\n"
    "Reply with any text to revise the current draft using your feedback.\n\n"
    "/new  make a draft now from the next topic\n"
    "/new <i>idea</i>  make a draft now about your idea\n"
    "/topic <i>idea</i>  add an idea to the end of the topic list\n"
    "/next <i>idea</i>  put an idea at the front of the topic list\n"
    "/drop  remove the current draft's topic for good and move on\n"
    "/topics  show the next topics\n"
    "/status  what is pending\n"
    "/rerender  redraw the current draft's slides (same text) with the latest template\n\n"
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


def post_times(cfg: dict) -> list:
    """Daily posting slots in IST, earliest first, e.g. [(18, 0), (20, 0)].
    The later slots are back-ups for when an earlier one is missed."""
    raw = cfg.get("post_times_ist") or [cfg.get("post_time_ist", "18:00")]
    return sorted(tuple(int(x) for x in str(t).split(":")) for t in raw)


def next_slot(cfg: dict, now: datetime = None, grace_min: int = 15) -> datetime:
    """The next posting slot. A slot still counts for up to grace_min
    minutes after it starts (so a tap at 6:10 pm still means 6 pm)."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    for day in range(3):
        for hh, mm in post_times(cfg):
            slot = (now + timedelta(days=day)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now < slot + timedelta(minutes=grace_min):
                return slot
    raise RuntimeError("no posting slot found")


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
        # A manual /new replaces whatever was pending; its topic is kept.
        history.append({**_hist(pending), "status": "replaced"})
        keep_topic(pending)
        remove_draft_files(pending)
        state["pending"] = None

    from_backlog = None
    if redo_of:
        series, topic = redo_of["series"], redo_of["topic"]
        theme, number = redo_of["theme"], redo_of["number"]
        from_backlog = redo_of.get("backlog_line")
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

    draft = {
        "id": draft_id, "series": series, "number": number, "tag": tag, "theme": theme,
        "topic": topic or post.get("topic"), "post": post, "caption": build_caption(post),
        "images": [str(p.relative_to(ROOT)) for p in paths], "created_at": now_iso(),
        "status": "awaiting", "backlog_line": from_backlog,
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
                retry_later(draft, f"An image is not reachable at\n{html.escape(u)}\n(HTTP {r.status_code}). "
                                   "Is the repository public?")
                return False
    try:
        result = instagram.publish_carousel(urls, draft["caption"], cfg.get("instagram_api_version", "v25.0"), log)
    except Exception as e:
        hint = ""
        if "token" in str(e).lower() or "OAuth" in str(e):
            hint = "\n\nThis looks like an access token problem. Generate a new token in the Meta app dashboard and update the IG_ACCESS_TOKEN secret."
        retry_later(draft, f"Instagram said:\n<code>{html.escape(str(e)[:900])}</code>{hint}")
        return False

    history.append({**_hist(draft), "status": "posted", "posted_at": now_iso(),
                    "ig_media_id": result["id"], "permalink": result.get("permalink", "")})
    state["series_counters"][draft["series"]] = int(draft["number"]) + 1
    state["last_theme"] = draft["theme"]
    state["pending"] = None
    remove_draft_files(draft)
    if draft.get("backlog_line"):
        remove_topic_from_backlog(draft["backlog_line"])  # the topic is done only once it's live
    save_state(state)
    save_history(history)
    telegram.send_message(f"Posted {describe(draft)}\n{result.get('permalink', '')}")
    return True


def retry_later(draft: dict, reason: str):
    """A failed post moves to the next slot: 6 pm -> 8 pm -> next day 6 pm."""
    cfg, state = load_config(), load_state()
    pending = state.get("pending")
    if pending and pending["id"] == draft["id"]:
        slot = next_slot(cfg, grace_min=0)
        pending["status"] = "approved"
        pending["publish_at"] = slot.astimezone(timezone.utc).isoformat()
        pending["failed_attempts"] = int(pending.get("failed_attempts", 0)) + 1
        save_state(state)
        when = slot_text(pending["publish_at"])
    else:
        when = "the next posting time"
    telegram.send_message(f"Couldn't post {describe(draft)} yet. {reason}\n\n"
                          f"I'll try again automatically at {when}, or tap Post now to retry sooner.",
                          buttons=telegram.scheduled_buttons(draft["id"]))


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
        elif cmd == "/next":
            if arg.strip():
                add_topic_to_backlog(clean_text(arg), front=True)
                telegram.send_message(f"Next in line: {html.escape(arg.strip())}")
            else:
                telegram.send_message("Usage: /next your idea")
        elif cmd == "/drop":
            drop(pending)
        elif cmd == "/topics":
            nxt = topic_backlog()[:10]
            telegram.send_message("<b>Next topics</b>\n" + "\n".join(f"{i+1}. {html.escape(t)}" for i, t in enumerate(nxt))
                                  if nxt else "The topic list is empty, so the writer will pick fresh topics itself.")
        elif cmd == "/rerender":
            if pending:
                telegram.send_message("Redrawing the slides...")
                rerender(pending)
            else:
                telegram.send_message("There is no draft to redraw right now.")
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

    # Safety net: if a posting job was skipped or delayed by GitHub, post here.
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
    """Runs a few minutes before each posting slot (6 pm and 8 pm IST by
    default), waits for it, then publishes the approved draft."""
    import time
    cfg = load_config()
    now = datetime.now(IST)
    slots_today = [now.replace(hour=hh, minute=mm, second=0, microsecond=0) for hh, mm in post_times(cfg)]
    # GitHub often starts timed jobs late, so this job is scheduled ~30 min
    # early and waits here for the slot.
    upcoming = [s for s in slots_today if 0 < (s - now).total_seconds() <= 45 * 60]
    if upcoming and not DRY_RUN:
        wait = (upcoming[0] - now).total_seconds()
        log(f"Waiting {int(wait)}s for the post time")
        time.sleep(wait)
    now = datetime.now(IST)
    later_today = [s for s in slots_today if s > now + timedelta(minutes=15)]

    # Pick up any Approve taps sent while this job was waiting. An approval
    # for this slot is published right here by check()'s overdue step.
    check()
    state = load_state()
    pending = state.get("pending")
    if not pending:
        today = now.date()
        posted_today = any(h.get("status") == "posted" and h.get("posted_at") and
                           datetime.fromisoformat(h["posted_at"]).astimezone(IST).date() == today
                           for h in load_history())
        log("Nothing to post")
        if not posted_today and not later_today:
            telegram.send_message("There was no draft for today's post, so nothing went live. Send /new to make one.")
        return
    if pending.get("status") == "approved":
        if datetime.now(timezone.utc) >= datetime.fromisoformat(pending["publish_at"]) - timedelta(minutes=1):
            publish(pending)  # on failure this moves it to the next slot
        else:
            log(f"Approved draft is scheduled for {pending['publish_at']}")
        return
    # Awaiting review at post time.
    if cfg.get("auto_post_if_not_reviewed"):
        telegram.send_message(f"You didn't review {describe(pending)}, so I'm posting it as planned.")
        publish(pending)
        return
    buttons = [telegram.review_buttons(pending["id"])[0],
               [{"text": "Post now", "callback_data": f"postnow:{pending['id']}"}]]
    if later_today:
        nxt = later_today[0].strftime('%-I:%M %p').lower()
        telegram.send_message(f"It's post time but {describe(pending)} isn't approved yet. "
                              f"Approve it and it goes live at {nxt} IST instead.", buttons=buttons)
    else:
        first = post_times(cfg)[0]
        first_txt = now.replace(hour=first[0], minute=first[1]).strftime('%-I:%M %p').lower()
        telegram.send_message(f"{describe(pending)} wasn't approved, so nothing went live today. "
                              f"Approve it and it goes live tomorrow at {first_txt} IST, or tap Post now.",
                              buttons=buttons)


def post_now_cmd():
    """Manual 'Go ahead and post': pick up any Telegram taps first, then
    publish the approved draft straight away, whatever the time."""
    before = (load_state().get("pending") or {}).get("id")
    check()
    state = load_state()
    pending = state.get("pending")
    if not pending:
        if before and any(h.get("id") == before and h.get("status") == "posted" for h in load_history()):
            return  # check() already posted it (it was due) and sent the link
        telegram.send_message("Go ahead and post: there's no draft waiting, so there's nothing to post. Send /new to make one.")
        log("Nothing to post")
        return
    if pending.get("status") != "approved":
        telegram.send_message(f"Go ahead and post: {describe(pending)} isn't approved yet. Tap Approve in Telegram, "
                              "then run this again.", buttons=telegram.review_buttons(pending["id"]))
        log("Draft not approved")
        return
    telegram.send_message(f"Posting {describe(pending)} now...")
    publish(pending)


def rerender(draft: dict):
    """Same words, fresh slides: useful after changing the template or fonts."""
    cfg, state = load_config(), load_state()
    draft = state["pending"]
    new_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    while (POSTS / new_id).exists() or new_id == draft["id"]:
        new_id += "b"
    closing = None
    if cfg.get("closing_slide", {}).get("enabled"):
        closing = cfg["closing_slide"]["text"].format(handle=cfg.get("instagram_handle", ""))
    paths = render_post(draft["post"], draft["theme"], draft["tag"], POSTS / new_id, closing=closing)
    remove_draft_files(draft)
    draft["id"] = new_id
    draft["images"] = [str(p.relative_to(ROOT)) for p in paths]
    save_state(state)
    full = {**draft, "images": [str(ROOT / p) for p in draft["images"]]}
    if draft.get("status") == "approved":
        telegram.send_album(full["images"])
        telegram.send_message(f"Redrawn. {describe(draft)} is still scheduled for {slot_text(draft['publish_at'])}.",
                              buttons=telegram.scheduled_buttons(new_id))
    else:
        telegram.send_review(full)


def keep_topic(draft: dict):
    """Make sure a draft's topic stays first in line (it may have come from
    /new or the writer rather than the topic list)."""
    if draft.get("backlog_line") and draft["backlog_line"] in topic_backlog():
        return
    topic = draft.get("topic") or draft.get("post", {}).get("topic")
    if not topic:
        return
    line = f"[{draft['series']}] {topic}" if draft.get("series") and draft["series"] != "MarketingTip" else topic
    if line not in topic_backlog():
        add_topic_to_backlog(line, front=True)


def skip(draft: dict):
    """Skip sets this version aside; the topic stays first in line so the
    next draft is a fresh take on it. Nothing is lost until it's live."""
    state, history = load_state(), load_history()
    history.append({**_hist(draft), "status": "skipped"})
    keep_topic(draft)
    state["pending"] = None
    remove_draft_files(draft)
    save_state(state)
    save_history(history)
    telegram.send_message(f"Set aside this version of {describe(draft)}. The topic stays first in line, so the next "
                          "draft (tomorrow morning, or now with /new) will be a fresh take on it.\n"
                          "To remove this topic for good instead, send /drop.")


def drop(pending: dict):
    state, history = load_state(), load_history()
    if pending:
        line = pending.get("backlog_line")
        if line:
            remove_topic_from_backlog(line)
        history.append({**_hist(pending), "status": "dropped"})
        state["pending"] = None
        remove_draft_files(pending)
        save_state(state)
        save_history(history)
        telegram.send_message(f"Dropped the topic of {describe(pending)} for good. Send /new for a draft on the next topic.")
        return
    backlog = topic_backlog()
    if not backlog:
        telegram.send_message("There is nothing to drop.")
        return
    remove_topic_from_backlog(backlog[0])
    history.append({"status": "dropped", "topic": backlog[0], "at": now_iso()})
    save_history(history)
    telegram.send_message(f"Dropped the next topic for good: {html.escape(backlog[0])}")


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
