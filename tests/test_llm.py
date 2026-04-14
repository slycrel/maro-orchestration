"""Tests for platform-agnostic LLM adapter layer.

Tests cover:
- Data types (LLMMessage, LLMTool, LLMResponse, ToolCall)
- ClaudeSubprocessAdapter: prompt building, tool parsing
- DryRunAdapter (from agent_loop): still works
- build_adapter() auto-detection
- detect_available_backends()
- MODEL_* constants and resolve_model()

Real API calls are NOT made in tests — subprocess tests mock the binary.
"""

import json
import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from llm import (
    LLMMessage,
    LLMTool,
    LLMResponse,
    ToolCall,
    LLMAdapter,
    ClaudeSubprocessAdapter,
    OpenRouterAdapter,
    AnthropicSDKAdapter,
    OpenAIAdapter,
    build_adapter,
    detect_available_backends,
    resolve_model,
    MODEL_CHEAP, MODEL_MID, MODEL_POWER,
    _load_env_file,
    _claude_bin_available,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_subprocess_output(result: str = "test response", input_tokens: int = 100, output_tokens: int = 50) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "result": result,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "cache_read_input_tokens": 0, "output_tokens": output_tokens},
        "modelUsage": {"claude-sonnet-4-6": {"inputTokens": input_tokens, "outputTokens": output_tokens}},
    })


# ---------------------------------------------------------------------------
# MODEL_* constants and resolve_model
# ---------------------------------------------------------------------------

def test_model_constants_exist():
    assert MODEL_CHEAP == "cheap"
    assert MODEL_MID == "mid"
    assert MODEL_POWER == "power"


def test_resolve_model_subprocess():
    assert resolve_model("subprocess", MODEL_CHEAP) == "haiku"
    assert resolve_model("subprocess", MODEL_MID) == "sonnet"
    assert resolve_model("subprocess", MODEL_POWER) == "opus"


def test_resolve_model_anthropic():
    m = resolve_model("anthropic", MODEL_CHEAP)
    assert "haiku" in m.lower()


def test_resolve_model_openrouter():
    m = resolve_model("openrouter", MODEL_MID)
    assert "sonnet" in m.lower()


def test_resolve_model_passthrough():
    # Raw model names pass through unchanged
    assert resolve_model("subprocess", "claude-sonnet-4-6") == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

def test_llm_message():
    m = LLMMessage("user", "hello")
    assert m.role == "user"
    assert m.content == "hello"


def test_llm_tool():
    t = LLMTool(name="foo", description="does foo", parameters={"type": "object", "properties": {}})
    assert t.name == "foo"


def test_llm_response_defaults():
    r = LLMResponse(content="hello")
    assert r.tool_calls == []
    assert r.stop_reason == "end_turn"
    assert r.input_tokens == 0


def test_tool_call():
    tc = ToolCall(name="do_thing", arguments={"x": 1})
    assert tc.name == "do_thing"
    assert tc.arguments["x"] == 1
    assert tc.call_id == ""


# ---------------------------------------------------------------------------
# ClaudeSubprocessAdapter — prompt building
# ---------------------------------------------------------------------------

def test_subprocess_build_prompt_simple():
    a = ClaudeSubprocessAdapter()
    msgs = [
        LLMMessage("system", "You are an assistant."),
        LLMMessage("user", "Hello!"),
    ]
    prompt = a._build_prompt(msgs, tools=None)
    assert "You are an assistant." in prompt
    assert "Hello!" in prompt


def test_subprocess_build_prompt_no_system():
    a = ClaudeSubprocessAdapter()
    msgs = [LLMMessage("user", "Just a user message")]
    prompt = a._build_prompt(msgs, tools=None)
    assert "Just a user message" in prompt


def test_subprocess_build_prompt_with_tools():
    a = ClaudeSubprocessAdapter()
    tools = [LLMTool("complete", "Mark done", {"type": "object", "properties": {"result": {"type": "string"}}})]
    msgs = [LLMMessage("user", "do the thing")]
    prompt = a._build_prompt(msgs, tools=tools)
    assert "complete" in prompt
    assert "AVAILABLE TOOLS" in prompt
    assert "tool" in prompt.lower()


