"""Durable post-run tail jobs — the registry is a FILE, not a module dict.

The post-run tail (deferred lesson extraction, skill crystallization, skill
maintenance, health probes, evolver scans, the surface refresh) used to be
registered as in-process closures and drained by `handle()`'s finalize block:
`_POST_NOTIFY_LEARNING` in handle.py, `_POST_NOTIFY_MAINTENANCE` in
loop_finalize.py. That made the tail *reordered* — it ran after the
`run_completed` notify instead of before it — but never *asynchronous*: same
process, same thread, so a caller waiting on process exit (`python3 -m handle`,
any script, any CI step) paid the whole tail anyway. Measured on the
2026-08-19 sol-advisor run: deliverable at 14m37s, process alive until
31m08s — 53% of wall clock spent after the answer existed.

Two things had to change to make a real process spawn possible, and they are
the same thing: a closure cannot cross a process boundary, and a module-level
dict cannot survive one. So the registration becomes a **serializable record
appended to a per-run store**, and the drain becomes a function that reads
that store — callable from the parent (in-process fallback) or from a fresh
`maro finalize-tail --handle-id X` child that knows nothing but the id.

Why a file rather than a second in-process registry: handle.py is also a
documented `python -m handle` entry point, so run that way it executes as
`__main__` and any importer's `import handle` loads a SECOND copy with its own
dict. That bug already cost this project one round (3-lens review of
`707a541`) and was worked around by placing the maintenance twin in a
different module — a placement rule that has to be re-derived by every future
author. A store keyed by handle_id has no module identity to get wrong.

Store shape — `<run_dir>/build/tail_jobs.jsonl`, **append-only**, three row
kinds:

    {"event": "job",   "seq": 1, "kind": "learning",    "spec": {...}, "recorded_at": ...}
    {"event": "claim", "seq": 0, "pid": 4242, "claimed_at": ...}
    {"event": "done",  "seq": 1, "ok": true, "ran": 1, "finished_at": ...}

Append-only is deliberate, not incidental: this store is written by two
processes that can overlap, and the destructive-rewrite arc (r1-r10, 2026-08)
is a ten-round record of what read->transform->rewrite does to a store under
exactly those conditions. Nothing here rewrites a line, so nothing here can
lose one. A torn row costs one record, announced, via `read_jsonl_announced`.

**Append-only is not the same as atomic, and the first adversarial round on
this module was mostly that distinction.** Byte-level safety says no line is
overwritten; it says nothing about a decision made from a read that a later
write depends on. Two registrars reading the same rows both allocated `seq: 1`
— both lines on disk, one job invisible, because the executor is keyed by seq
— and two drainers both read "unclaimed" and both ran. Every state-dependent
write therefore goes through `_transact`: read, decide, and append under one
lock.

Ordering is by `seq`, and `seq` is assigned inside that transaction — learning
before maintenance, which is the order the drains have always run in
(promotions read the freshest lesson/skill stats).

**Contract for overlapping tails.** One tail process per handle_id: a drainer
appends a `claim` row and declines if a *live* claim (pid alive, this host)
already stands. Tails for DIFFERENT runs may overlap — they already do today,
because heartbeat runs skill maintenance on its own tick concurrently with
any run's tail, and every store those phases touch is lock-protected
(`file_lock`). Serializing across runs would be a new guarantee this codebase
has never had, not a preserved one.

**Stranding.** The Phase-1 watch-item was that `handle()`'s `_hid=None`
exception path could strand registered callables with no trace. A record is
durable, so a stranded tail is now *discoverable*: `find_stranded()` reports
runs with pending jobs and no live claim, and `sweep_stranded()` drains what
is safe to drain.

**Not everything is.** "Every job kind is idempotent" was the first draft's
claim and it was too broad: learning is idempotent by its own design (lesson
extraction skips rows that already carry lessons; crystallization re-checks
the verdict gate), but `run_post_run_maintenance` advances DURABLE cadence
counters, and threshold-based is not idempotent — a maintenance job whose
drain died after a tick would have that tick counted twice. So the sweep asks
`_resweep_safe` per kind, and a maintenance job whose drain already started is
surfaced under `needs_operator` rather than repeated. Surface, and let the
operator decide, is this project's standing posture for work it cannot prove
safe to repeat.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("maro.tail_jobs")

JOBS_FILENAME = "tail_jobs.jsonl"
TAIL_LOG_FILENAME = "tail.log"

# Job kinds, in the order they drain. The order is the contract the phases
# were built around: maintenance's promotions read this run's freshly
# crystallized skills and freshly extracted lessons, so learning goes first.
KIND_LEARNING = "learning"
KIND_MAINTENANCE = "maintenance"
_KIND_ORDER = {KIND_LEARNING: 0, KIND_MAINTENANCE: 1}

# The cost-attribution phase each kind reports under (metrics.tail_cost_scope).
_KIND_PHASE = {KIND_LEARNING: "learning", KIND_MAINTENANCE: "maintenance"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def jobs_path(handle_id: str) -> Optional[Path]:
    """The tail-job store for a run, or None when the run owns no run-dir.

    Deliberately does NOT create the run-dir: a handle that never opened one
    (dry runs, lanes that skip `open_run`) must not mint a phantom run here
    for the run index and the report writers to find. Callers treat None as
    "not recordable" and keep the tail inline.
    """
    if not handle_id:
        return None
    try:
        from runs import run_dir as _run_dir
        rd = _run_dir(str(handle_id))
    except Exception:
        return None
    if not rd.exists():
        return None
    return rd / "build" / JOBS_FILENAME


def _read_rows(path: Optional[Path]) -> Optional[List[Dict[str, Any]]]:
    """Every row in the store, or None when the store could not be READ.

    None and `[]` are different answers and the difference is load-bearing:
    an empty store means "no jobs yet", and every caller may proceed. An
    unreadable one means "this store's contents are unknown" — allocating a
    sequence against it would hide whatever it already holds, and declaring a
    run un-stranded from it would drop a tail. Every caller declines on None.
    (Per-ROW loss is a separate, milder thing: `read_jsonl_announced` costs
    one record and says so.)
    """
    if path is None:
        return None
    if not path.exists():
        return []
    try:
        from jsonl_utils import read_jsonl_announced
        return read_jsonl_announced(path, "tail_jobs")
    except Exception as exc:
        log.warning("tail_jobs: store unreadable (%s): %s", path, exc)
        return None


def _append(path: Path, row: Dict[str, Any]) -> bool:
    try:
        from file_lock import locked_append
        locked_append(path, json.dumps(row, ensure_ascii=False))
        return True
    except Exception as exc:
        log.warning("tail_jobs: append failed (%s): %s", path, exc)
        return False


def _transact(path: Optional[Path], decide):
    """Read the store, decide, and append — as ONE locked transaction.

    Append-only makes a store impossible to corrupt; it does NOT make a
    read-then-write decision atomic, and every state-dependent write here is
    one of those. Both defects that came out of the first adversarial round on
    this module were the same shape:

      * `_next_seq` read the rows, then appended outside the lock, so two
        registrars could allocate the SAME seq — both lines physically
        present, one job logically gone, because `state()` is keyed by seq
        (probed: two `seq: 1` job rows, `pending == ['maintenance']`);
      * `run_jobs` checked the standing claim, then appended its own, so two
        drainers could both see "unclaimed" and both run the same jobs.

    `decide(rows)` returns `(row_to_append_or_None, value)`. The lock is
    `file_lock.locked_write`, which is reentrant within a thread, so the inner
    `_append` does not deadlock on it. Returns `(appended, value)`;
    `(False, None)` when the store could not be read or the lock could not be
    taken — the caller then owns the work it was trying to hand over.
    """
    if path is None:
        return False, None
    try:
        from file_lock import locked_write
        # require=True: locked_write's environment fallback (lock file
        # uncreatable) yields UNLOCKED by long-standing contract — fine for
        # its other callers, fatal here, because an unlocked transaction is
        # the round-1 race wearing the fix's clothes (adversarial r2, Expert
        # QA HIGH). A transaction that cannot be exclusive declines instead.
        with locked_write(path, require=True):
            rows = _read_rows(path)
            if rows is None:
                return False, None
            row, value = decide(rows)
            if row is None:
                return False, value
            return _append(path, row), value
    except Exception as exc:
        log.warning("tail_jobs: store transaction failed (%s): %s", path, exc)
        return False, None


def _next_seq(rows: List[Dict[str, Any]]) -> int:
    seqs = [int(r.get("seq") or 0) for r in rows
            if r.get("event") == "job" and isinstance(r.get("seq"), int)]
    return (max(seqs) + 1) if seqs else 1


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def _adapter_identity(adapter) -> Dict[str, str]:
    """Enough of an adapter to rebuild an equivalent one in another process.

    The tail's LLM calls (lesson extraction, crystallization, promotion
    validation, evolver) have always run on the RUN's adapter. A child process
    inherits no objects, so the identity travels and the child rebuilds; if it
    cannot, it falls back to the ordinary auto-detected adapter rather than
    running no tail at all.
    """
    return {
        "backend": str(getattr(adapter, "backend", "") or ""),
        "model_key": str(getattr(adapter, "model_key", "") or ""),
    }


# The RUN's live adapter, by handle_id — an in-process fidelity cache, never
# the registry. Adversarial round 1 (3 of 4 seats): the record carries an
# adapter IDENTITY, which is all a child process can use, and the first cut
# made the in-process lane rebuild from that identity too. Phase 1's closures
# captured the adapter OBJECT — and `_handle_impl` builds its own when the
# caller passes none (`handle.py:1270`), so the object is not recoverable from
# `handle()`'s own scope. Rebuilding drops a FailoverAdapter's live fallback
# state, a caller-injected adapter, and any per-call configuration, in the
# lane that ships ON by default. So the object stays reachable where it still
# exists, and the identity is the fallback for where it cannot.
#
# This dict is safe where the phase-1 registries were not: the module-identity
# hazard came from handle.py being a `python -m handle` entry point, and this
# module is only ever imported by its canonical name. It is also not
# load-bearing — losing it costs fidelity, not work.
_LIVE_ADAPTERS: dict = {}


def _remember_adapter(handle_id: str, seq: int, adapter) -> None:
    # Keyed by (handle_id, seq), not handle_id (adversarial r2, 3 seats):
    # one key per handle meant every registration overwrote the last — so a
    # maintenance job recorded with the run's adapter and a post-escalation
    # learning job recorded with the ESCALATED one handed maintenance the
    # wrong adapter — and any subset drain forgot the adapter of jobs still
    # pending (the escalation lane drains learning early; maintenance paid).
    if adapter is not None and handle_id and seq:
        _LIVE_ADAPTERS[(str(handle_id), int(seq))] = adapter


def _forget_adapter(handle_id: str, seq: int) -> None:
    _LIVE_ADAPTERS.pop((str(handle_id), int(seq)), None)


def _forget_all_adapters(handle_id: str) -> None:
    """Drop every cached adapter for a handle — the spawn/empty handoffs.

    A detached child has its own module dict and can never consume the
    parent's objects, so a successful spawn that kept them was a leak in
    every long-lived caller (drain loops, daemons) — one adapter per handled
    run, forever (adversarial r2, 3 seats).
    """
    hid = str(handle_id)
    for key in [k for k in _LIVE_ADAPTERS if k[0] == hid]:
        _LIVE_ADAPTERS.pop(key, None)


def _step_rows(step_outcomes) -> List[Dict[str, Any]]:
    """Serialize StepOutcomes for the handoff.

    The persisted loop log (`build/loop-*-log.json`) keeps `result_length`,
    not `result` — full step text is not recoverable from it, and the per-step
    `.md` artifact is a rendered file with a synthesized header, not the
    field. The tail's step-lesson extraction and crystallization read
    `result`, so the handoff carries the outcomes themselves. This is the
    "explicit state handoff" half of moving the tail out of process: the
    parent writes what it holds instead of the child guessing it back.
    """
    rows: List[Dict[str, Any]] = []
    for s in (step_outcomes or []):
        try:
            rows.append(dataclasses.asdict(s))
        except Exception:
            # Not a dataclass (test doubles, future shapes) — carry the
            # fields the tail consumers actually read.
            rows.append({
                "index": getattr(s, "index", 0),
                "text": str(getattr(s, "text", "") or ""),
                "status": str(getattr(s, "status", "") or ""),
                "result": str(getattr(s, "result", "") or ""),
                "iteration": getattr(s, "iteration", 0),
            })
    return rows


def record_learning(
    handle_id: str,
    loop_result,
    *,
    adapter=None,
    project: str = "",
    dry_run: bool = False,
    verbose: bool = False,
    extra_loop_ids: Optional[List[str]] = None,
    skip_loop_ids: Optional[List[str]] = None,
) -> bool:
    """Record the deferred-learning job for a run. True when it is durable.

    A False return means the caller still owns the work — record_* never
    silently drops a phase: the tail may move in time, never vanish.
    """
    path = jobs_path(handle_id)
    if path is None:
        return False
    spec = {
        "loop_id": str(getattr(loop_result, "loop_id", "") or ""),
        "goal": str(getattr(loop_result, "goal", "") or ""),
        "project": str(project or getattr(loop_result, "project", "") or ""),
        "status": str(getattr(loop_result, "status", "") or ""),
        "had_no_matching_skill": bool(
            getattr(loop_result, "had_no_matching_skill", False)),
        "steps": _step_rows(getattr(loop_result, "steps", None)),
        "extra_loop_ids": [str(l) for l in (extra_loop_ids or []) if l],
        "skip_loop_ids": [str(l) for l in (skip_loop_ids or []) if l],
        "dry_run": bool(dry_run),
        "verbose": bool(verbose),
        "adapter": _adapter_identity(adapter),
    }
    def _decide(rows):
        seq = _next_seq(rows)
        return ({"event": "job", "seq": seq, "kind": KIND_LEARNING,
                 "recorded_at": _now(), "spec": spec}, seq)

    recorded, seq = _transact(path, _decide)
    if recorded:
        _remember_adapter(handle_id, seq, adapter)
    return recorded


def record_maintenance(
    handle_id: str,
    *,
    loop_id: str = "",
    adapter=None,
    verbose: bool = False,
) -> bool:
    """Record the post-run maintenance job for a run. True when it is durable."""
    path = jobs_path(handle_id)
    if path is None:
        return False
    spec = {
        "loop_id": str(loop_id or ""),
        "verbose": bool(verbose),
        "adapter": _adapter_identity(adapter),
    }

    def _decide(rows):
        seq = _next_seq(rows)
        return ({"event": "job", "seq": seq, "kind": KIND_MAINTENANCE,
                 "recorded_at": _now(), "spec": spec}, seq)

    recorded, seq = _transact(path, _decide)
    if recorded:
        _remember_adapter(handle_id, seq, adapter)
    return recorded


# ---------------------------------------------------------------------------
# Reading state
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Portable liveness check.

    `os.kill(pid, 0)` rather than a `/proc` read: this project runs on a
    headless Ubuntu box AND on a macOS dev machine, and the `/proc`-based
    checks written for the former were silently always-false on the latter
    (2026-07-08). EPERM means the pid exists and belongs to someone else.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _state_from_rows(rows: Optional[List[Dict[str, Any]]],
                     path: Optional[Path]) -> Dict[str, Any]:
    """The store's meaning, as a pure function of its rows.

    Split out from `state()` so the claim transaction can compute it from the
    rows it read UNDER the lock rather than from a second, later read.
    """
    if rows is None:
        return {"path": path, "rows": [], "pending": [], "done": set(),
                "started": set(), "failed": [], "refresh_failed": [],
                "claim": None, "unreadable": True}
    jobs: Dict[int, Dict[str, Any]] = {}
    done: set = set()
    started: set = set()
    failed_raw: List[Dict[str, Any]] = []
    refresh_failed: List[Dict[str, Any]] = []
    claim: Optional[Dict[str, Any]] = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        event = r.get("event")
        if event == "job":
            try:
                jobs[int(r.get("seq") or 0)] = r
            except (TypeError, ValueError):
                continue
        elif event == "started":
            # The per-job touch marker, appended before a runner is invoked.
            # This is the evidence `_resweep_safe` reads: round 2 (4/4
            # seats) showed the store-global "was there an unreleased
            # claim?" heuristic was laundered by the first partial recovery
            # sweep — its own claim+release became the newest claim, and the
            # NEXT sweep re-ran maintenance that had already ticked durable
            # counters. Per-job evidence has no global bit to launder.
            try:
                started.add(int(r.get("seq") or 0))
            except (TypeError, ValueError):
                continue
        elif event == "done":
            try:
                done.add(int(r.get("seq") or 0))
            except (TypeError, ValueError):
                continue
            # A job that RAISED is done — it must not be re-run, because its
            # first half already happened — but it is not finished work, and
            # the first cut left it visible nowhere: `find_stranded` reports
            # pending jobs, and a failed job is not pending (round 1).
            # `is not True` rather than `is False` (round 2, Expert QA): a
            # forged or schema-drifted `"ok": "false"` is a STRING, which
            # `is False` read as success — a done row this cannot read as
            # success is surfaced, not trusted.
            if r.get("ok") is not True:
                failed_raw.append(r)
        elif event == "refresh":
            # Recorded by round 1, read by nobody until round 2 (2 seats):
            # a durable event with no reader is not surfaced.
            if r.get("ok") is not True:
                refresh_failed.append(r)
        elif event == "claim":
            claim = r
    pending = [jobs[s] for s in sorted(jobs) if s not in done]
    pending.sort(key=lambda j: (_KIND_ORDER.get(str(j.get("kind")), 9),
                                int(j.get("seq") or 0)))
    # An orphan done row (no matching job) completes nothing and must not
    # fabricate a failure for a job that never existed.
    failed = [r for r in failed_raw
              if int(r.get("seq") or 0) in jobs]
    return {"path": path, "rows": rows, "pending": pending, "done": done,
            "started": started, "failed": failed,
            "refresh_failed": refresh_failed, "claim": claim,
            "unreadable": False}


def state(handle_id: str) -> Dict[str, Any]:
    """Pending jobs, failures, done seqs, and the standing claim for a run."""
    path = jobs_path(handle_id)
    return _state_from_rows(_read_rows(path), path)


def pending_jobs(handle_id: str) -> List[Dict[str, Any]]:
    """Jobs recorded for this run that have not been marked done."""
    return state(handle_id)["pending"]


def _live_claim(claim: Optional[Dict[str, Any]]) -> bool:
    """Is a standing claim held by a process that still exists here?

    Host-scoped on purpose: a pid from another machine says nothing about
    this one, so a claim stamped elsewhere is not treated as live. Runs and
    their tails share a filesystem only when they share a host.
    """
    if not isinstance(claim, dict):
        return False
    if claim.get("released_at"):
        return False
    try:
        host = str(claim.get("host") or "")
        if host and host != _hostname():
            return False
        return _is_pid_alive(int(claim.get("pid") or 0))
    except (TypeError, ValueError):
        return False


def _hostname() -> str:
    try:
        import socket
        return socket.gethostname()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _build_adapter(spec: Dict[str, Any]):
    """Rebuild the run's adapter in this process, or fall back honestly."""
    ident = (spec or {}).get("adapter") or {}
    backend = str(ident.get("backend") or "")
    model = str(ident.get("model_key") or "")
    try:
        from llm import build_adapter
    except Exception:
        return None
    # Exact identity first, then auto-detect with the same model, then the
    # ordinary default. A tail that runs on a neighbouring backend is worth
    # far more than a tail that does not run.
    attempts = []
    if backend and backend not in {"base", "failover"}:
        attempts.append({"backend": backend, "model": model} if model
                        else {"backend": backend})
    if model:
        attempts.append({"model": model})
    attempts.append({})
    for kwargs in attempts:
        try:
            return build_adapter(**kwargs)
        except Exception as exc:
            log.debug("tail_jobs: adapter build failed for %s: %s", kwargs, exc)
    return None


