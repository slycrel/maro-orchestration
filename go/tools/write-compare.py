#!/usr/bin/env python3
"""Run BOTH engines through the same WRITE sequence and byte-diff the trees.

`engine-compare.py` is the read half: it compares STDOUT of read-only
renderers. It cannot see a divergence that only appears while writing, and
that is where this port's recorded bug families actually live — content-key
prose divergence (a byte-different emitted string mints a duplicate row on a
shared store), key ORDER in a written JSON object, absent vs zero, a created
directory's MODE.

`task` is the first target because it is pure filesystem work, both engines
have all eight verbs, and it is the only ported surface where a whole state
machine writes.

Safety, non-negotiable:
  * ~/.maro is never read as a source and never written. This harness
    builds its trees from scratch; there is nothing here worth seeding from
    the live store, and a write harness pointed at a live path is the
    2026-08-16 accident with a bigger blast radius.
  * Every workspace path is asserted to be under the scratch root, and to
    resolve outside ~/.maro, before anything runs.

Why TWO workspace paths and not one: the read harness hands both engines
the same path so no output can differ on the path alone. That trick is not
available here, because both engines are MUTATING. So each gets its own
tree, and any value that embeds the workspace root becomes volatile — one
more per-field elision, handled the same way as the timestamp: a positive
shape, elided only when it matches on both sides, with the substitution
counts required to agree and every elision printed.

If a field is volatile and is NOT in the elision list, this harness FAILS
rather than guesses. An unexplained difference is the finding, and a
harness that learns to tolerate new volatile fields on its own has moved
the assertion into itself (L51).

Usage:
    python3 write-compare.py                 # every scenario, plus self-test
    python3 write-compare.py enqueue-claim   # one scenario
    python3 write-compare.py --no-selftest   # skip the harness's own proof
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SCRATCH = os.environ.get("MARO_WRITE_COMPARE_SCRATCH") or os.path.join(
    tempfile.gettempdir(), "maro-write-compare")
LIVE_UD = os.path.expanduser("~/.maro")
GO_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_REPO = os.environ.get("MARO_PY_REPO") or os.path.dirname(GO_REPO)
GO_BIN = os.path.join(SCRATCH, "maro-go")

# Shapes. A job id or a timestamp is elided only when BOTH sides produce
# something matching these; a side that stops emitting one fails here
# instead of passing quietly.
JOB_ID_RE = re.compile(r"task-\d{8}T\d{6}Z-[0-9a-f]{8}")
TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?"
                   r"(?:Z|[+-]\d{2}:\d{2})?")
# `run_id` is str(uuid.uuid4()) (task_store.py:66). The shape is pinned to
# version 4 on purpose — the `4` and the `[89ab]` variant nibble are part of
# what is being asserted. A side that starts emitting a v1 uuid, a
# zero-uuid, or a bare hex string does not match, so its elision count drops
# and the counts-must-agree check reports it instead of erasing it.
UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}"
                      r"-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
# `claimed_by_pid` is os.getpid() of whichever process claimed, so the two
# engines can never agree on it. Key-SCOPED, not a bare number: a harness
# that elided every integer would erase the counts and the absent-vs-zero
# family along with the pid. And the shape starts [1-9], so a pid of 0 —
# which would be a real finding, not volatility — survives to the diff.
# `null` also survives: claimed_by_pid is set back to None on release
# (task_store.py:267,287) and "None vs 0" is precisely the divergence class
# this port keeps hitting.
PID_RE = re.compile(r'("claimed_by_pid":\s*)[1-9]\d{0,6}')

# (name, [argv-template...]) — one template per command, run in order
# against the same tree. "{job}" is the id captured from the first enqueue.
# Templates are engine-agnostic; ENGINES below turns each into real argv.
SCENARIOS = [
    ("enqueue", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "compare me"],
    ]),
    ("enqueue-claim", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "compare me"],
        ["claim", "{job}"],
    ]),
    ("enqueue-claim-complete", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "compare me"],
        ["claim", "{job}"],
        ["complete", "{job}"],
    ]),
    ("enqueue-claim-fail", [
        ["enqueue", "--lane", "agenda", "--source", "cli", "--reason", "compare me"],
        ["claim", "{job}"],
        # The error string rides straight into a written field: the
        # content-key family's home ground.
        ["fail", "{job}", "--error", "boom: it did not work"],
    ]),
    ("enqueue-claim-complete-archive", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "compare me"],
        ["claim", "{job}"],
        ["complete", "{job}"],
        # Archive creates a directory. Directory MODE is a named open
        # thread in this port (33 sites at 0o755 vs Python's 0o777&umask).
        ["archive", "{job}"],
    ]),
    ("enqueue-blocked-by", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "first"],
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "second",
         "--blocked-by", "{job}"],
    ]),
    ("recover-nothing-stale", [
        ["enqueue", "--lane", "now", "--source", "cli", "--reason", "compare me"],
        ["claim", "{job}"],
        # Nothing is stale yet, so this must be a NO-OP on both sides —
        # including not rewriting the file with a fresh timestamp.
        ["recover"],
    ]),
]


def engine_argv(engine, template):
    if engine == "py":
        return ["python3", "-m", "task_store"] + template
    return [GO_BIN, "task"] + template


ENGINES = [("py", PY_REPO), ("go", GO_REPO)]


def assert_safe(path):
    real = os.path.realpath(path)
    live = os.path.realpath(LIVE_UD)
    if real == live or real.startswith(live + os.sep):
        sys.exit("refusing to run: %s resolves inside the live store %s"
                 % (path, live))
    if not real.startswith(os.path.realpath(SCRATCH) + os.sep):
        sys.exit("refusing to run: %s is outside the scratch root %s"
                 % (path, SCRATCH))


def build_go():
    # -mod=readonly deliberately: a reviewer may be running `go test` in
    # this tree, and a battery owns the whole working tree (P4). A build
    # that cannot rewrite go.mod/go.sum cannot change what that battery
    # compiles.
    r = subprocess.run(["go", "build", "-mod=readonly", "-o", GO_BIN,
                        "./cmd/maro"],
                       cwd=GO_REPO, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("go build failed:\n" + r.stdout + r.stderr)


def run_sequence(engine, cwd, ws, templates):
    """Run one scenario against one fresh tree. Returns (ids, transcript)."""
    assert_safe(ws)
    os.makedirs(ws)
    env = dict(os.environ, MARO_WORKSPACE=ws,
               MARO_USER_DIR=os.path.join(ws, "_userdir"),
               PYTHONPATH="src", COLUMNS="100", NO_COLOR="1")
    os.makedirs(env["MARO_USER_DIR"])
    ids, transcript = [], []
    for template in templates:
        argv = engine_argv(engine, [
            t.replace("{job}", ids[0]) if "{job}" in t else t
            for t in template])
        if "{job}" in "".join(template) and not ids:
            sys.exit("scenario references {job} before any enqueue ran")
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                           text=True, timeout=120)
        transcript.append((template[0], r.returncode, r.stdout, r.stderr))
        if r.returncode != 0:
            break
        for m in JOB_ID_RE.finditer(r.stdout):
            if m.group(0) not in ids:
                ids.append(m.group(0))
    return ids, transcript


def snapshot(root):
    """{relpath: (kind, mode, bytes-or-target)} for every entry under root.

    Directories are included with their MODE, because a created directory's
    mode is one of this port's named open threads and a file-only walk
    cannot see it. The _userdir the harness itself creates is excluded: it
    is harness scaffolding, not engine output.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "_userdir")
        for name in dirnames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            out[rel] = ("dir", stat.S_IMODE(os.lstat(p).st_mode), b"")
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", 0, os.readlink(p).encode())
                continue
            with open(p, "rb") as f:
                out[rel] = ("file", stat.S_IMODE(st.st_mode), f.read())
    return out


