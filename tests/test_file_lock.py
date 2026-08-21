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


class TestAtomicWritePerms:
    """data-r2-03: mkstemp's 0600 must not leak onto real files."""

    def test_rewrite_preserves_existing_mode(self, tmp_path):
        import os
        from file_lock import atomic_write
        path = tmp_path / "ledger.jsonl"
        path.write_text("old\n")
        os.chmod(path, 0o644)
        atomic_write(path, "new\n")
        assert path.read_text() == "new\n"
        assert (os.stat(path).st_mode & 0o777) == 0o644

    def test_new_file_gets_umask_mode_not_0600(self, tmp_path):
        import os
        from file_lock import atomic_write
        old = os.umask(0o022)
        try:
            path = tmp_path / "fresh.md"
            atomic_write(path, "content\n")
            assert (os.stat(path).st_mode & 0o777) == 0o644
        finally:
            os.umask(old)


class TestByteSafeRmw:
    """locked_rmw + atomic_write round-trip bytes that are not valid UTF-8.

    Before 2026-08-17 the rmw read was a strict whole-file decode, so one
    crash-torn append anywhere in a store made EVERY future rmw of that
    store raise UnicodeDecodeError — a ValueError, invisible to the
    `except OSError` guards most callers hold. One bad line became a
    permanent write outage; all six memory_ledger stampers had it (found
    by adversarial review, confirmed by probe: six stampers, six raises).
    surrogateescape on both sides keeps the torn bytes as lone surrogates
    through fn and writes them back verbatim.
    """

    _TORN = b'{"cut": "\xff\x80 mid-codepoint'

    def test_rmw_survives_and_preserves_undecodable_bytes(self, tmp_path):
        from file_lock import locked_rmw
        path = tmp_path / "store.jsonl"
        healthy = b'{"ok": 1}\n'
        path.write_bytes(healthy + self._TORN + b"\n")
        # Identity rewrite: before the fix this line RAISED — asserting the
        # return would never run. The no-raise is the regression pin.
        locked_rmw(path, lambda old: old)
        assert path.read_bytes() == healthy + self._TORN + b"\n"

    def test_fn_sees_the_torn_line_as_a_skippable_row(self, tmp_path):
        # The contract the stampers rely on: per-line json.loads fails on
        # the surrogate-carrying line exactly like any malformed row, so a
        # scan-and-rejoin edits the healthy row and keeps the torn one.
        from file_lock import locked_rmw
        path = tmp_path / "store.jsonl"
        path.write_bytes(b'{"ok": 1}\n' + self._TORN + b"\n")

        def bump(old: str) -> str:
            out = []
            for line in old.splitlines():
                try:
                    row = json.loads(line)
                    row["ok"] += 1
                    out.append(json.dumps(row))
                except (json.JSONDecodeError, ValueError):
                    out.append(line)
            return "\n".join(out) + "\n"

        locked_rmw(path, bump)
        assert path.read_bytes() == b'{"ok": 2}\n' + self._TORN + b"\n"

    def test_atomic_write_surrogateescape_is_opt_in(self, tmp_path):
        # The write half alone — and its SCOPE. Opted in, a rewrite that
        # carried a torn line through the byte-safe read half writes the
        # original bytes back instead of dying with UnicodeEncodeError at
        # the last moment. But the default stays strict: most callers write
        # content they built themselves, where a lone surrogate is an
        # upstream bug and the loud encoder error at the write site is the
        # only signal naming it (round 2, 2026-08-17 adversarial review —
        # a global surrogateescape default converts that signal into
        # silently persisted mojibake for ~19 callers that never asked).
        from file_lock import atomic_write
        path = tmp_path / "store.jsonl"
        text = self._TORN.decode("utf-8", errors="surrogateescape")
        atomic_write(path, text, errors="surrogateescape")
        assert path.read_bytes() == self._TORN
        with pytest.raises(UnicodeEncodeError):
            atomic_write(tmp_path / "other.md", text)

    def test_valid_utf8_is_untouched(self, tmp_path):
        # surrogateescape must be a no-op for healthy stores, multibyte
        # content included.
        from file_lock import locked_rmw
        path = tmp_path / "store.jsonl"
        content = '{"note": "café ✓"}\n'
        path.write_text(content, encoding="utf-8")
        locked_rmw(path, lambda old: old)
        assert path.read_text(encoding="utf-8") == content


