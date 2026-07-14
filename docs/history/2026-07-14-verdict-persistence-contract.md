---
status: record
---

# Verdict-persistence contract checkpoint — 2026-07-14

## Intent

Replace the ambiguous boolean outcome-verdict persistence seam with one atomic
typed result that distinguishes `updated`, `missing`, and `write_failed`;
centralize bounded idempotent retry; migrate every caller and delete the legacy
API; preserve existing delivery behavior except for the already-hardened
rejected-attempt boundary.

The owner-facing behavior when a *delivered* attempt's stamp fails remains a
separate decision under BACKLOG EXT-AUDIT-2. This checkpoint supplies the
mechanism and evidence needed to implement that policy once chosen.

## Implementation

- `OutcomeVerdictStampResult` carries status, attempt count, and the final
  persistence error. Boolean coercion raises `TypeError`, making the former
  `if stamp(...):` ambiguity structurally impossible.
- `stamp_outcome_verdict()` holds the append-compatible file lock across row
  lookup and atomic publish. Missing files/rows are neither created nor
  rewritten. `OSError` retry is bounded and re-reads fresh state.
- Every production writer and CLI caller migrated to the typed seam;
  `annotate_outcome_verdict()` was deleted rather than retained as a dual API.
- The closure-restart boundary requests two attempts, permits `missing`, and
  refuses on any other non-success state. Its operator/run-metadata diagnostic
  includes the final error and attempt count.

## Verification

- Focused persistence/closure/CLI suites passed after implementation and again
  after review fixes.
- The canonical `.venv/bin/python -m pytest -q` suite passed twice at 100%; six
  environment-dependent tests skipped and only the existing Python 3.14 tar
  extraction deprecation warning remained.
- Tests cover updated/missing distinction, absent-file no-create behavior,
  transient failure then convergence, bounded repeated failure, forbidden bool
  coercion, both missing-row integration calls, and fail-closed run metadata.

## Opposite-model adversarial review

Claude Sonnet ran through the real `adversarial-review` skill. Raw stdout,
stderr, and status files are retained locally under `output/` with names
`{skeptic,architect,minimalist,failure-operator,architect-followup*}`.

Initial reviewer statuses were all 0 and all stderr files were zero bytes:

- Skeptic: APPROVED.
- Architect: NOT APPROVED; one HIGH structural footgun (typed results were
  unconditionally truthy), one MEDIUM integration-coverage regression, and
  several LOW documentation/diagnostic findings.
- Minimalist: APPROVED, while asking that stale docs, precise integration
  assertions, and unreachable fallback code be fixed.
- Failure Operator (bonus): APPROVED; confirmed lock compatibility,
  idempotence, no-create-on-missing behavior, and fail-closed restart handling.

Per the skill's verdict logic the initial synthesis was **CONTESTED**: one HIGH
finding without reviewer consensus. Lead judgment accepted the truthiness
footgun, integration evidence gap, stale architecture skill, broken docstring,
dead fallback, test-only convenience property, missing attempt diagnostics,
and unproven “row present” wording. It rejected adding speculative retry sleep
and rejected restoring the preflight read, whose extra lookup avoidance was the
TOCTOU defect this redesign removed.

All accepted findings were fixed. The first focused Architect follow-up hit its
$0.80 cap, produced status 1 and no review content, and remains recorded as an
incomplete attempt. One bounded retry at the previously proven $1.20 cap
returned status 0 and **APPROVED**, explicitly confirming that the high and
medium findings were closed and that no HIGH or MEDIUM defect remained.

## Remaining decision

The delivered, provenance, and post-escalation writers now share the same typed
result but intentionally preserve their prior best-effort response. EXT-AUDIT-2
still needs the product decision: warn but deliver, demote the process result,
or another repair/convergence contract when completed work cannot persist its
audit stamp.