class Elisions(object):
    """Per-side substitution counts, so both sides can be required to agree."""

    def __init__(self):
        self.counts = {}

    def bump(self, kind, n):
        if n:
            self.counts[kind] = self.counts.get(kind, 0) + n

    def __repr__(self):
        return repr(sorted(self.counts.items()))


def normalize(text, ws, ids, el):
    """Replace the volatile shapes with fixed tokens, counting each.

    Order matters in one place: the job id embeds an 8-hex tail and the
    timestamp regex would chew the `20260826T...` inside it, so job ids go
    first. UUIDs are matched before nothing in particular, but they are
    matched by a shape no other field here has.
    """
    for i, job in enumerate(ids):
        token = "<JOB%d>" % (i + 1)
        text, n = re.subn(re.escape(job), token, text)
        el.bump("job-id", n)
    text, n = re.subn(re.escape(ws), "<WS>", text)
    el.bump("workspace-path", n)
    text, n = TS_RE.subn("<TS>", text)
    el.bump("timestamp", n)
    text, n = UUID4_RE.subn("<UUID4>", text)
    el.bump("uuid4", n)
    text, n = PID_RE.subn(r"\1<PID>", text)
    el.bump("pid", n)
    return text


def normalize_snapshot(snap, ws, ids, el):
    out = {}
    for rel, (kind, mode, blob) in snap.items():
        rel_n = normalize(rel, ws, ids, el)
        try:
            body = normalize(blob.decode("utf-8"), ws, ids, el).encode("utf-8")
        except UnicodeDecodeError:
            body = blob  # compared raw; a non-UTF-8 write IS the finding
        out[rel_n] = (kind, mode, body)
    return out


