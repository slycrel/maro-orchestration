"""SqliteMemoryStore-specific tests — beyond the shared port contract.

The port contract (test_memory_port.py, parametrized) proves the verbs.
This file proves the adapter's own load-bearing claims: the SQLite index
is a CACHE over the JSONL event log, rebuildable and ghost-proof (the
dev-recall lesson: an index that can't detect a stale source rots
invisibly).
"""

from pathlib import Path

from memory_jsonl import JsonlMemoryStore
from memory_port import MemoryItem
from memory_sqlite import SqliteMemoryStore


def _add(store, content, **kw):
    return store.append(MemoryItem(kind="lesson", content=content, **kw))


class TestIndexIsACache:
    def test_deleted_index_rebuilds_from_log(self, tmp_path):
        s = SqliteMemoryStore(tmp_path / "m")
        a = _add(s, "durable fact one")
        b = _add(s, "durable fact two")
        s.link(a, b, "supports")
        s.invalidate(b, "demoted")
        s._db.close()

        (tmp_path / "m" / "index.db").unlink()
        s2 = SqliteMemoryStore(tmp_path / "m")
        assert s2.get(a).content == "durable fact one"
        assert s2.get(b, include_invalid=True).invalid_reason == "demoted"
        assert s2.stats() == {**s2.stats(), "items": 2, "edges": 1}

    def test_shrunken_log_triggers_rebuild_not_ghost(self, tmp_path):
        """If the log went backwards (restored backup, truncation), the
        index must resync to the log — never serve entries the log no
        longer contains."""
        root = tmp_path / "m"
        s = SqliteMemoryStore(root)
        keep = _add(s, "kept entry")
        _add(s, "entry that will vanish from the log")
        s._db.close()

        log = root / "memory_events.jsonl"
        first_line = log.read_text().splitlines()[0]
        log.write_text(first_line + "\n")

        s2 = SqliteMemoryStore(root)
        assert s2.stats()["items"] == 1
        assert s2.get(keep) is not None
        assert s2.recall("vanish") == []

    def test_log_interchangeable_with_jsonl_adapter(self, tmp_path):
        """Same on-disk truth: adapter-0 and adapter-1 read each other's
        stores (the swappable-backend promise, honored at the file level)."""
        root = tmp_path / "m"
        j = JsonlMemoryStore(root)
        iid = _add(j, "written by the jsonl adapter")

        s = SqliteMemoryStore(root)
        assert s.get(iid).content == "written by the jsonl adapter"
        sid = _add(s, "written by the sqlite adapter")

        j2 = JsonlMemoryStore(root)
        assert j2.get(sid).content == "written by the sqlite adapter"
        assert j2.stats()["items"] == 2


class TestBiTemporal:
    def test_invalidate_stamps_both_time_axes(self, tmp_path):
        s = SqliteMemoryStore(tmp_path / "m")
        iid = _add(s, "was true, then wasn't")
        s.invalidate(iid, "superseded")
        row = s._db.execute(
            "SELECT expired_at, invalid_at FROM items WHERE id=?",
            (iid,)).fetchone()
        assert row[0] and row[1]  # transaction + event time both recorded


class TestFtsHygiene:
    def test_fts_operators_in_query_do_not_raise(self, tmp_path):
        s = SqliteMemoryStore(tmp_path / "m")
        _add(s, "NEAR the fence AND the gate")
        for q in ('AND OR NOT', 'a NEAR/3 b', '"unclosed', 'col:foo*', '(((('):
            assert isinstance(s.recall(q), list)
