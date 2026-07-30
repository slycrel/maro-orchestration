"""Shell-level command-shape probes for scripts/test-safe.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent


def _write_fake(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _probe(tmp_path: Path, *, with_taskset: bool,
           options: tuple[str, ...] = (),
           target: str | None = "tests/test_run_curation.py",
           ) -> subprocess.CompletedProcess:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
    fake_python = tmp_path / "python"
    _write_fake(fake_python, "exit 0\n")
    _write_fake(
        fake_bin / "nice",
        'printf "nice:%s\\n" "$*" >> "$TEST_SAFE_LOG"\nexit 0\n',
    )
    _write_fake(fake_bin / "dirname", 'exec /usr/bin/dirname "$@"\n')
    if with_taskset:
        _write_fake(
            fake_bin / "taskset",
            'printf "taskset:%s\\n" "$*" >> "$TEST_SAFE_LOG"\nexit 0\n',
        )
    env = os.environ.copy()
    # PATH is ONLY fake_bin: dirname is faked explicitly (absolute exec) and
    # python is forced via TEST_PYTHON, so no other PATH entry is needed for
    # this code path. This matters because on merged-/usr Linux (e.g. Ubuntu),
    # /bin is a symlink to /usr/bin — including it here would leak the host's
    # real taskset into "taskset unavailable" probes.
    env.update({
        "PATH": str(fake_bin),
        "TEST_SAFE_LOG": str(log),
        "TEST_PYTHON": str(fake_python),
    })
    argv = ["/bin/bash", "scripts/test-safe.sh", *options]
    if target is not None:
        argv.append(target)
    result = subprocess.run(
        argv,
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    # The sequential chunking path shells out to mktemp/grep/sort/split, none
    # of which exist on the deliberately-minimal fake PATH — so a probe that
    # reaches it dies before logging. That's fine: those probes assert on
    # stderr instead, and an absent log reads as "no command was issued".
    result.command_log = log.read_text(encoding="utf-8") if log.exists() else ""
    result.fake_python = str(fake_python)
    return result


def test_test_safe_uses_nice_without_taskset(tmp_path):
    result = _probe(tmp_path, with_taskset=False)

    assert result.returncode == 0, result.stderr
    assert "taskset unavailable" in result.stderr
    assert result.command_log == (
        f"nice:-n 15 {result.fake_python} -m pytest "
        "tests/test_run_curation.py -m not slow or slow --tb=short -q -rs\n"
    )


def test_test_safe_adds_affinity_when_taskset_exists(tmp_path):
    result = _probe(tmp_path, with_taskset=True)

    assert result.returncode == 0, result.stderr
    assert "cores=0,1, nice=15" in result.stderr
    assert result.command_log == (
        f"nice:-n 15 taskset -c 0,1 {result.fake_python} -m pytest "
        "tests/test_run_curation.py -m not slow or slow --tb=short -q -rs\n"
    )


def test_test_safe_cli_resource_overrides_reach_command(tmp_path):
    result = _probe(
        tmp_path,
        with_taskset=True,
        options=("--cores", "2,3", "--nice", "7"),
    )

    assert result.returncode == 0, result.stderr
    assert "cores=2,3, nice=7" in result.stderr
    assert result.command_log == (
        f"nice:-n 7 taskset -c 2,3 {result.fake_python} -m pytest "
        "tests/test_run_curation.py -m not slow or slow --tb=short -q -rs\n"
    )


def test_test_safe_fast_mode_reaches_pytest(tmp_path):
    result = _probe(tmp_path, with_taskset=False, options=("--fast",))

    assert result.returncode == 0, result.stderr
    assert "mode=fast" in result.stderr
    assert result.command_log == (
        f"nice:-n 15 {result.fake_python} -m pytest "
        "tests/test_run_curation.py -m not slow --tb=short -q -rs\n"
    )


# --- parallel full-suite lane (2026-07-29) --------------------------------

@pytest.mark.parametrize("with_taskset,cores,expected_n", [
    # Pinned to an affinity mask: workers must not exceed the cores in it,
    # or the box gets an oversubscribed pool fighting over 2 CPUs.
    (True, None, "2"),          # default CORES=0,1
    (True, "0-3", "4"),         # ranges expand, not count as one entry
    (True, "1,4-5", "3"),       # mixed list + range
    # No affinity mask (macOS): let xdist size the pool to the host.
    (False, None, "auto"),
])
def test_test_safe_sizes_worker_pool_to_the_core_budget(
        tmp_path, with_taskset, cores, expected_n):
    options = ("--cores", cores) if cores else ()
    result = _probe(tmp_path, with_taskset=with_taskset,
                    options=options, target=None)

    assert result.returncode == 0, result.stderr
    assert f"-n {expected_n}" in result.stderr, result.stderr
    assert f"-m not slow or slow -n {expected_n} --tb=short -q -rs" in result.command_log, (
        result.command_log)


def test_test_safe_jobs_one_takes_the_sequential_chunking_path(tmp_path):
    """`--jobs 1` must bypass xdist entirely — the escape hatch for debugging
    an ordering-dependent failure that only reproduces serially."""
    result = _probe(tmp_path, with_taskset=False,
                    options=("--jobs", "1"), target=None)

    # The parallel lane announces itself and then execs pytest; taking it would
    # show up in both places. Neither may happen.
    assert "full suite, -n" not in result.stderr, result.stderr
    assert result.command_log == "", result.command_log
