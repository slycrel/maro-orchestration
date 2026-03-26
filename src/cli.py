#!/usr/bin/env python3
from __future__ import annotations

import argparse

from cli_args import build_parser
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
    parser = build_parser()
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

    if args.cmd == "poe-memory":
        from memory import (
            memory_status, run_decay_cycle, forget_lesson, promote_lesson,
            load_tiered_lessons, record_tiered_lesson, MemoryTier,
        )
        memory_cmd = getattr(args, "memory_cmd", None) or "status"
        if memory_cmd == "status":
            status = memory_status()
            print(json.dumps(status, indent=2))
        elif memory_cmd == "decay":
            tier = getattr(args, "tier", "medium")
            dry_run = getattr(args, "dry_run", False)
            result = run_decay_cycle(tier=tier, dry_run=dry_run)
            label = "(dry-run) " if dry_run else ""
            print(f"{label}tier={tier} decayed={result['decayed']} promoted={result['promoted']} gc={result['gc']}")
        elif memory_cmd == "forget":
            tier = getattr(args, "tier", "medium")
            removed = forget_lesson(args.lesson_id, tier=tier)
            if removed:
                print(f"Removed lesson_id={args.lesson_id} from tier={tier}")
            else:
                print(f"lesson_id={args.lesson_id} not found in tier={tier}")
                return 1
        elif memory_cmd == "promote":
            ok = promote_lesson(args.lesson_id)
            if ok:
                print(f"Promoted lesson_id={args.lesson_id} to long-tier")
            else:
                print(f"lesson_id={args.lesson_id} not eligible for promotion (score<{0.9} or sessions<3)")
                return 1
        elif memory_cmd == "list":
            tier = getattr(args, "tier", "medium")
            task_type = getattr(args, "task_type", None)
            lessons = load_tiered_lessons(tier=tier, task_type=task_type, min_score=0.0)
            if getattr(args, "format", "text") == "json":
                import dataclasses
                print(json.dumps([dataclasses.asdict(l) for l in lessons], indent=2))
            else:
                print(f"tier={tier} count={len(lessons)}")
                for l in lessons:
                    icon = "✓" if l.outcome == "done" else "✗"
                    print(f"  [{l.lesson_id}] score={l.score:.2f} sessions={l.sessions_validated} {icon} [{l.task_type}] {l.lesson[:80]}")
        elif memory_cmd == "record":
            tier = getattr(args, "tier", "medium")
            task_type = getattr(args, "task_type", "general")
            outcome = getattr(args, "outcome", "done")
            tl = record_tiered_lesson(args.lesson, task_type, outcome, source_goal="manual", tier=tier)
            print(f"Recorded lesson_id={tl.lesson_id} tier={tier} score={tl.score:.2f}")
        elif memory_cmd == "canon-candidates":
            from memory import get_canon_candidates
            min_hits = getattr(args, "min_hits", 10)
            min_task_types = getattr(args, "min_task_types", 3)
            candidates = get_canon_candidates(min_hits=min_hits, min_task_types=min_task_types)
            if getattr(args, "format", "text") == "json":
                print(json.dumps(candidates, indent=2))
            else:
                if not candidates:
                    print(f"No canon candidates (min_hits={min_hits}, min_task_types={min_task_types})")
                else:
                    print(f"Canon candidates ({len(candidates)}) — human review required before writing to AGENTS.md:")
                    for c in candidates:
                        print(f"\n  [{c['lesson_id']}] applied={c['times_applied']}x across {len(c['task_types_seen'])} task types")
                        print(f"  Task types: {', '.join(c['task_types_seen'])}")
                        print(f"  Lesson: {c['lesson']}")
                        print(f"  Score={c['score']} sessions={c['sessions_validated']} recorded={c['recorded_at']}")
                        print(f"  → {c['recommendation']}")
        else:
            print(f"Unknown poe-memory subcommand: {memory_cmd}")
            return 1
        return 0

    if args.cmd == "poe-persona":
        from persona import PersonaRegistry, compose_persona, spawn_persona, persona_to_dict
        registry = PersonaRegistry()
        persona_cmd = getattr(args, "persona_cmd", None) or "list"
        if persona_cmd == "list":
            names = registry.list()
            if not names:
                print("No personas found in personas/")
            else:
                print(f"Available personas ({len(names)}):")
                for n in names:
                    spec = registry.load(n)
                    if spec:
                        print(f"  {spec.name:20s} [{spec.model_tier:5s}] {spec.role}")
                    else:
                        print(f"  {n}")
        elif persona_cmd == "show":
            spec = registry.load(args.name)
            if spec is None:
                return fail("E_PERSONA_NOT_FOUND", f"Persona not found: {args.name!r}")
            if getattr(args, "format", "text") == "json":
                print(json.dumps(persona_to_dict(spec), indent=2))
            else:
                print(f"name:    {spec.name}")
                print(f"role:    {spec.role}")
                print(f"tier:    {spec.model_tier}")
                print(f"scope:   {spec.memory_scope}")
                print(f"style:   {spec.communication_style}")
                print(f"composes: {spec.composes or '(none)'}")
                print(f"hooks:   {spec.hooks or '(none)'}")
                print(f"source:  {spec.source_file}")
                print(f"\n--- System Prompt ---\n{spec.system_prompt[:500]}")
        elif persona_cmd == "compose":
            try:
                spec = compose_persona(*args.names, registry=registry)
            except ValueError as exc:
                return fail("E_PERSONA_COMPOSE", str(exc))
            if getattr(args, "format", "text") == "json":
                print(json.dumps(persona_to_dict(spec), indent=2))
            else:
                print(f"Composed: {spec.name}")
                print(f"role:     {spec.role}")
                print(f"tier:     {spec.model_tier}")
                print(f"scope:    {spec.memory_scope}")
                print(f"style:    {spec.communication_style}")
                print(f"hooks:    {spec.hooks or '(none)'}")
                print(f"\n--- Composed System Prompt (preview) ---\n{spec.system_prompt[:600]}")
        elif persona_cmd == "spawn":
            goal_str = " ".join(args.goal)
            compose_with = getattr(args, "compose", None) or None
            dry_run = getattr(args, "dry_run", False)
            max_steps = getattr(args, "max_steps", 20)
            result = spawn_persona(
                args.name, goal_str,
                registry=registry,
                dry_run=dry_run,
                max_steps=max_steps,
                compose_with=compose_with,
            )
            if getattr(args, "format", "text") == "json":
                import dataclasses
                print(json.dumps(dataclasses.asdict(result), indent=2))
            else:
                icon = "✓" if result.status == "done" else ("~" if result.status == "dry_run" else "✗")
                print(f"[{icon}] persona={result.persona_name} status={result.status} steps={result.steps_taken}")
                print(f"    {result.summary[:200]}")
            return 0 if result.status in ("done", "dry_run") else 1
        else:
            print(f"Unknown poe-persona subcommand: {persona_cmd}")
            return 1
        return 0

    if args.cmd == "poe-knowledge":
        from knowledge import print_dashboard, print_promote_actions
        knowledge_cmd = getattr(args, "knowledge_cmd", None)
        if knowledge_cmd == "promote":
            print_promote_actions()
        else:
            stage = getattr(args, "stage", None)
            print_dashboard(stage_filter=stage)
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
        from sandbox import run_skill_tests_sandboxed, load_audit_log, SandboxConfig

        sandbox_cmd = getattr(args, "sandbox_cmd", "test")

        if sandbox_cmd == "audit":
            entries = load_audit_log(limit=getattr(args, "limit", 20))
            if getattr(args, "format", "text") == "json":
                print(json.dumps(entries, indent=2))
            else:
                print(f"Sandbox audit log (last {len(entries)} entries):")
                for e in entries:
                    safe_icon = "✓" if e.get("static_safe") else "✗"
                    net_icon = "N" if e.get("network_blocked") else " "
                    venv_icon = "V" if e.get("venv_isolated") else " "
                    res_icon = "R" if e.get("resource_limited") else " "
                    ok = "ok" if e.get("success") else ("t/o" if e.get("timed_out") else "fail")
                    print(f"  [{e.get('timestamp','')[:19]}] {ok:4s} [{safe_icon}{net_icon}{venv_icon}{res_icon}] "
                          f"skill={e.get('skill_name','?')[:20]} exit={e.get('exit_code')} "
                          f"{e.get('elapsed_ms')}ms  {e.get('output_preview','')[:50]}")
            return 0

        if sandbox_cmd == "config":
            cfg = SandboxConfig()
            print("Sandbox hardening defaults:")
            print(f"  timeout_seconds:  {cfg.timeout_seconds}")
            print(f"  max_cpu_seconds:  {cfg.max_cpu_seconds}  (RLIMIT_CPU)")
            print(f"  max_file_size_mb: {cfg.max_file_size_mb}  (RLIMIT_FSIZE)")
            print(f"  max_open_files:   {cfg.max_open_files}  (RLIMIT_NOFILE)")
            print(f"  block_network:    {cfg.block_network}  (soft socket monkey-patch)")
            print(f"  use_venv:         {cfg.use_venv}  (isolated venv, ~500ms overhead)")
            print(f"  audit:            {cfg.audit}  (memory/sandbox-audit.jsonl)")
            return 0

        # sandbox_cmd == "test"
        from skills import load_skills, generate_skill_tests, _load_skill_tests
        skill_id = args.skill_id
        generate = getattr(args, "generate", False)

        all_skills = load_skills()
        target_skill = next((s for s in all_skills if s.id == skill_id or s.name == skill_id), None)
        if target_skill is None:
            return fail("E_SKILL_NOT_FOUND", f"No skill with id or name {skill_id!r}")

        sb_config = SandboxConfig(
            block_network=not getattr(args, "no_network_block", False),
            use_venv=getattr(args, "venv", False),
        )

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

            passed, total = run_skill_tests_sandboxed(target_skill, tests, config=sb_config)
            net_label = " [network-blocked]" if sb_config.block_network else ""
            venv_label = " [venv-isolated]" if sb_config.use_venv else ""
            if getattr(args, "format", "text") == "json":
                print(json.dumps({
                    "skill_id": target_skill.id,
                    "skill_name": target_skill.name,
                    "passed": passed,
                    "total": total,
                    "network_blocked": sb_config.block_network,
                    "venv_isolated": sb_config.use_venv,
                    "tests": [t.to_dict() for t in tests],
                }, indent=2))
            else:
                print(f"Skill: {target_skill.name} (id={target_skill.id}) [sandboxed]{net_label}{venv_label}")
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
