"""Shell-level command-shape probes for scripts/test-safe.sh."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parent.parent


def _write_fake(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _probe(tmp_path: Path, *, with_taskset: bool,
           options: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "commands.log"
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
    # /bin supplies dirname and bash but, on Linux, excludes /usr/bin/taskset.
    env.update({"PATH": f"{fake_bin}:/bin", "TEST_SAFE_LOG": str(log)})
    result = subprocess.run(
        ["/bin/bash", "scripts/test-safe.sh", *options,
         "tests/test_run_curation.py"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    result.command_log = log.read_text(encoding="utf-8")
    return result


def test_test_safe_uses_nice_without_taskset(tmp_path):
    result = _probe(tmp_path, with_taskset=False)

    assert result.returncode == 0, result.stderr
    assert "taskset unavailable" in result.stderr
    assert result.command_log == (
        f"nice:-n 15 {ROOT}/.venv/bin/python -m pytest "
        "tests/test_run_curation.py --tb=short -q\n"
    )


def test_test_safe_adds_affinity_when_taskset_exists(tmp_path):
    result = _probe(tmp_path, with_taskset=True)

    assert result.returncode == 0, result.stderr
    assert "cores=0,1, nice=15" in result.stderr
    assert result.command_log == (
        f"nice:-n 15 taskset -c 0,1 {ROOT}/.venv/bin/python -m pytest "
        "tests/test_run_curation.py --tb=short -q\n"
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
        f"nice:-n 7 taskset -c 2,3 {ROOT}/.venv/bin/python -m pytest "
        "tests/test_run_curation.py --tb=short -q\n"
    )
