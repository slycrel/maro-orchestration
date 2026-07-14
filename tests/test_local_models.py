"""Tests for the optional local validator runtime (src/local_models.py).

No live server required — the HTTP layer (`_http_json`) is monkeypatched.
"""
from __future__ import annotations

import sys
import urllib.error
from unittest.mock import MagicMock

import pytest

import local_models as lm
from llm import LLMMessage, FailoverAdapter


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    lm.reset_cache()
    lm._MANAGED["proc"] = None
    yield
    lm._MANAGED["proc"] = None
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


# --- per-model local_max_tokens (BACKLOG #10) --------------------------------

def test_local_max_tokens_for_global_int_backward_compat(monkeypatch):
    """A bare int applies to every model — the pre-2026-07-13 behavior."""
    _set_cfg(monkeypatch, local_max_tokens=1024)
    assert lm.local_max_tokens_for("llama3.2:3b") == 1024
    assert lm.local_max_tokens_for("some-other-model") == 1024


def test_local_max_tokens_for_default_when_unconfigured(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.local_max_tokens_for("anything") == 2048


def test_local_max_tokens_for_measured_builtin_when_unconfigured(monkeypatch):
    """No config override at all → the 3 models measured in the 2026-07-13
    sweep (BACKLOG #10) get their empirically-tuned floor (256), not the
    generous 2048 safety net reserved for unmeasured/reasoning models."""
    _set_cfg(monkeypatch)
    assert lm.local_max_tokens_for("llama3.2:3b") == 256
    assert lm.local_max_tokens_for("qwen-hermes:latest") == 256
    assert lm.local_max_tokens_for("qwen2.5-coder:3b") == 256
    assert lm.local_max_tokens_for("mlx-community/VibeThinker-3B-8bit") == 2048


def test_local_max_tokens_for_explicit_scalar_overrides_measured_builtin(monkeypatch):
    """An explicit global scalar config always wins over the built-in table,
    even for a model with a measured floor."""
    _set_cfg(monkeypatch, local_max_tokens=4096)
    assert lm.local_max_tokens_for("llama3.2:3b") == 4096


def test_local_max_tokens_for_dict_default_key_overrides_measured_builtin(monkeypatch):
    """A dict's own "default" key is explicit config too — it wins over the
    built-in measured table for any model not itself listed in the dict."""
    _set_cfg(monkeypatch, local_max_tokens={"some-other-model": 999, "default": 4096})
    assert lm.local_max_tokens_for("llama3.2:3b") == 4096


def test_local_max_tokens_for_dict_per_model(monkeypatch):
    _set_cfg(monkeypatch, local_max_tokens={
        "llama3.2:3b": 256, "qwen2.5-coder:3b": 256, "default": 2048,
    })
    assert lm.local_max_tokens_for("llama3.2:3b") == 256
    assert lm.local_max_tokens_for("qwen2.5-coder:3b") == 256


def test_local_max_tokens_for_dict_falls_back_to_dict_default(monkeypatch):
    """A model not explicitly listed in the dict uses the dict's own "default"
    key — this is how a newly-installed (unmeasured) model stays safe without
    needing a config edit first."""
    _set_cfg(monkeypatch, local_max_tokens={"qwen2.5-coder:3b": 256, "default": 1500})
    assert lm.local_max_tokens_for("brand-new-reasoning-model") == 1500


def test_local_max_tokens_for_dict_no_default_key_uses_module_default(monkeypatch):
    _set_cfg(monkeypatch, local_max_tokens={"qwen2.5-coder:3b": 256})
    assert lm.local_max_tokens_for("unlisted-model") == 2048


def test_local_max_tokens_for_coerces_bad_values(monkeypatch):
    _set_cfg(monkeypatch, local_max_tokens={"m1": "not-a-number", "default": 2048})
    assert lm.local_max_tokens_for("m1") == 2048
    _set_cfg(monkeypatch, local_max_tokens={"m1": -5, "default": 2048})
    assert lm.local_max_tokens_for("m1") == 2048  # non-positive rejected
    _set_cfg(monkeypatch, local_max_tokens={"m1": 0, "default": 2048})
    assert lm.local_max_tokens_for("m1") == 2048
    _set_cfg(monkeypatch, local_max_tokens="not-a-number")
    assert lm.local_max_tokens_for("m1") == 2048  # scalar parse error → module default


def test_local_max_tokens_for_string_int_coerced(monkeypatch):
    _set_cfg(monkeypatch, local_max_tokens="512")
    assert lm.local_max_tokens_for("m1") == 512
    _set_cfg(monkeypatch, local_max_tokens={"m1": "512", "default": 2048})
    assert lm.local_max_tokens_for("m1") == 512


def test_adapter_resolves_min_tokens_per_model_when_not_overridden(monkeypatch):
    """LocalValidatorAdapter with no explicit min_tokens uses the per-model
    config resolver, not a single global floor."""
    _set_cfg(monkeypatch, local_max_tokens={"fast-model": 256, "default": 2048})
    a_fast = lm.LocalValidatorAdapter("fast-model", endpoint="http://x/v1", runtime="ollama")
    a_other = lm.LocalValidatorAdapter("other-model", endpoint="http://x/v1", runtime="ollama")
    assert a_fast._min_tokens == 256
    assert a_other._min_tokens == 2048


def test_adapter_explicit_min_tokens_still_overrides_per_model_config(monkeypatch):
    """An explicit min_tokens kwarg (used throughout the existing test suite)
    must keep bypassing config entirely, dict or not."""
    _set_cfg(monkeypatch, local_max_tokens={"m": 256, "default": 2048})
    a = lm.LocalValidatorAdapter("m", endpoint="http://x/v1", runtime="ollama", min_tokens=999)
    assert a._min_tokens == 999


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


def test_validator_available_picks_up_lazy_spinup(monkeypatch):
    """A negative probe must NOT be cached: if the model is down at first check
    then spun up mid-process, validator_available() must flip to True without a
    reset. (build_local_validator_adapter already re-probes per call; this keeps
    auto_verify consistent with it instead of frozen OFF for the whole run.)"""
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama")
    state = {"up": False}
    monkeypatch.setattr(lm, "loaded_models",
                        lambda ep=None: (["m1"] if state["up"] else []))
    assert lm.validator_available() is False      # model down at first probe
    state["up"] = True                            # spin-up happens mid-process
    assert lm.validator_available() is True        # re-probed, not cached-negative


def test_validator_available_caches_positive(monkeypatch):
    """A positive IS cached — once loaded, stays loaded for the session, so we
    don't re-probe the endpoint on every step."""
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama")
    probes = {"n": 0}

    def _probe(ep=None):
        probes["n"] += 1
        return ["m1"]

    monkeypatch.setattr(lm, "loaded_models", _probe)
    assert lm.validator_available() is True
    assert lm.validator_available() is True
    assert probes["n"] == 1                        # second call served from cache


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


def test_input_char_budget_default_and_floor(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.input_char_budget() == 6000           # default, larger than paid 1200
    _set_cfg(monkeypatch, max_input_chars=500)
    assert lm.input_char_budget() == 1200           # never below the paid default
    _set_cfg(monkeypatch, max_input_chars=20000)
    assert lm.input_char_budget() == 20000
    _set_cfg(monkeypatch, max_input_chars="oops")
    assert lm.input_char_budget() == 6000           # parse error → default


# --- orchestration-managed lifecycle ---------------------------------------

def test_lifecycle_accessors(monkeypatch):
    _set_cfg(monkeypatch, idle_shutdown_secs=120, autostart=False)
    assert lm.idle_shutdown_secs() == 120
    assert lm.autostart_enabled() is False
    _set_cfg(monkeypatch, autostart="off")
    assert lm.autostart_enabled() is False
    assert lm._port_from_endpoint("http://127.0.0.1:8099/v1") == 8099
    assert lm._port_from_endpoint("http://h/v1") == 8088  # fallback


def test_ensure_unconfigured_no_spawn(monkeypatch):
    _set_cfg(monkeypatch)
    pop = MagicMock(); monkeypatch.setattr(lm.subprocess, "Popen", pop)
    assert lm.ensure_validator_running() is False
    pop.assert_not_called()


def test_ensure_reuses_running_server(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="mlx")
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"])
    pop = MagicMock(); monkeypatch.setattr(lm.subprocess, "Popen", pop)
    assert lm.ensure_validator_running() is True
    pop.assert_not_called()  # reuse, never duplicate


def test_ensure_noop_when_autostart_disabled(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="mlx", autostart=False)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])
    pop = MagicMock(); monkeypatch.setattr(lm.subprocess, "Popen", pop)
    assert lm.ensure_validator_running() is False
    pop.assert_not_called()


