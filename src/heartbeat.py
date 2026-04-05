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
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("poe.heartbeat")

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
    quality_summary: str = ""                 # from inspector.get_friction_summary() (Phase 12)

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
            "quality_summary": self.quality_summary,
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

    log.info("heartbeat_start run_id=%s dry_run=%s", run_id, dry_run)
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

    # --- Phase 12: Inspector quality summary (read-only, never affects execution) ---
    try:
        from inspector import get_friction_summary as _get_friction_summary
        report.quality_summary = _get_friction_summary()
    except Exception:
        pass  # Inspector failures must never affect heartbeat

    # --- Persist state ---
    if not dry_run:
        try:
            if write_heartbeat_state:
                write_heartbeat_state(health, project_reports=project_reports)
        except Exception:
            pass
        _log_heartbeat(report)

    report.elapsed_ms = int((time.monotonic() - started) * 1000)

    _escalated = sum(1 for a in report.recovery_actions if a.outcome == "escalated")
    log.info("heartbeat_done run_id=%s health=%s stuck=%d escalated=%d elapsed=%dms",
             run_id, health.status, len(stuck_projects), _escalated, report.elapsed_ms)
    if verbose:
        print(f"[heartbeat] done elapsed_ms={report.elapsed_ms}", file=sys.stderr)

    return report


# ---------------------------------------------------------------------------
# Poll loop
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Autonomous backlog drain
# ---------------------------------------------------------------------------

_backlog_drain_active = False
_backlog_drain_lock = threading.Lock()

_task_store_drain_active = False
_task_store_drain_lock = threading.Lock()

_evolver_active = False
_evolver_lock = threading.Lock()

_inspector_active = False
_inspector_lock = threading.Lock()

_eval_active = False
_eval_lock = threading.Lock()

_harness_optimizer_active = False
_harness_optimizer_lock = threading.Lock()

# SlowUpdateScheduler: gates heavy background LLM work to idle windows.
# Exposed at module level so poe-doctor can query its status.
try:
    from slow_update_scheduler import SlowUpdateScheduler as _SlowUpdateScheduler
    _slow_update_sched = _SlowUpdateScheduler(idle_cooldown=30)
except ImportError:  # pragma: no cover
    _slow_update_sched = None  # type: ignore[assignment]


def _run_harness_optimizer_bg(*, dry_run: bool = False, verbose: bool = False) -> None:
    """Run harness optimizer in background thread. Clears flag in finally."""
    global _harness_optimizer_active
    try:
        from harness_optimizer import run_harness_optimizer
        report = run_harness_optimizer(dry_run=dry_run, verbose=verbose)
        if verbose:
            print(f"[heartbeat] harness_optimizer: {report.summary()}", file=sys.stderr)
    except Exception as e:
        if verbose:
            print(f"[heartbeat] harness_optimizer failed: {e}", file=sys.stderr)
    finally:
        with _harness_optimizer_lock:
            _harness_optimizer_active = False
        if _slow_update_sched is not None:
            _slow_update_sched.finish_work()


def _run_evolver_bg(*, dry_run: bool = False, verbose: bool = False,
                    escalate: bool = True) -> None:
    """Run evolver in background thread. Clears flag in finally."""
    global _evolver_active
    try:
        from evolver import run_evolver
        run_evolver(dry_run=dry_run, verbose=verbose, notify=escalate, min_outcomes=5)
    except Exception as e:
        if verbose:
            print(f"[heartbeat] evolver failed: {e}", file=sys.stderr)
    finally:
        with _evolver_lock:
            _evolver_active = False
        if _slow_update_sched is not None:
            _slow_update_sched.finish_work()


def _run_inspector_bg(*, dry_run: bool = False, verbose: bool = False) -> None:
    """Run inspector in background thread. Clears flag in finally."""
    global _inspector_active
    try:
        from inspector import run_inspector
        run_inspector(dry_run=dry_run, verbose=verbose)
    except Exception as e:
        if verbose:
            print(f"[heartbeat] inspector failed: {e}", file=sys.stderr)
    finally:
        with _inspector_lock:
            _inspector_active = False
        if _slow_update_sched is not None:
            _slow_update_sched.finish_work()


def _run_eval_bg(*, dry_run: bool = False, verbose: bool = False) -> None:
    """Run nightly eval in background thread. Clears flag in finally."""
    global _eval_active
    try:
        from eval import run_nightly_eval
        n_regressions = run_nightly_eval(dry_run=dry_run, verbose=verbose)
        if n_regressions and verbose:
            print(f"[heartbeat] nightly eval: {n_regressions} regression suggestion(s)",
                  file=sys.stderr)
    except Exception as e:
        if verbose:
            print(f"[heartbeat] nightly eval failed: {e}", file=sys.stderr)
    finally:
        with _eval_lock:
            _eval_active = False
        if _slow_update_sched is not None:
            _slow_update_sched.finish_work()


