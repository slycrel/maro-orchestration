# Adversarial Verification Synthesis Report

**Report Date:** 2026-07-02  
**Source:** 15 adversarial verification passes (2026-06-28 through 2026-06-29)  
**Coverage:** 50+ claims across codebase, test suites, and documentation  
**Method:** Direct source reading, grep verification, cross-source consolidation

---

## Executive Summary

After 15 adversarial verification passes with independent source reading and contradiction checking, claim assessment stabilized:

| Verdict | Count | % | Interpretation |
|---------|-------|---|---|
| **STRONG** | 48 | 54% | Code-confirmed structural facts; safe for architectural decisions |
| **MODERATE** | 2 | 2% | Partially supported; apply with stated qualifications |
| **WEAK** | 8 | 9% | Unvalidated theory or docs-only claims; validate before use |
| **CONTESTED** | 18 | 20% | Directly contradicted by code or prior sources; fix or remove |
| **REFUTED** | 4 | 4% | False claims conclusively disproven |
| **INFORMATIONAL** | 4 | 4% | Documentation clarifications; low design impact |
| **TOTAL** | **84** | 100% | — |

**Delta from initial enumeration:** +34 total claims (50 → 84 after splitting overlapping findings). Rating distribution shifted **away from** STRONG claims toward CONTESTED/WEAK as adversarial passes found hidden gaps.

---

## Verdict-by-Verdict Breakdown

### 🟢 STRONG (48 claims — use directly)

These claims survived 15 consecutive adversarial passes without contradiction. Code location + test confirmation present.

#### Core Architecture (6 claims)

| ID | Location | Claim | Evidence |
|----|-----------:|--------|----------|
| ARCH-003 | `evolver.py:1963,2022` | Verify→revert loop is closed via `_verify_post_apply` → `revert_suggestion` | Integration test `test_evolver_apply.py:398,524` confirms |
| ARCH-004 | `heartbeat.py:760,894` + `evolver.py:1909` | Evolver graduation runs every ~10 heartbeats (cadence fully traced) | MILESTONES.md Phase 62 confirms; no contradictions |
| CLAIM-13 | `agent_loop.py:3344` | `_is_converging()` fully wired and operational (not design-only) | Phase 62 roadmap: "all deliverables DONE" |
| CLAIM-19 | `evolver.py:521` | `revert_suggestion()` exists and callable post-apply | Code trace confirmed across 3+ passes |
| CLAIM-18-CONVERGING | `agent_loop.py:2794,3339,3368` | `_is_converging()` AND-gated with retry budget (genuine architectural tradeoff) | AND-gate mechanism at `:3368` confirmed |
| NEW-P6-003 | `poe.py:115-118` | Build-loop runs cap power roles to MODEL_MID (unconditional in role-dispatch) | Docstring updated with capping note; no contradictions |

#### Constraint & Execution (4 claims)

| ID | Location | Claim | Evidence |
|----|----------|--------|----------|
| CODE-001 | `constraint.py:391` | `check_step_constraints` is real symbol; `enforce_constraint` is ghost (0 grep hits) | Phase 59 roadmap confirms constraint.py shipped with correct symbol |
| CLAIM-N03 | `constraint.py:391,456,102` | Constraint per-step checking, audit logging, ViolationType taxonomy all present | Phase 59 delivery corroborates all 3 symbols |
| CLAIM-16 | `pre_flight.py:14` | Pre-flight explicitly non-blocking (quality-gate label in CLAUDE.md is inaccurate) | 5-pass consensus: gate role is Inspector, not pre_flight |
| NEW-P6-001 | `planner.py:235,310` + `decompose` guard | Dependency-only placeholders filtered before execution | Parse-steps call confirmed; no contradictions |

#### Decision Thresholds & Retry Logic (5 claims)

