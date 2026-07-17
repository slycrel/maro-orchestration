#!/usr/bin/env python3
"""Platform-agnostic LLM adapter layer for Maro orchestration.

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
    2. MARO_BACKEND env var (single backend, no fallback)
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

import contextlib
import contextvars
import hashlib
import json
import logging
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from llm_parse import safe_float

log = logging.getLogger("maro.llm")


# ---------------------------------------------------------------------------
# Ambient subprocess working directory (the "where do agentic writes land" fix)
# ---------------------------------------------------------------------------
# The executor binds cwd per-call (step_exec passes cwd=project_dir). But the
# non-executor agentic paths — verification_agent, quality_gate, pre_flight,
# refinement, and claim_probe's settled_by_command runner — used to inherit
# whatever directory Maro was launched from. When one of those agents did real
# tool work (e.g. a verifier re-creating + running a script to "check" it),
# files leaked into the user's cwd AND the verifier fabricated ground truth it
# couldn't find at the cited path. This ContextVar is the run-scoped default:
# run_agent_loop sets it to the project dir, so EVERY agentic call defaults
# in-workspace unless a caller explicitly overrides cwd. NOW-lane leaves it
# unset (None) → inherits launch cwd, which is what an interactive ask wants.
_DEFAULT_SUBPROCESS_CWD: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "maro_default_subprocess_cwd", default=None
)


def get_default_subprocess_cwd() -> Optional[str]:
    """Run-scoped default cwd for agentic subprocesses (None → inherit launch cwd)."""
    return _DEFAULT_SUBPROCESS_CWD.get()


def set_default_subprocess_cwd(path: Optional[str]) -> None:
    """Set the run-scoped default agentic cwd. Pass None to clear."""
    _DEFAULT_SUBPROCESS_CWD.set(str(path) if path else None)


@contextlib.contextmanager
def default_subprocess_cwd(path: Optional[str]):
    """Scope the default agentic cwd to a block, restoring the prior value after."""
    token = _DEFAULT_SUBPROCESS_CWD.set(str(path) if path else None)
    try:
        yield
    finally:
        _DEFAULT_SUBPROCESS_CWD.reset(token)


# Run-scoped extra writable roots for the containerized executor (C3,
# docs/CONTAINER_EXECUTOR_DESIGN.md §4). run_agent_loop assembles the goal's
# declared roots + validate.write_fence_allow here so the container's mount map
# mirrors the run's write fence (the cwd is always rw; host /tmp + the workspace
# root are deliberately NOT mounted). Read only in the container branch of
# _run_subprocess_safe; empty everywhere else. Analogous to _DEFAULT_SUBPROCESS_CWD.
_DEFAULT_CONTAINER_RW_ROOTS: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar(
    "maro_default_container_rw_roots", default=None
)


def get_default_container_rw_roots() -> list:
    """Run-scoped extra rw mount roots for containerized executor calls."""
    return list(_DEFAULT_CONTAINER_RW_ROOTS.get() or [])


def set_default_container_rw_roots(roots: Optional[list]) -> None:
    """Set the run-scoped extra rw mount roots (pass None/empty to clear)."""
    _DEFAULT_CONTAINER_RW_ROOTS.set(list(roots) if roots else None)


# ---------------------------------------------------------------------------
# Runaway cost circuit (BACKLOG #23e — mid-step granularity)
# ---------------------------------------------------------------------------
# The loop's cost breaker only checks BETWEEN steps; run 8a20665f's step 9
# burned $2.04 in one subprocess call and landed the run at $4.26 against a
# $2.40 ceiling before the breaker could see it. This meter closes half that
# gap: every call through FailoverAdapter accrues its estimated cost, and
# once the run's spend crosses the runaway ceiling, further calls are REFUSED
# at the seam (pre-call, zero cost) instead of waiting for the step boundary.
#
# Deliberately runaway-only (Jeremy, 2026-07-11): "we need to be careful we
# don't create churn and more waste by stopping and retrying as we do
# allowing legit hard things to finish long tasks [...] and put a cost
# ceiling on our orchestrator's capability in a bad way." The ceiling is a
# MULTIPLE of the run budget (default 1.5x), above the between-step hard stop
# (budget + 20% slush) — a run doing legitimate long work under its budget
# never sees this; only a step already blowing past the whole-run ceiling
# does. It cannot kill a call already in flight (that needs stream-side
# accounting in the subprocess lane — still open in BACKLOG #23e); it stops
# the NEXT call, which is what turns one $2 overshoot into not-four.
#
# Armed by agent_loop around the execute phase only (decompose/finalize/
# closure/quality-gate spend is not metered and can never be refused —
# the budget-breaker demotion bug, 8f8344a, is the lesson: post-completion
# accounting must not kill a finished run).
_RUN_COST_METER: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "maro_run_cost_meter", default=None
)


def arm_cost_meter(ceiling_usd: float):
    """Arm the run-scoped runaway circuit; returns a disarm() callable.

    The state dict is shared by reference across thread fan-out (contextvars
    copy_context at submit copies the mapping, not the dict), so parallel
    peers accrue into one meter.
    """
    import threading
    state: Dict[str, Any] = {
        "spent_usd": 0.0,
        "ceiling_usd": float(ceiling_usd),
        "lock": threading.Lock(),
    }
    token = _RUN_COST_METER.set(state)

    def _disarm() -> None:
        try:
            _RUN_COST_METER.reset(token)
        except Exception:
            _RUN_COST_METER.set(None)

    return _disarm


def cost_meter_state() -> Optional[Dict[str, float]]:
    """Read-only snapshot of the armed meter (None when disarmed)."""
    state = _RUN_COST_METER.get()
    if state is None:
        return None
    return {"spent_usd": state["spent_usd"], "ceiling_usd": state["ceiling_usd"]}


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
        MODEL_CHEAP: "anthropic/claude-haiku-4.5",
        MODEL_MID:   "anthropic/claude-sonnet-4.6",
        MODEL_POWER: "anthropic/claude-opus-4.6",
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
    input_tokens: int = 0          # total input volume, INCLUDING cache reads
    output_tokens: int = 0
    cache_read_tokens: int = 0     # portion of input_tokens served from cache (~0.1x cost)
    cost_usd: float = 0.0          # provider-reported billed cost when available
    backend: str = ""
    # Real tools the inner agent actually invoked this call (subprocess agents
    # only; empty otherwise). Each entry: {name, input, output, is_error, id}.
    # This is ground truth for "did it really run X / write Y" — see
    # _parse_stream_json. Other adapters leave it empty and verifiers skip.
    tool_events: List[dict] = field(default_factory=list)
    session_id: str = ""          # subprocess conversation identity, when exposed
    session_resumed: bool = False  # this call used --resume (not a fresh fallback)

    @property
    def fresh_input_tokens(self) -> int:
        """Input tokens actually (re)computed this call — total minus cache hits.

        This is the cost-relevant figure for alarms: a worker re-reading a
        growing file pays ~full price only for what's NOT already cached.
        """
        return max(0, self.input_tokens - self.cache_read_tokens)


@dataclass
class LLMTool:
    name: str
    description: str
    parameters: Dict[str, Any]    # JSON Schema object


# ---------------------------------------------------------------------------
# Streaming-iterator protocol (BACKLOG #14)
# ---------------------------------------------------------------------------
# The four adapters already share the LLMAdapter base; the piece that was
# still copy-pasted per backend was "how does a stream of low-level signals
# from the backend become an LLMResponse". This is the canonical vocabulary
# for that stream, and `LLMAdapter._collect` is the ONE place that folds it
# into a response — written once, reused by every adapter that chooses to
# express its `complete()` as `self._collect(self._stream_events(...))`
# instead of hand-assembling an LLMResponse inline.
#
# Scope, deliberately: this does not make any backend deliver tokens any
# earlier than it does today. ClaudeSubprocessAdapter and CodexCLIAdapter
# still capture the whole subprocess output before translating it (the
# payload-first rc handling, rate-limit detection, and codex's usage summary
# all need the full text first) — so `chunk`/`tool_call` events fire in a
# tight loop just ahead of `done`, not truly incrementally. What's real: one
# shared consumption contract (a stalled/errored stream raises in exactly one
# place, `_collect`) instead of each adapter hand-rolling its own "now build
# the LLMResponse" tail. True mid-flight delivery would mean turning
# `_run_subprocess_safe`'s poll loop itself into a generator — out of scope
# here (see its docstring; that function is the liveness/kill/container
# wrap already shared across both subprocess adapters and is not touched by
# this change).
@dataclass
class StreamEvent:
    """One low-level unit from an adapter's stream, in the shape every
    `complete()` can be expressed in terms of.

    kind:
      "chunk"     — an incremental content delta (informational; the
                    terminal "done" event's `response`, when present, is
                    always authoritative over accumulated chunk text).
      "tool_call" — a tool invocation the model requested.
      "done"      — terminal success. `response`, when set, is the fully
                    assembled LLMResponse and short-circuits accumulation
                    (used by adapters that already know every field —
                    usage, model, tool_events — by the time they can emit
                    a terminal event). When unset, `_collect` builds an
                    LLMResponse from the accumulated chunks/tool_calls.
      "error"     — terminal failure (timeout, kill, backend error). Carries
                    the exception `_collect` raises.

    Exactly one of "done"/"error" ends a well-behaved stream.
    """
    kind: str
    text: str = ""
    tool_call: Optional["ToolCall"] = None
    response: Optional["LLMResponse"] = None
    error: Optional[BaseException] = None


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------

def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception is a transient error worth retrying.

    View over llm_errors.classify_error (BACKEND_RESILIENCE_DESIGN §1) —
    notably, billing failures that *look* transient (OpenAI's
    insufficient_quota 429) are no longer retried.
    """
    from llm_errors import classify_error
    return classify_error(exc).retryable


