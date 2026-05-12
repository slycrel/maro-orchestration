# MD Claims Audit — Grounded vs Ungrounded

**Generated:** 2026-05-12  
**Scope:** 79 `.md` files (69 in `docs/`, 10 in `lat.md/`)  
**Method:** automated grep-index verification of symbols, file paths, and class names against `src/` and repo tree; manual sampling of high-value files (MILESTONES.md, ROADMAP.md)

---

## Executive Summary

| Bucket | Count | % of 506 claims |
|--------|-------|-----------------|
| **GROUNDED** — verified in `src/` or repo | 430 | 85.0% |
| **STALE** — production docs with false/missing claims | 21 | 4.2% |
| **ASPIRATIONAL** — steal-list / research docs (expected unshipped) | 9 | 1.8% |
| **RUNTIME_ABSENT** — paths written at runtime, not in repo | ~46 | ~9.1% |

MILESTONES.md: **40/41 verified** (97.6%). One minor discrepancy.  
ROADMAP.md: **50/51 verified** (98.0%). One minor discrepancy (import location).

The repo docs are in good shape. The ~4% stale rate is confined to a small set of docs that reference planned-but-unshipped code or obsolete class names.

---

## STALE — Production Docs With False Claims (Action Required)

These appear in architecture, design, or lat.md files — not steal-lists — and reference
symbols or files that are absent from `src/`.

### Missing Symbols

| Symbol | Type | Source Doc | Line | Severity | Notes |
|--------|------|-----------|------|----------|-------|
| `enforce_constraint` | function | `lat.md/constraint-system.md` | 21 | **HIGH** | lat.md graph claims this is the enforcement entry point; no such function in `src/constraint.py` |
| `QueueAdapter` | class | `docs/QUEUE_ADAPTER.md` | 32 | **HIGH** | Entire doc about a class that does not exist; no implementation anywhere in `src/` |
| `inject_scope_into_plan` | function | `docs/PHASE_65_IMPLEMENTATION_PLAN.md` | — | **HIGH** | Phase 65 plan references planned-but-unshipped function |
| `generate_premises` | function | `docs/CONSTRAINT_ORCHESTRATION_AUDIT.md` | — | **MEDIUM** | Audit doc references unimplemented function |
| `inject_premises` | function | `docs/CONSTRAINT_ORCHESTRATION_AUDIT.md` | — | **MEDIUM** | Same audit |
| `_plan_and_decompose` | function | `docs/CONSTRAINT_ORCHESTRATION_AUDIT.md` | — | **MEDIUM** | Same audit; actual function is `_decompose` or `plan_and_execute` |
| `_build_decompose_context` | function | `docs/knowledge-layer/02_K_STAGES.md` | — | **MEDIUM** | K-stages doc references absent helper |
| `breaching` | class/symbol | `docs/success-criteria.md` | 95 | **MEDIUM** | Success criteria doc references absent symbol |
| `deferral` | class/symbol | `docs/research-brief-findings-and-design.md` | 142 | **LOW** | Research doc; low impact |

### Missing Files

| Missing Path | Referenced From | Severity | Notes |
|-------------|----------------|----------|-------|
| `docs/KNOWLEDGE_LAYER_BASELINE.md` | `docs/knowledge-layer/02_K_STAGES.md` | **MEDIUM** | K2 baseline cross-ref broken |
| `USER/GOALS.md` | `docs/BITTER_LESSON_ANALYSIS.md` | **MEDIUM** | `user/SIGNALS.md` exists; GOALS/PREFERENCES don't |
| `USER/PREFERENCES.md` | `docs/BITTER_LESSON_ANALYSIS.md` | **MEDIUM** | Same |
| `docs/conversations/2026-04-26-thread-architecture.md` | `docs/THREAD_ARCHITECTURE.md` | **MEDIUM** | Missing conversation log cross-ref |
| `docs/THREAD_ARCHITECTURE_NAVIGATOR_PROMPT.md` | `docs/THREAD_ARCHITECTURE.md` | **LOW** | Referenced prompt doc doesn't exist |

---

## MILESTONES.md Discrepancies

**40/41 claims verified.** One discrepancy:

| Claim | Source | Actual | Severity |
|-------|--------|--------|----------|
| `heartbeat_loop` autonomy default is `False` | MILESTONES.md | `heartbeat.py:769` — default is `None`, not `False` | minor |

---

## ROADMAP.md Discrepancies

**50/51 claims verified.** One discrepancy:

| Claim | Source | Actual | Severity |
|-------|--------|--------|----------|
| `calibrated_alignment_threshold()` defined in `memory.py` and `inspector.py` | ROADMAP.md — Phase 60 | Defined in `knowledge_lens.py:802`; `memory.py` and `inspector.py` import it | minor |

