"""step_events.py — Phase 41 step 5: Typed step event model with matcher patterns.

Complements the existing hooks.py scope-based system. hooks.py fires on
mission/milestone/feature/step lifecycle events via a registry. step_events.py
adds *typed* events — PreStepExecution and PostStepExecution — with:

  - Structured event payloads (goal, step_text, step_index, result, tool_name)
  - Glob matcher on step_text / tool_name (only fire for matching steps)
  - Blocking semantics: PreStepExecution handlers can veto execution
  - Non-blocking: PostStepExecution handlers always run (failures logged, not raised)

Usage:
    from step_events import StepEventBus, PreStepEvent, PostStepEvent

    bus = StepEventBus()

    @bus.on_pre_step(match="create_*")
    def check_quota(event):
        if at_quota():
            return StepVeto(reason="quota exceeded")

    @bus.on_post_step()
    def log_metric(event):
        metrics.record(event.step_index, event.result)

    # In step executor:
    veto = bus.fire_pre(step_text="create_team_worker ...", goal=goal, step_index=i)
    if veto:
        raise StepVetoedError(veto.reason)

    result = execute_step(...)
    bus.fire_post(step_text=..., goal=goal, step_index=i, result=result)
"""

from __future__ import annotations

import fnmatch
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

log = logging.getLogger("poe.step_events")


# ---------------------------------------------------------------------------
# Event payloads
# ---------------------------------------------------------------------------

@dataclass
class PreStepEvent:
    """Fired before a step executes. Handlers may return StepVeto to block."""
    goal: str
    step_text: str
    step_index: int
    tool_name: Optional[str] = None   # set if the step resolves to a known tool
    extra: dict = field(default_factory=dict)


@dataclass
class PostStepEvent:
    """Fired after a step executes (success or failure). Non-blocking."""
    goal: str
    step_text: str
    step_index: int
    result: Any = None                # step result — may be str, dict, or None
    error: Optional[Exception] = None # set if the step raised
    tool_name: Optional[str] = None
    elapsed_ms: int = 0
    extra: dict = field(default_factory=dict)


@dataclass
class StepVeto:
    """Returned by a PreStepExecution handler to veto the step."""
    reason: str
    handler_name: str = ""


class StepVetoedError(RuntimeError):
    """Raised by the bus when a pre-step handler vetoes execution."""
    def __init__(self, veto: StepVeto) -> None:
        super().__init__(f"Step vetoed by {veto.handler_name!r}: {veto.reason}")
        self.veto = veto


# ---------------------------------------------------------------------------
# Handler wrappers
# ---------------------------------------------------------------------------

@dataclass
class _PreHandler:
    fn: Callable[[PreStepEvent], Optional[StepVeto]]
    match: Optional[str]   # glob on step_text; None = match all
    name: str

    def matches(self, event: PreStepEvent) -> bool:
        if self.match is None:
            return True
        text = event.step_text.lower()
        return fnmatch.fnmatch(text, self.match.lower())


@dataclass
class _PostHandler:
    fn: Callable[[PostStepEvent], None]
    match: Optional[str]   # glob on step_text; None = match all
    name: str

    def matches(self, event: PostStepEvent) -> bool:
        if self.match is None:
            return True
        text = event.step_text.lower()
        return fnmatch.fnmatch(text, self.match.lower())


# ---------------------------------------------------------------------------
# StepEventBus
# ---------------------------------------------------------------------------