def _is_failover_error(exc: Exception) -> bool:
    """Return True if the exception warrants trying the next backend.

    View over llm_errors.classify_error. Failover triggers on errors that
    indicate a *backend is unavailable* (auth/billing/5xx-after-retry/
    subprocess death), not errors that indicate the *request is bad* —
    except the known trap: Anthropic credit exhaustion is a 400 that IS a
    backend-unavailable condition and now fails over.
    """
    from llm_errors import classify_error
    return classify_error(exc).failover


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(str(raw).strip()))
    except (TypeError, ValueError):
        return default



def _retry_complete(fn, *args, max_retries: Optional[int] = None, **kwargs) -> "LLMResponse":
    """Wrap an adapter .complete() call with retry on transient errors.

    Exponential backoff: 5s, 15s, 45s. Only retries on rate limits,
    server errors, and connection failures. Non-retryable errors propagate
    immediately.

    `max_retries=None` (the default) takes the env-tunable budget:
    `MARO_LLM_MAX_RETRIES` overrides it when set, else 3. This is useful for
    unattended worker runs where fast failure is better than camping on a
    rate limit for over a minute. Callers that pass an explicit int (e.g.
    the hosted-free adapters' deliberate `max_retries=0` fail-fast contract —
    see GroqAdapter/GeminiAdapter in this file) get exactly that value, not
    subject to the env override: an operator tuning paid-backend resilience
    via the global knob must not silently reactivate exponential backoff on
    a free-tier tier that's designed to fail fast on 429 instead.
    """
    if max_retries is None:
        max_retries = _env_int("MARO_LLM_MAX_RETRIES", 3)
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

    @staticmethod
    def _collect(events: Iterator["StreamEvent"]) -> "LLMResponse":
        """Drain a StreamEvent iterator into the LLMResponse contract.

        This is the one place "what does a stream of events mean" is
        decided: a `chunk`'s text accumulates, a `tool_call` appends, `done`
        returns its carried response if the adapter pre-assembled one
        (the common case today — see the StreamEvent docstring for why),
        else builds an LLMResponse from whatever accumulated, and `error`
        raises. Adapters that express `complete()` as an event stream call
        this once at the end; adapters that still hand-assemble an
        LLMResponse directly are unaffected and don't need to touch this.
        """
        content_parts: List[str] = []
        tool_calls: List["ToolCall"] = []
        for ev in events:
            if ev.kind == "chunk":
                content_parts.append(ev.text)
            elif ev.kind == "tool_call":
                if ev.tool_call is not None:
                    tool_calls.append(ev.tool_call)
            elif ev.kind == "error":
                raise ev.error or RuntimeError(
                    "LLMAdapter._collect: stream ended in error with no exception attached"
                )
            elif ev.kind == "done":
                if ev.response is not None:
                    return ev.response
                return LLMResponse(content="".join(content_parts), tool_calls=tool_calls)
            else:
                log.warning("LLMAdapter._collect: unknown StreamEvent kind %r — ignored", ev.kind)
        # A well-behaved iterator always ends in done/error (both current
        # adapters do). Running dry without a terminal event is exactly what
        # a dropped socket or aborted parser would look like on a future real
        # streaming adapter — silently returning the partial content_parts as
        # a successful LLMResponse would hide that failure as a truncated-but-
        # valid response (adversarial-review finding, 2026-07-13). Raise
        # instead so the caller's normal error handling sees it.
        raise RuntimeError(
            "LLMAdapter._collect: stream exhausted without a done/error terminal "
            f"event ({len(content_parts)} chunk(s), {len(tool_calls)} tool_call(s) "
            "accumulated) — treat as a truncated/dropped stream, not success"
        )


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

    @staticmethod
    def _render_for_record(messages) -> str:
        """Flatten the message list into a single string for the call record."""
        try:
            return "\n\n".join(
                f"[{getattr(m, 'role', '?')}]\n{getattr(m, 'content', '')}"
                for m in (messages or [])
            )
        except Exception:
            return str(messages)

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
        # purpose is a record-mode-only label (BACKLOG #17 sub-item 2) — pop it
        # out before forwarding kwargs to the real adapter, which has no use
        # for it and would otherwise just absorb it silently via **kwargs.
        _purpose = kwargs.pop("purpose", "")
        # Runaway cost circuit: refuse the call BEFORE any backend is tried
        # (pre-call = zero cost). Raised here, not per-adapter, so failover
        # can't route around it.
        _meter = _RUN_COST_METER.get()
        if _meter is not None and _meter["ceiling_usd"] > 0 \
                and _meter["spent_usd"] >= _meter["ceiling_usd"]:
            from llm_errors import BudgetRunawayError
            raise BudgetRunawayError(_meter["spent_usd"], _meter["ceiling_usd"])
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
                # Requested-cap visibility: not every backend enforces
                # max_tokens (the claude CLI ignored it until the no_tools env
                # cap, codex CLI still does). Warn on utility-call overrun —
                # a JSON-contract call blowing its cap is exactly the shape
                # that mangled cobalt-pine's goal rewrite (2026-07-16).
                # Agentic calls stay warning-free: multi-turn output exceeding
                # the default 4096 is normal, not a contract breach.
                _tokens_out = getattr(result, "output_tokens", None)
                if (kwargs.get("no_tools") and _tokens_out and max_tokens
                        and _tokens_out > max_tokens):
                    log.warning(
                        "LLM call (purpose=%r, backend=%s) exceeded requested "
                        "max_tokens: %d > %d — backend did not enforce the cap",
                        _purpose,
                        getattr(adapter, "backend", "?"),
                        _tokens_out, max_tokens,
                    )
                # Record-mode: capture the paid-for call for replay/mining. One
                # seam covers every backend. No-op when off / no active run-dir;
                # never affects the outcome (record_llm_call swallows errors).
                try:
                    from runs import record_llm_call
                    _rec_path = record_llm_call(
                        self._render_for_record(messages),
                        getattr(result, "content", "") or "",
                        backend=getattr(result, "backend", "") or getattr(adapter, "backend", ""),
                        model=getattr(result, "model", "") or "",
                        tool_events=getattr(result, "tool_events", None),
                        tokens_in=getattr(result, "input_tokens", None),
                        tokens_out=getattr(result, "output_tokens", None),
                        max_tokens_requested=max_tokens,
                        purpose=_purpose,
                    )
                    if _rec_path is not None:
                        # Cross-reference for rung-4 step I/O unification: the
                        # loop log links each step to its byte-level record.
                        result.call_record = str(_rec_path)
                except Exception:
                    pass
                # Runaway circuit accrual: estimate this call's cost into the
                # armed meter. Never affects the request outcome.
                if _meter is not None:
                    try:
                        from metrics import estimate_cost
                        _call_cost = estimate_cost(
                            getattr(result, "input_tokens", 0) or 0,
                            getattr(result, "output_tokens", 0) or 0,
                            model=getattr(result, "model", "") or getattr(adapter, "model_key", ""),
                            cache_read_tokens=getattr(result, "cache_read_tokens", 0) or 0,
                        )
                        with _meter["lock"]:
                            _meter["spent_usd"] += _call_cost
                    except Exception:
                        pass
                return result
            except Exception as exc:
                last_exc = exc
                if not _is_failover_error(exc) or idx >= len(self._adapters) - 1:
                    # Non-failover error or last adapter — propagate. When the
                    # user can DO something about it (auth/billing/context),
                    # wrap in BackendError so every surface downstream (CLI
                    # stderr, run metadata, notify) renders the fix instead of
                    # a traceback (BACKEND_RESILIENCE_DESIGN §2).
                    from llm_errors import BackendError, classify_error, is_actionable
                    if not isinstance(exc, BackendError):
                        _info = classify_error(exc, backend=getattr(adapter, "backend", ""))
                        if is_actionable(_info):
                            raise BackendError(_info) from exc
                    raise
                next_backend = getattr(self._adapters[idx + 1], "backend", "?")
                log.warning(
                    "FailoverAdapter: %s failed with %s (%s), trying %s",
                    adapter.backend, type(exc).__name__, str(exc)[:80], next_backend,
                )
                # Actionable-class failovers must not be silently absorbed by a
                # successful failover (design decision: the run should succeed
                # AND the user should learn their credential/billing is dead).
                try:
                    from llm_errors import classify_error as _cls, is_actionable as _act
                    _finfo = _cls(exc, backend=getattr(adapter, "backend", ""))
                    if _act(_finfo):
                        from notify import emit as _notify_emit
                        _notify_emit("backend_actionable", {
                            "status": "degraded",
                            "error_class": _finfo.error_class,
                            "backend": _finfo.backend,
                            "user_action": _finfo.user_action,
                            "summary": f"failed over to {next_backend}: {_finfo.user_action}",
                        })
                except Exception:
                    pass
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


