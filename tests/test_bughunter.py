"""Tests for bughunter — static code scan."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


def _write_py(tmp_path, name: str, code: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(code))
    return p


# ---------------------------------------------------------------------------
# scan_file
# ---------------------------------------------------------------------------

class TestScanFile:
    def test_clean_file_no_findings(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "clean.py", """
            def add(a: int, b: int) -> int:
                return a + b
        """)
        findings, ok = scan_file(str(p))
        assert ok
        assert findings == []

    def test_syntax_error_returns_finding(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "broken.py", "def foo(:\n    pass\n")
        findings, ok = scan_file(str(p))
        assert not ok
        assert len(findings) == 1
        assert findings[0].code == "BH000"
        assert findings[0].severity == "error"

    def test_bare_except_detected(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "bare.py", """
            try:
                x = 1
            except:
                pass
        """)
        findings, ok = scan_file(str(p))
        assert ok
        bh001 = [f for f in findings if f.code == "BH001"]
        assert len(bh001) == 1
        assert bh001[0].severity == "warning"

    def test_mutable_default_list(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "mutdef.py", """
            def foo(items=[]):
                items.append(1)
        """)
        findings, ok = scan_file(str(p))
        assert ok
        bh003 = [f for f in findings if f.code == "BH003"]
        assert len(bh003) == 1
        assert bh003[0].severity == "warning"

    def test_mutable_default_dict(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "mutdict.py", """
            def foo(d={}):
                return d
        """)
        findings, ok = scan_file(str(p))
        bh003 = [f for f in findings if f.code == "BH003"]
        assert len(bh003) == 1

    def test_broad_exception_with_logic(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "broad.py", """
            try:
                x = 1
            except Exception as e:
                do_something(e)
                raise
        """)
        findings, ok = scan_file(str(p))
        bh002 = [f for f in findings if f.code == "BH002"]
        assert len(bh002) == 1
        assert bh002[0].severity == "info"

    def test_broad_exception_pass_only_no_finding(self, tmp_path):
        """except Exception: pass should not trigger BH002 (body only has Pass)."""
        from bughunter import scan_file
        p = _write_py(tmp_path, "passonly.py", """
            try:
                x = 1
            except Exception:
                pass
        """)
        findings, ok = scan_file(str(p))
        bh002 = [f for f in findings if f.code == "BH002"]
        assert bh002 == []

    def test_todo_comment_detected(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "todos.py", """
            # TODO: fix this later
            x = 1
        """)
        findings, ok = scan_file(str(p), include_todos=True)
        bh010 = [f for f in findings if f.code == "BH010"]
        assert len(bh010) == 1

    def test_todo_comment_excluded_by_default(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "todos2.py", """
            # TODO: fix this later
            x = 1
        """)
        findings, ok = scan_file(str(p), include_todos=False)
        bh010 = [f for f in findings if f.code == "BH010"]
        assert bh010 == []

    def test_shadowed_builtin_arg(self, tmp_path):
        from bughunter import scan_file
        p = _write_py(tmp_path, "shadow.py", """
            def foo(list, dict):
                return list + [1]
        """)
        findings, ok = scan_file(str(p))
        bh004 = [f for f in findings if f.code == "BH004"]
        assert len(bh004) == 2


# ---------------------------------------------------------------------------
# run_bughunter
# ---------------------------------------------------------------------------

class TestRunBughunter:
    def test_scan_clean_dir(self, tmp_path):
        from bughunter import run_bughunter
        _write_py(tmp_path, "clean.py", "x = 1\n")
        report = run_bughunter(str(tmp_path))
        assert report.files_scanned == 1
        assert report.error_count == 0
        assert report.warning_count == 0

    def test_scan_finds_warnings(self, tmp_path):
        from bughunter import run_bughunter
        _write_py(tmp_path, "bad.py", """
            try:
                pass
            except:
                pass

            def foo(items=[]):
                pass
        """)
        report = run_bughunter(str(tmp_path), min_severity="warning")
        assert report.warning_count >= 2  # BH001 + BH003

    def test_summary_format(self, tmp_path):
        from bughunter import run_bughunter
        _write_py(tmp_path, "ok.py", "y = 2\n")
        report = run_bughunter(str(tmp_path))
        s = report.summary()
        assert "files scanned" in s
        assert "errors" in s

    def test_to_dict_structure(self, tmp_path):
        from bughunter import run_bughunter
        _write_py(tmp_path, "ok.py", "z = 3\n")
        report = run_bughunter(str(tmp_path))
        d = report.to_dict()
        assert "files_scanned" in d
        assert "findings" in d
        assert "error_count" in d

    def test_min_severity_filters_info(self, tmp_path):
        """With min_severity=warning, info findings should be excluded."""
        from bughunter import run_bughunter
        _write_py(tmp_path, "broad.py", """
            try:
                x = 1
            except Exception as e:
                do_something(e)
        """)
        report_warn = run_bughunter(str(tmp_path), min_severity="warning")
        report_info = run_bughunter(str(tmp_path), min_severity="info")
        # BH002 is info — should be absent at warning level, present at info level
        assert report_warn.info_count == 0
        assert report_info.info_count >= 1

    def test_own_src_scans_clean(self):
        """Bughunter should find no warnings/errors in its own src directory."""
        from bughunter import run_bughunter
        import os
        src_dir = str(Path(__file__).parent.parent / "src")
        report = run_bughunter(src_dir, min_severity="warning")
        assert report.error_count == 0
        assert report.warning_count == 0
