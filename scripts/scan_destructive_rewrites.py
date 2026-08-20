#!/usr/bin/env python3
"""Find per-line rewrites that DROP what they cannot parse.

The distinction this hunts is not silence — `tests/test_no_silent_drop.py`
owns that — it is DURABILITY. A row dropped from a read is recoverable:
the bytes are still on disk. A row dropped from a loop whose result is
written back is gone. That shape (drop + write-back) is the destructive
subset of the census debt, and it is what took skill-stats.jsonl from 4
lines to 1 and wiped a knowledge tier before it was found.

Reported shapes
---------------
RISK  a function that FRAMES a store into lines — `.splitlines()` or
      `.split("\n")` — and participates in a write-back: directly, via a
      lexically enclosing function, or via a SAME-MODULE helper it calls
      by name. It does NOT check that a drop is present; framing plus a
      write-back is the whole signal, which is why most hits are benign.
OK    the same, but a taint-refusing parse is applied on the drop path.

The call-graph leg exists because the scanner's first version could not
see the very shape the skills.py fix introduced: `_read_skill_stats`
(splitlines, no write) + `_write_skill_stats` (write, no splitlines) +
`record_skill_outcome` (calls both, contains neither). Three adversarial
lenses caught that independently — a scanner whose "found 0" cannot be
trusted is worse than no scanner. Fixtures in
`tests/test_scan_destructive_rewrites.py` pin that it can still find.

Limits, stated so nobody reads more off a clean run than it carries:
  * OK is a HINT, not a proof. It means a taint-refusing parse appears on
    the drop path, not that every unparseable line is provably carried.
    Read the function.
  * Cross-MODULE helpers are not followed.
  * `.split("\n")` counts as framing since 2026-08-20. It has to: this arc
    CONVERTED the sites it hardened from `splitlines()` to `split("\n")`
    (splitlines also breaks on U+2028/U+2029, which are legal inside a JSON
    string), which walked every one of them out of the scanner's field of
    view. Adversarial r3 proved it: reverting `interrupt.poll` to the exact
    destructive shape this arc removed produced ZERO hits, and the drift
    gate's `regressed` check — whose whole job is to catch that — could
    never fire. A fix that blinds the detector to its own subject is a
    worse outcome than the bug.
  * Markdown/single-object rewrites match the write markers and show up
    as RISK; they are usually false positives. Triage by reading.
  * No drop is required for a RISK. An earlier version of this docstring
    said "drops on a parse failure" was part of the test; it never was,
    and the 64-of-70 false-positive rate is the direct consequence
    (adversarial r3, 2026-08-20 — the claim had no executing line).
"""
from __future__ import annotations

import ast
import pathlib
import sys

_PARSERS: "tuple[set[str], set[str]]" = (set(), set())

WRITE_MARKERS = ("atomic_write", "write_text", "write_bytes", "locked_rmw",
                 "locked_append", "locked_write")
CLEAN_MARKERS = ("loads_clean", "_loads_clean")



def _parser_names(tree) -> "tuple[set[str], set[str]]":
    """(names proven to be the clean wrapper, names proven to be a raw parser).

    Adversarial r7 (2026-08-20, 5/5) killed r6's spelling-based version four
    ways in one round: `from json import loads as parse` was invisible
    (`parse` is neither `loads` nor `*_loads`), `parse_json = json.loads`
    likewise, and — the other direction — `from json import loads as
    _loads_clean` was TRUSTED, because the marker list matched the name and
    nothing checked where it came from. A verdict about safety cannot be
    read off an identifier. It comes from the binding:

        from jsonl_utils import loads_clean [as X]   -> X is clean
        import jsonl_utils [as M]                    -> M.loads_clean is clean
        X = loads_clean                              -> X is clean
        from json import loads [as X]                -> X is raw
        X = json.loads / X = J.loads                 -> X is raw
        import json [as J]                           -> J.loads is raw

    Anything unresolved stays unproven, and unproven parses do not earn OK.

    r7 kept a fallback for the conventional SPELLING (`loads_clean(...)`
    with no visible import earned OK), added so the round's own fixtures
    would pass. Adversarial r8 (3 lenses, probed) walked in through it:
    `from untrusted_parser import loads_clean` was trusted on the name
    alone. A fallback added under the pressure of a finding is exactly the
    shape the previous six rounds keep finding, and this one was in the
    docstring's own blind spot — the sentence above says unproven parses do
    not earn OK, and the fallback said otherwise. It is gone; every real
    caller in this repo imports the wrapper, and the scan proves it (77 RISK
    sites before and after).
    """
    clean, raw, json_mods = set(), set(), set()
    walk = ast.walk if isinstance(tree, ast.Module) else _own_scope
    for n in walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name == "json":
                    json_mods.add(a.asname or "json")
                elif a.name == "jsonl_utils":
                    clean.add(f"{a.asname or a.name}.loads_clean")
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                bound = a.asname or a.name
                if n.module == "json" and a.name == "loads":
                    raw.add(bound)
                elif a.name in ("loads_clean", "_loads_clean") \
                        and n.module == "jsonl_utils":
                    clean.add(bound)
        elif isinstance(n, ast.Assign) and len(n.targets) == 1 \
                and isinstance(n.targets[0], ast.Name):
            bound, v = n.targets[0].id, n.value
            if isinstance(v, ast.Name) and v.id in clean:
                clean.add(bound)
            elif isinstance(v, ast.Attribute) and v.attr == "loads" \
                    and isinstance(v.value, ast.Name) \
                    and (v.value.id in json_mods or v.value.id == "json"):
                raw.add(bound)
            elif isinstance(v, ast.Name) and v.id in raw:
                raw.add(bound)
    for mod in json_mods | {"json"}:
        raw.add(f"{mod}.loads")
    return clean - raw, raw


