#!/usr/bin/env python3
"""Quarantine (or unquarantine) a stored lesson by id — the McAfee move.

Stamps ``minted_from`` on a lesson row in the flat ledger and/or the tiered
stores. Quarantined ("prompt") rows stay on disk and visible in readouts;
they only leave the injection surfaces (provenance gate, 2026-07-29 —
see src/lesson_provenance.py for the incident this answers).

Usage:
    python3 scripts/quarantine_lesson.py <lesson_id> [--clear] [--reason "..."]

Searches all stores (flat, medium, long) for the id and stamps every match.
--clear sets minted_from back to "outcome" instead of "prompt".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("lesson_id", help="8-char lesson id to stamp")
    ap.add_argument("--clear", action="store_true",
                    help='set minted_from="outcome" (unquarantine) instead of "prompt"')
    ap.add_argument("--reason", default="", help="recorded in the captain's log event")
    args = ap.parse_args()

    minted_from = "outcome" if args.clear else "prompt"

    from knowledge_web import MemoryTier, set_lesson_minted_from
    from memory_ledger import set_flat_lesson_minted_from

    hits = []
    if set_flat_lesson_minted_from(args.lesson_id, minted_from, reason=args.reason):
        hits.append("flat")
    for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):
        if set_lesson_minted_from(args.lesson_id, minted_from, tier=tier,
                                  reason=args.reason):
            hits.append(tier)

    if not hits:
        print(f"lesson {args.lesson_id}: not found in any store")
        return 1
    print(f"lesson {args.lesson_id}: minted_from={minted_from!r} stamped in "
          f"{', '.join(hits)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
