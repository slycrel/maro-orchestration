"""Pins for harness-side execution receipts (MH #1 prevention half).

The load-bearing properties: receipts come only from the recorder's
call files (executor-unreachable evidence), loading never raises, and
the three evidence states — process work recorded / no process work
recorded / no record available — stay distinct all the way into the
pass-audit prompt (absence of record must never read as absence of
work).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from execution_receipts import (  # noqa: E402
    load_receipts, render_receipt_evidence, audit_receipt_block,
    MAX_RECEIPTS,
)


def _write_call(calls_dir, n, events):
    calls_dir.mkdir(parents=True, exist_ok=True)
    (calls_dir / f"call-{n:05d}.json").write_text(
        json.dumps({"tool_events": events}), encoding="utf-8")


def _bash(cmd, output="ok"):
    return {"name": "Bash", "input": {"command": cmd}, "output": output}


class TestLoad:
    def test_collects_commands_and_bounded_output(self, tmp_path):
        _write_call(tmp_path / "build/calls", 1,
                    [_bash("pytest -q", "8266 passed"), _bash("ls -la")])
        rows = load_receipts(tmp_path)
        assert [r["command"] for r in rows] == ["pytest -q", "ls -la"]
        assert rows[0]["output_head"] == "8266 passed"
        assert rows[0]["call"] == "call-00001.json"

    def test_malformed_file_fails_alone(self, tmp_path):
        calls = tmp_path / "build/calls"
        _write_call(calls, 1, [_bash("echo one")])
        (calls / "call-00002.json").write_text("{not json", encoding="utf-8")
        _write_call(calls, 3, [_bash("echo three")])
        assert [r["command"] for r in load_receipts(tmp_path)] == [
            "echo one", "echo three"]

    def test_non_command_events_skipped(self, tmp_path):
        _write_call(tmp_path / "build/calls", 1, [
            {"name": "Read", "input": {"file_path": "/x"}},
            {"name": "Bash", "input": {"command": "   "}},
            "not-a-dict",
            _bash("real command"),
        ])
        assert [r["command"] for r in load_receipts(tmp_path)] == [
            "real command"]

    def test_cap_bounds_the_collection(self, tmp_path):
        _write_call(tmp_path / "build/calls", 1,
                    [_bash(f"cmd {i}") for i in range(MAX_RECEIPTS + 50)])
        assert len(load_receipts(tmp_path)) == MAX_RECEIPTS

    def test_missing_dir_returns_empty_never_raises(self, tmp_path):
        assert load_receipts(tmp_path / "nope") == []
        assert load_receipts(None) == []


class TestRender:
    def test_process_marker_executions_surface_with_output(self):
        rows = [{"command": "python3 -m pytest tests/ -q",
                 "output_head": "12 passed", "call": "c"},
                {"command": "cat notes.md", "output_head": "", "call": "c"}]
        text = render_receipt_evidence(rows)
        assert "Process-shaped executions" in text
        assert "pytest" in text and "12 passed" in text

    def test_no_process_work_is_stated_positively(self):
        rows = [{"command": "echo done > report.md", "output_head": "",
                 "call": "c"}]
        text = render_receipt_evidence(rows)
        assert "NONE recorded" in text

    def test_checked_artifact_provenance_matches_basenames(self):
        rows = [{"command": "grep -c PASS results.txt",
                 "output_head": "3", "call": "c"},
                {"command": "python3 run_bench.py > results.txt",
                 "output_head": "", "call": "c"}]
        checks = [{"command": "grep -q PASS results.txt",
                   "description": "results claim a pass"}]
        text = render_receipt_evidence(rows, checks)
        assert "results.txt: 2 recorded command(s)" in text

    def test_empty_receipts_render_empty(self):
        assert render_receipt_evidence([]) == ""


class TestAuditBlock:
    def test_no_run_dir_is_no_signal(self, monkeypatch):
        import execution_receipts as er
        monkeypatch.setattr("runs.current_run_dir", lambda: None)
        block = er.audit_receipt_block([])
        assert "UNAVAILABLE" in block
        assert "not as evidence of absence" in block

    def test_recorded_run_renders_receipts_header(self, tmp_path, monkeypatch):
        _write_call(tmp_path / "build/calls", 1, [_bash("pytest -q", "ok")])
        monkeypatch.setattr("runs.current_run_dir", lambda: tmp_path)
        block = audit_receipt_block([])
        assert "RECORDED BY THE HARNESS" in block
        assert "pytest -q" in block

    def test_run_dir_without_events_is_no_signal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("runs.current_run_dir", lambda: tmp_path)
        block = audit_receipt_block([])
        assert "UNAVAILABLE" in block

    def test_crash_degrades_to_no_signal(self, monkeypatch):
        import execution_receipts as er
        monkeypatch.setattr(er, "load_receipts",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        monkeypatch.setattr("runs.current_run_dir", lambda: "/tmp")
        block = er.audit_receipt_block([])
        assert "UNAVAILABLE" in block


class TestPassAuditWiring:
    def test_pass_audit_prompt_carries_receipt_block(self, tmp_path,
                                                     monkeypatch):
        """The audit user message must contain exactly one of the three
        receipt states — here, real receipts from the recorder."""
        import closure_verify

        _write_call(tmp_path / "build/calls", 1,
                    [_bash("pytest -q", "3 passed")])
        monkeypatch.setattr("runs.current_run_dir", lambda: tmp_path)

        captured = {}

        class _Adapter:
            def complete(self, messages, **kw):
                captured["messages"] = messages

                class R:
                    content = ('{"agrees": true, "reason": "r", '
                               '"confidence": 0.9}')
                return R()

        out = closure_verify._audit_positive_verdict(
            goal="g", adapter=_Adapter(), summary="s",
            check_results=[{"command": "grep -q ok r.txt",
                            "description": "d", "outcome": "pass"}],
            workspace_path=str(tmp_path))
        assert out.get("agrees") is True
        user_msg = captured["messages"][1].content
        assert "RECORDED BY THE HARNESS" in user_msg
        assert "pytest -q" in user_msg
        # Receipts sit OUTSIDE the untrusted-artifact fence.
        assert user_msg.index("RECORDED BY THE HARNESS") < \
            user_msg.index("BEGIN UNTRUSTED ARTIFACT EXCERPTS")
