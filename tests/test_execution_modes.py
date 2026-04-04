"""Tests for three execution modes: team:, pipeline:, and decompose_to_dag()."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ---------------------------------------------------------------------------
# decompose_to_dag — structured DAG API
# ---------------------------------------------------------------------------

class TestDecomposeToDag:
    def _make_adapter(self):
        return MagicMock()

    def test_returns_four_tuple(self):
        """decompose_to_dag returns (clean_steps, deps, levels, parallel_levels)."""
        from planner import decompose_to_dag

        with patch("planner.decompose", return_value=["step A", "step B", "step C"]):
            result = decompose_to_dag("do something", self._make_adapter())

        assert len(result) == 4
        clean_steps, deps, levels, parallel_levels = result
        assert isinstance(clean_steps, list)
        assert isinstance(deps, dict)
        assert isinstance(levels, list)
        assert isinstance(parallel_levels, list)

    def test_strips_after_tags(self):
        """[after:N] tags should be stripped from clean_steps."""
        from planner import decompose_to_dag

        raw = ["gather data", "analyze results [after:1]", "write report [after:2]"]
        with patch("planner.decompose", return_value=raw):
            clean, deps, levels, pl = decompose_to_dag("goal", self._make_adapter())

        assert all("[after:" not in s for s in clean)
        assert len(clean) == 3

    def test_parallel_levels_detected(self):
        """Steps with shared dep create a parallel level."""
        from planner import decompose_to_dag

        # Steps 2 and 3 both depend only on step 1 → should be in same level
        raw = ["root", "branch A [after:1]", "branch B [after:1]", "merge [after:2,3]"]
        with patch("planner.decompose", return_value=raw):
            clean, deps, levels, pl = decompose_to_dag("goal", self._make_adapter())

        # parallel_levels should include a level with both step 2 and step 3
        assert any(2 in lvl and 3 in lvl for lvl in pl), f"Expected parallel level; got levels={levels}"

    def test_sequential_plan_has_no_parallel_levels(self):
        """A plan with all sequential deps produces no parallel levels."""
        from planner import decompose_to_dag

        raw = ["step A", "step B", "step C"]  # no [after:] tags → sequential default
        with patch("planner.decompose", return_value=raw):
            _, _, _, pl = decompose_to_dag("goal", self._make_adapter())

        assert pl == []

    def test_kwargs_forwarded_to_decompose(self):
        """Extra kwargs are passed through to decompose()."""
        from planner import decompose_to_dag

        called_with = {}

        def _fake_decompose(goal, adapter, max_steps, verbose=False, **kwargs):
            called_with.update(kwargs)
            return ["step A"]

        with patch("planner.decompose", side_effect=_fake_decompose):
            decompose_to_dag("goal", self._make_adapter(), lessons_context="hint")

        assert "lessons_context" in called_with
        assert called_with["lessons_context"] == "hint"


# ---------------------------------------------------------------------------
# _apply_prefixes — team: and pipeline: prefix parsing
# ---------------------------------------------------------------------------

class TestPrefixParsing:
    def test_team_prefix_sets_team_mode(self):
        from handle import _apply_prefixes
        result = _apply_prefixes("team: analyze the codebase")
        assert result.team_mode is True
        assert "team:" not in result.message.lower()
        assert "analyze the codebase" in result.message

    def test_team_prefix_sets_mid_model_tier(self):
        """team: mode defaults to mid model tier."""
        from handle import _apply_prefixes
        result = _apply_prefixes("team: do the thing")
        assert result.model_tier == "mid"

    def test_pipeline_prefix_sets_pipeline_mode(self):
        from handle import _apply_prefixes
        result = _apply_prefixes("pipeline: step1 | step2")
        assert result.pipeline_mode is True
        assert "pipeline:" not in result.message.lower()

    def test_team_stacks_with_strict(self):
        from handle import _apply_prefixes
        result = _apply_prefixes("strict: team: hard task")
        assert result.strict_mode is True
        assert result.team_mode is True

    def test_pipeline_stacks_with_ralph(self):
        from handle import _apply_prefixes
        result = _apply_prefixes("ralph: pipeline: s1 | s2")
        assert result.ralph_mode is True
        assert result.pipeline_mode is True


# ---------------------------------------------------------------------------
# preset_steps in run_agent_loop
# ---------------------------------------------------------------------------

class TestPresetSteps:
    def _make_loop_result(self, status="done"):
        from agent_loop import LoopResult, StepOutcome
        return LoopResult(
            loop_id="test-loop",
            project="test",
            goal="test goal",
            status=status,
            steps=[
                StepOutcome(index=1, text="step 1", status="done", result="r1"),
                StepOutcome(index=2, text="step 2", status="done", result="r2"),
            ],
            total_tokens_in=10,
            total_tokens_out=20,
            elapsed_ms=100,
        )

    def test_preset_steps_bypasses_decompose(self):
        """When preset_steps is provided, _decompose should not be called."""
        from agent_loop import run_agent_loop

        decompose_called = []

        def _fake_decompose(*a, **kw):
            decompose_called.append(True)
            return ["fallback step"]

        with (
            patch("agent_loop._decompose", side_effect=_fake_decompose),
            patch("agent_loop._execute_step", return_value={
                "status": "done", "result": "ok", "summary": "done",
                "tokens_in": 5, "tokens_out": 5, "inject_steps": [],
            }),
        ):
            result = run_agent_loop(
                "test goal",
                dry_run=False,
                preset_steps=["do step A", "do step B"],
            )

        assert not decompose_called, "decompose should not be called when preset_steps given"
        assert len(result.steps) == 2

    def test_preset_steps_executed_in_order(self):
        """preset_steps are executed in the order given."""
        from agent_loop import run_agent_loop

        executed = []

        def _fake_execute(**kwargs):
            executed.append(kwargs["step_text"])
            return {
                "status": "done", "result": "ok", "summary": "done",
                "tokens_in": 5, "tokens_out": 5, "inject_steps": [],
            }

        with (
            patch("agent_loop._decompose"),
            patch("agent_loop._execute_step", side_effect=_fake_execute),
        ):
            run_agent_loop(
                "test",
                dry_run=False,
                preset_steps=["alpha", "beta", "gamma"],
            )

        assert executed == ["alpha", "beta", "gamma"]

    def test_empty_preset_steps_falls_through_to_decompose(self):
        """Empty preset_steps list should fall through to normal decomposition."""
        from agent_loop import run_agent_loop

        decompose_called = []

        def _fake_decompose(*a, **kw):
            decompose_called.append(True)
            return ["step from decompose"]

        with (
            patch("agent_loop._decompose", side_effect=_fake_decompose),
            patch("agent_loop._execute_step", return_value={
                "status": "done", "result": "ok", "summary": "done",
                "tokens_in": 5, "tokens_out": 5, "inject_steps": [],
            }),
        ):
            run_agent_loop("test goal", dry_run=False, preset_steps=[])

        assert decompose_called, "empty preset_steps should fall through to decompose"

    def test_preset_steps_strips_blank_entries(self):
        """Blank entries in preset_steps are filtered out."""
        from agent_loop import run_agent_loop

        executed = []

        def _fake_execute(**kwargs):
            executed.append(kwargs["step_text"])
            return {
                "status": "done", "result": "ok", "summary": "done",
                "tokens_in": 5, "tokens_out": 5, "inject_steps": [],
            }

        with (
            patch("agent_loop._decompose"),
            patch("agent_loop._execute_step", side_effect=_fake_execute),
        ):
            run_agent_loop(
                "test",
                dry_run=False,
                preset_steps=["  ", "real step", "", "another step"],
            )

        assert executed == ["real step", "another step"]


# ---------------------------------------------------------------------------
# pipeline: prefix parsing helpers
# ---------------------------------------------------------------------------

class TestPipelineStepParsing:
    """Test the | splitting logic in handle() for pipeline: mode."""

    def test_pipe_split(self):
        """Verify | splits into multiple steps."""
        raw = "fetch data | analyze results | write report"
        steps = [s.strip() for s in raw.split("|") if s.strip()]
        assert steps == ["fetch data", "analyze results", "write report"]

    def test_single_step_no_pipe(self):
        """Single step without | returns one step."""
        raw = "fetch data from API"
        steps = [s.strip() for s in raw.split("|") if s.strip()]
        assert steps == ["fetch data from API"]

    def test_empty_segments_filtered(self):
        """Blank segments between pipes are filtered."""
        raw = "step1 | | step2 |   | step3"
        steps = [s.strip() for s in raw.split("|") if s.strip()]
        assert steps == ["step1", "step2", "step3"]


# ---------------------------------------------------------------------------
# _shape_steps — universal invariant gate
# ---------------------------------------------------------------------------

class TestShapeSteps:
    def _import(self):
        from agent_loop import _shape_steps
        return _shape_steps

    def test_passthrough_atomic_steps(self):
        _shape_steps = self._import()
        steps = ["research polymarket trends", "write a summary report"]
        assert _shape_steps(steps) == steps

    def test_splits_compound_exec_analyze(self):
        _shape_steps = self._import()
        steps = ["run pytest and analyze the failures"]
        result = _shape_steps(steps)
        assert len(result) == 2
        assert "analyze" in result[1].lower()

    def test_splits_one_of_three(self):
        _shape_steps = self._import()
        steps = [
            "fetch the data",
            "run the script and analyze output",
            "write a report",
        ]
        result = _shape_steps(steps)
        assert len(result) == 4  # one step split into 2

    def test_empty_list(self):
        _shape_steps = self._import()
        assert _shape_steps([]) == []

    def test_label_does_not_affect_output(self):
        _shape_steps = self._import()
        steps = ["run tests and analyze failures"]
        assert _shape_steps(steps, label="replan") == _shape_steps(steps)

    def test_all_compound_steps_split(self):
        _shape_steps = self._import()
        steps = [
            "run lint and analyze errors",
            "execute build and check results",
        ]
        result = _shape_steps(steps)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Registry completeness check
# ---------------------------------------------------------------------------

class TestPrefixRegistryCompleteness:
    def test_team_in_registry(self):
        from handle import _PREFIX_REGISTRY
        prefixes = [r.prefix for r in _PREFIX_REGISTRY]
        assert "team:" in prefixes

    def test_pipeline_in_registry(self):
        from handle import _PREFIX_REGISTRY
        prefixes = [r.prefix for r in _PREFIX_REGISTRY]
        assert "pipeline:" in prefixes
