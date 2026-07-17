#!/bin/bash
# Maro → Hermes inbox receiver. LIVES ON MINI2 at ~/bin/maro-inbox.sh —
# this repo copy is the source of truth; install with:
#   scp deploy/hermes/mini2-maro-inbox.sh mini2:bin/maro-inbox.sh
#
# Invoked over SSH by notify-hermes.sh with one event payload as JSON on
# stdin: files it under ~/.hermes/inbox/maro/ (so Hermes can answer "how's
# my job doing" from local state — see the maro-dispatch skill).
#
# Two-tone contract (Jeremy 2026-07-17): Maro pushes DATA (original ask,
# answer material, deliverable content) — the interface LLM composes the
# user-facing answer. When announce=1 this script spawns a detached Hermes
# brain turn pointed at the event file; Hermes reads the data and messages
# Jeremy in its own voice, grounded in the payload. If the brain turn can't
# spawn, fall back to a short deterministic DM so completions never go dark.
set -u
event="${1:-unknown}"
announce="${2:-0}"

inbox="$HOME/.hermes/inbox/maro"
mkdir -p "$inbox/processed" "$HOME/.hermes/logs"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
event_file="$inbox/${ts}-${event}-$$.json"
cat > "$event_file"

[ "$announce" = "1" ] || exit 0

export PATH="$HOME/.hermes/bin:$HOME/.local/bin:/usr/local/bin:$PATH"
log="$HOME/.hermes/logs/maro-inbox.log"

# Preferred lane: Hermes composes the answer from the event data.
if command -v hermes >/dev/null 2>&1; then
  prompt="A maro job you dispatched just pushed a '${event}' event. Read the JSON at ${event_file}. Key fields: .goal is the user's ORIGINAL ASK, .answer_summary is a distilled answer, .deliverable_content is the full deliverable text (.deliverable_name; may be truncated if .deliverable_truncated), .goal_achieved / .goal_verdict_summary / .goal_verdict_gaps are the verifier's take, .job_id ties it to your dispatch record. Compose the answer to the original ask and send it to Jeremy with: hermes send -t telegram:1741138930 '<message>'. Ground rules: answer the ask directly from the deliverable data — organize it however serves the reader; quote the data, never invent findings; if goal_achieved is false or there are gaps, say so plainly and relay the gaps; for a clarification_needed status relay .clarification_question and say you can re-dispatch with the answer. Keep it tight — a phone-glance message, not the whole report; mention the full report is available on request. When sent, move the event file to ${inbox}/processed/."
  nohup hermes -z "$prompt" >> "$log" 2>&1 &
  brain_pid=$!
  sleep 1
  if kill -0 "$brain_pid" 2>/dev/null || wait "$brain_pid" 2>/dev/null; then
    echo "$(date -u +%FT%TZ) brain turn spawned (pid $brain_pid) for $event_file" >> "$log"
    exit 0
  fi
  echo "$(date -u +%FT%TZ) brain turn died instantly, falling back to deterministic DM for $event_file" >> "$log"
fi

# Fallback lane: deterministic DM composed from payload fields, nothing guessed.
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
        head = "✅ Done"
    elif achieved is False:
        head = "⚠️ Finished, but the goal was NOT achieved"
    elif status == "error":
        head = "❌ Failed"
    else:
        head = f"Finished ({status})"
    # Answer-first: the body is the answer to what was asked (curation's
    # answer_summary); the verifier's self-grade only earns space when the
    # goal was NOT achieved.
    answer = str(d.get("answer_summary", "") or "").strip()
    verdict = str(d.get("goal_verdict_summary", "") or "").strip()
    lines = [f"{head} — {goal_short}"]
    if answer:
        lines.append(answer[:500] + ("…" if len(answer) > 500 else ""))
    if verdict and (not answer or achieved is False):
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
  hermes send -t telegram:1741138930 "$msg" >> "$log" 2>&1 \
    || echo "$(date -u +%FT%TZ) send failed for $event_file" >> "$log"
fi
exit 0
