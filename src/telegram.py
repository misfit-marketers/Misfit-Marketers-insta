"""Tiny Telegram Bot API client: send the draft for review, read button taps."""
import html
import json
from pathlib import Path

import requests

from .common import DRY_RUN, env, load_config

# Everything the bot says is also collected here, so the app can show it:
# OUTBOX holds every message of this run, EVENTS the ones worth keeping in the
# app's activity feed. main.py saves EVENTS into state.json at the end of a run.
OUTBOX = []
EVENTS = []


def enabled() -> bool:
    """Telegram can be switched off in config.json ("telegram": {"enabled": false})
    without removing any code. The app keeps working either way."""
    return bool(load_config().get("telegram", {}).get("enabled", True))


def only_live() -> bool:
    """config.json "telegram": {"only_live": true} keeps Telegram quiet except for
    the message that a post has gone live. Drafts, reminders, replies and errors
    still show in the app (Activity and the bell)."""
    return bool(load_config().get("telegram", {}).get("only_live", False))


def event(text: str):
    EVENTS.append(text)


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{env('TELEGRAM_BOT_TOKEN')}/{method}"


def _post(method: str, **kwargs):
    if DRY_RUN:
        data = kwargs.get("data") or kwargs.get("json") or {}
        print(f"[dry-run] telegram.{method}: {str(data)[:200]}")
        return {}
    r = requests.post(_url(method), timeout=60, **kwargs)
    body = r.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {body}")
    return body.get("result")


def chat_id() -> str:
    return env("TELEGRAM_CHAT_ID")


def send_message(text: str, buttons=None, parse_html: bool = True, log_event: bool = True, live: bool = False):
    OUTBOX.append(text)
    if log_event:
        event(text)
    if not enabled() or (only_live() and not live):
        return {}
    payload = {"chat_id": chat_id(), "text": text[:4000], "disable_web_page_preview": True}
    if parse_html:
        payload["parse_mode"] = "HTML"
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return _post("sendMessage", json=payload)


def send_album(paths: list):
    """Sends up to 10 images as one album (uploaded directly, no public URL needed)."""
    if not enabled() or only_live():
        return []
    media, files = [], {}
    for i, p in enumerate(paths[:10]):
        key = f"img{i}"
        media.append({"type": "photo", "media": f"attach://{key}"})
        files[key] = (Path(p).name, open(p, "rb"), "image/jpeg")
    try:
        return _post("sendMediaGroup", data={"chat_id": chat_id(), "media": json.dumps(media)}, files=files)
    finally:
        for _, fh, _ in files.values():
            fh.close()


def get_updates(offset: int = None, limit: int = 100) -> list:
    """Without an offset this only peeks: Telegram keeps the updates until a
    later call confirms them with a higher offset."""
    if DRY_RUN:
        return []
    params = {"timeout": 0, "limit": limit, "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(_url("getUpdates"), params=params, timeout=60)
    body = r.json()
    if not body.get("ok"):
        desc = str(body.get("description", ""))
        if "webhook" in desc.lower():
            raise RuntimeError("Another service is connected to this Telegram bot (a webhook is set), so GitHub "
                               "cannot read your taps. Remove that connection, or make a new bot token in "
                               f"BotFather and put it in the TELEGRAM_BOT_TOKEN secret. Telegram said: {desc}")
        if "terminated by other getupdates" in desc.lower():
            raise RuntimeError("Another program is reading this bot's messages at the same time as GitHub. "
                               f"Telegram said: {desc}")
        raise RuntimeError(f"Telegram getUpdates failed: {body}")
    return body["result"]


def webhook_info() -> dict:
    """Shows whether some other service has taken over this bot."""
    if DRY_RUN:
        return {}
    r = requests.get(_url("getWebhookInfo"), timeout=30)
    body = r.json()
    return body.get("result", {}) if body.get("ok") else {"error": body}


def answer_callback(callback_id: str, text: str = ""):
    try:
        _post("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text[:190]})
    except Exception:
        pass  # old buttons can expire; harmless


def review_buttons(draft_id: str):
    return [[
        {"text": "Approve", "callback_data": f"approve:{draft_id}"},
        {"text": "Redo", "callback_data": f"redo:{draft_id}"},
        {"text": "Skip", "callback_data": f"skip:{draft_id}"},
    ]]


def scheduled_buttons(draft_id: str):
    return [[
        {"text": "Post now", "callback_data": f"postnow:{draft_id}"},
        {"text": "Redo", "callback_data": f"redo:{draft_id}"},
        {"text": "Skip", "callback_data": f"skip:{draft_id}"},
    ]]


def _ids(result) -> list:
    if isinstance(result, list):
        return [m.get("message_id") for m in result if isinstance(m, dict) and m.get("message_id")]
    if isinstance(result, dict) and result.get("message_id"):
        return [result["message_id"]]
    return []


def send_review(draft: dict, when: str = "") -> list:
    """Album of slides, then one message with caption, fact-check and buttons.
    Returns the Telegram message ids, so replies can be matched to this draft."""
    ids = _ids(send_album(draft["images"]))
    post = draft["post"]
    e = html.escape
    lines = []
    if when:
        lines.append(f"<b>For {e(when)}</b>")
    lines += [f"<b>{e(draft['tag'])}</b> · {e(post.get('principle', ''))}", ""]
    lines.append("<b>Caption</b>")
    lines.append(e(draft["caption"]))
    checks = post.get("fact_check") or []
    if checks:
        lines += ["", "<b>Fact check before approving</b>"]
        for c in checks[:8]:
            conf = c.get("confidence", "?")
            lines.append(f"• {e(c.get('claim', ''))}\n   <i>{e(c.get('source', 'no source given'))}</i> [{e(conf)}]")
    lines += ["", "<b>Approve</b> schedules it for the time above. I'll confirm the exact posting time within "
              "about 10 minutes of your tap (the button may keep loading until then; that's normal).",
              "To change it, <b>reply to this message</b> with your feedback (for example: <i>make the hook punchier</i>)."]
    event(f"New draft ready for review: {draft['tag']} ({post.get('principle', '')})"
          + (f" for {when}" if when else ""))
    if not enabled() or only_live():
        return ids
    ids += _ids(send_message("\n".join(lines), buttons=review_buttons(draft["id"]), log_event=False))
    return ids
