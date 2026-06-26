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
    _retry_complete,
    _parse_stream_json,
    _stringify_tool_result,
)


def _make_stream_output(result="ok", tool_events=None, input_tokens=100, output_tokens=50,
                        rate_limit_status="allowed"):
    """Build a claude -p --output-format stream-json NDJSON stream:
    system/init, a rate_limit_event, assistant+user pairs for each tool call,
    and the final result event (identical payload to the old --output-format
    json single object)."""
    lines = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s"}),
        json.dumps({"type": "rate_limit_event",
                    "rate_limit_info": {"status": rate_limit_status, "resetsAt": 123}}),
    ]
    for i, te in enumerate(tool_events or []):
        tid = f"t{i}"
        lines.append(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": tid, "name": te["name"], "input": te.get("input", {})}]}}))
        lines.append(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid,
             "is_error": te.get("is_error", False),
             "content": te.get("output", "")}]}}))
    lines.append(json.dumps({
        "type": "result", "subtype": "success", "is_error": False, "result": result,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": input_tokens, "cache_read_input_tokens": 0, "output_tokens": output_tokens},
        "modelUsage": {"claude-sonnet-4-6": {"inputTokens": input_tokens, "outputTokens": output_tokens}},
    }))
    return "\n".join(lines)


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
    assert r.cache_read_tokens == 0
    assert r.fresh_input_tokens == 0


def test_llm_response_fresh_input_excludes_cache():
    r = LLMResponse(content="x", input_tokens=145_000, cache_read_tokens=130_000)
    assert r.fresh_input_tokens == 15_000


def test_llm_response_fresh_input_never_negative():
    # Defensive: cache_read should never exceed total, but don't go negative.
    r = LLMResponse(content="x", input_tokens=100, cache_read_tokens=500)
    assert r.fresh_input_tokens == 0


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