def _parse_ps_cpu_time(s: str) -> float:
    """Parse ps's cumulative CPU time format ``[[dd-]hh:]mm:ss[.ss]`` to seconds."""
    days = 0
    if "-" in s:
        d, s = s.split("-", 1)
        days = int(d)
    parts = [float(p) for p in s.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hh, mm, ss = parts[-3:]
    return days * 86400 + hh * 3600 + mm * 60 + ss


# Seam for tests: point the /proc fast path somewhere else (or nowhere) to
# force the ps fallback. Production value is always the real /proc.
_PROC_PATH = Path("/proc")


def _parse_proc_stat_cpu_ticks(stat_line: str) -> int:
    """Extract utime+stime (in clock ticks) from a /proc/<pid>/stat line.

    Field 2 (comm) is parenthesized and may itself contain spaces and
    parentheses — e.g. ``123 ((my evil) comm) R ...`` — so naive
    whitespace-splitting misnumbers everything after it. Per proc(5) the
    safe parse is: split after the LAST ``)`` in the line; the remaining
    fields start at state (field 3), so utime (field 14) is index 11 and
    stime (field 15) is index 12.

    Raises ValueError/IndexError on malformed input (caller skips the pid).
    """
    rest = stat_line[stat_line.rindex(")") + 1:].split()
    return int(rest[11]) + int(rest[12])


def _session_cpu_ticks_proc(leader_pid: int) -> int:
    """Linux /proc fast path for _session_cpu_ticks (centiseconds).

    Sums utime+stime clock ticks across every pid whose session is
    leader_pid, converted to centiseconds via the real SC_CLK_TCK (usually
    100/s, but kernels can be configured otherwise). Clock ticks give
    ~10ms resolution — the whole point vs ps's 1-second `time` column.

    Per-pid failures (proc vanished mid-read, permission, ESRCH from
    getsid, malformed stat line) are skipped silently. Sweep-level
    failures (no /proc listing, bad SC_CLK_TCK) raise so the caller can
    fall back to ps.
    """
    clk_tck = os.sysconf("SC_CLK_TCK")
    if clk_tck <= 0:
        raise OSError(f"unusable SC_CLK_TCK: {clk_tck}")
    total_ticks = 0
    for entry in os.listdir(_PROC_PATH):
        if not entry.isdigit():
            continue
        try:
            if os.getsid(int(entry)) != leader_pid:
                continue
            # errors="replace": comm is truncated to 15 BYTES by the kernel,
            # so a multibyte process name can split mid-character — strict
            # decoding would UnicodeDecodeError (a ValueError) into the
            # per-pid skip and make that process invisible to the rescue.
            # Everything after comm's ")" terminator is ASCII, so
            # replacement chars in comm are inert to the parse.
            stat_line = (_PROC_PATH / entry / "stat").read_text(errors="replace")
            total_ticks += _parse_proc_stat_cpu_ticks(stat_line)
        except (ValueError, OSError, IndexError):
            continue
    return total_ticks * 100 // clk_tck


def _session_cpu_ticks(leader_pid: int) -> int:
    """Sum CPU time (centiseconds) for every process in leader_pid's session.

    Secondary liveness signal: a silent-but-computing subprocess (e.g. a
    local LLM mid-inference) won't advance its output file's mtime but
    will burn CPU. Summing across the session catches multi-process
    pipelines (e.g. claude CLI → node worker) since `start_new_session=True`
    makes the Popen'd process the session leader.

    2026-07-08: previously read Linux's /proc, which macOS doesn't have at
    all — /proc/{pid} was always missing there, so this signal was silently
    a permanent no-op on every macOS run (a CPU-busy-but-silent subprocess
    would hit the liveness timeout instead of being spared). Now portable:
    `ps` enumerates processes and cumulative CPU time identically on both
    platforms, and session membership is checked via `os.getsid()` — a real
    POSIX syscall — rather than trusting ps's own "sess" column, which on
    macOS's BSD ps turns out to mean something else and does NOT equal the
    session leader's own pid the way it does on Linux.

    2026-07-15: hybrid. The ps-only version traded away resolution: ps's
    `time` column is whole seconds, so the rescue only fired when session
    CPU crossed an integer-second boundary inside a liveness window — with
    `liveness_timeout` < ~1s the signal was effectively dead on an idle box
    (BACKLOG "CPU-liveness rescue is second-granularity blind"). Now: on
    Linux, read /proc/<pid>/stat utime+stime directly (clock ticks, ~10ms
    resolution); if /proc is absent (macOS) or the sweep fails, fall back
    to the 2026-07-08 ps implementation unchanged — the portability fix is
    preserved.

    Unit contract: returns an int in centiseconds regardless of source
    (ticks converted via SC_CLK_TCK; ps seconds * 100). Callers only
    compare successive values for increase, but the unit stays honest.

    Best-effort: any per-proc read/parse failure is skipped silently.
    Returns 0 on total failure (no /proc AND `ps` unavailable), which
    disables the signal — same degradation contract as before.
    """
    if _PROC_PATH.is_dir():
        try:
            return _session_cpu_ticks_proc(leader_pid)
        except Exception:
            pass  # sweep-level failure → ps fallback below
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,time="],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return 0
    total = 0.0
    for line in out.stdout.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, time_str = parts
        try:
            pid = int(pid_str)
            if os.getsid(pid) != leader_pid:
                continue
            total += _parse_ps_cpu_time(time_str.strip())
        except (ValueError, OSError):
            continue
    return int(total * 100)


def _run_subprocess_safe(cmd, *, input=None, timeout=600,
                         liveness_timeout=None, poll_interval=2.0, cwd=None,
                         stream_probe=None, container_name=None,
                         env_extra=None):
    """Run a subprocess in its own process group with streaming + liveness check.

    Streams the subprocess's stdout+stderr (merged) to a single temp file
    so the on-disk view matches what an operator sees on a terminal.
    Three kill conditions:
      1. Wall-clock `timeout` — hard ceiling, same semantics as before.
      2. Liveness: if neither file-mtime advances nor CPU time accumulates
         across the subprocess session for `liveness_timeout` seconds,
         assume the subprocess is hung and kill. The CPU signal prevents
         false-kills of silent-but-computing local models.
      3. `stream_probe` (BACKLOG #23e stream-side accounting): a callable
         fed each poll's newly-arrived complete NDJSON event dicts from the
         merged stream. Returning an Exception kills the subprocess and
         raises that exception (with `.maro_kill_reason` and
         `.maro_partial_output` attached). This is how the runaway cost
         circuit kills a call already in flight instead of only refusing
         the next one. Non-dict lines and parse noise are skipped; probe
         errors are swallowed (accounting must never break the request).

    `liveness_timeout` defaults to min(timeout, 180). Pass 0 or None-like to
    disable (falls back to wall-clock only). Env var `MARO_LIVENESS_TIMEOUT`
    overrides the default for the whole process.

    Partial output captured up to the kill is preserved in the returned
    CompletedProcess.stdout (stderr is empty — both streams merged). This
    lets callers still access accumulated work on timeout, unlike
    communicate().

    Returns a subprocess.CompletedProcess with stdout=merged output and
    stderr="". On wall-clock or liveness timeout raises
    subprocess.TimeoutExpired with `.maro_kill_reason` attached so callers
    can distinguish.
    """
    import signal
    import tempfile
    import time

    if liveness_timeout is None:
        env_override = os.environ.get("MARO_LIVENESS_TIMEOUT")
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

    # Operator-visibility symlink: `tail -f /tmp/maro-current-step.log` from
    # anywhere shows the in-flight subprocess's merged output. Updated
    # atomically on each new subprocess; dangles between steps (by
    # design — means "no step running"). Under concurrent runs the global
    # link is last-writer-wins, so a per-run link
    # `/tmp/maro-current-step-<handle_id>.log` is also written when a
    # run-dir is active. Disable with MARO_CURRENT_STEP_SYMLINK=0.
    if os.environ.get("MARO_CURRENT_STEP_SYMLINK", "1") != "0":
        link_targets = ["/tmp/maro-current-step.log"]
        try:
            from runs import current_handle_id
            hid = current_handle_id()
            if hid:
                link_targets.append(f"/tmp/maro-current-step-{hid}.log")
        except Exception:
            pass
        for link_target in link_targets:
            try:
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

    # Stream-probe incremental reader state: a second read handle on the
    # merged file plus a partial-line buffer (NDJSON events can arrive split
    # across polls).
    _probe_read_f = None
    _probe_buf = b""

    def _drain_new_events():
        """Read bytes appended since the last poll; return parsed event dicts."""
        nonlocal _probe_read_f, _probe_buf
        if _probe_read_f is None:
            _probe_read_f = open(combined_path, "rb")
        chunk = _probe_read_f.read()
        if not chunk:
            return []
        _probe_buf += chunk
        lines = _probe_buf.split(b"\n")
        _probe_buf = lines.pop()  # trailing partial line waits for more bytes
        events = []
        for raw in lines:
            raw = raw.strip()
            if not raw.startswith(b"{"):
                continue
            try:
                ev = json.loads(raw.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(ev, dict):
                events.append(ev)
        return events

    def _cleanup_files():
        try: combined_f.close()
        except Exception: pass
        if _probe_read_f is not None:
            try: _probe_read_f.close()
            except Exception: pass
        for p in (combined_path, stdin_f.name if stdin_f else None):
            if p:
                try: os.unlink(p)
                except OSError: pass

    # Mark Maro-spawned agentic subprocesses so git guards (scripts/hooks/
    # pre-push) can tell a worker from a human: workers may push work
    # branches but not the default branch. workers.allow_main_push=true
    # (config) loosens this for the whole box; a goal can also export
    # MARO_ALLOW_MAIN_PUSH=1 itself when explicitly authorized to push.
    child_env = dict(os.environ)
    child_env["MARO_WORKER_RUN"] = "1"
    if env_extra:
        child_env.update(env_extra)
    try:
        from config import get as _cfg_get
        if bool(_cfg_get("workers.allow_main_push", False)):
            child_env["MARO_ALLOW_MAIN_PUSH"] = "1"
    except Exception:
        pass

    # Bind the subprocess working directory to the caller's workspace when one
    # is supplied and exists. Without this, an agentic subprocess (`claude -p`)
    # inherits the parent's cwd and writes relative paths wherever that happens
    # to be — files leaked outside the run/project dir despite the prompt asking
    # for {project_dir}/. Enforcing cwd makes relative writes land in-workspace.
    _cwd = None
    if cwd:
        if os.path.isdir(cwd):
            _cwd = cwd
        else:
            log.warning(
                "_run_subprocess_safe: cwd %r is not a directory; inheriting parent cwd", cwd
            )

    # Containerized executor (C2/C3, docs/CONTAINER_EXECUTOR_DESIGN.md §2/§4):
    # the caller decides whether this call runs in a container (executor.container
    # on/require + a worker executor step) and passes its name; we own the wrap
    # + kill path here so all the streaming/liveness/probe machinery below is
    # reused unchanged. The container sees only the -e vars we pass (host env is
    # dropped by construction) and the fence-derived mount set: the working dir
    # rw (a self-dev scratch clone when the fence dir is a repo), the run's
    # goal-declared / write-fence-allow roots rw, and configured reference
    # mounts ro. build_mount_map owns the translation (design §4 mount map).
    _container = None
    if container_name and not _cwd:
        # No resolvable working dir means no project mount to give the worker —
        # the container would run in an empty HOME with a filesystem view that
        # silently differs from the host path. Fall back to host rather than
        # ship that split (adversarial-review 2026-07-12).
        log.warning("_run_subprocess_safe: container requested but no working dir "
                    "resolved; running on host instead of an empty container")
    elif container_name:
        import container_exec as _ce
        _worker_env = {k: child_env[k]
                       for k in ("MARO_WORKER_RUN", "MARO_ALLOW_MAIN_PUSH")
                       if k in child_env}
        # Configured read-only reference mounts (executor.container_extra_mounts).
        _ro_mounts = []
        try:
            from config import get as _cfg_get
            _ro_mounts = [str(x) for x in (_cfg_get("executor.container_extra_mounts", []) or []) if x]
        except Exception as _mnt_exc:
            log.debug("container_extra_mounts read failed (non-fatal): %s", _mnt_exc)
        # realpath so a symlinked cwd resolves to the same target build_mount_map
        # binds and the exclusion filter checks (adversarial-review 2026-07-13):
        # -w must name the path that actually exists inside the container.
        _cwd_real = os.path.realpath(_cwd)
        _mounts = _ce.build_mount_map(
            _cwd_real,
            rw_roots=get_default_container_rw_roots(),
            ro_mounts=_ro_mounts,
        )
        # Per-run scratch bound at container /tmp so cross-step scratch survives
        # (C4-BOX follow-up); None when there is no active run dir → /tmp stays
        # ephemeral, unchanged.
        _scratch = _ce.run_scratch_dir()
        cmd = _ce.build_run_command(
            cmd, name=container_name, workdir=_cwd_real,
            mounts=_mounts, worker_env=_worker_env, scratch_dir=_scratch)
        _container = container_name

    proc = subprocess.Popen(
        cmd,
        stdin=stdin_f if stdin_f else subprocess.DEVNULL,
        stdout=combined_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=child_env,
        cwd=_cwd,
    )
    if stdin_f:
        try:
            stdin_f.close()
        except Exception:
            pass

    start = time.monotonic()
    last_seen = start          # monotonic time of most recent activity
    last_mtime = 0.0           # file mtime we've already credited
    # Skip the CPU baseline if the process has already exited — the loop
    # below breaks on its first poll without ever using last_cpu for a
    # comparison, so computing it here is wasted work (and, on the macOS
    # `ps` fallback path of _session_cpu_ticks, spawns a nested `ps`
    # subprocess that tests globally monkeypatching subprocess.Popen to
    # fake a fast-completing process would otherwise intercept, clobbering
    # their captured kwargs from the real Popen call above; the Linux
    # /proc path spawns nothing, but the skip stays for both reasons).
    last_cpu = 0 if proc.poll() is not None else _session_cpu_ticks(proc.pid)
    kill_reason = None
    kill_exc = None            # probe-ordered kill carries its own exception
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

            # Stream-side probe (e.g. runaway cost accounting): parse newly
            # arrived NDJSON events; a returned exception is a kill order.
            if stream_probe is not None:
                try:
                    _events = _drain_new_events()
                    if _events:
                        _probe_exc = stream_probe(_events)
                        if _probe_exc is not None:
                            kill_reason = f"stream probe kill: {_probe_exc}"
                            kill_exc = _probe_exc
                            break
                except Exception as _probe_err:
                    log.debug("stream probe error (non-fatal): %s", _probe_err)

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
            # Kill the container BY NAME first — os.killpg kills the docker
            # client, which does not reliably stop the container (§2 kill path).
            if _container:
                _ce.kill_container(_container)
            try: os.killpg(proc.pid, signal.SIGTERM)
            except OSError: pass
            try: proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(proc.pid, signal.SIGKILL)
                except OSError: pass
                proc.wait(timeout=5)
            stdout = _read_captured()
            _cleanup_files()
            if kill_exc is not None:
                # Probe-ordered kill: raise the probe's exception (e.g.
                # BudgetRunawayError) so callers get the right class — a
                # TimeoutExpired here would ride the timeout-split retry
                # path, which is exactly the churn the circuit prevents.
                kill_exc.maro_kill_reason = kill_reason  # type: ignore[attr-defined]
                kill_exc.maro_partial_output = stdout    # type: ignore[attr-defined]
                raise kill_exc
            exc = subprocess.TimeoutExpired(cmd, timeout or liveness_timeout,
                                            output=stdout, stderr="")
            # Attach reason for caller introspection; not used by base class.
            exc.maro_kill_reason = kill_reason  # type: ignore[attr-defined]
            raise exc
    except subprocess.TimeoutExpired:
        raise
    except Exception:
        if _container:
            _ce.kill_container(_container)
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


def _build_stream_cost_probe(default_model: str = ""):
    """Stream-side half of the runaway cost circuit (BACKLOG #23e residual).

    Returns a `stream_probe` for `_run_subprocess_safe`, or None when no
    cost meter is armed. The pre-call refusal at the FailoverAdapter seam
    stops the call AFTER a runaway one; this probe kills the runaway call
    ITSELF: it accumulates estimated cost from the usage blocks on the
    claude CLI's stream-json assistant events as they arrive, and once
    meter-spend + this call's running estimate crosses the armed ceiling it
    accrues the estimate into the meter and returns a BudgetRunawayError
    (which `_run_subprocess_safe` raises after killing the process group).
    The r4 specimen ($2.04/4.7M tokens in ONE subprocess call, run
    8a20665f step 9) is exactly the shape this catches mid-flight.

    File writes the inner agent already made are on disk and survive the
    kill; only the final narrated result is lost — the loop stops on
    error_class=budget_runaway either way.
    """
    meter = _RUN_COST_METER.get()
    if meter is None or meter["ceiling_usd"] <= 0:
        return None
    try:
        from metrics import estimate_cost
    except Exception:
        return None
    state = {"est_usd": 0.0, "accrued": False}

    def _probe(events):
        for ev in events:
            if ev.get("type") != "assistant":
                continue
            msg = ev.get("message") or {}
            usage = msg.get("usage") or {}
            if not usage:
                continue
            state["est_usd"] += estimate_cost(
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0),
                int(usage.get("output_tokens", 0) or 0),
                model=msg.get("model") or default_model,
                cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
            )
        with meter["lock"]:
            spent = meter["spent_usd"]
        if spent + state["est_usd"] >= meter["ceiling_usd"]:
            if not state["accrued"]:
                state["accrued"] = True
                with meter["lock"]:
                    meter["spent_usd"] += state["est_usd"]
            from llm_errors import BudgetRunawayError
            log.warning(
                "stream cost probe: in-flight call estimate $%.4f pushes run "
                "spend past ceiling $%.2f — killing subprocess",
                state["est_usd"], meter["ceiling_usd"])
            return BudgetRunawayError(spent + state["est_usd"],
                                      meter["ceiling_usd"])
        return None

    return _probe


def _extract_result_object(text: str) -> Optional[dict]:
    """Scan merged stdout+stderr for the claude CLI's `{"type": "result"}`
    object, skipping past warning text and non-result JSON noise."""
    text = (text or "").strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            data, consumed = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(data, dict) and data.get("type") == "result":
            return data
        start = text.find("{", start + consumed)
    return None


def _stringify_tool_result(content) -> str:
    """Flatten a Claude Code tool_result `content` (str | list-of-blocks |
    other) into a plain string for verification."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text", "") if b.get("type") == "text" else json.dumps(b))
            else:
                parts.append(str(b))
        return "\n".join(p for p in parts if p)
    return json.dumps(content)


def _parse_stream_json(text: str) -> dict:
    """Parse `claude -p --output-format stream-json` NDJSON output.

    Returns {result, tool_events, rate_limited}:
      result      — the final {"type":"result"} event dict (or None). Identical
                    payload to the old --output-format json single object, so
                    all downstream result handling is unchanged.
      tool_events — ordered list of {name, input, output, is_error, id} for the
                    REAL tools the inner agent invoked. This is the recovered
                    ground truth: --output-format json only handed us the
                    agent's final narrated message and discarded the actual
                    Bash/Write/Read calls behind it — the done≠achieved blind
                    spot. stream-json exposes them so "ran tests: 142 passed"
                    can be checked against whether a Bash tool actually ran.
      rate_limited — True iff a rate_limit_event reported a non-"allowed"
                    status. Structured signal replacing the old bare "resets"
                    substring match, which now false-positives because every
                    stream embeds resetsAt.

    Tolerant of interleaved system events, non-JSON noise lines, and a trailing
    partial line. Falls back to the whole-text result scanner when no per-line
    events parse (e.g. a pretty-printed single object).
    """
    out = {"result": None, "tool_events": [], "rate_limited": False}
    text = (text or "").strip()
    if not text:
        return out
    uses = []            # ordered [(id, name, input)]
    results_by_id = {}   # id -> {output, is_error}
    saw_any_event = False
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        saw_any_event = True
        etype = ev.get("type")
        if etype == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    uses.append((block.get("id"), block.get("name", ""), block.get("input")))
        elif etype == "user":
            content = (ev.get("message") or {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        results_by_id[block.get("tool_use_id")] = {
                            "output": _stringify_tool_result(block.get("content")),
                            "is_error": bool(block.get("is_error", False)),
                        }
        elif etype == "result":
            out["result"] = ev
        elif etype == "rate_limit_event":
            status = (ev.get("rate_limit_info") or {}).get("status")
            if status is not None and status != "allowed":
                out["rate_limited"] = True
    out["tool_events"] = [
        {
            "name": name,
            "input": inp,
            "output": results_by_id.get(uid, {}).get("output", ""),
            "is_error": results_by_id.get(uid, {}).get("is_error", False),
            "id": uid,
        }
        for (uid, name, inp) in uses
    ]
    if out["result"] is None and not saw_any_event:
        out["result"] = _extract_result_object(text)
    return out


def _extract_success_result(text: str) -> Optional[dict]:
    """Return the parsed claude CLI result payload if `text` contains a
    genuinely successful `--output-format json` result object, else None.

    The CLI can print a complete success result to stdout and still exit
    non-zero (e.g. failing to persist session state after the response). The
    payload, not the exit code, is the ground truth for whether the model
    call succeeded. Note the CLI also reports *errors* with subtype "success"
    plus is_error=true (the message lives in the "result" field), so is_error
    is the load-bearing check.
    """
    data = _extract_result_object(text)
    if (
        data is not None
        and data.get("subtype") == "success"
        and not data.get("is_error", False)
        and "result" in data
    ):
        return data
    return None


def _is_plain_missing_session_error(text: str) -> bool:
    """True only for Claude CLI's pre-execution missing-session error.

    The subprocess capture merges the full NDJSON transcript and stderr. A
    substring search is unsafe because model/tool output can quote the same
    phrase after already performing side effects. The live CLI failure is a
    short plain-text diagnostic, optionally followed by one zero-turn,
    zero-token structured error result. Require exactly that boundary shape
    before a full-prompt retry is allowed.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 3000:
        return False
    plain_lines = []
    events = []
    for line in raw.splitlines():
        line = line.strip()
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                events.append(parsed)
                continue
        except (json.JSONDecodeError, TypeError):
            pass
        plain_lines.append(line)

    def _has_marker(value: Any) -> bool:
        normalized = " ".join(str(value or "").lower().split())
        if normalized.startswith("error:"):
            normalized = normalized[6:].strip()
        return normalized.startswith("no conversation found with session id")

    if not plain_lines or not all(_has_marker(line) for line in plain_lines):
        return False
    if not events:
        return True
    if len(events) != 1:
        return False
    event = events[0]
    usage = event.get("usage") or {}
    errors = event.get("errors") or []
    def _is_zero_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0
    return bool(
        event.get("type") == "result"
        and event.get("subtype") == "error_during_execution"
        and event.get("is_error") is True
        and event.get("num_turns") == 0
        and _is_zero_number(event.get("total_cost_usd"))
        and _is_zero_number(usage.get("input_tokens"))
        and _is_zero_number(usage.get("output_tokens"))
        and errors and all(_has_marker(err) for err in errors)
    )


class _JSONToolPromptMixin:
    """Shared JSON-in-prompt tool-calling machinery for CLI subprocess adapters.

    Tools are described in the system prompt as JSON schema, and the model
    responds with a JSON object that `_parse_tool_call` parses back into a
    `ToolCall`. Used by `ClaudeSubprocessAdapter` and `CodexCLIAdapter`, which
    otherwise talk to unrelated CLIs — this mixin is the part that's identical.
    """

    def _build_prompt(self, messages: List[LLMMessage], tools: Optional[List[LLMTool]]) -> str:
        """Flatten messages into a single prompt string for CLI stdin."""
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


class ClaudeSubprocessAdapter(_JSONToolPromptMixin, LLMAdapter):
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
        no_tools: bool = False,
        executor: bool = False,
        **kwargs,  # absorb unsupported kwargs (e.g. thinking_budget) gracefully
    ) -> LLMResponse:
        # `executor=True` marks a worker EXECUTOR step (the agentic goal work) —
        # the only calls the container lane wraps. Everything else (verify,
        # quality-gate, refinement, planning, health probes) stays on the host
        # even with executor.container=on (see container_exec.resolve_container_run).
        # Always retain the full standalone prompt. A compatible resumed
        # executor session may send only the caller's delta, but an explicitly
        # missing/evicted session falls back to this full prompt exactly once.
        full_prompt = self._build_prompt(messages, tools)

        # Build command
        model_str = resolve_model("subprocess", self.model_key)
        # stream-json (not plain json) so the inner agent's REAL tool calls are
        # visible, not just its final narrated message. --verbose is required by
        # the CLI for stream-json. The final {"type":"result"} event carries the
        # identical payload the old --output-format json produced, so result
        # handling below is unchanged; tool_events are parsed additively.
        # --strict-mcp-config: don't load user-level MCP servers. Worker steps
        # never use them, and the handshake is a per-boot tax on EVERY call —
        # measured 2026-07-11 on this box: a claude.ai Google Drive server in
        # the clawd user config cost ~3.7s/boot (6.4s → 2.7s trivial call),
        # ~1.6 min across the clean Manti re-run's ~26 subprocess calls.
        cmd = [self.claude_bin, "-p", "--output-format", "stream-json", "--verbose",
               "--dangerously-skip-permissions", "--strict-mcp-config"]
        if no_tools:
            # Utility calls (routing/classification/scope) have no business
            # holding real tool access — the "-p" agentic CLI can otherwise
            # act on the goal text it's asked to merely classify (BACKLOG
            # #16: a routing prompt executed the goal and produced a "##
            # Done" report instead of a lane verdict). "" disables the
            # built-in tool set entirely, stronger than denying two names.
            cmd += ["--tools", ""]
        else:
            cmd += ["--disallowedTools", "WebFetch,WebSearch"]
        if model_str not in (MODEL_CHEAP, MODEL_MID, MODEL_POWER, "cheap", "mid", "power"):
            # Only add --model if it's a real model name, not our constants
            cmd += ["--model", model_str]
        elif model_str in ("sonnet", "opus", "haiku"):
            cmd += ["--model", model_str]

        _timeout = timeout or self.timeout
        # cwd: bind the agent's working dir to the caller's workspace so relative
        # file writes land in-workspace instead of the inherited parent cwd.
        # Explicit cwd= wins; otherwise fall back to the run-scoped ambient
        # default (set by run_agent_loop) so non-executor agentic paths
        # (verify/quality_gate/pre_flight/refinement) don't leak to launch cwd.
        _cwd = kwargs.get("cwd") or get_default_subprocess_cwd()
        # Containerized executor (C2, docs/CONTAINER_EXECUTOR_DESIGN.md): decide
        # once whether this call runs in a container. Utility (no_tools) calls
        # always stay on the host. require-mode without docker raises
        # ContainerUnavailable (refuse loudly) — it propagates by design.
        _container_name = None
        try:
            from container_exec import resolve_container_run
        except ImportError:
            resolve_container_run = None
        if resolve_container_run is not None:
            _container_name = resolve_container_run(no_tools, executor)

        # Opt-in per-boundary executor session. The mutable state belongs to
        # the loop/checkpoint, not this adapter instance (per-step model routing
        # can construct a fresh adapter). Utility and container calls stay
        # stateless. Compatibility includes every configuration dimension that
        # changes what the resumed Claude Code session is allowed to do.
        _session_state = kwargs.get("session_state")
        _delta_prompt = kwargs.get("session_delta_prompt")
        _session_active = (
            isinstance(_session_state, dict)
            and isinstance(_delta_prompt, str)
            and bool(_delta_prompt)
            and executor
            and _container_name is None
        )
        _session_signature = ""
        _resume_id = ""
        _new_session_id = ""
        _session_turns = 0
        if _session_active:
            try:
                _tool_contract = [
                    {"name": t.name, "description": t.description,
                     "parameters": t.parameters}
                    for t in (tools or [])
                ]
                _sig_payload = {
                    "model": model_str,
                    "cwd": str(Path(_cwd).resolve()) if _cwd else "",
                    "no_tools": bool(no_tools),
                    "executor": bool(executor),
                    # Stable caller-owned goal/persona/ancestry identity. The
                    # delta prompt intentionally omits these after call one,
                    # so a changed execution charter must rotate the session.
                    "context": str(kwargs.get("session_context_key") or ""),
                    "permission_contract": {
                        "skip_permissions": "--dangerously-skip-permissions" in cmd,
                        "disallowed_tools": (
                            cmd[cmd.index("--disallowedTools") + 1]
                            if "--disallowedTools" in cmd else ""
                        ),
                        "tools_disabled": "--tools" in cmd,
                    },
                    "tools": _tool_contract,
                }
                _session_signature = hashlib.sha256(
                    json.dumps(_sig_payload, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
                ).hexdigest()
            except Exception:
                _session_active = False
            if _session_active:
                if _session_state.get("signature") != _session_signature:
                    _session_state.clear()
                _resume_id = str(_session_state.get("session_id") or "")
                try:
                    _session_turns = max(0, int(_session_state.get("turns", 0)))
                except (TypeError, ValueError):
                    _session_turns = 0
                _session_state["signature"] = _session_signature

        # Enforce max_tokens on utility (no_tools) calls via the CLI's
        # CLAUDE_CODE_MAX_OUTPUT_TOKENS env var — the -p flag set has no
        # per-call token cap, so the signature's max_tokens was silently
        # ignored (cobalt-pine 2026-07-16: a 256-cap rewrite call returned
        # 2489 tokens of prose and mangled the goal). Overrun becomes a hard
        # CLI error, not truncation — the right direction for JSON-only
        # contract calls, whose callers all fall back safely. Agentic calls
        # are deliberately uncapped: their multi-turn output legitimately
        # exceeds any utility-sized cap and an error would kill real work.
        _env_extra = None
        if no_tools and max_tokens:
            _env_extra = {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(int(max_tokens))}

        prompt = _delta_prompt if _resume_id else full_prompt
        if _resume_id:
            cmd += ["--resume", _resume_id]
        try:
            # stream_probe: in-flight runaway cost kill (no-op unless a cost
            # meter is armed). BudgetRunawayError from the probe propagates
            # past the TimeoutExpired conversion below by design.
            result = _run_subprocess_safe(
                cmd, input=prompt, timeout=_timeout, cwd=_cwd,
                stream_probe=_build_stream_cost_probe(model_str),
                container_name=_container_name, env_extra=_env_extra)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"claude binary not found at {self.claude_bin}")

        # The CLI proves a missing/evicted session before executing the supplied
        # prompt (live probe: rc=1, "No conversation found..."). Only that
        # explicit no-execution shape is safe to retry automatically; ambiguous
        # failures propagate so an externally-effectful step is never doubled.
        if (_resume_id and result.returncode != 0
                and _extract_success_result(result.stdout) is None
                and _is_plain_missing_session_error(result.stdout)):
            _session_state.clear()
            _session_state["signature"] = _session_signature
            _session_turns = 0
            fresh_cmd = list(cmd)
            resume_at = fresh_cmd.index("--resume")
            del fresh_cmd[resume_at:resume_at + 2]
            prompt = full_prompt
            try:
                result = _run_subprocess_safe(
                    fresh_cmd, input=prompt, timeout=_timeout, cwd=_cwd,
                    env_extra=_env_extra,
                    stream_probe=_build_stream_cost_probe(model_str),
                    container_name=_container_name)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"claude subprocess timed out after {_timeout}s")
            except FileNotFoundError:
                raise RuntimeError(f"claude binary not found at {self.claude_bin}")
            cmd = fresh_cmd

        # Payload-first: a non-zero exit with a complete success result on
        # stdout is a successful call (see _extract_success_result). This was
        # the long-standing "claude subprocess failed (rc=1)" blocker.
        _rc_payload = None
        if result.returncode != 0:
            _rc_payload = _extract_success_result(result.stdout)
            if _rc_payload is not None:
                log.warning(
                    "claude exited rc=%d but stdout holds a success result; accepting payload",
                    result.returncode,
                )

        if result.returncode != 0 and _rc_payload is None:
            # stdout holds the merged stdout+stderr stream from the subprocess.
            merged = result.stdout.strip()
            detail = merged[:300] or "(no output)"

            # Rate limit detection: prefer the structured rate_limit_event
            # status from stream-json. The old bare "resets" substring match is
            # gone — every stream-json response embeds "resetsAt", so it now
            # false-positives on ordinary errors. Keep the phrase checks as a
            # backup for any plain-text error surface.
            _combined = merged.lower()
            if (_parse_stream_json(result.stdout)["rate_limited"]
                    or "hit your limit" in _combined or "rate limit" in _combined):
                import time as _time
                # Multi-cycle polling: retry up to _RATE_LIMIT_MAX_RETRIES times.
                # Each cycle waits exponentially longer (60→120→240→480→900→1800s, capped).
                _RATE_LIMIT_MAX_RETRIES = getattr(
                    self,
                    "_rate_limit_max_retries",
                    _env_int("MARO_CLAUDE_RATE_LIMIT_MAX_RETRIES", 6),
                )
                _RATE_LIMIT_CYCLE_CAP = 1800  # 30 minutes max per wait
                # Total-backoff wall-clock cap: the per-cycle cap alone let the
                # default 6 retries sum to 60+120+240+480+960+1800 = 61 min of
                # pure sleeping (scope-ab run-06, 2026-04-23). When the next
                # sleep would push cumulative backoff past this ceiling, stop
                # retrying and soft-fail with a "rate-limited, retry later" error
                # rather than committing to another 30-minute sleep.
                _RATE_LIMIT_TOTAL_CAP = _env_int("MARO_CLAUDE_RATE_LIMIT_TOTAL_CAP", 600)
                _total_slept = 0
                _wait = getattr(self, "_rate_limit_wait", 60)
                _retry_success = False
                _capped_out = False
                for _attempt in range(_RATE_LIMIT_MAX_RETRIES):
                    if _RATE_LIMIT_TOTAL_CAP > 0 and _total_slept + _wait > _RATE_LIMIT_TOTAL_CAP:
                        log.warning(
                            "rate limit: total backoff cap reached (%ds slept, next wait %ds "
                            "would exceed %ds cap) — bailing cleanly after %d attempt(s)",
                            _total_slept, _wait, _RATE_LIMIT_TOTAL_CAP, _attempt,
                        )
                        _capped_out = True
                        break
                    log.warning(
                        "rate limit detected (attempt %d/%d), waiting %ds before retry",
                        _attempt + 1, _RATE_LIMIT_MAX_RETRIES, _wait,
                    )
                    _time.sleep(_wait)
                    _total_slept += _wait
                    _wait = min(_wait * 2, _RATE_LIMIT_CYCLE_CAP)
                    # A retry is a NEW container run: resolve a FRESH name so a
                    # not-yet-removed --rm container from the prior attempt can't
                    # cause a docker --name conflict (adversarial-review
                    # 2026-07-12). None when not containerizing.
                    if resolve_container_run is not None and _container_name is not None:
                        _container_name = resolve_container_run(no_tools, executor)
                    try:
                        result = _run_subprocess_safe(
                            cmd, input=prompt, timeout=_timeout, cwd=_cwd,
                            stream_probe=_build_stream_cost_probe(model_str),
                            container_name=_container_name)
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
                        if _capped_out:
                            raise RuntimeError(
                                f"claude rate-limited; bailed after {_total_slept}s of backoff "
                                f"(total cap {_RATE_LIMIT_TOTAL_CAP}s) — retry later: "
                                f"{result.stdout[:200]}"
                            )
                        raise RuntimeError(
                            f"claude rate-limited after {_RATE_LIMIT_MAX_RETRIES} retries: "
                            f"{result.stdout[:200]}"
                        )

            # Re-check after retries: a retry can also exit non-zero with a
            # usable success payload.
            _rc_payload = _extract_success_result(result.stdout)
            if result.returncode != 0 and _rc_payload is None:
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
                # The CLI reports errors as a result object with is_error=true
                # and the human-readable message in "result" (e.g. "Not logged
                # in · Please run /login"). Surface that instead of raw JSON.
                _err_obj = _extract_result_object(result.stdout)
                if _err_obj is not None and _err_obj.get("result"):
                    detail = str(_err_obj["result"])[:300]
                else:
                    detail = result.stdout.strip()[:300] or "(no output)"
                raise RuntimeError(f"claude subprocess failed (rc={result.returncode}): {detail}")

        # Translate the fully-captured stream-json output into the canonical
        # chunk/tool_call/done vocabulary and fold it into an LLMResponse
        # through the one shared driver every adapter's stream goes through
        # (BACKLOG #14 — see StreamEvent / LLMAdapter._collect above).
        if _session_active:
            _session_result = _rc_payload or _parse_stream_json(result.stdout)["result"]
            _new_session_id = (
                str(_session_result.get("session_id") or "")
                if isinstance(_session_result, dict) else ""
            )
            if _new_session_id:
                _session_state["session_id"] = _new_session_id
                _session_state["signature"] = _session_signature
                _session_state["turns"] = _session_turns + 1
            else:
                _session_state.clear()
        response = self._collect(
            self._stream_events(result.stdout, tools=tools, rc_payload=_rc_payload))
        if _session_active:
            response.session_id = _new_session_id
            response.session_resumed = bool(_resume_id and cmd.count("--resume"))
            log.info(
                "executor session call: %s session=%s",
                "resumed" if response.session_resumed else "fresh",
                _new_session_id[:8] if _new_session_id else "none",
            )
        return response

    def _stream_events(
        self, raw_output: str, *, tools: Optional[List[LLMTool]], rc_payload: Optional[dict] = None
    ) -> Iterator[StreamEvent]:
        """Translate captured stream-json output into StreamEvents.

        `_parse_stream_json` remains the single NDJSON parser (also used
        upstream in `complete()` for the early rate-limit check); this is
        strictly downstream of it — it re-expresses that same parsed data
        as the ordered event sequence `LLMAdapter._collect` folds into an
        LLMResponse, rather than a one-off block assembling the response
        inline. `rc_payload` is the already-extracted success payload when
        `complete()` accepted a non-zero exit on the strength of its stdout
        (payload-first rc handling); it takes precedence over the parsed
        stream's own result, matching the pre-port behavior exactly.
        """
        text = (raw_output or "").strip()
        stream = _parse_stream_json(text)
        tool_events = stream["tool_events"]
        data = rc_payload or stream["result"]

        if data is None:
            # Fallback: no parseable result event — treat the whole capture
            # as plain text content.
            yield StreamEvent(kind="chunk", text=text)
            yield StreamEvent(kind="done", response=LLMResponse(
                content=text, tool_events=tool_events, backend=self.backend,
            ))
            return

        raw_result = data.get("result", "")
        usage = data.get("usage", {}) or {}
        cache_read = usage.get("cache_read_input_tokens", 0)
        input_tokens = usage.get("input_tokens", 0) + cache_read
        output_tokens = usage.get("output_tokens", 0)

        content = raw_result
        tool_calls: List[ToolCall] = []
        if tools and raw_result:
            tc = self._parse_tool_call(raw_result, tools)
            if tc:
                tool_calls = [tc]
                content = ""
                yield StreamEvent(kind="tool_call", tool_call=tc)
        if content:
            yield StreamEvent(kind="chunk", text=content)

        model = (
            list(data.get("modelUsage", {}).keys() or ["claude"])[0]
            if data.get("modelUsage") else "claude"
        )
        yield StreamEvent(kind="done", response=LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=data.get("stop_reason", "end_turn"),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cost_usd=safe_float(data.get("total_cost_usd")),
            tool_events=tool_events,
            backend=self.backend,
        ))