class StepEventBus:
    """Lightweight event bus for step lifecycle events.

    Thread-safety: not guaranteed — intended for single-threaded agent loops.
    Handler registration is idempotent by name (re-registering replaces).
    """

    def __init__(self) -> None:
        self._pre: List[_PreHandler] = []
        self._post: List[_PostHandler] = []

    # --- Registration API ---

    def on_pre_step(
        self,
        match: Optional[str] = None,
        *,
        name: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a PreStepExecution handler.

        Args:
            match: Glob pattern for step_text (e.g. "create_*"). None = all steps.
            name:  Optional handler name for logging / deduplication.
        """
        def decorator(fn: Callable) -> Callable:
            handler_name = name or fn.__name__
            self._pre = [h for h in self._pre if h.name != handler_name]
            self._pre.append(_PreHandler(fn=fn, match=match, name=handler_name))
            log.debug("step_events: registered pre-step handler %r (match=%r)", handler_name, match)
            return fn
        return decorator

    def on_post_step(
        self,
        match: Optional[str] = None,
        *,
        name: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a PostStepExecution handler (non-blocking)."""
        def decorator(fn: Callable) -> Callable:
            handler_name = name or fn.__name__
            self._post = [h for h in self._post if h.name != handler_name]
            self._post.append(_PostHandler(fn=fn, match=match, name=handler_name))
            log.debug("step_events: registered post-step handler %r (match=%r)", handler_name, match)
            return fn
        return decorator

    def register_pre(
        self,
        fn: Callable[[PreStepEvent], Optional[StepVeto]],
        match: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        """Imperative alternative to @on_pre_step decorator."""
        handler_name = name or getattr(fn, "__name__", repr(fn))
        self._pre = [h for h in self._pre if h.name != handler_name]
        self._pre.append(_PreHandler(fn=fn, match=match, name=handler_name))

    def register_post(
        self,
        fn: Callable[[PostStepEvent], None],
        match: Optional[str] = None,
        name: Optional[str] = None,
    ) -> None:
        """Imperative alternative to @on_post_step decorator."""
        handler_name = name or getattr(fn, "__name__", repr(fn))
        self._post = [h for h in self._post if h.name != handler_name]
        self._post.append(_PostHandler(fn=fn, match=match, name=handler_name))

    def unregister(self, name: str) -> bool:
        """Remove a handler by name. Returns True if removed."""
        pre_before = len(self._pre)
        post_before = len(self._post)
        self._pre = [h for h in self._pre if h.name != name]
        self._post = [h for h in self._post if h.name != name]
        return len(self._pre) < pre_before or len(self._post) < post_before

    def clear(self) -> None:
        """Remove all handlers."""
        self._pre.clear()
        self._post.clear()

    # --- Firing API ---

    def fire_pre(
        self,
        step_text: str,
        goal: str,
        step_index: int,
        tool_name: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> Optional[StepVeto]:
        """Run all matching PreStepExecution handlers.

        Returns the first StepVeto from any handler, or None if all passed.
        Raises StepVetoedError if a veto is returned (caller can also check
        return value and raise themselves for custom error handling).

        Handler exceptions other than StepVeto are logged and swallowed
        (a buggy pre-hook should not crash the loop).
        """
        event = PreStepEvent(
            goal=goal,
            step_text=step_text,
            step_index=step_index,
            tool_name=tool_name,
            extra=extra or {},
        )
        for handler in self._pre:
            if not handler.matches(event):
                continue
            try:
                result = handler.fn(event)
                if isinstance(result, StepVeto):
                    result.handler_name = result.handler_name or handler.name
                    log.warning(
                        "step_events: pre-step handler %r vetoed step %d: %s",
                        handler.name, step_index, result.reason,
                    )
                    return result
            except Exception:
                log.error(
                    "step_events: pre-step handler %r raised:\n%s",
                    handler.name, traceback.format_exc(),
                )
        return None

    def fire_post(
        self,
        step_text: str,
        goal: str,
        step_index: int,
        result: Any = None,
        error: Optional[Exception] = None,
        tool_name: Optional[str] = None,
        elapsed_ms: int = 0,
        extra: Optional[dict] = None,
    ) -> None:
        """Run all matching PostStepExecution handlers (non-blocking).

        All exceptions are caught and logged — post-step hooks never crash the loop.
        """
        event = PostStepEvent(
            goal=goal,
            step_text=step_text,
            step_index=step_index,
            result=result,
            error=error,
            tool_name=tool_name,
            elapsed_ms=elapsed_ms,
            extra=extra or {},
        )
        for handler in self._post:
            if not handler.matches(event):
                continue
            try:
                handler.fn(event)
            except Exception:
                log.error(
                    "step_events: post-step handler %r raised:\n%s",
                    handler.name, traceback.format_exc(),
                )

    # --- Introspection ---

    def list_handlers(self) -> dict:
        return {
            "pre": [{"name": h.name, "match": h.match} for h in self._pre],
            "post": [{"name": h.name, "match": h.match} for h in self._post],
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

step_event_bus: StepEventBus = StepEventBus()
