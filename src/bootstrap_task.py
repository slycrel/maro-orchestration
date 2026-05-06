#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import List

from orch_items import append_decision, append_next_items, get_item, next_path, parse_next, project_dir
from sprint_contract import _heuristic_criteria

PLACEHOLDER_DEFINE = "Define success criteria"
PLACEHOLDER_PLAN = "Create first-pass plan"
PLACEHOLDER_EXECUTE = "Execute next leaf task"
PLACEHOLDERS = {PLACEHOLDER_DEFINE, PLACEHOLDER_PLAN, PLACEHOLDER_EXECUTE}


def _mission_text(project: str) -> str:
    lines = next_path(project).read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">"):
            return stripped.lstrip(">").strip()
    return project.replace("-", " ").strip()


def _write_result(status: str, note: str, artifact_path: str | None = None) -> int:
    result_path = os.environ.get("ORCH_SESSION_RESULT_PATH")
    if result_path:
        payload = {"status": status, "note": note}
        if artifact_path:
            payload["artifact_path"] = artifact_path
        Path(result_path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0 if status == "done" else 1


def _criteria_path(project: str) -> Path:
    return project_dir(project) / "SUCCESS_CRITERIA.md"


def _plan_path(project: str) -> Path:
    return project_dir(project) / "PLAN.md"


def _existing_item_texts(project: str) -> List[str]:
    _lines, items = parse_next(project)
    return [item.text for item in items]


def _heuristic_plan_steps(mission: str) -> List[str]:
    compact = " ".join(mission.split())
    steps = [
        f"Gather inputs for: {compact[:120]}",
        f"Produce first artifact for: {compact[:120]}",
        f"Review results and capture next decisions for: {compact[:120]}",
    ]
    out: List[str] = []
    for step in steps:
        if step not in out:
            out.append(step)
    return out


def handle_placeholder(project: str, item_text: str) -> int:
    mission = _mission_text(project)

    if item_text == PLACEHOLDER_DEFINE:
        criteria, keywords = _heuristic_criteria(project.replace("-", " "), mission)
        path = _criteria_path(project)
        body = [
            f"# Success Criteria — {project}",
            "",
            "## Mission",
            "",
            f"> {mission}",
            "",
            "## Criteria",
            "",
            *[f"- {criterion}" for criterion in criteria],
            "",
            "## Acceptance Keywords",
            "",
            *[f"- {keyword}" for keyword in keywords],
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        append_decision(project, ["Defined heuristic success criteria.", *[f"- criterion: {c}" for c in criteria]])
        return _write_result("done", f"defined heuristic success criteria at {path.name}")

    if item_text == PLACEHOLDER_PLAN:
        existing = set(_existing_item_texts(project))
        candidates = _heuristic_plan_steps(mission)
        new_steps = [step for step in candidates if step not in existing and step not in PLACEHOLDERS]
        if new_steps:
            append_next_items(project, new_steps)
        path = _plan_path(project)
        plan_lines = [
            f"# Plan — {project}",
            "",
            "## Mission",
            "",
            f"> {mission}",
            "",
            "## First-pass steps",
            "",
            *[f"- {step}" for step in (new_steps or candidates)],
            "",
        ]
        path.write_text("\n".join(plan_lines), encoding="utf-8")
        append_decision(project, ["Created heuristic first-pass plan.", *[f"- step: {step}" for step in (new_steps or candidates)]])
        return _write_result("done", f"created heuristic plan at {path.name}")

    if item_text == PLACEHOLDER_EXECUTE:
        _lines, items = parse_next(project)
        leaf = next((item for item in items if item.text not in PLACEHOLDERS and item.state == " "), None)
        if leaf is None:
            return _write_result("blocked", "no concrete leaf task is queued after plan bootstrap")
        append_decision(project, [f"Bootstrap cleared placeholder; next leaf task is `{leaf.text}`."])
        return _write_result("done", f"queued next leaf task: {leaf.text}")

    return 10


def main() -> int:
    project = os.environ.get("ORCH_PROJECT", "").strip()
    item_text = os.environ.get("ORCH_ITEM_TEXT", "").strip()
    if not project or not item_text:
        return 10
    try:
        return handle_placeholder(project, item_text)
    except Exception as exc:
        return _write_result("blocked", f"bootstrap task failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
