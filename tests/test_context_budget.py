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
        # 2026-08-14 fixpoint round: producers, siblings, and consumers
        # the review-fix round itself missed (or introduced).
        ("handle", "return text[:limit]"),
        ("handle", "or \"\")[:500]"),
        ("loop_init", '(evidence or "")[:500]'),
        ("agent_loop", "_fence_msg[:500]"),
        ("loop_finalize", "{_cmerge.detail}\"[:500]"),
        ("director", "{reasoning}\"\n    )[:500]"),
        ("navigator_prompt", "decision.reasoning[:600]"),
        ("loop_blocked", "reasoning[:300]"),
        ("loop_blocked", "reasoning[:500]"),
        ("navigator_shadow", '"reasoning", ""))[:600]'),
        ("cli", "str(_verdict.summary)[:300]"),
        ("notify", '"summary", "")))[:300]'),
        ("observe", "detail[:200],"),
        ("notify_telegram", "summary[:300]"),
        ("run_curation", "excerpt[-1000:])"),
        ("decision_prior", "lessons[:3]"),
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
        assert "more prior attempt(s) not shown" in block
        assert block.rstrip().endswith("change the approach.")
        # Wide default: all three ride whole, nothing dropped or clipped.
        full = dp.format_prior_decisions([{"handle_id": h} for h in briefs])
        assert all(f"attempt {i}" in full for i in range(3))
        assert "not shown" not in full and "[truncated:" not in full


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
            downgrade_reason="d" * 900, gaps=None)
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


