#!/usr/bin/env python3
"""Explicit-prerequisite census over the live workspace (read-only).

Answers the LoopsBench-analysis open question "what is the real tag-usage
rate of [after:N,M] in production plans?" — the number that decides whether
the implicit sequential edge is a corner case or the norm (2026-09-16: 439
of 1537 NEXT.md plan items on this box, 28.6%). Reads projects/*/NEXT.md
(every plan step the loop ever appended) and nothing else; never writes.

    python3 scripts/prereq-census.py            # default workspace
    python3 scripts/prereq-census.py --json     # machine-readable
    MARO_WORKSPACE=/path python3 scripts/prereq-census.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from step_gate import AFTER_RE as _TAG  # noqa: E402  single grammar source
_ITEM = re.compile(r"^\s*[-*]\s*\[[ xX]\]\s*(.+)$")


def census(workspace: Path) -> dict:
    projects = sorted((workspace / "projects").glob("*/NEXT.md"))
    items = tagged = 0
    projects_with_items = projects_with_tag = 0
    per_project = []
    for p in projects:
        n = t = 0
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            m = _ITEM.match(line)
            if not m:
                continue
            n += 1
            if _TAG.search(m.group(1)):
                t += 1
        if n:
            projects_with_items += 1
            if t:
                projects_with_tag += 1
            per_project.append({"project": p.parent.name, "items": n, "tagged": t})
        items += n
        tagged += t
    return {
        "workspace": str(workspace),
        "projects_scanned": len(projects),
        "projects_with_items": projects_with_items,
        "projects_with_any_tag": projects_with_tag,
        "items": items,
        "tagged": tagged,
        "explicit_rate": round(tagged / items, 4) if items else 0.0,
        "per_project": per_project,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--workspace", default=os.environ.get("MARO_WORKSPACE")
                    or str(Path.home() / ".maro" / "workspace"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    c = census(Path(args.workspace))
    if args.json:
        print(json.dumps(c, indent=2))
        return 0
    print(f"workspace: {c['workspace']}")
    print(f"projects with plan items: {c['projects_with_items']} "
          f"(any [after:] tag: {c['projects_with_any_tag']})")
    print(f"plan items: {c['items']}  tagged: {c['tagged']}  "
          f"explicit-edge rate: {c['explicit_rate']:.1%}")
    print(f"implicit (sequential-default) edges: {c['items'] - c['tagged']} "
          f"({1 - c['explicit_rate']:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
