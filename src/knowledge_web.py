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
    from hybrid_search import hybrid_rank_scored as _hybrid_rank_scored
    _USE_HYBRID = True
except ImportError:  # pragma: no cover
    _USE_HYBRID = False


def ranker_name() -> str:
    """Which ranker family query_lessons uses — "hybrid" (BM25+RRF) or
    "tfidf". Camera-frame logging records this so logged scores stay
    interpretable (the two families are on different scales)."""
    return "hybrid" if _USE_HYBRID else "tfidf"

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
PROVISIONAL_ENTRY_SCORE = 0.6  # entry score for provisional (step-verified) lessons —
                               # below the confirmed 1.0 floor, so decay disposes of an
                               # unconfirmed one in ~1 week instead of ~2
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
    # Per-step learning (2026-07-27): True for lessons extracted from
    # individually-verified steps of a run whose run-level outcome failed the
    # learnability gate. Provisional lessons are excluded from every injection
    # surface (query_lessons, inject_tiered_lessons, memory_bridge ingest) and
    # from LONG promotion until a confirmed-context re-record clears the flag
    # (promote-on-evidence); decay disposes of the unconfirmed rest.
    provisional: bool = False
    # Provenance gate (2026-07-29, lesson_provenance.py): "" (legacy pre-gate
    # rows, treated as outcome-derived) | "outcome" | "prompt". "prompt" marks
    # a lesson generalized from dispatch-prompt instruction text rather than
    # from an observed outcome — quarantined from every injection surface and
    # from LONG promotion (same surfaces as provisional), visible in readouts,
    # cleared only by an outcome-derived confirming re-record.
    minted_from: str = ""
    # Retirement-by-contradiction (2026-08-02): empty dict = full citizen;
    # non-empty = contested ({reason, source, contested_at}) — the lesson was
    # named by contradiction adjudication or operator judgment as plausibly
    # wrong. Contested rows leave every injection surface (same set as
    # provisional/quarantined) and never promote; reinforcement bumps score
    # but is never confirming (sessions_validated frozen, flags never clear).
    # This is the only retirement path for LONG rows, which don't decay.
    # Sticky by design — no un-contest verb until a lesson-refight slice
    # exists (mirrors refight_rule). Old rows deserialize to {}.
    contested: Dict[str, Any] = field(default_factory=dict)
    # Δ-gate (2026-08-06, delta_replay.py): replay-measured effect evidence
    # when this lesson promoted by effect rather than tenure — {delta,
    # jackknife_spread, n_calls, stratum, measured_at, route: "effect"}.
    # Empty dict on tenure-promoted and unmeasured rows. Old rows
    # deserialize to {}.
    delta_evidence: Dict[str, Any] = field(default_factory=dict)
    # Mint-time grounding (2026-08-06, mint_grounding.py): receipt stamps
    # joining this lesson's method claims against the minting run's tool
    # events — [{claim, family, status: supported|unsupported|unprobed,
    # receipts, note?}]. Annotation only, fail-open: consumers weigh it
    # (injection marker, seed-reader skip); nothing here blocks a mint.
    # Empty = no parseable claims OR minted before grounding existed.
    grounding: List[Dict[str, Any]] = field(default_factory=list)


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


def _is_quarantined(tl: "TieredLesson") -> bool:
    """Prompt-derived lessons are quarantined from every injection surface
    and from promotion (lesson_provenance gate). Legacy ""/"outcome" rows
    are full citizens."""
    return tl.minted_from == "prompt"


def _is_contested(tl: "TieredLesson") -> bool:
    """Contested lessons (retirement-by-contradiction) leave every injection
    surface and never promote or confirm. The dict carries the audit trail
    (reason/source/contested_at); emptiness is the flag."""
    return bool(tl.contested)


