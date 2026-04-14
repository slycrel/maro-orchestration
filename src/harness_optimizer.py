"""Harness self-optimization loop (Meta-Harness steal).

Reads recent stuck execution traces and current EXECUTE_SYSTEM/DECOMPOSE_SYSTEM
prompt text to propose specific word-level improvements. Saves results as evolver
Suggestions with category="prompt_tweak" and target="EXECUTE_SYSTEM" or
"DECOMPOSE_SYSTEM" for review and optional auto-apply.

Unlike the main evolver (which looks at behavioral outcome patterns), the harness
optimizer reads the actual prompt text and failure traces to produce concrete
rewrite proposals — the same pattern that Stanford Meta-Harness found yields
+7.7 pts classification with 75% fewer tokens.

Key design decisions:
- Read-only: never writes EXECUTE_SYSTEM or DECOMPOSE_SYSTEM directly. All output
  goes through the existing Suggestion pipeline for human review or confidence-gated
  auto-apply.
- Trace-first: passes raw step-level failure traces to the proposer, not summary
  stats. This is the core Meta-Harness insight.
- Candidate history: records current prompt hashes to memory/harness_candidates.jsonl
  so the proposer can see what has been tried before.

Usage:
    python3 harness_optimizer.py               # run once
    python3 harness_optimizer.py --dry-run     # analyze without saving suggestions
    poe-harness-optimizer                      # CLI alias
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.harness_optimizer")

# ---------------------------------------------------------------------------
# Imports (graceful fallback for test patching)
# ---------------------------------------------------------------------------

try:
    from llm import build_adapter, MODEL_MID, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]
    MODEL_MID = "mid"
    LLMMessage = None  # type: ignore[assignment]

try:
    from llm_parse import extract_json, safe_list, safe_str, content_or_empty
except ImportError:  # pragma: no cover
    extract_json = json.loads  # type: ignore[assignment]
    safe_list = list  # type: ignore[assignment]
    safe_str = str  # type: ignore[assignment]
    content_or_empty = lambda r: getattr(r, "content", "") or ""  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Storage paths
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _step_traces_path() -> Path:
    return _memory_dir() / "step_traces.jsonl"


def _candidates_path() -> Path:
    return _memory_dir() / "harness_candidates.jsonl"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class HarnessProposal:
    """One proposed improvement to a harness prompt."""
    target: str               # "EXECUTE_SYSTEM" | "DECOMPOSE_SYSTEM"
    original_clause: str      # exact text from the current prompt that's problematic
    proposed_change: str      # what to replace/add/remove
    failure_pattern: str      # what trace pattern motivated this
    confidence: float         # 0.0–1.0


@dataclass
class HarnessOptimizerReport:
    run_id: str
    target_analyzed: List[str]
    traces_reviewed: int
    proposals: List[HarnessProposal] = field(default_factory=list)
    elapsed_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"harness_optimizer run_id={self.run_id} skipped: {self.skip_reason}"
        return (
            f"harness_optimizer run_id={self.run_id} "
            f"traces={self.traces_reviewed} proposals={len(self.proposals)} "
            f"targets={self.target_analyzed}"
        )


# ---------------------------------------------------------------------------
# Harness text loading
# ---------------------------------------------------------------------------

def _load_harness_text(target: str) -> Optional[str]:
    """Load the current text of a named harness prompt.

    Args:
        target: "EXECUTE_SYSTEM" | "DECOMPOSE_SYSTEM"

    Returns:
        The prompt text, or None if not found.
    """
    if target == "EXECUTE_SYSTEM":
        try:
            from step_exec import EXECUTE_SYSTEM
            return EXECUTE_SYSTEM
        except ImportError:
            return None
    elif target == "DECOMPOSE_SYSTEM":
        try:
            from planner import DECOMPOSE_SYSTEM
            return DECOMPOSE_SYSTEM
        except ImportError:
            return None
    return None


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _record_candidate(target: str, text: str) -> None:
    """Append current prompt text + hash to harness_candidates.jsonl."""
    try:
        entry = {
            "target": target,
            "hash": _hash_prompt(text),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "length": len(text),
        }
        with open(_candidates_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as exc:
        log.debug("record_candidate: %s", exc)


def load_candidates_history(target: str) -> List[Dict[str, Any]]:
    """Load all known prompt versions for a target from harness_candidates.jsonl."""
    path = _candidates_path()
    if not path.exists():
        return []
    history: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get("target") == target:
                    history.append(entry)
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return history


# ---------------------------------------------------------------------------
# Trace loading
# ---------------------------------------------------------------------------

def _load_stuck_traces(limit: int = 10) -> List[Dict[str, Any]]:
    """Load recent traces that contain at least one stuck step."""
    path = _step_traces_path()
    if not path.exists():
        return []

    raw: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                trace = json.loads(line)
                steps = trace.get("steps", [])
                if any(s.get("status") == "stuck" for s in steps):
                    raw.append(trace)
            except json.JSONDecodeError:
                pass
    except OSError:
        return []

    # Most recent first
    raw.sort(key=lambda t: t.get("recorded_at", ""), reverse=True)
    return raw[:limit]


def _format_trace_for_prompt(trace: Dict[str, Any], max_steps: int = 6) -> str:
    """Format one trace as a compact block for the LLM prompt."""
    goal = trace.get("goal", "")[:80]
    lines = [f"Goal: {goal}"]
    for step in trace.get("steps", [])[:max_steps]:
        status = step.get("status", "?")
        text = step.get("step", "")[:60]
        sr = step.get("stuck_reason", "")
        result = step.get("result", "")[:80]
        if status == "stuck":
            lines.append(f"  [STUCK] {text} — {sr or result}")
        else:
            lines.append(f"  [{status}] {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM analysis
# ---------------------------------------------------------------------------

_HARNESS_OPTIMIZER_SYSTEM = """\
You are a harness optimization agent. Your job: analyze execution traces showing
where an LLM agent got stuck, then propose specific word-level changes to the
agent's system prompts that would prevent those failures.

