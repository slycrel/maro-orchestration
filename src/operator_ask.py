"""Operator questions — the rare exception, built as a lane (2026-09-06).

Jeremy (decision 1d1ad8b0): *"push things in the direction of 'maro answers
its own questions as much as possible'... having that capability sort of
prompts the simpler path of prompt-for-work, which is very easy to slip
into prompt-for-decision/judgement/permission from an LLM... we need to
build that out, and it should be the rare exception, not the norm."*

What this module makes true, end to end:

1. **A worker can ask — through a file, not a claim.** The execute frame
   (`instructions`) names `$MARO_ASK`, a path in the run scratch (the
   container's /tmp). A worker that cannot proceed without something only
   the operator has writes ONE JSON object there and ends its step. The
   frame says first to try a path that needs no input, and to record what
   it tried: the ask carries `no_input_alternative` and `tried` so the
   event is reviewable, not a reflex.
2. **The run pauses, typed.** `loop_execute` reads the file after the
   step (`read_ask`), stamps the run `awaiting-clarification` (the same
   pause the pre-run clarity gate uses) with the question, why, the
   alternative, a time box (`ask.timeout_hours`), and records the trace
   edge `step.ask → pause.awaiting-clarification` (`pause_for_ask`). The
   ask is a counted event: `maro asks` lists every one, pending, answered
   or expired.
3. **The question reaches the operator where they are.** One notify
   event, `operator_question` — escalation-class (durable
   output/escalations.jsonl, Telegram leg, Hermes inbox leg with
   announce) — carrying the question, the alternative and the exact
   answer verb.
4. **The answer resumes the run by handle.** `answer()` stamps the answer
   on the run and enqueues a `loop_continuation` task whose parent is the
   run: `handle_queue.handle_task` routes it as a same-identity RESUME
   (typed pause, no verdict — its strict-affirmative test) and the answer
   rides `ancestry_context_extra` into the next step. `maro answer`
   drains it inline; the Hermes gate's `answer` verb drains it detached.
   A pre-run clarification (`clarification_needed`, no loop yet) answers
   the same way: the resume runs the loop under the original identity.
5. **The time box expires, nothing is destroyed.** `sweep()` stamps
   `expired` on a pending ask past its deadline (data-retention decree:
   the run and its question stay; a late answer still resumes and is
   marked late).

Both engines share the frame wording and the file contract
(`go/internal/run/ask.go`).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Any, Dict, List, Optional

log = logging.getLogger("maro.operator_ask")

ASK_NAME = "ask-operator.json"
ASK_ENV = "MARO_ASK"
CONTAINER_ASK_PATH = "/tmp/" + ASK_NAME
EVENT_QUESTION = "operator_question"
EVENT_EXPIRED = "operator_question_expired"
META_KEY = "operator_ask"
DEFAULT_TIMEOUT_HOURS = 24.0
STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_EXPIRED = "expired"

_Q_CAP = 800
_TXT_CAP = 600


# ---------------------------------------------------------------------------
# The file contract (worker side)
# ---------------------------------------------------------------------------

def ask_path(scratch: Optional[str]) -> Optional[Path]:
    """Host-side path of the ask file for a run scratch dir (None → None)."""
    if not scratch:
        return None
    return Path(scratch) / ASK_NAME


def instructions(path) -> str:
    """The `## Asking the operator` paragraph of the execute frame. Same
    wording in the Go frame (`run.AskInstructions`)."""
    return (
        "## Asking the operator\n"
        "The owner is not present. Asking them is the rare exception, not a "
        "step: first try a path that needs no input from them, and prefer "
        "finishing with a stated gap over asking for a decision, a judgement "
        "or permission. Ask only when the goal cannot proceed without "
        "something only the operator has — a code sent to them, a choice "
        "that is theirs alone, a credential absent from the secrets store. "
        f"To ask, write ONE JSON object to {path} — "
        '{"question": "...", "why": "...", "no_input_alternative": "what you '
        'tried without them, or why none exists", "tried": true} — then end '
        "the step saying you asked. The run pauses until the answer arrives "
        "and resumes with the answer in your next step's context; the "
        "question is a counted, reviewed event."
    )


def read_ask(path) -> Optional[Dict[str, Any]]:
    """Parse and normalise a worker's ask file. None when absent or unusable
    (an unusable file is logged and left in place for the operator)."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ask file %s unreadable: %s", p, exc)
        return None
    if not isinstance(raw, dict):
        log.warning("ask file %s is not an object", p)
        return None
    q = str(raw.get("question") or "").strip()
    if not q:
        log.warning("ask file %s has no question", p)
        return None
    alt = raw.get("no_input_alternative")
    if alt is None:
        alt = raw.get("alternative")
    return {
        "question": q[:_Q_CAP],
        "why": str(raw.get("why") or "").strip()[:_TXT_CAP],
        "no_input_alternative": str(alt or "").strip()[:_TXT_CAP],
        "tried": bool(raw.get("tried", False)),
    }


