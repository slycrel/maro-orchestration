---
status: record
---

# Settled-chunks batch — post-land adversarial review (2026-07-29)

Combined cross-model review of the four trio-triaged chunks, per the
house workflow (3 Codex lenses — Skeptic / Architect / Minimalist — via
`codex exec`, batch diff = 03a485a envelope delivery, 1311db9 certainty
receipts, 62cbdca NOW retry rung, 2572fff packaging readout; 1616-line
diff). Seat outputs at /tmp/adversarial-review.2V0YHR/ (session
scratch; this record is durable).

## Intent

Each chunk's intent as triaged (delivery block for envelope dispatches
only with verbatim ask; receipts render-only; one seeded NOW retry with
full dual recording; report-only packaging readout with input honesty).
The intents were operator-decided; the review challenged execution.

## Verdict: PASS with fixes

No high-severity findings; 6 distinct findings after dedup (11 raw), 0
hallucinated code claims — every cited line was real (one sub-claim
wrong, noted below). Eleventh round; hallucination streak holds.

## Findings (deduped, severity order) and lead judgment

1. **[medium, 3/3 lens consensus — ACCEPTED, FIXED]** Errored-retry
   escalation context claimed "TWO attempts... both were judged NOT"
   when the seeded retry ERRORED and was never judged
   (handle.py `_rung_fired` set pre-judgment, escalation text keyed on
   it). Real: verified directly. Fix: `_rung_retry_judged` flag — the
   two-attempt claim now requires a judged retry; an errored retry gets
   an honest "(an artifact-seeded retry was attempted but errored out
   before it could be judged)" note. Pinned by
   `test_errored_retry_escalation_context_stays_honest`.
2. **[medium, 3/3 convergence — ACCEPTED IN PART, doc + BACKLOG]**
   Dispatch→outcome join by 120-char goal prefix can merge distinct
   goals sharing a prefix. Architect's variant ("dispatch side never
   normalized / ellipsis mismatch") is WRONG — verified the writer
   stores `goal[:120]` plain (persona.py:985), same rule as the join
   key. The collision risk stands. Fix: join rule + collision semantics
   now named in the render's coverage line; durable fix (stamp
   handle_id at dispatch write) BACKLOG'd under the next-leap arc.
3. **[medium, 2 lenses — ACCEPTED, FIXED]** `--persona` filtered the
   cells but left workspace-wide coverage posing as the persona's
   evidence. Fix: scoped reports stamp `scoped_to_persona` and the
   render labels coverage as workspace-wide. Pinned by
   `test_scoped_report_labels_workspace_wide_coverage`.
4. **[medium, Skeptic — REJECTED as slice-1 design, mitigated]**
   `would_include` ignores the cell's own outcome verdicts. Deliberate:
   buckets are skill-level evidence, the cell mix is persona-level
   evidence, and folding them into one verdict is slice-2 policy the
   report-only slice must not pre-decide (the trio's own readout-first
   rationale). Mitigation shipped: all-failed cells now render an
   explicit CAUTION line; design note added to the module docstring.
5. **[low/medium, 2 lenses — ACCEPTED, FIXED]** Delivery `you_asked`
   silently fell back to the truncated display goal for legacy envelope
   recs. The "…" marker made it visibly lossy, but the renderer
   couldn't tell programmatically. Fix: `verbatim: bool` flag on the
   delivery block + legacy-rec test + DISPATCH_ENVELOPE.md note.
6. **[low, Architect/Minimalist — ACCEPTED IN PART]** Cell header mixed
   goal and outcome-row denominators (label fixed: "outcome rows:");
   exclusion threshold is invented policy (kept — a report needs a
   bucketing rule — but now explicitly labeled READOUT-ONLY with the
   circuit breaker named as the runtime's real negative signal).

## What went well

Data-retention handling in the rung (both attempts recorded, suffix
param) drew no findings across three lenses; the receipts chunk drew
zero findings; the readout's input-honesty posture (dead-join
reporting, named caps) was left standing by all three reviewers.

## Side-finds during close-out (mine, not the reviewers')

Two census tripwires caught this batch's own gaps in the full suite:
the triage record lacked fenced `status: record` frontmatter, and
`packaging_readout` was missing from pyproject py-modules. Both fixed
in the fix commit — the tripwires did exactly their job.
