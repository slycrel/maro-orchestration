"""Tests for the substrate notification hook (notify.emit) and uniform result
retrieval (run_curation.run_result).

The notify hook is how an external substrate (OpenClaw, Hermes) learns a run
finished or a human is needed. Off by default; config notify.command turns it
on. run_result normalizes NOW/AGENDA result shapes into one contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs
from runs import create_run_dir, finalize_run, set_current_run_dir
import notify as notify_mod
from notify import emit
from run_curation import run_result, curate_run


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    runs._CALL_COUNTERS.clear()
    yield tmp_path
    set_current_run_dir(None)


def _configure_notify(monkeypatch, command, events=None, timeout=30):
    values = {"notify.command": command, "notify.timeout_seconds": timeout}
    if events is not None:
        values["notify.events"] = events
    monkeypatch.setattr(
        notify_mod, "_config_get",
        lambda key, default: values.get(key, default),
    )


# --- notify.emit ------------------------------------------------------------

def test_emit_noop_without_command(workspace, monkeypatch):
    _configure_notify(monkeypatch, "")
    assert emit("run_completed", {"handle_id": "x", "status": "done"}) is False


def test_emit_runs_command_with_payload_on_stdin(workspace, monkeypatch, tmp_path):
    out = tmp_path / "captured.json"
    _configure_notify(monkeypatch, f"cat > {out}")
    ok = emit("run_completed", {"handle_id": "abc123", "status": "done",
                                "goal": "test goal"})
    assert ok is True
    payload = json.loads(out.read_text())
    assert payload["event_type"] == "run_completed"
    assert payload["handle_id"] == "abc123"
    assert payload["goal"] == "test goal"


def test_emit_sets_env_vars(workspace, monkeypatch, tmp_path):
    out = tmp_path / "env.txt"
    _configure_notify(monkeypatch,
                      f'echo "$MARO_EVENT_TYPE $MARO_HANDLE_ID $MARO_STATUS $MARO_RUN_DIR" > {out}')
    emit("escalation", {"handle_id": "h1", "status": "stuck"}, run_dir="/some/run")
    assert out.read_text().strip() == "escalation h1 stuck /some/run"


def test_emit_filters_by_event_list(workspace, monkeypatch, tmp_path):
    out = tmp_path / "never.txt"
    _configure_notify(monkeypatch, f"touch {out}", events=["escalation"])
    assert emit("run_completed", {"handle_id": "x"}) is False
    assert not out.exists()


def test_emit_failing_command_returns_false(workspace, monkeypatch):
    _configure_notify(monkeypatch, "exit 3")
    assert emit("run_completed", {"handle_id": "x"}) is False


def test_emit_timeout_returns_false(workspace, monkeypatch):
    _configure_notify(monkeypatch, "sleep 5", timeout=0.2)
    assert emit("run_completed", {"handle_id": "x"}) is False


def test_emit_never_raises_on_garbage(workspace, monkeypatch):
    _configure_notify(monkeypatch, "cat > /dev/null")
    # non-serializable values fall back to str via default=str
    assert emit("run_completed", {"handle_id": object(), "status": None}) in (True, False)


def test_emit_writes_event_stream_even_without_command(workspace, monkeypatch):
    _configure_notify(monkeypatch, "")
    emit("run_completed", {"handle_id": "h2", "status": "done", "goal": "g"})
    from observe import _events_path
    ev = _events_path()
    assert ev.is_file()
    lines = [json.loads(l) for l in ev.read_text().splitlines() if l.strip()]
    assert any(e.get("event_type") == "run_completed" for e in lines)


# --- durable escalation file (2026-07-12 decree) ----------------------------

def _read_escalations():
    p = notify_mod.escalations_path()
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


@pytest.mark.parametrize("event_type", [
    "escalation", "backend_actionable", "stranded_run",
    "resume_refused_busy", "resume_lock_unavailable",
])
def test_emit_writes_escalation_file_for_escalation_class_events(workspace, monkeypatch, event_type):
    _configure_notify(monkeypatch, "")  # no notify lane configured at all
    emit(event_type, {"handle_id": "h3", "status": "stuck", "summary": "x"})
    rows = _read_escalations()
    assert len(rows) == 1
    assert rows[0]["event_type"] == event_type
    assert rows[0]["handle_id"] == "h3"
    assert "ts" in rows[0]


def test_emit_excludes_run_completed_from_escalation_file(workspace, monkeypatch):
    _configure_notify(monkeypatch, "")
    emit("run_completed", {"handle_id": "h4", "status": "done"})
    assert _read_escalations() == []


def test_escalation_file_persists_when_notify_command_fails(workspace, monkeypatch):
    # "even when a notify lane delivers" — the file write is independent of
    # whether notify.command is configured, succeeds, or fails.
    _configure_notify(monkeypatch, "exit 3")
    ok = emit("escalation", {"handle_id": "h5", "status": "stuck"})
    assert ok is False  # the hook command itself failed
    rows = _read_escalations()
    assert len(rows) == 1
    assert rows[0]["handle_id"] == "h5"


def test_escalation_file_lives_under_output_dir(workspace, monkeypatch):
    _configure_notify(monkeypatch, "")
    emit("stranded_run", {"handle_id": "h6"})
    p = notify_mod.escalations_path()
    assert p.parent.name == "output"
    assert p.parent.parent == workspace
    assert p.name == "escalations.jsonl"


# --- recursion check-in event (docs/RECURSIVE_CHECKIN_DESIGN.md) -------------

def test_recursion_checkin_is_a_default_event():
    # Default-on so an away-from-keyboard user gets the redirect/stop chance.
    assert "recursion_checkin" in notify_mod.DEFAULT_EVENTS


def test_recursion_checkin_writes_to_escalation_file(workspace, monkeypatch):
    # It rides the durable escalation surface (human-might-miss class) even
    # with no notify.command configured.
    _configure_notify(monkeypatch, "")
    emit("recursion_checkin", {"handle_id": "h7", "status": "running",
                               "blocking": False, "goal_pass": 3,
                               "reasoning": "still narrowing scope"})
    rows = _read_escalations()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "recursion_checkin"
    # blocking=False is what lets a consumer tell this apart from a
    # park-the-goal escalation at a glance (design §2).
    assert rows[0]["blocking"] is False
    assert rows[0]["goal_pass"] == 3


def test_recursion_checkin_command_receives_payload(workspace, monkeypatch, tmp_path):
    out = tmp_path / "checkin.json"
    _configure_notify(monkeypatch, f"cat > {out}")
    ok = emit("recursion_checkin", {"handle_id": "h8", "status": "running",
                                    "blocking": False,
                                    "summary_for_user": "3 passes deep, on track"})
    assert ok is True
    payload = json.loads(out.read_text())
    assert payload["event_type"] == "recursion_checkin"
    assert payload["blocking"] is False
    assert payload["summary_for_user"] == "3 passes deep, on track"


# --- run_result -------------------------------------------------------------

def test_run_result_now_lane(workspace):
    rd = create_run_dir("hidnow01", prompt="what is 2+2?", lane="now")
    (rd / "artifact").mkdir(exist_ok=True)
    (rd / "artifact" / "now-hidnow01.json").write_text(json.dumps(
        {"handle_id": "hidnow01", "lane": "now", "result": "4"}))
    finalize_run("hidnow01", status="done")
    res = run_result("hidnow01")
    assert res["result"] == "4"
    assert res["lane"] == "now"
    assert res["status"] == "done"


def test_run_result_agenda_prefers_result_over_partial(workspace):
    rd = create_run_dir("hidag01", prompt="build it", lane="agenda")
    (rd / "build" / "loop-aaa-PARTIAL.md").write_text("# Partial result")
    (rd / "build" / "loop-bbb-RESULT.md").write_text("# Result: built it")
    finalize_run("hidag01", status="done")
    res = run_result("hidag01")
    assert "built it" in res["result"]
    assert res["result_path"].endswith("RESULT.md")


def test_run_result_agenda_falls_back_to_partial(workspace):
    rd = create_run_dir("hidag02", prompt="try it", lane="agenda")
    (rd / "build" / "loop-ccc-PARTIAL.md").write_text("# Partial result: half done")
    finalize_run("hidag02", status="stuck")
    res = run_result("hidag02")
    assert "half done" in res["result"]


def test_run_result_missing_run(workspace):
    assert run_result("nope1234") is None


def test_run_result_no_artifacts_returns_none(workspace):
    create_run_dir("hidbare1", prompt="g", lane="agenda")
    finalize_run("hidbare1", status="error")
    assert run_result("hidbare1") is None


def test_run_card_carries_result_excerpt(workspace):
    rd = create_run_dir("hidexc01", prompt="answer me", lane="now")
    (rd / "artifact").mkdir(exist_ok=True)
    (rd / "artifact" / "now-hidexc01.json").write_text(json.dumps(
        {"handle_id": "hidexc01", "result": "the answer is 42"}))
    finalize_run("hidexc01", status="done")
    card = curate_run("hidexc01")
    assert card["result_excerpt"] == "the answer is 42"
    assert card["result_path"].endswith("now-hidexc01.json")


def test_run_card_excerpt_truncates_long_results(workspace):
    rd = create_run_dir("hidexc02", prompt="long", lane="now")
    (rd / "artifact").mkdir(exist_ok=True)
    (rd / "artifact" / "now-hidexc02.json").write_text(json.dumps(
        {"handle_id": "hidexc02", "result": "x" * 2000}))
    finalize_run("hidexc02", status="done")
    card = curate_run("hidexc02")
    assert len(card["result_excerpt"]) == 501  # 500 + ellipsis
    assert card["result_excerpt"].endswith("…")
