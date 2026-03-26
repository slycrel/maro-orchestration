"""Crystallization dashboard — unified view of all knowledge graduation stages.

poe-knowledge status   → full dashboard (all stages)
poe-knowledge stage N  → show only stage N items
poe-knowledge promote  → list promotion actions available (doesn't execute)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Optional


# ---------------------------------------------------------------------------
# Stage descriptions (matches KNOWLEDGE_CRYSTALLIZATION.md exactly)
# ---------------------------------------------------------------------------

_STAGES = {
    1: ("Fluid", "LLM reasoning per decision — no memory yet"),
    2: ("Lesson", "Tiered memory: medium → long decay/promote"),
    3: ("Identity", "Canon → AGENTS.md system prompt"),
    4: ("Skill", "Python code, sandboxed, provisional → established"),
    5: ("Rule", "Hardcoded path, zero inference cost [not yet implemented]"),
}


# ---------------------------------------------------------------------------
# Data collectors for each stage
# ---------------------------------------------------------------------------

def _stage2_data() -> dict:
    """Tiered memory stats + graveyard (decay range 0.2–0.9)."""
    try:
        from memory import memory_status, load_tiered_lessons, MemoryTier, GC_THRESHOLD, PROMOTE_MIN_SCORE
        status = memory_status()
        medium_lessons = load_tiered_lessons(tier=MemoryTier.MEDIUM, min_score=0.0)
        long_lessons = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
        graveyard = [l for l in medium_lessons if GC_THRESHOLD <= l.score < 0.4]
        return {
            "medium_count": status["medium"].get("count", 0),
            "long_count": status["long"].get("count", 0),
            "promote_candidates": status["medium"].get("promote_candidates", 0),
            "gc_candidates": status["medium"].get("gc_candidates", 0),
            "graveyard_count": len(graveyard),
            "medium_avg_score": status["medium"].get("avg_score"),
        }
    except Exception as e:
        return {"error": str(e)}


def _stage3_data() -> dict:
    """Canon candidates (long-tier lessons ready for AGENTS.md promotion)."""
    try:
        from memory import get_canon_candidates
        candidates = get_canon_candidates()
        return {
            "canon_candidates": len(candidates),
            "top": [
                {"lesson": c.content[:80], "times_applied": c.times_applied}
                for c in sorted(candidates, key=lambda x: x.times_applied, reverse=True)[:3]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _stage4_data() -> dict:
    """Skills by tier: provisional / established."""
    try:
        from skills import load_skills, get_all_skill_stats
        skills = load_skills()
        provisional = [s for s in skills if s.tier == "provisional"]
        established = [s for s in skills if s.tier == "established"]
        stats = get_all_skill_stats()
        promote_ready = [
            s for s in provisional
            if any(
                st.skill_id == s.id and st.total_uses >= 3 and
                (st.success_count / st.total_uses) ** 3 >= 0.7
                for st in stats
            )
        ]
        return {
            "provisional_count": len(provisional),
            "established_count": len(established),
            "promote_ready": len(promote_ready),
            "promote_ready_names": [s.name for s in promote_ready],
        }
    except Exception as e:
        return {"error": str(e)}


def _suggestions_data() -> dict:
    """Open evolver suggestions (proposed improvements not yet applied)."""
    try:
        from evolver import load_suggestions
        suggestions = load_suggestions(limit=50)
        pending = [s for s in suggestions if not getattr(s, "applied", False)]
        categories: dict[str, int] = {}
        for s in pending:
            cat = getattr(s, "category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1
        return {
            "pending_count": len(pending),
            "by_category": categories,
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------

def _fmt_error(d: dict) -> Optional[str]:
    return f"  [error: {d['error']}]" if "error" in d else None


def print_dashboard(stage_filter: Optional[int] = None) -> None:
    s2 = _stage2_data()
    s3 = _stage3_data()
    s4 = _stage4_data()
    sug = _suggestions_data()

    print("╔══════════════════════════════════════════════════════╗")
    print("║          Knowledge Crystallization Dashboard          ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()

    # Stage 2: Lessons
    if stage_filter is None or stage_filter == 2:
        print("Stage 2 — Lessons (tiered memory)")
        if err := _fmt_error(s2):
            print(err)
        else:
            med = s2["medium_count"]
            lng = s2["long_count"]
            gy = s2["graveyard_count"]
            gc = s2["gc_candidates"]
            promo = s2["promote_candidates"]
            avg = s2.get("medium_avg_score")
            avg_str = f"  avg score {avg}" if avg is not None else ""
            print(f"  medium: {med} lessons{avg_str}")
            print(f"  long:   {lng} lessons")
            if gy:
                print(f"  graveyard: {gy} recoverable (score 0.2–0.4) — run 'poe-memory list' to inspect")
            if gc:
                print(f"  ⚠  {gc} lessons near GC threshold — consider 'poe-memory decay'")
            if promo:
                print(f"  ↑  {promo} ready to promote medium→long — run 'poe-memory promote'")
        print()

    # Stage 3: Identity / Canon
    if stage_filter is None or stage_filter == 3:
        print("Stage 3 — Identity (canon candidates for AGENTS.md)")
        if err := _fmt_error(s3):
            print(err)
        else:
            n = s3["canon_candidates"]
            if n == 0:
                print("  No canon candidates yet (need 10+ applies, 3+ task types)")
            else:
                print(f"  {n} candidate(s) ready for human review:")
                for item in s3.get("top", []):
                    print(f"    • [{item['times_applied']}×] {item['lesson']}...")
                print(f"  Run 'poe-memory canon-candidates' for full list")
        print()

    # Stage 4: Skills
    if stage_filter is None or stage_filter == 4:
        print("Stage 4 — Skills (Python code, sandboxed)")
        if err := _fmt_error(s4):
            print(err)
        else:
            print(f"  provisional: {s4['provisional_count']}  established: {s4['established_count']}")
            if s4["promote_ready"]:
                names = ", ".join(s4["promote_ready_names"])
                print(f"  ↑  {s4['promote_ready']} ready to promote: {names}")
                print(f"     Run 'poe-skill-stats' then 'poe-skills promote <name>'")
        print()

    # Stage 5: Rules (not yet implemented)
    if stage_filter is None or stage_filter == 5:
        print("Stage 5 — Rules (zero-cost hardcoded paths)")
        print("  [not yet implemented — see Phase 22 in ROADMAP.md]")
        print()

    # Evolver suggestions
    if stage_filter is None:
        print("Evolver suggestions")
        if err := _fmt_error(sug):
            print(err)
        else:
            n = sug["pending_count"]
            if n == 0:
                print("  No pending suggestions")
            else:
                by_cat = sug.get("by_category", {})
                cats = ", ".join(f"{v} {k}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1]))
                print(f"  {n} pending: {cats}")
                print(f"  Run 'poe-evolver --list' to review, '--apply <id>' to execute")
        print()

    print("──────────────────────────────────────────────────────")
    if stage_filter is None:
        print("Tip: run 'poe-memory canon-candidates' for Stage 3 detail")
        print("     run 'poe-persona list' to see 15 active personas")


def print_promote_actions() -> None:
    """List all available promotion actions across stages (read-only)."""
    s2 = _stage2_data()
    s3 = _stage3_data()
    s4 = _stage4_data()

    print("Available promotion actions:")
    print()

    if not _fmt_error(s2) and s2["promote_candidates"]:
        print(f"  Stage 2→3 (medium→long): {s2['promote_candidates']} lesson(s)")
        print(f"    poe-memory promote <id>")
        print()

    if not _fmt_error(s3) and s3["canon_candidates"]:
        print(f"  Stage 3→identity (long→AGENTS.md): {s3['canon_candidates']} candidate(s)")
        print(f"    poe-memory canon-candidates  # review first")
        print(f"    poe-memory canonize <id>     # HUMAN GATE — edits AGENTS.md")
        print()

    if not _fmt_error(s4) and s4["promote_ready"]:
        names = ", ".join(s4["promote_ready_names"])
        print(f"  Stage 4 tier (provisional→established): {names}")
        print(f"    poe-skills promote <name>")
        print()

    if not (
        (not _fmt_error(s2) and s2["promote_candidates"]) or
        (not _fmt_error(s3) and s3["canon_candidates"]) or
        (not _fmt_error(s4) and s4["promote_ready"])
    ):
        print("  Nothing ready to promote yet.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="poe-knowledge",
        description="Crystallization dashboard — view all knowledge graduation stages",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_status = sub.add_parser("status", help="Full crystallization dashboard")
    p_status.add_argument("--stage", type=int, choices=[2, 3, 4, 5], help="Show only this stage")

    sub.add_parser("promote", help="List available promotion actions (read-only)")

    args = parser.parse_args(argv)

    if args.cmd == "status":
        print_dashboard(stage_filter=getattr(args, "stage", None))
    elif args.cmd == "promote":
        print_promote_actions()
    else:
        print_dashboard()


if __name__ == "__main__":
    main()
