#!/usr/bin/env python3
"""Model-agnostic LLM adapter layer for Poe orchestration.

Supports OpenRouter as the primary routing layer with Claude Haiku as default
model (cheap for development, swap to Sonnet/Opus for production runs).

Usage:
    adapter = build_adapter()  # reads OPENROUTER_API_KEY from env
    response = adapter.complete([
        LLMMessage("system", "You are a planning assistant."),
        LLMMessage("user", "Break this goal into 3 steps: research X"),
    ])
    print(response.content)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
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


@dataclass
class LLMTool:
    name: str
    description: str
    parameters: Dict[str, Any]  # JSON Schema object


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

class LLMAdapter:
    """Abstract base. Subclass and implement `complete`."""

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenRouter adapter
# ---------------------------------------------------------------------------

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Model tiers — callers can reference these by name
MODEL_CHEAP = "anthropic/claude-haiku-4-5"       # default dev model (~$0.25/M)
MODEL_MID   = "anthropic/claude-sonnet-4-5"      # mid tier
MODEL_POWER = "anthropic/claude-opus-4-5"        # full power
MODEL_FALLBACK = "openai/gpt-4o-mini"            # if Anthropic is down


class OpenRouterAdapter(LLMAdapter):
    """HTTP adapter for OpenRouter. No SDK dependency — just requests."""

    def __init__(self, api_key: str, model: str = MODEL_CHEAP, site_url: str = "", site_name: str = "poe-orch"):
        self._api_key = api_key
        self.model = model
        self._site_url = site_url
        self._site_name = site_name

    def complete(
        self,
        messages: List[LLMMessage],
        *,
        tools: Optional[List[LLMTool]] = None,
        tool_choice: str = "auto",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> LLMResponse:
        import requests  # stdlib-ish, always available

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self._site_url,
            "X-Title": self._site_name,
        }

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
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
            tool_calls.append(ToolCall(
                name=fn.get("name", ""),
                arguments=args,
                call_id=tc.get("id", ""),
            ))

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_adapter(
    model: str = MODEL_CHEAP,
    *,
    api_key: Optional[str] = None,
    env_file: str = "/home/clawd/.openclaw/workspace/secrets/recovered/runtime-credentials/.env",
) -> OpenRouterAdapter:
    """Build an OpenRouter adapter, loading API key from env or env_file."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key and os.path.exists(env_file):
        for line in open(env_file).readlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if not key:
        raise RuntimeError(
            "No OPENROUTER_API_KEY found. Set env var or pass api_key= to build_adapter()."
        )
    return OpenRouterAdapter(api_key=key, model=model)
