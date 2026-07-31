---
status: record
---

# Adversarial review — system self-health lane v1 + audience adornment (2026-07-30)

Post-land review of commit `2c34742` per house discipline: 3 Codex
lenses (Skeptic / Architect / Minimalist, `codex exec`, read-only),
verify-before-fix on every claim. Tenth-plus round in the series.

## Intent

(a) Captain's-log dual contract — `audience` stamp at write,
`USER_SURFACED_EVENTS` = user-narration lane, system lane = immutable
queue-readable stream, retroactive `event_audience()` fallback,
run-report + CLI consumers. (b) `src/system_health.py` —
DECLARED_PROCESSES liveness registry, 6 deterministic report-only
probes riding loop_finalize, state in `memory/system_health.json`,
SUBSYSTEM_SILENT/RECOVERED transitions edge-triggered on narration
state. Report-only; never blocks a run.

## Verdict: CONTESTED → remediated same session

8 deduplicated findings, **8/8 verified real, 0 hallucinated** (another
clean round). Two carried cross-lens consensus. All fixed in the
follow-up commit; plus one self-found live bug (CLI limit-before-filter)
caught during post-land smoke before the reviewers returned.

## Findings (all verified, all fixed)

1. **[high] Snapshot RMW unlocked** (Skeptic + Architect consensus) —
   `run_health_probes` did load→mutate→`atomic_write`, and
   `file_lock.atomic_write` explicitly does NOT take the lock ("pair
   with locked_write()/locked_rmw() when concurrent writers are
   possible"). Concurrent finalizers could both read `narrated=None`,
   double-narrate, then last-writer-win the history. *Fix:* the whole
   cycle now runs under `locked_write(_snapshot_path())`; probes only
   read other stores, so no lock-ordering cycle exists.
2. **[medium] Narrate-before-persist** (Skeptic) — transitions were
   logged before the snapshot write; a failed write meant the log said
   "told the user" while the state machine forgot, re-narrating every
   cycle. *Fix:* transitions collect during the loop and narrate only
   after `_write_snapshot` succeeds. Accepted reverse trade (write
   succeeds, log append fails → line lost, snapshot still shows
   SILENT), documented in code.
3. **[medium] Ambient loop_id contamination** (Architect) — probes run
   inside `loop_id_scope(ctx.loop_id)` from agent_loop, so
   SUBSYSTEM_* events inherited the finishing run's loop_id and showed
   up as that run's *attributed* report entries (which bypass the
   audience filter by design). *Fix:* `_narrate_transition` wraps
   `log_event` in `loop_id_scope(None)` — global process state, not
   run evidence.
4. **[medium] Closure probe count-compare misses window slide**
   (Skeptic) — old unverdicted run scrolls out of the 50-row window as
   a new one appears: same count, brand-new silent failure, probe said
   OK. *Fix:* identity tracking (`unverdicted_ids` in observations;
   new id = growth), with baseline treatment for pre-id history.
   Follow-on self-catch during the fix: acknowledged growth flipped
   OK next cycle → SILENT/RECOVERED ping-pong under ongoing breakage;
   the alarm now holds while growth is within the streak window, so
   RECOVERED means "no new unverdicted runs for 3 cycles".
5. **[medium] Variant probe permanent false negative** (Minimalist) —
   `variants > 0 → OK` forever: one historical variant would mask a
   generator that broke later. *Fix:* cumulative SKILL_VARIANT_CREATED
   event count; SILENT needs count frozen across the streak AND last
   creation older than VARIANT_STALE_DAYS=7 (evolver-cadence grace).
   Live-fire side-find: 696 SKILL_VARIANT_CREATED events exist in a
   2026-04-10 archive — all test-fixture leakage (subject "test",
   parents "parent-1"/"p"; same leak class as the sweep's use_count
   fixtures). The probe tolerates them honestly: count frozen since
   April + stale ⇒ the coming SILENT (once the streak fills) states
   the true condition, and flips OK on the first organic variant from
   the un-starved gate.
6. **[medium] Receipts probe false SILENT from stale citation**
   (Minimalist) — "any cited run in the last-20 window" stays true for
   cycles after one old citing run; frozen sum then alarmed though
   nothing new was owed. *Fix:* track `last_cited_loop`; SILENT
   requires the citing run to have *changed* (or first citation to
   appear) while the sum stays frozen.
7. **[low→medium once doc'd] `--audience` ignored on `--events` and
   `--timeline`** (all three lenses) — the events doc explicitly
   promises `maro-log --events --audience user|system`. *Fix:*
   `event_slice` gained an `audience` param (filter before sort/limit);
   `--timeline --audience` now errors explicitly (aggregate counts are
   not lane-filtered).
8. **[low] `--limit 0` broke audience mode** (Minimalist) — documented
   as unlimited, but the audience slice `[:0]` emptied it; also
   pre-existing: `load_log` itself returned nothing at limit 0 in
   default CLI mode. *Fix:* `load_log` limit 0 now means unlimited
   (matching query_log/event_slice); audience slice skips when limit
   is 0.

Self-found (pre-review, live smoke): CLI applied `--limit` at fetch, so
`--audience user --limit 3` meant "user-lane events among the newest 3
of everything" — empty on a real log. Fixed: fetch wide, filter, then
limit.

## What went well

Reviewers found no fault with: the state-in-store/transitions-in-log
split, the narration-state edge-trigger design itself (the UNKNOWN→
SILENT fix predated the review), probe shielding/UNKNOWN semantics,
killswitch behavior, data-retention handling (unknown snapshot keys
survive), or the audience registry membership.

## Lead judgment

All 8 accepted — each verified against the tree before fixing, none
style-only. The pair worth naming: F1 (unlocked RMW) is the exact
concurrency-hardening lesson this repo already wrote down
(`atomic_write` docstring says so); F5's "old success masks new
breakage" is the same lesson the probe itself was built to teach —
liveness is about *recent* execution, not lifetime existence. Every
fix carries a pin test citing the finding.