def test_ensure_manages_ollama_runtime(monkeypatch):
    # Ollama is now orchestration-managed: spun up via `ollama serve`, capped.
    monkeypatch.delenv("MARO_PYTEST_ACTIVE", raising=False)  # exercise the real spawn path
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama",
             autostart=True, idle_shutdown_secs=0)
    monkeypatch.setattr(lm.shutil, "which", lambda exe: f"/usr/bin/{exe}", raising=False)
    state = {"spawned": False, "argv": None, "env": None}

    def fake_popen(argv, *a, **k):
        state["spawned"] = True
        state["argv"] = argv
        state["env"] = k.get("env")
        p = MagicMock(); p.poll.return_value = None; p.pid = 5151
        return p

    monkeypatch.setattr(lm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"] if state["spawned"] else [])
    monkeypatch.setattr(lm, "_terminate_group", lambda proc, timeout=10.0: None)
    try:
        assert lm.ensure_validator_running(wait_secs=5) is True
        assert state["spawned"] is True
        # `ollama serve` is the tail of the argv (a CPU-cap prefix may precede it).
        assert state["argv"][-2:] == ["/usr/bin/ollama", "serve"]
        assert state["env"]["OLLAMA_NUM_PARALLEL"] == "1"
    finally:
        lm.shutdown_validator()


def test_ensure_no_real_spawn_under_pytest(monkeypatch):
    # The test-harness guard: even with autostart + a managed runtime, no real
    # server is spawned while MARO_PYTEST_ACTIVE is set (set by conftest).
    monkeypatch.setenv("MARO_PYTEST_ACTIVE", "1")
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", autostart=True)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])
    pop = MagicMock(); monkeypatch.setattr(lm.subprocess, "Popen", pop)
    assert lm.ensure_validator_running() is False
    pop.assert_not_called()


def test_ensure_ollama_missing_binary_falls_back(monkeypatch):
    monkeypatch.delenv("MARO_PYTEST_ACTIVE", raising=False)
    _set_cfg(monkeypatch, local_models=["m1"], runtime="ollama", autostart=True)
    monkeypatch.setattr(lm.shutil, "which", lambda exe: None, raising=False)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])
    pop = MagicMock(); monkeypatch.setattr(lm.subprocess, "Popen", pop)
    assert lm.ensure_validator_running() is False
    pop.assert_not_called()


