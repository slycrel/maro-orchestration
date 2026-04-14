"""Tests for Captain's Log — learning system changelog."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from unittest.mock import patch, MagicMock

from captains_log import (
    log_event,
    load_log,
    query_log,
    timeline,
    correlate_with_git,
    render_entry,
    render_log,
    render_correlated_entry,
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
        assert len(EVENT_TYPES) == 30

    def test_input_mismatch_in_set(self):
        from captains_log import INPUT_MISMATCH
        assert INPUT_MISMATCH in EVENT_TYPES

    def test_metacognitive_decision_in_set(self):
        from captains_log import METACOGNITIVE_DECISION
        assert METACOGNITIVE_DECISION in EVENT_TYPES


# ---------------------------------------------------------------------------
# classify_input_type
# ---------------------------------------------------------------------------

class TestClassifyInputType:
    """Unit tests for the classify_input_type() helper."""

    def test_url_single_short(self):
        from captains_log import classify_input_type
        assert classify_input_type("https://example.com/page") == "url"

    def test_url_two_urls(self):
        from captains_log import classify_input_type
        text = "See https://foo.com and https://bar.com for details"
        assert classify_input_type(text) == "url"

    def test_url_single_long_is_not_url(self):
        from captains_log import classify_input_type
        # single URL embedded in a long paragraph → not classified as url
        text = ("This is a very long document with lots of prose. " * 5
                + "See https://example.com for more. " + "More prose here. " * 5)
        # length > 200, only 1 URL → falls through to plain_text or other
        result = classify_input_type(text)
        assert result != "url"

    def test_code_detection(self):
        from captains_log import classify_input_type
        text = "def my_func():\n    import os\n    return os.getcwd()"
        assert classify_input_type(text) == "code"

    def test_code_fenced(self):
        from captains_log import classify_input_type
        text = "Here is some code:\n```\nfunction foo() { return 1; }\nreturn foo();\n```"
        assert classify_input_type(text) == "code"

    def test_structured_data(self):
        from captains_log import classify_input_type
        text = '{"key": "value", "num": 42, "arr": [1, 2]}'
        assert classify_input_type(text) == "structured_data"

    def test_plain_text_fallback(self):
        from captains_log import classify_input_type
        text = "Summarise the latest Polymarket trends in crypto markets."
        assert classify_input_type(text) == "plain_text"

    def test_empty_string(self):
        from captains_log import classify_input_type
        assert classify_input_type("") == "plain_text"


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


# ===========================================================================
# Historical query + git correlation tests
# ===========================================================================

class TestQueryLog:

    def test_full_text_search_summary(self, tmp_path):
        log_event(DIAGNOSIS, "token_explosion", "Loop abc: token_explosion detected",
                  context={"tokens": 432924})
        log_event(DIAGNOSIS, "healthy", "Loop def: all healthy")
        entries = query_log("token_explosion")
        assert len(entries) == 1
        assert "token_explosion" in entries[0]["summary"]

    def test_full_text_search_note(self, tmp_path):
        log_event(EVOLVER_APPLIED, "test", "Applied suggestion",
                  note="distill context before injection")
        entries = query_log("distill")
        assert len(entries) == 1

    def test_full_text_search_context_values(self, tmp_path):
        log_event(DIAGNOSIS, "test", "Loop result",
                  context={"severity": "critical", "tokens": 500000})
        entries = query_log("critical")
        assert len(entries) == 1

    def test_empty_query_returns_all(self, tmp_path):
        log_event(SKILL_PROMOTED, "a", "first")
        log_event(SKILL_DEMOTED, "b", "second")
        entries = query_log("")
        assert len(entries) == 2

    def test_date_range_filter(self, tmp_path):
        # Write entries with controlled timestamps via direct file write
        path = tmp_path / "captains_log.jsonl"
        entries_data = [
            {"timestamp": "2026-04-09T12:00:00+00:00", "event_type": "DIAGNOSIS",
             "subject": "old", "summary": "old event"},
            {"timestamp": "2026-04-10T12:00:00+00:00", "event_type": "DIAGNOSIS",
             "subject": "mid", "summary": "mid event"},
            {"timestamp": "2026-04-11T12:00:00+00:00", "event_type": "DIAGNOSIS",
             "subject": "new", "summary": "new event"},
        ]
        with path.open("w") as f:
            for e in entries_data:
                f.write(json.dumps(e) + "\n")

        entries = query_log("", since="2026-04-10", until="2026-04-11")
        assert len(entries) == 1
        assert entries[0]["subject"] == "mid"

    def test_limit_zero_returns_all(self, tmp_path):
        for i in range(5):
            log_event(DIAGNOSIS, f"s{i}", f"event {i}")
        entries = query_log("", limit=0)
        assert len(entries) == 5

    def test_query_with_event_type_filter(self, tmp_path):
        log_event(DIAGNOSIS, "diag", "a diagnosis")
        log_event(EVOLVER_APPLIED, "evo", "an evolver event")
        entries = query_log("", event_type="EVOLVER")
        assert len(entries) == 1
        assert entries[0]["event_type"] == "EVOLVER_APPLIED"


class TestTimeline:

    def test_timeline_by_day(self, tmp_path):
        path = tmp_path / "captains_log.jsonl"
        entries = [
            {"timestamp": "2026-04-10T08:00:00+00:00", "event_type": "DIAGNOSIS", "subject": "a", "summary": ""},
            {"timestamp": "2026-04-10T09:00:00+00:00", "event_type": "EVOLVER_APPLIED", "subject": "b", "summary": ""},
            {"timestamp": "2026-04-11T08:00:00+00:00", "event_type": "DIAGNOSIS", "subject": "c", "summary": ""},
        ]
        with path.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        tl = timeline()
        assert len(tl) == 2
        assert tl[0]["date"] == "2026-04-10"
        assert tl[0]["total"] == 2
        assert tl[1]["date"] == "2026-04-11"
        assert tl[1]["total"] == 1

    def test_timeline_empty_log(self, tmp_path):
        tl = timeline()
        assert tl == []

    def test_timeline_by_hour(self, tmp_path):
        path = tmp_path / "captains_log.jsonl"
        entries = [
            {"timestamp": "2026-04-10T08:00:00+00:00", "event_type": "DIAGNOSIS", "subject": "a", "summary": ""},
            {"timestamp": "2026-04-10T08:30:00+00:00", "event_type": "DIAGNOSIS", "subject": "b", "summary": ""},
            {"timestamp": "2026-04-10T09:00:00+00:00", "event_type": "DIAGNOSIS", "subject": "c", "summary": ""},
        ]
        with path.open("w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        tl = timeline(bucket="hour")
        assert len(tl) == 2  # 08 and 09


class TestGitCorrelation:

    def test_correlation_adds_nearby_commits(self, tmp_path):
        git_output = (
            "abcdef123456|2026-04-10T08:30:00-06:00|Fix token explosion bug\n"
            "fedcba654321|2026-04-10T07:00:00-06:00|Add evolver threshold\n"
        )
        entries = [
            {"timestamp": "2026-04-10T14:25:00+00:00", "event_type": "DIAGNOSIS",
             "subject": "token_explosion", "summary": "detected"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_output

        with patch("subprocess.run", return_value=mock_result):
            correlated = correlate_with_git(entries, window_hours=2, repo_path="/tmp")

        assert len(correlated) == 1
        assert "nearby_commits" in correlated[0]
        assert correlated[0]["nearby_commits"][0]["hash"] == "abcdef123456"

    def test_correlation_no_nearby_commits(self, tmp_path):
        git_output = "abcdef123456|2026-04-01T08:30:00-06:00|Very old commit\n"
        entries = [
            {"timestamp": "2026-04-10T14:25:00+00:00", "event_type": "DIAGNOSIS",
             "subject": "test", "summary": "recent event"},
        ]
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = git_output

        with patch("subprocess.run", return_value=mock_result):
            correlated = correlate_with_git(entries, window_hours=2, repo_path="/tmp")

        assert "nearby_commits" not in correlated[0]

    def test_correlation_empty_entries(self):
        result = correlate_with_git([])
        assert result == []

    def test_render_correlated_entry(self):
        entry = {
            "timestamp": "2026-04-10T14:25:00+00:00",
            "event_type": "DIAGNOSIS",
            "subject": "token_explosion",
            "summary": "Loop abc: token explosion detected",
            "nearby_commits": [
                {"hash": "abcdef12", "message": "Fix token bug", "timestamp": "2026-04-10T14:20:00", "delta_hours": 0.1},
            ],
        }
        rendered = render_correlated_entry(entry)
        assert "Git:" in rendered
        assert "abcdef12" in rendered
        assert "Fix token bug" in rendered


# ---------------------------------------------------------------------------
# Edge-case coverage — malformed lines, since filter, timeline date range
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Targeted tests for previously uncovered branches."""

    def test_load_log_skips_empty_lines(self, _tmp_log):
        """Empty lines in the JSONL log don't crash load_log."""
        _tmp_log.write_text(
            '\n{"event_type":"DIAGNOSIS","subject":"real","summary":"ok","timestamp":"2026-04-10T12:00:00+00:00"}\n\n'
        )
        entries = load_log()
        assert len(entries) == 1
        assert entries[0]["subject"] == "real"

    def test_load_log_skips_malformed_json(self, _tmp_log):
        """Malformed JSON lines in the log are silently skipped."""
        _tmp_log.write_text(
            '{"event_type":"DIAGNOSIS","subject":"good","summary":"ok","timestamp":"2026-04-10T12:00:00+00:00"}\n'
            'not valid json at all\n'
            '{"event_type":"EVOLVER_APPLIED","subject":"also_good","summary":"ok","timestamp":"2026-04-10T13:00:00+00:00"}\n'
        )
        entries = load_log()
        assert len(entries) == 2
        subjects = {e["subject"] for e in entries}
        assert subjects == {"good", "also_good"}

    def test_load_log_since_filter(self, _tmp_log):
        """load_log(since=...) skips entries before the given date."""
        _tmp_log.write_text(
            '{"event_type":"DIAGNOSIS","subject":"old","summary":"x","timestamp":"2026-04-09T12:00:00+00:00"}\n'
            '{"event_type":"DIAGNOSIS","subject":"new","summary":"y","timestamp":"2026-04-11T12:00:00+00:00"}\n'
        )
        entries = load_log(since="2026-04-10")
        assert len(entries) == 1
        assert entries[0]["subject"] == "new"

    def test_query_log_skips_malformed_json(self, _tmp_log):
        """Malformed JSON in log is skipped during query."""
        _tmp_log.write_text(
            '{"event_type":"DIAGNOSIS","subject":"valid","summary":"has it","timestamp":"2026-04-10T12:00:00+00:00"}\n'
            '{broken json\n'
        )
        entries = query_log("has it")
        assert len(entries) == 1

    def test_query_log_empty_path_returns_empty(self, tmp_path):
        """query_log returns [] when log file doesn't exist."""
        from captains_log import set_log_path
        set_log_path(tmp_path / "no_log.jsonl")
        assert query_log("anything") == []

    def test_timeline_since_until_filters(self, tmp_path):
        """timeline(since=..., until=...) respects date bounds."""
        path = tmp_path / "captains_log.jsonl"
        entries_data = [
            {"timestamp": "2026-04-08T12:00:00+00:00", "event_type": "DIAGNOSIS"},
            {"timestamp": "2026-04-10T12:00:00+00:00", "event_type": "EVOLVER_APPLIED"},
            {"timestamp": "2026-04-12T12:00:00+00:00", "event_type": "SKILL_PROMOTED"},
        ]
        path.write_text("\n".join(json.dumps(e) for e in entries_data) + "\n")
        from captains_log import set_log_path
        set_log_path(path)
        tl = timeline(since="2026-04-09", until="2026-04-11")
        assert len(tl) == 1
        assert tl[0]["date"] == "2026-04-10"

    def test_timeline_skips_malformed_json(self, _tmp_log):
        """Malformed lines in the log are skipped by timeline."""
        _tmp_log.write_text(
            '{"timestamp":"2026-04-10T12:00:00+00:00","event_type":"DIAGNOSIS"}\n'
            'bad json\n'
            '{"timestamp":"2026-04-10T13:00:00+00:00","event_type":"EVOLVER_APPLIED"}\n'
        )
        tl = timeline()
        assert len(tl) == 1
        assert tl[0]["total"] == 2  # 2 valid entries on same day
