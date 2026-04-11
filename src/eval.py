"""Phase 8 + Evals-as-Training-Data: Evaluation suite for Poe orchestration.

Benchmark Poe against known-good goals with expected outcomes.
Flywheel: mine prod failures → auto-generate evals → harness tweaks.

Usage:
    from eval import run_eval, score_result
    report = run_eval(dry_run=True)
    print(report.summary())

    # Flywheel: mine failures and generate new evals
    from eval import mine_failure_patterns, generate_evals_from_patterns
    patterns = mine_failure_patterns()
    new_evals = generate_evals_from_patterns(patterns)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Phase 42: Nightly eval → evolver feedback loop
# ---------------------------------------------------------------------------

def run_nightly_eval(
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Run eval suite and convert failures to evolver Suggestion entries.

    Called from heartbeat_loop() on a 24h cadence. Now also runs the
    Evals-as-Training-Data flywheel: mines failures, generates new evals,
    and tracks pass-rate trends.

    Returns: number of regression suggestions written (0 on dry_run or all pass).
    """
    import sys as _sys
    if verbose:
        print("[eval] nightly eval starting...", file=_sys.stderr, flush=True)

    # Run the full flywheel (mine → generate → run → score → suggest)
    try:
        flywheel_summary = run_eval_flywheel(dry_run=dry_run, verbose=verbose)
        if verbose:
            print(f"[eval] flywheel: {flywheel_summary['patterns_mined']} patterns, "
                  f"{flywheel_summary['evals_generated']} evals generated, "
                  f"{flywheel_summary.get('suggestions_written', 0)} suggestions",
                  file=_sys.stderr, flush=True)
    except Exception as exc:
        if verbose:
            print(f"[eval] flywheel failed (non-fatal): {exc}", file=_sys.stderr, flush=True)

    try:
        report = run_eval(dry_run=dry_run, verbose=verbose)
    except Exception as exc:
        if verbose:
            print(f"[eval] nightly eval failed (non-fatal): {exc}", file=_sys.stderr, flush=True)
        return 0

    failed = [r for r in report.results if r.status != "pass"]
    if not failed:
        if verbose:
            print(f"[eval] nightly eval: all {report.pass_count} passed", file=_sys.stderr, flush=True)
        return 0

    if verbose:
        print(f"[eval] nightly eval: {len(failed)} failure(s) — generating regression suggestions",
              file=_sys.stderr, flush=True)

    if dry_run:
        return 0

    import uuid as _uuid
    import json as _json
    from datetime import datetime as _dt, timezone as _tz
    from pathlib import Path as _Path

    try:
        from orch_items import memory_dir
        sug_path = memory_dir() / "suggestions.jsonl"
    except Exception:
        sug_path = _Path.cwd() / "memory" / "suggestions.jsonl"

    run_id = _uuid.uuid4().hex[:8]
    new_suggestions = []
    for i, r in enumerate(failed):
        reason = r.failure_reason or f"score={r.score:.2f}"
        text = (
            f"Eval regression: benchmark '{r.benchmark_id}' failed "
            f"({reason}). "
            f"Investigate and fix the behavior pattern this benchmark covers."
        )
        new_suggestions.append({
            "suggestion_id": f"eval-{run_id}-{i:02d}",
            "category": "observation",
            "target": r.benchmark_id,
            "suggestion": text[:500],
            "failure_pattern": f"eval_regression:{r.benchmark_id}",
            "confidence": 0.90,
            "outcomes_analyzed": report.benchmarks_run,
            "generated_at": _dt.now(_tz.utc).isoformat(),
            "applied": False,
        })

    try:
        sug_path.parent.mkdir(parents=True, exist_ok=True)
        with sug_path.open("a", encoding="utf-8") as f:
            for s in new_suggestions:
                f.write(_json.dumps(s) + "\n")
    except Exception as exc:
        if verbose:
            print(f"[eval] failed to write regression suggestions: {exc}", file=_sys.stderr, flush=True)
        return 0

    return len(new_suggestions)


# ===========================================================================
# Evals-as-Training-Data Flywheel
# ===========================================================================
#
# Pipeline: mine prod failures → generate evals → run evals → score →
#           auto-suggest harness improvements → validate on next cycle
#
# Data sources:
#   - diagnoses.jsonl (failure classifications from introspect.py)
#   - outcomes.jsonl  (execution results from memory_ledger.py)
#
# Output:
#   - eval-generated.jsonl (auto-generated benchmarks)
#   - suggestions.jsonl    (improvement suggestions for evolver)
#   - eval-results.jsonl   (pass/fail history for trend tracking)
# ===========================================================================