@dataclasses.dataclass
class _ReplayLoop:
    """The subset of LoopResult the tail's learning phase reads.

    Rehydrated from the recorded spec rather than reconstructed from the run
    dir: `finalize_deferred_learning` reads loop_id, goal, project, status,
    steps and had_no_matching_skill, and every one of them is carried.
    """
    loop_id: str
    project: str
    goal: str
    status: str
    steps: List[Any]
    had_no_matching_skill: bool = False


def _steps_from_rows(rows) -> List[Any]:
    try:
        from loop_types import StepOutcome
    except Exception:
        return []
    names = {f.name for f in dataclasses.fields(StepOutcome)}
    out = []
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        kwargs = {k: v for k, v in row.items() if k in names}
        # Required fields, defaulted so a schema-drifted row degrades to a
        # weaker step instead of dropping one: a missing step is invisible in
        # the learning that follows, and invisible is the failure mode this
        # project keeps paying for.
        kwargs.setdefault("index", 0)
        kwargs.setdefault("text", "")
        kwargs.setdefault("status", "")
        kwargs.setdefault("result", "")
        kwargs.setdefault("iteration", 0)
        try:
            out.append(StepOutcome(**kwargs))
        except Exception as exc:
            log.warning("tail_jobs: step row rejected by StepOutcome: %s", exc)
    return out


