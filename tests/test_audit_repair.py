from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import runs
from audit_repair import (
    AuditRepairItemResult,
    AuditRepairSweepResult,
    find_pending_audits,
    reconcile_pending_audits,
)
from memory import record_outcome
from memory_ledger import OutcomeVerdictStampResult, load_outcome_by_loop_id
from run_curation import curate_run


class LessonAdapter:
    def complete(self, messages, **kwargs):
        return SimpleNamespace(content=json.dumps([
            {"lesson": "repair audit state before learning", "type": "verification"}
        ]))


class FailingAdapter:
    def complete(self, messages, **kwargs):
        raise RuntimeError("learning backend unavailable")


def _seed_pending(
    handle_id: str = "a0000001",
    loop_id: str = "loop-audit-1",
    *,
    achieved=False,
):
    repair = {
        "kind": "outcome_verdict_stamp",
        "loop_id": loop_id,
        "goal_achieved": achieved,
        "goal_verdict_source": "closure",
        "goal_verdict_confidence": 0.91,
        "stamp_status": "write_failed",
        "attempts": 2,
        "error": "disk full",
        "recorded_at": "2026-07-14T00:00:00+00:00",
    }
    rd = runs.create_run_dir(
        handle_id,
        prompt="build the audited thing",
        lane="agenda",
        model="mid",
        extra_metadata={
            "loop_ids": [loop_id],
            "audit_incomplete": True,
            "audit_repair_required": True,
            "audit_failure_source": "outcome_verdict_stamp",
            "audit_repair": repair,
            "goal_verdict_stamp_failed": True,
            "goal_verdict_stamp_failed_label": "closure",
            "goal_verdict_stamp_failed_loop_id": loop_id,
            "goal_verdict_stamp_failed_detail": "disk full",
            "preserve_me": "yes",
        },
    )
    runs.finalize_run(handle_id, status="done")
    record_outcome(
        "build the audited thing", "done", "work delivered",
        lessons=[], loop_id=loop_id, lesson_extraction_status="deferred",
        handle_id=handle_id,
    )
    card = curate_run(handle_id)
    card["maintenance_sentinel"] = "keep"
    (rd / "run_card.json").write_text(json.dumps(card, indent=2))
    return rd


def _metadata(rd):
    return json.loads((rd / "metadata.json").read_text())


def test_repair_replays_verdict_learning_and_derived_surfaces():
    rd = _seed_pending()

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.status == "completed"
    assert result.unresolved == 0
    assert result.items[0].status == "repaired"
    row = load_outcome_by_loop_id("loop-audit-1")
    assert row.goal_achieved is False
    assert row.goal_verdict_source == "closure"
    assert row.lesson_extraction_status == "completed"
    assert row.lessons == ["repair audit state before learning"]

    meta = _metadata(rd)
    assert meta["audit_incomplete"] is False
    assert meta["audit_repair_required"] is False
    assert meta["audit_repair_status"] == "completed"
    assert meta["goal_achieved"] is False
    assert meta["goal_verdict_source"] == "closure"
    assert meta["audit_repair"]["reconciliation"]["status"] == "completed"
    assert meta["preserve_me"] == "yes"
    assert "goal_verdict_stamp_failed" not in meta

    card = json.loads((rd / "run_card.json").read_text())
    assert card["audit_incomplete"] is False
    assert card["audit_repair_required"] is False
    assert card["maintenance_sentinel"] == "keep"
    assert card["decision_prior"]["goal_achieved"] is False
    assert card["decision_prior"]["outcome"] == "done-not-achieved"
    assert card["decision_prior"]["lessons"] == [
        "repair audit state before learning"]

    second = reconcile_pending_audits(adapter_factory=LessonAdapter)
    assert second.items == ()


def test_learning_failure_keeps_quarantine_and_retries_cleanly():
    rd = _seed_pending()

    first = reconcile_pending_audits(adapter_factory=FailingAdapter)

    assert first.items[0].status == "learning_failed"
    meta = _metadata(rd)
    assert meta["audit_repair_required"] is True
    assert meta["audit_repair_status"] == "learning_failed"
    row = load_outcome_by_loop_id("loop-audit-1")
    assert row.goal_achieved is False  # verdict stage did converge
    assert row.lesson_extraction_status == "failed"

    second = reconcile_pending_audits(adapter_factory=LessonAdapter)
    assert second.items[0].status == "repaired"
    assert _metadata(rd)["audit_repair_required"] is False


