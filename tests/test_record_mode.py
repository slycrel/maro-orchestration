"""Tests for record-mode capture (runs.record_llm_call / recording_enabled).

Record-mode is the keystone for visibility ladder rungs 4-6 (step I/O, agent
actions, LLM call). Default ON; off via MARO_RECORD=0 or config record.enabled.
Capture writes <run-dir>/build/calls/call-NNNNN.json, secret-scrubbed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs
from runs import (
    create_run_dir,
    set_current_run_dir,
    record_llm_call,
    recording_enabled,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    # Counters are process-global; clear so seq starts fresh per test.
    runs._CALL_COUNTERS.clear()
    yield tmp_path
    set_current_run_dir(None)


def _calls_dir(rd: Path) -> Path:
    return rd / "build" / "calls"


def test_recording_enabled_default_on(workspace, monkeypatch):
    monkeypatch.delenv("MARO_RECORD", raising=False)
    assert recording_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
def test_recording_disabled_by_env(workspace, monkeypatch, val):
    monkeypatch.setenv("MARO_RECORD", val)
    assert recording_enabled() is False


def test_recording_env_overrides_truthy(workspace, monkeypatch):
    monkeypatch.setenv("MARO_RECORD", "1")
    assert recording_enabled() is True


def test_record_writes_call_file(workspace):
    rd = create_run_dir("hid00001", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("the prompt", "the response", backend="anthropic",
                          model="claude", tokens_in=10, tokens_out=20)
    assert out is not None and out.is_file()
    rec = json.loads(out.read_text())
    assert rec["prompt"] == "the prompt"
    assert rec["response"] == "the response"
    assert rec["backend"] == "anthropic"
    assert rec["seq"] == 1
    assert rec["tokens_in"] == 10 and rec["tokens_out"] == 20


def test_record_writes_purpose_field(workspace):
    """BACKLOG #17 sub-item 2: caller-stamped purpose persists on the record."""
    rd = create_run_dir("hid00002", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r", purpose="routing")
    rec = json.loads(out.read_text())
    assert rec["purpose"] == "routing"


def test_record_writes_max_tokens_requested(workspace):
    """Requested cap persists on the record so an overrun is diagnosable from
    the call file alone (not every backend enforces max_tokens)."""
    rd = create_run_dir("hid00005", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r", tokens_out=2489, max_tokens_requested=256)
    rec = json.loads(out.read_text())
    assert rec["max_tokens_requested"] == 256


def test_record_purpose_defaults_to_empty_string(workspace):
    rd = create_run_dir("hid00003", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r")
    rec = json.loads(out.read_text())
    assert rec["purpose"] == ""


def test_record_sequence_increments(workspace):
    rd = create_run_dir("hid00002", prompt="g")
    set_current_run_dir(rd)
    a = record_llm_call("p1", "r1")
    b = record_llm_call("p2", "r2")
    assert a.name == "call-00001.json"
    assert b.name == "call-00002.json"
    assert len(list(_calls_dir(rd).glob("call-*.json"))) == 2


def test_record_seq_collision_does_not_overwrite(workspace):
    """C0.4 must-detect: the seq counter is process-local, so two processes
    sharing a run dir can allocate the same seq. Publication must be
    exclusive-create — the loser lands on the next free number and the
    winner's record survives byte-for-byte."""
    rd = create_run_dir("hid00008", prompt="g")
    set_current_run_dir(rd)
    calls = _calls_dir(rd)
    calls.mkdir(parents=True, exist_ok=True)
    # Another process published seq 1 AFTER this process's counter was
    # primed at 0 — the in-memory counter can't see it.
    winner = calls / "call-00001.json"
    winner.write_text('{"seq": 1, "prompt": "the other process"}')
    runs._CALL_COUNTERS[str(rd)] = 0

    out = record_llm_call("p", "r")

    assert out is not None
    assert out.name == "call-00002.json"
    # The existing record was NOT overwritten.
    assert json.loads(winner.read_text())["prompt"] == "the other process"
    # The new record carries its actual published seq.
    assert json.loads(out.read_text())["seq"] == 2


def test_record_noop_when_disabled(workspace, monkeypatch):
    rd = create_run_dir("hid00003", prompt="g")
    set_current_run_dir(rd)
    monkeypatch.setenv("MARO_RECORD", "0")
    assert record_llm_call("p", "r") is None
    assert not _calls_dir(rd).exists() or not list(_calls_dir(rd).glob("call-*.json"))


def test_record_noop_without_run_dir(workspace):
    set_current_run_dir(None)
    assert record_llm_call("p", "r") is None


def test_record_scrubs_secrets(workspace):
    rd = create_run_dir("hid00004", prompt="g")
    set_current_run_dir(rd)
    leak = "here is a key sk-ant-abcdefghij0123456789 do not store"
    out = record_llm_call(leak, "response with token=supersecretvalue123")
    rec = json.loads(out.read_text())
    assert "sk-ant-abcdefghij0123456789" not in rec["prompt"]
    assert "[REDACTED]" in rec["prompt"]
    assert "supersecretvalue123" not in rec["response"]


def test_record_explicit_run_dir_overrides_current(workspace):
    rd1 = create_run_dir("hid00005", prompt="g1")
    rd2 = create_run_dir("hid00006", prompt="g2")
    set_current_run_dir(rd1)
    out = record_llm_call("p", "r", run_dir=rd2)
    assert out.parent.parent.parent == rd2


def test_record_tool_events_persisted(workspace):
    rd = create_run_dir("hid00007", prompt="g")
    set_current_run_dir(rd)
    events = [{"tool": "Bash", "input": "ls"}, {"tool": "Read", "input": "x.py"}]
    out = record_llm_call("p", "r", tool_events=events)
    rec = json.loads(out.read_text())
    assert rec["tool_events"] == events


# ---------------------------------------------------------------------------
# Rung-4 unification (BACKLOG #0): loop-log links to the byte-level record
# ---------------------------------------------------------------------------

def test_failover_adapter_stamps_call_record(workspace):
    """When record-mode captures a call, the response carries the record path."""
    from llm import FailoverAdapter, LLMResponse

    rd = create_run_dir("hid00042", prompt="stamped goal")
    set_current_run_dir(rd)

    class _Fake:
        backend = "fake"
        model_key = "test"

        def complete(self, messages, **kwargs):
            return LLMResponse(content="hello", input_tokens=1, output_tokens=1)

    fa = FailoverAdapter([_Fake()])
    resp = fa.complete([{"role": "user", "content": "hi"}])
    rec = getattr(resp, "call_record", "")
    assert rec, "response should carry the call-record path"
    assert Path(rec).is_file()
    assert Path(rec).parent == _calls_dir(rd)


def test_failover_adapter_no_stamp_when_recording_off(workspace, monkeypatch):
    from llm import FailoverAdapter, LLMResponse
    monkeypatch.setenv("MARO_RECORD", "0")

    rd = create_run_dir("hid00043", prompt="unstamped goal")
    set_current_run_dir(rd)

    class _Fake:
        backend = "fake"
        model_key = "test"

        def complete(self, messages, **kwargs):
            return LLMResponse(content="hello", input_tokens=1, output_tokens=1)

    fa = FailoverAdapter([_Fake()])
    resp = fa.complete([{"role": "user", "content": "hi"}])
    assert getattr(resp, "call_record", "") == ""


def test_execute_step_outcome_carries_call_record(workspace, monkeypatch):
    """execute_step propagates resp.call_record onto the outcome dict."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(workspace))
    from llm import LLMResponse, ToolCall
    from step_exec import execute_step, EXECUTE_TOOLS

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            resp = LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "did the thing", "summary": "done"})],
                input_tokens=1, output_tokens=1,
            )
            resp.call_record = "/some/run/build/calls/call-00007.json"
            return resp

    outcome = execute_step(
        goal="g", step_text="do the thing", step_num=1, total_steps=1,
        completed_context=[], adapter=_Adapter(), tools=EXECUTE_TOOLS,
    )
    assert outcome["call_record"] == "/some/run/build/calls/call-00007.json"


def test_loop_log_includes_call_record(workspace, monkeypatch):
    """_write_loop_log emits the per-step call_record cross-reference."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(workspace))
    from loop_types import step_from_decompose
    from loop_artifacts import _write_loop_log
    import orch_items

    proj = "record-link-proj"
    (orch_items.project_dir(proj) / "artifacts").mkdir(parents=True, exist_ok=True)
    steps = [step_from_decompose(
        "step one", 0, status="done", result="full result text",
        call_record="/rd/build/calls/call-00001.json",
    )]
    _write_loop_log(proj, "loop123", "the goal", "done", steps,
                    "2026-07-04T00:00:00Z", 100, None)
    log_path = orch_items.project_dir(proj) / "artifacts" / "loop-loop123-log.json"
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["steps"][0]["call_record"] == "/rd/build/calls/call-00001.json"


# ---------------------------------------------------------------------------
# UU-1 (BACKLOG LT arc): failed/killed attempts leave a record too.
# The cold chlorination run's 10-minute killed step had ZERO bytes in
# build/calls/ — record-mode rode the success path only.
# ---------------------------------------------------------------------------

def test_failed_call_leaves_error_record_with_partial_output(workspace):
    """A timeout-killed attempt writes a stub record: error + partial output."""
    import subprocess as _sp
    from llm import FailoverAdapter

    rd = create_run_dir("hid00044", prompt="killed goal")
    set_current_run_dir(rd)

    class _Killed:
        backend = "subprocess"
        model_key = "test"

        def complete(self, messages, **kwargs):
            exc = _sp.TimeoutExpired(cmd="claude -p", timeout=601,
                                     output="partial output before kill")
            exc.maro_kill_reason = "wall-clock timeout after 601s"
            raise exc

    fa = FailoverAdapter([_Killed()])
    import pytest as _pytest
    with _pytest.raises(Exception):
        fa.complete([{"role": "user", "content": "hi"}], purpose="step-execute")

    calls = sorted(_calls_dir(rd).glob("call-*.json"))
    assert calls, "killed attempt must leave a record (UU-1)"
    rec = json.loads(calls[-1].read_text())
    assert "TimeoutExpired" in rec["error"]
    assert "wall-clock timeout after 601s" in rec["error"]
    assert rec["response"] == "partial output before kill"
    assert rec["purpose"] == "step-execute"


def test_failed_call_record_never_blocks_failover(workspace):
    """The error-record write is best-effort: failover still succeeds."""
    from llm import FailoverAdapter, LLMResponse

    rd = create_run_dir("hid00045", prompt="failover goal")
    set_current_run_dir(rd)

    class _Dies:
        backend = "primary"
        model_key = "test"

        def complete(self, messages, **kwargs):
            # A failover-class error per llm_errors.classify_error (the
            # subprocess-death shape) — a bare ConnectionError is classified
            # request-bad and propagates instead of failing over.
            raise RuntimeError("subprocess failed: claude -p crashed")

    class _Works:
        backend = "secondary"
        model_key = "test"

        def complete(self, messages, **kwargs):
            return LLMResponse(content="rescued", input_tokens=1, output_tokens=1)

    fa = FailoverAdapter([_Dies(), _Works()])
    resp = fa.complete([{"role": "user", "content": "hi"}], purpose="now")
    assert resp.content == "rescued"
    recs = [json.loads(p.read_text()) for p in sorted(_calls_dir(rd).glob("call-*.json"))]
    # Both the failed attempt AND the rescue are recorded, in order.
    assert len(recs) == 2
    assert "RuntimeError" in recs[0]["error"]
    assert recs[0]["backend"] == "primary"
    assert recs[1].get("error", "") == ""
    assert recs[1]["response"] == "rescued"


def test_success_records_carry_empty_error_field(workspace):
    """Success-path records keep a falsy error field (consumer contract)."""
    rd = create_run_dir("hid00046", prompt="ok goal")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r")
    rec = json.loads(out.read_text())
    assert rec["error"] == ""


def test_record_writes_cost_usd(workspace):
    """Async-tail visibility (2026-08-13): per-call provider cost persists
    on the record — previously derivable only for loop steps."""
    rd = create_run_dir("hid000c1", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r", cost_usd=0.0123)
    rec = json.loads(out.read_text())
    assert rec["cost_usd"] == pytest.approx(0.0123)


def test_record_cost_usd_defaults_to_zero(workspace):
    rd = create_run_dir("hid000c2", prompt="do a thing")
    set_current_run_dir(rd)
    out = record_llm_call("p", "r")
    rec = json.loads(out.read_text())
    assert rec["cost_usd"] == 0.0
