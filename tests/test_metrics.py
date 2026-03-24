"""Tests for Phase 8: metrics.py — quality + cost tracking."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from metrics import (
    GoalMetrics,
    SystemMetrics,
    compute_metrics,
    estimate_cost,
    format_metrics_report,
    get_metrics,
    identify_expensive_patterns,
    COST_PER_M_INPUT,
    COST_PER_M_OUTPUT,
)
from memory import Outcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_outcome(
    status="done",
    task_type="research",
    goal="test goal",
    summary="worked",
    tokens_in=1000,
    tokens_out=500,
    elapsed_ms=2000,
):
    return Outcome(
        outcome_id="x",
        goal=goal,
        task_type=task_type,
        status=status,
        summary=summary,
        lessons=[],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=elapsed_ms,
    )


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

def test_estimate_cost_zero():
    assert estimate_cost(0, 0) == 0.0


def test_estimate_cost_basic():
    cost = estimate_cost(1_000_000, 1_000_000)
    assert cost == pytest.approx(COST_PER_M_INPUT + COST_PER_M_OUTPUT)


def test_estimate_cost_proportional():
    cost = estimate_cost(500_000, 0)
    assert cost == pytest.approx(COST_PER_M_INPUT / 2)


# ---------------------------------------------------------------------------
# compute_metrics — empty
# ---------------------------------------------------------------------------

def test_compute_metrics_empty():
    m = compute_metrics([])
    assert m.total_goals == 0
    assert m.overall_success_rate == 0.0
    assert m.by_task_type == {}
    assert m.most_expensive_goals == []
    assert m.slowest_goals == []


# ---------------------------------------------------------------------------
# compute_metrics — mixed outcomes
# ---------------------------------------------------------------------------

def test_compute_metrics_mixed():
    outcomes = [
        _make_outcome(status="done", task_type="research", tokens_in=1000, tokens_out=500, elapsed_ms=2000),
        _make_outcome(status="stuck", task_type="research", tokens_in=2000, tokens_out=800, elapsed_ms=5000),
        _make_outcome(status="done", task_type="build", tokens_in=500, tokens_out=200, elapsed_ms=1000),
    ]
    m = compute_metrics(outcomes)

    assert m.total_goals == 3
    assert m.overall_success_rate == pytest.approx(2 / 3)

    assert "research" in m.by_task_type
    assert "build" in m.by_task_type

    research = m.by_task_type["research"]
    assert research.total_runs == 2
    assert research.success_rate == 0.5
    assert research.avg_elapsed_ms == 3500.0
    assert research.avg_tokens_in == 1500.0

    build = m.by_task_type["build"]
    assert build.total_runs == 1
    assert build.success_rate == 1.0


def test_compute_metrics_all_done():
    outcomes = [_make_outcome(status="done") for _ in range(5)]
    m = compute_metrics(outcomes)
    assert m.overall_success_rate == 1.0


def test_compute_metrics_all_stuck():
    outcomes = [_make_outcome(status="stuck") for _ in range(3)]
    m = compute_metrics(outcomes)
    assert m.overall_success_rate == 0.0


# ---------------------------------------------------------------------------
# compute_metrics — most_expensive and slowest
# ---------------------------------------------------------------------------

def test_compute_metrics_top5_expensive():
    outcomes = [
        _make_outcome(goal=f"goal-{i}", tokens_in=i * 10000, tokens_out=i * 5000)
        for i in range(10)
    ]
    m = compute_metrics(outcomes)
    assert len(m.most_expensive_goals) == 5
    # Most expensive should be highest index
    assert "goal-9" in m.most_expensive_goals[0]["goal"]


def test_compute_metrics_top5_slowest():
    outcomes = [
        _make_outcome(goal=f"goal-{i}", elapsed_ms=i * 1000)
        for i in range(10)
    ]
    m = compute_metrics(outcomes)
    assert len(m.slowest_goals) == 5
    assert "goal-9" in m.slowest_goals[0]["goal"]


# ---------------------------------------------------------------------------
# identify_expensive_patterns
# ---------------------------------------------------------------------------

def test_identify_expensive_patterns_empty():
    assert identify_expensive_patterns([]) == []


def test_identify_expensive_patterns_no_outliers():
    # All same cost — no outliers
    outcomes = [
        _make_outcome(task_type="research", tokens_in=1000, tokens_out=500),
        _make_outcome(task_type="research", tokens_in=1000, tokens_out=500),
        _make_outcome(task_type="build", tokens_in=1000, tokens_out=500),
    ]
    result = identify_expensive_patterns(outcomes)
    assert len(result) == 0


def test_identify_expensive_patterns_detects_expensive_type():
    outcomes = [
        _make_outcome(task_type="research", tokens_in=100000, tokens_out=50000),
        _make_outcome(task_type="build", tokens_in=100, tokens_out=50),
        _make_outcome(task_type="build", tokens_in=100, tokens_out=50),
        _make_outcome(task_type="build", tokens_in=100, tokens_out=50),
    ]
    result = identify_expensive_patterns(outcomes)
    assert any("research" in s for s in result)


def test_identify_expensive_patterns_detects_high_failure_rate():
    outcomes = [
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="done", task_type="research", tokens_in=1000, tokens_out=500),
    ]
    result = identify_expensive_patterns(outcomes)
    assert any("ops" in s and "stuck" in s for s in result)


def test_identify_expensive_patterns_zero_cost():
    outcomes = [_make_outcome(tokens_in=0, tokens_out=0) for _ in range(5)]
    result = identify_expensive_patterns(outcomes)
    assert result == []


# ---------------------------------------------------------------------------
# format_metrics_report
# ---------------------------------------------------------------------------

def test_format_metrics_report_empty():
    m = compute_metrics([])
    report = format_metrics_report(m)
    assert "Total goals: 0" in report
    assert "Poe System Metrics" in report


def test_format_metrics_report_with_data():
    outcomes = [
        _make_outcome(status="done", task_type="research", tokens_in=1000, tokens_out=500),
        _make_outcome(status="stuck", task_type="build", tokens_in=2000, tokens_out=800),
    ]
    m = compute_metrics(outcomes)
    report = format_metrics_report(m)
    assert "Total goals: 2" in report
    assert "research" in report
    assert "build" in report
    assert "By Task Type" in report


def test_format_metrics_report_includes_failure_patterns():
    outcomes = [
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="stuck", task_type="ops", tokens_in=5000, tokens_out=2000),
        _make_outcome(status="done", task_type="research", tokens_in=100, tokens_out=50),
    ]
    m = compute_metrics(outcomes)
    report = format_metrics_report(m)
    assert "Cost Optimization" in report


# ---------------------------------------------------------------------------
# get_metrics (integration with load_outcomes)
# ---------------------------------------------------------------------------

def test_get_metrics_loads_outcomes():
    outcomes = [_make_outcome()] * 3
    with patch("metrics.load_outcomes", return_value=outcomes):
        m = get_metrics()
    assert m.total_goals == 3


def test_get_metrics_empty():
    with patch("metrics.load_outcomes", return_value=[]):
        m = get_metrics()
    assert m.total_goals == 0


# ---------------------------------------------------------------------------
# GoalMetrics dataclass
# ---------------------------------------------------------------------------

def test_goal_metrics_dataclass():
    gm = GoalMetrics(
        task_type="research",
        total_runs=10,
        success_rate=0.8,
        avg_elapsed_ms=5000.0,
        avg_tokens_in=1000.0,
        avg_tokens_out=500.0,
        estimated_cost_usd=0.001,
    )
    assert gm.task_type == "research"
    assert gm.success_rate == 0.8


# ---------------------------------------------------------------------------
# SystemMetrics dataclass
# ---------------------------------------------------------------------------

def test_system_metrics_dataclass():
    sm = SystemMetrics(
        computed_at="2026-01-01T00:00:00Z",
        total_goals=5,
        overall_success_rate=0.8,
        by_task_type={},
        most_expensive_goals=[],
        slowest_goals=[],
        failure_patterns=[],
    )
    assert sm.total_goals == 5
    assert sm.overall_success_rate == 0.8


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_metrics_text(capsys):
    outcomes = [_make_outcome()] * 3
    with patch("metrics.load_outcomes", return_value=outcomes):
        import cli
        rc = cli.main(["poe-metrics"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Poe System Metrics" in out


def test_cli_poe_metrics_json(capsys):
    outcomes = [_make_outcome()] * 3
    with patch("metrics.load_outcomes", return_value=outcomes):
        import cli
        rc = cli.main(["poe-metrics", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "total_goals" in data
    assert data["total_goals"] == 3
