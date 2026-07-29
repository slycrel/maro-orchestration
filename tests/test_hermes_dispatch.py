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


def test_enqueue_typed_envelope_shows_ask_and_meta(tmp_path, monkeypatch, capsys):
    """A maro-dispatch/v1 payload enqueues the RAW payload (handle_task
    re-parses it) while the dispatch record displays the user's ask and an
    envelope summary — the record is for humans, the queue is for the box."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    enqueued = []
    fake_hq = types.ModuleType("handle_queue")
    fake_hq.enqueue_goal = lambda goal: enqueued.append(goal) or "job-env"
    monkeypatch.setitem(sys.modules, "handle_queue", fake_hq)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: None)

    payload = json.dumps({
        "envelope": "maro-dispatch/v1",
        "user_ask": "summarize the gist",
        "operator_context": "pasted in Telegram",
        "attached_artifacts": [{"name": "ref.md", "content": "x"}],
    })
    assert mod.cmd_enqueue(payload) == 0
    assert enqueued == [payload], "queue must carry the raw payload"
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-env.json").read_text())
    assert rec["goal"] == "summarize the gist"
    assert rec["envelope"]["artifacts"] == ["ref.md"]
    assert rec["envelope"]["version"] == "maro-dispatch/v1"


def test_enqueue_malformed_envelope_refused_at_boundary(tmp_path, monkeypatch, capsys):
    """Declared envelope + broken shape = refused in seconds at enqueue with
    exit 2 and nothing queued — not discovered inside a detached worker."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    enqueued = []
    fake_hq = types.ModuleType("handle_queue")
    fake_hq.enqueue_goal = lambda goal: enqueued.append(goal) or "job-bad"
    monkeypatch.setitem(sys.modules, "handle_queue", fake_hq)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: None)

    bad = json.dumps({"envelope": "maro-dispatch/v1"})  # no user_ask
    assert mod.cmd_enqueue(bad) == 2
    assert not enqueued, "malformed envelope must not be enqueued"
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "error"
    assert "envelope" in out["error"]


def test_enqueue_envelope_keeps_verbatim_ask_past_truncation(tmp_path, monkeypatch, capsys):
    """`goal` is a 500-char display copy; the delivery loop needs the user's
    actual words, so an envelope dispatch also stores `user_ask` verbatim
    ("the direct ask and the prompt separately", 2026-07-28)."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    fake_hq = types.ModuleType("handle_queue")
    fake_hq.enqueue_goal = lambda goal: "job-long-ask"
    monkeypatch.setitem(sys.modules, "handle_queue", fake_hq)
    monkeypatch.setattr(mod.subprocess, "Popen", lambda *a, **kw: None)

    long_ask = "please compare " + "z" * 600
    payload = json.dumps({
        "envelope": "maro-dispatch/v1",
        "user_ask": long_ask,
        "operator_context": "from Telegram",
    })
    assert mod.cmd_enqueue(payload) == 0
    rec = json.loads((tmp_path / "hermes-dispatch" / "job-long-ask.json").read_text())
    assert rec["goal"].endswith("…") and len(rec["goal"]) == 501
    assert rec["user_ask"] == long_ask


def test_result_emits_delivery_block_for_envelope_only(tmp_path, monkeypatch, capsys):
    """cmd_result carries a `delivery` block (you_asked / dispatched_with)
    for envelope dispatches only — prose dispatches keep the pre-envelope
    contract where the whole payload is the ask."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    (tmp_path / "hermes-dispatch").mkdir(parents=True)
    (tmp_path / "hermes-dispatch" / "job-env-done.json").write_text(json.dumps({
        "job_id": "job-env-done",
        "status": "done",
        "handle_id": None,
        "goal": "summarize the gist",
        "user_ask": "summarize the gist",
        "envelope": {"version": "maro-dispatch/v1",
                     "operator_context_chars": 17,
                     "constraints": 0,
                     "artifacts": ["ref.md"]},
    }))
    (tmp_path / "hermes-dispatch" / "job-prose-done.json").write_text(json.dumps({
        "job_id": "job-prose-done",
        "status": "done",
        "handle_id": None,
        "goal": "plain prose goal",
    }))

    assert mod.cmd_result("job-env-done") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["delivery"]["you_asked"] == "summarize the gist"
    assert out["delivery"]["verbatim"] is True
    assert out["delivery"]["dispatched_with"]["artifacts"] == ["ref.md"]

    assert mod.cmd_result("job-prose-done") == 0
    out = json.loads(capsys.readouterr().out)
    assert "delivery" not in out


def test_result_legacy_envelope_rec_marks_fallback_nonverbatim(
        tmp_path, monkeypatch, capsys):
    """An envelope rec minted before user_ask was stored falls back to the
    display goal — and must say so (verbatim=False), so the renderer can
    tell a verbatim ask from a lossy 500-char copy."""
    mod = _load_dispatch(tmp_path, monkeypatch)
    (tmp_path / "hermes-dispatch").mkdir(parents=True)
    (tmp_path / "hermes-dispatch" / "job-legacy.json").write_text(json.dumps({
        "job_id": "job-legacy",
        "status": "done",
        "handle_id": None,
        "goal": "old truncated display goal…",
        "envelope": {"version": "maro-dispatch/v1",
                     "operator_context_chars": 0,
                     "constraints": 0,
                     "artifacts": []},
    }))
    assert mod.cmd_result("job-legacy") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["delivery"]["you_asked"] == "old truncated display goal…"
    assert out["delivery"]["verbatim"] is False
