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

Live decide-only taps (called from the running pipeline, config-gated off):
- shadow_dispatch_live() — at the autonomous dispatch boundary.
- shadow_blocked_step_live() — at the heuristic recovery decision
  (agent_loop._handle_blocked_step), the dumb-loop audit priority-1 point.
Both emit NAVIGATOR_DECIDED rows with pipeline_actual.point set, so
analyze_live_agreement() can break agreement down per decision point.

Planning-depth shadow (thread-arch #5, MILESTONES 1.5, decided 2026-07-09
GOAL_BRAIN Decisions): shadow_dispatch_live() ALSO judges planning depth —
how much planning this goal needs (plan / one-shot / thin-plan /
spawn-sub-goal) — in the SAME decide() call, gated independently by
navigator.shadow_planning_depth (default off, docs/DEFAULTS.md). The
pipeline's dispatch path is always the moral equivalent of "plan" (the
normal full pipeline) regardless of goal shape, so pipeline_actual carries
depth_equivalent="plan" only when this shadow is on; analyze_planning_
depth_agreement() tabulates navigator-vs-pipeline agreement the same way
analyze_live_agreement() does for the move field.

CLI (dev tool, like maro-introspect):
    PYTHONPATH=src python3 -m navigator_shadow <handle-id>... \
        [--point dispatch|closure|both] [--tiers cheap,mid,power]
    PYTHONPATH=src python3 -m navigator_shadow --agreement   # move + depth tables
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("navigator")

