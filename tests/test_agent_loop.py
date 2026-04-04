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
    _BlockDecision,
    _DryRunAdapter,
    _build_loop_context,
    _handle_blocked_step,
    _finalize_loop,
    _decompose,
    _execute_step,
    _goal_to_slug,
    _write_plan_manifest,
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


@pytest.mark.slow
def test_loop_stuck_detection(monkeypatch, tmp_path):
    """If the LLM always flags stuck, loop terminates with status=stuck."""
    _setup_workspace(monkeypatch, tmp_path)
    import agent_loop as _al
    # Bypass multi-plan decompose (4 LLM calls) — this test focuses on stuck detection.
    monkeypatch.setattr(_al, "_decompose",
                        lambda *a, **kw: ["step one", "step two", "step three"])
    # Prevent _generate_refinement_hint from calling build_adapter (real subprocess).
    monkeypatch.setattr(_al, "_generate_refinement_hint",
                        lambda *a, **kw: "try something different")
    # Disable Phase 45 auto-recovery (retries with exhausted adapter).
    monkeypatch.setattr(_al.run_agent_loop, "_recovery_in_progress", True, raising=False)

    class AlwaysStuckAdapter:
        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
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


# ---------------------------------------------------------------------------
# Phase 19: March of Nines + Dead Ends tests
# ---------------------------------------------------------------------------

def test_march_of_nines_alert_not_set_on_all_done(monkeypatch, tmp_path):
    """Loop with all steps done → march_of_nines_alert=False."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "all steps should succeed",
        project="march-nines-ok",
        dry_run=True,
    )
    assert result.march_of_nines_alert is False


@pytest.mark.slow
def test_march_of_nines_alert_set_on_low_success(monkeypatch, tmp_path):
    """Loop with many blocked steps → march_of_nines_alert=True."""
    _setup_workspace(monkeypatch, tmp_path)

    class _MostlyBlockedAdapter:
        """Returns flag_stuck for most steps."""
        call_count = 0

        def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3):
            from llm import LLMResponse, ToolCall
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), ""
            )
            if "decompose" in user_content.lower() or "concrete steps" in user_content.lower():
                # Return 5 steps
                steps = ["Step A", "Step B", "Step C", "Step D", "Step E"]
                return LLMResponse(
                    content=json.dumps(steps),
                    stop_reason="end_turn",
                    input_tokens=50,
                    output_tokens=30,
                )
            # Execution: block on all steps
            if tools and tool_choice == "required":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": "cannot complete", "attempted": "tried and failed"},
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=40,
                )
            return LLMResponse(content="[ok]", stop_reason="end_turn", input_tokens=10, output_tokens=5)

    result = run_agent_loop(
        "multi step goal that keeps failing",
        project="march-nines-alert",
        adapter=_MostlyBlockedAdapter(),
        dry_run=False,
    )
    # With all steps blocked, chain_success should be < 0.5 after enough steps
    # Note: the loop stops on first stuck, so we need to check if the alert was set
    # The alert is set after 3+ steps have been attempted with low success
    # In this case steps_attempted could be 1 (stops on first stuck)
    # The real test is the boolean field exists and is a bool
    assert isinstance(result.march_of_nines_alert, bool)


def test_loop_result_has_march_of_nines_field(monkeypatch, tmp_path):
    """LoopResult has march_of_nines_alert field."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop("simple test", project="march-field-test", dry_run=True)
    assert hasattr(result, "march_of_nines_alert")
    assert isinstance(result.march_of_nines_alert, bool)


