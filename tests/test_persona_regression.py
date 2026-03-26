"""Persona regression tests — 3 assertions per registered persona.

These are smoke tests, not unit tests. They verify that every persona in
personas/ is: (1) loadable with valid fields, (2) spawnable in dry-run mode,
and (3) produces a non-trivial system prompt. New personas added to personas/
are automatically covered without editing this file.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from persona import PersonaRegistry, build_persona_system_prompt, spawn_persona

_REGISTRY = PersonaRegistry()
_ALL_PERSONAS = [spec.name for spec in _REGISTRY.load_all()]

assert _ALL_PERSONAS, "No personas found — check PersonaRegistry path resolution"


# ---------------------------------------------------------------------------
# Regression 1: every persona loads with required, valid fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persona_name", _ALL_PERSONAS)
def test_persona_loads_valid_fields(persona_name):
    """Persona registry returns a spec with all required non-empty fields."""
    spec = _REGISTRY.load(persona_name)
    assert spec is not None, f"Registry returned None for {persona_name!r}"
    assert spec.name == persona_name, f"name mismatch: {spec.name!r} != {persona_name!r}"
    assert spec.role.strip(), f"{persona_name}: role is empty"
    assert spec.model_tier in ("power", "mid", "cheap"), (
        f"{persona_name}: invalid model_tier {spec.model_tier!r}"
    )
    assert spec.memory_scope in ("session", "project", "global"), (
        f"{persona_name}: invalid memory_scope {spec.memory_scope!r}"
    )
    assert spec.system_prompt.strip(), f"{persona_name}: system_prompt is empty"


# ---------------------------------------------------------------------------
# Regression 2: every persona dry-runs without error
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persona_name", _ALL_PERSONAS)
def test_persona_dry_run_spawn(monkeypatch, tmp_path, persona_name):
    """Dry-run spawn returns dry_run status and correct persona name."""
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    (tmp_path / "memory").mkdir()

    result = spawn_persona(persona_name, goal="describe your role in one sentence", dry_run=True)

    assert result.status == "dry_run", (
        f"{persona_name}: expected status='dry_run', got {result.status!r}"
    )
    assert result.persona_name == persona_name, (
        f"persona_name mismatch: {result.persona_name!r} != {persona_name!r}"
    )
    assert result.model_tier in ("power", "mid", "cheap"), (
        f"{persona_name}: invalid model_tier in SpawnResult: {result.model_tier!r}"
    )


# ---------------------------------------------------------------------------
# Regression 3: system prompt has substance and includes the persona's role
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("persona_name", _ALL_PERSONAS)
def test_persona_system_prompt_has_substance(persona_name):
    """System prompt is non-trivial and includes the persona role string."""
    spec = _REGISTRY.load(persona_name)
    prompt = build_persona_system_prompt(spec, goal="test goal")

    assert len(prompt) > 100, (
        f"{persona_name}: system prompt suspiciously short ({len(prompt)} chars)"
    )
    assert spec.role in prompt, (
        f"{persona_name}: role {spec.role!r} not found in system prompt"
    )
    # Communication style should appear somewhere in the header
    if spec.communication_style:
        style_words = spec.communication_style.split(",")[0].strip().lower()
        assert style_words in prompt.lower(), (
            f"{persona_name}: communication_style first word {style_words!r} not in prompt"
        )
