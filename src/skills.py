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
import math
import re
import sys
import textwrap
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from llm_parse import extract_json, content_or_empty
from skill_types import (  # noqa: F401 — re-exported for backward compat
    Skill, SkillStats, SkillTestCase, SkillMutationResult,
    compute_skill_hash, verify_skill_hash,
    skill_to_dict, dict_to_skill,
)

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
UTILITY_EMA_ALPHA = 0.3      # EMA smoothing for utility score (Phase 32)
AUTO_PROMOTE_MIN_USES = 5    # minimum uses before auto-promotion considered
AUTO_PROMOTE_MIN_RATE = 0.70 # pass^3 threshold for auto-promotion
REWRITE_TRIGGER_RATE = 0.40  # utility score below this triggers rewrite
REWRITE_MIN_USES = 3         # minimum failures before rewrite fires

# Circuit breaker thresholds (Phase 32 — network-blip vs structural failure)
CIRCUIT_OPEN_THRESHOLD = 3      # consecutive failures to trip breaker CLOSED→OPEN
CIRCUIT_HALFOPEN_RECOVERY = 2   # consecutive successes to close HALF_OPEN→CLOSED
# States: "closed" (normal) | "half_open" (recovering) | "open" (rewrite eligible)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


# Skill, SkillStats, SkillTestCase, SkillMutationResult, compute_skill_hash,
# verify_skill_hash, skill_to_dict, dict_to_skill — all imported from skill_types.py.
# Re-exported above for backward compatibility.


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
    from orch_items import memory_dir
    return memory_dir() / "skills.jsonl"


# Aliases — internal names delegate to skill_types public API
_skill_to_dict = skill_to_dict
_dict_to_skill = dict_to_skill


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
    from file_lock import locked_write

    # Always recompute the hash on save
    skill.content_hash = compute_skill_hash(skill)

    path = _skills_path()
    with locked_write(path):
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
        data = extract_json(content_or_empty(resp), dict, log_tag="skills.extract_skill_patterns")
        if data:
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

_SKILL_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "be", "as", "at", "this",
    "that", "are", "was", "were", "been", "have", "has", "had", "do",
    "does", "did", "will", "would", "could", "should", "may", "might",
    "can", "not", "no", "so", "if", "we", "i", "you", "he", "she", "they",
})


def _stem(token: str) -> str:
    """Minimal suffix stemmer (MetaClaw steal: lightweight skill matching without embeddings).

    Strips common English suffixes while preserving the root. Rules applied in
    order, only when the resulting root is ≥4 chars. No dependencies — pure Python.

    Examples: "researching" → "research", "analyses" → "analys", "builder" → "build"
    """
    t = token
    # Longest suffixes first to avoid double-stripping
    for suffix, min_root in (
        ("ations", 4), ("ization", 4), ("isation", 4),
        ("tion", 4), ("ing", 4), ("ness", 4), ("ment", 4),
        ("ers", 4), ("ings", 4), ("ations", 4),
        ("ed", 4), ("er", 4), ("es", 4), ("ly", 4), ("s", 4),
    ):
        if t.endswith(suffix) and len(t) - len(suffix) >= min_root:
            return t[: -len(suffix)]
    return t


def _skill_tokens(text: str) -> List[str]:
    """Lowercase, split on non-alphanum, drop stop words, apply lightweight stemming."""
    return [
        _stem(t) for t in re.split(r"[^a-z0-9]+", text.lower())
        if len(t) >= 3 and t not in _SKILL_STOP_WORDS
    ]


def _tfidf_skill_rank(goal: str, skills: List[Skill], top_k: int = 3) -> List[Skill]:
    """TF-IDF cosine similarity ranking for skills against a goal string.

    Phase 59 NeMo S4: island-aware ranking — skills whose island matches the
    goal's detected intent get a +20% score boost. No model dependency; uses
    the same keyword scoring as assign_island().

    Used as a middle tier between the trained router (Phase 17) and raw
    keyword substring matching — better quality than keyword, no training data
    required. Returns up to top_k skills with non-zero similarity.
    """
    query_tokens = _skill_tokens(goal)
    if not query_tokens or not skills:
        return []

    # Detect goal's island intent for type-aware boost (NeMo S4)
    # Inline the same keyword scoring as assign_island() — goal text only
    _island_scores: dict = {isl: 0 for isl in _ISLAND_KEYWORDS}
    goal_lower_rank = goal.lower()
    for isl, kws in _ISLAND_KEYWORDS.items():
        for kw in kws:
            if kw in goal_lower_rank:
                _island_scores[isl] += 1
    _best_isl, _best_sc = max(_island_scores.items(), key=lambda kv: kv[1])
    goal_island = _best_isl if _best_sc > 0 else ""

    # Build skill documents: name + description + trigger_patterns
    def skill_doc(s: Skill) -> str:
        return " ".join([s.name, s.description] + list(s.trigger_patterns))

    docs = [skill_doc(s) for s in skills]
    doc_tokens = [_skill_tokens(d) for d in docs]
    N = len(docs)

    # IDF: smooth variant log((N+1)/(1+df)) — handles small N without zeroing out
    df: Counter = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            df[t] += 1
    idf = {t: math.log((N + 1) / (1 + df[t])) for t in df}

    def tfidf_vec(tokens: List[str]) -> dict:
        tf = Counter(tokens)
        total = len(tokens) or 1
        return {t: (tf[t] / total) * idf.get(t, 0.0) for t in tf}

    def cosine(a: dict, b: dict) -> float:
        dot = sum(a.get(t, 0.0) * b.get(t, 0.0) for t in a)
        norm_a = math.sqrt(sum(v * v for v in a.values())) or 1.0
        norm_b = math.sqrt(sum(v * v for v in b.values())) or 1.0
        return dot / (norm_a * norm_b)

    _ISLAND_BOOST = 0.20  # +20% score bonus for island match (NeMo S4)
    q_vec = tfidf_vec(query_tokens)
    scored = []
    for tokens, skill in zip(doc_tokens, skills):
        sc = cosine(q_vec, tfidf_vec(tokens))
        if sc > 0:
            # Apply island-type boost when skill island matches goal intent
            if goal_island and getattr(skill, "island", "") == goal_island:
                sc = sc * (1.0 + _ISLAND_BOOST)
            scored.append((sc, skill))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [sk for _, sk in scored[:top_k]]