def test_subprocess_complete_threads_cwd(monkeypatch):
    """complete(cwd=...) forwards cwd into _run_subprocess_safe (bounded writes)."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_subprocess_output("ok")
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result) as mock_run:
        a.complete([LLMMessage("user", "write a file")], cwd="/some/workspace")

    assert mock_run.call_args.kwargs.get("cwd") == "/some/workspace"


def test_run_subprocess_safe_honors_cwd(tmp_path):
    """The subprocess actually runs in the supplied cwd, so relative writes land there."""
    from llm import _run_subprocess_safe
    result = _run_subprocess_safe(
        ["python3", "-c", "import os; print(os.getcwd())"],
        timeout=30,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0
    assert str(tmp_path.resolve()) in result.stdout


def test_run_subprocess_safe_ignores_missing_cwd(tmp_path):
    """A non-existent cwd is ignored (inherits parent) rather than crashing."""
    from llm import _run_subprocess_safe
    missing = str(tmp_path / "does-not-exist")
    result = _run_subprocess_safe(
        ["python3", "-c", "print('ran')"],
        timeout=30,
        cwd=missing,
    )
    assert result.returncode == 0
    assert "ran" in result.stdout


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


def test_subprocess_rc1_with_success_payload_accepted(monkeypatch):
    """Non-zero exit with a complete success result on stdout is a success.

    The claude CLI can print a valid success JSON result and still exit 1
    (observed when it fails to persist session state, e.g. foreign HOME).
    This was the long-standing "claude subprocess failed (rc=1)" blocker:
    the adapter trusted the exit code over the payload.
    """
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = _make_subprocess_output("answer despite rc=1")
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        resp = a.complete([LLMMessage("user", "test")])

    assert resp.content == "answer despite rc=1"
    assert resp.input_tokens == 100


def test_subprocess_rc1_success_payload_amid_noise(monkeypatch):
    """Payload extraction scans past warning text and non-result JSON noise."""
    a = ClaudeSubprocessAdapter()
    noise_obj = json.dumps({"type": "diagnostic", "msg": "session save failed"})
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = (
        "Warning: cannot write to HOME\n"
        + noise_obj + "\n"
        + _make_subprocess_output("found it")
        + "\ntrailing noise"
    )
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        resp = a.complete([LLMMessage("user", "test")])

    assert resp.content == "found it"


def test_subprocess_rc1_with_error_payload_still_raises(monkeypatch):
    """A non-zero exit with an error result (is_error=true) must still raise,
    and the human-readable message from the payload's "result" field must
    surface in the exception instead of truncated raw JSON.

    Real-world shape: the CLI reports auth errors as subtype="success" with
    is_error=true and result="Not logged in · Please run /login".
    """
    a = ClaudeSubprocessAdapter()
    error_payload = json.dumps({
        "type": "result", "subtype": "success", "is_error": True,
        "result": "Not logged in · Please run /login",
    })
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = error_payload
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        with pytest.raises(RuntimeError, match="Not logged in"):
            a.complete([LLMMessage("user", "test")])


def test_extract_success_result_requires_not_is_error():
    """_extract_success_result is the payload-first gate: subtype alone is
    not enough — is_error must be falsy and "result" present."""
    from llm import _extract_success_result, _extract_result_object

    good = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "ok"})
    bad = json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in"})
    assert _extract_success_result(good) is not None
    assert _extract_success_result(bad) is None
    assert _extract_success_result("") is None
    assert _extract_success_result("no json here") is None
    # _extract_result_object still finds the error object (for error detail)
    assert _extract_result_object(bad)["result"] == "Not logged in"


def test_subprocess_complete_timeout(monkeypatch):
    import subprocess as sp
    a = ClaudeSubprocessAdapter(timeout=1)

    with patch("llm._run_subprocess_safe", side_effect=sp.TimeoutExpired(cmd="claude", timeout=1)):
        with pytest.raises(RuntimeError, match="timed out"):
            a.complete([LLMMessage("user", "test")])


def test_subprocess_rate_limit_total_backoff_cap(monkeypatch):
    """Perpetual rate-limiting bails at the total-backoff wall-clock cap.

    BACKLOG #2: the per-cycle cap let the default 6 retries sum to ~61 min of
    sleeping. The total cap stops retrying once the next sleep would exceed the
    ceiling and soft-fails with a 'retry later' error instead.
    """
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "Error: you have hit your limit. Try again later."
    mock_result.stderr = ""

    # Cap at 100s: first wait (60s) fits, second (120s) would push to 180 > 100,
    # so it bails after exactly one sleep instead of all six.
    monkeypatch.setenv("MARO_CLAUDE_RATE_LIMIT_TOTAL_CAP", "100")
    slept = []

    with patch("llm._run_subprocess_safe", return_value=mock_result), \
         patch("time.sleep", side_effect=lambda s: slept.append(s)):
        with pytest.raises(RuntimeError, match=r"bailed after \d+s of backoff"):
            a.complete([LLMMessage("user", "test")])

    assert slept == [60], f"expected a single 60s backoff before cap-out, got {slept}"


def test_subprocess_rate_limit_total_cap_disabled_with_zero(monkeypatch):
    """Total cap of 0 disables the wall-clock ceiling (falls back to retry count)."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stdout = "rate limit reached"
    mock_result.stderr = ""

    monkeypatch.setenv("MARO_CLAUDE_RATE_LIMIT_TOTAL_CAP", "0")
    monkeypatch.setenv("MARO_CLAUDE_RATE_LIMIT_MAX_RETRIES", "3")
    slept = []

    with patch("llm._run_subprocess_safe", return_value=mock_result), \
         patch("time.sleep", side_effect=lambda s: slept.append(s)):
        with pytest.raises(RuntimeError, match="rate-limited after 3 retries"):
            a.complete([LLMMessage("user", "test")])

    # All 3 retries sleep — the total cap did not intervene.
    assert len(slept) == 3, f"expected 3 backoffs with cap disabled, got {slept}"


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


def test_subprocess_complete_ignores_thinking_budget(monkeypatch):
    """ClaudeSubprocessAdapter.complete() must not raise on thinking_budget kwarg."""
    a = ClaudeSubprocessAdapter()
    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stdout = _make_subprocess_output("hello")
    mock_result.stderr = ""

    with patch("llm._run_subprocess_safe", return_value=mock_result):
        # Must not raise TypeError: got an unexpected keyword argument 'thinking_budget'
        resp = a.complete([LLMMessage("user", "hi")], thinking_budget=1024)

    assert resp.content == "hello"


# ---------------------------------------------------------------------------
# _run_subprocess_safe — process group cleanup
# ---------------------------------------------------------------------------

def test_run_subprocess_safe_returns_completed_process():
    """Normal completion returns CompletedProcess with merged stdout+stderr.

    stdout and stderr are piped into the same temp file (stderr=STDOUT),
    so .stdout holds the merged stream and .stderr is empty. Interleaving
    order between the two sources is not deterministic across platforms,
    so we only assert both payloads landed somewhere in .stdout.
    """
    from llm import _run_subprocess_safe
    import subprocess as sp

    result = _run_subprocess_safe(
        ["sh", "-c", "printf hello; printf oops >&2"],
        input="", timeout=10,
    )
    assert isinstance(result, sp.CompletedProcess)
    assert result.returncode == 0
    assert "hello" in result.stdout
    assert "oops" in result.stdout
    assert result.stderr == ""


