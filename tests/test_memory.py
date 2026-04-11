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
    # Near-duplicates should be reinforced, not duplicated — expect 1 unique entry
    unique_texts = set(lesson_texts)
    assert len(unique_texts) == 1, f"Expected 1 unique lesson, got {len(unique_texts)}: {lesson_texts}"
    # The file should also have at most 1 line for this lesson (dedup persists)
    from memory import _lessons_path
    line_count = len(_lessons_path().read_text().strip().splitlines())
    assert line_count <= 1, f"Expected <=1 lesson lines, got {line_count} (dedup not persisting)"


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
    monkeypatch.setattr("memory_ledger._lessons_path", lambda: lessons_file)

    result = load_lessons(query="polymarket prediction research", limit=3)
    # Both polymarket lessons should rank above systemd
    assert result[0].lesson_id in ("a", "c")
    assert result[1].lesson_id in ("a", "c")
    assert result[2].lesson_id == "b"


# ---------------------------------------------------------------------------
# Step trace recording (Meta-Harness steal)
# ---------------------------------------------------------------------------

from memory import record_step_trace, load_step_traces


def _make_step_outcome(step="do something", status="done", result="result text",
                       summary="summary", stuck_reason=None):
    from types import SimpleNamespace
    return SimpleNamespace(step=step, status=status, result=result,
                           summary=summary, stuck_reason=stuck_reason)


class TestRecordStepTrace:
    def test_writes_to_step_traces_jsonl(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "step_traces.jsonl")
        step_outcomes = [
            _make_step_outcome("fetch data", "done", "data fetched"),
            _make_step_outcome("analyze", "stuck", stuck_reason="LLM timed out"),
        ]
        record_step_trace("outcome-abc", "test goal", step_outcomes, task_type="research")

        lines = (tmp_path / "step_traces.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        trace = json.loads(lines[0])
        assert trace["outcome_id"] == "outcome-abc"
        assert trace["goal"] == "test goal"
        assert len(trace["steps"]) == 2

    def test_stuck_reason_included(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "step_traces.jsonl")
        step_outcomes = [_make_step_outcome("risky step", "stuck", stuck_reason="rate limit hit")]
        record_step_trace("o-001", "goal", step_outcomes)
        trace = json.loads((tmp_path / "step_traces.jsonl").read_text().strip())
        stuck_step = trace["steps"][0]
        assert stuck_step["stuck_reason"] == "rate limit hit"

    def test_done_step_no_stuck_reason_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "step_traces.jsonl")
        step_outcomes = [_make_step_outcome("do thing", "done", stuck_reason=None)]
        record_step_trace("o-002", "goal", step_outcomes)
        trace = json.loads((tmp_path / "step_traces.jsonl").read_text().strip())
        assert "stuck_reason" not in trace["steps"][0]

    def test_result_truncated_to_500(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "step_traces.jsonl")
        step_outcomes = [_make_step_outcome(result="x" * 1000)]
        record_step_trace("o-003", "goal", step_outcomes)
        trace = json.loads((tmp_path / "step_traces.jsonl").read_text().strip())
        assert len(trace["steps"][0]["result"]) <= 500

    def test_empty_step_outcomes_writes_empty_trace(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "step_traces.jsonl")
        record_step_trace("o-004", "goal", [])
        trace = json.loads((tmp_path / "step_traces.jsonl").read_text().strip())
        assert trace["steps"] == []


class TestLoadStepTraces:
    def test_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: tmp_path / "nonexistent.jsonl")
        assert load_step_traces(["o-001"]) == {}

    def test_loads_matching_outcome_id(self, monkeypatch, tmp_path):
        path = tmp_path / "step_traces.jsonl"
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: path)
        step_outcomes = [_make_step_outcome("step1", "done")]
        record_step_trace("o-abc", "test goal", step_outcomes)
        traces = load_step_traces(["o-abc"])
        assert "o-abc" in traces
        assert traces["o-abc"]["goal"] == "test goal"

    def test_filters_to_requested_ids(self, monkeypatch, tmp_path):
        path = tmp_path / "step_traces.jsonl"
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: path)
        record_step_trace("o-1", "goal 1", [_make_step_outcome()])
        record_step_trace("o-2", "goal 2", [_make_step_outcome()])
        traces = load_step_traces(["o-1"])
        assert "o-1" in traces
        assert "o-2" not in traces

    def test_malformed_lines_skipped(self, monkeypatch, tmp_path):
        path = tmp_path / "step_traces.jsonl"
        monkeypatch.setattr("memory_ledger._step_traces_path", lambda: path)
        with open(path, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"outcome_id": "o-good", "goal": "g", "steps": []}) + "\n")
        traces = load_step_traces(["o-good"])
        assert "o-good" in traces


# ---------------------------------------------------------------------------
# Three-layer memory compression (724-office steal)
# ---------------------------------------------------------------------------

