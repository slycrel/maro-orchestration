"""Per-run HTML visibility report + cross-run static index.

Design: docs/RUN_VISIBILITY_DESIGN.md. Replaces the archived observe.py
HTTP dashboard's read-only half with two static files, no server, no
control surface:

  - write_run_report(): a per-loop Gantt-style timeline + step table,
    rewritten in place after every step (same call-site pattern as
    _write_plan_manifest in loop_artifacts.py), frozen once the loop
    reaches a terminal status.
  - write_runs_index(): a cross-run index of goals/sessions, regenerated
    whenever any run updates.

Both writers never raise — a report bug must never break a run, matching
every other writer in loop_artifacts.py.
"""

from __future__ import annotations

import html
import json
import logging
import os
import shutil
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loop_types import StepOutcome, _orch, _project_dir_root

log = logging.getLogger("maro.loop")

_FREEZE_SENTINEL_PREFIX = "<!-- maro-report: final status="

_STATUS_ICON = {"done": "✅", "blocked": "❌", "skipped": "⏭", "pending": "⬜"}
_STATUS_CLASS = {"done": "st-done", "blocked": "st-blocked", "skipped": "st-skipped", "pending": "st-pending"}

# run_curation.classify_outcome's success_class vocabulary — "done" alone is
# ambiguous (steps finished vs. goal actually achieved vs. verified at all),
# so the index prefers this richer classification when run_card.json has it.
# (label, badge css class, tooltip/legend text)
_SUCCESS_CLASS_INFO = {
    "success": ("success", "badge-done", "Steps completed and the goal was verified achieved."),
    "done-not-achieved": ("done, not achieved", "badge-retry", "Steps completed, but verification says the goal wasn't met."),
    "done-unverified": ("done, unverified", "badge-pending", "Steps completed; no achievement verification ran."),
    "partial": ("partial", "badge-retry", "Stopped early — incomplete."),
    "failed": ("failed", "badge-blocked", "Stuck or errored."),
    "unknown": ("unknown", "badge-pending", "Outcome not classified."),
}
_LANE_HELP = "NOW = trivial, answered in a single LLM call. AGENDA = multi-step, planned and executed as a loop."


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _reports_enabled() -> bool:
    try:
        from config import get as _get
        return bool(_get("report.enabled", True))
    except Exception:
        return True


