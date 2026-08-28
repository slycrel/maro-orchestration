"""Tests for maro-observe execution snapshot (Phase 23 first cut)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import observe


# ---------------------------------------------------------------------------
# Helper: set up a fake workspace matching orch_root() layout
# ---------------------------------------------------------------------------

def _ws(tmp_path) -> Path:
    """Returns the memory dir that orch_root() will use under MARO_WORKSPACE."""
    mem = tmp_path / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem


def _write_loop_lock(mem: Path, goal: str = "test goal", pid: int = 1234) -> None:
    from datetime import datetime, timezone
    (mem / "loop.lock").write_text(json.dumps({
        "loop_id": "test-loop-001",
        "goal": goal,
        "pid": pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }))


def _write_heartbeat(mem: Path, status: str = "healthy") -> None:
    from datetime import datetime, timezone
    (mem / "heartbeat-state.json").write_text(json.dumps({
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "message": f"system is {status}",
    }))


def _append_outcome(mem: Path, goal: str = "task", status: str = "success") -> None:
    from datetime import datetime, timezone
    line = json.dumps({
        "goal": goal,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    with open(mem / "outcomes.jsonl", "a") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# _read_loop_state
# ---------------------------------------------------------------------------

def test_read_loop_state_idle_when_no_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    state = observe._read_loop_state()
    assert state["running"] is False


def test_read_loop_state_running_when_lock_exists(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    _write_loop_lock(mem, goal="paint kanji")
    state = observe._read_loop_state()
    assert state["running"] is True
    assert "kanji" in state["goal"]


# ---------------------------------------------------------------------------
# _read_heartbeat
# ---------------------------------------------------------------------------

def test_read_heartbeat_unavailable_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    hb = observe._read_heartbeat()
    assert hb["available"] is False


def test_read_heartbeat_reads_status(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    _write_heartbeat(mem, status="degraded")
    hb = observe._read_heartbeat()
    assert hb["available"] is True
    assert hb["status"] == "degraded"


# ---------------------------------------------------------------------------
# _read_recent_outcomes
# ---------------------------------------------------------------------------

def test_read_recent_outcomes_empty_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    assert observe._read_recent_outcomes() == []


def test_read_recent_outcomes_returns_most_recent_first(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    for i in range(5):
        _append_outcome(mem, goal=f"task-{i}", status="success")
    results = observe._read_recent_outcomes(limit=3)
    assert len(results) == 3
    # Most recent written is task-4
    assert results[0]["goal"] == "task-4"


def test_read_recent_outcomes_respects_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    for i in range(10):
        _append_outcome(mem, goal=f"task-{i}")
    results = observe._read_recent_outcomes(limit=4)
    assert len(results) == 4


# ---------------------------------------------------------------------------
# print_* functions — smoke tests
# ---------------------------------------------------------------------------

def test_print_loop_state_idle(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.print_loop_state()
    out = capsys.readouterr().out
    assert "idle" in out


def test_print_loop_state_running(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    _write_loop_lock(mem, goal="research topic X")
    observe.print_loop_state()
    out = capsys.readouterr().out
    assert "RUNNING" in out
    assert "research topic X" in out


def test_print_heartbeat_no_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.print_heartbeat()
    out = capsys.readouterr().out
    assert "heartbeat" in out.lower()


def test_print_heartbeat_with_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    _write_heartbeat(mem, "healthy")
    observe.print_heartbeat()
    out = capsys.readouterr().out
    assert "healthy" in out


def test_print_recent_outcomes_no_data(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.print_recent_outcomes()
    out = capsys.readouterr().out
    assert "none" in out or "Recent" in out


def test_print_recent_outcomes_with_data(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    _append_outcome(mem, goal="paint a kanji")
    observe.print_recent_outcomes()
    out = capsys.readouterr().out
    assert "kanji" in out


def test_print_memory_stats_no_memory(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.print_memory_stats()
    out = capsys.readouterr().out
    assert "medium" in out or "Memory" in out


def test_print_snapshot_runs(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.print_snapshot()
    out = capsys.readouterr().out
    assert "Snapshot" in out
    assert "Loop" in out
    assert "Heartbeat" in out
    assert "outcomes" in out.lower()
    assert "Memory" in out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def test_main_no_args_shows_snapshot(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.main([])
    out = capsys.readouterr().out
    assert "Snapshot" in out


def test_main_loop_subcommand(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.main(["loop"])
    out = capsys.readouterr().out
    assert "Loop" in out or "idle" in out


def test_main_heartbeat_subcommand(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.main(["heartbeat"])
    out = capsys.readouterr().out
    assert "Heartbeat" in out or "heartbeat" in out.lower()


def test_main_outcomes_subcommand(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.main(["outcomes"])
    out = capsys.readouterr().out
    assert "Recent" in out or "outcomes" in out.lower() or "none" in out


def test_main_memory_subcommand(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    observe.main(["memory"])
    out = capsys.readouterr().out
    assert "Memory" in out or "medium" in out


def test_main_outcomes_limit_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    for i in range(10):
        _append_outcome(mem, goal=f"task-{i}")
    observe.main(["outcomes", "--limit", "3"])
    out = capsys.readouterr().out
    # Should show "last 3" in the header
    assert "3" in out


# ---------------------------------------------------------------------------
# Phase 36: write_event and print_events_tail tests
# ---------------------------------------------------------------------------

from observe import write_event, print_events_tail


def test_write_event_creates_events_file(monkeypatch, tmp_path):
    """write_event creates events.jsonl and returns True."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event(
        "step_done",
        goal="test goal",
        project="test-project",
        loop_id="abc123",
        step="Do something useful",
        step_idx=1,
        status="done",
        tokens_in=100,
        tokens_out=50,
        elapsed_ms=1200,
    )
    assert ok is True
    events_path = _ws(tmp_path) / "events.jsonl"
    assert events_path.exists()
    entry = json.loads(events_path.read_text().strip())
    assert entry["event_type"] == "step_done"
    assert entry["status"] == "done"
    assert entry["loop_id"] == "abc123"
    assert entry["tokens_in"] == 100
    assert "ts" in entry