class TestCompressOldOutcomes:
    def _make_outcome_line(self, i, status="done"):
        import uuid
        return json.dumps({
            "outcome_id": f"o-{i:04d}",
            "goal": f"Test goal number {i}",
            "task_type": "research",
            "status": status,
            "summary": f"Completed task {i} successfully",
            "lessons": [],
            "recorded_at": f"2026-01-{(i % 28) + 1:02d}T00:00:00+00:00",
        })

    def test_skips_when_below_threshold(self, monkeypatch, tmp_path):
        from memory import compress_old_outcomes
        path = tmp_path / "outcomes.jsonl"
        with open(path, "w") as f:
            for i in range(10):
                f.write(self._make_outcome_line(i) + "\n")
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: tmp_path / "compressed.jsonl")
        result = compress_old_outcomes(threshold=100, dry_run=False)
        assert result is None

    def test_dry_run_returns_dummy_batch(self):
        from memory import compress_old_outcomes, CompressedBatch
        result = compress_old_outcomes(dry_run=True)
        assert isinstance(result, CompressedBatch)
        assert "dry-run" in result.summary

    def test_compresses_when_above_threshold(self, monkeypatch, tmp_path):
        from memory import compress_old_outcomes, CompressedBatch
        path = tmp_path / "outcomes.jsonl"
        compressed_path = tmp_path / "compressed.jsonl"
        with open(path, "w") as f:
            for i in range(120):
                f.write(self._make_outcome_line(i) + "\n")
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: compressed_path)
        result = compress_old_outcomes(threshold=100, batch_size=50, keep_recent=50)
        assert isinstance(result, CompressedBatch)
        assert result.batch_size == 50
        # outcomes.jsonl should now have 70 entries (120 - 50 compressed)
        remaining = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(remaining) == 70
        # compressed_outcomes.jsonl should have 1 entry
        assert compressed_path.exists()

    def test_respects_keep_recent(self, monkeypatch, tmp_path):
        from memory import compress_old_outcomes
        path = tmp_path / "outcomes.jsonl"
        compressed_path = tmp_path / "compressed.jsonl"
        with open(path, "w") as f:
            for i in range(110):
                f.write(self._make_outcome_line(i) + "\n")
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: compressed_path)
        # batch_size=200 but keep_recent=80 → should only compress 30 (110 - 80)
        result = compress_old_outcomes(threshold=100, batch_size=200, keep_recent=80)
        assert result is not None
        assert result.batch_size == 30
        remaining = [l for l in path.read_text().splitlines() if l.strip()]
        assert len(remaining) == 80

    def test_no_adapter_uses_heuristic_summary(self, monkeypatch, tmp_path):
        from memory import compress_old_outcomes
        path = tmp_path / "outcomes.jsonl"
        compressed_path = tmp_path / "compressed.jsonl"
        with open(path, "w") as f:
            for i in range(110):
                f.write(self._make_outcome_line(i) + "\n")
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: compressed_path)
        result = compress_old_outcomes(threshold=100, adapter=None)
        assert result is not None
        assert len(result.summary) > 10  # heuristic summary is non-empty

    def test_returns_none_if_no_file(self, monkeypatch, tmp_path):
        from memory import compress_old_outcomes
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: tmp_path / "nonexistent.jsonl")
        result = compress_old_outcomes(threshold=100)
        assert result is None


class TestLoadCompressedBatches:
    def _make_batch(self, batch_id, summary):
        from memory import CompressedBatch
        return CompressedBatch(
            batch_id=batch_id,
            summary=summary,
            task_types=["research"],
            outcome_ids=["o-1", "o-2"],
            batch_size=2,
            oldest_at="2026-01-01T00:00:00+00:00",
            newest_at="2026-01-05T00:00:00+00:00",
        )

    def test_empty_when_no_file(self, monkeypatch, tmp_path):
        from memory import load_compressed_batches
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: tmp_path / "compressed.jsonl")
        assert load_compressed_batches() == []

    def test_loads_batches_most_recent_first(self, monkeypatch, tmp_path):
        from memory import load_compressed_batches, _save_compressed_batch
        path = tmp_path / "compressed.jsonl"
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: path)
        b1 = self._make_batch("b1", "First batch summary")
        b2 = self._make_batch("b2", "Second batch summary")
        _save_compressed_batch(b1)
        _save_compressed_batch(b2)
        batches = load_compressed_batches(limit=10)
        assert batches[0].batch_id == "b2"  # most recent first
        assert batches[1].batch_id == "b1"


class TestLoadOutcomesWithContext:
    def test_returns_dict_with_required_keys(self, monkeypatch, tmp_path):
        from memory import load_outcomes_with_context
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: tmp_path / "empty.jsonl")
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: tmp_path / "compressed.jsonl")
        result = load_outcomes_with_context()
        assert "recent" in result
        assert "compressed" in result
        assert "context_text" in result

    def test_context_text_includes_both_layers(self, monkeypatch, tmp_path):
        from memory import load_outcomes_with_context, _save_compressed_batch, CompressedBatch
        outcomes_path = tmp_path / "outcomes.jsonl"
        compressed_path = tmp_path / "compressed.jsonl"
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: outcomes_path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: compressed_path)

        # Add one raw outcome
        outcomes_path.write_text(json.dumps({
            "outcome_id": "o-1",
            "goal": "Test research goal",
            "task_type": "research",
            "status": "done",
            "summary": "Found key results",
            "lessons": [],
            "recorded_at": "2026-01-10T00:00:00+00:00",
        }) + "\n")

        # Add one compressed batch
        batch = CompressedBatch(
            batch_id="b1",
            summary="Older missions showed improvement in research tasks",
            task_types=["research"],
            outcome_ids=["o-old"],
            batch_size=10,
            oldest_at="2025-12-01T00:00:00+00:00",
            newest_at="2025-12-31T00:00:00+00:00",
        )
        _save_compressed_batch(batch)

        result = load_outcomes_with_context(goal="research topics")
        assert "Compressed Memory" in result["context_text"]
        assert "Recent Outcomes" in result["context_text"]
        assert len(result["recent"]) == 1
        assert len(result["compressed"]) == 1

    def test_tfidf_ranks_compressed_by_relevance(self, monkeypatch, tmp_path):
        from memory import load_outcomes_with_context, _save_compressed_batch, CompressedBatch
        outcomes_path = tmp_path / "outcomes.jsonl"
        compressed_path = tmp_path / "compressed.jsonl"
        monkeypatch.setattr("memory_ledger._outcomes_path", lambda: outcomes_path)
        monkeypatch.setattr("memory_ledger._compressed_outcomes_path", lambda: compressed_path)
        outcomes_path.write_text("")

        # Two batches — one about research, one about deployment
        b1 = CompressedBatch(
            batch_id="b1",
            summary="Research tasks improved: web scraping and information retrieval worked well",
            task_types=["research"],
            outcome_ids=["o-1"],
            batch_size=5,
            oldest_at="2025-11-01T00:00:00+00:00",
            newest_at="2025-11-15T00:00:00+00:00",
        )
        b2 = CompressedBatch(
            batch_id="b2",
            summary="Deployment tasks failed due to missing environment variables and config errors",
            task_types=["ops"],
            outcome_ids=["o-2"],
            batch_size=5,
            oldest_at="2025-12-01T00:00:00+00:00",
            newest_at="2025-12-15T00:00:00+00:00",
        )
        _save_compressed_batch(b1)
        _save_compressed_batch(b2)

        # Query about research — b1 should rank higher
        result = load_outcomes_with_context(goal="research web scraping information", compressed_limit=2)
        assert result["compressed"][0].batch_id == "b1"


