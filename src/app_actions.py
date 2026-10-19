"""Actions sent from the MISFIT app (through the 'App action' workflow).

The app starts the workflow with:
  APP_ACTION   what to do (see ACTIONS below)
  APP_DRAFT    the draft id, for draft actions
  APP_TEXT     extra input: feedback text, or JSON for edits and topics
  APP_REQUEST  a unique id the app uses to follow the request

Every request is recorded in state.json under "app_requests", with a plain
message the app shows when it's done. Re-sending the same request id (the app
does that if GitHub drops a run) does nothing the second time.
"""
import json
import os
from datetime import datetime, timedelta

from . import main as m
from . import telegram, writer
from .common import (add_topic_to_backlog, clean_text, load_config, load_state, now_iso, remove_topic_from_backlog,
                     save_state, set_backlog, topic_backlog)


class Refused(Exception):
    """The request can't be done (for example the draft is gone). Not a crash."""


def _draft(draft_id: str) -> dict:
    d = m.find(load_state(), draft_id)
    if not d:
        raise Refused("That draft isn't there any more. It may have been posted, skipped or replaced "
                      "in the meantime. Pull down to refresh.")
    return d


def _json(text: str) -> dict:
    try:
        data = json.loads(text or "{}")
    except ValueError:
        raise Refused("The app sent something I couldn't read. Please try again.")
    if not isinstance(data, dict):
        raise Refused("The app sent something I couldn't read. Please try again.")
    return data


def _name(d: dict) -> str:
    return m.plain(m.describe(d))


# ------------------------------------------------------------------ draft actions

def a_approve(draft_id, text):
    _draft(draft_id)
    m.approve(draft_id)
    d = _draft(draft_id)
    return draft_id, f"Approved. {_name(d)} goes live at {m.when_text(d['publish_at'])}."


def a_unapprove(draft_id, text):
    d = _draft(draft_id)
    state = load_state()
    d = m.find(state, draft_id)
    d["status"], d["publish_at"], d["note"] = "awaiting", None, ""
    save_state(state)
    return draft_id, f"{_name(d)} is back to waiting for review. It won't go live until you approve it again."


def a_postnow(draft_id, text):
    d = _draft(draft_id)
    start = len(telegram.OUTBOX)
    if m.publish(draft_id):
        from .common import load_history
        h = next((h for h in reversed(load_history()) if h.get("id") == draft_id and h.get("status") == "posted"), {})
        return None, f"Posted {_name(d)} to Instagram. {h.get('permalink', '')}".strip()
    said = [m.plain(t) for t in telegram.OUTBOX[start:]]
    raise Refused(said[-1] if said else "Instagram didn't accept it. It will try again automatically.")


def a_skip(draft_id, text):
    d = _draft(draft_id)
    m.skip(draft_id)
    return None, (f"Set aside this version of {_name(d)}. Its topic stays first in line, and its slot is free: "
                  "tap Write a draft to fill it.")


def a_drop(draft_id, text):
    d = _draft(draft_id)
    m.drop(draft_id)
    return None, f"Removed the topic of {_name(d)} for good. Its slot is free: tap Write a draft to fill it."


def a_redo(draft_id, text):
    d = _draft(draft_id)
    feedback = clean_text(text.strip()) or None
    new = m.redo(draft_id, feedback=feedback)
    how = "with your feedback" if feedback else "as a fresh take"
    return new["id"], f"Rewrote {_name(d)} {how}. Have a look and approve it when you're happy."


def a_edit(draft_id, text):
    d = _draft(draft_id)
    data = _json(text)
    cfg = load_config()
    post = dict(d["post"])
    for k in ("hook", "slides", "takeaway", "caption", "hashtags"):
        if k in data:
            post[k] = data[k]
    if isinstance(post.get("hashtags"), str):
        post["hashtags"] = post["hashtags"].split()
    try:
        post = writer.validate(post, int(cfg.get("max_body_slides", 7)))
    except ValueError as e:
        raise Refused(f"Couldn't save that: {e}. A carousel needs a hook, at least 2 slides, a takeaway and a caption.")
    state = load_state()
    d = m.find(state, draft_id)
    d["post"], d["caption"] = post, m.build_caption(post)
    save_state(state)
    new_id = m.rerender(draft_id, reason="Edited") or draft_id
    d = m.find(load_state(), new_id)
    return new_id, "Saved your edits and redrew the slides." + _reapprove_tail(d)


def _reapprove_tail(d) -> str:
    if d and d.get("unapproved_by_change"):
        return " It was approved before, so it needs your approval again."
    return " Approve it when you're happy."


