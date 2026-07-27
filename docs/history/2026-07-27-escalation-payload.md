---
status: record
---

# §9.6 escalation payload — simple-first (2026-07-27)

**Status: SHIPPED.** Implements Jeremy's chunk-9 agenda item-3 decision
(GOAL_BRAIN Decisions 2026-07-27, verbatim): "Escalation payload: simple
first — single-chasm decision + one family-ROI context line; complex
later."

## What an escalation now carries

Before this chunk an escalation told the human *that* the system was
stuck (summary + reason + point). It now also tells them *what to
decide* and *whether this chasm is a pattern*:

- **`decision`** — one deterministic ask, templated per emit point
  (`src/escalation_context.py::decision_line`). Blocked step: "Decide
  this chasm: a step is blocked — {reason}. Options: re-send the goal
  with guidance, or drop it." Dispatch (run prevented): adjust/re-send
  or drop — no "resume" verb for a run that never started. Director
  escalation: the director's own summary framed as the ask. Unknown
  points get a generic-but-honest "Decide: {reason}" rather than "".
- **`family_roi`** — one recurrence line keyed on the Phase 44
  `diagnose_loop` failure_class taxonomy
  (`escalation_context::family_roi_line`): "Family context:
  'retry_churn' has 3 prior diagnoses on record, 2 in the last 30
  days." First occurrence is signal too ("first ... on record").
  Silence over noise: empty/healthy class or unreadable ledger → no
  line. Pre-V3 unstamped rows count all-time but not the window.

Both helpers are pure/deterministic — no LLM calls, no config keys, no
spend. The fields ride the existing `escalation` notify event
additively, so every consumer sees them (durable
`output/escalations.jsonl`, `notify.command` hook JSON, telegram) and
none requires them.

*Post-review note (same day, see
`2026-07-27-escalation-payload-adversarial-review.md`):* the family
count now covers the whole ledger via raw rows (the as-shipped
`load_diagnoses(limit=200)` capped it), an existing-but-unreadable
ledger renders silence rather than "first on record", and the
blocked_step diagnosis consult passes `emit_log_event=False` so
enrichment never writes a captain's-log event.

## Emit sites wired (all three)

| Site | Point | Payload |
|---|---|---|
| `loop_blocked._navigator_act_blocked_step` | `blocked_step` | decision + family_roi (family key = mid-run `diagnose_loop(loop_id).failure_class` — pure heuristics, mid-loop safe) |
| `handle._navigator_act_dispatch` | `dispatch` | decision only — no loop ran, so no diagnosis to key a family line on (omitted, not faked) |
| `handle_queue.handle_task` | `director_escalation` | decision (from `summary_for_user` or reasoning) |

Each wiring sits in an inner try/except: enrichment can never break the
emit. The decision ask is computed before the diagnosis consult, so a
diagnosis failure costs only the family line.

`notify_telegram` escalation rendering now leads with the decision line
when present (it IS the ask); summary demotes to a `Detail:` line and
the family line rides after. Payloads without `decision` render exactly
the pre-§9.6 shape.

## Tests

`tests/test_escalation_context.py` (template pins per point, hostile
input never raises, family counts/window/grammar, unstamped-row and
unreadable-ledger behavior — seeded via real `save_diagnosis` into the
per-test tmp workspace); `test_blocked_step_cutover.py` end-to-end
payload pin + diagnosis-failure survival; `test_handle.py` dispatch
payload pin (decision present, `family_roi` absent);
`test_notify_telegram.py` decision-leads rendering + no-Detail-dupe +
legacy shape preserved.

## Deliberately NOT built ("complex later")

Capability-investment scoring, cross-family ROI comparison, side-quest
recommendation at the escalation surface ("scientific-method thinking at
the failure boundary" — Jeremy's open question in the same Decisions
entry), and any new persistence. The family line reads the existing
diagnoses ledger; nothing new is written.
