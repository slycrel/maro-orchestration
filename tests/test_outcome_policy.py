import pytest

from outcome_policy import is_learnable_outcome


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ({"success_class": "success"}, True),
        ({"success_class": "done-unverified"}, True),
        ({"success_class": "done-not-achieved"}, False),
        ({"success_class": "failed"}, False),
        ({"success_class": "uncurated", "status": "done"}, False),
        ({"success_class": None, "status": "done"}, False),
        ({"success_class": "", "status": "done"}, False),
        ({"status": "done", "goal_achieved": True}, True),
        ({"status": "done"}, True),
        ({"status": "done", "goal_achieved": False}, False),
        ({"status": "stuck", "goal_achieved": True}, False),
    ],
)
def test_is_learnable_outcome_supports_card_and_ledger_shapes(outcome, expected):
    assert is_learnable_outcome(outcome) is expected


def test_curated_classification_wins_over_raw_status():
    outcome = {
        "success_class": "done-not-achieved",
        "status": "done",
        "goal_achieved": True,
    }

    assert is_learnable_outcome(outcome) is False