from ancestry import Origin
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
    """Prefer the real per-thread goal-brain (source/goal_brain.md, created
    at run-dir creation since 2026-06-11); fall back to the resolved-intent /
    scope stand-in for runs that predate it (NAVIGATOR_SCHEMA.md open ends)."""
    try:
        import thread_brain
        text = thread_brain.load_thread_brain(run_path)
        if text:
            return text
    except Exception:
        pass
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
    origin: Optional[Origin] = None,
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

    Also the ONE callsite for the planning-depth shadow (thread-arch #5,
    MILESTONES 1.5): when navigator.shadow_planning_depth is on (default
    off), this same call also asks the navigator to judge planning depth —
    no second LLM call, one more field in the same envelope — and records
    pipeline_actual.depth_equivalent="plan" (the pipeline's dispatch path is
    always the moral equivalent of the normal full pipeline, regardless of
    goal shape) so analyze_planning_depth_agreement() has a baseline to
    compare against.
    """
    try:
        from config import get as cfg_get
        # act_dispatch implies the decide call: a deployment that turned the
        # dispatch class live needs the decision even with shadowing off.
        # act_dispatch defaults ON (2026-07-08) — so a clean install pays one
        # cheap-tier call per autonomous dispatch unless it opts out.
        if not (bool(cfg_get("navigator.shadow_dispatch", False))
                or bool(cfg_get("navigator.act_dispatch", True))):
            return None
        if tiers is None:
            # Default cheap-only: live shadow wants volume of dispatch-class
            # decisions, not chain depth; an idunno is recorded as the
            # synthesized escalate with escalated_via="idunno_chain" and is
            # distinguishable in analysis.
            tiers = list(cfg_get("navigator.shadow_tiers", ["cheap"]))
        # Independent gate (default off): rides the SAME decide() call above,
        # so enabling it costs no extra model call — only a longer prompt.
        judge_depth = bool(cfg_get("navigator.shadow_planning_depth", False))
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
        # At dispatch this thread's own run-dir doesn't exist yet; the
        # decision is being made in the parent's context, so the parent
        # thread's goal-brain is the right steering input. Top-level goals
        # have no parent and get "" — the goal verbatim is their whole
        # intent at this point anyway.
        goal_brain = ""
        parent_id = str(thread.get("parent_handle_id") or "")
        if parent_id:
            try:
                import runs as _runs
                import thread_brain as _tb
                goal_brain = _tb.load_thread_brain(_runs.run_dir(parent_id))
            except Exception:
                goal_brain = ""
        nav_input = NavigatorInput(
            goal=goal,
            thread=thread,
            recall_block=rr.as_context_block() if rr is not None else "",
            goal_brain=goal_brain,
            budget={"note": "live dispatch shadow; loop budget not yet allocated"},
        )
        pipeline_actual = {
            "point": "dispatch",
            "move_equivalent": pipeline_move,
            "live": True,
            **(extra or {}),
        }
        if judge_depth:
            # Marker + baseline for analyze_planning_depth_agreement(): its
            # presence means this row carries a real depth judgment (the
            # model was asked), not just the dataclass's unjudged default.
            pipeline_actual["depth_equivalent"] = "plan"
        from navigator_prompt import decide
        decision, _meta = decide(
            nav_input,
            tiers=tiers,
            adapter_factory=adapter_factory,
            shadow=True,
            pipeline_actual=pipeline_actual,
            judge_planning_depth=judge_depth,
        )
        return decision
    except Exception as exc:
        import logging
        logging.getLogger("navigator").debug("live dispatch shadow skipped: %s", exc)
        return None


# The heuristic recovery tree (agent_loop._handle_blocked_step) emits one of
# four actions; each maps to the navigator move that subsumes it. This is the
# mapping the dumb-loop audit (docs/DUMB_LOOP_AUDIT.md, priority-1 point) names:
# retry == keep going on this thread (extend), redecompose/split == break the
# work apart (fork), stuck == give up on this thread (close).
_BLOCKED_ACTION_TO_MOVE = {
    "retry": "extend",
    "redecompose": "fork",
    "split": "fork",
    "stuck": "close",
}


def shadow_blocked_step_live(
    goal: str,
    *,
    run_dir: Optional[Any] = None,
    heuristic_action: str = "",
    block_reason: str = "",
    signals: Optional[Dict[str, Any]] = None,
    turn_index: int = 0,
    tiers: Optional[List[str]] = None,
    adapter_factory=None,
) -> Optional[Any]:
    """Live shadow at the blocked-step recovery decision: decide-only.

    Called from agent_loop's blocked-step handler right after the heuristic
    tree (`_handle_blocked_step`) picks retry / redecompose / split / stuck.
    The navigator independently judges the SAME block from the goal-brain plus
    the work-report signals (retries, convergence, sibling-failure rate,
    replan count). This is the priority-1 point of the dumb-loop audit data
    half — the densest threshold cluster, where a wrong extend-vs-close call
    wastes runs or strands goals.

    Config-gated by `navigator.shadow_blocked_step` (default OFF in code: a
    model call per blocked step is real spend and latency, so a deployment
    opts in via workspace config). Also fires when `navigator.act_blocked_step`
    is on — the escalate cutover (2026-07-03) needs the decision regardless of
    the shadow flag, and the NAVIGATOR_DECIDED row it logs is the audit trail
    that keeps accruing cutover evidence. This function itself never raises
    and never alters recovery — acting on the returned decision is the
    caller's job (`loop_blocked._navigator_act_blocked_step`).
    Returns the decision or None.
    """
    try:
        from config import get as cfg_get
        if not (bool(cfg_get("navigator.shadow_blocked_step", False))
                or bool(cfg_get("navigator.act_blocked_step", False))):
            return None
        if tiers is None:
            tiers = list(cfg_get("navigator.shadow_tiers", ["cheap"]))
    except Exception:
        return None

    try:
        sig = dict(signals or {})
        move_equivalent = _BLOCKED_ACTION_TO_MOVE.get(heuristic_action, heuristic_action)
        # stuck is the only terminal action — everything else is the loop
        # still trying. status feeds the navigator's extend-vs-close instinct.
        status = "failed" if heuristic_action == "stuck" else "partial"
        work = WorkReport(
            move="execute",
            status=status,
            summary=(block_reason or "step blocked")[:300],
            recommendation=heuristic_action,
            signals=sig,
        )
        goal_brain = ""
        if run_dir is not None:
            try:
                import thread_brain as _tb
                goal_brain = _tb.load_thread_brain(run_dir)
            except Exception:
                goal_brain = ""
        nav_input = NavigatorInput(
            goal=goal,
            goal_brain=goal_brain,
            turn_index=turn_index,
            last_work=work,
            budget={"note": "live blocked-step shadow"},
        )
        pipeline_actual = {
            "point": "blocked_step",
            "move_equivalent": move_equivalent,
            "heuristic_action": heuristic_action,
            "live": True,
            **{k: sig[k] for k in ("retries", "converging", "sibling_fail_rate",
                                   "replan_count") if k in sig},
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
        logging.getLogger("navigator").debug("live blocked-step shadow skipped: %s", exc)
        return None


def _tabulate_agreement(
    rows: List[Dict[str, Any]],
    group_keys: Dict[str, Any],
    agree_fn: Any,
) -> tuple:
    """Shared tabulation core for analyze_live_agreement() and
    analyze_planning_depth_agreement(): bucket rows into one or more
    per-key agree/diverge tables plus a flat divergence list, given an
    agreement predicate. group_keys maps table-name -> row -> key-value
    (a row may be tabulated into more than one table, e.g. by-move AND
    by-point from the same pass).
    """
    tables: Dict[str, Dict[str, Dict[str, int]]] = {name: {} for name in group_keys}
    divergences = []
    agreements = 0
    for r in rows:
        agree = agree_fn(r)
        if agree:
            agreements += 1
        else:
            divergences.append(r)
        for name, key_fn in group_keys.items():
            slot = tables[name].setdefault(str(key_fn(r)), {"agree": 0, "diverge": 0})
            slot["agree" if agree else "diverge"] += 1
    return tables, divergences, agreements


# VERIFY_LEARN_ARC V4: the adjudication verdict vocabulary. A divergence is not
# an error — it is a disagreement between the navigator (LLM policy) and the
# pipeline (current heuristic), and either side may be right.
ADJ_VERDICTS = ("navigator_right", "pipeline_right", "both_defensible")


def _divergence_key(row: Dict[str, Any]) -> str:
    """Deterministic join key for a divergence row and its adjudication.

    Derived from the fields a NAVIGATOR_DECIDED divergence and its
    NAVIGATOR_ADJUDICATED record both carry identically: the second-precision
    timestamp plus the decision point and the two moves that disagreed. Stable
    across reads (so we never re-adjudicate) and effectively unique — two
    distinct divergences would have to share a wall-clock second AND the same
    point/move/pipeline, at which point they are the same decision.
    """
    import hashlib
    raw = "|".join(str(row.get(k, "")) for k in
                   ("timestamp", "point", "move", "pipeline"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def analyze_live_agreement(
    events: List[Dict[str, Any]],
    adjudications: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Tabulate live NAVIGATOR_DECIDED rows into per-move agreement counts —
    the cutover evidence (NAVIGATOR_SCHEMA.md analysis query, structured).

    Agreement means navigator move == pipeline move_equivalent; a navigator
    escalate/close against a pipeline guard_refused counts as agreement-in-kind
    (both refused the run). Everything else is a divergence row, returned
    verbatim for adjudication — divergence is eval data, not an error.

    When ``adjudications`` (a ``div_key -> record`` map, VERIFY_LEARN_ARC V4) is
    given, each divergence gets its verdict attached (``d["adjudication"]``) and
    the result grows an ``adjudicated`` breakdown counting the verdicts — the
    surface Jeremy uses for cutover calls, now standing instead of by-hand.
    """
    rows = []
    for e in events:
        if e.get("event_type") != "NAVIGATOR_DECIDED":
            continue
        c = e.get("context") or {}
        pa = c.get("pipeline_actual") or {}
        if not pa.get("live"):
            continue
        rows.append({
            "timestamp": str(e.get("timestamp", ""))[:19],
            "move": c.get("move"),
            "confidence": c.get("confidence"),
            "tier": c.get("tier"),
            "pipeline": pa.get("move_equivalent"),
            "point": pa.get("point") or "dispatch",
            "reasoning": str(c.get("reasoning", ""))[:600],
            "goal_preview": str(
                (c.get("input_digest") or {}).get("goal_preview", ""))[:80],
        })

    def _agree(r: Dict[str, Any]) -> bool:
        m, p = r["move"], r["pipeline"]
        in_kind = m in ("close", "escalate") and p == "guard_refused"
        return m == p or in_kind

    tables, divergences, agreements = _tabulate_agreement(
        rows,
        {"by_move": lambda r: r["move"], "by_point": lambda r: r["point"]},
        _agree,
    )
    adj = adjudications or {}
    breakdown = {v: 0 for v in ADJ_VERDICTS}
    breakdown["unadjudicated"] = 0
    for d in divergences:
        rec = adj.get(_divergence_key(d))
        verdict = (rec or {}).get("verdict")
        d["adjudication"] = verdict
        if verdict in breakdown:
            breakdown[verdict] += 1
        else:
            breakdown["unadjudicated"] += 1
    return {
        "live_rows": len(rows),
        "by_move": tables["by_move"],
        "by_point": tables["by_point"],
        "agreements": agreements,
        "divergences": divergences,
        "adjudicated": breakdown,
    }


