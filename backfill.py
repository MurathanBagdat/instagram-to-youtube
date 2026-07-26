#!/usr/bin/env python3
"""Daily historical backfill: migrate ONE reel per run, oldest first.

The anchor reel (ANCHOR_SHORTCODE) marks the start of the timeline. Every reel
posted AFTER it that has not actually been uploaded to YouTube yet is backlog;
each run migrates the oldest backlog reel and stops. Run daily at 09:00 TRT by
.github/workflows/backfill.yml until the backlog drains, after which every run
is a cheap no-op.

"Actually uploaded" is decided by sync_log.jsonl (the upload ledger), NOT by
state.json: the first-run state initialization marked the 15 then-current posts
as synced without uploading them, so state.json overstates what's on YouTube.
The ledger only lacks the very first test upload, which predates it — that one
id is hardcoded in PRE_LEDGER_UPLOADS.

Set DRY_RUN=1 to print what would be migrated without uploading anything.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

import requests

from migrate import extract_shortcode
from notify import send_email
from sync import (
    IG_API,
    LOG_FILE,
    append_log,
    build_title,
    download_video,
    get_youtube_access_token,
    is_reel,
    load_state,
    save_state,
    upload_to_youtube,
)

ANCHOR_SHORTCODE = "DZnBBwAI5Hl"  # migrated by hand; the timeline starts AFTER it
PRE_LEDGER_UPLOADS = {"18120514939893312"}  # first test upload, predates sync_log.jsonl
MAX_PAGES = 40


def collect_newer_than_anchor(token):
    """Walk the media feed (newest first) until the anchor reel appears;
    return everything seen before it, i.e. every post newer than the anchor."""
    fields = "id,caption,media_type,media_product_type,media_url,permalink,timestamp"
    url = f"{IG_API}/me/media"
    params = {"fields": fields, "limit": 50, "access_token": token}
    collected = []
    for _ in range(MAX_PAGES):
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Instagram API error {resp.status_code}: {resp.text[:500]}")
        payload = resp.json()
        for item in payload.get("data", []):
            if extract_shortcode(item.get("permalink") or "") == ANCHOR_SHORTCODE:
                return collected
            collected.append(item)
        url = payload.get("paging", {}).get("next")
        params = None  # the "next" URL already carries all query params
        if not url:
            break
    raise RuntimeError(
        f"Anchor reel {ANCHOR_SHORTCODE} not found in the account's media — "
        "refusing to guess the timeline start."
    )


def uploaded_ids():
    ids = set(PRE_LEDGER_UPLOADS)
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    ids.add(json.loads(line)["reel_id"])
    return ids


def main():
    dry_run = os.environ.get("DRY_RUN") == "1"
    token = os.environ["IG_ACCESS_TOKEN"]

    newer = collect_newer_than_anchor(token)
    done = uploaded_ids()
    backlog = sorted(
        (m for m in newer if is_reel(m) and m["id"] not in done),
        key=lambda m: m.get("timestamp", ""),
    )

    if not backlog:
        print("Backlog empty — every reel after the anchor is already on YouTube.")
        return 0

    print(f"Backlog: {len(backlog)} reel(s) waiting, oldest first:")
    for m in backlog[:5]:
        print(f"  {m.get('timestamp', '?')[:10]}  {m.get('permalink')}")
    if len(backlog) > 5:
        print(f"  ... and {len(backlog) - 5} more")

    if dry_run:
        print("DRY RUN — nothing uploaded.")
        return 0

    # Oldest reel with a downloadable media_url; ones without get a ledger
    # entry marking them skipped so they never block the queue.
    reel = None
    for candidate in backlog:
        if candidate.get("media_url"):
            reel = candidate
            break
        print(f"Reel {candidate['id']} ({candidate.get('permalink')}) has no media_url; "
              "recording as skipped.")
        append_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "reel_id": candidate["id"],
            "title": None,
            "permalink": candidate.get("permalink"),
            "youtube_url": None,
            "backfill": True,
            "skipped": "no media_url",
        })
    if reel is None:
        print("Every remaining backlog reel lacks a media_url; nothing to upload.")
        return 0

    caption = reel.get("caption", "") or ""
    title = build_title(caption, reel.get("timestamp", ""))
    print(f"Migrating {reel.get('permalink')} as: {title}")
    yt_token = get_youtube_access_token()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
        download_video(reel["media_url"], tmp.name)
        video_id = upload_to_youtube(yt_token, tmp.name, title, caption)
    youtube_url = f"https://youtube.com/shorts/{video_id}"
    print(f"  -> {youtube_url}")

    state = load_state() or {"synced": []}
    if reel["id"] not in state["synced"]:
        state["synced"].append(reel["id"])
    save_state(state)
    append_log({
        "ts": datetime.now(timezone.utc).isoformat(),
        "reel_id": reel["id"],
        "title": title,
        "permalink": reel.get("permalink"),
        "youtube_url": youtube_url,
        "backfill": True,
    })

    remaining = len(backlog) - 1
    send_email(
        "📦 Günlük arşiv taşıma: 1 reel YouTube'a yüklendi",
        f"Bugünün arşiv reel'i YouTube'a taşındı:\n\n• {title}\n"
        f"  YouTube:   {youtube_url}\n  Instagram: {reel.get('permalink')}\n"
        f"  Orijinal tarih: {reel.get('timestamp', '?')[:10]}\n\n"
        f"Kalan arşiv: {remaining} reel"
        + (f" (~{remaining} gün)" if remaining else " — arşiv tamamlandı! 🎉"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
