#!/usr/bin/env python3
"""Find file/directory MODE arguments that no test can observe.

WHY THIS EXISTS. On 2026-08-27 two independent build agents, working on
different modules with no contact between them, found the same defect by
the same move: mutate a helper that has never been mutated. In both
cases the free value was a MODE.

    internal/worktree   makeDirs' record.NewDirMode -> 0o700, suite green
    internal/persona    all THREE os.MkdirAll sites -> 0o700, suite green

The mode arguments were EXECUTED by every test in those packages and
asserted by none, so line coverage could not tell "never checked" from
"guarded". That is L4/L9 territory, but the specific surface -- a
constant handed to a syscall whose effect is on the FILESYSTEM and not
in the return value -- fired twice in one day in two lanes, and the
standing rule for that is that it stops being a finding and becomes a
check.

Modes are not cosmetic in this port. The worktrees root and every loop
directory are read by workers that may run under a different uid; a
0o700 that should be 0o755 surfaces as a git error three layers away,
long after the code that chose it.

WHAT IT DOES. For every mode argument in the tree -- the last argument
of os.Mkdir/os.MkdirAll/os.WriteFile/os.OpenFile/os.Chmod -- rewrites
the expression E to (E ^ 0o044) and runs the OWNING package's tests. A
site whose flip leaves the package green has a mode nothing measures.

XOR, not a fixed replacement, and that choice is the point: a mutant
that assigns 0o700 to a mode that is ALREADY 0o700 changes nothing and
reports a gap that is not there. XOR always changes the value, whatever
the value is. The persona agent's sweep manufactured three such findings
in one session with no-op mutants; a tool that measures guards has to
have its own mutants checked first.

VACUITY FLOOR. umask is applied by the kernel to Mkdir/OpenFile modes.
If the running umask masks off both flipped bits, the mutant is a no-op
on disk and every site reports SURVIVED for a reason that has nothing to
do with the tests. The run refuses rather than printing that.

WHAT IT DOES NOT. It cannot tell an unobservable mode from an untested
one -- that judgement is the reader's, and the answer belongs in a
comment at the site either way (L8). A package with no test files is
reported UNTESTABLE, not SURVIVED: those are different facts and
collapsing them would be the same wall this tool exists to find.

Usage:
    python3 go/tools/mutate-modes.py
    python3 go/tools/mutate-modes.py --path internal/skills
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gosrc  # noqa: E402

gosrc.install_restore_handlers()

# The last argument of each of these IS the mode. Keep the list explicit:
# inferring "an argument that looks like a mode" would quietly widen the
# population every time someone writes an octal literal.
MODE_CALLS = [
    "os.Mkdir",
    "os.MkdirAll",
    "os.WriteFile",
    "os.OpenFile",
    "os.Chmod",
]

FLIP = 0o044  # group-read + other-read: never masked by the usual 022


def collect(path_filter):
    targets, unparsed = [], []
    for path in gosrc.go_files():
        rel = os.path.relpath(path, gosrc.GO_ROOT)
        if path_filter and not rel.startswith(path_filter.lstrip("./")):
            continue
        for callee in MODE_CALLS:
            src, found, skipped = gosrc.call_sites(path, callee)
            for line in skipped:
                unparsed.append((rel, line, callee))
            for start, open_idx, close_idx in found:
                args = gosrc.split_args(src, open_idx, close_idx)
                if not args:
                    unparsed.append((rel, gosrc.line_of(src, start), callee))
                    continue
                targets.append((path, callee, args[-1]))
    # One file can hold several sites; mutate them one at a time, so each
    # target carries its own freshly-read source at apply time.
    return targets, unparsed


def has_tests(path):
    d = os.path.dirname(path)
    return any(f.endswith("_test.go") for f in os.listdir(d))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", default="",
                    help="only mutate sites under this module-relative prefix")
    ap.add_argument("--quick", action="store_true", help="pass -short")
    args = ap.parse_args()

    cur = os.umask(0)
    os.umask(cur)
    if cur & FLIP == FLIP:
        sys.exit("umask is %04o, which masks off both bits this tool flips "
                 "(0o%03o). Every site would report SURVIVED for a reason "
                 "that has nothing to do with the tests. Re-run under a "
                 "umask that leaves at least one of them." % (cur, FLIP))

    targets, unparsed = collect(args.path)
    if unparsed:
        # P16: a site the tool cannot parse is a site it does not mutate,
        # and any coverage number printed afterwards is quantified over a
        # denominator that is quietly short.
        for rel, line, callee in unparsed:
            print("  ! could not parse %s( at %s:%d" % (callee, rel, line))
        sys.exit("%d site(s) could not be parsed. Fix gosrc before trusting "
                 "any number this run would print." % len(unparsed))
    if not targets:
        sys.exit("no mode arguments found under %s%s"
                 % (gosrc.GO_ROOT, "/" + args.path if args.path else ""))

    by_pkg = {}
    for t in targets:
        by_pkg.setdefault(gosrc.pkg_of(t[0]), []).append(t)

    print("umask %04o, flipping 0o%03o, %d mode argument(s) in %d package(s)"
          % (cur, FLIP, len(targets), len(by_pkg)))

    survivors, untestable, killed = [], [], 0
    for pkg in sorted(by_pkg):
        pkg_targets = by_pkg[pkg]
        if not has_tests(pkg_targets[0][0]):
            for path, callee, _ in pkg_targets:
                untestable.append((os.path.relpath(path, gosrc.GO_ROOT), callee))
            print("  UNTESTABLE %s — the package has no test files (%d site(s))"
                  % (pkg, len(pkg_targets)))
            continue
        ok, _, _ = gosrc.run_tests(pkg, args.quick)
        if not ok:
            sys.exit("%s is ALREADY red; a mutation run over a red package "
                     "reports every site as covered (P7)." % pkg)
        for path, callee, (astart, aend) in pkg_targets:
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            rel = os.path.relpath(path, gosrc.GO_ROOT)
            line = gosrc.line_of(src, astart)
            expr = src[astart:aend].strip()
            mutant = (src[:astart] + "(" + expr + " ^ 0o%03o)" % FLIP
                      + src[aend:])
            gosrc.stage(path)
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(mutant)
                mok, killers, panicked = gosrc.run_tests(pkg, args.quick)
            finally:
                gosrc.restore_all()
            label = "%s:%d %s(… %s)" % (rel, line, callee, expr)
            if mok:
                survivors.append(label)
                print("  SURVIVED   %s" % label)
            else:
                killed += 1
                why = ", ".join(killers[:3]) or ("panic" if panicked
                                                 else "build/failure")
                print("  killed     %s — %s" % (label, why))

    print()
    print("%d killed, %d survived, %d untestable, of %d"
          % (killed, len(survivors), len(untestable), len(targets)))
    if untestable:
        print("\nUNTESTABLE (no test files in the package — a different fact "
              "from SURVIVED, and not a smaller one):")
        for rel, callee in untestable:
            print("  %s  %s" % (rel, callee))
    if survivors:
        print("\n%d mode argument(s) are UNPROVEN. Each needs a test that "
              "reads the resulting mode off disk, or a comment at the site "
              "saying why no caller can observe it (L8)." % len(survivors))
    if survivors or untestable:
        sys.exit(1)


if __name__ == "__main__":
    main()
