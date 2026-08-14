"""Bounded accumulating context (context_budget.ContextBudget).

The one site in the 2026-08-03 truncation audit where a bound genuinely
earns its keep: `completed_context += …` grows quadratically because every
step re-sends everything before it. The old call sites had it backwards on
both axes -- factory_thin capped entries at 200 chars (too tight to be
evidence) with no total bound (unbounded where it actually grows).
"""
import json

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


class TestClip:
    """clip() — the audit's universal honest-cut idiom."""

    def test_short_text_unchanged(self):
        from context_budget import clip
        assert clip("hello", 100) == "hello"

    def test_exact_cap_unchanged(self):
        from context_budget import clip
        assert clip("x" * 100, 100) == "x" * 100

    def test_over_cap_marked_with_both_lengths(self):
        from context_budget import clip
        out = clip("y" * 250, 100)
        assert out.startswith("y" * 100)
        assert "truncated: first 100 of 250 characters" in out

    def test_none_and_empty_safe(self):
        from context_budget import clip
        assert clip(None, 10) == ""
        assert clip("", 10) == ""

    def test_non_string_coerced(self):
        from context_budget import clip
        assert clip(12345, 100) == "12345"


class TestPromptWorklistSitesUseClip:
    """The 2026-08-06 PROMPT-worklist sites cut evidence honestly now.

    Pins the *idiom* (clip import + no bare slice at the old width), not
    line numbers.
    """

    @pytest.mark.parametrize("module,gone", [
        ("director", "_r_result[:2000]"),
        ("director", "_r_text[:2000]"),
        ("attribution", "goal[:300]"),
        ("knowledge_bridge", "summary[:500]"),
        ("evolver_scans", '"goal", "")[:80]'),
    ])
    def test_old_bare_slice_gone(self, module, gone):
        import importlib
        from pathlib import Path
        src = Path(importlib.import_module(module).__file__).read_text()
        assert gone not in src, f"{module} still carries the silent cut {gone!r}"


class TestStoreWorklistSitesUseClip:
    """The 2026-08-13 STORE-worklist sites persist rationale honestly now.

    Same pin style as the PROMPT class above: the *idiom* (no bare slice at
    the old width), not line numbers. These fields are durable — a silent
    mid-word cut here is a rationale a future re-attempt half-reads.
    """

    @pytest.mark.parametrize("module,gone", [
        ("handle", "_stamp_reason[:300]"),
        ("handle", "str(_closure.summary)[:300]"),
        ("handle", "_closure_error[:300]"),
        ("handle", 'outcome.get("result", ""))[:500]'),
        ("closure_verify", 'data.get("reason", ""))[:300]'),
        ("closure_verify", '"summary": summary[:500]'),
        ("closure_verify", '"summary": summary[:400]'),
        ("run_curation", "str(lesson)[:200]"),
        ("run_curation", "excerpt[:_DECISION_TRIED_CHARS]"),
        ("run_curation", 'str(log["stuck_reason"])[:300]'),
        ("run_curation", 'str(why or "")[:400]'),
        ("handle_queue", '"reasoning", ""))[:300]'),
        ("decision_prior", 'str(why or "")[:_DECISION_TRIED_CHARS]'),
        ("decision_prior", "return block[:max_chars]"),
        # 2026-08-13 adversarial-review round: the lanes the first pass
        # missed (shared writers + downstream consumers that re-cut).
        ("runs", "str(summary)[:300]"),
        ("runs", "str(downgrade_reason)[:300]"),
        ("runs", "'reasoning') or '')[:300]"),
        ("recall", "return text[:max_chars]"),
        ("handle", ".strip()[:400]"),
        ("handle", '_retry.get("result", ""))[:500]'),
        ("handle", "reasoning[:500]"),
        ("handle", '(evidence or "")[:500]'),
        ("loop_types", '(evidence or "")[:500]'),
        ("memory_ledger", '(stop_evidence or "")[:500]'),
        ("run_curation", "text[:500] + "),
        ("run_curation", "t[:200] for t in step_txts"),
    ])
    def test_old_bare_slice_gone(self, module, gone):
        import importlib
        from pathlib import Path
        src = Path(importlib.import_module(module).__file__).read_text()
        assert gone not in src, f"{module} still carries the silent cut {gone!r}"

    def test_store_caps_hold_the_measured_distributions(self):
        # Floors, not exact values: the caps were sized 2026-08-13 from the
        # live distributions (closure/verdict prose censored max 500; lesson
        # p99 478, max 573). Shrinking below these floors re-introduces the
        # mid-word cuts the audit removed; growing them is a judgment call.
        from context_budget import LESSON_ENTRY_CAP, VERDICT_PROSE_CAP
        assert VERDICT_PROSE_CAP >= 2000
        assert LESSON_ENTRY_CAP >= 800

    def test_decision_prior_clips_announce_themselves(self):
        from context_budget import LESSON_ENTRY_CAP
        from decision_prior import make_decision_prior, _DECISION_TRIED_CHARS
        long_lesson = "L" * (LESSON_ENTRY_CAP + 100)
        long_tried = "T" * (_DECISION_TRIED_CHARS + 100)
        prior = make_decision_prior(
            handle_id="h1", goal="g", outcome="success", goal_achieved=True,
            when="2026-08-13", what_was_tried=long_tried, why="w",
            lessons=[long_lesson])
        assert "[truncated:" in prior["what_was_tried"]
        assert "[truncated:" in prior["lessons"][0]
        # And a fitting value passes through whole, no marker.
        assert make_decision_prior(
            handle_id="h2", goal="g", outcome="success", goal_achieved=True,
            when="2026-08-13", what_was_tried="short", why="w",
            lessons=["small lesson"])["lessons"] == ["small lesson"]

    def test_format_prior_decisions_drops_whole_briefs_not_midword(self, tmp_path, monkeypatch):
        # Three fat priors against a budget that fits one: the block keeps
        # whole briefs, announces the omission, and keeps its instruction
        # footer — the old block[:1000] cut mid-brief with no notice.
        import decision_prior as dp
        briefs = {}
        for i in range(3):
            hid = f"run-{i}"
            card = {"handle_id": hid, "goal": "g", "success_class": "failed",
                    "goal_achieved": False, "started_at": "2026-08-13",
                    "decision_prior": {
                        "handle_id": hid, "goal": "g", "outcome": "failed",
                        "goal_achieved": False, "when": "2026-08-13",
                        "what_was_tried": f"attempt {i}: " + "x" * 600,
                        "why": "y" * 200, "lessons": []}}
            rd = tmp_path / hid
            rd.mkdir()
            (rd / "run_card.json").write_text(json.dumps(card))
            briefs[hid] = rd
        monkeypatch.setattr(dp, "_run_dir_for", lambda hid: briefs.get(hid))
        block = dp.format_prior_decisions(
            [{"handle_id": h} for h in briefs], max_chars=1200)
        assert "attempt 0" in block
        assert "omitted for space" in block
        assert block.rstrip().endswith("change the approach.")
        # Wide default: all three ride whole, nothing dropped or clipped.
        full = dp.format_prior_decisions([{"handle_id": h} for h in briefs])
        assert all(f"attempt {i}" in full for i in range(3))
        assert "omitted for space" not in full and "[truncated:" not in full


