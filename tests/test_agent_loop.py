"""Tests for Phase 1: agent_loop.py (autonomous loop runner).

All tests use dry_run=True — no real API calls.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import orch
from agent_loop import (
    LoopResult,
    StepOutcome,
    _DryRunAdapter,
    _decompose,
    _execute_step,
    _goal_to_slug,
    run_agent_loop,
    run_parallel_loops,
)
from llm import LLMMessage, LLMTool, LLMResponse, ToolCall


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workspace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# _goal_to_slug
# ---------------------------------------------------------------------------

def test_goal_to_slug_basic():
    assert _goal_to_slug("research winning polymarket strategies") == "research-winning-polymarket-strategies"


def test_goal_to_slug_special_chars():
    slug = _goal_to_slug("Build a REST API! (v2)")
    assert "/" not in slug
    assert " " not in slug
    assert len(slug) > 0


def test_goal_to_slug_empty():
    assert _goal_to_slug("") == "unnamed-goal"


def test_goal_to_slug_max_words():
    long_goal = "one two three four five six seven eight nine ten"
    slug = _goal_to_slug(long_goal)
    assert slug.count("-") <= 4  # at most 5 words = 4 dashes


# ---------------------------------------------------------------------------
# _DryRunAdapter
# ---------------------------------------------------------------------------

def test_dry_run_adapter_decompose():
    adapter = _DryRunAdapter()
    resp = adapter.complete([
        LLMMessage("system", "Decompose goals"),
        LLMMessage("user", "Goal: test goal\n\nDecompose into 3 or fewer concrete steps."),
    ])
    # Should return a JSON array
    steps = json.loads(resp.content)
    assert isinstance(steps, list)
    assert len(steps) >= 1
    assert all(isinstance(s, str) for s in steps)


def test_dry_run_adapter_execute():
    adapter = _DryRunAdapter()
    tools = [
        LLMTool(
            name="complete_step",
            description="Mark done",
            parameters={"type": "object", "properties": {"result": {"type": "string"}, "summary": {"type": "string"}}, "required": ["result", "summary"]},
        ),
        LLMTool(
            name="flag_stuck",
            description="Flag stuck",
            parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        ),
    ]
    resp = adapter.complete(
        [
            LLMMessage("system", "You are an agent."),
            LLMMessage("user", "Overall goal: test\n\nCurrent step (1/3): do the thing"),
        ],
        tools=tools,
        tool_choice="required",
    )
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "complete_step"
    assert "result" in resp.tool_calls[0].arguments


# ---------------------------------------------------------------------------
# _decompose
# ---------------------------------------------------------------------------

def test_decompose_returns_list():
    adapter = _DryRunAdapter()
    steps = _decompose("build a research report on X", adapter, max_steps=4)
    assert isinstance(steps, list)
    assert 1 <= len(steps) <= 4
    assert all(isinstance(s, str) and s for s in steps)


def test_decompose_respects_max_steps():
    adapter = _DryRunAdapter()
    steps = _decompose("build a research report on X", adapter, max_steps=2)
    assert len(steps) <= 2


def test_decompose_falls_back_on_bad_json(monkeypatch):
    """If the LLM returns garbage, falls back to heuristic."""
    class BadAdapter:
        def complete(self, messages, **kwargs):
            return LLMResponse(content="not json at all", stop_reason="end_turn")

    steps = _decompose("do A then B then C", BadAdapter(), max_steps=4)
    assert isinstance(steps, list)
    assert len(steps) >= 1


# ---------------------------------------------------------------------------
# _execute_step
# ---------------------------------------------------------------------------

def test_execute_step_done():
    adapter = _DryRunAdapter()
    tools = [
        LLMTool(
            name="complete_step",
            description="Mark done",
            parameters={"type": "object", "properties": {"result": {"type": "string"}, "summary": {"type": "string"}}, "required": ["result", "summary"]},
        ),
        LLMTool(
            name="flag_stuck",
            description="Flag stuck",
            parameters={"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
        ),
    ]
    outcome = _execute_step(
        goal="write a report",
        step_text="research the topic",
        step_num=1,
        total_steps=3,
        completed_context=[],
        adapter=adapter,
        tools=tools,
    )
    assert outcome["status"] == "done"
    assert "result" in outcome


def test_execute_step_stuck_on_api_failure():
    class FailAdapter:
        def complete(self, messages, **kwargs):
            raise RuntimeError("API timeout")

    outcome = _execute_step(
        goal="test",
        step_text="do something",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=FailAdapter(),
        tools=[],
    )
    assert outcome["status"] == "blocked"
    assert "LLM call failed" in outcome["stuck_reason"]


# ---------------------------------------------------------------------------
# run_agent_loop
# ---------------------------------------------------------------------------

def test_loop_dry_run_completes(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "research polymarket strategies",
        project="test-loop",
        dry_run=True,
    )
    assert isinstance(result, LoopResult)
    assert result.status == "done"
    assert len(result.steps) >= 1
    assert all(isinstance(s, StepOutcome) for s in result.steps)


def test_loop_creates_project(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "write a haiku about autonomy",
        project="haiku-project",
        dry_run=True,
    )
    assert orch.project_dir("haiku-project").exists()


def test_loop_auto_slugs_project(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "analyze competitor pricing strategies",
        dry_run=True,
    )
    assert result.project != ""
    assert "/" not in result.project
    assert orch.project_dir(result.project).exists()


def test_loop_writes_log_artifact(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "test artifact writing",
        project="artifact-test",
        dry_run=True,
    )
    assert result.log_path is not None
    log_file = orch.orch_root() / result.log_path
    assert log_file.exists()
    data = json.loads(log_file.read_text())
    assert data["loop_id"] == result.loop_id
    assert data["status"] == result.status
    assert "steps" in data
    assert "totals" in data


def test_loop_steps_marked_in_project(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "complete all steps cleanly",
        project="steps-marked-test",
        dry_run=True,
    )
    assert result.status == "done"
    # All done steps should be marked done in NEXT.md
    _, items = orch.parse_next("steps-marked-test")
    done_items = [i for i in items if i.state == orch.STATE_DONE]
    assert len(done_items) == sum(1 for s in result.steps if s.status == "done")


def test_loop_stuck_detection(monkeypatch, tmp_path):
    """If the LLM always flags stuck, loop terminates with status=stuck."""
    _setup_workspace(monkeypatch, tmp_path)

    class AlwaysStuckAdapter:
        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            user_content = next((m.content for m in reversed(messages) if m.role == "user"), "")
            # decompose: return steps
            if "concrete steps" in user_content.lower() or "decompose" in user_content.lower():
                return LLMResponse(
                    content='["step one", "step two", "step three"]',
                    stop_reason="end_turn",
                )
            # execute: always flag stuck
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    name="flag_stuck",
                    arguments={"reason": "cannot proceed", "attempted": "tried everything"},
                )],
                stop_reason="tool_use",
            )

    result = run_agent_loop(
        "something impossible",
        project="stuck-test",
        adapter=AlwaysStuckAdapter(),
        max_steps=3,
    )
    assert result.status == "stuck"
    assert result.stuck_reason is not None


def test_loop_result_token_counts(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "count the tokens",
        project="token-count-test",
        dry_run=True,
    )
    assert result.total_tokens_in > 0
    assert result.total_tokens_out > 0


def test_loop_respects_max_steps(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "do many things A then B then C then D then E then F then G",
        project="max-steps-test",
        dry_run=True,
        max_steps=3,
    )
    assert len(result.steps) <= 3


def test_loop_summary_format(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "test summary output",
        project="summary-test",
        dry_run=True,
    )
    s = result.summary()
    assert "loop_id=" in s
    assert "project=" in s
    assert "status=" in s


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_run_dry_run(monkeypatch, tmp_path, capsys):
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-run", "test goal from cli", "--project", "cli-test", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status=done" in out


def test_cli_poe_run_json_format(monkeypatch, tmp_path, capsys):
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["poe-run", "json format test", "--project", "cli-json-test", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "loop_id" in data
    assert "status" in data
    assert data["status"] == "done"


# ---------------------------------------------------------------------------
# Phase 8: run_parallel_loops
# ---------------------------------------------------------------------------

def test_run_parallel_loops_two_goals(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    goals = ["test goal alpha", "test goal beta"]
    results = run_parallel_loops(goals, dry_run=True, max_workers=2)
    assert len(results) == 2
    assert all(isinstance(r, LoopResult) for r in results)
    assert all(r.status == "done" for r in results)


def test_run_parallel_loops_empty():
    results = run_parallel_loops([], dry_run=True)
    assert results == []


def test_run_parallel_loops_single_goal(monkeypatch, tmp_path):
    _setup_workspace(monkeypatch, tmp_path)
    results = run_parallel_loops(["solo goal"], dry_run=True, max_workers=3)
    assert len(results) == 1
    assert results[0].status == "done"


# ---------------------------------------------------------------------------
# Interrupt handling in agent loop
# ---------------------------------------------------------------------------

def test_interrupt_stop_halts_loop(monkeypatch, tmp_path):
    """A stop interrupt posted to the queue causes the loop to end with status=interrupted."""
    _setup_workspace(monkeypatch, tmp_path)
    from interrupt import InterruptQueue

    q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
    # Pre-load stop interrupt — will be picked up after the first step completes
    q.post("stop", source="test", intent="stop")

    result = run_agent_loop(
        "do several things",
        project="interrupt-stop-test",
        dry_run=True,
        interrupt_queue=q,
    )
    assert result.status == "interrupted"
    assert result.interrupts_applied >= 1


def test_interrupt_additive_adds_steps(monkeypatch, tmp_path):
    """An additive interrupt is processed and loop completes normally."""
    _setup_workspace(monkeypatch, tmp_path)
    from interrupt import InterruptQueue

    q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
    # Post additive — should not halt the loop
    q.post("also verify the output", source="test", intent="additive")

    result = run_agent_loop(
        "research a topic",
        project="interrupt-additive-test",
        dry_run=True,
        interrupt_queue=q,
    )
    # Loop completes (dry-run always produces done steps)
    assert result.status == "done"
    assert result.interrupts_applied >= 1


def test_interrupt_no_interrupt_queue_completes_normally(monkeypatch, tmp_path):
    """When queue_path points to a non-existent/empty file, loop runs normally."""
    _setup_workspace(monkeypatch, tmp_path)
    from interrupt import InterruptQueue

    # Queue backed by a file that doesn't exist — poll() returns []
    q = InterruptQueue(queue_path=tmp_path / "empty_interrupts.jsonl")

    result = run_agent_loop(
        "complete all tasks without interruption",
        project="interrupt-empty-test",
        dry_run=True,
        interrupt_queue=q,
    )
    assert result.status == "done"
    assert result.interrupts_applied == 0