def analyze_planning_depth_agreement(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Tabulate live NAVIGATOR_DECIDED rows carrying a planning-depth
    judgment into per-depth agreement counts — the same shape as
    analyze_live_agreement(), applied to the thread-arch #5 field (MILESTONES
    1.5) instead of the move field, for the same per-class-cutover workflow
    (docs/NAVIGATOR_SCHEMA.md).

    Only rows where the shadow was actually on are counted: presence of
    pipeline_actual.depth_equivalent is the marker (set by
    shadow_dispatch_live() only when navigator.shadow_planning_depth is on) —
    every other live row's planning_depth is an unjudged default, not data.

    Agreement means navigator planning_depth == pipeline depth_equivalent.
    The pipeline is always the moral equivalent of "plan" today (the normal
    full pipeline, regardless of goal shape) — so every "plan" row agrees and
    every lighter shape (one-shot / thin-plan / spawn-sub-goal) is a
    divergence: the informative case, exactly like a move divergence,
    returned verbatim for adjudication.
    """
    rows = []
    for e in events:
        if e.get("event_type") != "NAVIGATOR_DECIDED":
            continue
        c = e.get("context") or {}
        pa = c.get("pipeline_actual") or {}
        if not pa.get("live") or "depth_equivalent" not in pa:
            continue
        rows.append({
            "timestamp": str(e.get("timestamp", ""))[:19],
            "planning_depth": c.get("planning_depth", "plan"),
            "pipeline_depth": pa.get("depth_equivalent"),
            "move": c.get("move"),
            "confidence": c.get("confidence"),
            "tier": c.get("tier"),
            "goal_preview": str(
                (c.get("input_digest") or {}).get("goal_preview", ""))[:80],
        })
    tables, divergences, agreements = _tabulate_agreement(
        rows,
        {"by_depth": lambda r: r["planning_depth"]},
        lambda r: r["planning_depth"] == r["pipeline_depth"],
    )
    return {
        "live_rows": len(rows),
        "by_depth": tables["by_depth"],
        "agreements": agreements,
        "divergences": divergences,
    }


def _load_navigator_events() -> List[Dict[str, Any]]:
    """Read all NAVIGATOR_DECIDED + NAVIGATOR_ADJUDICATED rows from the workspace
    captain's log (active + rotated archives), chronological. Shared by the
    --agreement table and the V4 adjudication pass so both see one source."""
    try:
        from captains_log import _log_path  # type: ignore
        base = _log_path().parent
    except Exception:
        base = Path.home() / ".maro" / "workspace" / "memory"
    events: List[Dict[str, Any]] = []
    for p in sorted(base.glob("captains_log*.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if "NAVIGATOR_DECIDED" not in line and "NAVIGATOR_ADJUDICATED" not in line:
                    continue
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            continue
    return events


def _load_adjudications(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build ``div_key -> adjudication record`` from NAVIGATOR_ADJUDICATED rows.
    Last write wins (a re-adjudication supersedes), so the map reflects the
    current verdict for each divergence."""
    out: Dict[str, Dict[str, Any]] = {}
    for e in events:
        if e.get("event_type") != "NAVIGATOR_ADJUDICATED":
            continue
        c = e.get("context") or {}
        key = c.get("div_key")
        if key:
            out[str(key)] = c
    return out


