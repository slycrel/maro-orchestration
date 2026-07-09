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


def _declared_packages() -> set:
    text = (REPO_ROOT / "pyproject.toml").read_text()
    m = re.search(r"^packages = \[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    return set(re.findall(r'"([a-z0-9_.]+)"', m.group(1))) if m else set()


def test_src_stays_flat():
    """py-modules can't express subpackages — a src/ subdir with .py files
    would pass the census above yet silently ship broken (pip installs the
    flat modules, skips the subpackage, entry points crash on a clean
    machine — the exact 2026-07-09 bug, with a green tripwire vouching).
    Exception: subdirs declared as real packages in pyproject's `packages`
    list (currently maro_assets) DO ship. Anything undeclared still fails.
    When this fails (e.g. the Tier 4 subpackage move): declare the package
    in pyproject (`packages = [...]`) or switch the whole layout to
    packages.find and update this census to walk packages instead.
    """
    declared = _declared_packages()
    nested = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in (REPO_ROOT / "src").rglob("*.py")
        if p.parent != REPO_ROOT / "src"
        and "__pycache__" not in p.parts
        and ".".join(p.relative_to(REPO_ROOT / "src").parent.parts) not in declared
    )
    assert not nested, (
        f"src/ grew nested .py files that py-modules cannot package "
        f"(declare in pyproject `packages` if intentional): {nested}"
    )


def test_declared_packages_have_init():
    """A dir declared in `packages` without __init__.py installs nothing."""
    for pkg in _declared_packages():
        init = REPO_ROOT / "src" / Path(*pkg.split(".")) / "__init__.py"
        assert init.exists(), f"declared package {pkg} missing {init}"


# Box-specific specs that must NEVER ship in the wheel (2026-07-09 survey,
# docs/audit-2026-07/persona-skill-survey.md §3): personal/user-model personas
# and test fixtures. They stay in the repo; the workspace override layer is
# where box-specific specs live at runtime.
_NEVER_SHIP = {
    "personas": {"jeremy", "poe", "companion", "garrytan", "psyche-researcher"},
    "skills": {"test_skill", "test_skill_p32skill", "updatable_skill"},
}


def test_assets_package_matches_ship_manifest():
    """maro_assets ships the curated skill/persona catalog as package data
    via per-file symlinks to the top-level files (build follows symlinks —
    proven on wheel AND sdist, 2026-07-09). SHIPPED in maro_assets/__init__.py
    is the manifest; this census fails if the symlink dirs drift from it,
    a symlink dangles, or a never-ship spec sneaks into the wheel.
    """
    from maro_assets import SHIPPED

    for kind in ("skills", "personas"):
        d = REPO_ROOT / "src" / "maro_assets" / kind
        assert d.is_dir(), f"{d} missing — packaged defaults ship empty"
        linked = {p.stem for p in d.glob("*.md")}
        assert linked == set(SHIPPED[kind]), (
            f"{kind} symlinks drifted from SHIPPED manifest — "
            f"unlinked (add symlink): {sorted(set(SHIPPED[kind]) - linked)}; "
            f"unmanifested (add to SHIPPED or rm): {sorted(linked - set(SHIPPED[kind]))}"
        )
        for p in d.glob("*.md"):
            assert p.resolve().is_file(), f"dangling symlink: {p} -> {p.resolve()}"
            assert p.resolve() == (REPO_ROOT / kind / p.name).resolve(), (
                f"{p} points outside top-level {kind}/"
            )
        leaked = set(SHIPPED[kind]) & _NEVER_SHIP[kind]
        assert not leaked, f"box-specific {kind} in the ship manifest: {sorted(leaked)}"
        # non-.md files in the package dir would be dropped by the *.md glob
        undroppable = sorted(
            p.name for p in d.iterdir() if not p.name.endswith(".md")
        )
        assert not undroppable, (
            f"maro_assets/{kind}/ grew non-.md entries the package-data glob "
            f"won't ship: {undroppable}"
        )


def test_assets_dir_resolves():
    from maro_assets import SHIPPED, assets_dir

    for kind in ("skills", "personas"):
        d = assets_dir(kind)
        assert d is not None and d.is_dir()
        assert {p.stem for p in d.glob("*.md")} == set(SHIPPED[kind])


def test_entry_points_reference_real_modules():
    text = (REPO_ROOT / "pyproject.toml").read_text()
    scripts = re.findall(r'^[\w-]+ = "([a-z0-9_]+):', text, re.MULTILINE)
    actual = _src_modules()
    broken = sorted(set(scripts) - actual)
    assert not broken, f"console scripts point at modules not in src/: {broken}"
