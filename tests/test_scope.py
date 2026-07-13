"""Tests for scope generation (Phase 65 minimum viable experiment)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scope import (
    Deliverable,
    ResolvedIntent,
    ScopeSet,
    _looks_like_clarification,
    _parse_deliverable_line,
    _parse_proxy_response,
    _parse_resolved_intent_markdown,
    _parse_scope_markdown,
    generate_resolved_intent,
    generate_scope,
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


# inject_scope_into_context tests removed 2026-07-02 — function deleted (zero
# production callers). See docs/REFACTOR_PLAN.md Tier 1.

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


# ---------------------------------------------------------------------------
# Deliverable parsing
# ---------------------------------------------------------------------------

def test_parse_deliverable_line_full_form():
    d = _parse_deliverable_line(
        "cmd/server/main.go: HTTP server binary [preconditions: Go, gorilla/websocket]"
    )
    assert d.name == "cmd/server/main.go"
    assert d.description == "HTTP server binary"
    assert d.preconditions == ["Go", "gorilla/websocket"]


def test_parse_deliverable_line_no_preconditions():
    d = _parse_deliverable_line("web/index.html: browser entry page")
    assert d.name == "web/index.html"
    assert d.description == "browser entry page"
    assert d.preconditions == []


def test_parse_deliverable_line_bare_name():
    d = _parse_deliverable_line("docs/ARCHITECTURE.md")
    assert d.name == "docs/ARCHITECTURE.md"
    assert d.description == ""
    assert d.preconditions == []


def test_parse_deliverable_line_preconditions_only():
    d = _parse_deliverable_line("tool-name [preconditions: python3.12]")
    assert d.name == "tool-name"
    assert d.preconditions == ["python3.12"]


def test_deliverable_to_markdown_line_roundtrips():
    d = Deliverable(name="a.go", description="b c", preconditions=["Go"])
    line = d.to_markdown_line()
    assert line.startswith("- a.go")
    assert "b c" in line
    assert "preconditions: Go" in line


# ---------------------------------------------------------------------------
# Deliverable.shape (docs/ROUTING_AND_PROBE_SYNTHESIS_DESIGN.md Part B, "probe
# honesty" — B1: declare artifact kind at scope time instead of inferring it
# from keyword hits in prose at closure time)
# ---------------------------------------------------------------------------

def test_parse_deliverable_line_with_shape():
    d = _parse_deliverable_line(
        "cmd/server/main.go: HTTP server binary [preconditions: Go] [shape: runtime]"
    )
    assert d.name == "cmd/server/main.go"
    assert d.description == "HTTP server binary"
    assert d.preconditions == ["Go"]
    assert d.shape == "runtime"


def test_parse_deliverable_line_shape_before_preconditions():
    # Annotations may appear in either order.
    d = _parse_deliverable_line(
        "notes.md: written summary [shape: document] [preconditions: none]"
    )
    assert d.shape == "document"
    assert d.description == "written summary"


def test_parse_deliverable_line_shape_only():
    d = _parse_deliverable_line("ledger.json [shape: data]")
    assert d.name == "ledger.json"
    assert d.shape == "data"


def test_parse_deliverable_line_no_shape_annotation_is_none():
    d = _parse_deliverable_line("docs/ARCHITECTURE.md: architecture notes")
    assert d.shape is None


def test_parse_deliverable_line_unrecognized_shape_value_is_none():
    # An unrecognized value is dropped rather than trusted blindly.
    d = _parse_deliverable_line("thing.txt: a thing [shape: banana]")
    assert d.shape is None


def test_deliverable_to_markdown_line_includes_shape():
    d = Deliverable(name="a.go", description="b c", shape="runtime")
    line = d.to_markdown_line()
    assert "shape: runtime" in line


def test_deliverable_to_markdown_line_omits_shape_when_none():
    d = Deliverable(name="a.go", description="b c")
    assert "shape:" not in d.to_markdown_line()


# ---------------------------------------------------------------------------
# ResolvedIntent
# ---------------------------------------------------------------------------

_FULL_RESPONSE_WITH_DELIVERABLES = """## Failure Modes
- server hangs on WebSocket close
- session state not persisted across reconnect

## In Scope
- WebSocket protocol definition
- Per-session state persistence

## Out of Scope
- Authentication
- Persistent chat history

## Deliverables
- cmd/server/main.go: HTTP server binary [preconditions: Go toolchain, gorilla/websocket]
- web/index.html: browser entry point [preconditions: none]
- internal/session/state.go: per-connection session state
"""


def test_parse_resolved_intent_markdown_captures_all_sections():
    intent = _parse_resolved_intent_markdown(_FULL_RESPONSE_WITH_DELIVERABLES)
    assert not intent.is_empty()
    # Scope piece intact
    assert len(intent.scope.failure_modes) == 2
    assert len(intent.scope.in_scope) == 2
    assert len(intent.scope.out_of_scope) == 2
    # Deliverables
    assert len(intent.deliverables) == 3
    assert intent.deliverables[0].name == "cmd/server/main.go"
    assert "Go toolchain" in intent.deliverables[0].preconditions
    assert intent.deliverables[2].preconditions == []  # no annotation on 3rd


_RESPONSE_WITH_SHAPED_DELIVERABLES = """## Failure Modes
- server hangs

