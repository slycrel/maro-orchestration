from pathlib import Path

import orch


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