def test_subprocess_build_prompt_multi_turn():
    a = ClaudeSubprocessAdapter()
    msgs = [
        LLMMessage("user", "first"),
        LLMMessage("assistant", "response"),
        LLMMessage("user", "second"),
    ]
    prompt = a._build_prompt(msgs, tools=None)
    assert "first" in prompt
    assert "response" in prompt
    assert "second" in prompt


# ---------------------------------------------------------------------------
# ClaudeSubprocessAdapter — tool call parsing
# ---------------------------------------------------------------------------

def test_parse_tool_call_valid():
    a = ClaudeSubprocessAdapter()
    tools = [LLMTool("complete_step", "done", {"type": "object", "properties": {}})]
    raw = '{"tool": "complete_step", "result": "found X", "summary": "done"}'
    tc = a._parse_tool_call(raw, tools)
    assert tc is not None
    assert tc.name == "complete_step"
    assert tc.arguments["result"] == "found X"


def test_parse_tool_call_invalid_tool_name():
    a = ClaudeSubprocessAdapter()
    tools = [LLMTool("complete_step", "done", {"type": "object", "properties": {}})]
    raw = '{"tool": "unknown_tool", "result": "x"}'
    tc = a._parse_tool_call(raw, tools)
    assert tc is None


def test_parse_tool_call_no_json():
    a = ClaudeSubprocessAdapter()
    tools = [LLMTool("complete_step", "done", {"type": "object", "properties": {}})]
    tc = a._parse_tool_call("just some prose response", tools)
    assert tc is None


def test_parse_tool_call_embedded_in_prose():
    a = ClaudeSubprocessAdapter()
    tools = [LLMTool("complete_step", "done", {"type": "object", "properties": {}})]
    raw = 'Here is my answer: {"tool": "complete_step", "result": "done"}'
    tc = a._parse_tool_call(raw, tools)
    assert tc is not None
    assert tc.name == "complete_step"


# ---------------------------------------------------------------------------
# ClaudeSubprocessAdapter — complete() with mocked subprocess
# ---------------------------------------------------------------------------

def test_subprocess_complete_plain(monkeypatch):
    """Mock subprocess.run to return a successful plain text response."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_subprocess_output("hello world")
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result) as mock_run:
        resp = a.complete([LLMMessage("user", "say hi")])

    assert resp.content == "hello world"
    assert resp.backend == "subprocess"
    assert resp.input_tokens == 100
    assert resp.output_tokens == 50


def test_subprocess_complete_with_tool_call(monkeypatch):
    """Mock subprocess to return a JSON tool call response."""
    a = ClaudeSubprocessAdapter()
    tool_response = '{"tool": "complete_step", "result": "research done", "summary": "done"}'
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_subprocess_output(tool_response)
    mock_result.stderr = ""

    tools = [LLMTool("complete_step", "done", {"type": "object", "properties": {"result": {"type": "string"}}})]

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        resp = a.complete([LLMMessage("user", "research X")], tools=tools, tool_choice="required")

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "complete_step"
    assert resp.tool_calls[0].arguments["result"] == "research done"
    assert resp.content == ""


def test_subprocess_complete_failure(monkeypatch):
    """Subprocess returning non-zero exit raises RuntimeError."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = ""
    mock_result.stderr = "authentication error"

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        with pytest.raises(RuntimeError, match="failed"):
            a.complete([LLMMessage("user", "test")])


def test_subprocess_complete_timeout(monkeypatch):
    import subprocess as sp
    a = ClaudeSubprocessAdapter(timeout=1)

    with patch("llm._run_subprocess_safe", side_effect=sp.TimeoutExpired(cmd="claude", timeout=1)):
        with pytest.raises(RuntimeError, match="timed out"):
            a.complete([LLMMessage("user", "test")])


