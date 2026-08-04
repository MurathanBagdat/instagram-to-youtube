#!/usr/bin/env python3
"""One-off: add English localizations to already-uploaded channel videos.

Walks the channel's uploads playlist, finds videos without an "en"
localization, translates their Turkish title/description via translate.py and
attaches it with videos.update. New uploads get their localization at upload
time (sync.py), so this only matters for the pre-2026-08-04 catalog — but it
is safe to re-run anytime: localized videos are skipped.

videos.update needs the youtube.force-ssl scope, which the sync's own refresh
token lacks. Pass a ready access token via YT_ACCESS_TOKEN (e.g. minted by the
ig-dashboard worker, whose token has the scope).

Env: YT_ACCESS_TOKEN, OPENROUTER_API_KEY, DRY_RUN=1 to list without updating.
"""

import os
import sys

import requests

from translate import translate_metadata

API = "https://www.googleapis.com/youtube/v3"


def yt_get(token, path, **params):
    resp = requests.get(f"{API}/{path}", params=params,
                        headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GET {path} failed {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def all_upload_ids(token):
    ch = yt_get(token, "channels", part="contentDetails", mine="true")
    uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, page = [], None
    while True:
        params = {"part": "contentDetails", "playlistId": uploads, "maxResults": 50}
        if page:
            params["pageToken"] = page
        resp = yt_get(token, "playlistItems", **params)
        ids += [i["contentDetails"]["videoId"] for i in resp.get("items", [])]
        page = resp.get("nextPageToken")
        if not page:
            return ids


def main():
    token = os.environ["YT_ACCESS_TOKEN"]
    dry_run = os.environ.get("DRY_RUN") == "1"

    ids = all_upload_ids(token)
    print(f"{len(ids)} video(s) on the channel.")

    videos = []
    for i in range(0, len(ids), 50):
        resp = yt_get(token, "videos", part="snippet,localizations",
                      id=",".join(ids[i:i + 50]))
        videos += resp.get("items", [])

    todo = [v for v in videos if "en" not in (v.get("localizations") or {})]
    print(f"{len(todo)} video(s) missing an English localization.\n")

    failed = 0
    for v in todo:
        sn = v["snippet"]
        print(f"[{v['id']}] {sn['title']}")
        if dry_run:
            continue
        en = translate_metadata(sn["title"], sn.get("description", ""))
        if not en:
            failed += 1
            continue
        body = {
            "id": v["id"],
            "snippet": {
                # update overwrites every mutable snippet field, so echo them all
                "title": sn["title"],
                "description": sn.get("description", ""),
                "categoryId": sn.get("categoryId", "15"),
                "tags": sn.get("tags", []),
                "defaultLanguage": sn.get("defaultLanguage") or "tr",
                "defaultAudioLanguage": sn.get("defaultAudioLanguage") or "tr",
            },
            "localizations": {**(v.get("localizations") or {}),
                              "en": {"title": en["title"],
                                     "description": en["description"]}},
        }
        resp = requests.put(
            f"{API}/videos", params={"part": "snippet,localizations"},
            headers={"Authorization": f"Bearer {os.environ['YT_ACCESS_TOKEN']}"},
            json=body, timeout=60,
        )
        if resp.status_code != 200:
            print(f"  UPDATE FAILED {resp.status_code}: {resp.text[:300]}")
            failed += 1
            continue
        print(f"  -> EN: {en['title']}")

    if dry_run:
        print("\nDRY RUN — nothing updated.")
    else:
        print(f"\nDone: {len(todo) - failed} updated, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
