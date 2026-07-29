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
    # Real writes are chronological (record_step_cost appends under
    # locked_append) — spend_today scans backward from EOF and stops at the
    # first pre-midnight row, so this fixture orders entries the way the
    # system actually produces them: oldest first, today's entries last.
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    costs_path.write_text("\n".join([
        _entry(9.99, yesterday),   # excluded
        _entry(0.5, now),
        _entry(0.25, now),
    ]) + "\n")
    assert spend_today() == pytest.approx(0.75)


def test_spend_today_empty_file(costs_path):
    costs_path.write_text("", encoding="utf-8")
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


def test_spend_today_stops_scanning_at_first_old_entry(costs_path, monkeypatch):
    """Proves the O(lifetime) file-scan fix: a large run of old entries ahead
    of today's suffix must not all be pulled through the reverse-line
    generator — only today's tail plus the one entry that ends the scan."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    lines = [_entry(1.0, yesterday) for _ in range(5000)]
    lines += [_entry(0.5, now), _entry(0.25, now)]
    costs_path.write_text("\n".join(lines) + "\n")

    pulled = {"n": 0}
    real_reverse = metrics._reverse_readline

    def _counting_reverse(path, **kw):
        for line in real_reverse(path, **kw):
            pulled["n"] += 1
            yield line

    monkeypatch.setattr(metrics, "_reverse_readline", _counting_reverse)
    assert spend_today() == pytest.approx(0.75)
    assert pulled["n"] < 50  # today's 2 entries + the one that ends the scan, not 5002


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


def test_no_budget_config_gets_safe_default_caps(monkeypatch):
    """1.0 posture (2026-07-09): a fresh install with no budget config is
    capped at the hardcoded defaults, never uncapped. (p90 pinned to None so
    the auto-mode path deterministically lands on the floor.)"""
    import loop_init
    _pin_p90(monkeypatch, None)
    ctx, refusal = _run_gate(monkeypatch, {})
    assert refusal is None
    assert ctx.cost_budget == loop_init.DEFAULT_PER_RUN_USD


def test_default_daily_cap_refuses_when_exhausted(monkeypatch):
    import loop_init
    ctx, refusal = _run_gate(monkeypatch, {},
                             spent=loop_init.DEFAULT_DAILY_USD + 1.0)
    assert refusal is not None
    assert "daily budget exhausted" in refusal.stuck_reason


def test_explicit_zero_disables_caps(monkeypatch):
    """budget.per_run_usd: 0 / budget.daily_usd: 0 is the uncapped opt-out."""
    ctx, refusal = _run_gate(monkeypatch,
                             {"budget.per_run_usd": 0, "budget.daily_usd": 0},
                             spent=1000.0)
    assert refusal is None
    assert ctx.cost_budget is None


def test_quoted_string_zero_also_disables_caps(monkeypatch):
    """YAML `per_run_usd: "0"` (quoted) must behave like numeric 0 — coerce
    BEFORE the truthiness test, or "0" becomes a $0 cap that divides by zero
    downstream."""
    ctx, refusal = _run_gate(monkeypatch,
                             {"budget.per_run_usd": "0", "budget.daily_usd": "0"},
                             spent=1000.0)
    assert refusal is None
    assert ctx.cost_budget is None


def test_malformed_value_fails_closed_to_default(monkeypatch):
    """A typo in budget config must never silently disable the caps —
    fall back to the safe defaults, and a bad per-run value must not
    knock out the daily gate."""
    import loop_init
    ctx, refusal = _run_gate(monkeypatch,
                             {"budget.per_run_usd": "2 dollars",
                              "budget.daily_usd": "lots"},
                             spent=1_000_000.0)
    assert ctx.cost_budget == loop_init.DEFAULT_PER_RUN_USD
    assert refusal is not None  # daily default still gates despite both typos
    assert "daily budget exhausted" in refusal.stuck_reason


def test_dry_run_skips_gates(monkeypatch):
    ctx, refusal = _run_gate(monkeypatch, {"budget.daily_usd": 5.0},
                             spent=100.0, dry_run=True)
    assert refusal is None
    assert ctx.cost_budget is None


# ---------------------------------------------------------------------------
# Data-driven thresholds (Jeremy decree 2026-07-29): when budget.per_run_usd
# is ABSENT the breaker is max(floor, 4x p90 of successful-run costs) and the
# warn line is max($2.50, p90). Explicit config numbers stay static; 0/null
# stays the uncapped opt-out.
# ---------------------------------------------------------------------------

def _pin_p90(monkeypatch, value):
    monkeypatch.setattr(metrics, "successful_run_cost_p90",
                        lambda limit=None: value)


class TestAutoBudget:
    def test_thin_history_uses_floor(self, monkeypatch):
        import loop_init
        _pin_p90(monkeypatch, None)
        ctx, refusal = _run_gate(monkeypatch, {})
        assert refusal is None
        assert ctx.cost_budget == loop_init.DEFAULT_PER_RUN_USD

    def test_hot_history_scales_breaker(self, monkeypatch):
        """p90 above floor/4 — the breaker follows what success costs."""
        _pin_p90(monkeypatch, 5.0)
        ctx, _ = _run_gate(monkeypatch, {})
        assert ctx.cost_budget == 20.0

    def test_floor_governs_cheap_history(self, monkeypatch):
        """4x p90 below the floor — the floor wins (today's box: p90≈$1.21)."""
        import loop_init
        _pin_p90(monkeypatch, 1.21)
        ctx, _ = _run_gate(monkeypatch, {})
        assert ctx.cost_budget == loop_init.DEFAULT_PER_RUN_USD

    def test_explicit_config_stays_static(self, monkeypatch):
        """An operator-set number is a static cap — history never inflates it."""
        _pin_p90(monkeypatch, 5.0)
        ctx, _ = _run_gate(monkeypatch, {"budget.per_run_usd": 2.5})
        assert ctx.cost_budget == 2.5

    def test_zero_still_uncapped_despite_history(self, monkeypatch):
        _pin_p90(monkeypatch, 5.0)
        ctx, refusal = _run_gate(monkeypatch,
                                 {"budget.per_run_usd": 0,
                                  "budget.daily_usd": 0}, spent=1000.0)
        assert refusal is None
        assert ctx.cost_budget is None


class TestWarnLine:
    def test_auto_warn_floor_on_thin_history(self, monkeypatch):
        import loop_init
        _pin_p90(monkeypatch, None)
        ctx, _ = _run_gate(monkeypatch, {})
        assert ctx.cost_warn_usd == loop_init.WARN_FLOOR_USD

    def test_auto_warn_follows_p90(self, monkeypatch):
        """p90 above the $2.50 floor — warn at the top-decile boundary."""
        _pin_p90(monkeypatch, 4.0)
        ctx, _ = _run_gate(monkeypatch, {})
        assert ctx.cost_warn_usd == 4.0
        assert ctx.cost_budget == 16.0

    def test_explicit_warn_static(self, monkeypatch):
        _pin_p90(monkeypatch, 4.0)
        ctx, _ = _run_gate(monkeypatch, {"budget.warn_usd": 1.0})
        assert ctx.cost_warn_usd == 1.0

    def test_warn_zero_disables(self, monkeypatch):
        _pin_p90(monkeypatch, 4.0)
        ctx, _ = _run_gate(monkeypatch, {"budget.warn_usd": 0})
        assert ctx.cost_warn_usd == 0.0

    def test_warn_clamped_below_budget(self, monkeypatch):
        """A warn line at/above the breaker is useless — clamp to 80%."""
        _pin_p90(monkeypatch, None)
        ctx, _ = _run_gate(monkeypatch, {"budget.per_run_usd": 2.0,
                                         "budget.warn_usd": 5.0})
        assert ctx.cost_warn_usd == pytest.approx(1.6)

    def test_uncapped_run_sets_no_warn(self, monkeypatch):
        _pin_p90(monkeypatch, 4.0)
        ctx, _ = _run_gate(monkeypatch, {"budget.per_run_usd": 0,
                                         "budget.daily_usd": 0})
        assert getattr(ctx, "cost_warn_usd", None) is None

    def test_explicit_caller_budget_still_gets_warn_line(self, monkeypatch):
        _pin_p90(monkeypatch, 4.0)
        ctx, _ = _run_gate(monkeypatch, {}, cost_budget=9.0)
        assert ctx.cost_budget == 9.0
        assert ctx.cost_warn_usd == 4.0


class TestSuccessfulRunCostP90:
    """metrics.successful_run_cost_p90 — the history scan behind auto mode."""

    def _write_cards(self, root, specs):
        for i, (cls, cost) in enumerate(specs):
            rd = root / f"run-{i:03d}"
            rd.mkdir(parents=True)
            card = {"success_class": cls, "total_cost_usd": cost}
            (rd / "run_card.json").write_text(json.dumps(card))

    def _p90(self, monkeypatch, tmp_path, specs):
        import runs
        monkeypatch.setattr(runs, "runs_root", lambda: tmp_path)
        monkeypatch.setattr(metrics, "_run_cost_p90_cache", {})
        self._write_cards(tmp_path, specs)
        return metrics.successful_run_cost_p90()

    def test_p90_over_success_classes(self, monkeypatch, tmp_path):
        specs = [("success", float(i)) for i in range(1, 11)]  # $1..$10
        assert self._p90(monkeypatch, tmp_path, specs) == 9.0

    def test_failed_runs_excluded(self, monkeypatch, tmp_path):
        specs = ([("success", 1.0)] * 8) + [("failed", 100.0),
                                            ("partial", 50.0)]
        assert self._p90(monkeypatch, tmp_path, specs) == 1.0

    def test_done_unverified_counts_as_success(self, monkeypatch, tmp_path):
        specs = [("done-unverified", 2.0)] * 8
        assert self._p90(monkeypatch, tmp_path, specs) == 2.0

    def test_thin_history_returns_none(self, monkeypatch, tmp_path):
        specs = [("success", 1.0)] * (metrics.RUN_COST_MIN_SAMPLES - 1)
        assert self._p90(monkeypatch, tmp_path, specs) is None

    def test_null_cost_skipped(self, monkeypatch, tmp_path):
        """Pre-2026-07-02 cards have total_cost_usd: null — not a $0 sample."""
        specs = ([("success", 3.0)] * 8) + [("success", None)] * 5
        assert self._p90(monkeypatch, tmp_path, specs) == 3.0

    def test_missing_runs_root_returns_none(self, monkeypatch, tmp_path):
        import runs
        monkeypatch.setattr(runs, "runs_root", lambda: tmp_path / "nope")
        monkeypatch.setattr(metrics, "_run_cost_p90_cache", {})
        assert metrics.successful_run_cost_p90() is None
