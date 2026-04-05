# Poe Success Criteria

**Version:** 1.0  
**Date:** 2026-04-04  
**Review cadence:** Monthly (or after any phase transition)

---

## 1. Task Completion Rate

**Definition:** % of missions that produce a shipped work product (vs. abandoned, stuck, or requiring human rescue).

| Threshold | Value |
|-----------|-------|
| Target | ≥85% of NOW-lane missions complete without human rescue |
| Phase gate | Each phase exit requires ≥85% over last 20 missions |
| Alarm | <70% over any 7-day window → evolver runs improvement cycle |

**Measurement method:**
- Source: `task_store` closed vs. abandoned counts + `overall_quality` distribution in `inspector.py`
- Query: `SELECT COUNT(*) WHERE status='done' / COUNT(*) WHERE status IN ('done','abandoned','stuck')` over rolling 20-mission window
- Automated: evolver reads `quality_distribution` each meta-cycle (~10 heartbeats)

**Review cadence:** After every 20 missions; monthly trend report.

---

## 2. Autonomy Ratio

**Definition:** % of steps executed without human intervention (no `flag_stuck`, no re-prompt, no manual override).

| Threshold | Value |
|-----------|-------|
| Target | ≥90% of steps autonomous |
| Human-touch ceiling | ≤10% of steps require intervention |
| 'Reliable' milestone | ≥90% sustained over a 7-day window |
| Alarm | autonomy ratio <80% for 3+ consecutive days |

**Measurement method:**
- Source: `stuck_count` per slug in inspector.py; calibration log `override_rate`
- Formula: `(total_steps - stuck_steps - override_steps) / total_steps`
- Log location: `memory/outcomes.jsonl` — `intervention` field per outcome

**Review cadence:** Weekly rolling window; milestone check at each phase boundary.

---

## 3. Cost-Per-Mission

**Definition:** Median USD cost to complete a single mission end-to-end.

| Threshold | Value |
|-----------|-------|
| NOW-lane target | ≤$0.25/mission |
| AGENDA-lane target | ≤$1.50/mission |
| Haiku fallback guard | Cost regression >2× baseline triggers model-routing review |
| Alarm | Any single mission >$5.00 without prior budget authorization |

**Measurement method:**
- Source: `metrics.py` per-model cost tracking; `cost_usd` field in `outcomes.jsonl`
- Baseline: factory_minimal $0.04–0.06/60s; Mode 2 polymarket $1.27/1156s (existing benchmarks)
- Report: rolling 30-mission median by lane (NOW vs. AGENDA)

**Review cadence:** After each mission; monthly median trend vs. baseline.

---

## 4. Memory Retention Rate

**Definition:** % of extracted lessons that are still reachable and applied in subsequent similar sessions (durable knowledge).

| Threshold | Value |
|-----------|-------|
| Target | ≥80% of lessons promoted to `standing_rules.jsonl` are applied when relevant |
| Promotion bar | Lesson applied in ≥3 sessions before promotion (existing gate) |
| Staleness alarm | Any rule older than 30 days without application → flag for review |

**Measurement method:**
- Source: `memory/standing_rules.jsonl` (last_applied timestamp), `memory/lessons.jsonl`
- Formula: `rules applied in last 30 days / total active rules`
- Reflexion hook in `memory.py` records per-session rule hits

**Review cadence:** Monthly; triggered review after any phase that modifies memory format.

---

## 5. Friction Score

**Definition:** Rolling mean severity of friction signals detected by the inspector (stuck, backtracking, context churn, repeated rephrase, etc.).

| Threshold | Value |
|-----------|-------|
| Target | Mean friction score ≤0.20 per session |
| Threshold breach | >30% of sessions breach any single signal → improvement task queued |
| Phase gate | No signal type breaching >15% of sessions at phase exit |

**Measurement method:**
- Source: `inspector.py` — 7-signal model (ERROR_EVENTS, ESCALATION_TONE, ABANDONED_TOOL_FLOW, REPEATED_REPHRASE, BACKTRACKING, CONTEXT_CHURN, STUCK)
- Tracked: `threshold_breaches` list and `alignment_score_avg` per session
- Existing breach threshold: `_BREACH_THRESHOLD = 0.30`

**Review cadence:** Every 10 heartbeats (evolver meta-cycle); phase exit gate.

---

## 6. Self-Improvement Velocity

**Definition:** Net new lessons extracted and promoted over a rolling 30-day window.

| Threshold | Value |
|-----------|-------|
| Target | ≥5 net-new standing rules promoted per month |
| Regression alarm | Zero promotions in any 14-day window → evolver stuck |
| Quality bar | Promoted rules must have ≥3 application instances before promotion |

**Measurement method:**
- Source: `memory/standing_rules.jsonl` — `promoted_at` timestamp
- Formula: count of rules with `promoted_at` in last 30 days minus rules retired
- Evolver logs improvement actions in `output/` phase audits

**Review cadence:** Monthly; also checked at each evolver meta-cycle.

---

## 7. Phase Exit Quality

**Definition:** Binary gate — each phase must pass all measurable criteria before being marked DONE.

| Criterion | Threshold |
|-----------|-----------|
| Task completion rate | ≥85% over last 20 missions |
| Autonomy ratio | ≥90% over last 7 days |
| Friction score | No signal type breaching >15% of sessions |
| Test coverage | All new modules have ≥8 tests; total suite green |
| Cost baseline | No per-mission regression >2× established baseline |

**Measurement method:**
- Checklist in `ROADMAP.md` — phase exit row must reference each criterion above
- `scripts/audit-phases.sh` extended to emit pass/fail per criterion
- Manual review by Jeremy at phase boundary

**Review cadence:** At each phase transition; no ongoing polling.

---

## Review and Update Process

1. **Monthly review:** Pull rolling metrics from `memory/outcomes.jsonl` and `memory/standing_rules.jsonl`; compare against thresholds above.
2. **Phase transition review:** Run `audit-phases.sh`; confirm all Phase Exit Quality criteria pass before updating ROADMAP.md status to DONE.
3. **Alarm response:** Any alarm condition above creates a task in `task_store` with priority=high and links to the relevant criterion.
4. **Criteria updates:** Thresholds here are v1.0 baselines. Revise only after 30+ missions of data; document rationale in `CHANGELOG.md`.
