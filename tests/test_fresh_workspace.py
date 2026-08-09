"""Fresh-workspace tripwire — the out-of-the-box invariant, RUNTIME-DATA half.

Jeremy's DECREE (2026-07-28): "Functionality that we add should presume that
it's 'out of the box' functionality for the project day 1 — unless it's
specifically functionality gated on prior learning data"; corollary, "the work
we're doing should be able to be verified on a clean clone of the maro
repository." Declared in `docs/HOUSE_STYLE.md`, whose own house rule is that a
principle ships with the test that could fail it or it is labeled prose-only.

**Scope, stated honestly.** The invariant has two halves and this file pins one:

  * PACKAGING half — does a pip-installed copy expose working entry points?
    That is `tests/test_packaging.py` plus the clean-machine docker trial. NOT
    this file.
  * RUNTIME-DATA half — do entry points behave on a box that has never run
    anything, i.e. against an empty `~/.maro/workspace`? That is this file.

The distinction matters because an earlier draft of this docstring claimed the
packaging half too, while the harness injected `PYTHONPATH=src` — the exact
masking it named as the historical failure. Adversarial review caught it. The
harness now prefers the installed console script when one exists and says so
via `HARNESS_USES_CONSOLE_SCRIPT`, but cannot require it: the Linux runtime box
runs from source with no `maro` on PATH, so demanding the console script would
red the suite on the machine that matters most.

Why this can fail rather than being decoration: every probe below reaches a
runtime store. The failure mode is a store reader that assumes its file already
exists. A new `open(path)` without a missing-file branch anywhere under these
commands turns this file red.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "src" / "cli.py"

# Prefer the installed console script — it exercises the shipped entry point
# rather than a hand-built interpreter invocation. Absent on the Linux runtime
# box (source checkout, nothing pip-installed), so this is a preference and the
# fallback is explicit rather than silent.
CONSOLE_SCRIPT = Path(sys.executable).parent / "maro"
HARNESS_USES_CONSOLE_SCRIPT = CONSOLE_SCRIPT.is_file() and os.access(CONSOLE_SCRIPT, os.X_OK)

# Every alias that can redirect storage away from the pinned workspace. Scrubbed
# before each probe: an ambient override on a developer box pointing at populated
# memory would let a probe read real stores and mask the missing-store failure
# under test — which is how the original violation survived for months.
_STORAGE_ENV_ALIASES = (
    "MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT",
    "MARO_MEMORY_DIR", "MARO_ORCH_ROOT", "MARO_USER_DIR", "MARO_ENV_FILE",
)

# argv VECTORS, not bare command names. Bare `skills` only prints a usage hint —
# it never opens a store, so pinning it would have been a check that could not
# fail. Adversarial review caught that; `--list` and `--status` are the readers.
READ_ONLY_PROBES = [
    ("skill-stats", ["skill-stats"]),
    ("skills --list", ["skills", "--list"]),
    ("skills --status", ["skills", "--status"]),
    ("memory", ["memory"]),
    ("opstatus", ["opstatus"]),
    ("metrics", ["metrics"]),
    ("attribution", ["attribution"]),
    ("map", ["map"]),
    ("autonomy", ["autonomy"]),
    ("inspector-status", ["inspector-status"]),
    ("mission-status", ["mission-status"]),
]

# `status` is DELIBERATELY ABSENT despite being the obvious candidate: it makes
# a live LLM call (its output is generated prose, and it reads the working
# tree's git diff). Measured 2026-08-08 on an empty workspace — `status` 10.44s,
# every probe here ~0.07s. A tripwire must not spend money or need a network to
# pass. That `status` needs a configured backend at all is itself an
# out-of-the-box question, filed in BACKLOG rather than asserted here.


def _run(argv: list[str], workspace: Path) -> subprocess.CompletedProcess:
    """Invoke one CLI probe with storage pinned at `workspace`."""
    env = dict(os.environ)
    for var in _STORAGE_ENV_ALIASES:
        env.pop(var, None)
    env["MARO_WORKSPACE"] = str(workspace)
    if HARNESS_USES_CONSOLE_SCRIPT:
        cmd = [str(CONSOLE_SCRIPT), *argv]
    else:
        cmd = [sys.executable, str(CLI), *argv]
        env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO_ROOT),
    )


def _assert_clean(label: str, proc: subprocess.CompletedProcess, state: str) -> None:
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, (
        f"`maro {label}` CRASHED against {state} — a store reader is assuming "
        f"its file already exists:\n{combined[-1200:]}"
    )
    assert proc.returncode == 0, (
        f"`maro {label}` exited {proc.returncode} against {state}:\n{combined[-1200:]}"
    )


@pytest.mark.parametrize("label,argv", READ_ONLY_PROBES, ids=[p[0] for p in READ_ONLY_PROBES])
def test_probe_survives_an_empty_workspace(label, argv, tmp_path):
    """A box that has never run anything must still answer read-only questions."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _assert_clean(label, _run(argv, workspace), "an empty workspace")


@pytest.mark.parametrize("label,argv", READ_ONLY_PROBES, ids=[p[0] for p in READ_ONLY_PROBES])
def test_probe_survives_an_absent_workspace(label, argv, tmp_path):
    """First run on a new box: the workspace path itself does not exist.

    A distinct failure from the empty case — `mkdir(exist_ok=True)` on a leaf
    whose PARENT is missing raises, so a reader can pass the empty sweep and
    still die here. Applied to every probe rather than one: the earlier draft
    checked a single command, so a missing-parent regression confined to any
    other command stayed green.
    """
    workspace = tmp_path / "never" / "created"
    _assert_clean(label, _run(argv, workspace), "an absent workspace path")


def test_unknown_subcommand_is_rejected(tmp_path):
    """Guard the guard: the sweeps' value rests on argv being real.

    If a command is renamed, its probe must go red rather than silently testing
    nothing. argparse answers 2 for an unknown subcommand, which fails the
    sweeps' `returncode == 0` assertion — this pins that contract so a stale
    entry cannot pass vacuously.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert READ_ONLY_PROBES, "the probe list must not be empty"
    proc = _run(["definitely-not-a-command"], workspace)
    assert proc.returncode == 2, (
        f"expected argparse exit 2 for an unknown subcommand, got "
        f"{proc.returncode}; the sweeps above may be passing vacuously"
    )
