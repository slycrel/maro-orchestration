"""Tests for typed event graph nodes — EventRouter + await:<kind> in execute_step."""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import List
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from interrupt import TypedEvent, EventRouter, post_typed_event, get_event_router


# ---------------------------------------------------------------------------
# TypedEvent
# ---------------------------------------------------------------------------

class TestTypedEvent:
    def test_fields(self):
        ev = TypedEvent(id="abc123", kind="telegram", payload="hello", source="bot")
        assert ev.id == "abc123"
        assert ev.kind == "telegram"
        assert ev.payload == "hello"
        assert ev.source == "bot"
        assert ev.timestamp  # non-empty

    def test_to_dict_from_dict_roundtrip(self):
        ev = TypedEvent(id="x", kind="data_ready", payload="BTC=82k", source="api")
        d = ev.to_dict()
        ev2 = TypedEvent.from_dict(d)
        assert ev2.kind == ev.kind
        assert ev2.payload == ev.payload
        assert ev2.source == ev.source


# ---------------------------------------------------------------------------
# EventRouter — basic post/wait
# ---------------------------------------------------------------------------

class TestEventRouterBasic:
    def test_post_returns_typed_event(self):
        router = EventRouter()
        ev = router.post("telegram", "hello world", source="test")
        assert isinstance(ev, TypedEvent)
        assert ev.kind == "telegram"
        assert ev.payload == "hello world"
        assert ev.source == "test"
        assert ev.id

    def test_wait_for_already_posted_event(self):
        router = EventRouter()
        router.post("data_ready", "BTC=82k", source="api")
        ev = router.wait_for("data_ready", timeout=1.0)
        assert ev is not None
        assert ev.kind == "data_ready"
        assert ev.payload == "BTC=82k"

    def test_wait_for_wrong_kind_returns_none_on_timeout(self):
        router = EventRouter()
        router.post("telegram", "hello", source="test")
        ev = router.wait_for("timer", timeout=0.05)
        assert ev is None

    def test_wait_for_empty_router_times_out(self):
        router = EventRouter()
        t0 = time.monotonic()
        ev = router.wait_for("anything", timeout=0.1)
        elapsed = time.monotonic() - t0
        assert ev is None
        assert elapsed >= 0.09  # waited at least 90ms

    def test_kind_is_normalized_lowercase(self):
        router = EventRouter()
        router.post("Telegram", "hello", source="test")
        ev = router.wait_for("TELEGRAM", timeout=1.0)
        assert ev is not None

    def test_event_consumed_once(self):
        """One event should only be consumed by one waiter."""
        router = EventRouter()
        router.post("data", "payload", source="test")

        ev1 = router.wait_for("data", timeout=0.5)
        ev2 = router.wait_for("data", timeout=0.05)  # should timeout — already consumed

        assert ev1 is not None
        assert ev2 is None  # consumed

    def test_multiple_events_same_kind_each_consumed_once(self):
        router = EventRouter()
        router.post("tick", "1", source="timer")
        router.post("tick", "2", source="timer")

        ev1 = router.wait_for("tick", timeout=0.5)
        ev2 = router.wait_for("tick", timeout=0.5)

        assert ev1 is not None
        assert ev2 is not None
        # Different events
        assert ev1.id != ev2.id

    def test_wait_for_event_posted_after_wait_starts(self):
        """Waiter started before event is posted should still receive it."""
        router = EventRouter()
        received = []

        def _waiter():
            ev = router.wait_for("late_arrival", timeout=2.0)
            if ev is not None:
                received.append(ev)

        t = threading.Thread(target=_waiter)
        t.start()
        time.sleep(0.05)  # let waiter start blocking
        router.post("late_arrival", "I'm here!", source="test")
        t.join(timeout=2.0)

        assert len(received) == 1
        assert received[0].payload == "I'm here!"

    def test_concurrent_waiters_different_kinds(self):
        """Two waiters on different kinds should each receive their event."""
        router = EventRouter()
        results = {}

        def _wait(kind):
            ev = router.wait_for(kind, timeout=2.0)
            results[kind] = ev

        t1 = threading.Thread(target=_wait, args=("alpha",))
        t2 = threading.Thread(target=_wait, args=("beta",))
        t1.start()
        t2.start()
        time.sleep(0.05)

        router.post("alpha", "A", source="test")
        router.post("beta", "B", source="test")

        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert results.get("alpha") is not None
        assert results.get("beta") is not None
        assert results["alpha"].payload == "A"
        assert results["beta"].payload == "B"

    def test_pending_count_zero_when_empty(self):
        router = EventRouter()
        assert router.pending_count() == 0

    def test_pending_count_increments_on_post(self):
        router = EventRouter()
        router.post("x", "a", source="test")
        router.post("x", "b", source="test")
        assert router.pending_count("x") == 2
        assert router.pending_count("y") == 0

    def test_pending_count_decrements_after_consume(self):
        router = EventRouter()
        router.post("x", "payload", source="test")
        assert router.pending_count("x") == 1
        router.wait_for("x", timeout=0.5)
        assert router.pending_count("x") == 0

    def test_recent_events_returns_posted(self):
        router = EventRouter()
        router.post("audit", "log1", source="test")
        router.post("audit", "log2", source="test")
        recent = router.recent_events("audit")
        assert len(recent) == 2
        assert any(e.payload == "log1" for e in recent)


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

