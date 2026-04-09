# Learning Loop Audit: Does It Actually Learn?

*April 9, 2026 — written in response to Jeremy's suspicion that the orchestration project's learning/evolution phases "shook out to be a fancy system that monitors and visualizes, but potentially doesn't work."*

---

## The Question

> "I'm a little concerned that all shook out to be a fancy system that monitors and visualizes, but potentially doesn't work."
> — Jeremy, April 2026

This audit examines every learning-related component in the orchestration project and classifies each as one of:

- **Active Loop** — actually changes future behavior based on past results
- **Passive Instrument** — observes, records, or displays, but doesn't close the loop
- **Structural Scaffolding** — defines data models and storage, but the loop depends on external triggers

---

## What Was Built (Inventory)

The learning system spans 10+ modules across Phases 22, 32, 37, 44–46, 50–51, and 56, plus all 10 FunSearch/EUREKA/Voyager steals. 250+ unit tests. Every phase marked DONE.

That's the volume answer. Here's the substance answer.

---

## Classification: What Actually Closes the Loop?

### Active Loops (behavior changes automatically)

These components, *if the agent loop runs missions*, will change future behavior without human intervention:

1. **Standing Rule Injection (Phase 56, memory.py)** — `inject_standing_rules(domain)` prepends confirmed patterns into every `_decompose()` call. The promotion path: observation → hypothesis (2+ confirmations) → standing rule. Once promoted, rules are unconditionally applied. This is a real closed loop — past patterns literally alter future planning prompts.

2. **Rule Bypass of Decomposition (Phase 22/rules.py)** — `find_matching_rule(goal)` in `_build_loop_context()` can skip `_decompose()` entirely for established Stage 5 rules. A matched rule means zero LLM cost for that goal. The graduation path: skill (utility ≥ 0.70, use_count ≥ 3) → rule. This loop closes — but only if skills reach the promotion threshold.

3. **Evolver Auto-Apply (evolver.py)** — Suggestions with high confidence are automatically applied. `_apply_suggestion_action()` can mutate skills, create guardrails, or add tiered lessons. Wired to heartbeat every 10 ticks. This loop closes — but the quality of suggestions depends on having enough outcome data to analyze.

4. **Skill Circuit Breaker + Rewrite (Phase 32/skills.py)** — 3 consecutive failures → circuit OPEN → `rewrite_skill()` invokes LLM to revise the skill body → circuit HALF_OPEN (probationary). This is a genuine self-repair loop. On success, circuit closes. On failure during probation, circuit re-opens.

5. **Skill Synthesis (Phase 37/evolver.py)** — When the agent loop completes successfully but had no matching skill at start, `synthesize_skill()` creates a new provisional skill from the goal + outcome. Literally learns new capabilities from experience. But only fires when `_had_no_matching_skill` flag is set.

6. **Auto-Recovery (Phase 45/introspect.py → agent_loop.py)** — When a loop ends stuck: diagnose → pick low-risk auto-apply recovery plan → re-run with adjusted parameters. Recursion-guarded. This closes the loop within a single mission, not across missions.

7. **Intervention Graduation (Phase 46/graduation.py)** — Scans `diagnoses.jsonl` for 3+ occurrences of the same failure class → generates high-confidence evolver Suggestion → auto-applied next evolver run. Turns repeated failures into permanent fixes.

8. **Decision Journal Injection (Phase 56/memory.py)** — `inject_decisions(goal, domain)` appends relevant prior decisions to every decompose call. TF-IDF ranked. Past reasoning informs future reasoning.

9. **A/B Variant Retirement (skills.py)** — After ≥5 trials, losing variants are retired. Winners become the canonical skill. Real competitive selection.

### Passive Instruments (observe but don't close the loop)

These components record state but don't automatically change behavior:

1. **`poe-knowledge status` (knowledge.py)** — Dashboard showing all 5 crystallization stages. Pure visualization. Shows counts, candidates, graveyard. Doesn't trigger any promotions — just tells you what could be promoted.

2. **`poe-knowledge promote` (knowledge.py)** — Lists available promotions but is read-only. Someone (human or another process) has to act on it.

3. **`poe-observe serve` (observer)** — Dashboard panel for diagnoses. Display only.

4. **`poe-introspect` CLI (introspect.py)** — Manual diagnosis of a specific loop. Useful forensics, but the automated version (wired into `_finalize_loop`) is what matters.

5. **Thinkback Report Display (thinkback.py)** — The hindsight analysis produces a `ThinkbackReport` with per-step quality ratings and counterfactuals. But the *actionable* part — `_save_thinkback_lessons()` — only fires when `--save` flag is passed or when wired into the passes pipeline. The report itself is passive.