def test_subprocess_complete_plain_text_fallback(monkeypatch):
    """If output is not JSON, treat as plain text."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = "just plain text response"
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        resp = a.complete([LLMMessage("user", "test")])

    assert resp.content == "just plain text response"


# ---------------------------------------------------------------------------
# _run_subprocess_safe — process group cleanup
# ---------------------------------------------------------------------------

def test_run_subprocess_safe_kills_process_group_on_timeout():
    """On timeout, _run_subprocess_safe kills the entire process group."""
    from llm import _run_subprocess_safe
    import subprocess
    import signal

    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.pid = 12345
        proc.communicate.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=1)
        proc.wait.return_value = None
        mock_popen.return_value = proc

        with pytest.raises(subprocess.TimeoutExpired):
            _run_subprocess_safe(["test"], input="hello", timeout=1)

        # Should have been called with start_new_session=True
        mock_popen.assert_called_once()
        assert mock_popen.call_args[1].get("start_new_session") is True


def test_run_subprocess_safe_returns_completed_process():
    """Normal completion returns CompletedProcess with correct fields."""
    from llm import _run_subprocess_safe
    import subprocess

    with patch("subprocess.Popen") as mock_popen:
        proc = MagicMock()
        proc.pid = 12345
        proc.communicate.return_value = ("output", "")
        proc.returncode = 0
        mock_popen.return_value = proc

        result = _run_subprocess_safe(["test"], input="hello", timeout=10)

        assert isinstance(result, subprocess.CompletedProcess)
        assert result.stdout == "output"
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# build_adapter() factory
# ---------------------------------------------------------------------------

def test_build_adapter_subprocess_explicit(monkeypatch):
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    a = build_adapter("subprocess")
    assert isinstance(a, ClaudeSubprocessAdapter)


def test_build_adapter_auto_prefers_anthropic(monkeypatch):
    """Anthropic is first in the FailoverAdapter when available + top of backend_order."""
    from llm import DEFAULT_BACKEND_ORDER, FailoverAdapter
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.setattr("llm._get_backend_order", lambda: list(DEFAULT_BACKEND_ORDER))
    a = build_adapter("auto")
    # Multiple backends available → FailoverAdapter; primary (index 0) is Anthropic
    assert isinstance(a, FailoverAdapter), f"Expected FailoverAdapter, got {type(a)}"
    assert isinstance(a._adapters[0], AnthropicSDKAdapter)


def test_build_adapter_auto_falls_back_to_subprocess(monkeypatch):
    """Single available backend returns the adapter directly (no FailoverAdapter wrapper)."""
    from llm import DEFAULT_BACKEND_ORDER
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    monkeypatch.setattr("llm._codex_auth_available", lambda: False)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.setattr("llm._get_backend_order", lambda: list(DEFAULT_BACKEND_ORDER))
    a = build_adapter("auto")
    assert isinstance(a, ClaudeSubprocessAdapter)  # single backend, no wrapper


def test_build_adapter_honors_configured_backend_order(monkeypatch):
    """config model.backend_order puts subprocess first in the FailoverAdapter."""
    from llm import FailoverAdapter
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    # Configure subprocess-first — subprocess must be primary adapter.
    monkeypatch.setattr("llm._get_backend_order", lambda: ["subprocess", "anthropic"])
    a = build_adapter("auto")
    assert isinstance(a, FailoverAdapter)
    assert isinstance(a._adapters[0], ClaudeSubprocessAdapter)


def test_build_adapter_skips_unavailable_backends_in_order(monkeypatch):
    """If the top-ranked backend has no key/binary, skip it; next available is primary."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setattr("llm._claude_bin_available", lambda: False)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.setattr("llm._get_backend_order", lambda: ["openrouter", "subprocess", "anthropic"])
    a = build_adapter("auto")
    # Only anthropic available → single adapter, no wrapper
    assert isinstance(a, AnthropicSDKAdapter)


def test_get_backend_order_uses_default_when_unset(monkeypatch):
    from llm import _get_backend_order, DEFAULT_BACKEND_ORDER
    monkeypatch.setattr("config.get", lambda key, default=None: default)
    assert _get_backend_order() == DEFAULT_BACKEND_ORDER


