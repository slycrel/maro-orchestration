from pathlib import Path

import pytest

from audit_policy import persist_delivered_outcome_verdict
from memory_ledger import OutcomeVerdictStampResult


@pytest.mark.parametrize("status", ["updated", "missing"])
def test_success_or_absent_row_allows_learning(monkeypatch, status):
    import memory
    import runs

    monkeypatch.setattr(
        memory, "stamp_outcome_verdict",
        lambda *a, **kw: OutcomeVerdictStampResult(status=status, attempts=1),
    )
    metadata = []
    monkeypatch.setattr(runs, "stamp_run_audit_failure", lambda fields: metadata.append(fields))

    result = persist_delivered_outcome_verdict(
        "loop-a", goal_achieved=True, goal_verdict_source="closure")

    assert result.status == status
    assert result.learning_allowed
    assert result.warning == ""
    assert metadata == []


def test_write_failure_warns_skips_learning_and_records_repair(monkeypatch):
    import memory
    import runs

    monkeypatch.setattr(
        memory, "stamp_outcome_verdict",
        lambda *a, **kw: OutcomeVerdictStampResult(
            status="write_failed", attempts=2, error="disk full"),
    )
    metadata = []
    monkeypatch.setattr(
        runs, "stamp_run_audit_failure",
        lambda fields: (metadata.append(fields), Path("metadata.json"))[1],
    )

    result = persist_delivered_outcome_verdict(
        "loop-b",
        goal_achieved=False,
        goal_verdict_source="closure",
        goal_verdict_confidence=0.91,
        loop_ids=["loop-a", "loop-b", "loop-b"],
    )

    assert not result.learning_allowed
    assert result.repair_metadata_persisted
    assert "AUDIT INCOMPLETE" in result.warning
    assert "disk full after 2 attempt(s)" in result.warning
    assert metadata[0]["audit_incomplete"] is True
    assert metadata[0]["audit_repair_required"] is True
    assert metadata[0]["loop_ids"] == ["loop-a", "loop-b"]
    assert metadata[0]["audit_repair"]["goal_achieved"] is False


def test_stamp_exception_and_metadata_failure_are_both_visible(monkeypatch):
    import memory
    import runs

    monkeypatch.setattr(
        memory, "stamp_outcome_verdict",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("ledger offline")),
    )
    monkeypatch.setattr(runs, "stamp_run_audit_failure", lambda fields: None)

    result = persist_delivered_outcome_verdict(
        "loop-c", goal_achieved=True, goal_verdict_source="closure")

    assert result.status == "exception"
    assert not result.learning_allowed
    assert not result.repair_metadata_persisted
    assert "ledger offline" in result.warning
    assert "Repair metadata also could not be persisted" in result.warning


def test_warning_is_emitted_to_channel(monkeypatch):
    import memory
    import runs

    monkeypatch.setattr(
        memory, "stamp_outcome_verdict",
        lambda *a, **kw: OutcomeVerdictStampResult(
            status="write_failed", attempts=1, error="readonly"),
    )
    monkeypatch.setattr(runs, "stamp_run_audit_failure", lambda fields: Path("meta"))

    class Channel:
        events = []

        def emit(self, event_type, *, text):
            self.events.append((event_type, text))

    channel = Channel()
    result = persist_delivered_outcome_verdict(
        "loop-d", goal_achieved=None,
        goal_verdict_source="closure_unverifiable", channel=channel)

    assert channel.events == [("warning", result.warning)]