# ---------------------------------------------------------------------------
# Failure pattern mining
# ---------------------------------------------------------------------------

@dataclass
class FailurePattern:
    """A recurring failure pattern mined from prod data."""
    pattern_id: str                    # Deterministic hash of failure_class + goal_signature
    failure_class: str                 # From introspect.py (e.g., "empty_model_output")
    occurrence_count: int              # How many times this pattern appeared
    representative_goals: List[str]    # Example goals that triggered this failure
    evidence_summary: List[str]        # Aggregated evidence from diagnoses
    severity: str                      # Most common severity
    avg_tokens: int = 0                # Average token usage in failed runs
    avg_elapsed_ms: int = 0            # Average wall-clock time
    first_seen: str = ""               # Earliest occurrence
    last_seen: str = ""                # Most recent occurrence


def _generated_evals_path() -> Path:
    """Path to auto-generated eval benchmarks."""
    from orch_items import memory_dir
    return memory_dir() / "eval-generated.jsonl"


def _eval_trend_path() -> Path:
    """Path to eval pass rate trend log."""
    from orch_items import memory_dir
    return memory_dir() / "eval-trend.jsonl"


def mine_failure_patterns(
    *,
    min_occurrences: int = 2,
    max_patterns: int = 20,
    lookback_limit: int = 500,
) -> List[FailurePattern]:
    """Mine diagnoses + outcomes for recurring failure patterns.

    Clusters failures by (failure_class, goal_type_signature), filters to
    patterns that recur at least min_occurrences times, and extracts
    representative goals for eval generation.

    Args:
        min_occurrences: Minimum times a pattern must appear to be included.
        max_patterns: Cap on returned patterns (most frequent first).
        lookback_limit: Max diagnoses to scan (most recent first).

    Returns:
        List of FailurePattern, sorted by occurrence count descending.
    """
    try:
        from introspect import load_diagnoses
    except ImportError:
        return []

    diagnoses = load_diagnoses(limit=lookback_limit)
    if not diagnoses:
        return []

    # Load outcomes to cross-reference goals
    outcome_goals: Dict[str, str] = {}
    try:
        from memory_ledger import load_outcomes
        for o in load_outcomes(limit=lookback_limit):
            outcome_goals[o.outcome_id] = o.goal
    except Exception:
        pass

    # Cluster by failure_class (skip healthy)
    clusters: Dict[str, List] = {}
    for diag in diagnoses:
        if diag.failure_class == "healthy":
            continue
        key = diag.failure_class
        if key not in clusters:
            clusters[key] = []
        clusters[key].append(diag)

    # Build patterns from clusters
    patterns: List[FailurePattern] = []
    for failure_class, diags in clusters.items():
        if len(diags) < min_occurrences:
            continue

        # Extract representative goals from cross-referenced outcomes
        goals: List[str] = []
        for d in diags:
            goal = outcome_goals.get(d.loop_id, "")
            if goal and goal not in goals:
                goals.append(goal)

        # Aggregate evidence
        all_evidence: List[str] = []
        for d in diags:
            all_evidence.extend(d.evidence[:2])  # top 2 per diagnosis
        # Deduplicate evidence
        seen_ev: set = set()
        unique_evidence: List[str] = []
        for ev in all_evidence:
            ev_key = ev[:80].lower()
            if ev_key not in seen_ev:
                seen_ev.add(ev_key)
                unique_evidence.append(ev)

        severities = Counter(d.severity for d in diags)
        avg_tokens = sum(d.total_tokens for d in diags) // max(len(diags), 1)
        avg_elapsed = sum(d.total_elapsed_ms for d in diags) // max(len(diags), 1)

        # Deterministic ID from failure class
        pid = hashlib.sha256(failure_class.encode()).hexdigest()[:12]

        patterns.append(FailurePattern(
            pattern_id=pid,
            failure_class=failure_class,
            occurrence_count=len(diags),
            representative_goals=goals[:5],
            evidence_summary=unique_evidence[:10],
            severity=severities.most_common(1)[0][0],
            avg_tokens=avg_tokens,
            avg_elapsed_ms=avg_elapsed,
            first_seen=min((getattr(d, "recorded_at", "") or "") for d in diags) or "",
            last_seen=max((getattr(d, "recorded_at", "") or "") for d in diags) or "",
        ))

    patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
    return patterns[:max_patterns]


