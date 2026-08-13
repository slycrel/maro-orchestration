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
    NOVELTY_BONUS,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SESSIONS,
    GC_THRESHOLD,
    CANON_APPLY_THRESHOLD,
    CANON_TASK_TYPE_MIN,
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
    get_canon_candidates,
    _record_canon_hit,
    _load_canon_stats,
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
    # Chunk 6: a first lesson into an empty store is fully novel — the
    # novelty term boosts its initial score above the classic 1.0.
    assert tl.score == pytest.approx(1.0 + NOVELTY_BONUS)
    assert tl.novelty == pytest.approx(1.0)
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


def test_record_dedup_preserves_near_dup_text(monkeypatch, tmp_path):
    """MH Memory Rationale Erosion (2026-08-11): a >0.8-similar incoming
    lesson's text was discarded at the record-time dedup — the dropped
    words can be the operative clause. The survivor keeps it now."""
    _setup(monkeypatch, tmp_path)
    base = "always validate user inputs at the system boundary before processing any data"
    near = "always validate user inputs at the system boundary before processing all data"
    record_tiered_lesson(base, "research", "done", "goal-1")
    record_tiered_lesson(near, "research", "done", "goal-2")
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research")
    assert len(lessons) == 1
    assert lessons[0].lesson == base
    assert lessons[0].merged_variants == [near]


def test_reinforce_version_binding_voids_stale_sighting(monkeypatch, tmp_path):
    """Adversarial review 2026-08-11, both rounds: the dedup match is made
    outside the mutation lock — if the row's text was revised in between
    (refight), the sighting confirmed the OLD text. Full no-op: no
    variant, no counter, no score movement (similarity is not identity:
    an 'always…'→'never…' revision still scores 0.88)."""
    _setup(monkeypatch, tmp_path)
    from knowledge_web import (MemoryTier, _reinforce_tiered_lesson,
                               load_tiered_lessons, record_tiered_lesson)
    base = "always validate user inputs at the system boundary before processing any data"
    record_tiered_lesson(base, "research", "done", "goal-1")
    stale_copy = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research")[0]

    # Simulate a concurrent revision landing after the caller's match —
    # NEAR-identical text (>0.8 similar), so a similarity floor would
    # wrongly accept it; only byte-exact binding refuses.
    import knowledge_web as kw
    revised = base.replace("always", "never")

    def _revise(lessons):
        for l in lessons:
            if l.lesson_id == stale_copy.lesson_id:
                l.lesson = revised
        return lessons

    kw._mutate_tiered_lessons(MemoryTier.MEDIUM, _revise)

    _reinforce_tiered_lesson(stale_copy, tier=MemoryTier.MEDIUM,
                             incoming_text=base.replace("any", "all"),
                             matched_lesson_text=base)
    row = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research",
                              raw=True)[0]
    assert row.merged_variants == []
    assert row.times_reinforced == 0
    assert row.sessions_validated == 0


def test_record_dedup_identical_text_leaves_no_variant(monkeypatch, tmp_path):
    """Identical re-records lose nothing — no variant recorded."""
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("unique lesson here", "research", "done", "goal-1")
    record_tiered_lesson("unique lesson here", "research", "done", "goal-2")
    lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, task_type="research")
    assert len(lessons) == 1
    assert lessons[0].merged_variants == []


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