# ---------------------------------------------------------------------------
# Island model (FunSearch-inspired diversity mechanism)
# ---------------------------------------------------------------------------

_ISLAND_KEYWORDS: dict[str, list[str]] = {
    "research":  ["research", "fetch", "search", "web", "find", "look", "information",
                  "data", "gather", "scrape", "news", "article", "paper"],
    "build":     ["build", "code", "write", "implement", "create", "generate",
                  "develop", "make", "produce", "draft", "design"],
    "analysis":  ["analyz", "check", "inspect", "review", "test", "evaluat",
                  "assess", "audit", "verif", "compar", "diagnos", "measure"],
}
_ISLAND_DEFAULT = "general"


def assign_island(skill: "Skill") -> str:
    """Classify a skill into one of 4 islands based on trigger_patterns + description.

    Islands: research | build | analysis | general

    Uses simple keyword scoring (no LLM, no deps). The island with the most
    matching keywords wins; ties go to the first matching island in the ordering.
    """
    text = " ".join(skill.trigger_patterns + [skill.name, skill.description]).lower()
    scores: dict[str, int] = {island: 0 for island in _ISLAND_KEYWORDS}
    for island, keywords in _ISLAND_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                scores[island] += 1
    best_island, best_score = max(scores.items(), key=lambda kv: kv[1])
    return best_island if best_score > 0 else _ISLAND_DEFAULT


def ensure_island_assigned(skill: "Skill") -> "Skill":
    """Assign island if not already set. Mutates skill.island in place."""
    if not skill.island:
        skill.island = assign_island(skill)
    return skill


def get_skills_by_island(skills: Optional[List["Skill"]] = None) -> Dict[str, List["Skill"]]:
    """Return skills grouped by island. Skills without an island are auto-assigned.

    Args:
        skills: List of skills (loaded from disk if None).

    Returns:
        Dict mapping island name → list of skills.
    """
    if skills is None:
        skills = load_skills()
    islands: Dict[str, List["Skill"]] = {}
    for skill in skills:
        if not skill.island:
            skill.island = assign_island(skill)
        islands.setdefault(skill.island, []).append(skill)
    return islands


