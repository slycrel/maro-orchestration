"""Harness-side execution receipts (MH #1 prevention half, 2026-08-12).

Record-mode call files (``<run-dir>/build/calls/call-*.json``, written by
runs.py at call time) capture every tool event the harness relayed for
the executor — command and output. The RECORDER writes them, not the
executor, so a step cannot forge or retro-edit them the way it can stage
workspace artifacts. That makes the transcript the one evidence source
the specification-gaming class (MH #1, model—grader edge) cannot reach:
an artifact asserting "tests passed" is executor-authored; a recorded
``pytest`` invocation with its output is not.

This module turns that record into closure-audit evidence. v1 posture
matches pass-audit v1: receipts GROUND the audit prompt — they never
flip a verdict and never block closure. Loading is best-effort; every
public function degrades to empty output rather than raising.

Three-valued honesty at the evidence surface: "record shows process
work", "record shows NO process work", and "no record available"
(record mode off, no run dir) are distinct — the renderer says which,
because absence of record is never evidence of absence of work.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("maro.execution_receipts")

# Bounds: a corpus run records dozens of calls with several events each;
# the audit prompt gets a digest, never the firehose.
MAX_RECEIPTS = 400
OUTPUT_HEAD_CHARS = 240
MAX_EVIDENCE_CHARS = 2_400
MAX_LISTED_RECEIPTS = 8

# Process-shaped work markers. Semantic twin of closure_verify's
# _TEST_RUNNER modality regex (kept local — closure_verify imports this
# module, so importing back would be circular); if that regex learns a
# new runner, teach this one too.
_PROCESS_MARKERS = re.compile(
    r"\b(pytest|go test|cargo (test|build)|(npm|pnpm|yarn) (run )?"
    r"(test|build)|make (test|build)|tox|python3? -m (pytest|unittest))\b"
)

_PATH_TOKEN = re.compile(r"[\w./-]*/[\w./-]+|\b[\w-]+\.(?:py|md|txt|json|jsonl|sh|yml|yaml|html)\b")


def load_receipts(run_dir, cap: int = MAX_RECEIPTS) -> List[Dict[str, Any]]:
    """Collect recorded tool executions from a run dir's call files.

    Returns [] on any shape of trouble — missing dir, unreadable files,
    malformed JSON (each file fails alone). Receipt rows carry the
    command, a bounded output head, and the source call file name.
    """
    out: List[Dict[str, Any]] = []
    try:
        calls = sorted(Path(run_dir).glob("build/calls/call-*.json"))
    except Exception:
        return out
    for path in calls:
        if len(out) >= cap:
            break
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        events = data.get("tool_events")
        if not isinstance(events, list):
            continue
        for ev in events:
            if len(out) >= cap:
                break
            if not isinstance(ev, dict):
                continue
            inp = ev.get("input")
            cmd = inp.get("command") if isinstance(inp, dict) else None
            if not isinstance(cmd, str) or not cmd.strip():
                continue
            output = ev.get("output")
            out.append({
                "command": cmd.strip(),
                "output_head": (output if isinstance(output, str)
                                else "")[:OUTPUT_HEAD_CHARS],
                "call": path.name,
            })
    return out


def _check_path_tokens(check_results: List[dict]) -> List[str]:
    """Basenames of files the static checks inspect — the artifacts whose
    provenance the receipts can illuminate."""
    seen: List[str] = []
    for r in check_results or []:
        if not isinstance(r, dict):
            continue
        blob = f"{r.get('command', '')} {r.get('description', '')}"
        for tok in _PATH_TOKEN.findall(str(blob)):
            base = str(tok).rsplit("/", 1)[-1]
            if base and base not in seen:
                seen.append(base)
    return seen[:12]


def render_receipt_evidence(receipts: List[Dict[str, Any]],
                            check_results: Optional[List[dict]] = None) -> str:
    """Bounded evidence digest for the pass-audit prompt.

    Empty receipts list → "" (the caller renders the no-record
    disclaimer; this function only speaks when there is a record).
    """
    if not receipts:
        return ""
    lines: List[str] = [
        f"{len(receipts)} command execution(s) recorded during the run."
    ]
    process = [r for r in receipts if _PROCESS_MARKERS.search(r["command"])]
    if process:
        lines.append(
            f"Process-shaped executions (test/build runners): {len(process)} —"
        )
        for r in process[:MAX_LISTED_RECEIPTS]:
            lines.append(f"  $ {r['command'][:160]}")
            if r["output_head"]:
                lines.append(f"    -> {r['output_head'][:160]}")
    else:
        lines.append(
            "Process-shaped executions (test/build runners): NONE recorded."
        )
    bases = _check_path_tokens(check_results or [])
    if bases:
        touched = []
        for base in bases:
            hits = [r for r in receipts if base in r["command"]]
            if hits:
                touched.append(f"  {base}: {len(hits)} recorded command(s), "
                               f"e.g. $ {hits[0]['command'][:120]}")
        if touched:
            lines.append("Checked-artifact provenance (commands mentioning "
                         "files the static checks inspect):")
            lines.extend(touched[:MAX_LISTED_RECEIPTS])
    text = "\n".join(lines)
    return text[:MAX_EVIDENCE_CHARS]


def audit_receipt_block(check_results: Optional[List[dict]] = None) -> str:
    """The full prompt block for the pass audit, three-valued and
    self-describing. Never raises."""
    try:
        run_dir = None
        try:
            from runs import current_run_dir
            run_dir = current_run_dir()
        except Exception:
            run_dir = None
        if run_dir is None:
            return ("Harness execution receipts: UNAVAILABLE (no run "
                    "record for this judgment) — treat as no signal, "
                    "not as evidence of absence.")
        receipts = load_receipts(run_dir)
        if not receipts:
            return ("Harness execution receipts: UNAVAILABLE (record mode "
                    "off or no tool events captured) — treat as no signal, "
                    "not as evidence of absence.")
        return ("Harness execution receipts (RECORDED BY THE HARNESS at "
                "call time; the run under judgment cannot edit these):\n"
                + render_receipt_evidence(receipts, check_results))
    except Exception as exc:  # the audit must never block closure
        log.debug("receipt block failed (non-blocking): %s", exc)
        return ("Harness execution receipts: UNAVAILABLE (receipt read "
                "failed) — treat as no signal, not as evidence of absence.")
