"""Tests for convo_miner.py — Phase 48 Conversation Mining."""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _score_line
# ---------------------------------------------------------------------------

class TestScoreLine:
    def _call(self, line):
        from convo_miner import _score_line
        return _score_line(line)

    def test_high_signal_with_domain(self):
        conf, signals = self._call("we should add a kill switch to the agent loop")
        assert conf >= 0.4
        assert any("should" in s or "we" in s for s in signals)

    def test_domain_required(self):
        conf, _ = self._call("we should go to the grocery store today")
        assert conf == 0.0  # no domain keyword

    def test_too_short(self):
        conf, _ = self._call("agent loop")
        assert conf == 0.0

    def test_noise_filtered(self):
        conf, _ = self._call(">> ERROR: agent loop failed")
        assert conf == 0.0

    def test_unchecked_todo_with_domain(self):
        conf, signals = self._call("- [ ] add a self-improvement loop to the evolver")
        assert conf > 0
        assert any("todo" in s or "[ ]" in s for s in signals)

    def test_multiple_signals_boost_confidence(self):
        conf_single, _ = self._call("we should add memory to the agent loop")
        conf_double, _ = self._call("we should maybe add memory to the agent loop ideally")
        assert conf_double >= conf_single

    def test_confidence_capped_at_095(self):
        conf, _ = self._call(
            "we should ideally maybe eventually todo add memory to the agent loop evolver persona"
        )
        assert conf <= 0.95


# ---------------------------------------------------------------------------
# _extract_ideas_from_text
# ---------------------------------------------------------------------------

class TestExtractIdeasFromText:
    def _call(self, text, source="test"):
        from convo_miner import _extract_ideas_from_text
        return _extract_ideas_from_text(text, source)

    def test_extracts_matching_lines(self):
        text = textwrap.dedent("""\
            Some preamble text.
            We should add a kill switch to the agent loop.
            Random noise here.
            I want to build a self-improvement evolver eventually.
        """)
        ideas = self._call(text)
        assert len(ideas) >= 1
        assert any("kill switch" in i.text or "evolver" in i.text for i in ideas)

    def test_skips_noise(self):
        text = ">> ERROR: agent loop failed\n>>> DEBUG: memory module loaded"
        ideas = self._call(text)
        assert ideas == []

    def test_source_preserved(self):
        ideas = self._call("we should build a skill evolver system", "session:abc123")
        assert all(i.source == "session:abc123" for i in ideas)

    def test_empty_text(self):
        assert self._call("") == []


# ---------------------------------------------------------------------------
# scan_session_logs
# ---------------------------------------------------------------------------

class TestScanSessionLogs:
    def test_scans_user_messages(self, tmp_path):
        """Should extract ideas from user messages in JSONL files."""
        # Write a minimal session log
        entry = {
            "type": "user",
            "timestamp": "2026-04-01T12:00:00Z",
            "message": {
                "content": [
                    {"type": "text", "text": "we should add a memory evolver to the agent loop"}
                ]
            }
        }
        jf = tmp_path / "abc123.jsonl"
        jf.write_text(json.dumps(entry) + "\n")

        from convo_miner import scan_session_logs
        ideas = scan_session_logs(projects_dir=tmp_path)
        assert len(ideas) >= 1
        assert any("evolver" in i.text for i in ideas)

    def test_skips_assistant_messages(self, tmp_path):
        entry = {
            "type": "assistant",
            "message": {"content": "we should add a memory evolver to the agent loop"}
        }
        jf = tmp_path / "abc.jsonl"
        jf.write_text(json.dumps(entry) + "\n")

        from convo_miner import scan_session_logs
        ideas = scan_session_logs(projects_dir=tmp_path)
        assert ideas == []

    def test_since_filter(self, tmp_path):
        old_entry = {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"content": [{"type": "text", "text": "we should add memory evolver"}]}
        }
        jf = tmp_path / "old.jsonl"
        jf.write_text(json.dumps(old_entry) + "\n")

        since = datetime(2026, 3, 1, tzinfo=timezone.utc)
        from convo_miner import scan_session_logs
        ideas = scan_session_logs(projects_dir=tmp_path, since=since)
        assert ideas == []

    def test_missing_dir_returns_empty(self, tmp_path):
        from convo_miner import scan_session_logs
        ideas = scan_session_logs(projects_dir=tmp_path / "nonexistent")
        assert ideas == []

    def test_malformed_jsonl_skipped(self, tmp_path):
        jf = tmp_path / "bad.jsonl"
        jf.write_text("not json\n{also not json\n")
        from convo_miner import scan_session_logs
        ideas = scan_session_logs(projects_dir=tmp_path)
        assert ideas == []  # no crash