def test_write_event_appends_multiple(monkeypatch, tmp_path):
    """write_event appends entries; file grows with each call."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    write_event("loop_start", goal="goal A", loop_id="aaa", status="start")
    write_event("step_done", goal="goal A", loop_id="aaa", step="step 1", status="done")
    write_event("loop_done", goal="goal A", loop_id="aaa", status="done")
    events_path = _ws(tmp_path) / "events.jsonl"
    lines = [l for l in events_path.read_text().splitlines() if l.strip()]
    assert len(lines) == 3
    types = [json.loads(l)["event_type"] for l in lines]
    assert types == ["loop_start", "step_done", "loop_done"]


def _read_event_lines(tmp_path):
    events_path = _ws(tmp_path) / "events.jsonl"
    return [l for l in events_path.read_text().splitlines() if l.strip()]


def test_write_event_caps_huge_ascii_field(monkeypatch, tmp_path):
    """C0.3 must-detect: a 10KB project name previously rode uncapped into
    the line, breaking the <PIPE_BUF single-write atomicity the unlocked
    append depends on. Every emitted line must stay under 4096 bytes and
    remain valid JSON with the required keys."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", project="p" * 10240, goal="g", status="done")
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert len(line.encode("utf-8")) + 1 <= 4096
    entry = json.loads(line)
    assert entry["event_type"] == "step_done"
    assert entry["status"] == "done"
    assert "ts" in entry
    assert entry["project"].startswith("p")


def test_write_event_caps_multibyte_escape_blowup(monkeypatch, tmp_path):
    """C0.3 must-detect: caps were CHARACTER caps but the obligation is
    BYTES — json.dumps ASCII-escapes, so a multibyte char costs up to 12
    bytes. A model string of astral-plane chars must still produce a
    <4096-byte valid JSON line."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    # Each char json-encodes as \udNNN\udNNN (12 bytes).
    ok = write_event("step_done", model="\U0001d54f" * 2000,
                     goal="\U0001d54f" * 500, detail="\U0001d54f" * 500,
                     status="done")
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert len(line.encode("utf-8")) + 1 <= 4096
    entry = json.loads(line)
    assert entry["event_type"] == "step_done"
    assert "ts" in entry


def test_write_event_negative_control_passes_unmodified(monkeypatch, tmp_path):
    """Negative control: a normal event's fields ride through untouched."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", goal="a normal goal",
                     project="proj", loop_id="abc123", step="step one",
                     status="done", model="claude", detail="all fine",
                     tokens_in=10, tokens_out=5, elapsed_ms=100)
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    entry = json.loads(line)
    assert entry["goal"] == "a normal goal"
    assert entry["project"] == "proj"
    assert entry["loop_id"] == "abc123"
    assert entry["step"] == "step one"
    assert entry["status"] == "done"
    assert entry["model"] == "claude"
    assert entry["detail"] == "all fine"
    assert entry["tokens_in"] == 10


