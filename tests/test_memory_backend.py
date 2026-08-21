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


class TestTheBackendWriterCannotOutrunItsReader:
    """Adversarial r12 (four of five seats, probed): `allow_nan=False`
    closed only the constant case. `json.dumps` also serializes a lone
    surrogate as a clean-looking `\\udcXX` escape — so `rewrite()` could
    replace a healthy collection with a row `read_all()` announces and
    skips, and `append()` could mint the same strandee while reporting
    success. Every emission now runs through `prove_record_line` — the
    reader's own door — BEFORE the store is touched."""

    def test_rewrite_refuses_an_escaped_surrogate(self, tmpdir):
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"id": "healthy"})
        p = b._path("lessons")
        before = p.read_bytes()

        with pytest.raises(Exception):
            b.rewrite("lessons", [{"id": "replacement", "note": "\udcff"}])

        assert p.read_bytes() == before, "the store was touched on abort"
        assert b.read_all("lessons") == [{"id": "healthy"}]

    def test_append_refuses_nan_and_surrogates(self, tmpdir):
        b = JSONLBackend(tmpdir)

        with pytest.raises(ValueError):
            b.append("lessons", {"x": float("nan")})
        with pytest.raises(Exception):
            b.append("lessons", {"x": "s\udcff"})

        assert not b._path("lessons").exists(), \
            "a refused append still touched the store"

    def test_a_clean_append_still_lands(self, tmpdir):
        """Negative control."""
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"n": 1})
        assert b.read_all("lessons") == [{"n": 1}]


class TestTheAppendDoorRequiresAnObject:
    """`prove_record_line`'s require_object leg: read_all admits only JSON
    objects, so a writer handing the backend a bare list or `null` is
    minting a row every reader announces-and-skips. Refuse at the door."""

    def test_append_refuses_a_non_object_record(self, tmp_path):
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        with pytest.raises(ValueError):
            jb.append("rows", ["not", "a", "dict"])
        assert not (tmp_path / "mem" / "rows.jsonl").exists(), \
            "the store was touched on abort"


class TestTheRewriteStrandRuleMirrorsTheReadersAdmission:
    """Adversarial r12 (Skeptic + Expert QA, both probed): `read_all`
    announces-and-skips clean non-dict JSON (`null`, arrays, strings), so
    those rows were never in the caller's list either — but r11's re-read
    pass stranded only parse FAILURES, and the composition deleted a
    `null` row it had just been taught not to. The strand rule is the
    reader's admission rule, not the parser's."""

    def test_non_dict_json_rows_ride_the_rewrite(self, tmpdir):
        b = JSONLBackend(tmpdir)
        b.append("lessons", {"keep": 1})
        p = b._path("lessons")
        with p.open("a") as fh:
            fh.write("null\n[1, 2]\n")

        b.rewrite("lessons", b.read_all("lessons"))

        after = p.read_text()
        assert "null" in after, "a null row was deleted by the composition"
        assert "[1, 2]" in after, "an array row was deleted by the composition"
        assert '"keep": 1' in after


class TestTheRewriteKeepsItsAuditHonest:
    """Adversarial r13 (QA + Failure Operator, probed): the carry-through
    warning fired BEFORE the payload was proven, so a refused emission
    left an audit line about a rewrite that never happened; and strandees
    were re-homed to the tail, where a keyed last-row-wins consumer would
    let a later-repaired legacy row outrank the caller's records."""

    def test_strandees_ride_first_not_last(self, tmp_path):
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        f = tmp_path / "mem" / "led.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'BROKEN\n{"id": "old"}\n')
        jb.rewrite("led", jb.read_all("led"))
        assert f.read_bytes() == b'BROKEN\n{"id": "old"}\n'

    def test_a_refused_emission_does_not_announce_a_carry_through(
            self, tmp_path, caplog):
        import logging
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        f = tmp_path / "mem" / "led.jsonl"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'BROKEN\n')
        with caplog.at_level(logging.WARNING):
            with pytest.raises(Exception):
                jb.rewrite("led", [{"note": "bad\udcff"}])
        assert not any("carried through" in r.message for r in caplog.records)
        assert f.read_bytes() == b'BROKEN\n'


