"""Execution snapshot — Phase 23 / Phase 36 event stream.

maro-observe              → full snapshot (loop state, heartbeat, recent outcomes)
maro-observe loop         → active goal / loop lock only
maro-observe heartbeat    → heartbeat health only
maro-observe projects     → per-project status at a glance (ACTIVE/STUCK/HEALTHY/UNKNOWN)
maro-observe outcomes     → recent task outcomes
maro-observe memory       → memory tier stats (same data as Stage 2 of maro-knowledge status)
maro-observe events       → tail the live event stream (memory/events.jsonl)
maro-observe watch        → periodic full-snapshot refresh (like `watch`)

All reads are local JSONL/JSON — no LLM calls, no side effects.

Phase 36: write_event() appends structured step/loop events to memory/events.jsonl.
          Called from agent_loop.py after each step completion.

The HTTP dashboard (`maro-observe serve`) was archived 2026-07-02 — see
archive/observe_dashboard.py for the code and why, and the "Goal Lineage"
section of docs/ARCHITECTURE_OVERVIEW.md for the surviving ancestry-visibility
surface (`maro ancestry` CLI).
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from context_budget import clip as _cb_clip


# ---------------------------------------------------------------------------
# Path helpers (mirrors orch_root / config fallbacks)
# ---------------------------------------------------------------------------

def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _loop_lock_path() -> Path:
    return _memory_dir() / "loop.lock"


def _heartbeat_path() -> Path:
    return _memory_dir() / "heartbeat-state.json"


def _outcomes_path() -> Path:
    return _memory_dir() / "outcomes.jsonl"


def _events_path() -> Path:
    return _memory_dir() / "events.jsonl"


def _diagnoses_path() -> Path:
    return _memory_dir() / "diagnoses.jsonl"


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_loop_state() -> Dict[str, Any]:
    path = _loop_lock_path()
    if not path.exists():
        return {"running": False}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        d["running"] = True
        return d
    except Exception as e:
        return {"running": False, "error": str(e)}


def _read_heartbeat() -> Dict[str, Any]:
    path = _heartbeat_path()
    if not path.exists():
        return {"available": False}
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        d["available"] = True
        return d
    except Exception as e:
        return {"available": False, "error": str(e)}


def _read_recent_outcomes(limit: int = 10) -> List[Dict[str, Any]]:
    from jsonl_utils import read_jsonl_tail
    return list(reversed(read_jsonl_tail(_outcomes_path(), limit=limit)))


def _read_recent_diagnoses(limit: int = 8) -> List[Dict[str, Any]]:
    from jsonl_utils import read_jsonl_tail
    return read_jsonl_tail(_diagnoses_path(), limit=limit)


def _read_slow_scheduler() -> Dict[str, Any]:
    try:
        from slow_update_scheduler import SlowUpdateScheduler
        s = SlowUpdateScheduler()
        return s.status()
    except Exception as e:
        return {"error": str(e)}


def _read_memory_stats() -> Dict[str, Any]:
    try:
        from memory import memory_status
        return memory_status()
    except Exception as e:
        return {"error": str(e)}


def _read_cost_summary(hours: int = 24) -> Dict[str, Any]:
    """Sum step-costs.jsonl entries from the last N hours."""
    try:
        from metrics import load_step_costs
        entries = load_step_costs(limit=2000)
        if not entries:
            return {"total_usd": 0.0, "tokens_in": 0, "tokens_out": 0, "step_count": 0}

        cutoff_ts = None
        if hours > 0:
            from datetime import timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
            cutoff_ts = cutoff.isoformat()

        total_usd = 0.0
        tokens_in = 0
        tokens_out = 0
        by_model: Dict[str, float] = {}
        count = 0

        for e in entries:
            if cutoff_ts and (e.get("ts") or "") < cutoff_ts:
                continue
            total_usd += e.get("cost_usd", 0.0)
            tokens_in += e.get("tokens_in", 0)
            tokens_out += e.get("tokens_out", 0)
            model = e.get("model", "unknown")
            by_model[model] = by_model.get(model, 0.0) + e.get("cost_usd", 0.0)
            count += 1

        return {
            "total_usd": round(total_usd, 6),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "step_count": count,
            "by_model": {k: round(v, 6) for k, v in sorted(by_model.items(), key=lambda x: -x[1])},
            "window_hours": hours,
        }
    except Exception as e:
        return {"error": str(e), "total_usd": 0.0}


def _read_ancestry_tree() -> List[Dict[str, Any]]:
    """Scan workspace projects for ancestry relationships.

    Returns a list of project nodes each with:
      slug, parent_id, depth, ancestry (breadcrumb list of {id, title})
    """
    try:
        from orch_items import projects_root as _projects_root
        projects_root = _projects_root()
        if not projects_root.exists():
            return []

        nodes = []
        for slug_dir in sorted(projects_root.iterdir()):
            if not slug_dir.is_dir():
                continue
            ancestry_file = slug_dir / "ancestry.json"
            slug = slug_dir.name
            if ancestry_file.exists():
                try:
                    a = json.loads(ancestry_file.read_text(encoding="utf-8"))
                    nodes.append({
                        "slug": slug,
                        "parent_id": a.get("parent_id"),
                        "depth": len(a.get("ancestry", [])),
                        "ancestry": a.get("ancestry", []),
                    })
                except Exception:
                    pass
            else:
                # Project exists but no ancestry.json = root-level
                nodes.append({
                    "slug": slug,
                    "parent_id": None,
                    "depth": 0,
                    "ancestry": [],
                })

        return nodes
    except Exception:
        return []


def _read_eval_trend(limit: int = 10) -> List[Dict[str, Any]]:
    """Load recent eval pass-rate trend for the dashboard panel.

    Returns a list of recent trend entries (newest first), each with:
      timestamp, builtin_score, generated_pass_rate (optional), run_id.
    """
    try:
        from eval import load_eval_trend as _load_trend
        entries = _load_trend(limit=limit)
        return list(reversed(entries))  # newest first for display
    except Exception:
        return []


def _read_captain_log_entries(limit: int = 20) -> List[Dict[str, Any]]:
    """Read recent captain's log entries for the dashboard panel.

    Returns the most recent `limit` entries (newest first), each normalized to:
      ts, event_type, loop_id, subject, summary.
    """
    from jsonl_utils import read_jsonl_tail
    path = _memory_dir() / "captains_log.jsonl"
    raw = read_jsonl_tail(path, limit=limit)
    entries: List[Dict[str, Any]] = []
    for e in reversed(raw):
        ts = e.get("timestamp") or e.get("ts") or ""
        event_type = e.get("event_type", "?")
        loop_id = (e.get("loop_id") or "")[:12]
        subject = e.get("subject") or e.get("name") or ""
        # Best summary: use 'summary', fallback to 'note', fallback to 'suggestion'
        summary = (
            e.get("summary")
            or e.get("note")
            or e.get("suggestion")
            or e.get("lesson")
            or ""
        )
        entries.append({
            "ts": ts,
            "event_type": event_type,
            "loop_id": loop_id,
            "subject": subject[:60],
            "summary": summary[:120],
        })
    return entries


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _read_suggestion_stats() -> Dict[str, Any]:
    """Summarize evolver suggestions by category and status from suggestions.jsonl.

    Returns:
      total: int, by_category: {cat: count}, by_status: {status: count},
      pending: int (status unknown/pending_human_review), applied: int.
    """
    try:
        from jsonl_utils import read_jsonl_tail
        path = _memory_dir() / "suggestions.jsonl"
        by_cat: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for d in read_jsonl_tail(path):
            cat = d.get("category", "unknown")
            status = d.get("status", "unknown")
            by_cat[cat] = by_cat.get(cat, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
        total = sum(by_cat.values())
        pending = by_status.get("unknown", 0) + by_status.get("pending_human_review", 0)
        applied = by_status.get("applied", 0)
        return {
            "total": total,
            "by_category": by_cat,
            "by_status": by_status,
            "pending": pending,
            "applied": applied,
        }
    except Exception:
        return {"total": 0, "by_category": {}, "by_status": {}, "pending": 0, "applied": 0}


def _age(iso_str: str) -> str:
    """Human-readable age from ISO timestamp."""
    try:
        ts = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso_str[:19] if iso_str else "?"


def print_loop_state(loop: Optional[Dict[str, Any]] = None) -> None:
    loop = loop or _read_loop_state()
    print("Loop")
    if not loop.get("running"):
        print("  idle (no loop.lock)")
        if "error" in loop:
            print(f"  [error: {loop['error']}]")
        return
    goal = loop.get("goal", "(no goal)")
    pid = loop.get("pid", "?")
    started = loop.get("started_at", "")
    age = _age(started) if started else "?"
    loop_id = loop.get("loop_id", "?")
    print(f"  RUNNING  pid={pid}  started {age}")
    print(f"  id:   {loop_id}")
    print(f"  goal: {goal}")


def print_heartbeat(hb: Optional[Dict[str, Any]] = None) -> None:
    hb = hb or _read_heartbeat()
    print("Heartbeat")
    if not hb.get("available"):
        print("  no heartbeat-state.json")
        if "error" in hb:
            print(f"  [error: {hb['error']}]")
        return
    status = hb.get("status", "?")
    updated = hb.get("updated_at") or hb.get("timestamp", "")
    age = _age(updated) if updated else "?"
    print(f"  status: {status}  (updated {age})")
    if "message" in hb:
        print(f"  {hb['message']}")
    # Surface tier if present (tier-2 LLM diagnosis)
    if "tier" in hb:
        print(f"  tier: {hb['tier']}")


def print_recent_outcomes(limit: int = 10) -> None:
    outcomes = _read_recent_outcomes(limit=limit)
    print(f"Recent outcomes (last {min(limit, len(outcomes))})")
    if not outcomes:
        print("  none")
        return
    for o in outcomes:
        ts = o.get("timestamp") or o.get("recorded_at", "")
        age = _age(ts) if ts else "?"
        status = o.get("status") or o.get("outcome", "?")
        goal = o.get("goal") or o.get("task", "?")
        if len(goal) > 70:
            goal = goal[:67] + "..."
        print(f"  [{age:>8}]  {status:12}  {goal}")


def print_memory_stats() -> None:
    stats = _read_memory_stats()
    print("Memory")
    if "error" in stats:
        print(f"  [error: {stats['error']}]")
        return
    med = stats.get("medium", {})
    lng = stats.get("long", {})
    print(f"  medium: {med.get('count', 0)} lessons  avg={med.get('avg_score', '?')}")
    print(f"  long:   {lng.get('count', 0)} lessons")
    promo = med.get("promote_candidates", 0)
    gc = med.get("gc_candidates", 0)
    if promo:
        print(f"  ↑  {promo} ready to promote (medium→long)")
    if gc:
        print(f"  ⚠  {gc} near GC threshold")


# ---------------------------------------------------------------------------
# Project status board
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    "stuck":   "STUCK  ",
    "warning": "WARN   ",
    "healthy": "OK     ",
    "unknown": "UNKN   ",
    "active":  "ACTIVE ",
    "failed":  "FAILED ",
    "paused":  "PAUSED ",
}
_STATUS_COLOUR = {
    "stuck":   "\033[31m",   # red
    "warning": "\033[33m",   # yellow
    "healthy": "\033[32m",   # green
    "active":  "\033[36m",   # cyan
    "unknown": "\033[90m",   # grey
    "failed":  "\033[35m",   # magenta
    "paused":  "\033[90m",   # grey
}
_RESET = "\033[0m"


def _project_status_rows() -> List[dict]:
    """Return per-project status dicts using sheriff + heartbeat data.

    Each row: {"project": str, "status": str, "detail": str, "since": str}
    No LLM calls — all data is from local JSONL/JSON files.
    """
    rows: List[dict] = []

    # Check if the current loop is tied to a project
    loop = _read_loop_state()
    active_project = loop.get("project") if loop else None

    try:
        from sheriff import check_all_projects
        reports = check_all_projects()
        for r in reports:
            st = r.status if r.status in _STATUS_LABEL else "unknown"
            if r.project == active_project:
                st = "active"
            rows.append({
                "project": r.project,
                "status": st,
                "detail": r.diagnosis or "",
                "since": "",
            })
    except Exception:
        pass

    # Heartbeat stuck list as fallback / supplement
    hb = _read_heartbeat()
    hb_stuck = hb.get("stuck_projects", []) if hb else []
    known = {r["project"] for r in rows}
    for proj in hb_stuck:
        if proj not in known:
            rows.append({"project": proj, "status": "stuck",
                         "detail": "flagged by heartbeat", "since": ""})

    return rows


def print_project_status(use_colour: bool = True) -> None:
    """Print a one-line-per-project status board.

    Format:
      ACTIVE  openclaw-orchestration   Phase 60 running
      STUCK   do-something             repeated decisions
      OK      skills-research          no issues
    """
    rows = _project_status_rows()

    if not rows:
        print("Projects: no data (sheriff unavailable or no projects configured)")
        return

    print("Projects")
    max_proj = max(len(r["project"]) for r in rows)
    for r in rows:
        st = r["status"]
        label = _STATUS_LABEL.get(st, "UNKN   ")
        detail = r["detail"][:60] if r["detail"] else ""
        proj = r["project"].ljust(max_proj)
        if use_colour:
            col = _STATUS_COLOUR.get(st, "")
            print(f"  {col}{label}{_RESET} {proj}  {detail}")
        else:
            print(f"  {label} {proj}  {detail}")


# ---------------------------------------------------------------------------
# Full snapshot
# ---------------------------------------------------------------------------

def print_snapshot(outcomes_limit: int = 10) -> None:
    loop = _read_loop_state()
    hb = _read_heartbeat()

    print("╔══════════════════════════════════════════════════════╗")
    print("║              Maro Execution Snapshot                  ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print_loop_state(loop)
    print()
    print_heartbeat(hb)
    print()
    print_project_status()
    print()
    print_recent_outcomes(limit=outcomes_limit)
    print()
    print_memory_stats()
    print()
    print("──────────────────────────────────────────────────────")
    print("Tip: maro-observe loop | heartbeat | projects | outcomes | memory")
    print("     maro-knowledge status  for crystallization view")


# ---------------------------------------------------------------------------
# Phase 36: Event stream — write_event + print_events_tail
# ---------------------------------------------------------------------------

# PIPE_BUF: the unlocked single O_APPEND write below is only atomic while
# line + "\n" stays at or under this many BYTES. json.dumps ASCII-escapes,
# so characters are not bytes — a non-ASCII char can encode as up to 12
# bytes (\uXXXX\uXXXX) — which is why enforcement measures the ENCODED
# line, not field character counts (CONTRACTS C0.3).
_EVENT_LINE_MAX_BYTES = 4096


def _truncate_encoded(value: str, max_bytes: int) -> str:
    """Longest prefix of value whose json.dumps encoding fits max_bytes.

    Byte-aware where character slicing is not: json.dumps(s) is pure ASCII
    (default ensure_ascii), so len() of the dump IS its byte length, and a
    multibyte char can cost up to 12 of them.
    """
    if len(json.dumps(value)) <= max_bytes:
        return value
    lo, hi = 0, len(value)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(json.dumps(value[:mid])) <= max_bytes:
            lo = mid
        else:
            hi = mid - 1
    return value[:lo]


# Numeric projections may carry only sane finite numbers (R2-3): the caps
# ladder budgets STRING fields, so a container smuggled into tokens_in or a
# multi-thousand-digit int bypassed it entirely — the latter even makes
# json.dumps raise (CPython's int->str digit limit), silently dropping the
# event. Anything outside this bound could also threaten the PIPE_BUF line
# budget; 10**15 is far beyond any honest token/ms count.
_EVENT_NUM_BOUND = 10 ** 15


def _coerce_event_num(value: Any, name: str, invalid: List[str]) -> Optional[int | float]:
    """int/float only, finite, |v| < 10**15 — else None + a note in invalid."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int) and -_EVENT_NUM_BOUND < value < _EVENT_NUM_BOUND:
        return value
    if isinstance(value, float) and math.isfinite(value) \
            and abs(value) < _EVENT_NUM_BOUND:
        return value
    if len(invalid) < 8:  # capped — the note must not become a payload
        invalid.append(name)
    return None


