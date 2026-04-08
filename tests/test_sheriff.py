"""Tests for Phase 4: sheriff.py (Loop Sheriff + system health)."""

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from sheriff import (
    SheriffReport,
    SystemHealth,
    check_project,
    check_all_projects,
    check_system_health,
    detect_no_progress,
    fingerprint_project_state,
    write_heartbeat_state,
    read_heartbeat_state,
    mark_project_failed,
    mark_project_paused,
    project_lifecycle_state,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


def _mkproj(tmp_path, slug, content="- [ ] first task\n- [ ] second task\n"):
    p = tmp_path / "prototypes" / "poe-orchestration" / "projects" / slug
    p.mkdir(parents=True)
    (p / "NEXT.md").write_text(content, encoding="utf-8")
    (p / "PRIORITY").write_text("0\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# detect_no_progress
# ---------------------------------------------------------------------------

def test_no_progress_detected_on_repeated_fingerprints():
    fps = ["abc", "abc", "abc"]
    assert detect_no_progress(fps) is True


def test_progress_detected_when_fingerprints_differ():
    fps = ["abc", "def", "ghi"]
    assert detect_no_progress(fps) is False


def test_no_progress_not_triggered_with_too_few():
    fps = ["abc", "abc"]
    assert detect_no_progress(fps) is False


def test_no_progress_not_triggered_on_empty_fingerprint():
    fps = ["", "", ""]
    assert detect_no_progress(fps) is False


def test_partial_progress_not_stuck():
    fps = ["abc", "def", "def"]  # moved once, then stalled — below threshold
    assert detect_no_progress(fps) is False


# ---------------------------------------------------------------------------
# fingerprint_project_state
# ---------------------------------------------------------------------------

def test_fingerprint_changes_when_next_changes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "fp-test")
    fp1 = fingerprint_project_state("fp-test")

    # Modify NEXT.md
    proj_dir = tmp_path / "prototypes" / "poe-orchestration" / "projects" / "fp-test"
    (proj_dir / "NEXT.md").write_text("- [x] done\n- [ ] remaining\n", encoding="utf-8")

    fp2 = fingerprint_project_state("fp-test")
    assert fp1 != fp2


def test_fingerprint_stable_when_nothing_changes(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "fp-stable")
    fp1 = fingerprint_project_state("fp-stable")
    fp2 = fingerprint_project_state("fp-stable")
    assert fp1 == fp2


def test_fingerprint_nonexistent_project():
    # Should not raise — returns a consistent value (may be empty hash or empty string)
    fp = fingerprint_project_state("does-not-exist-ever")
    assert isinstance(fp, str)


# ---------------------------------------------------------------------------
# check_project
# ---------------------------------------------------------------------------

def test_check_healthy_project(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "healthy-proj")
    report = check_project("healthy-proj")
    assert isinstance(report, SheriffReport)
    assert report.project == "healthy-proj"
    assert report.status == "healthy"


def test_check_nonexistent_project(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    report = check_project("no-such-project")
    assert report.status == "unknown"
    assert "does not exist" in report.diagnosis


def test_check_stuck_project_with_doing_items(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # Create project with item in "doing" state
    _mkproj(tmp_path, "stuck-proj", "- [~] task stuck in doing\n- [ ] next task\n")
    report = check_project("stuck-proj")
    assert report.status in ("stuck", "warning")
    assert any("doing" in e.lower() for e in report.evidence)


def test_check_blocked_items_noted(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "blocked-proj", "- [!] blocked task\n- [ ] next task\n")
    report = check_project("blocked-proj")
    # blocked alone isn't "stuck" but should be noted
    assert any("blocked" in e.lower() for e in report.evidence)


def test_check_completed_project(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "done-proj", "- [x] done 1\n- [x] done 2\n")
    report = check_project("done-proj")
    assert report.status == "healthy"
    assert "complete" in report.diagnosis.lower() or "healthy" in report.status


def test_check_repeated_decisions_flagged(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    proj_dir = _mkproj(tmp_path, "repeat-proj", "- [~] stuck task\n")
    # Write a decisions file with repeated lines
    decisions = "## Decisions\n" + ("- same action repeated\n" * 5)
    (proj_dir / "DECISIONS.md").write_text(decisions, encoding="utf-8")
    report = check_project("repeat-proj")
    assert report.status in ("stuck", "warning")


def test_check_report_has_evidence(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "evidence-proj", "- [~] in progress\n- [ ] todo\n")
    report = check_project("evidence-proj")
    assert isinstance(report.evidence, list)


# ---------------------------------------------------------------------------
# check_all_projects
# ---------------------------------------------------------------------------

def test_check_all_returns_list(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "proj-a")
    _mkproj(tmp_path, "proj-b")
    reports = check_all_projects()
    assert isinstance(reports, list)
    assert len(reports) == 2
    slugs = {r.project for r in reports}
    assert "proj-a" in slugs
    assert "proj-b" in slugs


def test_check_all_empty_workspace(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    reports = check_all_projects()
    assert reports == []


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------

def test_check_system_health_returns_result(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    assert isinstance(health, SystemHealth)
    assert health.status in ("healthy", "degraded", "critical")


def test_health_has_required_checks(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    assert "workspace_writable" in health.checks
    assert "disk_space" in health.checks
    assert "api_key" in health.checks


def test_health_status_values(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    for check_val in health.checks.values():
        assert check_val.startswith(("ok", "warn", "fail"))


def test_health_format_text(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    text = health.format("text")
    assert "health=" in text
    assert "workspace_writable" in text


def test_health_format_json(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    data = json.loads(health.format("json"))
    assert "status" in data
    assert "checks" in data
    assert "checked_at" in data


# ---------------------------------------------------------------------------
# Heartbeat state persistence
# ---------------------------------------------------------------------------

def test_write_and_read_heartbeat_state(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    write_heartbeat_state(health)
    state = read_heartbeat_state()
    assert state is not None
    assert "system_status" in state
    assert "checked_at" in state


def test_heartbeat_state_includes_stuck_projects(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    health = check_system_health()
    reports = [
        SheriffReport(project="stuck-one", status="stuck", diagnosis="test", evidence=[]),
        SheriffReport(project="healthy-one", status="healthy", diagnosis="ok", evidence=[]),
    ]
    write_heartbeat_state(health, project_reports=reports)
    state = read_heartbeat_state()
    assert "stuck-one" in state["stuck_projects"]
    assert "healthy-one" not in state["stuck_projects"]


# ---------------------------------------------------------------------------
# SheriffReport format
# ---------------------------------------------------------------------------

def test_report_format_text():
    r = SheriffReport(
        project="test",
        status="stuck",
        diagnosis="loop detected",
        evidence=["3 repeats", "no artifacts"],
        recommended_action="re-run tick",
    )
    text = r.format("text")
    assert "project=test" in text
    assert "stuck" in text
    assert "loop detected" in text
    assert "3 repeats" in text
    assert "re-run tick" in text


def test_report_format_json():
    r = SheriffReport(
        project="test",
        status="healthy",
        diagnosis="all good",
        evidence=[],
    )
    data = json.loads(r.format("json"))
    assert data["project"] == "test"
    assert data["status"] == "healthy"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_sheriff_check(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "cli-proj")
    import cli
    rc = cli.main(["sheriff", "check", "cli-proj"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "healthy" in out


def test_cli_sheriff_health(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    cli.main(["sheriff", "health"])
    out = capsys.readouterr().out
    assert "health=" in out


def test_cli_sheriff_all_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    _mkproj(tmp_path, "proj-one")
    _mkproj(tmp_path, "proj-two")
    import cli
    rc = cli.main(["sheriff", "all", "--format", "json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# Project lifecycle state (.poe-failed / .poe-paused marker files)
# ---------------------------------------------------------------------------

class TestProjectLifecycleState:
    """Tests for mark_project_failed, mark_project_paused, project_lifecycle_state.

    These marker files are the primary mechanism for stopping zombie projects
    from consuming resources (heartbeat diagnosis, backlog drain, sheriff checks).
    """

    def test_active_project_has_active_state(self, monkeypatch, tmp_path):
        """A project with no markers is 'active'."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "live-proj")
        assert project_lifecycle_state("live-proj") == "active"

    def test_failed_marker_returns_failed(self, monkeypatch, tmp_path):
        """After mark_project_failed(), project_lifecycle_state returns 'failed'."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "dead-proj")
        mark_project_failed("dead-proj", reason="zombie — no progress for 48h")
        assert project_lifecycle_state("dead-proj") == "failed"

    def test_paused_marker_returns_paused(self, monkeypatch, tmp_path):
        """After mark_project_paused(), project_lifecycle_state returns 'paused'."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "paused-proj")
        mark_project_paused("paused-proj", reason="waiting on external dependency")
        assert project_lifecycle_state("paused-proj") == "paused"

    def test_failed_takes_precedence_over_paused(self, monkeypatch, tmp_path):
        """If both markers exist (corrupt state), 'failed' takes precedence."""
        _setup(monkeypatch, tmp_path)
        proj_dir = _mkproj(tmp_path, "both-markers")
        # Write both markers manually
        (proj_dir / ".poe-failed").write_text("failed\n", encoding="utf-8")
        (proj_dir / ".poe-paused").write_text("paused\n", encoding="utf-8")
        assert project_lifecycle_state("both-markers") == "failed"

    def test_failed_marker_writes_reason(self, monkeypatch, tmp_path):
        """mark_project_failed writes the reason into the marker file."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "reason-proj")
        marker = mark_project_failed("reason-proj", reason="too many retries")
        content = marker.read_text(encoding="utf-8")
        assert "too many retries" in content

    def test_failed_marker_without_reason(self, monkeypatch, tmp_path):
        """mark_project_failed with no reason writes minimal marker content."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "no-reason-proj")
        marker = mark_project_failed("no-reason-proj")
        assert marker.exists()
        assert "failed" in marker.read_text(encoding="utf-8")

    def test_check_project_short_circuits_on_failed(self, monkeypatch, tmp_path):
        """check_project returns status='failed' immediately for failed projects."""
        _setup(monkeypatch, tmp_path)
        _mkproj(tmp_path, "marked-failed")
        mark_project_failed("marked-failed", reason="stalled")
        report = check_project("marked-failed")
        assert report.status == "failed"
        assert "failed" in report.diagnosis.lower()

    def test_unknown_project_returns_active(self, monkeypatch, tmp_path):
        """project_lifecycle_state returns 'active' for nonexistent projects (safe default)."""
        _setup(monkeypatch, tmp_path)
        # Do NOT create the project — lifecycle state should gracefully return active
        result = project_lifecycle_state("nonexistent-ghost-project")
        assert result == "active"
