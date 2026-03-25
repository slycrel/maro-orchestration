#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from orch import (
    append_decision,
    artifact_progress_validation_bridge,
    artifact_validation_bridge,
    chain_validation_bridges,
    command_execution_bridge,
    ensure_project,
    finalize_run,
    run_loop,
    session_execution_bridge,
    worker_session_bridge,
    x_capture_salvage_validation_bridge,
    load_run_record,
    load_validation_summary,
    list_blocked_projects,
    review_command_validation_bridge,
    mark_first_todo_done,
    mark_item,
    named_validation_bridge,
    operator_status_path,
    orch_root,
    project_dir,
    run_once,
    plan_project,
    run_tick,
    select_global_next,
    select_next_item,
    start_item,
    status_report_json,
    status_report_markdown,
    write_operator_status,
)


def fail(code: str, msg: str) -> int:
    print(f"ERROR[{code}] {msg}", file=sys.stderr)
    return 2


def _print_run(prefix: str, run) -> None:
    print(
        " ".join(
            [
                prefix,
                f"run_id={run.run_id}",
                f"project={run.project}",
                f"index={run.index}",
                f"status={run.status}",
                f"text={run.text}",
                *( [f"artifact={run.artifact_path}"] if run.artifact_path else [] ),
                *( [f"note={json.dumps(run.note)}"] if run.note else [] ),
            ]
        )
    )


