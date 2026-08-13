---
status: record
---

# Adversarial review — async-tail phase 2 + visibility (7cba250, c928833)

*2026-08-13, 3-lens Codex review (Skeptic + Architect + Minimalist).
Reviewer CLI: `codex exec`, read-only sandbox. All three output files
present. 10th earning round for this arc's modules.*

## Intent

Answer-first notify at final-step compile with a durable `verdict_pending`
marker; verdict as a `run_verdict` follow-up; pending-aware curation class,
tripwire stand-down/regain, heartbeat crash-orphan sweep; tail cost lane
(`metrics.tail_cost_scope`) + per-call `cost_usd` + post-drain surfaces
refresh.

## Verdict: REJECT (all accepted findings fixed same session)

Strong consensus on three classes: delivery-result blindness (the exact
at-most-once-attempted class the morning's breaker review found — found
again, same day, in my own new code), repair that finishes the ledger but
not the user-visible story, and unscoped escalation-path tail spend.

## Findings (deduped, severity-ordered; all verified against the tree)

1. **[high] `notified_early` recorded regardless of delivery** (all three) —
   a configured hook failing transiently left the user's ONLY external
   message a terse verdict for an answer they never received. → Fixed:
   marker records `hook_configured` + `hook_delivered`; the finalize
   downgrades to a full `run_completed` when a configured hook did not
   deliver. Pinned.
2. **[high] Orphan sweep unreachable by default** (Skeptic) — it was wired
   into `_run_evolver_bg`, which the default health-only heartbeat never
   schedules. → Moved into the every-tick `stranded_state_sweep`
   (zero-LLM lane). Pinned.
3. **[high] Sweep didn't prove process death** (Skeptic, Architect) — the
   early close stamps `ended_at` while the tail legitimately runs; age
   alone would orphan a wedged-but-alive tail. → Metadata-pid liveness
   corroboration: recorded owner alive → skip, however old the marker.
   Pinned (live-owner test).
4. **[high] Repair finished the ledger but not the story** (Skeptic,
   Architect) — resolve-only branch skipped surface refresh; neither
   branch sent the owed follow-up; and the merge-only card refresh left a
   stale `verdict_pending=True` beside the repaired class. → Both branches
   refresh + emit the owed notify (routed like the finalize:
   answer-reached → `run_verdict`, else `run_completed`);
   `refresh_run_card_classification` now REMOVES pure-curator keys the
   rebuild omitted (fixes the whole stale-only-when-stamped-key class,
   not just this instance). Pinned.
5. **[high] Early publication not gated on the durable marker** (Architect)
   — a failed marker stamp still notified, so the tripwire fired
   never_stamped mid-tail and the finalize double-sent. → Marker stamp
   failure aborts the early lane (falls back to synchronous ordering).
6. **[medium] `answer_changed` compared stochastic renderings** (Minimalist,
   Architect) — with `curation.answer_synthesis` ON, two syntheses of the
   same run word the answer differently; every run would resend a
   "Revised answer". → Gated on an actual replacement loop shipping
   (metadata `loop_ids[-1]` differs from the marker's loop) AND text
   differing.
7. **[medium] Durable-write results ignored** (all three) — metadata stamp
   failures counted as `stamped`; marker left active while the counter
   claimed success. → Both stamp returns checked; failure leaves the
   marker ACTIVE for the next sweep (ledger re-stamp is idempotent).
8. **[medium] Escalation-path tail calls unscoped** (all three) —
   closure-restart re-verify, the gate-escalation early learning drain,
   post-escalate closure, and both `close_run` curations (answer
   synthesis) now run under tail scopes. The escalation re-run itself
   stays outside (its executor calls are excluded at the seam anyway).
9. **[medium] `run_verdict` events.jsonl projection was empty** (Architect)
   — polling substrates got type+goal only. → The verdict facts
   (achieved/source/answer_changed/summary + handle_id) ride the detail
   field.
10. **[low] Sweep held the shared repair lock during a full-history scan**
    (Architect) — candidates now collected lock-free; the common
    no-orphans case never takes the lock; candidates re-verified under it.

## Rejected / accepted-as-residual, with rationale

- **"missing" ledger status treated as success** (Architect 6): stands by
  design — no outcome row exists (death before reflect_and_record), so
  there is nothing in the ledger to mislead learning; the metadata stamp
  is the durable record. Documented at the site.
- **Cross-store transactional lock shared with closure** (Minimalist 2,
  Architect 2): no cross-store transaction exists anywhere in this
  codebase; the sweep now re-reads metadata immediately before stamping
  (window shrunk to ms) atop the 1h grace + pid-dead corroboration.
  Residual named here rather than inventing a new locking regime.
- **Full outbox/event-sourcing for notify state** (Architect 1, 3): the
  marker now carries the two delivery facts the routing needs;
  a durable outbox is machinery beyond observed need (subtract-before-
  you-add) — revisit if a third delivery-state consumer appears.

## What went well

- The core ordering pin (answer before closure, one completion, verdict
  after) survived all three lenses untouched.
- The tripwire stand-down/regain design and the resolve-before-finalize-
  close ordering were called out as correct by two lenses.
- The executor-exclusion guard on the tail cost seam held under
  adversarial double-count probes.
