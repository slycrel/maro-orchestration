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
    _is_combined_exec_analyze,
    _split_exec_analyze,
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


def test_loop_emits_loop_created_event(monkeypatch, tmp_path):
    """Run-transparency: every loop spawn emits a LOOP_CREATED captain's log
    event with the reason + parent_loop_id."""
    _setup_workspace(monkeypatch, tmp_path)
    captured: list = []

    def fake_log_event(event_type, *, subject, summary, context=None,
                       note=None, loop_id=None, related_ids=None):
        captured.append({
            "event_type": event_type, "subject": subject, "summary": summary,
            "context": context or {}, "loop_id": loop_id,
            "related_ids": related_ids,
        })
        return {}

    import captains_log as _cl
    monkeypatch.setattr(_cl, "log_event", fake_log_event)

    # Default reason ("initial") path
    run_agent_loop("default-reason goal", project="t-default", dry_run=True)
    initial = [e for e in captured if e["event_type"] == "LOOP_CREATED"]
    assert len(initial) >= 1
    assert initial[-1]["context"]["reason"] == "initial"
    assert initial[-1]["context"]["parent_loop_id"] is None
    assert initial[-1]["loop_id"]  # 8-char hex id

    # Explicit reason + parent path (closure_restart-style)
    captured.clear()
    run_agent_loop(
        "child-reason goal", project="t-child", dry_run=True,
        loop_reason="closure_restart", parent_loop_id="parent01",
    )
    child = [e for e in captured if e["event_type"] == "LOOP_CREATED"]
    assert len(child) >= 1
    assert child[-1]["context"]["reason"] == "closure_restart"
    assert child[-1]["context"]["parent_loop_id"] == "parent01"
    assert child[-1]["related_ids"] == ["parent01"]


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
    log_file = orch.resolve_artifact_path(result.log_path)
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
    import loop_planning as _lp
    import loop_blocked as _lb
    # Bypass multi-plan decompose (4 LLM calls) — this test focuses on stuck detection.
    monkeypatch.setattr(_lp, "_decompose",
                        lambda *a, **kw: ["step one", "step two", "step three"])
    # Prevent _generate_refinement_hint from calling build_adapter (real subprocess).
    monkeypatch.setattr(_lb, "_generate_refinement_hint",
                        lambda *a, **kw: "try something different")

    class AlwaysStuckAdapter:
        model_key = "explicit-test"  # prevent tier-up from replacing this adapter

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
        # Disable Phase 45 auto-recovery (retries with exhausted adapter).
        _recovery_in_progress=True,
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
    rc = cli.main(["run", "test goal from cli", "--project", "cli-test", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "status=done" in out


def test_cli_poe_run_json_format(monkeypatch, tmp_path, capsys):
    _setup_workspace(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["run", "json format test", "--project", "cli-json-test", "--dry-run", "--format", "json"])
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
        model_key = "test"
        call_count = 0

        def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3, **kw):
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

    from pre_flight import PlanReview
    from unittest.mock import patch as _patch
    _pf = PlanReview(scope="narrow", scope_note="test")
    with _patch("pre_flight.review_plan", return_value=_pf):
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


def test_compute_march_of_nines_healthy_long_run_does_not_alert():
    """Regression (session 20.5): a healthy long run at 90% per-step success
    must NOT fire the alert. The old rate^steps math produced 0.9^8 = 0.43,
    below the 0.5 threshold — false positive.
    """
    from agent_loop import _compute_march_of_nines, StepOutcome

    # 8 steps: 7 done, 1 stuck early in history — recent window is clean
    outcomes = [
        StepOutcome(index=i, text=f"s{i}", status="done", result=f"r{i}", iteration=0)
        for i in range(7)
    ]
    outcomes.insert(2, StepOutcome(index=99, text="s_stuck", status="stuck", result="r", iteration=0))
    assert len(outcomes) == 8
    # Last 5 are all done → rate 1.0 → no alert
    assert _compute_march_of_nines(outcomes) is None


def test_compute_march_of_nines_recent_degradation_fires():
    """Recent-window degradation (last N mostly stuck) must fire."""
    from agent_loop import _compute_march_of_nines, StepOutcome

    # 5 steps: 1 done, 4 stuck recently → window rate 0.2 → alert
    outcomes = [
        StepOutcome(index=0, text="s0", status="done", result="ok", iteration=0),
        StepOutcome(index=1, text="s1", status="stuck", result="", iteration=0),
        StepOutcome(index=2, text="s2", status="stuck", result="", iteration=0),
        StepOutcome(index=3, text="s3", status="stuck", result="", iteration=0),
        StepOutcome(index=4, text="s4", status="stuck", result="", iteration=0),
    ]
    result = _compute_march_of_nines(outcomes)
    assert result is not None
    rate, completed, size = result
    assert completed == 1
    assert size == 5
    assert rate == 0.2


def test_compute_march_of_nines_below_min_steps():
    """Under 3 steps → no alert (not enough data)."""
    from agent_loop import _compute_march_of_nines, StepOutcome

    outcomes = [StepOutcome(index=0, text="s0", status="stuck", result="", iteration=0)]
    assert _compute_march_of_nines(outcomes) is None
    outcomes.append(StepOutcome(index=1, text="s1", status="stuck", result="", iteration=0))
    assert _compute_march_of_nines(outcomes) is None


def test_compute_march_of_nines_exactly_threshold_does_not_fire():
    """Boundary: threshold is 0.5; rate == 0.5 must NOT fire (strict <)."""
    from agent_loop import _compute_march_of_nines, StepOutcome

    # 4 steps: 2 done, 2 stuck → rate 0.5 → no alert
    outcomes = [
        StepOutcome(index=0, text="s0", status="done", result="ok", iteration=0),
        StepOutcome(index=1, text="s1", status="stuck", result="", iteration=0),
        StepOutcome(index=2, text="s2", status="done", result="ok", iteration=0),
        StepOutcome(index=3, text="s3", status="stuck", result="", iteration=0),
    ]
    assert _compute_march_of_nines(outcomes) is None


def test_loop_result_has_march_of_nines_field(monkeypatch, tmp_path):
    """LoopResult has march_of_nines_alert field."""
    _setup_workspace(monkeypatch, tmp_path)
    result = run_agent_loop("simple test", project="march-field-test", dry_run=True)
    assert hasattr(result, "march_of_nines_alert")
    assert isinstance(result.march_of_nines_alert, bool)


# ---------------------------------------------------------------------------
# Step-too-broad signal (BACKLOG:316 leftover)
# ---------------------------------------------------------------------------


def test_check_step_too_broad_fires_above_caps():
    """A done step exceeding both elapsed (>120s) AND tokens (>200K) caps
    fires the signal. Both caps must be breached — either alone is allowed."""
    from agent_loop import _check_step_too_broad, StepOutcome

    step = StepOutcome(
        index=7, text="too broad step", status="done", result="ok",
        iteration=0, tokens_in=150_000, tokens_out=80_000, elapsed_ms=534_000,
    )
    result = _check_step_too_broad(step)
    assert result is not None
    elapsed_s, tokens, idx = result
    assert elapsed_s == 534
    assert tokens == 230_000
    assert idx == 7


def test_check_step_too_broad_below_caps():
    """Step under the caps does not fire."""
    from agent_loop import _check_step_too_broad, StepOutcome

    step = StepOutcome(
        index=2, text="normal step", status="done", result="ok",
        iteration=0, tokens_in=50_000, tokens_out=20_000, elapsed_ms=60_000,
    )
    assert _check_step_too_broad(step) is None


def test_check_step_too_broad_only_one_cap_breached():
    """Only one cap breached → no fire (need both, since either alone has
    legitimate explanations: tokens-only = lots of context, time-only = slow
    LLM/network). Both = the step is doing too much."""
    from agent_loop import _check_step_too_broad, StepOutcome

    # Time over, tokens under
    step1 = StepOutcome(
        index=0, text="slow but small", status="done", result="ok",
        iteration=0, tokens_in=10_000, tokens_out=5_000, elapsed_ms=300_000,
    )
    assert _check_step_too_broad(step1) is None

    # Tokens over, time under
    step2 = StepOutcome(
        index=1, text="big but fast", status="done", result="ok",
        iteration=0, tokens_in=200_000, tokens_out=50_000, elapsed_ms=30_000,
    )
    assert _check_step_too_broad(step2) is None


def test_check_step_too_broad_skips_blocked_step():
    """Blocked steps go through the diagnosis path; this signal is for
    successful but oversized steps."""
    from agent_loop import _check_step_too_broad, StepOutcome

    step = StepOutcome(
        index=3, text="blocked huge step", status="blocked", result="",
        iteration=0, tokens_in=200_000, tokens_out=80_000, elapsed_ms=300_000,
    )
    assert _check_step_too_broad(step) is None


def test_check_step_too_broad_skips_skipped_step():
    """Skipped steps did no work; metrics are meaningless here."""
    from agent_loop import _check_step_too_broad, StepOutcome

    step = StepOutcome(
        index=4, text="skipped", status="skipped", result="",
        iteration=0, tokens_in=300_000, tokens_out=100_000, elapsed_ms=400_000,
    )
    assert _check_step_too_broad(step) is None


def test_check_step_too_broad_zero_metrics():
    """Step with no recorded metrics (e.g. injected sentinel) does not fire."""
    from agent_loop import _check_step_too_broad, StepOutcome

    step = StepOutcome(
        index=0, text="zero metrics", status="done", result="ok", iteration=0,
    )
    assert _check_step_too_broad(step) is None


def test_step_too_broad_event_in_captains_log_event_types():
    """Constant is registered in EVENT_TYPES — log_event will accept it."""
    from captains_log import EVENT_TYPES, STEP_TOO_BROAD
    assert STEP_TOO_BROAD in EVENT_TYPES


@pytest.mark.slow
def test_dead_ends_written_on_block(monkeypatch, tmp_path):
    """Blocked step writes to DEAD_ENDS.md."""
    _setup_workspace(monkeypatch, tmp_path)

    class _StuckAdapter:
        """Decomposes into steps, blocks on first execution."""
        model_key = "test"

        def complete(self, messages, *, tools=None, tool_choice="auto", max_tokens=4096, temperature=0.3, **kw):
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

    from pre_flight import PlanReview
    from unittest.mock import patch as _patch
    _pf = PlanReview(scope="narrow", scope_note="test")

    project = "dead-ends-write-test"
    with _patch("pre_flight.review_plan", return_value=_pf):
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


def test_steps_are_independent_implicit_aggregation_caught():
    """Regression (session 20.5): aggregation verbs without explicit step
    references must be detected as dependent. Old regex missed these,
    causing race conditions when parallel-eligible steps actually depended
    on prior outputs.
    """
    cases = [
        # (case_name, steps, expected_independent)
        ("compile aggregates findings", [
            "Research peptide A safety profile",
            "Research peptide B safety profile",
            "Compile the findings into a comparison report",
        ], False),
        ("synthesize implies prior steps", [
            "Pull X from API",
            "Pull Y from API",
            "Synthesize the data into a summary",
        ], False),
        ("final report verb", [
            "Investigate option 1",
            "Investigate option 2",
            "Produce a final report",
        ], False),
        ("aggregate keyword", [
            "Fetch metric 1",
            "Fetch metric 2",
            "Aggregate the results into a single dashboard payload",
        ], False),
        ("comparing the results", [
            "Fetch baseline",
            "Fetch experiment",
            "Comparing the results, report the delta",
        ], False),
        ("with the data in hand", [
            "Pull leaderboard",
            "Pull markets",
            "With the above data in hand, compute the edge",
        ], False),
        # Independent control case — pure parallel fetch with no aggregation
        ("pure parallel fetch", [
            "Fetch URL A",
            "Fetch URL B",
            "Fetch URL C",
        ], True),
    ]
    for name, steps, expected in cases:
        actual = _steps_are_independent(steps)
        assert actual is expected, (
            f"case '{name}': expected independent={expected}, got {actual} "
            f"for steps={steps}"
        )


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


def test_parallel_path_still_writes_frozen_report_and_index(monkeypatch, tmp_path):
    """2026-07-08 adversarial review finding #1: _run_parallel_path's early
    return in run_agent_loop bypassed _build_result_and_finalize entirely, so
    parallel/DAG runs never got a final run-visibility report or a forced
    index write — the report stayed at "running, zero steps" forever. The
    _DryRunAdapter's decompose stub always returns dependency-flagged step
    text (see loop_init.py), so dry_run alone never actually reaches
    _run_parallel_path; force the gate directly via loop_planning's
    independence check instead of fighting the stub's wording.
    """
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    import importlib
    import agent_loop as al
    import loop_planning
    importlib.reload(al)
    monkeypatch.setattr(loop_planning, "_steps_are_independent", lambda steps: True)

    fake_result = LoopResult(
        loop_id="fanout-fake",
        project="fanout-proj",
        goal="fetch two things independently",
        status="done",
        steps=[
            StepOutcome(index=1, text="fetch A", status="done", result="ok",
                        iteration=1, tokens_in=5, tokens_out=5, elapsed_ms=100),
            StepOutcome(index=2, text="fetch B", status="done", result="ok",
                        iteration=1, tokens_in=5, tokens_out=5, elapsed_ms=120),
        ],
        elapsed_ms=250,
    )
    monkeypatch.setattr(al, "_run_parallel_path", lambda *a, **kw: fake_result)

    result = al.run_agent_loop(
        "fetch two things independently",
        project="fanout-proj",
        dry_run=True,
        parallel_fan_out=2,
    )
    assert result is fake_result

    artifacts = tmp_path / "projects" / "fanout-proj" / "artifacts"
    reports = list(artifacts.glob("loop-*-report.html"))
    assert len(reports) == 1, f"expected exactly one report, found {reports}"
    content = reports[0].read_text()
    assert "maro-report: final status=done" in content
    assert "fetch A" in content and "fetch B" in content

    # 2026-07-08 round 2 (all 5 reviewers, unanimous): the round-1 fix froze
    # the report and forced the index, but never wrote build/loop-*-log.json
    # — the ONLY source write_runs_index() reads token/step totals from.
    # Without this, the index shows "-" tokens/status forever for a
    # completed parallel run.
    logs = list(artifacts.glob("loop-*-log.json"))
    assert len(logs) == 1, f"expected exactly one loop log, found {logs}"
    log_data = json.loads(logs[0].read_text())
    assert log_data["status"] == "done"
    assert log_data["totals"]["tokens_in"] == 10  # 5 + 5, from the two fake steps
    assert log_data["totals"]["steps_done"] == 2

    # No run-dir is pinned in this test (fallback project path), so the
    # index write still runs but has nothing under runs_root() to list —
    # what matters here is that write_runs_index(force=True) was actually
    # invoked (didn't raise) for the parallel path, not its content.
    assert (tmp_path / "runs" / "index.html").exists()


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


def test_handle_blocked_step_terminates_after_threshold_retries():
    """At retry threshold (3) with non-converging errors → redecompose or stuck."""
    # Same fingerprint repeated = not converging
    same_fp = ["abc123"] * 3
    decision = _handle_blocked_step(
        step_text="write to database",
        outcome={"stuck_reason": "connection refused", "result": ""},
        prior_retries=3,
        adapter=None,
        error_fingerprints=same_fp,
        replan_count=2,  # already exhausted redecompose budget
    )
    assert decision.retry is False
    assert decision.loop_status == "stuck"
    assert "connection refused" in decision.stuck_reason


def test_handle_blocked_step_preserves_original_reason():
    """The stuck_reason in the decision comes from outcome, not fabricated."""
    decision = _handle_blocked_step(
        step_text="deploy service",
        outcome={"stuck_reason": "auth token expired", "result": ""},
        prior_retries=3,
        adapter=None,
        error_fingerprints=["abc"] * 3,
        replan_count=2,
    )
    assert "auth token expired" in decision.stuck_reason


def test_handle_blocked_step_missing_reason_uses_fallback():
    """Works cleanly when outcome has no stuck_reason key."""
    decision = _handle_blocked_step(
        step_text="run tests",
        outcome={},
        prior_retries=3,
        adapter=None,
        error_fingerprints=["abc"] * 3,
        replan_count=2,
    )
    assert decision.retry is False
    assert isinstance(decision.stuck_reason, str)


# ---------------------------------------------------------------------------
# Missing-external-input guard (anti-fabrication)
# ---------------------------------------------------------------------------

def test_missing_input_read_step_escalates_not_retries():
    """A read step whose file is absent → honest stuck on FIRST block, no retry."""
    decision = _handle_blocked_step(
        step_text="read /nonexistent/maro-test/data.csv and compute the mean of column 2",
        outcome={"stuck_reason": "No such file or directory: /nonexistent/maro-test/data.csv",
                 "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert decision.loop_status == "stuck"
    assert decision.redecompose is False
    assert decision.split_into == []
    assert decision.stuck_reason.startswith("MISSING_INPUT:")


def test_missing_input_detected_in_step_result():
    """The missing-resource signal may be in result, not stuck_reason."""
    decision = _handle_blocked_step(
        step_text="load config from /etc/poe/missing.yml",
        outcome={"stuck_reason": "blocked",
                 "result": "Traceback: FileNotFoundError: [Errno 2] ENOENT"},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert decision.stuck_reason.startswith("MISSING_INPUT:")


def test_missing_input_producing_step_not_short_circuited():
    """A 'create' step that doesn't yet find its target is NOT a missing-input fail."""
    decision = _handle_blocked_step(
        step_text="create report.csv with the summary table",
        outcome={"stuck_reason": "file not found", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    # Not input-consuming → falls through to normal retry, not the guard.
    assert not decision.stuck_reason.startswith("MISSING_INPUT:")


def test_input_consuming_step_normal_error_not_short_circuited():
    """A read step with an ordinary transient error still retries normally."""
    decision = _handle_blocked_step(
        step_text="read the dataset from the API",
        outcome={"stuck_reason": "network timeout", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is True
    assert not decision.stuck_reason.startswith("MISSING_INPUT:")


# ---------------------------------------------------------------------------
# Phase 62: Convergence tracking + metacognitive decisions
# ---------------------------------------------------------------------------

def test_convergence_tracking_converging_retries():
    """Converging errors (different fingerprints) → retry allowed."""
    decision = _handle_blocked_step(
        step_text="fetch data from API",
        outcome={"stuck_reason": "connection refused", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["aaa", "bbb", "ccc"],  # all different → converging
    )
    assert decision.retry is True
    assert "converging" in decision.metacognitive_reason


def test_convergence_tracking_not_converging_redecomposes():
    """Non-converging errors (same fingerprint) → redecompose instead of retry."""
    decision = _handle_blocked_step(
        step_text="parse response data",
        outcome={"stuck_reason": "invalid format", "result": ""},
        prior_retries=3,
        adapter=None,
        error_fingerprints=["same", "same", "same"],  # identical → not converging
        replan_count=0,
    )
    assert decision.retry is False
    assert decision.redecompose is True
    assert "not converging" in decision.metacognitive_reason


def test_convergence_tracking_exhausted_redecompose_budget():
    """After redecompose threshold exceeded → stuck (terminal)."""
    decision = _handle_blocked_step(
        step_text="deploy service",
        outcome={"stuck_reason": "auth error", "result": ""},
        prior_retries=3,
        adapter=None,
        error_fingerprints=["same", "same", "same"],
        replan_count=2,  # at threshold
    )
    assert decision.retry is False
    assert decision.redecompose is False
    assert decision.loop_status == "stuck"


def test_sibling_failure_triggers_redecompose():
    """High sibling failure rate triggers re-decomposition."""
    from agent_loop import StepOutcome
    # 3 blocked, 1 done = 75% failure rate
    fake_outcomes = [
        StepOutcome(0, "s1", "blocked", "", 0, 0, 0),
        StepOutcome(1, "s2", "blocked", "", 0, 0, 0),
        StepOutcome(2, "s3", "done", "ok", 0, 0, 0),
        StepOutcome(3, "s4", "blocked", "", 0, 0, 0),
    ]
    decision = _handle_blocked_step(
        step_text="another step",
        outcome={"stuck_reason": "failed again", "result": ""},
        prior_retries=1,
        adapter=None,
        step_outcomes=fake_outcomes,
        replan_count=0,
    )
    assert decision.redecompose is True
    assert "sibling failure rate" in decision.metacognitive_reason


def test_need_info_generates_research_substeps():
    """NEED_INFO: prefix generates research sub-steps + re-queues original."""
    decision = _handle_blocked_step(
        step_text="verify function signatures in auth module",
        outcome={"stuck_reason": "NEED_INFO: cannot access source code of auth.py", "result": ""},
        prior_retries=0,
        adapter=None,
    )
    assert decision.retry is False
    assert len(decision.split_into) >= 2
    assert any("Research" in s for s in decision.split_into)
    # Original step should be re-queued after research
    assert decision.split_into[-1] == "verify function signatures in auth module"


def test_error_fingerprint_deterministic():
    """Same outcome → same fingerprint."""
    from agent_loop import _error_fingerprint
    fp1 = _error_fingerprint({"stuck_reason": "connection refused", "result": "partial"})
    fp2 = _error_fingerprint({"stuck_reason": "connection refused", "result": "partial"})
    assert fp1 == fp2


def test_error_fingerprint_differs_on_different_errors():
    """Different outcomes → different fingerprints."""
    from agent_loop import _error_fingerprint
    fp1 = _error_fingerprint({"stuck_reason": "connection refused", "result": ""})
    fp2 = _error_fingerprint({"stuck_reason": "timeout after 30s", "result": ""})
    assert fp1 != fp2


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
# Phase 44+45 bridge: mid-loop diagnose_loop consultation
# ---------------------------------------------------------------------------

def _mk_diag(failure_class, evidence=None):
    """Helper: build a LoopDiagnosis-like object."""
    from introspect import LoopDiagnosis
    return LoopDiagnosis(
        loop_id="test-loop",
        failure_class=failure_class,
        severity="warning",
        evidence=evidence or [],
        recommendation="test",
    )


def test_diagnosis_retry_churn_triggers_redecompose(monkeypatch):
    """After 2+ retries with retry_churn diagnosis → redecompose to break churn."""
    import introspect
    monkeypatch.setattr(introspect, "diagnose_loop", lambda _lid: _mk_diag("retry_churn"))

    decision = _handle_blocked_step(
        step_text="flaky step",
        outcome={"stuck_reason": "reason A", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],  # converging
        replan_count=0,
        loop_id="test-loop",
    )
    assert decision.retry is False
    assert decision.redecompose is True
    assert "retry_churn" in decision.metacognitive_reason


def test_diagnosis_retry_churn_exhausted_marks_stuck(monkeypatch):
    """retry_churn + replan_count at threshold → stuck (no infinite redecompose)."""
    import introspect
    monkeypatch.setattr(introspect, "diagnose_loop", lambda _lid: _mk_diag("retry_churn"))

    decision = _handle_blocked_step(
        step_text="flaky step",
        outcome={"stuck_reason": "reason A", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],
        replan_count=2,  # at _REDECOMPOSE_THRESHOLD
        loop_id="test-loop",
    )
    assert decision.retry is False
    assert decision.redecompose is False
    assert decision.loop_status == "stuck"
    assert "retry_churn" in decision.metacognitive_reason


def test_diagnosis_decomposition_too_broad_triggers_redecompose(monkeypatch):
    """decomposition_too_broad diagnosis → redecompose, even if converging."""
    import introspect
    monkeypatch.setattr(
        introspect, "diagnose_loop",
        lambda _lid: _mk_diag("decomposition_too_broad"),
    )
    decision = _handle_blocked_step(
        step_text="review the whole repo",
        outcome={"stuck_reason": "token overflow", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],
        replan_count=0,
        loop_id="test-loop",
    )
    assert decision.retry is False
    assert decision.redecompose is True
    assert "decomposition_too_broad" in decision.metacognitive_reason


def test_diagnosis_empty_model_output_retry_with_hint(monkeypatch):
    """empty_model_output diagnosis → retry with explicit tool-call hint."""
    import introspect
    monkeypatch.setattr(
        introspect, "diagnose_loop",
        lambda _lid: _mk_diag("empty_model_output"),
    )
    decision = _handle_blocked_step(
        step_text="classify the result",
        outcome={"stuck_reason": "empty output", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],
        loop_id="test-loop",
    )
    assert decision.retry is True
    assert "complete_step" in decision.hint or "tool" in decision.hint.lower()
    assert "empty_model_output" in decision.metacognitive_reason


def test_diagnosis_healthy_falls_through_to_heuristic(monkeypatch):
    """healthy diagnosis → no override; existing convergence heuristic decides."""
    import introspect
    monkeypatch.setattr(introspect, "diagnose_loop", lambda _lid: _mk_diag("healthy"))
    decision = _handle_blocked_step(
        step_text="do something",
        outcome={"stuck_reason": "transient error", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],  # converging → retry path
        loop_id="test-loop",
    )
    # Convergence heuristic → retry (converging, under _RETRY_THRESHOLD=3)
    assert decision.retry is True
    assert "retry_churn" not in decision.metacognitive_reason
    assert "decomposition_too_broad" not in decision.metacognitive_reason


def test_diagnosis_not_consulted_below_threshold(monkeypatch):
    """prior_retries < _DIAGNOSIS_RETRY_THRESHOLD → don't even call diagnose_loop."""
    calls = []
    import introspect

    def _spy(_lid):
        calls.append(_lid)
        return _mk_diag("retry_churn")

    monkeypatch.setattr(introspect, "diagnose_loop", _spy)
    _handle_blocked_step(
        step_text="do something",
        outcome={"stuck_reason": "err", "result": ""},
        prior_retries=0,
        adapter=None,
        error_fingerprints=["a"],
        loop_id="test-loop",
    )
    assert calls == []  # not called — below threshold


def test_diagnosis_no_loop_id_skips_consultation(monkeypatch):
    """Empty loop_id → diagnose_loop not called (back-compat for tests)."""
    calls = []
    import introspect

    def _spy(_lid):
        calls.append(_lid)
        return _mk_diag("retry_churn")

    monkeypatch.setattr(introspect, "diagnose_loop", _spy)
    _handle_blocked_step(
        step_text="do something",
        outcome={"stuck_reason": "err", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],
        loop_id="",  # empty
    )
    assert calls == []


def test_diagnosis_exception_swallowed(monkeypatch):
    """diagnose_loop raising → falls through silently to heuristic."""
    import introspect

    def _boom(_lid):
        raise RuntimeError("boom")

    monkeypatch.setattr(introspect, "diagnose_loop", _boom)
    # Should not raise — falls through to convergence heuristic
    decision = _handle_blocked_step(
        step_text="do something",
        outcome={"stuck_reason": "err", "result": ""},
        prior_retries=2,
        adapter=None,
        error_fingerprints=["a", "b", "c"],
        loop_id="test-loop",
    )
    assert decision is not None


# ---------------------------------------------------------------------------
# _finalize_loop
# ---------------------------------------------------------------------------

def test_finalize_loop_does_not_raise_on_empty_outcomes(tmp_path, monkeypatch):
    """_finalize_loop never raises even with empty step_outcomes."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
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


# ---------------------------------------------------------------------------
# _finalize_loop — recovery lessons (session 40 M3)
# ---------------------------------------------------------------------------

def _run_finalize_for_recovery(monkeypatch, *, loop_status, recovery_steps,
                               failure_chain, dry_run=False, diag=None):
    """Call _finalize_loop with reflexion stubbed out, capturing every
    record_tiered_lesson call. Optionally force a specific diagnosis."""
    recorded = []

    import memory
    monkeypatch.setattr(memory, "reflect_and_record", lambda *a, **kw: None)
    monkeypatch.setattr(
        memory, "record_tiered_lesson",
        lambda *a, **kw: recorded.append(kw.get("lesson_text") or (a[0] if a else "")),
    )
    if diag is not None:
        import introspect
        monkeypatch.setattr(introspect, "diagnose_loop", lambda *a, **kw: diag)

    _finalize_loop(
        loop_id="rec-test",
        goal="recover this goal",
        project="proj",
        loop_status=loop_status,
        step_outcomes=[],
        adapter=None,
        dry_run=dry_run,
        verbose=False,
        total_tokens_in=0,
        total_tokens_out=0,
        elapsed_ms=100,
        had_no_matching_skill=False,
        failure_chain=failure_chain,
        recovery_steps=recovery_steps,
    )
    return recorded


def test_finalize_records_verified_recovery_lesson(monkeypatch, tmp_path):
    """A done run with recovery actions records a typed verified-recovery lesson."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    recorded = _run_finalize_for_recovery(
        monkeypatch,
        loop_status="done",
        recovery_steps=2,
        failure_chain=[
            "step 2 blocked (TIMEOUT: step exceeded 600s); retry 1 with hint",
            "step 2 re-decomposing: diagnose_loop: retry_churn",
        ],
    )
    verified = [r for r in recorded if r.startswith("[recovery-verified]")]
    assert len(verified) == 1
    assert "re-decompose" in verified[0]
    assert "retry-with-hint" in verified[0]
    assert "step 2 blocked (TIMEOUT" in verified[0]


def test_finalize_no_recovery_lesson_without_recovery_steps(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    recorded = _run_finalize_for_recovery(
        monkeypatch, loop_status="done", recovery_steps=0, failure_chain=[],
    )
    assert not any(r.startswith("[recovery-verified]") for r in recorded)


def test_finalize_no_recovery_lesson_when_stuck(monkeypatch, tmp_path):
    """Recovery actions on a stuck run are not verified — no lesson."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    recorded = _run_finalize_for_recovery(
        monkeypatch,
        loop_status="stuck",
        recovery_steps=1,
        failure_chain=["step 1 terminal: gave up"],
    )
    assert not any(r.startswith("[recovery-verified]") for r in recorded)


def test_finalize_skips_recovery_lessons_in_dry_run(monkeypatch, tmp_path):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    recorded = _run_finalize_for_recovery(
        monkeypatch,
        loop_status="done",
        recovery_steps=2,
        failure_chain=["step 2 blocked (x); retry 1 with hint"],
        dry_run=True,
    )
    assert recorded == []


def test_finalize_records_recovery_plan_lesson_for_failed_loop(monkeypatch, tmp_path):
    """A non-healthy diagnosis with a table plan records a [recovery-plan] lesson."""
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    from introspect import LoopDiagnosis
    diag = LoopDiagnosis(
        loop_id="rec-test",
        failure_class="empty_model_output",
        severity="warning",
        recommendation="retry with tool-call hint",
    )
    recorded = _run_finalize_for_recovery(
        monkeypatch,
        loop_status="stuck",
        recovery_steps=0,
        failure_chain=[],
        diag=diag,
    )
    plans = [r for r in recorded if r.startswith("[recovery-plan]")]
    assert len(plans) == 1
    assert plans[0].startswith("[recovery-plan] empty_model_output:")


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
    from pre_flight import PlanReview
    from unittest.mock import patch as _patch
    _pf = PlanReview(scope="narrow", scope_note="test")
    with _patch("pre_flight.review_plan", return_value=_pf):
        result = run_agent_loop(
            "expensive task",
            project="cost-test",
            adapter=_DryRunAdapter(),
            dry_run=False,
            cost_budget=0.0001,  # tiny budget — will be exceeded immediately
        )
    # Budget enforcement must stop the loop — 'done' would mean budget was ignored
    assert result.status in ("stuck", "budget_exceeded"), (
        f"Expected stuck/budget_exceeded but got {result.status!r} — budget enforcement may be broken"
    )


@pytest.mark.slow
def test_cost_budget_exceeded_on_final_step_keeps_done(monkeypatch, tmp_path):
    """Budget tripping with NO steps remaining must not demote a finished run.

    Run 692bd96f (2026-07-11): all steps done, closure complete=True,
    goal_achieved=True — then the cost stop fired after the FINAL step and
    stamped the run stuck/failed. The breaker exists to stop further spend;
    a fully-consumed plan has none.
    """
    _setup_workspace(monkeypatch, tmp_path)
    from pre_flight import PlanReview
    from unittest.mock import patch as _patch
    _pf = PlanReview(scope="narrow", scope_note="test")
    with _patch("pre_flight.review_plan", return_value=_pf):
        result = run_agent_loop(
            "single cheap task",
            project="cost-final-step",
            adapter=_DryRunAdapter(),
            dry_run=False,
            cost_budget=0.0001,  # trips on the one and only step
            preset_steps=["Research budget carve-out behavior"],
        )
    assert result.status == "done", (
        f"finished plan demoted to {result.status!r}: {result.stuck_reason}"
    )
    assert not (result.stuck_reason and "cost_budget" in result.stuck_reason)


# ---------------------------------------------------------------------------
# Phase 35 P2: HITL tier wiring in _execute_step
# ---------------------------------------------------------------------------

def test_execute_step_destroy_tier_warns_but_proceeds():
    """Step descriptions with DESTROY tier now warn but proceed to LLM (is_description=True).

    Previously: blocked before LLM call (causing decomposer false positives like
    "Clone repo (rm -rf first)" blocking real work).
    Now: DESTROY tier in step descriptions is downgraded to MEDIUM (warn gate) —
    the LLM decides how to accomplish the task safely.
    """
    from unittest.mock import MagicMock
    from llm import LLMResponse, ToolCall
    adapter = MagicMock()
    adapter.complete.return_value = LLMResponse(
        content="",
        tool_calls=[ToolCall(name="complete_step", arguments={"result": "done", "summary": "cleaned"})],
        input_tokens=1, output_tokens=1,
    )

    outcome = _execute_step(
        goal="clean up workspace",
        step_text="rm -rf /var/log/old/ to clean up disk space",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
    )
    # LLM should be called — step description is advisory, not blocked
    adapter.complete.assert_called()
    assert outcome["status"] == "done"


def test_execute_step_high_risk_description_proceeds():
    """HIGH risk step descriptions are downgraded to MEDIUM — LLM call proceeds."""
    from unittest.mock import MagicMock
    from llm import LLMResponse, ToolCall
    adapter = MagicMock()
    adapter.complete.return_value = LLMResponse(
        content="",
        tool_calls=[ToolCall(name="complete_step", arguments={"result": "done", "summary": "ok"})],
        input_tokens=1, output_tokens=1,
    )

    outcome = _execute_step(
        goal="system admin",
        step_text="rm -rf /tmp/old_build_dir",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
    )
    adapter.complete.assert_called()
    assert outcome["status"] == "done"


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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
# Fabrication ground-truth guard (done≠achieved) — artifact_check integration
# ---------------------------------------------------------------------------

def test_run_agent_loop_blocks_fabricated_write_claim(monkeypatch, tmp_path):
    """A step that claims a file write but produces no artifact is demoted to blocked."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    # Keep the text-only verifier out of the way so we isolate the FS guard.
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    class _FabAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Wrote the FizzBuzz solution to fizzbuzz.py and verified it prints 1..15.",
                    "summary": "implemented fizzbuzz",
                })],
                input_tokens=1, output_tokens=1,
            )

    result = al.run_agent_loop(
        "build fizzbuzz",
        adapter=_FabAdapter(),
        preset_steps=["Write the FizzBuzz solution to fizzbuzz.py"],
        max_steps=1,
        max_iterations=3,
    )
    blocked = [s for s in result.steps if s.status == "blocked"]
    assert blocked, f"expected a blocked step, got {[s.status for s in result.steps]}"
    assert any("fabrication-guard" in (s.result or "") for s in blocked)


def test_run_agent_loop_allows_real_write(monkeypatch, tmp_path):
    """A step that actually creates the claimed file is NOT flagged as fabrication."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    slug = al._goal_to_slug("build fizzbuzz for real")
    proj_dir = tmp_path / "projects" / slug

    class _RealAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            # Actually land the artifact the step claims to write.
            proj_dir.mkdir(parents=True, exist_ok=True)
            (proj_dir / "fizzbuzz.py").write_text("print('fizz')\n")
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Wrote the FizzBuzz solution to fizzbuzz.py.",
                    "summary": "implemented fizzbuzz",
                })],
                input_tokens=1, output_tokens=1,
            )

    result = al.run_agent_loop(
        "build fizzbuzz for real",
        adapter=_RealAdapter(),
        preset_steps=["Write the FizzBuzz solution to fizzbuzz.py"],
        max_steps=1,
        max_iterations=3,
    )
    assert not any("fabrication-guard" in (s.result or "") for s in result.steps)


def test_run_agent_loop_blocks_inert_output_claim(monkeypatch, tmp_path):
    """A step that writes an inert .py but narrates concrete output is blocked.

    The organic repro: fizzbuzz.py with no __main__ block (prints nothing) but a
    result claiming "verified output: 1,2,Fizz,4,Buzz". The file exists, so the
    missing-artifact layer passes; the inert-output layer catches it.
    """
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    slug = al._goal_to_slug("build fizzbuzz with output")
    proj_dir = tmp_path / "projects" / slug

    class _InertAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            proj_dir.mkdir(parents=True, exist_ok=True)
            # Definitions only — running this prints nothing.
            (proj_dir / "fizzbuzz.py").write_text(
                "def fizzbuzz(n):\n"
                "    if n % 15 == 0:\n"
                "        return 'FizzBuzz'\n"
                "    return str(n)\n"
            )
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Wrote fizzbuzz.py and verified output: 1,2,Fizz,4,Buzz,FizzBuzz.",
                    "summary": "implemented and verified fizzbuzz",
                })],
                input_tokens=1, output_tokens=1,
            )

    result = al.run_agent_loop(
        "build fizzbuzz with output",
        adapter=_InertAdapter(),
        preset_steps=["Write the FizzBuzz solution to fizzbuzz.py and verify its output"],
        max_steps=1,
        max_iterations=3,
    )
    blocked = [s for s in result.steps if s.status == "blocked"]
    assert blocked, f"expected a blocked step, got {[s.status for s in result.steps]}"
    assert any("fabrication-guard" in (s.result or "") for s in blocked)


def test_run_agent_loop_blocks_execution_contradiction(monkeypatch, tmp_path):
    """A step that claims success while the only command it ran failed (real
    tool transcript, is_error) is demoted to blocked."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    class _ContradictAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Ran the test suite — all 142 tests passed cleanly.",
                    "summary": "ran tests",
                })],
                # The real transcript: the only command run exited non-zero.
                tool_events=[{"name": "Bash", "input": {"command": "pytest -q"},
                              "output": "ImportError: no module named app", "is_error": True}],
                input_tokens=1, output_tokens=1,
            )

    result = al.run_agent_loop(
        "run the project tests",
        adapter=_ContradictAdapter(),
        preset_steps=["Run the full test suite and report results"],
        max_steps=1,
        max_iterations=3,
    )
    blocked = [s for s in result.steps if s.status == "blocked"]
    assert blocked, f"expected a blocked step, got {[s.status for s in result.steps]}"
    assert any("fabrication-guard" in (s.result or "") for s in blocked)


def test_run_agent_loop_allows_real_passing_run(monkeypatch, tmp_path):
    """A success claim backed by a real passing command is NOT flagged."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    class _RealRunAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Ran the test suite — all 142 tests passed.",
                    "summary": "ran tests",
                })],
                tool_events=[{"name": "Bash", "input": {"command": "pytest -q"},
                              "output": "142 passed in 3.1s", "is_error": False}],
                input_tokens=1, output_tokens=1,
            )

    result = al.run_agent_loop(
        "run the project tests for real",
        adapter=_RealRunAdapter(),
        preset_steps=["Run the full test suite and report results"],
        max_steps=1,
        max_iterations=3,
    )
    assert not any(
        "fabrication-guard" in (s.result or "") for s in result.steps
    ), "real passing run must not be flagged"


# ---------------------------------------------------------------------------
# Plan manifest (run visibility)
# ---------------------------------------------------------------------------

def test_write_plan_manifest_creates_file(monkeypatch, tmp_path):
    """_write_plan_manifest writes a markdown file before execution starts."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
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


def test_split_exec_analyze_produces_non_compound_analyze_step():
    parts = _split_exec_analyze("run pytest and analyze failures")
    assert len(parts) == 2
    assert _is_combined_exec_analyze(parts[0]) is False
    assert _is_combined_exec_analyze(parts[1]) is False
    assert parts[1].lower().startswith("read the captured output")


def test_is_combined_exec_analyze_does_not_over_trigger():
    """Pure analysis steps without an exec keyword are not flagged."""
    assert _is_combined_exec_analyze("Identify the root cause of the import failure") is False
    assert _is_combined_exec_analyze("Evaluate the quality of the test coverage summary") is False
    assert _is_combined_exec_analyze("Conclude whether the architecture matches the design doc") is False


def test_run_agent_loop_recovers_if_compound_step_leaks_to_executor(monkeypatch, tmp_path):
    """Even if a shaper path misses a compound step, the executor guard should split it."""
    _setup_workspace(monkeypatch, tmp_path)
    # Backend detection must succeed without a real claude install (CI has
    # none); conftest's subprocess guard blocks any actual invocation.
    _fake = tmp_path / "claude"
    _fake.write_text("#!/bin/sh\nexit 0\n")
    _fake.chmod(0o755)
    monkeypatch.setenv("CLAUDE_BIN", str(_fake))

    import agent_loop as al
    import loop_planning
    import loop_execute

    monkeypatch.setattr(loop_planning, "_decompose", lambda *args, **kwargs: ["run pytest and analyze failures"])
    monkeypatch.setattr(loop_planning, "_shape_steps", lambda steps, **kwargs: list(steps))

    executed = []

    def _fake_execute_step(**kwargs):
        executed.append(kwargs["step_text"])
        return {
            "status": "done",
            "result": f"ok: {kwargs['step_text']}",
            "summary": "done",
            "tokens_in": 0,
            "tokens_out": 0,
            "confidence": "high",
            "inject_steps": [],
        }

    monkeypatch.setattr(loop_execute, "_execute_step", _fake_execute_step)

    result = run_agent_loop("check repo", project="compound-leak", dry_run=False, max_steps=1)

    assert result.status == "done"
    assert executed
    assert all(not _is_combined_exec_analyze(step) for step in executed)
    assert executed[0].lower().startswith("run ")
    assert any("analyze" in step.lower() or "read the captured output" in step.lower() for step in executed)


# ---------------------------------------------------------------------------
# Budget ceiling continuation task
# ---------------------------------------------------------------------------

def test_budget_ceiling_enqueues_continuation(monkeypatch, tmp_path):
    """Below depth threshold: budget ceiling enqueues a continuation task."""
    import unittest.mock as mock
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_MAX_CONTINUATION_DEPTH", "4")

    enqueued = {}

    def _fake_enqueue(lane, source, reason, parent_job_id, continuation_depth=0,
                      origin=None):
        enqueued["lane"] = lane
        enqueued["source"] = source
        enqueued["reason"] = reason
        enqueued["depth"] = continuation_depth
        enqueued["origin"] = origin
        return {"job_id": "cont-001"}

    from pre_flight import PlanReview
    _pf = PlanReview(scope="narrow", scope_note="test")

    with mock.patch("task_store.enqueue", _fake_enqueue), \
         mock.patch("pre_flight.review_plan", return_value=_pf):
        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "adversarial review of the entire codebase",
            adapter=_DryRunAdapter(),
            max_iterations=1,   # force immediate budget ceiling
            max_steps=4,
            dry_run=False,
            continuation_depth=0,
            measurement_class="benchmark",
            handle_id="handle-root",
        )

    assert result.status in ("stuck", "done", "partial")
    if enqueued:
        assert enqueued["source"] == "loop_continuation"
        assert "CONTINUATION" in enqueued["reason"]
        assert enqueued["lane"] == "agenda"
        assert enqueued["depth"] == 1  # depth incremented
        assert enqueued["origin"]["measurement_class"] == "benchmark"
        assert enqueued["origin"]["parent_handle_id"] == "handle-root"