class TestTheBackendFailsLoudly:
    """Adversarial r13 (QA, probed): append caught OSError, warned with
    only the collection name, and returned normally — a disk-full became
    apparent success, and no later reader can announce a row that was
    never written."""

    def test_append_propagates_io_failure(self, tmp_path, monkeypatch):
        from memory_backends import JSONLBackend
        import file_lock
        jb = JSONLBackend(tmp_path / "mem")

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(file_lock, "locked_append", boom)
        with pytest.raises(OSError):
            jb.append("led", {"id": "x"})

    def test_rewrite_propagates_io_failure(self, tmp_path, monkeypatch):
        from memory_backends import JSONLBackend
        import file_lock
        jb = JSONLBackend(tmp_path / "mem")
        jb.append("led", {"id": "old"})

        def boom(*a, **k):
            raise OSError("disk full")
        monkeypatch.setattr(file_lock, "atomic_write", boom)
        with pytest.raises(OSError):
            jb.rewrite("led", [{"id": "new"}])


class TestTransformDecidesUnderOneLock:
    """Adversarial r13 (Skeptic, probed): with the bare
    read_all -> transform -> rewrite composition, a record appended
    between the read and the write is CLEAN, so the strandee pass cannot
    distinguish "the caller dropped it" from "the caller never saw it" —
    and the concurrent append was silently deleted. transform() reads and
    writes under one lock, making the decision decidable."""

    def test_transform_reads_modifies_writes(self, tmp_path):
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        jb.append("led", {"id": "old"})
        out = jb.transform("led", lambda rows: rows + [{"id": "new"}])
        assert [r["id"] for r in out] == ["old", "new"]
        assert [r["id"] for r in jb.read_all("led")] == ["old", "new"]

    def test_transform_runs_fn_under_the_store_lock(
            self, tmp_path, monkeypatch):
        """The lock IS the fix — pin that fn executes inside it, so the
        no-lock revert (the racy bare composition) cannot come back."""
        from contextlib import contextmanager
        import file_lock
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        jb.append("led", {"id": "old"})
        state = {"held": 0}
        real = file_lock.locked_write

        @contextmanager
        def wrap(path, *a, **k):
            state["held"] += 1
            try:
                with real(path, *a, **k):
                    yield
            finally:
                state["held"] -= 1
        monkeypatch.setattr(file_lock, "locked_write", wrap)

        def fn(rows):
            assert state["held"] > 0, "fn ran outside the store lock"
            return rows
        jb.transform("led", fn)


class TestTheSQLiteTwinKeepsTheSameDoctrine:
    """Adversarial r13 (Minimalist, HIGH, probed): the selectable SQLite
    backend was the destructive config twin — read_all silently dropped a
    damaged row (`except JSONDecodeError: pass`) and rewrite's DELETE-all
    then destroyed it for good, one MARO_MEMORY_BACKEND flip from live."""

    def _seed_bad_row(self, db_path):
        import sqlite3
        con = sqlite3.connect(str(db_path))
        con.execute(
            "INSERT INTO memory_records (collection, data) VALUES (?, ?)",
            ("lessons", "{bad json"))
        con.commit()
        con.close()

    def test_the_read_then_rewrite_composition_preserves_the_bad_row(
            self, tmp_path, caplog):
        import logging
        import sqlite3
        from memory_backends import SQLiteBackend
        sb = SQLiteBackend(tmp_path / "m.db")
        sb.append("lessons", {"id": "healthy"})
        self._seed_bad_row(tmp_path / "m.db")

        with caplog.at_level(logging.WARNING):
            rows = sb.read_all("lessons")
        # Pin the READ's own announcement, not the rewrite's — asserting a
        # substring both guards emit let the read-side silent drop hide
        # behind the rewrite's carry-through (r13 sweep survivor: the
        # guard-in-front-of-a-guard hole, third appearance in this arc).
        assert rows == [{"id": "healthy"}]
        assert any("excluded from the result" in r.message
                   for r in caplog.records)
        sb.rewrite("lessons", rows)
        con = sqlite3.connect(str(tmp_path / "m.db"))
        datas = [r[0] for r in con.execute(
            "SELECT data FROM memory_records WHERE collection='lessons'")]
        con.close()
        assert "{bad json" in datas, "the damaged row was destroyed"
        assert any('"id": "healthy"' in d for d in datas)

    def test_the_sqlite_writer_cannot_outrun_its_reader(self, tmp_path):
        from memory_backends import SQLiteBackend
        sb = SQLiteBackend(tmp_path / "m.db")
        with pytest.raises(Exception):
            sb.append("lessons", {"note": "bad\udcff"})
        with pytest.raises(Exception):
            sb.rewrite("lessons", [{"v": float("nan")}])
        assert sb.read_all("lessons") == []


# ---------------------------------------------------------------------------
# Adversarial r14
# ---------------------------------------------------------------------------


