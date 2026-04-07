#!/usr/bin/env python3
"""Phase 4: Loop Sheriff — independent progress validator.

The Sheriff monitors running loops and projects for genuine progress.
It detects when loops are spinning (repeated selection, no artifact changes,
no state changes) and triggers escalation.

Design principle from spec: "Validator-based, not count-based. Don't cap
iterations — detect when you're stuck."

Validation methods:
1. Artifact diff: are new artifacts being created? Do they differ from last run?
2. State diff: is NEXT.md / DECISIONS.md changing meaningfully?
3. Repetition: is the same project+task selected 3+ times in a short window?

Usage:
    from sheriff import check_loop, check_project
    report = check_project("polymarket-research")
    print(report.status, report.diagnosis)

CLI:
    orch sheriff --project SLUG
    orch sheriff --all
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SheriffReport:
    project: str
    status: str           # "healthy" | "warning" | "stuck" | "unknown"
    diagnosis: str        # Human-readable explanation
    evidence: List[str]   # Supporting observations
    recommended_action: Optional[str] = None
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def format(self, mode: str = "text") -> str:
        if mode == "json":
            return json.dumps({
                "project": self.project,
                "status": self.status,
                "diagnosis": self.diagnosis,
                "evidence": self.evidence,
                "recommended_action": self.recommended_action,
                "checked_at": self.checked_at,
            }, indent=2)
        lines = [
            f"project={self.project}",
            f"status={self.status}",
            f"diagnosis={self.diagnosis}",
        ]
        for e in self.evidence:
            lines.append(f"  evidence: {e}")
        if self.recommended_action:
            lines.append(f"action: {self.recommended_action}")
        return "\n".join(lines)


@dataclass
class SystemHealth:
    status: str               # "healthy" | "degraded" | "critical"
    checks: Dict[str, str]    # check_name → "ok" | "warn" | "fail" + detail
    checked_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def format(self, mode: str = "text") -> str:
        if mode == "json":
            return json.dumps({
                "status": self.status,
                "checks": self.checks,
                "checked_at": self.checked_at,
            }, indent=2)
        lines = [f"health={self.status}", f"checked_at={self.checked_at}"]
        for name, detail in self.checks.items():
            lines.append(f"  {name}: {detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Project progress checking
# ---------------------------------------------------------------------------

# How many times a project can be selected with no state change before stuck
STUCK_REPETITION_THRESHOLD = 3
# How many recent DECISIONS.md lines to consider "recent"
DECISION_WINDOW = 20


_FAILED_MARKER = ".poe-failed"
_PAUSED_MARKER = ".poe-paused"


def mark_project_failed(slug: str, reason: str = "") -> Path:
    """Write a .poe-failed marker in the project directory.

    Sheriff, backlog drain, and heartbeat diagnosis all skip failed projects.
    The marker persists until manually removed. Returns the marker path.
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import project_dir
    proj_dir = project_dir(slug)
    marker = proj_dir / _FAILED_MARKER
    content = f"failed: {reason}\n" if reason else "failed\n"
    marker.write_text(content, encoding="utf-8")
    return marker


def mark_project_paused(slug: str, reason: str = "") -> Path:
    """Write a .poe-paused marker — sheriff monitors but backlog drain skips."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import project_dir
    proj_dir = project_dir(slug)
    marker = proj_dir / _PAUSED_MARKER
    content = f"paused: {reason}\n" if reason else "paused\n"
    marker.write_text(content, encoding="utf-8")
    return marker


def project_lifecycle_state(slug: str) -> str:
    """Return 'failed' | 'paused' | 'active' based on marker files."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import project_dir
    try:
        proj_dir = project_dir(slug)
        if (proj_dir / _FAILED_MARKER).exists():
            return "failed"
        if (proj_dir / _PAUSED_MARKER).exists():
            return "paused"
    except Exception:
        pass
    return "active"


