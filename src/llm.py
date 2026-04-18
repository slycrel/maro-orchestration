#!/usr/bin/env python3
"""Platform-agnostic LLM adapter layer for Poe orchestration.

All agents talk to LLMAdapter.complete() — the same interface regardless of
which backend is actually serving the call:

  Backend            | When to use
  -------------------|------------------------------------------------------
  CodexCLI           | codex binary available + authenticated (ChatGPT OAuth, no extra cost)
  ClaudeSubprocess   | Claude Code is installed + authenticated (always on this box)
  AnthropicSDK       | ANTHROPIC_API_KEY is set
  OpenRouter         | OPENROUTER_API_KEY is set with credits
  OpenAI             | OPENAI_API_KEY is set with credits

Auto-detection order (highest to lowest priority):
    1. Explicit backend= or api_key= arg to build_adapter()
    2. POE_BACKEND env var (single backend, no fallback)
    3. config `model.backend_order` (ordered list; first available wins)
    4. DEFAULT_BACKEND_ORDER (anthropic, subprocess, openrouter, openai)

A backend is "available" when: (anthropic/openrouter/openai) its API key env var
is set, (subprocess) the `claude` binary is on PATH, (codex) `codex` binary plus
~/.codex/auth.json present. codex stays out of the default order (agentic
subprocess, not a drop-in API).

Model names are backend-specific but normalized through constants:
    MODEL_CHEAP, MODEL_MID, MODEL_POWER — callers use these, not raw strings.
    Each adapter maps them to its own model identifiers.

Tool calls:
    Native adapters (Anthropic, OpenRouter, OpenAI) use native tool APIs.
    ClaudeSubprocess uses JSON-in-prompt (same tool interface, simulated).

Usage:
    adapter = build_adapter()               # auto-detect
    adapter = build_adapter("subprocess")   # force claude -p
    adapter = build_adapter("openrouter")   # force OpenRouter

    response = adapter.complete([
        LLMMessage("system", "You are a planning assistant."),
        LLMMessage("user", "Break this goal into 3 steps: research X"),
    ])
    print(response.content)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.llm")


# ---------------------------------------------------------------------------
# Model name constants (backend-independent)
# ---------------------------------------------------------------------------

MODEL_CHEAP   = "cheap"    # Haiku / gpt-4o-mini / etc.
MODEL_MID     = "mid"      # Sonnet / gpt-4o
MODEL_POWER   = "power"    # Opus / gpt-4.5 / etc.
MODEL_DEFAULT = MODEL_CHEAP

# Per-backend model maps
_MODEL_MAP: Dict[str, Dict[str, str]] = {
    "anthropic": {
        MODEL_CHEAP: "claude-haiku-4-5-20251001",
        MODEL_MID:   "claude-sonnet-4-6",
        MODEL_POWER: "claude-opus-4-6",
    },
    "openrouter": {
        MODEL_CHEAP: "anthropic/claude-haiku-4-5-20251001",
        MODEL_MID:   "anthropic/claude-sonnet-4-6",
        MODEL_POWER: "anthropic/claude-opus-4-6",
    },
    "openai": {
        MODEL_CHEAP: "gpt-4o-mini",
        MODEL_MID:   "gpt-4o",
        MODEL_POWER: "gpt-4.5-preview",
    },
    "subprocess": {
        MODEL_CHEAP: "haiku",
        MODEL_MID:   "sonnet",
        MODEL_POWER: "opus",
    },
    # CodexCLI uses gpt-5.4 (via ChatGPT OAuth); all tiers map to same model since
    # GPT-5.4 is already the top available model on the ChatGPT Plus/Pro plan.
    # Heavy reasoning tasks that need Claude Opus should use backend="subprocess".
    "codex": {
        MODEL_CHEAP: "gpt-5.4",
        MODEL_MID:   "gpt-5.4",
        MODEL_POWER: "gpt-5.4",
    },
}


def resolve_model(backend: str, model_key: str) -> str:
    """Resolve a MODEL_* constant to a backend-specific model string."""
    bmap = _MODEL_MAP.get(backend, {})
    return bmap.get(model_key, model_key)  # pass-through if already a raw name


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------

@dataclass
class LLMMessage:
    role: str    # "system" | "user" | "assistant"
    content: str


@dataclass
class ToolCall:
    name: str
    arguments: Dict[str, Any]
    call_id: str = ""


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_turn"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    backend: str = ""


@dataclass
class LLMTool:
    name: str
    description: str
    parameters: Dict[str, Any]    # JSON Schema object


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is a transient error worth retrying."""
    msg = str(exc).lower()
    # HTTP 429, 5xx, rate limit, overloaded, timeout
    for pattern in ("429", "rate limit", "rate_limit", "overloaded", "502", "503", "529",
                    "timeout", "timed out", "connection", "temporarily unavailable"):
        if pattern in msg:
            return True
    # Anthropic SDK specific
    exc_type = type(exc).__name__
    if exc_type in ("RateLimitError", "APIStatusError", "APIConnectionError",
                     "InternalServerError", "OverloadedError"):
        return True
    return False


def _is_failover_error(exc: Exception) -> bool:
    """Return True if the exception warrants trying the next backend.

    Failover triggers on errors that indicate a *backend is unavailable*,
    not errors that indicate the *request is bad* (400, bad schema, etc.).

    - 402/payment required: quota or billing issue on this backend
    - 401/403: auth failure (bad or expired key for this backend)
    - 5xx after retry exhaustion: server-side instability
    - Subprocess failures: binary missing or timed out unrecoverably
    """
    msg = str(exc).lower()
    # Backend payment/quota/auth errors
    for pattern in ("402", "payment required", "quota exceeded", "billing",
                    "401", "unauthorized", "403", "forbidden"):
        if pattern in msg:
            return True
    # Server errors (after retry exhaustion in _retry_complete)
    for pattern in ("500", "502", "503", "529",
                    "service unavailable", "internal server error"):
        if pattern in msg:
            return True
    # Subprocess-specific failures (binary not found or subprocess crashed)
    if "subprocess" in msg and any(s in msg for s in ("failed", "not found", "unavailable")):
        return True
    if "claude binary" in msg or "claude -p" in msg:
        return True
    return False


