"""Stream-side runaway cost kill (BACKLOG #23e residual).

The pre-call refusal (test_budget_runaway.py) stops the call AFTER a runaway
one; run 8a20665f's step 9 burned $2.04/4.7M tokens inside a SINGLE
subprocess call the meter could only see after it returned. The stream probe
closes that gap: usage blocks on stream-json assistant events are cost-
estimated as they arrive, and the subprocess is killed mid-flight once
meter-spend + the running estimate crosses the armed ceiling.
"""

import json
import subprocess
import sys
import time

import pytest

from llm import _build_stream_cost_probe, _run_subprocess_safe, arm_cost_meter
from llm_errors import BudgetRunawayError


def _assistant_event(output_tokens, model="claude-sonnet-4-6", input_tokens=100):
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "usage": {"input_tokens": input_tokens,
                      "output_tokens": output_tokens},
        },
    }


# ---------------------------------------------------------------------------
# Probe unit behavior
# ---------------------------------------------------------------------------

class TestProbeUnit:
    def test_disarmed_returns_none(self):
        assert _build_stream_cost_probe("sonnet") is None

    def test_zero_ceiling_returns_none(self):
        disarm = arm_cost_meter(0.0)
        try:
            assert _build_stream_cost_probe("sonnet") is None
        finally:
            disarm()

    def test_under_ceiling_returns_none(self):
        disarm = arm_cost_meter(100.0)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            assert probe([_assistant_event(1_000)]) is None
        finally:
            disarm()

    def test_crossing_ceiling_returns_runaway_and_accrues(self):
        from llm import cost_meter_state
        disarm = arm_cost_meter(0.50)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            # ~10M output tokens at Sonnet rates is far past $0.50.
            exc = probe([_assistant_event(10_000_000)])
            assert isinstance(exc, BudgetRunawayError)
            # The in-flight estimate was accrued into the meter.
            assert cost_meter_state()["spent_usd"] > 0.50
        finally:
            disarm()

    def test_accumulates_across_polls(self):
        disarm = arm_cost_meter(0.50)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            # Each event alone is under the ceiling; the running total is not.
            assert probe([_assistant_event(15_000)]) is None
            for _ in range(40):
                exc = probe([_assistant_event(15_000)])
                if exc is not None:
                    assert isinstance(exc, BudgetRunawayError)
                    return
            pytest.fail("running total never crossed the ceiling")
        finally:
            disarm()

    def test_non_assistant_events_ignored(self):
        disarm = arm_cost_meter(0.01)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            events = [{"type": "system"}, {"type": "result"},
                      {"type": "user", "message": {}}]
            assert probe(events) is None
        finally:
            disarm()


# ---------------------------------------------------------------------------
# End-to-end: probe kills a live subprocess mid-flight
# ---------------------------------------------------------------------------

_EMITTER = """
import json, sys, time
ev = {"type": "assistant", "message": {"model": "claude-sonnet-4-6",
      "usage": {"input_tokens": 100, "output_tokens": 10000000}}}
print(json.dumps(ev), flush=True)
time.sleep(30)
print("never reached", flush=True)
"""


class TestMidFlightKill:
    def test_kills_subprocess_when_ceiling_crossed(self):
        disarm = arm_cost_meter(0.50)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            t0 = time.monotonic()
            with pytest.raises(BudgetRunawayError) as ei:
                _run_subprocess_safe(
                    [sys.executable, "-c", _EMITTER],
                    timeout=25, liveness_timeout=0, poll_interval=0.2,
                    stream_probe=probe,
                )
            elapsed = time.monotonic() - t0
            # Killed on the event, not the 25s wall clock / 30s sleep.
            assert elapsed < 15
            assert getattr(ei.value, "maro_kill_reason", "").startswith(
                "stream probe kill")
            # Partial output up to the kill is preserved for introspection.
            assert "assistant" in getattr(ei.value, "maro_partial_output", "")
        finally:
            disarm()

    def test_no_probe_runs_to_completion(self):
        result = _run_subprocess_safe(
            [sys.executable, "-c", "print('{\"type\": \"noise\"}')"],
            timeout=20, liveness_timeout=0, poll_interval=0.2,
        )
        assert result.returncode == 0

    def test_probe_under_ceiling_does_not_kill(self):
        disarm = arm_cost_meter(1000.0)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            emit_small = (
                "import json;"
                "print(json.dumps({'type': 'assistant', 'message': {'model': 'm',"
                " 'usage': {'input_tokens': 10, 'output_tokens': 10}}}))"
            )
            result = _run_subprocess_safe(
                [sys.executable, "-c", emit_small],
                timeout=20, liveness_timeout=0, poll_interval=0.2,
                stream_probe=probe,
            )
            assert result.returncode == 0
        finally:
            disarm()

    def test_probe_errors_never_break_the_request(self):
        def bad_probe(events):
            raise RuntimeError("accounting blew up")
        result = _run_subprocess_safe(
            [sys.executable, "-c", "print('{\"type\": \"assistant\"}')"],
            timeout=20, liveness_timeout=0, poll_interval=0.2,
            stream_probe=bad_probe,
        )
        assert result.returncode == 0

    def test_split_line_across_polls_is_parsed(self):
        # Emit one event byte-dribbled so a poll boundary lands mid-line.
        dribble = """
import json, sys, time
line = json.dumps({"type": "assistant", "message": {"model": "claude-sonnet-4-6",
    "usage": {"input_tokens": 100, "output_tokens": 10000000}}}) + "\\n"
half = len(line) // 2
sys.stdout.write(line[:half]); sys.stdout.flush()
time.sleep(1.0)
sys.stdout.write(line[half:]); sys.stdout.flush()
time.sleep(20)
"""
        disarm = arm_cost_meter(0.50)
        try:
            probe = _build_stream_cost_probe("claude-sonnet-4-6")
            with pytest.raises(BudgetRunawayError):
                _run_subprocess_safe(
                    [sys.executable, "-c", dribble],
                    timeout=15, liveness_timeout=0, poll_interval=0.2,
                    stream_probe=probe,
                )
        finally:
            disarm()
