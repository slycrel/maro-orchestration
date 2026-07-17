"""Tests for deploy/hermes/dispatch.py — the Hermes→Maro cross-box driver.

Loaded via importlib (deploy/ is not a package). The worker/status verbs are
exercised with faked task_store / handle_queue / runs modules so no real
workspace is touched.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_dispatch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "hermes_dispatch", REPO / "deploy" / "hermes" / "dispatch.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DISPATCH_DIR", tmp_path / "hermes-dispatch")
    return mod


def _fake_task_modules(monkeypatch, handle_result):
    """Install fake task_store/handle_queue; returns a call log for asserts."""
    calls = []
    fake_ts = types.ModuleType("task_store")
    fake_ts.claim = lambda job_id: {"job_id": job_id, "reason": "goal", "source": "user_goal"}
    fake_ts.complete = lambda job_id, **kw: calls.append(("complete", job_id, kw))
    fake_ts.fail = lambda job_id, msg: calls.append(("fail", job_id, msg))
    fake_hq = types.ModuleType("handle_queue")
    fake_hq.handle_task = lambda task: handle_result
    monkeypatch.setitem(sys.modules, "task_store", fake_ts)
    monkeypatch.setitem(sys.modules, "handle_queue", fake_hq)
    return calls


def test_worker_records_result_excerpt(tmp_path, monkeypatch, capsys):
    """The HandleResult's result text (clarification question, guard refusal,
    error detail) must land in the dispatch record — it is the only carrier
    of the "why" for runs that never reach the loop."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    res = types.SimpleNamespace(
        status="clarification_needed",
        handle_id="h1",
        lane="agenda",
        result="Before starting, I need to clarify one thing:\n\nWhich thread?",
    )
    calls = _fake_task_modules(monkeypatch, res)

    assert mod.cmd_worker("job-1") == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-1.json").read_text())
    assert rec["status"] == "clarification_needed"
    assert "Which thread?" in rec["result_excerpt"]
    # Queue "done" = drained, annotated with what the drain concluded.
    assert calls == [("complete", "job-1", {"result_status": "clarification_needed"})]


def test_worker_error_result_routes_to_fail(tmp_path, monkeypatch, capsys):
    """A handle-level error (guard refusal, backend death) is a drain that
    produced no work — the queue record must say failed, not done."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    res = types.SimpleNamespace(
        status="error",
        handle_id="",
        lane="agenda",
        result="recall guard: 3 attempts at this goal in the last 60m, all failed",
    )
    calls = _fake_task_modules(monkeypatch, res)

    assert mod.cmd_worker("job-err") == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-err.json").read_text())
    assert rec["status"] == "error"
    assert "recall guard" in rec["result_excerpt"]
    assert len(calls) == 1
    verb, job_id, detail = calls[0]
    assert verb == "fail"
    assert job_id == "job-err"
    assert "recall guard" in detail


def test_enqueue_marks_goal_truncation(tmp_path, monkeypatch, capsys):
    """The 500-char display copy of the goal must show that it was cut —
    a mid-word truncation read as a mangled goal (2026-07-16)."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    fake_hq = types.ModuleType("handle_queue")
    fake_hq.enqueue_goal = lambda goal: "job-long"
    monkeypatch.setitem(sys.modules, "handle_queue", fake_hq)
    spawned = []
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: spawned.append(a))

    long_goal = "x" * 600
    assert mod.cmd_enqueue(long_goal) == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-long.json").read_text())
    assert rec["goal"] == "x" * 500 + "…"
    assert spawned, "worker should still be spawned"

    short_goal = "y" * 40
    fake_hq.enqueue_goal = lambda goal: "job-short"
    assert mod.cmd_enqueue(short_goal) == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-short.json").read_text())
    assert rec["goal"] == short_goal


def test_worker_omits_result_excerpt_when_empty(tmp_path, monkeypatch, capsys):
    mod = _load_dispatch(tmp_path, monkeypatch)
    res = types.SimpleNamespace(status="done", handle_id="h2", lane="agenda", result="")
    _fake_task_modules(monkeypatch, res)

    assert mod.cmd_worker("job-2") == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-2.json").read_text())
    assert rec["status"] == "done"
    assert "result_excerpt" not in rec


def test_status_surfaces_clarification_question_from_card(tmp_path, monkeypatch, capsys):
    mod = _load_dispatch(tmp_path, monkeypatch)
    (tmp_path / "hermes-dispatch").mkdir(parents=True)
    (tmp_path / "hermes-dispatch" / "job-3.json").write_text(json.dumps({
        "job_id": "job-3",
        "status": "clarification_needed",
        "handle_id": "h3",
    }))
    run_dir = tmp_path / "runs" / "h3-test-nick"
    run_dir.mkdir(parents=True)
    (run_dir / "run_card.json").write_text(json.dumps({
        "nickname": "test-nick",
        "clarification_question": "Which thread should I reference?",
        "goal_verdict_gaps": ["thread content never fetched"],
    }))
    fake_runs = types.ModuleType("runs")
    fake_runs.runs_root = lambda: tmp_path / "runs"
    monkeypatch.setitem(sys.modules, "runs", fake_runs)

    assert mod.cmd_status("job-3") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["clarification_question"] == "Which thread should I reference?"
    assert out["goal_verdict_gaps"] == ["thread content never fetched"]
