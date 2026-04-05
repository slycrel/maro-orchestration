"""Execution and validation bridge implementations for Poe orchestration.

Extracted from orch.py — imports from orch_items but not from orch.py (no circular deps).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from shlex import quote
from typing import Any, List, Optional, Tuple

from orch_items import (
    RUN_OUTCOMES,
    WORKER_NAME_RE,
    X_CAPTURE_AUTH_MARKERS,
    X_CAPTURE_RATE_LIMIT_MARKERS,
    ExecutionBridge,
    ExecutionResult,
    RunRecord,
    ValidationBridge,
    ValidationResult,
    _load_run_records,
    _next_attempt,
    _run_artifact_root,
    now_utc_iso,
    orch_root,
    output_root,
    runs_root,
    validation_summary_path,
    workers_root,
)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ExecutionBridgeError(RuntimeError):
    """Raised when a concrete execution backend fails."""


# ---------------------------------------------------------------------------
# Validation trace helpers
# ---------------------------------------------------------------------------

def _validation_trace_event(
    bridge_name: str,
    *,
    status: str,
    passed: bool,
    note: Optional[str] = None,
    error: Optional[str] = None,
) -> dict:
    payload: dict[str, Any] = {
        "bridge": bridge_name,
        "status": status,
        "passed": passed,
    }
    if note:
        payload["note"] = note
    if error:
        payload["error"] = error
    return payload


def named_validation_bridge(name: str, bridge: ValidationBridge) -> ValidationBridge:
    clean_name = str(name or "").strip()
    if not clean_name:
        raise ValueError("validation bridge name cannot be empty")

    def _wrapped(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        try:
            result = bridge(run, execution)
        except Exception as exc:
            setattr(
                _wrapped,
                "_last_trace",
                [
                    _validation_trace_event(
                        clean_name,
                        status="blocked",
                        passed=False,
                        note=f"validation bridge failed: {exc}",
                        error=type(exc).__name__,
                    )
                ],
            )
            raise

        setattr(
            _wrapped,
            "_last_trace",
            [
                _validation_trace_event(
                    clean_name,
                    status=result.status,
                    passed=result.passed,
                    note=result.note,
                )
            ],
        )
        return result

    setattr(_wrapped, "__orch_validation_name__", clean_name)
    setattr(_wrapped, "_last_trace", [])
    return _wrapped


# ---------------------------------------------------------------------------
# Worker session spec and coercion helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WorkerSessionSpec:
    command: str
    payload_name: str = "worker-payload.json"
    result_name: str = "worker-result.json"
    working_directory: Optional[str] = None
    environment: dict = field(default_factory=dict)
    timeout_seconds: Optional[float] = None


def _ensure_nonempty_artifact_name(raw: object, default: str) -> str:
    value = str(raw or "").strip()
    return value if value else default


def _coerce_session_file_name(raw: object, *, default: str, field_name: str) -> str:
    value = _ensure_nonempty_artifact_name(raw, default)
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{field_name} must be a relative path: {value}")
    normalized = path.as_posix()
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError(f"{field_name} must not contain path traversal: {value}")
    return normalized


def _coerce_session_directory_name(raw: object, *, field_name: str) -> Optional[str]:
    value = _ensure_nonempty_artifact_name(raw, "")
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{field_name} must be a relative path: {value}")
    normalized = path.as_posix().strip("/")
    if not normalized or normalized == ".":
        return None
    if any(part == ".." for part in normalized.split("/")):
        raise ValueError(f"{field_name} must not contain path traversal: {value}")
    return normalized


def _coerce_env_map(raw: object, *, worker: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"worker session env for {worker} must be an object")
    out: dict = {}
    for key, value in raw.items():
        key_text = str(key).strip()
        if not key_text:
            raise ValueError(f"worker session env for {worker} must use non-empty keys")
        out[key_text] = str(value)
    return out


def _coerce_positive_timeout(raw: object, *, field_name: str) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"{field_name} must be a positive number: {raw}")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number: {raw}") from exc
    if value <= 0:
        raise ValueError(f"{field_name} must be greater than zero")
    return value


def _load_worker_session_manifest(path: Path) -> WorkerSessionSpec:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, str):
        command = data.strip()
        if not command:
            raise ValueError(f"invalid worker session manifest: {path} does not define a command")
        return WorkerSessionSpec(command=command)
    if not isinstance(data, dict):
        raise ValueError(f"invalid worker session manifest format in {path}")

    raw_command = data.get("command")
    if raw_command is None:
        raise ValueError(f"invalid worker session manifest format in {path}: missing 'command'")
    if isinstance(raw_command, (list, tuple)):
        command_parts = [str(part).strip() for part in raw_command]
        if not command_parts or any(not part for part in command_parts):
            raise ValueError(f"invalid worker session command in {path}")
        command = " ".join(quote(part) for part in command_parts)
    else:
        command = str(raw_command).strip()
    if not command:
        raise ValueError(f"invalid worker session manifest format in {path}: missing 'command'")

    payload_name = _coerce_session_file_name(
        data.get("payload_name"),
        default="worker-payload.json",
        field_name="payload_name",
    )
    result_name = _coerce_session_file_name(
        data.get("result_name"),
        default="worker-result.json",
        field_name="result_name",
    )
    _raw_wd = data.get("working_directory") or data.get("working_dir") or data.get("cwd")
    working_directory = _coerce_session_directory_name(
        _raw_wd,
        field_name="working_directory",
    )
    worker_name = path.stem if path.name else "worker_session"
    environment = _coerce_env_map(data.get("environment"), worker=worker_name)
    timeout_seconds = _coerce_positive_timeout(data.get("timeout_seconds"), field_name="timeout_seconds")
    return WorkerSessionSpec(
        command=command,
        payload_name=payload_name,
        result_name=result_name,
        working_directory=working_directory,
        environment=environment,
        timeout_seconds=timeout_seconds,
    )


# ---------------------------------------------------------------------------
# Artifact path and result parsing helpers
# ---------------------------------------------------------------------------

def _coerce_artifact_path(raw: Optional[str], *, default: Optional[str]) -> str:
    if raw is None:
        if default is None:
            raise ExecutionBridgeError("session result must include a valid artifact_path")
        return default

    artifact = str(raw).strip()
    if not artifact:
        if default is None:
            raise ExecutionBridgeError("session result must include a valid artifact_path")
        return default

    root = orch_root().resolve()
    candidate = Path(artifact)
    if candidate.is_absolute():
        candidate = candidate.resolve()
    else:
        candidate = (root / candidate).resolve()

    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ExecutionBridgeError(f"session result artifact_path must be under orchestration root: {candidate}") from exc

    relative_str = str(relative)
    if relative_str in {"", "."}:
        raise ExecutionBridgeError(f"session result artifact_path must be under orchestration root, not root: {candidate}")
    return relative_str


def _extract_session_result_from_text(raw: str) -> Optional[dict]:
    lines = [part.strip() for part in (raw or "").splitlines() if part.strip()]
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _extract_json_result(raw: str) -> Optional[dict]:
    if not raw.strip():
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = _extract_session_result_from_text(raw)
    if isinstance(payload, dict):
        return payload
    return None


def _coerce_validation_payload(raw: dict, *, run: RunRecord, execution: ExecutionResult) -> ValidationResult:
    status = (raw.get("status") or "").lower().strip()
    if status not in RUN_OUTCOMES:
        raise ValueError(f"invalid review status: {status}")

    note = raw.get("note")
    if isinstance(note, str) and note.strip():
        note_text = note.strip()
    else:
        note_text = f"review result for {run.run_id}"

    passed: bool
    if "passed" in raw:
        passed_raw = raw.get("passed")
        if not isinstance(passed_raw, bool):
            raise ValueError("review payload field 'passed' must be a boolean")
        passed = passed_raw
    else:
        passed = status == "done"

    return ValidationResult(status=status, passed=passed, note=note_text)


# ---------------------------------------------------------------------------
# Salvage index helpers
# ---------------------------------------------------------------------------

def _run_status_summary_record_path() -> Path:
    p = output_root() / "x-capture"
    p.mkdir(parents=True, exist_ok=True)
    return p / "salvage-index.jsonl"


def _append_jsonl_record(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


def _read_jsonl_records(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _resolve_review_artifact_root(run: RunRecord, artifact_path: Optional[str]) -> Path:
    if not artifact_path:
        return _run_artifact_root(run)
    relative = _coerce_artifact_path(artifact_path, default=str(_run_artifact_root(run).relative_to(orch_root())))
    return orch_root() / relative


def _validation_bridge_name(bridge: ValidationBridge, fallback_index: int) -> str:
    explicit = getattr(bridge, "__orch_validation_name__", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    bridge_name = getattr(bridge, "__name__", None)
    if isinstance(bridge_name, str) and bridge_name and bridge_name != "<lambda>":
        return bridge_name
    return f"bridge-{fallback_index}"


def _salvage_match_kinds(run: RunRecord) -> List[str]:
    if not run.artifact_path:
        return []
    salvage_path = orch_root() / run.artifact_path / "x-capture-salvage.json"
    if not salvage_path.exists() or not salvage_path.is_file():
        return []
    try:
        payload = json.loads(salvage_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    matches = payload.get("matches")
    if not isinstance(matches, list):
        return []

    out: List[str] = []
    for row in matches:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip().lower()
        if kind:
            out.append(kind)
    return out


def _repeated_salvage_streak(run: RunRecord, *, kind: str, max_attempts: int = 12) -> int:
    target = str(kind or "").strip().lower()
    if not target:
        return 0

    prior = []
    for record in _load_run_records():
        if record.project != run.project or record.index != run.index:
            continue
        if record.attempt >= run.attempt:
            continue
        prior.append(record)

    prior.sort(key=lambda record: record.attempt, reverse=True)
    streak = 0
    for record in prior[:max_attempts]:
        kinds = _salvage_match_kinds(record)
        if target in kinds:
            streak += 1
            continue
        break
    return streak


# ---------------------------------------------------------------------------
# Worker session resolution
# ---------------------------------------------------------------------------

def resolve_worker_session_script(
    worker: str,
    *,
    worker_root: Optional[Path] = None,
) -> Optional[Path]:
    spec = resolve_worker_session_spec(worker, worker_root=worker_root)
    if spec is None:
        return None
    if Path(spec.command).is_file():
        return Path(spec.command)
    return None


def resolve_worker_session_spec(
    worker: str,
    *,
    worker_root: Optional[Path] = None,
) -> Optional[WorkerSessionSpec]:
    if not worker or not worker.strip():
        return None

    raw = worker.strip()
    root = (worker_root or workers_root()).resolve()

    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        candidate_path = candidate
    else:
        candidate_path = orch_root() / candidate
    if candidate_path.exists():
        if not candidate_path.is_file():
            raise ExecutionBridgeError(f"worker session source must be a file: {candidate_path}")
        if candidate_path.suffix == ".json":
            return _load_worker_session_manifest(candidate_path)
        return WorkerSessionSpec(command=str(candidate_path.resolve()))

    if not WORKER_NAME_RE.match(raw):
        return None

    for spec in (raw, f"{raw}.sh"):
        script = root / spec
        if script.exists():
            if not script.is_file():
                raise ExecutionBridgeError(f"worker session source must be a file: {script}")
            if script.suffix == ".json":
                return _load_worker_session_manifest(script)
            return WorkerSessionSpec(command=str(script.resolve()))

    manifest = root / f"{raw}.json"
    if manifest.exists():
        if not manifest.is_file():
            raise ExecutionBridgeError(f"worker session source must be a file: {manifest}")
        return _load_worker_session_manifest(manifest)

    work_dir = root / raw
    if work_dir.exists() and work_dir.is_dir():
        for manifest_name in ("worker-session.json", "session.json", "manifest.json", "config.json", "run.json"):
            manifest_path = work_dir / manifest_name
            if manifest_path.exists() and manifest_path.is_file():
                return _load_worker_session_manifest(manifest_path)

        fallback = work_dir / "run.sh"
        if fallback.exists():
            if not fallback.is_file():
                raise ExecutionBridgeError(f"worker session source must be a file: {fallback}")
            return WorkerSessionSpec(command=str(fallback.resolve()))

    return None


# ---------------------------------------------------------------------------
# Execution bridges
# ---------------------------------------------------------------------------

def command_execution_bridge(command: str) -> ExecutionBridge:
    if not command or not command.strip():
        raise ValueError("command cannot be empty")

    def _execute(run: RunRecord) -> ExecutionResult:
        artifact_dir = _run_artifact_root(run)
        stdout_path = artifact_dir / "stdout.log"
        stderr_path = artifact_dir / "stderr.log"

        env = os.environ.copy()
        env.update(
            {
                "ORCH_RUN_ID": run.run_id,
                "ORCH_PROJECT": run.project,
                "ORCH_ITEM_INDEX": str(run.index),
                "ORCH_ITEM_TEXT": run.text,
                "ORCH_WORKER": run.worker,
                "ORCH_SOURCE": run.source,
                "ORCH_ROOT": str(orch_root()),
                "ORCH_RUN_ARTIFACT_DIR": str(artifact_dir),
            }
        )

        proc = subprocess.run(
            command,
            shell=True,
            cwd=orch_root(),
            env=env,
            capture_output=True,
            text=True,
        )
        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        artifact_path = str(artifact_dir.relative_to(orch_root()))
        if proc.returncode == 0:
            note = f"command succeeded: {command}"
            if proc.stdout.strip():
                note = f"{note}; stdout={proc.stdout.strip().splitlines()[-1]}"
            return ExecutionResult(status="done", note=note, artifact_path=artifact_path)

        note = f"command failed ({proc.returncode}): {command}"
        if proc.stderr.strip():
            note = f"{note}; stderr={proc.stderr.strip().splitlines()[-1]}"
        raise ExecutionBridgeError(note)

    return _execute


def worker_session_bridge(
    worker: str,
    *,
    worker_root: Optional[Path] = None,
    timeout_seconds: Optional[float] = None,
    payload_name: str = "worker-payload.json",
    result_name: str = "worker-result.json",
) -> ExecutionBridge:
    if not worker or not worker.strip():
        raise ValueError("worker cannot be empty")

    spec = resolve_worker_session_spec(worker, worker_root=worker_root)
    if spec is None:
        raise ValueError(f"no worker session script found for {worker!r}")

    resolved_payload_name = payload_name
    resolved_result_name = result_name
    if spec.payload_name:
        resolved_payload_name = spec.payload_name
    if spec.result_name:
        resolved_result_name = spec.result_name
    resolved_timeout_seconds = timeout_seconds if timeout_seconds is not None else spec.timeout_seconds

    return session_execution_bridge(
        spec.command,
        timeout_seconds=resolved_timeout_seconds,
        payload_name=resolved_payload_name,
        result_name=resolved_result_name,
        working_directory=spec.working_directory,
        extra_env=spec.environment,
    )


def session_execution_bridge(
    session_command: str,
    *,
    timeout_seconds: Optional[float] = None,
    payload_name: str = "session-payload.json",
    result_name: str = "session-result.json",
    working_directory: Optional[str] = None,
    extra_env: Optional[dict] = None,
) -> ExecutionBridge:
    if not session_command or not session_command.strip():
        raise ValueError("session_command cannot be empty")

    resolved_payload_name = _coerce_session_file_name(
        payload_name,
        default="session-payload.json",
        field_name="payload_name",
    )
    resolved_result_name = _coerce_session_file_name(
        result_name,
        default="session-result.json",
        field_name="result_name",
    )

    def _coerce_result_payload(raw: dict, *, default_artifact_path: str, run_id: str) -> ExecutionResult:
        status = (raw.get("status") or "").lower().strip()
        if status not in RUN_OUTCOMES:
            raise ExecutionBridgeError(f"invalid session result status: {status}")

        artifact_path = _coerce_artifact_path(
            raw.get("artifact_path"),
            default=default_artifact_path,
        )

        return ExecutionResult(
            status=status,
            note=raw.get("note") or f"session result for {run_id}",
            artifact_path=artifact_path,
        )

    def _read_result_payload(raw_text: str, *, default_artifact_path: str, run_id: str) -> Optional[ExecutionResult]:
        if not raw_text.strip():
            return None
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            raw = _extract_session_result_from_text(raw_text)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ExecutionBridgeError("session result payload must be an object")
        return _coerce_result_payload(raw, default_artifact_path=default_artifact_path, run_id=run_id)

    def _parse_result_file_payload(run: RunRecord, artifact_dir: Path, artifact_path: str) -> Optional[ExecutionResult]:
        result_path = artifact_dir / resolved_result_name
        if not result_path.exists():
            return None
        result_raw = result_path.read_text(encoding="utf-8")
        try:
            parsed = json.loads(result_raw)
        except json.JSONDecodeError as exc:
            raise ExecutionBridgeError(f"invalid session result json: {result_path}") from exc
        if not isinstance(parsed, dict):
            raise ExecutionBridgeError(f"session result payload must be an object: {result_path}")
        return _coerce_result_payload(parsed, default_artifact_path=artifact_path, run_id=run.run_id)

    def _execute(run: RunRecord) -> ExecutionResult:
        artifact_dir = _run_artifact_root(run)
        stdout_path = artifact_dir / "session-stdout.log"
        stderr_path = artifact_dir / "session-stderr.log"

        payload_path = artifact_dir / resolved_payload_name
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        result_path = artifact_dir / resolved_result_name
        result_path.parent.mkdir(parents=True, exist_ok=True)
        payload_path.write_text(
            json.dumps(
                {
                    "run_id": run.run_id,
                    "attempt": run.attempt,
                    "project": run.project,
                    "index": run.index,
                    "text": run.text,
                    "status": run.status,
                    "source": run.source,
                    "worker": run.worker,
                    "artifact_path": str(artifact_dir.relative_to(orch_root())),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        default_artifact_path = str(artifact_dir.relative_to(orch_root()))
        env = os.environ.copy()
        env.update(
            {
                "ORCH_RUN_ID": run.run_id,
                "ORCH_PROJECT": run.project,
                "ORCH_ITEM_INDEX": str(run.index),
                "ORCH_ITEM_TEXT": run.text,
                "ORCH_WORKER": run.worker,
                "ORCH_ATTEMPT": str(run.attempt),
                "ORCH_SOURCE": run.source,
                "ORCH_ROOT": str(orch_root()),
                "ORCH_RUN_ARTIFACT_DIR": str(artifact_dir),
                "ORCH_RUN_ARTIFACT_PATH": default_artifact_path,
                "ORCH_SESSION_PAYLOAD": str(payload_path),
                "ORCH_SESSION_RESULT_PATH": str(result_path),
            }
        )
        if extra_env:
            env.update({str(k): str(v) for k, v in extra_env.items()})

        cwd = orch_root()
        if working_directory:
            try:
                resolved_working_directory = (orch_root() / working_directory).resolve()
            except RuntimeError as exc:
                raise ExecutionBridgeError(
                    f"invalid worker working directory: {working_directory}: {exc}"
                ) from exc
            if not resolved_working_directory.exists():
                raise ExecutionBridgeError(f"worker working directory does not exist: {resolved_working_directory}")
            if not resolved_working_directory.is_dir():
                raise ExecutionBridgeError(f"worker working directory must be a directory: {resolved_working_directory}")
            cwd = resolved_working_directory
            env["ORCH_SESSION_WORKING_DIR"] = str(resolved_working_directory)

        try:
            proc = subprocess.run(
                session_command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExecutionBridgeError(f"session timed out after {timeout_seconds}s: {session_command}") from exc
        except Exception as exc:
            raise ExecutionBridgeError(f"session execution failed: {session_command}: {exc}") from exc

        stdout_path.write_text(proc.stdout or "", encoding="utf-8")
        stderr_path.write_text(proc.stderr or "", encoding="utf-8")

        payload_result = _parse_result_file_payload(run, artifact_dir, default_artifact_path)
        if payload_result:
            return payload_result
        stdout_result = _read_result_payload(proc.stdout or "", default_artifact_path=default_artifact_path, run_id=run.run_id)
        if stdout_result:
            return stdout_result

        if proc.returncode == 0:
            note = f"session succeeded: {session_command}"
            if proc.stdout.strip():
                note = f"{note}; stdout={proc.stdout.strip().splitlines()[-1]}"
            return ExecutionResult(status="done", note=note, artifact_path=default_artifact_path)

        note = f"session failed ({proc.returncode}): {session_command}"
        if proc.stderr.strip():
            note = f"{note}; stderr={proc.stderr.strip().splitlines()[-1]}"
        raise ExecutionBridgeError(note)

    return _execute


def _default_execution_bridge(run: RunRecord) -> ExecutionResult:
    return ExecutionResult(
        status="done",
        note=run.note or "No execution bridge configured; marking as complete for test/sync flow.",
    )


# ---------------------------------------------------------------------------
# Artifact signature helpers (used by progress bridge)
# ---------------------------------------------------------------------------

def _run_artifact_signature(run: RunRecord) -> Optional[List[Tuple[str, int, str]]]:
    base = _run_artifact_root(run)
    if not base.exists() or not base.is_dir():
        return None

    entries: List[Tuple[str, int, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(base))
        if rel in {"stdout.log", "stderr.log", "session-stdout.log", "session-stderr.log", "validation-summary.json"}:
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest() if data else ""
        entries.append((rel, path.stat().st_size, digest))
    return sorted(entries)


def _matching_attempt_signatures(run: RunRecord, *, max_attempts: int = 4) -> List[Tuple[int, List[Tuple[str, int, str]]]]:
    signatures = []
    for previous in _load_run_records():
        if previous.project != run.project:
            continue
        if previous.index != run.index:
            continue
        if previous.attempt >= run.attempt:
            continue
        if previous.status not in {"done", "blocked", "running"}:
            continue
        sig = _run_artifact_signature(previous)
        signatures.append((previous.attempt, sig))

    signatures.sort(key=lambda row: row[0], reverse=True)
    return signatures[:max_attempts]


def _validation_status_for_record(record: RunRecord) -> Optional[str]:
    path = validation_summary_path(record)
    if not path:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    validation = payload.get("validation")
    if not isinstance(validation, dict):
        return None
    status = str(validation.get("status") or "").strip().lower()
    if status not in RUN_OUTCOMES:
        return None
    return status


def _retry_streak_for_item(run: RunRecord, *, max_attempts: int = 12) -> int:
    previous_attempts = [
        record
        for record in _load_run_records()
        if record.project == run.project and record.index == run.index and record.attempt < run.attempt
    ]
    if not previous_attempts:
        return 1

    streak = 1
    for record in sorted(previous_attempts, key=lambda row: row.attempt, reverse=True)[:max_attempts]:
        status = _validation_status_for_record(record)
        if status == "retry":
            streak += 1
            continue
        break
    return streak


# ---------------------------------------------------------------------------
# Validation bridges
# ---------------------------------------------------------------------------

def artifact_progress_validation_bridge(
    *,
    history_size: int = 2,
    max_retry_attempts: int = 3,
) -> ValidationBridge:
    if history_size < 1:
        raise ValueError("history_size must be >= 1")

    def _validate(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        status = execution.status.lower().strip()
        if status not in RUN_OUTCOMES:
            raise ValueError(f"invalid execution status: {execution.status}")
        if status != "done":
            return ValidationResult(status=status, passed=False, note=execution.note or "execution did not complete")

        current_signature = _run_artifact_signature(run)
        previous = _matching_attempt_signatures(run, max_attempts=history_size - 1)
        if not previous:
            return ValidationResult(status="done", passed=True, note=execution.note)

        stale_count = 1
        for _attempt, signature in previous:
            if signature == current_signature:
                stale_count += 1
            else:
                break

        if stale_count >= history_size:
            if run.attempt < max_retry_attempts:
                return ValidationResult(
                    status="retry",
                    passed=False,
                    note=f"no artifact progress detected across {stale_count} attempts for {run.project}:{run.index}",
                )
            return ValidationResult(
                status="blocked",
                passed=False,
                note=f"blocked for repeated no-progress attempts on {run.project}:{run.index}",
            )

        return ValidationResult(status="done", passed=True, note=execution.note)

    return _validate


def x_capture_salvage_validation_bridge(*, max_auth_retries: int = 3) -> ValidationBridge:
    if max_auth_retries < 1:
        raise ValueError("max_auth_retries must be >= 1")
    auth_markers = [marker.lower() for marker in X_CAPTURE_AUTH_MARKERS]
    rate_markers = [marker.lower() for marker in X_CAPTURE_RATE_LIMIT_MARKERS]

    def _scan_message(message: str) -> List[Tuple[str, str, str]]:
        lower = message.lower()
        findings = []
        if any(marker in lower for marker in rate_markers):
            findings.append(("retry", "rate-limit or quota marker detected", "rate-limit"))
        if any(marker in lower for marker in auth_markers):
            findings.append(("retry", "auth/session marker detected", "auth"))
        return findings

    def _find_hit(
        run: RunRecord,
        execution: ExecutionResult,
        messages: List[Tuple[str, str, str]],
    ) -> ValidationResult:
        if not messages:
            return ValidationResult(status="done", passed=True, note=execution.note)

        first_status, first_detail, _first_kind = messages[0]
        resolved_status = first_status
        resolved_detail = first_detail
        if _first_kind == "auth":
            prior_auth_hits = _repeated_salvage_streak(run, kind="auth")
            auth_streak = prior_auth_hits + 1
            if auth_streak >= max_auth_retries:
                resolved_status = "blocked"
                resolved_detail = f"auth/session marker detected repeatedly ({auth_streak} attempts)"

        artifact_path = run.artifact_path
        if execution.artifact_path:
            artifact_path = execution.artifact_path

        payload = {
            "run_id": run.run_id,
            "project": run.project,
            "index": run.index,
            "artifact_path": artifact_path,
            "status": execution.status,
            "matches": [
                {"status": status, "detail": detail, "kind": kind}
                for status, detail, kind in messages
            ],
            "note": execution.note,
            "generated_at": now_utc_iso(),
        }
        if artifact_path:
            salvage_path = (orch_root() / artifact_path) / "x-capture-salvage.json"
            salvage_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            _append_jsonl_record(
                _run_status_summary_record_path(),
                {
                    "generated_at": payload["generated_at"],
                    "run_id": payload["run_id"],
                    "project": payload["project"],
                    "index": payload["index"],
                    "status": payload["status"],
                    "artifact_path": payload["artifact_path"],
                    "matches": payload["matches"],
                    "note": payload["note"],
                },
            )
            detail = resolved_detail
            if resolved_status == first_status and payload["matches"]:
                detail = payload["matches"][0]["detail"]
            return ValidationResult(
                status=resolved_status,
                passed=False,
                note=f"x capture salvage hint: {detail}; evidence={salvage_path.relative_to(orch_root())}",
            )
        return ValidationResult(
            status=resolved_status,
            passed=False,
            note=f"x capture salvage hint: {resolved_detail}",
        )

    def _validate(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        if execution.status != "done":
            return ValidationResult(status=execution.status, passed=False, note=execution.note or "execution did not complete")

        try:
            artifact_root = _resolve_review_artifact_root(run, execution.artifact_path)
        except Exception:
            artifact_root = _run_artifact_root(run)

        candidates = [
            artifact_root / "stdout.log",
            artifact_root / "stderr.log",
            artifact_root / "session-stdout.log",
            artifact_root / "session-stderr.log",
            artifact_root / "review" / "stdout.log",
            artifact_root / "review" / "stderr.log",
        ]

        matches: List[Tuple[str, str, str]] = []
        for candidate in candidates:
            if not candidate.exists() or not candidate.is_file():
                continue
            matches.extend(_scan_message(candidate.read_text(encoding="utf-8", errors="ignore")))

        matches.extend(_scan_message(execution.note or ""))
        return _find_hit(run, execution, messages=matches)

    return _validate


def artifact_validation_bridge(required_paths: List[str], *, nonempty: bool = False) -> ValidationBridge:
    cleaned = [p.strip() for p in required_paths if p and p.strip()]
    if not cleaned:
        raise ValueError("required_paths cannot be empty")

    def _validate(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        status = execution.status.lower().strip()
        if status not in RUN_OUTCOMES:
            raise ValueError(f"invalid execution status: {execution.status}")
        if status != "done":
            return ValidationResult(
                status=status,
                passed=False,
                note=execution.note or "execution did not complete successfully",
            )

        artifact_root = _run_artifact_root(run)
        if execution.artifact_path:
            try:
                artifact_root = _resolve_review_artifact_root(run, execution.artifact_path)
            except Exception:
                return ValidationResult(status="blocked", passed=False, note="invalid artifact_path in execution result")

        missing = []
        empty = []
        for rel in cleaned:
            target = artifact_root / rel
            if not target.exists():
                missing.append(rel)
                continue
            if nonempty and target.is_file() and target.stat().st_size == 0:
                empty.append(rel)

        if missing or empty:
            parts = []
            if missing:
                parts.append(f"missing artifacts: {', '.join(missing)}")
            if empty:
                parts.append(f"empty artifacts: {', '.join(empty)}")
            return ValidationResult(status="blocked", passed=False, note="; ".join(parts))

        note = execution.note or f"validated artifacts: {', '.join(cleaned)}"
        return ValidationResult(status="done", passed=True, note=note)

    return _validate


def review_command_validation_bridge(command: str, *, timeout_seconds: Optional[float] = None) -> ValidationBridge:
    if not command or not command.strip():
        raise ValueError("command cannot be empty")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than zero")

    def _validate(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        status = execution.status.lower().strip()
        if status not in RUN_OUTCOMES:
            raise ValueError(f"invalid execution status: {execution.status}")
        if status != "done":
            return ValidationResult(
                status=status,
                passed=False,
                note=execution.note or "execution did not complete successfully",
            )

        try:
            artifact_root = _resolve_review_artifact_root(run, execution.artifact_path)
        except Exception:
            return ValidationResult(status="blocked", passed=False, note="invalid artifact_path in execution result")
        review_dir = artifact_root / "review"
        review_dir.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env.update(
            {
                "ORCH_RUN_ID": run.run_id,
                "ORCH_PROJECT": run.project,
                "ORCH_ITEM_INDEX": str(run.index),
                "ORCH_ITEM_TEXT": run.text,
                "ORCH_WORKER": run.worker,
                "ORCH_SOURCE": run.source,
                "ORCH_ROOT": str(orch_root()),
                "ORCH_RUN_ARTIFACT_DIR": str(artifact_root),
                "ORCH_REVIEW_ARTIFACT_DIR": str(review_dir),
            }
        )

        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=orch_root(),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return ValidationResult(status="blocked", passed=False, note=f"review timed out after {timeout_seconds}s: {command}: {exc}")
        (review_dir / "stdout.log").write_text(proc.stdout or "", encoding="utf-8")
        (review_dir / "stderr.log").write_text(proc.stderr or "", encoding="utf-8")

        verdict_paths = [
            review_dir / "result.json",
            review_dir / "review.json",
            review_dir / "verdict.json",
        ]
        for verdict_path in sorted(
            {p for p in review_dir.glob("*.json") if p.is_file()}
        ):
            if verdict_path not in verdict_paths:
                verdict_paths.append(verdict_path)
        for verdict_path in verdict_paths:
            if not verdict_path.exists():
                continue
            payload = _extract_json_result(verdict_path.read_text(encoding="utf-8", errors="ignore"))
            if payload is not None:
                try:
                    return _coerce_validation_payload(payload, run=run, execution=execution)
                except Exception as exc:
                    return ValidationResult(status="blocked", passed=False, note=f"invalid review payload: {exc}")

        payload = _extract_json_result(proc.stdout or "")
        if payload is not None:
            try:
                return _coerce_validation_payload(payload, run=run, execution=execution)
            except Exception as exc:
                return ValidationResult(status="blocked", passed=False, note=f"invalid review payload: {exc}")

        payload = _extract_json_result(proc.stderr or "")
        if payload is not None:
            try:
                return _coerce_validation_payload(payload, run=run, execution=execution)
            except Exception as exc:
                return ValidationResult(status="blocked", passed=False, note=f"invalid review payload: {exc}")

        if proc.returncode == 0:
            note = f"review passed: {command}"
            if proc.stdout.strip():
                note = f"{note}; stdout={proc.stdout.strip().splitlines()[-1]}"
            return ValidationResult(status="done", passed=True, note=note)

        note = f"review failed ({proc.returncode}): {command}"
        if proc.stderr.strip():
            note = f"{note}; stderr={proc.stderr.strip().splitlines()[-1]}"
        return ValidationResult(status="blocked", passed=False, note=note)


    return _validate


def chain_validation_bridges(*bridges: ValidationBridge) -> ValidationBridge:
    cleaned = [bridge for bridge in bridges if bridge is not None]
    if not cleaned:
        raise ValueError("at least one validation bridge is required")

    def _validate(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
        notes = []
        final_status = "done"
        trace = []
        for idx, bridge in enumerate(cleaned, start=1):
            bridge_name = _validation_bridge_name(bridge, idx)
            try:
                result = bridge(run, execution)
            except Exception as exc:
                failure_note = f"validation bridge failed: {exc}"
                trace.append(
                    _validation_trace_event(
                        bridge_name,
                        status="blocked",
                        passed=False,
                        note=failure_note,
                        error=type(exc).__name__,
                    )
                )
                setattr(_validate, "_last_trace", trace)
                return ValidationResult(status="blocked", passed=False, note=failure_note)

            if result.status not in RUN_OUTCOMES:
                trace.append(
                    _validation_trace_event(
                        bridge_name,
                        status="blocked",
                        passed=False,
                        note=f"invalid validation status: {result.status}",
                    )
                )
                setattr(_validate, "_last_trace", trace)
                return ValidationResult(
                    status="blocked",
                    passed=False,
                    note=f"invalid validation status: {result.status}",
                )

            trace.append(
                _validation_trace_event(
                    bridge_name,
                    status=result.status,
                    passed=result.passed,
                    note=result.note,
                )
            )
            if result.status == "done" and not result.passed:
                setattr(_validate, "_last_trace", trace)
                return ValidationResult(
                    status="blocked",
                    passed=False,
                    note=result.note or "validation bridge returned done without pass",
                )

            if result.note:
                notes.append(result.note)
            final_status = result.status
            if result.status != "done":
                setattr(_validate, "_last_trace", trace)
                return ValidationResult(status=result.status, passed=result.passed, note="; ".join(notes) if notes else result.note)
        setattr(_validate, "_last_trace", trace)
        return ValidationResult(status=final_status, passed=True, note="; ".join(notes) if notes else None)

    setattr(_validate, "_last_trace", [])
    return _validate


def _default_validation_bridge(run: RunRecord, execution: ExecutionResult) -> ValidationResult:
    status = execution.status.lower().strip()
    if status not in RUN_OUTCOMES:
        raise ValueError(f"invalid execution status: {execution.status}")
    return ValidationResult(
        status=status,
        passed=status == "done",
        note=execution.note or ("execution accepted" if status == "done" else "execution blocked"),
    )


def _merge_notes(*notes: Optional[str]) -> Optional[str]:
    chunks = [n.strip() for n in notes if n and n.strip()]
    if not chunks:
        return None
    return "; ".join(chunks)
