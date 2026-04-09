"""Tests for Captain's Log — learning system changelog."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from captains_log import (
    log_event,
    load_log,
    render_entry,
    render_log,
    set_log_path,
    EVENT_TYPES,
    SKILL_CIRCUIT_OPEN,
    SKILL_PROMOTED,
    SKILL_DEMOTED,
    SKILL_SYNTHESIZED,
    SKILL_VARIANT_CREATED,
    AB_RETIRED,
    ISLAND_CULLED,
    LESSON_RECORDED,
    LESSON_REINFORCED,
    LESSON_DECAYED,
    HYPOTHESIS_CREATED,
    HYPOTHESIS_PROMOTED,
    HYPOTHESIS_CONTRADICTED,
    STANDING_RULE_CONTRADICTED,
    RULE_GRADUATED,
    RULE_DEMOTED,
    EVOLVER_APPLIED,
    EVOLVER_GENERATED,
    EVOLVER_SKIPPED,
    GRADUATION_PROPOSED,
    AUTO_RECOVERY,
    DIAGNOSIS,
    DECISION_RECORDED,
)


@pytest.fixture(autouse=True)
def _tmp_log(tmp_path):
    """Redirect captain's log to a temp file for every test."""
    path = tmp_path / "captains_log.jsonl"
    set_log_path(path)
    yield path
    set_log_path(None)


# ---------------------------------------------------------------------------
# log_event basics
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_basic_entry(self, _tmp_log):
        entry = log_event(
            event_type=SKILL_CIRCUIT_OPEN,
            subject="jina-x-scraper",
            summary="Hit 3 consecutive failures.",
        )
        assert entry["event_type"] == SKILL_CIRCUIT_OPEN
        assert entry["subject"] == "jina-x-scraper"
        assert "timestamp" in entry

        # Verify written to file
        lines = _tmp_log.read_text().strip().split("\n")
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["event_type"] == SKILL_CIRCUIT_OPEN

    def test_optional_fields(self, _tmp_log):
        entry = log_event(
            event_type=SKILL_PROMOTED,
            subject="test-skill",
            summary="Promoted.",
            context={"utility": 0.85},
            note="Good progress.",
            loop_id="loop-123",
            related_ids=["skill:abc"],
        )
        assert entry["context"]["utility"] == 0.85
        assert entry["note"] == "Good progress."
        assert entry["loop_id"] == "loop-123"
        assert entry["related_ids"] == ["skill:abc"]

    def test_omitted_optional_fields(self, _tmp_log):
        entry = log_event(
            event_type=LESSON_RECORDED,
            subject="test",
            summary="A lesson.",
        )
        assert "context" not in entry
        assert "note" not in entry
        assert "loop_id" not in entry
        assert "related_ids" not in entry

    def test_multiple_entries(self, _tmp_log):
        log_event(event_type=SKILL_PROMOTED, subject="a", summary="one")
        log_event(event_type=SKILL_DEMOTED, subject="b", summary="two")
        log_event(event_type=LESSON_RECORDED, subject="c", summary="three")

        lines = _tmp_log.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_never_raises_on_bad_path(self, tmp_path):
        set_log_path(tmp_path / "nonexistent" / "deep" / "path" / "log.jsonl")
        # Should not raise — creates dirs as needed
        entry = log_event(event_type=SKILL_PROMOTED, subject="test", summary="ok")
        assert entry["event_type"] == SKILL_PROMOTED


# ---------------------------------------------------------------------------
# load_log filtering
# ---------------------------------------------------------------------------