6. **Memory Decay (memory.py)** — Lessons decay over time (0.85x/day). This removes old patterns but doesn't add new ones. It's more garbage collection than learning.

7. **Skill Statistics (skills.py/SkillStats)** — Tracks use_count, successes, failures, cost, latency. These feed into the active loops above, but the stats themselves are just measurement.

### Structural Scaffolding (data models waiting for data)

These exist as infrastructure but their value scales with volume of real usage:

1. **Outcome Recording (memory.py)** — `record_outcome()` appends to `outcomes.jsonl`. Foundation of everything above. Without outcomes, nothing learns.

2. **Tiered Lesson Storage (memory.py)** — Short/medium/long tiers with decay. The storage is solid. But lessons only accumulate from `reflect_and_record()` calls in the agent loop.

3. **Island Model (skills.py)** — 4 islands for diversity, bottom-half culling per evolver run. Brilliant in theory. But with how many skills in the pool? If there are 6 total skills across 4 islands, you're culling 1 skill per island. The model needs density to work.

4. **Replay-Based Fitness Oracle (strategy_evaluator.py)** — Deterministic TF-IDF cosine over outcomes.jsonl. No LLM in the eval path. Beautiful design. But scoring quality depends on having a representative outcomes.jsonl to replay against.

5. **Majority-Vote Pseudo-Labels (memory.py)** — k-sample lesson extraction where only majority-agreed lessons are promoted. Eliminates noise. But only triggers when `extract_lessons_via_llm(k_samples=N)` is called with N > 1.

---

## The Critical Questions

Everything above has been classified by design. But Jeremy's concern isn't about design — it's about *reality*. The critical questions are empirical:

### 1. How many outcomes exist in `outcomes.jsonl`?

Every active loop depends on this file having enough data. If the system has run 10 missions total, the evolver is analyzing 10 data points. That's not enough for pattern detection, island culling, or A/B variant retirement. **This is the single most important number to check.**

### 2. How many skills have been promoted from provisional → established?

The skill lifecycle is: synthesized (provisional) → auto-promoted (utility ≥ 0.70, use_count ≥ 5) → established. If no skills have been promoted, the entire Stage 4→5 path (skill → rule) has never fired. Promotion requires 5+ uses with 70%+ success — has any skill been used 5 times?

### 3. How many standing rules exist?

Phase 56's observation → hypothesis → standing rule pipeline requires 2+ confirmations of the same pattern. If `standing_rules.jsonl` is empty or has 1-2 entries, the system hasn't accumulated enough signal. Not a design flaw — a runtime data scarcity issue.

### 4. How many evolver suggestions have been applied vs. generated?

If `suggestions.jsonl` has 50 suggestions and 3 are applied, the system generates ideas but doesn't act on them. If the ratio is reversed, it's working. The `applied: true/false` flag tells the story.

### 5. Has any skill ever been rewritten?

The circuit breaker → rewrite path (3 failures → OPEN → LLM rewrite → HALF_OPEN) is one of the most concrete learning loops. If `skills.jsonl` has no skills with `circuit_state: "half_open"` or with a non-zero rewrite count, this loop has never fired.

### 6. Has any A/B variant test completed?

`create_skill_variant()` creates challengers; `retire_losing_variants()` picks winners after ≥5 trials. If no variants exist or none have been retired, competitive selection hasn't happened.

### 7. Is the heartbeat loop actually running regularly?

The evolver is wired to heartbeat every 10 ticks. Harness optimizer every 50 ticks. If the heartbeat itself isn't running (or runs rarely), these periodic learning passes never fire.

---

## The Diagnosis

