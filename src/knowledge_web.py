#!/usr/bin/env python3
"""Tiered Memory — the associative/web layer of the knowledge architecture.

Three tiers:
  SHORT  — in-process only, never persisted. Evicted at session end.
  MEDIUM — memory/medium/lessons.jsonl. Decays daily; promoted on validation.
  LONG   — memory/long/lessons.jsonl. Explicit promotion required.

Grok decay model:
  score *= 0.85  per non-reinforced day
  score  = min(1.0, score + 0.3)  on reinforcement
  Promote when score >= 0.9 AND sessions_validated >= 3
  GC (garbage-collect) when score < 0.2

Extracted from memory.py (lines 497–1467) — Phase 16+ tiered memory,
TF-IDF ranking, gap detection, canon tracking, and memory status.
"""
from __future__ import annotations

import json
import math
import re
import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from memory_ledger import _memory_dir, _text_similarity

# Hybrid retrieval (BM25 + RRF) — graceful fallback to TF-IDF if unavailable
try:
    from hybrid_search import hybrid_rank as _hybrid_rank
    _USE_HYBRID = True
except ImportError:  # pragma: no cover
    _USE_HYBRID = False

# ---------------------------------------------------------------------------
# Lesson taxonomy + citation penalty (from Phase 59/60)
# ---------------------------------------------------------------------------

_LESSON_TYPES = frozenset({"execution", "planning", "recovery", "verification", "cost"})

# Phase 60: citation enforcement — uncited lessons are gently penalised in ranking.
# A 10% discount means a clearly-better uncited lesson still wins; this is a tie-breaker.
_CITATION_PENALTY = 0.90

# ===========================================================================
# Phase 16: Tiered Memory — Short, Medium, Long Term
# ===========================================================================

DECAY_FACTOR = 0.85          # daily non-reinforced decay multiplier
REINFORCE_BONUS = 0.3        # added to score on reinforcement
PROMOTE_MIN_SCORE = 0.9      # minimum score to promote medium → long
PROMOTE_MIN_SESSIONS = 3     # minimum validated sessions to promote
GC_THRESHOLD = 0.2           # gc entries with score below this


class MemoryTier:
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"


@dataclass
class TieredLesson:
    """A lesson with decay score and tier placement (Phase 16).

    Phase 59 (Feynman steal): evidence_sources field enables claim tracing —
    every lesson can carry the URLs/papers/outcomes that back its claim.
    """
    lesson_id: str
    task_type: str
    outcome: str
    lesson: str
    source_goal: str
    confidence: float
    tier: str                       # MemoryTier.MEDIUM | MemoryTier.LONG
    score: float                    # Grok decay score; starts at 1.0
    last_reinforced: str            # ISO date (YYYY-MM-DD)
    sessions_validated: int = 0     # how many sessions have confirmed this lesson
    times_applied: int = 0
    times_reinforced: int = 0
    recorded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acquired_for: Optional[str] = None  # goal_id that triggered this lesson (incidental flag)
    # Phase 59: evidence sources for claim tracing (URLs, outcome_ids, paper refs)
    evidence_sources: List[str] = field(default_factory=list)
    # Phase 59 NeMo S1: typed lesson taxonomy — "execution" | "planning" | "recovery" | "verification" | "cost"
    lesson_type: str = ""


# ---------------------------------------------------------------------------
# Short-term memory (in-process only, session-scoped)
# ---------------------------------------------------------------------------

_SHORT_TERM: Dict[str, Any] = {}


def short_set(key: str, value: Any) -> None:
    """Store a value in the short-term (session-scoped) memory store."""
    _SHORT_TERM[key] = value


def short_get(key: str, default: Any = None) -> Any:
    """Retrieve a value from short-term memory. Returns default if absent."""
    return _SHORT_TERM.get(key, default)


def short_clear() -> None:
    """Evict all short-term memory. Call at session end."""
    _SHORT_TERM.clear()


def short_all() -> Dict[str, Any]:
    """Return a snapshot of all short-term memory (read-only view)."""
    return dict(_SHORT_TERM)


# ---------------------------------------------------------------------------
# Storage paths (tiered)
# ---------------------------------------------------------------------------

def _tiered_lessons_path(tier: str) -> Path:
    d = _memory_dir() / tier
    d.mkdir(parents=True, exist_ok=True)
    return d / "lessons.jsonl"


