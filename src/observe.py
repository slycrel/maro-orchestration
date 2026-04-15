"""Execution snapshot — Phase 23 / Phase 36 event stream.

poe-observe              → full snapshot (loop state, heartbeat, recent outcomes, audit tail)
poe-observe loop         → active goal / loop lock only
poe-observe heartbeat    → heartbeat health only
poe-observe projects     → per-project status at a glance (ACTIVE/STUCK/HEALTHY/UNKNOWN)
poe-observe outcomes     → recent task outcomes
poe-observe audit        → sandbox audit log tail
poe-observe memory       → memory tier stats (same data as Stage 2 of poe-knowledge status)
poe-observe events       → tail the live event stream (memory/events.jsonl)
poe-observe watch        → periodic full-snapshot refresh (like `watch`)
poe-observe serve        → local HTTP dashboard (default port 7700); no deps, stdlib only

All reads are local JSONL/JSON — no LLM calls, no side effects.

Phase 36: write_event() appends structured step/loop events to memory/events.jsonl.
          Called from agent_loop.py after each step completion.
          serve_dashboard() exposes a browser-friendly live view via stdlib http.server.
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
    print("║              Poe Execution Snapshot                  ║")
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
    print("Tip: poe-observe loop | heartbeat | projects | outcomes | audit | memory")
    print("     poe-knowledge status  for crystallization view")


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
    elapsed_ms: int = 0,
    detail: str = "",
) -> bool:
    """Append a structured event to memory/events.jsonl.

    Called from agent_loop.py after each step so poe-observe events can
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
# Phase 36: stdlib HTTP dashboard — no deps
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Poe — Agent Command Center</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { --bg:#0d1117; --panel:#161b22; --border:#30363d; --text:#c9d1d9;
          --green:#3fb950; --red:#f85149; --yellow:#d29922; --blue:#58a6ff;
          --dim:#8b949e; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font: 13px/1.5 'Cascadia Code', 'SF Mono', monospace; padding: 16px; }
  h1 { font-size: 15px; color: var(--blue); margin-bottom: 16px; }
  h2 { font-size: 12px; color: var(--dim); text-transform: uppercase; letter-spacing: .08em;
       margin: 16px 0 6px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; }
  .panel.full { grid-column: 1 / -1; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; }
  .badge-green { background: #1a3a1a; color: var(--green); }
  .badge-red   { background: #3a1a1a; color: var(--red); }
  .badge-yellow{ background: #3a2d00; color: var(--yellow); }
  .badge-blue  { background: #0d2044; color: var(--blue); }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th { text-align: left; color: var(--dim); padding: 3px 6px; font-weight: normal; }
  td { padding: 3px 6px; border-top: 1px solid var(--border); word-break: break-word; }
  .status-done  { color: var(--green); }
  .status-stuck { color: var(--red); }
  .status-start { color: var(--blue); }
  #ticker { font-size: 11px; color: var(--dim); margin-top: 12px; }
  #loop-goal { font-size: 14px; color: var(--text); margin: 4px 0; }
  .idle { color: var(--dim); font-style: italic; }
  .kv { display: flex; gap: 8px; flex-wrap: wrap; }
  .kv span { white-space: nowrap; }
  .key { color: var(--dim); }
  .cost-big { font-size: 22px; font-weight: bold; color: var(--green); }
  button.replay { margin-top: 8px; background: #1a3a1a; color: var(--green); border: 1px solid var(--green);
    border-radius: 4px; padding: 3px 10px; font: 12px monospace; cursor: pointer; }
  button.replay:hover { background: #2a5a2a; }
  button.replay:disabled { opacity: 0.4; cursor: default; }
  .tree-node { margin-left: calc(var(--depth, 0) * 16px); font-size: 12px; padding: 2px 0; }
  .tree-root { color: var(--blue); }
  .tree-child { color: var(--text); }
  .tree-sep { color: var(--dim); }
</style>
</head>
<body>
<h1>&#x25B6; Poe — Agent Command Center</h1>
<div class="grid">

  <div class="panel full" id="chat-panel">
    <h2>Goal Chat</h2>

    <!-- New goal form -->
    <div id="submit-area">
      <textarea id="goal-input" rows="3" placeholder="Describe your goal..."
                style="width:100%;font-family:monospace;font-size:13px;padding:8px;
                       background:#1a1a2e;color:#e0e0e0;border:1px solid #444;border-radius:4px"></textarea>
      <button onclick="submitGoal()"
              style="margin-top:6px;padding:8px 20px;background:#4a9eff;color:#fff;
                     border:none;border-radius:4px;cursor:pointer;font-size:13px">
        Submit Goal
      </button>
    </div>

    <!-- Thread list -->
    <div id="thread-list" style="margin-top:12px"></div>

    <!-- Active thread view -->
    <div id="thread-view" style="margin-top:12px;display:none">
      <div id="thread-messages"
           style="max-height:400px;overflow-y:auto;background:#0d0d1a;
                  padding:12px;border-radius:4px;border:1px solid #333"></div>
      <div id="reply-area" style="margin-top:8px;display:none">
        <textarea id="reply-input" rows="2" placeholder="Reply to director..."
                  style="width:100%;font-family:monospace;font-size:13px;padding:8px;
                         background:#1a1a2e;color:#e0e0e0;border:1px solid #444;border-radius:4px"></textarea>
        <button onclick="sendReply()"
                style="margin-top:6px;padding:8px 20px;background:#44bb88;color:#fff;
                       border:none;border-radius:4px;cursor:pointer;font-size:13px">
          Send Reply
        </button>
      </div>
    </div>
  </div>

  <div class="panel">
    <h2>Active Loop</h2>
    <div id="loop-status"></div>
  </div>

  <div class="panel">
    <h2>Heartbeat</h2>
    <div id="hb-status"></div>
  </div>

  <div class="panel">
    <h2>Cost (24h)</h2>
    <div id="cost-status"></div>
  </div>

  <div class="panel">
    <h2>Memory</h2>
    <div id="memory-status"></div>
  </div>

  <div class="panel">
    <h2>Slow Scheduler</h2>
    <div id="scheduler-status"></div>
  </div>

  <div class="panel full">
    <h2>Recent Outcomes</h2>
    <div id="outcomes-status"></div>
    <button class="replay" id="replay-btn" onclick="replayLast()">&#9654; Replay Last Goal</button>
    <button class="replay" id="factory-btn" onclick="replayFactory()" style="margin-left:8px;background:#1a1a3a;border-color:#66f;">&#9654; Factory Mode Replay</button>
  </div>

  <div class="panel full">
    <h2>Mission Ancestry Tree</h2>
    <div id="ancestry-status"></div>
  </div>

  <div class="panel full">
    <h2>Diagnoses (Phase 44)</h2>
    <div id="diagnoses-status"></div>
  </div>

  <div class="panel full">
    <h2>Eval Pass Rate</h2>
    <div id="eval-trend-status"></div>
  </div>

  <div class="panel">
    <h2>Evolver Suggestions</h2>
    <div id="suggestion-stats"></div>
  </div>

  <div class="panel full">
    <h2>Captain's Log <span style="font-size:11px;color:var(--dim);font-weight:normal">(recent self-improvement events)</span></h2>
    <div id="captain-log-status"></div>
  </div>

  <div class="panel full">
    <h2>Live Events</h2>
    <table id="events-table">
      <thead><tr><th>Time</th><th>Loop</th><th>Type</th><th>Status</th><th>Step</th><th>Tokens</th></tr></thead>
      <tbody id="events-body"></tbody>
    </table>
  </div>


</div>
<div id="ticker">Loading...</div>

<script>
function badge(text, cls) {
  return `<span class="badge badge-${cls}">${text}</span>`;
}
function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
async function replayLast() {
  const btn = document.getElementById('replay-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Queuing...';
  try {
    const r = await fetch('/api/replay', {method: 'POST'});
    const d = await r.json();
    if (r.ok) {
      btn.textContent = '✓ Queued: ' + (d.goal||'').slice(0,40);
      setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Replay Last Goal'; }, 5000);
    } else {
      btn.textContent = '✗ ' + (d.error||'failed');
      setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Replay Last Goal'; }, 3000);
    }
  } catch(err) {
    btn.textContent = '✗ ' + err;
    setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Replay Last Goal'; }, 3000);
  }
}

async function replayFactory() {
  const btn = document.getElementById('factory-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Scanning signals...';
  try {
    const r = await fetch('/api/replay-factory', {method: 'POST'});
    const d = await r.json();
    if (r.ok) {
      btn.textContent = '✓ Factory queued (' + (d.outcomes_scanned||0) + ' outcomes scanned)';
      setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Factory Mode Replay'; }, 8000);
    } else {
      btn.textContent = '✗ ' + (d.error||'failed');
      setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Factory Mode Replay'; }, 3000);
    }
  } catch(err) {
    btn.textContent = '✗ ' + err;
    setTimeout(() => { btn.disabled = false; btn.textContent = '▶ Factory Mode Replay'; }, 3000);
  }
}

async function refresh() {
  try {
    const r = await fetch('/api/snapshot');
    const d = await r.json();

    // Loop
    const loop = d.loop || {};
    let loopHtml;
    if (loop.running) {
      loopHtml = `${badge('RUNNING','green')} pid=${esc(loop.pid||'?')}
        <div id="loop-goal">${esc(loop.goal||'(no goal)')}</div>
        <div class="kv"><span><span class="key">id</span> ${esc((loop.loop_id||'?').slice(0,12))}</span>
        <span><span class="key">started</span> ${esc(loop.started_at||'').slice(0,19)}</span></div>`;
    } else {
      loopHtml = `<span class="idle">idle — no loop.lock</span>`;
    }
    document.getElementById('loop-status').innerHTML = loopHtml;

    // Heartbeat
    const hb = d.heartbeat || {};
    let hbHtml;
    if (hb.available) {
      const st = hb.status || '?';
      const cls = st === 'ok' ? 'green' : st === 'warn' ? 'yellow' : 'red';
      hbHtml = `${badge(st.toUpperCase(), cls)} updated ${esc(hb.updated_at||hb.timestamp||'?').slice(0,19)}`;
      if (hb.message) hbHtml += `<div>${esc(hb.message)}</div>`;
    } else {
      hbHtml = `<span class="idle">no heartbeat-state.json</span>`;
    }
    document.getElementById('hb-status').innerHTML = hbHtml;

    // Memory
    const mem = d.memory || {};
    if (mem.error) {
      document.getElementById('memory-status').innerHTML = `<span class="status-stuck">${esc(mem.error)}</span>`;
    } else {
      const med = (mem.medium||{});
      const lng = (mem.long||{});
      let memHtml = `<div class="kv">
        <span><span class="key">medium</span> ${med.count||0} lessons</span>
        <span><span class="key">long</span> ${lng.count||0} lessons</span>`;
      if (med.avg_score != null) memHtml += `<span><span class="key">avg</span> ${esc(med.avg_score)}</span>`;
      memHtml += `</div>`;
      if (med.promote_candidates) memHtml += `<div>${badge(med.promote_candidates+' to promote','blue')}</div>`;
      if (med.gc_candidates) memHtml += `<div>${badge(med.gc_candidates+' near GC','yellow')}</div>`;
      document.getElementById('memory-status').innerHTML = memHtml;
    }

    // Cost
    const cost = d.cost || {};
    if (cost.error) {
      document.getElementById('cost-status').innerHTML = `<span class="status-stuck">${esc(cost.error)}</span>`;
    } else {
      const usd = (cost.total_usd || 0).toFixed(4);
      const tok = ((cost.tokens_in||0) + (cost.tokens_out||0)).toLocaleString();
      let costHtml = `<div class="cost-big">$${usd}</div>`;
      costHtml += `<div class="kv" style="margin-top:4px">
        <span><span class="key">steps</span> ${cost.step_count||0}</span>
        <span><span class="key">tokens</span> ${tok}</span>
        <span><span class="key">window</span> ${cost.window_hours||24}h</span>
      </div>`;
      const byModel = cost.by_model || {};
      const modelEntries = Object.entries(byModel);
      if (modelEntries.length) {
        costHtml += `<div style="margin-top:6px;font-size:11px;color:var(--dim)">`;
        modelEntries.forEach(([m, c]) => {
          costHtml += `<div>${esc(m)}: $${Number(c).toFixed(4)}</div>`;
        });
        costHtml += `</div>`;
      }
      document.getElementById('cost-status').innerHTML = costHtml;
    }

    // Slow Scheduler
    const sched = d.scheduler || {};
    if (sched.error) {
      document.getElementById('scheduler-status').innerHTML = `<span class="status-stuck">${esc(sched.error)}</span>`;
    } else {
      const st = sched.state || '?';
      const clsMap = {IDLE_WAIT:'yellow', WINDOW_OPEN:'green', UPDATING:'blue', PAUSING:'yellow'};
      const cls = clsMap[st] || 'dim';
      let schedHtml = `${badge(st, cls)}`;
      schedHtml += `<div class="kv" style="margin-top:4px">
        <span><span class="key">workers</span> ${sched.active_workers||0}</span>
        <span><span class="key">cooldown</span> ${sched.idle_cooldown||0}s</span>`;
      if (sched.idle_since) schedHtml += `<span><span class="key">idle since</span> ${esc(sched.idle_since).slice(0,19)}</span>`;
      schedHtml += `</div>`;
      document.getElementById('scheduler-status').innerHTML = schedHtml;
    }

    // Outcomes
    const outcomes = d.outcomes || [];
    if (!outcomes.length) {
      document.getElementById('outcomes-status').innerHTML = '<span class="idle">none</span>';
    } else {
      let rows = outcomes.slice(0,8).map(o => {
        const ts = (o.timestamp||o.recorded_at||'').slice(11,19);
        const st = o.status||o.outcome||'?';
        const cls = st==='done'?'status-done':st==='stuck'?'status-stuck':'';
        const goal = esc((o.goal||o.task||'?').slice(0,55));
        return `<tr><td>${ts}</td><td class="${cls}">${esc(st)}</td><td>${goal}</td></tr>`;
      }).join('');
      document.getElementById('outcomes-status').innerHTML =
        `<table><thead><tr><th>Time</th><th>Status</th><th>Goal</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    // Ancestry tree
    const ancestry = d.ancestry || [];
    if (!ancestry.length) {
      document.getElementById('ancestry-status').innerHTML = '<span class="idle">no projects found</span>';
    } else {
      // Sort by depth then slug so roots come first
      const sorted = [...ancestry].sort((a,b) => (a.depth - b.depth) || a.slug.localeCompare(b.slug));
      let html = '';
      sorted.forEach(node => {
        const indent = node.depth * 16;
        const prefix = node.depth > 0 ? '└─ ' : '';
        const cls = node.depth === 0 ? 'tree-root' : 'tree-child';
        const crumbs = (node.ancestry||[]).map(n => esc(n.title||n.id)).join(' › ');
        const trail = crumbs ? `<span class="tree-sep"> (${crumbs})</span>` : '';
        html += `<div class="tree-node ${cls}" style="--depth:${node.depth}">${prefix}<strong>${esc(node.slug)}</strong>${trail}</div>`;
      });
      document.getElementById('ancestry-status').innerHTML = html;
    }

    // Diagnoses
    const diags = d.diagnoses || [];
    if (!diags.length) {
      document.getElementById('diagnoses-status').innerHTML = '<span class="idle">none — diagnoses.jsonl is empty</span>';
    } else {
      let rows = diags.map(diag => {
        const ts = (diag.diagnosed_at||diag.ts||'').slice(0,19);
        const fc = esc(diag.failure_class||'?');
        const sev = diag.severity||'info';
        const sevCls = sev==='critical'?'badge-red':sev==='warning'?'badge-yellow':'badge-blue';
        const lid = esc((diag.loop_id||'').slice(0,12));
        const rec = esc((diag.recommendation||'').slice(0,80));
        const tok = diag.total_tokens||0;
        return `<tr><td>${ts}</td><td>${lid}</td><td>${badge(fc, sev==='critical'?'red':sev==='warning'?'yellow':'blue')}</td><td>${badge(sev,sevCls.replace('badge-',''))}</td><td>${tok}</td><td>${rec}</td></tr>`;
      }).join('');
      document.getElementById('diagnoses-status').innerHTML =
        `<table><thead><tr><th>Time</th><th>Loop</th><th>Class</th><th>Severity</th><th>Tokens</th><th>Recommendation</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    // Eval trend
    const evalTrend = d.eval_trend || [];
    if (!evalTrend.length) {
      document.getElementById('eval-trend-status').innerHTML =
        '<span class="idle">no eval runs yet — run poe-nightly-eval to populate</span>';
    } else {
      // Show the last 10 runs as a sparkline table
      let rows = evalTrend.slice(0,10).map(e => {
        const ts = (e.timestamp||'').slice(0,19).replace('T',' ');
        const score = e.builtin_score != null ? (e.builtin_score * 100).toFixed(1)+'%' : '—';
        const scoreCls = e.builtin_score >= 0.9 ? 'status-done' : e.builtin_score < 0.7 ? 'status-stuck' : '';
        const genRate = e.generated_pass_rate != null ? (e.generated_pass_rate * 100).toFixed(1)+'%' : '—';
        const genCls = e.generated_pass_rate >= 0.8 ? 'status-done' : e.generated_pass_rate < 0.6 ? 'status-stuck' : '';
        const total = e.builtin_total || 0;
        const pass = e.builtin_pass || 0;
        const genTotal = e.generated_total || 0;
        return `<tr>
          <td>${esc(ts)}</td>
          <td class="${scoreCls}">${score}</td>
          <td>${pass}/${total}</td>
          <td class="${genCls}">${genRate}</td>
          <td>${e.generated_pass||0}/${genTotal}</td>
          <td style="font-size:10px;color:var(--dim)">${esc((e.run_id||'').slice(0,12))}</td>
        </tr>`;
      }).join('');
      const latest = evalTrend[0] || {};
      const trend = evalTrend.length >= 2
        ? (latest.builtin_score || 0) - (evalTrend[evalTrend.length-1].builtin_score || 0)
        : 0;
      const trendStr = trend > 0.01 ? badge('↑ improving', 'green')
        : trend < -0.01 ? badge('↓ declining', 'red')
        : badge('→ stable', 'blue');
      document.getElementById('eval-trend-status').innerHTML =
        `<div style="margin-bottom:6px">${trendStr} over last ${evalTrend.length} runs</div>` +
        `<table><thead><tr>
          <th>Time</th><th>Builtin Score</th><th>Pass/Total</th>
          <th>Gen Pass Rate</th><th>Gen P/T</th><th>Run ID</th>
        </tr></thead><tbody>${rows}</tbody></table>`;
    }

    // Evolver Suggestion Stats
    const ss = d.suggestion_stats || {};
    if (!ss.total) {
      document.getElementById('suggestion-stats').innerHTML =
        '<span class="idle">no suggestions yet</span>';
    } else {
      const cats = ss.by_category || {};
      const pending = ss.pending || 0;
      const applied = ss.applied || 0;
      let catRows = Object.entries(cats).map(([cat, n]) =>
        `<tr><td>${esc(cat)}</td><td style="text-align:right">${n}</td></tr>`
      ).join('');
      document.getElementById('suggestion-stats').innerHTML =
        `<div style="display:flex;gap:2rem;margin-bottom:.5rem">
           <div><strong>${ss.total}</strong> total</div>
           <div>${badge(pending + ' pending', pending > 50 ? 'red' : pending > 10 ? 'yellow' : 'blue')}</div>
           <div>${badge(applied + ' applied', 'green')}</div>
         </div>
         <table><thead><tr><th>Category</th><th style="text-align:right">Count</th></tr></thead><tbody>${catRows}</tbody></table>`;
    }

    // Captain's Log
    const captainLog = d.captain_log || [];
    if (!captainLog.length) {
      document.getElementById('captain-log-status').innerHTML =
        '<span class="idle">no entries — captains_log.jsonl is empty</span>';
    } else {
      // Group event types for badge colors
      const _clBadge = (et) => {
        if (!et) return badge('?', 'blue');
        if (et.startsWith('EVOLVER')) return badge(et, 'green');
        if (et.startsWith('SKILL')) return badge(et, 'blue');
        if (et === 'DIAGNOSIS') return badge(et, 'yellow');
        if (et === 'GRADUATION_PROPOSED' || et === 'RULE_GRADUATED') return badge(et, 'green');
        if (et.includes('CONTRADICT') || et.includes('STUCK')) return badge(et, 'red');
        return badge(et, 'blue');
      };
      let clRows = captainLog.map(e => {
        const ts = (e.ts||'').slice(0,19).replace('T',' ');
        const lid = esc((e.loop_id||'').slice(0,8));
        const subj = esc((e.subject||'').slice(0,40));
        const summ = esc((e.summary||'').slice(0,100));
        return `<tr><td>${esc(ts)}</td><td>${lid}</td><td>${_clBadge(e.event_type)}</td><td>${subj}</td><td>${summ}</td></tr>`;
      }).join('');
      document.getElementById('captain-log-status').innerHTML =
        `<table><thead><tr><th>Time</th><th>Loop</th><th>Event</th><th>Subject</th><th>Summary</th></tr></thead><tbody>${clRows}</tbody></table>`;
    }

    // Events
    const events = d.events || [];
    const tbody = document.getElementById('events-body');
    tbody.innerHTML = events.slice(-30).reverse().map(e => {
      const ts = (e.ts||'').slice(11,19);
      const lid = esc((e.loop_id||'').slice(0,8));
      const et = esc(e.event_type||'?');
      const st = e.status||'';
      const stCls = st==='done'?'status-done':st==='stuck'?'status-stuck':st==='start'?'status-start':'';
      const step = esc((e.step||'').slice(0,60));
      const tok = (e.tokens_in||0)+(e.tokens_out||0);
      return `<tr><td>${ts}</td><td>${lid}</td><td>${et}</td><td class="${stCls}">${esc(st)}</td><td>${step}</td><td>${tok||''}</td></tr>`;
    }).join('');

  } catch(err) {
    document.getElementById('ticker').textContent = 'Error: ' + err;
  }
  document.getElementById('ticker').textContent =
    'Last updated: ' + new Date().toLocaleTimeString();
}
refresh();
setInterval(refresh, 5000);

// ---------------------------------------------------------------------------
// Goal Chat panel
// ---------------------------------------------------------------------------
let activeThread = null;
let lastEventIdx = 0;
let pollInterval = null;

function submitGoal() {
  const goal = document.getElementById('goal-input').value.trim();
  if (!goal) return;
  fetch('/api/submit', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({goal})
  }).then(r => r.json()).then(data => {
    document.getElementById('goal-input').value = '';
    openThread(data.handle_id);
    refreshThreadList();
  });
}

function openThread(handle_id) {
  activeThread = handle_id;
  lastEventIdx = 0;
  document.getElementById('thread-view').style.display = 'block';
  document.getElementById('thread-messages').innerHTML = '';
  startPoll();
}

function startPoll() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollThread, 1200);
}

function pollThread() {
  if (!activeThread) return;
  fetch(`/api/thread/${activeThread}?since=${lastEventIdx}`)
    .then(r => r.json()).then(data => {
      data.events.forEach(ev => appendEvent(ev));
      lastEventIdx += data.events.length;
      document.getElementById('reply-area').style.display =
        data.waiting ? 'block' : 'none';
      if (data.status !== 'running') clearInterval(pollInterval);
    });
}

function appendEvent(ev) {
  const msgs = document.getElementById('thread-messages');
  const div = document.createElement('div');
  div.style.cssText = 'margin:6px 0;padding:8px 10px;border-radius:4px;font-size:12px';

  const colors = {
    user_goal: '#1a3a5c',
    user_reply: '#1a3a5c',
    question: '#3a2a1a',
    step: '#1a2a1a',
    low_confidence: '#3a1a1a',
    complete: '#1a3a2a',
    error: '#3a1a1a',
  };
  div.style.background = colors[ev.type] || '#1a1a2e';

  const label = {
    user_goal: '&#127919; Goal',
    user_reply: '&#128100; You',
    question: '&#10067; Director asks',
    step: '&#9881;&#65039; Step',
    low_confidence: '&#9888;&#65039; Risky call',
    complete: '&#9989; Done',
    error: '&#10060; Error',
  }[ev.type] || ev.type;

  // Steps and completions get block layout with wrapping text; short events stay inline
  const blockTypes = ['step', 'complete', 'question', 'low_confidence'];
  if (blockTypes.includes(ev.type)) {
    div.innerHTML = `<div style="color:#888;font-size:11px;margin-bottom:3px">${label}</div>`
      + `<div style="color:#e0e0e0;white-space:pre-wrap;word-break:break-word">${esc(ev.text||'')}</div>`;
  } else {
    div.innerHTML = `<b style="color:#aaa">${label}</b> <span style="color:#e0e0e0">${esc(ev.text||'')}</span>`;
  }
  msgs.appendChild(div);
  msgs.scrollTop = msgs.scrollHeight;
}

function sendReply() {
  const text = document.getElementById('reply-input').value.trim();
  if (!text || !activeThread) return;
  fetch(`/api/reply/${activeThread}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({text})
  }).then(() => {
    document.getElementById('reply-input').value = '';
    appendEvent({type: 'user_reply', text});
  });
}

function refreshThreadList() {
  fetch('/api/threads').then(r => r.json()).then(data => {
    const list = document.getElementById('thread-list');
    if (!data.threads || !data.threads.length) { list.innerHTML = ''; return; }
    list.innerHTML = '<div style="font-size:11px;color:#888;margin-bottom:4px">Recent goals:</div>' +
      data.threads.slice(0,8).map(t =>
        `<div onclick="openThread('${esc(t.handle_id)}')"
              style="cursor:pointer;padding:6px 8px;margin:3px 0;border-radius:3px;
                     background:${t.handle_id===activeThread?'#2a2a4a':'#1a1a2e'};
                     border:1px solid #333;font-size:12px">
          <span style="color:${t.status==='complete'?'#44bb88':t.status==='error'?'#ff6644':'#4a9eff'}"
               >&#9679;</span>
          <span style="color:#ccc"> ${esc((t.goal||'').substring(0,60))}${(t.goal||'').length>60?'...':''}</span>
        </div>`
      ).join('');
  });
}

// Refresh thread list on load and periodically
refreshThreadList();
setInterval(refreshThreadList, 10000);
</script>
</body>
</html>
"""


def _snapshot_json(events_limit: int = 50) -> dict:
    """Collect all data for the dashboard API response."""
    loop = _read_loop_state()
    hb = _read_heartbeat()
    outcomes = _read_recent_outcomes(limit=15)
    mem = _read_memory_stats()
    diagnoses = _read_recent_diagnoses(limit=8)
    cost = _read_cost_summary(hours=24)
    ancestry = _read_ancestry_tree()
    scheduler = _read_slow_scheduler()
    eval_trend = _read_eval_trend(limit=10)
    captain_log = _read_captain_log_entries(limit=20)
    suggestion_stats = _read_suggestion_stats()

    events: List[dict] = []
    path = _events_path()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            pass
    events = events[-events_limit:]

    return {
        "loop": loop,
        "heartbeat": hb,
        "outcomes": outcomes,
        "memory": mem,
        "diagnoses": diagnoses,
        "cost": cost,
        "ancestry": ancestry,
        "scheduler": scheduler,
        "events": events,
        "eval_trend": eval_trend,
        "captain_log": captain_log,
        "suggestion_stats": suggestion_stats,
    }


def serve_dashboard(host: str = "0.0.0.0", port: int = 7700) -> None:
    """Serve the live dashboard over HTTP using stdlib only.

    GET /          → HTML dashboard (auto-refreshes every 5s via JS)
    GET /api/snapshot → JSON snapshot (loop + heartbeat + events + outcomes + memory)

    No external dependencies. Runs until Ctrl-C.
    """
    import http.server
    import threading

    html_bytes = _DASHBOARD_HTML.encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # type: ignore[override]
            pass  # silence access log

        def _send_json(self, status: int, data: dict) -> None:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/" or self.path == "/index.html":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_bytes)))
                self.end_headers()
                self.wfile.write(html_bytes)
            elif self.path.startswith("/api/snapshot"):
                try:
                    self._send_json(200, _snapshot_json())
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            elif self.path == "/api/threads":
                try:
                    from conversation import list_channels
                    self._send_json(200, {"threads": list_channels()})
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            elif self.path.startswith("/api/thread/"):
                try:
                    # parse handle_id and optional since= param
                    import urllib.parse as _up
                    _parts = self.path.split("?", 1)
                    _handle_id = _parts[0][len("/api/thread/"):]
                    _since = 0
                    if len(_parts) > 1:
                        _qs = dict(_up.parse_qsl(_parts[1]))
                        try:
                            _since = int(_qs.get("since", 0))
                        except (ValueError, TypeError):
                            _since = 0
                    from conversation import get_channel
                    _ch = get_channel(_handle_id)
                    if _ch is None:
                        self._send_json(404, {"error": "thread not found"})
                        return
                    self._send_json(200, {
                        "events": _ch.events_since(_since),
                        "waiting": _ch.waiting_for_reply,
                        "status": _ch.status,
                    })
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/api/replay":
                # Re-run the last completed outcome's goal in a background thread.
                outcomes = _read_recent_outcomes(limit=1)
                if not outcomes:
                    self._send_json(404, {"error": "no outcomes to replay"})
                    return
                goal = outcomes[0].get("goal") or outcomes[0].get("task", "")
                if not goal:
                    self._send_json(400, {"error": "last outcome has no goal field"})
                    return
                def _run() -> None:
                    try:
                        import sys as _sys
                        _sys.path.insert(0, str(Path(__file__).parent))
                        import orch  # noqa: F401 — sets up path
                        from handle import handle
                        handle(goal, dry_run=False, verbose=True)
                    except Exception as exc:
                        import traceback
                        traceback.print_exc()
                threading.Thread(target=_run, daemon=True).start()
                self._send_json(202, {"queued": True, "goal": goal})
            elif self.path == "/api/replay-factory":
                # Factory mode: run evolver signal scan on recent outcomes,
                # then queue the highest-confidence suggested sub-goals as new missions.
                # This closes the Mode 2 → Mode 3 loop: system proposes its own next work.
                outcomes_raw = _read_recent_outcomes(limit=10)
                if not outcomes_raw:
                    self._send_json(404, {"error": "no outcomes to scan"})
                    return
                def _run_factory() -> None:
                    try:
                        import sys as _sys
                        _sys.path.insert(0, str(Path(__file__).parent))
                        from memory import load_outcomes, Outcome
                        from evolver import scan_outcomes_for_signals
                        from handle import handle
                        outcomes = load_outcomes(limit=10)
                        signals = scan_outcomes_for_signals(outcomes, min_confidence=0.70)
                        if not signals:
                            print("[factory-replay] no signals found from recent outcomes",
                                  flush=True)
                            return
                        for sig in signals[:3]:  # cap at 3 to avoid token runaway
                            print(f"[factory-replay] queuing: {sig.suggested_goal[:80]}",
                                  flush=True)
                            try:
                                handle(sig.suggested_goal, dry_run=False, verbose=True)
                            except Exception as _sig_exc:
                                print(f"[factory-replay] goal failed: {_sig_exc}", flush=True)
                    except Exception as exc:
                        import traceback
                        traceback.print_exc()
                n_outcomes = len(outcomes_raw)
                threading.Thread(target=_run_factory, daemon=True).start()
                self._send_json(202, {"queued": True, "mode": "factory",
                                      "outcomes_scanned": n_outcomes})
            elif self.path == "/api/submit":
                try:
                    _length = int(self.headers.get("Content-Length", 0))
                    _body = json.loads(self.rfile.read(_length).decode("utf-8"))
                    _goal = _body.get("goal", "").strip()
                    _project = _body.get("project", None) or None
                    if not _goal:
                        self._send_json(400, {"error": "goal is required"})
                        return
                    import uuid as _uuid
                    _handle_id = _uuid.uuid4().hex[:12]
                    from conversation import create_channel
                    _channel = create_channel(_handle_id, _goal)
                    def _run_goal() -> None:
                        try:
                            import sys as _sys
                            _sys.path.insert(0, str(Path(__file__).parent))
                            from handle import handle as _handle
                            import inspect as _inspect
                            _sig = _inspect.signature(_handle)
                            if "channel" in _sig.parameters:
                                _hr = _handle(_goal, project=_project, verbose=True,
                                              channel=_channel)
                            else:
                                _channel.emit("step", text="Starting goal execution...")
                                _hr = _handle(_goal, project=_project, verbose=True)
                            _channel.complete(_hr.result if _hr else "[done]")
                        except Exception as _exc:
                            _channel.emit("error", text=str(_exc))
                            _channel.status = "error"
                    threading.Thread(target=_run_goal, daemon=True).start()
                    self._send_json(202, {"handle_id": _handle_id, "status": "running"})
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            elif self.path.startswith("/api/reply/"):
                try:
                    _handle_id = self.path[len("/api/reply/"):]
                    _length = int(self.headers.get("Content-Length", 0))
                    _body = json.loads(self.rfile.read(_length).decode("utf-8"))
                    _text = _body.get("text", "").strip()
                    if not _text:
                        self._send_json(400, {"error": "text is required"})
                        return
                    from conversation import get_channel
                    _ch = get_channel(_handle_id)
                    if _ch is None:
                        self._send_json(404, {"error": "thread not found"})
                        return
                    _ch.receive_reply(_text)
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
            else:
                self.send_response(404)
                self.end_headers()

    server = http.server.HTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"Poe Command Center → {url}")
    print("Ctrl-C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="poe-observe",
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
    p_serve = sub.add_parser("serve", help="Live HTTP dashboard (default http://127.0.0.1:7700)")
    p_serve.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0 — all interfaces)")
    p_serve.add_argument("--port", type=int, default=7700, help="Port (default: 7700)")

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
        serve_dashboard(host=args.host, port=args.port)
    else:
        print_snapshot()


if __name__ == "__main__":
    main()
