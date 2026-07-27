"""§9.6 escalation payload — decision_line + family_roi_line pins.

Jeremy's decided shape (GOAL_BRAIN Decisions 2026-07-27): "Escalation
payload: simple first — single-chasm decision + one family-ROI context
line; complex later." These pins hold the simple shape honest: one
deterministic ask per emit point, one recurrence line keyed on the
Phase 44 diagnosis taxonomy, silence over noise when context is missing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from escalation_context import decision_line, family_roi_line
from introspect import LoopDiagnosis, save_diagnosis


# ---------------------------------------------------------------------------
# decision_line — one ask per emit point
# ---------------------------------------------------------------------------

def test_blocked_step_names_step_reason_and_honest_options():
    line = decision_line("blocked_step",
                         reason="credentials expired", step="fetch the feed")
    assert line.startswith("Decide this chasm: a step is blocked")
    assert "fetch the feed — credentials expired" in line
    assert "re-send the goal with guidance" in line
    assert "drop it" in line


def test_dispatch_names_parked_run_and_its_options():
    line = decision_line("dispatch", reason="goal names a dead host")
    assert "parked before starting — goal names a dead host" in line
    assert "adjust the goal" in line
    # No "resume" verb: a prevented run has nothing to resume.
    assert "resume" not in line.lower()


def test_director_escalation_relays_the_summary_as_the_ask():
    line = decision_line("director_escalation",
                         reason="Should I spend $40 on the paid API?")
    assert line == "Decide: Should I spend $40 on the paid API?"


def test_unknown_point_gets_generic_ask_not_empty():
    line = decision_line("some_future_point", reason="odd state")
    assert line == "Decide: odd state"


def test_empty_reason_is_honest_not_blank():
    line = decision_line("blocked_step")
    assert "no reason recorded" in line


def test_reason_whitespace_collapsed_and_capped():
    line = decision_line("director_escalation",
                         reason="a\n b\t  c " + "x" * 500)
    assert "a b c" in line
    assert len(line) < 300      # 220-char reason cap + template overhead


def test_never_raises_on_hostile_input():
    class _Boom:
        def __str__(self):
            raise RuntimeError("nope")
    line = decision_line("blocked_step", reason=_Boom())
    assert line == "Decide: escalation raised (context unavailable)"


# ---------------------------------------------------------------------------
# family_roi_line — one recurrence line, silence over noise
# ---------------------------------------------------------------------------

def _seed(failure_class: str, *, recorded_at: str = "") -> None:
    save_diagnosis(LoopDiagnosis(
        loop_id=f"seed-{failure_class}", failure_class=failure_class,
        severity="warning", recorded_at=recorded_at))


def test_empty_and_healthy_classes_render_nothing():
    assert family_roi_line("") == ""
    assert family_roi_line("healthy") == ""


def test_first_occurrence_is_signal(tmp_path):
    line = family_roi_line("retry_churn")
    assert line == "Family context: first 'retry_churn' failure on record."


def test_counts_only_the_matching_class(tmp_path):
    _seed("retry_churn")
    _seed("retry_churn")
    _seed("artifact_missing")
    line = family_roi_line("retry_churn")
    assert "'retry_churn' has 2 prior diagnoses on record" in line
    assert "2 in the last 30 days" in line


def test_singular_grammar_for_one_prior(tmp_path):
    _seed("token_burn")
    line = family_roi_line("token_burn")
    assert "has 1 prior diagnosis on record" in line


def test_old_rows_count_all_time_but_not_window(tmp_path):
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    _seed("retry_churn", recorded_at=old)
    line = family_roi_line("retry_churn")
    assert "1 prior diagnosis on record" in line
    assert "last 30 days" not in line


def test_unstamped_pre_v3_row_counts_all_time_only(tmp_path):
    # Pre-V3 ledger rows have no recorded_at; they still count toward the
    # all-time total but can't claim a place in the window.
    import json
    from introspect import _diagnoses_path
    path = _diagnoses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"loop_id": "prev3", "failure_class": "retry_churn",
                            "severity": "warning"}) + "\n")
    line = family_roi_line("retry_churn")
    assert "1 prior diagnosis on record" in line
    assert "last 30 days" not in line


def test_count_covers_whole_ledger_not_a_recent_window(tmp_path):
    # Adversarial-review find (both lenses): a "newest N" load cap turns
    # "on record" into a lie once the ledger outgrows it — old family rows
    # vanish behind newer other-class rows and re-render as "first on
    # record". The count must cover the whole file.
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    _seed("retry_churn", recorded_at=old)
    _seed("retry_churn", recorded_at=old)
    import json
    from introspect import _diagnoses_path
    path = _diagnoses_path()
    with path.open("a", encoding="utf-8") as f:
        for i in range(230):
            f.write(json.dumps({"loop_id": f"noise-{i}",
                                "failure_class": "token_burn",
                                "severity": "info"}) + "\n")
    line = family_roi_line("retry_churn")
    assert "'retry_churn' has 2 prior diagnoses on record" in line


def test_construction_failing_row_still_counts(tmp_path):
    # The count is over raw rows: a row LoopDiagnosis can't construct
    # (pre-schema, hand-edited) is still on record.
    import json
    from introspect import _diagnoses_path
    path = _diagnoses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"failure_class": "retry_churn"}) + "\n")
    line = family_roi_line("retry_churn")
    assert "1 prior diagnosis on record" in line


def test_unreadable_nonempty_ledger_renders_nothing(tmp_path):
    # Adversarial-review find (both lenses): the loader swallows read
    # failures into [], which the first cut rendered as a confident false
    # "first ... on record". An existing non-empty ledger that yields
    # nothing readable must render silence instead.
    from introspect import _diagnoses_path
    path = _diagnoses_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00\xffnot json at all\n{broken\n")
    assert family_roi_line("retry_churn") == ""
