# Poe — Success Criteria

*Version 2.1 | 2026-04-04 | Synthesized from BACKLOG.md, ROADMAP.md phases 40–56, inspector.py, evolver.py, POE_IDENTITY.md, lat.md, CHANGELOG.md*

---

## North-Star

> "A self-improving, autonomous agent that wakes up with tasks, executes them without hand-holding, and gets measurably better over time."

Success = the system reliably executes missions, improves from experience, and costs predictably — without requiring human intervention except for genuine exceptions.

---

## Quick-Reference: Pass/Fail Thresholds by Phase

Binary gates — **PASS** requires ALL columns met; any miss = **FAIL**.

| Phase | Metric | Baseline (current) | Target (PASS) | FAIL condition |
|-------|--------|--------------------|---------------|----------------|
| P23 Observability | Dashboard renders live friction signals | Not tracked | Dashboard live; `poe-observe` returns data without manual query | Not queryable OR requires manual log parsing |
| P23 Observability | 7-day rolling inspector good% | Not measured | ≥70% good, ≤10% poor | good% < 70% OR poor% > 10% over any 7-day window |
| P24 Messaging | Slack/Signal round-trip latency | Not measured | message → goal → response ≤30s in ≥9/10 tests | Any run > 30s OR < 9/10 pass |
| P25 Ops Hardening | Unhandled exceptions in 48h soak | Unknown | 0 unhandled exceptions | ≥1 unhandled exception |
| P25 Ops Hardening | Restart recovery time | Unknown | ≤60s from crash to operational | > 60s recovery time |
| P27 Sub-Goals | Sub-goal dependency resolution | Manual | Auto-resolves without human intervention | Any sub-goal requires manual step to unblock |
| P28 Personality | Persona consistency across runs | Not measured | Score ≥0.80 over 10 consecutive runs | Score < 0.80 in any 10-run window |
| P29 Psychology | Research outputs with confidence | Not measured | ≥3 outputs at confidence ≥0.70 stored in memory | Fewer than 3 qualifying outputs |
| P38 Subpackage | Entry-points importable; poe-doctor clean | Partial | All entry-points import cleanly; `poe-doctor` exits 0 | Any import error OR poe-doctor failure |
| P42 Nightly Eval | Evolver firing mode | Count-based (not trend) | Trend-based nightly eval fires and adjusts evolver | Still count-based after P42 ships |
| P46 Intervention | Autonomy ratio (no mid-run escalation) | Not logged | ≥90% of AGENDA steps complete without human relay | < 90% autonomy rate over any 10-run window |
| ALL runs | Task completion rate | Not aggregated | ≥85% of planned steps complete per session | < 85% completion in any session |
| ALL runs | Cost — NOW goal | Not computed | ≤$0.25/session (acceptable ≤$1.00) | > $1.00/session |
| ALL runs | Cost — AGENDA goal | Not computed | ≤$1.50/session (acceptable ≤$3.00) | > $3.00/session |
| ALL runs | Inspector alignment_score | Not tracked cross-session | ≥0.70 per run | < 0.70 alignment_score |
| ALL runs | Lesson extraction | `times_applied` always 0 | ≥1 lesson/run; applied_rate ≥0.30 over 30 days | 0 lessons extracted OR applied_rate < 0.30 |
| ALL runs | Session summary coverage | 0% | session_summary.json present for 100% of sessions | Any session without session_summary.json |
| Monthly | Total cost | Not computed | ≤$50/month | > $50/month |

---

## Dimension 1: Reliability

### Criterion
Poe completes ≥85% of AGENDA goals within their planned step budget. `flag_stuck` is never called without a prior retry attempt. Zero silent failures — every terminal failure surfaces a root cause in the session log. Inspector `overall_quality = good` rate ≥70% on a 7-day rolling basis; `poor` rate ≤10%.

### Current State
- Inspector enforces 7 friction signals with breach thresholds, but no aggregate pass/fail rate is tracked across sessions.
- `flag_stuck` can be called without a preceding retry — no enforcement in agent_loop.py.
- `empty_model_output` events occur at ~7.6% rate and are unalerted (silent failures exist).
- Phase 44 (Failure Classifier) and Phase 45 (Recovery Planner) are DONE and feed inspector.py.
- Phase 54 (Session Checkpointing) shipped but replayability consistency has not been measured.

