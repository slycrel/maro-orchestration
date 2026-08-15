"""Lesson portability — earned-globality evidence for injection ranking.

§14a slice 2 (decision e2b83703: provenance stamped categorical at mint;
globality is a CONTINUOUS portability estimate earned from foreign-context
citation × verdict evidence, with thresholds applied only at
injection-ranking time — never category flips; contradiction bleeds
weight because failed foreign verdicts lower the Beta posterior).

Three pieces, all evidence-side (the mint-time scope stamp is a separate
slice riding the extraction schema):

1. Shared classification helpers — ``beta_mean``, ``is_home``,
   ``unattributable_source``, the mint-time ``SOURCE_GOAL_EXCERPT`` cap.
   Single source of truth for both the census instrument
   (``camera_readout --portability``) and rank-time consumption, so the
   measurement and the behavior can't drift apart.
2. The evidence cache — ``memory/portability_cache.json``, a full
   recount (never incremental, so it self-heals) of per-lesson verdicted
   foreign citations, refreshed at loop finalize via ``refresh_cache``.
   Verdicts that arrive after a run ends (audit repairs, operator
   re-stamps) are picked up by the next refresh.
3. ``apply_portability`` — the ranking hook. Foreign-context candidates
   with at least ``MIN_FOREIGN_EVIDENCE`` verdicted foreign citations get
   their ranker score multiplied by ``2 * beta_mean(s, f)`` — neutral
   (1.0) exactly at the uninformed prior, up-weighted when foreign
   citations tend to succeed, down when they tend to fail. Home
   candidates, unattributable sources, and evidence-poor lessons are
   untouched, so a fresh install (no camera frames → no cache) renders
   byte-identically. Known feedback loop, accepted for v1: boosted
   lessons get cited more and so accrue evidence faster — verdicts still
   discipline the estimate (failures bleed weight), which is the §14a
   contradiction verb.

Killswitch: ``recall.portability_weighting`` (docs/DEFAULTS.md) gates
both the finalize-time cache refresh and the rank-time weighting.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Mint-time cap: knowledge_web.record_tiered_lesson stores source_goal as
# a 120-char excerpt. Any equality compare against a full goal string must
# mirror the cap or it is dead for realistic goals (live corpus at the
# slice-1 review: 121/123 verdicted citations carried a truncated
# source_goal).
SOURCE_GOAL_EXCERPT = 120

# Threshold applied at ranking time only (decision e2b83703): below this
# many verdicted foreign citations the posterior is mostly prior, and
# weighting by it would just add noise.
MIN_FOREIGN_EVIDENCE = 3


def beta_mean(successes: int, failures: int) -> float:
    """Beta(1,1)-posterior mean: (s+1)/(s+f+2). 0.5 at no evidence."""
    return (successes + 1) / (successes + failures + 2)


def unattributable_source(src_goal: str) -> bool:
    """True when source_goal is not real goal text: empty, or one of the
    sentinel shapes three record_tiered_lesson callers pass (cli
    "manual", evolver_store "evolver-{id}", prereq "prereq:{topic}").
    Such lessons can't be home/foreign-classified honestly."""
    return (not src_goal or src_goal == "manual"
            or src_goal.startswith("evolver-")
            or src_goal.startswith("prereq:"))


def is_home(src_goal: str, goal: str, project: str) -> bool:
    """Is the current (goal, project) context the lesson's home context?

    Two legs, either suffices: exact goal-text equality under the
    mint-time excerpt cap, or the lesson's source_goal sluggifying to the
    current project (same derivation the run side uses — time-dependent,
    a historical rename can misclassify). Caller is expected to screen
    ``unattributable_source`` first; a sentinel source_goal is neither
    home nor foreign.
    """
    if src_goal and goal and src_goal == goal[:SOURCE_GOAL_EXCERPT]:
        return True
    if src_goal and project:
        try:
            from loop_artifacts import resolve_project_slug
            return resolve_project_slug(src_goal) == project
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Evidence cache
# ---------------------------------------------------------------------------

def weighting_enabled() -> bool:
    try:
        from config import get
        return bool(get("recall.portability_weighting", True))
    except Exception:
        return True


def cache_path() -> Path:
    from config import memory_dir
    return memory_dir() / "portability_cache.json"


