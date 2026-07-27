"""Mid-step token brake — the structural half of the tire-runs fetch fix.

Run 3 step 4 (docs/history/2026-07-27-tire-runs-examination.md) burned 2.14M
input tokens inside ONE `claude -p` call. Everything shipped on
`token-lean-fetch` before this was instruction; these tests pin the two
enforcement points maro actually controls at the substrate boundary:

  1. The CLI-side per-tool-call ingest cap (BASH_MAX_OUTPUT_LENGTH handed to
     the subprocess env — the CLI persists oversized Bash output to a file
     and puts only a capped slice in the model's context; behavior verified
     live on Claude Code 2.1.220, 2026-07-27).
  2. The per-call accumulation brake (stream-side probe that kills the
     subprocess once uncached ingest crosses a ceiling, surfacing a typed
     TokenRunawayError -> error_class "token_runaway" -> blocked STEP, run
     continues).

Also pins the stream-json usage-dedupe fix: the CLI emits one assistant
event per content block, all carrying the same message id and usage, so
per-event accounting double-counted (verified live, same session).
"""

import json
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from llm import (
    ClaudeSubprocessAdapter,
    LLMAdapter,
    LLMMessage,
    _BASH_OUTPUT_CAP_DEFAULT,
    _CONTAINER_ENV_PASSTHROUGH,
    _FRESH_INPUT_CEILING_DEFAULT,
    _bash_output_cap_env,
    _build_step_token_brake,
    _build_stream_cost_probe,
    _build_stream_probes,
    _run_subprocess_safe,
    arm_cost_meter,
    cost_meter_state,
)
from llm_errors import (
    TOKEN_RUNAWAY,
    BudgetRunawayError,
    TokenRunawayError,
    classify_error,
)

FAST_POLL = 0.02


def _assistant_event(fresh=0, cache_creation=0, cache_read=0, out=10,
                     mid=None, model="claude-sonnet-4-6"):
    msg = {
        "model": model,
        "usage": {
            "input_tokens": fresh,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": out,
        },
    }
    if mid is not None:
        msg["id"] = mid
    return {"type": "assistant", "message": msg}


@pytest.fixture
def _clean_brake_env(monkeypatch):
    monkeypatch.delenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", raising=False)
    monkeypatch.delenv("MARO_BASH_MAX_OUTPUT_CHARS", raising=False)
    monkeypatch.delenv("BASH_MAX_OUTPUT_LENGTH", raising=False)


# ---------------------------------------------------------------------------
# Brake unit behavior
# ---------------------------------------------------------------------------

