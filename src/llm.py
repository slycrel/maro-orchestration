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
    2. ANTHROPIC_API_KEY env var → AnthropicSDKAdapter
    3. OPENROUTER_API_KEY env var → OpenRouterAdapter
    4. OPENAI_API_KEY env var → OpenAIAdapter
    5. codex binary available + auth → CodexCLIAdapter (GPT-5.4 via ChatGPT OAuth, prompt caching)
    6. claude binary in PATH → ClaudeSubprocessAdapter (always available on this box)

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
        MODEL_CHEAP: "anthropic/claude-haiku-4-5",
        MODEL_MID:   "anthropic/claude-sonnet-4-5",
        MODEL_POWER: "anthropic/claude-opus-4-5",
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
        **kwargs,
    ) -> LLMResponse:
        raise NotImplementedError

    def _resolved_model(self, model_key: str) -> str:
        return resolve_model(self.backend, model_key)


# ---------------------------------------------------------------------------
# ClaudeSubprocessAdapter — uses `claude -p` (no API key needed)
# ---------------------------------------------------------------------------

_CLAUDE_BIN = "/home/clawd/.local/bin/claude"

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


class ClaudeSubprocessAdapter(LLMAdapter):
    """Adapter using `claude -p` subprocess. Works anywhere Claude Code is installed.

    Tool calls are simulated via JSON-in-prompt: tools are described in the
    system prompt as JSON schema, and the model responds with a JSON object
    that the adapter parses back into ToolCall objects.
    """

    backend = "subprocess"

    def __init__(self, model: str = MODEL_CHEAP, claude_bin: str = _CLAUDE_BIN, timeout: int = 300):
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
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=_timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"claude binary not found at {self.claude_bin}")

        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout_hint = result.stdout.strip()[:200] if result.stdout.strip() else ""
            detail = stderr[:300] or stdout_hint or "(no output)"

            # Rate limit detection: "hit your limit" or "resets" in output
            _combined = (detail + " " + stdout_hint).lower()
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
                        result = subprocess.run(
                            cmd, input=prompt, capture_output=True, text=True, timeout=_timeout,
                        )
                    except subprocess.TimeoutExpired:
                        log.warning("rate limit retry timed out after %ds, will retry", _timeout)
                        continue
                    if result.returncode == 0:
                        _retry_success = True
                        break
                    # Check if still rate-limited
                    _retry_combined = (result.stderr + " " + result.stdout).lower()
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
                        _f.write(f"--- STDERR ---\n{result.stderr}\n--- STDOUT ---\n{result.stdout[:2000]}\n")
                        _f.write(f"--- PROMPT (first 3000 chars) ---\n{prompt[:3000]}\n")
                except Exception:
                    pass
                raise RuntimeError(f"claude subprocess failed (rc={result.returncode}): {detail}")

        # Parse JSON output
        try:
            data = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            # Fallback: treat as plain text
            return LLMResponse(
                content=result.stdout.strip(),
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
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=_timeout,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"codex binary not found at {self.codex_bin}")

        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout_hint = result.stdout.strip()[:200] if result.stdout.strip() else ""
            detail = stderr[:300] or stdout_hint or "(no output)"
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

        client = anthropic.Anthropic(api_key=self._api_key)
        model_str = resolve_model("anthropic", self.model_key)

        system = "\n\n".join(m.content for m in messages if m.role == "system")
        msgs = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]

        kwargs: Dict[str, Any] = {
            "model": model_str,
            "max_tokens": max_tokens,
            "messages": msgs,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in tools
            ]
            if tool_choice == "required":
                kwargs["tool_choice"] = {"type": "any"}
            elif tool_choice != "auto":
                kwargs["tool_choice"] = {"type": tool_choice}

        resp = client.messages.create(**kwargs)

        content = ""
        tool_calls: List[ToolCall] = []
        for block in resp.content:
            if hasattr(block, "text"):
                content += block.text
            elif hasattr(block, "type") and block.type == "tool_use":
                tool_calls.append(ToolCall(
                    name=block.name,
                    arguments=block.input,
                    call_id=block.id,
                ))

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

        resp = requests.post(_OPENROUTER_URL, headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
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

        resp = requests.post(f"{self._base_url}/chat/completions", headers=headers, json=payload, timeout=120)
        resp.raise_for_status()
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

    # Explicit api_key overrides — try Anthropic first, then OpenRouter
    if api_key:
        key_prefix = api_key[:6]
        if key_prefix.startswith("sk-ant"):
            return AnthropicSDKAdapter(api_key=api_key, model=model)
        return OpenRouterAdapter(api_key=api_key, model=model)

    # 1. Anthropic SDK (cleanest native tool support, no routing overhead)
    key = _get_key("ANTHROPIC_API_KEY", env)
    if key:
        return AnthropicSDKAdapter(api_key=key, model=model)

    # 2. Claude subprocess — always available on this box, no credits needed.
    #    CodexCLIAdapter exists but is NOT in auto-detection: it wraps `codex exec`
    #    which is an agentic subprocess (same fundamental cost model as claude -p)
    #    and the OAuth token does not work with the public OpenAI API directly.
    #    Use build_adapter("codex") explicitly if you want to experiment with it.
    if _claude_bin_available():
        return ClaudeSubprocessAdapter(model=model)

    # 4. OpenRouter (multi-model routing, requires credits)
    key = _get_key("OPENROUTER_API_KEY", env)
    if key:
        return OpenRouterAdapter(api_key=key, model=model)

    # 5. OpenAI
    key = _get_key("OPENAI_API_KEY", env)
    if key:
        return OpenAIAdapter(api_key=key, model=model)

    raise RuntimeError(
        "No LLM backend available. Set ANTHROPIC_API_KEY, OPENROUTER_API_KEY, "
        "OPENAI_API_KEY, or install Claude Code (claude -p) / Codex CLI (codex)."
    )


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