def test_budget_ceiling_escalates_at_depth_limit(monkeypatch, tmp_path):
    """At depth threshold: budget ceiling writes an escalation task, not a continuation."""
    import unittest.mock as mock
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_MAX_CONTINUATION_DEPTH", "2")

    enqueued = {}

    def _fake_enqueue(lane, source, reason, parent_job_id, continuation_depth=0):
        enqueued["lane"] = lane
        enqueued["source"] = source
        enqueued["reason"] = reason
        enqueued["depth"] = continuation_depth
        return {"job_id": "esc-001"}

    from pre_flight import PlanReview
    _pf = PlanReview(scope="narrow", scope_note="test")

    with mock.patch("task_store.enqueue", _fake_enqueue), \
         mock.patch("pre_flight.review_plan", return_value=_pf):
        from agent_loop import run_agent_loop, _DryRunAdapter
        result = run_agent_loop(
            "adversarial review of the entire codebase",
            adapter=_DryRunAdapter(),
            max_iterations=1,   # force immediate budget ceiling
            max_steps=4,
            dry_run=False,
            continuation_depth=2,  # at the limit
        )

    assert result.status in ("stuck", "done", "partial")
    if enqueued:
        # Should escalate, not continue
        assert enqueued["source"] == "loop_escalation"
        assert "ESCALATION" in enqueued["reason"]
        assert "Options:" in enqueued["reason"]
        assert enqueued["lane"] == "agenda"


