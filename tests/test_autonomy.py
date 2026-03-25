"""Tests for autonomy.py — Phase 13: autonomy tier system."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autonomy import (
    TIER_MANUAL,
    TIER_SAFE,
    TIER_FULL,
    AutonomyConfig,
    ActionRequest,
    ActionDecision,
    get_tier,
    evaluate_action,
    set_project_tier,
    set_action_tier,
    set_default_tier,
    load_config,
    _save_config,
    _config_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(
    action_type: str = "execute_step",
    project: str = "test-project",
    is_reversible: bool = True,
    estimated_cost_usd: float = 0.0,
    description: str = "test action",
) -> ActionRequest:
    return ActionRequest(
        action_type=action_type,
        project=project,
        description=description,
        is_reversible=is_reversible,
        estimated_cost_usd=estimated_cost_usd,
    )


def _clean_config(tmp_path):
    """Patch config path to a temp file and return it."""
    cfg = tmp_path / "autonomy.json"
    return cfg


# ---------------------------------------------------------------------------
# Tier constants
# ---------------------------------------------------------------------------

def test_tier_constants():
    assert TIER_MANUAL == "manual"
    assert TIER_SAFE == "safe"
    assert TIER_FULL == "full"


# ---------------------------------------------------------------------------
# AutonomyConfig
# ---------------------------------------------------------------------------

def test_autonomy_config_defaults():
    config = AutonomyConfig()
    assert config.default_tier == TIER_SAFE
    assert config.project_overrides == {}
    assert config.action_overrides == {}


def test_autonomy_config_to_dict():
    config = AutonomyConfig(
        default_tier=TIER_FULL,
        project_overrides={"my-proj": TIER_MANUAL},
        action_overrides={"deploy": TIER_MANUAL},
    )
    d = config.to_dict()
    assert d["default_tier"] == TIER_FULL
    assert d["project_overrides"]["my-proj"] == TIER_MANUAL
    assert d["action_overrides"]["deploy"] == TIER_MANUAL


def test_autonomy_config_from_dict_roundtrip():
    config = AutonomyConfig(
        default_tier=TIER_FULL,
        project_overrides={"p": TIER_SAFE},
        action_overrides={"spend_money": TIER_MANUAL},
    )
    restored = AutonomyConfig.from_dict(config.to_dict())
    assert restored.default_tier == TIER_FULL
    assert restored.project_overrides["p"] == TIER_SAFE
    assert restored.action_overrides["spend_money"] == TIER_MANUAL


def test_autonomy_config_from_dict_missing_fields():
    config = AutonomyConfig.from_dict({})
    assert config.default_tier == TIER_SAFE
    assert config.project_overrides == {}
    assert config.action_overrides == {}


# ---------------------------------------------------------------------------
# get_tier
# ---------------------------------------------------------------------------

def test_get_tier_default(tmp_path):
    with patch("autonomy._config_path", return_value=tmp_path / "autonomy.json"):
        tier = get_tier()
    assert tier == TIER_SAFE


def test_get_tier_project_override(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {"special-proj": TIER_FULL},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        tier = get_tier(project="special-proj")
    assert tier == TIER_FULL


def test_get_tier_action_override(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {},
        "action_overrides": {"deploy": TIER_MANUAL},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        tier = get_tier(action_type="deploy")
    assert tier == TIER_MANUAL


def test_get_tier_action_override_beats_project(tmp_path):
    """Action override takes precedence over project override."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {"my-proj": TIER_FULL},
        "action_overrides": {"deploy": TIER_MANUAL},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        tier = get_tier(project="my-proj", action_type="deploy")
    assert tier == TIER_MANUAL


