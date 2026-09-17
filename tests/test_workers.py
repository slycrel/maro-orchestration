"""Tests for workers.py — dispatch routing, worker inference, crew sizing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from workers import (
    WorkerResult,
    dispatch_worker,
    infer_worker_type,
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


# infer_crew_size tests removed 2026-07-02 — function deleted (zero
# production callers). See docs/REFACTOR_PLAN.md Tier 1.

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

    def test_require_mode_incapable_backend_blocks_not_host_runs(
            self, monkeypatch):
        # 2026-08-13 review residual: executor.container=require with a
        # backend that can't containerize must refuse the ticket, never
        # silently run it on the host (require is isolation-or-nothing).
        import container_exec as ce
        monkeypatch.setattr(ce, "container_mode", lambda: "require")

        class HostOnlyAdapter:
            backend = "codex"
            model_key = "test"
            container_capable = False
            def complete(self, messages, **kwargs):
                raise AssertionError(
                    "executor call reached a non-containerizable backend "
                    "under require")

        result = dispatch_worker(WORKER_OPS, "check status",
                                 adapter=HostOnlyAdapter())
        assert result.status == "blocked"
        assert "require" in result.stuck_reason
        assert "containerize" in result.stuck_reason

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


# ---------------------------------------------------------------------------
# A/B experiment: worker memory slice
# ---------------------------------------------------------------------------

class TestWorkerMemorySlice:
    def test_worker_result_has_memory_slice_injected_field(self):
        """WorkerResult includes memory_slice_injected field."""
        result = dispatch_worker(WORKER_RESEARCH, "test ticket", dry_run=True)
        assert hasattr(result, "memory_slice_injected")
        assert isinstance(result.memory_slice_injected, bool)

    def test_memory_slice_injected_defaults_to_false(self):
        """Field defaults to False when not explicitly set."""
        result = dispatch_worker(WORKER_BUILD, "build something", dry_run=True)
        assert result.memory_slice_injected is False

    def test_memory_slice_injected_can_be_set(self):
        """Field can be set after dispatch."""
        result = dispatch_worker(WORKER_OPS, "check status", dry_run=True)
        result.memory_slice_injected = True
        assert result.memory_slice_injected is True


def test_adapter_failure_carries_the_structured_error_class():
    # Review round 2 (2026-09-13): the text-only stuck_reason destroyed the
    # typed refusal; the director reads error_class through the loop's seam.
    from llm_errors import BackendError, ErrorInfo, CONTAINER_AUTH
    from workers import dispatch_worker

    class _Refusing:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            raise BackendError(ErrorInfo(error_class=CONTAINER_AUTH, backend="subprocess",
                                         retryable=False, failover=False,
                                         user_action="re-seed", detail="breaker tripped"))
    res = dispatch_worker("research", "look at the inbox", adapter=_Refusing())
    assert res.status == "blocked" and res.blocked_origin == "adapter"
    assert res.error_class == "container_auth"

def test_worker_kill_preserves_partial_output_and_usage(monkeypatch, tmp_path):
    # Review round 8: the worker lane copied the error class but returned
    # result="" and zero tokens — a runaway kill's measured ingest and the
    # only record of what the ticket did before dying were dropped.
    from workers import dispatch_worker
    from llm_errors import kill_evidence
    exc = RuntimeError("token runaway: killed at 100000 input tokens")
    exc.maro_partial_output = "partial work already performed"
    exc.fresh_input_tokens = 100000
    exc.estimated_cost_usd = 1.25
    assert kill_evidence(exc) == ("[partial output before kill]\npartial work already performed", 100000, 1.25)
    assert kill_evidence(RuntimeError("plain")) == ("", 0, 0.0)
    bad = RuntimeError("x"); bad.fresh_input_tokens = "many"; bad.estimated_cost_usd = None
    assert kill_evidence(bad) == ("", 0, 0.0)
    class _Adapter:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            raise exc
    import container_exec as ce
    monkeypatch.setattr(ce, "enforce_backend_container_contract", lambda *a, **k: None)
    r = dispatch_worker("research", "find it", context="", adapter=_Adapter(), dry_run=False)
    assert r.status == "blocked" and r.blocked_origin == "adapter"
    assert r.result == "[partial output before kill]\npartial work already performed"
    assert r.tokens_in == 100000



def test_worker_kill_keeps_output_tokens_too(monkeypatch):
    # Round 10: the ticket's output tokens were dropped with the kill.
    from workers import dispatch_worker
    exc = RuntimeError("killed")
    exc.maro_partial_output = "half a ticket"; exc.fresh_input_tokens = 5; exc.fresh_output_tokens = 2
    class _Adapter:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            raise exc
    import container_exec as ce
    monkeypatch.setattr(ce, "enforce_backend_container_contract", lambda *a, **k: None)
    r = dispatch_worker("research", "find it", context="", adapter=_Adapter(), dry_run=False)
    assert r.status == "blocked" and (r.tokens_in, r.tokens_out) == (5, 2) and "half a ticket" in r.result


def test_paid_worker_refusal_preserves_cost_and_cache(monkeypatch):
    # Review round 19: the worker lane carried a failed ticket's partial
    # output and token counts but dropped its COST and CACHE-READ tokens —
    # the paid attempts a container-auth refusal or a runaway kill leaves
    # behind reached the director as free work.
    from container_exec import ContainerAuthExpired
    from workers import dispatch_worker
    exc = ContainerAuthExpired("container auth expired: OAuth session expired")
    exc.maro_partial_output = "read the inbox"
    exc.fresh_input_tokens = 37; exc.fresh_output_tokens = 9
    exc.fresh_cache_read_tokens = 100; exc.estimated_cost_usd = 0.12
    class _Adapter:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            raise exc
    import container_exec as ce
    monkeypatch.setattr(ce, "enforce_backend_container_contract", lambda *a, **k: None)
    r = dispatch_worker("research", "find it", context="", adapter=_Adapter(), dry_run=False)
    assert r.status == "blocked" and r.error_class == "container_auth"
    assert (r.tokens_in, r.tokens_out) == (137, 9), "total-input convention: fresh + cache"
    assert r.cost_usd == pytest.approx(0.12) and r.cache_read_tokens == 100
    assert "read the inbox" in r.result
    # a success carries the response's own cost and cache reads the same way
    from llm import LLMResponse
    class _Ok:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            return LLMResponse(content="done: the inbox has three unread threads", input_tokens=137, output_tokens=9,
                               cost_usd=0.12, cache_read_tokens=100)
    ok = dispatch_worker("research", "find it", context="", adapter=_Ok(), dry_run=False)
    assert ok.status == "done" and ok.cost_usd == pytest.approx(0.12) and ok.cache_read_tokens == 100
    # negative control: an evidence-less failure records nothing
    class _Plain:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            raise RuntimeError("plain")
    p = dispatch_worker("research", "find it", context="", adapter=_Plain(), dry_run=False)
    assert (p.cost_usd, p.cache_read_tokens, p.tokens_in) == (0.0, 0, 0)
    # sibling: an EMPTY answer was still a paid call
    class _Empty:
        model_key = "t"; backend = "subprocess"
        def complete(self, messages, **kwargs):
            return LLMResponse(content="", input_tokens=40, output_tokens=1, cost_usd=0.03, cache_read_tokens=8)
    e = dispatch_worker("research", "find it", context="", adapter=_Empty(), dry_run=False)
    assert e.status == "blocked" and e.blocked_origin == "empty"
    assert (e.tokens_in, e.tokens_out, e.cache_read_tokens) == (40, 1, 8) and e.cost_usd == pytest.approx(0.03)