---

## ASPIRATIONAL — Steal-List Functions (Not Bugs)

These appear in `docs/research/x-posts-steal-list-20260414.md` and `docs/research/funsearch-agent-design.md`. The steal-list is a backlog of ideas, not shipped features — absence from `src/` is expected.

| Symbol / File | Source | Notes |
|---------------|--------|-------|
| `parse_magic_keywords` | steal-list:29 | Future candidate |
| `build_tool_registry` | steal-list:54 | Future candidate |
| `run_daily_repair` | steal-list:55 | Future candidate |
| `find_matching_lessons` | steal-list:77 | Future candidate |
| `_apply_graph_boost` | steal-list:77 | Future candidate |
| `audit_goal_scaffolding` | steal-list:101 | Future candidate |
| `_score_suggestion_impact` | steal-list:103 | Future candidate |
| `categorize_suggestion_by_impact` | steal-list:103 | Future candidate |
| `propose_related_missions` | steal-list:127 | Future candidate |
| `src/strategy_db.py` | funsearch-agent-design.md:170 | Research prototype |
| `src/strategy_evolver.py` | funsearch-agent-design.md:171 | Research prototype |
| `src/skill_synthesizer.py` | steal-list:54 | Future candidate |
| `src/hybrid_retriever.py` | steal-list:77 | Future candidate |
| `src/dispute_detector.py` | steal-list:78 | Future candidate |

---

## RUNTIME_ABSENT — Correct by Design (~46 entries)

These paths appear in docs but are written by Poe at runtime, not checked into the repo. Not bugs.

| Path | Documented In | Notes |
|------|--------------|-------|
| `memory/outcomes.json` | `docs/ARCHITECTURE.md:247` | Runtime data |
| `memory/lessons.json` | `docs/ARCHITECTURE.md:249` | Runtime data |
| `memory/YYYY-MM-DD.md` | `docs/ARCHITECTURE.md:250` | Runtime data |
| `memory/medium/lessons.json` | `docs/ARCHITECTURE.md:259` | Runtime data |
| `memory/long/lessons.json` | `docs/ARCHITECTURE.md:260` | Runtime data |
| `memory/canon_stats.json` | `docs/ARCHITECTURE.md:272` | Runtime data |
| `memory/heartbeat-state.json` | `docs/ARCHITECTURE.md:343` | Runtime data |
| `memory/heartbeat-log.json` | `docs/ARCHITECTURE.md:344` | Runtime data |
| `memory/suggestions.json` | `docs/ARCHITECTURE.md:360` | Runtime data |
| `memory/eval-results.json` | `docs/ARCHITECTURE.md:395` | Runtime data |
| `memory/interrupts.json` | `docs/ARCHITECTURE.md:413` | Runtime data |
| `memory/mission-log.json` | `docs/ARCHITECTURE.md:452` | Runtime data |
| `memory/skills.json` | `docs/ARCHITECTURE.md:464` | Runtime data |
| `memory/sandbox-audit.json` | `docs/ARCHITECTURE.md:484` | Runtime data |
| `memory/inspector-log.json` | `docs/ARCHITECTURE.md:677` | Runtime data |
| `memory/friction-signals.json` | `docs/ARCHITECTURE.md:677` | Runtime data |
| `memory/autonomy.json` | `docs/ARCHITECTURE.md:711` | Runtime data |
| `~/.poe/workspace/` | `docs/ARCHITECTURE.md:527` | Runtime workspace root |
| `~/.poe/config.yml` | `docs/ARCHITECTURE_OVERVIEW.md:152` | User config |
| `memory/rules.json` | `docs/KNOWLEDGE_CRYSTALLIZATION.md:121` | Runtime data |
| `memory/skill-stats.json` | `docs/ROADMAP_ARCHIVE.md:238` | Runtime data |
| `memory/router-stats.json` | `docs/ROADMAP_ARCHIVE.md:306` | Runtime data |
| `memory/persona-outcomes.json` | `docs/ROADMAP_ARCHIVE.md:624` | Runtime data |
| `memory/step-costs.json` | `docs/ROADMAP_ARCHIVE.md:656` | Runtime data |
| `memory/events.json` | `docs/ROADMAP_ARCHIVE.md:704` | Runtime data |
| `memory/diagnoses.json` | `docs/ROADMAP_ARCHIVE.md:904` | Runtime data |
| `memory/hypotheses.json` | `docs/ROADMAP_ARCHIVE.md:1118` | Runtime data |
| `memory/decisions.json` | `docs/ROADMAP_ARCHIVE.md:1123` | Runtime data |
| `memory/captains_log.json` | `docs/knowledge-layer/07_CAPTAINS_LOG_SPEC.md:106` | Runtime data |
| `memory/repair-log.json` | `docs/research/x-posts-steal-list-20260414.md:55` | Runtime data |
| `~/.openclaw/workspace/secrets/` | `docs/SECURITY_MODEL.md:56` | Runtime secrets dir |
| `~/.claude/settings.json` | `docs/ROADMAP_ARCHIVE.md:585` | System config |
| `~/.codex/auth.json` | `docs/ROADMAP_ARCHIVE.md:588` | System config |
| `~/.openclaw/openclaw.json` | `docs/archive/plan-next-phase.md:89` | System config |
| `memory/standing_rules.json` | `docs/archive/plan-next-phase.md:183` | Runtime data |