def load_cache() -> Dict[str, Dict[str, int]]:
    """lesson_id -> {"foreign_s", "foreign_f"}. Empty dict on any miss —
    an absent/corrupt cache means "no evidence", never an error."""
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
        lessons = raw.get("lessons")
        return lessons if isinstance(lessons, dict) else {}
    except Exception:
        return {}


def refresh_cache() -> int:
    """Recount the census and rewrite the cache. Returns the number of
    lessons with any verdicted foreign evidence; -1 on failure (logged,
    never raises — this rides loop finalize)."""
    try:
        from runs import runs_root
        from camera_readout import _load_frames, portability_census

        root = runs_root()
        per_run: List[Dict[str, Any]] = []
        if root.exists():
            for rd in root.iterdir():
                if not rd.is_dir():
                    continue
                frames, _torn = _load_frames(rd)
                if frames:
                    per_run.append({"dir": rd, "frames": frames,
                                    "card": None})
        census = portability_census(per_run)
        lessons = {
            r["lesson_id"]: {"foreign_s": r["foreign_s"],
                             "foreign_f": r["foreign_f"]}
            for r in census["rows"]
            if r["foreign_s"] + r["foreign_f"] > 0
        }
        from datetime import datetime, timezone
        payload = {"computed_at": datetime.now(timezone.utc).isoformat(),
                   "runs_scanned": len(per_run),
                   "lessons": lessons}
        from file_lock import atomic_write
        atomic_write(cache_path(),
                     json.dumps(payload, indent=1) + "\n")
        return len(lessons)
    except Exception as exc:
        log.debug("portability cache refresh failed: %s", exc)
        return -1


# ---------------------------------------------------------------------------
# Rank-time consumption
# ---------------------------------------------------------------------------

def apply_portability(
    scored: List[Tuple[Any, Any]],
    goal: str,
    project: str,
    cache: Optional[Dict[str, Dict[str, int]]] = None,
) -> Tuple[List[Tuple[Any, Any]], List[Dict[str, Any]]]:
    """Re-rank (lesson, score) pairs by earned foreign-context evidence.

    Returns (pairs, adjustments). When nothing qualifies — flag off, no
    cache, all-home, evidence below MIN_FOREIGN_EVIDENCE — the input list
    is returned unchanged (same object, so callers can cheaply detect
    no-op) and adjustments is empty. Never raises.
    """
    try:
        if not scored or not weighting_enabled():
            return scored, []
        if cache is None:
            cache = load_cache()
        if not cache:
            return scored, []
        adjustments: List[Dict[str, Any]] = []
        out: List[Tuple[Any, Any]] = []
        for tl, score in scored:
            lid = getattr(tl, "lesson_id", "") or ""
            ev = cache.get(lid)
            weight = 1.0
            if ev and isinstance(score, (int, float)):
                s = int(ev.get("foreign_s", 0) or 0)
                f = int(ev.get("foreign_f", 0) or 0)
                src_goal = getattr(tl, "source_goal", "") or ""
                if (s + f >= MIN_FOREIGN_EVIDENCE
                        and not unattributable_source(src_goal)
                        and not is_home(src_goal, goal, project)):
                    weight = 2.0 * beta_mean(s, f)
            if weight != 1.0:
                adjustments.append({
                    "lesson_id": lid, "raw": score,
                    "weight": round(weight, 4),
                    "s": s, "f": f,
                })
                out.append((tl, score * weight))
            else:
                out.append((tl, score))
        if not adjustments:
            return scored, []
        # Stable sort: equal adjusted scores keep ranker order.
        out.sort(key=lambda pair: -pair[1])
        return out, adjustments
    except Exception as exc:
        log.debug("apply_portability failed open (unweighted): %s", exc)
        return scored, []


def main(argv: Optional[List[str]] = None) -> int:
    """``python3 -m portability refresh`` — manual cache refresh."""
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["refresh"],
                    help="refresh: recount the census into the cache")
    ap.parse_args(argv)
    n = refresh_cache()
    if n < 0:
        print("refresh FAILED (see debug log)")
        return 1
    print(f"portability cache refreshed: {n} lessons with verdicted "
          f"foreign evidence -> {cache_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
