"""Tests for Phase 8: eval.py — evaluation suite + benchmark runner."""

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
    score_result,
    run_benchmark,
    run_eval,
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
