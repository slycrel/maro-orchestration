"""Tests for correspondence.py — dev-facing retrieval over project docs/memory.

Deterministic embeddings via monkeypatched fake so tests don't touch the network.
sqlite-vec is a hard dependency for the live paths; tests that exercise the db
skip gracefully if the extension isn't installed on this box.
"""

from __future__ import annotations

import pytest
from pathlib import Path

import correspondence as corr


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

class TestChunking:
    def test_splits_on_h1_and_h2(self):
        text = (
            "# Top\nintro paragraph\n\n"
            "## First\nalpha beta gamma\n\n"
            "## Second\ndelta epsilon\n"
        )
        chunks = corr.chunk_markdown(text, "/fake/doc.md", 1000)
        assert len(chunks) == 3
        titles = [c.section for c in chunks]
        assert titles[0] == "Top"
        assert "First" in titles[1]
        assert "Second" in titles[2]

    def test_heading_chain_preserved(self):
        text = (
            "# Top\n\n"
            "## Middle\n\n"
            "### Leaf\npayload\n"
        )
        chunks = corr.chunk_markdown(text, "/fake/doc.md", 1000)
        leaf = [c for c in chunks if "payload" in c.content][0]
        assert "Top" in leaf.section
        assert "Middle" in leaf.section
        assert "Leaf" in leaf.section
        assert ">" in leaf.section

    def test_oversize_section_splits(self):
        big = "# Top\n\n" + "\n\n".join(["para " + "x" * 1000 for _ in range(10)])
        chunks = corr.chunk_markdown(big, "/fake/doc.md", 1000, max_chars=3000)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.content) <= 3000

    def test_empty_input_returns_empty(self):
        assert corr.chunk_markdown("", "/fake/doc.md", 0) == []
        assert corr.chunk_markdown("   \n  \n", "/fake/doc.md", 0) == []

    def test_content_hash_stable(self):
        text = "# A\npayload\n"
        c1 = corr.chunk_markdown(text, "/p.md", 1)[0]
        c2 = corr.chunk_markdown(text, "/p.md", 1)[0]
        assert c1.content_hash == c2.content_hash

    def test_content_hash_differs_on_source(self):
        text = "# A\npayload\n"
        c1 = corr.chunk_markdown(text, "/p1.md", 1)[0]
        c2 = corr.chunk_markdown(text, "/p2.md", 1)[0]
        assert c1.content_hash != c2.content_hash


# ---------------------------------------------------------------------------
# Ingest + query (requires sqlite-vec)
# ---------------------------------------------------------------------------

try:
    import sqlite_vec  # noqa: F401
    _HAS_SQLITE_VEC = True
except ImportError:
    _HAS_SQLITE_VEC = False

_sqlite_vec_required = pytest.mark.skipif(
    not _HAS_SQLITE_VEC,
    reason="sqlite-vec not installed (pip install sqlite-vec)",
)


def _deterministic_embed(dim: int):
    """Return an embed_fn that hashes text → a stable unit vector in R^dim.

    Same text ⇒ same vector; similar text shares leading chars ⇒ similar vectors
    (crude but enough for roundtrip tests).
    """
    import hashlib
    import struct

    def _fn(texts):
        out = []
        for t in texts:
            vec = [0.0] * dim
            # First dim/4 floats: derived from word-bucket counts
            for word in t.lower().split():
                h = int(hashlib.md5(word.encode()).hexdigest(), 16)
                idx = h % dim
                vec[idx] += 1.0
            # Normalize to unit length
            norm = sum(x * x for x in vec) ** 0.5
            if norm > 0:
                vec = [x / norm for x in vec]
            out.append(vec)
        return out

    return _fn


@pytest.fixture
def corr_cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "corr.db"),
        "sources": [],
        "embed_model": "fake",
        "embed_dim": 64,  # small dim for test speed
        "max_chunk_chars": 6000,
        "top_k": 5,
    }


