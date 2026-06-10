"""Static sweep: no undefined names in src/ (session 40 regression class).

Session 40 found four production bugs of the same shape — an undefined name
inside a broad try/except (or a caller's), so the crash was swallowed and the
feature silently died:

- agent_loop._finalize_loop referenced `ctx.project` (no ctx param) — the
  entire Phase 44-45 post-loop self-reflection block was dead for six weeks.
- evolver.rewrite_skill lost its `verbose` param while both callers passed
  verbose=verbose — skill rewriting (circuit-breaker recovery) raised
  TypeError on every call.
- llm.py referenced bare `thinking_budget` in a fallback branch.
- agent_loop terminal-failure handler referenced bare `block_reason`.

pyflakes' undefined-name check catches all of these at import-graph level
with zero runtime cost. Annotation-only names (quoted forward refs) must be
imported under TYPE_CHECKING to stay out of this report.
"""

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyflakes", reason="pyflakes not installed")

SRC = Path(__file__).parent.parent / "src"


def test_no_undefined_names_in_src():
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *sorted(str(p) for p in SRC.glob("*.py"))],
        capture_output=True,
        text=True,
        timeout=120,
    )
    undefined = [
        line for line in proc.stdout.splitlines() if "undefined name" in line
    ]
    assert not undefined, (
        "Undefined names in src/ — this exact bug class silently killed the "
        "post-loop introspection block and skill rewriting (session 40):\n"
        + "\n".join(undefined)
    )
