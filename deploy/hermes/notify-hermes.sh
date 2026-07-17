#!/usr/bin/env bash
# notify.command chain for the two-box setup (SESSION_PROTOCOL_DESIGN §3,
# "push direction" leg). Wired in ~/.maro/config.yml as:
#
#   notify:
#     command: "bash ~/claude/maro-orchestration/deploy/hermes/notify-hermes.sh"
#
# Receives one event payload as JSON on stdin plus MARO_EVENT_TYPE /
# MARO_RUN_DIR / MARO_HANDLE_ID / MARO_STATUS in env (src/notify.py contract).
#
#   leg 1: Telegram message to the ops channel via notify_telegram — the
#          user-facing completion/escalation message (verdict + findings).
#   leg 2: push the event JSON to Hermes's inbox on mini2 over SSH so the
#          interface agent KNOWS the state of work it dispatched. For
#          dispatched-run completions and escalation-class events the inbox
#          script also sends Jeremy a short follow-up in the DM lane.
#
# Each leg is best-effort; exit 0 if at least one delivered.
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERMES_HOST="${MARO_NOTIFY_HERMES_HOST:-mini2}"
INBOX_SCRIPT="\$HOME/bin/maro-inbox.sh"

payload="$(cat)"
event="${MARO_EVENT_TYPE:-unknown}"
[ -n "$payload" ] || exit 1

# Enrich with the originating dispatch job_id when the run records one —
# Hermes tracks its dispatches by job_id, not handle_id.
job_id=""
if [ -n "${MARO_RUN_DIR:-}" ] && [ -f "${MARO_RUN_DIR}/metadata.json" ]; then
  job_id="$(python3 -c '
import json, sys
meta = json.load(open(sys.argv[1]))
print((meta.get("origin") or {}).get("job_id", ""))
' "${MARO_RUN_DIR}/metadata.json" 2>/dev/null || true)"
fi
if [ -n "$job_id" ]; then
  enriched="$(printf '%s' "$payload" | python3 -c '
import json, sys
d = json.load(sys.stdin)
d["job_id"] = sys.argv[1]
print(json.dumps(d, default=str))
' "$job_id" 2>/dev/null || true)"
  [ -n "$enriched" ] && payload="$enriched"
fi

# Leg 1: ops-channel Telegram message.
printf '%s' "$payload" | (cd "$REPO" && PYTHONPATH=src python3 -m notify_telegram)
ok_telegram=$?

# Leg 2: Hermes inbox push. announce=1 → the inbox script also DMs Jeremy:
# dispatched-run completions (job_id present) and escalation-class events.
announce=0
case "$event" in
  run_completed) [ -n "$job_id" ] && announce=1 ;;
  escalation|backend_actionable|stranded_run|recursion_checkin|\
  self_improvement_verdict|resume_refused_busy|resume_lock_unavailable)
    announce=1 ;;
esac
printf '%s' "$payload" | ssh -o ConnectTimeout=5 -o BatchMode=yes \
  "$HERMES_HOST" "bash ${INBOX_SCRIPT} '${event//[^a-zA-Z0-9_-]/}' ${announce}"
ok_hermes=$?

[ "$ok_telegram" -eq 0 ] || [ "$ok_hermes" -eq 0 ]
