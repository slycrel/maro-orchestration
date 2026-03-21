import json
from pathlib import Path

import orch
import pytest


def _mkproj(tmp_path: Path, slug: str, content: str, priority: int = 0):
    p = tmp_path / "prototypes" / "poe-orchestration" / "projects" / slug
    p.mkdir(parents=True)
    (p / "NEXT.md").write_text(content, encoding="utf-8")
    (p / "PRIORITY").write_text(f"{priority}\n", encoding="utf-8")


def test_parse_edge_states(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "a", "- [ ] one\n- [~] two\n- [x] three\n- [!] four\n- [X] five\n")
    _, items = orch.parse_next("a")
    assert [i.state for i in items] == [" ", "~", "x", "!", "x"]


def test_nested_and_malformed(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "a", "- [] bad\n  - [ ] nested good\n- [ ] root good\n")
    _, items = orch.parse_next("a")
    assert len(items) == 2
    assert items[0].indent == 2


def test_global_next_prefers_priority_then_mtime(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "low", "- [ ] low\n", priority=1)
    _mkproj(tmp_path, "high", "- [ ] high\n", priority=10)
    slug, item = orch.select_global_next()
    assert slug == "high"
    assert item.text == "high"


def test_start_and_finalize_run(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    run = orch.run_once("demo", worker="tester", source="unit")
    assert run is not None
    assert run.status == "running"
    assert run.project == "demo"

    item = orch.get_item("demo", run.index)
    assert item.state == orch.STATE_DOING

    status = orch.write_operator_status()
    assert status["queue"]["doing"] == 1
    assert status["next"]["project"] == "demo"

    finished = orch.finalize_run(run.run_id, "done", note="unit verified")
    assert finished.status == "done"
    assert finished.note == "unit verified"
    assert finished.finished_at is not None

    item = orch.get_item("demo", run.index)
    assert item.state == orch.STATE_DONE

    status = orch.write_operator_status()
    assert status["queue"]["doing"] == 0
    assert status["queue"]["done"] == 1


def test_plan_project_and_next_items(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    plan = orch.plan_project("demo", "Add docs. Then wire smoke test. Then ship it.", max_steps=3)
    assert len(plan.steps) == 3
    assert plan.item_indices == [2, 3, 4]
    _, items = orch.parse_next("demo")
    assert items[2].text == plan.steps[0]
    assert items[-1].text == plan.steps[-1]


def test_run_tick_and_run_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    seen = []

    def executor(run):
        seen.append(run.index)
        return orch.ExecutionResult(status="done", note=f"executed {run.index}")

    def validator(run, execution):
        return orch.ValidationResult(status="done", passed=True, note=execution.note)

    tick = orch.run_tick("demo", execution=executor, validation=validator, worker="tester")
    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    assert tick.run.index == 0
    assert seen == [0]

    loop = orch.run_loop("demo", execution=executor, validation=validator, max_runs=3, worker="tester")
    assert len(loop) == 1
    assert loop[0].run.index == 1
    _, items = orch.parse_next("demo")
    assert items[0].state == orch.STATE_DONE
    assert items[1].state == orch.STATE_DONE


def test_validation_hook_can_block_or_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="blocked", note="blocked by policy"),
        validation=lambda run, execution: orch.ValidationResult(
            status="blocked",
            passed=False,
            note=execution.note,
        ),
    )
    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"

    loop = orch.run_loop(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="retry", note="retry later"),
        validation=lambda run, execution: orch.ValidationResult(status="retry", passed=False, note=execution.note),
        max_runs=3,
    )
    assert len(loop) == 1
    assert loop[0].validation.status == "retry"
    still = orch.load_run_record(loop[0].run.run_id)
    assert still.status == "running"