def _adjudicate_one(row: Dict[str, Any], adapter) -> Optional[Dict[str, str]]:
    """LLM adjudication of a single divergence. Returns
    ``{"verdict": <ADJ_VERDICTS>, "rationale": <one line>}`` or None if the
    model produced nothing usable (skip — never store a garbage verdict)."""
    from navigator_prompt import _complete
    from llm_parse import extract_json
    system = (
        "You adjudicate a disagreement about what a running autonomous agent "
        "should do next. Two decision-makers disagreed: the NAVIGATOR (an LLM "
        "policy) and the PIPELINE (the current hard-coded heuristic). Neither is "
        "presumed correct. Decide which call was better for THIS situation, or "
        "whether both are defensible.\n\n"
        "Reply with ONE JSON object and nothing else:\n"
        '{"verdict": "navigator_right" | "pipeline_right" | "both_defensible", '
        '"rationale": "<one sentence>"}'
    )
    user = (
        f"Decision point: {row.get('point', 'dispatch')}\n"
        f"Goal: {row.get('goal_preview', '')}\n"
        f"NAVIGATOR chose: {row.get('move')} "
        f"(confidence {row.get('confidence')})\n"
        f"  navigator reasoning: {row.get('reasoning', '') or '(none recorded)'}\n"
        f"PIPELINE did: {row.get('pipeline')}\n\n"
        "Which was the better call?"
    )
    try:
        raw = _complete(adapter, system, user, max_tokens=300)
    except Exception:
        return None
    obj = extract_json(raw)
    if not isinstance(obj, dict):
        return None
    verdict = str(obj.get("verdict", "")).strip()
    if verdict not in ADJ_VERDICTS:
        return None
    return {"verdict": verdict, "rationale": str(obj.get("rationale", ""))[:300]}


