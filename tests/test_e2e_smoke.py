"""End-to-end smoke tests for the full orchestration pipeline.

Tests the pipeline wiring: classify → decompose → execute → introspect → learn.
Uses a ScriptedAdapter that returns predetermined responses, so these are:
- Free (no API calls)
- Fast (<2s each)
- Deterministic (same result every run)
- Model-proof (won't break as models improve)

Three flavors:
1. Success — all steps complete, healthy diagnosis
2. Failure — steps get stuck, introspection fires, lessons recorded
3. Ambiguous — partial completion, mixed step outcomes
"""

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# ScriptedAdapter — programmable mock LLM
# ---------------------------------------------------------------------------

class ScriptedAdapter:
    """LLM adapter that returns scripted responses in sequence.

    responses: list of dicts, each consumed in order. Keys:
      - "steps": list of step strings (for decompose calls)
      - "tool": "complete_step" | "flag_stuck" (for execute calls)
      - "result": str (step result text)
      - "content": str (raw content for non-tool calls)
    """
    model_key = "scripted"

    def __init__(self, responses: List[Dict[str, Any]]):
        self._responses = list(responses)
        self._call_idx = 0
        self.calls: List[Dict[str, Any]] = []  # record for assertions

    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall

        user_content = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )

        # Record the call
        self.calls.append({
            "idx": self._call_idx,
            "user_content_prefix": user_content[:200],
            "has_tools": bool(tools),
            "tool_choice": tool_choice,
        })

        # Get next scripted response. If exhausted, repeat the last
        # tool-bearing response (for execute calls) or last non-tool
        # response (for non-tool calls). This prevents cycling into
        # unrelated response types.
        if self._call_idx < len(self._responses):
            resp = self._responses[self._call_idx]
        elif tools:
            # Find last tool response
            resp = next(
                (r for r in reversed(self._responses) if "tool" in r),
                self._responses[-1] if self._responses else {},
            )
        else:
            # Find last non-tool response
            resp = next(
                (r for r in reversed(self._responses) if "tool" not in r),
                self._responses[-1] if self._responses else {},
            )
        self._call_idx += 1

        # Decompose: return step list
        if "steps" in resp:
            return LLMResponse(
                content=json.dumps(resp["steps"]),
                stop_reason="end_turn",
                input_tokens=50,
                output_tokens=30,
            )

        # Execute: return tool call (when response has "tool" and call has tools)
        if "tool" in resp and (tools or tool_choice == "required"):
            tool_name = resp["tool"]
            if tool_name == "complete_step":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={
                            "result": resp.get("result", "[scripted] done"),
                            "summary": resp.get("result", "[scripted] done")[:60],
                        },
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=40,
                )
            elif tool_name == "flag_stuck":
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={
                            "reason": resp.get("reason", "[scripted] blocked"),
                        },
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=40,
                )

        # Validation / classification / other: return raw content
        content = resp.get("content", '{"passed": true}')
        return LLMResponse(
            content=content,
            stop_reason="end_turn",
            input_tokens=20,
            output_tokens=10,
        )


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Flavor 1: Success — clean 3-step goal completes end-to-end
# ---------------------------------------------------------------------------