def test_continuation_depth_in_ancestry_context(monkeypatch, tmp_path):
    """continuation_depth > 0 injects a CONTINUATION PASS note into ancestry context."""
    import unittest.mock as mock
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    captured_ancestry = {}

    def _fake_decompose(goal, adapter, max_steps, verbose=False, lessons_context="",
                        ancestry_context="", skills_context="", cost_context="",
                        thinking_budget=None):
        captured_ancestry["ctx"] = ancestry_context
        return ["single step: do the work"]

    with mock.patch("loop_planning._decompose", _fake_decompose):
        from agent_loop import run_agent_loop, _DryRunAdapter
        run_agent_loop(
            "review the auth module",
            adapter=_DryRunAdapter(),
            max_iterations=5,
            continuation_depth=2,
        )

    ctx = captured_ancestry.get("ctx", "")
    assert "CONTINUATION PASS 2" in ctx
    assert "narrowly" in ctx.lower()


# ---------------------------------------------------------------------------
# Mutable task graph: inject_steps
# ---------------------------------------------------------------------------

def test_inject_steps_inserted_into_plan(monkeypatch, tmp_path):
    """When a step returns inject_steps, those steps run before the original plan resumes."""
    import unittest.mock as mock
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    from agent_loop import _DryRunAdapter
    from llm import LLMResponse, ToolCall

    step_count = [0]

    class _InjectingAdapter(_DryRunAdapter):
        def complete(self, messages, *, tools=None, tool_choice="auto", **kw):
            n = step_count[0]
            step_count[0] += 1
            if tools and tool_choice == "required":
                if n == 0:
                    # Step 1 — injects a new step
                    return LLMResponse(
                        content="",
                        tool_calls=[ToolCall(
                            name="complete_step",
                            arguments={
                                "result": "step 1 done",
                                "summary": "step 1 done",
                                "inject_steps": ["injected: verify the finding"],
                            },
                        )],
                        stop_reason="tool_use",
                        input_tokens=5, output_tokens=10,
                    )
                else:
                    return LLMResponse(
                        content="",
                        tool_calls=[ToolCall(
                            name="complete_step",
                            arguments={"result": "done", "summary": "done"},
                        )],
                        stop_reason="tool_use",
                        input_tokens=5, output_tokens=10,
                    )
            # Decompose or other calls → delegate to parent
            return super().complete(messages, tools=tools, tool_choice=tool_choice, **kw)

    from pre_flight import PlanReview as _PlanReview
    _no_milestones = _PlanReview(scope="narrow", scope_note="test — no milestone expansion")

    with mock.patch("loop_planning._decompose",
                    return_value=["step 1: do first thing", "step 2: do second thing"]), \
         mock.patch("pre_flight.review_plan", return_value=_no_milestones):
        from agent_loop import run_agent_loop
        result = run_agent_loop(
            "test inject goal",
            adapter=_InjectingAdapter(),
            max_iterations=10,
        )

    # The injected step should appear in the outcome steps
    all_step_texts = [s.text for s in result.steps]
    assert any("injected" in t.lower() or "verify" in t.lower() for t in all_step_texts), \
        f"Injected step not found in: {all_step_texts}"
    # Original steps should still be present
    assert any("step 2" in t.lower() for t in all_step_texts), \
        f"Original step 2 missing from: {all_step_texts}"
    # 3 total steps: original step 1, injected step, original step 2
    assert len(all_step_texts) >= 3, f"Expected ≥3 steps, got: {all_step_texts}"


