# Roadmap

Phased build plan for autonomous agent orchestration. Each phase is independently shippable and testable.

**North star**: give the system a top-level mission; it breaks it into milestones, executes them autonomously over days/weeks, learns from what works, and reports progress without hand-holding. The user's job is mission definition and exception handling — not step supervision.

See `VISION.md` for the full intent.

## Guiding systems model: Visibility → Reliability → Replayability

A recurring systems principle for orchestration:

1. **Visibility** — see what the system planned, did, spent, produced, and why it failed.
2. **Reliability** — make the common path complete consistently, fail legibly, and recover sanely.
3. **Replayability** — preserve enough trace/checkpoint fidelity to replay failures, compare policy changes, and evaluate alternate interventions.

These stages build on each other. Visibility without reliability is just a clearer view of dysfunction. Reliability without replayability limits the system's ability to learn from past runs.

Where each phase sits:

| Stage | Phases | What they deliver |
|-------|--------|-------------------|
| **Visibility** | 23 (observe), 36 (dashboard), 43 (structured logging), 44 (failure classifier + lenses) | See what happened, why, and where tokens/time went |
| **Reliability** | 22 (rules), 32 (skills auto-promotion), 33 (token budgets), 35 (constraints + HITL), 44-45 (diagnosis + recovery planner) | Common path completes, failures are legible, recovery is mechanical |
| **Replayability** | 42 (nightly eval), 46 (intervention graduation), 40 (SQLite for queryable history) | Replay failures, compare policy changes, harden from past runs |

We're currently at the **visibility→reliability boundary**. The introspection and lens work is converting visibility into reliability — structured diagnosis that changes behavior, not just reports.

A short version: **stop debugging by seance, then stop failing the same way twice, then make past runs reusable for future improvement.**

---

## Completed Phases (0–56)

All shipped. See `docs/ROADMAP_ARCHIVE.md` for full details.

---

### Phase 57: Adaptive Model Tiering *(DONE)*

*"Haiku-first with system-driven upgrades — spend the expensive tokens where they compound."*