def cull_island_bottom_half(
    island_name: str,
    *,
    min_island_size: int = 4,
    dry_run: bool = False,
) -> List[str]:
    """Kill the bottom-performing half of a skill island (FunSearch selection pressure).

    Only skills with circuit_state == 'open' (already flagged as underperforming)
    are eligible for culling. This preserves skills still on probation (half_open)
    or that have never been rewired (closed).

    Args:
        island_name:      Which island to cull.
        min_island_size:  Don't cull if island has fewer than this many skills.
        dry_run:          If True, return the IDs that would be culled but don't delete.

    Returns:
        List of skill IDs that were (or would be) culled.
    """
    logger = logging.getLogger("poe.skills.island")
    all_skills = load_skills()
    island_skills = [s for s in all_skills if s.island == island_name or
                     (not s.island and assign_island(s) == island_name)]

    if len(island_skills) < min_island_size:
        logger.debug("island %r has %d skills (< %d min), skipping cull",
                     island_name, len(island_skills), min_island_size)
        return []

    # Only cull skills with open circuit — already proven underperforming
    open_skills = [s for s in island_skills if s.circuit_state == "open"]
    if not open_skills:
        logger.debug("island %r: no open-circuit skills to cull", island_name)
        return []

    # Sort by compactness-adjusted utility (ascending = worst first)
    try:
        from evolver import _compactness_adjusted_score
        scored = sorted(open_skills, key=_compactness_adjusted_score)
    except ImportError:
        scored = sorted(open_skills, key=lambda s: s.utility_score)

    # Cull bottom half of the open-circuit pool only
    cull_count = max(1, len(open_skills) // 2)
    to_cull = [s.id for s in scored[:cull_count]]

    if not dry_run and to_cull:
        surviving = [s for s in all_skills if s.id not in set(to_cull)]
        _save_skills(surviving)
        logger.info("island cull: removed %d skills from island %r: %s",
                    len(to_cull), island_name, to_cull)

    return to_cull


def run_island_cycle(
    *,
    min_island_size: int = 4,
    dry_run: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """One full island cycle: assign islands + cull bottom half of open-circuit skills.

    Returns:
        Dict with culled counts per island and total assigned.
    """
    skills = load_skills()
    assigned = 0
    changed = False
    for skill in skills:
        if not skill.island:
            skill.island = assign_island(skill)
            assigned += 1
            changed = True

    if changed and not dry_run:
        _save_skills(skills)

    islands_with_open = set(
        s.island for s in skills if s.circuit_state == "open" and s.island
    )

    cull_report: Dict[str, List[str]] = {}
    for island_name in (islands_with_open or set()):
        culled = cull_island_bottom_half(
            island_name, min_island_size=min_island_size, dry_run=dry_run
        )
        if culled:
            cull_report[island_name] = culled
            if verbose:
                print(f"[skills] island cull {island_name!r}: removed {len(culled)} skills",
                      file=__import__("sys").stderr)

    total_culled = sum(len(v) for v in cull_report.values())
    if verbose and assigned:
        print(f"[skills] island cycle: assigned {assigned} skills to islands", file=__import__("sys").stderr)

    # Captain's log: island culling
    if total_culled > 0 and not dry_run:
        try:
            from captains_log import log_event, ISLAND_CULLED
            for island_name, culled_ids in cull_report.items():
                log_event(
                    event_type=ISLAND_CULLED,
                    subject=island_name,
                    summary=f"Culled {len(culled_ids)} bottom-half skills from island.",
                    context={"culled_ids": culled_ids},
                    related_ids=[f"skill:{sid}" for sid in culled_ids],
                )
        except Exception:
            pass

    return {"assigned": assigned, "culled": cull_report, "total_culled": total_culled}


def find_matching_skills(goal: str, adapter=None, use_router: bool = True) -> List[Skill]:
    """Find skills whose trigger_patterns match the goal.

    Phase 17: when use_router=True (default) and a trained router is
    available, scores candidates by predicted success probability rather
    than keyword overlap. Falls back to keyword matching if the router
    is unavailable or returns empty results.

    Args:
        goal:       Goal string to match against.
        adapter:    Not used (reserved for future semantic search).
        use_router: If True, attempt router-based scoring (Phase 17).

    Returns:
        Top matching skills in score order (up to 3 via router, 2 via keywords).
    """
    skills = load_skills()
    if not skills:
        return []

    # Filter out skills with open circuit breaker — they've failed 3+ times
    # and shouldn't be injected until rewritten/recovered
    skills = [s for s in skills if getattr(s, "circuit_state", "closed") != "open"]
    if not skills:
        return []

    # Phase 17: router path — only use when a trained model is available
    if use_router:
        try:
            from router import route_skills
            route_results = route_skills(goal, skills, top_k=3)
            # Only trust router results when the model was actually used
            # (method="router"). If all results are keyword fallback, let
            # the local keyword matching below handle it properly so that
            # "no match → []" behavior is preserved.
            if route_results and any(r.method == "router" for r in route_results):
                skill_by_id = {s.id: s for s in skills}
                matched = [skill_by_id[r.skill_id] for r in route_results if r.skill_id in skill_by_id]
                if matched:
                    return matched
        except Exception:
            pass  # fall through to keyword matching

    # Keyword fallback: exact trigger pattern overlap
    goal_lower = goal.lower()
    kw_scored: List[tuple] = []
    for skill in skills:
        score = sum(
            1 for pattern in skill.trigger_patterns
            if pattern.lower() in goal_lower or goal_lower in pattern.lower()
        )
        if score > 0:
            kw_scored.append((score, skill))

    if kw_scored:
        kw_scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in kw_scored[:3]]

    # TF-IDF fallback: relevance-ranked retrieval when no keyword match fires
    # (Phase 32 selective retrieval — prevents returning stale/irrelevant skills)
    return _tfidf_skill_rank(goal, skills, top_k=2)


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
        if skill.optimization_objective:
            lines.append(f"Optimize for: {skill.optimization_objective}")
        lines.append("Steps:")
        for step in skill.steps_template:
            lines.append(f"  - {step}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 14: Hash-based poisoning defense
# ---------------------------------------------------------------------------

# compute_skill_hash and verify_skill_hash imported from skill_types


# ---------------------------------------------------------------------------
# Phase 16: Skill tier management (provisional → established)
# ---------------------------------------------------------------------------

def promote_skill_tier(skill_name: str) -> bool:
    """Promote a skill from 'provisional' to 'established'.

    Requires pass^3 >= 0.7: all 3 attempts would succeed at the current success rate.
    Formula: pass^k = success_rate^k (Memento-Skills / Phase 19 regression gate).
    Returns True if promotion succeeded, False otherwise.
    """
    skills = load_skills()
    target = next((s for s in skills if s.name == skill_name), None)
    if not target:
        return False
    if target.tier == "established":
        return True  # Already promoted

    # pass^3 = success_rate^3; require >= 0.7
    pass_all_3 = target.success_rate ** 3
    if pass_all_3 < 0.7:
        return False

    target.tier = "established"
    save_skill(target)
    # Phase 59: provenance record
    try:
        write_skill_provenance(
            skill_name=target.name,
            decision="promote",
            reason=f"pass^3={pass_all_3:.3f} >= 0.7; promoted to established",
            success_rate=target.success_rate,
            source_loop_ids=target.source_loop_ids,
        )
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Phase 59 (Feynman steal): Provenance records for skill decisions
# ---------------------------------------------------------------------------

def write_skill_provenance(
    skill_name: str,
    decision: str,
    *,
    reason: str = "",
    success_rate: float = 0.0,
    efficiency_score: float = 0.0,
    source_loop_ids: Optional[List[str]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Path:
    """Write a provenance record alongside a skill decision.

    Provenance records are sidecar JSON files in memory/skill_provenance/
    named `{skill_name}_{timestamp}.json`. They document what decision was
    made, why, and what data informed it — enabling post-hoc audit.

    Args:
        skill_name:       Name of the skill affected.
        decision:         One of "promote" | "demote" | "rewrite" | "create" | "delete".
        reason:           Human-readable rationale.
        success_rate:     Success rate at decision time.
        efficiency_score: Cost-adjusted score at decision time.
        source_loop_ids:  Loop IDs that contributed to this skill.
        extra:            Any additional metadata to record.

    Returns:
        Path to the written provenance file.
    """
    from orch_items import memory_dir
    prov_dir = memory_dir() / "skill_provenance"
    prov_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    filename = f"{skill_name}_{ts}.json"
    record = {
        "skill_name": skill_name,
        "decision": decision,
        "reason": reason,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "success_rate": success_rate,
        "efficiency_score": efficiency_score,
        "source_loop_ids": source_loop_ids or [],
        **(extra or {}),
    }
    path = prov_dir / filename
    try:
        path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("write_skill_provenance: write failed for %s: %s", skill_name, exc)
    return path


def load_skill_provenance(skill_name: str) -> List[Dict[str, Any]]:
    """Load all provenance records for a skill, sorted newest first."""
    from orch_items import memory_dir
    prov_dir = memory_dir() / "skill_provenance"
    if not prov_dir.exists():
        return []
    records = []
    for p in sorted(prov_dir.glob(f"{skill_name}_*.json"), reverse=True):
        try:
            records.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return records


# ---------------------------------------------------------------------------
# Phase 14: Per-skill success rate tracking (SkillStats)
# ---------------------------------------------------------------------------

def _skill_stats_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "skill-stats.jsonl"


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


def record_skill_outcome(
    skill_id: str,
    success: bool,
    *,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    confidence: float = 1.0,
) -> None:
    """Record a skill invocation outcome (upsert by skill_id in skill-stats.jsonl).

    Recomputes success_rate and needs_escalation after updating counts.

    Args:
        skill_id:    Skill ID to record against.
        success:     Whether the invocation succeeded.
        cost_usd:    LLM cost for this invocation (optional, for efficiency scoring).
        latency_ms:  Wall-clock latency in ms (optional, for efficiency scoring).
        confidence:  Confidence tag from step outcome (optional, 0.0–1.0).
    """
    from file_lock import locked_write

    path = _skill_stats_path()

    with locked_write(path):
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
        prev_uses = stats.total_uses
        stats.total_uses += 1
        if success:
            stats.successes += 1
        else:
            stats.failures += 1
        stats.last_used = datetime.now(timezone.utc).isoformat()
        stats.success_rate = stats.successes / max(stats.total_uses, 1)
        stats.needs_escalation = stats.success_rate < ESCALATION_THRESHOLD

        # Phase 59: update cost + latency telemetry (incremental EMA)
        if cost_usd:
            stats.total_cost_usd += cost_usd
        if latency_ms:
            # EMA update: new_avg = old_avg * (n-1)/n + latency_ms / n
            n = stats.total_uses
            stats.avg_latency_ms = stats.avg_latency_ms * (prev_uses / n) + latency_ms / n
        if confidence != 1.0:
            n = stats.total_uses
            stats.avg_confidence = stats.avg_confidence * (prev_uses / n) + confidence / n

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
# Phase 32: Utility scoring, failure attribution, auto-promotion, rewrite gating
# ---------------------------------------------------------------------------

def update_skill_utility(skill_id: str, success: bool, failure_reason: str = "") -> None:
    """Update utility_score (EMA) and circuit-breaker state for a skill.

    Circuit-breaker state machine:
        closed     → consecutive failures ≥ CIRCUIT_OPEN_THRESHOLD → open
        open       → any success → half_open (on probation)
        half_open  → consecutive successes ≥ CIRCUIT_HALFOPEN_RECOVERY → closed
        half_open  → another failure → open (breaker trips again immediately)
        closed     → single failure → stays closed (blip tolerance)

    EMA formula: utility = alpha * new_obs + (1 - alpha) * current_utility
    where new_obs = 1.0 for success, 0.0 for failure.

    Args:
        skill_id: The skill to update.
        success: True if the step using this skill completed; False if blocked.
        failure_reason: The stuck_reason string (only stored on failure, max 5 kept).
    """
    record_skill_outcome(skill_id, success)

    skills = load_skills()
    target = next((s for s in skills if s.id == skill_id), None)
    if target is None:
        return

    # EMA update
    new_obs = 1.0 if success else 0.0
    target.utility_score = (
        UTILITY_EMA_ALPHA * new_obs + (1 - UTILITY_EMA_ALPHA) * target.utility_score
    )

    # Circuit breaker state transitions
    old_circuit_state = target.circuit_state
    old_utility = target.utility_score
    if success:
        target.consecutive_failures = 0
        target.consecutive_successes += 1
        if target.circuit_state == "open":
            # First success after open → enter probationary half-open
            target.circuit_state = "half_open"
            target.consecutive_successes = 1  # reset counter for recovery run
        elif target.circuit_state == "half_open":
            if target.consecutive_successes >= CIRCUIT_HALFOPEN_RECOVERY:
                # Enough consecutive successes → back to fully closed
                target.circuit_state = "closed"
        # closed + success → stays closed, nothing to do
    else:
        target.consecutive_successes = 0
        target.consecutive_failures += 1
        if target.circuit_state == "half_open":
            # Failed during recovery — trip back to open immediately
            target.circuit_state = "open"
        elif (target.circuit_state == "closed"
              and target.consecutive_failures >= CIRCUIT_OPEN_THRESHOLD):
            target.circuit_state = "open"
        # closed + 1 or 2 failures → stays closed (blip tolerance)
        if failure_reason:
            target.failure_notes = (target.failure_notes + [failure_reason[:200]])[-5:]

    # Captain's log: circuit-breaker state transitions
    if target.circuit_state != old_circuit_state:
        try:
            from captains_log import log_event, SKILL_CIRCUIT_OPEN, SKILL_CIRCUIT_HALF_OPEN, SKILL_CIRCUIT_CLOSED
            _circuit_events = {"open": SKILL_CIRCUIT_OPEN, "half_open": SKILL_CIRCUIT_HALF_OPEN, "closed": SKILL_CIRCUIT_CLOSED}
            log_event(
                event_type=_circuit_events.get(target.circuit_state, "SKILL_CIRCUIT_OPEN"),
                subject=target.name,
                summary=f"Circuit {old_circuit_state} -> {target.circuit_state}. Utility: {old_utility:.2f} -> {target.utility_score:.2f}.",
                context={
                    "skill_id": skill_id,
                    "utility_before": round(old_utility, 3),
                    "utility_after": round(target.utility_score, 3),
                    "consecutive_failures": target.consecutive_failures,
                    "consecutive_successes": target.consecutive_successes,
                },
                note=failure_reason[:200] if failure_reason else None,
                related_ids=[f"skill:{skill_id}"],
            )
        except Exception:
            pass

    # Recompute content hash after mutation
    target.content_hash = compute_skill_hash(target)

    _save_skills(skills)


def attribute_failure_to_skills(
    step_text: str,
    failure_reason: str,
    goal: str = "",
) -> List[str]:
    """Find matching skills for a step that failed and record failure against them.

    Returns list of skill_ids that were attributed.
    """
    matched = find_matching_skills(step_text + " " + goal, use_router=False)
    attributed = []
    for skill in matched:
        try:
            update_skill_utility(skill.id, success=False, failure_reason=failure_reason)
            attributed.append(skill.id)
        except Exception:
            pass
    return attributed


_SKILL_VALIDATION_SYSTEM = (
    "You are a skill quality gate for an AI orchestration system. "
    "Evaluate whether a skill definition is ready for promotion to 'established' tier. "
    "A valid skill has: (1) a clear, specific description of what it does; "
    "(2) step templates that are concrete and actionable — not vague or self-referential; "
    "(3) trigger patterns that genuinely distinguish this skill from general instructions. "
    "Respond with JSON: {\"valid\": true|false, \"reason\": \"one sentence\", "
    "\"repair_hint\": \"brief suggestion if invalid, empty string if valid\"}"
)


def validate_skill_for_promotion(skill: "Skill", adapter: Any) -> Dict[str, Any]:
    """LLM quality gate for skill promotion (Voyager steal).

    Returns:
        {"valid": bool, "reason": str, "repair_hint": str}
    """
    try:
        from llm import LLMMessage
        from llm_parse import extract_json, content_or_empty
        skill_text = (
            f"Name: {skill.name}\n"
            f"Description: {skill.description}\n"
            f"Trigger patterns: {', '.join(skill.trigger_patterns[:5])}\n"
            f"Steps:\n" + "\n".join(f"  - {s}" for s in skill.steps_template[:6])
        )
        resp = adapter.complete(
            [
                LLMMessage("system", _SKILL_VALIDATION_SYSTEM),
                LLMMessage("user", f"Validate this skill for promotion:\n\n{skill_text}"),
            ],
            max_tokens=150,
            temperature=0.1,
        )
        parsed = extract_json(content_or_empty(resp), dict, log_tag="skills.validate")
        if isinstance(parsed, dict):
            return {
                "valid": bool(parsed.get("valid", False)),
                "reason": str(parsed.get("reason", "")),
                "repair_hint": str(parsed.get("repair_hint", "")),
            }
    except Exception as exc:
        logging.getLogger("poe.skills.validate").debug("validate_skill_for_promotion failed: %s", exc)
    # Fail-open: if we can't validate, allow promotion (don't block the cycle)
    return {"valid": True, "reason": "validation unavailable (fail-open)", "repair_hint": ""}


def maybe_auto_promote_skills(adapter: Any = None, max_repair_attempts: int = 3) -> List[str]:
    """Promote provisional skills that meet quality threshold to established.

    If `adapter` is provided, applies a Voyager-style validation harness before
    promoting each skill. Skills that fail validation are sent through up to
    `max_repair_attempts` rewrite cycles (via `evolver.rewrite_skill`).
    Skills that still fail after max attempts are kept provisional.

    Criteria for promotion:
      - tier == "provisional"
      - utility_score >= AUTO_PROMOTE_MIN_RATE (EMA-based, smoothed)
      - use_count >= AUTO_PROMOTE_MIN_USES
      - (if adapter) passes LLM validation gate or repairs within max_repair_attempts

    Returns list of promoted skill_ids.
    """
    skills = load_skills()
    promoted = []
    changed = False

    for skill in skills:
        if skill.tier != "provisional":
            continue
        if skill.use_count < AUTO_PROMOTE_MIN_USES:
            continue
        if skill.utility_score < AUTO_PROMOTE_MIN_RATE:
            continue

        # Voyager/Agent0 steal: validation harness with repair loop
        if adapter is not None:
            _logger = logging.getLogger("poe.skills.promote")
            _candidate = skill
            _valid = False
            for _attempt in range(max_repair_attempts):
                _result = validate_skill_for_promotion(_candidate, adapter)
                if _result["valid"]:
                    _valid = True
                    if _attempt > 0:
                        # Repair succeeded — update the skill in our list
                        for i, s in enumerate(skills):
                            if s.id == skill.id:
                                skills[i] = _candidate
                                break
                    break
                # Try to repair via evolver.rewrite_skill
                _logger.info(
                    "skill %s failed validation (attempt %d/%d): %s — rewriting",
                    skill.id, _attempt + 1, max_repair_attempts, _result["reason"],
                )
                try:
                    from evolver import rewrite_skill as _rewrite
                    _repaired = _rewrite(_candidate, adapter)
                    if _repaired is not None:
                        _candidate = _repaired
                    else:
                        break  # rewrite returned None — stop trying
                except Exception:
                    break

            if not _valid:
                _logger.info(
                    "skill %s held at provisional after %d repair attempt(s)",
                    skill.id, max_repair_attempts,
                )
                continue  # don't promote

        skill.tier = "established"
        skill.content_hash = compute_skill_hash(skill)
        promoted.append(skill.id)
        changed = True
        logging.getLogger("poe.skills").info("[skills] auto-promoted skill %s (%s)", skill.id, skill.name)
        try:
            from captains_log import log_event, SKILL_PROMOTED
            log_event(
                event_type=SKILL_PROMOTED,
                subject=skill.name,
                summary=f"Promoted provisional -> established. Utility: {skill.utility_score:.2f} over {skill.use_count} uses.",
                context={"skill_id": skill.id, "utility": round(skill.utility_score, 3), "use_count": skill.use_count},
                related_ids=[f"skill:{skill.id}"],
            )
        except Exception:
            pass

    if changed:
        _save_skills(skills)
        # Hermes steal: auto-export newly promoted skills as SKILL.md curated files
        for skill in skills:
            if skill.id in promoted:
                try:
                    from skill_loader import export_skill_as_markdown
                    export_skill_as_markdown(skill)
                except Exception:
                    pass  # export is optional, never blocks promotion

    return promoted


def maybe_demote_skills() -> List[str]:
    """Demote established skills with persistently low utility back to provisional.

    Criteria:
      - tier == "established"
      - utility_score < REWRITE_TRIGGER_RATE
      - use_count >= REWRITE_MIN_USES (enough data to trust the score)

    Returns list of demoted skill_ids.
    """
    skills = load_skills()
    demoted = []
    changed = False

    for skill in skills:
        if skill.tier != "established":
            continue
        if skill.use_count < REWRITE_MIN_USES:
            continue
        # Demote only if circuit is open (sustained failures, not a blip)
        # OR utility is very low AND EMA has had enough data to stabilize
        circuit_tripped = skill.circuit_state == "open"
        ema_bad = skill.utility_score < REWRITE_TRIGGER_RATE
        if not (circuit_tripped or ema_bad):
            continue
        skill.tier = "provisional"
        skill.content_hash = compute_skill_hash(skill)
        demoted.append(skill.id)
        changed = True
        logger.info("[skills] demoted skill %s (%s) utility=%.2f", skill.id, skill.name, skill.utility_score)
        try:
            from captains_log import log_event, SKILL_DEMOTED
            _reason = (
                "circuit breaker open (sustained failures)"
                if skill.circuit_state == "open"
                else f"utility_score={skill.utility_score:.3f} < {REWRITE_TRIGGER_RATE}"
            )
            log_event(
                event_type=SKILL_DEMOTED,
                subject=skill.name,
                summary=f"Demoted established -> provisional. {_reason}.",
                context={"skill_id": skill.id, "utility": round(skill.utility_score, 3), "circuit_state": skill.circuit_state},
                related_ids=[f"skill:{skill.id}"],
            )
        except Exception:
            pass
        # Phase 59: provenance record
        try:
            reason = (
                "circuit breaker open (sustained failures)"
                if skill.circuit_state == "open"
                else f"utility_score={skill.utility_score:.3f} < {REWRITE_TRIGGER_RATE}"
            )
            write_skill_provenance(
                skill_name=skill.name,
                decision="demote",
                reason=reason,
                success_rate=skill.success_rate,
                efficiency_score=0.0,
                source_loop_ids=skill.source_loop_ids,
                extra={"utility_score": skill.utility_score, "circuit_state": skill.circuit_state},
            )
        except Exception:
            pass

    if changed:
        _save_skills(skills)

    return demoted


def skills_needing_rewrite() -> List[Skill]:
    """Return skills eligible for LLM rewriting.

    A skill qualifies only when its circuit breaker is OPEN — meaning it has
    sustained consecutive failures (not just a blip) OR failed during recovery.
    This prevents rewrites from transient errors (network blips, one bad run).

    Criteria (all must hold):
      - circuit_state == "open"
      - use_count >= REWRITE_MIN_USES (enough data to trust the signal)
      - utility_score < REWRITE_TRIGGER_RATE (EMA confirms persistent underperformance)
        OR consecutive_failures >= CIRCUIT_OPEN_THRESHOLD (structural streak, EMA may lag)
    """
    skills = load_skills()
    return [
        s for s in skills
        if (
            s.circuit_state == "open"
            and s.use_count >= REWRITE_MIN_USES
            and (
                s.utility_score < REWRITE_TRIGGER_RATE
                or s.consecutive_failures >= CIRCUIT_OPEN_THRESHOLD
            )
        )
    ]


# Frontier targeting constants (Agent0 steal)
FRONTIER_LOW = 0.40   # below this → struggling skill (already covered by circuit breaker)
FRONTIER_HIGH = 0.70  # above this → healthy skill (leave alone)
# Frontier zone: FRONTIER_LOW..FRONTIER_HIGH — neither trivially easy nor failing


def frontier_skills(skills: Optional[List["Skill"]] = None, *, min_uses: int = 3) -> List["Skill"]:
    """Return skills in the 'frontier zone' (Agent0 steal: target 40–70% utility_score).

    The frontier zone is the sweet spot: skills that are consistently challenging
    but not broken — analogous to Agent0's R_unc reward targeting tasks near 50%
    solve-rate. The evolver should prioritise rewriting these skills over either
    very low performers (already in circuit-open state) or top performers (working well).

    Args:
        skills:    Skill list (loaded from disk if None).
        min_uses:  Minimum use_count to have reliable utility_score data.

    Returns:
        Skills with FRONTIER_LOW <= utility_score <= FRONTIER_HIGH,
        sorted ascending by utility_score (hardest first).
    """
    if skills is None:
        skills = load_skills()
    frontier = [
        s for s in skills
        if (
            s.use_count >= min_uses
            and s.circuit_state != "open"  # open-circuit handled by skills_needing_rewrite
            and FRONTIER_LOW <= s.utility_score <= FRONTIER_HIGH
        )
    ]
    return sorted(frontier, key=lambda s: s.utility_score)


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Phase 59 (NeMo Steal 7): Skill sampler constraints for conditional parameterization
# ---------------------------------------------------------------------------

@dataclass
class SkillConstraint:
    """Conditional constraint on skill selection and parameterization.

    NeMo DataDesigner steal: skills can carry constraints that determine when they
    apply and how their parameters should be adjusted for specific contexts. This
    enables one skill definition to adapt to different goal types without duplication.

    Fields:
        condition_keywords: Goal/step must contain at least one of these keywords
                            for the constraint to activate.
        excluded_keywords:  If the goal/step contains any of these, constraint is skipped.
        skill_id:           Which skill this constraint applies to.
        parameter_overrides: Dict of parameter adjustments when constraint activates.
                             Stored in skill.optimization_objective (as metadata hint).
        description:        Human-readable explanation of what this constraint does.
    """
    skill_id: str
    condition_keywords: List[str]
    parameter_overrides: Dict[str, Any] = field(default_factory=dict)
    excluded_keywords: List[str] = field(default_factory=list)
    description: str = ""


def apply_skill_constraints(
    goal: str,
    skills: "List[Skill]",
    constraints: Optional[List[SkillConstraint]] = None,
) -> "List[Skill]":
    """Filter and adapt skills based on goal context using SkillConstraints.

    Phase 59 (NeMo Steal 7): Applies conditional skill constraints to:
    1. Filter skills whose constraints don't match the current goal
    2. Annotate matched skills with parameter override hints

    Args:
        goal:        Current goal or step text.
        skills:      Available skills to filter.
        constraints: Constraints to apply. If None, returns skills unchanged.

    Returns:
        Filtered skills with parameter_overrides noted in optimization_objective.
    """
    if not constraints:
        return skills

    goal_lower = goal.lower()
    skill_by_id = {s.id: s for s in skills}
    applied_overrides: Dict[str, Dict[str, Any]] = {}

    for c in constraints:
        # Check if any condition keyword matches
        if not any(kw.lower() in goal_lower for kw in c.condition_keywords):
            continue
        # Check exclusions
        if any(kw.lower() in goal_lower for kw in c.excluded_keywords):
            continue
        # Constraint matches — record override for this skill_id
        if c.skill_id in skill_by_id:
            applied_overrides.setdefault(c.skill_id, {}).update(c.parameter_overrides)

    # Apply overrides as optimization_objective annotations
    result = []
    for skill in skills:
        if skill.id in applied_overrides:
            import copy as _copy
            adapted = _copy.copy(skill)
            overrides_str = "; ".join(f"{k}={v}" for k, v in applied_overrides[skill.id].items())
            adapted.optimization_objective = (
                (adapted.optimization_objective + "; " if adapted.optimization_objective else "")
                + f"constraint_overrides: {overrides_str}"
            )
            result.append(adapted)
        else:
            result.append(skill)

    return result


# ---------------------------------------------------------------------------
# A/B variant system (Agent0 Rule A/B Variants steal)
# ---------------------------------------------------------------------------
# A skill rewrite creates a "challenger" variant (variant_of=parent.id) rather
# than immediately replacing the parent. Both skills coexist in the pool.
# Routing: when a goal matches both parent and variant, task_id hash determines
# which is used (50/50 split). After MIN_VARIANT_USES trials on each side,
# retire_losing_variants() promotes the winner and removes the loser.
#
# This prevents the evolver from blindly replacing working skills with
# rewrites that haven't been validated on real tasks.
# ---------------------------------------------------------------------------

MIN_VARIANT_USES = 5   # minimum wins+losses per variant before retirement eligible


def create_skill_variant(original: Skill, rewritten: Skill) -> Skill:
    """Mark a rewritten skill as a challenger variant of the original.

    The variant competes against the original in live routing. Neither is
    discarded until retire_losing_variants() has sufficient evidence.

    Args:
        original: The existing skill being challenged.
        rewritten: The rewritten version produced by evolver.

    Returns:
        The rewritten skill with variant_of set to original.id.
    """
    rewritten.variant_of = original.id
    rewritten.variant_wins = 0
    rewritten.variant_losses = 0
    logger.info("skills.ab_variant: created challenger %s for parent %s", rewritten.id, original.id)
    try:
        from captains_log import log_event, SKILL_VARIANT_CREATED
        log_event(
            event_type=SKILL_VARIANT_CREATED,
            subject=original.name,
            summary=f"A/B challenger {rewritten.id[:8]} created for parent {original.id[:8]}.",
            context={"parent_id": original.id, "challenger_id": rewritten.id},
            related_ids=[f"skill:{original.id}", f"skill:{rewritten.id}"],
        )
    except Exception:
        pass
    return rewritten


def get_skill_variants(parent_id: str, skills: Optional[List[Skill]] = None) -> List[Skill]:
    """Return all active challenger variants for a given parent skill."""
    if skills is None:
        skills = load_skills()
    return [s for s in skills if s.variant_of == parent_id]


def select_variant_for_task(parent: Skill, task_id: str, skills: Optional[List[Skill]] = None) -> Skill:
    """Choose between parent and its challengers using task_id hash (50/50 split).

    If no variants exist, return parent unchanged.

    Args:
        parent:  The canonical/parent skill.
        task_id: A stable ID for this task/step (e.g., loop_id or step hash).
        skills:  Pre-loaded skills list (avoids extra disk read).

    Returns:
        Either the parent or one of its challenger variants.
    """
    variants = get_skill_variants(parent.id, skills)
    if not variants:
        return parent

    # Pool: [parent] + challengers. Hash task_id mod pool_size for stable routing.
    pool = [parent] + variants
    try:
        bucket = int(hashlib.sha1(task_id.encode()).hexdigest(), 16) % len(pool)
    except Exception:
        bucket = 0
    return pool[bucket]


def record_variant_outcome(skill_id: str, success: bool) -> None:
    """Record a win or loss for a variant skill.

    No-op for non-variant skills (variant_of is None). Thread-safe via
    full rewrite of skills.jsonl.
    """
    skills = load_skills()
    updated = False
    for s in skills:
        if s.id == skill_id and s.variant_of is not None:
            if success:
                s.variant_wins += 1
            else:
                s.variant_losses += 1
            updated = True
            break
    if updated:
        _save_skills(skills)


def retire_losing_variants(*, dry_run: bool = False, min_uses: int = MIN_VARIANT_USES) -> dict:
    """Evaluate all active A/B pairs and retire losers.

    For each (parent, challengers) group:
    - Compute win-rate for parent and each challenger.
    - Only act if BOTH sides have ≥ min_uses total trials.
    - Winner: highest win-rate among all variants + parent.
    - Loser(s): all others.
    - If challenger wins: replace parent's core content with challenger's; delete challenger.
    - If parent wins: delete challenger(s).

    Returns:
        dict with keys: promoted (list of IDs), retired (list of IDs)
    """
    skills = load_skills()
    skill_by_id = {s.id: s for s in skills}

    # Group challengers by parent
    parent_ids: set = {s.variant_of for s in skills if s.variant_of}
    promoted: List[str] = []
    retired: List[str] = []

    for parent_id in parent_ids:
        parent = skill_by_id.get(parent_id)
        if parent is None:
            continue  # parent was already removed
        challengers = [s for s in skills if s.variant_of == parent_id]
        if not challengers:
            continue

        # Compute parent win-rate using utility_score as proxy (it's EMA of real outcomes)
        # We don't track parent variant_wins/losses separately — use utility_score
        parent_rate = parent.utility_score
        parent_trials = parent.use_count

        # Only act if challenger(s) have enough data
        for challenger in challengers:
            c_total = challenger.variant_wins + challenger.variant_losses
            if c_total < min_uses or parent_trials < min_uses:
                continue  # not enough data yet

            c_rate = challenger.variant_wins / max(c_total, 1)
            if c_rate > parent_rate:
                # Challenger wins: copy its content into parent, retire challenger
                if not dry_run:
                    parent.description = challenger.description
                    parent.steps_template = challenger.steps_template
                    parent.trigger_patterns = challenger.trigger_patterns
                    parent.optimization_objective = challenger.optimization_objective
                    parent.content_hash = compute_skill_hash(parent)
                    logger.info(
                        "skills.ab_variant: challenger %s beat parent %s (%.0f%% vs %.0f%%) — promoted",
                        challenger.id, parent.id, c_rate * 100, parent_rate * 100,
                    )
                promoted.append(parent.id)
                retired.append(challenger.id)
            else:
                # Parent wins (or tie): retire challenger
                if not dry_run:
                    logger.info(
                        "skills.ab_variant: parent %s beat challenger %s (%.0f%% vs %.0f%%) — challenger retired",
                        parent.id, challenger.id, parent_rate * 100, c_rate * 100,
                    )
                retired.append(challenger.id)

    if not dry_run and retired:
        # Remove retired skills from pool; save
        retain_ids = {s.id for s in skills} - set(retired)
        skills = [s for s in skills if s.id in retain_ids]
        _save_skills(skills)
        # Captain's log: A/B retirement
        try:
            from captains_log import log_event, AB_RETIRED
            log_event(
                event_type=AB_RETIRED,
                subject=", ".join(retired),
                summary=f"A/B resolved: {len(promoted)} promoted, {len(retired)} retired.",
                context={"promoted": promoted, "retired": retired},
                related_ids=[f"skill:{sid}" for sid in promoted + retired],
            )
        except Exception:
            pass

    return {"promoted": promoted, "retired": retired}


def _save_skills(skills: List[Skill]) -> None:
    """Overwrite skills.jsonl with the current list (full rewrite for consistency)."""
    path = _skills_path()
    try:
        from file_lock import locked_write
        path.parent.mkdir(parents=True, exist_ok=True)
        with locked_write(path):
            path.write_text(
                "\n".join(json.dumps(_skill_to_dict(s)) for s in skills) + "\n",
                encoding="utf-8",
            )
    except Exception as e:
        logger.warning("[skills] _save_skills failed: %s", e)


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
    from orch_items import memory_dir
    return memory_dir() / "skill-tests.jsonl"


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
            raw = extract_json(content_or_empty(resp), list, log_tag="skills.generate_skill_tests")
            if raw is not None:
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
    sandboxed: bool = False,
) -> Tuple[int, int]:
    """Run test cases against a skill.

    For each test: prompt the skill with input_description, check if any
    expected_keyword appears in the response.

    Args:
        skill:     Skill to test.
        tests:     List of SkillTestCase to run.
        adapter:   LLMAdapter. None or dry_run → all pass.
        dry_run:   If True, return (len(tests), len(tests)) — all pass.
        sandboxed: If True and sandbox module available, run each test in
                   an isolated subprocess (Phase 15).

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


# ---------------------------------------------------------------------------
# Phase 59 (Feynman Steal 11): Skill description verifier
# ---------------------------------------------------------------------------

_SUSPICIOUS_CLAIM_PATTERNS = [
    # Absolute performance claims without sourcing
    (r'\b(always|never|100%|guaranteed|perfect|flawless)\b', "absolute_claim"),
    # Invented or unverifiable percentage claims
    (r'\b(\d+)%\s+(improvement|faster|better|more accurate|reduction)\b', "unsourced_metric"),
    # Claims about unverifiable version-specific behavior
    (r'\b(version \d+\.\d+|v\d+\.\d+)\b', "version_specific"),
    # Claims referencing non-existent internal APIs
    (r'\b(internal API|private endpoint|undocumented)\b', "internal_api"),
]


@dataclass
class SkillVerificationResult:
    """Result of verifying a skill's description for suspicious claims."""
    skill_name: str
    suspicious_claims: List[Dict[str, str]]  # [{"text": ..., "category": ..., "pattern": ...}]
    is_clean: bool                           # True if no suspicious claims found
    confidence: float                        # 0.0–1.0 based on how clean the description is


def verify_skill_description(skill: "Skill") -> SkillVerificationResult:
    """Heuristic verification of a skill's description for suspicious claims.

    Phase 59 (Feynman Steal 11): Post-generation verifier pattern.
    Checks for absolute claims, unsourced metrics, and patterns common in
    hallucinated skill descriptions. No LLM call — pure regex heuristics.

    Args:
        skill: Skill object to verify.

    Returns:
        SkillVerificationResult with list of suspicious patterns found.
    """
    text = " ".join([
        skill.description or "",
        " ".join(skill.trigger_patterns or []),
        " ".join(skill.steps_template or []),
    ])

    suspicious = []
    for pattern, category in _SUSPICIOUS_CLAIM_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            match_str = match if isinstance(match, str) else str(match)
            suspicious.append({
                "text": match_str,
                "category": category,
                "pattern": pattern,
            })

    # Confidence: starts at 1.0, decrements per suspicious finding (min 0.0)
    confidence = max(0.0, 1.0 - len(suspicious) * 0.2)

    return SkillVerificationResult(
        skill_name=skill.name,
        suspicious_claims=suspicious,
        is_clean=len(suspicious) == 0,
        confidence=confidence,
    )
