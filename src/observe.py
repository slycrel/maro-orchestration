"""Execution snapshot — Phase 23 / Phase 36 event stream.

poe-observe              → full snapshot (loop state, heartbeat, recent outcomes, audit tail)
poe-observe loop         → active goal / loop lock only
poe-observe heartbeat    → heartbeat health only
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
        from orch_items import orch_root
        projects_root = orch_root() / "projects"
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


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

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
    print_recent_outcomes(limit=outcomes_limit)
    print()
    print_audit_tail(limit=audit_limit)
    print()
    print_memory_stats()
    print()
    print("──────────────────────────────────────────────────────")
    print("Tip: poe-observe loop | heartbeat | outcomes | audit | memory")
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
