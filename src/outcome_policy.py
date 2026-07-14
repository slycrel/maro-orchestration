"""Neutral policy for deciding which completed outcomes may seed learning.

Two durable representations reach the learning pipeline: curated run cards
carry ``success_class``; raw outcome-ledger rows carry ``status`` and
``goal_achieved``. This leaf owns their shared eligibility rule so callers do
not translate one representation into the other.
"""

from __future__ import annotations

from typing import Any, Mapping


_LEARNABLE_SUCCESS_CLASSES = frozenset(("success", "done-unverified"))


def is_verdict_pending(outcome: Any) -> bool:
    """Return whether a deferred row is still waiting for its goal verdict.

    Agenda outcomes are written before closure verification and carry
    ``lesson_extraction_status=deferred`` until that contract finishes.  An
    unjudged row in that state is not success evidence: it may be the residue
    of a failed closure-verdict write.  Accept mappings and ledger dataclasses
    so every consumer can share the same durable quarantine rule.
    """
    if isinstance(outcome, Mapping):
        achieved = outcome.get("goal_achieved")
        extraction = outcome.get("lesson_extraction_status")
    else:
        achieved = getattr(outcome, "goal_achieved", None)
        extraction = getattr(outcome, "lesson_extraction_status", "")
    return achieved is None and extraction == "deferred"


def is_learnable_outcome(outcome: Mapping[str, Any]) -> bool:
    """Return whether ``outcome`` is safe as a successful learning example.

    A record containing ``success_class`` is treated as curated and must carry
    a recognized learnable class. Unknown/empty classifications fail closed;
    they never fall back to a possibly stale raw process status.
    """
    if "success_class" in outcome:
        return outcome.get("success_class") in _LEARNABLE_SUCCESS_CLASSES
    return (
        outcome.get("status") == "done"
        and outcome.get("goal_achieved") is not False
        and not is_verdict_pending(outcome)
    )
