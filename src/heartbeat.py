"""Heartbeat — Phase 4 completion (ROADMAP.md §4)

Periodic health check + tiered self-healing for the Poe orchestration system.

Tiers of recovery:
  1. Scripted/deterministic fixes (disk cleanup hints, config validation)
  2. LLM-assisted diagnosis for stuck projects (uses cheap/alternative model)
  3. Telegram escalation (human action required)

Uses a separate, cheap LLM model so heartbeat works even when the primary
model is unavailable or misconfigured.

Usage:
    python3 heartbeat.py               # run once and exit
    python3 heartbeat.py --loop        # run forever (poll loop)
    python3 heartbeat.py --interval 60 # check every 60 seconds
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Module-level imports so tests can patch them cleanly
try:
    from sheriff import check_system_health, check_all_projects, write_heartbeat_state, SystemHealth, SheriffReport
except ImportError:  # pragma: no cover
    check_system_health = None  # type: ignore[assignment]
    check_all_projects = None  # type: ignore[assignment]
    write_heartbeat_state = None  # type: ignore[assignment]

try:
    from llm import build_adapter, MODEL_CHEAP, LLMMessage
except ImportError:  # pragma: no cover
    build_adapter = None  # type: ignore[assignment]

try:
    from orch import project_dir as _project_dir, parse_next
except ImportError:  # pragma: no cover
    parse_next = None  # type: ignore[assignment]

try:
    from telegram_listener import TelegramBot, _resolve_token, _resolve_allowed_chats
except ImportError:  # pragma: no cover
    TelegramBot = None  # type: ignore[assignment]
    _resolve_token = None  # type: ignore[assignment]
    _resolve_allowed_chats = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Recovery action data model
# ---------------------------------------------------------------------------

@dataclass
class RecoveryAction:
    tier: int            # 1 = scripted, 2 = llm-diagnosed, 3 = escalate
    target: str          # what this action targets (check name or project slug)
    action: str          # human-readable description of the action taken
    outcome: str         # "fixed" | "suggested" | "escalated" | "skipped"
    detail: str = ""


@dataclass
class HeartbeatReport:
    run_id: str
    checked_at: str
    health_status: str                        # "healthy" | "degraded" | "critical"
    checks: Dict[str, str]                    # from SystemHealth
    stuck_projects: List[str] = field(default_factory=list)
    recovery_actions: List[RecoveryAction] = field(default_factory=list)
    telegram_sent: bool = False
    elapsed_ms: int = 0

    def summary(self) -> str:
        lines = [
            f"heartbeat run_id={self.run_id}",
            f"health={self.health_status}",
            f"stuck_projects={self.stuck_projects or 'none'}",
            f"recovery_actions={len(self.recovery_actions)}",
            f"telegram_sent={self.telegram_sent}",
            f"elapsed_ms={self.elapsed_ms}",
        ]
        for ra in self.recovery_actions:
            lines.append(f"  [tier{ra.tier}] {ra.target}: {ra.action} → {ra.outcome}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "checked_at": self.checked_at,
            "health_status": self.health_status,
            "checks": self.checks,
            "stuck_projects": self.stuck_projects,
            "recovery_actions": [
                {"tier": a.tier, "target": a.target, "action": a.action,
                 "outcome": a.outcome, "detail": a.detail}
                for a in self.recovery_actions
            ],
            "telegram_sent": self.telegram_sent,
            "elapsed_ms": self.elapsed_ms,
        }


# ---------------------------------------------------------------------------
# Tier 1: Scripted recovery
# ---------------------------------------------------------------------------

def _tier1_scripted(checks: Dict[str, str]) -> List[RecoveryAction]:
    """Deterministic recovery for known failure patterns."""
    actions: List[RecoveryAction] = []

    for name, detail in checks.items():
        if detail.startswith("fail") or detail.startswith("warn"):
            if name == "disk_space":
                # Suggest cleanup — can't do it autonomously without knowing what to delete
                actions.append(RecoveryAction(
                    tier=1,
                    target=name,
                    action="Disk space low — recommend: rm old logs in workspace/output/",
                    outcome="suggested",
                    detail=detail,
                ))
            elif name == "api_key":
                actions.append(RecoveryAction(
                    tier=1,
                    target=name,
                    action="API key missing — will fall back to claude subprocess adapter",
                    outcome="suggested",
                    detail=detail,
                ))
            elif name == "openclaw_gateway":
                actions.append(RecoveryAction(
                    tier=1,
                    target=name,
                    action="OpenClaw gateway unreachable — check if openclaw service is running",
                    outcome="suggested",
                    detail=detail,
                ))
            elif name == "workspace_writable":
                actions.append(RecoveryAction(
                    tier=1,
                    target=name,
                    action="Workspace not writable — check filesystem permissions",
                    outcome="escalated",
                    detail=detail,
                ))

    return actions


# ---------------------------------------------------------------------------
# Tier 2: LLM-assisted diagnosis
# ---------------------------------------------------------------------------

_DIAGNOSIS_SYSTEM = """\
You are Poe's diagnostic agent. A project loop appears to be stuck or unhealthy.
Analyze the provided project state and suggest ONE specific recovery action.
Be brief and concrete. Respond in this format:
ACTION: <one sentence describing what should be done>
REASON: <one sentence explaining why>
CONFIDENCE: <high|medium|low>
"""


def _tier2_llm_diagnosis(stuck_projects: List[str], *, dry_run: bool = False) -> List[RecoveryAction]:
    """Use LLM (cheap model) to diagnose stuck projects."""
    if not stuck_projects or dry_run:
        if dry_run and stuck_projects:
            return [RecoveryAction(
                tier=2,
                target=p,
                action="[dry-run] LLM diagnosis skipped",
                outcome="skipped",
            ) for p in stuck_projects]
        return []

    actions: List[RecoveryAction] = []

    try:
        adapter = build_adapter(model=MODEL_CHEAP)
    except Exception as e:
        for project in stuck_projects:
            actions.append(RecoveryAction(
                tier=2, target=project,
                action="LLM adapter unavailable for diagnosis",
                outcome="skipped", detail=str(e),
            ))
        return actions

    for project in stuck_projects:
        try:
            _lines, items = parse_next(project)
            doing = [i.text for i in items if i.state == ">"]
            todo = [i.text for i in items if i.state == " "][:3]
            blocked = [i.text for i in items if i.state == "!"]

            state_summary = (
                f"Project: {project}\n"
                f"In-progress ({len(doing)}): {doing}\n"
                f"Next TODO ({len(todo)}): {todo}\n"
                f"Blocked ({len(blocked)}): {blocked}"
            )

            resp = adapter.complete(
                [
                    LLMMessage("system", _DIAGNOSIS_SYSTEM),
                    LLMMessage("user", f"Stuck project state:\n{state_summary}"),
                ],
                max_tokens=512,
                temperature=0.2,
            )
            actions.append(RecoveryAction(
                tier=2, target=project,
                action=resp.content.strip(),
                outcome="suggested",
            ))
        except Exception as e:
            actions.append(RecoveryAction(
                tier=2, target=project,
                action="diagnosis failed",
                outcome="skipped", detail=str(e),
            ))

    return actions


# ---------------------------------------------------------------------------
# Tier 3: Telegram escalation
# ---------------------------------------------------------------------------

def _tier3_escalate(report: HeartbeatReport) -> bool:
    """Send Telegram alert if health is critical or projects are stuck."""
    if report.health_status not in ("critical", "degraded") and not report.stuck_projects:
        return False

    lines = [f"🔔 Poe Heartbeat Alert — {report.health_status.upper()}"]
    if report.stuck_projects:
        lines.append(f"Stuck projects: {', '.join(report.stuck_projects)}")

    # Include tier-2 suggestions
    llm_actions = [a for a in report.recovery_actions if a.tier == 2 and a.outcome == "suggested"]
    for ra in llm_actions[:3]:
        lines.append(f"  [{ra.target}] {ra.action}")

    # Failed checks
    failed = {k: v for k, v in report.checks.items() if v.startswith("fail")}
    if failed:
        lines.append("Failed checks: " + ", ".join(failed.keys()))

    message = "\n".join(lines)

    try:
        token = _resolve_token()
        if not token:
            return False
        bot = TelegramBot(token)
        allowed = _resolve_allowed_chats()
        if not allowed:
            return False
        for chat_id in allowed:
            bot.send_message(chat_id, message)
        return True
    except Exception as e:
        print(f"[heartbeat] telegram escalation failed: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Heartbeat log
# ---------------------------------------------------------------------------

def _log_heartbeat(report: HeartbeatReport) -> Optional[str]:
    """Append heartbeat report to memory/heartbeat-log.jsonl."""
    try:
        from orch import orch_root
        log_path = orch_root() / "memory" / "heartbeat-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(report.to_dict()) + "\n")
        return str(log_path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Core heartbeat run
# ---------------------------------------------------------------------------

def run_heartbeat(
    *,
    dry_run: bool = False,
    verbose: bool = True,
    escalate: bool = True,
) -> HeartbeatReport:
    """Run one heartbeat cycle. Returns a HeartbeatReport."""
    import uuid as _uuid
    from datetime import datetime, timezone

    run_id = _uuid.uuid4().hex[:8]
    started = time.monotonic()
    checked_at = datetime.now(timezone.utc).isoformat()

    if verbose:
        print(f"[heartbeat] run_id={run_id} starting...", file=sys.stderr)

    # --- System health check (from sheriff) ---
    try:
        health = check_system_health()
        project_reports = check_all_projects() if not dry_run else []
    except Exception as e:
        # Sheriff unavailable — build minimal health report
        health = SystemHealth(
            status="critical",
            checks={"sheriff": f"fail: {e}"},
            checked_at=checked_at,
        )
        project_reports = []

    stuck_projects = [r.project for r in project_reports if r.status in ("stuck", "warning")]

    report = HeartbeatReport(
        run_id=run_id,
        checked_at=checked_at,
        health_status=health.status,
        checks=health.checks,
        stuck_projects=stuck_projects,
    )

    if verbose:
        print(f"[heartbeat] health={health.status} stuck={stuck_projects}", file=sys.stderr)

    # --- Tier 1: Scripted recovery ---
    tier1 = _tier1_scripted(health.checks)
    report.recovery_actions.extend(tier1)

    # --- Tier 2: LLM diagnosis for stuck projects ---
    tier2 = _tier2_llm_diagnosis(stuck_projects, dry_run=dry_run)
    report.recovery_actions.extend(tier2)

    # --- Tier 3: Escalate if needed ---
    if escalate and not dry_run:
        sent = _tier3_escalate(report)
        report.telegram_sent = sent
        if sent and verbose:
            print(f"[heartbeat] telegram alert sent", file=sys.stderr)

    # --- Persist state ---
    if not dry_run:
        try:
            if write_heartbeat_state:
                write_heartbeat_state(health, project_reports=project_reports)
        except Exception:
            pass
        _log_heartbeat(report)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)

    if verbose:
        print(f"[heartbeat] done elapsed_ms={report.elapsed_ms}", file=sys.stderr)

    return report


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

def heartbeat_loop(
    *,
    interval: float = 60.0,
    dry_run: bool = False,
    verbose: bool = True,
    escalate: bool = True,
) -> None:
    """Run heartbeat on a fixed interval forever."""
    if verbose:
        print(f"[heartbeat] loop started interval={interval}s", file=sys.stderr)
    while True:
        try:
            run_heartbeat(dry_run=dry_run, verbose=verbose, escalate=escalate)
        except Exception as e:
            print(f"[heartbeat] run failed: {e}", file=sys.stderr)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# CLI entry point (standalone)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Poe heartbeat")
    parser.add_argument("--loop", action="store_true", help="Run forever on an interval")
    parser.add_argument("--interval", type=float, default=60.0, help="Seconds between checks (default: 60)")
    parser.add_argument("--dry-run", action="store_true", help="Check without recovery or alerting")
    parser.add_argument("--no-escalate", action="store_true", help="Skip Telegram escalation")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args()

    if args.loop:
        heartbeat_loop(
            interval=args.interval,
            dry_run=args.dry_run,
            escalate=not args.no_escalate,
        )
    else:
        report = run_heartbeat(dry_run=args.dry_run, escalate=not args.no_escalate)
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