def _is_delta_demoted(tl: "TieredLesson") -> bool:
    """Measured-negative lessons (Δ-gate demotion route, 2026-08-08) leave
    the tiered-lessons injection surface and never ride tenure to LONG.
    Surface-scoped by decree: the flat ledger, query_lessons, and extraction
    are untouched — a negative Δ on decision replays only demotes from
    decision injection; other surfaces need their own reward design. Unlike
    contest/quarantine, the stamp is a measurement: a later replay that
    clears the promote bar replaces it (measurement replaces measurement)."""
    try:
        return (tl.delta_evidence or {}).get("route") == "effect-demote"
    except Exception:
        return False


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
    provisional: bool = False,
    minted_from: str = "",
    lesson_id: str = "",
    grounding: Optional[List[Dict[str, Any]]] = None,
    source_evidence: str = "",
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
    Per-step learning (2026-07-27): ``provisional=True`` records the lesson at
        a reduced entry score with the provisional flag set (see TieredLesson).
        A provisional recording that dedup-matches an existing lesson
        reinforces it WITHOUT counting as confirmation; a confirmed
        (non-provisional) recording matching an existing provisional lesson
        clears its flag — promote-on-evidence.
    Provenance gate (2026-07-29): ``minted_from`` is classified here at the
        choke point when the caller leaves it "" — every mint path (reflect,
        deferred, per-step, evolver, prereq, CLI) is covered without
        call-site churn; an explicit caller value is trusted. A
        prompt-derived recording that dedup-matches an existing lesson is
        IGNORED (no reinforcement, no confirmation, no flag-clearing):
        instruction text must not move persistent state.
    Mint-time grounding (2026-08-06): ``grounding`` carries the caller's
        claim-receipt stamps (mint_grounding.ground_lessons_for_run).
        Fresh mints only — the reinforce path returns the existing row
        with its original stamps, whose receipts point at the run that
        actually minted the text.
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

    # Provenance gate: classify at the choke point, on the FULL source_goal.
    # Callers must not pre-truncate — scaffolding past char 120 of a goal
    # would starve the echo signal (adversarial review 2026-07-29); the row
    # itself stores the conventional 120-char excerpt below. Never blocks
    # recording.
    if not minted_from:
        try:
            from lesson_provenance import (classify_lesson_provenance,
                                           provenance_gate_enabled)
            if provenance_gate_enabled():
                minted_from = classify_lesson_provenance(
                    lesson_text, source_goal, source_evidence)
        except Exception:
            minted_from = ""

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
                if minted_from == "prompt":
                    # Least-privilege: an instruction-derived re-record must
                    # not reinforce, confirm, or clear anything.
                    log.info("prompt-derived re-record of %s ignored "
                             "(provenance gate)", ex.lesson_id)
                    return ex
                return _reinforce_tiered_lesson(
                    ex, tier=MemoryTier.LONG, confirming=not provisional,
                    incoming_minted_from=minted_from,
                    incoming_evidence=evidence_sources)
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
                if minted_from == "prompt":
                    # Same least-privilege rule as the LONG pre-scan above.
                    log.info("prompt-derived re-record of %s ignored "
                             "(provenance gate)", ex.lesson_id)
                    return ex
                return _reinforce_tiered_lesson(
                    ex, tier=tier, confirming=not provisional,
                    incoming_minted_from=minted_from,
                    incoming_evidence=evidence_sources)
            max_sim = max(max_sim, sim)

        # Chunk 6: novelty term — a lesson unlike anything stored starts above
        # 1.0 so it survives decay long enough to be tested; repeat-shaped
        # lessons keep the classic 1.0. Counteracts the reinforce-the-familiar
        # bias (+0.3 for repeats while novel one-offs die in ~7 days).
        # Promotion is unaffected — sessions_validated still gates.
        # Killswitch: knowledge.novelty_term_enabled.
        novelty = 1.0 - max_sim
        # Provisional lessons enter below the confirmed 1.0 floor: the same
        # novelty bonus applies (a novel provisional method observation still
        # deserves its testing window), but the ceiling (0.9) stays under the
        # confirmed floor so a provisional row can never outrank a confirmed
        # one at equal age.
        # Quarantined (prompt-derived) rows enter at the same reduced base:
        # they are never injected or reinforced, so decay disposes of them
        # in about a week unless an outcome-derived re-record clears them.
        base = (PROVISIONAL_ENTRY_SCORE
                if (provisional or minted_from == "prompt") else 1.0)
        score = base
        if _novelty_term_enabled():
            score = base + NOVELTY_BONUS * novelty

        # UU-4: shared-id support for dual-writing callers — fresh mints only;
        # the near-duplicate reinforce path returns the existing row with its
        # original id (see memory_ledger._store_lesson for the same contract).
        tl = TieredLesson(
            lesson_id=lesson_id or str(uuid.uuid4())[:8],
            task_type=task_type,
            outcome=outcome,
            lesson=lesson_text,
            # Row stores the conventional 120-char excerpt; classification
            # above already saw the full goal.
            source_goal=source_goal[:120],
            confidence=confidence,
            tier=tier,
            score=score,
            last_reinforced=_current_date(),
            acquired_for=acquired_for,
            evidence_sources=evidence_sources or [],
            lesson_type=lesson_type if lesson_type in _LESSON_TYPES else "",
            novelty=round(novelty, 4),
            provisional=provisional,
            minted_from=minted_from,
            grounding=grounding or [],
        )
        _append_tiered_lesson(tl, tier=tier)
        if minted_from == "prompt":
            log.info("lesson %s quarantined at mint (prompt-derived): %s",
                     tl.lesson_id, lesson_text[:80])

    # Captain's log
    try:
        from captains_log import log_event, LESSON_RECORDED
        log_event(
            event_type=LESSON_RECORDED,
            subject=tl.lesson_id,
            summary=f"New {tier} lesson (confidence: {confidence:.2f}): {lesson_text[:100]}",
            context={"tier": tier, "task_type": task_type, "confidence": confidence,
                     "lesson_type": lesson_type, "novelty": tl.novelty, "score": score,
                     "minted_from": minted_from},
        )
    except Exception:
        pass

    return tl


def _append_tiered_lesson(tl: TieredLesson, *, tier: str) -> None:
    from file_lock import locked_append
    locked_append(_tiered_lessons_path(tier), json.dumps(asdict(tl)))


_REINFORCE_EVIDENCE_CAP = 8  # distinct evidence refs kept per row (first N sightings)