def _load_salvage_summary(run):
    if not getattr(run, "artifact_path", None):
        return None
    path = Path(run.artifact_path) / "x-capture-salvage.json"
    root_path = orch_root() / path
    if not root_path.exists():
        return None
    try:
        payload = json.loads(root_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("path", str(path))
    return payload


def _build_validation(args):
    bridges = []
    has_execution_bridge = bool(
        getattr(args, "exec_cmd", None) or getattr(args, "session_cmd", None) or getattr(args, "worker_session", None)
    )
    if args.require_artifact:
        bridges.append(
            named_validation_bridge(
                "artifact-required",
                artifact_validation_bridge(args.require_artifact, nonempty=args.require_nonempty),
            )
        )
    if has_execution_bridge and not getattr(args, "disable_artifact_progress", False):
        bridges.append(
            named_validation_bridge(
                "artifact-progress",
                artifact_progress_validation_bridge(
                    history_size=max(1, getattr(args, "artifact_progress_window", 2)),
                    max_retry_attempts=max(1, getattr(args, "artifact_progress_max_attempts", 3)),
                ),
            )
        )
    if getattr(args, "review_cmd", None):
        bridges.append(
            named_validation_bridge(
                "review-command",
                review_command_validation_bridge(
                    args.review_cmd,
                    timeout_seconds=getattr(args, "review_timeout", None),
                ),
            )
        )
    if has_execution_bridge and not getattr(args, "disable_x_capture", False):
        bridges.append(named_validation_bridge("x-capture-salvage", x_capture_salvage_validation_bridge()))
    if not bridges:
        return None
    if len(bridges) == 1:
        return bridges[0]
    return chain_validation_bridges(*bridges)


def _build_execution(args):
    if args.exec_cmd and (args.session_cmd or args.worker_session):
        raise ValueError("only one of --exec-cmd, --session-cmd, or --worker-session can be set")
    if args.session_cmd and args.worker_session:
        raise ValueError("only one of --session-cmd or --worker-session can be set")
    if args.session_cmd:
        return session_execution_bridge(
            args.session_cmd,
            timeout_seconds=args.session_timeout,
        )
    if args.worker_session:
        return worker_session_bridge(args.worker_session, timeout_seconds=args.session_timeout)
    if args.exec_cmd:
        return command_execution_bridge(args.exec_cmd)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orch", description="File-first orchestration CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize a project")
    p_init.add_argument("slug")
    p_init.add_argument("mission", nargs="+")
    p_init.add_argument("--priority", type=int, default=0)

    p_next = sub.add_parser("next", help="Show next item")
    p_next.add_argument("--project")

    p_done = sub.add_parser("done", help="Mark task done")
    p_done.add_argument("project")
    p_done.add_argument("--index", type=int)

    p_log = sub.add_parser("log", help="Append decision log entry")
    p_log.add_argument("project")
    p_log.add_argument("message", nargs="+")

    p_enqueue = sub.add_parser("enqueue", help="Enqueue a project task into the workspace task queue")
    p_enqueue.add_argument("project")
    p_enqueue.add_argument("task", nargs="+")
    p_enqueue.add_argument("--lane", default="manual")
    p_enqueue.add_argument("--source", default="orch")
    p_enqueue.add_argument("--reason", default="queued from orch")
    p_enqueue.add_argument("--parent-job-id")

    p_blocked = sub.add_parser("blocked", help="List blocked projects")

    p_salvage = sub.add_parser("salvage", help="Show X-capture salvage status")
    p_salvage.add_argument("--format", choices=["text", "json"], default="text")

    p_report = sub.add_parser("report", help="Generate summary report")
    p_report.add_argument("--project")
    p_report.add_argument("--format", choices=["md", "json"], default="md")
    p_report.add_argument("--out")

    p_start = sub.add_parser("start", help="Claim the next TODO item and mark it in progress")
    p_start.add_argument("--project")
    p_start.add_argument("--index", type=int)
    p_start.add_argument("--worker", default="handle")
    p_start.add_argument("--source", default="manual")
    p_start.add_argument("--note")

    p_finish = sub.add_parser("finish", help="Finalize a running item")
    p_finish.add_argument("run_id")
    p_finish.add_argument("--status", choices=["done", "blocked"], default="done")
    p_finish.add_argument("--note")

    p_inspect = sub.add_parser("inspect-run", help="Show run record and validation summary")
    p_inspect.add_argument("run_id")
    p_inspect.add_argument("--format", choices=["json", "text"], default="text")

    p_run = sub.add_parser("run", help="Run one orchestration cycle")
    p_run.add_argument("--project")
    p_run.add_argument("--worker", default="handle")
    p_run.add_argument("--source", default="run-once")
    p_run.add_argument("--note")
    p_run.add_argument("--finish", choices=["done", "blocked"], help="Immediately finalize the claimed item")
    p_run.add_argument("--finish-note")

    p_memory = sub.add_parser("memory", help="Memory system — outcomes, lessons, session context (Phase 5)")
    memory_sub = p_memory.add_subparsers(dest="memory_cmd", required=True)
    memory_sub.add_parser("context", help="Print bootstrap context for current session")
    p_mem_outcomes = memory_sub.add_parser("outcomes", help="List recent outcomes")
    p_mem_outcomes.add_argument("--limit", type=int, default=10)
    p_mem_outcomes.add_argument("--format", choices=["text", "json"], default="text")
    p_mem_lessons = memory_sub.add_parser("lessons", help="List stored lessons")
    p_mem_lessons.add_argument("--type", dest="task_type", help="Filter by task type")
    p_mem_lessons.add_argument("--limit", type=int, default=10)
    p_mem_lessons.add_argument("--format", choices=["text", "json"], default="text")

    p_sheriff = sub.add_parser("sheriff", help="Loop Sheriff — check projects for stuck loops (Phase 4)")
    sheriff_sub = p_sheriff.add_subparsers(dest="sheriff_cmd", required=True)

    p_sheriff_check = sheriff_sub.add_parser("check", help="Check a project")
    p_sheriff_check.add_argument("project")
    p_sheriff_check.add_argument("--window", type=int, default=30)
    p_sheriff_check.add_argument("--format", choices=["text", "json"], default="text")

    p_sheriff_all = sheriff_sub.add_parser("all", help="Check all projects")
    p_sheriff_all.add_argument("--window", type=int, default=30)
    p_sheriff_all.add_argument("--format", choices=["text", "json"], default="text")

    p_sheriff_health = sheriff_sub.add_parser("health", help="System health check")
    p_sheriff_health.add_argument("--format", choices=["text", "json"], default="text")
    p_sheriff_health.add_argument("--write-state", action="store_true")

    p_poe_director = sub.add_parser("poe-director", help="Run Poe's Director/Worker hierarchy on a directive (Phase 3)")
    p_poe_director.add_argument("directive", nargs="+", help="The directive to execute")
    p_poe_director.add_argument("--project", "-p", help="Project slug")
    p_poe_director.add_argument("--model", "-m", help="LLM model string")
    p_poe_director.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    p_poe_director.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    p_poe_director.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_poe_handle = sub.add_parser("poe-handle", help="Send a request through Poe's handle (auto-routes NOW/AGENDA)")
    p_poe_handle.add_argument("message", nargs="+", help="The request to handle")
    p_poe_handle.add_argument("--project", "-p", help="Project slug for AGENDA work")
    p_poe_handle.add_argument("--model", "-m", help="LLM model string")
    p_poe_handle.add_argument("--lane", choices=["now", "agenda"], help="Force a specific lane")
    p_poe_handle.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    p_poe_handle.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    p_poe_handle.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_poe_run = sub.add_parser("poe-run", help="Run Poe's autonomous loop on a goal (Phase 1)")
    p_poe_run.add_argument("goal", nargs="+", help="Goal description")
    p_poe_run.add_argument("--project", "-p", help="Project slug (auto-created if not exists)")
    p_poe_run.add_argument("--parent", help="Parent project slug (sets goal ancestry for this run)")
    p_poe_run.add_argument("--parent-title", help="Human-readable title of the parent goal")
    p_poe_run.add_argument("--model", "-m", help="LLM model string")
    p_poe_run.add_argument("--max-steps", type=int, default=6, help="Max decomposition steps (default: 6)")
    p_poe_run.add_argument("--max-iterations", type=int, default=20, help="Hard cap on LLM calls")
    p_poe_run.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    p_poe_run.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    p_poe_run.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

    p_poe_evolver = sub.add_parser("poe-evolver", help="Run Poe's meta-evolver — analyze outcomes + propose improvements (§19)")
    p_poe_evolver.add_argument("--dry-run", action="store_true", help="Analyze without writing suggestions")
    p_poe_evolver.add_argument("--min-outcomes", type=int, default=3, help="Minimum outcomes needed to run (default: 3)")
    p_poe_evolver.add_argument("--window", type=int, default=50, help="How many recent outcomes to analyze (default: 50)")
    p_poe_evolver.add_argument("--notify", action="store_true", help="Send Telegram summary of suggestions")
    p_poe_evolver.add_argument("--list", action="store_true", dest="list_pending", help="List pending (unapplied) suggestions")
    p_poe_evolver.add_argument("--apply", dest="apply_id", help="Mark a suggestion as applied by ID")
    p_poe_evolver.add_argument("--verbose", "-v", action="store_true", default=True)
    p_poe_evolver.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_metrics = sub.add_parser("poe-metrics", help="Show quality + cost metrics (Phase 8/19)")
    p_poe_metrics.add_argument("--format", choices=["text", "json"], default="text")
    poe_metrics_sub = p_poe_metrics.add_subparsers(dest="metrics_cmd")

    p_pass_k = poe_metrics_sub.add_parser("pass-k", help="Show pass@k and pass^k for a skill (Phase 19)")
    p_pass_k.add_argument("skill_id", help="Skill ID to compute pass@k for")
    p_pass_k.add_argument("--k", type=int, default=3, help="Number of attempts (default: 3)")
    p_pass_k.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_eval = sub.add_parser("poe-eval", help="Run evaluation benchmarks (Phase 8)")
    p_poe_eval.add_argument("--dry-run", action="store_true", help="Return canned results without LLM calls")
    p_poe_eval.add_argument("--benchmark", dest="benchmark_id", help="Run a specific benchmark by ID")
    p_poe_eval.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_heartbeat = sub.add_parser("poe-heartbeat", help="Run Poe's heartbeat — health check + tiered recovery (Phase 4)")
    p_poe_heartbeat.add_argument("--loop", action="store_true", help="Run forever on an interval")
    p_poe_heartbeat.add_argument("--interval", type=float, default=60.0, help="Seconds between checks (default: 60)")
    p_poe_heartbeat.add_argument("--dry-run", action="store_true", help="Check only, no recovery or Telegram alerts")
    p_poe_heartbeat.add_argument("--no-escalate", action="store_true", help="Skip Telegram escalation")
    p_poe_heartbeat.add_argument("--verbose", "-v", action="store_true", default=True)
    p_poe_heartbeat.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_telegram = sub.add_parser("poe-telegram", help="Start Poe's Telegram listener (routes messages through handle)")
    p_poe_telegram.add_argument("--once", action="store_true", help="Process pending updates once and exit")
    p_poe_telegram.add_argument("--dry-run", action="store_true", help="Process but don't send responses")
    p_poe_telegram.add_argument("--project", "-p", default="poe-telegram", help="Project slug for memory")
    p_poe_telegram.add_argument("--verbose", "-v", action="store_true", default=True)

    p_poe_interrupt = sub.add_parser("poe-interrupt", help="Post an interrupt to a running agent loop")
    p_poe_interrupt.add_argument("message", nargs="+", help="Interrupt message (natural language)")
    p_poe_interrupt.add_argument("--source", default="cli", help="Source identifier (default: cli)")
    p_poe_interrupt.add_argument("--intent", choices=["additive", "corrective", "priority", "stop"],
                                  help="Force a specific intent (skip LLM classification)")
    p_poe_interrupt.add_argument("--status", action="store_true", help="Show running loop status instead")
    p_poe_interrupt.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_mission = sub.add_parser("poe-mission", help="Run a mission — milestone-gated multi-day goal pursuit (Phase 10)")
    p_poe_mission.add_argument("goal", nargs="+", help="High-level multi-day goal")
    p_poe_mission.add_argument("--project", "-p", help="Project slug (auto-created if not given)")
    p_poe_mission.add_argument("--dry-run", action="store_true", help="Simulate without LLM calls")
    p_poe_mission.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    p_poe_mission.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_mission_status = sub.add_parser("poe-mission-status", help="Show mission status for a project or all projects (Phase 10)")
    p_poe_mission_status.add_argument("project", nargs="?", help="Project slug (omit for all)")
    p_poe_mission_status.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_background = sub.add_parser("poe-background", help="Run a shell command in the background (Phase 10)")
    p_poe_background.add_argument("command", nargs="+", help="Shell command to run")
    p_poe_background.add_argument("--wait", action="store_true", help="Wait for completion (poll until done)")
    p_poe_background.add_argument("--timeout", type=int, default=300, help="Timeout seconds (default: 300)")
    p_poe_background.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_hooks = sub.add_parser("poe-hooks", help="Manage hook registry (Phase 11)")
    hooks_sub = p_poe_hooks.add_subparsers(dest="hooks_cmd", required=True)

    p_hooks_list = hooks_sub.add_parser("list", help="List registered hooks")
    p_hooks_list.add_argument("--scope", help="Filter by scope (mission/milestone/feature/step)")
    p_hooks_list.add_argument("--format", choices=["text", "json"], default="text")

    p_hooks_enable = hooks_sub.add_parser("enable", help="Enable a hook by ID")
    p_hooks_enable.add_argument("id", help="Hook ID to enable")

    p_hooks_disable = hooks_sub.add_parser("disable", help="Disable a hook by ID")
    p_hooks_disable.add_argument("id", help="Hook ID to disable")

    p_hooks_add_reporter = hooks_sub.add_parser("add-reporter", help="Register a reporter hook")
    p_hooks_add_reporter.add_argument("name", help="Hook name")
    p_hooks_add_reporter.add_argument("--scope", required=True,
                                       choices=["mission", "milestone", "feature", "step"],
                                       help="Scope for this hook")
    p_hooks_add_reporter.add_argument("--target", required=True,
                                       choices=["telegram", "log", "both"],
                                       help="Report target")
    p_hooks_add_reporter.add_argument("--template", default="",
                                       help="Prompt template (optional)")
    p_hooks_add_reporter.add_argument("--fire-on", choices=["before", "after"], default="after")

    p_hooks_run = hooks_sub.add_parser("run-builtin", help="Test a builtin hook manually")
    p_hooks_run.add_argument("id", help="Builtin hook ID to run")
    p_hooks_run.add_argument("--goal", default="", help="Goal context")
    p_hooks_run.add_argument("--step", default="", help="Step context")
    p_hooks_run.add_argument("--result", default="", help="Step result context")
    p_hooks_run.add_argument("--dry-run", action="store_true", help="Skip LLM calls")
    p_hooks_run.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_inspector = sub.add_parser("poe-inspector", help="Run Poe's quality inspector — friction detection + oversight (Phase 12)")
    p_poe_inspector.add_argument("--loop", action="store_true", help="Run forever on an interval (for systemd)")
    p_poe_inspector.add_argument("--interval", type=float, default=3600.0, help="Seconds between runs (default: 3600)")
    p_poe_inspector.add_argument("--limit", type=int, default=50, help="Number of outcomes to inspect (default: 50)")
    p_poe_inspector.add_argument("--dry-run", action="store_true", help="Run without LLM calls or saving results")
    p_poe_inspector.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_inspector_status = sub.add_parser("poe-inspector-status", help="Show latest inspector report summary (Phase 12)")
    p_poe_inspector_status.add_argument("--format", choices=["text", "json"], default="text")

    # poe-quality is an alias for poe-inspector-status (shorter to type)
    p_poe_quality = sub.add_parser("poe-quality", help="Show quality summary (alias for poe-inspector-status)")
    p_poe_quality.add_argument("--format", choices=["text", "json"], default="text")

    # Phase 13: Poe CEO layer commands
    p_poe_ceo = sub.add_parser("poe", help="Send a request through the Poe CEO layer (Phase 13)")
    p_poe_ceo.add_argument("message", nargs="+", help="The request to handle")
    p_poe_ceo.add_argument("--model", "-m", help="LLM model string override")
    p_poe_ceo.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    p_poe_ceo.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_status = sub.add_parser("poe-status", help="Executive summary via Poe CEO layer (Phase 13)")
    p_poe_status.add_argument("--format", choices=["text", "json"], default="text")
    p_poe_status.add_argument("--dry-run", action="store_true", help="Simulate without API calls")

    p_poe_map = sub.add_parser("poe-map", help="Goal relationship map (Phase 13)")
    p_poe_map.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_autonomy = sub.add_parser("poe-autonomy", help="View/set autonomy tier config (Phase 13)")
    p_poe_autonomy.add_argument("--tier", choices=["manual", "safe", "full"],
                                 help="Set default tier (or project/action tier)")
    p_poe_autonomy.add_argument("--project", help="Project to set tier for")
    p_poe_autonomy.add_argument("--action", dest="action_type",
                                 help="Action type to set tier for")
    p_poe_autonomy.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_skills = sub.add_parser("poe-skills", help="Manage skill library (Phase 10)")
    p_poe_skills.add_argument("--extract", action="store_true", help="Extract skills from recent outcomes")
    p_poe_skills.add_argument("--list", action="store_true", dest="list_skills", help="List known skills")
    p_poe_skills.add_argument("--outcomes-window", type=int, default=50, help="How many recent outcomes to analyze (default: 50)")
    p_poe_skills.add_argument("--dry-run", action="store_true", help="Analyze without writing skills")
    p_poe_skills.add_argument("--format", choices=["text", "json"], default="text")

    # Phase 14 CLI subcommands
    p_poe_attribution = sub.add_parser("poe-attribution", help="Run failure attribution on recent stuck outcomes (Phase 14)")
    p_poe_attribution.add_argument("--batch", action="store_true", help="Attribute all recent stuck outcomes (default: yes)")
    p_poe_attribution.add_argument("--limit", type=int, default=20, help="Max outcomes to analyze (default: 20)")
    p_poe_attribution.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_skill_stats = sub.add_parser("poe-skill-stats", help="Show per-skill success rates (Phase 14)")
    p_poe_skill_stats.add_argument("--escalated", action="store_true", help="Filter to skills needing redesign (below threshold)")
    p_poe_skill_stats.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_skill_test = sub.add_parser("poe-skill-test", help="Run or generate tests for a skill (Phase 14)")
    p_poe_skill_test.add_argument("skill_id", help="Skill ID to test")
    p_poe_skill_test.add_argument("--generate", action="store_true", help="Generate new test cases from recent failures")
    p_poe_skill_test.add_argument("--format", choices=["text", "json"], default="text")

    # Phase 15: gateway + sandbox CLI subcommands
    p_poe_gateway = sub.add_parser("poe-gateway", help="OpenClaw gateway status and messaging (Phase 15)")
    gateway_sub = p_poe_gateway.add_subparsers(dest="gateway_cmd", required=True)

    gateway_sub.add_parser("status", help="Check if OpenClaw gateway is reachable")

    p_gw_send = gateway_sub.add_parser("send", help="Send a message to the OpenClaw gateway")
    p_gw_send.add_argument("message", nargs="+", help="Message text to send")
    p_gw_send.add_argument("--timeout", type=int, default=10, help="Send timeout in seconds (default: 10)")
    p_gw_send.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_sandbox = sub.add_parser("poe-sandbox", help="Run skill tests in sandbox isolation (Phase 15)")
    sandbox_sub = p_poe_sandbox.add_subparsers(dest="sandbox_cmd", required=True)

    p_sb_test = sandbox_sub.add_parser("test", help="Run sandboxed tests for a skill")
    p_sb_test.add_argument("skill_id", help="Skill ID or name to test")
    p_sb_test.add_argument("--generate", action="store_true", help="Generate new tests from recent failures before running")
    p_sb_test.add_argument("--format", choices=["text", "json"], default="text")

    # Phase 17: Behavior-aligned skill router CLI
    p_poe_router = sub.add_parser("poe-router", help="Behavior-aligned skill router (Phase 17)")
    router_sub = p_poe_router.add_subparsers(dest="router_cmd", required=True)

    router_sub.add_parser("stats", help="Show router training stats (samples, accuracy, method)")

    router_sub.add_parser("retrain", help="Force retrain the router from current skill-stats")

    p_router_route = router_sub.add_parser("route", help="Show top skill matches for a goal text")
    p_router_route.add_argument("goal", nargs="+", help="Goal text to route")
    p_router_route.add_argument("--top-k", type=int, default=3, help="Number of results (default: 3)")
    p_router_route.add_argument("--format", choices=["text", "json"], default="text")

    # Phase 19: Sprint contract, boot protocol, manifest CLI commands
    p_poe_contract = sub.add_parser("poe-contract", help="Sprint contract negotiation + grading (Phase 19)")
    contract_sub = p_poe_contract.add_subparsers(dest="contract_cmd", required=True)

    p_contract_negotiate = contract_sub.add_parser("negotiate", help="Generate a sprint contract for a feature")
    p_contract_negotiate.add_argument("feature_title", nargs="+", help="Feature title")
    p_contract_negotiate.add_argument("--goal", default="", help="Mission goal")
    p_contract_negotiate.add_argument("--milestone", default="", help="Milestone title")
    p_contract_negotiate.add_argument("--dry-run", action="store_true", help="Use heuristic (no LLM)")
    p_contract_negotiate.add_argument("--format", choices=["text", "json"], default="text")

    p_contract_grade = contract_sub.add_parser("grade", help="Grade work result against a contract")
    p_contract_grade.add_argument("contract_id", help="Contract ID to grade against")
    p_contract_grade.add_argument("--result", default="", help="Work result text to grade")
    p_contract_grade.add_argument("--project", default="", help="Project slug (to load contract)")
    p_contract_grade.add_argument("--dry-run", action="store_true", help="Use heuristic grading (no LLM)")
    p_contract_grade.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_boot = sub.add_parser("poe-boot", help="Run Worker boot protocol for a project (Phase 19)")
    p_poe_boot.add_argument("project", help="Project slug")
    p_poe_boot.add_argument("--dry-run", action="store_true", help="Return minimal BootState without filesystem reads")
    p_poe_boot.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_manifest = sub.add_parser("poe-manifest", help="Show feature manifest status for a project (Phase 19)")
    p_poe_manifest.add_argument("project", help="Project slug")
    p_poe_manifest.add_argument("--format", choices=["text", "json"], default="text")

    p_plan = sub.add_parser("plan", help="Split a goal into NEXT tasks")
    p_plan.add_argument("project")
    p_plan.add_argument("goal", nargs="+")
    p_plan.add_argument("--max-steps", type=int, default=4)

    p_tick = sub.add_parser("tick", help="Claim a task and execute one automation tick")
    p_tick.add_argument("--project")
    p_tick.add_argument("--worker", default="handle")
    p_tick.add_argument("--source", default="tick")
    p_tick.add_argument("--note")
    p_tick.add_argument("--exec-cmd", help="Shell command execution bridge for the claimed task")
    p_tick.add_argument("--session-cmd", help="Session command execution bridge for the claimed task")
    p_tick.add_argument("--worker-session", help="Named worker session script")
    p_tick.add_argument("--session-timeout", type=float, default=None, help="Timeout in seconds for session command")
    p_tick.add_argument("--disable-x-capture", action="store_true", help="Disable X capture/rate-limit salvage classification")
    p_tick.add_argument("--disable-artifact-progress", action="store_true", help="Disable stale artifact progress detection across attempts")
    p_tick.add_argument("--artifact-progress-window", type=int, default=2, help="Consecutive matching artifact attempts before stale progress triggers (default: 2)")
    p_tick.add_argument("--artifact-progress-max-attempts", type=int, default=3, help="Attempts before stale artifact progress becomes blocked instead of retry (default: 3)")
    p_tick.add_argument("--max-retry-streak", type=int, default=None, help="Consecutive retry outcomes per item before auto-blocking")
    p_tick.add_argument("--require-artifact", action="append", default=[], help="Artifact path relative to the run artifact dir that must exist")
    p_tick.add_argument("--require-nonempty", action="store_true", help="Require listed artifacts to be non-empty files")
    p_tick.add_argument("--review-cmd", help="Shell command reviewer run after execution succeeds")
    p_tick.add_argument("--review-timeout", type=float, default=None, help="Timeout in seconds for review command")

    p_loop = sub.add_parser("loop", help="Run a bounded automation loop")
    p_loop.add_argument("--project")
    p_loop.add_argument("--worker", default="handle")
    p_loop.add_argument("--source", default="loop")
    p_loop.add_argument("--note")
    p_loop.add_argument("--max-runs", type=int, default=10)
    p_loop.add_argument("--exec-cmd", help="Shell command execution bridge for each claimed task")
    p_loop.add_argument("--session-cmd", help="Session command execution bridge for each claimed task")
    p_loop.add_argument("--worker-session", help="Named worker session script")
    p_loop.add_argument("--session-timeout", type=float, default=None, help="Timeout in seconds for session command")
    p_loop.add_argument("--disable-x-capture", action="store_true", help="Disable X capture/rate-limit salvage classification")
    p_loop.add_argument("--disable-artifact-progress", action="store_true", help="Disable stale artifact progress detection across attempts")
    p_loop.add_argument("--artifact-progress-window", type=int, default=2, help="Consecutive matching artifact attempts before stale progress triggers (default: 2)")
    p_loop.add_argument("--artifact-progress-max-attempts", type=int, default=3, help="Attempts before stale artifact progress becomes blocked instead of retry (default: 3)")
    p_loop.add_argument("--max-retry-streak", type=int, default=None, help="Consecutive retry outcomes per item before auto-blocking")
    p_loop.add_argument("--require-artifact", action="append", default=[], help="Artifact path relative to the run artifact dir that must exist")
    p_loop.add_argument("--require-nonempty", action="store_true", help="Require listed artifacts to be non-empty files")
    p_loop.add_argument("--review-cmd", help="Shell command reviewer run after execution succeeds")
    p_loop.add_argument("--review-timeout", type=float, default=None, help="Timeout in seconds for review command")
    p_loop.add_argument("--continue-on-retry", action="store_true", help="Continue loop when validation status is retry")
    p_loop.add_argument("--continue-on-blocked", action="store_true", help="Continue loop when validation status is blocked")
    p_loop.add_argument(
        "--max-attempts-per-item",
        type=int,
        help="Maximum attempts per item before stopping non-done statuses",
    )

    p_ancestry = sub.add_parser("ancestry", help="Show goal ancestry chain for a project (§18)")
    p_ancestry.add_argument("project", help="Project slug")
    p_ancestry.add_argument("--set-parent", help="Set parent project slug (creates ancestry link)")
    p_ancestry.add_argument("--parent-title", help="Human-readable title of the parent goal")
    p_ancestry.add_argument("--format", choices=["text", "json"], default="text")

    p_impact = sub.add_parser("impact", help="List all descendant projects of a goal (BFS)")
    p_impact.add_argument("project", help="Root project slug to trace descendants from")
    p_impact.add_argument("--format", choices=["text", "json"], default="text")

    p_status = sub.add_parser("status", help="Write/read operator status")
    p_status.add_argument("--format", choices=["json", "path"], default="json")

    args = parser.parse_args(argv)

    if args.cmd == "init":
        p = ensure_project(args.slug, " ".join(args.mission), priority=args.priority)
        write_operator_status()
        print(f"initialized={p}")
        return 0

    if args.cmd == "next":
        if args.project:
            p = project_dir(args.project)
            if not p.exists():
                return fail("E_PROJECT_NOT_FOUND", args.project)
            item = select_next_item(args.project)
            if item:
                print(f"project={args.project} index={item.index} state=[{item.state}] text={item.text}")
                return 0
            print(f"project={args.project} next=(none)")
            return 1

        sel = select_global_next()
        if not sel:
            print("next=(none)")
            return 1
        slug, item = sel
        print(f"project={slug} index={item.index} state=[{item.state}] text={item.text}")
        return 0

    if args.cmd == "done":
        if not project_dir(args.project).exists():
            return fail("E_PROJECT_NOT_FOUND", args.project)
        if args.index is None:
            item = mark_first_todo_done(args.project)
            if not item:
                print(f"project={args.project} updated=0")
                return 1
            write_operator_status()
            print(f"project={args.project} updated=1 index={item.index} text={item.text}")
            return 0
        mark_item(args.project, args.index, "x")
        write_operator_status()
        print(f"project={args.project} updated=1 index={args.index}")
        return 0

    if args.cmd == "log":
        if not project_dir(args.project).exists():
            return fail("E_PROJECT_NOT_FOUND", args.project)
        append_decision(args.project, [" ".join(args.message)])
        print(f"project={args.project} logged=1")
        return 0

    if args.cmd == "enqueue":
        if not project_dir(args.project).exists():
            return fail("E_PROJECT_NOT_FOUND", args.project)
        workspace_root = Path(os.environ.get("OPENCLAW_WORKSPACE", str(orch_root().parents[2])))
        queue = workspace_root / "scripts" / "task-queue.sh"
        if not queue.exists():
            return fail("E_QUEUE_NOT_FOUND", str(queue))
        task_text = " ".join(args.task).strip()
        if not task_text:
            return fail("E_TASK_REQUIRED", "task text cannot be empty")
        payload = f"project={args.project} :: {task_text}"
        cmd = [str(queue), "enqueue", "project_task", payload, args.lane, args.source, args.reason]
        if args.parent_job_id:
            cmd.append(args.parent_job_id)
        proc = subprocess.run(cmd, cwd=orch_root(), capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "queue enqueue failed").strip()
            return fail("E_QUEUE_ENQUEUE", detail)
        print(f"project={args.project} type=project_task lane={args.lane} payload={json.dumps(payload)}")
        if proc.stdout.strip():
            print(proc.stdout.strip())
        return 0

    if args.cmd == "blocked":
        blocked = list_blocked_projects()
        if not blocked:
            print("blocked=(none)")
            return 0
        for b in blocked:
            print(f"project={b.slug} priority={b.priority} blocked={b.blocked} todo={b.todo}")
        return 0

    if args.cmd == "salvage":
        payload = write_operator_status()["salvage"]
        if args.format == "json":
            print(json.dumps(payload, indent=2))
            return 0
        print(f"active_count={payload['active_count']} pending_count={payload['pending_count']} index_path={payload['index_path']}")
        if not payload["active_runs"]:
            print("salvage=(none)")
            return 0
        for run in payload["active_runs"]:
            print(
                " ".join(
                    [
                        f"run_id={run['run_id']}",
                        f"project={run['project']}",
                        f"item={run['item']}",
                        f"attempt={run['attempt']}",
                        *( [f"kind={run['first_kind']}"] if run.get("first_kind") else [] ),
                        *( [f"detail={json.dumps(run['first_detail'])}"] if run.get("first_detail") else [] ),
                        f"artifact={run['artifact_path']}",
                    ]
                )
            )
        return 0

    if args.cmd == "report":
        content = status_report_markdown(args.project) if args.format == "md" else status_report_json(args.project)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(content, encoding="utf-8")
            print(f"written={out}")
        else:
            print(content, end="")
        return 0

    if args.cmd == "start":
        project = args.project
        if args.index is not None and not project:
            return fail("E_PROJECT_REQUIRED", "--index requires --project")
        try:
            if project:
                if not project_dir(project).exists():
                    return fail("E_PROJECT_NOT_FOUND", project)
                run = start_item(project, args.index, source=args.source, worker=args.worker, note=args.note)
            else:
                run = run_once(worker=args.worker, source=args.source, note=args.note)
                if not run:
                    print("run=(none)")
                    return 1
        except ValueError as exc:
            return fail("E_START_FAILED", str(exc))
        _print_run("started", run)
        return 0

    if args.cmd == "finish":
        try:
            run = finalize_run(args.run_id, args.status, note=args.note)
        except FileNotFoundError:
            return fail("E_RUN_NOT_FOUND", args.run_id)
        except ValueError as exc:
            return fail("E_FINISH_FAILED", str(exc))
        _print_run("finished", run)
        return 0

    if args.cmd == "inspect-run":
        try:
            run = load_run_record(args.run_id)
            summary = load_validation_summary(args.run_id)
        except FileNotFoundError:
            return fail("E_RUN_NOT_FOUND", args.run_id)
        salvage = _load_salvage_summary(run)
        payload = {
            "run": json.loads(json.dumps(run, default=lambda o: o.__dict__)),
            "validation_summary": summary,
            "salvage_summary": salvage,
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2))
        else:
            print(f"run_id={run.run_id}")
            print(f"project={run.project}")
            print(f"index={run.index}")
            print(f"status={run.status}")
            print(f"text={run.text}")
            if run.artifact_path:
                print(f"artifact={run.artifact_path}")
            if run.note:
                print(f"note={run.note}")
            if summary:
                print(f"validation_status={summary['validation']['status']}")
                print(f"validation_passed={summary['validation']['passed']}")
            if salvage:
                print(f"salvage_path={salvage.get('path')}")
                matches = salvage.get("matches") or []
                first = next((item for item in matches if isinstance(item, dict)), None)
                if first:
                    if first.get("kind"):
                        print(f"salvage_kind={first['kind']}")
                    if first.get("detail"):
                        print(f"salvage_detail={first['detail']}")
        return 0

    if args.cmd == "run":
        try:
            run = run_once(project=args.project, worker=args.worker, source=args.source, note=args.note)
        except ValueError as exc:
            return fail("E_RUN_FAILED", str(exc))
        if not run:
            print("run=(none)")
            return 1
        _print_run("started", run)
        if args.finish:
            try:
                run = finalize_run(run.run_id, args.finish, note=args.finish_note)
            except ValueError as exc:
                return fail("E_RUN_FINISH_FAILED", str(exc))
            _print_run("finished", run)
        return 0

    if args.cmd == "memory":
        import memory as _mem
        if args.memory_cmd == "context":
            ctx = _mem.bootstrap_context()
            print(ctx if ctx else "(no memory yet)")
            return 0
        if args.memory_cmd == "outcomes":
            outcomes = _mem.load_outcomes(limit=args.limit)
            if args.format == "json":
                from dataclasses import asdict
                print(json.dumps([asdict(o) for o in outcomes], indent=2))
            else:
                for o in outcomes:
                    print(f"[{o.recorded_at[:10]}] {o.status:6s} {o.task_type:8s} {o.goal[:60]}")
            return 0
        if args.memory_cmd == "lessons":
            lessons = _mem.load_lessons(task_type=args.task_type, limit=args.limit)
            if args.format == "json":
                from dataclasses import asdict
                print(json.dumps([asdict(l) for l in lessons], indent=2))
            else:
                for l in lessons:
                    print(f"[{l.task_type:8s}] conf={l.confidence:.1f} {l.lesson[:80]}")
            return 0

    if args.cmd == "sheriff":
        import sheriff as _sheriff_mod
        if args.sheriff_cmd == "check":
            report = _sheriff_mod.check_project(args.project, window_minutes=args.window)
            print(report.format(args.format))
            return 0 if report.status == "healthy" else 1
        if args.sheriff_cmd == "all":
            reports = _sheriff_mod.check_all_projects(window_minutes=args.window)
            if args.format == "json":
                print(json.dumps([json.loads(r.format("json")) for r in reports], indent=2))
            else:
                for r in reports:
                    print(r.format("text"))
                    print()
            stuck = [r for r in reports if r.status in ("stuck", "warning")]
            return 1 if stuck else 0
        if args.sheriff_cmd == "health":
            health = _sheriff_mod.check_system_health()
            if args.write_state:
                project_reports = _sheriff_mod.check_all_projects()
                _sheriff_mod.write_heartbeat_state(health, project_reports=project_reports)
            print(health.format(args.format))
            return 0 if health.status == "healthy" else 1

    if args.cmd == "poe-director":
        import director as _director_mod
        directive = " ".join(args.directive)
        try:
            result = _director_mod.run_director(
                directive,
                project=args.project,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except Exception as exc:
            return fail("E_POE_DIRECTOR", str(exc))
        if args.format == "json":
            print(json.dumps({
                "director_id": result.director_id,
                "status": result.status,
                "plan_acceptance": result.plan_acceptance,
                "tickets": len(result.tickets),
                "report": result.report,
                "tokens_in": result.tokens_in,
                "tokens_out": result.tokens_out,
                "elapsed_ms": result.elapsed_ms,
                "log_path": result.log_path,
            }, indent=2))
        else:
            print(result.summary())
            if result.report:
                print()
                print("=== REPORT ===")
                print(result.report)
        return 0 if result.status == "done" else 1

    if args.cmd == "poe-handle":
        import handle as _handle_mod
        msg = " ".join(args.message)
        try:
            result = _handle_mod.handle(
                msg,
                project=args.project,
                model=args.model,
                force_lane=args.lane,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except Exception as exc:
            return fail("E_POE_HANDLE", str(exc))
        print(result.format(mode=args.format))
        return 0 if result.status == "done" else 1

    if args.cmd == "poe-run":
        import agent_loop as _al
        goal_str = " ".join(args.goal)
        # Wire up ancestry if --parent was specified
        if getattr(args, "parent", None):
            from ancestry import create_child_ancestry, set_project_ancestry
            import orch as _o
            _target_slug = args.project or _al._goal_to_slug(goal_str)
            _target_dir = _o.project_dir(_target_slug)
            if not _target_dir.exists():
                _o.ensure_project(_target_slug, goal_str[:80])
            _parent_dir = _o.project_dir(args.parent)
            _parent_title = getattr(args, "parent_title", None) or args.parent
            _child_ancestry = create_child_ancestry(args.parent, _parent_title, _parent_dir)
            set_project_ancestry(_target_dir, _child_ancestry)
        try:
            result = _al.run_agent_loop(
                goal_str,
                project=args.project,
                model=args.model,
                max_steps=args.max_steps,
                max_iterations=args.max_iterations,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except Exception as exc:
            return fail("E_POE_RUN", str(exc))
        if args.format == "json":
            print(json.dumps({
                "loop_id": result.loop_id,
                "project": result.project,
                "goal": result.goal,
                "status": result.status,
                "steps_done": sum(1 for s in result.steps if s.status == "done"),
                "steps_total": len(result.steps),
                "stuck_reason": result.stuck_reason,
                "tokens_in": result.total_tokens_in,
                "tokens_out": result.total_tokens_out,
                "elapsed_ms": result.elapsed_ms,
                "log_path": result.log_path,
            }, indent=2))
        else:
            print(result.summary())
        return 0 if result.status == "done" else 1

    if args.cmd == "poe-evolver":
        from evolver import run_evolver, list_pending_suggestions, apply_suggestion

        if getattr(args, "list_pending", False):
            pending = list_pending_suggestions()
            if args.format == "json":
                print(json.dumps([s.to_dict() for s in pending], indent=2))
            else:
                if not pending:
                    print("(no pending suggestions)")
                else:
                    for s in pending:
                        print(f"  [{s.suggestion_id}] [{s.category}] {s.target}: {s.suggestion[:80]}")
            return 0

        if getattr(args, "apply_id", None):
            ok = apply_suggestion(args.apply_id)
            if ok:
                print(f"applied={args.apply_id}")
                return 0
            else:
                return fail("E_SUGGESTION_NOT_FOUND", args.apply_id)

        report = run_evolver(
            outcomes_window=args.window,
            min_outcomes=args.min_outcomes,
            dry_run=args.dry_run,
            verbose=args.verbose,
            notify=args.notify,
        )
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
        return 0

    if args.cmd == "poe-heartbeat":
        from heartbeat import run_heartbeat, heartbeat_loop
        if args.loop:
            heartbeat_loop(
                interval=args.interval,
                dry_run=args.dry_run,
                verbose=args.verbose,
                escalate=not args.no_escalate,
            )
            return 0
        report = run_heartbeat(
            dry_run=args.dry_run,
            verbose=args.verbose,
            escalate=not args.no_escalate,
        )
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
        return 0

    if args.cmd == "poe-telegram":
        from telegram_listener import poll_once, poll_loop
        try:
            if args.once:
                n = poll_once(dry_run=args.dry_run, project=args.project, verbose=args.verbose)
                print(f"processed={n}")
                return 0
            else:
                poll_loop(dry_run=args.dry_run, project=args.project, verbose=args.verbose)
                return 0
        except RuntimeError as exc:
            return fail("E_TELEGRAM", str(exc))

    if args.cmd == "poe-interrupt":
        from interrupt import InterruptQueue, is_loop_running, get_running_loop
        import json as _json

        if args.status:
            info = get_running_loop()
            if args.format == "json":
                print(_json.dumps(info or {}))
            elif info:
                print(f"loop_id={info['loop_id']} goal={info.get('goal','?')!r} pid={info.get('pid','?')}")
            else:
                print("No loop running.")
            return 0

        message = " ".join(args.message)
        q = InterruptQueue()
        intr = q.post(message, source=args.source, intent=args.intent)
        if args.format == "json":
            print(_json.dumps(intr.to_dict()))
        else:
            running = get_running_loop()
            if running:
                print(f"interrupt posted: id={intr.id} intent={intr.intent} loop={running.get('loop_id','?')}")
            else:
                print(f"interrupt queued (no loop running yet): id={intr.id} intent={intr.intent}")
        return 0

    if args.cmd == "plan":
        try:
            result = plan_project(args.project, " ".join(args.goal), max_steps=args.max_steps)
        except ValueError as exc:
            return fail("E_PLAN_FAILED", str(exc))
        print(f"project={result.project} steps={len(result.steps)} added={len(result.item_indices)} first={result.item_indices[0] if result.item_indices else -1}")
        return 0

    if args.cmd == "tick":
        try:
            execution = _build_execution(args)
        except ValueError as exc:
            return fail("E_TICK_EXEC", str(exc))
        validation = _build_validation(args)
        try:
            tick = run_tick(
                project=args.project,
                worker=args.worker,
                source=args.source,
                note=args.note,
                max_retry_streak=args.max_retry_streak,
                execution=execution,
                validation=validation,
            )
        except ValueError as exc:
            return fail("E_TICK_FAILED", str(exc))
        except Exception as exc:
            return fail("E_TICK_FAILED", str(exc))
        if not tick:
            print("tick=(none)")
            return 1
        _print_run("tick-start", tick.run)
        print(f"execution={tick.execution.status} validation={tick.validation.status}")
        return 0

    if args.cmd == "loop":
        if args.max_runs <= 0:
            return fail("E_LOOP_BAD_LIMIT", "max-runs must be greater than zero")
        try:
            execution = _build_execution(args)
        except ValueError as exc:
            return fail("E_LOOP_EXEC", str(exc))
        validation = _build_validation(args)
        try:
            ticks = run_loop(
                project=args.project,
                worker=args.worker,
                source=args.source,
                note=args.note,
                max_runs=args.max_runs,
                max_retry_streak=args.max_retry_streak,
                execution=execution,
                validation=validation,
                continue_on_retry=args.continue_on_retry,
                continue_on_blocked=args.continue_on_blocked,
                max_attempts_per_item=args.max_attempts_per_item,
            )
        except ValueError as exc:
            return fail("E_LOOP_FAILED", str(exc))
        except Exception as exc:
            return fail("E_LOOP_FAILED", str(exc))
        if not ticks:
            print("loop=(none)")
            return 1
        print(f"runs={len(ticks)}")
        for idx, tick in enumerate(ticks, start=1):
            print(
                f"iteration={idx} project={tick.run.project} run_id={tick.run.run_id} status={tick.validation.status} item={tick.run.index}"
            )
        return 0

    if args.cmd == "ancestry":
        from ancestry import (
            get_project_ancestry, set_project_ancestry,
            create_child_ancestry, orch_ancestry,
        )
        p = project_dir(args.project)
        if not p.exists():
            return fail("E_PROJECT_NOT_FOUND", args.project)

        if args.set_parent:
            parent_p = project_dir(args.set_parent)
            parent_title = args.parent_title or args.set_parent
            new_ancestry = create_child_ancestry(args.set_parent, parent_title, parent_p)
            set_project_ancestry(p, new_ancestry)
            print(f"project={args.project} parent={args.set_parent} ancestry_depth={new_ancestry.depth()}")
            return 0

        chain = orch_ancestry(args.project, p)
        if args.format == "json":
            ancestry = get_project_ancestry(p)
            print(json.dumps(ancestry.to_dict() if ancestry else {}, indent=2))
        else:
            for line in chain:
                print(line)
        return 0

    if args.cmd == "impact":
        from ancestry import orch_impact
        p = project_dir(args.project)
        if not p.exists():
            return fail("E_PROJECT_NOT_FOUND", args.project)
        descendants = orch_impact(args.project, p.parent)
        if args.format == "json":
            print(json.dumps(descendants))
        else:
            if not descendants:
                print(f"project={args.project} descendants=(none)")
            else:
                for d in descendants:
                    print(d)
        return 0

    if args.cmd == "poe-metrics":
        from metrics import get_metrics, format_metrics_report
        # Phase 19: pass-k subcommand
        if getattr(args, "metrics_cmd", None) == "pass-k":
            from metrics import compute_pass_at_k, compute_pass_all_k, check_skill_promotion_eligibility
            skill_id = args.skill_id
            k = args.k
            pass_at_k = compute_pass_at_k(skill_id, k=k)
            pass_all_k = compute_pass_all_k(skill_id, k=k)
            eligible = check_skill_promotion_eligibility(skill_id, k=k)
            if getattr(args, "format", "text") == "json":
                print(json.dumps({
                    "skill_id": skill_id,
                    "k": k,
                    "pass_at_k": pass_at_k,
                    "pass_all_k": pass_all_k,
                    "promotion_eligible": eligible,
                }, indent=2))
            else:
                print(f"skill_id={skill_id} k={k}")
                print(f"  pass@k  = {pass_at_k:.4f}  (P at least 1 success in {k} attempts)")
                print(f"  pass^k  = {pass_all_k:.4f}  (P all {k} attempts succeed)")
                print(f"  promotion_eligible = {eligible}")
            return 0
        metrics = get_metrics()
        if args.format == "json":
            from dataclasses import asdict
            print(json.dumps(asdict(metrics), indent=2))
        else:
            print(format_metrics_report(metrics))
        return 0

    # ---------------------------------------------------------------------------
    # Phase 19: Sprint contract, boot protocol, manifest CLI handlers
    # ---------------------------------------------------------------------------

    if args.cmd == "poe-contract":
        from sprint_contract import negotiate_contract, grade_contract, load_contracts
        from dataclasses import asdict

        if args.contract_cmd == "negotiate":
            feature_title = " ".join(args.feature_title)
            contract = negotiate_contract(
                feature_title=feature_title,
                mission_goal=args.goal,
                milestone_title=args.milestone,
                adapter=None,  # heuristic when --dry-run; real adapter otherwise
            )
            if getattr(args, "format", "text") == "json":
                print(json.dumps(contract.to_dict(), indent=2))
            else:
                print(f"contract_id={contract.contract_id} negotiated_by={contract.negotiated_by}")
                print(f"feature={contract.feature_title!r}")
                print("success_criteria:")
                for c in contract.success_criteria:
                    print(f"  - {c}")
                print(f"acceptance_keywords: {', '.join(contract.acceptance_keywords)}")
            return 0

        if args.contract_cmd == "grade":
            project = args.project or ""
            contract_id = args.contract_id
            work_result = args.result

            # Try to load the contract from project contracts
            target_contract = None
            if project:
                contracts = load_contracts(project)
                for c in contracts:
                    if c.contract_id == contract_id:
                        target_contract = c
                        break

            if target_contract is None:
                # Can't grade without the original contract; show error
                return fail("E_CONTRACT_NOT_FOUND", f"No contract {contract_id!r} in project {project!r}")

            grade = grade_contract(target_contract, work_result, adapter=None)
            if getattr(args, "format", "text") == "json":
                print(json.dumps(grade.to_dict(), indent=2))
            else:
                print(f"contract_id={grade.contract_id} passed={grade.passed} score={grade.score:.3f}")
                print(f"feedback: {grade.feedback}")
                for cr in grade.criteria_results:
                    status = "PASS" if cr["passed"] else "FAIL"
                    print(f"  [{status}] {cr['criterion']}: {cr['evidence'][:80]}")
            return 0

    if args.cmd == "poe-boot":
        from boot_protocol import run_boot_protocol, format_boot_context
        state = run_boot_protocol(args.project, dry_run=args.dry_run)
        if getattr(args, "format", "text") == "json":
            print(json.dumps({
                "project": state.project,
                "loop_id": state.loop_id,
                "completed_features": state.completed_features,
                "git_head": state.git_head,
                "existing_tests_pass": state.existing_tests_pass,
                "dead_ends": state.dead_ends,
                "boot_timestamp": state.boot_timestamp,
                "boot_method": state.boot_method,
            }, indent=2))
        else:
            print(format_boot_context(state))
        return 0

    if args.cmd == "poe-manifest":
        from mission import load_feature_manifest
        manifest = load_feature_manifest(args.project)
        if manifest is None:
            print(f"project={args.project} manifest=(none)")
            return 1
        features = manifest.get("features", [])
        if getattr(args, "format", "text") == "json":
            print(json.dumps(manifest, indent=2))
        else:
            total = len(features)
            passing = sum(1 for f in features if f.get("passes"))
            print(f"project={args.project} features={total} passing={passing}/{total}")
            for f in features:
                passes = "PASS" if f.get("passes") else "pending"
                score_str = f" score={f['grade_score']:.2f}" if f.get("grade_score") is not None else ""
                print(f"  [{passes:7s}]{score_str} [{f['id']}] {f['title']}")
        return 0

    if args.cmd == "poe-eval":
        from eval import run_eval
        benchmark_ids = [args.benchmark_id] if getattr(args, "benchmark_id", None) else None
        report = run_eval(benchmarks=benchmark_ids, dry_run=args.dry_run)
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
        return 0

    if args.cmd == "status":
        payload = write_operator_status()
        if args.format == "path":
            print(operator_status_path())
        else:
            print(json.dumps(payload, indent=2))
        return 0

    if args.cmd == "poe-mission":
        import mission as _mission_mod
        goal_str = " ".join(args.goal)
        try:
            result = _mission_mod.run_mission(
                goal_str,
                project=args.project,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
        except Exception as exc:
            return fail("E_POE_MISSION", str(exc))
        if args.format == "json":
            print(json.dumps({
                "mission_id": result.mission_id,
                "project": result.project,
                "goal": result.goal,
                "status": result.status,
                "milestones_done": result.milestones_done,
                "milestones_total": result.milestones_total,
                "features_done": result.features_done,
                "features_total": result.features_total,
                "elapsed_ms": result.elapsed_ms,
            }, indent=2))
        else:
            print(result.summary())
        return 0 if result.status == "done" else 1

    if args.cmd == "poe-mission-status":
        import mission as _mission_mod
        if args.project:
            m = _mission_mod.load_mission(args.project)
            if not m:
                return fail("E_MISSION_NOT_FOUND", f"no mission.json for project={args.project}")
            if args.format == "json":
                summaries = [{
                    "project": m.project,
                    "mission_id": m.id,
                    "goal": m.goal,
                    "status": m.status,
                    "milestones": [
                        {
                            "id": ms.id,
                            "title": ms.title,
                            "status": ms.status,
                            "features": [
                                {"id": f.id, "title": f.title, "status": f.status}
                                for f in ms.features
                            ],
                        }
                        for ms in m.milestones
                    ],
                }]
                print(json.dumps(summaries, indent=2))
            else:
                print(f"mission_id={m.id} project={m.project} status={m.status}")
                print(f"goal={m.goal!r}")
                for ms in m.milestones:
                    done_count = sum(1 for f in ms.features if f.status == "done")
                    print(f"  milestone [{ms.status:10s}] {ms.title!r} features={done_count}/{len(ms.features)}")
                    for f in ms.features:
                        print(f"    feature  [{f.status:8s}] {f.title!r}")
        else:
            summaries = _mission_mod.list_missions()
            if args.format == "json":
                print(json.dumps(summaries, indent=2))
            else:
                if not summaries:
                    print("missions=(none)")
                else:
                    for s in summaries:
                        print(
                            f"project={s['project']} status={s['status']} "
                            f"milestones={s['milestones_done']}/{s['milestones_total']} "
                            f"features={s['features_done']}/{s['features_total']} "
                            f"goal={s['goal'][:60]!r}"
                        )
        return 0

    if args.cmd == "poe-background":
        import background as _bg_mod
        command = " ".join(args.command)
        try:
            task = _bg_mod.start_background(command, timeout_seconds=args.timeout)
            if args.wait:
                task = _bg_mod.wait_background(task.id, timeout_seconds=args.timeout)
        except Exception as exc:
            return fail("E_POE_BACKGROUND", str(exc))
        if args.format == "json":
            print(json.dumps({
                "id": task.id,
                "command": task.command,
                "pid": task.pid,
                "status": task.status,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
                "exit_code": task.exit_code,
                "output_file": task.output_file,
            }, indent=2))
        else:
            print(f"id={task.id} pid={task.pid} status={task.status} command={task.command!r}")
            if task.completed_at:
                print(f"completed_at={task.completed_at} exit_code={task.exit_code}")
        return 0

    if args.cmd == "poe-hooks":
        import hooks as _hooks_mod

        registry = _hooks_mod.load_registry()

        if args.hooks_cmd == "list":
            hook_list = registry.list_hooks(scope=getattr(args, "scope", None))
            if getattr(args, "format", "text") == "json":
                from dataclasses import asdict
                print(json.dumps([asdict(h) for h in hook_list], indent=2))
            else:
                if not hook_list:
                    print("hooks=(none)")
                else:
                    for h in hook_list:
                        status = "enabled" if h.enabled else "disabled"
                        print(f"  [{h.id}] [{status:8s}] {h.name!r} type={h.hook_type} scope={h.scope} fire_on={h.fire_on}")
            return 0

        if args.hooks_cmd == "enable":
            if registry.enable(args.id):
                print(f"enabled={args.id}")
                return 0
            # Try to enable a builtin that isn't yet in registry
            builtin = _hooks_mod._BUILTIN_BY_ID.get(args.id)
            if builtin:
                import copy
                h = copy.copy(builtin)
                h.enabled = True
                registry.register(h)
                print(f"enabled={args.id} (registered builtin)")
                return 0
            return fail("E_HOOK_NOT_FOUND", args.id)

        if args.hooks_cmd == "disable":
            if registry.disable(args.id):
                print(f"disabled={args.id}")
                return 0
            return fail("E_HOOK_NOT_FOUND", args.id)

        if args.hooks_cmd == "add-reporter":
            import uuid as _uuid
            hook = _hooks_mod.Hook(
                id=str(_uuid.uuid4())[:8],
                name=args.name,
                scope=args.scope,
                hook_type=_hooks_mod.TYPE_REPORTER,
                enabled=True,
                prompt_template=getattr(args, "template", ""),
                report_target=args.target,
                fire_on=args.fire_on,
            )
            registry.register(hook)
            print(f"registered={hook.id} name={hook.name!r} scope={hook.scope} target={hook.report_target}")
            return 0

        if args.hooks_cmd == "run-builtin":
            builtin = _hooks_mod._BUILTIN_BY_ID.get(args.id)
            if not builtin:
                return fail("E_HOOK_NOT_FOUND", args.id)
            ctx = {
                "goal": getattr(args, "goal", ""),
                "step": getattr(args, "step", ""),
                "step_result": getattr(args, "result", ""),
                "project": "",
                "milestone_title": "",
                "feature_title": "",
                "validation_criteria": "",
                "features_summary": "",
                "features_done": 0,
                "features_total": 0,
            }
            dry_run = getattr(args, "dry_run", True)
            result = _hooks_mod._run_single_hook(builtin, ctx, dry_run=dry_run)
            if getattr(args, "format", "text") == "json":
                from dataclasses import asdict
                print(json.dumps(asdict(result), indent=2))
            else:
                print(f"hook_id={result.hook_id} status={result.status} should_block={result.should_block}")
                if result.output:
                    print(f"output: {result.output}")
                if result.injected_context:
                    print(f"injected_context: {result.injected_context[:200]}")
            return 0

    if args.cmd == "poe-skills":
        import skills as _skills_mod
        if args.list_skills:
            skill_list = _skills_mod.load_skills()
            if args.format == "json":
                print(json.dumps([_skills_mod._skill_to_dict(s) for s in skill_list], indent=2))
            else:
                if not skill_list:
                    print("skills=(none)")
                else:
                    for s in skill_list:
                        print(f"  [{s.id}] {s.name} (uses={s.use_count} success_rate={s.success_rate:.2f})")
                        print(f"    {s.description}")
                        print(f"    triggers: {', '.join(s.trigger_patterns[:3])}")
            return 0

        if args.extract:
            try:
                from memory import load_outcomes
                outcomes_raw = load_outcomes(limit=args.outcomes_window)
                from dataclasses import asdict
                outcomes_dicts = [asdict(o) for o in outcomes_raw]
            except Exception as exc:
                return fail("E_SKILLS_LOAD_OUTCOMES", str(exc))

            if args.dry_run:
                print(f"dry_run: would analyze {len(outcomes_dicts)} outcomes for skill extraction")
                return 0

            try:
                from llm import build_adapter, MODEL_MID
                skill_adapter = build_adapter(model=MODEL_MID)
                extracted = _skills_mod.extract_skills(outcomes_dicts, skill_adapter)
            except Exception as exc:
                return fail("E_SKILLS_EXTRACT", str(exc))

            if args.format == "json":
                print(json.dumps([_skills_mod._skill_to_dict(s) for s in extracted], indent=2))
            else:
                if not extracted:
                    print("extracted=(none)")
                else:
                    for s in extracted:
                        print(f"extracted: [{s.id}] {s.name} — {s.description}")
            return 0

        # Default: show usage hint
        print("Use --list to list skills or --extract to extract from recent outcomes.")
        return 0

    if args.cmd == "poe-inspector":
        from inspector import run_inspector, inspector_loop
        if args.loop:
            inspector_loop(interval_seconds=args.interval)
            return 0
        try:
            from llm import build_adapter, MODEL_CHEAP
            _insp_adapter = None if args.dry_run else build_adapter(model=MODEL_CHEAP)
        except Exception:
            _insp_adapter = None
        report = run_inspector(limit=args.limit, adapter=_insp_adapter, dry_run=args.dry_run)
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
        return 0

    if args.cmd in ("poe-inspector-status", "poe-quality"):
        from inspector import get_latest_inspection, get_friction_summary
        if getattr(args, "format", "text") == "json":
            report = get_latest_inspection()
            print(json.dumps(report.to_dict() if report else {}, indent=2))
        else:
            summary = get_friction_summary()
            if summary:
                print(summary)
            else:
                print("No inspection report available. Run poe-inspector first.")
        return 0

    # Phase 13: Poe CEO layer commands

    if args.cmd == "poe":
        from poe import poe_handle
        msg = " ".join(args.message)
        try:
            response = poe_handle(msg, model=args.model, dry_run=args.dry_run)
        except Exception as exc:
            return fail("E_POE_CEO", str(exc))
        if args.format == "json":
            print(json.dumps({
                "message": response.message,
                "routed_to": response.routed_to,
                "mission_id": response.mission_id,
                "executive_summary": response.executive_summary,
            }, indent=2))
        else:
            print(response.message)
        return 0

    if args.cmd == "poe-status":
        from poe import _compile_executive_summary
        dry_run = getattr(args, "dry_run", False)
        if dry_run:
            summary = "[dry-run] Executive summary: no active missions."
        else:
            try:
                from llm import build_adapter, MODEL_CHEAP
                _adapter = build_adapter(model=MODEL_CHEAP)
            except Exception:
                _adapter = None
            summary = _compile_executive_summary(adapter=_adapter)
        if args.format == "json":
            print(json.dumps({"summary": summary}, indent=2))
        else:
            print(summary)
        return 0

    if args.cmd == "poe-map":
        from goal_map import build_goal_map
        try:
            gmap = build_goal_map()
        except Exception as exc:
            return fail("E_POE_MAP", str(exc))
        if args.format == "json":
            nodes_list = [n.to_dict() for n in gmap.nodes.values()]
            print(json.dumps(nodes_list, indent=2))
        else:
            print(gmap.summary())
        return 0

    if args.cmd == "poe-autonomy":
        from autonomy import (
            load_config, set_default_tier, set_project_tier, set_action_tier,
            TIER_MANUAL, TIER_SAFE, TIER_FULL,
        )
        tier = getattr(args, "tier", None)
        project = getattr(args, "project", None)
        action_type = getattr(args, "action_type", None)

        if tier:
            if project:
                set_project_tier(project, tier)
                print(f"set project={project} tier={tier}")
            elif action_type:
                set_action_tier(action_type, tier)
                print(f"set action_type={action_type} tier={tier}")
            else:
                set_default_tier(tier)
                print(f"set default_tier={tier}")
            return 0

        # Show current config
        config = load_config()
        if args.format == "json":
            print(json.dumps(config.to_dict(), indent=2))
        else:
            print(f"default_tier={config.default_tier}")
            if config.project_overrides:
                print("project_overrides:")
                for p, t in sorted(config.project_overrides.items()):
                    print(f"  {p}: {t}")
            if config.action_overrides:
                print("action_overrides:")
                for a, t in sorted(config.action_overrides.items()):
                    print(f"  {a}: {t}")
        return 0

    # ---------------------------------------------------------------------------
    # Phase 14: Failure attribution + skill stats + skill test CLI
    # ---------------------------------------------------------------------------

    if args.cmd == "poe-attribution":
        from attribution import attribute_batch, load_attributions
        from memory import load_outcomes as _load_outcomes
        limit = getattr(args, "limit", 20)
        try:
            outcomes_raw = _load_outcomes(limit=limit * 2)
            outcomes_dicts = []
            for o in outcomes_raw:
                try:
                    from dataclasses import asdict
                    outcomes_dicts.append(asdict(o))
                except Exception:
                    outcomes_dicts.append(o.__dict__ if hasattr(o, "__dict__") else {})
            report = attribute_batch(outcomes_dicts[:limit])
        except Exception as exc:
            return fail("E_POE_ATTRIBUTION", str(exc))
        if args.format == "json":
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.summary())
            if report.attributions:
                print()
                print("Recent attributions:")
                for attr in report.attributions[:10]:
                    print(f"  [{attr.failure_mode}] conf={attr.confidence:.2f} | {attr.failed_step[:60]}")
        return 0

    if args.cmd == "poe-skill-stats":
        from skills import get_all_skill_stats, get_skills_needing_escalation, ESCALATION_THRESHOLD
        escalated = getattr(args, "escalated", False)
        try:
            if escalated:
                stats_list = get_skills_needing_escalation()
            else:
                stats_list = get_all_skill_stats()
        except Exception as exc:
            return fail("E_POE_SKILL_STATS", str(exc))
        if args.format == "json":
            print(json.dumps([s.to_dict() for s in stats_list], indent=2))
        else:
            if not stats_list:
                msg = "No skill stats recorded yet."
                if escalated:
                    msg = f"No skills below escalation threshold ({ESCALATION_THRESHOLD})."
                print(msg)
            else:
                if escalated:
                    print(f"Skills needing redesign (success_rate < {ESCALATION_THRESHOLD}):")
                else:
                    print("Per-skill success rates:")
                for s in stats_list:
                    escalation_marker = " [ESCALATE]" if s.needs_escalation else ""
                    print(
                        f"  {s.skill_id} | {s.skill_name[:30]:30s} | "
                        f"rate={s.success_rate:.2f} uses={s.total_uses} "
                        f"ok={s.successes} fail={s.failures}{escalation_marker}"
                    )
        return 0

    if args.cmd == "poe-skill-test":
        from skills import load_skills, generate_skill_tests, run_skill_tests
        skill_id = args.skill_id
        generate = getattr(args, "generate", False)

        # Find the skill
        all_skills = load_skills()
        target_skill = next((s for s in all_skills if s.id == skill_id or s.name == skill_id), None)
        if target_skill is None:
            return fail("E_SKILL_NOT_FOUND", f"No skill with id or name {skill_id!r}")

        try:
            if generate:
                # Generate new tests from recent failure attributions
                from attribution import load_attributions
                attributions = load_attributions(limit=20)
                failure_examples = [
                    a.raw_reason for a in attributions
                    if a.failed_skill == target_skill.name
                ]
                tests = generate_skill_tests(target_skill, failure_examples)
                print(f"Generated {len(tests)} test case(s) for skill={target_skill.name!r}")
            else:
                # Load existing tests
                from skills import _load_skill_tests
                tests = _load_skill_tests(skill_id)
                if not tests:
                    tests = _load_skill_tests(target_skill.id)

            if not tests:
                print(f"No tests found for skill={target_skill.name!r}. Use --generate to create them.")
                return 0

            passed, total = run_skill_tests(target_skill, tests, adapter=None, dry_run=True)
            if args.format == "json":
                print(json.dumps([t.to_dict() for t in tests], indent=2))
            else:
                print(f"Skill: {target_skill.name} (id={target_skill.id})")
                print(f"Tests: {total} | Passed (dry_run): {passed}")
                for t in tests:
                    print(f"  - [{t.input_description[:60]}] expect: {t.expected_keywords}")
        except Exception as exc:
            return fail("E_POE_SKILL_TEST", str(exc))
        return 0

    # ---------------------------------------------------------------------------
    # Phase 15: Gateway + Sandbox CLI handlers
    # ---------------------------------------------------------------------------

    if args.cmd == "poe-gateway":
        from gateway import check_gateway_connection, send_to_gateway

        if args.gateway_cmd == "status":
            connected = check_gateway_connection()
            if connected:
                print("gateway=reachable")
                return 0
            else:
                print("gateway=unreachable")
                return 1

        if args.gateway_cmd == "send":
            message = " ".join(args.message)
            result = send_to_gateway(message, timeout_seconds=args.timeout)
            if getattr(args, "format", "text") == "json":
                print(json.dumps({
                    "connected": result.connected,
                    "sent": result.sent,
                    "response": result.response,
                    "error": result.error,
                    "elapsed_ms": result.elapsed_ms,
                }, indent=2))
            else:
                print(f"connected={result.connected} sent={result.sent} elapsed_ms={result.elapsed_ms}")
                if result.response:
                    print(f"response={result.response}")
                if result.error:
                    print(f"error={result.error}")
            return 0 if result.sent else 1

    if args.cmd == "poe-sandbox":
        from skills import load_skills, generate_skill_tests, _load_skill_tests
        from sandbox import run_skill_tests_sandboxed

        skill_id = args.skill_id
        generate = getattr(args, "generate", False)

        all_skills = load_skills()
        target_skill = next((s for s in all_skills if s.id == skill_id or s.name == skill_id), None)
        if target_skill is None:
            return fail("E_SKILL_NOT_FOUND", f"No skill with id or name {skill_id!r}")

        try:
            if generate:
                from attribution import load_attributions
                attributions = load_attributions(limit=20)
                failure_examples = [
                    a.raw_reason for a in attributions
                    if a.failed_skill == target_skill.name
                ]
                tests = generate_skill_tests(target_skill, failure_examples)
                print(f"Generated {len(tests)} test case(s) for skill={target_skill.name!r}")
            else:
                tests = _load_skill_tests(skill_id)
                if not tests:
                    tests = _load_skill_tests(target_skill.id)

            if not tests:
                print(f"No tests found for skill={target_skill.name!r}. Use --generate to create them.")
                return 0

            passed, total = run_skill_tests_sandboxed(target_skill, tests)
            if getattr(args, "format", "text") == "json":
                print(json.dumps({
                    "skill_id": target_skill.id,
                    "skill_name": target_skill.name,
                    "passed": passed,
                    "total": total,
                    "tests": [t.to_dict() for t in tests],
                }, indent=2))
            else:
                print(f"Skill: {target_skill.name} (id={target_skill.id}) [sandboxed]")
                print(f"Tests: {total} | Passed: {passed}")
                for t in tests:
                    print(f"  - [{t.input_description[:60]}] expect: {t.expected_keywords}")
        except Exception as exc:
            return fail("E_POE_SANDBOX", str(exc))
        return 0 if (not tests or passed == total) else 1

    # Phase 17: Behavior-aligned skill router CLI handlers
    # ---------------------------------------------------------------------------

    if args.cmd == "poe-router":
        from router import get_router_stats, train_router, route_skills as _route_skills
        from skills import load_skills as _load_skills_r

        if args.router_cmd == "stats":
            stats = get_router_stats()
            fmt = getattr(args, "format", "text")
            if fmt == "json":
                print(json.dumps(stats.to_dict(), indent=2))
            else:
                print(f"training_samples={stats.training_samples}")
                print(f"last_trained={stats.last_trained or '(never)'}")
                print(f"holdout_accuracy={stats.holdout_accuracy:.3f}")
                print(f"feature_method={stats.feature_method}")
                print(f"min_samples_reached={stats.min_samples_reached}")
                print(f"model_path={stats.model_path}")
            return 0

        if args.router_cmd == "retrain":
            stats = train_router()
            fmt = getattr(args, "format", "text")
            if fmt == "json":
                print(json.dumps(stats.to_dict(), indent=2))
            else:
                if stats.min_samples_reached:
                    print(f"retrained ok — samples={stats.training_samples} accuracy={stats.holdout_accuracy:.3f}")
                else:
                    print(f"not enough data — samples={stats.training_samples} (need 50)")
            return 0

        if args.router_cmd == "route":
            goal_text = " ".join(args.goal)
            top_k = getattr(args, "top_k", 3)
            fmt = getattr(args, "format", "text")
            all_skills = _load_skills_r()
            results = _route_skills(goal_text, all_skills, top_k=top_k)
            if fmt == "json":
                print(json.dumps([
                    {"skill_id": r.skill_id, "skill_name": r.skill_name, "score": r.score, "method": r.method}
                    for r in results
                ], indent=2))
            else:
                if not results:
                    print("(no matching skills)")
                else:
                    for r in results:
                        print(f"  [{r.method}] score={r.score:.3f} {r.skill_name} (id={r.skill_id})")
            return 0

    return fail("E_INTERNAL", "unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