class TestFixpointRoundBehaviors:
    """Behavior pins from the 2026-08-14 fixpoint adversarial review."""

    def test_clip_bound_holds_against_marker_shaped_input(self):
        # A forged/coincidental marker suffix with huge digit runs must not
        # ride the idempotence path through an arbitrarily small cap.
        from context_budget import _CLIP_MARKER_MAX, clip
        forged = "AB" + " … [truncated: first " + "1" * 25000 + " of 9 characters]"
        out = clip(forged, 10)
        assert len(out) <= 10 + _CLIP_MARKER_MAX + 64
        # A GENUINE clipped value still passes through unchanged.
        real = clip("z" * 3000, 2000)
        assert clip(real, 2000) == real

    def test_clear_run_verdict_removes_the_whole_tuple(self, tmp_path, monkeypatch):
        import runs
        rd = tmp_path / "run"
        rd.mkdir()
        (rd / "metadata.json").write_text(json.dumps({
            "handle_id": "h1", "goal_achieved": False,
            "goal_verdict_source": "now_self_verdict",
            "goal_verdict_confidence": 0.9,
            "goal_verdict_summary": "first attempt failed",
            "goal_verdict_gaps": ["missing"], "status": "done"}))
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        monkeypatch.setattr(runs, "index_run_dir", lambda *a, **k: None)
        runs.clear_run_verdict()
        meta = json.loads((rd / "metadata.json").read_text())
        for key in ("goal_achieved", "goal_verdict_source",
                    "goal_verdict_confidence", "goal_verdict_summary",
                    "goal_verdict_gaps"):
            assert key not in meta, key
        assert meta["status"] == "done"   # non-verdict keys untouched

    def test_retry_stamp_always_replaces_summary(self):
        # Source pin for the consensus HIGH, updated for round 14: the
        # delivered retry's state is replaced in ONE atomic write
        # (stamp_delivered_now_retry) — judged retries carry a summary
        # unconditionally (placeholder when the judge gave a bare
        # boolean); unjudged delivery clears the verdict tuple; the stop
        # tuple sets-or-clears in the same write; a failed stamp is
        # surfaced, not swallowed.
        import importlib
        from pathlib import Path
        src = Path(importlib.import_module("handle").__file__).read_text()
        assert "retry judged; no rationale recorded " in src
        assert "stamp_delivered_now_retry as _sdr_rr" in src
        assert "delivered-state stamp FAILED" in src

    def test_stamp_delivered_now_retry_state_matrix(self, tmp_path, monkeypatch):
        # The round-14 state matrix through the real writer: judged
        # replaces the tuple; unjudged clears it; stop tuple follows its
        # own set-or-clear in the SAME atomic write.
        import runs
        rd = tmp_path / "run"
        rd.mkdir()
        (rd / "metadata.json").write_text(json.dumps({
            "handle_id": "h1", "goal_achieved": False,
            "goal_verdict_source": "provenance",
            "goal_verdict_confidence": 0.91,
            "goal_verdict_summary": "first attempt failed",
            "goal_verdict_downgrade_reason": "no behavioral probe",
            "goal_verdict_gaps": ["stale gap"],
            "stop_verdict": "lost-the-plot",
            "stop_evidence": "old evidence", "status": "done"}))
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        monkeypatch.setattr(runs, "index_run_dir", lambda *a, **k: None)
        runs.stamp_delivered_now_retry(
            retry_marker="recovered", judged=True, goal_achieved=True,
            source="now_self_verdict", summary="retry delivered")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_achieved"] is True
        assert meta["goal_verdict_summary"] == "retry delivered"
        # WHOLE-tuple replacement (round-15 review: this branch's own
        # field list had drifted — stale confidence/downgrade/gaps
        # survived a judged transition).
        for key in ("goal_verdict_confidence",
                    "goal_verdict_downgrade_reason", "goal_verdict_gaps"):
            assert key not in meta, key
        assert "stop_verdict" not in meta and "stop_evidence" not in meta
        assert meta["now_artifact_retry"] == "recovered"
        # Unjudged delivery clears the whole verdict tuple.
        runs.stamp_delivered_now_retry(
            retry_marker="unrecovered", judged=False,
            stop_verdict="lost-the-plot", stop_evidence="retry evidence")
        meta = json.loads((rd / "metadata.json").read_text())
        for key in ("goal_achieved", "goal_verdict_source",
                    "goal_verdict_summary", "goal_verdict_gaps"):
            assert key not in meta, key
        assert meta["stop_verdict"] == "lost-the-plot"
        assert meta["stop_evidence"] == "retry evidence"

    def test_small_budget_contracts_hold(self, tmp_path, monkeypatch):
        import decision_prior as dp
        card = {"handle_id": "sm", "goal": "g", "success_class": "failed",
                "decision_prior": {
                    "handle_id": "sm", "goal": "g", "outcome": "failed",
                    "goal_achieved": False, "when": "2026-08-14",
                    "what_was_tried": "t" * 900, "why": "w" * 300,
                    "lessons": []}}
        rd = tmp_path / "sm"
        rd.mkdir()
        (rd / "run_card.json").write_text(json.dumps(card))
        monkeypatch.setattr(dp, "_run_dir_for", lambda hid: rd)
        # Documented minimum budget of 512 since round 16 (budgets too
        # small for an honest frame forced a bare mid-header slice) —
        # tiny requests are clamped UP and the block stays semantically
        # whole: frame intact, cuts announced.
        for budget in (32, 100, 300, 512):
            out = dp.format_prior_decisions(
                [{"handle_id": "sm"}], max_chars=budget)
            assert len(out) <= 512, budget
            assert out.rstrip().endswith("change the approach.")

    def test_recall_block_small_budget_holds(self):
        from recall import PriorAttempt, RecallResult
        attempts = [PriorAttempt(goal="g" * 500, handle_id="h",
                                 status="stuck",
                                 when="2026-08-14T00:00:00+00:00",
                                 match="exact")]
        r = RecallResult(thread=None, prior_attempts=attempts,
                         lessons="L" * 5000)
        for budget in (0, 20, 32, 128, 1200):
            out = r.as_context_block(max_chars=budget)
            assert len(out) <= budget, budget
            assert "first -" not in out   # no nonsense negative marker