### Gap
- Add session-level pass/fail rate tracking (outcomes.jsonl + a rolling summary counter).
- Enforce retry-before-stuck in agent_loop.py — at least one replan or `create_team_worker` before `flag_stuck`.
- Wire `empty_model_output` alert in heartbeat loop.
- Add 7-day rolling good%/poor% aggregate line to inspector output (Phase 23 close-out).
- Measure checkpoint replayability consistency (Phase 54 follow-up).

---

## Dimension 2: Autonomy

### Criterion
Poe executes a multi-step AGENDA mission end-to-end without any human prompt after goal submission, verified by zero mid-run escalations in ≥9 of 10 consecutive runs on novel (not previously seen) goals. Intervention rate (human relay per agenda step) ≤5%.

### Current State
- AGENDA lane routes multi-step plans through director.py + workers.py.
- No automated test harness runs 10-run novel-goal batteries or counts escalations.
- `intervention_count` field does not exist in mission outcomes.
- Human-relay events are not logged in agent_loop.py.
- Phase 46 (Intervention Graduation) is TODO — the autonomy tracking layer is unwired.

### Gap
- Add `intervention_count` and `escalation_count` fields to mission outcomes in outcomes.jsonl.
- Log human-relay events in agent_loop.py at the point of mid-run user prompting.
- Build a benchmark harness: 10-run novel-goal battery, counts escalations, reports pass rate.
- Complete Phase 46 (Intervention Graduation) to close the autonomy tracking loop.

---

## Dimension 3: Self-Improvement

### Criterion
The evolver produces ≥1 applied standing rule or lesson per 10-heartbeat window, measurable as `lessons.jsonl` line-count growth ≥1 per window and `evolver_applied_count` incrementing across sessions without manual intervention. Lesson application rate (`times_applied / recorded`) ≥0.30 over a 30-day window.

### Current State
- evolver.py fires meta-improvement every ~10 heartbeats (count-based, `--min-outcomes >= 5`), can generate Suggestions, and writes to `output/`.
- `times_applied` is always 0 — the lesson feedback loop is inert; lessons are recorded but never retrieved at inference time.
- No `evolver_applied_count` metric exists.
- Phase 56 (Promotion Cycle — Standing Rules + Decision Journal) is DONE and feeds evolver.
- Phase 42 (Nightly Eval Wired to Evolver) is TODO — trend-based improvement firing is unwired.
- High-confidence lessons (`confidence ≥ 0.8`) are only 61/623 = 10% of the library.

### Gap
- Wire lesson retrieval into the inference path so `times_applied` increments (Phase 40 dependency).
- Add `evolver_applied_count` to session metrics (metrics.py or evolver_metrics.jsonl).
- Complete Phase 42 (Nightly Eval) to shift evolver from count-based to trend-based firing.
- Add a CI/smoke check: assert lessons.jsonl grows after a heartbeat cycle completes.

---

## Dimension 4: Observability

### Criterion
Every session produces a machine-readable summary (JSON) covering: goal, steps_attempted, steps_completed, escalations, cost_usd, top_lesson. This summary is queryable via `poe-observe` without manual log parsing. All `stuck` and `empty_model_output` events alert within 1 heartbeat.

### Current State
- metrics.py tracks tokens_in, tokens_out, elapsed_ms per model — but dollar cost is not computed.
- poe-observe (Phase 36) exists but the dashboard is PARTIAL — no per-session JSON summary is written.
- Step outcomes go to outcomes.jsonl but are not aggregated into a per-session envelope.
- `stuck` and `empty_model_output` events are currently silent — no alert path.
- Phase 23 (Observability Dashboard) is PARTIAL; Phase 25 (Ops Hardening) is PARTIAL.

### Gap
- Define and write a `session_summary.json` schema (goal, steps_attempted, steps_completed, escalations, cost_usd, top_lesson).
- Write this file at session end in agent_loop.py.
- Add token→$ conversion in metrics.py (Phase 25 / ops hardening scope).
- Wire heartbeat alert for `stuck` and `empty_model_output` events.
- Extend `poe-observe` to ingest session_summary.json files and surface trend metrics (Phase 23 close-out).