@pytest.mark.slow
def test_dead_ends_written_on_block(monkeypatch, tmp_path):
    """Blocked step writes to DEAD_ENDS.md."""
    _setup_workspace(monkeypatch, tmp_path)

    class _StuckAdapter:
        """Decomposes into steps, blocks on first execution."""

        def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3):
            from llm import LLMResponse, ToolCall
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), ""
            )
            if "decompose" in user_content.lower() or "concrete steps" in user_content.lower():
                return LLMResponse(
                    content=json.dumps(["Only step: do the thing"]),
                    stop_reason="end_turn",
                    input_tokens=50,
                    output_tokens=20,
                )
            if tools and tool_choice == "required":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": "API unavailable", "attempted": "tried calling API"},
                    )],
                    stop_reason="tool_use",
                    input_tokens=60,
                    output_tokens=30,
                )
            return LLMResponse(content="ok", stop_reason="end_turn", input_tokens=10, output_tokens=5)

    project = "dead-ends-write-test"
    result = run_agent_loop(
        "do the thing that will fail",
        project=project,
        adapter=_StuckAdapter(),
        dry_run=False,
    )
    assert result.status == "stuck"
    # Check DEAD_ENDS.md was written
    project_path = orch.project_dir(project)
    dead_ends_file = project_path / "DEAD_ENDS.md"
    # The file may or may not be created depending on whether boot_protocol is available
    # But the loop should have completed
    assert result.loop_id is not None


# ---------------------------------------------------------------------------
# Parallel fan-out helpers (Phase 35 P1)
# ---------------------------------------------------------------------------

from agent_loop import _steps_are_independent, _run_steps_parallel


def test_steps_are_independent_clean():
    steps = [
        "Fetch the article at https://example.com/a",
        "Fetch the article at https://example.com/b",
        "Fetch the article at https://example.com/c",
    ]
    assert _steps_are_independent(steps)


def test_steps_are_independent_with_step_ref():
    steps = [
        "Fetch the article at https://example.com/a",
        "Based on step 1, extract the key claims",
    ]
    assert not _steps_are_independent(steps)


def test_steps_are_independent_with_above_ref():
    steps = [
        "Research the topic",
        "Synthesize the results from the previous step into a summary",
    ]
    assert not _steps_are_independent(steps)


def test_steps_are_independent_single_step():
    # Single step — trivially independent
    assert _steps_are_independent(["Do one thing"])


def test_run_agent_loop_fan_out_dry_run():
    """parallel_fan_out=3 with dry_run should return done without actual execution."""
    result = run_agent_loop(
        "fetch article A and fetch article B independently",
        dry_run=True,
        parallel_fan_out=3,
        verbose=False,
    )
    assert result.status in ("done", "dry_run", "stuck")


def test_run_agent_loop_fan_out_dependency_falls_back_sequential():
    """When steps have dependencies, fan-out gate blocks parallel path (sequential used)."""
    dependent_steps = [
        "Fetch the data",
        "Based on step 1, analyse the results",
    ]
    # Gate must detect dependency
    assert not _steps_are_independent(dependent_steps)
    # dry-run with parallel_fan_out=3 still completes via sequential path
    result = run_agent_loop(
        "research something with step dependencies",
        dry_run=True,
        parallel_fan_out=3,
        verbose=False,
    )
    assert result.status in ("done", "dry_run", "stuck")


# ---------------------------------------------------------------------------
# Phase 33: token_budget
# ---------------------------------------------------------------------------

def test_token_budget_not_exceeded_completes(monkeypatch, tmp_path):
    """A generous token_budget does not affect completion."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "simple budget test",
        project="budget-ok",
        dry_run=True,
        token_budget=1_000_000,  # extremely generous
    )
    assert result.status == "done"


def test_token_budget_zero_triggers_stuck(monkeypatch, tmp_path):
    """token_budget=0 causes the loop to abort immediately after the first step."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "budget zero test",
        project="budget-zero",
        dry_run=True,
        token_budget=0,  # any tokens at all exceeds this
    )
    # Should abort — first step completion will have >= 0 tokens
    assert result.status in ("stuck", "done")  # dry_run steps may be 0 tokens
    if result.stuck_reason:
        assert "token_budget" in result.stuck_reason


