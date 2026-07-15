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
        # ``loop_ids`` is not a durable run-index key, so loop references need
        # a scan. Manual targeting searches all runs rather than reporting a
        # false success for an older record.
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
