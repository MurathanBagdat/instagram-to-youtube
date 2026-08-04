#!/usr/bin/env python3
"""Manually migrate one Instagram reel (by URL) to YouTube.

Usage (normally run via the "Migrate a reel" GitHub Actions workflow):

    REEL_URL=https://www.instagram.com/reel/XXXX/ python migrate.py

Looks the reel up in the account's own media via the Instagram API, uploads
it to YouTube, and records it in state.json + sync_log.jsonl so the regular
sync never uploads it twice. Set FORCE=1 to re-upload a reel that is already
marked as synced.
"""

import os
import re
import sys
import tempfile
from datetime import datetime, timezone

import requests

from notify import send_email
from sync import (
    IG_API,
    append_log,
    build_title,
    download_video,
    get_youtube_access_token,
    load_state,
    save_state,
    upload_to_youtube,
)


def extract_shortcode(url):
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


def find_media_by_shortcode(token, shortcode, max_pages=40):
    fields = "id,caption,media_type,media_product_type,media_url,permalink,timestamp"
    url = f"{IG_API}/me/media"
    params = {"fields": fields, "limit": 50, "access_token": token}
    for _ in range(max_pages):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Instagram API error {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        for item in payload.get("data", []):
            code = extract_shortcode(item.get("permalink") or "")
            if code == shortcode:
                return item
        url = payload.get("paging", {}).get("next")
        params = None  # the "next" URL already carries all query params
        if not url:
            break
    return None


def main():
    reel_url = os.environ.get("REEL_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    shortcode = extract_shortcode(reel_url.strip())
    if not shortcode:
        print(f"Could not parse an Instagram reel URL from: {reel_url!r}")
        return 1

    token = os.environ["IG_ACCESS_TOKEN"]
    print(f"Looking for reel {shortcode} in the account's media...")
    media = find_media_by_shortcode(token, shortcode)
    if media is None:
        print("Reel not found in this Instagram account's media. "
              "Only reels from the connected account can be migrated.")
        return 1

    print(f"Found: {media['id']} ({media.get('permalink')})")
    if media.get("media_type") != "VIDEO":
        print(f"This post is not a video (media_type={media.get('media_type')}).")
        return 1
    # Reels with copyrighted audio come back without a media_url; the workflow's
    # video_url input lets the caller supply a direct download URL (e.g. from
    # `yt-dlp -g <reel url>`) so those can still be migrated.
    video_url = media.get("media_url") or os.environ.get("VIDEO_URL")
    if not video_url:
        print("Instagram did not provide a downloadable media_url for this reel "
              "(this can happen with copyrighted audio). Re-run the workflow with "
              "the video_url input set to a direct video URL (yt-dlp -g).")
        return 1

    state = load_state() or {"synced": []}
    if media["id"] in state["synced"] and os.environ.get("FORCE") != "1":
        print("This reel is already marked as synced to YouTube. "
              "Re-run with FORCE=1 to upload it again anyway.")
        return 0

    caption = media.get("caption", "") or ""
    title = build_title(caption, media.get("timestamp", ""))
    print(f"Uploading as: {title}")
    yt_token = get_youtube_access_token()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
        download_video(video_url, tmp.name)
        video_id = upload_to_youtube(yt_token, tmp.name, title, caption)
    youtube_url = f"https://youtube.com/shorts/{video_id}"
    print(f"  -> {youtube_url}")

    if media["id"] not in state["synced"]:
        state["synced"].append(media["id"])
    save_state(state)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "reel_id": media["id"],
        "title": title,
        "permalink": media.get("permalink"),
        "youtube_url": youtube_url,
        "manual": True,
    }
    append_log(entry)

    send_email(
        "✅ Reel manuel olarak YouTube'a taşındı",
        f"İstediğin reel YouTube'a yüklendi:\n\n• {title}\n"
        f"  YouTube:   {youtube_url}\n  Instagram: {media.get('permalink')}\n",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
