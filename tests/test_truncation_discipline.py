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

    2026-08-14 baseline: 176 occurrences across 129 sites; first
    burn-down tranche 2026-08-15 (knowledge_web + loop_execute swept to
    honest clip()) brought the ceiling down — keep ratcheting it with
    each tranche. The number
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
    assert sum(inventory.values()) <= 135   # 176 at freeze; tranche 1: -37; r4 sibling sweep: -4


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

# Frozen 2026-08-14 at 12 entries; BURNED DOWN 2026-08-15 (every site
# routed through a runs.py schema owner: stamp_run_verdict[+extra],
# stamp_run_stop_verdict, stamp_unjudged_verdict_source,
# stamp_run_verdict_contested). ONE reviewed-justified site remains —
# audit_repair's alignment patch (a partial set-or-pop on a FOREIGN run
# dir, atomic with its repair record; see the comment at the site) —
# surfaced by the round-2 scanner hardening, which the first cut could
# not see. Any other hit is a NEW bypass. Entries are file::writer::key;
# routing the site through a runs.py schema owner, then delete its rows.
KNOWN_BYPASSES_PATH = (Path(__file__).resolve().parent / "data"
                       / "verdict_bypass_inventory.json")


def _verdict_bypass_hits(tree: ast.AST, filename: str) -> Counter:
    """Bypass hits for one parsed module. Split out so the must-detect
    fixtures can feed source strings through the REAL scanner (round-14
    lesson: a scanner validated only against its own census misses the
    shapes designed to evade it)."""
    hits: Counter = Counter()
    # Paths bound to a name that contains the "metadata.json" constant —
    # `mp = rd / "metadata.json"` then `locked_rmw(mp, merge)` is the
    # exact idiom the schema owners themselves use, and it evaded the
    # first cut of this tripwire (round-2 review, executed probe).
    path_vars: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                isinstance(sub, ast.Constant)
                and sub.value == "metadata.json"
                for sub in ast.walk(node.value)):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    path_vars.add(tgt.id)
    # Raw locked_rmw merges over metadata.json that WRITE tuple keys —
    # the director shape (2026-08-15 burn-down): a bare
    # read-modify-write no writer-name census can see. Scoped to the
    # verdict/stop family: legitimate non-verdict merges (audit-repair
    # reconciliation, the stranded sweep's status/pause stamp) stay
    # legal. The merge callable is resolved by name to its FunctionDef
    # and its body scanned; an unresolvable callable falls back to
    # file-scope tuple-key presence (err toward detection).
    # ALL same-named FunctionDefs, unioned (round-3 review, executed
    # probe: a name→def dict resolved `_merge` to whichever same-named
    # function walked last, so a real bypass next to a benign
    # same-named merge was scored against the wrong body and silently
    # missed — order-dependent. Union errs toward detection: if ANY
    # candidate body writes a tuple key, the site flags.)
    fndefs: dict = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef):
            fndefs.setdefault(n.name, []).append(n)

    def _writes_tuple_key(scopes) -> bool:
        for scope in scopes:
            for sub in ast.walk(scope):
                if (isinstance(sub, ast.Assign)
                        and isinstance(sub.targets[0], ast.Subscript)
                        and isinstance(sub.targets[0].slice, ast.Constant)
                        and sub.targets[0].slice.value in _TUPLE_KEYS):
                    return True
        return False

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
        inline = any(isinstance(sub, ast.Constant)
                     and sub.value == "metadata.json"
                     for sub in ast.walk(node))
        via_var = any(isinstance(a, ast.Name) and a.id in path_vars
                      for a in node.args)
        if not (inline or via_var):
            continue
        scopes = [tree]
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
            named = fndefs.get(node.args[1].id)
            if named:
                scopes = named
        if _writes_tuple_key(scopes):
            hits[f"{filename}::locked_rmw::metadata.json"] += 1
    # Map aliases of the raw writers: `from runs import X [as Y]` AND
    # module-attribute calls (`import runs [as r]; r.stamp_run_metadata`)
    # — the attribute form is a live pattern and evaded the first cut
    # (round-2 review, executed probe).
    aliases = {}
    module_aliases: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "runs":
            for a in node.names:
                if a.name in _RAW_WRITERS:
                    aliases[a.asname or a.name] = a.name
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "runs":
                    module_aliases.add(a.asname or a.name)
    if not aliases and not module_aliases:
        return hits
    # Var-flow: dict literals assigned to a NAME, plus later
    # NAME["key"] = ... additions, count when the NAME reaches a raw
    # writer (2026-08-15 burn-down: the NOW lane's `_now_extra` and the
    # provenance lane's `_prov_extra` were invisible to the
    # literal-in-call scan — the two biggest real bypasses). Tracking is
    # file-global: a same-named var in another function can over-count,
    # which errs toward detection, never past it.
    def _literal_keys(node) -> set:
        """String keys carried by a dict-shaped expression: a literal,
        a dict(...) constructor (round-2 review: evaded the first cut),
        or nothing."""
        if isinstance(node, ast.Dict):
            return {k.value for k in node.keys
                    if isinstance(k, ast.Constant)}
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "dict"):
            return {kw.arg for kw in node.keywords if kw.arg}
        return set()

    var_keys: dict = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            keys = _literal_keys(node.value)
            if keys:
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name):
                        var_keys.setdefault(tgt.id, set()).update(keys)
            tgt = node.targets[0]
            if (isinstance(tgt, ast.Subscript)
                    and isinstance(tgt.value, ast.Name)
                    and isinstance(tgt.slice, ast.Constant)):
                var_keys.setdefault(tgt.value.id, set()).add(
                    tgt.slice.value)
        # tracked.update({...}) / tracked.update(dict(...)) — round-2
        # review: the .update shape evaded the first cut.
        elif (isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)
              and node.func.attr == "update"
              and isinstance(node.func.value, ast.Name)
              and node.args):
            keys = _literal_keys(node.args[0])
            if keys:
                var_keys.setdefault(node.func.value.id, set()).update(keys)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        writer = None
        if isinstance(f, ast.Name) and f.id in aliases:
            writer = aliases[f.id]
        elif (isinstance(f, ast.Attribute) and f.attr in _RAW_WRITERS
              and isinstance(f.value, ast.Name)
              and f.value.id in module_aliases):
            writer = f.attr
        if writer is None:
            continue
        seen_keys: set = set()
        for a in list(node.args) + [kw.value for kw in node.keywords]:
            seen_keys |= _literal_keys(a)
            if isinstance(a, ast.Name):
                seen_keys |= var_keys.get(a.id, set())
        # **unpacked tracked dicts (keyword with arg=None) are already
        # covered: their value is an ast.Name in node.keywords above.
        for key in sorted(seen_keys):
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
            'x["stop_verdict"] = v\n'
            'locked_rmw(rd / "metadata.json", merge)\n'),
        "attribute locked_rmw": (
            "import file_lock\n"
            'x["stop_verdict"] = v\n'
            'file_lock.locked_rmw(rd / "metadata.json", merge)\n'),
        # Round-2 review shapes, each an executed-probe evasion of the
        # first cut:
        "module-attribute writer call": (
            "import runs\n"
            'runs.stamp_run_metadata({"stop_verdict": v})\n'),
        "module-alias writer call": (
            "import runs as r\n"
            'r.write_metadata(rd, extra={"goal_achieved": False})\n'),
        "dict constructor": (
            "from runs import stamp_run_metadata\n"
            "stamp_run_metadata(dict(stop_verdict=v))\n"),
        "dict constructor via var": (
            "from runs import write_metadata\n"
            "extra = dict(goal_verdict_source='x')\n"
            "write_metadata(rd, extra=extra)\n"),
        "update with literal": (
            "from runs import write_metadata\n"
            "extra = {}\n"
            'extra.update({"stop_evidence": e})\n'
            "write_metadata(rd, extra=extra)\n"),
        "kwargs unpack of tracked var": (
            "from runs import stamp_run_metadata\n"
            'fields = {"stop_verdict": v}\n'
            "stamp_run_metadata(**fields)\n"),
        "locked_rmw path bound to a var": (
            "from file_lock import locked_rmw\n"
            "def foo(rd):\n"
            '    mp = rd / "metadata.json"\n'
            "    def merge(old):\n"
            '        existing = {}\n'
            '        existing["stop_verdict"] = "x"\n'
            "        return old\n"
            "    locked_rmw(mp, merge)\n"),
        # Round-3 review, executed probe: the writing site defined FIRST,
        # a benign SAME-NAMED merge defined after — name→def resolution
        # picked the last one and silently missed the real bypass.
        "same-named merge fns, benign last": (
            "from file_lock import locked_rmw\n"
            "def site_a(rd):\n"
            "    def _merge(old):\n"
            '        existing = {}\n'
            '        existing["stop_verdict"] = "x"\n'
            "        return old\n"
            '    locked_rmw(rd / "metadata.json", _merge)\n'
            "def site_b(rd):\n"
            "    def _merge(old):\n"
            "        return old\n"
            '    locked_rmw(rd / "metadata.json", _merge)\n'),
    }
    for label, src in shapes.items():
        hits = _verdict_bypass_hits(ast.parse(src), "fixture.py")
        assert hits, f"bypass scanner missed the {label} shape: {src!r}"


def test_bypass_scanner_ignores_benign_writes():
    """Non-tuple keys through the raw writers stay legal — the census
    polices the verdict/stop family, not metadata writes in general.
    Same for locked_rmw merges that never touch tuple keys (the
    stranded sweep's status/pause stamp, audit-repair bookkeeping)."""
    benign = (
        "from runs import stamp_run_metadata\n"
        'stamp_run_metadata({"audit_incomplete": True})\n'
        'fields = {"loop_ids": ids}\n'
        "stamp_run_metadata(fields)\n")
    assert not _verdict_bypass_hits(ast.parse(benign), "fixture.py")
    benign_rmw = (
        "from file_lock import locked_rmw\n"
        "def sweep(meta_path):\n"
        "    def _commit(old):\n"
        "        cur = {}\n"
        '        cur["status"] = "stranded"\n'
        '        cur["pause_reason"] = "err:writer-died"\n'
        "        return old\n"
        '    locked_rmw(meta_path / "metadata.json", _commit)\n')
    assert not _verdict_bypass_hits(ast.parse(benign_rmw), "fixture.py")
