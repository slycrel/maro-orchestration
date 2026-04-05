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
        monkeypatch.delenv("POE_MEMORY_BACKEND", raising=False)
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)

    def test_env_jsonl_returns_jsonl(self, tmpdir, monkeypatch):
        monkeypatch.setenv("POE_MEMORY_BACKEND", "jsonl")
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)

    def test_env_sqlite_returns_sqlite(self, tmpdir, monkeypatch):
        monkeypatch.setenv("POE_MEMORY_BACKEND", "sqlite")
        backend = get_backend(tmpdir)
        assert isinstance(backend, SQLiteBackend)

    def test_unknown_env_falls_back_to_jsonl(self, tmpdir, monkeypatch):
        monkeypatch.setenv("POE_MEMORY_BACKEND", "redis")
        backend = get_backend(tmpdir)
        assert isinstance(backend, JSONLBackend)