class TestGlobalRouter:
    def test_get_event_router_returns_singleton(self):
        r1 = get_event_router()
        r2 = get_event_router()
        assert r1 is r2

    def test_post_typed_event_convenience(self):
        router = EventRouter()
        with mock.patch("interrupt._ROUTER", router):
            ev = post_typed_event("convenient_kind", payload="data", source="unit_test")
        assert ev.kind == "convenient_kind"
        assert ev.payload == "data"


# ---------------------------------------------------------------------------
# execute_step — await:<kind> interceptor
# ---------------------------------------------------------------------------

class TestAwaitEventInExecuteStep:
    """Test that execute_step intercepts 'await:<kind>' steps without LLM calls."""

    def _make_fake_router(self, event: TypedEvent | None):
        """Return a mock EventRouter that immediately returns event (or None)."""
        router = EventRouter.__new__(EventRouter)
        router.wait_for = lambda kind, timeout=300.0: event
        return router

    def test_await_step_returns_done_when_event_arrives(self):
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        ev = TypedEvent(id="ev1", kind="telegram", payload="price ready", source="bot")
        fake_router = self._make_fake_router(ev)

        with mock.patch("interrupt.get_event_router", return_value=fake_router):
            result = execute_step(
                goal="wait for market data",
                step_text="await:telegram",
                step_num=1,
                total_steps=2,
                completed_context=[],
                adapter=None,
                tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            )

        assert result["status"] == "done"
        assert "telegram" in result["result"]
        assert "price ready" in result["result"]
        assert result["tokens_in"] == 0  # no LLM call

    def test_await_step_returns_blocked_on_timeout(self):
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        fake_router = self._make_fake_router(None)  # returns None = timeout

        with mock.patch("interrupt.get_event_router", return_value=fake_router):
            result = execute_step(
                goal="wait for data",
                step_text="await:data_ready",
                step_num=1,
                total_steps=1,
                completed_context=[],
                adapter=None,
                tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            )

        assert result["status"] == "blocked"
        assert "timeout" in result["stuck_reason"]
        assert "data_ready" in result["stuck_reason"]

    def test_await_event_prefix_also_works(self):
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        ev = TypedEvent(id="ev2", kind="timer", payload="tick", source="scheduler")
        fake_router = self._make_fake_router(ev)

        with mock.patch("interrupt.get_event_router", return_value=fake_router):
            result = execute_step(
                goal="wait for timer",
                step_text="await_event:timer",
                step_num=2,
                total_steps=3,
                completed_context=[],
                adapter=None,
                tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            )

        assert result["status"] == "done"
        assert "timer" in result["result"]

    def test_await_timeout_param_parsed(self):
        """await:kind[timeout=10s] should use 10s timeout."""
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        captured_timeout = []

        class _FakeRouter:
            def wait_for(self, kind, timeout=300.0):
                captured_timeout.append(timeout)
                return None

        with mock.patch("interrupt.get_event_router", return_value=_FakeRouter()):
            execute_step(
                goal="goal",
                step_text="await:api_response[timeout=10s]",
                step_num=1,
                total_steps=1,
                completed_context=[],
                adapter=None,
                tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            )

        assert captured_timeout and captured_timeout[0] == 10.0

    def test_normal_step_text_not_intercepted(self):
        """Steps not matching await: pattern should reach the LLM path."""
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        # No LLMMessage call expected to reach router — but adapter=None means blocked
        result = execute_step(
            goal="analyze data",
            step_text="fetch and analyze the market data",
            step_num=1,
            total_steps=1,
            completed_context=[],
            adapter=None,  # will fail at LLM stage, not at await interceptor
            tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
        )
        # Should not have been intercepted — status will be from LLM failure path
        assert result.get("status") in ("done", "blocked", "stuck")

    def test_await_step_case_insensitive(self):
        from step_exec import execute_step, EXECUTE_TOOLS
        from llm import LLMTool

        ev = TypedEvent(id="ev3", kind="slack", payload="msg", source="slack")
        fake_router = self._make_fake_router(ev)

        with mock.patch("interrupt.get_event_router", return_value=fake_router):
            result = execute_step(
                goal="wait",
                step_text="AWAIT:SLACK",
                step_num=1,
                total_steps=1,
                completed_context=[],
                adapter=None,
                tools=[LLMTool(**t) for t in EXECUTE_TOOLS],
            )

        assert result["status"] == "done"


# ---------------------------------------------------------------------------
# heartbeat.post_heartbeat_event → fires typed event
# ---------------------------------------------------------------------------

class TestHeartbeatFiresTypedEvent:
    def test_post_heartbeat_event_fires_typed_event(self):
        from heartbeat import post_heartbeat_event

        posted = []

        with mock.patch("interrupt.post_typed_event", side_effect=lambda kind, payload, source: posted.append((kind, payload))):
            post_heartbeat_event(event_type="telegram", payload="new message")

        assert any(k == "telegram" and "new message" in p for k, p in posted)

    def test_post_heartbeat_event_still_sets_wakeup_event(self):
        from heartbeat import post_heartbeat_event, _wakeup_event

        _wakeup_event.clear()
        with mock.patch("interrupt.post_typed_event"):
            post_heartbeat_event(event_type="test")

        assert _wakeup_event.is_set()

    def test_post_heartbeat_event_survives_router_failure(self):
        """If post_typed_event raises, heartbeat should not crash."""
        from heartbeat import post_heartbeat_event

        with mock.patch("interrupt.post_typed_event", side_effect=RuntimeError("router down")):
            # Should not raise
            post_heartbeat_event(event_type="test", payload="data")