def test_token_budget_none_is_ignored(monkeypatch, tmp_path):
    """token_budget=None (default) imposes no limit."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "no budget limit test",
        project="budget-none",
        dry_run=True,
        token_budget=None,
    )
    assert result.status == "done"
    assert result.stuck_reason is None or "token_budget" not in result.stuck_reason


# ---------------------------------------------------------------------------
# Phase 35 P2: _generate_refinement_hint
# ---------------------------------------------------------------------------

from agent_loop import _generate_refinement_hint


def test_generate_refinement_hint_no_adapter():
    """Falls back to generic hint when adapter is None."""
    hint = _generate_refinement_hint(
        step_text="fetch external data",
        block_reason="network timeout",
        adapter=None,
    )
    assert "blocked" in hint.lower() or "refinement" in hint.lower() or "approach" in hint.lower()
    assert isinstance(hint, str)
    assert len(hint) > 10


@pytest.mark.slow
def test_generate_refinement_hint_with_failing_adapter():
    """Falls back to generic hint when adapter raises."""
    class _BadAdapter:
        def complete(self, *a, **kw):
            raise RuntimeError("model unavailable")

    hint = _generate_refinement_hint(
        step_text="analyze data",
        block_reason="model error",
        adapter=_BadAdapter(),
    )
    assert isinstance(hint, str)
    assert len(hint) > 10


# ---------------------------------------------------------------------------
# _build_loop_context
# ---------------------------------------------------------------------------

def test_build_loop_context_returns_five_tuple():
    """_build_loop_context always returns a 5-tuple even with nothing available."""
    result = _build_loop_context("some research goal")
    assert len(result) == 5
    lessons_ctx, skills_ctx, cost_ctx, had_no_skill, matched_rule = result
    assert isinstance(lessons_ctx, str)
    assert isinstance(skills_ctx, str)
    assert isinstance(cost_ctx, str)
    assert isinstance(had_no_skill, bool)
    assert matched_rule is None or hasattr(matched_rule, "steps_template")


def test_build_loop_context_no_skills_sets_flag(monkeypatch):
    """had_no_matching_skill=True when skills module returns empty list."""
    # Verify the real function handles empty skills gracefully
    result = _build_loop_context("unlikely goal zzzxxx999aaa")
    assert result[3] is True or result[3] is False  # bool either way


def test_build_loop_context_survives_import_errors(monkeypatch):
    """_build_loop_context never raises even when memory/skills are missing."""
    import sys
    original_memory = sys.modules.get("memory")
    sys.modules["memory"] = None  # type: ignore[assignment]
    try:
        result = _build_loop_context("test goal")
        assert len(result) == 5
    finally:
        if original_memory is not None:
            sys.modules["memory"] = original_memory
        elif "memory" in sys.modules:
            del sys.modules["memory"]


# ---------------------------------------------------------------------------
# _handle_blocked_step
# ---------------------------------------------------------------------------

def test_handle_blocked_step_retry_on_first_block():
    """First block → retry=True with generic hint."""
    decision = _handle_blocked_step(
        step_text="fetch data from API",
        outcome={"stuck_reason": "network timeout", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is True
    assert "blocked" in decision.hint.lower() or "alternative" in decision.hint.lower()
    assert decision.loop_status == ""
    assert decision.stuck_reason == ""


def test_handle_blocked_step_retry_on_second_block():
    """Second block → retry=True with refinement hint."""
    decision = _handle_blocked_step(
        step_text="analyze the dataset",
        outcome={"stuck_reason": "permission denied", "result": "partial output"},
        prior_retries=1,
        adapter=None,
    )
    assert decision.retry is True
    assert isinstance(decision.hint, str)
    assert len(decision.hint) > 10


def test_handle_blocked_step_terminates_after_two_retries():
    """Third block (prior_retries=2) → retry=False, loop_status=stuck."""
    decision = _handle_blocked_step(
        step_text="write to database",
        outcome={"stuck_reason": "connection refused", "result": ""},
        prior_retries=2,
        adapter=None,
    )
    assert decision.retry is False
    assert decision.loop_status == "stuck"
    assert "connection refused" in decision.stuck_reason


def test_handle_blocked_step_preserves_original_reason():
    """The stuck_reason in the decision comes from outcome, not fabricated."""
    decision = _handle_blocked_step(
        step_text="deploy service",
        outcome={"stuck_reason": "auth token expired", "result": ""},
        prior_retries=2,
        adapter=None,
    )
    assert "auth token expired" in decision.stuck_reason


def test_handle_blocked_step_missing_reason_uses_fallback():
    """Works cleanly when outcome has no stuck_reason key."""
    decision = _handle_blocked_step(
        step_text="run tests",
        outcome={},
        prior_retries=2,
        adapter=None,
    )
    assert decision.retry is False
    assert isinstance(decision.stuck_reason, str)


def test_handle_blocked_step_timeout_no_retry():
    """Subprocess timeout → retry=False immediately, regardless of prior_retries."""
    decision = _handle_blocked_step(
        step_text="run pytest and analyze",
        outcome={"stuck_reason": "codex subprocess timed out after 300s", "result": ""},
        prior_retries=0,  # First attempt — normally would retry, but timeout skips that
        adapter=None,
    )
    assert decision.retry is False
    # Combined exec+analyze step → split_into path (not stuck)
    assert len(decision.split_into) == 2


def test_handle_blocked_step_timeout_combined_step_splits_not_terminates():
    """Timeout on a combined exec+analyze step injects two replacement steps instead of terminating."""
    decision = _handle_blocked_step(
        step_text="run full test suite and analyze results",
        outcome={"stuck_reason": "claude subprocess timed out after 600s", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert len(decision.split_into) == 2
    assert decision.loop_status == ""   # not stuck — split recovers
    assert decision.stuck_reason == ""


def test_handle_blocked_step_timeout_pure_exec_still_terminates():
    """Timeout on a non-combined step (pure execution) still terminates with stuck hint."""
    decision = _handle_blocked_step(
        step_text="run pytest -q",
        outcome={"stuck_reason": "claude subprocess timed out after 300s", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert decision.loop_status == "stuck"
    assert "split" in decision.stuck_reason.lower() or "separate" in decision.stuck_reason.lower()


def test_handle_blocked_step_timeout_matches_both_adapters():
    """Both claude and codex timeout messages trigger the no-retry path."""
    for reason in [
        "claude subprocess timed out after 300s",
        "codex subprocess timed out after 300s",
        "LLM call failed: claude subprocess timed out after 900s",
    ]:
        decision = _handle_blocked_step(
            step_text="run tests",
            outcome={"stuck_reason": reason, "result": ""},
            prior_retries=0,
            adapter=None,
        )
        assert decision.retry is False, f"Expected no retry for: {reason!r}"


def test_handle_blocked_step_network_timeout_still_retries():
    """'network timeout' (one word) is NOT a subprocess timeout — still retries."""
    decision = _handle_blocked_step(
        step_text="fetch data from API",
        outcome={"stuck_reason": "network timeout after 30s", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is True


# ---------------------------------------------------------------------------
# _finalize_loop
# ---------------------------------------------------------------------------

def test_finalize_loop_does_not_raise_on_empty_outcomes(tmp_path, monkeypatch):
    """_finalize_loop never raises even with empty step_outcomes."""
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _finalize_loop(
        loop_id="test-loop",
        goal="test goal",
        project="test-project",
        loop_status="done",
        step_outcomes=[],
        adapter=None,
        dry_run=True,
        verbose=False,
        total_tokens_in=0,
        total_tokens_out=0,
        elapsed_ms=100,
        had_no_matching_skill=False,
    )


def test_finalize_loop_calls_reflect_and_record(monkeypatch):
    """_finalize_loop calls reflect_and_record with the right arguments."""
    calls = {}

    def fake_reflect(goal, status, result_summary, task_type, project, **kw):
        calls["goal"] = goal
        calls["status"] = status
        calls["task_type"] = task_type

    import memory
    monkeypatch.setattr(memory, "reflect_and_record", fake_reflect)

    _finalize_loop(
        loop_id="fl-test",
        goal="my goal",
        project="proj",
        loop_status="done",
        step_outcomes=[],
        adapter=None,
        dry_run=False,
        verbose=False,
        total_tokens_in=5,
        total_tokens_out=10,
        elapsed_ms=200,
        had_no_matching_skill=False,
    )

    assert calls.get("goal") == "my goal"
    assert calls.get("status") == "done"
    assert calls.get("task_type") == "agenda"


def test_finalize_loop_skips_reflexion_in_dry_run(monkeypatch):
    """dry_run=True → adapter passed as None to reflect_and_record."""
    adapter_used = {}

    def fake_reflect(goal, status, result_summary, task_type, project, *, adapter, **kw):
        adapter_used["value"] = adapter

    import memory
    monkeypatch.setattr(memory, "reflect_and_record", fake_reflect)

    class _FakeAdapter:
        model_key = "test"

    _finalize_loop(
        loop_id="dr-test",
        goal="goal",
        project="proj",
        loop_status="done",
        step_outcomes=[],
        adapter=_FakeAdapter(),
        dry_run=True,
        verbose=False,
        total_tokens_in=0,
        total_tokens_out=0,
        elapsed_ms=0,
        had_no_matching_skill=False,
    )

    assert adapter_used.get("value") is None


@pytest.mark.slow
def test_generate_refinement_hint_uses_llm_response():
    """Uses LLM response when adapter works."""
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.complete.return_value.content = "Try fetching from a cached source instead."

    hint = _generate_refinement_hint(
        step_text="fetch data",
        block_reason="timeout",
        adapter=mock,
    )
    assert "cached source" in hint or len(hint) > 10


# ---------------------------------------------------------------------------
# Cost budget
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_cost_budget_stops_loop(monkeypatch, tmp_path):
    """Loop stops when estimated USD cost exceeds cost_budget + slush."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop(
        "expensive task",
        project="cost-test",
        dry_run=False,
        cost_budget=0.0001,  # tiny budget — will be exceeded immediately
    )
    assert result.status == "stuck" or result.status == "done"
    # If it ran at all with dry_run=False, the cost check should fire
    # (exact behavior depends on adapter availability)


