#!/usr/bin/env bash
# ci-watch.sh — watch the CI run for a landed SHA; Telegram-ping on red.
#
# Spawned detached by land.sh after a successful push (2026-08-01, Jeremy's
# CI-visibility ask; unlocked by the re-minted gh token). Conclusions:
#   red (failure/timed_out/...) -> one Telegram ping with title + URL, exit 1
#   success                     -> silent, exit 0
#   skipped                     -> silent, exit 0
#   cancelled                   -> superseded by a newer push. The first cut
#                                  trusted the successor's watcher to exist;
#                                  2026-08-09 showed a session landing six
#                                  commits with NO watcher at all, so a
#                                  cancelled run silently cost main its
#                                  verdict. Now: follow the chain — watch the
#                                  NEWEST main run to a real conclusion.
# Never saw a completed run inside the deadline -> logs and exits 0 without
# pinging: a missing run is usually Actions lag, and the phone ping is
# reserved for a confirmed red (sparing-updates rule). /tmp/ci-watch.log
# holds the trail either way.
#
# Ping dedup: several watchers from a rapid land burst can all follow the
# chain to the same final run; a marker dir per (sha, conclusion) keeps the
# red ping to ONE message no matter how many watchers converge on it.
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

ping_red() {  # <conclusion> <title> <url> <sha> — once per (sha, conclusion)
    local conclusion="$1" title="$2" url="$3" sha="$4"
    if ! mkdir "/tmp/ci-watch-pinged-${sha}-${conclusion}" 2>/dev/null; then
        echo "[$(date -u +%FT%TZ)] sha=${sha} ${conclusion} already pinged by another watcher"
        return 0
    fi
    PYTHONPATH=src python3 - "$conclusion" "$title" "$url" "$sha" <<'PY'
import sys
from notify_telegram import send
conclusion, title, url, sha = sys.argv[1:5]
send(f"CI {conclusion} on main @ {sha[:7]} — {title}\n{url}")
PY
}

follow_newest() {  # the cancelled path: main's newest run owns the verdict now
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
        ROW="$(gh run list --repo "$REPO" --branch main --limit 1 \
            --json status,conclusion,headSha,displayTitle,url \
            --jq '.[0] | "\(.status)|\(.conclusion)|\(.headSha)|\(.displayTitle)|\(.url)"' \
            2>/dev/null || true)"
        STATUS="${ROW%%|*}"
        if [ "$STATUS" = "completed" ]; then
            REST="${ROW#*|}";  CONCLUSION="${REST%%|*}"
            REST="${REST#*|}"; NSHA="${REST%%|*}"
            REST="${REST#*|}"; TITLE="${REST%%|*}"; URL="${REST#*|}"
            case "$CONCLUSION" in
                cancelled) : ;;  # burst still settling — keep waiting
                success|skipped)
                    echo "[$(date -u +%FT%TZ)] successor sha=${NSHA} conclusion=${CONCLUSION}"
                    exit 0 ;;
                *)
                    echo "[$(date -u +%FT%TZ)] successor sha=${NSHA} conclusion=${CONCLUSION}"
                    ping_red "$CONCLUSION" "$TITLE" "$URL" "$NSHA"
                    exit 1 ;;
            esac
        fi
        sleep "$CI_WATCH_POLL"
    done
    echo "[$(date -u +%FT%TZ)] sha=${SHA} superseded and no successor verdict inside ${CI_WATCH_TIMEOUT}s — main is unverdicted (no ping)"
    exit 0
}

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
            success|skipped) exit 0 ;;
            cancelled) follow_newest ;;
            *)
                ping_red "$CONCLUSION" "$TITLE" "$URL" "$SHA"
                exit 1 ;;
        esac
    fi
    sleep "$CI_WATCH_POLL"
done
echo "[$(date -u +%FT%TZ)] sha=${SHA} no completed run inside ${CI_WATCH_TIMEOUT}s — giving up (no ping)"
exit 0
