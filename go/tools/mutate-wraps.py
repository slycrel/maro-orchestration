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

The Go-source scanning lives in gosrc.py, shared with mutate-modes.py.

Usage:
    python3 go/tools/mutate-wraps.py pytext.PyFoldI
    python3 go/tools/mutate-wraps.py pytext.PyFoldI --pkg ./internal/guard/
    python3 go/tools/mutate-wraps.py pySpace --quick
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gosrc  # noqa: E402

gosrc.install_restore_handlers()


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

    targets, unparsed = [], []
    for path in gosrc.go_files():
        if args.path and not os.path.relpath(path, gosrc.GO_ROOT).startswith(
                args.path.lstrip("./")):
            continue
        src, found, skipped = gosrc.call_sites(path, args.wrapper)
        for s in found:
            targets.append((path, src, s))
        for line in skipped:
            unparsed.append((os.path.relpath(path, gosrc.GO_ROOT), line))
    if unparsed:
        # Not a warning. A site this tool cannot parse is a site it does
        # not mutate, and the run would still print "all N killed" over a
        # denominator quietly short of the truth (P16).
        for rel, line in unparsed:
            print("  ! could not find the matching ')' at %s:%d" % (rel, line))
        sys.exit("%d site(s) could not be parsed. Fix gosrc.match_close "
                 "before trusting any coverage number this run would print."
                 % len(unparsed))
    if not targets:
        sys.exit("no %s( call sites found under %s%s — nothing to mutate, "
                 "which is itself the answer"
                 % (args.wrapper, gosrc.GO_ROOT,
                    "/" + args.path if args.path else ""))

    print("baseline: %s across %d site(s)" % (args.pkg, len(targets)))
    ok, _, _ = gosrc.run_tests(args.pkg, args.quick)
    if not ok:
        sys.exit("the tree is ALREADY red; a mutation run over a red tree "
                 "reports every site as covered. Fix the tree first (P7).")

    survivors = []
    for path, src, (start, open_idx, close_idx) in targets:
        rel = os.path.relpath(path, gosrc.GO_ROOT)
        line = gosrc.line_of(src, start)
        # `pytext.PyFoldI(ARG)` -> `(ARG)`: the argument and every paren
        # around it are preserved, so the result still parses.
        mutant = (src[:start] + "(" + src[open_idx + 1:close_idx] + ")"
                  + src[close_idx + 1:])
        gosrc.stage(path)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(mutant)
            ok, killers, panicked = gosrc.run_tests(args.pkg, args.quick)
        finally:
            gosrc.restore_all()

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
