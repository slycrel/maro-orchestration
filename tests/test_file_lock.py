"""Tests for file_lock.py — advisory locking on shared data store writes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from file_lock import locked_write


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
