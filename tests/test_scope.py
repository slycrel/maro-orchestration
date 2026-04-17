"""Tests for scope generation (Phase 65 minimum viable experiment)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scope import (
    ScopeSet,
    _looks_like_clarification,
    _parse_proxy_response,
    _parse_scope_markdown,
    generate_scope,
    inject_scope_into_context,
    resolve_ambiguity_via_proxy,
)


# ---------------------------------------------------------------------------
# Fake adapter
# ---------------------------------------------------------------------------

class _FakeAdapter:
    """Minimal adapter returning a canned response.

    If ``responses`` is supplied it queues per-call responses (round-robin
    after the queue is exhausted). Otherwise ``response_text`` is returned on
    every call. This lets tests exercise the director-proxy retry path where
    three LLM calls can happen in one generate_scope invocation (scope ->
    proxy -> scope retry).
    """

    def __init__(self, response_text: str = "", raise_on_complete: bool = False,
                 responses=None):
        self.response_text = response_text
        self.raise_on_complete = raise_on_complete
        self.responses = list(responses) if responses else None
        self.calls: list = []

    def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if self.raise_on_complete:
            raise RuntimeError("simulated adapter failure")
        from llm import LLMResponse
        if self.responses:
            idx = min(len(self.calls) - 1, len(self.responses) - 1)
            text = self.responses[idx]
        else:
            text = self.response_text
        return LLMResponse(
            content=text,
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


# ---------------------------------------------------------------------------
# Director-proxy fallback (clarification-style response handling)
# ---------------------------------------------------------------------------

_CLARIFICATION_RESPONSE = """\
I can see the `headless-server` branch already exists with WebSocket server
and browser client scaffolding. Let me clarify what you're after:

Are you asking to:
1. Finalize the existing headless-server branch?
2. Review what should be in this branch?
3. Start fresh from a different base?
"""

_PROXY_COMMITMENT = """\
INTERPRETATION: Finalize the existing headless-server branch — commit outstanding changes, verify it builds, and push.
REASON: The branch already exists with substantial implementation; shipping incomplete work matches the user's concrete phrasing better than a review or restart.
"""


def test_looks_like_clarification_true_for_question_prose():
    assert _looks_like_clarification(_CLARIFICATION_RESPONSE)


def test_looks_like_clarification_false_for_empty():
    assert not _looks_like_clarification("")
    assert not _looks_like_clarification("   ")


def test_looks_like_clarification_false_for_short_or_no_question():
    assert not _looks_like_clarification("no")
    assert not _looks_like_clarification("I refuse to answer.")


def test_parse_proxy_response_extracts_both_fields():
    parsed = _parse_proxy_response(_PROXY_COMMITMENT)
    assert parsed is not None
    assert "Finalize" in parsed["interpretation"]
    assert "already exists" in parsed["reason"]


def test_parse_proxy_response_tolerates_missing_reason():
    parsed = _parse_proxy_response("INTERPRETATION: ship the branch")
    assert parsed is not None
    assert parsed["interpretation"] == "ship the branch"
    assert parsed["reason"] == ""


def test_parse_proxy_response_rejects_non_matching_text():
    assert _parse_proxy_response("") is None
    assert _parse_proxy_response("I don't know") is None
    assert _parse_proxy_response("INTERPRETATION:") is None


def test_resolve_ambiguity_via_proxy_returns_parsed_commitment():
    adapter = _FakeAdapter(response_text=_PROXY_COMMITMENT)
    result = resolve_ambiguity_via_proxy(
        goal="create a branch for headless server",
        clarification_text=_CLARIFICATION_RESPONSE,
        ancestry_context="",
        adapter=adapter,
    )
    assert result is not None
    assert "Finalize" in result["interpretation"]


def test_resolve_ambiguity_via_proxy_returns_none_on_adapter_failure():
    adapter = _FakeAdapter(raise_on_complete=True)
    result = resolve_ambiguity_via_proxy(
        goal="build X",
        clarification_text=_CLARIFICATION_RESPONSE,
        ancestry_context="",
        adapter=adapter,
    )
    assert result is None


def test_resolve_ambiguity_via_proxy_returns_none_on_unparseable_response():
    adapter = _FakeAdapter(response_text="I cannot answer that.")
    result = resolve_ambiguity_via_proxy(
        goal="build X",
        clarification_text=_CLARIFICATION_RESPONSE,
        ancestry_context="",
        adapter=adapter,
    )
    assert result is None


def test_generate_scope_retries_with_proxy_on_clarification_response():
    # First call: scope generator returns a clarification question.
    # Second call: director-proxy commits to an interpretation.
    # Third call: scope generator retry with augmented goal parses cleanly.
    adapter = _FakeAdapter(responses=[
        _CLARIFICATION_RESPONSE,
        _PROXY_COMMITMENT,
        _GOOD_MARKDOWN,
    ])
    scope = generate_scope("create a branch for headless server", adapter)
    assert scope is not None
    assert not scope.is_empty()
    assert len(adapter.calls) == 3
    assert scope.proxy_resolution
    assert "Finalize" in scope.proxy_resolution["interpretation"]
    assert "clarification_question" in scope.proxy_resolution


def test_generate_scope_falls_back_without_retry_when_proxy_disabled():
    # allow_proxy_fallback=False skips the escalation even when the response
    # looks like a clarification. Used on the retry call to prevent recursion.
    adapter = _FakeAdapter(response_text=_CLARIFICATION_RESPONSE)
    scope = generate_scope("build X", adapter, allow_proxy_fallback=False)
    assert scope is not None
    assert scope.is_empty()
    assert len(adapter.calls) == 1  # No proxy call, no retry.


def test_generate_scope_retry_does_not_recurse_if_second_scope_also_punts():
    # Proxy commits, but retry still returns a clarification response. Should
    # NOT recursively call proxy again — second call exits with empty scope
    # plus the raw retry text.
    adapter = _FakeAdapter(responses=[
        _CLARIFICATION_RESPONSE,  # first scope call
        _PROXY_COMMITMENT,        # proxy call
        _CLARIFICATION_RESPONSE,  # scope retry (still punts)
    ])
    scope = generate_scope("build X", adapter)
    assert scope is not None
    assert scope.is_empty()  # retry failed, no recursion
    assert len(adapter.calls) == 3  # exactly 3 — no recursive proxy pass


def test_generate_scope_skips_proxy_on_garbage_response_without_question():
    # Empty-scope response with no question mark should NOT route to proxy —
    # that's a different failure class (adapter/model problem, not ambiguity).
    adapter = _FakeAdapter(response_text="[internal error — generation stopped]")
    scope = generate_scope("build X", adapter)
    assert scope is not None
    assert scope.is_empty()
    assert len(adapter.calls) == 1  # no proxy escalation
