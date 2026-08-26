#!/usr/bin/env python3
"""Run BOTH engines over the same real workspace and byte-diff the output.

This is the second half of the standing goal ("then maybe we can do some
test runs on both engines and compare"), and it is deliberately the CHEAP
half: every command below is a READ-ONLY renderer that both runtimes
already have, so the comparison needs no more porting and no LLM spend.

Safety, non-negotiable:
  * ~/.maro is never written. The live workspace is COPIED to a scratch
    dir and both engines are pointed at the copy via MARO_WORKSPACE.
  * The copy is asserted to be outside ~/.maro before anything runs.

Why ONE workspace path and not two: an engine that prints its resolved
workspace (`maro inspect` does, on purpose) would differ on the PATH alone
if each engine got its own directory, and the only way to compare them
would be to normalise the path out — which moves the assertion into the
normaliser, the exact failure L51 names. So both engines are handed the
SAME path, and the tree is restored from a pristine copy before each run.
Neither can see the other's writes because neither is running when the
other's tree is laid down.

What this measures, and what it does not: it compares STDOUT of read-only
renderers over identical stored data. It does not exercise the loop, does
not spend tokens, and cannot see a divergence that only shows up while
writing. That is the next harness, not this one.

Usage:
    python3 engine_compare.py                # all commands
    python3 engine_compare.py metrics        # one command
"""
import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile

# Everything this harness creates lives under one scratch root, outside the
# repo and outside ~/.maro. Override with MARO_COMPARE_SCRATCH.
SCRATCH = os.environ.get("MARO_COMPARE_SCRATCH") or os.path.join(
    tempfile.gettempdir(), "maro-engine-compare")
LIVE_WS = os.path.expanduser("~/.maro/workspace")
LIVE_UD = os.path.expanduser("~/.maro")
# The Go repo is this file's own grandparent; the Python repo defaults to the
# same tree (src/ sits beside go/) and is overridable for a worktree setup
# where the two live apart.
GO_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_REPO = os.environ.get("MARO_PY_REPO") or os.path.dirname(GO_REPO)
GO_BIN = os.path.join(SCRATCH, "maro-go")

# One path per tier, handed to BOTH engines; PRISTINE_* is the untouched
# master the tree is restored from before each run.
CMP_WS = os.path.join(SCRATCH, "cmp_ws")
CMP_UD = os.path.join(SCRATCH, "cmp_ud")
PRISTINE_WS = os.path.join(SCRATCH, "cmp_pristine_ws")
PRISTINE_UD = os.path.join(SCRATCH, "cmp_pristine_ud")

# Lines that carry a LIVE reading rather than stored data. These cannot be
# compared -- the two engines run seconds apart -- but they must not be
# silently dropped either, which is how a differential ends up asserting
# whatever its normaliser leaves behind. So each entry is a POSITIVE shape:
# the line is elided only if it matches on BOTH sides, the count of elided
# lines has to agree, and every elision is printed. A metrics report that
# stopped printing its timestamp fails here instead of passing quietly.
LIVE_LINES = [
    (re.compile(r"^Computed: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"),
     "metrics report header timestamp"),
]


def elide_live(text):
    """Return (text with live lines replaced by a marker, [(desc, line)])."""
    hits, out = [], []
    for line in text.splitlines(True):
        for rx, desc in LIVE_LINES:
            if rx.match(line.rstrip("\n")):
                hits.append((desc, line.rstrip("\n")))
                out.append("<<live: %s>>\n" % desc)
                break
        else:
            out.append(line)
    return "".join(out), hits


# (name, python argv, go argv). Both are run with cwd at their own repo.
COMMANDS = [
    # cli.py's `metrics` takes NO --limit: _cmd_metrics calls get_metrics()
    # bare and lets the default (100) stand. `100` as a positional is eaten
    # by the pass-k subparser and rejected. The Go exposes -limit, so the
    # comparable invocation is the Go's DEFAULT.
    ("metrics",
     ["python3", "src/cli.py", "metrics"],
     ["metrics"]),
    # introspect and task are their OWN entry points in the Python
    # (maro-introspect = introspect:main, maro-task = task_store:main), not
    # cli.py subcommands. The Go folded both under `maro`, so the argv
    # shapes differ on purpose and only the OUTPUT is compared.
    ("introspect-latest",
     ["python3", "-m", "introspect", "--latest"],
     ["introspect", "--latest"]),
    ("introspect-patterns",
     ["python3", "-m", "introspect", "--patterns"],
     ["introspect", "--patterns"]),
    ("introspect-history",
     ["python3", "-m", "introspect", "--history", "5"],
     ["introspect", "--history", "5"]),
    ("task-list",
     ["python3", "-m", "task_store", "list"],
     ["task", "list"]),
    ("task-status",
     ["python3", "-m", "task_store", "status"],
     ["task", "status"]),
    # The Go has no `inspector-status` command: it folded that text lane into
    # `inspect -summary`. Whether the fold is faithful is exactly what this
    # row asks, so it is here rather than assumed either way.
    ("inspector-status",
     ["python3", "src/cli.py", "inspector-status"],
     ["inspect", "-summary"]),
]


