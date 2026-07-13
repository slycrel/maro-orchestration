"""Real-docker E2E scaffold for the containerized executor (C4 residual).

CI keeps docker MOCKED (test_container_exec.py) so it never needs a daemon.
These tests exercise the REAL path the mocked suite cannot prove: the
`build_mount_map` → `build_run_command` → `docker run` translation actually
honoring mount modes and punctuation-bearing paths in a live container, plus
`--rm` failure cleanup. They SKIP wholesale when docker or the executor image
is absent, so they are box-only — run during burn-in (see
docs/CONTAINER_BURN_IN.md), never a CI gate.

Run on the box after `maro-bootstrap container-setup`:
    PYTHONPATH=src python3 -m pytest tests/test_container_e2e.py -v

They use a plain `sh` inner command (not the claude CLI) so they test the
mount/translation layer without spending tokens or needing a logged-in auth
volume — build_run_command still mounts the auth volume, which docker
auto-creates empty if login hasn't run; the shell command doesn't touch it.
"""
from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import container_exec as ce


def _docker_ready() -> bool:
    try:
        return ce.docker_probe()[0] and ce.image_probe()[0]
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _docker_ready(),
    reason="real docker daemon + built executor image required "
    "(box-only burn-in; CI mocks docker)",
)

_TIMEOUT = 120


def _run(inner_cmd, *, mounts, workdir, name=None):
    """Build the production docker-run vector and execute it."""
    name = name or f"maro-e2e-{uuid.uuid4().hex[:8]}"
    cmd = ce.build_run_command(inner_cmd, name=name, workdir=workdir, mounts=mounts)
    return name, subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)


def _container_exists(name: str) -> bool:
    r = subprocess.run(
        ["docker", "ps", "-aq", "--filter", f"name=^{name}$"],
        capture_output=True, text=True, timeout=30,
    )
    return bool(r.stdout.strip())


def test_rw_mount_roundtrip_punctuation_path(tmp_path):
    """A rw mount at a colon+space host path round-trips: the container writes,
    the host sees it. Proves --mount (vs -v CSV) handles legal-but-awkward
    paths (adversarial-review 2026-07-12 colon-safety) against real docker."""
    workdir = tmp_path / "weird path:with-colon"
    workdir.mkdir()
    # cwd is auto-rw; forbidden filter off — E2E tests docker mechanics, not the fence.
    mounts = ce.build_mount_map(str(workdir), forbidden_roots=[])
    _, r = _run(
        ["sh", "-lc", "echo hello-from-container > out.txt"],
        mounts=mounts, workdir=str(workdir),
    )
    assert r.returncode == 0, r.stderr
    assert (workdir / "out.txt").read_text().strip() == "hello-from-container"


def test_ro_mount_is_readonly_in_real_container(tmp_path):
    """A ro reference mount is readable but a write to it fails inside the
    container — the containment the mount map promises, proven by real docker."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "given.txt").write_text("read me\n", encoding="utf-8")

    mounts = ce.build_mount_map(str(workdir), ro_mounts=[str(ref)], forbidden_roots=[])
    # read works
    _, r_read = _run(
        ["sh", "-lc", f"cat '{ref}/given.txt'"],
        mounts=mounts, workdir=str(workdir),
    )
    assert r_read.returncode == 0, r_read.stderr
    assert "read me" in r_read.stdout
    # write is refused (read-only bind mount)
    _, r_write = _run(
        ["sh", "-lc", f"echo x > '{ref}/should-fail.txt'"],
        mounts=mounts, workdir=str(workdir),
    )
    assert r_write.returncode != 0, "write to a ro mount unexpectedly succeeded"
    assert not (ref / "should-fail.txt").exists()


def test_ro_nested_under_rw_is_subsumed_to_rw(tmp_path):
    """build_mount_map dedup: a ro child under a rw parent is absorbed by the
    rw parent (rw covers ro child — C3 containment semantics), so the child is
    WRITABLE in the container. Proven end-to-end so the dedup can't silently
    invert against real docker."""
    workdir = tmp_path / "work"
    (workdir / "sub").mkdir(parents=True)
    mounts = ce.build_mount_map(
        str(workdir), ro_mounts=[str(workdir / "sub")], forbidden_roots=[]
    )
    _, r = _run(
        ["sh", "-lc", "echo nested > sub/nested.txt"],
        mounts=mounts, workdir=str(workdir),
    )
    assert r.returncode == 0, r.stderr
    assert (workdir / "sub" / "nested.txt").read_text().strip() == "nested"


def test_failed_container_leaves_no_stray(tmp_path):
    """--rm reaps a container that exits non-zero — no stray by name for the
    stranded-container sweep to find on a normal (non-SIGKILL) failure."""
    workdir = tmp_path / "work"
    workdir.mkdir()
    mounts = ce.build_mount_map(str(workdir), forbidden_roots=[])
    name, r = _run(
        ["sh", "-lc", "exit 7"],
        mounts=mounts, workdir=str(workdir), name=f"maro-e2e-fail-{uuid.uuid4().hex[:8]}",
    )
    assert r.returncode == 7, f"expected the inner exit code to propagate, got {r.returncode}"
    assert not _container_exists(name), "a failed --rm container left a stray"
