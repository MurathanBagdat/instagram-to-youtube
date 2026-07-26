#!/usr/bin/env python3
"""Daily email report: last 24 hours of sync activity and workflow errors.

Reads sync_log.jsonl for uploads and the GitHub Actions API for failed runs,
then emails a summary. Requires GITHUB_TOKEN and GITHUB_REPOSITORY (both
provided by GitHub Actions) plus the Gmail secrets used by notify.py.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

from notify import send_email

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_log.jsonl")


def uploads_since(cutoff):
    if not os.path.exists(LOG_FILE):
        return []
    entries = []
    with open(LOG_FILE) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            ts = datetime.fromisoformat(entry["ts"])
            if ts >= cutoff:
                entries.append(entry)
    return entries


def failed_runs_since(cutoff):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    resp = requests.get(
        f"https://api.github.com/repos/{repo}/actions/runs",
        params={
            "created": f">={cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "per_page": 100,
        },
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json"},
        timeout=30,
    )
    resp.raise_for_status()
    runs = resp.json().get("workflow_runs", [])
    completed = [r for r in runs if r.get("status") == "completed"]
    failed = [r for r in completed if r.get("conclusion") not in ("success", "skipped")]
    return len(completed), failed


def main():
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    uploads = uploads_since(cutoff)
    total_runs, failed = failed_runs_since(cutoff)

    tr_now = now + timedelta(hours=3)  # Türkiye is UTC+3 year-round
    lines = [
        f"Chia the Frenchton — günlük senkronizasyon raporu",
        f"({tr_now.strftime('%d.%m.%Y %H:%M')} itibarıyla, son 24 saat)\n",
        f"Yüklenen reel sayısı: {len(uploads)}",
        f"Toplam çalışma sayısı: {total_runs}",
        f"Hatalı çalışma sayısı: {len(failed)}\n",
    ]

    if uploads:
        lines.append("Yüklenen reel'ler:")
        for e in uploads:
            manual = " (manuel)" if e.get("manual") else ""
            lines.append(f"• {e['title']}{manual}")
            lines.append(f"  {e['youtube_url']}")
        lines.append("")
    else:
        lines.append("Son 24 saatte yeni reel yüklenmedi.\n")

    if failed:
        lines.append("⚠️ Hatalı çalışmalar:")
        for r in failed:
            lines.append(f"• {r.get('name')} — {r.get('conclusion')}")
            lines.append(f"  {r.get('html_url')}")
    else:
        lines.append("Hata yok, her şey yolunda ✅")

    status = "⚠️ hata var" if failed else "sorunsuz"
    subject = f"📊 Chia günlük rapor: {len(uploads)} reel yüklendi, {status}"
    if not send_email(subject, "\n".join(lines)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
