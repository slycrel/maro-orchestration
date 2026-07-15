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
    from orch_items import resolve_artifact_path as _resolve_ap
    path = Path(run.artifact_path) / "x-capture-salvage.json"
    root_path = _resolve_ap(run.artifact_path) / "x-capture-salvage.json"
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


def _cmd_init(args: argparse.Namespace) -> int:
    p = ensure_project(args.slug, " ".join(args.mission), priority=args.priority)
    write_operator_status()
    print(f"initialized={p}")
    return 0


def _cmd_next(args: argparse.Namespace) -> int:
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


def _cmd_done(args: argparse.Namespace) -> int:
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


def _cmd_log(args: argparse.Namespace) -> int:
    if not project_dir(args.project).exists():
        return fail("E_PROJECT_NOT_FOUND", args.project)
    append_decision(args.project, [" ".join(args.message)])
    print(f"project={args.project} logged=1")
    return 0


def _cmd_enqueue(args: argparse.Namespace) -> int:
    if not project_dir(args.project).exists():
        return fail("E_PROJECT_NOT_FOUND", args.project)
    task_text = " ".join(args.task).strip()
    if not task_text:
        return fail("E_TASK_REQUIRED", "task text cannot be empty")
    payload = f"project={args.project} :: {task_text}"
    # Use explicit --reason if provided, otherwise use constructed payload
    reason = args.reason if args.reason != "queued from orch" else payload
    blocked = [b.strip() for b in (args.blocked_by or "").split(",") if b.strip()]
    try:
        from task_store import enqueue as _enqueue
        task = _enqueue(
            lane=args.lane,
            source=args.source,
            reason=reason,
            parent_job_id=getattr(args, "parent_job_id", ""),
            blocked_by=blocked,
        )
        print(f"project={args.project} type=project_task lane={args.lane} job_id={task['job_id']} payload={json.dumps(payload)}")
    except Exception as exc:
        return fail("E_QUEUE_ENQUEUE", str(exc))
    return 0


def _cmd_blocked(args: argparse.Namespace) -> int:
    blocked = list_blocked_projects()
    if not blocked:
        print("blocked=(none)")
        return 0
    for b in blocked:
        print(f"project={b.slug} priority={b.priority} blocked={b.blocked} todo={b.todo}")
    return 0


def _cmd_salvage(args: argparse.Namespace) -> int:
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


def _cmd_report(args: argparse.Namespace) -> int:
    content = status_report_markdown(args.project) if args.format == "md" else status_report_json(args.project)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        print(f"written={out}")
    else:
        print(content, end="")
    return 0


def _cmd_start(args: argparse.Namespace) -> int:
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


def _cmd_finish(args: argparse.Namespace) -> int:
    try:
        run = finalize_run(args.run_id, args.status, note=args.note)
    except FileNotFoundError:
        return fail("E_RUN_NOT_FOUND", args.run_id)
    except ValueError as exc:
        return fail("E_FINISH_FAILED", str(exc))
    _print_run("finished", run)
    return 0


def _print_run_dir(rd, fmt: str) -> int:
    """Render a runs.py per-run dir for `maro inspect-run` (BACKLOG #18).

    The orch RunRecord store (`output/runs/<id>.json`) only covers the
    legacy cycle lane. Runs owned by maro-handle and the `maro run`/`maro
    resume` CLI lane live in `runs/<handle_id>-<nick>/` instead, so surface
    their metadata + attribution here rather than E_RUN_NOT_FOUND."""
    meta = {}
    mp = rd / "metadata.json"
    if mp.is_file():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    card = None
    cp = rd / "run_card.json"
    if cp.is_file():
        try:
            card = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:
            card = None
    attribution = {
        "environment": (rd / "source" / "environment.json").is_file(),
        "skills_manifest": (rd / "source" / "skills_manifest.jsonl").is_file(),
        "captains_log_slice": (rd / "build" / "captains_log_slice.jsonl").is_file(),
    }
    if fmt == "json":
        print(json.dumps({
            "run_dir": str(rd),
            "metadata": meta,
            "run_card": card,
            "attribution": attribution,
        }, indent=2))
    else:
        print(f"run_dir={rd}")
        print(f"handle_id={meta.get('handle_id', '')}")
        if meta.get("loop_id"):
            print(f"loop_id={meta['loop_id']}")
        print(f"nickname={meta.get('nickname', '')}")
        print(f"lane={meta.get('lane')}")
        print(f"model={meta.get('model')}")
        print(f"status={meta.get('status')}")
        print(f"prompt={meta.get('prompt', '')}")
        if "goal_achieved" in meta:
            print(f"goal_achieved={meta['goal_achieved']}")
        if meta.get("goal_verdict_summary"):
            print(f"goal_verdict_summary={meta['goal_verdict_summary']}")
        print(f"attribution.environment={attribution['environment']}")
        print(f"attribution.skills_manifest={attribution['skills_manifest']}")
        print(f"attribution.captains_log_slice={attribution['captains_log_slice']}")
    return 0


def _cmd_inspect_run(args: argparse.Namespace) -> int:
    try:
        run = load_run_record(args.run_id)
        summary = load_validation_summary(args.run_id)
    except FileNotFoundError:
        # Not an orch RunRecord — fall back to the runs.py per-run dir
        # (handle + `maro run`/`maro resume` lanes), resolvable by handle_id
        # or loop_id. E_RUN_NOT_FOUND only when neither store has it.
        try:
            from runs import resolve_run_dir as _resolve_run_dir
            _rd = _resolve_run_dir(args.run_id)
        except Exception:
            _rd = None
        if _rd is not None:
            return _print_run_dir(_rd, args.format)
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