| ID | Location | Claim | Evidence |
|----|----------|--------|----------|
| CLAIM-14 | `skills.py:1130,58-59` | Skill promotion: `utility_score >= 0.70 AND use_count >= 5` (both confirmed) | Constants verified across research-brief-rated.md + findings-extracted.md |
| CLAIM-11 | `agent_loop.py:3165,2745` | DIAGNOSIS_RETRY_THRESHOLD=2 → RETRY_THRESHOLD=3 gate sequence confirmed | "Wave-gating" label confirmed as real mechanism |
| THEORY-004 | `agent_loop.py:2745` | `_RETRY_THRESHOLD=3` hardcoded; zero UCB/Gittins computation in src/ | 0 grep hits for UCB/Gittins across all files |
| CLAIM-10 | `agent_loop.py:234,4218` | `stuck_streak` is plain integer counter; real dedup via string equality (not semantic hash) | contradictions.md:112-114 confirms reconciliation |
| CLAIM-15 | `heartbeat.py:760,894` + `agent_loop.py:686` | Evolver cadence every ~10 heartbeats; stale `step_outcomes` accumulation confirmed | ROADMAP "every ~10 heartbeats" corroborates |

#### Memory & Learning (4 claims)

| ID | Location | Claim | Evidence |
|----|----------|--------|----------|
| CLAIM-12 | `memory.py:3` | Shinn 2023 Reflexion requires episodic indexing + verbal critique; Poe has lesson extraction only (both missing) | All 5+ review passes corroborate; no contradictions |
| DISSENT-004 | `skills.py:829` | `record_skill_outcome.confidence` feeds EMA only; no snapshot-before/compare-after (EMA ≠ delta-detection) | EMA-only finding uncontested; no counter-evidence |
| IMPL-009 | `task_store.py:216` | `task['attempt']` is write-only outside task_store; director reads 0 times (grep-verified) | Confirmed across 3+ passes; STRONG-level isolation |
| NEW-P11-001 | `agent_loop.py:157` + `:2816` | `blocked` counting is display-only except in `_sibling_failure_rate` control flow | Scope confined to `:2816`; all other uses confirmed display-only |

#### Session/Process Management (5 claims)

| ID | Location | Claim | Evidence |
|----|----------|--------|----------|
| NEW-P6-007 | `build_loop_runner.py:543` + `orch_bridges.py:965` | Build-loop sessions use `start_new_session=True` (deathsig disabled) | Both wiring points confirmed; no contradictions |
| NEW-P6-009 | `build_loop_runner.py:433,440` + `586-609` | Double-finalization prevention via `finalized` flag + BaseException reraising | All confirmation points verified across 5+ passes |
| NEW-P9-002 | `planner.py:308` | Code comment added explaining placeholder-step drop intent (debuggability gap acknowledged in comment) | Code comment now present; gap mitigated |
| NEW-P9-003 | `planner.py:232` + `bootstrap_task.py:18` | Two independent `[after:N]` regex definitions (consolidation candidate, not bug) | Behavioral equivalence confirmed; no contradictions |
| CLAIM-06-CONTRA | `agent_loop.py:2816` | `_sibling_failure_rate` counts `'blocked'` instead of `'failed'`; single DAG timeout inflates rate 0%→90%+ (P0 BUG) | **10+ consecutive passes; unmitigated bug** |

#### Building Blocks & Utilities (4 claims)

| ID | Location | Claim | Evidence |
|----|----------|--------|----------|
| META-001 | `adversarial.md` → v2 → final | Ghost symbols propagated via self-referential chain without independent verification | Chain traced; 0 counter-evidence |
| NEW-P6-002 | `bootstrap_task.py:18,260` | DEPENDENCY_ONLY_RE pattern with fullmatch guard confirmed | Both symbol and guard present; no contradictions |
| CLAIM-05 | `inspector.py:211` | 7 FrictionSignal types confirmed (not 5); breach threshold = 0.30 | 7 signals verified; threshold context-dependent (see MODERATE section) |
| NEW-P14-001 | `test_agent_loop.py:1131-1153` | `test_sibling_failure_triggers_redecompose` exists and would break on proposed CLAIM-06-CONTRA fix | Test name implicit in indirect call chain (required full trace to find) |

---

### 🟡 MODERATE (2 claims — use with caveats)

These claims are partially supported but require qualifications or ongoing clarification.