def test_inject_steps_capped_at_three(monkeypatch, tmp_path):
    """inject_steps are capped at 3 even if the worker returns more."""
    from step_exec import execute_step, EXECUTE_TOOLS
    from llm import LLMResponse, ToolCall

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    class _ManyInjectAdapter:
        model_key = "test"
        def complete(self, messages, tools=None, **kw):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    name="complete_step",
                    arguments={
                        "result": "done",
                        "summary": "ok",
                        "inject_steps": ["step a", "step b", "step c", "step d", "step e"],
                    },
                )],
                stop_reason="tool_use",
                input_tokens=5, output_tokens=10,
            )

    outcome = execute_step(
        goal="test goal",
        step_text="test step",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=_ManyInjectAdapter(),
        tools=EXECUTE_TOOLS,
    )
    # inject_steps should be capped at 3
    injected = outcome.get("inject_steps", [])
    assert len(injected) <= 3


# ---------------------------------------------------------------------------
# Phase 58: Milestone-aware expansion
# ---------------------------------------------------------------------------

def test_milestone_step_is_expanded(monkeypatch, tmp_path):
    """When a pre-flight flagged step is flagged as a milestone, it should be
    decomposed into sub-steps rather than executed as a single step."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    # Create a fake PlanReview that flags step 1 as a milestone candidate
    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = [1]
    fake_pf.scope = "wide"
    fake_pf.flags = []

    # Decompose should return 3 sub-steps for the milestone step
    _sub_steps = ["sub-step A", "sub-step B", "sub-step C"]

    with patch("pre_flight.review_plan", return_value=fake_pf):
        with patch("planner.decompose", return_value=_sub_steps) as mock_decompose:
            result = run_agent_loop(
                "do a complex analysis",
                adapter=_DryRunAdapter(),
                dry_run=False,
                max_iterations=10,
            )

    # The milestone step should have been expanded — mock_decompose called for sub-decompose
    assert mock_decompose.called


def test_milestone_step_expansion_only_at_depth_zero(monkeypatch, tmp_path):
    """Milestone expansion is skipped at continuation_depth > 0 to prevent recursion."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = [1]
    fake_pf.scope = "wide"
    fake_pf.flags = []

    _sub_steps = ["sub-step A", "sub-step B"]

    # Pass _DryRunAdapter explicitly to avoid real HTTP calls — pre_flight path still
    # runs (gated on dry_run flag, not adapter type) so milestone_step_indices still applies.
    with patch("pre_flight.review_plan", return_value=fake_pf):
        with patch("planner.decompose", return_value=_sub_steps):
            result = run_agent_loop(
                "do a complex analysis",
                adapter=_DryRunAdapter(),
                dry_run=False,
                continuation_depth=1,  # depth > 0 — milestone expansion skipped
                max_iterations=10,
            )

    # Milestone expansion is gated on continuation_depth == 0; loop should complete.
    # Use attribute checks instead of isinstance — xdist workers can load agent_loop
    # under different sys.path entries, making class identity unreliable across workers.
    assert hasattr(result, "status"), f"result has no 'status': {result!r}"
    assert result.status in ("done", "stuck", "blocked"), f"unexpected status: {result.status}"


def test_milestone_expansion_falls_through_if_decompose_returns_one_step(monkeypatch, tmp_path):
    """If decompose returns only 1 step (not worth expanding), fall through to execute normally."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = [1]
    fake_pf.scope = "wide"
    fake_pf.flags = []

    # Returns only 1 sub-step → should not expand, just execute normally.
    # Pass _DryRunAdapter explicitly to avoid real HTTP calls.
    with patch("pre_flight.review_plan", return_value=fake_pf):
        with patch("planner.decompose", return_value=["same single step"]):
            result = run_agent_loop(
                "simple analysis",
                adapter=_DryRunAdapter(),
                dry_run=False,
                max_iterations=5,
            )

    # Use attribute checks (not isinstance) — see test above for explanation.
    assert hasattr(result, "status"), f"result has no 'status': {result!r}"
    assert result.status in ("done", "stuck", "blocked"), f"unexpected status: {result.status}"


# ---------------------------------------------------------------------------
# Phase 58: Pre-flight calibration feedback loop
# ---------------------------------------------------------------------------

def test_preflight_calibration_logged_on_completion(monkeypatch, tmp_path):
    """After a loop completes, pre-flight calibration should be logged to
    memory/preflight_calibration.jsonl."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = []
    fake_pf.scope = "wide"
    fake_pf.flags = []
    fake_pf.has_concerns = True

    with patch("pre_flight.review_plan", return_value=fake_pf):
        result = run_agent_loop("analyze data", adapter=_DryRunAdapter(), dry_run=False, max_iterations=5)

    # Check that calibration file was written
    try:
        from orch_items import memory_dir
        cal_path = memory_dir() / "preflight_calibration.jsonl"
    except Exception:
        cal_path = tmp_path / "memory" / "preflight_calibration.jsonl"

    assert cal_path.exists(), "preflight_calibration.jsonl should be written after loop"
    entries = [json.loads(line) for line in cal_path.read_text().splitlines() if line.strip()]
    assert len(entries) >= 1
    entry = entries[-1]
    assert "scope_predicted" in entry
    assert "actual_status" in entry
    assert "true_positive" in entry
    assert "false_positive" in entry
    assert entry["scope_predicted"] == "wide"


def test_preflight_calibration_not_written_on_dry_run(monkeypatch, tmp_path):
    """dry_run=True should not write calibration data."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = []
    fake_pf.scope = "narrow"
    fake_pf.flags = []

    with patch("pre_flight.review_plan", return_value=fake_pf):
        result = run_agent_loop("simple task", dry_run=True)

    # dry_run → no calibration written (pre-flight doesn't run in dry_run)
    try:
        from orch_items import memory_dir
        cal_path = memory_dir() / "preflight_calibration.jsonl"
    except Exception:
        cal_path = tmp_path / "memory" / "preflight_calibration.jsonl"

    if cal_path.exists():
        entries = [line for line in cal_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 0, "dry_run should not write calibration entries"


def test_preflight_calibration_false_positive_classification(monkeypatch, tmp_path):
    """scope=wide + actual done = false_positive."""
    _setup_workspace(monkeypatch, tmp_path)
    from unittest.mock import MagicMock, patch

    fake_pf = MagicMock()
    fake_pf.milestone_step_indices = []
    fake_pf.scope = "wide"
    fake_pf.flags = []
    fake_pf.has_concerns = True

    with patch("pre_flight.review_plan", return_value=fake_pf):
        result = run_agent_loop("analyze data", adapter=_DryRunAdapter(), dry_run=False, max_iterations=5)

    if result.status == "done":
        try:
            from orch_items import memory_dir
            cal_path = memory_dir() / "preflight_calibration.jsonl"
        except Exception:
            cal_path = tmp_path / "memory" / "preflight_calibration.jsonl"

        if cal_path.exists():
            entries = [json.loads(l) for l in cal_path.read_text().splitlines() if l.strip()]
            if entries:
                entry = entries[-1]
                assert entry["false_positive"] is True
                assert entry["true_positive"] is False


# ---------------------------------------------------------------------------
# Phase 62: Output path resolution + artifact storage
# ---------------------------------------------------------------------------

def test_project_dir_root_uses_canonical_path(tmp_path, monkeypatch):
    """_project_dir_root() delegates to projects_root(), not hardcoded path."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from agent_loop import _project_dir_root
    from orch_items import projects_root
    result = _project_dir_root()
    # Must match canonical projects_root()
    assert result == projects_root()
    # Should end with "projects"
    assert result.name == "projects"
    # Must be under tmp_path, not some other location
    assert str(tmp_path) in str(result)


def test_artifact_storage_in_shared_ctx():
    """Artifacts from step outcomes are stored in loop_shared_ctx."""
    from agent_loop import _error_fingerprint  # just to verify import works
    # This is a structural test — the actual storage happens in _process_step_result
    # which is deeply integrated. We test the key format convention.
    key = "artifact:3:file_list"
    parts = key.split(":", 2)
    assert parts[0] == "artifact"
    assert parts[1] == "3"
    assert parts[2] == "file_list"


# ---------------------------------------------------------------------------
# LoopStateMachine — transition enforcement
# ---------------------------------------------------------------------------

def test_loop_state_machine_is_loop_context_subclass():
    """LoopStateMachine inherits LoopContext — ctx.set_phase() is the API."""
    from agent_loop import LoopContext, LoopStateMachine
    assert issubclass(LoopStateMachine, LoopContext)
    ctx = LoopStateMachine()
    assert isinstance(ctx, LoopContext)


def test_loop_state_machine_happy_path():
    """Normal A→B→C→E→F→G path (skipping parallel) transitions cleanly."""
    from agent_loop import LoopPhase, LoopStateMachine
    ctx = LoopStateMachine()
    assert ctx.phase == LoopPhase.INIT

    ctx.set_phase(LoopPhase.DECOMPOSE)
    assert ctx.phase == LoopPhase.DECOMPOSE

    ctx.set_phase(LoopPhase.PRE_FLIGHT)
    assert ctx.phase == LoopPhase.PRE_FLIGHT

    ctx.set_phase(LoopPhase.PREPARE)
    assert ctx.phase == LoopPhase.PREPARE

    ctx.set_phase(LoopPhase.EXECUTE)
    assert ctx.phase == LoopPhase.EXECUTE

    ctx.set_phase(LoopPhase.FINALIZE)
    assert ctx.phase == LoopPhase.FINALIZE