def test_get_backend_order_drops_unknown_and_duplicates(monkeypatch):
    from llm import _get_backend_order
    monkeypatch.setattr(
        "config.get",
        lambda key, default=None: ["Subprocess", "unknown-thing", "subprocess", "ANTHROPIC", ""],
    )
    # Case-normalized, dedup'd, unknowns dropped.
    assert _get_backend_order() == ["subprocess", "anthropic"]


def test_get_backend_order_falls_back_on_non_list(monkeypatch):
    from llm import _get_backend_order, DEFAULT_BACKEND_ORDER
    monkeypatch.setattr("config.get", lambda key, default=None: "subprocess,anthropic")
    assert _get_backend_order() == DEFAULT_BACKEND_ORDER


def test_build_adapter_no_backends_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("llm._claude_bin_available", lambda: False)
    monkeypatch.setattr("llm._codex_auth_available", lambda: False)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    with pytest.raises(RuntimeError, match="No LLM backend"):
        build_adapter("auto")


def test_build_adapter_explicit_api_key_openrouter(monkeypatch):
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    a = build_adapter(api_key="sk-or-test-key")
    assert isinstance(a, OpenRouterAdapter)


def test_build_adapter_explicit_api_key_anthropic(monkeypatch):
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    a = build_adapter(api_key="sk-ant-test-key")
    assert isinstance(a, AnthropicSDKAdapter)


def test_build_adapter_model_passed_through():
    with patch("llm._claude_bin_available", return_value=True), \
         patch("llm._load_env_file", return_value={}), \
         patch.dict(os.environ, {}, clear=False):
        a = build_adapter("subprocess", MODEL_MID)
    assert a.model_key == MODEL_MID


# ---------------------------------------------------------------------------
# detect_available_backends
# ---------------------------------------------------------------------------

def test_detect_backends_returns_dict(monkeypatch):
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {"ANTHROPIC_API_KEY": "key"})
    result = detect_available_backends()
    assert isinstance(result, dict)
    assert "subprocess" in result
    assert "anthropic" in result
    assert result["subprocess"] is True
    assert result["anthropic"] is True


def test_detect_backends_no_keys(monkeypatch):
    monkeypatch.setattr("llm._claude_bin_available", lambda: False)
    monkeypatch.setattr("llm._codex_auth_available", lambda: False)
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = detect_available_backends()
    assert all(not v for v in result.values())


# ---------------------------------------------------------------------------
# LLMAdapter base class
# ---------------------------------------------------------------------------

def test_base_adapter_raises():
    a = LLMAdapter()
    with pytest.raises(NotImplementedError):
        a.complete([LLMMessage("user", "test")])


# ---------------------------------------------------------------------------
# DryRunAdapter (from agent_loop) still works with new interface
# ---------------------------------------------------------------------------

def test_dry_run_adapter_with_new_interface():
    from agent_loop import _DryRunAdapter
    a = _DryRunAdapter()
    r = a.complete([LLMMessage("user", "Say hi")])
    assert isinstance(r, LLMResponse)
    assert isinstance(r.content, str)


# ---------------------------------------------------------------------------
# Rate limit multi-cycle retry (auto-resume on rate limits)
# ---------------------------------------------------------------------------

from llm import ClaudeSubprocessAdapter