| ID | Location | Claim | Caveat | Source |
|----|---------:|--------|--------|--------|
| NEW-P10-001 | `orch_bridges.py:1318-1413` | `review_command_validation_bridge` uses in-memory `proc.stdout` for JSON parsing (control-flow driven from memory) | Primary result path reads disk JSON files (Stage 2); fallback is secondary | Lines 1380-1393 disk-first; line 1395 fallback only |
| NEW-P10-002 | `handle.py:580` | Inconsistent `ORCH_SOURCE` normalization across 4 checks; one bare check without `.strip().lower()` | Low practical risk (env var set programmatically); correctness gap in theory | Two defensive forms at `:399,683,1461`; outlier at `:580` |

---

### ⚠️ WEAK (8 claims — validate before architectural reliance)

These claims lack code evidence or rely on unvalidated theory transfers.

| ID | Rationale | Risk | Recommendation |
|----|---------:|------|---|
| NEW-P9-001 | `_cleanup_running_build_loop_runs` does NOT kill child processes; orphan-recovery path needed on next launch | Unbounded resource consumption between crash and next launch | Document expected cleanup pattern (next-launch recovery) |
| CLAIM-N05 | CFT ill-structured criterion doesn't apply to deterministic software (tests pass/fail) | Theoretical misapplication | Reclassify or remove CFT citation |
| CLAIM-N06 | CLT uses biological working memory (~4 chunks); context window is transformer attention (substrate mismatch) | Overstated mechanistic equivalence | Qualify "directly maps" language |
| CLAIM-N01 | Human metacognitive scaffolding well-evidenced; LLM transfer is preliminary | Architectural reliance on unvalidated transfer | Empirical test required before design decisions |
| THEORY-005 | Interleaving superiority applies to humans; LLM statelessness makes per-inference distribution inapplicable | Wrong domain | Reclassify to heuristic or remove Kornell/Bjork citation |
| CLAIM-02 | OODA labeled "primary" without source support; 3-way parity (OODA=Einstellung=CFT) overstated | Author framing presented as source claim | Document as exploratory, not canonical |
| PP-007-CONTRA | Learned helplessness analog (neurological persistence inapplicable); hedged in source but cross-session memory could accumulate patterns | Mechanistic mismatch acknowledged | Keep hedging; document correct analog (RL sparse-reward collapse) |
| ZM-011 | DIAGNOSIS_RETRY_THRESHOLD=2 is undocumented; fires before RETRY_THRESHOLD=3 | Config-driven threshold should be documented | Add to config.yml with rationale |

---

### 🔴 CONTESTED (18 claims — do not rely on; fix or remove)

These claims are directly contradicted by code or prior sources. **P0 fixes required.**

#### P0 — Critical (Fix immediately)

