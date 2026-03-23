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
    p_poe_run.add_argument("--model", "-m", help="LLM model string")
    p_poe_run.add_argument("--max-steps", type=int, default=6, help="Max decomposition steps (default: 6)")
    p_poe_run.add_argument("--max-iterations", type=int, default=20, help="Hard cap on LLM calls")
    p_poe_run.add_argument("--dry-run", action="store_true", help="Simulate without API calls")
    p_poe_run.add_argument("--verbose", "-v", action="store_true", help="Print progress")
    p_poe_run.add_argument("--format", choices=["text", "json"], default="text", help="Output format")

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

    if args.cmd == "status":
        payload = write_operator_status()
        if args.format == "path":
            print(operator_status_path())
        else:
            print(json.dumps(payload, indent=2))
        return 0

    return fail("E_INTERNAL", "unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