def _event_str(value: Any, cap: int) -> str:
    """str(value)[:cap], surviving values str() itself rejects (a >4300-digit
    int trips CPython's int->str limit) — a hostile field must degrade, not
    take the whole event down."""
    try:
        return str(value)[:cap]
    except Exception:
        return f"<unrepresentable {type(value).__name__}>"[:cap]


def write_event(
    event_type: str,
    *,
    goal: str = "",
    project: str = "",
    loop_id: str = "",
    step: str = "",
    step_idx: int = 0,
    status: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cache_read_tokens: int = 0,
    model: str = "",
    elapsed_ms: int = 0,
    detail: str = "",
    tool_pathologies: Optional[List[dict]] = None,
) -> bool:
    """Append a structured event to memory/events.jsonl.

    Called from agent_loop.py after each step so maro-observe events can
    display a live feed of what the system is doing.

    Never raises — True means the full encoded line was accepted by one
    write(2); a short write (torn row) or any failure returns False.

    event_type values: "step_done" | "step_stuck" | "loop_start" | "loop_done"
    """
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # R2-3: numeric projections are coerced, not copied — a container or
        # a huge int in any of them either bypassed the shed ladder or made
        # json.dumps raise (silent drop). Invalid values are DROPPED from
        # the row and named in `invalid_fields` (capped) so the event still
        # lands and the corruption is visible.
        invalid: List[str] = []
        entry = {
            # C0.3: EVERY string field is capped — the previously-uncapped
            # ones (event_type/project/loop_id/status/model) let one long
            # value blow the PIPE_BUF atomicity budget for the whole line.
            "event_type": _event_str(event_type, 64),
            "ts": datetime.now(timezone.utc).isoformat(),
            "goal": _event_str(goal, 80),
            "project": _event_str(project, 120),
            "loop_id": _event_str(loop_id, 64),
            "step": _event_str(step, 120),
            "step_idx": _coerce_event_num(step_idx, "step_idx", invalid),
            "status": _event_str(status, 64),
            "tokens_in": _coerce_event_num(tokens_in, "tokens_in", invalid),
            "tokens_out": _coerce_event_num(tokens_out, "tokens_out", invalid),
            "cache_read_tokens": _coerce_event_num(
                cache_read_tokens, "cache_read_tokens", invalid),
            "model": _event_str(model, 120),
            "elapsed_ms": _coerce_event_num(elapsed_ms, "elapsed_ms", invalid),
            # 200 is load-bearing (PIPE_BUF row atomicity) — kept, but the
            # cut announces itself now.
            "detail": _cb_clip(_event_str(detail, 4096)
                               if not isinstance(detail, str) else detail,
                               200),
        }
        for _name in invalid:
            entry.pop(_name, None)  # absent, not null — readers .get(x, 0)
        if invalid:
            entry["invalid_fields"] = invalid
        if tool_pathologies and isinstance(tool_pathologies, list):
            # Bounded like every other field (PIPE_BUF atomicity): at most 3
            # entries, evidence trimmed — the full text lives on the step
            # outcome / transcript artifact.
            entry["tool_pathologies"] = [
                {"cls": _event_str(p.get("cls", ""), 40),
                 "evidence": _event_str(p.get("evidence", ""), 160)}
                for p in tool_pathologies[:3] if isinstance(p, dict)
            ]
        # Character caps alone don't bound BYTES (json.dumps ASCII-escapes:
        # a multibyte char can encode 12 bytes) — measure the encoded line
        # and shed weight until line+"\n" fits PIPE_BUF. Never block, never
        # lock: drop optional payload first, then byte-budget every string
        # field so the sum provably fits. allow_nan=False (B2 wire format):
        # bare NaN/Infinity is not JSON — coercion above strips non-finite
        # floats, and the ValueError fallback catches anything that slips.
        try:
            line = json.dumps(entry, allow_nan=False)
            if len(line) + 1 > _EVENT_LINE_MAX_BYTES:
                entry.pop("tool_pathologies", None)
                line = json.dumps(entry, allow_nan=False)
            if len(line) + 1 > _EVENT_LINE_MAX_BYTES:
                # Encoded-representation budgets (quotes included); they sum
                # to ~2000 bytes, leaving ample room for keys, numerics, ts.
                for key, budget in (("event_type", 128), ("goal", 256),
                                    ("project", 256), ("loop_id", 128),
                                    ("step", 384), ("status", 128),
                                    ("model", 256), ("detail", 512)):
                    val = entry.get(key)
                    if isinstance(val, str):
                        entry[key] = _truncate_encoded(val, budget)
                line = json.dumps(entry, allow_nan=False)
            encoded = (line + "\n").encode("utf-8")
        except (TypeError, ValueError):
            encoded = None
        # ONE authoritative final check (R2-3): whatever the ladder above
        # did, nothing oversize — and nothing unencodable — reaches the
        # append. The fallback row is fixed-shape and provably tiny; an
        # event is NEVER silently dropped for being hostile.
        if encoded is None or len(encoded) > _EVENT_LINE_MAX_BYTES:
            fallback = {
                "event_type": "event_truncated",
                "ts": entry.get("ts")
                or datetime.now(timezone.utc).isoformat(),
                "orig_event_type": _event_str(event_type, 64),
            }
            encoded = (json.dumps(fallback) + "\n").encode("utf-8")
        # Deliberately UNLOCKED: the enforcement above keeps the line under
        # PIPE_BUF, and file_lock._report_timeout calls this — locking here
        # would recurse into the lock machinery while it's reporting a
        # timeout. "Single write" is literal (R2-3, B9): one os.write of
        # the fully encoded bytes on an O_APPEND fd — a buffered file
        # object could legally split the flush and void the atomicity the
        # contract claims.
        try:
            fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                         0o666)
            try:
                n = os.write(fd, encoded)
            finally:
                os.close(fd)
        except OSError as exc:
            # Round 4: an open/write failure (EACCES, ENOSPC, ...) was
            # swallowed by the blanket handler below with zero diagnostics —
            # and most callers ignore the bool, so the feed could vanish
            # silently. Stdlib logging only (never write_event, never a
            # lock — same recursion rule as the short-write branch).
            import logging
            logging.getLogger("maro.observe").warning(
                "write_event: append to %s failed (%s) — event %r lost",
                path, exc, entry.get("event_type", ""))
            return False
        if n != len(encoded):
            # R3-2: a short write(2) left a TORN row while this returned
            # True. True now means the full buffer was accepted by one
            # write; anything less is False. No retry of the remainder — a
            # second unlocked append could interleave with another writer's
            # row, turning one torn line into two. Diagnostic is stdlib
            # logging only (NEVER write_event, NEVER a lock: file_lock
            # reports its own failures through this function).
            import logging
            logging.getLogger("maro.observe").warning(
                "write_event: short write to %s (%d of %d bytes) — torn "
                "JSONL row for event %r", path, n, len(encoded),
                entry.get("event_type", ""))
            return False
        return True
    except Exception:
        return False


