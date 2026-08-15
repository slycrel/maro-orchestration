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


# --- second census: verdict-tuple bypass writers ---------------------------
# Round-17 Architect: every stale-tuple bug this arc fixed traces to raw
# write_metadata/stamp_run_metadata calls carrying goal_verdict_*/stop
# keys — merge semantics that can leave siblings standing. The schema
# owners in runs.py exist; this census freezes the bypass population the
# same way the slice census does: known sites are inventoried debt, NEW
# sites fail (route them through stamp_run_verdict /
# stamp_delivered_now_retry / the clear helpers instead).

_TUPLE_KEYS = frozenset((
    "goal_achieved", "goal_verdict_source", "goal_verdict_confidence",
    "goal_verdict_summary", "goal_verdict_downgrade_reason",
    "goal_verdict_gaps", "stop_verdict", "stop_evidence",
))
_RAW_WRITERS = ("write_metadata", "stamp_run_metadata")

# Frozen 2026-08-14 at 12 entries; BURNED TO ZERO 2026-08-15 (every site
# routed through a runs.py schema owner: stamp_run_verdict[+extra],
# stamp_run_stop_verdict, stamp_unjudged_verdict_source,
# stamp_run_verdict_contested). The inventory is {} and must stay {} —
# any hit is a NEW bypass. Each entry is file::writer::key. Burn down by
# routing the site through a runs.py schema owner, then delete its rows.
KNOWN_BYPASSES_PATH = (Path(__file__).resolve().parent / "data"
                       / "verdict_bypass_inventory.json")


def _verdict_bypass_hits(tree: ast.AST, filename: str) -> Counter:
    """Bypass hits for one parsed module. Split out so the must-detect
    fixtures can feed source strings through the REAL scanner (round-14
    lesson: a scanner validated only against its own census misses the
    shapes designed to evade it)."""
    hits: Counter = Counter()
    # Raw locked_rmw merges over metadata.json — the director shape
    # (2026-08-15 burn-down): a bare read-modify-write that no
    # writer-name census can see, and that skipped index_run_dir.
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        is_rmw = ((isinstance(callee, ast.Name)
                   and callee.id == "locked_rmw")
                  or (isinstance(callee, ast.Attribute)
                      and callee.attr == "locked_rmw"))
        if not is_rmw:
            continue
        if any(isinstance(sub, ast.Constant)
               and sub.value == "metadata.json"
               for sub in ast.walk(node)):
            hits[f"{filename}::locked_rmw::metadata.json"] += 1
    # Map aliases of the raw writers imported from runs.
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "runs":
            for a in node.names:
                if a.name in _RAW_WRITERS:
                    aliases[a.asname or a.name] = a.name
    if not aliases:
        return hits
    # Var-flow: dict literals assigned to a NAME, plus later
    # NAME["key"] = ... additions, count when the NAME reaches a raw
    # writer (2026-08-15 burn-down: the NOW lane's `_now_extra` and the
    # provenance lane's `_prov_extra` were invisible to the
    # literal-in-call scan — the two biggest real bypasses). Tracking is
    # file-global: a same-named var in another function can over-count,
    # which errs toward detection, never past it.
    var_keys: dict = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if isinstance(node.value, ast.Dict):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    var_keys.setdefault(tgt.id, set()).update(
                        k.value for k in node.value.keys
                        if isinstance(k, ast.Constant))
        tgt = node.targets[0]
        if (isinstance(tgt, ast.Subscript)
                and isinstance(tgt.value, ast.Name)
                and tgt.value.id in var_keys
                and isinstance(tgt.slice, ast.Constant)):
            var_keys[tgt.value.id].add(tgt.slice.value)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in aliases):
            continue
        writer = aliases[node.func.id]
        dicts = [a for a in node.args if isinstance(a, ast.Dict)]
        dicts += [kw.value for kw in node.keywords
                  if isinstance(kw.value, ast.Dict)]
        for d in dicts:
            for key_node in d.keys:
                if (isinstance(key_node, ast.Constant)
                        and key_node.value in _TUPLE_KEYS):
                    hits[f"{filename}::{writer}::{key_node.value}"] += 1
        names = [a for a in node.args if isinstance(a, ast.Name)]
        names += [kw.value for kw in node.keywords
                  if isinstance(kw.value, ast.Name)]
        for nm in names:
            for key in sorted(var_keys.get(nm.id, ())):
                if key in _TUPLE_KEYS:
                    hits[f"{filename}::{writer}::{key}"] += 1
    return hits


