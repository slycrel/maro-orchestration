#!/usr/bin/env python3
"""Cross-box dispatch driver for the Hermes→Maro SSH lane (session protocol v0).

The substrate contract (`maro-enqueue --drain`) is synchronous — fine locally,
wrong over the wire: Hermes caps a tool call at 300s and a drain can run 30
minutes. This driver splits it: `enqueue` returns the job_id in seconds and
spawns a detached `worker` that drains exactly that job (the drain-once
contract, same claim→handle_task→complete steps as drain_task_store) and
records the job_id→handle_id join in a dispatch record — the mapping nothing
in core persists, and the only thing `result` needs to hand back a run_card.

Verbs (all print one JSON object to stdout):
  enqueue <goal text>   queue + spawn detached worker → {job_id, status}
  worker <job_id>       internal — the detached drain (never call over ssh)
  status <job_id>       dispatch record + run_card status when available
  result <job_id>       final run_card JSON, or {status: running|queued|...}
  list                  recent dispatch records, newest first
  ping                  liveness/auth check

Records: ~/.maro/workspace/output/hermes-dispatch/<job_id>.json (+ .log).
Security: this file assumes the SSH gate (maro-ssh-gate.sh) already
allowlisted the verb and validated id arguments. Goal text is untrusted by
definition (a goal IS code execution) — containment posture is Maro's job,
not this driver's.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

# Same workspace sanitization as deploy/openclaw/maro-dispatch.sh: any pinned
# workspace var (even MARO_WORKSPACE) flips memory routing into legacy
# prototype layouts; the clean no-env default is the only path to
# ~/.maro/workspace (split-brain seen live 2026-07-02).
for _var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT",
             "MARO_ORCH_ROOT", "MARO_MEMORY_DIR"):
    os.environ.pop(_var, None)

DISPATCH_DIR = Path.home() / ".maro" / "workspace" / "output" / "hermes-dispatch"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rec_path(job_id: str) -> Path:
    return DISPATCH_DIR / f"{job_id}.json"


def _write_rec(rec: dict) -> None:
    DISPATCH_DIR.mkdir(parents=True, exist_ok=True)
    path = _rec_path(rec["job_id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(path)


def _read_rec(job_id: str) -> dict | None:
    try:
        return json.loads(_rec_path(job_id).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _task_status(job_id: str) -> str | None:
    """Status straight from the task store (pre-worker states)."""
    try:
        from task_store import task_path, _read_task  # noqa: SLF001 — read-only peek
        task = _read_task(task_path(job_id))
        return task["status"] if task else None
    except Exception:
        return None


def _emit(obj: dict) -> int:
    print(json.dumps(obj, indent=1))
    return 0


def cmd_enqueue(goal: str) -> int:
    from handle_queue import enqueue_goal

    job_id = enqueue_goal(goal)
    rec = {
        "job_id": job_id,
        "goal": goal[:500],
        "status": "dispatched",
        "handle_id": None,
        "dispatched_at": _now(),
        "source": "hermes-ssh",
    }
    _write_rec(rec)

    log = (DISPATCH_DIR / f"{job_id}.log").open("a")
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "worker", job_id],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=str(REPO),
    )
    return _emit({"job_id": job_id, "status": "dispatched",
                  "poll": f"status {job_id}", "fetch": f"result {job_id}"})


def cmd_worker(job_id: str) -> int:
    """Detached drain for exactly one job — claim → handle_task → complete."""
    from task_store import claim, complete, fail as task_fail
    import handle_queue

    rec = _read_rec(job_id) or {"job_id": job_id, "source": "hermes-ssh"}
    rec.update(status="running", started_at=_now())
    _write_rec(rec)
    try:
        task = claim(job_id)
        res = handle_queue.handle_task(task)
        complete(job_id)
        rec.update(
            status=getattr(res, "status", "done") or "done",
            handle_id=getattr(res, "handle_id", "") or None,
            lane=getattr(res, "lane", None),
            ended_at=_now(),
        )
        # The HandleResult's result text is the only carrier of the "why" for
        # runs that never reach the loop (clarification question, guard
        # refusal, error detail) — dropping it left clarification_needed
        # records with no question on the far side (2026-07-16, cobalt-pine).
        _why = str(getattr(res, "result", "") or "")
        if _why:
            rec["result_excerpt"] = _why[:2000]
        _write_rec(rec)
        return 0
    except Exception as exc:  # detached — the record IS the error channel
        try:
            task_fail(job_id, str(exc))
        except Exception:
            pass
        rec.update(status="error", error=str(exc)[:500], ended_at=_now())
        _write_rec(rec)
        return 1


def _run_card(handle_id: str) -> dict | None:
    try:
        from runs import runs_root
        for d in runs_root().glob(f"{handle_id}*"):
            card = d / "run_card.json"
            if card.is_file():
                return json.loads(card.read_text())
    except Exception:
        pass
    return None


def cmd_status(job_id: str) -> int:
    rec = _read_rec(job_id)
    if rec is None:
        ts = _task_status(job_id)
        if ts is None:
            return _emit({"job_id": job_id, "status": "unknown",
                          "error": "no dispatch record or task found"})
        return _emit({"job_id": job_id, "status": ts})
    if rec.get("handle_id"):
        card = _run_card(rec["handle_id"]) or {}
        for key in ("nickname", "success_class", "goal_achieved",
                    "goal_verdict_summary", "goal_verdict_gaps",
                    "clarification_question", "result_excerpt",
                    "total_cost_usd"):
            if key in card:
                rec[key] = card[key]
    return _emit(rec)


def cmd_result(job_id: str) -> int:
    rec = _read_rec(job_id)
    if rec is None:
        return _emit({"job_id": job_id, "status": _task_status(job_id) or "unknown"})
    if rec.get("status") in ("dispatched", "running"):
        return _emit({"job_id": job_id, "status": rec["status"],
                      "note": "still in flight — poll status again later"})
    handle_id = rec.get("handle_id")
    card = _run_card(handle_id) if handle_id else None
    return _emit({"job_id": job_id, "dispatch": rec, "run_card": card})


def cmd_list() -> int:
    recs = []
    if DISPATCH_DIR.is_dir():
        for p in sorted(DISPATCH_DIR.glob("*.json"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:20]:
            try:
                r = json.loads(p.read_text())
                recs.append({k: r.get(k) for k in
                             ("job_id", "status", "handle_id", "goal",
                              "dispatched_at", "ended_at")})
            except (json.JSONDecodeError, OSError):
                continue
    return _emit({"dispatches": recs})


def main(argv: list[str]) -> int:
    if not argv:
        return _emit({"error": "usage: dispatch.py enqueue|status|result|list|ping ..."}) or 2
    verb, args = argv[0], argv[1:]
    if verb == "ping":
        return _emit({"status": "ok", "box": "maro", "time": _now()})
    if verb == "enqueue" and args:
        return cmd_enqueue(" ".join(args))
    if verb == "worker" and len(args) == 1:
        return cmd_worker(args[0])
    if verb == "status" and len(args) == 1:
        return cmd_status(args[0])
    if verb == "result" and len(args) == 1:
        return cmd_result(args[0])
    if verb == "list":
        return cmd_list()
    return _emit({"error": f"bad verb/args: {verb}"}) or 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
