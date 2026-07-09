"""Backend-error classification (BACKEND_RESILIENCE_DESIGN §1-2, slice 1).

Table-driven over the design's classification rows — every shape below is a
documented or live-observed error. The two named traps are regression-pinned:
credit-exhaustion-400 must fail over with a billing action (it used to die
raw), and insufficient_quota-429 must NOT retry (it used to burn the ladder).
"""

import subprocess

import pytest

from llm_errors import (
    AUTH_ACTIONABLE,
    BILLING_ACTIONABLE,
    FAILOVER,
    FATAL,
    INPUT_TOO_LARGE,
    RETRY_AT,
    RETRY_BACKOFF,
    BackendError,
    classify_error,
    is_actionable,
)


# (exception, expected_class, retryable, failover)
CASES = [
    # --- Trap #1: Anthropic credit exhaustion hides in a 400
    (RuntimeError("Error code: 400 - {'type': 'error', 'error': {'type': "
                  "'invalid_request_error', 'message': 'Your credit balance "
                  "is too low to access the Anthropic API.'}}"),
     BILLING_ACTIONABLE, False, True),
    # --- Trap #2: OpenAI insufficient_quota hides in a 429
    (RuntimeError("429 Client Error: Too Many Requests — "
                  "{'error': {'code': 'insufficient_quota', 'message': "
                  "'You exceeded your current quota'}}"),
     BILLING_ACTIONABLE, False, True),
    # OpenRouter credits
    (RuntimeError("402: This request requires more credits."),
     BILLING_ACTIONABLE, False, True),
    # Plain rate limit: retry, no failover
    (RuntimeError("Error code: 429 rate_limit_error"), RETRY_BACKOFF, True, False),
    # Overloaded 529: retry AND failover-after-exhaustion (old dual policy)
    (RuntimeError("API Error: 529 Overloaded"), RETRY_BACKOFF, True, True),
    (RuntimeError("500 internal server error"), RETRY_BACKOFF, True, True),
    (RuntimeError("connection reset by peer"), RETRY_BACKOFF, True, False),
    # Auth: never retry, one failover, actionable
    (RuntimeError("Not logged in · Please run /login"), AUTH_ACTIONABLE, False, True),
    (RuntimeError("Error code: 401 - unauthorized"), AUTH_ACTIONABLE, False, True),
    (RuntimeError("OAuth token revoked · Please run /login"),
     AUTH_ACTIONABLE, False, True),
    # 403 + billing marker: billing outranks auth
    (RuntimeError("403 Forbidden {'type': 'billing_error'}"),
     BILLING_ACTIONABLE, False, True),
    # Context overrun: neither retry nor failover
    (RuntimeError("prompt is too long: 208310 tokens > 200000 maximum"),
     INPUT_TOO_LARGE, False, False),
    (RuntimeError("context_length_exceeded"), INPUT_TOO_LARGE, False, False),
    # Subprocess lane deaths → failover
    (RuntimeError("claude binary not found"), FAILOVER, False, True),
    (RuntimeError("subprocess failed with rc=1"), FAILOVER, False, True),
    # Usage limit with a stated reset
    (RuntimeError("You've hit your weekly limit · resets Mon 12:00am"),
     RETRY_AT, True, False),
    # Unclassified stays fatal (propagate raw)
    (ValueError("some schema mismatch"), FATAL, False, False),
]


@pytest.mark.parametrize("exc,cls,retryable,failover", CASES,
                         ids=[str(c[0])[:48] for c in CASES])
def test_classification_table(exc, cls, retryable, failover):
    info = classify_error(exc)
    assert info.error_class == cls
    assert info.retryable is retryable
    assert info.failover is failover


def test_subprocess_timeout_is_failover():
    exc = subprocess.TimeoutExpired(cmd="claude", timeout=600)
    info = classify_error(exc)
    assert info.error_class == FAILOVER
    assert info.failover is True


def test_kill_reason_attr_is_failover():
    exc = RuntimeError("adapter produced nothing")
    exc.maro_kill_reason = "liveness"
    assert classify_error(exc).error_class == FAILOVER


def test_actionable_classes_carry_user_action():
    for exc, cls, *_ in CASES:
        info = classify_error(exc)
        if is_actionable(info):
            assert info.user_action, f"actionable {info.error_class} without action: {exc}"


def test_cli_auth_action_names_login():
    info = classify_error(RuntimeError("Not logged in · Please run /login"),
                          backend="subprocess")
    assert "/login" in info.user_action
    assert "ANTHROPIC_API_KEY" in info.user_action


def test_sdk_exception_type_names_still_retry():
    class RateLimitError(Exception):
        pass
    assert classify_error(RateLimitError("please slow down")).retryable is True


def test_backend_error_is_runtimeerror_with_action():
    info = classify_error(RuntimeError("Your credit balance is too low"),
                          backend="anthropic")
    err = BackendError(info)
    assert isinstance(err, RuntimeError)  # handle.main's catch renders it clean
    assert "Top up" in str(err)


def test_llm_predicates_are_views():
    from llm import _is_failover_error, _is_retryable
    # the two traps, through the actual predicates the machinery calls
    quota = RuntimeError("429 insufficient_quota")
    assert _is_retryable(quota) is False
    assert _is_failover_error(quota) is True
    credit = RuntimeError("400 Your credit balance is too low")
    assert _is_retryable(credit) is False
    assert _is_failover_error(credit) is True
    # unchanged shapes keep their old answers
    assert _is_retryable(RuntimeError("rate limit exceeded 429")) is True
    assert _is_failover_error(RuntimeError("503 service unavailable")) is True
    assert _is_failover_error(RuntimeError("400 bad request schema")) is False


class _FakeAdapter:
    backend = "fake"
    model_key = "fake"

    def __init__(self, exc=None, content="ok"):
        self._exc = exc
        self._content = content

    def complete(self, messages, **kwargs):
        if self._exc is not None:
            raise self._exc
        from llm import LLMResponse
        return LLMResponse(content=self._content, model="fake", backend=self.backend,
                           input_tokens=1, output_tokens=1)


def test_failover_adapter_wraps_actionable_exhaustion():
    from llm import FailoverAdapter, LLMMessage
    fa = FailoverAdapter([_FakeAdapter(exc=RuntimeError(
        "Not logged in · Please run /login"))])
    with pytest.raises(BackendError) as ei:
        fa.complete([LLMMessage("user", "hi")])
    assert ei.value.info.error_class == AUTH_ACTIONABLE


def test_failover_adapter_still_fails_over_then_succeeds():
    from llm import FailoverAdapter, LLMMessage
    fa = FailoverAdapter([
        _FakeAdapter(exc=RuntimeError("Your credit balance is too low")),
        _FakeAdapter(content="second backend answer"),
    ])
    resp = fa.complete([LLMMessage("user", "hi")])
    assert resp.content == "second backend answer"


def test_failover_adapter_fatal_propagates_raw():
    from llm import FailoverAdapter, LLMMessage
    fa = FailoverAdapter([_FakeAdapter(exc=ValueError("schema mismatch"))])
    with pytest.raises(ValueError):
        fa.complete([LLMMessage("user", "hi")])