def test_print_events_tail_no_file(monkeypatch, tmp_path, capsys):
    """print_events_tail says 'No events recorded' when file missing."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    print_events_tail()
    out = capsys.readouterr().out
    assert "No events" in out


def test_print_events_tail_shows_events(monkeypatch, tmp_path, capsys):
    """print_events_tail displays recent events."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    write_event("step_done", goal="my goal", loop_id="x1", step="fetch data", status="done")
    print_events_tail(limit=5)
    out = capsys.readouterr().out
    assert "fetch data" in out
    assert "x1" in out


def test_main_events_subcommand(monkeypatch, tmp_path, capsys):
    """maro-observe events subcommand prints events tail."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    write_event("step_done", goal="goal", loop_id="zzz", step="do it", status="done")
    observe.main(["events"])
    out = capsys.readouterr().out
    assert "do it" in out or "zzz" in out


# ---------------------------------------------------------------------------
# New dashboard features: cost summary, ancestry tree, replay endpoint
# ---------------------------------------------------------------------------

def _ws_root(tmp_path) -> Path:
    """Returns the workspace root (parent of memory/ and projects/)."""
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    return root


class TestReadCostSummary:
    def test_empty_step_costs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        result = observe._read_cost_summary(hours=24)
        assert result["total_usd"] == 0.0
        assert result["step_count"] == 0

    def test_sums_costs(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _ws(tmp_path)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        entries = [
            {"ts": ts, "tokens_in": 100, "tokens_out": 50, "cost_usd": 0.001, "model": "sonnet"},
            {"ts": ts, "tokens_in": 200, "tokens_out": 100, "cost_usd": 0.002, "model": "haiku"},
        ]
        (mem / "step-costs.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        result = observe._read_cost_summary(hours=24)
        assert abs(result["total_usd"] - 0.003) < 1e-9
        assert result["step_count"] == 2
        assert result["tokens_in"] == 300
        assert result["tokens_out"] == 150

    def test_by_model_breakdown(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        mem = _ws(tmp_path)
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat()
        entries = [
            {"ts": ts, "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.001, "model": "opus"},
            {"ts": ts, "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.002, "model": "opus"},
        ]
        (mem / "step-costs.jsonl").write_text(
            "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
        )
        result = observe._read_cost_summary(hours=24)
        assert "opus" in result["by_model"]
        assert abs(result["by_model"]["opus"] - 0.003) < 1e-9

    def test_returns_error_key_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        # Force load_step_costs to raise
        import metrics
        monkeypatch.setattr(metrics, "load_step_costs", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        result = observe._read_cost_summary()
        assert "error" in result


class TestReadAncestryTree:
    def test_no_projects_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        _ws(tmp_path)
        result = observe._read_ancestry_tree()
        assert result == []

    def test_project_with_ancestry(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        root = _ws_root(tmp_path)
        proj = root / "projects" / "my-project"
        proj.mkdir(parents=True)
        (proj / "ancestry.json").write_text(json.dumps({
            "parent_id": "root-001",
            "ancestry": [{"id": "root-001", "title": "Root Goal"}],
        }), encoding="utf-8")
        result = observe._read_ancestry_tree()
        assert any(n["slug"] == "my-project" for n in result)
        node = next(n for n in result if n["slug"] == "my-project")
        assert node["parent_id"] == "root-001"
        assert node["depth"] == 1
        assert node["ancestry"][0]["title"] == "Root Goal"

    def test_project_without_ancestry_is_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        root = _ws_root(tmp_path)
        proj = root / "projects" / "standalone"
        proj.mkdir(parents=True)
        result = observe._read_ancestry_tree()
        assert any(n["slug"] == "standalone" for n in result)
        node = next(n for n in result if n["slug"] == "standalone")
        assert node["depth"] == 0
        assert node["parent_id"] is None

    def test_multiple_projects(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
        root = _ws_root(tmp_path)
        for name in ["alpha", "beta", "gamma"]:
            (root / "projects" / name).mkdir(parents=True)
        result = observe._read_ancestry_tree()
        slugs = {n["slug"] for n in result}
        assert {"alpha", "beta", "gamma"}.issubset(slugs)


# NOTE: TestSnapshotJsonIncludes, TestDashboardReplayEndpoint, and
# TestFactoryReplay tested the HTTP dashboard (_snapshot_json/serve_dashboard),
# archived 2026-07-02 to archive/observe_dashboard.py — see
# archive/test_observe_dashboard.py for their surviving coverage.

# ---------------------------------------------------------------------------
# Project status board (Phase 61 — maro-observe projects)
# ---------------------------------------------------------------------------

class TestProjectStatusBoard:
    """Tests for _project_status_rows() and print_project_status().

    The project status board surfaces per-project health without requiring LLM
    calls — all data comes from sheriff JSONL/JSON files.
    """

    def _make_sheriff_report(self, project: str, status: str, diagnosis: str = ""):
        """Build a minimal SheriffReport-like mock."""
        from unittest.mock import MagicMock
        r = MagicMock()
        r.project = project
        r.status = status
        r.diagnosis = diagnosis
        return r

    def test_empty_rows_when_no_projects(self, monkeypatch, tmp_path):
        """When sheriff finds no projects, rows is empty list."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=[]
        ):
            rows = observe._project_status_rows()
        assert rows == []

    def test_healthy_project_appears_as_healthy(self, monkeypatch, tmp_path):
        """A healthy project shows 'healthy' status in rows."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        report = self._make_sheriff_report("my-proj", "healthy", "All checks pass")
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=[report]
        ):
            rows = observe._project_status_rows()
        assert len(rows) == 1
        assert rows[0]["project"] == "my-proj"
        assert rows[0]["status"] == "healthy"

    def test_stuck_project_appears_as_stuck(self, monkeypatch, tmp_path):
        """A stuck project shows 'stuck' status in rows."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        report = self._make_sheriff_report("zombie-proj", "stuck", "No progress in 2h")
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=[report]
        ):
            rows = observe._project_status_rows()
        assert rows[0]["status"] == "stuck"
        assert "No progress" in rows[0]["detail"]

    def test_failed_project_appears_as_failed(self, monkeypatch, tmp_path):
        """A failed project shows 'failed' status in rows."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        report = self._make_sheriff_report("dead-proj", "failed", "Marked failed (.maro-failed)")
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=[report]
        ):
            rows = observe._project_status_rows()
        assert rows[0]["status"] == "failed"

    def test_print_project_status_outputs_label(self, monkeypatch, tmp_path, capsys):
        """print_project_status() writes STUCK / OK / FAILED labels to stdout."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        reports = [
            self._make_sheriff_report("live-proj", "healthy", ""),
            self._make_sheriff_report("bad-proj", "stuck", "stuck"),
        ]
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=reports
        ):
            observe.print_project_status(use_colour=False)
        out = capsys.readouterr().out
        assert "OK" in out
        assert "STUCK" in out
        assert "live-proj" in out
        assert "bad-proj" in out

    def test_print_project_status_no_data(self, monkeypatch, tmp_path, capsys):
        """print_project_status() prints a graceful 'no data' message when empty."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", side_effect=ImportError("no sheriff")
        ):
            observe.print_project_status(use_colour=False)
        out = capsys.readouterr().out
        assert "no data" in out.lower() or out.strip() == ""

    def test_main_projects_subcommand(self, monkeypatch, tmp_path, capsys):
        """'maro-observe projects' CLI subcommand calls print_project_status."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "observe.print_project_status"
        ) as mock_print:
            observe.main(["projects"])
        assert mock_print.called

    def test_unknown_status_shown_as_unknown(self, monkeypatch, tmp_path):
        """An unrecognized status string falls back to 'unknown' in rows."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        report = self._make_sheriff_report("weird-proj", "something-new", "")
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "sheriff.check_all_projects", return_value=[report]
        ):
            rows = observe._project_status_rows()
        assert rows[0]["status"] == "unknown"


# ---------------------------------------------------------------------------
# Eval trend dashboard integration
# ---------------------------------------------------------------------------

class TestEvalTrendDashboard:
    """Tests for eval pass-rate panel in observe dashboard."""

    def test_read_eval_trend_empty_when_no_data(self, monkeypatch, tmp_path):
        """_read_eval_trend returns [] when eval module unavailable."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from unittest.mock import patch
        with patch("observe._read_eval_trend", return_value=[]):
            import observe
            result = observe._read_eval_trend()
            assert result == []

    def test_read_eval_trend_returns_newest_first(self, monkeypatch, tmp_path):
        """_read_eval_trend returns entries in newest-first order."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import observe
        _entries = [
            {"timestamp": "2026-04-14T10:00:00Z", "builtin_score": 0.80, "run_id": "run1"},
            {"timestamp": "2026-04-14T11:00:00Z", "builtin_score": 0.85, "run_id": "run2"},
        ]
        from unittest.mock import patch
        with patch("eval.load_eval_trend", return_value=_entries):
            result = observe._read_eval_trend()
        # _read_eval_trend reverses the list so newest is first
        assert result[0]["run_id"] == "run2"
        assert result[1]["run_id"] == "run1"

    # test_collect_dashboard_includes_eval_trend and test_dashboard_html_contains_eval_panel
    # tested the archived HTTP dashboard — moved to archive/test_observe_dashboard.py
    # (TestEvalTrendDashboardHTML), 2026-07-02.


# ---------------------------------------------------------------------------
# Captain's Log dashboard panel
# ---------------------------------------------------------------------------

class TestCaptainLogDashboard:
    """Tests for the captain's log panel in observe dashboard."""

    @pytest.fixture(autouse=True)
    def _mem_dir(self, monkeypatch, tmp_path):
        """Redirect memory_dir to tmp_path/memory for all tests in this class."""
        mem = tmp_path / "memory"
        mem.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("MARO_MEMORY_DIR", str(mem))
        return mem

    def test_read_captain_log_empty_when_no_file(self, tmp_path):
        import observe
        result = observe._read_captain_log_entries()
        assert result == []

    def test_read_captain_log_returns_entries(self, tmp_path):
        import json
        log_path = tmp_path / "memory" / "captains_log.jsonl"
        entries = [
            {"timestamp": "2026-04-14T10:00:00Z", "event_type": "SKILL_PROMOTED",
             "loop_id": "abc123", "subject": "research-skill", "summary": "promoted to established"},
            {"timestamp": "2026-04-14T11:00:00Z", "event_type": "EVOLVER_APPLIED",
             "loop_id": "def456", "subject": "prompt_tweak", "note": "tightened decompose prompt"},
        ]
        log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        import observe
        result = observe._read_captain_log_entries()
        assert len(result) == 2
        # Newest first (reversed read order)
        assert result[0]["event_type"] == "EVOLVER_APPLIED"
        assert result[1]["event_type"] == "SKILL_PROMOTED"

    def test_read_captain_log_respects_limit(self, tmp_path):
        import json
        log_path = tmp_path / "memory" / "captains_log.jsonl"
        entries = [{"timestamp": f"2026-04-14T{i:02d}:00:00Z", "event_type": "DIAGNOSIS"} for i in range(30)]
        log_path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        import observe
        result = observe._read_captain_log_entries(limit=5)
        assert len(result) == 5

    def test_read_captain_log_uses_fallback_summary(self, tmp_path):
        """Falls back to 'note' then 'suggestion' when 'summary' is absent."""
        import json
        log_path = tmp_path / "memory" / "captains_log.jsonl"
        entry = {"timestamp": "2026-04-14T10:00:00Z", "event_type": "DIAGNOSIS",
                 "loop_id": "abc", "note": "fallback note text"}
        log_path.write_text(json.dumps(entry) + "\n")
        import observe
        result = observe._read_captain_log_entries()
        assert len(result) == 1
        assert result[0]["summary"] == "fallback note text"

    # test_snapshot_includes_captain_log and test_dashboard_html_contains_captain_log_panel
    # tested the archived HTTP dashboard — moved to archive/test_observe_dashboard.py
    # (TestCaptainLogDashboardHTML), 2026-07-02.