# ---------------------------------------------------------------------------
# Eval generation from mined patterns
# ---------------------------------------------------------------------------

# Scoring criteria per failure class — what does "fixed" look like?
_FAILURE_SCORING: Dict[str, Dict[str, Any]] = {
    "empty_model_output": {
        "check": "non_empty_response",
        "description": "Step must produce a non-trivial response (>20 chars)",
        "pass_if": lambda r: len(r.response) > 20,
    },
    "artifact_missing": {
        "check": "has_artifact",
        "description": "Loop must produce a concrete artifact or structured output",
        "pass_if": lambda r: r.status == "pass" and len(r.response) > 50,
    },
    "setup_failure": {
        "check": "no_setup_error",
        "description": "Loop must not fail during adapter/import initialization",
        "pass_if": lambda r: r.status != "error",
    },
    "decomposition_too_broad": {
        "check": "reasonable_tokens",
        "description": "Step tokens must stay under 100K",
        "pass_if": lambda r: r.tokens_used < 100_000,
    },
    "token_explosion": {
        "check": "token_budget",
        "description": "Total tokens must stay within 3x expected budget",
        "pass_if": lambda r: r.tokens_used < 50_000,
    },
    "retry_churn": {
        "check": "completes_without_churn",
        "description": "Must complete without the same step repeating 3+ times",
        "pass_if": lambda r: r.status == "pass",
    },
    "budget_exhaustion": {
        "check": "stays_in_budget",
        "description": "Must complete within iteration budget",
        "pass_if": lambda r: r.status != "fail" or "budget" not in (r.failure_reason or ""),
    },
    "constraint_false_positive": {
        "check": "not_blocked",
        "description": "Must not be blocked by constraint false positive",
        "pass_if": lambda r: r.status != "fail" or "constraint" not in (r.failure_reason or "").lower(),
    },
    "integration_drift": {
        "check": "no_import_error",
        "description": "Must not fail with import/attribute errors",
        "pass_if": lambda r: r.status != "error" or "import" not in (r.failure_reason or "").lower(),
    },
}


@dataclass
class GeneratedEval:
    """An auto-generated eval benchmark derived from a failure pattern."""
    eval_id: str                       # "gen-{pattern_id}-{seq}"
    source_pattern_id: str             # FailurePattern.pattern_id
    failure_class: str                 # What failure this tests for
    benchmark: Dict[str, Any]          # Compatible with BUILTIN_BENCHMARKS format
    scoring_check: str                 # Key into _FAILURE_SCORING
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pass_count: int = 0                # Historical pass count
    fail_count: int = 0                # Historical fail count
    active: bool = True                # Still part of the eval suite?


def generate_evals_from_patterns(
    patterns: List[FailurePattern],
    *,
    max_per_class: int = 2,
) -> List[GeneratedEval]:
    """Convert failure patterns into runnable eval benchmarks.

    For each pattern, generates up to max_per_class eval scenarios using
    representative goals from the failure data. The scoring criteria are
    derived from the failure class (e.g., empty_model_output → check for
    non-empty response).

    Args:
        patterns: Output from mine_failure_patterns().
        max_per_class: Max evals to generate per failure class.

    Returns:
        List of GeneratedEval ready to save and run.
    """
    generated: List[GeneratedEval] = []

    for pattern in patterns:
        scoring = _FAILURE_SCORING.get(pattern.failure_class)
        if not scoring:
            continue  # No scoring criteria for this failure class

        goals = pattern.representative_goals
        if not goals:
            # Use a generic test goal for this failure class
            goals = [f"Test recovery from {pattern.failure_class.replace('_', ' ')}"]

        for i, goal in enumerate(goals[:max_per_class]):
            eval_id = f"gen-{pattern.pattern_id[:8]}-{i:02d}"

            # Determine lane from goal characteristics
            lane = "agenda" if len(goal.split()) > 8 else "now"

            benchmark = {
                "id": eval_id,
                "goal": goal,
                "lane": lane,
                "expected_keywords": [],  # Scored by failure-specific check, not keywords
                "max_tokens": 4000 if lane == "agenda" else 500,
                "max_steps": 6 if lane == "agenda" else 2,
                "source_failure_class": pattern.failure_class,
            }

            generated.append(GeneratedEval(
                eval_id=eval_id,
                source_pattern_id=pattern.pattern_id,
                failure_class=pattern.failure_class,
                benchmark=benchmark,
                scoring_check=scoring["check"],
            ))

    return generated


