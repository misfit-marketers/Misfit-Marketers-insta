"""Publishes a carousel through Instagram's official API (Instagram Login).

Flow: one container per image -> one CAROUSEL container -> wait until it
is processed -> media_publish. Images must be JPEGs at public URLs."""
import time

import requests

from .common import DRY_RUN, env

BASE = "https://graph.instagram.com"


class InstagramError(RuntimeError):
    pass


def _req(method: str, path: str, version: str, **params):
    params["access_token"] = env("IG_ACCESS_TOKEN")
    url = f"{BASE}/{version}/{path}" if version else f"{BASE}/{path}"
    r = requests.request(method, url, params=params if method == "GET" else None,
                         data=params if method != "GET" else None, timeout=90)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:300]}
    if r.status_code != 200 or "error" in body:
        raise InstagramError(f"{method} {path}: {body.get('error', body)}")
    return body


def whoami(version: str) -> dict:
    return _req("GET", "me", version, fields="user_id,username")


def publish_carousel(image_urls: list, caption: str, version: str, log=print) -> dict:
    if DRY_RUN:
        log(f"[dry-run] would publish {len(image_urls)} images to Instagram")
        return {"id": "dry-run", "permalink": "https://instagram.com/p/dry-run"}
    if not 2 <= len(image_urls) <= 10:
        raise InstagramError(f"A carousel needs 2 to 10 images, got {len(image_urls)}")

    me = whoami(version)
    ig_id = me.get("user_id") or me.get("id")

    children = []
    for url in image_urls:
        c = _req("POST", f"{ig_id}/media", version, image_url=url, is_carousel_item="true")
        children.append(c["id"])

    for cid in children:
        _wait_until_ready(cid, version)

    carousel = _req("POST", f"{ig_id}/media", version, media_type="CAROUSEL",
                    children=",".join(children), caption=caption)
    _wait_until_ready(carousel["id"], version)

    published = _req("POST", f"{ig_id}/media_publish", version, creation_id=carousel["id"])
    media_id = published["id"]
    try:
        info = _req("GET", media_id, version, fields="permalink")
        permalink = info.get("permalink", "")
    except InstagramError:
        permalink = ""
    return {"id": media_id, "permalink": permalink}


def publish_reel(video_url: str, caption: str, version: str, log=print, share_to_feed: bool = True) -> dict:
    """A Reel from a public MP4 URL. Instagram downloads and processes the video
    first, which can take a minute or two."""
    if DRY_RUN:
        log(f"[dry-run] would publish a Reel from {video_url}")
        return {"id": "dry-run-reel", "permalink": "https://instagram.com/reel/dry-run"}
    me = whoami(version)
    ig_id = me.get("user_id") or me.get("id")
    c = _req("POST", f"{ig_id}/media", version, media_type="REELS", video_url=video_url, caption=caption,
             share_to_feed="true" if share_to_feed else "false")
    _wait_until_ready(c["id"], version, timeout_s=600)
    published = _req("POST", f"{ig_id}/media_publish", version, creation_id=c["id"])
    media_id = published["id"]
    try:
        permalink = _req("GET", media_id, version, fields="permalink").get("permalink", "")
    except InstagramError:
        permalink = ""
    return {"id": media_id, "permalink": permalink}


def _wait_until_ready(container_id: str, version: str, timeout_s: int = 180):
    start = time.time()
    while time.time() - start < timeout_s:
        status = _req("GET", container_id, version, fields="status_code,status").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise InstagramError(f"Container {container_id} failed with status {status}")
        time.sleep(5)
    raise InstagramError(f"Container {container_id} was not ready after {timeout_s}s")


def refresh_token() -> dict:
    """Long-lived tokens last 60 days; refreshing returns a fresh 60-day token."""
    r = requests.get(f"{BASE}/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": env("IG_ACCESS_TOKEN")},
                     timeout=60)
    body = r.json()
    if r.status_code != 200 or "access_token" not in body:
        raise InstagramError(f"Token refresh failed: {body}")
    return body
