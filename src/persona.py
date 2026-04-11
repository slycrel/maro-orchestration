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
import logging
import re
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.persona")


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
    """Scans workspace + repo personas/ directories.

    Resolution order (workspace wins on name collisions):
      1. ~/.poe/workspace/personas/  — self-created/evolved personas
      2. repo/personas/              — shipped persona specs
    """

    def __init__(self, personas_dir: Optional[Path] = None):
        self._repo_dir: Optional[Path] = None
        self._ws_dir: Optional[Path] = None

        if personas_dir is not None:
            # Explicit dir (tests) — use only that
            self._repo_dir = personas_dir
        else:
            # Auto-detect: workspace first, then repo
            try:
                from config import personas_dir as _ws_personas
                ws = _ws_personas()
                if ws.exists():
                    self._ws_dir = ws
            except Exception:
                pass
            try:
                from orch import orch_root
                candidate = orch_root() / "personas"
                if candidate.exists():
                    self._repo_dir = candidate
            except Exception:
                pass
            if self._repo_dir is None:
                self._repo_dir = Path(__file__).resolve().parent.parent / "personas"
                if not self._repo_dir.exists():
                    self._repo_dir = Path.cwd() / "personas"

        self._cache: Dict[str, PersonaSpec] = {}

    @property
    def _dir(self) -> Path:
        """Backward compat: return the repo dir."""
        return self._repo_dir or Path("personas")

    def _persona_files(self) -> List[Path]:
        """Collect persona files from repo + workspace (workspace wins)."""
        seen_names: set = set()
        files: List[Path] = []

        # Workspace first (self-created/evolved — takes precedence)
        if self._ws_dir and self._ws_dir.exists():
            for p in sorted(self._ws_dir.glob("*.md")):
                if p.name != "README.md":
                    files.append(p)
                    seen_names.add(p.stem)

        # Repo second (shipped personas — skipped if workspace has same name)
        repo = self._repo_dir
        if repo and repo.exists():
            for p in sorted(repo.glob("*.md")):
                if p.name != "README.md" and p.stem not in seen_names:
                    files.append(p)

        return files

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
# Import-time persona scan + routing table
# ---------------------------------------------------------------------------

# Hard-coded fallback PersonaSpec stubs — used when personas/ dir is empty/missing.
_HARDCODED_FALLBACKS: Dict[str, "PersonaSpec"] = {
    "builder": PersonaSpec(
        name="builder", role="Builder", model_tier="mid",
        tool_access=[], memory_scope="project",
        communication_style="direct, solution-focused",
        system_prompt="You are a software builder. Implement, fix, and test code.",
        hooks=[], composes=[],
    ),
    "research-assistant-deep-synth": PersonaSpec(
        name="research-assistant-deep-synth", role="Research Assistant",
        model_tier="power", tool_access=[], memory_scope="session",
        communication_style="analytical, source-grounded, crisp",
        system_prompt="You are a deep research assistant. Investigate, synthesize, and summarize.",
        hooks=[], composes=[],
    ),
    "ops": PersonaSpec(
        name="ops", role="Ops Engineer", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="terse, diagnostic",
        system_prompt="You are an ops engineer. Monitor, diagnose, and fix infrastructure.",
        hooks=[], composes=[],
    ),
    "critic": PersonaSpec(
        name="critic", role="Critic", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="precise, adversarial-constructive",
        system_prompt="You are a critic. Identify weaknesses, risks, and failure modes.",
        hooks=[], composes=[],
    ),
    "reporter": PersonaSpec(
        name="reporter", role="Reporter", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="structured, concise",
        system_prompt="You synthesize sub-agent outputs into final deliverables.",
        hooks=[], composes=[],
    ),
}


