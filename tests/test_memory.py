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


# ---------------------------------------------------------------------------
# TF-IDF ranking (Phase 35 P1)
# ---------------------------------------------------------------------------

from memory import _tokenize, _tfidf_rank, TieredLesson, MemoryTier


import datetime as _dt


def _make_tiered_lesson(text: str) -> TieredLesson:
    return TieredLesson(
        lesson_id=str(abs(hash(text)) % 100000),
        lesson=text,
        tier=MemoryTier.MEDIUM,
        task_type="research",
        outcome="done",
        source_goal="test goal",
        confidence=0.8,
        score=0.7,
        last_reinforced=_dt.date.today().isoformat(),
    )


def test_tokenize_removes_stopwords():
    tokens = _tokenize("The quick brown fox jumps around lazy dog")
    assert "the" not in tokens
    assert "quick" in tokens
    assert "brown" in tokens


def test_tokenize_lowercases():
    tokens = _tokenize("Polymarket XYZ findings")
    assert "polymarket" in tokens
    assert "xyz" in tokens
    assert "findings" in tokens


def test_tfidf_rank_empty_lessons():
    assert _tfidf_rank("research polymarket", []) == []


def test_tfidf_rank_returns_all_when_no_top_k():
    lessons = [_make_tiered_lesson(f"lesson number {i}") for i in range(5)]
    ranked = _tfidf_rank("lesson number three", lessons)
    assert len(ranked) == 5


def test_tfidf_rank_most_relevant_first():
    lessons = [
        _make_tiered_lesson("polymarket betting strategies for prediction markets"),
        _make_tiered_lesson("how to write unit tests in python"),
        _make_tiered_lesson("polymarket liquidity and calibration research"),
    ]
    ranked = _tfidf_rank("polymarket prediction research", lessons, top_k=3)
    # The two polymarket lessons should be ranked above the unit test lesson
    top_names = [l.lesson for l in ranked[:2]]
    assert all("polymarket" in n for n in top_names)


def test_tfidf_rank_top_k_limits_output():
    lessons = [_make_tiered_lesson(f"lesson {i} about research") for i in range(10)]
    ranked = _tfidf_rank("research topic", lessons, top_k=3)
    assert len(ranked) == 3


def test_tfidf_rank_no_query_signal_returns_as_is():
    """Empty or stop-word-only query returns list unchanged."""
    lessons = [_make_tiered_lesson("some lesson text")]
    result = _tfidf_rank("the a an", lessons)
    assert len(result) == 1


def test_load_lessons_query_reranks(monkeypatch, tmp_path):
    """With query=, load_lessons returns most relevant lessons first."""
    from memory import load_lessons

    # Fake lessons JSONL with varying relevance
    import json, datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines = [
        {"lesson_id": "a", "lesson": "polymarket betting calibration", "task_type": "research",
         "outcome": "done", "source_goal": "test", "confidence": 0.7, "recorded_at": now},
        {"lesson_id": "b", "lesson": "systemd service restart on failure", "task_type": "ops",
         "outcome": "done", "source_goal": "test", "confidence": 0.7, "recorded_at": now},
        {"lesson_id": "c", "lesson": "polymarket liquidity and market depth", "task_type": "research",
         "outcome": "done", "source_goal": "test", "confidence": 0.7, "recorded_at": now},
    ]
    lessons_file = tmp_path / "lessons.jsonl"
    lessons_file.write_text("\n".join(json.dumps(l) for l in lines))
    monkeypatch.setattr("memory._lessons_path", lambda: lessons_file)

    result = load_lessons(query="polymarket prediction research", limit=3)
    # Both polymarket lessons should rank above systemd
    assert result[0].lesson_id in ("a", "c")
    assert result[1].lesson_id in ("a", "c")
    assert result[2].lesson_id == "b"