# ---------------------------------------------------------------------------
# CodexCLIAdapter — uses `codex exec --json` (ChatGPT OAuth, prompt caching)
# ---------------------------------------------------------------------------

def _find_codex_bin() -> str:
    """Resolve the codex binary path. CODEX_BIN env, then PATH, then common locations."""
    import shutil
    if env := os.environ.get("CODEX_BIN"):
        return env
    if found := shutil.which("codex"):
        return found
    for candidate in (
        Path.home() / ".local" / "bin" / "codex",
        Path("/usr/local/bin/codex"),
        Path("/opt/homebrew/bin/codex"),
        Path("/home/linuxbrew/.linuxbrew/bin/codex"),
    ):
        if candidate.is_file():
            return str(candidate)
    return "codex"  # let exec-time PATH lookup have the last word


_CODEX_BIN = _find_codex_bin()
_CODEX_AUTH_FILE = str(Path.home() / ".codex" / "auth.json")


def _codex_auth_available() -> bool:
    """Check if codex binary exists and auth file is present."""
    bin_path = os.environ.get("CODEX_BIN", _CODEX_BIN)
    if not (os.path.isfile(bin_path) and os.access(bin_path, os.X_OK)):
        return False
    auth_path = os.environ.get("CODEX_AUTH_FILE", _CODEX_AUTH_FILE)
    return os.path.isfile(auth_path)