def _reinforce_tiered_lesson(tl: TieredLesson, *, tier: str,
                             confirming: bool = True,
                             incoming_minted_from: str = "",
                             incoming_evidence: Optional[List[str]] = None) -> TieredLesson:
    """Reinforce an existing lesson: bump score and sessions_validated, rewrite file.

    ``tl.score`` is expected to be the *effective* (decay-derived) score —
    reinforcement re-anchors it: score = effective + bonus, anchor = today.

    Phase 59 Feynman F5: once sessions_validated reaches 3, confidence is bumped
    to _CONFIDENCE_MULTI_SESSION (0.9+) — independently confirmed across sessions.

    Per-step learning (2026-07-27): ``confirming=False`` marks a reinforcement
    arriving from a provisional context (a step-verified extraction on a
    non-learnable run). It bumps score — the observation is real — but it is
    NOT validation: sessions_validated (the promotion/confidence counter)
    only moves on confirmed-context reinforcement, and the provisional flag
    only clears on one (promote-on-evidence). Without that split, a lesson
    sighted in three failed runs would carry promotion-grade
    sessions_validated while hidden, and its first confirmation would
    promote it to LONG immediately (adversarial review 2026-07-27).
    """
    # `tl` is the CALLER's copy, loaded before the lock (record_tiered_lesson's
    # dedup scan hands it straight here). Everything below therefore runs
    # against the row as it is on disk right now, re-read inside the lock —
    # only the decay-derived score and the incoming evidence come from `tl`.
    # Writing the caller's copy back wholesale reverted whatever had changed
    # on that row in between, and read its own flags off the stale copy: a
    # lesson contested mid-flight was both credited with a confirmation and
    # silently un-contested (repro'd 2026-08-04). Bystanders were always
    # reloaded fresh; the target row was the one row that wasn't.
    effective_score = tl.score
    lesson_id = tl.lesson_id
    result: Dict[str, Any] = {"tl": None}

    def _apply(all_lessons: List[TieredLesson]) -> List[TieredLesson]:
        row = next((l for l in all_lessons if l.lesson_id == lesson_id), None)
        if row is None:
            # GC'd or promoted out between the caller's load and now. Same
            # outcome as before: nothing to reinforce, nothing written back.
            return all_lessons
        # Retirement-by-contradiction: a contested lesson may still be
        # re-sighted (dedup re-records land here). The sighting is counted
        # (times_reinforced — honest evidence for a future refight) but
        # nothing else moves: no score bump, no decay re-anchor (or a
        # frequently re-derived contested row would never decay out —
        # retirement is the point), no confirmation (sessions_validated
        # frozen, no flag clears — otherwise a contested lesson could launder
        # itself back to citizenship through the same duplicate-write path
        # that made it look validated).
        contested_hit = _is_contested(row)
        confirms = confirming and not contested_hit
        if confirms and row.provisional:
            row.provisional = False
            log.info("provisional lesson %s confirmed by a learnable-context re-record",
                     row.lesson_id)
        if confirms and incoming_minted_from == "outcome" and row.minted_from == "prompt":
            # An outcome-derived confirming re-record means the same knowledge
            # was independently derived from an actual outcome — it earns
            # citizenship (mirrors the provisional promote-on-evidence clear).
            # Prompt-derived re-records never reach here: record_tiered_lesson
            # returns early on their dedup match. The incoming record must be
            # affirmatively outcome-classified: an unclassified "" (gate off,
            # or the applied-reinforcement path) must not clear quarantine —
            # otherwise disabling the killswitch re-arms stamped rows via the
            # next duplicate write (adversarial review 2026-07-29).
            row.minted_from = "outcome"
            log.info("quarantined lesson %s cleared by an outcome-derived re-record",
                     row.lesson_id)
        if not contested_hit:
            # The caller's score is the effective (decay-derived) one;
            # reinforcement re-anchors it. The stored score on the fresh row
            # is pre-decay, so this figure has to come from `tl`.
            row.score = reinforce_score(effective_score)
            row.last_reinforced = _current_date()
            # What-not-how (2026-08-02): a re-derivation's originating run is
            # the "repeated across runs X, Y" record — merge it (capped) so
            # the row accumulates WHERE it was sighted, not just how often.
            # Contested rows keep only the frozen counter (the refight input).
            for _src in (incoming_evidence or []):
                if len(row.evidence_sources) >= _REINFORCE_EVIDENCE_CAP:
                    break
                if _src and _src not in row.evidence_sources:
                    row.evidence_sources.append(_src)
        if confirms:
            row.sessions_validated += 1
            # F5: multi-session confidence promotion
            if row.sessions_validated >= 3:
                row.confidence = max(row.confidence, _CONFIDENCE_MULTI_SESSION)
        row.times_reinforced += 1
        result["tl"] = row
        return all_lessons

    # Read-modify-write under the lock, raw + unlimited (a non-raw load would
    # persist decay, compounding on each write).
    _mutate_tiered_lessons(tier, _apply)
    return _post_reinforce_hooks(result["tl"] or tl, tier=tier)


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
        # Provisional lessons never promote: LONG is decay-free, so an
        # unconfirmed step-verified observation reaching it would be
        # permanent without ever having been verified in a learnable run.
        if (tl.score >= PROMOTE_MIN_SCORE
                and tl.sessions_validated >= PROMOTE_MIN_SESSIONS
                and not tl.provisional
                and not _is_quarantined(tl)
                and not _is_contested(tl)
                and not _is_delta_demoted(tl)):
            try:
                if promote_lesson(tl.lesson_id):
                    tl.tier = MemoryTier.LONG
            except Exception as exc:
                log.warning("promotion-at-reinforcement failed for %s: %s", tl.lesson_id, exc)
    elif tier == MemoryTier.LONG:
        # A contested LONG lesson must not keep feeding the hypothesis/
        # standing-rule pipeline — that would accrete confirmations from a
        # pattern currently under contradiction.
        if _is_contested(tl):
            return tl
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
            # Provisional, quarantined, and contested lessons are excluded
            # from every injection surface, and resurrection reinforces
            # confirming=True — a topic match is not the learnable-context
            # re-record that may clear any flag (adversarial review
            # 2026-07-27).
            if tl.provisional or _is_quarantined(tl) or _is_contested(tl):
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
        if tl.provisional or _is_quarantined(tl) or _is_contested(tl):
            continue  # same exclusion as the live scan above
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


def set_lesson_minted_from(lesson_id: str, minted_from: str,
                           *, tier: str = MemoryTier.MEDIUM,
                           reason: str = "") -> bool:
    """Stamp minted_from on a stored tiered lesson — the quarantine (and
    unquarantine) maintenance verb. Quarantine keeps the row on disk and
    visible in readouts; it only leaves every injection surface. Rewrites
    under the lock via _mutate_tiered_lessons (raw stored scores preserved).
    Returns True if the lesson was found and stamped.
    """
    if minted_from not in ("", "outcome", "prompt"):
        raise ValueError(f"invalid minted_from: {minted_from!r}")
    hit = {"tl": None}

    def _stamp(lessons: List[TieredLesson]) -> List[TieredLesson]:
        for l in lessons:
            if l.lesson_id == lesson_id:
                l.minted_from = minted_from
                hit["tl"] = l
        return lessons

    _mutate_tiered_lessons(tier, _stamp)
    if hit["tl"] is None:
        return False
    try:
        from captains_log import log_event, LESSON_QUARANTINED
        log_event(
            event_type=LESSON_QUARANTINED,
            subject=lesson_id,
            summary=(f"Lesson {lesson_id} minted_from set to "
                     f"{minted_from or 'unset'}: {hit['tl'].lesson[:100]}"),
            context={"tier": tier, "minted_from": minted_from,
                     "reason": reason},
        )
    except Exception:
        pass
    log.info("set_lesson_minted_from: %s (%s) → %r%s", lesson_id, tier,
             minted_from, f" — {reason}" if reason else "")
    return True


