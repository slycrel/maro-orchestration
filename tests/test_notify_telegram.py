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
    assert "goal achieved" in msg
    assert "write fib.py" in msg
    assert "Created fib.py" in msg
    assert "maro-runs result abc123" in msg


def test_format_done_not_achieved_warns():
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "done-not-achieved",
        "goal": "g",
        "handle_id": "h",
        "goal_verdict_gaps": ["thread content never fetched", "no shortlist"],
    })
    assert "⚠" in msg and "NOT achieved" in msg
    # The taxonomy name must not leak into a user-facing message.
    assert "done-not-achieved" not in msg
    assert "Missing: thread content never fetched; no shortlist" in msg


def test_format_clarification_relays_question():
    """The day-one failure: a clarification_needed run must put the question
    itself in the user's hands, with how to answer it."""
    msg = format_message({
        "event_type": "run_completed",
        "status": "clarification_needed",
        "goal": "review the thread",
        "handle_id": "h9",
        "clarification_question": "Which thread should I reference?",
    })
    assert "❓" in msg
    assert "Which thread should I reference?" in msg
    assert "Re-send the goal" in msg


def test_format_verdict_and_stats():
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "g",
        "handle_id": "h1",
        "nickname": "dapper-heron",
        "goal_verdict_summary": "All core goal elements are present.",
        "total_cost_usd": 0.706856,
        "started_at": "2026-07-17T03:08:50+00:00",
        "ended_at": "2026-07-17T03:45:55+00:00",
    })
    assert "Verdict: All core goal elements are present." in msg
    assert "cost $0.71" in msg
    assert "37m" in msg
    assert "dapper-heron" in msg


def test_excerpt_skips_goal_echo_and_status_line(tmp_path):
    """RESULT.md opens with '# Result: <goal echo>' + a telemetry line —
    the message must lead with the findings body instead."""
    result = tmp_path / "loop-x-RESULT.md"
    result.write_text(
        "# Result: review the thread and do many things\n\n"
        "Status: done | 7/7 steps done | tokens: 2906907\n\n---\n\n"
        "Real finding: unrollnow.com recovered the full 7-tweet thread.\n"
    )
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "review the thread",
        "handle_id": "h2",
        "result_path": str(result),
    })
    assert "Real finding: unrollnow.com" in msg
    assert "tokens: 2906907" not in msg


def test_viewer_link_derived_from_result_path(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    rd = runs / "h3-nick" / "build"
    rd.mkdir(parents=True)
    result = rd / "loop-abc-RESULT.md"
    result.write_text("# Result: g\n\nbody\n")
    monkeypatch.setattr(
        notify_telegram, "_cfg",
        lambda key, default: "http://192.168.0.45:8787" if key == "notify.viewer_url" else default,
    )
    import runs as runs_mod
    monkeypatch.setattr(runs_mod, "runs_root", lambda: runs)
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "g",
        "handle_id": "h3",
        "result_path": str(result),
    })
    assert "http://192.168.0.45:8787/h3-nick/build/loop-abc-report.html" in msg


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
    assert "Done — goal achieved" in out


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


# --- answer-first completion (delivery-loop arc 2026-07-17) ------------------


def test_answer_first_message_leads_with_the_answer():
    """The user asked a question; the body is the answer, not the verifier's
    self-grade ("all core goal elements are demonstrably present" answers
    'did the machinery work?' — the wrong question)."""
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal_achieved": True,
        "goal": "use the maro orchestration to examine this X thread and "
                "recommend what to install with our new hermes setup",
        "handle_id": "h7",
        "goal_verdict_summary": "All core goal elements are demonstrably present.",
        "answer_summary": "1. Turn on cron\n2. Maker-checker delegation\n"
                          "3. MCP (pending one doc check)",
    })
    assert "1. Turn on cron" in msg
    assert "demonstrably present" not in msg
    assert "Re: use the maro orchestration" in msg
    assert msg.index("Turn on cron") < msg.index("maro-runs result")


def test_answer_first_keeps_verdict_when_not_achieved():
    """On a failure the why-not DOES matter — verdict stays alongside the
    partial answer."""
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "done-not-achieved",
        "goal_achieved": False,
        "goal": "g",
        "handle_id": "h8",
        "goal_verdict_summary": "Shortlist exists but constraints unverified.",
        "answer_summary": "Partial shortlist: cron, delegation.",
    })
    assert "Partial shortlist" in msg
    assert "constraints unverified" in msg


def test_truncated_answer_is_flagged_not_silent():
    """A 900-char-capped shortlist showed 3 of 5 items with no hint the rest
    existed (dapper-heron live) — a capped list must say it's capped."""
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "g",
        "handle_id": "h10",
        "answer_summary": "1. cron\n2. maker-checker\n3. MCP",
        "answer_truncated": True,
    })
    assert "⋯ more in the full report" in msg
    # And absent when the answer is complete.
    msg2 = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "g",
        "handle_id": "h10",
        "answer_summary": "1. cron\n2. maker-checker\n3. MCP",
    })
    assert "more in the full report" not in msg2


def test_re_line_trims_on_word_boundary():
    """'Re: … Extract concrete Herme…' (live) — mid-word chops read broken."""
    goal = ("Review this X thread: https://example.com/status/123 then "
            "extract concrete Hermes recommendations for the new setup please")
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": goal,
        "handle_id": "h11",
        "answer_summary": "the answer",
    })
    re_line = [l for l in msg.splitlines() if l.startswith("Re: ")][0]
    assert re_line.endswith("…")
    # The visible fragment before the ellipsis is a whole word from the goal.
    last_word = re_line[len("Re: "):-1].split()[-1]
    assert f" {last_word} " in goal + " "


def test_deliverable_link_preferred_over_report(monkeypatch):
    monkeypatch.setattr(
        notify_telegram, "_cfg",
        lambda key, default: "http://192.168.0.45:8787"
        if key == "notify.viewer_url" else default,
    )
    msg = format_message({
        "event_type": "run_completed",
        "success_class": "success",
        "goal": "g",
        "handle_id": "h9",
        "answer_summary": "the answer",
        "deliverable_link_path": "h9-nick/artifact/FINAL_REPORT.md",
        "result_path": "/anything/loop-x-RESULT.md",
    })
    assert "📄 Full report: http://192.168.0.45:8787/h9-nick/artifact/FINAL_REPORT.md" in msg
