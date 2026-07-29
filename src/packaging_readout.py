"""Packaging readout — persona × goal-type skill-packaging evidence report.

First slice of the auto persona+skill packaging arc (next-leap, first-claim
pulled 2026-07-29; trio-triage verdict PARTIAL: readout-first). Report-only
by decree: the evolver's outcome data is not causal evidence yet, so this
makes the would-be packaging INSPECTABLE before any behavior changes.
Wiring the selection behind a flag is slice 2, gated on what this readout
shows. No LLM calls, no writes, no config flags — a CLI that spends
nothing needs no killswitch (discretion_readout precedent).

For every persona with dispatch evidence, and each goal-type it was
dispatched on, four row buckets:

- would_include        — skills the live selector (find_matching_skills)
                         picks for this cell's goals AND whose skill-stats
                         evidence clears the auto-promote bar (>=5 uses,
                         >=70% success).
- would_exclude        — skills the cell's goals reach whose evidence
                         argues against packaging them (open circuit, or
                         >=3 uses under 50% success). Open-circuit skills
                         are surfaced by a supplementary keyword scan —
                         the live selector filters them out, which is
                         exactly why a packaging report must not rely on
                         it to see them.
- insufficient_evidence— skills the selector picks that have too little
                         usage evidence to judge either way.
- (cell header)        — the goal-achieved verdict mix for the cell's own
                         outcomes, so "this persona succeeds here" claims
                         carry their denominator.

Evidence joins (each reported with its hit rate — a dead join is a
finding, not an omission):
- persona-dispatch-log.jsonl goal_preview  -> outcomes.jsonl goal[:120]
  (task_type + goal_achieved per dispatched goal)
- skills.jsonl (identity, triggers, circuit) -> skill-stats.jsonl by
  skill_id (the LIVE usage evidence — skills.jsonl's own use_count is
  unmaintained: 2/314 nonzero on this box 2026-07-29)
- persona-outcomes.jsonl loop_id -> outcomes.jsonl loop_id (measured 0/40
  live at time of writing; reported, not silently dropped)

Skill matching previews the REAL selector: skills.find_matching_skills
with its default router/keyword/tf-idf ladder, project="" (global view —
dispatch rows carry no project). What this report calls would_include is
exactly what a run on that goal would have injected, filtered by the
evidence bar.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Auto-promote bar (skills.maybe_auto_promote_skills: >=5 uses + >=70%
# success) — the include bucket reuses the system's own graduation
# threshold rather than inventing a second one.
INCLUDE_MIN_USES = 5
INCLUDE_MIN_SUCCESS = 0.70
# Exclude bar: enough uses to mean something, failing more than winning.
EXCLUDE_MIN_USES = 3
EXCLUDE_MAX_SUCCESS = 0.50
# Named display cap per bucket (never silent — the render prints how many
# rows it holds back).
DISPLAY_ROWS_PER_BUCKET = 10


def _memory_dir() -> Path:
    from orch_items import memory_dir
    return memory_dir()


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _load_stats_by_id(mem: Path) -> Dict[str, Dict[str, Any]]:
    """skill-stats.jsonl keyed by skill_id (last row wins, matching the
    store's upsert semantics)."""
    stats: Dict[str, Dict[str, Any]] = {}
    for row in _read_jsonl(mem / "skill-stats.jsonl"):
        sid = row.get("skill_id", "")
        if sid:
            stats[sid] = row
    return stats


def _spec_summary(name: str) -> Dict[str, Any]:
    """Frontmatter facts for a persona name, or has_spec=False."""
    try:
        from persona import PersonaRegistry
        spec = PersonaRegistry().load(name)
    except Exception:
        spec = None
    if spec is None:
        return {"has_spec": False}
    return {
        "has_spec": True,
        "model_tier": getattr(spec, "model_tier", "") or "",
        "tool_access": list(getattr(spec, "tool_access", []) or []),
    }


def _bucket_for(skill: Any, stats: Optional[Dict[str, Any]]) -> str:
    if getattr(skill, "circuit_state", "closed") == "open":
        return "would_exclude"
    if stats is None:
        return "insufficient_evidence"
    uses = int(stats.get("total_uses", 0) or 0)
    rate = float(stats.get("success_rate", 0.0) or 0.0)
    if uses >= EXCLUDE_MIN_USES and rate < EXCLUDE_MAX_SUCCESS:
        return "would_exclude"
    if uses >= INCLUDE_MIN_USES and rate >= INCLUDE_MIN_SUCCESS:
        return "would_include"
    return "insufficient_evidence"


def build_readout(mem: Optional[Path] = None) -> Dict[str, Any]:
    """Assemble the full readout as a structured dict (render separately)."""
    mem = mem or _memory_dir()

    dispatch = _read_jsonl(mem / "persona-dispatch-log.jsonl")
    outcomes = _read_jsonl(mem / "outcomes.jsonl")
    pers_outcomes = _read_jsonl(mem / "persona-outcomes.jsonl")
    stats_by_id = _load_stats_by_id(mem)

    out_by_prefix: Dict[str, List[Dict[str, Any]]] = {}
    out_loop_ids = set()
    for o in outcomes:
        out_by_prefix.setdefault(str(o.get("goal", ""))[:120], []).append(o)
        if o.get("loop_id"):
            out_loop_ids.add(o["loop_id"])

    # Join health: persona-outcomes -> outcomes via loop_id. Historically
    # dead (0/40) — computed live so recovery shows up here on its own.
    po_join_hits = sum(
        1 for p in pers_outcomes if p.get("loop_id") in out_loop_ids)

    # Unique goals per persona (a repeated dispatch of the same goal is one
    # evidence goal; its outcome rows still all count in the verdict mix).
    goals_by_persona: Dict[str, Dict[str, Dict[str, Any]]] = {}
    fallback_by_persona: Dict[str, int] = {}
    for d in dispatch:
        name = d.get("persona_name", "") or "(unnamed)"
        gp = str(d.get("goal_preview", ""))
        rec = goals_by_persona.setdefault(name, {}).setdefault(
            gp, {"dispatches": 0})
        rec["dispatches"] += 1
        if d.get("is_fallback"):
            fallback_by_persona[name] = fallback_by_persona.get(name, 0) + 1

    joined_goals = 0
    unjoined_goals = 0
    verdict_totals = {"true": 0, "false": 0, "unverdicted": 0}

    try:
        from skills import find_matching_skills
    except Exception:
        find_matching_skills = None  # coverage will say so

    # Open-circuit skills for the supplementary scan: find_matching_skills
    # filters them, but "matches this cell AND its circuit is open" is a
    # would_exclude finding, not something to hide. Keyword rule mirrors
    # the selector's own overlap test.
    open_circuit: List[Any] = []
    try:
        from skills import load_skills
        open_circuit = [s for s in load_skills()
                        if getattr(s, "circuit_state", "closed") == "open"]
    except Exception:
        pass

    match_cache: Dict[str, List[Any]] = {}

    def _matches(goal: str) -> List[Any]:
        if goal in match_cache:
            return match_cache[goal]
        found: List[Any] = []
        if find_matching_skills is not None:
            try:
                found = list(find_matching_skills(goal))
            except Exception:
                found = []
        goal_lower = goal.lower()
        for s in open_circuit:
            if any(p.lower() in goal_lower or goal_lower in p.lower()
                   for p in getattr(s, "trigger_patterns", []) or []):
                found.append(s)
        match_cache[goal] = found
        return found

    personas: Dict[str, Any] = {}
    for name, goals in sorted(
            goals_by_persona.items(),
            key=lambda kv: -sum(g["dispatches"] for g in kv[1].values())):
        cells: Dict[str, Dict[str, Any]] = {}
        for goal, rec in goals.items():
            joined = out_by_prefix.get(goal, [])
            if joined:
                joined_goals += 1
            else:
                unjoined_goals += 1
            cell_types = sorted({o.get("task_type", "") or "(untyped)"
                                 for o in joined}) or ["(unjoined)"]
            for ttype in cell_types:
                cell = cells.setdefault(ttype, {
                    "goals": 0, "achieved_true": 0, "achieved_false": 0,
                    "unverdicted": 0, "_skill_hits": {}})
                cell["goals"] += 1
                for o in joined:
                    if (o.get("task_type", "") or "(untyped)") != ttype:
                        continue
                    ga = o.get("goal_achieved")
                    if ga is True:
                        cell["achieved_true"] += 1
                        verdict_totals["true"] += 1
                    elif ga is False:
                        cell["achieved_false"] += 1
                        verdict_totals["false"] += 1
                    else:
                        cell["unverdicted"] += 1
                        verdict_totals["unverdicted"] += 1
                for sk in _matches(goal):
                    hit = cell["_skill_hits"].setdefault(sk.id, {
                        "skill": sk, "matched_goals": 0})
                    hit["matched_goals"] += 1

        # Bucket each cell's matched skills by their stats evidence.
        for ttype, cell in cells.items():
            buckets: Dict[str, List[Dict[str, Any]]] = {
                "would_include": [], "would_exclude": [],
                "insufficient_evidence": []}
            for hit in cell.pop("_skill_hits").values():
                sk = hit["skill"]
                st = stats_by_id.get(sk.id)
                row = {
                    "skill_id": sk.id,
                    "name": sk.name,
                    "matched_goals": hit["matched_goals"],
                    "total_uses": int((st or {}).get("total_uses", 0) or 0),
                    "success_rate": round(float(
                        (st or {}).get("success_rate", 0.0) or 0.0), 3),
                    "has_stats": st is not None,
                    "circuit_state": getattr(sk, "circuit_state", "closed"),
                }
                buckets[_bucket_for(sk, st)].append(row)
            for b in buckets.values():
                b.sort(key=lambda r: (-r["matched_goals"], -r["total_uses"]))
            cell.update(buckets)

        personas[name] = {
            "spec": _spec_summary(name),
            "dispatches": sum(g["dispatches"] for g in goals.values()),
            "unique_goals": len(goals),
            "fallback_dispatches": fallback_by_persona.get(name, 0),
            "cells": cells,
        }

    # Personas on disk that never appear in the dispatch log — absence of
    # evidence is a first-class row, not an omission.
    try:
        from persona import PersonaRegistry
        all_specs = PersonaRegistry().list()
    except Exception:
        all_specs = []
    undispatched = sorted(set(all_specs) - set(personas))

    return {
        "coverage": {
            "dispatch_rows": len(dispatch),
            "unique_goals": sum(p["unique_goals"] for p in personas.values()),
            "joined_goals": joined_goals,
            "unjoined_goals": unjoined_goals,
            "outcome_rows": len(outcomes),
            "verdict_totals": verdict_totals,
            "skills_selector_available": find_matching_skills is not None,
            "skill_stats_rows": len(stats_by_id),
            "persona_outcomes_rows": len(pers_outcomes),
            "persona_outcomes_loop_join_hits": po_join_hits,
            "personas_without_dispatch_evidence": undispatched,
        },
        "personas": personas,
    }


def render_markdown(readout: Dict[str, Any]) -> str:
    cov = readout["coverage"]
    lines: List[str] = []
    lines.append("# Packaging readout — persona × goal-type skill evidence")
    lines.append("")
    lines.append(
        "REPORT-ONLY: nothing here changes selection behavior. Wiring the "
        "packaging behind a flag is slice 2, gated on this evidence.")
    lines.append("")
    lines.append("## Coverage (input honesty)")
    vt = cov["verdict_totals"]
    lines.append(
        f"- dispatch rows: {cov['dispatch_rows']} "
        f"({cov['unique_goals']} unique goals; "
        f"{cov['joined_goals']} joined to outcomes, "
        f"{cov['unjoined_goals']} unjoined)")
    lines.append(
        f"- joined outcome verdicts: {vt['true']} achieved / "
        f"{vt['false']} not-achieved / {vt['unverdicted']} unverdicted "
        "— unverdicted rows carry no packaging signal")
    lines.append(
        f"- skill evidence: {cov['skill_stats_rows']} skill-stats rows "
        "(skills.jsonl use_count is NOT the evidence store)")
    lines.append(
        f"- persona-outcomes loop_id join: "
        f"{cov['persona_outcomes_loop_join_hits']}/"
        f"{cov['persona_outcomes_rows']} — "
        + ("dead join, persona-outcomes contributes nothing yet"
           if cov["persona_outcomes_loop_join_hits"] == 0
           else "partially live"))
    if not cov["skills_selector_available"]:
        lines.append("- WARNING: skills selector unavailable — every "
                     "bucket below is empty for that reason, not for "
                     "lack of matches")
    if cov["personas_without_dispatch_evidence"]:
        lines.append(
            f"- personas with NO dispatch evidence "
            f"({len(cov['personas_without_dispatch_evidence'])}): "
            + ", ".join(cov["personas_without_dispatch_evidence"]))
    lines.append("")

    for name, p in readout["personas"].items():
        spec = p["spec"]
        spec_txt = (
            f"tier={spec.get('model_tier') or '—'}, "
            f"tool_access={spec.get('tool_access') or []}"
            if spec.get("has_spec") else "NO SPEC FILE")
        lines.append(f"## {name}")
        lines.append(
            f"{p['dispatches']} dispatches / {p['unique_goals']} unique "
            f"goals ({p['fallback_dispatches']} fallback); {spec_txt}")
        for ttype, cell in sorted(p["cells"].items(),
                                  key=lambda kv: -kv[1]["goals"]):
            lines.append("")
            lines.append(
                f"### {name} × {ttype} — {cell['goals']} goals "
                f"(verdicts: {cell['achieved_true']} achieved / "
                f"{cell['achieved_false']} not / "
                f"{cell['unverdicted']} unverdicted)")
            for bucket in ("would_include", "would_exclude",
                           "insufficient_evidence"):
                rows = cell[bucket]
                if not rows:
                    lines.append(f"- {bucket}: (none)")
                    continue
                lines.append(f"- {bucket}:")
                for r in rows[:DISPLAY_ROWS_PER_BUCKET]:
                    extra = ("no stats row" if not r["has_stats"] else
                             f"{r['total_uses']} uses @ "
                             f"{r['success_rate']:.0%}")
                    circ = ("" if r["circuit_state"] == "closed"
                            else f", circuit {r['circuit_state']}")
                    lines.append(
                        f"    - {r['name']} — matched {r['matched_goals']} "
                        f"goal(s); {extra}{circ}")
                if len(rows) > DISPLAY_ROWS_PER_BUCKET:
                    lines.append(
                        f"    - … +{len(rows) - DISPLAY_ROWS_PER_BUCKET} "
                        f"more (shown {DISPLAY_ROWS_PER_BUCKET} of "
                        f"{len(rows)})")
        lines.append("")

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Persona × goal-type skill-packaging evidence report "
                    "(report-only; reads the live workspace stores)")
    parser.add_argument("--persona", default=None,
                        help="limit the report to one persona name")
    parser.add_argument("--json", action="store_true",
                        help="emit the structured readout as JSON")
    args = parser.parse_args(argv)

    readout = build_readout()
    if args.persona:
        readout["personas"] = {
            k: v for k, v in readout["personas"].items()
            if k == args.persona}
    if args.json:
        # Skill objects were already flattened to plain rows in build.
        print(json.dumps(readout, indent=2, default=str))
    else:
        print(render_markdown(readout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
