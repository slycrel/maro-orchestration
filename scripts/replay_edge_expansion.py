#!/usr/bin/env python3
"""Offline A/B replay for knowledge.edge_expansion — the receipts behind the
default-flip decision (edge-review r1, Minimalist/Skeptic: the 4/120 figure
shipped unreceipted; Architect: the original run compared ordered id LISTS,
so pure reorderings counted as "changed").

Measures the LITERAL production layer (edge-review r2, consensus HIGH: the
r1 rewrite still compared raw query_knowledge sets at min_confidence=0.0
with no char budget — a laxer surface than the one that fires the
KNOWLEDGE_EDGE_EXPANSION event). Each arm now replays
recall.py's exact call shape — query_knowledge(max_results=5,
min_confidence=0.3) followed by the shared _render_knowledge_entries char
budget at max_chars=600 — and compares the RENDERED id sets.

Read-only over the live store: replays the N most recent real goals from
memory/outcomes.jsonl; no times_applied bumps, no events.

Usage:
    PYTHONPATH=src python3 scripts/replay_edge_expansion.py [N]

Commit the output (docs/history/...) when a readout feeds a decision.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    workspace = Path(os.environ.get(
        "MARO_WORKSPACE", Path.home() / ".maro" / "workspace"))
    outcomes = workspace / "memory" / "outcomes.jsonl"
    if not outcomes.exists():
        sys.exit(f"no outcomes store at {outcomes}")

    from knowledge_web import query_knowledge, _render_knowledge_entries

    # recall.py:865 calls inject_knowledge_for_goal(goal, max_chars=600),
    # which queries with max_results=5, min_confidence=0.3 then applies the
    # char budget. Mirror that exactly, per arm.
    def rendered_ids(goal: str, expand: bool) -> set:
        nodes = query_knowledge(goal, max_results=5, min_confidence=0.3,
                                expand_edges=expand)
        _, applied_ids, _ = _render_knowledge_entries(nodes, 600)
        return set(applied_ids)

    goals: list[str] = []
    for line in outcomes.read_text().splitlines():
        if not line.strip():
            continue
        try:
            g = json.loads(line).get("goal", "")
        except json.JSONDecodeError:
            continue
        if g:
            goals.append(g)
    goals = goals[-n:]

    changed = []
    for goal in goals:
        off = rendered_ids(goal, False)
        on = rendered_ids(goal, True)
        if on != off:
            changed.append((goal, sorted(on - off), sorted(off - on)))

    print(f"replayed {len(goals)} recent goals (rendered-set comparison at "
          f"the production layer: min_confidence=0.3, max_chars=600; "
          f"read-only)")
    print(f"expansion changed rendered membership on "
          f"{len(changed)}/{len(goals)} "
          f"recalls ({100.0 * len(changed) / max(1, len(goals)):.1f}%)")
    for goal, added, dropped in changed:
        print(f"  ~ {goal[:90]!r}")
        print(f"    +{added}  -{dropped}")


if __name__ == "__main__":
    main()