class CodexCLIAdapter(_JSONToolPromptMixin, LLMAdapter):
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
        no_tools: bool = False,
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
        ]
        if no_tools:
            # Same rationale as ClaudeSubprocessAdapter.no_tools (BACKLOG
            # #16): a utility call (routing/classification) has no business
            # writing files or running shell commands. codex has no blanket
            # tool-disable flag; read-only sandbox is the closest available
            # constraint — it still lets the model answer, but strips the
            # ability to act on the goal text it was only asked to classify.
            cmd += ["-s", "read-only"]
        cmd += ["-"]  # read prompt from stdin

        # Explicit cwd= wins; else fall back to the run-scoped ambient default
        # (parity with ClaudeSubprocessAdapter) so codex-backed non-executor
        # agentic calls also bind in-workspace instead of the launch cwd.
        _cwd = kwargs.get("cwd") or get_default_subprocess_cwd()
        try:
            result = _run_subprocess_safe(cmd, input=prompt, timeout=_timeout, cwd=_cwd)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"codex subprocess timed out after {_timeout}s")
        except FileNotFoundError:
            raise RuntimeError(f"codex binary not found at {self.codex_bin}")

        if result.returncode != 0:
            # stdout holds merged stdout+stderr.
            merged = result.stdout.strip()
            detail = merged[:300] or "(no output)"
            raise RuntimeError(f"codex subprocess failed (rc={result.returncode}): {detail}")

        # Same streaming-iterator shape as ClaudeSubprocessAdapter (BACKLOG
        # #14): translate the captured JSONL into chunk/tool_call/done events
        # and fold through the one shared driver.
        return self._collect(self._stream_events(result.stdout, tools))

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

    def _stream_events(self, stdout: str, tools: Optional[List[LLMTool]]) -> Iterator[StreamEvent]:
        """Translate `codex exec --json` JSONL output into StreamEvents.

        Second adapter proving the streaming-iterator shape generalizes
        (BACKLOG #14) to a structurally different transcript than Claude's
        stream-json: codex's `item.completed`/agent_message carries the
        FULL text of that item (each one overwrites, not deltas — the
        pre-port code took whatever the LAST agent_message said, same as
        here), and usage only arrives at the end via `turn.completed`. The
        `chunk` events below are informational/order-preserving for a
        consumer watching the stream live; the terminal `done` event still
        carries the fully-assembled, authoritative LLMResponse, so
        `LLMAdapter._collect`'s generic concatenation-of-chunks fallback is
        never actually exercised here (matches ClaudeSubprocessAdapter's
        port — see its `_stream_events`).
        """
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
                    yield StreamEvent(kind="chunk", text=content)

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
                yield StreamEvent(kind="tool_call", tool_call=tc)

        yield StreamEvent(kind="done", response=LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason="end_turn",
            model=resolve_model("codex", self.model_key),
            input_tokens=input_tokens + cached_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cached_tokens,
            backend=self.backend,
        ))


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
        _thinking = kwargs.get("thinking_budget", 0)
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

        # Anthropic reports input_tokens as fresh-only; cache reads/writes are
        # separate, non-overlapping counters. Fold them into the total-volume
        # contract so input_tokens means the same thing across all adapters.
        _cache_read = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        _cache_creation = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=resp.stop_reason or "end_turn",
            model=resp.model,
            input_tokens=resp.usage.input_tokens + _cache_creation + _cache_read,
            output_tokens=resp.usage.output_tokens,
            cache_read_tokens=_cache_read,
            backend=self.backend,
        )