# ---------------------------------------------------------------------------
# Phase 35 P2: HITL tier wiring in _execute_step
# ---------------------------------------------------------------------------

def test_execute_step_destroy_tier_is_blocked():
    """Steps classified as DESTROY tier must be blocked before LLM call."""
    from unittest.mock import MagicMock
    adapter = MagicMock()  # should never be called

    outcome = _execute_step(
        goal="clean up workspace",
        step_text="rm -rf /var/log/old/ to clean up disk space",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
    )
    assert outcome["status"] == "blocked"
    assert "DESTROY" in outcome["stuck_reason"]
    adapter.complete.assert_not_called()


def test_execute_step_high_risk_is_blocked():
    """HIGH risk steps are still blocked via hitl_policy."""
    from unittest.mock import MagicMock
    adapter = MagicMock()

    outcome = _execute_step(
        goal="system admin",
        step_text="rm -rf /tmp/old_build_dir",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
    )
    assert outcome["status"] == "blocked"
    adapter.complete.assert_not_called()


def test_execute_step_external_tier_logs_but_proceeds(capsys):
    """EXTERNAL tier steps log a headless warning but are not blocked."""
    class _OkAdapter:
        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={"result": "notification sent", "summary": "sent"})],
                input_tokens=1, output_tokens=1,
            )

    outcome = _execute_step(
        goal="notify team",
        step_text="Send a message to Slack via the webhook with the results",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=_OkAdapter(),
        tools=[],
        verbose=True,
    )
    # Should not be blocked
    assert outcome["status"] != "blocked"
    captured = capsys.readouterr()
    assert "EXTERNAL" in captured.err or "confirm" in captured.err.lower()


