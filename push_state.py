#!/usr/bin/env python3
"""Commit state.json + sync_log.jsonl safely under concurrent workflow runs.

A plain `git push` fails when another run pushed state in the meantime (the
2026-07-27 duplicate-upload incident: a delayed scheduled run and a manual
dispatch raced). This script instead: snapshots the local results, rebuilds on
top of the latest origin/main, MERGES (union of synced ids, union of ledger
lines) and pushes — retrying up to 3 times.

Used as the final step of sync.yml, migrate.yml and backfill.yml.
COMMIT_MSG env overrides the commit message.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "sync_log.jsonl")
MAX_STATE_IDS = 500


def sh(*args):
    subprocess.run(args, check=True, cwd=HERE)


def read_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def read_lines(path):
    try:
        with open(path) as f:
            return [line.rstrip("\n") for line in f if line.strip()]
    except FileNotFoundError:
        return []


def main():
    local_state = read_state(STATE)
    local_log = read_lines(LOG)
    if local_state is None and not local_log:
        print("No state files present; nothing to push.")
        return 0

    sh("git", "config", "user.name", "github-actions[bot]")
    sh("git", "config", "user.email", "github-actions[bot]@users.noreply.github.com")
    msg = os.environ.get("COMMIT_MSG", "Update sync state")

    for attempt in range(1, 4):
        sh("git", "fetch", "--quiet", "origin", "main")
        sh("git", "reset", "--hard", "--quiet", "origin/main")

        if local_state is not None:
            remote_state = read_state(STATE) or {"synced": []}
            seen = set(remote_state.get("synced", []))
            merged = remote_state.get("synced", []) + [
                i for i in local_state.get("synced", []) if i not in seen
            ]
            remote_state["synced"] = merged[-MAX_STATE_IDS:]
            with open(STATE, "w") as f:
                json.dump(remote_state, f, indent=2)

        if local_log:
            remote_log = read_lines(LOG)
            known = set(remote_log)
            merged_log = remote_log + [l for l in local_log if l not in known]
            with open(LOG, "w") as f:
                f.write("\n".join(merged_log) + "\n")

        for f in ("state.json", "sync_log.jsonl"):
            if os.path.exists(os.path.join(HERE, f)):
                sh("git", "add", f)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=HERE).returncode == 0:
            print("Remote already contains everything; nothing to commit.")
            return 0

        sh("git", "commit", "--quiet", "-m", msg)
        if subprocess.run(["git", "push", "--quiet"], cwd=HERE).returncode == 0:
            print(f"State pushed (attempt {attempt}).")
            return 0
        print(f"Push rejected on attempt {attempt}; refetching and re-merging...")

    print("Failed to push state after 3 attempts.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