# ---------------------------------------------------------------------------
# Majority-vote pseudo-labels (Agent0 steal)
# ---------------------------------------------------------------------------

class TestMajorityVoteLessons:
    def test_k1_returns_all(self):
        from memory import majority_vote_lessons
        samples = [["lesson A", "lesson B"]]
        result = majority_vote_lessons(samples)
        assert result == ["lesson A", "lesson B"]

    def test_empty_samples(self):
        from memory import majority_vote_lessons
        assert majority_vote_lessons([]) == []

    def test_majority_agreement_accepted(self):
        from memory import majority_vote_lessons
        samples = [
            ["use retry on rate limit errors"],
            ["use retry on rate limit errors", "something else"],
            ["retry on rate limit is important"],
        ]
        result = majority_vote_lessons(samples)
        # "retry on rate limit" appears in all 3 samples — should be accepted
        assert any("retry" in r.lower() for r in result)

    def test_minority_lesson_rejected(self):
        from memory import majority_vote_lessons
        samples = [
            ["always validate input"],
            ["rate limit retry needed"],
            ["rate limit retry needed"],
        ]
        result = majority_vote_lessons(samples)
        # "always validate input" only in 1 of 3 — should be rejected
        assert not any("validate" in r.lower() for r in result)

    def test_caps_at_3_lessons(self):
        from memory import majority_vote_lessons
        samples = [
            ["lesson A", "lesson B", "lesson C", "lesson D"],
            ["lesson A", "lesson B", "lesson C", "lesson D"],
            ["lesson A", "lesson B", "lesson C", "lesson D"],
        ]
        result = majority_vote_lessons(samples)
        assert len(result) <= 3

    def test_k_samples_multi_sample_path(self, monkeypatch):
        from memory import extract_lessons_via_llm
        import types

        call_count = [0]
        all_lessons = [
            ["retry on failure is key"],
            ["retry on failure is key"],
            ["unrelated lesson about something else"],
        ]

        class FakeAdapter:
            def complete(self, messages, **kw):
                idx = call_count[0]
                call_count[0] += 1
                import json
                return types.SimpleNamespace(content=json.dumps(all_lessons[idx]))

        result = extract_lessons_via_llm(
            "some goal", "done", "some summary", "research",
            adapter=FakeAdapter(), k_samples=3
        )
        assert call_count[0] == 3
        assert any("retry" in r.lower() for r in result)

    def test_k1_path_unchanged(self, monkeypatch):
        from memory import extract_lessons_via_llm
        import types, json

        class FakeAdapter:
            def complete(self, messages, **kw):
                return types.SimpleNamespace(content=json.dumps(["single lesson"]))

        result = extract_lessons_via_llm(
            "goal", "done", "summary", "general",
            adapter=FakeAdapter(), k_samples=1
        )
        assert result == ["single lesson"]


# ---------------------------------------------------------------------------
# load_tiered_lessons max_age_days staleness filter
# ---------------------------------------------------------------------------

class TestLoadTieredLessonsMaxAge:
    def _write_lessons(self, path, lessons):
        import json as _json
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for tl in lessons:
                f.write(_json.dumps({
                    "lesson_id": tl.lesson_id,
                    "lesson": tl.lesson,
                    "tier": tl.tier.value if hasattr(tl.tier, "value") else str(tl.tier),
                    "task_type": tl.task_type,
                    "outcome": tl.outcome,
                    "source_goal": tl.source_goal,
                    "confidence": tl.confidence,
                    "score": tl.score,
                    "last_reinforced": tl.last_reinforced,
                }) + "\n")

    def test_max_age_days_filters_stale(self, monkeypatch, tmp_path):
        """Lessons older than max_age_days should be excluded."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import datetime as dt
        from memory import load_tiered_lessons, _tiered_lessons_path, MemoryTier, TieredLesson

        old_date = (dt.date.today() - dt.timedelta(days=60)).isoformat()
        new_date = dt.date.today().isoformat()

        old_lesson = TieredLesson(
            lesson_id="old1", lesson="stale lesson", tier=MemoryTier.MEDIUM,
            task_type="research", outcome="done", source_goal="g",
            confidence=0.9, score=0.9, last_reinforced=old_date,
        )
        new_lesson = TieredLesson(
            lesson_id="new1", lesson="fresh lesson", tier=MemoryTier.MEDIUM,
            task_type="research", outcome="done", source_goal="g",
            confidence=0.9, score=0.9, last_reinforced=new_date,
        )

        path = _tiered_lessons_path("medium")
        self._write_lessons(path, [old_lesson, new_lesson])

        # Without max_age_days: both returned
        all_lessons = load_tiered_lessons("medium")
        assert len(all_lessons) == 2

        # With max_age_days=30: only fresh lesson returned
        filtered = load_tiered_lessons("medium", max_age_days=30)
        assert len(filtered) == 1
        assert filtered[0].lesson_id == "new1"

    def test_max_age_days_none_no_filter(self, monkeypatch, tmp_path):
        """max_age_days=None (default) returns all lessons regardless of age."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        import datetime as dt
        from memory import load_tiered_lessons, _tiered_lessons_path, MemoryTier, TieredLesson

        old_date = (dt.date.today() - dt.timedelta(days=365)).isoformat()
        lesson = TieredLesson(
            lesson_id="ancient", lesson="very old lesson", tier=MemoryTier.LONG,
            task_type="general", outcome="done", source_goal="g",
            confidence=0.9, score=0.9, last_reinforced=old_date,
        )
        path = _tiered_lessons_path("long")
        self._write_lessons(path, [lesson])

        results = load_tiered_lessons("long", max_age_days=None)
        assert len(results) == 1