def _own_scope(node):
    """Walk `node`, but do NOT descend into a nested scope.

    Adversarial r9 (2 lenses, probed): `ast.walk(fn)` descends into nested
    functions, so a helper defined inside `rewrite` that imports the real
    wrapper re-proved `rewrite`'s OWN parameter — which defaulted to
    `json.loads`. The scanner said OK about a function parsing every line
    with the raw parser. A scope-aware rule that reads the wrong scope is
    not scope-aware; Python's scopes are the unit, so this is the walk that
    respects them. (Nested functions are scanned as their own sites, so
    nothing stops being looked at.)
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        cur = stack.pop()
        yield cur
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.Lambda, ast.ClassDef)):
            continue                       # its own scope, its own proofs
        stack.extend(ast.iter_child_nodes(cur))


def _binding_census(fn) -> "dict[str, list]":
    """{name: [value per binding]} for every name bound inside `fn`.

    A binding whose value is not a proven constant is `_UNRESOLVED`, and a
    name bound more than once is unresolved by construction (see the caller).

    r7 enumerated binding NODE TYPES — Assign, AnnAssign, AugAssign,
    NamedExpr, For, With — and adversarial r8 (2 lenses, probed) found the
    two it had not thought of: a tuple target (`sep, ignored = "\n", 0`) and
    a `match` capture (`case {"separator": sep}`). Both made a live JSONL
    rewrite vanish from the scan entirely — neither RISK nor OK, the same
    disappearance the arc has now paid for three times.

    Enumerating forms is a denylist, so this does not enumerate. Python
    marks every name it binds by ASSIGNMENT with `ctx=ast.Store`, so that is
    what gets counted; the handful of binders that carry a bare `str`
    instead of a Name node (`except E as x`, `case ... as x`, `case [*rest]`,
    `import x`, parameters) are listed after it — a short, closed set that
    the grammar itself defines.
    """
    binds: "dict[str, list]" = {}
    resolved: "dict[int, object]" = {}
    for n in _own_scope(fn):
        # A simple `x = <constant>` is the ONLY shape whose value is known.
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    resolved[id(target)] = n.value.value
    for n in _own_scope(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            binds.setdefault(n.id, []).append(
                resolved.get(id(n), _UNRESOLVED))
        elif isinstance(n, ast.ExceptHandler) and n.name:
            binds.setdefault(n.name, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.MatchAs, ast.MatchStar)) and n.name:
            binds.setdefault(n.name, []).append(_UNRESOLVED)
        elif isinstance(n, ast.MatchMapping) and n.rest:
            binds.setdefault(n.rest, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                binds.setdefault((a.asname or a.name).split(".")[0],
                                 []).append(_UNRESOLVED)
        elif isinstance(n, ast.arg):
            binds.setdefault(n.arg, []).append(_UNRESOLVED)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                            ast.ClassDef)):
            binds.setdefault(n.name, []).append(_UNRESOLVED)
    for a in getattr(getattr(fn, "args", None), "args", []) or []:
        binds.setdefault(a.arg, []).append(_UNRESOLVED)
    return binds


def _shadowed(fn) -> set:
    """Names this function rebinds locally, whatever the module says.

    Adversarial r8 (4 lenses, probed): parser identity was collected
    module-wide, so `def rewrite(path, loads_clean=json.loads)` and a local
    `loads_clean = lambda s: json.loads(s)` both parsed with the raw parser
    while the scanner read the module-level import and said OK. A binding
    that is not proven clean IN THIS SCOPE cannot inherit the module's
    proof.
    """
    local = set(_binding_census(fn))
    module_clean, _ = _PARSERS
    # A local binding can RE-prove itself: importing the wrapper inside the
    # function is how half this codebase does it (`doctor.py` and
    # `gc_memory.py` both do), and that import is the proof, not a shadow.
    keep, local_raw = _parser_names(fn)
    for n in ast.walk(fn):
        # ...as is `X = <a name the module already proved clean>`.
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Name) \
                and n.value.id in module_clean:
            keep |= {tgt.id for tgt in n.targets if isinstance(tgt, ast.Name)}
    return local - (keep - local_raw)


def _called_names(node: ast.AST) -> set[str]:
    """Bare names this node calls: f(...) and self.f(...) alike."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def scan_module(tree: ast.Module) -> list[tuple[str, int, str]]:
    """Return (verdict, lineno, qualified_name) for every flagged function."""
    global _PARSERS
    _PARSERS = _parser_names(tree)
    parents: dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p

    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # name -> EVERY function with that name. Adversarial r9 (Skeptic,
    # probed): a dict kept the last one, so an unrelated `B.save` replaced
    # `A.save` and `A.rewrite`'s write leg resolved to the wrong body —
    # `A.helper`, a destructive JSONL loop, vanished from the scan
    # entirely (neither RISK nor OK). A name collision is not evidence of
    # anything; when the call is ambiguous the scanner says "someone should
    # read this", which is the direction it is allowed to be wrong in.
    by_name: "dict[str, list]" = {}
    for f in funcs:
        by_name.setdefault(f.name, []).append(f)

    def qual(fn):
        name, cur = fn.name, fn
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{cur.name}.{name}"
        return name

    def writes(fn, seen=None):
        """Does fn write back — itself, via an enclosing fn, or a callee?"""
        seen = seen or set()
        if fn.name in seen:
            return False
        seen.add(fn.name)
        dump = ast.dump(fn)
        if any(m in dump for m in WRITE_MARKERS):
            return True
        cur = fn
        while cur in parents:                      # lexically enclosing
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(m in ast.dump(cur) for m in WRITE_MARKERS):
                    return True
                break
        for callee in _called_names(fn):           # same-module call graph
            if any(writes(tgt, seen) for tgt in by_name.get(callee, ())):
                return True
        return False

    def written_by_a_caller(fn):
        """A read-helper is destructive when a caller writes what it returns."""
        for other in funcs:
            if other is fn:
                continue
            if fn.name in _called_names(other) and writes(other):
                return True
        return False

    def frames_lines(fn):
        """Does fn split a blob into lines?

        Every idiom that means the same thing: `.splitlines()`,
        `.readlines()`, `.split("\\n")` / `.split(b"\\n")`, the keyword form
        `.split(sep="\\n")`, a separator held in a variable, and plain
        iteration over an open file handle. Adversarial r4 (3 lenses) added
        the second and third — including `split(b"\\n")`, which
        `src/jsonl_utils.py` itself uses, so it was never hypothetical.
        Adversarial r5 (3 lenses, probed) added the last two, both invisible
        to r4's version and both one routine refactor away from any hardened
        site: hoisting the separator to a `sep = "\\n"` local, or moving from
        `.readlines()` to `with path.open() as fh: for line in fh:`.

        The direction of the doubt is the point. A `.split()` whose separator
        this cannot resolve is treated as framing, because the cost of a
        false RISK is one line of triage (the manifest already carries 69 of
        them) and the cost of a false OK is a destructive rewrite nobody can
        see. Only a separator PROVEN to be something other than a newline
        buys silence.
        """
        # A name buys silence only when ONE binding in the whole function
        # proves it non-newline. Adversarial r6 (2026-08-20, 2 lenses,
        # probed): the first cut kept the LAST assignment seen in AST order,
        # which is not control flow — `if newline: sep = "\n" else: sep =
        # ","` and a plain later `sep = ","` both made a live JSONL rewrite
        # vanish from the scan entirely. Counting bindings is not flow
        # analysis either; it just refuses to pretend. Two bindings means
        # unresolved, and unresolved means framing.
        binds = _binding_census(fn)
        consts = {k: v[0] for k, v in binds.items() if len(v) == 1}
        file_handles = set()                       # names bound from open()
        for n in ast.walk(fn):
            for target, value in _bindings(n):
                if _is_open_call(value) and isinstance(target, ast.Name):
                    file_handles.add(target.id)
        for n in ast.walk(fn):
            if isinstance(n, (ast.For, ast.AsyncFor)):
                it = n.iter
                if isinstance(it, ast.Name) and it.id in file_handles:
                    return True
                if _is_open_call(it):              # for line in open(p):
                    return True
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr in ("splitlines", "readlines"):
                return True
            if n.func.attr == "split":
                if isinstance(n.func.value, ast.Name) and \
                        n.func.value.id == "shlex":
                    continue          # shell words, never store lines
                args = list(n.args) + [k.value for k in n.keywords
                                       if k.arg == "sep"]
                if not args:
                    continue                       # .split() on whitespace
                a = args[0]
                if isinstance(a, ast.Constant):
                    if a.value in ("\n", b"\n"):
                        return True
                elif isinstance(a, ast.Name):
                    if consts.get(a.id, "\n") in ("\n", b"\n", _UNRESOLVED):
                        return True               # unresolved -> assume framing
                else:
                    return True                    # unresolvable expression
        return False

    out = []
    for fn in funcs:
        dump = ast.dump(fn)
        if not frames_lines(fn):
            continue
        if not (writes(fn) or written_by_a_caller(fn)):
            continue
        # OK is a HINT, and adversarial r5 (Architect, probed) showed how
        # weak a hint: the marker only had to APPEAR somewhere in the
        # function, so a rewrite that parses each line with bare
        # `json.loads` and merely mentions `loads_clean` elsewhere reported
        # OK — visible to the manifest's `vanished` leg, and green. A
        # function that still parses with the unguarded call has not been
        # cleared, whatever else it mentions.
        used_clean, used_raw = _parse_calls(fn)
        verdict = "OK  " if used_clean and not used_raw else "RISK"
        out.append((verdict, fn.lineno, qual(fn)))
    return out