---

## Well-Grounded Highlights

Representative sample of major symbols verified present in `src/`:

| Symbol | Location | Verified At |
|--------|----------|-------------|
| `LoopDiagnosis.project` | `introspect.py` | line 102 |
| `diagnose_loop(loop_id, project='')` | `introspect.py` | line 214 |
| `_format_decomp_too_broad_note` | `introspect.py` | line 462 |
| `_is_long_lived_step` | `step_exec.py` | line 217 |
| `STEP_TOO_BROAD` | `captains_log.py` | line 91 |
| `_write_iteration_artifacts` | `agent_loop.py` | line 859 |
| `record_log_offset()` | `runs.py` | line 278 |
| `slice_log_for_run()` | `runs.py` | line 294 |
| `_step_tier_overrides`, `_session_verify_failures`, `_session_tier_floor` | `agent_loop.py` | lines 3818–3823 |
| `classify_step_model` | `poe.py` | line 158 |
| `estimate_goal_scope(goal)` | `planner.py` | line 76 |
| `multi_lens_review()` | `pre_flight.py` | line 276 |
| `SkillStats`, `record_skill_outcome()`, `efficiency_score()` | `skills.py` | — |
| `extract_template_variables()`, `render_persona_template()` | `persona.py` | — |
| `ViolationType` | `constraint.py` | — |
| `calibrated_alignment_threshold()` | `knowledge_lens.py` | line 802 |
| `pre_flight.py` | `src/` | exists |
| `personas/plan-critic.md` | `personas/` | exists |
| `memory/preflight_calibration.jsonl` | `pre_flight.py:399`, `agent_loop.py:1668` | — |

MILESTONES.md and ROADMAP.md are exceptionally well-grounded — both above 97% verified, with only minor wording/import-location discrepancies.

---

## Prioritized Fix List

1. **`lat.md/constraint-system.md` line 21** — claims `enforce_constraint` is the enforcement entry; find the actual function name (likely in `constraint.py`) and update the lat.md node.
2. **`docs/QUEUE_ADAPTER.md`** — entire doc about `QueueAdapter` class that doesn't exist. Either implement it or mark the whole doc as `[ASPIRATIONAL]` / move to `docs/research/`.
3. **`docs/PHASE_65_IMPLEMENTATION_PLAN.md`** — `inject_scope_into_plan` is planned-but-unshipped; add `[PLANNED — not yet implemented]` marker.
4. **`docs/CONSTRAINT_ORCHESTRATION_AUDIT.md`** — `generate_premises`, `inject_premises`, `_plan_and_decompose` all absent; add a "gaps vs shipped" table to the audit doc to distinguish them.
5. **`docs/success-criteria.md` line 95** — `breaching` symbol reference; check if it was renamed and update the doc.
6. **`docs/BITTER_LESSON_ANALYSIS.md`** — references `USER/GOALS.md` and `USER/PREFERENCES.md`; these don't exist; `user/SIGNALS.md` does; update the cross-references.
7. **`docs/knowledge-layer/02_K_STAGES.md`** — references `KNOWLEDGE_LAYER_BASELINE.md` (missing) and `_build_decompose_context` (absent); create baseline doc stub or remove the cross-ref.
8. **`docs/THREAD_ARCHITECTURE.md`** — cross-ref to `docs/conversations/2026-04-26-thread-architecture.md` is broken; remove or restore the conversation log.
9. **MILESTONES.md** — `heartbeat_loop` autonomy default: doc says `False`, code is `None` (`heartbeat.py:769`); one-line fix.
10. **ROADMAP.md Phase 60** — `calibrated_alignment_threshold()` location: doc says `memory.py`/`inspector.py`; actual is `knowledge_lens.py:802`; update the phase description.
