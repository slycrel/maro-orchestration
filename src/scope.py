"""Scope generation — inversion-driven bounding of the solution space.

Phase 65 minimum viable experiment. One LLM call before planner.decompose()
produces a scope: failure modes → in-scope / out-of-scope derivation.

The hypothesis: having an explicit scope in planning context produces
measurably better plans than unbounded decomposition. This module tests
that with the smallest possible implementation — everything else in the
design is deferred until signal justifies it.

Deferred explicitly (logged at runtime with `[scope-deferred]` markers):
- Persona triad (PM/engineer/architect) — using single generalist
- Human gate — scope used without review
- Violation detection — scope injected but not enforced
- Lifecycle (revise/except/break) — scope is immutable after set
- Retrieval-based injection — scope goes into ancestry as one block
- Cross-goal memory — scope recorded but nothing retrieves it

See `docs/PHASE_65_IMPLEMENTATION_PLAN.md` for the rationale.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inversion prompt
# ---------------------------------------------------------------------------

_SCOPE_SYSTEM = """You are helping bound the solution space for a goal before work begins.

Your job is to do two things, in order:

1. **Inversion pass**: enumerate 3-7 ways this specific goal would definitively fail.
   Not generic "bug risk" items — concrete, grounded failure modes that would
   make a reasonable reviewer say "this didn't actually work."

2. **Scope derivation**: from the failure modes, identify:
   - **In scope** — concrete things that must be done to avoid the failures (2-5 items)
   - **Out of scope** — things that could be pursued but explicitly aren't for this goal (2-5 items)

Output FORMAT — plain markdown with exactly these three headings:

## Failure Modes
- <mode 1, specific to this goal>
- <mode 2>
- <...>

## In Scope
- <concrete thing we commit to doing>
- <...>

## Out of Scope
- <concrete thing we're NOT pursuing>
- <...>

