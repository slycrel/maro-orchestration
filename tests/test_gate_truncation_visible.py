"""Truncation must be visible to the quality gate.

Measured 2026-08-03 over the captain's log: of 7 escalations since the
closure-overrule reconciliation shipped (2026-07-29), **5 were overruled
by closure** — and every escalation reason, overruled or not, is phrased
as an absence claim ("never shows", "no evidence", "only shows").

Mechanism, verified from source: the gate reads the last 3 step results at
600 chars each and was never told so. Run 01e55212's steps ran 1058-1387
chars, and the gate escalated on "no evidence Q3 was ever answered" when
Q3 was answered — grep-verified — past the cut.
"""
from types import SimpleNamespace

import quality_gate
from quality_gate import _REVIEW_STEP_CUT, render_step_for_review


def _step(result, index=3, text="write the report"):
    return SimpleNamespace(index=index, text=text, result=result)


class TestTruncationIsMarked:
    def test_long_result_is_marked_with_both_numbers(self):
        body = "x" * (_REVIEW_STEP_CUT + 764)
        out = render_step_for_review(_step(body), 1)
        assert "TRUNCATED" in out
        assert str(_REVIEW_STEP_CUT) in out      # how much you saw
        assert str(_REVIEW_STEP_CUT + 764) in out  # how much existed

    def test_marked_result_says_the_rest_was_withheld(self):
        out = render_step_for_review(_step("y" * (_REVIEW_STEP_CUT + 300)), 1)
        assert "NOT shown to you" in out

    def test_only_the_cut_prefix_is_included(self):
        body = "A" * _REVIEW_STEP_CUT + "SECRET_TAIL"
        out = render_step_for_review(_step(body), 1)
        assert "SECRET_TAIL" not in out

    def test_boundary_exactly_at_cut_is_not_marked(self):
        out = render_step_for_review(_step("z" * _REVIEW_STEP_CUT), 1)
        assert "TRUNCATED" not in out

    def test_one_char_over_is_marked(self):
        out = render_step_for_review(_step("z" * (_REVIEW_STEP_CUT + 1)), 1)
        assert "TRUNCATED" in out


class TestShortResultsUnchanged:
    def test_short_result_has_no_marker_and_full_text(self):
        out = render_step_for_review(_step("all done, three of three"), 1)
        assert "TRUNCATED" not in out
        assert "all done, three of three" in out

    def test_empty_result_does_not_crash(self):
        out = render_step_for_review(_step(""), 7)
        assert "TRUNCATED" not in out

    def test_missing_attributes_fall_back(self):
        out = render_step_for_review(SimpleNamespace(), 4)
        assert "Step 4" in out


class TestCutIsWideEnoughToBeUseful:
    """The marker fixed the epistemics; the cut fixed the evidence.

    Measured over 261 recorded loop payloads: median last-3-step payload is
    3,323 chars, p90 5,659. At the original 600 only 15% of payloads
    survived intact and the gate judged 43% of the text. The extra input at
    4000 is ~422 tokens median -- about $0.0013 a call -- against a false
    ESCALATE that re-runs the whole loop at the next model tier.
    """

    def test_cut_covers_the_median_observed_payload(self):
        # median single-step result is ~1.1k chars (3,323 over ~3 steps);
        # a cut below that means routinely judging half an answer
        assert _REVIEW_STEP_CUT >= 2500

    def test_cut_is_still_bounded(self):
        """Not removed -- a pathological step dumping megabytes must not
        blow up the gate call."""
        assert _REVIEW_STEP_CUT <= 20000

    def test_a_typical_step_result_now_survives_intact(self):
        typical = "s" * 1400          # ~median single-step result
        out = render_step_for_review(_step(typical), 1)
        assert "TRUNCATED" not in out
        assert typical in out


class TestGatePromptContract:
    def test_prompt_forbids_escalating_on_absence_from_a_truncated_view(self):
        text = " ".join(quality_gate._GATE_SYSTEM.split())
        assert "MUST NOT escalate on something being absent from a truncated Result" in text

    def test_prompt_names_the_actual_false_reasons_seen_in_the_log(self):
        """The three phrasings really used by overruled escalations."""
        text = " ".join(quality_gate._GATE_SYSTEM.split())
        for phrase in ('"no evidence X was answered"', '"only shows Y"',
                       '"never states Z"'):
            assert phrase in text

    def test_prompt_routes_the_doubt_to_closure_instead(self):
        text = " ".join(quality_gate._GATE_SYSTEM.split())
        assert "missing evidence, not a missing deliverable" in text
        assert "execute against the real artifacts" in text
