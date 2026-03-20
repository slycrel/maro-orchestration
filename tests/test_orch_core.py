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
