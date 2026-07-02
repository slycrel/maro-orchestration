"""Tests for the post-goal curation pass (run_curation).

Curation classifies a finished run (done≠achieved aware) and inventories what's
mineable into <run-dir>/run_card.json, so later passes can act on the paid-for
capture instead of discarding it. list_runs/prune_run are the user-visible
surface ("show me my runs", "clean that up").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import runs
from runs import create_run_dir, finalize_run, set_current_run_dir, record_llm_call
from run_curation import curate_run, list_runs, prune_run, classify_outcome


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    runs._CALL_COUNTERS.clear()
    yield tmp_path
    set_current_run_dir(None)


def _finish(handle_id, prompt, status, *, achieved=None, verdict=None):
    extra = {}
    if achieved is not None:
        extra["goal_achieved"] = achieved
    if verdict is not None:
        extra["goal_verdict_summary"] = verdict
    rd = create_run_dir(handle_id, prompt=prompt, lane="now", model="claude",
                        extra_metadata=extra or None)
    finalize_run(handle_id, status=status)
    return rd


def test_curate_writes_run_card(workspace):
    _finish("h0000001", "build the thing", "done", achieved=True)
    card = curate_run("h0000001")
    assert card is not None
    rd = runs.run_dir("h0000001")
    assert (rd / "run_card.json").is_file()
    assert card["goal"] == "build the thing"
    assert card["success_class"] == "success"


def test_classify_done_not_achieved(workspace):
    _finish("h0000002", "g", "done", achieved=False)
    card = curate_run("h0000002")
    assert card["success_class"] == "done-not-achieved"


def test_classify_done_unverified(workspace):
    _finish("h0000003", "g", "done")  # no goal_achieved key
    card = curate_run("h0000003")
    assert card["success_class"] == "done-unverified"


def test_classify_failed(workspace):
    _finish("h0000004", "g", "stuck")
    card = curate_run("h0000004")
    assert card["success_class"] == "failed"


def test_classify_partial(workspace):
    _finish("h0000005", "g", "partial")
    card = curate_run("h0000005")
    assert card["success_class"] == "partial"


def test_classify_incomplete_is_partial(workspace):
    # closure-demoted runs (status "incomplete") were falling to "unknown"
    _finish("h000000a", "g", "incomplete", achieved=False)
    card = curate_run("h000000a")
    assert card["success_class"] == "partial"


def test_card_costs_from_loop_ids(workspace, monkeypatch):
    import metrics
    rd = create_run_dir("h000000b", prompt="g", lane="agenda", model="claude",
                        extra_metadata={"loop_ids": ["loopAAAA", "loopBBBB"]})
    finalize_run("h000000b", status="done")
    seen = {}

    def _fake_spend(lids):
        seen["lids"] = lids
        return 1.23

    monkeypatch.setattr(metrics, "spend_for_loops", _fake_spend)
    card = curate_run("h000000b")
    assert card["total_cost_usd"] == pytest.approx(1.23)
    assert seen["lids"] == ["loopAAAA", "loopBBBB"]


def test_card_cost_none_without_loop_ids(workspace):
    _finish("h000000c", "g", "done", achieved=True)
    card = curate_run("h000000c")
    assert card["total_cost_usd"] is None


def test_inventory_counts_calls(workspace):
    rd = _finish("h0000006", "g", "done", achieved=True)
    set_current_run_dir(rd)
    record_llm_call("p1", "r1")
    record_llm_call("p2", "r2")
    card = curate_run("h0000006")
    assert card["inventory"]["n_calls"] == 2
    assert card["mineable"] is True


def test_inventory_flags_scripts(workspace):
    rd = _finish("h0000007", "g", "done", achieved=True)
    (rd / "build" / "helper.py").write_text("print('hi')\n")
    card = curate_run("h0000007")
    assert any(s.endswith("helper.py") for s in card["inventory"]["scripts"])
    assert card["mineable"] is True


def test_empty_run_not_mineable(workspace):
    _finish("h0000008", "g", "done", achieved=True)
    card = curate_run("h0000008")
    assert card["inventory"]["n_calls"] == 0
    assert card["mineable"] is False


def test_curate_missing_run_returns_none(workspace):
    assert curate_run("nope9999") is None


def test_list_runs_includes_curated(workspace):
    _finish("h0000009", "alpha goal", "done", achieved=True)
    curate_run("h0000009")
    cards = list_runs()
    assert any(c["handle_id"] == "h0000009" for c in cards)


def test_list_runs_synthesizes_uncurated(workspace):
    _finish("h0000010", "beta goal", "done", achieved=True)
    # not curated — list should still surface it as "uncurated"
    cards = list_runs()
    match = [c for c in cards if c["handle_id"] == "h0000010"]
    assert match and match[0]["success_class"] == "uncurated"


def test_prune_removes_run_dir(workspace):
    rd = _finish("h0000011", "g", "done", achieved=True)
    assert rd.is_dir()
    assert prune_run("h0000011") is True
    assert not rd.is_dir()


def test_prune_missing_returns_false(workspace):
    assert prune_run("nope0000") is False


def test_classify_outcome_is_pure(workspace):
    # Direct curator call — registry functions are pure (rd, meta, card)->None.
    card = {}
    classify_outcome(Path("/nonexistent"), {"status": "done", "goal_achieved": True}, card)
    assert card["success_class"] == "success"