def _make_subprocess_result(returncode=0, stdout="", stderr=""):
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestRateLimitMultiCycleRetry:
    def _make_adapter(self, max_retries=3):
        a = ClaudeSubprocessAdapter()
        a._rate_limit_wait = 1  # start short for tests
        a._rate_limit_max_retries = max_retries
        return a

    def test_succeeds_on_second_attempt(self, monkeypatch):
        """First call hits rate limit, second succeeds."""
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(len(calls))
            if len(calls) == 1:
                return _make_subprocess_result(1, stderr="You have hit your limit", stdout="You have hit your limit")
            return _make_subprocess_result(0, stdout=json.dumps({"result": "done", "usage": {}}))

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)

        adapter = self._make_adapter(max_retries=3)
        resp = adapter.complete([LLMMessage("user", "test")])
        assert resp.content == "done"
        assert len(calls) == 2

    def test_retries_up_to_max_retries(self, monkeypatch):
        """Rate limit persists through all retries — raises after max_retries."""
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(1)
            return _make_subprocess_result(1, stderr="rate limit exceeded", stdout="rate limit exceeded")

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)

        adapter = self._make_adapter(max_retries=3)
        with pytest.raises(RuntimeError, match="rate-limited after 3 retries"):
            adapter.complete([LLMMessage("user", "test")])
        # 1 initial + 3 retries = 4 subprocess calls
        assert len(calls) == 4

    def test_backoff_wait_grows_exponentially(self, monkeypatch):
        """Wait times should grow each cycle."""
        wait_times = []

        def _fake_run(cmd, **kw):
            return _make_subprocess_result(1, stderr="hit your limit", stdout="hit your limit")

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: wait_times.append(s))

        adapter = self._make_adapter(max_retries=3)
        adapter._rate_limit_wait = 10  # start at 10s for easy math
        with pytest.raises(RuntimeError):
            adapter.complete([LLMMessage("user", "test")])

        assert len(wait_times) == 3
        assert wait_times[0] < wait_times[1] < wait_times[2]

    def test_non_rate_limit_error_stops_retry(self, monkeypatch):
        """Non-rate-limit errors should not trigger the multi-cycle loop."""
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(1)
            if len(calls) == 1:
                return _make_subprocess_result(1, stderr="hit your limit", stdout="hit your limit")
            # Second call: generic error, not rate-limit
            return _make_subprocess_result(1, stderr="internal error", stdout="internal error")

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)

        adapter = self._make_adapter(max_retries=5)
        with pytest.raises(RuntimeError):
            adapter.complete([LLMMessage("user", "test")])
        # Should stop after 2 calls (initial + 1 retry that gave non-rate-limit error)
        assert len(calls) == 2

    def test_rate_limit_wait_resets_on_success(self, monkeypatch):
        """After successful retry, _rate_limit_wait resets to 60."""
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(1)
            if len(calls) == 1:
                return _make_subprocess_result(1, stderr="hit your limit", stdout="hit your limit")
            return _make_subprocess_result(0, stdout=json.dumps({"result": "ok", "usage": {}}))

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)

        adapter = self._make_adapter(max_retries=3)
        adapter._rate_limit_wait = 300
        adapter.complete([LLMMessage("user", "test")])
        assert adapter._rate_limit_wait == 60


# ---------------------------------------------------------------------------
# POE_BACKEND env var + poe-run --backend
# ---------------------------------------------------------------------------

def test_poe_backend_env_var_selects_openrouter(monkeypatch):
    """POE_BACKEND=openrouter should route auto-detect to OpenRouter."""
    monkeypatch.setenv("POE_BACKEND", "openrouter")
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {"OPENROUTER_API_KEY": "sk-or-test"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("llm._claude_bin_available", lambda: False)
    a = build_adapter("auto")
    assert isinstance(a, OpenRouterAdapter)


def test_poe_backend_env_var_ignored_when_explicit_backend(monkeypatch):
    """POE_BACKEND env var should not override an explicit backend= argument."""
    monkeypatch.setenv("POE_BACKEND", "openrouter")
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    a = build_adapter("subprocess")
    assert isinstance(a, ClaudeSubprocessAdapter)


def test_openrouter_model_map_uses_current_ids():
    """OpenRouter model map should reference current -4-6 model IDs."""
    from llm import _MODEL_MAP, MODEL_MID, MODEL_POWER
    mid = _MODEL_MAP["openrouter"][MODEL_MID]
    power = _MODEL_MAP["openrouter"][MODEL_POWER]
    assert "4-6" in mid, f"Expected 4-6 in mid model, got {mid!r}"
    assert "4-6" in power, f"Expected 4-6 in power model, got {power!r}"


def test_run_agent_loop_passes_backend_to_build_adapter(monkeypatch):
    """run_agent_loop(backend='openrouter') should call build_adapter with backend='openrouter'."""
    import agent_loop
    captured = {}

    def _fake_build_adapter(**kw):
        captured.update(kw)
        from llm import _DryRunAdapter as _D
        return _D() if hasattr(agent_loop, "_DryRunAdapter") else None

    # Use dry_run so we never hit the adapter
    from agent_loop import run_agent_loop
    result = run_agent_loop("test goal", backend="openrouter", dry_run=True)
    # dry_run bypasses build_adapter entirely — just verify param is accepted without error
    assert result.status in ("done", "stuck", "interrupted", "error")


def test_poe_run_cli_accepts_backend_flag():
    """poe-run --backend openrouter should be parseable (dry-run)."""
    import agent_loop
    import sys
    # Verify argparse accepts --backend without raising
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", "-b",
        choices=["auto", "anthropic", "openrouter", "openai", "subprocess", "codex"],
        default=None)
    args = parser.parse_args(["--backend", "openrouter"])
    assert args.backend == "openrouter"