def compare(name, left, right, lname, rname):
    """Print every difference. Returns the number of differing entries."""
    diffs = 0
    for rel in sorted(set(left) | set(right)):
        lv, rv = left.get(rel), right.get(rel)
        if lv is None:
            print("   only in %s: %s" % (rname, rel))
            diffs += 1
            continue
        if rv is None:
            print("   only in %s: %s" % (lname, rel))
            diffs += 1
            continue
        lk, lm, lb = lv
        rk, rm, rb = rv
        if lk != rk:
            print("   %s: kind %s vs %s" % (rel, lk, rk))
            diffs += 1
            continue
        if lm != rm:
            print("   %s: MODE %o vs %o" % (rel, lm, rm))
            diffs += 1
        if lb != rb:
            print("   %s: bytes differ" % rel)
            for line in _blob_diff(lb, rb, lname, rname):
                print("     " + line)
            diffs += 1
    return diffs


def _blob_diff(a, b, lname, rname):
    import difflib
    try:
        al = a.decode("utf-8").splitlines()
        bl = b.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ["<binary> %d bytes vs %d bytes" % (len(a), len(b))]
    return list(difflib.unified_diff(al, bl, lname, rname, lineterm=""))[:40]


def run_scenario(name, templates):
    trees, ids_by, els, transcripts = {}, {}, {}, {}
    for engine, cwd in ENGINES:
        ws = os.path.join(SCRATCH, "%s_%s" % (name, engine))
        if os.path.exists(ws):
            shutil.rmtree(ws)
        ids, transcript = run_sequence(engine, cwd, ws, templates)
        transcripts[engine] = transcript
        codes = [c for _, c, _, _ in transcript]
        if any(c != 0 for c in codes):
            bad = next(t for t in transcript if t[1] != 0)
            print("%-32s %s REFUSED at `%s` (exit %d): %s"
                  % (name, engine, bad[0], bad[1], bad[3].strip()[-200:]))
            return "refused"
        # A job id is required wherever the scenario spends one. Missing
        # ids would make the elision vacuous and the comparison hollow.
        if "{job}" in "".join("".join(t) for t in templates) and not ids:
            print("%-32s %s emitted NO job id matching %s"
                  % (name, engine, JOB_ID_RE.pattern))
            return "refused"
        el = Elisions()
        ids_by[engine] = ids
        trees[engine] = normalize_snapshot(snapshot(ws), ws, ids, el)
        els[engine] = el

    # Elision counts must agree. A side that emitted five timestamps where
    # the other emitted four has a real difference that the normaliser
    # would otherwise have erased.
    if els["py"].counts != els["go"].counts:
        print("%-32s DIFFERS (elision counts disagree: py=%s go=%s)"
              % (name, els["py"], els["go"]))
        return "differ"
    if len(ids_by["py"]) != len(ids_by["go"]):
        print("%-32s DIFFERS (job-id counts: py=%d go=%d)"
              % (name, len(ids_by["py"]), len(ids_by["go"])))
        return "differ"

    n = len(trees["py"])
    if n == 0:
        print("%-32s COMPARED NOTHING — zero entries is an error, not a pass"
              % name)
        return "refused"
    diffs = compare(name, trees["py"], trees["go"], "python", "go")
    detail = "  [%d entries; elided %s; ids py=%s go=%s]" % (
        n, els["py"], ids_by["py"], ids_by["go"])
    if diffs == 0:
        print("%-32s identical%s" % (name, detail))
        return "same"
    print("%-32s DIFFERS in %d entr%s%s"
          % (name, diffs, "y" if diffs == 1 else "ies", detail))
    return "differ"


