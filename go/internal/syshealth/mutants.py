#!/usr/bin/env python3
"""Mutation battery for internal/syshealth, derived from the FILE.

Every mutant below is a decision syshealth.go makes, read off the source
rather than off the diff: if a test cannot tell the mutant from the
original, the guard for that decision does not exist (feedback:
mutation-from-file, "a guard that cannot fail is worse than no guard").

P4: the run owns the ENTIRE working tree — a Go module builds through its
import graph, so "a different package" is not an exemption. Everything
happens in a COPY under /tmp; the real worktree is never touched.

Each mutant's `site` must appear EXACTLY ONCE in the target file. A site
that matches zero times is a battery bug (the mutant never applied and gets
reported as a survivor that was never tried); a site that matches twice
mutates two places at once and the result means nothing.

Usage:  python3 go/internal/syshealth/mutants.py            # all mutants
        python3 go/internal/syshealth/mutants.py M12 M13    # named subset

Survivors that are labelled EQUIVALENT / EQUIVALENT-BY-CONTRACT below are
KEPT rather than deleted, so a later round does not re-derive them and
spend an afternoon writing a fixture that cannot exist (L8).
"""
import os
import shutil
import subprocess
import sys
import tempfile

# The module root, resolved from THIS file rather than hard-coded: the
# battery lives beside the package it mutates so the claim in PORT.md is
# re-checkable by anyone with the repo.
SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = "internal/syshealth/syshealth.go"
PKG = "./internal/syshealth/"