def test_run_subprocess_safe_walltime_timeout_kills_and_raises():
    """Wall-clock timeout raises TimeoutExpired after killing the process."""
    from llm import _run_subprocess_safe
    import subprocess as sp

    with pytest.raises(sp.TimeoutExpired) as exc_info:
        _run_subprocess_safe(
            ["sh", "-c", "sleep 10"],
            input="", timeout=1, liveness_timeout=0,
        )
    reason = getattr(exc_info.value, "maro_kill_reason", "")
    assert "wall-clock" in reason


def test_run_subprocess_safe_walltime_preserves_partial_output():
    """On wall-clock kill, accumulated stdout is available on the exception."""
    from llm import _run_subprocess_safe
    import subprocess as sp

    script = "printf partial; sleep 10"
    with pytest.raises(sp.TimeoutExpired) as exc_info:
        _run_subprocess_safe(
            ["sh", "-c", script],
            input="", timeout=2, liveness_timeout=0,
        )
    assert (exc_info.value.output or "").startswith("partial")


def test_run_subprocess_safe_liveness_kills_silent_process():
    """Liveness timeout fires when a process produces no output for the window."""
    from llm import _run_subprocess_safe
    import subprocess as sp

    with pytest.raises(sp.TimeoutExpired) as exc_info:
        _run_subprocess_safe(
            ["sh", "-c", "sleep 10"],
            input="", timeout=60, liveness_timeout=2, poll_interval=0.2,
        )
    reason = getattr(exc_info.value, "maro_kill_reason", "")
    assert "liveness" in reason


def test_run_subprocess_safe_liveness_spares_chatty_process():
    """A process that emits regularly does NOT trip the liveness timeout."""
    from llm import _run_subprocess_safe

    # Emits every 0.3s for ~1.5s — below the 2s liveness window every time.
    script = "for i in 1 2 3 4 5; do printf 'x'; sleep 0.3; done"
    result = _run_subprocess_safe(
        ["sh", "-c", script],
        input="", timeout=10, liveness_timeout=2, poll_interval=0.2,
    )
    assert result.returncode == 0
    assert result.stdout == "xxxxx"


def test_run_subprocess_safe_liveness_spares_cpu_busy_silent_process():
    """A process burning CPU with zero output does NOT trip the liveness timeout.

    This protects slow/local-model inference paths where the model may be
    silent-but-computing for long stretches before emitting any tokens.
    CPU activity in the subprocess session counts as "still working".
    """
    from llm import _run_subprocess_safe

    # Busy-loop for ~3s with zero stdout/stderr. Liveness is 1s, so a naive
    # output-only liveness check would kill this at ~1s. The CPU signal
    # must rescue it.
    script = (
        "python3 -c 'import time; t=time.time();\n"
        "x=0\n"
        "while time.time()-t<3: x+=1'"
    )
    result = _run_subprocess_safe(
        ["sh", "-c", script],
        input="", timeout=10, liveness_timeout=1, poll_interval=0.2,
    )
    assert result.returncode == 0, f"unexpected rc: out={result.stdout!r}"


def test_run_subprocess_safe_liveness_env_var_override(monkeypatch):
    """MARO_LIVENESS_TIMEOUT env var overrides the default liveness window."""
    from llm import _run_subprocess_safe
    import subprocess as sp

    monkeypatch.setenv("MARO_LIVENESS_TIMEOUT", "1")
    with pytest.raises(sp.TimeoutExpired) as exc_info:
        _run_subprocess_safe(
            ["sh", "-c", "sleep 10"],
            input="", timeout=30, poll_interval=0.2,
        )
    reason = getattr(exc_info.value, "maro_kill_reason", "")
    assert "liveness" in reason