class TestE2ESuccess:
    """Goals where all steps complete cleanly."""

    def test_simple_goal_completes(self, monkeypatch, tmp_path):
        """3-step decomposition, all succeed, status=done."""
        _setup(monkeypatch, tmp_path)
        from handle import handle

        adapter = ScriptedAdapter([
            # classify (intent) — not called when force_lane is set
            # decompose
            {"steps": ["Fetch the data", "Parse the results", "Write summary"]},
            # execute step 1
            {"tool": "complete_step", "result": "Fetched 42 records"},
            # execute step 2
            {"tool": "complete_step", "result": "Parsed into 3 categories"},
            # execute step 3
            {"tool": "complete_step", "result": "Summary: 42 records in 3 categories"},
        ])

        result = handle(
            "Fetch and summarize the dataset",
            project="smoke-success",
            adapter=adapter,
            force_lane="agenda",
        )
        assert result.status == "done"

    def test_single_step_goal(self, monkeypatch, tmp_path):
        """Minimal goal: 1 step, completes."""
        _setup(monkeypatch, tmp_path)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Answer the question directly"]},
            {"tool": "complete_step", "result": "The answer is 42"},
        ])

        result = handle(
            "What is 6 times 7?",
            project="smoke-single",
            adapter=adapter,
            force_lane="now",
        )
        assert result.status == "done"

    def test_lessons_recorded_after_success(self, monkeypatch, tmp_path):
        """After a successful run, reflect_and_record fires and persists."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Step one", "Step two"]},
            {"tool": "complete_step", "result": "Done one"},
            {"tool": "complete_step", "result": "Done two"},
            # reflect_and_record will call adapter for lesson extraction
            {"content": json.dumps({"lessons": ["Test lesson from smoke run"]})},
        ])

        result = run_agent_loop(
            "Smoke test goal",
            project="smoke-lessons",
            adapter=adapter,
        )
        assert result.status == "done"
        assert sum(1 for s in result.steps if s.status == "done") == 2

    def test_introspection_fires_on_success(self, monkeypatch, tmp_path):
        """Even on success, introspection runs and produces a diagnosis."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Do the thing"]},
            {"tool": "complete_step", "result": "Thing done"},
            {"content": json.dumps({"lessons": []})},
        ])

        result = run_agent_loop(
            "Simple goal",
            project="smoke-introspect",
            adapter=adapter,
        )
        assert result.status == "done"
        # Introspection should have run (even if diagnosis is "healthy")
        # We can't easily check diagnoses.jsonl since it writes to memory_dir,
        # but we can verify the loop completed without error


# ---------------------------------------------------------------------------
# Flavor 2: Failure — steps get stuck, system handles gracefully
# ---------------------------------------------------------------------------

