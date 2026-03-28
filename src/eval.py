"""Phase 8: Evaluation suite for Poe orchestration.

Benchmark Poe against known-good goals with expected outcomes.

Usage:
    from eval import run_eval, score_result
    report = run_eval(dry_run=True)
    print(report.summary())
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from handle import handle
except ImportError:  # pragma: no cover
    handle = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Built-in benchmarks
# ---------------------------------------------------------------------------

BUILTIN_BENCHMARKS: List[Dict[str, Any]] = [
    {
        "id": "now-greeting",
        "goal": "Say hello and introduce yourself as Poe",
        "lane": "now",
        "expected_keywords": ["poe", "hello"],
        "max_tokens": 500,
        "max_steps": 1,
    },
    {
        "id": "now-math",
        "goal": "What is 17 multiplied by 23?",
        "lane": "now",
        "expected_keywords": ["391"],
        "max_tokens": 200,
        "max_steps": 1,
    },
    {
        "id": "agenda-research",
        "goal": "Research the key features of Python asyncio and summarize the main concepts",
        "lane": "agenda",
        "expected_keywords": ["async", "await", "event loop", "coroutine"],
        "max_tokens": 4000,
        "max_steps": 6,
    },
    {
        "id": "agenda-analysis",
        "goal": "Analyze the pros and cons of microservices vs monolithic architecture",
        "lane": "agenda",
        "expected_keywords": ["microservice", "monolith", "scaling", "complexity"],
        "max_tokens": 4000,
        "max_steps": 6,
    },
]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkResult:
    benchmark_id: str
    goal: str
    status: str            # "pass" | "fail" | "error"
    score: float           # 0.0 - 1.0
    response: str
    elapsed_ms: int
    tokens_used: int
    failure_reason: Optional[str] = None


@dataclass
class EvalReport:
    run_id: str
    benchmarks_run: int
    pass_count: int
    fail_count: int
    overall_score: float
    results: List[BenchmarkResult] = field(default_factory=list)
    elapsed_ms: int = 0

    def summary(self) -> str:
        lines = [
            f"eval run_id={self.run_id}",
            f"benchmarks={self.benchmarks_run} pass={self.pass_count} fail={self.fail_count}",
            f"overall_score={self.overall_score:.2f}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        for r in self.results:
            icon = "PASS" if r.status == "pass" else "FAIL"
            lines.append(f"  [{icon}] {r.benchmark_id}: score={r.score:.2f}" +
                         (f" ({r.failure_reason})" if r.failure_reason else ""))
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "benchmarks_run": self.benchmarks_run,
            "pass_count": self.pass_count,
            "fail_count": self.fail_count,
            "overall_score": self.overall_score,
            "elapsed_ms": self.elapsed_ms,
            "results": [
                {
                    "benchmark_id": r.benchmark_id,
                    "goal": r.goal,
                    "status": r.status,
                    "score": r.score,
                    "elapsed_ms": r.elapsed_ms,
                    "tokens_used": r.tokens_used,
                    "failure_reason": r.failure_reason,
                }
                for r in self.results
            ],
        }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_result(response: str, expected_keywords: List[str]) -> float:
    """Check how many expected keywords appear in the response (case-insensitive).

    Returns fraction of keywords found (0.0 - 1.0).
    """
    if not expected_keywords:
        return 1.0
    if not response:
        return 0.0

    lower_response = response.lower()
    found = sum(1 for kw in expected_keywords if kw.lower() in lower_response)
    return found / len(expected_keywords)


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    benchmark: Dict[str, Any],
    *,
    adapter=None,
    dry_run: bool = False,
    verbose: bool = False,
) -> BenchmarkResult:
    """Run a single benchmark.

    Args:
        benchmark: Dict with id, goal, lane, expected_keywords, etc.
        adapter: Optional LLM adapter.
        dry_run: Return canned result without calling LLM.
        verbose: Print progress.

    Returns:
        BenchmarkResult with pass/fail status and score.
    """
    benchmark_id = benchmark["id"]
    goal = benchmark["goal"]
    expected_keywords = benchmark.get("expected_keywords", [])
    lane = benchmark.get("lane", "now")

    if dry_run:
        # Return a canned result that passes
        canned_response = f"[dry-run] {goal}. " + " ".join(expected_keywords)
        score = score_result(canned_response, expected_keywords)
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            goal=goal,
            status="pass" if score >= 0.5 else "fail",
            score=score,
            response=canned_response,
            elapsed_ms=10,
            tokens_used=0,
        )

    # Run through handle()
    if handle is None:
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            goal=goal,
            status="error",
            score=0.0,
            response="",
            elapsed_ms=0,
            tokens_used=0,
            failure_reason="handle module not available",
        )

    t0 = time.monotonic()
    try:
        result = handle(
            goal,
            force_lane=lane,
            adapter=adapter,
            dry_run=False,
            verbose=verbose,
        )
        elapsed = int((time.monotonic() - t0) * 1000)

        response = result.result
        score = score_result(response, expected_keywords)
        status_ok = result.status == "done"

        if status_ok and score >= 0.5:
            status = "pass"
            failure_reason = None
        else:
            status = "fail"
            reasons = []
            if not status_ok:
                reasons.append(f"handle status={result.status}")
            if score < 0.5:
                reasons.append(f"keyword score={score:.2f}")
            failure_reason = "; ".join(reasons)

        return BenchmarkResult(
            benchmark_id=benchmark_id,
            goal=goal,
            status=status,
            score=score,
            response=response[:2000],
            elapsed_ms=elapsed,
            tokens_used=result.tokens_in + result.tokens_out,
            failure_reason=failure_reason,
        )
    except Exception as exc:
        elapsed = int((time.monotonic() - t0) * 1000)
        return BenchmarkResult(
            benchmark_id=benchmark_id,
            goal=goal,
            status="error",
            score=0.0,
            response="",
            elapsed_ms=elapsed,
            tokens_used=0,
            failure_reason=str(exc),
        )


# ---------------------------------------------------------------------------
# Full eval run
# ---------------------------------------------------------------------------

def _eval_results_path() -> Path:
    """Path to eval results file."""
    from orch_items import memory_dir
    return memory_dir() / "eval-results.jsonl"


def run_eval(
    benchmarks: Optional[List[str]] = None,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> EvalReport:
    """Run all benchmarks (or subset by id).

    Args:
        benchmarks: List of benchmark IDs to run (None = all).
        dry_run: Return canned results without calling LLM.
        verbose: Print progress.

    Returns:
        EvalReport with all results.
    """
    run_id = uuid.uuid4().hex[:8]
    t0 = time.monotonic()

    # Filter benchmarks
    to_run = BUILTIN_BENCHMARKS
    if benchmarks:
        to_run = [b for b in BUILTIN_BENCHMARKS if b["id"] in benchmarks]

    results: List[BenchmarkResult] = []
    for b in to_run:
        if verbose:
            import sys
            print(f"[eval] running {b['id']}...", file=sys.stderr, flush=True)
        result = run_benchmark(b, dry_run=dry_run, verbose=verbose)
        results.append(result)

    elapsed = int((time.monotonic() - t0) * 1000)

    pass_count = sum(1 for r in results if r.status == "pass")
    fail_count = sum(1 for r in results if r.status != "pass")
    overall_score = (
        sum(r.score for r in results) / len(results) if results else 0.0
    )

    report = EvalReport(
        run_id=run_id,
        benchmarks_run=len(results),
        pass_count=pass_count,
        fail_count=fail_count,
        overall_score=overall_score,
        results=results,
        elapsed_ms=elapsed,
    )

    # Save results
    if not dry_run:
        try:
            path = _eval_results_path()
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(report.to_dict()) + "\n")
        except Exception:
            pass

    return report
