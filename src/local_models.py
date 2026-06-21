"""Local, zero-cost model runtime for step/quality validation.

Poe's most frequent LLM call is *validation* — "did this step result actually
satisfy the goal?" Those calls are high-volume and mostly easy, so paying a
frontier API for each one is the single biggest avoidable token sink. This
module lets a local model (running on the same box) act as the first-pass
validator for free, escalating to a paid model only when the local judge is
*uncertain*.

Design (intentionally small):

  * One HTTP adapter, `LocalValidatorAdapter`, speaks the OpenAI-compatible
    `/v1/chat/completions` schema. Both supported runtimes expose it:
      - **mlx**    — `mlx_lm.server` on Apple Silicon (default here)
      - **ollama** — `ollama serve` `/v1` endpoint (works on the Linux box)
    So one code path serves both; `validate.runtime` only picks the endpoint.

  * A **0..n list** of local models (`validate.local_models`, priority order)
    is wrapped in the existing `FailoverAdapter`. Empty list → this module is
    inert and validation behaves exactly as before (fully backward-compatible).

  * **Detect-and-use-if-present.** If the endpoint isn't reachable or no
    configured model is loaded, `build_local_validator_adapter()` returns
    None and callers fall back to the paid path. Installing the runtime is
    optional (see `scripts/local-validator.sh`); nothing breaks without it.

Pure stdlib (`urllib`) on purpose: the framework interpreter needs no MLX/torch
deps — the model runs in a separate process. See VISION.md §9 (cost philosophy)
and the validation ladder in quality_gate.py / verification_agent.py.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import urllib.error
import urllib.request
from typing import List, Optional

from llm import LLMAdapter, LLMMessage, LLMResponse

log = logging.getLogger("poe.local_models")

# Default endpoints for each runtime (OpenAI-compatible base URLs).
_DEFAULT_ENDPOINTS = {
    "mlx": "http://127.0.0.1:8088/v1",
    "ollama": "http://127.0.0.1:11434/v1",
}
_REACH_TIMEOUT = 2.0   # seconds — endpoint liveness probe must be cheap
_GEN_TIMEOUT = 60.0    # seconds — a single validation generation


# ---------------------------------------------------------------------------
# Config accessors (all under the `validate.*` namespace)
# ---------------------------------------------------------------------------

def _cfg(key: str, default):
    """Read `validate.<key>`; tolerate config.py being unavailable in tests."""
    try:
        from config import get
        return get(f"validate.{key}", default)
    except Exception:
        return default


def resolve_runtime() -> str:
    """Resolve the local runtime: explicit config, else auto by platform.

    auto → 'mlx' on Apple Silicon (Darwin/arm64), 'ollama' everywhere else.
    """
    runtime = str(_cfg("runtime", "auto")).strip().lower()
    if runtime in ("mlx", "ollama"):
        return runtime
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        return "mlx"
    return "ollama"


def resolve_endpoint(runtime: Optional[str] = None) -> str:
    """OpenAI-compatible base URL for the active runtime.

    Priority: `validate.endpoint` override → `LOCAL_VALIDATOR_ENDPOINT` env →
    runtime default.
    """
    override = str(_cfg("endpoint", "") or "").strip()
    if override:
        return override.rstrip("/")
    env = os.environ.get("LOCAL_VALIDATOR_ENDPOINT", "").strip()
    if env:
        return env.rstrip("/")
    runtime = runtime or resolve_runtime()
    return _DEFAULT_ENDPOINTS.get(runtime, _DEFAULT_ENDPOINTS["ollama"]).rstrip("/")


def configured_models() -> List[str]:
    """The 0..n local validator models, in priority (failover) order."""
    raw = _cfg("local_models", []) or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(m).strip() for m in raw if str(m).strip()]


def min_certainty() -> float:
    """Confidence below which a local verdict is UNDECIDED → escalate to paid."""
    try:
        return max(0.0, min(1.0, float(_cfg("min_certainty", 0.6))))
    except (TypeError, ValueError):
        return 0.6


def input_char_budget() -> int:
    """How much of a step result the *local* (free) validator sees. Larger than
    the paid default (1200) since local validation costs nothing — judging a
    fuller view beats judging the first 1200 chars. Bounded; for very large
    artifacts an agentic verifier that reads selectively is the better tool
    (see the deep-eval task in BACKLOG.md)."""
    try:
        return max(1200, int(_cfg("max_input_chars", 6000)))
    except (TypeError, ValueError):
        return 6000


def escalation_target() -> str:
    """Where an UNDECIDED local verdict escalates: 'cheap' (one paid gate) or
    'council' (the 3-persona trio in quality_gate.run_llm_council)."""
    target = str(_cfg("escalation", "cheap")).strip().lower()
    return target if target in ("cheap", "council") else "cheap"


# ---------------------------------------------------------------------------
# Endpoint detection
# ---------------------------------------------------------------------------

def _http_json(method: str, url: str, payload: Optional[dict], timeout: float) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def loaded_models(endpoint: Optional[str] = None) -> List[str]:
    """Model ids the endpoint reports as available, or [] if unreachable."""
    endpoint = endpoint or resolve_endpoint()
    try:
        data = _http_json("GET", f"{endpoint}/models", None, _REACH_TIMEOUT)
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception as exc:
        log.debug("local validator endpoint unreachable at %s: %s", endpoint, exc)
        return []


def endpoint_available(endpoint: Optional[str] = None) -> bool:
    return bool(loaded_models(endpoint))


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class LocalValidatorAdapter(LLMAdapter):
    """OpenAI-compatible HTTP client for a local model (mlx_lm.server / ollama).

    Non-fatal by contract: connection/5xx errors raise so a wrapping
    `FailoverAdapter` falls through to the next model or the paid fallback.
    """

    def __init__(self, model: str, endpoint: Optional[str] = None,
                 runtime: Optional[str] = None, timeout: float = _GEN_TIMEOUT,
                 min_tokens: Optional[int] = None):
        self.model_key = model
        self._model = model
        self._runtime = runtime or resolve_runtime()
        self.backend = self._runtime  # "mlx" | "ollama"
        self._endpoint = (endpoint or resolve_endpoint(self._runtime)).rstrip("/")
        self._timeout = timeout
        # Token floor: local *reasoning* models (e.g. VibeThinker) emit a long
        # <think> trace before the answer. The paid validation caller passes a
        # tiny budget (128) that's fine for non-reasoners but starves a reasoner
        # mid-thought, leaving `content` empty. Floor the budget so it finishes.
        # Default floor 2048: live runs showed a reasoning model's <think> trace
        # on a *real* (long) step result overruns 1024, truncating before the JSON
        # verdict → empty content → spurious escalation. Tune per model in deep-eval.
        self._min_tokens = int(_cfg("local_max_tokens", 2048) if min_tokens is None else min_tokens)

    def complete(self, messages: List[LLMMessage], *, tools=None,
                 tool_choice: str = "auto", max_tokens: int = 256,
                 temperature: float = 0.1, thinking_budget=None,
                 **kwargs) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max(int(max_tokens), self._min_tokens),
            "temperature": temperature,
            "stream": False,
        }
        try:
            data = _http_json("POST", f"{self._endpoint}/chat/completions",
                              payload, self._timeout)
        except urllib.error.URLError as exc:
            # Surfaces as a failover-eligible error (connection/unavailable).
            raise RuntimeError(
                f"local validator unavailable ({self._runtime} @ {self._endpoint}): {exc}"
            ) from exc
        choices = data.get("choices") or []
        content = ""
        if choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content", "") or ""
            # Reasoning runtimes split CoT into a separate field and leave the
            # answer in `content`. If the budget truncated mid-thought, `content`
            # is empty — fall back to the reasoning trace so a trailing JSON
            # verdict (if any) is still recoverable by extract_json downstream.
            if not content.strip():
                content = msg.get("reasoning", "") or msg.get("reasoning_content", "") or ""
        usage = data.get("usage") or {}
        return LLMResponse(
            content=content,
            stop_reason=(choices[0].get("finish_reason") if choices else "stop") or "stop",
            model=self._model,
            input_tokens=int(usage.get("prompt_tokens", 0) or 0),
            output_tokens=int(usage.get("completion_tokens", 0) or 0),
            backend=self._runtime,
        )


# ---------------------------------------------------------------------------
# Builder — the public entry point
# ---------------------------------------------------------------------------

# Session cache so we don't re-probe the endpoint on every step. Keyed by the
# (runtime, endpoint, tuple(models)) signature; reset via reset_cache() in tests.
_CACHE: dict = {}


def reset_cache() -> None:
    _CACHE.clear()


def validator_available() -> bool:
    """True if a local validator is configured AND at least one configured model
    is currently loaded at the endpoint. Cached for the session (the endpoint is
    not re-probed on every call)."""
    models = configured_models()
    if not models:
        return False
    key = ("_avail", resolve_runtime(), resolve_endpoint(), tuple(models))
    if key not in _CACHE:
        _CACHE[key] = bool(set(models) & set(loaded_models()))
    return _CACHE[key]


def auto_verify_enabled() -> bool:
    """Whether to default the ralph verify loop ON because a usable local
    validator exists (verification is then free). Opt out with
    `validate.auto_verify: false`. Returns False when no local validator is
    actually available, so we never silently switch verification to the paid
    path just because models were listed in config."""
    val = _cfg("auto_verify", True)
    if isinstance(val, str):
        if val.strip().lower() in ("false", "0", "no", "off"):
            return False
    elif not val:
        return False
    return validator_available()


def build_local_validator_adapter(fallback: Optional[LLMAdapter] = None
                                  ) -> Optional[LLMAdapter]:
    """Build the local-first validator adapter, or None to use the paid path.

    Returns:
      * None — no local models configured, or none are loaded at the endpoint.
        Callers should validate with their existing (paid) adapter, unchanged.
      * A `FailoverAdapter` over the configured local models that are actually
        loaded, with `fallback` appended last (if given) for graceful
        degradation when every local model errors mid-run.

    The result is cached for the session keyed by config + loaded-model set.
    """
    models = configured_models()
    if not models:
        return None

    runtime = resolve_runtime()
    endpoint = resolve_endpoint(runtime)
    available = set(loaded_models(endpoint))
    usable = [m for m in models if m in available]

    sig = (runtime, endpoint, tuple(usable), id(fallback))
    if sig in _CACHE:
        return _CACHE[sig]

    if not usable:
        log.info("local validator: configured %s but none loaded at %s (%s) — using paid path",
                 models, endpoint, runtime)
        _CACHE[sig] = None
        return None

    from llm import FailoverAdapter
    adapters: List[LLMAdapter] = [
        LocalValidatorAdapter(m, endpoint=endpoint, runtime=runtime) for m in usable
    ]
    if fallback is not None:
        adapters.append(fallback)
    result = adapters[0] if len(adapters) == 1 else FailoverAdapter(adapters)
    log.info("local validator active: %s via %s (%s)%s",
             usable, endpoint, runtime, " + paid fallback" if fallback else "")
    _CACHE[sig] = result
    return result
