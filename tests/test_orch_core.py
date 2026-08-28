import json
import os
import time
from pathlib import Path

import orch
import orch_bridges
import pytest


def _mkproj(tmp_path: Path, slug: str, content: str, priority: int = 0):
    p = tmp_path / "projects" / slug
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


def test_run_tick_blocks_when_retry_streak_reaches_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    validator = lambda _run, _execution: orch.ValidationResult(status="retry", passed=False, note="needs rerun")

    first = orch.run_tick(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note=f"ok {run.attempt}"),
        validation=validator,
        max_retry_streak=2,
    )
    assert first is not None
    assert first.validation.status == "retry"
    assert first.run.status == "running"

    second = orch.run_tick(
        "demo",
        worker="tester",
        execution=lambda run: orch.ExecutionResult(status="done", note=f"ok {run.attempt}"),
        validation=validator,
        max_retry_streak=2,
    )
    assert second is not None
    assert second.validation.status == "blocked"
    assert second.run.status == "blocked"
    assert second.run.attempt == 2
    assert "retry streak reached 2 attempts" in (second.run.note or "")


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
    assert "path traversal" in (tick.run.note or "")


def test_worker_session_bridge_by_name(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_root / "researcher-result.json").exists()
    assert not (artifact_root / "worker-result.json").exists()


def test_worker_session_bridge_manifest_command_list(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_root / "cmd.txt").read_text(encoding="utf-8") == "first"


@pytest.mark.slow
def test_session_execution_bridge_timeout_kills_lingering_child_processes(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    # The child below ignores SIGTERM on purpose, so this test always burns the
    # FULL escalation grace — at the production 5.0s it was the single slowest
    # test in the suite (6.2s) and, under `-n`, a floor on total wall time.
    # Shortening the grace exercises the same branch: SIGTERM ignored, grace
    # elapses, SIGKILL lands.
    monkeypatch.setattr(orch_bridges, "TERMINATE_GRACE_SECONDS", 0.2)
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    child_lifetime = 60          # the SIGTERM-ignoring child's own sleep
    started = time.monotonic()
    tick = orch.run_tick(
        "demo",
        worker="tester",
        execution=orch.session_execution_bridge(
            "python3 - <<'PY'\n"
            "import os, signal, subprocess, sys\n"
            "from pathlib import Path\n"
            "artifact_dir = Path(os.environ['ORCH_RUN_ARTIFACT_DIR'])\n"
            "child = subprocess.Popen([\n"
            "    sys.executable,\n"
            "    '-c',\n"
            f"    'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep({child_lifetime})',\n"
            "])\n"
            "(artifact_dir / 'child.pid').write_text(str(child.pid), encoding='utf-8')\n"
            "signal.pause()\n"
            "PY",
            # Widened from 0.1 (2026-08-06). This budget has to cover this
            # script's OWN setup — two interpreter startups, a Popen, a file
            # write — because every assertion below needs child.pid to exist.
            # At 0.1 the margin was thin: that work measures 28 ms unloaded
            # and 49 ms under 10 CPU spinners, so 2-3.5x, which is not much
            # for a timing-dependent test in a suite that runs under `-n`.
            #
            # Honesty about what this is: the suite failed here ONCE, with
            # child.pid missing (FileNotFoundError). A too-tight budget is the
            # best explanation, but I could NOT reproduce it — CPU load alone
            # does not push setup past 100 ms, so the mechanism is plausible
            # and unproven. This widens the margin ~20x against a cheap,
            # low-risk change; it is not a demonstrated fix. If it recurs at
            # 1.0s, the cause is elsewhere (fork/exec contention or tmpdir I/O
            # under xdist are the next suspects) — do not just widen again.
            #
            # An absent child.pid must never be treated as a pass: it means
            # the parent died before writing, which is EITHER "no child was
            # spawned" (nothing leaked) OR "a child was spawned and we lost
            # its pid" (exactly the leak this test exists to catch). Those are
            # indistinguishable from here, so the answer is to not race.
            #
            # Costs ~0.9s: signal.pause() never returns, so the bridge always
            # burns the full timeout. Deliberate spend against the shortened
            # TERMINATE_GRACE_SECONDS above (5.0 -> 0.2, which saved ~4.8s) —
            # an intermittent failure in a zero-skip suite costs more.
            timeout_seconds=1.0,
        ),
    )
    elapsed = time.monotonic() - started

    assert tick is not None
    assert tick.validation.status == "blocked"
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    child_pid = int((artifact_root / "child.pid").read_text(encoding="utf-8").strip())

    # The child must have been KILLED, not merely outlived. Without this bound
    # the test passes even with SIGKILL escalation removed: communicate() holds
    # the pipe open until the grandchild exits on its own, so the liveness poll
    # below then finds it already gone and the whole thing "passes" in 60s.
    # Verified 2026-07-29 by disabling the escalation — old test: pass in 60s;
    # with this bound: fail.
    assert elapsed < child_lifetime / 4, (
        f"bridge took {elapsed:.1f}s for a {child_lifetime}s child — the "
        "SIGTERM-ignoring process group was waited out, not SIGKILLed")

    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"timed-out session child still running: pid={child_pid}")