def _retry_complete(fn, *args, max_retries: int = 3, **kwargs) -> "LLMResponse":
    """Wrap an adapter .complete() call with retry on transient errors.

    Exponential backoff: 5s, 15s, 45s. Only retries on rate limits,
    server errors, and connection failures. Non-retryable errors propagate
    immediately.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not _is_retryable(exc) or attempt == max_retries:
                raise
            last_exc = exc
            wait = 5 * (3 ** attempt)  # 5, 15, 45
            log.warning(
                "llm retry: %s (attempt %d/%d, waiting %ds)",
                type(exc).__name__, attempt + 1, max_retries, wait,
            )
            import time
            time.sleep(wait)
    raise last_exc  # unreachable, but satisfies type checker



# ---------------------------------------------------------------------------
# Thinking budget presets (tokens).  Pass to complete(thinking_budget=...).
# ---------------------------------------------------------------------------
THINKING_HIGH = 10_000    # Planning, decomposition, complex synthesis
THINKING_MID  = 4_000     # Advisory calls, moderate reasoning
THINKING_LOW  = 1_024     # Light reasoning, simple analysis
# None = disabled (default).  Backends that don't support thinking ignore it.


class LLMAdapter:
    """Abstract base. Subclass and implement `complete`."""

    backend: str = "base"

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        thinking_budget: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        raise NotImplementedError

    def _resolved_model(self, model_key: str) -> str:
        return resolve_model(self.backend, model_key)


# ---------------------------------------------------------------------------
# FailoverAdapter — wraps multiple adapters; tries each on backend errors
# ---------------------------------------------------------------------------

class FailoverAdapter(LLMAdapter):
    """Wraps an ordered list of adapters; tries the next on backend failures.

    Failover triggers when the active adapter raises an error that indicates
    the backend is unavailable (4xx billing/auth, 5xx after retry exhaustion,
    subprocess not found). Errors that indicate a bad request (400, schema
    errors) propagate immediately — those won't be fixed by switching backends.

    The `backend` attribute always reflects the currently active adapter.
    The `model_key` is forwarded from the current adapter.

    Usage::

        adapter = FailoverAdapter([
            AnthropicSDKAdapter(...),
            OpenRouterAdapter(...),
            ClaudeSubprocessAdapter(),
        ])
    """

    backend: str = "failover"

    def __init__(self, adapters: List["LLMAdapter"]) -> None:
        if not adapters:
            raise ValueError("FailoverAdapter requires at least one adapter")
        self._adapters: List["LLMAdapter"] = list(adapters)
        self._current_idx: int = 0

    @property
    def backend(self) -> str:  # type: ignore[override]
        return getattr(self._adapters[self._current_idx], "backend", "failover")

    @backend.setter
    def backend(self, value: str) -> None:
        pass  # read-only; tracks active adapter

    @property
    def model_key(self) -> str:
        return getattr(self._adapters[self._current_idx], "model_key", "")

    def complete(
        self,
        messages: List["LLMMessage"],
        *,
        tools: Optional[List["LLMTool"]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        thinking_budget: Optional[int] = None,
        **kwargs,
    ) -> "LLMResponse":
        last_exc: Optional[Exception] = None
        for idx, adapter in enumerate(self._adapters):
            self._current_idx = idx
            try:
                result = adapter.complete(
                    messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking_budget=thinking_budget,
                    **kwargs,
                )
                if idx > 0:
                    log.info(
                        "FailoverAdapter: succeeded on %s (index %d/%d)",
                        adapter.backend, idx + 1, len(self._adapters),
                    )
                return result
            except Exception as exc:
                last_exc = exc
                if not _is_failover_error(exc) or idx >= len(self._adapters) - 1:
                    # Non-failover error or last adapter — propagate
                    raise
                next_backend = getattr(self._adapters[idx + 1], "backend", "?")
                log.warning(
                    "FailoverAdapter: %s failed with %s (%s), trying %s",
                    adapter.backend, type(exc).__name__, str(exc)[:80], next_backend,
                )
        # Should never reach here, but satisfy type checker
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("FailoverAdapter: no adapters configured")


# ---------------------------------------------------------------------------
# ClaudeSubprocessAdapter — uses `claude -p` (no API key needed)
# ---------------------------------------------------------------------------

def _find_claude_bin() -> str:
    """Resolve the claude binary path. Checks CLAUDE_BIN env, then PATH, then common locations."""
    import shutil
    if env := os.environ.get("CLAUDE_BIN"):
        return env
    if found := shutil.which("claude"):
        return found
    # Common install locations as last resort
    for candidate in (
        Path.home() / ".local" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
        Path("/opt/homebrew/bin/claude"),
    ):
        if candidate.is_file():
            return str(candidate)
    return str(Path.home() / ".local" / "bin" / "claude")  # best guess fallback

_CLAUDE_BIN = _find_claude_bin()

# When tools are requested, embed them in the prompt as JSON instructions.
# The subprocess adapter simulates native tool calls by asking the model
# to respond with a JSON object containing "tool" and its arguments.
_TOOL_INJECTION_TEMPLATE = textwrap.dedent("""\

--- AVAILABLE TOOLS ---
You MUST respond by calling exactly one of these tools. Reply ONLY with a JSON
object (no prose, no markdown fence) in this exact format:

{{"tool": "<tool_name>", <arguments as top-level keys>}}

