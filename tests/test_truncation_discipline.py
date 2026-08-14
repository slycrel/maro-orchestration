"""Truncation discipline tripwire — the review-round sweep, made permanent.

Three adversarial-review rounds of the 2026-08 truncation audit (BACKLOG
"Arbitrary-truncation audit") kept finding the same defect class: a bare
truncating slice ``<expr>[:N]`` on a value whose name says it carries
verdict/rationale prose (summary, reason, evidence, lesson, ...). Each
round fixed the sample it found; the population stayed. This test is the
population census, frozen:

- ``tests/data/truncation_inventory.json`` holds every KNOWN bare slice
  on a rationale-family name in ``src/``, keyed ``file::hint::cap`` with
  an occurrence count. Existing entries are legacy debt — visible,
  bounded, and burn-down-able (fix a site with clip()/a schema owner,
  then delete its row).
- A hit NOT in the inventory fails this test: new code does not get to
  add silent cuts on rationale-family values. Bound it with
  ``context_budget.clip`` (or a schema-owning writer) instead.
- An inventory row with no remaining hits also fails: delete the row so
  the debt count stays honest in both directions.

The scan is deliberately name-based and conservative — it catches the
class the reviewers kept catching, not every possible silent cut. That
is the point: it is the mechanical version of the sweep that found
rounds 11-13's medium/high findings, running on every test invocation.
"""
import ast
import json
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INVENTORY_PATH = Path(__file__).resolve().parent / "data" / "truncation_inventory.json"

FAMILY = re.compile(
    r"(summary|reason|reasoning|rationale|evidence|lesson|excerpt|why"
    r"|tried|verdict|stuck|gap|detail|error)", re.IGNORECASE)
CAP_MIN, CAP_MAX = 60, 5000


def _hint_candidates(node: ast.AST) -> list:
    """Every name-shaped identity a sliced expression might carry, in
    rough priority order. The caller prefers a FAMILY match over the
    first candidate (round-16 review: first-nonempty resolution let
    wrap(prefix, rationale)[:N] hide behind "prefix", and mapping
    subscripts like row["reason"][:N] carried no hint at all)."""
    out: list = []
    if isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, ast.Attribute):
        out.append(node.attr)
    elif isinstance(node, ast.Subscript):
        # row["reason"] — the string key is the identity.
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            out.append(sl.value)
        out.extend(_hint_candidates(node.value))
    elif isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name) and f.id == "str" and node.args:
            out.extend(_hint_candidates(node.args[0]))
        if isinstance(f, ast.Attribute) and f.attr == "get" and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append(a.value)
        if isinstance(f, ast.Name) and f.id == "getattr" and len(node.args) >= 2:
            a = node.args[1]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.append(a.value)
        if isinstance(f, ast.Attribute):
            # result.summary()[:N] — the METHOD name carries identity;
            # chained normalizers (.strip()) carry the base value's.
            out.append(f.attr)
            out.extend(_hint_candidates(f.value))
        # Conservative recursion into ANY call's arguments — a wrapper
        # like scrub(str(detail))[:N] transforms the value but not its
        # identity (round-15 review).
        for a in node.args:
            out.extend(_hint_candidates(a))
    elif isinstance(node, ast.BinOp):
        out.extend(_hint_candidates(node.left))
        out.extend(_hint_candidates(node.right))
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.FormattedValue):
                out.extend(_hint_candidates(v.value))
    elif isinstance(node, (ast.Tuple, ast.BoolOp)):
        elts = node.elts if isinstance(node, ast.Tuple) else node.values
        for e in elts:
            out.extend(_hint_candidates(e))
    return [h for h in out if h]


def _name_hint(node: ast.AST) -> str:
    candidates = _hint_candidates(node)
    for h in candidates:
        if FAMILY.search(h):
            return h
    return candidates[0] if candidates else ""


