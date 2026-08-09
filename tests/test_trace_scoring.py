"""Trace scoring (§5 cut B, 2026-08-09) — the LeAct acceptance filter
applied to the reasoning traces thinkback/evolver mint.

Three pieces: (1) thinkback mints enter the TIERED store provisional with
minted_by="thinkback" (pre-change: full-citizen FLAT rows that shipped
straight into recall top-up ungated); (2) evolver prompt_tweak mints carry
minted_by="evolver" (not provisional — that category has its own
EVOLVER_VERDICT lifecycle and exists to be injected); (3) delta_replay
--origin selects rows by producer stamp, and on acting runs a provisional
row clearing the promote bars gets confirm_lesson_by_delta — provisional
cleared, stays MEDIUM — instead of promotion. The filter's verdict gates
tiering, not just annotation (consumer-first).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from knowledge_web import (
    MemoryTier,
    _mutate_tiered_lessons,
    confirm_lesson_by_delta,
    contest_lesson,
    inject_tiered_lessons,
    load_tiered_lessons,
    record_tiered_lesson,
    short_clear,
)
from memory_ledger import _memory_dir, load_lessons
from delta_replay import lesson_in_prompt


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("MARO_WORKSPACE", str(tmp_path))
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


def _raw(lesson_id: str, tier: str = MemoryTier.MEDIUM):
    rows = load_tiered_lessons(tier=tier, limit=None, raw=True)
    return next((l for l in rows if l.lesson_id == lesson_id), None)


# Reason-stratum lesson text (a "why", not an imperative rule).
TRACE_LESSON = ("the retry loop stalled because the worker cached the dead "
                "endpoint between attempts")

GOOD_EVIDENCE = {"delta": 0.59, "jackknife_spread": 0.09, "n_calls": 18,
                 "stratum": "reason"}


# ---------------------------------------------------------------------------
# Thinkback mint path
# ---------------------------------------------------------------------------

class TestThinkbackMint:
    def _save(self, lessons, run_id="run123"):
        from thinkback import _save_thinkback_lessons
        _save_thinkback_lessons("improve the retry loop", lessons, run_id)

    def test_mints_tiered_provisional_with_producer_stamp(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        self._save([TRACE_LESSON])
        rows = load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None, raw=True)
        assert len(rows) == 1
        row = rows[0]
        assert row.provisional is True
        assert row.minted_by == "thinkback"
        assert row.evidence_sources == ["thinkback:run123"]
        # run_id rides provenance, not the lesson text (pre-change prefix
        # polluted every replay/injection surface)
        assert row.lesson == TRACE_LESSON
        assert "[thinkback" not in row.lesson

    def test_no_flat_ledger_write(self, monkeypatch, tmp_path):
        """The old path shipped narrations into recall top-up as full flat
        citizens — the exact ungated surface the acceptance gate closes."""
        _setup(monkeypatch, tmp_path)
        self._save([TRACE_LESSON])
        assert load_lessons(include_quarantined=True,
                            include_contested=True) == []

    def test_provisional_mint_not_injected(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        self._save([TRACE_LESSON])
        assert TRACE_LESSON not in inject_tiered_lessons("general")

    def test_blank_lines_skipped(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        self._save(["", "   "])
        assert load_tiered_lessons(tier=MemoryTier.MEDIUM, limit=None,
                                   raw=True) == []


# ---------------------------------------------------------------------------
# Evolver mint stamp
# ---------------------------------------------------------------------------

class TestEvolverMintStamp:
    def test_prompt_tweak_mint_carries_producer_stamp(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        captured = {}

        def fake_record(lesson_text, task_type, outcome, source_goal,
                        *, tier, confidence, **kw):
            captured.update(kw, lesson_text=lesson_text)
            return SimpleNamespace(lesson_id="ok")

        monkeypatch.setattr("evolver_store.record_tiered_lesson", fake_record)
        from evolver_store import _apply_suggestion_action
        _apply_suggestion_action({
            "category": "prompt_tweak",
            "target": "research",
            "suggestion": "Prefer primary sources when both are cited",
            "suggestion_id": "sug-01",
            "confidence": 0.8,
        })
        assert captured["minted_by"] == "evolver"
        # NOT provisional — evolver suggestions have their own behavioral
        # verify lifecycle and the category exists to be injected.
        assert "provisional" not in captured


# ---------------------------------------------------------------------------
# confirm_lesson_by_delta
# ---------------------------------------------------------------------------

def _seed_provisional(minted_by="thinkback"):
    return record_tiered_lesson(
        TRACE_LESSON, "general", "done", source_goal="g",
        tier=MemoryTier.MEDIUM, provisional=True, minted_by=minted_by)


class TestConfirmByDelta:
    def test_qualifying_delta_clears_provisional_stays_medium(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is True
        row = _raw(tl.lesson_id)
        assert row is not None                      # stayed MEDIUM
        assert row.provisional is False
        assert row.delta_evidence["route"] == "effect-confirm"
        assert row.delta_evidence["delta"] == 0.59
        longs = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
        assert all(l.lesson_id != tl.lesson_id for l in longs)
        # now injectable
        assert TRACE_LESSON in inject_tiered_lessons("general")

    def test_event_carries_producer(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE)
        events = _events("LESSON_DELTA_CONFIRMED")
        assert len(events) == 1
        assert events[0]["context"]["minted_by"] == "thinkback"

    def test_non_provisional_row_refused(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson(TRACE_LESSON, "general", "done",
                                  source_goal="g")
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is False

    def test_contested_provisional_refused(self, monkeypatch, tmp_path):
        """Contested has its own designed exit (refight_lesson) — a Δ must
        not shortcut it."""
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        contest_lesson(tl.lesson_id, "wrong", source="operator:test")
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is False
        assert _raw(tl.lesson_id).provisional is True

    def test_quarantined_provisional_refused(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson(TRACE_LESSON, "general", "done",
                                  source_goal="g", tier=MemoryTier.MEDIUM,
                                  provisional=True, minted_from="prompt")
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is False

    def test_same_bars_as_promotion(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        nan = float("nan")
        for ev in (
            dict(GOOD_EVIDENCE, delta=0.1),                 # below bar
            dict(GOOD_EVIDENCE, delta=nan),                 # non-finite
            dict(GOOD_EVIDENCE, jackknife_spread=0.7),      # dominated
            dict(GOOD_EVIDENCE, n_calls=3),                 # too few calls
            dict(GOOD_EVIDENCE, stratum="rule"),            # wrong stratum
            dict(GOOD_EVIDENCE, replay_errors=2),           # errored run
        ):
            assert confirm_lesson_by_delta(tl.lesson_id, ev) is False
        assert _raw(tl.lesson_id).provisional is True

    def test_killswitch_off_refuses(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        import knowledge_web as kw
        monkeypatch.setattr(kw, "effect_promotion_enabled", lambda: False)
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is False


# ---------------------------------------------------------------------------
# --origin selection + acting in run_effect_route
# ---------------------------------------------------------------------------

class _ScriptedAdapter:
    """Answers 'extend' when the lesson is in the prompt, 'execute' when
    not — maximally lesson-sensitive (mirrors test_delta_replay)."""

    def __init__(self, lesson: str):
        self.lesson = lesson

    def complete(self, messages, **kw):
        prompt = " ".join(m.content for m in messages)
        move = "extend" if lesson_in_prompt(prompt, self.lesson) else "execute"
        return SimpleNamespace(content=json.dumps({"move": move}))


def _write_call(calls_dir: Path, seq: int, response: str):
    calls_dir.mkdir(parents=True, exist_ok=True)
    (calls_dir / f"call-{seq:05d}.json").write_text(json.dumps({
        "seq": seq, "purpose": "navigator decision",
        "prompt": f"prompt {seq}", "response": response,
        "model": "claude-haiku-4-5-20251001",
    }))


def _seed_oracle_corpus(n=6):
    """n judged-True navigator calls recording 'extend' — the ScriptedAdapter
    then measures Δ=+1.0 spread 0 for any lesson it is sensitive to."""
    import runs
    rd = runs.create_run_dir("horigin", prompt="census", lane="agenda")
    for i in range(1, n + 1):
        _write_call(rd / "build" / "calls", i, '{"move": "extend"}')
    (rd / "run_card.json").write_text(json.dumps({"goal_achieved": True}))


class TestOriginRoute:
    def test_origin_selects_only_stamped_rows(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        trace = _seed_provisional()
        record_tiered_lesson("the plain funnel lesson about paths",
                             "general", "done", source_goal="g")
        _seed_oracle_corpus()
        from delta_replay import run_effect_route
        out = run_effect_route(_ScriptedAdapter(trace.lesson), samples=1,
                               origin="thinkback")
        assert [r["lesson_id"] for r in out["census"]] == [trace.lesson_id]
        assert out["census"][0]["minted_by"] == "thinkback"
        assert out["census"][0]["provisional"] is True
        # census-only: no state moved
        assert _raw(trace.lesson_id).provisional is True

    def test_acting_run_confirms_provisional_instead_of_promoting(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        trace = _seed_provisional()
        _seed_oracle_corpus()
        from delta_replay import run_effect_route
        out = run_effect_route(_ScriptedAdapter(trace.lesson), samples=1,
                               origin="thinkback", promote=True)
        row = next(r for r in out["census"]
                   if r["lesson_id"] == trace.lesson_id)
        assert row["delta"] == 1.0
        assert row["confirmed_by_effect"] is True
        assert row["promoted_by_effect"] is False
        stored = _raw(trace.lesson_id)
        assert stored is not None and stored.provisional is False
        assert stored.delta_evidence["route"] == "effect-confirm"
        longs = load_tiered_lessons(tier=MemoryTier.LONG, min_score=0.0)
        assert all(l.lesson_id != trace.lesson_id for l in longs)

    def test_origin_promote_still_promotes_full_citizens(self, monkeypatch, tmp_path):
        """Regression: the confirm branch must not swallow the existing
        promote route for non-provisional origin-stamped rows."""
        _setup(monkeypatch, tmp_path)
        tl = record_tiered_lesson(TRACE_LESSON, "general", "done",
                                  source_goal="g", minted_by="evolver")
        _seed_oracle_corpus()
        from delta_replay import run_effect_route
        out = run_effect_route(_ScriptedAdapter(tl.lesson), samples=1,
                               origin="evolver", promote=True)
        row = next(r for r in out["census"] if r["lesson_id"] == tl.lesson_id)
        assert row["promoted_by_effect"] is True
        assert row["confirmed_by_effect"] is False

    def test_checkpoint_roundtrip_of_minted_by(self, monkeypatch, tmp_path):
        """Old rows deserialize minted_by="" — and stamped rows keep it
        through a store rewrite."""
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        from knowledge_web import _mutate_tiered_lessons
        _mutate_tiered_lessons(MemoryTier.MEDIUM, lambda ls: ls)  # rewrite
        assert _raw(tl.lesson_id).minted_by == "thinkback"
        # legacy row without the key
        path = _memory_dir() / "medium" / "lessons.jsonl"
        row = json.loads(path.read_text().splitlines()[0])
        row.pop("minted_by")
        row["lesson_id"] = "legacy1"
        row["lesson"] = "a different legacy lesson about queues"
        with path.open("a") as f:
            f.write(json.dumps(row) + "\n")
        assert _raw("legacy1").minted_by == ""

# ---------------------------------------------------------------------------
# 2026-08-09 adversarial-review fix layer
# ---------------------------------------------------------------------------

class TestConfirmTextBinding:
    def test_confirmation_bound_to_measured_text(self, monkeypatch, tmp_path):
        """F8: the Δ was measured against a specific wording — a concurrent
        refight-revise (new text, re-entered provisional) must not inherit
        the confirmation."""
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        measured_text = tl.lesson
        def _revise(lessons):
            for l in lessons:
                if l.lesson_id == tl.lesson_id:
                    l.lesson = "Concurrently revised wording."
            return lessons
        _mutate_tiered_lessons(MemoryTier.MEDIUM, _revise)
        assert confirm_lesson_by_delta(
            tl.lesson_id, GOOD_EVIDENCE,
            expected_lesson=measured_text) is False
        assert _raw(tl.lesson_id).provisional is True

    def test_matching_text_confirms(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path)
        tl = _seed_provisional()
        assert confirm_lesson_by_delta(
            tl.lesson_id, GOOD_EVIDENCE, expected_lesson=tl.lesson) is True
        assert _raw(tl.lesson_id).provisional is False


class TestKillswitchNormalization:
    def test_quoted_false_kills_both_effect_switches(self, monkeypatch, tmp_path):
        """F6: config.get returns raw YAML nodes — a quoted "false" is a
        truthy string; normalize like _novelty_term_enabled (chunk-5a F1
        rule) or the killswitch can't kill."""
        _setup(monkeypatch, tmp_path)
        import config as config_mod
        from knowledge_web import (effect_demotion_enabled,
                                   effect_promotion_enabled)
        real_get = config_mod.get

        def fake_get(key, default=None):
            if key in ("knowledge.effect_promotion_enabled",
                       "knowledge.effect_demotion_enabled"):
                return "false"
            return real_get(key, default)

        monkeypatch.setattr(config_mod, "get", fake_get)
        assert effect_promotion_enabled() is False
        assert effect_demotion_enabled() is False
        tl = _seed_provisional()
        assert confirm_lesson_by_delta(tl.lesson_id, GOOD_EVIDENCE) is False
