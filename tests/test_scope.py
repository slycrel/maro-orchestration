"""Tests for scope generation (Phase 65 minimum viable experiment)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scope import (
    ScopeSet,
    _parse_scope_markdown,
    generate_scope,
    inject_scope_into_context,
)


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Minimal adapter returning a canned response."""

    def __init__(self, response_text: str = "", raise_on_complete: bool = False):
        self.response_text = response_text
        self.raise_on_complete = raise_on_complete
        self.calls: list = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.raise_on_complete:
            raise RuntimeError("simulated adapter failure")
        from llm import LLMResponse
        return LLMResponse(
            content=self.response_text,
            stop_reason="end_turn",
            input_tokens=50,
            output_tokens=50,
        )


# ---------------------------------------------------------------------------
# ScopeSet
# ---------------------------------------------------------------------------

def test_scope_set_to_markdown_renders_all_sections():
    scope = ScopeSet(
        failure_modes=["goroutine blocks on I/O"],
        in_scope=["timeouts on all I/O"],
        out_of_scope=["custom TLS handshake"],
        raw_text="(ignored)",
    )
    md = scope.to_markdown()
    assert "## Scope (goal bounds)" in md
    assert "Failure modes to avoid" in md
    assert "In scope" in md
    assert "Out of scope" in md
    assert "goroutine blocks on I/O" in md
    assert "timeouts on all I/O" in md
    assert "custom TLS handshake" in md


def test_scope_set_is_empty_when_no_content():
    assert ScopeSet().is_empty()
    assert not ScopeSet(failure_modes=["x"]).is_empty()
    assert not ScopeSet(in_scope=["x"]).is_empty()
    assert not ScopeSet(out_of_scope=["x"]).is_empty()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_GOOD_MARKDOWN = """
## Failure Modes
- If the WebSocket drops mid-game, state is lost
- If the game goroutine blocks on I/O the browser never responds to, deadlock

## In Scope
- Timeouts on every I/O operation
- Session persistence before game logic
- Browser client handles ANSI escape codes

## Out of Scope
- Multi-user matchmaking
- Persistent leaderboards
"""


def test_parse_scope_markdown_extracts_all_three_sections():
    scope = _parse_scope_markdown(_GOOD_MARKDOWN)
    assert len(scope.failure_modes) == 2
    assert len(scope.in_scope) == 3
    assert len(scope.out_of_scope) == 2
    assert "WebSocket" in scope.failure_modes[0]
    assert "Timeouts" in scope.in_scope[0]
    assert "Multi-user" in scope.out_of_scope[0]
    assert scope.raw_text == _GOOD_MARKDOWN


def test_parse_scope_markdown_handles_empty_input():
    scope = _parse_scope_markdown("")
    assert scope.is_empty()
    scope = _parse_scope_markdown("   \n\n  ")
    assert scope.is_empty()


def test_parse_scope_markdown_handles_no_headings():
    """Garbage LLM output should produce empty scope, not crash."""
    scope = _parse_scope_markdown("I'm sorry I don't know how to help with that.")
    assert scope.is_empty()


def test_parse_scope_markdown_tolerates_heading_variants():
    """LLM might use different heading levels or casings."""
    txt = """
# FAILURE MODES
- f1
### in-scope:
- x
#### Out-of-Scope
- y
"""
    scope = _parse_scope_markdown(txt)
    assert scope.failure_modes == ["f1"]
    assert scope.in_scope == ["x"]
    assert scope.out_of_scope == ["y"]


def test_parse_scope_markdown_tolerates_asterisk_bullets():
    txt = """
## Failure Modes
* failure one
* failure two

## In Scope
* thing one
"""
    scope = _parse_scope_markdown(txt)
    assert scope.failure_modes == ["failure one", "failure two"]
    assert scope.in_scope == ["thing one"]


# ---------------------------------------------------------------------------
# generate_scope
# ---------------------------------------------------------------------------

def test_generate_scope_returns_none_on_empty_goal():
    adapter = _FakeAdapter(response_text=_GOOD_MARKDOWN)
    assert generate_scope("", adapter) is None


def test_generate_scope_returns_none_on_missing_adapter():
    assert generate_scope("build X", None) is None


def test_generate_scope_returns_none_on_adapter_failure():
    adapter = _FakeAdapter(raise_on_complete=True)
    assert generate_scope("build X", adapter) is None


def test_generate_scope_returns_none_on_empty_response():
    adapter = _FakeAdapter(response_text="")
    assert generate_scope("build X", adapter) is None


def test_generate_scope_returns_empty_scope_with_raw_on_unparseable_response():
    # Parse failure used to return None, which discarded evidence. Now we
    # return an empty ScopeSet with raw_text populated so the caller can
    # persist the raw LLM output for debugging. is_empty() still flags
    # "don't inject" — this only changes what the caller can observe.
    adapter = _FakeAdapter(response_text="I'd love to help but...")
    scope = generate_scope("build X", adapter)
    assert scope is not None
    assert scope.is_empty()
    assert "I'd love to help" in scope.raw_text


def test_generate_scope_parses_good_response():
    adapter = _FakeAdapter(response_text=_GOOD_MARKDOWN)
    scope = generate_scope("build a headless server", adapter)
    assert scope is not None
    assert len(scope.failure_modes) == 2
    assert len(scope.in_scope) == 3
    assert len(scope.out_of_scope) == 2


def test_generate_scope_sends_goal_to_adapter():
    adapter = _FakeAdapter(response_text=_GOOD_MARKDOWN)
    generate_scope("build a headless server", adapter)
    assert len(adapter.calls) == 1
    user_msg = adapter.calls[0]["messages"][-1]
    assert "headless server" in user_msg.content


def test_generate_scope_emits_deferred_markers(caplog):
    """Phase 65 minimum viable must log [scope-deferred] at every punted decision.

    This makes the punts searchable when we come back to expand the feature.
    """
    import logging as _logging
    adapter = _FakeAdapter(response_text=_GOOD_MARKDOWN)
    with caplog.at_level(_logging.INFO, logger="scope"):
        generate_scope("build X", adapter)
    messages = " | ".join(r.getMessage() for r in caplog.records)
    assert "[scope-deferred] triad" in messages
    assert "[scope-deferred] lifecycle" in messages
    assert "[scope-deferred] retrieval" in messages
    assert "[scope-deferred] memory" in messages


# ---------------------------------------------------------------------------
# inject_scope_into_context
# ---------------------------------------------------------------------------

def test_inject_scope_appends_to_existing_ancestry():
    scope = ScopeSet(in_scope=["x"])
    result = inject_scope_into_context(scope, "existing ancestry")
    assert "existing ancestry" in result
    assert "## Scope" in result
    assert result.index("existing ancestry") < result.index("## Scope")


def test_inject_scope_with_empty_ancestry_returns_just_scope():
    scope = ScopeSet(in_scope=["x"])
    result = inject_scope_into_context(scope, "")
    assert "## Scope" in result
    assert result.startswith("## Scope")


def test_inject_scope_none_returns_ancestry_unchanged():
    result = inject_scope_into_context(None, "existing ancestry")
    assert result == "existing ancestry"


def test_inject_scope_empty_returns_ancestry_unchanged():
    result = inject_scope_into_context(ScopeSet(), "existing ancestry")
    assert result == "existing ancestry"