Tools:
{tool_list}
--- END TOOLS ---
""")


def _session_cpu_ticks(leader_pid: int) -> int:
    """Sum utime+stime (clock ticks) for all procs whose session == leader_pid.

    Secondary liveness signal: a silent-but-computing subprocess (e.g. a
    local LLM mid-inference) won't advance its output file's mtime but
    will burn CPU. Summing across the session catches multi-process
    pipelines (e.g. claude CLI → node worker) since `start_new_session=True`
    makes the Popen'd process the session leader.

    Best-effort: any per-proc read failure is skipped silently. Returns 0
    on total failure (Linux /proc unavailable), which disables the signal.
    """
    total = 0
    try:
        entries = os.listdir("/proc")
    except OSError:
        return 0
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "rb") as f:
                data = f.read().decode("ascii", errors="replace")
        except (FileNotFoundError, ProcessLookupError, OSError):
            continue
        # Format: pid (comm) state ppid pgrp session tty_nr ...
        # `comm` may contain spaces/parens; split fields after the final ')'.
        rparen = data.rfind(")")
        if rparen == -1:
            continue
        rest = data[rparen + 2:].split()
        # rest indices (0-based, starting after the `comm` field):
        #   0=state, 1=ppid, 2=pgrp, 3=session, ..., 11=utime, 12=stime
        try:
            session = int(rest[3])
            utime = int(rest[11])
            stime = int(rest[12])
        except (IndexError, ValueError):
            continue
        if session == leader_pid:
            total += utime + stime
    return total


def _run_subprocess_safe(cmd, *, input=None, timeout=600,
                         liveness_timeout=None, poll_interval=2.0):
    """Run a subprocess in its own process group with streaming + liveness check.

    Streams the subprocess's stdout+stderr (merged) to a single temp file
    so the on-disk view matches what an operator sees on a terminal.
    Two kill conditions:
      1. Wall-clock `timeout` — hard ceiling, same semantics as before.
      2. Liveness: if neither file-mtime advances nor CPU time accumulates
         across the subprocess session for `liveness_timeout` seconds,
         assume the subprocess is hung and kill. The CPU signal prevents
         false-kills of silent-but-computing local models.

    `liveness_timeout` defaults to min(timeout, 180). Pass 0 or None-like to
    disable (falls back to wall-clock only). Env var `POE_LIVENESS_TIMEOUT`
    overrides the default for the whole process.

    Partial output captured up to the kill is preserved in the returned
    CompletedProcess.stdout (stderr is empty — both streams merged). This
    lets callers still access accumulated work on timeout, unlike
    communicate().

    Returns a subprocess.CompletedProcess with stdout=merged output and
    stderr="". On wall-clock or liveness timeout raises
    subprocess.TimeoutExpired with `.poe_kill_reason` attached so callers
    can distinguish.
    """
    import signal
    import tempfile
    import time

    if liveness_timeout is None:
        env_override = os.environ.get("POE_LIVENESS_TIMEOUT")
        if env_override:
            try:
                liveness_timeout = int(env_override)
            except ValueError:
                liveness_timeout = None
        if liveness_timeout is None:
            liveness_timeout = min(timeout, 180) if timeout else 0

    stdin_f = None
    if input is not None:
        stdin_f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".stdin", delete=False, encoding="utf-8")
        stdin_f.write(input)
        stdin_f.flush()
        stdin_f.seek(0)

    combined_f = tempfile.NamedTemporaryFile(
        mode="w+b", suffix=".out", delete=False)
    combined_path = combined_f.name

    # Operator-visibility symlink: `tail -f /tmp/poe-current-step.log` from
    # anywhere shows the in-flight subprocess's merged output. Updated
    # atomically on each new subprocess; dangles between steps (by
    # design — means "no step running"). Disable with
    # POE_CURRENT_STEP_SYMLINK=0.
    if os.environ.get("POE_CURRENT_STEP_SYMLINK", "1") != "0":
        try:
            link_target = "/tmp/poe-current-step.log"
            tmp_link = f"{link_target}.{os.getpid()}.tmp"
            try: os.unlink(tmp_link)
            except OSError: pass
            os.symlink(combined_path, tmp_link)
            os.rename(tmp_link, link_target)  # atomic replace
        except OSError:
            pass  # best-effort; never block on symlink failures

    def _read_captured():
        combined_f.flush()
        combined_f.seek(0)
        return combined_f.read().decode("utf-8", errors="replace")

    def _cleanup_files():
        try: combined_f.close()
        except Exception: pass
        for p in (combined_path, stdin_f.name if stdin_f else None):
            if p:
                try: os.unlink(p)
                except OSError: pass

    proc = subprocess.Popen(
        cmd,
        stdin=stdin_f if stdin_f else subprocess.DEVNULL,
        stdout=combined_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    if stdin_f:
        try:
            stdin_f.close()
        except Exception:
            pass

    start = time.monotonic()
    last_seen = start          # monotonic time of most recent activity
    last_mtime = 0.0           # file mtime we've already credited
    last_cpu = _session_cpu_ticks(proc.pid)  # initial CPU baseline
    kill_reason = None
    try:
        while True:
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            elapsed = now - start

            # Output-mtime signal: file grew since last poll?
            try:
                latest_mtime = os.path.getmtime(combined_path)
            except OSError:
                latest_mtime = 0.0
            if latest_mtime > last_mtime:
                last_mtime = latest_mtime
                last_seen = now

            # CPU signal: session burned more cycles since last poll?
            # Catches silent-but-computing local models that don't stream.
            cur_cpu = _session_cpu_ticks(proc.pid)
            if cur_cpu > last_cpu:
                last_cpu = cur_cpu
                last_seen = now

            if timeout and elapsed >= timeout:
                kill_reason = f"wall-clock timeout after {int(elapsed)}s"
                break
            if liveness_timeout and (now - last_seen) >= liveness_timeout:
                kill_reason = (f"liveness timeout: no output or CPU activity "
                               f"for {int(now - last_seen)}s "
                               f"(elapsed={int(elapsed)}s)")
                break

            time.sleep(poll_interval)

        if kill_reason is not None:
            try: os.killpg(proc.pid, signal.SIGTERM)
            except OSError: pass
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except OSError: pass
                proc.wait(timeout=5)
            stdout = _read_captured()
            _cleanup_files()
            exc = subprocess.TimeoutExpired(cmd, timeout or liveness_timeout,
                                            output=stdout, stderr="")
            # Attach reason for caller introspection; not used by base class.
            exc.poe_kill_reason = kill_reason  # type: ignore[attr-defined]
            raise exc
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except OSError: pass
        _cleanup_files()
        raise
    finally:
        # Best-effort process-group cleanup on normal completion too.
        try: os.killpg(proc.pid, signal.SIGTERM)
        except OSError: pass

    stdout = _read_captured()
    _cleanup_files()
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, "")


class ClaudeSubprocessAdapter(LLMAdapter):
    """Adapter using `claude -p` subprocess. Works anywhere Claude Code is installed.

    Tool calls are simulated via JSON-in-prompt: tools are described in the
    system prompt as JSON schema, and the model responds with a JSON object
    that the adapter parses back into ToolCall objects.
    """

    backend = "subprocess"

    def __init__(self, model: str = MODEL_CHEAP, claude_bin: str = _CLAUDE_BIN, timeout: int = 600):
        self.model_key = model
        self.claude_bin = claude_bin
        self.timeout = timeout

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
        **kwargs,  # absorb unsupported kwargs (e.g. thinking_budget) gracefully
    ) -> LLMResponse:
        # Build the prompt text
        prompt = self._build_prompt(messages, tools)

        # Build command
        model_str = resolve_model("subprocess", self.model_key)
        cmd = [self.claude_bin, "-p", "--output-format", "json", "--dangerously-skip-permissions",
               "--disallowedTools", "WebFetch,WebSearch"]
        if model_str not in (MODEL_CHEAP, MODEL_MID, MODEL_POWER, "cheap", "mid", "power"):
            # Only add --model if it's a real model name, not our constants
            cmd += ["--model", model_str]
        elif model_str in ("sonnet", "opus", "haiku"):
            cmd += ["--model", model_str]

        _timeout = timeout or self.timeout
        try:
            result = _run_subprocess_safe(cmd, input=prompt, timeout=_timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"claude binary not found at {self.claude_bin}")

        if result.returncode != 0:
            # stdout holds the merged stdout+stderr stream from the subprocess.
            merged = result.stdout.strip()
            detail = merged[:300] or "(no output)"

            # Rate limit detection: "hit your limit" or "resets" in output
            _combined = merged.lower()
            if "hit your limit" in _combined or "rate limit" in _combined or "resets" in _combined:
                import time as _time
                # Multi-cycle polling: retry up to _RATE_LIMIT_MAX_RETRIES times.
                # Each cycle waits exponentially longer (60→120→240→480→900→1800s, capped).
                _RATE_LIMIT_MAX_RETRIES = getattr(self, "_rate_limit_max_retries", 6)
                _RATE_LIMIT_CYCLE_CAP = 1800  # 30 minutes max per wait
                _wait = getattr(self, "_rate_limit_wait", 60)
                _retry_success = False
                for _attempt in range(_RATE_LIMIT_MAX_RETRIES):
                    log.warning(
                        "rate limit detected (attempt %d/%d), waiting %ds before retry",
                        _attempt + 1, _RATE_LIMIT_MAX_RETRIES, _wait,
                    )
                    _time.sleep(_wait)
                    _wait = min(_wait * 2, _RATE_LIMIT_CYCLE_CAP)
                    try:
                        result = _run_subprocess_safe(cmd, input=prompt, timeout=_timeout)
                    except subprocess.TimeoutExpired:
                        log.warning("rate limit retry timed out after %ds, will retry", _timeout)
                        continue
                    if result.returncode == 0:
                        _retry_success = True
                        break
                    # Check if still rate-limited
                    _retry_combined = result.stdout.lower()
                    if "hit your limit" not in _retry_combined and "rate limit" not in _retry_combined:
                        # Non-rate-limit error — stop retrying
                        break
                    # Still rate-limited — continue loop with longer wait
                if _retry_success:
                    self._rate_limit_wait = 60  # reset backoff counter on success
                else:
                    self._rate_limit_wait = _wait  # persist longer wait for next call
                if not _retry_success:
                    if result.returncode != 0:
                        raise RuntimeError(
                            f"claude rate-limited after {_RATE_LIMIT_MAX_RETRIES} retries: "
                            f"{result.stdout[:200]}"
                        )

            if result.returncode != 0:
                # Dump debug info to /tmp for post-mortem diagnosis
                try:
                    import tempfile, os as _os
                    debug_path = _os.path.join(tempfile.gettempdir(), f"claude_rc1_{os.getpid()}.txt")
                    with open(debug_path, "w") as _f:
                        _f.write(f"rc={result.returncode}\ncmd={cmd}\nprompt_len={len(prompt)}\n\n")
                        _f.write(f"--- OUTPUT (merged stdout+stderr) ---\n{result.stdout[:4000]}\n")
                        _f.write(f"--- PROMPT (first 3000 chars) ---\n{prompt[:3000]}\n")
                except Exception:
                    pass
                raise RuntimeError(f"claude subprocess failed (rc={result.returncode}): {detail}")

        # Parse JSON output. stdout holds merged stdout+stderr, so claude's
        # JSON blob may be surrounded by warning/debug text. Try a direct
        # parse first; if that fails, scan for the first `{` that begins a
        # valid JSON object.
        _stdout_text = result.stdout.strip()
        data = None
        try:
            data = json.loads(_stdout_text)
        except json.JSONDecodeError:
            _decoder = json.JSONDecoder()
            _start = _stdout_text.find("{")
            while _start != -1:
                try:
                    data, _ = _decoder.raw_decode(_stdout_text[_start:])
                    break
                except json.JSONDecodeError:
                    _start = _stdout_text.find("{", _start + 1)
        if data is None:
            # Fallback: treat as plain text
            return LLMResponse(
                content=_stdout_text,
                backend=self.backend,
            )

        raw_result = data.get("result", "")
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        # Parse tool calls from JSON response
        tool_calls: List[ToolCall] = []
        content = raw_result

        if tools and raw_result:
            tc = self._parse_tool_call(raw_result, tools)
            if tc:
                tool_calls = [tc]
                content = ""

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", "end_turn"),
            model=list(data.get("modelUsage", {}).keys() or ["claude"])[0] if data.get("modelUsage") else "claude",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            backend=self.backend,
        )

    def _build_prompt(self, messages: List[LLMMessage], tools: Optional[List[LLMTool]]) -> str:
        """Flatten messages into a single prompt string for claude -p stdin."""
        parts = []

        # Collect system messages
        system_parts = [m.content for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if system_parts:
            parts.append("[SYSTEM INSTRUCTIONS]\n" + "\n\n".join(system_parts))

        # Inject tool instructions if tools are requested
        if tools:
            tool_list = "\n".join(
                f'- "{t.name}": {t.description}\n  Arguments: {json.dumps(t.parameters.get("properties", {}), indent=2)}'
                for t in tools
            )
            parts.append(_TOOL_INJECTION_TEMPLATE.format(tool_list=tool_list))

        parts.append("[END SYSTEM INSTRUCTIONS]\n")

        # Add conversation history
        for m in non_system:
            if m.role == "user":
                parts.append(f"User: {m.content}")
            elif m.role == "assistant":
                parts.append(f"Assistant: {m.content}")

        return "\n\n".join(parts)

    def _parse_tool_call(self, text: str, tools: List[LLMTool]) -> Optional[ToolCall]:
        """Extract a tool call from the model's JSON response."""
        text = text.strip()

        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None

        try:
            data = json.loads(text[start:end])
        except json.JSONDecodeError:
            return None

        tool_name = data.get("tool")
        if not tool_name:
            return None

        # Verify it's a valid tool
        valid_names = {t.name for t in tools}
        if tool_name not in valid_names:
            return None

        # Extract arguments (everything except "tool" key)
        args = {k: v for k, v in data.items() if k != "tool"}
        return ToolCall(name=tool_name, arguments=args)


