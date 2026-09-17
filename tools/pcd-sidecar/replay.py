#!/usr/bin/env python3
"""Replay tests/fixtures/validation_cases.json through a running sidecar as a
judge Request, and report agreement against the labelled expect_pass.

Each case becomes: state = {"goal": step_text, "result": result}, one
Choice question `outcome` over done/blocked/unclear. done => predicted pass;
blocked/unclear => predicted fail. This is a comparison harness only -- it
never writes result numbers into the repo (see BACKLOG contract guardrail
on the Jev comparison); numbers only ever go to stdout / the caller's report.

Usage:
    python replay.py http://192.168.0.50:8765
    python replay.py http://192.168.0.50:8765 --fixtures tests/fixtures/validation_cases.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUTCOME_CRITERIA = {
    "done": "the result fully and correctly satisfies the stated goal",
    "blocked": "the result cannot proceed further, is empty, or explicitly failed/refused",
    "unclear": "there is not enough information in the result to tell whether the goal was satisfied",
}


def build_request(case: dict) -> dict:
    return {
        "model": "pcd-sidecar-replay",
        "state": {"goal": case["step_text"], "result": case["result"]},
        "questions": {
            "outcome": {
                "type": "choice",
                "instructions": (
                    "A step had this goal, and produced this result. Judge whether the "
                    "result satisfies the goal."
                ),
                "criteria": OUTCOME_CRITERIA,
            }
        },
    }


def post(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{url}/v1/systemone",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="base URL of a running pcd-sidecar, e.g. http://192.168.0.50:8765")
    ap.add_argument(
        "--fixtures",
        default=str(Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "validation_cases.json"),
    )
    args = ap.parse_args()

    fixtures = json.loads(Path(args.fixtures).read_text())
    cases = fixtures["cases"]

    rows = []
    agree = 0
    false_pass = 0
    false_false = 0
    for case in cases:
        payload = build_request(case)
        try:
            resp = post(args.url, payload)
        except urllib.error.URLError as e:
            print(f"REQUEST FAILED for case {case['id']!r}: {e}", file=sys.stderr)
            continue
        answer = resp["answers"]["outcome"]
        choice = answer["choice"]
        predicted_pass = choice == "done"
        expect_pass = bool(case["expect_pass"])
        correct = predicted_pass == expect_pass
        agree += int(correct)
        if predicted_pass and not expect_pass:
            false_pass += 1
        if not predicted_pass and expect_pass:
            false_false += 1
        rows.append(
            {
                "id": case["id"],
                "expect_pass": expect_pass,
                "choice": choice,
                "correct": correct,
                "confidence": answer["confidence"],
                "probabilities": answer["probabilities"],
            }
        )

    total = len(rows)
    print(f"sidecar: {args.url}   cases: {total}")
    print(f"{'id':<32} {'expect':<8} {'choice':<10} {'ok':<4} conf   probabilities")
    for r in rows:
        probs = ", ".join(f"{k}={v:.2f}" for k, v in r["probabilities"].items())
        print(
            f"{r['id']:<32} {str(r['expect_pass']):<8} {r['choice']:<10} "
            f"{'Y' if r['correct'] else 'N':<4} {r['confidence']:.2f}   {probs}"
        )
    print()
    print(f"agreement:   {agree}/{total} ({100.0 * agree / total:.1f}%)" if total else "agreement: n/a")
    print(f"false PASS:  {false_pass} (predicted done, labelled fail)")
    print(f"false FALSE: {false_false} (predicted not-done, labelled pass)")


if __name__ == "__main__":
    main()
