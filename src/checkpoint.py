# @lat: [[checkpointing]]
"""Session checkpoint — write per-step progress so loops can resume mid-run.

Addresses research GAP 3: no mechanism to resume a prior loop from where it
stopped. Long-running goals can be resumed from the last completed step
instead of restarting from scratch.

Checkpoint format (JSON per file):
    {
        "loop_id": "abc12345",
        "goal": "...",
        "project": "...",
        "steps": ["step 1", "step 2", ...],
        "completed": [{"index": 1, "text": "...", "status": "done", "result": "..."}],
        "timestamp": "2026-04-01T12:00:00Z"
    }

Usage:
    from checkpoint import write_checkpoint, load_checkpoint, resume_from

    # After each step:
    write_checkpoint(loop_id, goal, project, all_steps, step_outcomes_so_far)

    # On resume:
    ckpt = load_checkpoint(loop_id)
    if ckpt:
        steps, completed = resume_from(ckpt)
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("maro.checkpoint")

_CHECKPOINT_DIR_NAME = "checkpoints"


def _checkpoint_dir() -> Path:
    """Non-run-dir checkpoint storage (write-fallback when no run is active).

    Workspace-rooted since 2026-08-06 (census item 5b), honoring the
    MARO_ORCH_ROOT-only pin like every other data root (orch_items.
    data_root). The old orch_root() anchoring wrote checkpoints into
    whatever orch_root resolved to — the repo checkout in production (52
    stale files by 2026-07-09). Pre-move files stay readable via
    _old_checkpoint_dirs().
    """
    try:
        from orch_items import data_root
        d = data_root() / _CHECKPOINT_DIR_NAME
    except Exception:
        d = Path(__file__).parent.parent / _CHECKPOINT_DIR_NAME
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass  # read-only fs: reads can still resolve; writes fail loudly later
    return d


def _old_checkpoint_dirs() -> List[Path]:
    """Pre-2026-08-06 location(s) — fallback for existing files, never created.

    Old data lives wherever orch_root() resolved when it was written — the
    repo checkout for unpinned production (still found unpinned, since
    orch_root() resolves there), the orch layout for legacy-pinned
    deployments. Deliberately env-relative: a PINNED env does not see
    files written unpinned — each workspace owns its data, and piercing
    the pin would leak the real repo's checkpoints into every isolated
    test env (2026-08-06 adversarial review, accepted residual). Files
    found here are consumed/deleted in place (retention decree: never
    migrated), but these dirs are never mkdir'd.
    """
    try:
        from orch_items import orch_root
        candidates = [orch_root() / _CHECKPOINT_DIR_NAME]
    except Exception:
        return []
    try:
        current = _checkpoint_dir()
    except Exception:
        current = None
    out: List[Path] = []
    for d in candidates:
        if d != current and d not in out and d.is_dir():
            out.append(d)
    return out


def _checkpoint_path(loop_id: str) -> Path:
    return _checkpoint_dir() / f"ckpt_{loop_id}.json"


def _find_checkpoint_path(loop_id: str) -> Optional[Path]:
    """Existing checkpoint file for loop_id: current dir first, then old."""
    p = _checkpoint_path(loop_id)
    if p.exists():
        return p
    for old in _old_checkpoint_dirs():
        op = old / f"ckpt_{loop_id}.json"
        if op.exists():
            return op
    return None


def _rundir_checkpoint_path() -> Optional[Path]:
    """Run-dir checkpoint location (`<run-dir>/build/checkpoint.json`).

    None when no run is active (tests, direct agent_loop invocations) —
    callers fall back to `_checkpoint_dir()`. The run dir is the durable,
    env-independent home: the pre-move `orch_root()/checkpoints` resolved
    differently per environment, which is how 52 stale checkpoints ended up
    in the repo and 0 in the live workspace.
    """
    try:
        from runs import current_run_dir
        rd = current_run_dir()
        if rd is not None:
            return Path(rd) / "build" / "checkpoint.json"
    except Exception:
        pass
    return None


def _run_handle_id(build_ckpt_path: Path) -> str:
    """handle_id from the run dir's metadata.json, best-effort."""
    try:
        meta = build_ckpt_path.parent.parent / "metadata.json"
        return str(json.loads(meta.read_text(encoding="utf-8")).get("handle_id", ""))
    except Exception:
        return ""


