"""Argument parser setup for the Poe orchestration CLI.

Extracted from cli.py — no dependency on orch modules.
"""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
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
    p_poe_skills.add_argument("--status", action="store_true", help="Show skill health dashboard (Phase 32)")
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

    p_poe_sandbox = sub.add_parser("poe-sandbox", help="Skill sandbox isolation — test, audit, config (Phase 15+18)")
    sandbox_sub = p_poe_sandbox.add_subparsers(dest="sandbox_cmd", required=True)

    p_sb_test = sandbox_sub.add_parser("test", help="Run sandboxed tests for a skill")
    p_sb_test.add_argument("skill_id", help="Skill ID or name to test")
    p_sb_test.add_argument("--generate", action="store_true", help="Generate new tests from recent failures before running")
    p_sb_test.add_argument("--no-network-block", action="store_true", help="Disable soft network blocking (Phase 18)")
    p_sb_test.add_argument("--venv", action="store_true", help="Use isolated venv per execution — slow, ~500ms (Phase 18)")
    p_sb_test.add_argument("--format", choices=["text", "json"], default="text")
    p_sb_audit = sandbox_sub.add_parser("audit", help="Show recent sandbox execution audit log (Phase 18)")
    p_sb_audit.add_argument("--limit", type=int, default=20)
    p_sb_audit.add_argument("--format", choices=["text", "json"], default="text")
    sandbox_sub.add_parser("config", help="Show current sandbox hardening config defaults (Phase 18)")

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

    p_poe_memory = sub.add_parser("poe-memory", help="Tiered memory management — short/medium/long tiers with decay (Phase 16)")
    memory_sub = p_poe_memory.add_subparsers(dest="memory_cmd")
    memory_sub.add_parser("status", help="Show tier breakdown, counts, decay candidates, promote candidates")
    p_mem_forget = memory_sub.add_parser("forget", help="Permanently expire a lesson by ID")
    p_mem_forget.add_argument("lesson_id", help="Lesson ID to remove")
    p_mem_forget.add_argument("--tier", choices=["medium", "long"], default="medium")
    p_mem_decay = memory_sub.add_parser("decay", help="Run decay cycle: apply daily decay, auto-promote eligibles, GC stale entries")
    p_mem_decay.add_argument("--dry-run", action="store_true", help="Preview what would happen without writing")
    p_mem_decay.add_argument("--tier", choices=["medium", "long"], default="medium")
    p_mem_promote = memory_sub.add_parser("promote", help="Promote a medium-tier lesson to long-tier")
    p_mem_promote.add_argument("lesson_id", help="Lesson ID to promote")
    p_mem_list = memory_sub.add_parser("list", help="List lessons in a tier")
    p_mem_list.add_argument("--tier", choices=["medium", "long"], default="medium")
    p_mem_list.add_argument("--task-type", default=None)
    p_mem_list.add_argument("--format", choices=["text", "json"], default="text")
    p_mem_record = memory_sub.add_parser("record", help="Manually record a tiered lesson")
    p_mem_record.add_argument("lesson", help="Lesson text")
    p_mem_record.add_argument("--task-type", default="general")
    p_mem_record.add_argument("--outcome", choices=["done", "stuck"], default="done")
    p_mem_record.add_argument("--tier", choices=["medium", "long"], default="medium")
    p_mem_canon = memory_sub.add_parser("canon-candidates", help="Show long-tier lessons eligible for AGENTS.md identity promotion (human review required)")
    p_mem_canon.add_argument("--min-hits", type=int, default=10, help="Minimum times_applied (default 10)")
    p_mem_canon.add_argument("--min-task-types", type=int, default=3, help="Minimum distinct task types seen (default 3)")
    p_mem_canon.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_persona = sub.add_parser("poe-persona", help="Persona system — list, show, compose, and spawn agent personas (Phase 20)")
    persona_sub = p_poe_persona.add_subparsers(dest="persona_cmd")
    persona_sub.add_parser("list", help="List all available personas")
    p_persona_show = persona_sub.add_parser("show", help="Show full spec for a persona")
    p_persona_show.add_argument("name", help="Persona name")
    p_persona_show.add_argument("--format", choices=["text", "json"], default="text")
    p_persona_spawn = persona_sub.add_parser("spawn", help="Spawn a persona agent loop for a goal")
    p_persona_spawn.add_argument("name", help="Persona name")
    p_persona_spawn.add_argument("goal", nargs="+", help="Goal for this spawn")
    p_persona_spawn.add_argument("--compose", nargs="*", default=None, help="Additional persona names to compose with")
    p_persona_spawn.add_argument("--dry-run", action="store_true", help="Show what would happen without executing")
    p_persona_spawn.add_argument("--max-steps", type=int, default=20)
    p_persona_spawn.add_argument("--format", choices=["text", "json"], default="text")
    p_persona_compose = persona_sub.add_parser("compose", help="Show a composed persona spec without spawning")
    p_persona_compose.add_argument("names", nargs="+", help="Persona names to compose")
    p_persona_compose.add_argument("--format", choices=["text", "json"], default="text")

    p_poe_knowledge = sub.add_parser("poe-knowledge", help="Crystallization dashboard — view all knowledge graduation stages (Phase 22)")
    knowledge_sub = p_poe_knowledge.add_subparsers(dest="knowledge_cmd")
    p_knowledge_status = knowledge_sub.add_parser("status", help="Full crystallization dashboard")
    p_knowledge_status.add_argument("--stage", type=int, choices=[2, 3, 4, 5], help="Show only one stage")
    knowledge_sub.add_parser("promote", help="List available promotion actions (read-only)")

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

    return parser
