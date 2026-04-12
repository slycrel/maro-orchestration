"""Tests for knowledge_web.py — tiered memory, TF-IDF ranking, decay, promotion.

Coverage: decay helpers, CRUD, near-duplicate detection, TF-IDF ranking,
decay cycle, promotion, graveyard search, gap detection, knowledge nodes,
wiki-links, short-term memory, inject, query, canon tracking.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import knowledge_web as kw
from knowledge_web import (
    DECAY_FACTOR,
    GC_THRESHOLD,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SESSIONS,
    REINFORCE_BONUS,
    GoalGap,
    KnowledgeEdge,
    KnowledgeNode,
    MemoryTier,
    TieredLesson,
    confidence_from_k_samples,
    decay_score,
    detect_goal_gaps,
    extract_wiki_links,
    forget_lesson,
    inject_tiered_lessons,
    load_tiered_lessons,
    promote_lesson,
    query_lessons,
    record_tiered_lesson,
    reinforce_lesson,
    reinforce_score,
    run_decay_cycle,
    search_graveyard,
    short_all,
    short_clear,
    short_get,
    short_set,
)


@pytest.fixture(autouse=True)
def _set_memory_dir(tmp_path, monkeypatch):
    """Point POE_MEMORY_DIR at tmp_path/memory so _memory_dir() resolves there."""
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    monkeypatch.setenv("POE_MEMORY_DIR", str(mem))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lesson(
    lesson_id="abc",
    lesson="test lesson",
    task_type="general",
    outcome="done",
    source_goal="goal-1",
    tier=MemoryTier.MEDIUM,
    score=1.0,
    sessions_validated=0,
    times_applied=0,
    times_reinforced=0,
    confidence=0.7,
    evidence_sources=None,
    lesson_type="",
    last_reinforced=None,
    acquired_for=None,
) -> TieredLesson:
    return TieredLesson(
        lesson_id=lesson_id,
        task_type=task_type,
        outcome=outcome,
        lesson=lesson,
        source_goal=source_goal,
        confidence=confidence,
        tier=tier,
        score=score,
        last_reinforced=last_reinforced or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        sessions_validated=sessions_validated,
        times_applied=times_applied,
        times_reinforced=times_reinforced,
        evidence_sources=evidence_sources or [],
        lesson_type=lesson_type,
        acquired_for=acquired_for,
    )


def _write_lesson_to_file(tmp_path, tl: TieredLesson, tier: str = MemoryTier.MEDIUM):
    """Write a lesson directly to the tiered lessons file.

    Uses the same path resolution as knowledge_web (via _tiered_lessons_path)
    so the lesson is visible to load_tiered_lessons.
    """
    path = kw._tiered_lessons_path(tier)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(tl)) + "\n")


# ===========================================================================
# decay_score
# ===========================================================================

class TestDecayScore:
    def test_zero_days_no_decay(self):
        assert decay_score(1.0, 0) == 1.0

    def test_one_day_decay(self):
        assert decay_score(1.0, 1) == pytest.approx(DECAY_FACTOR)

    def test_multiple_days(self):
        assert decay_score(1.0, 3) == pytest.approx(DECAY_FACTOR ** 3)

    def test_already_low_score(self):
        assert decay_score(0.1, 1) == pytest.approx(0.1 * DECAY_FACTOR)

    def test_zero_score_stays_zero(self):
        assert decay_score(0.0, 10) == 0.0

    def test_large_days_approaches_zero(self):
        result = decay_score(1.0, 100)
        assert result < 0.001


# ===========================================================================
# reinforce_score
# ===========================================================================

class TestReinforceScore:
    def test_normal_reinforce(self):
        assert reinforce_score(0.5) == pytest.approx(0.5 + REINFORCE_BONUS)

    def test_capped_at_one(self):
        assert reinforce_score(0.9) == 1.0

    def test_already_at_one(self):
        assert reinforce_score(1.0) == 1.0

    def test_zero_reinforce(self):
        assert reinforce_score(0.0) == pytest.approx(REINFORCE_BONUS)

    def test_just_under_cap(self):
        result = reinforce_score(1.0 - REINFORCE_BONUS)
        assert result == 1.0


# ===========================================================================
# confidence_from_k_samples
# ===========================================================================

class TestConfidenceFromKSamples:
    def test_single_sample(self):
        assert confidence_from_k_samples(1) == 0.5

    def test_two_samples(self):
        assert confidence_from_k_samples(2) == 0.6

    def test_three_samples_majority_vote(self):
        assert confidence_from_k_samples(3) == 0.7

    def test_many_samples(self):
        assert confidence_from_k_samples(10) == 0.7

    def test_zero_samples_defaults_to_single(self):
        # k_samples=0 shouldn't normally be called, but tests edge case
        assert confidence_from_k_samples(0) == 0.5


# ===========================================================================
# Short-term memory
# ===========================================================================

class TestShortTermMemory:
    def setup_method(self):
        short_clear()

    def test_set_and_get(self):
        short_set("key1", "value1")
        assert short_get("key1") == "value1"

    def test_get_missing_returns_default(self):
        assert short_get("nonexistent") is None
        assert short_get("nonexistent", 42) == 42

    def test_clear(self):
        short_set("a", 1)
        short_set("b", 2)
        short_clear()
        assert short_get("a") is None
        assert short_all() == {}

    def test_all_snapshot(self):
        short_set("x", 10)
        short_set("y", 20)
        snap = short_all()
        assert snap == {"x": 10, "y": 20}
        # Verify it's a copy, not a reference
        snap["z"] = 30
        assert short_get("z") is None

    def test_overwrite(self):
        short_set("key", "old")
        short_set("key", "new")
        assert short_get("key") == "new"

    def teardown_method(self):
        short_clear()


# ===========================================================================
# record_tiered_lesson
# ===========================================================================

class TestRecordTieredLesson:
    def test_basic_record(self, tmp_path):
        tl = record_tiered_lesson(
            "Always check return codes",
            task_type="build",
            outcome="done",
            source_goal="goal-1",
        )
        assert tl.lesson == "Always check return codes"
        assert tl.score == 1.0
        assert tl.tier == MemoryTier.MEDIUM
        assert len(tl.lesson_id) == 8

    def test_k_samples_overrides_confidence(self, tmp_path):
        tl = record_tiered_lesson(
            "Lesson with k_samples",
            task_type="build",
            outcome="done",
            source_goal="g1",
            k_samples=1,
        )
        assert tl.confidence == 0.5

    def test_explicit_confidence_when_no_k_samples(self, tmp_path):
        tl = record_tiered_lesson(
            "High confidence lesson",
            task_type="build",
            outcome="done",
            source_goal="g1",
            confidence=0.95,
        )
        assert tl.confidence == 0.95

    def test_near_duplicate_reinforces(self, tmp_path):
        tl1 = record_tiered_lesson(
            "Always check return codes after subprocess calls",
            task_type="build",
            outcome="done",
            source_goal="g1",
        )
        # Record a near-duplicate (very similar text)
        tl2 = record_tiered_lesson(
            "Always check return codes after subprocess calls",
            task_type="build",
            outcome="done",
            source_goal="g1",
        )
        # Should have reinforced the existing one
        assert tl2.lesson_id == tl1.lesson_id
        assert tl2.times_reinforced >= 1

    def test_different_lesson_gets_new_id(self, tmp_path):
        tl1 = record_tiered_lesson(
            "Use exponential backoff for retries",
            task_type="build",
            outcome="done",
            source_goal="g1",
        )
        tl2 = record_tiered_lesson(
            "Memory isolation prevents test pollution completely",
            task_type="test",
            outcome="done",
            source_goal="g2",
        )
        assert tl1.lesson_id != tl2.lesson_id

    def test_lesson_type_validated(self, tmp_path):
        tl = record_tiered_lesson(
            "execution lesson", task_type="build", outcome="done",
            source_goal="g1", lesson_type="execution",
        )
        assert tl.lesson_type == "execution"

    def test_invalid_lesson_type_cleared(self, tmp_path):
        tl = record_tiered_lesson(
            "invalid type lesson", task_type="build", outcome="done",
            source_goal="g1", lesson_type="bogus",
        )
        assert tl.lesson_type == ""

    def test_evidence_sources_stored(self, tmp_path):
        tl = record_tiered_lesson(
            "Evidence-backed lesson", task_type="research", outcome="done",
            source_goal="g1", evidence_sources=["https://example.com"],
        )
        assert tl.evidence_sources == ["https://example.com"]

    def test_acquired_for_stored(self, tmp_path):
        tl = record_tiered_lesson(
            "Incidental lesson", task_type="build", outcome="done",
            source_goal="g1", acquired_for="sub-goal-5",
        )
        assert tl.acquired_for == "sub-goal-5"

    def test_record_to_long_tier(self, tmp_path):
        tl = record_tiered_lesson(
            "Long tier lesson", task_type="build", outcome="done",
            source_goal="g1", tier=MemoryTier.LONG,
        )
        assert tl.tier == MemoryTier.LONG
        loaded = load_tiered_lessons(tier=MemoryTier.LONG)
        assert any(l.lesson_id == tl.lesson_id for l in loaded)


# ===========================================================================
# load_tiered_lessons
# ===========================================================================

class TestLoadTieredLessons:
    def test_empty_file(self, tmp_path):
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM)
        assert result == []

    def test_load_filters_by_task_type(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="a", task_type="build"))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="b", task_type="research"))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="build")
        assert len(result) == 1
        assert result[0].lesson_id == "a"

    def test_load_filters_by_lesson_type(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="a", lesson_type="execution"))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="b", lesson_type="planning"))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, lesson_type="execution")
        assert len(result) == 1
        assert result[0].lesson_id == "a"

    def test_load_filters_by_min_score(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="a", score=0.9))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="b", score=0.1))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.5)
        assert len(result) == 1
        assert result[0].lesson_id == "a"

    def test_load_respects_limit(self, tmp_path):
        for i in range(10):
            _write_lesson_to_file(tmp_path, _make_lesson(lesson_id=f"l{i}"))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=3)
        assert len(result) == 3

    def test_load_sorted_by_score_descending(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="low", score=0.3))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="high", score=0.9))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="mid", score=0.6))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert [r.lesson_id for r in result] == ["high", "mid", "low"]

    def test_load_applies_decay_for_old_lessons(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="old", score=1.0, last_reinforced=old_date,
        ))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert len(result) == 1
        assert result[0].score == pytest.approx(DECAY_FACTOR ** 5, abs=0.01)

    def test_load_max_age_days_filter(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="old", last_reinforced=old_date))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="new"))
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0, max_age_days=7)
        assert len(result) == 1
        assert result[0].lesson_id == "new"

    def test_malformed_json_lines_skipped(self, tmp_path):
        d = tmp_path / "memory" / "medium"
        d.mkdir(parents=True, exist_ok=True)
        path = d / "lessons.jsonl"
        # Write one valid + one malformed line
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="valid"))
        with open(path, "a") as f:
            f.write("not valid json\n")
        result = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert len(result) == 1
        assert result[0].lesson_id == "valid"


# ===========================================================================
# reinforce_lesson
# ===========================================================================

class TestReinforceLesson:
    def test_reinforce_bumps_score(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="r1", score=0.5))
        result = reinforce_lesson("r1", tier=MemoryTier.MEDIUM)
        assert result is not None
        assert result.score == pytest.approx(reinforce_score(0.5))
        assert result.sessions_validated == 1
        assert result.times_reinforced == 1

    def test_reinforce_nonexistent_returns_none(self, tmp_path):
        result = reinforce_lesson("nonexistent", tier=MemoryTier.MEDIUM)
        assert result is None

    def test_reinforce_promotes_confidence_after_3_sessions(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="r2", score=0.5, sessions_validated=2, confidence=0.5,
        ))
        result = reinforce_lesson("r2", tier=MemoryTier.MEDIUM)
        assert result.sessions_validated == 3
        assert result.confidence >= 0.9

    def test_reinforce_persists_to_disk(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="r3", score=0.5))
        reinforce_lesson("r3", tier=MemoryTier.MEDIUM)
        reloaded = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        found = next(l for l in reloaded if l.lesson_id == "r3")
        assert found.times_reinforced == 1


# ===========================================================================
# forget_lesson
# ===========================================================================

class TestForgetLesson:
    def test_forget_existing(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="f1"))
        assert forget_lesson("f1", tier=MemoryTier.MEDIUM) is True
        assert load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0) == []

    def test_forget_nonexistent(self, tmp_path):
        assert forget_lesson("nonexistent", tier=MemoryTier.MEDIUM) is False

    def test_forget_leaves_other_lessons(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="keep"))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="drop"))
        forget_lesson("drop", tier=MemoryTier.MEDIUM)
        remaining = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert len(remaining) == 1
        assert remaining[0].lesson_id == "keep"


# ===========================================================================
# promote_lesson
# ===========================================================================

class TestPromoteLesson:
    def test_promote_eligible(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="p1", score=0.95, sessions_validated=4,
        ))
        assert promote_lesson("p1") is True
        # Should be gone from medium
        medium = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert not any(l.lesson_id == "p1" for l in medium)
        # Should be in long
        long_ = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
        assert any(l.lesson_id == "p1" for l in long_)

    def test_promote_ineligible_low_score(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="p2", score=0.5, sessions_validated=5,
        ))
        assert promote_lesson("p2") is False

    def test_promote_ineligible_low_sessions(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="p3", score=0.95, sessions_validated=1,
        ))
        assert promote_lesson("p3") is False

    def test_promote_nonexistent(self, tmp_path):
        assert promote_lesson("nonexistent") is False

    def test_promoted_lesson_has_long_tier(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="p4", score=0.95, sessions_validated=4,
        ))
        promote_lesson("p4")
        long_ = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
        found = next(l for l in long_ if l.lesson_id == "p4")
        assert found.tier == MemoryTier.LONG


# ===========================================================================
# run_decay_cycle
# ===========================================================================

class TestRunDecayCycle:
    def test_decay_cycle_decays_old_lessons(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="d1", score=0.8, last_reinforced=old_date,
        ))
        result = run_decay_cycle(tier=MemoryTier.MEDIUM)
        assert result["decayed"] >= 1

    def test_decay_cycle_gcs_low_score(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="gc1", score=0.15, last_reinforced=old_date,
        ))
        result = run_decay_cycle(tier=MemoryTier.MEDIUM)
        assert result["gc"] >= 1
        remaining = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        assert not any(l.lesson_id == "gc1" for l in remaining)

    def test_decay_cycle_promotes_eligible(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="dp1", score=0.95, sessions_validated=4,
        ))
        result = run_decay_cycle(tier=MemoryTier.MEDIUM)
        assert result["promoted"] >= 1

    def test_decay_cycle_dry_run_no_mutations(self, tmp_path):
        old_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="dry1", score=0.8, last_reinforced=old_date,
        ))
        result = run_decay_cycle(tier=MemoryTier.MEDIUM, dry_run=True)
        assert result["decayed"] >= 1
        # Score should NOT be persisted — reload should show original
        reloaded = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        # The lesson still exists (no GC in dry_run)
        assert len(reloaded) == 1

    def test_decay_cycle_empty_tier(self, tmp_path):
        result = run_decay_cycle(tier=MemoryTier.MEDIUM)
        assert result == {"decayed": 0, "promoted": 0, "gc": 0}


# ===========================================================================
# search_graveyard
# ===========================================================================

class TestSearchGraveyard:
    def test_finds_decayed_matching_lessons(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="g1", score=0.25, lesson="retry strategy for network errors",
        ))
        results = search_graveyard("retry network")
        assert len(results) >= 1
        assert results[0].lesson_id == "g1"

    def test_ignores_high_score_lessons(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="active", score=0.9, lesson="retry strategy for network errors",
        ))
        results = search_graveyard("retry network")
        assert len(results) == 0

    def test_ignores_no_keyword_match(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="g2", score=0.25, lesson="database migration patterns",
        ))
        results = search_graveyard("network timeout")
        assert len(results) == 0

    def test_resurrect_reinforces_matches(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="g3", score=0.25, lesson="retry strategy for network errors",
        ))
        results = search_graveyard("retry network", resurrect=True)
        assert len(results) >= 1
        # After resurrection, the lesson should have a higher score
        reloaded = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        found = next((l for l in reloaded if l.lesson_id == "g3"), None)
        assert found is not None
        assert found.score > 0.25

    def test_empty_topic_returns_nothing(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="g4", score=0.25))
        results = search_graveyard("")
        assert results == []


# ===========================================================================
# TF-IDF ranking (_tfidf_rank)
# ===========================================================================

class TestTfidfRank:
    def test_relevant_ranked_above_irrelevant(self):
        relevant = _make_lesson(lesson="network retry exponential backoff strategy")
        irrelevant = _make_lesson(lesson="database schema migration patterns")
        ranked = kw._tfidf_rank("network retry timeout", [irrelevant, relevant])
        assert ranked[0].lesson == relevant.lesson

    def test_empty_lessons_returns_empty(self):
        assert kw._tfidf_rank("query", []) == []

    def test_empty_query_returns_all(self):
        lessons = [_make_lesson(lesson="something")]
        result = kw._tfidf_rank("", lessons)
        assert len(result) == 1

    def test_top_k_limits_results(self):
        lessons = [_make_lesson(lesson=f"lesson about topic {i}") for i in range(10)]
        result = kw._tfidf_rank("topic", lessons, top_k=3)
        assert len(result) == 3

    def test_citation_penalty_applied_to_uncited(self):
        cited = _make_lesson(
            lesson="network retry strategy", evidence_sources=["https://example.com"],
        )
        uncited = _make_lesson(lesson="network retry strategy")
        # Both have identical text but cited should rank higher
        ranked = kw._tfidf_rank("network retry", [uncited, cited])
        assert ranked[0].evidence_sources == ["https://example.com"]

    def test_query_with_only_stopwords_returns_all(self):
        lessons = [_make_lesson(lesson="real content here")]
        # "the and is" are all stop words
        result = kw._tfidf_rank("the and is", lessons)
        assert len(result) == 1


# ===========================================================================
# query_lessons
# ===========================================================================

class TestQueryLessons:
    def test_query_returns_relevant(self, tmp_path):
        record_tiered_lesson(
            "Use exponential backoff for retries",
            task_type="build", outcome="done", source_goal="g1",
        )
        record_tiered_lesson(
            "Database indexes improve query performance",
            task_type="build", outcome="done", source_goal="g2",
        )
        results = query_lessons("retry backoff", n=1)
        assert len(results) == 1
        assert "backoff" in results[0].lesson.lower()

    def test_query_empty_returns_empty(self, tmp_path):
        results = query_lessons("something", n=5)
        assert results == []

    def test_query_respects_tier_filter(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="med1", lesson="medium tier lesson about testing",
            tier=MemoryTier.MEDIUM,
        ))
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="long1", lesson="long tier lesson about testing",
            tier=MemoryTier.LONG,
        ), tier=MemoryTier.LONG)
        results = query_lessons("testing", tiers=[MemoryTier.LONG])
        ids = [r.lesson_id for r in results]
        assert "long1" in ids
        assert "med1" not in ids


# ===========================================================================
# detect_goal_gaps
# ===========================================================================

class TestDetectGoalGaps:
    def test_blocked_steps_produce_high_severity_gaps(self, tmp_path):
        gaps = detect_goal_gaps(
            "Build retry system",
            outcomes=[],
            blocked_steps=["Implement exponential backoff"],
        )
        assert len(gaps) >= 1
        assert gaps[0].gap_type == "blocked_step"
        assert gaps[0].severity == "high"

    def test_no_gaps_when_everything_covered(self, tmp_path):
        gaps = detect_goal_gaps("test", outcomes=[], blocked_steps=[])
        assert isinstance(gaps, list)

    def test_max_gaps_limits_output(self, tmp_path):
        gaps = detect_goal_gaps(
            "Build something complex",
            outcomes=[],
            blocked_steps=[f"Step {i}" for i in range(10)],
            max_gaps=3,
        )
        assert len(gaps) <= 3


# ===========================================================================
# inject_tiered_lessons
# ===========================================================================

class TestInjectTieredLessons:
    def test_inject_empty_returns_empty_string(self, tmp_path):
        result = inject_tiered_lessons("build", track_applied=False)
        assert result == ""

    def test_inject_includes_long_tier(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="inj1", lesson="Always validate inputs",
            tier=MemoryTier.LONG, outcome="done",
        ), tier=MemoryTier.LONG)
        result = inject_tiered_lessons("general", track_applied=False)
        assert "Always validate inputs" in result
        assert "Long-Term" in result

    def test_inject_includes_medium_above_threshold(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="inj2", lesson="Use structured logging", score=0.8,
        ))
        result = inject_tiered_lessons("general", track_applied=False)
        assert "Use structured logging" in result

    def test_inject_excludes_low_score_medium(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="inj3", lesson="Obsolete pattern", score=0.1,
        ))
        result = inject_tiered_lessons("general", track_applied=False)
        # Score 0.1 is below the 0.3 threshold for medium
        assert "Obsolete pattern" not in result

    def test_inject_includes_short_term_when_flagged(self, tmp_path):
        short_clear()
        short_set("current_focus", "testing")
        _write_lesson_to_file(tmp_path, _make_lesson(
            lesson_id="inj4", lesson="Some lesson", tier=MemoryTier.LONG,
        ), tier=MemoryTier.LONG)
        result = inject_tiered_lessons("general", include_short=True, track_applied=False)
        assert "current_focus" in result
        short_clear()


# ===========================================================================
# Knowledge Nodes (K2)
# ===========================================================================

class TestKnowledgeNodes:
    def test_append_and_load(self, tmp_path):
        node = KnowledgeNode(
            node_id="n001", node_type="principle",
            title="Test First", description="Write tests before code",
            domain="testing",
        )
        kw.append_knowledge_node(node)
        loaded = kw.load_knowledge_nodes()
        assert len(loaded) == 1
        assert loaded[0].node_id == "n001"

    def test_filter_by_node_type(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="n1", node_type="principle", title="P1", description="d1",
        ))
        kw.append_knowledge_node(KnowledgeNode(
            node_id="n2", node_type="pattern", title="P2", description="d2",
        ))
        result = kw.load_knowledge_nodes(node_type="principle")
        assert len(result) == 1
        assert result[0].node_id == "n1"

    def test_filter_by_domain(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="n3", node_type="principle", title="T", description="D",
            domain="memory",
        ))
        kw.append_knowledge_node(KnowledgeNode(
            node_id="n4", node_type="principle", title="T2", description="D2",
            domain="quality",
        ))
        result = kw.load_knowledge_nodes(domain="memory")
        assert len(result) == 1

    def test_filter_by_tag(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="n5", node_type="insight", title="T", description="D",
            tags=["important", "reviewed"],
        ))
        result = kw.load_knowledge_nodes(tag="important")
        assert len(result) == 1

    def test_find_node_by_id(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="find1", node_type="tool", title="Tool X", description="Desc",
        ))
        found = kw.find_knowledge_node("find1")
        assert found is not None
        assert found.title == "Tool X"

    def test_find_nonexistent_node(self, tmp_path):
        assert kw.find_knowledge_node("nope") is None

    def test_empty_store_returns_empty(self, tmp_path):
        assert kw.load_knowledge_nodes() == []


# ===========================================================================
# Knowledge Edges
# ===========================================================================

class TestKnowledgeEdges:
    def test_append_and_load(self, tmp_path):
        edge = KnowledgeEdge(source_id="a", target_id="b", relation="supports")
        kw.append_knowledge_edge(edge)
        loaded = kw.load_knowledge_edges()
        assert len(loaded) == 1
        assert loaded[0].relation == "supports"

    def test_filter_by_node_id(self, tmp_path):
        kw.append_knowledge_edge(KnowledgeEdge(source_id="a", target_id="b", relation="supports"))
        kw.append_knowledge_edge(KnowledgeEdge(source_id="c", target_id="d", relation="extends"))
        result = kw.load_knowledge_edges(node_id="a")
        assert len(result) == 1


# ===========================================================================
# Wiki-links
# ===========================================================================

class TestWikiLinks:
    def test_extract_single_link(self):
        assert extract_wiki_links("See [[concept-a]] for details") == ["concept-a"]

    def test_extract_multiple_links(self):
        text = "Uses [[pattern-x]] and [[technique-y]]"
        assert extract_wiki_links(text) == ["pattern-x", "technique-y"]

    def test_no_links(self):
        assert extract_wiki_links("No links here") == []

    def test_build_wiki_link_edges(self):
        nodes = [
            KnowledgeNode(node_id="n1", node_type="principle", title="Pattern X",
                          description="Related to [[technique-y]]"),
            KnowledgeNode(node_id="n2", node_type="technique", title="Technique Y",
                          description="Standalone"),
        ]
        edges = kw.build_wiki_link_edges(nodes)
        assert len(edges) == 1
        assert edges[0].source_id == "n1"
        assert edges[0].target_id == "n2"

    def test_wiki_link_no_self_reference(self):
        nodes = [
            KnowledgeNode(node_id="n1", node_type="principle", title="Concept A",
                          description="See also [[concept-a]]"),
        ]
        edges = kw.build_wiki_link_edges(nodes)
        assert len(edges) == 0


# ===========================================================================
# query_knowledge
# ===========================================================================

class TestQueryKnowledge:
    def test_relevant_nodes_ranked_first(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="q1", node_type="principle", title="Retry Patterns",
            description="exponential backoff retry network failure recovery",
            confidence=0.8,
        ))
        kw.append_knowledge_node(KnowledgeNode(
            node_id="q2", node_type="principle", title="Database Sharding",
            description="horizontal partitioning database performance scale",
            confidence=0.8,
        ))
        results = kw.query_knowledge("network retry failure")
        assert len(results) >= 1
        assert results[0].node_id == "q1"

    def test_empty_store(self, tmp_path):
        assert kw.query_knowledge("anything") == []

    def test_min_confidence_filter(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="q3", node_type="insight", title="Low confidence",
            description="something about testing patterns",
            confidence=0.1,
        ))
        results = kw.query_knowledge("testing", min_confidence=0.5)
        assert len(results) == 0


# ===========================================================================
# inject_knowledge_for_goal
# ===========================================================================

class TestInjectKnowledgeForGoal:
    def test_inject_empty_returns_empty(self, tmp_path):
        assert kw.inject_knowledge_for_goal("some goal") == ""

    def test_inject_includes_relevant_nodes(self, tmp_path):
        kw.append_knowledge_node(KnowledgeNode(
            node_id="ik1", node_type="principle", title="Test Isolation",
            description="always isolate test workspace from production",
            confidence=0.8, sources=["https://example.com/testing"],
        ))
        result = kw.inject_knowledge_for_goal("test isolation workspace")
        assert "Test Isolation" in result
        assert "Relevant Knowledge" in result


# ===========================================================================
# memory_status
# ===========================================================================

class TestMemoryStatus:
    def test_empty_status(self, tmp_path):
        short_clear()
        status = kw.memory_status()
        assert status["short"]["count"] == 0
        assert status["medium"]["count"] == 0
        assert status["long"]["count"] == 0

    def test_status_with_lessons(self, tmp_path):
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="s1", score=0.8))
        _write_lesson_to_file(tmp_path, _make_lesson(lesson_id="s2", score=0.6))
        status = kw.memory_status()
        assert status["medium"]["count"] == 2
        assert status["medium"]["avg_score"] > 0


# ===========================================================================
# _tokenize
# ===========================================================================

class TestTokenize:
    def test_removes_stopwords(self):
        tokens = kw._tokenize("the quick brown fox and the lazy dog")
        assert "the" not in tokens
        assert "and" not in tokens
        assert "quick" in tokens

    def test_removes_short_tokens(self):
        tokens = kw._tokenize("a is to go on")
        assert tokens == []  # all too short or stopwords

    def test_lowercases(self):
        tokens = kw._tokenize("HELLO World")
        assert "hello" in tokens
        assert "world" in tokens
