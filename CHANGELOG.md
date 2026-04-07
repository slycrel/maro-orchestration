# Changelog

## [1.11.0] - 2026-04-07

Phases 59–61 + token runaway fix. ~3200 tests passing.

### Fixed — Token runaway (overnight gorge diagnosis)
- `heartbeat.py`: `_tier2_llm_diagnosis()` was called every 60s × N stuck projects.
  With 6 stuck zombie projects, that was 360 LLM calls/hour overnight.
  Fixed with `_DIAGNOSIS_COOLDOWN_SECS = 1800` (max 2 diagnoses/hour per project).
- `heartbeat.py`: Session guard `_is_interactive_session_active()` detects running
  `claude --continue` process and skips ALL autonomous LLM work: tier-2 diagnosis,
  backlog drain, task-store drain, mission check, evolver, inspector.
- `heartbeat.py`: `backlog_every` default 3 → 30 ticks (~30 min between drains).
- 7 zombie projects marked `.poe-failed` — sheriff now reports `stuck=[]`.

### Added — Phase 59: NeMo + Feynman steal items (DONE)
- **Typed lesson taxonomy** (NeMo S1): `lesson_type` field on `TieredLesson`;
  type-filtered retrieval; `extract_lessons_via_llm()` returns `(text, type)` pairs;
  `reflect_and_record()` auto-records typed lessons. Types: execution/planning/recovery/verification/cost.
- **Seed-reader bootstrapping** (NeMo S2): top-1 LONG lesson as style guide before extraction.
- **ATIF feedback** (NeMo S3): `avg_times_reinforced` + `avg_times_applied` injected into extraction prompt.
- **Island-aware TF-IDF** (NeMo S4): `_ISLAND_BOOST = 0.20` for matching skill island in `_tfidf_skill_rank()`.
- **Cross-type cap** (NeMo S5): at most 1 lesson per `lesson_type` per extraction call.
- **Confidence tiers** (Feynman F5): `confidence_from_k_samples()` — k=1→0.5, k=2→0.6, k≥3→0.7; `sessions_validated≥3`→0.9+.
- **Adversarial lens** (Feynman F3): `_adversarial_lens()` in lens registry, cost="mid", deterministic flag.
- **Token transparency** (Feynman F6): `input_tokens`/`output_tokens` tracked per `extract_lessons_via_llm()` call.
- **Named threshold constants** (Feynman self-review): `_BROAD_STEP_TOKEN_LIMIT`, `_TOKEN_EXPLOSION_RATIO`, etc.
- **Tiered source fallback** (Feynman F1): `verify_claim_tiered()` in inspector.py — P1 lessons → P2 standing rules → P3 heuristic.
- **Accumulating verifier memory** (Feynman F4): `VerificationOutcome` + `record_verification()` + `verification_accuracy()`.
- **GoalGap detection** (Feynman F10): `detect_goal_gaps()` from outcomes.
- **Evidence tracing** (Feynman F11): `evidence_sources` field on `TieredLesson`.
- Tests: test_memory=118, test_introspect=42, test_inspector=84, test_skills=137.

### Added — Phase 60: Adversarial Verification Layer (DONE)
- **Citation enforcement** (`_CITATION_PENALTY = 0.90`): uncited lessons discounted 10% in `_tfidf_rank()`.
- **Verification calibration loop** (`calibrated_alignment_threshold()`): derives alignment threshold from verifier history; wired into `check_alignment()`.
- **Multi-model adversarial review** (`adversarial_sample()`): mid-run entry point, no `LoopDiagnosis` needed; `model` kwarg for cross-model verification; `_adversarial_lens()` gains `model` kwarg.
- Tests: +9 (TestCitationEnforcementPenalty ×3, TestCalibrationLoop ×6).

### Added — Phase 61: Integration depth (DONE)
- `tests/test_integration.py`: 14 integration tests across 5 scenarios — memory injection, checkpoint pipeline, adapter fallback chain, adversarial_sample mid-run, calibration→inspector wiring.

### Added — Project visibility + lifecycle state
- `poe-observe projects`: per-project status board (ACTIVE/STUCK/OK/FAILED/PAUSED) with ANSI colour. Included in default `poe-observe` snapshot.
- `sheriff.py`: `.poe-failed` / `.poe-paused` marker files; `mark_project_failed()`, `mark_project_paused()`, `project_lifecycle_state()`; `check_project()` short-circuits on marker.
- `orch_items.py`: `select_global_next()` skips failed/paused projects from backlog drain.

### Added — Compact notation skill
- `skills/compact_notation.md`: shorthand vocabulary for token-efficient agent reasoning (ok/err/blk, r:/bld:/exec:, w/, n=). Opt-in via skill injection. Shorthand testing deferred to backlog.

## [1.10.26] - 2026-04-05

Phase 29 research complete — tacit knowledge + Enneagram 6w5/INFJ companion design.

### Added — Phase 29 final research artifacts
- `docs/research/tacit-vs-explicit.md`: Polanyi/Dreyfus/SECI synthesis; tacit→explicit conversion loss; Stage 4→5 transition mechanics (chunking, subsidization, schema formation); 8 design implications for crystallization pipeline (Fluid/Lesson/Identity/Skill/Rule stages); failure modes to avoid. 6/6 steps, 188k tokens.
- `docs/research/enneagram-6w5-infj.md`: 6w5 trust calibration mechanics; INFJ Ni-Fe assumption gap + door slam; 7 combined failure modes (trust collapse cascade, double withdrawal loop, subtext overload, etc.); companion AI persona design principles (predictability > polish, acknowledge before advancing, calibrated honesty, no over-promising). 6/6 steps, 276k tokens. Informs Phase 28 persona design.

## [1.10.25] - 2026-04-05

Rule A/B variant system (Agent0 steal). 2917 tests passing.

### Added — Skill A/B variant competition (`src/skills.py`, `src/evolver.py`)
- `Skill` fields: `variant_of: Optional[str]` (parent ID), `variant_wins: int`, `variant_losses: int` — serialized to/from skills.jsonl; legacy skills load with safe defaults
- `create_skill_variant(original, rewritten)` — marks rewritten skill as challenger of original, resets win/loss counters; parent unchanged
- `get_skill_variants(parent_id, skills)` — returns all active challengers for a parent
- `select_variant_for_task(parent, task_id, skills)` — deterministic 50/50 routing via `sha1(task_id) % pool_size`; returns parent if no challengers
- `record_variant_outcome(skill_id, success)` — increments `variant_wins`/`variant_losses` for variant skills only; no-op for non-variants; full skills rewrite for thread safety
- `retire_losing_variants(*, dry_run, min_uses)` — evaluates all (parent, challengers) groups; requires ≥ `min_uses` total trials before acting; challenger wins → parent content replaced, challenger retired; parent wins/tie → challenger retired; dry_run returns result without saving; `MIN_VARIANT_USES = 5`
- Frontier rewrites in `run_skill_maintenance()` now create challenger variants (saved alongside parent) instead of immediately replacing — no skill is overwritten without competition
- `retire_losing_variants()` wired into `run_skill_maintenance()` on every evolver cycle (non-fatal)
- 25 new tests: `TestCreateSkillVariant`, `TestGetSkillVariants`, `TestSelectVariantForTask` (deterministic/covers-both/multiple-variants), `TestRecordVariantOutcome` (wins/losses/noop), `TestRetireLosingVariants` (promote/retire/insufficient-data/dry-run/no-variants/missing-parent/tie), `TestSkillVariantFields` (roundtrip/legacy)

## [1.10.24] - 2026-04-05

Events as first-class graph nodes (@_overment steal). 2892 tests passing.

### Added — Typed event graph nodes (`src/interrupt.py`, `src/step_exec.py`, `src/heartbeat.py`)
- `TypedEvent` dataclass: `id`, `kind`, `payload`, `source`, `timestamp` — first-class event with `to_dict()`/`from_dict()` serialization
- `EventRouter` class: thread-safe in-memory event bus with `post(kind, payload, source)` and `wait_for(kind, timeout=300.0)` (blocks until matching event arrives or timeout); events consumed at most once per kind (first-come first-served); ring buffer capped at 200 events, stale events (>1h) ignored; `pending_count(kind)` and `recent_events(kind)` for inspection
- `_ROUTER` module-level singleton; `get_event_router()` accessor; `post_typed_event(kind, payload, source)` convenience function
- `await:<kind>` step shorthand in `execute_step`: steps matching `^await(?:_event)?:<kind>(\[timeout=Ns\])?` are intercepted before any LLM call; blocks on `EventRouter.wait_for(kind, timeout)`; returns `done` with event payload as result (0 LLM tokens), or `blocked` on timeout; optional `[timeout=Ns]` override (default 300s)
- `post_heartbeat_event()` now also fires typed events — Telegram/Slack messages routed through heartbeat automatically unblock any `await:telegram` or `await:slack` DAG step
- 26 new tests: `TestTypedEvent`, `TestEventRouterBasic` (concurrent waiters, post-before/after-wait, consume-once), `TestGlobalRouter`, `TestAwaitEventInExecuteStep` (done/blocked/timeout-param/case-insensitive), `TestHeartbeatFiresTypedEvent`

