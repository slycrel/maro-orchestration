# K0 — Knowledge Layer Baseline

*Empirical snapshot of Poe's learning data stores. April 9, 2026.*

This document answers the question from the Learning Loop Audit (doc 06): **how sparse are the live data stores?**

---

## TL;DR

The data stores are populated but contain almost exclusively **test fixture data**. No real operational missions have generated learning signal. The learning pipeline is structurally complete but has never processed live traffic. This is a bootstrapping problem, not a design flaw.

---

## Data Store Inventory

### outcomes.jsonl — 688 entries

| Field | Finding |
|-------|---------|
| Total entries | 688 |
| Status breakdown | done: 606, stuck: 75, interrupted: 7 |
| `success` field | **Not present on any entry** (0/688) |
| Task types | agenda: 572, general: 92, research: 16, build: 8 |
| Date range | All 2026-04-04 (single day) |
| Source | Test runs — goals like "complete all steps cleanly", "Gather and analyze the dataset" |

**Assessment:** Test fixture data. No real missions. The absence of a `success` boolean field means downstream components checking `outcome.get('success')` will never find signal. This may be a schema gap worth investigating.

### lessons.jsonl — 623 entries

| Field | Finding |
|-------|---------|
| Total entries | 623 |
| Tier distribution | unknown: 623 (100%) |
| Content | Dry-run lessons: "[dry-run lesson] agenda task succeeded: ..." |

**Assessment:** All tier "unknown" — the tiered lesson system (short/medium/long with decay) has never processed real lessons. The 5-stage crystallization pipeline (Fluid → Lesson → Identity → Skill → Rule) has no input.

### skills.jsonl — 40 entries

| Field | Finding |
|-------|---------|
| Total entries | 40 |
| Tier: provisional | 38 |
| Tier: established | 2 |
| Promoted | 0 |
| Variants (A/B) | 0 |
| Utility scores | None set (all `?`) |
| Use counts | Mostly 0; max is 5 ("updatable skill") |
| Names | "myskill", "goodskill", "skill 0"–"skill 4", "hash storage test", etc. |

**Assessment:** Test fixtures. No real skills have been synthesized, promoted, or A/B tested. The skill lifecycle (synthesis → provisional → established → promoted, circuit breaker, variants) has never fired on live data.

### standing_rules.jsonl — DOES NOT EXIST

The file is not present in `memory/`. The hypothesis → standing rule promotion path (2+ confirmations) has never produced output. `inject_standing_rules()` has nothing to inject.

### rules.jsonl — DOES NOT EXIST

No skills have graduated to Stage 5 hardcoded rules. The rule bypass path (skip LLM entirely) has never been exercised.

### events.jsonl — 3,156 entries

Largest store. Event type distribution not fully analyzed but likely test-generated given the timeline correlation with other stores.

### diagnoses.jsonl — 572 entries

Failure classification data present but generated from test runs, not operational failures.

### Other Stores

| File | Entries | Notes |
|------|---------|-------|
| handle_inputs.jsonl | 8,505 | Input routing log — largest file |
| calibration.jsonl | 2,124 | LLM calibration data |
| step-costs.jsonl | 1,720 | Token/cost tracking |
| canon_stats.jsonl | 477 | Canon statistics |
| sandbox-audit.jsonl | 561 | Sandbox security audit |
| mission-log.jsonl | 68 | Mission summaries (test goals) |
| background-tasks.jsonl | 68 | Background task log |
| persona-outcomes.jsonl | 40 | Persona performance tracking |
| hook-log.jsonl | 32 | Hook execution log |
| skill-stats.jsonl | 9 | Skill performance stats |

### lat.md/ — 9 concept nodes + index

```
lat.md/
  lat.md (index)
  self-improvement.md
  checkpointing.md
  intent-classification.md
  memory-system.md
  poe-identity.md
  quality-gates.md
  core-loop.md
  constraint-system.md
  worker-agents.md
```

Wiki-link format, human-readable. 9 nodes covering core orchestration concepts. This is the Web (associative view) foundation — small but structurally sound.

---

## Learning Loop Status

Based on empirical data, here's the actual status of each learning loop:

| Loop | Status | Evidence |
|------|--------|----------|
| Standing Rule Injection | **No data** | `standing_rules.jsonl` doesn't exist |
| Rule Bypass (skip LLM) | **No data** | `rules.jsonl` doesn't exist |
| Skill Circuit Breaker | **Never fired** | 0 utility scores, all skills at baseline |
| Skill Synthesis | **Never fired** | Only test fixture skills exist |
| Skill Promotion/Demotion | **Never fired** | 0 promotions, 2 "established" are test fixtures |
| A/B Variant Testing | **Never fired** | 0 variants created |
| Island Model Culling | **Never fired** | No variant diversity |
| Auto-Recovery | **Unknown** | 75 "stuck" outcomes but from test data |
| Evolver Auto-Apply | **Unknown** | No suggestions.jsonl found |
| Intervention Graduation | **Never fired** | Would need repeated real failures |
| Lesson Decay/Graveyard | **Never fired** | All lessons are tier "unknown" |
| Hypothesis → Standing Rule | **Never fired** | No standing rules produced |

**Bottom line:** Every learning loop is structurally implemented and unit-tested, but none have processed real operational data. The system is a fully built engine that has never left the garage.

---

## What This Means for K1+

1. **The cold-start problem is real.** Before choosing a knowledge foundation (K1), we need real missions generating real signal. Otherwise we're building knowledge infrastructure on top of test data.

2. **The Captain's Log is even more important than anticipated.** When we start feeding real missions, we need to see what the learning system does with them. The log provides that visibility from day one.

3. **Schema gaps to address:**
   - `success` field missing from outcomes — downstream components may assume it exists
   - All lessons are tier "unknown" — tier classification may not be wired to real lesson recording
   - Utility scores not set on skills — the scoring path may not be connected

4. **The `standing_rules.jsonl` absence is notable.** This is the file that `inject_standing_rules()` reads from. The injection function exists but has nothing to inject. Not a bug — just no data yet.

5. **lat.md is solid foundation for the Web view.** 9 well-structured concept nodes with wiki-links. Expanding this is straightforward once the knowledge layer architecture is chosen.

---

## Recommended Next Steps

1. **Build the Captain's Log** — visibility before throughput
2. **Run 10-20 real missions** through the system to generate authentic learning data
3. **Re-run this diagnostic** after real data exists to verify loops actually close
4. **Investigate schema gaps** (success field, lesson tiers) before relying on them
5. **Proceed to K1** (choose knowledge foundation) once we have empirical evidence of what the learning system produces with real data
