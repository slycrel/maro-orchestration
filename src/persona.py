#!/usr/bin/env python3
"""Phase 20: Persona System — Modular, Composable Agent Identities.

Personas are composable data primitives: YAML frontmatter + markdown body.
Composition beats inheritance (Jeremy + Grok confirmed). No subclassing —
personas combine by merging their spec fields and system prompt sections.

File format (personas/*.md with optional YAML frontmatter):
    ---
    name: researcher
    role: Research Assistant
    model_tier: power
    tool_access: [web_search, read_file, write_file]
    memory_scope: session
    communication_style: analytical, source-grounded, crisp
    hooks: []
    composes: []
    ---
    # Persona: Research Assistant
    ... markdown system prompt body ...

Fields:
    name            short slug (must match filename stem)
    role            human-readable role name
    model_tier      "power" | "mid" | "cheap" (maps to MODEL_POWER etc.)
    tool_access     list of allowed tool names (empty = all allowed)
    memory_scope    "session" | "project" | "global"
    communication_style  one-line description baked into system prompt header
    hooks           list of hook names to register when this persona is active
    composes        list of other persona names to compose with (applied in order)

Composition:
    compose_persona("researcher", "skepticism") produces a new PersonaSpec where:
    - system_prompt = researcher.system_prompt + skepticism additions
    - tool_access = union(researcher.tool_access, skepticism.tool_access)
    - hooks = researcher.hooks + skepticism.hooks
    - model_tier = last non-default wins (overrides cascade)
    - memory_scope = last non-default wins

Usage:
    from persona import PersonaRegistry, compose_persona, spawn_persona
    registry = PersonaRegistry()
    spec = registry.load("researcher")
    combined = compose_persona("researcher", "skepticism", registry=registry)
    result = spawn_persona("researcher", "What are the best trading strategies?")
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class PersonaSpec:
    """A loaded, validated persona specification."""
    name: str
    role: str
    model_tier: str                # "power" | "mid" | "cheap"
    tool_access: List[str]         # empty list = all tools allowed
    memory_scope: str              # "session" | "project" | "global"
    communication_style: str
    system_prompt: str             # rendered markdown body
    hooks: List[str]               # hook names to register
    composes: List[str]            # other persona names composed into this one
    source_file: str = ""          # path to source .md file


@dataclass
class SpawnResult:
    """Result of a spawned persona execution."""
    persona_name: str
    goal: str
    status: str                    # "done" | "stuck" | "dry_run"
    summary: str
    artifacts: List[str] = field(default_factory=list)
    steps_taken: int = 0
    model_tier: str = "mid"
    memory_scope: str = "session"


# ---------------------------------------------------------------------------
# Persona spec parser
# ---------------------------------------------------------------------------

_DEFAULT_FRONTMATTER: Dict[str, Any] = {
    "name": "",
    "role": "General Assistant",
    "model_tier": "mid",
    "tool_access": [],
    "memory_scope": "session",
    "communication_style": "direct and concise",
    "hooks": [],
    "composes": [],
}


def _parse_persona_file(path: Path) -> PersonaSpec:
    """Parse a persona .md file with optional YAML frontmatter.

    If no frontmatter present, treats the entire file as the system prompt
    and infers name from the filename stem.
    """
    content = path.read_text(encoding="utf-8")
    meta = dict(_DEFAULT_FRONTMATTER)
    body = content

    # Parse YAML frontmatter between --- markers
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            fm_text = content[3:end].strip()
            body = content[end + 4:].strip()
            try:
                import yaml
                parsed = yaml.safe_load(fm_text)
                if isinstance(parsed, dict):
                    meta.update(parsed)
            except Exception:
                pass  # malformed frontmatter → use defaults

    # Infer name from filename if not set
    if not meta.get("name"):
        meta["name"] = path.stem

    # Ensure list fields are actually lists
    for list_field in ("tool_access", "hooks", "composes"):
        if isinstance(meta.get(list_field), str):
            meta[list_field] = [s.strip() for s in meta[list_field].split(",") if s.strip()]
        elif not isinstance(meta.get(list_field), list):
            meta[list_field] = []

    return PersonaSpec(
        name=str(meta.get("name", path.stem)),
        role=str(meta.get("role", "General Assistant")),
        model_tier=str(meta.get("model_tier", "mid")),
        tool_access=list(meta.get("tool_access", [])),
        memory_scope=str(meta.get("memory_scope", "session")),
        communication_style=str(meta.get("communication_style", "direct and concise")),
        system_prompt=body,
        hooks=list(meta.get("hooks", [])),
        composes=list(meta.get("composes", [])),
        source_file=str(path),
    )


# ---------------------------------------------------------------------------
# Persona Registry
# ---------------------------------------------------------------------------

class PersonaRegistry:
    """Scans the personas/ directory and provides load/list operations."""

    def __init__(self, personas_dir: Optional[Path] = None):
        if personas_dir is None:
            try:
                from orch import orch_root
                personas_dir = orch_root() / "personas"
            except Exception:
                personas_dir = Path.cwd() / "personas"
        self._dir = personas_dir
        self._cache: Dict[str, PersonaSpec] = {}

    def _persona_files(self) -> List[Path]:
        if not self._dir.exists():
            return []
        return sorted(p for p in self._dir.glob("*.md") if p.name != "README.md")

    def list(self) -> List[str]:
        """Return list of available persona names (sorted)."""
        names = []
        for p in self._persona_files():
            try:
                spec = _parse_persona_file(p)
                names.append(spec.name)
            except Exception:
                names.append(p.stem)
        return sorted(names)

    def load(self, name: str) -> Optional[PersonaSpec]:
        """Load a persona by name. Returns None if not found."""
        if name in self._cache:
            return self._cache[name]

        # Try exact filename match first, then stem match
        for p in self._persona_files():
            try:
                spec = _parse_persona_file(p)
            except Exception:
                continue
            if spec.name == name or p.stem == name:
                self._cache[name] = spec
                return spec
        return None

    def load_all(self) -> List[PersonaSpec]:
        """Load all personas from the directory."""
        specs = []
        for p in self._persona_files():
            try:
                specs.append(_parse_persona_file(p))
            except Exception:
                continue
        return specs


# ---------------------------------------------------------------------------
# Composition engine (compose > inherit)
# ---------------------------------------------------------------------------

def compose_persona(
    *names: str,
    registry: Optional[PersonaRegistry] = None,
    extra_prompt: str = "",
) -> PersonaSpec:
    """Compose multiple personas into a single unified spec.

    Composition rules (applied left-to-right):
    - system_prompt: concatenated with section separator
    - tool_access: union (all tools from all personas)
    - hooks: concatenated (all hooks, deduped)
    - model_tier: last explicit non-"mid" wins; falls back to "mid"
    - memory_scope: last explicit non-"session" wins; falls back to "session"
    - communication_style: concatenated as "A; B"
    - name: joined with "+" (e.g. "researcher+skepticism")
    - role: last persona's role wins
    """
    if registry is None:
        registry = PersonaRegistry()

    if not names:
        raise ValueError("compose_persona requires at least one persona name")

    specs = []
    for name in names:
        spec = registry.load(name)
        if spec is None:
            raise ValueError(f"Persona not found: {name!r}")
        specs.append(spec)

    if len(specs) == 1 and not extra_prompt:
        return specs[0]

    # Compose fields
    combined_prompt_sections = []
    for spec in specs:
        if spec.system_prompt.strip():
            combined_prompt_sections.append(spec.system_prompt.strip())
    if extra_prompt.strip():
        combined_prompt_sections.append(extra_prompt.strip())

    tool_access: List[str] = []
    for spec in specs:
        for t in spec.tool_access:
            if t not in tool_access:
                tool_access.append(t)

    hooks: List[str] = []
    for spec in specs:
        for h in spec.hooks:
            if h not in hooks:
                hooks.append(h)

    # Highest capability tier wins (power > mid > cheap)
    _tier_rank = {"power": 2, "mid": 1, "cheap": 0}
    model_tier = max(
        (s.model_tier for s in specs if s.model_tier),
        key=lambda t: _tier_rank.get(t, 1),
        default="mid",
    )

    # Broadest memory scope wins (global > project > session)
    _scope_rank = {"global": 2, "project": 1, "session": 0}
    memory_scope = max(
        (s.memory_scope for s in specs if s.memory_scope),
        key=lambda sc: _scope_rank.get(sc, 0),
        default="session",
    )

    comm_styles = [s.communication_style for s in specs if s.communication_style]
    communication_style = "; ".join(dict.fromkeys(comm_styles))

    return PersonaSpec(
        name="+".join(s.name for s in specs),
        role=specs[-1].role,
        model_tier=model_tier,
        tool_access=tool_access,
        memory_scope=memory_scope,
        communication_style=communication_style,
        system_prompt="\n\n---\n\n".join(combined_prompt_sections),
        hooks=hooks,
        composes=list(names),
        source_file="",
    )


# ---------------------------------------------------------------------------
# Persona-aware system prompt builder
# ---------------------------------------------------------------------------

def build_persona_system_prompt(spec: PersonaSpec, *, goal: str = "") -> str:
    """Build the full system prompt for a spawned persona session.

    Prepends a persona header (name, role, style, goal) to the spec's
    system_prompt body. This is what gets passed to the LLM as system context.
    """
    header = textwrap.dedent(f"""\
        # Persona: {spec.role}

        You are operating as **{spec.role}** ({spec.name}).
        Communication style: {spec.communication_style}
        Memory scope: {spec.memory_scope}
    """).strip()

    if goal:
        header += f"\n\nCurrent goal: {goal}"

    body = spec.system_prompt.strip()
    if body:
        return header + "\n\n" + body
    return header


# ---------------------------------------------------------------------------
# Spawn a persona (create a fresh agent loop with persona context)
# ---------------------------------------------------------------------------

def spawn_persona(
    name: str,
    goal: str,
    *,
    registry: Optional[PersonaRegistry] = None,
    adapter=None,
    dry_run: bool = False,
    max_steps: int = 20,
    compose_with: Optional[List[str]] = None,
) -> SpawnResult:
    """Launch a fresh agent loop with the given persona's system prompt.

    Memory isolation: each spawn gets its own session_id-scoped short-term
    memory slice. Medium/long tiers are readable but written with the persona
    name as task_type prefix, preventing cross-contamination.

    Args:
        name:         Persona name (must exist in registry).
        goal:         The goal to pursue.
        registry:     PersonaRegistry to use (default: auto-detect).
        adapter:      LLM adapter (default: infer from model_tier).
        dry_run:      If True, return a dry-run result without executing.
        max_steps:    Maximum loop steps.
        compose_with: Additional persona names to compose with.

    Returns:
        SpawnResult with status, summary, and artifacts.
    """
    if registry is None:
        registry = PersonaRegistry()

    # Load and optionally compose
    if compose_with:
        try:
            spec = compose_persona(name, *compose_with, registry=registry)
        except ValueError as exc:
            return SpawnResult(
                persona_name=name, goal=goal,
                status="stuck", summary=f"Persona composition failed: {exc}",
            )
    else:
        spec = registry.load(name)
        if spec is None:
            return SpawnResult(
                persona_name=name, goal=goal,
                status="stuck", summary=f"Persona not found: {name!r}",
            )

    if dry_run:
        system_prompt = build_persona_system_prompt(spec, goal=goal)
        return SpawnResult(
            persona_name=spec.name,
            goal=goal,
            status="dry_run",
            summary=f"[dry-run] Would spawn {spec.role!r} with model_tier={spec.model_tier} memory_scope={spec.memory_scope}",
            model_tier=spec.model_tier,
            memory_scope=spec.memory_scope,
        )

    # Resolve adapter
    if adapter is None:
        try:
            from llm import make_adapter, MODEL_POWER, MODEL_MID, MODEL_CHEAP
            tier_map = {"power": MODEL_POWER, "mid": MODEL_MID, "cheap": MODEL_CHEAP}
            model = tier_map.get(spec.model_tier, MODEL_MID)
            adapter = make_adapter(model=model)
        except Exception:
            return SpawnResult(
                persona_name=spec.name, goal=goal,
                status="stuck", summary="No LLM adapter available for persona spawn",
            )

    # Build full system prompt
    system_prompt = build_persona_system_prompt(spec, goal=goal)

    # Isolate short-term memory for this spawn
    from memory import short_clear, short_set
    short_clear()
    short_set("persona_name", spec.name)
    short_set("persona_goal", goal)

    # Run agent loop with persona context
    try:
        from agent_loop import run_agent_loop
        result = run_agent_loop(
            goal=goal,
            adapter=adapter,
            system_prompt_extra=system_prompt,
            max_steps=max_steps,
        )
        short_clear()  # evict session memory after loop

        return SpawnResult(
            persona_name=spec.name,
            goal=goal,
            status=result.status,
            summary=result.summary or "",
            steps_taken=len(result.steps or []),
            model_tier=spec.model_tier,
            memory_scope=spec.memory_scope,
        )
    except Exception as exc:
        short_clear()
        return SpawnResult(
            persona_name=spec.name, goal=goal,
            status="stuck", summary=f"Spawn failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Persona spec serialization (for CLI display)
# ---------------------------------------------------------------------------

def persona_to_dict(spec: PersonaSpec) -> Dict[str, Any]:
    d = asdict(spec)
    d["system_prompt_preview"] = spec.system_prompt[:200].replace("\n", " ")
    return d
