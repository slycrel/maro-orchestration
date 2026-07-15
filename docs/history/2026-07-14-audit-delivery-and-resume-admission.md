---
status: record
---

# Audit-incomplete delivery and resume admission — 2026-07-14

## Decisions

Jeremy approved two owner-policy choices:

1. If genuinely completed work cannot persist its outcome-verdict audit stamp,
   preserve and deliver the work with a prominent `AUDIT INCOMPLETE` warning.
   Do not learn from the unresolved row. Persist an exact repair record when
   run metadata remains writable.
2. If a second `maro resume` targets a run already being resumed, refuse it
   immediately and notify the operator. Do not wait or queue.

Rejected or superseded attempts remain different: their negative audit must be
durable before replacement work begins, so that boundary continues to fail
closed.

## Delivered-verdict contract

`audit_policy.persist_delivered_outcome_verdict()` is the shared owner-facing
seam for Handle, post-quality-gate retries, provenance demotions, `maro run`,
and `maro resume`.

- `updated`: continue into deferred learning.
- `missing`: honest optional absence; continue without inventing evidence.
- `write_failed` or exception: retry is already bounded at two attempts, keep
  delivery status unchanged, warn in the returned result/CLI/channel, and do
  not finalize deferred learning for that outcome row. Independently audited
  earlier attempts in the same handle may still extract their own lessons.
- On failure, active run metadata records `audit_incomplete`,
  `audit_repair_required`, all loop joins, and the exact intended verdict plus
  stamp error. The reconciler described below replays that idempotent patch;
  until it succeeds, the outcome row remains `deferred` and existing
  pending-verdict policy excludes it from learning and success scoring.
- If repair metadata also cannot persist, that second failure is included in
  the user-visible warning rather than silently claimed as recoverable.

Direct CLI lanes now opt into deferred learning so this policy is enforceable
there instead of attempting closure after learning has already occurred. They
construct and share the same worker adapter with execution and deferred
learning; an adversarial review caught and prevented an adapter-less path that
would have persisted synthetic dry-run lessons on real runs.

Curated run cards retain goal outcome and audit health as separate axes. The
neutral learnability policy rejects either audit flag before considering the
card's otherwise-successful status/verdict metadata.

## Resume admission contract

The admission unit is the durable `handle_id`, falling back to `loop_id` for a
legacy checkpoint. It is intentionally not project-wide: the mutable collision
surface is one run's checkpoint, metadata, reports, artifacts, and remaining
external side effects.

`maro resume` takes a nonblocking, fail-closed flock before status/PID checks,
then reloads the checkpoint under the lock,
and holds it through run finalization. Holder JSON includes PID, handle, loop,
command, and start time. A collision exits with `E_RESUME_BUSY`, performs no
loop work, and emits `resume_refused_busy`; that event is default-notified and
also written to the durable escalation file. Process death releases the lock
in the kernel. Lock contention and lock-store failure are typed separately:
the latter exits `E_RESUME_LOCK` and emits `resume_lock_unavailable`, never a
fabricated busy diagnosis.

When a resume finishes `done`, its new run/checkpoint is durable and the source
checkpoint is consumed. This prevents a later invocation of the same legacy
loop id from replaying the original remaining-step snapshot.

The lock's approved scope is resume-vs-resume. The pre-existing between-step
live-original detection gap needs a run-lifetime lease shared with
`run_agent_loop`; it remains explicit in BACKLOG rather than being hidden under
this checkpoint.

## Audit repair reconciler

`maro-runs repair-audits [handle-or-loop] [--limit N]` consumes exact
per-loop records from the canonical `audit_repairs` queue (`audit_repair`
remains the latest-record compatibility view) under one workspace-wide
nonblocking pidfile. The same
finite sweep runs before the evolver on an autonomy-enabled heartbeat cadence
(up to three records); there is no new daemon or timer. A busy manual/background
sweep skips immediately. Lock-store failure is surfaced separately and never
permits overlapping paid work.

Each record is treated as untrusted persisted input. The reconciler validates
kind, a required non-empty loop join, boolean/null verdict, non-empty source, and finite 0..1
confidence before calling the typed idempotent stamp seam. Missing outcome rows,
write failures, malformed records, and unavailable adapters remain quarantined
with durable transition/failure status instead of being guessed or fabricated.
Invalid/missing records stop automatic retry immediately; other failures stop
after five automatic attempts and require an explicit targeted retry, preventing
an unattended LLM-spend loop. An exhausted sibling keeps the run quarantined
and sets the run-level status to `manual_required`; a later loop that repaired
successfully still aligns the latest delivered verdict in run metadata and the
sweep reports the unresolved manual state rather than false success. Fair
scheduling uses persisted attempt time, not
run-directory mtime, so failed metadata rewrites cannot starve other records.

After verdict persistence, only the named outcome row resumes deferred lesson
and knowledge extraction. The real cheap-tier adapter is created lazily, so a
verdict-only repair makes no LLM call and an adapter failure stays retryable.
Skill crystallization is deliberately not reconstructed: it depends on
ephemeral `StepOutcome` values that the repair record does not durably retain.
Inventing them would violate the audit's evidence boundary.

Only finalized runs are eligible. Multiple failed loops in one run remain
separate: completing one cannot clear a sibling's quarantine, and the latest
delivered loop's repaired verdict is merged into run metadata before the
classification card is refreshed. All metadata writers share the same locked
RMW boundary, so a live writer cannot clobber the repair transition.

Completion uses a crash-safe `surface_pending` checkpoint: first the ledger and
learning are durable, then run metadata clears quarantine and marks derived
surface work pending, then classification-only run-card refresh and static
reports run, finally metadata records `completed`. A retry from that checkpoint
does not replay verdict or learning. Classification refresh never re-runs
trust-bearing run-curation maintenance such as skill promotion.

## Verification

Fault-injection coverage includes typed false returns, exceptions, absent rows,
metadata failure, channel warning, ordinary delivered learning suppression,
post-escalation learning suppression, direct-CLI deferral, concurrent resume
refusal/holder identity/notification, and lock release after completion.
The six-persona Claude pass rejected the first draft; accepted fixes covered
adapter fidelity, curated-card quarantine, typed lock outcomes, checkpoint
reload, holder JSON validation, declared audit result fields, and distinct
operator events. The focused follow-up additionally caught legacy checkpoint
replay and handle-wide over-quarantine; both were fixed with source consumption
and per-loop learning suppression. The final Skeptic pass verified both fixes
and caught a consume-failure status snapshot ordering bug; JSON output and run
metadata now share the fail-closed `incomplete` status, with regression coverage.
The subsequent reconciler pass added fault coverage for idempotent repeat,
learning/backend failure, no-adapter refusal, verdict failure, missing outcomes,
cross-run metadata, crash recovery at `surface_pending`, targeted loop lookup,
CLI status/exit behavior, malformed reconciliation history, live-run exclusion,
poison-record fairness, multi-loop quarantine, run-metadata verdict alignment,
and corrupt-card rebuilding.
