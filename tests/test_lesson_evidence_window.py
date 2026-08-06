"""What the lesson extractor is allowed to see (2026-08-06 truncation audit).

The finding this pins: every lesson the system had ever learned from a
completed run was extracted from `f"Completed {n}/{m} steps. " +
step_outcomes[-1].result[:80]`. Measured over 1,493 stored outcome rows,
90.1% matched that template, median length 70 chars, and 80 chars shows a
median 7.1% of the last step's result -- mid-word, with nothing at all from
the other N-1 steps. The `result_summary[:500]` downstream never once bound
(0 of 1,493), so the cut that LOOKED like the constraint was decoration and
the real loss happened upstream.

Full step results are not persisted anywhere -- runs/*/build/loop-*.json
keeps `result_length`, not the text -- so the deferred (post-verdict)
extractor can only ever see the stored row. That is why the stored summary
has to carry evidence too, and why it gets a STORE-grade budget while the
finalize-time prompt gets a wide one.
"""
import pytest

from context_budget import (
    DEFAULT_TOTAL_BUDGET,
    STORE_ENTRY_CAP,
    STORE_TOTAL_BUDGET,
)


class _Step:
    def __init__(self, index, text, result, status="done"):
        self.index = index
        self.text = text
        self.result = result
        self.status = status


def _steps(n=6, result_len=1180):
    """A median-shaped run: 6 steps, median step result 1,180 chars."""
    return [_Step(i, f"step {i} text", f"RESULT{i}-" + "r" * result_len)
            for i in range(1, n + 1)]


class TestStepEvidence:
    def test_every_step_is_represented_not_just_the_last(self):
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps())
        for i in range(1, 7):
            assert f"RESULT{i}-" in out, f"step {i} missing from evidence"

    def test_a_median_run_survives_whole_at_prompt_budget(self):
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps())
        assert "elided" not in out
        assert "entry truncated" not in out

    def test_store_profile_is_bounded(self):
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps(),
                             total_budget=STORE_TOTAL_BUDGET,
                             entry_cap=STORE_ENTRY_CAP)
        assert len(out) <= STORE_TOTAL_BUDGET + 400   # + the elision notice

    def test_store_profile_still_beats_the_old_80_chars_by_an_order(self):
        """The bar this replaces: 80 characters of one step."""
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps(),
                             total_budget=STORE_TOTAL_BUDGET,
                             entry_cap=STORE_ENTRY_CAP)
        assert len(out) > 800

    def test_store_profile_represents_a_whole_median_run(self):
        """Breadth is the point of the STORE profile: the deferred extractor
        should see that all six steps happened, not just the last two."""
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps(),
                             total_budget=STORE_TOTAL_BUDGET,
                             entry_cap=STORE_ENTRY_CAP)
        for i in range(1, 7):
            assert f"RESULT{i}-" in out, f"step {i} dropped from the stored row"
        assert "elided" not in out

    def test_trimming_is_announced_not_silent(self):
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps(n=34),
                             total_budget=STORE_TOTAL_BUDGET,
                             entry_cap=STORE_ENTRY_CAP)
        assert "elided" in out or "entry truncated" in out

    def test_recency_survives_eviction(self):
        from loop_finalize import _step_evidence
        out = _step_evidence(_steps(n=34),
                             total_budget=STORE_TOTAL_BUDGET,
                             entry_cap=STORE_ENTRY_CAP)
        assert "RESULT34-" in out

    def test_status_is_carried_so_a_failed_step_reads_as_failed(self):
        from loop_finalize import _step_evidence
        out = _step_evidence([_Step(1, "t", "r", status="stuck")])
        assert "stuck" in out

    def test_empty_steps_are_skipped_not_rendered_blank(self):
        from loop_finalize import _step_evidence
        out = _step_evidence([_Step(1, "", ""), _Step(2, "real", "evidence")])
        assert "evidence" in out
        assert "Step 1" not in out

    def test_no_steps_renders_empty(self):
        from loop_finalize import _step_evidence
        assert _step_evidence([]) == ""
        assert _step_evidence(None) == ""