## [1.10.23] - 2026-04-05

Replay-based fitness oracle (FunSearch steal). 2866 tests passing.

### Added — Replay-based fitness oracle (`src/strategy_evaluator.py`)
- `SimilarOutcome` dataclass: past outcome match with `outcome_id`, `goal`, `status`, `similarity`, `weight`, `summary`
- `StrategyFitnessReport` dataclass: full evaluation result — `fitness_score`, `confidence`, `verdict` (PASS/FAIL/UNCERTAIN), `similar_outcomes`, `done_count`, `stuck_count`, `above_threshold`, `notes`
- `_tokenize(text)`: stop-word-filtered alphanumeric tokenizer (no deps)
- `_tfidf_cosine(query_terms, docs)`: TF-IDF cosine similarity engine (pure stdlib)
- `evaluate_strategy(strategy, *, outcomes, max_outcomes, top_k, pass_threshold, fail_threshold)`: TF-IDF ranks past outcomes by similarity to strategy text; computes similarity-weighted pass-rate as `fitness_score`; confidence scales with count and quality of similar outcomes; no LLM in the eval path
- `evaluate_skill(skill)`: convenience wrapper — builds strategy text from skill description + trigger_patterns + steps_template
- `evaluate_suggestion(suggestion)`: convenience wrapper — uses `suggestion.suggestion` text
- CLI entry point: `python strategy_evaluator.py "strategy text"` prints report + top-5 similar outcomes
- 35 new tests: `TestTokenize`, `TestTfidfCosine`, `TestEvaluateStrategyNoOutcomes`, `TestEvaluateStrategyWithOutcomes`, `TestEvaluateStrategyThresholds`, `TestEvaluateSkill`, `TestEvaluateSuggestion`, `TestStrategyFitnessReportSummary`

### Changed — Frontier rewrite pre-scoring (`src/evolver.py`)
- `run_skill_maintenance()` frontier rewrite loop now calls `evaluate_skill()` before rewriting a frontier skill; if verdict is PASS with confidence ≥ 0.3, rewrite is skipped (skill is already performing well historically); pre-score failure is non-fatal

## [1.10.22] - 2026-04-05

Subagent context firewall — TeamCreateTool version. 2831 tests passing.

### Added — Subagent context firewall for TeamCreateTool (`src/team.py`)
- `firewall_shared_ctx(task, shared_ctx, *, max_entries=5, max_chars_per_entry=200)`: relevance-ranks shared_ctx entries by word-overlap with task description (regex tokenizer, stops removed); returns top-ranked entries only. Prevents team workers from being overwhelmed by irrelevant parent context.
- Wired into `create_team_worker()`: replaces the naive `[-5:]` chronological slice; prompt label updated to "Relevant context from prior steps"
- 5 new tests: `TestFirewallSharedCtx` — relevance ranking, max_entries cap, value truncation, empty ctx, irrelevant entry exclusion

## [1.10.21] - 2026-04-05

Majority-vote pseudo-labels (Agent0 steal). 2826 tests passing.

### Added — Majority-vote lesson extraction (`src/memory.py`)
- `_jaccard_similarity(a, b)`: word-level Jaccard similarity for lesson dedup/agreement detection
- `majority_vote_lessons(all_samples, *, threshold=0.4)`: filters lesson lists to only include lessons agreed on by strict majority of k samples; returns top-3 deduplicated
- `extract_lessons_via_llm()` extended with `k_samples` kwarg (default: 1, backwards-compatible). When `k_samples ≥ 2`, draws k independent LLM samples at temperature 0.3, passes all to `majority_vote_lessons` — only agreed-on lessons enter the lesson store
- 7 new tests: `TestMajorityVoteLessons` — k=1 pass-through, empty samples, majority accepted, minority rejected, cap-at-3, k_samples multi-path, k_samples=1 unchanged

## [1.10.20] - 2026-04-05

Frontier task targeting (Agent0 steal). 2819 tests passing.