class TestRound13Behaviors:
    """Behavior pins from the 2026-08-14 round-13 review (convergence loop)."""

    def _seeded(self, tmp_path, monkeypatch):
        import runs
        rd = tmp_path / "run"
        rd.mkdir()
        (rd / "metadata.json").write_text(json.dumps({
            "handle_id": "h1", "goal_achieved": False,
            "goal_verdict_source": "closure", "goal_verdict_confidence": 0.9,
            "goal_verdict_summary": "failed", "goal_verdict_gaps": ["OLD GAP"],
            "goal_verdict_downgrade_reason": "old downgrade",
            "stop_verdict": "lost-the-plot", "stop_evidence": "old evidence",
            "status": "done"}))
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        monkeypatch.setattr(runs, "index_run_dir", lambda *a, **k: None)
        return runs, rd

    def test_stamp_run_verdict_replaces_gaps_too(self, tmp_path, monkeypatch):
        # Round-13 consensus HIGH: the replacement API replaced every tuple
        # member EXCEPT gaps, so an achieved retry kept its failed
        # predecessor's "Missing:" list.
        runs, rd = self._seeded(tmp_path, monkeypatch)
        runs.stamp_run_verdict(goal_achieved=True, source="closure",
                               confidence=0.95, summary="delivered",
                               gaps=None)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_achieved"] is True
        assert "goal_verdict_gaps" not in meta
        assert "goal_verdict_downgrade_reason" not in meta
        # And a verdict WITH gaps replaces rather than merges.
        runs.stamp_run_verdict(goal_achieved=False, source="closure",
                               confidence=0.8, summary="regressed",
                               gaps=["NEW GAP"])
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_verdict_gaps"] == ["NEW GAP"]

    def test_clear_run_stop_verdict_removes_stop_tuple(self, tmp_path, monkeypatch):
        # Round-13 Skeptic HIGH: a recovered NOW retry kept attempt one's
        # stop_verdict="lost-the-plot" beside status=done.
        runs, rd = self._seeded(tmp_path, monkeypatch)
        runs.clear_run_stop_verdict()
        meta = json.loads((rd / "metadata.json").read_text())
        assert "stop_verdict" not in meta
        assert "stop_evidence" not in meta
        assert meta["goal_verdict_summary"] == "failed"  # verdict untouched

    def test_lesson_count_cap_announces_omission(self):
        from decision_prior import make_decision_prior
        prior = make_decision_prior(
            handle_id="h", goal="g", outcome="success", goal_achieved=True,
            when="2026-08-14", what_was_tried="t", why="w",
            lessons=[f"lesson {i}" for i in range(7)])
        assert len(prior["lessons"]) == 6
        assert "+2 more lesson(s)" in prior["lessons"][-1]