def save_generated_evals(evals: List[GeneratedEval]) -> int:
    """Persist generated evals to eval-generated.jsonl. Returns count saved."""
    if not evals:
        return 0

    path = _generated_evals_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing to avoid duplicates
    existing_ids: set = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                existing_ids.add(json.loads(line).get("eval_id", ""))
            except (json.JSONDecodeError, TypeError):
                continue

    saved = 0
    with path.open("a", encoding="utf-8") as f:
        for ev in evals:
            if ev.eval_id in existing_ids:
                continue
            f.write(json.dumps(asdict(ev), sort_keys=True) + "\n")
            saved += 1

    log.info("eval flywheel: saved %d new generated evals (%d skipped as duplicates)",
             saved, len(evals) - saved)
    return saved


def load_generated_evals(*, active_only: bool = True) -> List[GeneratedEval]:
    """Load auto-generated evals from eval-generated.jsonl."""
    path = _generated_evals_path()
    if not path.exists():
        return []

    evals: List[GeneratedEval] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            if active_only and not d.get("active", True):
                continue
            evals.append(GeneratedEval(**{
                k: v for k, v in d.items()
                if k in GeneratedEval.__dataclass_fields__
            }))
        except (json.JSONDecodeError, TypeError):
            continue
    return evals


# ---------------------------------------------------------------------------
# Enhanced scoring for generated evals
# ---------------------------------------------------------------------------

def score_generated_eval(result: BenchmarkResult, eval_entry: GeneratedEval) -> bool:
    """Score a benchmark result using failure-class-specific criteria.

    Returns True if the eval passes (the failure has been fixed).
    """
    scoring = _FAILURE_SCORING.get(eval_entry.failure_class)
    if not scoring:
        # Fall back to standard keyword scoring
        return result.status == "pass"

    try:
        return scoring["pass_if"](result)
    except Exception:
        return result.status == "pass"


# ---------------------------------------------------------------------------
# Trend tracking
# ---------------------------------------------------------------------------

def record_eval_trend(report: "EvalReport", *, generated_results: Optional[List[tuple]] = None) -> None:
    """Append a trend entry for pass-rate tracking over time.

    Each entry records: timestamp, total/pass/fail counts, generated eval stats.
    """
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "run_id": report.run_id,
        "builtin_total": report.benchmarks_run,
        "builtin_pass": report.pass_count,
        "builtin_fail": report.fail_count,
        "builtin_score": round(report.overall_score, 3),
    }

    if generated_results:
        gen_pass = sum(1 for _, passed in generated_results if passed)
        gen_fail = len(generated_results) - gen_pass
        entry["generated_total"] = len(generated_results)
        entry["generated_pass"] = gen_pass
        entry["generated_fail"] = gen_fail
        entry["generated_pass_rate"] = round(gen_pass / max(len(generated_results), 1), 3)

    try:
        path = _eval_trend_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        pass


def load_eval_trend(limit: int = 50) -> List[dict]:
    """Load recent eval trend entries."""
    path = _eval_trend_path()
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: List[dict] = []
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return list(reversed(entries))


# ---------------------------------------------------------------------------
# Main flywheel cycle
# ---------------------------------------------------------------------------

