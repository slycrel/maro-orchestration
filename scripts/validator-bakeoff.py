#!/usr/bin/env python3
"""Replay the committed local-validator corpus against one live model.

The production ladder only skips paid verification when local confidence meets
``validate.min_certainty``. Report both raw label accuracy and the operational
metrics that matter: decisive coverage, accuracy among decisive calls, and
unsafe decisive false-passes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import local_models as lm  # noqa: E402
from verification_agent import VerificationAgent  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "validation_cases.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--min-certainty", type=float, default=lm.min_certainty())
    args = parser.parse_args(argv)

    loaded = lm.loaded_models(args.endpoint)
    if args.model not in loaded:
        parser.error(f"model {args.model!r} is not loaded at {args.endpoint}; loaded={loaded}")

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    adapter = lm.LocalValidatorAdapter(args.model, endpoint=args.endpoint)
    verifier = VerificationAgent(
        adapter,
        confidence_threshold=args.min_certainty,
        max_input_chars=lm.input_char_budget(),
    )

    rows = []
    started = time.monotonic()
    for case in cases:
        call_started = time.monotonic()
        verdict = verifier.verify_step(case["step_text"], case["result"])
        elapsed = time.monotonic() - call_started
        expected = bool(case["expect_pass"])
        decisive = verdict.confidence >= args.min_certainty
        rows.append({
            "id": case["id"],
            "expected_pass": expected,
            "predicted_pass": verdict.passed,
            "confidence": round(verdict.confidence, 4),
            "decisive": decisive,
            "correct": verdict.passed == expected,
            "unsafe_false_pass": decisive and not expected and verdict.passed,
            "elapsed_s": round(elapsed, 3),
            "reason": verdict.reason,
        })

    total_s = time.monotonic() - started
    decisive_rows = [row for row in rows if row["decisive"]]
    result = {
        "model": args.model,
        "endpoint": args.endpoint,
        "case_count": len(rows),
        "min_certainty": args.min_certainty,
        "raw_correct": sum(row["correct"] for row in rows),
        "raw_accuracy": sum(row["correct"] for row in rows) / len(rows),
        "decisive_count": len(decisive_rows),
        "decisive_coverage": len(decisive_rows) / len(rows),
        "decisive_correct": sum(row["correct"] for row in decisive_rows),
        "decisive_accuracy": (
            sum(row["correct"] for row in decisive_rows) / len(decisive_rows)
            if decisive_rows else None
        ),
        "unsafe_decisive_false_passes": sum(
            row["unsafe_false_pass"] for row in rows
        ),
        "total_s": round(total_s, 3),
        "average_s": round(total_s / len(rows), 3),
        "rows": rows,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
