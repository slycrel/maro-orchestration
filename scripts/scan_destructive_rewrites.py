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

WRITE_MARKERS = ("atomic_write", "write_text", "write_bytes", "locked_rmw",
                 "locked_append", "locked_write")
CLEAN_MARKERS = ("loads_clean", "_loads_clean")


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
    parents: dict[ast.AST, ast.AST] = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p

    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    by_name = {f.name: f for f in funcs}

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
            tgt = by_name.get(callee)
            if tgt is not None and writes(tgt, seen):
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
        binds = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 \
                    and isinstance(n.targets[0], ast.Name):
                name = n.targets[0].id
                binds.setdefault(name, []).append(
                    n.value.value if isinstance(n.value, ast.Constant)
                    else _UNRESOLVED)
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
        clean = any(m in dump for m in CLEAN_MARKERS) and not _bare_json_loads(fn)
        verdict = "OK  " if clean else "RISK"
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


def _bare_json_loads(fn) -> bool:
    """Does this function parse with anything other than the taint-refusing
    wrapper?

    Adversarial r6 (2026-08-20, 4 lenses, probed) broke the first cut four
    ways: it matched only the literal spelling `json.loads`, so
    `import json as j`, `from json import loads`, and every aliased variant
    walked past it and the function was certified OK for merely mentioning
    `loads_clean` elsewhere. Chasing spellings is the losing half of that
    trade — the rule is now the other way round. ANY call named `loads` that
    is not the clean wrapper counts as unguarded, whatever module it came
    from, because `yaml.loads` and `pickle.loads` are not safer than
    `json.loads` for this purpose. The scanner's job is to say "someone
    should read this", and it is allowed to be wrong in that direction.
    """
    for n in ast.walk(fn):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else \
            (f.id if isinstance(f, ast.Name) else None)
        if name is None:
            continue
        if name in CLEAN_MARKERS or name.lstrip("_") in CLEAN_MARKERS:
            continue
        if name == "loads" or name.endswith("_loads"):
            return True
    return False


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
