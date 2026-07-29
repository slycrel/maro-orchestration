"""Clean-checkout tripwire — the mechanism, kept separate so it is testable.

Decree (Jeremy, 2026-07-28): work should presume it is "out of the box"
functionality on day 1, and "should be able to be verified on a clean clone
of the maro repository."

A declared invariant with no tripwire is how it gets violated for months
without anyone noticing. Two proofs, both found by accident rather than by a
check:

- 2026-07-09: pip packaging had NEVER worked — the flat ``src/`` layout meant
  pip installed zero modules and every entry point raised ModuleNotFoundError.
  Masked locally by ``PYTHONPATH=src``; caught only by a docker clean-machine
  trial.
- 2026-07-29: ``tests/test_build_loop_script.py`` cleaned up its inputs but
  not its outputs, leaking one run dir per suite execution into the checkout
  since 2026-06-21. 107 run dirs plus 110 heartbeat records had piled up —
  every one of them the same synthetic "repo local" smoke item — before
  anyone looked.

Both leaks landed in *gitignored* directories, which is why ``git status``
never surfaced them and why this walks the filesystem instead of asking git.

Only ADDITIONS are flagged. A test that rewrites a tracked file is something
git will show you; an untracked file dropped into ``output/`` is not.
"""

from __future__ import annotations

import os
from pathlib import Path

ALLOW_ENV = "MARO_ALLOW_CHECKOUT_WRITES"

# Regenerable dev artifacts — noise, not leaks. Deliberately does NOT include
# output/ or memory/: those are precisely where the real leaks landed.
PRUNE_DIRS = frozenset(
    {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
)
PRUNE_DIR_PREFIXES = (".venv",)
IGNORE_SUFFIXES = (".pyc", ".pyo")
IGNORE_NAMES = frozenset({".coverage"})

_MAX_LISTED = 20


def snapshot(root: Path) -> set[str]:
    """Relative paths of every file under ``root``, minus regenerable noise."""
    seen: set[str] = set()
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in PRUNE_DIRS and not d.startswith(PRUNE_DIR_PREFIXES)
        ]
        for name in filenames:
            if name in IGNORE_NAMES or name.endswith(IGNORE_SUFFIXES):
                continue
            seen.add(str(Path(dirpath, name).relative_to(root)))
    return seen


def format_report(added: list[str]) -> str:
    shown = added[:_MAX_LISTED]
    listing = "\n".join(f"    {p}" for p in shown)
    if len(added) > len(shown):
        listing += f"\n    ... and {len(added) - len(shown)} more"
    return (
        f"\nCLEAN-CHECKOUT TRIPWIRE: the suite left {len(added)} new file(s) "
        f"in the working tree.\n{listing}\n"
        "\nTests must clean up what they create — outputs, not just inputs. A "
        "leak here is invisible to `git status` when it lands in a gitignored "
        "directory, which is how 107 stale run dirs accumulated unnoticed "
        "(2026-06-21 → 2026-07-29). Snapshot the target dir before the work "
        "and remove only what your test produced; see "
        "tests/test_build_loop_script.py for the pattern.\n"
        f"Deliberate exception: set {ALLOW_ENV}=1.\n"
    )
