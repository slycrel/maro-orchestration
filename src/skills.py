#!/usr/bin/env python3
"""Phase 10/14: Skill library for Maro orchestration.

A skill is a reusable execution pattern extracted from completed goal chains.
Skills are injected into future agent_loop._decompose() prompts when a goal
matches trigger patterns.

Phase 14 additions:
- Per-skill success rate tracking (SkillStats, record_skill_outcome)
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
# Store-hygiene helpers (2026-08-17 silent-drop arc): announced byte-level
# reads + a taint-refusing parse. Probed live before converting: one torn
# byte in skill-stats.jsonl made the keyed rebuild read an EMPTY store and
# the next record_skill_outcome() — which fires on every skill invocation —
# rewrote the whole file down to that one record (4 lines -> 1, silently),
# and one torn byte in skills.jsonl made every future save_skill() raise
# UnicodeDecodeError, write-locking the skill library until hand repair.
from jsonl_utils import (
    is_frame_blank,
    loads_clean as _loads_clean,
    read_jsonl_announced as _read_store,
    store_text as _store_text,
)
from skill_types import (  # noqa: F401 — re-exported for backward compat
    Skill, SkillStats, SkillTestCase, SkillMutationResult,
    compute_skill_hash, verify_skill_hash,
    skill_to_dict, dict_to_skill, normalize_tags,
    validate_skill_row,
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
    You are a skill extraction agent.
    Analyze successful goal completions and find patterns worth generalizing.
    A skill is a step sequence that solved a class of problems and could be
    reused for similar future goals.
    Identify 1-3 reusable skill patterns. For each skill, extract:
    - A short name (2-4 words)
    - A description of what the skill does
    - 2-4 trigger patterns (phrases in goals/steps that suggest this skill applies)
    - A reusable step template (3-5 steps)
    - A domain: one short lowercase phrase naming the subject area
      (e.g. "web-research", "git", "data-analysis")
    - 3-6 tags: lowercase discovery keywords a future goal might contain
    Respond ONLY with JSON, no prose, no markdown fences.
    JSON shape:
    {
      "skills": [
        {
          "name": "short name",
          "description": "what it does",
          "trigger_patterns": ["pattern1", "pattern2"],
          "steps_template": ["step1", "step2", "step3"],
          "domain": "subject-area",
          "tags": ["keyword1", "keyword2", "keyword3"]
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


def _skills_archive_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "skills_archive.jsonl"


def _archive_skills(skills_to_archive: List[Skill], *, reason: str) -> None:
    """Append skills leaving the live pool to the archive.

    Retention decree (2026-07-10): selection pressure (island culls, A/B
    retirement) removes skills from the live pool but never destroys them.
    Append-only JSONL — full skill record plus archived_at/archived_reason.
    """
    if not skills_to_archive:
        return
    from file_lock import locked_append
    path = _skills_archive_path()
    now = datetime.now(timezone.utc).isoformat()
    # The archive IS the retention guarantee, so its writer proves its
    # emission like every other (adversarial r13, Skeptic, probed): a
    # skill mutated in memory to hold a lone surrogate archived as a
    # clean-looking \udcXX escape — a row the strict reader strands —
    # and was then removed from the live pool. Build and prove EVERY
    # line first: a refusal aborts the archive before any append, and
    # the raised error aborts the caller's live-pool removal too.
    from jsonl_utils import prove_record_line
    lines = []
    for s in skills_to_archive:
        rec = _skill_to_dict(s)
        rec["archived_at"] = now
        rec["archived_reason"] = reason
        lines.append(prove_record_line(rec))
    # ONE append for the whole batch (adversarial r14, Failure Operator,
    # probed): per-line appends let a mid-batch failure land a partial
    # batch, and the caller's retry then duplicated the already-landed
    # skills. A single write cannot split the batch. Residual, accepted:
    # a retry after a SUCCESSFUL append that failed later in the caller
    # still duplicates the batch — in an append-only retention store a
    # duplicate is noise, not loss, and the unsafe direction (dropping
    # rows to dedupe) is the one the retention decree forbids.
    # require=True + durable=True (adversarial r15, two seats, probed):
    # the live-pool removal that follows goes through fsyncing
    # atomic_write, but this append rode the page cache — a power loss
    # could keep the deletion and lose the retention copy. The archive
    # must be durable BEFORE the removal is allowed to happen, and the
    # retention writer must never run unlocked.
    locked_append(path, "\n".join(lines), require=True, durable=True)


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
    # Announced read: a torn byte used to raise UnicodeDecodeError into
    # every caller of the skill library.
    rows = _read_store(path, "load_skills")
    drifted = 0
    # Last version of each id wins
    for d in reversed(rows):
        try:
            sid = d.get("id", "")
            if sid in seen_ids:
                continue
            # validate_skill_row, not dict_to_skill. Adversarial r11 (four
            # of five seats, from four different call sites): the loader
            # ADMITTED rows the writers refuse to vouch for, and every list
            # that flows from load_skills into _save_skills carried the
            # mismatch. A coercible-but-unprovable row (utility_score as a
            # string) became a live Skill, _save_skills stranded the raw
            # row AND appended the normalized clone — which, landing last,
            # won last-row-wins: the launder twin again, minted from the
            # gap between two predicates. One predicate now, both ends:
            # admitted == provable. Census: 423/423 live rows pass.
            #
            # AFTER the proof, not before: a schema-drifted row used to
            # claim its id on the way past and then fail, so the newest
            # BROKEN row for an id hid the newest WORKING one (r10) — and
            # r11 showed the worse half: with the id claimed and the row
            # skipped, the older VALID row was in no caller's list, and
            # the next _save_skills deleted it as a deliberate drop.
            skill = validate_skill_row(d)
            seen_ids.add(sid)
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
            drifted += 1
            continue
    if drifted:
        logger.warning("[skills] load_skills: %d row(s) are JSON but not "
                       "loadable as Skill — skipped (%s)", drifted, path)
    return skills


def _prove_line(skill: Skill) -> str:
    """Serialize one skill AND prove the next read will accept the line.

    Adversarial r11 (Skeptic + Architect, both probed): both writers
    emitted rows their own reader refuses. `json.dumps` defaults to
    `allow_nan=True`, so a NaN utility_score wrote the CPython token
    `NaN`; and a lone surrogate in `tier` — a field compute_skill_hash
    never touches — serialized as a clean-looking `\\udcff` escape. Either
    way the save DELETED the prior valid row and replaced it with one
    `loads_clean` strands: the skill vanished from the live pool while
    its bytes sat on disk. Raising HERE aborts the save before the store
    is touched — the file stays intact, which is the safe direction
    (same contract as _read_skill_stats's OSError raise).
    """
    line = json.dumps(_skill_to_dict(skill), allow_nan=False)
    # The COMPLETE admission predicate, not just the byte door (adversarial
    # r12, two seats, probed): r11 moved load_skills onto validate_skill_row
    # and left this proof on loads_clean alone — so a constructible Skill
    # with `tier=7` (hash-excluded, JSON-clean) was emitted, REPLACED the
    # healthy row, and stranded on the next load. A writer proves what its
    # reader will ADMIT, and the reader admits via validate_skill_row.
    validate_skill_row(_loads_clean(line))
    return line


def save_skill(skill: Skill) -> None:
    """Append or update a skill in skills.jsonl.

    Phase 14: always computes and stores content_hash before writing.
    """
    from file_lock import locked_write

    # Always recompute the hash on save
    skill.content_hash = compute_skill_hash(skill)

    path = _skills_path()
    # require=True (r16): a keyed-store RMW must not run unlocked.
    with locked_write(path, require=True):
        # Unmatched lines are re-emitted VERBATIM (2026-08-17 silent-drop
        # arc): the old shape re-dumped every row (laundering byte-tainted
        # ones), DELETED lines it could not parse, and — because the read
        # was a strict whole-file decode with no guard — raised
        # UnicodeDecodeError out of every save once one torn byte landed,
        # write-locking the skill library. loads_clean refuses tainted
        # lines, so they never id-match.
        out: List[str] = []
        if path.exists():
            for line in _store_text(path).split("\n"):
                # Carry the line as it is. r9 (probed on the doctor's twin):
                # `strip()` removes Unicode whitespace that JSON forbids, so
                # the stripped copy could parse when the row does not — and
                # this loop WRITES what it carries, so the row's bytes were
                # rewritten by a save that never claimed to touch them.
                if is_frame_blank(line):
                    continue
                try:
                    # validate_skill_row, not `.get("id")`: adversarial r10
                    # (Minimalist, probed) wrote `{"id":"same","operator_
                    # note":"keep this row"}` into the store and watched a
                    # save of skill `same` delete it. A row that cannot be
                    # PROVEN to be a Skill is not a version of that skill,
                    # so it cannot be the thing this save replaces — which
                    # is the rule validate_skill_row's own docstring has
                    # stated since r3 for exactly this class of caller.
                    row = validate_skill_row(_loads_clean(line))
                except Exception:
                    out.append(line)      # unprovable: carried, never matched
                    continue
                if row.id == skill.id:
                    continue  # replaced below
                out.append(line)
        out.append(_prove_line(skill))
        from file_lock import atomic_write
        atomic_write(path, "\n".join(out) + "\n", errors="surrogateescape")


# NOTE: increment_use (the only Skill.use_count writer) was removed
# 2026-07-29 — it never had a caller, so use_count sat at 0 for 312/314
# live skills and silently starved the frontier gate (and the whole A/B
# variant subsystem behind it). frontier_skills now gates on the honest
# SkillStats.injected_runs counters instead. The use_count field stays on
# the dataclass for serialization/display compat; treat it as legacy-frozen.


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

    # Summarize outcomes for the prompt. Verdict-preferred (SF-2): never
    # crystallize skills from runs judged goal-NOT-achieved (done ≠ achieved);
    # verified-achieved runs are the strongest examples and go first, unjudged
    # done runs are the weaker fallback (absence means "not judged").
    from outcome_policy import is_learnable_outcome
    candidates = [o for o in outcomes if is_learnable_outcome(o)]
    candidates.sort(key=lambda o: o.get("goal_achieved") is not True)  # judged-True first, stable
    successes = candidates[:20]
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
            no_tools=True,
            purpose="skill extraction",
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
                    origin="crystallized",
                    domain=str(rs.get("domain", "")).strip().lower()[:40],
                    tags=normalize_tags(rs.get("tags")),
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

    # Build skill documents: name + description + trigger_patterns + discovery
    # metadata (tags/domain — the pedigree axis exists to be matched on)
    def skill_doc(s: Skill) -> str:
        return " ".join(
            [s.name, s.description, getattr(s, "domain", "")]
            + list(s.trigger_patterns) + list(getattr(s, "tags", []))
        )

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
    top = scored[:top_k]
    for sc, sk in top:
        # Match-tier telemetry (2026-08-08 scout-read item): the fallback
        # tier stamps its cosine so attribution can tell a weak retrieval
        # from a genuine trigger match.
        sk.match_method = "tfidf_fallback"
        sk.match_score = round(sc, 4)
    return [sk for _, sk in top]


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
    text = " ".join(
        skill.trigger_patterns + [skill.name, skill.description,
                                  getattr(skill, "domain", "")]
        + list(getattr(skill, "tags", []))
    ).lower()
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
    """Retire the bottom-performing half of a skill island (FunSearch selection pressure).

    Only skills with circuit_state == 'open' (already flagged as underperforming)
    are eligible for culling. This preserves skills still on probation (half_open)
    or that have never been rewired (closed). Culled skills are moved to
    skills_archive.jsonl, never deleted (retention decree, 2026-07-10).

    Args:
        island_name:      Which island to cull.
        min_island_size:  Don't cull if island has fewer than this many skills.
        dry_run:          If True, return the IDs that would be culled but don't archive.

    Returns:
        List of skill IDs that were (or would be) culled.
    """
    logger = logging.getLogger("maro.skills.island")
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
        cull_set = set(to_cull)
        culled = [s for s in all_skills if s.id in cull_set]
        # Archive BEFORE the pool rewrite (retention decree: a crash between
        # the two leaves a harmless duplicate, never a destroyed skill).
        _archive_skills(culled, reason="island_cull")
        surviving = [s for s in all_skills if s.id not in cull_set]
        _save_skills(surviving, dropped_ids=cull_set,
                     updated_ids=frozenset())
        for s in culled:
            try:
                write_skill_provenance(
                    s.name, "retire",
                    reason=f"island cull: bottom-half of open-circuit pool in {island_name!r}",
                    efficiency_score=s.utility_score,
                    extra={"skill_id": s.id, "island": island_name,
                           "archived_to": "skills_archive.jsonl"},
                )
            except Exception:
                pass
        logger.info("island cull: archived %d skills from island %r: %s",
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
    assigned_ids: set = set()
    for skill in skills:
        if not skill.island:
            skill.island = assign_island(skill)
            assigned += 1
            assigned_ids.add(skill.id)

    if assigned_ids and not dry_run:
        _save_skills(skills, updated_ids=assigned_ids)

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


def find_matching_skills(
    goal: str,
    adapter=None,
    use_router: bool = True,
    project: str = "",
    only_ids=None,
    telemetry: Optional[Dict[str, Any]] = None,
) -> List[Skill]:
    """Find skills whose trigger_patterns match the goal.

    Match-tier telemetry (2026-08-08 scout-read item): every returned skill
    carries `match_method` ("router" | "keyword" | "tfidf_fallback"; on the
    router tier each skill keeps its own RouteResult.method, and the
    record-level telemetry says "mixed" when a degraded batch mixes model
    and per-skill-fallback results) and
    `match_score` (router success probability / trigger-overlap count /
    TF-IDF cosine) as dynamic attributes, and a caller-supplied `telemetry`
    dict is filled with {method, n_candidates, top_score, scores} — method
    is "none" when nothing matched, which turns the old binary gap signal
    ("empty match set") into a graded one. Selection-time truth lands in
    the run's skills manifest via the injection sites.

    Phase 17: when use_router=True (default) and a trained router is
    available, scores candidates by predicted success probability rather
    than keyword overlap. Falls back to keyword matching if the router
    is unavailable or returns empty results.

    Args:
        goal:       Goal string to match against.
        adapter:    Not used (reserved for future semantic search).
        use_router: If True, attempt router-based scoring (Phase 17).
        project:    Project slug for isolation. When non-empty, only skills
                    with project=="" (global) or project==this value are
                    considered. Empty string disables filtering (legacy).
        only_ids:   Restrict candidates to these skill ids (the run's
                    injected manifest at attribution time). When given,
                    A/B challengers in the set stay eligible — they were
                    the routed arm. When None (candidate discovery),
                    challengers are excluded: a challenger is reachable
                    ONLY via its parent's routing.

    Returns:
        Top matching skills in score order (up to 3 via router, 2 via keywords).
    """
    def _note(method: str, scored_pairs, n_candidates: int) -> None:
        """Stamp match attrs on the winners and fill the telemetry dict."""
        for sk, sc in scored_pairs:
            sk.match_method = method
            sk.match_score = round(float(sc), 4)
        if telemetry is not None:
            telemetry.update({
                "method": method if scored_pairs else "none",
                "n_candidates": n_candidates,
                "top_score": round(float(scored_pairs[0][1]), 4) if scored_pairs else 0.0,
                "scores": {sk.id: round(float(sc), 4) for sk, sc in scored_pairs},
            })

    skills = load_skills()
    if not skills:
        _note("none", [], 0)
        return []

    # Project isolation: keep global skills (project=="") and project-specific
    # skills belonging to the current project. Silently excludes skills that
    # belong to a *different* project.
    if project:
        skills = [
            s for s in skills
            if not getattr(s, "project", "") or getattr(s, "project", "") == project
        ]

    # Filter out skills with open circuit breaker — they've failed 3+ times
    # and shouldn't be injected until rewritten/recovered
    skills = [s for s in skills if getattr(s, "circuit_state", "closed") != "open"]

    if only_ids is not None:
        _only = {str(i) for i in only_ids}
        skills = [s for s in skills if s.id in _only]
    else:
        # A/B challengers are not independent candidates (adversarial
        # review 2026-08-06 R3-2): a challenger sharing its parent's
        # triggers used to match alongside it, making the arms
        # non-exclusive ([parent, challenger] — or the challenger twice)
        # and crediting both on every outcome. A challenger enters a
        # prompt ONLY when select_variant_for_task routes its parent to
        # it.
        skills = [s for s in skills if not getattr(s, "variant_of", None)]
    if not skills:
        _note("none", [], 0)
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
                routed = [(skill_by_id[r.skill_id], r.score, r.method)
                          for r in route_results
                          if r.skill_id in skill_by_id]
                if routed:
                    # Telemetry method is "router" only when every winner
                    # actually came from the model — a mixed batch (one
                    # candidate's inference failed → per-skill keyword
                    # fallback) reports "mixed", and each skill keeps its
                    # own RouteResult.method (round-2 review: stamping all
                    # winners "router" recorded false provenance exactly in
                    # the degraded cases).
                    _pairs = [(sk, sc) for sk, sc, _ in routed]
                    _methods = {m for _, _, m in routed}
                    _note("router" if _methods == {"router"} else "mixed",
                          _pairs, len(skills))
                    for sk, _sc, m in routed:
                        sk.match_method = m
                    return [sk for sk, _, _ in routed]
        except Exception:
            pass  # fall through to keyword matching

    # Keyword fallback: exact trigger pattern overlap
    goal_lower = goal.lower()
    kw_scored: List[tuple] = []
    for skill in skills:
        # Tags count as trigger phrases here: substring-in-goal only (a tag
        # is a short keyword — the reverse goal-in-tag test would be noise).
        score = sum(
            1 for pattern in skill.trigger_patterns
            if pattern.lower() in goal_lower or goal_lower in pattern.lower()
        ) + sum(
            1 for tag in getattr(skill, "tags", [])
            if tag and tag.lower() in goal_lower
        )
        if score > 0:
            kw_scored.append((score, skill))

    if kw_scored:
        kw_scored.sort(key=lambda x: x[0], reverse=True)
        top_kw = kw_scored[:3]
        _note("keyword", [(s, sc) for sc, s in top_kw], len(skills))
        return [s for _, s in top_kw]

    # TF-IDF fallback: relevance-ranked retrieval when no keyword match fires
    # (Phase 32 selective retrieval — prevents returning stale/irrelevant skills)
    ranked = _tfidf_skill_rank(goal, skills, top_k=2)
    # _tfidf_skill_rank stamped match_method/match_score already; reuse them.
    _note("tfidf_fallback",
          [(s, getattr(s, "match_score", 0.0)) for s in ranked], len(skills))
    return ranked


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
        decision:         One of "promote" | "demote" | "rewrite" | "create" | "retire" | "delete".
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
        from file_lock import atomic_write
        atomic_write(path, json.dumps(record, indent=2))
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
    unreadable = 0
    for p in sorted(prov_dir.glob(f"{skill_name}_*.json"), reverse=True):
        try:
            records.append(json.loads(
                p.read_text(encoding="utf-8", errors="surrogateescape")))
        except Exception:
            unreadable += 1
            continue
    if unreadable:
        logger.warning("[skills] load_skill_provenance: %d provenance file(s) "
                       "for %s are unreadable or malformed — skipped (%s)",
                       unreadable, skill_name, prov_dir)
    return records


# ---------------------------------------------------------------------------
# Phase 14: Per-skill success rate tracking (SkillStats)
# ---------------------------------------------------------------------------

def _skill_stats_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "skill-stats.jsonl"


def validate_skill_stats_row(d: dict) -> None:
    """Prove a stats row is one the coercing constructor cannot distort.

    `SkillStats.from_dict` is a CONSTRUCTOR: `float("1.0")` passes,
    `bool("false")` is True. Adversarial r12 (two seats, probed): a
    schema-drifted row rode a routine counter bump and came back with its
    modeled fields silently laundered — and the injection recorder, which
    does not recompute `needs_escalation`, flipped a stored `"false"` to
    JSON `true`. Same rule as `validate_skill_row`: checks the RAW values,
    absence is fine (stats rows are sparse upserts), presence must be the
    type the model would write. Census 2026-08-20: 203/203 live rows pass.

    Presence is `name in d`, NOT `get() is not None` (adversarial r13,
    three seats, probed): an explicitly stored JSON `null` slipped
    through the absence exemption, `bool(None)` laundered it to `false`
    on the next counter bump, and a `null` counter field would make the
    NEXT update raise mid-recorder. No modeled field is nullable in the
    emitted schema, so a present null strands like any other drift.

    Deliberately TYPE-level, not plausibility-level: a row claiming
    `total_uses=-4, successes=100` is faithfully representable and
    faithfully re-emitted, so it is ADMITTED (r13, judged) — semantic
    auditing is an inspector's job; stranding implausible-but-readable
    rows would misfile legitimate legacy data behind a "corruption"
    warning.

    Identity is part of the predicate (adversarial r14, four seats,
    probed): the reader keys this store on a NON-EMPTY STRING skill_id,
    but this validator checked only the modeled statistic fields, so
    `_write_skill_stats` vouched for a `skill_id: null` row the reader
    immediately strands as keyless. The reader itself never reaches this
    check — it routes identity failures to its keyless strand first —
    so reader counters are unchanged; the writer now refuses to mint a
    row no reader will ever return.
    """
    sid = d.get("skill_id")
    if not (isinstance(sid, str) and sid):
        raise TypeError(
            f"skill_id must be a non-empty string, got {sid!r}")
    for name in ("total_uses", "successes", "failures",
                 "injected_runs", "injected_successes"):
        if name in d:
            v = d[name]
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be an int, got {v!r}")
    for name in ("success_rate", "total_cost_usd", "avg_latency_ms",
                 "avg_confidence", "injected_success_rate"):
        if name in d:
            v = d[name]
            if (isinstance(v, bool) or not isinstance(v, (int, float))
                    or not math.isfinite(v)):
                raise TypeError(f"{name} must be a finite number, got {v!r}")
    for name in ("skill_name", "last_used", "last_injected_verdict_at"):
        if name in d:
            v = d[name]
            if not isinstance(v, str):
                raise TypeError(f"{name} must be a string, got {v!r}")
    if "needs_escalation" in d:
        v = d["needs_escalation"]
        if not isinstance(v, bool):
            raise TypeError(f"needs_escalation must be a bool, got {v!r}")


class _StatsRead(tuple):
    """(records, stranded) 2-tuple that ALSO carries `.compacted` — the
    count of older same-id duplicates the keyed read excluded — so the
    writer can announce the actual compaction after its commit without
    breaking every `records, stranded = ...` unpack site (r15)."""

    def __new__(cls, records, stranded, compacted=0):
        self = super().__new__(cls, (records, stranded))
        self.compacted = compacted
        return self

    def __getnewargs__(self):
        # Default tuple reduction reconstructs a subclass from ONE tuple
        # argument, so copy/deepcopy/pickle raised TypeError (adversarial
        # r16, four seats, probed). The method's EXISTENCE is the fix;
        # the third element is belt-and-suspenders — pickle also restores
        # the instance __dict__ after __new__, which carries .compacted
        # (the r16 sweep proved that: a mutant zeroing this element could
        # not fail, and was retargeted at removing the method).
        return (self[0], self[1], self.compacted)


def _read_skill_stats(path: Path) -> Tuple[dict, List[str]]:
    """Announced read of skill-stats.jsonl → ({skill_id: row}, stranded).

    `stranded` carries every line the keyed rebuild cannot represent —
    byte-tainted/unparseable rows AND rows with no skill_id — so a
    write-back can re-emit them VERBATIM instead of deleting them.

    Before this (2026-08-17): the read was a strict whole-file decode
    wrapped in `except Exception: pass`, so one crash-torn byte left the
    map EMPTY and the very next write rebuilt the store from it — every
    skill's stats destroyed by a hot-path counter update. Probed live:
    4 lines -> 1.
    """
    records: dict = {}
    stranded: List[str] = []
    try:
        text = _store_text(path)
    except FileNotFoundError:
        return _StatsRead(records, stranded)
    except OSError:
        # Unreadable store: refuse to rebuild from nothing. Raising here
        # aborts the caller's write, which leaves the file intact — the
        # safe direction when the alternative is a wipe. Pure READERS
        # catch this and degrade to empty (see get_all_skill_stats).
        raise
    tainted = 0
    compacted = 0
    keyless = 0
    # split("\n") on the RAW line, not splitlines() on a stripped copy.
    # This function feeds a REWRITE, and adversarial r10 (Skeptic, probed)
    # found it was the last read->rewrite pair in the arc still using the
    # r8-era idiom. Both halves destroyed data: `splitlines()` broke a
    # valid row at the U+2028 inside a JSON string and `_write_skill_stats`
    # wrote the two fragments back rejoined with LF — the row's bytes
    # CHANGED while the log said "carried verbatim" — and `line.strip()`
    # deleted a U+00A0-only row outright, unstranded and uncounted.
    for line in text.split("\n"):
        if is_frame_blank(line):
            continue
        try:
            d = _loads_clean(line)
            sid = d.get("skill_id", "")
        except Exception:
            tainted += 1
            stranded.append(line)
            continue
        if not (isinstance(sid, str) and sid):
            pass  # falls to the keyless strand below
        else:
            try:
                # Admitted == provable, the r11 skills rule applied to its
                # own stats twin (adversarial r12, two seats, probed): this
                # map feeds SkillStats.from_dict, a COERCING constructor —
                # float("1.0") passes, bool("false") is True — so a
                # schema-drifted row was silently rewritten with laundered
                # values by the next routine counter bump, and the
                # injection recorder (which does not recompute
                # needs_escalation) flipped a stored "false" to true.
                # Census 2026-08-20: 203/203 live rows pass.
                validate_skill_stats_row(d)
            except Exception:
                tainted += 1
                stranded.append(line)
                continue
        if not (isinstance(sid, str) and sid):
            # A non-empty STRING, not merely truthy. Adversarial r11 (QA,
            # probed): JSON `1` and JSON `true` are distinct stored rows,
            # but Python keys them equal (`1 == True`), so the second
            # silently overwrote the first in this map and the rewrite
            # deleted a row with no strand and no warning. A key this
            # store cannot represent faithfully strands like any other
            # unreadable row.
            keyless += 1
            stranded.append(line)
            continue
        if sid in records:
            # Same id twice is representable (last wins, matching this
            # keyed read) but it is still N rows becoming one on the next
            # rewrite — say so instead of compacting in silence
            # (adversarial r12, QA). The drop is right; the silence is not.
            compacted += 1
        records[sid] = d
    if tainted or keyless:
        # A read announces what the READ did — exclusion from this
        # result — never a rewrite it has no knowledge of (adversarial
        # r14, Architect, probed): pure readers like get_all_skill_stats
        # logged "carried through the rewrite" with no rewrite anywhere,
        # and a recorder could log the claim and then fail its write.
        # The carry-through announcement now lives in
        # `_write_skill_stats`, after its commit.
        logger.warning(
            "[skills] skill-stats: %d unparseable/unprovable and %d "
            "keyless row(s) excluded from this read; they remain in the "
            "store verbatim (%s)",
            tainted, keyless, path)
    if compacted:
        # A read announces what the READ did (adversarial r15, four
        # seats, probed — the duplicate twin of r14's strandee fix):
        # the old message claimed a future rewrite ("will be compacted")
        # from a read-only path, an audit line about a deletion that may
        # never happen. The actual compaction is announced by
        # `_write_skill_stats`, after its commit, via the count this
        # result carries.
        logger.warning(
            "[skills] skill-stats: %d older duplicate row(s) for already-"
            "seen id(s) excluded from this keyed read — last row per id "
            "wins; the store still holds them (%s)", compacted, path)
    return _StatsRead(records, stranded, compacted)


def _write_skill_stats(path: Path, records: dict, stranded: List[str],
                       *, compacted: int = 0) -> None:
    """Crash-safe, byte-safe write-back of the keyed store + its strandees."""
    from file_lock import atomic_write
    # `stranded` holds raw lines WITHOUT their framing newline (r10), so
    # the writer owns the framing for both halves.
    # allow_nan=False: non-finite telemetry (a NaN avg_latency, say) would
    # otherwise write the CPython token `NaN`, which the r10 reader strands
    # on the next load — the writer manufacturing its own unreadable row
    # (adversarial r11, Architect). Raising aborts the counter update and
    # leaves the store intact.
    # prove_record_line, not bare allow_nan=False (adversarial r12, three
    # seats, probed): a surrogate-bearing skill_id serialized as a clean
    # \udcff escape, the write "succeeded", and the next read stranded the
    # row — the recorder reported an outcome no reader can ever return.
    # The payload is built before atomic_write runs, so a failure aborts
    # with the store intact.
    # And prove the READER'S full predicate, not just clean-object JSON
    # (adversarial r13, Architect, probed): this writer accepted a row
    # `validate_skill_stats_row` strands, so the store could hold a row
    # the writer vouched for and no reader will ever return. One
    # admission predicate on both ends — same rule as `_prove_line`.
    from jsonl_utils import prove_record_line
    lines = []
    for key, d in records.items():
        # The map key and the row's own identity must agree — a rekeyed
        # entry would silently write a row the next keyed read files
        # under a different id than the caller updated (adversarial r14,
        # Minimalist).
        if d.get("skill_id") != key:
            raise ValueError(
                f"records key {key!r} disagrees with row skill_id "
                f"{d.get('skill_id')!r}")
        validate_skill_stats_row(d)
        lines.append(prove_record_line(d))
    # Strandees ride FIRST (adversarial r14, three seats, probed): r13
    # moved generic-rewrite strandees to the head so a keyed
    # last-row-wins consumer can never let a stranded legacy row shadow
    # the caller's fresh record — and this sibling writer kept the old
    # tail position, where a same-id stranded row overrode the repaired
    # one for any naive parser. Same doctrine, same ordinal.
    # A stranded final row that lost its LF gains one here — required
    # framing once strandees ride first; payload bytes are unchanged and
    # re-splitting yields the identical strandee (r14, accept-and-pin).
    atomic_write(
        path,
        "".join(l + "\n" for l in stranded)
        + "".join(line + "\n" for line in lines),
        errors="surrogateescape",
    )
    # Announce AFTER the commit — the carry-through claim belongs to the
    # writer that performed it, not the reader (adversarial r14,
    # Architect: the read-side warning claimed a rewrite that had not
    # happened, and a failed atomic_write left a false audit line).
    if stranded:
        logger.warning(
            "[skills] skill-stats: %d stranded row(s) carried through "
            "the rewrite verbatim (%s)", len(stranded), path)
    if compacted:
        # The deletion the keyed read predicted, announced by the write
        # that actually performed it (adversarial r15, four seats).
        logger.warning(
            "[skills] skill-stats: %d older duplicate row(s) compacted "
            "by this rewrite — last row per id won (%s)", compacted, path)


def get_all_skill_stats() -> List[SkillStats]:
    """Load all skill stats records from memory/skill-stats.jsonl."""
    path = _skill_stats_path()
    if not path.exists():
        return []
    try:
        records, _stranded = _read_skill_stats(path)
    except OSError as exc:
        # Reads degrade, writes abort. The raise in _read_skill_stats is
        # for the two counter WRITERS (rebuilding from nothing would wipe
        # the store); a pure read has nothing to abort, and every other
        # reader in this module degrades to empty.
        logger.warning("[skills] get_all_skill_stats: store unreadable "
                       "(%s: %s) — reporting no stats, which is NOT the "
                       "same as no stats existing (%s)",
                       type(exc).__name__, exc, path)
        return []
    stats_map: dict = {}
    drifted = 0
    for sid, d in records.items():
        try:
            stats_map[sid] = SkillStats.from_dict(d)
        except Exception:
            drifted += 1
    if drifted:
        logger.warning("[skills] get_all_skill_stats: %d row(s) are JSON but "
                       "not loadable as SkillStats — skipped (%s)",
                       drifted, path)
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
    # A non-string id would mint the exact row the reader strands as
    # keyless (adversarial r12: `record_skill_outcome(1, ...)` wrote
    # {"skill_id": 1} and reported success while every future read carried
    # it as an unreadable strandee). Refuse at the door, store untouched.
    if not (isinstance(skill_id, str) and skill_id):
        raise TypeError(f"skill_id must be a non-empty string, "
                        f"got {skill_id!r}")
    try:
        skill_id.encode("utf-8")   # a lone surrogate can never be re-read
    except UnicodeEncodeError:
        raise TypeError(f"skill_id is not encodable text: {skill_id!r}") \
            from None
    # Evidence must arrive as evidence (adversarial r13, Architect,
    # probed): `success="false"` is truthy, so a stringly-typed caller
    # recorded a FAILURE as a success — permanently wrong evidence that
    # type-checks clean forever after. And non-finite telemetry
    # (cost_usd=NaN) sailed to the emission door, whose refusal the
    # never-raise write wrapper then swallowed — the outcome silently
    # discarded. Refuse both at the door, before any lock or mutation.
    if type(success) is not bool:
        raise TypeError(f"success must be a bool, got {success!r}")
    for _tname, _tv in (("cost_usd", cost_usd), ("latency_ms", latency_ms),
                        ("confidence", confidence)):
        if isinstance(_tv, bool) or not isinstance(_tv, (int, float)) \
                or not math.isfinite(_tv):
            raise TypeError(
                f"{_tname} must be a finite number, got {_tv!r}")
    from file_lock import locked_write

    path = _skill_stats_path()

    # require=True (adversarial r15, three seats, probed): this is a
    # read-modify-write over the whole keyed store, and the documented
    # fail-open lock mode would degrade it into the exact lost-update
    # race r14 closed for JSONLBackend.transform — two recorders both
    # read N, both write N+1, one outcome silently gone. A transaction
    # that cannot lock must refuse to run.
    #
    # The try covers the WHOLE transaction (adversarial r16, two seats,
    # probed): r15's error log wrapped only the write, so a lock or read
    # failure raised with no recorder-level announcement — and two of
    # the three production catch sites log at DEBUG, which made a lock
    # outage indistinguishable from missing telemetry.
    try:
      with locked_write(path, require=True):
        # Announced read; `stranded` rides the rewrite verbatim so a torn
        # or keyless row is never deleted by a counter update.
        _read = _read_skill_stats(path)
        all_records, stranded = _read
        compacted = _read.compacted

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

        # Update the map and write back (full rewrite for consistency).
        # Merge OVER the stored row, not replace it: to_dict() emits only
        # the dataclass schema, so any field this updater does not own — an
        # operator's hand-added note, a forward-version field — was deleted
        # by every routine counter bump (adversarial r11, Minimalist,
        # probed: `operator_note` gone with no warning). The updater wins
        # on the fields it writes; everything else rides through.
        all_records[skill_id] = {**all_records.get(skill_id, {}),
                                 **stats.to_dict()}
        _write_skill_stats(path, all_records, stranded,
                           compacted=compacted)
    except Exception as e:
        # Name what was lost and where — then RAISE (adversarial r15,
        # four seats, probed; widened to the whole transaction r16): the
        # r13 version warned and returned a normal None, so a disk-full
        # lost the outcome while the caller proceeded as if evidence
        # existed. An error result must not be a valid value.
        logger.error(
            "[skills] record_skill_outcome: outcome for %r NOT "
            "persisted (%s): %s", skill_id, path, e)
        raise


def record_skill_injection_outcome(skill_id: str, goal_achieved: bool) -> None:
    """Record a run-verdict outcome for a skill that was actually injected.

    The honest counter pair to record_skill_outcome: called at closure-verdict
    time (memory_ledger.stamp_outcome_verdict) for each skill in the run's
    skills_manifest.jsonl — skills that genuinely entered a prompt — with the
    run's FULL-trust goal verdict as the label. Contrast: the legacy counters
    credit keyword-matched bystanders with step completions (~1.0 base rate),
    which is how the store reached 99.4% positive and starved the router.
    """
    # A non-string id would mint the exact row the reader strands as
    # keyless (adversarial r12: `record_skill_outcome(1, ...)` wrote
    # {"skill_id": 1} and reported success while every future read carried
    # it as an unreadable strandee). Refuse at the door, store untouched.
    if not (isinstance(skill_id, str) and skill_id):
        raise TypeError(f"skill_id must be a non-empty string, "
                        f"got {skill_id!r}")
    try:
        skill_id.encode("utf-8")   # a lone surrogate can never be re-read
    except UnicodeEncodeError:
        raise TypeError(f"skill_id is not encodable text: {skill_id!r}") \
            from None
    # Same door as record_skill_outcome (adversarial r13): a truthy
    # non-bool verdict must not count as goal_achieved.
    if type(goal_achieved) is not bool:
        raise TypeError(
            f"goal_achieved must be a bool, got {goal_achieved!r}")
    from file_lock import locked_write

    path = _skill_stats_path()

    # require=True (adversarial r15, three seats, probed); try covers the
    # whole transaction (r16) — same contract as record_skill_outcome.
    try:
        with locked_write(path, require=True):
            # Announced read; `stranded` rides the rewrite verbatim so a
            # torn or keyless row is never deleted by a counter update.
            _read = _read_skill_stats(path)
            all_records, stranded = _read
            compacted = _read.compacted
            _apply_injection_verdict(all_records, skill_id, goal_achieved)
            _write_skill_stats(path, all_records, stranded,
                               compacted=compacted)
    except Exception as e:
        # Same contract as record_skill_outcome (r15): name it, raise.
        logger.error(
            "[skills] record_skill_injection_outcome: verdict for %r "
            "NOT persisted (%s): %s", skill_id, path, e)
        raise


def _require_recordable_id(skill_id) -> None:
    """The recorders' shared id door (r12/r16): non-string or
    non-encodable ids mint rows every future read strands as keyless."""
    if not (isinstance(skill_id, str) and skill_id):
        raise TypeError(f"skill_id must be a non-empty string, "
                        f"got {skill_id!r}")
    try:
        skill_id.encode("utf-8")
    except UnicodeEncodeError:
        raise TypeError(f"skill_id is not encodable text: {skill_id!r}") \
            from None


def _apply_injection_verdict(all_records: dict, skill_id: str,
                             goal_achieved: bool) -> None:
    """Apply one injection verdict to the in-memory keyed records.

    Runs INSIDE a caller-held required lock — shared by the single
    recorder and the batch recorder so the two cannot drift."""
    if skill_id in all_records:
        stats = SkillStats.from_dict(all_records[skill_id])
    else:
        skill_name = skill_id
        try:
            for sk in load_skills():
                if sk.id == skill_id:
                    skill_name = sk.name
                    break
        except Exception:
            pass
        stats = SkillStats(skill_id=skill_id, skill_name=skill_name)

    stats.injected_runs += 1
    if goal_achieved:
        stats.injected_successes += 1
    stats.injected_success_rate = (
        stats.injected_successes / max(stats.injected_runs, 1))
    stats.last_injected_verdict_at = datetime.now(timezone.utc).isoformat()

    # Same merge-over-stored rule as record_skill_outcome (r11).
    all_records[skill_id] = {**all_records.get(skill_id, {}),
                             **stats.to_dict()}


def record_skill_injection_outcomes(skill_ids, goal_achieved: bool) -> None:
    """Batch twin of record_skill_injection_outcome: ONE transaction.

    Adversarial r16 (four seats, probed): memory_ledger applied a run's
    manifest with a per-id loop, so once r15's recorders started
    raising, a mid-list failure became a reachable partial batch — id A
    committed, id B failed, the idempotence marker never written, and
    the caller's retry credited A twice. Permanently skewed training
    evidence. Here every id commits in one write or none do; the
    caller's marker can then mean what it says.

    (Residual, recorded: a crash BETWEEN this commit and the caller's
    marker write re-applies the whole batch on retry — the same
    ack-vs-apply window as the BACKLOG'd interrupt F9 design item, and
    the same design work resolves both.)"""
    if isinstance(skill_ids, (str, bytes)):
        # A lone id passed bare would be iterated character by
        # character — five verdicts nobody asked for (adversarial r17,
        # QA). Degenerate input fails loudly at the door.
        raise TypeError(
            f"skill_ids must be an iterable of ids, not a bare string: "
            f"{skill_ids!r}")
    ids: list = []
    seen_ids: set = set()
    dup = 0
    for sid in skill_ids:
        _require_recordable_id(sid)
        # One verdict per skill per batch (adversarial r17, two seats,
        # probed): a duplicated id would credit one injected run twice.
        # First-seen order is kept; the collapse is announced.
        if sid in seen_ids:
            dup += 1
            continue
        seen_ids.add(sid)
        ids.append(sid)
    if dup:
        logger.warning(
            "[skills] record_skill_injection_outcomes: %d duplicate "
            "id(s) collapsed — one verdict per skill per batch", dup)
    if type(goal_achieved) is not bool:
        raise TypeError(
            f"goal_achieved must be a bool, got {goal_achieved!r}")
    if not ids:
        return
    from file_lock import locked_write

    path = _skill_stats_path()
    try:
        with locked_write(path, require=True):
            _read = _read_skill_stats(path)
            all_records, stranded = _read
            compacted = _read.compacted
            for sid in ids:
                _apply_injection_verdict(all_records, sid, goal_achieved)
            _write_skill_stats(path, all_records, stranded,
                               compacted=compacted)
    except Exception as e:
        logger.error(
            "[skills] record_skill_injection_outcomes: NONE of the %d "
            "verdict(s) persisted (%s): %s", len(ids), path, e)
        raise


def get_skills_needing_escalation() -> List[SkillStats]:
    """Return skill stats where success_rate < ESCALATION_THRESHOLD."""
    return [s for s in get_all_skill_stats() if s.success_rate < ESCALATION_THRESHOLD]


# ---------------------------------------------------------------------------
# Phase 32: Utility scoring, failure attribution, auto-promotion, rewrite gating
# ---------------------------------------------------------------------------

def update_skill_utility(
    skill_id: str,
    success: bool,
    failure_reason: str = "",
    *,
    step_text: str = "",
) -> None:
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
        step_text: The step/goal text (optional). Used to detect INPUT_MISMATCH when
            a skill trained on web/URL content is invoked with plain-text input.

    Does NOT write skill-stats: both live call paths (loop_post_step done,
    loop_blocked via attribute_failure_to_skills) call record_skill_outcome
    themselves with cost/latency — the internal call this function used to
    make double-counted every outcome (found 2026-07-29).
    """
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

    # INPUT_MISMATCH: log if circuit just opened and step_text looks like a domain mismatch
    # (e.g., skill trained on URL/web content used for plain text, or vice versa)
    if (
        target.circuit_state == "open"
        and old_circuit_state != "open"
        and step_text
        and not success
    ):
        try:
            from captains_log import classify_input_type, log_event, INPUT_MISMATCH
            _input_type = classify_input_type(step_text)
            _trigger_text = " ".join(target.trigger_patterns).lower()
            _url_skill = any(kw in _trigger_text for kw in ("url", "web", "http", "jina", "fetch", "scrape"))
            _url_input = _input_type == "url"
            if _url_skill != _url_input:
                log_event(
                    event_type=INPUT_MISMATCH,
                    subject=target.name,
                    summary=(
                        f"Skill '{target.name}' expects {'url' if _url_skill else 'non-url'} input "
                        f"but received {_input_type!r}. Circuit opened — failures may reflect domain mismatch."
                    ),
                    context={
                        "skill_id": skill_id,
                        "input_type": _input_type,
                        "skill_url_domain": _url_skill,
                        "consecutive_failures": target.consecutive_failures,
                    },
                    note="Inspector: treat this as INPUT_MISMATCH, not skill degradation.",
                    related_ids=[f"skill:{skill_id}"],
                )
        except Exception:
            pass

    # Recompute content hash after mutation
    target.content_hash = compute_skill_hash(target)

    _save_skills(skills, updated_ids={target.id})


def attribute_failure_to_skills(
    step_text: str,
    failure_reason: str,
    goal: str = "",
    only_ids=None,
) -> List[str]:
    """Find matching skills for a step that failed and record failure against them.

    ``only_ids`` restricts attribution to the run's injected manifest
    (R3-2) — None keeps the legacy full-pool match for callers with no
    manifest.

    Returns list of skill_ids that were attributed.
    """
    matched = find_matching_skills(step_text + " " + goal, use_router=False,
                                   only_ids=only_ids)
    attributed = []
    for skill in matched:
        try:
            update_skill_utility(
                skill.id,
                success=False,
                failure_reason=failure_reason,
                step_text=step_text,
            )
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
            no_tools=True,
            purpose="skill promotion validation",
        )
        parsed = extract_json(content_or_empty(resp), dict, log_tag="skills.validate")
        if isinstance(parsed, dict):
            return {
                "valid": bool(parsed.get("valid", False)),
                "reason": str(parsed.get("reason", "")),
                "repair_hint": str(parsed.get("repair_hint", "")),
                "judged": True,
            }
    except Exception as exc:
        logging.getLogger("maro.skills.validate").debug("validate_skill_for_promotion failed: %s", exc)
    # Fail-open: if we can't validate, allow promotion (don't block the cycle)
    # — graceful degradation to the numeric-gates-only behavior this call
    # had before the adapter was wired. judged=False so the promotion event
    # can say the pass was never a judgment (§13e slice-2 pattern).
    return {"valid": True, "reason": "validation unavailable (fail-open)",
            "repair_hint": "", "judged": False}


