"""The clean-checkout tripwire has to be tested, or it is just a comment.

Decree (Jeremy, 2026-07-28): work should presume it is "out of the box"
functionality on day 1 and be verifiable on a clean clone. The hooks in
conftest.py enforce the test-suite half of that.

This file proves the tripwire actually fires. The standing lesson is the
2026-06-25 one: every git hook in this repo — including the worker push
guard — sat silently dead for a month behind a stale `core.hooksPath`, and
nobody noticed because no test exercised them. A guard nobody exercises is
indistinguishable from no guard.

The end-to-end cases run a real pytest against a scoped tmp tree, because
what matters is the behaviour (a littering test fails the run), not the
internals.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import _checkout_tripwire as tripwire


# --------------------------------------------------------------------------
# Pruning rules — the part that decides what counts as a leak
# --------------------------------------------------------------------------


def test_snapshot_prunes_regenerable_noise(tmp_path):
    (tmp_path / "real.txt").write_text("keep me")
    for noisy in (".git", "__pycache__", ".pytest_cache", ".venv", ".venv-mlx"):
        d = tmp_path / noisy
        d.mkdir()
        (d / "junk.txt").write_text("noise")
    (tmp_path / "mod.pyc").write_text("noise")
    (tmp_path / ".coverage").write_text("noise")

    assert tripwire.snapshot(tmp_path) == {"real.txt"}


def test_snapshot_does_not_prune_the_dirs_that_actually_leaked(tmp_path):
    """output/ and memory/ are where both real leaks landed — never prune them."""
    for leaky in ("output", "memory"):
        d = tmp_path / leaky / "runs"
        d.mkdir(parents=True)
        (d / "artifact.json").write_text("{}")

    found = tripwire.snapshot(tmp_path)
    assert str(Path("output/runs/artifact.json")) in found
    assert str(Path("memory/runs/artifact.json")) in found


def test_report_names_the_files_and_the_escape_hatch():
    report = tripwire.format_report(["output/runs/run-1/item.txt"])
    assert "output/runs/run-1/item.txt" in report
    assert tripwire.ALLOW_ENV in report


def test_report_truncates_but_says_how_many_it_hid():
    added = [f"output/runs/run-{i}/item.txt" for i in range(50)]
    report = tripwire.format_report(added)
    assert "50 new file(s)" in report
    assert "and 30 more" in report


# --------------------------------------------------------------------------
# End-to-end — does a littering test actually fail a run?
# --------------------------------------------------------------------------


def _write_scoped_tree(root: Path, *, litter: bool) -> None:
    """A miniature project wired to the same tripwire module."""
    tests_dir = Path(tripwire.__file__).resolve().parent
    (root / "conftest.py").write_text(
        textwrap.dedent(
            f"""\
            import os, sys
            from pathlib import Path

            sys.path.insert(0, {str(tests_dir)!r})
            import _checkout_tripwire as tripwire

            ROOT = Path(__file__).resolve().parent
            _baseline = None


            def pytest_sessionstart(session):
                global _baseline
                if os.environ.get(tripwire.ALLOW_ENV):
                    return
                _baseline = tripwire.snapshot(ROOT)


            def pytest_sessionfinish(session, exitstatus):
                if _baseline is None:
                    return
                added = sorted(tripwire.snapshot(ROOT) - _baseline)
                if not added:
                    return
                print(tripwire.format_report(added))
                if exitstatus == 0:
                    session.exitstatus = 1
            """
        ),
        encoding="utf-8",
    )
    body = (
        "    (Path(__file__).resolve().parent / 'leaked-artifact.txt').write_text('x')\n"
        if litter
        else "    pass\n"
    )
    (root / "test_subject.py").write_text(
        "from pathlib import Path\n\n\ndef test_subject():\n" + body,
        encoding="utf-8",
    )


def _run_scoped_pytest(root: Path, env_extra: dict | None = None):
    env = os.environ.copy()
    # The parent session's recursion guard would refuse a nested run.
    env.pop("MARO_PYTEST_ACTIVE", None)
    env.pop(tripwire.ALLOW_ENV, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(root)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.slow
def test_tripwire_fails_the_run_when_a_test_litters(tmp_path):
    _write_scoped_tree(tmp_path, litter=True)
    proc = _run_scoped_pytest(tmp_path)

    assert proc.returncode != 0, (
        "a littering test must fail the run\n" + proc.stdout + proc.stderr
    )
    assert "CLEAN-CHECKOUT TRIPWIRE" in proc.stdout
    assert "leaked-artifact.txt" in proc.stdout


@pytest.mark.slow
def test_tripwire_stays_quiet_when_nothing_leaks(tmp_path):
    _write_scoped_tree(tmp_path, litter=False)
    proc = _run_scoped_pytest(tmp_path)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CLEAN-CHECKOUT TRIPWIRE" not in proc.stdout


@pytest.mark.slow
def test_escape_hatch_disarms_it(tmp_path):
    _write_scoped_tree(tmp_path, litter=True)
    proc = _run_scoped_pytest(tmp_path, {tripwire.ALLOW_ENV: "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CLEAN-CHECKOUT TRIPWIRE" not in proc.stdout
