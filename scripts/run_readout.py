#!/usr/bin/env python3
"""Post-hoc readout for a finished run: what it really cost, and what the
terrain memory was worth.

Exists because both numbers were got wrong by hand, twice each.

**Cost.** Every figure in the LT arc's four-run cost series was ~3-4x too
high (2026-08-02 retraction). The mistake: summing ``tokens_in`` across call
records and pricing it at the fresh-input rate. ``metrics.estimate_cost``
says plainly that ``tokens_in`` is the TOTAL input volume *including cache
reads*, which bill at 0.1x — and on the subprocess backend ~90% of input
volume is cache reads, because every tool round-trip re-sends the growing
step conversation. The codebase had already learned this (loop_execute.py:
"full-rate pricing ... inflated azure-finch roughly 10x on batch steps and
the breaker hard-stopped the run one step before its final synthesis"); the
analysis layer re-made it anyway. So: **never derive a run's cost. Read the
one the run recorded.** This script prints all three columns side by side,
naive included and labelled, so the gap stays visible instead of plausible.

**Terrain.** ``avoidable_retries`` counts tool calls aimed at a host that
was ALREADY recorded hard-blocked in an EARLIER step — precisely the waste
RUN_TEACHINGS §5b closes, and the number that makes a terrain prediction
falsifiable after the fact. It imports ``terrain`` rather than re-deriving
the host/block rules: a checker that disagrees with the thing it checks is
worse than no checker (the first hand-rolled version of this used a
narrower host extractor and confidently reported 0 avoidable retries on a
run that had 23).

Usage:
    python3 scripts/run_readout.py                       # all runs in the workspace
    python3 scripts/run_readout.py 9d88acf2 4bf7f761     # by handle-id prefix
    python3 scripts/run_readout.py --json
    python3 scripts/run_readout.py --runs-dir DIR

Exit status is 0 whenever the readout ran. Read-only: nothing here writes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

try:
    from terrain import TerrainMemory, scan_tool_events, _host_of, _BLOCK_SIGNALS
except ImportError as exc:  # pragma: no cover - dev tool, src must be present
    print(f"run_readout needs src/terrain.py on the path: {exc}", file=sys.stderr)
    raise SystemExit(2)

# Fresh-input rate used only to reproduce the WRONG number for contrast.
# Deliberately a local constant: this is the mistake being displayed, not a
# price the rest of the system should ever consult.
_NAIVE_INPUT_PER_M = 3.0
_NAIVE_OUTPUT_PER_M = 15.0


def classify_tool_events(events: Any) -> Dict[str, Any]:
    """Coarse triage of one step's tool events, with an explicit residue.

    The point is NOT to classify everything. It is to make the pile we
    *couldn't* classify visible and countable, so an unknown failure mode
    surfaces as a number on day one instead of being found by accident six
    weeks later (see the AWS-WAF 202: a block that no signal recognized,
    caught only because an unrelated path happened to 403).

    Two things this deliberately does not do:

    * **It does not use ``is_error`` as the failure denominator.** Measured
      across the workspace 2026-08-02: of ~160 recognizable blocks, only 4
      carried ``is_error``. `curl` exits 0 and prints the 403 body, so a
      block is usually a *successful* tool call containing failure text. The
      transport succeeds; the semantics fail.
    * **It does not guess at "transient" or "got a payload".** A first draft
      classified both, and both were visibly wrong on real data — the
      transient regex matched the word "timeout" inside successful Overpass
      JSON, and a <200-byte rule flagged "File created successfully" as
      suspect. Anything not recognized with confidence goes to residue,
      where a human can look at it. Coarse and honest beats fine and wrong.
    """
    out = {"total": 0, "blocked": 0, "tool_missing": 0,
           "errors": 0, "residue": 0, "residue_samples": []}
    if not events:
        return out
    for event in events:
        if not isinstance(event, dict):
            continue
        out["total"] += 1
        blob = f"{event.get('output', '')} {event.get('error', '')}"
        if any(pattern.search(blob) for pattern, _ in _BLOCK_SIGNALS):
            out["blocked"] += 1
            continue
        if "No such tool available" in blob or "Unknown skill:" in blob:
            out["tool_missing"] += 1
            continue
        if not event.get("is_error"):
            continue                      # not claiming this succeeded — just
        out["errors"] += 1                # not claiming it failed either
        out["residue"] += 1
        if len(out["residue_samples"]) < 8:
            out["residue_samples"].append(
                f"[{event.get('name')}] {' '.join(blob.split())[:100]}")
    return out


def _load(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _default_runs_dir() -> Path:
    ws = os.environ.get("MARO_WORKSPACE") or os.path.expanduser("~/.maro/workspace")
    return Path(ws) / "runs"


def read_run(run_dir: Path) -> Optional[Dict[str, Any]]:
    """Everything this readout knows about one run. None if it isn't one."""
    card = _load(run_dir / "run_card.json")
    if not isinstance(card, dict):
        return None

    # --- cost -------------------------------------------------------------
    # provider_cost_usd is the backend's own reported spend per step: the
    # authoritative figure. card_cost is maro's cache-aware estimate (what
    # the budget breaker actually reads). naive is the retracted method.
    provider = 0.0
    provider_seen = False
    steps: List[Dict[str, Any]] = []
    for log_path in sorted(run_dir.glob("build/loop-*-log.json")):
        log = _load(log_path)
        if isinstance(log, dict):
            for step in log.get("steps") or []:
                if isinstance(step, dict):
                    steps.append(step)
                    pc = step.get("provider_cost_usd")
                    if isinstance(pc, (int, float)):
                        provider += float(pc)
                        provider_seen = True

    tokens_in = tokens_out = 0
    call_paths = sorted(run_dir.glob("build/calls/*.json"))
    for call_path in call_paths:
        call = _load(call_path)
        if isinstance(call, dict):
            tokens_in += int(call.get("tokens_in") or 0)
            tokens_out += int(call.get("tokens_out") or 0)
    naive = (tokens_in * _NAIVE_INPUT_PER_M / 1_000_000
             + tokens_out * _NAIVE_OUTPUT_PER_M / 1_000_000)

    # --- terrain ----------------------------------------------------------
    memory = TerrainMemory()
    avoidable: List[str] = []
    step_no = 0
    triage = {"total": 0, "blocked": 0, "tool_missing": 0,
              "errors": 0, "residue": 0, "residue_samples": []}
    for call_path in call_paths:
        call = _load(call_path)
        events = (call or {}).get("tool_events") or []
        if not events:
            continue
        step_no += 1
        one = classify_tool_events(events)
        for key in ("total", "blocked", "tool_missing", "errors", "residue"):
            triage[key] += one[key]
        for sample in one["residue_samples"]:
            if len(triage["residue_samples"]) < 8:
                triage["residue_samples"].append(sample)
        for event in events:
            if not isinstance(event, dict):
                continue
            host = _host_of(str(event.get("input", "")))
            fact = memory.facts.get(host) if host else None
            if fact is not None and fact.first_step < step_no:
                avoidable.append(f"s{step_no}:{host}")
        scan_tool_events(events, step_no, memory)

    return {
        "handle_id": card.get("handle_id") or run_dir.name.split("-")[0],
        "nickname": card.get("nickname") or "",
        "steps": len(steps),
        "tool_steps": step_no,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "provider_cost_usd": round(provider, 4) if provider_seen else None,
        "card_cost_usd": card.get("total_cost_usd"),
        "naive_cost_usd_RETRACTED": round(naive, 4),
        "goal_achieved": card.get("goal_achieved"),
        "blocked_hosts": {h: {"reason": f.reason, "hits": f.hits, "steps": f.steps}
                          for h, f in memory.facts.items()},
        "promotable_hosts": [f.host for f in memory.promotable()],
        "avoidable_retries": len(avoidable),
        "avoidable_detail": avoidable,
        "tool_events": triage["total"],
        "tool_missing": triage["tool_missing"],
        "residue": triage["residue"],
        "residue_samples": triage["residue_samples"],
        "step_flags": card.get("step_flags") or {},
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("handles", nargs="*",
                        help="handle-id prefixes; default = every run found")
    parser.add_argument("--runs-dir", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    runs_dir = Path(args.runs_dir) if args.runs_dir else _default_runs_dir()
    if not runs_dir.is_dir():
        print(f"no runs dir at {runs_dir}", file=sys.stderr)
        return 1

    rows = []
    for run_dir in sorted(runs_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        if args.handles and not any(run_dir.name.startswith(h) for h in args.handles):
            continue
        row = read_run(run_dir)
        if row:
            rows.append(row)

    if args.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print("no runs matched")
        return 0

    print(f"{'run':<10} {'steps':>5} {'tok_in':>12} {'provider$':>10} "
          f"{'card$':>8} {'naive$*':>8} {'blocked':>7} {'avoid':>6} "
          f"{'tools':>6} {'nosuch':>6} {'residue':>7}")
    for row in rows:
        prov = row["provider_cost_usd"]
        card = row["card_cost_usd"]
        print(f"{row['handle_id']:<10} {row['steps']:>5} {row['tokens_in']:>12,} "
              f"{(f'{prov:.2f}' if prov is not None else '-'):>10} "
              f"{(f'{card:.2f}' if isinstance(card, (int, float)) else '-'):>8} "
              f"{row['naive_cost_usd_RETRACTED']:>8.2f} "
              f"{len(row['blocked_hosts']):>7} {row['avoidable_retries']:>6} "
              f"{row['tool_events']:>6} {row['tool_missing']:>6} {row['residue']:>7}")
    print("\n* naive$ is the RETRACTED method (full-rate pricing of cached input) —")
    print("  shown only so the ~3-4x gap stays visible. provider$ is the real spend.")
    for row in rows:
        if row["blocked_hosts"]:
            detail = ", ".join(f"{h} ({v['reason']}, {v['hits']}x)"
                               for h, v in row["blocked_hosts"].items())
            print(f"\n{row['handle_id']} blocked: {detail}")
            if row["avoidable_retries"]:
                print(f"{row['handle_id']} avoidable retries "
                      f"({row['avoidable_retries']}): {', '.join(row['avoidable_detail'][:12])}"
                      + (" ..." if row["avoidable_retries"] > 12 else ""))
    for row in rows:
        too_broad = row["step_flags"].get("too_broad") or []
        if too_broad:
            detail = ", ".join(
                f"step {f.get('step_index')} "
                f"({f.get('elapsed_s')}s/{(f.get('tokens') or 0) // 1000}K vs "
                f"caps {f.get('cap_elapsed_s')}s/{(f.get('cap_tokens') or 0) // 1000}K)"
                for f in too_broad)
            print(f"\n{row['handle_id']} steps over watchdog caps "
                  f"({len(too_broad)}): {detail}")
    for row in rows:
        if row["residue_samples"]:
            print(f"\n{row['handle_id']} residue — errors we could not explain "
                  f"({row['residue']}); this is the work-list, not noise:")
            for sample in row["residue_samples"]:
                print(f"    {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
