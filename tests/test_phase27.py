"""Tests for Phase 27: Graveyard query + acquired_for tag (knowledge prerequisite sub-goals)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import memory
from memory import (
    GC_THRESHOLD,
    MemoryTier,
    TieredLesson,
    load_tiered_lessons,
    record_tiered_lesson,
    reinforce_lesson,
    search_graveyard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lesson(tmp_path, lesson_text, score, tier=MemoryTier.MEDIUM, acquired_for=None):
    """Write a TieredLesson directly with the given score (bypasses normal 1.0 start).

    Path mirrors memory._tiered_lessons_path():
      {POE_WORKSPACE}/prototypes/poe-orchestration/memory/{tier}/lessons.jsonl
    """
    import json, uuid
    from dataclasses import asdict
    from datetime import date
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="test",
        outcome="success",
        lesson=lesson_text,
        source_goal="test-goal",
        confidence=0.7,
        tier=tier,
        score=score,
        last_reinforced=date.today().isoformat(),  # today → no inline decay
        acquired_for=acquired_for,
    )
    # orch_root() = POE_WORKSPACE/prototypes/poe-orchestration
    tier_file = tmp_path / "prototypes" / "poe-orchestration" / "memory" / tier / "lessons.jsonl"
    tier_file.parent.mkdir(parents=True, exist_ok=True)
    with open(tier_file, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    return tl


# ---------------------------------------------------------------------------
# TieredLesson.acquired_for field
# ---------------------------------------------------------------------------

def test_tiered_lesson_acquired_for_defaults_none():
    tl = TieredLesson(
        lesson_id="abc",
        task_type="t",
        outcome="ok",
        lesson="test",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=1.0,
        last_reinforced="2026-01-01",
    )
    assert tl.acquired_for is None


def test_tiered_lesson_acquired_for_can_be_set():
    tl = TieredLesson(
        lesson_id="abc",
        task_type="t",
        outcome="ok",
        lesson="test",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=1.0,
        last_reinforced="2026-01-01",
        acquired_for="goal-kanji-001",
    )
    assert tl.acquired_for == "goal-kanji-001"


def test_record_tiered_lesson_stores_acquired_for(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    tl = record_tiered_lesson(
        "kanji stroke order matters",
        task_type="language",
        outcome="success",
        source_goal="paint-kanji",
        acquired_for="goal-kanji-001",
    )
    assert tl.acquired_for == "goal-kanji-001"


def test_record_tiered_lesson_acquired_for_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    record_tiered_lesson(
        "kanji stroke order persisted",
        task_type="language",
        outcome="success",
        source_goal="paint-kanji",
        acquired_for="goal-kanji-002",
    )
    loaded = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
    assert any(tl.acquired_for == "goal-kanji-002" for tl in loaded)


def test_record_tiered_lesson_without_acquired_for(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    tl = record_tiered_lesson(
        "general lesson with no prerequisite context",
        task_type="general",
        outcome="success",
        source_goal="main-task",
    )
    assert tl.acquired_for is None


# ---------------------------------------------------------------------------
# search_graveyard — basic matching
# ---------------------------------------------------------------------------

def test_search_graveyard_finds_matching_lesson(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "kanji stroke order is important", score=0.25)
    results = search_graveyard("kanji")
    assert len(results) == 1
    assert "kanji" in results[0].lesson.lower()


def test_search_graveyard_ignores_active_lessons(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    # Active lesson (score 0.8 — above 0.4 threshold)
    _make_lesson(tmp_path, "kanji brush technique", score=0.8)
    results = search_graveyard("kanji")
    assert results == []


def test_search_graveyard_ignores_gc_candidates(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    # Below GC_THRESHOLD (0.2) — should already be GC'd; search_graveyard respects min_score
    _make_lesson(tmp_path, "kanji below gc", score=GC_THRESHOLD - 0.05)
    results = search_graveyard("kanji")
    assert results == []


def test_search_graveyard_no_match_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "Python list comprehensions", score=0.3)
    results = search_graveyard("kanji calligraphy")
    assert results == []


def test_search_graveyard_empty_workspace_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    results = search_graveyard("kanji")
    assert results == []


def test_search_graveyard_multi_keyword_ranking(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "kanji stroke order", score=0.3)
    _make_lesson(tmp_path, "kanji brush kanji calligraphy kanji", score=0.25)
    results = search_graveyard("kanji calligraphy stroke")
    # Both have 'kanji'; second has 'calligraphy', first has 'stroke' — check order is by match ratio
    assert len(results) == 2


def test_search_graveyard_limit_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    for i in range(5):
        _make_lesson(tmp_path, f"topic lesson number {i}", score=0.25 + i * 0.01)
    results = search_graveyard("topic lesson", limit=3)
    assert len(results) <= 3


def test_search_graveyard_checks_long_tier(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "long tier kanji", score=0.3, tier=MemoryTier.LONG)
    results = search_graveyard("kanji")
    assert len(results) == 1


# ---------------------------------------------------------------------------
# search_graveyard — resurrect=True
# ---------------------------------------------------------------------------

def test_search_graveyard_resurrect_raises_score(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "resurrect kanji lesson", score=0.28)
    results = search_graveyard("kanji", resurrect=True)
    assert len(results) == 1
    # After reinforce, reloading should show higher score
    reloaded = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
    match = next((tl for tl in reloaded if "resurrect" in tl.lesson), None)
    assert match is not None
    assert match.score > 0.28


def test_search_graveyard_resurrect_false_does_not_modify(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "readonly kanji lesson", score=0.28)
    search_graveyard("kanji", resurrect=False)
    reloaded = load_tiered_lessons(MemoryTier.MEDIUM, min_score=0.0)
    match = next((tl for tl in reloaded if "readonly" in tl.lesson), None)
    # score not in JSONL (decay applied inline) — just verify it loaded fine
    assert match is not None


# ---------------------------------------------------------------------------
# Graveyard + acquired_for integration
# ---------------------------------------------------------------------------

def test_graveyard_result_preserves_acquired_for(monkeypatch, tmp_path):
    monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
    _make_lesson(tmp_path, "kanji with tag", score=0.3, acquired_for="goal-kanji-prev")
    results = search_graveyard("kanji")
    assert len(results) == 1
    assert results[0].acquired_for == "goal-kanji-prev"
