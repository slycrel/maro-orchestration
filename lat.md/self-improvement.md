# Self-Improvement

Systems that make Poe measurably better over time without human intervention.

## Active Components

Currently running self-improvement subsystems and their roles in the feedback loop.

- **Evolver** (`src/evolver.py`) — meta-improvement every ~10 heartbeats; scans outcomes, generates Suggestions, auto-applies low-risk fixes; runs skill maintenance (promote/demote/rewrite/retire-variants) on every cycle
- **Thinkback** (`src/thinkback.py`) — session-level hindsight replay; reviews each step decision (good/acceptable/poor), extracts key lessons, rates mission efficiency. See [[quality-gates#Passes Pipeline]]
- **Bughunter** (`src/bughunter.py`) — AST-based self-directed code quality scan; runs against own src/
- **Nightly eval** (`src/eval.py`) — fires via `eval_every=1440`; failures → evolver Suggestion entries
- **Harness optimizer** (`src/harness_optimizer.py`) — reads stuck traces + prompt text, proposes word-level changes as evolver Suggestions; wired into heartbeat every 50 ticks

## Skill Evolution Pipeline (FunSearch + Agent0 + Voyager steals)

The full evolutionary loop for skill improvement, wired end-to-end:

### Island Model (`src/skills.py`)
Skills partitioned into 4 islands (research/build/analysis/general) by keyword scoring. `run_island_cycle()` culls bottom half of open-circuit skills per island on every evolver run — prevents monoculture convergence.

### Fitness Oracle (`src/strategy_evaluator.py`)
`evaluate_strategy()` scores candidate strategies deterministically against `memory/outcomes.jsonl` via TF-IDF cosine similarity — no LLM in the eval path. `evaluate_skill()` used as pre-score gate before frontier rewrites: PASS verdict skips unnecessary rewrite.

### Frontier Targeting (`src/skills.py`)
`frontier_skills()` returns skills in 40–70% utility zone (challenging but not circuit-broken). Evolver rewrites up to 2 per cycle — the hardest-to-diagnose skills without empirical improvement attempts.

### Skill Validation Harness (`src/skills.py`)
`validate_skill_for_promotion()` LLM quality gate before promotion; fail-open (unavailability doesn't block). `maybe_auto_promote_skills(adapter, max_repair_attempts=3)` runs repair-rewrite loop — skills failing all attempts stay provisional.

### A/B Variant Competition (`src/skills.py`)
Frontier rewrites create challenger variants (`variant_of=parent.id`) rather than immediately replacing parents. `select_variant_for_task(skill, task_id)` routes via `sha1(task_id) % pool_size` — deterministic 50/50 split. `record_variant_outcome()` tracks wins/losses. `retire_losing_variants()` promotes winner and drops loser after ≥5 trials per side. Wired into `run_agent_loop` decompose path and step outcome paths.

### Score-Weighted Mutation (`src/evolver.py`)
`rewrite_skill()` feeds top-K ranked peer skills as version-tagged context to LLM ("v0 score=4, v1 score=7 — generate v2"). `_compactness_adjusted_score()` penalizes bloat at sampling time.

### Pre-Scoring Discard Gate (`src/evolver.py`)
Rewrites with empty description/steps, >400-char description, or >10 steps are silently discarded before any scoring.

## Memory Improvement Systems

### Failure-Chain Recording (`src/memory.py`, `src/agent_loop.py`)
`Outcome.failure_chain: List[str]` + `Outcome.recovery_steps: int`. Agent loop accumulates the full failure→diagnosis→recovery chain on retry/split/terminal. Every retry becomes a training signal.

### Majority-Vote Pseudo-Labels (`src/memory.py`)
`extract_lessons_via_llm(k_samples=3)` draws k independent LLM samples; `majority_vote_lessons()` only promotes lessons agreed on by strict majority via Jaccard similarity — eliminates false lessons from noisy single-attempt outcomes.

### Three-Layer Memory Compression (`src/memory.py`)
`compress_old_outcomes()` (LLM or heuristic fallback); `load_outcomes_with_context(goal)` retrieves raw recent + TF-IDF-ranked compressed batches.

### Promotion Cycle (Phase 56, `src/memory.py`)
Observation → hypothesis (2+ confirmations) → standing rule. `observe_pattern()`, `contradict_pattern()`, `inject_standing_rules()`. Decision journal: `record_decision()` + `inject_decisions()`. Both injected into every decompose call.

## Event-Reactive Architecture

### EventRouter (`src/interrupt.py`)
Thread-safe typed event bus. `post_typed_event(kind, payload, source)` unblocks DAG steps waiting on `await:<kind>` step text. `post_heartbeat_event()` fires typed events — Telegram/Slack signals can unblock waiting agent steps. Enables event-driven self-improvement triggers (e.g., data arrival unblocking a research step).

## Related Concepts

- [[memory-system]] — raw material for all improvement signals
- [[quality-gates]] — inspector + adversarial + thinkback; separate quality layer
- [[core-loop]] — loop outcomes feed into self-improvement pipeline
- [[worker-agents]] — team workers whose outcomes feed back into skill evolution
