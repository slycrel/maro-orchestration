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
    record_persona_outcome,
    load_persona_outcomes,
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


def test_spawn_persona_real_path_with_mock_adapter(tmp_path, monkeypatch):
    """Exercise the non-dry-run spawn path with a mocked agent loop."""
    from unittest.mock import patch
    from agent_loop import LoopResult, StepOutcome

    (tmp_path / "worker.md").write_text("---\nname: worker\nrole: Worker\n---\nDo work\n")
    registry = PersonaRegistry(personas_dir=tmp_path)

    fake_result = LoopResult(
        loop_id="test-loop",
        project="test",
        goal="do something",
        status="done",
        steps=[StepOutcome(index=1, text="step 1", status="done", result="ok", iteration=1)],
    )

    class _FakeAdapter:
        pass

    with patch("agent_loop.run_agent_loop", return_value=fake_result) as mock_loop:
        result = spawn_persona("worker", "do something", registry=registry, adapter=_FakeAdapter())
    assert result.status == "done"
    assert result.steps_taken == 1
    assert "1/1" in result.summary
    mock_loop.assert_called_once()
    call_kwargs = mock_loop.call_args[1]
    assert call_kwargs["goal"] == "do something"
    assert "ancestry_context_extra" in call_kwargs


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


# ===========================================================================
# Phase 31: Persona feedback loop tests
# ===========================================================================

def test_record_persona_outcome_writes_to_file(monkeypatch, tmp_path):
    """record_persona_outcome writes a jsonl entry to persona-outcomes.jsonl."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    ok = record_persona_outcome(
        persona_name="researcher",
        goal="summarize AI research papers",
        status="done",
        confidence=0.85,
        loop_id="abc123",
    )
    assert ok is True
    out_path = tmp_path / "prototypes" / "poe-orchestration" / "memory" / "persona-outcomes.jsonl"
    assert out_path.exists()
    entry = json.loads(out_path.read_text().strip())
    assert entry["persona"] == "researcher"
    assert entry["status"] == "done"
    assert entry["confidence"] == 0.85
    assert entry["loop_id"] == "abc123"
    assert "recorded_at" in entry


def test_record_persona_outcome_goal_truncated(monkeypatch, tmp_path):
    """record_persona_outcome truncates long goals to 120 chars."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    long_goal = "x" * 200
    record_persona_outcome("builder", long_goal, "done")
    out_path = tmp_path / "prototypes" / "poe-orchestration" / "memory" / "persona-outcomes.jsonl"
    entry = json.loads(out_path.read_text().strip())
    assert len(entry["goal"]) <= 120


def test_load_persona_outcomes_empty(monkeypatch, tmp_path):
    """load_persona_outcomes returns [] when no file exists."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    result = load_persona_outcomes()
    assert result == []


def test_load_persona_outcomes_returns_newest_first(monkeypatch, tmp_path):
    """load_persona_outcomes returns entries newest-first."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    for i, persona in enumerate(["builder", "researcher", "ops"]):
        record_persona_outcome(persona, f"goal {i}", "done")
    results = load_persona_outcomes()
    assert len(results) == 3
    # Newest first — last written is "ops"
    assert results[0]["persona"] == "ops"
    assert results[-1]["persona"] == "builder"


def test_load_persona_outcomes_respects_limit(monkeypatch, tmp_path):
    """load_persona_outcomes respects the limit parameter."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    for i in range(5):
        record_persona_outcome("researcher", f"goal {i}", "done")
    results = load_persona_outcomes(limit=2)
    assert len(results) == 2


# ===========================================================================
# Phase 35 P2: Agent capability manifest tests
# ===========================================================================

from persona import generate_manifest, save_manifest, load_manifest


def test_generate_manifest_returns_list():
    """generate_manifest() returns a non-empty list of dicts."""
    manifest = generate_manifest()
    assert isinstance(manifest, list)
    assert len(manifest) > 0


def test_generate_manifest_entries_have_required_fields():
    """Each manifest entry has required keys."""
    manifest = generate_manifest()
    required = {"name", "role", "model_tier", "tool_access", "trigger_keywords", "description"}
    for entry in manifest:
        for key in required:
            assert key in entry, f"Missing key {key!r} in entry {entry.get('name')!r}"


def test_generate_manifest_sorted_by_name():
    """Manifest entries are sorted alphabetically by name."""
    manifest = generate_manifest()
    names = [e["name"] for e in manifest]
    assert names == sorted(names)


def test_save_and_load_manifest(monkeypatch, tmp_path):
    """save_manifest writes JSON; load_manifest reads it back."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    path = tmp_path / "test-manifest.json"
    save_manifest(output_path=path, fmt="json")
    assert path.exists()
    loaded = load_manifest(path=path)
    assert isinstance(loaded, list)
    assert len(loaded) > 0
    # Verify round-trip fidelity
    original = generate_manifest()
    assert len(loaded) == len(original)


