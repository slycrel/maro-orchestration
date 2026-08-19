"""Must-detect fixtures for the destructive-rewrite scanner.

"Found 0" is untrusted until fixtures prove the instrument can find.
Written after adversarial r4 (2026-08-17), where three lenses
independently found the scanner blind to the exact split-helper shape the
skills.py fix had just introduced — a scanner that silently omits a shape
reports "clean" for it forever.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from scan_destructive_rewrites import scan_module


def _scan(src: str):
    return {name: verdict.strip()
            for verdict, _lineno, name in scan_module(ast.parse(src))}


class TestTheScannerFindsTheShapesItExistsFor:
    def test_the_single_function_shape(self):
        # drop + write-back in one function — the original shape
        src = '''
def rewrite(path):
    out = []
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        out.append(json.dumps(d))
    path.write_text("\\n".join(out))
'''
        assert _scan(src) == {"rewrite": "RISK"}

    def test_the_nested_closure_shape(self):
        # the rmw-callback shape: inner scans, outer writes
        src = '''
def outer(path):
    def _merge(old):
        out = []
        for line in old.splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue
            out.append(json.dumps(d))
        return "\\n".join(out)
    locked_rmw(path, _merge)
'''
        got = _scan(src)
        assert got.get("outer._merge") == "RISK"

    def test_the_split_helper_shape(self):
        # THE r4 blind spot: read-helper + write-helper + orchestrator,
        # all three top-level. None of them individually holds both
        # splitlines and a write marker.
        src = '''
def _read(path):
    rows = {}
    for line in path.read_text().splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        rows[d["id"]] = d
    return rows

def _write(path, rows):
    atomic_write(path, "\\n".join(json.dumps(r) for r in rows.values()))

def bump(path, key):
    rows = _read(path)
    rows[key] = {"id": key}
    _write(path, rows)
'''
        got = _scan(src)
        assert got.get("_read") == "RISK", (
            "the read-helper must be flagged: a caller writes back what it "
            f"returns, so its drop is durable — got {got}")

    def test_a_read_only_loop_is_not_flagged(self):
        # Negative control: same drop, no write-back anywhere. Recoverable
        # (the bytes stay on disk) and therefore out of THIS scanner's
        # scope — test_no_silent_drop.py owns the silence half.
        src = '''
def load(path):
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
'''
        assert _scan(src) == {}

    def test_a_taint_refusing_rewrite_reads_as_ok(self):
        # Negative control for the verdict: the fixed shape.
        src = '''
def rewrite(path, key):
    out = []
    for line in path.read_text().splitlines():
        try:
            if loads_clean(line).get("id") == key:
                continue
        except Exception:
            pass
        out.append(line)
    atomic_write(path, "\\n".join(out))
'''
        assert _scan(src) == {"rewrite": "OK"}


class TestTheScannerStillSeesTheRealFixedSurfaces:
    def test_the_live_skills_helpers_are_visible(self):
        # Regression pin for the r4 finding: these three were invisible
        # (not OK, not RISK — absent) before the call-graph leg landed.
        src = (Path(__file__).parent.parent / "src" / "skills.py").read_text(
            encoding="utf-8", errors="surrogateescape")
        got = _scan(src)
        assert got.get("_read_skill_stats") == "OK"
        assert got.get("_save_skills") == "OK"
        assert got.get("save_skill") == "OK"
