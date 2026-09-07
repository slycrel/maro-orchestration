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
| path as the worker sees it | the same file; `/tmp/ask-operator.json` when the step runs in the container (the scratch bind) | the same file |
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

**The asking worker still holds the project slot.** After it writes the
ask the worker runs its post-pause tail (record, curate, notify — minutes),
and the per-project admission gate is a flock it keeps until then. An
answer that lands inside that window used to come back `refused_busy` and
the run stayed paused with its answer recorded (first live firing,
084d3c1f, 2026-09-07). Now an operator-answer resume waits for the slot
(`ask.resume_wait_s`, default 900 s) instead of refusing; and `maro answer
<handle>` — text optional, `--retry` in the CLI — re-drives a run whose
every recorded resume ended `refused_busy` / `error` / `failed`, reusing the
recorded answer when none is given. A resume that ran, or one still
queued, keeps the door closed ("already answered"). Which resumes belong
to the current question is exact: each answer stamps its task id on the
record (`resume_job_ids`); a run that asked twice is judged by the second
question's resumes only, never by the first's (which legitimately ran and
paused again).

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
- **Not the channel for missing software.** A worker that lacks a tool writes an environment request, not a question: the engine builds it or escalates to the ORCHESTRATOR (`docs/ENV_REQUEST_DESIGN.md`, decree ea9e311f). The third question on 084d3c1f ("approval to install Chromium") was this lane's job.
- **No question channel for judges or planners.** Only a tool-bearing
  execute can ask; a judge that wants the operator is a judge with a
  missing falsifier.
- **Nothing container-specific.** The ask is a file in the worker's
  scratch dir; where the worker ran is the executor's business. The one
  container-aware line (the path string the worker sees through the
  `/tmp` bind) is executor plumbing, the same as the secrets drop file.
- **No unverified question reaches the operator unflagged** (§7): a link
  that does not resolve or a code request with no delivery evidence goes
  back to the worker first; what still fails rides the card as
  `unverified`.
- ~~First live firing owed~~ — run 084d3c1f, 2026-09-07, asked FIVE times
  (IMAP refused → app password → install approval → 2FA code with a dead
  link and no code sent → the same question again after Jeremy's "I never
  received a code"). Questions 3–5 are what §7, §8 and the env-request
  lane exist to prevent. The time-box sweep still has no cron line; add
  one when a question has actually expired.

## 7. Grounding — an ask is a claim (2026-09-07)

084d3c1f's fourth question read *"Open https://account.yahoo.com/security
and provide the 6-digit code from your phone."* The link 404'd (the
worker guessed it) and no code had been sent: the script had landed on
Yahoo's method-chooser page and closed the browser without choosing a
method. Jeremy, from the user's side: *"I never received a code"* — and
the resumed run asked the identical question again. His read: the
verification step on a step's output should have caught this. Decision
`c6a3bb47`.

`operator_ask.ground(ask)` runs on the host before any card goes out:

| probe | fails when | who gets it |
|---|---|---|
| links (`question`, `why`, `no_input_alternative`, `sent`; up to five) | HEAD/GET answers ≥ 400 or the host is unreachable (8 s) | the worker |
| code request (`asks_for_code`: 2FA / OTP / 6-digit / verification code / passcode …) | no `sent` — the ask does not say how the worker triggered delivery and what confirmation it saw | the worker for a pause-lane ask; for a LIVE ask the OPERATOR, as an `unverified` line ("if no text arrived, reply 'no code'") — the operator can judge whether a text came, and bouncing cost a real attempt (70aa9fd8 08:13Z: the code HAD been sent) |
| code request | not `live` — the code is consumed by the session that asked and a pause ends it (§8) | the worker |

A failing ask is **bounced**: the step re-runs once (the loop_blocked
retry idiom) with *"Your question to the operator was NOT sent — it
failed a check: …"* at the top of its context, and the ask is archived
beside the next one (same-second archives get a `-1` suffix; never
overwritten). A second failure passes through with the problems on the
record and on the card as `unverified` — the gate tells the operator what
Maro could not verify about its own question; it never blocks them.
Trace edge `step.ask → ask.bounced`. Test seam `_PROBE_URL`.

The frame says both rules up front (`instructions`): links must resolve;
a code request says in `sent` how YOU triggered delivery (choose the SMS
or authenticator option first) and what you saw.

## 8. Live asks — the worker waits, the engine announces mid-step

The pause lane ends the step; a 2FA code is consumed by the session that
requested it, so the browser has to outlive the question. `ask.live_wait_s`
(600) is the window.

| | |
|---|---|
| worker | writes the ask with `"live": true` and KEEPS RUNNING (browser open), polling `$MARO_ASK_ANSWER` (`<scratch>/ask-answer.json`, `/tmp/ask-answer.json` in the container) every few seconds, printing a line every 30 s; on `{"answer": …}` it enters the code; on `{"bounce": …}` it fixes the ask and writes it again; when the window closes it ends the step stating the gap |
| engine, mid-step | `llm._run_subprocess_safe`'s poll loop calls `operator_ask.watch_live(scratch)` each tick: a live ask that fails §7 is bounced through the answer file at once; one that passes is recorded on the run (`live: true`, deadline = now + window, NOT a pause), traced `step.ask → ask.live`, announced with the usual card + Hermes leg marked LIVE (minutes left, `sent`), and remembered in a scratch marker so it is announced once. While it is pending the liveness clock is held and the wall clock may stretch to the window + 120 s, so a 10-minute wait fits inside a 600 s step |
| operator | the same verb: `maro answer <handle> <code>` / gate `answer <handle> <code>` → the reply lands in the answer file, the record says `delivery: live`, the gate reports `delivered` (no resume job) |
| engine, after the step | `close_live` consumes the marker and the answer file (both archived). Answered → the step's own outcome stands. Unanswered → the same question becomes a normal pause with a 24 h box, **no second card** (`pause_for_ask(record=…, notify=False)`); a late reply resumes the run as before |

Learned on the second live attempt (70aa9fd8, 2026-09-07 08:02–08:14Z):
the worker asked at minute eleven of a 600 s step; the ask was bounced
(no `sent`), so no window was granted, and the wall clock killed the step
ten seconds after the real text went out — the code was orphaned. Three
rules from it: a **bounce grants the window too** (`watch_live` returns
`bounced` with `remaining_s`; the poll loop stretches the wall clock and
holds liveness 60 s while the worker fixes its ask); a **new ask file
after the marker is a new question** (mtime later than the announced
file's: the marker and any stale answer file are retired, archived, and
the new ask is announced — before this a re-ask in the same step was
never announced); and the frame says **a bounce is never a reason to end
the step**. A `sent` claim is still a claim: the gate checks that the
worker said how it triggered delivery, not that it did (the first attempt
misread "You'll get a verification code" as confirmation) — the
screenshot the worker saves is the operator's check, not the engine's.

Learned on the same run's later steps (08:20–08:44Z, codes relayed by
hand): after a stale code was rejected the worker restarted the whole
login — which requests a NEW code — and then entered the code it already
held, hard-coded into the script. A code belongs to the browser session
that requested it, so re-requesting kills every code in hand; the worker
also never wrote an ask for those later codes, so no card went out for
them (an ask that is not written is not announced — this is the system
working, not a delivery failure). The frame now says: never enter a code
you already hold after re-requesting; delete the answer file, write a new
ask, wait for the new answer. Run stopped and re-fired on the fixed
engine 08:52Z.

Not here: a live ask that a step never announced (the poll loop did not
run, e.g. a mocked executor) is treated as a normal pause with a card.
Go successor parity for §7–§8 is owed.
