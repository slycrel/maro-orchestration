---
status: record
---

# Playbook surprise read (2026-08-02)

**Status: reading artifact** — verbatim snapshot of
`~/.maro/workspace/playbook.md` (the evolver-maintained "Director's
Playbook", last self-updated 2026-07-29), taken 2026-08-02 so it renders in
the Reading tab. The live file is untouched.

This is the second self-maintained corpus getting an operator read (first:
the 2026-07-31 lesson-corpus read). Stakes are higher here: unlike lessons,
which inject selectively, **this document is injected into every director
and decompose call** — a wrong entry taxes every run.

## How to do the read

Same instrument as the lesson read, both signals welcome:

- **Surprise** — entries you would not have written yourself (taught the
  system something you didn't carry).
- **Argue** — entries you'd push back on. Chunk 1 of the lesson read showed
  the reaction data is at least as valuable as the surprise count.

React with entry numbers (`P1–P24`). 24 entries, ~10 minutes.

## Mechanical companion notes (before your read)

- **Three evolver entries are truncated mid-sentence** (P9 ends "adjust p",
  P10 ends "Do all of", P17 ends "goal no") — the evolver writes clipped
  lines into the director's context on every run.
- **The drift warnings are frozen alarms** (P19, P20, P23): three
  cost-rise-vs-baseline warnings from different eras all still "live",
  telling every run to consider rollbacks that may long since have
  happened. The playbook has no retirement path — nothing has ever exited.
- **P18 is a July run-debugging TODO** (two stuck haiku tasks) sitting in
  permanent every-run context.
- The calibration entries (P21, P22) are near-duplicates of each other.

---

## The playbook, numbered

### Decomposition

**P1** — Research goals benefit from a gather → synthesize → verify structure.

**P2** — Narrow goals (≤15 words) should get 1-4 steps, not more.

**P3** — Wide/deep goals should use staged-pass decomposition.

**P4** — More atomic steps > fewer broad steps. One file or one command per step.

### Execution

**P5** — If a step fails 3 times, the problem is usually the decomposition, not the execution.

**P6** — Token budgets for build tasks should be ~2x research tasks.

**P7** — Always verify outputs before recording as done.

**P8** — Before Write/Bash calls targeting file creation, validate: (1) target path is within /home/clawd/.maro/workspace/ unless explicitly authorized elsewhere, (2) parent directory exists or can be created, *(from evolver:8f7419c8-00)*

**P9** — When a step is marked 'blocked', add an investigative prompt: 'Why is this blocked? Is it a permission error, missing dependency, malformed task spec, or environment issue?' Attempt recovery (adjust p *(from evolver:8f7419c8-03)*

**P10** — Before execution, require all agenda goals to include explicit success criteria: 'Task succeeds when: [measurable condition]'. Reject vague goals like 'System file-relocation diagnostic' or 'Do all of *(from evolver:fdf888c3-00)*

### Cost

**P11** — Execution floor is MID (2026-07-21 unification decree); POWER at orchestrator/planner/reviewer decision points; CHEAP only for non-agentic calls (classify, triage, curation).

**P12** — Enable extended thinking for decompose (high) and advisory calls (mid).

**P13** — Narrow goals should skip multi-plan (saves 3 LLM calls).

### Quality

**P14** — The verification loop is the highest-leverage investment.

**P15** — Inspector friction signals should be acted on, not just logged.

**P16** — Standing rules are zero-cost — promote aggressively when validated.

### Learned

**P17** — For agenda tasks with explicit 'goal' state (achieved/NOT-achieved), define acceptance criteria upfront and validate against them before marking task complete. If completion steps are done but goal no *(from evolver:8f7419c8-01)*

**P18** — Investigate tasks bef5ea6e and b22b3ece (identical 'write a 3-line haiku about a fresh hermes install to file hermes_haiku.t' goals)—both stuck on identical step. This is a signal of either (1) inhere *(from evolver:8f7419c8-02)*

**P19** — avg_cost_usd has risen 21.6% from baseline (1.0350 vs 0.8515) for 7 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-3c1405d9)*

**P20** — avg_cost_usd has risen 42.1% from baseline (1.6163 vs 1.1372) for 4 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-571f333f)*

**P21** — CONFIDENCE MISCALIBRATION in category 'observation': self-reported confidence 0.92 but empirical pass rate 0.50 (3/6 verified). Reduce LLM confidence prompts for this category or tighten auto-apply th *(from evolver:calibration-12738e5f)*

**P22** — CONFIDENCE MISCALIBRATION in category 'observation': self-reported confidence 0.90 but empirical pass rate 0.43 (3/7 verified). Reduce LLM confidence prompts for this category or tighten auto-apply th *(from evolver:calibration-b9922c90)*

**P23** — avg_cost_usd has risen 53.2% from baseline (3.5405 vs 2.3106) for 7 consecutive cycles. Recent evolver changes may be degrading quality — consider rolling back recent auto-applied suggestions. *(from evolver:drift-92fc56aa)*

### Signals

**P24** — [Signal] Complete the AI-failure-task-patterns catalog v2, then analyze failure families to extract patterns that could inform Mode 1/2/3 taxonomy refinement and self-improving system robustness. *(from evolver:sig-94a153df)*
