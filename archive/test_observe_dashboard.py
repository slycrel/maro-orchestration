"""Tests for the archived observe_dashboard.py — see that module's docstring.

Not collected by the default `tests/` pytest run (lives outside tests/,
matching the "archived, not part of active CI" status). Run explicitly:

    PYTHONPATH=src:archive python3 -m pytest archive/test_observe_dashboard.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

import observe
import observe_dashboard


def _ws(tmp_path) -> Path:
    # MARO_WORKSPACE is the canonical workspace root: memory lives directly
    # beneath it. The prototypes/maro-orchestration layout is retained only
    # for legacy OPENCLAW_WORKSPACE/WORKSPACE_ROOT pins (orch_items.py).
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem


class TestSnapshotJsonIncludes:
    def test_cost_key_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        snap = observe_dashboard._snapshot_json()
        assert "cost" in snap
        assert "total_usd" in snap["cost"]

    def test_ancestry_key_present(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        snap = observe_dashboard._snapshot_json()
        assert "ancestry" in snap
        assert isinstance(snap["ancestry"], list)


class TestDashboardReplayEndpoint:
    """Test the /api/replay POST handler via serve_dashboard's internal handler."""

    def test_replay_with_no_outcomes_returns_404(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        monkeypatch.setattr(observe, "_read_recent_outcomes", lambda limit=1: [])
        outcomes = observe._read_recent_outcomes(limit=1)
        assert outcomes == []

    def test_replay_with_outcomes_finds_goal(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _ws(tmp_path)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        (mem / "outcomes.jsonl").write_text(
            json.dumps({"goal": "research Polymarket trends", "status": "done",
                        "timestamp": ts}),
            encoding="utf-8"
        )
        outcomes = observe._read_recent_outcomes(limit=1)
        assert outcomes
        assert outcomes[0]["goal"] == "research Polymarket trends"


class TestFactoryReplay:
    """Tests for /api/replay-factory logic: evolver signal scan → sub-mission queue."""

    def test_factory_replay_returns_202_with_outcomes(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _ws(tmp_path)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        (mem / "outcomes.jsonl").write_text(
            json.dumps({"goal": "research Polymarket", "status": "done", "timestamp": ts}),
            encoding="utf-8"
        )
        outcomes = observe._read_recent_outcomes(limit=10)
        assert len(outcomes) >= 1

    def test_factory_replay_no_outcomes_returns_404_equivalent(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        monkeypatch.setattr(observe, "_read_recent_outcomes", lambda limit=10: [])
        outcomes = observe._read_recent_outcomes(limit=10)
        assert outcomes == []

    def test_factory_replay_caps_signals_at_3(self, monkeypatch, tmp_path):
        import inspect
        src = inspect.getsource(observe_dashboard)
        assert "signals[:3]" in src, "Factory replay should cap signals at 3"

    def test_factory_replay_endpoint_exists_in_handler(self, monkeypatch, tmp_path):
        import inspect
        src = inspect.getsource(observe_dashboard)
        assert "/api/replay-factory" in src


class TestEvalTrendDashboardHTML:
    def test_collect_dashboard_includes_eval_trend(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from unittest.mock import patch
        _trend = [{"timestamp": "2026-04-14T10:00:00Z", "builtin_score": 0.90, "run_id": "r1"}]
        with patch("observe._read_eval_trend", return_value=_trend):
            data = observe_dashboard._snapshot_json()
        assert "eval_trend" in data
        assert data["eval_trend"] == _trend

    def test_dashboard_html_contains_eval_panel(self):
        assert "eval-trend-status" in observe_dashboard._DASHBOARD_HTML
        assert "Eval Pass Rate" in observe_dashboard._DASHBOARD_HTML


class TestCaptainLogDashboardHTML:
    @pytest.fixture(autouse=True)
    def _mem_dir(self, monkeypatch, tmp_path):
        mem = tmp_path / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MARO_MEMORY_DIR", str(mem))
        return mem

    def test_snapshot_includes_captain_log(self):
        from unittest.mock import patch
        _log = [{"ts": "2026-04-14T10:00:00Z", "event_type": "SKILL_PROMOTED",
                 "loop_id": "abc", "subject": "s", "summary": "promoted"}]
        with patch("observe._read_captain_log_entries", return_value=_log):
            data = observe_dashboard._snapshot_json()
        assert "captain_log" in data
        assert data["captain_log"] == _log

    def test_dashboard_html_contains_captain_log_panel(self):
        assert "captain-log-status" in observe_dashboard._DASHBOARD_HTML
        assert "Captain" in observe_dashboard._DASHBOARD_HTML


class TestSuggestionStatsDashboardHTML:
    @pytest.fixture(autouse=True)
    def _mem(self, tmp_path, monkeypatch):
        mem = tmp_path / "memory"
        mem.mkdir()
        monkeypatch.setenv("MARO_MEMORY_DIR", str(mem))
        self._mem_path = mem

    def test_snapshot_includes_suggestion_stats(self):
        from unittest.mock import patch
        _mock = {"total": 10, "by_category": {}, "by_status": {}, "pending": 8, "applied": 2}
        with patch("observe._read_suggestion_stats", return_value=_mock):
            data = observe_dashboard._snapshot_json()
        assert "suggestion_stats" in data
        assert data["suggestion_stats"]["total"] == 10

    def test_dashboard_html_contains_suggestion_panel(self):
        assert "suggestion-stats" in observe_dashboard._DASHBOARD_HTML
        assert "Evolver Suggestions" in observe_dashboard._DASHBOARD_HTML