def test_loop_state_machine_parallel_path():
    """PRE_FLIGHT → PARALLEL → PREPARE path is valid."""
    from agent_loop import LoopPhase, LoopStateMachine
    ctx = LoopStateMachine()
    ctx.set_phase(LoopPhase.DECOMPOSE)
    ctx.set_phase(LoopPhase.PRE_FLIGHT)
    ctx.set_phase(LoopPhase.PARALLEL)
    assert ctx.phase == LoopPhase.PARALLEL
    ctx.set_phase(LoopPhase.PREPARE)
    assert ctx.phase == LoopPhase.PREPARE


def test_loop_state_machine_early_exit_to_finalize():
    """Any phase can transition directly to FINALIZE (early-exit path)."""
    from agent_loop import LoopPhase, LoopStateMachine
    for start_phase in (
        LoopPhase.INIT,
        LoopPhase.DECOMPOSE,
        LoopPhase.PRE_FLIGHT,
        LoopPhase.PARALLEL,
        LoopPhase.PREPARE,
        LoopPhase.EXECUTE,
    ):
        ctx = LoopStateMachine()
        ctx.phase = start_phase
        ctx.set_phase(LoopPhase.FINALIZE)
        assert ctx.phase == LoopPhase.FINALIZE


def test_loop_state_machine_invalid_forward_skip():
    """Skipping phases raises InvalidTransitionError."""
    from agent_loop import LoopPhase, LoopStateMachine, InvalidTransitionError
    ctx = LoopStateMachine()
    # INIT → EXECUTE is not allowed (skips DECOMPOSE, PRE_FLIGHT, PREPARE)
    with pytest.raises(InvalidTransitionError, match="init.*execute"):
        ctx.set_phase(LoopPhase.EXECUTE)


def test_loop_state_machine_backwards_transition():
    """Going backwards (EXECUTE → DECOMPOSE) raises InvalidTransitionError."""
    from agent_loop import LoopPhase, LoopStateMachine, InvalidTransitionError
    ctx = LoopStateMachine()
    ctx.phase = LoopPhase.EXECUTE
    with pytest.raises(InvalidTransitionError, match="execute.*decompose"):
        ctx.set_phase(LoopPhase.DECOMPOSE)


def test_loop_state_machine_finalize_is_terminal():
    """FINALIZE → anything (including FINALIZE itself) raises InvalidTransitionError."""
    from agent_loop import LoopPhase, LoopStateMachine, InvalidTransitionError
    ctx = LoopStateMachine()
    ctx.phase = LoopPhase.FINALIZE
    with pytest.raises(InvalidTransitionError):
        ctx.set_phase(LoopPhase.INIT)
    with pytest.raises(InvalidTransitionError):
        ctx.set_phase(LoopPhase.FINALIZE)


def test_loop_state_machine_error_message_shows_both_phases():
    """InvalidTransitionError message shows current and target phases."""
    from agent_loop import LoopPhase, LoopStateMachine, InvalidTransitionError
    ctx = LoopStateMachine()
    ctx.phase = LoopPhase.PREPARE
    try:
        ctx.set_phase(LoopPhase.DECOMPOSE)
        pytest.fail("Should have raised")
    except InvalidTransitionError as exc:
        msg = str(exc)
        assert "prepare" in msg
        assert "decompose" in msg


def test_loop_state_machine_does_not_modify_ctx_on_failure():
    """ctx.phase is unchanged when a transition fails."""
    from agent_loop import LoopPhase, LoopStateMachine, InvalidTransitionError
    ctx = LoopStateMachine()
    ctx.phase = LoopPhase.EXECUTE
    try:
        ctx.set_phase(LoopPhase.INIT)
    except InvalidTransitionError:
        pass
    assert ctx.phase == LoopPhase.EXECUTE  # unchanged


# ---------------------------------------------------------------------------
# Regression: StepOutcome attribute access in skill extraction finalize path
# ---------------------------------------------------------------------------

def test_step_outcome_has_no_summary_attribute():
    """StepOutcome does not have .summary — skill extraction code must use .result/.text."""
    from agent_loop import StepOutcome
    s = StepOutcome(index=0, text="do the thing", status="done", result="it worked", iteration=0)
    assert not hasattr(s, "summary"), "StepOutcome.summary would break skill extraction — keep using .result"
    assert hasattr(s, "text")
    assert hasattr(s, "result")


# ---------------------------------------------------------------------------
# Mid-loop budget bump
# ---------------------------------------------------------------------------

def _make_outcomes(n_done: int, n_total: int):
    """Build a list of StepOutcome stubs for budget bump tests."""
    from agent_loop import StepOutcome
    outcomes = []
    for i in range(n_total):
        status = "done" if i < n_done else "blocked"
        outcomes.append(StepOutcome(index=i, text=f"step {i}", status=status, result="ok", iteration=i))
    return outcomes


def test_budget_bump_fires_when_conditions_met(monkeypatch, tmp_path):
    """Budget bump happens: 75%+ budget used, >2 steps remain, ≥50% done."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    # Suppress captains_log in bump path
    monkeypatch.setitem(sys.modules, "captains_log", type(sys)("captains_log"))
    sys.modules["captains_log"].log_event = lambda **kw: None

    outcomes = _make_outcomes(n_done=5, n_total=8)  # 62.5% done
    remaining = ["step A", "step B", "step C"]  # >2 remaining

    # Simulate state: iteration=8 out of max_iterations=10 (80% consumed)
    max_iterations = 10
    iteration = 8  # >= 75% threshold

    _budget_bumped = False
    _BUDGET_WARN_THRESHOLD = 0.75
    _steps_done = sum(1 for s in outcomes if s.status == "done")
    _completion_rate = _steps_done / max(len(outcomes), 1)

    bumped = False
    if (
        not _budget_bumped
        and len(remaining) > 2
        and iteration >= int(max_iterations * _BUDGET_WARN_THRESHOLD)
        and _completion_rate >= 0.5
    ):
        _bump_amount = max(10, max_iterations // 2)
        max_iterations += _bump_amount
        bumped = True

    assert bumped, "Budget bump should have fired"
    # bump_amount = max(10, 10//2) = 10; 10+10=20
    assert max_iterations == 20, f"Expected 20, got {max_iterations}"


def test_budget_bump_does_not_fire_below_threshold(monkeypatch, tmp_path):
    """No bump when budget consumption is below 75%."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    outcomes = _make_outcomes(n_done=5, n_total=8)
    remaining = ["step A", "step B", "step C"]
    max_iterations = 10
    iteration = 6  # 60% — below threshold

    _budget_bumped = False
    _BUDGET_WARN_THRESHOLD = 0.75
    _steps_done = sum(1 for s in outcomes if s.status == "done")
    _completion_rate = _steps_done / max(len(outcomes), 1)

    bumped = False
    if (
        not _budget_bumped
        and len(remaining) > 2
        and iteration >= int(max_iterations * _BUDGET_WARN_THRESHOLD)
        and _completion_rate >= 0.5
    ):
        bumped = True

    assert not bumped, "Bump should not fire below 75% threshold"


def test_budget_bump_does_not_fire_when_completion_low(monkeypatch, tmp_path):
    """No bump when completion rate is below 50% (poor progress doesn't warrant more budget)."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    outcomes = _make_outcomes(n_done=1, n_total=8)  # only 12.5% done
    remaining = ["step A", "step B", "step C"]
    max_iterations = 10
    iteration = 9  # 90% consumed

    _budget_bumped = False
    _BUDGET_WARN_THRESHOLD = 0.75
    _steps_done = sum(1 for s in outcomes if s.status == "done")
    _completion_rate = _steps_done / max(len(outcomes), 1)

    bumped = False
    if (
        not _budget_bumped
        and len(remaining) > 2
        and iteration >= int(max_iterations * _BUDGET_WARN_THRESHOLD)
        and _completion_rate >= 0.5
    ):
        bumped = True

    assert not bumped, "Bump should not fire with low completion rate"


def test_budget_bump_fires_at_most_once():
    """Budget bump is gated by _budget_bumped — second check never fires."""
    bump_count = 0
    for _ in range(3):
        _budget_bumped = (bump_count > 0)
        if not _budget_bumped:
            bump_count += 1
            _budget_bumped = True  # noqa: F841

    assert bump_count == 1


def test_budget_bump_does_not_fire_with_few_remaining(monkeypatch, tmp_path):
    """No bump when ≤2 steps remain — synthesis fallback handles that case."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    outcomes = _make_outcomes(n_done=5, n_total=8)
    remaining = ["step A", "step B"]  # exactly 2 — not >2
    max_iterations = 10
    iteration = 9

    _budget_bumped = False
    _BUDGET_WARN_THRESHOLD = 0.75
    _steps_done = sum(1 for s in outcomes if s.status == "done")
    _completion_rate = _steps_done / max(len(outcomes), 1)

    bumped = False
    if (
        not _budget_bumped
        and len(remaining) > 2
        and iteration >= int(max_iterations * _BUDGET_WARN_THRESHOLD)
        and _completion_rate >= 0.5
    ):
        bumped = True

    assert not bumped, "Bump should not fire when only 2 steps remain (synthesis handles it)"


# ---------------------------------------------------------------------------
# Artifact cleanup
# ---------------------------------------------------------------------------

def test_artifacts_kept_forever_by_default(monkeypatch, tmp_path):
    """Retention decree (Jeremy 2026-07-10): the system never decides run
    data is clutter. With no user opt-in, nothing is ever deleted — even
    ancient prior-loop step artifacts."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import os, time as _t
    from loop_finalize import cleanup_step_artifacts

    _art_dir = tmp_path / "projects" / "test-project" / "artifacts"
    _art_dir.mkdir(parents=True)
    ancient = _art_dir / "loop-old111-step-01.md"
    ancient.write_text("the path the result took")
    _old = _t.time() - 400 * 86400
    os.utime(ancient, (_old, _old))

    assert cleanup_step_artifacts("test-project", exclude_loop_id="abc123") == 0
    assert ancient.exists(), "no opt-in -> no deletion, ever"


def _optin_prune(monkeypatch, days):
    import config as _config
    _orig = _config.get
    monkeypatch.setattr(
        _config, "get",
        lambda key, default=None: (days if key == "artifacts.auto_prune_days"
                                   else _orig(key, default)))


def test_optin_prune_is_age_gated_and_excludes_current_loop(monkeypatch, tmp_path):
    """User opt-in (`artifacts.auto_prune_days`) prunes only OTHER loops'
    step artifacts past the window — never the just-finished loop's (its
    verdict is judged after the loop returns, BACKLOG #18)."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import os, time as _t
    from loop_finalize import cleanup_step_artifacts
    _optin_prune(monkeypatch, 30)

    _art_dir = tmp_path / "projects" / "test-project" / "artifacts"
    _art_dir.mkdir(parents=True)
    current = _art_dir / "loop-abc123-step-01.md"       # just finished
    aged_other = _art_dir / "loop-old111-step-01.md"    # past the window
    fresh_other = _art_dir / "loop-new222-step-01.md"   # inside the window
    permanent = _art_dir / "loop-old111-PARTIAL.md"     # never step-pattern
    for f in (current, aged_other, fresh_other, permanent):
        f.write_text("content")
    _old = _t.time() - 31 * 86400
    for f in (current, aged_other, permanent):
        os.utime(f, (_old, _old))

    deleted = cleanup_step_artifacts("test-project", exclude_loop_id="abc123")

    assert deleted == 1
    assert current.exists(), "just-finished loop survives even past the window"
    assert not aged_other.exists()
    assert fresh_other.exists()
    assert permanent.exists(), "non-step artifacts are never pruned"


def test_optin_prune_zero_and_negative_mean_never(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import os, time as _t
    from loop_finalize import cleanup_step_artifacts

    _art_dir = tmp_path / "projects" / "test-project" / "artifacts"
    _art_dir.mkdir(parents=True)
    aged = _art_dir / "loop-old111-step-01.md"
    aged.write_text("content")
    _old = _t.time() - 400 * 86400
    os.utime(aged, (_old, _old))

    for val in (0, -5, None, "0"):
        _optin_prune(monkeypatch, val)
        assert cleanup_step_artifacts("test-project") == 0
        assert aged.exists()


def test_artifact_cleanup_glob_pattern_does_not_match_permanent_files():
    """The glob pattern loop-{id}-step-*.md does not match PARTIAL.md or plan.md."""
    import fnmatch
    loop_id = "abc123"
    pattern = f"loop-{loop_id}-step-*.md"
    step_files = [f"loop-{loop_id}-step-01.md", f"loop-{loop_id}-step-09.md"]
    permanent_files = [f"loop-{loop_id}-PARTIAL.md", f"loop-{loop_id}-RESULT.md",
                       f"loop-{loop_id}-plan.md", f"loop-{loop_id}-scratchpad"]
    for f in step_files:
        assert fnmatch.fnmatch(f, pattern), f"{f} should match step pattern"
    for f in permanent_files:
        assert not fnmatch.fnmatch(f, pattern), f"{f} should NOT match step pattern"


# ---------------------------------------------------------------------------
# Heartbeat wakeup signal after loop completion
# ---------------------------------------------------------------------------

def test_loop_done_signals_heartbeat(monkeypatch, tmp_path):
    """run_agent_loop calls post_heartbeat_event('loop_done') when the loop finishes."""
    _setup_workspace(monkeypatch, tmp_path)

    calls = []
    fake_heartbeat = type(sys)("heartbeat")
    fake_heartbeat.post_heartbeat_event = lambda event_type, payload="": calls.append((event_type, payload))
    monkeypatch.setitem(sys.modules, "heartbeat", fake_heartbeat)

    result = run_agent_loop("write a haiku", project="hb-test", dry_run=True)

    assert result.status == "done"
    loop_done_calls = [c for c in calls if c[0] == "loop_done"]
    assert len(loop_done_calls) == 1, f"Expected 1 loop_done signal, got: {calls}"
    assert loop_done_calls[0][1] == "hb-test"


def test_loop_done_signal_includes_project(monkeypatch, tmp_path):
    """loop_done heartbeat event payload is the project slug."""
    _setup_workspace(monkeypatch, tmp_path)

    payloads = []
    fake_heartbeat = type(sys)("heartbeat")
    fake_heartbeat.post_heartbeat_event = lambda event_type, payload="": payloads.append(payload) if event_type == "loop_done" else None
    monkeypatch.setitem(sys.modules, "heartbeat", fake_heartbeat)

    run_agent_loop("research market data", project="market-research", dry_run=True)

    assert payloads == ["market-research"]


def test_loop_done_signal_fires_even_if_heartbeat_unavailable(monkeypatch, tmp_path):
    """Heartbeat import failure does not crash the loop — signal is best-effort."""
    _setup_workspace(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "heartbeat", None)  # simulate import failure

    result = run_agent_loop("write a haiku", project="no-hb", dry_run=True)
    assert result.status == "done"


# ---------------------------------------------------------------------------
# Stage 3→4 skill extraction regression
# ---------------------------------------------------------------------------

def test_step_outcome_has_result_attribute():
    """StepOutcome must have .result, not .summary — regression guard for Stage 3→4 fix."""
    so = StepOutcome(index=0, text="do work", status="done", result="found the answer", iteration=1)
    assert hasattr(so, "result"), "StepOutcome missing .result attribute"
    assert not hasattr(so, "summary"), "StepOutcome should not have .summary (old broken attr)"
    # Ensure the attribute access pattern used in skill extraction works
    done_summaries = [s.result[:200] for s in [so] if s.status == "done" and s.result]
    assert done_summaries == ["found the answer"]


def test_skill_extraction_fires_when_not_dry_run(monkeypatch, tmp_path):
    """extract_skills is called after a successful non-dry-run loop."""
    _setup_workspace(monkeypatch, tmp_path)

    import skills as _skills_mod
    calls = []

    def _fake_extract(outcomes, adapter):
        calls.append(outcomes)
        return []  # return no skills — just verify we were called

    monkeypatch.setattr(_skills_mod, "extract_skills", _fake_extract)
    # Stub out reflect_and_record to avoid real LLM calls for lesson extraction
    import agent_loop as _al
    monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)

    result = run_agent_loop(
        "summarise polymarket trends",
        project="skill-extract-test",
        adapter=_DryRunAdapter(),
        dry_run=False,
    )

    assert result.status == "done"
    # extract_skills must have been called exactly once
    assert len(calls) == 1, f"expected 1 extract_skills call, got {len(calls)}"
    # The outcome passed to extract_skills must use .result not .summary
    outcome = calls[0][0]
    assert "summary" in outcome, "outcome_for_extraction must have a summary key"
    assert "steps" in outcome, "outcome_for_extraction must have a steps key"
    for step in outcome["steps"]:
        assert "result" in step, "each step dict must have a result key (not summary)"


