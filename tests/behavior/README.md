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

**Hard rule:** no assertion may depend on Python internals or module
state, and no return object may be the sole oracle for a contract fact —
disk decides. The engine-specific DRIVER LAYER is bigger than one
function and is named honestly: `drive()` + `ScriptedAdapter` in
`harness.py`, plus every registered ingress/reader call inside individual
tests (`record_outcome`, `stamp_outcome_verdict`, `record_tiered_lesson`,
`append_to_playbook`, `log_event`, `write_event`, `open_run`/`close_run`,
`create_run_dir`, `stamp_run_verdict`, `refresh_run_card_classification`,
`prior_attempts`/`brief_for_goal`, `config.get`). A Go conformance
harness maps EACH of those seams to its own equivalent (they are the
registered APIs of CONTRACTS.md entries, so every engine has one); the
assertions and the scenario table carry over. Where a driver return value
is asserted (`res.status == "invalid"`), the on-disk consequence is
asserted beside it — the return is corroborating, not load-bearing.
The scenario table is plain data, but its scripted-adapter protocol
(consumption order, exhaustion policy — see `scenarios.py` docstring) is
part of the spec a replacement driver must honor; extracting the table +
protocol to language-neutral form is Phase-2 work.

## Layout

| File | Role |
|---|---|
| `harness.py` | Driver (handle() + ScriptedAdapter) and artifact readers; the cross-cutting `assert_common_contracts` every goal scenario must pass |
| `scenarios.py` | Data-first table: goal text + scripted adapter responses + expected workspace artifacts (plain dataclass rows a Go harness can consume) |
| `test_behavior_run_lifecycle.py` | Table runner + call records (B4), stuck path (exact unjudged posture), agenda flow-to-durable-evidence, metadata merge-on-write + corrupt-park (B3) |
| `test_behavior_now_lane.py` | NOW answer artifact (B3), re-run identity (B11) |
| `test_behavior_memory.py` | Outcomes tri-state + closed vocabularies (B6), post-hoc verdict stamping + unknown-key round-trip, lesson stores (B7), paused-family vocabularies |
| `test_behavior_logs.py` | Playbook grammar (B10), captains-log rotation + run slice (B8), events line discipline (B9) |
| `test_behavior_config.py` | Config resolution: env pin, tier merge, malformed→{} (B1) |
| `test_behavior_curation.py` | Run-card shape, success_class grid, `_curation` namespace, derived-view refresh (B5) |

A scenario = goal + scripted adapter responses + expected workspace
artifacts. All scenarios run on scripted adapters (no sleeps, no LLM
spend); network isolation is CONFTEST-enforced, not suite-enforced — the
autouse fixture hides every API key and redirects credential paths, so
components that would try a real call (e.g. pre-flight plan review)
fail to build an adapter and skip. Run outside pytest and those calls
can go out. The whole suite runs in a few seconds and is part of the
normal suite (no `@slow`). Workspace isolation comes from
`tests/conftest.py` (per-test tmp `MARO_WORKSPACE`); the harness
additionally asserts the resolved workspace is never the live one.

**Must-detect evidence (2026-08-28):** the suite is an instrument, so it
carries kill proof, re-runnable by hand: (M1) `write_event` early-return
→ 8 tests red; (M2) stuck runs classified `success` → 2 red; (M3)
`handle_inputs.jsonl` intake write dropped → 7 red; (M4) step-artifact
writer suppressed → flow-evidence test red (round-2 hardening: the
captains-log slice no longer counts as evidence). Before the round-1
review fixes, M1 and M3 passed clean — the B9/B11 blocks were vacuous
(missing-file readers returned `[]`, counts went unasserted).

## Scenario coverage (the ~15)

1. **NOW one-shot** (`now-one-shot`) — intake row B11, run dir + verbatim
   prompt B3, answer artifact, unjudged outcome row B6, card B5;
   captains-log attribution + events feed activity B8/B9 (per-run event
   attribution is not pinnable for NOW — FINDINGS #9).
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
   every reachable line under the 4096-byte budget (an authoring-time
   observation, not an asserted bound: the worst hostile-battery line
   measured 3908 bytes on one run), so the fallback can only be provoked
   by internals-level injection; it stays pinned by the unit battery in
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
   `FailoverAdapter` (the suite does for its `record_calls` scenario).
   Review round 1 (2026-08-28) found this was ALSO true in production:
   `build_adapter`'s explicit-backend branches returned bare adapters, so
   pre-flight plan-review calls (`backend="openrouter"/"anthropic"`) ran
   unrecorded, unmetered, and outside the cap warning. Fixed in this
   commit — every `build_adapter` branch now wraps (pinned by
   `tests/test_llm.py::TestExplicitBackendAlwaysWrapped`, red-verified).
   The residual truth: only ADAPTER INJECTION (a test-only seam) bypasses
   recording. *Design input stands: in the Go engine, put recording on
   the one call path construction guarantees, not on a wrapper.*
7. **Cross-process call-seq collision behavior is not drivable
   in-process.** B4's two-writers-one-run-dir discipline (temp+link,
   loser-bumps-seq) is pinned by its own unit tests; this suite pins the
   observable single-process facts (unique monotonic seqs,
   complete-on-appearance names).
8. **The five-round contract-fix ROOT failure modes stay unit-pinned,
   outside this acceptance gate.** Park/fsync failure (R3-7), short
   `os.write` (R3-2), concurrent B4 publication, concurrent B8
   rotate+append, and the durable `finalize_failed` card flag (R4-2/R5-2)
   all need fault injection or process interleaving below the workspace
   boundary (e.g. the finalize-failure card flag cannot be provoked by a
   read-only run dir — that kills the card write too). They are pinned by
   the red-verified unit tests in `tests/test_runs.py`,
   `tests/test_observe.py`, `tests/test_orch_core.py`. *Design input: the
   Go engine must port those batteries (or the conformance harness grows
   subprocess fault injection in Phase 2) — passing THIS suite alone does
   not certify the concurrency/durability half of the contract.*
9. **NOW-lane runs have no run-correlated B9/B8 lifecycle guarantee.**
   `events.jsonl` rows join by `loop_id` only — a NOW run owns no loop,
   so its events are not attributable at the workspace boundary; and the
   only captains-log row a NOW run reliably produces today is incidental
   maintenance traffic (`MEMORY_CONSOLIDATED`) attributed via the ambient
   run-dir pin. The suite therefore pins existence + attribution, not
   lane lifecycle. *Design input: Go events should carry `handle_id`
   (key-required at emission when a run is pinned), and each lane should
   emit at least one contractual lifecycle bus event.*

## Doc/code mismatches found

Authoring pass: every B-entry exercised (B1, B3–B11) matched the code's
observable behavior on first contact; the only rewrites were suite-side
misreadings. The 2026-08-28 review round then falsified the original
"none" claim in both directions:

- **The suite itself violated B5** by pinning `_curation.completed` /
  `_curation.failed` — underscore keys are writer-private. Fixed: only
  the namespace-is-an-object fact is asserted.
- **The agenda scenario tables were misaligned at birth**: the
  goal-clarity assessor consumes the first agenda call, so the scripted
  plan never reached decompose and the engine silently ran a one-step
  fallback plan — invisible while the suite asserted objects, caught the
  moment `test_agenda_flow_reaches_durable_evidence` asserted flow.
- **A real production gap** (flaw-finder role working): explicit-backend
  `build_adapter` calls bypassed the FailoverAdapter record/meter/cap
  seam — see FINDINGS #6.

## Running

```bash
python3 -m pytest tests/behavior/ -q      # part of the normal suite
```
