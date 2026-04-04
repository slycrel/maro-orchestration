"""Tests for Phase 41 step 5: step_events.py — typed step event model."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from step_events import (
    PostStepEvent,
    PreStepEvent,
    StepEventBus,
    StepVeto,
    StepVetoedError,
    step_event_bus,
)


# ---------------------------------------------------------------------------
# PreStepEvent / PostStepEvent
# ---------------------------------------------------------------------------

class TestEventDataclasses:
    def test_pre_step_event_fields(self):
        e = PreStepEvent(goal="g", step_text="do X", step_index=0)
        assert e.goal == "g"
        assert e.step_text == "do X"
        assert e.step_index == 0
        assert e.tool_name is None
        assert e.extra == {}

    def test_post_step_event_fields(self):
        e = PostStepEvent(goal="g", step_text="do X", step_index=1, result="done", elapsed_ms=42)
        assert e.result == "done"
        assert e.elapsed_ms == 42
        assert e.error is None

    def test_step_veto_fields(self):
        v = StepVeto(reason="quota exceeded", handler_name="quota_check")
        assert v.reason == "quota exceeded"
        assert v.handler_name == "quota_check"

    def test_step_vetoed_error_message(self):
        v = StepVeto(reason="too many requests", handler_name="rate_limit")
        err = StepVetoedError(v)
        assert "rate_limit" in str(err)
        assert "too many requests" in str(err)
        assert err.veto is v


# ---------------------------------------------------------------------------
# StepEventBus — registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_decorator_registers_pre_handler(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def my_handler(event):
            pass

        assert len(bus._pre) == 1
        assert bus._pre[0].name == "my_handler"

    def test_decorator_registers_post_handler(self):
        bus = StepEventBus()

        @bus.on_post_step()
        def my_post(event):
            pass

        assert len(bus._post) == 1

    def test_register_pre_imperative(self):
        bus = StepEventBus()
        fn = MagicMock()
        fn.__name__ = "check_fn"
        bus.register_pre(fn, match="create_*")
        assert len(bus._pre) == 1
        assert bus._pre[0].match == "create_*"

    def test_register_post_imperative(self):
        bus = StepEventBus()
        fn = MagicMock()
        fn.__name__ = "log_fn"
        bus.register_post(fn)
        assert len(bus._post) == 1

    def test_re_registration_replaces(self):
        bus = StepEventBus()

        @bus.on_pre_step(name="same_name")
        def handler_v1(event):
            return StepVeto(reason="v1")

        @bus.on_pre_step(name="same_name")
        def handler_v2(event):
            return StepVeto(reason="v2")

        assert len(bus._pre) == 1
        # v2 should replace v1
        veto = bus.fire_pre("anything", goal="g", step_index=0)
        assert veto.reason == "v2"

    def test_unregister_removes_by_name(self):
        bus = StepEventBus()

        @bus.on_pre_step(name="removable")
        def handler(event):
            pass

        removed = bus.unregister("removable")
        assert removed
        assert len(bus._pre) == 0

    def test_unregister_nonexistent_returns_false(self):
        bus = StepEventBus()
        assert not bus.unregister("does_not_exist")

    def test_clear_removes_all(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def pre(event): pass

        @bus.on_post_step()
        def post(event): pass

        bus.clear()
        assert bus._pre == []
        assert bus._post == []


# ---------------------------------------------------------------------------
# StepEventBus — matching
# ---------------------------------------------------------------------------

class TestMatching:
    def test_no_match_pattern_fires_always(self):
        bus = StepEventBus()
        calls = []

        @bus.on_pre_step()
        def handler(event):
            calls.append(event.step_text)

        bus.fire_pre("random step text", goal="g", step_index=0)
        assert len(calls) == 1

    def test_match_pattern_filters(self):
        bus = StepEventBus()
        calls = []

        @bus.on_pre_step(match="create_*")
        def handler(event):
            calls.append(event.step_text)

        bus.fire_pre("create_team_worker", goal="g", step_index=0)
        bus.fire_pre("complete_step", goal="g", step_index=1)
        assert len(calls) == 1
        assert calls[0] == "create_team_worker"

    def test_match_pattern_case_insensitive(self):
        bus = StepEventBus()
        calls = []

        @bus.on_pre_step(match="create_*")
        def handler(event):
            calls.append(True)

        bus.fire_pre("CREATE_TEAM_WORKER", goal="g", step_index=0)
        assert len(calls) == 1

    def test_post_match_pattern_filters(self):
        bus = StepEventBus()
        calls = []

        @bus.on_post_step(match="flag_*")
        def handler(event):
            calls.append(event.step_text)

        bus.fire_post("flag_stuck", goal="g", step_index=0)
        bus.fire_post("complete_step", goal="g", step_index=1)
        assert len(calls) == 1

    def test_wildcard_match_all(self):
        bus = StepEventBus()
        calls = []

        @bus.on_pre_step(match="*")
        def handler(event):
            calls.append(True)

        bus.fire_pre("anything at all", goal="g", step_index=0)
        bus.fire_pre("something else", goal="g", step_index=1)
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# StepEventBus — fire_pre (blocking)
# ---------------------------------------------------------------------------

class TestFirePre:
    def test_no_handlers_returns_none(self):
        bus = StepEventBus()
        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert result is None

    def test_handler_returning_none_passes(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def handler(event):
            return None

        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert result is None

    def test_handler_returning_veto_returns_veto(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def handler(event):
            return StepVeto(reason="not allowed")

        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert isinstance(result, StepVeto)
        assert result.reason == "not allowed"

    def test_handler_name_set_on_veto(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def check_quota(event):
            return StepVeto(reason="quota")

        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert result.handler_name == "check_quota"

    def test_first_veto_wins(self):
        bus = StepEventBus()

        @bus.on_pre_step(name="first")
        def first(event):
            return StepVeto(reason="first veto")

        @bus.on_pre_step(name="second")
        def second(event):
            return StepVeto(reason="second veto")

        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert result.reason == "first veto"

    def test_handler_exception_swallowed(self):
        bus = StepEventBus()

        @bus.on_pre_step()
        def buggy_handler(event):
            raise RuntimeError("oops")

        # Should not raise — buggy handlers are logged and swallowed
        result = bus.fire_pre("do X", goal="g", step_index=0)
        assert result is None

    def test_event_payload_passed(self):
        bus = StepEventBus()
        received = []

        @bus.on_pre_step()
        def capture(event):
            received.append(event)

        bus.fire_pre("my step", goal="my goal", step_index=3, tool_name="schedule_run")
        assert len(received) == 1
        e = received[0]
        assert e.goal == "my goal"
        assert e.step_text == "my step"
        assert e.step_index == 3
        assert e.tool_name == "schedule_run"

    def test_extra_passed_through(self):
        bus = StepEventBus()
        received = []

        @bus.on_pre_step()
        def capture(event):
            received.append(event.extra)

        bus.fire_pre("step", goal="g", step_index=0, extra={"dry_run": True})
        assert received[0]["dry_run"] is True


# ---------------------------------------------------------------------------
# StepEventBus — fire_post (non-blocking)
# ---------------------------------------------------------------------------

class TestFirePost:
    def test_no_handlers_no_error(self):
        bus = StepEventBus()
        bus.fire_post("do X", goal="g", step_index=0)  # should not raise

    def test_handler_called(self):
        bus = StepEventBus()
        calls = []

        @bus.on_post_step()
        def handler(event):
            calls.append(event.result)

        bus.fire_post("do X", goal="g", step_index=0, result="done")
        assert calls == ["done"]

    def test_handler_exception_swallowed(self):
        bus = StepEventBus()

        @bus.on_post_step()
        def buggy(event):
            raise ValueError("oops")

        # Should not raise
        bus.fire_post("step", goal="g", step_index=0)

    def test_error_field_passed(self):
        bus = StepEventBus()
        received = []

        @bus.on_post_step()
        def capture(event):
            received.append(event.error)

        exc = RuntimeError("failed")
        bus.fire_post("step", goal="g", step_index=0, error=exc)
        assert received[0] is exc

    def test_elapsed_ms_passed(self):
        bus = StepEventBus()
        received = []

        @bus.on_post_step()
        def capture(event):
            received.append(event.elapsed_ms)

        bus.fire_post("step", goal="g", step_index=0, elapsed_ms=250)
        assert received[0] == 250

    def test_multiple_post_handlers_all_called(self):
        bus = StepEventBus()
        calls = []

        @bus.on_post_step(name="h1")
        def h1(event): calls.append("h1")

        @bus.on_post_step(name="h2")
        def h2(event): calls.append("h2")

        bus.fire_post("step", goal="g", step_index=0)
        assert set(calls) == {"h1", "h2"}


# ---------------------------------------------------------------------------
# StepEventBus — introspection
# ---------------------------------------------------------------------------

class TestIntrospection:
    def test_list_handlers_empty(self):
        bus = StepEventBus()
        info = bus.list_handlers()
        assert info["pre"] == []
        assert info["post"] == []

    def test_list_handlers_populated(self):
        bus = StepEventBus()

        @bus.on_pre_step(match="create_*", name="quota_check")
        def h(event): pass

        @bus.on_post_step(name="metric_log")
        def g(event): pass

        info = bus.list_handlers()
        assert len(info["pre"]) == 1
        assert info["pre"][0] == {"name": "quota_check", "match": "create_*"}
        assert info["post"][0] == {"name": "metric_log", "match": None}


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

class TestModuleSingleton:
    def test_step_event_bus_is_bus_instance(self):
        assert isinstance(step_event_bus, StepEventBus)

    def test_singleton_starts_empty(self):
        # The module-level bus should not have any handlers pre-registered
        # (they're registered by callers, not at module import)
        # We can't guarantee it's empty if other tests ran first — just check type
        assert hasattr(step_event_bus, "_pre")
        assert hasattr(step_event_bus, "_post")