# ---------------------------------------------------------------------------
# Decay helpers
# ---------------------------------------------------------------------------

def _days_since(date_str: str) -> int:
    """Return whole days elapsed since date_str (YYYY-MM-DD)."""
    try:
        recorded = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return max(0, (now - recorded).days)
    except Exception:
        return 0


def decay_score(score: float, days: int) -> float:
    """Apply exponential decay: score *= DECAY_FACTOR^days."""
    return score * (DECAY_FACTOR ** days)


def reinforce_score(score: float) -> float:
    """Apply reinforcement bonus: score = min(1.0, score + REINFORCE_BONUS)."""
    return min(1.0, score + REINFORCE_BONUS)


def _current_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# CRUD for tiered lessons
# ---------------------------------------------------------------------------

# Phase 59 Feynman F5: Standardized confidence tiers.
# Confidence reflects extraction reliability, not just domain certainty.
_CONFIDENCE_SINGLE_CALL = 0.5    # single LLM call — not independently verified
_CONFIDENCE_MAJORITY_VOTE = 0.7  # majority-vote across k_samples ≥ 3
_CONFIDENCE_MULTI_SESSION = 0.9  # sessions_validated ≥ 3 — independently confirmed


def confidence_from_k_samples(k_samples: int) -> float:
    """Map extraction method to standardized initial confidence (Feynman F5).

    - k_samples == 1: single LLM call → 0.5 (unverified)
    - k_samples >= 3: majority-vote → 0.7 (consensus)
    - k_samples == 2: in-between → 0.6
    """
    if k_samples >= 3:
        return _CONFIDENCE_MAJORITY_VOTE
    if k_samples == 2:
        return 0.6
    return _CONFIDENCE_SINGLE_CALL


def record_tiered_lesson(
    lesson_text: str,
    task_type: str,
    outcome: str,
    source_goal: str,
    *,
    tier: str = MemoryTier.MEDIUM,
    confidence: float = _CONFIDENCE_MAJORITY_VOTE,
    k_samples: int = 0,
    acquired_for: Optional[str] = None,
    evidence_sources: Optional[List[str]] = None,
    lesson_type: str = "",
) -> TieredLesson:
    """Record a new lesson at the given tier.

    Checks for near-duplicates before writing; reinforces existing if match found.
    Pass ``acquired_for=goal_id`` to tag incidental knowledge (e.g. lessons acquired
    as a prerequisite sub-goal rather than as the primary task outcome).

    Phase 59 Feynman F5: when ``k_samples`` is set (> 0), initial confidence is
        computed from the extraction method rather than the caller's estimate:
        k_samples=1 → 0.5, k_samples=2 → 0.6, k_samples≥3 → 0.7.
        Explicit ``confidence`` kwarg overrides this when k_samples=0.
    Phase 59 NeMo S1: ``lesson_type`` classifies the lesson — "execution" | "planning" |
        "recovery" | "verification" | "cost". Enables type-filtered retrieval.
    Phase 59: ``evidence_sources`` accepts a list of URLs/outcome_ids/paper refs
        that back the lesson's claim, enabling post-hoc claim tracing.
    """
    import uuid

    if k_samples > 0:
        confidence = confidence_from_k_samples(k_samples)

    existing = load_tiered_lessons(tier=tier, task_type=task_type)
    for ex in existing:
        if _text_similarity(ex.lesson, lesson_text) > 0.8:
            return _reinforce_tiered_lesson(ex, tier=tier)

    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type=task_type,
        outcome=outcome,
        lesson=lesson_text,
        source_goal=source_goal,
        confidence=confidence,
        tier=tier,
        score=1.0,
        last_reinforced=_current_date(),
        acquired_for=acquired_for,
        evidence_sources=evidence_sources or [],
        lesson_type=lesson_type if lesson_type in _LESSON_TYPES else "",
    )
    _append_tiered_lesson(tl, tier=tier)

    # Captain's log
    try:
        from captains_log import log_event, LESSON_RECORDED
        log_event(
            event_type=LESSON_RECORDED,
            subject=tl.lesson_id,
            summary=f"New {tier} lesson (confidence: {confidence:.2f}): {lesson_text[:100]}",
            context={"tier": tier, "task_type": task_type, "confidence": confidence, "lesson_type": lesson_type},
        )
    except Exception:
        pass

    return tl


