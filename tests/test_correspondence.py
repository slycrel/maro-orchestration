"""Tests for correspondence.py — dev-facing retrieval over project docs/memory.

Uses SQLite FTS5 (built-in, no external deps). No network calls, no API keys.
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
# Ingest + query (FTS5 built into sqlite; no external deps)
# ---------------------------------------------------------------------------

@pytest.fixture
def corr_cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "corr.db"),
        "sources": [],
        "max_chunk_chars": 6000,
        "top_k": 5,
    }


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

        stats = corr.ingest(cfg=corr_cfg)
        assert stats.chunks_new == 3
        assert not stats.errors

        hits = corr.query("taste judgment", cfg=corr_cfg)
        assert hits, "expected at least one hit"
        # The 'Taste' section should rank highest
        top = hits[0]
        assert "Taste" in top.section or "taste" in top.content.lower()

    def test_ingest_is_idempotent(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\nbody text\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]

        s1 = corr.ingest(cfg=corr_cfg)
        s2 = corr.ingest(cfg=corr_cfg)
        assert s1.chunks_new >= 1
        assert s2.chunks_new == 0
        assert s2.chunks_existing >= 1

    def test_ingest_updates_on_changed_content(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\noriginal body\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]

        corr.ingest(cfg=corr_cfg)
        src.write_text("# H\nwholly different content\n", encoding="utf-8")
        s2 = corr.ingest(cfg=corr_cfg)
        assert s2.chunks_new >= 1  # new hash ⇒ new row

    def test_since_filter_skips_old_files(self, corr_cfg, tmp_path):
        import os
        src = tmp_path / "old.md"
        src.write_text("# Old\ncontent\n", encoding="utf-8")
        # set mtime to 2 days ago
        past = 1_700_000_000
        os.utime(src, (past, past))
        corr_cfg["sources"] = [str(tmp_path)]

        stats = corr.ingest(cfg=corr_cfg, since_seconds=60)
        assert stats.files_skipped_stale == 1
        assert stats.chunks_new == 0

    def test_status_reports_counts(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\nbody\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]
        corr.ingest(cfg=corr_cfg)

        s = corr.status(cfg=corr_cfg)
        assert "error" not in s
        assert s["total_chunks"] >= 1
        assert s["last_ingest_utc"] is not None

    def test_special_chars_in_query_dont_crash(self, corr_cfg, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# H\nbody with punctuation\n", encoding="utf-8")
        corr_cfg["sources"] = [str(tmp_path)]
        corr.ingest(cfg=corr_cfg)
        # FTS5 MATCH has lots of syntactic gotchas — confirm escape handles them
        corr.query('what is the "scope" concept (v2)?', cfg=corr_cfg)
        corr.query("AND OR NOT NEAR ^ * :", cfg=corr_cfg)
        corr.query("", cfg=corr_cfg)  # empty query — just don't crash


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
        stats = corr.ingest_sessions(cfg=corr_cfg, paths=[str(session)])
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
        s1 = corr.ingest_sessions(cfg=corr_cfg, paths=[str(session)])
        s2 = corr.ingest_sessions(cfg=corr_cfg, paths=[str(session)])
        assert s1.chunks_new >= 1
        assert s2.chunks_new == 0
        assert s2.chunks_existing >= 1


# ---------------------------------------------------------------------------
# Telegram export ingestion
# ---------------------------------------------------------------------------

class TestTelegramExtraction:
    def test_text_string_passthrough(self):
        assert corr._extract_telegram_text("hello") == "hello"

    def test_text_list_entities_flattened(self):
        entities = [
            "check ",
            {"type": "link", "text": "https://x.com/foo"},
            " then tell me what you think",
        ]
        out = corr._extract_telegram_text(entities)
        assert "check " in out
        assert "https://x.com/foo" in out
        assert "then tell me" in out

    def test_text_bot_command_entity(self):
        entities = [{"type": "bot_command", "text": "/start"}]
        assert corr._extract_telegram_text(entities) == "/start"


class TestTelegramTurns:
    def _write(self, tmp_path, messages):
        path = tmp_path / "result.json"
        path.write_text(json.dumps({
            "name": "test chat", "type": "bot_chat", "id": 1, "messages": messages,
        }), encoding="utf-8")
        return path

    def test_single_turn_user_then_bot(self, tmp_path):
        msgs = [
            {"id": 1, "type": "message", "date": "2026-02-05T00:00:00",
             "from": "Jeremy Stone", "text": "hello"},
            {"id": 2, "type": "message", "date": "2026-02-05T00:00:01",
             "from": "poe", "text": "hi there"},
        ]
        path = self._write(tmp_path, msgs)
        chunks = list(corr._iter_telegram_turns(path, max_chars=6000, bot_sender="poe"))
        assert len(chunks) == 1
        assert "USER: hello" in chunks[0].content
        assert "ASSISTANT: hi there" in chunks[0].content
        assert "2026-02-05" in chunks[0].section

    def test_consecutive_user_messages_concat(self, tmp_path):
        msgs = [
            {"id": 1, "type": "message", "date": "2026-02-05T00:00:00",
             "from": "Jeremy Stone", "text": "first line"},
            {"id": 2, "type": "message", "date": "2026-02-05T00:00:01",
             "from": "Jeremy Stone", "text": "second line"},
            {"id": 3, "type": "message", "date": "2026-02-05T00:00:02",
             "from": "poe", "text": "got it"},
        ]
        path = self._write(tmp_path, msgs)
        chunks = list(corr._iter_telegram_turns(path, max_chars=6000, bot_sender="poe"))
        assert len(chunks) == 1
        assert "first line" in chunks[0].content
        assert "second line" in chunks[0].content

    def test_multiple_turns_yield_separate_chunks(self, tmp_path):
        msgs = [
            {"id": 1, "type": "message", "date": "2026-02-05T00:00:00",
             "from": "Jeremy Stone", "text": "q1"},
            {"id": 2, "type": "message", "date": "2026-02-05T00:00:01",
             "from": "poe", "text": "a1"},
            {"id": 3, "type": "message", "date": "2026-02-05T00:01:00",
             "from": "Jeremy Stone", "text": "q2"},
            {"id": 4, "type": "message", "date": "2026-02-05T00:01:01",
             "from": "poe", "text": "a2"},
        ]
        path = self._write(tmp_path, msgs)
        chunks = list(corr._iter_telegram_turns(path, max_chars=6000, bot_sender="poe"))
        assert len(chunks) == 2

    def test_service_messages_skipped(self, tmp_path):
        msgs = [
            {"id": 1, "type": "service", "action": "joined"},
            {"id": 2, "type": "message", "date": "2026-02-05T00:00:00",
             "from": "Jeremy Stone", "text": "hello"},
            {"id": 3, "type": "message", "date": "2026-02-05T00:00:01",
             "from": "poe", "text": "hi"},
        ]
        path = self._write(tmp_path, msgs)
        chunks = list(corr._iter_telegram_turns(path, max_chars=6000, bot_sender="poe"))
        assert len(chunks) == 1

    def test_x_link_message_preserved(self, tmp_path):
        # Later in the export Jeremy started dumping X links with one-line notes.
        # Those are user-only turns (no bot reply); they should still ingest.
        msgs = [
            {"id": 1, "type": "message", "date": "2026-04-10T00:00:00",
             "from": "Jeremy Stone",
             "text": [
                 {"type": "link", "text": "https://x.com/karpathy/status/1"},
                 "\n\nworth a look for orchestration?",
             ]},
        ]
        path = self._write(tmp_path, msgs)
        chunks = list(corr._iter_telegram_turns(path, max_chars=6000, bot_sender="poe"))
        assert len(chunks) == 1
        assert "x.com/karpathy" in chunks[0].content
        assert "worth a look" in chunks[0].content


class TestIngestTelegram:
    def test_ingest_telegram_specific_path(self, corr_cfg, tmp_path):
        msgs = [
            {"id": 1, "type": "message", "date": "2026-02-05T00:00:00",
             "from": "Jeremy Stone", "text": "how does closure gating work"},
            {"id": 2, "type": "message", "date": "2026-02-05T00:00:01",
             "from": "poe", "text": "inversion probes the scope's failure modes"},
        ]
        path = tmp_path / "result.json"
        path.write_text(json.dumps({
            "name": "x", "type": "bot_chat", "id": 1, "messages": msgs,
        }), encoding="utf-8")
        stats = corr.ingest_telegram(cfg=corr_cfg, paths=[str(path)])
        assert stats.files_scanned == 1
        assert stats.chunks_new >= 1
        assert not stats.errors

    def test_ingest_telegram_directory_resolves_result_json(self, corr_cfg, tmp_path):
        export_dir = tmp_path / "ChatExport_2026-01-01"
        export_dir.mkdir()
        (export_dir / "result.json").write_text(json.dumps({
            "name": "x", "type": "bot_chat", "id": 1, "messages": [
                {"id": 1, "type": "message", "date": "2026-01-01T00:00:00",
                 "from": "Jeremy Stone", "text": "test"},
                {"id": 2, "type": "message", "date": "2026-01-01T00:00:01",
                 "from": "poe", "text": "ack"},
            ],
        }), encoding="utf-8")
        stats = corr.ingest_telegram(cfg=corr_cfg, paths=[str(export_dir)])
        assert stats.files_scanned == 1
        assert stats.chunks_new >= 1


class TestGracefulFailures:
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
