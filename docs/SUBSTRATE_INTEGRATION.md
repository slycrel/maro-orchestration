# Substrate Integration — driving Maro from an external system

Maro is the orchestration engine; the *substrate* is whatever hosts it and
talks to the human — OpenClaw, Hermes Agent, a Telegram/Slack bridge, a shell
script, cron on somebody else's machine. This doc is the contract between them.

Design constraints (GOAL_BRAIN invariants):
- **Program, not operating system.** Maro runs when invoked and exits. No
  daemon, no server socket, no self-rearming loop. Everything below is either
  a synchronous call, a file the substrate can read, or a command Maro invokes
  *inside a run's own lifecycle*.
- **Installable harness.** `pip install -e .` gives you every CLI below.
  Paths honor `MARO_WORKSPACE` (default `~/.maro/workspace/`).

The contract has four verbs: **submit**, **poll**, **notify**, **fetch**.

---

## 1. Submit — give Maro a goal

Synchronous (blocks until the goal finishes — seconds for NOW-lane questions,
potentially long for AGENDA loops):

```bash
maro-handle "summarize the failures in the last 5 runs" --format json
# → JSON HandleResult: handle_id, lane, status, result, tokens, elapsed_ms
```

Queued (returns immediately; work happens at the next drain):

```bash
maro-enqueue "research X" "then build Y"          # sequential (DAG-linked)
maro-enqueue "goal A" "goal B" --parallel          # independent
maro-enqueue "big goal" --drain                    # enqueue AND drain now
```

Python API (same-process substrates):

```python
from handle import handle, enqueue_goal, drain_task_store
result = handle("goal text")                # sync; HandleResult
job = enqueue_goal("goal text", "reason")   # queued
drain_task_store(max_tasks=3)               # process queued work, then return
```

Draining is **pull-based by design**: the substrate decides when Maro spends
tokens. Call `maro-enqueue --drain`, or `drain_task_store()` from your own
loop, or run `python3 -m heartbeat` (one-shot health check + drain) on
whatever cadence the substrate already has. Maro never wakes itself up.

Magic prefixes work through every path: `effort:low`, `ralph:`, `verify:`,
`strict:`, `direct:` etc. (see `src/handle.py` MAGIC_PREFIXES).

## 2. Poll — check on a run

Every submission gets a run-dir: `$MARO_WORKSPACE/runs/<handle_id>-<nickname>/`.

```bash
maro-runs status <handle_id>    # {status, goal_achieved, lane, started/ended}
maro-runs list                  # recent runs, one line each
maro-runs show <handle_id>      # full run_card.json (see below)
```

Files, if you'd rather read than shell out:

| File | What |
|---|---|
| `<run-dir>/metadata.json` | status, lane, model, timestamps, `goal_achieved` verdict |
| `<run-dir>/run_card.json` | post-goal curation: outcome class, result excerpt, mineable inventory |
| `memory/events.jsonl` | append-only live feed (`step_done`, `run_completed`, `escalation`, …) — tail this for progress |

Note `goal_achieved` vs `status`: `status=done` means the process finished;
`goal_achieved` is the verifier's verdict. A substrate that reports "done" to
a human should prefer `success_class` from the run_card
(`success` / `done-not-achieved` / `done-unverified` / `partial` / `failed`).

## 3. Notify — Maro calls you

Polling is fine on the same box; the hook removes the need. Register a command
in config and Maro invokes it at run finalize and on escalations, inside the
run's own lifecycle (no server, no daemon):

```yaml
# ~/.maro/config.yml  (or $MARO_WORKSPACE/config.yml)
notify:
  command: "bash /path/to/your-notify.sh"
  events: [run_completed, escalation]    # default; trim to filter
  timeout_seconds: 30
```

The command receives the event payload as **JSON on stdin** — for
`run_completed` that's the full run_card (including `result_excerpt` and
`result_path`); for `escalation` it's `{goal, status, summary, reason, job_id,
point}`. Env vars for cheap shell dispatch: `MARO_EVENT_TYPE`,
`MARO_HANDLE_ID`, `MARO_STATUS`, `MARO_RUN_DIR`.

Escalation events fire when Maro decides a human is needed:
- **navigator escalate at dispatch** — a queued goal was refused before
  spawning a run (`point: dispatch`; no run-dir exists, this event is the only
  signal out);
- **director surface** — a loop escalation was adjudicated "for operator
  review" (`point: director_escalation`).

The hook never affects the run: failures/timeouts are logged and swallowed.
Off by default — no `notify.command`, no subprocess. Every event is still
appended to `memory/events.jsonl` regardless, so polling always works.

## 4. Fetch — get the actual answer

```bash
maro-runs result <handle_id>     # prints the result text, lane-normalized
```

```python
from run_curation import run_result
res = run_result(handle_id)      # {handle_id, lane, status, result, result_path}
```

This normalizes the two lane shapes (NOW-lane `artifact/now-*.json` payload;
AGENDA `build/loop-*-RESULT.md`, falling back to `-PARTIAL.md`). The run_card's
`result_excerpt` (first 500 chars) is usually enough for a chat reply; use
`result_path` for the full artifact.

---

## Recipe: OpenClaw as the substrate

Goal flow: Telegram → OpenClaw → Maro → `maro-notify-telegram` → Telegram.

```bash
# 1. OpenClaw-side dispatch (symlinked into ~/.openclaw/workspace/scripts/):
bash deploy/openclaw/maro-dispatch.sh "the user's goal text"

# 2. Maro-side notify hook (~/.maro/config.yml):
#    notify:
#      command: "maro-notify-telegram"
```

`maro-notify-telegram` formats the run_card / escalation into a short plain
message and sends it via the same bot token OpenClaw already uses (env
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` first, legacy openclaw.json second) —
so results land in the conversation the goal came from. Full setup:
`deploy/openclaw/README.md`.

## Recipe: Hermes Agent as the substrate

Register Maro as a Hermes tool — Hermes agents call shell tools natively:

```
Tool: maro_submit
  Command: maro-enqueue "{goal}" --drain && echo enqueued
Tool: maro_status
  Command: maro-runs status {handle_id}
Tool: maro_result
  Command: maro-runs result {handle_id}
```

Point `notify.command` at a script that posts to Hermes' inbox/notify channel
(Hermes' notify-when-done pattern is the same shape as this hook — command in,
payload on stdin).

## Recipe: bare cron / shell substrate

```bash
maro-enqueue "nightly goal"                       # anytime
maro-heartbeat                                    # one-shot: health + drain
tail -f $MARO_WORKSPACE/memory/events.jsonl       # live feed
```

---

## What Maro does NOT provide (deliberately)

- **No REST/HTTP inbound API.** Same-box filesystem + CLI is the contract; a
  remote substrate mounts the workspace or wraps the CLI over ssh. (Revisit if
  a real remote trial demands it.)
- **No background drain.** If nothing calls drain, queued tasks wait. That is
  the substrate's job — and the off-switch that actually stays off.
- **No channel credentials.** Telegram/Slack tokens belong to the substrate;
  Maro's own listeners (`maro-slack`, `src/telegram_listener.py`) exist but a
  substrate trial should use the substrate's channels.