def _run_learning(spec: Dict[str, Any], adapter) -> None:
    from loop_finalize import finalize_deferred_learning
    loop = _ReplayLoop(
        loop_id=str(spec.get("loop_id") or ""),
        project=str(spec.get("project") or ""),
        goal=str(spec.get("goal") or ""),
        status=str(spec.get("status") or ""),
        steps=_steps_from_rows(spec.get("steps")),
        had_no_matching_skill=bool(spec.get("had_no_matching_skill")),
    )
    finalize_deferred_learning(
        loop,
        adapter=adapter,
        project=str(spec.get("project") or ""),
        dry_run=bool(spec.get("dry_run")),
        verbose=bool(spec.get("verbose")),
        extra_loop_ids=[str(l) for l in (spec.get("extra_loop_ids") or []) if l],
        skip_loop_ids=[str(l) for l in (spec.get("skip_loop_ids") or []) if l],
    )


def _run_maintenance(spec: Dict[str, Any], adapter) -> None:
    from loop_finalize import run_post_run_maintenance
    loop_id = str(spec.get("loop_id") or "")
    verbose = bool(spec.get("verbose"))
    if loop_id:
        # Re-enter the loop's captains-log scope: maintenance emitters
        # (SKILL_REWRITE, ...) attribute through the ambient loop id, and the
        # drain runs long after run_agent_loop's own scope exited (BACKLOG #17).
        from captains_log import loop_id_scope
        with loop_id_scope(loop_id):
            run_post_run_maintenance(adapter=adapter, verbose=verbose)
    else:
        run_post_run_maintenance(adapter=adapter, verbose=verbose)