def scan_bare_family_slices() -> Counter:
    """Counter of 'file::hint::cap' for every bare truncating slice on a
    rationale-family name in src/."""
    hits: Counter = Counter()
    for path in sorted((REPO / "src").glob("*.py")):
        # Unparseable source is a hard failure, not a skip: a SyntaxError
        # silently excluded a whole module from the census once (the file
        # healed, and its every site then read as "new").
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Subscript):
                continue
            sl = node.slice
            if not (isinstance(sl, ast.Slice) and sl.lower is None
                    and sl.step is None
                    and isinstance(sl.upper, ast.Constant)
                    and isinstance(sl.upper.value, int)
                    and CAP_MIN <= sl.upper.value <= CAP_MAX):
                continue
            hint = _name_hint(node.value)
            if hint and FAMILY.search(hint):
                hits[f"src/{path.name}::{hint}::{sl.upper.value}"] += 1
    return hits


def test_no_new_silent_rationale_slices():
    inventory = json.loads(INVENTORY_PATH.read_text())
    current = scan_bare_family_slices()

    new = {k: n for k, n in current.items()
           if n > inventory.get(k, 0)}
    assert not new, (
        "NEW bare truncating slice(s) on rationale-family values — bound "
        "them with context_budget.clip() (or a schema-owning writer) "
        "instead of a silent cut. If a bare slice is genuinely justified "
        "(e.g. a PIPE_BUF-bounded row), say why in a comment AND add the "
        f"site to {INVENTORY_PATH.name} with its justification reviewed: "
        f"{sorted(new)}")

    stale = {k: n for k, n in inventory.items()
             if current.get(k, 0) < n}
    assert not stale, (
        "Inventory rows exceed the sites that remain — a debt site was "
        "fixed (good!) but its row was not updated; trim these in "
        f"{INVENTORY_PATH.name} so the burn-down stays honest: "
        f"{sorted(stale)}")


def test_inventory_only_shrinks_marker():
    """The frozen debt count, asserted as a ceiling with its vintage.

    2026-08-14 baseline: 176 occurrences across 129 sites. The number
    moves in two directions for two different reasons: fixes shrink it
    (168 → 163 in round 13), scanner upgrades GROW it by surfacing
    already-existing debt (round 14 added chained normalizers; round 16
    added mapping subscripts, method names, and family-preferring arg
    resolution — +12 pre-existing sites became visible). Raising the
    ceiling for a scanner upgrade is honest; raising it for new code is
    not — the per-site test above tells the two apart. Shrink it as the
    burn-down proceeds.
    """
    inventory = json.loads(INVENTORY_PATH.read_text())
    assert sum(inventory.values()) <= 176


def test_scanner_detects_each_supported_shape():
    """Must-detect fixtures (round-14 review: the scanner had been
    validated only against its own census, never against shapes designed
    to evade it)."""
    shapes = {
        "plain name": "x = summary[:300]\n",
        "attribute": "x = obj.reasoning[:600]\n",
        "str() wrap": "x = str(reason)[:300]\n",
        "dict get": 'x = d.get("stop_evidence", "")[:500]\n',
        "getattr": 'x = getattr(o, "rationale", "")[:300]\n',
        "chained strip": "x = str(rationale).strip()[:300]\n",
        "join over parts": 'x = " ".join(lesson)[:200]\n',
        "f-string": 'x = f"why: {why}"[:400]\n',
        # Round-16 evasion shapes: a distracting first argument, a
        # mapping subscript, and a method whose NAME is the identity.
        "distracting arg": "x = wrap(prefix, rationale)[:300]\n",
        "mapping subscript": 'x = row["reason"][:300]\n',
        "method name": "x = result.summary()[:400]\n",
    }
    for label, src in shapes.items():
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript):
                hint = _name_hint(node.value)
                found = found or bool(hint and FAMILY.search(hint))
        assert found, f"scanner missed the {label} shape: {src!r}"