def check_project(slug: str, *, window_minutes: int = 30) -> SheriffReport:
    """Check a single project for loop health.

    Checks:
    0. Lifecycle markers: .poe-failed → status=failed (skip all other checks)
    1. Repetition: same TODO selected multiple times with no progress
    2. Artifact freshness: artifacts changing?
    3. Decision log freshness: new decisions being appended?

    Returns:
        SheriffReport with status and diagnosis.
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import orch_root, parse_next, project_dir, STATE_DOING, STATE_BLOCKED

    try:
        proj_dir = project_dir(slug)
        if not proj_dir.exists():
            return SheriffReport(
                project=slug,
                status="unknown",
                diagnosis="Project directory does not exist",
                evidence=[],
            )

        # Check lifecycle markers first — short-circuit before expensive checks
        _lc = project_lifecycle_state(slug)
        if _lc == "failed":
            return SheriffReport(
                project=slug,
                status="failed",
                diagnosis="Marked failed (.poe-failed)",
                evidence=[],
            )
        if _lc == "paused":
            return SheriffReport(
                project=slug,
                status="paused",
                diagnosis="Marked paused (.poe-paused)",
                evidence=[],
            )

        evidence: List[str] = []
        problems: List[str] = []

        # Check 1: Are there items stuck in "doing" state?
        _, items = parse_next(slug)
        doing_items = [i for i in items if i.state == STATE_DOING]
        blocked_items = [i for i in items if i.state == STATE_BLOCKED]
        todo_items = [i for i in items if i.state == " "]

        if doing_items:
            evidence.append(f"{len(doing_items)} item(s) stuck in 'doing' state: {[i.text for i in doing_items[:3]]}")
            problems.append("items_stuck_doing")

        if blocked_items:
            evidence.append(f"{len(blocked_items)} blocked item(s): {[i.text for i in blocked_items[:3]]}")

        if not todo_items and not doing_items:
            evidence.append("No TODO items remaining — project may be complete")

        # Check 2: Decision log freshness
        decisions_path = proj_dir / "DECISIONS.md"
        if decisions_path.exists():
            content = decisions_path.read_text(encoding="utf-8")
            lines = [l for l in content.splitlines() if l.strip()]
            recent = lines[-DECISION_WINDOW:]

            # Look for repeated patterns (same text appearing 3+ times)
            from collections import Counter
            counts = Counter(l.strip() for l in recent if l.strip())
            repeated = [(text, n) for text, n in counts.items() if n >= STUCK_REPETITION_THRESHOLD]
            if repeated:
                evidence.append(f"Repeated log entries ({len(repeated)} patterns): {repeated[0][0][:60]!r} x{repeated[0][1]}")
                problems.append("repeated_decisions")

        # Check 3: Artifact freshness
        artifacts_dir = proj_dir / "artifacts"
        if artifacts_dir.exists():
            artifact_files = sorted(artifacts_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
            if artifact_files:
                newest = artifact_files[0]
                age_s = time.time() - newest.stat().st_mtime
                age_min = age_s / 60
                evidence.append(f"Newest artifact: {newest.name} ({age_min:.0f}min ago)")
                if age_min > window_minutes and doing_items:
                    problems.append("artifact_stale")
                    evidence.append(f"Artifact is >{window_minutes}min old with items in progress — potential stall")
            else:
                if doing_items:
                    evidence.append("No artifacts produced despite items in progress")
                    problems.append("no_artifacts")

        # Determine status
        if not problems:
            if not todo_items and not doing_items:
                status = "healthy"
                diagnosis = "Project appears complete (no remaining TODO items)"
            else:
                status = "healthy"
                diagnosis = f"Project healthy: {len(todo_items)} todo, {len(doing_items)} doing"
            return SheriffReport(
                project=slug,
                status=status,
                diagnosis=diagnosis,
                evidence=evidence,
            )

        if "repeated_decisions" in problems or "items_stuck_doing" in problems:
            status = "stuck"
            diagnosis = "Loop detected: repeated decisions or items stuck in doing state"
            action = "Force-complete or skip stuck items: orch done " + slug
        elif "artifact_stale" in problems or "no_artifacts" in problems:
            status = "warning"
            diagnosis = "Potential stall: items in progress but no recent artifact activity"
            action = "Check execution bridge or re-run tick"
        else:
            status = "warning"
            diagnosis = "Anomalies detected; manual review recommended"
            action = "Review DECISIONS.md and NEXT.md"

        return SheriffReport(
            project=slug,
            status=status,
            diagnosis=diagnosis,
            evidence=evidence,
            recommended_action=action,
        )

    except Exception as exc:
        return SheriffReport(
            project=slug,
            status="unknown",
            diagnosis=f"Sheriff check failed: {exc}",
            evidence=[],
        )


def check_all_projects(*, window_minutes: int = 30) -> List[SheriffReport]:
    """Check all projects in the workspace."""
    try:
        from orch import orch_root
        projects_dir = orch_root() / "projects"
        if not projects_dir.exists():
            return []
        slugs = [d.name for d in projects_dir.iterdir() if d.is_dir()]
        return [check_project(slug, window_minutes=window_minutes) for slug in sorted(slugs)]
    except Exception as exc:
        return [SheriffReport(
            project="*",
            status="unknown",
            diagnosis=f"Could not enumerate projects: {exc}",
            evidence=[],
        )]


# ---------------------------------------------------------------------------
# Progress fingerprinting (for loop integration)
# ---------------------------------------------------------------------------

def fingerprint_project_state(slug: str) -> str:
    """Hash the current project state (NEXT.md + recent decisions).

    Use this at the start of a loop iteration and compare with the next
    iteration to detect no-progress.
    """
    try:
        from orch import project_dir
        proj_dir = project_dir(slug)
        parts = []

        next_path = proj_dir / "NEXT.md"
        if next_path.exists():
            parts.append(next_path.read_text(encoding="utf-8"))

        decisions_path = proj_dir / "DECISIONS.md"
        if decisions_path.exists():
            text = decisions_path.read_text(encoding="utf-8")
            parts.append(text[-2000:])  # last 2000 chars

        return hashlib.md5("\n".join(parts).encode()).hexdigest()
    except Exception:
        return ""


def detect_no_progress(fingerprints: List[str]) -> bool:
    """Return True if the last N fingerprints show no change.

    A fingerprint stream like [A, A, A] indicates stuck.
    A stream like [A, B, B] is warning (one step, then stuck).
    """
    if len(fingerprints) < STUCK_REPETITION_THRESHOLD:
        return False
    recent = fingerprints[-STUCK_REPETITION_THRESHOLD:]
    return len(set(recent)) == 1 and recent[0] != ""


# ---------------------------------------------------------------------------
# System health checks
# ---------------------------------------------------------------------------

def check_system_health() -> SystemHealth:
    """Check system health: workspace, Python packages, disk, processes.

    Returns:
        SystemHealth with per-check results and overall status.
    """
    checks: Dict[str, str] = {}

    # Check 1: orch root accessible and writable
    try:
        from orch import orch_root
        root = orch_root()
        if root.exists():
            test_path = root / ".sheriff-health-check"
            test_path.write_text("ok", encoding="utf-8")
            test_path.unlink()
            checks["workspace_writable"] = "ok"
        else:
            checks["workspace_writable"] = "fail: orch_root does not exist"
    except Exception as exc:
        checks["workspace_writable"] = f"fail: {exc}"

    # Check 2: Python packages
    for pkg in ["anthropic", "requests"]:
        try:
            __import__(pkg)
            checks[f"pkg_{pkg}"] = "ok"
        except ImportError:
            checks[f"pkg_{pkg}"] = f"warn: {pkg} not installed"

    # Check 3: Disk space (warn if < 500MB free)
    try:
        import shutil
        free_bytes = shutil.disk_usage("/").free
        free_mb = free_bytes // (1024 * 1024)
        if free_mb < 100:
            checks["disk_space"] = f"fail: {free_mb}MB free"
        elif free_mb < 500:
            checks["disk_space"] = f"warn: {free_mb}MB free"
        else:
            checks["disk_space"] = f"ok: {free_mb}MB free"
    except Exception as exc:
        checks["disk_space"] = f"warn: {exc}"

    # Check 4: API key available
    import os
    has_key = bool(
        os.environ.get("OPENROUTER_API_KEY") or
        os.environ.get("ANTHROPIC_API_KEY") or
        _read_env_file_key()
    )
    checks["api_key"] = "ok" if has_key else "warn: no API key found"

    # Check 5: OpenClaw gateway (optional — just check if accessible)
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", 18789))
        sock.close()
        checks["openclaw_gateway"] = "ok" if result == 0 else "warn: gateway not reachable"
    except Exception:
        checks["openclaw_gateway"] = "warn: gateway check failed"

    # Determine overall status
    fails = [k for k, v in checks.items() if v.startswith("fail")]
    warns = [k for k, v in checks.items() if v.startswith("warn")]

    if fails:
        status = "critical"
    elif warns:
        status = "degraded"
    else:
        status = "healthy"

    return SystemHealth(status=status, checks=checks)


def _read_env_file_key() -> Optional[str]:
    try:
        from config import credentials_env_file
        env_file = credentials_env_file()
    except Exception:
        env_file = Path.home() / ".poe" / "workspace" / "secrets" / ".env"
    if not env_file.exists():
        return None
    try:
        for line in env_file.read_text().splitlines():
            if "OPENROUTER_API_KEY=" in line or "ANTHROPIC_API_KEY=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Heartbeat state persistence
# ---------------------------------------------------------------------------

def write_heartbeat_state(health: SystemHealth, *, project_reports: Optional[List[SheriffReport]] = None):
    """Write heartbeat state to memory/heartbeat-state.json."""
    try:
        from orch import orch_root
        state_path = orch_root() / "memory" / "heartbeat-state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        stuck_projects = []
        if project_reports:
            stuck_projects = [r.project for r in project_reports if r.status in ("stuck", "warning")]

        payload = {
            "checked_at": health.checked_at,
            "system_status": health.status,
            "checks": health.checks,
            "stuck_projects": stuck_projects,
        }
        state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(state_path)
    except Exception:
        return None


def read_heartbeat_state() -> Optional[Dict[str, Any]]:
    """Read last heartbeat state."""
    try:
        from orch import orch_root
        state_path = orch_root() / "memory" / "heartbeat-state.json"
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(prog="orch-sheriff", description="Loop Sheriff — progress validator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="Check a project for loop health")
    p_check.add_argument("project", help="Project slug")
    p_check.add_argument("--window", type=int, default=30, help="Staleness window in minutes")
    p_check.add_argument("--format", choices=["text", "json"], default="text")

    p_all = sub.add_parser("all", help="Check all projects")
    p_all.add_argument("--window", type=int, default=30)
    p_all.add_argument("--format", choices=["text", "json"], default="text")

    p_health = sub.add_parser("health", help="Check system health")
    p_health.add_argument("--format", choices=["text", "json"], default="text")
    p_health.add_argument("--write-state", action="store_true", help="Write heartbeat state file")

    args = parser.parse_args(argv)

    if args.cmd == "check":
        report = check_project(args.project, window_minutes=args.window)
        print(report.format(args.format))
        return 0 if report.status in ("healthy",) else 1

    if args.cmd == "all":
        reports = check_all_projects(window_minutes=args.window)
        if args.format == "json":
            print(json.dumps([json.loads(r.format("json")) for r in reports], indent=2))
        else:
            for r in reports:
                print(r.format("text"))
                print()
        stuck = [r for r in reports if r.status in ("stuck", "warning")]
        return 1 if stuck else 0

    if args.cmd == "health":
        health = check_system_health()
        if args.write_state:
            reports = check_all_projects()
            write_heartbeat_state(health, project_reports=reports)
        print(health.format(args.format))
        return 0 if health.status == "healthy" else 1

    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
