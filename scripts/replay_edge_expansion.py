#!/usr/bin/env python3
"""Offline A/B replay for knowledge.edge_expansion — the receipts behind the
default-flip decision (edge-review r1, Minimalist/Skeptic: the 4/120 figure
shipped unreceipted; Architect: the original run compared ordered id LISTS,
so pure reorderings counted as "changed").

Read-only over the live store: replays the N most recent real goals from
memory/outcomes.jsonl through query_knowledge with expansion OFF and ON and
compares the rendered id SETS (membership — matching the post-r1 semantics
where expansion only acts when membership changes).

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

    from knowledge_web import query_knowledge

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
        off = {nd.node_id for nd in query_knowledge(goal, expand_edges=False)}
        on = {nd.node_id for nd in query_knowledge(goal, expand_edges=True)}
        if on != off:
            changed.append((goal, sorted(on - off), sorted(off - on)))

    print(f"replayed {len(goals)} recent goals (set comparison, read-only)")
    print(f"expansion changed membership on {len(changed)}/{len(goals)} "
          f"recalls ({100.0 * len(changed) / max(1, len(goals)):.1f}%)")
    for goal, added, dropped in changed:
        print(f"  ~ {goal[:90]!r}")
        print(f"    +{added}  -{dropped}")


if __name__ == "__main__":
    main()