def _cmd_cycle(args: argparse.Namespace) -> int:
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


def _cmd_outcomes(args: argparse.Namespace) -> int:
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
    return fail("E_INTERNAL", "unknown command")


def _cmd_sheriff(args: argparse.Namespace) -> int:
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
    if args.sheriff_cmd == "archive":
        rows = _sheriff_mod.archive_dormant_projects(days=args.days, apply=args.apply)
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
    return fail("E_INTERNAL", "unknown command")


def _cmd_director(args: argparse.Namespace) -> int:
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
        return fail("E_DIRECTOR", str(exc))
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


def _cmd_handle(args: argparse.Namespace) -> int:
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
            persona=getattr(args, "persona", None),
            measurement_class=args.measurement_class,
        )
    except Exception as exc:
        return fail("E_HANDLE", str(exc))
    print(result.format(mode=args.format))
    return 0 if result.status == "done" else 1


def _closure_verdict_pass(goal_str: str, result, *, dry_run: bool = False):
    """BACKLOG #18 (hermes trial, live specimen): every CLI lane that can mark
    "done" must run the same closure/verdict path as maro-handle — a
    third-party harness driving `maro run`/`maro resume` otherwise gets
    structurally-verified-only "done" with no goal_achieved verdict anywhere.

    Honesty-only parity: no closure-restart machinery, verdict stamped onto
    the outcomes row (loop-keyed; these lanes have no run-dir), done demoted
    to incomplete when a judged verdict contradicts it (same gate as
    handle.py: judged, confidence >= 0.7). If closure can't run (no adapter,
    LLM error), the verdict is simply absent — run history classifies that as
    done-unverified, never as verified done. Mutates result.status /
    result.stuck_reason in place; returns the ClosureVerdict or None.
    """
    _steps = getattr(result, "steps", None) or []
    if (dry_run
            or result.status not in ("done", "partial", "stuck", "restart")
            or not any(s.status == "done" for s in _steps)):
        return None
    try:
        from director import verify_goal_completion
        from llm import build_adapter, MODEL_CHEAP
        _cl_adapter = build_adapter(model=MODEL_CHEAP)
        _verdict = verify_goal_completion(
            goal_str,
            _steps,
            _cl_adapter,
            loop_id=result.loop_id or "",
            project=result.project or "",
        )
    except Exception as _cl_exc:
        print(f"[maro] closure verification unavailable ({_cl_exc}) — "
              "run stays done-unverified", file=sys.stderr)
        return None
    if _verdict is None or _verdict.checks_run <= 0:
        return _verdict
    _judged = getattr(_verdict, "judged", True)
    from audit_policy import persist_delivered_outcome_verdict
    _audit = persist_delivered_outcome_verdict(
        result.loop_id or "",
        goal_achieved=(bool(_verdict.complete) if _judged else None),
        goal_verdict_source=("closure" if _judged else "closure_unverifiable"),
        goal_verdict_confidence=float(_verdict.confidence),
        loop_ids=[result.loop_id] if result.loop_id else [],
    )
    if _audit.warning:
        result.audit_incomplete_warning = _audit.warning
        print(f"[maro] ⚠️ {_audit.warning}", file=sys.stderr)
    result.audit_learning_allowed = _audit.learning_allowed
    # BACKLOG #18 residual: the CLI lanes now own a run-dir, so stamp the
    # verdict into its metadata too — `maro inspect-run` shows goal_achieved
    # and run_curation folds it into run_card.json. No-op when no run-dir is
    # pinned (the outcomes annotation above stays the loop-keyed source of
    # truth either way), so the honesty-only unit tests are unaffected.
    try:
        from runs import stamp_run_metadata
        _vf = {
            "goal_verdict_confidence": float(_verdict.confidence),
            "goal_verdict_source": ("closure" if _judged else "closure_unverifiable"),
            "goal_verdict_summary": str(_verdict.summary)[:300],
        }
        if _judged:
            _vf["goal_achieved"] = bool(_verdict.complete)
        stamp_run_metadata(_vf)
    except Exception:
        pass
    # Status honesty: verified-done beats reported-done; unjudged verdicts
    # never demote.
    if (_judged and not _verdict.complete
            and _verdict.confidence >= 0.7
            and result.status == "done"):
        result.status = "incomplete"
        if result.stuck_reason is None:
            result.stuck_reason = (
                f"closure verification: {str(_verdict.summary)[:300]}"
            )
    return _verdict