def maybe_auto_promote_skills(adapter: Any = None, max_repair_attempts: int = 3,
                              *, limit: int = 10) -> List[str]:
    """Promote provisional skills that meet quality threshold to established.

    If `adapter` is provided, applies a Voyager-style validation harness before
    promoting each skill. Skills that fail validation are sent through up to
    `max_repair_attempts` rewrite cycles (via `evolver.rewrite_skill`).
    Skills that still fail after max attempts are kept provisional.

    Criteria for promotion:
      - tier == "provisional"
      - utility_score >= AUTO_PROMOTE_MIN_RATE (EMA-based, smoothed)
      - observed uses >= AUTO_PROMOTE_MIN_USES. Uses come from SkillStats
        (`total_uses`), NOT Skill.use_count: that field's only writer was
        removed 2026-07-29 as dead code, which left this gate reading a
        permanently-zero counter — no skill promoted for 8 weeks while the
        store grew to 376 provisionals, 134 of them use-eligible on real
        stats (found 2026-08-06). Skill.use_count still participates as a
        max() so old stores with real counts keep working.
      - (if adapter) passes LLM validation gate or repairs within max_repair_attempts

    At most `limit` CANDIDATES enter the validation harness per sweep
    (same cap shape as knowledge-node promotion): the first sweep after
    the dead-gate fix would otherwise push the whole 134-skill backlog
    through the LLM validation harness in one maintenance pass. The cap
    counts candidates, not successes — a pool of never-passing
    provisionals must not turn the cap into unbounded validate+repair
    spend every sweep.

    Where SkillStats carries verdict-grounded evidence (injected_runs >
    0), it can veto: the legacy counters credit keyword-matched
    bystanders (~1.0 base rate, see skill_types.SkillStats), so a skill
    whose actually-injected runs fail is held even if the inflated
    counters look good.

    Returns list of promoted skill_ids.
    """
    skills = load_skills()
    promoted = []

    _stats_by_id: Dict[str, Any] = {}
    try:
        for _st in get_all_skill_stats():
            _stats_by_id[_st.skill_id] = _st
    except Exception:
        pass

    examined = 0
    for skill in skills:
        if examined >= limit:
            break
        if skill.tier != "provisional":
            continue
        _st = _stats_by_id.get(skill.id)
        _uses = max(skill.use_count, int(getattr(_st, "total_uses", 0) or 0))
        if _uses < AUTO_PROMOTE_MIN_USES:
            continue
        if skill.utility_score < AUTO_PROMOTE_MIN_RATE:
            continue
        _inj_runs = int(getattr(_st, "injected_runs", 0) or 0)
        if (_inj_runs > 0
                and float(getattr(_st, "injected_success_rate", 0.0) or 0.0)
                < AUTO_PROMOTE_MIN_RATE):
            logging.getLogger("maro.skills.promote").info(
                "skill %s held: injected evidence (%d runs, rate %.2f) "
                "contradicts legacy counters",
                skill.id, _inj_runs,
                float(getattr(_st, "injected_success_rate", 0.0) or 0.0))
            continue
        _evidence = "injected-confirmed" if _inj_runs > 0 else "legacy-only"
        examined += 1

        # Voyager/Agent0 steal: validation harness with repair loop.
        # rewrite_skill (in_place default) persists repaired content to
        # disk itself; this sweep only decides tier, applied on a fresh
        # reload at the end so a repair is never clobbered by a stale
        # in-memory row.
        _validation = "skipped"  # no adapter → validation never ran
        if adapter is not None:
            _logger = logging.getLogger("maro.skills.promote")
            _candidate = skill
            _valid = False
            for _attempt in range(max_repair_attempts):
                _result = validate_skill_for_promotion(_candidate, adapter)
                if _result["valid"]:
                    _valid = True
                    # "passed" = the LLM actually judged; "unjudged" = the
                    # fail-open default let it through (validation errored).
                    _validation = ("passed" if _result.get("judged", True)
                                   else "unjudged")
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

        promoted.append(skill.id)
        logging.getLogger("maro.skills").info("[skills] auto-promoted skill %s (%s)", skill.id, skill.name)
        try:
            from captains_log import log_event, SKILL_PROMOTED
            log_event(
                event_type=SKILL_PROMOTED,
                subject=skill.name,
                summary=f"Promoted provisional -> established. Utility: {skill.utility_score:.2f} over {_uses} uses.",
                context={"skill_id": skill.id, "utility": round(skill.utility_score, 3), "use_count": _uses,
                         "validation": _validation, "evidence": _evidence},
                related_ids=[f"skill:{skill.id}"],
            )
        except Exception:
            pass

    if promoted:
        # Tier changes land on a fresh reload: repairs above already
        # saved the pool from their own load, so writing this sweep's
        # pre-repair list back would revert them (and drop any
        # concurrent mutation since our load). Reload, stamp tiers by
        # id, save — the promoted object on disk is the repaired one.
        # The lock spans reload→save (R3-8, adversarial review
        # 2026-08-06): _save_skills' own lock guards only the write, so
        # a mutation landing inside the reload→rewrite window would be
        # dropped by the full rewrite. locked_write is reentrant, so
        # _save_skills' inner acquisition is a no-op here.
        from file_lock import locked_write as _locked_write
        _promoted_set = set(promoted)
        with _locked_write(_skills_path()):
            fresh = load_skills()
            for s in fresh:
                if s.id in _promoted_set:
                    s.tier = "established"
                    s.content_hash = compute_skill_hash(s)
            _save_skills(fresh,
                         updated_ids=_promoted_set & {s.id for s in fresh})
        # Hermes steal: auto-export newly promoted skills as SKILL.md curated files
        for s in fresh:
            if s.id in _promoted_set:
                try:
                    from skill_loader import export_skill_as_markdown
                    export_skill_as_markdown(s)
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

    # Skill.use_count is legacy-frozen (writer removed 2026-07-29) — gate on
    # the live SkillStats counters, same shape as maybe_auto_promote_skills.
    _stats_uses: Dict[str, int] = {}
    try:
        for _st in get_all_skill_stats():
            _stats_uses[_st.skill_id] = int(getattr(_st, "total_uses", 0) or 0)
    except Exception:
        pass

    for skill in skills:
        if skill.tier != "established":
            continue
        if max(skill.use_count, _stats_uses.get(skill.id, 0)) < REWRITE_MIN_USES:
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
        _save_skills(skills, updated_ids=set(demoted))

    return demoted