def archive_ask(path) -> Optional[Path]:
    """Move a consumed ask file aside (never deleted — it is part of the
    run's record) so the next step cannot re-trigger the same pause."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = p.with_name(f"ask-operator.{stamp}.asked.json")
    try:
        os.replace(p, target)
        return target
    except OSError as exc:
        log.warning("ask file %s could not be archived: %s", p, exc)
        return None


# ---------------------------------------------------------------------------
# The pause (loop side)
# ---------------------------------------------------------------------------

def timeout_hours() -> float:
    try:
        from config import get as _get
        v = float(_get("ask.timeout_hours", DEFAULT_TIMEOUT_HOURS))
        return v if v > 0 else DEFAULT_TIMEOUT_HOURS
    except Exception:
        return DEFAULT_TIMEOUT_HOURS


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def deadline_for(asked_at: datetime) -> str:
    return _iso(asked_at + timedelta(hours=timeout_hours()))


def answer_command(handle_id: str) -> str:
    return f'maro answer {handle_id} "<your answer>"'


def pause_for_ask(ask: Dict[str, Any], *, handle_id: str, goal: str,
                  step: str = "", loop_id: Optional[str] = None) -> Dict[str, Any]:
    """Record a worker's question on the run and tell the operator.

    Stamps `operator_ask` (+ `clarification_question`, `pause_reason`) on
    the run metadata, records the trace edge, emits the
    `operator_question` notify event. Returns the stamped record. Never
    raises — the loop's pause must not depend on the notify path.
    """
    from stop_verdicts import PAUSE_OP_CLARIFICATION
    now = datetime.now(timezone.utc)
    record = {
        "question": ask.get("question", ""),
        "why": ask.get("why", ""),
        "no_input_alternative": ask.get("no_input_alternative", ""),
        "tried": bool(ask.get("tried", False)),
        "step": (step or "")[:300],
        "asked_at": _iso(now),
        "deadline": deadline_for(now),
        "status": STATUS_PENDING,
        "source": "worker",
    }
    try:
        from runs import stamp_run_metadata
        stamp_run_metadata({
            META_KEY: record,
            "clarification_question": record["question"],
            "pause_reason": PAUSE_OP_CLARIFICATION,
        })
    except Exception as exc:
        log.warning("ask: run metadata stamp failed: %s", exc)
    try:
        from run_trace import record_edge
        record_edge("step.ask", "pause." + PAUSE_OP_CLARIFICATION,
                    loop_id=loop_id, handle_id=handle_id or None,
                    question=record["question"][:200],
                    tried=record["tried"])
    except Exception:
        pass
    try:
        from notify import emit
        emit(EVENT_QUESTION, {
            "handle_id": handle_id,
            "goal": goal,
            "question": record["question"],
            "why": record["why"],
            "no_input_alternative": record["no_input_alternative"],
            "tried": record["tried"],
            "step": record["step"],
            "deadline": record["deadline"],
            "answer_with": answer_command(handle_id),
        })
    except Exception as exc:
        log.warning("ask: notify failed: %s", exc)
    return record


# ---------------------------------------------------------------------------
# The answer (operator side)
# ---------------------------------------------------------------------------

def _run_dir(ref: str) -> Optional[Path]:
    try:
        from runs import resolve_run_dir
        return resolve_run_dir(ref)
    except Exception:
        return None


def _read_meta(rd: Path) -> Dict[str, Any]:
    try:
        return json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def pending(ref: str) -> Optional[Dict[str, Any]]:
    """The run's question record when one is waiting; else None."""
    rd = _run_dir(ref)
    if rd is None:
        return None
    meta = _read_meta(rd)
    rec = meta.get(META_KEY)
    if isinstance(rec, dict) and rec.get("status") == STATUS_PENDING:
        return rec
    return None


def _continuation_reason(goal: str, question: str, text: str) -> str:
    return (
        f"CONTINUATION of: {goal}\n\n"
        f"== Operator answer ==\n"
        f"The run paused to ask the operator: {question}\n"
        f"The operator answered: {text}\n"
        f"Continue from where the run paused, using this answer; do not ask "
        f"it again.\n"
        f"== End operator answer =="
    )


_RESUME_FAILED = ("refused_busy", "error", "failed")


def _stamp_key(s: str) -> str:
    """ISO stamps from the two writers compared as one shape (Z / +00:00)."""
    return str(s or "").replace("+00:00", "Z")