def test_no_adapter_leaves_learning_pending_without_fake_dry_run_lesson():
    rd = _seed_pending()

    result = reconcile_pending_audits(adapter_factory=None)

    assert result.items[0].status == "learning_pending"
    assert _metadata(rd)["audit_repair_required"] is True
    row = load_outcome_by_loop_id("loop-audit-1")
    assert row.lesson_extraction_status == "deferred"
    assert row.lessons == []


def test_verdict_write_failure_remains_pending(monkeypatch):
    rd = _seed_pending()
    monkeypatch.setattr(
        "memory.stamp_outcome_verdict",
        lambda *a, **kw: OutcomeVerdictStampResult(
            "write_failed", attempts=2, error="still readonly"),
    )

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "verdict_failed"
    meta = _metadata(rd)
    assert meta["audit_repair_required"] is True
    assert "still readonly" in meta["audit_repair"]["reconciliation"]["error"]


def test_missing_outcome_does_not_clear_repair_record():
    rd = _seed_pending()
    # Remove the isolated test ledger after run metadata has recorded repair.
    from config import memory_dir
    (memory_dir() / "outcomes.jsonl").unlink()

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "outcome_missing"
    assert _metadata(rd)["audit_repair_required"] is True


def test_invalid_or_cross_run_patch_is_never_replayed(monkeypatch):
    rd = _seed_pending()
    meta = _metadata(rd)
    meta["audit_repair"]["loop_id"] = "loop-from-another-run"
    (rd / "metadata.json").write_text(json.dumps(meta))
    called = []
    monkeypatch.setattr(
        "memory.stamp_outcome_verdict", lambda *a, **kw: called.append(a))

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "invalid"
    assert "not joined" in result.items[0].detail
    assert called == []
    assert _metadata(rd)["audit_repair_required"] is True


def test_missing_loop_join_fails_closed(monkeypatch):
    rd = _seed_pending()
    meta = _metadata(rd)
    meta.pop("loop_ids")
    (rd / "metadata.json").write_text(json.dumps(meta))
    monkeypatch.setattr(
        "memory.stamp_outcome_verdict",
        lambda *a, **kw: pytest.fail("unjoined repair must not stamp a row"),
    )

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "invalid"
    repaired = _metadata(rd)
    assert repaired["audit_repair_required"] is True
    assert repaired["audit_repairs"][0]["reconciliation"]["auto_exhausted"] is True


def test_live_run_is_not_repaired_before_close():
    rd = runs.create_run_dir(
        "a0000001", prompt="still running", lane="agenda", model="mid",
        extra_metadata={
            "loop_ids": ["loop-live"],
            "audit_incomplete": True,
            "audit_repair_required": True,
            "audit_repair": {
                "kind": "outcome_verdict_stamp", "loop_id": "loop-live",
                "goal_achieved": False, "goal_verdict_source": "closure",
                "goal_verdict_confidence": 0.9,
                "recorded_at": "2026-07-14T00:00:00+00:00",
            },
        },
    )

    assert find_pending_audits() == []
    assert _metadata(rd)["audit_repair_required"] is True


def test_surface_pending_is_recovered_without_replaying_learning(monkeypatch):
    rd = _seed_pending()
    meta = _metadata(rd)
    meta["audit_incomplete"] = False
    meta["audit_repair_required"] = False
    meta["audit_repair_status"] = "surface_pending"
    (rd / "metadata.json").write_text(json.dumps(meta))
    monkeypatch.setattr(
        "memory.stamp_outcome_verdict",
        lambda *a, **kw: pytest.fail("surface recovery must not replay verdict"),
    )

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "repaired"
    assert _metadata(rd)["audit_repair_status"] == "completed"


