"""Execution snapshot — Phase 23 first cut.

poe-observe              → full snapshot (loop state, heartbeat, recent outcomes, audit tail)
poe-observe loop         → active goal / loop lock only
poe-observe heartbeat    → heartbeat health only
poe-observe outcomes     → recent task outcomes
poe-observe audit        → sandbox audit log tail
poe-observe memory       → memory tier stats (same data as Stage 2 of poe-knowledge status)

All reads are local JSONL/JSON — no LLM calls, no side effects.
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
    try:
        from orch import orch_root
        return orch_root() / "memory"
    except Exception:
        pass
    try:
        from config import memory_dir
        return memory_dir()
    except Exception:
        return Path.home() / ".poe" / "workspace" / "memory"


def _loop_lock_path() -> Path:
    return _memory_dir() / "loop.lock"


def _heartbeat_path() -> Path:
    return _memory_dir() / "heartbeat-state.json"


def _outcomes_path() -> Path:
    return _memory_dir() / "outcomes.jsonl"


def _audit_path() -> Path:
    return _memory_dir() / "sandbox-audit.jsonl"


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


def _read_memory_stats() -> Dict[str, Any]:
    try:
        from memory import memory_status
        return memory_status()
    except Exception as e:
        return {"error": str(e)}


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
    else:
        print_snapshot()


if __name__ == "__main__":
    main()
