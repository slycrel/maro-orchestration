"""Tests for Phase 20: Persona System — composable agent identities."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from persona import (
    PersonaSpec,
    PersonaRegistry,
    SpawnResult,
    _parse_persona_file,
    compose_persona,
    spawn_persona,
    build_persona_system_prompt,
    persona_to_dict,
    persona_for_goal,
)

PERSONAS_DIR = Path(__file__).parent.parent / "personas"


# ---------------------------------------------------------------------------
# _parse_persona_file
# ---------------------------------------------------------------------------

def test_parse_persona_with_frontmatter(tmp_path):
    p = tmp_path / "test_persona.md"
    p.write_text(
        "---\n"
        "name: mytest\n"
        "role: Test Role\n"
        "model_tier: power\n"
        "memory_scope: project\n"
        "communication_style: precise\n"
        "hooks: [hook_a, hook_b]\n"
        "composes: []\n"
        "---\n"
        "# Body\n\nThis is the body.\n"
    )
    spec = _parse_persona_file(p)
    assert spec.name == "mytest"
    assert spec.role == "Test Role"
    assert spec.model_tier == "power"
    assert spec.memory_scope == "project"
    assert spec.communication_style == "precise"
    assert spec.hooks == ["hook_a", "hook_b"]
    assert "Body" in spec.system_prompt


def test_parse_persona_without_frontmatter(tmp_path):
    p = tmp_path / "bare_persona.md"
    p.write_text("# Persona: Bare\n\nJust a body.\n")
    spec = _parse_persona_file(p)
    assert spec.name == "bare_persona"  # inferred from stem
    assert spec.model_tier == "mid"     # default
    assert "Bare" in spec.system_prompt


def test_parse_persona_infers_name_from_stem(tmp_path):
    p = tmp_path / "some_persona.md"
    p.write_text("---\n---\nBody\n")
    spec = _parse_persona_file(p)
    assert spec.name == "some_persona"


def test_parse_persona_frontmatter_missing_field_uses_default(tmp_path):
    p = tmp_path / "partial.md"
    p.write_text("---\nname: partial\n---\nBody\n")
    spec = _parse_persona_file(p)
    assert spec.model_tier == "mid"
    assert spec.memory_scope == "session"
    assert spec.hooks == []


def test_parse_persona_malformed_frontmatter_uses_defaults(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("---\n{{{not valid yaml\n---\nBody\n")
    spec = _parse_persona_file(p)  # should not raise
    assert spec.name == "bad"
    assert spec.system_prompt == "Body"


# ---------------------------------------------------------------------------
# PersonaRegistry
# ---------------------------------------------------------------------------

def test_registry_list(tmp_path):
    (tmp_path / "alpha.md").write_text("---\nname: alpha\n---\nAlpha body\n")
    (tmp_path / "beta.md").write_text("---\nname: beta\n---\nBeta body\n")
    (tmp_path / "README.md").write_text("# README")  # should be excluded
    registry = PersonaRegistry(personas_dir=tmp_path)
    names = registry.list()
    assert "alpha" in names
    assert "beta" in names
    assert "README" not in names


def test_registry_list_empty(tmp_path):
    registry = PersonaRegistry(personas_dir=tmp_path)
    assert registry.list() == []


def test_registry_load_by_name(tmp_path):
    (tmp_path / "myp.md").write_text("---\nname: myp\nrole: My Persona\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = registry.load("myp")
    assert spec is not None
    assert spec.role == "My Persona"


def test_registry_load_not_found(tmp_path):
    registry = PersonaRegistry(personas_dir=tmp_path)
    assert registry.load("ghost") is None


def test_registry_load_all(tmp_path):
    for name in ["aa", "bb", "cc"]:
        (tmp_path / f"{name}.md").write_text(f"---\nname: {name}\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    specs = registry.load_all()
    assert len(specs) == 3


def test_registry_caches_loaded_persona(tmp_path):
    (tmp_path / "cached.md").write_text("---\nname: cached\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec1 = registry.load("cached")
    spec2 = registry.load("cached")
    assert spec1 is spec2  # same object from cache


# ---------------------------------------------------------------------------
# Built-in personas exist and parse correctly
# ---------------------------------------------------------------------------

def test_builtin_personas_exist():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    names = registry.list()
    for required in ["researcher", "builder", "critic", "ops", "summarizer", "strategist"]:
        assert required in names, f"Built-in persona missing: {required}"


def test_researcher_persona_fields():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = registry.load("researcher")
    assert spec is not None
    assert spec.model_tier == "power"
    assert "Research" in spec.role
    assert len(spec.system_prompt) > 100


def test_critic_persona_fields():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = registry.load("critic")
    assert spec is not None
    assert "Critic" in spec.role


def test_strategist_memory_scope():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = registry.load("strategist")
    assert spec is not None
    assert spec.memory_scope == "global"


def test_summarizer_model_tier():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = registry.load("summarizer")
    assert spec is not None
    assert spec.model_tier == "cheap"


# ---------------------------------------------------------------------------
# compose_persona (compose > inherit)
# ---------------------------------------------------------------------------

def test_compose_single_persona(tmp_path):
    (tmp_path / "solo.md").write_text("---\nname: solo\nrole: Solo\n---\nSolo body\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("solo", registry=registry)
    assert spec.name == "solo"
    assert spec.role == "Solo"


def test_compose_two_personas_name_joined(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\nrole: A\n---\nA body\n")
    (tmp_path / "b.md").write_text("---\nname: b\nrole: B\n---\nB body\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert spec.name == "a+b"


def test_compose_system_prompt_merged(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\n---\nPart A\n")
    (tmp_path / "b.md").write_text("---\nname: b\n---\nPart B\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert "Part A" in spec.system_prompt
    assert "Part B" in spec.system_prompt


def test_compose_tool_access_union(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\ntool_access: [search, read]\n---\nBody\n")
    (tmp_path / "b.md").write_text("---\nname: b\ntool_access: [write, read]\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert "search" in spec.tool_access
    assert "write" in spec.tool_access
    assert spec.tool_access.count("read") == 1  # deduped


def test_compose_hooks_deduped(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\nhooks: [hook_x, hook_y]\n---\nBody\n")
    (tmp_path / "b.md").write_text("---\nname: b\nhooks: [hook_y, hook_z]\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert spec.hooks.count("hook_y") == 1
    assert "hook_x" in spec.hooks
    assert "hook_z" in spec.hooks


def test_compose_model_tier_highest_wins(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\nmodel_tier: cheap\n---\nBody\n")
    (tmp_path / "b.md").write_text("---\nname: b\nmodel_tier: power\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert spec.model_tier == "power"


def test_compose_memory_scope_broadest_wins(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\nmemory_scope: session\n---\nBody\n")
    (tmp_path / "b.md").write_text("---\nname: b\nmemory_scope: global\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", "b", registry=registry)
    assert spec.memory_scope == "global"


def test_compose_unknown_persona_raises(tmp_path):
    registry = PersonaRegistry(personas_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        compose_persona("ghost", registry=registry)


def test_compose_extra_prompt(tmp_path):
    (tmp_path / "a.md").write_text("---\nname: a\n---\nBase\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("a", registry=registry, extra_prompt="Extra instructions.")
    assert "Extra instructions." in spec.system_prompt


def test_compose_three_personas(tmp_path):
    for name in ["x", "y", "z"]:
        (tmp_path / f"{name}.md").write_text(f"---\nname: {name}\n---\n{name.upper()} body\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    spec = compose_persona("x", "y", "z", registry=registry)
    assert spec.name == "x+y+z"
    assert "X body" in spec.system_prompt
    assert "Z body" in spec.system_prompt


# ---------------------------------------------------------------------------
# build_persona_system_prompt
# ---------------------------------------------------------------------------

def test_build_system_prompt_includes_role(tmp_path):
    spec = PersonaSpec(
        name="test", role="Test Role", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="precise", system_prompt="Do things.",
        hooks=[], composes=[],
    )
    prompt = build_persona_system_prompt(spec)
    assert "Test Role" in prompt


def test_build_system_prompt_includes_goal(tmp_path):
    spec = PersonaSpec(
        name="test", role="Test Role", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="precise", system_prompt="Do things.",
        hooks=[], composes=[],
    )
    prompt = build_persona_system_prompt(spec, goal="Investigate X")
    assert "Investigate X" in prompt


def test_build_system_prompt_includes_body(tmp_path):
    spec = PersonaSpec(
        name="test", role="Test Role", model_tier="mid",
        tool_access=[], memory_scope="session",
        communication_style="precise", system_prompt="The body content here.",
        hooks=[], composes=[],
    )
    prompt = build_persona_system_prompt(spec)
    assert "The body content here." in prompt


# ---------------------------------------------------------------------------
# spawn_persona (dry-run only — no LLM in tests)
# ---------------------------------------------------------------------------

def test_spawn_persona_dry_run(tmp_path):
    (tmp_path / "tester.md").write_text("---\nname: tester\nrole: Tester\n---\nTest body\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    result = spawn_persona("tester", "Do something", registry=registry, dry_run=True)
    assert isinstance(result, SpawnResult)
    assert result.status == "dry_run"
    assert "tester" in result.persona_name


def test_spawn_persona_not_found_returns_stuck(tmp_path):
    registry = PersonaRegistry(personas_dir=tmp_path)
    result = spawn_persona("ghost", "goal", registry=registry, dry_run=True)
    assert result.status == "stuck"
    assert "not found" in result.summary.lower()


def test_spawn_persona_compose_dry_run(tmp_path):
    (tmp_path / "base.md").write_text("---\nname: base\n---\nBase body\n")
    (tmp_path / "ext.md").write_text("---\nname: ext\n---\nExt body\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    result = spawn_persona("base", "goal", registry=registry, dry_run=True, compose_with=["ext"])
    assert result.status == "dry_run"
    assert "base+ext" in result.persona_name


def test_spawn_persona_compose_unknown_returns_stuck(tmp_path):
    (tmp_path / "base.md").write_text("---\nname: base\n---\nBody\n")
    registry = PersonaRegistry(personas_dir=tmp_path)
    result = spawn_persona("base", "goal", registry=registry, dry_run=True, compose_with=["nonexistent"])
    assert result.status == "stuck"


# ---------------------------------------------------------------------------
# persona_to_dict
# ---------------------------------------------------------------------------

def test_persona_to_dict_serializable():
    spec = PersonaSpec(
        name="x", role="X", model_tier="mid",
        tool_access=["a"], memory_scope="session",
        communication_style="terse", system_prompt="Body",
        hooks=[], composes=[],
    )
    d = persona_to_dict(spec)
    assert isinstance(d, dict)
    assert d["name"] == "x"
    assert "system_prompt_preview" in d
    json.dumps(d)  # must be JSON-serializable


# ---------------------------------------------------------------------------
# Real personas — compose smoke tests
# ---------------------------------------------------------------------------

def test_compose_researcher_critic():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = compose_persona("researcher", "critic", registry=registry)
    assert "researcher" in spec.name
    assert "critic" in spec.name
    assert len(spec.system_prompt) > 200


def test_compose_researcher_summarizer():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    spec = compose_persona("researcher", "summarizer", registry=registry)
    assert spec.model_tier == "power"  # researcher is power; summarizer is cheap; power wins (non-default)


def test_all_builtin_personas_parse_without_error():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    specs = registry.load_all()
    assert len(specs) >= 6
    for spec in specs:
        assert spec.name
        assert spec.role
        assert spec.model_tier in ("power", "mid", "cheap")
        assert spec.memory_scope in ("session", "project", "global")


# ---------------------------------------------------------------------------
# persona_for_goal — Phase 31 auto-selection
# ---------------------------------------------------------------------------

def test_persona_for_goal_research_keyword():
    name, conf = persona_for_goal("research and summarise the latest LLM orchestration frameworks")
    assert name == "research-assistant-deep-synth"
    assert conf >= 0.70


def test_persona_for_goal_psychology_keyword():
    name, conf = persona_for_goal("what does psychology say about grit and persistence in agents")
    assert name == "psyche-researcher"
    assert conf >= 0.75


def test_persona_for_goal_build_keyword():
    name, conf = persona_for_goal("implement a WebSocket handler for real-time updates")
    assert name == "builder"
    assert conf >= 0.70


def test_persona_for_goal_ops_keyword():
    name, conf = persona_for_goal("monitor the heartbeat service and diagnose why alerts are noisy")
    assert name == "ops"
    assert conf >= 0.70


def test_persona_for_goal_finance_keyword():
    name, conf = persona_for_goal("analyse the polymarket odds on the 2026 election")
    assert name == "finance-analyst"
    assert conf >= 0.75


def test_persona_for_goal_default_on_no_match():
    name, conf = persona_for_goal("do something miscellaneous")
    assert name == "research-assistant-deep-synth"  # default
    assert conf >= 0.0


def test_persona_for_goal_validates_against_registry():
    registry = PersonaRegistry(personas_dir=PERSONAS_DIR)
    name, conf = persona_for_goal("what does psychology say about cognition", registry=registry)
    # psyche-researcher exists in the real personas dir
    assert name in registry.list()
    assert conf >= 0.70


def test_persona_for_goal_returns_tuple():
    result = persona_for_goal("research the latest papers on LLM memory")
    assert isinstance(result, tuple)
    assert len(result) == 2
    name, conf = result
    assert isinstance(name, str)
    assert isinstance(conf, float)
    assert 0.0 <= conf <= 1.0


def test_persona_for_goal_tweet_goal():
    name, conf = persona_for_goal(
        "Research the tweet at https://x.com/user/status/123 and summarise what they say"
    )
    assert name == "research-assistant-deep-synth"
    assert conf >= 0.70


# ---------------------------------------------------------------------------
# Phase 31: extended persona routing (health, legal, strategy, creative, etc.)
# ---------------------------------------------------------------------------

def test_persona_for_goal_health_keyword():
    name, conf = persona_for_goal("research symptoms and treatments for insomnia and sleep health")
    assert name == "health-researcher"
    assert conf >= 0.70


def test_persona_for_goal_legal_keyword():
    name, conf = persona_for_goal("review this contract for GDPR compliance and liability clauses")
    assert name == "legal-researcher"
    assert conf >= 0.70


def test_persona_for_goal_strategy_keyword():
    name, conf = persona_for_goal("build a strategic roadmap for the next milestone prioritization")
    assert name == "strategist"
    assert conf >= 0.70


def test_persona_for_goal_creative_keyword():
    name, conf = persona_for_goal("write creative marketing copy and brand narrative for the campaign")
    assert name == "creative-director"
    assert conf >= 0.70


def test_persona_for_goal_scraping_keyword():
    name, conf = persona_for_goal("scrape and extract structured data from the site using playwright")
    assert name == "scrapling-adaptive-web-recon"
    assert conf >= 0.70


def test_persona_for_goal_simplifier_keyword():
    name, conf = persona_for_goal("the codebase is too complex, simplify and remove dead code")
    assert name == "simplifier"
    assert conf >= 0.70


def test_persona_for_goal_critic_keyword():
    name, conf = persona_for_goal("review this design and identify the failure modes and weaknesses")
    assert name == "critic"
    assert conf >= 0.70


def test_persona_for_goal_systems_design_keyword():
    name, conf = persona_for_goal(
        "design the system architecture for scalable distributed microservices"
    )
    assert name == "systems-design-architect-coach"
    assert conf >= 0.70
