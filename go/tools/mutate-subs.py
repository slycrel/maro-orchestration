#!/usr/bin/env python3
"""Run a hand-written mutation battery from a JSON spec.

WHY THIS EXISTS. Every tranche in this port gets a battery of textual
mutations derived from the FILE (L9), and every one of them has been a
throwaway script in a scratchpad directory. Three consequences, all of
which have now happened:

  1. A killed run leaves a MUTANT ON DISK. Python does not run `finally`
     blocks for a default-disposition signal, so a script whose only
     protection is try/finally is unprotected against the timeout that
     kills it. On 2026-08-27 a battery hit its runner's ten-minute cap
     and left `if nWorkers < 0` in loopparallel.go; the next five test
     runs deadlocked, and the first three explanations for that were all
     wrong because the file on disk was not the file in the diff.
     mutate-wraps.py had exactly this bug, in provenance.go, the same
     week (see gosrc.install_restore_handlers).
  2. The spec is thrown away with the script, so the next round cannot
     re-run the battery the last round converged.
  3. Each copy re-invents pattern-uniqueness checking, and a pattern that
     matches zero sites reports SURVIVED in the copies that forgot.

So: one runner, gosrc's signal-safe restore, a spec that lives on disk,
and a matched-count check that is an ERROR and not a line of output.

SPEC FORMAT (JSON):

    {"packages": {"default": ["./internal/foo/"]},
     "mutations": [
       {"file": "internal/foo/foo.go",
        "name": "clip-counts-bytes",
        "old": "pyval.Clip(s, 60)",
        "new": "s[:60]",
        "packages": ["./internal/foo/", "./internal/bar/"]}]}

`packages` on a mutation overrides the default. Choose it as the set of
packages that can SEE the change: narrowing it to the one you are
building makes a shared helper's mutation report SURVIVED because its
killers were never run.

`equivalent` on a mutation says NO INPUT CAN OBSERVE IT, and its value is
the reason. Such a row is expected to survive; it is reported as `equiv`
and does not fail the run. If it is ever KILLED the run fails, because
the reason has gone stale — a guard moved, a fixture widened, or the
equivalence was wrong to begin with.

This field exists because the alternative was DELETING the row. Every
tranche so far has produced one or two survivors of the same shape — a
mutation that substitutes a value some guard ABOVE the site has already
pinned (`LoopStatus: "done"` under a `status != "done"` return;
`dry_run` under a `if dry_run { return }`) — and deleting them loses the
reasoning and invites the next round to re-derive it. Keeping the row
makes the note a tripwire on itself, which is the standing rule for a
pinned difference.

Usage:
    python3 go/tools/mutate-subs.py spec.json
    python3 go/tools/mutate-subs.py spec.json --only clip-counts-bytes
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gosrc  # noqa: E402

gosrc.install_restore_handlers()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="path to the JSON battery spec")
    ap.add_argument("--only", action="append", default=[],
                    help="run just these mutation names (repeatable)")
    args = ap.parse_args()

    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    default_pkgs = spec.get("packages", {}).get("default") or ["./..."]
    muts = spec["mutations"]
    if args.only:
        muts = [m for m in muts if m["name"] in args.only]
        if not muts:
            sys.exit("no mutation matched %s" % args.only)

    # Read every target file ONCE, up front. A battery that re-read the
    # file per mutation would pick up a mutant left by a previous row if
    # a restore had failed, and then measure the wrong baseline.
    originals = {}
    for m in muts:
        path = os.path.join(gosrc.GO_ROOT, m["file"])
        if path not in originals:
            with open(path, encoding="utf-8") as fh:
                originals[path] = fh.read()

    # The baseline is run over the UNION of every package any mutation
    # names, so a red tree is caught before any file is touched (P7).
    union = sorted({p for m in muts for p in (m.get("packages") or default_pkgs)})
    print("baseline: %s" % " ".join(union))
    ok, _, out = gosrc.run_tests_multi(union)
    if not ok:
        sys.exit("the tree is ALREADY red; every mutation would report as "
                 "covered. Fix the tree first (P7).\n" + out[-2000:])

    survivors, unmatched, buildfails, stale = [], [], [], []
    for m in muts:
        path = os.path.join(gosrc.GO_ROOT, m["file"])
        src = originals[path]
        n = src.count(m["old"])
        if n != 1:
            unmatched.append((m["name"], n))
            print("  !SPEC     %-36s matched %d site(s)" % (m["name"], n))
            continue
        pkgs = m.get("packages") or default_pkgs
        gosrc.stage(path)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(src.replace(m["old"], m["new"], 1))
            ok, killers, out = gosrc.run_tests_multi(pkgs)
        finally:
            gosrc.restore_all()
        if ok:
            if m.get("equivalent"):
                print("  equiv     %-36s %s" % (m["name"], m["equivalent"]))
            else:
                survivors.append(m["name"])
                print("  SURVIVED  %s" % m["name"])
        elif killers:
            if m.get("equivalent"):
                # The row claims no input can observe this. One just did.
                stale.append((m["name"], killers[0]))
                print("  STALE     %-36s killed by %s" % (m["name"],
                                                          killers[0]))
            else:
                print("  killed    %-36s %s" % (m["name"],
                                                ", ".join(killers[:3])))
        elif "panic:" in out:
            # A mutation that DEADLOCKS is killed by the test binary's
            # own timeout, not by an assertion. Saying so distinguishes
            # it from a build break, which is the other killer with no
            # named test.
            print("  killed    %-36s (panic or deadlock)" % m["name"])
        else:
            # A mutant that does not compile has tested nothing: the
            # compiler rejected an identifier that went unused, and no
            # assertion ever ran. L8 has said since the metrics battery
            # that a BUILDFAIL is a fault in the battery, and this runner
            # used to print it in the killed column and exit 0 anyway —
            # so three rows in the riskmint battery (2026-08-27) counted
            # as coverage they had not measured.
            #
            # But "no named test failed" is not the same question as "did
            # it compile", and reading one off the other put two REAL
            # kills in the battery-bug column (mintground, 2026-08-28):
            # both mutants made splitSentences stop advancing, and the
            # test binary was OOM-killed with `signal: killed` and no
            # `--- FAIL:` line to its name. Ask the compiler instead.
            if gosrc.compiles(pkgs):
                print("  killed    %-36s (hung or died outside a test)"
                      % m["name"])
            else:
                buildfails.append(m["name"])
                print("  !BUILD    %-36s does not compile" % m["name"])

    print()
    if unmatched:
        # Not a warning. A pattern that matches zero sites is a BATTERY
        # BUG, and a pattern that matches two mutates only the first —
        # either way the row measured something other than what it names,
        # and a run that printed "all killed" over it would be lying
        # about its own denominator (P16).
        print("%d SPEC BUG(S): %s" % (len(unmatched),
              ", ".join("%s (%d)" % u for u in unmatched)))
    if buildfails:
        print("%d MUTANT(S) DID NOT COMPILE: %s" % (len(buildfails),
              ", ".join(buildfails)))
        print("\nA build break is not a kill. Re-spell each one so it "
              "compiles — usually by keeping the identifier the deletion "
              "orphaned — and let a test do the killing (L8).")
    if stale:
        print("%d STALE EQUIVALENCE NOTE(S): %s" % (len(stale),
              ", ".join("%s (%s)" % t for t in stale)))
        print("\nA row marked `equivalent` was killed. Either the guard "
              "that made it unobservable moved, or the note was wrong. "
              "Re-read the site before touching the spec.")
    if survivors:
        print("%d SURVIVED: %s" % (len(survivors), ", ".join(survivors)))
        print("\nEach needs a fixture that fails with the mutation applied, "
              "or a comment at the site saying why no input can observe it "
              "(L8). The answer belongs next to the code either way.")
    if unmatched or survivors or buildfails or stale:
        sys.exit(1)
    n_equiv = sum(1 for m in muts if m.get("equivalent"))
    print("all %d mutation(s) killed at least one test (%d expected-"
          "equivalent row(s) survived as documented)"
          % (len(muts) - n_equiv, n_equiv))


if __name__ == "__main__":
    main()
