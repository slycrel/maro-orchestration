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

import math
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# The six classes (+ FATAL for everything unmatched).
RETRY_BACKOFF = "retry_backoff"        # transient — same-backend ladder
RETRY_AT = "retry_at"                  # rate/usage limit with a reset time
FAILOVER = "failover"                  # this backend is down — try the next
AUTH_ACTIONABLE = "auth_actionable"    # credentials dead — tell user the fix
BILLING_ACTIONABLE = "billing_actionable"  # credits/quota gone — never retry
INPUT_TOO_LARGE = "input_too_large"    # context overrun — retry/failover useless
OUTPUT_CAP_EXCEEDED = "output_cap_exceeded"  # utility call blew its own token cap — caller's fallback, never failover
BUDGET_RUNAWAY = "budget_runaway"      # run's runaway cost circuit tripped — never retry/failover
TOKEN_RUNAWAY = "token_runaway"        # ONE subprocess call crossed the per-call ingest ceiling — step blocked, run continues
CONTAINER_AUTH = "container_auth"      # executor.container=require and the auth volume's session is dead — PAUSE the run, human re-seeds
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


class BudgetRunawayError(RuntimeError):
    """The run's runaway cost circuit tripped (llm.arm_cost_meter).

    Raised by FailoverAdapter.complete PRE-call — no backend is tried, no
    cost incurred. Deliberately not a BackendError: nothing is wrong with any
    backend; the run has spent past multiplier x cost_budget and further
    calls are refused. Never retried, never failed-over (retrying is exactly
    the churn the circuit exists to stop).
    """

    def __init__(self, spent_usd: float, ceiling_usd: float):
        self.spent_usd = spent_usd
        self.ceiling_usd = ceiling_usd
        super().__init__(
            f"runaway cost circuit: ${spent_usd:.2f} already spent this run >= "
            f"ceiling ${ceiling_usd:.2f} (budget.runaway_multiplier x cost_budget) "
            f"— refusing further LLM calls. If this work was legitimate, raise "
            f"budget.per_run_usd or budget.runaway_multiplier."
        )


class TokenRunawayError(RuntimeError):
    """One subprocess call crossed the per-call uncached-input token ceiling
    (mid-step token brake, llm._build_step_token_brake).

    Raised by the stream-side probe MID-call: the subprocess is killed the
    poll after cumulative fresh ingest (input_tokens + cache_creation, deduped
    per API message) crosses the ceiling. Distinct from BudgetRunawayError on
    purpose — that one means the RUN has overspent and stops the loop; this
    one means a SINGLE call is pathologically ingesting (the tire-runs
    2.14M-token curl step) and only that step should die. Never retried,
    never failed-over: replaying the same step on any backend replays the
    same ingest.
    """

    def __init__(self, fresh_input_tokens: int, ceiling_tokens: int,
                 estimated_cost_usd: float = 0.0,
                 weighted_input_tokens: int = 0, trigger: str = "fresh"):
        self.fresh_input_tokens = int(fresh_input_tokens)
        self.ceiling_tokens = int(ceiling_tokens)
        # Carried so the blocked outcome can record what the killed call
        # actually cost. Reporting a 300K-token call as zero tokens / zero
        # dollars understates run totals and skill telemetry, and an unmetered
        # run loses the spend entirely (adversarial review 2026-07-27, 3/3).
        self.estimated_cost_usd = float(estimated_cost_usd or 0.0)
        # Which ceiling fired: "fresh" (uncached ingest) or "weighted"
        # (fresh + cache_read/10 — the transcript-amplification backstop the
        # same review flagged: fresh alone can't see a call that re-reads a
        # quarter-million-token cached transcript twenty times).
        self.weighted_input_tokens = int(weighted_input_tokens or 0)
        self.trigger = str(trigger or "fresh")
        if self.trigger == "weighted":
            _detail = (
                f"mid-step token brake: weighted ingest (fresh + cache_read/10) "
                f"{self.weighted_input_tokens} >= ceiling {self.ceiling_tokens} "
                f"(fresh alone: {self.fresh_input_tokens}) — call killed, step "
                f"blocked, run continues. If this step legitimately needs more, "
                f"raise llm.subprocess.weighted_input_ceiling_tokens (config) or "
                f"MARO_SUBPROCESS_WEIGHTED_INPUT_CEILING (env; 0 disables)."
            )
            super().__init__(_detail)
            return
        super().__init__(
            f"mid-step token brake: one subprocess call ingested "
            f"{self.fresh_input_tokens} uncached input tokens >= ceiling "
            f"{self.ceiling_tokens} — call killed, step blocked, run continues. "
            f"If this step legitimately needs more, raise "
            f"llm.subprocess.fresh_input_ceiling_tokens (config) or "
            f"MARO_SUBPROCESS_FRESH_INPUT_CEILING (env; 0 disables)."
        )


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

