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

def _gather_log_markers(loop_id: str, start_ts: str) -> Tuple[List[dict], List[dict]]:
    """Return (attributed, global) captain's-log entries for this run.

    attributed: entries whose loop_id matches this loop, chronological.
    global: entries with no loop_id at all, timestamped since this run
    started — cross-run reflections that aren't part of this run but
    shouldn't be silently dropped from the report either (rendered in a
    collapsed section instead).

    Known limitation (accepted for v1, see docs/RUN_VISIBILITY_DESIGN.md):
    load_log() reads only the active captain's-log file; rotation keeps
    roughly a 1000-entry tail, so an extremely long/busy run could lose its
    earliest markers.
    """
    if not loop_id:
        return [], []
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
"""

_DETAIL_JS = """
function escHtml(s){ const d=document.createElement('div'); d.innerText=s==null?'':String(s); return d.innerHTML; }
function renderCallRecord(data){
  var out = '';
  out += '<h4>Prompt</h4><pre>' + escHtml(data.prompt || '') + '</pre>';
  out += '<h4>Response</h4><pre>' + escHtml(data.response || '') + '</pre>';
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
        if s.call_record:
            rec_path = Path(s.call_record)
            rel = _relpath(rec_path, report_dir) if rec_path.is_absolute() else s.call_record
            data_attr = f' data-call-record="{_esc(rel)}"'
            detail_html = (
                f'<button class="detail-toggle" type="button">detail</button>'
                f'<a class="raw-link" href="{_esc(rel)}" target="_blank">raw &#8599;</a>'
                f'<div class="detail-panel"></div>'
            )
        else:
            detail_html = '<span class="meta">no call record</span>'

        rows.append(
            f'<tr class="{_STATUS_CLASS.get(status, "")}"{data_attr}>'
            f'<td>{pos}</td>'
            f'<td>{icon}</td>'
            f'<td class="step-text">{_esc_truncated(s.text, 200)}{tag_html}</td>'
            f'<td>{s.elapsed_ms}ms</td>'
            f'<td>{_fmt_tokens_total(s.tokens_in, s.tokens_out)}</td>'
            f'<td>{cost_str}</td>'
            f'<td>{_esc(s.confidence)}</td>'
            f'<td>{detail_html}</td>'
            f'</tr>'
        )
    return (
        '<table class="steps"><thead><tr>'
        '<th>#</th><th></th><th>Step</th><th>Elapsed</th><th>Tokens</th><th>Cost</th><th>Confidence</th><th>Detail</th>'
        '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
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


def _render_global_context(entries: List[dict]) -> str:
    if not entries:
        return ""
    rows = []
    for e in entries:
        rows.append(
            f'<tr><td class="meta">{_esc(_fmt_ts(e.get("timestamp", "")))}</td>'
            f'<td>{_esc(e.get("event_type", ""))}</td>'
            f'<td>{_esc(e.get("subject", ""))}</td>'
            f'<td class="meta">{_esc((e.get("summary", "") or "")[:160])}</td></tr>'
        )
    return (
        '<details class="global-ctx"><summary>Global context — not attributed to this run '
        f'({len(entries)})</summary><table class="idx-table">'
        '<tr><th>Time</th><th>Type</th><th>Subject</th><th>Summary</th></tr>'
        + "".join(rows) + '</table></details>'
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
) -> str:
    windows, approx = _step_windows(step_outcomes, start_ts)
    attributed_markers, global_entries = _gather_log_markers(loop_id, start_ts)
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

    body = f"""{sentinel}
<!DOCTYPE html>
<html><head><meta charset="utf-8">{refresh_html}<title>Run {_esc(loop_id)}</title>
<style>{_CSS}</style></head>
<body>
<h1>Run <code>{_esc(loop_id)}</code></h1>
<div class="meta"><b>Project:</b> {_esc(project or "(none)")} &middot; <b>Goal:</b> {_esc_truncated(goal, 160)}</div>
<div class="meta"><b><span title="Process status only — steps finished/blocked, not whether the goal was verified achieved. See the cross-run index for that once curation runs.">Status:</span></b> {_esc(status)} &middot; <b>Progress:</b> {done}/{total_planned} done, {blocked} blocked{replan_html}</div>
<div class="meta"><b>Started:</b> {_esc(_fmt_ts(start_ts))} &middot; <b>Elapsed:</b> {elapsed_ms}ms &middot; <b>Tokens:</b> {_fmt_tokens_split(total_tokens_in, total_tokens_out)} &middot; <b><span title="Estimated from this report's step token counts — may differ from the run's recorded actual spend shown in the cross-run index.">Cost (est.):</span></b> {cost_str}</div>

<h2>Timeline</h2>
<div class="panel">{_render_timeline(planned_steps, step_outcomes, windows, approx, marker_slots, status)}</div>

<h2>Steps</h2>
<div class="panel">{_render_step_table(project, step_outcomes, report_dir)}</div>

{_render_decision_points(attributed_markers)}
{_render_global_context(global_entries)}
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


def _render_index_html(summaries: List[dict]) -> str:
    rows = []
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
                links.append(
                    f'<a href="{_esc(href)}">{_esc(Path(r).stem.replace("loop-", "").replace("-report", ""))}</a>'
                )
            links_html = " ".join(links)
        else:
            links_html = '<span class="meta">no report</span>'
        row_attr = f' data-href="{_esc(primary_href)}"' if primary_href else ""
        rows.append(
            f'<tr{row_attr}>'
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
<table class="idx-table">
<tr><th>Started</th><th>Status</th><th>Goal</th><th title="{_esc(_LANE_HELP)}">Lane</th><th>Elapsed</th><th>Tokens</th><th>Cost</th><th>Report</th></tr>
{body_rows}
</table>
<script>{_INDEX_ROW_JS}</script>
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