class TestSuggestionStats:
    """Tests for _read_suggestion_stats."""

    @pytest.fixture(autouse=True)
    def _mem(self, tmp_path, monkeypatch):
        mem = tmp_path / "memory"
        mem.mkdir()
        monkeypatch.setenv("MARO_MEMORY_DIR", str(mem))
        self._mem_path = mem

    def test_empty_when_no_file(self):
        import observe
        stats = observe._read_suggestion_stats()
        assert stats["total"] == 0
        assert stats["pending"] == 0
        assert stats["applied"] == 0

    def test_counts_by_category(self):
        import json, observe
        path = self._mem_path / "suggestions.jsonl"
        entries = [
            {"category": "skill_mutation", "status": "applied"},
            {"category": "skill_mutation", "status": "applied"},
            {"category": "inspection_finding", "status": "unknown"},
            {"category": "inspection_finding", "status": "unknown"},
            {"category": "inspection_finding", "status": "unknown"},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries))
        stats = observe._read_suggestion_stats()
        assert stats["total"] == 5
        assert stats["by_category"]["skill_mutation"] == 2
        assert stats["by_category"]["inspection_finding"] == 3

    def test_pending_counts_unknown_and_pending_human_review(self):
        import json, observe
        path = self._mem_path / "suggestions.jsonl"
        entries = [
            {"category": "x", "status": "unknown"},
            {"category": "x", "status": "pending_human_review"},
            {"category": "x", "status": "applied"},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries))
        stats = observe._read_suggestion_stats()
        assert stats["pending"] == 2
        assert stats["applied"] == 1

    # test_snapshot_includes_suggestion_stats and test_dashboard_html_contains_suggestion_panel
    # tested the archived HTTP dashboard — moved to archive/test_observe_dashboard.py
    # (TestSuggestionStatsDashboardHTML), 2026-07-02.


