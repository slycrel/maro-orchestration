"""Concurrency phase 1: in-process run isolation.

The current-run-dir moved from a module global to a ContextVar so concurrent
loops in one process (run_parallel_loops, DAG step fan-out) each see their own
run-dir instead of sharing a last-writer-wins global. ThreadPoolExecutor
workers do NOT inherit the submitting thread's context — fan-out sites must
submit via contextvars.copy_context().run. These tests pin both halves of
that contract, plus the atomic_write primitive.
"""
from __future__ import annotations

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import runs
from file_lock import atomic_write


def test_threads_see_their_own_run_dir(tmp_path):
    """Two threads set distinct run dirs; neither sees the other's."""
    barrier = threading.Barrier(2)
    seen = {}

    def worker(name: str) -> None:
        runs.set_current_run_dir(tmp_path / name)
        barrier.wait()  # both have set before either reads
        seen[name] = runs.current_run_dir()

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert seen["a"] == tmp_path / "a"
    assert seen["b"] == tmp_path / "b"


def test_main_thread_unaffected_by_worker_set(tmp_path):
    runs.set_current_run_dir(tmp_path / "main")

    def worker() -> None:
        runs.set_current_run_dir(tmp_path / "worker")

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert runs.current_run_dir() == tmp_path / "main"


def test_pool_submit_requires_copy_context(tmp_path):
    """Documents the fan-out invariant: bare submit loses the run-dir,
    copy_context().run carries it (the loop_parallel submit pattern)."""
    runs.set_current_run_dir(tmp_path / "parent")

    with ThreadPoolExecutor(max_workers=1) as pool:
        bare = pool.submit(runs.current_run_dir).result()
        wrapped = pool.submit(
            contextvars.copy_context().run, runs.current_run_dir
        ).result()

    assert bare is None
    assert wrapped == tmp_path / "parent"


def test_scoped_run_dir_restores_prior(tmp_path):
    runs.set_current_run_dir(tmp_path / "outer")
    with runs.scoped_run_dir(tmp_path / "inner"):
        assert runs.current_run_dir() == tmp_path / "inner"
    assert runs.current_run_dir() == tmp_path / "outer"

    with runs.scoped_run_dir(None):
        assert runs.current_run_dir() is None
    assert runs.current_run_dir() == tmp_path / "outer"


def test_artifact_dir_isolated_across_threads(tmp_path):
    """The consumer that used to cross-contaminate: artifact_dir() routes to
    the (formerly global) run-dir. Each thread must get its own build/."""
    barrier = threading.Barrier(2)
    dirs = {}

    def worker(name: str) -> None:
        rd = tmp_path / "runs" / f"{name}-nick"
        rd.mkdir(parents=True)
        runs.set_current_run_dir(rd)
        barrier.wait()
        dirs[name] = runs.artifact_dir("some-project")

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("r1", "r2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert dirs["r1"] == tmp_path / "runs" / "r1-nick" / "build"
    assert dirs["r2"] == tmp_path / "runs" / "r2-nick" / "build"


def test_atomic_write_roundtrip(tmp_path):
    target = tmp_path / "meta.json"
    atomic_write(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    atomic_write(target, '{"a": 2}')
    assert target.read_text(encoding="utf-8") == '{"a": 2}'
    # no temp litter left behind
    assert [p.name for p in tmp_path.iterdir()] == ["meta.json"]


def test_atomic_write_creates_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "out.txt"
    atomic_write(target, "content")
    assert target.read_text(encoding="utf-8") == "content"


def test_finalize_run_writes_status_atomically(tmp_path, monkeypatch):
    """finalize_run's metadata rewrite goes through atomic_write now —
    behavior check that status/ended_at still land."""
    rd = runs.create_run_dir("cafe0001", prompt="test goal")
    out = runs.finalize_run("cafe0001", status="success")
    assert out == rd
    import json

    meta = json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
    assert meta["status"] == "success"
    assert meta["ended_at"]