def contest_lesson(lesson_id: str, reason: str, *, source: str,
                   tier: Optional[str] = None) -> bool:
    """Mark a stored lesson contested (retirement-by-contradiction,
    2026-08-02). A contested lesson stays on disk and visible in readouts
    but leaves every injection surface, never promotes, and never confirms
    — for MEDIUM, decay then disposes of it; for decay-free LONG this is
    the retirement mechanism itself.

    Callers: contradiction adjudication (knowledge_lens) when a certified
    failure names a cited lesson, and the operator verb
    (`maro-memory contest`). Sticky by design — no un-contest path until a
    lesson-refight slice exists. Re-sightings via dedup only bump
    times_reinforced (evidence for that future refight); score and the
    decay anchor freeze, so a contested MEDIUM row retires on the decay
    schedule no matter how often it is re-derived.

    Args:
        lesson_id: id of the lesson to contest.
        reason:    why — the contradiction evidence or operator judgment.
        source:    who/what contested it, e.g.
                   "contradiction_adjudication:<loop_id>" or "operator:<who>".
        tier:      search only this tier; default searches MEDIUM then LONG.

    Returns True if the lesson was found and stamped (idempotent: an
    already-contested row keeps its ORIGINAL stamp — first contradiction
    wins the audit trail — but still returns True).
    """
    stamp = {
        "reason": reason[:400],
        "source": source,
        "contested_at": datetime.now(timezone.utc).isoformat(),
    }
    hit: Dict[str, Any] = {"tl": None, "already": False}

    def _stamp(lessons: List[TieredLesson]) -> List[TieredLesson]:
        for l in lessons:
            if l.lesson_id == lesson_id:
                if _is_contested(l):
                    hit["already"] = True
                else:
                    l.contested = stamp
                hit["tl"] = l
        return lessons

    found_tier = ""
    for t in ([tier] if tier else [MemoryTier.MEDIUM, MemoryTier.LONG]):
        _mutate_tiered_lessons(t, _stamp)
        if hit["tl"] is not None:
            found_tier = t
            break

    # UU-4 dual-written rows share a lesson_id across the tiered store and
    # the flat ledger — stamp both so the lesson leaves EVERY injection
    # surface (the flat ledger feeds recall top-up / bootstrap_context
    # independently). Best-effort: a flat miss is normal for tiered-only
    # rows and vice versa.
    flat_hit = False
    try:
        from memory_ledger import contest_flat_lesson
        flat_hit = contest_flat_lesson(lesson_id, stamp)
    except Exception as exc:
        log.warning("contest_lesson: flat-ledger stamp failed for %s: %s",
                    lesson_id, exc)

    if hit["tl"] is None and not flat_hit:
        return False
    if hit["already"]:
        log.info("contest_lesson: %s already contested — original stamp kept",
                 lesson_id)
        return True
    lesson_text = hit["tl"].lesson if hit["tl"] is not None else ""
    try:
        from captains_log import log_event, LESSON_CONTESTED
        log_event(
            event_type=LESSON_CONTESTED,
            subject=lesson_id,
            summary=(f"Lesson {lesson_id} contested ({source}): "
                     f"{lesson_text[:100]}"),
            context={"tier": found_tier, "flat": flat_hit,
                     "reason": stamp["reason"], "source": source},
        )
    except Exception:
        pass
    log.info("contest_lesson: %s (tier=%s flat=%s) contested by %s — %s",
             lesson_id, found_tier or "-", flat_hit, source, reason[:120])
    return True


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
    if target.provisional:
        # Guard at the promotion boundary itself, not only in the callers
        # (_post_reinforce_hooks / run_decay_cycle): the CLI calls this
        # directly, and LONG is decay-free — an unconfirmed row reaching it
        # would be permanent (adversarial review 2026-07-27).
        log.info("promote_lesson: %s is provisional (unconfirmed) — not promoting", lesson_id)
        return False
    if _is_quarantined(target):
        # Same boundary guard: a prompt-derived row reaching decay-free LONG
        # would make the contamination permanent.
        log.info("promote_lesson: %s is quarantined (prompt-derived) — not promoting", lesson_id)
        return False
    if _is_contested(target):
        # Same boundary guard: a contested row reaching decay-free LONG
        # would make a plausibly-wrong lesson permanent.
        log.info("promote_lesson: %s is contested — not promoting", lesson_id)
        return False
    if _is_delta_demoted(target):
        # Same boundary guard: tenure must not launder a measured-negative
        # lesson into decay-free LONG (LONG = always-injected — promotion
        # would undo exactly what the demotion stamp excludes). The effect
        # route can still promote it if a NEW measurement clears the bar.
        log.info("promote_lesson: %s is Δ-demoted (measured negative) — "
                 "not promoting", lesson_id)
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


# Δ-gate effect route (2026-08-06, DELTA_GATE_BUILD_BRIEF §3.3). Thresholds
# validated on the LT-1 arms against pre-registered predictions before this
# route existed: known-effective Δ=+0.59, known-inert Δ=−0.06, rule-stratum
# Δ=−0.15 (record: docs/history/2026-08-06-delta-gate-validation.md). The
# 0.30 default sits between the inert band and the validated effect.
EFFECT_PROMOTE_MIN_DELTA = 0.30
EFFECT_PROMOTE_MIN_CALLS = 6


def effect_promotion_enabled() -> bool:
    """Killswitch for the Δ-gate effect route (default ON — it only acts
    when a replay measurement exists, and measurement is operator/CLI-
    driven spend, not ambient). config: knowledge.effect_promotion_enabled."""
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("knowledge.effect_promotion_enabled", True))
    except Exception:
        return True


