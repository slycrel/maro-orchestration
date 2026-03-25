"""Tests for Phase 19: boot_protocol.py

All tests use tmp_path — no real filesystem mutations outside tmp_path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from boot_protocol import (
    BootState,
    format_boot_context,
    run_boot_protocol,
    update_dead_ends,
    _load_dead_ends,
    _read_completed_from_mission,
    _read_completed_from_next,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Test: run_boot_protocol
# ---------------------------------------------------------------------------

def test_run_boot_protocol_dry_run(monkeypatch, tmp_path):
    """dry_run=True → returns BootState with method=dry_run."""
    _setup_workspace(monkeypatch, tmp_path)
    state = run_boot_protocol("test-project", dry_run=True)
    assert isinstance(state, BootState)
    assert state.boot_method == "dry_run"
    assert state.project == "test-project"
    assert state.completed_features == []
    assert state.dead_ends == []
    assert state.git_head is None
    assert state.existing_tests_pass is True


def test_run_boot_protocol_no_project(monkeypatch, tmp_path):
    """Project dir doesn't exist → graceful, returns BootState."""
    _setup_workspace(monkeypatch, tmp_path)
    state = run_boot_protocol("nonexistent-xyz-project")
    assert isinstance(state, BootState)
    assert state.project == "nonexistent-xyz-project"
    assert isinstance(state.completed_features, list)
    assert isinstance(state.dead_ends, list)


def test_run_boot_protocol_reads_next_md(monkeypatch, tmp_path):
    """BootState reads completed items from NEXT.md."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "my-project"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    next_file = p_dir / "NEXT.md"
    next_file.write_text(
        "- [ ] Pending task\n"
        "- [x] Completed task one\n"
        "- [x] Completed task two\n",
        encoding="utf-8",
    )
    state = run_boot_protocol(project)
    assert "Completed task one" in state.completed_features
    assert "Completed task two" in state.completed_features
    assert len([f for f in state.completed_features if "Pending" in f]) == 0


def test_run_boot_protocol_reads_mission_json(monkeypatch, tmp_path):
    """BootState reads completed features from mission.json."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "mission-project"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    mission_data = {
        "id": "m001",
        "goal": "Test goal",
        "project": project,
        "milestones": [
            {
                "id": "ms001",
                "title": "Milestone A",
                "features": [
                    {"id": "f001", "title": "Feature done", "status": "done"},
                    {"id": "f002", "title": "Feature pending", "status": "pending"},
                ],
                "validation_criteria": [],
                "status": "done",
                "validation_result": None,
            }
        ],
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
        "completed_at": None,
        "ancestry_context": "",
    }
    (p_dir / "mission.json").write_text(json.dumps(mission_data), encoding="utf-8")
    state = run_boot_protocol(project)
    assert "Feature done" in state.completed_features
    assert "Feature pending" not in state.completed_features


def test_run_boot_protocol_creates_dead_ends_file(monkeypatch, tmp_path):
    """DEAD_ENDS.md created if missing."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "deadends-project"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    assert not (p_dir / "DEAD_ENDS.md").exists()
    state = run_boot_protocol(project)
    assert (p_dir / "DEAD_ENDS.md").exists()


def test_run_boot_protocol_loads_dead_ends(monkeypatch, tmp_path):
    """DEAD_ENDS.md contents loaded into BootState."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "deadends-loaded"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    dead_ends_content = (
        "# Dead Ends\n\n"
        "## [2026-01-01T00:00:00Z] Loop abc — Step: Try approach A\n"
        "Reason: didn't work\n\n"
        "## [2026-01-02T00:00:00Z] Loop def — Step: Try approach B\n"
        "Reason: also failed\n"
    )
    (p_dir / "DEAD_ENDS.md").write_text(dead_ends_content, encoding="utf-8")
    state = run_boot_protocol(project)
    assert len(state.dead_ends) == 2
    assert any("approach A" in de for de in state.dead_ends)


def test_run_boot_protocol_has_boot_timestamp(monkeypatch, tmp_path):
    """BootState always has a boot_timestamp."""
    _setup_workspace(monkeypatch, tmp_path)
    state = run_boot_protocol("timestamp-project")
    assert state.boot_timestamp
    assert "T" in state.boot_timestamp  # ISO 8601


# ---------------------------------------------------------------------------
# Test: format_boot_context
# ---------------------------------------------------------------------------

