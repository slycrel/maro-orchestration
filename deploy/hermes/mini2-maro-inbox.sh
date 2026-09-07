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
  prompt="A maro job you dispatched just pushed a '${event}' event. Read the JSON at ${event_file}. Key fields: .goal is the user's ORIGINAL ASK, .answer_summary is a distilled answer, .deliverable_content is the full deliverable text (.deliverable_name; may be truncated if .deliverable_truncated), .goal_achieved / .goal_verdict_summary / .goal_verdict_gaps are the verifier's take, .job_id ties it to your dispatch record, .served_artifact_urls (when present) are public links to the run's actual deliverable files — include the most relevant one or two in your message so Jeremy can click through to the full artifact instead of relying on any summary. Compose the answer to the original ask and send it to Jeremy with: hermes send -t telegram:1741138930 '<message>'. Ground rules: answer the ask directly from the deliverable data — organize it however serves the reader; quote the data, never invent findings; if goal_achieved is false or there are gaps, say so plainly and relay the gaps; for a clarification_needed status relay .clarification_question and say the user can just reply with the answer — you pass it back with: ssh maro-dispatch \"answer <job_id or handle_id> <their answer>\" (either id works; the SAME run resumes, do not re-dispatch). For an operator_question event the run is PAUSED on .question: relay .question verbatim, .why in one line, and .no_input_alternative as what Maro already tried without them; give the deadline (.deadline) and say a reply resumes it (answer <handle_id> <text>, .handle_id). For an escalation event with .point == env_request and .audience == orchestrator the decision is YOURS, not Jeremy's (decree 2026-09-07: the orchestrator guides in place of the user; the user gets involved only if they must): the run wants to install packages (.request lists them by source, .summary says what for, .reason says why policy escalated). If they are ordinary tooling for the stated need, run: ssh maro-dispatch \"answer <handle_id> allow\" — then tell Jeremy in ONE line what you allowed and why. Deny with: ssh maro-dispatch \"answer <handle_id> deny <reason>\" when the packages are unrelated to the need, replace the box's services (sudo, ssh, docker, systemd, cron) without a stated reason, or are enormous. Involve Jeremy only when the request touches his accounts, money, or the box's role — then relay the decision line and say you are waiting on him. Keep it tight — a phone-glance message, not the whole report; mention the full report is available on request. When sent, move the event file to ${inbox}/processed/."
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

if event == "operator_question":
    q = str(d.get("question", "") or "").strip()
    alt = str(d.get("no_input_alternative", "") or "").strip()
    hid = str(d.get("handle_id", "") or "")
    lines = [f"❓ Maro is paused on a question — {goal_short}", f"Q: {q[:600]}"]
    if alt:
        lines.append(f"Tried without you: {alt[:300]}")
    dl = str(d.get("deadline", "") or "")
    if dl:
        lines.append(f"Waiting until {dl}")
    lines.append(f"Reply with the answer and I'll pass it back (run {hid}).")
    print("\n".join(lines))
    sys.exit(0)
if event == "escalation" and str(d.get("point", "")) == "env_request":
    hid = str(d.get("handle_id", "") or "")
    req = d.get("request") or {}
    pk = ", ".join(f"{k}: {' '.join(v)}" for k, v in req.items() if v)
    print(f"🔧 Maro wants to install software — {goal_short}\n{pk[:300]}\n"
          f"Why: {str(d.get('summary', '') or '')[:200]}\n"
          f"Policy said: {str(d.get('reason', '') or '')[:200]}\n"
          f"This is my call; the brain lane was down, so I am relaying it. "
          f"Say 'allow' or 'deny <why>' and I'll pass it back (run {hid}).")
    sys.exit(0)
if event == "operator_question_expired":
    q = str(d.get("question", "") or "").strip()
    print(f"⏳ Maro's question went unanswered — {goal_short}\nQ: {q[:400]}\n"
          f"A late answer still resumes it (run {d.get('handle_id', '')}).")
    sys.exit(0)
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
