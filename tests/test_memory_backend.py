"""Tests for memory_backends.py — Phase 40.

Covers JSONLBackend (11+ tests), SQLiteBackend, migration, and get_backend factory.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from memory_backends import JSONLBackend, SQLiteBackend, get_backend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def jb(tmpdir):
    return JSONLBackend(tmpdir / "memory")


@pytest.fixture
def sb(tmpdir):
    return SQLiteBackend(tmpdir / "memory" / "memory.db")


# ===========================================================================
# JSONLBackend — CRUD
# ===========================================================================

class TestJSONLBackendCRUD:
    def test_append_and_read_all(self, jb):
        jb.append("outcomes", {"id": 1, "status": "ok"})
        jb.append("outcomes", {"id": 2, "status": "fail"})
        records = jb.read_all("outcomes")
        assert len(records) == 2
        assert records[0]["id"] == 1
        assert records[1]["id"] == 2

    def test_read_all_empty_collection(self, jb):
        assert jb.read_all("nonexistent") == []

    def test_rewrite_replaces_all(self, jb):
        jb.append("outcomes", {"id": 1})
        jb.append("outcomes", {"id": 2})
        jb.rewrite("outcomes", [{"id": 99}])
        assert jb.read_all("outcomes") == [{"id": 99}]

    def test_rewrite_empty_clears_collection(self, jb):
        jb.append("outcomes", {"id": 1})
        jb.rewrite("outcomes", [])
        assert jb.read_all("outcomes") == []

    def test_append_text_and_read_text(self, jb):
        jb.append_text("daily/2026-04-01", "line one\n")
        jb.append_text("daily/2026-04-01", "line two\n")
        text = jb.read_text("daily/2026-04-01")
        assert "line one" in text
        assert "line two" in text

    def test_read_text_missing_returns_empty(self, jb):
        assert jb.read_text("daily/9999-99-99") == ""

    def test_count_and_exists(self, jb):
        assert jb.count("outcomes") == 0
        assert not jb.exists("outcomes")
        jb.append("outcomes", {"x": 1})
        assert jb.count("outcomes") == 1
        assert jb.exists("outcomes")

    def test_iter_all_yields_records(self, jb):
        jb.append("lessons", {"lesson": "a"})
        jb.append("lessons", {"lesson": "b"})
        items = list(jb.iter_all("lessons"))
        assert len(items) == 2

    def test_slash_collection_path(self, jb):
        """tiered/agenda maps to memory/tiered/agenda/lessons.jsonl."""
        jb.append("tiered/agenda", {"tier": "agenda", "text": "test"})
        records = jb.read_all("tiered/agenda")
        assert records == [{"tier": "agenda", "text": "test"}]

    def test_rewrite_is_atomic_via_tmp(self, jb):
        """rewrite uses a .jsonl.tmp file then renames — ensure no tmp leftover."""
        jb.append("outcomes", {"id": 1})
        jb.rewrite("outcomes", [{"id": 2}])
        path = jb._path("outcomes")
        tmp = path.with_suffix(".jsonl.tmp")
        assert not tmp.exists()
        assert jb.read_all("outcomes") == [{"id": 2}]

    def test_read_all_skips_corrupt_lines(self, jb):
        """Lines with invalid JSON are silently skipped."""
        path = jb._path("outcomes")
        path.write_text('{"id": 1}\nNOT_JSON\n{"id": 2}\n', encoding="utf-8")
        records = jb.read_all("outcomes")
        assert len(records) == 2
        assert records[0]["id"] == 1
        assert records[1]["id"] == 2

    def test_read_all_skips_blank_lines(self, jb):
        path = jb._path("lessons")
        path.write_text('{"a": 1}\n\n\n{"b": 2}\n', encoding="utf-8")
        records = jb.read_all("lessons")
        assert len(records) == 2

    def test_append_creates_nested_dirs(self, tmpdir):
        deep = tmpdir / "a" / "b" / "c"
        jb2 = JSONLBackend(deep)
        jb2.append("outcomes", {"x": 1})
        assert jb2.count("outcomes") == 1

    def test_multiple_collections_isolated(self, jb):
        jb.append("outcomes", {"src": "outcomes"})
        jb.append("lessons", {"src": "lessons"})
        assert jb.read_all("outcomes") == [{"src": "outcomes"}]
        assert jb.read_all("lessons") == [{"src": "lessons"}]


# ===========================================================================
# SQLiteBackend — CRUD
# ===========================================================================

class TestSQLiteBackendCRUD:
    def test_append_and_read_all(self, sb):
        sb.append("outcomes", {"id": 1})
        sb.append("outcomes", {"id": 2})
        records = sb.read_all("outcomes")
        assert len(records) == 2
        assert records[0]["id"] == 1

    def test_read_all_empty(self, sb):
        assert sb.read_all("nonexistent") == []

    def test_rewrite_replaces(self, sb):
        sb.append("outcomes", {"id": 1})
        sb.rewrite("outcomes", [{"id": 99}])
        assert sb.read_all("outcomes") == [{"id": 99}]

    def test_rewrite_empty(self, sb):
        sb.append("outcomes", {"id": 1})
        sb.rewrite("outcomes", [])
        assert sb.read_all("outcomes") == []

    def test_append_text_and_read_text(self, sb):
        sb.append_text("daily/2026-04-01", "hello\n")
        sb.append_text("daily/2026-04-01", "world\n")
        text = sb.read_text("daily/2026-04-01")
        assert "hello" in text
        assert "world" in text

    def test_read_text_missing(self, sb):
        assert sb.read_text("daily/9999") == ""

    def test_slash_collection_preserved(self, sb):
        sb.append("tiered/agenda", {"tier": "agenda"})
        records = sb.read_all("tiered/agenda")
        assert records == [{"tier": "agenda"}]

    def test_count_and_exists(self, sb):
        assert sb.count("outcomes") == 0
        assert not sb.exists("outcomes")
        sb.append("outcomes", {"x": 1})
        assert sb.count("outcomes") == 1
        assert sb.exists("outcomes")

    def test_iter_all(self, sb):
        sb.append("lessons", {"n": 1})
        sb.append("lessons", {"n": 2})
        assert list(sb.iter_all("lessons")) == [{"n": 1}, {"n": 2}]

    def test_multiple_collections_isolated(self, sb):
        sb.append("outcomes", {"src": "outcomes"})
        sb.append("lessons", {"src": "lessons"})
        assert sb.read_all("outcomes") == [{"src": "outcomes"}]
        assert sb.read_all("lessons") == [{"src": "lessons"}]


# ===========================================================================
# Migration (write via one backend, read via other)
# ===========================================================================

class TestMigration:
    def test_jsonl_to_sqlite_migration(self, tmpdir):
        """Write records with JSONL, read them with SQLite via manual copy."""
        mem = tmpdir / "memory"
        jb = JSONLBackend(mem)
        jb.append("outcomes", {"id": 1, "status": "ok"})
        jb.append("outcomes", {"id": 2, "status": "ok"})

        # Simulate migration: read from JSONL, write to SQLite
        records = jb.read_all("outcomes")
        sb = SQLiteBackend(mem / "memory.db")
        for r in records:
            sb.append("outcomes", r)

        migrated = sb.read_all("outcomes")
        assert len(migrated) == 2
        assert migrated[0]["id"] == 1
        assert migrated[1]["id"] == 2

    def test_sqlite_to_jsonl_migration(self, tmpdir):
        mem = tmpdir / "memory"
        sb = SQLiteBackend(mem / "memory.db")
        sb.append("lessons", {"lesson": "alpha"})
        sb.append("lessons", {"lesson": "beta"})

        records = sb.read_all("lessons")
        jb = JSONLBackend(mem)
        jb.rewrite("lessons", records)

        migrated = jb.read_all("lessons")
        assert len(migrated) == 2
        assert migrated[0]["lesson"] == "alpha"


# ===========================================================================
# Factory — get_backend
# ===========================================================================

class TestGetBackend:
    def test_default_returns_jsonl(self, tmpdir, monkeypatch):
        monkeypatch.delenv("MARO_MEMORY_BACKEND", raising=False)
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)

    def test_env_jsonl_returns_jsonl(self, tmpdir, monkeypatch):
        monkeypatch.setenv("MARO_MEMORY_BACKEND", "jsonl")
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)

    def test_env_sqlite_returns_sqlite(self, tmpdir, monkeypatch):
        monkeypatch.setenv("MARO_MEMORY_BACKEND", "sqlite")
        backend = get_backend(tmpdir)
        assert isinstance(backend, SQLiteBackend)

    def test_unknown_env_falls_back_to_jsonl(self, tmpdir, monkeypatch):
        monkeypatch.setenv("MARO_MEMORY_BACKEND", "redis")
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)


class TestTheJSONLBackendSurvivesATornByte:
    """Adversarial r10, and the one site the round found rather than
    re-found: making the scanner lexical put `JSONLBackend.read_all` into
    view for the first time, and it was carrying three of the arc's
    families at once. `read_text(encoding="utf-8")` is a strict whole-file
    decode and `except OSError` does not catch UnicodeDecodeError, so one
    torn byte raised out of every caller; the per-line
    `except json.JSONDecodeError: pass` dropped rows in silence; and
    `rewrite()` — whose own comment names the read->transform->rewrite
    pattern — writes the survivors back, which turns each silent drop into
    a deletion."""

    def test_a_torn_byte_costs_one_record_not_the_whole_collection(self, tmpdir):
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        b.append("lessons", {"n": 2})
        p = b._path("lessons")
        with p.open("ab") as fh:
            fh.write(b'{"n": "\xff"}\n')

        records = b.read_all("lessons")

        assert records == [{"n": 1}, {"n": 2}]

    def test_the_torn_row_is_not_destroyed_by_a_read_then_rewrite(self, tmpdir):
        """The half that is data loss: the drop is right, the silence and
        the write-back are not.

        Adversarial r11 — the round's only unanimous finding, all five
        seats: this test's r10 version never called `rewrite`, so the name
        was a lie and the composition it names really did delete the torn
        row (`read_all` returned a list without it; `rewrite` wrote that
        list back). Now it runs the composition the docstrings advertise.
        Red on reverting rewrite()'s own re-read-and-strand pass."""
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        p = b._path("lessons")
        with p.open("ab") as fh:
            fh.write(b'{"n": "\xff"}\n')

        rows = b.read_all("lessons")
        b.rewrite("lessons", rows)

        after = p.read_bytes()
        assert b'{"n": "\xff"}' in after          # carried verbatim
        assert b.read_all("lessons") == [{"n": 1}]  # records intact

    def test_a_read_only_read_all_still_leaves_the_bytes_alone(self, tmpdir):
        """What the old test actually proved — kept, under an honest name."""
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        p = b._path("lessons")
        with p.open("ab") as fh:
            fh.write(b'{"n": "\xff"}\n')
        before = p.read_bytes()

        b.read_all("lessons")

        assert p.read_bytes() == before

    def test_the_rewrite_itself_announces_the_carried_row(self, tmpdir, caplog):
        """The strand must be loud AT THE DESTRUCTIVE VERB, with the path —
        read_all's warning fires in some other caller's log context."""
        import logging

        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        p = b._path("lessons")
        with p.open("ab") as fh:
            fh.write(b'{"n": "\xff"}\n')
        rows = b.read_all("lessons")

        with caplog.at_level(logging.WARNING):
            b.rewrite("lessons", rows)

        msgs = [r.getMessage() for r in caplog.records if "rewrite" in r.getMessage()]
        assert any("lessons" in m and str(p) in m for m in msgs), msgs

    def test_a_clean_rewrite_round_trips_exactly_and_quietly(self, tmpdir, caplog):
        """Negative control: no strandees invented, no warning cried."""
        import logging

        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        b.append("lessons", {"n": 2})

        with caplog.at_level(logging.WARNING):
            b.rewrite("lessons", b.read_all("lessons"))

        assert b.read_all("lessons") == [{"n": 1}, {"n": 2}]
        assert not caplog.records, [r.getMessage() for r in caplog.records]

    def test_rewrite_refuses_to_mint_nonjson_constants(self, tmpdir):
        """`json.dumps` writes CPython `NaN` by default — a row the strict
        reader then refuses, i.e. the writer minting its own strandee.
        The safe direction is to abort before the store is touched."""
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        p = b._path("lessons")
        before = p.read_bytes()

        with pytest.raises(ValueError):
            b.rewrite("lessons", [{"n": float("nan")}])

        assert p.read_bytes() == before

    def test_the_loss_is_announced_with_the_store_path(self, tmpdir, caplog):
        import logging

        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        with b._path("lessons").open("ab") as fh:
            fh.write(b'{"n": "\xff"}\n')

        with caplog.at_level(logging.WARNING):
            b.read_all("lessons")

        assert any("lessons" in r.getMessage() for r in caplog.records), \
            [r.getMessage() for r in caplog.records]

    def test_a_healthy_collection_reads_clean_and_quiet(self, tmpdir, caplog):
        """Negative control."""
        import logging

        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})

        with caplog.at_level(logging.WARNING):
            assert b.read_all("lessons") == [{"n": 1}]

        assert not caplog.records, [r.getMessage() for r in caplog.records]
