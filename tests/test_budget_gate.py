"""Tests for the substrate-trial budget gates: config-default per-run cap +
daily cross-run cap (metrics.spend_today feeding budget.daily_usd)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import metrics
from metrics import spend_today, record_step_cost


@pytest.fixture
def costs_path(tmp_path, monkeypatch):
    p = tmp_path / "step-costs.jsonl"
    monkeypatch.setattr(metrics, "_step_costs_path", lambda: p)
    return p


def _entry(cost, when):
    return json.dumps({"id": "x", "recorded_at": when.isoformat(),
                       "cost_usd": cost})


# --- spend_today ------------------------------------------------------------

def test_spend_today_sums_todays_entries(costs_path):
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    costs_path.write_text("\n".join([
        _entry(0.5, now),
        _entry(0.25, now),
        _entry(9.99, yesterday),   # excluded
    ]) + "\n")
    assert spend_today() == pytest.approx(0.75)


def test_spend_today_empty_file(costs_path):
    assert spend_today() == 0.0


def test_spend_today_missing_file(costs_path):
    # fixture patches the path but never creates the file
    assert spend_today() == 0.0


def test_spend_today_skips_malformed_lines(costs_path):
    now = datetime.now(timezone.utc)
    costs_path.write_text(f"{{not json {now.date().isoformat()}\n" + _entry(1.0, now) + "\n")
    assert spend_today() == pytest.approx(1.0)


def test_spend_today_sees_record_step_cost(costs_path):
    record_step_cost("do a thing", tokens_in=1000, tokens_out=500, status="done")
    assert spend_today() > 0.0


# --- spend_for_loops (cost-per-run join) -------------------------------------

def _loop_entry(cost, loop_id):
    return json.dumps({"id": "x", "recorded_at": "2026-07-02T12:00:00+00:00",
                       "cost_usd": cost, "loop_id": loop_id})


def test_spend_for_loops_sums_only_matching(costs_path):
    costs_path.write_text("\n".join([
        _loop_entry(0.5, "aaaa1111"),
        _loop_entry(0.25, "aaaa1111"),
        _loop_entry(9.99, "bbbb2222"),   # different loop — excluded
        json.dumps({"id": "y", "cost_usd": 3.0}),  # legacy, no loop_id
    ]) + "\n")
    assert metrics.spend_for_loops(["aaaa1111"]) == pytest.approx(0.75)


def test_spend_for_loops_multiple_ids(costs_path):
    costs_path.write_text("\n".join([
        _loop_entry(0.5, "aaaa1111"),
        _loop_entry(0.25, "bbbb2222"),
    ]) + "\n")
    assert metrics.spend_for_loops(["aaaa1111", "bbbb2222"]) == pytest.approx(0.75)


def test_spend_for_loops_empty_input(costs_path):
    assert metrics.spend_for_loops([]) == 0.0
    assert metrics.spend_for_loops(None) == 0.0


def test_record_step_cost_carries_loop_id(costs_path):
    record_step_cost("do a thing", tokens_in=1000, tokens_out=500,
                     status="done", loop_id="cccc3333")
    assert metrics.spend_for_loops(["cccc3333"]) > 0.0


# --- loop budget gates (agent_loop._budget_gate seam) ------------------------

class _Ctx:
    loop_id = "test1234"
    cost_budget = None


def _run_gate(monkeypatch, config_values, spent=0.0, cost_budget=None,
              dry_run=False):
    import agent_loop
    import config as config_mod
    monkeypatch.setattr(config_mod, "get",
                        lambda key, default=None: config_values.get(key, default))
    monkeypatch.setattr(metrics, "spend_today", lambda: spent)
    ctx = _Ctx()
    ctx.cost_budget = cost_budget
    refusal = agent_loop._budget_gate(ctx, goal="test goal", project=None,
                                      dry_run=dry_run)
    return ctx, refusal


def test_daily_gate_refuses_when_exhausted(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.daily_usd": 5.0}, spent=6.0)
    assert refusal is not None
    assert refusal.status == "stuck"
    assert "daily budget exhausted" in refusal.stuck_reason


def test_daily_gate_emits_escalation(monkeypatch):
    import notify
    seen = []
    monkeypatch.setattr(notify, "emit",
                        lambda event, payload, **kw: seen.append((event, payload)) or True)
    _run_gate(monkeypatch, {"budget.daily_usd": 5.0}, spent=6.0)
    assert seen and seen[0][0] == "escalation"
    assert seen[0][1]["point"] == "budget_gate"


def test_daily_gate_allows_under_budget(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.daily_usd": 5.0}, spent=1.0)
    assert refusal is None


def test_per_run_default_from_config(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.per_run_usd": 2.5})
    assert refusal is None
    assert ctx.cost_budget == 2.5


def test_explicit_cost_budget_wins_over_config(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.per_run_usd": 2.5},
                             cost_budget=9.0)
    assert ctx.cost_budget == 9.0


def test_no_budget_config_means_uncapped(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {})
    assert refusal is None
    assert ctx.cost_budget is None


def test_dry_run_skips_gates(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.daily_usd": 5.0},
                             spent=100.0, dry_run=True)
    assert refusal is None
    assert ctx.cost_budget is None