def test_run_once_can_resume_stale_running_item(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    first = orch.run_once("demo", worker="tester", source="unit")
    assert first is not None
    assert first.attempt == 1

    resumed = orch.run_once("demo", worker="tester", source="unit")
    assert resumed is not None
    assert resumed.attempt == 2
    assert resumed.index == first.index


def test_run_loop_continue_on_retry_creates_new_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    seen = []
    def validator(run, execution):
        seen.append(run.attempt)
        if run.attempt < 3:
            return orch.ValidationResult(status="retry", passed=False, note=f"retry attempt {run.attempt}")
        return orch.ValidationResult(status="done", passed=True, note="allow complete")

    loop = orch.run_loop(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note=f"ok {run.attempt}"),
        validation=validator,
        max_runs=4,
        continue_on_retry=True,
    )
    assert seen == [1, 2, 3]
    assert len(loop) == 3
    assert loop[-1].validation.status == "done"
    assert loop[-1].run.status == "done"


def test_artifact_progress_validation_bridge_detects_stale_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    run1 = orch.start_item("demo", worker="tester", source="unit")
    artifact_root = orch._run_artifact_root(run1)
    (artifact_root / "result.txt").write_text("same", encoding="utf-8")

    run2 = orch.start_item("demo", run1.index, worker="tester", source="unit", allow_running=True)
    artifact_root = orch._run_artifact_root(run2)
    (artifact_root / "result.txt").write_text("same", encoding="utf-8")

    run3 = orch.start_item("demo", run1.index, worker="tester", source="unit", allow_running=True)
    artifact_root = orch._run_artifact_root(run3)
    (artifact_root / "result.txt").write_text("same", encoding="utf-8")

    validator = orch.artifact_progress_validation_bridge(history_size=2, max_retry_attempts=3)
    result2 = validator(run2, orch.ExecutionResult(status="done", note="ok", artifact_path=run2.artifact_path))
    assert result2.status == "retry"

    result3 = validator(run3, orch.ExecutionResult(status="done", note="ok", artifact_path=run3.artifact_path))
    assert result3.status == "blocked"


def test_session_execution_bridge_parses_result_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.session_execution_bridge(
            'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
            '{"status":"done","note":"session complete","artifact_path":"output/runs/$ORCH_RUN_ID"}\n'
            "EOF\n"
        ),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"


