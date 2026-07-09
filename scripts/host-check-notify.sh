#!/usr/bin/env bash
#
# host-check-notify.sh — cron wrapper for host-check.sh that alerts a human.
#
# Runs host-check.sh --quiet; on ANY failure, pipes an escalation-shaped JSON
# payload into the existing notify_telegram target (same lane as
# run_completed/escalation events), so a red check lands in Jeremy's Telegram
# instead of dying silently in cron mail. This is the BACKLOG "host-check.sh
# alerting + scheduling" wire-up: channel = Telegram (notify.command lane),
# frequency = daily (crontab entry installed 2026-07-09).
#
# Silent when green — cron discipline: no output, exit 0.
# On red: prints the FAIL lines (for cron logs) AND notifies. Exit 1.
#
# Needs python only on the failure path; the check itself stays dependency-light.

set -o pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

out="$("$REPO_DIR/scripts/host-check.sh" --quiet 2>&1)"
rc=$?
[ "$rc" -eq 0 ] && exit 0

echo "$out"

# Build the escalation payload with python (json-safe), feed notify_telegram.
printf '%s' "$out" | PYTHONPATH="$REPO_DIR/src" python3 -c '
import json, socket, sys
fails = sys.stdin.read().strip()
payload = {
    "event_type": "escalation",
    "goal": "host-check on " + socket.gethostname(),
    "summary": "host-check FAILED:\n" + fails[:1500],
    "reason": "scripts/host-check-notify.sh cron",
}
print(json.dumps(payload))
' | PYTHONPATH="$REPO_DIR/src" python3 -m notify_telegram
exit 1
