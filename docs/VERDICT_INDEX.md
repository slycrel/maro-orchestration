# Adversarial Verification — Verdict Quick Reference

**Generated:** 2026-07-02 from 15-pass synthesis  
**Full report:** `docs/ADVERSARIAL_VERIFICATION_SYNTHESIS.md`

---

## Verdict Distribution (84 claims)

```
STRONG    48 (54%) ████████████████████
MODERATE   2 (2%)  █
WEAK       8 (9%)  ███
CONTESTED 18 (20%) ███████
REFUTED    4 (4%)  ██
INFO       4 (4%)  ██
```

---

## By Verdict: Line Lookup

### 🟢 STRONG (48) — Safe to use directly

| Tier | IDs | Key Evidence |
|------|-----|---|
| **Arch** | ARCH-003, ARCH-004, CLAIM-13, CLAIM-19, CLAIM-18-CONVERGING, NEW-P6-003 | `evolver.py:1963`, `heartbeat.py:760`, `agent_loop.py:3344` |
| **Constraint** | CODE-001, CLAIM-N03, CLAIM-16, NEW-P6-001 | `constraint.py:391`, `pre_flight.py:14`, `planner.py:310` |
| **Retry/Logic** | CLAIM-14, CLAIM-11, THEORY-004, CLAIM-10, CLAIM-15 | `skills.py:1130`, `agent_loop.py:3165,2745` |
| **Memory** | CLAIM-12, DISSENT-004, IMPL-009, NEW-P11-001 | `memory.py:3`, `skills.py:829`, `task_store.py:216` |
| **Process** | NEW-P6-007, NEW-P6-009, NEW-P9-002, NEW-P9-003, **CLAIM-06-CONTRA** | `build_loop_runner.py:543`, `planner.py:308`, `agent_loop.py:2816` ⚠️ P0 BUG |
| **Utility** | META-001, NEW-P6-002, CLAIM-05 | `adversarial.md chain`, `bootstrap_task.py:18`, `inspector.py:211` |

### 🟡 MODERATE (2) — Use with caveat

| Claim | Caveat | Line |
|-------|--------|------|
| NEW-P10-001 | Disk-first mitigates memory concern | `orch_bridges.py:1380-1393` |
| NEW-P10-002 | Low practical risk; correctness gap exists | `handle.py:580` (outlier form) |

### ⚠️ WEAK (8) — Validate before use

| ID | Risk | Fix |
|----|------|-----|
| NEW-P9-001 | Orphan cleanup assumed on next launch | Document pattern |
| CLAIM-N05 | CFT criterion not met | Reclassify |
| CLAIM-N06 | Substrate mismatch | Qualify language |
| CLAIM-N01 | LLM transfer unvalidated | Empirical test first |
| THEORY-005 | Interleaving inapplicable (stateless) | Remove or reclassify |
| CLAIM-02 | Author framing as source | Document exploratory |
| PP-007-CONTRA | Hedged; correct analog documented | Keep hedge |
| ZM-011 | Undocumented config | Add to config.yml |

### 🔴 CONTESTED (18) — Do NOT use; P0/P1 fixes required

#### **P0 — Immediate (5)**

| ID | Location | Problem | Severity |
|----|----------|---------|----------|
| **CLAIM-06-CONTRA** | `agent_loop.py:2816` | `_sibling_failure_rate` bug (0%→90%+ on single timeout) | 10 passes unfixed |
| **CLAIM-07** | `lat.md:21` | Ghost symbols (`reframe_intent`, `context_signature`) | 0 grep hits |
| **THEORY-009** | Docs | Kadavath citation 180° inverted | All sources affected |
| **CLAIM-09** | `agent_loop.py:4220` | Fictional hash formula | 3+ passes confirmed |
| **CLAIM-04** | `introspect.py:1202` | Majority claim inverted (1/3 not unanimous) | Recovery table check |

#### **P1 — Major (8)**

| ID | Problem | Source |
|----|---------|--------|
| CLAIM-18 | Temporal direction + ghost symbol | `pre_flight.py:14` |
| CLAIM-17 | UCB/Gittins + regime mismatch | 0 grep hits |
| DISSENT-002 | Domain mismatch (human→LLM) | Bjork citation |
| PP-007 | Neurological mechanism inapplicable | Seligman citation |
| CLAIM-03 | Author framing as canon | `zoom-metacognition.md:70` |
| PP-008 | False numeric analogs (0.60, 0.85) | `knowledge_lens.py:1060` |
| PP-011 | Bjork lower bound (0.15) invented | `intent.py:153` |
| THEORY-003 | TAP incompatible with stateless LLM | 0 grep hits |

