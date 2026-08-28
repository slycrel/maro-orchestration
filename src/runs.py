"""Per-run isolation: nickname + run-dir destination.

Each `handle()` invocation gets a run-dir at
`~/.maro/workspace/runs/<handle_id>-<nickname>/` that is the destination
for run-specific writes (prompt, scope, resolved_intent, scratchpad,
PARTIAL files, step outputs, captain's log slice, repo bundle).

Design principle (Jeremy, 2026-04-26): writes go to the run-dir from
the start. No end-of-run gather/copy phase — the run-dir is the
destination, not a capture target. We're already doing the work; this
is organization, not new instrumentation.

The nickname is a deterministic 2-word label derived from handle_id so
runs can be referenced in conversation without copy-pasting UUIDs.
~50 adjectives × ~50 nouns ≈ 2500 combinations — unique-enough for
local use, memorable, greppable.

This module owns the run lifecycle, current-run routing, and durable reference
index used to resolve handle/loop/resume IDs without scanning every run.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("maro.runs")
from pathlib import Path
from typing import Optional

from ancestry import Origin
from context_budget import clip, VERDICT_PROSE_CAP


_ADJECTIVES = (
    "amber", "azure", "brisk", "calm", "clever", "cobalt", "crisp",
    "dapper", "dusky", "eager", "fierce", "frosty", "gentle", "gilded",
    "glassy", "golden", "hardy", "humble", "icy", "jaunty", "keen",
    "lively", "lucid", "merry", "misty", "noble", "nimble", "olive",
    "patient", "plucky", "quick", "quiet", "rapid", "ruby", "rustic",
    "silent", "silver", "sleek", "spry", "stout", "sturdy", "sunny",
    "swift", "tawny", "tidy", "vivid", "warm", "wily", "witty", "zesty",
)

_NOUNS = (
    "alder", "ash", "badger", "beacon", "birch", "brook", "cedar",
    "comet", "crane", "delta", "echo", "ember", "falcon", "ferret",
    "finch", "forge", "glen", "harbor", "haven", "heron", "ibis",
    "jasper", "kestrel", "lantern", "ledger", "lichen", "magpie",
    "marsh", "meadow", "moss", "nettle", "oak", "orchard", "otter",
    "pebble", "pine", "quartz", "raven", "ridge", "river", "saffron",
    "shore", "spruce", "thicket", "thorn", "tundra", "vale", "wren",
    "yarrow", "zephyr",
)


def nickname(handle_id: str) -> str:
    """Deterministic 2-word nickname from handle_id.

    Same handle_id always yields the same nickname. Uses sha1 to spread
    across the adjective/noun space evenly regardless of handle_id
    distribution.
    """
    if not handle_id:
        return "unset-run"
    digest = hashlib.sha1(handle_id.encode("utf-8")).digest()
    adj_idx = digest[0] % len(_ADJECTIVES)
    noun_idx = digest[1] % len(_NOUNS)
    return f"{_ADJECTIVES[adj_idx]}-{_NOUNS[noun_idx]}"


def runs_root() -> Path:
    """Workspace runs/ directory. Honors MARO_WORKSPACE for tests."""
    ws = os.environ.get("MARO_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
    if ws:
        return Path(ws) / "runs"
    return Path.home() / ".maro" / "workspace" / "runs"


def run_dir(handle_id: str) -> Path:
    """Path of the run-dir for a given handle_id (does not create it)."""
    return runs_root() / f"{handle_id}-{nickname(handle_id)}"


# v2 (2026-07-29): refs include the plural loops-ledger keys (loop_ids +
# loops[].loop_id). The loops ledger stopped stamping the singular
# metadata.loop_id, so every v1-indexed run was unreachable by loop_id —
# which silently no-op'd every loop_id-keyed consumer at the verdict seam
# (contradiction candidates, skill attribution). The version bump forces
# one full re-migration on first miss; the v1 dir is an orphaned cache,
# left in place.
_RUN_INDEX_DIR = ".run-ref-index-v2"
_RUN_INDEX_MARKER = ".migrated"


def _index_dir(root: Optional[Path] = None) -> Path:
    runs = root or runs_root()
    return runs.parent / _RUN_INDEX_DIR


def _index_entry_path(ref: str, root: Optional[Path] = None) -> Path:
    digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()
    return _index_dir(root) / f"{digest}.json"


def _metadata_refs(meta: dict) -> set:
    refs = {str(meta.get("handle_id") or ""), str(meta.get("loop_id") or "")}
    # Loops-ledger keys: a run dir hosts several loops (initial + closure
    # restarts/continuations), recorded as metadata.loop_ids (handle.py)
    # and the metadata.loops lineage ledger. Each loop id must be a durable
    # index key — the outcomes store and the verdict seam join by loop_id.
    loop_ids = meta.get("loop_ids")
    if isinstance(loop_ids, list):
        refs.update(str(lid or "") for lid in loop_ids)
    loops = meta.get("loops")
    if isinstance(loops, list):
        refs.update(
            str(e.get("loop_id") or "")
            for e in loops if isinstance(e, dict))
    origin = meta.get("origin") if isinstance(meta.get("origin"), dict) else {}
    refs.add(str(origin.get("resumed_from") or ""))
    return {ref for ref in refs if ref}


def _write_index_entry(ref: str, rd: Path) -> None:
    from file_lock import atomic_write, locked_write
    path = _index_entry_path(ref, rd.parent)
    payload = json.dumps({"ref": ref, "run_dir": rd.name}, sort_keys=True)
    with locked_write(path):
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_name = existing.get("run_dir")
            if (existing.get("ref") == ref
                    and isinstance(existing_name, str)
                    and Path(existing_name).name == existing_name
                    and (rd.parent / existing_name).is_dir()
                    and existing_name <= rd.name):
                # Preserve historical scan semantics for duplicate refs: the
                # alphabetically-first live directory wins.
                return
        except (FileNotFoundError, json.JSONDecodeError, OSError,
                AttributeError, TypeError, ValueError):
            pass
        atomic_write(path, payload)


def invalidate_run_index(root: Optional[Path] = None) -> None:
    """Force one legacy metadata migration on the next indexed lookup."""
    from file_lock import locked_write
    marker = _index_dir(root) / _RUN_INDEX_MARKER
    with locked_write(marker):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass


def index_run_dir(rd: Path, meta: Optional[dict] = None) -> None:
    """Best-effort publication of one run's durable reference mappings."""
    try:
        if meta is None:
            meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
        for ref in _metadata_refs(meta):
            _write_index_entry(ref, rd)
    except Exception:
        # If a published metadata mutation could not be indexed, make the next
        # miss rebuild history rather than silently making that run unreachable.
        try:
            invalidate_run_index(rd.parent)
        except Exception:
            pass


def remove_run_index(rd: Path, meta: Optional[dict] = None) -> None:
    """Remove known mappings for a run being pruned (stale hits also self-heal)."""
    try:
        if meta is None:
            meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    from file_lock import locked_write
    for ref in _metadata_refs(meta):
        path = _index_entry_path(ref, rd.parent)
        try:
            with locked_write(path):
                current = json.loads(path.read_text(encoding="utf-8"))
                if current.get("run_dir") == rd.name:
                    path.unlink()
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue


