#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from orch import (
    append_decision,
    command_execution_bridge,
    ensure_project,
    finalize_run,
    load_run_record,
    list_blocked_projects,
    run_loop,
    mark_first_todo_done,
    mark_item,
    operator_status_path,
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

    p_blocked = sub.add_parser("blocked", help="List blocked projects")

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

    p_run = sub.add_parser("run", help="Run one orchestration cycle")
    p_run.add_argument("--project")
    p_run.add_argument("--worker", default="handle")
    p_run.add_argument("--source", default="run-once")
    p_run.add_argument("--note")
    p_run.add_argument("--finish", choices=["done", "blocked"], help="Immediately finalize the claimed item")
    p_run.add_argument("--finish-note")

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

    p_loop = sub.add_parser("loop", help="Run a bounded automation loop")
    p_loop.add_argument("--project")
    p_loop.add_argument("--worker", default="handle")
    p_loop.add_argument("--source", default="loop")
    p_loop.add_argument("--note")
    p_loop.add_argument("--max-runs", type=int, default=10)
    p_loop.add_argument("--exec-cmd", help="Shell command execution bridge for each claimed task")

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

    if args.cmd == "blocked":
        blocked = list_blocked_projects()
        if not blocked:
            print("blocked=(none)")
            return 0
        for b in blocked:
            print(f"project={b.slug} priority={b.priority} blocked={b.blocked} todo={b.todo}")
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

    if args.cmd == "plan":
        try:
            result = plan_project(args.project, " ".join(args.goal), max_steps=args.max_steps)
        except ValueError as exc:
            return fail("E_PLAN_FAILED", str(exc))
        print(f"project={result.project} steps={len(result.steps)} added={len(result.item_indices)} first={result.item_indices[0] if result.item_indices else -1}")
        return 0

    if args.cmd == "tick":
        execution = command_execution_bridge(args.exec_cmd) if args.exec_cmd else None
        try:
            tick = run_tick(project=args.project, worker=args.worker, source=args.source, note=args.note, execution=execution)
        except ValueError as exc:
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
        execution = command_execution_bridge(args.exec_cmd) if args.exec_cmd else None
        try:
            ticks = run_loop(project=args.project, worker=args.worker, source=args.source, note=args.note, max_runs=args.max_runs, execution=execution)
        except ValueError as exc:
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
