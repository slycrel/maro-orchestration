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
    CONTAINER_AUTH,
    FAILOVER,
    FATAL,
    INPUT_TOO_LARGE,
    OUTPUT_CAP_EXCEEDED,
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
    # Output-cap overrun (no_tools CLAUDE_CODE_MAX_OUTPUT_TOKENS hard error):
    # request-shaped, NOT backend death — must outrank the generic
    # "subprocess failed" row above. Live shape from azure-finch 2026-07-17,
    # where this failed over to a dead OpenRouter key (402 alert spam).
    (RuntimeError("claude subprocess failed (rc=1): API Error: Claude's "
                  "response exceeded the 128 output token maximum. To "
                  "configure this behavior, set the "
                  "CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."),
     OUTPUT_CAP_EXCEEDED, False, False),
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


# ---------------------------------------------------------------------------
# Billing/auth circuit breaker (azure-finch 2026-07-17: one dead OpenRouter
# key re-tried and re-alerted on every failover walk of the run)
# ---------------------------------------------------------------------------

class _NamedFake(_FakeAdapter):
    def __init__(self, backend, exc=None, content="ok"):
        super().__init__(exc=exc, content=content)
        self.backend = backend
        self.calls = 0

    def complete(self, messages, **kwargs):
        self.calls += 1
        return super().complete(messages, **kwargs)


def test_circuit_skips_billing_dead_backend_on_next_call():
    from llm import FailoverAdapter, LLMMessage, _circuit_clear
    _circuit_clear()
    dead = _NamedFake("openrouter", exc=RuntimeError(
        "402 Client Error: Payment Required"))
    ok = _NamedFake("openai", content="answer")
    # First walk: dead backend is tried (trips the circuit), failover succeeds.
    fa = FailoverAdapter([dead, ok])
    assert fa.complete([LLMMessage("user", "hi")]).content == "answer"
    assert dead.calls == 1
    # Fresh adapter, same process: the dead backend is skipped outright.
    dead2 = _NamedFake("openrouter", exc=RuntimeError(
        "402 Client Error: Payment Required"))
    ok2 = _NamedFake("openai", content="answer2")
    fa2 = FailoverAdapter([dead2, ok2])
    assert fa2.complete([LLMMessage("user", "hi")]).content == "answer2"
    assert dead2.calls == 0


def test_circuit_trip_during_walk_does_not_skip_same_walk():
    # The existing failover semantics within ONE walk are unchanged: the trip
    # protects future complete() calls only.
    from llm import FailoverAdapter, LLMMessage, _circuit_clear
    _circuit_clear()
    fa = FailoverAdapter([
        _FakeAdapter(exc=RuntimeError("Your credit balance is too low")),
        _FakeAdapter(content="still reached"),
    ])
    assert fa.complete([LLMMessage("user", "hi")]).content == "still reached"


def test_all_circuit_open_raises_backend_error():
    from llm import FailoverAdapter, LLMMessage, _circuit_clear, _circuit_trip
    from llm_errors import classify_error
    _circuit_clear()
    info = classify_error(RuntimeError("402 payment required"), backend="openrouter")
    _circuit_trip("openrouter", info)
    only = _NamedFake("openrouter", content="never")
    fa = FailoverAdapter([only])
    with pytest.raises(BackendError) as ei:
        fa.complete([LLMMessage("user", "hi")])
    assert ei.value.info.error_class == BILLING_ACTIONABLE
    assert only.calls == 0


def test_circuit_ttl_expiry_restores_backend():
    import llm as llm_mod
    from llm import FailoverAdapter, LLMMessage, _circuit_clear, _circuit_trip
    from llm_errors import classify_error
    _circuit_clear()
    info = classify_error(RuntimeError("402 payment required"), backend="openrouter")
    _circuit_trip("openrouter", info)
    llm_mod._BACKEND_CIRCUIT["openrouter"]["until"] = 1.0  # long expired
    healed = _NamedFake("openrouter", content="back")
    fa = FailoverAdapter([healed])
    assert fa.complete([LLMMessage("user", "hi")]).content == "back"
    assert healed.calls == 1


def test_actionable_failover_alert_dedups_and_carries_chain(monkeypatch):
    from llm import FailoverAdapter, LLMMessage, _circuit_clear
    _circuit_clear()
    events = []
    import notify
    monkeypatch.setattr(notify, "emit",
                        lambda et, payload, **kw: events.append((et, payload)) or True)

    def walk():
        fa = FailoverAdapter([
            _NamedFake("subprocess", exc=RuntimeError(
                "claude subprocess failed (rc=1): API Error: 529 Overloaded")),
            _NamedFake("openrouter", exc=RuntimeError(
                "402 Client Error: Payment Required")),
            _NamedFake("openai", content="answer"),
        ])
        return fa.complete([LLMMessage("user", "hi")])

    assert walk().content == "answer"
    assert len(events) == 1
    etype, payload = events[0]
    assert etype == "backend_actionable"
    # Root-cause-first chain: subprocess's failure is visible, not just the
    # billed backends (the 2026-07-17 misread).
    assert "subprocess(failover)" in payload["failover_chain"]
    assert "openrouter(billing_actionable)" in payload["failover_chain"]
    # Second walk in the same process: no second alert for the same key.
    assert walk().content == "answer"
    assert len(events) == 1