def _run_task_store_drain(*, dry_run: bool = False, verbose: bool = False) -> None:
    """Drain loop_continuation and loop_escalation tasks from the task store.

    Called from a daemon thread by heartbeat_loop. Each call processes up to
    max_tasks tasks. The thread flag prevents stacking — if a prior drain is
    still running (e.g. a slow continuation loop), the next heartbeat tick skips.
    """
    global _task_store_drain_active
    try:
        from handle import drain_task_store
        n = drain_task_store(dry_run=dry_run, verbose=verbose, max_tasks=3)
        if n and verbose:
            print(f"[heartbeat] task store drain: processed {n} task(s)", file=sys.stderr)
    except Exception as exc:
        if verbose:
            print(f"[heartbeat] task store drain failed: {exc}", file=sys.stderr)
    finally:
        with _task_store_drain_lock:
            _task_store_drain_active = False


def _run_backlog_step(*, dry_run: bool = False, verbose: bool = False) -> None:
    """Pick the highest-priority NEXT.md TODO and run one agent loop iteration.

    Called from a daemon thread by heartbeat_loop. Marks items DOING on start,
    DONE on loop success, BLOCKED on loop failure (to prevent infinite retry).
    """
    global _backlog_drain_active
    try:
        from orch_items import (
            select_global_next,
            mark_item,
            STATE_DOING,
            STATE_DONE,
            STATE_BLOCKED,
            STATE_TODO,
        )

        result = select_global_next()
        if result is None:
            if verbose:
                print("[heartbeat] backlog drain: no TODO items found", file=sys.stderr)
            return

        slug, item = result
        if verbose:
            print(
                f"[heartbeat] backlog drain: [{slug}] {item.text[:80]}",
                file=sys.stderr,
            )

        # Claim the item by marking it in-progress
        try:
            mark_item(slug, item.index, STATE_DOING)
        except Exception as exc:
            print(f"[heartbeat] backlog drain mark_doing failed: {exc}", file=sys.stderr)
            return

        if dry_run:
            # In dry-run, immediately mark done — used by tests to validate wiring
            mark_item(slug, item.index, STATE_DONE)
            if verbose:
                print(f"[heartbeat] backlog drain: dry-run — marked done [{slug}] item {item.index}", file=sys.stderr)
            return

        try:
            from agent_loop import run_agent_loop
            loop_result = run_agent_loop(
                goal=item.text,
                project=slug,
                dry_run=False,
                verbose=verbose,
            )
            if loop_result.status == "done":
                mark_item(slug, item.index, STATE_DONE)
            else:
                # stuck/error → block so we don't retry the same item on every tick
                mark_item(slug, item.index, STATE_BLOCKED)
                if verbose:
                    print(
                        f"[heartbeat] backlog drain: loop ended {loop_result.status!r} "
                        f"for [{slug}] item {item.index} — marked blocked",
                        file=sys.stderr,
                    )
        except Exception as exc:
            print(f"[heartbeat] backlog drain loop failed: {exc}", file=sys.stderr)
            try:
                mark_item(slug, item.index, STATE_BLOCKED)
            except Exception:
                pass
    finally:
        with _backlog_drain_lock:
            _backlog_drain_active = False


