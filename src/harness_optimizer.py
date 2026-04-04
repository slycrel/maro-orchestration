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