def print_events_tail(limit: int = 20) -> None:
    """Print the most recent events from events.jsonl."""
    from jsonl_utils import read_jsonl_tail
    path = _events_path()
    if not path.exists():
        print("No events recorded yet.")
        return

    recent = read_jsonl_tail(path, limit=limit)
    print(f"Recent events (last {len(recent)}):")
    print("─" * 60)
    for e in recent:
        ts = e.get("ts", "")[:19].replace("T", " ")
        etype = e.get("event_type", "?")
        status = e.get("status", "")
        step = e.get("step", "")[:50]
        loop_id = e.get("loop_id", "")[:8]
        tok = e.get("tokens_in", 0) + e.get("tokens_out", 0)
        status_icon = {"done": "✓", "stuck": "✗", "start": "→"}.get(status, " ")
        print(f"  {ts}  [{loop_id}] {status_icon} {etype:<12} {step}")
        if tok:
            print(f"  {'':>26}  tokens={tok}")


# ---------------------------------------------------------------------------
# The stdlib HTTP dashboard (Phase 36 proof-of-concept) was archived 2026-07-02.
# See archive/observe_dashboard.py for the code + why, and the "Goal Lineage"
# section of docs/ARCHITECTURE_OVERVIEW.md for the surviving ancestry-visibility
# surface (`maro ancestry` CLI). Not imported here; `maro-observe serve` below
# points users at the archive instead of silently running dead code.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="maro-observe",
        description="Execution snapshot — loop state, heartbeat, outcomes",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("loop", help="Active goal / loop lock")
    sub.add_parser("heartbeat", help="Heartbeat health status")
    sub.add_parser("projects", help="Per-project status board (ACTIVE/STUCK/OK)")
    p_out = sub.add_parser("outcomes", help="Recent task outcomes")
    p_out.add_argument("--limit", type=int, default=20, help="Number of outcomes (default: 20)")
    sub.add_parser("memory", help="Memory tier stats")
    p_events = sub.add_parser("events", help="Live event stream tail (memory/events.jsonl)")
    p_events.add_argument("--limit", type=int, default=20, help="Number of events (default: 20)")
    p_watch = sub.add_parser("watch", help="Refresh snapshot on an interval (like watch)")
    p_watch.add_argument("--interval", type=float, default=5.0, help="Refresh interval in seconds (default: 5)")
    sub.add_parser("serve", help="[ARCHIVED] see archive/observe_dashboard.py")

    args = parser.parse_args(argv)

    if args.cmd == "loop":
        print_loop_state()
    elif args.cmd == "heartbeat":
        print_heartbeat()
    elif args.cmd == "projects":
        print_project_status()
    elif args.cmd == "outcomes":
        print_recent_outcomes(limit=args.limit)
    elif args.cmd == "memory":
        print_memory_stats()
    elif args.cmd == "events":
        print_events_tail(limit=args.limit)
    elif args.cmd == "watch":
        import time, os
        while True:
            os.system("clear")
            print_snapshot()
            print(f"\n(refreshing every {args.interval}s — Ctrl-C to stop)")
            time.sleep(args.interval)
    elif args.cmd == "serve":
        print(
            "maro-observe serve was archived 2026-07-02 (failed proof-of-concept).\n"
            "See archive/observe_dashboard.py for the code, and\n"
            "docs/ARCHITECTURE_OVERVIEW.md's \"Goal Lineage\" section for the\n"
            "surviving ancestry-visibility surface: `maro ancestry`.\n"
            "To run the archived dashboard anyway:\n"
            "  PYTHONPATH=src:archive python3 -c "
            "\"import observe_dashboard as d; d.serve_dashboard()\""
        )
    else:
        print_snapshot()


if __name__ == "__main__":
    main()
