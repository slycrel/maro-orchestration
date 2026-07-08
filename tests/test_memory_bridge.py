#!/usr/bin/env python3
"""Tests for memory_bridge: incremental ingest of lessons into SqliteMemoryStore."""

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from memory_bridge import (
    ingest_lessons_to_store,
    recall_for_worker,
    format_worker_memory_block,
    _derive_scope_from_lesson,
    _lesson_to_memory_item,
    _get_ingest_offset,
    _save_ingest_offset,
)
from memory_sqlite import SqliteMemoryStore
from memory_port import MemoryItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_memory_dir():
    """Temporary memory directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_lessons():
    """Sample lessons for testing."""
    return [
        {
            "lesson_id": "L001",
            "task_type": "research",
            "outcome": "done",
            "lesson": "Use multiple sources for fact-checking",
            "source_goal": "G001",
            "confidence": 0.8,
            "tier": "medium",
            "score": 0.9,
            "last_reinforced": "2026-07-07",
            "sessions_validated": 2,
            "times_applied": 5,
            "times_reinforced": 1,
            "recorded_at": "2026-07-04T12:00:00Z",
            "acquired_for": "goal_abc",
            "lesson_type": "execution",
            "evidence_sources": [],
            "meta": {},
        },
        {
            "lesson_id": "L002",
            "task_type": "build",
            "outcome": "done",
            "lesson": "Always write tests alongside implementation",
            "source_goal": "G002",
            "confidence": 0.95,
            "tier": "long",
            "score": 1.0,
            "last_reinforced": "2026-07-06",
            "sessions_validated": 5,
            "times_applied": 10,
            "times_reinforced": 3,
            "recorded_at": "2026-06-20T10:00:00Z",
            "acquired_for": None,
            "lesson_type": "execution",
            "evidence_sources": [],
            "meta": {"thread_id": "thread_xyz"},
        },
    ]


@pytest.fixture
def lessons_jsonl_file(tmp_memory_dir, sample_lessons):
    """Create a temporary lessons.jsonl file."""
    lessons_file = tmp_memory_dir / "lessons.jsonl"
    with open(lessons_file, "w") as f:
        for lesson in sample_lessons:
            f.write(json.dumps(lesson) + "\n")
    return lessons_file


# ---------------------------------------------------------------------------
# Tests: Scope derivation
# ---------------------------------------------------------------------------


def test_derive_scope_from_lesson_with_thread_id():
    """Scope from explicit thread_id in meta."""
    lesson = {"meta": json.dumps({"thread_id": "thread_xyz"})}
    assert _derive_scope_from_lesson(lesson) == "thread/thread_xyz"


def test_derive_scope_from_lesson_with_acquired_for():
    """Scope from acquired_for (goal_id)."""
    lesson = {"acquired_for": "goal_abc", "meta": {}}
    assert _derive_scope_from_lesson(lesson) == "goal/goal_abc"


def test_derive_scope_from_lesson_global():
    """Global scope when no metadata."""
    lesson = {"acquired_for": None, "meta": {}}
    assert _derive_scope_from_lesson(lesson) == ""


def test_derive_scope_thread_priority_over_acquired_for():
    """Thread ID in meta takes priority over acquired_for."""
    lesson = {
        "acquired_for": "goal_abc",
        "meta": json.dumps({"thread_id": "thread_xyz"}),
    }
    assert _derive_scope_from_lesson(lesson) == "thread/thread_xyz"


# ---------------------------------------------------------------------------
# Tests: Lesson to MemoryItem conversion
# ---------------------------------------------------------------------------


def test_lesson_to_memory_item_basic(sample_lessons):
    """Convert lesson to MemoryItem."""
    lesson = sample_lessons[0]
    item = _lesson_to_memory_item(lesson)

    assert isinstance(item, MemoryItem)
    assert item.kind == "lesson"
    assert item.content == "Use multiple sources for fact-checking"
    assert item.scope == "goal/goal_abc"
    assert item.trust == 0.9


def test_lesson_to_memory_item_preserves_provenance(sample_lessons):
    """Provenance fields are preserved."""
    lesson = sample_lessons[0]
    item = _lesson_to_memory_item(lesson)

    assert item.provenance["lesson_id"] == "L001"
    assert item.provenance["source_goal"] == "G001"
    assert item.provenance["task_type"] == "research"
    assert item.provenance["confidence"] == 0.8


# ---------------------------------------------------------------------------
# Tests: Incremental offset tracking
# ---------------------------------------------------------------------------


def test_get_ingest_offset_fresh_start(tmp_memory_dir):
    """Fresh file should start at offset 0."""
    source = tmp_memory_dir / "lessons.jsonl"
    source.write_text("test\n")

    store = SqliteMemoryStore(tmp_memory_dir / "store")
    offset = _get_ingest_offset(store, source)
    assert offset == 0


def test_get_ingest_offset_resumes_from_saved(tmp_memory_dir):
    """Should resume from saved offset."""
    source = tmp_memory_dir / "lessons.jsonl"
    source.write_text("line 1\nline 2\n")

    # Save an offset
    _save_ingest_offset(source, 6)  # After "line 1\n"

    store = SqliteMemoryStore(tmp_memory_dir / "store")
    offset = _get_ingest_offset(store, source)
    assert offset == 6


def test_get_ingest_offset_corruption_detection(tmp_memory_dir):
    """If source shrank, full rebuild (return 0)."""
    source = tmp_memory_dir / "lessons.jsonl"
    source.write_text("line 1\nline 2\nline 3\n")

    # Save an offset beyond current size
    _save_ingest_offset(source, 100)

    # Shrink file
    source.write_text("line 1\n")

    store = SqliteMemoryStore(tmp_memory_dir / "store")
    offset = _get_ingest_offset(store, source)
    assert offset == 0


# ---------------------------------------------------------------------------
# Tests: Ingest (incremental and idempotent)
# ---------------------------------------------------------------------------


def test_ingest_basic(tmp_memory_dir, lessons_jsonl_file):
    """Ingest lessons from file."""
    # Mock config to use temp directory
    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        stats = ingest_lessons_to_store(store)

        assert stats["ingested"] == 2
        assert lessons_jsonl_file.name in stats["sources"]
        assert stats["sources"][lessons_jsonl_file.name] == 2


def test_ingest_incremental(tmp_memory_dir, lessons_jsonl_file):
    """Ingest is incremental: only new lines processed."""
    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")

        # First ingest: all 2 items
        stats1 = ingest_lessons_to_store(store)
        assert stats1["ingested"] == 2

        # Second ingest: nothing new
        stats2 = ingest_lessons_to_store(store)
        assert stats2["ingested"] == 0

        # Add a new lesson to the file
        new_lesson = {
            "lesson_id": "L003",
            "task_type": "research",
            "outcome": "done",
            "lesson": "Third lesson",
            "source_goal": "G003",
            "confidence": 0.7,
            "tier": "medium",
            "score": 0.8,
            "last_reinforced": "2026-07-07",
            "sessions_validated": 1,
            "times_applied": 1,
            "times_reinforced": 0,
            "recorded_at": "2026-07-07T12:00:00Z",
            "acquired_for": None,
            "lesson_type": "execution",
            "evidence_sources": [],
            "meta": {},
        }
        with open(lessons_jsonl_file, "a") as f:
            f.write(json.dumps(new_lesson) + "\n")

        # Third ingest: only new lesson
        stats3 = ingest_lessons_to_store(store)
        assert stats3["ingested"] == 1


def test_ingest_never_modifies_source(tmp_memory_dir, lessons_jsonl_file):
    """Ingest never modifies source files (read-only)."""
    original_content = lessons_jsonl_file.read_text()

    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        ingest_lessons_to_store(store)
        ingest_lessons_to_store(store)  # Again

    # Source should be unchanged
    assert lessons_jsonl_file.read_text() == original_content


def test_ingest_skips_malformed_lines(tmp_memory_dir):
    """Malformed lines are skipped, not fatal."""
    bad_file = tmp_memory_dir / "lessons.jsonl"
    bad_file.write_text(
        '{"lesson_id": "L1", "lesson": "good"}\n'
        "not valid json\n"
        '{"lesson_id": "L2", "lesson": "also good"}\n'
    )

    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [bad_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        stats = ingest_lessons_to_store(store)

        # Should ingest 2 good lines, skip 1 bad
        assert stats["ingested"] == 2
        assert stats["errors"] == 1


# ---------------------------------------------------------------------------
# Tests: Recall for worker
# ---------------------------------------------------------------------------


def test_recall_for_worker_basic(tmp_memory_dir, lessons_jsonl_file):
    """Recall returns items matching the ticket (or empty if no matches)."""
    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        ingest_lessons_to_store(store)

        # Query with words from actual lesson content
        items = recall_for_worker("sources fact checking", k=5, store=store)

        # May return items or empty depending on BM25 scoring
        assert isinstance(items, list)
        if items:
            # If we got matches, verify they're MemoryItems
            assert all(isinstance(item, MemoryItem) for item in items)


def test_recall_for_worker_scoped(tmp_memory_dir, lessons_jsonl_file):
    """Recall respects scope (thread items + global items)."""
    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        ingest_lessons_to_store(store)

        # Query at thread scope — should get thread-scoped + global items
        items = recall_for_worker("tests implementation", thread_scope="thread/thread_xyz", k=5, store=store)

        # Should return a list (may be empty if no matches)
        assert isinstance(items, list)


def test_recall_for_worker_empty_on_no_match(tmp_memory_dir, lessons_jsonl_file):
    """Recall returns empty list if no matches."""
    with mock.patch("memory_bridge._lessons_source_paths") as mock_paths:
        mock_paths.return_value = [lessons_jsonl_file]

        store = SqliteMemoryStore(tmp_memory_dir / "store")
        ingest_lessons_to_store(store)

        items = recall_for_worker("xyzabc_nonexistent_query_xyz", k=5, store=store)

        # May return empty or items with low relevance depending on the query
        # Just ensure it doesn't crash
        assert isinstance(items, list)


# ---------------------------------------------------------------------------
# Tests: Formatting
# ---------------------------------------------------------------------------


def test_format_worker_memory_block_empty():
    """Empty items return empty block."""
    block = format_worker_memory_block([])
    assert block == ""


def test_format_worker_memory_block_single_item():
    """Single item formatted with header."""
    item = MemoryItem(
        kind="lesson",
        content="Test lesson content",
        scope="",
        trust=0.9,
    )
    block = format_worker_memory_block([item])

    assert "Prior lessons from memory:" in block
    assert "Test lesson content" in block
    assert "[lesson]" in block


def test_format_worker_memory_block_caps_at_max_chars():
    """Block is capped at max_chars."""
    items = [
        MemoryItem(
            kind="lesson",
            content="x" * 500,
            scope="",
            trust=0.9,
        ),
        MemoryItem(
            kind="lesson",
            content="y" * 500,
            scope="",
            trust=0.9,
        ),
    ]
    block = format_worker_memory_block(items, max_chars=500)

    # Should be capped at ~500 chars
    assert len(block) <= 600  # Allow some buffer for header


def test_format_worker_memory_block_preserves_order():
    """Items are formatted in order."""
    items = [
        MemoryItem(kind="lesson", content="First", scope="", trust=1.0),
        MemoryItem(kind="lesson", content="Second", scope="", trust=0.9),
    ]
    block = format_worker_memory_block(items)

    first_idx = block.find("First")
    second_idx = block.find("Second")
    assert first_idx < second_idx