@_sqlite_vec_required
class TestIngestQuery:
    def test_roundtrip_returns_relevant_chunk(self, corr_cfg, tmp_path):
        src = tmp_path / "docs" / "notes.md"
        src.parent.mkdir(parents=True)
        src.write_text(
            "# Scope\ninversion and failure modes\n\n"
            "# Closure\nverification checks run mechanically\n\n"
            "# Taste\njudgment rather than checklist matching\n",
            encoding="utf-8",
        )
        corr_cfg["sources"] = [str(tmp_path / "docs")]
        embed = _deterministic_embed(corr_cfg["embed_dim"])

        stats = corr.ingest(cfg=corr_cfg, embed_fn=embed)
        assert stats.chunks_new == 3
        assert not stats.errors

        hits = corr.query("taste and judgment", cfg=corr_cfg, embed_fn=embed)
        assert hits, "expected at least one hit"
        # The 'Taste' section should rank highest
        top = hits[0]
        assert "Taste" in top.section or "taste" in top.content.lower()

    def test_ingest_is_idempotent(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\nbody text\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]
        embed = _deterministic_embed(corr_cfg["embed_dim"])

        s1 = corr.ingest(cfg=corr_cfg, embed_fn=embed)
        s2 = corr.ingest(cfg=corr_cfg, embed_fn=embed)
        assert s1.chunks_new >= 1
        assert s2.chunks_new == 0
        assert s2.chunks_existing >= 1

    def test_ingest_updates_on_changed_content(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\noriginal body\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]
        embed = _deterministic_embed(corr_cfg["embed_dim"])

        corr.ingest(cfg=corr_cfg, embed_fn=embed)
        src.write_text("# H\nwholly different content\n", encoding="utf-8")
        s2 = corr.ingest(cfg=corr_cfg, embed_fn=embed)
        assert s2.chunks_new >= 1  # new hash ⇒ new row

    def test_since_filter_skips_old_files(self, corr_cfg, tmp_path):
        import os
        src = tmp_path / "old.md"
        src.write_text("# Old\ncontent\n", encoding="utf-8")
        # set mtime to 2 days ago
        past = 1_700_000_000
        os.utime(src, (past, past))
        corr_cfg["sources"] = [str(tmp_path)]
        embed = _deterministic_embed(corr_cfg["embed_dim"])

        stats = corr.ingest(cfg=corr_cfg, embed_fn=embed, since_seconds=60)
        assert stats.files_skipped_stale == 1
        assert stats.chunks_new == 0

    def test_status_reports_counts(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\nbody\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]
        embed = _deterministic_embed(corr_cfg["embed_dim"])
        corr.ingest(cfg=corr_cfg, embed_fn=embed)

        s = corr.status(cfg=corr_cfg)
        assert "error" not in s
        assert s["total_chunks"] >= 1
        assert s["last_ingest_utc"] is not None


# ---------------------------------------------------------------------------
# Graceful-failure paths (no sqlite-vec installed, no API key)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# JSONL session transcript ingestion
# ---------------------------------------------------------------------------

import json


def _write_jsonl(path: Path, records: list) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class TestJsonlExtraction:
    def test_user_text_string(self):
        assert corr._extract_user_text("hello world") == "hello world"

    def test_user_text_scaffold_filtered(self):
        assert corr._extract_user_text("<local-command-caveat>stuff</local-command-caveat>") == ""
        assert corr._extract_user_text("<command-name>/foo</command-name>") == ""

    def test_user_text_list_blocks(self):
        content = [
            {"type": "text", "text": "first message"},
            {"type": "tool_result", "tool_use_id": "x", "content": "should be skipped"},
            {"type": "text", "text": "second message"},
        ]
        out = corr._extract_user_text(content)
        assert "first message" in out
        assert "second message" in out
        assert "should be skipped" not in out

    def test_user_text_list_skips_scaffold_blocks(self):
        content = [
            {"type": "text", "text": "<local-command-caveat>nope</local-command-caveat>"},
            {"type": "text", "text": "real prose"},
        ]
        assert corr._extract_user_text(content) == "real prose"

    def test_assistant_text_skips_thinking_and_tool_use(self):
        content = [
            {"type": "thinking", "thinking": "should not appear"},
            {"type": "text", "text": "visible reply"},
            {"type": "tool_use", "name": "Read", "input": {}},
            {"type": "text", "text": "second reply"},
        ]
        out = corr._extract_assistant_text(content)
        assert "visible reply" in out
        assert "second reply" in out
        assert "should not appear" not in out