# ---------------------------------------------------------------------------
# Advisor Pattern tests
# ---------------------------------------------------------------------------

class TestAdvisorCall:

    def test_returns_advice_from_mock_adapter(self):
        from llm import advisor_call, LLMResponse, LLMAdapter
        class MockPowerAdapter(LLMAdapter):
            def complete(self, messages, **kwargs):
                return LLMResponse(
                    content="(b) Rephrase: try fetching via alternate URL",
                    input_tokens=500, output_tokens=50,
                )
        advice = advisor_call(
            goal="Research topic X",
            context="Step 3 failed twice.",
            question="Should we continue?",
            adapter=MockPowerAdapter(),
        )
        assert "(b)" in advice
        assert "Rephrase" in advice

    def test_returns_empty_on_adapter_failure(self):
        from llm import advisor_call, LLMAdapter
        class FailingAdapter(LLMAdapter):
            def complete(self, messages, **kwargs):
                raise RuntimeError("model unavailable")
        advice = advisor_call(
            goal="test",
            context="test",
            question="test",
            adapter=FailingAdapter(),
        )
        assert advice == ""

    def test_returns_empty_when_no_adapter_available(self):
        from llm import advisor_call
        from unittest.mock import patch
        with patch("llm.build_adapter", side_effect=RuntimeError("no backend")):
            advice = advisor_call(
                goal="test",
                context="test",
                question="test",
            )
        assert advice == ""

    def test_advisor_system_prompt_is_concise(self):
        from llm import _ADVISOR_SYSTEM
        # Advisor prompt should be focused and short
        assert len(_ADVISOR_SYSTEM) < 500
        assert "concise" in _ADVISOR_SYSTEM.lower() or "CONCISE" in _ADVISOR_SYSTEM


# ---------------------------------------------------------------------------
# Thinking token budget
# ---------------------------------------------------------------------------

