"""Hosted-free small-LLM tier for the validation ladder (BACKLOG #25).

Groq and Gemini both offer a genuinely free API tier (no card for Groq, a
Google account for Gemini) suitable for the orchestrator's high-volume
*non-agentic* call classes — chiefly step/quality validation. This module
plugs them in as an ADDITIONAL rung of the validation ladder, alongside
(never replacing) `local_models.py`'s in-process local-model tier:

    Tier 1  local models      (local_models.py)   — free, in-process, fast
    Tier 1b hosted-free       (this module)        — free, network, rate-limited
    Tier 2  paid                                   — the existing adapter

Design mirrors `local_models.py` on purpose (same shape the codebase already
knows how to reason about):

  * **Detect-and-use-if-present.** No `GROQ_API_KEY`/`GEMINI_API_KEY` in env
    or the credentials `.env` → `configured_providers()` returns `[]` and
    `build_hosted_free_adapter()` returns `None`. The whole module is inert
    by default — callers fall back to the paid path exactly as if this file
    didn't exist.

  * **Latency breaker**, same semantics as `local_models.latency_guard_tripped`
    / `report_latency`: a provider slower than `max_latency_ms()` trips a
    per-provider, per-process breaker (with a cold-start grace pass on the
    very first call) so a bad network day doesn't tax every subsequent step.

  * **Rate-limit breaker** (the free-tier-specific addition): a 429 response
    trips a per-provider cooldown honoring the `Retry-After` header when
    present (falling back to `rate_limit_default_cooldown_s()` otherwise).
    This is deliberately NOT the generic `_retry_complete` exponential-backoff
    ladder — see `GroqAdapter`/`GeminiAdapter` in llm.py (`max_retries=0`):
    burning 5s/15s/45s retrying a call that's blocked by a strict 30 RPM cap
    just wastes wall-clock and quota. Trip the breaker, move to the next
    provider (or the paid tier) immediately, and let time (or the next
    process) heal it.

  * **Model IDs live in config, not code** (`validate.hosted_free.groq_model`
    / `gemini_model`) — provider free-tier model catalogs churn (Kimi K2 was
    pulled from Groq's OpenAI-compat catalog Mar 2026); the defaults below
    are a reasonable starting point, not a promise.

GroqAdapter / GeminiAdapter (the actual HTTP transports) live in llm.py,
mirroring OpenRouterAdapter/OpenAIAdapter — see llm.py's docstring above
them for why they're deliberately kept OUT of DEFAULT_BACKEND_ORDER /
build_adapter's auto-detect walk. This module is the only thing that
constructs them for the validation ladder.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

from llm import LLMAdapter, LLMResponse

log = logging.getLogger("maro.hosted_free")

# Providers this module knows how to build, and the env var each one's key
# lives in (same discovery contract as every other backend: env wins over
# the credentials .env file — see `_load_env`/`_get_key` below).
_ENV_KEYS: Dict[str, str] = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


# ---------------------------------------------------------------------------
# Config accessors (all under the `validate.hosted_free.*` namespace)
# ---------------------------------------------------------------------------

def _cfg(key: str, default):
    """Read `validate.hosted_free.<key>`; tolerate config.py being unavailable
    in tests (mirrors local_models._cfg)."""
    try:
        from config import get
        return get(f"validate.hosted_free.{key}", default)
    except Exception:
        return default


def hosted_free_enabled() -> bool:
    """Master switch. Even when True, an unset API key still makes the
    corresponding provider a no-op — this only lets an operator force the
    tier off without touching env vars."""
    val = _cfg("enabled", True)
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "0", "no", "off")
    return bool(val)


def provider_order() -> List[str]:
    """The hosted-free providers to try, in priority order. Unknown names are
    dropped; default order is Groq (higher free-volume tier) then Gemini."""
    raw = _cfg("order", ["groq", "gemini"]) or []
    if isinstance(raw, str):
        raw = [raw]
    out: List[str] = []
    seen: set = set()
    for item in raw:
        name = str(item).strip().lower()
        if name and name in _ENV_KEYS and name not in seen:
            out.append(name)
            seen.add(name)
    return out or ["groq", "gemini"]


def groq_model() -> str:
    """Groq free-tier model id. Default: llama-3.1-8b-instant (30 RPM /
    ~14.4K req/day, verified 2026-07-12 — see BACKLOG #25). Override via
    config when the catalog moves; this is a starting point, not a promise."""
    return str(_cfg("groq_model", "llama-3.1-8b-instant") or "llama-3.1-8b-instant").strip()


def gemini_model() -> str:
    """Gemini free-tier model id via the official OpenAI-compat endpoint.
    Default: a Flash-class model (~10 RPM free, unpublished exact cap as of
    2026-07-12 — probe, don't assume). Override via config."""
    return str(_cfg("gemini_model", "gemini-2.0-flash") or "gemini-2.0-flash").strip()


def min_certainty() -> float:
    """Confidence below which a hosted-free verdict is UNDECIDED → escalate to
    paid. Own namespace from local_models' `validate.min_certainty` so the two
    free tiers can be tuned independently; same default (0.6)."""
    try:
        return max(0.0, min(1.0, float(_cfg("min_certainty", 0.6))))
    except (TypeError, ValueError):
        return 0.6


def input_char_budget() -> int:
    """How much of a step result the hosted-free validator sees. Smaller than
    local's default (6000): these calls cross the network against a rate-
    limited free tier, so a leaner payload is the more conservative default."""
    try:
        return max(1200, int(_cfg("max_input_chars", 4000)))
    except (TypeError, ValueError):
        return 4000


def max_latency_ms() -> int:
    """Per-call latency cap for the hosted-free tier. Higher than local's
    default (15000) since this is a network round trip to a third party, not
    an in-process/loopback call. 0 disables the breaker."""
    try:
        return max(0, int(_cfg("max_latency_ms", 20000)))
    except (TypeError, ValueError):
        return 20000


def rate_limit_default_cooldown_s() -> float:
    """Cooldown applied on a 429 that carries no (or an unparseable)
    Retry-After header. Most of these free tiers window on ~1 minute."""
    try:
        return max(1.0, float(_cfg("rate_limit_default_cooldown_s", 60.0)))
    except (TypeError, ValueError):
        return 60.0


# ---------------------------------------------------------------------------
# Credential discovery (env wins over credentials .env — same contract as
# every other backend in llm.py; reimplemented locally rather than reaching
# into llm.py's private helpers, mirroring local_models.py's own style of
# only importing llm's public adapter surface).
# ---------------------------------------------------------------------------

def _load_env() -> Dict[str, str]:
    try:
        from config import load_credentials_env
        return load_credentials_env()
    except Exception:
        return {}


def _get_key(name: str, env: Dict[str, str]) -> Optional[str]:
    v = os.environ.get(name)
    if v:
        return v
    return env.get(name)


def configured_providers() -> List[str]:
    """Providers (in priority order) with an API key present. Empty when
    neither GROQ_API_KEY nor GEMINI_API_KEY is set (or hosted_free_enabled()
    is False) — the module is then a total no-op, same degrade-gracefully
    contract as local_models with zero configured local models."""
    if not hosted_free_enabled():
        return []
    env = _load_env()
    out = []
    for name in provider_order():
        key_name = _ENV_KEYS.get(name)
        if key_name and _get_key(key_name, env):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Rate-limit + latency breakers — per provider, per process
# ---------------------------------------------------------------------------
# Same self-healing shape as local_models' latency breaker (trips in-process,
# a fresh process re-probes), plus a rate-limit-specific cooldown that clears
# itself once elapsed rather than staying tripped for the rest of the
# process — an RPM cap resets on its own schedule, unlike "this box is slow".

_LATENCY_TRIPPED: Dict[str, str] = {}
_LATENCY_REPORTS: Dict[str, int] = {}
_RATE_LIMIT_UNTIL: Dict[str, float] = {}


def reset_cache() -> None:
    """Clear all per-provider breaker state. Tests call this for isolation."""
    _LATENCY_TRIPPED.clear()
    _LATENCY_REPORTS.clear()
    _RATE_LIMIT_UNTIL.clear()


def latency_guard_tripped(provider: str) -> str:
    """Non-empty reason when `provider` measured over its latency cap this
    process — callers skip it for the rest of the process (self-healing:
    a new process re-probes)."""
    return _LATENCY_TRIPPED.get(provider, "")


def report_latency(provider: str, elapsed_ms: int) -> None:
    """Record a measured hosted-free call latency for `provider`; trips its
    breaker when it exceeds max_latency_ms(). The provider's FIRST measured
    call gets a grace pass (may carry connection setup / cold routing), same
    as local_models.report_latency."""
    cap = max_latency_ms()
    n = _LATENCY_REPORTS.get(provider, 0) + 1
    _LATENCY_REPORTS[provider] = n
    if not cap or elapsed_ms <= cap or _LATENCY_TRIPPED.get(provider):
        return
    if n == 1:
        log.info(
            "hosted-free %s took %dms (cap %dms) on the first call — "
            "grace, not tripping yet", provider, int(elapsed_ms), cap,
        )
        return
    _LATENCY_TRIPPED[provider] = (
        f"hosted-free {provider} took {int(elapsed_ms)}ms (cap {cap}ms) — "
        f"skipping this provider for the rest of this process"
    )
    log.info("hosted-free latency breaker tripped: %s", _LATENCY_TRIPPED[provider])


def rate_limit_tripped(provider: str) -> str:
    """Non-empty reason when `provider` is under an active rate-limit
    cooldown (self-clears once the cooldown elapses, unlike the latency
    breaker — an RPM cap is a temporary condition, not a verdict on the box)."""
    until = _RATE_LIMIT_UNTIL.get(provider, 0.0)
    if until and time.monotonic() < until:
        remaining = until - time.monotonic()
        return f"hosted-free {provider} rate-limited (429) — cooling down {remaining:.0f}s more"
    return ""


def _trip_rate_limit(provider: str, retry_after: Optional[float]) -> None:
    cooldown = retry_after if (retry_after is not None and retry_after > 0) else rate_limit_default_cooldown_s()
    _RATE_LIMIT_UNTIL[provider] = time.monotonic() + cooldown
    log.info(
        "hosted-free rate-limit breaker tripped for %s — cooling down %.0fs%s",
        provider, cooldown, " (Retry-After honored)" if retry_after else " (default cooldown)",
    )


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """Parse an HTTP `Retry-After` header: either delta-seconds or an
    HTTP-date. Returns None when absent/unparseable — caller falls back to
    `rate_limit_default_cooldown_s()`."""
    if not value:
        return None
    value = str(value).strip()
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    try:
        import datetime
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt is None:
            return None
        now = (
            datetime.datetime.now(dt.tzinfo)
            if dt.tzinfo is not None
            else datetime.datetime.utcnow()
        )
        return max(0.0, (dt - now).total_seconds())
    except Exception:
        return None


def available() -> bool:
    """At least one configured provider is currently usable (not rate-limited
    or latency-tripped). Top-level gate step_exec checks before attempting
    the tier — mirrors local_models.validator_available()'s role."""
    for name in configured_providers():
        if not rate_limit_tripped(name) and not latency_guard_tripped(name):
            return True
    return False


# ---------------------------------------------------------------------------
# Ladder adapter — tries configured providers in order
# ---------------------------------------------------------------------------

class _HostedFreeLadder(LLMAdapter):
    """Tries configured hosted-free providers (Groq, then Gemini by default)
    in order. Skips any provider currently rate-limited or latency-tripped;
    a 429 on an attempted provider additionally trips its rate-limit breaker
    (honoring Retry-After) before moving to the next.

    Raises the last error only when every provider was tried-and-failed or
    skip-tripped — the caller (step_exec.verify_step) treats that exactly
    like an unreachable local validator: fall through to the paid tier.
    Never silently swallows — an exception here is deliberate signal, same
    contract as local_models.LocalValidatorAdapter.
    """

    backend = "hosted_free"

    def __init__(self, adapters: List[Tuple[str, "LLMAdapter"]]):
        self._adapters = list(adapters)
        self._active_provider = ""

    @property
    def model_key(self) -> str:  # type: ignore[override]
        for name, a in self._adapters:
            if name == self._active_provider:
                return getattr(a, "model_key", "")
        return ""

    @model_key.setter
    def model_key(self, value: str) -> None:
        pass  # read-only; tracks the active provider's model

    def complete(self, messages, **kwargs) -> "LLMResponse":
        import requests

        last_exc: Optional[Exception] = None
        tried = False
        for name, adapter in self._adapters:
            if rate_limit_tripped(name):
                log.debug("hosted-free %s: %s — skipping", name, rate_limit_tripped(name))
                continue
            if latency_guard_tripped(name):
                log.debug("hosted-free %s: %s — skipping", name, latency_guard_tripped(name))
                continue
            tried = True
            self._active_provider = name
            _t0 = time.monotonic()
            try:
                resp = adapter.complete(messages, **kwargs)
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429:
                    retry_after = _parse_retry_after(
                        exc.response.headers.get("Retry-After") if exc.response is not None else None
                    )
                    _trip_rate_limit(name, retry_after)
                else:
                    log.info("hosted-free %s failed (HTTP %s) — trying next provider", name, status)
                last_exc = exc
                continue
            except Exception as exc:
                # A slow non-HTTP failure (timeout, connection reset) still
                # pays the full latency tax — feed it through the same
                # breaker as a slow success, else nothing ever skips this
                # provider and every subsequent call repeats the same hang.
                report_latency(name, int((time.monotonic() - _t0) * 1000))
                log.info("hosted-free %s failed (%s) — trying next provider", name, exc)
                last_exc = exc
                continue
            report_latency(name, int((time.monotonic() - _t0) * 1000))
            resp.backend = f"hosted_free:{name}"
            return resp
        self._active_provider = ""
        if not tried:
            raise RuntimeError(
                "hosted_free: all configured providers are currently rate-limited or slow"
            )
        raise last_exc or RuntimeError("hosted_free: no providers configured")


def build_hosted_free_adapter() -> Optional["LLMAdapter"]:
    """Build the hosted-free validation-ladder adapter, or None to use the
    next tier (mirrors local_models.build_local_validator_adapter's contract).

    Returns:
      * None — no GROQ_API_KEY/GEMINI_API_KEY configured (or the tier is
        disabled via config). Callers fall back unchanged.
      * A `_HostedFreeLadder` over the configured, keyed providers, in
        priority order.
    """
    providers = configured_providers()
    if not providers:
        return None

    from llm import GroqAdapter, GeminiAdapter

    env = _load_env()
    adapters: List[Tuple[str, "LLMAdapter"]] = []
    for name in providers:
        key = _get_key(_ENV_KEYS[name], env)
        if not key:
            continue
        if name == "groq":
            adapters.append((name, GroqAdapter(api_key=key, model=groq_model())))
        elif name == "gemini":
            adapters.append((name, GeminiAdapter(api_key=key, model=gemini_model())))
    if not adapters:
        return None
    return _HostedFreeLadder(adapters)