def test_worker_session_bridge_manifest_supports_nested_artifacts_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_root / "nested" / "payload.json").exists()
    assert (artifact_root / "nested" / "result.json").exists()


def test_worker_session_bridge_supports_working_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
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
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    expected = str(worker_dir)
    assert (artifact_root / "working_directory.txt").read_text(encoding="utf-8") == expected


def test_worker_session_bridge_defaults_cwd_to_manifest_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
    worker_dir = workers / "relative-worker"
    worker_dir.mkdir(parents=True, exist_ok=True)
    helper = worker_dir / "write-note.sh"
    helper.write_text('printf "manifest-cwd" > "$ORCH_RUN_ARTIFACT_DIR/from-relative-command.txt"\n', encoding="utf-8")
    helper.chmod(0o755)
    manifest = worker_dir / "worker-session.json"
    manifest.write_text(json.dumps({"command": "./write-note.sh"}), encoding="utf-8")

    tick = orch.run_tick(
        "demo",
        worker=str(manifest),
        execution=orch.worker_session_bridge(str(manifest)),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_root / "from-relative-command.txt").read_text(encoding="utf-8") == "manifest-cwd"


def test_worker_session_bridge_resolves_relative_working_directory_from_manifest_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
    worker_dir = workers / "rel-workdir"
    workdir = worker_dir / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    manifest = worker_dir / "worker-session.json"
    manifest.write_text(
        json.dumps(
            {
                "command": 'pwd > "$ORCH_RUN_ARTIFACT_DIR/resolved-cwd.txt"',
                "working_directory": "workspace",
            }
        ),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker=str(manifest),
        execution=orch.worker_session_bridge(str(manifest)),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_root / "resolved-cwd.txt").read_text(encoding="utf-8").strip() == str(workdir)


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
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    salvage = artifact_root / "x-capture-salvage.json"
    assert salvage.exists()
    payload = json.loads(salvage.read_text(encoding="utf-8"))
    assert payload["matches"]
    salvage_index = tmp_path / "output" / "x-capture" / "salvage-index.jsonl"
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
    artifact_dir = orch.resolve_artifact_path(tick.run.artifact_path)
    assert (artifact_dir / "result.txt").read_text(encoding="utf-8") == "first"
    assert (artifact_dir / "stdout.log").exists()
    assert (artifact_dir / "stderr.log").exists()
    summary = artifact_dir / "validation-summary.json"
    assert summary.exists()
    assert '"status": "done"' in summary.read_text(encoding="utf-8")

    prov = tmp_path / "projects" / "demo" / "PROVENANCE.md"
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
    review_dir = orch.resolve_artifact_path(tick.run.artifact_path) / "review"
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


# ---------------------------------------------------------------------------
# Bootstrap fixes: append_next_items creates NEXT.md if missing (BFix-NEXT-01)
# ---------------------------------------------------------------------------

def test_append_next_items_creates_next_md_when_missing(monkeypatch, tmp_path):
    """append_next_items should not raise when NEXT.md is missing — creates it instead."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    # Create just the project dir (no NEXT.md — simulates partially-initialised project)
    proj_dir = tmp_path / "projects" / "partial-proj"
    proj_dir.mkdir(parents=True)

    # append_next_items should not raise FileNotFoundError or ValueError
    indices = orch.append_next_items("partial-proj", ["step one", "step two"])
    assert len(indices) == 2
    next_md = proj_dir / "NEXT.md"
    assert next_md.exists()
    content = next_md.read_text(encoding="utf-8")
    assert "step one" in content
    assert "step two" in content


def test_append_next_items_empty_creates_nothing(monkeypatch, tmp_path):
    """append_next_items with empty list returns [] without touching the filesystem."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    proj_dir = tmp_path / "projects" / "empty-proj"
    proj_dir.mkdir(parents=True)

    result = orch.append_next_items("empty-proj", [])
    assert result == []
    # NEXT.md should NOT be created
    assert not (proj_dir / "NEXT.md").exists()


