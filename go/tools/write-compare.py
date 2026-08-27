#!/usr/bin/env python3
"""Run BOTH engines through the same WRITE sequence and byte-diff the trees.

`engine-compare.py` is the read half: it compares STDOUT of read-only
renderers. It cannot see a divergence that only appears while writing, and
that is where this port's recorded bug families actually live — content-key
prose divergence (a byte-different emitted string mints a duplicate row on a
shared store), key ORDER in a written JSON object, absent vs zero, a created
directory's MODE.

`task` was the first target because it is pure filesystem work, both engines
have all eight verbs, and it is the only ported surface where a whole state
machine writes. `pack` is the second and the more consequential one: it is
the INTEROP format. "Lessons are data" means a pack is how one machine's
learning reaches another, so two engines disagreeing about a pack's bytes is
two engines disagreeing about a shared artifact, not about their own output.

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
    python3 write-compare.py enqueue-claim   # one scenario (name is checked)
    python3 write-compare.py --no-selftest   # skip the harness's own proof
"""
import collections
import fnmatch
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
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
# `pack_tag` is f"{name}@{sha256(pack_file)[:8]}" (pack.py:1100) and the
# archive it digests contains `created_at` with microseconds, so the tag
# differs between two exports of the SAME workspace by the same runtime.
# Measured, not assumed: two CPython exports 119ms apart produced inner-tar
# digests 28b724195933 and 3865dc7764ab. (The archive is otherwise
# deterministic — pack.py:246 pins every tar member's mtime to 0, and the
# gzip header mtime matched across both runs.) Key-SCOPED to the two fields
# that carry it, so an @-suffixed value anywhere else still reaches the diff.
PACK_TAG_RE = re.compile(r'("pack(?:_tag)?":\s*")([A-Za-z0-9._-]+)@[0-9a-f]{8}"')

# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------
#
# A scenario is (name, module, [step, ...]). Each step is a Step(py, go) of
# argv TAILS; MODULES supplies the per-engine prefix. `same(...)` is for the
# common case where the two CLIs take identical arguments.
#
# The two CLIs do NOT always agree on argv, and that is a difference this
# harness must NOT paper over: `pack.py` takes the pack name and the adopt
# label as POSITIONALS where the Go takes `-name` / `-label`, and spells
# `--out-dir` as `-out`. Those spellings are filed in BACKLOG as a surface
# difference; here they are written out per engine so the comparison is
# about what gets WRITTEN, not about how it was invoked.
#
# Tokens substituted in either side: "{job}" (the id from the first
# enqueue), "{ws}" (that engine's workspace root).

Step = collections.namedtuple("Step", "py go")


def same(argv):
    return Step(argv, argv)


MODULES = {
    "task": {"py": ["python3", "-m", "task_store"], "go": ["task"]},
    "pack": {"py": ["python3", "-m", "pack"], "go": ["pack"]},
}

TASK_SCENARIOS = [
    ("enqueue", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "compare me"]),
    ]),
    ("enqueue-claim", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "compare me"]),
        same(["claim", "{job}"]),
    ]),
    ("enqueue-claim-complete", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "compare me"]),
        same(["claim", "{job}"]),
        same(["complete", "{job}"]),
    ]),
    ("enqueue-claim-fail", "task", [
        same(["enqueue", "--lane", "agenda", "--source", "cli",
              "--reason", "compare me"]),
        same(["claim", "{job}"]),
        # The error string rides straight into a written field: the
        # content-key family's home ground. It is also the argument Go's
        # `flag` silently DROPPED here until 2026-08-26, which is what this
        # harness found on its first run — so this step is a regression pin
        # as much as a comparison.
        same(["fail", "{job}", "--error", "boom: it did not work"]),
    ]),
    ("enqueue-claim-complete-archive", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "compare me"]),
        same(["claim", "{job}"]),
        same(["complete", "{job}"]),
        # Archive creates a directory. Directory MODE is a named open
        # thread in this port (33 sites at 0o755 vs Python's 0o777&umask).
        same(["archive", "{job}"]),
    ]),
    ("enqueue-blocked-by", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "first"]),
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "second", "--blocked-by", "{job}"]),
    ]),
    ("recover-nothing-stale", "task", [
        same(["enqueue", "--lane", "now", "--source", "cli",
              "--reason", "compare me"]),
        same(["claim", "{job}"]),
        # Nothing is stale yet, so this must be a NO-OP on both sides —
        # including not rewriting the file with a fresh timestamp.
        same(["recover"]),
    ]),
]