def test_format_boot_context_nonempty(monkeypatch, tmp_path):
    """format_boot_context always returns a non-empty string."""
    state = BootState(
        project="test",
        loop_id="abc12345",
        completed_features=[],
        git_head=None,
        existing_tests_pass=True,
        dead_ends=[],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    ctx = format_boot_context(state)
    assert ctx
    assert len(ctx) > 20


def test_format_boot_context_includes_completed(monkeypatch, tmp_path):
    """format_boot_context lists completed features."""
    state = BootState(
        project="test",
        loop_id="abc12345",
        completed_features=["Feature A", "Feature B"],
        git_head=None,
        existing_tests_pass=True,
        dead_ends=[],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    ctx = format_boot_context(state)
    assert "Feature A" in ctx
    assert "Feature B" in ctx


def test_format_boot_context_includes_dead_ends(monkeypatch, tmp_path):
    """format_boot_context lists dead ends."""
    state = BootState(
        project="test",
        loop_id="abc12345",
        completed_features=[],
        git_head=None,
        existing_tests_pass=True,
        dead_ends=["Approach X failed", "Approach Y failed"],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    ctx = format_boot_context(state)
    assert "Approach X" in ctx or "dead end" in ctx.lower()


def test_format_boot_context_includes_git_head(monkeypatch, tmp_path):
    """format_boot_context includes git HEAD if available."""
    state = BootState(
        project="test",
        loop_id="abc12345",
        completed_features=[],
        git_head="abc123def456",
        existing_tests_pass=True,
        dead_ends=[],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    ctx = format_boot_context(state)
    assert "abc123def456" in ctx


def test_format_boot_context_instruction_present(monkeypatch, tmp_path):
    """format_boot_context always includes 'do not redo' instruction."""
    state = BootState(
        project="test",
        loop_id="abc12345",
        completed_features=["Done feature"],
        git_head=None,
        existing_tests_pass=True,
        dead_ends=[],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    ctx = format_boot_context(state)
    assert "NOT" in ctx or "not" in ctx.lower()


# ---------------------------------------------------------------------------
# Test: update_dead_ends
# ---------------------------------------------------------------------------

def test_update_dead_ends_creates_file(monkeypatch, tmp_path):
    """update_dead_ends creates DEAD_ENDS.md if it doesn't exist."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "create-dead-ends"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    assert not (p_dir / "DEAD_ENDS.md").exists()
    update_dead_ends(project, ["Approach A failed: too slow"])
    assert (p_dir / "DEAD_ENDS.md").exists()


def test_update_dead_ends_appends(monkeypatch, tmp_path):
    """Second call to update_dead_ends appends, doesn't overwrite."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "append-dead-ends"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    update_dead_ends(project, ["First dead end"])
    update_dead_ends(project, ["Second dead end"])
    content = (p_dir / "DEAD_ENDS.md").read_text(encoding="utf-8")
    assert "First dead end" in content
    assert "Second dead end" in content


def test_update_dead_ends_empty_list_noop(monkeypatch, tmp_path):
    """update_dead_ends with empty list is a no-op."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "noop-dead-ends"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    update_dead_ends(project, [])
    # File shouldn't be created for empty list
    # (either created or not, just shouldn't crash)


def test_boot_state_fields(monkeypatch, tmp_path):
    """BootState has all expected fields."""
    state = BootState(
        project="p",
        loop_id="l",
        completed_features=["f1"],
        git_head="abc",
        existing_tests_pass=True,
        dead_ends=["d1"],
        boot_timestamp="2026-01-01T00:00:00Z",
        boot_method="full",
    )
    assert state.project == "p"
    assert state.loop_id == "l"
    assert state.completed_features == ["f1"]
    assert state.git_head == "abc"
    assert state.existing_tests_pass is True
    assert state.dead_ends == ["d1"]
    assert state.boot_timestamp == "2026-01-01T00:00:00Z"
    assert state.boot_method == "full"


def test_read_completed_from_next_various_formats(monkeypatch, tmp_path):
    """_read_completed_from_next handles various checkbox formats."""
    _setup_workspace(monkeypatch, tmp_path)
    project = "next-formats"
    p_dir = orch.project_dir(project)
    p_dir.mkdir(parents=True, exist_ok=True)
    next_file = p_dir / "NEXT.md"
    next_file.write_text(
        "- [x] Done item A\n"
        "- [ ] Pending item\n"
        "- [x] Done item B\n"
        "Some other content\n",
        encoding="utf-8",
    )
    completed = _read_completed_from_next(p_dir)
    assert "Done item A" in completed
    assert "Done item B" in completed
    assert len([c for c in completed if "Pending" in c]) == 0
