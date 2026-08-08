"""Fresh-workspace tripwire — the out-of-the-box invariant, runtime-data half.

Jeremy's DECREE (2026-07-28): "Functionality that we add should presume that
it's 'out of the box' functionality for the project day 1 — unless it's
specifically functionality gated on prior learning data"; corollary, "the work
we're doing should be able to be verified on a clean clone of the maro
repository." Declared in `docs/HOUSE_STYLE.md`, whose own house rule is that a
principle ships with the test that could fail it or it is labeled prose-only.

The CLEAN-CHECKOUT half shipped 2026-07-29 (`tests/_checkout_tripwire.py` —
the suite must not litter the working tree). This is the other half named in
that item and left open: **entry points must behave against an EMPTY
`~/.maro/workspace`**, i.e. a box that has never run anything.

Why this can fail rather than being decoration: every command here reads
runtime stores (skill stats, outcomes, manifests, operator status). The failure
mode is a store reader that assumes its file already exists — the same shape as
the historical packaging bug this invariant was written for, which was masked
locally by `PYTHONPATH=src` and caught only by a clean-machine trial in 2026-07.
A new `open(path)` without a missing-file path anywhere under these commands
turns this file red.

Deliberately NOT covered: subcommands argparse rejects before any store read
(`manifest` needs a project, `outcomes` needs a sub-verb). Asserting on those
would exercise argparse, not the invariant — a check that cannot fail proves
nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI = REPO_ROOT / "src" / "cli.py"

# Read-only commands that reach the runtime stores. Verified 2026-08-08 to exit
# 0 against an empty workspace; this list is the pin, not a wish.
#
# `status` is DELIBERATELY ABSENT despite being the obvious candidate: it makes
# a live LLM call (its output is generated prose, and it reads the working
# tree's git diff). Measured 2026-08-08 — `status` 10.44s, every other command
# here ~0.07s; it was the entire cost of this file. A tripwire must not spend
# money or require a network to pass. That `status` needs a configured backend
# at all is itself an out-of-the-box question, filed in BACKLOG rather than
# asserted here.
READ_ONLY_COMMANDS = [
    "skill-stats",
    "skills",
    "memory",
    "opstatus",
    "metrics",
    "attribution",
    "map",
    "autonomy",
    "inspector-status",
    "mission-status",
]


def _run(command: str, workspace: Path) -> subprocess.CompletedProcess:
    """Invoke a CLI command with the workspace pinned at `workspace`.

    The environment is scrubbed of every workspace alias (`config.workspace_root`
    accepts three) so an ambient pin on the developer's box cannot mask a
    fresh-workspace failure — that masking is exactly how the original
    out-of-the-box violation survived for months.
    """
    env = dict(os.environ)
    for var in ("MARO_WORKSPACE", "OPENCLAW_WORKSPACE", "WORKSPACE_ROOT"):
        env.pop(var, None)
    env["MARO_WORKSPACE"] = str(workspace)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, str(CLI), command],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(REPO_ROOT),
    )


def test_read_only_commands_survive_an_empty_workspace(tmp_path):
    """A box that has never run anything must still answer read-only questions.

    All ten commands run CONCURRENTLY, each in its own interpreter against its
    own empty workspace — isolation identical to running them one at a time, at
    roughly the wall cost of one. Measured while building this: as eleven
    serial parametrized tests (with `status` still in the list) the file cost
    37.6s against a 25s suite; concurrent and with `status` removed it is 4.6s.
    A tripwire that triples the suite is a tripwire people start skipping.

    Deliberately one test rather than ten parametrized ones: under xdist a
    parametrized sweep spreads across workers, and each worker would then pay
    its own process-pool cost. Every command is still reported INDEPENDENTLY —
    the assertion names each failing command with its own output, so a single
    break points at a single command, not at "the workspace tests".
    """
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    def _probe(command: str):
        workspace = workspace_root / command
        workspace.mkdir()
        return command, _run(command, workspace)

    with ThreadPoolExecutor(max_workers=len(READ_ONLY_COMMANDS)) as pool:
        results = list(pool.map(_probe, READ_ONLY_COMMANDS))

    failures = []
    for command, proc in results:
        combined = proc.stdout + proc.stderr
        if "Traceback" in combined:
            failures.append(
                f"`maro {command}` CRASHED against an empty workspace — a store "
                f"reader is assuming its file already exists:\n{combined[-800:]}"
            )
        elif proc.returncode != 0:
            failures.append(
                f"`maro {command}` exited {proc.returncode} against an empty "
                f"workspace:\n{combined[-800:]}"
            )

    assert not failures, (
        f"{len(failures)} of {len(READ_ONLY_COMMANDS)} read-only entry points "
        f"broke the out-of-the-box invariant:\n\n" + "\n\n".join(failures)
    )


def test_workspace_directory_need_not_pre_exist(tmp_path):
    """First run on a new box: the workspace path itself is absent.

    Distinct from the empty-directory case above — `mkdir(exist_ok=True)` on a
    leaf whose parent is missing raises, so a reader can pass the empty case and
    still fail here.
    """
    workspace = tmp_path / "never" / "created"
    proc = _run("skill-stats", workspace)
    combined = proc.stdout + proc.stderr
    assert "Traceback" not in combined, combined[-1500:]
    assert proc.returncode == 0, combined[-1500:]


def test_read_only_commands_are_real_subcommands(tmp_path):
    """Guard the guard: if a command is renamed, the list must go red rather
    than silently testing nothing. argparse answers 2 for an unknown command,
    so a stale name in the list fails the sweep above with a usage error rather
    than passing vacuously — this test pins that contract explicitly, since the
    sweep's value rests entirely on it."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert READ_ONLY_COMMANDS, "the pin list must not be empty"
    proc = _run("definitely-not-a-command", workspace)
    assert proc.returncode == 2, (
        "expected argparse to reject an unknown subcommand with exit 2; if this "
        "changed, the parametrized pin above may be passing vacuously"
    )


def test_empty_workspace_run_does_not_write_into_the_repo(tmp_path):
    """The out-of-the-box corollary: verifiable on a clean clone.

    Complements the session-level clean-checkout tripwire by catching a write
    at the moment it happens, attributed to one command, rather than as an
    end-of-suite diff nobody can attribute.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = {p for p in (REPO_ROOT / "output").glob("*")} if (REPO_ROOT / "output").is_dir() else set()
    _run("skill-stats", workspace)
    after = {p for p in (REPO_ROOT / "output").glob("*")} if (REPO_ROOT / "output").is_dir() else set()
    assert after == before, f"`maro skill-stats` wrote into the repo: {after - before}"