def heartbeat_loop(
    *,
    interval: float = 60.0,
    evolver_every: int = 10,
    inspector_every: int = 20,
    mission_check_every: int = 5,
    backlog_every: int = 3,       # autonomous NEXT.md drain every N ticks
    eval_every: int = 1440,   # Phase 42: ~24h at 60s interval
    dry_run: bool = False,
    verbose: bool = True,
    escalate: bool = True,
) -> None:
    """Run heartbeat on a fixed interval forever.

    Every `evolver_every` heartbeat cycles, also runs the meta-evolver
    to analyze recent outcomes and propose improvements.

    Every `inspector_every` cycles (Phase 12), runs the quality inspector
    to detect friction patterns and feed suggestions to the evolver.

    Every `mission_check_every` cycles (Phase 34), checks for pending
    missions and logs/notifies if autonomous drain would be warranted.

    Every `backlog_every` cycles, picks the highest-priority NEXT.md TODO
    item across all projects and runs it via run_agent_loop (autonomous
    backlog drain). Skipped if a mission drain or prior backlog drain is
    already active. This is the primary mechanism for duty-cycle > 0 when
    no missions are explicitly queued.

    Every `eval_every` cycles (Phase 42, default ~24h), runs the eval suite
    and converts any regressions to evolver Suggestion entries.

    Every `evolver_every * 5` cycles, runs the harness optimizer to propose
    word-level improvements to EXECUTE_SYSTEM/DECOMPOSE_SYSTEM based on stuck traces.
    """
    if verbose:
        print(
            f"[heartbeat] loop started interval={interval}s "
            f"evolver_every={evolver_every} inspector_every={inspector_every} "
            f"mission_check_every={mission_check_every} backlog_every={backlog_every}",
            file=sys.stderr,
        )
    global _evolver_active, _inspector_active, _backlog_drain_active
    global _task_store_drain_active, _eval_active, _harness_optimizer_active

    # Phase 41 step 7 — MCP server init: load servers listed in user/CONFIG.md
    try:
        _cfg_path = Path(__file__).resolve().parent.parent / "user" / "CONFIG.md"
        _mcp_raw = ""
        if _cfg_path.exists():
            for _line in _cfg_path.read_text(encoding="utf-8").splitlines():
                _line = _line.strip()
                if _line.startswith("#") or ":" not in _line:
                    continue
                _k, _, _v = _line.partition(":")
                if _k.strip() == "mcp_servers":
                    _mcp_raw = _v.split("#")[0].strip()
                    break
        if _mcp_raw:
            from tool_registry import registry as _registry
            for _entry in _mcp_raw.split(","):
                _entry = _entry.strip()
                if not _entry:
                    continue
                try:
                    _registry.load_mcp_server(_entry)
                    if verbose:
                        print(f"[heartbeat] MCP server loaded: {_entry!r}", file=sys.stderr)
                except Exception as _mcp_exc:
                    print(f"[heartbeat] MCP server load failed ({_entry!r}): {_mcp_exc}",
                          file=sys.stderr)
    except Exception as _cfg_exc:
        print(f"[heartbeat] MCP init error: {_cfg_exc}", file=sys.stderr)

    tick = 0
    while True:
        try:
            run_heartbeat(dry_run=dry_run, verbose=verbose, escalate=escalate)
        except Exception as e:
            print(f"[heartbeat] run failed: {e}", file=sys.stderr)
        tick += 1

        # SlowUpdateScheduler (MetaClaw steal): gate heavy background work to idle windows.
        # Check if a mission drain is currently running — if so, skip expensive LLM work
        # this tick to avoid competing with an active mission's token/API budget.
        _mission_active = False
        try:
            from mission import is_drain_running
            _mission_active = is_drain_running()
        except Exception:
            pass
        if _mission_active and verbose:
            print("[heartbeat] mission active — deferring heavy background work this tick",
                  file=sys.stderr)

        # SlowUpdateScheduler gate: advance state machine + check if idle window is open.
        # should_run() internally calls tick(), so we call it once and reuse the result.
        _can_run = (
            _slow_update_sched.should_run(is_busy=_mission_active)
            if _slow_update_sched is not None
            else not _mission_active
        )
        if verbose and _slow_update_sched is not None:
            _sus_state = _slow_update_sched.state.value
            if _sus_state != "WINDOW_OPEN" and not _mission_active:
                print(f"[heartbeat] SlowUpdateScheduler: {_sus_state} — heavy work deferred",
                      file=sys.stderr)

        if tick % evolver_every == 0 and _can_run:
            with _evolver_lock:
                _ev_running = _evolver_active
            if not _ev_running:
                with _evolver_lock:
                    _evolver_active = True
                if _slow_update_sched is not None:
                    _slow_update_sched.start_work()
                _et = threading.Thread(
                    target=_run_evolver_bg,
                    kwargs={"dry_run": dry_run, "verbose": verbose, "escalate": escalate},
                    daemon=True,
                    name="evolver-bg",
                )
                _et.start()
                if verbose:
                    print("[heartbeat] evolver started in background", file=sys.stderr)
            elif verbose:
                print("[heartbeat] evolver already active — skipping tick", file=sys.stderr)

        if tick % inspector_every == 0 and _can_run:
            # Phase 12: Inspector — quality oversight, separate from health (heartbeat)
            with _inspector_lock:
                _insp_running = _inspector_active
            if not _insp_running:
                with _inspector_lock:
                    _inspector_active = True
                if _slow_update_sched is not None:
                    _slow_update_sched.start_work()
                _it = threading.Thread(
                    target=_run_inspector_bg,
                    kwargs={"dry_run": dry_run, "verbose": verbose},
                    daemon=True,
                    name="inspector-bg",
                )
                _it.start()
                if verbose:
                    print("[heartbeat] inspector started in background", file=sys.stderr)
            elif verbose:
                print("[heartbeat] inspector already active — skipping tick", file=sys.stderr)
        if tick % mission_check_every == 0:
            # Phase 34: Check for pending missions and drain autonomously
            try:
                from mission import pending_missions, is_drain_running, drain_next_mission
                pending = pending_missions()
                if pending:
                    if verbose:
                        print(
                            f"[heartbeat] {len(pending)} mission(s) pending drain: "
                            + ", ".join(m.get("project", "?") for m in pending[:3]),
                            file=sys.stderr,
                        )
                    if not is_drain_running():
                        import threading as _threading
                        _drain_thread = _threading.Thread(
                            target=drain_next_mission,
                            kwargs={"verbose": verbose, "notify": escalate},
                            daemon=True,
                            name="mission-drain",
                        )
                        _drain_thread.start()
                        if verbose:
                            print("[heartbeat] mission drain started in background", file=sys.stderr)
            except Exception as e:
                if verbose:
                    print(f"[heartbeat] mission check failed: {e}", file=sys.stderr)
        # Autonomous backlog drain: pick up NEXT.md TODO items when idle.
        # Fires every `backlog_every` ticks. Skips if a mission or prior backlog
        # drain is already running (one active drain at a time).
        if tick % backlog_every == 0 and not _mission_active:
            with _backlog_drain_lock:
                _bd_running = _backlog_drain_active
            if not _bd_running:
                with _backlog_drain_lock:
                    _backlog_drain_active = True
                _bt = threading.Thread(
                    target=_run_backlog_step,
                    kwargs={"dry_run": dry_run, "verbose": verbose},
                    daemon=True,
                    name="backlog-drain",
                )
                _bt.start()
                if verbose:
                    print("[heartbeat] autonomous backlog drain started", file=sys.stderr)
            elif verbose:
                print("[heartbeat] backlog drain already active — skipping tick", file=sys.stderr)

        # Phase 42: nightly eval — run once per ~24h cycle (skip if mission active)
        if tick % eval_every == 0 and tick > 0 and _can_run:
            with _eval_lock:
                _eval_running = _eval_active
            if not _eval_running:
                with _eval_lock:
                    _eval_active = True
                if _slow_update_sched is not None:
                    _slow_update_sched.start_work()
                _evalt = threading.Thread(
                    target=_run_eval_bg,
                    kwargs={"dry_run": dry_run, "verbose": verbose},
                    daemon=True,
                    name="nightly-eval",
                )
                _evalt.start()
                if verbose:
                    print("[heartbeat] nightly eval started in background", file=sys.stderr)
            elif verbose:
                print("[heartbeat] nightly eval already active — skipping tick", file=sys.stderr)
        # Harness optimizer: propose word-level improvements to EXECUTE_SYSTEM/DECOMPOSE_SYSTEM.
        # Runs every ~50 heartbeats (evolver_every * 5) — staggered from evolver to spread load.
        if tick % (evolver_every * 5) == 0 and tick > 0 and _can_run:
            with _harness_optimizer_lock:
                _ho_running = _harness_optimizer_active
            if not _ho_running:
                with _harness_optimizer_lock:
                    _harness_optimizer_active = True
                if _slow_update_sched is not None:
                    _slow_update_sched.start_work()
                _hot = threading.Thread(
                    target=_run_harness_optimizer_bg,
                    kwargs={"dry_run": dry_run, "verbose": verbose},
                    daemon=True,
                    name="harness-optimizer-bg",
                )
                _hot.start()
                if verbose:
                    print("[heartbeat] harness optimizer started in background", file=sys.stderr)
            elif verbose:
                print("[heartbeat] harness optimizer already active — skipping tick", file=sys.stderr)

        # Cron persistence: check for due scheduler jobs every tick
        try:
            from scheduler import drain_due_jobs
            _n_due = drain_due_jobs(dry_run=dry_run, verbose=verbose)
            if _n_due and verbose:
                print(f"[heartbeat] scheduler: submitted {_n_due} job(s)", file=sys.stderr)
        except Exception as e:
            if verbose:
                print(f"[heartbeat] scheduler check failed: {e}", file=sys.stderr)

        # Task store drain: pick up loop_continuation and loop_escalation tasks every tick.
        # Runs in a background thread (via _run_task_store_drain) so a slow continuation loop
        # (which may run dozens of LLM iterations) doesn't block the heartbeat tick.
        # The _task_store_drain_active flag prevents stacking if a prior drain is still running.
        if not _mission_active:
            with _task_store_drain_lock:
                _ts_running = _task_store_drain_active
            if not _ts_running:
                with _task_store_drain_lock:
                    _task_store_drain_active = True
                _tst = threading.Thread(
                    target=_run_task_store_drain,
                    kwargs={"dry_run": dry_run, "verbose": verbose},
                    daemon=True,
                    name="task-store-drain",
                )
                _tst.start()
                if verbose:
                    print("[heartbeat] task store drain started", file=sys.stderr)
            elif verbose:
                print("[heartbeat] task store drain already active — skipping tick", file=sys.stderr)

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
