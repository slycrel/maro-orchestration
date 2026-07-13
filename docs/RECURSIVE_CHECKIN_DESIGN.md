---
status: dormant-design
---

# Recursive-goal check-in — deep-recursion progress conversations

**Status:** design pass, written 2026-07-13 (`/goal` session). No code changed
by this pass. Resolves the known-gap pinned by
`tests/test_escalation.py::test_known_gap_continue_enqueues_past_max_restart_depth`
(2026-07-13, adversarial-review-skill follow-on) — that test's own comment
says the fix is "a design decision, not a one-line gate... which layer
should own the check... and what 'capped' should do." Jeremy made that
decision the same day (GOAL_BRAIN Decisions 2026-07-13, "recursive-goal
check-in decree") — this doc turns the decree into an implementable spec.
File:line references verified against the tree at the time of writing; they
WILL drift per this repo's docs-are-best-guess invariant — verify each seam
before editing.

## The gap, precisely

Two independent mechanisms both call themselves "depth capping" and neither
is what Jeremy is asking for:

1. `loop_post_step.py`'s continuation-pass respawn caps at
   `MARO_MAX_CONTINUATION_DEPTH` (env, defaults to `loop_types.MAX_RESTART_DEPTH
   = 3`) — when a loop's natural continuation-pass count hits the cap, it
   stops respawning and instead enqueues a `loop_escalation` task for the
   director to review (`loop_post_step.py:74-123`). This part already works
   and is out of scope for this doc.
2. `director.handle_escalation` (`src/director.py:980`) is what processes
   that escalation task. Its `continue` and `narrow` actions
   (`src/director.py:1114-1150`) enqueue a **brand new** `task_store` job
   (a fresh run, new run-dir) with `continuation_depth=depth+1` —
   **unconditionally**. Nothing here imports or checks `MAX_RESTART_DEPTH`.
   If the LLM keeps choosing `continue`, this recurses without bound. This
   is mechanism (2) — a chain of *sequential distinct goal executions*, not
   a single loop's retry count — and it's the one Jeremy means by "goal
   pass": pass 1 = the original task (`continuation_depth=0`), pass 2 = the
   first continuation (depth=1), pass 3 = the second continuation (depth=2,
   "2 goals deep beyond the first" — matches his wording exactly).

**This doc only touches mechanism (2).** Do not add a hard depth cap to
`loop_post_step.py`'s existing mechanism (1) — it's a different, already-
capped concern; conflating them would be scope creep past what was decreed.

## The decree (verbatim, GOAL_BRAIN Decisions 2026-07-13)

> once we are starting the 3rd goal pass (2 goals deep beyond the first),
> while maro is executing in the background towards that 3rd recursive
> goal, have the top level maro start a conversation with the user; explain
> it's going to take a while and explain the current plan it's begun
> working on, and how what it's done is working towards what the user
> asked; allows the user to guide or stop, but doesn't stop the goal until
> the user wants it to. Every 4-7 goals after that we do the same; progress
> update, interact with a chance to redirect or stop, and assume (ralph
> style) that we want to proceed optimistically.

Non-negotiable properties from this text:
- **Fires at depth==2** (starting pass 3), then again on a **jittered
  4-7-goal cadence** thereafter — not a fixed period (this repo's own
  "signals not rule tables" posture; matches the jittered spirit of #5
  planning-depth's "judgment inputs, not a rule table").
- **Never blocks.** The goal keeps running; the check-in is a notify, not a
  wait. This is the opposite of the existing `escalate` navigator move
  (which parks the goal pending human input) — do not reuse that code path
  or its "stuck" task-store status.
- **Optimistic default** (ralph-style): absent a user response, proceed.

## Mechanism

### 1. Depth/cadence state — ride the existing `origin` ancestry dict

`origin` already threads parent/goal/loop lineage forward through every
`continue`/`narrow` enqueue (`src/director.py:1124`, `:1146`:
`origin=task.get("origin") or {}`). Add two fields to it, read/written only
here:

- `origin["next_checkin_depth"]` (int) — depth at which the *next*
  check-in should fire. Absent/missing = `2` (first threshold, per decree).
- `origin["checkins_sent"]` (int, default 0) — count, for the summary
  composer and for picking the next jitter window.

At the top of the `continue`/`narrow` branches, before enqueueing:

```python
new_depth = depth + 1
origin = dict(task.get("origin") or {})
next_checkin = origin.get("next_checkin_depth", 2)
if new_depth >= next_checkin:
    _fire_checkin(task, new_depth, action, reasoning, summary_for_user, origin)
    origin["next_checkin_depth"] = new_depth + random.randint(4, 7)
    origin["checkins_sent"] = origin.get("checkins_sent", 0) + 1
```

Then pass `origin=origin` (the updated dict, not the original
`task.get("origin") or {}`) into the existing `_ts_enqueue(...)` calls at
`:1118-1125` and `:1140-1147`.

`random.randint(4, 7)` is fine here — this is production Python, not a
Workflow script; no restriction on `random`/`time` applies.

### 2. `_fire_checkin` — compose + deliver, never block

New helper in `director.py`, same file (`handle_escalation`'s neighbors —
`_ESCALATION_SYSTEM`, `EscalationDecision` — already live there; this is
the same kind of seam, not a new module). Signature and contract:

```python
def _fire_checkin(task, new_depth, action, reasoning, summary_for_user, origin) -> None:
    """Non-blocking progress notification at deep recursion. Never raises."""
```

Composes a payload from data **already available in this function** —
don't invent a new summarization LLM call for the MVP:
- the original goal (`task`'s ancestry — walk `origin` back to the root
  goal text if carried, else use `reason` from the earliest task this
  chain has; if only the current task's `reason` is available, use that —
  don't block the check-in on being able to reconstruct full lineage)
- `new_depth` and `origin["checkins_sent"] + 1` (which check-in this is)
  ("this is goal pass N")
- the director's own `reasoning`/`summary_for_user` from *this*
  escalation decision — it already explains "how what's been done is
  working towards what the user asked," which is exactly the decree's ask;
  don't re-derive it with a second LLM call
- an explicit line telling the user HOW to redirect/stop: via whatever
  inbound channel is live (Telegram/Slack/CLI — see §3), reminding them
  that no reply means "continue" (ralph-style default)

Delivery: `notify.emit("recursion_checkin", payload, run_dir=...)`
(`src/notify.py:90`). Wrap the whole thing in `try/except Exception: pass`
— same defensive posture as the existing `channel.notify_low_confidence`
call four lines above it (`src/director.py:1081-1089`) — a notify failure
must never affect whether the continuation gets enqueued.

**`notify.py` changes needed:**
- Add `"recursion_checkin"` to `DEFAULT_EVENTS` (`notify.py:44`) and to
  `ESCALATION_FILE_EVENTS` (`notify.py:58`) — it's exactly the "notify-
  worthy, easy to miss with no notify.command lane" class that set exists
  for, per the module's own docstring.
- Payload should include an explicit `"blocking": False` field so any
  consumer (a future `doctor.py` row, README docs, the human reading
  `escalations.jsonl`) can tell this apart from a real park-the-goal
  escalation at a glance — don't let it get confused with the `escalate`
  navigator move's semantics.
- `doctor.py`'s existing "Escalation file surface" row already reports
  path + row count for the whole file mixing all `ESCALATION_FILE_EVENTS`
  types — fine to leave mixed; a per-type breakdown is a nice-to-have, not
  required for this chunk.

### 3. Redirect/stop — reuse `InterruptQueue`, build nothing new

Jeremy's "allows the user to guide or stop" is **already infrastructure**:
`src/interrupt.py`'s `InterruptQueue` (source-agnostic: Telegram, CLI,
Slack, heartbeat all already post into it — see `telegram_listener.py`,
`slack_listener.py`, `listener_core.py`), and every loop already polls it
between steps (`loop_post_step.py:378-396`, wired at loop init in
`loop_init.py:246`). A `stop` intent halts the loop; a `corrective` intent
redirects it. **The continuation task this check-in fires alongside is
itself a normal task-store job that gets its own `InterruptQueue` when it
runs** — so once the continuation is dispatched, a user's Telegram/Slack/
CLI reply is picked up with zero new code.

Do not build a new inbound channel, a new "pending redirect" store, or a
synchronous wait for a reply. The only thing this chunk needs to do on the
inbound side is make sure the check-in's outbound text actually tells the
user which channel replies go through (whatever's configured — same
`notify.command` substrate the check-in itself rides) so they know the
affordance exists. That's a string in the payload, not a mechanism.

Known limitation, acceptable to leave undocumented-as-a-TODO rather than
solved here: there's a window between the check-in firing and the new
continuation task actually being dispatched (queue drain / heartbeat
cadence) where a reply posted to `InterruptQueue` has no active loop to
poll it yet. Out of scope — matches this repo's existing accepted-residual
posture (e.g. the pass-3 waiver-content-unjudged gaps), don't over-build.

## Tests

Extend `tests/test_escalation.py`:
- Flip `test_known_gap_continue_enqueues_past_max_restart_depth` per its own
  comment ("this assertion flipped... once a cap is added here") — except
  there's no cap being added, so update the test name/docstring to reflect
  what actually shipped: enqueue still happens (not refused — decree says
  "doesn't stop the goal"), AND a check-in fires. Don't leave it named
  "known_gap" once the gap is addressed; this repo's convention is to
  rename/close known-gap tests when their behavior changes (see how
  `test_known_gap_*` tests describe themselves as "a concrete artifact to
  flip... once its underlying gap is actually closed").
- New: check-in does NOT fire below the first threshold (depth 0→1, i.e.
  `new_depth=1 < 2`).
- New: check-in DOES fire exactly at `new_depth==2` on a fresh chain (no
  prior `origin["next_checkin_depth"]`).
- New: after a check-in fires, `origin["next_checkin_depth"]` lands in
  `[new_depth+4, new_depth+7]` inclusive; enqueue does NOT fire again before
  that threshold on subsequent continues.
- New: notify failure (mock `notify.emit` to raise) does not prevent
  enqueue — the continuation still happens.
- New: check-in payload includes `blocking: False` and the director's own
  `reasoning`/`summary_for_user` text.

## Docs/config follow-through (standing constraints, IMPLEMENTATION_HANDOFF.md)

- `docs/DEFAULTS.md`: if the `2` / `4-7` thresholds become config-tunable
  (recommended — e.g. `recursion.checkin_first_depth` default 2,
  `recursion.checkin_jitter_min`/`checkin_jitter_max` default 4/7), add
  rows with reasoning; census test (`tests/test_defaults_doc.py`) will
  catch a miss. If left as hardcoded constants instead, that's also fine —
  just note the choice in this doc's status line when closing it.
- `BACKLOG.md`: the "(i) restart-depth-cap coverage" entry and its
  known-gap test cross-reference need updating once this ships — it's
  resolved, not deferred, but resolved as "check-in and continue," not
  "hard cap." Update the prose so a future reader doesn't think a refusal
  was added.
- This doc closes to `docs/history/` (status: record) once shipped, same
  convention as the routing/probe-synthesis design doc — update the top
  status banner to say what actually shipped vs. what this pass proposed,
  and note any deviation.

## Explicitly NOT in scope for this chunk

- Do not touch `loop_post_step.py`'s or `handle.py`'s existing
  `MAX_RESTART_DEPTH` gates — they're a different mechanism, already capped,
  not part of the decreed behavior change.
- Do not build a synchronous "wait for user reply" path — the decree is
  explicit that the goal must not stop until the user says so.
- Do not add a second LLM call to summarize the plan — reuse the escalation
  decision's own `reasoning`/`summary_for_user` output plus the ancestry
  chain already in hand.
- Do not build new inbound-message plumbing — `InterruptQueue` +
  the listener modules already exist for this.