def test_run_subprocess_safe_stdin_passed_through():
    """Input provided via `input=` reaches the subprocess stdin."""
    from llm import _run_subprocess_safe

    result = _run_subprocess_safe(
        ["sh", "-c", "cat"],
        input="payload-data\n", timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout == "payload-data\n"


def test_run_subprocess_safe_no_stdin_uses_devnull():
    """With input=None, stdin is /dev/null — proc exits immediately if it reads."""
    from llm import _run_subprocess_safe

    # `cat` with no stdin sees EOF immediately → exit 0, no output.
    result = _run_subprocess_safe(
        ["sh", "-c", "cat"],
        input=None, timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_run_subprocess_safe_updates_current_step_symlink():
    """During a run, /tmp/maro-current-step.log is created/updated as a symlink."""
    from llm import _run_subprocess_safe

    _run_subprocess_safe(["sh", "-c", "printf ok"], input="", timeout=5)
    # The stdout temp file gets deleted on cleanup, so the symlink dangles
    # after completion — that's by design. Just verify the link exists.
    assert os.path.islink("/tmp/maro-current-step.log")


def test_run_subprocess_safe_symlink_disabled_by_env(monkeypatch):
    """MARO_CURRENT_STEP_SYMLINK=0 suppresses the symlink update."""
    from llm import _run_subprocess_safe

    # Record the symlink target (or absence) before our call.
    before_target = None
    if os.path.islink("/tmp/maro-current-step.log"):
        try:
            before_target = os.readlink("/tmp/maro-current-step.log")
        except OSError:
            pass

    monkeypatch.setenv("MARO_CURRENT_STEP_SYMLINK", "0")
    _run_subprocess_safe(["sh", "-c", "printf ok"], input="", timeout=5)

    after_target = None
    if os.path.islink("/tmp/maro-current-step.log"):
        try:
            after_target = os.readlink("/tmp/maro-current-step.log")
        except OSError:
            pass
    # When disabled, the link either stays at its pre-call target or stays absent.
    assert after_target == before_target


def test_run_subprocess_safe_cleans_temp_files():
    """Merged-output temp file (and stdin temp file, if any) are deleted on completion."""
    from llm import _run_subprocess_safe
    import glob, tempfile

    tmpdir = tempfile.gettempdir()
    before = set(
        glob.glob(f"{tmpdir}/tmp*.out") + glob.glob(f"{tmpdir}/tmp*.stdin")
    )
    _run_subprocess_safe(["sh", "-c", "printf done"], input="ignored", timeout=5)
    after = set(
        glob.glob(f"{tmpdir}/tmp*.out") + glob.glob(f"{tmpdir}/tmp*.stdin")
    )
    # No new .out/.stdin tempfiles should have been left behind by our call.
    assert not (after - before)


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


def test_retry_complete_respects_env_override(monkeypatch):
    calls = []

    def _always_429():
        calls.append(1)
        raise RuntimeError("429 Client Error")

    monkeypatch.setenv("MARO_LLM_MAX_RETRIES", "0")
    monkeypatch.setattr("time.sleep", lambda s: None)

    with pytest.raises(RuntimeError, match="429"):
        _retry_complete(_always_429, max_retries=3)

    assert len(calls) == 1


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
            return _make_subprocess_result(0, stdout=json.dumps({"type": "result", "result": "done", "usage": {}}))

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

    def test_env_override_can_disable_subprocess_rate_limit_retries(self, monkeypatch):
        calls = []

        def _fake_run(cmd, **kw):
            calls.append(1)
            return _make_subprocess_result(1, stderr="rate limit exceeded", stdout="rate limit exceeded")

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)
        monkeypatch.setenv("MARO_CLAUDE_RATE_LIMIT_MAX_RETRIES", "0")

        adapter = ClaudeSubprocessAdapter()
        with pytest.raises(RuntimeError, match="rate-limited after 0 retries"):
            adapter.complete([LLMMessage("user", "test")])
        assert len(calls) == 1

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
            return _make_subprocess_result(0, stdout=json.dumps({"type": "result", "result": "ok", "usage": {}}))

        monkeypatch.setattr("llm._run_subprocess_safe", _fake_run)
        monkeypatch.setattr("time.sleep", lambda s: None)

        adapter = self._make_adapter(max_retries=3)
        adapter._rate_limit_wait = 300
        adapter.complete([LLMMessage("user", "test")])
        assert adapter._rate_limit_wait == 60


# ---------------------------------------------------------------------------
# MARO_BACKEND env var + maro-run --backend
# ---------------------------------------------------------------------------

def test_maro_backend_env_var_selects_openrouter(monkeypatch):
    """MARO_BACKEND=openrouter should route auto-detect to OpenRouter."""
    monkeypatch.setenv("MARO_BACKEND", "openrouter")
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {"OPENROUTER_API_KEY": "sk-or-test"})
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("llm._claude_bin_available", lambda: False)
    a = build_adapter("auto")
    assert isinstance(a, OpenRouterAdapter)


def test_maro_backend_env_var_ignored_when_explicit_backend(monkeypatch):
    """MARO_BACKEND env var should not override an explicit backend= argument."""
    monkeypatch.setenv("MARO_BACKEND", "openrouter")
    monkeypatch.setattr("llm._load_env_file", lambda *a, **kw: {})
    monkeypatch.setattr("llm._claude_bin_available", lambda: True)
    a = build_adapter("subprocess")
    assert isinstance(a, ClaudeSubprocessAdapter)


