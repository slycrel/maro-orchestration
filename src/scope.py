"""Scope + resolved-intent — the thread the driver watches.

Originally Phase 65's minimum viable experiment (scope only): one LLM call
before planner.decompose() produces an inversion-derived scope.

Expanded 2026-04-23 to produce a `ResolvedIntent` — per
`docs/INTENT_RESOLUTION_DESIGN.md` and `docs/DRIVER_AND_WATCHER.md` #4
("plan-creation as its own step"), this is the durable artifact that sits
between goal and decomposition. v0 adds a **deliverable map** (concrete
artifacts the goal implies, with preconditions). Future versions add
assumed / verified / unknown-but-accepted sections and cross-turn
agenda-state carryover.

The 2026-04-22 scope A/B showed scope injection structurally compresses
planner output (8 steps vs 15-40); widening the thread with deliverables
tests whether committing to concrete artifacts up front makes closure's
"did we actually build the right thing" question answerable against a
checked-in map instead of a post-hoc grep.

Deferred explicitly (logged at runtime with `[scope-deferred]` markers):
- Persona triad (PM/engineer/architect) — using single generalist
- Human gate — scope used without review
- Violation detection — scope injected but not enforced
- Lifecycle (revise/except/break) — scope is immutable after set
- Retrieval-based injection — scope goes into ancestry as one block
- Cross-goal memory — scope recorded but nothing retrieves it
- Side-quest DAG for unknowns — v0 is one-shot; INTENT_RESOLUTION_DESIGN
  says don't build this until we've run one by hand
- Cross-turn agenda-state tracking — thread is per-invocation for v0

See `docs/PHASE_65_IMPLEMENTATION_PLAN.md` and
`docs/INTENT_RESOLUTION_DESIGN.md` for the rationale.
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

Your job is to do three things, in order:

1. **Inversion pass**: enumerate 3-7 ways this specific goal would definitively fail.
   Not generic "bug risk" items — concrete, grounded failure modes that would
   make a reasonable reviewer say "this didn't actually work."

2. **Scope derivation**: from the failure modes, identify:
   - **In scope** — concrete things that must be done to avoid the failures (2-5 items)
   - **Out of scope** — things that could be pursued but explicitly aren't for this goal (2-5 items)

3. **Deliverable map**: list the concrete, checkable artifacts that must exist for the goal to be done.
   Files, commits, processes, endpoints — things someone else could point at afterward and say
   "yes, this is what we asked for." Include known preconditions (tools, dependencies, services)
   inline using the format `[preconditions: X, Y]`. Also classify what KIND of artifact each one
   is using `[shape: document|runtime|data]`:
   - `document` — a file meant to be read (docs, reports, config, source that isn't itself run
     as a service in this goal).
   - `runtime` — something that runs and can be exercised: a server, CLI, endpoint, websocket,
     background process, UI flow. Verifying this later requires actually running/hitting it, not
     just checking it exists.
   - `data` — a dataset, ledger, or index that's queried for content (not read like prose and
     not "run" like a program).
   2-6 items.

   For any quantitative result (a count, total, percentage, size, duration, or
   similar measurement), the deliverable description MUST state the measurement
   boundary and inclusion rule. For example: "recursive count of `*.md` under
   docs/, including nested directories and excluding symlink targets" — never
   just "markdown file count". Commit to one reasonable interpretation when the
   goal leaves the boundary implicit; that definition is part of the deliverable.

Output FORMAT — plain markdown with exactly these four headings:

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

## Deliverables
- <artifact name>: <one-line description> [preconditions: <tool or dep>, <...>] [shape: <document|runtime|data>]
- <artifact name>: <description> [preconditions: <...>] [shape: <...>]
- <...>

Be specific. "Add error handling" is not a failure mode. "If the WebSocket
connection drops mid-game, session state is lost" is. Same for scope:
"Support WebSocket reconnection with session recovery" is concrete;
"Handle errors well" is not. Same for deliverables: "cmd/server/main.go:
HTTP server binary serving /ws and /static/ [preconditions: Go toolchain,
gorilla/websocket] [shape: runtime]" is concrete; "working server" is not.
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
    # Set when the first scope pass returned a clarification question and the
    # director-proxy committed to one interpretation before a successful retry.
    # Keys: "interpretation", "reason", "clarification_question". Empty dict =
    # no proxy resolution happened (scope parsed on first try).
    proxy_resolution: dict = field(default_factory=dict)

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
# Deliverable + ResolvedIntent
# ---------------------------------------------------------------------------

_VALID_SHAPES = frozenset({"document", "runtime", "data"})


@dataclass
class Deliverable:
    """A concrete artifact the goal implies, with any known preconditions.

    `name` is the identifier (file path, commit, endpoint, etc.).
    `description` is one line of context.
    `preconditions` are tools or dependencies required for the deliverable
    to exist — used by closure's pre-flight to short-circuit INCONCLUSIVE
    verdicts rather than silent pass-throughs (see BACKLOG: closure
    silent-verification bug).
    `shape` (docs/ROUTING_AND_PROBE_SYNTHESIS_DESIGN.md Part B, "probe
    honesty") declares what kind of artifact this is, so closure can decide
    whether a behavioral probe is *required* instead of inferring it from
    keyword hits in prose. Three values, not two: "data" (a dataset/ledger/
    index probed by content queries) is distinct from "document" — a
    static grep against it is the right modality, unlike a "runtime"
    deliverable (server/CLI/endpoint) which needs a behavioral probe.
    None when the LLM didn't declare one (legacy/unshaped) — callers fall
    back to keyword-regex inference in that case.
    """
    name: str
    description: str = ""
    preconditions: List[str] = field(default_factory=list)
    shape: Optional[str] = None

    def to_markdown_line(self) -> str:
        pre = ""
        if self.preconditions:
            pre = f" [preconditions: {', '.join(self.preconditions)}]"
        shape = f" [shape: {self.shape}]" if self.shape else ""
        desc = f": {self.description}" if self.description else ""
        return f"- {self.name}{desc}{pre}{shape}"


@dataclass
class ResolvedIntent:
    """The thread the driver watches — what we know about the goal before decompose.

    v0 wraps `ScopeSet` and adds a deliverable map. Future fields (per
    `docs/INTENT_RESOLUTION_DESIGN.md`): `assumed`, `verified`,
    `unknown_but_accepted`, and cross-turn `open_agenda_items` for the
    godot-replay agenda-state-divergence finding.

    Keep ScopeSet as the inner record so existing callers that just want
    the scope view keep working — `resolved_intent.scope` is the ScopeSet
    they already know how to handle.
    """
    scope: ScopeSet = field(default_factory=ScopeSet)
    deliverables: List[Deliverable] = field(default_factory=list)
    raw_text: str = ""  # original LLM output — same payload as scope.raw_text

    def to_markdown(self) -> str:
        """Render the resolved intent as injectable markdown for planner context."""
        parts = []
        resolution = self.scope.proxy_resolution or {}
        interpretation = str(resolution.get("interpretation", "")).strip()
        reason = str(resolution.get("reason", "")).strip()
        if interpretation:
            parts.append("## Resolved interpretation (binding goal definition)")
            parts.append(f"- {interpretation}")
            if reason:
                parts.append(f"- Rationale: {reason}")
        if not self.scope.is_empty():
            parts.append(self.scope.to_markdown())
        if self.deliverables:
            parts.append("\n## Deliverables (concrete artifacts)")
            parts.extend(d.to_markdown_line() for d in self.deliverables)
        return "\n".join(parts) if parts else ""

    def is_empty(self) -> bool:
        """True when neither scope nor deliverables have content."""
        return self.scope.is_empty() and not self.deliverables


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_HEADING_PATTERN = re.compile(r"^#{1,4}\s*(.+?)\s*$", re.MULTILINE)


def _split_sections(text: str) -> dict:
    """Split a markdown blob into {section_key: [bullet_items]}.

    Headings can be ##/###/####, possibly with trailing colon. Recognized
    section keys: failure_modes, in_scope, out_of_scope, deliverables.
    Anything else is ignored. Shared by scope and resolved-intent parsers.
    """
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
        if "deliverable" in k or "artifact" in k:
            return "deliverables"
        return None

    for line in text.split("\n"):
        stripped = line.strip()
        m = _HEADING_PATTERN.match(line)
        if m:
            if current_key is not None:
                sections[current_key] = current_items
            current_key = _normalize(m.group(1))
            current_items = []
            continue
        if current_key is not None and (stripped.startswith("-") or stripped.startswith("*")):
            item = stripped.lstrip("-* ").strip()
            if item:
                current_items.append(item)
    if current_key is not None:
        sections[current_key] = current_items
    return sections


# `[preconditions: X, Y, Z]` — trailing annotation in deliverable bullets.
_PRECONDITIONS_RE = re.compile(r"\[preconditions?:\s*(.+?)\s*\]", re.IGNORECASE)
# `[shape: document|runtime|data]` — trailing annotation, parallel to
# preconditions. See Deliverable.shape docstring for the three-value split.
_SHAPE_RE = re.compile(r"\[shape:\s*(.+?)\s*\]", re.IGNORECASE)


def _parse_deliverable_line(item: str) -> Deliverable:
    """Parse a single deliverable bullet into a Deliverable.

    Format: `<name>: <description> [preconditions: X, Y] [shape: runtime]`
    - `name:` is the split point; if absent, the whole string is the name.
    - The preconditions/shape annotations can appear at the end of the
      description, in either order.
    - Tolerates missing description, missing preconditions/shape, or any
      subset alone.
    - An unrecognized shape value (not document/runtime/data) is dropped —
      treated the same as no annotation, since a value we can't classify
      against isn't worth pretending to trust.
    """
    if not item:
        return Deliverable(name="")
    # Extract preconditions/shape first so they don't pollute the description.
    preconditions: List[str] = []
    m = _PRECONDITIONS_RE.search(item)
    if m:
        pre_raw = m.group(1)
        preconditions = [p.strip() for p in pre_raw.split(",") if p.strip()]
        item = (item[:m.start()] + item[m.end():]).strip()
    shape: Optional[str] = None
    m = _SHAPE_RE.search(item)
    if m:
        _shape_raw = m.group(1).strip().lower()
        shape = _shape_raw if _shape_raw in _VALID_SHAPES else None
        item = (item[:m.start()] + item[m.end():]).strip()
    # Split name: description.
    if ":" in item:
        name, _, desc = item.partition(":")
        return Deliverable(
            name=name.strip(),
            description=desc.strip(),
            preconditions=preconditions,
            shape=shape,
        )
    return Deliverable(name=item.strip(), preconditions=preconditions, shape=shape)


def _parse_scope_markdown(text: str) -> ScopeSet:
    """Parse the LLM's markdown response into a ScopeSet.

    Tolerates variations: extra whitespace, different heading levels,
    alternate phrasings like "Failure Modes:" or "## FAILURE MODES".

    Returns an empty ScopeSet if nothing parseable — caller decides whether
    that means "skip injection" or "warn and proceed without scope."

    Deliverables section (if present) is ignored here; use
    `_parse_resolved_intent_markdown` to capture it.
    """
    if not text or not text.strip():
        return ScopeSet(raw_text=text or "")

    sections = _split_sections(text)
    return ScopeSet(
        failure_modes=sections.get("failure_modes", []),
        in_scope=sections.get("in_scope", []),
        out_of_scope=sections.get("out_of_scope", []),
        raw_text=text,
    )


def _parse_resolved_intent_markdown(text: str) -> "ResolvedIntent":
    """Parse the LLM's markdown into a ResolvedIntent (scope + deliverables)."""
    if not text or not text.strip():
        return ResolvedIntent(scope=ScopeSet(raw_text=text or ""), raw_text=text or "")
    sections = _split_sections(text)
    scope = ScopeSet(
        failure_modes=sections.get("failure_modes", []),
        in_scope=sections.get("in_scope", []),
        out_of_scope=sections.get("out_of_scope", []),
        raw_text=text,
    )
    deliverables = [
        _parse_deliverable_line(line)
        for line in sections.get("deliverables", [])
    ]
    # Drop deliverables with empty names (malformed lines).
    deliverables = [d for d in deliverables if d.name]
    return ResolvedIntent(scope=scope, deliverables=deliverables, raw_text=text)


# ---------------------------------------------------------------------------
# Director-proxy fallback for clarification-style scope responses
# ---------------------------------------------------------------------------

def _looks_like_clarification(raw_text: str) -> bool:
    """True when the LLM returned a question instead of structured markdown.

    Heuristic, intentionally narrow: only treat as clarification when there's
    actual prose with a question mark. Empty responses or garbage without a
    question are a different failure class and should not route through the
    proxy — they indicate an adapter/model problem, not an ambiguity problem.
    """
    if not raw_text:
        return False
    text = raw_text.strip()
    if len(text) < 30 or len(text) > 4000:
        return False
    return "?" in text


_PROXY_RESPONSE_RE = re.compile(
    r"INTERPRETATION\s*:\s*(.+?)\s*(?:\n+REASON\s*:\s*(.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_proxy_response(content: str) -> Optional[dict]:
    """Extract INTERPRETATION / REASON from director-proxy output."""
    if not content:
        return None
    # Find the last INTERPRETATION: line — proxies sometimes preamble despite
    # instructions, and the commitment is always at the end.
    m = _PROXY_RESPONSE_RE.search(content.strip())
    if not m:
        return None
    interp = (m.group(1) or "").strip()
    reason = (m.group(2) or "").strip()
    if not interp:
        return None
    return {"interpretation": interp, "reason": reason}


def resolve_ambiguity_via_proxy(
    goal: str,
    clarification_text: str,
    ancestry_context: str,
    adapter,
) -> Optional[dict]:
    """Ask director-proxy persona to commit to one interpretation.

    Returns {"interpretation": ..., "reason": ...} on success, or None if the
    proxy persona isn't available, the LLM call fails, or the response doesn't
    parse. Callers should treat None as "proceed without scope."
    """
    if not goal or not clarification_text or not adapter:
        return None
    try:
        from persona import PersonaRegistry, build_persona_system_prompt
    except Exception as exc:
        log.warning("scope.proxy: persona module not importable: %s", exc)
        return None

    try:
        registry = PersonaRegistry()
        spec = registry.load("director-proxy")
    except Exception as exc:
        log.warning("scope.proxy: PersonaRegistry failed: %s", exc)
        return None
    if spec is None:
        log.warning("scope.proxy: director-proxy persona not found")
        return None

    system_prompt = build_persona_system_prompt(spec, goal=goal)

    # Append the resolve_ambiguity skill body so the how-to is in context.
    try:
        from skill_loader import SkillLoader
        skill_body = SkillLoader().load_full("resolve_ambiguity")
        if skill_body:
            system_prompt = system_prompt + "\n\n---\n\n" + skill_body
    except Exception as exc:
        log.debug("scope.proxy: could not load resolve_ambiguity skill: %s", exc)

    ancestry_block = (ancestry_context or "").strip() or "(no ancestry available — CLI or top-level goal)"
    user_msg = (
        f"Goal (verbatim):\n{goal}\n\n"
        f"The scope generator returned a clarification question instead of "
        f"committing to an interpretation. Its full response:\n\n"
        f"{clarification_text.strip()}\n\n"
        f"Context / ancestry:\n{ancestry_block}\n\n"
        f"Commit to one interpretation now. Emit exactly:\n"
        f"INTERPRETATION: <one imperative sentence>\n"
        f"REASON: <one justification sentence>"
    )

    try:
        from llm import LLMMessage
        resp = adapter.complete(
            [LLMMessage("system", system_prompt), LLMMessage("user", user_msg)],
            max_tokens=300,
            temperature=0.2,
            no_tools=True,
            purpose="scope",
        )
    except Exception as exc:
        log.warning("scope.proxy: adapter.complete failed: %s", exc)
        return None

    try:
        from llm_parse import content_or_empty
        content = content_or_empty(resp)
    except Exception as exc:
        log.warning("scope.proxy: could not extract content: %s", exc)
        return None

    parsed = _parse_proxy_response(content)
    if parsed is None:
        log.warning("scope.proxy: response did not match INTERPRETATION/REASON format; raw=%r",
                    (content or "")[:200])
        return None
    log.info("scope.proxy: committed interpretation=%r (reason=%r)",
             parsed["interpretation"][:120], parsed["reason"][:120])
    return parsed


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_resolved_intent(
    goal: str,
    adapter,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.3,
    ancestry_context: str = "",
    allow_proxy_fallback: bool = True,
) -> Optional["ResolvedIntent"]:
    """Generate a resolved intent (scope + deliverable map) for `goal`.

    One LLM call. Returns None on any failure, a ResolvedIntent on success.
    If the scope sections parse but deliverables don't, you still get back a
    ResolvedIntent with an empty deliverables list — inject scope alone and
    let the planner proceed.

    This is the successor to `generate_scope()` — per
    `docs/INTENT_RESOLUTION_DESIGN.md` and `docs/DRIVER_AND_WATCHER.md`,
    it's the "plan-creation as its own step" artifact that sits between goal
    and decomposition.
    """
    if not goal or not adapter:
        return None

    scope = generate_scope(
        goal, adapter,
        max_tokens=max_tokens,
        temperature=temperature,
        ancestry_context=ancestry_context,
        allow_proxy_fallback=allow_proxy_fallback,
    )
    if scope is None:
        return None
    # Pick deliverables out of the same raw response scope came from. Cheap:
    # no extra LLM round-trip. We keep the scope ScopeSet as-is (not re-parsed)
    # so that test double patches on generate_scope still see their returned
    # values flow through unchanged.
    sections = _split_sections(scope.raw_text) if scope.raw_text else {}
    deliverables = [
        _parse_deliverable_line(line)
        for line in sections.get("deliverables", [])
    ]
    deliverables = [d for d in deliverables if d.name]
    intent = ResolvedIntent(
        scope=scope,
        deliverables=deliverables,
        raw_text=scope.raw_text,
    )
    if intent.deliverables:
        log.info(
            "resolved_intent: parsed %d deliverable(s) alongside scope",
            len(intent.deliverables),
        )
    else:
        log.info(
            "resolved_intent: no deliverables parsed; scope-only intent "
            "(prompt may need tightening or goal is unusual)"
        )
    return intent


def generate_scope(
    goal: str,
    adapter,
    *,
    max_tokens: int = 1200,
    temperature: float = 0.3,
    ancestry_context: str = "",
    allow_proxy_fallback: bool = True,
) -> Optional[ScopeSet]:
    """Generate a scope for `goal` via a single-call inversion pass.

    Non-fatal: returns None on any failure. Never blocks the caller.

    The call is single-persona (generalist) — the triad (PM/engineer/architect)
    is deferred until A/B signal justifies the 3x cost.

    Note: the underlying prompt now asks for four sections (failure modes,
    in/out of scope, deliverables). `generate_scope` returns only the scope
    view (deliverables are silently dropped); callers who want the full
    thread should use `generate_resolved_intent()` instead.
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
            no_tools=True,
            purpose="scope",
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
        # Parse failure. If the response looks like the LLM asked for
        # clarification rather than producing garbage, route it to the
        # director-proxy persona to commit to one interpretation, then retry
        # scope with that interpretation baked into the goal context.
        if allow_proxy_fallback and _looks_like_clarification(content):
            log.info("scope: response looks like clarification, escalating to director-proxy")
            resolution = resolve_ambiguity_via_proxy(
                goal=goal,
                clarification_text=content,
                ancestry_context=ancestry_context,
                adapter=adapter,
            )
            if resolution is not None:
                # Retry scope with the committed interpretation. Disable the
                # proxy fallback on the retry so we can't recurse if the LLM
                # keeps punting.
                augmented_goal = (
                    f"{goal}\n\n"
                    f"(Interpretation committed by director-proxy: "
                    f"{resolution['interpretation']})"
                )
                retry = generate_scope(
                    augmented_goal, adapter,
                    max_tokens=max_tokens, temperature=temperature,
                    ancestry_context=ancestry_context,
                    allow_proxy_fallback=False,
                )
                if retry is not None and not retry.is_empty():
                    retry.proxy_resolution = {
                        **resolution,
                        "clarification_question": content.strip()[:800],
                    }
                    log.info(
                        "scope: director-proxy resolved ambiguity, retry produced "
                        "%d failure modes, %d in-scope, %d out-of-scope",
                        len(retry.failure_modes), len(retry.in_scope),
                        len(retry.out_of_scope),
                    )
                    return retry
                log.warning("scope: retry after proxy resolution still did not parse")

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


# inject_scope_into_context / inject_resolved_intent_into_context removed
# 2026-07-02 — zero production callers, test-only. See docs/REFACTOR_PLAN.md
# Tier 1.
