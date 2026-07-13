"""Tests for the hosted-free validation-ladder tier (BACKLOG #25).

Covers:
- Inert/no-op by default when GROQ_API_KEY/GEMINI_API_KEY are unset.
- Adapter construction when a key IS set (config-driven model IDs).
- A successful mocked completion (no real network — requests.post mocked).
- The 429 -> rate-limit breaker trip path (Retry-After honored + default
  cooldown fallback), and that a tripped provider is skipped without a
  wasted call on the next attempt.
- Provider-to-provider fallback (Groq 429s -> Gemini serves the call).
- Full exhaustion (all providers tripped) raises without any HTTP calls.
- Latency breaker parity with local_models' grace-then-trip semantics.

No real network calls are made anywhere in this file.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import hosted_free as hf
from llm import GroqAdapter, GeminiAdapter, LLMMessage, LLMResponse


@pytest.fixture(autouse=True)
def _reset_hosted_free_state():
    """Every test gets a clean breaker/latency-report slate."""
    hf.reset_cache()
    yield
    hf.reset_cache()


# ---------------------------------------------------------------------------
# Inert-by-default contract
# ---------------------------------------------------------------------------

def test_inert_when_no_keys_configured(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    assert hf.configured_providers() == []
    assert hf.available() is False
    assert hf.build_hosted_free_adapter() is None


def test_configured_when_groq_key_set(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    assert hf.configured_providers() == ["groq"]
    assert hf.available() is True
    adapter = hf.build_hosted_free_adapter()
    assert adapter is not None


def test_configured_when_gemini_key_set(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    assert hf.configured_providers() == ["gemini"]
    adapter = hf.build_hosted_free_adapter()
    assert adapter is not None


def test_both_keys_configured_respects_order(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    assert hf.configured_providers() == ["groq", "gemini"]


def test_disabled_via_config_is_inert(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    monkeypatch.setattr("hosted_free.hosted_free_enabled", lambda: False)
    assert hf.configured_providers() == []
    assert hf.build_hosted_free_adapter() is None


# ---------------------------------------------------------------------------
# Adapter construction (llm.py GroqAdapter / GeminiAdapter)
# ---------------------------------------------------------------------------

def test_groq_adapter_construction():
    a = GroqAdapter(api_key="gsk-test", model="llama-3.1-8b-instant")
    assert a.backend == "groq"
    assert a.model_key == "llama-3.1-8b-instant"
    assert a._base_url == "https://api.groq.com/openai/v1"
    assert a._max_retries == 0  # fail-fast default — no naive retry ladder


def test_gemini_adapter_construction():
    a = GeminiAdapter(api_key="gm-test", model="gemini-2.0-flash")
    assert a.backend == "gemini"
    assert a.model_key == "gemini-2.0-flash"
    assert "generativelanguage.googleapis.com" in a._base_url
    assert a._max_retries == 0


def test_groq_gemini_not_in_global_backend_order():
    """BACKLOG #25 requirement: never part of the paid backend_order failover."""
    from llm import DEFAULT_BACKEND_ORDER, _KNOWN_BACKENDS
    assert "groq" not in DEFAULT_BACKEND_ORDER
    assert "gemini" not in DEFAULT_BACKEND_ORDER
    assert "groq" not in _KNOWN_BACKENDS
    assert "gemini" not in _KNOWN_BACKENDS


def test_config_driven_model_ids(monkeypatch):
    """Model IDs come from config, not a hardcoded _MODEL_MAP entry (churn-proof)."""
    monkeypatch.setattr("hosted_free._cfg", lambda key, default:
                         "custom-groq-model" if key == "groq_model" else default)
    assert hf.groq_model() == "custom-groq-model"

    monkeypatch.setattr("hosted_free._cfg", lambda key, default:
                         "custom-gemini-model" if key == "gemini_model" else default)
    assert hf.gemini_model() == "custom-gemini-model"


