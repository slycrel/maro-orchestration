"""Camera-frame logging — the log-forward half of the camera readout.

Round-3 panel disposition (docs/history/2026-07-30-taste-lens-panel.md):
log each instrumented fork FORWARD — what the chooser could see, what it
chose, with the ranker's raw scores — instead of reconstructing choices
retrospectively (the observation-repair ablation showed retrospective
reading cannot see missing axes; the frame must be captured at the fork).

One frame per logged fork, appended to the run's
`source/camera_frames.jsonl` (the chunk-4 run-keyed join pattern:
readouts join frames to run_card verdicts by run dir; see
camera_readout.py, the consumer this store ships with).

Score honesty: `score` is the ranker's raw ordinal score (BM25 / RRF /
citation-penalised cosine — see knowledge_web.ranker_name()), stored
unrounded. `score_share` is score/sum(positive scores) WITHIN one
candidate set, rounded to 4dp for the log (a tied triple stores 0.3333
each — shares are readout convenience, not an exactly-normalized
distribution), NOT a probability and NOT a sampling propensity
(selection is deterministic top-k today). Candidates from unscored
sources (flat-store top-ups) carry score=None and are never assigned
fake mass.

Never raises: any failure logs at debug and returns False — a broken
camera must not take down the run it is filming. Killswitch:
`camera.frame_log_enabled` (default ON, docs/DEFAULTS.md).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Best-effort writer: a stuck camera lock costs the frame, never 30s of
# the recall it is filming (adversarial-review 2026-07-31 F1). The default
# file_lock deadline protects data stores; frames are droppable telemetry.
_LOCK_TIMEOUT_S = 2.0


def _frame_log_enabled() -> bool:
    try:
        from config import get as _cfg_get
    except Exception:  # pragma: no cover
        return True
    val = _cfg_get("camera.frame_log_enabled", True)
    # Tolerate YAML-stringly config ("false"/"off"/"0") — the chunk-5a
    # quoted-killswitch lesson: a string "false" is truthy in Python.
    if isinstance(val, str):
        return val.strip().lower() not in ("false", "off", "no", "0", "")
    return bool(val)


def _with_shares(candidates: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Attach score_share per candidate set. Only positive numeric scores
    carry mass; None-scored (unscored-source) entries get share None."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for source, cands in candidates.items():
        total = sum(
            c.get("score") for c in cands
            if isinstance(c.get("score"), (int, float)) and c.get("score") > 0
        )
        rows = []
        for c in cands:
            row = dict(c)
            s = c.get("score")
            if isinstance(s, (int, float)) and s > 0 and total:
                row["score_share"] = round(s / total, 4)
            else:
                row["score_share"] = None
            rows.append(row)
        out[source] = rows
    return out


def log_fork_frame(
    fork: str,
    *,
    query: str = "",
    axes: Optional[Dict[str, Any]] = None,
    candidates: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    chosen: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> bool:
    """Append one camera frame for a decision fork to the current run.

    Args:
        fork:       Dotted fork name, e.g. "recall.lesson_selection".
        query:      The query/goal the fork ranked against (preview stored).
        axes:       What the chooser's viewpoint was — substrate sizes,
                    slice, project. Log what is KNOWN at the fork; readouts
                    join run_card/metadata for the rest (honest coverage
                    beats fabricated completeness).
        candidates: Per-source candidate dicts ({"lesson_id", "text",
                    "score"}). Scored sources carry raw ranker scores;
                    unscored sources carry score=None.
        chosen:     What was actually selected/rendered (durable IDs).
        extra:      Fork-specific extras (ranker name, windows, budgets).

    Returns True iff a frame was written. Never raises.
    """
    try:
        if not _frame_log_enabled():
            return False
        import runs
        rd = runs.current_run_dir()
        if rd is None:
            return False  # no run to key the frame to — drop, don't orphan
        if not Path(rd).is_dir():
            # Stale ContextVar pointing at a deleted/never-created run dir:
            # writing would resurrect the dir as an orphan. Drop the frame.
            return False
        frame = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "fork": fork,
            "query_preview": (query or "")[:200],
            "axes": axes or {},
            "candidates": _with_shares(candidates or {}),
            "chosen": chosen or {},
        }
        if extra:
            frame["extra"] = extra
        src = Path(rd) / "source"
        src.mkdir(parents=True, exist_ok=True)
        from file_lock import locked_append
        locked_append(src / "camera_frames.jsonl",
                      json.dumps(frame, ensure_ascii=False),
                      timeout_s=_LOCK_TIMEOUT_S)
        return True
    except Exception as exc:
        log.debug("camera frame drop (%s): %s", fork, exc)
        return False