def adjudicate_navigator_divergences(
    run_id: str = "",
    *,
    max_per_cycle: Optional[int] = None,
    tier: Optional[str] = None,
    dry_run: bool = False,
    adapter_factory: Optional[Any] = None,
    verbose: bool = False,
) -> Dict[str, Any]:
    """VERIFY_LEARN_ARC V4: adjudicate un-adjudicated navigator/pipeline
    divergences with a capped, cheap-tier LLM pass. Append-only — each verdict
    is a NAVIGATOR_ADJUDICATED row keyed to the divergence it judges; nothing
    is reverted or acted on. Rides the evolver cadence hook (no daemon).

    Returns a summary dict. ``dry_run`` renders verdicts without persisting them.
    """
    from config import get as config_get
    if max_per_cycle is None:
        max_per_cycle = int(config_get("navigator.adjudicate_max_per_cycle", 5))
    if tier is None:
        tier = str(config_get("navigator.adjudicate_tier", "cheap"))

    events = _load_navigator_events()
    existing = _load_adjudications(events)
    summary = analyze_live_agreement(events, adjudications=existing)
    divergences = summary["divergences"]
    todo = [d for d in divergences if not d.get("adjudication")]
    result: Dict[str, Any] = {
        "divergences_total": len(divergences),
        "already_adjudicated": len(divergences) - len(todo),
        "adjudicated": 0,
        "skipped_no_verdict": 0,
        "write_failed": 0,
        "verdicts": {v: 0 for v in ADJ_VERDICTS},
        "dry_run": dry_run,
    }
    if not todo:
        return result

    factory = adapter_factory
    if factory is None:
        from navigator_prompt import _default_adapter_factory
        factory = _default_adapter_factory
    try:
        adapter = factory(tier)
    except Exception as exc:
        log.warning("navigator adjudication: no adapter for tier %s: %s", tier, exc)
        result["error"] = f"no adapter for tier {tier}"
        return result

    for d in todo[:max_per_cycle]:
        verdict = _adjudicate_one(d, adapter)
        if verdict is None:
            result["skipped_no_verdict"] += 1
            continue
        if verbose or dry_run:
            print(f"  {d['timestamp']} [{d.get('point','dispatch')}] "
                  f"{d['move']} vs {d['pipeline']} -> {verdict['verdict']}: "
                  f"{verdict['rationale']}")
        if dry_run:
            result["adjudicated"] += 1
            result["verdicts"][verdict["verdict"]] += 1
            continue
        # Persist FIRST, count only after the append-only evidence row is durably
        # written. Counting before persist (with log_event best-effort + a
        # swallowing except) would report the backlog cleared while a silently
        # failed write leaves the divergence to be re-judged + re-spent next run
        # (adversarial-review finding B). raise_on_error surfaces the failure so
        # we can count it instead of lying about it.
        try:
            from captains_log import log_event, NAVIGATOR_ADJUDICATED
            log_event(
                NAVIGATOR_ADJUDICATED,
                subject="navigator",
                summary=f"adjudicated {d['move']} vs {d['pipeline']} "
                        f"({d.get('point','dispatch')}): {verdict['verdict']}",
                context={
                    "div_key": _divergence_key(d),
                    "verdict": verdict["verdict"],
                    "rationale": verdict["rationale"],
                    "tier": tier,
                    "run_id": run_id,
                    # human-readable echo of the judged divergence (debuggability)
                    "timestamp": d["timestamp"],
                    "point": d.get("point", "dispatch"),
                    "move": d["move"],
                    "pipeline": d["pipeline"],
                    "goal_preview": d.get("goal_preview", ""),
                },
                raise_on_error=True,
            )
        except Exception as exc:
            log.warning("navigator adjudication: persist failed for %s: %s",
                        _divergence_key(d), exc)
            result["write_failed"] += 1
            continue
        result["adjudicated"] += 1
        result["verdicts"][verdict["verdict"]] += 1
    # Refresh the materialized navigator-lesson view from the current
    # adjudications (V5). Cheap (clustering, no LLM) and only ever consumed when
    # navigator.lesson_inject is on, but kept fresh whenever adjudications move.
    if not dry_run:
        try:
            crystallize_navigator_lessons()
        except Exception as _cryst_exc:
            log.debug("navigator lesson crystallization failed (non-fatal): %s", _cryst_exc)
    return result