def _finalize_cli_deferred_learning(result, *, adapter=None,
                                    dry_run: bool = False,
                                    verbose: bool = False) -> None:
    """Complete direct-CLI learning only after its verdict audit is durable."""
    if not getattr(result, "audit_learning_allowed", True):
        return
    try:
        from loop_finalize import finalize_deferred_learning
        finalize_deferred_learning(
            result,
            adapter=adapter,
            project=getattr(result, "project", "") or "",
            dry_run=dry_run,
            verbose=verbose,
        )
    except Exception as exc:
        print(f"[maro] deferred learning unavailable ({exc})", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    import agent_loop as _al
    import runs as _runs
    import uuid as _uuid
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

    # BACKLOG #18 residual: own a run-dir on the direct-CLI lane too, so this
    # run gets the same per-run attribution capture (environment snapshot,
    # skills manifest, captains-log slice, run_card) as maro-handle and is
    # visible to `maro inspect-run` / `maro viz search`. `runs.open_run` is
    # the identical "own a run" sequence handle.py uses — no duplication.
    handle_id = _uuid.uuid4().hex[:8]
    from ancestry import Origin
    _origin = Origin(source="cli-run")
    if getattr(args, "parent", None):
        _origin["parent_project"] = args.parent
    _rd = None
    try:
        _rd = _runs.open_run(
            handle_id, prompt=goal_str, model=args.model, lane="agenda",
            origin=_origin,
            measurement_class=args.measurement_class,
            dry_run=args.dry_run,
        )
    except Exception:
        _rd = None

    _status = "error"
    result = None
    _learning_adapter = None
    try:
        with _runs.scoped_run_dir(_rd):
            try:
                if not args.dry_run:
                    from llm import build_adapter
                    from conductor import assign_model_by_role
                    _learning_adapter = build_adapter(
                        model=args.model or assign_model_by_role("worker"))
                result = _al.run_agent_loop(
                    goal_str,
                    project=args.project,
                    model=args.model,
                    max_steps=args.max_steps,
                    max_iterations=args.max_iterations,
                    dry_run=args.dry_run,
                    verbose=args.verbose,
                    measurement_class=args.measurement_class,
                    handle_id=handle_id,
                    defer_learning=True,
                    adapter=_learning_adapter,
                )
            except Exception as exc:
                return fail("E_RUN", str(exc))
            _status = result.status
            # Stamp the loop_id so `maro inspect-run <loop_id>` / `maro resume`
            # can resolve this run-dir by the id the command printed.
            try:
                _runs.stamp_run_metadata({
                    "loop_id": result.loop_id,
                    "loop_ids": [result.loop_id] if result.loop_id else [],
                })
            except Exception:
                pass
            _verdict = _closure_verdict_pass(goal_str, result, dry_run=args.dry_run)
            _finalize_cli_deferred_learning(
                result, adapter=_learning_adapter,
                dry_run=args.dry_run, verbose=args.verbose)
            _status = result.status  # verdict may have demoted done→incomplete
        return _emit_run_output(args, result, _verdict)
    finally:
        if _rd is not None:
            try:
                _runs.close_run(handle_id, status=_status)
            except Exception:
                pass


def _emit_run_output(args, result, _verdict) -> int:
    _out = {
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
    }
    if _verdict is not None and _verdict.checks_run > 0:
        if getattr(_verdict, "judged", True):
            _out["goal_achieved"] = bool(_verdict.complete)
        _out["goal_verdict_summary"] = str(_verdict.summary)[:300]
    if getattr(result, "audit_incomplete_warning", ""):
        _out["audit_incomplete_warning"] = result.audit_incomplete_warning
    if args.format == "json":
        print(json.dumps(_out, indent=2))
    else:
        print(result.summary())
        if "goal_achieved" in _out:
            print(f"goal_achieved: {_out['goal_achieved']} — {_out['goal_verdict_summary']}")
    return 0 if result.status == "done" else 1


def _cmd_evolver(args: argparse.Namespace) -> int:
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
        ok = apply_suggestion(args.apply_id, manual=True)
        if ok:
            print(f"applied={args.apply_id}")
            return 0
        else:
            return fail("E_SUGGESTION_NOT_FOUND", args.apply_id)

    if getattr(args, "revert_id", None):
        from evolver import revert_suggestion
        result = revert_suggestion(args.revert_id)
        if result["reverted"]:
            print(f"reverted={args.revert_id}: {result['detail']}")
            return 0
        else:
            print(f"revert failed: {result['detail']}", file=sys.stderr)
            return 1

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


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    from heartbeat import run_heartbeat, heartbeat_loop
    if args.loop:
        heartbeat_loop(
            interval=args.interval,
            dry_run=args.dry_run,
            verbose=args.verbose,
            escalate=not args.no_escalate,
            autonomy=args.autonomy,
            backlog_every=args.backlog_every,
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


def _cmd_telegram(args: argparse.Namespace) -> int:
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


def _cmd_interrupt(args: argparse.Namespace) -> int:
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


def _warn_legacy_loop(cmd: str) -> None:
    """Deprecation notice for the pre-agent_loop heuristic executor.

    Jeremy confirmed 2026-07-09 these are unused (no scripts/cron/heartbeat
    call sites); superseded by `maro-handle` / `maro run`. Kept working for
    one deprecation window; the orch.py path/NEXT.md layer is NOT deprecated.
    """
    print(
        f"warning: `maro {cmd}` is deprecated (superseded by `maro-handle` / "
        f"`maro run`; see BACKLOG_DONE 2026-07-09) and will be removed in a "
        f"future release.",
        file=sys.stderr,
    )


def _cmd_plan(args: argparse.Namespace) -> int:
    _warn_legacy_loop("plan")
    try:
        result = plan_project(args.project, " ".join(args.goal), max_steps=args.max_steps)
    except ValueError as exc:
        return fail("E_PLAN_FAILED", str(exc))
    print(f"project={result.project} steps={len(result.steps)} added={len(result.item_indices)} first={result.item_indices[0] if result.item_indices else -1}")
    return 0


def _cmd_tick(args: argparse.Namespace) -> int:
    _warn_legacy_loop("tick")
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


def _cmd_loop(args: argparse.Namespace) -> int:
    _warn_legacy_loop("loop")
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


def _cmd_build_loop(args: argparse.Namespace) -> int:
    from build_loop_runner import build_loop_status_path, run_build_loop

    if args.max_runs <= 0:
        return fail("E_BUILD_LOOP_BAD_LIMIT", "max-runs must be greater than zero")
    try:
        summary = run_build_loop(
            project=args.project,
            worker=args.worker,
            worker_session=args.worker_session,
            max_runs=args.max_runs,
            max_retry_streak=args.max_retry_streak,
            max_attempts_per_item=args.max_attempts_per_item,
            continue_on_retry=not args.no_continue_on_retry,
            continue_on_blocked=args.continue_on_blocked,
        )
    except ValueError as exc:
        return fail("E_BUILD_LOOP_FAILED", str(exc))
    except Exception as exc:
        return fail("E_BUILD_LOOP_FAILED", str(exc))
    if args.format == "path":
        print(build_loop_status_path())
    else:
        print(json.dumps(summary, indent=2))
    return 0


def _cmd_ancestry(args: argparse.Namespace) -> int:
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


def _cmd_impact(args: argparse.Namespace) -> int:
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


def _cmd_metrics(args: argparse.Namespace) -> int:
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


def _cmd_contract(args: argparse.Namespace) -> int:
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
    return fail("E_INTERNAL", "unknown command")


def _cmd_boot(args: argparse.Namespace) -> int:
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


def _cmd_manifest(args: argparse.Namespace) -> int:
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


def _cmd_memory(args: argparse.Namespace) -> int:
    from memory import (
        memory_status, run_decay_cycle, forget_lesson, promote_lesson,
        load_tiered_lessons, record_tiered_lesson, MemoryTier,
    )
    memory_cmd = getattr(args, "memory_cmd", None) or "opstatus"
    if memory_cmd == "opstatus":
        status = memory_status()
        print(json.dumps(status, indent=2))
    elif memory_cmd == "decay":
        tier = getattr(args, "tier", "medium")
        dry_run = getattr(args, "dry_run", False)
        result = run_decay_cycle(tier=tier, dry_run=dry_run)
        label = "(dry-run) " if dry_run else ""
        print(f"{label}tier={tier} decayed={result['decayed']} promoted={result['promoted']} gc={result['gc']}")
    elif memory_cmd == "consolidate":
        from memory import maybe_consolidate, consolidation_due
        force = getattr(args, "force", False)
        if not force and not consolidation_due():
            print("Consolidation not due (marker within interval) — use --force to run anyway")
            return 0
        result = maybe_consolidate(force=force)
        if result is None:
            print("Consolidation skipped (disabled in config, not due, or failed — see logs)")
        else:
            cycle = result["medium"]
            print(f"Consolidated: decayed={cycle['decayed']} promoted={cycle['promoted']} gc={cycle['gc']}")
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
    elif memory_cmd == "migrate":
        import hashlib
        from pathlib import Path as _Path
        from memory_backends import JSONLBackend, SQLiteBackend
        from orch_items import memory_dir as _memory_dir_fn

        src_dir = _Path(args.src_dir) if getattr(args, "src_dir", None) else _memory_dir_fn()
        db_path = _Path(args.db_path) if getattr(args, "db_path", None) else src_dir / "memory.db"
        dry_run = getattr(args, "dry_run", False)

        jsonl_backend = JSONLBackend(src_dir)
        sqlite_backend = SQLiteBackend(db_path)

        # Discover all collections from .jsonl files on disk
        collections: list[str] = []
        for p in sorted(src_dir.rglob("*.jsonl")):
            rel = p.relative_to(src_dir)
            parts = rel.parts
            if len(parts) == 1:
                collections.append(parts[0].removesuffix(".jsonl"))
            elif len(parts) == 3 and parts[2] == "lessons.jsonl":
                # tiered/<tier>/lessons.jsonl → "tiered/<tier>"
                collections.append(f"{parts[0]}/{parts[1]}")

        total_skipped = 0
        total_inserted = 0
        for collection in collections:
            records = jsonl_backend.read_all(collection)
            if not records:
                continue

            # Build fingerprint set of existing SQLite rows for this collection
            existing_fps: set[str] = set()
            existing_rows = sqlite_backend.read_all(collection)
            for row in existing_rows:
                fp = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()
                existing_fps.add(fp)

            inserted = 0
            skipped = 0
            for record in records:
                fp = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
                if fp in existing_fps:
                    skipped += 1
                else:
                    if not dry_run:
                        sqlite_backend.append(collection, record)
                    existing_fps.add(fp)
                    inserted += 1

            label = "(dry-run) " if dry_run else ""
            print(f"{label}collection={collection} inserted={inserted} skipped={skipped}")
            total_inserted += inserted
            total_skipped += skipped

        label = "(dry-run) " if dry_run else ""
        print(f"{label}total inserted={total_inserted} skipped={total_skipped} db={db_path}")
    else:
        print(f"Unknown maro-memory subcommand: {memory_cmd}")
        return 1
    return 0


def _cmd_persona(args: argparse.Namespace) -> int:
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
    elif persona_cmd == "manifest":
        from persona import generate_manifest, save_manifest
        fmt = getattr(args, "format", "text")
        if fmt == "json":
            entries = generate_manifest(registry=registry)
            print(json.dumps({"agents": entries}, indent=2))
        elif fmt == "save":
            path = save_manifest(registry=registry, fmt="json")
            print(f"Manifest saved to: {path}")
        else:
            entries = generate_manifest(registry=registry)
            print(f"Agent Capability Manifest ({len(entries)} agents)")
            print("─" * 60)
            for e in entries:
                tier = e.get("model_tier", "?")
                role = e.get("role", "?")
                triggers = ", ".join(e.get("trigger_keywords", [])[:4])
                print(f"  {e['name']:30s} [{tier:5s}] {role}")
                if triggers:
                    print(f"  {'':30s}  triggers: {triggers}")
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
        print(f"Unknown maro-persona subcommand: {persona_cmd}")
        return 1
    return 0


def _cmd_knowledge(args: argparse.Namespace) -> int:
    from knowledge import print_dashboard, print_promote_actions
    knowledge_cmd = getattr(args, "knowledge_cmd", None)
    if knowledge_cmd == "promote":
        print_promote_actions()
    else:
        stage = getattr(args, "stage", None)
        print_dashboard(stage_filter=stage)
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    from eval import run_eval
    benchmark_ids = [args.benchmark_id] if getattr(args, "benchmark_id", None) else None
    report = run_eval(benchmarks=benchmark_ids, dry_run=args.dry_run)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    return 0


def _cmd_opstatus(args: argparse.Namespace) -> int:
    payload = write_operator_status()
    if args.format == "path":
        print(operator_status_path())
    else:
        print(json.dumps(payload, indent=2))
    return 0


def _cmd_mission(args: argparse.Namespace) -> int:
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
        return fail("E_MISSION", str(exc))
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


def _cmd_mission_status(args: argparse.Namespace) -> int:
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


def _cmd_background(args: argparse.Namespace) -> int:
    import background as _bg_mod
    command = " ".join(args.command)
    try:
        task = _bg_mod.start_background(command, timeout_seconds=args.timeout)
        if args.wait:
            task = _bg_mod.wait_background(task.id, timeout_seconds=args.timeout)
    except Exception as exc:
        return fail("E_BACKGROUND", str(exc))
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


def _cmd_hooks(args: argparse.Namespace) -> int:
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
    return fail("E_INTERNAL", "unknown command")


def _cmd_skills(args: argparse.Namespace) -> int:
    import skills as _skills_mod
    if getattr(args, "status", False):
        skill_list = _skills_mod.load_skills()
        rewrite_candidates = _skills_mod.skills_needing_rewrite()
        rewrite_ids = {s.id for s in rewrite_candidates}
        provisional = [s for s in skill_list if s.tier == "provisional"]
        established = [s for s in skill_list if s.tier == "established"]
        open_circuit = [s for s in skill_list if s.circuit_state == "open"]
        half_open = [s for s in skill_list if s.circuit_state == "half_open"]
        if args.format == "json":
            print(json.dumps({
                "total": len(skill_list),
                "provisional": len(provisional),
                "established": len(established),
                "circuit_open": len(open_circuit),
                "circuit_half_open": len(half_open),
                "rewrite_candidates": len(rewrite_candidates),
                "skills": [_skills_mod._skill_to_dict(s) for s in skill_list],
            }, indent=2))
        else:
            print(f"Skills: {len(skill_list)} total  |  {len(provisional)} provisional  {len(established)} established")
            print(f"Circuit: {len(skill_list) - len(open_circuit) - len(half_open)} closed  {len(half_open)} half-open  {len(open_circuit)} open")
            print(f"Rewrite candidates: {len(rewrite_candidates)}")
            if skill_list:
                print()
                # Sort by utility descending
                for s in sorted(skill_list, key=lambda x: x.utility_score, reverse=True):
                    circuit_tag = "" if s.circuit_state == "closed" else f" [{s.circuit_state.upper()}]"
                    rewrite_tag = " *REWRITE*" if s.id in rewrite_ids else ""
                    print(f"  {s.tier[0].upper()} {circuit_tag}  [{s.id}] {s.name}")
                    print(f"    utility={s.utility_score:.2f}  uses={s.use_count}  "
                          f"cf={s.consecutive_failures}  cs={s.consecutive_successes}"
                          f"{rewrite_tag}")
        return 0

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

    if getattr(args, "rollback", None):
        from skills import _skills_path as _sp
        import shutil as _shutil
        _src = _sp()
        _bak = Path(str(_src) + ".bak")
        if not _bak.exists():
            print(f"No backup found at {_bak}. Nothing to restore.")
            return 1
        if args.dry_run:
            print(f"dry_run: would restore {_bak} → {_src}")
            return 0
        _shutil.copy2(str(_bak), str(_src))
        restored = _skills_mod.load_skills()
        print(f"Restored skills.jsonl from .bak ({len(restored)} skills).")
        return 0

    # Default: show usage hint
    print("Use --status for health dashboard, --list to list skills, --extract to extract from recent outcomes, or --rollback <name> to restore from backup.")
    return 0


def _cmd_inspector(args: argparse.Namespace) -> int:
    from inspector import run_inspector, inspector_loop
    if args.loop:
        inspector_loop(interval_seconds=args.interval)
        return 0
    try:
        from llm import build_adapter, MODEL_CHEAP
        _insp_adapter = None if args.dry_run else build_adapter(model=MODEL_CHEAP)
    except ImportError:
        _insp_adapter = None
    report = run_inspector(limit=args.limit, adapter=_insp_adapter, dry_run=args.dry_run)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())
    return 0


def _cmd_inspector_status(args: argparse.Namespace) -> int:
    from inspector import get_latest_inspection, get_friction_summary
    if getattr(args, "format", "text") == "json":
        report = get_latest_inspection()
        print(json.dumps(report.to_dict() if report else {}, indent=2))
    else:
        summary = get_friction_summary()
        if summary:
            print(summary)
        else:
            print("No inspection report available. Run maro-inspector first.")
    return 0

# Conductor — top-level orchestration role


def _cmd_conductor(args: argparse.Namespace) -> int:
    from conductor import conduct
    msg = " ".join(args.message)
    try:
        response = conduct(msg, model=args.model, dry_run=args.dry_run)
    except Exception as exc:
        return fail("E_CONDUCTOR", str(exc))
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


def _cmd_status(args: argparse.Namespace) -> int:
    from conductor import _compile_executive_summary
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


def _cmd_map(args: argparse.Namespace) -> int:
    from goal_map import build_goal_map
    try:
        gmap = build_goal_map()
    except Exception as exc:
        return fail("E_MAP", str(exc))
    if args.format == "json":
        nodes_list = [n.to_dict() for n in gmap.nodes.values()]
        print(json.dumps(nodes_list, indent=2))
    else:
        print(gmap.summary())
    return 0


def _cmd_autonomy(args: argparse.Namespace) -> int:
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


def _cmd_attribution(args: argparse.Namespace) -> int:
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
        return fail("E_ATTRIBUTION", str(exc))
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


def _cmd_skill_stats(args: argparse.Namespace) -> int:
    from skills import get_all_skill_stats, get_skills_needing_escalation, ESCALATION_THRESHOLD
    escalated = getattr(args, "escalated", False)
    try:
        if escalated:
            stats_list = get_skills_needing_escalation()
        else:
            stats_list = get_all_skill_stats()
    except Exception as exc:
        return fail("E_SKILL_STATS", str(exc))
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


def _cmd_skill_test(args: argparse.Namespace) -> int:
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
        return fail("E_SKILL_TEST", str(exc))
    return 0

# ---------------------------------------------------------------------------
# Phase 15: Gateway + Sandbox CLI handlers
# ---------------------------------------------------------------------------


def _cmd_gateway(args: argparse.Namespace) -> int:
    from gateway import check_gateway_connection, send_to_gateway

    if args.gateway_cmd == "opstatus":
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
    return fail("E_INTERNAL", "unknown command")


def _cmd_viz(args: argparse.Namespace) -> int:
    if args.viz_cmd == "serve":
        from viz_server import serve
        serve(host=args.host, port=args.port)
        return 0
    if args.viz_cmd == "backfill":
        from loop_report import backfill_run_reports
        counts = backfill_run_reports(force=args.force, limit=args.limit)
        print(f"scanned {counts['runs_scanned']} run dir(s): "
              f"{counts['written']} report(s) written, {counts['skipped']} skipped, "
              f"{counts['failed']} failed; index rebuilt")
        return 0 if counts["failed"] == 0 else 1
    if args.viz_cmd == "search":
        from loop_report import search_runs
        results = search_runs(
            goal=args.goal, status=args.status, lane=args.lane,
            since=args.since, until=args.until,
        )
        if args.limit is not None:
            results = results[: args.limit]
        if args.format == "json":
            print(json.dumps(results, indent=2, default=str))
        else:
            if not results:
                print("(no matching runs)")
            for s in results:
                cost = s.get("cost_usd")
                cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) else "-"
                started = (s.get("started_at") or "")[:19].replace("T", " ")
                goal_preview = (s.get("goal") or "")[:80].replace("\n", " ")
                print(f"{started}  {s.get('status'):<18} {(s.get('lane') or '-'):<9} "
                      f"{cost_str:>9}  {s.get('handle_id')}  {goal_preview}")
            print(f"{len(results)} run(s)")
        return 0
    return fail("E_INTERNAL", "unknown command")