def test_ensure_project_is_idempotent(monkeypatch, tmp_path):
    """ensure_project called twice does not overwrite NEXT.md with user content."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    orch.ensure_project("idempotent-test", "first mission")
    proj_dir = tmp_path / "projects" / "idempotent-test"
    # Write custom content to NEXT.md
    (proj_dir / "NEXT.md").write_text("# Custom\n\n- [ ] my item\n", encoding="utf-8")
    # Call again — should not overwrite
    orch.ensure_project("idempotent-test", "second mission")
    content = (proj_dir / "NEXT.md").read_text(encoding="utf-8")
    assert "my item" in content


def test_ensure_project_mints_no_risks_or_provenance_stubs(monkeypatch, tmp_path):
    """RISKS.md/PROVENANCE.md are lazy-created on first append, never stubbed.

    A "(fill in)" stub outlives any run with nothing to record, and curation
    served one as a run deliverable (8b8671bd 2026-08-06).
    """
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    orch.ensure_project("no-stubs", "test mission")
    proj_dir = tmp_path / "projects" / "no-stubs"
    assert (proj_dir / "NEXT.md").exists()
    assert (proj_dir / "DECISIONS.md").exists()
    assert not (proj_dir / "RISKS.md").exists()
    assert not (proj_dir / "PROVENANCE.md").exists()

    # First real write creates the file with its heading
    orch.append_risk("no-stubs", ["something went sideways"])
    risks = (proj_dir / "RISKS.md").read_text(encoding="utf-8")
    assert risks.startswith("# RISKS")
    assert "something went sideways" in risks
    orch.append_provenance("no-stubs", ["run xyz artifact"])
    prov = (proj_dir / "PROVENANCE.md").read_text(encoding="utf-8")
    assert prov.startswith("# PROVENANCE")
    assert "run xyz artifact" in prov


def test_dir_exists_no_next_md_is_handled(monkeypatch, tmp_path):
    """Project dir exists but NEXT.md missing: append_next_items recovers gracefully."""
    monkeypatch.setenv("MARO_ORCH_ROOT", str(tmp_path))
    # Create project via ensure_project so NEXT.md exists, then delete it
    orch.ensure_project("ghost-next", "test mission")
    proj_dir = tmp_path / "projects" / "ghost-next"
    next_md = proj_dir / "NEXT.md"
    assert next_md.exists()
    next_md.unlink()
    assert not next_md.exists()

    # Now append_next_items should recreate NEXT.md
    indices = orch.append_next_items("ghost-next", ["recover step"])
    assert len(indices) == 1
    assert next_md.exists()


def test_write_operator_status_skips_missing_next_md(monkeypatch, tmp_path):
    """write_operator_status must not crash when a project dir exists but NEXT.md is missing.

    Regression test for the spawn_persona bootstrap failure:
    write_operator_status() is called at the end of run_agent_loop. If any project
    directory exists without NEXT.md (from a prior crashed init or path mismatch),
    the old code raised ValueError which propagated as "Spawn failed: project X has no NEXT.md".
    """
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    # Create a well-formed project
    _mkproj(tmp_path, "good-project", "- [ ] step one\n", priority=1)
    # Create a project directory WITHOUT NEXT.md (simulates partial init / crashed run)
    broken_dir = tmp_path / "projects" / "broken-project"
    broken_dir.mkdir(parents=True)
    # No NEXT.md written — this is the broken state

    # Should not raise despite the broken project
    status = orch.write_operator_status()
    assert "good-project" in status["active_projects"] or status["queue"]["todo"] >= 1
    # broken-project is silently skipped — its missing NEXT.md is not propagated


def test_worker_session_bridge_supports_cwd_alias(monkeypatch, tmp_path):
    import json as _json
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)

    workers = tmp_path / "prototypes" / "maro-orchestration" / "workers"
    worker_dir = workers / "worker-dir"
    worker_dir.mkdir(parents=True, exist_ok=True)
    manifest = workers / "runner-cwd.json"
    manifest.write_text(
        _json.dumps({
            "command": 'printf "%s" "$ORCH_SESSION_WORKING_DIR" > "$ORCH_RUN_ARTIFACT_DIR/cwd.txt"',
            "cwd": "workers/worker-dir",
        }),
        encoding="utf-8",
    )

    tick = orch.run_tick(
        "demo",
        worker="runner-cwd",
        execution=orch.worker_session_bridge("runner-cwd"),
    )

    assert tick is not None
    assert tick.validation.status == "done"
    assert tick.run.status == "done"
    artifact_root = orch.resolve_artifact_path(tick.run.artifact_path)
    expected = str(worker_dir)
    assert (artifact_root / "cwd.txt").read_text(encoding="utf-8") == expected


# ---------------------------------------------------------------------------
# C0.8 — tolerant RunRecord loader (docs/CONTRACTS.md)
# ---------------------------------------------------------------------------

def _write_raw_run_record(run_id: str, data: dict) -> Path:
    import orch_items
    path = orch_items.runs_root() / f"{run_id}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_record_dict(run_id: str) -> dict:
    return {
        "run_id": run_id,
        "project": "demo",
        "index": 0,
        "text": "first",
        "status": "done",
        "source": "unit",
        "worker": "tester",
        "started_at": "2026-08-28T00:00:00+00:00",
        "updated_at": "2026-08-28T00:00:00+00:00",
    }


def test_load_run_record_tolerates_additive_field(monkeypatch, tmp_path):
    """C0.8 must-detect: RunRecord(**data) raised TypeError on any field a
    newer writer legally added; the loader must filter, not crash."""
    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    data = _minimal_record_dict("run-add")
    data["future_field"] = "x"
    _write_raw_run_record("run-add", data)
    rec = orch_items.load_run_record("run-add")
    assert rec.run_id == "run-add"
    assert rec.project == "demo"
    assert rec.attempt == 1


def test_load_run_records_keeps_additive_field_rows(monkeypatch, tmp_path):
    """C0.8 must-detect: one legal additive field silently vanished EVERY
    such record from listings (except Exception: continue)."""
    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    data = _minimal_record_dict("run-list")
    data["future_field"] = "x"
    _write_raw_run_record("run-list", data)
    records = orch_items._load_run_records()
    assert [r.run_id for r in records] == ["run-list"]


def test_load_run_records_skips_corrupt_file_with_logged_reason(
        monkeypatch, tmp_path, caplog):
    """C0.8 must-detect: a genuinely corrupt file is skipped WITH a logged
    reason — the pre-fix blanket except was intolerant AND invisible."""
    import logging

    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _write_raw_run_record("run-good", _minimal_record_dict("run-good"))
    bad = orch_items.runs_root() / "run-bad.json"
    bad.write_text("not json{{{", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="maro.orch_items"):
        records = orch_items._load_run_records()
    assert [r.run_id for r in records] == ["run-good"]
    assert "run-bad.json" in caplog.text
    assert "skipped" in caplog.text


# ---------------------------------------------------------------------------
# R2-4 — run-record store: extras round-trip through production RMW,
# atomic writes, validated hard core (docs/CONTRACTS.md round 2)
# ---------------------------------------------------------------------------

def test_finalize_run_roundtrips_future_field(monkeypatch, tmp_path):
    """R2-4 must-detect: the tolerant loader FILTERED unknown keys without
    retaining them, so the literal production RMW (load -> mutate ->
    write_run_record in orch.finalize_run) deleted a newer engine's fields
    on the next update — the C0.1 trap re-created one store over."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)
    run = orch.run_once("demo", worker="tester", source="unit")
    assert run is not None
    # A newer engine legally adds a field to the record on disk.
    import orch_items
    path = orch_items.runs_root() / f"{run.run_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["future_field"] = "engine-b-was-here"
    path.write_text(json.dumps(raw), encoding="utf-8")

    orch.finalize_run(run.run_id, "done", note="unit verified")

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["status"] == "done"
    assert after["future_field"] == "engine-b-was-here"


