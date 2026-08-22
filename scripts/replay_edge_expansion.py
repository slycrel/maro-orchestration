#!/usr/bin/env python3
"""Offline A/B replay for knowledge.edge_expansion — the receipts behind the
default-flip decision (edge-review r1, Minimalist/Skeptic: the 4/120 figure
shipped unreceipted; Architect: the original run compared ordered id LISTS,
so pure reorderings counted as "changed").

Measures the LITERAL production layer (edge-review r2, consensus HIGH: the
r1 rewrite still compared raw query_knowledge sets at min_confidence=0.0
with no char budget — a laxer surface than the one that fires the
KNOWLEDGE_EDGE_EXPANSION event). Each arm now replays
recall.py's exact call shape — query_knowledge at inject's max_nodes /
min_confidence=0.3, followed by the shared _render_knowledge_entries char
budget at inject_knowledge_for_goal's own default (read from its
signature; recall.py stopped overriding it 2026-08-21) — and compares
the RENDERED id sets.

Read-only over the live store: replays the N most recent real goals from
memory/outcomes.jsonl; no times_applied bumps, no events.

`--query-level` skips the char budget (same confidence filter) — the
diagnostic layer that separates "expansion never changes the query slice"
from "the render budget truncates the entrants" (edge-review r3, Architect:
the 29/500 diagnosis must be reproducible, not prose).

Usage:
    PYTHONPATH=src python3 scripts/replay_edge_expansion.py [N] [--query-level]

Commit the output (docs/history/...) when a readout feeds a decision.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--query-level"]
    query_level = "--query-level" in sys.argv[1:]
    n = int(args[0]) if args else 120
    workspace = Path(os.environ.get(
        "MARO_WORKSPACE", Path.home() / ".maro" / "workspace"))
    outcomes = workspace / "memory" / "outcomes.jsonl"
    if not outcomes.exists():
        sys.exit(f"no outcomes store at {outcomes}")

    import inspect

    from knowledge_web import (query_knowledge, _render_knowledge_entries,
                               inject_knowledge_for_goal)

    # recall.py calls inject_knowledge_for_goal(goal) with NO max_chars
    # override (Jeremy 2026-08-21 — the old 600 was an unrationalized cap),
    # so the production budget IS the function default. Read it from the
    # signature so this receipt can never drift from the real call shape.
    prod_budget = inspect.signature(
        inject_knowledge_for_goal).parameters["max_chars"].default
    prod_nodes = inspect.signature(
        inject_knowledge_for_goal).parameters["max_nodes"].default

    def rendered_ids(goal: str, expand: bool) -> set:
        nodes = query_knowledge(goal, max_results=prod_nodes,
                                min_confidence=0.3, expand_edges=expand)
        if query_level:
            return {nd.node_id for nd in nodes}
        _, applied_ids, _ = _render_knowledge_entries(nodes, prod_budget)
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

    layer = ("query-level (min_confidence=0.3, NO char budget)"
             if query_level else
             f"production layer (min_confidence=0.3, "
             f"max_chars={prod_budget})")
    print(f"replayed {len(goals)} recent goals — {layer}; read-only")
    print(f"expansion changed membership on "
          f"{len(changed)}/{len(goals)} "
          f"recalls ({100.0 * len(changed) / max(1, len(goals)):.1f}%)")
    for goal, added, dropped in changed:
        print(f"  ~ {goal[:90]!r}")
        print(f"    +{added}  -{dropped}")


if __name__ == "__main__":
    main()
