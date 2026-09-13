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
    import config
    section = {"command": command, "timeout_seconds": timeout}
    if events is not None:
        section["events"] = events
    monkeypatch.setattr(config, "snapshot", lambda **kw: ({"notify": section}, []))


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
    # VERDICT_PROSE_CAP (2000) since the 2026-08-13 STORE widening: a
    # 2000-char answer rides whole; past the cap the cut announces itself
    # (the old 500 + bare ellipsis silently pre-bound decision_prior's
    # what_was_tried/why fallbacks).
    assert card["result_excerpt"] == "x" * 2000
    rd3 = create_run_dir("hidexc03", prompt="longer", lane="now")
    (rd3 / "artifact").mkdir(exist_ok=True)
    (rd3 / "artifact" / "now-hidexc03.json").write_text(json.dumps(
        {"handle_id": "hidexc03", "result": "y" * 2500}))
    finalize_run("hidexc03", status="done")
    card3 = curate_run("hidexc03")
    assert card3["result_excerpt"].startswith("y" * 2000)
    assert "truncated: first 2000 of 2500" in card3["result_excerpt"]


@pytest.mark.parametrize("section", ["[command, some-hook]", "17"])
def test_r18_malformed_notify_section_is_unknown(workspace, monkeypatch, section):
    import config
    import observe
    user = config._user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("notify: {command: some-hook}\n")
    ws = config._workspace_config_path()
    ws.write_text(f"notify: {section}\n")
    called = []
    monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
    monkeypatch.setattr(notify_mod.subprocess, "run", lambda *a, **kw: called.append(a))
    config.load_config(reload=True)
    assert config.load_faults() == []
    assert notify_mod.hook_owed("run_completed") is None
    assert notify_mod.tell("run_completed", {"handle_id": "x"}) is False
    assert called == []
    ws.write_text("notify: {}\n")
    config.load_config(reload=True)
    assert notify_mod.hook_owed("run_completed") is True


def test_r18_faulted_override_never_runs_inherited_hook(workspace, monkeypatch):
    import config
    import observe
    from types import SimpleNamespace
    user = config._user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("notify: {command: user-hook}\n")
    ws = config._workspace_config_path()
    ws.write_text("notify: {command: ws-hook, events: [run_completed]}\n")
    real_read = Path.read_text
    unreadable = True

    def read(path, *args, **kwargs):
        if path == ws and unreadable:
            raise OSError("workspace unreadable")
        return real_read(path, *args, **kwargs)

    called = []
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
    monkeypatch.setattr(
        notify_mod.subprocess, "run",
        lambda command, **kw: called.append(command) or SimpleNamespace(returncode=0))
    config.load_config(reload=True)
    assert notify_mod.hook_owed("run_completed") is None
    assert notify_mod.tell("run_completed", {"handle_id": "x"}) is False
    assert called == []
    unreadable = False
    assert notify_mod.tell("run_completed", {"handle_id": "x"}) is True
    assert called == ["ws-hook"]


def test_r18_summary_only_answer_reaches_journal(workspace, monkeypatch):
    import observe
    rows = []
    monkeypatch.setattr(
        observe, "write_event", lambda kind, **kw: rows.append((kind, kw)) or True)
    assert notify_mod.tell("run_completed", {
        "handle_id": "x", "status": "done", "goal_achieved": None,
        "verdict_pending": True, "answer_summary": "Revenue rose 12%.",
    }) is True
    assert rows[0][1]["detail"].endswith("; Revenue rose 12%.")


def test_r19_policy_keeps_faults_with_its_snapshot(workspace, monkeypatch):
    import config
    user = config._user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("notify: {command: some-hook}\n")
    ws = config._workspace_config_path()
    ws.write_text("{}\n")
    monkeypatch.setattr(config, "_config_cache", None)
    real_read = Path.read_text
    real_get = config.get
    broken = True

    def read(path, *args, **kwargs):
        # review r19: both failed reads make the old section absent.
        if broken and path in (user, ws):
            raise OSError("transient config read failure")
        return real_read(path, *args, **kwargs)

    def repair():
        nonlocal broken
        broken = False
        config.load_config(reload=True)

    def get(key, default=None):
        value = real_get(key, default)
        if key == "notify":
            repair()  # a clean publish between the old section/fault reads
        return value

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(config, "get", get)
    assert notify_mod.hook_owed("run_completed") is None
    repair()  # the snapshot reader does not call the old get seam
    assert notify_mod.hook_owed("run_completed") is True


@pytest.mark.parametrize("command", ["[]", "{}", "0", "17", "true"])
def test_r19_malformed_command_stays_owed(workspace, monkeypatch, command):
    import config
    import observe
    user = config._user_config_path()
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("notify: {command: user-hook}\n")
    ws = config._workspace_config_path()
    ws.write_text(f"notify: {{command: {command}}}\n")
    calls = []
    monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)
    monkeypatch.setattr(notify_mod.subprocess, "run", lambda *a, **kw: calls.append(a))
    config.load_config(reload=True)
    assert config.load_faults() == []
    assert notify_mod.hook_owed("run_completed") is None
    assert notify_mod.tell("run_completed", {}) is False
    assert calls == []
    for disabled in ('false', '""', 'null', '"   "'):
        ws.write_text(f"notify: {{command: {disabled}}}\n")
        config.load_config(reload=True)
        assert notify_mod.hook_owed("run_completed") is False
        assert notify_mod.tell("run_completed", {}) is True
    assert calls == []


@pytest.mark.parametrize("events", ["run_completed_extra", "{run_completed: false}",
                                    "[run_completed, 17]"])
def test_r19_emit_validates_subscriptions(workspace, monkeypatch, events):
    import config
    import observe
    from types import SimpleNamespace
    ws = config._workspace_config_path()
    calls = []
    monkeypatch.setattr(observe, "write_event", lambda *a, **kw: True)

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(notify_mod.subprocess, "run", run)
    ws.write_text(f"notify: {{command: some-hook, events: {events}}}\n")
    config.load_config(reload=True)
    assert notify_mod.hook_owed("run_completed") is None
    assert notify_mod.tell("run_completed", {}) is False
    assert calls == []
    ws.write_text("notify: {command: some-hook, events: [run_completed]}\n")
    config.load_config(reload=True)
    assert notify_mod.tell("run_completed", {}) is True
    assert calls == ["some-hook"]
    calls.clear()
    ws.write_text("notify: {command: some-hook, events: [run_verdict]}\n")
    config.load_config(reload=True)
    assert notify_mod.hook_owed("run_completed") is False
    assert notify_mod.tell("run_completed", {}) is True
    assert calls == []


def test_r19_invalid_timeout_uses_default(workspace, monkeypatch, caplog):
    import config
    from types import SimpleNamespace
    config._workspace_config_path().write_text(
        "notify: {command: some-hook, timeout_seconds: invalid}\n")
    config.load_config(reload=True)
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(notify_mod.subprocess, "run", run)
    assert notify_mod.tell("run_completed", {}) is True
    assert calls == [30]
    assert "timeout" in caplog.text
