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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence, Any, Dict, List, Optional

log = logging.getLogger("maro.operator_ask")

ASK_NAME = "ask-operator.json"
ASK_ENV = "MARO_ASK"
CONTAINER_ASK_PATH = "/tmp/" + ASK_NAME
# Live (in-step) ask, 2026-09-07: the worker keeps running and polls this
# file for the operator's reply. A 2FA code is consumed by the session that
# requested it, so the browser has to outlive the question (run 084d3c1f
# asked through the pause lane and the code would have arrived at a dead
# session). The engine's poll loop announces the ask mid-step
# (`watch_live`) and `answer()` drops the reply here instead of enqueueing
# a resume.
ANSWER_NAME = "ask-answer.json"
ANSWER_ENV = "MARO_ASK_ANSWER"
CONTAINER_ANSWER_PATH = "/tmp/" + ANSWER_NAME
LIVE_MARK = ".ask-live.json"      # scratch marker: the live ask was announced
MAX_BOUNCES_PER_STEP = 1          # grounding: re-runs of a step whose ask failed the probes
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


def answer_path(scratch: Optional[str]) -> Optional[Path]:
    """Where a live ask's reply lands (host side). None without a scratch."""
    if not scratch:
        return None
    return Path(scratch) / ANSWER_NAME


def live_wait_s() -> float:
    """How long a live ask waits for the operator (`ask.live_wait_s`)."""
    try:
        from config import get as _cfg
        return float(_cfg("ask.live_wait_s", 600))
    except Exception:
        return 600.0


