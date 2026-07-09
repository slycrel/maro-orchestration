"""Census tripwire: pyproject's py-modules list must match src/*.py exactly.

src/ is a flat module layout, so setuptools can't auto-discover it —
`packages.find` silently installs NOTHING (2026-07-09 docker clean-install
trial: pip succeeded, every entry point crashed ModuleNotFoundError, and it
was never noticed because this box runs PYTHONPATH=src). The explicit
py-modules list in pyproject.toml is the fix; this test keeps it honest.
When this fails: add/remove the module name in pyproject's py-modules.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pyproject_modules() -> set:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r"py-modules = \[(.*?)\]", text, re.DOTALL)
    assert m, "pyproject.toml lost its py-modules list — pip installs nothing without it"
    return set(re.findall(r'"([a-z0-9_]+)"', m.group(1)))


def _src_modules() -> set:
    return {p.stem for p in (REPO_ROOT / "src").glob("*.py")}


def test_py_modules_matches_src():
    listed = _pyproject_modules()
    actual = _src_modules()
    missing = sorted(actual - listed)
    stale = sorted(listed - actual)
    assert not missing and not stale, (
        f"pyproject py-modules drifted from src/*.py — "
        f"missing (add): {missing}; stale (remove): {stale}"
    )


def test_src_stays_flat():
    """py-modules can't express subpackages — a src/ subdir with .py files
    would pass the census above yet silently ship broken (pip installs the
    flat modules, skips the subpackage, entry points crash on a clean
    machine — the exact 2026-07-09 bug, with a green tripwire vouching).
    When this fails (e.g. the Tier 4 subpackage move): switch pyproject to
    real packages (`[tool.setuptools.packages.find] where=["src"]` +
    __init__.py) and update this census to walk packages instead.
    """
    nested = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "src").rglob("*.py")
        if p.parent != REPO_ROOT / "src" and "__pycache__" not in p.parts
    )
    assert not nested, (
        f"src/ grew nested .py files that py-modules cannot package: {nested}"
    )


def test_entry_points_reference_real_modules():
    text = (REPO_ROOT / "pyproject.toml").read_text()
    scripts = re.findall(r'^[\w-]+ = "([a-z0-9_]+):', text, re.MULTILINE)
    actual = _src_modules()
    broken = sorted(set(scripts) - actual)
    assert not broken, f"console scripts point at modules not in src/: {broken}"