# ---------------------------------------------------------------------------
# CodexCLIAdapter — uses `codex exec --json` (ChatGPT OAuth, prompt caching)
# ---------------------------------------------------------------------------

_CODEX_BIN = "/home/linuxbrew/.linuxbrew/bin/codex"
_CODEX_AUTH_FILE = str(Path.home() / ".codex" / "auth.json")


def _codex_auth_available() -> bool:
    """Check if codex binary exists and auth file is present."""
    bin_path = os.environ.get("CODEX_BIN", _CODEX_BIN)
    if not (os.path.isfile(bin_path) and os.access(bin_path, os.X_OK)):
        return False
    auth_path = os.environ.get("CODEX_AUTH_FILE", _CODEX_AUTH_FILE)
    return os.path.isfile(auth_path)


class CodexCLIAdapter(LLMAdapter):
    """Adapter using `codex exec --json` subprocess.

    Uses ChatGPT OAuth credentials from ~/.codex/auth.json — no separate API
    key needed. Supports prompt caching (cached_input_tokens in usage).
    Tools are simulated via JSON-in-prompt (same approach as ClaudeSubprocessAdapter).

    Recommended for default orchestration steps. Use ClaudeSubprocessAdapter
    with model=MODEL_POWER (Opus) for heavy reasoning tasks.
    """

    backend = "codex"

    def __init__(
        self,
        model: str = MODEL_CHEAP,
        codex_bin: str = _CODEX_BIN,
        timeout: int = 300,
    ):
        self.model_key = model
        self.codex_bin = os.environ.get("CODEX_BIN", codex_bin)
        self.timeout = timeout

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        timeout: Optional[int] = None,
        **kwargs,
    ) -> LLMResponse:
        prompt = self._build_prompt(messages, tools)
        model_str = resolve_model("codex", self.model_key)
        _timeout = timeout or self.timeout

        cmd = [
            self.codex_bin,
            "exec",
            "--json",
            "--model", model_str,
            "-c", "approval_policy=\"never\"",
            "-",  # read prompt from stdin
        ]

        try:
            result = _run_subprocess_safe(cmd, input=prompt, timeout=_timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"codex binary not found at {self.codex_bin}")

        if result.returncode != 0:
            # stdout holds merged stdout+stderr.
            merged = result.stdout.strip()
            detail = merged[:300] or "(no output)"
            raise RuntimeError(f"codex subprocess failed (rc={result.returncode}): {detail}")

        return self._parse_jsonl_output(result.stdout, tools)

    def _build_prompt(self, messages: List[LLMMessage], tools: Optional[List[LLMTool]]) -> str:
        """Flatten messages into a single prompt string for codex exec stdin."""
        parts = []

        system_parts = [m.content for m in messages if m.role == "system"]
        non_system = [m for m in messages if m.role != "system"]

        if system_parts:
            parts.append("[SYSTEM INSTRUCTIONS]\n" + "\n\n".join(system_parts))

        if tools:
            tool_list = "\n".join(
                f'- "{t.name}": {t.description}\n  Arguments: {json.dumps(t.parameters.get("properties", {}), indent=2)}'
                for t in tools
            )
            parts.append(_TOOL_INJECTION_TEMPLATE.format(tool_list=tool_list))

        parts.append("[END SYSTEM INSTRUCTIONS]\n")

        for m in non_system:
            if m.role == "user":
                parts.append(f"User: {m.content}")
            elif m.role == "assistant":
                parts.append(f"Assistant: {m.content}")

        return "\n\n".join(parts)

    def _parse_jsonl_output(self, stdout: str, tools: Optional[List[LLMTool]]) -> LLMResponse:
        """Parse JSONL lines from `codex exec --json` output."""
        content = ""
        input_tokens = 0
        output_tokens = 0
        cached_tokens = 0

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    content = item.get("text", "")

            elif event_type == "turn.completed":
                usage = event.get("usage", {})
                input_tokens = usage.get("input_tokens", 0)
                cached_tokens = usage.get("cached_input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)

        # Parse tool calls if tools were requested
        tool_calls: List[ToolCall] = []
        if tools and content:
            tc = self._parse_tool_call(content, tools)
            if tc:
                tool_calls = [tc]
                content = ""

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="end_turn",
            model=resolve_model("codex", self.model_key),
            input_tokens=input_tokens + cached_tokens,
            output_tokens=output_tokens,
            backend=self.backend,
        )

    def _parse_tool_call(self, text: str, tools: List[LLMTool]) -> Optional[ToolCall]:
        """Extract a tool call from the model's JSON response."""
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start:end])
        except json.JSONDecodeError:
            return None
        tool_name = data.get("tool")
        if not tool_name:
            return None
        valid_names = {t.name for t in tools}
        if tool_name not in valid_names:
            return None
        args = {k: v for k, v in data.items() if k != "tool"}
        return ToolCall(name=tool_name, arguments=args)