class TestTransformIsTheContractNotANicety:
    """Adversarial r14 (five seats, probed): r13 added transform() to
    JSONLBackend only, so the selectable SQLite twin kept the exact
    lost-update race the method exists to close — a clean append between
    a caller's read_all() and rewrite() was reader-vouched by rewrite's
    own re-select, deleted, and never reinserted."""

    def test_transform_is_abstract_on_the_interface(self):
        import abc
        from memory_backends import MemoryBackend
        assert "transform" in MemoryBackend.__abstractmethods__

    def test_sqlite_transform_reads_modifies_writes(self, tmp_path):
        from memory_backends import SQLiteBackend
        b = SQLiteBackend(tmp_path / "m.db")
        b.append("led", {"id": "old", "n": 1})
        out = b.transform(
            "led", lambda rows: [dict(r, n=r["n"] + 1) for r in rows])
        assert out == [{"id": "old", "n": 2}]
        assert b.read_all("led") == [{"id": "old", "n": 2}]

    def test_sqlite_transform_carries_unreadable_rows(self, tmp_path):
        import sqlite3
        from memory_backends import SQLiteBackend
        db = tmp_path / "m.db"
        b = SQLiteBackend(db)
        b.append("led", {"id": "old"})
        with sqlite3.connect(str(db)) as con:
            con.execute(
                "INSERT INTO memory_records (collection, data) "
                "VALUES (?, ?)", ("led", "BROKEN"))
            con.commit()
        b.transform("led", lambda rows: rows)
        with sqlite3.connect(str(db)) as con:
            datas = [r[0] for r in con.execute(
                "SELECT data FROM memory_records").fetchall()]
        assert "BROKEN" in datas

    def test_sqlite_transform_blocks_a_concurrent_append(self, tmp_path):
        """The append attempted while fn deliberates lands AFTER the
        commit — never inside the transaction's replace window."""
        import sqlite3
        import threading
        import time
        from memory_backends import SQLiteBackend
        db = tmp_path / "m.db"
        b = SQLiteBackend(db)
        b.append("led", {"id": "old"})
        landed_during_fn = []

        def fn(rows):
            t = threading.Thread(
                target=lambda: SQLiteBackend(db).append(
                    "led", {"id": "concurrent"}))
            t.start()
            time.sleep(0.4)
            with sqlite3.connect(str(db)) as con:
                landed_during_fn.extend(
                    r[0] for r in con.execute(
                        "SELECT data FROM memory_records").fetchall()
                    if "concurrent" in r[0])
            fn.t = t
            return rows + [{"id": "from-fn"}]

        b.transform("led", fn)
        fn.t.join(15)
        assert not landed_during_fn, "append landed inside the transaction"
        ids = {r["id"] for r in b.read_all("led")}
        assert ids == {"old", "from-fn", "concurrent"}

    def test_sqlite_transform_rolls_back_when_fn_raises(self, tmp_path):
        import pytest
        from memory_backends import SQLiteBackend
        b = SQLiteBackend(tmp_path / "m.db")
        b.append("led", {"id": "old"})

        def fn(rows):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            b.transform("led", fn)
        assert b.read_all("led") == [{"id": "old"}]

    def test_sqlite_transform_refuses_an_unprovable_emission(self, tmp_path):
        import pytest
        from memory_backends import SQLiteBackend
        b = SQLiteBackend(tmp_path / "m.db")
        b.append("led", {"id": "old"})
        with pytest.raises((TypeError, ValueError)):
            b.transform("led", lambda rows: rows + [{"bad": float("nan")}])
        assert b.read_all("led") == [{"id": "old"}]


class TestAFailedReadIsNotAnEmptyStore:
    """Adversarial r14 (two seats, probed): SQLite read_all() converted
    every sqlite3.Error into [], indistinguishable from verified-empty —
    a transient lock during a repair's read phase handed the caller a
    valid-looking empty list and the next rewrite deleted every healthy
    row."""

    def test_read_all_raises_on_sqlite_error(self, tmp_path):
        import pytest
        import sqlite3
        from memory_backends import SQLiteBackend
        b = SQLiteBackend(tmp_path / "m.db")
        b.append("led", {"id": "healthy"})

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")

        b._connect = Boom
        with pytest.raises(sqlite3.Error):
            b.read_all("led")

    def test_the_failed_read_cannot_feed_a_rewrite(self, tmp_path):
        """The r14 composition: read fails transiently, connection
        recovers, caller would have rewritten []. With the raise the
        caller never gets a list; after recovery the store is intact."""
        import sqlite3
        from memory_backends import SQLiteBackend
        b = SQLiteBackend(tmp_path / "m.db")
        b.append("led", {"id": "healthy"})
        real_connect = b._connect
        calls = {"n": 0}

        class Boom:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, *a):
                raise sqlite3.OperationalError("database is locked")

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                return Boom()
            return real_connect()

        b._connect = flaky
        try:
            rows = b.read_all("led")
        except sqlite3.Error:
            rows = None
        assert rows is None
        assert b.read_all("led") == [{"id": "healthy"}]