def prepare():
    for p in (CMP_WS, CMP_UD, PRISTINE_WS, PRISTINE_UD):
        real = os.path.realpath(p)
        assert not real.startswith(os.path.realpath(LIVE_UD)), real
    if not os.path.isdir(LIVE_WS):
        sys.exit("no live workspace at %s" % LIVE_WS)
    if os.path.exists(PRISTINE_WS):
        shutil.rmtree(PRISTINE_WS)
    shutil.copytree(LIVE_WS, PRISTINE_WS, symlinks=True,
                    ignore_dangling_symlinks=True)
    if os.path.exists(PRISTINE_UD):
        shutil.rmtree(PRISTINE_UD)
    os.makedirs(PRISTINE_UD)
    cfg = os.path.join(LIVE_UD, "config.yml")
    if os.path.exists(cfg):
        shutil.copy2(cfg, os.path.join(PRISTINE_UD, "config.yml"))
    print("copied the live workspace to a pristine master "
          "(%d entries)" % len(os.listdir(PRISTINE_WS)))


def restore():
    """Lay the pristine tree down at the shared comparison path."""
    for live, master in ((CMP_WS, PRISTINE_WS), (CMP_UD, PRISTINE_UD)):
        if os.path.exists(live):
            shutil.rmtree(live)
        shutil.copytree(master, live, symlinks=True,
                        ignore_dangling_symlinks=True)


def build_go():
    # -mod=readonly, deliberately: a reviewer may be running `go test` in
    # this tree, and P4 says a battery owns the whole working tree. A build
    # that cannot rewrite go.mod/go.sum cannot change what that battery
    # compiles, so it is safe to run alongside one; -mod=mod is not.
    r = subprocess.run(["go", "build", "-mod=readonly", "-o", GO_BIN,
                        "./cmd/maro"],
                       cwd=GO_REPO, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("go build failed:\n" + r.stdout + r.stderr)
    print("built %s" % GO_BIN)


def run(argv, cwd, extra_env=None):
    """Restore the shared tree, then run one engine against it."""
    restore()
    env = dict(os.environ, MARO_WORKSPACE=CMP_WS, MARO_USER_DIR=CMP_UD,
               PYTHONPATH="src", COLUMNS="100", NO_COLOR="1")
    env.update(extra_env or {})
    r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                       text=True, timeout=300)
    return r.returncode, r.stdout, r.stderr


def main():
    wanted = set(sys.argv[1:])
    os.makedirs(SCRATCH, exist_ok=True)
    prepare()
    build_go()
    same, diff, broke = [], [], []
    for name, pyargv, goargv in COMMANDS:
        if wanted and name not in wanted:
            continue
        pc, po, pe = run(pyargv, PY_REPO)
        gc, go_out, ge = run([GO_BIN] + goargv, GO_REPO)
        if pc != 0 and gc != 0:
            broke.append((name, "both refused", pe.strip()[-200:],
                          ge.strip()[-200:]))
            print("%-22s BOTH REFUSED  py=%d go=%d" % (name, pc, gc))
            continue
        if pc != 0 or gc != 0:
            broke.append((name, "one refused", pe.strip()[-300:],
                          ge.strip()[-300:]))
            print("%-22s ONE REFUSED   py=%d go=%d" % (name, pc, gc))
            continue
        pcmp, phits = elide_live(po)
        gcmp, ghits = elide_live(go_out)
        note = ""
        if phits or ghits:
            if len(phits) != len(ghits):
                diff.append(name)
                print("%-22s DIFFERS (live-line counts disagree: "
                      "py=%d go=%d)" % (name, len(phits), len(ghits)))
                continue
            if [d for d, _ in phits] != [d for d, _ in ghits]:
                diff.append(name)
                print("%-22s DIFFERS (different live lines matched)" % name)
                continue
            note = "  [elided %s]" % "; ".join(
                "%s py=%r go=%r" % (d, pl, gl)
                for (d, pl), (_, gl) in zip(phits, ghits))
        if pcmp == gcmp:
            same.append(name)
            print("%-22s identical     (%d bytes)%s" % (name, len(po), note))
            continue
        diff.append(name)
        print("%-22s DIFFERS%s" % (name, note))
        for line in list(difflib.unified_diff(
                pcmp.splitlines(), gcmp.splitlines(),
                "python", "go", lineterm=""))[:60]:
            print("   " + line)
    print("\n%d identical, %d differ, %d refused"
          % (len(same), len(diff), len(broke)))
    for name, why, pe, ge in broke:
        print("\n%s (%s)\n  python: %s\n  go:     %s" % (name, why, pe, ge))


if __name__ == "__main__":
    main()