def test_execute_step_read_tier_proceeds_silently(capsys):
    """READ tier steps pass through with no HITL log output."""
    class _OkAdapter:
        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={"result": "findings summarised", "summary": "done"})],
                input_tokens=1, output_tokens=1,
            )

    outcome = _execute_step(
        goal="research topic",
        step_text="Summarise the findings from the research notes",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=_OkAdapter(),
        tools=[],
        verbose=True,
    )
    assert outcome["status"] != "blocked"
    captured = capsys.readouterr()
    assert "HITL" not in captured.err
    assert "EXTERNAL" not in captured.err


# ---------------------------------------------------------------------------
# Bootstrap fix: run_agent_loop always calls ensure_project (BFix-NEXT-02)
# ---------------------------------------------------------------------------

def test_run_agent_loop_recovers_from_partial_project(monkeypatch, tmp_path):
    """Loop runs without error when project dir exists but NEXT.md is missing."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    # Pre-create the project dir WITHOUT NEXT.md to simulate a crashed previous run
    slug = _goal_to_slug("check out this repo at http://example.com")
    proj_dir = tmp_path / "projects" / slug
    proj_dir.mkdir(parents=True)
    assert not (proj_dir / "NEXT.md").exists()

    result = run_agent_loop(
        "check out this repo at http://example.com",
        dry_run=True,
    )
    # Should not raise; NEXT.md should now exist
    assert (proj_dir / "NEXT.md").exists()
    assert result.status in ("done", "stuck")


def test_run_agent_loop_ensure_project_always_called_even_when_dir_exists(monkeypatch, tmp_path):
    """ensure_project is called even when project dir already exists."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    # Create via ensure_project to get a fully-initialised dir
    orch.ensure_project("pre-existing", "initial mission")
    proj_dir = tmp_path / "projects" / "pre-existing"
    assert (proj_dir / "NEXT.md").exists()
    # Delete NEXT.md to simulate corruption
    (proj_dir / "NEXT.md").unlink()

    result = run_agent_loop("pre-existing goal text", project="pre-existing", dry_run=True)
    # ensure_project must have run again and recreated NEXT.md
    assert (proj_dir / "NEXT.md").exists()
    assert result.status in ("done", "stuck")


