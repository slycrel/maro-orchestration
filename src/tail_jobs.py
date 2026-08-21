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

Ordering is by `seq`, and `seq` is assigned at record time from the rows
already present — learning before maintenance, which is the order the drains
have always run in (promotions read the freshest lesson/skill stats).

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
runs with pending jobs and no live claim, and `sweep_stranded()` drains them.
Every job kind is idempotent by construction — lesson extraction skips rows
that already carry lessons, crystallization re-checks the verdict gate, and
maintenance is threshold/cadence-based — so re-running a job whose process
died mid-flight is safe.
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


def _read_rows(path: Optional[Path]) -> List[Dict[str, Any]]:
    if path is None or not path.exists():
        return []
    try:
        from jsonl_utils import read_jsonl_announced
        return read_jsonl_announced(path, "tail_jobs")
    except Exception as exc:
        log.warning("tail_jobs: store unreadable (%s): %s", path, exc)
        return []


def _append(path: Path, row: Dict[str, Any]) -> bool:
    try:
        from file_lock import locked_append
        locked_append(path, json.dumps(row, ensure_ascii=False))
        return True
    except Exception as exc:
        log.warning("tail_jobs: append failed (%s): %s", path, exc)
        return False


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
    rows = _read_rows(path)
    return _append(path, {
        "event": "job",
        "seq": _next_seq(rows),
        "kind": KIND_LEARNING,
        "recorded_at": _now(),
        "spec": spec,
    })


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
    rows = _read_rows(path)
    return _append(path, {
        "event": "job",
        "seq": _next_seq(rows),
        "kind": KIND_MAINTENANCE,
        "recorded_at": _now(),
        "spec": {
            "loop_id": str(loop_id or ""),
            "verbose": bool(verbose),
            "adapter": _adapter_identity(adapter),
        },
    })


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


def state(handle_id: str) -> Dict[str, Any]:
    """Pending jobs, done seqs, and the standing claim for a run."""
    path = jobs_path(handle_id)
    rows = _read_rows(path)
    jobs: Dict[int, Dict[str, Any]] = {}
    done: set = set()
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
        elif event == "done":
            try:
                done.add(int(r.get("seq") or 0))
            except (TypeError, ValueError):
                continue
        elif event == "claim":
            claim = r
    pending = [jobs[s] for s in sorted(jobs) if s not in done]
    pending.sort(key=lambda j: (_KIND_ORDER.get(str(j.get("kind")), 9),
                                int(j.get("seq") or 0)))
    return {"path": path, "rows": rows, "pending": pending,
            "done": done, "claim": claim}


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