#### **P2 — Documentation (5)**

| ID | Problem | Check |
|----|---------|-------|
| CLAIM-08 | PFL consolidation unverified | `introspect.py:1288` |
| ARCH-001 | 4 claimed, 3 distinct + dual path | `handle.py:87-90` |
| META-002 | Code/docs drift (1.0 vs 0.5–0.7) | `knowledge_web.py:83` |
| ZM-010 | Grounding thin (expert guess) | Constants hardcoded |
| NEW-P10-003 | Cap bypass not documented | `handle.py:~573-858` |

### 🚫 REFUTED (4) — False; disproven

| ID | Claim | Counter-Evidence |
|----|-------|---|
| NEW-P9-001 | Gap exists (exception handlers don't kill processes) | `build_loop_runner.py:634,657` call `_terminate_worker_session_processes` (wrapper calls `_terminate_process_group`) |
| NEW-P6-010 | Fork edge case with flock | 0 grep hits for fork(); `subprocess.Popen` doesn't inherit flock |
| CLAIM-01 | `reframe_intent` wired | 0 grep hits confirmed (4+ passes) |
| PASS-12-META | No test for `_sibling_failure_rate` | Test `test_sibling_failure_triggers_redecompose` at `test_agent_loop.py:1131` |

### 📋 INFORMATIONAL (4) — Clarifications

| ID | Finding | Action |
|----|---------|--------|
| NEW-P15-001 | No `"failed"/"error"` status values exist | 3 options for fix (A/B/C) |
| NEW-P15-002 | Dependency-blocked distinction absent at step_outcomes | Subtle DAG cascading possible |
| NEW-P10-003 | Handle-level cap bypassable | Narrow docs: role-dispatch cap unconditional |
| PASS-15-META | P0 bug unchanged 10 passes | Escalate to planning review |

---

## Priority Matrix

```
CRITICAL (P0)     MAJOR (P1)      MODERATE (P2)
─────────────     ──────────      ─────────────
CLAIM-06-CONTRA   CLAIM-18        CLAIM-08
CLAIM-07          CLAIM-17        ARCH-001
THEORY-009        DISSENT-002     META-002
CLAIM-09          PP-007          ZM-010
CLAIM-04          CLAIM-03        NEW-P10-003
                  PP-008
                  PP-011
                  THEORY-003
```

---

## Survival Rate by Category

| Category | Total | Survived (STRONG/MODERATE) | Need Fix | % Safe |
|----------|:-----:|:------------------------:|:--------:|--------|
| Architecture | 6 | 6 | — | 100% ✓ |
| Constraint/Exec | 4 | 4 | — | 100% ✓ |
| Retry Logic | 5 | 5 | — | 100% ✓ |
| Memory/Learning | 4 | 4 | — | 100% ✓ |
| Process Mgmt | 5 | 4 | 1 | 80% (NEW-P9-001 weak) |
| **Bug Claims** | **5** | **1** | **4** | **20%** ⚠️ |
| Theory/Citation | 31 | 2 | 29 | **6%** 🔴 |
| **Overall** | **84** | **50** | **34** | **59%** |

---

## How to Use This Index

1. **For architectural decisions:** Read STRONG claims directly (section above)
2. **For code changes:** Check CONTESTED/WEAK sections for your module
3. **For docs edits:** Cross-reference P0/P1/P2 action queues against your topic
4. **For testing:** Verify STRONG claims hold via regression tests
5. **For theory usage:** Validate WEAK claims before relying; avoid CONTESTED entirely

---

## Key Findings Summary

✓ **48 STRONG claims** → Core execution, memory, constraint, process management are well-grounded  
⚠️ **18 CONTESTED claims** → Heavy in theory/citation section; P0 fixes are code bugs (not theory)  
🚫 **4 REFUTED claims** → False concerns; real mechanisms were already present  
❌ **34 need action** → Mostly docs corrections (P1/P2); P0 has 5 code-level fixes

---

*Synthesis date: 2026-07-02 | Source: 15 adversarial passes (2026-06-28 through 2026-06-29) | Full details: ADVERSARIAL_VERIFICATION_SYNTHESIS.md*
