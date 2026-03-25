#!/usr/bin/env python3
"""Phase 10: Skill library for Poe orchestration.

A skill is a reusable execution pattern extracted from completed goal chains.
Skills are injected into future agent_loop._decompose() prompts when a goal
matches trigger patterns.

Usage:
    from skills import find_matching_skills, format_skills_for_prompt
    skills = find_matching_skills("research polymarket strategies")
    prompt_block = format_skills_for_prompt(skills)
"""

from __future__ import annotations

import json
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
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
    )


def load_skills() -> List[Skill]:
    """Load all skills from skills.jsonl. Returns [] if file doesn't exist."""
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
            skills.insert(0, _dict_to_skill(d))
        except Exception:
            continue
    return skills


def save_skill(skill: Skill) -> None:
    """Append or update a skill in skills.jsonl."""
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