class TestTransformRefusesToRunUnlocked:
    """Adversarial r14 (four seats, probed): transform() passed no
    require flag, so MARO_FILELOCK_FAIL_OPEN=1 (or an uncreatable lock
    file) let the "one lock" transaction run unlocked — the lost-update
    race wearing the fix's clothes."""

    def test_contended_fail_open_raises_before_fn(
            self, tmp_path, monkeypatch):
        import fcntl
        import pytest
        from file_lock import FileLockTimeout
        from memory_backends import JSONLBackend
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        jb = JSONLBackend(tmp_path / "mem")
        jb.append("led", {"id": "x"})
        path = jb._path("led")
        before = path.read_bytes()
        holder = open(str(path) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            ran = {"fn": False}
            with pytest.raises(FileLockTimeout):
                jb.transform(
                    "led",
                    lambda rows: (ran.__setitem__("fn", True), rows)[1])
            assert not ran["fn"], "fn ran without the lock"
            assert path.read_bytes() == before
        finally:
            holder.close()

    def test_transform_passes_require(self):
        """Cheap structural pin: the keyword is the fix."""
        import ast
        import inspect
        from memory_backends import JSONLBackend
        src = inspect.getsource(JSONLBackend.transform)
        call = [n for n in ast.walk(ast.parse(src.lstrip()))
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "locked_write"]
        assert call, "transform no longer calls locked_write directly"
        kw = {k.arg: getattr(k.value, "value", None)
              for k in call[0].keywords}
        assert kw.get("require") is True


class TestVerbatimMeansThePayload:
    """Adversarial r14 (two seats, probed) — judged accept-and-pin: a
    torn FINAL row that lost its LF terminator gains one on rewrite.
    That LF is required row framing once strandees ride first; the
    payload bytes are unchanged and the round-trip is stable. This pin
    documents the accepted shape so a future round doesn't re-litigate
    it — and catches any drift into ACTUAL payload mutation."""

    def test_unterminated_final_strandee_round_trips(self, tmp_path):
        from memory_backends import JSONLBackend
        jb = JSONLBackend(tmp_path / "mem")
        path = jb._path("led")
        path.write_bytes(b'{"id":"good"}\n\xff')   # torn final row, no LF
        jb.rewrite("led", [{"id": "good"}])
        assert path.read_bytes() == b'\xff\n{"id": "good"}\n'
        jb.rewrite("led", [{"id": "good"}])        # idempotent
        assert path.read_bytes() == b'\xff\n{"id": "good"}\n'


class TestACommittedTransformTellsTheTruth:
    """Adversarial r15 (Skeptic, probed): a sqlite3.Error raised AFTER
    con.commit() — a close() failure — fell into the outer handler,
    which logged "transform NOT performed, store unchanged" about a
    store that HOLDS the transform. That message invites a retry, and a
    non-idempotent transform would then apply twice. The handler now
    branches on a committed flag and says what actually happened."""

    def test_a_close_failure_after_commit_is_honest(
            self, tmp_path, caplog):
        import logging
        import sqlite3
        import pytest
        from memory_backends import SQLiteBackend
        be = SQLiteBackend(tmp_path / "mem.db")
        be.append("led", {"k": 1})

        class CloseBomb:
            def __init__(self, con):
                self._con = con

            def __getattr__(self, name):
                return getattr(self._con, name)

            def close(self):
                raise sqlite3.OperationalError("simulated close failure")

        real_connect = be._connect
        be._connect = lambda: CloseBomb(real_connect())
        try:
            with caplog.at_level(
                    logging.ERROR, logger="maro.memory_backends"):
                with pytest.raises(sqlite3.Error):
                    be.transform(
                        "led",
                        lambda rows: rows + [{"k": 2}])
        finally:
            be._connect = real_connect
        assert "COMMITTED" in caplog.text
        assert "do not retry" in caplog.text
        assert "store unchanged" not in caplog.text
        assert be.read_all("led") == [{"k": 1}, {"k": 2}]
