#!/usr/bin/env python3
"""Phase 10/14: Skill library for Poe orchestration.

A skill is a reusable execution pattern extracted from completed goal chains.
Skills are injected into future agent_loop._decompose() prompts when a goal
matches trigger patterns.

Phase 14 additions:
- Per-skill success rate tracking (SkillStats, record_skill_outcome)
- Structured three-section markdown format (parse_skill_sections, render_skill_markdown)
- Unit-test gate on skill mutations (SkillTestCase, SkillMutationResult, validate_skill_mutation)
- Hash-based poisoning defense (compute_skill_hash, verify_skill_hash)

Usage:
    from skills import find_matching_skills, format_skills_for_prompt
    skills = find_matching_skills("research polymarket strategies")
    prompt_block = format_skills_for_prompt(skills)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# Module-level imports for clean test patching
try:
    from llm import MODEL_CHEAP, LLMMessage
except ImportError:  # pragma: no cover
    MODEL_CHEAP = "cheap"  # type: ignore[assignment]
    LLMMessage = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ESCALATION_THRESHOLD = 0.4   # success_rate below this → needs redesign


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


@dataclass
class SkillStats:
    """Per-skill success/failure tracking (Phase 14)."""
    skill_id: str
    skill_name: str
    total_uses: int = 0
    successes: int = 0
    failures: int = 0
    last_used: str = ""
    success_rate: float = 1.0    # computed: successes / max(total_uses, 1)
    needs_escalation: bool = False  # success_rate < ESCALATION_THRESHOLD

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
        )


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
# Prompts
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = textwrap.dedent("""\
    You are Poe, a skill extraction agent.
    Analyze successful goal completions and find patterns worth generalizing.
    A skill is a step sequence that solved a class of problems and could be
    reused for similar future goals.
    Identify 1-3 reusable skill patterns. For each skill, extract:
    - A short name (2-4 words)
    - A description of what the skill does
    - 2-4 trigger patterns (phrases in goals/steps that suggest this skill applies)
    - A reusable step template (3-5 steps)
    Respond ONLY with JSON, no prose, no markdown fences.
    JSON shape:
    {
      "skills": [
        {
          "name": "short name",
          "description": "what it does",
          "trigger_patterns": ["pattern1", "pattern2"],
          "steps_template": ["step1", "step2", "step3"]
        }
      ]
    }
