"""Tests for orch_bridges.py — execution bridges, worker session specs, validation bridges."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orch_bridges import (
    ExecutionBridgeError,
    WorkerSessionSpec,
    _append_jsonl_record,
    _coerce_env_map,
    _coerce_positive_timeout,
    _coerce_session_directory_name,
    _coerce_session_file_name,
    _coerce_validation_payload,
    _ensure_nonempty_artifact_name,
    _extract_json_result,
    _extract_session_result_from_text,
    _load_worker_session_manifest,
    _merge_notes,
    _read_jsonl_records,
    _validation_bridge_name,
    _validation_trace_event,
    artifact_validation_bridge,
    chain_validation_bridges,
    command_execution_bridge,
    named_validation_bridge,
    _default_validation_bridge,
    _default_execution_bridge,
)
from orch_items import ExecutionResult, RunRecord, ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run(**overrides) -> RunRecord:
    defaults = dict(
        run_id="run-001",
        project="test-project",
        index=0,
        text="do the thing",
        status="pending",
        source="test",
        worker="general",
        started_at="2026-04-11T00:00:00Z",
        updated_at="2026-04-11T00:00:00Z",
        attempt=1,
    )
    defaults.update(overrides)
    return RunRecord(**defaults)


def _make_execution(**overrides) -> ExecutionResult:
    defaults = dict(status="done", note="ok")
    defaults.update(overrides)
    return ExecutionResult(**defaults)


# ---------------------------------------------------------------------------
# _ensure_nonempty_artifact_name
# ---------------------------------------------------------------------------

class TestEnsureNonemptyArtifactName:
    def test_returns_value_when_nonempty(self):
        assert _ensure_nonempty_artifact_name("payload.json", "default.json") == "payload.json"

    def test_strips_whitespace(self):
        assert _ensure_nonempty_artifact_name("  result.json  ", "default.json") == "result.json"

    def test_returns_default_for_none(self):
        assert _ensure_nonempty_artifact_name(None, "default.json") == "default.json"

    def test_returns_default_for_empty_string(self):
        assert _ensure_nonempty_artifact_name("", "default.json") == "default.json"

    def test_returns_default_for_whitespace_only(self):
        assert _ensure_nonempty_artifact_name("   ", "default.json") == "default.json"

    def test_coerces_non_string_to_string(self):
        assert _ensure_nonempty_artifact_name(42, "default.json") == "42"

    def test_returns_default_for_zero(self):
        # str(0) == "0" which is non-empty, so returns "0"
        assert _ensure_nonempty_artifact_name(0, "default.json") == "default.json"


# ---------------------------------------------------------------------------
# _coerce_session_file_name
# ---------------------------------------------------------------------------

class TestCoerceSessionFileName:
    def test_returns_relative_path(self):
        assert _coerce_session_file_name("result.json", default="d.json", field_name="f") == "result.json"

    def test_returns_default_for_none(self):
        assert _coerce_session_file_name(None, default="d.json", field_name="f") == "d.json"

    def test_returns_default_for_empty(self):
        assert _coerce_session_file_name("", default="d.json", field_name="f") == "d.json"

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="must be a relative path"):
            _coerce_session_file_name("/etc/passwd", default="d.json", field_name="f")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="must not contain path traversal"):
            _coerce_session_file_name("../secret.json", default="d.json", field_name="f")

    def test_rejects_nested_traversal(self):
        with pytest.raises(ValueError, match="must not contain path traversal"):
            _coerce_session_file_name("a/../../b.json", default="d.json", field_name="f")

    def test_normalizes_to_posix(self):
        result = _coerce_session_file_name("sub/result.json", default="d.json", field_name="f")
        assert result == "sub/result.json"


# ---------------------------------------------------------------------------
# _coerce_session_directory_name
# ---------------------------------------------------------------------------

class TestCoerceSessionDirectoryName:
    def test_returns_none_for_none(self):
        assert _coerce_session_directory_name(None, field_name="wd") is None

    def test_returns_none_for_empty(self):
        assert _coerce_session_directory_name("", field_name="wd") is None

    def test_returns_none_for_dot(self):
        assert _coerce_session_directory_name(".", field_name="wd") is None

    def test_returns_relative_dir(self):
        assert _coerce_session_directory_name("subdir", field_name="wd") == "subdir"

    def test_strips_trailing_slash(self):
        assert _coerce_session_directory_name("subdir/", field_name="wd") == "subdir"

    def test_rejects_absolute_path(self):
        with pytest.raises(ValueError, match="must be a relative path"):
            _coerce_session_directory_name("/tmp/evil", field_name="wd")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="must not contain path traversal"):
            _coerce_session_directory_name("../evil", field_name="wd")

    def test_returns_none_for_whitespace(self):
        assert _coerce_session_directory_name("   ", field_name="wd") is None


# ---------------------------------------------------------------------------
# _coerce_env_map
# ---------------------------------------------------------------------------

class TestCoerceEnvMap:
    def test_none_returns_empty(self):
        assert _coerce_env_map(None, worker="w") == {}

    def test_valid_dict(self):
        result = _coerce_env_map({"FOO": "bar", "NUM": 42}, worker="w")
        assert result == {"FOO": "bar", "NUM": "42"}

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError, match="must be an object"):
            _coerce_env_map("not a dict", worker="w")

    def test_rejects_list(self):
        with pytest.raises(ValueError, match="must be an object"):
            _coerce_env_map(["a", "b"], worker="w")

    def test_rejects_empty_key(self):
        with pytest.raises(ValueError, match="non-empty keys"):
            _coerce_env_map({"": "val"}, worker="w")

    def test_strips_key_whitespace(self):
        result = _coerce_env_map({"  KEY  ": "val"}, worker="w")
        assert result == {"KEY": "val"}

    def test_empty_dict_returns_empty(self):
        assert _coerce_env_map({}, worker="w") == {}


# ---------------------------------------------------------------------------
# _coerce_positive_timeout
# ---------------------------------------------------------------------------

class TestCoercePositiveTimeout:
    def test_none_returns_none(self):
        assert _coerce_positive_timeout(None, field_name="t") is None

    def test_positive_float(self):
        assert _coerce_positive_timeout(3.5, field_name="t") == 3.5

    def test_positive_int(self):
        assert _coerce_positive_timeout(10, field_name="t") == 10.0

    def test_string_number(self):
        assert _coerce_positive_timeout("5.0", field_name="t") == 5.0

    def test_rejects_zero(self):
        with pytest.raises(ValueError, match="must be greater than zero"):
            _coerce_positive_timeout(0, field_name="t")

    def test_rejects_negative(self):
        with pytest.raises(ValueError, match="must be greater than zero"):
            _coerce_positive_timeout(-1, field_name="t")

    def test_rejects_bool(self):
        with pytest.raises(ValueError, match="must be a positive number"):
            _coerce_positive_timeout(True, field_name="t")

    def test_rejects_non_numeric_string(self):
        with pytest.raises(ValueError, match="must be a number"):
            _coerce_positive_timeout("abc", field_name="t")


# ---------------------------------------------------------------------------
# _extract_session_result_from_text
# ---------------------------------------------------------------------------

class TestExtractSessionResultFromText:
    def test_extracts_last_json_line(self):
        text = "some log output\n{\"status\": \"done\"}\n"
        result = _extract_session_result_from_text(text)
        assert result == {"status": "done"}

    def test_returns_none_for_empty(self):
        assert _extract_session_result_from_text("") is None

    def test_returns_none_for_none(self):
        assert _extract_session_result_from_text(None) is None

    def test_returns_none_for_no_json(self):
        assert _extract_session_result_from_text("just plain text\nno json here") is None

    def test_prefers_last_json_line(self):
        text = '{\"status\": \"retry\"}\nmore text\n{\"status\": \"done\"}\n'
        result = _extract_session_result_from_text(text)
        assert result == {"status": "done"}

    def test_skips_non_dict_json(self):
        text = '[1, 2, 3]\n{"status": "done"}\n'
        result = _extract_session_result_from_text(text)
        assert result == {"status": "done"}

    def test_returns_none_for_only_array_json(self):
        text = '[1, 2, 3]\n'
        assert _extract_session_result_from_text(text) is None


# ---------------------------------------------------------------------------
# _extract_json_result
# ---------------------------------------------------------------------------

class TestExtractJsonResult:
    def test_parses_valid_json_dict(self):
        result = _extract_json_result('{"status": "done"}')
        assert result == {"status": "done"}

    def test_returns_none_for_empty(self):
        assert _extract_json_result("") is None

    def test_returns_none_for_whitespace(self):
        assert _extract_json_result("   ") is None

    def test_returns_none_for_json_array(self):
        assert _extract_json_result("[1, 2, 3]") is None

    def test_falls_back_to_text_extraction(self):
        text = 'log line\n{"status": "done"}\nmore log'
        result = _extract_json_result(text)
        assert result == {"status": "done"}

    def test_returns_none_for_json_string(self):
        assert _extract_json_result('"hello"') is None


# ---------------------------------------------------------------------------
# JSONL round-trip
# ---------------------------------------------------------------------------

class TestJsonlRoundTrip:
    def test_append_and_read(self, tmp_path):
        path = tmp_path / "records.jsonl"
        _append_jsonl_record(path, {"a": 1})
        _append_jsonl_record(path, {"b": 2})
        records = _read_jsonl_records(path)
        assert records == [{"a": 1}, {"b": 2}]

    def test_read_nonexistent_returns_empty(self, tmp_path):
        path = tmp_path / "missing.jsonl"
        assert _read_jsonl_records(path) == []

    def test_read_skips_blank_lines(self, tmp_path):
        path = tmp_path / "records.jsonl"
        path.write_text('{"a": 1}\n\n\n{"b": 2}\n', encoding="utf-8")
        records = _read_jsonl_records(path)
        assert records == [{"a": 1}, {"b": 2}]

    def test_read_skips_invalid_json(self, tmp_path):
        path = tmp_path / "records.jsonl"
        path.write_text('{"a": 1}\nnot json\n{"b": 2}\n', encoding="utf-8")
        records = _read_jsonl_records(path)
        assert records == [{"a": 1}, {"b": 2}]

    def test_read_skips_non_dict_json(self, tmp_path):
        path = tmp_path / "records.jsonl"
        path.write_text('{"a": 1}\n[1,2]\n"hello"\n{"b": 2}\n', encoding="utf-8")
        records = _read_jsonl_records(path)
        assert records == [{"a": 1}, {"b": 2}]

    def test_append_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "sub" / "deep" / "records.jsonl"
        _append_jsonl_record(path, {"x": 1})
        assert _read_jsonl_records(path) == [{"x": 1}]


# ---------------------------------------------------------------------------
# _load_worker_session_manifest
# ---------------------------------------------------------------------------

class TestLoadWorkerSessionManifest:
    def test_string_manifest(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps("echo hello"), encoding="utf-8")
        spec = _load_worker_session_manifest(path)
        assert spec.command == "echo hello"
        assert spec.payload_name == "worker-payload.json"

    def test_dict_manifest_with_command_string(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({"command": "run.sh"}), encoding="utf-8")
        spec = _load_worker_session_manifest(path)
        assert spec.command == "run.sh"

    def test_dict_manifest_with_command_list(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({"command": ["python3", "-m", "worker"]}), encoding="utf-8")
        spec = _load_worker_session_manifest(path)
        assert "python3" in spec.command
        assert "worker" in spec.command

    def test_dict_manifest_all_fields(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({
            "command": "run.sh",
            "payload_name": "in.json",
            "result_name": "out.json",
            "working_directory": "workdir",
            "environment": {"FOO": "bar"},
            "timeout_seconds": 30,
        }), encoding="utf-8")
        spec = _load_worker_session_manifest(path)
        assert spec.command == "run.sh"
        assert spec.payload_name == "in.json"
        assert spec.result_name == "out.json"
        assert spec.working_directory == "workdir"
        assert spec.environment == {"FOO": "bar"}
        assert spec.timeout_seconds == 30.0

    def test_missing_command_raises(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({"payload_name": "in.json"}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'command'"):
            _load_worker_session_manifest(path)

    def test_empty_string_manifest_raises(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps("   "), encoding="utf-8")
        with pytest.raises(ValueError, match="does not define a command"):
            _load_worker_session_manifest(path)

    def test_non_dict_non_string_raises(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps(42), encoding="utf-8")
        with pytest.raises(ValueError, match="invalid worker session manifest format"):
            _load_worker_session_manifest(path)

    def test_empty_command_string_raises(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({"command": "   "}), encoding="utf-8")
        with pytest.raises(ValueError, match="missing 'command'"):
            _load_worker_session_manifest(path)

    def test_empty_command_list_raises(self, tmp_path):
        path = tmp_path / "worker.json"
        path.write_text(json.dumps({"command": []}), encoding="utf-8")
        with pytest.raises(ValueError, match="invalid worker session command"):
            _load_worker_session_manifest(path)

    def test_working_dir_aliases(self, tmp_path):
        for alias in ("working_directory", "working_dir", "cwd"):
            path = tmp_path / f"worker_{alias}.json"
            path.write_text(json.dumps({"command": "run.sh", alias: "mydir"}), encoding="utf-8")
            spec = _load_worker_session_manifest(path)
            assert spec.working_directory == "mydir"


# ---------------------------------------------------------------------------
# WorkerSessionSpec dataclass
# ---------------------------------------------------------------------------

class TestWorkerSessionSpec:
    def test_defaults(self):
        spec = WorkerSessionSpec(command="echo hi")
        assert spec.command == "echo hi"
        assert spec.payload_name == "worker-payload.json"
        assert spec.result_name == "worker-result.json"
        assert spec.working_directory is None
        assert spec.environment == {}
        assert spec.timeout_seconds is None

    def test_frozen(self):
        spec = WorkerSessionSpec(command="echo hi")
        with pytest.raises(AttributeError):
            spec.command = "something else"


# ---------------------------------------------------------------------------
# named_validation_bridge
# ---------------------------------------------------------------------------

class TestNamedValidationBridge:
    def test_wraps_bridge_and_records_trace(self):
        inner = MagicMock(return_value=ValidationResult(status="done", passed=True, note="ok"))
        bridge = named_validation_bridge("test-bridge", inner)
        run = _make_run()
        execution = _make_execution()
        result = bridge(run, execution)
        assert result.status == "done"
        assert result.passed is True
        assert getattr(bridge, "__orch_validation_name__") == "test-bridge"
        trace = getattr(bridge, "_last_trace")
        assert len(trace) == 1
        assert trace[0]["bridge"] == "test-bridge"

    def test_empty_name_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            named_validation_bridge("", MagicMock())

    def test_none_name_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            named_validation_bridge(None, MagicMock())

    def test_records_error_trace_on_exception(self):
        def bad_bridge(run, execution):
            raise RuntimeError("boom")

        bridge = named_validation_bridge("bad", bad_bridge)
        with pytest.raises(RuntimeError, match="boom"):
            bridge(_make_run(), _make_execution())
        trace = getattr(bridge, "_last_trace")
        assert len(trace) == 1
        assert trace[0]["status"] == "blocked"
        assert trace[0]["error"] == "RuntimeError"


# ---------------------------------------------------------------------------
# _coerce_validation_payload
# ---------------------------------------------------------------------------

class TestCoerceValidationPayload:
    def test_valid_done(self):
        run = _make_run()
        execution = _make_execution()
        result = _coerce_validation_payload(
            {"status": "done", "passed": True, "note": "looks good"},
            run=run, execution=execution,
        )
        assert result.status == "done"
        assert result.passed is True
        assert result.note == "looks good"

    def test_passed_defaults_to_status_done(self):
        run = _make_run()
        execution = _make_execution()
        result = _coerce_validation_payload(
            {"status": "done"},
            run=run, execution=execution,
        )
        assert result.passed is True

    def test_passed_defaults_to_false_for_retry(self):
        run = _make_run()
        execution = _make_execution()
        result = _coerce_validation_payload(
            {"status": "retry"},
            run=run, execution=execution,
        )
        assert result.passed is False

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="invalid review status"):
            _coerce_validation_payload(
                {"status": "invalid"},
                run=_make_run(), execution=_make_execution(),
            )

    def test_empty_status_raises(self):
        with pytest.raises(ValueError, match="invalid review status"):
            _coerce_validation_payload(
                {"status": ""},
                run=_make_run(), execution=_make_execution(),
            )

    def test_non_bool_passed_raises(self):
        with pytest.raises(ValueError, match="must be a boolean"):
            _coerce_validation_payload(
                {"status": "done", "passed": "yes"},
                run=_make_run(), execution=_make_execution(),
            )

    def test_note_falls_back_to_run_id(self):
        run = _make_run(run_id="run-xyz")
        result = _coerce_validation_payload(
            {"status": "done"},
            run=run, execution=_make_execution(),
        )
        assert "run-xyz" in result.note


# ---------------------------------------------------------------------------
# command_execution_bridge
# ---------------------------------------------------------------------------

class TestCommandExecutionBridge:
    def test_empty_command_raises(self):
        with pytest.raises(ValueError, match="command cannot be empty"):
            command_execution_bridge("")

    def test_none_command_raises(self):
        with pytest.raises(ValueError, match="command cannot be empty"):
            command_execution_bridge(None)

    def test_whitespace_command_raises(self):
        with pytest.raises(ValueError, match="command cannot be empty"):
            command_execution_bridge("   ")

    def test_returns_callable(self):
        bridge = command_execution_bridge("echo hello")
        assert callable(bridge)

    @patch("orch_bridges.subprocess.run")
    def test_successful_command(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="output line\n",
            stderr="",
        )
        bridge = command_execution_bridge("echo hello")
        run = _make_run()
        result = bridge(run)
        assert result.status == "done"
        assert "command succeeded" in result.note
        mock_run.assert_called_once()

    @patch("orch_bridges.subprocess.run")
    def test_failed_command_raises(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("POE_WORKSPACE", str(tmp_path))
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="error happened\n",
        )
        bridge = command_execution_bridge("false")
        with pytest.raises(ExecutionBridgeError, match="command failed"):
            bridge(_make_run())


# ---------------------------------------------------------------------------
# _validation_trace_event
# ---------------------------------------------------------------------------

class TestValidationTraceEvent:
    def test_basic_event(self):
        event = _validation_trace_event("my-bridge", status="done", passed=True)
        assert event["bridge"] == "my-bridge"
        assert event["status"] == "done"
        assert event["passed"] is True
        assert "note" not in event
        assert "error" not in event

    def test_with_note_and_error(self):
        event = _validation_trace_event("b", status="blocked", passed=False, note="oops", error="Err")
        assert event["note"] == "oops"
        assert event["error"] == "Err"


# ---------------------------------------------------------------------------
# _validation_bridge_name
# ---------------------------------------------------------------------------

class TestValidationBridgeName:
    def test_explicit_name(self):
        bridge = MagicMock()
        bridge.__orch_validation_name__ = "explicit"
        assert _validation_bridge_name(bridge, 0) == "explicit"

    def test_function_name_fallback(self):
        def my_validator(run, execution):
            pass
        assert _validation_bridge_name(my_validator, 0) == "my_validator"

    def test_lambda_uses_index(self):
        bridge = lambda run, execution: None
        assert _validation_bridge_name(bridge, 3) == "bridge-3"

    def test_no_name_uses_index(self):
        bridge = MagicMock(spec=[])
        assert _validation_bridge_name(bridge, 5) == "bridge-5"


# ---------------------------------------------------------------------------
# chain_validation_bridges
# ---------------------------------------------------------------------------

class TestChainValidationBridges:
    def test_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            chain_validation_bridges()

    def test_all_none_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            chain_validation_bridges(None, None)

    def test_single_passing_bridge(self):
        def good(run, execution):
            return ValidationResult(status="done", passed=True, note="ok")
        chain = chain_validation_bridges(good)
        result = chain(_make_run(), _make_execution())
        assert result.passed is True
        assert result.status == "done"

    def test_first_failure_short_circuits(self):
        def fail(run, execution):
            return ValidationResult(status="blocked", passed=False, note="bad")
        def should_not_run(run, execution):
            raise AssertionError("should not be called")
        chain = chain_validation_bridges(fail, should_not_run)
        result = chain(_make_run(), _make_execution())
        assert result.passed is False
        assert result.status == "blocked"

    def test_exception_in_bridge_returns_blocked(self):
        def explode(run, execution):
            raise RuntimeError("boom")
        chain = chain_validation_bridges(explode)
        result = chain(_make_run(), _make_execution())
        assert result.status == "blocked"
        assert result.passed is False
        assert "boom" in result.note

    def test_done_not_passed_returns_blocked(self):
        def done_no_pass(run, execution):
            return ValidationResult(status="done", passed=False, note="nope")
        chain = chain_validation_bridges(done_no_pass)
        result = chain(_make_run(), _make_execution())
        assert result.status == "blocked"
        assert result.passed is False


# ---------------------------------------------------------------------------
# _default_validation_bridge
# ---------------------------------------------------------------------------

class TestDefaultValidationBridge:
    def test_done_passes(self):
        result = _default_validation_bridge(
            _make_run(),
            _make_execution(status="done", note="great"),
        )
        assert result.passed is True
        assert result.status == "done"

    def test_blocked_fails(self):
        result = _default_validation_bridge(
            _make_run(),
            _make_execution(status="blocked", note="problem"),
        )
        assert result.passed is False

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError, match="invalid execution status"):
            _default_validation_bridge(
                _make_run(),
                _make_execution(status="bogus"),
            )


# ---------------------------------------------------------------------------
# _default_execution_bridge
# ---------------------------------------------------------------------------

class TestDefaultExecutionBridge:
    def test_returns_done(self):
        result = _default_execution_bridge(_make_run(note="test note"))
        assert result.status == "done"
        assert "test note" in result.note

    def test_returns_done_no_note(self):
        result = _default_execution_bridge(_make_run(note=None))
        assert result.status == "done"
        assert "No execution bridge" in result.note


# ---------------------------------------------------------------------------
# _merge_notes
# ---------------------------------------------------------------------------

class TestMergeNotes:
    def test_merges_multiple(self):
        assert _merge_notes("a", "b", "c") == "a; b; c"

    def test_skips_none(self):
        assert _merge_notes("a", None, "b") == "a; b"

    def test_skips_empty(self):
        assert _merge_notes("a", "", "b") == "a; b"

    def test_all_none_returns_none(self):
        assert _merge_notes(None, None) is None

    def test_strips_whitespace(self):
        assert _merge_notes("  a  ", "  b  ") == "a; b"


# ---------------------------------------------------------------------------
# artifact_validation_bridge — construction errors
# ---------------------------------------------------------------------------

class TestArtifactValidationBridge:
    def test_empty_paths_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            artifact_validation_bridge([])

    def test_only_empty_strings_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            artifact_validation_bridge(["", "  "])

    def test_returns_callable(self):
        bridge = artifact_validation_bridge(["result.json"])
        assert callable(bridge)


# ---------------------------------------------------------------------------
# ExecutionBridgeError
# ---------------------------------------------------------------------------

class TestExecutionBridgeError:
    def test_is_runtime_error(self):
        exc = ExecutionBridgeError("fail")
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "fail"