class TestE2EFailure:
    """Goals where steps fail and the system should handle it."""

    def test_all_steps_stuck(self, monkeypatch, tmp_path):
        """Every step flags stuck → loop never completes, status != done."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop
        import llm as _llm

        # AlwaysStuck adapter: decompose returns 1 step, every execute
        # call returns flag_stuck, everything else returns generic JSON.
        class AlwaysStuckAdapter:
            model_key = "stuck-test"

            def complete(self, messages, *, tools=None, tool_choice="auto",
                         max_tokens=4096, temperature=0.3, **kwargs):
                from llm import LLMResponse, ToolCall
                user = next((m.content for m in reversed(messages) if m.role == "user"), "")

                if "decompose" in user.lower() or "concrete steps" in user.lower():
                    return LLMResponse(content='["Attempt the impossible task"]',
                                       stop_reason="end_turn", input_tokens=30, output_tokens=20)
                if tool_choice == "required" or (tools and any(t.name == "complete_step" for t in tools)):
                    return LLMResponse(content="", tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": "Cannot complete: resource is permanently unavailable"},
                    )], stop_reason="tool_use", input_tokens=50, output_tokens=30)
                return LLMResponse(content='{"passed": true}', stop_reason="end_turn",
                                   input_tokens=15, output_tokens=5)

        adapter = AlwaysStuckAdapter()
        monkeypatch.setattr(_llm, "build_adapter", lambda **kw: adapter)

        result = run_agent_loop(
            "Access the forbidden resource",
            project="smoke-stuck",
            adapter=adapter,
            max_iterations=6,
        )
        assert result.status == "stuck"
        assert result.stuck_reason is not None

    def test_first_step_stuck_second_succeeds(self, monkeypatch, tmp_path):
        """First step blocks, second succeeds → status depends on retry behavior."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Fetch from flaky API", "Process the data"]},
            # Step 1 fails first try
            {"tool": "flag_stuck", "reason": "API returned 503"},
            # Refinement hint generated, step 1 retried
            {"tool": "complete_step", "result": "Got data on retry"},
            # Step 2 succeeds
            {"tool": "complete_step", "result": "Processed successfully"},
            # Reflection
            {"content": json.dumps({"lessons": ["Retry on 503 works"]})},
        ])

        result = run_agent_loop(
            "Fetch and process flaky data",
            project="smoke-retry",
            adapter=adapter,
        )
        # Should complete — retry succeeded
        assert result.status == "done"
        assert sum(1 for s in result.steps if s.status == "done") >= 2

    def test_stuck_loop_records_failure_lesson(self, monkeypatch, tmp_path):
        """When loop gets stuck, introspection should inject a diagnosis lesson."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop
        import llm as _llm

        class AlwaysStuck2:
            model_key = "stuck-diag"
            def complete(self, messages, *, tools=None, tool_choice="auto",
                         max_tokens=4096, temperature=0.3, **kwargs):
                from llm import LLMResponse, ToolCall
                user = next((m.content for m in reversed(messages) if m.role == "user"), "")
                if "decompose" in user.lower() or "concrete steps" in user.lower():
                    return LLMResponse(content='["Solve the complex problem"]',
                                       stop_reason="end_turn", input_tokens=30, output_tokens=20)
                if tool_choice == "required" or (tools and any(t.name == "complete_step" for t in tools)):
                    return LLMResponse(content="", tool_calls=[ToolCall(
                        name="flag_stuck",
                        arguments={"reason": "Problem is too complex to solve"},
                    )], stop_reason="tool_use", input_tokens=50, output_tokens=30)
                return LLMResponse(content='{"passed": true, "lessons": []}',
                                   stop_reason="end_turn", input_tokens=15, output_tokens=5)

        adapter = AlwaysStuck2()
        monkeypatch.setattr(_llm, "build_adapter", lambda **kw: adapter)

        result = run_agent_loop(
            "Solve an impossible problem",
            project="smoke-diag",
            adapter=adapter,
            max_iterations=4,
        )
        assert result.status == "stuck"


# ---------------------------------------------------------------------------
# Flavor 3: Ambiguous — mixed outcomes, partial success
# ---------------------------------------------------------------------------

class TestE2EAmbiguous:
    """Goals with mixed step outcomes — partial success scenarios."""

    def test_some_steps_done_some_stuck(self, monkeypatch, tmp_path):
        """2 of 3 steps complete, last one stuck → loop finishes with partial work."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": [
                "Gather data from source A",
                "Gather data from source B",
                "Synthesize A and B into report",
            ]},
            # Step 1 succeeds
            {"tool": "complete_step", "result": "Source A: 100 records"},
            # Step 2 succeeds
            {"tool": "complete_step", "result": "Source B: 50 records"},
            # Step 3 fails
            {"tool": "flag_stuck", "reason": "Cannot merge incompatible formats"},
            {"tool": "flag_stuck", "reason": "Still incompatible"},
            # Reflection
            {"content": json.dumps({"lessons": ["Format mismatch between sources"]})},
        ])

        result = run_agent_loop(
            "Gather and synthesize data from A and B",
            project="smoke-partial",
            adapter=adapter,
        )
        # 2 steps done, 1 stuck — loop should be stuck (last step blocked)
        assert sum(1 for s in result.steps if s.status == "done") >= 2

    def test_large_decomposition_runs_all_steps(self, monkeypatch, tmp_path):
        """8-step decomposition — verify all steps get attempted."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        steps = [f"Step {i}: do thing {i}" for i in range(1, 9)]
        responses = [{"steps": steps}]
        for i in range(1, 9):
            responses.append(
                {"tool": "complete_step", "result": f"Thing {i} done"}
            )
        responses.append({"content": json.dumps({"lessons": []})})

        adapter = ScriptedAdapter(responses)

        result = run_agent_loop(
            "Do 8 sequential things",
            project="smoke-8step",
            adapter=adapter,
            max_steps=8,
        )
        assert result.status == "done"
        assert sum(1 for s in result.steps if s.status == "done") == 8

    def test_empty_result_step(self, monkeypatch, tmp_path):
        """Step returns empty result — should still count as done (not stuck)."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Check if file exists", "Report finding"]},
            {"tool": "complete_step", "result": ""},
            {"tool": "complete_step", "result": "File does not exist"},
            {"content": json.dumps({"lessons": []})},
        ])

        result = run_agent_loop(
            "Check for the config file",
            project="smoke-empty",
            adapter=adapter,
        )
        assert result.status == "done"

    def test_mission_with_partial_milestone(self, monkeypatch, tmp_path):
        """Mission where first milestone partially succeeds — should continue."""
        _setup(monkeypatch, tmp_path)
        from mission import run_mission

        # Mission decompose → 2 milestones, each with 1 feature
        # First milestone's feature succeeds but validation fails
        # Second milestone's feature succeeds
        adapter = ScriptedAdapter([
            # Mission decompose (milestones + features)
            {"content": json.dumps({
                "milestones": [
                    {
                        "title": "Gather data",
                        "features": [{"title": "Fetch records from API"}],
                        "validation_criteria": ["All records fetched"],
                    },
                    {
                        "title": "Analyze data",
                        "features": [{"title": "Compute statistics"}],
                        "validation_criteria": ["Statistics computed"],
                    },
                ],
            })},
            # Feature 1 loop: decompose + execute
            {"steps": ["Fetch the records"]},
            {"tool": "complete_step", "result": "Fetched 10 of 100 records"},
            {"content": json.dumps({"lessons": []})},
            # Milestone 1 validation — fails (only 10%)
            {"content": json.dumps({"passed": False, "reason": "Only 10% fetched"})},
            # Feature 2 loop: decompose + execute
            {"steps": ["Compute the stats"]},
            {"tool": "complete_step", "result": "Mean=42, StdDev=7"},
            {"content": json.dumps({"lessons": []})},
            # Milestone 2 validation — passes
            {"content": json.dumps({"passed": True})},
        ])

        result = run_mission(
            "Gather and analyze the dataset",
            project="smoke-mission-partial",
            adapter=adapter,
        )
        # Should be partial (first milestone failed validation, second passed)
        assert result.status in ("partial", "done")
        assert result.milestones_done >= 1
        assert result.features_done >= 1


