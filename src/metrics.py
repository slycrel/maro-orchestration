"""Phase 8: Quality + Cost tracking for Poe orchestration.
Phase 19 adds pass@k / pass^k metrics and skill promotion eligibility.


Computes per-goal success rate, time-to-completion, and estimated cost.
Builds on memory/outcomes.jsonl data.

Usage:
    from metrics import get_metrics, format_metrics_report
    metrics = get_metrics()
    print(format_metrics_report(metrics))
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from memory import load_outcomes, Outcome

try:
    from skills import get_all_skill_stats
except ImportError:  # pragma: no cover
    get_all_skill_stats = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Cost constants (rough mid-tier pricing)
# ---------------------------------------------------------------------------

COST_PER_M_INPUT = 0.25    # $0.25 per 1M input tokens
COST_PER_M_OUTPUT = 1.25   # $1.25 per 1M output tokens


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GoalMetrics:
    task_type: str
    total_runs: int
    success_rate: float          # 0.0 - 1.0
    avg_elapsed_ms: float
    avg_tokens_in: float
    avg_tokens_out: float
    estimated_cost_usd: float    # total estimated cost for all runs of this type


@dataclass
class SystemMetrics:
    computed_at: str
    total_goals: int
    overall_success_rate: float
    by_task_type: Dict[str, GoalMetrics]
    most_expensive_goals: List[Dict[str, Any]]   # top 5 by cost
    slowest_goals: List[Dict[str, Any]]           # top 5 by elapsed_ms
    failure_patterns: List[str]                    # from identify_expensive_patterns


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for a given token usage."""
    return (tokens_in * COST_PER_M_INPUT / 1_000_000) + (tokens_out * COST_PER_M_OUTPUT / 1_000_000)


# ---------------------------------------------------------------------------
# Core computation
# ---------------------------------------------------------------------------

def compute_metrics(outcomes: List[Outcome]) -> SystemMetrics:
    """Pure function: compute SystemMetrics from a list of Outcome objects."""
    now = datetime.now(timezone.utc).isoformat()

    if not outcomes:
        return SystemMetrics(
            computed_at=now,
            total_goals=0,
            overall_success_rate=0.0,
            by_task_type={},
            most_expensive_goals=[],
            slowest_goals=[],
            failure_patterns=[],
        )

    # Group by task_type
    by_type: Dict[str, List[Outcome]] = {}
    for o in outcomes:
        by_type.setdefault(o.task_type, []).append(o)

    # Compute per-type metrics
    type_metrics: Dict[str, GoalMetrics] = {}
    for task_type, type_outcomes in by_type.items():
        total = len(type_outcomes)
        done_count = sum(1 for o in type_outcomes if o.status == "done")
        success_rate = done_count / total if total > 0 else 0.0
        avg_elapsed = sum(o.elapsed_ms for o in type_outcomes) / total
        avg_in = sum(o.tokens_in for o in type_outcomes) / total
        avg_out = sum(o.tokens_out for o in type_outcomes) / total
        total_cost = sum(estimate_cost(o.tokens_in, o.tokens_out) for o in type_outcomes)

        type_metrics[task_type] = GoalMetrics(
            task_type=task_type,
            total_runs=total,
            success_rate=success_rate,
            avg_elapsed_ms=avg_elapsed,
            avg_tokens_in=avg_in,
            avg_tokens_out=avg_out,
            estimated_cost_usd=total_cost,
        )

    # Overall success rate
    total_goals = len(outcomes)
    total_done = sum(1 for o in outcomes if o.status == "done")
    overall_success_rate = total_done / total_goals if total_goals > 0 else 0.0

    # Most expensive goals (top 5)
    goals_with_cost = [
        {
            "goal": o.goal[:80],
            "task_type": o.task_type,
            "cost_usd": estimate_cost(o.tokens_in, o.tokens_out),
            "tokens_in": o.tokens_in,
            "tokens_out": o.tokens_out,
        }
        for o in outcomes
    ]
    most_expensive = sorted(goals_with_cost, key=lambda x: x["cost_usd"], reverse=True)[:5]

    # Slowest goals (top 5)
    goals_with_time = [
        {
            "goal": o.goal[:80],
            "task_type": o.task_type,
            "elapsed_ms": o.elapsed_ms,
            "status": o.status,
        }
        for o in outcomes
    ]
    slowest = sorted(goals_with_time, key=lambda x: x["elapsed_ms"], reverse=True)[:5]

    # Failure patterns
    failure_patterns = identify_expensive_patterns(outcomes)

    return SystemMetrics(
        computed_at=now,
        total_goals=total_goals,
        overall_success_rate=overall_success_rate,
        by_task_type=type_metrics,
        most_expensive_goals=most_expensive,
        slowest_goals=slowest,
        failure_patterns=failure_patterns,
    )


def get_metrics(limit: int = 100) -> SystemMetrics:
    """Load outcomes and compute metrics."""
    outcomes = load_outcomes(limit=limit)
    return compute_metrics(outcomes)


# ---------------------------------------------------------------------------
# Expensive pattern identification
# ---------------------------------------------------------------------------

