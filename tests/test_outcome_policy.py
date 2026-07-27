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
        ({"success_class": "success", "audit_incomplete": True}, False),
        ({"success_class": "done-unverified", "audit_repair_required": True}, False),
        ({"status": "done", "goal_achieved": True}, True),
        ({"status": "done"}, True),
        ({"status": "done", "lesson_extraction_status": "deferred"}, False),
        ({"status": "done", "goal_achieved": False}, False),
        # Verdict-preferred (per-step learning chunk, 2026-07-27): a judged
        # goal_achieved=True is success evidence regardless of process
        # status — the raw-row twin of success_class "achieved-not-done".
        # (This row previously pinned False — the SF-2 inversion.)
        ({"status": "stuck", "goal_achieved": True}, True),
        ({"success_class": "achieved-not-done"}, True),
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