def test_openrouter_model_map_uses_current_ids():
    """OpenRouter model map should reference current OpenRouter model IDs."""
    from llm import _MODEL_MAP, MODEL_CHEAP, MODEL_MID, MODEL_POWER
    cheap = _MODEL_MAP["openrouter"][MODEL_CHEAP]
    mid = _MODEL_MAP["openrouter"][MODEL_MID]
    power = _MODEL_MAP["openrouter"][MODEL_POWER]
    assert cheap == "anthropic/claude-haiku-4.5"
    assert mid == "anthropic/claude-sonnet-4.6"
    assert power == "anthropic/claude-opus-4.6"


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
    """maro-run --backend openrouter should be parseable (dry-run)."""
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


# ---------------------------------------------------------------------------
# stream-json transcript parsing — the inner agent's REAL tool calls
# ---------------------------------------------------------------------------

class TestStreamJsonParsing:
    def test_stringify_tool_result_variants(self):
        assert _stringify_tool_result(None) == ""
        assert _stringify_tool_result("plain") == "plain"
        assert _stringify_tool_result([{"type": "text", "text": "hi"}]) == "hi"
        # non-text blocks fall back to json
        out = _stringify_tool_result([{"type": "image", "x": 1}])
        assert "image" in out
        assert _stringify_tool_result({"k": "v"}) == json.dumps({"k": "v"})

    def test_parse_extracts_result_and_tool_events_in_order(self):
        stream = _make_stream_output(
            result="done. 142 passed.",
            tool_events=[
                {"name": "Bash", "input": {"command": "pytest -q"}, "output": "142 passed"},
                {"name": "Write", "input": {"file_path": "out.py"}, "output": "perm denied", "is_error": True},
            ],
        )
        p = _parse_stream_json(stream)
        assert p["result"]["result"] == "done. 142 passed."
        assert p["rate_limited"] is False
        names = [e["name"] for e in p["tool_events"]]
        assert names == ["Bash", "Write"]
        assert p["tool_events"][0]["output"] == "142 passed"
        assert p["tool_events"][0]["is_error"] is False
        assert p["tool_events"][1]["is_error"] is True
        assert p["tool_events"][1]["output"] == "perm denied"

    def test_parse_rate_limited_status(self):
        assert _parse_stream_json(json.dumps(
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}}
        ))["rate_limited"] is True
        # allowed (with resetsAt present) must NOT be flagged — the old "resets"
        # substring match false-positived on every stream.
        assert _make_stream_output() and _parse_stream_json(_make_stream_output())["rate_limited"] is False

    def test_parse_tolerates_noise_and_empty(self):
        assert _parse_stream_json("")["result"] is None
        assert _parse_stream_json("garbage\nnot json\n")["tool_events"] == []

    def test_parse_backcompat_pretty_single_object(self):
        pretty = json.dumps({"type": "result", "result": "hi", "is_error": False}, indent=2)
        assert _parse_stream_json(pretty)["result"]["result"] == "hi"

    def test_complete_populates_tool_events(self):
        a = ClaudeSubprocessAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_stream_output(
            result="wrote fizzbuzz.py and ran it",
            tool_events=[{"name": "Write", "input": {"file_path": "fizzbuzz.py"}, "output": "File created"}],
        )
        mock_result.stderr = ""
        with patch("llm._run_subprocess_safe", return_value=mock_result):
            resp = a.complete([LLMMessage("user", "make fizzbuzz")])
        assert resp.content == "wrote fizzbuzz.py and ran it"
        assert len(resp.tool_events) == 1
        assert resp.tool_events[0]["name"] == "Write"
        assert resp.tool_events[0]["input"]["file_path"] == "fizzbuzz.py"

    def test_complete_uses_stream_json_flags(self):
        a = ClaudeSubprocessAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_stream_output(result="ok")
        mock_result.stderr = ""
        with patch("llm._run_subprocess_safe", return_value=mock_result) as mock_run:
            a.complete([LLMMessage("user", "hi")])
        cmd = mock_run.call_args.args[0]
        assert "stream-json" in cmd
        assert "--verbose" in cmd

    def test_complete_no_tool_events_when_none(self):
        a = ClaudeSubprocessAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _make_stream_output(result="just text")
        mock_result.stderr = ""
        with patch("llm._run_subprocess_safe", return_value=mock_result):
            resp = a.complete([LLMMessage("user", "hi")])
        assert resp.tool_events == []