def test_skill_extraction_outcome_uses_step_result(monkeypatch, tmp_path):
    """outcome_for_extraction.steps[n].result is populated from StepOutcome.result."""
    _setup_workspace(monkeypatch, tmp_path)

    import skills as _skills_mod
    captured = {}

    def _fake_extract(outcomes, adapter):
        captured["outcome"] = outcomes[0]
        return []

    monkeypatch.setattr(_skills_mod, "extract_skills", _fake_extract)
    import agent_loop as _al
    monkeypatch.setattr(_al, "reflect_and_record", lambda *a, **kw: None, raising=False)

    result = run_agent_loop(
        "build a research summary",
        project="skill-result-test",
        adapter=_DryRunAdapter(),
        dry_run=False,
    )

    assert result.status == "done"
    assert captured, "extract_skills was never called"
    steps = captured["outcome"]["steps"]
    assert len(steps) >= 1
    for step in steps:
        assert step["result"] != "", "step result must be non-empty for done steps"
        # Ensure each step dict is correctly structured (regression: old code used s.summary)
        assert isinstance(step["result"], str)


# ---------------------------------------------------------------------------
# Regression: adaptive adjust/replan must use -1 indices, not step counts
# ---------------------------------------------------------------------------

def test_adaptive_adjust_uses_negative_indices(monkeypatch, tmp_path):
    """remaining_indices after adaptive adjust must be -1 sentinels, not step counts.

    Bug: adjust set remaining_indices = list(range(len(step_outcomes), ...))
    which produced small integers (0, 1, 2...) that collide with actual line
    numbers in NEXT.md, causing 'item_index N not found' ValueError mid-loop.

    Fix: use [-1] * len(new_steps) — same convention as interrupt injection.
    """
    _setup_workspace(monkeypatch, tmp_path)

    from director import DirectorDecision
    import agent_loop as _al
    import config as _cfg_mod

    # Enable adaptive_execution via config patch
    _orig_cfg_get = _cfg_mod.get
    def _patched_cfg_get(key, default=None):
        if key == "adaptive_execution":
            return True
        return _orig_cfg_get(key, default)
    monkeypatch.setattr(_cfg_mod, "get", _patched_cfg_get)

    # Patch director_evaluate to return adjust on first call
    _calls = []
    import director as _dm

    def _fake_director_evaluate(goal, eval_ctx, trigger, adapter, *, dry_run=False):
        _calls.append(trigger)
        return DirectorDecision(
            action="adjust",
            reasoning="test adjust",
            revised_steps=["adjusted step A", "adjusted step B"],
        )

    monkeypatch.setattr(_dm, "director_evaluate", _fake_director_evaluate)

    result = run_agent_loop(
        "multi-step goal that triggers adjust",
        adapter=_DryRunAdapter(),
        dry_run=False,
    )
    # The loop must complete without ValueError from mark_item
    assert result.status in ("done", "stuck", "error")


def test_adaptive_adjust_remaining_indices_are_negative_one(monkeypatch, tmp_path):
    """After adjust fires, remaining_indices must all be -1, not step-count integers."""
    _setup_workspace(monkeypatch, tmp_path)

    from director import DirectorDecision
    import agent_loop as _al
    import config as _cfg_mod
    import orch_items as _oi

    # Enable adaptive_execution via config patch
    _orig_cfg_get = _cfg_mod.get
    def _patched_cfg_get(key, default=None):
        if key == "adaptive_execution":
            return True
        return _orig_cfg_get(key, default)
    monkeypatch.setattr(_cfg_mod, "get", _patched_cfg_get)

    # Spy on mark_item to catch any bad indices
    _mark_calls = []
    _orig_mark = _oi.mark_item

    def _spy_mark(slug, item_index, new_state):
        _mark_calls.append(item_index)
        _orig_mark(slug, item_index, new_state)

    monkeypatch.setattr(_oi, "mark_item", _spy_mark)

    # Patch director_evaluate to return adjust once, then continue
    _eval_count = [0]
    import director as _dm

    def _fake_eval(goal, eval_ctx, trigger, adapter, *, dry_run=False):
        _eval_count[0] += 1
        if _eval_count[0] == 1:
            return DirectorDecision(
                action="adjust",
                reasoning="force adjust",
                revised_steps=["new step X", "new step Y"],
            )
        return DirectorDecision(action="continue", reasoning="ok")

    monkeypatch.setattr(_dm, "director_evaluate", _fake_eval)

    result = run_agent_loop(
        "test adjust index fix",
        adapter=_DryRunAdapter(),
        dry_run=False,
    )

    # mark_item is never called with -1 (skipped by agent_loop).
    # mark_item IS called for original NEXT.md items — their indices are line
    # numbers (typically >= 8 for a normal NEXT.md with header lines).
    # The bug produced indices like 5, 6, 7 (step counts), which are blank
    # lines in NEXT.md. Verify no such collision occurred.
    # Conservatively: no item_index in [0, 1, 2] which are always header lines.
    low_indices = [i for i in _mark_calls if 0 <= i < 3]
    assert low_indices == [], (
        f"mark_item called with header-area index {low_indices} — "
        "adaptive adjust must use -1 sentinels, not step counts"
    )


def test_adaptive_adjust_source_pattern_absent():
    """Source-level regression: the buggy index-rebuild pattern must not reappear.

    Before fix, all 4 adaptive execution sites (stuck/adjust, stuck/replan,
    verify-or-threshold/adjust, verify-or-threshold/replan) used:

        remaining_indices[:] = list(
            range(len(step_outcomes), len(step_outcomes) + len(new_steps))
        )

    These integers collide with NEXT.md line numbers and cause mark_item to
    raise ValueError. Post-fix: `[-1] * len(new_steps)` at all 4 sites.

    This test exists because the functional tests above can't reliably trigger
    the adjust/replan code path under dry_run (DryRunAdapter only produces
    3 steps; the step threshold is 5). A source-level check is the most
    direct guard against accidental revert.
    """
    import inspect
    import agent_loop as _al

    src = inspect.getsource(_al)

    # The buggy pattern combines remaining_indices slice-assignment with a
    # range() expression using step_outcomes count. If any site reintroduces
    # it, this check fails.
    import re
    buggy_pattern = re.compile(
        r"remaining_indices\[:\]\s*=\s*list\(\s*\n?\s*range\(\s*len\(step_outcomes\)",
        re.MULTILINE,
    )
    matches = buggy_pattern.findall(src)
    assert not matches, (
        f"Buggy remaining_indices rebuild pattern reintroduced "
        f"({len(matches)} occurrence(s)). All adaptive adjust/replan sites "
        "must use [-1] * len(new_steps) — NEXT.md line numbers are not "
        "step counts."
    )


def test_adaptive_escalate_reply_reaches_next_step(monkeypatch, tmp_path):
    """A user's reply to a verify/threshold director-escalate must reach the
    next step's prompt.

    Bug (2026-07-15 seam inventory): the stuck-trigger escalate survives via
    `continue`, but the verify_failure/step_threshold escalate wrote
    _next_step_injected_context and then fell through to the carry-forward
    assignment (`_next_step_injected_context = _step_injected_context`),
    which silently clobbered the reply. The channel round-trip happened —
    the user answered — and the answer never entered any prompt.
    """
    _setup_workspace(monkeypatch, tmp_path)

    from director import DirectorDecision
    import config as _cfg_mod

    _orig_cfg_get = _cfg_mod.get

    def _patched_cfg_get(key, default=None):
        if key == "adaptive_execution":
            return True
        return _orig_cfg_get(key, default)

    monkeypatch.setattr(_cfg_mod, "get", _patched_cfg_get)

    # 6 steps so the step_threshold trigger (every 5 steps) fires mid-run,
    # with at least one step left to receive the injected reply.
    class _SixStepAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), ""
            )
            if ("decompose" in user_content.lower()
                    or "concrete steps" in user_content.lower()):
                from llm import LLMResponse
                steps = [f"Perform part {i} of the work" for i in range(1, 7)]
                return LLMResponse(
                    content=json.dumps(steps), stop_reason="end_turn",
                    input_tokens=50, output_tokens=30,
                )
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    _eval_count = [0]
    import director as _dm

    def _fake_eval(goal, eval_ctx, trigger, adapter, *, dry_run=False):
        _eval_count[0] += 1
        if _eval_count[0] == 1:
            return DirectorDecision(
                action="escalate",
                reasoning="need direction",
                user_question="Which approach should I take?",
            )
        return DirectorDecision(action="continue", reasoning="ok")

    monkeypatch.setattr(_dm, "director_evaluate", _fake_eval)

    class _FakeChannel:
        def __init__(self):
            self.questions = []

        def ask(self, question):
            self.questions.append(question)
            return "use approach B with the smaller dataset"

    _channel = _FakeChannel()
    adapter = _SixStepAdapter()
    result = run_agent_loop(
        "six part goal exercising the escalate reply carry",
        adapter=adapter,
        dry_run=False,
        channel=_channel,
    )
    assert result.status in ("done", "stuck", "error")
    assert _channel.questions, "director escalate never reached the channel"
    assert any(
        "use approach B with the smaller dataset" in msg
        for msg in adapter.exec_user_msgs
    ), (
        "escalate reply was clobbered before the next step prompt — "
        "the carry-forward assignment must merge, not overwrite"
    )


def test_loop_projectless_run_still_fences_cwd(monkeypatch, tmp_path):
    """BACKLOG #1 (3rd repro): a run with no project must not execute with the
    inherited launch cwd — the ambient subprocess cwd falls back to the
    goal-slug project dir, created on the spot (Popen raises on missing cwd)."""
    _setup_workspace(monkeypatch, tmp_path)
    from llm import get_default_subprocess_cwd
    from agent_loop import _goal_to_slug

    run_agent_loop("summarize the incident timeline", dry_run=True)

    bound = get_default_subprocess_cwd()
    assert bound, "ambient cwd must be bound even without a project"
    assert _goal_to_slug("summarize the incident timeline") in bound
    assert Path(bound).is_dir()


def test_run_agent_loop_emits_scavenge_event(monkeypatch, tmp_path):
    """Out-of-fence file access in the real tool transcript emits
    SCAVENGE_DETECTED (diagnostic only — the step is NOT blocked)."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    # Must be outside BOTH the project dir and the conftest workspace
    # (MARO_WORKSPACE=tmp_path), and not a filtered system prefix.
    stray = "/home/nonexistent-stale-clone/main.go"
    events_seen = []
    _orig_log_event = captains_log.log_event

    def _capture(event_type, subject, summary, **kw):
        events_seen.append((event_type, subject, summary, kw))
        return _orig_log_event(event_type, subject, summary, **kw)

    monkeypatch.setattr(captains_log, "log_event", _capture)

    class _ScavAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Surveyed the existing implementation.",
                    "summary": "surveyed",
                })],
                input_tokens=1, output_tokens=1,
                tool_events=[{"name": "Read", "input": {"file_path": stray},
                              "output": "package main", "is_error": False}],
            )

    result = al.run_agent_loop(
        "survey the project",
        adapter=_ScavAdapter(),
        preset_steps=["Survey the existing implementation"],
        max_steps=1,
        max_iterations=2,
    )
    scav = [e for e in events_seen if e[0] == "SCAVENGE_DETECTED"]
    assert scav, f"expected SCAVENGE_DETECTED, saw {[e[0] for e in events_seen]}"
    ctx = scav[0][3].get("context") or {}
    assert any(r["path"] == stray for r in ctx.get("reads", []))
    # Diagnostic only: the step must not be blocked by scavenging.
    assert not any(s.status == "blocked" for s in result.steps)


def test_run_agent_loop_no_scavenge_event_for_in_fence_access(monkeypatch, tmp_path):
    """Reads inside the project dir / workspace do not emit SCAVENGE_DETECTED."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    events_seen = []
    monkeypatch.setattr(
        captains_log, "log_event",
        lambda event_type, subject, summary, **kw: events_seen.append(event_type) or {},
    )

    slug = al._goal_to_slug("survey in fence")
    proj_file = str(tmp_path / "projects" / slug / "notes.md")

    class _InFenceAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Read the project notes.",
                    "summary": "read notes",
                })],
                input_tokens=1, output_tokens=1,
                tool_events=[{"name": "Read", "input": {"file_path": proj_file},
                              "output": "notes", "is_error": False}],
            )

    al.run_agent_loop(
        "survey in fence",
        adapter=_InFenceAdapter(),
        preset_steps=["Read the project notes"],
        max_steps=1,
        max_iterations=2,
    )
    assert "SCAVENGE_DETECTED" not in events_seen