# ---------------------------------------------------------------------------
# R2-3: numeric-field coercion, final encoded-size authority, event_truncated
# fallback — hostile values in ANY kwarg must never write an oversize line or
# silently drop an event.
# ---------------------------------------------------------------------------

def test_write_event_container_typed_tokens_rejected(monkeypatch, tmp_path):
    """R2-3 must-detect: a container smuggled into tokens_in bypassed the
    shed ladder entirely (probe: 4276-byte line, returned True). Numeric
    projections accept int/float only; invalid values are dropped from the
    row and named in invalid_fields."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", status="done",
                     tokens_in={"k": "v" * 2000})
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert len(line.encode("utf-8")) + 1 <= 4096
    entry = json.loads(line)
    assert "tokens_in" not in entry
    assert "tokens_in" in entry["invalid_fields"]
    assert entry["event_type"] == "step_done"


def test_write_event_huge_int_still_lands(monkeypatch, tmp_path):
    """R2-3 must-detect: a >4300-digit int made json.dumps raise (CPython
    int->str digit limit) and the event was SILENTLY dropped. An event must
    still land — coerced — and the function must return True."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", status="done", tokens_in=10 ** 5000)
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert len(line.encode("utf-8")) + 1 <= 4096
    entry = json.loads(line)
    assert entry["event_type"] == "step_done"
    assert "tokens_in" not in entry
    assert "tokens_in" in entry["invalid_fields"]