""").strip()


# ---------------------------------------------------------------------------
# Lazy orch import
# ---------------------------------------------------------------------------

def _orch():
    import orch
    return orch


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _skills_path() -> Path:
    o = _orch()
    mem = o.orch_root() / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    return mem / "skills.jsonl"


def _skill_to_dict(skill: Skill) -> dict:
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
    }


def _dict_to_skill(d: dict) -> Skill:
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
    )


def load_skills() -> List[Skill]:
    """Load all skills from skills.jsonl. Returns [] if file doesn't exist.

    Phase 14: verifies content_hash for each skill. Logs a warning if hash
    mismatch detected (does not raise — graceful degradation).
    """
    path = _skills_path()
    if not path.exists():
        return []
    skills = []
    seen_ids: set = set()
    lines = path.read_text(encoding="utf-8").splitlines()
    # Last version of each id wins
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            sid = d.get("id", "")
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            skill = _dict_to_skill(d)
            # Phase 14: verify hash if one is recorded
            stored_hash = d.get("content_hash", "")
            if stored_hash:
                expected = compute_skill_hash(skill)
                if not verify_skill_hash(skill, stored_hash):
                    logger.warning(
                        "[skills] content_hash mismatch for skill id=%s name=%r "
                        "(expected=%s stored=%s) — possible tampering",
                        sid, skill.name, expected[:12], stored_hash[:12],
                    )
            skills.insert(0, skill)
        except Exception:
            continue
    return skills


def save_skill(skill: Skill) -> None:
    """Append or update a skill in skills.jsonl.

    Phase 14: always computes and stores content_hash before writing.
    """
    # Always recompute the hash on save
    skill.content_hash = compute_skill_hash(skill)

    path = _skills_path()
    lines: List[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("id") == skill.id:
                    continue  # replaced below
                lines.append(entry)
            except Exception:
                continue
    lines.append(_skill_to_dict(skill))
    path.write_text(
        "\n".join(json.dumps(e) for e in lines) + "\n",
        encoding="utf-8",
    )


def increment_use(skill_id: str) -> None:
    """Increment use_count for a skill by id."""
    skills = load_skills()
    for skill in skills:
        if skill.id == skill_id:
            skill.use_count += 1
            save_skill(skill)
            return


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def extract_skills(outcomes: List[dict], adapter) -> List[Skill]:
    """Analyze successful outcomes and extract reusable skill patterns.

    Args:
        outcomes: List of outcome dicts (from outcomes.jsonl).
        adapter: LLMAdapter for the extraction call.

    Returns:
        List of extracted Skill objects (also saved to skills.jsonl).
    """
    if not outcomes:
        return []

    from llm import LLMMessage, MODEL_MID

    # Summarize outcomes for the prompt
    successes = [o for o in outcomes if o.get("status") == "done"][:20]
    if not successes:
        return []

    outcomes_text = "\n\n".join(
        f"Goal: {o.get('goal', '')}\nTask type: {o.get('task_type', '')}\n"
        f"Summary: {o.get('summary', o.get('result_summary', ''))[:300]}"
        for o in successes
    )

    # Get source loop ids
    source_ids = [
        str(o.get("outcome_id", ""))[:8]
        for o in successes
        if o.get("outcome_id")
    ][:10]

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _EXTRACT_SYSTEM),
                LLMMessage(
                    "user",
                    f"Successful goal completions to analyze:\n\n{outcomes_text}",
                ),
            ],
            max_tokens=2048,
            temperature=0.3,
        )
        content = resp.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(content[start:end])
            raw_skills = data.get("skills", [])
            extracted: List[Skill] = []
            now = datetime.now(timezone.utc).isoformat()
            for rs in raw_skills[:3]:
                skill = Skill(
                    id=str(uuid.uuid4())[:8],
                    name=str(rs.get("name", "unnamed")).strip(),
                    description=str(rs.get("description", "")).strip(),
                    trigger_patterns=[str(p).strip() for p in rs.get("trigger_patterns", []) if str(p).strip()],
                    steps_template=[str(s).strip() for s in rs.get("steps_template", []) if str(s).strip()],
                    source_loop_ids=source_ids,
                    created_at=now,
                )
                if skill.name and skill.steps_template:
                    save_skill(skill)
                    extracted.append(skill)
            return extracted
    except Exception:
        pass

    return []


# ---------------------------------------------------------------------------
# Skill matching + formatting
# ---------------------------------------------------------------------------

def find_matching_skills(goal: str, adapter=None) -> List[Skill]:
    """Find skills whose trigger_patterns match the goal (keyword search).

    Args:
        goal: Goal string to match against.
        adapter: Not used (reserved for future semantic search).

    Returns:
        Top 2 matching skills by number of matching patterns.
    """
    skills = load_skills()
    if not skills:
        return []

    goal_lower = goal.lower()
    scored: List[tuple] = []
    for skill in skills:
        score = sum(
            1 for pattern in skill.trigger_patterns
            if pattern.lower() in goal_lower or goal_lower in pattern.lower()
        )
        if score > 0:
            scored.append((score, skill))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:2]]


def format_skills_for_prompt(skills: List[Skill]) -> str:
    """Format matching skills as a prompt block for injection.

    Returns:
        Formatted string for prepending to decompose system prompt.
        Empty string if no skills.
    """
    if not skills:
        return ""

    lines = ["Reusable skills from past successful goals:"]
    for skill in skills:
        lines.append(f"\nSkill: {skill.name} — {skill.description}")
        lines.append("Steps:")
        for step in skill.steps_template:
            lines.append(f"  - {step}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 14: Hash-based poisoning defense
# ---------------------------------------------------------------------------

def compute_skill_hash(skill: Skill) -> str:
    """SHA256 of skill content (name + description + steps_template joined)."""
    content = "\n".join([
        skill.name,
        skill.description,
        "\n".join(skill.steps_template),
    ])
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def verify_skill_hash(skill: Skill, expected_hash: str) -> bool:
    """Return True if skill content matches the recorded hash."""
    if not expected_hash:
        return True  # No hash to verify against
    return compute_skill_hash(skill) == expected_hash


# ---------------------------------------------------------------------------
# Phase 14: Per-skill success rate tracking (SkillStats)
# ---------------------------------------------------------------------------

def _skill_stats_path() -> Path:
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "skill-stats.jsonl"
    except Exception:
        d = Path.cwd() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "skill-stats.jsonl"


def get_all_skill_stats() -> List[SkillStats]:
    """Load all skill stats records from memory/skill-stats.jsonl."""
    path = _skill_stats_path()
    if not path.exists():
        return []
    stats_map: dict = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                sid = d.get("skill_id", "")
                if sid:
                    stats_map[sid] = SkillStats.from_dict(d)
            except Exception:
                continue
    except Exception:
        pass
    return list(stats_map.values())


def get_skill_stats(skill_id: str) -> Optional[SkillStats]:
    """Return SkillStats for a specific skill_id, or None if unknown."""
    all_stats = get_all_skill_stats()
    for s in all_stats:
        if s.skill_id == skill_id:
            return s
    return None


def record_skill_outcome(skill_id: str, success: bool) -> None:
    """Record a skill invocation outcome (upsert by skill_id in skill-stats.jsonl).

    Recomputes success_rate and needs_escalation after updating counts.
    """
    path = _skill_stats_path()

    # Load all existing records
    all_records: dict = {}
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    sid = d.get("skill_id", "")
                    if sid:
                        all_records[sid] = d
                except Exception:
                    continue
        except Exception:
            pass

    # Find or create the record
    if skill_id in all_records:
        stats = SkillStats.from_dict(all_records[skill_id])
    else:
        # Try to get the skill name
        skill_name = skill_id
        try:
            skills = load_skills()
            for sk in skills:
                if sk.id == skill_id:
                    skill_name = sk.name
                    break
        except Exception:
            pass
        stats = SkillStats(skill_id=skill_id, skill_name=skill_name)

    # Update counts
    stats.total_uses += 1
    if success:
        stats.successes += 1
    else:
        stats.failures += 1
    stats.last_used = datetime.now(timezone.utc).isoformat()
    stats.success_rate = stats.successes / max(stats.total_uses, 1)
    stats.needs_escalation = stats.success_rate < ESCALATION_THRESHOLD

    # Update the map and write back (full rewrite for consistency)
    all_records[skill_id] = stats.to_dict()
    try:
        path.write_text(
            "\n".join(json.dumps(d) for d in all_records.values()) + "\n",
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[skills] record_skill_outcome write failed: %s", e)


def get_skills_needing_escalation() -> List[SkillStats]:
    """Return skill stats where success_rate < ESCALATION_THRESHOLD."""
    return [s for s in get_all_skill_stats() if s.success_rate < ESCALATION_THRESHOLD]


# ---------------------------------------------------------------------------
# Phase 14: Structured three-section markdown format
# ---------------------------------------------------------------------------

def parse_skill_sections(markdown: str) -> dict:
    """Parse a structured skill markdown into {spec, behavior, guardrails} dict.

    Tolerant of missing sections — returns empty string for any missing section.
    """
    result = {"spec": "", "behavior": "", "guardrails": ""}
    if not markdown:
        return result

    # Find section headers (## Spec, ## Behavior, ## Guardrails)
    # Use case-insensitive matching
    section_pattern = re.compile(r"^##\s+(Spec|Behavior|Guardrails)\s*$", re.IGNORECASE | re.MULTILINE)
    matches = list(section_pattern.finditer(markdown))

    if not matches:
        # No structured sections — treat whole content as spec
        result["spec"] = markdown.strip()
        return result

    for i, match in enumerate(matches):
        section_name = match.group(1).lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        content = markdown[start:end].strip()
        if section_name in result:
            result[section_name] = content

    return result


def render_skill_markdown(skill: Skill) -> str:
    """Format a Skill as structured three-section markdown."""
    # Check if description already contains section structure
    sections = parse_skill_sections(skill.description)

    spec_content = sections.get("spec") or skill.description or ""
    behavior_content = sections.get("behavior") or "\n".join(f"- {s}" for s in skill.steps_template)
    guardrails_content = sections.get("guardrails") or ""

    lines = [f"# {skill.name}", ""]
    lines += ["## Spec", spec_content, ""]
    lines += ["## Behavior", behavior_content, ""]
    if guardrails_content:
        lines += ["## Guardrails", guardrails_content, ""]
    else:
        lines += ["## Guardrails", "(none defined)", ""]

    return "\n".join(lines)


def update_skill_section(skill: Skill, section: str, new_content: str) -> Skill:
    """Return a new Skill with the specified section updated.

    Section must be one of: 'spec', 'behavior', 'guardrails'.
    Reconstructs the description from the updated sections.
    """
    import copy
    new_skill = copy.copy(skill)

    # Parse existing sections
    sections = parse_skill_sections(skill.description)
    section_lower = section.lower()
    if section_lower not in ("spec", "behavior", "guardrails"):
        raise ValueError(f"Invalid section name: {section!r}. Must be spec, behavior, or guardrails.")

    sections[section_lower] = new_content

    # Reconstruct description from all three sections
    new_skill.description = (
        f"## Spec\n{sections['spec']}\n\n"
        f"## Behavior\n{sections['behavior']}\n\n"
        f"## Guardrails\n{sections['guardrails']}"
    )

    # Recompute hash since content changed
    new_skill.content_hash = compute_skill_hash(new_skill)
    return new_skill


# ---------------------------------------------------------------------------
# Phase 14: Unit-test gate on skill mutations
# ---------------------------------------------------------------------------

def _skill_tests_path() -> Path:
    try:
        from orch import orch_root
        d = orch_root() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "skill-tests.jsonl"
    except Exception:
        d = Path.cwd() / "memory"
        d.mkdir(parents=True, exist_ok=True)
        return d / "skill-tests.jsonl"


def _save_skill_tests(tests: List[SkillTestCase]) -> None:
    """Append test cases to memory/skill-tests.jsonl."""
    path = _skill_tests_path()
    with path.open("a", encoding="utf-8") as f:
        for t in tests:
            f.write(json.dumps(t.to_dict()) + "\n")


def _load_skill_tests(skill_id: str) -> List[SkillTestCase]:
    """Load test cases for a specific skill_id from skill-tests.jsonl."""
    path = _skill_tests_path()
    if not path.exists():
        return []
    tests: List[SkillTestCase] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("skill_id") == skill_id:
                    tests.append(SkillTestCase.from_dict(d))
            except Exception:
                continue
    except Exception:
        pass
    return tests


_GENERATE_TESTS_SYSTEM = """\
You are generating synthetic test cases for an AI skill.
Given the skill description and failure examples, create 2-3 test cases.

