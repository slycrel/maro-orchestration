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
    status: str           # "healthy" | "warning" | "stuck" | "dormant" | "failed" | "paused" | "unknown"
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


_FAILED_MARKER = ".maro-failed"
_PAUSED_MARKER = ".maro-paused"


def project_lifecycle_state(slug: str) -> str:
    """Return 'failed' | 'paused' | 'active' based on marker files.

    Markers (`.maro-failed` / `.maro-paused`, project-dir-relative) are
    manual: no code path in this repo writes them automatically. An operator
    creates one directly (`touch <project_dir>/.maro-failed`) to pull a
    project out of sheriff/backlog-drain/heartbeat rotation.
    """
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


# Days without any file activity before a project counts as dormant.
# Dormant projects are excluded from stuck/warning classification — nothing is
# working on them, so tier-2 diagnosis and tier-3 escalation must not spend on
# them every heartbeat tick (BACKLOG #21: ~230 accumulated test projects were
# being re-diagnosed — the June-21 cron-tokenburn class).
DORMANT_DAYS_DEFAULT = 14.0


def _dormant_days() -> float:
    """Dormancy threshold in days; 0 disables the check."""
    try:
        from config import get
        return float(get("sheriff.dormant_days", DORMANT_DAYS_DEFAULT) or 0)
    except Exception:
        return DORMANT_DAYS_DEFAULT


def project_activity_age_days(slug: str) -> Optional[float]:
    """Days since the last file activity in a project dir (None = unknown).

    Activity = newest mtime among the project dir itself, NEXT.md,
    DECISIONS.md, and artifacts/ (dir + a bounded sample of entries).
    Deliberately a cheap stat scan — this runs for every project on every
    heartbeat tick.
    """
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import project_dir
    try:
        proj_dir = project_dir(slug)
        if not proj_dir.exists():
            return None
        candidates = [proj_dir, proj_dir / "NEXT.md", proj_dir / "DECISIONS.md"]
        artifacts = proj_dir / "artifacts"
        if artifacts.exists():
            candidates.append(artifacts)
            candidates.extend(sorted(artifacts.iterdir())[:50])
        newest = 0.0
        for p in candidates:
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
        if newest <= 0:
            return None
        return (time.time() - newest) / 86400.0
    except Exception:
        return None