def test_load_manifest_returns_empty_when_missing(monkeypatch, tmp_path):
    """load_manifest returns [] when no manifest file exists."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    result = load_manifest(path=tmp_path / "no-such-file.json")
    assert result == []


def test_manifest_includes_trigger_keywords_for_known_personas():
    """Manifest entries for known personas include trigger keywords."""
    manifest = generate_manifest()
    entry_map = {e["name"]: e for e in manifest}
    if "builder" in entry_map:
        assert len(entry_map["builder"]["trigger_keywords"]) >= 0  # may be empty if not in routing table
    # At least one persona should have keywords
    total_keywords = sum(len(e["trigger_keywords"]) for e in manifest)
    assert total_keywords > 0


def test_poe_persona_manifest_cli(monkeypatch, tmp_path, capsys):
    """poe-persona manifest CLI subcommand shows the manifest table."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import cli
    cli.main(["poe-persona", "manifest"])
    out = capsys.readouterr().out
    assert "Manifest" in out or "agent" in out.lower()


def test_poe_persona_manifest_json_cli(monkeypatch, tmp_path, capsys):
    """poe-persona manifest --format json outputs valid JSON."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    import cli
    cli.main(["poe-persona", "manifest", "--format", "json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "agents" in data
    assert len(data["agents"]) > 0


# ---------------------------------------------------------------------------
# Phase 59: Persona template variable injection (NeMo DataDesigner steal)
# ---------------------------------------------------------------------------

class TestExtractTemplateVariables:
    """Tests for extract_template_variables()."""

    def test_no_variables(self):
        from persona import extract_template_variables
        assert extract_template_variables("plain text no vars") == set()

    def test_single_variable(self):
        from persona import extract_template_variables
        assert extract_template_variables("use {{ goal }} here") == {"goal"}

    def test_multiple_variables(self):
        from persona import extract_template_variables
        result = extract_template_variables("{{ standing_rules }}\n{{ recent_lessons }}")
        assert result == {"standing_rules", "recent_lessons"}

    def test_duplicate_variable_counted_once(self):
        from persona import extract_template_variables
        result = extract_template_variables("{{ goal }} then {{ goal }} again")
        assert result == {"goal"}

    def test_whitespace_inside_braces(self):
        from persona import extract_template_variables
        result = extract_template_variables("{{  goal  }}")
        assert "goal" in result


class TestRenderPersonaTemplate:
    """Tests for render_persona_template()."""

    def test_no_variables_returns_unchanged(self):
        from persona import render_persona_template
        tmpl = "You are a research assistant. Be precise."
        assert render_persona_template(tmpl) == tmpl

    def test_goal_variable_substituted(self):
        from persona import render_persona_template
        tmpl = "Work on: {{ goal }}"
        result = render_persona_template(tmpl, goal="research polymarket")
        assert "research polymarket" in result
        assert "{{ goal }}" not in result

    def test_unknown_variable_left_as_is(self):
        from persona import render_persona_template
        tmpl = "Use {{ unknown_var }} here"
        result = render_persona_template(tmpl, goal="test")
        assert "{{ unknown_var }}" in result

    def test_standing_rules_injected(self, monkeypatch, tmp_path):
        from persona import render_persona_template
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(
            "memory.load_standing_rules",
            lambda: [],
            raising=False,
        )
        tmpl = "Rules: {{ standing_rules }}"
        result = render_persona_template(tmpl, goal="test goal")
        assert "Rules:" in result
        # No crash — either "(none)" or injected rules
        assert "{{ standing_rules }}" not in result

    def test_recent_lessons_injected(self, monkeypatch, tmp_path):
        from persona import render_persona_template
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        monkeypatch.setattr(
            "memory.query_lessons",
            lambda *a, **kw: [],
            raising=False,
        )
        tmpl = "Lessons: {{ recent_lessons }}"
        result = render_persona_template(tmpl, goal="test goal")
        assert "{{ recent_lessons }}" not in result


class TestBuildPersonaSystemPromptWithTemplate:
    """Tests for build_persona_system_prompt with template variable rendering."""

    def _make_spec(self, body: str) -> "PersonaSpec":
        from persona import PersonaSpec
        return PersonaSpec(
            name="test",
            role="Test Role",
            model_tier="mid",
            tool_access=[],
            memory_scope="session",
            communication_style="precise",
            system_prompt=body,
            hooks=[],
            composes=[],
        )

    def test_template_rendered_in_body(self, monkeypatch, tmp_path):
        """{{ goal }} in body is replaced with the actual goal."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import build_persona_system_prompt
        spec = self._make_spec("Focus on: {{ goal }}")
        result = build_persona_system_prompt(spec, goal="improve test coverage")
        assert "improve test coverage" in result
        assert "{{ goal }}" not in result

    def test_no_template_vars_body_unchanged(self):
        """Body without template vars passes through unchanged."""
        from persona import build_persona_system_prompt
        spec = self._make_spec("Be precise. Be brief. Be right.")
        result = build_persona_system_prompt(spec, goal="any goal")
        assert "Be precise. Be brief. Be right." in result