_UNRESOLVED = object()


def _bindings(node):
    """(target, value) pairs for assignments and `with ... as` clauses."""
    if isinstance(node, ast.Assign):
        for t in node.targets:
            yield t, node.value
    elif isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            if item.optional_vars is not None:
                yield item.optional_vars, item.context_expr


def _is_open_call(node) -> bool:
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    return (isinstance(f, ast.Name) and f.id == "open") or \
           (isinstance(f, ast.Attribute) and f.attr == "open")


def _parse_calls(fn) -> "tuple[bool, bool]":
    """(calls a proven-clean parser, calls anything else parse-shaped)."""
    clean_names, raw_names = _PARSERS
    shadow = _shadowed(fn)
    # A dotted proof is only as good as its RECEIVER. `import jsonl_utils`
    # proves `jsonl_utils.loads_clean`, and `def rewrite(path, jsonl_utils)`
    # or a local `jsonl_utils = shim` replaces the object that name points
    # at — adversarial r9 (3 lenses, probed) certified both OK, because the
    # r8 revocation subtracted bare names from a set holding dotted ones.
    clean_names = {n for n in clean_names - shadow
                   if n.split(".")[0] not in shadow}
    used_clean = used_raw = False
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        if isinstance(f, ast.Attribute):
            recv = f.value.id if isinstance(f.value, ast.Name) else "?"
            name, dotted = f.attr, f"{recv}.{f.attr}"
        elif isinstance(f, ast.Name):
            name = dotted = f.id
        else:
            continue
        # RAW WINS. `from json import loads as _loads_clean` binds a
        # trusted-looking name to the untrusted parser, and r7 probed that
        # exact shape earning OK — so a proven raw binding beats every
        # naming convention below it.
        if dotted in raw_names or name in raw_names:
            used_raw = True
        elif name in clean_names or dotted in clean_names:
            used_clean = True
        elif name == "loads" or name.endswith("_loads") \
                or name in ("loads_clean", "_loads_clean"):
            used_raw = True          # parse-shaped and unproven
    return used_clean, used_raw


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = pathlib.Path(argv[0]) if argv else pathlib.Path("src")
    paths = sorted(root.glob("*.py")) if root.is_dir() else [root]
    risks = 0
    for path in paths:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="surrogateescape"))
        except SyntaxError:
            continue
        for verdict, lineno, name in scan_module(tree):
            print(f"{verdict} {path.name}:{lineno} {name}()")
            risks += verdict.strip() == "RISK"
    print(f"\n{risks} RISK site(s) — triage by reading; OK is a hint, "
          f"not a proof (see module docstring).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
