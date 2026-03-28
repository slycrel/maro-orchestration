#!/usr/bin/env python3
"""Phase 22 Stage 5: Rule graduation — zero-cost hardcoded step sequences.

A Rule is an established Skill (pass^3 >= 0.7) that has been promoted out of
the sandbox entirely.  When a goal matches a Rule's trigger patterns, the Rule
returns a deterministic step list directly — no LLM decompose call needed.

Lifecycle:
    Stage 4 Skill (established tier, pass^3 >= 0.7)
        → get_rule_graduation_candidates()  # surfaces eligible skills
        → graduate_skill_to_rule(skill_id)  # human- or auto-triggered
        → Rule active in memory/rules.jsonl
        → find_matching_rule(goal)          # checked in _build_loop_context()
        → demote_rule_to_skill(rule_id)     # Inspector auto-demote on wrong answer

Cost impact:
    Rule hit → zero LLM call for decomposition (save ~200–800 tokens per goal)
    Rule miss → fall through to normal _decompose() (no regression)

Storage: {workspace}/memory/rules.jsonl — one JSON object per line.

Graduation threshold:
    skill.tier == "established" AND pass^3 >= RULE_GRADUATION_THRESHOLD
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RULE_GRADUATION_THRESHOLD = 0.70    # pass^3 must exceed this to graduate
RULE_WRONG_ANSWER_DEMOTE_AT = 3     # auto-demote after this many Inspector flags
RULE_MIN_USES_FOR_GRADUATION = 3    # skill must have been used at least N times


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Rule:
    id: str
    name: str                           # matches source skill name
    description: str
    trigger_patterns: List[str]         # regex patterns to match goals
    steps_template: List[str]           # deterministic steps injected on match
    source_skill_id: str                # which established skill produced this
    graduated_at: str
    use_count: int = 0
    wrong_answer_count: int = 0         # Inspector-detected failures
    active: bool = True                 # False if demoted back to Stage 4


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _rules_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "rules.jsonl"


def load_rules(active_only: bool = True) -> List[Rule]:
    """Load all rules from rules.jsonl, newest first.

    Args:
        active_only: If True (default), exclude demoted rules.
    """
    path = _rules_path()
    if not path.exists():
        return []
    rules: List[Rule] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                r = Rule(**{k: d[k] for k in Rule.__dataclass_fields__ if k in d})
                if not active_only or r.active:
                    rules.append(r)
            except Exception:
                continue
    except Exception:
        return []
    return list(reversed(rules))


def save_rule(rule: Rule) -> None:
    """Append or update a rule in rules.jsonl."""
    path = _rules_path()
    lines: List[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("id") == rule.id:
                    continue  # replaced below
                lines.append(d)
            except Exception:
                continue
    lines.append(rule.__dict__)
    path.write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def find_matching_rule(goal: str) -> Optional[Rule]:
    """Return the first active rule whose trigger patterns match goal, or None.

    Matching is case-insensitive regex OR substring.  First match wins
    (rules are iterated newest-first so recently graduated rules take priority).

    When a rule matches, the caller should use rule.steps_template directly
    instead of calling the LLM to decompose — that's the whole point.
    """
    if not goal:
        return None
    goal_lower = goal.lower()
    for rule in load_rules(active_only=True):
        if not rule.steps_template:
            continue
        for pat in rule.trigger_patterns:
            try:
                if re.search(pat, goal_lower, re.I):
                    return rule
            except re.error:
                # Pattern is not valid regex — fall back to substring match
                if pat.lower() in goal_lower:
                    return rule
    return None


def record_rule_use(rule_id: str) -> bool:
    """Increment use_count for a rule.  Returns True if rule was found."""
    rules_all = load_rules(active_only=False)
    for r in rules_all:
        if r.id == rule_id:
            r.use_count += 1
            save_rule(r)
            return True
    return False


# ---------------------------------------------------------------------------
# Graduation
# ---------------------------------------------------------------------------

def get_rule_graduation_candidates() -> List[dict]:
    """Return established skills that meet the graduation threshold.

    A candidate skill has:
      - tier == "established"
      - use_count >= RULE_MIN_USES_FOR_GRADUATION
      - (success_rate)^3 >= RULE_GRADUATION_THRESHOLD
      - not already graduated (id not in active rules)

    Returns list of dicts with keys: id, name, success_rate, use_count, pass3.
    """
    try:
        from skills import load_skills
    except ImportError:
        return []

    skills = load_skills()
    active_rules = load_rules(active_only=True)
    graduated_skill_ids = {r.source_skill_id for r in active_rules}

    candidates = []
    for s in skills:
        if s.tier != "established":
            continue
        if s.id in graduated_skill_ids:
            continue
        if s.use_count < RULE_MIN_USES_FOR_GRADUATION:
            continue
        pass3 = (s.success_rate ** 3) if s.success_rate > 0 else 0.0
        if pass3 >= RULE_GRADUATION_THRESHOLD:
            candidates.append({
                "id": s.id,
                "name": s.name,
                "success_rate": round(s.success_rate, 3),
                "use_count": s.use_count,
                "pass3": round(pass3, 3),
            })

    return sorted(candidates, key=lambda x: x["pass3"], reverse=True)


def graduate_skill_to_rule(skill_id: str) -> Optional[Rule]:
    """Graduate an established skill to a Rule.

    Checks eligibility (tier=established, pass^3 >= threshold) then writes
    a new Rule entry to rules.jsonl.

    Returns the new Rule on success, None if ineligible or skill not found.
    Never raises.
    """
    try:
        from skills import load_skills
    except ImportError:
        return None

    try:
        skills = load_skills()
        skill = next((s for s in skills if s.id == skill_id or s.name == skill_id), None)
        if skill is None:
            return None

        if skill.tier != "established":
            if __debug__:
                print(
                    f"[rules] graduate_skill_to_rule: {skill.name!r} is tier={skill.tier!r} (need established)",
                    file=sys.stderr,
                )
            return None

        pass3 = skill.success_rate ** 3 if skill.success_rate > 0 else 0.0
        if pass3 < RULE_GRADUATION_THRESHOLD:
            if __debug__:
                print(
                    f"[rules] graduate_skill_to_rule: {skill.name!r} pass^3={pass3:.3f} < {RULE_GRADUATION_THRESHOLD}",
                    file=sys.stderr,
                )
            return None

        import uuid
        rule = Rule(
            id=uuid.uuid4().hex[:8],
            name=skill.name,
            description=skill.description,
            trigger_patterns=list(skill.trigger_patterns),
            steps_template=list(skill.steps_template),
            source_skill_id=skill.id,
            graduated_at=datetime.now(timezone.utc).isoformat(),
        )
        save_rule(rule)
        return rule

    except Exception as e:
        print(f"[rules] graduate_skill_to_rule failed: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------

def record_rule_wrong_answer(rule_id: str) -> Optional[Rule]:
    """Record a wrong-answer signal from the Inspector.

    Increments wrong_answer_count.  If count reaches RULE_WRONG_ANSWER_DEMOTE_AT,
    auto-demotes the rule (sets active=False) so it falls back to Stage 4.

    Returns the updated Rule, or None if not found.
    """
    rules_all = load_rules(active_only=False)
    for r in rules_all:
        if r.id == rule_id:
            r.wrong_answer_count += 1
            if r.wrong_answer_count >= RULE_WRONG_ANSWER_DEMOTE_AT:
                r.active = False
            save_rule(r)
            return r
    return None


def demote_rule_to_skill(rule_id: str) -> bool:
    """Manually demote a rule back to Stage 4 (active=False).

    Returns True if the rule was found and updated.
    """
    rules_all = load_rules(active_only=False)
    for r in rules_all:
        if r.id == rule_id:
            r.active = False
            save_rule(r)
            return True
    return False
