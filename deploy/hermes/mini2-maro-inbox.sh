#!/bin/bash
# Maro → Hermes inbox receiver. LIVES ON MINI2 at ~/bin/maro-inbox.sh —
# this repo copy is the source of truth; install with:
#   scp deploy/hermes/mini2-maro-inbox.sh mini2:bin/maro-inbox.sh
#
# Invoked over SSH by notify-hermes.sh with one event payload as JSON on
# stdin: files it under ~/.hermes/inbox/maro/ (so Hermes can answer "how's
# my job doing" from local state — see the maro-dispatch skill), and when
# announce=1 sends Jeremy a short Telegram DM composed DETERMINISTICALLY
# from the payload fields — no brain turn, nothing guessed, fields quoted
# as-is per the skill's ground rules.
set -u
event="${1:-unknown}"
announce="${2:-0}"

inbox="$HOME/.hermes/inbox/maro"
mkdir -p "$inbox/processed" "$HOME/.hermes/logs"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
event_file="$inbox/${ts}-${event}-$$.json"
cat > "$event_file"

[ "$announce" = "1" ] || exit 0

msg="$(/usr/bin/python3 - "$event_file" "$event" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
event = sys.argv[2]
goal = str(d.get("goal", "") or d.get("reason", "")).strip()
goal_short = goal[:120] + ("…" if len(goal) > 120 else "")
job = str(d.get("job_id", "") or "")

if event == "run_completed":
    status = str(d.get("status", "?"))
    if status == "clarification_needed":
        q = str(d.get("clarification_question", "") or "").strip()
        print(f"❓ Your maro job needs an answer: {q or '(question missing — ask me to fetch the result)'}"
              f"\nJob {job}. Tell me the answer and I'll re-dispatch.")
        sys.exit(0)
    achieved = d.get("goal_achieved")
    if achieved is True:
        head = "✅ Done — goal achieved (verified)"
    elif achieved is False:
        head = "⚠️ Finished, but the goal was NOT achieved"
    elif status == "error":
        head = "❌ Failed"
    else:
        head = f"Finished ({status})"
    verdict = str(d.get("goal_verdict_summary", "") or "").strip()
    lines = [f"{head}: {goal_short}"]
    if verdict:
        lines.append(verdict[:200] + ("…" if len(verdict) > 200 else ""))
    tail = f"Job {job}." if job else ""
    lines.append((tail + " Ask me for the details or the full report.").strip())
    print("\n".join(lines))
else:
    detail = str(d.get("summary", "") or d.get("user_action", "")
                 or d.get("reason", "") or "").strip()
    print(f"🔔 Maro raised {event}: {detail[:250] or goal_short}"
          + (f" (job {job})" if job else ""))
PY
)"

if [ -n "$msg" ]; then
  export PATH="$HOME/.hermes/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
  hermes send -t telegram:1741138930 "$msg" \
    >> "$HOME/.hermes/logs/maro-inbox.log" 2>&1 \
    || echo "$(date -u +%FT%TZ) send failed for $event_file" >> "$HOME/.hermes/logs/maro-inbox.log"
fi
exit 0