# ---------------------------------------------------------------------------
# VERIFY_LEARN_ARC V5 — navigator lessons (crystallize adjudicated
# navigator-wrong clusters, inject into decide())
# ---------------------------------------------------------------------------

def _navigator_lessons_path() -> Path:
    try:
        from orch_items import memory_dir
        return memory_dir() / "navigator_lessons.jsonl"
    except Exception:
        return Path.home() / ".maro" / "workspace" / "memory" / "navigator_lessons.jsonl"


def _lesson_text(point: str, move: str, pipeline: str, count: int,
                 examples: List[str]) -> str:
    """One navigator lesson: a recurring shape where the navigator was
    adjudicated wrong and the pipeline's call was the better one."""
    where = "" if point == "dispatch" else f" at the {point} decision"
    eg = "; ".join(e for e in examples if e)[:200]
    tail = f" (e.g. {eg})" if eg else ""
    return (f"When you chose '{move}'{where} and the pipeline instead did "
            f"'{pipeline}', a judge found the pipeline's call better {count}× — "
            f"prefer '{pipeline}' for this shape unless the situation clearly "
            f"differs{tail}.")


def crystallize_navigator_lessons(min_count: int = 3) -> Dict[str, Any]:
    """VERIFY_LEARN_ARC V5: cluster ``pipeline_right`` adjudications by shape
    (point, navigator move, pipeline move) and materialize a navigator lesson
    for every cluster at/above the graduation threshold (≥3 same-shape). The
    lessons file is a derived view (full rewrite) over the append-only
    adjudication evidence — recomputable, so rewriting it never loses data.
    Returns a small summary.
    """
    events = _load_navigator_events()
    adj = _load_adjudications(events)
    clusters: Dict[tuple, Dict[str, Any]] = {}
    for rec in adj.values():
        if rec.get("verdict") != "pipeline_right":
            continue
        key = (rec.get("point", "dispatch"), rec.get("move"), rec.get("pipeline"))
        slot = clusters.setdefault(key, {"count": 0, "examples": []})
        slot["count"] += 1
        gp = rec.get("goal_preview")
        if gp and len(slot["examples"]) < 3:
            slot["examples"].append(str(gp))
    lessons = []
    for (point, move, pipeline), slot in clusters.items():
        if slot["count"] < min_count:
            continue
        lessons.append({
            "point": point, "navigator_move": move, "pipeline_move": pipeline,
            "count": slot["count"], "examples": slot["examples"],
            "lesson": _lesson_text(point, move, pipeline, slot["count"],
                                   slot["examples"]),
        })
    lessons.sort(key=lambda l: l["count"], reverse=True)
    path = _navigator_lessons_path()
    try:
        import os as _os
        path.parent.mkdir(parents=True, exist_ok=True)
        # Full rewrite of the materialized view (not append) — it is derived
        # from the adjudication evidence and must reflect only current clusters.
        # Write to a temp sibling + atomic rename so a crash or a concurrent
        # writer can never leave decide() reading a half-truncated file
        # (adversarial-review finding C).
        body = "".join(json.dumps(l) + "\n" for l in lessons)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(body, encoding="utf-8")
        _os.replace(tmp, path)
    except Exception:
        pass
    return {"clusters": len(clusters), "lessons": len(lessons)}


def load_navigator_lessons(limit: int = 8) -> List[str]:
    """Decide-time consumer (V5): the materialized navigator lesson texts,
    most-supported first, capped. Cheap — reads only the small derived view,
    not the raw logs. Empty list if none / disabled / unreadable."""
    path = _navigator_lessons_path()
    if not path.exists():
        return []
    out: List[str] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            text = d.get("lesson")
            if text:
                out.append(str(text))
            if len(out) >= limit:
                break
    except Exception:
        return []
    return out