def _scan_legacy_run_dirs(root: Path):
    """Yield legacy metadata for the one-time index migration."""
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        try:
            meta = json.loads((d / "metadata.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        yield d, meta


def _legacy_run_dir(ref: str, root: Path) -> Optional[Path]:
    for rd, meta in _scan_legacy_run_dirs(root):
        if ref in _metadata_refs(meta):
            return rd
    return None


def _migration_complete(marker: Path) -> bool:
    try:
        state = json.loads(marker.read_text(encoding="utf-8"))
        return bool(state.get("complete", True))
    except Exception:
        return False


def _ensure_run_index(root: Path) -> bool:
    from file_lock import atomic_write, locked_write
    marker = _index_dir(root) / _RUN_INDEX_MARKER
    if marker.is_file():
        return _migration_complete(marker)
    with locked_write(marker):
        if marker.is_file():
            return _migration_complete(marker)
        failed = 0
        for rd, meta in _scan_legacy_run_dirs(root):
            for ref in _metadata_refs(meta):
                try:
                    _write_index_entry(ref, rd)
                except Exception:
                    failed += 1
        complete = failed == 0
        atomic_write(marker, json.dumps({
            "version": 1,
            "complete": complete,
            "failed_entries": failed,
        }))
        return complete


def _indexed_run_dir(ref: str, root: Path) -> Optional[Path]:
    path = _index_entry_path(ref, root)
    if not path.is_file():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        name = entry.get("run_dir")
        if entry.get("ref") != ref or not isinstance(name, str) or Path(name).name != name:
            raise ValueError("invalid run-index entry")
        candidate = root / name
        if candidate.is_dir():
            return candidate
        raise ValueError("indexed run directory is missing")
    except (json.JSONDecodeError, ValueError, OSError, AttributeError, TypeError):
        try:
            path.unlink()
        except OSError:
            pass
        # Repair only this reference. A stale/corrupt leaf must not invalidate
        # the global migration marker and force unrelated misses through a
        # second O(all runs) rebuild.
        legacy = _legacy_run_dir(ref, root)
        if legacy is not None:
            _write_index_entry(ref, legacy)
        return legacy


def resolve_run_dir(ref: str) -> Optional[Path]:
    """Locate a per-run dir by handle_id (its dir-name prefix) or by a
    loop_id / handle_id recorded in metadata.json. Returns the dir or None.

    Handle IDs resolve directly from the deterministic directory name. Other
    references use a hashed, per-reference durable index. The first lookup on
    an older workspace performs one lock-guarded metadata migration; misses
    after that are bounded and never make older resumable runs unreachable.
    """
    if not ref:
        return None
    direct = run_dir(ref)
    if direct.is_dir():
        return direct
    root = runs_root()
    if not root.is_dir():
        return None
    try:
        indexed = _indexed_run_dir(ref, root)
        if indexed is not None:
            return indexed
        complete = _ensure_run_index(root)
        indexed = _indexed_run_dir(ref, root)
        if indexed is not None or complete:
            return indexed
        # A best-effort migration recorded failed entries. Preserve historical
        # reachability without repeating the whole rewrite pass on every call.
        return _legacy_run_dir(ref, root)
    except Exception:
        # Index storage is an optimization, not a new availability dependency.
        # A read-only/corrupt/lock-starved index degrades to the historical
        # scan; healthy steady-state misses remain O(1).
        return _legacy_run_dir(ref, root)


def create_run_dir(
    handle_id: str,
    *,
    prompt: str,
    lane: Optional[str] = None,
    model: Optional[str] = None,
    extra_metadata: Optional[dict] = None,
) -> Path:
    """Create the run-dir and seed metadata.json + prompt.txt.

    Returns the run-dir path. Idempotent: re-calling on the same
    handle_id refreshes metadata.json without clobbering existing
    artifacts (the agent may have already written into source/ /
    build/ / artifact/ subtrees).
    """
    rd = run_dir(handle_id)
    rd.mkdir(parents=True, exist_ok=True)

    # Subtree skeleton — source/build/artifact (Jeremy's compile mental model).
    # Created lazily on first write would also work, but pre-creating
    # makes "where does this go?" obvious to anyone inspecting mid-run.
    (rd / "source").mkdir(exist_ok=True)
    (rd / "build").mkdir(exist_ok=True)
    (rd / "artifact").mkdir(exist_ok=True)

    # prompt.txt is the verbatim user input — don't overwrite if it
    # already exists (the first call wins; subsequent calls are
    # no-ops on this file).
    prompt_path = rd / "source" / "prompt.txt"
    if not prompt_path.exists():
        from file_lock import atomic_write
        atomic_write(prompt_path, prompt)

    origin = (extra_metadata or {}).get("origin")
    origin = origin if isinstance(origin, dict) else None

    # Artifacts-travel rider (docs/DISPATCH_ENVELOPE.md): a run born from a
    # typed dispatch gets the dispatch's attached artifacts copied into its
    # own tree — the container executor can't see the workspace output dir,
    # and a self-contained run tree is the artifacts-over-streams contract.
    # Same rationale as the navigator rationale below: no run dir existed at
    # dispatch time, so the linkage rides origin.
    if origin and origin.get("dispatch_envelope") and origin.get("job_id"):
        try:
            from dispatch_envelope import land_in_run_dir
            land_in_run_dir(rd, str(origin["job_id"]))
        except Exception:
            pass  # fail-soft twice over — the run must start regardless

    # Same rationale for operator-attached files (--attach): the workspace
    # output dir is mount-excluded from the container, so an attachment is
    # only reachable once it is inside the run tree.
    if origin and origin.get("operator_attachments"):
        try:
            from dispatch_envelope import land_operator_attachments
            land_operator_attachments(rd, str(origin["operator_attachments"]))
        except Exception:
            pass

    # Per-thread goal-brain — first call wins, same rule as prompt.txt.
    try:
        import thread_brain
        created = thread_brain.create_thread_brain(rd, goal=prompt, origin=origin)
        # Dispatch rationale (MILESTONES #3b): the navigator's live dispatch
        # decision rode in on origin because no run dir existed at decision
        # time. Record it as this thread's first real Decision so the run
        # knows why it was allowed to exist.
        _nav = (origin or {}).get("dispatch_navigator")
        if created is not None and isinstance(_nav, dict):
            try:
                _conf = float(_nav.get("confidence", 0.0) or 0.0)
            except Exception:
                _conf = 0.0
            thread_brain.append_decision(
                rd,
                # Secondary render — the canonical full-length copy rides
                # origin.dispatch_navigator (VERDICT_PROSE_CAP); this line
                # keeps the brain doc scannable but announces its cut.
                f"dispatch navigator: {_nav.get('move') or '?'}({_conf:.2f})"
                f" — {clip(_nav.get('reasoning'), 500)}",
            )
        # Fan-out defense: a new child registers in its parent's Threads
        # section so nothing leaves the parent's list silently.
        parent_id = str((origin or {}).get("parent_handle_id") or "")
        if created is not None and parent_id:
            thread_brain.record_child(run_dir(parent_id), handle_id, prompt)
    except Exception:
        pass  # a thread-brain failure must not block run-dir creation

    write_metadata(
        rd,
        handle_id=handle_id,
        prompt=prompt,
        lane=lane,
        model=model,
        extra=extra_metadata,
    )
    return rd


def write_metadata(
    rd: Path,
    *,
    handle_id: str,
    prompt: str,
    lane: Optional[str] = None,
    model: Optional[str] = None,
    status: Optional[str] = None,
    ended_at: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Path:
    """Write/refresh metadata.json. Preserves started_at if already set."""
    meta_path = rd / "metadata.json"

    def _merge(old: str) -> str:
        try:
            existing = json.loads(old) if old else {}
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        meta = {
            "handle_id": handle_id,
            "nickname": nickname(handle_id),
            "prompt": prompt,
            "lane": lane,
            "model": model,
            "started_at": existing.get("started_at")
                or datetime.now(timezone.utc).isoformat(),
            "ended_at": ended_at,
            "status": status,
            # Owner PID: lets the stranded-run sweep tell "owner died before
            # finalize" (SIGTERM runs no finally — specimen 51b09271) from
            # "still running" without age guesswork. First writer wins.
            "pid": existing.get("pid") or os.getpid(),
        }
        if extra:
            # Caller-supplied keys merge in but don't override the core set.
            for k, v in extra.items():
                meta.setdefault(k, v)
        # Preserve prior keys (e.g. ended_at from an earlier finalize call).
        for k, v in existing.items():
            if k not in meta or meta[k] is None:
                meta[k] = v
        # Publish lookup refs before metadata, preserving the crash-order
        # contract while the metadata snapshot remains lock-stable.
        index_run_dir(rd, meta)
        return json.dumps(meta, indent=2, default=str)

    from file_lock import locked_rmw
    locked_rmw(meta_path, _merge)
    return meta_path


def stamp_run_metadata(fields: dict) -> Optional[Path]:
    """Merge `fields` into the active run's metadata.json.

    For mid-run annotations (persona selection, experiment arms) where the
    caller doesn't hold the full write_metadata argument set. Existing keys
    win only when the new value is None. Best-effort, never raises.
    """
    try:
        rd = current_run_dir()
    except Exception:
        return None
    return _stamp_metadata_at(rd, fields)


def stamp_run_metadata_for(handle_id: str, fields: dict) -> Optional[Path]:
    """stamp_run_metadata by handle id — for finalize/repair paths that run
    after (or outside) the pinned run context (async-tail phase 2: the
    verdict_pending marker is resolved from handle()'s finalize)."""
    try:
        rd = run_dir(handle_id)
    except Exception:
        return None
    return _stamp_metadata_at(rd, fields)


def _stamp_metadata_at(rd: Optional[Path], fields: dict) -> Optional[Path]:
    try:
        if rd is None or not fields:
            return None
        meta_path = rd / "metadata.json"
        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            for k, v in fields.items():
                if v is not None:
                    existing[k] = v
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception:
        return None


def stamp_run_loop_lineage(entry: dict) -> Optional[Path]:
    """Append a loop-lineage entry to the active run's metadata.json.

    A run dir can host several loops — the initial attempt plus closure
    restarts and continuations reuse the same handle run dir — so
    lineage is a list: metadata["loops"], append-only, deduped by
    loop_id. Restart ancestry previously lived ONLY in captain's-log
    LOOP_CREATED events (2026-07-29 recon: zero of 728 live run dirs
    carried any of loop_reason/parent_loop_id/continuation_depth); this
    makes the run dir self-describing (Jeremy 2026-07-29: persist the
    artifacts along the way — for debugging, offline analysis, and
    showing the path a run took). Best-effort, never raises.
    """
    try:
        rd = current_run_dir()
        if rd is None or not entry or not entry.get("loop_id"):
            return None
        meta_path = rd / "metadata.json"

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            loops = existing.get("loops")
            if not isinstance(loops, list):
                loops = []
            if not any(
                isinstance(e, dict) and e.get("loop_id") == entry.get("loop_id")
                for e in loops
            ):
                loops.append(entry)
            existing["loops"] = loops
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception:
        return None


def stamp_run_audit_failure(fields: dict) -> Optional[Path]:
    """Append/upsert one loop's audit repair while preserving other loops.

    ``audit_repairs`` is the canonical multi-loop queue. ``audit_repair``
    remains the latest-record compatibility view for older readers.
    """
    try:
        rd = current_run_dir()
        repair = fields.get("audit_repair")
        if rd is None or not isinstance(repair, dict):
            return None
        meta_path = rd / "metadata.json"
        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            repairs = existing.get("audit_repairs")
            repairs = list(repairs) if isinstance(repairs, list) else []
            legacy = existing.get("audit_repair")
            if isinstance(legacy, dict) and not repairs:
                repairs.append(legacy)
            loop_id = repair.get("loop_id")
            repairs = [
                item for item in repairs
                if not isinstance(item, dict) or item.get("loop_id") != loop_id
            ]
            repairs.append(dict(repair))
            for k, v in fields.items():
                if v is not None:
                    existing[k] = v
            existing["audit_repairs"] = repairs
            existing["audit_repair"] = dict(repair)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception:
        return None


_VERDICT_KEYS = ("goal_achieved", "goal_verdict_source",
                 "goal_verdict_confidence", "goal_verdict_summary",
                 "goal_verdict_downgrade_reason", "goal_verdict_gaps",
                 # Contest standing is part of the verdict record
                 # (round-16 review: an uncontested replacement left the
                 # predecessor's disputed marker standing, and
                 # rerun_identity told future runs to distrust the NEW
                 # verdict). The disputed writer re-stamps AFTER a
                 # replacement when the new verdict is itself contested.
                 "goal_verdict_contested", "goal_verdict_contested_by")


def _apply_verdict_tuple(existing: dict, *, goal_achieved, source: str,
                         confidence, summary: str, downgrade_reason: str,
                         gaps) -> None:
    """THE verdict-tuple replacement. Mutates ``existing`` in place.

    One implementation for every verdict writer (round-15 review, 3-lens:
    a second hand-maintained field list in the retry stamp had already
    drifted — judged retries left stale confidence/downgrade/gaps).
    Every member is set or popped; nothing is ever left to a merge.
    ``confidence=None`` pops the key (the NOW lane records no
    confidence); ``goal_achieved=None`` pops the boolean (unjudged);
    empty downgrade/gaps pop theirs.
    """
    existing["goal_verdict_source"] = source
    existing["goal_verdict_summary"] = clip(summary, VERDICT_PROSE_CAP)
    if confidence is None:
        existing.pop("goal_verdict_confidence", None)
    else:
        existing["goal_verdict_confidence"] = float(confidence)
    if downgrade_reason:
        existing["goal_verdict_downgrade_reason"] = (
            clip(downgrade_reason, VERDICT_PROSE_CAP))
    else:
        existing.pop("goal_verdict_downgrade_reason", None)
    _all_gaps = [clip(g, 500) for g in (gaps or []) if g]
    _gaps = _all_gaps[:5]
    if len(_all_gaps) > 5:
        # Count cuts announce themselves like char cuts do (round-14
        # review: five-of-seven gaps rendered as a complete list).
        _gaps.append(f"(+{len(_all_gaps) - 5} more gap(s) in the "
                     "closure verdict artifact)")
    if _gaps:
        existing["goal_verdict_gaps"] = _gaps
    else:
        existing.pop("goal_verdict_gaps", None)
    if goal_achieved is None:
        existing.pop("goal_achieved", None)
    else:
        existing["goal_achieved"] = bool(goal_achieved)
    existing.pop("goal_verdict_contested", None)
    existing.pop("goal_verdict_contested_by", None)


def _clear_verdict_keys(existing: dict) -> None:
    for key in _VERDICT_KEYS:
        existing.pop(key, None)


def _apply_stop_tuple(existing: dict, stop_verdict, stop_evidence,
                      reopen_payload=None) -> None:
    """THE stop-tuple replacement. Mutates ``existing`` in place.

    Nonempty verdict sets both members (evidence honest-clipped at the
    stuck-reason-family 800); empty verdict pops both — this ending has
    no stop verdict, and a stale predecessor's must not stand (the same
    replace-whole-or-not-at-all doctrine as ``_apply_verdict_tuple``).
    One implementation for every stop writer (2026-08-15 bypass
    burn-down: three call sites and the retry stamp each hand-rolled
    this pair).

    ``reopen_payload`` (§13b, 2026-08-15): evidence-SPECIFIC reopen data
    recorded at stamp time — which budget, which cost estimate — the
    upgrade the stop_verdicts.REOPEN_CONDITIONS comment names. Rides the
    tuple's replace-whole doctrine: a new verdict without a payload pops
    any stale one (a predecessor's numbers must not annotate this
    ending), and clearing the verdict clears it too. Dict only; anything
    else is dropped rather than persisted."""
    if stop_verdict:
        existing["stop_verdict"] = str(stop_verdict)
        existing["stop_evidence"] = clip(stop_evidence, 800)
        if isinstance(reopen_payload, dict) and reopen_payload:
            existing["stop_reopen_payload"] = reopen_payload
        else:
            existing.pop("stop_reopen_payload", None)
    else:
        existing.pop("stop_verdict", None)
        existing.pop("stop_evidence", None)
        existing.pop("stop_reopen_payload", None)


def stamp_run_verdict(
    *,
    goal_achieved: Optional[bool],
    source: str,
    confidence: float,
    summary: str,
    downgrade_reason: str = "",
    gaps: Optional[list],
    extra: Optional[dict] = None,
) -> Optional[Path]:
    """Replace the active run's latest goal verdict, preserving tri-state.

    ``goal_achieved=None`` means the latest verifier was unable to judge the
    goal.  In that case an earlier attempt's boolean must be removed rather
    than inherited: the run metadata describes the delivered/latest attempt,
    not the best-looking verdict seen anywhere in the handle.

    ``downgrade_reason`` follows the only-when-stamped convention: nonempty
    writes ``goal_verdict_downgrade_reason``; empty removes any stale one
    from an earlier attempt (same replace-semantics as ``goal_achieved``).
    ``gaps`` likewise (2026-08-14 fixpoint round, 3-lens consensus HIGH:
    this writer replaced every tuple member EXCEPT gaps, so an achieved
    retry kept its failed predecessor's "Missing:" list): a non-empty list
    replaces ``goal_verdict_gaps`` (each entry honest-clipped; at most 5
    ride, with an explicit omission entry when more existed); None/empty
    removes any stale one. ``gaps`` is REQUIRED, no default (round-14
    review, 3-lens consensus: a destructive default let two unmigrated
    callers silently CLEAR their verdicts' real gaps — every caller must
    choose set-or-clear explicitly). The verdict tuple is replaced WHOLE
    or not at all.

    ``extra`` rides non-tuple context fields (e.g. the loop_ids a
    closure-stamp-failure records) in the SAME locked write — splitting
    them into a second raw write is the non-atomic write-then-clear
    shape round 14 removed. Tuple members inside extra are a ValueError
    (2026-08-15 bypass burn-down).
    """
    extra = _guard_owner_extra(extra)
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"
        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            _apply_verdict_tuple(
                existing, goal_achieved=goal_achieved, source=source,
                confidence=confidence, summary=summary,
                downgrade_reason=downgrade_reason, gaps=gaps)
            for k, v in (extra or {}).items():
                existing[k] = v
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception as exc:
        # NEVER silent (round-16 review, 3-lens: every caller
        # ignored the None and a failed write left the
        # superseded state standing with zero trace).
        log.warning("runs: verdict stamp FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def stamp_run_stop_verdict(
    *,
    stop_verdict: str,
    stop_evidence: str,
    pause_reason: str = "",
    run_dir: Optional[Path] = None,
    refine_note: bool = False,
    evidence_out: Optional[list] = None,
    reopen_payload: Optional[dict] = None,
) -> Optional[Path]:
    """Replace a run's stop-verdict tuple in one locked write.

    The SET twin of ``clear_run_stop_verdict`` (2026-08-15 bypass
    burn-down: agent_loop's fence stamp, loop_finalize's ending stamp,
    handle's demotion stamp, and director's close stamp each hand-rolled
    the pair through raw writers — one of them through a bare locked_rmw
    that skipped ``index_run_dir`` entirely, so the index could hold a
    stale row). Semantics owned here:

    - Nonempty ``stop_verdict`` sets both members (evidence clipped 800);
      EMPTY clears both — metadata reflects THIS ending, not the first.
      Consumers read ``meta.get("stop_verdict") or ""``, so key-absent is
      the schema's "no stop verdict" (parity with the clear helper).
    - ``pause_reason``: truthy writes it; falsy leaves any existing value
      untouched (a resumed run's fresh context has no pause_reason, and
      an unconditional clear would erase the stranded sweep's post-hoc
      writer-died stamp — 2026-07-31 slice-1 review #2).
    - ``run_dir``: an explicit target (e.g. resolved by loop_id after the
      run ended); default is the active run dir.
    - ``refine_note=True``: when a DIFFERENT nonempty verdict is being
      replaced, append " [refines: <prior>]" to the evidence before the
      clip — atomically, inside the lock (the close-refinement
      convention: a later, more specific verdict records what it
      refined instead of silently overwriting it).
    - ``evidence_out``: a list the owner APPENDS the final written
      stop_evidence to, captured INSIDE the lock (2026-08-15 round-2
      review, probe-confirmed HIGH: the first cut made the refine-note
      caller re-read the file after lock release for its ledger row — a
      concurrent writer in that window silently substituted ITS content,
      the exact drift class the owners exist to end).
    - ``reopen_payload`` (§13b): evidence-specific reopen data — which
      budget, which cost estimate — stored as ``stop_reopen_payload``
      beside the tuple; consumer today is the revisit scanner
      (candidate context + CLI). Follows the tuple's replace-whole
      doctrine (see ``_apply_stop_tuple``): a re-stamp that doesn't
      resupply the payload pops it, even for the SAME verdict — the
      payload always describes the stamp that wrote it, never a
      predecessor's numbers standing beside fresher evidence.
    """
    try:
        rd = run_dir if run_dir is not None else current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            evidence = stop_evidence
            if refine_note and stop_verdict:
                _prior = existing.get("stop_verdict") or ""
                if _prior and _prior != stop_verdict:
                    evidence = f"{stop_evidence} [refines: {_prior}]"
            _apply_stop_tuple(existing, stop_verdict, evidence,
                              reopen_payload=reopen_payload)
            if evidence_out is not None:
                evidence_out.append(existing.get("stop_evidence", ""))
            if pause_reason:
                existing["pause_reason"] = str(pause_reason)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception as exc:
        # NEVER silent (round-16 review, 3-lens: every caller
        # ignored the None and a failed write left the
        # superseded state standing with zero trace).
        log.warning("runs: stop-verdict stamp FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def stamp_unjudged_verdict_source(source: str, summary: str = "") -> Optional[Path]:
    """Record WHY there is no goal verdict, without inventing one.

    The deliberate-partial owner (2026-08-15 bypass burn-down): two
    handle lanes stamp only ``goal_verdict_source`` (+ optionally a
    summary) while ``goal_achieved`` stays ABSENT — a skipped closure
    (no steps completed) and a crashed judge (closure_error) are
    measurement gaps, not verdicts, and absence-means-not-judged is the
    schema's tri-state. Routing these through ``stamp_run_verdict``
    would flatten that: the tuple owner replaces WHOLE, popping members
    these lanes must not touch. This owner touches exactly source (+
    summary when nonempty, clipped) and nothing else — the partiality
    is the point, and now it has one named, documented home.
    """
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            existing["goal_verdict_source"] = str(source)
            if summary:
                existing["goal_verdict_summary"] = clip(
                    summary, VERDICT_PROSE_CAP)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception as exc:
        log.warning("runs: unjudged-source stamp FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def stamp_run_verdict_contested(
    *,
    contested_by: str,
    extra: Optional[dict] = None,
) -> Optional[Path]:
    """Set the durable disputed marker on the active run's verdict.

    The contested pair is popped by every tuple replacement
    (``_apply_verdict_tuple``) and re-stamped AFTER by the lane that
    disputes the fresh verdict — this owner is that re-stamp (2026-08-15
    bypass burn-down: three sites hand-rolled it through write_metadata).
    ``extra`` carries the dispute's context fields (e.g. the closure
    confidence a provenance demotion overrode) in the same locked write;
    it must not carry verdict/stop tuple members — those belong to the
    tuple owners (ValueError, fail loud).
    """
    extra = _guard_owner_extra(extra)
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            existing["goal_verdict_contested"] = True
            existing["goal_verdict_contested_by"] = str(contested_by)
            for k, v in (extra or {}).items():
                existing[k] = v
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception as exc:
        log.warning("runs: contested marker stamp FAILED — metadata may "
                    "hold superseded state: %s", exc)
        return None


_OWNER_EXTRA_FORBIDDEN = frozenset(_VERDICT_KEYS) | {
    "stop_verdict", "stop_evidence"}


def _guard_owner_extra(extra: Optional[dict]) -> Optional[dict]:
    """``extra`` riders on the owners must not smuggle tuple members —
    that would recreate the exact partial-write drift the owners exist
    to end. Returns a SANITIZED copy with forbidden keys stripped, after
    a loud warning.

    Deliberately warn-and-strip, not raise (round-2 review, Skeptic,
    executed probe): every extra-carrying call site sits inside a
    blanket ``except`` — a raise here is swallowed and the ENTIRE stamp
    is lost, leaving the superseded verdict standing silently (the
    round-16 failure class, reintroduced by the guard meant to prevent
    drift). Dropping the smuggled key and landing the rest errs the
    safe direction; the warning makes the coding bug visible.
    """
    if not extra:
        return extra
    bad = sorted(set(extra) & _OWNER_EXTRA_FORBIDDEN)
    if not bad:
        return extra
    log.warning(
        "runs: owner extra carried verdict/stop tuple keys %s — STRIPPED "
        "(pass them through the owner's own parameters); the rest of the "
        "stamp still lands", bad)
    return {k: v for k, v in extra.items()
            if k not in _OWNER_EXTRA_FORBIDDEN}


def stamp_delivered_now_retry(
    *,
    retry_marker: str,
    judged: bool,
    goal_achieved: Optional[bool] = None,
    source: str = "",
    summary: str = "",
    stop_verdict: str = "",
    stop_evidence: str = "",
) -> Optional[Path]:
    """Replace the active run's delivered-retry state in ONE locked write.

    Round-14 review (Skeptic HIGH): the retry block's write-then-clear
    sequence was three separate mutations whose clear halves swallowed
    failures — an injected failure between them recreated exactly the
    contradictory states (achieved=true beside a stale lost-the-plot;
    an unjudged delivery beside its predecessor's false verdict) the
    round-13 fixes existed to remove. One merge function now sets the
    retry marker, then sets OR removes the verdict tuple (judged /
    unjudged) and the stop tuple, atomically. Returns None on failure —
    callers must surface that, not ignore it.
    """
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _merge(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            existing["now_artifact_retry"] = retry_marker
            if judged:
                # THE shared tuple replacement — every member set or
                # popped (round-15 review, 3-lens: this branch's own
                # field list had already drifted from the schema owner's,
                # leaving stale confidence/downgrade/gaps standing).
                _apply_verdict_tuple(
                    existing, goal_achieved=bool(goal_achieved),
                    source=source, confidence=None, summary=summary,
                    downgrade_reason="", gaps=None)
            else:
                _clear_verdict_keys(existing)
            _apply_stop_tuple(existing, stop_verdict, stop_evidence)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _merge)
        return meta_path
    except Exception as exc:
        # NEVER silent (round-16 review, 3-lens: every caller
        # ignored the None and a failed write left the
        # superseded state standing with zero trace).
        log.warning("runs: delivered-retry stamp FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def clear_run_stop_verdict() -> Optional[Path]:
    """Remove the active run's stop-verdict tuple (verdict + evidence).

    The stop-tuple twin of ``clear_run_verdict`` (fixpoint round
    2026-08-14, Skeptic HIGH: a recovered NOW retry kept the failed first
    attempt's ``stop_verdict="lost-the-plot"`` beside ``status=done`` /
    ``goal_achieved=true``). The delivered attempt's state replaces its
    predecessor's — including the absence of a stop verdict.
    """
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _strip(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            for key in ("stop_verdict", "stop_evidence"):
                existing.pop(key, None)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _strip)
        return meta_path
    except Exception as exc:
        # NEVER silent (round-16 review, 3-lens: every caller
        # ignored the None and a failed write left the
        # superseded state standing with zero trace).
        log.warning("runs: stop-tuple clear FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def clear_run_verdict() -> Optional[Path]:
    """Remove the active run's goal-verdict tuple entirely.

    The unjudged twin of ``stamp_run_verdict``'s replace semantics
    (fixpoint review 2026-08-14): when the DELIVERED attempt was never
    judged, an earlier attempt's boolean, source, summary, and gaps must
    not stand — ``write_metadata`` preserves omitted keys, so absence has
    to be written explicitly. Key-absent is the schema's "unjudged".
    No-op when no run dir is pinned or no verdict was stamped.
    """
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        meta_path = rd / "metadata.json"

        def _strip(old: str) -> str:
            try:
                existing = json.loads(old) if old else {}
            except Exception:
                existing = {}
            if not isinstance(existing, dict):
                existing = {}
            _clear_verdict_keys(existing)
            index_run_dir(rd, existing)
            return json.dumps(existing, indent=2, default=str)

        from file_lock import locked_rmw
        locked_rmw(meta_path, _strip)
        return meta_path
    except Exception as exc:
        # NEVER silent (round-16 review, 3-lens: every caller
        # ignored the None and a failed write left the
        # superseded state standing with zero trace).
        log.warning("runs: verdict clear FAILED — metadata may hold "
                    "superseded state: %s", exc)
        return None


def finalize_run(
    handle_id: str,
    *,
    status: str,
    ended_at: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Optional[Path]:
    """Mark a run as ended in metadata.json. Returns run-dir or None if absent.

    `extra` merges additional top-level keys into metadata (e.g.
    `backend_error` — the actionable why/what-to-do for error-status runs,
    BACKEND_RESILIENCE_DESIGN §2)."""
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    meta_path = rd / "metadata.json"
    if not meta_path.exists():
        return rd

    def _merge(old: str) -> str:
        # Same locked-RMW shape as the stamp_run_* family (C0.2): the old
        # bare read→mutate→atomic_write raced every concurrent stamper and
        # dropped whichever keys the loser had written.
        try:
            meta = json.loads(old) if old.strip() else {}
            if not isinstance(meta, dict):
                raise ValueError(
                    f"metadata.json holds {type(meta).__name__}, not an object")
        except Exception as exc:
            # Never silently destroy another writer's keys: the old path
            # replaced an unparseable file with {} + two keys. Park the
            # bytes in a sidecar so nothing is lost, then finalize onto a
            # fresh object — the run must still reach a terminal status.
            meta = {}
            side = meta_path.with_name(meta_path.name + ".corrupt")
            try:
                side.write_text(old, encoding="utf-8",
                                errors="surrogateescape")
                log.warning(
                    "finalize_run(%s): metadata.json unparseable (%s) — "
                    "original preserved at %s", handle_id, exc, side.name)
            except OSError as werr:
                log.warning(
                    "finalize_run(%s): metadata.json unparseable (%s) and "
                    "sidecar write failed (%s) — finalizing fresh",
                    handle_id, exc, werr)
        meta["status"] = status
        meta["ended_at"] = ended_at or datetime.now(timezone.utc).isoformat()
        if extra:
            meta.update(extra)
        return json.dumps(meta, indent=2, default=str)

    from file_lock import locked_rmw
    locked_rmw(meta_path, _merge)
    try:
        import thread_brain
        thread_brain.record_close(rd, status=status)
    except Exception:
        pass  # never block finalize on the audit artifact
    return rd


# ---------------------------------------------------------------------------
# Run lifecycle: the generic "own a run" open/close sequence.
#
# Every lane that owns a run (handle NOW/AGENDA, and — since BACKLOG #18 —
# the `maro run`/`maro resume` CLI lane) does the same two things: at start,
# create + pin the run-dir and capture start-of-run attribution; at end,
# slice the log, snapshot the repo, stamp the terminal status, curate the
# run_card, and re-render reports. These two helpers are that sequence, so
# the lanes share one implementation instead of copy-pasting the primitive
# calls. NOW-lane-specific work (persona resolution, dispatch classification,
# notify-channel emits) stays at each call site — only the generic run-dir
# mechanism lives here.
# ---------------------------------------------------------------------------

def open_run(
    handle_id: str,
    *,
    prompt: str,
    model: Optional[str] = None,
    lane: Optional[str] = None,
    repo_path: str = "",
    origin: Optional[Origin] = None,
    measurement_class: Optional[str] = None,
    dry_run: Optional[bool] = None,
) -> Path:
    """Create + pin the run-dir and arm start-of-run attribution capture.

    Returns the run-dir path (already pinned as the current-run context, so
    downstream artifact writers land in it). Callers wrap this in their own
    try/except — a runs/ failure must never block the run. The environment
    snapshot is best-effort here; the run-dir + pin are the load-bearing part.
    """
    extra = {"origin": origin} if origin else {}
    if measurement_class is not None:
        extra["measurement_class"] = measurement_class
    if dry_run is not None:
        extra["dry_run"] = bool(dry_run)
    rd = create_run_dir(
        handle_id,
        prompt=prompt,
        lane=lane,
        model=model,
        extra_metadata=extra or None,
    )
    set_current_run_dir(rd)
    record_log_offset(handle_id)
    if repo_path:
        record_repo_base(handle_id, repo_path)
    try:
        write_environment_snapshot(rd)
    except Exception:
        pass
    return rd


def close_run(
    handle_id: str,
    *,
    status: str,
    backend_error=None,
) -> Optional[dict]:
    """Finalize a run-dir and return its curated run_card (or None).

    Slices the captain's-log window, snapshots the repo bundle, stamps the
    terminal status (merging an actionable backend_error when present),
    curates run_card.json, and re-renders the run's reports so downstream
    tooling (`maro inspect-run`, `maro viz search`) can see the finished
    run. Best-effort throughout — never raises. `backend_error` is a
    classified BackendError-info object; only its actionable fields are
    persisted.
    """
    # Finished-without-closure tripwire (LT-0 b): an agenda run whose metadata
    # carries no goal verdict means closure never stamped one (5/51 loop_id-era
    # agenda rows at the 2026-07-29 census, 2 same-day — live, low-rate). Make
    # the gap visible at the moment it becomes permanent instead of letting
    # unverdicted rows accrete silently.
    # Emitted BEFORE the log slice so the event rides the run's own slice.
    #
    # Two corrections from the 2026-08-06 verdict-coverage census:
    #
    # 1. It watched `done` only, so a `stuck` run that closure never judged
    #    was equally silent and equally invisible — 1 of the 3 residual rows.
    #    An unjudged terminal run is the gap regardless of which terminal
    #    status it wears; "stuck" is a process status, not a verdict.
    # 2. The event landed in the captain's log while the OUTCOMES ROW — the
    #    thing an honest denominator actually counts — still said nothing.
    #    A log that knows and a ledger that doesn't is still a ledger you
    #    cannot count. So stamp the row explicitly unverdicted: goal_achieved
    #    stays None (we genuinely do not know, and None never erases an
    #    existing provenance False) while the source records WHY it is absent.
    #    That is the backlog's "verdictable-or-exempt, never silently
    #    neither" — absence with a reason is a fact; absence alone is a hole.
    #
    # Known false positive, named rather than papered over: closure also
    # requires `_ran_any_step` (handle.py), so a run that completed zero
    # steps legitimately has no verdict and will still trip this. Run
    # metadata carries no step count, so it cannot be told apart here.
    # Whoever next measures the denominator should expect a small share of
    # these and can separate them from the loop log's step records.
    from stop_verdicts import (
        EXECUTION_FINISHED_STATUSES,
        VERDICT_SOURCE_NEVER_STAMPED,
        VERDICT_SOURCE_RUN_ERRORED,
    )
    if status in EXECUTION_FINISHED_STATUSES:
        try:
            meta = json.loads(
                (run_dir(handle_id) / "metadata.json").read_text(encoding="utf-8"))
            # Async-tail phase 2: the answer-first early close runs BEFORE
            # closure by design — an ACTIVE verdict_pending marker means the
            # verdict is owed but not yet due, not that closure forgot. The
            # tripwire waits for the finalize-time close (marker resolved) or
            # the crash-orphan sweep (audit_repair) to make the honest call.
            _vp = meta.get("verdict_pending")
            _vp_active = isinstance(_vp, dict) and not _vp.get("resolved_at")
            if (not meta.get("goal_verdict_source")
                    and not _vp_active
                    and meta.get("lane") == "agenda"
                    and not meta.get("dry_run")):
                # Modern runs carry plural loop_ids; the singular
                # metadata.loop_id stopped being stamped (see the v1-index
                # note above) — reading it alone made this ledger stamp
                # dead code for every current agenda run (adversarial
                # review 2026-08-06 R2-2). Old rows keep working via the
                # singular fallback.
                _loop_ids = [
                    str(l) for l in (meta.get("loop_ids") or []) if l
                ] or [s for s in (str(meta.get("loop_id") or ""),) if s]
                from captains_log import log_event, DONE_WITHOUT_VERDICT
                log_event(
                    DONE_WITHOUT_VERDICT,
                    subject=handle_id,
                    summary=(f"run finalized status={status} with no goal "
                             "verdict in run metadata — closure never "
                             "stamped one"),
                    context={"handle_id": handle_id,
                             "status": str(status),
                             "lane": str(meta.get("lane", "")),
                             "loop_id": (_loop_ids[0] if _loop_ids else "")},
                    loop_id=(_loop_ids[0] if _loop_ids else None),
                )
                for _lid in _loop_ids:
                    try:
                        from memory_ledger import stamp_outcome_verdict
                        stamp_outcome_verdict(
                            _lid,
                            goal_achieved=None,
                            goal_verdict_source=VERDICT_SOURCE_NEVER_STAMPED,
                        )
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        slice_log_for_run(handle_id)
    except Exception:
        pass
    try:
        snapshot_repo_bundle(handle_id)
    except Exception:
        pass
    extra = None
    if backend_error is not None:
        try:
            extra = {"backend_error": {
                "error_class": backend_error.error_class,
                "backend": backend_error.backend,
                "user_action": backend_error.user_action,
            }}
        except Exception:
            extra = None

    # An errored run is unverdictable, and should SAY so (2026-08-06, the
    # residual left open when the finished-without-closure tripwire shipped).
    # It gets its own source rather than VERDICT_SOURCE_NEVER_STAMPED: there,
    # closure owed a verdict and did not deliver one — a closure bug worth
    # hunting; here nothing was owed, because there was no finished work to
    # judge. Same reasoning as the tripwire's, one layer over: absence with a
    # reason is a fact, absence alone is a hole.
    #
    # Metadata only, deliberately — no ledger stamp. Measured over 1,493
    # rows: "error" never appears as an outcome status at all, because the
    # run dies before reflect_and_record, so there is no row to stamp and a
    # stamp call here would be dead code pretending to be a guard.
    #
    # Historical note for whoever finds this: the population is 132 runs,
    # 129 of them in 2026-05 and none since 2026-07-04. This closes the gap
    # for the next one rather than a live bleed.
    if status == "error":
        try:
            meta = json.loads(
                (run_dir(handle_id) / "metadata.json").read_text(encoding="utf-8"))
            if not meta.get("goal_verdict_source") and not meta.get("dry_run"):
                extra = dict(extra or {})
                extra["goal_verdict_source"] = VERDICT_SOURCE_RUN_ERRORED
        except Exception:
            pass
    try:
        finalize_run(handle_id, status=status, extra=extra)
    except Exception:
        pass
    card = None
    try:
        from run_curation import curate_run
        card = curate_run(handle_id, status=status)
    except Exception:
        card = None
    try:
        from loop_report import write_reports_for_run_dir
        write_reports_for_run_dir(run_dir(handle_id))
    except Exception:
        pass
    # Record the terminal. Deliberately once per ACTUAL close, not once per
    # run: on the answer-first path (notify.verdict_followup) close_run fires
    # twice — first stamping "done" with success_class done-verdict-pending
    # before closure has run, then again after the verdict with the real
    # class. Both are true at the time they happen, and a reader that assumed
    # one terminal per run would have to pick one and be wrong. Append order
    # is the record, so the last row is the outcome that stands.
    try:
        from run_trace import record_edge
        _cls = (card or {}).get("success_class") if isinstance(card, dict) else None
        _rd = run_dir(handle_id)
        record_edge("close.curate", "close.terminal", run_dir=_rd,
                    handle_id=handle_id, status=status)
        record_edge("close.terminal", f"term.{_cls or status or 'unknown'}",
                    run_dir=_rd, handle_id=handle_id,
                    success_class=_cls or "", status=status)
    except Exception:
        pass
    return card


# ---------------------------------------------------------------------------
# Current-run context: lets agent_loop / scope writers land in the run-dir
# without threading run_dir through every signature.
#
# Setting this is opt-in: when unset, callers fall back to the legacy
# project-dir artifact path. Tests that don't set it see no behavior change.
#
# ContextVar, not a module global: concurrent loops in one process
# (run_parallel_loops, DAG step fan-out) each see their own run-dir instead
# of sharing a last-writer-wins global. Same pattern as
# llm._DEFAULT_SUBPROCESS_CWD. ThreadPoolExecutor workers do NOT inherit
# the submitting thread's context — fan-out sites must submit via
# contextvars.copy_context().run (see loop_parallel).
# ---------------------------------------------------------------------------

_current_run_dir: contextvars.ContextVar[Optional[Path]] = contextvars.ContextVar(
    "maro_current_run_dir", default=None
)


def set_current_run_dir(path: Optional[Path]) -> None:
    """Set (or clear) the run-dir for the current handle. Accepts None to clear."""
    _current_run_dir.set(Path(path) if path is not None else None)


def current_run_dir() -> Optional[Path]:
    """Return the run-dir for the current handle, or None if unset."""
    return _current_run_dir.get()


@contextlib.contextmanager
def scoped_run_dir(path: Optional[Path]):
    """Scope the current run-dir to a block, restoring the prior value after."""
    token = _current_run_dir.set(Path(path) if path is not None else None)
    try:
        yield
    finally:
        _current_run_dir.reset(token)


def current_handle_id() -> Optional[str]:
    """Handle id of the active run, derived from the pinned run-dir name
    (`<handle_id>-<nickname>`). None when no run-dir is pinned."""
    rd = current_run_dir()
    if rd is None:
        return None
    return rd.name.split("-", 1)[0]


def artifact_dir(project: str, project_root_fn=None) -> Path:
    """Where to write per-loop artifacts (PARTIAL files, scratchpad, step outputs).

    If a run-dir is active, returns `<run-dir>/build/`. Otherwise falls back
    to `project_root_fn()/project/artifacts` for backwards compatibility.
    The fallback is what every existing call site already computed inline,
    so swapping in this helper is behavior-preserving when no run-dir is set.

    `project_root_fn` is injected so callers can keep their existing
    `_project_dir_root` import path without circular-import shenanigans.
    """
    rd = current_run_dir()
    if rd is not None:
        out = rd / "build"
        out.mkdir(parents=True, exist_ok=True)
        return out
    if project_root_fn is None:
        # No run-dir AND no fallback — punt to the workspace default.
        ws = os.environ.get("MARO_WORKSPACE") or os.environ.get("OPENCLAW_WORKSPACE")
        root = Path(ws) / "projects" if ws else Path.home() / ".maro" / "workspace" / "projects"
    else:
        root = project_root_fn()
    out = Path(root) / project / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Run recorder — per-call prompt/response/tool_events capture (record-mode)
# ---------------------------------------------------------------------------
# Default-ON capture of the paid-for LLM traffic so a finished run is replayable
# and mineable later (skills, scripts, decision priors, rephrased re-attempts).
# Completes rungs 4-6 of the visibility ladder (see ROADMAP). Adornment on the
# existing run-dir plan: writes land in `<run-dir>/build/calls/`, same as every
# other run-scoped write. Turn off with `MARO_RECORD=0` or config
# `record.enabled: false` (a real off-switch, per good-system-citizen).

import threading as _threading

_CALL_COUNTERS: dict = {}
_CALL_LOCK = _threading.Lock()


def recording_enabled() -> bool:
    """Record-mode is on unless explicitly disabled (env wins over config)."""
    env = os.environ.get("MARO_RECORD")
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from config import get as _get
        return bool(_get("record.enabled", True))
    except Exception:
        return True


def _scan_highest_seq(rd: Path) -> int:
    highest = 0
    try:
        for p in (rd / "build" / "calls").glob("call-*.json"):
            try:
                highest = max(highest, int(p.stem.split("-")[1]))
            except (IndexError, ValueError):
                continue
    except OSError:
        pass
    return highest


def _next_call_seq(rd: Path) -> int:
    key = str(rd)
    with _CALL_LOCK:
        if key not in _CALL_COUNTERS:
            # Rebuild from disk: after a crash+resume the in-memory counter
            # is gone, and starting at 1 would overwrite call-00001.json.
            _CALL_COUNTERS[key] = _scan_highest_seq(rd)
        n = _CALL_COUNTERS[key] + 1
        _CALL_COUNTERS[key] = n
        return n


def _bump_call_seq(rd: Path) -> int:
    """Collision recovery (C0.4): another PROCESS published the seq this
    process's counter allocated — the threading.Lock can't see it. Resync
    from disk under the counter lock and claim the number after both views.
    """
    key = str(rd)
    with _CALL_LOCK:
        n = max(_scan_highest_seq(rd), _CALL_COUNTERS.get(key, 0)) + 1
        _CALL_COUNTERS[key] = n
        return n


def record_llm_call(prompt, response_text, *, backend="", model="",
                    tool_events=None, tokens_in=None, tokens_out=None,
                    max_tokens_requested=None,
                    purpose: str = "",
                    error: str = "",
                    cost_usd: float = 0.0,
                    run_dir: Optional[Path] = None) -> Optional[Path]:
    """Persist one LLM call to `<run-dir>/build/calls/call-NNNNN.json` (scrubbed).

    No-op (returns None) when record-mode is off or no run-dir is active —
    capture must never affect the request outcome, so all errors are swallowed.

    `purpose` is an optional caller-stamped label ("classify", "advisor",
    "director_plan", ...) — the durable replacement for loop_report.py's
    prompt-opener sniffer (BACKLOG #17 sub-item 2), which stays as a fallback
    for historical records recorded before this field existed.
    """
    try:
        if not recording_enabled():
            return None
        rd = run_dir or current_run_dir()
        if rd is None:
            return None
        from secret_scrub import scrub
        calls = Path(rd) / "build" / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        seq = _next_call_seq(Path(rd))
        rec = scrub({
            "seq": seq,
            "backend": backend,
            "model": model,
            "prompt": prompt if isinstance(prompt, str) else str(prompt),
            "response": response_text if isinstance(response_text, str) else str(response_text),
            "tool_events": tool_events or [],
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            # Requested cap, recorded so an overrun is diagnosable from the
            # call record alone (not every backend enforces max_tokens).
            "max_tokens_requested": max_tokens_requested,
            "purpose": purpose or "",
            # Provider-reported billed cost when the backend gave one (0.0 =
            # not reported, NOT free) — per-call cost was previously only
            # derivable for loop steps (async-tail visibility, 2026-08-13).
            "cost_usd": float(cost_usd or 0.0),
            # UU-1 (BACKLOG LT arc): failed/killed attempts get a record too.
            # Before this, a timeout-killed call left ZERO bytes — the cold
            # chlorination run's most expensive wall-clock event (10min step-1
            # kill) was unrecoverable. On error records, `response` holds
            # whatever partial output the transport salvaged and `error` names
            # why it ended. Consumers treat error-records as attempts, not
            # results. Scrubbed with everything else.
            "error": str(error)[:500] if error else "",
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        # Temp-then-link publication (C0.4 + R2-1): write-once is enforced,
        # not conventional, AND the final name only ever appears with the
        # complete payload. Bare O_EXCL at the final name published an EMPTY
        # file before the payload write — a crash in that window permanently
        # stranded the seq with a zero-byte record, and readers could see
        # partial JSON. Now the payload lands (flushed + fsynced) in a
        # dot-prefixed temp in the same calls/ dir — invisible to
        # _scan_highest_seq and every call-record reader, whose globs all
        # require `call-*.json` — and os.link publishes it atomically:
        # FileExistsError on collision means the loser bumps the seq and
        # retries (bounded), exactly the old O_EXCL discipline.
        payload = json.dumps(rec, default=str)
        tmp = calls / f".call-tmp-{os.getpid()}-{os.urandom(4).hex()}"
        try:
            for _ in range(10):  # bounded — capture must never block the call
                with open(tmp, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                out = calls / f"call-{seq:05d}.json"
                try:
                    os.link(str(tmp), str(out))
                except FileExistsError:
                    seq = _bump_call_seq(Path(rd))
                    rec["seq"] = seq
                    payload = json.dumps(rec, default=str)
                    continue
                return out
            return None
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except Exception:
        return None


def source_dir() -> Optional[Path]:
    """`<run-dir>/source/` if a run-dir is active, else None.

    Used by handle.py for scope.md and resolved_intent.md placement.
    Callers fall back to project_dir-based writes when this returns None.
    """
    rd = current_run_dir()
    if rd is None:
        return None
    out = rd / "source"
    out.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# Environment snapshot + skills manifest — per-run attribution inputs.
# A run's outcome is a function of goal AND environment: which config era,
# which skill variants, which persona. None of that was run-keyed, so skill
# or config changes couldn't be attributed to outcome shifts (the
# verify->learn gap) and the verdict corpus straddles config eras invisibly.
# source/ is the natural home — it already holds the run's other compile
# inputs (prompt.txt, goal_brain.md).
# ---------------------------------------------------------------------------

def write_environment_snapshot(run_dir_override: Optional[Path] = None) -> Optional[Path]:
    """Write `<run-dir>/source/environment.json` — the run's config era.

    Captured at run start: scrubbed effective config, MARO_*/OPENCLAW_* env
    overrides (env beats config, so config alone lies), framework git sha,
    host platform, and today's spend so far. Best-effort, never raises —
    history can't be backfilled, but a run must never fail over its own
    bookkeeping.
    """
    try:
        rd = run_dir_override or current_run_dir()
        if rd is None:
            return None
        from secret_scrub import scrub

        snap: dict = {"captured_at": datetime.now(timezone.utc).isoformat()}
        try:
            import platform as _platform
            import sys as _sys
            snap["host"] = {
                "hostname": _platform.node(),
                "platform": _platform.platform(),
                "python": _sys.version.split()[0],
            }
        except Exception:
            pass
        try:
            import subprocess as _sp
            repo_root = Path(__file__).resolve().parent.parent
            r = _sp.run(
                ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                snap["maro_git_sha"] = r.stdout.strip()
        except Exception:
            pass
        try:
            snap["env_overrides"] = scrub({
                k: v for k, v in os.environ.items()
                if k.startswith(("MARO_", "OPENCLAW_"))
            })
        except Exception:
            pass
        try:
            from config import load_config
            snap["config"] = scrub(load_config())
        except Exception:
            pass
        try:
            from metrics import spend_today
            snap["spend_today_usd_at_start"] = round(spend_today(), 4)
        except Exception:
            pass
        try:
            # Same predicates a run's build_adapter walk uses — records which
            # backends were actually available, in failover order (key values
            # never included, only set/not-set).
            from llm import detect_backends
            snap["backends"] = [
                {"name": n, "usable": u, "detail": det}
                for (n, u, det) in detect_backends()
            ]
        except Exception:
            pass

        out = rd / "source" / "environment.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        return out
    except Exception:
        return None


def append_skills_manifest(entries: list, *, stage: str,
                           meta: Optional[dict] = None) -> Optional[Path]:
    """Append one skill-injection event to `<run-dir>/source/skills_manifest.jsonl`.

    Called at each injection site with the skills that actually entered a
    prompt (post A/B variant routing — the routed variant is the point of
    recording this). `entries` is a list of dicts shaped by the caller
    ({name, id, content_hash, variant_of, tier, ...}) so this stays a dumb
    appender. JSONL because injection can happen more than once per run
    (decompose, curated summaries, replans). Best-effort, never raises.
    """
    try:
        rd = current_run_dir()
        if rd is None:
            return None
        # An EMPTY entries list is recorded, not skipped. Absence of this file
        # used to mean two different things — "no skills matched" and "the
        # recorder never ran" — which makes the file useless as an attribution
        # rail: a cold-store run legitimately matches nothing, and that is a
        # data point, not a gap. Present-and-empty now means "nothing was
        # injected"; absent means the recorder genuinely did not run.
        # (Both readers iterate `rec["skills"] or []`, so empty is a no-op for
        # them — memory_ledger skips on empty skill_ids, loop_report renders
        # no rows.)
        out = rd / "source" / "skills_manifest.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": stage,
            "skills": entries,
        }
        if meta:
            # Match-tier telemetry (2026-08-08): record-level selection info
            # ({method, n_candidates, top_score}) — present even when the
            # skills list is empty, which is what turns the binary gap
            # signal into a graded one.
            record["match"] = meta
        from file_lock import locked_append
        locked_append(out, json.dumps(record, default=str))
        return out
    except Exception:
        return None


def read_injected_skill_ids(run_dir_path: Optional[Path] = None) -> Optional[set]:
    """Deduped skill ids from the run's skills_manifest.jsonl.

    Tri-state on purpose (R3-2 attribution rail): None when the manifest
    is absent — the recorder never ran, so what was injected is unknown;
    an empty set when present-and-empty — nothing was injected, and
    crediting anything would be bystander attribution.
    """
    try:
        rd = run_dir_path or current_run_dir()
        if rd is None:
            return None
        manifest = Path(rd) / "source" / "skills_manifest.jsonl"
        if not manifest.is_file():
            return None
        ids: set = set()
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for entry in rec.get("skills") or []:
                # String ids only, never coerced (adversarial r17, two
                # seats — the memory_ledger twin minted "True"/"7"
                # identities out of malformed rows via str()).
                sid = entry.get("id") if isinstance(entry, dict) else None
                if isinstance(sid, str) and sid:
                    ids.add(sid)
        return ids
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Captain's log slicing — record offset at run-start, slice on finalize.
# Same pattern scope_ab_runner.py uses externally; centralized here so
# handle() can do it for every run, not just the experiment harness.
# ---------------------------------------------------------------------------

_run_log_offsets: dict = {}
_run_log_offsets_lock = _threading.Lock()


def _captains_log_path() -> Optional[Path]:
    """Locate the captain's log file. None if captains_log isn't available."""
    try:
        from captains_log import _log_path  # type: ignore
        return _log_path()
    except Exception:
        return None


def record_log_offset(handle_id: str) -> None:
    """Record the captain's log byte-length at run start.

    Call this once at the top of handle() *after* the run-dir exists.
    Pairs with `slice_log_for_run()` at finalize.
    """
    log_path = _captains_log_path()
    if log_path is None:
        return
    try:
        offset = log_path.stat().st_size if log_path.exists() else 0
    except Exception:
        offset = 0
    with _run_log_offsets_lock:
        _run_log_offsets[handle_id] = offset


def slice_log_for_run(handle_id: str) -> Optional[Path]:
    """Write `<run-dir>/build/captains_log_slice.jsonl` covering this run.

    Reads from the offset recorded by `record_log_offset()` to the
    current end of file. Returns the slice path on success, None on
    failure or when no offset was recorded.
    """
    log_path = _captains_log_path()
    if log_path is None or not log_path.exists():
        return None
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    with _run_log_offsets_lock:
        offset = _run_log_offsets.get(handle_id, 0)
    out = rd / "build" / "captains_log_slice.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open("rb") as src, out.open("wb") as dst:
            src.seek(offset)
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
    except Exception:
        return None
    return out


# ---------------------------------------------------------------------------
# Repo bundle — captures git state at end-of-run so the artifact survives
# downstream resets. Restorable with `git clone repo.bundle`.
# ---------------------------------------------------------------------------

import subprocess
_run_repo_bases: dict = {}
_run_repo_bases_lock = _threading.Lock()


def record_repo_base(handle_id: str, repo_path: str) -> None:
    """Record the current HEAD sha of repo_path so end-of-run can diff against it.

    Call this once at run start when a --repo is supplied. Pairs with
    `snapshot_repo_bundle()` at finalize.
    """
    if not repo_path:
        return
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            with _run_repo_bases_lock:
                _run_repo_bases[handle_id] = (repo_path, result.stdout.strip())
    except Exception:
        pass


def snapshot_repo_bundle(handle_id: str) -> Optional[Path]:
    """Write `<run-dir>/artifact/repo.bundle` + git_log.txt + branch_diff.patch.

    Restorable with `git clone repo.bundle`. Captures the current state
    of the repo paired in `record_repo_base()`. Returns the bundle path
    on success, None if no repo was paired or the snapshot failed.
    """
    with _run_repo_bases_lock:
        pair = _run_repo_bases.get(handle_id)
    if not pair:
        return None
    repo_path, base_sha = pair
    rd = run_dir(handle_id)
    if not rd.exists():
        return None
    out_dir = rd / "artifact"
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle = out_dir / "repo.bundle"
    try:
        subprocess.run(
            ["git", "bundle", "create", str(bundle), "--all"],
            cwd=repo_path, capture_output=True, timeout=60, check=True,
        )
    except Exception:
        return None
    # Best-effort: log + diff. Failures here don't void the bundle.
    try:
        log_out = subprocess.run(
            ["git", "log", "--all", "--graph", "--oneline", "-100"],
            cwd=repo_path, capture_output=True, text=True, timeout=15,
        )
        (out_dir / "git_log.txt").write_text(log_out.stdout, encoding="utf-8")
    except Exception:
        pass
    try:
        diff_out = subprocess.run(
            ["git", "diff", f"{base_sha}..HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=30,
        )
        (out_dir / "branch_diff.patch").write_text(diff_out.stdout, encoding="utf-8")
    except Exception:
        pass
    try:
        (out_dir / "base_sha.txt").write_text(base_sha + "\n", encoding="utf-8")
    except Exception:
        pass
    return bundle