Be specific. "Add error handling" is not a failure mode. "If the WebSocket
connection drops mid-game, session state is lost" is. Same for scope:
"Support WebSocket reconnection with session recovery" is concrete;
"Handle errors well" is not.
"""


# ---------------------------------------------------------------------------
# ScopeSet
# ---------------------------------------------------------------------------

@dataclass
class ScopeSet:
    """The scope derived from an inversion pass on a goal."""
    failure_modes: List[str] = field(default_factory=list)
    in_scope: List[str] = field(default_factory=list)
    out_of_scope: List[str] = field(default_factory=list)
    raw_text: str = ""  # the original LLM output, for audit/debug

    def to_markdown(self) -> str:
        """Render the scope as injectable markdown for planner context."""
        parts = ["## Scope (goal bounds)"]
        if self.failure_modes:
            parts.append("\n### Failure modes to avoid")
            parts.extend(f"- {m}" for m in self.failure_modes)
        if self.in_scope:
            parts.append("\n### In scope")
            parts.extend(f"- {m}" for m in self.in_scope)
        if self.out_of_scope:
            parts.append("\n### Out of scope")
            parts.extend(f"- {m}" for m in self.out_of_scope)
        return "\n".join(parts)

    def is_empty(self) -> bool:
        """True when the scope has no content — treat as not-generated."""
        return not (self.failure_modes or self.in_scope or self.out_of_scope)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_HEADING_PATTERN = re.compile(r"^#{1,4}\s*(.+?)\s*$", re.MULTILINE)


def _parse_scope_markdown(text: str) -> ScopeSet:
    """Parse the LLM's markdown response into a ScopeSet.

    Tolerates variations: extra whitespace, different heading levels,
    alternate phrasings like "Failure Modes:" or "## FAILURE MODES".

    Returns an empty ScopeSet if nothing parseable — caller decides whether
    that means "skip injection" or "warn and proceed without scope."
    """
    if not text or not text.strip():
        return ScopeSet(raw_text=text or "")

    # Split into sections by heading. Headings can be ## or ###.
    sections: dict = {}
    current_key: Optional[str] = None
    current_items: List[str] = []

    def _normalize(key: str) -> Optional[str]:
        k = key.lower().strip().rstrip(":")
        if "failure" in k or "mode" in k:
            return "failure_modes"
        if "out of scope" in k or "out-of-scope" in k or "outofscope" in k:
            return "out_of_scope"
        if "in scope" in k or "in-scope" in k or "inscope" in k:
            return "in_scope"
        return None

    for line in text.split("\n"):
        stripped = line.strip()
        # Heading line
        m = _HEADING_PATTERN.match(line)
        if m:
            # Flush previous section
            if current_key is not None:
                sections[current_key] = current_items
            current_key = _normalize(m.group(1))
            current_items = []
            continue
        # Bullet line inside a section
        if current_key is not None and (stripped.startswith("-") or stripped.startswith("*")):
            item = stripped.lstrip("-* ").strip()
            if item:
                current_items.append(item)
    # Final section
    if current_key is not None:
        sections[current_key] = current_items

    return ScopeSet(
        failure_modes=sections.get("failure_modes", []),
        in_scope=sections.get("in_scope", []),
        out_of_scope=sections.get("out_of_scope", []),
        raw_text=text,
    )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_scope(
    goal: str,
    adapter,
    *,
    max_tokens: int = 800,
    temperature: float = 0.3,
) -> Optional[ScopeSet]:
    """Generate a scope for `goal` via a single-call inversion pass.

    Non-fatal: returns None on any failure. Never blocks the caller.

    The call is single-persona (generalist) — the triad (PM/engineer/architect)
    is deferred until A/B signal justifies the 3x cost.
    """
    if not goal or not adapter:
        return None

    # [scope-deferred] markers: record what this minimal version skips, so
    # expanding the implementation later can grep for these to find all
    # the decisions we punted on.
    log.info("[scope-deferred] triad: using single generalist inversion, "
             "multi-persona rotation deferred")
    log.info("[scope-deferred] lifecycle: scope immutable after set, "
             "director revise/except/break deferred")
    log.info("[scope-deferred] retrieval: scope fully injected as block, "
             "per-step relevance deferred")
    log.info("[scope-deferred] memory: scope recorded but no cross-goal "
             "retrieval, Phase D deferred")

    try:
        from llm import LLMMessage
        resp = adapter.complete(
            [
                LLMMessage("system", _SCOPE_SYSTEM),
                LLMMessage("user", f"Goal: {goal}"),
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:
        log.warning("scope: adapter.complete failed: %s", exc)
        return None

    try:
        from llm_parse import content_or_empty
        content = content_or_empty(resp)
    except Exception as exc:
        log.warning("scope: could not extract content from response: %s", exc)
        return None

    if not content or not content.strip():
        log.warning("scope: LLM returned empty content, skipping scope injection")
        return None

    scope = _parse_scope_markdown(content)
    if scope.is_empty():
        # Return the empty ScopeSet (with raw_text populated) so the caller
        # can persist the raw LLM output for debugging. `is_empty()` still
        # flags "don't inject into planner context" — this is about keeping
        # the evidence, not about changing injection behaviour.
        log.warning("scope: LLM response had no parseable sections, returning raw for debug")
        return scope

    log.info(
        "scope: generated %d failure modes, %d in-scope, %d out-of-scope items",
        len(scope.failure_modes), len(scope.in_scope), len(scope.out_of_scope),
    )
    return scope


# ---------------------------------------------------------------------------
# Injection helper
# ---------------------------------------------------------------------------

def inject_scope_into_context(scope: Optional[ScopeSet], ancestry_context_extra: str) -> str:
    """Append scope markdown to an existing ancestry_context_extra string.

    Returns the ancestry with scope appended. If scope is None or empty,
    returns the ancestry unchanged.
    """
    if not scope or scope.is_empty():
        return ancestry_context_extra

    scope_block = scope.to_markdown()
    if ancestry_context_extra:
        return f"{ancestry_context_extra}\n\n{scope_block}"
    return scope_block