# ---------------------------------------------------------------------------
# OpenRouterAdapter — HTTP to openrouter.ai (OpenAI-compatible)
# ---------------------------------------------------------------------------


class OpenAICompatAdapter(LLMAdapter):
    """Base for HTTP adapters targeting an OpenAI chat-completions-compatible
    endpoint. No SDK dependency — just requests. Subclasses set `backend`,
    `_resolve_backend_key` (the key passed to `resolve_model`), and may
    override `_extra_headers()` for endpoint-specific auth/routing headers.

    `max_retries`: forwarded to `_retry_complete` (None = its own default of
    3, same as before this parameter existed). Hosted-free validation-ladder
    subclasses (GroqAdapter, GeminiAdapter — see hosted_free.py) pass 0: a
    tight free-tier RPM budget makes a multi-attempt exponential backoff
    (up to 65s) the wrong response to a 429 — a rate-limit-aware breaker one
    layer up (hosted_free._HostedFreeLadder) should decide to skip the
    provider instead, which needs the 429 to surface immediately, not after
    this class's own retry ladder burns through it first.
    """

    backend = "openai"
    _resolve_backend_key = "openai"

    def __init__(self, api_key: str, model: str = MODEL_CHEAP,
                 base_url: str = "https://api.openai.com/v1",
                 max_retries: Optional[int] = None):
        self._api_key = api_key
        self.model_key = model
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries

    def _extra_headers(self) -> Dict[str, str]:
        return {}

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

        model_str = resolve_model(self._resolve_backend_key, self.model_key)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        headers.update(self._extra_headers())
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

        request_timeout = float(kwargs.get("request_timeout_s", 120))

        def _do_request():
            r = requests.post(f"{self._base_url}/chat/completions", headers=headers, json=payload, timeout=request_timeout)
            r.raise_for_status()
            return r
        _retry_kwargs = {} if self._max_retries is None else {"max_retries": self._max_retries}
        resp = _retry_complete(_do_request, **_retry_kwargs)
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


