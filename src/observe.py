"""Execution snapshot — Phase 23 / Phase 36 event stream.

maro-observe              → full snapshot (loop state, heartbeat, recent outcomes, audit tail)
maro-observe loop         → active goal / loop lock only
maro-observe heartbeat    → heartbeat health only
maro-observe projects     → per-project status at a glance (ACTIVE/STUCK/HEALTHY/UNKNOWN)
maro-observe outcomes     → recent task outcomes
maro-observe audit        → sandbox audit log tail
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
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


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


def _audit_path() -> Path:
    return _memory_dir() / "sandbox-audit.jsonl"


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
    path = _outcomes_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
            if len(results) >= limit:
                break
        return results
    except Exception:
        return []


def _read_audit_tail(limit: int = 5) -> List[Dict[str, Any]]:
    path = _audit_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
            if len(results) >= limit:
                break
        return list(reversed(results))
    except Exception:
        return []


def _read_recent_diagnoses(limit: int = 8) -> List[Dict[str, Any]]:
    path = _diagnoses_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        results = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except Exception:
                continue
            if len(results) >= limit:
                break
        return list(reversed(results))
    except Exception:
        return []


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
    try:
        path = _memory_dir() / "captains_log.jsonl"
        if not path.exists():
            return []
        entries: List[Dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
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
                if len(entries) >= limit:
                    break
            except Exception:
                continue
        return entries
    except Exception:
        return []


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
        path = _memory_dir() / "suggestions.jsonl"
        if not path.exists():
            return {"total": 0, "by_category": {}, "by_status": {}, "pending": 0, "applied": 0}
        by_cat: Dict[str, int] = {}
        by_status: Dict[str, int] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                cat = d.get("category", "unknown")
                status = d.get("status", "unknown")
                by_cat[cat] = by_cat.get(cat, 0) + 1
                by_status[status] = by_status.get(status, 0) + 1
            except Exception:
                pass
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


def print_audit_tail(limit: int = 5) -> None:
    entries = _read_audit_tail(limit=limit)
    print(f"Sandbox audit (last {min(limit, len(entries))})")
    if not entries:
        print("  none")
        return
    for e in entries:
        ts = e.get("timestamp", "")
        age = _age(ts) if ts else "?"
        skill = e.get("skill_name", "?")
        status = "OK" if e.get("success") else "FAIL"
        duration = e.get("duration_ms")
        dur_str = f"  {duration}ms" if duration is not None else ""
        blocked = " [network-blocked]" if e.get("network_blocked") else ""
        safe = " [safe=static]" if e.get("static_safe") else ""
        print(f"  [{age:>8}]  {status:4}  {skill}{dur_str}{blocked}{safe}")


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

def print_snapshot(outcomes_limit: int = 10, audit_limit: int = 5) -> None:
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
    print_audit_tail(limit=audit_limit)
    print()
    print_memory_stats()
    print()
    print("──────────────────────────────────────────────────────")
    print("Tip: maro-observe loop | heartbeat | projects | outcomes | audit | memory")
    print("     maro-knowledge status  for crystallization view")


# ---------------------------------------------------------------------------
# Phase 36: Event stream — write_event + print_events_tail
# ---------------------------------------------------------------------------

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
) -> bool:
    """Append a structured event to memory/events.jsonl.

    Called from agent_loop.py after each step so maro-observe events can
    display a live feed of what the system is doing.

    Never raises — returns True on success, False on failure.

    event_type values: "step_done" | "step_stuck" | "loop_start" | "loop_done"
    """
    try:
        path = _events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "event_type": event_type,
            "ts": datetime.now(timezone.utc).isoformat(),
            "goal": goal[:80],
            "project": project,
            "loop_id": loop_id,
            "step": step[:120],
            "step_idx": step_idx,
            "status": status,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cache_read_tokens": cache_read_tokens,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "detail": detail[:200],
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return True
    except Exception:
        return False


def print_events_tail(limit: int = 20) -> None:
    """Print the most recent events from events.jsonl."""
    path = _events_path()
    if not path.exists():
        print("No events recorded yet.")
        return

    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            continue

    recent = entries[-limit:]
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
        description="Execution snapshot — loop state, heartbeat, outcomes, audit",
    )
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("loop", help="Active goal / loop lock")
    sub.add_parser("heartbeat", help="Heartbeat health status")
    sub.add_parser("projects", help="Per-project status board (ACTIVE/STUCK/OK)")
    p_out = sub.add_parser("outcomes", help="Recent task outcomes")
    p_out.add_argument("--limit", type=int, default=20, help="Number of outcomes (default: 20)")
    p_audit = sub.add_parser("audit", help="Sandbox audit log tail")
    p_audit.add_argument("--limit", type=int, default=10, help="Number of entries (default: 10)")
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
    elif args.cmd == "audit":
        print_audit_tail(limit=args.limit)
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