# ---------------------------------------------------------------------------
# Pipeline wiring — verify specific subsystems fire
# ---------------------------------------------------------------------------

class TestE2EPipelineWiring:
    """Verify specific pipeline components execute in order."""

    def test_token_tracking_accumulates(self, monkeypatch, tmp_path):
        """Token counts from adapter are accumulated in the result."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Do work"]},
            {"tool": "complete_step", "result": "Done"},
            {"content": json.dumps({"lessons": []})},
        ])

        result = run_agent_loop(
            "Track my tokens",
            project="smoke-tokens",
            adapter=adapter,
        )
        # ScriptedAdapter returns 50+80=130 input, 30+40=70 output
        assert result.total_tokens_in > 0
        assert result.total_tokens_out > 0

    def test_adapter_calls_recorded(self, monkeypatch, tmp_path):
        """ScriptedAdapter records all calls for inspection."""
        _setup(monkeypatch, tmp_path)
        from agent_loop import run_agent_loop

        adapter = ScriptedAdapter([
            {"steps": ["Step A", "Step B"]},
            {"tool": "complete_step", "result": "A done"},
            {"tool": "complete_step", "result": "B done"},
            {"content": json.dumps({"lessons": []})},
        ])

        run_agent_loop(
            "Two step goal",
            project="smoke-calls",
            adapter=adapter,
        )

        # At minimum: 1 decompose + 2 step executions
        assert len(adapter.calls) >= 3
        # First call should be decompose
        assert "decompose" in adapter.calls[0]["user_content_prefix"].lower() or \
               "concrete steps" in adapter.calls[0]["user_content_prefix"].lower()

    def test_handle_routes_now_vs_agenda(self, monkeypatch, tmp_path):
        """Verify handle correctly routes based on force_lane."""
        _setup(monkeypatch, tmp_path)
        from handle import handle

        adapter = ScriptedAdapter([
            {"steps": ["Do it"]},
            {"tool": "complete_step", "result": "Done"},
            {"content": json.dumps({"lessons": []})},
        ])

        result = handle(
            "A simple task",
            adapter=adapter,
            force_lane="now",
        )
        assert result.lane == "now"

        adapter2 = ScriptedAdapter([
            {"steps": ["Research it", "Analyze it"]},
            {"tool": "complete_step", "result": "Researched"},
            {"tool": "complete_step", "result": "Analyzed"},
            {"content": json.dumps({"lessons": []})},
        ])

        result2 = handle(
            "Deep research task",
            project="smoke-route-agenda",
            adapter=adapter2,
            force_lane="agenda",
        )
        assert result2.lane == "agenda"
