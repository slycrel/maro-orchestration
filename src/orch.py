#!/usr/bin/env python3
"""Poe orchestration core utilities."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

STATE_TODO = " "
STATE_DOING = "~"
STATE_DONE = "x"
STATE_BLOCKED = "!"
VALID_STATES = {STATE_TODO, STATE_DOING, STATE_DONE, STATE_BLOCKED}
RUN_OUTCOMES = {"done", "blocked", "retry"}

ITEM_RE = re.compile(r"^(?P<indent>\s*)-\s*\[(?P<state>[ xX~!])\]\s*(?P<text>.+?)\s*$")


@dataclass
class NextItem:
    index: int
    state: str
    text: str
    line: str
    indent: int = 0


@dataclass
class ProjectStatus:
    slug: str
    priority: int
    todo: int
    doing: int
    blocked: int
    done: int
    next_item: Optional[NextItem]


@dataclass
class RunRecord:
    run_id: str
    project: str
    index: int
    text: str
    status: str
    source: str
    worker: str
    started_at: str
    updated_at: str
    finished_at: Optional[str] = None
    note: Optional[str] = None
    artifact_path: Optional[str] = None


@dataclass
class ExecutionResult:
    status: str
    note: Optional[str] = None
    artifact_path: Optional[str] = None


@dataclass
class ValidationResult:
    status: str
    passed: bool
    note: Optional[str] = None


@dataclass
class TickResult:
    run: RunRecord
    execution: ExecutionResult
    validation: ValidationResult


@dataclass
class PlanResult:
    project: str
    goal: str
    steps: List[str]
    item_indices: List[int]


ExecutionBridge = Callable[[RunRecord], ExecutionResult]
ValidationBridge = Callable[[RunRecord, ExecutionResult], ValidationResult]


class ExecutionBridgeError(RuntimeError):
    """Raised when a concrete execution backend fails."""


def ws_root() -> Path:
    env_root = os.environ.get("OPENCLAW_WORKSPACE") or os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[3]


def orch_root() -> Path:
    return ws_root() / "prototypes" / "poe-orchestration"


def projects_root() -> Path:
    return orch_root() / "projects"


def output_root() -> Path:
    p = orch_root() / "output"
    p.mkdir(parents=True, exist_ok=True)
    return p


def runs_root() -> Path:
    p = output_root() / "runs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def operator_status_path() -> Path:
    return output_root() / "operator-status.json"


def project_dir(slug: str) -> Path:
    return projects_root() / slug


def next_path(slug: str) -> Path:
    return project_dir(slug) / "NEXT.md"


def decisions_path(slug: str) -> Path:
    return project_dir(slug) / "DECISIONS.md"


def risks_path(slug: str) -> Path:
    return project_dir(slug) / "RISKS.md"


def provenance_path(slug: str) -> Path:
    return project_dir(slug) / "PROVENANCE.md"


def priority_path(slug: str) -> Path:
    return project_dir(slug) / "PRIORITY"


def now_utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"


def list_projects() -> List[str]:
    root = projects_root()
    if not root.exists():
        return []
    slugs = []
    for p in root.iterdir():
        if p.is_dir() and next_path(p.name).exists():
            slugs.append(p.name)
    return sorted(slugs)


def project_priority(slug: str) -> int:
    p = priority_path(slug)
    if not p.exists():
        return 0
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def parse_next(slug: str) -> Tuple[List[str], List[NextItem]]:
    p = next_path(slug)
    lines = p.read_text(encoding="utf-8").splitlines()
    items: List[NextItem] = []
    for i, line in enumerate(lines):
        m = ITEM_RE.match(line)
        if not m:
            continue
        state = m.group("state")
        if state == "X":
            state = STATE_DONE
        items.append(
            NextItem(
                index=i,
                state=state,
                text=m.group("text").strip(),
                line=line,
                indent=len(m.group("indent")),
            )
        )
    return lines, items


def write_next_lines(slug: str, lines: List[str]) -> None:
    p = next_path(slug)
    p.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def mark_item(slug: str, item_index: int, new_state: str) -> None:
    if new_state not in VALID_STATES:
        raise ValueError(f"invalid new state: {new_state}")
    lines, items = parse_next(slug)
    item = next((it for it in items if it.index == item_index), None)
    if item is None:
        raise ValueError(f"item_index {item_index} not found in NEXT.md for {slug}")
    lines[item.index] = re.sub(r"\[(.)\]", f"[{new_state}]", lines[item.index], count=1)
    write_next_lines(slug, lines)


def append_next_items(slug: str, items: List[str]) -> List[int]:
    if not items:
        return []
    p = next_path(slug)
    lines = p.read_text(encoding="utf-8").splitlines()
    start = len(lines)
    next_lines = [f"- [ ] {i}" for i in items]
    lines.extend(next_lines)
    write_next_lines(slug, lines)
    return list(range(start, start + len(next_lines)))


def get_item(slug: str, item_index: int) -> NextItem:
    _lines, items = parse_next(slug)
    item = next((it for it in items if it.index == item_index), None)
    if item is None:
        raise ValueError(f"item_index {item_index} not found in NEXT.md for {slug}")
    return item


def decompose_goal(goal: str, *, max_steps: int = 4) -> List[str]:
    if max_steps <= 0:
        raise ValueError("max_steps must be greater than zero")
    normalized = re.sub(r"\s+", " ", goal.strip())
    if not normalized:
        raise ValueError("goal cannot be empty")

    # Conservative heuristic decomposition, useful for now and deterministic for
    # tests and local automation.
    pieces = [part.strip() for part in re.split(r"[.;]|\b(?:and then|then)\b", normalized, flags=re.IGNORECASE)]
    pieces = [p for p in pieces if p and p.lower() != "and"]
    if len(pieces) == 1 and "," in normalized:
        if "," in normalized:
            pieces = [p.strip() for p in normalized.split(",") if p.strip()]
    if len(pieces) == 1 and len(normalized.split()) > 12:
        words = normalized.split()
        chunk = max(1, len(words) // max_steps + 1)
        pieces = [" ".join(words[i : i + chunk]).strip() for i in range(0, len(words), chunk)]

    cleaned = [p for p in pieces if p]
    if not cleaned:
        raise ValueError(f"could not decompose goal: {goal}")
    return cleaned[:max_steps]


def plan_project(slug: str, goal: str, *, max_steps: int = 4) -> PlanResult:
    if not project_dir(slug).exists():
        raise ValueError(f"project {slug} does not exist")
    steps = decompose_goal(goal, max_steps=max_steps)
    item_indices = append_next_items(slug, steps)
    append_decision(slug, [f"Planned work from goal: {goal}", *[f"- step: {s}" for s in steps]])
    return PlanResult(project=slug, goal=goal, steps=steps, item_indices=item_indices)


def mark_first_todo_done(slug: str) -> Optional[NextItem]:
    item = select_next_item(slug)
    if not item:
        return None
    mark_item(slug, item.index, STATE_DONE)
    return item


def select_next_item(slug: str) -> Optional[NextItem]:
    _lines, items = parse_next(slug)
    for it in items:
        if it.state == STATE_TODO:
            return it
    return None


def item_counts(slug: str) -> dict:
    _lines, items = parse_next(slug)
    counts = {"todo": 0, "doing": 0, "blocked": 0, "done": 0}
    for item in items:
        if item.state == STATE_TODO:
            counts["todo"] += 1
        elif item.state == STATE_DOING:
            counts["doing"] += 1
        elif item.state == STATE_BLOCKED:
            counts["blocked"] += 1
        elif item.state == STATE_DONE:
            counts["done"] += 1
    return counts


def project_status(slug: str) -> ProjectStatus:
    counts = item_counts(slug)
    return ProjectStatus(
        slug=slug,
        priority=project_priority(slug),
        todo=counts["todo"],
        doing=counts["doing"],
        blocked=counts["blocked"],
        done=counts["done"],
        next_item=select_next_item(slug),
    )


def select_global_next() -> Optional[Tuple[str, NextItem]]:
    candidates: List[Tuple[int, float, str]] = []
    for slug in list_projects():
        p = next_path(slug)
        try:
            mtime = p.stat().st_mtime
        except FileNotFoundError:
            continue
        candidates.append((project_priority(slug), mtime, slug))

    for _priority, _mtime, slug in sorted(candidates, key=lambda row: (row[0], row[1]), reverse=True):
        it = select_next_item(slug)
        if it:
            return slug, it
    return None


def list_blocked_projects() -> List[ProjectStatus]:
    out: List[ProjectStatus] = []
    for slug in list_projects():
        status = project_status(slug)
        if status.blocked > 0:
            out.append(status)
    return sorted(out, key=lambda s: (s.priority, s.blocked, s.slug), reverse=True)


def append_section_lines(path: Path, heading: str, lines: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(heading + "\n\n", encoding="utf-8")
    stamp = now_utc_iso()
    payload = ["", f"## {stamp}", *[f"- {ln}" for ln in lines]]
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(payload) + "\n")


def append_decision(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(decisions_path(slug), "# DECISIONS", lines)


def append_risk(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(risks_path(slug), "# RISKS", lines)


def append_provenance(slug: str, lines: Iterable[str]) -> None:
    append_section_lines(provenance_path(slug), "# PROVENANCE", lines)


def ensure_project(slug: str, mission: str, priority: int = 0) -> Path:
    pdir = project_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)
    if not next_path(slug).exists():
        next_path(slug).write_text(
            (
                f"# NEXT — {slug}\n\n"
                "Mission:\n\n"
                f"> {mission}\n\n"
                "## Checklist\n\n"
                "- [ ] Define success criteria\n"
                "- [ ] Create first-pass plan\n"
                "- [ ] Execute next leaf task\n"
            ),
            encoding="utf-8",
        )
    if not risks_path(slug).exists():
        risks_path(slug).write_text("# RISKS\n\n## Risks / Unknowns\n\n- (fill in)\n", encoding="utf-8")
    if not decisions_path(slug).exists():
        decisions_path(slug).write_text("# DECISIONS\n\n", encoding="utf-8")
        append_decision(slug, ["Project created.", f"Mission: {mission}"])
    if not provenance_path(slug).exists():
        provenance_path(slug).write_text("# PROVENANCE\n\n- (links to key artifacts, datasets, runs)\n", encoding="utf-8")
    priority_path(slug).write_text(f"{priority}\n", encoding="utf-8")
    return pdir


def _run_record_path(run_id: str) -> Path:
    return runs_root() / f"{run_id}.json"


def write_run_record(record: RunRecord) -> Path:
    path = _run_record_path(record.run_id)
    path.write_text(json.dumps(asdict(record), indent=2) + "\n", encoding="utf-8")
    return path


def load_run_record(run_id: str) -> RunRecord:
    data = json.loads(_run_record_path(run_id).read_text(encoding="utf-8"))
    return RunRecord(**data)


def write_operator_status() -> dict:
    statuses = [project_status(slug) for slug in list_projects()]
    active = [s for s in statuses if s.doing > 0]
    blocked = [s for s in statuses if s.blocked > 0]
    next_sel = select_global_next()
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
    }
    operator_status_path().write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def start_item(slug: str, item_index: Optional[int] = None, *, source: str = "manual", worker: str = "handle", note: Optional[str] = None) -> RunRecord:
    if item_index is None:
        item = select_next_item(slug)
        if not item:
            raise ValueError(f"no TODO item found for {slug}")
    else:
        item = get_item(slug, item_index)
        if item.state != STATE_TODO:
            raise ValueError(f"item_index {item_index} for {slug} is not TODO")
    mark_item(slug, item.index, STATE_DOING)
    started_at = now_utc_iso()
    run = RunRecord(
        run_id=new_run_id(),
        project=slug,
        index=item.index,
        text=item.text,
        status="running",
        source=source,
        worker=worker,
        started_at=started_at,
        updated_at=started_at,
        note=note,
    )
    artifact = write_run_record(run)
    run.artifact_path = str(artifact.relative_to(orch_root()))
    artifact = write_run_record(run)
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
        append_decision(run.project, [f"Completed `{run.text}` ({run.run_id}).", *( [run.note] if run.note else [] )])
    else:
        append_risk(run.project, [f"Blocked `{run.text}` ({run.run_id}).", *( [run.note] if run.note else [] )])
    append_provenance(run.project, [f"Finalized `{run.text}` as {status} ({run.run_id}).", f"Artifact: `{run.artifact_path}`"])
    write_operator_status()
    return run


def run_once(project: Optional[str] = None, *, worker: str = "handle", source: str = "run-once", note: Optional[str] = None) -> Optional[RunRecord]:
    if project:
        item = select_next_item(project)
        if not item:
            return None
        return start_item(project, item.index, source=source, worker=worker, note=note)
    selected = select_global_next()
    if not selected:
        write_operator_status()
        return None
    slug, item = selected
    return start_item(slug, item.index, source=source, worker=worker, note=note)


def command_execution_bridge(command: str) -> ExecutionBridge:
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    def _execute(run: RunRecord) -> ExecutionResult:
        artifact_dir = runs_root() / run.run_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"

        env = os.environ.copy()
        env.update(
            {
                "ORCH_RUN_ID": run.run_id,
                "ORCH_PROJECT": run.project,
                "ORCH_ITEM_INDEX": str(run.index),
                "ORCH_ITEM_TEXT": run.text,
                "ORCH_WORKER": run.worker,
                "ORCH_SOURCE": run.source,
                "ORCH_ROOT": str(orch_root()),
                "ORCH_RUN_ARTIFACT_DIR": str(artifact_dir),
            }
        )

        proc = subprocess.run(
            command,
            shell=True,
            cwd=orch_root(),
            env=env,
            capture_output=True,
            text=True,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        artifact_path = str(artifact_dir.relative_to(orch_root()))
        if proc.returncode == 0:
            note = f"command succeeded: {command}"
            if proc.stdout.strip():
                note = f"{note}; stdout={proc.stdout.strip().splitlines()[-1]}"
            return ExecutionResult(status="done", note=note, artifact_path=artifact_path)

        note = f"command failed ({proc.returncode}): {command}"
        if proc.stderr.strip():
            note = f"{note}; stderr={proc.stderr.strip().splitlines()[-1]}"
        raise ExecutionBridgeError(note)

    return _execute


def _default_execution_bridge(run: RunRecord) -> ExecutionResult:
    return ExecutionResult(
        status="done",
        note=run.note or "No execution bridge configured; marking as complete for test/sync flow.",
    )


def _default_validation_bridge(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
    status = execution.status.lower().strip()
    if status not in RUN_OUTCOMES:
        raise ValueError(f"invalid execution status: {execution.status}")
    return ValidationResult(
        status=status,
        passed=status == "done",
        note=execution.note or ("execution accepted" if status == "done" else "execution blocked"),
    )


def _merge_notes(*notes: Optional[str]) -> Optional[str]:
    chunks = [n.strip() for n in notes if n and n.strip()]
    if not chunks:
        return None
    return "; ".join(chunks)


def run_tick(
    project: Optional[str] = None,
    *,
    worker: str = "handle",
    source: str = "tick",
    note: Optional[str] = None,
    execution: Optional[ExecutionBridge] = None,
    validation: Optional[ValidationBridge] = None,
) -> Optional[TickResult]:
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
    result = validate(run, outcome)

    update_note = _merge_notes(run.note, outcome.note)
    if update_note and update_note != run.note:
        run.note = update_note
        run.updated_at = now_utc_iso()
        write_run_record(run)

    if outcome.artifact_path:
        run.artifact_path = outcome.artifact_path
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
    execution: Optional[ExecutionBridge] = None,
    validation: Optional[ValidationBridge] = None,
) -> List[TickResult]:
    if max_runs <= 0:
        raise ValueError("max_runs must be greater than zero")
    out: List[TickResult] = []
    for _ in range(max_runs):
        tick = run_tick(
            project=project,
            worker=worker,
            source=source,
            note=note,
            execution=execution,
            validation=validation,
        )
        if not tick:
            break
        out.append(tick)
        if tick.validation.status == "retry":
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
