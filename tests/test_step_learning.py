"""Per-step learning (2026-07-27): provisional lessons from verified steps
+ the achieved-not-done verdict-preferred classify fix.

Learn at the granularity where verification actually happened: a run whose
high-level outcome failed the learnability gate may still contain
individually-verified steps whose method evidence enters the tiered store as
PROVISIONAL — reduced entry score, excluded from every injection surface,
never promoted to LONG — until a confirmed-context re-record clears the flag.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_web import (
    MemoryTier,
    NOVELTY_BONUS,
    PROVISIONAL_ENTRY_SCORE,
    PROMOTE_MIN_SCORE,
    PROMOTE_MIN_SESSIONS,
    _tiered_lessons_path,
    inject_tiered_lessons,
    load_tiered_lessons,
    query_lessons,
    record_tiered_lesson,
    run_decay_cycle,
)


# ---------------------------------------------------------------------------
# classify_outcome: achieved-not-done (the SF-2 inversion)
# ---------------------------------------------------------------------------

def _classify(meta):
    from run_curation import classify_outcome
    card = {}
    classify_outcome(Path("/nonexistent"), meta, card)
    return card


class TestAchievedNotDone:
    def test_stuck_with_judged_true_is_achieved_not_done(self):
        # The live specimens' shape (692bd96f-brisk-lichen, d9f01e13-golden-birch):
        # judged goal_achieved=True, process status "stuck" — previously "failed".
        card = _classify({"status": "stuck", "goal_achieved": True})
        assert card["success_class"] == "achieved-not-done"

    def test_partial_with_judged_true_is_achieved_not_done(self):
        card = _classify({"status": "partial", "goal_achieved": True})
        assert card["success_class"] == "achieved-not-done"

    def test_interrupt_with_judged_true_prefers_verdict(self):
        # Item-5 decree: the interrupt is an event (status channel); the
        # judged verdict is the map observation and wins the class.
        card = _classify({"status": "interrupted", "goal_achieved": True})
        assert card["success_class"] == "achieved-not-done"

    def test_stuck_unjudged_still_failed(self):
        card = _classify({"status": "stuck"})
        assert card["success_class"] == "failed"

    def test_stuck_judged_false_still_failed(self):
        card = _classify({"status": "stuck", "goal_achieved": False})
        assert card["success_class"] == "failed"

    def test_incomplete_judged_false_keeps_done_not_achieved(self):
        # The chunk-9 #4 rebucket outranks the new branch (order pin).
        card = _classify({"status": "incomplete", "goal_achieved": False})
        assert card["success_class"] == "done-not-achieved"

    def test_achieved_not_done_is_learnable(self):
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome({"success_class": "achieved-not-done"}) is True

    def test_raw_row_verdict_preferred(self):
        # Ledger-row twin: judged True beats a failure-shaped status.
        from outcome_policy import is_learnable_outcome
        assert is_learnable_outcome(
            {"status": "stuck", "goal_achieved": True}) is True
        assert is_learnable_outcome({"status": "stuck"}) is False

    def test_report_and_notify_maps_cover_new_classes(self):
        from loop_report import _SUCCESS_CLASS_INFO
        from notify_telegram import _CLASS_LABEL
        assert "achieved-not-done" in _SUCCESS_CLASS_INFO
        assert "interrupted" in _SUCCESS_CLASS_INFO
        assert "achieved-not-done" in _CLASS_LABEL
        assert "interrupted" in _CLASS_LABEL


# ---------------------------------------------------------------------------
# Provisional lifecycle in the tiered store
# ---------------------------------------------------------------------------

class TestProvisionalLifecycle:
    def test_provisional_entry_score_below_confirmed_floor(self):
        tl = record_tiered_lesson(
            "Use the paginated endpoint for large result sets",
            task_type="agenda", outcome="step-verified", source_goal="g",
            provisional=True, k_samples=1,
        )
        assert tl.provisional is True
        # Empty store → novelty 1.0 → entry = PROVISIONAL_ENTRY_SCORE + bonus,
        # still under the confirmed 1.0 floor.
        assert tl.score == pytest.approx(PROVISIONAL_ENTRY_SCORE + NOVELTY_BONUS)
        assert tl.score < 1.0

    def test_provisional_round_trips_and_old_rows_default_false(self):
        record_tiered_lesson(
            "Provisional row", task_type="agenda", outcome="step-verified",
            source_goal="g", provisional=True)
        path = _tiered_lessons_path(MemoryTier.MEDIUM)
        # Simulate a pre-chunk row with no provisional key.
        legacy = {"lesson_id": "old00001", "task_type": "agenda",
                  "outcome": "done", "lesson": "Legacy confirmed row",
                  "source_goal": "g", "confidence": 0.5, "tier": "medium",
                  "score": 1.0, "last_reinforced": "2026-07-27"}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(legacy) + "\n")
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None)
        by_lesson = {r.lesson: r for r in rows}
        assert by_lesson["Provisional row"].provisional is True
        assert by_lesson["Legacy confirmed row"].provisional is False

    def test_excluded_from_query_lessons_until_confirmed(self):
        record_tiered_lesson(
            "Parsing the manifest needs the schema pass first",
            task_type="agenda", outcome="step-verified", source_goal="g",
            provisional=True)
        assert query_lessons("manifest schema parsing", n=5) == []
        got = query_lessons("manifest schema parsing", n=5,
                            include_provisional=True)
        assert len(got) == 1 and got[0].provisional is True

    def test_excluded_from_inject_tiered_lessons(self):
        record_tiered_lesson(
            "Provisional-only lesson", task_type="agenda",
            outcome="step-verified", source_goal="g", provisional=True)
        assert "Provisional-only lesson" not in inject_tiered_lessons("agenda")

    def test_confirmed_context_clears_flag(self):
        text = "Batch the API calls to stay under the rate limit"
        record_tiered_lesson(text, task_type="agenda",
                             outcome="step-verified", source_goal="g",
                             provisional=True)
        # Same lesson re-learned from a learnable run: promote-on-evidence.
        tl = record_tiered_lesson(text, task_type="agenda", outcome="done",
                                  source_goal="g2")
        assert tl.provisional is False
        assert tl.times_reinforced == 1
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert [r.provisional for r in rows] == [False]
        # And it now reaches retrieval.
        assert query_lessons("API rate limit batching", n=5) != []

    def test_provisional_context_does_not_clear_flag(self):
        text = "Batch the API calls to stay under the rate limit"
        first = record_tiered_lesson(text, task_type="agenda",
                                     outcome="step-verified", source_goal="g",
                                     provisional=True)
        before_sessions = first.sessions_validated
        before_score = first.score
        tl = record_tiered_lesson(text, task_type="agenda",
                                  outcome="step-verified", source_goal="g2",
                                  provisional=True)
        # The observation still reinforces score — but two failed-run
        # sightings are not confirmation: the flag stays, and
        # sessions_validated (the promotion/confidence counter) does not
        # move. Without the split, three provisional sightings would carry
        # promotion-grade validation while hidden, and the first
        # confirmation would promote to LONG immediately (review 2026-07-27).
        assert tl.provisional is True
        assert tl.times_reinforced == 1
        assert tl.sessions_validated == before_sessions
        assert tl.score > before_score

    def test_excluded_from_graveyard_and_resurrection(self):
        from knowledge_web import search_graveyard
        record_tiered_lesson(
            "Provisional graveyard bait", task_type="agenda",
            outcome="step-verified", source_goal="g", provisional=True)
        record_tiered_lesson(
            "Confirmed graveyard control", task_type="agenda",
            outcome="done", source_goal="g")
        # Window widened so both fresh rows are score-eligible: only the
        # confirmed row may surface, even with resurrect=True (resurrection
        # reinforces confirming=True — a topic match must not confirm).
        hits = search_graveyard("graveyard", min_score=0.0, max_score=2.0,
                                resurrect=True)
        texts = [h.lesson for h in hits]
        assert "Confirmed graveyard control" in texts
        assert "Provisional graveyard bait" not in texts
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        bait = next(r for r in rows if r.lesson == "Provisional graveyard bait")
        assert bait.provisional is True
        assert bait.times_reinforced == 0

    def test_promote_lesson_refuses_provisional_directly(self):
        from knowledge_web import promote_lesson
        tl = record_tiered_lesson(
            "Eligible but provisional, promoted by hand", task_type="agenda",
            outcome="step-verified", source_goal="g", provisional=True)
        path = _tiered_lessons_path(MemoryTier.MEDIUM)
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows[0]["score"] = PROMOTE_MIN_SCORE + 0.1
        rows[0]["sessions_validated"] = PROMOTE_MIN_SESSIONS
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        # The CLI (`maro memory promote`) calls promote_lesson directly —
        # the guard must live at the promotion boundary, not only in the
        # reinforce-hook/decay-cycle callers.
        assert promote_lesson(tl.lesson_id) is False
        assert load_tiered_lessons(tier=MemoryTier.LONG, limit=None, raw=True) == []
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert [r.provisional for r in rows] == [True]

    def test_provisional_never_promotes_to_long(self):
        record_tiered_lesson(
            "Eligible but provisional", task_type="agenda",
            outcome="step-verified", source_goal="g", provisional=True)
        # Force promotion eligibility on the stored row.
        path = _tiered_lessons_path(MemoryTier.MEDIUM)
        rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        rows[0]["score"] = PROMOTE_MIN_SCORE + 0.1
        rows[0]["sessions_validated"] = PROMOTE_MIN_SESSIONS
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        stats = run_decay_cycle(MemoryTier.MEDIUM)
        assert stats["promoted"] == 0
        assert load_tiered_lessons(tier=MemoryTier.LONG, limit=None) == []
        # The same store with the flag cleared DOES promote (guard is the
        # flag, not something else about the row).
        rows[0]["provisional"] = False
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        stats = run_decay_cycle(MemoryTier.MEDIUM)
        assert stats["promoted"] == 1

    def test_bridge_ingest_skips_provisional(self):
        record_tiered_lesson(
            "Confirmed for the bridge", task_type="agenda", outcome="done",
            source_goal="g")
        record_tiered_lesson(
            "Provisional for the bridge", task_type="agenda",
            outcome="step-verified", source_goal="g", provisional=True)
        from memory_bridge import ingest_lessons_to_store
        from memory_sqlite import SqliteMemoryStore
        store = SqliteMemoryStore(
            _tiered_lessons_path(MemoryTier.MEDIUM).parent / "bridge-test.db")
        stats = ingest_lessons_to_store(store)
        assert stats["ingested"] == 1
        texts = [i.content for i in store.recall("bridge", k=10)]
        assert any("Confirmed" in t for t in texts)
        assert not any("Provisional" in t for t in texts)


# ---------------------------------------------------------------------------
# extract_step_lessons
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.input_tokens = 10
        self.output_tokens = 10


class _FakeAdapter:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        self.messages = messages
        return _FakeResp(self.payload)


def _step(text, status="done", confidence="strong", result="ok"):
    from loop_types import step_from_decompose
    return step_from_decompose(
        text, 0, status=status, result=result, confidence=confidence)


class TestExtractStepLessons:
    def test_no_verified_strong_steps_no_llm_call(self):
        from memory import extract_step_lessons
        adapter = _FakeAdapter("[]")
        steps = [_step("a", confidence="weak"),
                 _step("b", confidence="inferred"),
                 _step("c", status="blocked", confidence="strong")]
        assert extract_step_lessons("g", steps, adapter=adapter) == 0
        assert adapter.calls == 0

    def test_killswitch_off_no_call(self, monkeypatch):
        import memory
        monkeypatch.setattr(memory, "_step_learning_enabled", lambda: False)
        adapter = _FakeAdapter("[]")
        assert memory.extract_step_lessons(
            "g", [_step("a")], adapter=adapter) == 0
        assert adapter.calls == 0

    def test_records_provisional_with_loop_evidence(self):
        from memory import extract_step_lessons
        payload = json.dumps([
            {"lesson": "Fetch the sitemap before crawling", "type": "execution"},
            {"lesson": "Retry the fetch once with backoff", "type": "recovery"},
        ])
        adapter = _FakeAdapter(payload)
        n = extract_step_lessons(
            "research the site", [_step("fetch sitemap")],
            adapter=adapter, loop_id="loop-sl-1")
        assert n == 2
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert all(r.provisional for r in rows)
        assert all("loop:loop-sl-1" in r.evidence_sources for r in rows)
        # "recovery" is not in the step-pass type set — coerced to execution.
        assert {r.lesson_type for r in rows} == {"execution"}

    def test_idempotent_via_row_stamp(self):
        from memory import extract_step_lessons
        from memory_ledger import record_outcome
        record_outcome(goal="g", status="stuck", summary="s",
                       loop_id="loop-sl-2")
        adapter = _FakeAdapter(json.dumps(
            [{"lesson": "Lesson once", "type": "execution"}]))
        assert extract_step_lessons(
            "g", [_step("a")], adapter=adapter, loop_id="loop-sl-2") == 1
        assert extract_step_lessons(
            "g", [_step("a")], adapter=adapter, loop_id="loop-sl-2") == 0
        assert adapter.calls == 1
        from memory_ledger import outcome_row_has_step_lessons
        assert outcome_row_has_step_lessons("loop-sl-2") is True

    def test_prompt_carries_negative_claim_bar(self):
        # Contract pin: the asymmetric bar (never record deadness claims
        # from a failed run) lives in the system prompt.
        from memory import _STEP_LESSON_SYSTEM
        assert "NEVER extract negative/deadness claims" in _STEP_LESSON_SYSTEM
        assert "NOT land" in _STEP_LESSON_SYSTEM

    def test_step_cap_logged_not_silent(self):
        from memory import extract_step_lessons, _STEP_LESSON_MAX_STEPS
        adapter = _FakeAdapter("[]")
        steps = [_step(f"s{i}") for i in range(_STEP_LESSON_MAX_STEPS + 4)]
        extract_step_lessons("g", steps, adapter=adapter)
        assert adapter.calls == 1
        sent = adapter.messages[1].content
        assert f"s{_STEP_LESSON_MAX_STEPS - 1}" in sent
        assert f"s{_STEP_LESSON_MAX_STEPS}" not in sent


# ---------------------------------------------------------------------------
# Step-trace confidence persistence
# ---------------------------------------------------------------------------

def test_record_step_trace_persists_confidence(tmp_path):
    from memory_ledger import record_step_trace, load_step_traces
    record_step_trace("oid-1", "g", [
        _step("verified one", confidence="strong"),
        _step("unverified one", confidence=""),
    ])
    traces = load_step_traces(["oid-1"])
    steps = traces["oid-1"]["steps"]
    assert steps[0]["confidence"] == "strong"
    assert "confidence" not in steps[1]


# ---------------------------------------------------------------------------
# Closure-lane runs defer run-level extraction for ALL statuses
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,expected_defer", [
    ("done", True),
    ("stuck", True),   # pre-review this extracted immediately, verdict-blind
    ("partial", True),
])
def test_finalize_defers_lessons_for_all_closure_lane_statuses(
        monkeypatch, status, expected_defer):
    """A stuck run later judged goal_achieved=True is achieved-not-done —
    extracting at finalize (verdict unknown) recorded its lessons
    failure-framed into confirmed injection surfaces (adversarial review
    2026-07-27, three-lens consensus). All closure-lane statuses defer."""
    from loop_finalize import _finalize_loop
    captured = {}

    def fake_reflect(goal, status, result_summary, task_type, project, **kw):
        captured.update(kw)

    import memory
    monkeypatch.setattr(memory, "reflect_and_record", fake_reflect)
    _finalize_loop(
        loop_id="fl-defer",
        goal="g",
        project="p",
        loop_status=status,
        step_outcomes=[],
        adapter=None,
        dry_run=False,
        verbose=False,
        total_tokens_in=1,
        total_tokens_out=1,
        elapsed_ms=1,
        had_no_matching_skill=False,
        defer_learning=True,
    )
    assert captured.get("defer_lessons") is expected_defer


def test_finalize_extracts_immediately_when_no_closure_will_run(monkeypatch):
    """defer_learning=False (no closure lane) keeps the fail-safe: immediate
    extraction, any status."""
    from loop_finalize import _finalize_loop
    captured = {}

    def fake_reflect(goal, status, result_summary, task_type, project, **kw):
        captured.update(kw)

    import memory
    monkeypatch.setattr(memory, "reflect_and_record", fake_reflect)
    _finalize_loop(
        loop_id="fl-nodefer",
        goal="g",
        project="p",
        loop_status="stuck",
        step_outcomes=[],
        adapter=None,
        dry_run=False,
        verbose=False,
        total_tokens_in=1,
        total_tokens_out=1,
        elapsed_ms=1,
        had_no_matching_skill=False,
        defer_learning=False,
    )
    assert captured.get("defer_lessons") is False
