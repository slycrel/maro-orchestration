"""Shadow replay harness for the navigator (goal-brain step 5).

Rebuilds a NavigatorInput from a historical run dir — including the recall
context AS OF that run's start time — asks the navigator what it would have
done, and records the decision beside what the pipeline actually did
(NAVIGATOR_DECIDED with shadow=true + pipeline_actual). Changes nothing:
this is decide-only. Divergence between navigator-said and pipeline-did is
the evaluation data that earns per-class cutover (docs/NAVIGATOR_SCHEMA.md).

Two replayable decision points per run:
- "dispatch" — turn 0: the goal arrives with its history. The pipeline's
  actual behavior here was always the moral equivalent of `execute`
  (classify lane, decompose, run).
- "closure" — turn 1: the run's outcome replayed as a WorkReport. The
  pipeline's actual behavior was to end the run with metadata.status
  (and, historically, the heartbeat often re-enqueued failures verbatim).

CLI (dev tool, like poe-introspect):
    PYTHONPATH=src python3 -m navigator_shadow <handle-id>... \
        [--point dispatch|closure|both] [--tiers cheap,mid,power]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from navigator import NavigatorInput, WorkReport
from recall import (
    PriorAttempt,
    RecallResult,
    ThreadIdentity,
    _normalize,
    _read_run_metadata,
)

# Historical replay scans the whole runs tree (no mtime cap — old dirs'
# mtimes are meaningless for an as-of query, and this is a dev tool).
_ASOF_WINDOW_HOURS = 24.0

_STATUS_TO_WORK = {"done": "ok", "stuck": "partial", "error": "failed"}


def resolve_run_dir(handle_or_path: str) -> Path:
    """Accept a handle id (or prefix) or a literal run-dir path."""
    p = Path(handle_or_path)
    if p.is_dir() and (p / "metadata.json").exists():
        return p
    from runs import runs_root
    matches = sorted(runs_root().glob(f"{handle_or_path}*"))
    dirs = [m for m in matches if m.is_dir()]
    if not dirs:
        raise FileNotFoundError(f"no run dir matches {handle_or_path!r}")
    if len(dirs) > 1:
        raise ValueError(
            f"{handle_or_path!r} is ambiguous: {', '.join(d.name for d in dirs)}")
    return dirs[0]


def _parse_when(value: str) -> Optional[datetime]:
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def _prior_attempts_asof(
    goal: str, as_of: datetime, *, window_hours: float = _ASOF_WINDOW_HOURS,
) -> List[PriorAttempt]:
    """recall()'s prior-attempt match, evaluated at a moment in the past:
    runs whose goal matches AND that started inside (as_of - window, as_of)."""
    from runs import runs_root
    from memory_ledger import _text_similarity

    root = runs_root()
    if not root.is_dir():
        return []
    cutoff = as_of - timedelta(hours=window_hours)
    goal_norm = _normalize(goal)
    attempts: List[PriorAttempt] = []
    for rd in root.iterdir():
        if not rd.is_dir():
            continue
        meta = _read_run_metadata(rd)
        if not meta:
            continue
        when = _parse_when(str(meta.get("started_at") or ""))
        if when is None or not (cutoff <= when < as_of):
            continue
        prompt = str(meta.get("prompt") or "")
        if not prompt:
            continue
        if _normalize(prompt) == goal_norm:
            match = "exact"
        elif _text_similarity(prompt, goal) >= 0.9:
            match = "near"
        else:
            continue
        attempts.append(PriorAttempt(
            goal=prompt,
            handle_id=str(meta.get("handle_id") or rd.name.split("-", 1)[0]),
            status=str(meta.get("status") or "unknown"),
            when=str(meta.get("started_at") or ""),
            match=match,
        ))
    attempts.sort(key=lambda a: a.when, reverse=True)
    return attempts


def _goal_brain_standin(run_path: Path) -> str:
    """Per-thread goal-brains don't exist yet; scope / resolved intent are
    the pinned stand-in (NAVIGATOR_SCHEMA.md open ends)."""
    parts: List[str] = []
    for name in ("resolved_intent.md", "scope.md"):
        f = run_path / "source" / name
        try:
            text = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            parts.append(text[:1500])
    return "\n\n".join(parts)


def input_from_run(
    run_path: Path, *, point: str = "dispatch",
) -> Tuple[NavigatorInput, Dict[str, Any]]:
    """Build (NavigatorInput, pipeline_actual) from a historical run dir."""
    meta = _read_run_metadata(run_path)
    if not meta:
        raise ValueError(f"unreadable metadata in {run_path}")
    goal = str(meta.get("prompt") or "")
    started = _parse_when(str(meta.get("started_at") or "")) or datetime.now(timezone.utc)

    prior = _prior_attempts_asof(goal, started)
    origin = meta.get("origin") or {}
    thread: Dict[str, Any] = {}
    if origin.get("parent_goal") or origin.get("parent_handle_id"):
        thread = {
            "parent_goal": str(origin.get("parent_goal") or ""),
            "parent_handle_id": str(origin.get("parent_handle_id") or ""),
            "chain": [str(origin.get("parent_handle_id") or "")],
            "source": str(origin.get("source") or "unknown"),
        }
    recall_block = RecallResult(
        thread=ThreadIdentity(**thread) if thread else None,
        prior_attempts=prior,
    ).as_context_block()

    status = str(meta.get("status") or "unknown")
    last_work: Optional[WorkReport] = None
    turn_index = 0
    if point == "closure":
        ended = _parse_when(str(meta.get("ended_at") or ""))
        duration = int((ended - started).total_seconds()) if ended else -1
        last_work = WorkReport(
            move="execute",
            status=_STATUS_TO_WORK.get(status, "failed"),
            summary=f"The execution loop finished with status {status!r}"
                    + (f" after {duration}s" if duration >= 0 else ""),
            recommendation="",
            signals={"pipeline_status": status, "duration_s": duration},
            output_ref=str(run_path / "build"),
        )
        turn_index = 1

    nav_input = NavigatorInput(
        goal=goal,
        goal_brain=_goal_brain_standin(run_path),
        thread=thread,
        turn_index=turn_index,
        last_work=last_work,
        open_children=[],   # historical runs never recorded children
        recall_block=recall_block,
        budget={"note": "historical replay; live budget unavailable"},
    )
    pipeline_actual = {
        "point": point,
        "lane": str(meta.get("lane") or ""),
        "model": str(meta.get("model") or ""),
        "status": status,
        "handle_id": str(meta.get("handle_id") or ""),
        "prior_attempts_asof": len(prior),
        # Turn 0, the old pipeline always ran the goal — execute-equivalent.
        "move_equivalent": "execute" if point == "dispatch" else f"ended:{status}",
    }
    return nav_input, pipeline_actual


def replay_run(
    handle_or_path: str,
    *,
    points: Tuple[str, ...] = ("dispatch",),
    tiers: Optional[List[str]] = None,
    adapter_factory=None,
) -> List[Dict[str, Any]]:
    """Replay one run at the given decision points. Returns result dicts;
    every navigator call is instrumented (shadow=true) by decide()."""
    from navigator_prompt import decide

    run_path = resolve_run_dir(handle_or_path)
    results: List[Dict[str, Any]] = []
    for point in points:
        nav_input, pipeline_actual = input_from_run(run_path, point=point)
        decision, meta = decide(
            nav_input,
            tiers=tiers,
            adapter_factory=adapter_factory,
            shadow=True,
            pipeline_actual=pipeline_actual,
        )
        results.append({
            "run": run_path.name,
            "point": point,
            "goal": nav_input.goal[:100],
            "prior_attempts": pipeline_actual["prior_attempts_asof"],
            "pipeline": pipeline_actual["move_equivalent"],
            "navigator": decision.move,
            "confidence": decision.confidence,
            "tier": meta.get("tier", "?"),
            "escalated_via": meta.get("escalated_via", ""),
            "reasoning": decision.reasoning,
            "payload": decision.payload,
        })
    return results


def shadow_dispatch_live(
    goal: str,
    *,
    origin: Optional[Dict[str, Any]] = None,
    recall_result: Optional[RecallResult] = None,
    pipeline_move: str = "execute",
    extra: Optional[Dict[str, Any]] = None,
    tiers: Optional[List[str]] = None,
    adapter_factory=None,
) -> Optional[Any]:
    """Live shadow at the autonomous dispatch boundary: decide-only.

    Called from handle_task() right after the dispatch guard verdict is known
    (pipeline_move is "execute" or "guard_refused"). Reuses the guard's
    RecallResult so dispatch pays no extra file scanning — only the one
    cheap-tier model call. Config-gated by navigator.shadow_dispatch
    (default OFF in code: a model call per dispatch is real spend and real
    latency, so a deployment opts in via workspace config — this box has).
    Never raises; never alters dispatch. Returns the decision or None.
    """
    try:
        from config import get as cfg_get
        if not bool(cfg_get("navigator.shadow_dispatch", False)):
            return None
        if tiers is None:
            # Default cheap-only: live shadow wants volume of dispatch-class
            # decisions, not chain depth; an idunno is recorded as the
            # synthesized escalate with escalated_via="idunno_chain" and is
            # distinguishable in analysis.
            tiers = list(cfg_get("navigator.shadow_tiers", ["cheap"]))
    except Exception:
        return None

    try:
        thread: Dict[str, Any] = {}
        rr = recall_result
        if rr is not None and rr.thread is not None:
            thread = {
                "parent_goal": rr.thread.parent_goal,
                "parent_handle_id": rr.thread.parent_handle_id,
                "chain": list(rr.thread.chain),
                "source": rr.thread.source,
            }
        elif origin:
            thread = {
                "parent_goal": str(origin.get("parent_goal") or ""),
                "parent_handle_id": str(origin.get("parent_handle_id") or ""),
                "chain": [],
                "source": str(origin.get("source") or "unknown"),
            }
        nav_input = NavigatorInput(
            goal=goal,
            thread=thread,
            recall_block=rr.as_context_block() if rr is not None else "",
            budget={"note": "live dispatch shadow; loop budget not yet allocated"},
        )
        pipeline_actual = {
            "point": "dispatch",
            "move_equivalent": pipeline_move,
            "live": True,
            **(extra or {}),
        }
        from navigator_prompt import decide
        decision, _meta = decide(
            nav_input,
            tiers=tiers,
            adapter_factory=adapter_factory,
            shadow=True,
            pipeline_actual=pipeline_actual,
        )
        return decision
    except Exception as exc:
        import logging
        logging.getLogger("navigator").debug("live dispatch shadow skipped: %s", exc)
        return None


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-replay historical runs through the navigator "
                    "(decide-only; changes nothing).")
    parser.add_argument("runs", nargs="+", help="handle ids / prefixes / run-dir paths")
    parser.add_argument("--point", choices=("dispatch", "closure", "both"),
                        default="dispatch")
    parser.add_argument("--tiers", default="",
                        help="comma-separated tier list, e.g. cheap,mid,power")
    parser.add_argument("--json", action="store_true", help="emit JSON lines")
    args = parser.parse_args(argv)

    points = ("dispatch", "closure") if args.point == "both" else (args.point,)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()] or None

    rc = 0
    for ref in args.runs:
        try:
            results = replay_run(ref, points=points, tiers=tiers)
        except Exception as exc:
            print(f"!! {ref}: {exc}", file=sys.stderr)
            rc = 1
            continue
        for r in results:
            if args.json:
                print(json.dumps(r))
            else:
                print(f"\n== {r['run']} [{r['point']}]")
                print(f"   goal:      {r['goal']}")
                print(f"   pipeline:  {r['pipeline']}")
                print(f"   navigator: {r['navigator']} "
                      f"(conf {r['confidence']:.2f}, tier {r['tier']}"
                      + (f", via {r['escalated_via']}" if r['escalated_via'] else "")
                      + ")")
                print(f"   reasoning: {r['reasoning']}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
