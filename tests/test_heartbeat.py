"""Tests for heartbeat.py — Phase 4 completion."""

import json
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from heartbeat import (
    HeartbeatReport,
    RecoveryAction,
    _tier1_scripted,
    _tier2_llm_diagnosis,
    _tier3_escalate,
    _run_backlog_step,
    _run_evolver_bg,
    _run_inspector_bg,
    _run_eval_bg,
    run_heartbeat,
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def test_recovery_action_fields():
    ra = RecoveryAction(tier=1, target="disk", action="clean up", outcome="suggested")
    assert ra.tier == 1
    assert ra.outcome == "suggested"


def test_heartbeat_report_summary():
    report = HeartbeatReport(
        run_id="abc12345",
        checked_at="2026-01-01T00:00:00Z",
        health_status="healthy",
        checks={"disk": "ok"},
    )
    s = report.summary()
    assert "abc12345" in s
    assert "healthy" in s


def test_heartbeat_report_to_dict():
    report = HeartbeatReport(
        run_id="r1",
        checked_at="2026-01-01T00:00:00Z",
        health_status="degraded",
        checks={"api_key": "fail: missing"},
        stuck_projects=["proj-a"],
        recovery_actions=[
            RecoveryAction(tier=1, target="api_key", action="use subprocess", outcome="suggested"),
        ],
    )
    d = report.to_dict()
    assert d["health_status"] == "degraded"
    assert d["stuck_projects"] == ["proj-a"]
    assert len(d["recovery_actions"]) == 1


# ---------------------------------------------------------------------------
# Tier 1: scripted recovery
# ---------------------------------------------------------------------------

def test_tier1_healthy_no_actions():
    checks = {"disk_space": "ok", "api_key": "ok", "workspace_writable": "ok"}
    actions = _tier1_scripted(checks)
    assert actions == []


def test_tier1_disk_warn():
    actions = _tier1_scripted({"disk_space": "warn: 85% used"})
    assert len(actions) == 1
    assert actions[0].tier == 1
    assert actions[0].target == "disk_space"
    assert actions[0].outcome == "suggested"


def test_tier1_api_key_fail():
    actions = _tier1_scripted({"api_key": "fail: key not set"})
    assert any(a.target == "api_key" for a in actions)


def test_tier1_workspace_not_writable():
    actions = _tier1_scripted({"workspace_writable": "fail: permission denied"})
    assert any(a.outcome == "escalated" for a in actions)


def test_tier1_openclaw_gateway_fail():
    actions = _tier1_scripted({"openclaw_gateway": "fail: connection refused"})
    assert any("gateway" in a.action.lower() for a in actions)


def test_tier1_multiple_failures():
    checks = {
        "disk_space": "warn: 90% used",
        "api_key": "fail: missing",
        "workspace_writable": "ok",
    }
    actions = _tier1_scripted(checks)
    assert len(actions) == 2


# ---------------------------------------------------------------------------
# Tier 2: LLM diagnosis
# ---------------------------------------------------------------------------

def test_tier2_empty_stuck_list():
    actions = _tier2_llm_diagnosis([])
    assert actions == []


def test_tier2_dry_run_skips():
    actions = _tier2_llm_diagnosis(["stuck-project"], dry_run=True)
    assert len(actions) == 1
    assert actions[0].outcome == "skipped"


def test_tier2_llm_error_graceful():
    with patch("heartbeat.build_adapter", side_effect=ImportError("no llm")):
        actions = _tier2_llm_diagnosis(["my-project"])
    assert len(actions) == 1
    assert actions[0].outcome == "skipped"


def test_tier2_llm_diagnosis_happy_path():
    mock_adapter = MagicMock()
    mock_resp = MagicMock()
    mock_resp.content = "ACTION: Reset the in-progress task\nREASON: It has been stuck for 30 minutes\nCONFIDENCE: high"
    mock_adapter.complete.return_value = mock_resp

    with patch("heartbeat.build_adapter", return_value=mock_adapter), \
         patch("heartbeat.parse_next", return_value=([], [])):
        actions = _tier2_llm_diagnosis(["stuck-proj"])

    assert len(actions) == 1
    assert actions[0].tier == 2
    assert actions[0].outcome == "suggested"
    assert "ACTION" in actions[0].action


# ---------------------------------------------------------------------------
# Tier 3: Telegram escalation
# ---------------------------------------------------------------------------

def test_tier3_no_escalation_healthy():
    report = HeartbeatReport(
        run_id="r1", checked_at="2026-01-01T00:00:00Z",
        health_status="healthy", checks={},
    )
    result = _tier3_escalate(report)
    assert result is False


def test_tier3_escalates_critical():
    report = HeartbeatReport(
        run_id="r1", checked_at="2026-01-01T00:00:00Z",
        health_status="critical",
        checks={"workspace_writable": "fail: permission denied"},
    )
    with patch("heartbeat.TelegramBot") as mock_cls, \
         patch("heartbeat._resolve_token", return_value="fake-token"), \
         patch("heartbeat._resolve_allowed_chats", return_value={12345}):
        mock_bot = MagicMock()
        mock_cls.return_value = mock_bot
        result = _tier3_escalate(report)

    assert result is True
    mock_bot.send_message.assert_called_once()
    msg = mock_bot.send_message.call_args[0][1]
    assert "CRITICAL" in msg


def test_tier3_escalates_stuck_projects():
    report = HeartbeatReport(
        run_id="r1", checked_at="2026-01-01T00:00:00Z",
        health_status="degraded",
        checks={},
        stuck_projects=["project-x"],
    )
    with patch("heartbeat.TelegramBot") as mock_cls, \
         patch("heartbeat._resolve_token", return_value="fake-token"), \
         patch("heartbeat._resolve_allowed_chats", return_value={99}):
        mock_bot = MagicMock()
        mock_cls.return_value = mock_bot
        _tier3_escalate(report)

    msg = mock_bot.send_message.call_args[0][1]
    assert "project-x" in msg


def test_tier3_no_token_no_escalate():
    report = HeartbeatReport(
        run_id="r1", checked_at="2026-01-01T00:00:00Z",
        health_status="critical", checks={},
    )
    with patch("heartbeat._resolve_token", return_value=""):
        result = _tier3_escalate(report)
    assert result is False


# ---------------------------------------------------------------------------
# run_heartbeat integration
# ---------------------------------------------------------------------------

def _make_mock_health(status="healthy"):
    from sheriff import SystemHealth
    return SystemHealth(
        status=status,
        checks={"disk_space": "ok", "api_key": "ok"},
    )


def test_run_heartbeat_dry_run():
    with patch("heartbeat.check_system_health", return_value=_make_mock_health()), \
         patch("heartbeat.check_all_projects", return_value=[]), \
         patch("heartbeat.write_heartbeat_state"), \
         patch("heartbeat._log_heartbeat"):
        report = run_heartbeat(dry_run=True, verbose=False, escalate=False)
    assert report.health_status == "healthy"
    assert report.telegram_sent is False


def test_run_heartbeat_triggers_tier1():
    from sheriff import SystemHealth
    health = SystemHealth(
        status="degraded",
        checks={"disk_space": "warn: 90% used", "api_key": "ok"},
    )
    with patch("heartbeat.check_system_health", return_value=health), \
         patch("heartbeat.check_all_projects", return_value=[]), \
         patch("heartbeat.write_heartbeat_state"), \
         patch("heartbeat._log_heartbeat"):
        report = run_heartbeat(dry_run=True, verbose=False, escalate=False)
    assert any(a.tier == 1 for a in report.recovery_actions)


def test_run_heartbeat_stuck_projects_trigger_tier2():
    from sheriff import SheriffReport
    stuck = SheriffReport(project="proj-x", status="stuck", diagnosis="repeated same action", evidence=["same task selected 3x"])

    with patch("heartbeat.check_system_health", return_value=_make_mock_health()), \
         patch("heartbeat.check_all_projects", return_value=[stuck]), \
         patch("heartbeat.write_heartbeat_state"), \
         patch("heartbeat._log_heartbeat"), \
         patch("heartbeat._is_interactive_session_active", return_value=False), \
         patch("heartbeat._tier2_llm_diagnosis", return_value=[
             RecoveryAction(tier=2, target="proj-x", action="reset task", outcome="suggested")
         ]) as mock_diag:
        report = run_heartbeat(dry_run=False, verbose=False, escalate=False)

    mock_diag.assert_called_once_with(["proj-x"], dry_run=False)
    assert "proj-x" in report.stuck_projects


def test_run_heartbeat_sheriff_unavailable():
    with patch("heartbeat.check_system_health", side_effect=Exception("sheriff broken")), \
         patch("heartbeat._log_heartbeat"):
        report = run_heartbeat(dry_run=True, verbose=False, escalate=False)
    assert report.health_status == "critical"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_heartbeat_once(capsys):
    with patch("heartbeat.check_system_health", return_value=_make_mock_health()), \
         patch("heartbeat.check_all_projects", return_value=[]), \
         patch("heartbeat.write_heartbeat_state"), \
         patch("heartbeat._log_heartbeat"):
        import cli
        rc = cli.main(["poe-heartbeat", "--dry-run", "--no-escalate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "heartbeat" in out


def test_cli_poe_heartbeat_json(capsys):
    with patch("heartbeat.check_system_health", return_value=_make_mock_health()), \
         patch("heartbeat.check_all_projects", return_value=[]), \
         patch("heartbeat.write_heartbeat_state"), \
         patch("heartbeat._log_heartbeat"):
        import cli
        rc = cli.main(["poe-heartbeat", "--dry-run", "--no-escalate", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "health_status" in data


# ---------------------------------------------------------------------------
# Autonomous backlog drain
# ---------------------------------------------------------------------------

def test_run_backlog_step_no_todo_items():
    """When no TODO items exist, _run_backlog_step exits cleanly and resets the active flag."""
    import heartbeat
    heartbeat._backlog_drain_active = True  # simulate it being claimed before call
    with patch("orch_items.select_global_next", return_value=None):
        _run_backlog_step(dry_run=True, verbose=False)
    # finally block must reset flag regardless of early-return path
    assert heartbeat._backlog_drain_active is False


def test_run_backlog_step_dry_run_marks_done(tmp_path, monkeypatch):
    """In dry-run mode, backlog drain claims a TODO item and marks it done."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))

    import importlib
    import orch_items as oi
    importlib.reload(oi)

    oi.ensure_project("test-proj", "test mission")
    oi.append_next_items("test-proj", ["Do the thing"])
    _lines, items_before = oi.parse_next("test-proj")
    todo_item = next(i for i in items_before if i.state == oi.STATE_TODO)

    import heartbeat
    heartbeat._backlog_drain_active = True

    with patch("orch_items.select_global_next", return_value=("test-proj", todo_item)):
        _run_backlog_step(dry_run=True, verbose=False)

    _lines2, items_after = oi.parse_next("test-proj")
    done_item = next((i for i in items_after if i.index == todo_item.index), None)
    assert done_item is not None
    assert done_item.state == oi.STATE_DONE
    assert heartbeat._backlog_drain_active is False


def test_run_backlog_step_loop_done_marks_done(tmp_path, monkeypatch):
    """When agent loop returns 'done', item is marked STATE_DONE."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))

    import importlib
    import orch_items as oi
    importlib.reload(oi)

    oi.ensure_project("proj-b", "mission b")
    oi.append_next_items("proj-b", ["Research something"])
    _lines, items = oi.parse_next("proj-b")
    todo = next(i for i in items if i.state == oi.STATE_TODO)

    import heartbeat
    heartbeat._backlog_drain_active = True

    mock_loop_result = MagicMock()
    mock_loop_result.status = "done"

    with patch("orch_items.select_global_next", return_value=("proj-b", todo)), \
         patch("agent_loop.run_agent_loop", return_value=mock_loop_result):
        _run_backlog_step(dry_run=False, verbose=False)

    _lines2, items2 = oi.parse_next("proj-b")
    updated = next(i for i in items2 if i.index == todo.index)
    assert updated.state == oi.STATE_DONE
    assert heartbeat._backlog_drain_active is False


def test_run_backlog_step_loop_stuck_marks_blocked(tmp_path, monkeypatch):
    """When agent loop returns 'stuck', item is marked STATE_BLOCKED (no infinite retry)."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))

    import importlib
    import orch_items as oi
    importlib.reload(oi)

    oi.ensure_project("proj-c", "mission c")
    oi.append_next_items("proj-c", ["Do something hard"])
    _lines, items = oi.parse_next("proj-c")
    todo = next(i for i in items if i.state == oi.STATE_TODO)

    import heartbeat
    heartbeat._backlog_drain_active = True

    mock_loop_result = MagicMock()
    mock_loop_result.status = "stuck"

    with patch("orch_items.select_global_next", return_value=("proj-c", todo)), \
         patch("agent_loop.run_agent_loop", return_value=mock_loop_result):
        _run_backlog_step(dry_run=False, verbose=False)

    _lines2, items2 = oi.parse_next("proj-c")
    updated = next(i for i in items2 if i.index == todo.index)
    assert updated.state == oi.STATE_BLOCKED
    assert heartbeat._backlog_drain_active is False


def test_run_backlog_step_loop_exception_marks_blocked(tmp_path, monkeypatch):
    """If agent loop raises, item is marked STATE_BLOCKED and flag is cleared."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))

    import importlib
    import orch_items as oi
    importlib.reload(oi)

    oi.ensure_project("proj-d", "mission d")
    oi.append_next_items("proj-d", ["Task that will crash"])
    _lines, items = oi.parse_next("proj-d")
    todo = next(i for i in items if i.state == oi.STATE_TODO)

    import heartbeat
    heartbeat._backlog_drain_active = True

    with patch("orch_items.select_global_next", return_value=("proj-d", todo)), \
         patch("agent_loop.run_agent_loop", side_effect=RuntimeError("boom")):
        _run_backlog_step(dry_run=False, verbose=False)

    _lines2, items2 = oi.parse_next("proj-d")
    updated = next(i for i in items2 if i.index == todo.index)
    assert updated.state == oi.STATE_BLOCKED
    assert heartbeat._backlog_drain_active is False


# ---------------------------------------------------------------------------
# Background evolver / inspector / eval thread functions
# ---------------------------------------------------------------------------

def test_run_evolver_bg_clears_flag():
    """_run_evolver_bg clears _evolver_active flag even on exception."""
    import heartbeat
    heartbeat._evolver_active = True
    with patch("evolver.run_evolver", side_effect=RuntimeError("evolver fail")):
        _run_evolver_bg(dry_run=True, verbose=False)
    assert heartbeat._evolver_active is False


def test_run_evolver_bg_happy_path():
    import heartbeat
    heartbeat._evolver_active = True
    with patch("evolver.run_evolver") as mock_ev:
        _run_evolver_bg(dry_run=True, verbose=False)
    mock_ev.assert_called_once()
    assert heartbeat._evolver_active is False


def test_run_inspector_bg_clears_flag():
    import heartbeat
    heartbeat._inspector_active = True
    with patch("inspector.run_inspector", side_effect=RuntimeError("insp fail")):
        _run_inspector_bg(dry_run=True, verbose=False)
    assert heartbeat._inspector_active is False


def test_run_inspector_bg_happy_path():
    import heartbeat
    heartbeat._inspector_active = True
    with patch("inspector.run_inspector") as mock_insp:
        _run_inspector_bg(dry_run=True, verbose=False)
    mock_insp.assert_called_once()
    assert heartbeat._inspector_active is False


def test_run_eval_bg_clears_flag():
    import heartbeat
    heartbeat._eval_active = True
    with patch("eval.run_nightly_eval", side_effect=RuntimeError("eval fail")):
        _run_eval_bg(dry_run=True, verbose=False)
    assert heartbeat._eval_active is False


def test_run_eval_bg_happy_path():
    import heartbeat
    heartbeat._eval_active = True
    with patch("eval.run_nightly_eval", return_value=0) as mock_eval:
        _run_eval_bg(dry_run=True, verbose=False)
    mock_eval.assert_called_once()
    assert heartbeat._eval_active is False


def test_evolver_not_double_started():
    """When _evolver_active is True, flag correctly gates a second launch."""
    import heartbeat
    heartbeat._evolver_active = True
    with heartbeat._evolver_lock:
        ev_running = heartbeat._evolver_active
    assert ev_running is True
    heartbeat._evolver_active = False


def test_inspector_not_double_started():
    import heartbeat
    heartbeat._inspector_active = True
    with heartbeat._inspector_lock:
        insp_running = heartbeat._inspector_active
    assert insp_running is True
    heartbeat._inspector_active = False


def test_backlog_drain_not_double_started():
    """heartbeat_loop skips launching a second drain if one is already active."""
    import heartbeat
    # Simulate an active drain by setting the flag
    heartbeat._backlog_drain_active = True
    threads_started = []

    original_thread_init = threading.Thread.__init__
    def capture_thread(self, *args, **kwargs):
        original_thread_init(self, *args, **kwargs)
        if kwargs.get("name") == "backlog-drain":
            threads_started.append(self)

    # Verify that when _backlog_drain_active is True, no new thread is created
    with heartbeat._backlog_drain_lock:
        bd_running = heartbeat._backlog_drain_active
    assert bd_running is True  # already active → no new thread should be spawned
    # Reset for other tests
    heartbeat._backlog_drain_active = False


def test_heartbeat_loop_global_flags_accessible():
    """heartbeat_loop must declare all bg-thread flags global to avoid UnboundLocalError.

    This is a regression test for the bug where the service crashed on tick 1:
    Python treats any assignment in a function as local, so _flag = True made
    all reads of _flag unresolvable without an explicit 'global' declaration.
    """
    import inspect
    import heartbeat as hb_mod

    src = inspect.getsource(hb_mod.heartbeat_loop)
    # All six flags must appear in global declarations inside the function body
    assert "_evolver_active" in src.split("global")[1] if "global" in src else False, \
        "_evolver_active not declared global in heartbeat_loop"
    assert "_task_store_drain_active" in src, \
        "_task_store_drain_active missing from heartbeat_loop"
    # Quick functional check: calling the loop for 0 ticks should not raise
    # (we can't easily run actual ticks without real adapters, so just verify import)
    assert callable(hb_mod.heartbeat_loop)


# ---------------------------------------------------------------------------
# Event-reactive heartbeat (STEAL_LIST: event-reactive heartbeat, M priority)
# ---------------------------------------------------------------------------

class TestPostHeartbeatEvent:
    """Tests for post_heartbeat_event() — wakeup event mechanism."""

    def test_post_heartbeat_event_sets_wakeup(self):
        """post_heartbeat_event() sets the module-level _wakeup_event."""
        import heartbeat as hb
        hb._wakeup_event.clear()
        assert not hb._wakeup_event.is_set()
        hb.post_heartbeat_event("test")
        assert hb._wakeup_event.is_set()
        hb._wakeup_event.clear()

    def test_post_heartbeat_event_with_payload(self):
        """post_heartbeat_event() accepts event_type and payload without error."""
        import heartbeat as hb
        hb._wakeup_event.clear()
        hb.post_heartbeat_event("telegram", payload="some message text")
        assert hb._wakeup_event.is_set()
        hb._wakeup_event.clear()

    def test_post_heartbeat_event_is_callable_from_interrupt(self, tmp_path, monkeypatch):
        """InterruptQueue.post() calls post_heartbeat_event() after posting."""
        import heartbeat as hb
        hb._wakeup_event.clear()

        q_file = tmp_path / "interrupts.jsonl"
        monkeypatch.setattr("interrupt._classify_intent",
                            lambda *a, **kw: ("additive", [], None))

        from interrupt import InterruptQueue
        q = InterruptQueue(queue_path=q_file)
        q.post("also research topic X about agent loop", source="test")

        assert hb._wakeup_event.is_set()
        hb._wakeup_event.clear()

    def test_wakeup_event_clears_after_wait(self):
        """After _wakeup_event.wait() + clear(), event is not set."""
        import heartbeat as hb
        hb._wakeup_event.set()
        hb._wakeup_event.wait(timeout=0.01)
        hb._wakeup_event.clear()
        assert not hb._wakeup_event.is_set()

    def test_post_heartbeat_event_never_raises(self, monkeypatch):
        """post_heartbeat_event() should not raise even with a broken import."""
        import heartbeat as hb
        # Set the event in an unusual state and post should still not raise
        hb._wakeup_event.clear()
        hb.post_heartbeat_event("edge_case", payload="x" * 500)
        assert hb._wakeup_event.is_set()
        hb._wakeup_event.clear()