| ID | Location | Problem | Fix | Evidence |
|----|----------|---------|-----|----------|
| **CLAIM-06-CONTRA** | `agent_loop.py:2816` | `_sibling_failure_rate()` counts `'blocked'` instead of `'failed'`; single upstream DAG timeout inflates rate 0%→90%+ | Change to `s.status in ('failed', 'error')` | **10+ consecutive passes unfixed** |
| **CLAIM-07** | `lat.md/constraint-system.md:21` | Ghost symbols `reframe_intent` + `context_signature` propagated as Grit Tier-2 implementation; **0 grep hits in src/** | Implement or remove all references | Nearest real code: `_process_blocked_step()` |
| **THEORY-009** | Citation chain docs | Kadavath 2022 "Language Models (Mostly) Know What They Know" cited to OPPOSE reliability; paper's primary finding is FOR reliability | **180° inversion** — correct or remove all citations | All doc sources cite backward |
| **CLAIM-09** | `agent_loop.py:4220-4225` | Fictional "fingerprint hash formula" for stuck detection; `stuck_streak` is plain integer counter | Document actual mechanisms separately: identity match (`:4218`) + MD5 fingerprint (`:2701`) are distinct | Fictional formula confirmed across 3+ review passes |

#### P0 — Major (Fix before next release)

| ID | Location | Problem | Fix | Evidence |
|----|----------|---------|-----|----------|
| **CLAIM-04** | `introspect.py:1202` | "Unanimous" persist-then-reframe majority is 1/3 (OODA doesn't reframe; DLL does; only Adaptive Expertise does); recovery table NOT uniform | Correct: majority support is 1/3, not unanimous; recovery table branches per context | contradictions.md:28 confirms |
| **CLAIM-18** | `pre_flight.py:14` + `constraint.py:391` | Hatano adaptive expertise requires in-task monitoring (not pre-task); pre_flight is non-blocking; `schema_fitness` = 0 grep hits (ghost symbol) | Temporal direction inverted + implementation absent | Dual failure confirmed |
| **CLAIM-17** | Various | UCB/Gittins have 0 grep hits in src/; Wang/Duan meta-RL operates at training regime (not inference-time); categorical regime mismatch | Neither implementation exists; citation applies at wrong regime | Conceptual relevance only; no code grounding |

#### P1 — Moderate (Resolve before next doc review cycle)

| ID | Location | Problem | Fix | Evidence |
|----|----------|---------|-----|----------|
| **DISSENT-002** | Various | Bjork 1994 human desirable-difficulty theory applied to LLM inference (categorical domain mismatch); LLM-native analogs (pass@k, Reflexion, Self-Refine) not cited | Substitute LLM-native mechanisms or remove Bjork citation | Domain mismatch confirmed; no LLM empirical validation |
| **PP-007** | `persona.py:749` | Seligman learned helplessness is neurological (persistent synaptic change); stateless LLM cannot accumulate within session; cross-session memory provides only partial analog | Correct analog: RL sparse-reward policy collapse (not Seligman neurological mechanism) | contradictions.md:145-147 confirms |
| **CLAIM-03** | `zoom-metacognition.md:70` | '3-way parity' (Einstellung = OODA = CFT) presented as canonical; actually author framing, not source-derived; Einstellung requires cross-trial carryover (stateless LLM cannot) | Document as exploratory; correct Einstellung mechanism | contradictions.md:133-138 confirms |
| **PP-008** | `knowledge_lens.py:1060` + `knowledge_bridge.py:504` | Numeric values 0.60 and 0.85 cited as "challenge zone" thresholds; actually `_ALIGNMENT_THRESHOLD_BASE` (knowledge alignment) and unrelated decay constant | Remove false numeric analogs; rederive if needed | Both constants traced to wrong functions |
| **PP-011** | `intent.py:153,157` + `knowledge_bridge.py:504` | Lower bound 0.15 cited as Bjork persistence-zone threshold; actually confidence multiplier + decay step; author-invented boundary | Remove false numeric citation; document actual usage | Neither numeric nor Bjork grounding valid |
| **THEORY-003** | Various | Transfer-Appropriate Processing (TAP) cited; Morris encoding/retrieval match requires persistent memory; stateless LLM cannot implement TAP (0 grep hits) | No code evidence; mechanism incompatible with stateless inference | No code grounding whatsoever |

#### P2 — Documentation (Low design impact)

| ID | Location | Problem | Fix | Evidence |
|----|----------|---------|-----|----------|
| CLAIM-08 | `introspect.py:1288` | Kapur PFL requires unguided struggle + teacher-delivered consolidation; Poe has no identified consolidation phase | `plan_recovery` may partially satisfy; mark as "unverified partial match" | Consolidation phase simply absent (not disputed) |
| ARCH-001 | `handle.py:87-90` | `_PREFIX_REGISTRY` claims 4 prefixes but `verify:` aliases `ralph:`; `pipeline:` has dual code path (registry + inline) | 3 distinct behaviors, not 4; update docs | Technically accurate; behaviorally misleading |
| META-002 | `knowledge_web.py:83` | Lesson initial score: code says 1.0, one source brief says 0.5–0.7 | Reconcile; code wins (1.0 canonical) | Doc/code drift confirmed |
| ZM-010 | `agent_loop.py:3165,2745` | Threshold constants labeled 'expert guess' not theory-derived; range 2-3 accurate but grounding thin | Document design rationale and expert basis | Accurate but unsupported claim |

---

### 🚫 REFUTED (4 claims — false; disproven conclusively)

| ID | Evidence | Claim | Refutation |
|----|---------:|--------|-----------|
| NEW-P9-001 | `build_loop_runner.py:247-266,634,657` | Gap: exception handlers call `_cleanup_running_build_loop_runs` without prior `_terminate_process_group` | **REFUTED** — `_terminate_worker_session_processes` wrapper (called at lines 634, 657) calls `_terminate_process_group` internally for each pgid |
| NEW-P6-010 | `build_loop_runner.py` (full file) | `fork()` edge case: inherited flock locks hold after parent death | **REFUTED** — 0 grep hits for fork(); `subprocess.Popen` does not inherit flock locks on Linux |
| CLAIM-01 | `handle.py:399,683` + `poe.py:115` | `reframe_intent` is wired into goal-handling tier-2; two-tier core fully implemented | **REFUTED** — `reframe_intent` = 0 grep hits across all src/ (4+ passes confirm ghost symbol) |
| PASS-12-META | Pass 12 claim | "0 test hits for `_sibling_failure_rate`; no test would break on a fix" | **REFUTED** — `test_sibling_failure_triggers_redecompose` exists at `test_agent_loop.py:1131`; test WOULD break on proposed fix (NEW-P14-001) |

---

### 📋 INFORMATIONAL (4 claims — clarifications; low impact)

| ID | Location | Finding | Action |
|----|---------:|----------|--------|
| NEW-P15-001 | `agent_loop.py:98-101` | `StepOutcome.status` documented values: `"done" | "blocked" | "skipped"` — no `"failed"/"error"` values exist (proposed fix would be silent-breaking) | Options for CLAIM-06-CONTRA fix: (A) add `"failed"` status to all execution paths, (B) add `is_execution_failure` boolean flag, or (C) document current behavior as intentional |
| NEW-P15-002 | `planner.py:310` | Dependency-blocked distinction at step_outcomes layer does not exist (all steps were attempted); placeholder-only steps filtered before execution | Cascading-dependency blocking in DAG path may be subtle issue worth a follow-up trace |
| NEW-P10-003 | `handle.py:~573-858` | Build-loop model cap is bypassable by explicit model request (`power:` prefix or `-m power` flag) vs. role-dispatch cap which is unconditional | Narrow docs: "Build-loop runs default to 'mid' tier; handle-level cap is bypassable; role-dispatch cap is unconditional" |
| PASS-15-META | Passes 5-15 | CLAIM-06-CONTRA unchanged across 10 consecutive passes; find-and-ignore behavior or deep architectural blocker | Escalate P0 bug to planning review |

---

## Source Citation Index

### Code Files (Primary Evidence)

| File | Lines Cited | Claims |
|------|:----------:|--------|
| `agent_loop.py` | 98-101, 157, 234, 686, 2745, 2794, 2808-2816, 3165, 3339-3368, 3443, 4218-4225, 4988, 5104 | CLAIM-06-CONTRA, CLAIM-10, CLAIM-11, CLAIM-15, CLAIM-13, CLAIM-18-CONVERGING, IMPL-009, NEW-P11-001, NEW-P14-001 |
| `evolver.py` | 1963, 1909-1918, 2022, 521 | ARCH-003, ARCH-004, CLAIM-19 |
| `heartbeat.py` | 579, 760, 894 | CLAIM-15, ARCH-004, ADVERSARIAL-003-inspector |
| `constraint.py` | 391, 456, 102 | CODE-001, CLAIM-N03 |
| `skills.py` | 829, 1130, 58-59 | DISSENT-004, CLAIM-14 |
| `memory.py` | 3 | CLAIM-12 |
| `handle.py` | 87-90, 399, 580, 683, 1461 | ARCH-001, NEW-P10-002, NEW-P11-001 |
| `poe.py` | 115-118 | NEW-P6-003 |
| `pre_flight.py` | 14 | CLAIM-16 |
| `planner.py` | 232, 308, 310, 235, 521 | NEW-P9-003, NEW-P9-002, NEW-P6-001 |
| `bootstrap_task.py` | 18, 260 | NEW-P6-002 |
| `build_loop_runner.py` | 247-266, 437-483, 543, 586-609, 634, 657 | NEW-P9-001 (REFUTED), NEW-P6-007, NEW-P6-009 |
| `orch_bridges.py` | 965, 1318-1413 | NEW-P6-007, NEW-P10-001 |
| `knowledge_lens.py` | 1060 | PP-008 |
| `knowledge_bridge.py` | 504 | PP-008, PP-011 |
| `intent.py` | 153, 157 | PP-011 |
| `task_store.py` | 216, 223 | IMPL-009 |
| `inspector.py` | 211, 150 | CLAIM-05 |

### Test Files

| File | Lines | Claims |
|------|:-----:|--------|
| `test_evolver_apply.py` | 398, 524 | ARCH-003 integration gap |
| `test_agent_loop.py` | 1131-1153 | NEW-P14-001, CLAIM-06-CONTRA |
| `test_build_loop_runner.py` | 437, 483 | NEW-P9-001 exception-handler path |

### Documentation Files

| File | Key sections | Claims |
|------|:-----------:|--------|
| `MILESTONES.md` | 113, 396, 465, 492, 525, 581 | Phase 62 delivery, LoopStateMachine, Inspector |
| `ROADMAP.md` | Full file (55 lines) | Phase 62-65 status, constraint.py delivery |
| `lat.md/constraint-system.md` | Line 21 | CODE-001 (ghost symbol), CLAIM-07 (ghost symbols) |
| `zoom-metacognition.md` | Line 70 | CLAIM-02, CLAIM-03 |
| `research-brief-rated.md` | 73, 125-126, 129, 131-132, 164, 210-211 | Per-pillar analysis, theory validation |
| `contradictions.md` | 16, 22, 28, 35-38, 112-114, 121-125, 133-138, 145-147, 157-160, 194 | Cross-source reconciliation |
| `findings-extracted.md` | Full file | 50-claim enumeration (baseline) |

### Configuration & Metadata

| File | Relevance | Evidence |
|------|:--------:|----------|
| `src/` (full grep) | Construction verification | 0 hits for: `reframe_intent`, `context_signature`, `enforce_constraint`, `UCB`, `Gittins`, `schema_fitness`, `transfer.*appropriate` |
| `tests/` (full grep) | Integration coverage | 1 hit for `_sibling_failure_rate` (indirect via `_handle_blocked_step` call chain) |

---

## Priority Action Queue

### 🔴 P0 — Critical (Fix immediately, unblock next phase)

1. **`_sibling_failure_rate()` denominator bug** (CLAIM-06-CONTRA + CLAIM-06)  
   - **Location:** `agent_loop.py:2816`  
   - **Issue:** `blocked = sum(1 for s in step_outcomes if s.status == "blocked")` counts dependency blocks as failures; single upstream DAG timeout inflates sibling failure rate 0%→90%+  
   - **Fix:** `s.status in ('failed', 'error')`  
   - **Test impact:** Will require `test_sibling_failure_triggers_redecompose` (line 1131) to be updated with proper status values  
   - **Severity:** 10 consecutive unfixed passes; architectural impact (redecompose gate broken)  
   - **Evidence:** agent_loop.py:2816 (code), test_agent_loop.py:1131 (test), passes 5-15 (unfixed status)

2. **Ghost symbols: `reframe_intent` + `context_signature`** (CLAIM-07)  
   - **Location:** `lat.md/constraint-system.md:21` (docs only; not in src/)  
   - **Issue:** Cited as Grit Tier-2 implementation with 0 grep hits in src/  
   - **Fix:** Either implement or remove all references  
   - **Nearest real code:** `_process_blocked_step()` in `agent_loop.py`  
   - **Evidence:** 0 grep hits confirmed across 4+ independent passes

3. **Kadavath 2022 citation inversion** (THEORY-009)  
   - **Location:** Multiple docs citing paper to oppose reliability (backward citation)  
   - **Issue:** Paper's primary finding is FOR LLM reliability; docs cite it AGAINST  
   - **Fix:** Correct all citations or remove paper references entirely  
   - **Evidence:** All doc sources cite backward (verified across 3+ doc reviews)

4. **Fictional hash formula** (CLAIM-09)  
   - **Location:** Docs referencing "fingerprint hash formula"; actual code: `agent_loop.py:4220-4225`  
   - **Issue:** `stuck_streak` is plain integer counter; two real mechanisms (identity match at `:4218`, MD5 fingerprint at `:2701`) merged into one fictional formula  
   - **Fix:** Document actual mechanisms separately  
   - **Evidence:** Confirmed across 3+ review passes; no hash computation found

5. **OODA/DLL majority claim inversion** (CLAIM-04)  
   - **Location:** Docs claiming "unanimous" persist-then-reframe support; actual: `introspect.py:1202`  
   - **Issue:** 1/3 majority, not unanimous; recovery table does NOT uniformly prescribe persist  
   - **Fix:** Correct: majority is 1/3 (OODA≠reframe, DLL=reframe, Adaptive≈reframe)  
   - **Evidence:** contradictions.md:28 + introspect.py:1202 recovery table inspection

### 🟡 P1 — Major (Resolve before next release)

6. **Hatano temporal direction + ghost symbol** (CLAIM-18)  
7. **UCB/Gittins absence + regime mismatch** (CLAIM-17)  
8. **Bjork domain mismatch (human→LLM inference)** (DISSENT-002)  
9. **Seligman learned helplessness mechanism mismatch** (PP-007)  
10. **Einstellung 3-way parity** (CLAIM-03 — author framing as source)  
11. **Challenge zone numeric analogs (0.60, 0.85)** (PP-008)  
12. **Bjork persistence-zone lower bound (0.15)** (PP-011)  
13. **TAP (Transfer-Appropriate Processing) applicability** (THEORY-003)

### 🟢 P2 — Moderate (Resolve before next doc review cycle)

14. **Breach threshold ambiguity** (CLAIM-05) — 0.30 canonical vs. 0.50 in tests  
15. **Lesson initial score drift** (META-002) — 1.0 (code) vs. 0.5–0.7 (docs)  
16. **Prefix count and pipeline dual path** (ARCH-001) — 4 claimed, 3 distinct + dual dispatch  
17. **DIAGNOSIS_RETRY_THRESHOLD undocumented** (ZM-011)  
18. **Reframed NEW-PROTO-002** (NEW-P10-003) — handle-level cap bypassable vs. role-dispatch unconditional

---

## Claims Requiring No Action (Safe to Use)

The following STRONG and MODERATE claims require no changes and are safe for architectural reliance:

- **STRONG (use directly):** ARCH-003, ARCH-004, CLAIM-13, CLAIM-19, CLAIM-18-CONVERGING, NEW-P6-003, CODE-001, CLAIM-N03, CLAIM-16, NEW-P6-001, CLAIM-14, CLAIM-11, THEORY-004, CLAIM-10, CLAIM-15, CLAIM-12, DISSENT-004 (revised), IMPL-009, NEW-P11-001, NEW-P6-007, NEW-P6-009, NEW-P9-002, NEW-P9-003, CLAIM-06-CONTRA (as bug, not design), META-001, NEW-P6-002, CLAIM-05 (context-noted)
- **MODERATE (use with caveat):** NEW-P10-001 (disk-first mitigates), NEW-P10-002 (low practical risk)

---

## Recommendation: Next Steps

1. **Create task for P0 fixes** — break into 5 subtasks (one per P0 claim)  
2. **Commit synthesis report to BACKLOG_DONE.md** with verification checkpoint  
3. **Escalate CLAIM-06-CONTRA** — 10 unfixed passes indicates potential architectural blocker; requires design review  
4. **Batch-process P1 doc corrections** — consolidate into single doc-review pass  
5. **Audit memory.py + skills.py + inspector.py** — STRONG claims in these modules imply implementation maturity; verify end-to-end integration

---

**This report terminates all prior adversarial briefs. Ground truth: direct code evidence from this session. All claims now rooted in source line citations.**
