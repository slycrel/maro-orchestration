"""Tripwire: the restart/continuation depth caps must share one constant.

Was three independently-drifted magic numbers (4 / <3 / 2) across
loop_post_step.py's MARO_MAX_CONTINUATION_DEPTH default, handle.py's two
continuation_depth restart gates, and LoopContext.director_budget_ceiling
(docs/BACKEND_RESILIENCE_DESIGN.md "three depth caps"). Unified per
GOAL_BRAIN Decisions 2026-07-12 (backend-resilience ratification) to
loop_types.MAX_RESTART_DEPTH. This guards against a future edit
reintroducing a bare numeric literal instead of the shared constant.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import loop_types

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_director_budget_ceiling_matches_shared_constant():
    default = loop_types.LoopContext.__dataclass_fields__["director_budget_ceiling"].default
    assert default == loop_types.MAX_RESTART_DEPTH


def test_handle_restart_gates_reference_shared_constant():
    src = (REPO_ROOT / "src" / "handle.py").read_text()
    assert src.count("MAX_RESTART_DEPTH") >= 2, (
        "handle.py's director-restart and closure-restart continuation_depth "
        "gates must both reference loop_types.MAX_RESTART_DEPTH"
    )
    # No bare numeric continuation_depth comparison left behind.
    assert not re.search(r"continuation_depth[^\n]{0,20}<\s*\d", src)


def test_continuation_pass_default_references_shared_constant():
    src = (REPO_ROOT / "src" / "loop_post_step.py").read_text()
    assert "MAX_RESTART_DEPTH" in src, (
        "loop_post_step.py's MARO_MAX_CONTINUATION_DEPTH default must derive "
        "from loop_types.MAX_RESTART_DEPTH, not a hardcoded fallback"
    )
    assert 'os.environ.get("MARO_MAX_CONTINUATION_DEPTH", "4")' not in src


def test_doctor_reports_shared_default():
    src = (REPO_ROOT / "src" / "doctor.py").read_text()
    assert "MAX_RESTART_DEPTH" in src