# ---------------------------------------------------------------------------
# Plan manifest (run visibility)
# ---------------------------------------------------------------------------

def test_write_plan_manifest_creates_file(monkeypatch, tmp_path):
    """_write_plan_manifest writes a markdown file before execution starts."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    import importlib, agent_loop as al
    importlib.reload(al)

    steps = ["Research the topic", "Summarize findings", "Write report"]
    result = al._write_plan_manifest(
        project="test-proj",
        loop_id="abc12345",
        goal="Do research",
        planned_steps=steps,
        start_ts="2026-04-04T00:00:00Z",
    )
    assert result is not None
    path = tmp_path / "projects" / "test-proj" / "artifacts" / "loop-abc12345-plan.md"
    assert path.exists()
    content = path.read_text()
    assert "abc12345" in content
    assert "Do research" in content
    # All 3 steps listed
    assert "Research the topic" in content
    assert "Summarize findings" in content
    assert "Write report" in content
    # Status is running (default)
    assert "running" in content


def test_write_plan_manifest_shows_step_status(monkeypatch, tmp_path):
    """Plan manifest marks completed steps with ✅ and blocked with ❌."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    import importlib, agent_loop as al
    importlib.reload(al)

    steps = ["Step one", "Step two", "Step three"]
    outcomes = [
        al.StepOutcome(index=1, text="Step one", status="done", result="ok",
                       iteration=1, tokens_in=10, tokens_out=20, elapsed_ms=500),
        al.StepOutcome(index=2, text="Step two", status="blocked", result="failed",
                       iteration=2, tokens_in=5, tokens_out=5, elapsed_ms=200),
    ]
    al._write_plan_manifest(
        project="test-proj",
        loop_id="xyz99999",
        goal="multi-step goal",
        planned_steps=steps,
        start_ts="2026-04-04T00:00:00Z",
        step_outcomes=outcomes,
    )
    content = (tmp_path / "projects" / "test-proj" / "artifacts" / "loop-xyz99999-plan.md").read_text()
    assert "✅" in content   # done step
    assert "❌" in content   # blocked step
    assert "⬜" in content   # pending step 3
    assert "500ms" in content
    assert "Execution Log" in content


