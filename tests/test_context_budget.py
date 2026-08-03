"""Bounded accumulating context (context_budget.ContextBudget).

The one site in the 2026-08-03 truncation audit where a bound genuinely
earns its keep: `completed_context += …` grows quadratically because every
step re-sends everything before it. The old call sites had it backwards on
both axes -- factory_thin capped entries at 200 chars (too tight to be
evidence) with no total bound (unbounded where it actually grows).
"""
import pytest

from context_budget import (
    DEFAULT_ENTRY_CAP,
    DEFAULT_TOTAL_BUDGET,
    ContextBudget,
)


class TestNormalRunsSurviveWhole:
    def test_median_shaped_run_is_untouched(self):
        """Median whole-run accumulation is ~7,464 chars over ~6 steps.
        Nothing about that should be trimmed."""
        cb = ContextBudget()
        for i in range(6):
            cb.add(f"Step {i}: " + "r" * 1200)
        out = cb.render()
        assert "elided" not in out
        assert "truncated" not in out
        assert out.count("Step ") == 6

    def test_p90_shaped_run_is_untouched(self):
        """p90 is 16,292 chars; the budget covers it deliberately."""
        cb = ContextBudget()
        for i in range(8):
            cb.add(f"Step {i}: " + "r" * 2000)
        assert "elided" not in cb.render()

    def test_empty_is_falsey_and_renders_empty(self):
        cb = ContextBudget()
        assert not cb
        assert cb.render() == ""

    def test_blank_entries_are_ignored(self):
        cb = ContextBudget()
        cb.add("")
        cb.add(None)
        assert not cb and len(cb) == 0


class TestTotalBudgetHolds:
    def test_long_run_is_bounded(self):
        """34 steps is the longest run on record. Unbounded, that is what
        makes the quadratic term bite."""
        cb = ContextBudget()
        for i in range(34):
            cb.add(f"Step {i}: " + "r" * 2000)
        out = cb.render()
        assert len(out) <= DEFAULT_TOTAL_BUDGET + 400   # + the elision notice
        assert "elided" in out

    def test_eviction_keeps_the_most_recent(self):
        """Recency is what the next step builds on."""
        cb = ContextBudget()
        for i in range(40):
            cb.add(f"MARKER{i} " + "x" * 2000)
        out = cb.render()
        assert "MARKER39" in out          # newest kept
        assert "MARKER0" not in out       # oldest evicted

    def test_eviction_is_announced_with_a_count(self):
        cb = ContextBudget()
        for i in range(40):
            cb.add("y" * 2000)
        out = cb.render()
        assert "earlier entries elided" in out
        assert "the most recent" in out

    def test_singular_plural_on_the_notice(self):
        cb = ContextBudget(total_budget=2500, entry_cap=2000)
        cb.add("a" * 2000)
        cb.add("b" * 2000)
        assert "1 earlier entry elided" in cb.render()

    def test_a_single_oversized_entry_still_renders(self):
        """Never return nothing: one entry always survives even if it alone
        exceeds the budget."""
        cb = ContextBudget(total_budget=100, entry_cap=DEFAULT_ENTRY_CAP)
        cb.add("z" * 3000)
        out = cb.render()
        assert out
        assert "z" * 100 in out


class TestPerEntryCap:
    def test_oversized_entry_is_capped_and_marked(self):
        cb = ContextBudget()
        cb.add("q" * (DEFAULT_ENTRY_CAP + 500))
        out = cb.render()
        assert "entry truncated" in out
        assert str(DEFAULT_ENTRY_CAP) in out
        assert str(DEFAULT_ENTRY_CAP + 500) in out

    def test_entry_cap_covers_p99_step_results(self):
        """p99 single step result is 4,671 chars; max observed 20,534."""
        assert DEFAULT_ENTRY_CAP >= 4000

    def test_entry_cap_cannot_eat_the_whole_budget(self):
        assert DEFAULT_ENTRY_CAP * 4 <= DEFAULT_TOTAL_BUDGET


class TestConstantsCameFromTheDistribution:
    def test_budget_covers_p90_whole_run_accumulation(self):
        assert DEFAULT_TOTAL_BUDGET >= 16292      # measured p90

    def test_budget_is_a_real_bound_not_a_formality(self):
        """max observed whole-run accumulation is 74,288 chars -- the budget
        must actually bite on the tail or it is decoration."""
        assert DEFAULT_TOTAL_BUDGET < 74288


class TestCallSitesUseIt:
    @pytest.mark.parametrize("module", ["factory_thin", "director"])
    def test_no_raw_string_accumulation_remains(self, module):
        import importlib
        from pathlib import Path
        src = Path(importlib.import_module(module).__file__).read_text()
        assert "completed_context +=" not in src
        assert "ContextBudget()" in src