# `pack` is the highest-stakes write surface in this port, because it is the
# INTEROP format: "lessons are data", and a pack is how one machine's data
# reaches another. A byte divergence here is not a cosmetic difference
# between two engines, it is two engines disagreeing about what a shared
# artifact says.
PACK_SCENARIOS = [
    ("pack-export", "pack", [
        Step(["export", "cmp-pack", "--label", "compare-me",
              "--include-medium", "--include-knowledge", "--include-playbook"],
             ["export", "-name", "cmp-pack", "-label", "compare-me",
              "-include-medium", "-include-knowledge", "-include-playbook"]),
    ]),
    ("pack-export-seal", "pack", [
        Step(["export", "cmp-pack", "--label", "compare-me",
              "--include-medium", "--include-knowledge", "--include-playbook"],
             ["export", "-name", "cmp-pack", "-label", "compare-me",
              "-include-medium", "-include-knowledge", "-include-playbook"]),
        Step(["seal", "{ws}/output/packs/cmp-pack.maropack.tar.gz", "--yes"],
             ["seal", "-pack", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "-yes"]),
    ]),
    ("pack-export-minimal", "pack", [
        # No --include-* flags: the artifact set is smaller, and an
        # artifact that is EMPTY is skipped rather than shipped with zero
        # rows. Absence-vs-empty, in the format both engines have to agree
        # on.
        Step(["export", "cmp-pack", "--label", "compare-me"],
             ["export", "-name", "cmp-pack", "-label", "compare-me"]),
    ]),
    ("pack-roundtrip", "pack", [
        Step(["export", "cmp-pack", "--label", "compare-me",
              "--include-medium", "--include-knowledge", "--include-playbook"],
             ["export", "-name", "cmp-pack", "-label", "compare-me",
              "-include-medium", "-include-knowledge", "-include-playbook"]),
        Step(["seal", "{ws}/output/packs/cmp-pack.maropack.tar.gz", "--yes"],
             ["seal", "-pack", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "-yes"]),
        # Import into a SECOND workspace nested under the snapshot root, so
        # both the source and the destination of the transfer are compared
        # in one tree.
        Step(["import", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "--label", "from-elsewhere", "--target", "{ws}/inbox"],
             ["import", "-pack", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "-label", "from-elsewhere", "-target", "{ws}/inbox"]),
        # Adopt promotes out of quarantine: a second write, into a tree the
        # import just created.
        Step(["adopt", "from-elsewhere", "--all", "--target", "{ws}/inbox"],
             ["adopt", "-label", "from-elsewhere", "-all",
              "-target", "{ws}/inbox"]),
    ]),
    ("pack-import-dry-run", "pack", [
        Step(["export", "cmp-pack", "--label", "compare-me",
              "--include-medium"],
             ["export", "-name", "cmp-pack", "-label", "compare-me",
              "-include-medium"]),
        Step(["seal", "{ws}/output/packs/cmp-pack.maropack.tar.gz", "--yes"],
             ["seal", "-pack", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "-yes"]),
        # --dry-run must write NOTHING. The tree after this is the tree
        # before it, on both engines, or the flag is a lie.
        Step(["import", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "--label", "from-elsewhere", "--target", "{ws}/inbox",
              "--dry-run"],
             ["import", "-pack", "{ws}/output/packs/cmp-pack.maropack.tar.gz",
              "-label", "from-elsewhere", "-target", "{ws}/inbox",
              "-dry-run"]),
    ]),
]