---

## Dimension 5: Cost Per Mission

### Criterion
Mean cost per completed routine mission ≤$0.25 (acceptable ≤$1.00). Mean cost per complex mission ≤$1.00 (acceptable ≤$3.00). Monthly total cost for always-on autonomous operation ≤$50. Cost computed and emitted per outcome at 100% coverage.

### Current State
- metrics.py records token counts per model but no dollar conversion is implemented.
- Per-outcome cost field is absent — cost cannot be attributed per mission.
- Cost per stuck vs. successful mission ratio is untracked (runaway retry risk is unknown).
- Monthly total cost is untracked.

### Gap
- Add token→$ conversion to metrics.py using per-model pricing constants.
- Emit `cost_usd` field on every outcome written to outcomes.jsonl.
- Add monthly cost rollup to poe-observe dashboard.
- Add cost-ceiling alert (configurable threshold, fires in heartbeat loop).

---

## Coverage Gaps Summary

These gaps must close before the corresponding metrics become verifiable:

| # | Gap | Blocking | Suggested Fix |
|---|-----|----------|---------------|
| G1 | Lesson feedback loop inert — `times_applied` always 0 | Self-Improvement criterion | Wire lesson retrieval into inference path (Phase 40) |
| G2 | No aggregate inspector pass/fail line | Reliability criterion | Add 7-day rolling good%/poor% in inspector output |
| G3 | `intervention_count` not logged | Autonomy criterion | Add field to outcomes.jsonl + log in agent_loop.py |
| G4 | `empty_model_output` unalerted | Reliability + Observability | Alert in heartbeat when outcome type = `empty_model_output` |
| G5 | Evolver fires on count, not trend | Self-Improvement criterion | Phase 42: nightly eval with trend detection |
| G6 | Replayability not measured | Reliability criterion | Add consistency check to Phase 54 checkpoint reruns |
| G7 | Dollar cost not computed | Cost criterion | Token→$ conversion in metrics.py |
| G8 | No per-session summary JSON | Observability criterion | Write session_summary.json at session end |

---

## Phase Completion Gating

These phases must reach DONE before their dimension's criterion can be declared measurable:

| Phase | Name | Blocks |
|-------|------|--------|
| 23 | Observability Dashboard | Reliability aggregate pass/fail, alert coverage |
| 25 | Ops Hardening | Cost alerting, ops-level failure detection |
| 40 | Pluggable Memory Backend | Lesson retrieval at inference time (G1) |
| 42 | Nightly Eval Wired to Evolver | Trend-based improvement firing (G5) |
| 46 | Intervention Graduation | Autonomy intervention rate logging (G3) |

---

## Evaluation Cadence

| Window | What to check |
|--------|--------------|
| Per heartbeat | `stuck`/`empty_model_output` alerts, friction breach auto-escalation |
| Per mission | cost_usd, quality label, autonomous vs. relay flag, step budget adherence |
| 7-day rolling | Inspector good%/poor%, stuck rate trend |
| 30-day | Lesson application rate, override rate, standing rules promoted |
| Monthly | Total cost, high-confidence lesson % growth |

---

## Overall Exit Condition

Poe has reached autonomous-agent status when all five dimensions are simultaneously green:

| Dimension | Green when |
|-----------|-----------|
| Reliability | 7-day rolling quality-good rate ≥85%, zero unalerted silent failures in last 50 sessions |
| Autonomy | 10-run novel-goal battery passes ≥9/10 with no mid-run escalations |
| Self-Improvement | lessons.jsonl grows ≥1/window over 30 consecutive heartbeat windows; `times_applied` rate ≥0.30 |
| Observability | session_summary.json present for 100% of sessions in last 7 days, cost_usd emitted on all outcomes |
| Cost | Monthly total ≤$50, per-routine-mission mean ≤$0.25 |

---

*Thresholds are proposed baselines — pending Jeremy review and calibration after 30 days of instrumented operation.*
*Sources: BACKLOG.md gap analysis, ROADMAP.md phases 40–56, inspector.py friction signals, evolver.py meta-loop, POE_IDENTITY.md north-star, lat.md self-improvement node, CHANGELOG.md shipping patterns.*
