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


def test_shutdown_terminates_managed_proc():
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
