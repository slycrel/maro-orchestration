"""The historical direct-Director A/B harness isolates every cell."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "worker_slice_ab", ROOT / "scripts" / "worker_slice_ab.py"
)
worker_slice_ab = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(worker_slice_ab)


def test_run_arm_binds_worker_cwd_and_records_workspace(monkeypatch, tmp_path):
    import director
    from llm import get_default_subprocess_cwd

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    seen = {}

    def fake_run_director(directive, **kwargs):
        seen["cwd"] = get_default_subprocess_cwd()
        seen["project"] = kwargs["project"]
        return SimpleNamespace(
            status="done", worker_slice=True, tokens_in=1, tokens_out=2,
            worker_results=[], log_path=None,
        )

    monkeypatch.setattr(director, "run_director", fake_run_director)
    row = worker_slice_ab.run_arm(
        "design a checklist",
        True,
        False,
        batch_id="batch-1",
        cell_id="m3-rep-1-A-off",
    )

    workspace = Path(row["workspace"])
    assert workspace.is_dir()
    assert seen["cwd"] == str(workspace)
    assert seen["project"].startswith("benchmark-batch-1-")
    assert ROOT not in workspace.parents


def test_run_arm_refuses_duplicate_cell_identity(monkeypatch, tmp_path):
    import director
    from benchmark_isolation import BenchmarkCellExistsError

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(
        director,
        "run_director",
        lambda *a, **k: SimpleNamespace(
            status="done", worker_slice=False, tokens_in=0, tokens_out=0,
            worker_results=[], log_path=None,
        ),
    )
    kwargs = dict(batch_id="same-batch", cell_id="same-cell")
    worker_slice_ab.run_arm("goal", False, True, **kwargs)

    import pytest
    with pytest.raises(BenchmarkCellExistsError, match="same-batch.*same-cell"):
        worker_slice_ab.run_arm("goal", False, True, **kwargs)


def test_run_arm_preserves_workspace_when_director_fails(monkeypatch, tmp_path):
    import director
    from llm import get_default_subprocess_cwd, set_default_subprocess_cwd

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    prior = str(tmp_path / "prior")
    set_default_subprocess_cwd(prior)

    def fail_after_artifact(*args, **kwargs):
        cwd = Path(get_default_subprocess_cwd())
        (cwd / "partial.txt").write_text("inspect me")
        raise RuntimeError("director exploded")

    monkeypatch.setattr(director, "run_director", fail_after_artifact)
    row = worker_slice_ab.run_arm(
        "goal",
        False,
        False,
        batch_id="failed-batch",
        cell_id="failed-cell",
    )

    workspace = Path(row["workspace"])
    assert row["status"] == "error: director exploded"
    assert (workspace / "partial.txt").read_text() == "inspect me"
    assert get_default_subprocess_cwd() == prior