class TestQueryLessons:
    """Tests for query_lessons() — RAG retrieval API for workers."""

    def _write_lessons(self, path: Path, lessons) -> None:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for l in lessons:
                from dataclasses import asdict as _asdict
                f.write(json.dumps(_asdict(l)) + "\n")

    def test_query_returns_ranked_lessons(self, monkeypatch, tmp_path):
        """query_lessons returns lessons ranked by relevance."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import query_lessons, _tiered_lessons_path, MemoryTier, TieredLesson
        import datetime as dt

        today = dt.date.today().isoformat()

        lessons = [
            TieredLesson(
                lesson_id="l1", lesson="use web search for research tasks",
                tier=MemoryTier.LONG, task_type="research", outcome="done",
                source_goal="g1", confidence=0.9, score=0.9, last_reinforced=today,
            ),
            TieredLesson(
                lesson_id="l2", lesson="avoid running build commands as root",
                tier=MemoryTier.LONG, task_type="build", outcome="done",
                source_goal="g2", confidence=0.8, score=0.8, last_reinforced=today,
            ),
            TieredLesson(
                lesson_id="l3", lesson="research tasks benefit from multiple sources",
                tier=MemoryTier.MEDIUM, task_type="research", outcome="done",
                source_goal="g3", confidence=0.7, score=0.7, last_reinforced=today,
            ),
        ]
        for tier in ["long", "medium"]:
            path = _tiered_lessons_path(tier)
            tier_lessons = [l for l in lessons if l.tier == tier]
            if tier_lessons:
                self._write_lessons(path, tier_lessons)

        results = query_lessons("find research sources for goal", n=3)
        assert len(results) >= 1
        # Research-related lessons should rank higher than build lesson
        ids = [l.lesson_id for l in results]
        assert "l2" not in ids[:1]  # build lesson not first for research query

    def test_query_respects_n_limit(self, monkeypatch, tmp_path):
        """query_lessons returns at most n results."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import query_lessons, _tiered_lessons_path, MemoryTier, TieredLesson
        import datetime as dt

        today = dt.date.today().isoformat()
        lessons = [
            TieredLesson(
                lesson_id=f"l{i}", lesson=f"lesson {i} about research",
                tier=MemoryTier.LONG, task_type="general", outcome="done",
                source_goal="g", confidence=0.9, score=0.9, last_reinforced=today,
            )
            for i in range(10)
        ]
        path = _tiered_lessons_path("long")
        self._write_lessons(path, lessons)

        results = query_lessons("research task", n=3)
        assert len(results) <= 3

    def test_query_empty_store_returns_empty(self, monkeypatch, tmp_path):
        """query_lessons returns [] when no lessons exist."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import query_lessons

        results = query_lessons("any query", n=5)
        assert results == []

    def test_query_filters_by_task_type(self, monkeypatch, tmp_path):
        """query_lessons with task_type=X excludes non-X lessons."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import query_lessons, _tiered_lessons_path, MemoryTier, TieredLesson
        import datetime as dt

        today = dt.date.today().isoformat()
        lessons = [
            TieredLesson(
                lesson_id="r1", lesson="research tip",
                tier=MemoryTier.LONG, task_type="research", outcome="done",
                source_goal="g", confidence=0.9, score=0.9, last_reinforced=today,
            ),
            TieredLesson(
                lesson_id="b1", lesson="build tip",
                tier=MemoryTier.LONG, task_type="build", outcome="done",
                source_goal="g", confidence=0.9, score=0.9, last_reinforced=today,
            ),
        ]
        path = _tiered_lessons_path("long")
        self._write_lessons(path, lessons)

        results = query_lessons("tip", n=5, task_type="research")
        ids = [l.lesson_id for l in results]
        assert "b1" not in ids
        assert "r1" in ids