def a_postpone(draft_id, text):
    d = _draft(draft_id)
    cfg, state = load_config(), load_state()
    d = m.find(state, draft_id)
    after = datetime.fromisoformat(d["slot_at"]) + timedelta(minutes=1)
    new_slot = m.next_free_same_slot(cfg, state["drafts"], d, after)
    d["slot_at"], d["reminded"] = m.iso(new_slot), False
    if d["status"] == "approved":
        d["publish_at"] = d["slot_at"]
    d["note"] = "Postponed from the app."
    save_state(state)
    return draft_id, f"Moved {_name(d)} to {m.when_text(d['slot_at'])}."


def a_new(draft_id, text):
    data = _json(text)
    cfg, state = load_config(), load_state()
    free = m.free_slots(cfg, state["drafts"], m.now_utc())
    want = data.get("slot")
    if want:
        free = [f for f in free if f[0] == want] or free
    if not free:
        raise Refused("Every upcoming slot already has a draft. Skip one first to make room.")
    series = data.get("series") if data.get("series") in cfg["series"] else None
    idea = clean_text((data.get("idea") or "").strip())
    topic = (f"[{series}] {idea}" if series else idea) if idea else None
    name, slot_at = free[0]
    new = m.write_draft(cfg, name, slot_at, series=series, topic=topic)
    return new["id"], f"New draft ready: {_name(new)} for {m.when_text(new['slot_at'])}. Have a look and approve it."


# ------------------------------------------------------------------ topic list

def _line(series: str, idea: str, cfg: dict) -> str:
    idea = clean_text(idea.strip())
    if not idea:
        raise Refused("The topic is empty.")
    return idea if not series or series == m.default_series(cfg) else f"[{series}] {idea}"


def a_topic_add(draft_id, text):
    data, cfg = _json(text), load_config()
    line = _line(data.get("series"), data.get("idea", ""), cfg)
    if line in topic_backlog():
        raise Refused("That topic is already on the list.")
    add_topic_to_backlog(line, front=bool(data.get("front")))
    return None, f"Added to the {'front' if data.get('front') else 'end'} of the topic list: {line}"


def a_topic_remove(draft_id, text):
    line = _json(text).get("line", "")
    if line not in topic_backlog():
        raise Refused("That topic isn't on the list any more.")
    remove_topic_from_backlog(line)
    return None, f"Removed from the topic list: {line}"


def a_topic_edit(draft_id, text):
    data, cfg = _json(text), load_config()
    old = data.get("line", "")
    lines = topic_backlog()
    if old not in lines:
        raise Refused("That topic isn't on the list any more.")
    new = _line(data.get("series"), data.get("idea", ""), cfg)
    lines[lines.index(old)] = new
    set_backlog(lines)
    state = load_state()
    for d in state["drafts"]:  # a draft written from this line still points at it
        if d.get("backlog_line") == old:
            d["backlog_line"] = new
    save_state(state)
    return None, f"Updated the topic: {new}"


def a_topic_order(draft_id, text):
    wanted = [str(x) for x in _json(text).get("order", [])]
    current = topic_backlog()
    keep = [t for t in wanted if t in current]
    # Anything added since the app loaded the list keeps its place at the end.
    rest = [t for t in current if t not in keep]
    set_backlog(keep + rest)
    return None, "Saved the new topic order."


# ------------------------------------------------------------------ slide styles

STYLE_WORDS = {"poster": "Poster", "editorial": "Editorial", "essay": "Essay", "notebook": "Notebook",
               "classic": "Classic"}


def _styles_on(cfg):
    if not (cfg.get("design") or {}).get("enabled"):
        raise Refused("Slide styles are switched off in config.json (design.enabled is false).")


def _series_style(cfg, series):
    return ((cfg["design"].get("styles") or {}).get(series) or {}).get("style", "classic")


def a_redesign(draft_id, text):
    """Same words, new art direction. Optional: a style for just this draft
    and a note for the AI designer (for example 'fewer emoji')."""
    from .render import STYLE_NAMES, style_for
    _draft(draft_id)
    data = _json(text)
    cfg = load_config()
    _styles_on(cfg)
    style = str(data.get("style") or "").strip().lower()
    if style and style not in STYLE_NAMES:
        raise Refused("I don't know that style. Pick Poster, Editorial, Essay, Notebook or Classic.")
    note = clean_text(str(data.get("note") or ""))[:300]
    state = load_state()
    d = m.find(state, draft_id)
    d["style"] = style if style and style != _series_style(cfg, d["series"]) else None
    d["post"].pop("design", None)
    if note:
        d["post"]["design_note"] = note
    else:
        d["post"].pop("design_note", None)
    save_state(state)
    new_id = m.rerender(draft_id, reason="Redesigned") or draft_id
    d = m.find(load_state(), new_id)
    if not d:
        raise Refused("The draft changed while I was redrawing it. Pull down to refresh.")
    used = (style_for(cfg, d["series"], d.get("style")) or ("classic",))[0]
    post = d["post"]
    if post.get("design_error"):
        msg = f"Couldn't draw {_name(d)} in the new style, so it kept the classic look. ({post['design_error'][:160]})"
    else:
        msg = f"Redesigned {_name(d)} in the {STYLE_WORDS[used]} style."
        if (post.get("design") or {}).get("source") == "fallback":
            msg += " The AI designer didn't answer, so it used plain highlights. Try Redesign again in a minute."
    return new_id, msg + _reapprove_tail(d)