_RUNNERS = {
    KIND_LEARNING: _run_learning,
    KIND_MAINTENANCE: _run_maintenance,
}


def _refresh_surfaces(handle_id: str, path: Optional[Path] = None) -> bool:
    """Re-derive the run's read surfaces after the tail wrote to them.

    The drains' events land AFTER close_run cut the captains-log slice and
    built the card, and the tail's cost rows land after the card's total was
    computed — so re-slice (same recorded offset, now reaching EOF past the
    tail), refresh the pure curators, and re-render. Mirrors
    audit_repair._refresh_surfaces; this is the same pass handle()'s finalize
    block ran inline, moved here so BOTH lanes (in-process and spawned) get
    it from one place rather than two that drift.

    Failure is recorded rather than swallowed at debug level. Every job is
    already marked done by the time this runs, so a silent failure leaves a
    store confidently saying the tail finished beside a card and a report that
    are stale — a record that lies, which is worse than no record
    (adversarial round 1, Expert QA).
    """
    try:
        from runs import slice_log_for_run, run_dir
        from run_curation import refresh_run_card_classification
        from loop_report import write_reports_for_run_dir
        slice_log_for_run(handle_id)
        refresh_run_card_classification(handle_id)
        write_reports_for_run_dir(run_dir(handle_id))
        return True
    except Exception as exc:
        log.warning("tail_jobs: surface refresh failed for %s — the card and "
                    "report may be stale: %s", handle_id, exc)
        if path is not None:
            _append(path, {"event": "refresh", "ok": False,
                           "error": f"{type(exc).__name__}: {exc}",
                           "finished_at": _now()})
        return False


