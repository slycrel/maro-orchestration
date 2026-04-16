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