# ---------------------------------------------------------------------------
# AnthropicSDKAdapter — uses anthropic Python SDK
# ---------------------------------------------------------------------------

class AnthropicSDKAdapter(LLMAdapter):
    """Adapter using the Anthropic Python SDK with ANTHROPIC_API_KEY."""

    backend = "anthropic"

    def __init__(self, api_key: str, model: str = MODEL_CHEAP):
        self._api_key = api_key
        self.model_key = model
        self._client = None  # lazy-init, reused across calls

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        import anthropic

        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._api_key)
        client = self._client
        model_str = resolve_model("anthropic", self.model_key)

        system = "\n\n".join(m.content for m in messages if m.role == "system")
        msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

        api_kwargs: Dict[str, Any] = {
            "model": model_str,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        if system:
            api_kwargs["system"] = system

        # Extended thinking: pass budget to Anthropic API when requested
        _thinking = kwargs.get("thinking_budget") if "thinking_budget" in kwargs else thinking_budget
        if _thinking and _thinking > 0:
            api_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": _thinking,
            }
            # Thinking requires max_tokens large enough for thinking + output
            if max_tokens < _thinking + 4096:
                api_kwargs["max_tokens"] = _thinking + 4096
            # Extended thinking doesn't support custom temperature
            # (API rejects temperature with thinking enabled)
        else:
            api_kwargs["temperature"] = temperature

        if tools:
            api_kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
            if tool_choice == "required":
                api_kwargs["tool_choice"] = {"type": "any"}
            elif tool_choice != "auto":
                api_kwargs["tool_choice"] = {"type": tool_choice}

        resp = _retry_complete(client.messages.create, **api_kwargs)

        content = ""
        thinking_content = ""
        tool_calls: List[ToolCall] = []
        for block in resp.content:
            if hasattr(block, "type") and block.type == "thinking":
                thinking_content += getattr(block, "thinking", "")
            elif hasattr(block, "text"):
                content += block.text
            elif hasattr(block, "type") and block.type == "tool_use":
                tool_calls.append(ToolCall(
                    name=block.name,
                    arguments=block.input,
                    call_id=block.id,
                ))

        # If thinking was used, prepend a brief note (the thinking itself
        # isn't returned to callers — it's internal reasoning).
        # Log it for observability.
        if thinking_content:
            log.debug("thinking (%d chars) for model=%s", len(thinking_content), model_str)

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            model=resp.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            backend=self.backend,
        )


