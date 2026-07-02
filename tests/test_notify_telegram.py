"""Tests for the Telegram notify target (notify_telegram)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import notify_telegram
from notify_telegram import format_message, main


def test_format_run_completed_success():
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "write fib.py",
        "result_excerpt": "Created fib.py with the first 10 numbers.",
        "handle_id": "abc123",
    })
    assert "✅" in msg
    assert "success" in msg
    assert "write fib.py" in msg
    assert "Created fib.py" in msg
    assert "maro-runs result abc123" in msg


def test_format_done_not_achieved_warns():
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "done-not-achieved",
        "goal": "g",
        "handle_id": "h",
    })
    assert "⚠" in msg and "done-not-achieved" in msg


def test_format_escalation():
    msg = format_message({
        "event_type": "escalation",
        "goal": "wire $50k",
        "summary": "This needs human signoff.",
        "reason": "irreversible financial action",
        "point": "dispatch",
        "job_id": "task-1",
    })
    assert "needs a human" in msg
    assert "wire $50k" in msg
    assert "human signoff" in msg
    assert "dispatch" in msg


def test_format_escalation_without_summary_uses_reason():
    msg = format_message({
        "event_type": "escalation",
        "goal": "g",
        "reason": "navigator escalated",
    })
    assert "navigator escalated" in msg


def test_format_truncates_long_goal():
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "x" * 500,
        "handle_id": "h",
    })
    goal_line = [l for l in msg.splitlines() if l.startswith("Goal:")][0]
    assert len(goal_line) < 220 and goal_line.endswith("…")


def test_main_dry_run_prints_message(monkeypatch, capsys):
    payload = {"event_type": "run_completed", "success_class": "success",
               "goal": "g", "handle_id": "h1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    rc = main(["--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "maro run success" in out


def test_main_empty_payload_fails(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--dry-run"]) == 1


def test_main_garbage_payload_degrades(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    rc = main(["--dry-run"])
    assert rc == 0
    assert "not json at all" in capsys.readouterr().out


def test_send_without_token_returns_false(monkeypatch):
    monkeypatch.setattr("telegram_listener._resolve_token", lambda: "")
    assert notify_telegram.send("hello") is False