def _last_resume_failed(handle_id: str, since: str = "",
                        job_ids: Sequence[str] = ()) -> Optional[str]:
    """A failure status when EVERY resume of the CURRENT question ended
    without running the loop (refused_busy / error / failed) and none is
    still queued — else None. An answer whose resume was refused is an
    answer that never arrived; the next `answer` (same text or new) must
    be allowed to re-drive it. A resume that ran, or one still pending,
    closes the door.

    The current question's resumes are the tasks whose ids the record
    stamped (`resume_job_ids`); a record from before that stamp existed
    falls back to every resume queued since the answer time. Resumes of
    an earlier question of the same run (a run may ask more than once)
    do not count either way."""
    try:
        from task_store import list_tasks
        tasks = [t for t in list_tasks()
                 if str(t.get("source") or "") == "loop_continuation"
                 and str((t.get("origin") or {}).get("parent_handle_id") or "") == handle_id]
        if job_ids:
            wanted = set(job_ids)
            tasks = [t for t in tasks if str(t.get("job_id") or "") in wanted]
        else:
            tasks = [t for t in tasks
                     if _stamp_key((t.get("timestamps") or {}).get("queued_at_utc") or "")
                     >= _stamp_key(since)]
    except Exception:
        return None
    if not tasks:
        return None
    statuses = [str(t.get("result_status") or t.get("status") or "") for t in tasks]
    if all(st in _RESUME_FAILED for st in statuses):
        return statuses[-1]
    return None


def answer(ref: str, text: str, *, source: str = "cli") -> Dict[str, Any]:
    """Stamp the answer on the run and enqueue its resume.

    Returns {"handle_id", "job_id", "late", "status"}; "status" is
    "queued" or "error" (with "error" text). The task is a
    `loop_continuation` whose origin names the run as parent — the queue's
    strict-affirmative resume test routes it as the same run.

    An already-answered run is refused UNLESS its last resume never ran
    (refused_busy / error): then this answer re-drives it — with the new
    text, or with the recorded answer when `text` is empty.
    """
    text = (text or "").strip()
    rd = _run_dir(ref)
    if rd is None:
        return {"status": "error", "error": f"no run found for {ref!r}"}
    meta = _read_meta(rd)
    handle_id = str(meta.get("handle_id") or rd.name.split("-", 1)[0])
    goal = str(meta.get("prompt") or meta.get("goal") or "").strip()
    rec = meta.get(META_KEY) if isinstance(meta.get(META_KEY), dict) else None
    question = ""
    if rec:
        question = str(rec.get("question") or "")
    elif meta.get("clarification_question"):
        question = str(meta.get("clarification_question"))
        rec = {"question": question, "asked_at": str(meta.get("created_at") or ""),
               "deadline": "", "status": STATUS_PENDING, "source": "clarity-gate"}
    if not rec:
        return {"status": "error",
                "error": f"run {handle_id} has no operator question to answer"}
    retried = None
    if rec.get("status") == STATUS_ANSWERED:
        retried = _last_resume_failed(handle_id, since=str(rec.get("answered_at") or ""),
                                      job_ids=[str(j) for j in (rec.get("resume_job_ids") or [])])
        if not retried:
            return {"status": "error",
                    "error": f"run {handle_id} was already answered at {rec.get('answered_at')}"}
        if not text:
            text = str(rec.get("answer") or "").strip()
        log.info("answer: re-driving %s — the previous resume ended %s", handle_id, retried)
    if not text:
        return {"status": "error", "error": "empty answer"}
    if meta.get("goal_verdict_source"):
        return {"status": "error",
                "error": f"run {handle_id} already reached a verdict; dispatch a new goal instead"}
    now = datetime.now(timezone.utc)
    late = False
    dl = str(rec.get("deadline") or "")
    if dl:
        try:
            late = now > datetime.fromisoformat(dl)
        except ValueError:
            late = False
    rec = dict(rec)
    rec.update({"status": STATUS_ANSWERED, "answer": text[:2000],
                "answered_at": _iso(now), "answer_source": source,
                "late": late})
    try:
        from runs import stamp_run_metadata_for
        stamp_run_metadata_for(handle_id, {
            META_KEY: rec,
            "clarification_answer": text[:2000],
        })
    except Exception as exc:
        log.warning("answer: metadata stamp failed: %s", exc)
    try:
        from task_store import enqueue
        from ancestry import Origin
        origin = Origin(source="operator_answer", parent_handle_id=handle_id,
                        parent_goal=goal[:500])
        if meta.get("measurement_class"):
            origin["measurement_class"] = str(meta["measurement_class"])
        task = enqueue(
            lane="agenda",
            source="loop_continuation",
            reason=_continuation_reason(goal, question, text),
            continuation_depth=1,
            origin=origin,
        )
    except Exception as exc:
        return {"status": "error", "handle_id": handle_id,
                "error": f"could not enqueue the resume: {exc}"}
    try:
        from runs import stamp_run_metadata_for
        ids = [str(j) for j in (rec.get("resume_job_ids") or []) if str(j)]
        ids.append(str(task.get("job_id") or ""))
        rec["resume_job_ids"] = ids
        stamp_run_metadata_for(handle_id, {META_KEY: rec})
    except Exception as exc:
        log.warning("answer: resume id stamp failed: %s", exc)
    try:
        from run_trace import record_edge
        record_edge("pause.awaiting-clarification", "answer.queued",
                    handle_id=handle_id, source=source, late=late)
    except Exception:
        pass
    out = {"status": "queued", "handle_id": handle_id,
           "job_id": str(task.get("job_id") or ""), "late": late,
           "question": question}
    if retried:
        out["retried_after"] = retried
    return out