def promote_lesson_by_effect(lesson_id: str, delta_evidence: Dict[str, Any]) -> bool:
    """Effect-based route to LONG — tenure unmet is fine; measured Δ is the
    eligibility (DELTA_GATE_BUILD_BRIEF §1: tenure selects for corpus
    agreement, which excludes exactly the blind-spot lessons a Δ measure
    values). The tenure route stays live beside this one.

    `delta_evidence` comes from delta_replay.score_lesson's as_dict():
    delta, jackknife_spread, n_calls, stratum at minimum. Eligibility:
      - killswitch on (knowledge.effect_promotion_enabled)
      - delta >= knowledge.effect_promotion_min_delta (default 0.30)
      - n_calls >= knowledge.effect_promotion_min_calls (default 6)
      - jackknife spread < delta (one call must not own the verdict)
      - stratum == "reason" (rule lessons have Δ≈0 by construction —
        LeAct §6 — and the validation's rule specimen measured NEGATIVE;
        an accidental positive there would be noise)
    plus the same boundary guards as tenure promotion (provisional /
    quarantined / contested rows never reach decay-free LONG).
    """
    if not effect_promotion_enabled():
        log.info("promote_lesson_by_effect: killswitch off — not promoting")
        return False
    try:
        from config import get as _cfg_get
        min_delta = float(_cfg_get("knowledge.effect_promotion_min_delta",
                                   EFFECT_PROMOTE_MIN_DELTA))
        min_calls = int(_cfg_get("knowledge.effect_promotion_min_calls",
                                 EFFECT_PROMOTE_MIN_CALLS))
    except Exception:
        min_delta, min_calls = EFFECT_PROMOTE_MIN_DELTA, EFFECT_PROMOTE_MIN_CALLS

    ev = dict(delta_evidence or {})
    delta = ev.get("delta")
    if not isinstance(delta, (int, float)) or delta < min_delta:
        return False
    if int(ev.get("n_calls") or 0) < min_calls:
        return False
    spread = ev.get("jackknife_spread")
    if not isinstance(spread, (int, float)) or spread >= delta:
        return False
    if ev.get("stratum") != "reason":
        return False

    rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0, limit=None)
    target = next((l for l in rows if l.lesson_id == lesson_id), None)
    if not target:
        return False
    if target.provisional or _is_quarantined(target) or _is_contested(target):
        log.info("promote_lesson_by_effect: %s is provisional/quarantined/"
                 "contested — not promoting", lesson_id)
        return False

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
    target.delta_evidence = {
        "delta": float(delta),
        "jackknife_spread": float(spread),
        "n_calls": int(ev.get("n_calls") or 0),
        "stratum": "reason",
        "measured_at": ev.get("measured_at") or datetime.now(timezone.utc).isoformat(),
        "route": "effect",
    }
    _append_tiered_lesson(target, tier=MemoryTier.LONG)
    log.info("promote_lesson_by_effect: %s → LONG (Δ=%.3f over %d calls)",
             lesson_id, float(delta), int(ev.get("n_calls") or 0))

    try:
        from knowledge_lens import observe_pattern
        observe_pattern(target.lesson, getattr(target, "task_type", "") or "",
                        source_lesson_id=target.lesson_id)
    except Exception:
        pass  # standing-rule pipeline must not block lesson promotion

    return True


# Demotion mirror (2026-08-08, Jeremy's conditional grant met: census round-2
# retest re-measured all three round-1 negatives with the same sign —
# −0.137/−0.059/−0.078). Threshold sits at the edge of the inert band: the
# round-1/round-2 negatives all cleared it, the known-inert specimen (−0.06
# with jackknife 0.09) is jackknife-dominated and does not.
EFFECT_DEMOTE_MAX_DELTA = -0.05


def effect_demotion_enabled() -> bool:
    """Killswitch for the Δ-gate demotion route (default ON — same ambient-
    spend argument as promotion: it only acts on operator/CLI-driven
    measurement). config: knowledge.effect_demotion_enabled."""
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("knowledge.effect_demotion_enabled", True))
    except Exception:
        return True


def demote_lesson_by_effect(lesson_id: str, delta_evidence: Dict[str, Any]) -> bool:
    """Effect-based demotion: stamp a measured-negative Δ on a MEDIUM lesson.

    What the stamp does (see _is_delta_demoted): excludes the row from
    inject_tiered_lessons and blocks the tenure route to LONG. What it does
    NOT do — by the 2026-08-08 surface-scoping decree: no score mutation, no
    deletion, no flat-ledger change, no exclusion from query_lessons or
    extraction. Δ measured on decision replays licenses demotion from
    decision injection only; "other contexts might end up promoting on the
    same data" (Jeremy).

    Eligibility mirrors promote_lesson_by_effect with the sign flipped:
      - killswitch on (knowledge.effect_demotion_enabled)
      - delta <= knowledge.effect_demotion_max_delta (default −0.05)
      - n_calls >= knowledge.effect_promotion_min_calls (shared floor)
      - jackknife spread < |delta| (one call must not own the verdict)
      - stratum == "reason" (the rule stratum measured MIXED in census
        round 2 — rule-negative does not generalize, and rules are already
        excluded from the effect surface by construction)
    No provisional/quarantined/contested guard: those rows are already off
    every surface, and the stamp is a true measurement either way. The stamp
    is not a verdict for all time — a later measurement that clears the
    promote bar replaces it wholesale (promote_lesson_by_effect overwrites
    delta_evidence with route="effect").
    """
    if not effect_demotion_enabled():
        log.info("demote_lesson_by_effect: killswitch off — not demoting")
        return False
    try:
        from config import get as _cfg_get
        max_delta = float(_cfg_get("knowledge.effect_demotion_max_delta",
                                   EFFECT_DEMOTE_MAX_DELTA))
        min_calls = int(_cfg_get("knowledge.effect_promotion_min_calls",
                                 EFFECT_PROMOTE_MIN_CALLS))
    except Exception:
        max_delta, min_calls = EFFECT_DEMOTE_MAX_DELTA, EFFECT_PROMOTE_MIN_CALLS

    ev = dict(delta_evidence or {})
    delta = ev.get("delta")
    if not isinstance(delta, (int, float)) or delta > max_delta:
        return False
    if int(ev.get("n_calls") or 0) < min_calls:
        return False
    spread = ev.get("jackknife_spread")
    if not isinstance(spread, (int, float)) or spread >= abs(delta):
        return False
    if ev.get("stratum") != "reason":
        return False

    stamped: Dict[str, TieredLesson] = {}

    def _stamp(lessons: List[TieredLesson]) -> List[TieredLesson]:
        t = next((l for l in lessons if l.lesson_id == lesson_id), None)
        if t is None:
            return lessons
        t.delta_evidence = {
            "delta": float(delta),
            "jackknife_spread": float(spread),
            "n_calls": int(ev.get("n_calls") or 0),
            "stratum": "reason",
            "measured_at": ev.get("measured_at") or datetime.now(timezone.utc).isoformat(),
            "route": "effect-demote",
        }
        stamped["t"] = t
        return lessons

    _mutate_tiered_lessons(MemoryTier.MEDIUM, _stamp)
    if "t" not in stamped:
        return False
    log.info("demote_lesson_by_effect: %s excluded from decision injection "
             "(Δ=%.3f over %d calls)", lesson_id, float(delta),
             int(ev.get("n_calls") or 0))
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
        if (tl.score >= PROMOTE_MIN_SCORE
                and tl.sessions_validated >= PROMOTE_MIN_SESSIONS
                and not tl.provisional
                and not _is_quarantined(tl)
                and not _is_contested(tl)
                and not _is_delta_demoted(tl)):
            # not-provisional/quarantined/contested/Δ-demoted mirrors
            # _post_reinforce_hooks — the backstop must not promote what
            # the reinforcement path refuses to.
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