def skills_needing_rewrite() -> List[Skill]:
    """Return skills eligible for LLM rewriting.

    A skill qualifies only when its circuit breaker is OPEN — meaning it has
    sustained consecutive failures (not just a blip) OR failed during recovery.
    This prevents rewrites from transient errors (network blips, one bad run).

    Criteria (all must hold):
      - circuit_state == "open"
      - uses >= REWRITE_MIN_USES (enough data to trust the signal; live
        SkillStats.total_uses, max'd with the legacy-frozen Skill.use_count)
      - utility_score < REWRITE_TRIGGER_RATE (EMA confirms persistent underperformance)
        OR consecutive_failures >= CIRCUIT_OPEN_THRESHOLD (structural streak, EMA may lag)
    """
    skills = load_skills()
    _stats_uses: Dict[str, int] = {}
    try:
        for _st in get_all_skill_stats():
            _stats_uses[_st.skill_id] = int(getattr(_st, "total_uses", 0) or 0)
    except Exception:
        pass
    return [
        s for s in skills
        if (
            s.circuit_state == "open"
            and max(s.use_count, _stats_uses.get(s.id, 0)) >= REWRITE_MIN_USES
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
    """Return skills in the 'frontier zone' (Agent0 steal: target 40–70% success).

    The frontier zone is the sweet spot: skills that are consistently challenging
    but not broken — analogous to Agent0's R_unc reward targeting tasks near 50%
    solve-rate. The evolver should prioritise rewriting these skills over either
    very low performers (already in circuit-open state) or top performers (working well).

    Evidence basis is the run-verdict injected counters
    (SkillStats.injected_runs / injected_success_rate): skills that actually
    entered a prompt, labeled by FULL-trust goal verdicts. The original gate
    read Skill.use_count, whose only writer never had a caller — the gate
    returned [] on live data forever, which silently starved the entire A/B
    variant subsystem downstream (0 variants ever created in 314 skills,
    found 2026-07-29). Legacy use_count is deliberately NOT a fallback:
    no honest evidence, no rewrite candidacy.

    Args:
        skills:    Skill list (loaded from disk if None).
        min_uses:  Minimum injected_runs (verdicted injections) for reliable data.

    Returns:
        Skills with FRONTIER_LOW <= injected_success_rate <= FRONTIER_HIGH,
        sorted ascending by injected_success_rate (hardest first).
    """
    if skills is None:
        skills = load_skills()
    stats_by_id = {st.skill_id: st for st in get_all_skill_stats()}
    frontier = []
    for s in skills:
        st = stats_by_id.get(s.id)
        if st is None or st.injected_runs < min_uses:
            continue
        if s.circuit_state == "open":  # open-circuit handled by skills_needing_rewrite
            continue
        if FRONTIER_LOW <= st.injected_success_rate <= FRONTIER_HIGH:
            frontier.append(s)
    return sorted(frontier, key=lambda s: stats_by_id[s.id].injected_success_rate)


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
    if rewritten.id == original.id:
        # A challenger must be a distinct row: marking a skill as its own
        # variant makes the A/B armless, and retiring the "challenger"
        # would archive-and-delete the parent itself. (This happened —
        # rewrite_skill used to mutate in place and return the parent;
        # every pre-2026-08-06 variant was self-referential.)
        raise ValueError(
            f"challenger id equals parent id ({original.id}); "
            "pass a distinct rewritten skill (rewrite_skill in_place=False)"
        )
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
        _save_skills(skills, updated_ids={skill_id})


def retire_losing_variants(*, dry_run: bool = False, min_uses: int = MIN_VARIANT_USES) -> dict:
    """Evaluate all active A/B pairs and retire losers.

    For each (parent, challengers) group:
    - Compute win-rate for parent and each challenger.
    - Only act if BOTH sides have ≥ min_uses total trials.
    - Winner: highest win-rate among all variants + parent.
    - Loser(s): all others.
    - If challenger wins: replace parent's core content with challenger's; retire challenger.
    - If parent wins: retire challenger(s).
    Retired variants are archived to skills_archive.jsonl, never deleted.

    Returns:
        dict with keys: promoted (list of IDs), retired (list of IDs)
    """
    skills = load_skills()
    skill_by_id = {s.id: s for s in skills}

    # Heal self-referential variants (pre-2026-08-06 mint bug: rewrite_skill
    # mutated the parent in place, so create_skill_variant stamped every
    # skill as its own challenger). A self-variant can never be A/B'd, and
    # "retiring" one would archive-and-delete the parent itself — clear the
    # corrupt marker instead of ever acting on it.
    _healed = [s for s in skills if s.variant_of == s.id]
    for s in _healed:
        s.variant_of = None
        logger.warning(
            "skills.ab_variant: healed self-referential variant_of on %s (%s)",
            s.id, s.name,
        )
    if _healed and not dry_run:
        _save_skills(skills, updated_ids={s.id for s in _healed})

    # Skill.use_count is legacy-frozen (writer removed 2026-07-29) — parent
    # trial counts come from live SkillStats, max'd for old stores.
    _stats_uses: Dict[str, int] = {}
    try:
        for _st in get_all_skill_stats():
            _stats_uses[_st.skill_id] = int(getattr(_st, "total_uses", 0) or 0)
    except Exception:
        pass

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
        parent_trials = max(parent.use_count, _stats_uses.get(parent.id, 0))

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
        # Retire losers: archive first (retention decree — retirement moves
        # a skill out of the live pool, it never destroys the record), then
        # rewrite the pool without them.
        retired_set = set(retired)
        losers = [s for s in skills if s.id in retired_set]
        _archive_skills(losers, reason="ab_variant_retired")
        skills = [s for s in skills if s.id not in retired_set]
        # updated_ids: a winning challenger's content was copied into
        # its PARENT above — those parents are writes this save must
        # name (r17). A parent that was itself retired this pass (a
        # variant chain) is a drop, not a write.
        _save_skills(skills, dropped_ids=retired_set,
                     updated_ids=set(promoted) - retired_set)
        for s in losers:
            try:
                write_skill_provenance(
                    s.name, "retire",
                    reason=f"A/B variant lost against parent {s.variant_of}",
                    extra={"skill_id": s.id, "variant_of": s.variant_of,
                           "archived_to": "skills_archive.jsonl"},
                )
            except Exception:
                pass
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


def _save_skills(skills: List[Skill], *,
                 dropped_ids: "frozenset[str] | set[str]" = frozenset(),
                 updated_ids: "frozenset[str] | set[str]",
                 ) -> None:
    """Overwrite skills.jsonl with the current list, carrying strandees.

    A deliberate drop must be NAMED (adversarial r16 — three seats,
    HIGH, probed): every caller hands us a list built from an UNLOCKED
    load_skills() snapshot, and the old contract read "proven row absent
    from the list" as "deliberately deleted" — so a skill saved by a
    concurrent process between the caller's read and this rewrite was
    silently destroyed, with no archive copy. Now absence means CARRY
    (the fresh row rides through verbatim, holding its ordinal); only an
    id in `dropped_ids` is removed, and the destructive callers (island
    cull, A/B retirement, evolver rollback) name their drops.

    A deliberate WRITE must be named too (adversarial r17 — three
    seats, HIGH, probed): r16 protected a concurrently ADDED id, but a
    row present in the caller's stale snapshot still replaced the live
    row wholesale — a concurrent `save_skill(B)` was reverted by any
    unrelated caller that loaded before it and saved after it, with no
    archive, announcement, or conflict signal. `updated_ids` is the
    write twin of `dropped_ids`: only a named id takes the caller's
    version; every other live row — including ids the caller's list
    holds a stale copy of — is carried verbatim in place. The caller's
    unnamed copies are never written, so "I loaded it" no longer
    implies "I own it".

    Naming is also not creation (adversarial r18 — QA, HIGH, probed):
    a named id ABSENT from the live store is a lost race with a
    deliberate drop (cull, retirement, rollback), and the r17 tail
    append silently resurrected the retired row. No call site creates
    rows through this function — creation is save_skill's job — so a
    named-but-absent write is now dropped and ANNOUNCED, and the
    deletion stands. A caller that mutated a row but forgot to name it
    gets a divergence warning instead of silence (r18, Failure
    Operator).

    (Residual, recorded: an id in dropped_ids OR updated_ids whose row
    was revised after the caller's snapshot still loses that revision —
    naming an id claims it. Upgrade edge: a transform-style primitive
    that re-derives the mutation inside this lock.)

    The list a caller hands us came from load_skills(), which cannot
    represent a row it could not parse — so a naive full rewrite from that
    list DELETES every torn line in the store. Found by adversarial review
    of the 2026-08-17 byte-safety chunk, and it was that chunk's own
    regression: before it, load_skills() RAISED on a torn byte and the
    whole load->save cycle aborted (loud, non-destructive); after it, the
    read degraded to "one row short" and this rewrite made the loss
    durable. Eight call sites feed this function, including
    update_skill_utility() — which fires on every skill match.

    So: re-read the store under the lock and carry forward, verbatim,
    every line the in-memory list cannot account for.
    """
    path = _skills_path()
    # Contradictory intent is a caller bug — refuse before the lock,
    # store untouched (r17): an id both dropped and updated, an id
    # "updated" that the caller's own list does not hold, or an id
    # dropped while still in the list has no honest interpretation.
    updated_ids = set(updated_ids)
    dropped_ids = set(dropped_ids)
    list_ids = {s.id for s in skills}
    if updated_ids & dropped_ids:
        raise ValueError(
            f"_save_skills: id(s) named both updated and dropped: "
            f"{sorted(updated_ids & dropped_ids)}")
    if updated_ids - list_ids:
        raise ValueError(
            f"_save_skills: updated id(s) absent from the caller's list: "
            f"{sorted(updated_ids - list_ids)}")
    if dropped_ids & list_ids:
        raise ValueError(
            f"_save_skills: dropped id(s) still present in the caller's "
            f"list: {sorted(dropped_ids & list_ids)}")
    try:
        from file_lock import locked_write, atomic_write
        path.parent.mkdir(parents=True, exist_ok=True)
        # require=True (r16): this is THE destructive rewrite of the
        # skill pool; fail-open would let two writers race it.
        with locked_write(path, require=True):
            by_id = {s.id: s for s in skills}
            out: "List[Optional[str]]" = []
            slot: dict = {}
            dropped_seen: set = set()
            dropped_rows = 0
            divergent: set = set()
            ghost_ids: list = []
            strand_ids: set = set()
            unprovable_unnamed = 0
            compacted = tainted = unprovable = 0
            if path.exists():
                # split("\n"), not splitlines(): the latter also breaks on
                # U+2028/U+2029, which are legal INSIDE a JSON string, and
                # this loop feeds a REWRITE — one such row would be carried
                # through as two invalid fragments.
                for line in _store_text(path).split("\n"):
                    if is_frame_blank(line):
                        continue
                    # Two different unrepresentables, counted apart. The
                    # RAW line, both to parse and to carry: a stripped copy
                    # can parse when the row does not, and "verbatim" that
                    # strips is not verbatim (adversarial r9).
                    try:
                        d = _loads_clean(line)
                    except Exception:
                        tainted += 1
                        out.append(line)
                        continue
                    try:
                        # r9 stopped here and called every parseable row
                        # "represented by the list". Adversarial r10
                        # (Minimalist + Failure Operator, both probed) showed
                        # what that costs: a row that is valid JSON but not a
                        # loadable Skill — `"utility_score": "nope"` — is
                        # skipped by load_skills with a log line, so it is in
                        # NO caller's list, and the next unrelated outcome
                        # update deleted it. Representable means provable,
                        # the rule validate_skill_row was written for.
                        row = validate_skill_row(d)
                    except Exception:
                        unprovable += 1
                        # The row failed the PROOF, but its declared id
                        # may still parse — recovered so a NAMED write
                        # against it can be announced honestly as
                        # "present but unprovable", not "concurrently
                        # removed" (adversarial r19, two seats, probed).
                        _sid = d.get("id") if isinstance(d, dict) else None
                        if isinstance(_sid, str) and _sid:
                            strand_ids.add(_sid)
                        else:
                            # No recoverable id — this row could be
                            # ANY named id; the ghost message must
                            # hedge on it (adversarial r20, two seats,
                            # probed: an id-less unprovable row let
                            # the ghost message assert absence the
                            # scan had not proved).
                            unprovable_unnamed += 1
                        out.append(line)
                        continue
                    if row.id in dropped_ids:
                        # A NAMED deliberate drop (island cull, A/B
                        # retirement, rollback) — that IS a decision.
                        # Physical rows counted apart from ids
                        # (adversarial r18, Architect): a legacy store
                        # holding duplicate rows for a dropped id used
                        # to announce fewer removals than it performed.
                        dropped_seen.add(row.id)
                        dropped_rows += 1
                    elif row.id in updated_ids:
                        # A NAMED write. Hold this row's ORDINAL.
                        # Appending survivors after the rewritten skills
                        # reorders the store, and this store is read
                        # last-row-wins by id — so a carried row could be
                        # promoted over a live skill purely by being moved
                        # (adversarial r10, Minimalist). Last occurrence
                        # wins, which is what the reader would have picked
                        # anyway; an earlier duplicate row this named
                        # write supersedes is compacted — counted and
                        # announced after the commit, like the stats twin
                        # (adversarial r17, Minimalist).
                        if row.id in slot:
                            compacted += 1
                        slot[row.id] = len(out)
                        out.append(None)
                    else:
                        # Everything else — a row absent from the
                        # caller's list (concurrent save, a row
                        # load_skills skipped — r16) AND a row the
                        # caller's list holds but did not NAME as
                        # updated (r17: the live row is at least as
                        # fresh as the caller's stale copy) — is
                        # carried verbatim, holding its ordinal.
                        # A caller that MUTATED an unnamed copy gets a
                        # warning, not silence (adversarial r18, Failure
                        # Operator): forgetting to name an id in
                        # updated_ids discards the edit with no signal.
                        # But divergence has TWO causes this function
                        # cannot tell apart (adversarial r19, four
                        # seats, probed): a forgotten edit, and a
                        # CONCURRENT named write that legitimately moved
                        # the live row after the caller's snapshot — the
                        # exact case r16/r17 carry silently by design.
                        # So the announcement states the fact and names
                        # both causes; it must not assert "the caller's
                        # edit" — under load, staleness is the common
                        # cause and a lying warning trains operators to
                        # ignore the honest one.
                        # content_hash is excluded: it is derived, and a
                        # not-yet-backfilled empty hash is not an edit.
                        cand = by_id.get(row.id)
                        if cand is not None and row.id not in divergent:
                            _cd = skill_to_dict(cand)
                            _ld = skill_to_dict(row)
                            _cd.pop("content_hash", None)
                            _ld.pop("content_hash", None)
                            if _cd != _ld:
                                divergent.add(row.id)
                        out.append(line)
            # A writer must not emit a row it would itself refuse. Every
            # row this function writes is read back by the NEXT call to it,
            # which since r10 will only let a PROVABLE row take part in a
            # removal decision — and `content_hash` is one of the fields
            # `validate_skill_row` requires, because a destructive caller
            # acts on it. A Skill that never went through `save_skill` (a
            # cull pool built in memory, say) carries an empty hash, so
            # without this the store would fill with rows that can never be
            # removed again: probed by the suite, where a deliberate island
            # cull left every culled skill live. `save_skill` has always
            # recomputed on write; this fills the gap for the bulk writer
            # without erasing a hash the caller deliberately carries.
            # Scoped to NAMED writes (adversarial r18, Minimalist): only
            # named rows are serialized since r17 — everything else is
            # carried verbatim from disk — so backfilling an unnamed
            # in-memory copy mutated an object whose hash the store
            # would never hold.
            for s in skills:
                if s.id in updated_ids and not s.content_hash:
                    s.content_hash = compute_skill_hash(s)
            for sid, i in slot.items():
                out[i] = _prove_line(by_id[sid])
            # A NAMED write the live loop never placed is NOT appended
            # (adversarial r18, QA, probed): "an updated id whose live
            # row vanished" is a lost race with a DELIBERATE drop — an
            # island cull, an A/B retirement, a rollback — and the r17
            # tail append silently resurrected the retired row, with
            # none of the retirement's reasoning and no signal at all.
            # No call site creates rows through this function
            # (creation is save_skill's job; the census checked all of
            # them), so the deletion stands and is announced below.
            # The same branch covers a named id whose SOLE live row
            # went unparseable mid-flight: the raw row is stranded
            # above and announced; the operator repairs, the caller
            # retries.
            stranded_named = sorted(sid for sid in updated_ids
                                    if sid not in slot
                                    and sid in strand_ids)
            # The DROP twin (adversarial r20, five seats, probed): the
            # dropped_ids branch is only reachable for PROVABLE rows,
            # so a named drop whose live row fails the proof silently
            # no-oped — the cull returned clean, the row survived, and
            # the only signal was the id-less carry line. Deletions by
            # name earn the same three truths as writes by name.
            stranded_dropped = sorted(sid for sid in dropped_ids
                                      if sid in strand_ids
                                      and sid not in dropped_seen)
            partially_dropped = sorted(sid for sid in dropped_ids
                                       if sid in strand_ids
                                       and sid in dropped_seen)
            ghost_ids = sorted(sid for sid in updated_ids
                               if sid not in slot
                               and sid not in strand_ids)
            atomic_write(
                path,
                "\n".join([l for l in out if l is not None]) + "\n",
                errors="surrogateescape",
            )
            # Announce AFTER the commit (adversarial r17, Minimalist,
            # probed): the carried-verbatim warning used to precede
            # atomic_write, so a failed rewrite left a log claiming rows
            # were carried through a rewrite that never happened.
            if tainted or unprovable:
                logger.warning(
                    "[skills] _save_skills: %d unparseable/byte-tainted and "
                    "%d unprovable row(s) carried through the rewrite "
                    "verbatim (%s)", tainted, unprovable, path)
            if compacted:
                logger.warning(
                    "[skills] _save_skills: %d older duplicate row(s) for "
                    "updated id(s) compacted by this rewrite — last row "
                    "per id won (%s)", compacted, path)
            # A committed removal is operator-visible and names its
            # store (adversarial r17, Failure Operator, probed: a cull
            # or rollback removed rows with no line naming skills.jsonl).
            if dropped_seen:
                # warning, not info: the announcement exists for
                # operator visibility, and info is invisible at the
                # default level — same reasoning as the reader
                # announcements.
                logger.warning(
                    "[skills] _save_skills: %d physical row(s) for %d "
                    "named id(s) removed by this rewrite (%s): %s",
                    dropped_rows, len(dropped_seen), path,
                    sorted(dropped_seen))
            if stranded_dropped:
                logger.warning(
                    "[skills] _save_skills: %d named drop(s) NOT "
                    "applied — the live row(s) for these id(s) are "
                    "present but unprovable, carried verbatim; the row "
                    "was NOT removed; repair, then confirm the drop "
                    "(%s): %s",
                    len(stranded_dropped), path, stranded_dropped)
            if partially_dropped:
                logger.warning(
                    "[skills] _save_skills: %d named drop(s) removed "
                    "the provable row(s), but unprovable duplicate "
                    "row(s) for these id(s) remain in the store, "
                    "carried verbatim (%s): %s",
                    len(partially_dropped), path, partially_dropped)
            if stranded_named:
                # Present, not deleted (adversarial r19, two seats,
                # probed): the row for this named id is stranded
                # verbatim above — "concurrently removed" would send
                # the operator hunting a deletion that never happened
                # when the fix is to repair the row and retry.
                logger.warning(
                    "[skills] _save_skills: %d named write(s) NOT "
                    "applied — the live row(s) for these id(s) are "
                    "present but unprovable, carried verbatim; repair "
                    "and retry (%s): %s",
                    len(stranded_named), path, stranded_named)
            if ghost_ids:
                # No parseable live row holds these ids. The causes
                # this function cannot tell apart: concurrently
                # removed, never created, or riding one of the
                # byte-tainted rows whose ids are unrecoverable — the
                # message claims only what the scan proved
                # (adversarial r19, two seats).
                logger.warning(
                    "[skills] _save_skills: %d named write(s) NOT "
                    "applied — no parseable live row holds these id(s) "
                    "(concurrently removed or never created%s); nothing "
                    "was written for them and nothing was removed by "
                    "this refusal (%s): %s",
                    len(ghost_ids),
                    (", or held by one of the %d row(s) carried "
                     "verbatim whose id could not be read"
                     % (tainted + unprovable_unnamed))
                    if (tainted + unprovable_unnamed) else "",
                    path, ghost_ids)
            if divergent:
                logger.warning(
                    "[skills] _save_skills: %d unnamed row(s) in the "
                    "caller's list differ from the live store — either "
                    "an unnamed edit was discarded, or a concurrent "
                    "write legitimately moved the row after the "
                    "caller's snapshot; the live row was carried "
                    "either way (%s): %s",
                    len(divergent), path, sorted(divergent))
    except Exception as e:
        # Name the store and RAISE (adversarial r16, two seats, probed):
        # the warn-and-return-None shape let a cull report "retired"
        # while every skill remained live — the same
        # error-result-is-a-valid-value flaw r15 removed from the stats
        # recorders.
        logger.error(
            "[skills] _save_skills: pool rewrite NOT performed (%s): %s",
            path, e)
        raise


# ---------------------------------------------------------------------------
# Phase 14: Unit-test gate on skill mutations
# ---------------------------------------------------------------------------

def _skill_tests_path() -> Path:
    from orch_items import memory_dir
    return memory_dir() / "skill-tests.jsonl"


def _save_skill_tests(tests: List[SkillTestCase]) -> None:
    """Append test cases to memory/skill-tests.jsonl."""
    from file_lock import locked_write
    path = _skill_tests_path()
    with locked_write(path):
        with path.open("a", encoding="utf-8") as f:
            for t in tests:
                f.write(json.dumps(t.to_dict()) + "\n")


def _load_skill_tests(skill_id: str) -> List[SkillTestCase]:
    """Load test cases for a specific skill_id from skill-tests.jsonl."""
    path = _skill_tests_path()
    if not path.exists():
        return []
    tests: List[SkillTestCase] = []
    drifted = 0
    for d in _read_store(path, "_load_skill_tests"):
        if d.get("skill_id") != skill_id:
            continue
        try:
            tests.append(SkillTestCase.from_dict(d))
        except Exception:
            drifted += 1
    if drifted:
        logger.warning("[skills] _load_skill_tests: %d row(s) for %s are JSON "
                       "but not loadable as SkillTestCase — skipped (%s)",
                       drifted, skill_id, path)
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
                no_tools=True,
                purpose="skill test generation",
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
) -> Tuple[int, int]:
    """Run test cases against a skill.

    For each test: prompt the skill with input_description, check if any
    expected_keyword appears in the response.

    Args:
        skill:     Skill to test.
        tests:     List of SkillTestCase to run.
        adapter:   LLMAdapter. None or dry_run → all pass.
        dry_run:   If True, return (len(tests), len(tests)) — all pass.

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
                # Contract, not agentic: the "execution" is a textual smoke
                # test — output is keyword-matched only, and this runs inside
                # the autonomous mutation gate where live tools would be a
                # side-effect hazard.
                resp = adapter.complete(
                    [
                        LLMMessage("system", skill_context),
                        LLMMessage("user", test.input_description),
                    ],
                    max_tokens=256,
                    temperature=0.1,
                    no_tools=True,
                    purpose="skill smoke test",
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