def instructions(path, answer_path_: Optional[str] = None) -> str:
    """The `## Asking the operator` paragraph of the execute frame. Same
    wording in the Go frame (`run.AskInstructions`)."""
    mins = max(1, int(live_wait_s() // 60))
    live = ""
    if answer_path_:
        live = (
            " A code sent to the operator is consumed by the session that asked "
            "for it, so ask for one LIVE instead of ending the step: add "
            '"live": true, keep your process (and the browser) running, and '
            f"poll {answer_path_} every few seconds for up to {mins} minutes, "
            "printing a line every 30 s so the step is not judged stalled. It "
            'arrives as {"answer": "..."}; a {"bounce": "..."} means the ask '
            "failed a check — fix it and write it again. If the window closes, "
            "end the step stating the gap."
        )
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
        "question is a counted, reviewed event. The engine checks a question "
        "before the operator sees it: every link in it must resolve (a dead "
        "link comes back to you, not to them), and a request for a code must "
        'say in "sent" how YOU triggered its delivery (choose the SMS or '
        "authenticator option first) and what confirmation you saw."
        + live
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
        "live": bool(raw.get("live", False)),
        "sent": str(raw.get("sent") or "").strip()[:_TXT_CAP],
    }


# ---------------------------------------------------------------------------
# Grounding — an ask is a claim; probe it before the operator sees it
# ---------------------------------------------------------------------------
# 2026-09-07, run 084d3c1f's fourth question: "Open https://account.yahoo.com/
# security and provide the 6-digit code" — the link 404'd and no code had
# been sent (the worker stopped on Yahoo's method-chooser page without
# choosing). Jeremy: the verification step on a step's output should have
# caught this. Two deterministic probes, run on the host before the card
# goes out; a failing ask is bounced to the worker (one re-run), then
# passed through marked unverified — honest over blocking.

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_CODE_RE = re.compile(
    r"\b(2fa|otp|one[- ]time|verification code|6[- ]digit|passcode|security code|"
    r"code (?:sent|from|that was sent|yahoo|google|apple)|backup code|"
    r"authenticat\w+ code)\b", re.I)


def _probe_url(url: str, timeout: float = 8.0) -> str:
    """'' when the link answers < 400; otherwise a short reason."""
    import urllib.request
    import urllib.error
    last = ""
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(url, method=method, headers={
            "User-Agent": "Mozilla/5.0 (maro ask-grounding)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if int(getattr(resp, "status", 200) or 200) < 400:
                    return ""
                last = f"HTTP {resp.status}"
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (403, 405) and method == "HEAD":
                continue
            if exc.code >= 400:
                return f"returns {last}"
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", None) or exc
            from context_budget import clip as _clip
            return f"could not be reached ({_clip(str(reason), 80)})"
    return f"returns {last}" if last else ""


_PROBE_URL = _probe_url  # test seam


def _links(ask: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for field in ("question", "why", "no_input_alternative", "sent"):
        for url in _URL_RE.findall(str(ask.get(field) or "")):
            url = url.rstrip(".,;:!?")
            if url not in out:
                out.append(url)
    return out


def asks_for_code(ask: Dict[str, Any]) -> bool:
    q = str(ask.get("question") or "")
    return bool(_CODE_RE.search(q)) and "code" in q.lower()


def ground(ask: Dict[str, Any]) -> List[str]:
    """Problems that must reach the WORKER, not the operator. Empty = pass."""
    problems: List[str] = []
    for url in _links(ask)[:5]:
        why = _PROBE_URL(url)
        if why:
            problems.append(
                f"the link {url} {why} — a question must not send the operator to a "
                "page that does not exist; fix the link or drop it")
    if asks_for_code(ask):
        if not str(ask.get("sent") or "").strip():
            problems.append(
                "you ask for a code but do not say how it was sent: trigger the delivery "
                "yourself first (choose the SMS or authenticator option on the challenge "
                'page), then put the confirmation you saw in "sent" and ask again')
        if not ask.get("live"):
            problems.append(
                "a code is consumed by the session that requested it and this ask ends the "
                'step: ask LIVE — set "live": true, keep the browser open and poll the '
                "answer file (see the frame)")
    return problems


def unique_archive(target: Path) -> Path:
    """A second archive in the same second must not overwrite the first
    (grounding bounce + re-ask, 2026-09-07: the file is part of the run's
    record and "never deleted" includes "never clobbered")."""
    if not target.exists():
        return target
    stem, suffixes = target.name.split(".", 1)[0], target.name.split(".", 1)[1]
    for i in range(1, 1000):
        cand = target.with_name(f"{stem}.{suffixes.replace('.', f'-{i}.', 1)}")
        if not cand.exists():
            return cand
    return target


def archive_ask(path) -> Optional[Path]:
    """Move a consumed ask file aside (never deleted — it is part of the
    run's record) so the next step cannot re-trigger the same pause."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = unique_archive(p.with_name(f"ask-operator.{stamp}.asked.json"))
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


def _record(ask: Dict[str, Any], *, step: str, now: datetime, live: bool,
            unverified: Sequence[str] = ()) -> Dict[str, Any]:
    if live:
        deadline = _iso(now + timedelta(seconds=live_wait_s()))
    else:
        deadline = deadline_for(now)
    rec = {
        "question": ask.get("question", ""),
        "why": ask.get("why", ""),
        "no_input_alternative": ask.get("no_input_alternative", ""),
        "tried": bool(ask.get("tried", False)),
        "sent": str(ask.get("sent") or ""),
        "live": bool(live),
        "step": (step or "")[:300],
        "asked_at": _iso(now),
        "deadline": deadline,
        "status": STATUS_PENDING,
        "source": "worker",
    }
    if unverified:
        rec["unverified"] = [str(x)[:300] for x in unverified]
    return rec


def _notify(record: Dict[str, Any], *, handle_id: str, goal: str) -> None:
    try:
        from notify import emit
        payload = {
            "handle_id": handle_id,
            "goal": goal,
            "question": record["question"],
            "why": record["why"],
            "no_input_alternative": record["no_input_alternative"],
            "tried": record["tried"],
            "step": record["step"],
            "deadline": record["deadline"],
            "answer_with": answer_command(handle_id),
        }
        if record.get("live"):
            payload["live"] = True
            payload["wait_s"] = int(live_wait_s())
        if record.get("sent"):
            payload["sent"] = record["sent"]
        if record.get("unverified"):
            payload["unverified"] = list(record["unverified"])
        emit(EVENT_QUESTION, payload)
    except Exception as exc:
        log.warning("ask: notify failed: %s", exc)


def pause_for_ask(ask: Dict[str, Any], *, handle_id: str, goal: str,
                  step: str = "", loop_id: Optional[str] = None,
                  unverified: Sequence[str] = (),
                  record: Optional[Dict[str, Any]] = None,
                  notify: bool = True) -> Dict[str, Any]:
    """Record a worker's question on the run and tell the operator.

    Stamps `operator_ask` (+ `clarification_question`, `pause_reason`) on
    the run metadata, records the trace edge, emits the
    `operator_question` notify event. Returns the stamped record. Never
    raises — the loop's pause must not depend on the notify path.

    `unverified` lists grounding problems the worker did not fix after its
    bounce; they ride the record and the card so the operator knows what
    was not checked. `record` re-uses a live ask's record when its window
    closed with the step (the pause replaces the live wait; `notify=False`
    keeps the operator from getting a second card for the same question).
    """
    from stop_verdicts import PAUSE_OP_CLARIFICATION
    now = datetime.now(timezone.utc)
    if record is None:
        record = _record(ask, step=step, now=now, live=False, unverified=unverified)
    else:
        record = dict(record)
        record.update({"live": False, "deadline": deadline_for(now),
                       "status": STATUS_PENDING, "live_window_closed_at": _iso(now)})
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
                    tried=record["tried"], unverified=len(record.get("unverified") or []))
    except Exception:
        pass
    if notify:
        _notify(record, handle_id=handle_id, goal=goal)
    return record


# ---------------------------------------------------------------------------
# Live ask — announced mid-step by the engine's poll loop
# ---------------------------------------------------------------------------

def _mark_path(scratch) -> Path:
    return Path(scratch) / LIVE_MARK


def _read_mark(scratch) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(_mark_path(scratch).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_answer_file(scratch, payload: Dict[str, Any]) -> Optional[Path]:
    ap = answer_path(str(scratch))
    if ap is None:
        return None
    tmp = ap.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, ap)
    return ap


def watch_live(scratch, *, handle_id: str = "", goal: str = "", step: str = "",
               loop_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Called from the step's poll loop every tick. Announces a live ask
    once (record + trace + card), bounces one that fails grounding straight
    back through the answer file, and returns the pending record (with
    `remaining_s`) while the worker is still waiting. None otherwise.
    Never raises."""
    try:
        p = ask_path(str(scratch) if scratch else None)
        if p is None or not p.is_file():
            return None
        mark = _read_mark(scratch)
        if mark is not None:
            if mark.get("status") != STATUS_PENDING:
                return None
            ap = answer_path(str(scratch))
            if ap is not None and ap.is_file():
                return None
            try:
                dl = datetime.fromisoformat(str(mark.get("deadline")))
                remaining = (dl - datetime.now(timezone.utc)).total_seconds()
            except ValueError:
                remaining = 0.0
            if remaining <= 0:
                return None
            return {**mark, "remaining_s": remaining}
        ask = read_ask(p)
        if not ask or not ask.get("live"):
            return None
        problems = ground(ask)
        now = datetime.now(timezone.utc)
        if problems:
            archive_ask(p)
            _write_answer_file(scratch, {"answer": "", "bounce": "\n".join(problems),
                                         "bounced_at": _iso(now)})
            log.warning("live ask bounced to the worker: %s", "; ".join(problems)[:200])
            try:
                from run_trace import record_edge
                record_edge("step.ask", "ask.bounced", loop_id=loop_id,
                            handle_id=handle_id or None, problems=len(problems))
            except Exception:
                pass
            return None
        record = _record(ask, step=step, now=now, live=True)
        try:
            from runs import stamp_run_metadata
            stamp_run_metadata({META_KEY: record, "clarification_question": record["question"]})
        except Exception as exc:
            log.warning("live ask: run metadata stamp failed: %s", exc)
        try:
            from run_trace import record_edge
            record_edge("step.ask", "ask.live", loop_id=loop_id,
                        handle_id=handle_id or None, question=record["question"][:200])
        except Exception:
            pass
        _notify(record, handle_id=handle_id, goal=goal)
        try:
            _mark_path(scratch).write_text(json.dumps(record), encoding="utf-8")
        except OSError as exc:
            log.warning("live ask: mark not written: %s", exc)
        return {**record, "remaining_s": live_wait_s()}
    except Exception as exc:
        log.warning("live ask watch failed: %s", exc)
        return None


def close_live(scratch) -> Optional[Dict[str, Any]]:
    """After the step: consume the live marker (archived beside the ask)
    and the answer file if any. Returns the marker's record with
    `answered` set from what the run metadata says, or None when the step
    never announced a live ask."""
    mark = _read_mark(scratch)
    if mark is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    try:
        os.replace(_mark_path(scratch), Path(scratch) / f"ask-live.{stamp}.done.json")
    except OSError:
        pass
    ap = answer_path(str(scratch))
    answered = False
    if ap is not None and ap.is_file():
        answered = True
        try:
            os.replace(ap, Path(scratch) / f"ask-answer.{stamp}.json")
        except OSError:
            pass
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd is not None:
            rec = _read_meta(Path(rd)).get(META_KEY)
            if isinstance(rec, dict) and rec.get("live") and rec.get("status") == STATUS_ANSWERED:
                answered = True
                mark = rec
    except Exception:
        pass
    return {**mark, "answered": answered}


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


def _continuation_reason(goal: str, question: str, text: str, outcome: str = "") -> str:
    if outcome:
        # An env_request escalation: the orchestrator's verb was applied on
        # the host (grant + build) before the resume; the worker needs the
        # outcome, not the verb.
        return (
            f"CONTINUATION of: {goal}\n\n"
            f"== Orchestrator decision ==\n"
            f"The run paused on an install request: {question}\n"
            f"Outcome: {outcome}\n"
            f"Continue from where the run paused; do not request it again.\n"
            f"== End orchestrator decision =="
        )
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
    if (rec.get("live") and rec.get("status") == STATUS_PENDING
            and not meta.get("pause_reason")):
        # The worker is waiting on the answer file right now — no resume.
        # A live window that closed with the step is converted to a pause
        # by the loop (pause_reason set), which routes below instead.
        rec = dict(rec)
        rec.update({"status": STATUS_ANSWERED, "answer": text[:2000],
                    "answered_at": _iso(now), "answer_source": source,
                    "delivery": "live"})
        ap = _write_answer_file(rd / "scratch", {"answer": text, "answered_at": _iso(now),
                                                  "source": source})
        try:
            from runs import stamp_run_metadata_for
            stamp_run_metadata_for(handle_id, {META_KEY: rec, "clarification_answer": text[:2000]})
        except Exception as exc:
            log.warning("answer: live metadata stamp failed: %s", exc)
        try:
            from run_trace import record_edge
            record_edge("ask.live", "answer.delivered", handle_id=handle_id, source=source)
        except Exception:
            pass
        return {"status": "delivered", "handle_id": handle_id, "question": question,
                "answer_file": str(ap or ""), "late": False}
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
    outcome_text = ""
    if str(rec.get("kind") or "") == "env_request":
        try:
            import env_request as _er
            _verb, outcome_text = _er.apply_answer(rec, text)
            rec["decision"] = _verb or "unclear"
            rec["outcome"] = outcome_text[:1000]
        except Exception as exc:
            outcome_text = f"the orchestrator's answer could not be applied: {exc}"
            log.warning("answer: env_request apply failed: %s", exc)
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
            reason=_continuation_reason(goal, question, text, outcome_text),
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
            "live": bool(rec.get("live", False)),
            "delivery": str(rec.get("delivery") or ""),
            "kind": str(rec.get("kind") or "question"),
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
        kind = " [install]" if r.get("kind") == "env_request" else ""
        if r.get("live"):
            kind += " [live]"
        line = f"{mark} {r['handle_id']}  {r['status']:<8}{kind} {r['asked_at']}  {r['question'][:100]}"
        if r["status"] == STATUS_PENDING and r["deadline"]:
            line += f"  (until {r['deadline']})"
        if r["late"]:
            line += "  [late]"
        out.append(line)
    return "\n".join(out)