def test_cpu_cap_prefix_linux(monkeypatch):
    monkeypatch.setattr(lm.os, "cpu_count", lambda: 4)
    _set_cfg(monkeypatch, cpu_affinity="2,3", cpu_nice=10)
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.shutil, "which", lambda exe: f"/usr/bin/{exe}", raising=False)
    assert lm._cpu_cap_prefix() == ["nice", "-n", "10", "taskset", "-c", "2,3"]


def test_default_cpu_affinity_derives_from_cpu_count(monkeypatch):
    cases = {1: "", 2: "", 3: "2-2", 4: "2-3", 8: "4-7", 16: "8-15", 64: "32-63"}
    for n, expected in cases.items():
        monkeypatch.setattr(lm.os, "cpu_count", lambda n=n: n)
        assert lm._default_cpu_affinity() == expected, n


def test_cpu_affinity_uses_derived_default_when_unset(monkeypatch):
    # No explicit config → portable derived default, normalized to a list.
    monkeypatch.setattr(lm.os, "cpu_count", lambda: 8)
    _set_cfg(monkeypatch)  # no cpu_affinity key
    assert lm.cpu_affinity() == "4,5,6,7"


def test_cpu_affinity_clamps_out_of_range_cores(monkeypatch):
    # A borrowed/stale config naming cores this box lacks must not make taskset
    # fail — out-of-range cores are dropped (here only 0,1 exist).
    monkeypatch.setattr(lm.os, "cpu_count", lambda: 2)
    _set_cfg(monkeypatch, cpu_affinity="2,3")
    assert lm.cpu_affinity() == ""  # 2,3 don't exist → no pin, falls to nice-only
    _set_cfg(monkeypatch, cpu_affinity="0,1,2,3")
    assert lm.cpu_affinity() == "0,1"


def test_cpu_cap_prefix_noop_off_linux(monkeypatch):
    _set_cfg(monkeypatch)
    monkeypatch.setattr(lm.platform, "system", lambda: "Darwin")
    assert lm._cpu_cap_prefix() == []