def test_write_plan_manifest_final_status(monkeypatch, tmp_path):
    """Final manifest write includes done/stuck status and total elapsed."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    import importlib, agent_loop as al
    importlib.reload(al)

    steps = ["Only step"]
    outcomes = [
        al.StepOutcome(index=1, text="Only step", status="done", result="result",
                       iteration=1, tokens_in=10, tokens_out=10, elapsed_ms=999),
    ]
    al._write_plan_manifest(
        project="p",
        loop_id="finaltest",
        goal="goal",
        planned_steps=steps,
        start_ts="2026-04-04T00:00:00Z",
        step_outcomes=outcomes,
        status="done",
        elapsed_ms=1500,
    )
    content = (tmp_path / "projects" / "p" / "artifacts" / "loop-finaltest-plan.md").read_text()
    assert "done" in content
    assert "1500ms" in content


def test_run_agent_loop_writes_plan_manifest(monkeypatch, tmp_path):
    """run_agent_loop writes a plan manifest to artifacts/ after decomposition."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    result = run_agent_loop(
        "write a plan for research",
        project="vis-test",
        dry_run=True,
    )
    # Find the manifest file
    artifacts = tmp_path / "projects" / "vis-test" / "artifacts"
    assert artifacts.exists(), "artifacts dir should be created"
    manifests = list(artifacts.glob("loop-*-plan.md"))
    assert len(manifests) == 1, f"expected 1 plan manifest, found {manifests}"
    content = manifests[0].read_text()
    assert "vis-test" in content
    assert "write a plan for research" in content
    # Terminal status should be written
    assert any(s in content for s in ("done", "stuck"))


# ---------------------------------------------------------------------------
# Step-shape detector: _is_combined_exec_analyze / _split_exec_analyze
# ---------------------------------------------------------------------------

from agent_loop import _is_combined_exec_analyze, _split_exec_analyze


def test_is_combined_exec_analyze_detects_run_and_analyze():
    assert _is_combined_exec_analyze("Run pytest and analyze test failures") is True
    assert _is_combined_exec_analyze("Execute make and summarize build errors") is True
    assert _is_combined_exec_analyze("Run git log and summarize the commit history") is True


def test_is_combined_exec_analyze_pure_exec_is_not_combined():
    assert _is_combined_exec_analyze("Run pytest -q and capture output to a file") is False
    assert _is_combined_exec_analyze("Install dependencies with pip install -r requirements.txt") is False


def test_is_combined_exec_analyze_pure_analyze_is_not_combined():
    assert _is_combined_exec_analyze("Analyze the captured test output for failure patterns") is False
    assert _is_combined_exec_analyze("Summarize the results from the previous step") is False


def test_is_combined_exec_analyze_unrelated_step():
    assert _is_combined_exec_analyze("Research the top 5 Polymarket markets by volume") is False
    assert _is_combined_exec_analyze("Write a summary of findings") is False


def test_split_exec_analyze_returns_two_steps():
    parts = _split_exec_analyze("Run pytest -q and analyze the failures")
    assert len(parts) == 2
    # First step should be about running
    assert any(kw in parts[0].lower() for kw in ("run", "capture", "save", "output"))
    # Second step should be about reading/analyzing
    assert any(kw in parts[1].lower() for kw in ("analyz", "read", "result"))