class OpenRouterAdapter(OpenAICompatAdapter):
    """HTTP adapter for OpenRouter."""

    backend = "openrouter"
    _resolve_backend_key = "openrouter"

    def __init__(self, api_key: str, model: str = MODEL_CHEAP, site_name: str = "maro-orch"):
        super().__init__(api_key, model, base_url="https://openrouter.ai/api/v1")
        self._site_name = site_name

    def _extra_headers(self) -> Dict[str, str]:
        return {"X-Title": self._site_name}


# ---------------------------------------------------------------------------
# OpenAIAdapter — direct OpenAI or compatible endpoint
# ---------------------------------------------------------------------------

class OpenAIAdapter(OpenAICompatAdapter):
    """Adapter for OpenAI API (or any OpenAI-compatible endpoint)."""

    backend = "openai"
    _resolve_backend_key = "openai"


# ---------------------------------------------------------------------------
# GroqAdapter / GeminiAdapter — hosted-free validation-ladder tier (BACKLOG #25)
# ---------------------------------------------------------------------------
# Zero-cost rung for non-agentic call classes (validation ladder, classify/
# routing, cheap verification) — see hosted_free.py for the config, model-ID
# resolution, and per-provider rate-limit/latency breakers that wire these
# into step_exec.verify_step alongside local_models' local-model tier.
#
# Deliberately NOT in DEFAULT_BACKEND_ORDER / _KNOWN_BACKENDS / build_adapter:
# these are free-tier helpers with tight RPM caps (Groq: 30 RPM / ~14.4K-1K
# req/day depending on model; Gemini free: ~10 RPM), not general-purpose
# execution backends — they must never be silently picked up by the paid
# failover chain. `max_retries=0` (the class default here, not the base
# class's) means a 429 surfaces immediately instead of burning
# `_retry_complete`'s exponential backoff ladder — hosted_free.py's breaker
# is what decides whether/when to try this provider again.
#
# Model IDs are NOT hardcoded in `_MODEL_MAP` on purpose (BACKLOG #25: "Model
# churn is real — keep model IDs in config, not code", e.g. Kimi K2 removed
# Mar 2026). `resolve_model()` passes an unrecognized backend/model pair
# through unchanged (see its docstring), so hosted_free.py resolves the
# actual model string from config and hands it straight to `model=` here.