def _append_tiered_lesson(tl: TieredLesson, *, tier: str) -> None:
    path = _tiered_lessons_path(tier)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(tl)) + "\n")


def _reinforce_tiered_lesson(tl: TieredLesson, *, tier: str) -> TieredLesson:
    """Reinforce an existing lesson: bump score and sessions_validated, rewrite file.

    Phase 59 Feynman F5: once sessions_validated reaches 3, confidence is bumped
    to _CONFIDENCE_MULTI_SESSION (0.9+) — independently confirmed across sessions.
    """
    tl.score = reinforce_score(tl.score)
    tl.sessions_validated += 1
    tl.times_reinforced += 1
    tl.last_reinforced = _current_date()
    # F5: multi-session confidence promotion
    if tl.sessions_validated >= 3:
        tl.confidence = max(tl.confidence, _CONFIDENCE_MULTI_SESSION)
    _rewrite_tiered_lessons(tier)
    return tl


def load_tiered_lessons(
    tier: str,
    *,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    min_score: float = 0.0,
    limit: int = 50,
    max_age_days: Optional[int] = None,
) -> List[TieredLesson]:
    """Load tiered lessons from disk, applying current-day decay inline.

    Args:
        lesson_type:  If set, only return lessons with this lesson_type
                      (Phase 59 NeMo S1 typed taxonomy filter).
        max_age_days: If set, skip lessons last reinforced more than this many days ago.
                      Useful for pruning stale lessons in retrieval contexts.
    """
    path = _tiered_lessons_path(tier)
    if not path.exists():
        return []

    results: List[TieredLesson] = []
    today = _current_date()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                tl = TieredLesson(**{k: d[k] for k in TieredLesson.__dataclass_fields__ if k in d})
                # Apply decay inline (days since last reinforcement)
                days = _days_since(tl.last_reinforced)
                if max_age_days is not None and days > max_age_days:
                    continue  # lesson too stale
                if days > 0:
                    tl.score = decay_score(tl.score, days)
                if tl.score < min_score:
                    continue
                if task_type and tl.task_type != task_type:
                    continue
                if lesson_type and tl.lesson_type != lesson_type:
                    continue
                results.append(tl)
            except Exception:
                continue
    except Exception:
        pass

    results.sort(key=lambda x: x.score, reverse=True)
    return results[:limit]


def _rewrite_tiered_lessons(tier: str, lessons: Optional[List[TieredLesson]] = None) -> None:
    """Rewrite the tiered lessons file with the current state (after updates/GC)."""
    if lessons is None:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    path = _tiered_lessons_path(tier)
    try:
        from file_lock import locked_write
        with locked_write(path):
            with open(path, "w", encoding="utf-8") as f:
                for tl in lessons:
                    f.write(json.dumps(asdict(tl)) + "\n")
    except ImportError:
        with open(path, "w", encoding="utf-8") as f:
            for tl in lessons:
                f.write(json.dumps(asdict(tl)) + "\n")


# ---------------------------------------------------------------------------
# Reinforce, forget, promote
# ---------------------------------------------------------------------------

def reinforce_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> Optional[TieredLesson]:
    """Find lesson by ID in the given tier and reinforce it (score + sessions).

    Phase 59 Feynman F5: once sessions_validated reaches 3, confidence is
    promoted to >= _CONFIDENCE_MULTI_SESSION (0.9).
    """
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    target = next((l for l in lessons if l.lesson_id == lesson_id), None)
    if not target:
        return None
    target.score = reinforce_score(target.score)
    target.sessions_validated += 1
    target.times_reinforced += 1
    target.last_reinforced = _current_date()
    if target.sessions_validated >= 3:
        target.confidence = max(target.confidence, _CONFIDENCE_MULTI_SESSION)
    _rewrite_tiered_lessons(tier=tier, lessons=lessons)

    # Captain's log
    try:
        from captains_log import log_event, LESSON_REINFORCED
        log_event(
            event_type=LESSON_REINFORCED,
            subject=lesson_id,
            summary=f"Reinforced (sessions: {target.sessions_validated}, score: {target.score:.2f}): {target.lesson[:80]}",
            context={"tier": tier, "sessions_validated": target.sessions_validated, "score": round(target.score, 3)},
        )
    except Exception:
        pass

    return target