# (id, note, site, replacement)
MUTANTS = [
    # --- Summary.ToDict ----------------------------------------------------
    ("M1", "an empty silent list marshals as null, not []",
     "\tsil := pyval.List{}", "\tvar sil pyval.List"),
    ("M2", "silent is always empty", 'sil = append(sil, n)', "_ = n"),
    ("M3", "skipped is emitted even when empty",
     'if s.Skipped != "" {', "if true {"),
    ("M4", "error is emitted even when empty",
     'if s.Error != "" {', "if true {"),
    ("M5", "ran and transitions swap", 'o.Set("ran", s.Ran)',
     "o.Set(\"ran\", s.Transitions)"),
    # EQUIVALENT-BY-CONTRACT: the differential compares the summary
    # SEMANTICALLY (sameJSON), because CPython hands it back as a dict and
    # the two runtimes' key order is not something either one promises. The
    # snapshot FILE is the surface where order is pinned, and sort_keys
    # decides it there. Kept so this is not re-derived.
    ("M6", "the summary key order changes",
     'o.Set("ran", s.Ran)\n\tsil := pyval.List{}',
     'o.Set("transitions", s.Transitions)\n\to.Set("ran", s.Ran)\n\tsil := pyval.List{}'),

    # --- SnapshotPath / LoadSnapshot ---------------------------------------
    ("M7", "the snapshot file is named differently",
     'orch.MemoryDir(ws), "system_health.json")',
     'orch.MemoryDir(ws), "health.json")'),
    # `ws` alone would leave the orch import unused, and a mutant that fails
    # to COMPILE is reported as caught while proving nothing — the same
    # false positive class as a site that matches zero times. Keep orch in
    # the expression.
    ("M8", "the snapshot lives beside the workspace, not in memory/",
     "orch.MemoryDir(ws), \"system_health.json\")",
     "filepath.Dir(orch.MemoryDir(ws)), \"system_health.json\")"),
    # EQUIVALENT-BY-CONSTRUCTION: os.ReadFile on a path that does not exist
    # fails, and that lane returns the same empty Obj. The stat stays because
    # the Python spells `path.exists()` explicitly and this port states
    # requirements rather than inheriting them (L48).
    ("M9", "a missing file is not special-cased",
     "if _, err := os.Stat(path); err != nil {", "if false {"),
    ("M10", "unparseable json returns the raw text's type instead of {}",
     "\tv, jerr := pyval.LoadsOrdered(text)\n\tif jerr != nil {\n\t\treturn pyval.Obj{}\n\t}",
     "\tv, jerr := pyval.LoadsOrdered(text)\n\tif jerr != nil {\n\t\treturn pyval.Obj{Field{Key: \"raw\", Val: text}}\n\t}"),
    ("M11", "a non-object json value is returned as an empty-but-present obj",
     "\tif o, ok := v.(pyval.Obj); ok {\n\t\treturn o\n\t}\n\treturn pyval.Obj{}",
     "\tif o, ok := v.(pyval.Obj); ok {\n\t\treturn o\n\t}\n\treturn pyval.Obj{Field{Key: \"v\", Val: v}}"),
    # r1 labelled this EQUIVALENT on the grounds that no FIXTURE can hold
    # invalid UTF-8 (the probe's cases travel as JSON). True, and beside the
    # point: encoding/json silently replaces bad bytes with U+FFFD where
    # CPython's read_text raises, so the two runtimes really do differ. The
    # guard is a unit test, not a differential case.
    ("M12", "the strict utf-8 decode is skipped",
     "\ttext, derr := pyval.DecodeUTF8Strict(raw)\n\tif derr != nil {\n\t\treturn pyval.Obj{}\n\t}",
     "\ttext := string(raw)"),

    # --- WriteSnapshot -----------------------------------------------------
    ("M13", "the snapshot is written at indent=2",
     "pyval.DumpsIndentNSorted(snap, 1)", "pyval.DumpsIndentNSorted(snap, 2)"),
    ("M14", "the snapshot keys are not sorted",
     "pyval.DumpsIndentNSorted(snap, 1)", "pyval.DumpsIndentN(snap, 1)"),
    ("M15", "the trailing newline is dropped",
     'record.AtomicWrite(path, []byte(body+"\\n"))',
     "record.AtomicWrite(path, []byte(body))"),
    ("M16", "the snapshot ends with two newlines",
     'record.AtomicWrite(path, []byte(body+"\\n"))',
     'record.AtomicWrite(path, []byte(body+"\\n\\n"))'),

    # --- HistoryOf ---------------------------------------------------------
    ("M17", "an empty history marshals as null, not []",
     "\tout := pyval.List{}\n\tv, _ := prior.Get(\"history\")",
     "\tvar out pyval.List\n\tv, _ := prior.Get(\"history\")"),
    ("M18", "non-dict history entries are kept",
     "if _, isObj := h.(pyval.Obj); isObj {", "if true {"),
    ("M19", "the history key is misspelled", 'prior.Get("history")',
     'prior.Get("histories")'),
    # EQUIVALENT-BY-CONSTRUCTION: a failed assertion leaves `lst` nil, and
    # ranging over a nil slice appends nothing, so `out` is the same empty
    # List either way.
    ("M20", "a non-list history is not rejected",
     "\tlst, ok := v.(pyval.List)\n\tif !ok {\n\t\treturn out\n\t}",
     "\tlst, _ := v.(pyval.List)"),

    # --- RunCycle: the config gate -----------------------------------------
    ("M21", "only an explicit false disables the probes",
     "if !pyval.Truthy(enabled) {", "if enabled == false {"),
    ("M22", "the skip message changes",
     '"health.probes_enabled is off"', '"probes disabled"'),
    ("M23", "the skip still hands back a snapshot to write",
     "\t\tsummary.Skipped = \"health.probes_enabled is off\"\n\t\treturn nil, nil, summary",
     "\t\tsummary.Skipped = \"health.probes_enabled is off\"\n\t\treturn snapshot, nil, summary"),

    # --- RunCycle: the processes map ---------------------------------------
    # EQUIVALENT-BY-CONSTRUCTION (M24, M27): a failed type assertion yields
    # nil, and the `if processes == nil` / `if prior == nil` fallback two
    # lines down already turns that into an empty Obj. The explicit form
    # stays because it is the shape Python has.
    ("M24", "a non-dict processes value is kept rather than replaced",
     "\t\tif o, isObj := v.(pyval.Obj); isObj {\n\t\t\tprocesses = o\n\t\t}",
     "\t\tprocesses, _ = v.(pyval.Obj)"),
    ("M25", "the processes key is misspelled on read",
     'if v, ok := snapshot.Get("processes"); ok {',
     'if v, ok := snapshot.Get("process"); ok {'),
    # r1 spelled this as "insert an early Set", which left the real one in
    # place — the snapshot came out correct and the mutant survived as a
    # false negative. A mutant that leaves the original behaviour reachable
    # is not a mutant (L8). A single search-and-replace cannot both insert
    # above the loop and delete below it, so the decision is split:
    #
    #   M26  the store is REMOVED       -> the snapshot loses `processes`
    #   M26b the store is DUPLICATED    -> equivalent, and labelled as such
    #
    # The ordering bug itself — store above the loop, delete below — is what
    # TestRunCycleKeepsEveryProcessItAppended exists for, and that test was
    # verified against a hand-edited copy carrying exactly that shape.
    ("M26", "the processes map is never stored back into the snapshot",
     "\tsnapshot.Set(\"processes\", processes)\n\tsnapshot.Set(\"updated_at\", now())",
     "\tsnapshot.Set(\"updated_at\", now())"),
    # EQUIVALENT-BY-CONSTRUCTION: the store below the loop runs regardless
    # and overwrites this one with the grown slice.
    ("M26b", "processes is ALSO stored before the loop",
     "\tvar pending []Narration\n\tfor _, decl := range decls {",
     "\tsnapshot.Set(\"processes\", processes)\n\tvar pending []Narration\n\tfor _, decl := range decls {"),
    ("M27", "a non-dict prior entry is not treated as empty",
     "\t\t\tif o, isObj := v.(pyval.Obj); isObj {\n\t\t\t\tprior = o\n\t\t\t}",
     "\t\t\tprior, _ = v.(pyval.Obj)"),

    # --- RunCycle: the probe shield ----------------------------------------
    ("M28", "a raising probe reports OK",
     "\t\t\tstatus, evidence, obs = Unknown,", "\t\t\tstatus, evidence, obs = OK,"),
    ("M29", "the probe-failure prefix changes",
     '"probe failed: "+pyval.Clip(perr.Error(), 120)',
     '"probe error: "+pyval.Clip(perr.Error(), 120)'),
    ("M30", "the probe-failure message is not clipped",
     "pyval.Clip(perr.Error(), 120)", "perr.Error()"),
    ("M31", "the probe-failure clip is off by one",
     "pyval.Clip(perr.Error(), 120)", "pyval.Clip(perr.Error(), 119)"),
    ("M32", "a raising probe keeps whatever observation it half-returned",
     "\t\t\t\t\"probe failed: \"+pyval.Clip(perr.Error(), 120), pyval.Obj{}",
     "\t\t\t\t\"probe failed: \"+pyval.Clip(perr.Error(), 120), obs"),
    ("M33", "the run counter never advances", "\t\tsummary.Ran++\n", "\n"),
    ("M34", "the silent list collects everything BUT silent",
     "if status == Silent {\n\t\t\tsummary.Silent = append",
     "if status != Silent {\n\t\t\tsummary.Silent = append"),

    # --- RunCycle: the entry -----------------------------------------------
    ("M35", "the prior entry is not copied, so unknown keys are lost",
     "\t\tentry := make(pyval.Obj, len(prior))\n\t\tcopy(entry, prior)",
     "\t\tentry := pyval.Obj{}"),
    ("M36", "an empty observation is appended anyway",
     "\t\tif len(obs) > 0 {", "\t\tif true {"),
    ("M37", "a probe-supplied 'at' is left alone",
     "\t\t\tstamped.Set(\"at\", stamp)",
     "\t\t\tif _, seen := stamped.Get(\"at\"); !seen {\n\t\t\t\tstamped.Set(\"at\", stamp)\n\t\t\t}"),
    # r2 survivor for a subtle reason worth keeping: Obj.Set APPENDS a new
    # key, and append on a full slice reallocates, so an aliased observation
    # without an existing "at" is corrupted only by luck. The pin
    # (TestRunCycleDoesNotMutateTheProbesObservation) hands it an
    # observation that already carries "at", which takes Set's in-place lane.
    ("M38", "the observation is not copied before stamping",
     "\t\t\tstamped := make(pyval.Obj, len(obs))\n\t\t\tcopy(stamped, obs)",
     "\t\t\tstamped := obs"),
    ("M39", "the ring buffer keeps the OLDEST eight",
     "history = history[len(history)-HistoryKeep:]", "history = history[:HistoryKeep]"),
    ("M40", "the ring buffer keeps nine", "if len(history) > HistoryKeep {",
     "if len(history) > HistoryKeep+1 {"),
    # EQUIVALENT-BY-CONSTRUCTION: the trim takes the LAST HistoryKeep, and
    # slicing the last 8 of an 8-element list is the identity. `>` and `>=`
    # differ only on the length where the operation does nothing.
    ("M41", "the ring-buffer guard is inclusive", "if len(history) > HistoryKeep {",
     "if len(history) >= HistoryKeep {"),
    ("M42", "checked_at is never written", '\t\tentry.Set("checked_at", stamp)\n', "\n"),
    ("M43", "description and expectation are swapped",
     'entry.Set("description", decl.Description)\n\t\tentry.Set("expectation", decl.Expectation)',
     'entry.Set("description", decl.Expectation)\n\t\tentry.Set("expectation", decl.Description)'),
    ("M44", "the history is not written back",
     '\t\tentry.Set("history", history)\n', "\n"),
    ("M45", "the entry is never stored",
     "\t\tprocesses.Set(decl.Name, entry)\n", "\n"),
    ("M46", "every entry is stored under the same key",
     "processes.Set(decl.Name, entry)", 'processes.Set("p", entry)'),

    # --- RunCycle: the narration edge --------------------------------------
    ("M47", "the silence edge reads the previous STATUS, not what was told",
     'wentSilent := status == Silent && narrated != "silent"',
     "wentSilent := status == Silent && prevStatus != Silent"),
    ("M48", "the recovery edge fires for anything not told ok",
     'recovered := status == OK && narrated == "silent"',
     'recovered := status == OK && narrated != "ok"'),
    ("M49", "recoveries are never narrated", "if wentSilent || recovered {",
     "if wentSilent {"),
    ("M50", "silences are never narrated", "if wentSilent || recovered {",
     "if recovered {"),
    ("M51", "the narrated flag is inverted",
     '\t\t\t\tentry.Set("narrated", "silent")\n\t\t\t} else {\n\t\t\t\tentry.Set("narrated", "ok")',
     '\t\t\t\tentry.Set("narrated", "ok")\n\t\t\t} else {\n\t\t\t\tentry.Set("narrated", "silent")'),
    ("M52", "last_transition records the new status as its 'from'",
     'lt.Set("from", prevStatus)', 'lt.Set("from", status)'),
    ("M53", "last_transition's 'to' is the old status",
     'lt.Set("to", status)', 'lt.Set("to", prevStatus)'),
    ("M54", "last_transition is stamped with nothing",
     'lt.Set("at", stamp)', 'lt.Set("at", nil)'),
    ("M55", "last_transition is never recorded",
     '\t\t\tentry.Set("last_transition", lt)\n', "\n"),
    ("M56", "the pending narrations come out reversed",
     "pending = append(pending, Narration{decl, status, evidence})",
     "pending = append([]Narration{{decl, status, evidence}}, pending...)"),

    # --- RunCycle: the tail ------------------------------------------------
    ("M57", "updated_at is never written",
     '\tsnapshot.Set("updated_at", now())\n', "\n"),
    ("M58", "updated_at reuses the last declaration's stamp instead of "
            "reading the clock again",
     '\tsnapshot.Set("updated_at", now())',
     '\tif len(decls) > 0 {\n\t\tsnapshot.Set("updated_at", syLastStamp)\n\t} else {\n\t\tsnapshot.Set("updated_at", now())\n\t}'),
    ("M59", "a dead cycle counter still writes the snapshot",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn nil, nil, summary",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn snapshot, nil, summary"),
    ("M60", "the cycle error is not clipped",
     "pyval.Clip(err.Error(), 200)", "err.Error()"),
    ("M61", "the cycle error clip is off by one",
     "pyval.Clip(err.Error(), 200)", "pyval.Clip(err.Error(), 199)"),
    # r1 moved this ONE line up, from below the cycle-counter write to above
    # it — both of which are already past the error return, so the mutant
    # could not change an answer (L8). The decision it is meant to probe is
    # whether the count is taken before the counter can RAISE.
    ("M62", "transitions are counted before the counter can raise",
     "\tnext, err := nextCycle(snapshot)",
     "\tsummary.Transitions = len(pending)\n\tnext, err := nextCycle(snapshot)"),
    ("M63", "transitions always reports zero",
     "summary.Transitions = len(pending)", "summary.Transitions = 0"),

    # --- nextCycle ---------------------------------------------------------
    ("M64", "a falsy counter is parsed instead of restarting",
     "if !ok || !pyval.Truthy(v) {", "if !ok {"),
    ("M65", "an absent counter raises instead of restarting",
     "if !ok || !pyval.Truthy(v) {", "if !pyval.Truthy(v) {"),
    ("M66", "the counter does not advance", "return n + 1, nil", "return n, nil"),
    ("M67", "the counter advances by two", "return n + 1, nil", "return n + 2, nil"),
    ("M68", "a restart begins at zero",
     "\t\treturn 1, nil\n\t}\n\tn, err := pyval.Int(v)", "\t\treturn 0, nil\n\t}\n\tn, err := pyval.Int(v)"),
    ("M69", "an unparseable counter silently restarts",
     "\t\treturn 0, err\n\t}", "\t\treturn 1, nil\n\t}"),
    ("M70", "the counter key is misspelled", 'snapshot.Get("cycle")',
     'snapshot.Get("cycles")'),

    # --- RenderSnapshot ----------------------------------------------------
    ("M71", "the report heading changes",
     '"# System health — dynamic-process liveness"',
     '"# System health - dynamic process liveness"'),
    ("M72", "an EMPTY processes dict renders a table instead of 'no snapshot'",
     "if !pyval.Truthy(procsVal) {", "if procsVal == nil {"),
    ("M73", "the no-snapshot line changes",
     '"No snapshot yet — probes run at goal-run finalization "',
     '"No snapshot yet - probes run at goal run finalization "'),
    ("M74", "a list processes claims the wrong missing attribute",
     "\"'%s' object has no attribute 'items'\"", "\"'%s' object has no attribute 'get'\""),
    ("M75", "a non-dict entry claims the wrong missing attribute",
     "\"'%s' object has no attribute 'get'\"", "\"'%s' object has no attribute 'items'\""),
    ("M76", "the offending type is hard-coded as str",
     "\"'%s' object has no attribute 'get'\", pyval.TypeName(f.Val))",
     "\"'%s' object has no attribute 'get'\", \"str\")"),
    ("M77", "an unhashable status falls to rank 3 instead of raising",
     "\t\tcase pyval.List, pyval.Obj:", "\t\tcase pyval.List:"),
    ("M78", "SILENT and OK swap places in the ordering",
     "order := map[string]int{Silent: 0, Unknown: 1, OK: 2}",
     "order := map[string]int{Silent: 2, Unknown: 1, OK: 0}"),
    ("M79", "an unrecognised status sorts FIRST",
     "\t\t\t} else {\n\t\t\t\trank[i] = 3\n\t\t\t}", "\t\t\t} else {\n\t\t\t\trank[i] = -1\n\t\t\t}"),
    ("M80", "a missing status sorts with the OK group",
     "\t\tdefault:\n\t\t\trank[i] = 3", "\t\tdefault:\n\t\t\trank[i] = 2"),
    ("M81", "ties break by name descending",
     "return procs[idx[a]].Key < procs[idx[b]].Key",
     "return procs[idx[a]].Key > procs[idx[b]].Key"),
    ("M82", "the name half of the sort key is dropped",
     "\t\tif rank[idx[a]] != rank[idx[b]] {\n\t\t\treturn rank[idx[a]] < rank[idx[b]]\n\t\t}\n\t\treturn procs[idx[a]].Key < procs[idx[b]].Key",
     "\t\treturn rank[idx[a]] < rank[idx[b]]"),
    ("M83", "a present-but-null updated_at renders '?' like an absent one",
     '\t\tgetOr(snap, "updated_at", "?"), getOr(snap, "cycle", "?")), "")',
     '\t\tgetOrQ(snap, "updated_at"), getOrQ(snap, "cycle")), "")'),
    ("M84", "the header's two spaces become one",
     '"Updated: %s  (cycle %s)"', '"Updated: %s (cycle %s)"'),
    ("M85", "a missing status renders empty instead of '?'",
     'getOr(p, "status", "?")', 'getOr(p, "status", "")'),
    ("M86", "the expectation indent is three spaces",
     '"    expectation: "', '"   expectation: "'),
    ("M87", "the evidence label loses its column alignment",
     '"    evidence:    "', '"    evidence: "'),
    ("M88", "the process line uses a hyphen, not an em dash",
     '"[%s] %s — %s"', '"[%s] %s - %s"'),
    ("M89", "the transition arrow is an ascii one",
     '"    last transition: %s → %s at %s"', '"    last transition: %s -> %s at %s"'),
    ("M90", "a non-dict last_transition renders instead of being skipped",
     "\t\t\tif o, isObj := lt.(pyval.Obj); isObj {", "\t\t\tif o, isObj := lt.(pyval.Obj); !isObj {"),
    ("M91", "the blank line between processes is dropped",
     '\t\tlines = append(lines, "")\n\t}\n\treturn strings.Join(lines, "\\n"), nil',
     "\t}\n\treturn strings.Join(lines, \"\\n\"), nil"),
    ("M92", "the report joins with CRLF",
     '\t}\n\treturn strings.Join(lines, "\\n"), nil\n}\n\n// getOr is',
     '\t}\n\treturn strings.Join(lines, "\\r\\n"), nil\n}\n\n// getOr is'),
    ("M93", "getOr returns its default for a present-but-null value",
     "\tv, ok := o.Get(key)\n\tif !ok {\n\t\treturn def\n\t}\n\treturn pyval.Str(v)",
     "\tv, ok := o.Get(key)\n\tif !ok || v == nil {\n\t\treturn def\n\t}\n\treturn pyval.Str(v)"),
]