def _run_one(job: Dict[str, Any], path: Path, handle_id: str, adapter,
             tail_cost_scope) -> str:
    """Run one job and record its outcome: "ok" | "failed" | "skipped".

    "skipped" means the runner was never invoked — nothing happened, so the
    caller's refresh decision must not count it as an attempt.

    Never raises, and the whole body means it now: round 2 (Expert QA HIGH)
    fed a row whose `spec` was a string — valid JSONL, wrong container — and
    `spec.get(...)` raised BEFORE the old try block, straight out of
    `run_jobs`, leaving the claim unreleased and a spawned child crashing on
    the same row forever. Decoding is inside the belt now, and a malformed
    job is recorded and retired instead of retried.
    """
    seq, kind = 0, ""
    try:
        try:
            seq = int(job.get("seq") or 0)
        except (TypeError, ValueError):
            seq = 0
        kind = str(job.get("kind") or "")
        runner = _RUNNERS.get(kind)
        spec = job.get("spec")
        problem = ""
        if runner is None:
            problem = f"unknown kind {kind!r}"
        elif not isinstance(spec, dict):
            problem = f"malformed spec ({type(spec).__name__}, not an object)"
        if problem:
            log.warning("tail_jobs: job seq %s not runnable — %s",
                        seq, problem)
            _append(path, {"event": "done", "seq": seq, "ok": False,
                           "error": problem, "finished_at": _now()})
            return "skipped"

        # The touch marker, BEFORE the runner: the per-job evidence
        # `_resweep_safe` reads (see _state_from_rows). If we cannot record
        # that a non-idempotent job is about to run, we do not run it — an
        # unprovable touch is exactly the state the marker exists to prevent.
        # Learning is idempotent by its own design, so it proceeds.
        if not _append(path, {"event": "started", "seq": seq,
                              "pid": os.getpid(), "ts": _now()}):
            if kind != KIND_LEARNING:
                log.error("tail_jobs: could not record the start of "
                          "non-idempotent %s job (seq %s) for %s — declining "
                          "rather than running unprovably", kind, seq,
                          handle_id)
                return "skipped"

        # The live adapter when this process still holds it (the inline
        # lane), the recorded identity when it does not (the spawned child,
        # the sweep). Per-job key: see _remember_adapter.
        job_adapter = (adapter if adapter is not None
                       else _LIVE_ADAPTERS.get((str(handle_id), seq))
                       or _build_adapter(spec))
        ok, err = True, ""
        try:
            with tail_cost_scope(str(spec.get("loop_id") or ""),
                                 _KIND_PHASE.get(kind, "tail")):
                runner(spec, job_adapter)
        except Exception as exc:   # noqa: BLE001 — the tail never raises out
            ok, err = False, f"{type(exc).__name__}: {exc}"
            log.warning("tail_jobs: %s job (seq %s) failed for %s: %s",
                        kind, seq, handle_id, exc)
        # Marked done either way, and the failure recorded next to it: a job
        # that raised already had its effect on whatever it touched before it
        # raised, and re-running it from a sweep would repeat that half.
        # `state()["failed"]` is where that record surfaces (round 1).
        if not _append(path, {"event": "done", "seq": seq, "ok": ok,
                              "error": err, "finished_at": _now()}):
            # The side effect happened and the store does not know. Loud,
            # because the next sweep sees this job pending and may run it
            # again.
            log.error("tail_jobs: %s job (seq %s) for %s RAN but its "
                      "completion could not be recorded — a later sweep will "
                      "see it as pending", kind, seq, handle_id)
        _forget_adapter(handle_id, seq)
        return "ok" if ok else "failed"
    except Exception as exc:   # noqa: BLE001 — belt for the belt
        log.error("tail_jobs: job seq %s handling itself failed for %s: %s",
                  seq, handle_id, exc)
        _append(path, {"event": "done", "seq": seq, "ok": False,
                       "error": f"{type(exc).__name__}: {exc}",
                       "finished_at": _now()})
        return "failed"