# ---------------------------------------------------------------------------
# OpenRouterAdapter — HTTP to openrouter.ai (OpenAI-compatible)
# ---------------------------------------------------------------------------

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterAdapter(LLMAdapter):
    """HTTP adapter for OpenRouter. No SDK dependency — just requests."""

    backend = "openrouter"

    def __init__(self, api_key: str, model: str = MODEL_CHEAP, site_name: str = "poe-orch"):
        self._api_key = api_key
        self.model_key = model
        self._site_name = site_name

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        import requests

        model_str = resolve_model("openrouter", self.model_key)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "X-Title": self._site_name,
        }
        payload: Dict[str, Any] = {
            "model": model_str,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
            payload["tool_choice"] = tool_choice

        def _do_request():
            r = requests.post(_OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            return r
        resp = _retry_complete(_do_request)
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        stop_reason = choice.get("finish_reason", "end_turn")

        tool_calls: List[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args, call_id=tc.get("id", "")))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=data.get("model", model_str),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            backend=self.backend,
        )


# ---------------------------------------------------------------------------
# OpenAIAdapter — direct OpenAI or compatible endpoint
# ---------------------------------------------------------------------------

class OpenAIAdapter(LLMAdapter):
    """Adapter for OpenAI API (or any OpenAI-compatible endpoint)."""

    backend = "openai"

    def __init__(self, api_key: str, model: str = MODEL_CHEAP, base_url: str = "https://api.openai.com/v1"):
        self._api_key = api_key
        self.model_key = model
        self._base_url = base_url.rstrip("/")

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
        **kwargs,
    ) -> LLMResponse:
        import requests

        model_str = resolve_model("openai", self.model_key)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": model_str,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in tools
            ]
            payload["tool_choice"] = tool_choice

        def _do_request():
            r = requests.post(f"{self._base_url}/chat/completions", headers=headers, json=payload, timeout=120)
            r.raise_for_status()
            return r
        resp = _retry_complete(_do_request)
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""
        stop_reason = choice.get("finish_reason", "end_turn")

        tool_calls: List[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            raw_args = fn.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {"_raw": raw_args}
            tool_calls.append(ToolCall(name=fn.get("name", ""), arguments=args, call_id=tc.get("id", "")))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=data.get("model", model_str),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            backend=self.backend,
        )


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------