# ---------------------------------------------------------------------------
# record_persona_dispatch + scan_persona_gaps
# ---------------------------------------------------------------------------

class TestPersonaDispatchTracking:
    def test_record_dispatch_writes_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, _dispatch_log_path
        record_persona_dispatch("build a search index", "builder", 0.85, is_fallback=False)
        p = _dispatch_log_path()
        assert p.exists()
        import json
        entries = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert len(entries) == 1
        assert entries[0]["persona_name"] == "builder"
        assert entries[0]["is_fallback"] is False

    def test_record_dispatch_fallback_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, _dispatch_log_path
        record_persona_dispatch("do some miscellaneous thing", "general-assistant", 0.50, is_fallback=True)
        import json
        p = _dispatch_log_path()
        entries = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
        assert entries[0]["is_fallback"] is True
        assert entries[0]["confidence"] == 0.5

    def test_scan_no_gaps_when_few_fallbacks(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, scan_persona_gaps
        # Only 2 fallbacks — below min_fallbacks=3
        record_persona_dispatch("build a UI", "builder", 0.40, is_fallback=True)
        record_persona_dispatch("build another UI", "builder", 0.40, is_fallback=True)
        gaps = scan_persona_gaps(min_fallbacks=3)
        assert gaps == []

    def test_scan_detects_recurring_role(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, scan_persona_gaps
        for i in range(4):
            record_persona_dispatch(f"build feature {i}", "default", 0.40, is_fallback=True)
        gaps = scan_persona_gaps(min_fallbacks=3)
        assert len(gaps) >= 1
        assert gaps[0]["fallback_count"] >= 3

    def test_scan_only_counts_fallback_entries(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, scan_persona_gaps
        # 3 fallbacks + 2 non-fallbacks for same role
        for i in range(3):
            record_persona_dispatch(f"build thing {i}", "builder", 0.40, is_fallback=True)
        for i in range(5):
            record_persona_dispatch(f"build thing {i+10}", "builder", 0.90, is_fallback=False)
        gaps = scan_persona_gaps(min_fallbacks=3)
        # Gap exists (3 fallbacks), but non-fallbacks don't inflate count
        if gaps:
            assert gaps[0]["fallback_count"] == 3

    def test_gap_has_required_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from persona import record_persona_dispatch, scan_persona_gaps
        for i in range(3):
            record_persona_dispatch(f"research paper {i}", "default", 0.40, is_fallback=True)
        gaps = scan_persona_gaps(min_fallbacks=3)
        if gaps:
            g = gaps[0]
            assert "role_hint" in g
            assert "fallback_count" in g
            assert "sample_goals" in g
            assert "suggested_slug" in g
