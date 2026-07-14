#!/usr/bin/env python3
"""Measure fresh Claude subprocesses versus one resumed boundary segment.

This is a retained-evidence experiment, not a production session manager.
Each arm gets equivalent but arm-tagged context so prompt-cache entries cannot
cross-contaminate the comparison.  Fresh calls receive the whole segment state
on every step; the resumed arm receives it once and then relies on session
history, matching the parked per-boundary-segment proposal in BACKLOG.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any


FACTS = {
    "codename": "KESTREL",
    "port": "4317",
    "budget": "$1.75",
    "policy": "keep evidence files",
}

STEPS = [
    ("Reply with exactly the segment codename.", "KESTREL"),
    ("Reply with exactly the telemetry port.", "4317"),
    ("Reply with exactly the run budget, including the dollar sign.", "$1.75"),
    ("Reply with exactly the evidence policy, lowercase.", "keep evidence files"),
    (
        "Reply with one compact JSON object containing codename, port, budget, "
        "and policy, using the exact values from the reference material.",
        None,
    ),
]


def _segment_state(arm: str, padding_lines: int, nonce: str = "test") -> str:
    arm_tag = f"{arm}-{nonce}"
    facts = "\n".join(f"- {key}: {value}" for key, value in FACTS.items())
    padding = "\n".join(
        f"{arm_tag} reference line {i:04d}: telemetry samples use stable labels, "
        "bounded values, and documented units."
        for i in range(padding_lines)
    )
    return (
        "REFERENCE MATERIAL FOR A FIVE-QUESTION RECALL BENCHMARK\n"
        f"{facts}\n\n"
        "The following neutral reference entries approximate Maro's ordinary "
        "re-injected step-context size:\n"
        f"{padding}\n\n"
        "Answer the question after the reference material. "
    )


def _correct(step_index: int, result: str, expected: str | None) -> bool:
    text = result.strip()
    if expected is not None:
        return text == expected
    if text.startswith("```json") and text.endswith("```"):
        text = text[len("```json"): -len("```")].strip()
    try:
        payload = json.loads(text)
    except ValueError:
        return False
    return (
        isinstance(payload, dict)
        and set(payload) == set(FACTS)
        and all(str(payload.get(key)) == value for key, value in FACTS.items())
    )


def _invoke(
    *,
    claude_bin: str,
    model: str,
    prompt: str,
    cwd: Path,
    resume: str = "",
) -> tuple[dict[str, Any], int]:
    cmd = [
        claude_bin,
        "-p",
        "--model", model,
        "--output-format", "json",
        "--tools", "",
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
    ]
    if resume:
        cmd.extend(["--resume", resume])
    cmd.append(prompt)
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    wall_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}"
        )
    try:
        return json.loads(proc.stdout), wall_ms
    except ValueError as exc:
        raise RuntimeError(f"claude returned non-JSON: {proc.stdout[:500]}") from exc


def _run_arm(
    name: str,
    *,
    resumed: bool,
    output_dir: Path,
    claude_bin: str,
    model: str,
    padding_lines: int,
    nonce: str,
) -> list[dict[str, Any]]:
    state = _segment_state(name, padding_lines, nonce)
    session_id = ""
    rows = []
    for index, (instruction, expected) in enumerate(STEPS, 1):
        prompt = instruction
        if not resumed or index == 1:
            prompt = state + instruction
        raw, wall_ms = _invoke(
            claude_bin=claude_bin,
            model=model,
            prompt=prompt,
            cwd=output_dir,
            resume=session_id if resumed and index > 1 else "",
        )
        if resumed and index == 1:
            session_id = str(raw.get("session_id") or "")
            if not session_id:
                raise RuntimeError("first resumed-arm call returned no session_id")
        result = str(raw.get("result") or "")
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        row = {
            "arm": name,
            "step": index,
            "resumed": resumed and index > 1,
            "session_id": raw.get("session_id"),
            "wall_ms": wall_ms,
            "duration_ms": raw.get("duration_ms"),
            "duration_api_ms": raw.get("duration_api_ms"),
            "cost_usd": raw.get("total_cost_usd"),
            "input_tokens": usage.get("input_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "result": result,
            "correct": _correct(index, result, expected),
        }
        rows.append(row)
        (output_dir / f"{name}-step-{index}.json").write_text(
            json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return rows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "steps": len(rows),
        "correct": sum(bool(row["correct"]) for row in rows),
        "wall_ms": sum(int(row["wall_ms"]) for row in rows),
        "duration_api_ms": sum(int(row["duration_api_ms"] or 0) for row in rows),
        "cost_usd": round(sum(float(row["cost_usd"] or 0) for row in rows), 6),
        "input_tokens": sum(int(row["input_tokens"] or 0) for row in rows),
        "cache_creation_input_tokens": sum(
            int(row["cache_creation_input_tokens"] or 0) for row in rows),
        "cache_read_input_tokens": sum(
            int(row["cache_read_input_tokens"] or 0) for row in rows),
        "output_tokens": sum(int(row["output_tokens"] or 0) for row in rows),
    }


def _reserve_output_dir(output_dir: Path) -> None:
    """Atomically refuse an identity already holding retained evidence."""
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(exist_ok=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--claude-bin", default="claude")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--padding-lines", type=int, default=1200)
    parser.add_argument("--nonce", default="", help="cache-isolation tag; default is time-based")
    parser.add_argument(
        "--arm-order", choices=("resumed-first", "fresh-first"),
        default="resumed-first",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    _reserve_output_dir(output_dir)

    nonce = args.nonce or str(time.time_ns())
    arm_specs = [
        ("resumed", True),
        ("fresh", False),
    ]
    if args.arm_order == "fresh-first":
        arm_specs.reverse()
    rows = []
    for name, resumed in arm_specs:
        rows.extend(_run_arm(
            name, resumed=resumed, output_dir=output_dir,
            claude_bin=args.claude_bin, model=args.model,
            padding_lines=args.padding_lines, nonce=nonce,
        ))

    summary = {
        "model": args.model,
        "padding_lines": args.padding_lines,
        "nonce": nonce,
        "arm_order": args.arm_order,
        "arms": {
            arm: _aggregate([row for row in rows if row["arm"] == arm])
            for arm in ("resumed", "fresh")
        },
        "rows": rows,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if all(row["correct"] for row in rows) else 2


if __name__ == "__main__":
    raise SystemExit(main())