class TestBrakeUnit:
    def test_armed_by_default(self, _clean_brake_env):
        # Always-on: unlike the cost probe, no meter needs to be armed.
        assert _build_step_token_brake("sonnet") is not None

    def test_env_zero_disables(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "0")
        assert _build_step_token_brake("sonnet") is None

    def test_under_ceiling_returns_none(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        assert probe([_assistant_event(fresh=999, mid="m1")]) is None

    def test_crossing_ceiling_returns_typed_error(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        exc = probe([_assistant_event(fresh=600, mid="m1"),
                     _assistant_event(fresh=600, mid="m2")])
        assert isinstance(exc, TokenRunawayError)
        assert exc.fresh_input_tokens == 1200
        assert exc.ceiling_tokens == 1000

    def test_cache_creation_counts_as_ingest(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        # The blowup shape: pages enter context as cache_creation, not input.
        exc = probe([_assistant_event(fresh=10, cache_creation=1500, mid="m1")])
        assert isinstance(exc, TokenRunawayError)

    def test_cache_reads_do_not_count(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        # A long legitimate step re-reads its transcript every turn — cheap
        # (0.1x) and NOT new ingest. Must never trip the brake.
        events = [_assistant_event(fresh=10, cache_read=10_000_000, mid=f"m{i}")
                  for i in range(20)]
        assert probe(events) is None

    def test_dedupes_by_message_id(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        # One API message = one thinking event + one tool_use event, same id
        # and usage (live CLI 2.1.220 shape). 600 must count once, not twice.
        same = [_assistant_event(fresh=600, mid="msg_x"),
                _assistant_event(fresh=600, mid="msg_x")]
        assert probe(same) is None
        # A genuinely new message id crosses.
        exc = probe([_assistant_event(fresh=600, mid="msg_y")])
        assert isinstance(exc, TokenRunawayError)

    def test_idless_events_accumulate(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        probe = _build_step_token_brake("claude-sonnet-4-6")
        assert probe([_assistant_event(fresh=600)]) is None
        exc = probe([_assistant_event(fresh=600)])
        assert isinstance(exc, TokenRunawayError)

    def test_kill_accrues_estimate_into_armed_meter(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        disarm = arm_cost_meter(100.0)
        try:
            probe = _build_step_token_brake("claude-sonnet-4-6")
            exc = probe([_assistant_event(fresh=2000, out=500, mid="m1")])
            assert isinstance(exc, TokenRunawayError)
            assert cost_meter_state()["spent_usd"] > 0.0
        finally:
            disarm()

    def test_default_ceiling_is_generous(self, _clean_brake_env):
        # The default must not strangle a healthy heavy step: 200K fresh is
        # the existing decomposition_too_broad DIAGNOSTIC watermark, and the
        # brake sits above it.
        assert _FRESH_INPUT_CEILING_DEFAULT > 200_000


# ---------------------------------------------------------------------------
# Probe composition
# ---------------------------------------------------------------------------

class TestProbeComposition:
    def test_non_agentic_without_meter_is_none(self, _clean_brake_env):
        assert _build_stream_probes("sonnet", agentic=False) is None

    def test_agentic_is_armed_without_meter(self, _clean_brake_env):
        assert _build_stream_probes("sonnet", agentic=True) is not None

    def test_disabled_brake_and_no_meter_is_none(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "0")
        assert _build_stream_probes("sonnet", agentic=True) is None

    def test_cost_probe_wins_when_both_fire(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_SUBPROCESS_FRESH_INPUT_CEILING", "1000")
        disarm = arm_cost_meter(0.000001)
        try:
            probe = _build_stream_probes("claude-sonnet-4-6", agentic=True)
            exc = probe([_assistant_event(fresh=10_000_000, out=10_000_000,
                                          mid="m1")])
            # Run-ceiling breach outranks the per-call brake: its loop
            # semantics (stop the run) must win the composed race.
            assert isinstance(exc, BudgetRunawayError)
        finally:
            disarm()


# ---------------------------------------------------------------------------
# Cost-probe dedupe (the double-count fix, same live evidence)
# ---------------------------------------------------------------------------

class TestCostProbeDedupe:
    def test_same_message_id_counted_once(self, _clean_brake_env):
        from metrics import estimate_cost
        ev = _assistant_event(fresh=100, out=20_000, mid="msg_a")
        u = ev["message"]["usage"]
        one = estimate_cost(u["input_tokens"] + u["cache_read_input_tokens"]
                            + u["cache_creation_input_tokens"],
                            u["output_tokens"], model="claude-sonnet-4-6",
                            cache_read_tokens=u["cache_read_input_tokens"])
        assert one > 0
        disarm = arm_cost_meter(one * 1.5)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            # Two events, one API message: deduped estimate = 1x < 1.5x
            # ceiling. The pre-fix per-event accounting summed 2x >= 1.5x
            # and killed healthy calls.
            assert probe([ev, ev]) is None
            # A second REAL message crosses: 2x >= 1.5x.
            ev2 = _assistant_event(fresh=100, out=20_000, mid="msg_b")
            assert isinstance(probe([ev2]), BudgetRunawayError)
        finally:
            disarm()


# ---------------------------------------------------------------------------
# End-to-end: brake kills a live subprocess mid-flight
# ---------------------------------------------------------------------------

_EMITTER = """
import json, time
ev = {"type": "assistant", "message": {"id": "msg_big",
      "model": "claude-sonnet-4-6",
      "usage": {"input_tokens": 200000, "cache_creation_input_tokens": 200000,
                "cache_read_input_tokens": 0, "output_tokens": 10}}}
print(json.dumps(ev), flush=True)
time.sleep(30)
print("never reached", flush=True)
"""


class TestMidFlightKill:
    def test_kills_subprocess_when_ceiling_crossed(self, _clean_brake_env):
        probe = _build_step_token_brake("claude-sonnet-4-6")
        t0 = time.monotonic()
        with pytest.raises(TokenRunawayError) as ei:
            _run_subprocess_safe(
                [sys.executable, "-c", _EMITTER],
                timeout=25, liveness_timeout=0, poll_interval=FAST_POLL,
                stream_probe=probe,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 15, f"kill took {elapsed:.1f}s — brake did not fire"
        assert str(ei.value.maro_kill_reason).startswith("stream probe kill")
        assert "never reached" not in (ei.value.maro_partial_output or "")


# ---------------------------------------------------------------------------
# Classification + the blocked-step seam
# ---------------------------------------------------------------------------

class TestClassification:
    def test_token_runaway_class_never_retries_or_fails_over(self):
        exc = TokenRunawayError(400_000, 300_000)
        # The kill path attaches maro_kill_reason — which maps to FAILOVER
        # for plain timeouts. Exact type must outrank that shape.
        exc.maro_kill_reason = "stream probe kill: ..."
        info = classify_error(exc, backend="subprocess")
        assert info.error_class == TOKEN_RUNAWAY
        assert info.retryable is False
        assert info.failover is False

    def test_step_exec_converts_to_blocked_outcome(self):
        from step_exec import execute_step

        class _BrakedAdapter(LLMAdapter):
            backend = "fake"
            model_key = "x"

            def complete(self, messages, **kwargs):
                raise TokenRunawayError(400_000, 300_000)

        outcome = execute_step(
            goal="g",
            step_text="research the thing",
            step_num=1,
            total_steps=2,
            completed_context=[],
            adapter=_BrakedAdapter(),
            tools=[],
        )
        assert outcome["status"] == "blocked"
        assert outcome.get("error_class") == TOKEN_RUNAWAY
        assert "token brake" in outcome["stuck_reason"]


# ---------------------------------------------------------------------------
# CLI-side per-tool-call ingest cap (BASH_MAX_OUTPUT_LENGTH)
# ---------------------------------------------------------------------------

def _cli_stdout(result="ok"):
    return json.dumps({
        "type": "result", "subtype": "success", "is_error": False,
        "result": result, "session_id": "s", "total_cost_usd": 0.0,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    })


class TestBashOutputCapEnv:
    def test_default_cap(self, _clean_brake_env):
        assert _bash_output_cap_env() == {
            "BASH_MAX_OUTPUT_LENGTH": str(_BASH_OUTPUT_CAP_DEFAULT)}

    def test_maro_env_overrides(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_BASH_MAX_OUTPUT_CHARS", "1234")
        assert _bash_output_cap_env() == {"BASH_MAX_OUTPUT_LENGTH": "1234"}

    def test_maro_env_zero_disables(self, monkeypatch, _clean_brake_env):
        monkeypatch.setenv("MARO_BASH_MAX_OUTPUT_CHARS", "0")
        assert _bash_output_cap_env() is None

    def test_operator_env_respected(self, monkeypatch, _clean_brake_env):
        # An operator already exporting the CLI's own var keeps control: the
        # subprocess inherits it and maro doesn't overwrite.
        monkeypatch.setenv("BASH_MAX_OUTPUT_LENGTH", "50000")
        assert _bash_output_cap_env() is None

    def test_agentic_complete_passes_cap_env(self, _clean_brake_env):
        a = ClaudeSubprocessAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _cli_stdout()
        with patch("llm._run_subprocess_safe", return_value=mock_result) as mock_run:
            a.complete([LLMMessage("user", "x")])
        env_extra = mock_run.call_args.kwargs["env_extra"]
        assert env_extra == {
            "BASH_MAX_OUTPUT_LENGTH": str(_BASH_OUTPUT_CAP_DEFAULT)}

    def test_no_tools_complete_keeps_token_cap_only(self, _clean_brake_env):
        # Utility calls have the whole tool set disabled — the Bash cap is
        # meaningless there and the existing output-token cap must survive.
        a = ClaudeSubprocessAdapter()
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = _cli_stdout()
        with patch("llm._run_subprocess_safe", return_value=mock_result) as mock_run:
            a.complete([LLMMessage("user", "x")], no_tools=True, max_tokens=256)
        env_extra = mock_run.call_args.kwargs["env_extra"]
        assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in env_extra
        assert "BASH_MAX_OUTPUT_LENGTH" not in env_extra

    def test_container_lane_forwards_the_cap(self):
        # docker drops host env by construction; the guard-rail keys must be
        # in the explicit passthrough list or containers run uncapped.
        assert "BASH_MAX_OUTPUT_LENGTH" in _CONTAINER_ENV_PASSTHROUGH
        assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in _CONTAINER_ENV_PASSTHROUGH


# ---------------------------------------------------------------------------
# Prompt: the JSON-API carve-out is closed
# ---------------------------------------------------------------------------

class TestPromptCarveOut:
    def test_unbounded_json_carveout_gone(self):
        from step_exec import EXECUTE_SYSTEM
        assert "which are already compact" not in EXECUTE_SYSTEM

    def test_size_aware_guidance_present(self):
        from step_exec import EXECUTE_SYSTEM
        assert "head -c 20000" in EXECUTE_SYSTEM
        assert "multi-megabyte JSON" in EXECUTE_SYSTEM

    def test_truncation_reality_disclosed(self):
        # The prompt tells the worker the harness truncates oversized output
        # and persists the full copy — so the model plans around the file
        # instead of fighting the cap.
        from step_exec import EXECUTE_SYSTEM
        assert "saves the full output to a file" in EXECUTE_SYSTEM