class TestTaskLedger:
    """Tests for append_task_ledger() + load_task_ledger() — Feynman steal."""

    def test_append_and_load_basic(self, monkeypatch, tmp_path):
        """append_task_ledger writes, load_task_ledger reads back."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import append_task_ledger, load_task_ledger, TaskLedgerEntry

        entry = TaskLedgerEntry(
            task_id="step_1",
            owner="agent_loop",
            task="research Polymarket trends",
            status="done",
            loop_id="abc12345",
            result_summary="Found 5 relevant markets",
        )
        append_task_ledger(entry)

        entries = load_task_ledger()
        assert len(entries) == 1
        assert entries[0].task_id == "step_1"
        assert entries[0].status == "done"
        assert entries[0].loop_id == "abc12345"

    def test_load_filters_by_loop_id(self, monkeypatch, tmp_path):
        """load_task_ledger(loop_id=X) returns only entries for loop X."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import append_task_ledger, load_task_ledger, TaskLedgerEntry

        append_task_ledger(TaskLedgerEntry(
            task_id="s1", owner="agent_loop", task="step A",
            status="done", loop_id="loop1",
        ))
        append_task_ledger(TaskLedgerEntry(
            task_id="s2", owner="agent_loop", task="step B",
            status="done", loop_id="loop2",
        ))

        entries = load_task_ledger(loop_id="loop1")
        assert len(entries) == 1
        assert entries[0].task_id == "s1"

    def test_load_empty_returns_empty(self, monkeypatch, tmp_path):
        """load_task_ledger returns [] when no ledger file exists."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import load_task_ledger
        assert load_task_ledger() == []

    def test_multiple_entries_ordered_most_recent_first(self, monkeypatch, tmp_path):
        """load_task_ledger returns entries most recent first."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import append_task_ledger, load_task_ledger, TaskLedgerEntry

        for i in range(3):
            append_task_ledger(TaskLedgerEntry(
                task_id=f"step_{i}", owner="agent_loop",
                task=f"task {i}", status="done", loop_id="loop1",
            ))

        entries = load_task_ledger()
        assert entries[0].task_id == "step_2"  # most recent first

    def test_blocked_status_recorded(self, monkeypatch, tmp_path):
        """Blocked steps are recorded correctly in ledger."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import append_task_ledger, load_task_ledger, TaskLedgerEntry

        append_task_ledger(TaskLedgerEntry(
            task_id="s1", owner="agent_loop", task="stuck step",
            status="blocked", loop_id="loopX",
        ))

        entries = load_task_ledger(loop_id="loopX")
        assert entries[0].status == "blocked"


class TestTieredLessonEvidenceSources:
    """Tests for TieredLesson.evidence_sources (Feynman claim tracing)."""

    def _write_lessons(self, path: Path, lessons) -> None:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for l in lessons:
                from dataclasses import asdict as _asdict
                f.write(json.dumps(_asdict(l)) + "\n")

    def test_evidence_sources_default_empty(self):
        """TieredLesson.evidence_sources defaults to empty list."""
        from memory import TieredLesson, MemoryTier
        import datetime as dt
        l = TieredLesson(
            lesson_id="l1", lesson="test lesson", tier=MemoryTier.LONG,
            task_type="research", outcome="done", source_goal="g",
            confidence=0.9, score=0.9, last_reinforced=dt.date.today().isoformat(),
        )
        assert l.evidence_sources == []

    def test_record_tiered_lesson_stores_evidence_sources(self, monkeypatch, tmp_path):
        """record_tiered_lesson persists evidence_sources to disk."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, load_tiered_lessons, MemoryTier

        record_tiered_lesson(
            "use multiple sources", "research", "done", "research polymarket",
            tier=MemoryTier.LONG,
            evidence_sources=["https://example.com/paper1", "outcome_abc123"],
        )

        lessons = load_tiered_lessons(MemoryTier.LONG)
        assert len(lessons) == 1
        assert "https://example.com/paper1" in lessons[0].evidence_sources
        assert "outcome_abc123" in lessons[0].evidence_sources

    def test_evidence_sources_roundtrip(self, monkeypatch, tmp_path):
        """Evidence sources survive serialization/deserialization."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import TieredLesson, MemoryTier, _tiered_lessons_path
        import json, datetime as dt
        from dataclasses import asdict

        lesson = TieredLesson(
            lesson_id="l1", lesson="test", tier=MemoryTier.MEDIUM,
            task_type="general", outcome="done", source_goal="g",
            confidence=0.8, score=0.8, last_reinforced=dt.date.today().isoformat(),
            evidence_sources=["url1", "url2"],
        )
        path = _tiered_lessons_path("medium")
        self._write_lessons(path, [lesson])

        from memory import load_tiered_lessons
        loaded = load_tiered_lessons("medium")
        assert loaded[0].evidence_sources == ["url1", "url2"]


class TestDetectGoalGaps:
    """Tests for detect_goal_gaps() — Feynman Steal 10."""

    def test_blocked_steps_produce_high_severity_gaps(self, monkeypatch, tmp_path):
        """Blocked steps become high-severity GoalGap entries."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps, GoalGap

        gaps = detect_goal_gaps(
            "research polymarket predictions",
            outcomes=[],
            blocked_steps=["Fetch data from API"],
        )
        assert any(g.gap_type == "blocked_step" and g.severity == "high" for g in gaps)

    def test_multiple_blocked_steps_all_recorded(self, monkeypatch, tmp_path):
        """Each blocked step produces its own gap."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps

        gaps = detect_goal_gaps(
            "analyze sentiment trends",
            outcomes=[],
            blocked_steps=["Step A failed", "Step B failed"],
        )
        blocked = [g for g in gaps if g.gap_type == "blocked_step"]
        assert len(blocked) == 2

    def test_no_coverage_gap_when_keywords_missing_from_outcomes(self, monkeypatch, tmp_path):
        """Goal keywords absent from all outcomes produce a no_coverage gap."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps, Outcome

        outcomes = [
            Outcome(
                outcome_id="o1", goal="something else entirely",
                status="done", summary="unrelated content here",
                task_type="general", lessons=[],
            )
        ]
        gaps = detect_goal_gaps(
            "analyze sentiment trends bitcoin prices",
            outcomes=outcomes,
        )
        no_cov = [g for g in gaps if g.gap_type == "no_coverage"]
        assert len(no_cov) >= 1
        assert no_cov[0].severity == "medium"

    def test_no_gap_when_keywords_covered(self, monkeypatch, tmp_path):
        """No no_coverage gap when goal keywords appear in outcomes."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps, Outcome

        outcomes = [
            Outcome(
                outcome_id="o1",
                goal="analyze sentiment trends bitcoin prices",
                status="done",
                summary="sentiment analysis of bitcoin price trends completed successfully",
                task_type="research", lessons=[],
            )
        ]
        gaps = detect_goal_gaps(
            "analyze sentiment trends bitcoin prices",
            outcomes=outcomes,
        )
        no_cov = [g for g in gaps if g.gap_type == "no_coverage"]
        assert len(no_cov) == 0

    def test_max_gaps_limits_output(self, monkeypatch, tmp_path):
        """max_gaps parameter caps the returned list."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps

        gaps = detect_goal_gaps(
            "research polymarket sentiment bitcoin analysis trends",
            outcomes=[],
            blocked_steps=["Step A", "Step B", "Step C", "Step D"],
            max_gaps=2,
        )
        assert len(gaps) <= 2

    def test_gaps_sorted_high_before_medium(self, monkeypatch, tmp_path):
        """High-severity gaps appear before medium-severity gaps."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps, Outcome

        outcomes = [
            Outcome(
                outcome_id="o1", goal="unrelated topic",
                status="done", summary="nothing about the goal",
                task_type="general", lessons=[],
            )
        ]
        gaps = detect_goal_gaps(
            "analyze bitcoin sentiment trends predictions",
            outcomes=outcomes,
            blocked_steps=["Some blocked step"],
        )
        severities = [g.severity for g in gaps]
        assert "high" in severities
        high_idx = severities.index("high")
        for i, sev in enumerate(severities):
            if sev == "medium":
                assert i > high_idx

    def test_empty_goal_returns_empty(self, monkeypatch, tmp_path):
        """Empty goal string produces no keyword gaps."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import detect_goal_gaps

        gaps = detect_goal_gaps("", outcomes=[])
        # blocked_steps=None, no outcomes, no keywords → no gaps
        assert gaps == []

    def test_goalgap_dataclass_fields(self):
        """GoalGap has expected fields."""
        from memory import GoalGap

        g = GoalGap(
            gap_type="single_source",
            description="Only one source found",
            severity="medium",
            suggested_step="Find corroborating sources",
        )
        assert g.gap_type == "single_source"
        assert g.severity == "medium"
        assert "source" in g.description