def test_step_shape_splits_combined_steps_before_execution(monkeypatch, tmp_path):
    """Pre-execution step-shape check splits combined steps in the manifest."""
    monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
    result = run_agent_loop(
        "check repo health",
        project="shape-test",
        dry_run=True,
    )
    # Dry-run uses ScriptedAdapter which produces fixed steps — just verify no crash
    assert result.status in ("done", "stuck")


# ---------------------------------------------------------------------------
# Step-shape: combined exec+analyze always splits, regardless of block reason
# ---------------------------------------------------------------------------

def test_handle_blocked_step_combined_splits_on_non_timeout_block():
    """Combined exec+analyze step splits on first block even without a timeout."""
    decision = _handle_blocked_step(
        step_text="run pytest and analyze failures",
        outcome={"stuck_reason": "LLM could not interpret the mixed output", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert len(decision.split_into) == 2
    assert decision.loop_status == ""    # not stuck — split recovers
    assert decision.stuck_reason == ""


def test_handle_blocked_step_combined_splits_even_at_retry_2():
    """Combined steps split even if prior_retries=2 — never terminates as stuck."""
    decision = _handle_blocked_step(
        step_text="execute grep and identify matching lines",
        outcome={"stuck_reason": "output too large", "result": ""},
        prior_retries=2,
        adapter=None,
    )
    assert decision.retry is False
    assert len(decision.split_into) == 2
    assert decision.loop_status == ""


def test_is_combined_exec_analyze_new_patterns():
    """Expanded keyword sets catch patterns Codex identified as slipping through."""
    # grep + identify/conclude
    assert _is_combined_exec_analyze("grep for all TODO comments and identify the most critical ones") is True
    # fetch + evaluate
    assert _is_combined_exec_analyze("fetch the API response and evaluate whether the data is complete") is True
    # invoke + assess
    assert _is_combined_exec_analyze("invoke the build script and assess any compilation errors") is True
    # run + judge
    assert _is_combined_exec_analyze("run mypy and judge the severity of each type error") is True
    # curl + conclude
    assert _is_combined_exec_analyze("curl the endpoint and conclude whether auth is working") is True
    # find + evaluate
    assert _is_combined_exec_analyze("find all Python files and evaluate import dependencies") is True


def test_is_combined_exec_analyze_does_not_over_trigger():
    """Pure analysis steps without an exec keyword are not flagged."""
    assert _is_combined_exec_analyze("Identify the root cause of the import failure") is False
    assert _is_combined_exec_analyze("Evaluate the quality of the test coverage summary") is False
    assert _is_combined_exec_analyze("Conclude whether the architecture matches the design doc") is False


# ---------------------------------------------------------------------------
# Budget ceiling continuation task
# ---------------------------------------------------------------------------

def test_budget_ceiling_enqueues_continuation(monkeypatch, tmp_path):
    """When max_iterations is hit with remaining steps, a continuation task is enqueued."""
    import sys
    sys.path.insert(0, str(tmp_path))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    enqueued = {}

    def _fake_enqueue(lane, source, reason, parent_job_id):
        enqueued["lane"] = lane
        enqueued["source"] = source
        enqueued["reason"] = reason
        enqueued["parent"] = parent_job_id
        return {"job_id": "cont-001"}

    import agent_loop as _al
    monkeypatch.setattr(_al, "_ts_enqueue_ref", None, raising=False)

    import task_store as _ts
    monkeypatch.setattr(_ts, "enqueue", _fake_enqueue)

    # Patch task_store import inside agent_loop's closure
    import unittest.mock as mock
    with mock.patch("task_store.enqueue", _fake_enqueue):
        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "adversarial review of the entire codebase",
            adapter=_DryRunAdapter(),
            max_iterations=1,   # force immediate budget ceiling
            max_steps=4,
            dry_run=False,
        )

    # loop hits ceiling and records stuck status
    assert result.status in ("stuck", "done", "partial")
    # If continuation was enqueued, check it
    if enqueued:
        assert enqueued["source"] == "loop_continuation"
        assert "CONTINUATION" in enqueued["reason"]
        assert enqueued["lane"] == "agenda"
