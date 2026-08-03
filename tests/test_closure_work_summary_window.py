"""Closure's work-summary window: wide enough to judge, honest when cut.

Third instance of one family, all found 2026-08-02/03:
  f7b775c  closure asserting file contents it was never shown
  f4ef704  the gate escalating on absence from a 600-char excerpt
  this     closure judging from a 300-char-per-step excerpt

Measured over 268 recorded loop payloads (last-6-step totals: median
6,134 chars, p90 11,319): at 300 chars/step the verdict saw 23% of the
evidence and 5% of payloads survived intact. This string feeds BOTH the
closure plan call and the closure verdict call.
"""
import closure_verify
from closure_verify import (
    _WORK_SUMMARY_RESULT_CUT,
    _WORK_SUMMARY_TEXT_CUT,
    render_step_for_closure,
)


class TestWindowIsWideEnough:
    def test_result_cut_covers_the_median_observed_step(self):
        # median last-6 payload is 6,134 chars over ~6 steps -> ~1k/step;
        # a cut below that routinely judges a fraction of each answer
        assert _WORK_SUMMARY_RESULT_CUT >= 2500

    def test_result_cut_is_still_bounded(self):
        assert _WORK_SUMMARY_RESULT_CUT <= 20000

    def test_step_instruction_is_not_cut_mid_sentence_by_default(self):
        """120 chars cut most step instructions -- the one thing the judge
        needs to decide whether the step did what it was asked."""
        assert _WORK_SUMMARY_TEXT_CUT >= 250

    def test_typical_step_survives_intact(self):
        result = "r" * 1100          # ~median single-step result
        out = render_step_for_closure("do the thing", result, 1)
        assert "TRUNCATED" not in out
        assert result in out


class TestTruncationIsVisible:
    def test_long_result_marked_with_both_numbers(self):
        result = "x" * (_WORK_SUMMARY_RESULT_CUT + 900)
        out = render_step_for_closure("t", result, 2)
        assert "TRUNCATED" in out
        assert str(_WORK_SUMMARY_RESULT_CUT) in out
        assert str(_WORK_SUMMARY_RESULT_CUT + 900) in out
        assert "NOT shown to you" in out

    def test_tail_is_actually_withheld(self):
        result = "A" * _WORK_SUMMARY_RESULT_CUT + "SECRET_TAIL"
        assert "SECRET_TAIL" not in render_step_for_closure("t", result, 1)

    def test_long_step_text_is_marked_too(self):
        out = render_step_for_closure("q" * (_WORK_SUMMARY_TEXT_CUT + 50),
                                      "short", 1)
        assert "step text truncated" in out

    def test_boundary_not_marked(self):
        out = render_step_for_closure("t", "z" * _WORK_SUMMARY_RESULT_CUT, 1)
        assert "TRUNCATED" not in out

    def test_empty_and_missing_do_not_crash(self):
        assert "Step 3" in render_step_for_closure("", "", 3)
        assert "Step 4" in render_step_for_closure(None, None, 4)


class TestPromptContract:
    def test_verdict_prompt_forbids_absence_claims_from_a_truncated_result(self):
        text = " ".join(closure_verify._CLOSURE_VERDICT_SYSTEM.split())
        assert "Do not report as missing anything you simply cannot find in a truncated Result" in text
        assert "a fact about your window, not about the work" in text
