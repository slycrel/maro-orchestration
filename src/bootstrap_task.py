#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import List

from orch_items import append_decision, append_next_items, next_path, parse_next, project_dir
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


def _project_items(project: str):
    _lines, items = parse_next(project)
    return items


def _reference_items(project: str) -> List[str]:
    out: List[str] = []
    for item in _project_items(project):
        if item.text in PLACEHOLDERS:
            continue
        if item.text.startswith(("Gather inputs for:", "Produce first artifact for:", "Review results and capture next decisions for:")):
            continue
        out.append(item.text)
    return out


def _best_context_text(project: str, mission: str) -> str:
    refs = sorted(_reference_items(project), key=len, reverse=True)
    for ref in refs:
        if len(ref) >= max(40, len(mission)):
            return ref
    return mission


def _discover_urls(project: str, mission: str) -> List[str]:
    corpus = "\n".join([mission, *_reference_items(project)])
    return list(dict.fromkeys(re.findall(r"https?://\S+", corpus)))


def _discover_paths(project: str, mission: str) -> List[str]:
    corpus = "\n".join([mission, *_reference_items(project)])
    return list(dict.fromkeys(re.findall(r"\b(?:docs|output|projects|memory)/[^\s)]+", corpus)))


def _gather_inputs_path(project: str) -> Path:
    return project_dir(project) / "INPUTS.md"


def _first_artifact_path(project: str) -> Path:
    return project_dir(project) / "FIRST_ARTIFACT.md"


def _review_path(project: str) -> Path:
    return project_dir(project) / "REVIEW.md"


def _heuristic_plan_steps(project: str, mission: str) -> List[str]:
    compact = " ".join(_best_context_text(project, mission).split())
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
        candidates = _heuristic_plan_steps(project, mission)
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

    if item_text.startswith("Gather inputs for:"):
        mission_context = _best_context_text(project, mission)
        refs = _reference_items(project)
        urls = _discover_urls(project, mission)
        paths = _discover_paths(project, mission)
        path = _gather_inputs_path(project)
        body = [
            f"# Inputs — {project}",
            "",
            "## Mission context",
            "",
            f"- mission: {mission}",
            f"- active step: {item_text}",
            f"- inferred focus: {mission_context}",
            "",
            "## Reference checklist items",
            "",
            *([f"- {ref}" for ref in refs] or ["- (none discovered)"]),
            "",
            "## URLs",
            "",
            *([f"- {url}" for url in urls] or ["- (none discovered)"]),
            "",
            "## File references",
            "",
            *([f"- {p}" for p in paths] or ["- (none discovered)"]),
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        append_decision(project, [f"Gathered bootstrap inputs into {path.name}.", *(f"- url: {u}" for u in urls[:5]), *(f"- path: {p}" for p in paths[:5])])
        return _write_result("done", f"gathered bootstrap inputs at {path.name}")

    if item_text.startswith("Produce first artifact for:"):
        inputs_path = _gather_inputs_path(project)
        refs = _reference_items(project)
        path = _first_artifact_path(project)
        body = [
            f"# First Artifact — {project}",
            "",
            "## Source step",
            "",
            f"- {item_text}",
            "",
            "## Available references",
            "",
            *([f"- {ref}" for ref in refs[:8]] or ["- (none discovered)"]),
            "",
            "## Supporting files",
            "",
            f"- inputs: {inputs_path.name if inputs_path.exists() else '(not generated yet)'}",
            "",
            "## Bootstrap next move",
            "",
            "- Use the gathered references to produce the actual domain artifact on the next pass.",
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        append_decision(project, [f"Created bootstrap artifact stub at {path.name}."])
        return _write_result("done", f"created bootstrap artifact at {path.name}")

    if item_text.startswith("Review results and capture next decisions for:"):
        artifact_path = _first_artifact_path(project)
        inputs_path = _gather_inputs_path(project)
        path = _review_path(project)
        body = [
            f"# Review — {project}",
            "",
            "## Checked artifacts",
            "",
            f"- inputs: {'present' if inputs_path.exists() else 'missing'} ({inputs_path.name})",
            f"- first artifact: {'present' if artifact_path.exists() else 'missing'} ({artifact_path.name})",
            "",
            "## Next decisions",
            "",
            "- Continue from the gathered inputs and bootstrap artifact into the domain-specific deliverable.",
            "- Replace truncated or ambiguous mission text when a clearer source is known.",
            "",
        ]
        path.write_text("\n".join(body), encoding="utf-8")
        append_decision(project, [f"Reviewed bootstrap artifacts in {path.name}."])
        return _write_result("done", f"reviewed bootstrap artifacts at {path.name}")

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
