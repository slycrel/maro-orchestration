"""Tests for workers.py — dispatch routing, worker inference, crew sizing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers import (
    WorkerResult,
    dispatch_worker,
    infer_worker_type,
    infer_crew_size,
    WORKER_RESEARCH,
    WORKER_BUILD,
    WORKER_OPS,
    WORKER_GENERAL,
    WORKER_TYPES,
    _load_persona,
)


# ---------------------------------------------------------------------------
# infer_worker_type — keyword routing
# ---------------------------------------------------------------------------

class TestInferWorkerType:
    def test_research_keywords(self):
        assert infer_worker_type("research the market") == WORKER_RESEARCH
        assert infer_worker_type("analyze stock trends") == WORKER_RESEARCH
        assert infer_worker_type("investigate the root cause") == WORKER_RESEARCH

    def test_build_keywords(self):
        assert infer_worker_type("build a REST API") == WORKER_BUILD
        assert infer_worker_type("implement the parser") == WORKER_BUILD
        assert infer_worker_type("write a Python script") == WORKER_BUILD

    def test_ops_keywords(self):
        assert infer_worker_type("deploy to production") == WORKER_OPS
        assert infer_worker_type("check status of services") == WORKER_OPS
        assert infer_worker_type("configure the firewall") == WORKER_OPS

    def test_no_keywords_returns_general(self):
        assert infer_worker_type("do the thing") == WORKER_GENERAL
        assert infer_worker_type("") == WORKER_GENERAL

    def test_case_insensitive(self):
        assert infer_worker_type("RESEARCH the market") == WORKER_RESEARCH
        assert infer_worker_type("Build A Thing") == WORKER_BUILD

    def test_mixed_keywords_highest_score_wins(self):
        # "research and analyze" has 2 research keywords, "build" has 1
        assert infer_worker_type("research and analyze the build") == WORKER_RESEARCH


# ---------------------------------------------------------------------------
# infer_crew_size
# ---------------------------------------------------------------------------

class TestInferCrewSize:
    def test_short_directive_one_worker(self):
        assert infer_crew_size("do it") == 1

    def test_simple_keyword_one_worker(self):
        assert infer_crew_size("give me a quick summary of the project") == 1

    def test_medium_directive_two_workers(self):
        directive = " ".join(["word"] * 15)
        assert infer_crew_size(directive) == 2

    def test_comprehensive_keyword_three_workers(self):
        assert infer_crew_size("provide a comprehensive analysis of the system") == 3

    def test_exhaustive_keyword_four_workers(self):
        assert infer_crew_size("do a thorough audit of everything") == 4

    def test_long_directive_four_workers(self):
        directive = " ".join(["word"] * 55)
        assert infer_crew_size(directive) == 4


# ---------------------------------------------------------------------------
# dispatch_worker — dry run
# ---------------------------------------------------------------------------

class TestDispatchWorkerDryRun:
    def test_dry_run_returns_done(self):
        result = dispatch_worker(WORKER_RESEARCH, "test ticket", dry_run=True)
        assert isinstance(result, WorkerResult)
        assert result.status == "done"
        assert result.worker_type == WORKER_RESEARCH
        assert "test ticket" in result.result

    def test_each_worker_type_dispatches(self):
        for wtype in WORKER_TYPES:
            result = dispatch_worker(wtype, f"ticket for {wtype}", dry_run=True)
            assert result.status == "done"
            assert result.worker_type == wtype

    def test_unknown_worker_type_falls_back_to_general(self):
        result = dispatch_worker("nonexistent", "ticket", dry_run=True)
        assert result.worker_type == WORKER_GENERAL
        assert result.status == "done"

    def test_none_adapter_uses_dry_run(self):
        result = dispatch_worker(WORKER_BUILD, "build something", adapter=None)
        assert result.status == "done"


# ---------------------------------------------------------------------------
# dispatch_worker — with mock adapter
# ---------------------------------------------------------------------------

class TestDispatchWorkerWithAdapter:
    def test_deliver_result_tool_call(self):
        from llm import LLMResponse, ToolCall

        class MockAdapter:
            model_key = "test"
            def complete(self, messages, **kwargs):
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="deliver_result",
                        arguments={"result": "the research findings"},
                    )],
                    stop_reason="tool_use",
                    input_tokens=100,
                    output_tokens=50,
                )

        result = dispatch_worker(WORKER_RESEARCH, "research something", adapter=MockAdapter())
        assert result.status == "done"
        assert result.result == "the research findings"
        assert result.tokens_in == 100

    def test_flag_blocked_tool_call(self):
        from llm import LLMResponse, ToolCall

        class BlockedAdapter:
            model_key = "test"
            def complete(self, messages, **kwargs):
                return LLMResponse(
                    content="",
                    tool_calls=[ToolCall(
                        name="flag_blocked",
                        arguments={"reason": "no access", "partial": "got this far"},
                    )],
                    stop_reason="tool_use",
                    input_tokens=80,
                    output_tokens=30,
                )

        result = dispatch_worker(WORKER_BUILD, "build something", adapter=BlockedAdapter())
        assert result.status == "blocked"
        assert result.stuck_reason == "no access"
        assert result.result == "got this far"

    def test_adapter_exception_returns_blocked(self):
        class FailAdapter:
            model_key = "test"
            def complete(self, messages, **kwargs):
                raise ConnectionError("network down")

        result = dispatch_worker(WORKER_OPS, "check status", adapter=FailAdapter())
        assert result.status == "blocked"
        assert "network down" in result.stuck_reason

    def test_content_fallback_when_no_tool_calls(self):
        from llm import LLMResponse

        class ContentOnlyAdapter:
            model_key = "test"
            def complete(self, messages, **kwargs):
                return LLMResponse(
                    content="Here is a detailed analysis of the topic with many findings.",
                    tool_calls=[],
                    stop_reason="end_turn",
                    input_tokens=100,
                    output_tokens=200,
                )

        result = dispatch_worker(WORKER_RESEARCH, "research topic", adapter=ContentOnlyAdapter())
        assert result.status == "done"
        assert "detailed analysis" in result.result


# ---------------------------------------------------------------------------
# _load_persona
# ---------------------------------------------------------------------------

class TestLoadPersona:
    def test_each_worker_type_has_persona(self):
        for wtype in WORKER_TYPES:
            persona = _load_persona(wtype)
            assert isinstance(persona, str)
            assert len(persona) > 50

    def test_unknown_type_gets_general_persona(self):
        persona = _load_persona("nonexistent")
        assert "General Worker" in persona