# A TERMINAL failure (the CLI ran and reported a result object of its own)
# classifies from that object's error fields, and only by the phrases the
# CLI/API author — never the bare status codes or single words above,
# which a partial-work `result` mentions incidentally (review round 20,
# 2026-09-13: "Read invoice 401 before stopping." ahead of a reset in
# `errors[]` classified a healthy host as auth-dead and lost the pause).
_TERMINAL_BILLING_PHRASES = (
    "credit balance is too low", "insufficient_quota", "requires more credits",
    "payment required", "quota exceeded",
)
_TERMINAL_INPUT_PHRASES = (
    "prompt is too long", "context_length_exceeded", "request_too_large",
    "maximum context length",
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
    if cls == CONTAINER_AUTH:
        return ("The executor container's Claude session has expired "
                "(executor.container=require). Re-seed the maro-claude-auth "
                "volume: run the interactive login from `maro-bootstrap "
                "container-setup` (`claude /login` inside the executor image), "
                "then resume the paused run.")
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
    # A BackendError already IS a classification: FailoverAdapter wraps an
    # actionable failure in one so downstream surfaces render the fix. Re-
    # classifying its rendered text lost the class (review 2026-09-13: the
    # container_auth refusal came back as auth_actionable/failover after the
    # wrap, and the typed pause never fired). Structured info outranks text.
    if isinstance(exc, BackendError) and isinstance(getattr(exc, "info", None), ErrorInfo):
        return exc.info
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

    # Exact type outranks all text matching: the circuit's own message
    # mentions "cost"/"budget" and must never ride a retry ladder. Both
    # runaway classes also outrank the maro_kill_reason FAILOVER shape below:
    # the stream-probe kill path ATTACHES maro_kill_reason to these very
    # exceptions, and a probe-ordered kill must never be re-run elsewhere.
    if isinstance(exc, BudgetRunawayError):
        return _mk(BUDGET_RUNAWAY)
    if isinstance(exc, TokenRunawayError):
        return _mk(TOKEN_RUNAWAY)
    # Type marker, not text: container_exec.ContainerAuthExpired carries it.
    # Outranks the auth text patterns below because the remedy differs — no
    # failover (the API lane would run the worker OUTSIDE the container the
    # require contract demands), no retry: the run pauses until re-seeded.
    if getattr(exc, "maro_error_class", "") == CONTAINER_AUTH:
        return _mk(CONTAINER_AUTH)

    # The CLI RAN and reported a terminal execution failure of its own
    # (error_max_turns and kin). The binary is fine and the work is partly
    # done: neither a retry nor a failover may replay it on another backend
    # (review round 12, 2026-09-13: the generic "subprocess failed" text
    # routed it to FAILOVER and the wrapper re-ran the finished work
    # elsewhere). STRUCTURED evidence decides here, ahead of every text
    # pattern below (round 20: the display message carries the partial-work
    # `result`, and an incidental "401"/"402"/"413" in it classified a
    # rate-limited terminal as a host auth/billing/input failure — the
    # healthy host circuit tripped, another backend replayed the work, the
    # no-tokens pause was lost). The markers come from the terminal
    # object's own fields (llm._mark_terminal_failure): the shared
    # rate-limit reading, the shared auth reading, and the error fields'
    # text for the authored billing/input phrases only.
    if getattr(exc, "maro_terminal_failure", False):
        _ttext = str(getattr(exc, "maro_terminal_text", "") or "").lower()
        if getattr(exc, "maro_rate_limited", False):
            # A stated limit is a wait, not a replay.
            return _mk(RETRY_AT, retryable=True)
        if getattr(exc, "maro_terminal_auth", False):
            # The CLI names a dead HOST credential: same remedy as the
            # text pattern below, decided from the object's fields.
            return _mk(AUTH_ACTIONABLE, failover=True)
        if "limit" in _ttext and "resets" in _ttext:
            return _mk(RETRY_AT, retryable=True)
        if any(p in _ttext for p in _TERMINAL_BILLING_PHRASES):
            return _mk(BILLING_ACTIONABLE, failover=True)
        if any(p in _ttext for p in _TERMINAL_INPUT_PHRASES):
            return _mk(INPUT_TOO_LARGE)
        return _mk(FATAL)

    # The CLI ran to a result this adapter could not convert (round 20):
    # a protocol failure of THIS call — never a replay elsewhere, whatever
    # its message text happens to match.
    if getattr(exc, "maro_protocol_failure", False):
        return _mk(FATAL)

    if any(p in msg for p in _INPUT_PATTERNS):
        return _mk(INPUT_TOO_LARGE)

    # A no_tools utility call overran CLAUDE_CODE_MAX_OUTPUT_TOKENS. Since
    # 2026-07-29 that env cap is one uniform runaway ceiling (llm.py
    # _NO_TOOLS_OUTPUT_CEILING), not per-call contract enforcement, so
    # tripping it means a genuine generation runaway — failover would just
    # re-run the runaway on a billed backend (azure-finch 2026-07-17:
    # routing call → OpenRouter 402 alert spam → OpenAI). Must outrank the
    # generic "subprocess failed" shape below. Propagate raw; utility call
    # sites have parse-fallbacks that handle the exception.
    if "output token maximum" in msg:
        return _mk(OUTPUT_CAP_EXCEEDED)

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


def kill_evidence(exc: BaseException) -> Tuple[str, int, float]:
    """What a killed/refused adapter call leaves behind, read ONCE for every
    outcome builder (review round 8, 2026-09-13: the worker lane copied the
    class but dropped the partial output and the runaway's measured
    ingest that step outcomes keep). Returns (partial_result_text,
    fresh_input_tokens, estimated_cost_usd): the partial output is the
    only record of what the call did before dying (the tail, framed);
    the runaway fields are the spend the brake exists to account for."""
    _u = call_usage_evidence(exc)
    return _u["partial"], _u["tokens_in"], _u["cost"]


def evidence_attr(exc: BaseException, name: str, default=None):
    """Read an evidence attribute off `exc` or, failing that, its cause
    chain: FailoverAdapter re-raises actionable failures as a fresh
    BackendError `from` the adapter's exception, so the evidence rides the
    cause, not the wrapper (review round 9, 2026-09-13)."""
    _e, _hops = exc, 0
    while _e is not None and _hops < 8:
        _v = getattr(_e, name, None)
        if _v is not None:
            return _v
        _e, _hops = getattr(_e, "__cause__", None), _hops + 1
    return default


# No token counter or dollar figure a call can produce is this large; a
# JSON integer past it is malformed (review round 14, 2026-09-13: a valid
# JSON integer of 400 digits passed as a finite int, then the cost
# estimator's float conversion raised OverflowError ahead of the pause
# seam). Bounded here so every consumer prices what it accepts.
COUNTER_MAX = 10 ** 15


def finite_nonneg(v, cast, default):
    """Accounting is total, finite, bounded and non-negative or it is the
    default (review round 9: int(inf) raised OverflowError past the blocked
    builder's guard, NaN reached cost records, a negative subtracted;
    round 14: an oversized integer overflowed the pricer)."""
    try:
        x = cast(v if v is not None else default)
    except Exception:
        return default
    if isinstance(x, float) and not math.isfinite(x):
        return default
    if x > COUNTER_MAX:
        return default
    return x if x >= 0 else default


def call_usage_evidence(exc: BaseException) -> Dict[str, Any]:
    """Everything a failed/killed adapter call leaves behind, as one record
    (review round 10, 2026-09-13: the 3-tuple carried input tokens and
    cost only — a failure after cache-served work recorded zero tokens
    and, through the wrapper with zero fresh input, zero spend). Keys:
    partial, tokens_in, tokens_out, cache_read, cost. Never raises."""
    _p = str(evidence_attr(exc, "maro_partial_output", "") or "")
    return {
        "partial": f"[partial output before kill]\n{_p[-2000:]}" if _p else "",
        "tokens_in": finite_nonneg(evidence_attr(exc, "fresh_input_tokens", 0), int, 0),
        "tokens_out": finite_nonneg(evidence_attr(exc, "fresh_output_tokens", 0), int, 0),
        "cache_read": finite_nonneg(evidence_attr(exc, "fresh_cache_read_tokens", 0), int, 0),
        "cost": finite_nonneg(evidence_attr(exc, "estimated_cost_usd", 0.0), float, 0.0),
    }


def is_actionable(info: ErrorInfo) -> bool:
    """True when the user must act (auth/billing/input) — these surface on
    every channel (stderr, run metadata, notify, doctor)."""
    return info.error_class in (AUTH_ACTIONABLE, BILLING_ACTIONABLE, INPUT_TOO_LARGE,
                                CONTAINER_AUTH)