RULES:
1. Propose only CONCRETE changes — exact text to add, remove, or rewrite.
2. Never propose adding vague advice ("be more careful") — every change must be
   a specific instruction with clear failure mode it addresses.
3. Maximum 3 proposals per prompt target.
4. Confidence 0.0–1.0: how certain you are this change would help.
5. Anti-sycophancy: if the prompt is fine, say so. Output {"proposals": []} rather
   than inventing improvements.

Respond ONLY with this JSON:
{
  "proposals": [
    {
      "target": "EXECUTE_SYSTEM",
      "original_clause": "exact text from current prompt that's problematic",
      "proposed_change": "what to replace/add/remove",
      "failure_pattern": "what trace pattern motivated this",
      "confidence": 0.0-1.0
    }
  ]
}
"""


def _llm_analyze_harness(
    harness_texts: Dict[str, str],
    stuck_traces: List[Dict[str, Any]],
    *,
    dry_run: bool = False,
) -> List[HarnessProposal]:
    """Ask LLM to propose harness improvements based on stuck traces."""
    if dry_run or not stuck_traces or build_adapter is None:
        return []

    try:
        adapter = build_adapter(model=MODEL_MID)
    except Exception as exc:
        log.warning("harness_optimizer: could not build adapter: %s", exc)
        return []

    # Build user message
    lines = ["## Current harness prompts\n"]
    for target, text in harness_texts.items():
        lines.append(f"### {target}\n```\n{text}\n```\n")

    lines.append("## Recent stuck execution traces\n")
    for i, trace in enumerate(stuck_traces[:8], 1):
        lines.append(f"Trace {i}:\n{_format_trace_for_prompt(trace)}\n")

    user_msg = "\n".join(lines)

    try:
        resp = adapter.complete(
            [
                LLMMessage("system", _HARNESS_OPTIMIZER_SYSTEM),
                LLMMessage("user", user_msg),
            ],
            max_tokens=1024,
            temperature=0.3,
        )
        raw = extract_json(content_or_empty(resp), dict)
        raw_proposals = safe_list(raw.get("proposals", []) if raw else [], element_type=dict)
    except Exception as exc:
        log.warning("harness_optimizer: LLM call failed: %s", exc)
        return []

    proposals: List[HarnessProposal] = []
    for p in raw_proposals[:6]:
        target = safe_str(p.get("target", ""), max_length=50)
        if target not in harness_texts:
            continue
        proposals.append(HarnessProposal(
            target=target,
            original_clause=safe_str(p.get("original_clause", ""), max_length=300),
            proposed_change=safe_str(p.get("proposed_change", ""), max_length=500),
            failure_pattern=safe_str(p.get("failure_pattern", ""), max_length=200),
            confidence=float(max(0.0, min(1.0, p.get("confidence", 0.5)))),
        ))
    return proposals


# ---------------------------------------------------------------------------
# Save proposals as evolver Suggestions
# ---------------------------------------------------------------------------

def _save_harness_proposals(proposals: List[HarnessProposal], run_id: str) -> int:
    """Save harness proposals as evolver Suggestion entries. Returns count saved."""
    if not proposals:
        return 0
    try:
        from evolver import Suggestion, _save_suggestions
        suggestions = []
        for i, p in enumerate(proposals):
            suggestions.append(Suggestion(
                suggestion_id=f"harness-{run_id}-{i:02d}",
                category="prompt_tweak",
                target=p.target,
                suggestion=p.proposed_change,
                failure_pattern=p.failure_pattern,
                confidence=p.confidence,
                outcomes_analyzed=0,
            ))
        _save_suggestions(suggestions)
        return len(suggestions)
    except Exception as exc:
        log.warning("harness_optimizer: failed to save suggestions: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_harness_optimizer(
    *,
    targets: Optional[List[str]] = None,
    max_traces: int = 10,
    min_stuck_traces: int = 2,
    dry_run: bool = False,
    verbose: bool = False,
) -> HarnessOptimizerReport:
    """Run one harness optimization cycle.

    Args:
        targets: Prompt targets to analyze. Defaults to ["EXECUTE_SYSTEM", "DECOMPOSE_SYSTEM"].
        max_traces: Maximum number of stuck traces to load.
        min_stuck_traces: Skip if fewer stuck traces than this.
        dry_run: Analyze without writing proposals.
        verbose: Print progress to stderr.

    Returns:
        HarnessOptimizerReport with proposals.
    """
    import uuid
    run_id = uuid.uuid4().hex[:8]
    started = time.monotonic()
    targets = targets or ["EXECUTE_SYSTEM", "DECOMPOSE_SYSTEM"]

    log.info("harness_optimizer_start run_id=%s targets=%s dry_run=%s", run_id, targets, dry_run)
    if verbose:
        print(f"[harness_optimizer] run_id={run_id} starting...", file=sys.stderr)

    # Load harness texts
    harness_texts: Dict[str, str] = {}
    for t in targets:
        text = _load_harness_text(t)
        if text:
            harness_texts[t] = text
            if not dry_run:
                _record_candidate(t, text)

    if not harness_texts:
        return HarnessOptimizerReport(
            run_id=run_id, target_analyzed=[], traces_reviewed=0,
            skipped=True, skip_reason="could not load any harness text",
        )

    # Load stuck traces
    stuck_traces = _load_stuck_traces(limit=max_traces)
    if len(stuck_traces) < min_stuck_traces:
        return HarnessOptimizerReport(
            run_id=run_id, target_analyzed=list(harness_texts.keys()),
            traces_reviewed=len(stuck_traces), skipped=True,
            skip_reason=f"only {len(stuck_traces)} stuck traces (need {min_stuck_traces})",
        )

    if verbose:
        print(f"[harness_optimizer] analyzing {len(stuck_traces)} stuck traces...", file=sys.stderr)

    # LLM analysis
    proposals = _llm_analyze_harness(harness_texts, stuck_traces, dry_run=dry_run)

    # Save proposals
    if not dry_run and proposals:
        saved = _save_harness_proposals(proposals, run_id)
        if verbose:
            print(f"[harness_optimizer] saved {saved} proposal(s)", file=sys.stderr)
        log.info("harness_optimizer saved %d proposals", saved)

    report = HarnessOptimizerReport(
        run_id=run_id,
        target_analyzed=list(harness_texts.keys()),
        traces_reviewed=len(stuck_traces),
        proposals=proposals,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
    if verbose:
        print(f"[harness_optimizer] done: {len(proposals)} proposal(s)", file=sys.stderr)
    log.info("harness_optimizer_done run_id=%s proposals=%d elapsed=%dms",
             run_id, len(proposals), report.elapsed_ms)
    return report


# ---------------------------------------------------------------------------
# Harness friction analysis — "Harness Is the Problem" (@sebgoddijn / Ramp Glass)
#
# Models are fine; the harness is the bottleneck. Friction = quality signal.
# Instead of just analyzing stuck prompts, scan ALL traces for code-path friction:
# which error types recur? which phases fail? which adapter paths are hot?
#
# Produces `HarnessFrictionReport` with ranked friction points → evolver Suggestions
# with category="harness_friction" for human review.
# ---------------------------------------------------------------------------

from collections import Counter


@dataclass
class FrictionPoint:
    """One recurring code-path friction signal extracted from traces."""
    friction_type: str        # "adapter_error" | "timeout" | "retry_storm" | "phase_failure" | "tool_error"
    signal: str               # What was observed (e.g. "ClaudeSubprocessAdapter.complete() timeout")
    frequency: int            # How many traces/steps exhibit this
    total_traces: int         # Denominator for rate calculation
    examples: List[str]       # Up to 3 example snippets from traces
    suggestion: str           # Proposed harness fix (heuristic, not LLM)
    severity: str             # "high" | "medium" | "low"

    @property
    def rate(self) -> float:
        return self.frequency / max(1, self.total_traces)


@dataclass
class HarnessFrictionReport:
    run_id: str
    traces_analyzed: int
    friction_points: List[FrictionPoint] = field(default_factory=list)
    suggestions_saved: int = 0
    elapsed_ms: int = 0
    skipped: bool = False
    skip_reason: str = ""

    def summary(self) -> str:
        if self.skipped:
            return f"friction_scan run_id={self.run_id} skipped: {self.skip_reason}"
        n = len(self.friction_points)
        high = sum(1 for f in self.friction_points if f.severity == "high")
        return (
            f"friction_scan run_id={self.run_id}: {n} friction point(s) "
            f"({high} high) from {self.traces_analyzed} traces"
        )


def _load_all_traces(limit: int = 50) -> List[Dict[str, Any]]:
    """Load recent step traces (both stuck and successful)."""
    path = _step_traces_path()
    if not path.exists():
        return []
    raw = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        return []
    raw.sort(key=lambda t: t.get("recorded_at", ""), reverse=True)
    return raw[:limit]


_ADAPTER_ERROR_KEYWORDS = (
    "adapter_error", "adaptererror", "timed out", "timeout",
    "rate limit", "hit your limit", "connection error", "api error",
    "subprocess", "returncode", "unexpected keyword",
)
_RETRY_KEYWORDS = ("retry", "retrying", "retried", "reattempt", "backoff")
_TOOL_ERROR_KEYWORDS = ("tool_error", "tool call", "malformed", "json decode", "parse error")


def _classify_error(text: str) -> Optional[str]:
    t = text.lower()
    if any(k in t for k in ("timed out", "timeout", "expired")):
        return "timeout"
    if any(k in t for k in ("rate limit", "hit your limit")):
        return "rate_limit"
    if any(k in t for k in ("adapter_error", "adaptererror", "unexpected keyword")):
        return "adapter_error"
    if any(k in t for k in _RETRY_KEYWORDS):
        return "retry_storm"
    if any(k in t for k in _TOOL_ERROR_KEYWORDS):
        return "tool_error"
    if any(k in t for k in ("stuck", "blocked")):
        return "phase_failure"
    return None


def scan_harness_friction(
    traces: Optional[List[Dict[str, Any]]] = None,
    *,
    limit: int = 50,
    min_frequency: int = 2,
) -> HarnessFrictionReport:
    """Scan execution traces for code-path friction signals.

    Args:
        traces:         Preloaded traces (for testing). If None, loads from disk.
        limit:          Max traces to scan.
        min_frequency:  Minimum occurrences to surface a friction point.

    Returns:
        HarnessFrictionReport with ranked friction points.
    """
    import uuid as _uuid
    import time as _time
    run_id = _uuid.uuid4().hex[:8]
    started = _time.monotonic()

    if traces is None:
        traces = _load_all_traces(limit=limit)

    if not traces:
        return HarnessFrictionReport(
            run_id=run_id, traces_analyzed=0, skipped=True,
            skip_reason="no traces found",
        )

    n_traces = len(traces)

    # Counters
    error_counter: Counter = Counter()
    error_examples: Dict[str, List[str]] = {}
    phase_counter: Counter = Counter()
    retry_total: int = 0
    retry_examples: List[str] = []
    timeout_counter: Counter = Counter()
    tool_error_counter: Counter = Counter()
    tool_error_examples: Dict[str, List[str]] = {}

    for trace in traces:
        for step in trace.get("steps", []):
            stuck_reason = step.get("stuck_reason", "") or ""
            result = step.get("result", "") or ""
            status = step.get("status", "")
            step_text = step.get("step", "") or ""

            combined = f"{stuck_reason} {result}".strip()
            if not combined:
                continue

            err_type = _classify_error(combined)
            if err_type == "timeout":
                timeout_counter[step_text[:60] or "unknown_step"] += 1
            elif err_type in ("adapter_error", "rate_limit"):
                key = err_type
                error_counter[key] += 1
                if key not in error_examples:
                    error_examples[key] = []
                if len(error_examples[key]) < 3:
                    error_examples[key].append(combined[:120])
            elif err_type == "retry_storm":
                retry_total += 1
                if len(retry_examples) < 3:
                    retry_examples.append(combined[:120])
            elif err_type == "tool_error":
                key = "tool_error"
                tool_error_counter[key] += 1
                if key not in tool_error_examples:
                    tool_error_examples[key] = []
                if len(tool_error_examples[key]) < 3:
                    tool_error_examples[key].append(combined[:120])
            elif err_type == "phase_failure":
                phase = step.get("phase", "unknown")
                phase_counter[phase] += 1

    friction_points: List[FrictionPoint] = []

    # Adapter errors
    for err_type, count in error_counter.items():
        if count < min_frequency:
            continue
        rate = count / n_traces
        severity = "high" if rate > 0.3 else ("medium" if rate > 0.1 else "low")
        hint = {
            "adapter_error": (
                "Check adapter kwargs compatibility (e.g. thinking_budget not supported by subprocess). "
                "Consider adding **kwargs to adapter.complete() or fixing caller kwargs."
            ),
            "rate_limit": (
                "Rate limit errors recur — consider increasing backoff delay, switching to a lower-tier "
                "model for cheap steps, or adding request spreading."
            ),
        }.get(err_type, f"Recurring {err_type} — investigate adapter layer.")
        friction_points.append(FrictionPoint(
            friction_type=err_type,
            signal=f"{err_type}: {count}/{n_traces} traces affected",
            frequency=count,
            total_traces=n_traces,
            examples=error_examples.get(err_type, []),
            suggestion=hint,
            severity=severity,
        ))

    # Timeouts
    top_timeouts = timeout_counter.most_common(3)
    total_timeouts = sum(timeout_counter.values())
    if total_timeouts >= min_frequency:
        rate = total_timeouts / n_traces
        severity = "high" if rate > 0.3 else ("medium" if rate > 0.1 else "low")
        examples = [f"{step[:80]} ({cnt}×)" for step, cnt in top_timeouts]
        friction_points.append(FrictionPoint(
            friction_type="timeout",
            signal=f"timeout: {total_timeouts}/{n_traces} steps timed out",
            frequency=total_timeouts,
            total_traces=n_traces,
            examples=examples,
            suggestion=(
                "Timeout hotspot detected. Consider: (1) increasing POE_LONG_RUNNING_TIMEOUT for "
                "full-suite steps, (2) splitting long steps into smaller atomic units, "
                "(3) adding streaming progress checks."
            ),
            severity=severity,
        ))

    # Retry storms
    if retry_total >= min_frequency:
        rate = retry_total / n_traces
        severity = "high" if rate > 0.3 else ("medium" if rate > 0.1 else "low")
        friction_points.append(FrictionPoint(
            friction_type="retry_storm",
            signal=f"retry_storm: {retry_total} retry-related events in {n_traces} traces",
            frequency=retry_total,
            total_traces=n_traces,
            examples=retry_examples,
            suggestion=(
                "Frequent retries signal brittle steps. Consider: (1) adding pre-flight validation "
                "so failed steps fail fast, (2) lower retry counts for deterministic steps, "
                "(3) investigate root cause of step brittleness."
            ),
            severity=severity,
        ))

    # Tool errors
    total_tool_errors = sum(tool_error_counter.values())
    if total_tool_errors >= min_frequency:
        rate = total_tool_errors / n_traces
        severity = "high" if rate > 0.3 else ("medium" if rate > 0.1 else "low")
        friction_points.append(FrictionPoint(
            friction_type="tool_error",
            signal=f"tool_error: {total_tool_errors} tool call parsing failures",
            frequency=total_tool_errors,
            total_traces=n_traces,
            examples=tool_error_examples.get("tool_error", []),
            suggestion=(
                "Tool call parsing failures. Consider: (1) strengthening JSON output validation "
                "in EXECUTE_TOOLS, (2) adding a repair pass for malformed tool calls, "
                "(3) checking if model output truncation is causing JSON truncation."
            ),
            severity=severity,
        ))

    # Phase failures
    for phase, count in phase_counter.most_common(3):
        if count < min_frequency:
            continue
        rate = count / n_traces
        severity = "high" if rate > 0.3 else ("medium" if rate > 0.1 else "low")
        friction_points.append(FrictionPoint(
            friction_type="phase_failure",
            signal=f"phase_failure: {phase} failed {count}/{n_traces} traces",
            frequency=count,
            total_traces=n_traces,
            examples=[f"phase={phase}, count={count}"],
            suggestion=(
                f"Phase '{phase}' frequently fails. Review phase transition guards and "
                f"error recovery for this phase in agent_loop.py."
            ),
            severity=severity,
        ))

    # Sort: high severity first, then by frequency
    _SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
    friction_points.sort(key=lambda f: (_SEV_ORDER.get(f.severity, 3), -f.frequency))

    return HarnessFrictionReport(
        run_id=run_id,
        traces_analyzed=n_traces,
        friction_points=friction_points,
        elapsed_ms=int((_time.monotonic() - started) * 1000),
    )


def _save_friction_suggestions(
    friction_points: List[FrictionPoint],
    run_id: str,
    dry_run: bool = False,
) -> int:
    """Convert friction points to evolver Suggestions. Returns count saved."""
    if not friction_points or dry_run:
        return 0
    try:
        from evolver import Suggestion, _save_suggestions
        suggestions = []
        for i, fp in enumerate(friction_points):
            if fp.severity == "low":
                continue  # only surface medium+ friction
            suggestions.append(Suggestion(
                suggestion_id=f"friction-{run_id}-{i:02d}",
                category="harness_friction",
                target=f"harness:{fp.friction_type}",
                suggestion=fp.suggestion,
                failure_pattern=fp.signal,
                confidence=fp.rate,  # frequency rate as confidence proxy
                outcomes_analyzed=fp.total_traces,
            ))
        if suggestions:
            _save_suggestions(suggestions)
        return len(suggestions)
    except Exception as exc:
        log.warning("friction_scan: failed to save suggestions: %s", exc)
        return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Harness self-optimization loop")
    parser.add_argument("--dry-run", action="store_true", help="Analyze without saving proposals")
    parser.add_argument("--targets", nargs="+", default=["EXECUTE_SYSTEM", "DECOMPOSE_SYSTEM"],
                        metavar="TARGET", help="Prompt targets to analyze")
    parser.add_argument("--min-traces", type=int, default=2,
                        help="Minimum stuck traces required (default: 2)")
    parser.add_argument("--max-traces", type=int, default=10,
                        help="Maximum stuck traces to load (default: 10)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)
    report = run_harness_optimizer(
        targets=args.targets,
        max_traces=args.max_traces,
        min_stuck_traces=args.min_traces,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )
    print(report.summary())
    if not report.skipped and report.proposals:
        for p in report.proposals:
            print(f"\n[{p.target}] confidence={p.confidence:.0%}")
            print(f"  Pattern: {p.failure_pattern}")
            print(f"  Change: {p.proposed_change[:120]}")


if __name__ == "__main__":
    main()