def a_styles_set(draft_id, text):
    """Choose the style (and highlight colour) a series uses from now on.
    Saved in state.json; config.json keeps the original defaults."""
    import re
    from .render import STYLE_NAMES
    data = _json(text)
    cfg = load_config()
    _styles_on(cfg)
    series = data.get("series")
    if series not in cfg["series"]:
        raise Refused("I don't know that series.")
    state = load_state()
    picks = state.setdefault("design_styles", {})
    if data.get("reset"):
        picks.pop(series, None)
    else:
        style = str(data.get("style") or "").strip().lower()
        if style not in STYLE_NAMES:
            raise Refused("I don't know that style. Pick Poster, Editorial, Essay, Notebook or Classic.")
        accent = str(data.get("accent") or "").strip()
        if accent and not re.fullmatch(r"#[0-9A-Fa-f]{6}", accent):
            raise Refused("That colour doesn't look right. Use a hex colour like #FF4D2E.")
        picks[series] = {"style": style, **({"accent": accent.upper()} if accent else {})}
    save_state(state)
    cfg = load_config()
    now = _series_style(cfg, series)
    msg = f"{series} now uses the {STYLE_WORDS.get(now, now)} style" + (" (back to the default)." if data.get("reset") else ".")
    redrawn = 0
    if data.get("redraw"):
        for d in [x for x in load_state()["drafts"] if x["series"] == series and not x.get("style")]:
            if m.rerender(d["id"], reason="Redrawn in the new look"):
                redrawn += 1
    if redrawn:
        msg += f" Redrew {redrawn} waiting draft{'s' if redrawn != 1 else ''}."
    elif data.get("redraw"):
        msg += " No waiting drafts in this series to redraw."
    else:
        msg += " New drafts will use it."
    return "", msg



def a_stats(draft_id, text):
    """Fetch the latest numbers from Instagram now."""
    from . import stats
    stats.refresh(load_config(), m.log, force=True)
    acc = load_state().get("account") or {}
    tail = f" {acc['followers']} followers." if acc.get("followers") is not None else ""
    return "", "Fetched the latest numbers from Instagram." + tail


ACTIONS = {
    "approve": a_approve, "unapprove": a_unapprove, "postnow": a_postnow, "skip": a_skip, "drop": a_drop,
    "redo": a_redo, "edit": a_edit, "postpone": a_postpone, "new": a_new,
    "topic_add": a_topic_add, "topic_remove": a_topic_remove, "topic_edit": a_topic_edit,
    "topic_order": a_topic_order, "redesign": a_redesign, "styles_set": a_styles_set, "stats": a_stats,
}


def _record(rid: str, action: str, draft_id: str, ok: bool, message: str, new_id=None):
    state = load_state()
    state["app_requests"] = (state.get("app_requests", []) + [{
        "id": rid, "action": action, "draft_id": draft_id, "new_draft_id": new_id,
        "ok": ok, "message": message[:900], "at": now_iso()}])[-60:]
    save_state(state)


def app_cmd():
    action = os.environ.get("APP_ACTION", "").strip()
    draft_id = os.environ.get("APP_DRAFT", "").strip()
    text = os.environ.get("APP_TEXT", "")
    rid = os.environ.get("APP_REQUEST", "").strip() or now_iso()
    m.log(f"App request {rid}: {action} {draft_id}")
    if any(r.get("id") == rid for r in load_state().get("app_requests", [])):
        m.log("Already done earlier; nothing to do.")
        return
    if action not in ACTIONS:
        _record(rid, action, draft_id, False, f"Unknown action '{action}'.")
        return
    mark = len(telegram.EVENTS)  # the app's own summary replaces the bot's chatter for this action
    try:
        new_id, message = ACTIONS[action](draft_id, text)
    except Refused as e:
        del telegram.EVENTS[mark:]
        _record(rid, action, draft_id, False, str(e))
        telegram.event(f"App: {action} not done. {e}")
        return
    except Exception as e:
        _record(rid, action, draft_id, False, f"Something went wrong: {str(e)[-500:]}")
        raise
    del telegram.EVENTS[mark:]
    _record(rid, action, draft_id, True, message, new_id)
    telegram.event(f"App: {message}")
    m.process_due(notify=True)  # post anything that's now due
