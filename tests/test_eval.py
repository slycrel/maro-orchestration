"""Tests for Phase 8 + Evals-as-Training-Data flywheel."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from eval import (
    BUILTIN_BENCHMARKS,
    BenchmarkResult,
    EvalReport,
    FailurePattern,
    GeneratedEval,
    score_result,
    run_benchmark,
    run_eval,
    mine_failure_patterns,
    generate_evals_from_patterns,
    save_generated_evals,
    load_generated_evals,
    score_generated_eval,
    record_eval_trend,
    load_eval_trend,
    run_eval_flywheel,
    _FAILURE_SCORING,
)


# ---------------------------------------------------------------------------
# score_result
# ---------------------------------------------------------------------------

def test_score_result_all_keywords():
    response = "Hello, I am Poe, an autonomous AI assistant."
    score = score_result(response, ["poe", "hello"])
    assert score == 1.0


def test_score_result_some_keywords():
    response = "Hello there."
    score = score_result(response, ["hello", "poe", "assistant"])
    assert score == pytest.approx(1 / 3)


def test_score_result_no_keywords():
    response = "Something completely different."
    score = score_result(response, ["poe", "hello"])
    assert score == 0.0


def test_score_result_empty_keywords():
    score = score_result("anything", [])
    assert score == 1.0


def test_score_result_empty_response():
    score = score_result("", ["hello"])
    assert score == 0.0


def test_score_result_case_insensitive():
    response = "HELLO, POE here!"
    score = score_result(response, ["hello", "poe"])
    assert score == 1.0


# ---------------------------------------------------------------------------
# BenchmarkResult dataclass
# ---------------------------------------------------------------------------

def test_benchmark_result_dataclass():
    r = BenchmarkResult(
        benchmark_id="test-1",
        goal="test goal",
        status="pass",
        score=1.0,
        response="test response",
        elapsed_ms=100,
        tokens_used=50,
    )
    assert r.benchmark_id == "test-1"
    assert r.status == "pass"
    assert r.failure_reason is None


def test_benchmark_result_with_failure():
    r = BenchmarkResult(
        benchmark_id="test-2",
        goal="test goal",
        status="fail",
        score=0.3,
        response="bad response",
        elapsed_ms=200,
        tokens_used=100,
        failure_reason="keyword score=0.30",
    )
    assert r.status == "fail"
    assert r.failure_reason is not None


# ---------------------------------------------------------------------------
# EvalReport
# ---------------------------------------------------------------------------

def test_eval_report_summary():
    results = [
        BenchmarkResult(
            benchmark_id="b1", goal="g1", status="pass",
            score=1.0, response="ok", elapsed_ms=10, tokens_used=50,
        ),
        BenchmarkResult(
            benchmark_id="b2", goal="g2", status="fail",
            score=0.0, response="bad", elapsed_ms=20, tokens_used=60,
            failure_reason="no keywords",
        ),
    ]
    report = EvalReport(
        run_id="r1",
        benchmarks_run=2,
        pass_count=1,
        fail_count=1,
        overall_score=0.5,
        results=results,
        elapsed_ms=30,
    )
    s = report.summary()
    assert "pass=1" in s
    assert "fail=1" in s
    assert "overall_score=0.50" in s
    assert "[PASS] b1" in s
    assert "[FAIL] b2" in s


def test_eval_report_to_dict():
    report = EvalReport(
        run_id="r1",
        benchmarks_run=1,
        pass_count=1,
        fail_count=0,
        overall_score=1.0,
        results=[
            BenchmarkResult(
                benchmark_id="b1", goal="g1", status="pass",
                score=1.0, response="ok", elapsed_ms=10, tokens_used=50,
            ),
        ],
        elapsed_ms=10,
    )
    d = report.to_dict()
    assert d["run_id"] == "r1"
    assert d["benchmarks_run"] == 1
    assert len(d["results"]) == 1
    assert d["results"][0]["benchmark_id"] == "b1"


def test_eval_report_empty():
    report = EvalReport(
        run_id="r1",
        benchmarks_run=0,
        pass_count=0,
        fail_count=0,
        overall_score=0.0,
    )
    s = report.summary()
    assert "benchmarks=0" in s


# ---------------------------------------------------------------------------
# BUILTIN_BENCHMARKS
# ---------------------------------------------------------------------------

def test_builtin_benchmarks_exist():
    assert len(BUILTIN_BENCHMARKS) >= 4


def test_builtin_benchmarks_have_required_keys():
    for b in BUILTIN_BENCHMARKS:
        assert "id" in b
        assert "goal" in b
        assert "lane" in b
        assert "expected_keywords" in b


def test_builtin_benchmarks_lanes():
    lanes = {b["lane"] for b in BUILTIN_BENCHMARKS}
    assert "now" in lanes
    assert "agenda" in lanes


# ---------------------------------------------------------------------------
# run_benchmark dry_run
# ---------------------------------------------------------------------------

def test_run_benchmark_dry_run():
    b = BUILTIN_BENCHMARKS[0]
    result = run_benchmark(b, dry_run=True)
    assert result.benchmark_id == b["id"]
    assert result.status == "pass"
    assert result.score > 0.0
    assert result.elapsed_ms >= 0
    assert result.tokens_used == 0


def test_run_benchmark_dry_run_all_benchmarks():
    for b in BUILTIN_BENCHMARKS:
        result = run_benchmark(b, dry_run=True)
        assert result.benchmark_id == b["id"]
        assert result.status == "pass"


# ---------------------------------------------------------------------------
# run_eval dry_run
# ---------------------------------------------------------------------------

def test_run_eval_dry_run():
    report = run_eval(dry_run=True)
    assert report.benchmarks_run == len(BUILTIN_BENCHMARKS)
    assert report.pass_count == len(BUILTIN_BENCHMARKS)
    assert report.fail_count == 0
    assert report.overall_score > 0.0


def test_run_eval_dry_run_subset():
    report = run_eval(benchmarks=["now-greeting"], dry_run=True)
    assert report.benchmarks_run == 1
    assert report.results[0].benchmark_id == "now-greeting"


def test_run_eval_dry_run_no_matching():
    report = run_eval(benchmarks=["nonexistent"], dry_run=True)
    assert report.benchmarks_run == 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_poe_eval_dry_run_text(capsys):
    import cli
    rc = cli.main(["poe-eval", "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "eval" in out
    assert "pass=" in out


def test_cli_poe_eval_dry_run_json(capsys):
    import cli
    rc = cli.main(["poe-eval", "--dry-run", "--format", "json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert "benchmarks_run" in data
    assert data["benchmarks_run"] >= 4


def test_cli_poe_eval_single_benchmark(capsys):
    import cli
    rc = cli.main(["poe-eval", "--dry-run", "--benchmark", "now-math"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "now-math" in out


# ===========================================================================
# Evals-as-Training-Data Flywheel Tests
# ===========================================================================


@pytest.fixture
def flywheel_workspace(monkeypatch, tmp_path):
    """Set up an isolated workspace with sample diagnoses and outcomes."""
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "medium").mkdir()
    (mem / "long").mkdir()
    monkeypatch.setenv("POE_MEMORY_DIR", str(mem))
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    # Write sample diagnoses
    diag_path = mem / "diagnoses.jsonl"
    diagnoses = [
        {"loop_id": f"loop-{i}", "failure_class": "empty_model_output",
         "severity": "warning", "evidence": ["content <20 chars"], "recommendation": "retry",
         "total_tokens": 500, "total_elapsed_ms": 100, "steps_done": 1,
         "steps_blocked": 1, "steps_total": 2}
        for i in range(5)
    ] + [
        {"loop_id": f"loop-art-{i}", "failure_class": "artifact_missing",
         "severity": "warning", "evidence": ["no artifact produced"], "recommendation": "add hint",
         "total_tokens": 800, "total_elapsed_ms": 200, "steps_done": 3,
         "steps_blocked": 0, "steps_total": 3}
        for i in range(3)
    ] + [
        {"loop_id": "loop-healthy", "failure_class": "healthy",
         "severity": "info", "evidence": [], "recommendation": "",
         "total_tokens": 400, "total_elapsed_ms": 50, "steps_done": 2,
         "steps_blocked": 0, "steps_total": 2}
    ]
    with diag_path.open("w") as f:
        for d in diagnoses:
            f.write(json.dumps(d) + "\n")

    # Write matching outcomes
    out_path = mem / "outcomes.jsonl"
    outcomes = [
        {"outcome_id": f"loop-{i}", "goal": f"test goal {i} for empty output",
         "task_type": "agenda", "status": "stuck", "summary": "empty response",
         "lessons": [], "tokens_in": 250, "tokens_out": 250, "elapsed_ms": 100,
         "recorded_at": "2026-04-10T00:00:00Z"}
        for i in range(5)
    ] + [
        {"outcome_id": f"loop-art-{i}", "goal": f"build artifact {i} for project",
         "task_type": "build", "status": "done", "summary": "no artifact",
         "lessons": [], "tokens_in": 400, "tokens_out": 400, "elapsed_ms": 200,
         "recorded_at": "2026-04-10T00:00:00Z"}
        for i in range(3)
    ]
    with out_path.open("w") as f:
        for o in outcomes:
            f.write(json.dumps(o) + "\n")

    return tmp_path


class TestFailureMining:

    def test_mine_finds_patterns(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        assert len(patterns) >= 2
        classes = {p.failure_class for p in patterns}
        assert "empty_model_output" in classes
        assert "artifact_missing" in classes

    def test_mine_excludes_healthy(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=1)
        classes = {p.failure_class for p in patterns}
        assert "healthy" not in classes

    def test_mine_respects_min_occurrences(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=4)
        # Only empty_model_output has 5 occurrences, artifact_missing has 3
        assert len(patterns) == 1
        assert patterns[0].failure_class == "empty_model_output"

    def test_mine_includes_representative_goals(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        emo = [p for p in patterns if p.failure_class == "empty_model_output"][0]
        assert len(emo.representative_goals) > 0
        assert "empty output" in emo.representative_goals[0]

    def test_mine_empty_diagnoses(self, monkeypatch, tmp_path):
        mem = tmp_path / "memory"
        mem.mkdir()
        monkeypatch.setenv("POE_MEMORY_DIR", str(mem))
        monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
        patterns = mine_failure_patterns()
        assert patterns == []

    def test_pattern_has_deterministic_id(self, flywheel_workspace):
        p1 = mine_failure_patterns(min_occurrences=2)
        p2 = mine_failure_patterns(min_occurrences=2)
        ids1 = {p.pattern_id for p in p1}
        ids2 = {p.pattern_id for p in p2}
        assert ids1 == ids2  # Same data → same IDs


class TestEvalGeneration:

    def test_generate_from_patterns(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        evals = generate_evals_from_patterns(patterns)
        assert len(evals) > 0
        assert all(e.failure_class for e in evals)
        assert all(e.benchmark.get("goal") for e in evals)

    def test_generate_respects_max_per_class(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        evals = generate_evals_from_patterns(patterns, max_per_class=1)
        classes = [e.failure_class for e in evals]
        # Each class should appear at most once
        from collections import Counter
        counts = Counter(classes)
        assert all(c <= 1 for c in counts.values())

    def test_generated_benchmark_is_runnable(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        evals = generate_evals_from_patterns(patterns)
        for ev in evals:
            b = ev.benchmark
            assert "id" in b
            assert "goal" in b
            assert "lane" in b
            # Should be runnable by run_benchmark
            result = run_benchmark(b, dry_run=True)
            assert result.benchmark_id == b["id"]

    def test_scoring_criteria_exist_for_common_failures(self):
        for cls in ("empty_model_output", "artifact_missing", "setup_failure",
                     "decomposition_too_broad"):
            assert cls in _FAILURE_SCORING


class TestEvalPersistence:

    def test_save_and_load(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        evals = generate_evals_from_patterns(patterns)
        saved = save_generated_evals(evals)
        assert saved > 0

        loaded = load_generated_evals()
        assert len(loaded) == saved
        assert loaded[0].eval_id == evals[0].eval_id

    def test_save_deduplicates(self, flywheel_workspace):
        patterns = mine_failure_patterns(min_occurrences=2)
        evals = generate_evals_from_patterns(patterns)
        save_generated_evals(evals)
        saved2 = save_generated_evals(evals)
        assert saved2 == 0  # All duplicates

    def test_load_empty(self, flywheel_workspace):
        loaded = load_generated_evals()
        assert loaded == []


class TestGeneratedEvalScoring:

    def test_score_empty_model_output_pass(self):
        result = BenchmarkResult(
            benchmark_id="test", goal="test", status="pass",
            score=1.0, response="A meaningful response with real content",
            elapsed_ms=100, tokens_used=500,
        )
        ge = GeneratedEval(
            eval_id="gen-test", source_pattern_id="p1",
            failure_class="empty_model_output",
            benchmark={}, scoring_check="non_empty_response",
        )
        assert score_generated_eval(result, ge) is True

    def test_score_empty_model_output_fail(self):
        result = BenchmarkResult(
            benchmark_id="test", goal="test", status="fail",
            score=0.0, response="ok",  # < 20 chars
            elapsed_ms=100, tokens_used=500,
        )
        ge = GeneratedEval(
            eval_id="gen-test", source_pattern_id="p1",
            failure_class="empty_model_output",
            benchmark={}, scoring_check="non_empty_response",
        )
        assert score_generated_eval(result, ge) is False

    def test_score_setup_failure(self):
        result = BenchmarkResult(
            benchmark_id="test", goal="test", status="error",
            score=0.0, response="",
            elapsed_ms=10, tokens_used=0,
            failure_reason="ModuleNotFoundError",
        )
        ge = GeneratedEval(
            eval_id="gen-test", source_pattern_id="p1",
            failure_class="setup_failure",
            benchmark={}, scoring_check="no_setup_error",
        )
        assert score_generated_eval(result, ge) is False


class TestEvalTrend:

    def test_record_and_load_trend(self, flywheel_workspace):
        report = EvalReport(
            run_id="test-001", benchmarks_run=4,
            pass_count=3, fail_count=1, overall_score=0.75,
        )
        record_eval_trend(report)
        trend = load_eval_trend()
        assert len(trend) == 1
        assert trend[0]["builtin_pass"] == 3

    def test_trend_with_generated_results(self, flywheel_workspace):
        report = EvalReport(
            run_id="test-002", benchmarks_run=4,
            pass_count=4, fail_count=0, overall_score=1.0,
        )
        gen_results = [
            (GeneratedEval(eval_id="g1", source_pattern_id="p1",
                           failure_class="x", benchmark={}, scoring_check="x"), True),
            (GeneratedEval(eval_id="g2", source_pattern_id="p1",
                           failure_class="x", benchmark={}, scoring_check="x"), False),
        ]
        record_eval_trend(report, generated_results=gen_results)
        trend = load_eval_trend()
        assert trend[0]["generated_total"] == 2
        assert trend[0]["generated_pass"] == 1


class TestFlywheelIntegration:

    def test_full_flywheel_dry_run(self, flywheel_workspace):
        summary = run_eval_flywheel(dry_run=True, min_occurrences=2)
        assert summary["patterns_mined"] >= 2
        assert summary["evals_generated"] > 0
        assert summary["builtin_report"] is not None

    def test_full_flywheel_saves_generated_evals(self, flywheel_workspace):
        # dry_run=True still runs mining and generation, just not LLM calls
        summary = run_eval_flywheel(dry_run=True, min_occurrences=2)
        # Verify mining and generation happened
        assert summary["patterns_mined"] >= 2
        assert summary["evals_generated"] > 0
        assert "generated_results" in summary