# ---------------------------------------------------------------------------
# scan_openclaw_docs
# ---------------------------------------------------------------------------

class TestScanOpenclawDocs:
    def test_scans_tasks_md(self, tmp_path):
        tasks = tmp_path / "TASKS.md"
        tasks.write_text("- [ ] Add a self-improvement loop to the agent evolver\n")

        from convo_miner import scan_openclaw_docs
        ideas = scan_openclaw_docs(workspace=tmp_path)
        assert any("evolver" in i.text for i in ideas)

    def test_missing_workspace_returns_empty(self, tmp_path):
        from convo_miner import scan_openclaw_docs
        ideas = scan_openclaw_docs(workspace=tmp_path / "nonexistent")
        assert ideas == []


# ---------------------------------------------------------------------------
# scan_poe_memory
# ---------------------------------------------------------------------------

class TestScanPoeMemory:
    def test_extracts_unchecked_backlog_items(self, tmp_path):
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text(textwrap.dedent("""\
            - [x] Done item about the agent loop
            - [ ] Add evolver self-improvement to the heartbeat
            - [ ] Another open memory task for the agent
        """))
        from convo_miner import scan_poe_memory
        ideas = scan_poe_memory(workspace=tmp_path)
        assert len(ideas) == 2
        assert all(i.confidence >= 0.8 for i in ideas)

    def test_skips_checked_items(self, tmp_path):
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("- [x] Completed agent evolver task\n")
        from convo_miner import scan_poe_memory
        ideas = scan_poe_memory(workspace=tmp_path)
        assert ideas == []


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------