def run_eval_flywheel(
    *,
    dry_run: bool = False,
    verbose: bool = False,
    min_occurrences: int = 2,
) -> Dict[str, Any]:
    """Run the full Evals-as-Training-Data flywheel.

    1. Mine failure patterns from diagnoses + outcomes
    2. Generate new eval benchmarks from patterns
    3. Run all evals (builtin + generated)
    4. Score generated evals with failure-specific criteria
    5. Generate suggestions for evolver on failures
    6. Track pass-rate trend

    Args:
        dry_run: Simulate without LLM calls.
        verbose: Print progress.
        min_occurrences: Minimum failure count to generate an eval.

    Returns:
        Summary dict with counts and results.
    """
    import sys as _sys

    summary: Dict[str, Any] = {
        "patterns_mined": 0,
        "evals_generated": 0,
        "evals_saved": 0,
        "builtin_report": None,
        "generated_results": [],
        "suggestions_written": 0,
    }

    # Step 1: Mine failure patterns
    if verbose:
        print("[eval-flywheel] mining failure patterns...", file=_sys.stderr, flush=True)
    patterns = mine_failure_patterns(min_occurrences=min_occurrences)
    summary["patterns_mined"] = len(patterns)
    if verbose:
        print(f"[eval-flywheel] found {len(patterns)} recurring patterns", file=_sys.stderr, flush=True)
        for p in patterns[:5]:
            print(f"  {p.failure_class}: {p.occurrence_count}x", file=_sys.stderr, flush=True)

    # Step 2: Generate evals
    if patterns:
        new_evals = generate_evals_from_patterns(patterns)
        summary["evals_generated"] = len(new_evals)
        if not dry_run and new_evals:
            summary["evals_saved"] = save_generated_evals(new_evals)

    # Step 3: Run builtin evals
    if verbose:
        print("[eval-flywheel] running builtin benchmarks...", file=_sys.stderr, flush=True)
    builtin_report = run_eval(dry_run=dry_run, verbose=verbose)
    summary["builtin_report"] = builtin_report.to_dict()

    # Step 4: Run generated evals with failure-specific scoring
    generated_evals = load_generated_evals()
    gen_results: List[tuple] = []  # (GeneratedEval, passed: bool)

    if generated_evals:
        if verbose:
            print(f"[eval-flywheel] running {len(generated_evals)} generated evals...",
                  file=_sys.stderr, flush=True)
        for ge in generated_evals:
            result = run_benchmark(ge.benchmark, dry_run=dry_run, verbose=verbose)
            passed = score_generated_eval(result, ge)
            gen_results.append((ge, passed))

            if verbose:
                icon = "PASS" if passed else "FAIL"
                print(f"  [{icon}] {ge.eval_id} ({ge.failure_class})", file=_sys.stderr, flush=True)

    summary["generated_results"] = [
        {"eval_id": ge.eval_id, "failure_class": ge.failure_class, "passed": passed}
        for ge, passed in gen_results
    ]

    # Step 5: Track trend
    record_eval_trend(builtin_report, generated_results=gen_results)

    # Step 6: Generate suggestions for failed generated evals
    if not dry_run:
        failed_gen = [(ge, passed) for ge, passed in gen_results if not passed]
        if failed_gen:
            suggestions_written = _write_flywheel_suggestions(failed_gen, builtin_report.run_id)
            summary["suggestions_written"] = suggestions_written

    log.info("eval-flywheel: %d patterns, %d generated, %d/%d gen passed, %d suggestions",
             summary["patterns_mined"], summary["evals_generated"],
             sum(1 for _, p in gen_results if p), len(gen_results),
             summary.get("suggestions_written", 0))

    return summary


def _write_flywheel_suggestions(
    failed: List[tuple],
    run_id: str,
) -> int:
    """Write targeted improvement suggestions for failed generated evals."""
    try:
        from orch_items import memory_dir
        sug_path = memory_dir() / "suggestions.jsonl"
    except Exception:
        return 0

    suggestions = []
    for i, (ge, _) in enumerate(failed):
        scoring = _FAILURE_SCORING.get(ge.failure_class, {})
        desc = scoring.get("description", ge.failure_class)

        text = (
            f"Eval flywheel: generated eval '{ge.eval_id}' failed "
            f"(failure_class={ge.failure_class}). "
            f"Check: {desc}. "
            f"This pattern has recurred — consider a systemic fix in the "
            f"{'decompose' if 'decomposition' in ge.failure_class else 'execution'} path."
        )

        suggestions.append({
            "suggestion_id": f"flywheel-{run_id}-{i:02d}",
            "category": "observation",
            "target": ge.failure_class,
            "suggestion": text[:500],
            "failure_pattern": f"eval_flywheel:{ge.failure_class}",
            "confidence": 0.85,  # Higher than base (pattern recurs)
            "outcomes_analyzed": ge.pass_count + ge.fail_count + 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "applied": False,
        })

    try:
        sug_path.parent.mkdir(parents=True, exist_ok=True)
        with sug_path.open("a", encoding="utf-8") as f:
            for s in suggestions:
                f.write(json.dumps(s) + "\n")
        return len(suggestions)
    except Exception:
        return 0
