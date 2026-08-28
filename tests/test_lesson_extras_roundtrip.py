"""C0.1 — lesson-store RMW raw round-tripping (docs/CONTRACTS.md).

Both lesson stores load rows through a filtered dataclass constructor and
rewrite whole files from the parsed objects. Before the fix, any unknown key
a newer writer legally added (rule A2: additive optional fields) was silently
stripped store-wide by the next reinforcement / promotion / dedup rewrite.

Must-detect: every test here writes a row carrying an unknown field
("future_field") straight into the store, triggers a real rewrite through
the production path, and asserts the field survives on disk. On the pre-fix
code each of these goes red — the filtered constructor drops the key and the
asdict()/_verdict_row() rebuild persists the stripped row.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import knowledge_web as kw
import memory_ledger as ml
from knowledge_web import MemoryTier, TieredLesson


@pytest.fixture(autouse=True)
def _set_memory_dir(tmp_path, monkeypatch):
    mem = tmp_path / "memory"
    mem.mkdir(exist_ok=True)
    monkeypatch.setenv("MARO_MEMORY_DIR", str(mem))


def _tiered_row(lesson_id="tl-1", **overrides):
    row = {
        "lesson_id": lesson_id,
        "task_type": "general",
        "outcome": "done",
        "lesson": "always check the exit code",
        "source_goal": "goal-1",
        "confidence": 0.8,
        "tier": MemoryTier.MEDIUM,
        "score": 1.0,
        "last_reinforced": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "future_field": "x",  # the unknown key a newer writer added
    }
    row.update(overrides)
    return row


def _read_rows(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# Tiered store (knowledge_web)
# ---------------------------------------------------------------------------

class TestTieredStoreRoundTrip:
    def test_reinforce_rewrite_preserves_unknown_field(self):
        path = kw._tiered_lessons_path(MemoryTier.MEDIUM)
        path.write_text(json.dumps(_tiered_row()) + "\n")

        assert kw.reinforce_lesson("tl-1", tier=MemoryTier.MEDIUM) is not None

        rows = _read_rows(path)
        assert len(rows) == 1
        assert rows[0].get("future_field") == "x"
        # The rewrite still did its job.
        assert rows[0]["times_reinforced"] >= 1

    def test_mutate_rewrite_preserves_unknown_field_on_untouched_rows(self):
        """A rewrite triggered by ANY mutation must not strip bystander rows."""
        path = kw._tiered_lessons_path(MemoryTier.MEDIUM)
        path.write_text(
            json.dumps(_tiered_row(lesson_id="tl-a")) + "\n"
            + json.dumps(_tiered_row(lesson_id="tl-b",
                                     lesson="a different lesson entirely",
                                     future_field="y")) + "\n")

        kw.reinforce_lesson("tl-a", tier=MemoryTier.MEDIUM)

        by_id = {r["lesson_id"]: r for r in _read_rows(path)}
        assert by_id["tl-a"].get("future_field") == "x"
        assert by_id["tl-b"].get("future_field") == "y"

    def test_promotion_carries_unknown_field_to_long(self):
        med = kw._tiered_lessons_path(MemoryTier.MEDIUM)
        med.write_text(json.dumps(_tiered_row(
            lesson_id="tl-p",
            score=kw.PROMOTE_MIN_SCORE + 1.0,
            sessions_validated=kw.PROMOTE_MIN_SESSIONS,
        )) + "\n")

        assert kw.promote_lesson("tl-p") is True

        long_rows = _read_rows(kw._tiered_lessons_path(MemoryTier.LONG))
        assert len(long_rows) == 1
        assert long_rows[0]["lesson_id"] == "tl-p"
        assert long_rows[0].get("future_field") == "x"

    def test_declared_fields_win_over_extras_on_collision(self):
        """A hand-built _extras carrying a declared-field name must not
        shadow the typed value at serialization."""
        tl = TieredLesson(**{k: v for k, v in _tiered_row().items()
                             if k in TieredLesson.__dataclass_fields__})
        tl._extras = {"score": 99.0, "future_field": "x"}
        row = kw._tiered_lesson_row(tl)
        assert row["score"] == 1.0
        assert row["future_field"] == "x"


# ---------------------------------------------------------------------------
# Flat store (memory_ledger)
# ---------------------------------------------------------------------------

def _flat_row(lesson_id="fl-1", lesson="always check the exit code",
              **overrides):
    row = {
        "lesson_id": lesson_id,
        "task_type": "general",
        "outcome": "done",
        "lesson": lesson,
        "source_goal": "goal-1",
        "confidence": 0.7,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "future_field": "x",
    }
    row.update(overrides)
    return row


class TestFlatStoreRoundTrip:
    def test_dedup_rewrite_preserves_unknown_field_on_survivor(self):
        path = ml._lessons_path()
        # Survivor carries the unknown key; an exact dup forces a real
        # rewrite (dedup leaves the file untouched when nothing is removed).
        path.write_text(
            json.dumps(_flat_row(lesson_id="fl-1")) + "\n"
            + json.dumps(_flat_row(lesson_id="fl-2", future_field="dup")) + "\n")

        stats = ml.deduplicate_lessons()
        assert stats["removed_exact"] == 1

        rows = _read_rows(path)
        assert len(rows) == 1
        assert rows[0].get("future_field") == "x"

    def test_dedup_rewrite_preserves_unknown_field_on_bystander(self):
        path = ml._lessons_path()
        path.write_text(
            json.dumps(_flat_row(lesson_id="fl-1")) + "\n"
            + json.dumps(_flat_row(lesson_id="fl-2", future_field="dup")) + "\n"
            + json.dumps(_flat_row(lesson_id="fl-3",
                                   lesson="a completely unrelated insight",
                                   future_field="z")) + "\n")

        ml.deduplicate_lessons()

        by_id = {r["lesson_id"]: r for r in _read_rows(path)}
        assert by_id["fl-3"].get("future_field") == "z"

    def test_reinforce_rmw_preserves_unknown_field(self):
        path = ml._lessons_path()
        path.write_text(json.dumps(_flat_row(lesson_id="fl-r")) + "\n")

        def _bump(row):
            row.times_reinforced += 1

        out = ml._reinforce_flat_row("fl-r", _bump)
        assert out is not None

        rows = _read_rows(path)
        assert rows[0].get("future_field") == "x"
        assert rows[0]["times_reinforced"] == 1
