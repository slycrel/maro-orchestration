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


def test_record_sequence_increments(workspace):
    rd = create_run_dir("hid00002", prompt="g")
    set_current_run_dir(rd)
    a = record_llm_call("p1", "r1")
    b = record_llm_call("p2", "r2")
    assert a.name == "call-00001.json"
    assert b.name == "call-00002.json"
    assert len(list(_calls_dir(rd).glob("call-*.json"))) == 2


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