class TestReviewRoundBehaviors:
    """Behavior pins from the 2026-08-13 adversarial-review round."""

    def test_clip_idempotent_at_same_or_wider_cap(self):
        from context_budget import clip
        once = clip("z" * 3000, 2000)
        assert clip(once, 2000) == once, "same-cap re-clip must be a no-op"
        assert clip(once, 4000) == once, "wider re-clip must be a no-op"
        assert "first 2000 of 3000" in once
        # A strictly tighter cap still cuts (the payload genuinely
        # does not fit the smaller bound).
        tighter = clip(once, 500)
        assert len(tighter) < len(once) and "truncated" in tighter

    def test_stamp_run_verdict_persists_long_summary_with_marker(self, tmp_path, monkeypatch):
        import runs
        rd = tmp_path / "run"
        rd.mkdir()
        (rd / "metadata.json").write_text(json.dumps({"handle_id": "h1"}))
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        monkeypatch.setattr(runs, "index_run_dir", lambda *a, **k: None)
        runs.stamp_run_verdict(
            source="closure", confidence=0.9,
            summary="s" * 900, goal_achieved=True,
            downgrade_reason="d" * 900)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_verdict_summary"].startswith("s" * 900)
        assert meta["goal_verdict_downgrade_reason"].startswith("d" * 900)

    def test_format_prior_decisions_schema_max_brief_honors_budget(self, tmp_path, monkeypatch):
        # One legal schema-max prior (2000 tried + 2000 why + 3x800
        # lessons) overflows the 6000 default: the frame (header, footer
        # instruction) must survive and the return must honor max_chars.
        import decision_prior as dp
        card = {"handle_id": "big", "goal": "g", "success_class": "failed",
                "decision_prior": {
                    "handle_id": "big", "goal": "g", "outcome": "failed",
                    "goal_achieved": False, "when": "2026-08-13",
                    "what_was_tried": "t" * 2000, "why": "w" * 2000,
                    "lessons": ["l" * 800] * 3}}
        rd = tmp_path / "big"
        rd.mkdir()
        (rd / "run_card.json").write_text(json.dumps(card))
        monkeypatch.setattr(dp, "_run_dir_for", lambda hid: rd)
        block = dp.format_prior_decisions([{"handle_id": "big"}])
        assert len(block) <= 6000
        assert block.rstrip().endswith("change the approach.")
        assert "[truncated:" in block
