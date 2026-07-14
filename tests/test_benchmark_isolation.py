"""Benchmark cells never reuse filesystem state or inherit the launch cwd."""

from pathlib import Path

import pytest


def test_benchmark_project_slug_is_stable_readable_and_safe():
    from benchmark_isolation import benchmark_project_slug

    slug = benchmark_project_slug("Run 12/A", "M3: host monitoring")
    assert slug.startswith("benchmark-run-12-a-")
    assert "-m3-host-monitoring-" in slug
    assert slug == benchmark_project_slug("Run 12/A", "M3: host monitoring")
    assert "/" not in slug
    assert " " not in slug


def test_benchmark_identity_digest_prevents_sanitization_and_truncation_collisions():
    from benchmark_isolation import benchmark_project_slug

    assert benchmark_project_slug("run", "A/B") != benchmark_project_slug("run", "A B")
    long_prefix = "x" * 100
    assert (
        benchmark_project_slug("run", long_prefix + "one")
        != benchmark_project_slug("run", long_prefix + "two")
    )


def test_benchmark_workspace_scopes_cwd_and_refuses_reuse(monkeypatch, tmp_path):
    from benchmark_isolation import BenchmarkCellExistsError, benchmark_workspace
    from llm import get_default_subprocess_cwd, set_default_subprocess_cwd

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    prior = str(tmp_path / "prior")
    set_default_subprocess_cwd(prior)

    with benchmark_workspace("batch-1", "cell-a") as workspace:
        assert workspace.is_dir()
        assert get_default_subprocess_cwd() == str(workspace)
        (workspace / "artifact.txt").write_text("cell A")

    assert get_default_subprocess_cwd() == prior
    with pytest.raises(BenchmarkCellExistsError, match="run_id='batch-1'.*cell_id='cell-a'"):
        with benchmark_workspace("batch-1", "cell-a"):
            pass


def test_benchmark_project_reservation_refuses_reuse(monkeypatch, tmp_path):
    from benchmark_isolation import BenchmarkCellExistsError, reserve_benchmark_project

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    slug, project = reserve_benchmark_project("run-1", "cell-a")
    assert slug.startswith("benchmark-run-1-")
    assert "-cell-a-" in slug
    assert project.is_dir()
    with pytest.raises(BenchmarkCellExistsError, match="run_id='run-1'.*cell_id='cell-a'"):
        reserve_benchmark_project("run-1", "cell-a")


def test_distinct_cells_get_distinct_retained_workspaces(monkeypatch, tmp_path):
    from benchmark_isolation import benchmark_workspace

    monkeypatch.setenv("OPENCLAW_WORKSPACE", str(tmp_path))
    with benchmark_workspace("batch-2", "A") as first:
        (first / "only-a.txt").write_text("a")
    with benchmark_workspace("batch-2", "B") as second:
        assert not (second / "only-a.txt").exists()
    assert first != second
    assert (first / "only-a.txt").read_text() == "a"