class TestADurableAppendReachesTheDisk:
    """Adversarial r15 (two seats, probed): locked_append rode the page
    cache while atomic_write fsyncs — so a retention archive was LESS
    durable than the deletion it justifies. durable=True flushes and
    fsyncs the file, plus the parent directory when the append created
    the file; require=True forbids the fail-open unlocked fallback."""

    def test_durable_fsyncs_file_and_new_parent_dir(
            self, tmp_path, monkeypatch):
        import os as _os
        from file_lock import locked_append
        path = tmp_path / "a.jsonl"
        fsyncs = []
        real = _os.fsync
        monkeypatch.setattr(
            _os, "fsync", lambda fd: (fsyncs.append(fd), real(fd))[1])
        locked_append(path, "one", durable=True)
        assert len(fsyncs) == 2, "create: file + parent dir"
        fsyncs.clear()
        locked_append(path, "two", durable=True)
        assert len(fsyncs) == 1, "existing file: file only"
        assert path.read_text(encoding="utf-8") == "one\ntwo\n"

    def test_plain_append_does_not_fsync(self, tmp_path, monkeypatch):
        import os as _os
        from file_lock import locked_append
        fsyncs = []
        real = _os.fsync
        monkeypatch.setattr(
            _os, "fsync", lambda fd: (fsyncs.append(fd), real(fd))[1])
        locked_append(tmp_path / "a.jsonl", "one")
        assert fsyncs == []

    def test_require_refuses_the_fail_open_fallback(
            self, tmp_path, monkeypatch):
        import fcntl
        import pytest
        from file_lock import FileLockTimeout, locked_append
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        path = tmp_path / "a.jsonl"
        holder = open(str(path) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            with pytest.raises(FileLockTimeout):
                locked_append(path, "one", timeout_s=0.2, require=True)
            assert not path.exists()
        finally:
            holder.close()


class TestATornTailIsFramedNotFused:
    """Adversarial r16 (Architect, probed): appending straight onto a
    crash-torn unterminated tail fuses two rows into one malformed line
    — for the retention archive that turned the sole archived copy
    unreadable while the live delete stood. locked_append now frames
    the fragment with an LF first: bytes untouched, fragment strandable,
    new row readable."""

    def test_the_fragment_becomes_its_own_line(self, tmp_path):
        from file_lock import locked_append
        p = tmp_path / "a.jsonl"
        p.write_bytes(b'{"id":"torn')
        locked_append(p, '{"id":"b"}')
        assert p.read_bytes() == b'{"id":"torn\n{"id":"b"}\n'

    def test_a_clean_tail_is_not_double_framed(self, tmp_path):
        from file_lock import locked_append
        p = tmp_path / "a.jsonl"
        locked_append(p, "one")
        locked_append(p, "two")
        assert p.read_text(encoding="utf-8") == "one\ntwo\n"


class TestLockedRmwRequiresItsLock:
    """Adversarial r16 (Architect, probed): locked_rmw EXISTS to prevent
    lost updates, so the fail-open degraded mode is self-contradictory —
    an unlocked RMW is the race the primitive was written to close."""

    def test_contended_fail_open_raises_before_fn(
            self, tmp_path, monkeypatch):
        import fcntl
        import pytest
        from file_lock import FileLockTimeout, locked_rmw
        monkeypatch.setenv("MARO_FILELOCK_FAIL_OPEN", "1")
        monkeypatch.setenv("MARO_FILELOCK_TIMEOUT_S", "1")
        p = tmp_path / "s.jsonl"
        p.write_text("x\n", encoding="utf-8")
        holder = open(str(p) + ".lock", "w")
        try:
            fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
            ran = {"fn": False}
            with pytest.raises(FileLockTimeout):
                locked_rmw(p, lambda t: (ran.__setitem__("fn", True), t)[1])
            assert not ran["fn"]
            assert p.read_text(encoding="utf-8") == "x\n"
        finally:
            holder.close()