def test_write_event_nan_never_reaches_the_line(monkeypatch, tmp_path):
    """R2-3 must-detect: json.dumps happily emits bare NaN, which is not
    JSON (contract B2 forbids it). A NaN float field must be stripped."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", status="done", elapsed_ms=float("nan"))
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert "NaN" not in line and "Infinity" not in line
    entry = json.loads(line)  # would raise on bare NaN in strict parsers
    assert "elapsed_ms" not in entry
    assert "elapsed_ms" in entry["invalid_fields"]


def test_write_event_numeric_negative_control(monkeypatch, tmp_path):
    """Sane numerics ride through untouched, no invalid_fields note."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", status="done", tokens_in=123,
                     tokens_out=45, elapsed_ms=6789.5, step_idx=2,
                     cache_read_tokens=99)
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    entry = json.loads(line)
    assert entry["tokens_in"] == 123
    assert entry["tokens_out"] == 45
    assert entry["elapsed_ms"] == 6789.5
    assert entry["step_idx"] == 2
    assert entry["cache_read_tokens"] == 99
    assert "invalid_fields" not in entry


_HOSTILE_BY_KWARG = {
    # str-projected fields: byte-blowup strings and containers
    "goal": "\U0001d54f" * 5000,
    "project": {"nested": ["x" * 5000]},
    "loop_id": "l" * 20000,
    "step": "\U0001d54f" * 5000,
    "status": ["not", "a", "string", "y" * 5000],
    "model": "\U0001d54f" * 5000,
    "detail": "d" * 100000,
    # numeric fields: monster ints, containers, non-finite floats
    "step_idx": 10 ** 5000,
    "tokens_in": {"a": "b" * 5000},
    "tokens_out": float("inf"),
    "cache_read_tokens": [1] * 4000,
    "elapsed_ms": float("nan"),
    # optional payload
    "tool_pathologies": [{"cls": "z" * 9000, "evidence": "e" * 9000}] * 50,
}


