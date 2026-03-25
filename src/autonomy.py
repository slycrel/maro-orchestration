#!/usr/bin/env python3
"""Phase 13: Autonomy tier system for Poe orchestration.

Three tiers govern how much human oversight is required before executing actions:

  TIER_MANUAL  — human approves each action before execution
  TIER_SAFE    — auto-execute low-risk (reversible, cheap), escalate high-risk
  TIER_FULL    — autonomous within scope (excludes external posts, money)

Config is loaded from memory/autonomy.json (created with SAFE defaults if absent).
Overrides can be set per-project or per-action-type.

evaluate_action() is synchronous and never makes LLM calls — pure heuristic logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIER_MANUAL = "manual"
TIER_SAFE = "safe"
TIER_FULL = "full"

_VALID_TIERS = {TIER_MANUAL, TIER_SAFE, TIER_FULL}

# Actions that are always treated as high-risk regardless of tier
_HIGH_RISK_ACTIONS = {"post_externally", "spend_money"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class AutonomyConfig:
    default_tier: str = TIER_SAFE
    project_overrides: Dict[str, str] = field(default_factory=dict)  # project → tier
    action_overrides: Dict[str, str] = field(default_factory=dict)   # action_type → tier

    def to_dict(self) -> dict:
        return {
            "default_tier": self.default_tier,
            "project_overrides": dict(self.project_overrides),
            "action_overrides": dict(self.action_overrides),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AutonomyConfig":
        return cls(
            default_tier=d.get("default_tier", TIER_SAFE),
            project_overrides=dict(d.get("project_overrides", {})),
            action_overrides=dict(d.get("action_overrides", {})),
        )


@dataclass
class ActionRequest:
    action_type: str      # "execute_step" | "run_mission" | "deploy" | "post_externally" | "spend_money" | etc.
    project: str
    description: str      # human-readable description of the action
    is_reversible: bool = True
    estimated_cost_usd: float = 0.0


@dataclass
class ActionDecision:
    action: ActionRequest
    tier: str             # tier that applied
    approved: bool
    reason: str           # why approved/denied
    requires_human: bool  # True if tier=manual or action is high-risk


# ---------------------------------------------------------------------------
# Config I/O
# ---------------------------------------------------------------------------

def _config_path() -> Path:
    """Return path to autonomy.json in memory/ directory."""
    try:
        from orch import orch_root
        base = orch_root()
    except Exception:
        base = Path.cwd()
    mem = base / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem / "autonomy.json"


def load_config() -> AutonomyConfig:
    """Load autonomy config from memory/autonomy.json. Returns SAFE defaults if absent."""
    p = _config_path()
    if not p.exists():
        return AutonomyConfig()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return AutonomyConfig.from_dict(data)
    except Exception:
        return AutonomyConfig()


def _save_config(config: AutonomyConfig) -> None:
    """Persist autonomy config to memory/autonomy.json."""
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tier resolution
# ---------------------------------------------------------------------------

def get_tier(project: str = "", action_type: str = "") -> str:
    """Resolve the effective autonomy tier for a project/action combination.

    Resolution order:
      1. action_overrides (most specific)
      2. project_overrides
      3. default_tier
    """
    config = load_config()

    # Action override takes highest precedence
    if action_type and action_type in config.action_overrides:
        tier = config.action_overrides[action_type]
        if tier in _VALID_TIERS:
            return tier

    # Project override next
    if project and project in config.project_overrides:
        tier = config.project_overrides[project]
        if tier in _VALID_TIERS:
            return tier

    # Fall back to default
    if config.default_tier in _VALID_TIERS:
        return config.default_tier

    return TIER_SAFE


# ---------------------------------------------------------------------------
# Action evaluation
# ---------------------------------------------------------------------------

def evaluate_action(request: ActionRequest) -> ActionDecision:
    """Evaluate whether an action should be auto-approved or requires human input.

    This function is synchronous and makes no LLM calls.

    Rules by tier:
      MANUAL: always requires_human=True, approved=False
      SAFE:   approved if is_reversible=True and estimated_cost_usd < 1.0
              escalate (requires_human=True) otherwise
      FULL:   approved unless action_type is in _HIGH_RISK_ACTIONS
    """
    tier = get_tier(project=request.project, action_type=request.action_type)

    if tier == TIER_MANUAL:
        return ActionDecision(
            action=request,
            tier=tier,
            approved=False,
            reason="Manual tier: human approval required for all actions",
            requires_human=True,
        )

    if tier == TIER_SAFE:
        is_low_risk = request.is_reversible and request.estimated_cost_usd < 1.0
        if is_low_risk:
            return ActionDecision(
                action=request,
                tier=tier,
                approved=True,
                reason="Safe tier: action is reversible and low-cost",
                requires_human=False,
            )
        else:
            reason_parts = []
            if not request.is_reversible:
                reason_parts.append("irreversible")
            if request.estimated_cost_usd >= 1.0:
                reason_parts.append(f"cost=${request.estimated_cost_usd:.2f}")
            return ActionDecision(
                action=request,
                tier=tier,
                approved=False,
                reason=f"Safe tier: escalating high-risk action ({', '.join(reason_parts)})",
                requires_human=True,
            )

    # TIER_FULL
    if request.action_type in _HIGH_RISK_ACTIONS:
        return ActionDecision(
            action=request,
            tier=tier,
            approved=False,
            reason=f"Full tier: action type '{request.action_type}' always requires human approval",
            requires_human=True,
        )

    return ActionDecision(
        action=request,
        tier=tier,
        approved=True,
        reason="Full tier: autonomous execution approved",
        requires_human=False,
    )


# ---------------------------------------------------------------------------
# Config mutation helpers
# ---------------------------------------------------------------------------

def set_project_tier(project: str, tier: str) -> None:
    """Set autonomy tier for a specific project. Persists to autonomy.json."""
    if tier not in _VALID_TIERS:
        raise ValueError(f"Invalid tier: {tier!r}. Must be one of {sorted(_VALID_TIERS)}")
    config = load_config()
    config.project_overrides[project] = tier
    _save_config(config)


def set_action_tier(action_type: str, tier: str) -> None:
    """Set autonomy tier for a specific action type. Persists to autonomy.json."""
    if tier not in _VALID_TIERS:
        raise ValueError(f"Invalid tier: {tier!r}. Must be one of {sorted(_VALID_TIERS)}")
    config = load_config()
    config.action_overrides[action_type] = tier
    _save_config(config)


def set_default_tier(tier: str) -> None:
    """Set the default autonomy tier. Persists to autonomy.json."""
    if tier not in _VALID_TIERS:
        raise ValueError(f"Invalid tier: {tier!r}. Must be one of {sorted(_VALID_TIERS)}")
    config = load_config()
    config.default_tier = tier
    _save_config(config)