def _tfidf_rank_scored(
    query: str,
    lessons: List[TieredLesson],
    *,
    top_k: Optional[int] = None,
) -> List[tuple]:
    """(lesson, score) variant of _tfidf_rank — identical ordering and
    truncation. Score is cosine similarity in [0, 1] AFTER the Phase 60
    citation penalty (uncited lessons × _CITATION_PENALTY), i.e. the exact
    number the ranking sorted on. The no-signal path mirrors _tfidf_rank:
    all lessons in input order, top_k ignored, score 0.0.
    """
    if not lessons:
        return []

    query_terms = _tokenize(query)
    if not query_terms:
        return [(l, 0.0) for l in lessons]  # no query signal — input order

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
    pairs = [(l, s) for s, l in scores]
    return pairs[:top_k] if top_k is not None else pairs


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
    return [l for l, _ in _tfidf_rank_scored(query, lessons, top_k=top_k)]


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

    # Provisional, quarantined (prompt-derived), and contested lessons are
    # excluded from every injection surface (per-step learning 2026-07-27;
    # provenance gate 2026-07-29; retirement-by-contradiction 2026-08-02).
    # LONG can't hold provisional/quarantined (promotion is guarded) but CAN
    # hold contested — contestation is how decay-free LONG rows retire.
    long_candidates = [t for t in load_tiered_lessons(
        tier=MemoryTier.LONG, task_type=task_type, min_score=0.0,
        limit=max_long * _pool_multiplier,
    ) if not (t.provisional or _is_quarantined(t) or _is_contested(t))]
    if goal and len(long_candidates) > max_long:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        long_candidates = _ranker(goal, long_candidates, top_k=max_long)
    long_lessons = long_candidates[:max_long]

    # Mint-grounding display (design §3 slice 1): an unsupported method
    # claim rides into the prompt WITH its warning — fail-open, the
    # consumer weighs it.
    from mint_grounding import grounding_marker

    if long_lessons:
        parts.append("### Long-Term Lessons (always apply)")
        for l in long_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson}"
                         f"{grounding_marker(getattr(l, 'grounding', None))}")
            applied_ids.append((l.lesson_id, MemoryTier.LONG))

    # Δ-demoted rows leave THIS surface only (the measured one) — MEDIUM
    # filter alone because demotion is MEDIUM-scoped in v1; a LONG row's
    # delta_evidence can only carry route="effect".
    medium_candidates = [t for t in load_tiered_lessons(
        tier=MemoryTier.MEDIUM, task_type=task_type, min_score=0.3,
        limit=max_medium * _pool_multiplier,
    ) if not (t.provisional or _is_quarantined(t) or _is_contested(t)
              or _is_delta_demoted(t))]
    if goal and len(medium_candidates) > max_medium:
        _ranker = _hybrid_rank if _USE_HYBRID else _tfidf_rank
        medium_candidates = _ranker(goal, medium_candidates, top_k=max_medium)
    medium_lessons = medium_candidates[:max_medium]

    if medium_lessons:
        parts.append("### Medium-Term Lessons (apply if relevant)")
        for l in medium_lessons:
            icon = "✓" if l.outcome == "done" else "✗"
            parts.append(f"- {icon} {l.lesson}"
                         f"{grounding_marker(getattr(l, 'grounding', None))}"
                         f" [score={l.score:.2f}]")
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


def query_lessons_scored(
    query: str,
    *,
    n: int = 3,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    tiers: Optional[List[str]] = None,
    min_score: float = 0.0,
    include_provisional: bool = False,
    include_quarantined: bool = False,
    include_contested: bool = False,
) -> List[tuple]:
    """(lesson, score) variant of query_lessons — same pool, same ranking,
    same truncation; the ranker's internal score is surfaced instead of
    discarded (Chunk A camera-frame logging needs the road not taken).

    Score semantics follow ranker_name(): "hybrid" = RRF when recency
    fuses / raw BM25 otherwise; "tfidf" = citation-penalised cosine.
    Ordinal within one call's result — NOT probabilities; normalize per
    candidate set if a share is wanted.
    """
    if tiers is None:
        tiers = [MemoryTier.LONG, MemoryTier.MEDIUM]

    _ranker_scored = _hybrid_rank_scored if _USE_HYBRID else _tfidf_rank_scored

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
        if not include_provisional:
            pool = [t for t in pool if not t.provisional]
        if not include_quarantined:
            pool = [t for t in pool if not _is_quarantined(t)]
        if not include_contested:
            pool = [t for t in pool if not _is_contested(t)]
        candidates.extend(pool)

    if not candidates:
        return []

    ranked = _ranker_scored(query, candidates, top_k=n)
    return ranked[:n]


