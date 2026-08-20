"""Shared skill data types and serialization helpers.

Extracted from skills.py to break the circular import between skills.py and
evolver.py. Both modules import types from here; neither imports the other
for type definitions.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
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
    imported: dict = field(default_factory=dict)  # PORTABLE_LEARNING_DESIGN §3: provenance
                                      # stamp for pack-imported skills; stats get moved to
                                      # imported["claimed_use_count"]/["claimed_success_rate"]
                                      # on import, local use_count/success_rate reset to 0/1.0.
    # Pedigree + discovery metadata (2026-08-08 BACKLOG item; lessons'
    # minted_from is the precedent — stamp at mint, legacy rows stay "").
    origin: str = ""                 # "crystallized" (extracted from run outcomes) |
                                     # "synthesized" (LLM-minted on a gap/suggestion) |
                                     # "imported" (pack import) | "" (pre-stamp legacy)
    domain: str = ""                 # coarse subject area, e.g. "web-research", "git"
    tags: List[str] = field(default_factory=list)  # discovery keywords; fed into
                                     # keyword/TF-IDF matching corpora


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
    # Run-verdict evidence (2026-07-29): counted at closure-verdict time for
    # skills that were ACTUALLY in the run's injected-prompt manifest, label
    # = the run's FULL-trust goal verdict. The legacy counters above credit
    # keyword-matched bystanders with step completions (~1.0 base rate) —
    # inflated; consumers should prefer these where present.
    injected_runs: int = 0        # verdicted runs where this skill was injected
    injected_successes: int = 0   # of those, goal_achieved True
    injected_success_rate: float = 0.0  # successes / max(injected_runs, 1)
    last_injected_verdict_at: str = ""

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
            "injected_runs": self.injected_runs,
            "injected_successes": self.injected_successes,
            "injected_success_rate": self.injected_success_rate,
            "last_injected_verdict_at": self.last_injected_verdict_at,
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
            injected_runs=int(d.get("injected_runs", 0)),
            injected_successes=int(d.get("injected_successes", 0)),
            injected_success_rate=float(d.get("injected_success_rate", 0.0)),
            last_injected_verdict_at=str(d.get("last_injected_verdict_at", "")),
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
        "imported": skill.imported,
        "origin": skill.origin,
        "domain": skill.domain,
        "tags": skill.tags,
    }


def normalize_tags(raw: object, cap: Optional[int] = 6) -> List[str]:
    """One normalizer for every tag boundary (2026-08-08 round-2 review:
    per-site normalization missed the LLM mint sites, and a string value
    iterates into character tags that then keyword-match everything).
    List-only, lowercased, stripped, empties dropped, capped at mint
    (cap=None for read paths — stored rows aren't re-truncated)."""
    if not isinstance(raw, list):
        return []
    out = [str(t).strip().lower() for t in raw if str(t).strip()]
    return out if cap is None else out[:cap]


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
        imported=d.get("imported", {}),
        # Origin derivation-at-read: an imported dict is certain evidence of
        # pack import, so blank-origin legacy rows with one get "imported"
        # for free. Everything else stays "" — crystallized vs synthesized
        # is not reliably derivable retroactively (both carry
        # source_loop_ids), and guessing would violate positive-evidence.
        origin=d.get("origin", "") or ("imported" if d.get("imported") else ""),
        domain=d.get("domain", ""),
        tags=normalize_tags(d.get("tags"), cap=None),
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


def validate_skill_row(d: dict) -> Skill:
    """Build a Skill from a stored row AND prove the row is one. Raises if not.

    `dict_to_skill` is a CONSTRUCTOR, not a validator — Python does not
    enforce dataclass annotations, so `description=7` and
    `trigger_patterns="x"` sail straight through it. Adversarial r3
    (2026-08-20, 5/5 consensus) probed what that costs a DESTRUCTIVE caller:
    in `doctor.cleanup_workspace_skills` a row carrying a healthy skill's
    `content_hash` but `description=7` could not have its hash recomputed,
    `_skill_hash_is_stale` answered "not stale" for the failure, the forgery
    won the dedup on a later `created_at`, and the healthy skill was DELETED.
    Probed: 2 rows in, only `forged` out.

    So: a caller that REMOVES rows must use this, not `dict_to_skill`. A row
    that cannot be proven to be a Skill must never take part in a decision
    about which rows to remove — strand the raw line instead. Read-only
    callers stay on `dict_to_skill`; degrading them would be a behaviour
    change nobody asked for.

    What is proven, and only that: the required keys exist, the content
    fields are text (proven by computing the hash over them), the
    identity/timestamp fields are strings, the list fields are lists of
    strings, and the fields `score_skill` ranks by are finite numbers. All
    423 rows in the live store satisfy it. Non-finite is called out on
    purpose: a NaN `success_rate` makes `max()` ordering undefined, so it can
    win a dedup at random — and `score_skill`'s tuple compares `created_at`
    first, so a non-string there raises TypeError inside `max()` and takes
    the whole verb down.
    """
    for name in ("id", "name", "description"):
        if name not in d:
            raise KeyError(name)     # dict_to_skill's required keys, up front
    for name in _STR_FIELDS:
        _raw_check(d, name, lambda v: isinstance(v, str), "a string")
    for name in ("id", "name", "content_hash"):
        if name in d and not d[name].strip():
            raise ValueError(f"{name} must not be empty")
    if "created_at" in d:             # a timestamp is a RANKING input
        try:
            datetime.fromisoformat(d["created_at"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"created_at is not a timestamp: "
                             f"{d['created_at']!r} ({exc})") from None
    for name in _LIST_OF_STR_FIELDS:
        _raw_check(d, name, lambda v: isinstance(v, list)
                   and all(isinstance(x, str) for x in v), "a list of strings")
    for name in ("success_rate", "utility_score"):
        _raw_check(d, name, lambda v: not isinstance(v, bool)
                   and isinstance(v, (int, float)) and math.isfinite(v),
                   "a finite number")
    for name in ("use_count", "consecutive_failures", "consecutive_successes",
                 "variant_wins", "variant_losses"):
        _raw_check(d, name, lambda v: not isinstance(v, bool)
                   and isinstance(v, int), "an int")
    _raw_check(d, "variant_of", lambda v: v is None or isinstance(v, str),
               "a string or null")
    _raw_check(d, "imported", lambda v: isinstance(v, dict), "an object")

    skill = dict_to_skill(d)
    compute_skill_hash(skill)         # proves the content fields ENCODE
    return skill


def _raw_check(d: dict, name: str, ok, expected: str) -> None:
    """Check the STORED value, not what the constructor made of it.

    Adversarial r5 (2026-08-20, 2 lenses, probed): every check here used to
    read `getattr(skill, ...)` AFTER `dict_to_skill`, which coerces —
    `int()`, `float()`, `normalize_tags()`. So `consecutive_failures: "7"`
    arrived as `7`, `utility_score: true` as `1.0`, `tags: "not-a-list"` as
    `[]`, and each was admitted as a proven value. Those same fields are
    excluded from the dedup identity, so the forged row then wins on
    `created_at` and deletes the healthy one. Coercion is the right answer
    for a tolerant READ path (that is why `dict_to_skill` keeps it); it is
    the wrong answer for a caller about to delete rows, because the thing
    being proven is what the STORE says, not what the constructor could
    make of it. Absent is fine — the default is ours, not the row's.
    """
    if name in d and not ok(d[name]):
        raise TypeError(f"{name} must be {expected}, got {d[name]!r}")


# Every string-typed field the repair verb may compare, rank or carry, and
# every list-of-string field. Named explicitly rather than derived from the
# annotations: `typing.get_type_hints` on a dataclass tells you what the
# author DECLARED, and the whole reason this function exists is that the
# declaration is not enforced. Adversarial r4 (2026-08-20, 5/5) found the
# first cut of this list short by `tier`, `failure_notes` and `tags` — each
# one a field a forged row could carry junk in and still be admitted.
_STR_FIELDS = ("id", "name", "description", "content_hash", "created_at",
               "tier", "circuit_state", "optimization_objective", "island",
               "domain", "project", "origin")
_LIST_OF_STR_FIELDS = ("trigger_patterns", "steps_template", "source_loop_ids",
                       "failure_notes", "tags")
