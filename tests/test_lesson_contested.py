"""Retirement-by-contradiction (2026-08-02, Jeremy: "time to level the decay
up"): lessons gain a contested state mirroring the standing-rule grey flip.

Contested lessons leave every injection surface (inject_tiered_lessons,
query_lessons, search_graveyard, flat-ledger load_lessons, canon candidates),
never promote to LONG, and never confirm — re-sightings via dedup bump
times_reinforced only (evidence for a future refight slice) while score and
the decay anchor freeze, so MEDIUM rows retire on the decay schedule and
contestation is the retirement mechanism for decay-free LONG rows.
Acceptance corpus: the six chunk-1 surprise-read contradictions.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_web import (
    MemoryTier,
    TieredLesson,
    _is_contested,
    _mutate_tiered_lessons,
    _tiered_lessons_path,
    contest_lesson,
    inject_tiered_lessons,
    load_tiered_lessons,
    promote_lesson,
    query_lessons,
    record_tiered_lesson,
    run_decay_cycle,
    search_graveyard,
    short_clear,
)
from memory_ledger import (
    _memory_dir,
    _store_lesson,
    contest_flat_lesson,
    load_lessons,
)
from captains_log import LESSON_CONTESTED


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    short_clear()
    return tmp_path


def _events(event_type: str):
    path = _memory_dir() / "captains_log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event_type") == event_type
    ]


def _set(lesson_id: str, tier: str = MemoryTier.MEDIUM, **attrs):
    """Set attributes on a stored lesson under the lock."""
    def _mut(lessons):
        for l in lessons:
            if l.lesson_id == lesson_id:
                for k, v in attrs.items():
                    setattr(l, k, v)
        return lessons
    _mutate_tiered_lessons(tier, _mut)


def _raw(lesson_id: str, tier: str = MemoryTier.MEDIUM) -> TieredLesson:
    rows = load_tiered_lessons(tier=tier, limit=None, raw=True)
    return next(l for l in rows if l.lesson_id == lesson_id)


# ---------------------------------------------------------------------------
# The verb + schema
# ---------------------------------------------------------------------------


class TestContestVerb:
    def test_old_rows_deserialize_uncontested(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Always use two sources.", "research", "done",
                                  source_goal="g")
        # Simulate a pre-contested-field row: strip the key from disk.
        path = _tiered_lessons_path(MemoryTier.MEDIUM)
        row = json.loads(path.read_text().strip())
        row.pop("contested")
        path.write_text(json.dumps(row) + "\n")
        loaded = _raw(tl.lesson_id)
        assert loaded.contested == {}
        assert not _is_contested(loaded)

    def test_contest_stamps_both_stores_and_emits(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Tighter step count improves plans.",
                                  "planning", "done", source_goal="g")
        # UU-4 dual-write: same id in the flat ledger.
        _store_lesson("planning", "done", "Tighter step count improves plans.",
                      "g", lesson_id=tl.lesson_id)
        assert contest_lesson(tl.lesson_id, "operator surprise read: adds "
                              "constraints, not quality", source="operator:test")
        stored = _raw(tl.lesson_id)
        assert stored.contested["source"] == "operator:test"
        assert "constraints" in stored.contested["reason"]
        assert stored.contested["contested_at"]
        flat = load_lessons(task_type="planning", include_contested=True)
        assert flat and flat[0].contested["source"] == "operator:test"
        events = _events(LESSON_CONTESTED)
        assert len(events) == 1
        assert events[0]["subject"] == tl.lesson_id
        assert events[0]["context"]["flat"] is True

    def test_contest_unknown_id_returns_false(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        assert not contest_lesson("nope", "r", source="operator:test")
        assert _events(LESSON_CONTESTED) == []

    def test_contest_idempotent_first_stamp_wins(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("L", "general", "done", source_goal="g")
        assert contest_lesson(tl.lesson_id, "first", source="operator:a")
        assert contest_lesson(tl.lesson_id, "second", source="operator:b")
        stored = _raw(tl.lesson_id)
        assert stored.contested["reason"] == "first"
        assert len(_events(LESSON_CONTESTED)) == 1  # no duplicate event

    def test_contest_flat_only_lesson_hits(self, monkeypatch, tmp_path):
        """A lesson that lives only in the flat ledger (legacy rows) is
        still contestable — adjudication cites flat-ledger ids."""
        _setup(monkeypatch, tmp_path)
        l = _store_lesson("general", "done", "Flat-only wisdom.", "g")
        assert contest_lesson(l.lesson_id, "r", source="operator:test")
        assert load_lessons(task_type="general") == []
        assert _events(LESSON_CONTESTED)[0]["context"]["tier"] == ""


# ---------------------------------------------------------------------------
# Exclusion surfaces
# ---------------------------------------------------------------------------


class TestExclusionSurfaces:
    def test_injection_excludes_contested(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Grep the saved source for cited claims.",
                                  "research", "done", source_goal="g")
        assert "Grep the saved" in inject_tiered_lessons("research")
        contest_lesson(tl.lesson_id, "false-pass on scope generalization",
                       source="operator:test")
        assert "Grep the saved" not in inject_tiered_lessons("research")

    def test_long_tier_injection_excludes_contested(self, monkeypatch, tmp_path):
        """LONG is decay-free — contestation is its only retirement path,
        so the LONG injection filter is load-bearing, not belt-and-braces."""
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Cheaper code surface wins.", "planning",
                                  "done", source_goal="g", tier=MemoryTier.LONG)
        assert "Cheaper code" in inject_tiered_lessons("planning")
        contest_lesson(tl.lesson_id, "L4 surprise read", source="operator:test")
        assert "Cheaper code" not in inject_tiered_lessons("planning")

    def test_query_excludes_unless_opt_in(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Name a specific failure path.", "general",
                                  "done", source_goal="g")
        contest_lesson(tl.lesson_id, "describes how, not what",
                       source="operator:test")
        assert query_lessons("failure path") == []
        opted = query_lessons("failure path", include_contested=True)
        assert [l.lesson_id for l in opted] == [tl.lesson_id]

    def test_graveyard_excludes_contested(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Decayed but findable wisdom.", "general",
                                  "done", source_goal="g")
        _set(tl.lesson_id, score=0.35)
        assert search_graveyard("findable wisdom")
        contest_lesson(tl.lesson_id, "r", source="operator:test")
        assert search_graveyard("findable wisdom") == []

    def test_flat_ledger_excludes_contested(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        l = _store_lesson("general", "done", "Flat wisdom.", "g")
        contest_flat_lesson(l.lesson_id, {"reason": "r", "source": "s",
                                          "contested_at": "t"})
        assert load_lessons(task_type="general") == []
        assert load_lessons(task_type="general",
                            include_contested=True)[0].lesson == "Flat wisdom."


# ---------------------------------------------------------------------------
# Promotion + confirmation guards
# ---------------------------------------------------------------------------


class TestPromotionGuards:
    def test_contested_never_promotes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Promotable wisdom.", "general", "done",
                                  source_goal="g")
        _set(tl.lesson_id, score=1.0, sessions_validated=3)
        contest_lesson(tl.lesson_id, "r", source="operator:test")
        assert not promote_lesson(tl.lesson_id)
        result = run_decay_cycle()
        assert result["promoted"] == 0
        assert load_tiered_lessons(tier=MemoryTier.LONG, limit=None) == []

    def test_dedup_rerecord_counts_sighting_but_never_confirms(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Sticky wisdom here.", "general", "done",
                                  source_goal="g")
        contest_lesson(tl.lesson_id, "r", source="operator:test")
        before = _raw(tl.lesson_id)
        record_tiered_lesson("Sticky wisdom here.", "general", "done",
                             source_goal="g2")
        after = _raw(tl.lesson_id)
        assert after.times_reinforced == before.times_reinforced + 1
        assert after.sessions_validated == before.sessions_validated
        assert after.score == before.score              # no reinforce bump
        assert after.last_reinforced == before.last_reinforced  # decay anchor frozen
        assert _is_contested(after)

    def test_rerecord_never_clears_quarantine_or_provisional_on_contested(
            self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson("Quarantined wisdom.", "general", "done",
                                  source_goal="g", provisional=True,
                                  minted_from="prompt")
        contest_lesson(tl.lesson_id, "r", source="operator:test")
        record_tiered_lesson("Quarantined wisdom.", "general", "done",
                             source_goal="g2", minted_from="outcome")
        after = _raw(tl.lesson_id)
        assert after.minted_from == "prompt"   # citizenship not laundered
        assert after.provisional is True
        assert _is_contested(after)


# ---------------------------------------------------------------------------
# Adjudication wiring: a certified failure now retires cited lessons
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def __init__(self, *payloads: str):
        self.payloads = list(payloads)

    def complete(self, messages, **kwargs):
        content = self.payloads.pop(0) if self.payloads else ""
        return types.SimpleNamespace(content=content)


class TestAdjudicationWiring:
    def _seed_candidate(self, loop_id, lesson_ids):
        from captains_log import log_event, CONTRADICTION_CANDIDATE
        log_event(
            CONTRADICTION_CANDIDATE,
            subject=loop_id,
            summary="test candidate",
            context={"loop_id": loop_id, "rule_ids": [],
                     "lesson_ids": lesson_ids,
                     "failure_summary": "the run failed",
                     "goal_preview": "test goal"},
        )

    def test_yes_verdict_contests_cited_lesson(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_lens import adjudicate_contradiction_candidates
        # Adjudication resolves cited lesson text from the flat ledger;
        # dual-write so the tiered row shares the id (UU-4).
        flat = _store_lesson("general", "done", "Cited wisdom that failed.",
                             "g")
        record_tiered_lesson("Cited wisdom that failed.", "general", "done",
                             source_goal="g", lesson_id=flat.lesson_id)
        self._seed_candidate("lp-c1", [flat.lesson_id])
        counts = adjudicate_contradiction_candidates(_FakeAdapter(
            f'{{"contradicted": "yes", "contradicted_ids": '
            f'["{flat.lesson_id}"], "reasoning": "the lesson steered it wrong"}}'))
        assert counts["contradicted"] == 1
        stored = _raw(flat.lesson_id)
        assert _is_contested(stored)
        assert stored.contested["source"] == "contradiction_adjudication:lp-c1"
        assert "steered it wrong" in stored.contested["reason"]
        assert load_lessons(task_type="general") == []  # flat store flipped too
        from captains_log import CONTRADICTION_ADJUDICATED
        adjudicated = _events(CONTRADICTION_ADJUDICATED)
        # Honest mutation record: the lesson actually took the hit.
        assert adjudicated[0]["context"]["applied"] == [flat.lesson_id]

    def test_no_verdict_leaves_lesson_alone(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        from knowledge_lens import adjudicate_contradiction_candidates
        flat = _store_lesson("general", "done", "Innocent wisdom.", "g")
        self._seed_candidate("lp-c2", [flat.lesson_id])
        adjudicate_contradiction_candidates(_FakeAdapter(
            '{"contradicted": "no", "reasoning": "unrelated failure"}'))
        assert load_lessons(task_type="general")[0].lesson == "Innocent wisdom."
        assert _events(LESSON_CONTESTED) == []