# Same commands as `pack-roundtrip`, different SEED (see SCENARIO_SEEDERS).
# An id-less row is only observable once it reaches import, so the scenario
# has to run the whole export -> seal -> import -> adopt chain; sharing the
# step list rather than copying it is what keeps the two from drifting into
# comparing different things while claiming to differ only in the seed.
PACK_SCENARIOS.append((
    "pack-roundtrip-idless", "pack",
    next(s for (n, _m, s) in PACK_SCENARIOS if n == "pack-roundtrip"),
))

SCENARIOS = TASK_SCENARIOS + PACK_SCENARIOS

# Scenarios whose module needs a seeded workspace before the first command.
# `task` starts from nothing by design; `pack` gathers what is already
# there, so an unseeded run would export an empty pack and compare two
# nothings — which is the P10 failure this harness exists to avoid.
SEEDERS = {"pack": "seed_pack_workspace"}
# Per-SCENARIO overrides win over the per-module default above.
SCENARIO_SEEDERS = {"pack-roundtrip-idless": "seed_pack_workspace_idless"}


def seed_pack_workspace_idless(ws):
    """The happy-path seed plus one identity-less row per trust lane.

    Its own scenario, not a row smuggled into the main seed: an id-less row
    exercises exactly one documented divergence, and a fixture that mixes it
    with the ordinary path can no longer say which rule it is measuring
    (L41).
    """
    seed_pack_workspace(ws)
    for rel, row in (
        ("memory/standing_rules.jsonl", {"rule": "no-id-on-purpose",
                                         "source": "human"}),
        ("memory/hypotheses.jsonl", {"lesson": "no-id-on-purpose",
                                     "status": "open"}),
        ("memory/long/lessons.jsonl", {"lesson": "no-id-on-purpose",
                                       "tier": "long"}),
        ("memory/skills.jsonl", {"name": "no-id-on-purpose", "uses": 1}),
    ):
        with io.open(os.path.join(ws, rel), "a", encoding="utf-8",
                     newline="") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def seed_pack_workspace(ws):
    """Write the artifact set `pack export` gathers (pack.py:363-392).

    Deliberately awkward content, because the export path SCRUBS text and
    the scrubber is exactly the kind of code where two engines drift:
    a home path, a hostname, a lone-surrogate-free but non-ASCII string,
    an emoji, a CRLF line, and a row the provenance gate must WITHHOLD
    (minted_from="prompt") whose withholding has to show up as a count and
    not as a silently shorter file.
    """
    def w(rel, text):
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    w("skills/alpha.md", "# alpha\n\nlives at " + os.path.expanduser("~") +
      "/things and on host " + (os.uname().nodename) + ".\n")
    w("skills/beta.md", "# beta\n\ncaf\u00e9 \u2014 na\u00efve \U0001f9ed\r\nsecond line\n")
    w("personas/gamma.md", "# gamma\n\nnothing to scrub here.\n")
    w("playbook.md", "# playbook\n\n- one rule\n- another\n")

    def jsonl(rel, rows):
        w(rel, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

    # Every trust-lane row carries its identity field. That is not
    # decoration: `pack.import` mints the arriving id as
    # f"imported-{pack}-{original_id}", so an id-LESS row collapses onto
    # "imported-<pack>-" in CPython and the second such row is silently
    # eaten as "already_imported". The port refuses those rows instead
    # (import.go:385-398, a documented divergence). A seed made only of
    # id-less rows therefore measures nothing but that one known
    # difference and hides whether the ordinary path agrees (L41). So the
    # seed carries WELL-FORMED rows for the happy path, plus exactly one
    # id-less row per lane, which lives in its own scenario
    # (`pack-roundtrip-idless`) so the divergence stays visible without
    # contaminating every other measurement.
    jsonl("memory/skills.jsonl", [
        {"id": "S-1", "name": "alpha", "uses": 3, "wins": 2},
        {"id": "S-2", "name": "beta", "uses": 0, "wins": 0},
    ])
    jsonl("memory/standing_rules.jsonl", [
        {"rule_id": "R-1", "rule": "always read the review", "source": "human"},
        {"rule_id": "R-2", "rule": "caf\u00e9 rules apply", "source": "human"},
    ])
    jsonl("memory/hypotheses.jsonl", [
        {"hyp_id": "H-1", "lesson": "packs travel", "status": "open",
         "confirmations": 2, "contradictions": 1},
    ])
    jsonl("memory/long/lessons.jsonl", [
        {"lesson_id": "L-1", "lesson": "a kept lesson", "tier": "long",
         "score": 0.9, "uses": 4},
        # Must be WITHHELD and counted, not shipped (pack.py:323-330).
        {"lesson_id": "L-2", "lesson": "a quarantined lesson",
         "minted_from": "prompt"},
        {"lesson_id": "L-3", "lesson": "caf\u00e9 in a lesson", "tier": "long"},
    ])
    jsonl("memory/medium/lessons.jsonl", [
        {"lesson_id": "M-1", "lesson": "a medium lesson", "tier": "medium",
         "score": 0.4},
    ])
    jsonl("memory/knowledge_nodes.jsonl", [{"id": "n1", "label": "node one"}])
    jsonl("memory/knowledge_edges.jsonl", [{"src": "n1", "dst": "n1"}])


def engine_argv(engine, module, tail):
    if engine == "py":
        return MODULES[module]["py"] + tail
    return [GO_BIN] + MODULES[module]["go"] + tail


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


def run_sequence(engine, cwd, ws, module, steps, scenario):
    """Run one scenario against one fresh tree. Returns (ids, transcript)."""
    assert_safe(ws)
    os.makedirs(ws)
    env = dict(os.environ, MARO_WORKSPACE=ws,
               MARO_USER_DIR=os.path.join(ws, "_userdir"),
               PYTHONPATH="src", COLUMNS="100", NO_COLOR="1")
    os.makedirs(env["MARO_USER_DIR"])
    seeder = SCENARIO_SEEDERS.get(scenario) or SEEDERS.get(module)
    if seeder:
        globals()[seeder](ws)
    ids, transcript = [], []
    for step in steps:
        tail = step.py if engine == "py" else step.go
        if "{job}" in "".join(tail) and not ids:
            sys.exit("scenario references {job} before any enqueue ran")
        argv = engine_argv(engine, module, [
            t.replace("{job}", ids[0] if ids else "").replace("{ws}", ws)
            for t in tail])
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                           text=True, timeout=300)
        transcript.append((tail[0], r.returncode, r.stdout, r.stderr))
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

    A `.tar.gz` is EXPANDED rather than byte-compared, and the reason is
    stated because it is the one place this harness looks away from bytes:
    gzip stores an mtime in its own header and tar stores an mtime per
    member, so two archives of identical content differ in bytes for
    reasons that are not about either engine. What IS compared, for each
    archive: the member ORDER as a synthetic entry (row order is part of
    the answer), and every member's name, type, MODE and content. A
    divergence that survives that is real; a divergence in the gz framing
    is not one this harness can attribute.
    """
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "_userdir")
        for name in dirnames:
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            # A symlink TO a directory is in dirnames, not filenames, and
            # os.walk's default followlinks=False means its contents are
            # never walked. Recording it as an ordinary directory therefore
            # discards the only observable thing about it: two trees with
            # `link -> left` and `link -> right` compared EQUAL, because
            # both sides read ("dir", 0o777, b"") from the LINK's own lstat
            # (adversarial r10, MEDIUM — a blind spot in the instrument,
            # which is worse than a blind spot in the port, since it is the
            # thing that decides whether the port has one).
            #
            # Same helper shape as the port's own pypath.EntryIsDir problem
            # one layer down, arrived at from the opposite side: there the
            # bug was asking lstat's question where CPython asks stat's;
            # here it is recording stat's answer where only lstat's is true.
            if os.path.islink(p):
                out[rel] = ("link", 0, os.readlink(p).encode())
                continue
            out[rel] = ("dir", stat.S_IMODE(os.lstat(p).st_mode), b"")
        for name in sorted(filenames):
            p = os.path.join(dirpath, name)
            rel = os.path.relpath(p, root)
            st = os.lstat(p)
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", 0, os.readlink(p).encode())
                continue
            if name.endswith(".tar.gz"):
                out[rel] = ("targz", 0, b"<expanded; see members>")
                expand_targz(p, rel, out)
                continue
            with open(p, "rb") as f:
                out[rel] = ("file", stat.S_IMODE(st.st_mode), f.read())
    return out


def expand_targz(path, rel, out):
    """Add one entry per archive member, plus the member ORDER."""
    try:
        tf = tarfile.open(path, "r:gz")
    except Exception as e:                      # a corrupt archive IS a finding
        out[rel + "!<unreadable>"] = ("tarerror", 0, repr(e).encode())
        return
    with tf:
        names = []
        for m in tf:                            # archive order, not sorted
            names.append(m.name)
            key = "%s!%s" % (rel, m.name)
            if m.isdir():
                out[key] = ("tar-dir", m.mode, b"")
            elif m.issym() or m.islnk():
                out[key] = ("tar-link", m.mode, m.linkname.encode())
            elif m.isfile():
                f = tf.extractfile(m)
                out[key] = ("tar-file", m.mode, f.read() if f else b"")
            else:
                out[key] = ("tar-other:%d" % m.type, m.mode, b"")
        out[rel + "!<member-order>"] = (
            "tar-order", 0, "\n".join(names).encode("utf-8"))


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
    text, n = PACK_TAG_RE.subn(r'\1\2@<PACKSHA>"', text)
    el.bump("pack-tag", n)
    return text


# ---------------------------------------------------------------------------
# Known divergences
# ---------------------------------------------------------------------------
#
# A divergence the port made ON PURPOSE and documented at its site. Listing
# it here keeps it OUT of the difference count so a new divergence in the
# same run stays visible - and every row carries a must-still-be-observed
# rule, so an entry that stops matching FAILS the scenario instead of
# quietly certifying nothing. That is the same discipline the fssort class
# guard uses, and it is the only thing separating an allowlist from a
# blindfold.
#
# Each row: (scenario-glob, entry-glob, why). fnmatch semantics.
KNOWN_DIVERGENCES = [
    ("*", "<banner>/*",
     "Every Go verb prints `workspace: <path>` before any write "
     "(live-store discipline, the 2026-08-16 incident); the Python CLIs "
     "print nothing. A deliberate ADDITION by the port. It is split out of "
     "the stream into its OWN entry rather than allowlisting the stream, "
     "so the report each CLI prints around it is still compared byte for "
     "byte."),
    ("pack-roundtrip", "inbox/memory/skills.jsonl*",
     "v1 divergence named in the port's own package doc "
     "(internal/pack/import.go:15): this runtime has no skills store yet, "
     "so skill_records quarantine to imports/<label>/ rather than "
     "half-importing. The rows are preserved and the outcome string says "
     "so."),
    ("pack-roundtrip", "inbox/imports/from-elsewhere/memory/skills.jsonl*",
     "The other half of the same named v1 divergence: where those rows go "
     "instead."),
]


def known_reason(scenario, entry):
    for scen_pat, entry_pat, why in KNOWN_DIVERGENCES:
        if fnmatch.fnmatch(scenario, scen_pat) and fnmatch.fnmatch(entry,
                                                                   entry_pat):
            return (scen_pat, entry_pat, why)
    return None


def compare(name, left, right, lname, rname, scenario=None):
    """Print every difference. Returns (new_diffs, known_hits).

    `scenario` None means "no allowlist applies" - that is what the
    self-test passes, so the self-test can never be quieted by a row
    written for a real scenario.
    """
    diffs = 0
    known_hits = set()

    def note(entry, line):
        hit = known_reason(scenario, entry) if scenario else None
        if hit:
            known_hits.add((hit[0], hit[1]))
            print("   [known] " + line)
            return 0
        print("   " + line)
        return 1

    for rel in sorted(set(left) | set(right)):
        lv, rv = left.get(rel), right.get(rel)
        if lv is None:
            diffs += note(rel, "only in %s: %s" % (rname, rel))
            continue
        if rv is None:
            diffs += note(rel, "only in %s: %s" % (lname, rel))
            continue
        lk, lm, lb = lv
        rk, rm, rb = rv
        if lk != rk:
            diffs += note(rel, "%s: kind %s vs %s" % (rel, lk, rk))
            continue
        if lm != rm:
            diffs += note(rel, "%s: MODE %o vs %o" % (rel, lm, rm))
        if lb != rb:
            n = note(rel, "%s: bytes differ" % rel)
            if n:
                for line in _blob_diff(lb, rb, lname, rname):
                    print("     " + line)
            diffs += n
    return diffs, known_hits


def _blob_diff(a, b, lname, rname):
    import difflib
    try:
        al = a.decode("utf-8").splitlines()
        bl = b.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return ["<binary> %d bytes vs %d bytes" % (len(a), len(b))]
    return list(difflib.unified_diff(al, bl, lname, rname, lineterm=""))[:40]


def normalize_snapshot(snap, ws, ids, el, scenario=None):
    """Normalise every entry; count elisions only for entries that COUNT.

    An entry already named in KNOWN_DIVERGENCES is normalised with a
    throwaway counter. Otherwise the Go banner's own `<WS>` substitution
    would make the two sides' elision totals disagree in every scenario,
    which would either drown the counts-must-agree check in false alarms or
    tempt someone to delete it — and that check is the only thing standing
    between this harness and a normaliser that quietly erases a real
    difference (L51).
    """
    out = {}
    for rel, (kind, mode, blob) in snap.items():
        counter = el
        if scenario and known_reason(scenario, rel):
            counter = Elisions()
        rel_n = normalize(rel, ws, ids, counter)
        try:
            body = normalize(blob.decode("utf-8"), ws, ids, counter)
            body = body.encode("utf-8")
        except UnicodeDecodeError:
            body = blob  # compared raw; a non-UTF-8 write IS the finding
        out[rel_n] = (kind, mode, body)
    return out


BANNER_RE = re.compile(r"^workspace: .*$\n?", re.M)


def transcript_entries(transcript):
    """Synthetic snapshot entries for each command's stdout/stderr.

    Named `<stdout>/NN-verb` so they sort with, and diff like, real files.
    Exit codes are not included here — a non-zero one already aborts the
    scenario as REFUSED before any comparison happens.

    The Go CLIs print a `workspace: <path>` banner the Python ones do not
    (task on stderr, pack on stdout). It is LIFTED OUT into its own
    `<banner>/NN-verb` entry rather than tolerated inside the stream,
    because the stream also carries the report each CLI prints about what
    it just did — and that report is exactly what a write comparison wants
    to read. Allowlisting the whole stream to excuse one banner line would
    have thrown that away.
    """
    out = {}
    for i, (verb, code, stdout, stderr) in enumerate(transcript):
        banners = []
        rest = {}
        for stream, text in (("stdout", stdout), ("stderr", stderr)):
            banners.extend(m.group(0).rstrip("\n")
                           for m in BANNER_RE.finditer(text))
            rest[stream] = BANNER_RE.sub("", text)
        out["<stdout>/%02d-%s" % (i, verb)] = ("stdout", 0,
                                               rest["stdout"].encode("utf-8"))
        out["<stderr>/%02d-%s" % (i, verb)] = ("stderr", 0,
                                               rest["stderr"].encode("utf-8"))
        out["<banner>/%02d-%s" % (i, verb)] = (
            "banner", 0, "\n".join(banners).encode("utf-8"))
    return out


def run_scenario(name, module, steps):
    trees, ids_by, els, transcripts = {}, {}, {}, {}
    for engine, cwd in ENGINES:
        ws = os.path.join(SCRATCH, "%s_%s" % (name, engine))
        if os.path.exists(ws):
            shutil.rmtree(ws)
        ids, transcript = run_sequence(engine, cwd, ws, module, steps, name)
        transcripts[engine] = transcript
        codes = [c for _, c, _, _ in transcript]
        if any(c != 0 for c in codes):
            bad = next(t for t in transcript if t[1] != 0)
            print("%-32s %s REFUSED at `%s` (exit %d): %s"
                  % (name, engine, bad[0], bad[1], bad[3].strip()[-300:]))
            return "refused"
        # A job id is required wherever the scenario spends one. Missing
        # ids would make the elision vacuous and the comparison hollow.
        spent = "".join("".join(st.py) + "".join(st.go) for st in steps)
        if "{job}" in spent and not ids:
            print("%-32s %s emitted NO job id matching %s"
                  % (name, engine, JOB_ID_RE.pattern))
            return "refused"
        el = Elisions()
        ids_by[engine] = ids
        snap = snapshot(ws)
        # Each engine's own ACCOUNT of what it did is part of the answer, not
        # scaffolding: `pack import` prints a report naming every row's
        # outcome, and two engines can leave identical trees while disagreeing
        # about what they just did (a row reported "imported" by one and
        # "malformed_skipped" by the other, with neither writing anything in
        # --dry-run). Folded into the same comparison, under the same
        # elisions, so it cannot be forgotten.
        snap.update(transcript_entries(transcript))
        trees[engine] = normalize_snapshot(snap, ws, ids, el, scenario=name)
        els[engine] = el

    # Elision counts must agree. A side that emitted five timestamps where
    # the other emitted four has a real difference that the normaliser
    # would otherwise have erased.
    mismatched = els["py"].counts != els["go"].counts
    if mismatched:
        print("%-32s ELISION COUNTS DISAGREE: py=%s go=%s"
              % (name, els["py"], els["go"]))
        print("   ^ one side emitted more of a volatile shape than the "
              "other. That is itself a difference. The entry diff below is "
              "printed anyway, but read it knowing the normaliser has "
              "already replaced unequal numbers of things on the two sides.")
    if len(ids_by["py"]) != len(ids_by["go"]):
        print("%-32s DIFFERS (job-id counts: py=%d go=%d)"
              % (name, len(ids_by["py"]), len(ids_by["go"])))
        return "differ"

    n = len(trees["py"])
    if n == 0:
        print("%-32s COMPARED NOTHING — zero entries is an error, not a pass"
              % name)
        return "refused"
    diffs, known_hits = compare(name, trees["py"], trees["go"],
                                "python", "go", scenario=name)
    # Must-still-be-observed: a row that stopped matching is either a fixed
    # divergence (delete the row) or a fixture that stopped reaching it
    # (fix the fixture). Either way the allowlist is now certifying
    # something nobody measured, so it FAILS rather than passes.
    expected = set((sp, ep) for sp, ep, _ in KNOWN_DIVERGENCES
                   if fnmatch.fnmatch(name, sp))
    stale = expected - known_hits
    if stale:
        for sp, ep in sorted(stale):
            print("   ALLOWLIST ROW NO LONGER OBSERVED: %s / %s" % (sp, ep))
        print("%-32s DIFFERS (a known-divergence row matched nothing — it "
              "is certifying a difference this run did not produce)" % name)
        return "differ"
    detail = "  [%d entries; %d known; elided %s]" % (
        n, len(known_hits), els["py"])
    if diffs == 0 and not mismatched:
        print("%-32s identical%s" % (name, detail))
        return "same"
    if diffs == 0:
        print("%-32s DIFFERS (elision counts only)%s" % (name, detail))
        return "differ"
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

    Two symlink shapes were added after r10 found the walk blind to them.
    They are here rather than in a separate test because THIS is the count
    that is asserted: a fix without a fixture is how a wrong fix survives a
    round, and a fixture the assertion does not count is not a fixture.

      dlink   a symlink to a directory, pointed at a DIFFERENT target on
              each side. Both sides' lstat says lrwxrwxrwx, so recording
              it as a directory made the two identical — the miss itself.
      shape   a real directory on one side, a symlink on the other. The
              old code caught this ACCIDENTALLY (0o755 vs the link's
              0o777) and reported it as a mode difference, which is the
              wrong sentence about the right tree.
    """
    base = os.path.join(SCRATCH, "_selftest")
    if os.path.exists(base):
        shutil.rmtree(base)
    a, b = os.path.join(base, "a"), os.path.join(base, "b")
    for root in (a, b):
        os.makedirs(os.path.join(root, "sub"))
        os.makedirs(os.path.join(root, "other"))
        with open(os.path.join(root, "same.txt"), "w") as f:
            f.write("identical\n")
    # Same NAME, same kind, different target — the shape the walk was blind
    # to. `other` is identical on both sides so it contributes nothing of
    # its own to the count.
    os.symlink("sub", os.path.join(a, "dlink"))
    os.symlink("other", os.path.join(b, "dlink"))
    # Same name, DIFFERENT kind.
    os.makedirs(os.path.join(a, "shape"))
    os.symlink("other", os.path.join(b, "shape"))
    with open(os.path.join(a, "sub", "x.json"), "w") as f:
        f.write('{"k": 1}\n')
    with open(os.path.join(b, "sub", "x.json"), "w") as f:
        f.write('{"k": 2}\n')
    with open(os.path.join(a, "only-in-a.txt"), "w") as f:
        f.write("\n")
    os.chmod(os.path.join(b, "sub"), 0o700)
    os.chmod(os.path.join(a, "sub"), 0o755)

    # content byte, directory mode, missing file, retargeted symlink,
    # directory-became-symlink.
    want = 5
    print("self-test: the differ must report %d differences" % want)
    diffs, _ = compare("_selftest", snapshot(a), snapshot(b), "a", "b")
    if diffs != want:
        sys.exit("SELF-TEST FAILED: the differ reported %d differences, not "
                 "%d. Nothing this harness says can be trusted."
                 % (diffs, want))
    same, _ = compare("_selftest", snapshot(a), snapshot(a), "a", "a")
    if same != 0:
        sys.exit("SELF-TEST FAILED: a tree differs from itself (%d)" % same)
    print("self-test: ok (%d found, 0 false positives)\n" % want)


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
        ('{"pack": "cmp-pack@3a04958b"}', {"pack-tag": 1}),
        ('{"pack_tag": "cmp-pack@3a04958b"}', {"pack-tag": 1}),
        # The NAME half must survive: a pack that came out named differently
        # is a finding, not volatility.
        ('{"pack": "other-pack@3a04958b"}', {"pack-tag": 1}),
        # Not under one of those two keys, and not an 8-hex tail: untouched.
        ('{"note": "cmp-pack@3a04958b"}', {}),
        ('{"pack": "cmp-pack@3a04958"}', {}),
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
    unknown = wanted - set(n for n, _, _ in SCENARIOS)
    if unknown:
        sys.exit("no such scenario: %s\nknown: %s"
                 % (", ".join(sorted(unknown)),
                    ", ".join(n for n, _, _ in SCENARIOS)))
    tally = {"same": [], "differ": [], "refused": []}
    for name, module, steps in SCENARIOS:
        if wanted and name not in wanted:
            continue
        tally[run_scenario(name, module, steps)].append(name)
    print("\n%d identical, %d differ, %d refused"
          % (len(tally["same"]), len(tally["differ"]), len(tally["refused"])))
    if tally["differ"]:
        print("differ:  " + ", ".join(tally["differ"]))
    if tally["refused"]:
        print("refused: " + ", ".join(tally["refused"]))
    return 1 if tally["differ"] or tally["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