def test_cpu_cap_prefix_empty_affinity_skips_taskset(monkeypatch):
    _set_cfg(monkeypatch, cpu_affinity="", cpu_nice=0)
    monkeypatch.setattr(lm.platform, "system", lambda: "Linux")
    monkeypatch.setattr(lm.shutil, "which", lambda exe: f"/usr/bin/{exe}", raising=False)
    assert lm._cpu_cap_prefix() == []


def test_launch_argv_env_ollama_and_unknown(monkeypatch):
    _set_cfg(monkeypatch, ollama_keep_alive="30s")
    monkeypatch.setattr(lm.shutil, "which", lambda exe: "/usr/bin/ollama", raising=False)
    argv, env = lm._launch_argv_env("ollama", "m1", "http://127.0.0.1:11434/v1")
    assert argv == ["/usr/bin/ollama", "serve"]
    assert env["OLLAMA_KEEP_ALIVE"] == "30s"
    assert env["OLLAMA_MAX_LOADED_MODELS"] == "1"
    assert lm._launch_argv_env("bogus", "m1", "http://x")[0] is None


def test_ensure_spawns_and_waits_until_ready(monkeypatch):
    monkeypatch.delenv("MARO_PYTEST_ACTIVE", raising=False)
    _set_cfg(monkeypatch, local_models=["m1"], runtime="mlx", autostart=True, idle_shutdown_secs=0)
    monkeypatch.setattr(lm, "mlx_python", lambda: sys.executable)  # exists
    state = {"spawned": False}

    def fake_popen(*a, **k):
        state["spawned"] = True
        p = MagicMock(); p.poll.return_value = None; p.pid = 4242
        return p

    monkeypatch.setattr(lm.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: ["m1"] if state["spawned"] else [])
    try:
        assert lm.ensure_validator_running(wait_secs=5) is True
        assert state["spawned"] is True
    finally:
        lm.shutdown_validator()


def test_ensure_returns_false_if_server_exits_during_startup(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="mlx", autostart=True, idle_shutdown_secs=0)
    monkeypatch.setattr(lm, "mlx_python", lambda: sys.executable)
    monkeypatch.setattr(lm, "loaded_models", lambda ep=None: [])

    def fake_popen(*a, **k):
        p = MagicMock(); p.poll.return_value = 1; p.returncode = 1; p.pid = 7  # already dead
        return p

    monkeypatch.setattr(lm.subprocess, "Popen", fake_popen)
    assert lm.ensure_validator_running(wait_secs=2) is False
    assert lm._MANAGED["proc"] is None


def test_shutdown_terminates_managed_proc(monkeypatch):
    """_terminate_group() prefers os.killpg(pgid, ...) over proc.terminate()
    whenever the process's group can be resolved, and only falls back to
    proc.terminate() when os.getpgid() raises (see src/local_models.py's
    _terminate_group). A hardcoded p.pid=999 doesn't reliably trigger that
    raise: PID allocation is OS/machine-specific, and 999 happens to be a
    live process group on some machines (observed on macOS — this test used
    to send a real SIGTERM to whatever occupies that group). Mock
    os.getpgid to always raise ProcessLookupError so this test deterministically
    exercises the proc.terminate() fallback path regardless of what's
    actually running on the host, on any OS."""
    monkeypatch.setattr(
        lm.os, "getpgid",
        lambda pid: (_ for _ in ()).throw(ProcessLookupError(pid)),
    )
    p = MagicMock(); p.poll.return_value = None; p.pid = 999
    lm._MANAGED["proc"] = p
    lm.shutdown_validator()
    p.terminate.assert_called_once()
    assert lm._MANAGED["proc"] is None


def test_shutdown_is_noop_when_external():
    lm._MANAGED["proc"] = None
    lm.shutdown_validator()  # must not raise
    assert lm._MANAGED["proc"] is None


# --- run-scoped lifecycle (managed_for_run) --------------------------------

def test_auto_verify_configured_ignores_availability(monkeypatch):
    _set_cfg(monkeypatch, auto_verify=True)
    monkeypatch.setattr(lm, "validator_available", lambda: False)
    assert lm.auto_verify_configured() is True          # config flag only
    assert lm.auto_verify_enabled() is False             # config on, not available
    _set_cfg(monkeypatch, auto_verify=False)
    assert lm.auto_verify_configured() is False