def run_jobs(
    handle_id: str,
    *,
    kinds: Optional[tuple] = None,
    adapter=None,
    refresh: bool = True,
    respect_claim: bool = True,
) -> int:
    """Drain this run's pending tail jobs in order. Returns the number run.

    Never raises: a tail that fails must not change the outcome of the run it
    belongs to, and by the time this runs the user already has the answer.

    kinds: restrict to a subset (the quality-gate escalation path drains only
    learning early — the escalated retry's decompose needs the lessons of the
    loop it is retrying, and nothing else about the tail is owed yet).
    adapter: use this adapter instead of rebuilding from the recorded
    identity — the in-process lane still holds the live one.
    respect_claim: decline when another live process holds the claim. The
    early-drain lane passes False: it is the parent, draining its own run
    before any child exists.
    """
    path = jobs_path(handle_id)
    if path is None:
        return 0
    wanted = set(kinds) if kinds else None

    # Claim acquisition is one transaction: the pending set is re-read, the
    # standing claim is checked, and this process's claim is appended, all
    # under the same lock. Check-then-append let two drainers both see
    # "unclaimed" and run the same jobs (adversarial round 1, 4/4 seats).
    def _claim(rows):
        st = _state_from_rows(rows, path)
        todo = [j for j in st["pending"]
                if wanted is None or str(j.get("kind")) in wanted]
        if not todo:
            return None, ([], st)
        if respect_claim and _live_claim(st["claim"]):
            log.info("tail_jobs: %s already claimed by pid %s — declining",
                     handle_id, (st["claim"] or {}).get("pid"))
            return None, ([], st)
        return ({"event": "claim", "pid": os.getpid(), "host": _hostname(),
                 "claimed_at": _now()}, (todo, st))

    claimed, value = _transact(path, _claim)
    pending, _st = value if value else ([], None)
    if not claimed:
        # A claim we could not publish is a claim we do not hold. Running
        # anyway is the unsafe direction for a store whose whole job is
        # telling a second drainer to stand down (adversarial round 1).
        if pending:
            log.warning("tail_jobs: could not publish a claim for %s — "
                        "declining rather than draining unclaimed", handle_id)
        return 0

    try:
        from metrics import tail_cost_scope
    except Exception:
        from contextlib import nullcontext

        def tail_cost_scope(*_a, **_k):  # type: ignore[misc]
            return nullcontext()

    # Pin the run for the duration. `runs.current_run_dir()` is a ContextVar
    # — process-local — and a spawned child inherits nothing, so without this
    # every run-scoped resolution in the tail silently falls back to
    # workspace-global. `runs.record_llm_call` NO-OPS when no run-dir is
    # active and record-mode is ON by default, so the spawned lane would have
    # stopped capturing the tail's LLM calls into `<run_dir>/build/calls/` —
    # and the run card's `n_calls`, which counts those files, would have
    # under-reported calls the run actually paid for. The codebase already
    # documents this hazard one lane over (`llm.py:1368`, the fetch tool's
    # capture dir). `scoped_run_dir` restores the prior value, which matters
    # for the heartbeat sweep: it drains ANOTHER run's tail inside its own
    # process.
    try:
        from runs import scoped_run_dir as _scoped_run, run_dir as _rd_for_pin
        _pin = _scoped_run(_rd_for_pin(str(handle_id)))
    except Exception:
        from contextlib import nullcontext
        _pin = nullcontext()

    ran = attempted = 0
    try:
        with _pin:
            for job in pending:
                outcome = _run_one(job, path, handle_id, adapter,
                                   tail_cost_scope)
                if outcome != "skipped":
                    attempted += 1
                if outcome == "ok":
                    ran += 1
            # Refresh on ATTEMPTED, not succeeded (round 2, Minimalist): a
            # learning job can make paid LLM calls, write lessons, and THEN
            # raise — "failed" is not "nothing happened", and the run-dir
            # pin exists precisely because those calls are captured. Only a
            # drain where no runner was ever invoked leaves the close_run
            # totals standing.
            if attempted and refresh:
                _refresh_surfaces(handle_id, path)
    finally:
        # The release is owed however the drain ended — round 2 (Expert QA)
        # showed a raising job used to leave the claim standing forever.
        if not _append(path, {"event": "claim", "pid": os.getpid(),
                              "host": _hostname(), "released_at": _now()}):
            log.error("tail_jobs: could not release the claim on %s — later "
                      "drains will decline while pid %s is alive",
                      handle_id, os.getpid())
    return ran


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------

