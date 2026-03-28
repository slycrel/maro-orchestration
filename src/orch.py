#!/usr/bin/env python3
"""Poe orchestration core utilities."""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from shlex import quote
from typing import Any, Callable, Iterable, List, Optional, Tuple

from orch_items import (
    STATE_TODO,
    STATE_DOING,
    STATE_DONE,
    STATE_BLOCKED,
    VALID_STATES,
    RUN_OUTCOMES,
    WORKER_NAME_RE,
    X_CAPTURE_AUTH_MARKERS,
    X_CAPTURE_RATE_LIMIT_MARKERS,
    ITEM_RE,
    NextItem,
    ProjectStatus,
    RunRecord,
    ExecutionResult,
    ValidationResult,
    TickResult,
    PlanResult,
    ExecutionBridge,
    ValidationBridge,
    ws_root,
    orch_root,
    memory_dir,
    projects_root,
    output_root,
    runs_root,
    workers_root,
    operator_status_path,
    project_dir,
    next_path,
    decisions_path,
    risks_path,
    provenance_path,
    priority_path,
    now_utc_iso,
    new_run_id,
    list_projects,
    project_priority,
    _run_record_path,
    write_run_record,
    load_run_record,
    validation_summary_path,
    load_validation_summary,
    _load_run_records,
    _next_attempt,
    _run_artifact_root,
    parse_next,
    write_next_lines,
    mark_item,
    append_next_items,
    get_item,
    decompose_goal,
    plan_project,
    mark_first_todo_done,
    select_next_item,
    item_counts,
    project_status,
    select_global_next,
    list_blocked_projects,
    append_section_lines,
    append_decision,
    append_risk,
    append_provenance,
    ensure_project,
)



from orch_bridges import (
    ExecutionBridgeError,
    _validation_trace_event,
    named_validation_bridge,
    WorkerSessionSpec,
    _ensure_nonempty_artifact_name,
    _coerce_session_file_name,
    _coerce_session_directory_name,
    _coerce_env_map,
    _coerce_positive_timeout,
    _load_worker_session_manifest,
    _coerce_artifact_path,
    _extract_session_result_from_text,
    _extract_json_result,
    _coerce_validation_payload,
    _run_status_summary_record_path,
    _append_jsonl_record,
    _read_jsonl_records,
    _resolve_review_artifact_root,
    _validation_bridge_name,
    _salvage_match_kinds,
    _repeated_salvage_streak,
    resolve_worker_session_script,
    resolve_worker_session_spec,
    command_execution_bridge,
    worker_session_bridge,
    session_execution_bridge,
    _default_execution_bridge,
    _run_artifact_signature,
    _matching_attempt_signatures,
    _validation_status_for_record,
    _retry_streak_for_item,
    artifact_progress_validation_bridge,
    x_capture_salvage_validation_bridge,
    artifact_validation_bridge,
    review_command_validation_bridge,
    chain_validation_bridges,
    _default_validation_bridge,
    _merge_notes,
)


# ---------------------------------------------------------------------------
# Run orchestration helpers (depend on both orch_items and orch_bridges)
# ---------------------------------------------------------------------------

def _mark_stale_running_attempts(project: str, item_index: int) -> None:
    stale_note = "superseded by a new attempt"
    for record in _load_run_records():
        if record.project == project and record.index == item_index and record.status == "running":
            record.status = "blocked"
            record.updated_at = now_utc_iso()
            record.finished_at = record.updated_at
            record.note = _merge_notes(record.note, stale_note)
            write_run_record(record)


def select_global_running_next() -> Optional[Tuple[str, NextItem]]:
    candidates: List[Tuple[int, str, str, NextItem]] = []
    for record in _load_run_records():
        if record.status != "running":
            continue
        try:
            item = get_item(record.project, record.index)
        except ValueError:
            continue
        if item.state != STATE_DOING:
            continue
        candidates.append((project_priority(record.project), record.updated_at, record.project, item))

    for _priority, _updated_at, project, item in sorted(candidates, key=lambda row: (row[0], row[1]), reverse=True):
        return project, item
    return None


def select_running_item(slug: str) -> Optional[NextItem]:
    candidates: List[Tuple[str, NextItem]] = []
    for record in _load_run_records():
        if record.project != slug or record.status != "running":
            continue
        try:
            item = get_item(record.project, record.index)
        except ValueError:
            continue
        if item.state != STATE_DOING:
            continue
        candidates.append((record.updated_at, item))

    for _updated_at, item in sorted(candidates, key=lambda row: row[0], reverse=True):
        return item
    return None


