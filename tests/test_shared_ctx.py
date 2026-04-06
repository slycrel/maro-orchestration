"""Tests for Team-level SharedMemory — shared_ctx threading through agent_loop → step_exec → team."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from team import create_team_worker, format_team_result_for_injection


# ---------------------------------------------------------------------------
# team.create_team_worker — shared_ctx injection
# ---------------------------------------------------------------------------


class TestCreateTeamWorkerSharedCtx:
    def test_dry_run_ignores_shared_ctx(self):
        result = create_team_worker("analyst", "analyze X", dry_run=True, shared_ctx={"k": "v"})
        assert result.status == "done"
        assert "dry-run" in result.result

    def test_shared_ctx_injected_into_user_message(self):
        """Worker user message should include shared context entries."""
        captured = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                captured["user_msg"] = messages[-1].content
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(
                            name="deliver_result",
                            arguments={"result": "done"},
                        )
                    ],
                )

        shared = {"market-analyst:get prices": "BTC is at 80k"}
        create_team_worker("synthesizer", "summarize findings", adapter=_Adapter(), shared_ctx=shared)
        assert "BTC is at 80k" in captured["user_msg"]
        assert "Relevant context from prior steps" in captured["user_msg"]

    def test_empty_shared_ctx_no_extra_block(self):
        """Empty shared_ctx should not inject any block."""
        captured = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                captured["user_msg"] = messages[-1].content
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(name="deliver_result", arguments={"result": "x"})
                    ],
                )

        create_team_worker("analyst", "do something", adapter=_Adapter(), shared_ctx={})
        assert "Relevant context from prior steps" not in captured["user_msg"]

    def test_none_shared_ctx_no_extra_block(self):
        captured = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                captured["user_msg"] = messages[-1].content
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(name="deliver_result", arguments={"result": "x"})
                    ],
                )

        create_team_worker("analyst", "do something", adapter=_Adapter(), shared_ctx=None)
        assert "Shared context" not in captured["user_msg"]

    def test_shared_ctx_capped_at_last_five_entries(self):
        """Only last 5 shared_ctx entries should be injected."""
        captured = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                captured["user_msg"] = messages[-1].content
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(name="deliver_result", arguments={"result": "x"})
                    ],
                )

        shared = {f"key_{i}": f"value_{i}" for i in range(10)}
        create_team_worker("analyst", "do something", adapter=_Adapter(), shared_ctx=shared)
        # Count occurrences of "value_" — should be at most 5
        assert captured["user_msg"].count("value_") <= 5


# ---------------------------------------------------------------------------
# step_exec — shared_ctx passed to create_team_worker and written back
# ---------------------------------------------------------------------------


class TestStepExecSharedCtxWriteback:
    def test_successful_worker_writes_to_shared_ctx(self):
        """After a successful create_team_worker, result is written to shared_ctx."""
        shared = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(
                            name="create_team_worker",
                            arguments={"role": "analyst", "task": "analyze market"},
                        )
                    ],
                )

        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        def _fake_create_worker(role, task, **kw):
            return SimpleNamespace(
                status="done",
                result="market is bullish",
                stuck_reason=None,
                tokens_in=5,
                tokens_out=10,
                role=role,
                task=task,
            )

        with mock.patch("team.create_team_worker", _fake_create_worker):
            with mock.patch("team.format_team_result_for_injection", return_value="[worker] market is bullish"):
                execute_step(
                    goal="analyze the market",
                    step_text="analyze market",
                    step_num=1,
                    total_steps=3,
                    completed_context=[],
                    adapter=_Adapter(),
                    tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
                    shared_ctx=shared,
                )

        # shared_ctx should have an entry keyed by role:task prefix
        assert any("analyst" in k for k in shared)
        assert any("market is bullish" in v for v in shared.values())

    def test_blocked_worker_does_not_write_to_shared_ctx(self):
        """Blocked worker result should not pollute shared_ctx."""
        shared = {}

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(
                            name="create_team_worker",
                            arguments={"role": "analyst", "task": "analyze market"},
                        )
                    ],
                )

        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        def _fake_create_worker(role, task, **kw):
            return SimpleNamespace(
                status="blocked",
                result="",
                stuck_reason="no data",
                tokens_in=5,
                tokens_out=10,
                role=role,
                task=task,
            )

        with mock.patch("team.create_team_worker", _fake_create_worker):
            with mock.patch("team.format_team_result_for_injection", return_value="[blocked]"):
                execute_step(
                    goal="analyze the market",
                    step_text="analyze market",
                    step_num=1,
                    total_steps=3,
                    completed_context=[],
                    adapter=_Adapter(),
                    tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
                    shared_ctx=shared,
                )

        assert len(shared) == 0

    def test_shared_ctx_none_does_not_crash(self):
        """shared_ctx=None should not cause an error in execute_step."""
        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                return SimpleNamespace(
                    content="",
                    input_tokens=5,
                    output_tokens=10,
                    tool_calls=[
                        SimpleNamespace(
                            name="complete_step",
                            arguments={"result": "done", "summary": "completed"},
                        )
                    ],
                )

        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        outcome = execute_step(
            goal="test goal",
            step_text="do something",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=_Adapter(),
            tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            shared_ctx=None,
        )
        assert outcome["status"] == "done"


# ---------------------------------------------------------------------------
# firewall_shared_ctx (subagent context firewall for TeamCreateTool)
# ---------------------------------------------------------------------------

class TestFirewallSharedCtx:
    def test_returns_relevant_entries(self):
        from team import firewall_shared_ctx
        ctx = {
            "step:1:fetch BTC prices": "BTC price is 80000",
            "step:2:analyze portfolio": "portfolio has 3 assets",
            "step:3:compute ETH volume": "ETH volume 1M",
        }
        result = firewall_shared_ctx("BTC price analysis", ctx)
        # BTC-related entry should rank highest
        assert "step:1:fetch BTC prices" in result

    def test_max_entries_respected(self):
        from team import firewall_shared_ctx
        ctx = {f"key{i}": f"value about topic {i}" for i in range(20)}
        result = firewall_shared_ctx("some task", ctx, max_entries=3)
        assert len(result) <= 3

    def test_values_capped_at_max_chars(self):
        from team import firewall_shared_ctx
        ctx = {"key": "x" * 1000}
        result = firewall_shared_ctx("task", ctx, max_chars_per_entry=100)
        assert all(len(v) <= 100 for v in result.values())

    def test_empty_ctx_returns_empty(self):
        from team import firewall_shared_ctx
        assert firewall_shared_ctx("any task", {}) == {}

    def test_irrelevant_entries_excluded_when_cap_small(self):
        from team import firewall_shared_ctx
        ctx = {
            "step:1:BTC market data": "bitcoin price 80k",
            "step:2:ETH market data": "ethereum price 4k",
            "step:3:deploy kubernetes": "k8s pod started",
        }
        result = firewall_shared_ctx("analyze ETH price", ctx, max_entries=1)
        # ETH-related should rank higher than k8s for an ETH task
        assert "step:2:ETH market data" in result
