# Behavior suite — the executable workspace contract (Phase 1b)

**This suite outlives the Python engine.** It is the black-box behavior
spec of the execution backbone, written at the WORKSPACE BOUNDARY: a goal
(or a registered store-ingress call) goes in; the assertions read only the
on-disk artifacts that come out — run dirs, `metadata.json`, call records,
ledgers, the playbook, the logs — against the shapes registered in
`docs/CONTRACTS.md` (cited per assertion as B1–B12). It is simultaneously:

- (a) the executable spec of the backbone's observable behavior,
- (b) a flaw-finder against the Python engine, and
- (c) the Go successor's acceptance tests: a second engine passes this
  suite by producing the same files, not by sharing any code.

**Hard rule:** no assertion may touch Python internals, module state, or
return objects. The only engine-specific code is the driver layer in
`harness.py` (`drive()` + the store-ingress calls inside individual
tests); a Go conformance harness replaces that layer and keeps everything
else, including the data-first scenario table.

## Layout

| File | Role |
|---|---|
| `harness.py` | Driver (handle() + ScriptedAdapter) and artifact readers; the cross-cutting `assert_common_contracts` every goal scenario must pass |
| `scenarios.py` | Data-first table: goal text + scripted adapter responses + expected workspace artifacts (plain dataclass rows a Go harness can consume) |
| `test_behavior_run_lifecycle.py` | Table runner + call records (B4), stuck path, metadata merge-on-write + corrupt-park (B3) |
| `test_behavior_now_lane.py` | NOW answer artifact (B3), re-run identity (B11) |
| `test_behavior_memory.py` | Outcomes tri-state + closed vocabularies (B6), post-hoc verdict stamping + unknown-key round-trip, lesson stores (B7), paused-family vocabularies |
| `test_behavior_logs.py` | Playbook grammar (B10), captains-log rotation + run slice (B8), events line discipline (B9) |
| `test_behavior_config.py` | Config resolution: env pin, tier merge, malformed→{} (B1) |
| `test_behavior_curation.py` | Run-card shape, success_class grid, `_curation` namespace, derived-view refresh (B5) |

A scenario = goal + scripted adapter responses + expected workspace
artifacts. All scenarios run on scripted adapters (no network, no LLM
spend, no sleeps); the whole suite runs in a few seconds and is part of
the normal suite (no `@slow`). Workspace isolation comes from
`tests/conftest.py` (per-test tmp `MARO_WORKSPACE`); the harness
additionally asserts the resolved workspace is never the live one.

## Scenario coverage (the ~15)

1. **NOW one-shot** (`now-one-shot`) — intake row B11, run dir + verbatim
   prompt B3, answer artifact, unjudged outcome row B6, card B5, logs
   B8/B9.
2. **AGENDA happy path** (`agenda-happy-path`) — plan → steps → done;
   skeleton + lifecycle fields + loops lineage B3.
3. **Call records** (`now-call-records` + `test_call_records_shape`) —
   B4 field-by-field, seq monotonic/unique from 1, filename==seq, no
   reader-visible temp debris.
4. **Outcome tri-state ingress** — required keys, absent-not-null verdict,
   omit-when-empty, malformed-verdict refusal (B6).
5. **Verdict stamping** — goal-driven half: `now-provenance-verdict`
   (deterministic provenance judge → verdict tuple in metadata B3, judged
   outcome row B6, `done-not-achieved` card B5, closed `stop_verdict`);
   store half: post-hoc `stamp_outcome_verdict` with unknown-key
   round-trip, `verdict_history` append, invalid-stamp refusal (B6).
6. **Blocked/stuck path** (`agenda-stuck`) — status vocabulary, failure
   recorded not fabricated, card never `success` (B3/B5/B6).
7. **Lesson stores** — tiered + flat ingress shapes, shared `lesson_id`,
   dedup-reinforce, unknown-key survival through a tier rewrite (B7/C0.1).
8. **Playbook grammar** — one-line entries, header-spoof collapse, section
   anchoring, dedup, ~500-char clip, alarm-key replace-in-place (B10).
9. **Captains-log rotation + slice** — archive sibling, zero loss,
   retained tail; byte-offset slice into `build/`, filter-by-handle_id
   (B8).