## In Scope
- server

## Out of Scope
- auth

## Deliverables
- cmd/server/main.go: HTTP server binary [preconditions: Go] [shape: runtime]
- docs/README.md: usage notes [shape: document]
- data/index.json: search index [shape: data]
- unshaped.txt: no shape declared
"""


def test_parse_resolved_intent_markdown_captures_shape():
    intent = _parse_resolved_intent_markdown(_RESPONSE_WITH_SHAPED_DELIVERABLES)
    shapes = {d.name: d.shape for d in intent.deliverables}
    assert shapes["cmd/server/main.go"] == "runtime"
    assert shapes["docs/README.md"] == "document"
    assert shapes["data/index.json"] == "data"
    assert shapes["unshaped.txt"] is None


def test_parse_resolved_intent_markdown_empty_input():
    intent = _parse_resolved_intent_markdown("")
    assert intent.is_empty()
    assert intent.scope.raw_text == ""


def test_parse_resolved_intent_drops_malformed_deliverable_lines():
    text = _FULL_RESPONSE_WITH_DELIVERABLES + "\n- [preconditions: only]\n"
    intent = _parse_resolved_intent_markdown(text)
    # The malformed line (no name) should be dropped — still 3 deliverables.
    assert len(intent.deliverables) == 3


def test_resolved_intent_to_markdown_renders_both_sections():
    intent = ResolvedIntent(
        scope=ScopeSet(
            failure_modes=["x"],
            in_scope=["y"],
            out_of_scope=["z"],
        ),
        deliverables=[Deliverable(name="a.go", description="the thing")],
    )
    md = intent.to_markdown()
    assert "Scope (goal bounds)" in md
    assert "## Deliverables" in md
    assert "- a.go: the thing" in md


def test_resolved_intent_is_empty_when_neither_scope_nor_deliverables():
    assert ResolvedIntent().is_empty()


def test_resolved_intent_is_not_empty_with_only_deliverables():
    intent = ResolvedIntent(deliverables=[Deliverable(name="a.go")])
    assert not intent.is_empty()


def test_parse_scope_markdown_ignores_deliverables_section():
    """Back-compat: old generate_scope callers still get a plain ScopeSet
    even when the LLM emits a deliverables block — no crashes, no bleed."""
    scope = _parse_scope_markdown(_FULL_RESPONSE_WITH_DELIVERABLES)
    assert not scope.is_empty()
    assert len(scope.failure_modes) == 2
    # ScopeSet has no deliverables field — just verify the shape is unchanged.
    assert not hasattr(scope, "deliverables")


# ---------------------------------------------------------------------------
# generate_resolved_intent + injection
# ---------------------------------------------------------------------------

def test_generate_resolved_intent_returns_both_scope_and_deliverables():
    adapter = _FakeAdapter(response_text=_FULL_RESPONSE_WITH_DELIVERABLES)
    intent = generate_resolved_intent("build a headless server", adapter)
    assert intent is not None
    assert not intent.is_empty()
    assert len(intent.deliverables) == 3
    assert len(intent.scope.failure_modes) == 2


def test_generate_resolved_intent_none_on_missing_goal():
    adapter = _FakeAdapter(response_text=_FULL_RESPONSE_WITH_DELIVERABLES)
    assert generate_resolved_intent("", adapter) is None


def test_generate_resolved_intent_none_on_adapter_failure():
    adapter = _FakeAdapter(raise_on_complete=True)
    assert generate_resolved_intent("build X", adapter) is None


def test_generate_resolved_intent_empty_deliverables_scope_only():
    # Scope sections present but no deliverables — intent should still be
    # non-empty (scope alone is useful).
    scope_only = """## Failure Modes
- x

## In Scope
- y

## Out of Scope
- z
"""
    adapter = _FakeAdapter(response_text=scope_only)
    intent = generate_resolved_intent("build X", adapter)
    assert intent is not None
    assert not intent.is_empty()
    assert intent.deliverables == []
    assert len(intent.scope.failure_modes) == 1


# inject_resolved_intent_into_context tests removed 2026-07-02 — function
# deleted (zero production callers). See docs/REFACTOR_PLAN.md Tier 1.


def test_generate_resolved_intent_carries_proxy_resolution():
    # If the scope path went through the proxy (ambiguous goal → committed
    # interpretation), the ResolvedIntent should preserve that state on
    # its inner scope, so post-hoc audit can see what happened.
    adapter = _FakeAdapter(responses=[
        _CLARIFICATION_RESPONSE,    # first scope call: asks a question
        _PROXY_COMMITMENT,          # proxy commits to an interpretation
        _FULL_RESPONSE_WITH_DELIVERABLES,  # scope retry with commitment: succeeds
    ])
    intent = generate_resolved_intent("do something ambiguous", adapter)
    assert intent is not None
    assert not intent.is_empty()
    assert intent.scope.proxy_resolution  # non-empty dict
    assert "interpretation" in intent.scope.proxy_resolution
    # And the deliverables from the retry response should be captured.
    assert len(intent.deliverables) == 3

