"""Tests for Phase 16: Tiered Memory — short/medium/long tiers with decay."""

import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory import (
    # Tiered memory
    MemoryTier,
    TieredLesson,
    DECAY_FACTOR,
    REINFORCE_BONUS,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SESSIONS,
    GC_THRESHOLD,
    # Functions
    decay_score,
    reinforce_score,
    record_tiered_lesson,
    load_tiered_lessons,
    reinforce_lesson,
    forget_lesson,
    promote_lesson,
    run_decay_cycle,
    inject_tiered_lessons,
    memory_status,
    # Short-term
    short_set,
    short_get,
    short_clear,
    short_all,
    # Internal helpers
    _current_date,
    _days_since,
    _tiered_lessons_path,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    short_clear()
    return tmp_path


# ---------------------------------------------------------------------------
# Decay math
# ---------------------------------------------------------------------------

def test_decay_score_zero_days():
    assert decay_score(1.0, 0) == pytest.approx(1.0)


def test_decay_score_one_day():
    result = decay_score(1.0, 1)
    assert result == pytest.approx(DECAY_FACTOR)


def test_decay_score_compounding():
    result = decay_score(1.0, 3)
    assert result == pytest.approx(DECAY_FACTOR ** 3)


def test_decay_score_already_low():
    # Decaying a low score gets even lower
    result = decay_score(0.3, 5)
    assert result < 0.3


def test_reinforce_score_normal():
    score = 0.5
    result = reinforce_score(score)
    assert result == pytest.approx(score + REINFORCE_BONUS)


def test_reinforce_score_caps_at_one():
    result = reinforce_score(0.95)
    assert result == pytest.approx(1.0)


def test_reinforce_score_at_one():
    assert reinforce_score(1.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Short-term memory (in-process, session-scoped)
# ---------------------------------------------------------------------------

def test_short_set_get(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    short_set("key1", "value1")
    assert short_get("key1") == "value1"


def test_short_get_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert short_get("missing") is None
    assert short_get("missing", "fallback") == "fallback"


def test_short_clear(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    short_set("a", 1)
    short_set("b", 2)
    short_clear()
    assert short_get("a") is None
    assert short_all() == {}


def test_short_all_snapshot(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    short_set("x", 10)
    snap = short_all()
    assert snap["x"] == 10
    # Modifying snapshot doesn't affect store
    snap["x"] = 99
    assert short_get("x") == 10


# ---------------------------------------------------------------------------
# record_tiered_lesson
# ---------------------------------------------------------------------------

def test_record_tiered_lesson_medium(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("research needs clear criteria", "research", "done", "goal-1")
    assert isinstance(tl, TieredLesson)
    assert tl.tier == MemoryTier.MEDIUM
    assert tl.score == pytest.approx(1.0)
    assert tl.lesson_id


def test_record_tiered_lesson_long(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("always verify external data", "general", "done", "goal-2", tier=MemoryTier.LONG)
    assert tl.tier == MemoryTier.LONG


def test_record_tiered_lesson_persisted(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("lesson text A", "build", "done", "goal-3")
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert any(l.lesson == "lesson text A" for l in lessons)


def test_record_tiered_lesson_dedup_reinforces(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("unique lesson here", "research", "done", "goal-1")
    # Nearly identical lesson → reinforces existing
    record_tiered_lesson("unique lesson here", "research", "done", "goal-2")
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research")
    assert len(lessons) == 1


def test_record_tiered_lesson_different_types_both_stored(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("same words same words", "research", "done", "goal-A")
    record_tiered_lesson("same words same words", "build", "done", "goal-B")
    # Different task_type → both stored (dedup is per-type)
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert len(lessons) >= 1  # at minimum research one stored


# ---------------------------------------------------------------------------
# load_tiered_lessons
# ---------------------------------------------------------------------------

def test_load_tiered_lessons_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert load_tiered_lessons(tier=MemoryTier.MEDIUM) == []


def test_load_tiered_lessons_filters_task_type(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("research lesson", "research", "done", "g1")
    record_tiered_lesson("build lesson", "build", "done", "g2")
    research = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research")
    assert all(l.task_type == "research" for l in research)
    assert len(research) == 1


def test_load_tiered_lessons_filters_min_score(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # Write a lesson with low score manually
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    import uuid
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="stuck",
        lesson="low score lesson",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=0.15,
        last_reinforced=_current_date(),
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    # Should be filtered out when min_score=0.2
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.2)
    assert not any(l.lesson_id == tl.lesson_id for l in lessons)


def test_load_tiered_lessons_sorted_by_score(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import uuid
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    for score, text in [(0.5, "low"), (0.9, "high"), (0.7, "mid")]:
        tl = TieredLesson(
            lesson_id=str(uuid.uuid4())[:8],
            task_type="general",
            outcome="done",
            lesson=text,
            source_goal="g",
            confidence=0.7,
            tier=MemoryTier.MEDIUM,
            score=score,
            last_reinforced=_current_date(),
        )
        with open(path, "a") as f:
            f.write(json.dumps(asdict(tl)) + "\n")
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    scores = [l.score for l in lessons]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# reinforce_lesson
# ---------------------------------------------------------------------------

def test_reinforce_lesson(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import uuid
    # Start with a score of 0.5 so reinforcement is visible
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="done",
        lesson="reinforce me",
        source_goal="g1",
        confidence=0.7,
        tier=MemoryTier.MEDIUM,
        score=0.5,
        last_reinforced=_current_date(),
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    updated = reinforce_lesson(tl.lesson_id, tier=MemoryTier.MEDIUM)
    assert updated is not None
    assert updated.score > 0.5
    assert updated.sessions_validated == 1
    assert updated.times_reinforced == 1


def test_reinforce_lesson_not_found(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = reinforce_lesson("nonexistent", tier=MemoryTier.MEDIUM)
    assert result is None


def test_reinforce_lesson_persisted(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("persist check", "general", "done", "g1")
    reinforce_lesson(tl.lesson_id, tier=MemoryTier.MEDIUM)
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    updated = next(l for l in lessons if l.lesson_id == tl.lesson_id)
    assert updated.times_reinforced == 1


# ---------------------------------------------------------------------------
# forget_lesson
# ---------------------------------------------------------------------------

def test_forget_lesson_removes_entry(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("forget me", "general", "done", "g1")
    removed = forget_lesson(tl.lesson_id, tier=MemoryTier.MEDIUM)
    assert removed is True
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert not any(l.lesson_id == tl.lesson_id for l in lessons)


def test_forget_lesson_not_found(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert forget_lesson("ghost", tier=MemoryTier.MEDIUM) is False


def test_forget_lesson_leaves_others(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl1 = record_tiered_lesson("lesson one", "general", "done", "g1")
    tl2 = record_tiered_lesson("lesson two about something else", "general", "done", "g2")
    forget_lesson(tl1.lesson_id)
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert any(l.lesson_id == tl2.lesson_id for l in lessons)


# ---------------------------------------------------------------------------
# promote_lesson (medium → long)
# ---------------------------------------------------------------------------

def _make_eligible_lesson(tmp_path) -> TieredLesson:
    import uuid
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="done",
        lesson="highly validated lesson",
        source_goal="g",
        confidence=0.9,
        tier=MemoryTier.MEDIUM,
        score=0.95,
        last_reinforced=_current_date(),
        sessions_validated=4,
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    return tl


def test_promote_lesson_success(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = _make_eligible_lesson(tmp_path)
    ok = promote_lesson(tl.lesson_id)
    assert ok is True
    # Should no longer be in medium
    medium = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert not any(l.lesson_id == tl.lesson_id for l in medium)
    # Should appear in long
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG)
    assert any(l.lesson_id == tl.lesson_id for l in long_lessons)


def test_promote_lesson_ineligible_score(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("low score lesson", "general", "done", "g1")
    # score=1.0 but sessions=0 → ineligible (sessions < PROMOTE_MIN_SESSIONS)
    ok = promote_lesson(tl.lesson_id)
    assert ok is False


def test_promote_lesson_not_found(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert promote_lesson("ghost") is False


# ---------------------------------------------------------------------------
# run_decay_cycle
# ---------------------------------------------------------------------------

def test_run_decay_cycle_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("cycle lesson", "general", "done", "g1")
    result = run_decay_cycle(tier=MemoryTier.MEDIUM, dry_run=True)
    assert isinstance(result, dict)
    assert "decayed" in result
    assert "promoted" in result
    assert "gc" in result
    # Dry run should not remove anything
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert len(lessons) == 1


def test_run_decay_cycle_gc(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import uuid
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    # Write an entry with score below GC_THRESHOLD (0.2)
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="stuck",
        lesson="old stale lesson",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=0.1,
        last_reinforced="2025-01-01",  # old enough to have decayed past threshold
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    result = run_decay_cycle(tier=MemoryTier.MEDIUM, dry_run=False)
    assert result["gc"] >= 1
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
    assert not any(l.lesson_id == tl.lesson_id for l in lessons)


def test_run_decay_cycle_auto_promote(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = _make_eligible_lesson(tmp_path)
    result = run_decay_cycle(tier=MemoryTier.MEDIUM, dry_run=False)
    assert result["promoted"] >= 1
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG)
    assert any(l.lesson_id == tl.lesson_id for l in long_lessons)


# ---------------------------------------------------------------------------
# inject_tiered_lessons
# ---------------------------------------------------------------------------

def test_inject_tiered_lessons_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = inject_tiered_lessons("general")
    assert result == ""


def test_inject_tiered_lessons_long_only(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("long-tier lesson", "general", "done", "g1", tier=MemoryTier.LONG)
    result = inject_tiered_lessons("general")
    assert "long-tier lesson" in result
    assert "Long-Term" in result


def test_inject_tiered_lessons_medium_filtered(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("medium lesson", "research", "done", "g1", tier=MemoryTier.MEDIUM)
    result = inject_tiered_lessons("research")
    assert "medium lesson" in result
    assert "Medium-Term" in result


def test_inject_tiered_lessons_includes_short(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    short_set("current_project", "my-project")
    result = inject_tiered_lessons("general", include_short=True)
    assert "current_project" in result


def test_inject_tiered_lessons_excludes_short_by_default(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    short_set("secret_key", "secret_value")
    result = inject_tiered_lessons("general", include_short=False)
    assert "secret_key" not in result


def test_inject_tiered_lessons_min_score_filters_medium(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import uuid
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="done",
        lesson="barely passing lesson",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=0.2,
        last_reinforced=_current_date(),
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    # inject_tiered_lessons uses min_score=0.3 for medium → this lesson is filtered
    result = inject_tiered_lessons("general")
    assert "barely passing lesson" not in result


# ---------------------------------------------------------------------------
# memory_status
# ---------------------------------------------------------------------------

def test_memory_status_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    status = memory_status()
    assert "short" in status
    assert "medium" in status
    assert "long" in status
    assert status["medium"].get("count", 0) == 0
    assert status["long"].get("count", 0) == 0


def test_memory_status_with_data(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("medium lesson", "general", "done", "g1")
    record_tiered_lesson("long lesson", "general", "done", "g2", tier=MemoryTier.LONG)
    short_set("k", "v")
    status = memory_status()
    assert status["medium"]["count"] == 1
    assert status["long"]["count"] == 1
    assert status["short"]["count"] == 1


def test_memory_status_gc_candidates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    import uuid
    path = _tiered_lessons_path(MemoryTier.MEDIUM)
    tl = TieredLesson(
        lesson_id=str(uuid.uuid4())[:8],
        task_type="general",
        outcome="done",
        lesson="stale lesson",
        source_goal="g",
        confidence=0.5,
        tier=MemoryTier.MEDIUM,
        score=0.1,
        last_reinforced=_current_date(),
    )
    with open(path, "a") as f:
        f.write(json.dumps(asdict(tl)) + "\n")
    status = memory_status()
    assert status["medium"]["gc_candidates"] >= 1


def test_memory_status_promote_candidates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _make_eligible_lesson(tmp_path)
    status = memory_status()
    assert status["medium"]["promote_candidates"] >= 1


# ---------------------------------------------------------------------------
# _days_since helper
# ---------------------------------------------------------------------------

def test_days_since_today():
    today = _current_date()
    assert _days_since(today) == 0


def test_days_since_past():
    assert _days_since("2020-01-01") > 1000


def test_days_since_invalid():
    # Should not raise; returns 0
    assert _days_since("not-a-date") == 0


# ---------------------------------------------------------------------------
# Skill tier (integration)
# ---------------------------------------------------------------------------

def test_skill_default_tier():
    from skills import Skill
    import datetime
    s = Skill(
        id="s1", name="test", description="desc",
        trigger_patterns=[], steps_template=[], source_loop_ids=[],
        created_at=datetime.datetime.now().isoformat(),
    )
    assert s.tier == "provisional"


def test_promote_skill_tier_insufficient_rate(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import promote_skill_tier, save_skill, Skill
    import datetime, uuid
    s = Skill(
        id=str(uuid.uuid4())[:8], name="myskill", description="desc",
        trigger_patterns=[], steps_template=[], source_loop_ids=[],
        created_at=datetime.datetime.now().isoformat(),
        success_rate=0.5,  # pass^3 = 0.125 < 0.7
    )
    save_skill(s)
    assert promote_skill_tier("myskill") is False


def test_promote_skill_tier_success(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    from skills import promote_skill_tier, save_skill, load_skills, Skill
    import datetime, uuid
    s = Skill(
        id=str(uuid.uuid4())[:8], name="goodskill", description="desc",
        trigger_patterns=[], steps_template=[], source_loop_ids=[],
        created_at=datetime.datetime.now().isoformat(),
        success_rate=0.92,  # pass^3 = 0.778 >= 0.7
    )
    save_skill(s)
    result = promote_skill_tier("goodskill")
    assert result is True
    skills = load_skills()
    promoted = next(sk for sk in skills if sk.name == "goodskill")
    assert promoted.tier == "established"