def _analyze_main(json_out: bool) -> int:
    """--agreement mode: read the workspace captain's log (active + rotated
    archives) and print the live-agreement table."""
    events = _load_navigator_events()
    adjudications = _load_adjudications(events)
    summary = analyze_live_agreement(events, adjudications=adjudications)
    depth_summary = analyze_planning_depth_agreement(events)
    if json_out:
        print(json.dumps({"moves": summary, "planning_depth": depth_summary}, indent=2))
        return 0
    print(f"live NAVIGATOR_DECIDED rows: {summary['live_rows']} "
          f"(agreements {summary['agreements']})")
    print("by decision point:")
    for point, s in sorted(summary.get("by_point", {}).items()):
        print(f"  {point:12s} agree={s['agree']:3d} diverge={s['diverge']:3d}")
    print("by navigator move:")
    for move, s in sorted(summary["by_move"].items()):
        print(f"  {move:10s} agree={s['agree']:3d} diverge={s['diverge']:3d}")
    adjb = summary.get("adjudicated", {})
    if any(adjb.get(v) for v in ADJ_VERDICTS):
        print("adjudicated divergences (VERIFY_LEARN_ARC V4):")
        for v in ADJ_VERDICTS:
            print(f"  {v:16s} {adjb.get(v, 0):3d}")
        print(f"  {'unadjudicated':16s} {adjb.get('unadjudicated', 0):3d}")
    if summary["divergences"]:
        print("divergences (adjudicate each — divergence is eval data):")
        for d in summary["divergences"]:
            verdict = d.get("adjudication")
            tag = f" -> {verdict}" if verdict else ""
            print(f"  {d['timestamp']} [{d.get('point','dispatch')}] "
                  f"{d['move']}({d['confidence']}) "
                  f"vs {d['pipeline']}{tag} | {d['goal_preview']}")
    if depth_summary["live_rows"]:
        print(f"\nplanning-depth shadow rows: {depth_summary['live_rows']} "
              f"(agreements {depth_summary['agreements']}) "
              "[navigator.shadow_planning_depth]:")
        for depth, s in sorted(depth_summary["by_depth"].items()):
            print(f"  {depth:14s} agree={s['agree']:3d} diverge={s['diverge']:3d}")
        if depth_summary["divergences"]:
            print("  lighter-shape candidates (adjudicate each):")
            for d in depth_summary["divergences"]:
                print(f"    {d['timestamp']} {d['planning_depth']} "
                      f"(move={d['move']}, conf={d['confidence']}) | {d['goal_preview']}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Shadow-replay historical runs through the navigator "
                    "(decide-only; changes nothing).")
    parser.add_argument("runs", nargs="*", help="handle ids / prefixes / run-dir paths")
    parser.add_argument("--agreement", action="store_true",
                        help="tabulate live NAVIGATOR_DECIDED agreement per move "
                             "(the per-class cutover evidence) and exit")
    parser.add_argument("--adjudicate", action="store_true",
                        help="LLM-adjudicate un-adjudicated divergences "
                             "(VERIFY_LEARN_ARC V4); with --json/--dry-run to preview")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --adjudicate: render verdicts without persisting")
    parser.add_argument("--max", type=int, default=None,
                        help="with --adjudicate: cap divergences judged this run")
    parser.add_argument("--point", choices=("dispatch", "closure", "both"),
                        default="dispatch")
    parser.add_argument("--tiers", default="",
                        help="comma-separated tier list, e.g. cheap,mid,power")
    parser.add_argument("--json", action="store_true", help="emit JSON lines")
    args = parser.parse_args(argv)

    if args.adjudicate:
        result = adjudicate_navigator_divergences(
            run_id="cli", max_per_cycle=args.max,
            dry_run=args.dry_run, verbose=not args.json)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"divergences: {result['divergences_total']} "
                  f"({result['already_adjudicated']} already adjudicated); "
                  f"this run: {result['adjudicated']} judged, "
                  f"{result['skipped_no_verdict']} skipped "
                  f"{'(dry-run)' if result['dry_run'] else ''}")
            print(f"  verdicts: {result['verdicts']}")
        return 0
    if args.agreement:
        return _analyze_main(args.json)
    if not args.runs:
        parser.error("runs required unless --agreement/--adjudicate")

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