def _refresh_surfaces(handle_id: str) -> None:
    """Re-derive the run's read surfaces after the tail wrote to them.

    The drains' events land AFTER close_run cut the captains-log slice and
    built the card, and the tail's cost rows land after the card's total was
    computed — so re-slice (same recorded offset, now reaching EOF past the
    tail), refresh the pure curators, and re-render. Mirrors
    audit_repair._refresh_surfaces; this is the same pass handle()'s finalize
    block ran inline, moved here so BOTH lanes (in-process and spawned) get
    it from one place rather than two that drift.
    """
    try:
        from runs import slice_log_for_run, run_dir
        from run_curation import refresh_run_card_classification
        from loop_report import write_reports_for_run_dir
        slice_log_for_run(handle_id)
        refresh_run_card_classification(handle_id)
        write_reports_for_run_dir(run_dir(handle_id))
    except Exception as exc:
        log.debug("tail_jobs: surface refresh failed (non-critical): %s", exc)


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
    st = state(handle_id)
    path = st["path"]
    if path is None:
        return 0
    pending = st["pending"]
    if kinds:
        wanted = set(kinds)
        pending = [j for j in pending if str(j.get("kind")) in wanted]
    if not pending:
        return 0
    if respect_claim and _live_claim(st["claim"]):
        log.info("tail_jobs: %s already claimed by pid %s — declining",
                 handle_id, (st["claim"] or {}).get("pid"))
        return 0
    _append(path, {"event": "claim", "pid": os.getpid(),
                   "host": _hostname(), "claimed_at": _now()})

    try:
        from metrics import tail_cost_scope
    except Exception:
        from contextlib import nullcontext

        def tail_cost_scope(*_a, **_k):  # type: ignore[misc]
            return nullcontext()

    ran = 0
    for job in pending:
        kind = str(job.get("kind") or "")
        runner = _RUNNERS.get(kind)
        seq = int(job.get("seq") or 0)
        if runner is None:
            log.warning("tail_jobs: unknown job kind %r (seq %s) — skipped",
                        kind, seq)
            _append(path, {"event": "done", "seq": seq, "ok": False,
                           "error": f"unknown kind {kind!r}",
                           "finished_at": _now()})
            continue
        spec = job.get("spec") or {}
        job_adapter = adapter if adapter is not None else _build_adapter(spec)
        ok, err = True, ""
        try:
            with tail_cost_scope(str(spec.get("loop_id") or ""),
                                 _KIND_PHASE.get(kind, "tail")):
                runner(spec, job_adapter)
            ran += 1
        except Exception as exc:   # noqa: BLE001 — the tail never raises out
            ok, err = False, f"{type(exc).__name__}: {exc}"
            log.warning("tail_jobs: %s job (seq %s) failed for %s: %s",
                        kind, seq, handle_id, exc)
        # Marked done either way, and the failure is recorded next to it: a
        # job that raised has already had its effect on whatever it touched
        # before it raised, and re-running it from a sweep would repeat that
        # half. The record is the trace; `find_stranded` reports the error.
        _append(path, {"event": "done", "seq": seq, "ok": ok,
                       "error": err, "finished_at": _now()})

    if ran and refresh:
        _refresh_surfaces(handle_id)
    _append(path, {"event": "claim", "pid": os.getpid(), "host": _hostname(),
                   "released_at": _now()})
    return ran


# ---------------------------------------------------------------------------
# Spawning
# ---------------------------------------------------------------------------

def spawn_enabled() -> bool:
    """`tail.spawn` — off by default (see docs; on the runtime box by config)."""
    try:
        from config import get as _cfg_get
        return bool(_cfg_get("tail.spawn", False))
    except Exception:
        return False


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
        return {"mode": "empty", "pid": None, "ran": 0}
    if spawn_enabled():
        pid = spawn(handle_id)
        if pid is not None:
            return {"mode": "spawned", "pid": pid, "ran": 0}
        log.info("tail_jobs: spawn unavailable — draining %s inline", handle_id)
    ran = run_jobs(handle_id, adapter=adapter)
    return {"mode": "inline", "pid": None, "ran": ran}


# ---------------------------------------------------------------------------
# Stranded-tail sweep
# ---------------------------------------------------------------------------

def find_stranded(*, limit: int = 50, min_age_s: float = 900.0) -> List[Dict[str, Any]]:
    """Runs whose tail jobs are pending with no live claim.

    min_age_s keeps a tail that is merely young out of the report: a job
    recorded seconds ago belongs to a run whose child may not have started
    yet, and calling that stranded would make the sweep race the spawn.
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
                            key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        return out
    now = datetime.now(timezone.utc).timestamp()
    for p in candidates[:max(1, limit) * 4]:
        if len(out) >= max(1, limit):
            break
        try:
            age = now - p.stat().st_mtime
        except OSError:
            continue
        if age < min_age_s:
            continue
        handle_id = p.parent.parent.name.split("-", 1)[0]
        st = state(handle_id)
        if not st["pending"] or _live_claim(st["claim"]):
            continue
        out.append({
            "handle_id": handle_id,
            "run_dir": str(p.parent.parent),
            "pending": [str(j.get("kind")) for j in st["pending"]],
            "age_s": round(age, 1),
        })
    return out


def sweep_stranded(*, limit: int = 10, min_age_s: float = 900.0,
                   dry_run: bool = False) -> Dict[str, Any]:
    """Drain stranded tails. Idempotent by construction (see module docstring)."""
    found = find_stranded(limit=limit, min_age_s=min_age_s)
    drained = 0
    for item in found:
        if dry_run:
            continue
        try:
            drained += run_jobs(item["handle_id"])
        except Exception as exc:
            log.warning("tail_jobs: sweep failed for %s: %s",
                        item["handle_id"], exc)
    return {"stranded": found, "drained": drained, "dry_run": bool(dry_run)}