def _load_env_file(path: Optional[str] = None) -> Dict[str, str]:
    """Load key=value pairs from an env file."""
    try:
        from config import load_credentials_env, credentials_env_file
        if path is None:
            return load_credentials_env()
        return load_credentials_env()  # path arg kept for compat; use config resolution
    except Exception:
        pass
    result: Dict[str, str] = {}
    env_path = path or str(Path.home() / ".poe" / "workspace" / "secrets" / ".env")
    if not os.path.exists(env_path):
        return result
    try:
        for line in open(env_path).readlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip().strip('"').strip("'")
    except ImportError:
        pass
    return result


def _get_key(name: str, env_vars: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Get a credential from env or loaded env file."""
    v = os.environ.get(name)
    if v:
        return v
    if env_vars:
        return env_vars.get(name)
    return None


def _claude_bin_available() -> bool:
    """Check if claude binary is accessible and working."""
    bin_path = os.environ.get("CLAUDE_BIN", _CLAUDE_BIN)
    return os.path.isfile(bin_path) and os.access(bin_path, os.X_OK)


# ---------------------------------------------------------------------------
# Backend-order config
# ---------------------------------------------------------------------------

# Default auto-detect order. Configurable via ~/.poe/config.yml:
#     model:
#       backend_order: [subprocess, anthropic, openrouter, openai]
#
# Rationale: anthropic first (native tool calls, no routing overhead);
# subprocess second (always available on this box, no API credits);
# openrouter/openai last (billed routes).
DEFAULT_BACKEND_ORDER = ["anthropic", "subprocess", "openrouter", "openai"]

_KNOWN_BACKENDS = {"anthropic", "openrouter", "openai", "subprocess", "codex"}


def _get_backend_order() -> List[str]:
    """Resolve the ordered list of backends to try in auto-detect mode.

    Reads `model.backend_order` from config. Unknown names are dropped with a
    warning; an empty/missing list falls back to DEFAULT_BACKEND_ORDER. Names
    are lowercased so the YAML is forgiving about case.
    """
    try:
        from config import get as _config_get
        raw = _config_get("model.backend_order", None)
    except Exception:
        raw = None

    if not raw:
        return list(DEFAULT_BACKEND_ORDER)
    if not isinstance(raw, list):
        log.warning("config model.backend_order must be a list, got %s — using default", type(raw).__name__)
        return list(DEFAULT_BACKEND_ORDER)

    cleaned: List[str] = []
    seen: set = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if not name or name in seen:
            continue
        if name not in _KNOWN_BACKENDS:
            log.warning("config model.backend_order: unknown backend %r — skipping", name)
            continue
        cleaned.append(name)
        seen.add(name)

    return cleaned or list(DEFAULT_BACKEND_ORDER)


# ---------------------------------------------------------------------------
# Factory — auto-detect or explicit backend
# ---------------------------------------------------------------------------

def build_adapter(
    backend: str = "auto",
    model: str = MODEL_DEFAULT,
    *,
    api_key: Optional[str] = None,
    timeout: Optional[int] = None,
) -> LLMAdapter:
    """Build an LLM adapter with auto-detection or explicit backend choice.

    Args:
        backend: One of "auto", "subprocess", "anthropic", "openrouter", "openai".
                 "auto" tries each in priority order until one works.
        model:   MODEL_CHEAP | MODEL_MID | MODEL_POWER, or a raw model string.
        api_key: Explicit API key (overrides env detection).
        timeout: Override the subprocess adapter's per-call timeout in seconds.
                 Default is 300s. Increase for long research steps.

    Returns:
        A ready-to-use LLMAdapter.

    Raises:
        RuntimeError: if no backend can be configured.
    """
    env = _load_env_file()

    if backend == "codex":
        if not _codex_auth_available():
            raise RuntimeError("codex not available: binary missing or ~/.codex/auth.json not found")
        return CodexCLIAdapter(model=model)

    if backend == "subprocess" or backend == "claude":
        if not _claude_bin_available():
            raise RuntimeError(f"claude binary not found at {_CLAUDE_BIN}")
        kwargs = {"timeout": timeout} if timeout is not None else {}
        return ClaudeSubprocessAdapter(model=model, **kwargs)

    if backend == "anthropic":
        key = api_key or _get_key("ANTHROPIC_API_KEY", env)
        if not key:
            raise RuntimeError("No ANTHROPIC_API_KEY found")
        return AnthropicSDKAdapter(api_key=key, model=model)

    if backend == "openrouter":
        key = api_key or _get_key("OPENROUTER_API_KEY", env)
        if not key:
            raise RuntimeError("No OPENROUTER_API_KEY found")
        return OpenRouterAdapter(api_key=key, model=model)

    if backend == "openai":
        key = api_key or _get_key("OPENAI_API_KEY", env)
        if not key:
            raise RuntimeError("No OPENAI_API_KEY found")
        return OpenAIAdapter(api_key=key, model=model)

    # Auto-detect
    assert backend == "auto", f"Unknown backend: {backend!r}"

    # POE_BACKEND env var overrides auto-detection priority without forcing a specific key
    _poe_backend = os.environ.get("POE_BACKEND", "").strip().lower()
    if _poe_backend and _poe_backend != "auto":
        return build_adapter(backend=_poe_backend, model=model, api_key=api_key, timeout=timeout)

    # Explicit api_key overrides — try Anthropic first, then OpenRouter
    if api_key:
        key_prefix = api_key[:6]
        if key_prefix.startswith("sk-ant"):
            return AnthropicSDKAdapter(api_key=api_key, model=model)
        return OpenRouterAdapter(api_key=api_key, model=model)

    # Walk the configured backend order, build all available adapters, and
    # return a FailoverAdapter that tries each in priority order at runtime.
    # Previously: first-in-list wins (no runtime failover across backends).
    # Now: primary adapter is tried first; if it returns 402/4xx/5xx, the
    # next available backend is tried automatically.
    order = _get_backend_order()
    available: List[LLMAdapter] = []
    power_fallback_warned = False
    for name in order:
        if name == "anthropic":
            key = _get_key("ANTHROPIC_API_KEY", env)
            if key:
                available.append(AnthropicSDKAdapter(api_key=key, model=model))
        elif name == "openrouter":
            key = _get_key("OPENROUTER_API_KEY", env)
            if key:
                available.append(OpenRouterAdapter(api_key=key, model=model))
        elif name == "openai":
            key = _get_key("OPENAI_API_KEY", env)
            if key:
                available.append(OpenAIAdapter(api_key=key, model=model))
        elif name == "subprocess":
            if _claude_bin_available():
                if model == MODEL_POWER and not power_fallback_warned:
                    # Opus over `claude -p` is flaky on complex steps — warn but honor config.
                    log.warning(
                        "build_adapter: MODEL_POWER resolving to subprocess (claude -p) "
                        "per backend_order. Opus via subprocess is unreliable for long "
                        "multi-step work; set an API key or reorder `model.backend_order`."
                    )
                    power_fallback_warned = True
                available.append(ClaudeSubprocessAdapter(model=model))
        elif name == "codex":
            if _codex_auth_available():
                available.append(CodexCLIAdapter(model=model))
        else:
            log.warning("build_adapter: unknown backend %r in backend_order — skipping", name)

    if not available:
        raise RuntimeError(
            "No LLM backend available. Set ANTHROPIC_API_KEY, OPENROUTER_API_KEY, "
            "OPENAI_API_KEY, or install Claude Code (claude -p) / Codex CLI (codex). "
            f"Tried backend_order={order!r}."
        )

    if len(available) == 1:
        return available[0]  # single backend — no wrapper overhead

    log.debug("build_adapter(auto): %d backends available, using FailoverAdapter: %s",
              len(available), [a.backend for a in available])
    return FailoverAdapter(available)


def detect_available_backends() -> Dict[str, bool]:
    """Return which backends are currently available."""
    env = _load_env_file()
    return {
        "subprocess": _claude_bin_available(),
        "codex":      _codex_auth_available(),  # available but not in auto-detect chain
        "anthropic":  bool(_get_key("ANTHROPIC_API_KEY", env)),
        "openrouter": bool(_get_key("OPENROUTER_API_KEY", env)),
        "openai":     bool(_get_key("OPENAI_API_KEY", env)),
    }


# ---------------------------------------------------------------------------
# Advisor Pattern — Opus at decision points
#
# Sonnet executes every step; at decision points (milestone boundaries,
# stuck detection, evolver meta-improvement) a focused advisory call goes
# to Opus for strategic guidance. Same context window approach: Opus reads
# the current state and returns advice that Sonnet acts on.
#
# Cost profile: one Opus call per decision point (2-5 per mission) vs
# Opus on every step. Estimated 60-80% cost reduction vs full-Opus runs.
# Source: @aakashgupta X research 2026-04-11.
# ---------------------------------------------------------------------------

_ADVISOR_SYSTEM = (
    "You are a strategic advisor. You see the full context of an autonomous "
    "agent's mission: the goal, plan, completed steps, current state, and the "
    "specific decision point where advice is needed.\n\n"
    "Respond with CONCISE, ACTIONABLE advice. No preamble. Lead with the "
    "recommendation, then one line of reasoning. Max 200 words."
)


def advisor_call(
    *,
    goal: str,
    context: str,
    question: str,
    adapter: Optional["LLMAdapter"] = None,
    model: str = MODEL_POWER,
) -> str:
    """Call a power-tier model for strategic advice at a decision point.

    This is NOT the execution model. It's a focused advisory call that reads
    the current context and returns guidance. The execution model (cheap/mid)
    acts on the advice.

    Args:
        goal:     The overall mission goal.
        context:  Current state — completed steps, remaining steps, stuck reasons.
        question: The specific decision: "should we continue, narrow, or abort?"
        adapter:  Optional pre-built power-tier adapter. Built on demand if None.
        model:    Model tier (default: MODEL_POWER / Opus).

    Returns:
        Advisor response text, or empty string if the call fails.
    """
    if adapter is None:
        try:
            adapter = build_adapter(model=model)
        except Exception as exc:
            log.warning("advisor_call: failed to build %s adapter: %s", model, exc)
            return ""

    messages = [
        LLMMessage(role="system", content=_ADVISOR_SYSTEM),
        LLMMessage(
            role="user",
            content=(
                f"GOAL: {goal}\n\n"
                f"CURRENT STATE:\n{context}\n\n"
                f"DECISION POINT: {question}"
            ),
        ),
    ]

    try:
        _adv_kwargs: Dict[str, Any] = {"max_tokens": 1024, "temperature": 0.2}
        # Enable mid-level thinking for advisory calls (strategic decisions)
        if getattr(adapter, "backend", "") == "anthropic":
            _adv_kwargs["thinking_budget"] = THINKING_MID
        response = _retry_complete(
            adapter.complete, messages, **_adv_kwargs,
        )
        log.info(
            "advisor_call: %d in + %d out tokens, model=%s",
            response.input_tokens, response.output_tokens, response.model or model,
        )
        return response.content.strip()
    except Exception as exc:
        log.warning("advisor_call failed: %s", exc)
        return ""