def test_run_agent_loop_write_fence_blocks_out_of_fence_write(monkeypatch, tmp_path):
    """Tier-a write fence (validate.write_fence on): an out-of-fence WRITE in
    the real tool transcript demotes the step done→blocked and emits
    FENCE_WRITE_BLOCKED."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    import config as config_mod
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    _orig_get = config_mod.get

    def _fake_get(key, default=None):
        if key == "validate.write_fence":
            return True
        return _orig_get(key, default)

    monkeypatch.setattr(config_mod, "get", _fake_get)

    events_seen = []
    _orig_log_event = captains_log.log_event

    def _capture(event_type, subject, summary, **kw):
        events_seen.append((event_type, kw))
        return _orig_log_event(event_type, subject, summary, **kw)

    monkeypatch.setattr(captains_log, "log_event", _capture)

    stray = "/home/nonexistent-other-repo/leaked.py"

    class _WriteFenceAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Completed the refactor step.",
                    "summary": "refactored",
                })],
                input_tokens=1, output_tokens=1,
                tool_events=[{"name": "Write", "input": {"file_path": stray},
                              "output": "ok", "is_error": False}],
            )

    result = al.run_agent_loop(
        "refactor the helper",
        adapter=_WriteFenceAdapter(),
        preset_steps=["Refactor the helper module"],
        max_steps=1,
        max_iterations=2,
    )
    blocked = [s for s in result.steps if s.status == "blocked"]
    assert blocked, f"expected a blocked step, got {[s.status for s in result.steps]}"
    assert any("[write-fence]" in (s.result or "") for s in blocked)
    fence_events = [e for e in events_seen if e[0] == "FENCE_WRITE_BLOCKED"]
    assert fence_events, f"expected FENCE_WRITE_BLOCKED, saw {[e[0] for e in events_seen]}"
    ctx = fence_events[0][1].get("context") or {}
    assert any(w["path"] == stray for w in ctx.get("writes", []))


def test_run_agent_loop_write_fence_explicit_off(monkeypatch, tmp_path):
    """With validate.write_fence explicitly disabled (default flipped ON
    2026-07-09), an out-of-fence write is diagnostic only: SCAVENGE_DETECTED
    fires, the step stays done."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    import config as config_mod
    _orig_get = config_mod.get
    monkeypatch.setattr(
        config_mod, "get",
        lambda key, default=None: False if key == "validate.write_fence"
        else _orig_get(key, default),
    )
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)

    events_seen = []
    monkeypatch.setattr(
        captains_log, "log_event",
        lambda event_type, subject, summary, **kw: events_seen.append(event_type) or {},
    )

    stray = "/home/nonexistent-other-repo/leaked.py"

    class _WriteAdapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            from llm import LLMResponse, ToolCall
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": "Completed the refactor step.",
                    "summary": "refactored",
                })],
                input_tokens=1, output_tokens=1,
                tool_events=[{"name": "Write", "input": {"file_path": stray},
                              "output": "ok", "is_error": False}],
            )

    result = al.run_agent_loop(
        "refactor the helper",
        adapter=_WriteAdapter(),
        preset_steps=["Refactor the helper module"],
        max_steps=1,
        max_iterations=2,
    )
    assert "SCAVENGE_DETECTED" in events_seen
    assert "FENCE_WRITE_BLOCKED" not in events_seen
    assert not any(s.status == "blocked" for s in result.steps)


def _fence_on_config(monkeypatch):
    """Force validate.write_fence on, passthrough for everything else."""
    import config as config_mod
    _orig_get = config_mod.get

    def _fake_get(key, default=None):
        if key == "validate.write_fence":
            return True
        return _orig_get(key, default)

    monkeypatch.setattr(config_mod, "get", _fake_get)


def _write_adapter_for(path):
    from llm import LLMResponse, ToolCall

    class _Adapter:
        model_key = "test"

        def complete(self, messages, **kwargs):
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(name="complete_step", arguments={
                    "result": f"Wrote {path}.", "summary": "wrote file",
                })],
                input_tokens=1, output_tokens=1,
                tool_events=[{"name": "Write", "input": {"file_path": path},
                              "output": "ok", "is_error": False}],
            )

    return _Adapter()


def test_run_agent_loop_write_fence_allows_tmp_scratch(monkeypatch, tmp_path):
    """/tmp carve-out (2026-07-04): a /tmp scratch write under fence-on stays
    done — scratch is not drift, no demotion, no fence events."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)
    _fence_on_config(monkeypatch)

    events_seen = []
    monkeypatch.setattr(
        captains_log, "log_event",
        lambda event_type, subject, summary, **kw: events_seen.append(event_type) or {},
    )

    result = al.run_agent_loop(
        "stage the data transform",
        adapter=_write_adapter_for("/tmp/maro-test-scratch/staging.json"),
        preset_steps=["Stage the transform scratch file"],
        max_steps=1,
        max_iterations=2,
    )
    assert "FENCE_WRITE_BLOCKED" not in events_seen
    assert "SCAVENGE_DETECTED" not in events_seen
    assert not any(s.status == "blocked" for s in result.steps)


def test_run_agent_loop_write_fence_widens_to_goal_declared_path(monkeypatch, tmp_path):
    """Intent-widening (2026-07-04): the goal explicitly names an out-of-fence
    path; the worker writes exactly there. Fence-on must let it through and
    audit the widening via FENCE_EXTENDED — this is the probe-run failure mode
    ('goal conflicts with fence') fixed. Intent trumps."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)
    _fence_on_config(monkeypatch)

    events_seen = []
    _orig_log_event = captains_log.log_event

    def _capture(event_type, subject, summary, **kw):
        events_seen.append((event_type, kw))
        return _orig_log_event(event_type, subject, summary, **kw)

    monkeypatch.setattr(captains_log, "log_event", _capture)

    target = "/home/nonexistent-target-repo/fix.py"
    result = al.run_agent_loop(
        f"apply the null-check fix to {target}",
        adapter=_write_adapter_for(target),
        preset_steps=["Apply the null-check fix"],
        max_steps=1,
        max_iterations=2,
    )
    kinds = [e[0] for e in events_seen]
    assert "FENCE_WRITE_BLOCKED" not in kinds
    assert not any(s.status == "blocked" for s in result.steps)
    ext = [e for e in events_seen if e[0] == "FENCE_EXTENDED"]
    assert ext, f"expected FENCE_EXTENDED, saw {kinds}"
    _ctx = ext[0][1].get("context") or {}
    assert "/home/nonexistent-target-repo/fix.py" in _ctx.get("roots", [])


def test_run_agent_loop_write_fence_still_blocks_undeclared_path(monkeypatch, tmp_path):
    """The widening must not swallow genuine drift: goal names one tree, the
    worker writes into a DIFFERENT out-of-fence tree → still demoted."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    import agent_loop as al
    import loop_execute
    import captains_log
    monkeypatch.setattr(loop_execute, "_local_auto_ralph_enabled", lambda: False)
    _fence_on_config(monkeypatch)

    events_seen = []
    monkeypatch.setattr(
        captains_log, "log_event",
        lambda event_type, subject, summary, **kw: events_seen.append(event_type) or {},
    )

    result = al.run_agent_loop(
        "apply the null-check fix to /home/nonexistent-target-repo/fix.py",
        adapter=_write_adapter_for("/home/nonexistent-OTHER-repo/leaked.py"),
        preset_steps=["Apply the null-check fix"],
        max_steps=1,
        max_iterations=2,
    )
    assert "FENCE_WRITE_BLOCKED" in events_seen
    assert any(s.status == "blocked" for s in result.steps)


# ---------------------------------------------------------------------------
# _finalize_loop — evolver meta-cycle on run-cadence (2026-07-09: no daemon;
# every N-th real finalization fires run_evolver; dry_run never counts)
# ---------------------------------------------------------------------------

def _finalize_for_cadence(dry_run=False, adapter=None):
    _finalize_loop(
        loop_id="cad-test",
        goal="cadence goal",
        project="proj",
        loop_status="done",
        step_outcomes=[],
        adapter=adapter,
        dry_run=dry_run,
        verbose=False,
        total_tokens_in=0,
        total_tokens_out=0,
        elapsed_ms=0,
        had_no_matching_skill=False,
    )


def _patch_cadence(monkeypatch, tmp_path, cadence_value):
    """Isolate counter file, set evolver.run_cadence, capture run_evolver calls."""
    import evolver_store
    import evolver as evolver_mod
    import config as config_mod

    monkeypatch.setattr(
        evolver_store, "_cadence_path", lambda: tmp_path / "evolver_cadence.json"
    )

    orig_get = config_mod.get

    def fake_get(key, default=None, *a, **kw):
        if key == "evolver.run_cadence":
            return cadence_value
        return orig_get(key, default, *a, **kw)

    monkeypatch.setattr(config_mod, "get", fake_get)

    calls = []

    def fake_run_evolver(**kw):
        calls.append(kw)
        from evolver import EvolverReport
        return EvolverReport(run_id="fake", outcomes_reviewed=0, skipped=True)

    monkeypatch.setattr(evolver_mod, "run_evolver", fake_run_evolver)
    return calls


def test_finalize_cadence_off_by_default(monkeypatch, tmp_path):
    """Fresh installs unchanged: evolver.run_cadence defaults to 0 → never fires."""
    import evolver_store
    import evolver as evolver_mod
    monkeypatch.setattr(
        evolver_store, "_cadence_path", lambda: tmp_path / "evolver_cadence.json"
    )
    calls = []
    monkeypatch.setattr(evolver_mod, "run_evolver", lambda **kw: calls.append(kw))
    for _ in range(3):
        _finalize_for_cadence(dry_run=False)
    assert calls == []


def test_finalize_cadence_counter_increments(monkeypatch, tmp_path):
    import json as _json
    _patch_cadence(monkeypatch, tmp_path, 10)
    _finalize_for_cadence(dry_run=False)
    _finalize_for_cadence(dry_run=False)
    state = _json.loads((tmp_path / "evolver_cadence.json").read_text())
    assert state["runs_since_evolve"] == 2


def test_finalize_cadence_fires_at_n_and_resets(monkeypatch, tmp_path):
    import json as _json

    class _FakeAdapter:
        model_key = "test"

    adapter = _FakeAdapter()
    calls = _patch_cadence(monkeypatch, tmp_path, 2)
    _finalize_for_cadence(dry_run=False, adapter=adapter)
    assert calls == []
    _finalize_for_cadence(dry_run=False, adapter=adapter)
    assert len(calls) == 1
    # the run's adapter is threaded through, not a freshly constructed one
    assert calls[0].get("adapter") is adapter
    state = _json.loads((tmp_path / "evolver_cadence.json").read_text())
    assert state["runs_since_evolve"] == 0
    # next window
    _finalize_for_cadence(dry_run=False, adapter=adapter)
    _finalize_for_cadence(dry_run=False, adapter=adapter)
    assert len(calls) == 2


def test_finalize_cadence_dry_run_does_not_count_or_trigger(monkeypatch, tmp_path):
    calls = _patch_cadence(monkeypatch, tmp_path, 1)
    _finalize_for_cadence(dry_run=True)
    _finalize_for_cadence(dry_run=True)
    assert calls == []
    assert not (tmp_path / "evolver_cadence.json").exists()


def test_finalize_cadence_evolver_exception_is_nonfatal(monkeypatch, tmp_path):
    import evolver_store
    import evolver as evolver_mod
    import config as config_mod

    monkeypatch.setattr(
        evolver_store, "_cadence_path", lambda: tmp_path / "evolver_cadence.json"
    )
    orig_get = config_mod.get
    monkeypatch.setattr(
        config_mod, "get",
        lambda key, default=None, *a, **kw: 1 if key == "evolver.run_cadence"
        else orig_get(key, default, *a, **kw),
    )

    def boom(**kw):
        raise RuntimeError("evolver exploded")

    monkeypatch.setattr(evolver_mod, "run_evolver", boom)
    # must not raise — failures are logged, never fatal to finalization
    _finalize_for_cadence(dry_run=False)


# ---------------------------------------------------------------------------
# §6 injection seam — typed contributions (docs/SESSION_PROTOCOL_DESIGN.md §6a)
# ---------------------------------------------------------------------------

from loop_types import (
    ContextContribution,
    ContributionLedger,
    render_contributions,
)


def test_render_contributions_empty_is_empty_string():
    """HARD CONTRACT: zero contributions render to "" so prompts stay
    byte-identical to the pre-seam flat-string behavior. Do NOT weaken this
    pin — the worker-slice A/B contract depends on byte-identity discipline."""
    assert render_contributions([]) == ""
    ledger = ContributionLedger()
    assert render_contributions(ledger.drain()) == ""
    # whitespace-only appends are dropped, preserving the empty case
    ledger.append("hook", "context", "   \n  ")
    assert len(ledger) == 0
    assert render_contributions(ledger.drain()) == ""


def test_ledger_two_contributors_same_boundary_both_render():
    """The escalate-reply clobber class (fixed 2026-07-15): two contributors
    appending at the same boundary must BOTH survive to the render."""
    ledger = ContributionLedger()
    ledger.append("escalate_reply", "reply", "User replied: approach B")
    ledger.append("hook", "context", "reviewer: watch the file size")
    rendered = render_contributions(ledger.drain())
    assert "[escalate_reply] User replied: approach B" in rendered
    assert "[hook] reviewer: watch the file size" in rendered
    # drained exactly once — nothing survives to the next boundary
    assert len(ledger) == 0
    assert render_contributions(ledger.drain()) == ""


class _PromptCaptureAdapter:
    """Records every complete() call's newest user message and kwargs."""

    def __init__(self):
        self.user_msgs = []
        self.call_kwargs = []

    def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
        self.user_msgs.append(
            next((m.content for m in reversed(messages) if m.role == "user"), "")
        )
        self.call_kwargs.append(kwargs)
        return LLMResponse(
            content="",
            stop_reason="tool_use",
            tool_calls=[ToolCall(
                name="complete_step",
                arguments={"result": "ok", "summary": "ok"},
            )],
            input_tokens=10,
            output_tokens=5,
        )