# Phase 17: Behavior-aligned skill router CLI handlers
# ---------------------------------------------------------------------------


def _cmd_router(args: argparse.Namespace) -> int:
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


def _resume_lock_name(identity: str) -> str:
    """Stable, path-safe admission-lock name for one resumable run."""
    import hashlib
    return "resume-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _load_resume_checkpoint(ref: str):
    """Resolve a loop/handle reference to its latest durable checkpoint."""
    from checkpoint import load_checkpoint

    ckpt = load_checkpoint(ref)
    if ckpt is not None:
        return ckpt
    try:
        import json as _json
        from runs import run_dir
        path = run_dir(ref) / "build" / "checkpoint.json"
        ckpt_data = _json.loads(path.read_text(encoding="utf-8"))
        from checkpoint import Checkpoint
        return Checkpoint.from_dict(ckpt_data)
    except Exception:
        return None


def _cmd_resume(args: argparse.Namespace) -> int:
    """Resume a crashed run from its checkpoint ((h) slice 3).

    Accepts a handle_id (resolves the run dir's checkpoint) or a loop_id.
    Refuses runs that finalized or whose owner PID is still alive.
    """
    ref = args.run_id
    ckpt = _load_resume_checkpoint(ref)
    if ckpt is None:
        return fail("E_RESUME", f"no checkpoint found for {ref!r}")

    # The run handle owns checkpoint/metadata/report/artifact state across a
    # resume. Claim it before any liveness/status check so two terminals cannot
    # both pass a TOCTOU window and duplicate the remaining side effects.
    # Legacy checkpoints without a handle fall back to their loop id.
    from proc_lock import acquire_pidfile, read_holder

    _resume_identity = ckpt.handle_id or ckpt.loop_id or ref
    _lock_name = _resume_lock_name(_resume_identity)
    _acquired = acquire_pidfile(
        _lock_name,
        payload={
            "handle_id": ckpt.handle_id or "",
            "loop_id": ckpt.loop_id,
            "resume_ref": ref,
            "goal": ckpt.goal[:120],
            "command": f"maro resume {ref}",
        },
    )
    if _acquired.status != "acquired":
        _holder = {}
        if _acquired.status == "busy":
            import time as _time
            for _ in range(4):
                _candidate = read_holder(_lock_name)
                if _candidate:
                    try:
                        os.kill(int(_candidate.get("pid", 0)), 0)
                        _holder = _candidate
                        break
                    except PermissionError:
                        _holder = _candidate
                        break
                    except (ProcessLookupError, TypeError, ValueError):
                        pass
                _time.sleep(0.01)
        if _acquired.status == "busy" and _holder:
            _holder_label = (
                f"pid {_holder.get('pid', '?')}, "
                f"loop {_holder.get('loop_id', '?')}, "
                f"started {_holder.get('started_at', '?')}"
            )
            _reason = (
                f"resume for run {_resume_identity!r} refused immediately: "
                f"another resume holds the admission lock ({_holder_label})"
            )
            _error_code = "E_RESUME_BUSY"
            _event_type = "resume_refused_busy"
            _status = "refused_busy"
        elif _acquired.status == "busy":
            _reason = (
                f"resume for run {_resume_identity!r} refused immediately: "
                "another resume holds the admission lock (holder details unavailable)"
            )
            _error_code = "E_RESUME_BUSY"
            _event_type = "resume_refused_busy"
            _status = "refused_busy"
        else:
            _reason = (
                f"resume for run {_resume_identity!r} refused immediately: "
                f"the admission lock is unavailable ({_acquired.error or 'unknown error'})"
            )
            _error_code = "E_RESUME_LOCK"
            _event_type = "resume_lock_unavailable"
            _status = "lock_unavailable"
        try:
            import notify as _notify
            _notify.emit(_event_type, {
                "handle_id": ckpt.handle_id or "",
                "loop_id": ckpt.loop_id,
                "resume_ref": ref,
                "status": _status,
                "reason": _reason,
                "summary": _reason,
                "holder": _holder,
                "blocking": False,
            })
        except Exception:
            pass
        return fail(_error_code, _reason)

    _resume_lock = _acquired.handle
    # The first read selected the lock identity. Re-read under that lock so a
    # resume that completed between selection and acquisition cannot leave us
    # executing an older completed-step snapshot.
    _fresh_ckpt = _load_resume_checkpoint(ref)
    if _fresh_ckpt is None:
        _resume_lock.close()
        return fail("E_RESUME", f"checkpoint disappeared for {ref!r}")
    ckpt = _fresh_ckpt

    if ckpt.is_complete():
        _resume_lock.close()
        return fail("E_RESUME",
                    f"loop {ckpt.loop_id} completed all its steps — nothing to resume")
    if ckpt.is_consumed():
        _resume_lock.close()
        return fail(
            "E_RESUME",
            f"loop {ckpt.loop_id} was already resumed successfully as "
            f"{ckpt.resumed_to_loop_id or 'a newer loop'}",
        )

    pid = int((ckpt.in_flight or {}).get("pid", 0) or 0)
    if pid:
        try:
            os.kill(pid, 0)
            _resume_lock.close()
            return fail("E_RESUME",
                        f"loop {ckpt.loop_id} appears to still be running (pid {pid})")
        except (ProcessLookupError, PermissionError, ValueError):
            pass

    _measurement_class = ""
    _resume_model = None
    if ckpt.handle_id:
        try:
            import json as _json
            from runs import run_dir
            meta = _json.loads((run_dir(ckpt.handle_id) / "metadata.json")
                               .read_text(encoding="utf-8"))
            _measurement_class = str(meta.get("measurement_class") or "")
            _resume_model = meta.get("model") or None
            if meta.get("status") == "done":
                _resume_lock.close()
                return fail("E_RESUME",
                            f"run {ckpt.handle_id} already finalized done")
        except FileNotFoundError:
            pass
        except Exception:
            pass

    print(f"[maro] resuming loop {ckpt.loop_id}: "
          f"{len(ckpt.completed)}/{len(ckpt.steps)} steps done"
          + (f", step {ckpt.in_flight['index']} was in flight"
             if ckpt.in_flight else ""))

    import agent_loop as _al
    import runs as _runs
    import uuid as _uuid
    # BACKLOG #18 residual: own a run-dir for the resumed run too. Reuse the
    # original run-dir when the checkpoint carries a handle_id (open_run is
    # idempotent — started_at/prompt.txt are preserved); otherwise mint one so
    # a loop_id-only checkpoint still gets attribution capture + inspectability.
    handle_id = ckpt.handle_id or _uuid.uuid4().hex[:8]
    _rd = None
    try:
        from ancestry import Origin
        _rd = _runs.open_run(
            handle_id, prompt=ckpt.goal, lane="agenda",
            origin=Origin(source="cli-resume", resumed_from=ckpt.loop_id),
        )
    except Exception:
        _rd = None
    _status = "error"
    result = None
    _learning_adapter = None
    try:
        with _runs.scoped_run_dir(_rd):
            try:
                from llm import build_adapter
                from conductor import assign_model_by_role
                _learning_adapter = build_adapter(
                    model=_resume_model or assign_model_by_role("worker"))
                result = _al.run_agent_loop(
                    ckpt.goal,
                    project=ckpt.project or None,
                    resume_from_loop_id=ckpt.loop_id,
                    verbose=args.verbose,
                    measurement_class=_measurement_class,
                    handle_id=handle_id,
                    defer_learning=True,
                    adapter=_learning_adapter,
                )
            except Exception as exc:
                return fail("E_RESUME", str(exc))
            _status = result.status
            try:
                _runs.stamp_run_metadata({
                    "loop_id": result.loop_id,
                    "loop_ids": [result.loop_id] if result.loop_id else [],
                })
            except Exception:
                pass
            _verdict = _closure_verdict_pass(ckpt.goal, result)
            _finalize_cli_deferred_learning(
                result, adapter=_learning_adapter, verbose=args.verbose)
            _status = result.status
        # A successful resume has a new durable checkpoint/run record. Consume
        # the source checkpoint so invoking the same legacy loop id again
        # cannot replay its old remaining-step snapshot and duplicate effects.
        if result.status == "done" and not ckpt.handle_id:
            try:
                from checkpoint import mark_checkpoint_consumed
                if not mark_checkpoint_consumed(
                        ckpt.loop_id, resumed_to_loop_id=result.loop_id):
                    result.status = "incomplete"
                    result.stuck_reason = (
                        "resume completed, but the source checkpoint could not "
                        "be marked consumed; refusing a success status because "
                        "the old resume id could replay external effects"
                    )
            except Exception:
                result.status = "incomplete"
                result.stuck_reason = (
                    "resume completed, but source-checkpoint consumption failed")
        _status = result.status
        _resume_out = {"loop_id": result.loop_id, "status": result.status,
                       "resumed_from": ckpt.loop_id}
        if _verdict is not None and _verdict.checks_run > 0 and getattr(_verdict, "judged", True):
            _resume_out["goal_achieved"] = bool(_verdict.complete)
        if getattr(result, "audit_incomplete_warning", ""):
            _resume_out["audit_incomplete_warning"] = result.audit_incomplete_warning
        if args.format == "json":
            print(json.dumps(_resume_out))
        else:
            print(f"[maro] resume finished: {result.status}")
            if "goal_achieved" in _resume_out:
                print(f"goal_achieved: {_resume_out['goal_achieved']}")
        return 0 if result.status == "done" else 1
    finally:
        if _rd is not None:
            try:
                _runs.close_run(handle_id, status=_status)
            except Exception:
                pass
        try:
            _resume_lock.close()
        except Exception:
            pass


