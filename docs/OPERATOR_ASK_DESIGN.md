---
status: living
---

# Operator questions — the rare exception, built as a lane

*Shipped both engines 2026-09-06. Decree: GOAL_BRAIN 2026-09-06 (decision
`1d1ad8b0`). Python: `src/operator_ask.py`; Go: `go/internal/run/ask.go`,
`go/cmd/maro-go/ask.go`. Tests: `tests/test_operator_ask.py`,
`go/internal/run/ask_test.go`, `go/cmd/maro-go/ask_test.go`.*

## 1. Why this exists

The mailbox-access arc (BACKLOG, 2026-09-06) ended with a question Maro
could not answer alone, delivered to Telegram, and no way for the answer
to come back: Hermes polled the bot, the listener ran nowhere, `dispatch.py`
had no resume verb, and a `clarification_needed` returned the question and
waited for a fresh dispatch that never carried the answer by handle.

Jeremy's decree fixes the posture before the plumbing: *"push things in the
direction of 'maro answers its own questions as much as possible'…
prompt-for-work is very easy to slip into prompt-for-decision/judgement/
permission… we need to build that out, and it should be the rare
exception, not the norm."* So the lane exists, and everything about it
leans the other way: the frame tells the worker to try a path that needs
no input first and to prefer finishing with a stated gap over asking for a
decision; every ask is a counted, reviewable record with a time box; the
answer resumes the run by handle instead of starting a new one.

## 2. The contract — a file, not a sentence

The execute frame carries a `## Asking the operator` paragraph (identical
wording in both engines) naming one path, `$MARO_ASK`. A worker that
cannot proceed without something only the operator has writes ONE JSON
object there and ends its step:

```json
{"question": "...", "why": "...",
 "no_input_alternative": "what you tried without them, or why none exists",
 "tried": true}
```

The engine reads the file after the execute and nothing else: "I asked the
operator" in the response without the file is not an ask, and the file is
an ask whatever the prose says. The file is archived beside itself
(`ask-operator.<stamp>.asked.json`), never deleted, so the resumed run
cannot re-trigger the same question and the record survives.

| | Python | Go |
|---|---|---|
| path (host lane) | `<run_dir>/scratch/ask-operator.json` | `<workspace>/drop/ask-operator.json` |
| path (container) | `/tmp/ask-operator.json` (the scratch bind) | n/a (host only in v1) |
| env var | `MARO_ASK` (worker child env; container `-e`) | `MARO_ASK` (subprocess backend, tool-bearing calls) |
| read by | `loop_execute` after each step | `Driver.askAfterExecute` after the NOW execute / each AGENDA step |

## 3. What the ask does to the run

**Python** — the step's outcome becomes `blocked` with the typed pause
`awaiting-clarification` (the same `PAUSE_OP_CLARIFICATION` the pre-run
clarity gate uses); the loop ends `interrupted`, the run metadata carries
`operator_ask` {question, why, no_input_alternative, tried, step,
asked_at, deadline, status} plus `clarification_question` and
`pause_reason`; a trace edge `step.ask → pause.awaiting-clarification` is
recorded; `operator_question` is emitted (escalation-class: Telegram card
"❓ maro has a question" with Goal / Q / Why / Tried without you / Waiting
until / Answer:, and the Hermes inbox leg with `announce=1`).

**Go** — a `question` record (run-scoped: step, question, why,
no_input_alternative, tried, deadline) is committed and the attempt ends
on an honest failed terminal `needs answer: <question>`; the tail treats
it like `needs clarification` (no signal, no lesson minted from waiting).
`runs show` prints the question; `asks` lists it.

Both engines: the time box is 24 h (`ask.timeout_hours` in Python config;
`AskTimebox` in Go). Expiry destroys nothing — a late answer still runs,
marked late; `maro asks --sweep` / `maro-go asks` report the expired state.

## 4. The answer — by handle

**Python** — `maro answer <handle> "<text>"` (`--stdin`, `--detach`,
`--source`): refuses an unknown run, a run with no question, one already
answered, or one that already has a verdict; stamps `operator_ask.status =
answered` + `clarification_answer`; enqueues a `loop_continuation` task
with `origin.parent_handle_id` on the AGENDA lane, which `handle_queue`
routes as RESUME (same identity: `run_agent_loop(goal, handle_id=parent,
ancestry_context_extra=…)`) with the answer in the continuation reason:

```
CONTINUATION of: <goal>

== Operator answer ==
The run paused to ask the operator: <question>
The operator answered: <text>
Continue from where the run paused, using this answer; do not ask it again.
== End operator answer ==
```

Inline by default (the CLI drains the queued task and exits when the resumed
run finishes); `--detach` leaves it for the queue.

**Hermes / Telegram** — the gate grows an `answer <handle> <text>` verb
(`deploy/hermes/maro-ssh-gate.sh` → `dispatch.py answer`, source
`hermes-ssh`, a detached worker records the resumed run under its own job
id with `parent_job_id`). The mini2 inbox prompt tells the brain that a
reply to a question card is `ssh maro-dispatch "answer <handle> …"`, never a
fresh dispatch; the dispatch SKILL carries the same rule.

**Go** — `maro-go answer <handle> [--source s] <text>` commits an `answer`
record against the asked run (refuses unasked / already answered; `late`
past the deadline) and runs the goal again in its lane, lineage `--after`
the asked run, with the answer block as `--context`. The follow-up is a
recorded run of its own; the worker sees the question, the answer, and the
instruction not to ask again. The Go engine also answers the AGENDA clarity
gate's question this way (an unclear intent is an ask with no file).

## 5. The ledger

`maro asks` (`--sweep` stamps expired + emits `operator_question_expired`;
`--json`) and `maro-go asks [--json]` list every question with its state:
`?` pending, `✓` answered (by whom, late or not), `×` expired. Asking is
meant to be reviewed: a goal family that asks often is a family whose
frame, secrets or capabilities are short, not a family that needs a better
question channel.

## 6. What is deliberately not here

- **No auto-resume of a paused run without an answer.** The no-input
  alternative is the worker's, tried before the ask; the engine does not
  invent one after.
- **No question channel for judges or planners.** Only a tool-bearing
  execute can ask; a judge that wants the operator is a judge with a
  missing falsifier.
- **No container ask path in Go** (host only in v1; the Go engine has no
  container lane yet).
- **First live firing owed:** the mail re-ask (BACKLOG mailbox arc), with
  Jeremy's planner breakdown, is the first run expected to write the file
  for real. The time-box sweep has no cron line yet; add one when a
  question has actually expired.
