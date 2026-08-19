"""Durable edge trace — the transitions a run actually took.

**Why this exists.** Until now the pipeline recorded its *nodes* (a step ran,
a verdict was stamped, a gate fired) but never its *edges*. `LoopPhase` is the
sharpest case: `ctx.phase` is in-memory only, `set_phase()` mutates it, and
nothing writes it to disk — so "which way did this run go at that branch" had
to be reconstructed after the fact from artifact presence plus a captain's-log
slice that is not even run-scoped (`runs.slice_log_for_run` copies to EOF of the
GLOBAL log, so it carries concurrent runs' events). Reconstruction gets the
shape right and the ordering wrong, and it cannot distinguish "this edge was
not taken" from "this edge was taken but nothing recorded it".

This module closes that gap: every transition appends one row to the run's own
`build/trace.jsonl` at the moment it is taken. Direct extension of the
persist-the-artifacts decree (Jeremy, 2026-07-29) that put closure outcomes in
the run dir, and of Jeremy's 2026-08-18 follow-on: *"add all of the edges that
aren't written down into the system as tracked metadata or artifacts."*

**Ordering.** Append order under flock is authoritative — there is deliberately
no sequence number. A row's position in the file is when it actually happened,
which matters because the answer-first split (`handle.py`, `verdict_followup`)
runs close-out and notify BEFORE closure and the quality gate. A trace written
in source order would record a false sequence; this one records the real one.

**Honesty on failure.** Recording is best-effort and never affects a run — but
a silently dropped edge would read downstream as "not traveled", which is a
false negative that looks like a fact. So drops are counted, and the first drop
for a run tries to leave a `trace.degraded` marker row in the file. A consumer
that sees that marker knows the trace is incomplete rather than believing a
gap.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

TRACE_FILENAME = "trace.jsonl"

# One announced cut for every string attribute, applied centrally in
# record_edge rather than as a bare slice at each call site. A trace row is
# evidence: a silently shortened reason reads as the whole reason, so the cut
# goes through context_budget.clip, which marks what it removed and how much.
EVIDENCE_CAP = 400

# --------------------------------------------------------------------------
# Node vocabulary. These ids are the shared contract between the recorder and
# every consumer (scripts/run_atlas/template.html declares the same ids with a
# code anchor on each). A typo here would invent a phantom node downstream, so
# record_edge marks ids it does not recognise rather than letting them pass as
# real — see _MARK_UNKNOWN.
# --------------------------------------------------------------------------

# LoopPhase transitions (loop_types.LoopPhase). Recorded from the state machine
# itself, so these are exact rather than inferred.
PHASE_NODES = frozenset({
    "phase.init", "phase.decompose", "phase.pre_flight", "phase.parallel",
    "phase.prepare", "phase.execute", "phase.finalize",
})

INTAKE_NODES = frozenset({
    "intake.arrive", "intake.cli", "intake.listener", "intake.queue",
    "intake.scheduler", "intake.navigator", "intake.nav_escalate",
    "intake.guard_refuse", "intake.open_run",
})

ROUTE_NODES = frozenset({
    "route.classify", "route.now", "route.now_verify", "route.agenda",
    "route.clarity", "route.clarify_stop", "route.rewrite", "route.persona",
    "route.recall", "route.scope_ok", "route.scope_fail", "route.scope_skip",
})

PLAN_NODES = frozenset({
    "plan.loop_created", "plan.fence", "plan.recall", "plan.skills",
    "plan.decompose", "plan.cuts", "plan.manifest", "plan.busy_refused",
    "plan.budget_gate", "plan.killswitch", "plan.cost_gate",
})

EXEC_NODES = frozenset({
    "exec.step", "exec.session_reuse", "exec.inject", "exec.boundary",
    "exec.reanchor", "exec.validate", "exec.ralph", "exec.advisor",
    "exec.director", "exec.navigator", "exec.too_broad", "exec.scavenge",
    "exec.write_fence", "exec.fabrication", "exec.blocked", "exec.retry",
    "exec.redecompose", "exec.split", "exec.budget_ladder", "exec.timeout",
    "exec.missing_input", "exec.budget_break", "exec.nav_escalate",
    "exec.stuck", "exec.never_ran", "exec.parallel",
})

FIN_NODES = frozenset({
    "fin.result", "fin.partial", "fin.checkpoint", "fin.diagnose",
    "fin.world_facts", "fin.auto_recovery", "fin.stop_verdict", "fin.pause",
})

VERIFY_NODES = frozenset({
    "verify.plan", "verify.closure", "verify.audit", "verify.downgrade",
    "verify.restart", "verify.provenance", "verify.stamp", "verify.contested",
})

GATE_NODES = frozenset({
    "gate.verdict", "gate.crossref", "gate.claims", "gate.pass",
    "gate.escalate", "gate.overruled", "gate.escalate_rerun",
})

CLOSE_NODES = frozenset({
    "close.curate", "close.learning", "close.no_verdict", "close.stranded",
    "close.terminal",
})

# Terminal outcomes. `close.terminal -> term.<success_class>` closes every run
# that reaches curation; runs that die earlier terminate at their own stop node.
TERM_NODES = frozenset({
    # run_curation.classify_outcome's ladder
    "term.success", "term.partial", "term.failed", "term.done-unverified",
    "term.done-not-achieved", "term.achieved-not-done", "term.interrupted",
    "term.unknown",
    # `done-verdict-pending` is the answer-first early close: curation runs
    # before closure has produced a verdict, so the first of the two closes
    # legitimately terminates here and a later one supersedes it.
    "term.done-verdict-pending",
    # raw statuses, used when a run has no card (pre-2026-07) or dies early
    "term.clarification_needed", "term.stranded", "term.error",
    "term.incomplete", "term.done", "term.stuck", "term.refused_busy",
})

# Bookkeeping about the trace itself.
META_NODES = frozenset({"trace.degraded"})

NODES = (PHASE_NODES | INTAKE_NODES | ROUTE_NODES | PLAN_NODES | EXEC_NODES
         | FIN_NODES | VERIFY_NODES | GATE_NODES | CLOSE_NODES | TERM_NODES
         | META_NODES)

# --------------------------------------------------------------------------

_lock = threading.Lock()
_dropped: Dict[str, int] = {}      # run-dir path -> edges we failed to record
_degraded_marked: set = set()      # run-dirs that already carry the marker


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_run_dir(run_dir, handle_id) -> Optional[Path]:
    if run_dir is not None:
        return Path(run_dir)
    try:
        from runs import current_run_dir, run_dir as run_dir_for
        if handle_id:
            rd = run_dir_for(handle_id)
            if rd is not None and Path(rd).exists():
                return Path(rd)
        return current_run_dir()
    except Exception:
        return None


def _enabled() -> bool:
    try:
        from config import get as _get
        return bool(_get("trace.enabled", True))
    except Exception:
        # A config failure must not silence the record; recording is the
        # default and the safer direction.
        return True


def _clip(v: Any) -> Any:
    """Bound a single attribute value, announcing the cut (never a bare slice)."""
    if not isinstance(v, str) or len(v) <= EVIDENCE_CAP:
        return v
    try:
        from context_budget import clip
        return clip(v, EVIDENCE_CAP)
    except Exception:
        return v


def _note_drop(rd: Optional[Path]) -> None:
    """Count a lost edge and, once per run, try to say so in the file itself.

    A dropped edge that leaves no trace reads downstream as "this run never
    went there" — the one failure mode this module exists to prevent.
    """
    key = str(rd) if rd is not None else "<no-run-dir>"
    with _lock:
        _dropped[key] = _dropped.get(key, 0) + 1
        first = key not in _degraded_marked
        _degraded_marked.add(key)
    if not first or rd is None:
        return
    try:
        from file_lock import locked_append
        locked_append(
            Path(rd) / "build" / TRACE_FILENAME,
            json.dumps({"ts": _now(), "from": "trace.degraded",
                        "to": "trace.degraded",
                        "attrs": {"reason": "an edge failed to record; this "
                                            "trace is incomplete"}}),
        )
    except Exception:
        pass


def dropped_count(run_dir=None) -> int:
    """Edges this process failed to record for a run (0 when healthy)."""
    rd = _resolve_run_dir(run_dir, None)
    key = str(rd) if rd is not None else "<no-run-dir>"
    with _lock:
        return _dropped.get(key, 0)


def record_edge(frm: str, to: str, *, loop_id: Optional[str] = None,
                run_dir=None, handle_id: Optional[str] = None,
                **attrs: Any) -> bool:
    """Append one traversed edge to the run's `build/trace.jsonl`.

    Returns True when the row was written. Never raises: a trace failure must
    not change what a run does. Unknown node ids are still recorded (losing the
    row would be worse) but flagged, so tests can assert the vocabulary holds.
    """
    if not _enabled():
        return False
    rd = _resolve_run_dir(run_dir, handle_id)
    if rd is None:
        # No run context — nothing to attach the edge to. Counted, not raised:
        # some call sites legitimately run outside a run (unit tests, doctor).
        _note_drop(None)
        return False
    try:
        from file_lock import locked_append
        from secret_scrub import scrub

        row: Dict[str, Any] = {
            "ts": _now(),
            "loop_id": loop_id or "",
            "from": frm,
            "to": to,
        }
        unknown = [n for n in (frm, to) if n not in NODES]
        if unknown:
            row["unknown_node"] = unknown
        if attrs:
            row["attrs"] = {k: _clip(v) for k, v in attrs.items()}
        locked_append(Path(rd) / "build" / TRACE_FILENAME,
                      json.dumps(scrub(row), default=str))
        return True
    except Exception:
        _note_drop(rd)
        return False


def record_path(nodes, *, loop_id: Optional[str] = None, run_dir=None,
                handle_id: Optional[str] = None, **attrs: Any) -> int:
    """Record a straight run of edges (a -> b -> c). Returns rows written."""
    seq = [n for n in nodes if n]
    return sum(
        1 for a, b in zip(seq, seq[1:])
        if record_edge(a, b, loop_id=loop_id, run_dir=run_dir,
                       handle_id=handle_id, **attrs)
    )


def read_trace(run_dir, *, counted: bool = False):
    """Read a run's trace in recorded order.

    Uses loads_clean so a byte-tainted row is skipped rather than laundered
    into legitimate-looking content (jsonl_utils.loads_clean). A skipped row is
    WARNed and counted, never silently swallowed: a trace that quietly returns
    40 of 41 edges is indistinguishable from a run that took 40, which is the
    same silence the no-silent-drop tripwire exists to prevent.

    counted=True returns (rows, skipped) instead of just rows.
    """
    p = Path(run_dir) / "build" / TRACE_FILENAME
    rows: list = []
    skipped = 0
    try:
        from jsonl_utils import store_text, loads_clean
        text = store_text(p)
    except FileNotFoundError:
        return ([], 0) if counted else []
    except Exception as exc:
        _log_warn(f"run_trace: cannot read {p}: {exc!r}")
        return ([], 0) if counted else []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(loads_clean(line))
        except Exception as exc:
            skipped += 1
            _log_warn(f"run_trace: skipped an unreadable row in {p}: {exc!r}")
    if skipped:
        _log_warn(f"run_trace: {p} has {skipped} unreadable row(s); "
                  f"returning {len(rows)} edge(s) — the trace is incomplete")
    return (rows, skipped) if counted else rows


def _log_warn(msg: str) -> None:
    try:
        import logging
        logging.getLogger("run_trace").warning(msg)
    except Exception:
        pass
