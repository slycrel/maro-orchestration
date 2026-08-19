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
RISK  a function that scans `.splitlines()`, drops on a parse failure,
      and participates in a write-back — directly, via a lexically
      enclosing function, or via a SAME-MODULE helper it calls by name.
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
  * Markdown/single-object rewrites match the write markers and show up
    as RISK; they are usually false positives. Triage by reading.
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

    out = []
    for fn in funcs:
        dump = ast.dump(fn)
        if "splitlines" not in dump:
            continue
        if not (writes(fn) or written_by_a_caller(fn)):
            continue
        verdict = "OK  " if any(m in dump for m in CLEAN_MARKERS) else "RISK"
        out.append((verdict, fn.lineno, qual(fn)))
    return out


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