class TestTurnChunks:
    def test_single_turn_yields_one_chunk(self, tmp_path):
        records = [
            {"type": "user", "timestamp": "2026-04-16T10:00:00Z",
             "message": {"content": "why did we rename constraint to scope"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:00:01Z",
             "message": {"content": [{"type": "text", "text": "because scope is narrower"}]}},
        ]
        path = tmp_path / "session.jsonl"
        _write_jsonl(path, records)
        chunks = list(corr._iter_turn_chunks(path, max_chars=6000))
        assert len(chunks) == 1
        c = chunks[0]
        assert "USER:" in c.content
        assert "ASSISTANT:" in c.content
        assert "rename constraint" in c.content
        assert "scope is narrower" in c.content
        assert "turn 1" in c.section
        assert "2026-04-16" in c.section

    def test_multi_turn_yields_multiple_chunks(self, tmp_path):
        records = [
            {"type": "user", "timestamp": "2026-04-16T10:00:00Z",
             "message": {"content": "first question"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:00:01Z",
             "message": {"content": [{"type": "text", "text": "first answer"}]}},
            {"type": "user", "timestamp": "2026-04-16T10:01:00Z",
             "message": {"content": "second question"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:01:01Z",
             "message": {"content": [{"type": "text", "text": "second answer"}]}},
        ]
        path = tmp_path / "session.jsonl"
        _write_jsonl(path, records)
        chunks = list(corr._iter_turn_chunks(path, max_chars=6000))
        assert len(chunks) == 2
        assert "first question" in chunks[0].content
        assert "second question" in chunks[1].content

    def test_scaffold_user_message_skipped(self, tmp_path):
        records = [
            {"type": "user", "timestamp": "2026-04-16T10:00:00Z",
             "message": {"content": "<local-command-caveat>noise</local-command-caveat>"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:00:01Z",
             "message": {"content": [{"type": "text", "text": "a reply"}]}},
            {"type": "user", "timestamp": "2026-04-16T10:01:00Z",
             "message": {"content": "real question"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:01:01Z",
             "message": {"content": [{"type": "text", "text": "real answer"}]}},
        ]
        path = tmp_path / "session.jsonl"
        _write_jsonl(path, records)
        chunks = list(corr._iter_turn_chunks(path, max_chars=6000))
        # The scaffold user is skipped → the assistant reply attaches to nothing
        # and becomes a leading assistant-only chunk; then the real turn.
        contents = [c.content for c in chunks]
        assert any("real question" in c and "real answer" in c for c in contents)
        assert not any("local-command-caveat" in c for c in contents)

    def test_malformed_lines_skipped(self, tmp_path):
        path = tmp_path / "session.jsonl"
        path.write_text(
            json.dumps({"type": "user", "message": {"content": "good line"}}) + "\n"
            "not json at all\n"
            + json.dumps({"type": "assistant",
                          "message": {"content": [{"type": "text", "text": "reply"}]}}) + "\n",
            encoding="utf-8",
        )
        chunks = list(corr._iter_turn_chunks(path, max_chars=6000))
        assert len(chunks) == 1
        assert "good line" in chunks[0].content


class TestRenderTranscript:
    def test_renders_readable_transcript(self, tmp_path):
        records = [
            {"type": "user", "timestamp": "2026-04-16T10:00:00Z",
             "message": {"content": "question one"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:00:01Z",
             "message": {"content": [
                 {"type": "thinking", "thinking": "hidden"},
                 {"type": "text", "text": "answer one"},
                 {"type": "tool_use", "name": "Read", "input": {}},
             ]}},
        ]
        path = tmp_path / "s.jsonl"
        _write_jsonl(path, records)
        out = corr.render_transcript(path)
        assert "question one" in out
        assert "answer one" in out
        assert "hidden" not in out
        assert "USER:" in out
        assert "ASSISTANT:" in out


@_sqlite_vec_required
class TestIngestSessions:
    def test_ingests_specific_paths(self, corr_cfg, tmp_path):
        records = [
            {"type": "user", "timestamp": "2026-04-16T10:00:00Z",
             "message": {"content": "scope and inversion"}},
            {"type": "assistant", "timestamp": "2026-04-16T10:00:01Z",
             "message": {"content": [{"type": "text", "text": "fail modes before plans"}]}},
        ]
        session = tmp_path / "session.jsonl"
        _write_jsonl(session, records)
        embed = _deterministic_embed(corr_cfg["embed_dim"])
        stats = corr.ingest_sessions(
            cfg=corr_cfg, embed_fn=embed, paths=[str(session)],
        )
        assert stats.files_scanned == 1
        assert stats.chunks_new >= 1
        assert not stats.errors

    def test_ingest_sessions_idempotent(self, corr_cfg, tmp_path):
        records = [
            {"type": "user", "message": {"content": "test question"}},
            {"type": "assistant",
             "message": {"content": [{"type": "text", "text": "test reply"}]}},
        ]
        session = tmp_path / "session.jsonl"
        _write_jsonl(session, records)
        embed = _deterministic_embed(corr_cfg["embed_dim"])
        s1 = corr.ingest_sessions(cfg=corr_cfg, embed_fn=embed, paths=[str(session)])
        s2 = corr.ingest_sessions(cfg=corr_cfg, embed_fn=embed, paths=[str(session)])
        assert s1.chunks_new >= 1
        assert s2.chunks_new == 0
        assert s2.chunks_existing >= 1


class TestGracefulFailures:
    def test_missing_api_key_raises_useful_error(self, monkeypatch):
        """No API key → RuntimeError with install/setup guidance (not crash)."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as exc_info:
            corr._embed_openai(["hello"], model="text-embedding-3-small")
        assert "OPENAI_API_KEY" in str(exc_info.value) or "API key" in str(exc_info.value)

    def test_duration_parser_accepts_suffixes(self):
        assert corr._parse_duration("30s") == 30
        assert corr._parse_duration("5m") == 300
        assert corr._parse_duration("2h") == 7200
        assert corr._parse_duration("1d") == 86400

    def test_duration_parser_rejects_garbage(self):
        with pytest.raises(ValueError):
            corr._parse_duration("banana")
        with pytest.raises(ValueError):
            corr._parse_duration("30")  # no unit