class TestBypassBurndownBehaviors:
    """Owner pins for the 2026-08-15 verdict-bypass burn-down: every raw
    write_metadata/stamp_run_metadata site carrying tuple keys was routed
    through a runs.py schema owner; these pin the owners' semantics."""

    def _pin(self, tmp_path, monkeypatch, seed):
        import runs
        rd = tmp_path / "run"
        rd.mkdir(exist_ok=True)
        (rd / "metadata.json").write_text(json.dumps(seed))
        monkeypatch.setattr(runs, "current_run_dir", lambda: rd)
        monkeypatch.setattr(runs, "index_run_dir", lambda *a, **k: None)
        return runs, rd

    def test_stop_owner_sets_pair_and_clips(self, tmp_path, monkeypatch):
        runs, rd = self._pin(tmp_path, monkeypatch, {"status": "stuck"})
        runs.stamp_run_stop_verdict(
            stop_verdict="external-interrupt", stop_evidence="e" * 2000)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["stop_verdict"] == "external-interrupt"
        assert meta["stop_evidence"].startswith("e" * 800)
        assert "[truncated: first 800 of 2000 characters]" \
            in meta["stop_evidence"]

    def test_stop_owner_empty_verdict_clears_stale_pair(
            self, tmp_path, monkeypatch):
        # loop_finalize's contract: metadata reflects THIS ending — an
        # earlier restarted loop's verdict must not stand. The owner POPS
        # (parity with clear_run_stop_verdict); consumers read
        # `meta.get("stop_verdict") or ""` so absent == the old "".
        runs, rd = self._pin(tmp_path, monkeypatch, {
            "stop_verdict": "lost-the-plot", "stop_evidence": "old",
            "status": "done"})
        runs.stamp_run_stop_verdict(stop_verdict="", stop_evidence="")
        meta = json.loads((rd / "metadata.json").read_text())
        assert "stop_verdict" not in meta
        assert "stop_evidence" not in meta
        assert meta["status"] == "done"

    def test_stop_owner_pause_reason_preserves_history(
            self, tmp_path, monkeypatch):
        # Falsy pause_reason leaves the stranded sweep's post-hoc
        # writer-died stamp standing (2026-07-31 slice-1 review #2);
        # a new typed reason still overwrites.
        runs, rd = self._pin(tmp_path, monkeypatch, {
            "pause_reason": "writer-died"})
        runs.stamp_run_stop_verdict(
            stop_verdict="out-of-budget", stop_evidence="x")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["pause_reason"] == "writer-died"
        runs.stamp_run_stop_verdict(
            stop_verdict="out-of-budget", stop_evidence="x",
            pause_reason="operator-manual")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["pause_reason"] == "operator-manual"

    def test_stop_owner_refine_note_composes_in_lock(
            self, tmp_path, monkeypatch):
        # director.close: a later, more specific verdict records what it
        # refined instead of silently overwriting (and the composition
        # happens inside the owner's lock, not around it).
        runs, rd = self._pin(tmp_path, monkeypatch, {
            "stop_verdict": "external-interrupt", "stop_evidence": "old"})
        runs.stamp_run_stop_verdict(
            stop_verdict="reachable-but-not-worth-it",
            stop_evidence="closed by operator", run_dir=rd,
            refine_note=True)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["stop_verdict"] == "reachable-but-not-worth-it"
        assert meta["stop_evidence"] \
            == "closed by operator [refines: external-interrupt]"
        # Same verdict re-stamped → no self-referential note.
        runs.stamp_run_stop_verdict(
            stop_verdict="reachable-but-not-worth-it",
            stop_evidence="closed again", run_dir=rd, refine_note=True)
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["stop_evidence"] == "closed again"

    def test_stop_owner_explicit_run_dir_wins(self, tmp_path, monkeypatch):
        import runs
        other = tmp_path / "other"
        other.mkdir()
        (other / "metadata.json").write_text(json.dumps({}))
        runs_mod, rd = self._pin(tmp_path, monkeypatch, {})
        runs_mod.stamp_run_stop_verdict(
            stop_verdict="v", stop_evidence="e", run_dir=other)
        assert "stop_verdict" not in json.loads(
            (rd / "metadata.json").read_text())
        assert json.loads(
            (other / "metadata.json").read_text())["stop_verdict"] == "v"

    def test_unjudged_source_owner_touches_nothing_else(
            self, tmp_path, monkeypatch):
        # The deliberate-partial owner: absence-means-not-judged is the
        # tri-state — goal_achieved and gaps must NOT be popped or set.
        runs, rd = self._pin(tmp_path, monkeypatch, {
            "goal_achieved": False, "goal_verdict_gaps": ["real gap"],
            "status": "incomplete"})
        runs.stamp_unjudged_verdict_source("closure_error", "judge crashed")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_verdict_source"] == "closure_error"
        assert meta["goal_verdict_summary"] == "judge crashed"
        assert meta["goal_achieved"] is False
        assert meta["goal_verdict_gaps"] == ["real gap"]

    def test_unjudged_source_owner_empty_summary_writes_none(
            self, tmp_path, monkeypatch):
        runs, rd = self._pin(tmp_path, monkeypatch, {})
        runs.stamp_unjudged_verdict_source("no_steps_completed")
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_verdict_source"] == "no_steps_completed"
        assert "goal_verdict_summary" not in meta

    def test_contested_owner_sets_pair_plus_context(
            self, tmp_path, monkeypatch):
        runs, rd = self._pin(tmp_path, monkeypatch, {
            "goal_achieved": False})
        runs.stamp_run_verdict_contested(
            contested_by="closure",
            extra={"closure_complete": True, "closure_confidence": 0.92})
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_verdict_contested"] is True
        assert meta["goal_verdict_contested_by"] == "closure"
        assert meta["closure_confidence"] == 0.92
        assert meta["goal_achieved"] is False

    def test_owner_extra_rejects_tuple_keys(self, tmp_path, monkeypatch):
        # Smuggling a tuple member through extra recreates the exact
        # partial-write drift the owners end — fail loud at the call.
        import pytest as _pytest
        runs, rd = self._pin(tmp_path, monkeypatch, {})
        with _pytest.raises(ValueError):
            runs.stamp_run_verdict_contested(
                contested_by="closure",
                extra={"goal_achieved": True})
        with _pytest.raises(ValueError):
            runs.stamp_run_verdict(
                goal_achieved=True, source="s", confidence=0.5,
                summary="x", gaps=None,
                extra={"stop_verdict": "smuggled"})

    def test_verdict_owner_extra_rides_the_same_write(
            self, tmp_path, monkeypatch):
        runs, rd = self._pin(tmp_path, monkeypatch, {})
        runs.stamp_run_verdict(
            goal_achieved=False, source="closure_stamp_failed",
            confidence=0.7, summary="why", gaps=None,
            extra={"loop_ids": ["a", "b"]})
        meta = json.loads((rd / "metadata.json").read_text())
        assert meta["goal_achieved"] is False
        assert meta["loop_ids"] == ["a", "b"]
