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
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests

from notify import send_email
from translate import translate_metadata

IG_API = "https://graph.instagram.com/v23.0"
_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_HERE, "state.json")
LOG_FILE = os.path.join(_HERE, "sync_log.jsonl")
MAX_STATE_IDS = 500


def append_log(entry):
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


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


def download_video(url, dest, attempts=3):
    # Instagram's CDN occasionally resets long downloads mid-stream; retry a
    # couple of times with a pause before declaring failure.
    for attempt in range(1, attempts + 1):
        try:
            with requests.get(url, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        f.write(chunk)
            return
        except (requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.Timeout) as e:
            if attempt == attempts:
                raise
            print(f"Download attempt {attempt} failed ({e}); retrying...")
            time.sleep(10 * attempt)


def fetch_video(reel, dest):
    """Download a reel's video to dest.

    Uses the API's media_url when present. Reels with copyrighted audio come
    back without one; for those, download the public permalink with yt-dlp
    (verified to work anonymously from GitHub Actions runners).
    """
    if reel.get("media_url"):
        download_video(reel["media_url"], dest)
        return
    permalink = reel.get("permalink")
    if not permalink:
        raise RuntimeError(f"Reel {reel['id']} has neither media_url nor permalink.")
    print(f"Reel {reel['id']} has no media_url (copyrighted audio); "
          f"falling back to yt-dlp: {permalink}")
    subprocess.run(
        ["yt-dlp", "--no-progress", "--force-overwrites", "-f", "b",
         "-o", dest, permalink],
        check=True,
        timeout=600,
    )
    if not (os.path.exists(dest) and os.path.getsize(dest)):
        raise RuntimeError(f"yt-dlp produced no video for {permalink}")


def upload_to_youtube(access_token, video_path, title, description, en=None):
    """Upload a video. `en` is an optional {"title", "description"} dict attached
    as an English localization (default metadata stays Turkish)."""
    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "categoryId": "15",  # Pets & Animals
            "defaultLanguage": "tr",
            "defaultAudioLanguage": "tr",
        },
        "status": {
            "privacyStatus": os.environ.get("PRIVACY_STATUS", "public"),
            "selfDeclaredMadeForKids": False,
        },
    }
    parts = "snippet,status"
    if en:
        metadata["localizations"] = {
            "en": {"title": en["title"], "description": en["description"]},
        }
        parts = "snippet,status,localizations"

    def init_upload(md, part):
        return requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos",
            params={"uploadType": "resumable", "part": part},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/mp4",
            },
            json=md,
            timeout=60,
        )

    size = os.path.getsize(video_path)
    init = init_upload(metadata, parts)
    if init.status_code != 200 and en:
        # A bad localization payload must never block the upload itself.
        print(f"Upload init with localization failed {init.status_code}: "
              f"{init.text[:300]} — retrying without localization.")
        metadata.pop("localizations", None)
        init = init_upload(metadata, "snippet,status")
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
    if not os.environ.get("IG_ACCESS_TOKEN"):
        print("IG_ACCESS_TOKEN not configured yet; skipping run.")
        return 0

    media = fetch_recent_media(os.environ["IG_ACCESS_TOKEN"])
    reels = [m for m in media if is_reel(m)]
    print(f"Instagram OK: {len(media)} recent posts visible, {len(reels)} are reels.")

    yt_missing = [k for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
                  if not os.environ.get(k)]
    if yt_missing:
        print(f"YouTube credentials not configured yet ({', '.join(yt_missing)}); "
              "stopping after the Instagram check.")
        return 0

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

    uploaded = []
    for reel in new_reels[:max_per_run]:
        caption = reel.get("caption", "") or ""
        title = build_title(caption, reel.get("timestamp", ""))
        print(f"Uploading reel {reel['id']} ({reel.get('permalink')}) as: {title}")
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
            if reel.get("media_url"):
                # Transient CDN failures propagate and fail the run so the
                # 5-minute schedule retries; the reel is not marked synced.
                download_video(reel["media_url"], tmp.name)
            else:
                # No media_url means the yt-dlp fallback. If even that fails,
                # skip the reel with a warning email instead of retrying (and
                # emailing) every 5 minutes forever.
                try:
                    fetch_video(reel, tmp.name)
                except Exception as e:
                    print(f"yt-dlp fallback failed for {reel.get('permalink')}: {e}")
                    state["synced"].append(reel["id"])
                    save_state(state)
                    append_log({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "reel_id": reel["id"],
                        "title": title,
                        "permalink": reel.get("permalink"),
                        "youtube_url": None,
                        "skipped": f"no media_url; yt-dlp failed: {e}",
                    })
                    send_email(
                        "⚠️ Reel YouTube'a otomatik taşınamadı",
                        "Bu reel'in videosu indirilemedi (media_url yok ve "
                        f"yt-dlp da başarısız oldu):\n\n  {reel.get('permalink')}\n\n"
                        f"Hata: {e}\n\n"
                        "Manuel taşımak için 'Migrate a reel' workflow'unu "
                        "video_url girdisi (yt-dlp -g çıktısı) ve force=true "
                        "ile çalıştır.",
                    )
                    continue
            en = translate_metadata(title, caption)
            video_id = upload_to_youtube(yt_token, tmp.name, title, caption, en=en)
        youtube_url = f"https://youtube.com/shorts/{video_id}"
        print(f"  -> {youtube_url}")
        state["synced"].append(reel["id"])
        save_state(state)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "reel_id": reel["id"],
            "title": title,
            "permalink": reel.get("permalink"),
            "youtube_url": youtube_url,
        }
        append_log(entry)
        uploaded.append(entry)

    save_state(state)

    if uploaded:
        lines = ["Yeni reel(ler) YouTube'a yüklendi:\n"]
        for e in uploaded:
            lines.append(f"• {e['title']}")
            lines.append(f"  YouTube:   {e['youtube_url']}")
            lines.append(f"  Instagram: {e['permalink']}\n")
        send_email(
            f"✅ {len(uploaded)} yeni reel YouTube'a yüklendi",
            "\n".join(lines),
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
