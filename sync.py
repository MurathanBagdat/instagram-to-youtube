#!/usr/bin/env python3
"""Sync new Instagram Reels from @chiathefrenchton to YouTube Shorts.

Runs on a schedule (GitHub Actions). Reads recent media via the official
Instagram API (Instagram Login flavor, graph.instagram.com), downloads any
reel not yet synced, and uploads it to YouTube via the Data API v3.

State (already-synced reel IDs) lives in state.json, committed back to the
repo by the workflow.

Required environment variables:
  IG_ACCESS_TOKEN   Instagram long-lived access token
  YT_CLIENT_ID      Google OAuth client ID (Desktop app)
  YT_CLIENT_SECRET  Google OAuth client secret
  YT_REFRESH_TOKEN  YouTube OAuth refresh token (from get_youtube_refresh_token.py)

Optional:
  PRIVACY_STATUS    public | unlisted | private  (default: public)
  MAX_PER_RUN       max uploads per run          (default: 3)
"""

import json
import os
import sys
import tempfile

import requests

IG_API = "https://graph.instagram.com/v23.0"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
MAX_STATE_IDS = 500


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return None


def save_state(state):
    state["synced"] = state["synced"][-MAX_STATE_IDS:]
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def fetch_recent_media(token):
    fields = "id,caption,media_type,media_product_type,media_url,permalink,timestamp"
    resp = requests.get(
        f"{IG_API}/me/media",
        params={"fields": fields, "limit": 15, "access_token": token},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Instagram API error {resp.status_code}: {resp.text[:500]}")
    return resp.json().get("data", [])


def is_reel(item):
    return item.get("media_type") == "VIDEO" and item.get("media_product_type") in (
        "REELS",
        "FEED",
    )


def build_title(caption, timestamp):
    first_line = (caption or "").strip().splitlines()
    title = first_line[0].strip() if first_line else ""
    if not title:
        title = f"Chia the Frenchton {timestamp[:10]}"
    title = title.replace("<", "").replace(">", "")
    if "#shorts" not in title.lower():
        suffix = " #Shorts"
        title = title[: 100 - len(suffix)].rstrip() + suffix
    else:
        title = title[:100]
    return title


def get_youtube_access_token():
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ["YT_CLIENT_ID"],
            "client_secret": os.environ["YT_CLIENT_SECRET"],
            "refresh_token": os.environ["YT_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"YouTube token refresh failed: {resp.text[:500]}")
    return resp.json()["access_token"]


def download_video(url, dest):
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                f.write(chunk)


def upload_to_youtube(access_token, video_path, title, description):
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "15",  # Pets & Animals
        },
        "status": {
            "privacyStatus": os.environ.get("PRIVACY_STATUS", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }
    size = os.path.getsize(video_path)
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        json=metadata,
        timeout=60,
    )
    if init.status_code != 200:
        raise RuntimeError(f"Upload init failed {init.status_code}: {init.text[:500]}")
    upload_url = init.headers["Location"]

    with open(video_path, "rb") as f:
        up = requests.put(
            upload_url,
            headers={"Content-Length": str(size), "Content-Type": "video/mp4"},
            data=f,
            timeout=600,
        )
    if up.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed {up.status_code}: {up.text[:500]}")
    return up.json()["id"]


def main():
    required = ["IG_ACCESS_TOKEN", "YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"Credentials not configured yet ({', '.join(missing)}); skipping run.")
        return 0

    media = fetch_recent_media(os.environ["IG_ACCESS_TOKEN"])
    reels = [m for m in media if is_reel(m)]
    state = load_state()

    if state is None:
        # First run: mark everything currently on the profile as already synced
        # so only reels posted from now on get uploaded.
        state = {"synced": [m["id"] for m in media]}
        save_state(state)
        print(f"Initialized state with {len(state['synced'])} existing posts. "
              "Only new reels will be synced from now on.")
        return 0

    synced = set(state["synced"])
    new_reels = [r for r in reels if r["id"] not in synced]
    if not new_reels:
        print("No new reels.")
        return 0

    new_reels.sort(key=lambda r: r.get("timestamp", ""))  # oldest first
    max_per_run = int(os.environ.get("MAX_PER_RUN", "3"))
    yt_token = get_youtube_access_token()

    for reel in new_reels[:max_per_run]:
        if not reel.get("media_url"):
            print(f"Reel {reel['id']} has no media_url; marking as skipped.")
            state["synced"].append(reel["id"])
            continue
        caption = reel.get("caption", "") or ""
        title = build_title(caption, reel.get("timestamp", ""))
        print(f"Uploading reel {reel['id']} ({reel.get('permalink')}) as: {title}")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
            download_video(reel["media_url"], tmp.name)
            video_id = upload_to_youtube(yt_token, tmp.name, title, caption)
        print(f"  -> https://youtube.com/shorts/{video_id}")
        state["synced"].append(reel["id"])
        save_state(state)

    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
