"""Decision-prior card schema — the compact "what did a run try, how did it
end, what did it learn" record a finished run's `run_card.json` carries under
`decision_prior`, plus its load/format (read-side) functions.

Neutral module, extracted 2026-07-13 (adversarial-review R1 batch-1 finding
#2): `run_curation.py`'s `index_decision_prior` curator builds one at goal-end
(via `make_decision_prior` below) as part of the run_card.json write; the read
half — `format_prior_decisions` / `load_decision_prior` — used to live in
run_curation.py too, and recall.py imported it from there directly. That made
recall.py (a read seam — see its own docstring: "callers never talk to a
substrate directly") reach sideways into run_curation.py (a write/curation
substrate) for functionality that's really about a shared data shape, not
about curation. Both modules now depend on this neutral one instead of one
reaching into the other.

Schema (the dict under `card["decision_prior"]`):
    handle_id, goal, outcome (a success_class string), goal_achieved
    (True/False/None — done != achieved), when (started_at),
    what_was_tried, why, lessons (list[str], capped at
    `_DECISION_LESSON_CAP`), resume_from (optional — partial runs only).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

_DECISION_TRIED_CHARS = 400
_DECISION_LESSON_CAP = 5


def make_decision_prior(
    *,
    handle_id: str,
    goal: str,
    outcome: Optional[str],
    goal_achieved: Optional[bool],
    when: Optional[str],
    what_was_tried: str,
    why: str,
    lessons: List[str],
    resume_from: Optional[str] = None,
) -> dict:
    """Build the canonical `decision_prior` dict.

    The one place the schema's shape and size caps are defined —
    `run_curation.index_decision_prior` calls this instead of constructing
    the dict literal inline, so the write side can't silently drift from the
    shape `load_decision_prior`/`format_prior_decisions` expect.
    """
    prior = {
        "handle_id": handle_id,
        "goal": goal,
        "outcome": outcome,
        "goal_achieved": goal_achieved,
        "when": when,
        "what_was_tried": str(what_was_tried or "")[:_DECISION_TRIED_CHARS],
        "why": str(why or "")[:_DECISION_TRIED_CHARS],
        "lessons": list(lessons or [])[:_DECISION_LESSON_CAP],
    }
    if resume_from:
        prior["resume_from"] = resume_from
    return prior


def _run_dir_for(handle_id: str) -> Optional[Path]:
    # Same lookup run_curation._run_dir_for uses — kept local so this module
    # depends only on runs.py (no run_curation import — stays a leaf module).
    from runs import run_dir
    rd = run_dir(handle_id)
    return rd if rd.is_dir() else None


def load_decision_prior(handle_id: str) -> Optional[dict]:
    """Read a finished run's decision-prior brief from its run_card.json.

    Falls back to a thin brief synthesized from the card's classification +
    result excerpt for runs curated before the indexer existed (no backfill
    needed). None when the run has no card at all (uncurated / pruned) — which
    is exactly why the CURRENT run self-excludes: its card is written at
    goal-END, so at read time (goal-start) it has none.
    """
    rd = _run_dir_for(handle_id)
    if rd is None:
        return None
    cp = rd / "run_card.json"
    if not cp.is_file():
        return None
    try:
        card = json.loads(cp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    dp = card.get("decision_prior")
    if isinstance(dp, dict):
        return dp
    return {
        "handle_id": card.get("handle_id", handle_id),
        "goal": card.get("goal", ""),
        "outcome": card.get("success_class"),
        "goal_achieved": card.get("goal_achieved"),
        "when": card.get("started_at"),
        "what_was_tried": (card.get("result_excerpt") or "")[:_DECISION_TRIED_CHARS],
        "why": card.get("goal_verdict_summary") or "",
        "lessons": [],
    }


def format_prior_decisions(attempts: Any, *, goal: str = "",
                           exclude_handle_id: str = "", k: int = 3,
                           max_chars: int = 1000) -> str:
    """Render up to k prior attempts' decision-priors as one injectable block.

    `attempts` are recall.PriorAttempt-shaped (only `.handle_id` is required;
    dicts also accepted). Only attempts with a loadable card contribute — the
    current run has no card yet at read time, so it self-excludes;
    exclude_handle_id is belt-and-suspenders. Empty string when no prior has a
    usable brief. This is the READ half of run_curation's miner #3; recall()
    calls it so a re-attempt of the same/rephrased goal arrives warm."""
    briefs: List[str] = []
    seen: set = set()
    for a in (attempts or []):
        if isinstance(a, dict):
            hid = a.get("handle_id")
        else:
            hid = getattr(a, "handle_id", None)
        if not hid or hid == exclude_handle_id or hid in seen:
            continue
        seen.add(hid)
        dp = load_decision_prior(hid)
        if not dp:
            continue
        outcome = dp.get("outcome") or "?"
        when = (dp.get("when") or "")[:10]
        line = f"- [{outcome} {when} · {hid}] tried: {dp.get('what_was_tried') or '?'}"
        why = (dp.get("why") or "").strip()
        if why and outcome != "success":
            line += f". Why it ended: {why}"
        lessons = dp.get("lessons") or []
        if lessons:
            line += ". Lessons: " + "; ".join(lessons[:3])
        if dp.get("resume_from"):
            line += f". Resume: {dp['resume_from']}"
        briefs.append(line)
        if len(briefs) >= k:
            break
    if not briefs:
        return ""
    block = ("## Prior attempts at this goal — read before planning\n"
             + "\n".join(briefs)
             + "\nDo not repeat an approach that already failed the same way; "
               "build on what worked, resume a partial, or change the approach.")
    return block[:max_chars]
