"""Idempotent convergence for delivered verdict audits that failed to persist.

The delivery path records an exact outcome-verdict patch in run metadata and
quarantines deferred learning.  This module replays that patch later, resumes
only the named outcome row's lesson/knowledge extraction, and clears the audit
flags only after both durable stages succeed.

One workspace-wide nonblocking pidfile serializes manual and heartbeat sweeps.
The exact repair record is treated as untrusted persisted input: malformed or
mismatched records remain quarantined and are reported, never guessed at.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import time
import threading
from pathlib import Path
from typing import Callable, List, Optional


log = logging.getLogger("maro.audit_repair")

_REPAIR_LOCK = "audit-repair"
_AUTO_FAILURE_LIMIT = 5
_FAILURE_STATUSES = {
    "invalid", "verdict_failed", "outcome_missing", "learning_pending",
    "learning_failed", "metadata_failed", "surface_failed",
}


@dataclass(frozen=True)
class PendingAudit:
    handle_id: str
    run_dir: Path
    metadata: dict
    repair: dict
    surface_only: bool = False


@dataclass(frozen=True)
class AuditRepairItemResult:
    handle_id: str
    loop_id: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class AuditRepairSweepResult:
    status: str  # completed | not_found | busy | unavailable
    items: tuple[AuditRepairItemResult, ...] = ()
    error: str = ""

    @property
    def unresolved(self) -> int:
        return sum(1 for item in self.items if item.status != "repaired")

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "repaired": sum(1 for item in self.items if item.status == "repaired"),
            "unresolved": self.unresolved,
            "error": self.error,
            "items": [asdict(item) for item in self.items],
        }


def _read_metadata(run_dir: Path) -> Optional[dict]:
    try:
        value = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _reconciliation(repair: dict) -> dict:
    """Narrow untrusted nested reconciliation state at the read boundary."""
    value = repair.get("reconciliation")
    return value if isinstance(value, dict) else {}


def _repair_items(meta: dict) -> List[dict]:
    """Load and de-duplicate the untrusted per-loop repair queue."""
    raw = meta.get("audit_repairs")
    items = [dict(item) for item in raw if isinstance(item, dict)] \
        if isinstance(raw, list) else []
    if not items:
        legacy = meta.get("audit_repair")
        items = [dict(legacy)] if isinstance(legacy, dict) else [{}]
    deduped: List[dict] = []
    positions = {}
    for item in items:
        token = (item.get("loop_id"), item.get("recorded_at"))
        if token in positions:
            deduped[positions[token]] = item
        else:
            positions[token] = len(deduped)
            deduped.append(item)
    return deduped


def _candidate(run_dir: Path, *, loop_ref: str = "") -> Optional[PendingAudit]:
    meta = _read_metadata(run_dir)
    if meta is None:
        return None
    # Repair only finalized runs. Delivery can write the quarantine record
    # before closure/escalation finishes, and clearing it mid-run can race a
    # later verdict for another loop.
    if not meta.get("ended_at"):
        return None
    surface_only = (
        not bool(meta.get("audit_repair_required"))
        and meta.get("audit_repair_status") in ("surface_pending", "surface_failed")
    )
    if not meta.get("audit_repair_required") and not surface_only:
        return None
    repair_items = _repair_items(meta)
    unresolved = [
        item for item in repair_items
        if _reconciliation(item).get("status") != "completed"
    ]
    if loop_ref:
        repair = next(
            (item for item in repair_items if item.get("loop_id") == loop_ref),
            unresolved[0] if unresolved else repair_items[-1],
        )
    else:
        repair = next(
            (
                item for item in unresolved
                if _reconciliation(item).get("auto_exhausted") is not True
            ),
            unresolved[0] if unresolved else repair_items[-1],
        )
    handle_id = str(meta.get("handle_id") or run_dir.name.split("-", 1)[0])
    return PendingAudit(handle_id, run_dir, meta, repair, surface_only)


def find_pending_audits(
    *, handle_ref: str = "", limit: int = 20,
) -> List[PendingAudit]:
    """Return a fair bounded batch, optionally targeting one run reference."""
    from runs import resolve_run_dir, runs_root

    if handle_ref:
        run_dir = resolve_run_dir(handle_ref)
        candidate = _candidate(run_dir, loop_ref=handle_ref) if run_dir is not None else None
        if candidate is not None:
            return [candidate]
        # ``loop_ids`` became a durable run-index key in run-ref-index v2
        # (2026-07-29); the scan below stays as the safety net for runs that
        # crashed before their loop was stamped. Manual targeting searches
        # all runs rather than reporting a false success for an older record.
        root = runs_root()
        if not root.is_dir():
            return []
        for path in root.iterdir():
            if not path.is_dir():
                continue
            fallback = _candidate(path, loop_ref=handle_ref)
            if fallback is not None and (
                fallback.handle_id == handle_ref
                or fallback.repair.get("loop_id") == handle_ref
            ):
                return [fallback]
        return []

    root = runs_root()
    if not root.is_dir():
        return []
    pending: List[PendingAudit] = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        candidate = _candidate(run_dir)
        reconciliation = _reconciliation(candidate.repair) \
            if candidate is not None else {}
        if candidate is not None and reconciliation.get("auto_exhausted") is not True:
            pending.append(candidate)
    # Stable persisted timestamps, not run-dir mtime: metadata rewrites must
    # never promote poison records ahead of repairable work.
    pending.sort(key=lambda item: (
        str(_reconciliation(item.repair).get("last_attempt_at") or ""),
        str(item.repair.get("recorded_at") or item.metadata.get("started_at") or ""),
        item.handle_id,
    ))
    return pending[:max(1, int(limit))]


def _validated_patch(candidate: PendingAudit) -> tuple[Optional[dict], str]:
    repair = candidate.repair
    if repair.get("kind") != "outcome_verdict_stamp":
        return None, "repair kind is missing or unsupported"
    loop_id = repair.get("loop_id")
    source = repair.get("goal_verdict_source")
    achieved = repair.get("goal_achieved")
    confidence = repair.get("goal_verdict_confidence")
    if not isinstance(loop_id, str) or not loop_id.strip():
        return None, "repair loop_id is missing"
    if not isinstance(source, str) or not source.strip():
        return None, "repair goal_verdict_source is missing"
    if achieved is not None and not isinstance(achieved, bool):
        return None, "repair goal_achieved is not boolean/null"
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            return None, "repair confidence is not numeric/null"
        confidence = float(confidence)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            return None, "repair confidence is outside 0..1"
    loop_ids = candidate.metadata.get("loop_ids")
    if not isinstance(loop_ids, list) or not loop_ids or loop_id not in loop_ids:
        return None, "repair loop_id is not joined to this run"
    return {
        "loop_id": loop_id,
        "goal_achieved": achieved,
        "goal_verdict_source": source,
        "goal_verdict_confidence": confidence,
    }, ""


def _update_metadata(
    candidate: PendingAudit,
    *,
    status: str,
    error: str = "",
    clear_flags: bool = False,
    verdict_patch: Optional[dict] = None,
) -> tuple[bool, bool, bool]:
    """Return ``(written, all_records_complete, manual_action_required)``.

    The repair's loop id + recorded timestamp form a fencing token. A newer
    audit record may arrive during paid extraction; this merge updates only
    the snapshotted record and never clears another loop's quarantine.
    """
    from file_lock import locked_rmw

    path = candidate.run_dir / "metadata.json"
    updated = {"ok": False}
    now = datetime.now(timezone.utc).isoformat()

    def _transform(old: str) -> str:
        try:
            meta = json.loads(old)
        except (ValueError, TypeError):
            return old
        if not isinstance(meta, dict):
            return old
        repair = dict(candidate.repair)
        repair_items = _repair_items(meta)
        target = None
        for index, item in enumerate(repair_items):
            if (
                item.get("loop_id") == repair.get("loop_id")
                and item.get("recorded_at") == repair.get("recorded_at")
            ):
                target = index
                break
        if target is None:
            return old
        repair = repair_items[target]
        reconciliation = repair.get("reconciliation")
        reconciliation = dict(reconciliation) if isinstance(reconciliation, dict) else {}
        raw_transition_count = reconciliation.get(
            "transition_count", reconciliation.get("attempt_count", 0))
        transition_count = (
            raw_transition_count
            if isinstance(raw_transition_count, int)
            and not isinstance(raw_transition_count, bool)
            and raw_transition_count >= 0
            else 0
        )
        failure_count = reconciliation.get("failure_count", 0)
        if (
            not isinstance(failure_count, int)
            or isinstance(failure_count, bool)
            or failure_count < 0
        ):
            failure_count = 0
        if status in _FAILURE_STATUSES:
            failure_count += 1
        auto_exhausted = (
            status in ("invalid", "outcome_missing")
            or failure_count >= _AUTO_FAILURE_LIMIT
        )
        reconciliation.update({
            "status": status,
            "last_attempt_at": now,
            "transition_count": transition_count + 1,
            "failure_count": failure_count,
            "auto_exhausted": auto_exhausted,
            "error": str(error)[:500],
        })
        reconciliation.pop("attempt_count", None)
        repair["reconciliation"] = reconciliation
        repair_items[target] = repair

        other_pending = any(
            index != target
            and _reconciliation(item).get("status") != "completed"
            for index, item in enumerate(repair_items)
        )
        exhausted_pending = any(
            index != target
            and _reconciliation(item).get("status") != "completed"
            and _reconciliation(item).get("auto_exhausted") is True
            for index, item in enumerate(repair_items)
        )
        effective_status = status
        if status == "surface_pending" and other_pending:
            # This loop is repaired, but a newer/sibling failed loop still
            # owns the run-level quarantine. Do not enter surface-only mode.
            effective_status = "completed"
            repair["reconciliation"]["status"] = effective_status
            repair_items[target] = repair
        all_complete = not other_pending and effective_status in (
            "completed", "surface_pending", "surface_failed")

        meta["audit_repairs"] = repair_items
        # Compatibility view stays the most recently appended record.
        meta["audit_repair"] = repair_items[-1]
        meta["audit_repair_status"] = (
            "manual_required" if exhausted_pending else effective_status)
        # Run metadata describes the latest delivered attempt. Align it as
        # soon as that queue record repairs, even if an older exhausted sibling
        # keeps the run-level learning quarantine in place.
        # SANCTIONED direct verdict-key merge (the bypass census's one
        # inventoried site, reviewed 2026-08-15): this is a partial
        # ALIGNMENT patch — source always, confidence/achieved
        # set-or-popped from the repair record — on a FOREIGN run dir,
        # atomic with the repair-record update above. Routing through
        # runs.stamp_run_verdict would replace the tuple WHOLE and
        # clobber summary/downgrade/gaps/contested that the original
        # stamp legitimately owns; splitting into two locked writes
        # would reintroduce the round-14 non-atomic class.
        align_latest_verdict = (
            verdict_patch is not None and target == len(repair_items) - 1)
        if align_latest_verdict:
            meta["goal_verdict_source"] = verdict_patch["goal_verdict_source"]
            confidence = verdict_patch.get("goal_verdict_confidence")
            if confidence is None:
                meta.pop("goal_verdict_confidence", None)
            else:
                meta["goal_verdict_confidence"] = confidence
            achieved = verdict_patch.get("goal_achieved")
            if achieved is None:
                meta.pop("goal_achieved", None)
            else:
                meta["goal_achieved"] = achieved
        if clear_flags:
            if all_complete:
                meta["audit_incomplete"] = False
                meta["audit_repair_required"] = False
                meta["audit_repaired_at"] = now
                meta.pop("audit_failure_source", None)
                for key in (
                    "goal_verdict_stamp_failed",
                    "goal_verdict_stamp_failed_label",
                    "goal_verdict_stamp_failed_loop_id",
                    "goal_verdict_stamp_failed_detail",
                ):
                    meta.pop(key, None)
            else:
                meta["audit_incomplete"] = True
                meta["audit_repair_required"] = True
        else:
            meta["audit_incomplete"] = True
            meta["audit_repair_required"] = True
        updated["ok"] = True
        updated["all_complete"] = all_complete
        updated["manual_required"] = exhausted_pending
        return json.dumps(meta, indent=2, default=str)

    try:
        locked_rmw(path, _transform)
    except Exception as exc:
        log.error("audit repair metadata update failed for %s: %s", candidate.handle_id, exc)
        return False, False, False
    return (
        updated["ok"],
        bool(updated.get("all_complete")),
        bool(updated.get("manual_required")),
    )


def _refresh_surfaces(candidate: PendingAudit) -> bool:
    try:
        from run_curation import refresh_run_card_classification

        card = refresh_run_card_classification(
            candidate.handle_id, run_dir=candidate.run_dir)
        if card is None:
            return False
        from loop_report import write_reports_for_run_dir

        reports = write_reports_for_run_dir(candidate.run_dir)
        return not bool(reports.get("failed"))
    except Exception as exc:
        log.warning("audit repair surface refresh failed for %s: %s", candidate.handle_id, exc)
        return False


def _finish_surface_repair(candidate: PendingAudit, loop_id: str) -> AuditRepairItemResult:
    if not _refresh_surfaces(candidate):
        _update_metadata(
            candidate, status="surface_failed",
            error="run-card/report refresh failed", clear_flags=True,
        )
        return AuditRepairItemResult(
            candidate.handle_id, loop_id, "surface_failed",
            "verdict and learning repaired; run-card/report refresh remains pending",
        )
    written, _, _ = _update_metadata(
        candidate, status="completed", clear_flags=True)
    if not written:
        return AuditRepairItemResult(
            candidate.handle_id, loop_id, "metadata_failed",
            "surfaces refreshed but completion metadata could not be persisted",
        )
    return AuditRepairItemResult(candidate.handle_id, loop_id, "repaired")


def _repair_one(
    candidate: PendingAudit,
    *,
    adapter_factory: Optional[Callable[[], object]],
) -> AuditRepairItemResult:
    loop_id = str(candidate.repair.get("loop_id") or "")
    if candidate.surface_only:
        # Ledger + learning already converged. Derived surfaces do not need the
        # verdict patch, so corruption here must not move the state backward
        # into full quarantine or replay paid work.
        return _finish_surface_repair(candidate, loop_id)

    patch, invalid = _validated_patch(candidate)
    if patch is None:
        _update_metadata(candidate, status="invalid", error=invalid)
        return AuditRepairItemResult(candidate.handle_id, loop_id, "invalid", invalid)

    from memory import stamp_outcome_verdict

    try:
        stamped = stamp_outcome_verdict(
            patch["loop_id"],
            goal_achieved=patch["goal_achieved"],
            goal_verdict_source=patch["goal_verdict_source"],
            goal_verdict_confidence=patch["goal_verdict_confidence"],
            max_attempts=2,
        )
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        _update_metadata(candidate, status="verdict_failed", error=detail)
        return AuditRepairItemResult(candidate.handle_id, patch["loop_id"], "verdict_failed", detail)
    stamp_status = getattr(stamped, "status", "")
    if stamp_status not in ("updated", "missing", "write_failed"):
        detail = "outcome verdict writer returned an invalid result"
        _update_metadata(candidate, status="verdict_failed", error=detail)
        return AuditRepairItemResult(
            candidate.handle_id, patch["loop_id"], "verdict_failed", detail)
    if stamp_status != "updated":
        detail = getattr(stamped, "error", "") or (
            "outcome row is missing" if stamp_status == "missing"
            else "outcome verdict could not be persisted"
        )
        status = "outcome_missing" if stamp_status == "missing" else "verdict_failed"
        _update_metadata(candidate, status=status, error=detail)
        return AuditRepairItemResult(candidate.handle_id, patch["loop_id"], status, detail)

    from memory_ledger import load_outcome_by_loop_id

    outcome = load_outcome_by_loop_id(patch["loop_id"])
    if outcome is None:
        detail = "outcome disappeared after verdict persistence"
        _update_metadata(candidate, status="outcome_missing", error=detail)
        return AuditRepairItemResult(
            candidate.handle_id, patch["loop_id"], "outcome_missing", detail)

    if not outcome.lessons and outcome.lesson_extraction_status != "completed":
        if adapter_factory is None:
            detail = "deferred learning needs an available adapter"
            _update_metadata(candidate, status="learning_pending", error=detail)
            return AuditRepairItemResult(
                candidate.handle_id, patch["loop_id"], "learning_pending", detail)
        try:
            adapter = adapter_factory()
            if adapter is None:
                raise RuntimeError("deferred learning adapter is unavailable")
            from memory import extract_deferred_lessons

            extract_deferred_lessons(
                patch["loop_id"], adapter=adapter, dry_run=False,
                raise_on_failure=True,
            )
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            _update_metadata(candidate, status="learning_failed", error=detail)
            return AuditRepairItemResult(
                candidate.handle_id, patch["loop_id"], "learning_failed", detail)

        outcome = load_outcome_by_loop_id(patch["loop_id"])
        if outcome is None or outcome.lesson_extraction_status != "completed":
            detail = "deferred learning did not reach durable completed state"
            _update_metadata(candidate, status="learning_failed", error=detail)
            return AuditRepairItemResult(
                candidate.handle_id, patch["loop_id"], "learning_failed", detail)

    # Metadata is the source for run-card classification. Mark a recoverable
    # surface-pending state before touching derived files; a crash at either
    # boundary is picked up by the next sweep.
    written, all_complete, manual_required = _update_metadata(
        candidate,
        status="surface_pending",
        clear_flags=True,
        verdict_patch=patch,
    )
    if not written:
        return AuditRepairItemResult(
            candidate.handle_id, patch["loop_id"], "metadata_failed",
            "verdict and learning repaired but audit flags could not be cleared",
        )
    if not all_complete:
        # This record converged, but a sibling loop still owns the run-level
        # quarantine and will be selected by a later sweep.
        status = "manual_required" if manual_required else "repaired"
        detail = (
            "this loop repaired; an exhausted sibling keeps the run quarantined"
            if manual_required else ""
        )
        return AuditRepairItemResult(
            candidate.handle_id, patch["loop_id"], status, detail)
    return _finish_surface_repair(candidate, patch["loop_id"])


# Async-tail phase 2 crash-orphan sweep. Grace before declaring a still-
# ACTIVE verdict_pending marker orphaned: the tail normally lands in
# minutes (worst measured pre-decree: 8m34s, run 2a3b1f85); an hour of
# active pending on a run with ended_at stamped means the owning process
# died between the answer-first notify and handle()'s finalize, not that
# closure is slow. Observational basis, not a tuning knob.
_ORPHAN_GRACE_S = 3600.0


_DEAD_RUN_GRACE_S = 600.0


def sweep_dead_runs(
    *, grace_s: float = _DEAD_RUN_GRACE_S, limit: int = 20,
    dry_run: bool = False,
) -> dict:
    """Give a run whose owning process died mid-flight an honest terminal
    status (BACKLOG 2026-09-07, runs 38cfec83 / 50dea643 / 2a779342).

    Signature: metadata has a `pid`, no `ended_at`, and that pid is gone.
    A killed worker (operator `kill`, OOM, box reboot) never reaches
    finalize_run, so the record says nothing forever — the rerun brief then
    reads the attempt as "possibly still in flight" and the navigator binds
    the next dispatch to a dead attempt's project. The stamp reuses the
    existing INTERRUPT vocabulary (`stranded` — a stranded owner is exactly
    this) with `stop_verdict: external-interrupt` and the evidence line;
    no goal verdict is invented (a crash is not failure evidence).
    `grace_s` guards the spawn window (pid recorded before the process is
    checkable) and a metadata write that is still in progress: the record
    must be older than the grace. A pid that exists but is not ours is a
    recycled pid — treated as dead, like sweep_verdict_orphans. Serialized
    under the same repair pidfile. Returns counts for the caller's log line.
    """
    from proc_lock import acquire_pidfile
    from runs import runs_root, stamp_run_metadata_for

    root = runs_root()
    if not root.is_dir():
        return {"status": "completed", "stamped": 0, "considered": 0}
    now = time.time()
    candidates = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        meta = _read_metadata(run_dir)
        if meta is None or meta.get("ended_at"):
            continue
        try:
            pid = int(meta.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue  # no owner recorded — nothing to corroborate against
        if _pid_alive(pid):
            continue
        try:
            age_s = now - (run_dir / "metadata.json").stat().st_mtime
        except OSError:
            continue
        if age_s <= grace_s:
            continue
        candidates.append((run_dir, pid))
    if not candidates:
        return {"status": "completed", "stamped": 0, "considered": 0}
    if dry_run:
        return {"status": "dry_run", "stamped": 0,
                "considered": len(candidates),
                "handles": [str((_read_metadata(rd) or {}).get("handle_id")
                                or rd.name.split("-", 1)[0])
                            for rd, _ in candidates[:limit]]}

    acquired = acquire_pidfile(
        _REPAIR_LOCK, payload={"command": "dead-run-sweep"})
    if acquired.status == "busy":
        return {"status": "busy", "stamped": 0, "considered": len(candidates)}
    if acquired.status == "unavailable":
        return {"status": "unavailable", "stamped": 0,
                "error": acquired.error}
    stamped = 0
    handles: List[str] = []
    try:
        for run_dir, pid in candidates[:limit]:
            # Re-read under the lock: finalize may have landed since the scan.
            meta = _read_metadata(run_dir)
            if meta is None or meta.get("ended_at"):
                continue
            if _pid_alive(pid):
                continue
            handle_id = str(
                meta.get("handle_id") or run_dir.name.split("-", 1)[0])
            ended = datetime.now(timezone.utc).isoformat()
            fields = {
                "status": "stranded",
                "ended_at": ended,
                "stop_verdict": "external-interrupt",
                "stop_evidence": (
                    f"owning pid {pid} is gone with no ended_at; stamped by "
                    f"the dead-run sweep at {ended}"),
                "dead_run_sweep": {"pid": pid, "stamped_at": ended},
            }
            if stamp_run_metadata_for(handle_id, fields) is None:
                log.warning("dead-run sweep: stamp failed for %s — retrying "
                            "next sweep", handle_id)
                continue
            stamped += 1
            handles.append(handle_id)
            try:
                from run_curation import refresh_run_card_classification
                from loop_report import write_reports_for_run_dir
                refresh_run_card_classification(handle_id, run_dir=run_dir)
                write_reports_for_run_dir(run_dir)
            except Exception:
                log.debug("dead-run sweep: surface refresh failed for %s",
                          handle_id, exc_info=True)
    finally:
        try:
            acquired.release()
        except Exception:
            pass
    return {"status": "completed", "stamped": stamped,
            "considered": len(candidates), "handles": handles}


def _pid_alive(pid: int) -> bool:
    """True only when `pid` exists AND is ours (a recycled pid owned by
    another user cannot be the run's process). A non-positive pid is no
    run's process either: `os.kill(-1, 0)` signals every process we own
    and reports "alive" (review r14)."""
    try:
        if int(pid) <= 0:
            return False
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except (OverflowError, ValueError):
        # not a pid that can exist — dead (review r14: the untold sweep
        # called this over a `pid: 2**80` record and the OverflowError
        # aborted every later candidate; the verdict sweep's inline check
        # had caught it since r8 — the helper is the shared boundary)
        return False
    except OSError:
        return False


def _pending_settlements() -> Optional[dict]:
    """The settlements a run in THIS process could not write (the run's two
    attempts, then its finalize): `handle._UNSETTLED_TRANSITIONS`, keyed by
    handle id — only when `handle` is loaded here (a process that never
    handled a run has nothing pending; importing it to find out would be
    wrong)."""
    import sys
    mod = sys.modules.get("handle")
    pending = getattr(mod, "_UNSETTLED_TRANSITIONS", None) if mod is not None else None
    return pending if isinstance(pending, dict) else None


def _pending_lock():
    import sys
    mod = sys.modules.get("handle")
    lock = getattr(mod, "_UNSETTLED_LOCK", None) if mod is not None else None
    return lock if lock is not None else threading.Lock()


def reconcile_kept_write(existing: dict, kept: dict) -> dict:
    """A kept write against the store's LOCKED snapshot `existing` — the
    fields still owed, or {} when the store already carries the outcome.
    A kept SETTLEMENT is owed only while the snapshot's transition is
    active with the same `since` (another process's sweep may have
    reverted it; the RESUME lane reuses the handle id for a later
    transition) — otherwise the disk's settlement stands and readers that
    bound to it keep their world. A FINALIZE obligation (`_finalize`)
    also resolves the snapshot's verdict marker when it is still active,
    materialised HERE from the snapshot: the obligation exists without a
    read of its own (review r10: a failed read before the finalize's
    write kept nothing). Runs inside `runs.revise_run_metadata_for`, so
    the decision and the publication share one snapshot (review r9/r10)."""
    out = {k: v for k, v in kept.items() if k not in ("_finalize", "_by", "verdict_pending")}
    t = out.get("project_transition")
    if isinstance(t, dict):
        disk = existing.get("project_transition")
        same = (isinstance(disk, dict) and not disk.get("settled_at")
                and disk.get("since") == t.get("since"))
        if not same:
            for key in ("project", "project_binding", "project_transition"):
                out.pop(key, None)
    if kept.get("_finalize"):
        dvp = existing.get("verdict_pending")
        if isinstance(dvp, dict) and not dvp.get("resolved_at"):
            kvp = kept.get("verdict_pending")
            stamp = (kvp.get("resolved_at") if isinstance(kvp, dict) and kvp.get("resolved_at")
                     else datetime.now(timezone.utc).isoformat())
            out["verdict_pending"] = {**dvp, "resolved_at": stamp}
        if not existing.get("final_notified_at") and not existing.get("story_owed_at"):
            # the finalize whose write failed may not have told its story
            # either (the close and the emit follow the failed write); the
            # untold sweep selects this record — review r15: the drain
            # resolved the marker and no sweep could select the run again
            out["story_owed_at"] = datetime.now(timezone.utc).isoformat()
            # review r20: only a repair knows the owner has finished finalizing.
            out["story_owed_by"] = kept.get("_by", "repair")
    return out


def _refresh_run_surfaces(handle_id: str, run_dir: Path, *, by: str) -> Optional[dict]:
    """Best-effort card + reports refresh after a repair write (the saved
    card is derived from metadata and does not follow it by itself —
    review r11: a drained finalize left `done-verdict-pending` on disk).
    Returns the rebuilt card, None when the refresh failed."""
    try:
        from run_curation import refresh_run_card_classification
        from loop_report import write_reports_for_run_dir
        card = refresh_run_card_classification(handle_id, run_dir=run_dir)
        write_reports_for_run_dir(run_dir)
        return card if isinstance(card, dict) else None
    except Exception:
        log.debug("%s: surface refresh failed for %s", by, handle_id, exc_info=True)
        return None


def _story_payload(handle_id: str, run_dir: Path, meta: Optional[dict], *, by: str) -> dict:
    """The payload a repair tells the run's story with: the card REBUILT
    from the record (`_refresh_run_surfaces`), or — when the rebuild
    fails — the record's own verdict fields re-read AFTER the repair's
    write (review r14/r15: a saved card may predate the verdict; an
    id-only fallback acknowledged an empty story)."""
    card = _refresh_run_surfaces(handle_id, run_dir, by=by)
    if isinstance(card, dict):
        payload = dict(card)
    else:
        fresh = None
        try:
            fresh = _read_metadata(run_dir)
        except Exception:
            fresh = None
        rec = fresh if isinstance(fresh, dict) else (meta if isinstance(meta, dict) else {})
        payload = {"handle_id": handle_id, "status": str(rec.get("status") or ""),
                   "goal": str(rec.get("prompt") or "")[:300],
                   "goal_achieved": rec.get("goal_achieved"),
                   "goal_verdict_source": rec.get("goal_verdict_source")}
    payload.setdefault("handle_id", handle_id)
    return payload


def _drain_pending(pending: dict, lock=None) -> tuple:
    """Write this process's kept finalize writes — decided AND published
    from one locked snapshot (`reconcile_kept_write` inside
    `runs.revise_run_metadata_for`); a store that cannot be read or
    written keeps the obligation (review r10). A finalize obligation over
    a marker whose run carries NO verdict first makes the honest call the
    close's tripwire waited on (`runs.record_finalized_without_verdict`:
    the ledger row stamped never-stamped, the DONE_WITHOUT_VERDICT event)
    — ledger BEFORE the marker, as the verdict sweep does; a ledger stamp
    that raises defers the write (review r12: the drain resolved the
    marker and nothing ever recorded the absence). Caller holds the
    repair pidfile. Returns (retried, dropped)."""
    from runs import (revise_run_metadata_for, run_dir,
                      finalized_without_verdict, record_finalized_without_verdict)
    if lock is None:
        lock = _pending_lock()

    def remove_drained(hid, kept):
        # review r22: I/O may outlive this entry; never consume its replacement.
        with lock:
            if pending.get(hid) is kept:
                pending.pop(hid, None)
                return
        log.info("kept write for %s replaced while draining — the newer obligation stays for the next drain", hid)

    retried = dropped = 0
    with lock:
        ids = list(pending.keys())
    for hid in ids:
        with lock:
            kept = pending.get(hid)
        if not isinstance(kept, dict) or not kept:
            remove_drained(hid, kept)
            continue
        before = None
        if kept.get("_finalize"):
            try:
                before = _read_metadata(run_dir(str(hid)))
            except Exception:
                before = None
            # an unreadable pre-read does not gate the write (the decision
            # is the locked snapshot's — review r10); the honest call is
            # then made after the write, best-effort, as close_run's is
            vp = before.get("verdict_pending") if before else None
            if (isinstance(vp, dict) and not vp.get("resolved_at")
                    and finalized_without_verdict({**before, "verdict_pending": None})):
                if not record_finalized_without_verdict(
                        str(hid), before, status=str(before.get("status") or "")):
                    log.warning("kept-write drain: unverdicted-ledger stamp for %s "
                                "failed — retrying next time", hid)
                    continue
        written = revise_run_metadata_for(
            str(hid), lambda existing, _k=kept: reconcile_kept_write(existing, {**_k, "_by": "repair"}))
        if written is None:
            log.warning("kept-write drain: kept write for %s still not "
                        "readable/writable — retrying next time", hid)
            continue
        remove_drained(hid, kept)
        if not written:
            dropped += 1
            log.warning("kept-write drain: kept write for %s dropped — the store "
                        "already carries its outcome (%s)", hid,
                        (kept.get("project_transition") or {}).get("outcome") or "marker")
            continue
        retried += 1
        log.info("kept-write drain: kept write for %s written (%s)",
                 hid, ", ".join(sorted(written)))
        if before is None and "verdict_pending" in written:
            try:
                after = _read_metadata(run_dir(str(hid)))
                if after and finalized_without_verdict(after):
                    record_finalized_without_verdict(
                        str(hid), after, status=str(after.get("status") or ""))
            except Exception:
                log.debug("kept-write drain: post-write unverdicted record for %s "
                          "failed", hid, exc_info=True)
        try:
            _refresh_run_surfaces(str(hid), run_dir(str(hid)), by="kept-write drain")
        except Exception:
            log.debug("kept-write drain: run dir for %s unavailable for the "
                      "surface refresh", hid, exc_info=True)
    return retried, dropped


def drain_kept_writes() -> dict:
    """This process's kept finalize writes, drained NOW — the entry point
    for a long-lived host that is not the heartbeat (`handle()` calls it on
    entry; review r11: the heartbeat's sweep runs only in the heartbeat
    process, so a listener holding an obligation never retried it).
    Serialized under the repair pidfile; busy → nothing this time."""
    from proc_lock import acquire_pidfile
    pending = _pending_settlements()
    if not pending:
        return {"status": "completed", "retried": 0, "dropped": 0}
    acquired = acquire_pidfile(
        _REPAIR_LOCK, payload={"command": "kept-write-drain"})
    if acquired.status != "acquired":
        return {"status": acquired.status, "retried": 0, "dropped": 0}
    try:
        retried, dropped = _drain_pending(pending, _pending_lock())
        return {"status": "completed", "retried": retried, "dropped": dropped}
    finally:
        try:
            acquired.handle.close()
        except Exception:
            pass


def sweep_transition_orphans(
    *, grace_s: float = _ORPHAN_GRACE_S, limit: int = 20,
) -> dict:
    """Settle project transitions the run's own lifecycle could not.

    Two sources, in order. (1) In-process: settlements kept in
    `handle._UNSETTLED_TRANSITIONS` because both the run's writes and its
    finalize's failed — written now with their INTENDED outcome (an
    adopted retry stays adopted) and dropped only once written (review
    2026-09-13 round 8: the finalize used to drop the entry before its
    write, and a long-lived worker whose handle had finished kept every
    later sweep away by being alive). (2) On disk: runs with `ended_at`,
    an ACTIVE `project_transition` and a verdict marker that is NOT active
    (resolved, or never written — `notify.verdict_followup` off); an
    active marker belongs to the verdict sweep, which reverts the
    transition itself. In this domain the handle has FINISHED — `ended_at`
    without an active marker is the finalize's close — so the recorded pid
    is the host process, not the handle: no liveness test, only the grace
    (a settlement in flight from the finalize is already on disk or in
    (1)). The revert is `landscape.settle_project_transition` (the
    delivered project restored in the same write). A kept write is
    decided from the locked snapshot (`reconcile_kept_write` inside
    `runs.revise_run_metadata_for`) and a handle whose kept write still
    fails is left out of the disk pass. Serialized under the repair
    pidfile."""
    from proc_lock import acquire_pidfile
    from runs import runs_root, stamp_run_metadata_for
    from landscape import settle_project_transition

    def _transition_only(meta: dict) -> bool:
        vp = meta.get("verdict_pending")
        if isinstance(vp, dict) and not vp.get("resolved_at"):
            return False
        t = meta.get("project_transition")
        return isinstance(t, dict) and not t.get("settled_at")

    pending = _pending_settlements() or {}
    root = runs_root()
    candidates = []
    if root.is_dir():
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            meta = _read_metadata(run_dir)
            if meta is None or not meta.get("ended_at") or not _transition_only(meta):
                continue
            candidates.append(run_dir)
    if not candidates and not pending:
        return {"status": "completed", "stamped": 0, "considered": 0, "retried": 0,
                "dropped": 0}
    acquired = acquire_pidfile(
        _REPAIR_LOCK, payload={"command": "transition-orphan-sweep"})
    if acquired.status == "busy":
        return {"status": "busy", "stamped": 0, "retried": 0, "dropped": 0}
    if acquired.status == "unavailable":
        return {"status": "unavailable", "stamped": 0, "retried": 0, "dropped": 0,
                "error": acquired.error}
    stamped = considered = retried = dropped = 0
    try:
        retried, dropped = _drain_pending(pending, _pending_lock())
        now = time.time()
        for run_dir in candidates:
            if stamped >= max(1, int(limit)):
                break
            meta = _read_metadata(run_dir)
            if meta is None or not meta.get("ended_at") or not _transition_only(meta):
                continue
            handle_id = str(meta.get("handle_id") or run_dir.name.split("-", 1)[0])
            if handle_id in pending:
                # its settlement is still queued HERE (the drain above could
                # not write it): one sweep, one outcome — the disk fallback
                # would publish the opposite settlement and the next drain
                # would flip it back (review r9)
                continue
            considered += 1
            t = meta["project_transition"]
            try:
                since = datetime.fromisoformat(
                    str(t.get("since", "")).replace("Z", "+00:00"))
                age_s = now - since.timestamp()
            except (TypeError, ValueError):
                age_s = grace_s + 1
            if age_s <= grace_s:
                continue
            fields = settle_project_transition(meta, by="transition_orphan_sweep")
            if not fields:
                continue
            if stamp_run_metadata_for(handle_id, fields) is None:
                log.warning("transition-orphan sweep: revert write failed for %s "
                            "— retrying next sweep", handle_id)
                continue
            stamped += 1
            _refresh_run_surfaces(handle_id, run_dir, by="transition-orphan sweep")
            log.info("transition-orphan sweep: %s reverted %s → %s (aged %.0fs)",
                     handle_id, t.get("to"), fields.get("project"), age_s)
        return {"status": "completed", "stamped": stamped, "considered": considered,
                "retried": retried, "dropped": dropped}
    finally:
        try:
            acquired.handle.close()
        except Exception:
            pass


def sweep_untold_finalizes(
    *, grace_s: float = _ORPHAN_GRACE_S, limit: int = 20,
) -> dict:
    """Tell the story of a finished run whose telling was never recorded.
    The final close stamps `finalized_at`; the finalize's emit comes
    AFTER (curation, then the notify) and records `final_notified_at`
    only when the hook ran cleanly or no hook is owed. A run with the
    first and not the second is untold: the process died between the
    close and the emit, or a configured hook failed — and its verdict
    marker is usually RESOLVED by then, so the verdict sweep never
    revisits it (review 2026-09-13 r13). The obligation is the story,
    independent of the marker. A crash-orphan the verdict sweep repaired
    never had a final close: its resolution write carries `story_owed_at`
    in the SAME write (review r14: the sweep's own hook failure, or its
    death after resolving, left a story no sweep would select again) —
    that record is this sweep's other candidate.

    Gates: a live owner pid within the grace is a finalize still in its
    curation — leave it; a dead owner, or an aged one (a record that
    failed to stamp after a clean emit — a repeated story is the accepted
    direction, a missing one is not) is told here. Routed like the
    finalize: the early answer reached the user → `run_verdict`, else the
    full `run_completed`. The payload is the card REBUILT from the
    record, never the saved card as found: the final close precedes the
    curation, so a death between them leaves the answer-first card on
    disk (review r14: the sweep delivered `done-verdict-pending` as the
    final story over a judged verdict); when the rebuild fails the
    payload is the record's own verdict fields. `limit` bounds ATTEMPTS
    (a failed configured hook counts — review r14: an outage of N hooks
    held the heartbeat and the repair pidfile for N timeouts), and the
    order is never-attempted first, then the oldest attempt (a failure
    stamps `final_notify_attempted_at`), so a failing row does not shadow
    the rows behind it. Serialized under the repair pidfile."""
    from proc_lock import acquire_pidfile
    from runs import runs_root, stamp_run_metadata_for

    def _untold(meta: dict) -> bool:
        vp = meta.get("verdict_pending")
        if isinstance(vp, dict) and not vp.get("resolved_at"):
            # an ACTIVE marker is the verdict sweep's: its story is told
            # on resolution (review r15: telling the pending card first
            # acknowledged it, and the resolved verdict was then never told)
            return False
        return bool(meta.get("ended_at")
                    and (meta.get("finalized_at") or meta.get("story_owed_at"))
                    and not meta.get("final_notified_at"))

    def _since(meta: dict) -> str:
        return str(meta.get("finalized_at") or meta.get("story_owed_at") or "")

    root = runs_root()
    candidates = []
    if root.is_dir():
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            meta = _read_metadata(run_dir)
            if meta is None or not _untold(meta):
                continue
            candidates.append((str(meta.get("final_notify_attempted_at") or ""),
                               _since(meta), run_dir))
    candidates = [rd for _a, _s, rd in sorted(candidates, key=lambda c: (c[0], c[1]))]
    if not candidates:
        return {"status": "completed", "told": 0, "considered": 0}
    acquired = acquire_pidfile(
        _REPAIR_LOCK, payload={"command": "untold-finalize-sweep"})
    if acquired.status == "busy":
        return {"status": "busy", "told": 0}
    if acquired.status == "unavailable":
        return {"status": "unavailable", "told": 0, "error": acquired.error}
    told = considered = attempted = 0
    try:
        from notify import tell, early_reached
        now = time.time()
        for run_dir in candidates:
            if attempted >= max(1, int(limit)):
                break
            meta = _read_metadata(run_dir)
            if meta is None or not _untold(meta):
                continue
            considered += 1
            handle_id = str(meta.get("handle_id") or run_dir.name.split("-", 1)[0])
            try:
                since = datetime.fromisoformat(_since(meta).replace("Z", "+00:00"))
                age_s = now - since.timestamp()
            except (TypeError, ValueError):
                age_s = grace_s + 1
            repaired_story = (meta.get("story_owed_at")
                              and meta.get("story_owed_by", "repair") == "repair")
            if age_s <= grace_s and not repaired_story:
                # review r20: an owner-owed story can still change before final close.
                # Legacy unattributed stories keep repair's duplicate-over-missing rule.
                try:
                    _pid = int(meta.get("pid") or 0)
                except (TypeError, ValueError):
                    _pid = 0
                if _pid > 0 and _pid_alive(_pid):
                    continue  # its finalize is still telling it
            payload = _story_payload(handle_id, run_dir, meta, by="untold-finalize sweep")
            vp = meta.get("verdict_pending")
            vp = vp if isinstance(vp, dict) else {}
            kind = "run_verdict" if early_reached(vp) else "run_completed"
            attempted += 1
            try:
                owed = not tell(kind, payload, run_dir=str(run_dir))
            except Exception:
                # a telling that raises told nobody — owed, whatever the channel
                log.debug("untold-finalize sweep: tell raised for %s", handle_id, exc_info=True)
                owed = True
            if owed:
                log.warning("untold-finalize sweep: the owed channel did not "
                            "acknowledge %s for %s — still owed", kind, handle_id)
                stamp_run_metadata_for(handle_id, {
                    "final_notify_attempted_at": datetime.now(timezone.utc).isoformat()})
                continue
            if stamp_run_metadata_for(handle_id, {
                    "final_notified_at": datetime.now(timezone.utc).isoformat(),
                    "final_notified_by": "untold_finalize_sweep"}) is None:
                log.warning("untold-finalize sweep: told %s for %s but could not record "
                            "it — it may be told again", kind, handle_id)
            told += 1
            log.info("untold-finalize sweep: told %s for %s (finalized %.0fs ago)",
                     kind, handle_id, age_s)
        return {"status": "completed", "told": told, "considered": considered}
    finally:
        try:
            acquired.handle.close()
        except Exception:
            pass


def sweep_verdict_orphans(
    *, grace_s: float = _ORPHAN_GRACE_S, limit: int = 20,
) -> dict:
    """Stamp crash-orphaned verdict_pending runs as honestly unverdicted.

    A run whose verdict_pending marker is still ACTIVE (no resolved_at)
    past `grace_s` with ended_at stamped is the phase-2 crash signature:
    the user got the answer-first notify, then the process died before
    closure resolved the marker. The repair stamps
    VERDICT_SOURCE_PENDING_ORPHANED (goal_achieved stays None — a crash is
    not failure evidence), resolves the marker LAST (repair-audits
    contract: the flag clears only after the durable stamp), and refreshes
    the run's surfaces. Serialized under the same workspace pidfile as
    reconcile_pending_audits. Returns counts for the caller's log line.
    """
    from proc_lock import acquire_pidfile
    from runs import runs_root

    root = runs_root()
    if not root.is_dir():
        return {"status": "completed", "stamped": 0}
    # Candidate scan WITHOUT the lock: this walks every run's metadata and
    # in the common no-orphans case must not hold the shared repair pidfile
    # while doing it (review 2026-08-13 — audit repairs were delayed behind
    # a full-history read). Candidates re-verify under the lock below.
    candidates = []
    for run_dir in root.iterdir():
        if not run_dir.is_dir():
            continue
        meta = _read_metadata(run_dir)
        if meta is None or not meta.get("ended_at"):
            continue
        vp = meta.get("verdict_pending")
        if not isinstance(vp, dict) or vp.get("resolved_at"):
            continue
        candidates.append(run_dir)
    if not candidates:
        return {"status": "completed", "stamped": 0, "considered": 0}

    acquired = acquire_pidfile(
        _REPAIR_LOCK, payload={"command": "verdict-orphan-sweep"})
    if acquired.status == "busy":
        return {"status": "busy", "stamped": 0}
    if acquired.status == "unavailable":
        return {"status": "unavailable", "stamped": 0,
                "error": acquired.error}
    stamped = 0
    scanned = 0
    try:
        now = time.time()
        for run_dir in candidates:
            if stamped >= max(1, int(limit)):
                break
            # Re-read under the lock — the state may have moved since the
            # unlocked candidate scan.
            meta = _read_metadata(run_dir)
            if meta is None or not meta.get("ended_at"):
                continue
            vp = meta.get("verdict_pending")
            if not isinstance(vp, dict) or vp.get("resolved_at"):
                continue
            # The handle's OWN record that its finalize ran (`finalized_at`,
            # the final close) outranks age and the host pid: the marker's
            # resolution precedes that close, so an active marker on a
            # finalized run is a failed resolving write — recover it now,
            # whatever process hosts it and however young the marker
            # (review r11: a long-lived host that never sweeps kept its
            # finished run out of the landscape for its life). Whether its
            # user story was told is a SEPARATE record — `final_notified_at`,
            # stamped by the finalize after its emit; the final close
            # precedes that emit, so `finalized_at` is not delivery evidence
            # (review r12) — and the epilogue notifies unless it is there.
            _finalized = bool(meta.get("finalized_at"))
            _told = bool(meta.get("final_notified_at"))
            try:
                since = datetime.fromisoformat(
                    str(vp.get("since", "")).replace("Z", "+00:00"))
                age_s = now - since.timestamp()
            except (TypeError, ValueError):
                # An unparseable `since` cannot prove youth — treat as aged
                # (the pid corroboration below still protects a live run).
                age_s = grace_s + 1
            if age_s <= grace_s and not _finalized:
                continue
            # Corroborate death before stamping (review 2026-08-13): the
            # early close stamps ended_at while the tail legitimately still
            # runs, so age alone would falsely orphan a wedged-but-alive
            # tail (LLM backoff, docker hang). The recorded metadata pid
            # alive = not orphaned, however old the marker.
            try:
                _pid = int(meta.get("pid") or 0)
            except (TypeError, ValueError):
                _pid = 0
            if _pid > 0 and not _finalized:
                try:
                    os.kill(_pid, 0)
                    continue  # owning process is alive — let it finish
                except ProcessLookupError:
                    pass  # dead — genuinely orphaned
                except PermissionError:
                    # exists but not ours — a recycled pid; proceed (the
                    # `_pid_alive` convention: every worker on a workspace
                    # runs as the workspace's user, so a pid we cannot
                    # signal is a system process that took the number; the
                    # other reading would leave the run unresolved for that
                    # process's life — review r9 pinned this)
                    pass
                except (OverflowError, ValueError):
                    pass  # not a pid that can exist — dead (review r8: one
                    #       malformed record aborted the whole sweep)
                except OSError:
                    pass
            scanned += 1
            handle_id = str(
                meta.get("handle_id") or run_dir.name.split("-", 1)[0])
            from stop_verdicts import VERDICT_SOURCE_PENDING_ORPHANED
            from runs import stamp_run_metadata_for

            def _finish(run_dir=run_dir, handle_id=handle_id, vp=vp,
                        meta=meta, notify=True):
                # Shared repair epilogue: surfaces + the OWED notify — the
                # crashed process never sent its follow-up (review
                # 2026-08-13: repair must finish the user-visible story,
                # not just the ledger). Routed exactly like handle's
                # finalize: answer already reached the user → run_verdict;
                # otherwise the full run_completed.
                if not notify:
                    _refresh_run_surfaces(handle_id, run_dir, by="verdict-orphan sweep")
                    return
                try:
                    from notify import tell, early_reached
                    reached = early_reached(vp)
                    # the rebuilt card, or the record as resolved just now
                    # (review r15: an id-only fallback acknowledged an
                    # empty story)
                    payload = _story_payload(handle_id, run_dir, meta,
                                             by="verdict-orphan sweep")
                    kind = "run_verdict" if reached else "run_completed"
                    if tell(kind, payload, run_dir=str(run_dir)):
                        # recorded like the finalize's own telling, so the
                        # untold-finalize sweep does not repeat it (r13)
                        stamp_run_metadata_for(handle_id, {
                            "final_notified_at": datetime.now(timezone.utc).isoformat(),
                            "final_notified_by": "verdict_orphan_sweep"})
                    else:
                        log.warning("verdict-orphan sweep: the owed channel did not "
                                    "acknowledge %s for %s — still owed (story_owed_at "
                                    "selects it for the untold sweep)", kind, handle_id)
                except Exception:
                    log.debug("verdict-orphan sweep: owed notify failed for "
                              "%s", handle_id, exc_info=True)

            def _story_owed(told=_told):
                # The owed story is recorded IN the resolution write (review
                # r14): a run that never had a final close has no
                # `finalized_at`, so when the epilogue's hook fails — or
                # this process dies after resolving — the untold-finalize
                # sweep selects it by this record; `final_notified_at`
                # (stamped by the epilogue on delivery) retires it.
                return {} if told else {
                    "story_owed_at": datetime.now(timezone.utc).isoformat(),
                    "story_owed_by": "repair"}

            # Re-read immediately before deciding: a verdict may have landed
            # since the scan's read (narrow but real TOCTOU vs a finishing
            # tail that the pid check raced; review 2026-08-13).
            meta = _read_metadata(run_dir) or meta
            if meta.get("goal_verdict_source"):
                # Closure DID stamp a verdict and the process died between
                # that and the marker resolution — the verdict is real;
                # orphan-stamping over it would erase a judged source.
                # Resolve the marker only, then finish the surfaces + notify.
                # A crash mid-escalation left the provisional retry project
                # as the record: the delivered work is the pre-move project,
                # restored in the SAME write that settles the run (review
                # 2026-09-13 round 6 — resolving the marker alone made the
                # abandoned retry directory the landscape's destination).
                from landscape import settle_project_transition as _settle_pt
                resolved_path = stamp_run_metadata_for(
                    handle_id, {"verdict_pending": {
                        **vp,
                        "resolved_at": datetime.now(timezone.utc).isoformat(),
                        "resolved_by": "verdict_orphan_sweep(verdict-present)",
                    }, **_story_owed(), **_settle_pt(meta, by="verdict_orphan_sweep")})
                if resolved_path is None:
                    log.warning("verdict-orphan sweep: resolve-only write "
                                "failed for %s — retrying next sweep",
                                handle_id)
                    continue
                stamped += 1
                _finish(notify=not _told)
                continue
            loop_id = str(vp.get("loop_id") or "")
            if not loop_id:
                lids = meta.get("loop_ids") or []
                loop_id = str(lids[-1]) if lids else ""
            ledger_ok = True
            if loop_id:
                try:
                    from memory_ledger import stamp_outcome_verdict
                    res = stamp_outcome_verdict(
                        loop_id,
                        goal_achieved=None,
                        goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED,
                    )
                    # "missing" is acceptable BY DESIGN: no outcome row
                    # exists (the run died before reflect_and_record), so
                    # there is nothing in the ledger to mislead learning —
                    # the metadata stamp below is the durable record.
                    ledger_ok = res.status in ("updated", "missing")
                except Exception:
                    ledger_ok = False
            if not ledger_ok:
                # Durable stamp failed — leave the marker ACTIVE so the next
                # sweep retries (never clear the flag first).
                log.warning("verdict-orphan sweep: ledger stamp failed for "
                            "%s (loop %s) — retrying next sweep",
                            handle_id, loop_id[:8])
                continue
            from landscape import settle_project_transition as _settle_pt
            fields = {"verdict_pending": {
                **vp,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "resolved_by": "verdict_orphan_sweep",
            }, "goal_verdict_source": VERDICT_SOURCE_PENDING_ORPHANED,
                **_story_owed(), **_settle_pt(meta, by="verdict_orphan_sweep")}
            if stamp_run_metadata_for(handle_id, fields) is None:
                # Metadata write failed: marker stays ACTIVE, next sweep
                # retries (the ledger re-stamp is idempotent — same source,
                # achieved stays None).
                log.warning("verdict-orphan sweep: metadata resolve failed "
                            "for %s — retrying next sweep", handle_id)
                continue
            stamped += 1
            _finish(notify=not _told)
            log.info("verdict-orphan sweep: %s stamped %s (marker aged %.0fs)",
                     handle_id, VERDICT_SOURCE_PENDING_ORPHANED, age_s)
        return {"status": "completed", "stamped": stamped,
                "considered": scanned}
    finally:
        try:
            acquired.handle.close()
        except Exception:
            pass


def reconcile_pending_audits(
    *,
    handle_ref: str = "",
    limit: int = 10,
    adapter_factory: Optional[Callable[[], object]] = None,
) -> AuditRepairSweepResult:
    """Repair pending audits under one nonblocking workspace-wide lock."""
    from proc_lock import acquire_pidfile

    acquired = acquire_pidfile(
        _REPAIR_LOCK,
        payload={"command": "audit-repair", "handle_ref": handle_ref},
    )
    if acquired.status == "busy":
        return AuditRepairSweepResult("busy")
    if acquired.status == "unavailable":
        return AuditRepairSweepResult("unavailable", error=acquired.error)

    try:
        pending = find_pending_audits(handle_ref=handle_ref, limit=limit)
        if handle_ref and not pending:
            return AuditRepairSweepResult(
                "not_found", error="no pending audit repair matched the reference")
        cached_adapter = {"loaded": False, "value": None, "error": None}

        def _cached_adapter_factory():
            if not cached_adapter["loaded"]:
                cached_adapter["loaded"] = True
                try:
                    cached_adapter["value"] = adapter_factory()
                except Exception as exc:
                    cached_adapter["error"] = exc
            if cached_adapter["error"] is not None:
                raise cached_adapter["error"]
            return cached_adapter["value"]

        repair_factory = (
            _cached_adapter_factory if adapter_factory is not None else None)
        items = tuple(
            _repair_one(candidate, adapter_factory=repair_factory)
            for candidate in pending
        )
        return AuditRepairSweepResult("completed", items=items)
    finally:
        try:
            acquired.handle.close()
        except Exception:
            pass