def test_managed_for_run_noop_when_unconfigured(monkeypatch):
    _set_cfg(monkeypatch)
    ens = MagicMock(); sd = MagicMock()
    monkeypatch.setattr(lm, "ensure_validator_running", ens)
    monkeypatch.setattr(lm, "shutdown_validator", sd)
    with lm.managed_for_run("verify: x"):
        pass
    ens.assert_not_called(); sd.assert_not_called()


def test_managed_for_run_spawns_then_tears_down(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], runtime="mlx", autostart=True, auto_verify=True)
    monkeypatch.setattr(lm, "validator_available", lambda: False)

    def fake_ensure(**k):
        lm._MANAGED["proc"] = MagicMock()   # simulate a spawn
        return True

    monkeypatch.setattr(lm, "ensure_validator_running", fake_ensure)
    sd = MagicMock(); monkeypatch.setattr(lm, "shutdown_validator", sd)
    with lm.managed_for_run("research x", ralph_verify=False):
        pass
    sd.assert_called_once()                  # the spawner reaps


def test_managed_for_run_reuses_external_leaves_it_running(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], autostart=True, auto_verify=True)
    monkeypatch.setattr(lm, "validator_available", lambda: True)  # already up (external)
    ens = MagicMock(); sd = MagicMock()
    monkeypatch.setattr(lm, "ensure_validator_running", ens)
    monkeypatch.setattr(lm, "shutdown_validator", sd)
    with lm.managed_for_run("verify: x"):
        pass
    ens.assert_not_called(); sd.assert_not_called()   # reuse, never reap external


def test_managed_for_run_tears_down_on_exception(monkeypatch):
    _set_cfg(monkeypatch, local_models=["m1"], autostart=True, auto_verify=True)
    monkeypatch.setattr(lm, "validator_available", lambda: False)
    monkeypatch.setattr(lm, "ensure_validator_running",
                        lambda **k: lm._MANAGED.__setitem__("proc", MagicMock()) or True)
    sd = MagicMock(); monkeypatch.setattr(lm, "shutdown_validator", sd)
    with pytest.raises(ValueError):
        with lm.managed_for_run("research x"):
            raise ValueError("boom")
    sd.assert_called_once()                  # finally reaped despite failure


def test_managed_for_run_skips_when_validation_not_wanted(monkeypatch):
    # configured + autostart but auto_verify off and no verify: prefix → don't spin up
    _set_cfg(monkeypatch, local_models=["m1"], autostart=True, auto_verify=False)
    monkeypatch.setattr(lm, "validator_available", lambda: False)
    ens = MagicMock(); monkeypatch.setattr(lm, "ensure_validator_running", ens)
    with lm.managed_for_run("plain goal, no verify prefix"):
        pass
    ens.assert_not_called()


# --- latency breaker (2026-07-10 envelope arc) -------------------------------

def test_latency_breaker_trips_over_cap(monkeypatch):
    """454s of a 1671s run went to local verdicts averaging 41s each (ROI:
    ~$0.64 saved lifetime). A warm call over the cap must switch the
    process to the paid tier."""
    _set_cfg(monkeypatch, local_max_latency_ms=15000)
    assert lm.latency_guard_tripped() == ""
    lm.report_latency(14999)
    assert lm.latency_guard_tripped() == ""
    lm.report_latency(34460)  # the live specimen (warm — call #2)
    assert "34460ms" in lm.latency_guard_tripped()
    # Further reports don't overwrite the original reason
    lm.report_latency(99999)
    assert "34460ms" in lm.latency_guard_tripped()


def test_latency_breaker_cold_load_grace(monkeypatch):
    """The first measured call carries the model's cold load (~25-30s on
    this box; keep_alive shorter than the validation cadence made EVERY
    call pay it — 18/18 across the two Manti runs). Cold call #1 must NOT
    trip; a warm over-cap call after it must."""
    _set_cfg(monkeypatch, local_max_latency_ms=15000)
    lm.report_latency(38000)  # cold first call — grace
    assert lm.latency_guard_tripped() == ""
    lm.report_latency(12000)  # healthy warm call
    assert lm.latency_guard_tripped() == ""
    lm.report_latency(47000)  # warm and still slow — trip
    assert "47000ms" in lm.latency_guard_tripped()


def test_latency_breaker_disabled_by_zero_cap(monkeypatch):
    _set_cfg(monkeypatch, local_max_latency_ms=0)
    lm.report_latency(120000)
    lm.report_latency(120000)
    assert lm.latency_guard_tripped() == ""


