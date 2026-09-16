"""Regression coverage for conditional outcome verdict persistence."""
import json


def test_r24_a_placeholder_stamp_declines_a_judged_row(monkeypatch, tmp_path):
    import memory_ledger as ml
    from stop_verdicts import VERDICT_SOURCE_NEVER_STAMPED, VERDICT_SOURCE_PENDING_ORPHANED
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    path = ml._outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    judged = {"loop_id": "r24", "goal_achieved": False,
              "goal_verdict_source": "closure_unverifiable", "verdict_excluded": True}
    original = json.dumps(judged) + "\n"
    path.write_text(original)
    kwargs = dict(goal_achieved=None, goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED)
    result = ml.stamp_outcome_verdict("r24", only_unjudged=True, **kwargs)
    assert result.status == "superseded"
    assert path.read_text() == original
    assert ml.verdict_trust(json.loads(path.read_text())) == "excluded"
    assert ml.stamp_outcome_verdict("r24", **kwargs).status == "updated"
    assert json.loads(path.read_text())["goal_verdict_source"] == VERDICT_SOURCE_PENDING_ORPHANED
    for row in ({"loop_id": "r24", "goal_verdict_source": VERDICT_SOURCE_NEVER_STAMPED},
                {"loop_id": "r24"}):
        path.write_text(json.dumps(row) + "\n")
        assert ml.stamp_outcome_verdict("r24", only_unjudged=True, **kwargs).status == "updated"
        assert json.loads(path.read_text())["goal_verdict_source"] == VERDICT_SOURCE_PENDING_ORPHANED


def test_r25_every_placeholder_source_yields_to_a_placeholder(monkeypatch, tmp_path):
    import memory_ledger as ml
    from stop_verdicts import (VERDICT_PLACEHOLDER_SOURCES, VERDICT_SOURCE_NEVER_STAMPED,
                               VERDICT_SOURCE_RUN_ERRORED,
                               VERDICT_SOURCE_NO_STEPS_COMPLETED, VERDICT_SOURCE_PENDING_ORPHANED)
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    path = ml._outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # review r26: pin the exact family; a fifth source must not sneak in.
    assert VERDICT_PLACEHOLDER_SOURCES == {
        VERDICT_SOURCE_NEVER_STAMPED, VERDICT_SOURCE_RUN_ERRORED,
        VERDICT_SOURCE_NO_STEPS_COMPLETED, VERDICT_SOURCE_PENDING_ORPHANED,
    }
    for source in (VERDICT_SOURCE_RUN_ERRORED, VERDICT_SOURCE_NO_STEPS_COMPLETED):
        path.write_text(json.dumps({"loop_id": "r25", "goal_verdict_source": source}) + "\n")
        res = ml.stamp_outcome_verdict("r25", goal_achieved=None,
                                       goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED,
                                       only_unjudged=True)
        assert res.status == "updated", source
        assert json.loads(path.read_text())["goal_verdict_source"] == VERDICT_SOURCE_PENDING_ORPHANED
    # a judged row still declines
    path.write_text(json.dumps({"loop_id": "r25", "goal_achieved": True,
                                "goal_verdict_source": "closure"}) + "\n")
    assert ml.stamp_outcome_verdict("r25", goal_achieved=None,
                                    goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED,
                                    only_unjudged=True).status == "superseded"


def test_r26_a_placeholder_stamp_declines_a_contradictory_row(monkeypatch, tmp_path):
    import memory_ledger as ml
    from stop_verdicts import (VERDICT_SOURCE_NEVER_STAMPED,
                               VERDICT_SOURCE_PENDING_ORPHANED)
    # review r26: a bool verdict makes a placeholder source contradictory.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    path = ml._outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"loop_id": "r26-contradictory", "goal_achieved": True,
           "goal_verdict_source": VERDICT_SOURCE_NEVER_STAMPED,
           "verdict_excluded": True}
    original = json.dumps(row) + "\n"
    path.write_text(original)
    result = ml.stamp_outcome_verdict(
        row["loop_id"], goal_achieved=None,
        goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED,
        only_unjudged=True)
    assert result.status == "superseded"
    assert path.read_text() == original
    assert ml.verdict_trust(json.loads(path.read_text())) == "excluded"


def test_r26_a_placeholder_stamp_declines_a_malformed_source(monkeypatch, tmp_path):
    import memory_ledger as ml
    from stop_verdicts import VERDICT_SOURCE_PENDING_ORPHANED
    # review r26: non-string provenance fails closed and remains byte-identical.
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
    path = ml._outcomes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({"loop_id": "r26-malformed",
                           "goal_verdict_source": 17}) + "\n"
    path.write_text(original)
    result = ml.stamp_outcome_verdict(
        "r26-malformed", goal_achieved=None,
        goal_verdict_source=VERDICT_SOURCE_PENDING_ORPHANED,
        only_unjudged=True)
    assert result.status == "superseded"
    assert path.read_text() == original