def search_graveyard(
    topic: str,
    *,
    min_score: float = GC_THRESHOLD,
    max_score: float = 0.4,
    limit: int = 10,
    resurrect: bool = False,
) -> List[TieredLesson]:
    """Find decayed lessons matching *topic* before triggering a sub-goal re-acquisition.

    The "graveyard" is lessons in the decay band [GC_THRESHOLD, 0.4) — still on disk
    but below the active-injection threshold (0.3 default in inject_lessons).  These
    are recoverable via ``reinforce_lesson()``.

    Args:
        topic:      Keywords to fuzzy-match against lesson text (space-separated; any
                    word match counts; ranked by match ratio then score).
        min_score:  Lower bound — default is GC_THRESHOLD (0.2) to include everything
                    that hasn't been GC'd yet.
        max_score:  Upper bound — default 0.4 (just below the injection threshold 0.3,
                    plus a small buffer to surface lessons that need one reinforcement
                    to become active again).
        limit:      Maximum results to return.
        resurrect:  If True, automatically call ``reinforce_lesson()`` on every match,
                    bumping them back toward the active zone.  Default False (read-only).

    Returns a list of TieredLesson sorted by similarity then score (descending).
    """
    keywords = [w.lower() for w in topic.split() if w]
    results: List[tuple] = []

    for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
        lessons = load_tiered_lessons(tier=tier, min_score=min_score)
        for tl in lessons:
            if tl.score >= max_score:
                continue
            text = tl.lesson.lower()
            match_ratio = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
            if match_ratio > 0:
                results.append((match_ratio, tl.score, tl))

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    matched = [tl for _, _, tl in results[:limit]]

    if resurrect:
        for tl in matched:
            reinforce_lesson(tl.lesson_id, tier=tl.tier)

    return matched


def forget_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> bool:
    """Permanently remove a lesson from a tier. Returns True if found and removed."""
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)
    before = len(lessons)
    lessons = [l for l in lessons if l.lesson_id != lesson_id]
    if len(lessons) == before:
        return False
    _rewrite_tiered_lessons(tier=tier, lessons=lessons)
    return True


def promote_lesson(lesson_id: str) -> bool:
    """Promote a medium-tier lesson to long-tier.

    Eligibility: score >= PROMOTE_MIN_SCORE AND sessions_validated >= PROMOTE_MIN_SESSIONS.
    Returns True if promotion succeeded.
    """
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
    target = next((l for l in lessons if l.lesson_id == lesson_id), None)
    if not target:
        return False
    if target.score < PROMOTE_MIN_SCORE or target.sessions_validated < PROMOTE_MIN_SESSIONS:
        return False
    # Remove from medium, add to long
    lessons = [l for l in lessons if l.lesson_id != lesson_id]
    _rewrite_tiered_lessons(tier=MemoryTier.MEDIUM, lessons=lessons)
    target.tier = MemoryTier.LONG
    _append_tiered_lesson(target, tier=MemoryTier.LONG)

    # Feed into standing-rule pipeline: observe the pattern for hypothesis tracking
    try:
        from knowledge_lens import observe_pattern
        domain = getattr(target, "task_type", "") or ""
        observe_pattern(target.lesson, domain, source_lesson_id=target.lesson_id)
    except Exception:
        pass  # standing-rule pipeline must not block lesson promotion

    return True


# ---------------------------------------------------------------------------
# Decay cycle (run daily / on session start)
# ---------------------------------------------------------------------------