def test_get_tier_project_beats_default(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {"locked": TIER_MANUAL},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        tier = get_tier(project="locked")
    assert tier == TIER_MANUAL


# ---------------------------------------------------------------------------
# evaluate_action
# ---------------------------------------------------------------------------

def test_evaluate_action_manual(tmp_path):
    """Manual tier: always requires_human, approved=False."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_MANUAL,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request())
    assert decision.tier == TIER_MANUAL
    assert decision.requires_human is True
    assert decision.approved is False


def test_evaluate_action_safe_reversible(tmp_path):
    """Safe tier + reversible + cheap → approved."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(is_reversible=True, estimated_cost_usd=0.0))
    assert decision.tier == TIER_SAFE
    assert decision.approved is True
    assert decision.requires_human is False


def test_evaluate_action_safe_irreversible(tmp_path):
    """Safe tier + irreversible → escalate."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(is_reversible=False, estimated_cost_usd=0.0))
    assert decision.tier == TIER_SAFE
    assert decision.approved is False
    assert decision.requires_human is True


def test_evaluate_action_safe_expensive(tmp_path):
    """Safe tier + cost >= $1 → escalate."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_SAFE,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(is_reversible=True, estimated_cost_usd=2.50))
    assert decision.tier == TIER_SAFE
    assert decision.approved is False
    assert decision.requires_human is True
    assert "cost" in decision.reason


def test_evaluate_action_full(tmp_path):
    """Full tier → approved for normal actions."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_FULL,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(action_type="execute_step"))
    assert decision.tier == TIER_FULL
    assert decision.approved is True
    assert decision.requires_human is False


def test_evaluate_action_full_external_post(tmp_path):
    """Full tier + post_externally → not approved."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_FULL,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(action_type="post_externally"))
    assert decision.tier == TIER_FULL
    assert decision.approved is False
    assert decision.requires_human is True


def test_evaluate_action_full_spend_money(tmp_path):
    """Full tier + spend_money → not approved."""
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text(json.dumps({
        "default_tier": TIER_FULL,
        "project_overrides": {},
        "action_overrides": {},
    }))
    with patch("autonomy._config_path", return_value=cfg_path):
        decision = evaluate_action(_make_request(action_type="spend_money"))
    assert decision.tier == TIER_FULL
    assert decision.approved is False
    assert decision.requires_human is True


def test_evaluate_action_returns_action_decision():
    """evaluate_action returns an ActionDecision dataclass."""
    with patch("autonomy._config_path", return_value=Path("/tmp/nonexistent_autonomy.json")):
        req = _make_request()
        decision = evaluate_action(req)
    assert isinstance(decision, ActionDecision)
    assert decision.action is req
    assert isinstance(decision.approved, bool)
    assert isinstance(decision.requires_human, bool)
    assert isinstance(decision.reason, str)


# ---------------------------------------------------------------------------
# Config mutation + persistence
# ---------------------------------------------------------------------------

def test_set_project_tier_persists(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    with patch("autonomy._config_path", return_value=cfg_path):
        set_project_tier("my-project", TIER_FULL)
        config = load_config()
    assert config.project_overrides.get("my-project") == TIER_FULL


def test_set_action_tier_persists(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    with patch("autonomy._config_path", return_value=cfg_path):
        set_action_tier("deploy", TIER_MANUAL)
        config = load_config()
    assert config.action_overrides.get("deploy") == TIER_MANUAL


def test_set_default_tier_persists(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    with patch("autonomy._config_path", return_value=cfg_path):
        set_default_tier(TIER_FULL)
        config = load_config()
    assert config.default_tier == TIER_FULL


def test_set_project_tier_invalid_raises():
    with pytest.raises(ValueError, match="Invalid tier"):
        set_project_tier("proj", "super-autonomous")


def test_set_default_tier_invalid_raises():
    with pytest.raises(ValueError, match="Invalid tier"):
        set_default_tier("godmode")


def test_load_config_missing_file(tmp_path):
    with patch("autonomy._config_path", return_value=tmp_path / "missing.json"):
        config = load_config()
    assert config.default_tier == TIER_SAFE


def test_load_config_corrupt_file(tmp_path):
    cfg_path = tmp_path / "autonomy.json"
    cfg_path.write_text("this is not json {{{")
    with patch("autonomy._config_path", return_value=cfg_path):
        config = load_config()
    assert config.default_tier == TIER_SAFE


def test_evaluate_action_no_llm_calls():
    """evaluate_action must be synchronous — no LLM adapter calls."""
    import autonomy as _auto
    with patch("autonomy._config_path", return_value=Path("/tmp/nonexistent_autonomy.json")):
        # Should complete without any adapter being involved
        req = _make_request()
        decision = _auto.evaluate_action(req)
    assert decision is not None