def test_session_execution_bridge_parses_result_from_stdout(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.session_execution_bridge(
            'printf \'{"status":"done","note":"stdout result","artifact_path":"output/runs/$ORCH_RUN_ID"}\'',
        ),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"


def test_session_execution_bridge_blocks_invalid_artifact_path(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.session_execution_bridge(
            'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
            '{"status":"done","note":"bad artifact","artifact_path":"../../outside"}\n'
            "EOF\n",
        ),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"
    assert "under orchestration root" in (tick.run.note or "")


def test_worker_session_bridge_by_name(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    script = workers / "handle.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
        '{"status":"done","note":"named worker","artifact_path":"$ORCH_RUN_ARTIFACT_PATH"}\n'
        "EOF\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    tick = orch.run_tick(
        "demo",
        worker="handle",
        execution=orch.worker_session_bridge("handle"),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"


def test_worker_session_bridge_from_manifest_json(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    manifest = workers / "researcher.json"
    manifest.write_text(
        json.dumps(
            {
                "command": 'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
                '{"status":"done","note":"manifest worker","artifact_path":"$ORCH_RUN_ARTIFACT_PATH"}\n'
                "EOF\n",
                "payload_name": "researcher-payload.json",
                "result_name": "researcher-result.json",
            }
        ),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker="researcher",
        execution=orch.worker_session_bridge("researcher"),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    assert (artifact_root / "researcher-result.json").exists()
    assert not (artifact_root / "worker-result.json").exists()


def test_worker_session_bridge_manifest_command_list(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    manifest = workers / "list.json"
    manifest.write_text(
        json.dumps(
            {
                "command": ["bash", "-lc", 'printf "%s" "$ORCH_ITEM_TEXT" > "$ORCH_RUN_ARTIFACT_DIR/cmd.txt"'],
            }
        ),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker="list",
        execution=orch.worker_session_bridge("list"),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    assert (artifact_root / "cmd.txt").read_text(encoding="utf-8") == "first"


def test_worker_session_manifest_timeout_applies_without_cli_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    manifest = workers / "slow.json"
    manifest.write_text(
        json.dumps(
            {
                "command": "sleep 1",
                "timeout_seconds": 0.01,
            }
        ),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker="slow",
        execution=orch.worker_session_bridge("slow"),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"


def test_worker_session_bridge_manifest_supports_nested_artifacts_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    workers.mkdir(parents=True)
    manifest = workers / "nested.json"
    manifest.write_text(
        json.dumps(
            {
                "command": (
                    'cat > "$ORCH_SESSION_RESULT_PATH" <<EOF\n'
                    '{"status":"done","note":"token:$ORCH_WORKER_TOKEN","artifact_path":"$ORCH_RUN_ARTIFACT_PATH"}\n'
                    "EOF\n"
                ),
                "payload_name": "nested/payload.json",
                "result_name": "nested/result.json",
                "environment": {"ORCH_WORKER_TOKEN": "abc123"},
            }
        ),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker="nested",
        execution=orch.worker_session_bridge("nested"),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    assert tick.run.note and "token:abc123" in tick.run.note
    artifact_root = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    assert (artifact_root / "nested" / "payload.json").exists()
    assert (artifact_root / "nested" / "result.json").exists()


def test_worker_session_bridge_supports_working_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "poe-orchestration" / "workers"
    worker_dir = workers / "worker-dir"
    worker_dir.mkdir(parents=True, exist_ok=True)
    manifest = worker_dir / "runner.json"
    manifest.write_text(
        json.dumps(
                {
                    "command": 'printf "%s" "$ORCH_SESSION_WORKING_DIR" > "$ORCH_RUN_ARTIFACT_DIR/working_directory.txt"',
                    "working_directory": "workers/worker-dir",
                }
            ),
            encoding="utf-8",
        )

    tick = orch.run_tick(
        "demo",
        worker="runner",
        execution=orch.worker_session_bridge(str(manifest)),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    expected = str(worker_dir)
    assert (artifact_root / "working_directory.txt").read_text(encoding="utf-8") == expected


def test_worker_session_bridge_errors_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    with pytest.raises(ValueError):
        orch.worker_session_bridge("missing-has-no-script")


def test_review_command_validation_bridge_parses_json_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge("printf ok > \"$ORCH_RUN_ARTIFACT_DIR/result.txt\""),
        validation=orch.review_command_validation_bridge(
            'cat <<\"JSON\"\n'
            '{"status":"retry","note":"temporary captcha"}\n'
            'JSON',
        ),
    )

    assert tick is not None
    assert tick.validation.status == "retry"
    assert tick.run.status == "running"


def test_chain_validation_bridge_blocks_done_without_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    run = orch.start_item("demo", source="unit", worker="tester")
    bridge = orch.chain_validation_bridges(
        lambda _run, execution: orch.ValidationResult(status="done", passed=False, note="reviewer bug"),
    )

    result = bridge(run, orch.ExecutionResult(status="done", note="command ok"))
    assert result.status == "blocked"
    assert result.passed is False


def test_run_tick_blocks_validation_done_without_pass(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge("true"),
        validation=lambda _run, _execution: orch.ValidationResult(status="done", passed=False, note="reviewer failed"),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"


def test_review_command_validation_payload_can_report_done_not_passed(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.review_command_validation_bridge(
            'printf \'{"status":"done","passed":false,"note":"policy fail"}\' > "$ORCH_REVIEW_ARTIFACT_DIR/decision.json"',
        ),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"
    assert "policy fail" in (tick.validation.note or "")


def test_run_loop_stops_on_blocked_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    def validator(run, _execution):
        if run.index == 0:
            return orch.ValidationResult(status="blocked", passed=False, note="blocked by policy")
        return orch.ValidationResult(status="done", passed=True, note="continue")

    loop = orch.run_loop(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note=f"ok {run.index}"),
        validation=validator,
        max_runs=3,
    )
    assert len(loop) == 1
    assert loop[0].validation.status == "blocked"
    _, items = orch.parse_next("demo")
    assert items[0].state == orch.STATE_BLOCKED
    assert items[1].state == orch.STATE_TODO


def test_run_loop_continue_on_blocked_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    def validator(run, _execution):
        if run.index == 0:
            return orch.ValidationResult(status="blocked", passed=False, note="blocked by policy")
        return orch.ValidationResult(status="done", passed=True, note="continue")

    loop = orch.run_loop(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note=f"ok {run.index}"),
        validation=validator,
        max_runs=3,
        continue_on_blocked=True,
    )
    assert len(loop) == 2
    assert loop[0].validation.status == "blocked"
    assert loop[1].validation.status == "done"


def test_run_loop_respects_max_attempts_per_item(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    def validator(run, _execution):
        if run.attempt < 3:
            return orch.ValidationResult(status="retry", passed=False, note=f"retry {run.attempt}")
        return orch.ValidationResult(status="done", passed=True, note="ok")

    loop = orch.run_loop(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note="ok"),
        validation=validator,
        max_runs=10,
        continue_on_retry=True,
        max_attempts_per_item=2,
    )

    assert len(loop) == 2
    assert loop[-1].run.attempt == 2
    assert loop[-1].validation.status == "blocked"
    assert loop[-1].run.status == "blocked"


def test_review_command_validation_bridge_parses_any_json_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.review_command_validation_bridge(
            'printf \'{"status":"retry","note":"custom verdict"}\' > "$ORCH_REVIEW_ARTIFACT_DIR/loop.json"',
            timeout_seconds=2,
        ),
    )

    assert tick is not None
    assert tick.validation.status == "retry"
    assert "custom verdict" in (tick.validation.note or "")


def test_run_once_rejects_missing_project(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))

    with pytest.raises(ValueError):
        orch.run_once("missing")


def test_x_capture_salvage_bridge_writes_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge("printf \"%s\" \"this page isn't working\" >&2"),
        validation=orch.x_capture_salvage_validation_bridge(),
    )

    assert tick is not None
    assert tick.validation.status == "retry"
    assert tick.run.status == "running"
    artifact_root = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    salvage = artifact_root / "x-capture-salvage.json"
    assert salvage.exists()
    payload = json.loads(salvage.read_text(encoding="utf-8"))
    assert payload["matches"]
    salvage_index = tmp_path / "prototypes" / "poe-orchestration" / "output" / "x-capture" / "salvage-index.jsonl"
    assert salvage_index.exists()
    records = [json.loads(line) for line in salvage_index.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(record["run_id"] == tick.run.run_id for record in records)


def test_x_capture_salvage_bridge_escalates_repeated_auth_to_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    validation = orch.x_capture_salvage_validation_bridge(max_auth_retries=3)

    first = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "this page isn\'t working" >&2'),
        validation=validation,
    )
    assert first is not None
    assert first.validation.status == "retry"
    assert first.run.status == "running"

    second = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "captcha challenge" >&2'),
        validation=validation,
    )
    assert second is not None
    assert second.validation.status == "retry"
    assert second.run.status == "running"

    third = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "login required" >&2'),
        validation=validation,
    )
    assert third is not None
    assert third.validation.status == "blocked"
    assert third.run.status == "blocked"
    assert third.validation.note and "repeatedly (3 attempts)" in third.validation.note


def test_operator_status_tracks_active_x_capture_salvage(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "this page isn\'t working" >&2'),
        validation=orch.x_capture_salvage_validation_bridge(),
    )
    assert tick is not None
    assert tick.validation.status == "retry"

    status = orch.write_operator_status()
    assert status["salvage"]["active_count"] == 1
    assert status["salvage"]["pending_count"] == 1
    assert status["salvage"]["active_runs"][0]["run_id"] == tick.run.run_id
    assert status["salvage"]["active_runs"][0]["first_kind"] == "auth"


def test_operator_status_pending_salvage_excludes_resolved_runs(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n- [ ] second\n", priority=3)

    first = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "this page isn\'t working" >&2'),
        validation=orch.x_capture_salvage_validation_bridge(),
    )
    assert first is not None
    assert first.validation.status == "retry"

    finalized = orch.finalize_run(first.run.run_id, "blocked", note="resolved auth issue")
    assert finalized.status == "blocked"

    second = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "captcha" >&2'),
        validation=orch.x_capture_salvage_validation_bridge(),
    )
    assert second is not None
    assert second.validation.status == "retry"

    status = orch.write_operator_status()
    assert status["salvage"]["active_count"] == 1
    assert status["salvage"]["pending_count"] == 1
    assert status["salvage"]["active_runs"][0]["run_id"] == second.run.run_id



def test_command_execution_bridge_success(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf "%s" "$ORCH_ITEM_TEXT" > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_dir = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path
    assert (artifact_dir / "result.txt").read_text(encoding="utf-8") == "first"
    assert (artifact_dir / "stdout.log").exists()
    assert (artifact_dir / "stderr.log").exists()
    summary = artifact_dir / "validation-summary.json"
    assert summary.exists()
    assert '"status": "done"' in summary.read_text(encoding="utf-8")

    prov = tmp_path / "prototypes" / "poe-orchestration" / "projects" / "demo" / "PROVENANCE.md"
    assert "validation-summary.json" in prov.read_text(encoding="utf-8")



def test_command_execution_bridge_failure_blocks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('echo nope >&2; exit 7'),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"
    assert "command failed (7)" in (tick.run.note or "")


def test_review_command_validation_bridge_reads_result_file(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.review_command_validation_bridge(
            'printf \'{"status":"done","note":"from-file"}\' > "$ORCH_REVIEW_ARTIFACT_DIR/result.json"',
        ),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"


def test_validation_summary_includes_bridge_trace(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    validation = orch.chain_validation_bridges(
        orch.named_validation_bridge(
            "artifact-gate",
            orch.artifact_validation_bridge(["result.txt"], nonempty=True),
        ),
        orch.named_validation_bridge(
            "review-gate",
            orch.review_command_validation_bridge(
                'printf \'{"status":"retry","note":"needs manual check"}\' > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.json"'
            ),
        ),
    )
    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=validation,
    )

    assert tick is not None
    assert tick.validation.status == "retry"
    summary = orch.load_validation_summary(tick.run.run_id)
    assert summary is not None
    trace = summary.get("validation_trace")
    assert isinstance(trace, list)
    assert [event["bridge"] for event in trace] == ["artifact-gate", "review-gate"]
    assert trace[-1]["status"] == "retry"



def test_artifact_validation_bridge_accepts_present_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.artifact_validation_bridge(["result.txt"], nonempty=True),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"



def test_artifact_validation_bridge_blocks_missing_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('true'),
        validation=orch.artifact_validation_bridge(["result.txt"], nonempty=True),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"
    assert "missing artifacts: result.txt" in (tick.run.note or "")



def test_review_command_validation_bridge_passes(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.review_command_validation_bridge('test -s "$ORCH_RUN_ARTIFACT_DIR/result.txt" && printf reviewed > "$ORCH_REVIEW_ARTIFACT_DIR/verdict.txt"'),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    review_dir = tmp_path / "prototypes" / "poe-orchestration" / tick.run.artifact_path / "review"
    assert (review_dir / "verdict.txt").read_text(encoding="utf-8") == "reviewed"



def test_chain_validation_bridge_stops_on_review_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.command_execution_bridge('printf ok > "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        validation=orch.chain_validation_bridges(
            orch.artifact_validation_bridge(["result.txt"], nonempty=True),
            orch.review_command_validation_bridge('grep -q excellent "$ORCH_RUN_ARTIFACT_DIR/result.txt"'),
        ),
    )

    assert tick is not None
    assert tick.validation.status == "blocked"
    assert tick.run.status == "blocked"
    assert "review failed" in (tick.run.note or "")
