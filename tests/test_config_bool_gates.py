"""R3-1 — the bool(config-getter) class, closed structurally.

``bool(get("some.flag", ...))`` on a config value is the C0.6/R2-7 bug
class: a quoted YAML value ("false", "0") arrives as a STRING and
``bool("false")`` is True — for flags that gate authority, persistence,
spend, pushes, or autonomy that error direction silently defeats the
operator's intent. The shared strict parse is ``config.parse_bool`` /
``config.get_bool``; every privileged gate was migrated in R3-1.

This tripwire greps src/ for the pattern and fails when a NEW site
appears that is not in the reviewed allowlist below (defaults-registry
census style: the fix for a new site is `config.get_bool`, not a new
allowlist row — a row is only for a genuinely cosmetic display/report
toggle, and needs a one-line justification). It also fails on STALE
rows so the allowlist can't quietly outlive the code it excuses.
"""
from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).parent.parent / "src"

# (?<![\w.]) — don't match parse_bool(get(...)) / _strict_bool(_cfg_get(...)),
# which are already strict; only a BARE bool( over a config getter is the bug.
_PATTERN = re.compile(
    r"(?<![\w.])bool\(\s*"
    r"(?:_cfg_get|_config_get|cfg_get|config_get|_get|get)\(\s*"
    r"(['\"])([^'\"]+)\1"
)

# Reviewed allowlist: (filename, config key) -> why a lax parse is tolerable.
# Every row is a COSMETIC display/report/diagnostic toggle where a misparsed
# string flips rendering or log verbosity, never authority/persistence/spend/
# pushes/autonomy. Migrating one of these to get_bool is always allowed —
# then DELETE its row (stale rows fail this test).
ALLOWED = {
    # Read-only health probes + captain's-log narration; diagnostic lane.
    ("system_health.py", "health.probes_enabled"):
        "diagnostic probes/narration toggle — no authority or spend",
    # Rerun brief is a rendering block in the dispatch prompt.
    ("rerun_identity.py", "rerun.brief"):
        "display: whether the rerun brief section is rendered",
    # Report HTML generation + debug snapshots: output artifacts only.
    ("loop_report.py", "report.enabled"):
        "display: report rendering toggle",
    ("loop_report.py", "report.debug_snapshots"):
        "display: extra debug snapshot files in reports",
    # Recall ranking weight — shapes ordering of recalled lessons, not a
    # gate on any action.
    ("portability.py", "recall.portability_weighting"):
        "ranking weight in recall ordering — not a gate",
    # Log level only.
    ("loop_types.py", "debug"):
        "diagnostic: DEBUG log level toggle",
    # Age stamps are display suffixes on recalled lessons.
    ("age_stamp.py", "memory.age_stamps"):
        "display: age-stamp suffix on recall render",
}


def _census():
    found = []
    for path in sorted(SRC.glob("*.py")):
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            for m in _PATTERN.finditer(line):
                found.append((path.name, m.group(2), lineno))
    return found


def test_no_new_bool_config_gate_sites():
    """A new bare bool(<config getter>("key", ...)) site must use
    config.get_bool instead — or, for a genuinely cosmetic toggle, add a
    justified allowlist row above."""
    found = _census()
    new = [(f, k, ln) for f, k, ln in found if (f, k) not in ALLOWED]
    assert not new, (
        "new bool(config-getter) site(s) — use config.get_bool (strict "
        "parse; 'false' must not read as True), or add a justified "
        f"allowlist row if truly cosmetic: {new}"
    )


def test_allowlist_has_no_stale_rows():
    """Every allowlist row must still match a live site — a migrated or
    deleted site must take its row with it."""
    live = {(f, k) for f, k, _ in _census()}
    stale = [row for row in ALLOWED if row not in live]
    assert not stale, f"stale allowlist rows (site migrated/removed): {stale}"


# ---------------------------------------------------------------------------
# Migrated-gate behavior pins: the string "false" must gate OFF (was: truthy).
# ---------------------------------------------------------------------------

def _config_false_for(monkeypatch, key):
    import config
    monkeypatch.setattr(
        config, "get",
        lambda k, default=None: "false" if k == key else default)


def test_persistence_install_config_string_false_blocks(monkeypatch):
    """R3-1 must-detect: constraints.allow_persistence_install quoted
    "false" opened the persistence-install gate pre-fix."""
    import constraint
    monkeypatch.delenv("MARO_PERSISTENCE_ALLOW", raising=False)
    _config_false_for(monkeypatch, "constraints.allow_persistence_install")
    assert constraint._persistence_allowed() is False


def test_closure_audit_gates_config_string_false_stay_off(monkeypatch):
    """R3-1: closure.pass_audit / closure.verdict_audit (spend adapter
    calls, can degrade/reverse verdict trust) — "false" stays OFF."""
    import closure_verify
    _config_false_for(monkeypatch, "closure.pass_audit")
    assert closure_verify._pass_audit_enabled() is False
    _config_false_for(monkeypatch, "closure.verdict_audit")
    assert closure_verify._verdict_audit_enabled() is False