def scan_personas_dir(
    personas_dir: Optional[Path] = None,
) -> Dict[str, "PersonaSpec"]:
    """Scan the personas/ directory and return a routing table: name → PersonaSpec.

    Supports .md files (YAML frontmatter + markdown body) and .yaml/.yml files
    (pure YAML with optional system_prompt/body key). Falls back to
    _HARDCODED_FALLBACKS if the directory is empty or missing.

    Args:
        personas_dir: Override the directory to scan. Auto-detected if None.

    Returns:
        Dict mapping persona name → PersonaSpec. Never empty (fallbacks ensure this).
    """
    if personas_dir is None:
        try:
            from orch import orch_root
            candidate = orch_root() / "personas"
            if candidate.exists():
                personas_dir = candidate
        except Exception:
            pass
        if personas_dir is None:
            personas_dir = Path(__file__).resolve().parent.parent / "personas"
            if not personas_dir.exists():
                personas_dir = Path.cwd() / "personas"

    specs: Dict[str, PersonaSpec] = {}

    if personas_dir.exists():
        # .md files — YAML frontmatter + markdown body
        for p in sorted(personas_dir.glob("*.md")):
            if p.name == "README.md":
                continue
            try:
                spec = _parse_persona_file(p)
                specs[spec.name] = spec
            except Exception as exc:
                log.warning("scan_personas_dir: skipping %s: %s", p.name, exc)

        # .yaml / .yml files — pure YAML with optional system_prompt/body key
        yaml_files = sorted(personas_dir.glob("*.yaml")) + sorted(personas_dir.glob("*.yml"))
        for p in yaml_files:
            try:
                import yaml as _yaml
                data = _yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                name = str(data.get("name") or p.stem)
                spec = PersonaSpec(
                    name=name,
                    role=str(data.get("role", "General Assistant")),
                    model_tier=str(data.get("model_tier", "mid")),
                    tool_access=list(data.get("tool_access") or []),
                    memory_scope=str(data.get("memory_scope", "session")),
                    communication_style=str(data.get("communication_style", "direct and concise")),
                    system_prompt=str(data.get("system_prompt") or data.get("body", "")),
                    hooks=list(data.get("hooks") or []),
                    composes=list(data.get("composes") or []),
                    source_file=str(p),
                )
                specs[name] = spec
            except Exception as exc:
                log.warning("scan_personas_dir: skipping %s: %s", p.name, exc)

    if not specs:
        log.warning(
            "scan_personas_dir: no personas found in %s — using hard-coded fallbacks",
            personas_dir,
        )
        return dict(_HARDCODED_FALLBACKS)

    log.debug("scan_personas_dir: loaded %d personas from %s", len(specs), personas_dir)
    return specs


# Module-level routing table — populated at import time.
# Maps persona name → PersonaSpec. Access via _PERSONA_SPECS["builder"] etc.
_PERSONA_SPECS: Dict[str, PersonaSpec] = scan_personas_dir()


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
# Phase 59 (NeMo DataDesigner steal): Persona template variable injection
# ---------------------------------------------------------------------------

_TEMPLATE_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def extract_template_variables(template: str) -> set:
    """Extract {{ variable }} references from a persona template string.

    Enables lazy context fetching — only inject what the template needs.
    Pure regex, no Jinja2 dependency.

    Returns:
        Set of variable name strings found in `{{ ... }}` blocks.

    Example:
        >>> extract_template_variables("Use {{ standing_rules }}. Goal: {{ goal }}.")
        {'standing_rules', 'goal'}
    """
    return set(_TEMPLATE_VAR_RE.findall(template))


def render_persona_template(template: str, goal: str = "") -> str:
    """Render a persona template by substituting {{ variable }} references.

    Supported variables (lazy-fetched — only loaded if referenced):
        {{ goal }}             — the current goal text
        {{ standing_rules }}   — formatted standing rules from memory
        {{ recent_lessons }}   — top-3 tiered lessons for this goal
        {{ task_type }}        — inferred task type (research/build/ops/general)

    Unrecognised variables are left as-is (no crash on unknown refs).

    Args:
        template: Persona system_prompt body with optional {{ var }} references.
        goal:     Current goal text.

    Returns:
        Rendered string with all known variables substituted.
    """
    variables = extract_template_variables(template)
    if not variables:
        return template  # fast path — no template vars, return as-is

    context: Dict[str, str] = {}

    if "goal" in variables:
        context["goal"] = goal or ""

    if "task_type" in variables:
        try:
            from intent import classify_intent
            context["task_type"] = classify_intent(goal).lower() if goal else "general"
        except Exception:
            context["task_type"] = "general"

    if "standing_rules" in variables:
        try:
            from memory import load_standing_rules
            rules = load_standing_rules()
            context["standing_rules"] = "\n".join(
                f"- {r.rule}" for r in rules[:5]
            ) if rules else "(none)"
        except Exception:
            context["standing_rules"] = "(unavailable)"

    if "recent_lessons" in variables:
        try:
            from memory import query_lessons
            task_type = context.get("task_type", "general")
            lessons = query_lessons(goal, n=3)
            if lessons:
                context["recent_lessons"] = "\n".join(
                    f"- {'✓' if l.outcome == 'done' else '✗'} {l.lesson}"
                    for l in lessons
                )
            else:
                context["recent_lessons"] = "(none)"
        except Exception:
            context["recent_lessons"] = "(unavailable)"

    def _sub(match: re.Match) -> str:
        var = match.group(1).strip()
        return context.get(var, match.group(0))  # leave unknown vars as-is

    return _TEMPLATE_VAR_RE.sub(_sub, template)


