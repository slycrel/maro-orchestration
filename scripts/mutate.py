#!/usr/bin/env python3
"""Must-detect mutation runner. Applies a mutation, runs a test target,
asserts the tests FAIL, restores. A survivor is a hole in the suite.

Why this exists as a script instead of a scratch file per round: the
§14a slice-3 review arc produced five throwaway harnesses, and every one
of them re-derived the same runner (apply, run, restore, report) plus
the same three mistakes. Landing it once makes a sweep repeatable and
reviewable — and the spec file becomes the durable artifact, so "what
did we actually probe?" has an answer next month (Jeremy decree
2026-08-16; method in HOUSE_STYLE step 3).

**Derive the mutation list from the FILE, not from your own diff.** A
diff-derived list only tests whether your fixes are pinned; a
file-derived list tests whether the behavior is. In the arc that
produced this script, five review rounds and two diff-derived harnesses
walked past twelve real gaps, and the worst was a decree guarded by two
tests that could not fail.

Runs against a `git archive` copy of a committed tree by default, NEVER
the working checkout: a reviewer doing in-tree mutation collided with
two concurrent sessions on 2026-07 and made a sibling's results
unreliable. `--in-place` exists for the case where you are mutating
uncommitted work, and refuses to run if any target file is dirty with
someone else's changes.

Spec file — JSON list, one object per mutation:

    [
      {"name":        "coverage scans the medium tier only",
       "file":        "src/camera_readout.py",
       "anchor":      "for tier in (MemoryTier.MEDIUM, MemoryTier.LONG):",
       "replacement": "for tier in (MemoryTier.MEDIUM,):",
       "tests":       "tests/test_lesson_scope.py"},

      {"name":        "the .get() guard on a pre-seeded key",
       "file":        "src/camera_readout.py",
       "anchor":      "out[scope] = out.get(scope, 0) + 1",
       "replacement": "out[scope] += 1",
       "tests":       "tests/test_lesson_scope.py",
       "equivalent":  "seed loop pre-creates every vocabulary key; the
                       two _LESSON_SCOPES bindings cannot diverge in
                       production"}
    ]

`anchor` must match EXACTLY ONCE in the file — an anchor that matches 0
or 2+ times is reported as a SKIP and fails the run, because a silently
mis-applied mutation reads as a pass. `equivalent` marks a mutant that
cannot fail for a stated reason: it is still applied and run, but a
survival is expected and does not fail the run, while a DETECTED
equivalent is a finding (your reason was wrong). Recording those beats
contorting a test to kill one, which is how a suite starts testing its
own mocks.

Usage:
    python3 scripts/mutate.py specs/scope.json
    python3 scripts/mutate.py specs/scope.json --rev HEAD~1
    python3 scripts/mutate.py specs/scope.json --in-place
    python3 scripts/mutate.py specs/scope.json --only "quarantine"

Every distinct test target is run ONCE unmutated first and must pass —
the negative control. A DETECTED verdict is only "pytest exited
non-zero", so without a baseline an environment where the command fails
for an unrelated reason (no PYTHONPATH, a collection error, a typo'd
path) reports a perfect sweep.

Exit status: 0 only if the baseline passed, every non-equivalent
mutation was DETECTED, and every equivalent one SURVIVED as claimed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _load_spec(path: Path) -> list:
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        sys.exit(f"mutate: cannot read spec {path}: {exc}")
    if not isinstance(spec, list) or not spec:
        sys.exit(f"mutate: spec {path} must be a non-empty JSON list")
    # "replacement" is checked for PRESENCE, not truthiness: deleting the
    # anchored lines outright is a legitimate mutation ("this guard is not
    # there at all"), and `""` is how you write it.
    nonempty = ("name", "file", "anchor", "tests")
    for i, m in enumerate(spec):
        missing = [k for k in nonempty if not m.get(k)]
        if "replacement" not in m:
            missing.append("replacement")
        if missing:
            sys.exit(f"mutate: spec entry {i} missing {', '.join(sorted(missing))}")
    return spec


def _materialize(rev: str) -> Path:
    """git archive <rev> into a temp tree. Committed content only — which
    is the point: a sweep of landed code should not silently include
    whatever happens to be uncommitted in the shared checkout."""
    if _sh(["git", "rev-parse", "--verify", "--quiet", rev + "^{commit}"],
           cwd=REPO).returncode:
        sys.exit(f"mutate: {rev} is not a commit")
    root = Path(tempfile.mkdtemp(prefix="maro-mutate-"))
    ar = subprocess.Popen(["git", "archive", rev], cwd=REPO,
                          stdout=subprocess.PIPE)
    tar = subprocess.Popen(["tar", "-x", "-C", str(root)], stdin=ar.stdout)
    ar.stdout.close()
    tar.communicate()
    if tar.returncode or not (root / "src").is_dir():
        shutil.rmtree(root, ignore_errors=True)
        sys.exit(f"mutate: could not extract {rev}")
    return root


def _refuse_if_dirty(spec) -> None:
    out = _sh(["git", "status", "--porcelain", "--"] +
              sorted({m["file"] for m in spec}), cwd=REPO).stdout.strip()
    if out:
        sys.exit("mutate: --in-place refused, target files are dirty:\n" + out
                 + "\n  A crash mid-run would restore these to the mutated "
                   "state, and in a shared checkout the changes may not be "
                   "yours. Commit them, or drop --in-place.")


def _baseline_ok(spec, root: Path) -> bool:
    """Negative control: every distinct test target must PASS unmutated.

    Without this the runner has the exact flaw it exists to find. A
    DETECTED verdict is just "pytest exited non-zero", so an environment
    where the command fails for an unrelated reason — no PYTHONPATH, no
    pytest on this interpreter, a collection error, a typo'd path — marks
    every mutation detected and reports a clean sweep. The instrument
    built to find false confidence would manufacture it (found 2026-08-16
    by the Experimentalist lens on the path_rewrite sweep, reproduced
    twice). A sweep whose baseline is red has nothing to say.
    """
    for tests in sorted({m["tests"] for m in spec}):
        r = _sh([sys.executable, "-m", "pytest", *tests.split(),
                 "-q", "--no-header", "-p", "no:cacheprovider"], cwd=root)
        if r.returncode != 0:
            print(f"BASELINE FAILED for: {tests}\n"
                  "  The unmutated tests do not pass, so every mutation would\n"
                  "  read as DETECTED and the sweep would mean nothing. Fix the\n"
                  "  target (or the environment) before trusting any verdict.\n"
                  + "\n".join(
                      (r.stdout + r.stderr).strip().splitlines()[-15:]
                      or [f"  (pytest printed nothing; exit {r.returncode})"]))
            return False
        print(f"baseline ok: {tests}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("spec", type=Path)
    ap.add_argument("--rev", default="HEAD",
                    help="commit to extract and mutate (default HEAD)")
    ap.add_argument("--in-place", action="store_true",
                    help="mutate the working checkout instead of a copy")
    ap.add_argument("--only", default="",
                    help="run only mutations whose name contains this")
    ap.add_argument("--keep", action="store_true",
                    help="keep the extracted tree (prints the path)")
    args = ap.parse_args()

    spec = _load_spec(args.spec)
    if args.only:
        spec = [m for m in spec if args.only.lower() in m["name"].lower()]
        if not spec:
            sys.exit(f"mutate: --only {args.only!r} matched no mutation")

    if args.in_place:
        _refuse_if_dirty(spec)
        root, tmp = REPO, None
    else:
        root = tmp = _materialize(args.rev)
        print(f"mutating a copy of {args.rev} at {root}")

    failures, results = [], []
    try:
        if not _baseline_ok(spec, root):
            return 1
        for m in spec:
            name, target = m["name"], root / m["file"]
            equivalent = m.get("equivalent")
            if not target.exists():
                results.append(("SKIP", name, f"no such file: {m['file']}"))
                print(f"{'SKIP':12s} {name}\n             no such file: {m['file']}")
                failures.append(name)
                continue
            orig = target.read_text(encoding="utf-8")
            n = orig.count(m["anchor"])
            if n != 1:
                # Not a warning. An anchor that misses applies NO mutation,
                # so the tests pass and the run reads as covered.
                note = (f"anchor matched {n}x, not 1 — NO mutation was "
                        f"applied, so a 'pass' here would mean nothing")
                results.append(("SKIP", name, note))
                print(f"{'SKIP':12s} {name}\n             {note}")
                failures.append(name)
                continue
            target.write_text(orig.replace(m["anchor"], m["replacement"]),
                              encoding="utf-8")
            try:
                r = _sh([sys.executable, "-m", "pytest", *m["tests"].split(),
                         "-q", "-x", "--no-header", "-p", "no:cacheprovider"],
                        cwd=root)
            finally:
                target.write_text(orig, encoding="utf-8")
                assert target.read_text(encoding="utf-8") == orig, \
                    f"mutate: FAILED TO RESTORE {target} — fix by hand"
            detected = r.returncode != 0
            if equivalent:
                status = "EQUIV-OK" if not detected else "EQUIV-BROKEN"
                note = ("survived as claimed" if not detected else
                        "DETECTED, so it is not equivalent — the stated "
                        "reason is wrong")
                if detected:
                    failures.append(name)
            else:
                status = "DETECTED" if detected else "SURVIVED"
                note = "" if detected else "no test fails on this change"
                if not detected:
                    failures.append(name)
            results.append((status, name, note))
            print(f"{status:12s} {name}" + (f"\n             {note}" if note else ""))
    finally:
        if tmp and not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)
        elif tmp:
            print(f"kept: {tmp}")

    total = len(results)
    print(f"\n{sum(1 for s, _, _ in results if s in ('DETECTED', 'EQUIV-OK'))}"
          f"/{total} accounted for")
    if failures:
        print("NEEDS WORK:\n  " + "\n  ".join(failures))
        print("\nA survivor is a hole in the suite, not a reason to delete the "
              "mutation. Either strengthen the test, or mark the mutant "
              "`equivalent` with the reason it cannot fail.")
        return 1
    print("every mutation accounted for")
    return 0


if __name__ == "__main__":
    sys.exit(main())
