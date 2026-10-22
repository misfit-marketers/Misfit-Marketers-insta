"""Fetches how each live post is doing (views, reach, likes, comments, saves,
shares) and the account's follower count, so the app's History can show the
numbers without opening Instagram.

Runs from the regular check at most every 2 hours. Posts from the last 3 days
are refreshed every time; older ones (up to 60 days) about once a day.
Needs the access token to include the instagram_business_manage_insights
permission. Without it, likes and comments still come through and the app
says what's missing."""
from datetime import datetime, timedelta, timezone

from . import instagram
from .common import DRY_RUN, load_history, load_state, now_iso, save_history, save_state

METRICS = ["views", "reach", "likes", "comments", "saved", "shares", "total_interactions"]
EXTRA_METRICS = ["ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "profile_visits", "follows",
                 "profile_activity"]
EVERY = timedelta(hours=2)


def _now():
    return datetime.now(timezone.utc)


def _when(s):
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def media_stats(media_id: str, version: str) -> dict:
    out, note = {}, ""
    sets = [METRICS, ["reach", "likes", "comments", "saved", "shares"], ["reach"]]
    for k, metrics in enumerate(sets):
        try:
            body = instagram._req("GET", f"{media_id}/insights", version, metric=",".join(metrics))
            for item in body.get("data", []):
                vals = item.get("values") or [{}]
                out[item["name"]] = vals[0].get("value", (item.get("total_value") or {}).get("value"))
            note = ""
            break
        except instagram.InstagramError as e:
            msg = str(e)
            note = "insights permission missing" if ("permission" in msg.lower() or "(#10)" in msg) else msg[:160]
            if note == "insights permission missing":
                break  # a smaller set only helps when one metric isn't offered for this post type
    if note:
        try:  # basic counts work without the insights permission
            basic = instagram._req("GET", media_id, version, fields="like_count,comments_count")
            out["likes"], out["comments"] = basic.get("like_count"), basic.get("comments_count")
        except instagram.InstagramError:
            pass
        out["note"] = note
    # Extras: watch time for Reels; profile visits and follows for carousels.
    # Asked for one at a time, since Instagram offers different ones per format.
    for metric in EXTRA_METRICS:
        if note or metric in out:
            continue
        try:
            body = instagram._req("GET", f"{media_id}/insights", version, metric=metric)
            for item in body.get("data", []):
                vals = item.get("values") or [{}]
                out[item["name"]] = vals[0].get("value", (item.get("total_value") or {}).get("value"))
        except instagram.InstagramError:
            pass
    out["updated_at"] = now_iso()
    return out


def _total(item):
    tv = item.get("total_value") or {}
    res = {"total": tv.get("value")}
    for b in tv.get("breakdowns") or []:
        for r in b.get("results") or []:
            key = "_".join(str(x).lower() for x in r.get("dimension_values") or [])
            if key:
                res[key] = r.get("value")
    return res


def account_day(version: str) -> dict:
    """Yesterday-and-today account numbers: reach split into followers and
    non-followers, views, accounts engaged, profile link taps, follows."""
    out = {}
    asks = [("reach", {"breakdown": "follow_type"}), ("views", {}), ("accounts_engaged", {}),
            ("total_interactions", {}), ("profile_links_taps", {}), ("follows_and_unfollows", {"breakdown": "follow_type"})]
    for metric, extra in asks:
        try:
            body = instagram._req("GET", "me/insights", version, metric=metric, period="day",
                                  metric_type="total_value", **extra)
            for item in body.get("data", []):
                out[item["name"]] = _total(item)
        except instagram.InstagramError as e:
            out.setdefault("_errors", {})[metric] = str(e)[:160]
    return out


def demographics(version: str) -> dict:
    """Who follows the page (needs 100+ followers; Instagram's rule)."""
    out = {}
    for b in ("age", "gender", "city", "country"):
        try:
            body = instagram._req("GET", "me/insights", version, metric="follower_demographics", period="lifetime",
                                  timeframe="this_month", metric_type="total_value", breakdown=b)
            for item in body.get("data", []):
                out[b] = {k: v for k, v in _total(item).items() if k != "total"}
        except instagram.InstagramError as e:
            out.setdefault("_errors", {})[b] = str(e)[:160]
    return out


def refresh(cfg: dict, log=print, force: bool = False):
    if not (cfg.get("stats") or {}).get("enabled", True) or DRY_RUN:
        return
    state = load_state()
    last = _when(state.get("stats_checked_at"))
    if not force and last and _now() - last < EVERY:
        return
    version = cfg.get("instagram_api_version", "v25.0")
    history = load_history()
    changed = 0
    for h in history:
        if h.get("status") != "posted" or not h.get("ig_media_id"):
            continue
        posted = _when(h.get("posted_at"))
        if not posted or _now() - posted > timedelta(days=60):
            continue
        prev = _when((h.get("stats") or {}).get("updated_at"))
        young = _now() - posted < timedelta(days=3)
        if prev and not young and _now() - prev < timedelta(hours=22):
            continue
        h["stats"] = media_stats(h["ig_media_id"], version)
        if h.get("reel_media_id"):  # the Reel posted alongside the carousel
            h["reel_stats"] = media_stats(h["reel_media_id"], version)
        changed += 1
    if changed:
        save_history(history)
    state = load_state()
    try:
        me = instagram._req("GET", "me", version, fields="followers_count,media_count,username")
        state["account"] = {"followers": me.get("followers_count"), "posts": me.get("media_count"),
                            "username": me.get("username"), "updated_at": now_iso()}
    except instagram.InstagramError as e:
        log(f"Couldn't read the follower count: {str(e)[:200]}")
    # Account-level numbers: once a day is plenty (kept for 60 days).
    today = _now().date().isoformat()
    daily = state.get("account_daily") or {}
    if today not in daily or force:
        daily[today] = account_day(version)
        state["account_daily"] = dict(sorted(daily.items())[-60:])
    demo = state.get("audience") or {}
    if force or not demo.get("updated_at") or _now() - _when(demo["updated_at"]) > timedelta(days=7):
        state["audience"] = {**demographics(version), "updated_at": now_iso()}
    state["stats_checked_at"] = now_iso()
    save_state(state)
    log(f"Stats refreshed for {changed} post(s)")