def test_latency_breaker_reset_with_cache(monkeypatch):
    """reset_cache() re-arms the breaker AND the cold-load grace — each
    process re-probes, so a faster box or smaller model self-heals without
    config surgery."""
    _set_cfg(monkeypatch, local_max_latency_ms=15000)
    lm.report_latency(50000)
    lm.report_latency(50000)
    assert lm.latency_guard_tripped()
    lm.reset_cache()
    assert lm.latency_guard_tripped() == ""
    lm.report_latency(50000)  # first call after reset → grace again
    assert lm.latency_guard_tripped() == ""


def test_max_latency_ms_default_and_parse_error(monkeypatch):
    _set_cfg(monkeypatch)
    assert lm.max_latency_ms() == 15000
    _set_cfg(monkeypatch, local_max_latency_ms="nope")
    assert lm.max_latency_ms() == 15000
    _set_cfg(monkeypatch, local_max_latency_ms=-5)
    assert lm.max_latency_ms() == 0


def test_tripped_breaker_skips_local_tier_in_verify_step(monkeypatch):
    """step_exec.verify_step must not touch the local endpoint once the
    breaker is tripped — straight to the paid adapter."""
    from unittest.mock import MagicMock
    import step_exec

    _set_cfg(monkeypatch, local_models=["qwen2.5-coder:3b"],
             local_max_latency_ms=15000)
    lm.report_latency(40000)  # cold-load grace slot
    lm.report_latency(40000)  # warm and slow — trips

    ensure = MagicMock()
    monkeypatch.setattr(lm, "ensure_validator_running", ensure)
    build = MagicMock()
    monkeypatch.setattr(lm, "build_local_validator_adapter", build)

    paid = MagicMock()
    verdict = MagicMock(passed=True, reason="ok", confidence=0.9)
    monkeypatch.setattr(
        "verification_agent.VerificationAgent.verify_step",
        lambda self, *a, **k: verdict,
    )
    out = step_exec.verify_step("step", "result", paid)
    assert out["passed"] is True
    ensure.assert_not_called()
    build.assert_not_called()


def test_verify_step_reports_local_latency(monkeypatch):
    """The single-call ladder site owns latency reporting: a slow local
    verdict must trip the breaker for subsequent steps in the process."""
    from unittest.mock import MagicMock
    import step_exec

    _set_cfg(monkeypatch, local_models=["qwen2.5-coder:3b"],
             local_max_latency_ms=15000, min_certainty=0.6)
    lm.report_latency(1000)  # consume the cold-load grace slot
    local_adapter = MagicMock(model_key="qwen2.5-coder:3b")
    monkeypatch.setattr(lm, "ensure_validator_running", MagicMock())
    monkeypatch.setattr(lm, "build_local_validator_adapter",
                        lambda *a, **k: local_adapter)

    verdict = MagicMock(passed=True, reason="ok", confidence=0.95)
    monkeypatch.setattr(
        "verification_agent.VerificationAgent.verify_step",
        lambda self, *a, **k: verdict,
    )
    # Simulate a slow local call: monotonic advances 40s across the call
    ticks = iter([0.0, 40.0, 100.0, 100.1, 200.0, 200.1])
    monkeypatch.setattr(step_exec.time, "monotonic", lambda: next(ticks))

    out = step_exec.verify_step("step", "result", MagicMock())
    assert out["source"] == "qwen2.5-coder:3b"  # first call still used local
    assert lm.latency_guard_tripped()           # ...and tripped the breaker


def test_local_retry_uses_same_threshold_as_decisive_gate(monkeypatch):
    """A RETRY that clears min_certainty must never cross the local gate as PASS."""
    import step_exec

    _set_cfg(monkeypatch, local_models=["local-test"], min_certainty=0.6)
    response = MagicMock()
    response.content = '{"verdict":"RETRY","reason":"incomplete","confidence":0.65}'
    local_adapter = MagicMock(model_key="local-test")
    local_adapter.complete.return_value = response
    monkeypatch.setattr(lm, "ensure_validator_running", MagicMock())
    monkeypatch.setattr(lm, "build_local_validator_adapter", lambda: local_adapter)

    paid_adapter = MagicMock()
    out = step_exec.verify_step("finish the work", "partial result", paid_adapter)

    assert out["passed"] is False
    assert out["decision"] == "LOCAL_FAIL"
    assert out["confidence"] == pytest.approx(0.65)
    paid_adapter.complete.assert_not_called()