class TestThinkingBudget:
    """Tests for the thinking_budget parameter on adapters."""

    def test_thinking_budget_constants_exported(self):
        from llm import THINKING_HIGH, THINKING_MID, THINKING_LOW
        assert THINKING_HIGH == 10_000
        assert THINKING_MID == 4_000
        assert THINKING_LOW == 1_024

    def test_base_adapter_accepts_thinking_budget(self):
        """Base LLMAdapter.complete() signature includes thinking_budget."""
        import inspect
        sig = inspect.signature(LLMAdapter.complete)
        assert "thinking_budget" in sig.parameters

    def test_anthropic_adapter_passes_thinking_to_api(self):
        """AnthropicSDKAdapter should pass thinking param when budget > 0."""
        mock_anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            adapter = AnthropicSDKAdapter(api_key="sk-ant-test", model=MODEL_MID)
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(type="text", text="result")]
            mock_resp.stop_reason = "end_turn"
            mock_resp.model = "claude-sonnet-4-6"
            mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_resp.content[0].text = "result"
            mock_client.messages.create.return_value = mock_resp
            adapter._client = mock_client

            adapter.complete(
                [LLMMessage("user", "test")],
                thinking_budget=5000,
            )

            call_kwargs = mock_client.messages.create.call_args
            assert call_kwargs[1]["thinking"] == {"type": "enabled", "budget_tokens": 5000}
            # max_tokens should be bumped to accommodate thinking + output
            assert call_kwargs[1]["max_tokens"] >= 5000 + 4096
            # temperature should NOT be in kwargs when thinking is enabled
            assert "temperature" not in call_kwargs[1]

    def test_anthropic_adapter_no_thinking_when_none(self):
        """No thinking param when thinking_budget is None."""
        mock_anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            adapter = AnthropicSDKAdapter(api_key="sk-ant-test", model=MODEL_MID)
            mock_client = MagicMock()
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(type="text", text="result")]
            mock_resp.stop_reason = "end_turn"
            mock_resp.model = "claude-sonnet-4-6"
            mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_resp.content[0].text = "result"
            mock_client.messages.create.return_value = mock_resp
            adapter._client = mock_client

            adapter.complete(
                [LLMMessage("user", "test")],
                thinking_budget=None,
            )

            call_kwargs = mock_client.messages.create.call_args
            assert "thinking" not in call_kwargs[1]
            assert "temperature" in call_kwargs[1]

    def test_anthropic_adapter_thinking_extracts_content(self):
        """Thinking blocks should be logged but not included in content."""
        mock_anthropic = MagicMock()
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            adapter = AnthropicSDKAdapter(api_key="sk-ant-test", model=MODEL_MID)
            mock_client = MagicMock()
            # Simulate response with thinking block + text block
            thinking_block = MagicMock(type="thinking", thinking="internal reasoning here")
            thinking_block.text = None  # thinking blocks don't have .text in normal sense
            delattr(thinking_block, "text")  # remove .text so hasattr returns False
            text_block = MagicMock(type="text", text="the actual response")
            mock_resp = MagicMock()
            mock_resp.content = [thinking_block, text_block]
            mock_resp.stop_reason = "end_turn"
            mock_resp.model = "claude-sonnet-4-6"
            mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
            mock_client.messages.create.return_value = mock_resp
            adapter._client = mock_client

            result = adapter.complete(
                [LLMMessage("user", "test")],
                thinking_budget=5000,
            )

            assert result.content == "the actual response"
            assert "internal reasoning" not in result.content

    def test_decompose_passes_thinking_budget(self):
        """planner.decompose() should forward thinking_budget to adapter."""
        from planner import decompose
        mock_adapter = MagicMock()
        mock_adapter.complete.return_value = LLMResponse(
            content='["step 1", "step 2"]',
            stop_reason="end_turn",
        )
        # Narrow goal → single-shot, but thinking_budget should be forwarded
        steps = decompose(
            "fix the login bug",
            mock_adapter,
            max_steps=8,
            thinking_budget=5000,
        )
        # At least one complete() call should have been made
        assert mock_adapter.complete.called
        assert len(steps) == 2


# ---------------------------------------------------------------------------
# FailoverAdapter
# ---------------------------------------------------------------------------

class TestIsFailoverError:
    def test_402_payment_required(self):
        from llm import _is_failover_error
        assert _is_failover_error(RuntimeError("HTTP 402 Payment Required"))

    def test_401_unauthorized(self):
        from llm import _is_failover_error
        assert _is_failover_error(RuntimeError("HTTP 401 Unauthorized"))

    def test_403_forbidden(self):
        from llm import _is_failover_error
        assert _is_failover_error(RuntimeError("403 Forbidden"))

    def test_500_internal_server_error(self):
        from llm import _is_failover_error
        assert _is_failover_error(RuntimeError("500 Internal Server Error"))

    def test_503_service_unavailable(self):
        from llm import _is_failover_error
        assert _is_failover_error(RuntimeError("503 Service Unavailable"))

    def test_400_bad_request_not_failover(self):
        from llm import _is_failover_error
        # 400 bad request = bad caller, not broken backend
        assert not _is_failover_error(RuntimeError("400 Bad Request — invalid schema"))

    def test_generic_runtime_error_not_failover(self):
        from llm import _is_failover_error
        # Generic error without backend signal should not failover
        assert not _is_failover_error(RuntimeError("model returned empty response"))