class TestTypedLessonTaxonomy:
    """Tests for NeMo S1/S2/S3/S5 — typed lesson taxonomy + seed/ATIF steals."""

    def test_tiered_lesson_has_lesson_type_field(self):
        """TieredLesson.lesson_type defaults to empty string."""
        from memory import TieredLesson, MemoryTier
        import datetime as dt

        tl = TieredLesson(
            lesson_id="t1", lesson="test lesson", tier=MemoryTier.MEDIUM,
            task_type="research", outcome="done", source_goal="g",
            confidence=0.9, score=0.9, last_reinforced=dt.date.today().isoformat(),
        )
        assert tl.lesson_type == ""

    def test_record_tiered_lesson_stores_lesson_type(self, monkeypatch, tmp_path):
        """record_tiered_lesson persists lesson_type to disk."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, load_tiered_lessons, MemoryTier

        record_tiered_lesson(
            "verify outputs before committing", "build", "done", "ship feature X",
            tier=MemoryTier.MEDIUM, lesson_type="verification",
        )

        lessons = load_tiered_lessons(MemoryTier.MEDIUM)
        assert lessons[0].lesson_type == "verification"

    def test_record_tiered_lesson_normalizes_invalid_type(self, monkeypatch, tmp_path):
        """Invalid lesson_type is stored as empty string (not an error)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, load_tiered_lessons, MemoryTier

        record_tiered_lesson(
            "some lesson", "general", "done", "goal",
            tier=MemoryTier.MEDIUM, lesson_type="bogus_type",
        )
        lessons = load_tiered_lessons(MemoryTier.MEDIUM)
        assert lessons[0].lesson_type == ""

    def test_load_tiered_lessons_filters_by_lesson_type(self, monkeypatch, tmp_path):
        """load_tiered_lessons(lesson_type=...) filters correctly."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, load_tiered_lessons, MemoryTier

        record_tiered_lesson("plan better", "research", "done", "g", tier=MemoryTier.MEDIUM, lesson_type="planning")
        record_tiered_lesson("recover faster", "research", "done", "g", tier=MemoryTier.MEDIUM, lesson_type="recovery")
        record_tiered_lesson("save tokens", "research", "done", "g", tier=MemoryTier.MEDIUM, lesson_type="cost")

        planning = load_tiered_lessons(MemoryTier.MEDIUM, lesson_type="planning")
        assert len(planning) == 1
        assert planning[0].lesson_type == "planning"

    def test_query_lessons_filters_by_lesson_type(self, monkeypatch, tmp_path):
        """query_lessons(lesson_type=...) only returns matching type."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, query_lessons, MemoryTier

        record_tiered_lesson("verify outputs carefully", "build", "done", "g",
                              tier=MemoryTier.LONG, lesson_type="verification")
        record_tiered_lesson("plan scope carefully", "build", "done", "g",
                              tier=MemoryTier.LONG, lesson_type="planning")

        results = query_lessons("build feature", lesson_type="verification")
        assert all(r.lesson_type == "verification" for r in results)

    def test_extract_lessons_via_llm_dry_run_returns_strings(self, monkeypatch, tmp_path):
        """Dry-run returns List[str] by default (backward compat)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import extract_lessons_via_llm

        result = extract_lessons_via_llm("my goal", "done", "all good", "research", dry_run=True)
        assert isinstance(result, list)
        assert all(isinstance(r, str) for r in result)

    def test_extract_lessons_via_llm_dry_run_return_typed(self, monkeypatch, tmp_path):
        """Dry-run with return_typed=True returns List[Tuple[str, str]]."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import extract_lessons_via_llm

        result = extract_lessons_via_llm(
            "my goal", "done", "all good", "research",
            dry_run=True, return_typed=True,
        )
        assert isinstance(result, list)
        assert len(result) == 1
        lesson_text, lesson_type = result[0]
        assert isinstance(lesson_text, str)
        assert isinstance(lesson_type, str)
        assert "execution" == lesson_type  # dry-run defaults to "execution"

    def test_extract_lessons_cross_type_cap(self, monkeypatch, tmp_path):
        """S5: Cross-type cap ensures at most 1 lesson per lesson_type."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import extract_lessons_via_llm

        class FakeAdapter:
            def complete(self, messages, **kwargs):
                class R:
                    def __init__(self): self.content = (
                        '[{"lesson": "lesson A", "type": "execution"}, '
                        '{"lesson": "lesson B", "type": "execution"}, '
                        '{"lesson": "lesson C", "type": "planning"}]'
                    )
                return R()

        result = extract_lessons_via_llm(
            "goal", "done", "summary", "general",
            adapter=FakeAdapter(), return_typed=True,
        )
        types_returned = [t for _, t in result]
        # Only 1 "execution" allowed despite 2 in the response
        assert types_returned.count("execution") <= 1

    def test_lesson_type_roundtrip_serialization(self, monkeypatch, tmp_path):
        """lesson_type survives JSON serialization in load_tiered_lessons."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import TieredLesson, MemoryTier, _tiered_lessons_path
        import json, datetime as dt
        from dataclasses import asdict

        lesson = TieredLesson(
            lesson_id="x1", lesson="use retries", tier=MemoryTier.LONG,
            task_type="ops", outcome="done", source_goal="g",
            confidence=0.8, score=0.8, last_reinforced=dt.date.today().isoformat(),
            lesson_type="recovery",
        )
        path = _tiered_lessons_path(MemoryTier.LONG)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(json.dumps(asdict(lesson)) + "\n")

        from memory import load_tiered_lessons
        loaded = load_tiered_lessons(MemoryTier.LONG)
        assert loaded[0].lesson_type == "recovery"