def scan_verdict_bypasses() -> Counter:
    hits: Counter = Counter()
    for path in sorted((REPO / "src").glob("*.py")):
        if path.name == "runs.py":
            continue   # the schema owners themselves
        tree = ast.parse(path.read_text())
        hits.update(_verdict_bypass_hits(tree, f"src/{path.name}"))
    return hits


def test_no_new_verdict_tuple_bypasses():
    inventory = json.loads(KNOWN_BYPASSES_PATH.read_text())
    current = scan_verdict_bypasses()
    new = {k: n for k, n in current.items() if n > inventory.get(k, 0)}
    assert not new, (
        "NEW raw-writer call carrying verdict/stop tuple keys — merge "
        "semantics leave omitted siblings standing (six review rounds of "
        "evidence). Route it through runs.stamp_run_verdict / "
        "stamp_delivered_now_retry / the clear helpers, or add the site "
        f"to {KNOWN_BYPASSES_PATH.name} with a reviewed justification: "
        f"{sorted(new)}")
    stale = {k: n for k, n in inventory.items() if current.get(k, 0) < n}
    assert not stale, (
        f"Bypass fixed but inventory not trimmed — update "
        f"{KNOWN_BYPASSES_PATH.name}: {sorted(stale)}")


def test_bypass_scanner_detects_each_supported_shape():
    """Must-detect fixtures for the bypass census (same discipline as the
    slice scanner's): each shape below is a real evasion the 2026-08-15
    burn-down found in live code or closed pre-emptively."""
    shapes = {
        "literal in call": (
            "from runs import stamp_run_metadata\n"
            'stamp_run_metadata({"stop_verdict": v})\n'),
        "aliased import": (
            "from runs import write_metadata as _wm\n"
            '_wm(rd, extra={"goal_achieved": False})\n'),
        "var-assigned dict": (
            "from runs import write_metadata\n"
            'extra = {"goal_verdict_source": "x"}\n'
            "write_metadata(rd, extra=extra)\n"),
        "var grown by subscript": (
            "from runs import write_metadata\n"
            "extra = {}\n"
            'extra["stop_evidence"] = e\n'
            "write_metadata(rd, extra=extra)\n"),
        "positional var": (
            "from runs import stamp_run_metadata\n"
            'fields = {"stop_verdict": v}\n'
            "stamp_run_metadata(fields)\n"),
        "bare locked_rmw on metadata.json": (
            "from file_lock import locked_rmw\n"
            'locked_rmw(rd / "metadata.json", merge)\n'),
        "attribute locked_rmw": (
            "import file_lock\n"
            'file_lock.locked_rmw(rd / "metadata.json", merge)\n'),
    }
    for label, src in shapes.items():
        hits = _verdict_bypass_hits(ast.parse(src), "fixture.py")
        assert hits, f"bypass scanner missed the {label} shape: {src!r}"


def test_bypass_scanner_ignores_benign_writes():
    """Non-tuple keys through the raw writers stay legal — the census
    polices the verdict/stop family, not metadata writes in general."""
    benign = (
        "from runs import stamp_run_metadata\n"
        'stamp_run_metadata({"audit_incomplete": True})\n'
        'fields = {"loop_ids": ids}\n'
        "stamp_run_metadata(fields)\n")
    assert not _verdict_bypass_hits(ast.parse(benign), "fixture.py")