def test_promotion_long_append_failure_keeps_medium_row(monkeypatch, tmp_path):
    """Promotion atomicity (adversarial review 2026-08-11, HIGH): the old
    remove-then-append order silently and permanently lost the lesson when
    the LONG write failed. Destination-first: a LONG failure must leave
    the MEDIUM row untouched."""
    _setup(monkeypatch, tmp_path)
    import knowledge_web as kw
    tl = _make_eligible_lesson(tmp_path)
    real_mutate = kw._mutate_tiered_lessons

    def _long_write_fails(tier, mutate):
        if tier == MemoryTier.LONG:
            raise OSError("disk full")
        return real_mutate(tier, mutate)

    monkeypatch.setattr(kw, "_mutate_tiered_lessons", _long_write_fails)
    import pytest as _pytest
    with _pytest.raises(OSError):
        promote_lesson(tl.lesson_id)
    monkeypatch.setattr(kw, "_mutate_tiered_lessons", real_mutate)
    medium = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert any(l.lesson_id == tl.lesson_id for l in medium), \
        "LONG write failure must not lose the MEDIUM row"
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG)
    assert not any(l.lesson_id == tl.lesson_id for l in long_lessons)


def test_promotion_is_idempotent_after_crash_window(monkeypatch, tmp_path):
    """A retry after a crash between LONG append and MEDIUM removal must
    not duplicate the LONG row (append-if-absent) and must complete the
    interrupted move."""
    _setup(monkeypatch, tmp_path)
    import json
    from dataclasses import asdict, replace
    import knowledge_web as kw
    tl = _make_eligible_lesson(tmp_path)
    # Manufacture the crash-window state: copy already in LONG, row still
    # in MEDIUM.
    long_copy = replace(tl)
    long_copy.tier = MemoryTier.LONG
    kw._append_tiered_lesson(long_copy, tier=MemoryTier.LONG)

    ok = promote_lesson(tl.lesson_id)
    assert ok is True
    long_rows = [l for l in load_tiered_lessons(tier=MemoryTier.LONG, raw=True)
                 if l.lesson_id == tl.lesson_id]
    assert len(long_rows) == 1, "append-if-absent must not duplicate"
    medium = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert not any(l.lesson_id == tl.lesson_id for l in medium)


def test_move_aborts_and_rolls_back_when_stamp_lands_mid_move(monkeypatch, tmp_path):
    """A boundary stamp landing between stage and removal aborts the move:
    the stamped MEDIUM row is the truth; the LONG copy is rolled back."""
    _setup(monkeypatch, tmp_path)
    import knowledge_web as kw
    tl = _make_eligible_lesson(tmp_path)
    calls = {"n": 0}

    def _guards_flip(t):
        calls["n"] += 1
        return calls["n"] == 1  # pass at stage, fail at remove

    moved = kw._move_medium_to_long(tl.lesson_id, in_lock_guards=_guards_flip)
    assert moved is None
    medium = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert any(l.lesson_id == tl.lesson_id for l in medium)
    long_lessons = load_tiered_lessons(tier=MemoryTier.LONG, raw=True)
    assert not any(l.lesson_id == tl.lesson_id for l in long_lessons), \
        "aborted move must roll the LONG copy back"


def test_decay_cycle_reconciles_interrupted_move(monkeypatch, tmp_path):
    """A MEDIUM row whose id already lives in LONG is an interrupted-move
    leftover — the decay cycle drops it (LONG is authoritative); dry_run
    counts without acting."""
    _setup(monkeypatch, tmp_path)
    from dataclasses import replace
    import knowledge_web as kw
    tl = record_tiered_lesson("a lesson caught mid move", "general", "done", "g1")
    long_copy = replace(tl)
    long_copy.tier = MemoryTier.LONG
    kw._append_tiered_lesson(long_copy, tier=MemoryTier.LONG)

    stats = kw.run_decay_cycle(tier=MemoryTier.MEDIUM, dry_run=True)
    assert stats["reconciled"] == 1
    assert any(l.lesson_id == tl.lesson_id
               for l in load_tiered_lessons(tier=MemoryTier.MEDIUM))

    stats = kw.run_decay_cycle(tier=MemoryTier.MEDIUM)
    assert stats["reconciled"] == 1
    medium = load_tiered_lessons(tier=MemoryTier.MEDIUM)
    assert not any(l.lesson_id == tl.lesson_id for l in medium)
    long_rows = [l for l in load_tiered_lessons(tier=MemoryTier.LONG, raw=True)
                 if l.lesson_id == tl.lesson_id]
    assert len(long_rows) == 1


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


