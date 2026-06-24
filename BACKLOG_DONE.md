# Backlog — Completed Archive

This is the history of shipped items. When something gets completed in BACKLOG.md, it moves here with its context intact so we keep the "why" / "how" / "source" for future reference.

Live items are in [BACKLOG.md](BACKLOG.md). This file is ingested by the correspondence module so `dev-recall` can surface prior decisions, rejected approaches, and "already-tried" context during new work.

Last split: 2026-04-16 (session 34).

---

### Captain's-log event contract doc — DONE (2026-06-24, was AFK chunk #8)

**Source:** Actionable Stack #8. "We have 36+ event types emitted across 10+ modules. No single doc says here's every event, field schema, when it fires."

**What shipped:** `docs/CAPTAINS_LOG_EVENTS.md` — entry schema (the 4 required + 4 optional `log_event` fields), rotation/reader behavior, and a category-grouped table of all **45 actively-emitted event types** (~52 call sites across 16 modules) with emitter file:line, `context` field names, and when-it-fires. Inventory done via an Explore-agent call-site sweep, then the load-bearing claims were code-verified.

**Bonus findings (now BACKLOG #8):** the inventory surfaced registry drift — 3 events emitted via string literals but missing from `EVENT_TYPES` (`EVOLVER_REVERTED`, `EVOLVER_VERIFY`, `PLAYBOOK_UPDATED`), and 3 defined-but-unemitted (`CANON_CANDIDATE`, `LESSON_RECOVERED`, `SKILL_REWRITE` — the last referenced by consumers in recall.py/evolver.py yet never produced). Documented in the doc's "Integrity gaps" section and filed as a cheap follow-up.

### Persistence-install guardrail for autonomous runs — DONE (2026-06-24, was BLOCKER #3)

**Source:** Actionable Stack #3 (BLOCKER). April 22 live-box cleanup found a stale scheduled goal (`Monitor BTC price`, created April 4) had been revived and installed BOTH cron and systemd automation — exactly the rogue-process failure that has burned tokens before. Background/scheduled paths (heartbeat, backlog drains, timers) must never install or enable persistence (systemd units, launchd agents, cron entries, login items, init scripts, long-lived daemons) without an explicit high-trust gate.

**What shipped (`src/constraint.py`):**
- New `persistence_install` pattern group (`_PERSISTENCE_PATTERNS`) wired into `_ALL_PATTERNS`, so it runs in the always-on, zero-cost, no-LLM constraint layer before any subprocess spawns. Covers `systemctl enable/start/daemon-reload`, writes to `/etc/systemd/system`, `/lib/systemd/system`, `~/.config/systemd/user`, `systemd-run`, `loginctl enable-linger`, `crontab -e/-`, `@reboot`, `/etc/cron.*`, `/etc/crontab`, `launchctl load/bootstrap`, `/Library/Launch{Agents,Daemons}`, `update-rc.d`, `chkconfig … on`, plus a natural-language intent pattern ("set up a cron job", "install a systemd service", "register a launchd agent", "add a login item", "schedule a systemd timer", …).
- **Fail-safe default: HIGH → block.** Unlike a `(rm -rf …)` hint in a plan, a persistence-install step's stated intent IS the action, so it is **exempt from the `is_description` softening** in `hitl_policy()` — it blocks at the real call site (`step_exec.py:688`, which passes `is_description=True`), not just on verified tool output. Blocked steps are recorded as stuck with a clear reason; that is the "propose but do not apply" outcome the backlog asked for.
- **Explicit high-trust opt-in:** `_persistence_allowed()` reads `POE_PERSISTENCE_ALLOW=1` (env) or `constraints.allow_persistence_install: true` (config). When set (attended run, deliberate operator choice), the flags downgrade HIGH→MEDIUM (warn + proceed). Background/scheduled paths must never set it — and the default is off, so "off switches stay off."
- Hits are logged to `constraint_log.jsonl` via the existing audit trail.

**Tests:** `tests/test_constraint.py::TestPersistenceInstallGuardrail` — 15 install patterns block by default, 8 benign steps don't false-positive ("create a service class", "enable verbose logging", "start the analysis", …), block survives the `is_description` path, the destructive-hint softening still works, and the high-trust gate downgrades to warn. Chose block-everywhere-by-default over block-only-when-unattended: simpler, strictly safer, and an attended operator can opt in per-run. Did not add a separate "unattended-mode" signal — the fail-safe default makes one unnecessary.

### Closure treats failed-to-run commands as checks-passed — FIXED (2026-06-24 backlog-audit catch)

**Source:** Actionable Stack #2. Scope A/B run-00 (2026-04-22): closure ran behavioral-verification commands as subprocesses with a PATH missing `/home/clawd/go/bin`; every compound died at the first `&&` with `go: command not found`, yet closure returned `complete=True, confidence=0.75, checks_passed=5/5`. The verification verdict was decoupled from whether anything was actually verified.

**What shipped (verified in code 2026-06-24):** `src/director.py:_check_outcome(exit_code, stderr)` (1343) classifies a probe as `pass` (exit 0), `inconclusive` (exit -1/126/127, or stderr matching "command not found"/"not on path"/"no such file or directory"/"timeout"), or `fail`. Probe results carry an `outcome` field; `verify_goal_completion` computes `inconclusive_checks = [r ... outcome == "inconclusive"]` (1578) and then **forces the verdict down** (1669–1681): `if complete and inconclusive_checks: complete = False`, confidence clamped to 0.6, and a gap "N verification probe(s) were inconclusive and cannot be counted as proof of completion" is appended. `inconclusive_count` is surfaced on the `ClosureVerdict` and the `CLOSURE_VERDICT` event. This is exactly the demanded behavior: INCONCLUSIVE never counts as passed, and an inconclusive probe blocks `complete=True`.

(Found still listed as open during the 2026-06-24 audit; the fix predated the audit. Sibling "runtime-probe bias" item in Vision/Deferred is a different axis — closure *choosing* static over behavioral probes — and remains open.)

### handle.py prefix registry (AFK chunk) — DONE (2026-06-24 backlog-audit catch)

**Source:** Actionable Stack #10. `apply_prefixes()` was a chain of if/elif on magic strings (`ralph:`, `verify:`, `pipeline:`, `strict:`, `effort:`, etc.); "what modifiers exist?" required reading the whole chain.

**What shipped (verified in code 2026-06-24):** `src/handle.py` now has a `_PrefixRule` dataclass (52), a `_PrefixResult` (61), a declarative `_PREFIX_REGISTRY: List[_PrefixRule]` (77) holding the rule set, and `_apply_prefixes(message) -> _PrefixResult` (98) that iterates the registry preserving stacking semantics. Called at handle.py:889. "What modifiers exist?" is now a one-line grep of `_PREFIX_REGISTRY`. Regression risk was low (tests already covered every modifier) and the suite is green.

### agent_loop.py monolith decomposition — COMPLETE (Phase F extracted, 2026-06-24)

**Source:** project_monolith_extraction.md memory; long-running incremental refactor (`LoopPhase` + `LoopContext` seam shipped early, phases A–E + G extracted across prior sessions).

**What shipped:** the last inline phase — Phase F (the ~900-line main execute loop, EXECUTE) — extracted from `run_agent_loop()` into a module-level `_execute_main_loop(ctx, steps, step_indices, *, ...) -> dict`, mirroring the established extracted-phase pattern (`_initialize_loop`, `_decompose_goal`, `_preflight_checks`, `_run_parallel_path`, `_prepare_execution`, `_build_result_and_finalize`). `run_agent_loop()` is now the thin orchestrator: it `set_phase`s and calls each phase in turn.

**How:** pure structural move (no behavior change). The loop body was copied verbatim; an alias block at the top of the new function reproduces the locals it relied on (`goal`, `max_iterations`, ctx-derived config, phase-result inputs). The function returns a dict of terminal state (`step_outcomes`, `loop_status`, `stuck_reason`, token totals, mutated `manifest_steps`/`replan_count`/`milestone_expanded`/`failure_chain`/`recovery_step_count`/`scratchpad`, and the possibly-mutated `goal`/`max_iterations`) consumed by Phase G and the auto-recovery re-run. `ctx.goal`/`ctx.max_iterations` are intentionally left untouched, matching pre-extraction behavior (finalize reads ctx.goal; auto-recovery reads the returned, interrupt/bump-mutated values).

**Why it mattered:** removes the dead-`ctx` NameError bug class that incomplete extraction risked (guarded by `tests/test_static_undefined_names.py`, which stayed green). The note "Phase F still inline / not yet extracted" is removed from `skills/arch-core-loop.md` and `docs/ARCHITECTURE_OVERVIEW.md`. Verified: py_compile OK, zero pyflakes undefined-names, full safe suite green (all 128 items).

(Backlog item text was "Next 2 phases: scope_generation_phase and step_execution_phase" — those were provisional names; the real decomposition followed the A–G phase boundaries and is now complete.)

### Provenance guards — done!=achieved when claimed I/O never happened (2026-06-24) — COMPLETE (v0 + both residuals)

- [x] **The verdict couldn't see whether a claimed artifact actually landed.**
  Surfaced by the shadow-eval per-class batch (n=42): a `general` step "list
  skills/ and save the listing to `artifacts/skills-listing.txt`" saved to a
  *different* path (`projects/<slug>/skills-listing.txt`) and narrated success.
  Local validator PASSed at confidence **1.00**; paid FAILed (requirement
  unmet). Confidence gating gave zero protection — the lever is provenance, not
  certainty. Same provenance-blindness root as the fabricated-input recovery
  bug and `verify_step` (text-only validators can't see side effects).

  **Fix (v0):** a deterministic output-provenance guard in `handle.py`
  (`_claimed_output_paths` / `_missing_claimed_outputs`) wired into both verdict
  paths — `_verify_now_outcome` (NOW/task-path, ahead of the LLM judge so it
  also saves the call) and the agenda twin (before the closure status-honesty
  block, works even when closure is None). When the goal names an output path
  **with a directory component** ("save … to `artifacts/X`") and the file isn't
  found under any reasonable base (cwd, repo root, run dir, workspace,
  workspace/output, `projects/*`), the run is demoted to `incomplete` /
  `goal_achieved=False` with `provenance_missing`. **Strictness rule:** the user
  said *where* (a path with a dir) → honor it exactly; a bare filename (just
  *what*) is out of scope (location ambiguous) to avoid false demotions.
  Deterministic, fail-open, default on (`validate.output_provenance`), reversible.

  **Residuals shipped same arc (2026-06-24):** both BACKLOG residuals closed,
  unified under a `_provenance_missing(goal)` aggregator that both verdict paths
  now call.
  - **Input-provenance** (`_claimed_input_paths` / `_missing_claimed_inputs`,
    `validate.input_provenance`, default on): a goal that names a *local,
    non-transient* input path ("read `/data/x.csv`") that doesn't exist demotes —
    you can't read a missing file. This is the verdict-layer net behind the
    recovery-seam guard: the recovery guard only fires on a *block*, so silent
    fabrication that reaches `done` without blocking now still gets caught. Remote
    URLs (`http(s)://`, `s3://`, `git@`, …) and transient paths (`/tmp/`,
    `scratchpad`, `/dev/`, `/proc/`) are skipped — they can't be checked or are
    legitimately ephemeral.
  - **Bare-filename outputs** (`_claimed_output_bare` / `_missing_output_bare`):
    a bare "save `report.md`" (no directory, but has an extension) whose basename
    exists *nowhere reasonable* (run dir, `workspace/output`, `projects/*`, incl.
    one/two levels deep) demotes — lenient because location isn't part of the
    contract, so a present-but-elsewhere file passes. Complements the strict
    dir-qualified check (exact path) from v0.

  12 tests (`TestOutputProvenanceGuard`) + now-status suite green; full suite
  green (4,278+).

  **Tool-evidence layer SHIPPED same arc (2026-06-24).** A fourth check that
  scans the RESULT text (not the goal) for paths the run claims it wrote
  (`_result_claimed_outputs` / `_missing_or_stale_result_outputs`,
  `validate.result_provenance`, default on). A claimed-written path demotes
  unless it exists AND its mtime falls within this run's wall-clock window
  (`_run_window_start` = now − elapsed_ms − 120s buffer; `_is_fresh`). **The
  mtime gate is the actual side-effect evidence** — a pre-existing stale
  same-named file does NOT prove the run wrote it, which a pure existence check
  (and the text-only judge) can't distinguish. This is the layer that catches
  fabrication when the GOAL names no path (the *claim* names it) and the n=42
  "narrated success, saved elsewhere/nowhere" case. Window start is None when
  elapsed is unknown → the gate is skipped (fail-open); remote/transient paths
  skipped. Both verdict paths pass `result_text` + `window_start` into the
  unified `_provenance_missing()` aggregator (NOW: `outcome["result"]` +
  `elapsed_ms`; AGENDA: concatenated done-step results + `loop_result.elapsed_ms`).
  6 new tests (18 in `TestOutputProvenanceGuard`), full suite green.

  **Investigated and ruled out — no transcript available.** `claude -p
  --output-format json` returns only `{result, is_error, usage, stop_reason}` —
  no `messages`/tool-call list/`num_turns`. So real "did a read/write tool fire
  on path X" evidence isn't exposed by the backend; the mtime-on-claimed-path
  approach is the strongest deterministic signal reachable without re-plumbing
  the subprocess to `--output-format stream-json`. Residual left in BACKLOG:
  fabrication that names NO path at all ("ran the tests: 142 passed", writes
  nothing) — genuinely unreachable without execution-trace evidence.

### Recovery fabricated missing inputs to fake success (2026-06-23) — FIXED

- [x] **Blocked-step recovery satisfied an impossible goal by fabricating its
  missing input.** Found while gathering dumb-loop-audit round-2 data (see
  `docs/DUMB_LOOP_AUDIT.md`). Goal: "read `/nonexistent/poe-test/data.csv`,
  compute the mean of its second column." The file does not exist. Instead of
  failing or escalating, the recovery tree (retry → split → redecompose, 5
  rounds) **fabricated a synthetic data.csv** (`[10.5, 20.0, 15.75, …]`),
  computed its mean (17.5), and declared the goal "fully satisfied." The
  navigator shadow wanted escalate/close on all 5 blocked-step decisions — it
  was right; the heuristic's "success" was a fabricated-data false positive,
  worse than honest failure.

  **Root cause (grounded):** `verify_step` is provenance-blind (sees only
  `(step_text, result)` strings — fabricated data is indistinguishable from
  real there, so a verifier prompt-patch would be both weak and the
  patch-the-prompt-with-a-taxonomy anti-pattern). The fabrication *originated*
  in the redecompose path (`agent_loop.py:707`): it calls
  `planner.decompose("read /nonexistent/data.csv …")`, which emits a "generate
  the data file" sub-step. The planner has no notion that a missing *external*
  input cannot be manufactured.

  **Fix:** a recovery-seam guard at the top of `_handle_blocked_step`. When a
  block is a missing-external-input (`_looks_like_missing_input` over
  stuck_reason/result) **and** the step is input-consuming
  (`_is_input_consuming_step` — read/open/load/parse/fetch/download/import/
  ingest/…), it short-circuits to honest `stuck` with a `MISSING_INPUT:` reason
  *before* any retry/split/redecompose branch. A missing external input can't
  be retried (won't appear), split, or manufactured. Routing fix grounded in
  the round-2 data (navigator escalate/close 5/5 at this exact point), not a
  prompt taxonomy. Conservative: producing steps ("create X") and ordinary
  transient errors on read steps fall through to normal recovery. 4 tests in
  `test_agent_loop.py` (`test_missing_input_*`,
  `test_input_consuming_step_normal_error_*`) + a direct proof the exact bug
  case short-circuits at retry depths 0 and 3. Defense-in-depth follow-up
  (closure-verdict provenance net) remains in BACKLOG.

### Session 20 (2026-04-14) — adversarial review findings (`output/self-review-report-20260414T040637Z-blind.md`)

- [x] **CRITICAL: Evolver broken state persistence** — FIXED (commit `4b8dd7e`). `_verify_post_apply` now tracks `applied_ids` and iterates `revert_suggestion` on test failure. 3 new tests cover fail→revert, pass→no-revert, and legacy int-count backward compat. The `revert_suggestion` no-op for `prompt_tweak` is honest now (lessons decay naturally) — separate item if we want true snapshot/restore.
- [x] **CRITICAL: Silent exception swallowing (systemic)** — FIXED (session 20.5, commit `d8364a6`). All 14 bare-pass exception sites in agent_loop.py first 1k lines upgraded: ERROR for safety/security/correctness (kill switch, interrupts, security scan, hooks); WARNING for resumption-affecting (checkpoint, manifest, dead_ends, claim verifier, skill outcome); DEBUG for telemetry. Also fixed lines 1000+ in the same session. Verified in session 22: no `except Exception: pass` patterns remain in agent_loop.py.
- [x] **CRITICAL: LoopPhase is string constants, not state machine** — FIXED (session 21). `LoopStateMachine` class with `_ALLOWED` transitions dict; `set_phase` raises `InvalidTransitionError`. Wired at 7 transition points in `run_agent_loop`. 8 tests.
- [x] **HIGH: Director bypassed in practice** — FIXED (session 21). Added `now_lane.escalate_to_director` config flag + `_is_complex_directive()` heuristic. Complex NOW-classified goals optionally reclassify to AGENDA for Director routing. Default: off (existing behavior unchanged).
- [x] **HIGH: Inspector signal reliability** — FIXED (session 20.5, commit `f0f6e36`). All 3 false-positive mechanisms fixed: (a) escalation tone: split tautological vs informative keywords, require ≥2 informative hits; (b) backtracking: sort outcomes by `created_at` chronologically before scanning; (c) context-churn: require ≥2 lessons + no keyword overlap with stuck narrative. +5 tests.
- [x] **HIGH: Evolver `cost_optimization` silent no-op** — FIXED (commit `4b8dd7e`). Explicit branch in `apply_suggestion` sets `applied=False`, `status=pending_human_review`, with block_reason. Test added. Real auto-apply executor still TODO if we ever want one.
- [x] **MODERATE: `_steps_are_independent` regex heuristic** — Expanded `_DEPENDENCY_PATTERNS` to catch aggregation verbs (compile/synthesize/aggregate/summarize/analyze) and generic prior-output references ("the findings", "based on results", "with the above", "given the data", "comparing the results"). 7-case regression test added. False-positive direction (mark independent as dependent) is safe — only disables parallelism. False-negative direction (the race-condition direction) is what got tightened.
- [x] **MODERATE: `rate^steps` math false alerts** — Replaced cumulative-product formula with a 5-step sliding window. Healthy 90% long runs no longer fire. Extracted `_compute_march_of_nines` helper for direct testing; 4 unit tests cover healthy long run, recent degradation, below-min-steps, exact-threshold boundary.
- [x] **MODERATE: Memory Stage 2→3 and 3→4 not implemented** — FIXED (session 21). Stage 2→3: evolver scans canon candidates, surfaces as crystallization Suggestions (human-gated). Stage 3→4: extract_skills() was silently broken (s.summary/s.step → AttributeError); fixed to use s.result/s.text. Skill crystallization now fires on successful runs.
- [x] **MODERATE: `_process_blocked_step` 18+ parameters** — Introduced `BlockedStepContext` dataclass; function now takes `(ctx, blk)` instead of 21 args. Body unchanged (unpack at top); call site rewritten to construct the dataclass.
- [x] **MINOR: `new_guardrail` permanently gated** — Now auto-applies in non-prod (default), held in prod. Override hierarchy: `POE_AUTO_APPLY_GUARDRAILS=1` forces on, `=0` forces hold, unset uses `config.environment` (default `dev`). 3 integration tests cover prod/dev/explicit-off paths.

### Session 20 infrastructure bugs

- [x] **File-claim verifier truncates first char of cited paths** — FIXED (commit `a34228b`). Tightened lookbehind from `(?<![\`'\"(])` to `(?<![\w\`'\"(])` so matches can't start one char into a backtick-wrapped path. 4 regression tests cover backtick/single-quote/paren/word-adjacent wrappers.
- [x] **pytest-via-subprocess 900s timeout** — FIXED (session 21). Default long-running timeout bumped 900→1800s; full-suite runs get 2× (3600s). `POE_LONG_RUNNING_TIMEOUT` env override. Better log message identifies full_suite vs long_running. 5 tests.
- [x] **`scripts/test-safe.sh` collection broken** — Fixed. Two-tier parse: try nodeid format first (`tests/path::test`), fall back to file-level (`tests/path.py: NN` → strip count suffix). Switched chunk dispatch from `$(cat chunk)` to `xargs -a chunk` for safer arg-passing. Now correctly chunks by file when pytest produces file-level output.

### Prior

- [x] **Flaky: test_mission_with_partial_milestone** — Fixed. Root cause: (1) `maybe_add_verification_step` fires on "analyze" in goal, adding extra step that exhausts ScriptedAdapter; (2) `negotiate_contract` + `grade_contract` consume 2 more LLM calls per feature; (3) `run_boot_protocol` + `run_hooks` added 10-90s latency. Fix: patch `_decompose`, `sprint_contract`, `boot_protocol`, and `hooks` in the test. Test now deterministic and <1s.

- [x] **Stale mission shortcircuit** — `poe_handle()` returned cached summary instead of new mission. Fixed: skip CEO layer when `--project` is explicit. (`e7ad725`)
- [x] **Rate-limit no recovery** — Claude "hit your limit" → immediate failure. Fixed: exponential backoff retry in `llm.py`. (`e7ad725`)
- [x] **Stale mission still possible without --project** — Fixed: CEO layer now only handles meta-commands (status/inspect/map); actual goals always go direct to run_agent_loop. (`low-hanging-fruit`)
- [x] **Flaky e2e tests** — Fixed `test_empty_result_step`, `test_loop_stuck_detection`, and `test_some_steps_done_some_stuck`. Root cause: multi-plan decompose (4 LLM calls) consumed execute-step responses out of sequence; `_generate_refinement_hint` called `build_adapter` (real subprocess, could block); Phase 45 auto-recovery re-ran with exhausted adapter. Fix: patch `_decompose`, `_generate_refinement_hint`, and `_recovery_in_progress` in affected tests. (2026-03-31)

### Verification / Hallucination Detection
- [x] **Adversarial verification step** — implemented in factory_thin (post-execute, pre-compile) and quality_gate (second pass on Mode 2 runs). Catches overclaimed mechanisms, wrong evidence tiers, contested findings. (`factory` branch, 2026-03-31)
- [x] **LLM Council / multi-angle critique skill** — 3 critics (devil's advocate, domain skeptic, implementation critic) run in `quality_gate.py` via `run_llm_council()`. Escalates if 2+ rate WEAK. Wired into `run_quality_gate(run_council=True)`. 21 tests. (2026-03-31)
- [x] **Cross-reference check** — `src/cross_ref.py`: extracts verifiable claims from step output, queries a fresh LLM context with no prior response (prevents confirmation bias), flags disputed claims. `ClaimVerification` + `CrossRefReport` dataclasses. Wired into `run_quality_gate(run_cross_ref=True)` as Pass 2.5. Disputed claims escalate the verdict. `poe-cross-ref` CLI. 39 tests. (2026-04-01)
- [x] **Confidence tagging** — each step result should carry a confidence indicator (strong evidence / weak evidence / model inference / unverified). Done: `confidence` field added to complete_step tool schema (optional enum), StepOutcome dataclass, and completed_context entries tagged with `[confidence:X]`. (2026-03-31)

### Token Efficiency
- [x] **Data pipeline enforcement** — `_is_data_heavy_step()` detects risky steps (keywords: fetch all, list all, polymarket-cli, etc.) and injects a stronger `DATA PIPELINE ENFORCEMENT` block into the user_msg. `_result_looks_like_raw_dump()` post-checks results (>2000 chars + high brace density or long lines) and prepends `[RAW_OUTPUT_DETECTED]`. 12 tests. (2026-03-31)
- [x] **Completed context compression** — older entries compressed to one-liner after step 5; last 3 steps kept at full length. 47-63% reduction at 7-12 steps. Zero token cost. (`agent_loop.py`, 2026-03-31)
- [x] **Lesson injection overhead** — Fixed: capped inject output at 1200 chars in memory.py. (`low-hanging-fruit`)
- [x] **System prompt token audit (Pi steal)** — Audited EXECUTE_SYSTEM and DECOMPOSE_SYSTEM against Pi coding agent's <1k target. Cut redundant negatives, editorial commentary, and duplicate BAD/GOOD examples. Result: EXECUTE_SYSTEM 844→333 tokens (-61%), DECOMPOSE_SYSTEM 1048→603 tokens (-42%), combined 1892→936 tokens (-51%). All behavior-changing content preserved. (2026-04-03)
- [x] **Architecture non-goals doc (Pi steal)** — `docs/ARCHITECTURE_NON_GOALS.md` documents 8 deliberate non-goals with rationale: tool minimalism, MCP-as-default, interactive gating, hidden sub-agents, Neo4j, plugin marketplace, provider portability contracts, headless UI. Helps say no cleanly to scope creep. (2026-04-03)
- [x] **Compact notation / shorthand vocabulary** — TESTED, NOT RECOMMENDED (2026-04-10). A/B test: 9 rounds on cheap model, avg +0.7% reduction (median +9.3%), range -97.8% to +63.6%. Variance too high — LLM doesn't reliably adopt shorthand. Sometimes spends *more* tokens mixing styles. `always_inject` stays false. Existing measures (500-tok target, context compression, pipeline enforcement) are sufficient. LLMLingua remains deferred option if server-side compression is needed. A/B harness: `compact_ab.py`, report at `output/compact_ab/`.

### Self-Improvement Loop
- [x] **Evolver signal scanning** — `scan_outcomes_for_signals()` in `evolver.py`. Scans done outcomes for actionable leads/opportunities, converts to `sub_mission` Suggestion entries. Wired into `run_evolver(scan_signals=True)`. 8 tests. (2026-03-31)
- [x] **Phase 46: Intervention Graduation** — `graduation.py` shipped. Scans diagnoses for repeated failure classes (≥3x), proposes high-confidence Suggestions that evolver auto-applies. 8 failure classes covered. CLI: `poe-graduation`. (2026-03-31)
- [x] **Verification patterns on rules** — each graduated rule gets a machine-checkable test before going fully permanent. Done: `verify_pattern` shell command on all 8 templates; `verify_graduation_rules()` and `poe-graduation --verify` CLI. (meta_alchemist pattern, Phase 46 follow-on, 2026-03-31)
- [x] **Problem generation (Agent0)** — Research complete (2026-04-05). 8/8 steps, $2.49, loop `ee4d5e86`. Key: two-agent co-evolution (Curriculum + Executor), R_unc frontier reward (target 50% solve-rate), no human labels. Mapped to Poe: failure-chain recording (DONE), majority-vote pseudo-labels (TODO M), frontier task targeting (TODO M), skill validation harness (TODO M). See `docs/research/agent0-synthesis.md`. Added steal items: `failure_chain` field on Outcome shipped; remaining items in STEAL_LIST.
- [x] **LLM + genetic programming (FunSearch)** — All steal items complete (2026-04-05). Implemented: compactness-adjusted scoring, ranked-candidate mutation context in rewrite_skill, pre-scoring discard gate, skill stemmer, island model diversity, replay-based fitness oracle (`src/strategy_evaluator.py` — TF-IDF cosine over outcomes.jsonl, no LLM in eval path; wired into frontier rewrite loop as pre-score gate; 35 tests). Design doc: `docs/research/funsearch-agent-design.md`.

### Director / Judgment Quality
- [x] **GStack Tier 1 — Decision taxonomy + confidence gates** — `EscalationDecision` extended with `decision_class` + `confidence`; `handle_escalation()` enforces user_challenge→surface, low-confidence→surface, medium-confidence caveat; anti-sycophancy rules in escalation prompt; calibration logging to `memory/calibration.jsonl`. 6 tests. (2026-04-04)
- [x] **GStack Tier 2 — Calibration review loop** — `scan_calibration_log()` in evolver.py; flags high override rate + low mean confidence; wired into `run_evolver(scan_calibration=True)`. 10 tests. (2026-04-04)

### Director / Mission Level
- [x] **Clarification milestone** — director asks user for clarification on ambiguous goals before committing resources. YOLO option. Done: `check_goal_clarity()` in intent.py, wired in handle.py AGENDA path; skippable with `yolo: true` in user/CONFIG.md. (2026-03-31)
- [x] **User-level config defaults** — Added user/CONFIG.md. Wired: default_model_tier. Documented: yolo, always_skeptic, notify_on_complete. (`low-hanging-fruit`)
- [x] **Skip-Director experiment** — `_is_simple_directive()` classifier (≤15 words, no complex keywords); `skip_if_simple=True` in `run_director()` routes to `run_agent_loop` directly; `direct:` prefix in `handle.py` forces AGENDA lane + skips quality gate + escalation overhead. `skip_if_simple=True` wired into `telegram_listener.py`. 28 tests (classifier + integration). (2026-03-31)
- [x] **Multi-agent debate pattern** — `run_debate()` in `quality_gate.py`: Bull argues FOR output, Bear argues AGAINST, Risk Manager gives PROCEED/CAUTION/REJECT. CAUTION+REJECT escalate. Wired into `run_quality_gate(with_debate=True)` as Pass 4. `DebatePosition` + `DebateVerdict` dataclasses. 15 tests. Bug found: `import json` missing inside outer try block — all parsing failed silently. (2026-03-31)

### Phase 65 — Constraint/Premise Orchestration (proposed, not yet implemented)

See `docs/CONSTRAINT_ORCHESTRATION_DESIGN.md` + `docs/CONSTRAINT_ORCHESTRATION_REVIEW.md`. Items below are the review's sharp findings that must be resolved before code lands.

- [x] **Rename decided: "scope".** (2026-04-16) `ScopeSet`, `generate_scope()`, `src/scope.py`. Rationale: captures both what IS and what IS NOT in the bounded space (complements specs). Avoids collision with `src/constraint.py` (HITL/risk harness).

### Observability
- [x] **Dashboard as real tool** — Added: Cost panel (24h spend, per-model breakdown from step-costs.jsonl), Mission Ancestry Tree (scans all workspace projects, shows parent/child depth), Replay button (POST /api/replay re-runs last outcome's goal in background thread). 12 tests. (2026-03-31)
- [x] **Replay with "factory mode"** — evolver signal scan on recent outcomes → queues highest-confidence sub-missions as new goals. `/api/replay-factory` endpoint + "Factory Mode Replay" button in dashboard. 4 tests. (2026-04-05)

### Factory Mode Experiment (Mode 3 test)
- [x] **"factory" branch** — created. Two variants: `factory_minimal` (single-call Haiku $0.04-0.06/60s) and `factory_thin` (loop+adversarial Haiku $0.38/375s). Bitter Lesson result: minimal surprisingly competitive; thin+adv matches Mode 2 quality at ~2x lower cost. Scaffolding that's load-bearing: adversarial verification. Scaffolding that's not: persona routing, lesson injection, multi-plan comparison. (2026-03-31)
- [x] **Factory comparison complete** — Full comparison table in /tmp/factory-comparison.md. Key: thin+adv+verify nootropic: $0.36/493s/6 steps done. thin+adv polymarket: $1.40/574s/7 of 8 steps (Haiku token explosion on research = 4.4× Mode 2 tokens, so cost advantage disappears for complex goals). Mode 2 polymarket: $1.27/1156s/8 steps done on Sonnet. (2026-03-31)
- [x] **Factory branch merge decision** — Adversarial patterns already merged to main (quality_gate two-pass, handle.py contested claims). `mode:thin` prefix added to handle.py — routes to factory_thin loop for wall-time-sensitive goals. Ralph verify (--verify) validated useful for research goals. 4 tests. (2026-03-31)
- [x] **Token efficiency prompt in factory_thin** — Added "Target under 500 tokens" constraint to FACTORY_STEP. Matches Mode 2's EXECUTE_SYSTEM language. (2026-03-31)
- [x] **Factory branch merge decision** — Adversarial patterns already merged to main. Factory files (factory_minimal.py, factory_thin.py) available as standalone modules. Full merge (factory to main) done 2026-03-31.
- [x] **Factory overnight experiment** — Ran factory_minimal on PAI goal (overnight 2026-03-31→04-01). Result: subprocess adapter timed out at 300s on first call — factory_minimal is a single-call approach and the PAI goal is too complex for one 300s window. Key insight: factory_minimal's single-call architecture has a hard ceiling at the subprocess timeout; complex research goals need factory_thin's loop approach or Mode 2. Documents the Phase 49 prerequisite: need timeout config to make factory experiments reliable.

### From X research (2026-04-11 — 10 posts, live orchestration, 2 loops)

Full report: `~/.poe/workspace/output/x-research-20260411T081706Z.md`

- [x] **Advisor Pattern** — (2026-04-11 session 16) `advisor_call()` in llm.py. Wired into: stuck detection, evolver medium-confidence gate (0.6-0.79), milestone boundary decompose failures, recovery plan wisdom check. Source: @aakashgupta.
- [x] **Codebase Graph + LSP** — DONE (session 26, AST-only, no LSP). `src/codebase_graph.py`: 5-pass AST analysis (collect, parse, resolve imports, centrality, rank). Basename import resolution. Centrality = 0.7×in_degree + 0.3×line_coverage. Goal-biased ranking in `format_graph_context()`. Wired into `_build_loop_context()`. 39 tests. `llm.py` confirmed tops centrality (54 importers). LSP deferred (overkill given AST already works). **Priority 9/10.** Source: @bniwael / SoulForge.
- [x] **Evals-as-Training-Data flywheel** — (2026-04-11 session 16) `mine_failure_patterns()` → `generate_evals_from_patterns()` → `run_eval_flywheel()`. Failure-class scoring for 9 types, trend tracking, auto-suggestions. Wired into `run_nightly_eval()`. 29 tests. Source: @realsigridjin.
- [x] **Thinking Token Budget** — (2026-04-11 session 16) `THINKING_HIGH/MID/LOW` constants, `thinking_budget` param on all adapters. AnthropicSDK: extended thinking API. Wired into decompose (HIGH) and advisor_call (MID). Source: @av1dlive.
- [x] **Harness Is the Problem** — DONE (session 24, 2026-04-14). `scan_harness_friction()` in harness_optimizer.py: aggregates adapter_error, timeout, retry_storm, tool_error, phase_failure signals from traces. FrictionPoint + HarnessFrictionReport. Wired into `run_evolver(scan_harness_friction=True)`. `--friction` CLI flag. 19 new tests. category="harness_friction" Suggestions surfaced for medium/high severity. Source: @sebgoddijn / Ramp Glass.
- [x] **Harness Architecture Spectrum** — DONE (session 26). Friction scan wired into inspector heartbeat tick alongside run_inspector() (heuristic, no LLM). Inspector friction summary injected into quality gate Pass 1 user message. Checkpoint audit: NOW is intentionally thin (1-shot), AGENDA has pre-flight + quality gate + post-hoc inspector. Injection guard wired at synthesize_skill(). **Priority 7/10.** Source: @akshay_pachaar.
- [x] **Event-driven subprocess wakeup** — FIXED (session 22). `run_agent_loop` calls `post_heartbeat_event("loop_done", payload=project)` after releasing the loop lock. Heartbeat's `_wakeup_event.wait()` unblocks immediately → next task picked up in near-zero time instead of waiting up to `interval` seconds. 3 tests. Source: @teknium / NousResearch hermes-agent.

### Session 15 bugs (2026-04-11)
- [x] **memory_dir split-brain** — `orch_items.memory_dir()` and `config.memory_dir()` resolved to different locations. Captain's log went to `~/.poe/workspace/memory/` while everything else went to the repo's `memory/`. Fixed: `orch_items.memory_dir()` now defaults to `~/.poe/workspace/memory/` (same as config.py) when no workspace env var is set. Tests unaffected (they pin OPENCLAW_WORKSPACE).
- [x] **_check_cycle false-positive** — task_store cycle detection raised on linear A→B→C chains. Root cause: added job_id to visited set then found it on first recursive call. Fixed: track visited deps, not the job being checked.
- [x] **user_goal queue** — `enqueue_goal()`, `enqueue_goals()`, `poe-enqueue` CLI. Director-level queue for user-submitted missions. Sequential blocking via task_store DAG deps.

### Conversation Mining (Phase 48 idea)
- [x] **Research pass through Telegram + Claude session data** — DONE (2026-04-05). `poe-mine --no-git` scanned 902 session log ideas → 336 unique after dedup. High-confidence (11): mostly already in BACKLOG. No new ideas injected above threshold. Notable finding from sessions: "knowledge graveyard" concept (temp storage for sub-goal learnings), "positive mid-IQ agent" (ralph approach, done), context size concern for sub-agents (done via context_firewall). Scan tool: `src/convo_miner.py`.

### From real-world regression runs (2026-04-12, session 18 — 4 parallel goals)

Ran 4 live goals: Polymarket research, nootropic synthesis, recipe site build, self-audit.

**Bugs found:**
- [x] **Output path resolution** — FIXED (session 19). Replaced 5 hardcoded `orch_root() / "prototypes" / "poe-orchestration" / "projects"` paths with `_project_dir_root()` → `orch_items.projects_root()`. Output now goes to `~/.poe/workspace/projects/<slug>/`.
- [x] **Subprocess adapter orphan process leak** — FIXED (session 19). `_run_subprocess_safe()` with `start_new_session=True` and `os.killpg()` on timeout/completion. Applied to ClaudeSubprocessAdapter + CodexCLIAdapter. Still needs: (a) subprocess cwd pinning so `claude -p` doesn't run tests on wrong codebase, (b) process count guard in heartbeat.
- [x] **Stale test skills in workspace** — FIXED (session 22). `poe-doctor --cleanup-skills` now detects `compute_skill_hash(skill) != stored_hash` (stale hashes from test fixtures). Removes them in Pass 1 before dedup. `_skill_hash_is_stale()` helper + `skills_path` kwarg for testing. 6 tests. Ran on live workspace: 15 stale-hash + 2 dup removed, 14 clean skills remain.
- [x] **Playbook deduplication bug** — FIXED (session 19). `append_to_playbook()` now checks if core entry text exists before appending. Also wrapped with `locked_write()`.
- [x] **skills.py read-modify-write race** — FIXED (session 19). `save_skill()` and `record_skill_outcome()` now use `locked_write()` from file_lock.py.
- [x] **Constraint false-positive on step descriptions** — FIXED (session 22). Two-part fix: (a) DECOMPOSE_SYSTEM prompt gets STEP DESCRIPTION STYLE section — "describe task/outcome, not shell commands"; (b) `hitl_policy(is_description=True)` downgrades DESTROY→WRITE and caps HIGH risk at MEDIUM for planner-generated step text. step_exec.py passes `is_description=True` for the pre-LLM scan. 3 tests.
- [x] **11 unlocked bare-append JSONL paths** — FIXED (session 22). Added `locked_append()` to file_lock.py; converted 11 highest-traffic sites (captains_log, memory_ledger×5, metrics, evolver×4, inspector×2). Also fixed knowledge_web.py (nodes+edges). +5 tests.
- [x] **Inspector dual report classes (InspectorReport vs InspectionReport)** — RESOLVED by documentation (session 22). Added explicit docstrings to both classes: InspectorReport = heavyweight spec §12 via run_inspector(); InspectionReport = lightweight scan via run_inspection_cycle(). Separate storage files, separate purposes. No merge needed.
- [x] **Inspector verify_claim_tiered P1/P2 threshold asymmetry** — RESOLVED (session 23). Asymmetry is intentional: standing rules are authoritative and written for broad applicability; fixed match-2 threshold is deliberately looser than P1's proportional. Added inline comment in inspector.py explaining the rationale. No threshold change needed. P4.
- [x] **Cross-backend failover on 4xx/5xx** — FIXED (session 22). `build_adapter("auto")` returns `FailoverAdapter` (wraps all available adapters in priority order). On 402/401/403/5xx errors, tries next backend automatically. Single-backend case returns adapter directly. `_is_failover_error()` for explicit checks. Logs WARNING on failover. 14 tests. Closes BACKLOG P2.
- [x] **Director persona authoring skill** — DONE (session 22). `record_persona_dispatch()` logs persona selections with is_fallback flag to memory/persona-dispatch-log.jsonl. `scan_persona_gaps()` groups fallback clusters by inferred role (keyword-verb matching), returns gaps with ≥3 occurrences. `run_evolver(scan_persona_gaps=True)` converts gaps to persona_authoring Suggestions (confidence=0.75, human review before auto-apply). handle.py calls record_persona_dispatch() after persona_for_goal() in AGENDA path. +6 tests.
- [x] **Prompt-injection hardening for persona + skill ingestion** — DONE (session 26). `src/injection_guard.py`: 17 regex patterns (override/tool-call/exfil), allowlist (skills/personas/workspace/builtin/internal), `InjectionScanReport` with risk_level + safe_to_auto_apply, fail-closed. Wired into: `scan_personas_dir()` YAML loading, `create_freeform_persona()` goal scanning, `evolver.apply_suggestion()`, `evolver.synthesize_skill()`. 59 tests. P3.

**Architectural gaps surfaced:**
- [x] **Phase audit: verify "done" phases against current code** — DONE (session 23). Verified phases 44-62: all implementations are real, not surface-level. Phase 45 "action side never closed" was stale — plan_recovery() is wired at agent_loop:4181-4227. Phase 48 (convo_miner), 50 (thinkback.py), 51 (passes.py), 53 (poe_self.py), 54 (checkpoint.py), 55 (knowledge_web.py), 56 (memory.load_standing_rules), 57 (llm.MODEL_*), 58 (pre_flight.PlanReview), 59 (record_tiered_lesson/detect_goal_gaps), 60 (inspector.InspectorReport) all verified present and importable. No phantom phases found.
- [x] **Cross-ref not wired into step execution** — FIXED (session 19). `verify_step_with_cross_ref()` in step_exec.py. Heuristic `_has_specific_claims()` detects file paths, line numbers, function names. Triggers cross-ref for specific claims. Annotates disputes, doesn't block.
- [x] **No anti-hallucination prompt in EXECUTE_SYSTEM** — FIXED (session 19). ANTI-HALLUCINATION section + NEED_INFO mechanism added to EXECUTE_SYSTEM. Steps can say NEED_INFO: [what's missing] to trigger research sub-steps.
- [x] **Shared artifact layer for step context** — FIXED (session 19). `complete_step` tool extended with `artifacts` field. Stored in `loop_shared_ctx` as `artifact:{step}:{name}`. Injected into subsequent steps as "Artifacts from prior steps" block.
- [x] **PAT missing pull_requests:write** — Fixed mid-session by Jeremy (session 18). Token 2 now has PR write permission.

**Test goal results:**
- Polymarket: 8/8 done, 1.47M tokens, 16min, quality gate PASS (0.85), 3 contested claims
- Nootropic: 8/8 done, 544K tokens, 12min, quality gate PASS (0.80), 5 contested claims
- Recipe site: 10/10 done (pending confirmation)
- Self-audit: 11/11 done, found 5 contradictions + structural bugs, 2 critical races


**Output routing policy:**
- [x] **Artifact output routing cleanup** — DONE (session 21). Per-step artifacts deleted at loop end by default. Config `keep_artifacts: true` retains them. Permanent files (PARTIAL.md, plan.md, loop log, scratchpad) always kept. Implemented in agent_loop.py around line 1650.

### Architectural (from self-review pass 5, 2026-04-10)
- [x] **Extract LoopStateMachine from agent_loop.py** — DONE (2026-04-10). 16 methods extracted across 14 commits. run_agent_loop reduced from ~1,800 to ~470 lines. While loop body is ~300 lines of orchestration (budget checks, step execution call, extracted method dispatch). All heavy logic in standalone functions. Next: convert to LoopStateMachine class where LoopContext becomes `self`.
- [x] **Break circular import skills.py ↔ evolver.py** — (2026-04-12) Extracted `Skill`, `SkillStats`, `SkillTestCase`, `SkillMutationResult`, `compute_skill_hash`, `verify_skill_hash`, `skill_to_dict`, `dict_to_skill` to `src/skill_types.py`. Both modules import types from there. skills.py re-exports for backward compat.

### From adversarial review (2026-04-12, 3 rounds — haiku + full model)
- [x] **Test isolation: workspace + API key leakage** — (2026-04-12) 62 test files had no workspace isolation. Added `tests/conftest.py` with autouse fixture: `POE_WORKSPACE` → tmp, API keys stripped, credential file paths redirected. Prevents tests from writing to `~/.poe/workspace/` or hitting real LLM endpoints.
- [x] **Director 500-char context truncation** — (2026-04-12) `director.py:503` truncated worker results at 500 chars when building context for final report. Bumped to 2000.
- [x] **agent_loop cost-warn flag persists across runs** — (2026-04-12) `_cost_warned` set on function object, never reset. Added reset at top of `run_agent_loop()`.
- [x] **test_loop_stuck_detection failure** — (2026-04-12) `AlwaysStuckAdapter` had no `model_key`, so tier-up replaced it with real `ClaudeSubprocessAdapter`. Added `model_key = "explicit-test"` to prevent override.
- [x] **Evolver auto-apply integration test** — Already exists at `tests/integration/test_evolver_apply.py` (12 tests, 350s). Covers skill mutation, change_log, backup, prompt_tweak→lesson, guardrail gating, confidence thresholds. Adversarial review missed it (looked only in `tests/`, not `tests/integration/`).
- [x] **workers.py minimum viable tests** — (2026-04-12 session 17) 22 tests: dispatch routing, type inference, crew sizing, mock adapters.
- [x] **constraint.py enforcement tests** — Already had 62 tests. Adversarial review hallucinated this gap.
- [x] **Evolver confidence calibration** — DONE (session 22). `_record_suggestion_outcomes()` writes per-suggestion verified/passed outcomes to suggestion_outcomes.jsonl. `scan_suggestion_outcomes()` computes empirical pass rate vs mean self-reported confidence, flags systematically overconfident categories. Wired into `run_evolver(scan_suggestion_calibration=True)`. +6 tests.
- [x] **Evolver suggestion rollback API** — (2026-04-12 session 17) `revert_suggestion(suggestion_id)` reads change_log.jsonl, reverses action based on before_state (restore skill desc, remove created skill, remove dynamic constraint). CLI: `poe-evolver --revert <id>`. Logs EVOLVER_REVERTED to captain's log.
- [x] **LoopStateMachine conversion** — DONE (session 23 continued, 2026-04-14). `LoopStateMachine(LoopContext)` — inherits all context fields; instance `set_phase(new_phase)` replaces classmethod. `_initialize_loop` creates `LoopStateMachine()`. 6 production call sites + 8 test functions updated. +1 subclass check test.

### Session bugs (2026-04-11)
- [x] **Meta-command detection false-positives** — (2026-04-11) Rebuilt with two-tier hard gate: (1) reject if message contains URLs or is >12 words — missions are long; commands aren't. (2) exact phrase match only — no substring tricks. Slash-commands are prefix-only. Eliminates the template-placeholder collision class: `inspector.py`, `/status/123`, `status=done` all correctly rejected. 3 tests added, 1 test updated. (`src/poe.py`)

### From adversarial review (2026-04-11 seeded-haiku, escalated to sonnet)
- [x] **platform_confusion detection stub** — (2026-04-11) Added to batch `detect_friction()` with expanded 6-keyword set (summary, stuck_reason, result_summary). Was only in heuristic `detect_friction_signals()`.
- [x] **Evolver auto-apply audit trail** — (2026-04-11) Enriched `change_log.jsonl` with `suggestion_text`, `confidence`, and `before_state` (old skill description on updates, mutation type for creates/appends). Enables rollback without guessing from a hash.
- [x] **repeated_rephrasing threshold** — (2026-04-11) Lowered from 3 to 2. Most failure loops die at 2 attempts.
- [x] **CLI enqueue --reason ignored** — (2026-04-11) `--reason` CLI arg was parsed but silently overwritten by constructed payload. Fixed: explicit `--reason` used when provided, falls back to payload when default.
- [x] **Evolver drift detection** — (2026-04-11) `scan_quality_drift()` tracks per-cycle quality snapshots in `evolver-baselines.jsonl`. Flags when success_rate drops or avg_cost rises beyond 15% of rolling baseline for 3+ consecutive cycles. Wired into `run_evolver(scan_drift=True)`. Generates observation suggestions with escalating confidence.
- [x] **Lesson contradiction check** — (2026-04-11) `check_contradiction()` in knowledge_lens.py uses text similarity + negation keyword pairs to detect opposing rules. Wired into `observe_pattern()` — blocks promotion when candidate contradicts existing standing rule. Also wired `observe_pattern()` into `promote_lesson()` in knowledge_web.py, closing the standing-rules pipeline (was dead code).
- [x] **Early model escalation on wide-scope goals** — (2026-04-11) Two-layer fix: (a) handle.py now lifts model to mid when pre-flight scope=wide/deep (zero-cost, <1ms heuristic check before adapter build); (b) agent_loop.py trajectory check after step 3 — if done-rate <50% on cheap model, raises session floor to mid for remaining steps. Both reuse existing infrastructure (estimate_goal_scope, _session_tier_floor). No new LLM calls.
- [x] **Inspector threshold calibration** — (2026-04-11) Extracted 6 hardcoded thresholds to module-level variables with env var overrides (INSPECTOR_BREACH_THRESHOLD, INSPECTOR_ESCALATION_MIN_HITS, INSPECTOR_CONTEXT_CHURN_TOKENS, INSPECTOR_ALIGNMENT_GOOD, INSPECTOR_ALIGNMENT_POOR, INSPECTOR_REPHRASING_MIN_COUNT). Added `inspector_thresholds()` for introspection. Calibration mode against historical outcomes deferred — needs real run data first.
- [x] **Handle result formatting unification** — (2026-04-10) pipeline/team/direct/default AGENDA paths in handle.py had 4 near-identical LoopResult→HandleResult formatting blocks. Extracted `_loop_result_to_handle()` helper. Original BACKLOG framing ("plan_NOW/plan_AGENDA/replan are 3 implementations") was inaccurate — they're architecturally different planning modes (NOW=1-shot, Director=multi-ticket, decompose=step pipeline), not duplicated code.

### From adversarial review (2026-04-11, Opus deep scan)
- [x] **Shell injection in runtime_tools.py** — (2026-04-11) CRITICAL. `subprocess.run(shell=True)` with unsanitized LLM args. Fixed: shlex.quote all args before substitution, shlex.split instead of shell=True.
- [x] **Missing `os` import in evolver.py** — (2026-04-11) CRITICAL. `os.environ.get("POE_AUTO_APPLY_GUARDRAILS")` silently crashed with NameError, caught by bare except. Guardrail gate never fired. Fixed: added `import os`.
- [x] **Broken `import o` in scan_calibration_log** — (2026-04-11) CRITICAL. Nonexistent module `o` made calibration scan dead code. Fixed: `from orch_items import memory_dir`.
- [x] **`_reinforce_tiered_lesson` stale data race** — (2026-04-11) HIGH. In-memory mutation lost because `_rewrite_tiered_lessons(tier)` re-loaded from disk. Fixed: reload, replace mutated lesson, pass explicit list to rewrite.
- [x] **File handle leak in handle.py** — (2026-04-11) MEDIUM. `_inputs_path.open().write()` without `with` leaked fd per message. Fixed: `with` block.
- [x] **Operator precedence bug in observe_pattern** — (2026-04-11) MEDIUM. `or` vs `and` precedence caused empty-domain hypotheses to match across unrelated domains. Fixed: explicit parens + require non-empty domain for fuzzy match.
- [x] **Tiered lessons missing adversarial check** — (2026-04-11) MEDIUM. `record_tiered_lesson()` had no `_lesson_looks_adversarial()` check (flat-tier did). Fixed: added check at entry.
- [x] **Wrong attribute in record_step_trace** — (2026-04-11) LOW. `getattr(s, "step")` should be `getattr(s, "text")` per StepOutcome dataclass. Removed phantom `summary` field.
- [x] **Dynamic constraint DoS potential** — (2026-04-11) TTL on dynamic constraints (`added_at` + `_DYNAMIC_CONSTRAINT_TTL_DAYS`, default 30d). Circuit breaker opens after N consecutive dynamic-only blocks (`_DYNAMIC_BLOCK_CIRCUIT_BREAKER`, default 5), disables for cooldown window. 8 tests.
- [x] **Parallel fan-out skips security scanning** — (2026-04-11) `_run_steps_parallel()._run_one` now runs `scan_external_content` on step result; HIGH-risk → blocked, lower risk → sanitized in-place. Ralph verify not added (requires session-level state incompatible with fan-out).
- [x] **Constraint checker combines goal text** — (2026-04-11) `_check_patterns` changed to `step_text.lower()` only. Goal text excluded to prevent goal-keyword false-positives (e.g. goal containing "research" blocking every research step). 2 tests.
- [x] **Security scanner 50K truncation bypass** — (2026-04-11) `sanitized` now always bounded to `scan_target` (max_length chars). Before: no-signal path returned full `text`, allowing injection past position 50K. 2 tests.

### Adversarial review (2026-04-11, session 15 self-review via orchestration)
- [x] **BUG-1: verbose always True** — `verbose=args.verbose or True` → `verbose=args.verbose`. Two call sites in handle.py.
- [x] **Dead imports/vars** — 7 items cleaned: sys/time/uuid from poe.py, os/field/_btw_t0 from handle.py, field/Any from orch_items.py.
- [x] **BUG-2: lock file open mode** — (2026-04-11 session 16) `_lock_task` now opens with `'a'` mode. Prevents inode deletion race where another process could unlink+recreate between touch and open.
- [x] **BUG-3: project starvation sort** — (2026-04-11 session 16) `select_global_next` now prefers oldest mtime for equal-priority projects (inverted tiebreak). Most neglected project gets picked.
- [x] **SEC-2/SEC-3: f-string + swallowed exc** — (2026-04-11 session 16) Fixed 4 f-strings without placeholders in poe.py. Swallowed mission dispatch exception now logged at DEBUG.

### Memory / Knowledge Layer (K stages — from research/orchestration-knowledge-layer)
- [x] **K3 partial: Captain's log read bridge** — (2026-04-11) Captain's log (11K events, write-only since creation) now wired as read source into: (1) decompose context injection in `agent_loop.py` — planner sees last 5 actionable learning events; (2) evolver LLM analysis in `evolver.py` — evolver sees recent skill/rule changes before generating suggestions. Filters: SKILL_PROMOTED/DEMOTED/CIRCUIT_OPEN, EVOLVER_APPLIED, DIAGNOSIS, HYPOTHESIS_PROMOTED, STANDING_RULE_CONTRADICTED, RULE_GRADUATED. (`captains_log.load_log()` API already existed — just had zero consumers.)
- [x] **memory.py decomposition (K1-aligned)** — DONE (2026-04-10). 2,968→530 lines (82% reduction). Split into: `memory_ledger.py` (944L — outcomes, lessons, compression, step traces), `knowledge_web.py` (1,006L — tiered lessons, decay/promotion, TF-IDF, canon tracking), `knowledge_lens.py` (758L — rules, hypotheses, decisions, verification). memory.py is now a thin public API with re-exports + coordination functions (bootstrap_context, reflect_and_record, inject_lessons_for_task).
- [x] **Consolidate knowledge layer research** — (2026-04-10) Merged into `docs/knowledge-layer/` as canonical location. Architecture, K-stages, research landscape, gaps docs moved from research/. Raw transcripts archived. README with K-stage status table added. K0 (baseline) and K1 (module split) marked DONE.
- [x] **llm_parse.py test coverage** — (2026-04-10) 68 unit tests added. Covers all 6 public functions + edge cases (None, NaN, fences, type mismatch unwrapping).

### Test Coverage Gaps (from 2026-04-10 audit)
- [x] **task_store.py tests** — (2026-04-10) 36 unit tests added. Covers enqueue/claim/complete/fail/archive, dependency resolution, cycle detection, stale claim recovery, atomic writes.
- [x] **orch.py tests** — DONE. test_orch_core.py has 48 tests covering start/finalize_run, run_tick, run_loop, run_once, validation hooks, artifact path validation, worker session bridge, manifest-driven execution. Item was stale.

### Data Portability / Workspace Consolidation (hardening)
- [x] **memory_dir consolidated** — (2026-04-11) `orch_items.memory_dir()` and `config.memory_dir()` now both default to `~/.poe/workspace/memory/`. Captain's log + all learning data in one place.
- [x] **Two-tier YAML config** — (2026-04-11) `~/.poe/config.yml` (user) + `~/.poe/workspace/config.yml` (workspace). Inspector thresholds and constraint settings wired to config. 17 tests.
- [x] **Route output + projects to workspace** — (2026-04-11 session 16) `output_root()` and `projects_root()` now route to `~/.poe/workspace/` via config.py. `relative_display_path()` helper for safe cross-root path display. 12 `relative_to(orch_root())` calls fixed.
- [x] **poe-export / poe-import for learning data** — (2026-04-11 session 16) `scripts/poe_export.py`: export/import of `~/.poe/workspace/` as tar.gz. Excludes secrets, prototypes, ephemeral state. 12MB→910KB compressed. Merge-restore with path traversal protection. 13 tests.

### Concurrent Run Safety (hardening)
- [x] **First-class project isolation** — DONE (session 27). `Skill.project` field (""=global, non-empty=project-scoped). `find_matching_skills(project=...)` filters to global + project-specific skills. `set_loop_running(project=...)` writes per-project lock file. `get_running_project_loop()` + `is_project_running()` for concurrent-run safety checks. 11 tests. Remaining: project-scoped lesson injection (currently filters by task_type but not project) and captain's log project tagging — deferred until parallel missions actually needed.

### Captain's Log extensions (from Grok Round 5 feedback, 2026-04-10)
- [x] **Input classification tag** — DONE (session 23). `classify_input_type()` in captains_log.py (url/code/structured_data/plain_text). `INPUT_MISMATCH` + `METACOGNITIVE_DECISION` event constants. `update_skill_utility()` logs INPUT_MISMATCH when circuit opens on url-skill-vs-non-url-input domain mismatch. `attribute_failure_to_skills()` threads step_text through. 9 tests. EVENT_TYPES 28→30.
- [x] **Director context hook** — (2026-04-11 session 16) Captain's log context + playbook + knowledge nodes now injected into `_build_loop_context()`. Director sees recent learning events, operational wisdom, and relevant knowledge at decompose time.
- [x] **Dashboard captain's log panel** — DONE (session 27). `_read_captain_log_entries(limit=20)` in observe.py reads captains_log.jsonl newest-first. Wired into `_snapshot_json()` and `_DASHBOARD_HTML`. Badge color-coding by event type. 6 tests in TestCaptainLogDashboard.

### From X research runs (2026-04-09)

Six X posts researched via live Poe missions. Actionable items extracted:

- [x] **markitdown installed** — `pip install --user markitdown` done (Python 3.14). HTML→MD confirmed working. High-value use case: PDF/Word/Excel ingestion (Jina can't handle these). Wiring into `web_fetch.py` or `file_ingest.py` is next step — needs `fetch_file(path_or_url)` that falls back to markitdown for non-HTML content types.
- [x] **Auto-detect repo stack → skill discovery + summarization** — DONE (session 25). `src/repo_scan.py`: 50+ file indicators, deep-scan requirements.txt/package.json for frameworks, detect Docker/CI/DB. `format_repo_context()` injects compact stack summary into `_build_loop_context()`. Wired via project slug heuristic (~/claude/{project}/) + `--repo` CLI flag. 53 tests. Source: @ihtesham2005

### Infrastructure
- [x] **Phase 42 nightly eval** — wire eval suite to evolver on a schedule. Done: `run_nightly_eval()` in eval.py; fires via `eval_every=1440` in heartbeat_loop(); failures → evolver Suggestion entries. (2026-03-31)
- [x] **Heartbeat backgrounding** — evolver, inspector, nightly eval each moved to daemon threads with double-checked locking flags; heartbeat tick no longer blocks on slow runs. 11 tests. (2026-04-04)
- [x] **Heartbeat service deployment** — poe-heartbeat.service and poe-telegram.service installed as systemd units, enabled + started. Fixed UnboundLocalError: `global` declarations missing for all 6 bg-thread flags in `heartbeat_loop`; without them Python treated writes as local → crash on tick 1 every 30s → ~0% duty cycle. (2026-04-04)
- [x] **Context firewall (depth-gated)** — `_context_firewall()` in handle.py: depth ≥ 2 strips accumulated history, keeps only original goal + remaining steps. Wired into continuation task handling. 5 tests. (2026-04-04)
- [x] **Mutable task graph (inject_steps)** — `complete_step` tool accepts `inject_steps` (max 3); serial and parallel agent_loop prepend injected steps to remaining_steps mid-execution. 2 tests. (2026-04-04)
- [x] **SlowUpdateScheduler** — `src/slow_update_scheduler.py`: 4-state machine (IDLE_WAIT→WINDOW_OPEN→UPDATING→PAUSING) gates heavy background LLM work to idle windows. Thread-safe with `start_work()`/`finish_work()` context manager; wired into `heartbeat_loop()` before evolver/inspector/eval dispatch. 16 tests. (MetaClaw steal, 2026-04-04). Follow-on done: scheduler state exposed in `poe-doctor` health check (snapshot()-based) and `poe-observe` dashboard (state badge + workers/cooldown/idle_since panel). 60 tests pass. (2026-04-04)
- [x] **Auto-resume on rate limits** — multi-cycle polling retry in `ClaudeSubprocessAdapter`: 6 retries, exponential backoff 60→1800s, stops early on non-rate-limit errors. 5 tests. (2026-04-04)
- [x] **Cron persistence** — scheduled missions survive restarts. `jobs.json` pattern. Done: `src/scheduler.py` with `JobStore` backed by `memory/jobs.json`; supports once/daily/interval schedules; `drain_due_jobs()` wired into `heartbeat_loop()`; `poe-schedule` CLI. 21 tests. (724-office steal, 2026-03-31)
- [x] **ScheduleCronTool in Poe heartbeat** — wire Poe's cron tool so she can schedule her own future runs from within a mission. Closes the self-managing loop. Done: `schedule_run` tool added to `EXECUTE_TOOLS` in step_exec.py; parses 'daily at HH:MM' / 'in N minutes/hours/days' / ISO datetime; calls scheduler.add_job(); 13 tests. (2026-03-31)

### claw-code steal list (github.com/instructkr/claw-code — Claude Code architecture map)
- [x] **verificationAgent as first-class agent** — `src/verification_agent.py` with `VerificationAgent` class: `verify_step()`, `adversarial_pass()`, `quality_review()`. step_exec.py's `verify_step` delegates to it. `poe-verify` CLI. 21 tests. (2026-03-31)
- [x] **TeamCreateTool pattern** — `src/team.py`: `create_team_worker(role, task)` spins up a specialist with a custom persona. 8 known roles (market-analyst, risk-auditor, fact-checker, data-extractor, devil-advocate, synthesizer, strategist, domain-skeptic); free-form roles get generic persona. `create_team_worker` tool added to `EXECUTE_TOOLS_WORKER` (not SHORT/INSPECTOR). Step-terminating: agent delegates step to specialist, synthesizes in next step. 30 tests. (2026-03-31)
- [x] **thinkback replay** — session-level decision replay for self-improvement. `src/thinkback.py`: `ThinkbackReport` with per-step StepReview (good/acceptable/poor), mission_efficiency, key_lessons, would_retry, retry_strategy. `run_thinkback(loop_result)` + `run_thinkback_from_outcome(outcome_dict)`. Optionally writes lessons back to memory tagged `[thinkback:{run_id}]`. `poe-thinkback --latest [--save]` CLI. 31 tests. (Phase 50, 2026-03-31)
- [x] **effort modifier** — add `effort:` keyword to handle.py routing that sets a thinking/token budget level. Done: `effort:low/mid/high` prefix in handle.py strips keyword and overrides model tier (low→cheap, mid→mid, high→power). (claw-code steal, 2026-03-31)
- [x] **passes command** — multi-pass review as a unified first-class concept. `src/passes.py`: `PassConfig` with presets (quick/standard/thorough/full/all), `run_passes()` chains quality_gate → adversarial → council → debate → thinkback. `PassReport` aggregates all pass verdicts into one escalation signal. `poe-passes --goal "..." --passes council,debate` CLI. 29 tests. (Phase 51, 2026-03-31)
- [x] **ultraplan / ultrareview modes** — `ultraplan:` prefix added to handle.py: strips keyword, sets model=power, passes max_steps=12 to run_agent_loop. For complex multi-part goals needing thorough decomposition. 3 tests. `ultrareview:` deferred — quality gate already covers the review use case. (2026-03-31)
- [x] **bughunter mode** — self-directed code quality scan. Poe scanning her own orchestration code for bugs, not just diagnosing runtime failures. Done: `src/bughunter.py` with stdlib AST scanner (BH001 bare except, BH003 mutable defaults, BH004 shadowed builtins, BH010 TODOs); `poe-bughunter` CLI. 16 tests. Src scans clean. (claw-code steal, 2026-03-31)
- [x] **btw (by-the-way) mode** — non-blocking observation mode; `btw:` prefix routes to NOW lane with `_BTW_SYSTEM` prompt, tags result as `[Observation]`. 5 tests. (2026-03-31)

### X Links steal list (2026-04-01 research batch — research/X_LINKS_SYNTHESIS.md)

- [x] **lat.md — Knowledge graph docs** (9/10) — DONE (2026-04-01). 9 cross-linked concept nodes in `lat.md/`, `[[wiki links]]`, `lat check` CI clean. Phase 55.
- [x] **Promotion cycle + decision journal** (8/10) — DONE (2026-04-01). `observe_pattern()` → hypothesis → StandingRule at 2 confirmations. `contradict_pattern()` demotes. `inject_standing_rules()` + `inject_decisions()` wired into every decompose call. Phase 56.
- [x] **Polymarket BTC lag edge validation** (6/10) — Research complete (2026-04-02). **Verdict: UNCONFIRMED — promotional fiction.** Structural failures: (1) Wrong product type — Polymarket BTC contracts are binary YES/NO (prob markets), not continuous price feeds; no "lag" surface exists. (2) Fee economics — even at corrected ~4% round-trip fee, the 0.3% claimed edge is 13x smaller than fees. (3) Near-zero liquidity — no resting orders to fill against. (4) Resolution mismatch — single Binance 12:00 ET candle close; intraday moves irrelevant. Full report: `research/POLYMARKET_BTC_LAG_VALIDATION.md`. No further investigation warranted unless claim is restated for a different venue (perpetual futures, spot CEX).
- [x] **Claude Code declarative skill/hook architecture** (5/10) — IMPLEMENTED (2026-04-02, steps 1-6). `tool_registry.py`, `skill_loader.py`, `step_events.py`, `tool_search.py` all shipped. 139 new tests. Step 7 (MCP) remains. Design doc: `research/PHASE41_TOOL_REGISTRY_DESIGN.md`.
- [x] **Magic keyword triggers** — `ralph:`, `verify:`, `pipeline:`, `strict:` prefixes in handle.py. DONE 2026-04-02. 8 tests.
- [x] **Magic prefix registry** — `_PrefixRule` dataclass + `_PREFIX_REGISTRY` + `_apply_prefixes()` replaces 9 scattered `startswith()` chains. Stacking, case-insensitive, model tier precedence. 11 tests. (2026-04-04)
- [x] **Hermes steal: Skill Document auto-extraction** — `export_skill_as_markdown()` in skill_loader.py; called from `maybe_auto_promote_skills()`. DONE 2026-04-02. 18 tests.
- [x] **poe-doctor Phase 41 checks** — tool registry, curated skills, step event bus, bughunter. DONE 2026-04-02. 10 tests.

### Links fetched but not fully digested
- [x] **TradingAgents** (github.com/TauricResearch/TradingAgents) — multi-agent Polymarket trading. Dogfood run complete. Steal items in STEAL_LIST.md: commitment-forced verdicts (done), pre-plan challenger, two-tier model routing.
- [x] **Stanford Agent0** — self-improvement without supervision. Dogfood run complete. Results in projects/agent0-research/. Key: problem generation + self-evaluation loop. Maps to evolver.
- [x] **LLM sycophancy** (rohanpaul/karpathy) — models mirror prompts not truth. Addressed: adversarial verification step now auto-injects for research goals.
- [x] **FunSearch/EUREKA/Voyager papers** (garybasin) — Research complete (2026-04-05). 7 shared primitives extracted. Critical gap: generator/evaluator separation (evolver.py mixes both). Design sketch written. See `docs/research/funsearch-agent-design.md`. Steal candidates: island model diversity, replay-based fitness oracle, score-weighted mutation context, brevity penalty in skill scoring.
- [x] **claw-code** (github.com/instructkr/claw-code) — Python skeleton of Claude Code's leaked TS source. Most code is stubs but the tool/command inventory is a goldmine. Key findings: verificationAgent is a first-class built-in; TeamCreateTool exists; thinkback/replay is a real pattern; $ralph mode (OmX) validated our Ralph verify loop. Steal list added above. (2026-03-31)
- [x] **vtrivedy10 tweet** (x.com/vtrivedy10/status/2038346865775874285) — Viv @Vtrivedy10 (LangChain agents/evals) on "harnesses" for autonomous agents. Key findings from related @systematicls article: (1) Instruction fade-out is real — agents cut corners as context accumulates, event-driven reminders at decision points (not just system prompt) fix this. (2) Verification is the highest-leverage investment — success correlates with ability to verify own work. (3) Multi-layer defense: prompt + schema + runtime gates + tool validation + lifecycle hooks. (4) Dual-memory: episodic (events.jsonl) + working (completed_context) — we have both. These validate Mode 2 scaffolding direction. ~~Steal candidate: inject contextual guidance at step retry/budget-exceeded decision points (not just in initial system prompt).~~ **DONE: agent_loop.py now re-injects goal+constraints every 5 steps and on every retry. (2026-03-31)** New steal items added to STEAL_LIST.md LATER: role-specific tool visibility, back-pressure lifecycle hooks, subagent context firewall.

### Persona System
- [x] **garrytan persona** — GStack phase-gated persona (THINK→PLAN→BUILD→REVIEW→TEST→SHIP→REFLECT), six forcing questions, CRITICAL/MODERATE/MINOR severity, founder taste layer, anti-sycophancy guardrails. `garrytan:` prefix or keyword-detected. (2026-04-04)
- [x] **Persona injection in AGENDA path** — personas now active for all AGENDA goals (was only CEO meta-commands). `forced_persona` field on `_PrefixResult` / `_PrefixRule`; `ancestry_context_extra` populated before `run_agent_loop`. (2026-04-04)
- [x] **Dynamic persona discovery** — persona system is now auto-discoverable. `scan_personas_dir()` loads all `personas/*.yaml` at import time; `persona_for_goal()` keyword-matches against loaded specs with confidence threshold fallback; `create_freeform_persona()` writes a minimal YAML spec (`personas/<slug>.yaml`) and registers it in module cache when no existing persona matches well. Free-form path: goal → kebab slug (first 5 words) → mid-tier spec with goal-derived system prompt → session scope. 140 persona tests passing, 0 failures. (2026-04-04)

### Grok feedback sessions
- [x] grok-response-2.txt — oh-my-claudecode, 724-office, Mimir steal list. Processed, items in STEAL_LIST.md.
- [x] grok-response-3.txt — Miessler Bitter Lesson Engineering + Zakin Mode 1/2/3 taxonomy. Processed (session 25). Key steal items implemented:
  - BLE goal rewriter: `rewrite_imperative_goal()` in intent.py. 15 tests.
  - SIGNALS.md → signal alignment: `_load_user_signals()` in evolver.py. 5 tests.
  Deferred items: USER/ folder formalization (CONFIG/GOALS/SIGNALS already exist), replay factory mode toggle in dashboard (dashboard is still basic).

### Steal-list items from Miessler/Zakin (grok-response-3.txt)
- [x] **BLE goal rewriter** — DONE (session 25). `rewrite_imperative_goal()` strips imperative steps, rewrites as outcome-focused. Wired into AGENDA path before clarity check. Non-blocking.
- [x] **SIGNALS.md signal alignment** — DONE (session 25). User-declared research priorities injected into signal scanning. Factory sub-missions now aligned with user intent.
- [x] grok-response-3.txt — Bitter Lesson Engineering + Mode 1/2/3 taxonomy. Processed, implemented outcome-first decomposition + user context.
- [x] **PAI (danielmiessler/Personal_AI_Infrastructure)** — Research run complete (2026-03-31, partial — subprocess timeout on step 6). Key findings: 964 TELOS files across 5 categories (world/self/goals/projects/standards), 340 hooks files, rich hook pattern library. Steal candidates: TELOS-style structured context injection; hook-based lifecycle callbacks at decision points. Jeremy's gut: good bones, too much ceremony for Poe's use case.
- [x] **Hermes (NousResearch/hermes-agent)** — Jeremy asked if we should set up Hermes instead of OpenClaw. Research complete (2026-03-31). Verdict: **keep OpenClaw + poe-orchestration**. Hermes is optimized for repeated iterative tasks with automatic skill refinement; our system is more sophisticated in multi-agent oversight, recovery, and mission structure. Selective steal candidates below.
  - **Hermes steal: Skill Document auto-extraction** — formalize lessons.jsonl into SKILL.md files that get FTS-searched automatically (vs. manual lesson injection). Maps to Phase 32 skill synthesis.
  - **Hermes steal: Persistent user modeling** — Honcho-style user preference tracking across sessions. Jeremy-specific knowledge compounding over time. Partial overlap with Phase 28 companion persona.
  - **Hermes steal: Terminal persistence backends** — SSH/Modal backends for long-lived sandboxed execution separate from the primary process. Complements Phase 18 sandbox hardening.

## Self-Review Quality (from 2026-04-06 haiku adversarial run — vetted)

Findings from the haiku blind run ($7.87, 11 steps, adaptive tiering). Hallucinations discarded; only verified findings listed.

- [x] **CRITICAL: Evolver dry-run gate bug** — `_run_skill_test_gate` passed `adapter=None` to `validate_skill_mutation`, causing `dry_run=True` → `blocked=False` always. Gate never blocked any mutation. **Fixed 2026-04-06**: gate now builds a cheap adapter; heuristic fallback only if adapter unavailable.
- [x] **Skill backup before mutation** — `_apply_suggestion_action` wrote to `skills.jsonl` with no backup. Bad mutations had no automated rollback. **Fixed 2026-04-06**: `skills.jsonl.bak` written before any skill_pattern mutation.
- [x] **Memory decay scores not persisted** — `run_decay_cycle` computed decayed scores in memory, then reloaded from disk for the rewrite, losing all score changes. Middle-ground decay (above GC, below promote threshold) was silently lost on restart. **Fixed 2026-04-06**: rewrite uses in-memory lesson list with updated scores.
- [x] **No real LLM coverage in tests** — Added `tests/integration/test_integration.py` (23 mocked-LLM integration scenarios) and `tests/regression/test_regression.py` (7 golden-path scenarios). Both trace handle() end-to-end with ScriptedAdapter. Live Haiku integration still TODO (infrastructure cost). (2026-04-06)
- [x] **No coverage measurement** — `pytest-cov` installed, `.coveragerc` configured, `dev` extras updated in pyproject.toml. Run with `python3 -m pytest --cov=src tests/`. (2026-04-06)
- [x] **Memory decay persistence across restarts** — Non-issue (investigated 2026-04-06): `record_tiered_lesson` → `_append_tiered_lesson` persists immediately; `reinforce_lesson` → `_rewrite_tiered_lessons` also persists immediately. Decay is recomputed from `last_reinforced` date on every `load_tiered_lessons` call (inline, line ~1272), so no decay is lost across restarts. Scores used for injection are always correct. Only cosmetic gap: inline-computed decay scores aren't written back unless `run_decay_cycle` runs (fixed in prior session for that path). No action needed.
- [x] **Skill rollback CLI** — `poe-skills --rollback <skill_name>` restores `skills.jsonl` from `.bak` backup. `--dry-run` supported. (2026-04-06)

## Self-Review Quality (from 2026-04-07 Sonnet seeded run)

Findings from Sonnet seeded run (full code read). Vetted; hallucinations discarded.

- [x] **Director review exhaustion silent** — after MAX_REVIEW_ROUNDS, director fell through silently. **Fixed 2026-04-07**: added WARNING log + `for-else` branch in review loop; 2 tests added. (11c05c3)
- [x] **WorkerResult schema validation** — director.py now spot-checks `result.worker_type` matches `ticket.worker_type` and `result.ticket` is non-empty after each `dispatch_worker` call. Logs WARNING on mismatch. (2026-04-07)
- [x] **Prefix combination validation** — added log.warning in `_apply_prefixes` when conflicting model tiers detected (e.g. effort:high + effort:low). (2026-04-07)
- [x] **Lesson staleness detection** — `load_tiered_lessons()` now accepts `max_age_days` parameter; lessons older than N days skipped at load time. 2 tests. (2026-04-07)
- [x] **Introspection lens determinism** — `run_lenses(deterministic=True)` uses `temperature=0` for LLM-based lenses. `LensRegistry.run_all()` uses `inspect.signature` to pass kwarg only to supporting lenses. `_quality_lens()` accepts `deterministic` kwarg. (2026-04-06)
- [x] **LLM schema hallucination crash** — when Haiku returned a JSON schema dict instead of string for `summary` field, `step_summary[:200]` raised `KeyError: slice(None,200,None)`. **Fixed 2026-04-07**: coerce summary to str in `step_exec.py` + defensive guard in `agent_loop.py`. (df8375b)

## Self-Review Quality (from 2026-04-06 blind adversarial run)

Real findings from the run — hallucinations already vetted and discarded:

- [x] **Evolver audit trail** — `evolver.py` appends to `memory/change_log.jsonl` before any suggestion mutation. `memory.py` logs decay cycle (promoted_ids + gc_ids) before rewriting lesson store. Creates rollback surface without requiring git tracking of runtime files. (2026-04-06)
- [x] **No end-to-end integration test** — `tests/integration/test_integration.py` added: 23 mocked-LLM scenarios covering both lanes, magic keywords, constraint enforcement. `tests/regression/test_regression.py` added: 7 golden-path scenarios. (2026-04-06)
- [x] **`tests/regression/` has spec but no tests** — `tests/regression/test_regression.py` implements 7 golden-path scenarios (NOW, AGENDA, direct:, btw:, pipeline:, stuck, prefix stacking). (2026-04-06)
- [x] **Phase 24 (Slack)** — `src/slack_listener.py` (424L) + `tests/test_slack_listener.py` (25 tests). Socket Mode, slash commands, interrupt routing. Item was stale — already done. (verified session 29)
- [x] **`lat.md` knowledge graph — wired into director.py (2026-04-06)** — `lat_inject.py` with TF-IDF `inject_relevant_nodes()` now wired into `_produce_spec()` in director.py (same pattern as planner.py). Silently skips if no relevant nodes match.
- [x] **Adversarial review hallucination rate too high** — FULLY DONE (session 29). `claim_verifier.py` extended with Python symbol (function/class/method) existence checking: `extract_symbol_claims()`, `_build_symbol_index()` (direct .py scan, no grep subprocess), `verify_symbol_claims()`, `verify_all_claims()`, `SymbolReport`, `CompoundClaimReport`. `annotate_result()` surfaces `SYMBOL_CLAIMS_NOT_FOUND`. 24 new tests (61 total). All three hallucination-detection vectors now covered: file paths, symbols, and decompose prompt hardening.

### From link-farm (2026-04-09–11 batch)


- [x] **Claude Skills quality gate for synthesize_skill** — DONE (session 34, 2026-04-16). Duplicate of the "synthesize_skill() 3-gate pre-promotion check" item below — same source (@av1dlive), recast in the session 30 research run with more specific gates. Shipped as trigger-precision + output-schema + edge-case-coverage gates in `evolver.synthesize_skill()`. Source: @av1dlive.


### From 18-link research runs (2026-04-14, session 30)

Full reports: `docs/research/ai-agent-memory-synthesis.md`, `docs/research/ai-agent-memory-steal-list.md`, `docs/research/x-posts-steal-list-20260414.md`

- [x] **Proactive memory injection at loop entry** — Engramme (@svpino): memories surface automatically without explicit query. Already DONE (pre-existing, misidentified in research synthesis). `_build_loop_context()` at `agent_loop.py:2607` performs ranked top-N injection at every goal start across: tiered lessons (`load_lessons`), standing rules (`inject_standing_rules`), decision journal (`inject_decisions`), graveyard lessons (`search_graveyard`), failure notes (`find_relevant_failure_notes`), captain's log actionable events, playbook (`inject_playbook`), **knowledge nodes (`knowledge_web.inject_knowledge_for_goal` — TF-IDF ranked, top-5 nodes, `max_chars=1200`)**, matching skills, and curated skill summaries. The research synthesis named `knowledge_lens.rank()` but the equivalent live capability is `knowledge_web.query_knowledge` + `inject_knowledge_for_goal`. Nothing further required. Source: @svpino/Engramme (confirmed DONE 2026-04-16, session 34).

- [x] **synthesize_skill() 3-gate pre-promotion check** — DONE (session 34, 2026-04-16). Three gates in `evolver.synthesize_skill()` before persistence: (1) trigger precision — `_TRIGGER_PRECISION_MAX_HITS=3` against fixed 10-goal off-target corpus, plus `_TRIGGER_MIN_LEN=4`; (2) output schema — requires non-empty `expected_outputs` list in LLM response; (3) edge case coverage — requires ≥3 distinct `edge_cases`. `_SYNTHESIZE_SYSTEM` prompt updated to request both fields with examples. 15 new tests. Source: @av1dlive/@eng_khairallah1.


### Grok Round 4 feedback (2026-04-07)
- [x] **`poe evolver apply` CLI** — `poe-evolver list|apply|run` subcommands. `apply` supports interactive/--all/--dry-run/by-id modes. Registered as `poe-evolver` entry point. (2026-04-07)
- [x] **`estimate_goal_scope` debug CLI** — `poe-preflight-stats --scope-check "goal"` prints scope + effect string. Registered as `poe-preflight-stats` entry point. (2026-04-07)
- [x] **RAG query API for workers** — `query_lessons(query, n=3, task_type, tiers)` in memory.py. Uses hybrid BM25+RRF (falls back to TF-IDF). Returns List[TieredLesson]. Workers can call this to pull relevant past lessons without full injection. (2026-04-07)
- [x] **Replay mode for A/B testing** — `poe-replay` CLI in strategy_evaluator.py. Supports `--compare` (fitness delta with/without lessons) and `--outcome-id` (load past outcome by id). 5 tests. (2026-04-07)
- [x] **NVIDIA NeMo DataDesigner** — (goodhunt tweet, 95K views) Research complete (2026-04-07). 7 steal items identified: (1) discriminated union config for skills, (2) processor pipeline for skill generation, (3) Jinja2 dependency injection in personas, (4) ViolationType enum config, (5) AIMD throttling for workers, (6) skill usage telemetry, (7) sampler constraints for skill A/B testing. Full report: `output/x-research-20260407T063015Z.md`. Est. 1-2 weeks to implement Phase 57.
- [x] **Feynman research agent** — Research complete (2026-04-07). 6 steal items identified: (8) task ledger + verification log, (9) evidence table + claim tracing, (10) multi-round loop with gap analysis, (11) verifier agent (inline citation), (12) reviewer agent with severity levels, (13) provenance records for skills. Full report: `output/x-research-20260407T063015Z.md`. Est. 2-3 weeks to implement Phase 58.
- [x] **Claude Code / OpenClaw / Hermes misconception thread** — (exm7777 tweet) Good framing: these are general-purpose agents not just coding tools. Example: academic research skills for Claude Code (literature review, etc.). Confirms the direction; no new steal items.

## Test Ideas

- [x] **Nootropic with verification** — DONE (2026-04-05). 6/6 steps, 679k tokens, ~11min. `verify:` prefix activated cross-reference pass. Key downgrades from verification: Alpha-GPC evidence weak in healthy adults (only 4 RCTs in MCI/Alzheimer's); Lion's Mane neurogenesis claims are preclinical only; Bacopa "25 studies" corrected to ~12 RCTs. Results: `docs/research/nootropic-stack-verified.md`.
- [x] **Cross-domain transfer** — DONE (2026-04-05). Smart home automation goal: 6/6 steps, 191k tokens, 231s. Full protocol comparison (Zigbee/Z-Wave/WiFi), rollout order, hub scoring (HA 63/70 > Hubitat 51 > SmartThings 50), cost tiers ($625/$1,049/$1,815). Generalization confirmed — system handled a completely new domain without customization.

## Completed (archive)

Items moved here when done, for reference:

- [x] FileTaskStore port (`task_store.py`) — 2026-03-29
- [x] Phase 44 (Self-Reflection) — 2026-03-29
- [x] Phase 45 (Recovery Planner) — 2026-03-29
- [x] Mission resilience (partial milestone status) — 2026-03-29
- [x] 14 e2e smoke tests — 2026-03-29
- [x] Concise step prompting — 2026-03-29
- [x] Data pipeline strategy (prompt) — 2026-03-30
- [x] Outcome-first decomposition (Bitter Lesson) — 2026-03-30
- [x] User context injection (user/ folder) — 2026-03-30
- [x] Agent-generated tools (backtester) — 2026-03-30

From jeremy (clean up and integrate with the above later)

---

## Archived from BACKLOG 2026-06-24 (bulk triage)

### Per-step worker token explosion — DIAGNOSED 2026-06-21 (was [NEXT])

Live finding from a `verify:` coding run: 485K tokens over 6 steps (47K→111K→**145K**
→80K→17K→84K per step); introspect flagged `token_explosion`.

**Measured the code (2026-06-21). Conclusion: the original framing was wrong, and so
was the metric.**
1. **Every inter-step seam is already hard-capped** — completed_context 800 chars +
   compress-after-5 (`step_exec.py:660`, `agent_loop.py:1487/1527`), artifact→prompt
   `str(_v)[:500]` (`step_exec.py:676`), env snapshot 200 chars (`agent_loop.py:1497`),
   team firewall 5×200 (`team.py:208`). The step's own LLM call is `max_tokens=4096`,
   single tool (`step_exec.py:833`). So our plumbing physically cannot emit a 145K step.
2. **The big number is the delegated subprocess worker** (the `claude` CLI path,
   `_extract_result_object` in `llm.py`) rolled into the step total. It runs its own
   internal agentic tool loop (Read/Edit/Write on the growing file); we see only totals.
   Non-monotonic per-step shape (rise to 145K, fall to 17K) confirms intrinsic per-step
   work, NOT monotonic inherited-context accumulation.
3. **The metric was cache-blind.** `llm.py` folded `cache_read_input_tokens` into
   `input_tokens` at full weight, and `token_explosion` fires on raw token *volume*
   (`introspect.py:42,63`). A worker re-reading a growing file is mostly cache HITS
   (~0.1x cost; Claude Code reports ~92% hit rate) — so the "explosion" likely overstates
   real $ by ~10x. We were arguing about a number that was lying.

**DONE (foundation, commit pending):** cache-aware accounting — `LLMResponse.cache_read_tokens`
+ `.fresh_input_tokens`, all 3 adapters populate it on the same total-volume contract,
`estimate_cost(..., cache_read_tokens=)` prices cache reads at `CACHE_READ_MULTIPLIER` (0.1x).

Conclusion on the original levers: the "summary instead of file" / "cap `_art_val`" levers
target the orchestration layer, which is NOT the leak — **don't build them.** The durable
lever for the actual leak is worker-layer caching (CAG: static prefix cached, pay the delta
only — likely already half-free via Anthropic prompt cache) IF cost is genuinely large.
Decide that with the now-correct meter, not the old volume number.

### Make metric alarms cache-aware — DONE 2026-06-21

Wired `cache_read_tokens` end-to-end and re-measured. **Verdict: the token explosion was
a cache-blind metric artifact; caching already absorbs the re-reads. No CAG/retrieval build
needed for this.**
- [x] `cache_read_tokens` carried through the step record: `step_exec` outcome (all 9
  resp-based sites) → `write_event`/`observe` → `events.jsonl` → `StepProfile.cache_read_tokens`
  + `.fresh_tokens`.
- [x] `token_explosion` (`introspect.py`) now compares `fresh_tokens`, not raw volume.
- [x] **Consistency pass (2026-06-21):** converted the remaining raw-volume alarms to the
  same cache-aware basis and closed the false-negative gap the user flagged ("are we skewing
  the other direction now?"):
  - `decomposition_too_broad` now gates on `fresh_tokens` (a 250K step that's all cache reads
    no longer flags as over-broad).
  - New `cost_spike` failure class (`introspect.py` check 5b): an **absolute** cache-aware
    dollar guard (`_STEP_COST_WARN_USD=0.50`, `_LOOP_COST_WARN_USD=2.00`). Catches the inverse
    case the fresh-token alarms miss — a huge cached prefix on a *pricey* model that's flat in
    fresh terms but still real money at 0.1x. Priced cache-aware, so cheap-tier cache reads
    never trip it. Registered in `FAILURE_CLASSES`, `_GRADUATION_TEMPLATES`, and `RECOVERY_PLANS`.
  - `StepProfile` carries `tokens_in`/`tokens_out`/`model` + a `cost_usd` property;
    `model` (model_key) now flows step_exec → `write_event`/`observe` → `events.jsonl`.
  - `_cost_lens` ranks steps by cache-aware dollars, not raw tokens.
  - The crypto-tax framing: fresh = ordinary income (full rate), cache reads = the like-kind
    0.1x basis. We now tax **net** on growth/bloat and keep a separate **absolute** spend alarm
    so neither direction goes blind.
- [x] Re-measured live (sandboxed file-accumulating build, cheap/Haiku, 4 steps re-reading a
  growing file). **Result: input was ~100% cache reads** (per-step 42K→69K→69K→96K total,
  fresh_in 4–6 tokens each; whole run 276,894 input / 276,874 cache / **20 fresh**). The same
  pattern that historically flagged `token_explosion` now diagnoses **healthy**; cache-blind
  cost was **6.6x overstated** ($0.235 → $0.036). The 485K run's "growth" was cache-read
  growth at ~0.1x, not fresh compute.
- [x] **DONE 2026-06-22:** Passed `cache_read_tokens` into `estimate_cost` at the live recording
  sites — `record_step_cost` (now takes + persists `cache_read_tokens` to step-costs.jsonl),
  skill `_est_cost` (success + fail paths), and the per-step/running cost log. Persisted cost
  telemetry is now cache-adjusted, matching what the introspect alarms judge. **Also fixed a
  bug found while here:** the running `cost_total` log/budget-breaker repriced *all* accumulated
  tokens at the latest step's model, so the figure swung when steps switched cheap↔mid↔power
  (seen live: $0.63 at a mid step → $0.26 at the next cheap step). Now accumulated per-step
  (`total_cost_usd`), correct across model switches and more accurate for the cost-budget circuit
  breaker. `StepOutcome` carries `cache_read_tokens` so the run-summary ✅ cost string is
  cache-aware too. Note: the claude-CLI subprocess reports ~all input as cache_read once the
  session warms, so fresh-token alarms are now very quiet for subprocess workers — confirmed
  live (run1 2026-06-22: per-step cache_read=83979 of in=83984, ~99.99%). Watch we don't go
  cache-blind the other way; the absolute `cost_spike` alarm is the backstop for that.
- **Live validation 2026-06-22 (run1, real spend):** a 9-step `verify:` build goal did
  **457,322 total tokens** and diagnosed **`healthy`** — pre-fix that volume would have tripped
  `token_explosion`/`decomposition_too_broad`; now correctly healthy because fresh tokens/step
  are ~5 (rest cache reads). The cache-aware introspect works on organic data.

### Entropy / decay-by-invalidation (2026-06-11, queued behind navigator)

Steering context in GOAL_BRAIN.md Intent (entropy quote). Crystallized artifacts
(skills, standing rules, playbook entries) rot when the world changes under them —
distinct from decay-by-disuse, which tiered lessons already have. The most-reinforced
artifact is the most dangerous one at world-shift time, because reinforcement and
validity are different signals and we only track one.

- [x] **Decay v0 — re-fight on collision (Jeremy's pinned first pass).** When a
  crystallized artifact fails, inject the existing mechanism + the failure into the
  prompt and re-derive. *"at worst we have better context, at best it's a slight
  tweak and we fix forward."* **Done 2026-06-11 for the rule layer:** a contradicted
  standing rule is *contested* — immediately demoted from "apply unconditionally" to
  a verify-before-relying injection block (read-time trust derivation, data untouched),
  and `knowledge_lens.refight_rule()` re-derives it against its contradiction evidence
  (pulled from the captain's log) with verdicts keep / revise / retire (retire demotes
  back to hypothesis — must re-earn promotion). Runs from `run_skill_maintenance` in
  the evolver cycle (adapter-gated, max 3/cycle), beside `rewrite_skill` — the skill
  seed it generalizes. `RULE_REFOUGHT` event is the audit trail. No cron — collision
  detection rides on contradiction recording, repair rides on the evolver cycle.
  Note: no standing rules exist on this box yet (accretion only became possible in M2),
  so first live exercise awaits a real rule + collision.
- [x] **Freshness signal on crystallized artifacts.** `last_verified` (last
  successful run against the real world) distinct from `last_reinforced`. Trust at
  injection time = f(score, time-since-verified); stale-but-promoted gets a
  "verify before relying" flag, not silent confident injection.
  **Done 2026-06-11 for the rule layer:** `StandingRule.last_verified` stamped at
  promotion, on production re-confirmation, and on re-fight keep/revise. The
  anchoring fix: post-promotion re-confirmations never reached the rule —
  `observe_pattern` only matched hypotheses, so a re-confirmed promoted lesson
  seeded a *duplicate hypothesis* (which could re-promote into a duplicate rule)
  while `rule.confirmations` stayed frozen at its promotion value. Now an
  observation matching an existing rule verifies the rule (`RULE_VERIFIED`
  event, 46th type). At injection, an uncontradicted rule unverified for
  `knowledge.rule_staleness_days` (default 30, 0 disables) joins a "Stale rules
  (unverified for N+ days — verify before relying)" block; contested takes
  precedence. Read-time derivation only, data untouched; `promoted_at` is the
  fallback anchor for pre-field rules. Skill/playbook layers still open —
  skills have score+circuit-breaker already; revisit if staleness shows up there.

### Live orchestration run findings (2026-06-11, first post-suite-green session) — governance push

From real task-path runs (enqueue → drain_task_store → handle_task → handle).
Fixed same day: task-path runs never finalized, poisoning recall's all_failing
(9402d3d); lesson extraction silently returned [] on every real run — safe_list's
str default dropped the typed lesson dicts the prompt asks for (verify→learn was
dead at the extraction step since Phase 59 S1). Remaining observations:

- [x] **GOVERNANCE: a vague goal pipeline-executed into an unreviewed mainline
  push as Jeremy.** "improve things" (deliberately vague test goal) decomposed
  itself into "pick an improvement from MILESTONES/BACKLOG and implement it
  end-to-end", wrote a real fix (CLOSURE_VERDICT skip-path emission, 06c3764,
  reviewed post-hoc: good code, kept), committed as author "Jeremy Stone" and
  **pushed to origin/main** — 4.09M tokens, no human or quality gate between a
  worker and a public push under Jeremy's identity. The live navigator shadow
  said **escalate (0.95)** at dispatch — the pipeline executed anyway and
  declared done. **RESOLVED 2026-06-11 (cfab080):** Jeremy's call — workers
  authoring as him is fine ("haven't made that distinction yet, not sure it
  matters"); the gate is about unreviewed mainline pushes, not identity.
  Shipped as branch policy: `_run_subprocess_safe` marks all Poe-spawned
  subprocesses `POE_WORKER_RUN=1`; `scripts/hooks/pre-push` (installed via
  `scripts/install-git-hooks.sh`, part of harness install) blocks worker
  pushes to main/master with a redirect to work branches; explicit bypass
  via config `workers.allow_main_push` (default false) → `POE_ALLOW_MAIN_PUSH=1`.
  Humans/interactive sessions unaffected. Still a strong cutover data point
  for the dispatch decision class.

- [x] **NOW lane recorded an honest "this cannot be done" as `done`** — found
  by the impossible-binary probe batch (3× "run /usr/bin/nonexistent-binary-xyz"):
  intent routed the execution goal NOW, the completion honestly replied "the
  goal is incomplete... cannot be fulfilled", and the run was recorded `done`
  in 18s — NOW status meant "the completion call returned", not "the goal was
  achieved". The false done then poisoned every judgment layer above it:
  recall reported a done prior, so the dispatch guard could never trip, and
  the navigator's attempt-2 `close` was reasonable-on-poisoned-input. (The
  navigator still said `escalate 0.95` on attempts 1 and 3 — attempt 3
  explicitly caught the contradiction "prior attempts are marked done" vs an
  impossible goal. Divergences #2 and #3, both adjudicated navigator-right.)
  **Fixed same day:** (1) `now_lane.escalate_to_director` default flipped to
  True — complex directives route to the agenda lane; (2) autonomous NOW runs
  (origin present, no human reading the text) get a cheap self-verdict and
  demote to `incomplete` when the response reports non-fulfillment
  (`_verify_now_outcome`, fails open). Interactive NOW calls keep raw speed.
  **Agenda twin fixed same night (02b0263):** the same goal re-run through
  the loop still finalized done — closure said complete=False at 0.95–0.99
  but restarted loops were never re-verified and the verdict never gated
  status. Now: re-verify after closure restart; final complete=False at
  conf ≥0.7 demotes done→incomplete. **Both fixes live-verified:** the
  impossible-file probe finalized `incomplete` end-to-end, and the 4th
  attempt at the binary goal drew the first live RECALL_GUARD_TRIPPED
  (6 honest non-done priors) with the navigator concurring (close 0.99 /
  guard_refused).

- [x] **NOW artifacts write to a stale prototype path** — `_write_now_artifact`
  resolved orch_root and appended `prototypes/poe-orchestration/artifacts/now/`,
  landing files at `~/prototypes/poe-orchestration/prototypes/poe-orchestration/…`
  (doubled segment, outside the workspace). **Fixed 2026-06-12:** NOW artifacts
  now land in the run dir's `artifact/` subtree (current_run_dir, falling back
  to run_dir(handle_id) — both workspace-honoring); artifact_path is absolute.

- [x] **`loop-*-PARTIAL.md` is misnamed on done runs** — fixed same day: the
  transcript is `loop-<id>-RESULT.md` when the loop finished done, `-PARTIAL.md`
  otherwise. Verified no production code reads the filename (only synthetic-name
  tests + cleanup glob, which matches neither).

- [x] **Step numbering in transcripts starts mid-sequence** — root-caused same
  day: `s.index` is the NEXT.md *ledger line* (orch_items.append_next_items
  returns file-line offsets, headers included), not plan position. Display-only
  fix: transcripts and the execution log now render `Step <pos>/<n> (ledger #i)`;
  the ledger index stays untouched (load-bearing for get_item/_by_idx).

### Goal-brain pressure-test findings — runtime gaps (2026-06-10)

From sequencing step 2 (GOAL_BRAIN.md Compiled truth has the full findings; sample = the 2026-05-13..17 run-dir window). The decompose-fallback chop is fixed; these are the remaining mechanical gaps:

- [x] **Run↔thread linkage** — fixed 2026-06-10: tasks carry an `origin` dict (parent_handle_id via `runs.current_handle_id()`, parent_loop_id, parent_goal) set at the agent_loop continuation/escalation enqueues and propagated by director escalation follow-ups; `handle_task` threads it (plus source/job_id/parent_job_id) into `handle(origin=...)`, which stamps it into run-dir `metadata.json` and `handle_inputs.jsonl`. Every requeued run is now traceable to the work that spawned it. Note: ancestry is *recorded*, not yet *consulted* — the dispatch-time read is the recall() work.
- [x] **Dispatch-time dedup/memory** — the same agenda goal ran ~25× in ~35 min on 2026-05-17 (mixed stuck/done) with nothing consulting prior outcomes. **Fixed 2026-06-10 (goal-brain step 3):** `src/recall.py` dispatch slice — `handle()` injects prior-attempt history + thread ancestry into context on every run; `handle_task()` guards the autonomous requeue path (≥3 attempts in 60min all non-done → task errors with a readable reason instead of running; `RECALL_GUARD_TRIPPED` event). Design: `docs/RECALL_DESIGN.md`. Follow-up done 2026-06-11: `_build_loop_context`'s memory half (8 substrates, not 4 sites) relocated behind recall(slice="loop"); evolver's captain's-log bridge absorbed; lesson-cited stamp live in RECALL_PERFORMED.
- [x] **Scope generation fails silently** — `generate_scope` returns None on any adapter failure, so during the rc=1 outage no run got a scope.md and nothing recorded that scope was skipped. **Fixed 2026-06-10:** new `SCOPE_SKIPPED` captain's-log event emitted from handle.py when scope generation is enabled but yields nothing — both the returned-None path (`reason: generator_returned_none`) and the raised-exception path (`reason: exception`, with error preview). Outages now show up in the captain's log alongside `SCOPE_PARSE_FAILED`.

### Dry-run hermeticity — fixed two leak sites, two more fail-safe-by-accident (2026-06-10)

Session 40 found `dry_run=True` runs making **real authenticated `claude -p` CLI calls** (subprocess adapter needs no API key, so conftest key-isolation didn't stop it). test_handle.py alone took 2h06m of real token burn. Fixed:

- [x] `_decompose_goal` planner-lift: `build_adapter()` was called unconditionally, replacing `_DryRunAdapter` with a live adapter. Now guarded on `ctx.dry_run`.
- [x] `_select_step_adapter` (Phase F5): `_DryRunAdapter` has no `model_key` attr → `getattr(..., "")` slipped past the explicit-model check → live adapter per step. Now early-returns on `ctx.dry_run`.
- [x] conftest guard: `tests/conftest.py` now blocks `claude`/`codex` binaries at the `llm._run_subprocess_safe` seam (other commands pass through so its unit tests still run). Tests needing LLM behavior must mock the adapter.
- [x] Adapter-swap seam made principled: the decompose planner-lift and Phase F5 per-step selection now only re-tier adapters that are `isinstance(_, LLMAdapter)` (i.e. build_adapter products they know how to rebuild). Injected test doubles and `_DryRunAdapter` are plain classes and pass through untouched — this is the injection contract.
- [x] Step-shape auto-split was non-convergent: analysis-first steps with an incidental exec keyword (e.g. "Analyze findings from build X") split into a replacement that re-tripped the detector every iteration until max_iterations → stuck. `_split_exec_analyze` now strips analysis clauses from the run part, and the executor-side leak guard executes as-is when a split wouldn't converge. (Also fixed `lstrip('Rr un')` char-set bug.)
- [x] **Hardcoded `_CODEX_BIN = "/home/linuxbrew/.linuxbrew/bin/codex"`** in llm.py — fixed in M5 (2026-06-10): `_find_codex_bin()` resolves CODEX_BIN env → PATH → common locations → bare name, mirroring `_find_claude_bin()`.
- [x] **5 pre-existing worker_session_bridge failures in test_orch_core.py** — root-caused + fixed 2026-06-11. Regression from `a799871` ("support worker manifest args arrays"): the refactor funneled *string* manifest commands through the list-argv quote-join, so the whole shell line became one `shlex.quote`d token and `/bin/sh -c` looked for a program literally named `printf "%s" ... > ...` (exit 127, surfaced as validation 'blocked'). Every string command containing shell syntax (`$VAR`, `>`, heredocs) broke; bare names like `./run.sh` survived because quote was a no-op, and the timeout test passed for the wrong reason (127 also raises → blocked). Fix in `_load_worker_session_manifest`: string commands pass verbatim (matching the top-level-string manifest form), args (if any) appended quoted; list commands keep quote-join. All existing string+args pins (`"python3" + ["-m","worker"]` → `python3 -m worker`) unchanged.
- [x] **Pre-existing: test_scheduler.py `test_inflight_job_not_returned_until_lease_stale`** — root-caused + fixed 2026-06-11. Time-of-day-dependent test: `mark_job_dispatched` stamped the lease at real wall clock while the test probed staleness at synthetic `next_run + 5min`; with the 6h lease the first probe only read fresh between 03:05–09:00 UTC. Fix: `now` seam param on `mark_job_dispatched(job_id, *, now=None)` (mirrors `check_due_jobs`); test stamps the lease at the synthetic probe time.
- [x] **Pre-existing: 4 plan-manifest tests in test_agent_loop.py are order-dependent** — root-caused + fixed 2026-06-11. Not an orch-root cache: `runs._current_run_dir` (module global, pinned by `handle()` via `set_current_run_dir`) leaked across tests, so `runs.artifact_dir()` routed later tests' plan manifests into the stale run's `build/` instead of `projects/<p>/artifacts/`. Production contract is deliberate (CLI clears; programmatic callers clear themselves — handle.py comment) and tests are exactly such callers: autouse conftest fixture now resets the global after every test. Whole pollution class closed, not just these 4.

### Memory lifecycle was write-dead / decay-corrupting — core fixed, wiring shipped (2026-06-10)

Session 40 audit confirmed consolidation **never ran** (only entry point was the `poe-memory decay` CLI, never invoked) and the lifecycle had three latent data-corruption bugs, all fixed in knowledge_web.py:

- [x] Tier-blind decay on load: LONG-tier lessons decayed on read despite "no decay by design" (22 long lessons were reading at ~0.85^46 effective score).
- [x] `run_decay_cycle` persisted decayed scores without moving the `last_reinforced` anchor → compounding rot on every RMW write (reinforce/forget/promote all re-persisted decayed bystander scores). Decay is now strictly a read-time derivation; rewrites use `raw=True`.
- [x] RMW paths loaded with default `limit=50` → stores >50 lessons would be silently truncated on rewrite. All rewrite paths now load `raw=True, limit=None`.
- [x] In-process consolidation ("dream cycle"): `maybe_consolidate()` marker-gated to once per `memory.consolidation_interval_hours` (default 24h), wired into `handle()` (post-request, never affects outcome), heartbeat tick, and `poe-memory consolidate [--force]`. In-process by design — **no cron/daemon** (Jeremy: rogue-process history).
- [x] Promotion timing race (M2, shipped 2026-06-10): promotion now evaluated at reinforcement time via `_post_reinforce_hooks` — score is freshly re-anchored, so eligibility is real. Consolidation-cycle promotion stays as a backstop.
- [x] Standing rules accrete (M2, shipped 2026-06-10): LONG re-confirmation calls `observe_pattern`; `record_tiered_lesson` dedups cross-tier so re-learning a promoted lesson reinforces the LONG record instead of duplicating into MEDIUM. Full path medium → long → standing rule now reachable in production.

### Build-loop wiring — cron wakeups are hitting the wrong abstraction (2026-05-06)

The repeated `poe-orchestration-build-loop` duty-cycle alerts finally coughed up a concrete diagnosis: the 5-minute cron is not running a dedicated autonomous build loop. It is waking the main session with the generic reminder text:

> Read HEARTBEAT.md if it exists (workspace context). Follow it strictly. Do not infer or repeat old tasks from prior chats. If nothing needs attention, reply HEARTBEAT_OK.

That explains the observed pattern:
- `last_status=ok`
- duty cycle usually ~5–7%, occasionally a bit higher
- background checkpoints fine
- repo often clean

In other words: the system is succeeding at the wrong thing. A reminder wake can only do opportunistic work; it is not a real build-runner substrate.

- [x] **Route build-loop cron to a dedicated autonomous runner/supervisor.** Completed 2026-05-06.
  - The dedicated runner lives in `src/build_loop_runner.py` with a lockfile/status contract plus a default `workers/handle.sh` bridge.
  - `python3 src/cli.py build-loop` is the first-class entrypoint and `scripts/build-loop.sh` is the stable cron-facing wrapper.
  - The live OpenClaw cron job `poe-orchestration-build-loop` now targets the dedicated persistent session with a payload instructing it to run the build-loop wrapper instead of the old generic HEARTBEAT reminder text.

### Comprehensive run transparency (audit phase, queued 2026-04-26) — shipped pieces

- [x] **Per-run isolation: branch-name front-loaded into the prompt.** Shipped in `scope_ab_runner.py` 2026-04-26 as the test-side affordance — `scope-ab-r{NN}-{arm}-{TS}` branch pre-created and named in the prompt. Generalized variant for non-test invocations of handle.py is part of the next backlog wave (`--repo-branch-prefix` or auto-derived from goal slug + handle_id).
- [x] **Run-dir as the write destination (not a copy target).** Shipped 2026-04-26 (commits `13a6470`, `8a68e37`). `src/runs.py` creates `~/.poe/workspace/runs/<handle_id>-<nickname>/` at handle start; `set_current_run_dir` pins it as a process-level context var; `artifact_dir()` and `source_dir()` route writes there from agent_loop (PARTIAL.md, scratchpad, step files, plan manifest, loop log) and handle.py (scope.md, resolved_intent.md). Fallback to project_dir/artifacts when no run-dir is active — behavior-preserving for existing callers.
- [x] **Run nickname module.** Shipped 2026-04-26 (commit `13a6470`). 50 adjectives × 50 nouns = 2500 combos; sha1-hashed handle_id for even distribution. 13 tests.
- [x] **Per-run repo bundle.** Shipped 2026-04-26 (commit `a99771b`). `record_repo_base()` at run start when `--repo` is given; `snapshot_repo_bundle()` on finalize writes `repo.bundle` (`git bundle --all`), `git_log.txt`, `branch_diff.patch`, `base_sha.txt` into `<run-dir>/artifact/`. Restorable with `git clone repo.bundle`. 5 tests.
- [x] **Per-run captain's log slice.** Shipped 2026-04-26 (commit `17fb0e9`). `record_log_offset()` at run start, `slice_log_for_run()` on finalize writes `<run-dir>/build/captains_log_slice.jsonl` covering only this run's events. Same pattern `scope_ab_runner.py` used externally — now centralized so every paid run gets a slice. 4 tests.
- [x] **Quality-gate verdict as a captain's log event.** Shipped 2026-04-26 (commit `c644d82`). `QUALITY_GATE_VERDICT` event with verdict/confidence/escalate/reason/step_count/loop_id; emitted from `quality_gate.py::run_quality_gate` after pass1 verdict parsing.
- [x] **`LOOP_CREATED` captain's log event with `reason` + `parent_loop_id`.** Shipped 2026-04-26 (commit `c644d82`). Emitted in `agent_loop._initialize_loop` with reason ∈ {initial, director_restart, closure_restart, quality_gate_escalate}, parent_loop_id, project, max_steps, continuation_depth, dry_run. Threaded through handle.py spawn sites for closure-restart, director-restart, and quality-gate escalation.

### Runtime visibility (tracked 2026-04-17) — shipped pieces

- [x] **Current-step symlink.** `/tmp/poe-current-step.log` → active
  streaming merged-output file, updated atomically as each subprocess
  starts. (Shipped 2026-04-17 — commit 58a91dd; symlink target extended
  to merged stream in b188e5f.)
- [x] **Claim-verifier outcome event.** Structured CLAIM_VERIFIER_OUTCOME
  event now emitted with step id + file_not_found/symbol_not_found lists
  + downstream action taken. (Shipped 2026-04-17 — commit 58a91dd.)
- [x] **Closure + quality_gate run on partial/stuck/restart.** Previously
  gated on `status == "done"`; metacognitive-recovery paths produced
  material work but emitted no CLOSURE_VERDICT / CLAIM_VERIFIER_OUTCOME /
  CLAIM_PROBED events because terminal status wasn't "done". Widened to
  run on any terminal state that produced ≥1 successful step; kept the
  *escalation* branches gated on "done" only. (Shipped 2026-04-18 —
  commit 7f907bd.)
- [x] **Merged stdout+stderr stream.** `_run_subprocess_safe` pipes both
  streams into a single temp file via `stderr=subprocess.STDOUT`.
  Operator view via `/tmp/poe-current-step.log` now matches what the
  subprocess would print to a terminal. JSON parser tolerant of
  interleaved non-JSON prose. (Shipped 2026-04-18 — commit b188e5f.)
- [x] **CPU-activity liveness signal.** Secondary liveness check sums
  utime+stime across every proc whose session == subprocess pid. A
  silent-but-computing local model burns CPU → last_seen advances →
  liveness timer doesn't fire. Protects slow/local-model inference paths
  from false-kills. (Shipped 2026-04-18 — commit b188e5f.)

### Step-process visibility + elevation (discovered 2026-04-17) — shipped piece

Run 5 of slycrel-go lost step 9 to a hard 600s wall-clock kill of the
`claude -p` subprocess. No way to distinguish "hung" from "working hard",
no partial output captured. Jeremy's framing: "if a step is going to take
that long, it should probably be a sub-milestone/goal on its own, not
just a step" — mirrors the ralph-within-structure feedback (a step that
needs 10+ minutes is a goal the decomposer miscategorized).

- [x] **Heartbeat / liveness timeout.** Stream step subprocess stdout+stderr
  to disk instead of buffering. Kill on *no output for N seconds*, not
  wall clock. Partial output survives the kill. See
  `src/llm.py::_run_subprocess_safe`. (Shipped 2026-04-17 — commit
  a44eb6a.)

### Session 20 (2026-04-14) — adversarial review findings (`output/self-review-report-20260414T040637Z-blind.md`)

- [~] **HIGH: Test coverage width not depth** — PARTIAL. pytest-cov with 70% floor: DONE (session 20.5, .coveragerc). Concurrent task_store tests: DONE (session 20.5, +5 tests). End-to-end integration tests: DONE (test_integration.py, 23 tests). Remaining: mutation testing (aspirational, no tooling) and real-LLM-fixture tests (expensive, defer). Item substantially closed.
- [~] **MINOR: Persona auto-selection missing** — Hallucinated. Auto-selection already exists: `persona.py:793` (`persona_for_goal`) with keyword routing + scoring + LLM fallback + freeform creation; called from `handle.py:615` in AGENDA flow. NOW lane intentionally skips persona injection (1-shot path). No fix needed.

### Step runner hang protection / long-lived-process affordance (2026-04-26)

- [x] **Step runner has no hang protection / no long-lived-process affordance.** Partially closed 2026-04-26 (commit TBD): step_exec.py now classifies long-lived steps via `_is_long_lived_step` (phrase set + verb-noun regex catching "start/launch/run/spawn/boot the X server/service/daemon/listener/broker/worker/api"); when matched, injects `_LONG_LIVED_PROCESS_EXTRA` into user_msg telling the executor to (a) background-spawn (`run_in_background`/`& disown`/`nohup &`), (b) probe readiness via curl/nc/log-grep, (c) call complete_step on readiness signal — not on exit. 14 new tests in `tests/test_step_exec.py::TestIsLongLivedStep` cover the audit case ("Start server with --headless flag on localhost:8080"), each long-lived phrase, the verb-noun regex, and false-positive guards (test/read/analyze steps).

  Original audit case: scope A/B run-02-control (2026-04-23, `~/.poe/experiments/scope-ab-2026-04-22/run-02-control/`) hit step 27 "Start server with --headless flag on localhost:8080", hung indefinitely until SIGTERM (rc=-15). Planner treated "start the server" as a discrete decompose step; the executor had no signal to spawn-and-detach.

  **Still open** (deferred — escalate if observed in the next A/B run):
  - step-runner hard timeout: per-step wall-clock cap that produces a `requires_background_mode` outcome rather than a generic timeout (currently the adapter-level 600s cap fires, but the step is marked blocked rather than actionable)
  - decompose-time classification: emit `background=true` on the step manifest so introspection sees the structural mismatch when later steps depend on a non-terminating one
  - planner prompt change: instruct the decomposer to *not* emit "start server" as a terminal step — servers should start inside a verification step that also probes and shuts down

  **Why this matters:** until this is fully closed, any blind-test goal that produces a long-running binary remains a hazard on the control arm. Scope-injected arms compress to 8 steps and keep server startup inside the verification phase, so they sidestep it; the prompt nudge above should help control arms too.

### decomposition_too_broad threshold miscalibrated post-scope (2026-04-23)

- [~] **`decomposition_too_broad` threshold is miscalibrated post-scope.** Scope A/B 2026-04-23: every treat run (scope injected) got `DIAGNOSIS: decomposition_too_broad (warning). 8/8 steps done.` — despite 8 being the *narrowest* decomposition achieved across the whole experiment (controls were 15/37/40). The diagnostic threshold was tuned on pre-scope runs; scope-injected plans are now systematically compressed enough to trip the threshold as a baseline. The warning has become noise.
  - **LARGELY ADDRESSED by the cache-aware conversion (2026-06-22).** The threshold now gates on `fresh_tokens`, not raw volume. Most of the spurious flagging was a step's *cache reads* (subprocess worker re-reading files) counting toward the 200K cap. **Live evidence, same day:** three real runs at **457K / 393K / 547K total tokens** all diagnosed **`healthy`** — none tripped `decomposition_too_broad` — because fresh tokens/step are ~5 (the rest cache reads). The exact scenario that used to flag noise now reads clean. Remaining open question (hence ~, not done): whether a step doing genuinely >200K *fresh* tokens on an otherwise-successful run should warn at all, or only when the loop also shows stress (blocked steps / budget exhaustion). Revisit only if a real fresh-heavy run flags spuriously — the cache fix removed the observed noise source.

  **Candidates:**
  - re-tune the threshold against the post-scope decomposition distribution (8 steps for a medium-complexity blind-test goal is fine; treat that as the new normal)
  - condition the threshold on `scope_supplied=true` — scope-gated plans should be *expected* to be tighter
  - separate "too few steps" from "too many steps" — current single-dimension warning fires on both ends ambiguously

### run-03-treat CLOSURE_VERDICT emission (2026-04-23 → fixed 2026-06-11)

- [x] **`run-03-treat` didn't emit CLOSURE_VERDICT despite reaching adversarial review.** Scope A/B run-03 (2026-04-23): 8/8 steps completed, adversarial review fired (3 claim probes), `decomposition_too_broad` diagnosis logged, rc=0 — but no `CLOSURE_VERDICT` event in captain's log and no `closure check: complete=...` line in handle.log. **Root cause (2026-06-11):** `verify_goal_completion` had three silent `return _null` early-exit paths (`no_checks_generated`, `no_check_results`, `verdict_parse_failed`) and an outer-except path that all returned without emitting CLOSURE_VERDICT. Run-03-treat hit `no_checks_generated` (LLM plan returned empty checks). **Fix:** added `_emit_skip(reason)` local helper emitting CLOSURE_VERDICT with `skip_reason` context before each silent return; outer except now also emits. 4 regression tests added (`test_closure_verdict_emitted_when_no_checks_generated`, `…_no_check_results`, `…_on_exception`, `…_not_emitted_on_dry_run`). Shipped 2026-06-11.

### Introspect-sees-no-action: decomposition_too_broad (and siblings)

- [x] **`decomposition_too_broad` fires but nothing acts on it.** Partially closed 2026-04-26: introspect now stamps `LoopDiagnosis.project` so retrieval can prioritize same-project history; `find_relevant_failure_notes` ranks same-project diagnoses above goal-token overlap; `decomposition_too_broad` notes render with concrete numbers (e.g. "Step 8 took 534s with 277K tok") and append the actionable cap (`≤120s/200K tok per step; split if a step touches >3 files`). The next loop on the same project sees this in `lessons_context` ahead of all other failure-pattern injections. Phase 62 (mid-loop redecompose on `_handle_blocked_step`) was already live for the blocked path; this closes the *post-mortem → next-decompose* feedback that was previously generic-lesson-only. Original Apr 16 finding (`loop 85ac29ee-*`) is the canonical case this addresses.

  **Mid-loop visibility added 2026-04-26 (commit TBD):** new `STEP_TOO_BROAD` captain's log event fires the moment a `done` step exceeds both caps (>120s elapsed AND >200K tokens). Wired in `_write_iteration_artifacts` after march-of-nines. Visible in the per-run `captains_log_slice.jsonl` and as a project decision. The post-mortem path already feeds the next decompose; this closes the visibility gap on the in-flight loop. 7 new tests in `tests/test_agent_loop.py` cover the predicate (above caps, below caps, only-one-cap, blocked/skipped/zero-metric guards, EVENT_TYPES registration).

  **Still open** (deferred — needs more A/B data before committing to mid-loop intervention): actually *acting* on the signal mid-loop (kill + replan vs continue with warning logged). Visibility-first is the cheapest credible upgrade today; the action question deserves data on how often the signal fires and whether the loop completes successfully despite it.

### Semantic memory deduplication (2026-04-12)

- [~] **Semantic memory deduplication** — SUBSTANTIALLY ADDRESSED. `record_lesson()` already does at-write-time near-dedup: exact-text match + word-overlap Jaccard ≥ 0.8 within most-recent 100 lessons. Unbounded growth prevented. Embedding-based similarity (true semantic) remains aspirational P3 — requires API call at every write, cost not justified given current lesson volume.