Based purely on the codebase (without access to the live system's `memory/` directory), here's the honest assessment:

### What's genuinely well-designed

The architecture is sophisticated and internally consistent. The 5-stage crystallization path (Fluid → Lesson → Identity → Skill → Rule) is a real graduated autonomy model with concrete code behind every transition. The circuit breaker pattern prevents a bad skill from being used forever. The FunSearch/EUREKA steals (island model, replay oracle, brevity penalty, A/B variants) are thoughtfully adapted, not cargo-culted.

The unit tests are comprehensive (250+), which means the individual functions work correctly in isolation.

### Where Jeremy's concern likely lands

**The system is a well-tested collection of components that have probably never experienced enough throughput to close loops in practice.**

Here's why:

- **Cold start problem.** Every learning mechanism requires accumulated data. Standing rules need 2+ confirmations. Skill promotion needs 5+ uses at 70%+ success. A/B retirement needs 5+ trials. Island culling needs enough skills per island to have a meaningful bottom half. If the system has been doing mostly infrastructure development (building itself) rather than running diverse missions against real problems, the data stores are likely sparse.

- **Unit tests ≠ integration proof.** The tests verify: "given this fake outcome, does the evolver generate a reasonable suggestion?" They don't verify: "after 100 real missions, did the system's decisions measurably improve?" There's no end-to-end learning test that starts naive, runs a workload, and verifies that later runs are better than early runs.

- **The monitoring is the visible part.** `poe-knowledge status`, `poe-observe serve`, `poe-introspect` — these produce visible dashboards. When you check on the system, you see dashboards. The actual learning (standing rule injection, rule bypass, evolver auto-apply) is invisible by design — it just silently makes the next decompose call better. So the *perception* is "it monitors" even if it's also learning, because the monitoring is what you see.

- **Twelve layers of indirection.** Outcome → lesson → hypothesis → standing rule → decompose injection. Outcome → skill synthesis → provisional → auto-promote → established → rule graduation → rule bypass. Each transition has conditions. If any step in the chain has a threshold that hasn't been met, the whole pipeline stalls. And there are 5-6 such chains, each with 3-5 transitions.

### The likely truth

The system probably *does* learn — at the individual component level. A skill that fails 3 times probably does get its circuit broken and rewritten. An evolver suggestion with high confidence probably does get applied. But the **full graduation pipeline** (raw outcome → crystallized rule that bypasses LLM entirely) has likely never completed end-to-end with real data, because the throughput required to drive a signal all the way through hasn't been achieved.

This isn't a design failure. It's a bootstrapping problem. The system was built to learn from operational experience, but most of its operational experience has been... building itself.

---

## What Would Verify This

To turn this from hypothesis into fact, check the live system:

```bash
# 1. How many outcomes exist?
wc -l memory/outcomes.jsonl

# 2. How many skills exist and what tier?
cat memory/skills.jsonl | python3 -c "
import json, sys
skills = [json.loads(l) for l in sys.stdin]
tiers = {}
for s in skills:
    t = s.get('tier', 'unknown')
    tiers[t] = tiers.get(t, 0) + 1
print(tiers)
"

# 3. How many standing rules?
wc -l memory/standing_rules.jsonl 2>/dev/null || echo "File doesn't exist"

# 4. Suggestions applied ratio
cat memory/suggestions.jsonl | python3 -c "
import json, sys
sugs = [json.loads(l) for l in sys.stdin]
applied = sum(1 for s in sugs if s.get('applied'))
print(f'{applied}/{len(sugs)} applied')
"

# 5. Any rules graduated from skills?
wc -l memory/rules.jsonl 2>/dev/null || echo "File doesn't exist"

# 6. Hypotheses vs standing rules
wc -l memory/hypotheses.jsonl memory/standing_rules.jsonl 2>/dev/null

# 7. Any skill variants?
cat memory/skills.jsonl | python3 -c "
import json, sys
skills = [json.loads(l) for l in sys.stdin]
variants = [s for s in skills if s.get('variant_of')]
print(f'{len(variants)} variants of {len(skills)} total skills')
"
```

---

## Recommendations

### 1. Run the diagnostics above on the live system
Before changing anything, get the empirical data. Jeremy's instinct may be exactly right — or the system may have more accumulated learning than expected. The numbers decide.

### 2. Create an end-to-end learning test
Not a unit test. A scenario: "Start with zero skills and lessons. Run 20 missions of increasing complexity. Verify: (a) skills were synthesized, (b) at least one was promoted, (c) the evolver generated and applied suggestions, (d) later missions used fewer tokens than early ones." This is the missing proof.

### 3. Feed it a real workload
The learning system was designed for operational throughput. If it's only been running infrastructure-building missions, it hasn't had the diversity of experience to form patterns. Give it a batch of varied real tasks — research, build, ops — and let the loops turn.

### 4. Lower promotion thresholds temporarily
If the data shows sparse stores, consider temporarily reducing: skill promotion from 5 uses to 3, standing rule confirmation from 2 to 1, A/B variant retirement from 5 trials to 3. Let the loops complete at lower confidence to verify the mechanics work, then raise thresholds back.

### 5. Add a "learning health" heartbeat metric
Not a dashboard — a single number that answers "is the system learning?" Something like: `(new_skills + promoted_skills + applied_suggestions + new_standing_rules) / total_outcomes` over a rolling window. If this ratio is 0, the learning system is dormant regardless of how many dashboards are green.

---

## Summary

The architecture is sound. The components are individually tested and well-designed. The concern is valid but likely misdiagnosed: it's probably not that the system "doesn't work" — it's that the system hasn't had enough operational throughput to close the longer feedback loops. The monitoring is visible; the learning is silent. So it *looks like* all you got was monitoring, even if the learning plumbing is there and functional.

The fix isn't architectural. It's operational: run real missions, check the data stores, and verify the loops are turning.
