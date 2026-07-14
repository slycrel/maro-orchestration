#!/usr/bin/env python3
"""§7 A/B: does the worker recall slice move outcomes?

Same director missions run twice — arm A with memory.worker_slice off
(today's behavior), arm B with it on (in-process patch of
director.config_get; shared config files are never touched, so nothing
else on the box can inherit the flag). Interleaved A,B per mission to
reduce drift. Results append to output/experiments/worker_slice_ab.jsonl.

Measures (per brief §7): director status (closure), per-worker status +
token in/out, whether the slice actually injected, wall time.

Usage:
    PYTHONPATH=src python3 scripts/worker_slice_ab.py [--dry-run]
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OUT = Path(__file__).resolve().parent.parent / \
    "output" / "experiments" / "worker_slice_ab.jsonl"

MISSIONS = [
    ("m1-polymarket",
     "Research what makes Polymarket prediction-market edges persist over "
     "time and summarize 3 actionable strategies with supporting evidence."),
    ("m2-ops-review",
     "Review common failure patterns in autonomous agent runs (timeouts, "
     "fabricated results, scope drift) and propose the top 3 operational "
     "guardrails, each with a concrete detection signal."),
    ("m3-host-monitoring",
     "Design a monitoring checklist for an always-on autonomous agent host: "
     "disk, token spend, orphaned processes, stale heartbeats. Give a "
     "concrete detection command for each item."),
]


def run_arm(
    directive: str,
    slice_on: bool,
    dry_run: bool,
    *,
    batch_id: str,
    cell_id: str,
) -> dict:
    import director
    from benchmark_isolation import benchmark_project_slug, benchmark_workspace

    orig_get = director.config_get

    def patched(key, default=None):
        if key == "memory.worker_slice":
            return True
        return orig_get(key, default)

    if slice_on:
        director.config_get = patched
    try:
        # Direct Director calls do not own a loop project/cwd. Bind every
        # experiment cell to a fresh retained workspace so workers cannot
        # mutate the launch repo or consume another cell's artifacts.
        with benchmark_workspace(batch_id, cell_id) as cell_workspace:
            t0 = time.time()
            try:
                result = director.run_director(
                    directive,
                    project=benchmark_project_slug(batch_id, cell_id),
                    dry_run=dry_run,
                    verbose=False,
                )
            except Exception as exc:
                # The failed cell is evidence too. Preserve its workspace
                # pointer instead of letting main() collapse the row to a bare
                # exception with no way to inspect partial artifacts.
                return {
                    "status": f"error: {exc}",
                    "workspace": str(cell_workspace),
                }
            elapsed = round(time.time() - t0, 1)
    finally:
        director.config_get = orig_get

    return {
        "status": result.status,
        "worker_slice": result.worker_slice,
        "elapsed_s": elapsed,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "workers": [
            {
                "type": r.worker_type,
                "status": r.status,
                "tokens_in": getattr(r, "tokens_in", 0),
                "tokens_out": getattr(r, "tokens_out", 0),
                "slice_injected": getattr(r, "memory_slice_injected", False),
                "result_len": len(r.result or ""),
            }
            for r in result.worker_results
        ],
        "log_path": result.log_path,
        "workspace": str(cell_workspace),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="plumbing check only, no LLM calls")
    ap.add_argument("--reps", type=int, default=1,
                    help="repetitions per (mission, arm) pair")
    opts = ap.parse_args()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-%fZ")

    for rep in range(opts.reps):
      for mission_id, directive in MISSIONS:
        for arm, slice_on in (("A-off", False), ("B-on", True)):
            print(f"=== {mission_id} arm {arm} rep {rep + 1} ===", flush=True)
            cell_id = f"{mission_id}-rep-{rep + 1}-{arm}"
            try:
                row = run_arm(
                    directive,
                    slice_on,
                    opts.dry_run,
                    batch_id=batch_id,
                    cell_id=cell_id,
                )
            except Exception as exc:
                row = {"status": f"error: {exc}"}
            row.update({"mission": mission_id, "arm": arm, "rep": rep + 1,
                        "dry_run": opts.dry_run, "batch": stamp})
            with open(OUT, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(json.dumps({k: row.get(k) for k in
                              ("mission", "arm", "status", "worker_slice",
                               "elapsed_s", "tokens_in", "tokens_out")}),
                  flush=True)

    print(f"done → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