class TestDeduplicate:
    def _make_idea(self, text, conf=0.5):
        from convo_miner import Idea
        return Idea(text=text, source="test", confidence=conf)

    def test_removes_near_duplicates(self):
        from convo_miner import _deduplicate
        ideas = [
            self._make_idea("we should add a kill switch to the agent loop evolver"),
            self._make_idea("we should add a kill switch to the agent loop evolver system"),
        ]
        result = _deduplicate(ideas)
        assert len(result) == 1

    def test_keeps_distinct_ideas(self):
        from convo_miner import _deduplicate
        ideas = [
            self._make_idea("we should add a kill switch to the agent loop"),
            self._make_idea("I want to build a self-improvement evolver with memory"),
        ]
        result = _deduplicate(ideas)
        assert len(result) == 2

    def test_keeps_highest_confidence_on_dup(self):
        from convo_miner import _deduplicate
        ideas = [
            self._make_idea("we should add memory to the agent loop evolver", conf=0.5),
            self._make_idea("we should add memory to the agent loop evolver", conf=0.9),
        ]
        result = _deduplicate(ideas)
        assert len(result) == 1
        assert result[0].confidence == 0.9


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def _make_idea(self, text, conf=0.5, source="test"):
        from convo_miner import Idea
        return Idea(text=text, source=source, confidence=conf)

    def test_report_is_markdown(self):
        from convo_miner import generate_report
        ideas = [self._make_idea("we should build a skill evolver loop agent", conf=0.8)]
        report = generate_report(ideas)
        assert "# " in report
        assert "High Confidence" in report

    def test_empty_ideas_no_crash(self):
        from convo_miner import generate_report
        report = generate_report([])
        assert "Generated" in report

    def test_source_breakdown_present(self):
        from convo_miner import generate_report
        ideas = [
            self._make_idea("agent loop evolver skill", conf=0.7, source="session:abc"),
            self._make_idea("memory agent heartbeat loop", conf=0.7, source="git:commits"),
        ]
        report = generate_report(ideas)
        assert "Source Breakdown" in report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_help_runs(self):
        from convo_miner import main
        try:
            main(["--help"])
        except SystemExit as e:
            assert e.code == 0

    def test_no_sessions_no_git_runs(self, tmp_path, monkeypatch, capsys):
        """With minimal flags, should run without error."""
        monkeypatch.setattr("convo_miner.scan_openclaw_docs", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_poe_memory", lambda *a, **kw: [])
        from convo_miner import main
        rc = main(["--no-sessions", "--no-git"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Generated" in out

    def test_output_to_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("convo_miner.scan_session_logs", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_openclaw_docs", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_git_log", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_poe_memory", lambda *a, **kw: [])
        out_file = tmp_path / "report.md"
        from convo_miner import main
        rc = main(["--output", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        assert "Generated" in out_file.read_text()


# ---------------------------------------------------------------------------
# inject_into_backlog
# ---------------------------------------------------------------------------

class TestInjectIntoBacklog:
    def _make_idea(self, text, conf=0.8):
        from convo_miner import Idea
        return Idea(text=text, source="session:test", confidence=conf)

    def test_injects_high_confidence_ideas(self, tmp_path):
        from convo_miner import inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("# Backlog\n\n")
        ideas = [self._make_idea("we should add a memory evolver agent loop system here", conf=0.9)]
        n = inject_into_backlog(ideas, backlog_path=backlog)
        assert n == 1
        content = backlog.read_text()
        assert "mined:" in content
        assert "memory evolver agent loop" in content

    def test_skips_below_threshold(self, tmp_path):
        from convo_miner import inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("# Backlog\n\n")
        ideas = [self._make_idea("we should add a skill evolver loop memory", conf=0.5)]
        n = inject_into_backlog(ideas, backlog_path=backlog, threshold=0.70)
        assert n == 0
        content = backlog.read_text()
        assert "mined:" not in content

    def test_skips_already_present_ideas(self, tmp_path):
        from convo_miner import inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        # Idea text already present in backlog
        backlog.write_text("# Backlog\n\n- [x] memory evolver skill loop system already done\n")
        ideas = [self._make_idea("memory evolver skill loop system already done", conf=0.9)]
        n = inject_into_backlog(ideas, backlog_path=backlog)
        assert n == 0

    def test_creates_backlog_if_not_exists(self, tmp_path):
        from convo_miner import inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        assert not backlog.exists()
        ideas = [self._make_idea("we should add memory agent loop evolver system", conf=0.9)]
        n = inject_into_backlog(ideas, backlog_path=backlog)
        assert n == 1
        assert backlog.exists()

    def test_caps_at_20_injections(self, tmp_path):
        from convo_miner import inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("")
        ideas = [
            self._make_idea(f"we should add agent loop feature {i} to the evolver memory system", conf=0.9)
            for i in range(30)
        ]
        n = inject_into_backlog(ideas, backlog_path=backlog)
        assert n <= 20

    def test_cli_inject_backlog_flag(self, tmp_path, monkeypatch, capsys):
        """--inject-backlog flag causes injection into BACKLOG.md."""
        from convo_miner import Idea, main, inject_into_backlog
        backlog = tmp_path / "BACKLOG.md"
        backlog.write_text("# Backlog\n\n")

        high_idea = Idea(text="we should add a memory evolver agent loop", source="test", confidence=0.9)
        monkeypatch.setattr("convo_miner.scan_session_logs", lambda *a, **kw: [high_idea])
        monkeypatch.setattr("convo_miner.scan_openclaw_docs", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_git_log", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.scan_poe_memory", lambda *a, **kw: [])
        monkeypatch.setattr("convo_miner.inject_into_backlog",
                            lambda ideas, threshold=0.70: inject_into_backlog(ideas, backlog, threshold=threshold))

        rc = main(["--no-git", "--inject-backlog"])
        assert rc == 0
        # stderr should mention injected count
        err = capsys.readouterr().err
        assert "Injected" in err
