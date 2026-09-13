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