def check_project(slug: str, *, window_minutes: int = 30) -> SheriffReport:
    """Check a single project for loop health.

    Checks:
    0. Lifecycle markers: .maro-failed → status=failed (skip all other checks)
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
                diagnosis="Marked failed (.maro-failed)",
                evidence=[],
            )
        if _lc == "paused":
            return SheriffReport(
                project=slug,
                status="paused",
                diagnosis="Marked paused (.maro-paused)",
                evidence=[],
            )

        # Dormancy — no file activity for sheriff.dormant_days (0 disables).
        # A dormant project is not "stuck": nothing is working on it, so it
        # must not draw diagnosis spend or escalation noise every tick.
        _dd = _dormant_days()
        if _dd > 0:
            _age = project_activity_age_days(slug)
            if _age is not None and _age > _dd:
                return SheriffReport(
                    project=slug,
                    status="dormant",
                    diagnosis=(f"No file activity in {_age:.0f}d (>{_dd:g}d) — "
                               "excluded from diagnosis/escalation"),
                    evidence=[],
                    recommended_action=("Archive with `maro sheriff archive --apply`, "
                                        "or touch a project file to reactivate"),
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
    """Check all projects in the workspace.

    Skips `_`/`.`-prefixed dirs — `projects/_archive/` (the archive sweep's
    target) and hidden dirs are not projects.
    """
    try:
        from orch import projects_root
        projects_dir = projects_root()
        if not projects_dir.exists():
            return []
        slugs = [d.name for d in projects_dir.iterdir()
                 if d.is_dir() and not d.name.startswith((".", "_"))]
        return [check_project(slug, window_minutes=window_minutes) for slug in sorted(slugs)]
    except Exception as exc:
        return [SheriffReport(
            project="*",
            status="unknown",
            diagnosis=f"Could not enumerate projects: {exc}",
            evidence=[],
        )]


def archive_dormant_projects(*, days: float = 30.0, apply: bool = False) -> List[Dict[str, Any]]:
    """List (and optionally move) dormant projects to `projects/_archive/`.

    Manual hygiene op (`maro sheriff archive`) — never called from automated
    paths, so an off switch stays off. Dry-run by default; `apply=True`
    performs the moves. Lifecycle markers travel with the dir; archived
    projects disappear from check_all_projects and backlog enumeration.
    """
    import shutil
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from orch import projects_root

    out: List[Dict[str, Any]] = []
    projects_dir = projects_root()
    if not projects_dir.exists():
        return out
    archive_root = projects_dir / "_archive"
    for d in sorted(projects_dir.iterdir()):
        if not d.is_dir() or d.name.startswith((".", "_")):
            continue
        age = project_activity_age_days(d.name)
        if age is None or age <= days:
            continue
        entry: Dict[str, Any] = {"project": d.name, "age_days": round(age, 1), "moved": False}
        if apply:
            archive_root.mkdir(exist_ok=True)
            target = archive_root / d.name
            if target.exists():
                target = archive_root / f"{d.name}-{int(time.time())}"
            shutil.move(str(d), str(target))
            entry["moved"] = True
            entry["target"] = str(target)
        out.append(entry)
    return out


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

    # Check 2: Python packages (requests backs telegram/notify paths).
    # The anthropic SDK is deliberately NOT checked here: a box on the
    # claude-CLI subprocess lane never needs it — lane health is check 4.
    for pkg in ["requests"]:
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

    # Check 4: LLM backend lane — lane-aware (BACKLOG #21). Warn only when NO
    # viable lane exists: an API-key check alone marks a healthy claude-CLI
    # subprocess box as degraded. llm.detect_backends() is the single source
    # of truth (same predicates build_adapter's auto-detect walk uses).
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).parent))
        import llm as _llm
        lanes = _llm.detect_backends()
        usable = [name for name, ok, _ in lanes if ok]
        if usable:
            checks["llm_backend"] = f"ok: {', '.join(usable)}"
        else:
            checks["llm_backend"] = "warn: no viable LLM backend lane — " + "; ".join(
                f"{name}: {detail}" for name, _ok, detail in lanes)
    except Exception as exc:
        checks["llm_backend"] = f"warn: backend detection failed: {exc}"

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


# ---------------------------------------------------------------------------
# Heartbeat state persistence
# ---------------------------------------------------------------------------

def write_heartbeat_state(health: SystemHealth, *, project_reports: Optional[List[SheriffReport]] = None):
    """Write heartbeat state to memory/heartbeat-state.json."""
    try:
        from orch import memory_dir
        state_path = memory_dir() / "heartbeat-state.json"

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
        from orch import memory_dir
        state_path = memory_dir() / "heartbeat-state.json"
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

    p_arch = sub.add_parser("archive", help="Archive dormant projects to projects/_archive/ (dry-run unless --apply)")
    p_arch.add_argument("--days", type=float, default=30.0, help="Dormancy threshold in days (default 30)")
    p_arch.add_argument("--apply", action="store_true", help="Actually move dirs; default is dry-run")
    p_arch.add_argument("--format", choices=["text", "json"], default="text")

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

    if args.cmd == "archive":
        rows = archive_dormant_projects(days=args.days, apply=args.apply)
        if args.format == "json":
            print(json.dumps(rows, indent=2))
        else:
            if not rows:
                print(f"No projects dormant >{args.days:g}d.")
            for r in rows:
                verb = "archived" if r["moved"] else "would archive"
                print(f"{verb}: {r['project']} (idle {r['age_days']}d)")
            if rows and not args.apply:
                print(f"\nDry run — re-run with --apply to move {len(rows)} project(s) to projects/_archive/")
        return 0

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
