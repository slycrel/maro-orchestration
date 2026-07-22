#!/usr/bin/env python3
"""Tiered Memory — the associative/web layer of the knowledge architecture.

Three tiers:
  SHORT  — in-process only, never persisted. Evicted at session end.
  MEDIUM — memory/medium/lessons.jsonl. Decays daily; promoted on validation.
  LONG   — memory/long/lessons.jsonl. Explicit promotion required.

Grok decay model:
  score *= 0.85  per non-reinforced day
  score  = min(max(1.0, score), score + 0.3)  on reinforcement (never lowers
           a novelty-boosted score > 1.0; classic min(1.0, ...) below that)
  Initial score = 1.0 + 0.3 * novelty  (novelty = 1 - max similarity vs the
           store at record time; chunk 6 — killswitch knowledge.novelty_term_enabled)
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
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

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
NOVELTY_BONUS = 0.3          # max initial-score boost for a fully novel lesson (chunk 6)
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
    # PORTABLE_LEARNING_DESIGN §3: provenance stamp for pack-imported rows; empty
    # on locally-originated lessons. asdict()/filtered-reconstruction round-trip
    # this automatically since it's a declared field.
    imported: Dict[str, Any] = field(default_factory=dict)
    # Chunk 6: inverse max-similarity vs the store at record time (0.0 = near-dup
    # of something we already knew, 1.0 = unlike anything stored). Boosts initial
    # score so novel one-offs survive decay long enough to be tested; wrong novel
    # guesses still die by decay. Old rows without this field deserialize to 0.0.
    novelty: float = 0.0


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
    """Apply reinforcement bonus, capped at 1.0 — unless the score is already
    above 1.0 (novelty-boosted, chunk 6), in which case reinforcement must
    never LOWER it: the cap becomes the score itself. Behavior for scores
    ≤ 1.0 is unchanged."""
    return min(max(1.0, score), score + REINFORCE_BONUS)


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


def _novelty_term_enabled() -> bool:
    """Killswitch for the chunk-6 novelty term (default ON). config.get
    returns raw YAML nodes — a quoted "false" is a truthy string, so
    normalize the same way the quality-gate killswitches do (chunk-5a
    review F1) or the killswitch can't kill."""
    try:
        from config import get as _cfg_get
        val = _cfg_get("knowledge.novelty_term_enabled", True)
        if isinstance(val, str):
            return val.strip().lower() not in ("false", "0", "no", "off")
        return bool(val)
    except Exception:
        return True


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

    # Reject lessons that look like prompt injection attempts
    try:
        from memory_ledger import _lesson_looks_adversarial
        if _lesson_looks_adversarial(lesson_text):
            log.warning("tiered lesson rejected (adversarial): %s", lesson_text[:80])
            # Return a dummy TieredLesson so callers don't crash
            return TieredLesson(
                lesson_id="rejected", lesson=lesson_text[:50], task_type=task_type,
                outcome=outcome, source_goal=source_goal, tier=tier,
                score=0.0, confidence=0.0, sessions_validated=0,
                times_reinforced=0, last_reinforced=_current_date(),
            )
    except ImportError:
        pass

    # Session 40 M2: a lesson the system already promoted to LONG and has now
    # re-learned is a production re-confirmation, not new knowledge. Reinforce
    # the long-tier record (which feeds the standing-rule pipeline) instead of
    # accreting a duplicate in medium. limit=None — a dedup check against a
    # truncated load would silently miss matches.
    # Chunk 6 (+ its adversarial review): the scans double as the novelty
    # measurement. DEDUP stays task_type-scoped (existing contract — identical
    # text under a different task type is a separate lesson, pinned in
    # test_tiered_memory); NOVELTY is store-wide (all task types), because
    # "novel" must mean novel to the agent, not novel within one dedup
    # partition — a cross-domain repeat is not a surprise.
    max_sim = 0.0
    if tier == MemoryTier.MEDIUM:
        for ex in load_tiered_lessons(tier=MemoryTier.LONG, task_type=None, limit=None):
            sim = _text_similarity(ex.lesson, lesson_text)
            if ex.task_type == task_type and sim > 0.8:
                return _reinforce_tiered_lesson(ex, tier=MemoryTier.LONG)
            max_sim = max(max_sim, sim)

    # Scan-and-append is one critical section (review finding: the dedup
    # read raced a concurrent writer's append — two workers recording the
    # same novel lesson both saw no match and both appended boosted
    # duplicates). locked_write is reentrant per-thread, so the reinforce
    # and append paths inside are safe.
    from file_lock import locked_write
    with locked_write(_tiered_lessons_path(tier)):
        for ex in load_tiered_lessons(tier=tier, task_type=None, limit=None):
            sim = _text_similarity(ex.lesson, lesson_text)
            if ex.task_type == task_type and sim > 0.8:
                return _reinforce_tiered_lesson(ex, tier=tier)
            max_sim = max(max_sim, sim)

        # Chunk 6: novelty term — a lesson unlike anything stored starts above
        # 1.0 so it survives decay long enough to be tested; repeat-shaped
        # lessons keep the classic 1.0. Counteracts the reinforce-the-familiar
        # bias (+0.3 for repeats while novel one-offs die in ~7 days).
        # Promotion is unaffected — sessions_validated still gates.
        # Killswitch: knowledge.novelty_term_enabled.
        novelty = 1.0 - max_sim
        score = 1.0
        if _novelty_term_enabled():
            score = 1.0 + NOVELTY_BONUS * novelty

        tl = TieredLesson(
            lesson_id=str(uuid.uuid4())[:8],
            task_type=task_type,
            outcome=outcome,
            lesson=lesson_text,
            source_goal=source_goal,
            confidence=confidence,
            tier=tier,
            score=score,
            last_reinforced=_current_date(),
            acquired_for=acquired_for,
            evidence_sources=evidence_sources or [],
            lesson_type=lesson_type if lesson_type in _LESSON_TYPES else "",
            novelty=round(novelty, 4),
        )
        _append_tiered_lesson(tl, tier=tier)

    # Captain's log
    try:
        from captains_log import log_event, LESSON_RECORDED
        log_event(
            event_type=LESSON_RECORDED,
            subject=tl.lesson_id,
            summary=f"New {tier} lesson (confidence: {confidence:.2f}): {lesson_text[:100]}",
            context={"tier": tier, "task_type": task_type, "confidence": confidence,
                     "lesson_type": lesson_type, "novelty": tl.novelty, "score": score},
        )
    except Exception:
        pass

    return tl


