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


def test_format_backend_actionable_renders_the_instructions():
    # Review 2026-08-13: this event fell through to the run-completed
    # formatter and rendered as "ℹ run auth_expired" — the re-seed
    # instructions (the entire point of the notification) were dropped.
    msg = format_message({
        "event_type": "backend_actionable",
        "status": "auth_expired",
        "reason": "container executor auth expired: oauth session expired",
        "summary": ("Container executor OAuth session is dead — executor "
                    "steps now run on the HOST under the write-fence. "
                    "Re-seed the maro-claude-auth volume: run "
                    "`maro-bootstrap container-setup` step 2."),
    })
    assert "Re-seed the maro-claude-auth volume" in msg
    assert "backend actionable" in msg
    assert msg != "ℹ run auth_expired"


def test_format_backend_actionable_user_action_line():
    msg = format_message({
        "event_type": "backend_actionable",
        "status": "degraded",
        "summary": "openrouter auth_actionable (chain: ...)",
        "user_action": "Update OPENROUTER_API_KEY",
    })
    assert "Fix: Update OPENROUTER_API_KEY" in msg


def test_escalation_class_events_match_notify_registry():
    # _ESCALATION_CLASS_EVENTS is a local literal (standalone command);
    # this pin keeps it from drifting against notify's source of truth.
    from notify import ESCALATION_FILE_EVENTS
    from notify_telegram import _ESCALATION_CLASS_EVENTS
    assert _ESCALATION_CLASS_EVENTS == frozenset(ESCALATION_FILE_EVENTS) - {"escalation"}


def test_format_escalation_without_summary_uses_reason():
    msg = format_message({
        "event_type": "escalation",
        "goal": "g",
        "reason": "navigator escalated",
    })
    assert "navigator escalated" in msg


def test_format_escalation_decision_leads():
    # §9.6: the single-chasm ask IS the message body; summary demotes to
    # a Detail line, the family-ROI line rides after, "Why:" disappears.
    msg = format_message({
        "event_type": "escalation",
        "goal": "sync the feeds",
        "summary": "NAVIGATOR_ESCALATE: recovery overridden (conf 0.95)",
        "reason": "credentials expired",
        "decision": "Decide this chasm: a step is blocked — credentials "
                    "expired. Options: re-send the goal with guidance, or drop it.",
        "family_roi": "Family context: 'retry_churn' has 3 prior diagnoses "
                      "on record, 2 in the last 30 days.",
        "point": "blocked_step",
        "job_id": "task-9",
    })
    lines = msg.splitlines()
    assert lines[2].startswith("Decide this chasm:")
    assert "Detail: NAVIGATOR_ESCALATE" in msg
    assert "Family context: 'retry_churn'" in msg
    assert "Why:" not in msg


def test_format_escalation_decision_alone_no_detail_dupe():
    # When the summary is already inside the ask, no Detail echo.
    msg = format_message({
        "event_type": "escalation",
        "goal": "g",
        "summary": "budget exhausted",
        "decision": "Decide: budget exhausted",
    })
    assert "Decide: budget exhausted" in msg
    assert "Detail:" not in msg


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


def test_live_example_dapper_heron_message_shape(monkeypatch):
    """End-to-end pin on the REAL first-dispatch run card (2026-07-17,
    the X-thread research goal that drove the whole delivery-loop arc).
    Fixture = the actual curated card. If the completion-message pattern
    evolves, it must still deliver on this example: answer first, capped
    list flagged, no verifier self-grade on a success, deliverable link,
    honest stats. (Jeremy: capture the example so the pattern stays
    testable against real data.)"""
    card = json.loads(
        (Path(__file__).parent / "fixtures" / "dapper_heron"
         / "run_card.json").read_text())
    card["event_type"] = "run_completed"
    monkeypatch.setattr(
        notify_telegram, "_cfg",
        lambda key, default: "http://192.168.0.45:8787"
        if key == "notify.viewer_url" else default,
    )
    msg = format_message(card)
    lines = msg.splitlines()
    # Outcome header first, answer body before any run metadata.
    assert lines[0] == "✅ Done — goal achieved"
    assert "Enable cron scheduling" in msg
    assert msg.index("cron scheduling") < msg.index("maro-runs result")
    # The Re: goal line trims on a word boundary, never mid-word.
    re_line = [l for l in lines if l.startswith("Re: ")][0]
    assert re_line.endswith("…") and not re_line.endswith("Herme…")
    # The 900-char cap cut this answer — the message must say so.
    assert "⋯ more in the full report" in msg
    # Verifier self-grade stays out of a successful completion.
    assert "demonstrably present" not in msg
    # One-tap deliverable, not the loop report.
    assert ("📄 Full report: http://192.168.0.45:8787/"
            "2b25b6c7-dapper-heron/artifact/FINAL_REPORT.md") in msg
    assert "cost $0.71" in msg and "37m" in msg
    assert "Full result: maro-runs result 2b25b6c7" in msg


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