def selftest():
    """Prove the differ can FAIL before believing anything it says.

    A tree-differ that reports "identical" for two trees it never read is
    the exact failure P10 exists for, and it is easy to write by accident:
    a walk over the wrong root finds nothing, and finds nothing
    symmetrically. So: two hand-built trees that differ in one byte, one
    mode, and one missing file, and the differ must find all three.
    """
    base = os.path.join(SCRATCH, "_selftest")
    if os.path.exists(base):
        shutil.rmtree(base)
    a, b = os.path.join(base, "a"), os.path.join(base, "b")
    for root in (a, b):
        os.makedirs(os.path.join(root, "sub"))
        with open(os.path.join(root, "same.txt"), "w") as f:
            f.write("identical\n")
    with open(os.path.join(a, "sub", "x.json"), "w") as f:
        f.write('{"k": 1}\n')
    with open(os.path.join(b, "sub", "x.json"), "w") as f:
        f.write('{"k": 2}\n')
    with open(os.path.join(a, "only-in-a.txt"), "w") as f:
        f.write("\n")
    os.chmod(os.path.join(b, "sub"), 0o700)
    os.chmod(os.path.join(a, "sub"), 0o755)

    print("self-test: the differ must report three differences")
    diffs = compare("_selftest", snapshot(a), snapshot(b), "a", "b")
    if diffs != 3:
        sys.exit("SELF-TEST FAILED: the differ reported %d differences, not "
                 "3. Nothing this harness says can be trusted." % diffs)
    same = compare("_selftest", snapshot(a), snapshot(a), "a", "a")
    if same != 0:
        sys.exit("SELF-TEST FAILED: a tree differs from itself (%d)" % same)
    print("self-test: ok (3 found, 0 false positives)\n")


def normalize_selftest():
    """Prove each elision fires on the shape it claims — and ONLY on it.

    An elision is an assertion that a field is volatile, and a too-greedy
    one erases findings silently: the run still prints "identical". So each
    shape is checked in both directions, and the negative half is the half
    that matters.
    """
    ws = "/scratch/ws"
    cases = [
        # (text, expected-counts) — counts, not just "did something change",
        # because the comparison this feeds requires the two sides' counts
        # to agree and a doubled substitution would break that silently.
        ('{"run_id": "0d8f4a1e-8e2c-4a1f-9b3d-2c4e6f8a0b1d"}', {"uuid4": 1}),
        # v1 uuid: version nibble is 1, so it must NOT be elided.
        ('{"run_id": "0d8f4a1e-8e2c-1a1f-9b3d-2c4e6f8a0b1d"}', {}),
        # all-zero uuid: a plausible "we forgot to generate one" bug.
        ('{"run_id": "00000000-0000-0000-0000-000000000000"}', {}),
        ('{"claimed_by_pid": 12345}', {"pid": 1}),
        # pid 0 is a finding, not volatility.
        ('{"claimed_by_pid": 0}', {}),
        # null is the released state — absent-vs-zero lives here.
        ('{"claimed_by_pid": null}', {}),
        # A number under a DIFFERENT key must survive untouched.
        ('{"attempts": 12345}', {}),
        ('{"created_at": "2026-08-26T10:11:12Z"}', {"timestamp": 1}),
        ('{"root": "/scratch/ws/queue"}', {"workspace-path": 1}),
    ]
    for text, want in cases:
        el = Elisions()
        got = normalize(text, ws, [], el)
        if el.counts != want:
            sys.exit("SELF-TEST FAILED: normalize(%r) elided %s, expected %s "
                     "(result %r)" % (text, el.counts, want, got))
    print("normalize self-test: ok (%d shapes, positive and negative)\n"
          % len(cases))


def main():
    args = [a for a in sys.argv[1:] if a != "--no-selftest"]
    os.makedirs(SCRATCH, exist_ok=True)
    if "--no-selftest" not in sys.argv[1:]:
        selftest()
        normalize_selftest()
    build_go()
    wanted = set(args)
    tally = {"same": [], "differ": [], "refused": []}
    for name, templates in SCENARIOS:
        if wanted and name not in wanted:
            continue
        tally[run_scenario(name, templates)].append(name)
    print("\n%d identical, %d differ, %d refused"
          % (len(tally["same"]), len(tally["differ"]), len(tally["refused"])))
    return 1 if tally["differ"] or tally["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