def test_surface_pending_with_corrupt_patch_never_requarantines(monkeypatch):
    rd = _seed_pending()
    meta = _metadata(rd)
    meta["audit_incomplete"] = False
    meta["audit_repair_required"] = False
    meta["audit_repair_status"] = "surface_pending"
    meta["audit_repair"].pop("kind")
    (rd / "metadata.json").write_text(json.dumps(meta))
    monkeypatch.setattr(
        "memory.stamp_outcome_verdict",
        lambda *a, **kw: pytest.fail("surface-only recovery must not replay verdict"),
    )

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.items[0].status == "repaired"
    repaired = _metadata(rd)
    assert repaired["audit_incomplete"] is False
    assert repaired["audit_repair_required"] is False


def test_poison_records_do_not_starve_repairable_work():
    poison_dirs = []
    for index in range(3):
        rd = _seed_pending(
            handle_id=f"a000000{index + 1}", loop_id=f"loop-poison-{index}")
        meta = _metadata(rd)
        meta["audit_repair"] = "corrupt"
        (rd / "metadata.json").write_text(json.dumps(meta))
        poison_dirs.append(rd)
    good = _seed_pending(handle_id="d0000001", loop_id="loop-good")

    results = [
        reconcile_pending_audits(limit=3, adapter_factory=LessonAdapter),
        reconcile_pending_audits(limit=3, adapter_factory=LessonAdapter),
    ]

    attempted = [item for result in results for item in result.items]
    assert any(
        item.handle_id == "d0000001" and item.status == "repaired"
        for item in attempted
    )
    assert _metadata(good)["audit_repair_required"] is False
    for rd in poison_dirs:
        reconciliation = _metadata(rd)["audit_repairs"][0]["reconciliation"]
        assert reconciliation["auto_exhausted"] is True


def test_multi_loop_repairs_clear_quarantine_only_after_both_converge():
    rd = _seed_pending(handle_id="a0000001", loop_id="loop-first", achieved=False)
    meta = _metadata(rd)
    second = dict(meta["audit_repair"])
    second.update({
        "loop_id": "loop-second", "goal_achieved": True,
        "recorded_at": "2026-07-14T00:01:00+00:00",
    })
    meta["loop_ids"] = ["loop-first", "loop-second"]
    meta["audit_repairs"] = [meta["audit_repair"], second]
    meta["audit_repair"] = second
    (rd / "metadata.json").write_text(json.dumps(meta))
    record_outcome(
        "build second", "done", "second delivered", lessons=[],
        loop_id="loop-second", lesson_extraction_status="deferred",
        handle_id="a0000001",
    )

    first = reconcile_pending_audits(limit=1, adapter_factory=LessonAdapter)
    midway = _metadata(rd)
    second_result = reconcile_pending_audits(limit=1, adapter_factory=LessonAdapter)
    final = _metadata(rd)

    assert first.items[0].loop_id == "loop-first"
    assert midway["audit_repair_required"] is True
    assert midway["audit_repairs"][0]["reconciliation"]["status"] == "completed"
    assert second_result.items[0].loop_id == "loop-second"
    assert final["audit_repair_required"] is False
    assert final["goal_achieved"] is True
    assert load_outcome_by_loop_id("loop-first").goal_achieved is False
    assert load_outcome_by_loop_id("loop-second").goal_achieved is True


def test_repaired_latest_loop_surfaces_exhausted_sibling_for_manual_action():
    rd = _seed_pending(handle_id="a0000001", loop_id="loop-bad", achieved=False)
    meta = _metadata(rd)
    bad = dict(meta["audit_repair"])
    bad.pop("goal_verdict_source")
    good = dict(meta["audit_repair"])
    good.update({
        "loop_id": "loop-good", "goal_achieved": True,
        "recorded_at": "2026-07-14T00:01:00+00:00",
    })
    meta["loop_ids"] = ["loop-bad", "loop-good"]
    meta["audit_repairs"] = [bad, good]
    meta["audit_repair"] = good
    (rd / "metadata.json").write_text(json.dumps(meta))
    record_outcome(
        "build good", "done", "good delivered", lessons=[],
        loop_id="loop-good", lesson_extraction_status="deferred",
        handle_id="a0000001",
    )

    first = reconcile_pending_audits(limit=1, adapter_factory=LessonAdapter)
    second = reconcile_pending_audits(limit=1, adapter_factory=LessonAdapter)
    final = _metadata(rd)

    assert first.items[0].status == "invalid"
    assert second.items[0].status == "manual_required"
    assert second.unresolved == 1
    assert final["audit_repair_status"] == "manual_required"
    assert final["audit_repair_required"] is True
    assert final["goal_achieved"] is True
    assert find_pending_audits() == []


