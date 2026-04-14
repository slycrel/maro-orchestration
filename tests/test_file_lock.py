"""Tests for file_lock.py — advisory locking on shared data store writes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json

from file_lock import locked_write, locked_append


class TestLockedWrite:
    def test_basic_write_under_lock(self, tmp_path):
        path = tmp_path / "data.jsonl"
        with locked_write(path):
            path.write_text("hello\n")
        assert path.read_text() == "hello\n"

    def test_lock_file_created(self, tmp_path):
        path = tmp_path / "data.jsonl"
        lock_path = tmp_path / "data.jsonl.lock"
        with locked_write(path):
            assert lock_path.exists()
            path.write_text("test\n")

    def test_reentrant_same_thread(self, tmp_path):
        """Nested locked_write on same path doesn't deadlock (reentrancy guard)."""
        path = tmp_path / "data.jsonl"
        with locked_write(path):
            with locked_write(path):
                path.write_text("nested\n")
        assert path.read_text() == "nested\n"

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "data.jsonl"
        with locked_write(path):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("deep\n")
        assert path.read_text() == "deep\n"

    def test_exception_inside_lock_releases(self, tmp_path):
        path = tmp_path / "data.jsonl"
        with pytest.raises(ValueError):
            with locked_write(path):
                raise ValueError("boom")
        # Lock should be released — can acquire again
        with locked_write(path):
            path.write_text("after error\n")
        assert path.read_text() == "after error\n"


class TestLockedAppend:
    def test_appends_single_line(self, tmp_path):
        path = tmp_path / "out.jsonl"
        locked_append(path, json.dumps({"x": 1}))
        lines = path.read_text().splitlines()
        assert lines == ['{"x": 1}']

    def test_appends_multiple_lines_sequential(self, tmp_path):
        path = tmp_path / "out.jsonl"
        locked_append(path, json.dumps({"a": 1}))
        locked_append(path, json.dumps({"b": 2}))
        locked_append(path, json.dumps({"c": 3}))
        lines = path.read_text().splitlines()
        assert len(lines) == 3
        assert json.loads(lines[0]) == {"a": 1}
        assert json.loads(lines[2]) == {"c": 3}

    def test_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "deep" / "sub" / "out.jsonl"
        locked_append(path, "line1")
        assert path.exists()
        assert path.read_text().strip() == "line1"

    def test_adds_newline_terminator(self, tmp_path):
        path = tmp_path / "out.jsonl"
        locked_append(path, "no-newline-in-arg")
        content = path.read_text()
        assert content.endswith("\n")
        assert content.count("\n") == 1

    def test_reentrant_with_locked_write(self, tmp_path):
        """locked_append inside locked_write on same path doesn't deadlock."""
        path = tmp_path / "out.jsonl"
        with locked_write(path):
            path.write_text("existing\n")
            # locked_append uses locked_write internally — reentrancy guard fires
            locked_append(path, "appended")
        lines = path.read_text().splitlines()
        assert "appended" in lines