# ---------------------------------------------------------------------------
# R3-6 — run-record mutations are LOCKED RMWs: a write landing between a
# mutator's load and its publish must survive (atomic_write prevents torn
# files, not lost updates).
# ---------------------------------------------------------------------------

def test_finalize_run_does_not_lose_concurrent_update(monkeypatch, tmp_path):
    """R3-6 must-detect: orch.finalize_run loaded a RunRecord, mutated the
    stale object, and rewrote the file — a concurrent writer's update
    landing in the window (here: injected during mark_item, i.e. after
    finalize's load, before its write) was silently destroyed."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)
    run = orch.run_once("demo", worker="tester", source="unit")
    assert run is not None
    import orch_items
    path = orch_items.runs_root() / f"{run.run_id}.json"

    real_mark_item = orch.mark_item

    def mark_item_and_race(slug, index, state):
        out = real_mark_item(slug, index, state)
        # Concurrent writer (another process / a second engine) lands an
        # update AFTER finalize_run's load, BEFORE its publish.
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["future_b"] = "landed-mid-finalize"
        path.write_text(json.dumps(raw), encoding="utf-8")
        return out

    monkeypatch.setattr(orch, "mark_item", mark_item_and_race)
    finished = orch.finalize_run(run.run_id, "done", note="unit verified")
    assert finished.status == "done"

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["status"] == "done"
    assert after.get("future_b") == "landed-mid-finalize"


def test_mutate_run_record_interleaved_mutations_both_survive(
        monkeypatch, tmp_path):
    """R3-6: the primitive itself — a stale RunRecord object held across
    another mutate_run_record does not cause a lost update, because each
    mutation re-reads the CURRENT raw dict under the record's lock."""
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _mkproj(tmp_path, "demo", "- [ ] first\n", priority=3)
    run = orch.run_once("demo", worker="tester", source="unit")
    assert run is not None
    import orch_items

    # Writer A loads (and holds a now-soon-to-be-stale object)...
    stale_a = orch_items.load_run_record(run.run_id)
    assert not stale_a.note
    # ...writer B lands its mutation...
    def _b(rec):
        rec.note = "note-from-b"
    orch_items.mutate_run_record(run.run_id, _b)

    # ...writer A now applies ITS mutation via the primitive. Its stale
    # object is irrelevant: the mutator runs against the fresh record.
    def _a(rec):
        rec.worker = "worker-from-a"
    final = orch_items.mutate_run_record(run.run_id, _a)
    assert final.note == "note-from-b"
    assert final.worker == "worker-from-a"

    path = orch_items.runs_root() / f"{run.run_id}.json"
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["note"] == "note-from-b"
    assert after["worker"] == "worker-from-a"