def test_model_id_passthrough_no_hardcoded_map_entry():
    """resolve_model() must pass raw hosted-free model ids through unchanged —
    confirms no _MODEL_MAP["groq"/"gemini"] entry silently overrides config."""
    from llm import resolve_model, _MODEL_MAP
    assert "groq" not in _MODEL_MAP
    assert "gemini" not in _MODEL_MAP
    assert resolve_model("groq", "llama-3.1-8b-instant") == "llama-3.1-8b-instant"
    assert resolve_model("gemini", "gemini-2.0-flash") == "gemini-2.0-flash"


# ---------------------------------------------------------------------------
# Successful mocked completion
# ---------------------------------------------------------------------------

def _ok_response(content="PASS", model="llama-3.1-8b-instant"):
    m = MagicMock()
    m.raise_for_status = lambda: None
    m.json = lambda: {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "model": model,
        "usage": {"prompt_tokens": 12, "completion_tokens": 4},
    }
    return m


def test_successful_completion_via_groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    with patch("requests.post", return_value=_ok_response()) as mock_post:
        adapter = hf.build_hosted_free_adapter()
        resp = adapter.complete([LLMMessage("user", "hi")], max_tokens=10)

    assert isinstance(resp, LLMResponse)
    assert resp.content == "PASS"
    assert resp.backend == "hosted_free:groq"
    assert adapter._active_provider == "groq"
    assert adapter.model_key == "llama-3.1-8b-instant"
    mock_post.assert_called_once()
    assert "api.groq.com" in mock_post.call_args[0][0]


