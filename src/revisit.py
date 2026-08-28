"""
revisit.py — §14h revisit mechanic: acquisition events reopen dead ends
(COMPOUND_THINKING_DESIGN §13b, §14h).

§13b: a stop verdict is an observation with a stated reopen condition,
not a fact — "a dead end doesn't stay a dead end." §14h (Jeremy):
"sometimes you have to revisit previous dead ends with new tools (or
just to take a look at something you missed) in order for a path to
open up." This module is the scanner that notices when a way back MAY
have opened: a capability acquired AFTER a run stopped (skill promoted,
canon lesson, rule graduated, knowledge node promoted) is matched
against standing stop verdicts, and matches surface as
REVISIT_CANDIDATE events plus a heartbeat report line.

Honest-matching rules (v1):

- thesis-refuted     — reopens on "new connection evidence (a new
                       landmark or vantage)"; an acquisition IS new
                       vantage → event-matchable.
- reachable-but-not-worth-it — reopens when "the cost or value estimate
                       moves"; a new tool moves the cost estimate →
                       event-matchable.
- out-of-budget      — reopens with budget: an operator/config act, not
                       an observable acquisition. Listed as standing,
                       never event-matched. (Its §13b reopen_payload —
                       which budget, where it stood — is recorded at
                       stamp time for a future numbers-vs-numbers check.)
- lost-the-plot      — reopens on "re-anchor against the original ask":
                       an operator/§9.5 act. Listed as standing, never
                       event-matched.

A candidate is a LEAD, not a rerun: this module surfaces (event + report
line) and never re-executes anything — the operator or director decides.
Deterministic, zero LLM calls. Dedup state (memory/revisit_state.json)
records the newest signal surfaced per run, so a candidate fires once
per new acquisition, not once per heartbeat.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from datetime import timezone

from stop_verdicts import (
    NOT_WORTH_IT,
    OUT_OF_BUDGET,
    LOST_THE_PLOT,
    THESIS_REFUTED,
    reopen_condition,
)

log = logging.getLogger("maro.revisit")

# Capability-acquisition event vocabulary (captains_log.py): the "new
# tools" of §14h. Matching treats them equally; membership is the only
# thing read (r1 review: an earlier comment claimed a display ordering
# nothing implemented).
ACQUISITION_EVENT_TYPES = frozenset((
    "SKILL_PROMOTED",
    "CANON_PROMOTED",
    "RULE_GRADUATED",
    "KNOWLEDGE_NODE_PROMOTED",
))

# Verdicts whose type-derived reopen condition an acquisition can
# plausibly satisfy (module docstring: the honest-matching rules).
EVENT_MATCHABLE_VERDICTS = frozenset((THESIS_REFUTED, NOT_WORTH_IT))
STANDING_ONLY_VERDICTS = frozenset((OUT_OF_BUDGET, LOST_THE_PLOT))

# At most this many candidates surface per sweep — a burst of promotions
# must not turn the captain's log into a wall of revisit rows; the rest
# surface on later sweeps (dedup state doesn't advance for unsurfaced
# runs, so nothing is lost).
MAX_CANDIDATES_PER_SWEEP = 3

# Signals shown per candidate (event summary and CLI alike).
SIGNALS_SHOWN = 3

_STATE_FILENAME = "revisit_state.json"
_SWEEP_MUTEX_FILENAME = "revisit_sweep.lock"


@dataclass
class RevisitCandidate:
    run_name: str            # run dir name (handle_id-nickname)
    verdict: str
    reopen_cond: str
    goal: str
    ended_at: str
    stop_evidence: str
    reopen_payload: Optional[dict] = None
    signals: List[dict] = field(default_factory=list)  # {ts, event_type, subject}

    @property
    def newest_signal_ts(self) -> str:
        return max((s.get("ts") or "" for s in self.signals), default="")


@dataclass
class RevisitScan:
    candidates: List[RevisitCandidate] = field(default_factory=list)
    standing: List[dict] = field(default_factory=list)  # unmatched dead ends
    runs_scanned: int = 0


def _parse_ts(value: str) -> Optional[datetime]:
    """ISO timestamp → AWARE datetime, or None.

    "Z" is normalized to "+00:00" first: requires-python floors at 3.10,
    whose fromisoformat rejects "Z" (r2 review; sibling modules carry the
    same normalization). Naive values are pinned to UTC — the runtime
    writer (runs.py finalize) stamps datetime.now(timezone.utc), so naive
    rows are foreign/hand-authored and UTC is the honest default:
    comparing naive against aware raises TypeError, and one such row
    would have unwound past scan() and silently zeroed the whole
    sweep's candidates (r1 review, both lenses' HIGH — probe-confirmed)."""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _state_path() -> Path:
    from config import memory_dir
    return memory_dir() / _STATE_FILENAME


def _load_state() -> Dict[str, str]:
    """{run_name: newest signal ts already surfaced}."""
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning("revisit: state unreadable (treating as empty): %s", exc)
    return {}


def _save_state(state: Dict[str, str]) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        from file_lock import locked_rmw

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            # Newest-ts-wins merge: a concurrent sweep's surfacing must
            # not be un-recorded by ours.
            for k, v in state.items():
                if str(v) > str(existing.get(k) or ""):
                    existing[k] = v
            return json.dumps(existing, indent=2)

        locked_rmw(path, _merge)
    except Exception as exc:
        log.warning("revisit: state save failed (candidates may resurface "
                    "next sweep): %s", exc)


def _dead_ends() -> tuple:
    """(dead_end_rows, runs_scanned): every run with a standing stop
    verdict whose goal was not achieved."""
    rows: List[dict] = []
    scanned = 0
    try:
        from runs import runs_root
        root = runs_root()
    except Exception:
        return rows, scanned
    for md in sorted(root.glob("*/metadata.json")):
        scanned += 1
        try:
            meta = json.loads(md.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        verdict = str(meta.get("stop_verdict") or "")
        if not verdict:
            continue
        if meta.get("goal_achieved") is True:
            continue  # achieved despite the verdict — nothing to revisit
        payload = meta.get("stop_reopen_payload")
        rows.append({
            "run_name": md.parent.name,
            "verdict": verdict,
            "goal": str(meta.get("prompt") or meta.get("goal") or ""),
            "ended_at": str(meta.get("ended_at") or ""),
            "stop_evidence": str(meta.get("stop_evidence") or ""),
            "reopen_payload": payload if isinstance(payload, dict) else None,
        })
    return rows, scanned


def _acquisitions_since(since_iso: str) -> List[dict]:
    """Acquisition events on/after the given ISO date, one pass over the
    log corpus (r1 review: the first cut called query_log once per event
    type — four full active+archive scans per sweep; query_log's
    event_type filter is a single prefix, so membership is filtered
    here instead)."""
    out: List[dict] = []
    since_date = (since_iso or "")[:10] or None
    try:
        from captains_log import query_log
        for row in query_log("", since=since_date, limit=0):
            if str(row.get("event_type") or "") in ACQUISITION_EVENT_TYPES:
                out.append({
                    "ts": str(row.get("timestamp") or ""),
                    "event_type": str(row.get("event_type") or ""),
                    "subject": str(row.get("subject") or ""),
                })
    except Exception as exc:
        log.warning("revisit: acquisition read failed: %s", exc)
    out.sort(key=lambda r: r["ts"])
    return out


def scan() -> RevisitScan:
    """One full pass: dead ends × acquisitions → candidates + standing
    list. Pure read — no events, no state writes (sweep() owns those)."""
    result = RevisitScan()
    dead, result.runs_scanned = _dead_ends()
    if not dead:
        return result
    matchable = [d for d in dead if d["verdict"] in EVENT_MATCHABLE_VERDICTS]
    result.standing = [d for d in dead
                       if d["verdict"] not in EVENT_MATCHABLE_VERDICTS]
    if not matchable:
        return result
    # Window heuristic only (per-run filtering below is the authority):
    # min over PARSED datetimes, not raw strings — a lexicographic min
    # over mixed-offset ISO strings can pick the wrong element, and
    # slicing a date off a non-UTC string can set the window a day late
    # and exclude valid acquisitions (r2 review). Parseable-only so one
    # garbage string can't poison the min.
    _dts = [p for p in (_parse_ts(d["ended_at"]) for d in matchable)
            if p is not None]
    oldest_dt = min(_dts, default=None)
    oldest_end = (oldest_dt.astimezone(timezone.utc).date().isoformat()
                  if oldest_dt else "")
    acquisitions = _acquisitions_since(oldest_end)
    if not acquisitions:
        # No acquisitions ≠ no dead ends: the matchable runs are still
        # standing, and the report must say so.
        result.standing.extend(matchable)
        return result
    for d in matchable:
        ended = _parse_ts(d["ended_at"])
        if ended is None:
            # No trustworthy stop time → can't honestly claim any
            # acquisition came after; keep it standing, don't guess.
            result.standing.append(d)
            continue
        signals = []
        for a in acquisitions:
            ats = _parse_ts(a["ts"])
            if ats is not None and ats > ended:
                signals.append(a)
        if signals:
            result.candidates.append(RevisitCandidate(
                run_name=d["run_name"],
                verdict=d["verdict"],
                reopen_cond=reopen_condition(d["verdict"]),
                goal=d["goal"],
                ended_at=d["ended_at"],
                stop_evidence=d["stop_evidence"],
                reopen_payload=d["reopen_payload"],
                signals=signals,
            ))
        else:
            result.standing.append(d)
    return result


def _emit_candidate(c: RevisitCandidate) -> None:
    from captains_log import log_event, REVISIT_CANDIDATE
    from context_budget import clip
    latest = c.signals[-SIGNALS_SHOWN:]
    sig_text = "; ".join(
        f"{s['event_type']} '{clip(s['subject'], 40)}'" for s in latest)
    more = len(c.signals) - len(latest)
    if more > 0:
        sig_text += f" (+{more} more)"
    log_event(
        REVISIT_CANDIDATE,
        subject=c.run_name,
        summary=(f"Dead end may have reopened ({c.verdict}, stopped "
                 f"{(c.ended_at or '?')[:10]}): capability acquired since — "
                 f"{sig_text}. Reopen condition: {c.reopen_cond}. "
                 f"Goal: {clip(c.goal, 120)}"),
        context={
            "run": c.run_name,
            "verdict": c.verdict,
            "reopen_condition": c.reopen_cond,
            "signal_count": len(c.signals),
            "signals": latest,
            **({"reopen_payload": c.reopen_payload}
               if c.reopen_payload else {}),
        },
        related_ids=[f"run:{c.run_name}"],
    )


def sweep(verbose: bool = False) -> dict:
    """The heartbeat entry point: scan, surface NEW candidates (event per
    candidate, capped), advance dedup state. Never raises.

    Returns {"total": standing dead ends incl. matched, "matched": runs
    with post-stop acquisitions, "new": candidates surfaced this sweep}.
    """
    out = {"total": 0, "matched": 0, "new": 0}
    try:
        from config import get_bool as _cfg_get_bool
        if not _cfg_get_bool("revisit.enabled", True):
            return out
        # One sweep at a time: load-state → emit → save-state is a
        # read-decide-write sequence, and two overlapping sweeps would
        # both see stale dedup state and double-fire the same candidate
        # (r1 review, two lenses). Non-blocking: a held mutex means a
        # sweep is already doing this exact work — skip, don't queue.
        from file_lock import locked_write, FileLockTimeout
        from config import memory_dir
        mutex = memory_dir() / _SWEEP_MUTEX_FILENAME
        try:
            with locked_write(mutex, timeout_s=1.0):
                result = scan()
                out["total"] = len(result.candidates) + len(result.standing)
                out["matched"] = len(result.candidates)
                state = _load_state()
                fresh = [c for c in result.candidates
                         if c.newest_signal_ts > (state.get(c.run_name) or "")]
                surfaced = fresh[:MAX_CANDIDATES_PER_SWEEP]
                for c in surfaced:
                    try:
                        _emit_candidate(c)
                        state[c.run_name] = c.newest_signal_ts
                        out["new"] += 1
                    except Exception as exc:
                        log.warning("revisit: candidate emit failed for "
                                    "%s: %s", c.run_name, exc)
                if out["new"]:
                    # _save_state's newest-ts-wins merge makes passing the
                    # whole dict equivalent to any subset (r1, minimalist).
                    _save_state(state)
        except FileLockTimeout:
            log.debug("revisit: another sweep in flight — skipping")
            return out
        if verbose and out["new"]:
            print(f"[revisit] {out['new']} dead end(s) may have reopened "
                  f"({out['matched']} matched of {out['total']} standing)")
    except Exception as exc:
        log.warning("revisit: sweep failed (non-blocking): %s", exc)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    """CLI: report the scan without emitting events or advancing state
    (the heartbeat sweep owns surfacing; this is the operator's read)."""
    import argparse
    parser = argparse.ArgumentParser(
        description="Scan stopped runs for revisit candidates (§14h)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)
    result = scan()
    if args.json:
        print(json.dumps({
            "runs_scanned": result.runs_scanned,
            "candidates": [
                {"run": c.run_name, "verdict": c.verdict,
                 "reopen_condition": c.reopen_cond, "ended_at": c.ended_at,
                 "signals": c.signals,
                 "reopen_payload": c.reopen_payload}
                for c in result.candidates
            ],
            "standing": result.standing,
        }, indent=2))
        return 0
    print(f"scanned {result.runs_scanned} runs — "
          f"{len(result.candidates)} candidate(s), "
          f"{len(result.standing)} standing dead end(s)")
    for c in result.candidates:
        print(f"\n▸ {c.run_name}  [{c.verdict}]  stopped {c.ended_at[:10]}")
        print(f"  goal: {c.goal[:100]}")
        print(f"  reopen when: {c.reopen_cond}")
        if c.reopen_payload:
            print(f"  reopen data: {json.dumps(c.reopen_payload)}")
        for s in c.signals[-SIGNALS_SHOWN:]:
            print(f"  + {s['ts'][:19]}  {s['event_type']}  {s['subject'][:50]}")
        if len(c.signals) > SIGNALS_SHOWN:
            print(f"    (+{len(c.signals) - SIGNALS_SHOWN} earlier "
                  "acquisition(s))")
    for d in result.standing:
        cond = reopen_condition(d["verdict"]) or "?"
        line = (f"\n○ {d['run_name']}  [{d['verdict']}]  standing — "
                f"reopen when: {cond}")
        if d.get("reopen_payload"):
            line += f"  data: {json.dumps(d['reopen_payload'])}"
        print(line)
    return 0


if __name__ == "__main__":
    sys_exit = main()
    raise SystemExit(sys_exit)
