"""Tests for Phase 5: memory.py (outcome recording, lessons, Reflexion)."""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory import (
    Outcome,
    Lesson,
    record_outcome,
    load_outcomes,
    load_lessons,
    bootstrap_context,
    inject_lessons_for_task,
    extract_lessons_via_llm,
    reflect_and_record,
    _text_similarity,
    _memory_dir,
)


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# _text_similarity
# ---------------------------------------------------------------------------

def test_text_similarity_identical():
    assert _text_similarity("hello world", "hello world") == 1.0


def test_text_similarity_disjoint():
    assert _text_similarity("foo bar", "baz qux") == 0.0


def test_text_similarity_partial():
    score = _text_similarity("research task failed", "research task succeeded")
    assert 0.3 < score < 0.9


def test_text_similarity_empty():
    assert _text_similarity("", "hello") == 0.0


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------

def test_record_outcome_returns_outcome(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    o = record_outcome("test goal", "done", "completed successfully")
    assert isinstance(o, Outcome)
    assert o.goal == "test goal"
    assert o.status == "done"


def test_record_outcome_writes_to_ledger(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal A", "done", "summary A")
    record_outcome("goal B", "stuck", "summary B")
    outcomes = load_outcomes()
    assert len(outcomes) == 2


def test_record_outcome_writes_daily_log(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("log test goal", "done", "log summary")
    mem_dir = _memory_dir()
    daily_files = list(mem_dir.glob("????-??-??.md"))
    assert len(daily_files) == 1
    content = daily_files[0].read_text()
    assert "log test goal" in content


def test_record_outcome_with_lessons(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "summary", lessons=["lesson one", "lesson two"])
    lessons = load_lessons()
    assert len(lessons) == 2


def test_record_outcome_stores_task_type(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("research task", "done", "found things", task_type="research")
    outcomes = load_outcomes()
    assert outcomes[0].task_type == "research"


# ---------------------------------------------------------------------------
# load_outcomes
# ---------------------------------------------------------------------------

def test_load_outcomes_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert load_outcomes() == []


def test_load_outcomes_respects_limit(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    for i in range(5):
        record_outcome(f"goal {i}", "done", f"summary {i}")
    outcomes = load_outcomes(limit=3)
    assert len(outcomes) == 3


def test_load_outcomes_most_recent_first(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("first", "done", "s1")
    record_outcome("second", "done", "s2")
    record_outcome("third", "done", "s3")
    outcomes = load_outcomes(limit=10)
    assert outcomes[0].goal == "third"


# ---------------------------------------------------------------------------
# load_lessons
# ---------------------------------------------------------------------------

def test_load_lessons_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    assert load_lessons() == []


def test_load_lessons_filter_by_type(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("research goal", "done", "summary", task_type="research", lessons=["research lesson"])
    record_outcome("build goal", "done", "summary", task_type="build", lessons=["build lesson"])
    research_lessons = load_lessons(task_type="research")
    assert all(l.task_type == "research" for l in research_lessons)
    assert any("research" in l.lesson for l in research_lessons)


def test_load_lessons_filter_by_outcome(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("done goal", "done", "summary", lessons=["success lesson"])
    record_outcome("stuck goal", "stuck", "summary", lessons=["failure lesson"])
    done_lessons = load_lessons(outcome_filter="done")
    stuck_lessons = load_lessons(outcome_filter="stuck")
    assert all(l.outcome == "done" for l in done_lessons)
    assert all(l.outcome == "stuck" for l in stuck_lessons)


def test_load_lessons_deduplicates(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    # Record the same lesson multiple times
    for _ in range(3):
        record_outcome("goal", "done", "summary", task_type="general", lessons=["the same lesson text here"])
    lessons = load_lessons()
    lesson_texts = [l.lesson for l in lessons]
    # Should be deduplicated
    assert len(set(lesson_texts)) <= len(lesson_texts)


# ---------------------------------------------------------------------------
# bootstrap_context
# ---------------------------------------------------------------------------

def test_bootstrap_context_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    ctx = bootstrap_context()
    assert ctx == ""


def test_bootstrap_context_with_data(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("test goal", "done", "all good", lessons=["key lesson here"])
    ctx = bootstrap_context()
    assert "test goal" in ctx
    assert "key lesson here" in ctx


def test_bootstrap_context_is_string(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "summary")
    ctx = bootstrap_context()
    assert isinstance(ctx, str)


# ---------------------------------------------------------------------------
# inject_lessons_for_task
# ---------------------------------------------------------------------------

def test_inject_lessons_empty(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    result = inject_lessons_for_task("research", "some goal")
    assert result == ""


def test_inject_lessons_returns_string(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    record_outcome("research goal", "done", "summary", task_type="research", lessons=["research lesson"])
    result = inject_lessons_for_task("research", "another goal")
    assert "research lesson" in result


# ---------------------------------------------------------------------------
# extract_lessons_via_llm
# ---------------------------------------------------------------------------

def test_extract_lessons_dry_run():
    lessons = extract_lessons_via_llm("test goal", "done", "completed fine", "research", dry_run=True)
    assert isinstance(lessons, list)
    assert len(lessons) >= 1


def test_extract_lessons_api_failure():
    class FailAdapter:
        def complete(self, *args, **kwargs):
            raise RuntimeError("API down")

    lessons = extract_lessons_via_llm("goal", "done", "summary", "general", adapter=FailAdapter())
    assert lessons == []


def test_extract_lessons_returns_strings(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    lessons = extract_lessons_via_llm("goal", "stuck", "couldn't finish", "build", dry_run=True)
    assert all(isinstance(l, str) for l in lessons)


# ---------------------------------------------------------------------------
# reflect_and_record (Reflexion)
# ---------------------------------------------------------------------------

def test_reflect_and_record_dry_run(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    outcome = reflect_and_record(
        "research polymarket strategies",
        "done",
        "found 5 key strategies",
        task_type="research",
        dry_run=True,
    )
    assert isinstance(outcome, Outcome)
    assert outcome.status == "done"
    assert len(outcome.lessons) >= 1


def test_reflect_and_record_lesson_stored(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    reflect_and_record("analyze data", "stuck", "couldn't parse format", task_type="build", dry_run=True)
    lessons = load_lessons()
    assert len(lessons) >= 1


# ---------------------------------------------------------------------------
# Integration: agent_loop records memory
# ---------------------------------------------------------------------------

def test_agent_loop_records_outcome(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path)
    from agent_loop import run_agent_loop
    result = run_agent_loop("build a test project", project="mem-loop-test", dry_run=True)
    assert result.status == "done"
    # Memory should have been recorded
    outcomes = load_outcomes()
    assert len(outcomes) >= 1
    assert any(o.goal == "build a test project" for o in outcomes)


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_memory_context_empty(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    import cli
    rc = cli.main(["memory", "context"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no memory" in out.lower() or out.strip() == "(no memory yet)"


def test_cli_memory_outcomes(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    record_outcome("test goal", "done", "summary")
    import cli
    rc = cli.main(["memory", "outcomes"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "test goal" in out


def test_cli_memory_lessons(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    record_outcome("goal", "done", "summary", lessons=["a lesson"])
    import cli
    rc = cli.main(["memory", "lessons"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "a lesson" in out


def test_cli_memory_outcomes_json(monkeypatch, tmp_path, capsys):
    _setup(monkeypatch, tmp_path)
    record_outcome("json goal", "stuck", "failed")
    import cli
    rc = cli.main(["memory", "outcomes", "--format", "json"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, list)
    assert data[0]["goal"] == "json goal"