def drain(job_id: str) -> Any:
    """Run a queued answer inline: claim → handle_task → complete. Returns
    the HandleResult (or raises). The Hermes gate uses the detached
    dispatch worker instead; this is the CLI's synchronous path."""
    from task_store import claim, complete, fail as task_fail
    import handle_queue
    task = claim(job_id)   # raises when missing, claimed elsewhere, or not queued
    try:
        res = handle_queue.handle_task(task)
    except Exception as exc:
        try:
            task_fail(job_id, str(exc))
        except Exception:
            pass
        raise
    status = getattr(res, "status", "done") or "done"
    if status == "error":
        task_fail(job_id, str(getattr(res, "result", "") or status)[:500])
    else:
        complete(job_id, result_status=status)
    return res


# ---------------------------------------------------------------------------
# The ledger: every ask, counted (operator side)
# ---------------------------------------------------------------------------

def list_asks(limit: int = 50) -> List[Dict[str, Any]]:
    """Every run that ever asked, newest first: handle, status, question,
    asked/answered/deadline. Reads run metadata only."""
    rows: List[Dict[str, Any]] = []
    try:
        from runs import runs_root
        root = runs_root()
    except Exception:
        return rows
    if not root.is_dir():
        return rows
    for rd in root.iterdir():
        mp = rd / "metadata.json"
        if not mp.is_file():
            continue
        meta = _read_meta(rd)
        rec = meta.get(META_KEY)
        if not isinstance(rec, dict):
            continue
        rows.append({
            "handle_id": str(meta.get("handle_id") or rd.name.split("-", 1)[0]),
            "status": str(rec.get("status") or ""),
            "question": str(rec.get("question") or ""),
            "asked_at": str(rec.get("asked_at") or ""),
            "deadline": str(rec.get("deadline") or ""),
            "answered_at": str(rec.get("answered_at") or ""),
            "late": bool(rec.get("late", False)),
            "tried": bool(rec.get("tried", False)),
            "goal": str(meta.get("prompt") or "")[:120],
        })
    rows.sort(key=lambda r: r["asked_at"], reverse=True)
    return rows[:limit]


def sweep(now: Optional[datetime] = None) -> List[str]:
    """Stamp `expired` on pending asks past their deadline; returns the
    handles expired this pass. Nothing is deleted; a late answer still
    resumes the run (marked late)."""
    now = now or datetime.now(timezone.utc)
    expired: List[str] = []
    for row in list_asks(limit=10_000):
        if row["status"] != STATUS_PENDING or not row["deadline"]:
            continue
        try:
            if now <= datetime.fromisoformat(row["deadline"]):
                continue
        except ValueError:
            continue
        rd = _run_dir(row["handle_id"])
        if rd is None:
            continue
        meta = _read_meta(rd)
        rec = dict(meta.get(META_KEY) or {})
        rec.update({"status": STATUS_EXPIRED, "expired_at": _iso(now)})
        try:
            from runs import stamp_run_metadata_for
            stamp_run_metadata_for(row["handle_id"], {META_KEY: rec})
        except Exception as exc:
            log.warning("sweep: stamp failed for %s: %s", row["handle_id"], exc)
            continue
        expired.append(row["handle_id"])
        try:
            from notify import emit
            emit(EVENT_EXPIRED, {
                "handle_id": row["handle_id"],
                "goal": row["goal"],
                "question": row["question"],
                "deadline": row["deadline"],
                "answer_with": answer_command(row["handle_id"]),
            })
        except Exception:
            pass
    return expired


def render_asks(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "no operator questions recorded"
    out = []
    for r in rows:
        mark = {"pending": "?", "answered": "✓", "expired": "×"}.get(r["status"], "·")
        line = f"{mark} {r['handle_id']}  {r['status']:<8} {r['asked_at']}  {r['question'][:100]}"
        if r["status"] == STATUS_PENDING and r["deadline"]:
            line += f"  (until {r['deadline']})"
        if r["late"]:
            line += "  [late]"
        out.append(line)
    return "\n".join(out)