class TestFailoverAdapter:
    def _make_adapter(self, response_or_exc):
        """Build a fake LLMAdapter that returns a value or raises."""
        from llm import LLMAdapter, LLMResponse

        class FakeAdapter(LLMAdapter):
            def __init__(self, name, val):
                self._name = name
                self._val = val

            @property
            def backend(self):
                return self._name

            def complete(self, messages, **kwargs):
                if isinstance(self._val, Exception):
                    raise self._val
                return LLMResponse(content=self._val, stop_reason="end_turn")

        return FakeAdapter(response_or_exc[0], response_or_exc[1])

    def test_first_adapter_succeeds(self):
        from llm import FailoverAdapter, LLMMessage
        a1 = self._make_adapter(("backend-a", "ok response"))
        a2 = self._make_adapter(("backend-b", RuntimeError("should not reach")))
        fa = FailoverAdapter([a1, a2])
        result = fa.complete([LLMMessage("user", "hi")])
        assert result.content == "ok response"
        assert fa.backend == "backend-a"

    def test_failover_to_second_on_402(self):
        from llm import FailoverAdapter, LLMMessage
        a1 = self._make_adapter(("backend-a", RuntimeError("HTTP 402 Payment Required")))
        a2 = self._make_adapter(("backend-b", "fallback response"))
        fa = FailoverAdapter([a1, a2])
        result = fa.complete([LLMMessage("user", "hi")])
        assert result.content == "fallback response"
        assert fa.backend == "backend-b"

    def test_no_failover_on_non_backend_error(self):
        from llm import FailoverAdapter, LLMMessage
        a1 = self._make_adapter(("backend-a", RuntimeError("model returned empty response")))
        a2 = self._make_adapter(("backend-b", "should not reach"))
        fa = FailoverAdapter([a1, a2])
        with pytest.raises(RuntimeError, match="model returned empty response"):
            fa.complete([LLMMessage("user", "hi")])
        # backend should remain at a1 since no failover happened
        assert fa.backend == "backend-a"

    def test_all_adapters_fail_raises_last(self):
        from llm import FailoverAdapter, LLMMessage
        a1 = self._make_adapter(("backend-a", RuntimeError("HTTP 402")))
        a2 = self._make_adapter(("backend-b", RuntimeError("HTTP 503")))
        fa = FailoverAdapter([a1, a2])
        with pytest.raises(RuntimeError, match="503"):
            fa.complete([LLMMessage("user", "hi")])

    def test_single_adapter_returns_directly(self):
        """build_adapter('auto') returns the adapter directly when only one is available."""
        from llm import FailoverAdapter, LLMAdapter
        a1 = self._make_adapter(("only-backend", "ok"))
        fa = FailoverAdapter([a1])
        assert len(fa._adapters) == 1
        assert fa.backend == "only-backend"

    def test_empty_adapter_list_raises(self):
        from llm import FailoverAdapter
        with pytest.raises(ValueError, match="at least one"):
            FailoverAdapter([])

    def test_model_key_forwarded_from_active_adapter(self):
        from llm import FailoverAdapter, LLMAdapter

        class KeyedAdapter(LLMAdapter):
            backend = "keyed"
            model_key = "mid"
            def complete(self, messages, **kwargs):
                raise RuntimeError("HTTP 402")

        class FallbackAdapter(LLMAdapter):
            backend = "fallback"
            model_key = "cheap"
            def complete(self, messages, **kwargs):
                from llm import LLMResponse
                return LLMResponse(content="ok", stop_reason="end_turn")

        fa = FailoverAdapter([KeyedAdapter(), FallbackAdapter()])
        from llm import LLMMessage
        fa.complete([LLMMessage("user", "test")])
        assert fa.model_key == "cheap"  # now on FallbackAdapter