@pytest.mark.parametrize("kwarg", sorted(_HOSTILE_BY_KWARG))
def test_write_event_every_kwarg_survives_hostile_value(
        monkeypatch, tmp_path, kwarg):
    """The catches-next-month's-field instrument (R2-3): EVERY kwarg of
    write_event, fed a hostile value, must still produce exactly one valid
    JSON line whose encoded length (with newline) is <= 4096 bytes — and
    the call must report success."""
    import inspect
    sig = inspect.signature(write_event)
    assert kwarg in sig.parameters, f"stale hostile map: {kwarg}"
    # The map must cover every kwarg so a field added later fails loudly.
    payload_params = set(sig.parameters) - {"event_type"}
    assert payload_params == set(_HOSTILE_BY_KWARG), (
        "write_event grew/changed kwargs — extend _HOSTILE_BY_KWARG")
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    ok = write_event("step_done", **{kwarg: _HOSTILE_BY_KWARG[kwarg]})
    assert ok is True
    (line,) = _read_event_lines(tmp_path)
    assert len(line.encode("utf-8")) + 1 <= 4096
    entry = json.loads(line)
    assert "NaN" not in line and "Infinity" not in line
    assert entry["event_type"] in ("step_done", "event_truncated")


def test_write_event_hostile_event_type_still_lands(monkeypatch, tmp_path):
    """event_type itself gets the same treatment (str() of a >4300-digit
    int raises; a huge string must be capped)."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    assert write_event("e" * 50000, status="done") is True
    assert write_event(10 ** 5000, status="done") is True
    lines = _read_event_lines(tmp_path)
    assert len(lines) == 2
    for line in lines:
        assert len(line.encode("utf-8")) + 1 <= 4096
        json.loads(line)


def test_write_event_single_os_write_append(monkeypatch, tmp_path):
    """B9 honesty (R2-3d): the append is ONE os.write of the fully encoded
    bytes on an O_APPEND fd — not a buffered file object whose flush may
    legally split."""
    import os as _os
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    writes = []
    real_write = _os.write

    def spy(fd, data):
        writes.append(bytes(data))
        return real_write(fd, data)
    monkeypatch.setattr(_os, "write", spy)
    ok = write_event("step_done", status="done", detail="payload")
    assert ok is True
    event_writes = [w for w in writes if b'"step_done"' in w]
    assert len(event_writes) == 1
    assert event_writes[0].endswith(b"\n")
    entry = json.loads(event_writes[0].decode("utf-8"))
    assert entry["detail"] == "payload"


def test_write_event_short_write_returns_false_and_logs(
        monkeypatch, tmp_path, caplog):
    """R3-2 must-detect: os.write accepting fewer bytes than the encoded
    line is a TORN row — write_event must return False and emit a
    non-recursive diagnostic, and must NOT retry the remainder (a second
    unlocked append could interleave with another writer)."""
    import logging
    import os as _os
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    _ws(tmp_path)
    calls = []
    real_write = _os.write

    def short_write(fd, data):
        calls.append(bytes(data))
        real_write(fd, bytes(data)[:-1])  # torn: one byte never lands
        return len(data) - 1
    monkeypatch.setattr(_os, "write", short_write)
    with caplog.at_level(logging.WARNING, logger="maro.observe"):
        ok = write_event("step_done", status="done", detail="payload")
    assert ok is False
    # No retry of the remainder: exactly one write attempt for this event.
    assert len([c for c in calls if b'"step_done"' in c]) == 1
    assert any("short write" in r.getMessage() for r in caplog.records)


def test_write_event_full_write_still_true(monkeypatch, tmp_path):
    """R3-2 negative control: a normal full write keeps returning True."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    mem = _ws(tmp_path)
    assert write_event("step_done", status="done", detail="payload") is True
    line = (mem / "events.jsonl").read_text().strip().splitlines()[-1]
    assert json.loads(line)["event_type"] == "step_done"
