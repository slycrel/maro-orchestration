# Phase Audit — 2026-04-13 (Session 19)

Automated audit of 8 high-risk phases marked "done" in ROADMAP, checking whether
main code paths actually call the feature vs. function only existing.

Context: Jeremy suspected multiple phases were surface-level implemented. Session 18
proved this for Phase 45 (recovery planner) — diagnosis built, action side never
wired. Audit scope: does the feature actually fire during normal execution?

## Summary Table

| Phase | Feature | Status | Notes |
|-------|---------|--------|-------|
| **44** | Failure Diagnosis | PARTIAL | `diagnose_loop()` runs at loop-end only. Never called mid-step to inform retry decisions. |
| **45** | Recovery Planner | PARTIAL | `plan_recovery()` computes at loop-end; mostly logged. Auto-apply requires 2nd loop run (too late for complex problems). |
| **46** | Intervention Graduation | CONFIRMED | `run_graduation()` wired into evolver heartbeat cycle (~50 ticks). Runs unsupervised. |
| **57** | Adaptive Tier Escalation | CONFIRMED | cheap→mid→power on retries (agent_loop:502-509). Trajectory check raises session floor at done-rate <50% after 3 steps. |
| **58** | Pre-flight + Milestone Expansion | CONFIRMED | `review_plan()` at agent_loop:1706. Milestone candidates pre-decomposed and injected before execution (line 3360+). |
| **59** | Skill Cost/Latency Telemetry | FIXED | Was ghost feature — `record_skill_outcome()` defined but never called. Wired in session 19. |
| **36** | Observe Dashboard | CONFIRMED | `write_event()` wired into loop lifecycle. `serve_dashboard()` HTTP server functional. |
| **35** | Skill Synthesis/Extraction | CONFIRMED | `synthesize_skill()` + `extract_skills()` + `run_skill_maintenance()` all run at loop-end. |

**Score: 5 fully working, 2 loop-end gaps (44, 45), 1 ghost feature fixed (59).**

## Gap 1: Mid-Loop Diagnosis (Phases 44-45)

`diagnose_loop()` (introspect.py) classifies failures across 10 classes but only
runs in `_finalize_loop()` at agent_loop.py line 2923. It's never called when a
step is blocked mid-loop, so stuck-step decisions are made on heuristics in
`_handle_blocked_step()` rather than the richer diagnosis system.

Phase 62 partially addresses this with convergence tracking + sibling failure
correlation, but uses its own heuristics instead of consulting `diagnose_loop()`.

**Proposed fix:** After N retries on a step (e.g. N=2), call `diagnose_loop()` on
the outcomes-so-far to classify the failure, then route through `plan_recovery()`
to decide retry vs redecompose vs escalate. This would replace (or augment) the
hard-coded heuristics in `_handle_blocked_step()`.

**Cost:** ~1 extra LLM call per blocked step (cheap model). Worth it — the
diagnosis system has far richer failure classification than the 10-line heuristic.

## Gap 2: Recovery Auto-Apply Too Late

Even when `plan_recovery()` returns `auto_apply=True` at loop-end, the recovery
action re-runs the whole goal with adjusted params. This is the right move for
"wrong adapter backend" style failures, but wastes all the work done in the first
loop for complex problems.

**Proposed fix:** Trigger recovery mid-loop when convergence tracking (Phase 62)
says the step isn't converging. Apply the recovery action to the current loop
state, not a fresh restart.

## Fixed Gap: Phase 59 Telemetry (Session 19)

`SkillStats` dataclass has `total_cost_usd`, `avg_latency_ms`, `avg_confidence`
fields. `record_skill_outcome()` updates them. But nothing in `agent_loop.py`
ever called it — only `update_skill_utility()` (Phase 32) and
`record_variant_outcome()` (variant A/B testing).

**Fix applied (commit 49d2326):** Wired `record_skill_outcome()` into both the
success path (line ~1214) and failure path (line ~650) of the main loop.
Confidence string mapped to float: strong=1.0, weak=0.5, inferred=0.3, unverified=0.1.
Cost_usd still hardcoded to 0.0 — TODO: compute from token counts × model pricing.

## What's Actually Well-Wired

- **Graduation** (Phase 46): evolver calls it in heartbeat. Suggestions flow to
  workspace. Fully automated.
- **Tier escalation** (Phase 57): retry-based + trajectory-based both work.
- **Milestone expansion** (Phase 58): pre-flight classifies, execution expands.
  Depth-gated to prevent recursion.
- **Dashboard** (Phase 36): events flow, HTTP serves. Usable.
- **Skill synthesis** (Phase 35): auto-creates skills when loop succeeds with
  no initial match. Extraction runs at loop-end on all outcomes. Maintenance
  promotes proven skills.

These were the phases most at risk of being surface-level. Good news: they're
honestly implemented.