def test_audit_failure_writer_preserves_distinct_loop_repairs():
    rd = runs.create_run_dir(
        "a0000001", prompt="two attempts", lane="agenda", model="mid")
    runs.set_current_run_dir(rd)
    base = {
        "audit_incomplete": True,
        "audit_repair_required": True,
        "loop_ids": ["loop-first", "loop-second"],
    }
    first = {
        "kind": "outcome_verdict_stamp", "loop_id": "loop-first",
        "goal_achieved": False, "goal_verdict_source": "closure",
        "goal_verdict_confidence": 0.9,
        "recorded_at": "2026-07-14T00:00:00+00:00",
    }
    second = {
        **first, "loop_id": "loop-second",
        "recorded_at": "2026-07-14T00:01:00+00:00",
    }

    runs.stamp_run_audit_failure({**base, "audit_repair": first})
    runs.stamp_run_audit_failure({**base, "audit_repair": second})

    meta = _metadata(rd)
    assert [item["loop_id"] for item in meta["audit_repairs"]] == [
        "loop-first", "loop-second"]
    assert meta["audit_repair"]["loop_id"] == "loop-second"


def test_targeted_lookup_accepts_loop_reference():
    _seed_pending()
    pending = find_pending_audits(handle_ref="loop-audit-1")
    assert [item.handle_id for item in pending] == ["a0000001"]


def test_malformed_reconciliation_counter_does_not_abort_repair():
    rd = _seed_pending()
    metadata = _metadata(rd)
    metadata["audit_repair"]["reconciliation"] = {"transition_count": "corrupt"}
    (rd / "metadata.json").write_text(json.dumps(metadata))

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.unresolved == 0
    repaired = _metadata(rd)
    # surface_pending and completed are two crash-safe metadata transitions.
    assert repaired["audit_repair"]["reconciliation"]["transition_count"] == 2


@pytest.mark.parametrize("malformed", [
    None,
    "pending",
    ["x"],
    3,
    {"auto_exhausted": "no", "failure_count": -10_000},
])
def test_malformed_nested_reconciliation_cannot_crash_sweep(malformed):
    poison = _seed_pending(handle_id="a0000001", loop_id="loop-poison")
    metadata = _metadata(poison)
    metadata["audit_repair"]["reconciliation"] = malformed
    (poison / "metadata.json").write_text(json.dumps(metadata))
    good = _seed_pending(handle_id="b0000001", loop_id="loop-good")

    result = reconcile_pending_audits(limit=2, adapter_factory=LessonAdapter)

    assert result.unresolved == 0
    assert {item.handle_id for item in result.items} == {"a0000001", "b0000001"}
    assert _metadata(good)["audit_repair_required"] is False


def test_duplicate_fencing_tokens_are_deduplicated_and_converge():
    rd = _seed_pending()
    metadata = _metadata(rd)
    duplicate = dict(metadata["audit_repair"])
    metadata["audit_repairs"] = [dict(duplicate), dict(duplicate)]
    (rd / "metadata.json").write_text(json.dumps(metadata))

    result = reconcile_pending_audits(adapter_factory=LessonAdapter)

    assert result.unresolved == 0
    repaired = _metadata(rd)
    assert repaired["audit_repair_required"] is False
    assert len(repaired["audit_repairs"]) == 1


def test_cli_repair_audits_json(monkeypatch, capsys):
    import run_curation
    expected = AuditRepairSweepResult(
        "completed",
        items=(AuditRepairItemResult("a0000001", "loop-a", "repaired"),),
    )
    monkeypatch.setattr("audit_repair.reconcile_pending_audits", lambda **kw: expected)

    rc = run_curation.main(["repair-audits", "a0000001", "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repaired"] == 1
    assert payload["unresolved"] == 0


def test_targeted_missing_repair_is_not_reported_as_success():
    result = reconcile_pending_audits(
        handle_ref="does-not-exist", adapter_factory=LessonAdapter)
    assert result.status == "not_found"