def _runs_root() -> Optional[Path]:
    try:
        from runs import runs_root
        return runs_root()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


# Row statuses that FINISH a plan position (a resume skips the step). Every
# other status — blocked (retry-requeued or superseded by sub-steps), stuck,
# failed — leaves the step to be re-attempted on resume.
_FINISHED_STATUSES = frozenset({"done", "skipped"})


def _as_int(value: Any, default: int) -> int:
    """Integer identity or `default` — never a guess.

    Accepts ints and integral floats/strings ("13", 13.0). Refuses bools,
    None, non-integral or non-finite numbers and anything else: a
    position of 1.9 must not become position 1 (review r2 finding 4).
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else default
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _coerce_row(c: Dict[str, Any], n_steps: int) -> Optional["CompletedStep"]:
    """Build a CompletedStep from a persisted dict, tolerating hand edits
    and older shapes: `index` defaults to -1, `position` to 0, and a
    position outside 1..n_steps reads as 0 (not a plan step). A row that
    still cannot be built is dropped rather than crashing the load — the
    resume path treats a load error as "start fresh", which re-executes
    everything (review round 1, finding 6)."""
    known = {f.name for f in fields(CompletedStep)}
    row = {k: v for k, v in c.items() if k in known}
    row["index"] = _as_int(row.get("index"), -1)
    pos = _as_int(row.get("position"), 0)
    row["position"] = pos if 0 < pos <= n_steps else 0
    row["text"] = str(row.get("text", "") or "")
    row["status"] = str(row.get("status", "") or "")
    try:
        return CompletedStep(**row)
    except TypeError:
        return None


@dataclass
class CompletedStep:
    index: int             # NEXT.md item index the loop assigned (-1 for recovery sub-steps)
    text: str
    status: str
    result: str = ""
    # 1-based position in `Checkpoint.steps` (0 = not a plan step: a
    # recovery sub-step, or a row carried in from an earlier attempt whose
    # plan this checkpoint no longer holds). Added 2026-09-16: `index` is
    # the NEXT.md item number, never a plan position — live checkpoints
    # held [13, 49, 11, 12] for 2–7-step plans, so `remaining_steps`
    # (which compared it to 1..n) returned the WHOLE plan and a resume
    # re-executed finished steps. Resume selects by this field.
    position: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    elapsed_ms: int = 0
    provider_cost_usd: float = 0.0
    executor_session_id: str = ""
    executor_session_resumed: bool = False


@dataclass
class Checkpoint:
    loop_id: str
    goal: str
    project: str
    steps: List[str]
    completed: List[CompletedStep]
    timestamp: str = ""
    parent_loop_id: str = ""  # Set when this checkpoint is a branch of another
    handle_id: str = ""  # run-dir linkage (2026-07-09 substrate fix)
    # Written BEFORE a step executes: {"index": int, "started_at": iso, "pid": int}.
    # Present on load ⇒ that step may have partial side effects (crashed
    # mid-step); absent ⇒ the next step never started. Cleared by the
    # post-step checkpoint write.
    in_flight: Optional[Dict[str, Any]] = None
    # Opt-in Claude executor conversation for a clean between-step resume.
    # Discarded whenever in_flight is present; adapter re-validates its config
    # signature (model/cwd/tools/permissions) before using the ID.
    executor_session: Optional[Dict[str, Any]] = None
    # A successful resume marks (rather than deletes) its source so the
    # retained checkpoint remains inspectable but can never replay effects.
    consumed_at: str = ""
    resumed_to_loop_id: str = ""
    # Run-scoped world-fact ledger rows (WORLD_FACTS_DESIGN slice 1) — a
    # resume must see the facts, not just the surviving steps.
    world_facts: Optional[List[Dict[str, Any]]] = None
    # Regression obligations (regression_ledger rows) — a resume must keep
    # re-verifying what the pre-pause steps proved.
    regression: Optional[List[Dict[str, Any]]] = None
    # Provenance of the rows' `position` field (2026-09-16 review, round 1):
    # True ⇒ the writer mapped rows to plan positions and a position of 0
    # means "not a plan step"; False ⇒ an older file whose `index` is read
    # as a position (pre-fix semantics). Checkpoint-level on purpose — a
    # positioned file whose rows ALL sit at 0 (a resume's in-flight write
    # before its first suffix step completes: every row is carried history)
    # must not fall back to reading stale item numbers as suffix positions.
    positioned: bool = False

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def _done_positions(self) -> set:
        """1-based plan positions a resume must NOT re-execute.

        Positioned file (`positioned` is True — written by this build): a
        position counts only when its LATEST row ended in a FINISHED
        status (`_FINISHED_STATUSES`; latest-row-wins matches export_human).
        A blocked row never counts, whatever produced it (review r1
        finding 3, r2 finding 1):
          - retry: the loop requeued the step — resume must start AT it;
          - superseded by sub-steps: the sub-steps are not plan steps
            (position 0), so a crash mid-way would otherwise lose them —
            the step re-decomposes on resume (wasteful, never lost);
          - prerequisite gate: a resume is the operator's retry of the
            failed prerequisite, and the dependent must be re-decided
            against the fresh outcome, not frozen by the old refusal.
        The direction of error is deliberate — re-running a blocked step is
        a retry, re-running a done step is the duplicate-effects bug.

        Legacy file (`positioned` False): every row's `index` is read as a
        position — the pre-fix behaviour, kept so an old file resumes
        exactly as it did rather than as nothing-done.
        """
        if self.positioned:
            n = len(self.steps)
            latest: Dict[int, str] = {}   # position → status of its LATEST row
            for s in self.completed:
                pos = _as_int(getattr(s, "position", 0), 0)
                if 0 < pos <= n:
                    latest[pos] = s.status
            return {pos for pos, st in latest.items() if st in _FINISHED_STATUSES}
        return {s.index for s in self.completed}

    @property
    def next_step_index(self) -> int:
        """Zero-based index of the next step to execute.

        Positioned: the first remaining position (len(steps) when nothing
        remains). Legacy: max recorded index, as before.
        """
        done = self._done_positions()
        if self.positioned:
            for i in range(1, len(self.steps) + 1):
                if i not in done:
                    return i - 1
            return len(self.steps)
        return max(done) if done else 0

    @property
    def remaining_steps(self) -> List[str]:
        """Steps not yet completed (by plan position)."""
        done = self._done_positions()
        return [s for i, s in enumerate(self.steps, 1) if i not in done]

    @property
    def done_count(self) -> int:
        """Plan steps with a finished outcome — for progress displays.

        Positioned: unique finished positions (carried-in history rows and
        sub-steps sit at position 0 and do not count). Legacy: rows whose
        status is done, as export_human always counted.
        """
        if self.positioned:
            return len(self._done_positions())
        return sum(1 for s in self.completed if s.status == "done")

    def is_complete(self) -> bool:
        """True if nothing remains to execute.

        Positioned: every plan position has a finished row. Legacy: row
        count reaches the plan length (pre-fix rule, kept for old files).
        After a resume `steps` is the SUFFIX and `completed` also holds the
        carried-in rows, so a row count over-reports (review round 1,
        finding 1: a second resume was refused as "completed all its
        steps" while suffix work remained).
        """
        if self.positioned:
            return not self.remaining_steps
        return len(self.completed) >= len(self.steps)

    def is_consumed(self) -> bool:
        return bool(self.consumed_at)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "loop_id": self.loop_id,
            "goal": self.goal,
            "project": self.project,
            "steps": self.steps,
            "completed": [asdict(c) for c in self.completed],
            "timestamp": self.timestamp,
        }
        if self.parent_loop_id:
            d["parent_loop_id"] = self.parent_loop_id
        if self.handle_id:
            d["handle_id"] = self.handle_id
        if self.in_flight:
            d["in_flight"] = self.in_flight
        if self.executor_session:
            d["executor_session"] = self.executor_session
        if self.consumed_at:
            d["consumed_at"] = self.consumed_at
            d["resumed_to_loop_id"] = self.resumed_to_loop_id
        if self.world_facts:
            d["world_facts"] = self.world_facts
        if self.regression:
            d["regression"] = self.regression
        if self.positioned:
            d["positioned"] = True
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Checkpoint":
        steps = d.get("steps", [])
        if not isinstance(steps, list):
            steps = []
        raw_rows = d.get("completed", [])
        if not isinstance(raw_rows, list):
            raw_rows = []
        # Only dict rows are rows; anything else is a hand edit or a torn
        # write and must not count toward "done" (r2 finding 4).
        completed = [_coerce_row(c, len(steps)) for c in raw_rows if isinstance(c, dict)]
        completed = [c for c in completed if c is not None]
        raw_session = d.get("executor_session")
        executor_session = None
        if isinstance(raw_session, dict):
            session_id = raw_session.get("session_id")
            signature = raw_session.get("signature")
            turns = raw_session.get("turns", 0)
            if (isinstance(session_id, str)
                    and re.fullmatch(
                        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
                        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}", session_id)
                    and isinstance(signature, str)
                    and re.fullmatch(r"[0-9a-f]{64}", signature)
                    and isinstance(turns, int) and 0 <= turns <= 1000):
                executor_session = {
                    "session_id": session_id,
                    "signature": signature,
                    "turns": turns,
                }
        return cls(
            loop_id=d["loop_id"],
            goal=d["goal"],
            project=d.get("project", ""),
            steps=steps,
            completed=completed,
            # The marker is a JSON boolean or nothing — a hand-edited
            # "false" string must read as legacy, not as positioned.
            positioned=d.get("positioned") is True,
            timestamp=d.get("timestamp", ""),
            parent_loop_id=d.get("parent_loop_id", ""),
            handle_id=d.get("handle_id", ""),
            in_flight=d.get("in_flight") or None,
            executor_session=executor_session,
            consumed_at=str(d.get("consumed_at") or ""),
            resumed_to_loop_id=str(d.get("resumed_to_loop_id") or ""),
            world_facts=d.get("world_facts") or None,
            regression=d.get("regression") or None,
        )


# ---------------------------------------------------------------------------
# Core API
# ---------------------------------------------------------------------------


def write_checkpoint(
    loop_id: str,
    goal: str,
    project: str,
    steps: List[str],
    step_outcomes: List[Any],  # List[StepOutcome] — avoid circular import
    *,
    in_flight_index: Optional[int] = None,
    executor_session: Optional[Dict[str, Any]] = None,
    world_facts: Optional[List[Dict[str, Any]]] = None,
    regression: Optional[List[Dict[str, Any]]] = None,
    step_indices: Optional[List[int]] = None,
) -> None:
    """Write current loop progress to disk.

    Safe to call after every step — overwrites previous checkpoint for the
    same loop_id. Swallows all errors: a failed checkpoint write must never
    abort a running loop.

    Lands in `<run-dir>/build/checkpoint.json` when a run is active (durable,
    linked to handle_id, survives crashes in the place the operator already
    looks); falls back to the non-run-dir checkpoints dir otherwise.

    Args:
        loop_id: Unique ID for this loop run.
        goal: Original goal text.
        project: Project slug.
        steps: Full list of planned steps (all, including future ones).
        step_outcomes: List of StepOutcome objects completed so far.
        in_flight_index: When set, stamps the step about to execute (call
            BEFORE execution) so a mid-step crash is distinguishable from
            "next step never started". Post-step writes omit it, which
            clears the marker.
        executor_session: Compatible clean between-step Claude session state.
            It is retained beside the checkpoint, never treated as sufficient
            without the adapter's configuration-signature check.
    """
    try:
        # item index → 1-based plan position, from the loop's own mapping
        # (`step_indices[i]` is the NEXT.md item of plan step i+1). A row
        # whose item is not in the mapping (recovery sub-step, or a row
        # carried in from an earlier attempt) gets position 0.
        _pos_of_item: Dict[int, int] = {}
        _dup_items: List[int] = []
        for _pos, _item in enumerate(step_indices or (), 1):
            _item_i = _as_int(_item, -1)
            if _item_i < 0:
                continue
            if _item_i in _pos_of_item:
                _dup_items.append(_item_i)
                continue
            _pos_of_item[_item_i] = _pos
        for _item_i in _dup_items:
            # An item that names two positions is an ambiguous identity:
            # its rows get position 0 (= re-run), never the first slot
            # (r2 finding 3 — the first slot would skip work that never ran).
            _pos_of_item.pop(_item_i, None)
        if step_indices is not None and (len(step_indices) != len(steps) or _dup_items):
            # Rows the mapping cannot place resolve to position 0 (= not
            # done) — the safe direction — but a malformed mapping means
            # the writer's plan and item lists drifted apart; say so.
            log.warning(
                "checkpoint %s: step_indices does not map the plan cleanly "
                "(%d indices for %d steps, duplicate items %s) — rows of "
                "unmapped or duplicated items will be re-executed on resume",
                loop_id, len(step_indices), len(steps), _dup_items or "none",
            )
        completed = [
            CompletedStep(
                index=_as_int(getattr(s, "index", i + 1), -1),
                position=_pos_of_item.get(_as_int(getattr(s, "index", -1), -1), 0),
                text=getattr(s, "text", ""),
                status=getattr(s, "status", ""),
                result=getattr(s, "result", ""),
                tokens_in=getattr(s, "tokens_in", 0),
                tokens_out=getattr(s, "tokens_out", 0),
                elapsed_ms=getattr(s, "elapsed_ms", 0),
                provider_cost_usd=float(getattr(s, "provider_cost_usd", 0.0) or 0.0),
                executor_session_id=str(getattr(s, "executor_session_id", "") or ""),
                executor_session_resumed=bool(
                    getattr(s, "executor_session_resumed", False)),
            )
            for i, s in enumerate(step_outcomes)
        ]
        in_flight = None
        if in_flight_index is not None:
            in_flight = {
                "index": in_flight_index,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "pid": os.getpid(),
            }
        rd_path = _rundir_checkpoint_path()
        ckpt = Checkpoint(
            loop_id=loop_id,
            goal=goal,
            project=project,
            steps=steps,
            completed=completed,
            positioned=step_indices is not None,
            handle_id=_run_handle_id(rd_path) if rd_path else "",
            in_flight=in_flight,
            # A mid-step crash has indeterminate provider state and may have
            # partial side effects. Do not persist a resumable capability in
            # an in-flight checkpoint; readers then cannot accidentally use it.
            executor_session=(dict(executor_session or {}) or None)
            if in_flight is None else None,
            world_facts=list(world_facts) if world_facts else None,
            regression=list(regression) if regression else None,
        )
        if rd_path is not None:
            rd_path.parent.mkdir(parents=True, exist_ok=True)
            path = rd_path
        else:
            path = _checkpoint_path(loop_id)
        path.write_text(json.dumps(ckpt.to_dict(), indent=2), encoding="utf-8")
        log.debug("checkpoint written: %s (%d/%d steps done, %d rows)",
                  loop_id, ckpt.done_count, len(steps), len(completed))
    except Exception as exc:
        log.debug("checkpoint write failed (non-fatal): %s", exc)


def _load_from(path: Path, loop_id: Optional[str] = None) -> Optional[Checkpoint]:
    """Parse one checkpoint file; None on missing/corrupt/loop_id mismatch."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ckpt = Checkpoint.from_dict(data)
        if loop_id is not None and ckpt.loop_id != loop_id:
            return None
        return ckpt
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("checkpoint load failed for %s: %s", path, exc)
        return None


def load_checkpoint(loop_id: str) -> Optional[Checkpoint]:
    """Load a checkpoint by loop_id. Returns None if not found or corrupt.

    Search order: the active run dir (if any), then the non-run-dir
    checkpoint dir (current location first, then pre-move locations), then
    a newest-first scan of all run dirs (resume usually happens in a fresh
    process where no run-dir contextvar is set).
    """
    rd_path = _rundir_checkpoint_path()
    if rd_path is not None:
        ckpt = _load_from(rd_path, loop_id)
        if ckpt is not None:
            return ckpt

    found = _find_checkpoint_path(loop_id)
    if found is not None:
        ckpt = _load_from(found)
        if ckpt is not None:
            return ckpt

    root = _runs_root()
    if root is not None and root.is_dir():
        try:
            candidates = sorted(
                root.glob("*/build/checkpoint.json"),
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
        except Exception:
            candidates = []
        for p in candidates:
            ckpt = _load_from(p, loop_id)
            if ckpt is not None:
                return ckpt
    return None


def mark_checkpoint_consumed(loop_id: str, *, resumed_to_loop_id: str) -> bool:
    """Retain but irrevocably consume a successful resume source checkpoint.

    This is intentionally narrower than deletion: closure-demoted/stuck runs
    keep resumable state, while a successfully-resumed checkpoint cannot be
    invoked a second time to replay the same external effects.
    """
    path = _find_checkpoint_path(loop_id) or _checkpoint_path(loop_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if str(data.get("loop_id") or "") != loop_id:
            return False
        data["consumed_at"] = datetime.now(timezone.utc).isoformat()
        data["resumed_to_loop_id"] = resumed_to_loop_id
        from file_lock import atomic_write
        atomic_write(path, json.dumps(data, indent=2))
        return True
    except Exception as exc:
        log.error("checkpoint consume failed for %s: %s", loop_id, exc)
        return False


def delete_checkpoint(loop_id: str) -> None:
    """Delete a checkpoint file.

    User-level only (the `checkpoint delete` CLI). No automatic path calls
    this: completed loops keep their checkpoint (retention decree,
    2026-07-10 — closure verdicts land after finalize, and a demoted run
    needs its resume state), and crashed/stuck loops keep theirs by design
    (that's the resume substrate).
    """
    try:
        rd_path = _rundir_checkpoint_path()
        if rd_path is not None and rd_path.exists():
            if _load_from(rd_path, loop_id) is not None:
                rd_path.unlink(missing_ok=True)
        _checkpoint_path(loop_id).unlink(missing_ok=True)
        for old in _old_checkpoint_dirs():
            (old / f"ckpt_{loop_id}.json").unlink(missing_ok=True)
        log.debug("checkpoint deleted: %s", loop_id)
    except Exception:
        pass


def list_checkpoints() -> List[Checkpoint]:
    """List all saved checkpoints (checkpoint dirs + run dirs), newest first."""
    entries: List[tuple] = []
    try:
        for p in _checkpoint_dir().glob("ckpt_*.json"):
            entries.append(p)
    except Exception:
        pass
    for old in _old_checkpoint_dirs():
        try:
            entries.extend(old.glob("ckpt_*.json"))
        except Exception:
            pass
    root = _runs_root()
    if root is not None and root.is_dir():
        try:
            entries.extend(root.glob("*/build/checkpoint.json"))
        except Exception:
            pass
    ckpts = []
    seen_ids: set = set()
    for p in sorted(entries, key=lambda x: x.stat().st_mtime, reverse=True):
        ckpt = _load_from(p)
        if ckpt is None:
            continue
        # A loop_id can exist at both the current and a pre-move location
        # (e.g. an operator copied old files forward) — newest copy wins,
        # matching _find_checkpoint_path's current-dir-first preference.
        if ckpt.loop_id in seen_ids:
            continue
        seen_ids.add(ckpt.loop_id)
        ckpts.append(ckpt)
    return ckpts


def resume_from(ckpt: Checkpoint) -> tuple[List[str], List[CompletedStep]]:
    """Extract resumable state from a checkpoint.

    Returns:
        (remaining_steps, already_completed) — caller skips completed steps
        and resumes execution from remaining_steps.
    """
    return ckpt.remaining_steps, ckpt.completed


def export_human(loop_id: str) -> Optional[str]:
    """Render a checkpoint as human-readable markdown.

    Returns the markdown string, or None if the checkpoint is not found.

    The output is suitable for reading in a terminal, saving as a .md file,
    or injecting into a follow-up mission as context.
    """
    ckpt = load_checkpoint(loop_id)
    if ckpt is None:
        return None

    done_count = ckpt.done_count
    blocked_count = sum(1 for s in ckpt.completed if s.status == "blocked")
    total = len(ckpt.steps)

    status_parts = [f"{done_count}/{total} steps done"]
    if blocked_count:
        status_parts.append(f"{blocked_count} blocked")
    if ckpt.parent_loop_id:
        status_parts.append(f"branched from {ckpt.parent_loop_id}")

    lines = [
        f"# Mission: {ckpt.goal}",
        "",
        f"**Loop ID:** `{ckpt.loop_id}`  ",
        f"**Project:** {ckpt.project or '(none)'}  ",
        f"**Timestamp:** {ckpt.timestamp[:19].replace('T', ' ')}  ",
        f"**Progress:** {', '.join(status_parts)}",
        "",
        "---",
        "",
        "## Steps",
        "",
    ]

    # Completed steps by their 1-based plan position (older files: index);
    # a positioned file's history rows (position 0) never claim a slot.
    completed_by_index: Dict[int, CompletedStep] = {}
    for s in ckpt.completed:
        _key = s.position if ckpt.positioned else s.index
        if _key > 0:
            completed_by_index[_key] = s

    for i, step_text in enumerate(ckpt.steps, 1):
        cs = completed_by_index.get(i)
        if cs is not None:
            icon = "✓" if cs.status == "done" else "✗"
            status_label = cs.status
        else:
            icon = "·"
            status_label = "pending"

        lines.append(f"### Step {i} · {step_text}")
        lines.append(f"**Status:** {icon} {status_label}")
        if cs is not None and cs.result:
            lines.append("")
            # Truncate very long results for readability
            result_text = cs.result
            if len(result_text) > 800:
                result_text = result_text[:800] + "\n…[truncated]"
            lines.append(result_text)
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def branch_checkpoint(loop_id: str) -> Optional[str]:
    """Create a branch of an existing checkpoint with a new loop_id.

    The branch is an independent copy starting from the same completed steps.
    The original checkpoint is unchanged. The branch records its origin via
    parent_loop_id for traceability.

    Use this to explore an alternative approach from a mid-mission state without
    affecting the main session.

    Returns:
        New loop_id string, or None if the source checkpoint is not found.
    """
    ckpt = load_checkpoint(loop_id)
    if ckpt is None:
        log.warning("branch_checkpoint: no checkpoint found for %s", loop_id)
        return None

    new_loop_id = uuid.uuid4().hex[:8]
    branch = Checkpoint(
        loop_id=new_loop_id,
        goal=ckpt.goal,
        project=ckpt.project,
        steps=list(ckpt.steps),
        completed=list(ckpt.completed),
        parent_loop_id=loop_id,
        world_facts=list(ckpt.world_facts) if ckpt.world_facts else None,
        regression=list(ckpt.regression) if ckpt.regression else None,
        # The rows travel with their position semantics — a branch of a
        # positioned file read as legacy would take item numbers for
        # positions (the very bug `positioned` exists to name).
        positioned=ckpt.positioned,
    )
    path = _checkpoint_path(new_loop_id)
    path.write_text(json.dumps(branch.to_dict(), indent=2), encoding="utf-8")
    log.info("branch_checkpoint: %s -> %s (%d/%d steps done carried over, %d rows)",
             loop_id, new_loop_id, branch.done_count, len(branch.steps), len(branch.completed))
    return new_loop_id


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Maro checkpoint manager")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("list", help="List all saved checkpoints")

    load_p = sub.add_parser("show", help="Show checkpoint details (raw JSON)")
    load_p.add_argument("loop_id", help="Loop ID to show")

    exp_p = sub.add_parser("export", help="Export a checkpoint as human-readable markdown")
    exp_p.add_argument("loop_id", help="Loop ID to export")
    exp_p.add_argument("-o", "--output", help="Write to file instead of stdout")

    branch_p = sub.add_parser("branch", help="Branch a checkpoint with a new loop_id")
    branch_p.add_argument("loop_id", help="Loop ID to branch from")

    del_p = sub.add_parser("delete", help="Delete a checkpoint")
    del_p.add_argument("loop_id", help="Loop ID to delete")

    args = parser.parse_args()

    if args.cmd == "list":
        ckpts = list_checkpoints()
        if not ckpts:
            print("No checkpoints found.")
            return
        for c in ckpts:
            done = c.done_count
            total = len(c.steps)
            branch_tag = f"  [branch of {c.parent_loop_id}]" if c.parent_loop_id else ""
            print(f"{c.loop_id}  {done}/{total}  {c.timestamp[:19]}  {c.goal[:55]}{branch_tag}")
    elif args.cmd == "show":
        c = load_checkpoint(args.loop_id)
        if c is None:
            print(f"No checkpoint found for {args.loop_id}")
            return
        print(json.dumps(c.to_dict(), indent=2))
    elif args.cmd == "export":
        md = export_human(args.loop_id)
        if md is None:
            print(f"No checkpoint found for {args.loop_id}")
            return
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"Exported to {args.output}")
        else:
            print(md)
    elif args.cmd == "branch":
        new_id = branch_checkpoint(args.loop_id)
        if new_id is None:
            print(f"No checkpoint found for {args.loop_id}")
            return
        print(f"Branch created: {new_id}  (parent: {args.loop_id})")
        print(f"Resume with: maro-run --resume {new_id}")
    elif args.cmd == "delete":
        delete_checkpoint(args.loop_id)
        print(f"Deleted checkpoint {args.loop_id}")
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli_main()