class TestLoadLog:
    def _seed(self):
        log_event(event_type=SKILL_PROMOTED, subject="alpha", summary="promoted alpha")
        log_event(event_type=SKILL_DEMOTED, subject="beta", summary="demoted beta")
        log_event(event_type=LESSON_RECORDED, subject="gamma", summary="lesson gamma")
        log_event(event_type=EVOLVER_APPLIED, subject="delta", summary="evolver delta")
        log_event(event_type=SKILL_CIRCUIT_OPEN, subject="alpha", summary="circuit open alpha")

    def test_load_all(self):
        self._seed()
        entries = load_log(limit=100)
        assert len(entries) == 5
        # Most recent first
        assert entries[0]["event_type"] == SKILL_CIRCUIT_OPEN

    def test_filter_by_type(self):
        self._seed()
        entries = load_log(event_type="SKILL", limit=100)
        assert len(entries) == 3
        assert all(e["event_type"].startswith("SKILL") for e in entries)

    def test_filter_by_subject(self):
        self._seed()
        entries = load_log(subject="alpha", limit=100)
        assert len(entries) == 2

    def test_limit(self):
        self._seed()
        entries = load_log(limit=2)
        assert len(entries) == 2

    def test_empty_log(self, _tmp_log):
        entries = load_log()
        assert entries == []

    def test_nonexistent_log(self, tmp_path):
        set_log_path(tmp_path / "no_such_file.jsonl")
        entries = load_log()
        assert entries == []


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_entry(self):
        entry = {
            "timestamp": "2026-04-09T14:32:00+00:00",
            "event_type": "SKILL_CIRCUIT_OPEN",
            "subject": "jina-x-scraper",
            "summary": "Hit 3 consecutive failures.",
            "note": "Failures may reflect input mismatch.",
            "loop_id": "loop-123",
        }
        text = render_entry(entry)
        assert "SKILL_CIRCUIT_OPEN" in text
        assert "jina-x-scraper" in text
        assert "Hit 3 consecutive failures." in text
        assert "Note: Failures may reflect input mismatch." in text
        assert "Loop: loop-123" in text

    def test_render_log_no_entries(self, _tmp_log):
        text = render_log()
        assert text == "No log entries found."

    def test_render_log_with_entries(self):
        log_event(event_type=SKILL_PROMOTED, subject="test", summary="promoted")
        text = render_log()
        assert "SKILL_PROMOTED" in text
        assert "test" in text


# ---------------------------------------------------------------------------
# Event type constants
# ---------------------------------------------------------------------------

class TestEventTypes:
    def test_all_types_in_set(self):
        """Verify all module-level constants are in EVENT_TYPES set."""
        assert SKILL_CIRCUIT_OPEN in EVENT_TYPES
        assert SKILL_PROMOTED in EVENT_TYPES
        assert SKILL_DEMOTED in EVENT_TYPES
        assert SKILL_SYNTHESIZED in EVENT_TYPES
        assert SKILL_VARIANT_CREATED in EVENT_TYPES
        assert AB_RETIRED in EVENT_TYPES
        assert ISLAND_CULLED in EVENT_TYPES
        assert LESSON_RECORDED in EVENT_TYPES
        assert LESSON_REINFORCED in EVENT_TYPES
        assert LESSON_DECAYED in EVENT_TYPES
        assert HYPOTHESIS_CREATED in EVENT_TYPES
        assert HYPOTHESIS_PROMOTED in EVENT_TYPES
        assert HYPOTHESIS_CONTRADICTED in EVENT_TYPES
        assert STANDING_RULE_CONTRADICTED in EVENT_TYPES
        assert RULE_GRADUATED in EVENT_TYPES
        assert RULE_DEMOTED in EVENT_TYPES
        assert EVOLVER_APPLIED in EVENT_TYPES
        assert EVOLVER_GENERATED in EVENT_TYPES
        assert EVOLVER_SKIPPED in EVENT_TYPES
        assert GRADUATION_PROPOSED in EVENT_TYPES
        assert AUTO_RECOVERY in EVENT_TYPES
        assert DIAGNOSIS in EVENT_TYPES
        assert DECISION_RECORDED in EVENT_TYPES

    def test_event_type_count(self):
        assert len(EVENT_TYPES) == 28


# ---------------------------------------------------------------------------
# Integration: verify call sites don't break imports
# ---------------------------------------------------------------------------

class TestCallSiteImports:
    """Verify that the modules we wired into can still import cleanly."""

    def test_skills_imports(self):
        import skills
        assert hasattr(skills, "update_skill_utility")
        assert hasattr(skills, "maybe_auto_promote_skills")

    def test_evolver_imports(self):
        import evolver
        assert hasattr(evolver, "synthesize_skill")
        assert hasattr(evolver, "run_evolver")

    def test_memory_imports(self):
        import memory
        assert hasattr(memory, "record_tiered_lesson")
        assert hasattr(memory, "observe_pattern")

    def test_rules_imports(self):
        import rules
        assert hasattr(rules, "graduate_skill_to_rule")
        assert hasattr(rules, "demote_rule_to_skill")

    def test_graduation_imports(self):
        import graduation
        assert hasattr(graduation, "run_graduation")

    def test_introspect_imports(self):
        import introspect
        assert hasattr(introspect, "diagnose_loop")

    def test_gc_memory_imports(self):
        import gc_memory
        assert hasattr(gc_memory, "_gc_tiered_lessons")