def test_mutate_run_record_missing_record_raises(monkeypatch, tmp_path):
    """R3-6: write_run_record stays the create-new path — the locked RMW
    refuses to invent a record."""
    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        orch_items.mutate_run_record("run-nonexistent", lambda rec: None)


def test_empty_object_run_record_is_skipped_with_reason(
        monkeypatch, tmp_path, caplog):
    """R2-4 must-detect: `{}` used to default into a plausible live record
    ('' run_id, index 0) feeding attempt arithmetic. It must be logged and
    skipped, and load_run_record must raise a clear error."""
    import logging

    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _write_raw_run_record("run-empty", {})
    with caplog.at_level(logging.WARNING, logger="maro.orch_items"):
        records = orch_items._load_run_records()
    assert records == []
    assert "run-empty.json" in caplog.text and "skipped" in caplog.text
    with pytest.raises(ValueError):
        orch_items.load_run_record("run-empty")


def test_non_coercible_index_run_record_is_skipped(
        monkeypatch, tmp_path, caplog):
    """R2-4 must-detect: a wrong-typed index ("NaN") became int() fodder for
    attempt arithmetic downstream. Logged rejection, not a default."""
    import logging

    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    data = _minimal_record_dict("run-nanidx")
    data["index"] = "NaN"
    _write_raw_run_record("run-nanidx", data)
    with caplog.at_level(logging.WARNING, logger="maro.orch_items"):
        records = orch_items._load_run_records()
    assert records == []
    assert "run-nanidx" in caplog.text and "index" in caplog.text


def test_run_record_filename_mismatch_is_rejected(monkeypatch, tmp_path, caplog):
    """The filename is the store's key: a body claiming another run_id would
    impersonate that record on the next rewrite."""
    import logging

    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    _write_raw_run_record("run-alias", _minimal_record_dict("run-other"))
    with caplog.at_level(logging.WARNING, logger="maro.orch_items"):
        records = orch_items._load_run_records()
    assert records == []
    assert "does not match" in caplog.text
    with pytest.raises(ValueError):
        orch_items.load_run_record("run-alias")


def test_run_record_additive_field_still_loads(monkeypatch, tmp_path):
    """Negative control (rule A3): an additive unknown field neither crashes
    nor rejects — it loads AND rides the record as extras."""
    import orch_items
    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    data = _minimal_record_dict("run-extra")
    data["future_field"] = {"nested": True}
    _write_raw_run_record("run-extra", data)
    records = orch_items._load_run_records()
    assert [r.run_id for r in records] == ["run-extra"]
    assert records[0]._extras == {"future_field": {"nested": True}}
    # And write_run_record restores it, declared fields winning.
    out = orch_items.write_run_record(records[0])
    assert json.loads(out.read_text(encoding="utf-8"))["future_field"] == \
        {"nested": True}