Each test case has:
- input_description: a task description to give the skill
- expected_keywords: 2-4 keywords that should appear in a correct response

Return ONLY a JSON array:
[
  {"input_description": "...", "expected_keywords": ["kw1", "kw2"]},
  ...
]
"""


def generate_skill_tests(
    skill: Skill,
    failure_examples: List[str],
    adapter=None,
) -> List[SkillTestCase]:
    """Generate 2-3 test cases for a skill from failure examples.

    Args:
        skill:            The skill to generate tests for.
        failure_examples: List of stuck_reason strings from failures.
        adapter:          LLMAdapter (cheap model). None → heuristic.

    Returns:
        List of SkillTestCase (also saved to skill-tests.jsonl).
    """
    tests: List[SkillTestCase] = []

    # LLM path
    if adapter is not None and LLMMessage is not None:
        try:
            failure_text = "\n".join(f"- {e[:200]}" for e in failure_examples[:5])
            steps_text = "\n".join(f"- {s}" for s in skill.steps_template[:5])
            user_msg = (
                f"Skill: {skill.name}\n"
                f"Description: {skill.description[:300]}\n"
                f"Steps:\n{steps_text}\n\n"
                f"Failure examples:\n{failure_text}\n\n"
                "Generate 2-3 test cases."
            )
            resp = adapter.complete(
                [
                    LLMMessage("system", _GENERATE_TESTS_SYSTEM),
                    LLMMessage("user", user_msg),
                ],
                max_tokens=512,
                temperature=0.2,
            )
            content = resp.content.strip()
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                raw = json.loads(content[start:end])
                for item in raw[:3]:
                    if isinstance(item, dict):
                        input_desc = str(item.get("input_description", "")).strip()
                        keywords = [str(k).strip() for k in item.get("expected_keywords", []) if str(k).strip()]
                        if input_desc and keywords:
                            tests.append(SkillTestCase(
                                skill_id=skill.id,
                                input_description=input_desc,
                                expected_keywords=keywords,
                                derived_from_failure=failure_examples[0][:200] if failure_examples else "",
                            ))
            if tests:
                _save_skill_tests(tests)
                return tests
        except Exception as e:
            if __debug__:
                print(f"[skills] generate_skill_tests LLM call failed: {e}", file=sys.stderr)

    # Heuristic fallback: generate basic tests from skill's own steps
    failure_hint = failure_examples[0][:100] if failure_examples else "handle errors gracefully"
    heuristic_tests = [
        SkillTestCase(
            skill_id=skill.id,
            input_description=f"Apply the '{skill.name}' skill to: {skill.trigger_patterns[0] if skill.trigger_patterns else 'a typical task'}",
            expected_keywords=[
                skill.name.split()[0] if skill.name else "result",
                skill.steps_template[0].split()[0] if skill.steps_template else "step",
            ],
            derived_from_failure=failure_hint,
        ),
        SkillTestCase(
            skill_id=skill.id,
            input_description=f"Describe how to use the '{skill.name}' skill",
            expected_keywords=["skill", skill.name.split()[0] if skill.name else "skill"],
            derived_from_failure=failure_hint,
        ),
    ]
    _save_skill_tests(heuristic_tests)
    return heuristic_tests


def run_skill_tests(
    skill: Skill,
    tests: List[SkillTestCase],
    adapter=None,
    dry_run: bool = False,
) -> Tuple[int, int]:
    """Run test cases against a skill.

    For each test: prompt the skill with input_description, check if any
    expected_keyword appears in the response.

    Args:
        skill:    Skill to test.
        tests:    List of SkillTestCase to run.
        adapter:  LLMAdapter. None or dry_run → all pass.
        dry_run:  If True, return (len(tests), len(tests)) — all pass.

    Returns:
        Tuple of (passed_count, total_count).
    """
    if not tests:
        return 0, 0

    total = len(tests)

    # No adapter or dry_run: all pass
    if adapter is None or dry_run:
        return total, total

    passed = 0
    for test in tests:
        try:
            # Use the skill as a prompt context
            skill_context = (
                f"You are executing the '{skill.name}' skill.\n"
                f"Description: {skill.description[:200]}\n"
                f"Steps:\n" + "\n".join(f"- {s}" for s in skill.steps_template[:5])
            )
            if LLMMessage is not None:
                resp = adapter.complete(
                    [
                        LLMMessage("system", skill_context),
                        LLMMessage("user", test.input_description),
                    ],
                    max_tokens=256,
                    temperature=0.1,
                )
                output = resp.content.lower()
                if any(kw.lower() in output for kw in test.expected_keywords):
                    passed += 1
        except Exception as e:
            if __debug__:
                print(f"[skills] run_skill_tests test failed: {e}", file=sys.stderr)

    return passed, total


def validate_skill_mutation(
    original: Skill,
    mutated: Skill,
    adapter=None,
) -> SkillMutationResult:
    """Run unit-test gate on a skill mutation before write-back.

    Loads existing test cases for this skill_id. If none exist, generates them
    from recent attribution failures. Blocks the mutation if tests fail.

    Args:
        original: The original Skill object.
        mutated:  The proposed mutated Skill object.
        adapter:  LLMAdapter. None → dry_run (all pass).

    Returns:
        SkillMutationResult indicating pass/block.
    """
    skill_id = original.id

    # Load existing test cases
    tests = _load_skill_tests(skill_id)

    if not tests:
        # Generate test cases from recent failure attributions for this skill
        failure_examples: List[str] = []
        try:
            from attribution import load_attributions
            attributions = load_attributions(limit=20)
            for attr in attributions:
                if attr.failed_skill == original.name:
                    failure_examples.append(attr.raw_reason)
        except Exception:
            pass

        tests = generate_skill_tests(original, failure_examples, adapter=adapter)

    if not tests:
        # No tests available at all — allow the mutation (warn)
        return SkillMutationResult(
            skill_id=skill_id,
            original_skill=original,
            mutated_skill=mutated,
            tests_run=0,
            tests_passed=0,
            blocked=False,
            block_reason="",
        )

    # Run tests against the mutated skill
    dry_run = adapter is None
    passed, total = run_skill_tests(mutated, tests, adapter=adapter, dry_run=dry_run)

    blocked = (not dry_run) and (passed < total)
    block_reason = ""
    if blocked:
        block_reason = f"Mutation failed {total - passed}/{total} tests for skill '{original.name}'"

    return SkillMutationResult(
        skill_id=skill_id,
        original_skill=original,
        mutated_skill=mutated,
        tests_run=total,
        tests_passed=passed,
        blocked=blocked,
        block_reason=block_reason,
    )
