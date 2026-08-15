"""Map lens — render any run as its self-surveying map, on demand.

Compound-thinking §9.1 (docs/COMPOUND_THINKING_DESIGN.md): the landmark
graph is a LENS over run artifacts, never a store — Jeremy's 2026-07-28
adjudication ("gut says that's lens, not schema") with one binding caveat:
"any given run must be visualizable as a map on demand." This module is
that caveat implemented.

It reads only what runs already write — metadata.json,
build/loop-*-log.json, build/closure_verdicts.jsonl, build/reanchor.jsonl,
build/backchain.json, run_card.json — and reconstructs the map: landmarks (steps) with tri-state fog (§2a), edges
(explicit [after:N] dependencies vs sequential default), recon moves with
the decision they informed (§4), loop lineage as vantage moves (§13a),
closure stalls (§9.3, identical consecutive fingerprints), and the stop
verdict with its evidence and type-derived reopen condition (§13b).

Deliberately NOT here (the §12 nudge-4 discipline — the schema must emerge
by subtraction, never build the store first): no persistence, no new run
artifact, no landmark IDs that outlive one build_map() call. The `json`
format IS the subtraction instrument: whatever fields this lens needed in
practice is the empirical answer to "what does a minimal map schema
contain."

Pure reader: build_map() and every renderer perform no filesystem writes.

Fog vocabulary (§2a):
  live  — directly observed this run (step done)
  grey  — last-known-state, possibly stale (step blocked/skipped: an
          observation with a reopen condition, not a fact)
  fog   — undiscovered (planned but never executed; v1 surfaces these only
          when the log itself carries a non-terminal status — mining
          checkpoint.json for the unexecuted plan tail is a named upgrade
          edge, its `steps` semantics are not clean enough to read yet)

CLI (dev tool, like maro-introspect):
    PYTHONPATH=src python3 -m map_lens <run-ref-or-path> [--format text|mermaid|json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from planner import after_deps, step_flavor, strip_after_tag, strip_recon_tag
from stop_verdicts import reopen_condition

# Fog states (§2a). Values are the serialized vocabulary — renderers and
# the json format both speak these strings.
STATE_LIVE = "live"
STATE_GREY = "grey"
STATE_FOG = "fog"

# Step status → fog state. Anything unrecognized maps to fog with a note
# (honest-missing beats silent misclassification).
_STATUS_TO_STATE = {
    "done": STATE_LIVE,
    "blocked": STATE_GREY,
    "skipped": STATE_GREY,
}

_GLYPH = {STATE_LIVE: "●", STATE_GREY: "◐", STATE_FOG: "○"}

_LABEL_CLIP = 100


@dataclass
class MapNode:
    id: str                 # unique within one build_map() result only
    kind: str               # goal | step | stop
    label: str
    state: str = STATE_LIVE  # fog vocabulary above (goal node: live)
    flavor: str = ""        # "recon" | "" (commit steps carry "")
    recon_decision: str = ""  # the VOI decision a recon step informs
    loop_id: str = ""
    index: Optional[int] = None  # 1-based step position within its loop
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MapEdge:
    src: str
    dst: str
    kind: str               # seq | after | lineage
    explicit: bool = False  # True for [after:N] tags; False for the
                            # sequential default parse_dependencies fills in


@dataclass
class RunMap:
    run_dir: str
    handle_id: str
    goal: str
    status: str
    nodes: List[MapNode] = field(default_factory=list)
    edges: List[MapEdge] = field(default_factory=list)
    stop: Dict[str, Any] = field(default_factory=dict)   # verdict/evidence/reopen
    pause: Dict[str, Any] = field(default_factory=dict)  # reason/family if present
    loops: List[Dict[str, Any]] = field(default_factory=list)
    closure: List[Dict[str, Any]] = field(default_factory=list)
    stall: bool = False     # two consecutive closure verdicts, same fingerprint
    anchors: List[Dict[str, Any]] = field(default_factory=list)  # §9.5
    # re-anchor checks (build/reanchor.jsonl): every milestone-boundary
    # coherence verdict the runtime recorded, on course or not.
    backchain: List[Dict[str, Any]] = field(default_factory=list)  # §9.9
    # goal-regression links (build/backchain.json): the backward frontier —
    # established links are convergence evidence, unknowns are named fog.
    notes: List[str] = field(default_factory=list)  # honest-missing markers


# ---------------------------------------------------------------------------
# Readers (each one file, each fail-soft with a note)
# ---------------------------------------------------------------------------

def _read_json(path: Path, notes: List[str], what: str) -> Optional[Any]:
    """Read one JSON artifact; None ALWAYS arrives with a note attached.

    Contract (round-2 review, Skeptic): a file containing literal `null`
    parses cleanly to None and used to slip past every corrupt-shape
    check downstream — noted here so callers can treat None uniformly as
    "absent, and the map already says why."
    """
    if not path.is_file():
        notes.append(f"missing: {what} ({path.name})")
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # narrow-except: any unreadable artifact is a
        # note on the map, never a crash — the lens must render partial runs.
        notes.append(f"unreadable: {what} ({path.name}: {exc})")
        return None
    if parsed is None:
        notes.append(f"corrupt: {what} is JSON null ({path.name})")
    return parsed


def _read_jsonl(path: Path, notes: List[str], what: str) -> List[dict]:
    if not path.is_file():
        return []
    rows: List[dict] = []
    bad = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                bad += 1
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                # Valid JSON, wrong shape — count it with the malformed
                # lines so the partial note stays honest (round-1 review).
                bad += 1
    except Exception as exc:
        notes.append(f"unreadable: {what} ({path.name}: {exc})")
        return rows
    if bad:
        notes.append(f"partial: {what} skipped {bad} malformed line(s)")
    return rows


def _clip(text: str, limit: int = _LABEL_CLIP) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _step_label(text: str) -> str:
    """Display label: recon + after tags stripped, whitespace collapsed."""
    return _clip(strip_after_tag(strip_recon_tag(text)))


# ---------------------------------------------------------------------------
# build_map
# ---------------------------------------------------------------------------

def build_map(run_dir: Path) -> RunMap:
    """Reconstruct the self-surveying map for one run directory.

    Reads metadata.json (goal, status, stop/pause fields, loops[] lineage),
    every build/loop-*-log.json (executed steps), and
    build/closure_verdicts.jsonl (closure attempts + stall detection).
    Missing or unreadable artifacts become `notes` entries — the lens
    renders whatever survives.
    """
    run_dir = Path(run_dir)
    notes: List[str] = []
    meta = _read_json(run_dir / "metadata.json", notes, "run metadata")
    if meta is not None and not isinstance(meta, dict):
        # Valid JSON that isn't an object (round-1 review, Skeptic): `or {}`
        # only catches falsy parses, so a stray list/string here crashed the
        # whole render instead of degrading — the exact anti-contract.
        notes.append("corrupt: run metadata is not a JSON object")
        meta = None
    if meta is None:
        meta = {}
    if not meta.get("handle_id"):
        notes.append("handle_id derived from directory name (unrecorded)")

    m = RunMap(
        run_dir=str(run_dir),
        handle_id=str(meta.get("handle_id") or run_dir.name.split("-")[0]),
        goal=str(meta.get("prompt") or meta.get("goal") or ""),
        status=str(meta.get("status") or ""),
        notes=notes,
    )

    m.nodes.append(MapNode(id="goal", kind="goal", label=_clip(m.goal, 160),
                           state=STATE_LIVE))

    # Stop verdict (§9.4/§13b): observation + evidence + the way back.
    verdict = str(meta.get("stop_verdict") or "")
    if verdict:
        m.stop = {
            "verdict": verdict,
            "evidence": str(meta.get("stop_evidence") or ""),
            "reopen": reopen_condition(verdict),
        }
        m.nodes.append(MapNode(
            id="stop", kind="stop", label=verdict, state=STATE_GREY,
            detail=dict(m.stop)))

    # Pause state (§13e) rides the card when typed; meta status names the
    # paused family. Only surface what is actually recorded.
    card = _read_json(run_dir / "run_card.json", notes, "run card")
    if card is not None and not isinstance(card, dict):
        notes.append("corrupt: run card is not a JSON object")
        card = None
    if isinstance(card, dict):
        if card.get("pause_reason"):
            m.pause = {
                "reason": str(card.get("pause_reason")),
                "family": str(card.get("pause_family") or ""),
            }

    # Loop lineage (§13a: vantage moves — same map, new survey station).
    loops_meta = meta.get("loops") if isinstance(meta.get("loops"), list) else []
    for entry in loops_meta:
        if isinstance(entry, dict):
            m.loops.append({
                "loop_id": str(entry.get("loop_id") or ""),
                "parent_loop_id": str(entry.get("parent_loop_id") or ""),
                "loop_reason": str(entry.get("loop_reason") or ""),
                "continuation_depth": entry.get("continuation_depth"),
            })

    # Executed steps, one log per loop.
    log_paths = sorted((run_dir / "build").glob("loop-*-log.json"))
    if not log_paths:
        notes.append("missing: loop logs (build/loop-*-log.json)")
    known_loop_order = [lp["loop_id"] for lp in m.loops if lp["loop_id"]]

    # Keys computed in one pass BEFORE sorting (round-2 review, Architect:
    # a sort key with a notes side effect was correct only because CPython
    # evaluates each key exactly once — fragile coupling, now gone).
    def _order_of(path: Path) -> Tuple[int, str]:
        stem = path.name[len("loop-"):-len("-log.json")]
        for pos, lid in enumerate(known_loop_order):
            if lid.startswith(stem) or stem.startswith(lid[:8]):
                return (pos, stem)
        # Unmatched stems sort last, alphabetically — a GUESS, and the map
        # must say so (round-1 review, both lenses): lineage edges built
        # from guessed order look causal while being lexical.
        if len(log_paths) > 1:
            notes.append(f"loop order guessed for log {stem} "
                         "(no matching loops[] entry in metadata)")
        return (len(known_loop_order), stem)

    ordered_logs = sorted(log_paths, key={p: _order_of(p)
                                          for p in log_paths}.get)

    prev_loop_last: Optional[str] = None
    for log_path in ordered_logs:
        loop_stem = log_path.name[len("loop-"):-len("-log.json")]
        data = _read_json(log_path, notes, f"loop log {loop_stem}")
        if data is None:
            continue
        if not isinstance(data, dict):
            notes.append(f"corrupt: loop log {loop_stem} is not a JSON object")
            continue
        raw_steps = data.get("steps")
        if raw_steps is None:
            steps: List[Any] = []
        elif isinstance(raw_steps, list):
            steps = raw_steps
        else:
            notes.append(f"corrupt: loop log {loop_stem} steps is not a list")
            steps = []
        # Index space MUST stay aligned with `steps` (round-1 review,
        # Skeptic HIGH): filtering non-dict rows here while the node loop
        # enumerates unfiltered desynced every [after:N] edge past the
        # first malformed row. Placeholder-"" keeps positions honest.
        texts = [str(s.get("text") or "") if isinstance(s, dict) else ""
                 for s in steps]
        # Grammar single-source: planner's public per-step reader.
        explicit = {
            i: deps
            for i, t in enumerate(texts, 1)
            if (deps := after_deps(t)) is not None
        }

        node_ids: Dict[int, str] = {}
        for i, s in enumerate(steps, 1):
            if not isinstance(s, dict):
                notes.append(f"corrupt: loop {loop_stem} step {i} row "
                             "is not an object — omitted from map")
                continue
            text = str(s.get("text") or "")
            status = str(s.get("status") or "")
            state = _STATUS_TO_STATE.get(status, STATE_FOG)
            if status and status not in _STATUS_TO_STATE:
                notes.append(f"unknown step status {status!r} "
                             f"(loop {loop_stem} step {i}) -> fog")
            flavor, voi = step_flavor(text)
            if flavor != "recon" and "[recon" in text:
                # Live data shows plan-time clipping can truncate a tag
                # ("[recon: dec…" with no closing bracket) — the grammar
                # honestly misses it, so say so instead of silently
                # rendering an authored recon step as commit.
                notes.append(f"unparsed [recon tag on step {i} "
                             f"(loop {loop_stem}) — truncated upstream?")
            nid = f"{loop_stem}:{i}"
            node_ids[i] = nid
            m.nodes.append(MapNode(
                id=nid, kind="step", label=_step_label(text), state=state,
                flavor=flavor if flavor == "recon" else "",
                recon_decision=_clip(voi, 80) if flavor == "recon" else "",
                loop_id=loop_stem, index=i,
                detail={"status": status},
            ))

        for i in sorted(node_ids):
            deps = explicit.get(i)
            if deps:
                for d in sorted(deps):
                    if d in node_ids:
                        m.edges.append(MapEdge(
                            src=node_ids[d], dst=node_ids[i],
                            kind="after", explicit=True))
                    else:
                        notes.append(f"dangling [after:{d}] on step {i} "
                                     f"(loop {loop_stem})")
            else:
                # Sequential default links to the nearest EARLIER surviving
                # node, not strictly i-1 (round-2 review, Architect): a
                # step right after a malformed row otherwise rendered as a
                # floating landmark — the silent-drop class again.
                prior = [j for j in node_ids if j < i]
                if prior:
                    m.edges.append(MapEdge(
                        src=node_ids[max(prior)], dst=node_ids[i],
                        kind="seq", explicit=False))

        if node_ids:
            first = node_ids[min(node_ids)]
            if prev_loop_last is None:
                m.edges.append(MapEdge(src="goal", dst=first, kind="seq",
                                       explicit=False))
            else:
                m.edges.append(MapEdge(src=prev_loop_last, dst=first,
                                       kind="lineage", explicit=False))
            prev_loop_last = node_ids[max(node_ids)]

    # Closure attempts (§9.3): the stall signal is two consecutive verdicts
    # with an identical non-empty fingerprint — the same evidence the
    # runtime declare-blocked brake keys on, made visible.
    closure_rows = _read_jsonl(run_dir / "build" / "closure_verdicts.jsonl",
                               notes, "closure verdicts")
    prev_fp: Optional[str] = None
    for row in closure_rows:
        fp = str(row.get("fingerprint") or "")
        m.closure.append({
            "verdict": str(row.get("verdict") or row.get("summary") or ""),
            "complete": row.get("complete"),
            "fingerprint": fp,
            "failed_checks": row.get("failed_checks") or [],
        })
        if fp and fp == prev_fp:
            m.stall = True
        prev_fp = fp or None

    # §9.9 backchain (2026-08-15): the backward frontier. A separate layer,
    # not step nodes — links are conjectured preconditions (fog until a
    # forward step or probe connects them), and the file is absent for most
    # runs (planner.backchain OFF-default), which is not a gap worth a note.
    bc = None
    if (run_dir / "build" / "backchain.json").is_file():
        bc = _read_json(run_dir / "build" / "backchain.json", notes, "backchain")
    if isinstance(bc, dict):
        for row in bc.get("links") or []:
            if isinstance(row, dict):
                m.backchain.append({
                    "condition": str(row.get("condition") or ""),
                    "class": str(row.get("class") or ""),
                    "step": row.get("step"),
                    "probe": str(row.get("probe") or ""),
                })
    elif bc is not None:
        notes.append("corrupt: backchain.json is not an object")

    # §9.5 re-anchor checks (2026-08-15): milestone-boundary coherence
    # verdicts. Kept as a separate layer rather than step nodes — an anchor
    # annotates the boundary BEFORE a step, it isn't a landmark itself.
    for row in _read_jsonl(run_dir / "build" / "reanchor.jsonl",
                           notes, "re-anchor checks"):
        m.anchors.append({
            "loop_id": str(row.get("loop_id") or ""),
            "step_idx": row.get("step_idx"),
            "on_course": bool(row.get("on_course", True)),
            "drift_summary": str(row.get("drift_summary") or ""),
            "anchor_source": str(row.get("anchor_source") or ""),
            "error": str(row.get("error") or ""),
        })

    return m


# ---------------------------------------------------------------------------
# Renderers (pure functions of RunMap)
# ---------------------------------------------------------------------------

def render_text(m: RunMap) -> str:
    """Compact terminal map: landmarks with fog glyphs, edges, verdicts."""
    out: List[str] = []
    out.append(f"MAP  {m.handle_id}  [{m.status or 'unknown'}]")
    out.append(f"goal: {_clip(m.goal, 160)}")
    if m.pause:
        fam = f" ({m.pause['family']})" if m.pause.get("family") else ""
        out.append(f"paused: {m.pause['reason']}{fam}")
    if m.stop:
        out.append(f"stop: {m.stop['verdict']} — {_clip(m.stop['evidence'])}")
        if m.stop.get("reopen"):
            out.append(f"      reopens when: {m.stop['reopen']}")

    steps = [n for n in m.nodes if n.kind == "step"]
    explicit_deps: Dict[str, List[str]] = {}
    for e in m.edges:
        if e.kind == "after":
            explicit_deps.setdefault(e.dst, []).append(e.src)

    # §9.5 anchors keyed by (loop stem, 1-based step index) — rendered on
    # the line BEFORE the step whose boundary they checked. Anything that
    # matches no rendered step falls to an "unplaced" section below so a
    # verdict is never silently dropped.
    anchors_by_key: Dict[Any, List[dict]] = {}
    for a in m.anchors:
        key = (str(a.get("loop_id") or "")[:8], a.get("step_idx"))
        anchors_by_key.setdefault(key, []).append(a)
    placed: set = set()

    current_loop = None
    loop_reasons = {lp["loop_id"][:8]: lp["loop_reason"] for lp in m.loops
                    if lp.get("loop_id")}
    for n in steps:
        if n.loop_id != current_loop:
            current_loop = n.loop_id
            reason = loop_reasons.get(current_loop, "")
            suffix = f"  ({reason})" if reason else ""
            out.append(f"-- loop {current_loop}{suffix}")
        for a in anchors_by_key.get((n.loop_id, n.index), []):
            out.append(f"  {_anchor_line(a)}")
            placed.add(id(a))
        glyph = _GLYPH.get(n.state, "?")
        mark = f"  ⌕ recon→ {n.recon_decision}" if n.flavor == "recon" else ""
        deps = explicit_deps.get(n.id)
        dep_str = ""
        if deps:
            nums = ",".join(d.rsplit(":", 1)[-1] for d in deps)
            dep_str = f"  [after {nums}]"
        out.append(f"  {glyph} {n.index}. {n.label}{dep_str}{mark}")

    unplaced = [a for a in m.anchors if id(a) not in placed]
    if unplaced:
        out.append("re-anchor checks (no matching step rendered):")
        out.extend(f"  {_anchor_line(a)}" for a in unplaced)

    if m.backchain:
        out.append("backchain (goal regression, nearest-to-goal first):")
        for l in m.backchain:
            cls = l.get("class")
            if cls == "established":
                step_ref = f" (step {l['step']})" if l.get("step") else ""
                out.append(f"  ✓ {_clip(l.get('condition') or '')}{step_ref}")
            elif cls == "verifiable":
                out.append(f"  ⌕ {_clip(l.get('condition') or '')}"
                           f"  → probe: {_clip(l.get('probe') or '', 60)}")
            else:
                out.append(f"  {_GLYPH[STATE_FOG]} {_clip(l.get('condition') or '')}"
                           "  (unknown)")

    if m.closure:
        out.append(f"closure attempts: {len(m.closure)}"
                   + ("  ⚠ STALL (repeated fingerprint)" if m.stall else ""))
    if m.notes:
        out.append("notes:")
        out.extend(f"  - {note}" for note in m.notes)
    legend = (f"  {_GLYPH[STATE_LIVE]} live  {_GLYPH[STATE_GREY]} grey "
              f"(blocked/skipped)  {_GLYPH[STATE_FOG]} fog")
    if m.anchors:
        legend += "  ⚓ re-anchor"
    out.append(legend)
    return "\n".join(out)


def _anchor_line(a: dict) -> str:
    """One text line for a §9.5 re-anchor verdict."""
    src = f" [{a['anchor_source']}]" if a.get("anchor_source") else ""
    if a.get("on_course", True):
        line = f"⚓ re-anchor{src}: on course"
    else:
        line = f"⚓ re-anchor{src}: DRIFT — {_clip(a.get('drift_summary') or '')}"
    if a.get("error"):
        line += f"  (check error: {_clip(a['error'], 80)})"
    return line


_MERMAID_UNSAFE = re.compile(r'["\[\]{}<>()#;`|]')


def _mermaid_label(text: str) -> str:
    return _MERMAID_UNSAFE.sub("", _clip(text, 60))


def render_mermaid(m: RunMap) -> str:
    """Mermaid flowchart (docs / Artifact embedding; the repo's viz pages
    are static and don't run mermaid.js — this format is for elsewhere)."""
    lines = ["flowchart TD"]
    for n in m.nodes:
        label = _mermaid_label(n.label or n.id)
        if n.kind == "goal":
            lines.append(f'    goal(["GOAL: {label}"])')
        elif n.kind == "stop":
            lines.append(f'    stop["STOP: {label}"]')
        else:
            nid = n.id.replace(":", "_")
            shape = f'{{{{"{label}"}}}}' if n.flavor == "recon" else f'["{label}"]'
            lines.append(f"    {nid}{shape}")
    for e in m.edges:
        src = e.src.replace(":", "_")
        dst = e.dst.replace(":", "_")
        arrow = "-->" if e.explicit or e.kind != "lineage" else "-.->"
        lines.append(f"    {src} {arrow} {dst}")
    if m.stop and any(n.kind == "step" for n in m.nodes):
        last_step = [n for n in m.nodes if n.kind == "step"][-1]
        lines.append(f'    {last_step.id.replace(":", "_")} -.-> stop')
    # §9.5 drift anchors only — an on-course check is noise in a topology
    # view (text/json carry every verdict).
    node_ids = {n.id for n in m.nodes}
    for i, a in enumerate(x for x in m.anchors if not x.get("on_course", True)):
        label = _mermaid_label(a.get("drift_summary") or "drift caught")
        lines.append(f'    anchor_{i}(["⚓ DRIFT: {label}"])')
        tgt = f"{str(a.get('loop_id') or '')[:8]}:{a.get('step_idx')}"
        if tgt in node_ids:
            lines.append(f'    anchor_{i} --> {tgt.replace(":", "_")}')
    lines.append("    classDef grey fill:#999,color:#fff")
    lines.append("    classDef fog fill:#eee,stroke-dasharray: 5 5")
    grey = [n.id.replace(":", "_") for n in m.nodes
            if n.kind == "step" and n.state == STATE_GREY]
    fog = [n.id.replace(":", "_") for n in m.nodes
           if n.kind == "step" and n.state == STATE_FOG]
    if grey:
        lines.append(f"    class {','.join(grey)} grey")
    if fog:
        lines.append(f"    class {','.join(fog)} fog")
    return "\n".join(lines)


def render_json(m: RunMap) -> str:
    """The subtraction instrument: exactly the fields the lens needed."""
    return json.dumps(asdict(m), indent=2, default=str)


_RENDERERS = {"text": render_text, "mermaid": render_mermaid,
              "json": render_json}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="map_lens",
        description="Render a run as its self-surveying map (read-only).")
    parser.add_argument("run", help="run ref (handle id / loop id) or a "
                        "run-directory path")
    parser.add_argument("--format", choices=sorted(_RENDERERS),
                        default="text")
    args = parser.parse_args(argv)

    candidate = Path(args.run)
    if candidate.is_dir():
        run_dir = candidate
    else:
        from runs import resolve_run_dir
        resolved = resolve_run_dir(args.run)
        if resolved is None:
            print(f"no run found for {args.run!r}", file=sys.stderr)
            return 2
        run_dir = resolved

    print(_RENDERERS[args.format](build_map(run_dir)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