### Added — Frontier task targeting (`src/skills.py`, `src/evolver.py`)
- `FRONTIER_LOW = 0.40`, `FRONTIER_HIGH = 0.70`: define the frontier zone (skills challenging but not circuit-broken)
- `frontier_skills(skills=None, *, min_uses=3)`: returns skills with `FRONTIER_LOW ≤ utility_score ≤ FRONTIER_HIGH`, not circuit-open, sorted ascending by utility (hardest first)
- Wired into `run_evolver_with_friction()`: after open-circuit rewrites, rewrites up to 2 frontier skills per cycle (doesn't double-rewrite open-circuit skills); non-fatal
- 5 new tests: `TestFrontierSkills` — frontier zone filter, open-circuit exclusion, min_uses guard, sort order, disk-load path

## [1.10.19] - 2026-04-05

Skill validation harness (Voyager/Agent0 steal). 2814 tests passing.

### Added — Skill validation harness with repair loop (`src/skills.py`)
- `_SKILL_VALIDATION_SYSTEM`: LLM system prompt for skill quality evaluation (3 criteria: description clarity, step actionability, trigger relevance)
- `validate_skill_for_promotion(skill, adapter)`: LLM quality gate returning `{valid, reason, repair_hint}`; fail-open on error (returns valid=True if LLM unavailable)
- `maybe_auto_promote_skills(adapter=None, max_repair_attempts=3)`: replaces old version; with `adapter` — validates before each promotion, retries up to `max_repair_attempts` times via `evolver.rewrite_skill()`; skills failing all attempts stay provisional; without `adapter` — original behavior preserved (no validation overhead)
- 8 new tests: `TestSkillValidationHarness` covering pass/fail/error/repair-loop/max-attempts/threshold-guard

## [1.10.18] - 2026-04-05

Agent0 synthesis + Failure-Chain Lesson Recording. 2806 tests passing.

### Added — Agent0 research synthesis
- `docs/research/agent0-synthesis.md`: full synthesis of Agent0 (arxiv 2511.16043), FunSearch, EUREKA, Voyager research run. 3 laws of cross-paper convergence, Agent0 mechanism (two-agent co-evolution, R_unc frontier reward, GRPO optimizer), Poe gap analysis table, 5 prioritized recommendations.
- **Problem generation (Agent0)** backlog item marked DONE — research processed.

### Added — Failure-Chain Lesson Recording (`src/memory.py`, `src/agent_loop.py`) — Agent0 steal
- `Outcome.failure_chain: List[str]` — ordered list of failure/diagnosis/recovery strings stored per run
- `Outcome.recovery_steps: int` — count of retry/recovery actions
- `record_outcome()` and `reflect_and_record()` accept `failure_chain` and `recovery_steps` kwargs; persisted to `outcomes.jsonl`
- `_finalize_loop()` extended with same parameters
- `run_agent_loop()` accumulates `_failure_chain` list: one entry per retry (`blocked; retry N with hint`), split (`split into N parts`), and terminal stuck (`terminal: reason`); `_recovery_step_count` incremented on each recovery
- Every retry in the execution loop is now a training signal stored alongside the outcome — turns error paths into structured lessons

## [1.10.17] - 2026-04-05

Three-layer memory compression (724-office steal). 2806 tests passing.

### Added — Three-layer memory compression (`src/memory.py`)
- `CompressedBatch` dataclass: `batch_id`, `summary`, `task_types`, `outcome_ids`, `batch_size`, `oldest_at`, `newest_at`, `compressed_at`
- `compress_old_outcomes(threshold, batch_size, keep_recent, dry_run, adapter)`: when `outcomes.jsonl` exceeds `threshold`, compresses the oldest `batch_size` entries (respecting `keep_recent` floor) via LLM or heuristic fallback → saves to `compressed_outcomes.jsonl` → rewrites `outcomes.jsonl` without compressed entries
- `load_compressed_batches(limit)`: loads most-recent-first from `compressed_outcomes.jsonl`
- `_tfidf_rank_batches(query, batches)`: TF-IDF cosine similarity ranking of CompressedBatch objects by summary text (no deps)
- `load_outcomes_with_context(goal, limit, compressed_limit)`: three-layer retrieval — recent raw outcomes + top TF-IDF-ranked compressed batches + merged `context_text` string ready for prompt injection
- 11 new tests in `tests/test_memory.py`: `TestCompressOldOutcomes` (6 tests), `TestLoadCompressedBatches` (2 tests), `TestLoadOutcomesWithContext` (3 tests)

## [1.10.16] - 2026-04-05

Island model diversity (FunSearch steal). 2795 tests passing.

### Added — Island model for anti-monoculture skill diversity (FunSearch steal)
- `src/skills.py`: `Skill.island` field (persisted in JSON); `_ISLAND_KEYWORDS` keyword bank (research/build/analysis); `_ISLAND_DEFAULT = "general"`
- `assign_island(skill)`: classifies skill into research/build/analysis/general via keyword scoring on name + description + trigger_patterns — no LLM, no deps
- `ensure_island_assigned(skill)`: mutates `.island` in place if unset
- `get_skills_by_island(skills=None)`: groups skills by island (loads from disk if None); auto-assigns untagged skills
- `cull_island_bottom_half(island_name, *, min_island_size, dry_run)`: removes bottom half of **open-circuit** skills per island; respects min_island_size floor; dry_run mode returns IDs without deleting
- `run_island_cycle(*, min_island_size, dry_run, verbose)`: full cycle — assigns untagged skills, then culls open-circuit bottom half per island; returns `{assigned, culled, total_culled}`
- Wired into `run_evolver()`: island cycle runs on every evolver invocation (non-fatal, non-blocking)
- 11 new tests: `TestIslandModel` class covering classification, grouping, culling guards (min_island_size, open-circuit filter), dry_run

## [1.10.11] - 2026-04-05

Phase 48 (poe-mine/convo_miner) complete. Phase 29 research DONE. Phase 30 follow-on: per-model cost tracking + evolver cost scanning. Event-reactive heartbeat. Factory mode replay. 2710 tests passing.

### Added — Phase 48: Conversation Mining (poe-mine)
- `src/convo_miner.py`: `Idea` dataclass, `_score_line()` (keyword signals + domain gate + noise filter), `_extract_ideas_from_text()`, `scan_session_logs()` (JSONL session log scanning with `since=` filter), `scan_openclaw_docs()`, `scan_poe_memory()` (BACKLOG.md unchecked items → high-confidence ideas), `scan_git_log()`, `_deduplicate()` (Jaccard similarity, keeps highest confidence), `generate_report()` (markdown with tier sections + source breakdown)
- `inject_into_backlog()`: appends high-confidence mined ideas as `- [ ]` items to BACKLOG.md; 40-char substring dedup; caps at 20 injections per run
- CLI flags: `--inject-backlog`, `--inject-threshold FLOAT`; stderr reports injected count
- Registered as `poe-mine` entry point

### Added — Phase 30 follow-on: per-model cost tracking
- `src/memory.py`: `model: str = ""` field on `Outcome`; `record_outcome()` + `reflect_and_record()` accept `model=`; `estimate_cost()` is model-aware
- `src/agent_loop.py`: passes `model=getattr(adapter, "model_key", "")` to `reflect_and_record()`
- `src/metrics.py`: `ModelMetrics` dataclass; `by_model: Dict[str, ModelMetrics]` on `SystemMetrics`; `compute_metrics()` populates it; `format_metrics_report()` emits "By Model" section sorted by spend

### Added — Phase 30 follow-on: evolver cost scanning
- `src/evolver.py`: `scan_step_costs()` — pure statistical analysis of `step-costs.jsonl`; identifies step types with avg tokens >2× median; emits `cost_optimization` `Suggestion` entries recommending MODEL_CHEAP routing; `run_evolver(scan_costs=True)` wired

### Added — Event-reactive heartbeat (steal list item M)
- `src/heartbeat.py`: `_wakeup_event = threading.Event()`; replaces `time.sleep(interval)` with `_wakeup_event.wait(timeout=interval)` + `clear()`; `post_heartbeat_event(event_type, payload)` public API
- `src/interrupt.py`: `InterruptQueue.post()` calls `post_heartbeat_event()` after persisting — new interrupts immediately wake the heartbeat loop

### Added — Factory mode replay
- `src/observe.py`: `/api/replay-factory` POST endpoint — reads 10 recent outcomes, calls `scan_outcomes_for_signals()`, queues up to 3 suggested sub-missions via `handle()` in a daemon thread; returns 202 immediately
- Dashboard: "Factory Mode Replay" button + `replayFactory()` JS function

### Added — Phase 29: Human Psychology Research (DONE)
- `docs/research/tacit-vs-explicit.md`: Polanyi tacit dimension, Dreyfus 5-stage model, SECI model, Stage 4→5 transition mechanics, 8 design implications for crystallization pipeline
- `docs/research/enneagram-6w5-infj.md`: 6w5 communication patterns, INFJ failure modes, trust calibration mechanics, companion persona design principles

## [1.10.15] - 2026-04-05

Skill stemmer + pre-scoring discard gate. 2784 tests passing.

### Added — Lightweight skill stemmer (MetaClaw steal)
- `src/skills.py`: `_stem(token)` — suffix-stripping porter-style stemmer (no deps); strips "ing", "tion", "ed", "er", "ers", "es", "ly", "s", "ness", "ment" with 4-char root minimum
- Applied inside `_skill_tokens()` → TF-IDF skill retrieval now matches morphological variants ("researching" ↔ "research", "builder" ↔ "build")
- 8 new tests: `TestStemmer` class

### Added — Pre-scoring discard gate in rewrite_skill (FunSearch steal)
- `src/evolver.py:rewrite_skill()`: invalid LLM-generated rewrites are silently discarded before saving — empty steps/desc → discard; description >400 chars → discard; >10 steps → discard; empty triggers → inherit from existing skill. Prevents bad candidates from entering the skill gene pool.

## [1.10.14] - 2026-04-05

Channels architecture + dogfood research artifacts. 2776 tests passing.

### Added — `src/channels.py` (Agent-Reach steal: pluggable data channels)
- `ChannelResult` dataclass: `channel`, `query`, `items`, `error`, `truncated`; `to_text()` + `to_json()`
- `GitHubChannel`: `search_repositories()`, `search_code()`, `search_issues()` — GitHub REST API v3, no auth required
- `RedditChannel`: `posts()`, `search()` — Reddit public JSON API, no auth required
- `YouTubeChannel`: `transcript()` — uses youtube-transcript-api if installed, falls back to oEmbed metadata
- `fetch_channel(url_or_query)` — auto-dispatches to right channel based on URL pattern
- Agent-callable functions: `github_search()`, `reddit_posts()`, `reddit_search()`, `youtube_transcript()`
- `channels_health_check()` wired into `poe-doctor`
- 33 new tests

### Added — Research artifacts (dogfood runs 2026-04-05)
- `docs/research/nootropic-stack-verified.md`: 10-compound reference table with RCT evidence grades, doses, synergies, contraindications, verification notes. Produced by `verify:` prefix run (6/6 steps, 679k tokens, cross-reference pass applied).

## [1.10.13] - 2026-04-05

Polymarket CLI integration. 2743 tests passing.

### Added — `src/polymarket.py` (STEAL_LIST: Polymarket CLI integration)
- Wraps all `polymarket-cli` subcommands as Python functions: `polymarket_search()`, `polymarket_list()`, `polymarket_market()`, `polymarket_price()`, `polymarket_midpoint()`, `polymarket_history()`, `polymarket_trades()`
- Read-only; no wallet required; structured JSON output; error handling returns `{"error": ...}` objects (no exceptions to caller)
- `polymarket_health_check()` — availability probe; wired into `poe-doctor` as a new check
- 25 new tests

## [1.10.12] - 2026-04-05

FunSearch steal items + research docs. 2718 tests passing.

### Added — FunSearch-inspired evolver improvements (score-weighted mutation context)
- `src/evolver.py`: `_compactness_adjusted_score(skill)` — brevity-penalized utility: `utility_score / log(1 + char_count/200)`. Favors compact skills at selection time without modifying the EMA utility_score.
- `src/evolver.py`: `_top_peer_skills(failing_skill, k=2)` — returns up to k healthy peer skills ranked by compactness-adjusted score; excludes open-circuit skills and utility_score < 0.5. Used to build ranked-candidate mutation context.
- `src/evolver.py`: `rewrite_skill()` now includes peer context block in prompt — LLM sees top-K performers as version-tagged context ("v0 score=X: …, v1 score=Y: …") before generating rewrite. FunSearch pattern: informed recombination, not fresh generation.
- 9 new tests: `TestCompactnessAdjustedScore` (4) + `TestTopPeerSkills` (5)

### Added — Research docs
- `docs/research/funsearch-agent-design.md`: FunSearch/EUREKA/Voyager synthesis — 7 shared primitives, gap analysis vs Poe's existing modules, concrete design sketch for agent self-improvement via FunSearch loop. Critical gap documented: generator/evaluator separation.

## [1.10.10] - 2026-04-04

Three execution modes + decompose_to_dag. 2521 passed, 5 skipped.

### Added — Three execution modes (open-multi-agent steal)
- `team:` prefix: routes to AGENDA with `parallel_fan_out=4`, activating `_run_steps_dag` when the LLM emits `[after:N]` parallelism; defaults to `mid` model tier; stacks with `strict:`, `ralph:`, etc.
- `pipeline:` prefix: now actually implemented (was registered but never wired); parses goal as pipe-separated steps (`step1 | step2 | step3`), falls back to newline-split; bypasses LLM decomposition via new `preset_steps` parameter
- `run_agent_loop(preset_steps=...)`: optional pre-specified step list; when non-empty, skips `_decompose()` entirely; blank entries filtered
- `decompose_to_dag(goal, adapter, ...)` in `planner.py`: coordinator-as-agent API — calls `decompose()`, parses deps, builds execution levels; returns `(clean_steps, deps, levels, parallel_levels)` ready to feed into `_run_steps_dag()`
- 19 new tests in `tests/test_execution_modes.py`

## [1.10.9] - 2026-04-04

inject_steps step-shaper fix. 2506 tests, 5 skipped.

### Fixed — inject_steps bypass of step-shaper (Codex review)
- `src/agent_loop.py` — compound exec+analyze steps injected mid-run via `inject_steps` previously bypassed the pre-execution `_is_combined_exec_analyze` shaper, allowing bad step shapes into the live plan after the initial guard had fired
- Serial inject path (line ~1910): `_split_exec_analyze()` now applied to each injected step before prepending to `remaining_steps`; shaped parts logged
- Parallel-batch inject path: same treatment applied to `_batch_injected` before capping at 6 and inserting into plan
- No new tests needed — existing step-shape tests cover the shaper; behavior change is in injection paths

## [1.10.8] - 2026-04-04

Dep-aware parallel execution pool. 2506 tests, 5 skipped.

### Added — `_run_steps_dag()` in agent_loop.py (open-multi-agent steal)
- Semaphore-gated pool where tasks start as soon as their specific deps complete — auto-unblock replaces level-based "wait for whole level" scheduling
- Completed dep results passed as `completed_context` to downstream steps: "Synthesize [after:1,2]" receives actual outputs of steps 1 and 2 (not empty context)
- Diamond, pipeline, and mixed DAG topologies handled correctly; blocked/errored deps still unblock downstream (resilient)
- Wired as preferred execution path when `_parallel_levels` is non-empty (explicit `[after:N]` parallelism detected by `parse_dependencies` + `build_execution_levels`)
- Falls back to heuristic fan-out (legacy) for plans without explicit dep tags; serial loop unchanged for sequential plans
- 12 new tests in `tests/test_dag_executor.py`: ordering guarantees, dep context injection, blocked-dep resilience, concurrency limiting, round-trip with `parse_dependencies`

## [1.10.7] - 2026-04-04

Harness self-optimization loop (Meta-Harness steal). 2494 tests, 5 skipped.

### Added — Harness optimizer (`src/harness_optimizer.py`)
- `HarnessProposal` dataclass: target, original_clause, proposed_change, failure_pattern, confidence
- `HarnessOptimizerReport` dataclass: run_id, target_analyzed, traces_reviewed, proposals, elapsed_ms, skipped, skip_reason, with `summary()` method
- `_load_harness_text(target)`: imports EXECUTE_SYSTEM from step_exec or DECOMPOSE_SYSTEM from planner at runtime
- `_hash_prompt(text)` + `_record_candidate(target, text)`: tracks prompt version history in `memory/harness_candidates.jsonl` so the proposer can see what has been tried before
- `load_candidates_history(target)`: loads all recorded versions for a target (public — usable by other analysis tools)
- `_load_stuck_traces(limit=10)`: reads `memory/step_traces.jsonl`, filters to traces with at least one stuck step, returns most-recent-first
- `_format_trace_for_prompt(trace, max_steps=6)`: compact `[STUCK]`/`[done]` format with stuck_reason included
- `_HARNESS_OPTIMIZER_SYSTEM`: anti-sycophancy system prompt — concrete word-level proposals only; `{"proposals": []}` if prompt is fine
- `_llm_analyze_harness(harness_texts, stuck_traces, dry_run)`: builds multi-section user message (current prompts + stuck traces), calls MODEL_MID, parses JSON proposals
- `_save_harness_proposals(proposals, run_id)`: creates evolver `Suggestion` objects with `category="prompt_tweak"`, saves via `_save_suggestions()`
- `run_harness_optimizer(targets, max_traces, min_stuck_traces, dry_run, verbose)`: main entry point; skips gracefully if no harness text loadable or insufficient stuck traces
- CLI: `python3 harness_optimizer.py [--dry-run] [--targets ...] [--min-traces N] [--max-traces N] [-v]`; also registered as `poe-harness-optimizer`
- 25 tests in `tests/test_harness_optimizer.py`

### Changed — Heartbeat wires harness optimizer
- `src/heartbeat.py` — added `_harness_optimizer_active` flag + `_harness_optimizer_lock`; `_run_harness_optimizer_bg()` daemon-thread wrapper; heartbeat_loop fires optimizer every `evolver_every * 5` ticks (default ~50 heartbeats ≈ 50 min) when no mission is active

## [1.10.0] - 2026-04-04

Session 10: GStack Tier 1 steals (decision taxonomy + confidence gates + anti-sycophancy + calibration). Heartbeat backgrounding (daemon threads). Depth-gated context firewall. Mutable task graph via inject_steps. Magic prefix registry refactor. 2425 tests, 5 skipped.

### Added — GStack Tier 1: Decision taxonomy + confidence-gated escalation
- `src/director.py` — `_ESCALATION_SYSTEM` extended with Mechanical/Taste/User Challenge taxonomy; anti-sycophancy rules injected directly into prompt
- `EscalationDecision` dataclass: added `decision_class: str = "mechanical"` and `confidence: int = 5`
- `handle_escalation()`: extracts `decision_class` + `confidence` from LLM JSON; `user_challenge` → force surface; `confidence < 5` → force surface with `[Low confidence (N/10)]` in summary; `confidence 5–6` → prepend `[Confidence N/10]` caveat; appends entry to `memory/calibration.jsonl`
- 6 new tests: taxonomy override, low-confidence override, medium-confidence caveat, calibration log written, decision_class/confidence in result, invalid action defaults to surface

### Added — Heartbeat backgrounding
- `src/heartbeat.py` — evolver, inspector, and nightly eval each moved to daemon threads; module-level active flags (`_evolver_active`, `_inspector_active`, `_eval_active`) with companion `RLock` double-checked locking; same pattern as backlog drain
- `_run_evolver_bg()`, `_run_inspector_bg()`, `_run_eval_bg()` — clear flag in finally block; exceptions logged, never propagated
- Heartbeat tick no longer blocks on slow evolver or inspector runs
- 11 new tests: happy path + exception swallowed for each bg function; double-start prevention for evolver and inspector

### Added — Context firewall (depth-gated)
- `src/handle.py` — `_context_firewall(reason, depth, cap=600)`: depth ≤ 1 → cap to 600 chars; depth ≥ 2 → strip accumulated history, keep only "Original goal:" line + "Remaining:" block; falls back to cap truncation on unstructured input
- Wired into `handle_task()` continuation path, replacing bare `_cont_ctx[:600]`
- 5 new tests: shallow pass-through, deep extraction, "Accomplished" stripped, fallback cap, always within cap

### Added — Mutable task graph (inject_steps)
- `src/step_exec.py` — `inject_steps` optional array field added to `complete_step` tool schema (max 3 per step); extracted and capped after parsing tool call response
- `src/agent_loop.py` — serial done path: injected steps prepended to `remaining_steps`; parallel batch: `_batch_injected` collected from all batch members, capped at 6, prepended after batch completes; `StepOutcome.injected_steps` field added
- Workers can now add discovery-driven steps mid-execution without pre-planning them
- 2 new tests: inject_steps inserted into plan, inject_steps capped at three

### Changed — Magic prefix registry
- `src/handle.py` — replaced 9 scattered `startswith()` chains with `_PrefixRule` dataclass + `_PREFIX_REGISTRY` list + `_apply_prefixes()` loop; supports stacking multiple prefixes, case-insensitive matching, model tier precedence; `verify:` aliased to `ralph_mode`; `ultraplan:` sets both model_tier=power and max_steps=12
- `_PrefixResult` dataclass carries all parsed flags cleanly into `handle()`
- 11 new tests: single prefix, stacking, effort model tier, ultraplan max_steps, verify=ralph alias, case-insensitive, registry completeness check

## [1.10.6] - 2026-04-04

Environment snapshot caching + Skill steering field. 2469 tests, 5 skipped.

### Changed — Environment snapshot caching (Meta-Harness steal)
- `src/agent_loop.py` — after each successful serial step, write compressed step summary (or first 200 chars of result) to `_loop_shared_ctx["step:N:{text[:40]}"]`; team workers spawned in later steps inherit these snapshots through `shared_ctx` injection, eliminating redundant re-fetches

## [1.10.5] - 2026-04-04

Skill text as steering (Meta-Harness steal). 2469 tests, 5 skipped.

### Added — optimization_objective field on Skill (Meta-Harness steal)
- `src/skills.py` — `optimization_objective: str = ""` added to Skill dataclass; wired into `_skill_to_dict()`, `_dict_to_skill()` (backward-compatible via `.get()`), `format_skills_for_prompt()` (shown when non-empty as "Optimize for: ..."), `compute_skill_hash()` (field included in hash so mutations are detected)
- `src/skill_loader.py` — `export_skill_as_markdown()` writes `optimization_objective: "..."` in YAML frontmatter when non-empty
- 8 new tests: default empty, round-trips through dict, missing-key default, prompt includes/excludes, hash changes, export includes/omits

## [1.10.4] - 2026-04-04

Auto-resume on rate limits — multi-cycle polling retry. 2461 tests, 5 skipped.

### Changed — Rate limit multi-cycle polling retry
- `src/llm.py` — adds `import logging` + `log = logging.getLogger("poe.llm")`; replaces single-retry rate-limit handler in `ClaudeSubprocessAdapter.complete()` with a configurable multi-cycle polling loop: up to `_rate_limit_max_retries` (default 6) attempts, exponential backoff starting from `_rate_limit_wait` (default 60s), capped at 1800s per cycle; stops early on non-rate-limit errors; backoff resets to 60s on success; persists to `self._rate_limit_wait` for next call
- 5 new tests: succeeds on second attempt, retries up to max, backoff grows exponentially, non-rate-limit error stops retry, wait resets on success

## [1.10.3] - 2026-04-04

Meta-Harness steal: proposer reads full execution traces. 2456 tests, 5 skipped.

### Added — Full execution trace storage + evolver enrichment
- `src/memory.py` — `record_step_trace(outcome_id, goal, step_outcomes, task_type)`: persists per-step detail (step text, status, result, summary, stuck_reason) to `memory/step_traces.jsonl`; `load_step_traces(outcome_ids)` returns matching traces by id
- `src/agent_loop.py` — captures `reflect_and_record()` return value; calls `record_step_trace()` for every non-dry-run loop after recording the outcome
- `src/evolver.py` — `_build_outcomes_summary()` extended: for stuck outcomes, calls `load_step_traces()` and appends per-step trace blocks (up to 8 steps, 5 stuck outcomes); load_traces exceptions silently ignored
- 9 new tests in `tests/test_memory.py`: trace written, stuck_reason included, done step no stuck_reason key, result truncated, empty steps, missing file, loads matching id, filters to requested ids, malformed lines skipped
- 4 new tests in `tests/test_evolver.py`: trace enrichment with/without traces, done outcomes don't trigger fetch, exception safety

## [1.10.2] - 2026-04-04

GStack Tier 2 calibration review loop. 2443 tests, 5 skipped.

### Added — Calibration review loop (GStack Tier 2)
- `src/evolver.py` — `CalibrationFinding` dataclass (decision_class, entry_count, override_count, override_rate, mean_confidence, suggestion)
- `scan_calibration_log(cal_path, min_entries, high_override_threshold, low_confidence_threshold)` — reads `memory/calibration.jsonl`, groups by decision_class, flags override_rate > 40% or mean_confidence < 6/10 as actionable findings
- `run_evolver()` — `scan_calibration: bool = True` parameter; calibration findings become `prompt_tweak` / `escalation` suggestions in the evolver report
- 10 new tests in `tests/test_evolver.py`: empty file, missing file, insufficient entries, high override rate, no override, low confidence, multiple classes, malformed lines, wired into run_evolver, scan_calibration=False skip

## [1.10.1] - 2026-04-04

Team-level SharedMemory (open-multi-agent steal). 2433 tests, 5 skipped.

### Added — Team-level SharedMemory
- `src/agent_loop.py` — `_loop_shared_ctx: Dict[str, Any] = {}` initialized before fan-out block; passed to both `_run_steps_parallel` call sites (fan-out and parallel batch) and to serial `_execute_step`
- `src/step_exec.py` — `shared_ctx` parameter added to `execute_step()`; passed to `create_team_worker`; successful worker results written back keyed by `"{role}:{task[:40]}"`
- `src/team.py` — `shared_ctx` parameter added to `create_team_worker()`; non-empty dict injects "Shared context from prior team workers" block (last 5 entries, each capped at 200 chars) into worker user message before the task
- `_run_steps_parallel()` — `shared_ctx` parameter added; threaded into each `_run_one` closure
- 8 new tests in `tests/test_shared_ctx.py`: shared_ctx injection, empty/None no-op, last-5-entries cap, writeback on success, no-writeback on blocked, None-safe execute_step

## [1.9.1] - 2026-04-03

Codex review feedback: fix subprocess timeout handling. Three bugs found via adversarial repo review run (step 9/13 "run pytest and analyze" timing out at 300s and retrying uselessly).

### Fixed — CodexAdapter ignores timeout kwarg
- `src/llm.py` `CodexAdapter.complete()`: add `timeout: Optional[int] = None` parameter; use `_timeout = timeout or self.timeout` instead of always using `self.timeout`. Previously, step_exec's `_step_timeout=600` for long-running steps had no effect on Codex runs — now it does.

### Fixed — Retry on subprocess timeout burns time with no progress
- `src/agent_loop.py` `_handle_blocked_step()`: detect `"timed out"` in `stuck_reason` and return `retry=False` immediately, regardless of `prior_retries`. Retrying an identical timed-out step just burns another timeout window. The stuck_reason now includes a split hint for the recovery planner: "split this step into (1) run command + save output, (2) read file and analyze."
- Added 5 tests covering: both adapter timeout messages, network timeout still retries, split hint in stuck_reason.

### Changed — Long-running step timeout bumped
- `src/step_exec.py`: 600s → 900s for long-running steps (pytest on large suites + LLM analysis needs headroom). Added `cargo` and `mvn` to long-running keywords.

## [1.9.0] - 2026-04-03

Session 9: Pi steal — remaining NEXT items. Runtime tool extension, human-readable session export, session branching. 47 new tests (2329 total, 0 failures, 1 pre-existing flaky).

### Added — Runtime Tool Extension (Pi self-extending agent pattern)
- `src/runtime_tools.py` — `RuntimeTool` dataclass (name, description, bash_template, parameters); `_RuntimeToolStore` with lazy disk load + auto-register into `tool_registry` singleton; `register_runtime_tool()`, `dispatch_runtime_tool()`, `list_runtime_tools()`, `clear_runtime_tools()`. Persists to `memory/runtime_tools.json` across sessions.
- `register_tool` added to `EXECUTE_TOOLS` (WORKER role only) — agent provides name, description, bash_template, optional parameters_json; handler in step_exec.py registers the tool and injects it into `_active_tools` immediately.
- `dispatch_runtime_tool()` called in the `else` branch of step_exec tool dispatch — unknown tool names now check runtime registry before blocking.
- `src/agent_loop.py` — `_active_tools` replaced with `_resolve_tools()` closure re-queried per step; newly registered tools appear in subsequent steps without restarting.
- `tool_registry.py` — `register_tool` added to `_ROLE_MAP` as WORKER-only.
- `tests/test_runtime_tools.py` — 20 tests: RuntimeTool unit, register/dispatch, persistence round-trip, global registry integration.

### Added — Human-Readable Session Export
- `src/checkpoint.py` — `export_human(loop_id) -> Optional[str]`: renders checkpoint as markdown with goal, loop_id, progress summary, per-step sections (icon, status, truncated result). Returns None if checkpoint not found.
- `poe-checkpoint export <loop_id>` CLI subcommand; `-o FILE` flag writes to file instead of stdout.

### Added — Session Branching
- `src/checkpoint.py` — `parent_loop_id: str` field on `Checkpoint` dataclass; included in `to_dict()` only when set; `from_dict()` handles missing key. `branch_checkpoint(loop_id) -> Optional[str]`: copies checkpoint with new loop_id + parent tracked; returns new loop_id.
- `poe-checkpoint branch <loop_id>` CLI subcommand; prints new loop_id and resume command.
- `tests/test_checkpoint_extended.py` — 27 tests: parent_loop_id field, export_human (content, truncation, markdown structure, missing), branch_checkpoint (independence, parent tracking, chain), CLI integration.

## [1.8.0] - 2026-04-03

Session 8: Pi coding agent synthesis + steal execution. System prompt token audit (1892→936 tokens, -51%). Architecture non-goals doc. STEAL_LIST.md updated with Pi items. BACKLOG.md updated.

### Changed — System Prompt Token Audit (Pi steal)
- `src/step_exec.py` — `EXECUTE_SYSTEM` trimmed from ~844 to ~333 tokens (-61%). Cuts: removed editorial commentary ("your output is consumed by downstream agents"), deduplicated negatives (URL policy already implied "don't fetch"), removed rules 1 and 5 from TOKEN EFFICIENCY (covered by other sections), cut the polymarket example from DATA PIPELINE STRATEGY (the 3 steps are sufficient). All behavior-changing content preserved.
- `src/planner.py` — `DECOMPOSE_SYSTEM` trimmed from ~1048 to ~603 tokens (-42%). Cuts: reduced 3 BAD/GOOD pairs in STEP GRANULARITY to 2 (removed the clone/review example, covered by the setup rule), merged CODE REVIEW STEPS into the granularity section, removed the second BAD/GOOD pair in LONG-RUNNING COMMANDS (build is same pattern as test), shortened the PARALLEL EXECUTION example from 7 steps to 4. All rules preserved.
- Combined system prompt cost: 1892 → 936 tokens (-51%). Pi's target was <1k combined.

### Added — Architecture Non-Goals
- `docs/ARCHITECTURE_NON_GOALS.md` — Documents 8 deliberate non-goals: tool minimalism (Poe is an orchestrator, not a coding REPL), MCP-as-default, interactive approval gates, hidden sub-agents, full Neo4j, plugin marketplace, provider portability contracts, headless UI. Each entry has rationale and revisit conditions. Prevents scope creep during planning discussions.

### Changed — Steal List / Backlog
- `STEAL_LIST.md` — Added Pi coding agent section with 5 steal candidates (2 DONE, 2 TODO, 1 LATER). Sources section updated.
- `BACKLOG.md` — Token audit and architecture non-goals marked complete under Token Efficiency.

## [1.7.0] - 2026-04-02

Session 7: LLM parse robustness overhaul, bughunter anti-pattern detectors, Phase 41 implementation (tool registry, curated skill loader, step event model, tool search, deferred tools), magic keyword prefixes, doctor Phase 41 checks, skill auto-export. 139 new tests (2282 total, 0 failures).

### Added — LLM Parse Robustness
- `src/llm_parse.py` — `extract_json()` (depth-counter bracket matching, markdown fence stripping, type validation, list-unwrapping), `safe_float()` (None/NaN/Inf/non-numeric guards), `safe_str()`, `safe_list()`, `content_or_empty()`, `strip_markdown_fences()`, `_find_json_bounds()`
- `tests/test_llm_parse_robustness.py` — 90 tests covering all failure modes: markdown-fenced JSON, None content, malformed JSON, truncated responses, type mismatches, wrapped lists, refusal messages, nested braces in string fields
- Wired `llm_parse` into 17 modules replacing `rfind + json.loads` pattern: `director.py`, `step_exec.py`, `memory.py`, `intent.py`, `evolver.py`, `quality_gate.py`, `mission.py`, `attribution.py`, `inspector.py`, `planner.py`, `skills.py`, `sprint_contract.py`, `verification_agent.py`, `factory_thin.py`, `interrupt.py`, `cross_ref.py`, `thinkback.py`

### Added — Bughunter Anti-Pattern Detectors
- `src/bughunter.py` — BH011 (`json.loads(content[`/`raw[` rfind-slice pattern), BH012 (`float(data/raw/parsed/r/result.get(...)` float-on-LLM-dict pattern)
- `_scan_llm_parse_patterns()` — regex-based static detector wired into `scan_file()`; skips `llm_parse.py` itself and JSONL-reading patterns to avoid false positives

### Added — Phase 41: Tool Registry (step 1-2, completed previous session)
- `src/tool_registry.py` — `ToolDefinition`, `PermissionContext` (glob deny patterns), `ToolRegistry` (role→deny→is_enabled→sort pipeline), module-level `registry` singleton, `worker_context()`/`short_context()`/`inspector_context()`/`director_context()` factories
- Role constants: `ROLE_WORKER`, `ROLE_SHORT`, `ROLE_INSPECTOR`, `ROLE_DIRECTOR`, `ROLE_VERIFIER`
- `src/step_exec.py` — `get_tools_for_role(role, deny_patterns=None)` using registry; backward-compat lists retained
- `src/agent_loop.py` — `permission_context` param on `run_agent_loop()`; `_active_tools` resolved at composition time from `PermissionContext`
- `tests/test_tool_registry.py` — 45 tests

### Added — Phase 41: Curated Skill Loader (step 3-4)
- `src/skill_loader.py` — `SkillSummary` dataclass, `SkillLoader` class: `load_summaries(role)`, `find_matching(goal, role)`, `load_full(name)`, `get_summaries_block(role, goal)`; `_parse_frontmatter()` for YAML-ish frontmatter; module-level `skill_loader` singleton
- `skills/` directory — 4 seed SKILL.md files with YAML frontmatter: `web_research`, `code_implement`, `debug_investigate`, `data_analysis`
- Progressive disclosure: summaries (name + description + triggers) injected into decompose prompt; full body loaded on demand via `load_full()`
- Wired `skill_loader.get_summaries_block()` into `agent_loop._build_loop_context()` alongside runtime skills; merged into `skills_context` before decompose
- `_build_loop_context()` now accepts `permission_context=None` and forwards role to skill loader
- `tests/test_skill_loader.py` — 42 tests

### Added — Phase 41: Step Event Model (step 5)
- `src/step_events.py` — `PreStepEvent`, `PostStepEvent`, `StepVeto`, `StepVetoedError`; `StepEventBus` with `@on_pre_step(match=)`, `@on_post_step(match=)`, `register_pre()`, `register_post()`, `unregister()`, `clear()`, `fire_pre()`, `fire_post()`, `list_handlers()`; module-level `step_event_bus` singleton
- Glob matcher on step_text — handlers fire only for matching steps (e.g. `match="create_*"`)
- Blocking semantics: `fire_pre()` returns `StepVeto` to veto execution; non-blocking: `fire_post()` swallows all handler exceptions
- Wired into `step_exec.execute_step()`: `fire_pre` fires after constraint check; `fire_post` fires before final return with elapsed_ms and result
- Refactored `execute_step` outcome paths to collect `_outcome` dict + single return (cleaner, enables post-fire)
- `tests/test_step_events.py` — 35 tests

### Added — Doctor Phase 41 checks
- `src/doctor.py` — Added Phase 41 checks: tool registry (expected tools registered), curated skills (SKILL.md count), step event bus (handler count), bughunter scan result
- Added `--json` flag stub to CLI (`argparse`-based)
- 10 tests in `tests/test_doctor.py`

### Added — Magic keyword prefixes
- `handle.py` — `ralph:` and `verify:` prefixes enable per-step Ralph verify loop (alias for `ralph_verify=True`)
- `handle.py` — `pipeline:` prefix marks goal as data-heavy (future: injects pipeline enforcement mode flag)
- `handle.py` — `strict:` prefix enables thorough quality passes: council + cross-reference checks (wires `run_council=True, run_cross_ref=True` into `run_quality_gate`)
- 8 new tests in `tests/test_handle.py`

### Added — Phase 41: Tool Search / Deferred Tool Resolution (step 6)
- `src/tool_search.py` — `resolve_deferred_tools(query, ctx, registry)`: glob/substring/description matching, returns full schemas for deferred tools; `format_tool_search_result()`: human-readable schema block for LLM injection; `inject_tool_search_if_needed()`: adds `tool_search` schema to tool list when deferred stubs present; `TOOL_SEARCH_SCHEMA`: always-full tool definition
- Wired into `step_exec.execute_step()`: deferred tool detection, `inject_tool_search_if_needed` applied before LLM call, `tool_search` tool call handling with schema resolution and LLM re-call with expanded tool list
- 26 tests in `tests/test_tool_search.py`

### Added — Skill Auto-Export (Hermes steal)
- `src/skill_loader.py` — `export_skill_as_markdown(skill, skills_dir, overwrite)`: converts a runtime `Skill` (from skills.jsonl) to SKILL.md in `skills/`; `_slugify()` for safe filenames; invalidates `skill_loader` cache on write
- `src/skills.py` — `maybe_auto_promote_skills()` now calls `export_skill_as_markdown()` after each promotion; newly established skills become available to `SkillLoader` immediately
- 18 new tests in `tests/test_skill_loader.py` (export + slugify)

### Changed
- `BACKLOG.md` — lat.md and promotion cycle marked done; last reviewed updated

## [1.6.0] - 2026-04-01

Session 6: Knowledge graph (lat.md), promotion cycle + decision journal, Polymarket claim validation, Phase 41 architecture design. 25 new tests (2013 total, 0 failures).

### Added — Phase 55: lat.md Knowledge Graph
- `lat.md/` directory — 9 concept files cross-linked via `[[wiki links]]`: `core-loop`, `memory-system`, `self-improvement`, `worker-agents`, `quality-gates`, `poe-identity`, `checkpointing`, `intent-classification`, `constraint-system`
- `lat.md/lat.md` — index of all concept nodes
- `# @lat: [[node#Section]]` backlinks added to key source modules: `agent_loop.py`, `planner.py`, `checkpoint.py`, `memory.py`, `worker-agents.md`
- `lat check` passes clean (0 broken links)

### Added — Phase 56: Promotion Cycle — Standing Rules + Decision Journal
- `StandingRule` dataclass — `rule_id`, `rule`, `domain`, `confirmations`, `contradictions`, `promoted_at`, `source_lesson_id` (JSONL: `memory/standing_rules.jsonl`)
- `Hypothesis` dataclass — pre-promotion rule candidate (JSONL: `memory/hypotheses.jsonl`)
- `observe_pattern(lesson, domain, source_lesson_id)` — create/increment hypothesis; auto-promotes at `RULE_PROMOTE_CONFIRMATIONS=2`; returns `StandingRule` on promotion
- `contradict_pattern(lesson, domain)` — demotes hypothesis if `contradictions > confirmations`; increments rule contradiction count
- `inject_standing_rules(domain)` — formatted rules block injected into every decompose call (unconditional)
- `Decision` dataclass — `decision_id`, `decision`, `rationale`, `domain`, `alternatives`, `trade_offs`
- `record_decision(decision, rationale, domain, ...)` — writes to `memory/decisions.jsonl`
- `search_decisions(query, domain, limit)` — TF-IDF ranking; returns relevant prior decisions
- `inject_decisions(goal, domain)` — formatted prior decisions block for decompose injection
- Wired into `agent_loop.py` — standing rules + decisions injected into every `_build_decompose_context()` call
- `memory_status()` updated to report standing rule count
- `tests/test_promotion_cycle.py` — 25 tests

### Research / Documentation
- `research/POLYMARKET_BTC_LAG_VALIDATION.md` — @slash1sol BTC lag claim validated as UNCONFIRMED. Binary YES/NO contracts (not continuous price feed), 4% round-trip fees vs 0.3% claimed edge = −13x EV. No build warranted.
- `research/PHASE41_TOOL_REGISTRY_DESIGN.md` — Phase 41 implementation design from Claude Code architecture analysis. 8 sections: tool registry, role-gated visibility, progressive skill disclosure, hook lifecycle, function calling schema. Implementation order documented.
- `research/X_LINKS_SYNTHESIS.md` — ingested 5 X/Twitter posts via Jina-based orchestration loop; ranked steal candidates; lat.md + promotion cycle selected for immediate build
- STEAL_LIST.md — X links research batch added; lat.md and promotion cycle marked DONE; Phase 41 and Polymarket claim documented
- ROADMAP.md — Phases 55 and 56 added with full spec entries
- CLAUDE.md — current state table updated through Phase 56

---

## [1.5.0] - 2026-04-01

Session 5: Persistent identity block (GAP 1), session checkpointing/resume (GAP 3). 42 new tests (1988 total, 0 failures).

### Added
- `src/poe_self.py` — `load_poe_identity()`, `with_poe_identity()` — identity block injected into every decompose call (Phase 53)
- `user/POE_IDENTITY.md` — durable, user-editable Poe identity file; used as source of truth for `poe_self`
- `src/checkpoint.py` — `write_checkpoint()`, `load_checkpoint()`, `resume_from()`, `delete_checkpoint()`, `list_checkpoints()` (Phase 54)
- `poe-checkpoint` CLI — list/show/delete saved checkpoints
- `run_agent_loop(resume_from_loop_id=...)` — checkpoint resume parameter; skips already-completed steps
- Checkpoint written after each step; deleted on successful loop completion; retained for resume on stuck/partial
- `tests/test_poe_self.py` — 18 tests
- `tests/test_checkpoint.py` — 24 tests

### Changed
- `planner.py::decompose()` — identity block now prepended to `DECOMPOSE_SYSTEM` via `with_poe_identity()` on every call

---

## [1.4.0] - 2026-04-01

Session 4 (overnight, continued): Cross-reference fact verification. 39 new tests.

### Added — Phase 52: Cross-Reference Check (`src/cross_ref.py`)
- `ClaimVerification` dataclass: claim, category, status (confirmed/disputed/unknown), confidence, note, elapsed_ms
- `CrossRefReport` dataclass: verified list, disputes list, `has_disputes` property, `dispute_summary()`, `full_summary()`
- Two-stage pipeline: `extract_verifiable_claims(text, adapter)` → `verify_single_claim(claim, category, adapter)`
- Verification uses fresh LLM context per claim — verifier never sees the original response (prevents confirmation bias)
- `run_cross_ref(text, adapter, dry_run, max_claims, dispute_threshold)` — full pipeline, never raises
- `cross_ref_annotation(report)` — empty string when no disputes, safe to always append
- Wired into `run_quality_gate(run_cross_ref=True)` as Pass 2.5; disputes trigger ESCALATE + `QualityVerdict.cross_ref` field added
- `poe-cross-ref --text "..." [--file FILE] [--max-claims N] [--dispute-threshold N]` CLI

### Tests
- 39 tests: `tests/test_cross_ref.py` — claim extraction, verification, full pipeline, annotation, quality_gate integration

---

## [1.3.0] - 2026-04-01

Session 4 (overnight): Thinkback replay, unified passes pipeline, Hermes evaluation, factory experiment findings. 60 new tests.

### Added — Phase 50: Thinkback Replay (`src/thinkback.py`)
- `ThinkbackReport` dataclass: per-step `StepReview` (decision_quality: good/acceptable/poor, hindsight_note, counterfactual), overall_assessment, mission_efficiency (0.0–1.0), key_lessons, would_retry, retry_strategy
- `run_thinkback(loop_result)` — replays a LoopResult through hindsight LLM analysis; falls back to dry-run mode on adapter failure; never raises
- `run_thinkback_from_outcome(outcome_dict)` — works directly from outcomes.jsonl records (synthesizes steps from summary + lessons)
- `_save_thinkback_lessons()` — writes extracted lessons to `memory/lessons.jsonl` tagged `[thinkback:{run_id}]`
- `load_latest_outcome()` / `load_outcome_by_id(id)` — outcome loading helpers for the CLI
- `poe-thinkback --latest [--task-type TYPE] [--dry-run] [--save]` CLI

### Added — Phase 51: Passes — Unified Multi-Pass Review Pipeline (`src/passes.py`)
- `PassConfig` — configures which passes to run: quality_gate, adversarial, council, debate, thinkback
- Named presets: `quick` (quality_gate), `standard` (+adversarial), `thorough` (+council), `full` (+debate), `all` (+thinkback)
- `PassConfig.from_names(["council","debate"])` / `PassConfig.from_preset("thorough")` constructors
- `PassResult` — per-pass verdict/reason/escalate/elapsed_ms
- `PassReport` — aggregates all passes: escalate=True if any pass escalated, escalation_reason = first escalating pass reason
- `run_passes(goal, step_outcomes, config=..., preset=..., loop_result=...)` — chains passes, never raises
- Council/debate absorbed into quality_gate's internal passes when co-enabled; thinkback always standalone
- `poe-passes --goal "..." --passes council,debate [--latest-outcome] [--output FILE]` CLI

### Changed
- `pyproject.toml`: added `poe-thinkback` and `poe-passes` entry points

### Research / Documentation
- Hermes (NousResearch/hermes-agent) evaluation complete — keep OpenClaw + poe-orchestration. Steal candidates: Skill Document auto-extraction, persistent user modeling (Honcho-style), terminal persistence backends. See BACKLOG.md.
- Factory overnight experiment: factory_minimal hit subprocess timeout (300s) on complex research goal — confirms single-call architecture has hard ceiling. Phase 49 prerequisite: configurable timeout.
- PAI (danielmiessler/Personal_AI_Infrastructure) steal items documented in BACKLOG.md: TELOS-style structured context injection, hook-based lifecycle callbacks.
- ROADMAP.md: Phases 50 and 51 added.

### Tests
- 31 tests: `tests/test_thinkback.py` — StepReview, ThinkbackReport, dry-run, adapter, from_outcome, save_lessons
- 29 tests: `tests/test_passes.py` — PassConfig presets, PassResult, PassReport, run_passes integration
- All 65 source files pass bughunter scan (0 issues)

---

## [1.2.0] - 2026-03-31

Session 3: Verification, council, evolver signals, data pipeline enforcement, context compression, Skip-Director, dashboard, TeamCreateTool, multi-agent debate, factory mode. 450+ new tests (total ~1290+).

### Added — Adversarial Verification (`src/quality_gate.py`, `src/verification_agent.py`)
- `VerificationAgent` class: `verify_step()`, `adversarial_pass()`, `quality_review()` — first-class verification agent
- `run_llm_council()` — 3 critics (devil's advocate, domain skeptic, implementation critic) run in parallel; escalates if 2+ rate WEAK. `CouncilVerdict` + `CouncilCritique` dataclasses
- `run_debate()` — Bull/Bear/Risk Manager pattern; `DebatePosition` + `DebateVerdict` dataclasses; CAUTION+REJECT both escalate; wired as Pass 4 in `run_quality_gate(with_debate=True)`
- `poe-verify` CLI

### Added — Evolver Signal Scanning (`src/evolver.py`)
- `scan_outcomes_for_signals()` — scans done outcomes for actionable leads, converts to `sub_mission` Suggestion entries; wired into `run_evolver(scan_signals=True)`

### Added — Data Pipeline Enforcement (`src/agent_loop.py`)
- `_is_data_heavy_step()` — detects risky steps (fetch all, list all, polymarket-cli, etc.) and injects `DATA PIPELINE ENFORCEMENT` block
- `_result_looks_like_raw_dump()` — post-checks results (>2000 chars + high brace density) and prepends `[RAW_OUTPUT_DETECTED]`

### Added — Skip-Director Experiment (`src/director.py`, `src/handle.py`)
- `_is_simple_directive()` classifier (≤15 words, no complex keywords)
- `skip_if_simple=True` in `run_director()` routes simple goals directly to `run_agent_loop`
- `direct:` prefix in `handle.py` forces AGENDA lane + skips quality gate + escalation overhead
- `skip_if_simple=True` wired into `telegram_listener.py`

### Added — Dashboard as Real Tool (`src/observe.py`)
- Cost panel: 24h spend + per-model breakdown from `step-costs.jsonl`
- Mission Ancestry Tree: scans all workspace projects for `ancestry.json` files
- Replay button: POST /api/replay re-runs last outcome's goal in background thread
- External binding: `0.0.0.0` (was 127.0.0.1) — reachable on LAN

### Added — TeamCreateTool Pattern (`src/team.py`)
- `create_team_worker(role, task)` — spins up specialist with custom persona
- 8 known roles: market-analyst, risk-auditor, fact-checker, data-extractor, devil-advocate, synthesizer, strategist, domain-skeptic
- `create_team_worker` tool in `EXECUTE_TOOLS_WORKER` (not SHORT/INSPECTOR)

### Added — Phase 46: Intervention Graduation (`src/graduation.py`)
- Scans diagnoses for repeated failure classes (≥3x), proposes permanent rules as high-confidence suggestions
- 8 failure classes covered with verify_pattern shell commands
- `poe-graduation [--verify]` CLI

### Added — Other
- Completed context compression: older entries → one-liner after step 5; 47-63% reduction at 7-12 steps
- Confidence tagging: `confidence` field in `StepOutcome`, `complete_step` tool schema, `completed_context` entries
- Clarification milestone: `check_goal_clarity()` in `intent.py`; skippable with `yolo: true` in `user/CONFIG.md`
- User-level config: `user/CONFIG.md` — default_model_tier, yolo, always_skeptic, notify_on_complete
- Cron persistence: `src/scheduler.py` with `JobStore` backed by `memory/jobs.json`; `poe-schedule` CLI
- `schedule_run` tool in `EXECUTE_TOOLS` — agents can schedule their own future runs
- `effort:low/mid/high` prefix in `handle.py` — overrides model tier
- `ultraplan:` prefix — sets model=power, max_steps=12
- `mode:thin` prefix — routes to factory_thin loop
- `btw:` prefix — non-blocking observation mode
- `bughunter` — AST scanner for BH001/BH003/BH004/BH010; `poe-bughunter` CLI
- Nightly eval wired to evolver via heartbeat

### Fixed
- `_GOAL_MAP_KEYWORDS` "how does" too broad — removed; added specific phrases
- `run_debate` import json missing in outer try block — all parsing silently failed
- `run_debate` parameter name collision with function name — renamed to `with_debate`

### Factory Mode
- `factory_minimal` (single-call Haiku) and `factory_thin` (loop+adversarial) built and benchmarked
- Adversarial patterns merged to main; load-bearing scaffolding identified
- Full comparison: `docs/FACTORY_MODE_FINDINGS.md`

---

## [1.1.0] - 2026-03-27

Token burn reduction: 789k → 67k (91%) through pre-fetch layer, clean markdown fetching, and sub-agent tool restrictions.

### Added — Web Pre-fetch Layer (`src/web_fetch.py`)
- `_jina_fetch()`: Jina AI Reader (`r.jina.ai`) returns clean markdown from any URL — no raw HTML in context
- `fetch_x_tweet()`: tries Jina first (full thread), then authenticated X CLI, then oEmbed fallback
- `fetch_x_article()`: returns immediate human-readable notice (X native articles are deprecated/inaccessible)
- `_x_cli_available()` / `_x_cookie_env()` / `_fetch_via_x_cli()`: authenticated X scraping via OpenClaw's `x-twitter-cli.sh`
- Second-pass URL following: resolves t.co links and X article links found in fetched content (not just step text)
- `enrich_step_with_urls(extra_context=...)`: scans prior step summaries so later steps can access URLs introduced earlier

### Changed — Sub-agent Token Hygiene (`src/agent_loop.py`, `src/llm.py`)
- `ClaudeSubprocessAdapter` now passes `--disallowedTools WebFetch,WebSearch` — prevents sub-agent from fetching raw HTML (was primary source of 200–535k token spikes per step)
- `_EXECUTE_SYSTEM` — added URL FETCHING POLICY: sub-agent must use only pre-fetched content, no curl/wget/tool fetches
- `_EXECUTE_SYSTEM` — added TOKEN EFFICIENCY section: prefer concise output, avoid verbatim quotes, work with partial info
- `_execute_step()` — passes `completed_context` as `extra_context` to `enrich_step_with_urls` so URLs from step 1 are available to step 3 (fixes context-carry bug that caused step 3 blocks)

### Tests
- 1290 tests passing (up from 1264)
- `tests/test_web_fetch.py`: 26 tests covering html stripping, URL extraction, X routing, Jina integration, enrich pipeline

---

## [1.0.0] - 2026-03-23

Phases 1–7 complete. Poe is now a fully autonomous, self-improving AI concierge reachable via Telegram.

### Added — Phase 1: Autonomous Loop
- `src/agent_loop.py`: goal → decompose → execute steps → done|stuck loop (`poe-run`)
- Basic stuck detection (same action 3x)

### Added — Phase 2: NOW/AGENDA Routing
- `src/intent.py`: LLM + heuristic intent classifier
- `src/handle.py`: unified entry point auto-routing to fast (NOW) or deep (AGENDA) lane (`poe-handle`)

### Added — Phase 3: Director/Worker Hierarchy
- `src/director.py`: Director agent plans, delegates, reviews (`poe-director`)
- `src/workers.py`: research/build/ops/general workers with persona system prompts

### Added — Phase 4: Loop Sheriff + Heartbeat
- `src/sheriff.py`: per-project stuck detection + `check_system_health()` + heartbeat state I/O
- `src/heartbeat.py`: 60s health loop with 3-tier recovery (scripted → LLM diagnosis → Telegram escalation)
- `deploy/poe-heartbeat.service`: systemd unit

### Added — Phase 5: Memory + Learning (Reflexion)
- `src/memory.py`: outcome recording, LLM lesson extraction, session bootstrap, lesson injection
- Files: `memory/outcomes.jsonl`, `memory/lessons.jsonl`, daily logs

### Added — Phase 6: Telegram + Platform-agnostic LLM
- `src/llm.py` (rewrite): `ClaudeSubprocessAdapter`, `AnthropicSDKAdapter`, `OpenRouterAdapter`, `OpenAIAdapter` behind one interface; `MODEL_CHEAP/MID/POWER` constants; `build_adapter("auto")`
- `src/telegram_listener.py`: long-poll listener, slash commands, immediate-ack + edit UX (`poe-telegram`)
- `src/ancestry.py`: goal ancestry chain (§18 spec) — `ancestry.json` per project, prompt injection, `orch ancestry/impact` CLI
- `deploy/poe-telegram.service`: systemd unit
- Slash commands: `/status /director /research /build /ops /ancestry /help`

### Added — Phase 7: Meta-Evolution (§19)
- `src/evolver.py`: analyzes last N outcomes, identifies failure patterns, generates structured suggestions; wired into heartbeat loop every 10 ticks (`poe-evolver`)
- `memory/suggestions.jsonl`: persistent suggestion store

### Added — Phase 8: Scaling + Evaluation
- `src/metrics.py`: quality tracking — success rate, cost, token usage per task type (`poe-metrics`)
- `src/eval.py`: benchmark suite with known-good goals and scoring (`poe-eval`)
- Concurrent loop support: `run_parallel_loops()` in agent_loop.py
- Crew composition: `infer_crew_size()` in workers.py
- Auto-apply evolver suggestions: `poe-evolver --list` / `poe-evolver --apply <id>`

### Added — Docs
- `docs/ARCHITECTURE.md`: full system architecture, module dependency graph, data flows
- `ROADMAP.md`: updated to reflect Phases 0–7 complete, Phase 8 next

### Changed
- `src/agent_loop.py`: ancestry context injected into decompose + execute prompts
- `src/cli.py`: 14 new subcommands across all phases

### Tests
- 346 tests passing (up from ~50 at v0.4.0)

---

## [0.4.0] - 2026-03-11

### Added
- `src/cli.py` with `init|next|done|log|blocked|report`
- priority file support (`projects/<slug>/PRIORITY`) and priority-aware global scheduling
- blocked-project triage and report generation helpers
- parser/unit tests and CLI integration tests (`tests/`)
- smoke harness (`scripts/smoke.sh`)
- CI workflow (`.github/workflows/ci.yml`)
- migration + queue adapter + compatibility + security + end-to-end docs

### Changed
- `scripts/new_project.sh` and `scripts/mark_next_done.sh` now route through CLI
- scripts and CLI now emit explicit error taxonomy codes for common failures

### Fixed
- roadmap M1-M4 items were converted from plan-only to executable implementation