def _active_salvage_runs() -> List[dict]:
    out = []
    for record in _load_run_records():
        if record.status != "running":
            continue
        artifact_path = record.artifact_path
        if not artifact_path:
            continue
        salvage_path = orch_root() / artifact_path / "x-capture-salvage.json"
        if not salvage_path.exists():
            continue
        first_kind = None
        first_detail = None
        try:
            payload = json.loads(salvage_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                matches = payload.get("matches") or []
                first = next((item for item in matches if isinstance(item, dict)), None)
                if first:
                    first_kind = first.get("kind")
                    first_detail = first.get("detail")
        except (OSError, json.JSONDecodeError):
            pass
        out.append(
            {
                "run_id": record.run_id,
                "project": record.project,
                "item": record.index,
                "attempt": record.attempt,
                "artifact_path": record.artifact_path,
                "first_kind": first_kind,
                "first_detail": first_detail,
            }
        )
    return out


def _pending_salvage_count(active_runs: Optional[List[dict]] = None) -> int:
    if active_runs is None:
        active_runs = _active_salvage_runs()
    active_ids = {str(run.get("run_id")) for run in active_runs if run.get("run_id")}
    if not active_ids:
        return 0
    records = _read_jsonl_records(_run_status_summary_record_path())
    pending_ids = {str(record.get("run_id")) for record in records if record.get("run_id") in active_ids}
    return len(pending_ids)


def write_operator_status() -> dict:
    statuses = [project_status(slug) for slug in list_projects()]
    active = [s for s in statuses if s.doing > 0]
    blocked = [s for s in statuses if s.blocked > 0]
    next_sel = select_global_next()
    active_salvage_runs = _active_salvage_runs()
    payload = {
        "generated_at": now_utc_iso(),
        "project_count": len(statuses),
        "active_projects": [s.slug for s in active],
        "blocked_projects": [s.slug for s in blocked],
        "queue": {
            "todo": sum(s.todo for s in statuses),
            "doing": sum(s.doing for s in statuses),
            "blocked": sum(s.blocked for s in statuses),
            "done": sum(s.done for s in statuses),
        },
        "next": {
            "project": next_sel[0],
            "index": next_sel[1].index,
            "text": next_sel[1].text,
        } if next_sel else None,
        "salvage": {
            "active_runs": active_salvage_runs,
            "active_count": 0,
            "pending_count": 0,
            "index_path": str(_run_status_summary_record_path().relative_to(orch_root())),
        },
    }
    payload["salvage"]["active_count"] = len(active_salvage_runs)
    payload["salvage"]["pending_count"] = _pending_salvage_count(active_salvage_runs)
    operator_status_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def start_item(
    slug: str,
    item_index: Optional[int] = None,
    *,
    source: str = "manual",
    worker: str = "handle",
    note: Optional[str] = None,
    allow_running: bool = False,
) -> RunRecord:
    if not project_dir(slug).exists():
        raise ValueError(f"project {slug} does not exist")
    if item_index is None:
        item = select_next_item(slug)
        if not item:
            raise ValueError(f"no TODO item found for {slug}")
    else:
        item = get_item(slug, item_index)
        if item.state == STATE_TODO:
            mark_item(slug, item.index, STATE_DOING)
        elif item.state in {STATE_DOING, STATE_BLOCKED} and allow_running:
            pass
        else:
            raise ValueError(f"item_index {item_index} for {slug} is not TODO")
    started_at = now_utc_iso()
    run_id = new_run_id()
    run = RunRecord(
        run_id=run_id,
        project=slug,
        index=item.index,
        text=item.text,
        status="running",
        artifact_path=str((runs_root() / run_id).relative_to(orch_root())),
        attempt=_next_attempt(slug, item.index),
        source=source,
        worker=worker,
        started_at=started_at,
        updated_at=started_at,
        note=note,
    )
    _run_artifact_root(run)
    write_run_record(run)
    append_provenance(slug, [f"Started `{run.text}` via {source}/{worker} ({run.run_id}).", f"Artifact: `{run.artifact_path}`"])
    write_operator_status()
    return run


def finalize_run(run_id: str, status: str, *, note: Optional[str] = None) -> RunRecord:
    if status not in {"done", "blocked"}:
        raise ValueError(f"unsupported final status: {status}")
    run = load_run_record(run_id)
    state = STATE_DONE if status == "done" else STATE_BLOCKED
    current = get_item(run.project, run.index)
    if current.state != STATE_DOING:
        raise ValueError(f"cannot finalize {run_id}: item is not in progress")
    mark_item(run.project, run.index, state)
    now = now_utc_iso()
    run.status = status
    run.updated_at = now
    run.finished_at = now
    run.note = note or run.note
    write_run_record(run)
    if status == "done":
        append_decision(run.project, [f"Completed `{run.text}` ({run.run_id}).", *([run.note] if run.note else [])])
    else:
        append_risk(run.project, [f"Blocked `{run.text}` ({run.run_id}).", *([run.note] if run.note else [])])

    provenance_lines = [f"Finalized `{run.text}` as {status} ({run.run_id}).", f"Artifact: `{run.artifact_path}`"]
    if run.artifact_path:
        artifact_root = orch_root() / run.artifact_path
        validation_summary = artifact_root / "validation-summary.json"
        if validation_summary.exists():
            provenance_lines.append(f"Validation: `{validation_summary.relative_to(orch_root())}`")
    append_provenance(run.project, provenance_lines)
    write_operator_status()
    return run


def run_once(project: Optional[str] = None, *, worker: str = "handle", source: str = "run-once", note: Optional[str] = None) -> Optional[RunRecord]:
    if project and not project_dir(project).exists():
        raise ValueError(f"project {project} does not exist")
    if project:
        item = select_next_item(project)
        if item:
            return start_item(project, item.index, source=source, worker=worker, note=note)

        running = select_running_item(project)
        if running:
            _mark_stale_running_attempts(project, running.index)
            return start_item(
                project,
                running.index,
                source=source,
                worker=worker,
                note=note,
                allow_running=True,
            )
        return None

    selected = select_global_next()
    if not selected:
        selected_running = select_global_running_next()
        if selected_running:
            running_project, running_item = selected_running
            _mark_stale_running_attempts(running_project, running_item.index)
            return start_item(
                running_project,
                running_item.index,
                source=source,
                worker=worker,
                note=note,
                allow_running=True,
            )
        write_operator_status()
        return None
    slug, item = selected
    return start_item(slug, item.index, source=source, worker=worker, note=note)


def _write_validation_summary(
    run: RunRecord,
    execution: ExecutionResult,
    validation: ValidationResult,
    *,
    validation_trace: Optional[List[dict]] = None,
) -> Optional[str]:
    artifact_root = orch_root()
    if run.artifact_path:
        artifact_root = orch_root() / run.artifact_path
    if not artifact_root.exists() or not artifact_root.is_dir():
        return None

    summary_path = artifact_root / "validation-summary.json"
    summary = {
        "generated_at": now_utc_iso(),
        "run_id": run.run_id,
        "project": run.project,
        "index": run.index,
        "text": run.text,
        "execution": asdict(execution),
        "validation": asdict(validation),
    }
    if validation_trace:
        summary["validation_trace"] = validation_trace
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return str(summary_path.relative_to(orch_root()))


def run_tick(
    project: Optional[str] = None,
    *,
    worker: str = "handle",
    source: str = "tick",
    note: Optional[str] = None,
    max_retry_streak: Optional[int] = None,
    execution: Optional[ExecutionBridge] = None,
    validation: Optional[ValidationBridge] = None,
) -> Optional[TickResult]:
    if max_retry_streak is not None and max_retry_streak <= 0:
        raise ValueError("max_retry_streak must be greater than zero")

    run = run_once(project=project, worker=worker, source=source, note=note)
    if not run:
        write_operator_status()
        return None

    execute = execution or _default_execution_bridge
    validate = validation or _default_validation_bridge
    try:
        outcome = execute(run)
    except ExecutionBridgeError as exc:
        outcome = ExecutionResult(status="blocked", note=str(exc), artifact_path=run.artifact_path)
    except Exception as exc:
        outcome = ExecutionResult(status="blocked", note=f"execution bridge crashed: {exc}", artifact_path=run.artifact_path)
    if outcome.status.lower().strip() not in RUN_OUTCOMES:
        outcome = ExecutionResult(status="blocked", note=f"invalid execution status: {outcome.status}", artifact_path=outcome.artifact_path or run.artifact_path)
    try:
        result = validate(run, outcome)
    except Exception as exc:
        result = ValidationResult(status="blocked", passed=False, note=f"validation failed: {exc}")
    if result.status not in RUN_OUTCOMES:
        result = ValidationResult(status="blocked", passed=False, note=f"invalid validation status: {result.status}")
    if result.status == "done" and not result.passed:
        result = ValidationResult(
            status="blocked",
            passed=False,
            note=result.note or "validation reported unsuccessful result",
        )
    if result.status == "retry" and max_retry_streak is not None:
        retry_streak = _retry_streak_for_item(run)
        if retry_streak >= max_retry_streak:
            result = ValidationResult(
                status="blocked",
                passed=False,
                note=_merge_notes(
                    result.note,
                    f"retry streak reached {retry_streak} attempts for {run.project}:{run.index}",
                ),
            )

    if outcome.artifact_path:
        run.artifact_path = outcome.artifact_path
        run.updated_at = now_utc_iso()
        write_run_record(run)

    validation_trace = getattr(validate, "_last_trace", None)
    if not isinstance(validation_trace, list):
        validation_trace = None
    summary_path = _write_validation_summary(run, outcome, result, validation_trace=validation_trace)

    update_note = _merge_notes(run.note, outcome.note, result.note, f"validation_summary={summary_path}" if summary_path else None)
    if update_note and update_note != run.note:
        run.note = update_note
        run.updated_at = now_utc_iso()
        write_run_record(run)

    if result.status in {"done", "blocked"}:
        run = finalize_run(run.run_id, result.status, note=result.note)
    return TickResult(run=run, execution=outcome, validation=result)


def run_loop(
    project: Optional[str] = None,
    *,
    worker: str = "handle",
    source: str = "loop",
    note: Optional[str] = None,
    max_runs: int = 10,
    max_retry_streak: Optional[int] = None,
    max_attempts_per_item: Optional[int] = None,
    execution: Optional[ExecutionBridge] = None,
    validation: Optional[ValidationBridge] = None,
    continue_on_retry: bool = False,
    continue_on_blocked: bool = False,
) -> List[TickResult]:
    if max_runs <= 0:
        raise ValueError("max_runs must be greater than zero")
    if max_retry_streak is not None and max_retry_streak <= 0:
        raise ValueError("max_retry_streak must be greater than zero")
    if max_attempts_per_item is not None and max_attempts_per_item <= 0:
        raise ValueError("max_attempts_per_item must be greater than zero")
    out: List[TickResult] = []
    for _ in range(max_runs):
        tick = run_tick(
            project=project,
            worker=worker,
            source=source,
            note=note,
            max_retry_streak=max_retry_streak,
            execution=execution,
            validation=validation,
        )
        if not tick:
            break
        out.append(tick)
        if (
            max_attempts_per_item is not None
            and tick.validation.status == "retry"
            and tick.run.attempt >= max_attempts_per_item
            and tick.run.status == "running"
        ):
            note = _merge_notes(
                tick.validation.note,
                f"max attempts reached for {tick.run.project}:{tick.run.index} ({tick.run.attempt}/{max_attempts_per_item})",
            )
            tick = TickResult(
                run=finalize_run(tick.run.run_id, "blocked", note=note),
                execution=tick.execution,
                validation=ValidationResult(status="blocked", passed=False, note=note),
            )
            out[-1] = tick
            break
        if tick.validation.status not in {"done", "blocked", "retry"}:
            break
        if tick.validation.status == "retry" and not continue_on_retry:
            break
        if tick.validation.status == "blocked" and not continue_on_blocked:
            break
        if (
            max_attempts_per_item is not None
            and tick.validation.status in {"retry", "blocked"}
            and tick.run.attempt >= max_attempts_per_item
        ):
            break
    return out


def status_report(project: Optional[str] = None) -> dict:
    slugs = [project] if project else list_projects()
    projects = [project_status(slug) for slug in slugs]
    return {
        "generated_at": now_utc_iso(),
        "projects": [
            {
                **asdict(p),
                "next_item": asdict(p.next_item) if p.next_item else None,
            }
            for p in projects
        ],
    }


def status_report_markdown(project: Optional[str] = None) -> str:
    report = status_report(project)
    lines = [f"# Orchestration Report ({report['generated_at']})", ""]
    for p in report["projects"]:
        lines.append(f"## {p['slug']} (priority={p['priority']})")
        lines.append(f"- todo: {p['todo']}")
        lines.append(f"- doing: {p['doing']}")
        lines.append(f"- blocked: {p['blocked']}")
        lines.append(f"- done: {p['done']}")
        nxt = p.get("next_item")
        lines.append(f"- next: {nxt['text'] if nxt else '(none)'}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def status_report_json(project: Optional[str] = None) -> str:
    return json.dumps(status_report(project), indent=2) + "\n"
