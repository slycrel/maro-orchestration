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
  stamp error. A future/manual reconciler can replay that idempotent patch;
  until then the outcome row remains `deferred` and existing pending-verdict
  policy excludes it from learning and success scoring.
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
Automated repair convergence remains an explicit backlog follow-up; the shipped
owner decision is durable repair metadata, not a queue.