def identify_expensive_patterns(outcomes: List[Outcome]) -> List[str]:
    """Find task types with above-average cost, return suggestions."""
    if not outcomes:
        return []

    # Compute overall average cost
    costs = [estimate_cost(o.tokens_in, o.tokens_out) for o in outcomes]
    avg_cost = sum(costs) / len(costs) if costs else 0.0

    if avg_cost == 0.0:
        return []

    # Group by task_type and find above-average
    by_type: Dict[str, List[float]] = {}
    for o, cost in zip(outcomes, costs):
        by_type.setdefault(o.task_type, []).append(cost)

    suggestions: List[str] = []
    for task_type, type_costs in by_type.items():
        type_avg = sum(type_costs) / len(type_costs)
        if type_avg > avg_cost * 1.5:  # 50% above average
            suggestions.append(
                f"'{task_type}' tasks cost {type_avg:.6f} USD avg "
                f"({type_avg/avg_cost:.1f}x the overall average). "
                f"Consider using MODEL_CHEAP or reducing max_tokens."
            )

    # Check for high failure rate + high cost
    by_type_outcomes: Dict[str, List[Outcome]] = {}
    for o in outcomes:
        by_type_outcomes.setdefault(o.task_type, []).append(o)

    for task_type, type_outcomes in by_type_outcomes.items():
        stuck = sum(1 for o in type_outcomes if o.status == "stuck")
        total = len(type_outcomes)
        if total >= 3 and stuck / total > 0.5:
            type_cost = sum(estimate_cost(o.tokens_in, o.tokens_out) for o in type_outcomes)
            suggestions.append(
                f"'{task_type}' has {stuck}/{total} stuck outcomes "
                f"(total cost: ${type_cost:.6f}). "
                f"High failure rate indicates wasted spend."
            )

    return suggestions


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 19: pass@k / pass^k metrics
# ---------------------------------------------------------------------------

def compute_pass_at_k(skill_id: str, k: int = 3) -> float:
    """Compute pass@k: P(at least 1 success in k attempts).

    Formula: pass@k = 1 - (1 - success_rate)^k

    Args:
        skill_id: Skill ID to look up in skill-stats.jsonl.
        k:        Number of attempts.

    Returns:
        Float 0.0–1.0. Returns 0.0 if skill not found.
    """
    success_rate = _get_skill_success_rate(skill_id)
    if success_rate is None:
        return 0.0
    result = 1.0 - (1.0 - success_rate) ** k
    return max(0.0, min(1.0, result))


def compute_pass_all_k(skill_id: str, k: int = 3) -> float:
    """Compute pass^k: P(all k attempts succeed).

    Formula: pass^k = success_rate^k

    Args:
        skill_id: Skill ID to look up in skill-stats.jsonl.
        k:        Number of attempts.

    Returns:
        Float 0.0–1.0. Returns 0.0 if skill not found.
    """
    success_rate = _get_skill_success_rate(skill_id)
    if success_rate is None:
        return 0.0
    result = success_rate ** k
    return max(0.0, min(1.0, result))


def check_skill_promotion_eligibility(
    skill_id: str,
    k: int = 3,
    threshold: float = 0.7,
) -> bool:
    """Check if a skill is eligible for promotion from provisional to established.

    A skill is eligible if pass^k >= threshold.
    Default: pass^3 >= 0.7 means the skill must succeed 70%+ of the time
    consistently across 3 consecutive attempts.

    Args:
        skill_id:   Skill ID.
        k:          Number of consecutive attempts.
        threshold:  Minimum pass^k score required (0.0–1.0).

    Returns:
        True if eligible for promotion.
    """
    pass_all = compute_pass_all_k(skill_id, k=k)
    return pass_all >= threshold


def _get_skill_success_rate(skill_id: str) -> Optional[float]:
    """Internal: get success_rate for a skill from skill-stats.jsonl."""
    if get_all_skill_stats is None:
        return None
    try:
        all_stats = get_all_skill_stats()
        for s in all_stats:
            if s.skill_id == skill_id:
                return float(s.success_rate)
    except Exception:
        pass
    return None


def format_metrics_report(metrics: SystemMetrics) -> str:
    """Format metrics as a human-readable text report."""
    lines = [
        "=== Poe System Metrics ===",
        f"Computed: {metrics.computed_at[:19]}Z",
        f"Total goals: {metrics.total_goals}",
        f"Overall success rate: {metrics.overall_success_rate:.1%}",
        "",
    ]

    if metrics.by_task_type:
        lines.append("--- By Task Type ---")
        for task_type, gm in sorted(metrics.by_task_type.items()):
            lines.append(
                f"  {task_type}: {gm.total_runs} runs, "
                f"{gm.success_rate:.0%} success, "
                f"avg {gm.avg_elapsed_ms:.0f}ms, "
                f"${gm.estimated_cost_usd:.6f} total"
            )
        lines.append("")

    if metrics.most_expensive_goals:
        lines.append("--- Most Expensive Goals ---")
        for i, g in enumerate(metrics.most_expensive_goals, 1):
            lines.append(f"  {i}. ${g['cost_usd']:.6f} — {g['goal']}")
        lines.append("")

    if metrics.slowest_goals:
        lines.append("--- Slowest Goals ---")
        for i, g in enumerate(metrics.slowest_goals, 1):
            lines.append(f"  {i}. {g['elapsed_ms']}ms — {g['goal']}")
        lines.append("")

    if metrics.failure_patterns:
        lines.append("--- Cost Optimization Suggestions ---")
        for p in metrics.failure_patterns:
            lines.append(f"  ! {p}")
        lines.append("")

    return "\n".join(lines)