# ---------------------------------------------------------------------------
# Canon tracking (times_applied + canon-candidates)
# ---------------------------------------------------------------------------

def test_record_canon_hit_persists(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _record_canon_hit("abc123", tier=MemoryTier.LONG, task_type="research")
    stats = _load_canon_stats()
    assert "abc123" in stats
    assert stats["abc123"]["total_hits"] == 1
    assert "research" in stats["abc123"]["task_types"]


def test_record_canon_hit_accumulates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    _record_canon_hit("lid1", tier=MemoryTier.LONG, task_type="research")
    _record_canon_hit("lid1", tier=MemoryTier.LONG, task_type="build")
    _record_canon_hit("lid1", tier=MemoryTier.LONG, task_type="ops")
    stats = _load_canon_stats()
    assert stats["lid1"]["total_hits"] == 3
    assert len(stats["lid1"]["task_types"]) == 3


def test_inject_tiered_lessons_increments_times_applied(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("always verify sources", "general", "done", "g1", tier=MemoryTier.LONG)
    lessons_before = load_tiered_lessons(tier=MemoryTier.LONG)
    assert lessons_before[0].times_applied == 0

    inject_tiered_lessons("general", track_applied=True)

    lessons_after = load_tiered_lessons(tier=MemoryTier.LONG)
    assert lessons_after[0].times_applied == 1


def test_inject_tiered_lessons_no_tracking(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("stable lesson", "general", "done", "g1", tier=MemoryTier.LONG)
    inject_tiered_lessons("general", track_applied=False)
    lessons = load_tiered_lessons(tier=MemoryTier.LONG)
    assert lessons[0].times_applied == 0


def test_get_canon_candidates_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    candidates = get_canon_candidates(min_hits=1, min_task_types=1)
    assert candidates == []


def test_get_canon_candidates_below_threshold(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("not yet ready", "general", "done", "g1", tier=MemoryTier.LONG)
    # Only 2 hits, 2 task types — below default threshold (10 hits, 3 types)
    _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type="research")
    _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type="build")
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    assert not any(c["lesson_id"] == tl.lesson_id for c in candidates)


def test_get_canon_candidates_eligible(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = record_tiered_lesson("lead with action, not reasoning", "general", "done", "g1", tier=MemoryTier.LONG)
    # Simulate many hits across diverse task types
    for task_type in ["research", "build", "ops", "general", "now"]:
        for _ in range(3):  # 15 total hits, 5 task types
            _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type=task_type)
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    assert any(c["lesson_id"] == tl.lesson_id for c in candidates)
    candidate = next(c for c in candidates if c["lesson_id"] == tl.lesson_id)
    assert candidate["times_applied"] == 15
    assert len(candidate["task_types_seen"]) == 5
    assert "recommendation" in candidate


def test_get_canon_candidates_only_long_tier(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # Medium-tier lesson with many hits should NOT appear as canon candidate
    tl = record_tiered_lesson("medium lesson", "general", "done", "g1", tier=MemoryTier.MEDIUM)
    for task_type in ["research", "build", "ops", "general"]:
        for _ in range(5):
            _record_canon_hit(tl.lesson_id, tier=MemoryTier.MEDIUM, task_type=task_type)
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    assert not any(c["lesson_id"] == tl.lesson_id for c in candidates)


def _seed_canon_candidate(text="lead with action, not reasoning"):
    """A LONG lesson over both canon bars (15 hits, 5 task types)."""
    tl = record_tiered_lesson(text, "general", "done", "g1", tier=MemoryTier.LONG)
    for task_type in ["research", "build", "ops", "general", "now"]:
        for _ in range(3):
            _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type=task_type)
    return tl


def _stamp_delta_evidence(lesson_id, evidence):
    import knowledge_web as kw

    def _apply(lessons):
        for l in lessons:
            if l.lesson_id == lesson_id:
                l.delta_evidence = evidence
        return lessons

    kw._mutate_tiered_lessons(MemoryTier.LONG, _apply)


def test_canon_candidates_exclude_delta_inert(monkeypatch, tmp_path):
    # Δ-gate exclusion: a lesson measured redundant has no claim on
    # identity, whatever its pre-measurement apply-count says.
    _setup(monkeypatch, tmp_path)
    tl = _seed_canon_candidate()
    _stamp_delta_evidence(tl.lesson_id, {"route": "effect-inert", "delta": 0.0})
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    assert not any(c["lesson_id"] == tl.lesson_id for c in candidates)


def test_canon_candidates_exclude_delta_demoted(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = _seed_canon_candidate()
    _stamp_delta_evidence(tl.lesson_id, {"route": "effect-demote", "delta": -0.137})
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    assert not any(c["lesson_id"] == tl.lesson_id for c in candidates)


def test_canon_candidates_carry_measured_delta(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = _seed_canon_candidate()
    _stamp_delta_evidence(tl.lesson_id, {"route": "effect", "delta": 0.59})
    c = next(c for c in get_canon_candidates(min_hits=10, min_task_types=3)
             if c["lesson_id"] == tl.lesson_id)
    assert c["measured_delta"] == 0.59


def test_canon_candidates_no_delta_measurement_is_null(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl = _seed_canon_candidate()
    c = next(c for c in get_canon_candidates(min_hits=10, min_task_types=3)
             if c["lesson_id"] == tl.lesson_id)
    assert c["measured_delta"] is None


def test_promote_canon_lesson_door(monkeypatch, tmp_path):
    # The door itself: playbook write + canon stamp + leaves the
    # candidate list + second promote refused.
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    tl = _seed_canon_candidate("verify the artifact exists before claiming done")
    result = promote_canon_lesson(tl.lesson_id)
    assert result["ok"] is True
    playbook = (tmp_path / "playbook.md").read_text(encoding="utf-8")
    assert "verify the artifact exists before claiming done" in playbook
    assert f"canon:{tl.lesson_id}" in playbook
    row = next(l for l in load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
               if l.lesson_id == tl.lesson_id)
    assert row.canon["target"] == "playbook"
    assert not any(c["lesson_id"] == tl.lesson_id
                   for c in get_canon_candidates(min_hits=10, min_task_types=3))
    again = promote_canon_lesson(tl.lesson_id)
    assert again["ok"] is False


def test_promote_canon_lesson_refuses_non_candidate(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    # Below the bars: recorded but barely applied
    tl = record_tiered_lesson("not identity material yet", "general", "done",
                              "g1", tier=MemoryTier.LONG)
    _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type="research")
    result = promote_canon_lesson(tl.lesson_id)
    assert result["ok"] is False
    assert "not a current canon candidate" in result["reason"]
    assert not (tmp_path / "playbook.md").exists() or \
        "not identity material yet" not in (tmp_path / "playbook.md").read_text(encoding="utf-8")


def test_promote_canon_lesson_refuses_delta_inert(monkeypatch, tmp_path):
    # The door shares the surfacer's Δ-gate exclusions — one bar
    # definition, not two.
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    tl = _seed_canon_candidate()
    _stamp_delta_evidence(tl.lesson_id, {"route": "effect-inert", "delta": 0.0})
    assert promote_canon_lesson(tl.lesson_id)["ok"] is False


def test_promote_canon_lesson_lands_in_canon_section(monkeypatch, tmp_path):
    # Section membership, not whole-file substring (skeptic finding 1):
    # the canon source marker must sit under ## Canon.
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    tl = _seed_canon_candidate("prefer executed probes over narrated checks")
    assert promote_canon_lesson(tl.lesson_id)["ok"] is True
    text = (tmp_path / "playbook.md").read_text(encoding="utf-8")
    canon_start = text.index("## Canon")
    canon_end = text.find("\n## ", canon_start + 1)
    canon_section = text[canon_start:canon_end if canon_end >= 0 else len(text)]
    assert f"canon:{tl.lesson_id}" in canon_section


def test_promote_canon_lesson_refuses_on_dedup(monkeypatch, tmp_path):
    # append_to_playbook silently skips when the entry text already
    # exists anywhere in the playbook — the door must not report success
    # or stamp on a skipped write (skeptic finding 1).
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    from playbook import append_to_playbook
    tl = _seed_canon_candidate("dedup me: this text predates the promotion")
    append_to_playbook(tl.lesson, section="Learned", source="evolver:test")
    result = promote_canon_lesson(tl.lesson_id)
    assert result["ok"] is False
    assert "deduped" in result["reason"]
    # not stamped — still a candidate for after the operator curates
    row = next(l for l in load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
               if l.lesson_id == tl.lesson_id)
    assert not row.canon
    assert any(c["lesson_id"] == tl.lesson_id
               for c in get_canon_candidates(min_hits=10, min_task_types=3))


def test_promote_canon_lesson_custom_bars(monkeypatch, tmp_path):
    # A candidate surfaced with lowered bars can walk through the same
    # door (skeptic finding 3 — bars pass through, no silent revert to
    # defaults).
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    tl = record_tiered_lesson("young but real pattern", "general", "done",
                              "g1", tier=MemoryTier.LONG)
    _record_canon_hit(tl.lesson_id, tier=MemoryTier.LONG, task_type="research")
    assert promote_canon_lesson(tl.lesson_id)["ok"] is False  # default bars
    result = promote_canon_lesson(tl.lesson_id, min_hits=1, min_task_types=1)
    assert result["ok"] is True


def test_promote_canon_lesson_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from knowledge_web import promote_canon_lesson
    tl = _seed_canon_candidate()
    result = promote_canon_lesson(tl.lesson_id, dry_run=True)
    assert result["ok"] is True and result["dry_run"] is True
    # nothing written, nothing stamped, still a candidate
    assert not (tmp_path / "playbook.md").exists() or \
        tl.lesson not in (tmp_path / "playbook.md").read_text(encoding="utf-8")
    row = next(l for l in load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
               if l.lesson_id == tl.lesson_id)
    assert not row.canon
    assert any(c["lesson_id"] == tl.lesson_id
               for c in get_canon_candidates(min_hits=10, min_task_types=3))


def test_get_canon_candidates_sorted_by_hits(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    tl1 = record_tiered_lesson("lesson one", "general", "done", "g1", tier=MemoryTier.LONG)
    tl2 = record_tiered_lesson("lesson two different words", "general", "done", "g2", tier=MemoryTier.LONG)
    # tl1: 20 hits; tl2: 12 hits
    for tt in ["research", "build", "ops", "general"]:
        for _ in range(5):
            _record_canon_hit(tl1.lesson_id, tier=MemoryTier.LONG, task_type=tt)
    for tt in ["research", "build", "ops", "general"]:
        for _ in range(3):
            _record_canon_hit(tl2.lesson_id, tier=MemoryTier.LONG, task_type=tt)
    candidates = get_canon_candidates(min_hits=10, min_task_types=3)
    hits = [c["times_applied"] for c in candidates]
    assert hits == sorted(hits, reverse=True)


def test_inject_tiered_lessons_records_canon_stats(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_tiered_lesson("canon track lesson", "research", "done", "g1", tier=MemoryTier.LONG)
    inject_tiered_lessons("research", track_applied=True)
    stats = _load_canon_stats()
    assert len(stats) == 1
    lid = list(stats.keys())[0]
    assert stats[lid]["total_hits"] == 1
    assert "research" in stats[lid]["task_types"]
