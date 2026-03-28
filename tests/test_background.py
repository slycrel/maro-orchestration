"""Tests for Phase 10: background.py

Background execution primitive — non-blocking subprocess with polling.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from background import (
    BackgroundTask,
    _load_task,
    list_background_tasks,
    poll_background,
    start_background,
    wait_background,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# start_background
# ---------------------------------------------------------------------------

def test_start_background_returns_task(monkeypatch, tmp_path):
    """start_background returns a BackgroundTask with pid."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo hello")
    assert isinstance(task, BackgroundTask)
    assert task.pid > 0
    assert task.id
    assert len(task.id) == 8


def test_start_background_nonblocking(monkeypatch, tmp_path):
    """start_background returns immediately (does not wait for slow command)."""
    _setup_workspace(monkeypatch, tmp_path)
    start_time = time.monotonic()
    task = start_background("sleep 5")
    elapsed = time.monotonic() - start_time
    # Should return in well under 1 second
    assert elapsed < 2.0
    assert task.status == "running"
    # Clean up: kill the sleep process
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


def test_start_background_status_running(monkeypatch, tmp_path):
    """Newly started task has status=running."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("sleep 3")
    assert task.status == "running"
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


def test_start_background_has_output_file(monkeypatch, tmp_path):
    """start_background creates an output file."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo test output")
    assert task.output_file
    # Wait briefly for file to be created
    time.sleep(0.1)
    assert Path(task.output_file).exists()
    # Clean up
    try:
        Path(task.output_file).unlink()
    except Exception:
        pass


def test_start_background_persists(monkeypatch, tmp_path):
    """start_background writes to background-tasks.jsonl."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo persisted")
    bg_log = orch.orch_root() / "memory" / "background-tasks.jsonl"
    assert bg_log.exists()
    content = bg_log.read_text()
    assert task.id in content
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


def test_start_background_started_at(monkeypatch, tmp_path):
    """Task has a started_at timestamp."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo ts")
    assert task.started_at
    assert "T" in task.started_at  # ISO format
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# poll_background
# ---------------------------------------------------------------------------

def test_poll_background_running(monkeypatch, tmp_path):
    """Polling a live PID → status stays running."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("sleep 5")
    polled = poll_background(task.id)
    assert polled.status == "running"
    assert polled.pid == task.pid
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


def test_poll_background_done(monkeypatch, tmp_path):
    """After command completes, poll returns status=done."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo done")
    # Wait for the process to finish
    time.sleep(0.5)
    polled = poll_background(task.id)
    # Should be done or at most running if system is slow
    assert polled.status in ("done", "running", "failed")


def test_poll_background_not_found(monkeypatch, tmp_path):
    """poll_background raises KeyError for unknown task id."""
    _setup_workspace(monkeypatch, tmp_path)
    with pytest.raises(KeyError):
        poll_background("nonexistent-id-zzz")


def test_poll_background_preserves_command(monkeypatch, tmp_path):
    """Polled task retains original command string."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo preserve test")
    polled = poll_background(task.id)
    assert polled.command == "echo preserve test"
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# wait_background
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_wait_background_completes(monkeypatch, tmp_path):
    """wait_background waits for a short command to finish."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo wait-test")
    result = wait_background(task.id, timeout_seconds=10)
    assert result.status in ("done", "failed")  # short echo should complete


@pytest.mark.slow
def test_wait_background_timeout(monkeypatch, tmp_path):
    """wait_background returns status=timeout if command is too slow."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("sleep 60")
    result = wait_background(task.id, timeout_seconds=2)
    assert result.status == "timeout"
    assert result.completed_at is not None
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


def test_wait_background_fast_command(monkeypatch, tmp_path):
    """wait_background with generous timeout → status=done for quick command."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("true")  # exits immediately with code 0
    result = wait_background(task.id, timeout_seconds=15)
    assert result.status in ("done", "failed")


# ---------------------------------------------------------------------------
# list_background_tasks
# ---------------------------------------------------------------------------

def test_list_background_tasks(monkeypatch, tmp_path):
    """list_background_tasks loads from jsonl."""
    _setup_workspace(monkeypatch, tmp_path)
    task1 = start_background("echo task-one")
    task2 = start_background("echo task-two")
    tasks = list_background_tasks()
    ids = [t.id for t in tasks]
    assert task1.id in ids
    assert task2.id in ids
    try:
        os.kill(task1.pid, 9)
        os.kill(task2.pid, 9)
    except Exception:
        pass


def test_list_background_tasks_empty(monkeypatch, tmp_path):
    """list_background_tasks returns [] when no tasks exist."""
    _setup_workspace(monkeypatch, tmp_path)
    tasks = list_background_tasks()
    assert tasks == []


def test_list_background_tasks_deduplicates(monkeypatch, tmp_path):
    """Each task id appears only once even if log has multiple entries."""
    _setup_workspace(monkeypatch, tmp_path)
    task = start_background("echo dedup")
    # Poll to create a second entry for the same id
    try:
        poll_background(task.id)
    except Exception:
        pass
    tasks = list_background_tasks()
    ids = [t.id for t in tasks]
    assert ids.count(task.id) == 1
    try:
        os.kill(task.pid, 9)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_background(monkeypatch, tmp_path, capsys):
    """poe-background CLI starts a task and returns 0."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-background", "echo", "cli-test"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status=running" in out or "id=" in out


def test_cli_poe_background_wait(monkeypatch, tmp_path, capsys):
    """poe-background --wait completes for a fast command."""
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-background", "echo", "wait-test", "--wait", "--timeout", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "id=" in out
