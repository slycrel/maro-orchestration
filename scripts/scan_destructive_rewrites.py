"""Narrow the drop-sites to the DESTRUCTIVE subset: loops that drop what
they cannot parse AND whose rebuilt content is written back to the store.
A dropped row in a pure read is recoverable (the bytes are still on disk);
a dropped row in a rewrite is gone."""
import ast, pathlib

SRC = pathlib.Path("src")
WRITE_MARKERS = ("atomic_write", "write_text", "write_bytes", "locked_rmw",
                 "locked_append", "locked_write")

def enclosing_funcs(tree):
    parents = {}
    for p in ast.walk(tree):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    return parents

for path in sorted(SRC.glob("*.py")):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="surrogateescape"))
    except SyntaxError:
        continue
    parents = enclosing_funcs(tree)
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body_dump = ast.dump(fn)
        if "splitlines" not in body_dump:
            continue
        if not any(m in body_dump for m in WRITE_MARKERS):
            # a nested rmw callback returns text; its PARENT holds locked_rmw
            par = parents.get(fn)
            outer = None
            cur = fn
            while cur in parents:
                cur = parents[cur]
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    outer = cur
                    break
            if not (outer and any(m in ast.dump(outer) for m in WRITE_MARKERS)):
                continue
        # this function both scans lines and participates in a write-back
        qual = fn.name
        cur = fn
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = f"{cur.name}.{qual}"
        clean = "loads_clean" in body_dump or "_loads_clean" in body_dump
        print(f"{'OK  ' if clean else 'RISK'} {path.name}:{fn.lineno} {qual}()")