# ---------------------------------------------------------------------------
# Persona-aware system prompt builder
# ---------------------------------------------------------------------------

def build_persona_system_prompt(spec: PersonaSpec, *, goal: str = "") -> str:
    """Build the full system prompt for a spawned persona session.

    Prepends a persona header (name, role, style, goal) to the spec's
    system_prompt body. This is what gets passed to the LLM as system context.

    Phase 59: if the persona body contains {{ variable }} references, they are
    rendered via render_persona_template before building the final prompt.
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
        body = render_persona_template(body, goal=goal)
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
    log.info("spawn persona=%r goal=%r tier=%s", spec.name, goal[:60], spec.model_tier)
    if adapter is None:
        try:
            from llm import build_adapter, MODEL_POWER, MODEL_MID, MODEL_CHEAP
            tier_map = {"power": MODEL_POWER, "mid": MODEL_MID, "cheap": MODEL_CHEAP}
            model = tier_map.get(spec.model_tier, MODEL_MID)
            adapter = build_adapter(model=model)
            log.info("adapter resolved: %s (model=%s)", type(adapter).__name__, model)
        except Exception as exc:
            log.error("adapter resolution failed: %s", exc)
            return SpawnResult(
                persona_name=spec.name, goal=goal,
                status="stuck", summary=f"No LLM adapter available for persona spawn: {exc}",
            )

    # Build full system prompt
    system_prompt = build_persona_system_prompt(spec, goal=goal)
    log.debug("system_prompt length=%d chars", len(system_prompt))

    # Isolate short-term memory for this spawn
    from memory import short_clear, short_set
    short_clear()
    short_set("persona_name", spec.name)
    short_set("persona_goal", goal)

    # Run agent loop with persona context
    import time as _time
    _spawn_t0 = _time.monotonic()
    try:
        from agent_loop import run_agent_loop
        result = run_agent_loop(
            goal=goal,
            adapter=adapter,
            ancestry_context_extra=system_prompt,
            max_steps=max_steps,
        )
        short_clear()  # evict session memory after loop

        _elapsed = _time.monotonic() - _spawn_t0
        log.info("spawn done persona=%r status=%s steps=%d elapsed=%.1fs",
                 spec.name, result.status, len(result.steps or []), _elapsed)
        return SpawnResult(
            persona_name=spec.name,
            goal=goal,
            status=result.status,
            summary=result.summary() or "",
            steps_taken=len(result.steps or []),
            model_tier=spec.model_tier,
            memory_scope=spec.memory_scope,
        )
    except Exception as exc:
        short_clear()
        _elapsed = _time.monotonic() - _spawn_t0
        log.error("spawn failed persona=%r exc=%s elapsed=%.1fs", spec.name, exc, _elapsed)
        return SpawnResult(
            persona_name=spec.name, goal=goal,
            status="stuck", summary=f"Spawn failed: {exc}",
        )


# ---------------------------------------------------------------------------
# Persona auto-selection (Phase 31)
# ---------------------------------------------------------------------------

# Keyword → persona name routing table.
# Each entry: (keywords_any_of, persona_name, confidence)
# Evaluated in order; first confident match wins.
_PERSONA_ROUTING: List[tuple] = [
    # GStack / garrytan / founder-engineer mode → garrytan
    (["garrytan", "gstack", "founder review", "phase-gated", "think plan build",
      "founder taste", "gstack review"], "garrytan", 0.95),
    # Psychology / cognition / neuroscience / philosophy → psyche-researcher
    (["psychology", "neuroscience", "cognition", "cognitive", "philosophy",
      "enneagram", "mbti", "personality", "memory model", "spaced repetition",
      "grit", "persistence", "learned helplessness", "kahneman", "system 1",
      "tacit knowledge", "expertise", "intrinsic motivation"], "psyche-researcher", 0.85),
    # Health / medical / clinical / symptoms → health-researcher
    (["health", "medical", "clinical", "symptoms", "symptom", "treatment",
      "medication", "disease", "diagnosis", "therapy", "nutrition",
      "exercise", "mental health", "wellness", "sleep", "diet"], "health-researcher", 0.85),
    # Legal / compliance / regulatory / contracts → legal-researcher
    (["legal", "law", "contract", "compliance", "regulation", "liability",
      "gdpr", "privacy", "terms of service", "intellectual property",
      "copyright", "patent", "lawsuit", "jurisdiction", "statute"], "legal-researcher", 0.85),
    # Strategy / planning / roadmap / direction → strategist
    (["strategy", "strategic", "roadmap", "direction", "prioritize",
      "prioritization", "milestones", "okr", "kpi", "tradeoff", "north star",
      "vision", "long-term", "short-term", "planning", "alignment"], "strategist", 0.80),
    # Creative / writing / content / narrative / design → creative-director
    (["creative", "content", "narrative", "story", "brand", "voice",
      "copywriting", "headline", "tagline", "marketing copy", "campaign",
      "creative brief", "design direction", "tone of voice"], "creative-director", 0.80),
    # Web scraping / data extraction / crawl → scrapling-adaptive-web-recon
    (["scrape", "scraping", "crawl", "crawling", "web extraction",
      "data extraction", "html", "parse", "playwright", "selenium",
      "beautifulsoup", "site map", "spider"], "scrapling-adaptive-web-recon", 0.85),
    # Systems design / architecture / scalability → systems-design-architect-coach
    (["architecture", "system design", "scalability", "distributed",
      "microservice", "database schema", "data model", "latency",
      "throughput", "capacity", "design pattern", "trade-off analysis"], "systems-design-architect-coach", 0.80),
    # Review / critique / evaluate quality → critic
    (["review", "critique", "evaluate", "assess", "quality", "problems",
      "weaknesses", "flaws", "risks", "what's wrong", "failure mode"], "critic", 0.75),
    # Simplify / reduce complexity / delete → simplifier
    (["simplify", "simplification", "too complex", "over-engineered",
      "delete", "remove", "deprecate", "refactor toward", "reduce complexity",
      "unnecessary", "bloat", "dead code"], "simplifier", 0.80),
    # Research / analysis / investigate / summarize → research-assistant-deep-synth
    (["research", "investigate", "analyse", "analyze", "summarise", "summarize",
      "tweet", "article", "paper", "study", "literature", "findings", "survey",
      "what does", "what is", "how does", "explain", "why does"], "research-assistant-deep-synth", 0.75),
    # Build / implement / code / write software → builder
    (["build", "implement", "code", "write", "create", "develop", "add feature",
      "fix bug", "refactor", "test", "unit test", "integration", "deploy",
      "function", "class", "module", "api", "endpoint"], "builder", 0.80),
    # System / ops / monitor / deploy / diagnose → ops
    (["monitor", "diagnose", "health", "service", "systemd", "cron", "deploy",
      "restart", "log", "alert", "heartbeat", "disk", "memory usage",
      "process", "daemon", "script", "automation"], "ops", 0.75),
    # Finance / market / trading / polymarket → finance-analyst
    (["polymarket", "market", "trading", "prediction market", "bet", "odds",
      "finance", "investment", "portfolio", "price", "token", "crypto"], "finance-analyst", 0.80),
    # Synthesis / consolidate / combine outputs → reporter
    (["consolidate", "synthesize", "synthesis", "combine outputs", "merge results",
      "write report", "compile findings", "summarize all", "integrate results",
      "final report", "deliverable", "combine sub-agent"], "reporter", 0.80),
]

_DEFAULT_PERSONA = "research-assistant-deep-synth"


def persona_for_goal(
    goal: str,
    registry: Optional["PersonaRegistry"] = None,
    *,
    confidence_threshold: float = 0.70,
    allow_llm_fallback: bool = False,
    allow_freeform: bool = False,
    adapter=None,
) -> tuple[str, float]:
    """Select the best persona for a goal. Returns (persona_name, confidence).

    Uses keyword routing first (fast, zero-cost). Falls back to LLM classification
    if allow_llm_fallback=True and no keyword match exceeds confidence_threshold.

    Args:
        goal: Natural language goal string.
        registry: PersonaRegistry instance (optional — used to validate that the
            selected persona actually exists).
        confidence_threshold: Minimum confidence to accept a keyword match.
        allow_llm_fallback: Use cheap LLM if keyword match falls below threshold.
        adapter: LLMAdapter instance for LLM fallback (auto-built if None).

    Returns:
        Tuple of (persona_name, confidence). confidence=1.0 means certain.
    """
    import re as _re
    goal_lower = goal.lower()

    def _kw_match(kw: str, text: str) -> bool:
        """Word-boundary-aware keyword match. Multi-word phrases match as substrings."""
        if " " in kw:
            return kw in text
        return bool(_re.search(r"\b" + _re.escape(kw) + r"\b", text))

    # Keyword routing — score by hit count normalized by keyword list size
    best_name = _DEFAULT_PERSONA
    best_conf = 0.0

    for keywords, persona_name, base_confidence in _PERSONA_ROUTING:
        hits = sum(1 for kw in keywords if _kw_match(kw, goal_lower))
        if hits == 0:
            continue
        # Scale confidence by hit density: more hits = more certain
        conf = min(1.0, base_confidence * (1.0 + (hits - 1) * 0.05))
        if conf > best_conf:
            best_conf = conf
            best_name = persona_name

    # Validate persona exists in registry
    if registry is not None and best_name != _DEFAULT_PERSONA:
        available = registry.list()
        if best_name not in available:
            # Closest fallback that is available
            fallbacks = {
                "psyche-researcher": ["research-assistant-deep-synth"],
                "finance-analyst": ["research-assistant-deep-synth"],
            }
            alternatives = fallbacks.get(best_name, [_DEFAULT_PERSONA])
            for alt in alternatives:
                if alt in available:
                    best_name = alt
                    best_conf *= 0.9  # slight confidence penalty for fallback
                    break
            else:
                best_name = _DEFAULT_PERSONA
                best_conf = 0.5

    if best_conf >= confidence_threshold:
        return best_name, best_conf

    # LLM fallback (optional — avoids adding token cost to every routing decision)
    if allow_llm_fallback and adapter is not None:
        available_names = registry.list() if registry else list(
            n for _, n, _ in _PERSONA_ROUTING
        ) + [_DEFAULT_PERSONA]

        try:
            from llm import LLMMessage
            personas_str = ", ".join(available_names)
            prompt = (
                f"Available personas: {personas_str}\n\n"
                f"Goal: {goal[:300]}\n\n"
                f"Which single persona best fits this goal? Reply with ONLY the persona name, nothing else."
            )
            resp = adapter.complete([LLMMessage("user", prompt)], max_tokens=30)
            llm_name = resp.content.strip().lower().split()[0] if resp.content.strip() else ""
            if llm_name in available_names:
                return llm_name, 0.80
        except Exception:
            pass

    # Free-form fallback: write a goal-specific persona to disk and re-route
    if allow_freeform and best_conf < confidence_threshold:
        spec = create_freeform_persona(goal)
        # Register in module-level cache so subsequent registry.load() calls find it
        _PERSONA_SPECS[spec.name] = spec
        log.info(
            "persona_for_goal: freeform persona created slug=%r for goal=%r",
            spec.name, goal[:60],
        )
        return spec.name, 0.70

    return best_name or _DEFAULT_PERSONA, max(best_conf, 0.5)


# ---------------------------------------------------------------------------
# Free-form persona creation (Phase 31 extension)
# ---------------------------------------------------------------------------

def _default_personas_dir() -> Path:
    """Return the canonical personas/ directory path (same logic as PersonaRegistry)."""
    try:
        from orch import orch_root
        d = Path(orch_root()) / "personas"
        if d.exists():
            return d
    except Exception:
        pass
    return Path(__file__).resolve().parent.parent / "personas"


def _goal_to_slug(goal: str) -> str:
    """Convert a goal string to a filesystem-safe persona slug.

    Examples:
        "Analyze competitor pricing strategies" → "analyze-competitor-pricing"
        "Build a REST API in FastAPI"           → "build-a-rest-api-in"
    """
    import re as _re
    words = _re.sub(r"[^a-z0-9\s]", "", goal.lower()).split()
    return "-".join(words[:5]) or "freeform"


def create_freeform_persona(
    goal: str,
    personas_dir: Optional[Path] = None,
) -> PersonaSpec:
    """Write a minimal persona spec to personas/<slug>.yaml and return it.

    Called when keyword routing and LLM fallback both fail to find a confident
    match.  Generates a goal-specific persona on-the-fly, persists it so future
    runs can reuse or refine it, and returns a ready-to-use PersonaSpec.

    Args:
        goal:         Natural-language goal (used to derive the slug and prompt).
        personas_dir: Override for the personas/ directory (default: auto-detect).

    Returns:
        PersonaSpec for the newly created persona.
    """
    if personas_dir is None:
        personas_dir = _default_personas_dir()

    slug = _goal_to_slug(goal)
    yaml_path = personas_dir / f"{slug}.yaml"

    # Build a minimal but useful spec
    role_words = goal.split()[:6]
    role = " ".join(w.capitalize() for w in role_words)
    system_prompt = (
        f"You are a specialist agent focused on: {goal}.\n"
        "Work methodically. Prefer concrete actions over vague plans. "
        "Cite sources when reasoning about facts. Be direct."
    )

    spec_data = {
        "name": slug,
        "role": role,
        "model_tier": "mid",
        "tool_access": [],
        "memory_scope": "session",
        "communication_style": "direct and concise",
        "system_prompt": system_prompt,
        "hooks": [],
        "composes": [],
    }

    try:
        import yaml as _yaml
        yaml_path.write_text(
            _yaml.dump(spec_data, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        log.info("create_freeform_persona: wrote %s", yaml_path)
    except Exception as exc:
        log.warning("create_freeform_persona: could not write %s: %s", yaml_path, exc)

    return PersonaSpec(
        name=slug,
        role=role,
        model_tier="mid",
        tool_access=[],
        memory_scope="session",
        communication_style="direct and concise",
        system_prompt=system_prompt,
        hooks=[],
        composes=[],
        source_file=str(yaml_path),
    )


# ---------------------------------------------------------------------------
# Skeptic modifier
# ---------------------------------------------------------------------------

_SKEPTIC_ADDITION = (
    "\n\nSKEPTIC MODE: Before proposing any plan or answer, briefly list 2-3 ways "
    "it could fail, go wrong, or miss the mark. Be specific to this task — not "
    "generic warnings. Then proceed with your best answer accounting for those risks."
)


def apply_skeptic_modifier(spec: PersonaSpec) -> PersonaSpec:
    """Return a copy of spec with the skeptic framing prepended to the system prompt.

    Use when the goal is ambiguous, high-stakes, or the previous step produced
    a result that feels overconfident. Does not affect tool_access or model_tier.
    """
    from dataclasses import replace
    return replace(spec, system_prompt=spec.system_prompt + _SKEPTIC_ADDITION)


# ---------------------------------------------------------------------------
# Persona spec serialization (for CLI display)
# ---------------------------------------------------------------------------

def persona_to_dict(spec: PersonaSpec) -> Dict[str, Any]:
    d = asdict(spec)
    d["system_prompt_preview"] = spec.system_prompt[:200].replace("\n", " ")
    return d


# ---------------------------------------------------------------------------
# Phase 31: Persona feedback loop
# ---------------------------------------------------------------------------

def record_persona_outcome(
    persona_name: str,
    goal: str,
    status: str,  # "done" | "stuck" | "unknown"
    *,
    confidence: float = 0.0,
    loop_id: str = "",
) -> bool:
    """Record the outcome of a persona-routed loop to persona-outcomes.jsonl.

    Used by the evolver to correlate persona selection quality with success.
    Never raises — returns True if write succeeded, False otherwise.
    """
    from datetime import datetime, timezone

    try:
        import orch
        out_path = orch.orch_root() / "memory" / "persona-outcomes.jsonl"
        out_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    entry = {
        "persona": persona_name,
        "goal": goal[:120],
        "status": status,
        "confidence": round(confidence, 3),
        "loop_id": loop_id,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception:
        return False


def load_persona_outcomes(limit: int = 100) -> List[dict]:
    """Load recent persona outcome records, newest first."""
    try:
        import orch
        out_path = orch.orch_root() / "memory" / "persona-outcomes.jsonl"
        if not out_path.exists():
            return []
        lines = out_path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
        return list(reversed(results))[:limit]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Phase 35 P2: Machine-readable agent capability manifest
# ---------------------------------------------------------------------------

def generate_manifest(registry: Optional["PersonaRegistry"] = None) -> List[dict]:
    """Generate a list of agent capability dicts from all loaded persona specs.

    Each entry contains:
      name, role, model_tier, tool_access, capabilities, trigger_keywords, description

    This is the machine-readable replacement for AGENTS.md prose.
    Suitable for serialization to YAML or JSON.
    """
    if registry is None:
        registry = PersonaRegistry()

    manifest = []
    for name in registry.list():
        try:
            spec = registry.load(name)
            if spec is None:
                continue
            # Extract trigger keywords from the routing table in persona_for_goal
            triggers = _PERSONA_ROUTING_KEYWORDS.get(name, [])
            entry = {
                "name": name,
                "role": spec.role,
                "model_tier": spec.model_tier,
                "tool_access": list(spec.tool_access),
                "memory_scope": spec.memory_scope,
                "trigger_keywords": list(triggers),
                "composes": list(spec.composes),
                "description": spec.system_prompt[:200].strip().replace("\n", " "),
            }
            manifest.append(entry)
        except Exception:
            continue

    manifest.sort(key=lambda e: e["name"])
    return manifest


def save_manifest(
    output_path: Optional[Path] = None,
    registry: Optional["PersonaRegistry"] = None,
    fmt: str = "json",
) -> Path:
    """Write the agent capability manifest to disk.

    Args:
        output_path: Where to write. Defaults to agents/manifest.json in orch_root.
        registry:    PersonaRegistry to source from.
        fmt:         "json" or "yaml" (yaml requires PyYAML).

    Returns:
        The path written to.
    """
    if output_path is None:
        try:
            import orch
            output_path = orch.orch_root() / "agents" / f"manifest.{fmt}"
        except Exception:
            output_path = Path(".") / f"manifest.{fmt}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = generate_manifest(registry=registry)

    if fmt == "yaml":
        try:
            import yaml
            content = yaml.dump({"agents": manifest}, default_flow_style=False, allow_unicode=True)
        except Exception:
            # Fallback to JSON if PyYAML not available
            content = json.dumps({"agents": manifest}, indent=2, ensure_ascii=False)
            output_path = output_path.with_suffix(".json")
    else:
        content = json.dumps({"agents": manifest}, indent=2, ensure_ascii=False)

    output_path.write_text(content + "\n", encoding="utf-8")
    return output_path


def load_manifest(path: Optional[Path] = None) -> List[dict]:
    """Load the agent capability manifest from disk. Returns [] if not found."""
    if path is None:
        try:
            import orch
            for ext in ("json", "yaml"):
                candidate = orch.orch_root() / "agents" / f"manifest.{ext}"
                if candidate.exists():
                    path = candidate
                    break
        except Exception:
            pass

    if path is None or not path.exists():
        return []

    try:
        content = path.read_text(encoding="utf-8")
        if str(path).endswith(".yaml") or str(path).endswith(".yml"):
            try:
                import yaml
                data = yaml.safe_load(content)
            except Exception:
                data = json.loads(content)
        else:
            data = json.loads(content)
        return data.get("agents", [])
    except Exception:
        return []


# Trigger keyword map for manifest generation (mirrors persona_for_goal routing table)
_PERSONA_ROUTING_KEYWORDS: Dict[str, List[str]] = {
    "health-researcher": ["health", "medical", "nutrition", "disease", "clinical", "symptom"],
    "legal-researcher": ["legal", "law", "contract", "regulation", "compliance", "statute"],
    "strategist": ["strategy", "roadmap", "competitive", "market", "planning", "vision"],
    "creative-director": ["creative", "brand", "marketing", "campaign", "design", "story"],
    "scrapling-adaptive-web-recon": ["scrape", "scraping", "crawl", "web extraction", "site data"],
    "systems-design-architect-coach": ["architecture", "distributed", "system design", "microservice", "infra"],
    "critic": ["critique", "review", "failure mode", "weakness", "evaluate", "assess"],
    "simplifier": ["simplify", "too complex", "remove", "dead code", "reduce"],
    "research-assistant-deep-synth": ["research", "analyze", "summarize", "literature", "investigate"],
    "builder": ["build", "implement", "create", "code", "develop", "write"],
    "ops": ["deploy", "monitor", "ops", "infrastructure", "pipeline", "automate"],
    "finance-analyst": ["finance", "invest", "portfolio", "market", "trading", "profit"],
    "psyche-researcher": ["psychology", "neurology", "cognitive", "mental", "behavior"],
    "reporter": ["consolidate", "synthesize", "combine outputs", "final report", "merge results"],
}