def _debug_snapshots_enabled() -> bool:
    env = os.environ.get("MARO_REPORT_DEBUG")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from config import get as _get
        return bool(_get("report.debug_snapshots", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Atomic write (2026-07-08 adversarial review, findings #4 / Reality Checker
# claim 1/3): a bare path.write_text() lets a concurrent reader observe a
# partially-written file, and lets a crash mid-write leave a truncated one
# that a later frozen-check would misread. Write to a sibling temp file and
# os.replace() it into place — POSIX guarantees the rename is atomic, so
# readers only ever see the old complete file or the new complete file.
# ---------------------------------------------------------------------------

def _atomic_write_text(path: Path, content: str) -> None:
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Path resolution — mirrors loop_artifacts._plan_manifest_path exactly, so
# the report always lands next to the plan manifest / loop log / call records.
# ---------------------------------------------------------------------------

def _report_dir(project: str) -> Optional[Path]:
    if not project:
        return None
    try:
        o = _orch()
        try:
            from runs import artifact_dir as _runs_artifact_dir
            return _runs_artifact_dir(project, project_root_fn=_project_dir_root)
        except Exception:
            d = o.projects_root() / project / "artifacts"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        return None


def _report_path(project: str, loop_id: str) -> Optional[Path]:
    d = _report_dir(project)
    if d is None:
        return None
    return d / f"loop-{loop_id}-report.html"


def _debug_snapshot_dir(project: str, loop_id: str) -> Optional[Path]:
    d = _report_dir(project)
    if d is None:
        return None
    return d / f"loop-{loop_id}-report-debug"


# ---------------------------------------------------------------------------
# Freeze sentinel — once a terminal write embeds this, later calls for the
# same loop are no-ops (idempotent freeze; cheap insurance against ordering
# bugs, salvage/curation re-entry, or a resumed process re-touching a
# finished loop).
# ---------------------------------------------------------------------------

def _is_frozen(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as f:
            head = f.read(256)
    except Exception:
        return False
    return head.startswith(_FREEZE_SENTINEL_PREFIX)


# ---------------------------------------------------------------------------
# Debug snapshot mode (config `report.debug_snapshots`, default off; env
# override MARO_REPORT_DEBUG=1). On: keep a timestamped copy of every
# regeneration for debugging the generator itself. Off: the dir is never
# created, and any leftover dir from a prior debug session is deleted the
# next time this loop's report is written — so flipping the flag off
# self-cleans, no separate GC job needed.
# ---------------------------------------------------------------------------

_debug_seq_counters: Dict[str, int] = {}
_debug_seq_lock = threading.Lock()


def _next_debug_seq(key: str) -> int:
    with _debug_seq_lock:
        n = _debug_seq_counters.get(key, 0) + 1
        _debug_seq_counters[key] = n
        return n


def _clear_debug_snapshots(project: str, loop_id: str) -> None:
    d = _debug_snapshot_dir(project, loop_id)
    if d is None or not d.exists():
        return
    try:
        shutil.rmtree(d)
    except Exception:
        pass


def _maybe_snapshot(html_text: str, project: str, loop_id: str) -> None:
    if not _debug_snapshots_enabled():
        _clear_debug_snapshots(project, loop_id)
        return
    d = _debug_snapshot_dir(project, loop_id)
    if d is None:
        return
    try:
        d.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        seq = _next_debug_seq(f"{project}:{loop_id}")
        (d / f"report-{stamp}-{seq:03d}.html").write_text(html_text, encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except Exception:
        return None
    # 2026-07-08 adversarial review (finding #10): every live ended_ts today
    # is tz-aware (datetime.now(timezone.utc).isoformat()), but nothing
    # structurally prevents a future/legacy caller from passing a naive
    # timestamp — comparing naive vs. aware datetimes raises TypeError, which
    # would otherwise bubble up and fail the whole report write. Normalizing
    # here (assume UTC for a naive value) makes that class of input safe
    # rather than merely unreachable-by-convention.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _step_windows(step_outcomes: List[StepOutcome], loop_start_ts: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Compute a (start, end) datetime window per executed step outcome.

    Prefers StepOutcome.ended_ts (present on every outcome constructed after
    this field was added). If ANY outcome in this run predates it (e.g. a
    checkpoint resumed from before the field existed), the whole timeline
    falls back to a cumulative-sum approximation from loop_start_ts rather
    than mixing real and approximate windows, which could jump backward.
    Returns (windows, approximate).
    """
    any_missing = any(not getattr(s, "ended_ts", "") for s in step_outcomes)
    windows: List[Dict[str, Any]] = []
    if any_missing:
        cumulative = _parse_iso(loop_start_ts) or datetime.now(timezone.utc)
        for s in step_outcomes:
            start = cumulative
            cumulative = cumulative + timedelta(milliseconds=max(s.elapsed_ms, 0))
            windows.append({"start": start, "end": cumulative})
        return windows, True

    for s in step_outcomes:
        end = _parse_iso(s.ended_ts) or datetime.now(timezone.utc)
        start = end - timedelta(milliseconds=max(s.elapsed_ms, 0))
        windows.append({"start": start, "end": end})
    return windows, False


# ---------------------------------------------------------------------------
# Captain's log markers
# ---------------------------------------------------------------------------

def _read_log_slice(report_dir: Optional[Path]) -> Optional[List[dict]]:
    """Read the run's own captain's-log slice, if one exists.

    runs.slice_log_for_run() writes <run-dir>/build/captains_log_slice.jsonl
    at finalize — a byte-offset copy of everything logged during the run's
    lifetime, regardless of loop_id. It's the durable per-run record: it
    survives rotation of the global log, and it's already scoped to this
    run's time window. When present (the report lives in the same build/
    dir), it beats re-filtering the global log's ~1000-entry tail.
    Returns None (not []) when the slice doesn't exist, so the caller can
    distinguish "no slice yet" from "slice exists but is empty".
    """
    if report_dir is None:
        return None
    slice_path = report_dir / "captains_log_slice.jsonl"
    if not slice_path.exists():
        return None
    entries: List[dict] = []
    try:
        with slice_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return None
    return entries


def _gather_log_markers(
    loop_id: str, start_ts: str, report_dir: Optional[Path] = None
) -> Tuple[List[dict], List[dict]]:
    """Return (attributed, run_activity) captain's-log entries for this run.

    attributed: entries whose loop_id matches this loop, chronological.
    run_activity: everything else logged during this run's window — skills
    learned/refined, evolver actions, knowledge extraction, diagnoses.
    Only ~11 of ~48 log_event() call sites pass a loop_id (85% of real
    entries on this box have none), so the unattributed set is where most
    of the run's meta actually lives — it gets a visible grouped section,
    not silent dropping.

    Source preference: the run's own captains_log_slice.jsonl when it
    exists (rotation-proof, already run-scoped — see _read_log_slice),
    else the global active log filtered to this run's window (in-flight
    runs; the slice is only written at finalize). The global path keeps
    the v1 limitation: load_log() reads the ~1000-entry rotation tail, so
    a very long/busy run could lose its earliest markers.
    """
    if not loop_id:
        return [], []

    slice_entries = _read_log_slice(report_dir)
    if slice_entries is not None:
        attributed = [e for e in slice_entries if e.get("loop_id") == loop_id]
        activity = [e for e in slice_entries if e.get("loop_id") != loop_id]
        return attributed, activity

    try:
        from captains_log import load_log
    except Exception:
        return [], []
    since_date = (start_ts or "")[:10] or None
    try:
        # 2026-07-08 adversarial review (finding #8): this cap was stricter
        # than the accepted-risk note above actually promises (500 vs. the
        # ~1000-entry rotation tail) — 1000 matches what was documented.
        entries = load_log(since=since_date, limit=1000)
    except Exception:
        return [], []
    attributed = [e for e in entries if e.get("loop_id") == loop_id]
    # 2026-07-08 adversarial review round 2 (Plan Critic): raw string
    # comparison on ISO timestamps only orders correctly when every producer
    # uses the exact same format; _parse_iso() (already normalizing naive
    # datetimes to UTC, finding #10) is the same parser used for real
    # datetime comparisons elsewhere in this module — reuse it here too
    # instead of a second, weaker comparison method for the same kind of data.
    _run_start_dt = _parse_iso(start_ts) or datetime.min.replace(tzinfo=timezone.utc)
    global_entries = [
        e for e in entries
        if not e.get("loop_id")
        and (_parse_iso(e.get("timestamp", "")) or datetime.min.replace(tzinfo=timezone.utc)) >= _run_start_dt
    ]
    attributed.reverse()       # load_log is most-recent-first; report reads chronologically
    global_entries.reverse()
    return attributed, global_entries


def _slot_markers(markers: List[dict], windows: List[Dict[str, Any]], approx: bool) -> Dict[int, List[int]]:
    """Map 1-based step position -> marker indices whose timestamp falls in
    that step's window. Skipped entirely in approximate-timing mode (no real
    windows to slot against) — markers still appear in the Decision Points
    list, just without a timeline footnote badge."""
    slots: Dict[int, List[int]] = {}
    if approx:
        return slots
    for mi, m in enumerate(markers):
        ts = _parse_iso(m.get("timestamp", ""))
        if ts is None:
            continue
        for pos, w in enumerate(windows, start=1):
            if w["start"] <= ts <= w["end"]:
                slots.setdefault(pos, []).append(mi)
                break
    return slots


# ---------------------------------------------------------------------------
# Call-record metadata — model/backend/purpose per LLM call
#
# Every call record (runs.record_llm_call) already carries backend, model,
# tokens, and the full prompt — the report just never read them (2026-07-09,
# Jeremy: the report "doesn't surface any of the meta about the system
# itself"). Records are write-once, so a process-local cache keeps the
# per-step regeneration from reparsing the same files after every step.
# ---------------------------------------------------------------------------

# Distinctive openers of the system prompts each subsystem sends — matched
# against the first few hundred chars of a recorded prompt to label what a
# call was *for* (map built from the actual distribution across this box's
# 761 historical call records, 2026-07-09). Unmatched calls fall back to "".
_PURPOSE_PATTERNS: List[Tuple[str, str]] = [
    ("autonomous execution agent", "step execution"),
    ("autonomous planning agent", "decompose"),
    ("bound the solution space", "scope"),
    ("routing agent", "routing"),
    ("goal clarity assessor", "clarity check"),
    ("director reviewing verification", "verify review"),
    ("director evaluating mid-execution", "director eval"),
    ("director performing a closure check", "closure check"),
    ("meta-learning agent", "lesson extraction"),
    ("plan reviewer", "plan review"),
    ("skill extraction agent", "skill extraction"),
    ("navigator", "navigator"),
    ("generalizable knowledge", "knowledge extraction"),
    ("verification agent", "step verify"),
    ("adversarial reviewer", "adversarial review"),
    ("quality reviewer", "quality review"),
    ("bitter lesson goal rewriter", "goal rewrite"),
    ("strategic advisor", "strategic advisor"),
]

_PERSONA_RE = None  # compiled lazily; regex import kept local to first use

_call_meta_cache: Dict[str, Optional[dict]] = {}
_call_meta_lock = threading.Lock()


def _sniff_call_head(prompt: str) -> Tuple[str, str]:
    """(purpose, persona) sniffed from a recorded prompt's opening text."""
    head = (prompt or "")[:400]
    low = head.lower()
    purpose = ""
    for needle, label in _PURPOSE_PATTERNS:
        if needle in low:
            purpose = label
            break
    persona = ""
    global _PERSONA_RE
    if _PERSONA_RE is None:
        import re
        _PERSONA_RE = re.compile(r"#\s*Persona:\s*([^\n(]+)")
    m = _PERSONA_RE.search(head)
    if m:
        persona = m.group(1).strip()
    return purpose, persona


def _call_meta(path_str: str) -> Optional[dict]:
    """Small-field summary of one call record; None if unreadable."""
    if not path_str:
        return None
    with _call_meta_lock:
        if path_str in _call_meta_cache:
            return _call_meta_cache[path_str]
    meta: Optional[dict] = None
    try:
        rec = json.loads(Path(path_str).read_text(encoding="utf-8"))
        # Caller-stamped purpose (BACKLOG #17 sub-item 2) wins when present;
        # the prompt-opener sniffer is a fallback for records written before
        # record_llm_call() gained the purpose= field.
        stamped_purpose = rec.get("purpose") or ""
        sniffed_purpose, persona = _sniff_call_head(rec.get("prompt", ""))
        purpose = stamped_purpose or sniffed_purpose
        meta = {
            "seq": rec.get("seq"),
            "model": rec.get("model") or "",
            "backend": rec.get("backend") or "",
            "tokens_in": rec.get("tokens_in") or 0,
            "tokens_out": rec.get("tokens_out") or 0,
            "ts": rec.get("ts") or "",
            "purpose": purpose,
            "persona": persona,
            "n_tool_events": len(rec.get("tool_events") or []),
        }
    except Exception:
        meta = None
    with _call_meta_lock:
        _call_meta_cache[path_str] = meta
    return meta


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _esc(x: Any) -> str:
    return html.escape(str(x if x is not None else ""), quote=True)


def _fmt_ts(ts: str) -> str:
    return (ts or "")[:19].replace("T", " ")


def _relpath(target: Path, start: Path) -> str:
    try:
        return os.path.relpath(str(target), start=str(start))
    except Exception:
        return str(target)


def _esc_truncated(text: Optional[str], limit: int) -> str:
    """Escaped display text, truncated at `limit` chars with an ellipsis —
    the full text always rides along in a `title=` tooltip so nothing is
    silently unreachable (2026-07-09, Jeremy: "no way to see the original
    ask"). No-op (plain `_esc`) when the text already fits.
    """
    text = text or ""
    if len(text) <= limit:
        return _esc(text)
    short = text[: limit - 1].rstrip() + "…"
    return f'<span title="{_esc(text)}">{_esc(short)}</span>'


def _fmt_tokens_compact(n: int) -> str:
    """Compact token count for display (4929692 -> '4.93M')."""
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _fmt_tokens_split(tokens_in: int, tokens_out: int) -> str:
    """Compact in/out token display with the exact counts in a tooltip."""
    tokens_in, tokens_out = tokens_in or 0, tokens_out or 0
    if not tokens_in and not tokens_out:
        return "-"
    exact = f"{tokens_in:,} in / {tokens_out:,} out"
    compact = f"{_fmt_tokens_compact(tokens_in)} in / {_fmt_tokens_compact(tokens_out)} out"
    return f'<span title="{_esc(exact)}">{_esc(compact)}</span>'


def _fmt_tokens_total(tokens_in: int, tokens_out: int) -> str:
    """Compact single-number token display (per-step table) with an exact
    in/out breakdown in a tooltip."""
    tokens_in, tokens_out = tokens_in or 0, tokens_out or 0
    total = tokens_in + tokens_out
    exact = f"{total:,} ({tokens_in:,} in / {tokens_out:,} out)"
    return f'<span title="{_esc(exact)}">{_esc(_fmt_tokens_compact(total))}</span>'


_CSS = """
:root {
  --bg: #0d0d14; --panel: #15151f; --border: #2a2a38; --text: #d8d8e0;
  --dim: #9494ac; --green: #4ade80; --red: #f87171; --yellow: #fbbf24;
  --blue: #60a5fa; --gray: #52525b;
}
* { box-sizing: border-box; }
body { background: var(--bg); color: var(--text); font: 16px/1.5 'SF Mono', 'Cascadia Code', monospace;
       padding: 20px; max-width: 1200px; margin: 0 auto; }
h1 { font-size: 20px; color: var(--blue); margin: 0 0 4px; }
h2 { font-size: 15px; color: var(--dim); text-transform: uppercase; letter-spacing: .06em;
     margin: 20px 0 8px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
a { color: var(--blue); }
.meta { color: var(--dim); font-size: 15px; margin-bottom: 2px; }
.meta b { color: var(--text); }
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 12px; }
.badge { display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 13px; margin-left: 4px; }
.badge-done { background: #103a1e; color: var(--green); }
.badge-blocked { background: #3a1414; color: var(--red); }
.badge-skipped { background: #2a2a38; color: var(--dim); }
.badge-pending { background: #1a1a24; color: var(--dim); border: 1px dashed var(--border); }
.badge-retry { background: #3a2d00; color: var(--yellow); }
.badge-approx { background: #1a1a24; color: var(--dim); }

.tl-wrap { overflow-x: auto; padding-bottom: 4px; }
.tl-track { display: flex; flex-direction: row; align-items: stretch; min-height: 50px; gap: 2px; }
.tl-chip { flex-shrink: 0; min-width: 38px; border-radius: 4px; display: flex; flex-direction: column;
           align-items: center; justify-content: center; padding: 4px 6px; font-size: 13px;
           border: 1px solid var(--border); cursor: default; position: relative; }
.tl-chip.st-done { background: #103a1e; border-color: #1e5a34; }
.tl-chip.st-blocked { background: #3a1414; border-color: #5a2020; }
.tl-chip.st-skipped { background: #1a1a24; border-color: var(--border); color: var(--dim); }
.tl-chip.st-pending { background: transparent; border-style: dashed; color: var(--dim); }
.tl-chip .tl-idx { font-weight: bold; }
.tl-chip .tl-foot { position: absolute; top: -7px; right: -2px; font-size: 11px; color: var(--yellow); }
.tl-gap { flex-shrink: 0; min-width: 6px; background: repeating-linear-gradient(45deg, transparent, transparent 3px, var(--border) 3px, var(--border) 4px); border-radius: 2px; }
.tl-current { outline: 2px solid var(--blue); }

table.steps { width: 100%; border-collapse: collapse; font-size: 15px; }
table.steps th { text-align: left; color: var(--dim); font-weight: normal; padding: 5px 8px; border-bottom: 1px solid var(--border); }
table.steps td { padding: 5px 8px; border-bottom: 1px solid #1c1c28; vertical-align: top; }
table.steps tr.st-blocked td { color: var(--red); }
.step-text { max-width: 520px; }
.detail-toggle { background: none; border: 1px solid var(--border); color: var(--dim); border-radius: 3px;
                 padding: 2px 7px; font-size: 13px; cursor: pointer; font-family: inherit; }
.detail-toggle:hover { color: var(--text); border-color: var(--blue); }
.detail-panel { display: none; background: #0a0a10; border: 1px solid var(--border); border-radius: 4px;
                margin-top: 6px; padding: 10px; font-size: 13px; max-height: 340px; overflow: auto; }
.detail-panel.open { display: block; }
.detail-panel h4 { margin: 6px 0 2px; color: var(--dim); font-size: 12px; text-transform: uppercase; }
.detail-panel pre { white-space: pre-wrap; word-break: break-word; margin: 0; }
.detail-error { color: var(--yellow); }
.raw-link { font-size: 13px; color: var(--dim); margin-left: 6px; }

.decision-list { list-style: none; margin: 0; padding: 0; font-size: 15px; }
.decision-list li { padding: 4px 0; border-bottom: 1px solid #1c1c28; }
.decision-list .d-ts { color: var(--dim); font-size: 13px; }
.decision-list .d-type { color: var(--blue); }

details.global-ctx summary { cursor: pointer; color: var(--dim); font-size: 15px; }
.idx-table { width: 100%; border-collapse: collapse; font-size: 15px; margin-top: 8px; }
.idx-table th { text-align: left; color: var(--dim); font-weight: normal; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.idx-table td { padding: 6px 8px; border-bottom: 1px solid #1c1c28; }
.idx-table tr:hover td { background: #171722; }
.idx-table tr[data-href] { cursor: pointer; }
.idx-table th[title] { text-decoration: underline dotted var(--dim); text-underline-offset: 3px; }
.goal-cell { max-width: 520px; }
.footer-nav { margin-top: 16px; font-size: 13px; color: var(--dim); }
.idx-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.idx-header h1 { margin: 0 0 4px; }
details.legend { flex-shrink: 0; max-width: 420px; text-align: right; }
details.legend summary { cursor: pointer; color: var(--dim); font-size: 15px; }
details.legend .meta { margin-top: 8px; line-height: 1.9; text-align: left; }
.idx-filters { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 12px 0; }
.idx-filters input[type="text"], .idx-filters input[type="date"], .idx-filters select {
  background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 4px;
  padding: 4px 8px; font: inherit; font-size: 14px;
}
.idx-filters input[type="text"] { min-width: 220px; }
.idx-filters label { color: var(--dim); font-size: 13px; display: flex; align-items: center; gap: 4px; }
.idx-filters button { background: var(--panel); color: var(--dim); border: 1px solid var(--border); border-radius: 4px;
  padding: 4px 10px; font: inherit; font-size: 14px; cursor: pointer; }
.idx-filters button:hover { color: var(--text); border-color: var(--blue); }
.idx-table tr.idx-hidden { display: none; }
"""

_DETAIL_JS = """
function escHtml(s){ const d=document.createElement('div'); d.innerText=s==null?'':String(s); return d.innerHTML; }
function renderCallRecord(data){
  var out = '';
  var meta = [];
  if (data.model) meta.push(data.model);
  if (data.backend) meta.push(data.backend);
  if (data.tokens_in || data.tokens_out) meta.push((data.tokens_in||0) + ' in / ' + (data.tokens_out||0) + ' out');
  if (meta.length) out += '<div class="meta">' + escHtml(meta.join(' · ')) + '</div>';
  out += '<h4>Prompt</h4><pre>' + escHtml(data.prompt || '') + '</pre>';
  if (data.response) {
    out += '<h4>Response</h4><pre>' + escHtml(data.response) + '</pre>';
  } else if (data.tool_events && data.tool_events.length) {
    // Tool-call-driven steps legitimately record an empty response text —
    // the actual result travels in the tool events below. Say so instead
    // of rendering a blank panel that reads like lost data.
    out += '<h4>Response</h4><div class="meta">(empty — response was delivered via tool call; see tool events)</div>';
  } else {
    out += '<h4>Response</h4><pre></pre>';
  }
  if (data.tool_events && data.tool_events.length) {
    out += '<h4>Tool events (' + data.tool_events.length + ')</h4><pre>' + escHtml(JSON.stringify(data.tool_events, null, 2)) + '</pre>';
  }
  return out;
}
document.querySelectorAll('.detail-toggle').forEach(function(btn){
  btn.addEventListener('click', function(){
    var row = btn.closest('[data-call-record]');
    if (!row) return;
    var rec = row.getAttribute('data-call-record');
    var panel = row.querySelector('.detail-panel');
    if (!rec || !panel) return;
    if (panel.classList.contains('open')) { panel.classList.remove('open'); return; }
    panel.classList.add('open');
    if (panel.dataset.loaded) return;
    fetch(rec).then(function(r){ if (!r.ok) throw new Error('fetch failed'); return r.json(); })
      .then(function(data){ panel.innerHTML = renderCallRecord(data); panel.dataset.loaded = '1'; })
      .catch(function(){
        // Browsers block fetch() of sibling files under a file:// origin —
        // this is the documented fallback, not an error state. Built via
        // DOM APIs, not innerHTML string-concat (2026-07-08 adversarial
        // review, finding #7): `rec` is always an internally-generated
        // calls/call-NNNNN.json path today, but the server-side render
        // already escapes it once — the client-side fallback shouldn't be
        // the one place that trusts an attribute value verbatim into markup.
        panel.textContent = '';
        var div = document.createElement('div');
        div.className = 'detail-error';
        div.appendChild(document.createTextNode('Inline preview needs http:// (browsers block fetch() under file://). '));
        var a = document.createElement('a');
        a.href = rec;
        a.target = '_blank';
        a.textContent = 'Open the raw record ↗';
        div.appendChild(a);
        panel.appendChild(div);
        panel.dataset.loaded = '1';
      });
  });
});
"""


def _step_type_badge(step_text: str) -> str:
    try:
        from step_exec import _classify_step
        t = _classify_step(step_text)
        return t if t and t != "general" else ""
    except Exception:
        return ""


def _render_timeline(
    planned_steps: List[str],
    step_outcomes: List[StepOutcome],
    windows: List[Dict[str, Any]],
    approx: bool,
    marker_slots: Dict[int, List[int]],
    loop_status: str,
) -> str:
    chips: List[str] = []
    attempt_counts: Dict[str, int] = {}
    for s in step_outcomes:
        attempt_counts[s.text] = attempt_counts.get(s.text, 0) + 1
    seen_counts: Dict[str, int] = {}

    for pos, (s, w) in enumerate(zip(step_outcomes, windows), start=1):
        seen_counts[s.text] = seen_counts.get(s.text, 0) + 1
        # 2026-07-08 adversarial review round 2 (Minimalist): in approximate
        # mode, elapsed_ms itself can be the fabricated value (a parallel
        # batch assigns every step in it ~the same elapsed_ms, measured from
        # batch start) — sizing chips by it renders a duration shape the
        # system already knows is wrong, badge or no badge. Equal width is
        # honest about what's actually known here: execution order, not
        # relative duration.
        weight = 1 if approx else max(s.elapsed_ms, 1)
        status = s.status if s.status in _STATUS_CLASS else "blocked"
        cls = _STATUS_CLASS[status]
        icon = _STATUS_ICON.get(status, "?")
        is_last = pos == len(step_outcomes)
        current_cls = " tl-current" if (is_last and loop_status == "running") else ""
        retry_badge = ""
        if attempt_counts[s.text] > 1:
            retry_badge = f' <span title="attempt {seen_counts[s.text]}/{attempt_counts[s.text]}">&#8635;</span>'
        foot = ""
        if pos in marker_slots:
            foot = f'<span class="tl-foot" title="{len(marker_slots[pos])} decision point(s) here">&dagger;{len(marker_slots[pos])}</span>'
        title = _esc(f"{pos}. {s.text[:160]} ({s.status}, {s.elapsed_ms}ms)")
        chips.append(
            f'<div class="tl-chip {cls}{current_cls}" style="flex-grow:{weight}" title="{title}">'
            f'{foot}<span class="tl-idx">{pos}</span><span>{icon}{retry_badge}</span></div>'
        )
        if pos < len(windows):
            gap_ms = (windows[pos]["start"] - w["end"]).total_seconds() * 1000
            if not approx and gap_ms > 500:
                chips.append(f'<div class="tl-gap" style="flex-grow:{int(gap_ms)}" title="{int(gap_ms)}ms between steps"></div>')

    # Pending (not-yet-executed) steps trail as flat placeholders.
    pending_count = max(len(planned_steps) - len(step_outcomes), 0)
    for i in range(pending_count):
        plan_pos = len(step_outcomes) + i + 1
        text = planned_steps[plan_pos - 1] if plan_pos - 1 < len(planned_steps) else ""
        chips.append(
            f'<div class="tl-chip st-pending" style="flex-grow:1" title="{_esc(f"{plan_pos}. {text[:160]} (not yet run)")}">'
            f'<span class="tl-idx">{plan_pos}</span><span>{_STATUS_ICON["pending"]}</span></div>'
        )

    approx_note = (
        '<div class="badge badge-approx">approximate timing — recorded before per-step '
        'timestamps were tracked</div>' if approx and step_outcomes else ""
    )
    return f'<div class="tl-wrap"><div class="tl-track">{"".join(chips)}</div></div>{approx_note}'


def _render_step_table(
    project: str,
    step_outcomes: List[StepOutcome],
    report_dir: Path,
) -> str:
    rows = []
    for pos, s in enumerate(step_outcomes, start=1):
        status = s.status if s.status in _STATUS_CLASS else "blocked"
        icon = _STATUS_ICON.get(status, "?")
        try:
            from metrics import estimate_cost as _est
            cost = _est(s.tokens_in, s.tokens_out, cache_read_tokens=getattr(s, "cache_read_tokens", 0))
            cost_str = f"${cost:.4f}"
        except Exception:
            cost_str = "-"
        tag = _step_type_badge(s.text)
        tag_html = f' <span class="meta">[{_esc(tag)}]</span>' if tag else ""

        data_attr = ""
        detail_html = ""
        model_html = '<span class="meta">-</span>'
        if s.call_record:
            rec_path = Path(s.call_record)
            rel = _relpath(rec_path, report_dir) if rec_path.is_absolute() else s.call_record
            data_attr = f' data-call-record="{_esc(rel)}"'
            detail_html = (
                f'<button class="detail-toggle" type="button">detail</button>'
                f'<a class="raw-link" href="{_esc(rel)}" target="_blank">raw &#8599;</a>'
                f'<div class="detail-panel"></div>'
            )
            cm = _call_meta(str(rec_path if rec_path.is_absolute() else report_dir / rec_path))
            if cm and cm["model"]:
                model_html = f'<span title="{_esc(cm["backend"])}">{_esc(_short_model(cm["model"]))}</span>'
        else:
            detail_html = '<span class="meta">no call record</span>'

        rows.append(
            f'<tr class="{_STATUS_CLASS.get(status, "")}"{data_attr}>'
            f'<td>{pos}</td>'
            f'<td>{icon}</td>'
            f'<td class="step-text">{_esc_truncated(s.text, 200)}{tag_html}</td>'
            f'<td>{model_html}</td>'
            f'<td>{s.elapsed_ms}ms</td>'
            f'<td>{_fmt_tokens_total(s.tokens_in, s.tokens_out)}</td>'
            f'<td>{cost_str}</td>'
            f'<td>{_esc(s.confidence)}</td>'
            f'<td>{detail_html}</td>'
            f'</tr>'
        )
    return (
        '<table class="steps"><thead><tr>'
        '<th>#</th><th></th><th>Step</th><th>Model</th><th>Elapsed</th><th>Tokens</th><th>Cost</th><th>Confidence</th><th>Detail</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    )


def _short_model(model: str) -> str:
    """claude-haiku-4-5-20251001 -> haiku-4-5; keeps unknown names as-is."""
    m = model or ""
    if m.startswith("claude-"):
        m = m[len("claude-"):]
    # strip a trailing -YYYYMMDD date stamp
    parts = m.rsplit("-", 1)
    if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
        m = parts[0]
    return m


def _render_llm_calls(report_dir: Path, step_outcomes: List[StepOutcome]) -> str:
    """Every LLM call recorded for this run directory — not just the one call
    each executed step links. On a real 8-step run this box captured 21
    records: routing, clarity, scope, decompose, navigator, verify, lesson/
    skill extraction all live only here (2026-07-09, Jeremy: sub-agent
    prompts and "thinking" data weren't surfaced). Reuses the step table's
    detail-toggle machinery, so each call's full prompt/response is one
    click away without inlining anything.
    """
    calls_dir = report_dir / "calls"
    if not calls_dir.is_dir():
        return ""
    try:
        call_files = sorted(calls_dir.glob("call-*.json"))
    except Exception:
        return ""
    if not call_files:
        return ""

    # Which call file belongs to which executed step (by basename).
    step_by_rec: Dict[str, int] = {}
    for pos, s in enumerate(step_outcomes, start=1):
        if s.call_record:
            step_by_rec[Path(s.call_record).name] = pos

    rows = []
    personas_seen: List[str] = []
    for cf in call_files:
        cm = _call_meta(str(cf))
        rel = _relpath(cf, report_dir)
        if cm is None:
            rows.append(
                f'<tr><td class="meta">?</td><td class="meta" colspan="6">unreadable record</td>'
                f'<td><a class="raw-link" href="{_esc(rel)}" target="_blank">raw &#8599;</a></td></tr>'
            )
            continue
        if cm["persona"] and cm["persona"] not in personas_seen:
            personas_seen.append(cm["persona"])
        step_pos = step_by_rec.get(cf.name)
        purpose = cm["purpose"] or ""
        purpose_html = _esc(purpose) if purpose else '<span class="meta">?</span>'
        if step_pos:
            purpose_html += f' <span class="meta">(step {step_pos})</span>'
        persona_html = f' <span class="badge badge-approx">{_esc(cm["persona"])}</span>' if cm["persona"] else ""
        rows.append(
            f'<tr data-call-record="{_esc(rel)}">'
            f'<td class="meta">{_esc(cm["seq"])}</td>'
            f'<td class="meta">{_esc(_fmt_ts(cm["ts"]))}</td>'
            f'<td>{purpose_html}{persona_html}</td>'
            f'<td>{_esc(_short_model(cm["model"]))}</td>'
            f'<td class="meta">{_esc(cm["backend"])}</td>'
            f'<td>{_fmt_tokens_split(cm["tokens_in"], cm["tokens_out"])}</td>'
            f'<td class="meta">{cm["n_tool_events"] or "-"}</td>'
            f'<td><button class="detail-toggle" type="button">detail</button>'
            f'<a class="raw-link" href="{_esc(rel)}" target="_blank">raw &#8599;</a>'
            f'<div class="detail-panel"></div></td>'
            f'</tr>'
        )
    persona_note = (
        f' &middot; personas: {_esc(", ".join(personas_seen))}' if personas_seen else ""
    )
    return (
        f'<h2>LLM calls ({len(call_files)})</h2>'
        f'<div class="meta">Every recorded call in this run directory — routing, planning, '
        f'navigation, verification, learning — not just the per-step execution calls'
        f'{persona_note}</div>'
        '<div class="panel"><table class="steps"><thead><tr>'
        '<th>#</th><th>Time</th><th>Purpose</th><th>Model</th><th>Backend</th>'
        '<th>Tokens</th><th title="tool events">Tools</th><th>Detail</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>'
    )


def _render_verdict(report_dir: Path) -> str:
    """Outcome verdict from run_card.json when it exists next to this run's
    build/ dir — goal_achieved, the verdict summary, recorded cost. Written
    by handle.py's curation after the loop finalizes, so a live run's frozen
    report usually predates it (known gap #5 in the design doc); backfilled
    reports get it because the card already exists by then.
    """
    try:
        card_path = report_dir.parent / "run_card.json"
        if not card_path.exists():
            return ""
        card = json.loads(card_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    badge = _render_status_badge(card.get("status") or "", card.get("success_class"))
    achieved = card.get("goal_achieved")
    achieved_str = "yes" if achieved is True else ("no" if achieved is False else "not verified")
    cost = card.get("total_cost_usd")
    cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) else "-"
    verdict = (card.get("goal_verdict_summary") or "").strip()
    verdict_html = f'<div class="meta" style="margin-top:6px">{_esc_truncated(verdict, 600)}</div>' if verdict else ""
    # Downgrade cause as its own labeled line — a goal_achieved:false beside
    # a positive verdict narrative must read as cause, not contradiction.
    downgrade = (card.get("goal_verdict_downgrade_reason") or "").strip()
    downgrade_html = (
        f'<div class="meta" style="margin-top:6px"><b>Downgraded:</b> '
        f'{_esc_truncated(downgrade, 300)}</div>'
        if downgrade else ""
    )
    result_html = ""
    rp = card.get("result_path")
    if rp and Path(rp).exists():
        result_html = f' &middot; <a href="{_esc(_relpath(Path(rp), report_dir))}">result &#8599;</a>'
    return (
        '<h2>Outcome</h2><div class="panel">'
        f'<div class="meta"><b>Verdict:</b> {badge} &middot; <b>Goal achieved:</b> {_esc(achieved_str)} '
        f'&middot; <b><span title="Recorded spend from run_card.json — the authoritative number, unlike the header\'s per-step estimate.">Cost (recorded):</span></b> {_esc(cost_str)}{result_html}</div>'
        f'{downgrade_html}{verdict_html}</div>'
    )


def _render_environment(report_dir: Path) -> str:
    """The run's compile inputs beyond the goal: persona (metadata.json),
    injected skills post-A/B-routing (source/skills_manifest.jsonl), and the
    config era (source/environment.json). All three are optional — runs that
    predate this capture render nothing here. Full config sits behind a
    <details> so the panel stays scannable.
    """
    run_dir = report_dir.parent
    lines: List[str] = []

    # Persona (stamped into metadata.json at selection time)
    try:
        meta = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        pname = meta.get("persona")
        if pname:
            conf = meta.get("persona_confidence")
            conf_str = f" (conf {conf:.2f})" if isinstance(conf, (int, float)) else ""
            flags = []
            if meta.get("persona_forced"):
                flags.append("forced")
            if meta.get("persona_fallback"):
                flags.append("fallback")
            flag_str = f' <span class="meta">[{", ".join(flags)}]</span>' if flags else ""
            lines.append(f'<div class="meta"><b>Persona:</b> {_esc(str(pname))}{_esc(conf_str)}{flag_str}</div>')
    except Exception:
        pass

    # Skills manifest — what actually entered prompts, post variant routing
    try:
        manifest_path = run_dir / "source" / "skills_manifest.jsonl"
        if manifest_path.exists():
            rows = []
            for raw in manifest_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                try:
                    rec = json.loads(raw)
                except Exception:
                    continue
                for s in rec.get("skills", []):
                    variant = s.get("variant_of")
                    variant_str = f'variant of {variant}' if variant else ""
                    rows.append(
                        f'<tr><td>{_esc(rec.get("stage", ""))}</td>'
                        f'<td>{_esc(s.get("name", "") or s.get("id", ""))}</td>'
                        f'<td class="meta">{_esc((s.get("content_hash") or "")[:10])}</td>'
                        f'<td class="meta">{_esc(variant_str)}</td></tr>'
                    )
            if rows:
                lines.append(
                    f'<details><summary>Skills injected ({len(rows)})</summary>'
                    '<table class="idx-table">'
                    '<tr><th>Stage</th><th>Skill</th><th>Hash</th><th>Variant</th></tr>'
                    + "".join(rows) + '</table></details>'
                )
    except Exception:
        pass

    # Environment snapshot — the config era
    try:
        env_path = run_dir / "source" / "environment.json"
        if env_path.exists():
            env = json.loads(env_path.read_text(encoding="utf-8"))
            bits = []
            sha = env.get("maro_git_sha")
            if sha:
                bits.append(f"<b>maro</b> {_esc(str(sha))}")
            host = env.get("host") or {}
            if host.get("hostname"):
                bits.append(f'<b>host</b> {_esc(str(host.get("hostname")))} (py {_esc(str(host.get("python", "?")))})')
            spend = env.get("spend_today_usd_at_start")
            if isinstance(spend, (int, float)):
                bits.append(f'<b><span title="Recorded spend for the day at the moment this run started.">spend today at start</span></b> ${spend:.4f}')
            overrides = env.get("env_overrides") or {}
            if overrides:
                bits.append(f'<b>env overrides</b> {len(overrides)}')
            backends = env.get("backends") or []
            if backends:
                order = " &rarr; ".join(
                    _esc(str(b.get("name", "?"))) + ("" if b.get("usable") else " (unavailable)")
                    for b in backends
                )
                bits.append(f'<b>backends</b> {order}')
            if bits:
                lines.append('<div class="meta">' + " &middot; ".join(bits) + '</div>')
            cfg = env.get("config")
            if cfg:
                cfg_json = json.dumps(cfg, indent=2, default=str)
                lines.append(
                    '<details><summary>Effective config (scrubbed, captured at run start)</summary>'
                    f'<pre>{_esc_truncated(cfg_json, 8000)}</pre></details>'
                )
            if overrides:
                ov_json = json.dumps(overrides, indent=2, default=str)
                lines.append(
                    f'<details><summary>Env overrides ({len(overrides)})</summary>'
                    f'<pre>{_esc_truncated(ov_json, 4000)}</pre></details>'
                )
    except Exception:
        pass

    if not lines:
        return ""
    return '<h2>Environment</h2><div class="panel">' + "".join(lines) + '</div>'


def _event_family(event_type: str) -> str:
    return (event_type or "?").split("_", 1)[0]


def _render_run_activity(entries: List[dict]) -> str:
    """Unattributed captain's-log entries from this run's window — the
    system's own meta-activity (SKILL_*, EVOLVER_*, HYPOTHESIS_*, DIAGNOSIS,
    knowledge extraction...). 85% of real entries carry no loop_id, so this
    is where most of what the system *learned or changed* during a run is
    visible; the family counts stay readable even with the table collapsed.
    """
    if not entries:
        return ""
    families: Dict[str, int] = {}
    for e in entries:
        fam = _event_family(e.get("event_type", ""))
        families[fam] = families.get(fam, 0) + 1
    fam_summary = " &middot; ".join(
        f"{_esc(k)} {v}" for k, v in sorted(families.items(), key=lambda kv: -kv[1])
    )
    rows = []
    for e in entries:
        rows.append(
            f'<tr><td class="meta">{_esc(_fmt_ts(e.get("timestamp", "")))}</td>'
            f'<td>{_esc(e.get("event_type", ""))}</td>'
            f'<td>{_esc(e.get("subject", ""))}</td>'
            f'<td class="meta">{_esc_truncated(e.get("summary", "") or "", 200)}</td></tr>'
        )
    return (
        '<h2>Run activity</h2>'
        f'<div class="meta">System meta-events logged during this run without a loop attribution '
        f'&mdash; {fam_summary}</div>'
        f'<details class="global-ctx"><summary>Show all {len(entries)} events</summary>'
        '<table class="idx-table">'
        '<tr><th>Time</th><th>Type</th><th>Subject</th><th>Summary</th></tr>'
        + "".join(rows) + '</table></details>'
    )


def _render_decision_points(markers: List[dict]) -> str:
    if not markers:
        return ""
    items = []
    for m in markers:
        items.append(
            '<li><span class="d-ts">' + _esc(_fmt_ts(m.get("timestamp", ""))) + '</span> '
            '<span class="d-type">' + _esc(m.get("event_type", "")) + '</span> '
            '&mdash; ' + _esc(m.get("subject", "")) +
            (f'<br><span class="meta">{_esc(m.get("summary", ""))}</span>' if m.get("summary") else "") +
            '</li>'
        )
    return '<h2>Decision points (' + str(len(markers)) + ')</h2><ul class="decision-list">' + "".join(items) + '</ul>'


def _render_injections(injections: Optional[List[dict]]) -> str:
    """Operator injections applied mid-run — §6a after-the-fact delineation.

    Injected content must read as injected, never blended into the goal or
    the plan. Goal changes render the original goal alongside the new one.
    """
    if not injections:
        return ""
    items = []
    for inj in injections:
        intent = _esc(str(inj.get("intent", "?")))
        source = _esc(str(inj.get("source", "?")))
        ts = _esc(_fmt_ts(str(inj.get("ts", ""))))
        message = _esc_truncated(str(inj.get("message", "")), 400)
        scope = "context-only" if inj.get("context_only") else "plan-affecting"
        goal_html = ""
        if inj.get("goal_after"):
            goal_html = (
                '<div class="inj-goal-change"><b>GOAL CHANGED:</b> '
                f'<s>{_esc_truncated(str(inj.get("goal_before", "")), 160)}</s>'
                ' &rarr; '
                f'<b>{_esc_truncated(str(inj.get("goal_after", "")), 160)}</b></div>'
            )
        items.append(
            f'<li><span class="badge">{intent}</span> from <b>{source}</b>'
            f' at {ts} <i>({scope})</i>:<br>&ldquo;{message}&rdquo;{goal_html}</li>'
        )
    return (
        f'<h2>Operator injections ({len(injections)})</h2>'
        '<div class="panel" style="border-left:4px solid #c77d00;">'
        '<p><i>Injected mid-run by the operator — not part of the original '
        'goal or plan.</i></p>'
        '<ul class="decision-list">' + "".join(items) + '</ul></div>'
    )


def _render_report_html(
    *,
    project: str,
    loop_id: str,
    goal: str,
    planned_steps: List[str],
    start_ts: str,
    step_outcomes: List[StepOutcome],
    status: str,
    elapsed_ms: int,
    replan_count: int,
    report_dir: Path,
    index_link: Optional[str],
    injections: Optional[List[dict]] = None,
) -> str:
    windows, approx = _step_windows(step_outcomes, start_ts)
    attributed_markers, activity_entries = _gather_log_markers(loop_id, start_ts, report_dir)
    marker_slots = _slot_markers(attributed_markers, windows, approx)

    done = sum(1 for s in step_outcomes if s.status == "done")
    blocked = sum(1 for s in step_outcomes if s.status == "blocked")
    total_planned = len(planned_steps)
    total_tokens_in = sum(s.tokens_in for s in step_outcomes)
    total_tokens_out = sum(s.tokens_out for s in step_outcomes)
    try:
        from metrics import estimate_cost as _est
        total_cost = _est(total_tokens_in, total_tokens_out)
        cost_str = f"${total_cost:.4f}"
    except Exception:
        cost_str = "-"

    sentinel = (
        f'<!-- maro-report: final status={_esc(status)} -->'
        if status != "running" else
        '<!-- maro-report: status=running -->'
    )

    replan_html = f' &middot; <b>{replan_count}</b> replan(s)' if replan_count else ""
    nav_html = ""
    if index_link:
        nav_html = f'<div class="footer-nav"><a href="{_esc(index_link)}">&larr; all runs</a></div>'
    # Cheap nicety for an in-flight run — reload picks up the next
    # regeneration. Never emitted once frozen (status != "running"), so a
    # finished report never re-fetches itself.
    refresh_html = '<meta http-equiv="refresh" content="30">' if status == "running" else ""
    goal_changed_html = (
        ' <b style="color:#c77d00;">(redirected mid-run &mdash; see Operator injections)</b>'
        if any(inj.get("goal_after") for inj in (injections or [])) else ""
    )

    body = f"""{sentinel}
<!DOCTYPE html>
<html><head><meta charset="utf-8">{refresh_html}<title>Run {_esc(loop_id)}</title>
<style>{_CSS}</style></head>
<body>
<h1>Run <code>{_esc(loop_id)}</code></h1>
<div class="meta"><b>Project:</b> {_esc(project or "(none)")} &middot; <b>Goal:</b> {_esc_truncated(goal, 160)}{goal_changed_html}</div>
<div class="meta"><b><span title="Process status only — steps finished/blocked, not whether the goal was verified achieved. See the cross-run index for that once curation runs.">Status:</span></b> {_esc(status)} &middot; <b>Progress:</b> {done}/{total_planned} done, {blocked} blocked{replan_html}</div>
<div class="meta"><b>Started:</b> {_esc(_fmt_ts(start_ts))} &middot; <b>Elapsed:</b> {elapsed_ms}ms &middot; <b>Tokens:</b> {_fmt_tokens_split(total_tokens_in, total_tokens_out)} &middot; <b><span title="Estimated from this report's step token counts — may differ from the run's recorded actual spend shown in the cross-run index.">Cost (est.):</span></b> {cost_str}</div>

{_render_verdict(report_dir)}
{_render_injections(injections)}
<h2>Timeline</h2>
<div class="panel">{_render_timeline(planned_steps, step_outcomes, windows, approx, marker_slots, status)}</div>

<h2>Steps</h2>
<div class="panel">{_render_step_table(project, step_outcomes, report_dir)}</div>

{_render_llm_calls(report_dir, step_outcomes)}
{_render_decision_points(attributed_markers)}
{_render_run_activity(activity_entries)}
{_render_environment(report_dir)}
{nav_html}
<script>{_DETAIL_JS}</script>
</body></html>
"""
    return body


# ---------------------------------------------------------------------------
# Public: per-run report
# ---------------------------------------------------------------------------

def write_run_report(
    project: str,
    loop_id: str,
    goal: str,
    planned_steps: List[str],
    start_ts: str,
    step_outcomes: Optional[List[StepOutcome]] = None,
    *,
    status: str = "running",
    elapsed_ms: int = 0,
    replan_count: int = 0,
    injections: Optional[List[dict]] = None,
) -> Optional[str]:
    """Write (or overwrite) the per-run HTML visibility report.

    Same call shape as loop_artifacts._write_plan_manifest so every hook
    site can pass what it already has. Never raises. No-op (returns the
    existing path) once the report has been frozen by a terminal write.
    """
    if not project:
        return None
    path = _report_path(project, loop_id)
    if path is None:
        return None

    # Debug-snapshot hygiene runs regardless of report.enabled — cheap
    # (an existence check) and this is the only place that does it, so
    # disabling reports shouldn't strand a leftover snapshot dir
    # (2026-07-08 adversarial review, finding #9; the narrower remaining
    # case — a loop whose report is never written again — is documented in
    # docs/RUN_VISIBILITY_DESIGN.md rather than solved with a standalone
    # sweep, which would be more machinery than this developer-only aid
    # warrants).
    if not _debug_snapshots_enabled():
        _clear_debug_snapshots(project, loop_id)

    if not _reports_enabled():
        return None

    o = _orch()
    # Hold the report's own lock across the frozen-check AND the write
    # (2026-07-08 adversarial review, Reality Checker claim 1): checking
    # _is_frozen() and then writing were two unsynchronized steps, so a
    # post-step writer and a finalize writer could interleave and the
    # finalize (frozen) write could be clobbered by a late post-step write
    # that started its check before finalize's write landed. One lock held
    # across both steps makes the two writers see each other's outcome.
    from file_lock import locked_write
    with locked_write(path):
        if path.exists() and _is_frozen(path):
            try:
                return o.relative_display_path(path)
            except Exception:
                return str(path)

        step_outcomes = step_outcomes or []
        try:
            from runs import runs_root as _runs_root, current_run_dir as _current_run_dir
            rd = _current_run_dir()
            index_link = None
            if rd is not None:
                try:
                    index_path = _runs_root() / "index.html"
                    index_link = _relpath(index_path, path.parent)
                except Exception:
                    index_link = None
        except Exception:
            index_link = None

        try:
            content = _render_report_html(
                project=project,
                loop_id=loop_id,
                goal=goal,
                planned_steps=planned_steps,
                start_ts=start_ts,
                step_outcomes=step_outcomes,
                status=status,
                elapsed_ms=elapsed_ms,
                replan_count=replan_count,
                report_dir=path.parent,
                index_link=index_link,
                injections=injections,
            )
            _atomic_write_text(path, content)
        except Exception:
            log.warning("run report write failed for loop %s", loop_id, exc_info=True)
            return None

        if _debug_snapshots_enabled():
            _maybe_snapshot(content, project, loop_id)

    try:
        return o.relative_display_path(path)
    except Exception:
        return str(path)


# ---------------------------------------------------------------------------
# Public: cross-run static index
# ---------------------------------------------------------------------------

def _gather_run_summaries() -> List[dict]:
    try:
        from runs import runs_root
    except Exception:
        return []
    root = runs_root()
    if not root.is_dir():
        return []
    summaries: List[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        meta_path = d / "metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        card: dict = {}
        card_path = d / "run_card.json"
        if card_path.exists():
            try:
                card = json.loads(card_path.read_text(encoding="utf-8"))
            except Exception:
                card = {}

        build_dir = d / "build"
        reports: List[str] = []
        totals = {"tokens_in": 0, "tokens_out": 0, "steps_done": 0, "steps_blocked": 0}
        if build_dir.is_dir():
            reports = sorted(str(p.relative_to(d)) for p in build_dir.glob("loop-*-report.html"))
            reports += sorted(str(p.relative_to(d)) for p in build_dir.glob("now-*-report.html"))
            for log_path in sorted(build_dir.glob("loop-*-log.json")):
                try:
                    lj = json.loads(log_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                t = lj.get("totals", {})
                totals["tokens_in"] += t.get("tokens_in", 0)
                totals["tokens_out"] += t.get("tokens_out", 0)
                totals["steps_done"] += t.get("steps_done", 0)
                totals["steps_blocked"] += t.get("steps_blocked", 0)

        started_at = meta.get("started_at", "") or ""
        ended_at = meta.get("ended_at")
        elapsed_str = "-"
        if started_at and ended_at:
            s = _parse_iso(started_at)
            e = _parse_iso(ended_at)
            if s and e:
                elapsed_str = f"{int((e - s).total_seconds())}s"

        summaries.append({
            "dir_name": d.name,
            "handle_id": meta.get("handle_id", d.name),
            "nickname": meta.get("nickname", ""),
            "goal": meta.get("prompt", ""),
            "lane": meta.get("lane"),
            "status": card.get("status") or meta.get("status") or "unknown",
            "success_class": card.get("success_class"),
            "started_at": started_at,
            "ended_at": ended_at,
            "elapsed_str": elapsed_str,
            "cost_usd": card.get("total_cost_usd"),
            "totals": totals,
            "reports": reports,
        })
    summaries.sort(key=lambda s: s.get("started_at") or "", reverse=True)
    return summaries


def _effective_status(s: dict) -> str:
    """The status value the index badge actually displays: success_class
    once curation has classified the run, else the raw process status.
    Shared by the index's client-side filter and search_runs() so both
    surfaces agree on what "status" means for a run.
    """
    return s.get("success_class") or s.get("status") or "unknown"


def _normalize_date_bound(value: Optional[str], *, end_of_day: bool) -> Optional[str]:
    """A bare `YYYY-MM-DD` bound needs a time suffix to compare correctly
    against full `started_at` ISO timestamps (string comparison is
    lexicographic): `--until 2026-07-01` should include the whole day, not
    exclude everything after midnight. Full timestamps pass through as-is.
    """
    if value and len(value) == 10:
        return value + ("T23:59:59.999999" if end_of_day else "T00:00:00")
    return value


def search_runs(
    *,
    goal: Optional[str] = None,
    status: Optional[str] = None,
    lane: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> List[dict]:
    """Linear scan over run summaries, filtered by goal text / status / lane
    / date range (BACKLOG #17 — "goal search in the run visualization").

    Deliberately reuses `_gather_run_summaries()` — the same data the
    cross-run index renders from — rather than a parallel index or
    database. At current scale (hundreds to low-thousands of run dirs)
    a plain scan is the right layer; see the adjacent (deferred) "Storage
    decision" backlog entry.

    - `goal`: case-insensitive substring match against the goal/prompt text.
    - `status`: exact match (case-insensitive) against the effective status
      — `success_class` once curated (success / done-not-achieved /
      done-unverified / partial / failed), else the raw process status.
    - `lane`: exact match (case-insensitive) against the run's lane
      (now / agenda / user_goal).
    - `since` / `until`: inclusive bounds on `started_at`, ISO date
      (`2026-07-01`) or full timestamp.

    Returns summaries newest-first (same order as `_gather_run_summaries`).
    """
    summaries = _gather_run_summaries()
    goal_needle = goal.strip().lower() if goal else None
    status_needle = status.strip().lower() if status else None
    lane_needle = lane.strip().lower() if lane else None
    since_norm = _normalize_date_bound(since, end_of_day=False)
    until_norm = _normalize_date_bound(until, end_of_day=True)

    def _matches(s: dict) -> bool:
        if goal_needle and goal_needle not in (s.get("goal") or "").lower():
            return False
        if status_needle and _effective_status(s).lower() != status_needle:
            return False
        if lane_needle and (s.get("lane") or "").lower() != lane_needle:
            return False
        started = s.get("started_at") or ""
        if since_norm and started < since_norm:
            return False
        if until_norm and started > until_norm:
            return False
        return True

    return [s for s in summaries if _matches(s)]


def _render_status_badge(status: str, success_class: Optional[str]) -> str:
    if success_class:
        label, badge_cls, help_text = _SUCCESS_CLASS_INFO.get(
            success_class, (success_class, "badge-pending", "")
        )
        return f'<span class="badge {badge_cls}" title="{_esc(help_text)}">{_esc(label)}</span>'
    return (
        '<span class="badge badge-pending" '
        'title="Process status only — verified outcome not yet available (curation runs after the process finishes).">'
        f'{_esc(status)}</span>'
    )


_INDEX_ROW_JS = """
document.querySelectorAll('.idx-table tr[data-href]').forEach(function(row){
  row.addEventListener('click', function(e){
    if (window.getSelection().toString()) return;  // preserve text selection/copy
    if (e.target.closest('a')) return;              // let the real link behave normally
    window.location.href = row.getAttribute('data-href');
  });
});
"""

# Client-side filter over the index's already-rendered rows (BACKLOG #17,
# "goal search in the run visualization"). No new endpoint, no shipped-JSON
# index — the table itself IS the data; this just toggles row visibility.
# Guard on f-goal existing: the filter bar isn't rendered when there are no
# runs yet (nothing to filter), so this is a silent no-op on that page.
_INDEX_FILTER_JS = """
(function(){
  var goalInput = document.getElementById('f-goal');
  if (!goalInput) return;
  var statusSel = document.getElementById('f-status');
  var laneSel = document.getElementById('f-lane');
  var sinceInput = document.getElementById('f-since');
  var untilInput = document.getElementById('f-until');
  var clearBtn = document.getElementById('f-clear');
  var countEl = document.getElementById('f-count');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.idx-table tr[data-goal]'));

  function apply(){
    var q = goalInput.value.trim().toLowerCase();
    var st = statusSel.value;
    var ln = laneSel.value;
    var since = sinceInput.value;
    var until = untilInput.value;
    var shown = 0;
    rows.forEach(function(row){
      var ok = true;
      if (q && row.getAttribute('data-goal').indexOf(q) === -1) ok = false;
      if (ok && st && row.getAttribute('data-status') !== st) ok = false;
      if (ok && ln && row.getAttribute('data-lane') !== ln) ok = false;
      var d = row.getAttribute('data-date') || '';
      if (ok && since && (!d || d < since)) ok = false;
      if (ok && until && (!d || d > until)) ok = false;
      row.classList.toggle('idx-hidden', !ok);
      if (ok) shown++;
    });
    countEl.textContent = shown + ' / ' + rows.length + ' run(s) shown';
  }

  [goalInput, statusSel, laneSel, sinceInput, untilInput].forEach(function(el){
    el.addEventListener('input', apply);
    el.addEventListener('change', apply);
  });
  clearBtn.addEventListener('click', function(){
    goalInput.value = ''; statusSel.value = ''; laneSel.value = '';
    sinceInput.value = ''; untilInput.value = '';
    apply();
  });
  apply();
})();
"""


def _render_index_html(summaries: List[dict]) -> str:
    rows = []
    statuses_seen: Dict[str, str] = {}  # raw effective-status value -> display label
    lanes_seen: set = set()
    for s in summaries:
        cost = s.get("cost_usd")
        cost_str = f"${cost:.4f}" if isinstance(cost, (int, float)) else "-"
        t = s["totals"]
        tok_str = _fmt_tokens_split(t["tokens_in"], t["tokens_out"])
        primary_href = None
        if s["reports"]:
            links = []
            for r in s["reports"]:
                href = f'{s["dir_name"]}/{r}'
                if primary_href is None:
                    primary_href = href
                stem = Path(r).stem.replace("-report", "")
                # NOW reports embed the (long) handle_id; the row already
                # names the run, so a lane label reads better than a UUID.
                label = "now" if stem.startswith("now-") else stem.replace("loop-", "")
                links.append(f'<a href="{_esc(href)}">{_esc(label)}</a>')
            links_html = " ".join(links)
        else:
            links_html = '<span class="meta">no report</span>'
        row_attr = f' data-href="{_esc(primary_href)}"' if primary_href else ""

        eff_status = _effective_status(s)
        status_label, _cls, _help = _SUCCESS_CLASS_INFO.get(eff_status, (eff_status, "badge-pending", ""))
        statuses_seen.setdefault(eff_status, status_label)
        lane_val = s.get("lane") or ""
        if lane_val:
            lanes_seen.add(lane_val)
        filter_attrs = (
            f' data-goal="{_esc((s["goal"] or "").lower())}"'
            f' data-status="{_esc(eff_status)}"'
            f' data-lane="{_esc(lane_val)}"'
            f' data-date="{_esc((s.get("started_at") or "")[:10])}"'
        )
        rows.append(
            f'<tr{row_attr}{filter_attrs}>'
            f'<td class="meta">{_esc(_fmt_ts(s["started_at"]))}</td>'
            f'<td>{_render_status_badge(s["status"], s.get("success_class"))}</td>'
            f'<td class="goal-cell">{_esc_truncated(s["goal"], 220)}</td>'
            f'<td class="meta">{_esc(s.get("lane") or "-")}</td>'
            f'<td>{_esc(s["elapsed_str"])}</td>'
            f'<td class="meta">{tok_str}</td>'
            f'<td title="Recorded spend for this run (run_card.json) — independent of the per-step token estimate shown on the report page.">{_esc(cost_str)}</td>'
            f'<td>{links_html}</td>'
            '</tr>'
        )
    body_rows = "".join(rows) if rows else '<tr><td colspan="8" class="meta">No runs yet.</td></tr>'
    legend_rows = "".join(
        f'<span class="badge {badge_cls}">{_esc(label)}</span> {_esc(help_text)}<br>'
        for label, badge_cls, help_text in _SUCCESS_CLASS_INFO.values()
    )
    filters_html = ""
    if rows:
        status_options = "".join(
            f'<option value="{_esc(val)}">{_esc(lbl)}</option>'
            for val, lbl in sorted(statuses_seen.items(), key=lambda kv: kv[1])
        )
        lane_options = "".join(
            f'<option value="{_esc(v)}">{_esc(v)}</option>' for v in sorted(lanes_seen)
        )
        filters_html = f"""
<div class="idx-filters">
<input type="text" id="f-goal" placeholder="Search goal text..." autocomplete="off">
<select id="f-status" title="Filter by status"><option value="">All statuses</option>{status_options}</select>
<select id="f-lane" title="Filter by lane"><option value="">All lanes</option>{lane_options}</select>
<label>From <input type="date" id="f-since"></label>
<label>To <input type="date" id="f-until"></label>
<button type="button" id="f-clear">Clear</button>
<span class="meta" id="f-count"></span>
</div>"""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Maro runs</title>
<style>{_CSS}</style></head>
<body>
<div class="idx-header">
<div><h1>Runs</h1><div class="meta">{len(summaries)} run(s), newest first</div></div>
<details class="legend"><summary>What do Status / Lane mean?</summary>
<div class="meta">{legend_rows}<br><b>Lane:</b> {_esc(_LANE_HELP)}</div>
</details>
</div>
{filters_html}
<table class="idx-table">
<tr><th>Started</th><th>Status</th><th>Goal</th><th title="{_esc(_LANE_HELP)}">Lane</th><th>Elapsed</th><th>Tokens</th><th>Cost</th><th>Report</th></tr>
{body_rows}
</table>
<script>{_INDEX_ROW_JS}{_INDEX_FILTER_JS}</script>
</body></html>
"""


_INDEX_DEBOUNCE_SECONDS = 3.0
_last_index_write: Dict[str, float] = {}
_index_write_lock = threading.Lock()


def write_runs_index(*, force: bool = False) -> Optional[str]:
    """Write the cross-run static index. Never raises.

    Debounced by default: this scans every run-dir under runs_root() every
    time it's called, and the per-step hook calls it after *every* step of
    *every* active run — O(total historical runs) work per step, forever.
    Skips the rescan if the index was regenerated within the last few
    seconds. Pass force=True where an authoritative write matters more than
    the debounce (loop finalize — a run's terminal state should always be
    reflected immediately).

    Respects report.enabled (2026-07-08 adversarial review, finding #6):
    previously this ran unconditionally even with the run-visibility feature
    turned off via config, so disabling it only stopped per-run reports, not
    the index scan/write.
    """
    if not _reports_enabled():
        return None
    try:
        from runs import runs_root
        root = runs_root()
        key = str(root)
        now = _time.monotonic()
        if not force:
            with _index_write_lock:
                last = _last_index_write.get(key, 0.0)
                if now - last < _INDEX_DEBOUNCE_SECONDS:
                    return str(root / "index.html")
        root.mkdir(parents=True, exist_ok=True)
        out = root / "index.html"
        # Lock + atomic replace (2026-07-08 review, findings #4 / Reality
        # Checker claim 1): this is the first artifact this feature writes
        # that's shared across every concurrently-running process, not just
        # within one run's own directory — a bare write_text() here is the
        # one write in this module where two different orchestrator
        # processes racing is a realistic, not hypothetical, scenario.
        from file_lock import locked_write
        with locked_write(out):
            summaries = _gather_run_summaries()
            content = _render_index_html(summaries)
            _atomic_write_text(out, content)
        # Record the timestamp on every real write, forced or not — otherwise
        # a forced write (e.g. loop finalize) wouldn't suppress an immediate
        # follow-up debounced call, defeating the point of debouncing.
        with _index_write_lock:
            _last_index_write[key] = now
        return str(out)
    except Exception:
        log.warning("runs index write failed", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Backfill — reports for runs that predate this feature
#
# The feature shipped 2026-07-08; every run before that (665 on this box)
# has the raw data on disk (build/loop-*-log.json, calls/, the captain's-log
# slice, run_card.json) but no report, and the index only listed such runs
# link-less. Backfill reconstructs each loop's StepOutcomes from its loop
# log and writes the same frozen report the live path would have, then one
# forced index write. It's also the real-data shakeout the feature never
# had: until now the generator had only ever seen synthetic test fixtures.
# ---------------------------------------------------------------------------

def _outcomes_from_loop_log(lj: dict) -> List[StepOutcome]:
    outcomes: List[StepOutcome] = []
    for st in lj.get("steps", []) or []:
        outcomes.append(StepOutcome(
            index=st.get("index", 0),
            text=st.get("text", ""),
            status=st.get("status", "done"),
            result="",  # loop logs persist result_length only; full text lives in the call record
            iteration=st.get("iteration", 0),
            tokens_in=st.get("tokens_in", 0) or 0,
            tokens_out=st.get("tokens_out", 0) or 0,
            elapsed_ms=st.get("elapsed_ms", 0) or 0,
            call_record=st.get("call_record", "") or "",
            # Missing on pre-feature logs -> "" -> the timeline's designed
            # approximate-mode fallback, flagged as such in the report.
            ended_ts=st.get("ended_ts", "") or "",
        ))
    return outcomes


def _write_loop_report_from_log(build: Path, log_path: Path, root: Path) -> None:
    """Render one loop's report directly from its build/loop-*-log.json.

    Bypasses write_run_report's current_run_dir pinning and frozen check —
    callers (backfill, post-curation re-render) have already decided this
    report should be (re)written. Raises on failure; callers count.
    """
    loop_id = log_path.name[len("loop-"):-len("-log.json")]
    report_path = build / f"loop-{loop_id}-report.html"
    lj = json.loads(log_path.read_text(encoding="utf-8"))
    outcomes = _outcomes_from_loop_log(lj)
    status = lj.get("status") or "done"
    # A loop log still claiming "running" is a crashed/killed run
    # — rendering it as running would emit the auto-refresh tag
    # and no freeze sentinel, forever. The run demonstrably isn't
    # running anymore; "interrupted" is the honest terminal state.
    if status == "running":
        status = "interrupted"
    index_link = _relpath(root / "index.html", build)
    content = _render_report_html(
        project=lj.get("project", "") or "",
        loop_id=loop_id,
        goal=lj.get("goal", "") or "",
        planned_steps=[s.text for s in outcomes],
        start_ts=lj.get("started_at", "") or "",
        step_outcomes=outcomes,
        status=status,
        elapsed_ms=lj.get("elapsed_ms", 0) or 0,
        replan_count=0,
        report_dir=build,
        index_link=index_link,
    )
    from file_lock import locked_write
    with locked_write(report_path):
        _atomic_write_text(report_path, content)


def _render_now_report_html(run_dir: Path, artifact: dict, meta: dict,
                            index_link: Optional[str]) -> str:
    """Mini-report for a NOW-lane run: no steps or timeline, just the
    question, the answer, the calls that produced it, and the run's meta
    panels (verdict, activity, environment) — all data that already exists
    in the run dir; this is purely a rendering layer.
    """
    build = run_dir / "build"
    handle_id = artifact.get("handle_id") or meta.get("handle_id") or run_dir.name
    status = meta.get("status") or "done"
    if status == "running":
        status = "interrupted"
    message = artifact.get("message") or meta.get("prompt") or ""
    result = artifact.get("result") or ""
    created_at = artifact.get("created_at") or meta.get("started_at") or ""
    elapsed_ms = artifact.get("elapsed_ms", 0) or 0
    achieved = meta.get("goal_achieved")
    achieved_html = ""
    if achieved is not None:
        achieved_html = f' &middot; <b>Goal achieved:</b> {"yes" if achieved else "no"}'

    attributed_markers, activity_entries = _gather_log_markers(
        handle_id, meta.get("started_at", "") or "", build if build.is_dir() else None
    )
    nav_html = ""
    if index_link:
        nav_html = f'<div class="footer-nav"><a href="{_esc(index_link)}">&larr; all runs</a></div>'
    nickname = meta.get("nickname", "")

    return f"""<!-- maro-report: final status={_esc(status)} -->
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>NOW {_esc(nickname or handle_id)}</title>
<style>{_CSS}</style></head>
<body>
<h1>NOW run <code>{_esc(nickname or handle_id)}</code></h1>
<div class="meta"><b>Lane:</b> now &middot; <b>Status:</b> {_esc(status)}{achieved_html}</div>
<div class="meta"><b>Started:</b> {_esc(_fmt_ts(created_at))} &middot; <b>Elapsed:</b> {elapsed_ms}ms</div>

{_render_verdict(build)}
<h2>Request</h2>
<div class="panel"><pre>{_esc_truncated(message, 4000)}</pre></div>

<h2>Result</h2>
<div class="panel"><pre>{_esc_truncated(result, 20000)}</pre></div>

{_render_llm_calls(build, [])}
{_render_decision_points(attributed_markers)}
{_render_run_activity(activity_entries)}
{_render_environment(build)}
{nav_html}
<script>{_DETAIL_JS}</script>
</body></html>
"""


def _write_now_report(run_dir: Path, root: Path) -> Optional[Path]:
    """Render build/now-<handle_id>-report.html for a NOW-lane run dir.

    Returns the report path, or None when the dir is not a NOW run.
    Raises on render/write failure; callers count. Always overwrites — NOW
    reports are only ever written after the run finished, so there is no
    live-vs-frozen distinction to protect.

    Runs that predate the NOW artifact writer (metadata says lane=now but
    artifact/ is empty) still get a report from metadata alone — request,
    status, and the activity slice are all real; only the result text was
    never captured, and the report says so rather than pretending.
    """
    meta: dict = {}
    meta_path = run_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    artifacts = sorted((run_dir / "artifact").glob("now-*.json")) if (run_dir / "artifact").is_dir() else []
    if artifacts:
        artifact = json.loads(artifacts[0].read_text(encoding="utf-8"))
    elif meta.get("lane") == "now":
        elapsed_ms = 0
        s = _parse_iso(meta.get("started_at") or "")
        e = _parse_iso(meta.get("ended_at") or "")
        if s and e:
            elapsed_ms = int((e - s).total_seconds() * 1000)
        artifact = {
            "handle_id": meta.get("handle_id"),
            "message": meta.get("prompt", ""),
            "result": "(result not captured — this run predates the NOW artifact writer)",
            "created_at": meta.get("started_at", ""),
            "elapsed_ms": elapsed_ms,
        }
    else:
        return None
    build = run_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    handle_id = artifact.get("handle_id") or meta.get("handle_id") or run_dir.name
    report_path = build / f"now-{handle_id}-report.html"
    index_link = _relpath(root / "index.html", build)
    content = _render_now_report_html(run_dir, artifact, meta, index_link)
    from file_lock import locked_write
    with locked_write(report_path):
        _atomic_write_text(report_path, content)
    return report_path


def write_reports_for_run_dir(run_dir: Path) -> Dict[str, int]:
    """Force re-render every report in one run dir, then rebuild the index.

    The post-curation hook: called from handle.py's finalize AFTER
    run_curation writes run_card.json, so the frozen report gets re-rendered
    with the verdict it couldn't have had at freeze time (design known-gap
    #5). Also writes the NOW mini-report when the dir has a NOW artifact.
    Never raises. Returns {"written", "failed"}.
    """
    counts = {"written": 0, "failed": 0}
    try:
        from runs import runs_root
        root = runs_root()
    except Exception:
        return counts
    if not _reports_enabled():
        return counts
    build = run_dir / "build"
    if build.is_dir():
        for log_path in sorted(build.glob("loop-*-log.json")):
            try:
                _write_loop_report_from_log(build, log_path, root)
                counts["written"] += 1
            except Exception:
                log.warning("report re-render failed for %s", log_path, exc_info=True)
                counts["failed"] += 1
    try:
        if _write_now_report(run_dir, root) is not None:
            counts["written"] += 1
    except Exception:
        log.warning("NOW report write failed for %s", run_dir, exc_info=True)
        counts["failed"] += 1
    try:
        write_runs_index(force=True)
    except Exception:
        pass
    return counts


def backfill_run_reports(*, force: bool = False, limit: Optional[int] = None) -> Dict[str, int]:
    """Generate frozen reports for historical runs, then rebuild the index.

    Covers both loop reports (from build/loop-*-log.json) and NOW-lane
    mini-reports (from artifact/now-*.json). Skips runs that already have a
    report unless force=True (force also overwrites frozen reports — that's
    the point: re-render an old report with data that didn't exist at
    finalize time, e.g. run_card.json).
    Returns counts: {"written", "skipped", "failed", "runs_scanned"}.
    """
    counts = {"written": 0, "skipped": 0, "failed": 0, "runs_scanned": 0}
    try:
        from runs import runs_root
        root = runs_root()
    except Exception:
        return counts
    if not root.is_dir():
        return counts

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        build = d / "build"
        artifact_dir = d / "artifact"
        has_loop_logs = build.is_dir() and any(build.glob("loop-*-log.json"))
        has_now = artifact_dir.is_dir() and any(artifact_dir.glob("now-*.json"))
        if not has_now:
            # Pre-artifact-writer NOW runs: metadata is the only marker.
            try:
                _m = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
                has_now = _m.get("lane") == "now"
            except Exception:
                pass
        if not has_loop_logs and not has_now:
            continue
        counts["runs_scanned"] += 1
        if has_loop_logs:
            for log_path in sorted(build.glob("loop-*-log.json")):
                loop_id = log_path.name[len("loop-"):-len("-log.json")]
                report_path = build / f"loop-{loop_id}-report.html"
                if report_path.exists() and not force:
                    counts["skipped"] += 1
                    continue
                try:
                    _write_loop_report_from_log(build, log_path, root)
                    counts["written"] += 1
                except Exception:
                    log.warning("backfill failed for %s", log_path, exc_info=True)
                    counts["failed"] += 1
                if limit is not None and counts["written"] >= limit:
                    write_runs_index(force=True)
                    return counts
        if has_now:
            existing_now = build.is_dir() and any(build.glob("now-*-report.html"))
            if existing_now and not force:
                counts["skipped"] += 1
            else:
                try:
                    if _write_now_report(d, root) is not None:
                        counts["written"] += 1
                except Exception:
                    log.warning("NOW backfill failed for %s", d, exc_info=True)
                    counts["failed"] += 1
                if limit is not None and counts["written"] >= limit:
                    write_runs_index(force=True)
                    return counts

    write_runs_index(force=True)
    return counts