def run_decay_cycle(
    tier: str = MemoryTier.MEDIUM,
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Apply decay to all lessons in a tier, auto-promote eligibles, GC below threshold.

    Returns a dict with counts: decayed, promoted, gc'd.
    """
    lessons = load_tiered_lessons(tier=tier, min_score=0.0)

    decayed = 0
    promoted_ids = []
    gc_ids = []

    for tl in lessons:
        days = _days_since(tl.last_reinforced)
        if days > 0:
            old_score = tl.score
            tl.score = decay_score(tl.score, days)
            if tl.score != old_score:
                decayed += 1

        if tier == MemoryTier.MEDIUM:
            if tl.score >= PROMOTE_MIN_SCORE and tl.sessions_validated >= PROMOTE_MIN_SESSIONS:
                promoted_ids.append(tl.lesson_id)
            elif tl.score < GC_THRESHOLD:
                gc_ids.append(tl.lesson_id)

    if not dry_run:
        # Audit trail: log the decay cycle before mutating lesson store.
        try:
            from datetime import datetime as _dt, timezone as _tz
            _cl_path = _tiered_lessons_path(tier).parent / "change_log.jsonl"
            _cl_entry = {
                "ts": _dt.now(_tz.utc).isoformat(),
                "module": "knowledge_web",
                "action": "run_decay_cycle",
                "tier": tier,
                "total": len(lessons),
                "decayed": decayed,
                "promoted": len(promoted_ids),
                "gc": len(gc_ids),
                "promoted_ids": promoted_ids,
                "gc_ids": gc_ids,
            }
            with open(_cl_path, "a", encoding="utf-8") as _clf:
                _clf.write(json.dumps(_cl_entry) + "\n")
        except Exception:
            pass  # audit trail must never block execution

        # Promote eligible lessons
        for lid in promoted_ids:
            promote_lesson(lid)

        # Rewrite remaining lessons using the in-memory list (with updated decay scores).
        # Do NOT reload from disk here — a reload would lose the score changes computed above.
        promoted_set = set(promoted_ids)
        gc_set = set(gc_ids)
        remaining = [l for l in lessons if l.lesson_id not in promoted_set and l.lesson_id not in gc_set]
        _rewrite_tiered_lessons(tier=tier, lessons=remaining)

    return {"decayed": decayed, "promoted": len(promoted_ids), "gc": len(gc_ids)}


# ---------------------------------------------------------------------------
# TF-IDF relevance ranking (Phase 35 P1)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "is", "was", "are", "were", "be", "been", "being", "it",
    "its", "this", "that", "these", "those", "i", "we", "you", "he", "she",
    "they", "what", "when", "where", "who", "which", "how", "if", "as", "by",
    "from", "not", "can", "will", "do", "did", "does", "have", "had", "has",
    "should", "would", "could", "may", "might", "step", "goal", "task",
})


def _tokenize(text: str) -> List[str]:
    """Lowercase + split on non-alphanumeric, filter stop words + short tokens."""
    return [
        t for t in re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
        if t not in _STOP_WORDS and len(t) > 2
    ]


def _tfidf_rank(
    query: str,
    lessons: List[TieredLesson],
    *,
    top_k: Optional[int] = None,
) -> List[TieredLesson]:
    """Rank lessons by TF-IDF cosine similarity to query.

    Pure stdlib — no sklearn, no numpy. Uses Counter for term frequency,
    log-IDF for inverse document frequency, cosine similarity for ranking.

    Args:
        query: Goal or step text used as the query document.
        lessons: List of TieredLesson objects to rank.
        top_k: Return only the top-K matches. None = return all, ranked.

    Returns:
        Lessons sorted by descending cosine similarity to query.
        Lessons with zero similarity are still included (sorted last).
    """
    if not lessons:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return lessons  # no query signal — return as-is

    # Build corpus: query + all lesson texts
    docs: List[List[str]] = [query_terms]
    for l in lessons:
        docs.append(_tokenize(l.lesson))

    n_docs = len(docs)  # includes query

    # IDF: log(N / df + 1) for each term across the corpus
    df: Counter = Counter()
    for doc in docs:
        for term in set(doc):
            df[term] += 1

    def idf(term: str) -> float:
        return math.log(n_docs / (df.get(term, 0) + 1)) + 1.0

    def tfidf_vec(doc_terms: List[str]) -> Dict[str, float]:
        tf = Counter(doc_terms)
        total = max(len(doc_terms), 1)
        return {t: (c / total) * idf(t) for t, c in tf.items()}

    def cosine(v1: Dict[str, float], v2: Dict[str, float]) -> float:
        dot = sum(v1.get(t, 0.0) * v2.get(t, 0.0) for t in v1)
        norm1 = math.sqrt(sum(x * x for x in v1.values())) or 1.0
        norm2 = math.sqrt(sum(x * x for x in v2.values())) or 1.0
        return dot / (norm1 * norm2)

    query_vec = tfidf_vec(query_terms)
    scores: List[tuple] = []
    for lesson, doc_terms in zip(lessons, docs[1:]):
        doc_vec = tfidf_vec(doc_terms)
        sim = cosine(query_vec, doc_vec)
        # Phase 60: citation enforcement — lessons without evidence_sources
        # are penalised by _CITATION_PENALTY so cited lessons rank higher on ties.
        _has_cite = bool(getattr(lesson, "evidence_sources", None))
        if not _has_cite:
            sim *= _CITATION_PENALTY
        scores.append((sim, lesson))

    scores.sort(key=lambda x: x[0], reverse=True)
    ranked = [l for _, l in scores]
    return ranked[:top_k] if top_k is not None else ranked


# ---------------------------------------------------------------------------
# Tier-aware context injection
# ---------------------------------------------------------------------------

def inject_tiered_lessons(
    task_type: str,
    goal: str = "",
    *,
    max_long: int = 5,
    max_medium: int = 3,
    include_short: bool = False,
    track_applied: bool = True,
) -> str:
    """Build a lessons injection string that respects tier priority.

    Long-tier lessons are always included (up to max_long).
    Medium-tier lessons are filtered by recency and relevance.
    Short-tier (session) items only included if include_short=True.

    If track_applied=True (default), increments times_applied on each injected
    lesson. This powers the canon-candidates pathway: lessons applied many times
    across diverse task types become candidates for AGENTS.md identity promotion.
    """
    parts: List[str] = []
    applied_ids: List[tuple] = []  # (lesson_id, tier)

    # Load candidate lessons — fetch a wider pool when using TF-IDF ranking
    _pool_multiplier = 3 if goal else 1

    long_candidates = load_tiered_lessons(
        tier=MemoryTier.LONG, task_type=task_type, min_score=0.0,
        limit=max_long * _pool_multiplier,
    )
    if goal and len(long_candidates) > max_long:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        long_candidates = _ranker(goal, long_candidates, top_k=max_long)
    long_lessons = long_candidates[:max_long]

    if long_lessons:
        parts.append("### Long-Term Lessons (always apply)")
        for l in long_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson}")
            applied_ids.append((l.lesson_id, MemoryTier.LONG))

    medium_candidates = load_tiered_lessons(
        tier=MemoryTier.MEDIUM, task_type=task_type, min_score=0.3,
        limit=max_medium * _pool_multiplier,
    )
    if goal and len(medium_candidates) > max_medium:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        medium_candidates = _ranker(goal, medium_candidates, top_k=max_medium)
    medium_lessons = medium_candidates[:max_medium]

    if medium_lessons:
        parts.append("### Medium-Term Lessons (apply if relevant)")
        for l in medium_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson} [score={l.score:.2f}]")
            applied_ids.append((l.lesson_id, MemoryTier.MEDIUM))

    if include_short and _SHORT_TERM:
        parts.append("### Session Context")
        for k, v in list(_SHORT_TERM.items())[:5]:
            parts.append(f"- {k}: {str(v)[:80]}")

    if not parts:
        return ""

    # Track application counts for canon-candidate detection
    if track_applied and applied_ids:
        _increment_times_applied(applied_ids, task_type=task_type)

    return "## Tiered Lessons\n\n" + "\n".join(parts)


# ---------------------------------------------------------------------------
# Phase 59 (Feynman Steal 10): Multi-round gap analysis
# ---------------------------------------------------------------------------

@dataclass
class GoalGap:
    """A detected gap in goal coverage — motivates a targeted follow-up step.

    Feynman pattern: after each research round, assess gaps and spawn targeted
    steps to fill them. GoalGap describes what evidence or coverage is missing.
    """
    gap_type: str          # "single_source" | "blocked_step" | "lesson_gap" | "no_coverage"
    description: str       # human-readable gap description
    severity: str          # "high" | "medium" | "low"
    suggested_step: str    # what follow-up step to spawn (empty = no suggestion)


def detect_goal_gaps(
    goal: str,
    outcomes: Optional[List] = None,
    *,
    blocked_steps: Optional[List[str]] = None,
    max_gaps: int = 5,
) -> List[GoalGap]:
    """Detect coverage gaps in a set of outcomes relative to a goal.

    Phase 59 (Feynman Steal 10): Heuristic gap detection — no LLM call.
    Identifies:
    1. Blocked/stuck steps that were attempted but not completed
    2. Lessons that mention key terms in the goal but weren't applied
    3. Goal keywords with zero outcome coverage
    4. Outcomes with no evidence sources (single-source claims)

    Args:
        goal:          The original goal text.
        outcomes:      List of completed Outcomes. Loads recent if None.
        blocked_steps: List of step texts that were blocked/stuck.
        max_gaps:      Maximum number of gaps to return.

    Returns:
        List of GoalGap objects, most severe first.
    """
    gaps: List[GoalGap] = []
    goal_lower = goal.lower()

    # Gap 1: Blocked steps → high-severity gaps
    for step in (blocked_steps or []):
        gaps.append(GoalGap(
            gap_type="blocked_step",
            description=f"Step was blocked and not completed: {step[:100]}",
            severity="high",
            suggested_step=f"Retry with different approach: {step[:80]}",
        ))

    # Gap 2: Load outcomes if not provided
    if outcomes is None:
        try:
            from memory_ledger import load_outcomes
            outcomes = load_outcomes(limit=20)
        except Exception:
            outcomes = []

    # Gap 3: Check goal keywords against outcome coverage
    # Extract meaningful keywords from goal (skip stopwords)
    _STOP = {"the", "a", "an", "and", "or", "for", "to", "in", "of", "is",
              "it", "this", "that", "with", "from", "are", "were", "have"}
    goal_words = set(
        w for w in re.findall(r"[a-z]{4,}", goal_lower) if w not in _STOP
    )
    covered_words: set = set()
    for o in outcomes:
        text = (o.goal + " " + o.summary).lower()
        covered_words.update(w for w in goal_words if w in text)

    uncovered = goal_words - covered_words
    if uncovered and len(uncovered) >= 2:
        sample = list(uncovered)[:3]
        gaps.append(GoalGap(
            gap_type="no_coverage",
            description=f"Goal concepts not addressed in outcomes: {', '.join(sample)}",
            severity="medium",
            suggested_step=f"Research specifically: {', '.join(sample)}",
        ))

    # Gap 4: Recent lessons about similar topics that weren't applied
    try:
        relevant_lessons = query_lessons(goal, n=3)
        unused_lessons = [l for l in relevant_lessons if l.times_applied == 0]
        if unused_lessons:
            gaps.append(GoalGap(
                gap_type="lesson_gap",
                description=f"Relevant past lessons not applied: {unused_lessons[0].lesson[:80]}",
                severity="low",
                suggested_step="",  # no specific step — just apply the lesson
            ))
    except Exception:
        pass

    # Sort by severity and truncate
    _sev_order = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(key=lambda g: _sev_order.get(g.severity, 3))
    return gaps[:max_gaps]


def query_lessons(
    query: str,
    *,
    n: int = 3,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    tiers: Optional[List[str]] = None,
    min_score: float = 0.0,
) -> List[TieredLesson]:
    """Retrieve the top-N lessons most relevant to `query` via hybrid retrieval.

    Workers can call this directly in step context to get relevant past insights
    without burning tokens on full lesson injection.

    Args:
        query:       Goal text or step description to match against.
        n:           Maximum number of lessons to return.
        task_type:   If set, only search lessons for this task type.
        lesson_type: If set, only return lessons of this type (NeMo S1 filter).
                     Values: "execution" | "planning" | "recovery" | "verification" | "cost"
        tiers:       Which tiers to search. Default: [LONG, MEDIUM].
        min_score:   Minimum lesson confidence/score to include.

    Returns:
        List of TieredLesson objects (most relevant first).
    """
    if tiers is None:
        tiers = [MemoryTier.LONG, MemoryTier.MEDIUM]

    _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank

    candidates: List[TieredLesson] = []
    for tier in tiers:
        pool = load_tiered_lessons(
            tier=tier,
            task_type=task_type,
            lesson_type=lesson_type,
            min_score=min_score,
            limit=n * 5,
        )
        candidates.extend(pool)

    if not candidates:
        return []

    ranked = _ranker(query, candidates, top_k=n)
    return ranked[:n]


def _increment_times_applied(
    lesson_ids: List[tuple],
    *,
    task_type: str,
) -> None:
    """Increment times_applied for each (lesson_id, tier) pair.

    Also records which task_types a lesson has been applied to, enabling
    the canon-candidate check (task_type diversity gate).
    """
    for lid, tier in lesson_ids:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
        target = next((l for l in lessons if l.lesson_id == lid), None)
        if not target:
            continue
        target.times_applied += 1
        # Track task_type diversity in short-term store (session-level aggregator)
        # Persisted canon-tracking uses a separate canon_stats.jsonl
        _record_canon_hit(lid, tier=tier, task_type=task_type)
        _rewrite_tiered_lessons(tier=tier, lessons=lessons)


# ---------------------------------------------------------------------------
# Canon tracking (long → AGENTS.md identity path)
# ---------------------------------------------------------------------------

CANON_APPLY_THRESHOLD = 10   # times_applied before surfacing as candidate
CANON_TASK_TYPE_MIN = 3      # distinct task_types before surfacing as candidate


def _canon_stats_path() -> Path:
    d = _memory_dir()
    return d / "canon_stats.jsonl"


def _record_canon_hit(lesson_id: str, *, tier: str, task_type: str) -> None:
    """Record that lesson_id was applied to task_type. Appends to canon_stats.jsonl."""
    path = _canon_stats_path()
    entry = {
        "lesson_id": lesson_id,
        "tier": tier,
        "task_type": task_type,
        "at": _current_date(),
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _load_canon_stats() -> Dict[str, Dict[str, Any]]:
    """Load aggregated canon stats keyed by lesson_id.

    Returns: {lesson_id: {total_hits, task_types: set, tier}}
    """
    path = _canon_stats_path()
    if not path.exists():
        return {}
    stats: Dict[str, Dict[str, Any]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                lid = e["lesson_id"]
                if lid not in stats:
                    stats[lid] = {"total_hits": 0, "task_types": set(), "tier": e.get("tier", MemoryTier.LONG)}
                stats[lid]["total_hits"] += 1
                stats[lid]["task_types"].add(e.get("task_type", "general"))
            except Exception:
                continue
    except Exception:
        pass
    return stats


def get_canon_candidates(
    *,
    min_hits: int = CANON_APPLY_THRESHOLD,
    min_task_types: int = CANON_TASK_TYPE_MIN,
) -> List[Dict[str, Any]]:
    """Return long-tier lessons eligible for promotion to AGENTS.md identity.

    Eligibility: times_applied >= min_hits AND distinct task_types >= min_task_types.
    Candidates are surfaced for human review — never auto-written to AGENTS.md.
    """
    stats = _load_canon_stats()
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0, limit=200)
    lesson_map = {l.lesson_id: l for l in long_lessons}

    candidates = []
    for lid, s in stats.items():
        if s["tier"] != MemoryTier.LONG:
            continue
        if s["total_hits"] < min_hits:
            continue
        if len(s["task_types"]) < min_task_types:
            continue
        lesson = lesson_map.get(lid)
        if not lesson:
            continue
        candidates.append({
            "lesson_id": lid,
            "lesson": lesson.lesson,
            "task_type": lesson.task_type,
            "score": round(lesson.score, 3),
            "times_applied": s["total_hits"],
            "task_types_seen": sorted(s["task_types"]),
            "sessions_validated": lesson.sessions_validated,
            "recorded_at": lesson.recorded_at[:10],
            "recommendation": "PROMOTE TO AGENTS.md — identity-level pattern",
        })

    candidates.sort(key=lambda x: x["times_applied"], reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Memory status report
# ---------------------------------------------------------------------------

def memory_status() -> Dict[str, Any]:
    """Return a status report across all tiers."""
    def _tier_stats(tier: str) -> Dict[str, Any]:
        lessons = load_tiered_lessons(tier=tier, min_score=0.0)
        if not lessons:
            return {"count": 0}
        scores = [l.score for l in lessons]
        decay_candidates = [l for l in lessons if l.score < GC_THRESHOLD]
        promote_candidates = [
            l for l in lessons
            if l.score >= PROMOTE_MIN_SCORE and l.sessions_validated >= PROMOTE_MIN_SESSIONS
        ] if tier == MemoryTier.MEDIUM else []
        return {
            "count": len(lessons),
            "avg_score": round(sum(scores) / len(scores), 3),
            "min_score": round(min(scores), 3),
            "max_score": round(max(scores), 3),
            "gc_candidates": len(decay_candidates),
            "promote_candidates": len(promote_candidates),
            "oldest": min(l.recorded_at[:10] for l in lessons),
            "newest": max(l.recorded_at[:10] for l in lessons),
        }

    return {
        "short": {"count": len(_SHORT_TERM), "note": "in-process only"},
        "medium": _tier_stats(MemoryTier.MEDIUM),
        "long": _tier_stats(MemoryTier.LONG),
        "gc_threshold": GC_THRESHOLD,
        "promote_min_score": PROMOTE_MIN_SCORE,
        "promote_min_sessions": PROMOTE_MIN_SESSIONS,
    }