def _append_tiered_lesson(tl: TieredLesson, *, tier: str) -> None:
    from file_lock import locked_append
    locked_append(_tiered_lessons_path(tier), json.dumps(asdict(tl)))


def _reinforce_tiered_lesson(tl: TieredLesson, *, tier: str) -> TieredLesson:
    """Reinforce an existing lesson: bump score and sessions_validated, rewrite file.

    ``tl.score`` is expected to be the *effective* (decay-derived) score —
    reinforcement re-anchors it: score = effective + bonus, anchor = today.

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
    # Replace the mutated lesson under the lock (raw stored scores for all
    # bystanders — a non-raw load would persist decay, compounding on each
    # write; an unlocked load would drop concurrent updates).
    _mutate_tiered_lessons(
        tier, lambda all_lessons: [tl if l.lesson_id == tl.lesson_id else l for l in all_lessons],
    )
    return _post_reinforce_hooks(tl, tier=tier)


def _post_reinforce_hooks(tl: TieredLesson, *, tier: str) -> TieredLesson:
    """Re-confirmation side effects (session 40, M2). Never raises.

    MEDIUM — promote the moment eligibility is met. Reinforcement re-anchors
    the score to today, and a single day of decay (1.0 * 0.85) already falls
    below PROMOTE_MIN_SCORE (0.9) — so the daily consolidation cycle can only
    ever promote lessons reinforced that same day. Promotion has to happen
    here, at reinforcement time; the consolidation-cycle check remains as a
    backstop.

    LONG — a re-confirmed permanent lesson is a repeated pattern observation.
    Feed observe_pattern so hypotheses accrue confirmations and standing
    rules accrete. promote_lesson seeds the first observation; without this
    hook nothing ever confirms a hypothesis, so standing_rules.jsonl never
    grows.
    """
    if tier == MemoryTier.MEDIUM:
        if tl.score >= PROMOTE_MIN_SCORE and tl.sessions_validated >= PROMOTE_MIN_SESSIONS:
            try:
                if promote_lesson(tl.lesson_id):
                    tl.tier = MemoryTier.LONG
            except Exception as exc:
                log.warning("promotion-at-reinforcement failed for %s: %s", tl.lesson_id, exc)
    elif tier == MemoryTier.LONG:
        try:
            from knowledge_lens import observe_pattern
            observe_pattern(tl.lesson, tl.task_type or "", source_lesson_id=tl.lesson_id)
        except Exception as exc:
            log.warning("observe_pattern at reinforcement failed for %s: %s", tl.lesson_id, exc)
    return tl


def load_tiered_lessons(
    tier: str,
    *,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    min_score: float = 0.0,
    limit: Optional[int] = 50,
    max_age_days: Optional[int] = None,
    raw: bool = False,
) -> List[TieredLesson]:
    """Load tiered lessons from disk, applying current-day decay inline.

    Decay is a *read-time derivation*: the stored score is the score as of
    ``last_reinforced`` (the anchor), and the effective score is computed
    here as ``stored * DECAY_FACTOR^days``. Stored scores must never be
    overwritten with decayed values — that would compound decay on every
    rewrite. Only MEDIUM decays; LONG is promoted-permanent by design.

    Args:
        lesson_type:  If set, only return lessons with this lesson_type
                      (Phase 59 NeMo S1 typed taxonomy filter).
        limit:        Max results (None = unlimited — required for any
                      read-modify-write that rewrites the file, otherwise
                      the rewrite silently truncates the store).
        max_age_days: If set, skip lessons last reinforced more than this many days ago.
                      Useful for pruning stale lessons in retrieval contexts.
        raw:          Skip decay derivation and return stored scores as-is.
                      Use for read-modify-write paths that persist records.
    """
    path = _tiered_lessons_path(tier)
    if not path.exists():
        return []

    results: List[TieredLesson] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                tl = TieredLesson(**{k: d[k] for k in TieredLesson.__dataclass_fields__ if k in d})
                days = _days_since(tl.last_reinforced)
                if max_age_days is not None and days > max_age_days:
                    continue  # lesson too stale
                # Derive effective score (MEDIUM only — LONG does not decay)
                if not raw and tier == MemoryTier.MEDIUM and days > 0:
                    tl.score = decay_score(tl.score, days)
                if not raw and tl.score < min_score:
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
    return results[:limit] if limit is not None else results


def _rewrite_tiered_lessons(tier: str, lessons: Optional[List[TieredLesson]] = None) -> None:
    """Rewrite the tiered lessons file with the current state (after updates/GC).

    When no lesson list is supplied, reloads RAW and unlimited — persisting
    decay-derived scores or a truncated load would corrupt the store.

    Passing an explicit ``lessons`` list is only safe if that list was built
    INSIDE this file's lock — a list from an unlocked read silently drops
    concurrent writers' updates. Mutations should use _mutate_tiered_lessons.
    """
    path = _tiered_lessons_path(tier)
    from file_lock import locked_write, atomic_write
    with locked_write(path):
        # Reload INSIDE the lock — reloading before acquisition raced a
        # concurrent writer (its lessons landed between our read and write
        # and were silently dropped).
        if lessons is None:
            lessons = load_tiered_lessons(tier=tier, min_score=0.0, limit=None, raw=True)
        atomic_write(path, "".join(json.dumps(asdict(tl)) + "\n" for tl in lessons))


def _mutate_tiered_lessons(tier: str, mutate) -> None:
    """Read-modify-write the tier's store safely: reload RAW + unlimited
    INSIDE the lock, apply ``mutate(lessons) -> lessons``, write while still
    holding it. This is the only safe shape for lesson mutations — callers
    that loaded a list unlocked and passed it to _rewrite_tiered_lessons
    were losing concurrent reinforcements/promotions.
    """
    path = _tiered_lessons_path(tier)
    from file_lock import locked_write, atomic_write
    with locked_write(path):
        lessons = load_tiered_lessons(tier=tier, min_score=0.0, limit=None, raw=True)
        lessons = mutate(lessons)
        atomic_write(path, "".join(json.dumps(asdict(tl)) + "\n" for tl in lessons))


# ---------------------------------------------------------------------------
# Lesson archive (retention decree, 2026-07-10)
# ---------------------------------------------------------------------------
# "Decay trust, never data": GC and forget move lessons OUT of the live
# store but never destroy them. The archive is an append-only JSONL log —
# a lesson removed and later re-archived simply gets a second record.

def _lessons_archive_path() -> Path:
    return _memory_dir() / "lessons_archive.jsonl"


def _archive_lessons(lessons: List[TieredLesson], *, reason: str) -> None:
    """Append lessons to the archive before they leave the live store.

    reason: "decay_gc" (system GC — eligible for graveyard resurrection)
            or "user_forget" (explicit user removal — never auto-resurrected).
    """
    if not lessons:
        return
    from file_lock import locked_append
    path = _lessons_archive_path()
    now = datetime.now(timezone.utc).isoformat()
    for tl in lessons:
        rec = asdict(tl)
        rec["archived_at"] = now
        rec["archived_reason"] = reason
        locked_append(path, json.dumps(rec))


def _load_archived_lessons(*, reasons: tuple = ("decay_gc",)) -> List[TieredLesson]:
    """Load archived lessons whose archive reason is in *reasons*.

    Returns the newest archive record per lesson_id, skipping records that
    can't be parsed. Archive-only view — callers must exclude ids that are
    currently live if they merge the two.
    """
    path = _lessons_archive_path()
    if not path.exists():
        return []
    field_names = {f.name for f in fields(TieredLesson)}
    by_id: Dict[str, TieredLesson] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("archived_reason") not in reasons:
                    # A later user_forget overrides an earlier decay_gc record
                    by_id.pop(rec.get("lesson_id", ""), None)
                    continue
                tl = TieredLesson(**{k: v for k, v in rec.items() if k in field_names})
                by_id[tl.lesson_id] = tl  # newest record wins (file is append-order)
            except Exception:
                continue
    except Exception:
        return []
    return list(by_id.values())


def resurrect_archived_lesson(lesson_id: str) -> Optional[TieredLesson]:
    """Restore a system-archived (decay_gc) lesson to its live tier store.

    The archive record is left in place — it's history. Restores with
    last_reinforced=today so decay restarts from now. No-op (returns None)
    if the lesson is already live or was user-forgotten.
    """
    match = next((tl for tl in _load_archived_lessons()
                  if tl.lesson_id == lesson_id), None)
    if match is None:
        return None
    tier = match.tier or MemoryTier.MEDIUM
    live = load_tiered_lessons(tier=tier, min_score=0.0, limit=None, raw=True)
    if any(l.lesson_id == lesson_id for l in live):
        return None
    match.last_reinforced = _current_date()
    match.times_reinforced += 1
    from file_lock import locked_append
    locked_append(_tiered_lessons_path(tier), json.dumps(asdict(match)))
    return match


# ---------------------------------------------------------------------------
# Reinforce, forget, promote
# ---------------------------------------------------------------------------

def reinforce_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> Optional[TieredLesson]:
    """Find lesson by ID in the given tier and reinforce it (score + sessions).

    Phase 59 Feynman F5: once sessions_validated reaches 3, confidence is
    promoted to >= _CONFIDENCE_MULTI_SESSION (0.9).

    Session 40 M2: reinforcement triggers _post_reinforce_hooks — an eligible
    MEDIUM lesson is promoted to LONG immediately (check the returned
    ``.tier``), and a LONG re-confirmation feeds the standing-rule pipeline
    via observe_pattern.
    """
    # Non-raw load: target's effective (decay-derived) score is the
    # reinforcement base. The rewrite inside _reinforce_tiered_lesson
    # reloads raw, so bystander lessons keep their stored scores.
    lessons = load_tiered_lessons(tier=tier, min_score=0.0, limit=None)
    target = next((l for l in lessons if l.lesson_id == lesson_id), None)
    if not target:
        return None
    target = _reinforce_tiered_lesson(target, tier=tier)

    # Captain's log
    try:
        from captains_log import log_event, LESSON_REINFORCED
        log_event(
            event_type=LESSON_REINFORCED,
            subject=lesson_id,
            summary=f"Reinforced (sessions: {target.sessions_validated}, score: {target.score:.2f}): {target.lesson[:80]}",
            context={
                "tier": tier,
                "sessions_validated": target.sessions_validated,
                "score": round(target.score, 3),
                "promoted": target.tier != tier,
            },
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

    The "graveyard" is lessons in the decay band [GC_THRESHOLD, 0.4) — still in the
    live store but below the active-injection threshold (0.3 default in
    inject_lessons) — PLUS lessons the decay GC moved to the archive (retention
    decree: GC archives, never deletes). Live matches are recoverable via
    ``reinforce_lesson()``; archived matches via ``resurrect_archived_lesson()``.

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
    live_ids: set = set()

    for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
        lessons = load_tiered_lessons(tier=tier, min_score=min_score)
        live_ids.update(tl.lesson_id for tl in lessons)
        for tl in lessons:
            if tl.score >= max_score:
                continue
            text = tl.lesson.lower()
            match_ratio = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
            if match_ratio > 0:
                results.append((match_ratio, tl.score, tl))

    # Retention decree: GC'd lessons live on in the archive — the graveyard
    # extends below GC_THRESHOLD now. Archived (decay_gc only; user_forget
    # is deliberately excluded) lessons match the same way; resurrection
    # restores them to their live tier via resurrect_archived_lesson().
    archived_ids: set = set()
    for tl in _load_archived_lessons():
        if tl.lesson_id in live_ids:
            continue
        text = tl.lesson.lower()
        match_ratio = sum(1 for kw in keywords if kw in text) / max(len(keywords), 1)
        if match_ratio > 0:
            archived_ids.add(tl.lesson_id)
            results.append((match_ratio, tl.score, tl))

    results.sort(key=lambda x: (x[0], x[1]), reverse=True)
    matched = [tl for _, _, tl in results[:limit]]

    if resurrect:
        for tl in matched:
            if tl.lesson_id in archived_ids:
                resurrect_archived_lesson(tl.lesson_id)
            else:
                reinforce_lesson(tl.lesson_id, tier=tl.tier)

    return matched


def forget_lesson(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> bool:
    """Remove a lesson from a tier's live store. Returns True if found and removed.

    The lesson is archived (reason="user_forget") rather than destroyed —
    but user-forgotten lessons are excluded from graveyard resurrection, so
    forgetting is final unless the user digs it out of the archive by hand.
    """
    removed = {"hit": False}

    def _drop(lessons: List[TieredLesson]) -> List[TieredLesson]:
        dead = [l for l in lessons if l.lesson_id == lesson_id]
        _archive_lessons(dead, reason="user_forget")
        kept = [l for l in lessons if l.lesson_id != lesson_id]
        removed["hit"] = len(kept) != len(lessons)
        return kept

    _mutate_tiered_lessons(tier, _drop)
    return removed["hit"]


def promote_lesson(lesson_id: str) -> bool:
    """Promote a medium-tier lesson to long-tier.

    Eligibility: effective score >= PROMOTE_MIN_SCORE AND
    sessions_validated >= PROMOTE_MIN_SESSIONS.
    Returns True if promotion succeeded.
    """
    # Eligibility is judged on the effective (decay-derived) score...
    effective = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0, limit=None)
    target = next((l for l in effective if l.lesson_id == lesson_id), None)
    if not target:
        return False
    if target.score < PROMOTE_MIN_SCORE or target.sessions_validated < PROMOTE_MIN_SESSIONS:
        return False
    # ...but the record that moves tiers is the stored (raw) one — popped
    # from MEDIUM under the lock so concurrent updates aren't dropped.
    popped: Dict[str, TieredLesson] = {}

    def _pop(lessons: List[TieredLesson]) -> List[TieredLesson]:
        t = next((l for l in lessons if l.lesson_id == lesson_id), None)
        if t is None:
            return lessons
        popped["t"] = t
        return [l for l in lessons if l.lesson_id != lesson_id]

    _mutate_tiered_lessons(MemoryTier.MEDIUM, _pop)
    target = popped.get("t")
    if target is None:
        return False
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
# Decay cycle (run via maybe_consolidate() or `maro-memory decay`)
# ---------------------------------------------------------------------------

def run_decay_cycle(
    tier: str = MemoryTier.MEDIUM,
    *,
    dry_run: bool = False,
) -> Dict[str, int]:
    """Promote eligible lessons and GC dead ones, judged on effective scores.

    Decay itself is a read-time derivation (see load_tiered_lessons) and is
    never persisted — this cycle's job is the *consequences* of decay:
    promotion (effective score held >= PROMOTE_MIN_SCORE with enough
    validated sessions) and garbage collection (effective score below
    GC_THRESHOLD). The ``decayed`` count is informational: lessons whose
    effective score is currently below their stored score.

    Only MEDIUM has promote/GC semantics; calling with LONG is a no-op
    (long-tier lessons neither decay nor expire by design).

    Returns a dict with counts: decayed, promoted, gc'd.
    """
    if tier != MemoryTier.MEDIUM:
        return {"decayed": 0, "promoted": 0, "gc": 0}

    effective = load_tiered_lessons(tier=tier, min_score=0.0, limit=None)

    decayed = sum(1 for tl in effective if _days_since(tl.last_reinforced) > 0)
    promoted_ids = []
    gc_ids = []

    for tl in effective:
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
                "total": len(effective),
                "decayed": decayed,
                "promoted": len(promoted_ids),
                "gc": len(gc_ids),
                "promoted_ids": promoted_ids,
                "gc_ids": gc_ids,
            }
            from file_lock import locked_append
            locked_append(_cl_path, json.dumps(_cl_entry))
        except Exception:
            pass  # audit trail must never block execution

        # Promote eligible lessons (each promote rewrites the medium file)
        for lid in promoted_ids:
            promote_lesson(lid)

        # GC: archive-then-drop the GC'd ids under the lock (reload happens
        # inside, so the rewrite reflects the promotions above and any
        # concurrent writers). Stored scores stay untouched. Archive happens
        # BEFORE the rewrite so a crash between the two duplicates a lesson
        # (harmless) instead of destroying it (retention decree).
        if gc_ids:
            gc_set = set(gc_ids)

            def _archive_and_drop(lessons: List[TieredLesson]) -> List[TieredLesson]:
                _archive_lessons([l for l in lessons if l.lesson_id in gc_set],
                                 reason="decay_gc")
                return [l for l in lessons if l.lesson_id not in gc_set]

            _mutate_tiered_lessons(tier, _archive_and_drop)

    return {"decayed": decayed, "promoted": len(promoted_ids), "gc": len(gc_ids)}


# ---------------------------------------------------------------------------
# In-process consolidation — the "dream cycle" (session 40)
# ---------------------------------------------------------------------------
# Deliberately NOT a daemon or cron job: consolidation rides along inside
# normal app lifecycle calls (end of handle(), heartbeat ticks, CLI) and
# self-gates via a marker file so it runs at most once per interval no
# matter how many entry points call it. A concurrent double-run is safe:
# decay is read-derived (never persisted), promotion is eligibility-gated
# (second attempt finds the lesson already moved), and GC is idempotent.

CONSOLIDATION_INTERVAL_HOURS = 24.0


def _consolidation_marker_path() -> Path:
    return _memory_dir() / "last_consolidation.json"


def consolidation_due(*, interval_hours: Optional[float] = None) -> bool:
    """True if no consolidation has run within the interval."""
    if interval_hours is None:
        try:
            from config import get as _cfg_get
            interval_hours = float(_cfg_get("memory.consolidation_interval_hours",
                                            CONSOLIDATION_INTERVAL_HOURS))
        except Exception:
            interval_hours = CONSOLIDATION_INTERVAL_HOURS
    marker = _consolidation_marker_path()
    if not marker.exists():
        return True
    try:
        last = json.loads(marker.read_text(encoding="utf-8"))
        last_ts = datetime.fromisoformat(last["ts"])
        elapsed_h = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600.0
        return elapsed_h >= interval_hours
    except Exception:
        return True  # unreadable marker → treat as due


def maybe_consolidate(*, force: bool = False) -> Optional[Dict[str, Any]]:
    """Run memory consolidation if due. The in-process dream cycle.

    Config (workspace-level):
        memory.consolidation_enabled         default True
        memory.consolidation_interval_hours  default 24

    Returns the consolidation summary dict if it ran, None if skipped.
    Never raises — callers sit on the app's exit path.
    """
    try:
        if not force:
            try:
                from config import get as _cfg_get
                if not _cfg_get("memory.consolidation_enabled", True):
                    return None
            except Exception:
                pass  # config unavailable → default enabled
            if not consolidation_due():
                return None

        cycle = run_decay_cycle(tier=MemoryTier.MEDIUM)
        summary: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "medium": cycle,
        }

        # Playbook curation rides the same dream cycle (swarm-review
        # chunk 2): dedup always, size-gated LLM compress; archives the
        # prior version first. Self-caps via this function's interval gate.
        try:
            from playbook import curate_playbook
            _pb_stats = curate_playbook()
            if _pb_stats:
                summary["playbook"] = _pb_stats
        except Exception as _pb_exc:
            log.debug("playbook curation skipped (non-fatal): %s", _pb_exc)

        marker = _consolidation_marker_path()
        marker.write_text(json.dumps(summary), encoding="utf-8")

        try:
            from captains_log import log_event, MEMORY_CONSOLIDATED
            log_event(
                event_type=MEMORY_CONSOLIDATED,
                subject="consolidation",
                summary=(f"Consolidation: decayed={cycle['decayed']} "
                         f"promoted={cycle['promoted']} gc={cycle['gc']}"),
                context=cycle,
            )
        except Exception:
            pass

        return summary
    except Exception as exc:
        log.warning("maybe_consolidate failed (non-fatal): %s", exc)
        return None


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
        # limit=None — rank over the FULL live store (chunk-6 review): the
        # old n*5 cap was applied to a score-sorted load, so a relevant
        # lesson sitting below the top decayed scores was invisible to the
        # ranker. Relevance filtering is the ranker's job; the store stays
        # bounded by decay + GC, not by hiding rows from retrieval.
        pool = load_tiered_lessons(
            tier=tier,
            task_type=task_type,
            lesson_type=lesson_type,
            min_score=min_score,
            limit=None,
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
        # Mutate under the lock, raw + unlimited — the old shape here loaded
        # non-raw with the default limit=50, so each rewrite persisted
        # decay-derived scores AND truncated the store to 50 lessons.
        hit = {"found": False}

        def _bump(lessons: List[TieredLesson]) -> List[TieredLesson]:
            target = next((l for l in lessons if l.lesson_id == lid), None)
            if target is not None:
                target.times_applied += 1
                hit["found"] = True
            return lessons

        _mutate_tiered_lessons(tier, _bump)
        if not hit["found"]:
            continue
        # Track task_type diversity in short-term store (session-level aggregator)
        # Persisted canon-tracking uses a separate canon_stats.jsonl
        _record_canon_hit(lid, tier=tier, task_type=task_type)


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
    from file_lock import locked_append
    locked_append(path, json.dumps(entry))


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


# ===========================================================================
# Phase K2: Knowledge Nodes — Structured, Queryable Knowledge
# ===========================================================================
#
# Knowledge nodes are the building blocks of the Web (associative) view.
# Each node represents a reusable piece of knowledge (principle, pattern,
# technique, tool, decision) with evidence tracing and temporal metadata.
#
# Schema designed for:
#   - Import from external collections (links, research, steal-list items)
#   - LLM-assisted extraction (batch-process sources → principle candidates)
#   - Query by domain, type, or goal-relevance (TF-IDF ranked)
#   - Injection into decompose/evolver context alongside tiered lessons
#   - Provenance: every node traces to ≥1 source
# ===========================================================================

# Node types — what kind of knowledge this represents
NODE_TYPES = frozenset({
    "principle",      # Reusable design/engineering principle
    "pattern",        # Recurring solution pattern (like a design pattern)
    "technique",      # Specific approach or method
    "tool",           # External tool, library, or service
    "insight",        # Observation or finding (less prescriptive than principle)
    "decision",       # Architectural decision record (ADR-style)
    "concept",        # Core concept definition (lat.md-style)
})

# Node statuses
NODE_ACTIVE = "active"
NODE_SUPERSEDED = "superseded"
NODE_DEPRECATED = "deprecated"
NODE_CANDIDATE = "candidate"     # Not yet validated


@dataclass
class KnowledgeNode:
    """A single unit of structured knowledge in the Web layer.

    Every node has provenance (sources), domain tags, and temporal metadata.
    Nodes can link to each other via wiki-links ([[concept-name]]) in their
    description field, matching the lat.md convention.
    """
    node_id: str                       # Unique identifier (uuid hex[:12])
    node_type: str                     # One of NODE_TYPES
    title: str                         # Short descriptive title
    description: str                   # Full text, may contain [[wiki-links]]
    domain: str = ""                   # Domain tag (e.g., "orchestration", "memory", "quality")
    sources: List[str] = field(default_factory=list)   # URLs, file paths, outcome IDs
    tags: List[str] = field(default_factory=list)       # Freeform tags for filtering
    status: str = NODE_ACTIVE
    confidence: float = 0.5            # How validated is this knowledge (0-1)
    times_applied: int = 0             # How often injected into context
    superseded_by: Optional[str] = None  # node_id of replacement (if superseded)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    validated_at: Optional[str] = None   # Last validation timestamp
    author: str = ""                   # Who contributed this (handle, system, etc.)


@dataclass
class KnowledgeEdge:
    """A directed relationship between two knowledge nodes."""
    source_id: str                     # From node
    target_id: str                     # To node
    relation: str                      # "supports", "contradicts", "extends", "implements", "related"
    weight: float = 1.0                # Relationship strength (0-1)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _knowledge_nodes_path() -> Path:
    return _memory_dir() / "knowledge_nodes.jsonl"


def _knowledge_edges_path() -> Path:
    return _memory_dir() / "knowledge_edges.jsonl"


def append_knowledge_node(node: KnowledgeNode) -> None:
    """Append a knowledge node to the store."""
    p = _knowledge_nodes_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    from file_lock import locked_append
    locked_append(p, json.dumps(asdict(node), sort_keys=True))
    log.info("knowledge_node: added %s (%s) %r", node.node_id, node.node_type, node.title[:60])


def append_knowledge_edge(edge: KnowledgeEdge) -> None:
    """Append a knowledge edge to the store."""
    p = _knowledge_edges_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    from file_lock import locked_append
    locked_append(p, json.dumps(asdict(edge), sort_keys=True))


def load_knowledge_nodes(
    *,
    node_type: Optional[str] = None,
    domain: Optional[str] = None,
    status: Optional[str] = NODE_ACTIVE,
    tag: Optional[str] = None,
) -> List[KnowledgeNode]:
    """Load knowledge nodes with optional filtering."""
    p = _knowledge_nodes_path()
    if not p.exists():
        return []

    nodes: List[KnowledgeNode] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if status and d.get("status", NODE_ACTIVE) != status:
                continue
            if node_type and d.get("node_type") != node_type:
                continue
            if domain and d.get("domain", "") != domain:
                continue
            if tag and tag not in d.get("tags", []):
                continue
            nodes.append(KnowledgeNode(**{
                k: v for k, v in d.items()
                if k in KnowledgeNode.__dataclass_fields__
            }))
        except (json.JSONDecodeError, TypeError):
            continue
    return nodes


def load_knowledge_edges(*, node_id: Optional[str] = None) -> List[KnowledgeEdge]:
    """Load knowledge edges, optionally filtered by source or target node."""
    p = _knowledge_edges_path()
    if not p.exists():
        return []

    edges: List[KnowledgeEdge] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if node_id and d.get("source_id") != node_id and d.get("target_id") != node_id:
                continue
            edges.append(KnowledgeEdge(**{
                k: v for k, v in d.items()
                if k in KnowledgeEdge.__dataclass_fields__
            }))
        except (json.JSONDecodeError, TypeError):
            continue
    return edges


def find_knowledge_node(node_id: str) -> Optional[KnowledgeNode]:
    """Find a single node by ID."""
    for node in load_knowledge_nodes(status=""):  # all statuses
        if node.node_id == node_id:
            return node
    return None


# ---------------------------------------------------------------------------
# Query — TF-IDF ranked retrieval
# ---------------------------------------------------------------------------

def query_knowledge(
    goal: str,
    *,
    domain: Optional[str] = None,
    node_type: Optional[str] = None,
    max_results: int = 5,
    min_confidence: float = 0.0,
) -> List[KnowledgeNode]:
    """Query knowledge nodes by goal relevance (TF-IDF ranked).

    Returns the most relevant active nodes for a given goal/query string.
    """
    nodes = load_knowledge_nodes(domain=domain, node_type=node_type)
    if not nodes:
        return []

    # Filter by confidence
    nodes = [n for n in nodes if n.confidence >= min_confidence]
    if not nodes:
        return []

    # Build corpus for TF-IDF
    goal_tokens = _tokenize(goal)
    if not goal_tokens:
        return nodes[:max_results]

    scored: List[tuple] = []
    for node in nodes:
        doc = f"{node.title} {node.description} {' '.join(node.tags)}"
        doc_tokens = _tokenize(doc)
        if not doc_tokens:
            continue
        # Simple TF-IDF score
        tf = Counter(doc_tokens)
        doc_len = len(doc_tokens)
        score = 0.0
        for token in goal_tokens:
            if token in tf:
                score += tf[token] / doc_len
        # Boost by confidence and application count
        score *= (0.5 + 0.5 * node.confidence)
        if node.times_applied > 0:
            score *= 1.0 + 0.1 * min(node.times_applied, 5)
        scored.append((score, node))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [node for _, node in scored[:max_results]]


# ---------------------------------------------------------------------------
# Injection — format knowledge for context injection
# ---------------------------------------------------------------------------

def inject_knowledge_for_goal(
    goal: str,
    *,
    domain: Optional[str] = None,
    max_chars: int = 1200,
    max_nodes: int = 5,
) -> str:
    """Build a knowledge injection string for a goal.

    Returns a formatted block of the most relevant knowledge nodes,
    suitable for prepending to decompose/evolver context.
    """
    nodes = query_knowledge(goal, domain=domain, max_results=max_nodes, min_confidence=0.3)
    if not nodes:
        return ""

    lines: List[str] = ["## Relevant Knowledge"]
    chars = 0
    for node in nodes:
        entry = f"- [{node.node_type}] {node.title}: {node.description[:200]}"
        if node.sources:
            entry += f" (source: {node.sources[0][:60]})"
        if chars + len(entry) > max_chars:
            break
        lines.append(entry)
        chars += len(entry)
        # Track application
        node.times_applied += 1

    return "\n".join(lines) if len(lines) > 1 else ""


# ---------------------------------------------------------------------------
# Wiki-link extraction — parse [[concept]] references from node descriptions
# ---------------------------------------------------------------------------

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def extract_wiki_links(text: str) -> List[str]:
    """Extract [[wiki-link]] references from text."""
    return _WIKI_LINK_RE.findall(text)


def build_wiki_link_edges(nodes: List[KnowledgeNode]) -> List[KnowledgeEdge]:
    """Build edges from wiki-links in node descriptions.

    If node A's description references [[concept-B]] and a node with
    title matching "concept-B" exists, create a "related" edge A→B.
    """
    title_to_id: Dict[str, str] = {}
    for node in nodes:
        # Normalize title for matching: lowercase, hyphens/spaces equivalent
        key = node.title.lower().replace(" ", "-").replace("_", "-")
        title_to_id[key] = node.node_id

    edges: List[KnowledgeEdge] = []
    for node in nodes:
        refs = extract_wiki_links(node.description)
        for ref in refs:
            ref_key = ref.lower().replace(" ", "-").replace("_", "-")
            target_id = title_to_id.get(ref_key)
            if target_id and target_id != node.node_id:
                edges.append(KnowledgeEdge(
                    source_id=node.node_id,
                    target_id=target_id,
                    relation="related",
                ))
    return edges


# ---------------------------------------------------------------------------
# K2 link-farm import
# ---------------------------------------------------------------------------

# Map link-farm topics to KnowledgeNode domain tags
_TOPIC_TO_DOMAIN = {
    "agent-design": "orchestration",
    "dev-practices": "engineering",
    "claude-code": "tooling",
    "skills-mcp": "tooling",
    "prompting": "engineering",
    "research": "research",
    "management": "strategy",
    "industry": "research",
    "general": "general",
}

# Map link-farm topics to KnowledgeNode node_type
_TOPIC_TO_NODE_TYPE = {
    "agent-design": "pattern",
    "dev-practices": "technique",
    "claude-code": "tool",
    "skills-mcp": "tool",
    "prompting": "technique",
    "research": "insight",
    "management": "principle",
    "industry": "insight",
    "general": "insight",
}


def import_link_farm(
    posts: list,
    *,
    min_priority: str = "long-term",
    only_enriched: bool = True,
    dry_run: bool = False,
) -> dict:
    """Import enriched posts from slycrel/link-farm into the knowledge node store.

    Args:
        posts: List of post dicts from posts_final_v3.json.
        min_priority: Minimum priority to import ("near-term" | "medium-term" | "long-term").
        only_enriched: Skip posts where enriched=False (no content yet).
        dry_run: If True, return stats without writing anything.

    Returns:
        Dict with keys: added, skipped_dup, skipped_unenriched, skipped_priority, total.
    """
    import hashlib

    _PRIORITY_ORDER = {"near-term": 0, "medium-term": 1, "long-term": 2}
    min_rank = _PRIORITY_ORDER.get(min_priority, 2)

    # Build URL dedup set from existing nodes (all statuses — candidates count as dups)
    existing = load_knowledge_nodes(status=None)
    existing_sources: set = set()
    for n in existing:
        existing_sources.update(n.sources)

    stats = {
        "added": 0,
        "skipped_dup": 0,
        "skipped_unenriched": 0,
        "skipped_priority": 0,
        "total": len(posts),
    }

    for post in posts:
        url = post.get("url", "")
        if url in existing_sources:
            stats["skipped_dup"] += 1
            continue

        if only_enriched and not post.get("enriched", False):
            stats["skipped_unenriched"] += 1
            continue

        priority = post.get("priority", "long-term")
        if _PRIORITY_ORDER.get(priority, 2) > min_rank:
            stats["skipped_priority"] += 1
            continue

        topics = post.get("topics", ["general"])
        primary_topic = topics[0] if topics else "general"
        domain = _TOPIC_TO_DOMAIN.get(primary_topic, "general")
        node_type = _TOPIC_TO_NODE_TYPE.get(primary_topic, "insight")

        # Build description from summary + content excerpt
        summary = post.get("summary", "")
        content = post.get("content", "")
        description = summary
        if content and len(content) > len(summary):
            # Append first ~600 chars of full content if richer than summary
            extra = content[:600].strip()
            if extra and extra not in summary:
                description = f"{summary}\n\n{extra}"

        # Stable node_id from URL hash
        node_id = hashlib.sha256(url.encode()).hexdigest()[:12]

        # Title: use subject if it's not the generic "Post by X on X" pattern,
        # otherwise fall back to summary first sentence
        subject = post.get("subject", "")
        if subject and "Post by" not in subject and "on X" not in subject:
            title = subject[:120]
        elif summary:
            first_sentence = summary.split(".")[0].strip()
            title = first_sentence[:120] if first_sentence else subject[:120] or "Untitled"
        else:
            title = url[:80]

        node = KnowledgeNode(
            node_id=node_id,
            node_type=node_type,
            title=title,
            description=description[:2000],
            domain=domain,
            sources=[url],
            tags=topics,
            status=NODE_CANDIDATE,  # imported nodes start as candidates — not yet validated
            confidence=0.4,         # external source, unverified
            author=post.get("handle", post.get("author", "link-farm")),
        )

        if not dry_run:
            append_knowledge_node(node)
            existing_sources.add(url)  # prevent within-batch duplicates

        stats["added"] += 1

    log.info(
        "import_link_farm: added=%d skipped_dup=%d skipped_unenriched=%d total=%d",
        stats["added"], stats["skipped_dup"], stats["skipped_unenriched"], stats["total"],
    )
    return stats