**Shipped:**
- Decompose always uses at least mid (Sonnet) even when loop adapter is cheap. A weak planner produces a bad plan that costs you across every step; Sonnet is ~10x cheaper than Opus and much stronger than Haiku for decomposition.
- Retry-based tier escalation: when a step is blocked and retried, the system automatically bumps it — cheap → mid on first retry, mid → power on second. System-driven, not model-driven (the model that failed doesn't decide it needs help).
- Opus reserved for explicit `garrytan:` / power-tier requests, not the default path.

**Design intent:** Haiku as the cheap execution baseline, `classify_step_model` for content-based upgrades (synthesis/planning → Sonnet), retry escalation for quality failures. The system shakes out the right tier from signals, not brute-force.

**All shipped (2026-04-06):**
- Ralph verify failure → tier escalation: `_step_tier_overrides` set on verify fail.
- Session-level lagging signal: `_session_verify_failures` counter; ≥3 consecutive verify
  failures raises `_session_tier_floor` to mid for remaining steps. (DONE)
- Synthesis step always uses mid or higher: `classify_step_model` already returns MODEL_MID
  for synthesis/analyze/research keywords; session floor also applies. (CONFIRMED WORKING)
- Adaptive decompose: narrow goals skip multi-plan (4 LLM calls → 1 via `estimate_goal_scope`).
  Wide/deep still route to staged-pass. (DONE via Phase 58 scope estimator)

---

## Superseded Plans

The original M0-M4 milestones and N1-N4 roadmap items focused on infrastructure plumbing (adapters, scheduling, CI). That work was valuable scaffolding, but it didn't address the core need: making Poe autonomous. This roadmap replaces N1-N4 entirely.

---

### Phase 58: Pre-Flight Plan Review *(DONE)*

*"System 1 proxy: fast pattern recognition over the plan before System 2 commits to executing it."*

**The problem**: the planner (System 2) decomposes a goal into steps without knowing the true
scope, hidden assumptions, or which steps are sub-goals in disguise. By the time these surface
during execution, budget has been wasted. Taste — the intuitive sense that a plan smells right —
is what's missing.

**Shipped:**
- `src/pre_flight.py`: cheap Haiku call that reviews the proposed step list before execution
  starts. Returns scope estimate (narrow/medium/wide), assumption flags, milestone candidates,
  and unknown-unknown warnings. Advisory only — never blocks execution.
- `personas/plan-critic.md`: System 1 proxy persona — fast, adversarial, verdict-first.
  Asks: is the scope honest? What assumptions could be wrong? Which steps are sub-goals?
- Wired into `agent_loop.py` after decompose and pre-run cost estimate. Flags logged at
  WARNING level if scope=wide or milestone candidates found.

**Shipped (2026-04-06):**
- Scope estimation *before* decomposition: `estimate_goal_scope(goal)` in `planner.py`
  classifies as narrow/medium/wide/deep using zero-LLM heuristics. Narrow goals skip
  multi-plan (saves 4 LLM calls); wide/deep route to staged-pass; scope hint injected
  for medium goals. (DONE)

**Shipped (2026-04-06):**
- Multiple philosopher perspectives: `multi_lens_review()` in `pre_flight.py` — 3 focused
  Haiku passes (scope-detector, dependency-spider, assumption-auditor), merged into one
  PlanReview. Opt-in for high-stakes goals. (DONE)

**Not yet shipped (see ARCHITECTURE.md for full design note):**
**Shipped (2026-04-06 continued):**
- Milestone-aware execution: steps flagged by pre-flight as milestone candidates are
  pre-decomposed into sub-steps (via `planner.decompose`) before execution. Depth-gated
  at continuation_depth == 0 to prevent recursive explosion. Fall-through if decompose
  returns ≤1 step. 3 tests. (DONE)
- Acting on pre-flight output: `LoopResult.pre_flight_review` field now carries the
  PlanReview to callers; handle.py appends a ⚠️ warning to result text when scope=wide.
  (DONE 2026-04-06)
- Feedback loop: `memory/preflight_calibration.jsonl` logs scope_predicted vs actual_status
  per loop, with true_positive/false_positive/false_negative/true_negative classification.
  Wired into agent_loop.py at loop completion. 3 tests. (DONE)

**Not yet shipped:** (all items complete — Phase 58 DONE)

---

### Phase 59: Systemic Quality — NeMo DataDesigner + Feynman Research Steals *(DONE)*

*"Steal the best patterns from production systems that solved the same problems."*

**Source research:** `output/x-research-20260407T063015Z.md` — 10 steal candidates from NVIDIA NeMo DataDesigner and Feynman AI Research Agent.

**Shipped (2026-04-07):**

- **Skill cost/latency telemetry** (NeMo Steal 6) — `SkillStats` extended with `total_cost_usd`, `avg_latency_ms`, `avg_confidence` fields. `record_skill_outcome()` accepts optional `cost_usd`, `latency_ms`, `confidence` kwargs. `efficiency_score()` method: cost-adjusted success rate for evolver promotion decisions. 7 tests. (`src/skills.py`)

- **Persona template variable injection** (NeMo Steal 3) — `extract_template_variables()` and `render_persona_template()` in `persona.py`. Persona system_prompt can now use `{{ goal }}`, `{{ standing_rules }}`, `{{ recent_lessons }}`, `{{ task_type }}` — lazy-fetched (only loads what the template references). Wired into `build_persona_system_prompt()`. 12 tests.

**Not yet shipped:**

- ~~**Typed lesson taxonomy + seed/ATIF/cross-type cap** (NeMo S1/S2/S3/S5)~~ — **DONE** (2026-04-07). `lesson_type` field on `TieredLesson`; `load_tiered_lessons()` + `query_lessons()` filter by type. `_REFLECT_SYSTEM` prompt updated to elicit typed lessons. Seed-reader bootstrapping: top-1 long-tier lesson prepended as style guide. ATIF feedback: avg reinforcement stats injected into prompt. Cross-type cap: max 1 lesson per type per extraction. `return_typed` kwarg. 9 tests.
- **SEED_READER / plugin injection** (NeMo Steal 2 residual) — `inject_into_processor_config_type_union` pattern: skill types register into a shared dispatch table without touching core routing. Low priority; current island-based routing covers 90% of use cases.
- ~~**Skill type-aware ranking** (NeMo S4)~~ — **DONE** (2026-04-07). `_tfidf_skill_rank()` detects goal intent via inline island keyword scoring; applies +20% boost to skills whose island matches. 1 test. (`src/skills.py`)
- ~~**ViolationType enum config** (NeMo Steal 4)~~ — **DONE** (2026-04-07). `ViolationType` class + `ViolationReport` dataclass in constraint.py. 12 typed violation constants (DESTRUCTIVE_COMMAND, HALLUCINATED_CLAIM, etc.) with (category, description, severity). `ConstraintResult.to_violation_reports()` + `has_fatal_violations()`. 10 tests.
- **AIMD throttling** (NeMo Steal 5) — per-worker concurrency self-tuning. Low complexity once parallel workers are enabled.
- ~~**Sampler constraints for skill A/B testing** (NeMo Steal 7)~~ — **DONE** (2026-04-07). `SkillConstraint` dataclass + `apply_skill_constraints()` in skills.py. Condition/exclusion keyword matching; parameter_overrides noted in optimization_objective. 6 tests.
- ~~**Task ledger + verification log** (Feynman Steal 8)~~ — **DONE** (2026-04-07). `TaskLedgerEntry`, `append_task_ledger()`, `load_task_ledger()` in memory.py. Wired into agent_loop.py after each step. 5 tests.
- ~~**Evidence table + claim tracing** (Feynman Steal 9)~~ — **DONE** (2026-04-07). `evidence_sources: List[str]` field added to `TieredLesson`; `record_tiered_lesson()` accepts `evidence_sources` kwarg. Backward compatible. 3 tests.
- ~~**Multi-round gap analysis** (Feynman Steal 10)~~ — **DONE** (2026-04-07). `GoalGap` dataclass + `detect_goal_gaps()` in memory.py. Heuristic detection: blocked steps (high), uncovered goal keywords (medium), unused lessons (low). Sorted by severity, capped by max_gaps. 8 tests.
- ~~**Verifier agent** (Feynman Steal 11)~~ — **DONE** (2026-04-07). `verify_skill_description()` in skills.py: heuristic regex check for absolute claims, unsourced metrics, version-specific claims, internal API references. Returns `SkillVerificationResult` with suspicious claims + confidence score. No LLM call, zero cost. 5 tests.
- ~~**Provenance records** (Feynman Steal 12)~~ — **DONE** (2026-04-07). `write_skill_provenance()` + `load_skill_provenance()` in skills.py. Wired into promote and demote. 4 tests.
- ~~**Confidence tier standardization** (Feynman F5)~~ — **DONE** (2026-04-07). `confidence_from_k_samples()`: single=0.5, 2-sample=0.6, majority-vote=0.7. `record_tiered_lesson(k_samples=N)` auto-computes. `reinforce_lesson()` + `_reinforce_tiered_lesson()` bump to 0.9+ at sessions_validated≥3. 6 tests; test_memory at 103.
- ~~**Accumulating verifier memory** (Feynman F4)~~ — **DONE** (2026-04-07). `VerificationOutcome` dataclass + `record_verification()` + `load_verification_outcomes()` + `verification_accuracy()` in memory.py. Wired into `inspector.check_alignment()`. Enables per-claim-type accuracy trends. 6 tests; test_memory at 109.
- ~~**Adversarial lens** (Feynman F3)~~ — **DONE** (2026-04-07). `_adversarial_lens()` in introspect.py — devil's advocate LLM review, cost='mid'. Registered in default LensRegistry. 4 tests; test_introspect at 38.
- ~~**Token transparency in extraction** (Feynman F6)~~ — **DONE** (2026-04-07). Per-call `tokens_in/out` tracked in `extract_lessons_via_llm()`, logged at INFO + forwarded to `metrics.record_cost`. Zero breaking change.
- ~~**Typed lesson taxonomy + seed/ATIF/cross-type cap** (NeMo S1/S2/S3/S5)~~ — **DONE** (2026-04-07). See above entry. test_memory at 97 after S1-S5 merge.
- ~~**Island-aware TF-IDF skill ranking** (NeMo S4)~~ — **DONE** (2026-04-07). See above entry. test_skills at 137.
- ~~**Tiered source fallback for claim verification** (Feynman F1)~~ — **DONE** (2026-04-07). `verify_claim_tiered()` + `TieredVerificationResult` in inspector.py. P1: lessons; P2: standing_rules; P3: heuristic. 4 tests; test_inspector at 82.
- **Typed lessons wired through reflect_and_record()**: `reflect_and_record()` now uses `return_typed=True` and auto-records each typed lesson to MEDIUM tier (k_samples=1 → 0.5 confidence). Closes the S1 loop — types flow from extraction through tiered storage to future type-filtered injection.
- **Remaining (deferred):** AIMD throttling (NeMo Steal 5, needs parallel workers), SEED_READER plugin injection (low priority).

**Phase 59 summary:** 15+ steal items shipped. test_memory: 109, test_skills: 137, test_introspect: 38, test_inspector: 82.


### Phase 60: Adversarial Verification Layer *(DONE)*

*"Make verification adversarial by design — not an afterthought."*

Shipped 2026-04-07. 4 systemic steal items, all pattern-driven (no new if-else branches).

- [x] **Citation enforcement** (`_CITATION_PENALTY = 0.90`): uncited lessons discounted 10% in `_tfidf_rank()` — cited lessons rank higher on ties. Evidence quality is now a first-class ranking signal. (`memory.py`)
- [x] **Verification calibration loop** (`calibrated_alignment_threshold()`): derives alignment pass/fail threshold from `verification_accuracy()` history. Conservative verifier → lower threshold; strict verifier → raise. Wired into `check_alignment()`. (`memory.py`, `inspector.py`)
- [x] **Multi-model adversarial review** (`adversarial_sample()`): mid-run entry point for adversarial lens — no LoopDiagnosis needed. `model` kwarg enables cross-model verification (adversarial call runs on a different model than the primary loop). (`introspect.py`)
- [x] **Heartbeat session guard** (`_is_interactive_session_active()`): detects `claude --continue` in process table and skips ALL autonomous LLM work (backlog drain, task-store drain, evolver, inspector). Prevents double-burning during interactive sessions. Backlog drain interval 3→30 ticks. (`heartbeat.py`)

**Tests:** test_memory=118, test_introspect=42, test_inspector=84.

**Deferred (Phase 61 candidates):**
- Cross-agent claim challenge — persona B challenges worker claims; disagreement triggers retry.

---

### Phase 62: Adaptive Replanning — Close the Double-Loop *(DONE)*

*"Stop guessing when you can decompose further. Stop retrying when the plan is wrong."*

**The gap:** The zoom-metacognition research (`docs/research/zoom-metacognition.md`, 2026-03-27) designed a complete retry-vs-redecompose algorithm. Phases 44-45 built the diagnosis side. The action side — mid-loop replanning triggered by blocked steps — was never implemented. Steps currently retry with tier escalation until stuck, then the whole goal re-runs. No mid-loop re-decomposition, no convergence tracking, no sibling failure correlation, no "I don't know" step output.

**Found during session 18:** Self-audit hallucinated 6/10 findings because steps guessed about code they hadn't read instead of requesting sub-steps to verify. Dev agent hit constraint false-positive because decomposer put shell commands in step text. Both failures trace to the same root: steps can't say "I need to break this down further" or "I need more context before proceeding."

**Design source:** `docs/research/zoom-metacognition.md` — Argyris double-loop, Boyd OODA, adaptive expertise. The `on_step_failure()` algorithm with `RETRY_THRESHOLD=3`, `SIBLING_THRESHOLD=50%`, `REDECOMPOSE_THRESHOLD=2`.

**Deliverables (ordered by dependency):**

1. **Convergence tracking** ✓ — `_error_fingerprint()` + `_is_converging()` in agent_loop.py. Tracks error fingerprints per retry via `_error_fingerprints` dict. Convergence = unique fingerprints > 50%.

2. **Mid-loop re-decomposition** ✓ — `_handle_blocked_step()` now returns `redecompose=True` when retries are not converging. `_process_blocked_step()` calls `decompose()` on the stuck step to generate sub-steps, injected via existing mechanism.

3. **Sibling failure correlation** ✓ — `_sibling_failure_rate()` checks blocked/total ratio. If >50% siblings failing and ≥3 steps completed, triggers re-decomposition of the plan instead of retrying individual steps.

4. **"I don't have enough info" as valid step output** ✓ — `NEED_INFO:` prefix in stuck_reason triggers research sub-step generation + re-queue of original step. EXECUTE_SYSTEM prompt updated with NEED_INFO instructions.

5. **Shared artifact layer** ✓ — `complete_step` tool extended with `artifacts` field (key-value string pairs, max 5, 2000 chars each). Stored in `loop_shared_ctx` as `artifact:{step_idx}:{name}`. Injected into subsequent steps as "Artifacts from prior steps" block. EXECUTE_SYSTEM updated with artifact documentation.

6. **Cross-ref wired into step verification** ✓ — `verify_step_with_cross_ref()` in step_exec.py. Heuristic `_has_specific_claims()` detects file paths, line numbers, function names. Triggers cross-ref claim extraction for steps with specific claims. Annotates (doesn't block) when disputes found.

7. **Anti-hallucination prompt injection** ✓ — EXECUTE_SYSTEM updated with ANTI-HALLUCINATION section: never guess file paths/line numbers/function names, mark unverified claims as [UNVERIFIED], use inject_steps for verification sub-steps.

8. **Metacognitive logging** ✓ — Every `_handle_blocked_step()` decision includes `metacognitive_reason` string. Logged to captain's log as `METACOGNITIVE_DECISION` event with step context, retry count, fingerprints, and chosen action.

**Existing infrastructure to build on:**
- `inject_steps` mechanism (step_exec.py + agent_loop.py) — mechanical step injection, working
- `_handle_blocked_step()` in agent_loop.py — current retry logic, needs convergence check added
- `plan_recovery()` in introspect.py — currently fires at loop-end, needs mid-loop trigger
- `replan_count` tracking — exists, needs to gate re-decompose decisions
- `loop_shared_ctx` dict — exists but barely used, expand into artifact layer
- `cross_ref.py` — built, tested, not wired into step path

---

### Phase 61: Integration Depth *(PARTIAL — session 23)*

*"3061 unit tests and only 31 integration tests — close the gap."*

Self-review identified 15 integration tests needed that aren't covered by the unit suite:

**Shipped (session 23, 2026-04-14):** +5 tests in `tests/integration/test_integration.py`
- `TestCheckpointRecovery` (3 tests): checkpoint written per step, resume skips completed steps, missing checkpoint starts fresh
- `TestMemoryInjection` (2 tests): lessons written during loop are retrievable, lessons inject into decompose context
- Total integration tests: 42

**Remaining candidate items:**
- AGENDA lane end-to-end: enqueue → heartbeat picks up → agent_loop runs → outcome recorded
- Adapter switching: Anthropic API → OpenRouter fallback → subprocess fallback chain
- Cross-agent claim challenge: persona B challenges worker claim; disagreement triggers retry (deferred from Phase 60)