def query_lessons(
    query: str,
    *,
    n: int = 3,
    task_type: Optional[str] = None,
    lesson_type: Optional[str] = None,
    tiers: Optional[List[str]] = None,
    min_score: float = 0.0,
    include_provisional: bool = False,
    include_quarantined: bool = False,
    include_contested: bool = False,
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
        include_provisional: Default False — provisional (step-verified,
                     unconfirmed) lessons stay out of retrieval until a
                     confirmed-context re-record clears the flag.
        include_quarantined: Default False — prompt-derived (quarantined)
                     lessons stay out of retrieval; readout surfaces that
                     want to SHOW quarantine contents opt in.
        include_contested: Default False — contested lessons
                     (retirement-by-contradiction) stay out of retrieval;
                     readout/refight surfaces opt in.

    Returns:
        List of TieredLesson objects (most relevant first).
    """
    return [l for l, _ in query_lessons_scored(
        query, n=n, task_type=task_type, lesson_type=lesson_type,
        tiers=tiers, min_score=min_score,
        include_provisional=include_provisional,
        include_quarantined=include_quarantined,
        include_contested=include_contested,
    )]


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
        if _is_quarantined(lesson) or _is_contested(lesson):
            # A quarantined/contested row can accumulate stale canon hits
            # from before its stamp — never recommend it for AGENTS.md
            # identity.
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

# Link-farm import prefix (scripts/import_link_farm.py stamps node_id as
# "lf-" + sha256(url)[:10]). These nodes are a third-party reference corpus,
# not maro-learned knowledge — Jeremy 2026-08-02: "treat like a 3rd party
# website for gathering data." They must never rank into goal-context
# injection; reference queries opt in via include_reference=True.
LINK_FARM_PREFIX = "lf-"


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


def _bump_node_times_applied(node_ids: List[str]) -> None:
    """Persist a times_applied increment for each rendered node.

    The injection surface used to bump the deserialized copy only —
    1/647 live nodes ever showed times_applied > 0, and the query-time
    receipt boost (times_applied in _score) was dead by construction.
    Operates on raw dicts so unknown keys survive the rewrite (the
    dataclass filter is a READ convenience, never a write filter).
    """
    if not node_ids:
        return
    wanted = set(node_ids)
    p = _knowledge_nodes_path()
    from file_lock import atomic_write, locked_write
    try:
        with locked_write(p):
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except FileNotFoundError:
                return
            out: List[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped:
                    try:
                        d = json.loads(stripped)
                        if isinstance(d, dict) and d.get("node_id") in wanted:
                            d["times_applied"] = int(
                                d.get("times_applied", 0) or 0) + 1
                            line = json.dumps(d, sort_keys=True)
                    except json.JSONDecodeError:
                        pass
                out.append(line)
            atomic_write(p, "\n".join(out) + ("\n" if out else ""))
    except OSError as exc:
        log.warning("knowledge_node: times_applied bump failed: %s", exc)


# ---------------------------------------------------------------------------
# Candidate → active promotion (V3, 2026-08-02)
# ---------------------------------------------------------------------------
# Jeremy decree 2026-08-02: "same as skills, promoted to maro-local usable,
# up to the user to pick permanence." Mirrors skills.maybe_auto_promote_skills:
# numeric use+score gates, optional LLM validation stamped
# passed/unjudged/skipped, one captain's-log event per promotion. The
# permanent-vs-useful user gate is a later UX layer, not this sweep.
#
# Signal semantics: a CANDIDATE's times_applied can only grow through
# knowledge_bridge's dedup upsert (the injection-surface bump touches ACTIVE
# nodes only), so on candidates it counts independent re-observations of the
# same generalization from later runs — the analog of skill use_count.
# confidence moves in step (+0.05 per re-observation from a 0.3 birth), so
# together the gates read "re-derived at least twice since mint".

NODE_PROMOTE_MIN_APPLICATIONS = 2   # re-observations before promotion considered
NODE_PROMOTE_MIN_CONFIDENCE = 0.4   # 0.3 birth + 2 x 0.05 re-observation bumps

# Two +0.05 float bumps land at 0.39999999999999997 — a bare >= 0.4 gate
# would hold every legitimately re-observed-twice node forever.
_CONFIDENCE_EPSILON = 1e-9

_NODE_VALIDATION_SYSTEM = (
    "You are a knowledge quality gate for an AI orchestration system. "
    "Evaluate whether an auto-extracted knowledge node is ready to be "
    "injected into future planning context. A valid node is: "
    "(1) generalizable beyond one specific run; "
    "(2) concrete enough to act on — not a truism or vague aspiration; "
    "(3) plausibly correct as stated, with no unjustified absolutes. "
    "Respond with JSON: {\"valid\": true|false, \"reason\": \"one sentence\"}"
)


def _validate_node_for_promotion(d: Dict[str, Any], adapter: Any) -> Dict[str, Any]:
    """LLM quality gate for node promotion (skills.validate_skill_for_promotion mirror)."""
    try:
        from llm import LLMMessage
        from llm_parse import extract_json, content_or_empty
        node_text = (
            f"Type: {d.get('node_type', '')}\n"
            f"Title: {d.get('title', '')}\n"
            f"Description: {(d.get('description') or '')[:800]}\n"
            f"Re-observed {d.get('times_applied', 0)} time(s), "
            f"confidence {float(d.get('confidence', 0) or 0):.2f}"
        )
        resp = adapter.complete(
            [
                LLMMessage("system", _NODE_VALIDATION_SYSTEM),
                LLMMessage("user", f"Validate this knowledge node for promotion:\n\n{node_text}"),
            ],
            max_tokens=120,
            temperature=0.1,
            no_tools=True,
            purpose="knowledge node promotion validation",
        )
        parsed = extract_json(content_or_empty(resp), dict, log_tag="knowledge_web.promote")
        if isinstance(parsed, dict):
            return {
                "valid": bool(parsed.get("valid", False)),
                "reason": str(parsed.get("reason", "")),
                "judged": True,
            }
    except Exception as exc:
        log.debug("node promotion validation failed (fail-open): %s", exc)
    # Fail-open like skills: the numeric gates carried it here; a broken
    # validator must not block the cycle. judged=False so the promotion
    # event can say the pass was never a judgment.
    return {"valid": True, "reason": "validation unavailable (fail-open)",
            "judged": False}


def promote_knowledge_candidates(*, adapter: Any = None, dry_run: bool = False,
                                 limit: int = 10) -> List[str]:
    """Promote earned NODE_CANDIDATE rows to NODE_ACTIVE.

    Gates: times_applied >= NODE_PROMOTE_MIN_APPLICATIONS and confidence >=
    NODE_PROMOTE_MIN_CONFIDENCE (epsilon-tolerant). lf- reference-corpus
    nodes are never promoted — third-party data, not maro-learned knowledge.
    With an adapter, each survivor passes the LLM gate (held at candidate on
    an explicit "no"; fail-open "unjudged" on validator errors); without one,
    validation is "skipped" and the numeric gates decide alone — the same
    degradation contract as skill promotion.

    dry_run returns the numeric-gate survivors without validating, writing,
    or logging. Capped at `limit` promotions per sweep to bound validation
    spend; the remainder waits for the next maintenance pass.

    Returns the list of promoted node_ids.
    """
    p = _knowledge_nodes_path()
    if not p.exists():
        return []

    eligible: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("status") != NODE_CANDIDATE:
            continue
        if str(d.get("node_id", "")).startswith(LINK_FARM_PREFIX):
            continue
        if int(d.get("times_applied", 0) or 0) < NODE_PROMOTE_MIN_APPLICATIONS:
            continue
        if float(d.get("confidence", 0) or 0) < (
                NODE_PROMOTE_MIN_CONFIDENCE - _CONFIDENCE_EPSILON):
            continue
        eligible.append(d)

    if len(eligible) > limit:
        log.info("knowledge_node promotion: %d eligible, sweeping first %d",
                 len(eligible), limit)
        eligible = eligible[:limit]

    if dry_run or not eligible:
        return [d["node_id"] for d in eligible]

    promoted: Dict[str, str] = {}  # node_id → validation stamp
    for d in eligible:
        validation = "skipped"  # no adapter → validation never ran
        if adapter is not None:
            result = _validate_node_for_promotion(d, adapter)
            if not result["valid"]:
                log.info("knowledge_node %s held at candidate: %s",
                         d.get("node_id"), result["reason"])
                continue
            validation = "passed" if result.get("judged", True) else "unjudged"
        promoted[d["node_id"]] = validation

    if not promoted:
        return []

    # Flip survivors in one locked rewrite. Raw dicts so unknown keys survive
    # (the dataclass filter is a READ convenience, never a write filter).
    now = datetime.now(timezone.utc).isoformat()
    flipped: List[Dict[str, Any]] = []
    from file_lock import atomic_write, locked_write
    with locked_write(p):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        out: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped:
                try:
                    d = json.loads(stripped)
                    if (isinstance(d, dict) and d.get("node_id") in promoted
                            and d.get("status") == NODE_CANDIDATE):
                        d["status"] = NODE_ACTIVE
                        d["validated_at"] = now
                        line = json.dumps(d, sort_keys=True)
                        flipped.append(d)
                except json.JSONDecodeError:
                    pass
            out.append(line)
        atomic_write(p, "\n".join(out) + ("\n" if out else ""))

    for d in flipped:
        node_id = d["node_id"]
        log.info("knowledge_node promoted candidate -> active: %s %r",
                 node_id, str(d.get("title", ""))[:60])
        try:
            from captains_log import log_event, KNOWLEDGE_NODE_PROMOTED
            log_event(
                event_type=KNOWLEDGE_NODE_PROMOTED,
                subject=str(d.get("title", ""))[:120],
                summary=(
                    f"Promoted candidate -> active. Re-observed "
                    f"{int(d.get('times_applied', 0) or 0)}x, confidence "
                    f"{float(d.get('confidence', 0) or 0):.2f}."
                ),
                context={
                    "node_id": node_id,
                    "node_type": d.get("node_type", ""),
                    "times_applied": int(d.get("times_applied", 0) or 0),
                    "confidence": round(float(d.get("confidence", 0) or 0), 3),
                    "validation": promoted[node_id],
                },
                related_ids=[f"knowledge:{node_id}"],
            )
        except Exception:
            pass

    return [d["node_id"] for d in flipped]


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
    include_reference: bool = False,
) -> List[KnowledgeNode]:
    """Query knowledge nodes by goal relevance (TF-IDF ranked).

    Returns the most relevant active nodes for a given goal/query string.

    Reference-corpus nodes (link-farm imports, ``lf-`` prefix) are excluded
    unless ``include_reference=True``: they are third-party source material
    for research, not learned knowledge, and must not reach goal-context
    injection (decree 2026-08-02).
    """
    nodes = load_knowledge_nodes(domain=domain, node_type=node_type)
    if not include_reference:
        nodes = [n for n in nodes if not n.node_id.startswith(LINK_FARM_PREFIX)]
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
    applied_ids: List[str] = []
    for node in nodes:
        entry = f"- [{node.node_type}] {node.title}: {node.description[:200]}"
        if node.sources:
            entry += f" (source: {node.sources[0][:60]})"
        if chars + len(entry) > max_chars:
            break
        lines.append(entry)
        chars += len(entry)
        applied_ids.append(node.node_id)

    if len(lines) <= 1:
        return ""
    # Track application — persisted (2026-07-29): the old in-place bump
    # mutated the deserialized copy and dropped it, so the receipt (and
    # the times_applied boost in query scoring) never accrued.
    _bump_node_times_applied(applied_ids)
    return "\n".join(lines)


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

        # Stable node_id from URL hash, marked as reference corpus. The
        # LINK_FARM_PREFIX is what enforces the third-party disposition
        # (query exclusion, no promotion) — an unmarked import lane would
        # dodge every carve-out (found 2026-08-03 answering Jeremy's
        # "shouldn't lf- promote like any 3rd party data?" question).
        node_id = LINK_FARM_PREFIX + hashlib.sha256(url.encode()).hexdigest()[:12]

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
            # ACTIVE like the scripts/import_link_farm.py lane: reference
            # data is consult-ready on arrival (include_reference=True is
            # how it's reached), not maro knowledge earning trust — the
            # candidate→active ladder is the wrong verb for it, and a
            # CANDIDATE reference row would be invisible even to reference
            # queries (they load ACTIVE-only, then filter by prefix).
            status=NODE_ACTIVE,
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
