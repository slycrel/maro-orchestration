#!/usr/bin/env python3
"""Find wrapper call sites whose removal breaks nothing.

WHY THIS EXISTS. On 2026-08-27 three separate fixes in this port landed
correct and unprovable on the same day:

    internal/artifactcheck  4 patterns wrapped, 3 fixtured
    internal/guard          a class body fixed, the fixtures aimed at the stem
    internal/provenance     3 patterns wrapped, 1 fixtured

Each time the fixtures were written from the FINDING while the fix was
written from the FILE, so the site the review had not named was the site
nothing could catch. That is L9's inversion (docs/REVIEW_PATTERNS.md),
and it had fired often enough to stop being a discipline and become a
command.

WHAT IT DOES. Removes one wrapper application at a time -- rewriting
`pytext.PyFoldI(X)` back to `(X)` -- and runs the tests. A site whose
removal leaves the suite GREEN is a guard that cannot fail, which is
worse than no guard (Jeremy, 2026-08-16).

WHAT IT DOES NOT. It cannot write the missing fixture, and it cannot
tell a site that is genuinely unobservable from one that is merely
untested -- that judgement is the reader's, and the answer belongs in a
comment next to the site either way (L8). It also only understands
wrappers spelled as a direct call around the whole argument.

Usage:
    python3 go/tools/mutate-wraps.py pytext.PyFoldI
    python3 go/tools/mutate-wraps.py pytext.PyFoldI --pkg ./internal/guard/
    python3 go/tools/mutate-wraps.py pySpace --quick
"""

import argparse
import atexit
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

# Every file this run has rewritten, mapped to its pristine copy. A
# per-site try/finally covers an exception; it does NOT cover SIGTERM,
# because Python does not run finally blocks for a default-disposition
# signal. Killing an early version of this script left
# internal/provenance/provenance.go mutated in the working tree -- a tool
# that measures the tree must not be able to damage it when interrupted.
_PENDING = {}


def _restore_all(*_):
    for path, backup in list(_PENDING.items()):
        try:
            shutil.copyfile(backup, path)
            os.unlink(backup)
        except OSError:
            pass
        _PENDING.pop(path, None)


atexit.register(_restore_all)
for _sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(_sig, lambda s, f: (_restore_all(), sys.exit(128 + s)))

HERE = os.path.dirname(os.path.abspath(__file__))
GO_ROOT = os.path.dirname(HERE)


def go_files():
    for dirpath, dirnames, files in os.walk(GO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "testdata")]
        for fn in sorted(files):
            if fn.endswith(".go") and not fn.endswith("_test.go"):
                yield os.path.join(dirpath, fn)


def match_close(src, open_idx):
    """Index of the ')' matching the '(' at open_idx, or -1.

    Go raw strings are delimited by backticks and can contain anything;
    interpreted strings honour backslash escapes. A paren inside either
    is not a paren, and getting that wrong is how a rewriter corrupts a
    file it was only supposed to measure.
    """
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c == "`":
            i = src.find("`", i + 1)
            if i < 0:
                return -1
        elif c == '"':
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "'":
            i += 1
            while i < n and src[i] != "'":
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def sites(path, wrapper):
    """(start, open_idx, close_idx) for each `wrapper(...)` in path."""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    out = []
    pat = re.compile(r"\b" + re.escape(wrapper) + r"\(")
    for m in pat.finditer(src):
        close = match_close(src, m.end() - 1)
        if close < 0:
            print("  ! unbalanced parens at %s:%d — skipped"
                  % (path, src.count("\n", 0, m.start()) + 1), file=sys.stderr)
            continue
        out.append((m.start(), m.end() - 1, close))
    return src, out


def line_of(src, idx):
    return src.count("\n", 0, idx) + 1


def run_tests(pkg, quick):
    env = dict(os.environ, MARO_PYPROBE_REQUIRED="1")
    cmd = ["go", "test", pkg, "-count=1"]
    if quick:
        cmd.append("-short")
    p = subprocess.run(cmd, cwd=GO_ROOT, env=env,
                       capture_output=True, text=True)
    killers = sorted(set(re.findall(r"--- FAIL: (\S+)", p.stdout)))
    panicked = "panic:" in p.stdout or "panic:" in p.stderr
    return p.returncode == 0, killers, panicked


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wrapper", help="e.g. pytext.PyFoldI")
    ap.add_argument("--pkg", default="./...",
                    help="package pattern to TEST (default ./...)")
    ap.add_argument("--path", default="",
                    help="only mutate sites under this module-relative "
                         "prefix. Narrowing --pkg alone is a TRAP: it "
                         "changes what is tested, not what is mutated, so "
                         "a site in another package reports SURVIVED "
                         "because its killers were never run.")
    ap.add_argument("--quick", action="store_true", help="pass -short")
    args = ap.parse_args()

    targets = []
    for path in go_files():
        if args.path and not os.path.relpath(path, GO_ROOT).startswith(
                args.path.lstrip("./")):
            continue
        src, found = sites(path, args.wrapper)
        for s in found:
            targets.append((path, src, s))
    if not targets:
        sys.exit("no %s( call sites found under %s%s — nothing to mutate, "
                 "which is itself the answer"
                 % (args.wrapper, GO_ROOT, "/" + args.path if args.path else ""))

    print("baseline: %s across %d site(s)" % (args.pkg, len(targets)))
    ok, _, _ = run_tests(args.pkg, args.quick)
    if not ok:
        sys.exit("the tree is ALREADY red; a mutation run over a red tree "
                 "reports every site as covered. Fix the tree first (P7).")

    survivors = []
    for path, src, (start, open_idx, close_idx) in targets:
        rel = os.path.relpath(path, GO_ROOT)
        line = line_of(src, start)
        # `pytext.PyFoldI(ARG)` -> `(ARG)`: the argument and every paren
        # around it are preserved, so the result still parses.
        mutant = src[:start] + "(" + src[open_idx + 1:close_idx] + ")" + src[close_idx + 1:]
        backup = tempfile.mktemp(suffix=".go")
        shutil.copyfile(path, backup)
        _PENDING[path] = backup
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutant)
            ok, killers, panicked = run_tests(args.pkg, args.quick)
        finally:
            _restore_all()

        if ok:
            survivors.append((rel, line))
            print("  SURVIVED  %s:%d — nothing failed" % (rel, line))
        elif panicked and not killers:
            print("  killed    %s:%d — panic at init" % (rel, line))
        else:
            print("  killed    %s:%d — %s" % (rel, line, ", ".join(killers[:4])))

    print()
    if survivors:
        print("%d of %d site(s) are UNPROVEN:" % (len(survivors), len(targets)))
        for rel, line in survivors:
            print("  %s:%d" % (rel, line))
        print("\nEach needs a fixture that fails without the wrapper, or a "
              "comment next to it saying why no input can observe it (L8). "
              "A guard that cannot fail is worse than no guard.")
        sys.exit(1)
    print("all %d site(s) killed at least one test" % len(targets))


if __name__ == "__main__":
    main()