_TRUE_TOKENS = {"1", "true", "yes", "on"}
_FALSE_TOKENS = {"0", "false", "no", "off", ""}


def _strict_bool(value, default: bool) -> bool:
    """A config flag's value, refusing to guess.

    `bool("false")` is True, and YAML hands back a string whenever the value
    was quoted — so plain truthiness turns `tail.spawn: "false"` into ON, in
    the one direction the OFF-by-default rollout exists to prevent
    (adversarial round 1, 3 seats, probed). A value this cannot read is a
    value nobody decided, so it takes the default and says so.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Only the two numbers that MEAN a boolean. `bool(nan)` is True and
        # YAML's `.nan`/`.inf` parse as floats, so plain numeric truthiness
        # turned malformed config into spawn-ON — the unsafe direction again
        # (round 2, 3 seats). 2, -1, inf: nobody decided those either.
        if value == 0:
            return False
        if value == 1:
            return True
        log.warning("tail_jobs: uninterpretable flag value %r — using %s",
                    value, default)
        return default
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    log.warning("tail_jobs: uninterpretable flag value %r — using %s",
                value, default)
    return default


def spawn_enabled() -> bool:
    """`tail.spawn` — ON by default since the 2026-08-21 flip (env wins).

    The flip decree (Jeremy, on burn-in evidence: three clean organic runs
    plus a deliberate mid-maintenance SIGKILL with full recovery semantics —
    docs/history/2026-08-20-async-tail-process-spawn.md). `MARO_TAIL_SPAWN`
    overrides config, same env-wins contract as `recording_enabled`: the
    test suite pins it "0" so unit tests never fork real children, and an
    operator can kill the lane the same way without touching config.
    """
    env = os.environ.get("MARO_TAIL_SPAWN")
    if env is not None:
        return _strict_bool(env, True)
    try:
        from config import get as _cfg_get
        return _strict_bool(_cfg_get("tail.spawn", True), True)
    except Exception:
        return True


def _cli_path() -> Path:
    return Path(__file__).resolve().parent / "cli.py"


def spawn(handle_id: str) -> Optional[int]:
    """Start a detached `finalize-tail` child for this run. Returns its pid.

    Returns None when the spawn could not be made — the caller then drains
    in-process, which is the pre-spawn behaviour and never worse than it.

    Three properties make this a real detachment rather than a background
    thread with extra steps:

      * `start_new_session=True` — the child leaves the parent's process
        group, so a Ctrl-C or a group kill aimed at the run does not take the
        tail with it;
      * stdout/stderr go to `<run_dir>/build/tail.log`, NOT to the parent's —
        an inherited pipe keeps `out=$(maro handle ...)` blocked until the
        LAST writer closes it, which would make the caller wait for the whole
        tail while believing it was asynchronous;
      * stdin is /dev/null, so nothing in the tail can block reading a
        terminal that has already moved on.
    """
    path = jobs_path(handle_id)
    if path is None:
        return None
    log_path = path.parent / TAIL_LOG_FILENAME
    cli = _cli_path()
    if not cli.exists():
        log.warning("tail_jobs: cannot spawn — %s missing", cli)
        return None
    env = dict(os.environ)
    src_dir = str(cli.parent)
    existing = env.get("PYTHONPATH", "")
    # Prepended unconditionally: a duplicate entry costs nothing, and the
    # child must find `src/` whether or not the parent was started with it.
    env["PYTHONPATH"] = (f"{src_dir}{os.pathsep}{existing}"
                         if existing else src_dir)
    try:
        with open(log_path, "a", encoding="utf-8") as out_fh:
            proc = subprocess.Popen(
                [sys.executable, str(cli), "finalize-tail",
                 "--handle-id", str(handle_id)],
                stdin=subprocess.DEVNULL,
                stdout=out_fh,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(cli.parent.parent),
                env=env,
            )
    except Exception as exc:
        log.warning("tail_jobs: spawn failed for %s: %s", handle_id, exc)
        return None
    _append(path, {"event": "spawn", "pid": proc.pid, "host": _hostname(),
                   "spawned_at": _now()})
    log.info("tail_jobs: spawned tail pid=%s for %s (log: %s)",
             proc.pid, handle_id, log_path)
    return proc.pid


def drain_or_spawn(handle_id: str, *, adapter=None) -> Dict[str, Any]:
    """The finalize seam: hand the tail to a child, or run it here.

    Returns {"mode": "spawned"|"inline"|"empty", "pid": int|None, "ran": int}.
    """
    if not pending_jobs(handle_id):
        # Someone else (a child, a sweep) already took the work — the cached
        # adapters have no consumer left in this process.
        _forget_all_adapters(handle_id)
        return {"mode": "empty", "pid": None, "ran": 0}
    if spawn_enabled():
        pid = spawn(handle_id)
        if pid is not None:
            # The child has its own module dict and can never consume the
            # parent's objects — keeping them was one adapter leaked per
            # handled run in every long-lived caller (round 2, 3 seats).
            _forget_all_adapters(handle_id)
            return {"mode": "spawned", "pid": pid, "ran": 0}
        log.info("tail_jobs: spawn unavailable — draining %s inline", handle_id)
    ran = run_jobs(handle_id, adapter=adapter)
    return {"mode": "inline", "pid": None, "ran": ran}


# ---------------------------------------------------------------------------
# Stranded-tail sweep
# ---------------------------------------------------------------------------

def _resweep_safe(kind: str, started: bool) -> bool:
    """May a sweep RUN this pending job, given whether ITS runner was invoked?

    "Every job kind is idempotent" was too broad, and round 1 caught it;
    round 2 caught the evidence being store-global. `started` is now THIS
    job's own durable marker (a `started` row appended before its runner —
    see _run_one), not a "was there ever an unreleased claim" heuristic:

      * the heuristic was LAUNDERED by recovery itself — the first partial
        sweep's own claim+release became the newest claim, so the second
        sweep read "never started" and re-ran maintenance that had already
        ticked durable cadence counters (round 2, 4/4 seats);
      * and it was store-global, so a crash AFTER learning but BEFORE
        maintenance stranded the untouched maintenance job forever — safe
        against duplication, failing the drainable half of the contract
        (round 2, Expert QA).

    Per-job evidence answers both: a maintenance job whose own runner was
    never invoked is safe to run; one whose runner started and never
    finished is surfaced instead — surface, and let the operator decide.
    Learning re-runs either way: `extract_deferred_lessons` skips rows that
    already carry lessons and crystallization re-checks the verdict gate.
    """
    if kind == KIND_LEARNING:
        return True
    return not started


def find_stranded(*, limit: int = 50,
                  min_age_s: float = 900.0) -> List[Dict[str, Any]]:
    """Runs whose tail jobs are pending with no live claim, oldest first.

    min_age_s keeps a tail that is merely young out of the report: a job
    recorded seconds ago belongs to a run whose child may not have started
    yet, and calling that stranded would make the sweep race the spawn.

    The scan walks candidates until `limit` STRANDED runs are found, and the
    candidate walk itself is UNBOUNDED on purpose. The first cut truncated to
    `limit * 4` newest-first and heartbeat's twelve healthiest runs hid an
    old abandoned tail forever (round 1); the fix added a `scan_cap=2000`
    that was the same starvation one magnitude up — a fixed oldest-first
    prefix that a store past position 2000 could never enter (round 2, 2
    seats). Both were magic-number enforcement where the standing decree is
    observational: the scan logs its size and walks everything. The real
    bound is the number of job stores on disk, which run pruning owns.
    """
    out: List[Dict[str, Any]] = []
    try:
        from runs import runs_root
        root = runs_root()
    except Exception:
        return out
    if not root.exists():
        return out
    try:
        candidates = sorted(root.glob(f"*/build/{JOBS_FILENAME}"),
                            key=lambda p: p.stat().st_mtime)
    except Exception:
        return out
    if len(candidates) > 500:
        log.info("tail_jobs: stranded scan walking %d job stores",
                 len(candidates))
    now = datetime.now(timezone.utc).timestamp()
    for p in candidates:
        if len(out) >= max(1, limit):
            break
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < min_age_s:
            continue
        handle_id = p.parent.parent.name.split("-", 1)[0]
        rows = _read_rows(p)
        if rows is None:      # unreadable != "nothing here"
            log.warning("tail_jobs: %s job store unreadable — not classified",
                        handle_id)
            continue
        st = _state_from_rows(rows, p)
        if _live_claim(st["claim"]):
            continue
        if not st["pending"] and not st["failed"] and not st["refresh_failed"]:
            continue
        drainable, needs_operator = [], []
        for j in st["pending"]:
            kind = str(j.get("kind"))
            job_started = int(j.get("seq") or 0) in st["started"]
            (drainable if _resweep_safe(kind, job_started)
             else needs_operator).append(kind)
        out.append({
            "handle_id": handle_id,
            "run_dir": str(p.parent.parent),
            "pending": [str(j.get("kind")) for j in st["pending"]],
            "drainable": drainable,
            "needs_operator": needs_operator,
            "failed": [str(f.get("error") or "") for f in st["failed"]],
            "refresh_failed": bool(st["refresh_failed"]),
            "age_s": round(age, 1),
        })
    return out


def sweep_stranded(*, limit: int = 10, min_age_s: float = 900.0,
                   dry_run: bool = False) -> Dict[str, Any]:
    """Drain what is safe to drain; surface the rest.

    Not "idempotent by construction" — see `_resweep_safe`, which now judges
    each job on its OWN started marker. A maintenance job whose runner was
    invoked and never finished is reported under `needs_operator` and left
    alone, because repeating a durable cadence tick is a real cost and this
    project's posture on work it cannot prove safe to repeat is to surface
    it. One whose runner was never reached is drained like anything else.
    """
    found = find_stranded(limit=limit, min_age_s=min_age_s)
    drained = 0
    for item in found:
        if dry_run or not item["drainable"]:
            continue
        try:
            drained += run_jobs(item["handle_id"],
                                kinds=tuple(item["drainable"]))
        except Exception as exc:
            log.warning("tail_jobs: sweep failed for %s: %s",
                        item["handle_id"], exc)
    return {
        "stranded": found,
        "drained": drained,
        "dry_run": bool(dry_run),
        "needs_operator": [i["handle_id"] for i in found
                           if i["needs_operator"]],
        "failed_jobs": [i["handle_id"] for i in found if i["failed"]],
        "refresh_failed": [i["handle_id"] for i in found
                           if i.get("refresh_failed")],
    }
