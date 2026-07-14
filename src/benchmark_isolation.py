"""Fail-closed filesystem isolation helpers for benchmark/eval cells.

Benchmarks are repeated by design.  Reusing a goal-derived project directory
lets later cells observe earlier artifacts and destroys the comparison.  Direct
Director experiments are even easier to leak: without a loop-owned project,
agentic subprocesses inherit the harness launch directory (often this repo).

This module gives both harness shapes one neutral, auditable convention:

* handle/loop benchmarks get a unique project slug per run + cell;
* direct-agent benchmarks get a unique retained workspace and a scoped default
  subprocess cwd;
* a requested path collision refuses instead of silently reusing old state.
"""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Tuple


_SAFE_PART_RE = re.compile(r"[^a-z0-9]+")


class BenchmarkCellExistsError(FileExistsError):
    """A benchmark identity resolved to retained state from another attempt."""


def _safe_part(value: str, *, fallback: str = "cell", max_chars: int = 48) -> str:
    part = _SAFE_PART_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return (part or fallback)[:max_chars].rstrip("-") or fallback


def _identity_part(value: str, *, fallback: str, max_chars: int) -> str:
    raw = str(value or "")
    readable = _safe_part(raw, fallback=fallback, max_chars=max_chars)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{readable}-{digest}"


def benchmark_project_slug(run_id: str, cell_id: str) -> str:
    """Return a unique, readable Maro project slug for one eval cell."""
    return (
        "benchmark-"
        f"{_identity_part(run_id, fallback='run', max_chars=20)}-"
        f"{_identity_part(cell_id, fallback='cell', max_chars=40)}"
    )


def reserve_benchmark_project(run_id: str, cell_id: str) -> Tuple[str, Path]:
    """Atomically reserve a fresh project directory for one handle eval cell.

    ``run_agent_loop`` normally creates project directories idempotently, which
    is correct for organic continuation but unsafe for an independent eval
    cell. Reserving first makes accidental identity reuse visible while leaving
    the normal loop free to initialize its files inside the owned directory.
    """
    from orch_items import project_dir, projects_root

    slug = benchmark_project_slug(run_id, cell_id)
    projects_root().mkdir(parents=True, exist_ok=True)
    path = project_dir(slug)
    try:
        path.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise BenchmarkCellExistsError(
            f"benchmark cell identity already exists; refusing retained state: "
            f"run_id={run_id!r} cell_id={cell_id!r} path={path}"
        ) from exc
    return slug, path


@contextmanager
def benchmark_workspace(run_id: str, cell_id: str) -> Generator[Path, None, None]:
    """Create and bind a retained workspace for a direct-agent benchmark.

    The directory lives under the normal Maro output root so operators can
    inspect artifacts after the experiment. These are retained evidence under
    Maro's no-silent-deletion decree, not temporary scratch.
    ``exist_ok=False`` is the contamination guard: a duplicate run/cell
    identity is an error, never an invitation to consume a previous cell's
    files.
    """
    from llm import default_subprocess_cwd
    from orch_items import output_root

    path = (
        output_root()
        / "benchmark-workspaces"
        / _identity_part(run_id, fallback="run", max_chars=40)
        / _identity_part(cell_id, fallback="cell", max_chars=64)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise BenchmarkCellExistsError(
            f"benchmark cell identity already exists; refusing retained state: "
            f"run_id={run_id!r} cell_id={cell_id!r} path={path}"
        ) from exc
    with default_subprocess_cwd(str(path)):
        yield path
