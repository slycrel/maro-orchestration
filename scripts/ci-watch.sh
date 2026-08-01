#!/usr/bin/env bash
# ci-watch.sh — watch the CI run for a landed SHA; Telegram-ping on red.
#
# Spawned detached by land.sh after a successful push (2026-08-01, Jeremy's
# CI-visibility ask; unlocked by the re-minted gh token). Conclusions:
#   red (failure/timed_out/...) -> one Telegram ping with title + URL, exit 1
#   success                     -> silent, exit 0
#   cancelled / skipped         -> silent, exit 0 — a rapid successive push
#                                  supersedes the run; the land that pushed
#                                  the successor is watching that one.
# Never saw a completed run inside the deadline -> logs and exits 0 without
# pinging: a missing run is usually Actions lag, and the phone ping is
# reserved for a confirmed red (sparing-updates rule). /tmp/ci-watch.log
# holds the trail either way.
#
# Usage: ci-watch.sh <sha> [owner/repo]
set -u
SHA="${1:?usage: ci-watch.sh <sha> [owner/repo]}"
REPO="${2:-slycrel/maro-orchestration}"
: "${CI_WATCH_DELAY:=30}"       # initial wait for Actions to register the run
: "${CI_WATCH_TIMEOUT:=1800}"   # give up after 30 min
: "${CI_WATCH_POLL:=60}"

cd "$(dirname "$0")/.." || exit 1
DEADLINE=$(( $(date +%s) + CI_WATCH_TIMEOUT ))
echo "[$(date -u +%FT%TZ)] watch start sha=${SHA}"
sleep "$CI_WATCH_DELAY"

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    ROW="$(gh run list --repo "$REPO" --commit "$SHA" --limit 1 \
        --json status,conclusion,displayTitle,url \
        --jq '.[0] | "\(.status)|\(.conclusion)|\(.displayTitle)|\(.url)"' \
        2>/dev/null || true)"
    STATUS="${ROW%%|*}"
    if [ "$STATUS" = "completed" ]; then
        REST="${ROW#*|}";  CONCLUSION="${REST%%|*}"
        REST="${REST#*|}"; TITLE="${REST%%|*}"; URL="${REST#*|}"
        echo "[$(date -u +%FT%TZ)] sha=${SHA} conclusion=${CONCLUSION}"
        case "$CONCLUSION" in
            success|cancelled|skipped) exit 0 ;;
            *)
                PYTHONPATH=src python3 - "$CONCLUSION" "$TITLE" "$URL" "$SHA" <<'PY'
import sys
from notify_telegram import send
conclusion, title, url, sha = sys.argv[1:5]
send(f"CI {conclusion} on main @ {sha[:7]} — {title}\n{url}")
PY
                exit 1 ;;
        esac
    fi
    sleep "$CI_WATCH_POLL"
done
echo "[$(date -u +%FT%TZ)] sha=${SHA} no completed run inside ${CI_WATCH_TIMEOUT}s — giving up (no ping)"
exit 0