10. **Events line discipline** — every line one JSON object ≤4096 bytes,
    hostile strings/numerics capped/coerced/dropped-and-named, no
    NaN/Infinity on the wire, no silent event loss (B9/B2).
11. **Re-run identity** — same goal twice → joinable intake rows; the
    re-run brief resolves the first attempt for the second (B11).
12. **Config resolution** — `MARO_WORKSPACE` pin, workspace-over-user,
    one-level nested merge, malformed file reads as `{}` (B1).
13. **Run-card curation** — identity echo, success_class grid,
    `_curation` writer-private namespace, derived-view refresh converges
    to metadata (B5).
14. **Interrupt/pause** — the drivable half only: paused-family
    vocabularies at ledger ingress + `interrupted` classification
    (B6/B5). Live delivery is FINDINGS #1.
15. **Metadata merge-on-write** — caller extras and unknown keys survive
    stamp traffic and finalize; `started_at` preserved; corrupt bytes
    parked verbatim to a unique sidecar, never destroyed (B3).

## FINDINGS — behaviors this suite could NOT pin (Phase-2 design input)

Per the plan's honesty rule: if a behavior can't be pinned through public
seams, that's a finding, not a blocker — and these are the findings.

1. **Live mid-run interrupt/pause is not drivable at the workspace
   boundary.** Delivering an interrupt requires a concurrently running
   agenda loop plus queue delivery, and the intake store
   (`memory/interrupts`) is B12-unregistered — there is no contract to
   assert against. Pinned instead: the paused-family vocabularies at the
   outcomes ledger and the B5 `interrupted` class. *Design input: the Go
   engine should make interrupt intake a registered contract (its own
   B-entry) so pause behavior becomes conformance-testable.*
2. **Lesson minting (LLM extraction) is unpinnable without a model.** The
   production path runs `reflect_and_record` → LLM → lesson text. The
   suite pins the on-disk shapes of both stores and the shared-`lesson_id`
   dual-write via the registered store APIs; the extraction step itself is
   model behavior, not a workspace contract, and stays unpinned here.
3. **B9's `event_truncated` fallback row is unreachable through the
   public `write_event` kwargs.** The per-field caps + shed ladder keep
   every reachable line under the 4096-byte budget (worst hostile-battery
   line observed: 3908 bytes), so the fallback can only be provoked by
   internals-level injection; it stays pinned by the unit battery in
   `tests/test_observe.py`. Readers must still tolerate the row.
4. **The async-tail `verdict_pending` lifecycle (and the
   `done-verdict-pending` success class) is not drivable here.** The
   marker is set by the answer-first early close, which needs a delivering
   notify hook and the finalize tail spawn — deliberately pinned OFF for
   the whole test suite (`MARO_TAIL_SPAWN=0` in conftest).
5. **Judged-NOW via a text judge is consent-gated LLM.** Only the
   deterministic `provenance` verdict source is drivable (scenario
   `now-provenance-verdict`); the `now_self_verdict` /
   `now_self_verdict_free` sources need a live judge.
6. **The B4 recording seam is per-adapter-wrapper, not per-run.** An
   injected adapter produces NO call records unless the caller wraps it in
   `FailoverAdapter` (the suite does, mirroring `build_adapter`). B4
   documents exactly this — no doc mismatch — but it means "record mode
   ON" alone does not guarantee records exist. *Design input: in the Go
   engine, put recording on the one call path construction guarantees,
   not on a wrapper a caller can omit.*
7. **Cross-process call-seq collision behavior is not drivable
   in-process.** B4's two-writers-one-run-dir discipline (temp+link,
   loser-bumps-seq) is pinned by its own unit tests; this suite pins the
   observable single-process facts (unique monotonic seqs,
   complete-on-appearance names).

## Doc/code mismatches found

None. Every B-entry this suite exercises (B1, B3–B11) matched the code's
observable behavior on first contact; the only test rewrites during
authoring were suite-side misreadings (a line-anchored grammar asserted as
a substring; a result object with a deliberate no-truth-value guard).

## Running

```bash
python3 -m pytest tests/behavior/ -q      # part of the normal suite
```
