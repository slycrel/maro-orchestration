"""Tests for step_exec — _parse_when, schedule_run, data pipeline enforcement."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---------------------------------------------------------------------------
# _parse_when
# ---------------------------------------------------------------------------

class TestParseWhen:
    def test_daily_at(self):
        from step_exec import _parse_when
        result = _parse_when("daily at 09:00")
        assert result == {"type": "daily", "time": "09:00"}

    def test_daily_at_no_leading_zero(self):
        from step_exec import _parse_when
        result = _parse_when("daily at 8:30")
        assert result == {"type": "daily", "time": "8:30"}

    def test_in_minutes(self):
        from step_exec import _parse_when
        result = _parse_when("in 30 minutes")
        assert result["type"] == "once"
        assert "at" in result

    def test_in_hours(self):
        from step_exec import _parse_when
        result = _parse_when("in 2 hours")
        assert result["type"] == "once"
        assert "at" in result

    def test_in_days(self):
        from step_exec import _parse_when
        result = _parse_when("in 1 day")
        assert result["type"] == "once"
        assert "at" in result

    def test_iso_datetime_z(self):
        from step_exec import _parse_when
        result = _parse_when("2026-04-02T09:00:00Z")
        assert result["type"] == "once"
        assert "09:00:00" in result["at"]

    def test_iso_datetime_offset(self):
        from step_exec import _parse_when
        result = _parse_when("2026-04-02T09:00:00+00:00")
        assert result["type"] == "once"
        assert "09:00:00" in result["at"]

    def test_unknown_falls_back(self):
        from step_exec import _parse_when
        result = _parse_when("whenever")
        # Fallback: once, 1 hour from now
        assert result["type"] == "once"
        assert "at" in result

    def test_plural_singular_minutes(self):
        from step_exec import _parse_when
        singular = _parse_when("in 1 minute")
        plural = _parse_when("in 5 minutes")
        assert singular["type"] == "once"
        assert plural["type"] == "once"


# ---------------------------------------------------------------------------
# schedule_run tool handler in execute_step
# ---------------------------------------------------------------------------

class _ScheduleRunAdapter:
    """Minimal adapter that returns a schedule_run tool call."""
    model_key = "test"

    def __init__(self, goal, when, note=""):
        self._goal = goal
        self._when = when
        self._note = note

    def complete(self, messages, *, tools=None, tool_choice="auto",
                 max_tokens=4096, temperature=0.3, **kwargs):
        from llm import LLMResponse, ToolCall
        args = {"goal": self._goal, "when": self._when}
        if self._note:
            args["note"] = self._note
        return LLMResponse(
            content="",
            tool_calls=[ToolCall(name="schedule_run", arguments=args)],
            stop_reason="tool_use",
            input_tokens=50,
            output_tokens=20,
        )


class TestScheduleRunTool:
    def test_schedule_run_daily(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Check Polymarket top markets",
            when="daily at 08:00",
        )
        result = execute_step(
            goal="Set up daily research routine",
            step_text="Schedule daily Polymarket check at 08:00",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Polymarket" in result["result"]
        assert "job_id" in result["result"]

    def test_schedule_run_in_hours(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Re-analyze market after data update",
            when="in 2 hours",
        )
        result = execute_step(
            goal="Watch for market update",
            step_text="Schedule re-analysis in 2 hours",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Re-analyze" in result["result"]

    def test_schedule_run_with_note(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Run weekly digest",
            when="daily at 07:30",
            note="Include top 5 markets",
        )
        result = execute_step(
            goal="Set up weekly digest",
            step_text="Schedule weekly digest",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert "Include top 5 markets" in result["result"]

    def test_schedule_run_persists_job(self, tmp_path, monkeypatch):
        """Job should actually be saved to jobs.json."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS

        adapter = _ScheduleRunAdapter(
            goal="Monitor BTC price",
            when="in 10 minutes",
        )
        execute_step(
            goal="Set up monitoring",
            step_text="Schedule BTC monitoring",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        # Verify the job was persisted
        from scheduler import list_jobs
        jobs = list_jobs()
        assert any("BTC" in j["goal"] for j in jobs)


# ---------------------------------------------------------------------------
# Data pipeline enforcement helpers
# ---------------------------------------------------------------------------

class TestIsDataHeavyStep:
    def test_polymarket_cli_detected(self):
        from step_exec import _is_data_heavy_step
        assert _is_data_heavy_step("Run polymarket-cli list to get all markets")

    def test_fetch_all_detected(self):
        from step_exec import _is_data_heavy_step
        assert _is_data_heavy_step("Fetch all events from the API")

    def test_list_all_detected(self):
        from step_exec import _is_data_heavy_step
        assert _is_data_heavy_step("List all trades from the database")

    def test_requests_get_detected(self):
        from step_exec import _is_data_heavy_step
        assert _is_data_heavy_step("Use requests.get to fetch the full data")

    def test_normal_step_not_detected(self):
        from step_exec import _is_data_heavy_step
        assert not _is_data_heavy_step("Summarize the research findings")

    def test_analysis_step_not_detected(self):
        from step_exec import _is_data_heavy_step
        assert not _is_data_heavy_step("Analyze the top performing wallets by return rate")

    def test_case_insensitive(self):
        from step_exec import _is_data_heavy_step
        assert _is_data_heavy_step("FETCH ALL records from the endpoint")


class TestResultLooksLikeRawDump:
    def test_short_result_not_flagged(self):
        from step_exec import _result_looks_like_raw_dump
        assert not _result_looks_like_raw_dump("Short clean summary of findings.")

    def test_long_clean_text_not_flagged(self):
        from step_exec import _result_looks_like_raw_dump
        # Long but no brace density or long lines
        clean = "Finding: " + "A" * 2100
        assert not _result_looks_like_raw_dump(clean)

    def test_high_brace_density_flagged(self):
        from step_exec import _result_looks_like_raw_dump
        # 35+ braces and over 2000 chars = JSON dump
        raw = "{" * 20 + "key: value" * 10 + "}" * 20
        padded = raw + "x" * 2000  # ensure over char threshold
        assert _result_looks_like_raw_dump(padded)

    def test_many_long_lines_flagged(self):
        from step_exec import _result_looks_like_raw_dump
        # 6 lines each 350 chars = raw text dump
        long_line = "A" * 350
        result = "\n".join([long_line] * 8)
        assert _result_looks_like_raw_dump(result)

    def test_exactly_at_threshold_not_flagged(self):
        from step_exec import _result_looks_like_raw_dump
        # Right at the char threshold — just under
        result = "x" * 1999
        assert not _result_looks_like_raw_dump(result)


class TestDataPipelineEnforcementInExecuteStep:
    """Integration: data-heavy steps get pipeline block injected; raw results get flagged."""

    def _complete_step_adapter(self, result_text: str):
        """Adapter that returns a complete_step call with given result."""
        from llm import LLMResponse, ToolCall
        resp = LLMResponse(
            content="",
            tool_calls=[ToolCall(name="complete_step", arguments={
                "result": result_text,
                "summary": "step done",
            })],
            stop_reason="tool_use",
            input_tokens=50,
            output_tokens=20,
        )
        adapter = MagicMock()
        adapter.complete.return_value = resp
        return adapter

    def test_data_heavy_step_injects_enforcement(self, tmp_path, monkeypatch):
        """When step is data-heavy, user_msg contains the pipeline enforcement block."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        captured_msgs = []
        adapter = MagicMock()

        from llm import LLMResponse, ToolCall
        adapter.complete.side_effect = lambda msgs, **kw: (
            captured_msgs.append(msgs) or
            LLMResponse("", [ToolCall("complete_step", {"result": "ok", "summary": "done"})],
                        "tool_use", 10, 10)
        )

        from step_exec import execute_step, EXECUTE_TOOLS
        execute_step(
            goal="Research markets",
            step_text="Fetch all events from the polymarket-cli API",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert captured_msgs, "adapter.complete never called"
        user_content = captured_msgs[0][-1].content  # last message is user msg
        assert "DATA PIPELINE ENFORCEMENT" in user_content

    def test_non_data_heavy_step_no_injection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        captured_msgs = []
        adapter = MagicMock()
        from llm import LLMResponse, ToolCall
        adapter.complete.side_effect = lambda msgs, **kw: (
            captured_msgs.append(msgs) or
            LLMResponse("", [ToolCall("complete_step", {"result": "clean summary", "summary": "done"})],
                        "tool_use", 10, 10)
        )

        from step_exec import execute_step, EXECUTE_TOOLS
        execute_step(
            goal="Research markets",
            step_text="Summarize the top three findings from prior steps",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        user_content = captured_msgs[0][-1].content
        assert "DATA PIPELINE ENFORCEMENT" not in user_content

    def test_raw_dump_result_gets_flagged(self, tmp_path, monkeypatch):
        """If the agent ignores pipeline enforcement and returns raw output, flag it."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        # 35 braces + long result (>2000 chars) = raw dump signal
        raw_result = "{" * 20 + "data" * 600 + "}" * 20

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter = self._complete_step_adapter(raw_result)
        result = execute_step(
            goal="Get market data",
            step_text="Fetch all markets",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert result["status"] == "done"
        assert result["result"].startswith("[RAW_OUTPUT_DETECTED]")

    def test_clean_result_not_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))

        clean_result = "Top 3 markets by volume: BTC-USD ($1.2B), ETH-USD ($0.8B), SOL-USD ($0.3B)."

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter = self._complete_step_adapter(clean_result)
        result = execute_step(
            goal="Summarize markets",
            step_text="Summarize the top markets",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert not result["result"].startswith("[RAW_OUTPUT_DETECTED]")


# ---------------------------------------------------------------------------
# _classify_step
# ---------------------------------------------------------------------------

class TestClassifyStep:
    def test_exec_command_pytest(self):
        from step_exec import _classify_step
        assert _classify_step("Run pytest -q and save output to a file") == "exec_command"

    def test_exec_command_grep(self):
        from step_exec import _classify_step
        assert _classify_step("grep for TODO comments in src/") == "exec_command"

    def test_exec_command_make(self):
        from step_exec import _classify_step
        assert _classify_step("make build and capture output") == "exec_command"

    def test_read_artifact(self):
        from step_exec import _classify_step
        assert _classify_step("Read the captured output and count failures") == "read_artifact"

    def test_inspect_code(self):
        from step_exec import _classify_step
        assert _classify_step("inspect the source files for missing imports") == "inspect_code"

    def test_analyze(self):
        from step_exec import _classify_step
        assert _classify_step("Analyze the test output for failure patterns") == "analyze"

    def test_synthesize(self):
        from step_exec import _classify_step
        assert _classify_step("Synthesize findings into a final report") == "synthesize"

    def test_general_fallback(self):
        from step_exec import _classify_step
        assert _classify_step("Set up the project workspace") == "general"


class TestArtifactMaterialization:
    """exec_command steps get artifact path injected into user_msg."""

    def _adapter_capturing_prompt(self):
        """Returns adapter that captures the user message it received."""
        from llm import LLMResponse, ToolCall
        captured = {}

        class _Cap:
            def complete(self, messages, **kw):
                captured["user"] = next(
                    (m.content for m in reversed(messages) if m.role == "user"), ""
                )
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={"result": "Saved to artifacts/step-1-output.txt. Exit 0.", "summary": "done"},
                    )],
                )

        return _Cap(), captured

    def test_exec_step_injects_artifact_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_prompt()
        execute_step(
            goal="Review repo",
            step_text="Run pytest -q and save output",
            step_num=1,
            total_steps=3,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
            project_dir=str(tmp_path / "projects" / "review-repo"),
        )
        assert "ARTIFACT MATERIALIZATION" in captured["user"]
        assert "step-1-output.txt" in captured["user"]

    def test_non_exec_step_no_artifact_injection(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_prompt()
        execute_step(
            goal="Review repo",
            step_text="Analyze the failure patterns from prior step",
            step_num=2,
            total_steps=3,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
            project_dir=str(tmp_path / "projects" / "review-repo"),
        )
        assert "ARTIFACT MATERIALIZATION" not in captured["user"]

    def test_step_type_label_in_user_msg(self, tmp_path, monkeypatch):
        """Step type is embedded in the user message for the executor."""
        monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_prompt()
        execute_step(
            goal="Review repo",
            step_text="Run pytest -q and save output",
            step_num=1,
            total_steps=3,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
            project_dir=str(tmp_path / "projects" / "review-repo"),
        )
        assert "[exec_command]" in captured["user"]


# ---------------------------------------------------------------------------
# Phase 62: Anti-hallucination prompt + cross-ref detection
# ---------------------------------------------------------------------------

class TestAntiHallucinationPrompt:
    """Verify EXECUTE_SYSTEM contains anti-hallucination and NEED_INFO instructions."""

    def test_anti_hallucination_in_system_prompt(self):
        from step_exec import EXECUTE_SYSTEM
        assert "ANTI-HALLUCINATION" in EXECUTE_SYSTEM
        assert "[UNVERIFIED]" in EXECUTE_SYSTEM
        assert "NEVER guess file paths" in EXECUTE_SYSTEM

    def test_need_info_in_system_prompt(self):
        from step_exec import EXECUTE_SYSTEM
        assert "NEED_INFO" in EXECUTE_SYSTEM
        assert "inject_steps" in EXECUTE_SYSTEM

    def test_prior_step_data_warning(self):
        from step_exec import EXECUTE_SYSTEM
        assert "never invent or guess" in EXECUTE_SYSTEM


class TestSpecificClaimDetection:
    """Test _has_specific_claims heuristic for cross-ref triggering."""

    def test_detects_file_path_and_line_number(self):
        from step_exec import _has_specific_claims
        assert _has_specific_claims(
            "The bug is in src/agent_loop.py at line 250. "
            "The function _handle_blocked_step returns wrong value."
        )

    def test_detects_function_names_and_class(self):
        from step_exec import _has_specific_claims
        assert _has_specific_claims(
            "The class LoopContext has a method called run_agent_loop "
            "which calls the function decompose at line 500."
        )

    def test_ignores_generic_text(self):
        from step_exec import _has_specific_claims
        assert not _has_specific_claims(
            "The system seems to work well overall. Performance is good."
        )

    def test_ignores_empty_and_short(self):
        from step_exec import _has_specific_claims
        assert not _has_specific_claims("")
        assert not _has_specific_claims("short")


class TestVerifyStepWithCrossRef:
    """verify_step_with_cross_ref: cross-ref triggered only on specific claims + passing base."""

    def _make_adapter(self, monkeypatch):
        """Patch VerificationAgent so verify_step returns a controllable verdict."""
        from unittest.mock import MagicMock, patch
        adapter = MagicMock()
        return adapter

    def test_no_specific_claims_skips_cross_ref(self, monkeypatch):
        """Generic result without specific claims: cross-ref not called."""
        from unittest.mock import MagicMock, patch
        from step_exec import verify_step_with_cross_ref

        mock_va = MagicMock()
        mock_va.verify_step.return_value = MagicMock(passed=True, reason="ok", confidence=0.9)

        cross_ref_called = []

        def _mock_cross_ref(text, **kw):
            cross_ref_called.append(True)
            return MagicMock(disputed_claims=[])

        with patch("verification_agent.VerificationAgent", return_value=mock_va), \
             patch.dict("sys.modules", {"cross_ref": MagicMock(run_cross_ref=_mock_cross_ref)}):
            result = verify_step_with_cross_ref(
                "summarize findings",
                "The system works well overall. No specific issues found.",
                MagicMock(),
            )

        assert result["passed"] is True
        assert not cross_ref_called

    def test_specific_claims_with_disputes_annotated(self, monkeypatch):
        """If cross-ref finds disputes, base verdict gets cross_ref_disputes annotation."""
        from unittest.mock import MagicMock, patch
        from step_exec import verify_step_with_cross_ref

        mock_va = MagicMock()
        mock_va.verify_step.return_value = MagicMock(passed=True, reason="ok", confidence=0.9)

        disputed = MagicMock()
        disputed.claim = "The function nonexistent_func was added at line 500"
        disputed.status = "DISPUTED"
        mock_cross_ref_report = MagicMock(disputed_claims=[disputed])

        mock_cross_ref = MagicMock()
        mock_cross_ref.run_cross_ref = MagicMock(return_value=mock_cross_ref_report)

        result_text = (
            "The function nonexistent_func was added to src/agent_loop.py at line 500. "
            "Class Director now calls this method. Variable count is set to zero."
        )

        with patch("verification_agent.VerificationAgent", return_value=mock_va), \
             patch.dict("sys.modules", {"cross_ref": mock_cross_ref}):
            result = verify_step_with_cross_ref("implement feature", result_text, MagicMock())

        assert result["passed"] is True  # still passes (annotation only, not block)
        assert "cross_ref_disputes" in result

    def test_cross_ref_exception_non_fatal(self, monkeypatch):
        """cross-ref failure is swallowed silently."""
        from unittest.mock import MagicMock, patch
        from step_exec import verify_step_with_cross_ref

        mock_va = MagicMock()
        mock_va.verify_step.return_value = MagicMock(passed=True, reason="ok", confidence=0.9)

        mock_cross_ref = MagicMock()
        mock_cross_ref.run_cross_ref = MagicMock(side_effect=RuntimeError("cross-ref broken"))

        result_text = (
            "The function nonexistent_func was added at line 500. "
            "Class Director calls this method. Variable count is set."
        )

        with patch("verification_agent.VerificationAgent", return_value=mock_va), \
             patch.dict("sys.modules", {"cross_ref": mock_cross_ref}):
            result = verify_step_with_cross_ref("step", result_text, MagicMock())

        # Should not raise, should return base verdict
        assert "passed" in result


# ---------------------------------------------------------------------------
# Phase 62: Shared artifact layer
# ---------------------------------------------------------------------------

class TestArtifactSchema:
    """Verify complete_step tool accepts artifacts field."""

    def test_artifacts_field_in_tool_schema(self):
        from step_exec import EXECUTE_TOOLS
        complete_step = next(t for t in EXECUTE_TOOLS if t["name"] == "complete_step")
        props = complete_step["parameters"]["properties"]
        assert "artifacts" in props
        assert props["artifacts"]["type"] == "object"

    def test_artifact_injection_in_system_prompt(self):
        from step_exec import EXECUTE_SYSTEM
        assert "Artifacts from prior steps" in EXECUTE_SYSTEM
        assert "artifacts" in EXECUTE_SYSTEM


class TestArtifactContextInjection:
    """Verify artifacts from shared_ctx are injected into step context."""

    def _adapter_capturing_prompt(self):
        from llm import LLMResponse, ToolCall
        captured = {}

        class _Cap:
            def complete(self, messages, **kw):
                captured["user"] = next(
                    (m.content for m in reversed(messages) if m.role == "user"), ""
                )
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={"result": "done", "summary": "done"},
                    )],
                )

        return _Cap(), captured

    def test_artifacts_injected_into_step_context(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_prompt()

        shared = {
            "artifact:1:file_list": '["src/main.py", "src/utils.py"]',
            "artifact:2:grep_results": "3 matches found in auth.py",
            "step:1:some step": "summary text",  # not an artifact
        }
        execute_step(
            goal="Test goal",
            step_text="Review the files found earlier",
            step_num=3,
            total_steps=5,
            completed_context=["Step 1: found files"],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
            shared_ctx=shared,
        )
        assert "Artifacts from prior steps" in captured["user"]
        assert "file_list (from step 1)" in captured["user"]
        assert "grep_results (from step 2)" in captured["user"]
        # Non-artifact keys should NOT be in artifacts block
        assert "some step" not in captured["user"].split("Artifacts from prior steps")[1].split("Completed")[0]

    def test_no_artifacts_block_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_ORCH_ROOT", str(tmp_path))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_prompt()

        execute_step(
            goal="Test goal",
            step_text="Do something",
            step_num=1,
            total_steps=3,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
            shared_ctx={},
        )
        assert "Artifacts from prior steps" not in captured["user"]


# ---------------------------------------------------------------------------
# Long-running timeout classification
# ---------------------------------------------------------------------------

class TestLongRunningTimeout:
    """Tests for per-step timeout injection in execute_step."""

    def _adapter_capturing_kwargs(self):
        """Returns adapter that captures the kwargs passed to complete()."""
        from llm import LLMResponse, ToolCall
        captured = {}

        class _Cap:
            def complete(self, messages, **kw):
                captured["kw"] = kw
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="complete_step",
                        arguments={"result": "done", "summary": "ok"},
                    )],
                )

        return _Cap(), captured

    def test_pytest_step_gets_long_timeout(self, tmp_path, monkeypatch):
        """A step containing 'pytest' gets the long-running timeout (1800s default)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.delenv("POE_LONG_RUNNING_TIMEOUT", raising=False)

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_kwargs()
        execute_step(
            goal="test goal",
            step_text="Run pytest tests/test_foo.py -q and check results",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert "timeout" in captured.get("kw", {})
        assert captured["kw"]["timeout"] == 1800

    def test_full_suite_step_gets_doubled_timeout(self, tmp_path, monkeypatch):
        """A step with 'tests/' in it gets 2× the long-running timeout."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.delenv("POE_LONG_RUNNING_TIMEOUT", raising=False)

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_kwargs()
        execute_step(
            goal="test goal",
            step_text="Run pytest tests/ -q to verify no regressions",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert "timeout" in captured.get("kw", {})
        assert captured["kw"]["timeout"] == 3600

    def test_non_long_running_step_no_timeout(self, tmp_path, monkeypatch):
        """A normal analysis step does not inject a timeout."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_kwargs()
        execute_step(
            goal="test goal",
            step_text="Analyze the failure patterns and write a summary",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert "timeout" not in captured.get("kw", {})

    def test_poe_long_running_timeout_env_override(self, tmp_path, monkeypatch):
        """POE_LONG_RUNNING_TIMEOUT overrides the default 1800s for long-running steps."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("POE_LONG_RUNNING_TIMEOUT", "600")

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_kwargs()
        execute_step(
            goal="test goal",
            step_text="Run pytest tests/test_foo.py -q",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert captured["kw"]["timeout"] == 600

    def test_docker_step_gets_long_timeout(self, tmp_path, monkeypatch):
        """Docker build steps also get the long-running timeout."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.delenv("POE_LONG_RUNNING_TIMEOUT", raising=False)

        from step_exec import execute_step, EXECUTE_TOOLS
        adapter, captured = self._adapter_capturing_kwargs()
        execute_step(
            goal="deploy app",
            step_text="Build the docker image and push to registry",
            step_num=1,
            total_steps=3,
            completed_context=[],
            adapter=adapter,
            tools=EXECUTE_TOOLS,
        )
        assert captured["kw"]["timeout"] == 1800