# M58 and M83 reference helpers that do not exist in the original — a mutant
# is allowed to need scaffolding, but the scaffolding must not change the
# unmutated file. These are appended only when their mutant is applied.
SCAFFOLD = {
    "M58": '\n\nvar syLastStamp = "1999-01-01T00:00:00+00:00"\n',
    "M83": '\n\nfunc getOrQ(o pyval.Obj, key string) string {\n'
           '\tv, ok := o.Get(key)\n\tif !ok || v == nil {\n\t\treturn "?"\n\t}\n'
           '\treturn pyval.Str(v)\n}\n',
}

ENV = dict(os.environ, MARO_PYPROBE_REQUIRED="1")


def run(cmd, cwd, timeout=900):
    # MARO_PYPROBE_REQUIRED is not optional here. Without it a copy that
    # cannot see ../../../src skips every differential and `go test` prints
    # ok, so EVERY mutant survives and the battery reads as a total test
    # gap — the failure pyprobe.SrcDir's own comment was written about.
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=ENV)


def main():
    wanted = set(sys.argv[1:])
    mutants = [m for m in MUTANTS if not wanted or m[0] in wanted]

    tree = tempfile.mkdtemp(prefix="symut-")
    work = os.path.join(tree, "go")
    print("copying the module to %s" % work, flush=True)
    shutil.copytree(SRC, work, symlinks=True)
    # pyprobe.SrcDir resolves ../../../src from the test's directory, so the
    # copy needs a src/ beside its go/. A symlink to the real tree is right:
    # the battery mutates GO, never Python, and the CPython half must be the
    # same interpreter and the same sources the live differential used.
    os.symlink(os.path.join(os.path.dirname(SRC), "src"),
               os.path.join(tree, "src"))
    target = os.path.join(work, TARGET)
    original = open(target).read()

    base = run(["go", "test", "-count=1", PKG], work)
    if base.returncode != 0:
        print("BASELINE IS RED — nothing this battery reports would mean "
              "anything (P7)\n" + base.stdout[-4000:] + base.stderr[-2000:])
        sys.exit(2)
    print("baseline green\n", flush=True)

    caught, survived, broken = [], [], []
    for mid, note, site, repl in mutants:
        n = original.count(site)
        if n != 1:
            broken.append((mid, note, "site matches %d times" % n))
            print("%-5s BATTERY-BUG  %s (site matches %d times)"
                  % (mid, note, n), flush=True)
            continue
        body = original.replace(site, repl, 1) + SCAFFOLD.get(mid, "")
        open(target, "w").write(body)
        r = run(["go", "test", "-count=1", PKG], work)
        if r.returncode == 0:
            survived.append((mid, note))
            print("%-5s SURVIVED     %s" % (mid, note), flush=True)
        else:
            caught.append((mid, note))
            print("%-5s caught       %s" % (mid, note), flush=True)
    open(target, "w").write(original)

    after = run(["go", "test", "-count=1", PKG], work)
    print("\nrestored clean: %s" % (after.returncode == 0))
    print("caught %d / %d   survived %d   battery-bugs %d"
          % (len(caught), len(mutants), len(survived), len(broken)))
    if survived:
        print("\nSURVIVORS:")
        for mid, note in survived:
            print("  %-5s %s" % (mid, note))
    if broken:
        print("\nBATTERY BUGS:")
        for mid, note, why in broken:
            print("  %-5s %s — %s" % (mid, note, why))
    shutil.rmtree(tree, ignore_errors=True)


if __name__ == "__main__":
    main()
