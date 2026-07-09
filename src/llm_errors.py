"""Backend-error classification + actionable messaging (BACKEND_RESILIENCE_DESIGN §1-2).

One classifier turns any backend exception into one of six classes, each
with a fixed policy. The old two-predicate worldview (`_is_retryable` /
`_is_failover_error` in llm.py) becomes a pair of views over the class —
same call shape, but errors that used to be misfiled get the right policy:

- Anthropic credit exhaustion is a 400 "credit balance is too low" that
  matched NEITHER predicate → died as a raw traceback. Now BILLING_ACTIONABLE.
- OpenAI `insufficient_quota` arrives as a 429 → burned the full retry
  ladder on a permanent billing failure. Now BILLING_ACTIONABLE.

Matching is prefix/substring on lowered text, never exact — the claude-CLI
strings have already changed between versions, and provider type lists grow.

Design + evidence: docs/BACKEND_RESILIENCE_DESIGN.md (classification table
sources every row in _classify below from a documented or live-observed
error shape).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Optional

# The six classes (+ FATAL for everything unmatched).
RETRY_BACKOFF = "retry_backoff"        # transient — same-backend ladder
RETRY_AT = "retry_at"                  # rate/usage limit with a reset time
FAILOVER = "failover"                  # this backend is down — try the next
AUTH_ACTIONABLE = "auth_actionable"    # credentials dead — tell user the fix
BILLING_ACTIONABLE = "billing_actionable"  # credits/quota gone — never retry
INPUT_TOO_LARGE = "input_too_large"    # context overrun — retry/failover useless
FATAL = "fatal"                        # unclassified — propagate raw


@dataclass
class ErrorInfo:
    """Classification result. `retryable`/`failover` are the policy flags the
    existing machinery consumes (llm._is_retryable / _is_failover_error views);
    `user_action` is non-empty exactly when there is something the user can DO."""
    error_class: str
    backend: str = ""
    retryable: bool = False
    failover: bool = False
    user_action: str = ""
    detail: str = ""


class BackendError(RuntimeError):
    """An LLM-backend failure with a user-actionable message.

    Subclasses RuntimeError deliberately: handle.main()'s existing
    print-`Error: …`-and-exit-1 catch (the no-backend pattern) renders it
    without a traceback, no new catch site needed.
    """

    def __init__(self, info: ErrorInfo):
        self.info = info
        msg = info.user_action or info.detail or info.error_class
        if info.user_action and info.detail:
            msg = f"{info.user_action} [{info.error_class} on {info.backend or 'backend'}: {info.detail[:160]}]"
        super().__init__(msg)


# 5xx markers: retryable today AND failover-eligible after ladder exhaustion
# (preserves the old _is_failover_error overlap).
_SERVER_ERR = ("500", "502", "503", "529", "service unavailable",
               "internal server error", "overloaded")
_RETRY_PATTERNS = ("429", "rate limit", "rate_limit", "timeout", "timed out",
                   "connection", "temporarily unavailable") + _SERVER_ERR
_RETRY_TYPES = ("RateLimitError", "APIStatusError", "APIConnectionError",
                "InternalServerError", "OverloadedError")

_BILLING_PATTERNS = (
    "credit balance is too low",   # Anthropic 400 — trap #1
    "insufficient_quota",          # OpenAI 429 — trap #2
    "requires more credits",       # OpenRouter 402
    "402", "payment required", "quota exceeded", "billing",
)
_AUTH_PATTERNS = (
    "not logged in", "please run /login", "oauth token revoked",
    "oauth token expired", "authentication_error", "invalid api key",
    "api key invalid", "401", "unauthorized", "403", "forbidden",
)
_INPUT_PATTERNS = (
    "prompt is too long", "context_length_exceeded", "request_too_large",
    "maximum context length", "413",
)


def _action_for(cls: str, backend: str) -> str:
    """Message registry — say exactly what to run (design §2)."""
    b = (backend or "").lower()
    if cls == AUTH_ACTIONABLE:
        if b in ("subprocess", "claude", "claude-cli"):
            return ("Claude CLI is not logged in. Run 'claude' then '/login' on this "
                    "machine, or set ANTHROPIC_API_KEY to use the API directly.")
        return ("The API key for this backend was rejected (auth error). Check the "
                "key in your environment/config, or unset it to fall back to "
                "another configured backend.")
    if cls == BILLING_ACTIONABLE:
        return ("Backend credits/quota exhausted (not a rate limit — waiting will "
                "not help). Top up the account, or configure another backend "
                "(see `maro-doctor` for what's available).")
    if cls == INPUT_TOO_LARGE:
        return ("The request exceeded the model's context window. The step needs "
                "to be split or its inputs summarized; re-run with a narrower goal.")
    if cls == RETRY_AT:
        return ("Backend usage limit hit; it resets later. Wait for the reset, or "
                "configure another backend to continue sooner.")
    return ""


def classify_error(exc: Exception, backend: str = "") -> ErrorInfo:
    """Map an exception to (class, policy flags, user action).

    Precedence: input-too-large → billing → auth → subprocess-lane shapes →
    rate/transient → fatal. Billing outranks retry because both real traps
    carry retry-looking markers (429 / 402); auth outranks retry because
    401/403 must never burn the ladder.
    """
    msg = str(exc).lower()
    exc_type = type(exc).__name__

    def _mk(cls, *, retryable=False, failover=False) -> ErrorInfo:
        return ErrorInfo(
            error_class=cls,
            backend=backend,
            retryable=retryable,
            failover=failover,
            user_action=_action_for(cls, backend),
            detail=str(exc)[:500],
        )

    if any(p in msg for p in _INPUT_PATTERNS):
        return _mk(INPUT_TOO_LARGE)

    if any(p in msg for p in _BILLING_PATTERNS):
        # Permanent until billing is fixed: never retry; failover-eligible
        # (another backend may have credit) — always surfaced via user_action.
        return _mk(BILLING_ACTIONABLE, failover=True)

    if any(p in msg for p in _AUTH_PATTERNS):
        # Never retry. One failover attempt is design decision #2 — a dead
        # credential shouldn't kill the run when another backend exists, but
        # the user_action always surfaces so it can't be silently absorbed.
        return _mk(AUTH_ACTIONABLE, failover=True)

    # Subprocess lane: binary missing / crashed / wall-or-liveness kill.
    # The kill (adapter_timeout) is the #1 live failure class on this box;
    # the documented mitigation is the API lane → failover.
    if isinstance(exc, subprocess.TimeoutExpired) or hasattr(exc, "maro_kill_reason"):
        return _mk(FAILOVER, failover=True)
    if "claude binary" in msg or "claude -p" in msg:
        return _mk(FAILOVER, failover=True)
    if "subprocess" in msg and any(s in msg for s in ("failed", "not found", "unavailable")):
        return _mk(FAILOVER, failover=True)

    # Usage limit with a stated reset ("You've hit your weekly limit · resets
    # Mon"). Classified distinctly for messaging/metadata; the wait-until-T
    # policy is slice 4 — until then it rides the retry ladder like before.
    if "limit" in msg and "resets" in msg:
        return _mk(RETRY_AT, retryable=True)

    if any(p in msg for p in _RETRY_PATTERNS) or exc_type in _RETRY_TYPES:
        # 5xx keeps the old dual policy: retry ladder first, failover-eligible
        # on exhaustion. Pure-transient shapes (429/timeout/connection) stay
        # retry-only, as before.
        return _mk(RETRY_BACKOFF, retryable=True,
                   failover=any(p in msg for p in _SERVER_ERR))

    return _mk(FATAL)


def is_actionable(info: ErrorInfo) -> bool:
    """True when the user must act (auth/billing/input) — these surface on
    every channel (stderr, run metadata, notify, doctor)."""
    return info.error_class in (AUTH_ACTIONABLE, BILLING_ACTIONABLE, INPUT_TOO_LARGE)
