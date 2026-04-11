#!/usr/bin/env python3
"""Compact notation A/B test — measure token savings from shorthand vocabulary.

Runs the same step(s) with and without the compact_notation skill injected,
compares output token counts and result quality.

Usage:
    # Dry-run (mock adapter, validates harness):
    python3 -m compact_ab --dry-run

    # Live test with cheap model:
    python3 -m compact_ab --model cheap

    # Live test with specific step text:
    python3 -m compact_ab --model cheap --step "Analyze the top 5 nootropics by evidence quality"

    # Multiple rounds for statistical confidence:
    python3 -m compact_ab --model cheap --rounds 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.compact_ab")

# ---------------------------------------------------------------------------
# Test prompts — single-turn steps suitable for A/B comparison
# ---------------------------------------------------------------------------

DEFAULT_STEPS = [
    "Analyze the top 5 nootropics for cognitive enhancement, comparing evidence quality and mechanism of action",
    "Research the current state of autonomous AI agent architectures, focusing on memory and self-improvement",
    "Compare three approaches to Python dependency management: pip, poetry, and uv",
]

# ---------------------------------------------------------------------------
# Compact notation skill content
# ---------------------------------------------------------------------------

def _load_compact_skill() -> str:
    """Load the compact notation skill markdown."""
    skill_path = Path(__file__).resolve().parent.parent / "skills" / "compact_notation.md"
    if not skill_path.exists():
        raise FileNotFoundError(f"compact_notation.md not found at {skill_path}")
    text = skill_path.read_text(encoding="utf-8")
    # Strip YAML frontmatter
    if text.startswith("---"):
        _, _, text = text.split("---", 2)
    return text.strip()


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ABRound:
    """One A/B comparison round."""
    step_text: str
    control_tokens_out: int
    treatment_tokens_out: int
    control_tokens_in: int
    treatment_tokens_in: int
    control_result_len: int
    treatment_result_len: int
    token_reduction_pct: float  # negative = treatment used MORE tokens
    control_status: str
    treatment_status: str
    round_idx: int = 0


@dataclass
class ABReport:
    """Summary of all A/B rounds."""
    rounds: List[ABRound] = field(default_factory=list)
    model: str = ""
    avg_token_reduction_pct: float = 0.0
    median_token_reduction_pct: float = 0.0
    total_control_tokens: int = 0
    total_treatment_tokens: int = 0
    quality_notes: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def summary(self) -> str:
        lines = [
            f"Compact Notation A/B Test — {len(self.rounds)} rounds, model={self.model}",
            f"  Control (no notation):  {self.total_control_tokens} output tokens",
            f"  Treatment (with notation): {self.total_treatment_tokens} output tokens",
            f"  Avg reduction: {self.avg_token_reduction_pct:+.1f}%",
            f"  Median reduction: {self.median_token_reduction_pct:+.1f}%",
        ]
        if self.avg_token_reduction_pct >= 15:
            lines.append("  VERDICT: >=15% reduction — recommend always_inject=true")
        elif self.avg_token_reduction_pct >= 5:
            lines.append("  VERDICT: 5-15% reduction — marginal, consider context-specific injection")
        else:
            lines.append("  VERDICT: <5% reduction — not worth the prompt overhead")
        for note in self.quality_notes:
            lines.append(f"  Note: {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core test runner
# ---------------------------------------------------------------------------

def run_ab_step(
    step_text: str,
    adapter,
    *,
    round_idx: int = 0,
    verbose: bool = False,
) -> ABRound:
    """Run one A/B comparison: control (no notation) vs treatment (with notation)."""
    from step_exec import execute_step, EXECUTE_TOOLS
    from llm import LLMTool

    tools = [LLMTool(**t) for t in EXECUTE_TOOLS]
    goal = "A/B test: compare output with and without compact notation"

    # --- Control: no compact notation ---
    if verbose:
        print(f"  [control] running step...", file=sys.stderr, flush=True)
    control = execute_step(
        goal=goal,
        step_text=step_text,
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=tools,
        verbose=False,
    )

    # --- Treatment: with compact notation ---
    compact_skill = _load_compact_skill()
    treatment_ancestry = (
        "ACTIVE SKILL: Compact Notation\n"
        "Use the following shorthand in your step results and summaries.\n\n"
        f"{compact_skill}"
    )
    if verbose:
        print(f"  [treatment] running step...", file=sys.stderr, flush=True)
    treatment = execute_step(
        goal=goal,
        step_text=step_text,
        step_num=1,
        total_steps=1,
        completed_context=[],
        adapter=adapter,
        tools=tools,
        verbose=False,
        ancestry_context=treatment_ancestry,
    )

    # --- Compare ---
    c_out = control.get("tokens_out", 0)
    t_out = treatment.get("tokens_out", 0)
    reduction = ((c_out - t_out) / c_out * 100) if c_out > 0 else 0.0

    return ABRound(
        step_text=step_text,
        control_tokens_out=c_out,
        treatment_tokens_out=t_out,
        control_tokens_in=control.get("tokens_in", 0),
        treatment_tokens_in=treatment.get("tokens_in", 0),
        control_result_len=len(control.get("result", "")),
        treatment_result_len=len(treatment.get("result", "")),
        token_reduction_pct=reduction,
        control_status=control.get("status", "?"),
        treatment_status=treatment.get("status", "?"),
        round_idx=round_idx,
    )


def run_ab_test(
    steps: Optional[List[str]] = None,
    *,
    model: str = "cheap",
    rounds: int = 1,
    dry_run: bool = False,
    verbose: bool = False,
) -> ABReport:
    """Run the full A/B test across multiple steps and rounds."""
    if dry_run:
        from agent_loop import _DryRunAdapter
        adapter = _DryRunAdapter()
    else:
        from llm import build_adapter
        adapter = build_adapter(model=model)

    steps = steps or DEFAULT_STEPS
    all_rounds: List[ABRound] = []

    for r in range(rounds):
        for i, step_text in enumerate(steps):
            if verbose:
                print(f"Round {r+1}/{rounds}, step {i+1}/{len(steps)}: {step_text[:60]}...",
                      file=sys.stderr, flush=True)
            ab = run_ab_step(step_text, adapter, round_idx=r, verbose=verbose)
            all_rounds.append(ab)
            if verbose:
                print(f"  → control={ab.control_tokens_out}tok, treatment={ab.treatment_tokens_out}tok, "
                      f"reduction={ab.token_reduction_pct:+.1f}%",
                      file=sys.stderr, flush=True)

    # Aggregate
    reductions = [r.token_reduction_pct for r in all_rounds]
    sorted_reductions = sorted(reductions)
    median_idx = len(sorted_reductions) // 2

    report = ABReport(
        rounds=all_rounds,
        model=model if not dry_run else "dry-run",
        avg_token_reduction_pct=sum(reductions) / len(reductions) if reductions else 0,
        median_token_reduction_pct=sorted_reductions[median_idx] if sorted_reductions else 0,
        total_control_tokens=sum(r.control_tokens_out for r in all_rounds),
        total_treatment_tokens=sum(r.treatment_tokens_out for r in all_rounds),
    )

    # Quality notes
    both_done = all(r.control_status == "done" and r.treatment_status == "done" for r in all_rounds)
    if not both_done:
        blocked = sum(1 for r in all_rounds if r.treatment_status != "done")
        report.quality_notes.append(f"{blocked}/{len(all_rounds)} treatment steps blocked — quality concern")

    # Check for result length regression (treatment much shorter could mean missing content)
    for r in all_rounds:
        if r.control_result_len > 0 and r.treatment_result_len < r.control_result_len * 0.3:
            report.quality_notes.append(
                f"Round {r.round_idx}: treatment result is {r.treatment_result_len}/{r.control_result_len} chars — "
                f"possible content loss"
            )

    return report


# ---------------------------------------------------------------------------
# Artifact persistence
# ---------------------------------------------------------------------------

def save_report(report: ABReport, path: Optional[Path] = None) -> Path:
    """Save report to output/compact_ab/ for tracking over time."""
    if path is None:
        try:
            from orch_items import memory_dir
            out_dir = memory_dir().parent / "output" / "compact_ab"
        except Exception:
            out_dir = Path("output") / "compact_ab"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"ab-{report.model}-{ts}.json"

    data = {
        "summary": report.summary(),
        "model": report.model,
        "avg_reduction_pct": report.avg_token_reduction_pct,
        "median_reduction_pct": report.median_token_reduction_pct,
        "total_control_tokens": report.total_control_tokens,
        "total_treatment_tokens": report.total_treatment_tokens,
        "quality_notes": report.quality_notes,
        "rounds": [asdict(r) for r in report.rounds],
        "timestamp": report.timestamp,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Compact notation A/B test")
    parser.add_argument("--model", default="cheap", help="Model tier (cheap/mid/power)")
    parser.add_argument("--rounds", type=int, default=1, help="Number of rounds per step")
    parser.add_argument("--step", type=str, default=None, help="Custom step text (overrides defaults)")
    parser.add_argument("--dry-run", action="store_true", help="Use mock adapter")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    steps = [args.step] if args.step else None

    report = run_ab_test(
        steps=steps,
        model=args.model,
        rounds=args.rounds,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    if args.json:
        print(json.dumps({
            "summary": report.summary(),
            "avg_reduction_pct": report.avg_token_reduction_pct,
            "rounds": [asdict(r) for r in report.rounds],
        }, indent=2))
    else:
        print(report.summary())

    # Persist
    try:
        path = save_report(report)
        if args.verbose:
            print(f"\nReport saved: {path}", file=sys.stderr, flush=True)
    except Exception as exc:
        if args.verbose:
            print(f"\nFailed to save report: {exc}", file=sys.stderr, flush=True)

    # Exit code: 0 if >=15% reduction, 1 otherwise
    return 0 if report.avg_token_reduction_pct >= 15 else 1


if __name__ == "__main__":
    raise SystemExit(main())
