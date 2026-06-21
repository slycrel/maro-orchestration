"""Tests for the optional local validator runtime (src/local_models.py).

No live server required — the HTTP layer (`_http_json`) is monkeypatched.
"""
from __future__ import annotations

import urllib.error

import pytest

import local_models as lm
from llm import LLMMessage, FailoverAdapter


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lm.reset_cache()
    yield
    lm.reset_cache()


def _set_cfg(monkeypatch, **vals):
    monkeypatch.setattr(lm, "_cfg", lambda key, default: vals.get(key, default))


# --- config accessors -------------------------------------------------------

def test_configured_models_empty_default(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.configured_models() == []


def test_configured_models_string_coerced_to_list(monkeypatch):
    _set_cfg(monkeypatch, local_models="modelA")
    assert lm.configured_models() == ["modelA"]


def test_configured_models_filters_blanks(monkeypatch):
    _set_cfg(monkeypatch, local_models=["a", "", "  ", "b"])
    assert lm.configured_models() == ["a", "b"]


def test_min_certainty_clamped(monkeypatch):
    _set_cfg(monkeypatch, min_certainty=5)
    assert lm.min_certainty() == 1.0
    _set_cfg(monkeypatch, min_certainty="nope")
    assert lm.min_certainty() == 0.6  # default on parse error


def test_resolve_runtime_explicit(monkeypatch):
    _set_cfg(monkeypatch, runtime="ollama")
    assert lm.resolve_runtime() == "ollama"


def test_resolve_runtime_auto_apple_silicon(monkeypatch):
    _set_cfg(monkeypatch, runtime="auto")
    monkeypatch.setattr(lm.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(lm.platform, "machine", lambda: "arm64")
    assert lm.resolve_runtime() == "mlx"


def test_resolve_runtime_auto_linux(monkeypatch):
    _set_cfg(monkeypatch, runtime="auto")
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.platform, "machine", lambda: "x86_64")
    assert lm.resolve_runtime() == "ollama"


def test_resolve_endpoint_override_wins(monkeypatch):
    _set_cfg(monkeypatch, endpoint="http://host:9999/v1/")
    monkeypatch.delenv("LOCAL_VALIDATOR_ENDPOINT", raising=False)
    assert lm.resolve_endpoint() == "http://host:9999/v1"


def test_resolve_endpoint_runtime_default(monkeypatch):
    _set_cfg(monkeypatch, runtime="ollama")
    monkeypatch.delenv("LOCAL_VALIDATOR_ENDPOINT", raising=False)
    assert lm.resolve_endpoint() == "http://127.0.0.1:11434/v1"


# --- detection --------------------------------------------------------------

def test_loaded_models_parses_openai_schema(monkeypatch):
    monkeypatch.setattr(lm, "_http_json",
                        lambda *a, **k: {"data": [{"id": "m1"}, {"id": "m2"}, {"id": ""}]})
    assert lm.loaded_models("http://x/v1") == ["m1", "m2"]


def test_loaded_models_unreachable_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")
    monkeypatch.setattr(lm, "_http_json", boom)
    assert lm.loaded_models("http://x/v1") == []
    assert lm.endpoint_available("http://x/v1") is False


# --- adapter ----------------------------------------------------------------

def _mock_chat(monkeypatch, message: dict, usage: dict | None = None, capture: dict | None = None):
    def fake(method, url, payload, timeout):
        if capture is not None:
            capture["payload"] = payload
            capture["url"] = url
        return {"choices": [{"message": message, "finish_reason": "stop"}],
                "usage": usage or {"prompt_tokens": 5, "completion_tokens": 7}}
    monkeypatch.setattr(lm, "_http_json", fake)


def test_adapter_complete_parses_content(monkeypatch):
    _mock_chat(monkeypatch, {"role": "assistant", "content": '{"verdict":"PASS"}'})
    a = lm.LocalValidatorAdapter("m", endpoint="http://x/v1", runtime="ollama", min_tokens=128)
    r = a.complete([LLMMessage("user", "hi")])
    assert r.content == '{"verdict":"PASS"}'
    assert r.backend == "ollama" and r.input_tokens == 5 and r.output_tokens == 7


def test_adapter_reasoning_fallback_when_content_empty(monkeypatch):
    # Reasoning models can leave content="" and put the trace (with trailing JSON) in `reasoning`.
    _mock_chat(monkeypatch, {"role": "assistant", "content": "",
                             "reasoning": 'thinking... {"verdict":"FAIL"}'})
    a = lm.LocalValidatorAdapter("m", endpoint="http://x/v1", runtime="mlx")
    r = a.complete([LLMMessage("user", "hi")])
    assert r.content.endswith('{"verdict":"FAIL"}')


def test_adapter_enforces_token_floor(monkeypatch):
    cap: dict = {}
    _mock_chat(monkeypatch, {"content": "{}"}, capture=cap)
    a = lm.LocalValidatorAdapter("m", endpoint="http://x/v1", runtime="mlx", min_tokens=1024)
    a.complete([LLMMessage("user", "hi")], max_tokens=128)  # caller asks for 128
    assert cap["payload"]["max_tokens"] == 1024  # floored up so a reasoner can finish


def test_adapter_dead_endpoint_raises_failover_eligible(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(lm, "_http_json", boom)
    a = lm.LocalValidatorAdapter("m", endpoint="http://127.0.0.1:9/v1", runtime="mlx")
    with pytest.raises(RuntimeError) as ei:
        a.complete([LLMMessage("user", "hi")])
    assert "unavailable" in str(ei.value).lower()


# --- builder ----------------------------------------------------------------

def test_build_returns_none_when_unconfigured(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.build_local_validator_adapter() is None


def test_build_returns_none_when_models_not_loaded(monkeypatch):
    _set_cfg(monkeypatch, local_models=["ghost"], runtime="ollama")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])
    assert lm.build_local_validator_adapter() is None


def test_build_single_model_returns_bare_adapter(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1", "other"])
    a = lm.build_local_validator_adapter()
    assert isinstance(a, lm.LocalValidatorAdapter) and a.model_key == "m1"


def test_build_multi_model_wraps_in_failover_with_fallback(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1", "m2"], runtime="ollama")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1", "m2"])

    class Paid:
        backend = "paid"
        model_key = "cheap"
        def complete(self, *a, **k): ...

    fb = Paid()
    a = lm.build_local_validator_adapter(fallback=fb)
    assert isinstance(a, FailoverAdapter)
    assert a._adapters[-1] is fb and len(a._adapters) == 3  # m1, m2, paid


# --- auto-verify gating -----------------------------------------------------

def test_validator_available_false_when_unconfigured(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.validator_available() is False


def test_validator_available_true_when_loaded(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"])
    assert lm.validator_available() is True


def test_validator_available_false_when_not_loaded(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["other"])
    assert lm.validator_available() is False


def test_auto_verify_follows_availability(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", auto_verify=True)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"])
    assert lm.auto_verify_enabled() is True


def test_auto_verify_opt_out(monkeypatch):
    # configured + available, but explicitly disabled
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", auto_verify=False)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"])
    assert lm.auto_verify_enabled() is False
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", auto_verify="off")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"])
    assert lm.auto_verify_enabled() is False


def test_auto_verify_false_when_unavailable_even_if_configured(monkeypatch):
    # models listed in config but none loaded → don't silently verify on paid
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", auto_verify=True)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])
    assert lm.auto_verify_enabled() is False