class TestConfidenceTierStandardization:
    """Tests for Feynman F5 — standardized confidence from k_samples + session count."""

    def test_confidence_single_call(self):
        """k_samples=1 → 0.5 confidence (single, unverified LLM call)."""
        from memory import confidence_from_k_samples
        assert confidence_from_k_samples(1) == 0.5

    def test_confidence_two_samples(self):
        """k_samples=2 → 0.6 confidence (partial consensus)."""
        from memory import confidence_from_k_samples
        assert confidence_from_k_samples(2) == 0.6

    def test_confidence_majority_vote(self):
        """k_samples >= 3 → 0.7 confidence (majority consensus)."""
        from memory import confidence_from_k_samples
        assert confidence_from_k_samples(3) == 0.7
        assert confidence_from_k_samples(5) == 0.7

    def test_k_samples_kwarg_overrides_confidence(self, monkeypatch, tmp_path):
        """record_tiered_lesson(k_samples=1) uses 0.5 regardless of confidence kwarg."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, load_tiered_lessons, MemoryTier

        # k_samples=1 should override the default confidence=0.7
        tl = record_tiered_lesson(
            "check preconditions", "build", "done", "goal",
            tier=MemoryTier.MEDIUM, k_samples=1,
        )
        assert tl.confidence == 0.5

    def test_k_samples_zero_uses_confidence_kwarg(self, monkeypatch, tmp_path):
        """k_samples=0 (default) uses explicit confidence kwarg."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, MemoryTier

        tl = record_tiered_lesson(
            "verify before deploy", "ops", "done", "goal",
            tier=MemoryTier.MEDIUM, confidence=0.85,
        )
        assert tl.confidence == 0.85

    def test_confidence_bumped_at_three_sessions(self, monkeypatch, tmp_path):
        """sessions_validated reaching 3 promotes confidence to >= 0.9."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_tiered_lesson, reinforce_lesson, load_tiered_lessons, MemoryTier

        # Record with low confidence (k_samples=1)
        tl = record_tiered_lesson(
            "unique reinforcement lesson here", "research", "done", "goal",
            tier=MemoryTier.MEDIUM, k_samples=1,
        )
        assert tl.confidence == 0.5

        # Reinforce 3 times to reach sessions_validated >= 3
        for _ in range(3):
            reinforce_lesson(tl.lesson_id, tier=MemoryTier.MEDIUM)

        loaded = load_tiered_lessons(MemoryTier.MEDIUM)
        target = next(l for l in loaded if l.lesson_id == tl.lesson_id)
        assert target.sessions_validated >= 3
        assert target.confidence >= 0.9


class TestVerificationOutcomes:
    """Tests for Feynman F4 — accumulating verifier memory."""

    def test_record_verification_writes_to_disk(self, monkeypatch, tmp_path):
        """record_verification() writes VerificationOutcome to jsonl."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_verification, load_verification_outcomes

        vo = record_verification(
            claim_type="alignment",
            verdict="pass",
            source="llm",
            confidence=0.85,
            goal="research polymarket",
            outcome_id="o1",
        )
        assert vo.claim_type == "alignment"
        assert vo.verdict == "pass"
        assert vo.confidence == 0.85

        loaded = load_verification_outcomes()
        assert len(loaded) == 1
        assert loaded[0].verification_id == vo.verification_id

    def test_load_verification_outcomes_filters_by_claim_type(self, monkeypatch, tmp_path):
        """load_verification_outcomes(claim_type=...) filters correctly."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_verification, load_verification_outcomes

        record_verification("alignment", "pass", "llm", 0.8)
        record_verification("quality", "fail", "llm", 0.6)
        record_verification("alignment", "uncertain", "heuristic", 0.5)

        alignment = load_verification_outcomes(claim_type="alignment")
        assert len(alignment) == 2
        assert all(v.claim_type == "alignment" for v in alignment)

    def test_load_verification_outcomes_newest_first(self, monkeypatch, tmp_path):
        """load_verification_outcomes returns newest records first."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_verification, load_verification_outcomes

        v1 = record_verification("quality", "pass", "llm", 0.9)
        v2 = record_verification("quality", "fail", "heuristic", 0.4)

        loaded = load_verification_outcomes()
        # Newest (v2) should be first
        assert loaded[0].verification_id == v2.verification_id

    def test_verification_accuracy_computes_rates(self, monkeypatch, tmp_path):
        """verification_accuracy() returns correct pass/fail rates."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import record_verification, verification_accuracy

        record_verification("alignment", "pass", "llm", 0.9)
        record_verification("alignment", "pass", "llm", 0.85)
        record_verification("alignment", "fail", "llm", 0.3)

        stats = verification_accuracy(claim_type="alignment")
        assert stats["total"] == 3
        assert abs(stats["pass_rate"] - 2/3) < 0.01
        assert abs(stats["fail_rate"] - 1/3) < 0.01
        assert stats["avg_confidence"] > 0.6

    def test_verification_accuracy_empty_returns_zeros(self, monkeypatch, tmp_path):
        """verification_accuracy() on empty store returns zero rates."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import verification_accuracy

        stats = verification_accuracy()
        assert stats["total"] == 0
        assert stats["pass_rate"] == 0.0

    def test_verification_outcome_dataclass_fields(self):
        """VerificationOutcome has expected fields."""
        from memory import VerificationOutcome

        vo = VerificationOutcome(
            verification_id="abc123",
            claim_type="quality",
            verdict="uncertain",
            source="lesson",
            confidence=0.65,
        )
        assert vo.goal == ""
        assert vo.outcome_id == ""
        assert vo.notes == ""