_COMMAND_HANDLERS = {
    "init": _cmd_init,
    "resume": _cmd_resume,
    "next": _cmd_next,
    "done": _cmd_done,
    "log": _cmd_log,
    "enqueue": _cmd_enqueue,
    "blocked": _cmd_blocked,
    "salvage": _cmd_salvage,
    "report": _cmd_report,
    "start": _cmd_start,
    "finish": _cmd_finish,
    "inspect-run": _cmd_inspect_run,
    "cycle": _cmd_cycle,
    "outcomes": _cmd_outcomes,
    "sheriff": _cmd_sheriff,
    "director": _cmd_director,
    "handle": _cmd_handle,
    "run": _cmd_run,
    "evolver": _cmd_evolver,
    "heartbeat": _cmd_heartbeat,
    "telegram": _cmd_telegram,
    "interrupt": _cmd_interrupt,
    "plan": _cmd_plan,
    "tick": _cmd_tick,
    "loop": _cmd_loop,
    "build-loop": _cmd_build_loop,
    "ancestry": _cmd_ancestry,
    "impact": _cmd_impact,
    "metrics": _cmd_metrics,
    "contract": _cmd_contract,
    "boot": _cmd_boot,
    "manifest": _cmd_manifest,
    "memory": _cmd_memory,
    "persona": _cmd_persona,
    "knowledge": _cmd_knowledge,
    "eval": _cmd_eval,
    "opstatus": _cmd_opstatus,
    "mission": _cmd_mission,
    "mission-status": _cmd_mission_status,
    "background": _cmd_background,
    "hooks": _cmd_hooks,
    "skills": _cmd_skills,
    "inspector": _cmd_inspector,
    "inspector-status": _cmd_inspector_status,
    "quality": _cmd_inspector_status,
    "conductor": _cmd_conductor,
    "status": _cmd_status,
    "map": _cmd_map,
    "autonomy": _cmd_autonomy,
    "attribution": _cmd_attribution,
    "skill-stats": _cmd_skill_stats,
    "skill-test": _cmd_skill_test,
    "gateway": _cmd_gateway,
    "router": _cmd_router,
    "viz": _cmd_viz,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = _COMMAND_HANDLERS.get(args.cmd)
    if handler is None:
        return fail("E_INTERNAL", "unknown command")
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())


# ---------------------------------------------------------------------------
# Entry-point shims for pyproject.toml console_scripts
# Each injects the subcommand name as argv[0] so main() can dispatch correctly.
# ---------------------------------------------------------------------------

def _memory_main() -> None:
    import sys
    raise SystemExit(main(["memory"] + sys.argv[1:]))


def _persona_main() -> None:
    import sys
    raise SystemExit(main(["persona"] + sys.argv[1:]))


def _skills_main() -> None:
    import sys
    raise SystemExit(main(["skills"] + sys.argv[1:]))