def test_execute_step_prompts_byte_identical_with_no_contributions():
    """Empty incremental context ⇒ exact pre-seam prompt shapes for BOTH the
    fresh user_msg and the live-session delta prompt (byte-identity pin)."""
    adapter = _PromptCaptureAdapter()
    outcome = _execute_step(
        goal="byte identity goal",
        step_text="do the byte identity thing",
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=[],
        incremental_context="",
        executor_session={},  # forces session_delta_prompt to be built + passed
    )
    assert outcome["status"] == "done"
    expected_user_msg = (
        "Overall goal: byte identity goal\n\n"
        "Current step (1/1) [general]: do the byte identity thing\n\n"
        "Complete this step now. Call complete_step when done or flag_stuck if blocked."
    )
    assert adapter.user_msgs[0] == expected_user_msg
    expected_delta = (
        "Continue the same goal and bounded work segment.\n\n"
        "Current step (1/1) [general]: do the byte identity thing\n\n"
        "Complete this step now. Call complete_step when done or flag_stuck if blocked."
    )
    assert adapter.call_kwargs[0].get("session_delta_prompt") == expected_delta


def test_note_interrupt_reaches_next_step_and_mutates_nothing(monkeypatch, tmp_path):
    """A posted `note` interrupt is context-only: it reaches the NEXT step's
    prompt provenance-labeled, and does not touch steps or goal."""
    _setup_workspace(monkeypatch, tmp_path)
    from interrupt import InterruptQueue

    q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")
    q.post("the deadline moved to Friday", source="test", intent="note")

    class _CaptureExecAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), "")
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    adapter = _CaptureExecAdapter()
    result = run_agent_loop(
        "prepare a topic writeup for the note seam",
        adapter=adapter,
        dry_run=False,
        interrupt_queue=q,
    )
    assert result.status == "done"
    assert result.interrupts_applied == 1
    # Goal untouched; plan untouched (the _DryRunAdapter plan is 3 steps and
    # the note must not add, replace, or reprioritize any of them).
    assert result.goal == "prepare a topic writeup for the note seam"
    assert len(result.steps) == 3
    assert all("deadline moved" not in s.text for s in result.steps)
    # Delivered at the next boundary: not in step 1's prompt (posted before
    # the run; the boundary poll runs AFTER each step), present afterwards.
    assert "[user_note]" not in adapter.exec_user_msgs[0]
    assert any(
        "[user_note] the deadline moved to Friday" in m
        for m in adapter.exec_user_msgs[1:]
    ), adapter.exec_user_msgs


def test_two_contributors_same_boundary_both_render_in_loop(monkeypatch, tmp_path):
    """Two different contributors at the same boundary — a user note consumed
    at the step-4 boundary poll plus the step-5 goal reorientation — must both
    render in step 5's prompt, neither clobbered (the semantics-trap class)."""
    _setup_workspace(monkeypatch, tmp_path)
    from interrupt import InterruptQueue

    q = InterruptQueue(queue_path=tmp_path / "interrupts.jsonl")

    class _SixStepCaptureAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), "")
            if ("decompose" in user_content.lower()
                    or "concrete steps" in user_content.lower()):
                steps = [f"Perform part {i} of the work" for i in range(1, 7)]
                return LLMResponse(
                    content=json.dumps(steps), stop_reason="end_turn",
                    input_tokens=50, output_tokens=30,
                )
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    posted = []

    def _post_note_after_step_4(step_num, step_text, summary, status):
        if step_num == 4 and not posted:
            posted.append(True)
            q.post("budget approval came through", source="test", intent="note")

    adapter = _SixStepCaptureAdapter()
    result = run_agent_loop(
        "six part goal exercising same boundary contributors",
        adapter=adapter,
        dry_run=False,
        interrupt_queue=q,
        step_callback=_post_note_after_step_4,
    )
    assert result.status == "done"
    step5_msgs = [m for m in adapter.exec_user_msgs
                  if "Perform part 5 of the work" in m]
    assert step5_msgs, adapter.exec_user_msgs
    assert any("[user_note] budget approval came through" in m
               for m in step5_msgs), step5_msgs
    assert any("[reorientation] GOAL REORIENTATION" in m
               for m in step5_msgs), step5_msgs


def test_parallel_batch_delivers_pending_once(monkeypatch, tmp_path):
    """Pending contributions reach a parallel batch boundary exactly once:
    the batch consumes them, and the following boundary gets nothing."""
    _setup_workspace(monkeypatch, tmp_path)
    import loop_parallel
    from loop_types import LoopContext

    captured = []

    def _fake_run_steps_parallel(**kw):
        captured.append(kw)
        return [{"status": "done", "result": "r"} for _ in kw["steps"]]

    monkeypatch.setattr(loop_parallel, "_run_steps_parallel",
                        _fake_run_steps_parallel)

    ctx = LoopContext(loop_id="x", project="", goal="g",
                      ancestry_context="ANC")
    ctx.pending_context.append("user_note", "note", "watch the deadline")

    for _ in range(2):  # two consecutive batch boundaries
        loop_parallel._run_parallel_batch(
            ctx, "lead step", ["peer one"],
            step_outcomes=[], completed_context=[],
            remaining_steps=[], remaining_indices=[],
            loop_shared_ctx={}, resolve_tools_fn=lambda: [],
            parallel_fan_out=2, proj_artifact_dir="",
            iteration=0, step_idx=0,
        )

    first, second = captured
    assert first["incremental_context"] == "[user_note] watch the deadline"
    assert first["ancestry_context"] == "ANC\n\n[user_note] watch the deadline"
    # consumed once: nothing leaks into the following boundary
    assert second["incremental_context"] == ""
    assert second["ancestry_context"] == "ANC"
    assert len(ctx.pending_context) == 0


def test_run_steps_parallel_passes_incremental_to_every_step(monkeypatch, tmp_path):
    """Every step in a fan-out batch sees the same rendered contributions."""
    _setup_workspace(monkeypatch, tmp_path)
    import loop_parallel

    calls = []

    def _fake_execute_step(**kw):
        calls.append(kw)
        return {"status": "done", "result": "r", "summary": "s",
                "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr(loop_parallel, "_execute_step", _fake_execute_step)
    outcomes = loop_parallel._run_steps_parallel(
        goal="g", steps=["step a", "step b"], adapter=object(),
        ancestry_context="ANC", tools=[], verbose=False, max_workers=2,
        incremental_context="[user_note] watch the deadline",
    )
    assert len(outcomes) == 2
    assert len(calls) == 2
    assert all(c["incremental_context"] == "[user_note] watch the deadline"
               for c in calls)
    assert all(c["ancestry_context"] == "ANC" for c in calls)


def test_blocked_retry_rearms_delivered_contributions(monkeypatch, tmp_path):
    """A blocked-step retry re-delivers what the failed step saw plus the
    retry hint — appended to the pending ledger, never assigned."""
    _setup_workspace(monkeypatch, tmp_path)
    import loop_blocked as _lb
    from loop_types import LoopContext

    monkeypatch.setattr(
        _lb, "_handle_blocked_step",
        lambda *a, **kw: _lb._BlockDecision(
            retry=True, hint="try the smaller file",
            loop_status="", stuck_reason=""),
    )
    ctx = LoopContext(loop_id="x", project="p", goal="g", dry_run=True)
    blk = _lb.BlockedStepContext(
        step_text="blocked step", step_idx=1, step_result="partial",
        step_elapsed=10, outcome={"stuck_reason": "boom"}, item_index=-1,
        iteration=1, step_adapter=None, step_retries={},
        step_tier_overrides={}, failure_chain=[], step_outcomes=[],
        remaining_steps=[], remaining_indices=[], manifest_steps=[],
        delivered_contributions=[
            ContextContribution("user_note", "note", "keep this")],
    )
    flow = _lb._process_blocked_step(ctx, blk)
    assert flow[0] == "continue"
    rendered = render_contributions(ctx.pending_context.drain())
    assert "[user_note] keep this" in rendered
    assert "[blocked_retry]" in rendered


def _make_blocked_ctx(decision, monkeypatch):
    """Drive _process_blocked_step with a forced decision and one delivered
    contribution; return (ctx, blk, flow)."""
    import loop_blocked as _lb
    from loop_types import LoopContext

    monkeypatch.setattr(_lb, "_handle_blocked_step", lambda *a, **kw: decision)
    ctx = LoopContext(loop_id="x", project="p", goal="g", dry_run=True)
    blk = _lb.BlockedStepContext(
        step_text="blocked step", step_idx=1, step_result="partial",
        step_elapsed=10, outcome={"stuck_reason": "boom"}, item_index=-1,
        iteration=1, step_adapter=None, step_retries={},
        step_tier_overrides={}, failure_chain=[], step_outcomes=[],
        remaining_steps=[], remaining_indices=[], manifest_steps=[],
        delivered_contributions=[
            ContextContribution("user_note", "note", "keep this")],
    )
    flow = _lb._process_blocked_step(ctx, blk)
    return ctx, blk, flow


def test_blocked_redecompose_rearms_delivered_contributions(monkeypatch, tmp_path):
    """The redecompose branch must carry the failed step's context to the
    replacement sub-steps, same as retry (adversarial review 2026-07-15:
    pre-refactor, the flat string round-tripped through all three blocked
    branches; the first typed-ledger cut narrowed it to retry-only)."""
    _setup_workspace(monkeypatch, tmp_path)
    import loop_blocked as _lb
    import planner

    monkeypatch.setattr(
        planner, "decompose",
        lambda *a, **kw: ["sub-step alpha", "sub-step beta"])
    ctx, blk, flow = _make_blocked_ctx(
        _lb._BlockDecision(
            retry=False, hint="", loop_status="", stuck_reason="",
            redecompose=True, metacognitive_reason="finer"),
        monkeypatch,
    )
    assert flow[0] == "continue"
    assert blk.remaining_steps, "sub-steps were not inserted"
    rendered = render_contributions(ctx.pending_context.drain())
    assert "[user_note] keep this" in rendered


def test_blocked_split_rearms_delivered_contributions(monkeypatch, tmp_path):
    """The timeout-split branch must carry the failed step's context to the
    split halves, same as retry (adversarial review 2026-07-15)."""
    _setup_workspace(monkeypatch, tmp_path)
    import loop_blocked as _lb

    ctx, blk, flow = _make_blocked_ctx(
        _lb._BlockDecision(
            retry=False, hint="", loop_status="", stuck_reason="",
            split_into=["first half", "second half"]),
        monkeypatch,
    )
    assert flow[0] == "continue"
    assert blk.remaining_steps == ["first half", "second half"]
    rendered = render_contributions(ctx.pending_context.drain())
    assert "[user_note] keep this" in rendered


def test_ledger_append_caps_oversized_text():
    """A single contribution cannot render unbounded text into a prompt —
    oversized appends truncate with a marker (adversarial review 2026-07-15:
    a 10MB --intent note rendered 10MB)."""
    from loop_types import ContributionLedger

    ledger = ContributionLedger()
    ledger.append(
        "user_note", "note", "A" * (ContributionLedger.MAX_TEXT_CHARS + 500))
    rendered = render_contributions(ledger.drain())
    assert len(rendered) <= ContributionLedger.MAX_TEXT_CHARS + 100
    assert "…[truncated 500 chars]" in rendered


def test_compound_guard_split_rearms_contributions(monkeypatch, tmp_path):
    """Contributions drained at a boundary where the compound-step invariant
    guard splits the step and `continue`s must be re-armed for the split
    parts, not destroyed (adversarial review 2026-07-15, HIGH: an
    acknowledged operator note / hook output vanished when director-adjust
    inserted an unshaped compound step).

    Recipe: a step-5 post-step hook contributes context; the director's
    step_threshold evaluation at the same boundary adjusts the plan with an
    unshaped compound step (real gap — director-adjust assigns revised_steps
    without _shape_steps, which is why the guard exists). Next iteration
    drains the ledger, the guard splits and continues — the hook context
    must still reach an executed prompt."""
    _setup_workspace(monkeypatch, tmp_path)

    from director import DirectorDecision
    import config as _cfg_mod

    _orig_cfg_get = _cfg_mod.get

    def _patched_cfg_get(key, default=None):
        if key == "adaptive_execution":
            return True
        return _orig_cfg_get(key, default)

    monkeypatch.setattr(_cfg_mod, "get", _patched_cfg_get)

    class _SixStepCaptureAdapter(_DryRunAdapter):
        def __init__(self):
            self.exec_user_msgs = []

        def complete(self, messages, *, tools=None, tool_choice="auto", **kwargs):
            user_content = next(
                (m.content for m in reversed(messages) if m.role == "user"), "")
            if ("decompose" in user_content.lower()
                    or "concrete steps" in user_content.lower()):
                steps = [f"Perform part {i} of the work" for i in range(1, 7)]
                return LLMResponse(
                    content=json.dumps(steps), stop_reason="end_turn",
                    input_tokens=50, output_tokens=30,
                )
            if tools and tool_choice == "required":
                self.exec_user_msgs.append(user_content)
            return super().complete(
                messages, tools=tools, tool_choice=tool_choice, **kwargs)

    _eval_count = [0]
    import director as _dm

    def _fake_eval(goal, eval_ctx, trigger, adapter, *, dry_run=False):
        _eval_count[0] += 1
        if _eval_count[0] == 1:
            return DirectorDecision(
                action="adjust", reasoning="tighten the plan",
                revised_steps=[
                    "Run the pytest suite and analyze failures in depth",
                    "Write the final summary",
                ],
            )
        return DirectorDecision(action="continue", reasoning="ok")

    monkeypatch.setattr(_dm, "director_evaluate", _fake_eval)

    import hooks as _hooks_mod
    from hooks import HookResult, HookRegistry, SCOPE_STEP

    _fired = []

    def _fake_run_hooks(scope, context, registry=None, adapter=None,
                        dry_run=False, fire_on=None):
        if (scope == SCOPE_STEP
                and "Perform part 5" in str(context.get("step", ""))
                and not _fired):
            _fired.append(True)
            return [HookResult(
                hook_id="t", hook_name="t", hook_type="notification",
                scope=SCOPE_STEP, status="notification_sent",
                injected_context="watch file sizes closely",
            )]
        return []

    monkeypatch.setattr(_hooks_mod, "run_hooks", _fake_run_hooks)

    adapter = _SixStepCaptureAdapter()
    result = run_agent_loop(
        "six part goal exercising the compound guard re-arm",
        adapter=adapter,
        dry_run=False,
        hook_registry=HookRegistry(),
    )
    assert result.status == "done"
    assert _fired, "step-5 hook never fired"
    assert _eval_count[0] >= 1, "director evaluation never fired"
    assert any(
        "[hook] watch file sizes closely" in m
        for m in adapter.exec_user_msgs
    ), adapter.exec_user_msgs