class TestExtractorReceivesTheWideView:
    def _capture(self, monkeypatch):
        """Return a list that collects the user message sent to the LLM."""
        import memory
        seen = []

        class _Resp:
            content = '[{"lesson": "x", "type": "execution"}]'
            input_tokens = 0
            output_tokens = 0

        class _Adapter:
            model_key = "test"

            def complete(self, messages, **kw):
                seen.append(messages[-1].content)
                return _Resp()

        return seen, _Adapter()

    def test_lesson_evidence_is_preferred_over_the_stored_summary(self, monkeypatch):
        import memory
        seen, adapter = self._capture(monkeypatch)
        memory.extract_lessons_via_llm(
            goal="g", status="done",
            result_summary="Completed 6/6 steps.",
            lesson_evidence="WIDE-EVIDENCE-MARKER step details here",
            task_type="agenda", adapter=adapter,
        )
        assert seen and "WIDE-EVIDENCE-MARKER" in seen[0]

    def test_falls_back_to_the_summary_when_no_evidence_passed(self, monkeypatch):
        """The deferred post-verdict path has only the stored row."""
        import memory
        seen, adapter = self._capture(monkeypatch)
        memory.extract_lessons_via_llm(
            goal="g", status="done",
            result_summary="STORED-SUMMARY-MARKER",
            task_type="agenda", adapter=adapter,
        )
        assert seen and "STORED-SUMMARY-MARKER" in seen[0]

    def test_the_old_500_cut_no_longer_clips_real_evidence(self, monkeypatch):
        import memory
        seen, adapter = self._capture(monkeypatch)
        memory.extract_lessons_via_llm(
            goal="g", status="done", result_summary="",
            lesson_evidence="E" * 5000 + "TAIL-MARKER",
            task_type="agenda", adapter=adapter,
        )
        assert seen and "TAIL-MARKER" in seen[0]

    def test_the_backstop_marks_itself_when_it_bites(self, monkeypatch):
        import memory
        seen, adapter = self._capture(monkeypatch)
        memory.extract_lessons_via_llm(
            goal="g", status="done", result_summary="",
            lesson_evidence="E" * (memory._LESSON_EVIDENCE_CUT + 100),
            task_type="agenda", adapter=adapter,
        )
        assert seen and "evidence truncated" in seen[0]

    def test_backstop_sits_above_the_budget_that_builds_the_block(self):
        """If the backstop were tighter than the budget it would be the real
        bound, and the budget would be the decoration."""
        import memory
        assert memory._LESSON_EVIDENCE_CUT >= DEFAULT_TOTAL_BUDGET


def _code_of(module: str) -> str:
    """Module source with comment lines dropped.

    These pins must fire on live code, not on a comment that QUOTES the old
    code to explain why it went -- which is exactly what tripped this pin on
    first run, and is a false positive the day someone documents the history.
    """
    import importlib
    from pathlib import Path
    src = Path(importlib.import_module(module).__file__).read_text()
    return "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))


class TestTheOldTemplateIsGone:
    def test_finalize_no_longer_quotes_only_the_last_step_at_80(self):
        assert "step_outcomes[-1].result[:80]" not in _code_of("loop_finalize")

    def test_step_lessons_no_longer_cut_the_verified_result_at_300(self):
        """The verified result IS the evidence for a method lesson."""
        assert '(getattr(s, "result", "") or "")[:300]' not in _code_of("memory")


class TestDailyLogStaysReadable:
    def test_long_summary_is_trimmed_for_the_human_log(self):
        from memory_ledger import _daily_log_summary, _DAILY_LOG_SUMMARY_CUT
        out = _daily_log_summary("x" * 5000)
        assert len(out) < 600
        assert "5000 chars total" in out

    def test_short_summary_is_untouched(self):
        from memory_ledger import _daily_log_summary
        assert _daily_log_summary("Completed 6/6 steps.") == "Completed 6/6 steps."

    def test_trim_points_at_where_the_full_text_lives(self):
        from memory_ledger import _daily_log_summary
        assert "outcomes.jsonl" in _daily_log_summary("x" * 5000)


class TestTeamHandoffCarriesTheWork:
    def test_shared_context_is_budgeted_not_cut_at_600(self):
        assert "_tw_res.result[:600]" not in _code_of("step_exec")
