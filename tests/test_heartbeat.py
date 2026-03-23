"""Tests for heartbeat.py — Phase 4 completion."""

import json
import sys
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
