#!/usr/bin/env bash
#
# followups-check.sh — dated-followup tripwire (cron-friendly).
#
# "We won't revisit in a week" (Jeremy, 2026-10-23) is the failure mode this
# closes: a deferred item gets a DUE DATE in FOLLOWUPS.md, and from that date
# on this check is RED every day until the box is ticked. Same cron discipline
# as host-check.sh: silent exit 0 when nothing is due; prints FAIL lines and
# exits 1 when something is. With --notify, a red check also lands in Jeremy's
# Telegram through notify_telegram (the host-check-notify.sh lane).
#
# FOLLOWUPS.md line shape (anything else is ignored):
#   - [ ] YYYY-MM-DD  text          open item, due that day
#   - [x] YYYY-MM-DD  text          done (ignored)
#
# Also carries one standing probe that has no natural date: the Java
# eMudhra blocklist. apt owns /etc/java-21-openjdk/security/blocked.certs and
# rewrites it on every openjdk upgrade, silently dropping the 16 fingerprints
# appended 2026-10-23 (project_security_pass_2026_10_23) — so the entry count
# is checked, not the date.
#
# Usage: scripts/followups-check.sh [--notify] [--file PATH]

set -o pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FILE="$REPO_DIR/FOLLOWUPS.md"
NOTIFY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --notify) NOTIFY=1 ;;
        --file) FILE="$2"; shift ;;
        *) echo "usage: $0 [--notify] [--file PATH]" >&2; exit 2 ;;
    esac
    shift
done

today="$(date +%F)"
fails=""

if [ -f "$FILE" ]; then
    while IFS= read -r line; do
        case "$line" in
            "- [ ] "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*) ;;
            *) continue ;;
        esac
        due="${line:6:10}"
        text="${line:16}"
        # ISO dates compare lexically.
        if [[ "$due" < "$today" || "$due" == "$today" ]]; then
            fails+="FAIL followup  due $due: ${text## }"$'\n'
        fi
    done < "$FILE"
fi

# Standing probe: Java blocklist still carries the eMudhra fingerprints.
BLOCKED=/etc/java-21-openjdk/security/blocked.certs
if [ -f "$BLOCKED" ]; then
    n="$(grep -c -E '^[0-9A-Fa-f]{64}' "$BLOCKED")"
    if [ "$n" -lt 54 ]; then
        fails+="FAIL java-blocklist  $BLOCKED has $n entries (<54): apt rewrote it — re-append the 16 eMudhra fingerprints and re-probe (see FOLLOWUPS.md)"$'\n'
    fi
fi

[ -z "$fails" ] && exit 0
printf '%s' "$fails"

if [ "$NOTIFY" -eq 1 ]; then
    printf '%s' "$fails" | PYTHONPATH="$REPO_DIR/src" python3 -c '
import json, socket, sys
fails = sys.stdin.read().strip()
print(json.dumps({
    "event_type": "escalation",
    "goal": "followups due on " + socket.gethostname(),
    "summary": fails[:1500],
    "reason": "scripts/followups-check.sh cron",
}))
' | PYTHONPATH="$REPO_DIR/src" python3 -m notify_telegram
fi
exit 1