class _MarkedAuthExpired(RuntimeError):
    """Stand-in for container_exec.ContainerAuthExpired: the classifier keys
    on the type marker, never on the message text."""
    maro_error_class = CONTAINER_AUTH


def test_container_auth_marker_outranks_text_and_never_fails_over():
    # The message deliberately carries a backend-auth pattern ("oauth token
    # expired") AND a subprocess-death pattern: the marker must win, and the
    # policy must be pause-shaped (no retry, no failover — the API lane
    # would run the worker outside the container the require contract
    # demands).
    exc = _MarkedAuthExpired(
        "executor.container=require but the container lane is unavailable: "
        "container auth breaker tripped (oauth token expired); claude subprocess failed")
    info = classify_error(exc, backend="subprocess")
    assert info.error_class == CONTAINER_AUTH
    assert info.retryable is False and info.failover is False
    assert is_actionable(info)
    assert "maro-claude-auth" in info.user_action and "/login" in info.user_action


def test_container_auth_marker_is_a_type_marker_not_a_string_match():
    # Plain text mentioning the class name does not qualify.
    info = classify_error(RuntimeError("container_auth something"))
    assert info.error_class != CONTAINER_AUTH


def test_backend_error_keeps_its_structured_classification():
    # Review 2026-09-13: FailoverAdapter wraps an actionable failure in
    # BackendError; re-classifying the wrapper's TEXT lost the class (the
    # container_auth refusal came back as auth_actionable/failover). The
    # wrapper's own ErrorInfo is the classification.
    from llm_errors import BackendError, ErrorInfo
    info = ErrorInfo(error_class=CONTAINER_AUTH, backend="subprocess", retryable=False,
                     failover=False, user_action="re-seed the maro-claude-auth volume",
                     detail="container auth breaker tripped (oauth token expired)")
    wrapped = BackendError(info)
    assert "oauth token expired" in str(wrapped)          # text that WOULD misclassify
    got = classify_error(wrapped, backend="subprocess")
    assert got is info and got.error_class == CONTAINER_AUTH and got.failover is False


@pytest.mark.parametrize("fresh, cost", [
    (float("inf"), 0), (1e400, float("nan")), (-7, -1), ("many", None), (None, "x"), (float("inf"), float("-inf")),
])
def test_kill_evidence_is_total_finite_and_nonnegative(fresh, cost):
    # Round 9: int(inf) raised OverflowError past the blocked builder's
    # guard; NaN reached cost records; a negative subtracted from totals.
    from llm_errors import kill_evidence
    e = RuntimeError("killed")
    e.maro_partial_output = "abc"; e.fresh_input_tokens = fresh; e.estimated_cost_usd = cost
    assert kill_evidence(e) == ("[partial output before kill]\nabc", 0, 0.0)


def test_kill_evidence_reads_through_the_failover_wrapper():
    from llm_errors import kill_evidence
    cause = RuntimeError("killed")
    cause.maro_partial_output = "abc"; cause.fresh_input_tokens = 7; cause.estimated_cost_usd = 0.5
    wrapper = RuntimeError("wrapped")
    wrapper.__cause__ = cause
    assert kill_evidence(wrapper) == ("[partial output before kill]\nabc", 7, 0.5)
    assert kill_evidence(RuntimeError("bare")) == ("", 0, 0.0)


@pytest.mark.parametrize("value, cast, want", [
    (10 ** 400, int, 0), (10 ** 16, int, 0), (10 ** 12, int, 10 ** 12),
    (1e300, float, 0.0), (float(10 ** 15), float, float(10 ** 15)), ("1" + "0" * 400, int, 0),
])
def test_finite_nonneg_is_bounded(value, cast, want):
    # Round 14: a valid JSON integer of 400 digits passed as a finite int
    # and overflowed the float pricer ahead of the pause seam.
    from llm_errors import finite_nonneg, COUNTER_MAX
    assert finite_nonneg(value, cast, cast(0)) == want
    assert COUNTER_MAX == 10 ** 15


def test_an_oversized_terminal_counter_keeps_the_others():
    from llm_errors import call_usage_evidence
    exc = RuntimeError("claude subprocess failed")
    exc.fresh_input_tokens = 10 ** 400; exc.fresh_output_tokens = 9; exc.estimated_cost_usd = 0.12
    ev = call_usage_evidence(exc)
    assert (ev["tokens_in"], ev["tokens_out"], ev["cost"]) == (0, 9, 0.12)
