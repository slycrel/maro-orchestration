---
status: dormant-design
---
# Prerequisite declaration as a constrained field (PCD groundwork, item 3)

*Written 2026-09-16 after the LoopsBench analysis run (0b0a8fb6) and the
same-day chunk that shipped the prerequisite gate (`src/step_gate.py`) and
regression obligations (`src/regression_ledger.py`). This is groundwork for
Jeremy's item 3 — "lay the proper groundwork for 3 for later" — not a build
order. Read for intent; verify against the code before acting.*

## The measured gap

`scripts/prereq-census.py` (read-only over `projects/*/NEXT.md`, 2026-09-16
on the maro box):

| plan items | carry `[after:N,M]` | explicit-edge rate |
|---|---|---|
| 1537 | 438 | 28.5% |

71.5% of every step this box ever planned sits on the planner's *sequential
default* — "untagged step N depends on N-1" (`planner.parse_dependencies`).
That default is a guess, not a declaration: it is right for a linear recipe
and wrong for every independent branch the planner did not bother to tag.
The gate shipped today therefore enforces only the 28.5% it can trust
(explicit edges are hard; implicit edges are soft, logged, flip-able via
`execution.gate_implicit_prerequisites`). The LoopsBench run's verdict —
"explicit-when-authored, else silently absent" — is now a measured 71.5%
absent, which is what makes this a representation problem worth a
structured field rather than a prompting nudge.

## What "done" means

Every step carries a prerequisite declaration produced by a constrained
decision, so the implicit class does not exist:

- `after: []` means *declared independent* — the step may run whenever the
  loop reaches it, and a blocked predecessor does not gate it.
- `after: [k, …]` names prior plan numbers; the gate is uniformly hard.
- No third state. A step with no declaration is a parse failure, not a
  sequential default.

When that holds, `execution.gate_implicit_prerequisites` has no members to
govern and is retired (remove, don't disable — `feedback_good_system_citizen`).
That retirement is the observable "this shipped" signal.

## Two stages, cheapest first (subtract-before-you-add)

**Stage A — planner-native, no new model.** The planner already emits a JSON
list of strings and parses `[after:]` as a trailing tag. Accept an object
form alongside it — `{"text": "...", "after": [1]}` — and make the planner
prompt require `after` on every step, with `[]` as the explicit way to say
independent. `parse_dependencies` maps the object form onto the same
`deps` dict; the string form keeps working for old checkpoints and
resumes. Measure with the census after a week of organic runs: if the
explicit rate reaches ≥ 90% with no drop in plan-review acceptance, Stage B
is not needed *for this field* and the PCD experiment should pick a
different dimension (verdict, recovery) as its first target.

**Stage B — PCD decision layer.** If the planner keeps guessing (rate stays
under ~60%, or declared edges are wrong when spot-checked), route
prerequisite assignment through a constrained decoder: given the emitted
step texts, score one orthogonal dimension — `prerequisites: subset of
{1..N-1}` per step — on a small local model (Qwen-2.5-1.5B 4-bit, ~400 ms
per decision per PCD_SCHEMA_DESIGN.md) or Jev hosted (70–500 ms,
$0.042/MTok), validated deterministically: backward-only references,
acyclic, every step declared. This is the one place PCD's orthogonality
claim (4/8 → 9/10 when fields were split) maps onto a gap we measured; it
is *not* a plan-quality layer and must not grow into one.

## Prerequisites for Stage B (the honest list)

- **Validation corpus first.** PCD_SCHEMA_DESIGN.md names a 14-case corpus
  that was never written. For this field the corpus is 20–30 real plans
  from `projects/*/NEXT.md` with hand-labelled `after` sets, split
  linear / branchy / mixed. No corpus, no experiment.
- **A place to run it.** Linux MLX is unverified on this box; the M6 mini
  (~2026-10-07) is the local target. Until then Stage B can run on the
  hosted-free validation ladder (`project_local_validator_box_test`:
  gemini-flash-lite → groq) — same ladder, same latency breaker, no new
  spend path.
- **The gate as consumer.** `step_gate.prerequisite_verdict` already reads
  edges from the step text first and the `deps` map second; a declared
  `after` list lands in `deps` and needs no gate change. The parallel/DAG
  lane reads the same `deps`.

## Falsifiers

- Stage A alone reaches ≥ 90% explicit with sound edges → Stage B is
  unnecessary here; record and move the PCD target.
- Declared edges are *wrong* more often than the sequential default was
  (spot-check: a step that reads a file the "independent" step writes) →
  the representation is not the problem; stop.
- Regression obligations (`regression_ledger`) catch the breakage that
  mis-declared independence causes → the two shipped instruments already
  cover the cost, and the field is a latency/cost optimisation, not a
  correctness one. Re-rank accordingly.

## Pointers

- LoopsBench (Microsoft, arXiv:2608.00267v2) — run 0b0a8fb6's
  `artifact/loopsbench-analysis.md`; the 25% resolve rate is verified, the
  "67.4%" figure is not in the paper.
- PCD design docs — `projects/saw-this-in-discord-and/` (run 944a67f7):
  PCD_SCHEMA_DESIGN.md, PCD_ORTHOGONALITY_ANALYSIS.md, pcd_schema.json,
  pcd_decision_point_mapping.json, jev_spec_notes.md.
- BACKLOG "PCD + Formal Methods" (hermes, cdf4ec9b) — the parent item.
- Shipped today: `src/step_gate.py`, `src/regression_ledger.py`,
  `scripts/prereq-census.py`, DEFAULTS rows
  `execution.gate_implicit_prerequisites`, `regression.enabled`.