# ---------------------------------------------------------------------------
# Phase 60: Citation enforcement + calibration loop
# ---------------------------------------------------------------------------

class TestCitationEnforcementPenalty:
    """Phase 60: uncited lessons rank below cited ones on equal text similarity."""

    def _make_lesson(self, lesson_id, text, evidence_sources=None):
        from memory import TieredLesson, MemoryTier
        return TieredLesson(
            lesson_id=lesson_id,
            lesson=text,
            task_type="general",
            outcome="done",
            tier=MemoryTier.MEDIUM,
            source_goal="test goal",
            confidence=0.7,
            score=1.0,
            last_reinforced="2026-04-07",
            evidence_sources=evidence_sources or [],
        )

    def test_cited_lesson_ranks_above_uncited_on_equal_content(self):
        """A cited lesson should outrank a nearly-identical uncited lesson."""
        from memory import _tfidf_rank
        text = "always validate input before passing to downstream"
        cited = self._make_lesson("cited1", text, evidence_sources=["https://example.com/ref"])
        uncited = self._make_lesson("uncited1", text, evidence_sources=[])
        ranked = _tfidf_rank("validate input downstream", [uncited, cited], top_k=2)
        # cited should come first
        assert ranked[0].lesson_id == "cited1"

    def test_uncited_lesson_still_wins_on_clearly_better_content(self):
        """Citation penalty (10%) must not block uncited lessons with far better content."""
        from memory import _tfidf_rank
        good_uncited = self._make_lesson(
            "uncited_good",
            "retry with exponential backoff on rate limit errors",
            evidence_sources=[],
        )
        poor_cited = self._make_lesson(
            "cited_poor",
            "always do things carefully and consider options",
            evidence_sources=["https://example.com"],
        )
        ranked = _tfidf_rank("rate limit retry backoff", [poor_cited, good_uncited], top_k=2)
        assert ranked[0].lesson_id == "uncited_good"

    def test_citation_penalty_constant_is_reasonable(self):
        """_CITATION_PENALTY should be in (0.8, 1.0) — a gentle nudge, not a block."""
        from memory import _CITATION_PENALTY
        assert 0.80 < _CITATION_PENALTY < 1.0


class TestCalibrationLoop:
    """Phase 60: calibrated_alignment_threshold() auto-tunes based on verifier history."""

    def test_returns_base_when_insufficient_samples(self, monkeypatch, tmp_path):
        """With < _CALIBRATION_MIN_SAMPLES outcomes, return base threshold."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import calibrated_alignment_threshold, _ALIGNMENT_THRESHOLD_BASE, record_verification

        # Only 2 records — below the minimum
        record_verification("alignment", "pass", "llm", 0.9)
        record_verification("alignment", "fail", "llm", 0.3)

        result = calibrated_alignment_threshold("alignment")
        assert result == _ALIGNMENT_THRESHOLD_BASE

    def test_lowers_threshold_when_verifier_is_conservative(self, monkeypatch, tmp_path):
        """Low avg_confidence + high uncertain_rate → lower threshold."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import calibrated_alignment_threshold, _ALIGNMENT_THRESHOLD_BASE, record_verification

        # Simulate conservative verifier: low confidence, lots of uncertain
        for _ in range(8):
            record_verification("alignment", "uncertain", "llm", 0.50)
        for _ in range(4):
            record_verification("alignment", "pass", "llm", 0.52)

        result = calibrated_alignment_threshold("alignment")
        assert result < _ALIGNMENT_THRESHOLD_BASE

    def test_raises_threshold_when_verifier_is_confident_and_strict(self, monkeypatch, tmp_path):
        """High avg_confidence + high fail_rate → raised threshold."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import calibrated_alignment_threshold, _ALIGNMENT_THRESHOLD_BASE, record_verification

        # Simulate strict verifier: high confidence, mostly fail
        for _ in range(8):
            record_verification("alignment", "fail", "llm", 0.82)
        for _ in range(4):
            record_verification("alignment", "pass", "llm", 0.78)

        result = calibrated_alignment_threshold("alignment")
        assert result > _ALIGNMENT_THRESHOLD_BASE

    def test_threshold_stays_within_bounds(self, monkeypatch, tmp_path):
        """Calibrated threshold must stay in [_ALIGNMENT_THRESHOLD_MIN, _ALIGNMENT_THRESHOLD_MAX]."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import (
            calibrated_alignment_threshold, record_verification,
            _ALIGNMENT_THRESHOLD_MIN, _ALIGNMENT_THRESHOLD_MAX,
        )

        for _ in range(15):
            record_verification("alignment", "uncertain", "llm", 0.1)

        result = calibrated_alignment_threshold("alignment")
        assert _ALIGNMENT_THRESHOLD_MIN <= result <= _ALIGNMENT_THRESHOLD_MAX

    def test_returns_base_on_empty_store(self, monkeypatch, tmp_path):
        """With no verifier history at all, return base threshold."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import calibrated_alignment_threshold, _ALIGNMENT_THRESHOLD_BASE

        result = calibrated_alignment_threshold("alignment")
        assert result == _ALIGNMENT_THRESHOLD_BASE

    def test_normal_distribution_returns_base(self, monkeypatch, tmp_path):
        """Mixed healthy verifier history → no adjustment (return base)."""
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        from memory import calibrated_alignment_threshold, _ALIGNMENT_THRESHOLD_BASE, record_verification

        # Balanced distribution at moderate confidence
        for _ in range(6):
            record_verification("alignment", "pass", "llm", 0.70)
        for _ in range(4):
            record_verification("alignment", "fail", "llm", 0.65)

        result = calibrated_alignment_threshold("alignment")
        assert result == _ALIGNMENT_THRESHOLD_BASE
