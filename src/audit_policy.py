"""Owner-visible policy for delivered results whose verdict audit cannot persist.

Rejected/superseded attempts are a different boundary and fail closed before a
replacement run starts.  This module covers the result we are actually about
to deliver: preserve genuine work, make the audit gap prominent, quarantine
learning by telling the caller not to finalize it, and retain an exact repair
record in run metadata when that store is still writable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Iterable, Literal, Optional


log = logging.getLogger("maro.audit_policy")


@dataclass(frozen=True)
class DeliveredVerdictAudit:
    """Outcome of applying a delivered result's verdict audit policy."""

    status: Literal["updated", "missing", "write_failed", "exception"]
    warning: str = ""
    repair_metadata_persisted: bool = False

    @property
    def learning_allowed(self) -> bool:
        return self.status in ("updated", "missing")

    def __bool__(self) -> bool:
        raise TypeError(
            "DeliveredVerdictAudit has no truth value; inspect .learning_allowed")


def persist_delivered_outcome_verdict(
    loop_id: str,
    *,
    goal_achieved: Optional[bool],
    goal_verdict_source: str,
    goal_verdict_confidence: Optional[float] = None,
    loop_ids: Optional[Iterable[str]] = None,
    channel=None,
) -> DeliveredVerdictAudit:
    """Persist a delivered verdict or return a prominent audit warning.

    ``missing`` is an honest no-op: there is no optional outcome row to repair.
    A present row that cannot be updated remains deferred/pending and callers
    must skip deferred learning.  The delivered work itself is not demoted.
    """
    attempts = 0
    error = ""
    try:
        from memory import stamp_outcome_verdict

        stamp = stamp_outcome_verdict(
            loop_id,
            goal_achieved=goal_achieved,
            goal_verdict_source=goal_verdict_source,
            goal_verdict_confidence=goal_verdict_confidence,
            max_attempts=2,
        )
        attempts = int(stamp.attempts or 0)
        error = str(stamp.error or "")
        if stamp.status in ("updated", "missing"):
            return DeliveredVerdictAudit(status=stamp.status)
        status = stamp.status
    except Exception as exc:
        status = "exception"
        error = str(exc) or type(exc).__name__

    detail = error or "outcome verdict was not updated"
    if attempts:
        detail += f" after {attempts} attempt(s)"
    warning = (
        "AUDIT INCOMPLETE: the delivered result is preserved, but its outcome "
        f"verdict ({goal_verdict_source}) could not be persisted for loop "
        f"{loop_id or '(unknown)'} "
        f"({detail}). Learning from this result was skipped; audit repair is required."
    )

    repair = {
        "kind": "outcome_verdict_stamp",
        "loop_id": loop_id,
        "goal_achieved": goal_achieved,
        "goal_verdict_source": goal_verdict_source,
        "goal_verdict_confidence": goal_verdict_confidence,
        "stamp_status": status,
        "attempts": attempts,
        "error": error,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_persisted = False
    try:
        from runs import stamp_run_audit_failure

        path = stamp_run_audit_failure({
            "audit_incomplete": True,
            "audit_repair_required": True,
            "audit_failure_source": "outcome_verdict_stamp",
            "audit_repair": repair,
            "loop_ids": list(dict.fromkeys(loop_ids or ([loop_id] if loop_id else []))),
            # Compatibility breadcrumbs from the first EXT-AUDIT-2 landing.
            # Keep these flat fields for existing readers while ``audit_repair``
            # remains the canonical, exact idempotent repair description.
            "goal_verdict_stamp_failed": True,
            "goal_verdict_stamp_failed_label": goal_verdict_source,
            "goal_verdict_stamp_failed_loop_id": loop_id,
            "goal_verdict_stamp_failed_detail": detail[:300],
        })
        metadata_persisted = path is not None
    except Exception as exc:
        log.error("audit repair metadata raised for loop %s: %s", loop_id, exc)

    if not metadata_persisted:
        warning += " Repair metadata also could not be persisted."
    log.error("%s", warning)
    if channel is not None:
        try:
            channel.emit("warning", text=warning)
        except Exception:
            log.debug("audit warning channel emission failed", exc_info=True)
    return DeliveredVerdictAudit(
        status=status,
        warning=warning,
        repair_metadata_persisted=metadata_persisted,
    )