def test_successful_completion_via_gemini(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    with patch("requests.post", return_value=_ok_response(model="gemini-2.0-flash")):
        adapter = hf.build_hosted_free_adapter()
        resp = adapter.complete([LLMMessage("user", "hi")], max_tokens=10)

    assert resp.content == "PASS"
    assert resp.backend == "hosted_free:gemini"


# ---------------------------------------------------------------------------
# 429 -> rate-limit breaker trip path
# ---------------------------------------------------------------------------

def _http_429(retry_after=None):
    m = MagicMock()
    m.status_code = 429
    m.headers = {"Retry-After": retry_after} if retry_after is not None else {}

    def _raise():
        exc = requests.exceptions.HTTPError("429 Client Error: Too Many Requests")
        exc.response = m
        raise exc

    m.raise_for_status = _raise
    return m


def test_429_trips_breaker_and_falls_over_to_next_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    def fake_post(url, headers=None, json=None, timeout=None):
        if "groq" in url:
            return _http_429(retry_after="5")
        return _ok_response(model="gemini-2.0-flash")

    with patch("requests.post", side_effect=fake_post), \
         patch("time.sleep") as mock_sleep:
        adapter = hf.build_hosted_free_adapter()
        resp = adapter.complete([LLMMessage("user", "hi")], max_tokens=10)

    assert resp.backend == "hosted_free:gemini"
    assert not mock_sleep.called  # no naive retry/backoff delay on the 429
    assert hf.rate_limit_tripped("groq") != ""
    assert hf.rate_limit_tripped("gemini") == ""


def test_429_without_retry_after_uses_default_cooldown(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    monkeypatch.setattr("hosted_free.rate_limit_default_cooldown_s", lambda: 42.0)

    with patch("requests.post", return_value=_http_429(retry_after=None)):
        adapter = hf.build_hosted_free_adapter()
        with pytest.raises(Exception):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)

    until = hf._RATE_LIMIT_UNTIL.get("groq", 0.0)
    assert until > 0
    # Cooldown should be roughly the configured default (allow scheduling slack).
    import time as _time
    remaining = until - _time.monotonic()
    assert 35 <= remaining <= 42.5


def test_tripped_provider_is_skipped_without_a_call(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    hf._trip_rate_limit("groq", retry_after=60.0)
    with patch("requests.post") as mock_post:
        adapter = hf.build_hosted_free_adapter()
        with pytest.raises(RuntimeError, match="rate-limited or slow"):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
    mock_post.assert_not_called()


def test_all_providers_tripped_raises_without_network_calls(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    hf._trip_rate_limit("groq", retry_after=30.0)
    hf._trip_rate_limit("gemini", retry_after=30.0)
    with patch("requests.post") as mock_post:
        adapter = hf.build_hosted_free_adapter()
        with pytest.raises(RuntimeError):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
    mock_post.assert_not_called()
    assert hf.available() is False


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------

def test_parse_retry_after_seconds():
    assert hf._parse_retry_after("5") == 5.0
    assert hf._parse_retry_after("0") == 0.0
    assert hf._parse_retry_after(None) is None
    assert hf._parse_retry_after("") is None
    assert hf._parse_retry_after("not-a-number-or-date") is None


def test_parse_retry_after_http_date():
    import datetime
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=30)
    http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    parsed = hf._parse_retry_after(http_date)
    assert parsed is not None
    assert 25 <= parsed <= 31


# ---------------------------------------------------------------------------
# Latency breaker parity with local_models' grace-then-trip semantics
# ---------------------------------------------------------------------------

def test_latency_breaker_grace_on_first_call(monkeypatch):
    monkeypatch.setattr("hosted_free.max_latency_ms", lambda: 100)
    hf.report_latency("groq", 5000)  # first call — grace, should NOT trip
    assert hf.latency_guard_tripped("groq") == ""


def test_latency_breaker_trips_on_second_slow_call(monkeypatch):
    monkeypatch.setattr("hosted_free.max_latency_ms", lambda: 100)
    hf.report_latency("groq", 5000)  # grace
    hf.report_latency("groq", 5000)  # trips
    assert hf.latency_guard_tripped("groq") != ""


def test_latency_breaker_disabled_when_cap_zero(monkeypatch):
    monkeypatch.setattr("hosted_free.max_latency_ms", lambda: 0)
    hf.report_latency("groq", 999999)
    hf.report_latency("groq", 999999)
    assert hf.latency_guard_tripped("groq") == ""


def test_slow_non_http_failure_trips_latency_breaker(monkeypatch):
    """A hung/erroring provider (timeout, connection reset — not a 429) must
    still feed the latency breaker, else nothing ever skips it and every
    subsequent call repeats the same full timeout tax (adversarial-review
    batch-2 finding, architect + minimalist + skeptic lenses all raised it)."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})
    monkeypatch.setattr("hosted_free.max_latency_ms", lambda: 100)

    ticks = iter([0.0, 5.0, 5.0, 10.0])  # two (start, end) pairs, 5s each
    monkeypatch.setattr("hosted_free.time.monotonic", lambda: next(ticks))

    with patch("requests.post", side_effect=requests.exceptions.ConnectionError("boom")) as mock_post:
        adapter = hf.build_hosted_free_adapter()

        with pytest.raises(requests.exceptions.ConnectionError):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
        assert hf.latency_guard_tripped("groq") == ""  # grace on the first slow failure

        with pytest.raises(requests.exceptions.ConnectionError):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
        assert hf.latency_guard_tripped("groq") != ""  # tripped on the second

        # Third call: the breaker skips groq before it ever dials out again.
        with pytest.raises(RuntimeError, match="rate-limited or slow"):
            adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
        assert mock_post.call_count == 2


def test_latency_tripped_provider_falls_over(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test")
    monkeypatch.setattr("hosted_free._load_env", lambda: {})

    hf._LATENCY_TRIPPED["groq"] = "too slow"
    with patch("requests.post", return_value=_ok_response(model="gemini-2.0-flash")) as mock_post:
        adapter = hf.build_hosted_free_adapter()
        resp = adapter.complete([LLMMessage("user", "hi")], max_tokens=10)
    assert resp.backend == "hosted_free:gemini"
    # Only gemini was called — groq was skipped entirely.
    assert mock_post.call_count == 1
    assert "generativelanguage" in mock_post.call_args[0][0]


# ---------------------------------------------------------------------------
# provider_order()
# ---------------------------------------------------------------------------

def test_provider_order_default():
    assert hf.provider_order() == ["groq", "gemini"]


def test_provider_order_custom(monkeypatch):
    monkeypatch.setattr("hosted_free._cfg", lambda key, default:
                         ["gemini", "groq"] if key == "order" else default)
    assert hf.provider_order() == ["gemini", "groq"]


def test_provider_order_drops_unknown(monkeypatch):
    monkeypatch.setattr("hosted_free._cfg", lambda key, default:
                         ["groq", "bogus", "gemini"] if key == "order" else default)
    assert hf.provider_order() == ["groq", "gemini"]
