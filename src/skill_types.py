"""Shared skill data types and serialization helpers.

Extracted from skills.py to break the circular import between skills.py and
evolver.py. Both modules import types from here; neither imports the other
for type definitions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Skill:
    id: str
    name: str                       # short name
    description: str                # what this skill does
    trigger_patterns: List[str]     # goal/step patterns that should use this skill
    steps_template: List[str]       # reusable step sequence
    source_loop_ids: List[str]      # loop_ids that produced this skill
    created_at: str
    use_count: int = 0
    success_rate: float = 1.0
    content_hash: str = ""          # Phase 14: SHA256 of content for poisoning defense
    tier: str = "provisional"       # Phase 16: "provisional" (medium) | "established" (long)
    utility_score: float = 1.0      # Phase 32: EMA of recent success/fail (1.0=perfect, 0.0=always fails)
    failure_notes: List[str] = field(default_factory=list)  # Phase 32: recent failure reasons
    consecutive_failures: int = 0   # Phase 32: streak of consecutive failures (resets on success)
    consecutive_successes: int = 0  # Phase 32: streak of consecutive successes (for half-open recovery)
    circuit_state: str = "closed"   # Phase 32: "closed" | "half_open" | "open"
    optimization_objective: str = ""  # Meta-Harness: what the skill should optimize for
    island: str = ""                 # FunSearch: island partition
    variant_of: Optional[str] = None # A/B: parent skill ID if this is a challenger variant
    variant_wins: int = 0            # A/B: times this variant was selected and step succeeded
    variant_losses: int = 0          # A/B: times this variant was selected and step failed
    project: str = ""                # project slug this skill belongs to; "" = global (all projects)


@dataclass
class SkillStats:
    """Per-skill success/failure tracking (Phase 14).

    NeMo DataDesigner steal (Phase 59): extended with cost + latency telemetry
    so evolver can score skills on efficiency (success_rate / cost) not just rate.
    """
    skill_id: str
    skill_name: str
    total_uses: int = 0
    successes: int = 0
    failures: int = 0
    last_used: str = ""
    success_rate: float = 1.0    # computed: successes / max(total_uses, 1)
    needs_escalation: bool = False  # success_rate < ESCALATION_THRESHOLD
    # Phase 59: cost + latency telemetry
    total_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    avg_confidence: float = 1.0   # average confidence tag across uses (1.0 = no data yet)

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "skill_name": self.skill_name,
            "total_uses": self.total_uses,
            "successes": self.successes,
            "failures": self.failures,
            "last_used": self.last_used,
            "success_rate": self.success_rate,
            "needs_escalation": self.needs_escalation,
            "total_cost_usd": self.total_cost_usd,
            "avg_latency_ms": self.avg_latency_ms,
            "avg_confidence": self.avg_confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillStats":
        return cls(
            skill_id=d.get("skill_id", ""),
            skill_name=d.get("skill_name", ""),
            total_uses=d.get("total_uses", 0),
            successes=d.get("successes", 0),
            failures=d.get("failures", 0),
            last_used=d.get("last_used", ""),
            success_rate=float(d.get("success_rate", 1.0)),
            needs_escalation=bool(d.get("needs_escalation", False)),
            total_cost_usd=float(d.get("total_cost_usd", 0.0)),
            avg_latency_ms=float(d.get("avg_latency_ms", 0.0)),
            avg_confidence=float(d.get("avg_confidence", 1.0)),
        )

    def efficiency_score(self) -> float:
        """Cost-adjusted success rate — higher is better.

        Normalises cost per run and weights success rate heavily.
        Returns 0.0 if less than 3 uses (not enough data).
        """
        if self.total_uses < 3:
            return 0.0
        cost_per_run = self.total_cost_usd / max(self.total_uses, 1)
        cost_penalty = min(0.5, cost_per_run * 100)
        return max(0.0, self.success_rate - cost_penalty)


@dataclass
class SkillTestCase:
    """Auto-generated test case for a skill (Phase 14)."""
    skill_id: str
    input_description: str           # what the test asks the skill to do
    expected_keywords: List[str]     # at least one must appear in output
    derived_from_failure: str        # original stuck_reason that motivated this test

    def to_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "input_description": self.input_description,
            "expected_keywords": self.expected_keywords,
            "derived_from_failure": self.derived_from_failure,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillTestCase":
        return cls(
            skill_id=d.get("skill_id", ""),
            input_description=d.get("input_description", ""),
            expected_keywords=d.get("expected_keywords", []),
            derived_from_failure=d.get("derived_from_failure", ""),
        )


@dataclass
class SkillMutationResult:
    """Result of running the unit-test gate on a skill mutation (Phase 14)."""
    skill_id: str
    original_skill: Skill
    mutated_skill: Skill
    tests_run: int
    tests_passed: int
    blocked: bool               # True if mutation failed the gate
    block_reason: str


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def skill_to_dict(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "name": skill.name,
        "description": skill.description,
        "trigger_patterns": skill.trigger_patterns,
        "steps_template": skill.steps_template,
        "source_loop_ids": skill.source_loop_ids,
        "created_at": skill.created_at,
        "use_count": skill.use_count,
        "success_rate": skill.success_rate,
        "content_hash": skill.content_hash,
        "tier": skill.tier,
        "utility_score": skill.utility_score,
        "failure_notes": skill.failure_notes,
        "consecutive_failures": skill.consecutive_failures,
        "consecutive_successes": skill.consecutive_successes,
        "circuit_state": skill.circuit_state,
        "optimization_objective": skill.optimization_objective,
        "island": skill.island,
        "variant_of": skill.variant_of,
        "variant_wins": skill.variant_wins,
        "variant_losses": skill.variant_losses,
        "project": skill.project,
    }


def dict_to_skill(d: dict) -> Skill:
    return Skill(
        id=d["id"],
        name=d["name"],
        description=d["description"],
        trigger_patterns=d.get("trigger_patterns", []),
        steps_template=d.get("steps_template", []),
        source_loop_ids=d.get("source_loop_ids", []),
        created_at=d.get("created_at", ""),
        use_count=d.get("use_count", 0),
        success_rate=d.get("success_rate", 1.0),
        content_hash=d.get("content_hash", ""),
        tier=d.get("tier", "provisional"),
        utility_score=float(d.get("utility_score", 1.0)),
        failure_notes=d.get("failure_notes", []),
        consecutive_failures=int(d.get("consecutive_failures", 0)),
        consecutive_successes=int(d.get("consecutive_successes", 0)),
        circuit_state=d.get("circuit_state", "closed"),
        optimization_objective=d.get("optimization_objective", ""),
        island=d.get("island", ""),
        variant_of=d.get("variant_of", None),
        variant_wins=int(d.get("variant_wins", 0)),
        variant_losses=int(d.get("variant_losses", 0)),
        project=d.get("project", ""),
    )


# ---------------------------------------------------------------------------
# Hash / integrity
# ---------------------------------------------------------------------------

def compute_skill_hash(skill: Skill) -> str:
    """SHA256 of skill content (name + description + steps_template + optimization_objective joined)."""
    content = "\n".join([
        skill.name,
        skill.description,
        "\n".join(skill.steps_template),
        skill.optimization_objective,
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_skill_hash(skill: Skill, expected_hash: str) -> bool:
    """Return True if skill content matches the recorded hash."""
    if not expected_hash:
        return True
    return compute_skill_hash(skill) == expected_hash