class GroqAdapter(OpenAICompatAdapter):
    """HTTP adapter for Groq's OpenAI-compatible endpoint (free tier).

    `GROQ_API_KEY` (env or credentials .env, same discovery contract as the
    other backends) — absent key means callers simply never construct this
    (see hosted_free.configured_providers()); the adapter itself has no
    inert/no-op mode of its own, matching how AnthropicSDKAdapter/
    OpenRouterAdapter/OpenAIAdapter behave without a key.
    """

    backend = "groq"
    _resolve_backend_key = "groq"

    def __init__(self, api_key: str, model: str, max_retries: Optional[int] = 0):
        super().__init__(api_key, model, base_url="https://api.groq.com/openai/v1",
                          max_retries=max_retries)


class GeminiAdapter(OpenAICompatAdapter):
    """HTTP adapter for Gemini's official OpenAI-compatible endpoint (free tier).

    `GEMINI_API_KEY` (env or credentials .env). Same no-key-means-don't-build
    contract as GroqAdapter.
    """

    backend = "gemini"
    _resolve_backend_key = "gemini"

    def __init__(self, api_key: str, model: str, max_retries: Optional[int] = 0):
        super().__init__(api_key, model,
                          base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                          max_retries=max_retries)


# ---------------------------------------------------------------------------
# Credential discovery
# ---------------------------------------------------------------------------

def _load_env_file() -> Dict[str, str]:
    """Load key=value pairs from the credentials env file."""
    try:
        from config import load_credentials_env
        return load_credentials_env()
    except Exception:
        pass
    result: Dict[str, str] = {}
    env_path = str(Path.home() / ".maro" / "workspace" / "secrets" / ".env")
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

# Default auto-detect order. Configurable via ~/.maro/config.yml:
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


def detect_backends() -> List[Tuple[str, bool, str]]:
    """Availability of each backend in the configured order, without building adapters.

    Single source of truth shared with build_adapter's auto-detect walk — uses the
    same predicates (_get_key over env + env file, _claude_bin_available,
    _codex_auth_available) so diagnostics (maro-doctor) can never disagree with
    what a run would actually do. Returns [(name, usable, detail), ...].
    """
    env = _load_env_file()
    out: List[Tuple[str, bool, str]] = []
    for name in _get_backend_order():
        if name in ("anthropic", "openrouter", "openai"):
            env_var = {"anthropic": "ANTHROPIC_API_KEY",
                       "openrouter": "OPENROUTER_API_KEY",
                       "openai": "OPENAI_API_KEY"}[name]
            usable = bool(_get_key(env_var, env))
            detail = f"{env_var} {'set' if usable else 'not set (env or credentials .env)'}"
        elif name == "subprocess":
            usable = _claude_bin_available()
            bin_path = os.environ.get("CLAUDE_BIN", _CLAUDE_BIN)
            detail = f"claude binary {'ok' if usable else 'missing/not executable'} at {bin_path}"
        elif name == "codex":
            usable = _codex_auth_available()
            detail = "codex CLI + ~/.codex/auth.json" + ("" if usable else " missing")
        else:  # unreachable while _get_backend_order filters to _KNOWN_BACKENDS
            usable, detail = False, "unknown backend"
        out.append((name, usable, detail))
    return out


def probe_backends() -> List[Tuple[str, bool, str]]:
    """LIVE liveness probe: one tiny completion per usable backend.

    Where detect_backends() checks presence (key set, binary on PATH), this
    catches the "installed but not logged in" / "key set but account dry"
    class by actually calling each backend and classifying the failure.
    Spends a few real tokens per backend — opt-in only (maro-doctor --live),
    never called from automated paths (no silent spend).
    """
    out: List[Tuple[str, bool, str]] = []
    for name, usable, detail in detect_backends():
        if not usable:
            out.append((name, False, f"skipped: {detail}"))
            continue
        try:
            adapter = build_adapter(backend=name)
            resp = adapter.complete(
                [LLMMessage("user", "Reply with the single word: ok")],
                max_tokens=8,
            )
            ok = bool((resp.content or "").strip())
            out.append((name, ok, "live" if ok else "empty response"))
        except Exception as exc:
            try:
                from llm_errors import classify_error
                info = classify_error(exc, backend=name)
                msg = info.error_class + (f": {info.user_action}"
                                          if info.user_action else "")
            except Exception:
                msg = str(exc)[:120]
            out.append((name, False, msg))
    return out


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

    # MARO_BACKEND env var overrides auto-detection priority without forcing a specific key
    _maro_backend = os.environ.get("MARO_BACKEND", "").strip().lower()
    if _maro_backend and _maro_backend != "auto":
        return build_adapter(backend=_maro_backend, model=model, api_key=api_key, timeout=timeout)

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
        _adv_kwargs: Dict[str, Any] = {
            "max_tokens": 1024, "temperature": 0.2, "no_tools": True,
            "purpose": "strategic advisor",
        }
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
