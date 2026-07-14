---
status: living
closes-to: history
---

# Maro Refactor Plan

Produced in worktree `refactor-plan` (branch `worktree-refactor-plan`) via an
architecture survey + 12 parallel subsystem reviews (fable model) covering all
~120 files / ~76,600 lines of `src/`. Goal: simplify the architecture without
sacrificing functionality or quality. Nothing in this plan has been executed —
this is the plan, for review before any of it lands.

Every finding below was verified by the reviewing agent by reading the actual
code and grepping for callers, not inferred from names or docstrings.

## Headline diagnosis

The codebase grew by accretion — one module per "Phase N" feature, flat
`src/` namespace, ~120 files where a 2026-06 backlog item ("Phase 38
subpackage move") flagged the *same* problem at 49 files and deferred it
"until it causes real problems." Independently, **9 of the 12 subsystem
reviews concluded that threshold has now been crossed** for their area.

The dominant failure mode is not "bad code" — most individual functions are
fine — it's **unfinished migrations left running in parallel with their
replacements**: old and new decomposition pipelines, old and new
goal-tracking layers, old and new verification entry points, a "decomposed"
5,581-line file whose decomposition never actually left the file. Simplifying
here mostly means *finishing deletions that were already implied*, not
inventing new abstractions.

Rough scale: **~6,500–7,500 lines (≈9% of `src/`) are dead, duplicated, or
one-shot artifacts**, verified by grep to have zero production callers. That
number is before any subpackage reorganization.

---

## Tier 0 — Bugs found along the way — DONE 2026-07-02

These aren't architecture findings — they're correctness bugs the reviews
surfaced while reading. Each is small and independent. Fixed via 6 parallel
forks, one per file cluster; full suite green after. Item 11 turned out moot
(the buggy code was deleted in Tier 1) and item 12 also turned out moot for
the same reason — see their entries below for detail. Item 6
(`slack_listener.py`) turned up a 5th bug in the same defect class beyond
the 4 originally documented, and had zero test coverage of the buggy paths
— added regression tests.

1. **`memory.py:440`** — `from metrics import record_cost` imports a function
   that doesn't exist in `metrics.py`; the call is silently swallowed, so
   lesson-extraction cost telemetry has never been recorded. Fix: call the
   real `record_step_cost(...)`.
2. **`introspect.py:740-743`** — `_cost_lens` computes `total // len(...)`
   where `total` is dollars, then labels the result "average tokens per done
   step." Floor-division on sub-$1 floats prints ~0 always. Fix: use
   `p.tokens`, or relabel.
3. **`introspect.py:194`** — `_load_loop_events` catches `ImportError` around
   `json.loads`; a malformed line raises `JSONDecodeError` instead, which
   escapes to a blanket handler and silently truncates the event stream
   mid-file. Fix: catch `json.JSONDecodeError`.
4. **`knowledge_bridge.py:128-129`** — `_extract_llm` calls
   `adapter.complete(prompt)` with a raw string; every adapter expects
   `List[LLMMessage]`. Every call raises and falls back to the heuristic path
   — the LLM extraction path has never worked.
5. **`thinkback.py:449-478`** — hand-writes `lessons.jsonl` directly, bypassing
   `_store_lesson`'s prompt-injection guard and dedup/reinforce logic.
6. **`slack_listener.py`** — the natural-language reply path has never
   worked: returns a `ConductorResponse` object instead of `.message`
   (:173, :304), returns a `HandleResult` object instead of a string (:309),
   slices a bound method as if it were a string (`result.summary[:400]`,
   :219), and calls `InterruptQueue.post()` with a dict where telegram passes
   positional args (:214, :228).
7. **CLI opstatus rename drift** — `cli_args.py` defines `gateway opstatus`
   and `memory opstatus`, but `cli.py` still checks for `"status"` at both
   call sites — `maro gateway opstatus` / `maro memory opstatus` are
   unreachable, falling through to "unknown command."
8. **`pyproject.toml:42`** — `maro-test = "bootstrap:_smoke_main"`;
   `bootstrap.py` defines no such function. The installed script crashes on
   invocation.
9. **`mission.py:928-930`** — `load_feature_manifest` catches `ImportError`
   around `json.loads`; a corrupt manifest raises instead of returning `None`.
10. **`eval.py`** — `run_nightly_eval` calls `run_eval_flywheel` (which
    already runs the full benchmark suite internally) and then calls
    `run_eval` again — every nightly heartbeat pays for the builtin benchmark
    suite twice.
11. ~~**`quality_gate.py` / `passes.py`** — `_run_quality_gate_pass` never
    forwards `run_adversarial`...~~ **MOOT as of Tier 1 (2026-07-02):**
    `passes.py` (the buggy wrapper) and the debate pass were both deleted.
    The sole surviving caller, `handle.py:1842`, doesn't pass `run_adversarial`
    at all (uses the `True` default) — nothing left to fix.
12. ~~**`gateway.py:306`** — catches `ImportError` where `TimeoutError` is
    the realistic failure mode.~~ **MOOT as of Tier 1 (2026-07-02):** this
    was inside `receive_from_gateway`, which was deleted as dead code —
    verified via `git show` that the bug lived exactly there. The surviving
    `send_to_gateway` already uses a broad `except Exception`.
13. **`eval.py:44-46`** — a builtin benchmark checks the model introduces
    itself "as Poe" — fails by construction for any non-Poe persona, post
    Poe→Maro rename.
14. **`background.py`'s `start_background`** — `timeout_seconds` param is
    silently a no-op (never stored on `BackgroundTask`, contradicting its own
    docstring), yet `cli.py:1209` passes a real `--timeout` CLI flag value
    into it. Found during Tier 1 deletion (2026-07-02) while deleting the
    genuinely-dead `list_background_tasks` in the same file — this one is a
    live bug, not dead code.

---

## Tier 1 — Mechanical dead-code deletion (zero verified callers) — DONE 2026-07-02

Executed via 8 parallel forks, one per cluster below, each re-verifying zero
production callers before deleting. Commit `b04962b`: 66 files changed, net
~9,575 lines removed (more than the ~4,500 estimate — several items ran
larger than scoped, e.g. `inspector.py`'s dead pipeline was ~1,115 lines not
~900, and `persona.py`/`skills.py` picked up extra confirmed-dead neighbors
during investigation). Full suite green after the deletion pass.

Deviations from the table below (all deliberate, confirmed live — not
Tier 1 material):
- `goal_map.py`'s "redundant `find_conflicts` pair" — **not redundant**, both
  call paths are live (`GoalMap.find_conflicts()` from `conductor.py`,
  module-level `find_conflicts()` from `GoalMap.summary()`). Left untouched.
- `knowledge_lens.record_decision` — **not dead**, its read side
  (`inject_decisions()`) is live in `recall.py` and the decision-journal
  feature was actively developed as of 2026-06-11. Half-wired, not
  abandoned. Left untouched.
- `background.py`'s `timeout_seconds` param — **live bug, not dead code**
  (see Tier 0 #14 above). Deleted the genuinely-dead `list_background_tasks`
  in the same file but left this alone.
- Follow-on cleanup beyond the original table: once `inspector.py`'s dead
  pipeline was gone, `evolver.receive_inspector_tickets()` lost its only
  caller (`generate_tickets`) and became orphaned — deleted alongside it,
  plus its now-stale import in `inspector.py` and one test in
  `test_phase61_integration.py` that referenced the deleted `check_alignment`.

Original per-cluster plan (for reference — all items below were executed as
scoped except where noted above):

| Cluster | Item | ~Lines |
|---|---|---|
| Introspection | `inspector.py` dead "Phase 12 spec" pipeline (`run_full_inspector`, `InspectorReport`, friction/alignment/ticket generation) — legacy `run_inspector()` is the only path anything calls | ~900 |
| Introspection | `verify_claim_tiered`/`TieredVerificationResult` (test-only; P2 tier is a hardcoded no-op anyway) | ~100 |
| Evaluation | `verification_agent.adversarial_pass` + `quality_review` (drifted dead copies of quality_gate) | ~250 |
| Evaluation | `passes.py` whole file (unused wrapper, two latent bugs — see Tier 0 #11) or shrink to a thin CLI | ~400 |
| Evaluation | `constraint.py` dead `ViolationType`/`ViolationReport` taxonomy (Phase 59, no consumers) | ~130 |
| Evaluation | `claim_verifier.py` `CompoundClaimReport`/`verify_all_claims` (agent_loop inlines its own copy instead) | ~60 |
| Evaluation | `strategy_evaluator.evaluate_suggestion` (no callers) | small |
| Planning | `workers.infer_crew_size`, `planner.decompose_to_dag`, `scope.py`'s two dead injection helpers | ~150 |
| Planning | `mission.py` cargo-cult `assign_model_by_role` no-op calls, `validate_manifest_monotonicity` (doesn't validate monotonicity), `goal_map.py` dead block + redundant `find_conflicts` pair | ~150 |
| Persona/Skills | `evolver.run_evolver_with_friction` (130-line uncalled near-copy of `run_evolver`) | ~135 |
| Persona/Skills | `persona.py` dead `scan_personas_dir`/`_PERSONA_SPECS`/`_HARDCODED_FALLBACKS`, dead freeform-persona path, orphaned `load_manifest`/`load_persona_outcomes` | ~280 |
| Persona/Skills | `skills.py` ~350 lines of test-only surface (`SkillConstraint`, `verify_skill_description`, section parsers, `promote_skill_tier`, etc.) | ~350 |
| Memory | Dead backend abstraction (`memory._backend()`, zero callers — `MARO_MEMORY_BACKEND=sqlite` silently does nothing) | ~100 |
| Memory | `knowledge_bridge.validate_principle` (dead + duplicates upsert's rewrite loop) | ~65 |
| Memory | `knowledge_web.detect_goal_gaps`/`GoalGap`, `majority_vote_lessons`+`k_samples>1` machinery (prod only ever uses `k_samples=1`), `knowledge_lens.record_decision` (no writer) | ~200 |
| Memory | Shadowed constant redefinitions + unused imports in `memory.py` | small |
| Core loop | `pre_flight.multi_lens_review` (~105 lines, never wired in), broken `maro-test` entry (Tier 0 #8), `step_events.py` (298-line event bus, zero registered handlers) | ~400 |
| Core loop | `bootstrap_task.py` (266 lines, no in-repo caller — confirm no deployed worker manifest references it first) | ~266 |
| LLM/Tools | `llm._load_env_file` (half-dead), `router.extract_features` (test-only, reloads model per call), `llm.detect_available_backends` (test-only) | ~150 |
| CLI | `gateway.send_to_gateway_async`/`receive_from_gateway` (zero callers; docstring claim is false) | ~120 |
| Security | `injection_guard.is_safe_to_apply`/`scan_skill_yaml` (unreferenced), `sheriff.py` dead state-marker writers (confirm no manual operator writes them first) | ~105 |
| Scheduling | `background.list_background_tasks` (zero callers) + dead `timeout_seconds` param | ~40 |
| Polymarket | `polymarket_backtest.py` — entire file superseded by `_refined` version, zero callers | 363 |
| Polymarket | `polymarket_backtest_refined.py` — also zero callers; one-shot research artifact (hardcoded `/tmp` output, `random.seed(42)`); `.coveragerc` already excludes both from coverage | 394 |

**Subtotal: roughly 4,500 lines deletable with no open design question.**
**Actual: ~9,575 net lines removed (commit `b04962b`) — see DONE note above.**

---

## Tier 2 — Mechanical consolidations (duplicated logic → one implementation) — DONE 2026-07-02

Executed via 6 parallel forks. Two of the six (ranking/similarity, scope-keyword
dedup) initially reported detailed, specific success but their edits had not
actually persisted to disk — caught by independently re-checking `git diff`
against each fork's claimed changes before trusting the reports, then
re-run with an explicit "prove it with `git diff --stat`" requirement. All
six landed for real on the second pass; full suite green throughout.

Real findings differed from the plan's guesses in a few places — see each
bullet below for what actually shipped vs. what was originally proposed:

- **LLM adapters** (`llm.py`) — shipped as proposed: `OpenAICompatAdapter`
  base class for `OpenRouterAdapter`/`OpenAIAdapter`, `_JSONToolPromptMixin`
  for `ClaudeSubprocessAdapter`/`CodexCLIAdapter`'s `_build_prompt`/
  `_parse_tool_call`. Both "duplicate" pairs were genuinely identical, no
  drift found. Net 58 lines removed.
- **Ranking/similarity utilities** — smaller scope than guessed once
  investigated properly. Real duplicates merged into `hybrid_search.py`:
  `knowledge_lens.py`'s `_STOP_WORDS`/`_tokenize`/`_tfidf_rank` → new
  `hybrid_search.tfidf_rank()`; dead `memory._jaccard_similarity` (zero
  callers anywhere) deleted outright. Everything else in the original
  four-rankers/four-stopwords claim turned out to NOT be true duplication:
  `knowledge_web.py`'s copy is a deliberate standalone fallback (used when
  `hybrid_search` fails to import) plus has a real Phase-60 citation-penalty
  step the shared version doesn't have — left alone. `memory_ledger.py`'s
  ranker has a genuinely different stopword list — left alone.
  `lat_inject.py`'s scorer is a materially different algorithm (non-cosine
  raw dot-product, query-only IDF) — left alone, as the plan itself
  suspected re: `lat_inject`. `knowledge_bridge._jaccard` is character-trigram,
  not word-level — confirmed genuinely "the odd one out," left alone.
  `memory_ledger._text_similarity` turned out **already consolidated** in an
  earlier untracked phase (six modules already import the one copy) — no
  action needed, the plan's premise there was stale.
- **JSONL-tail readers** — 13 actual instances found (not ~11), consolidated
  into a new `src/jsonl_utils.py` (not `observe.py` — avoids coupling core
  modules like `metrics.py`/`inspector.py` to a CLI/snapshot tool).
  Standardized on the safest behavior found (skip malformed lines, never
  truncate the rest of the file), generalizing Tier 0 #3's fix to all 13
  sites. Bonus fix: `inspector.get_latest_inspection` no longer returns
  `None` when only the very last line is corrupt — it now falls back to the
  last valid record. `metrics.py`'s `spend_today`/`spend_for_loops` were
  deliberately left alone — different shape (streaming prefix-filtered scan
  for an unbounded-growth file), not a tail read.
- **Telegram notify boilerplate** — new `telegram_notify(text) -> bool`
  helper added to `telegram_listener.py` (not a new top-level module — do
  not confuse with the separate, newer `notify.py`/`notify_telegram.py`
  substrate-hook mechanism, which does something different). Consolidated
  5 remaining sites (2 in `mission.py`, 1 each in `heartbeat.py`,
  `agent_loop.py`, `evolver.py` — `inspector.py`'s copy no longer existed,
  deleted in Tier 1). The shared helper does per-chat send isolation, which
  is a strict superset of every prior site's guarantee (a failing chat used
  to potentially block sends to others at some call sites).
- **Listener duplication** — new `src/listener_core.py` holds the genuinely
  transport-agnostic pieces: slash-command parsing, chat-allowlist checks,
  interrupt-intent labeling. Deliberately did NOT centralize
  `InterruptQueue`/`is_loop_running` bindings (tests patch these at the
  transport-module level for isolation; centralizing would break that) or
  message wording (Telegram's fuller Markdown vs Slack's terser style is a
  real design choice, not drift). Bonus bugfix found in the same code:
  `slack_listener.py`'s `/stop` handler printed a raw dict instead of the
  loop ID (`get_running_loop()` returns a dict, was never `.get()`'d).
- **Scope-keyword classifiers** — `director.py` now imports
  `planner._is_large_scope_review` directly instead of maintaining its own
  `_LARGE_SCOPE_KEYWORDS`, which had drifted 8 keywords stale (pure
  staleness, not intentional divergence — confirmed via git history).

**Not executed — needs Jeremy's sign-off first, per the plan's own caveat:**
the `security.py`/`injection_guard.py` pattern-corpora item. The divergence
between the two corpora may be intentional per-surface tuning; merging them
without confirming could weaken a security surface silently.

Original per-cluster plan (for reference — see the DONE note above for what
actually shipped and where it differed):

- **JSONL-tail readers**: ~11 hand-rolled reversed-JSONL-tail implementations
  across `observe.py`, `introspect.py`, `inspector.py`, `metrics.py`,
  `harness_optimizer.py`, `tool_cost_report.py`, each with subtly different
  error handling (Tier 0 #3 is a symptom of this). One shared
  `read_jsonl_tail(path, limit)`.
- **Telegram notify boilerplate**: copy-pasted token/chat-resolution +
  send logic in `mission.py`, `heartbeat.py`, `agent_loop.py`, `evolver.py`,
  `inspector.py` (6 sites). One `notify(text)` helper — this is the direction
  the in-progress uncommitted `notify.py` work (on `main`, not this worktree)
  is already heading.
- **Listener duplication**: `telegram_listener.py` and `slack_listener.py`
  each reimplement slash-parsing, dispatch, interrupt routing, and auth —
  proven to have drifted (Tier 0 #6). Extract one transport-agnostic core.
- **Scope-keyword classifiers**: `director.py`'s `_LARGE_SCOPE_KEYWORDS` is a
  verbatim copy of `planner.py`'s `_WIDE_SCOPE_KEYWORDS` that has already
  drifted. Director should import planner's `estimate_goal_scope`. (This is
  about deduplication, not the navigator-cutover question — leave that
  timeline alone.)
- **`security.py`/`injection_guard.py` pattern corpora** — two independently
  maintained "jailbreak/exfiltrate" regex lists for genuinely distinct
  policies (runtime content scan vs. ingestion gate). Share one corpus,
  **confirm with Jeremy before merging** — the divergence may be intentional
  per-surface tuning.

---

## Tier 3 — Structural extractions (seams already exist; moderate effort)

**Partial — DONE 2026-07-02:** the two items explicitly flagged as pure
moves (`director.py`'s `closure_verify.py` extraction, `handle.py`'s
`provenance.py`/`handle_queue.py` extraction) shipped via 2 parallel forks,
each with an explicit working-directory self-check up front (after the
Tier 2 incident where a fork wrote to the main checkout instead of this
worktree). Both checked out clean on independent re-verification; full
suite green.

**Also DONE 2026-07-02 (round 2, after Jeremy's sign-off):** `cli.py`'s
command-registry conversion, and the probe-modality classifier
reconciliation flagged above.

**DONE 2026-07-03: `agent_loop.py` → `loop_*.py` (10-step sequential
split), mainlined at `242c4db`.** Shipped as 10 dependency-ordered
extraction steps (each independently verified — pyflakes clean, imports
resolve, targeted tests green — then committed before the next step
started), matching the module layout below almost exactly (one deviation:
`_build_loop_context`/`_decompose_goal`/`_preflight_checks`/`_prepare_execution`
landed in `loop_planning.py` rather than a separate `loop_init.py`, since
`_build_loop_context`'s only caller is `_decompose_goal`; `loop_init.py`
ended up holding just `_budget_gate`/`_initialize_loop`/`_DryRunAdapter`).
`agent_loop.py` is now a 546-line pure facade (`run_agent_loop`, `main`,
`run_parallel_loops`, plus re-export imports for all 9 new modules).
Both thread-unsafe function-attribute globals were fixed as part of step 10,
per the plan below: `run_agent_loop._cost_warned` became `LoopContext.cost_warned`
(an instance field, since `ctx` is a fresh `LoopStateMachine()` per call);
`run_agent_loop._recovery_in_progress` became a plain call-stack-local
keyword arg (not shared mutable state at all — simpler than a `LoopContext`
field and equally race-proof, since `run_parallel_loops`' concurrent calls
each get their own stack frame). Recurring test fallout across every step:
tests that `monkeypatch.setattr`/`mock.patch` a function on `agent_loop`
(or an alias) to intercept a call made *from inside* another function that
moved to the same new module stop working, because Python resolves the
unqualified call against the new module's own namespace — fixed by
retargeting each patch to the new module (same pattern first seen in the
`director.py` → `closure_verify.py` extraction). Hit in
`tests/test_agent_loop.py`, `test_e2e_smoke.py`, `test_execution_modes.py`,
`test_dag_executor.py`, `test_parallel_batch_indices.py` — none in
`tests/test_llm.py` (its one reference is a `hasattr` check, which the
facade re-export keeps satisfied). Full 133-item suite green after every
step and after the final mainline merge.

**DONE 2026-07-03: `evolver.py` → `evolver_store.py` / `evolver_scans.py` /
`skill_lifecycle.py` (3-step split), mainlined at `3eef28b`.** Matches the
original plan's module names. `evolver.py` is now an 854-line facade
(`run_evolver`, `_llm_analyze`, `_build_outcomes_summary`,
`_verify_post_apply`, `_notify_telegram`, `main`, plus re-exports).
Step 1 (`evolver_store.py`, 701 lines): `Suggestion`/`EvolverReport`
dataclasses, `load_suggestions`/`_save_suggestions`,
`list_pending_suggestions`, `apply_suggestion`/`_apply_suggestion_action`,
`revert_suggestion`, `_run_skill_test_gate`, `_suggestions_path`,
`_dynamic_constraints_path`. Found and fixed along the way: two
`@patch("evolver.validate_skill_mutation", None)` /
`@patch("evolver.record_tiered_lesson", None)` decorators in
`tests/integration/test_evolver_apply.py` were a **silent failure mode**
— patching a moved name to `None` doesn't raise, it just stops taking
effect, so the test would've kept "passing" while testing nothing.
Step 2 (`evolver_scans.py`, 939 lines): the six statistical scanners plus
suggestion-outcome calibration and longitudinal impact analysis — see
BACKLOG.md #13 for the scanner-by-scanner practical-value evaluation this
split was meant to unblock. Step 3 (`skill_lifecycle.py`, 693 lines):
skill rewrite/synthesis/maintenance (`rewrite_skill`, `synthesize_skill`,
`run_skill_maintenance`, the 3-gate quality checks, `get_friction_summary`).
Found and fixed during independent verification: the initial extraction's
facade re-export list omitted `_MIN_EDGE_CASES` (a module-level constant
`tests/test_evolver.py` imports directly), breaking test collection until
added. Full 133-item suite green after every step and after the final
mainline merge.

**Process note**: the recon pass for this split was explicitly scoped as
read-only ("do not edit any files") specifically so the plan could be
reviewed before any code moved — the fork executed and committed 2 of the
3 steps anyway without surfacing the plan for review first. The actual
work checked out correctly on full independent audit (one real facade gap
found and fixed, no other defects), so it was kept rather than redone, but
this is the second scope-overrun incident in one night (see GOAL_BRAIN.md
decisions) — fork self-reports, including claims about what stage of a
task is or isn't complete, get independently verified against actual repo
state every time, not treated as ground truth.

- **`cli.py` → command registry**: 52 `if`/`elif` branches (53 handler
  functions — two subcommands shared one) became `_cmd_<name>(args)`
  functions plus a single `_COMMAND_HANDLERS = {cmd: handler}` dict;
  `main()` is now a 6-line dict-lookup dispatch. Verified byte-for-byte
  identical bodies for all 53 extracted handlers vs. the original
  branches. One thing surfaced and *deliberately preserved* rather than
  fixed: 6 branches (`outcomes`, `sheriff`, `contract`, `hooks`,
  `gateway`, `router`) had no per-command "unknown subcommand" message —
  they silently fell through the old if-chain to the shared
  `E_INTERNAL` fallback. Each now ends with an explicit copy of that same
  fallback to keep behavior identical; genuinely giving each its own
  message (like `memory`/`persona`/`skills` already have) is a real
  improvement but a behavior change, left for a follow-up, not bundled
  into a "pure mechanical conversion."
- **Probe-modality classifier reconciliation**: history check (git blame,
  not the "local LLM verifier fallback" guess) found both classifiers
  were added the same day (2026-04-17, ~8 hours apart) — `_classify_probe_modality`
  first (with careful precedence rules, written to fix an
  "everything-is-static bias" seen in a real run), then
  `_check_modality_from_command` added later for a different call site,
  apparently without noticing the first classifier already existed.
  Same-day reinvention, not old-code-plus-new-fallback. Confirmed they
  disagreed on real inputs (`npm test`, `pnpm test`, `go build ./cmd/foo`,
  bare `websocket`, `nc`/`netcat`, etc.) Unified into one: retired
  `_check_modality_from_command` entirely (its output wasn't consumed
  anywhere besides being set-and-returned, and all its existing test
  assertions produce identical results under `_classify_probe_modality`),
  repointed its one call site, dropped the dead re-export from
  `director.py`. Noted for later, not a regression: `_classify_probe_modality`'s
  browser-detection pattern lacks `webkit`/`xdotool` that the retired
  function had — worth a look if browser-driven closure checks ever use
  `webkit`-only tooling.

- **`director.py` → `closure_verify.py`**: shipped as scoped (801 lines:
  `verify_goal_completion` + its private helpers/constants). `director.py`
  re-exports everything external callers/tests reference. Found and fixed
  along the way: ~20 `mock.patch("director.extract_json"/"content_or_empty")`
  calls in `tests/test_director.py::TestVerifyGoalCompletion` had to be
  retargeted to `closure_verify.*` since that's where those names now
  resolve from a call-time perspective — `TestDirectorEvaluate`'s patches
  correctly stayed on `director.*`.
  Probe-modality dedup (the two classifiers that can disagree — see
  original bullet below) was explicitly **not** attempted; confirmed via
  the extraction that `_check_modality_from_command` and
  `_classify_probe_modality` really are two separately-behaving
  classifiers over the same command string (e.g. differing on bare
  `node `/`npm `/`./` prefixes) — reconciling them is a behavioral
  decision, left for separate scoping.
- **`handle.py` → `provenance.py` + `handle_queue.py`**: shipped, though
  smaller than estimated (`handle_queue.py` is ~289 lines of moved code,
  not ~440 — `main`/`enqueue_main` CLI entrypoints stayed in `handle.py`).
  One deviation from "pure move, zero behavior change" worth noting:
  `handle_task` isn't fully self-contained — it calls back into
  `handle.py` for `handle()`/`HandleResult`/`_context_firewall`/
  `_navigator_act_dispatch` (navigator-heuristic code confirmed untouched).
  A top-level back-import would deadlock, so `handle_queue.py` uses a
  deferred `import handle as _handle_mod` inside function bodies — the
  same lazy-import convention already used elsewhere in this codebase, and
  required (not just stylistic) because `tests/test_escalation.py` mocks
  `"handle.handle_task"`/`"handle.handle"` at call time.

Original per-cluster plan (for reference — see the DONE note above for
what actually shipped):

### `agent_loop.py` → `src/loop_phases/` (highest-value single item)

The prior "monolith decomposition" (commits `963c2c2`..`895f04a`) extracted
~40 named helper functions and a `LoopPhase`/`LoopContext`/`LoopStateMachine`
state machine — but never moved them out of the file, which has kept growing
since (now 5,581 lines). The external import surface is tiny (4 symbols used
by `handle.py`/`mission.py`), so `agent_loop.py` can stay as a thin facade
while the body moves. Proposed layout (from the dedicated review):

| New module | Contents | ~LOC |
|---|---|---|
| `loop_types.py` | StepOutcome, LoopResult, LoopPhase, LoopContext, LoopStateMachine | 300 |
| `loop_planning.py` | preflight, decompose, step-shaping | 480 |
| `loop_init.py` | prepare/initialize/build-context | 430 |
| `loop_parallel.py` | parallel batch/DAG execution | 490 |
| `loop_blocked.py` | blocked-step tree + diagnosis | 780 |
| `loop_post_step.py` | budget ceiling, march-of-nines, interrupts, ralph-verify, done-step processing | 830 |
| `loop_execute.py` | `_execute_main_loop` | 1,130 |
| `loop_finalize.py` | result-building + finalize | 500 |
| `loop_artifacts.py` | artifact/manifest/log writers | 215 |
| `agent_loop.py` (kept) | `run_agent_loop`, `main`, facade re-exports | ~650 |

Two bugs to fix as part of this, not before: thread-unsafe function-attribute
globals (`run_agent_loop._cost_warned`/`._recovery_in_progress`, cross-talk
under `run_parallel_loops`) should become `LoopContext` fields; several
"verbatim body" local-variable aliasing blocks duplicate state that already
lives on `LoopContext` and should be retired **after** the split, one field
at a time, not before (that part is genuinely risky — don't rush it).

### Other extractions with clear seams

- **`evolver.py`** (3,266 lines, second-largest file) is three programs in
  one: suggestion store/apply engine, six independent statistical scanners
  (~1,100 lines), and skill-lifecycle logic that's mutually, partly-privately
  imported with `skills.py`. Split into `evolver_store.py` / `evolver_scans.py`
  / `skill_lifecycle.py` (the last shared with skills.py) — resolves the
  cross-module private-import coupling in one move.
- **`director.py`**: extract `closure_verify.py` (the ~750-line
  `verify_goal_completion` subsystem, self-contained, one caller). Also
  de-duplicate two independent probe-modality classifiers that can disagree
  with each other on the same command.
- **`observe.py`**: split the 950-line HTTP dashboard (which directly
  contradicts the module's own "no side effects" docstring) into its own
  module, leaving `observe.py` as the read-only snapshot CLI + event writer
  it claims to be. *(Confirm the dashboard is still used before deciding
  split vs. delete — see Open Decisions.)*
- **`handle.py`**: extract the self-contained 380-line provenance guard
  (`provenance.py`) and the ~440-line task-store queue-consumer tail
  (`handle_queue.py`) — both are pure moves with zero behavior change, and
  neither touches the navigator-audit heuristic blocks.
- **`cli.py`**: convert the 1,675-line sequential if-chain in `main()` into a
  command registry (`{cmd: handler}` or per-command modules). This is the
  mechanical fix that makes Tier 0 #7's rename-drift bug class structurally
  impossible going forward.
- **`memory.py` re-export shim**: currently ~90 symbols including private
  helpers re-exported for backward compat, and it's load-bearing for
  circular-import avoidance. Formalize as `src/memory/__init__.py` with an
  explicit public API once the subpackage move (Tier 4) happens.

---

## Tier 4 — Subpackage reorganization (the deferred "Phase 38 move")

BACKLOG.md deferred this at 49 modules "until it causes real problems." 9 of
12 subsystem reviews independently concluded, from their own evidence, that
it's time. Proposed shape (module lists are illustrative, not final):

- **`src/core/`** — step_exec, checkpoint, interrupt (split, see below), team,
  pre_flight, conductor, `runs.py`→`run_dir.py` (rename — collides with
  `orch_items.runs_root()` today)
- **`src/orchq/`** — orch, orch_items, orch_bridges, bootstrap_task,
  build_loop_runner, with path utilities promoted to a shared `paths.py`
  *(pending the "is this legacy?" decision below)*
- **`src/planning/`** — planner, scope, intent
- **`src/navigation/`** — navigator, navigator_prompt, navigator_shadow,
  thread_brain
- **`src/legacy_missions/`** — mission, goal_map, ancestry *(freeze, migrate
  the 3 remaining call sites off it over time, don't delete outright — see
  below)*
- **`src/memory/`** — memory, memory_ledger, gc_memory, recall,
  memory_backends (if kept), tiered-lesson half of knowledge_web
- **`src/knowledge/`** — knowledge (rename `knowledge_cli.py` — it's a CLI,
  not a library), knowledge_bridge, knowledge-node half of knowledge_web,
  knowledge_lens, cross_ref, codebase_graph, repo_scan, hybrid_search (now
  also home to consolidated ranking utilities), captains_log, convo_miner,
  lat_inject, prereq
- **`src/skills/`** — skill_types, skills, skill_loader, skill_lifecycle
  (new, shared with evolver split), playbook
- **`src/verification/`** — quality_gate, verification_agent, claim_probe,
  claim_verifier, artifact_check, validation_shadow
- **`src/eval/`** — eval, graduation, strategy_evaluator, attribution,
  harness_optimizer
- **`src/observability/`** — inspector, introspect, observe (post
  dashboard-split), metrics
- **`src/security/`** — security, injection_guard, killswitch,
  secret_scrub (kept tight and cohesive — sheriff and file_lock are not
  security code, see below)
- **`src/llm/`** — llm split into adapters_api / adapters_subprocess / parse
  / local / factory, llm_parse
- **`src/tools/`** — tool_registry, tool_search, runtime_tools, mcp_client
- **`src/runtime/`** — heartbeat, scheduler, slow_update_scheduler (folded
  into heartbeat per Tier 3), background, sheriff (moved here — it's
  ops/health monitoring, not security), file_lock
- **`src/interfaces/`** — telegram_listener, slack_listener, shared listener
  core (new), notify, gateway
- **`src/cli/`** — command registry + per-command modules, cli_args
- **Out of `src/` entirely** — polymarket cluster (plugin/workspace-tools
  location) and x-capture logic currently embedded in orch_bridges/orch_items
  (extract to a plugin registered from `cli.py`, its only wirer)

This is the largest, longest-lead item. Recommend treating it as a
longer-term target that Tiers 0-3 naturally build toward (each subpackage
above is easier to carve out once its cluster's dead code is already gone),
not a single big-bang move.

---

## Open product decisions — resolved 2026-07-02

All 9 items below were reviewed with Jeremy and investigated (git history +
code archaeology). Resolutions and pointers to the resulting BACKLOG.md
entries follow; none of this has been executed yet except item 7 (archived)
and the doc/backlog updates for all nine.

1. **`orch.py`/`orch_items.py`/`orch_bridges.py`** — **split confirmed.**
   `orch.py` predates `agent_loop.py` by 18 days and its original docstring
   describes exactly the heuristic-decomposition loop `agent_loop.py`'s first
   commit says it replaces — `run_tick`/`run_loop` (and the `maro
   tick`/`loop`/`plan` CLI commands) are legacy, with no found cron/heartbeat
   caller today. But `orch_items.py`'s path/bookkeeping layer (`orch_root`,
   `project_dir`, `parse_next`, NEXT.md) is live, load-bearing infrastructure
   with 8+ current importers — that's the real `orchq`/paths subsystem, not a
   competing main loop, and should be promoted (not deprecated) in the Tier 4
   subpackage move. See BACKLOG.md "`orch.py`'s tick/loop engine is legacy;
   its path/bookkeeping layer is not" — still needs Jeremy to confirm the
   `maro tick`/`loop`/`plan` commands are actually unused before deprecating.
2. **`sandbox.py` — RESOLVED 2026-07-13:** retired as an unwired simulation
   during container-executor C4 (`69265f6`). Its only real pattern-list
   consumer moved to `run_curation.py`; containment work now lives in
   `container_exec.py`. See `docs/CONTAINER_EXECUTOR_DESIGN.md` §7.
3. **MCP tool dispatch** — logged as a bug to clean up later. See BACKLOG.md
   "MCP tools registered/advertised but never dispatchable (bug)."
4. **x-capture (Twitter) domain logic** — **not one bug, three uncoordinated
   builds.** `web_fetch.py` (generic URL + X oEmbed fallback), `channels.py`
   (GitHub/Reddit/YouTube, docstring falsely claims tool-registry
   registration), and `orch_bridges.py`'s x-capture salvage bridge (which
   doesn't fetch anything itself — it reads an artifact from an external,
   out-of-repo X-capture pipeline that doesn't exist in this repo) are three
   independent, non-duplicated implementations that were never unified.
   Jeremy wants this collapsed into one general reusable fetch skill rather
   than ad hoc per-feature fetch code. No formally tracked prior "standard
   skills" initiative was found — this is new scoping. See BACKLOG.md "Unify
   fragmented web/content-fetch capability into one skill" (covers this and
   item 8 together).
5. **Polymarket cluster** — **confirmed clutter, proceed with extract/delete.**
   Git history rules out "preserved test data": `polymarket_backtest*.py`
   were created-and-abandoned same-day (2026-04-01, zero callers);
   `backtester.py`/`backtest_metrics.py` are a literal one-off
   agent-generated artifact from a 2026-03-30 dogfood run; only later touches
   are mechanical rename/portability sweeps, not feature work. The separate
   "harvest orchestration history into a reusable test corpus" effort
   (`e7c2e4a`) is unrelated — it covers `output/runs/`/`projects/` workspace
   data, not `src/` code, and has zero dependency on any polymarket file. The
   actual research conclusions are already properly archived in
   `research/POLYMARKET_BTC_LAG_VALIDATION.md` et al. — original Tier 1
   recommendation stands. See BACKLOG.md "Polymarket cluster +
   quality_gate's debate pass are TradingAgents dogfood leftovers."
6. **`mission.py`/`goal_map.py`/`ancestry.py`** — **not one legacy layer,
   four distinct (and one duplicated) lineage mechanisms.** `ancestry.py`
   (per-project multi-hop chain), `goal_map.py` (same data + conflict
   detection), `thread_brain.py` (per-thread one-hop origin, in-progress
   Thread Architecture), and `recall.py`'s `_resolve_thread` (an independent,
   *disagreeing* second ancestry walk over run metadata) all coexist.
   `agent_loop.py` currently injects ancestry twice from two sources that can
   disagree — a real bug, not a design choice. Documented in full in the new
   "Goal Lineage" section of `docs/ARCHITECTURE_OVERVIEW.md`, satisfying
   Jeremy's ask for both visibility (the `maro ancestry` CLI survives as the
   durable surface once the dashboard is archived) and downstream-reference
   intent (currently only partial — `ancestry.json`'s chain is real but
   manually wired; consolidation plan is in BACKLOG.md "Ancestry
   double-injection: two disagreeing lineage sources in the loop prompt").
   Also fixed the stale `§18` → `§12` spec-section reference in
   `ancestry.py`'s docstring.
7. **`observe.py`'s dashboard** — **archived, not deleted.** Jeremy: "proof
   of concept that sort of failed." Moved to `archive/observe_dashboard.py`
   (code + full original-intent writeup in its docstring) with tests split
   to `archive/test_observe_dashboard.py`; `src/observe.py` no longer
   contains `_DASHBOARD_HTML`/`_snapshot_json`/`serve_dashboard`, and
   `maro-observe serve` now prints a pointer to the archive instead of
   running it. BACKLOG_DONE.md's three dashboard entries ("Dashboard as real
   tool," "Replay with factory mode," "Dashboard captain's log panel") are
   annotated needs-revisited with the original high-level+detail visibility
   intent restated. Forward item in BACKLOG.md: "Observability dashboard —
   archived, revisit the visibility goal with a different implementation."
8. **`channels.py`** — loose thread, not formally-tracked dropped scaffolding
   (grepped BACKLOG + git log + `STEAL_LIST.md`, found nothing — this is new
   scoping). Folded into item 4's fetch-skill consolidation entry in
   BACKLOG.md rather than tracked separately, since it's one of the three
   pieces that entry proposes unifying.
9. **`quality_gate.py`'s debate pass** — **confirmed TradingAgents-origin,
   domain-specific.** Added 2026-03-31, the day after `STEAL_LIST.md`'s dated
   TradingAgents dogfood entry, same commit cluster as three other named
   TradingAgents steal-items; architecture is a verbatim match to
   TradingAgents' actual Bull/Bear/Risk-Manager design, generalized but never
   given a production caller. Jeremy's read confirmed: trading-domain-shaped
   code, not something to integrate into general Maro. Tracked alongside
   item 5 in BACKLOG.md's polymarket-cluster entry — extract/delete both
   together.

## Explicitly out of scope (deliberate, in-flight, not mine to re-decide)

- **Navigator cutover timing** (`docs/DUMB_LOOP_AUDIT.md`, `src/navigator.py`)
  — a live, data-gated project already deciding, per decision point, when
  hardcoded heuristics in `handle.py`/`agent_loop.py`/`planner.py`/
  `director.py`/`scheduler.py` get replaced. Nothing in this plan touches
  that timeline; several findings above note "independent of the navigator
  question" specifically to keep that boundary clear.
- **Weakening any security check** — where `security.py`/`injection_guard.py`
  findings look redundant, the plan flags them as maintainer questions, not
  removals. The later `sandbox.py` retirement removed an unwired simulation,
  not a live-path check.

## Docs hygiene (small, do anytime)

`docs/history/2026-04-22-architecture.md` (formerly docs/ARCHITECTURE.md, now a dated record) is stale — predates the Poe→Maro rename, references a
deleted `poe.py`, and its module table (48 files/~29K LOC) is roughly 2.5x out
of date. `docs/ARCHITECTURE_OVERVIEW.md` is the better current reference
(conceptual, hasn't rotted at the file level) but still uses "Poe" branding
throughout. Recommend archiving `ARCHITECTURE.md` and rewriting
`ARCHITECTURE_OVERVIEW.md` as the canonical doc once Tier 4 lands (so it
describes the post-move structure, not the pre-move one).
