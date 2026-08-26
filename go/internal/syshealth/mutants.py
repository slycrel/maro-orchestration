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
    # NOT equivalent — this label was WRONG for a whole round, and r1 caught
    # it: it asserted "nothing promises the summary's key order" while
    # ToDict's own doc asserted the opposite, with nothing adjudicating
    # because syGo routes the summary through a Go map. Python's summary IS
    # insertion-ordered and its consumer json.dumps it. The adjudicator is
    # TestSummaryToDictKeepsCPythonsInsertionOrder, which compares the
    # rendered string; this mutant is now expected to be CAUGHT.
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
     "\tv, jerr := pyval.LoadsOrdered(text)\n\tif jerr != nil {\n\t\treturn pyval.Obj{pyval.Field{Key: \"raw\", Val: text}}\n\t}"),
    ("M11", "a non-object json value is returned as an empty-but-present obj",
     "\tif o, ok := v.(pyval.Obj); ok {\n\t\treturn o\n\t}\n\treturn pyval.Obj{}",
     "\tif o, ok := v.(pyval.Obj); ok {\n\t\treturn o\n\t}\n\treturn pyval.Obj{pyval.Field{Key: \"v\", Val: v}}"),
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
     "\t\tif o, isObj := asDict(h); isObj {\n\t\t\tout = append(out, o)\n\t\t}",
     "\t\tif o, isObj := asDict(h); isObj {\n\t\t\tout = append(out, o)\n\t\t} else {\n\t\t\tout = append(out, h)\n\t\t}"),
    ("M19", "the history key is misspelled", 'prior.Get("history")',
     'prior.Get("histories")'),
    # EQUIVALENT-BY-CONSTRUCTION: a failed assertion leaves `lst` nil, and
    # ranging over a nil slice appends nothing, so `out` is the same empty
    # List either way.
    ("M20", "a non-list history is not rejected",
     "\tlst, ok := asList(v)\n\tif !ok {\n\t\treturn out\n\t}",
     "\tlst, _ := asList(v)"),

    # --- RunCycle: the config gate -----------------------------------------
    ("M21", "only an explicit false disables the probes",
     "if !pyval.Truthy(enabled) {", "if enabled == false {"),
    ("M22", "the skip message changes",
     '"health.probes_enabled is off"', '"probes disabled"'),
    # Moved out of RunCycle by the r1 restructure: the config gate now lives
    # in RunAndPersist, beside the write it shares a try with.
    ("M23", "the skip records itself but does not stop the cycle",
     "\t\tsummary.Skipped = \"health.probes_enabled is off\"\n\t\treturn summary, nil",
     "\t\tsummary.Skipped = \"health.probes_enabled is off\""),

    # --- RunCycle: the processes map ---------------------------------------
    # EQUIVALENT-BY-CONSTRUCTION (M24, M27): a failed type assertion yields
    # nil, and the `if processes == nil` / `if prior == nil` fallback two
    # lines down already turns that into an empty Obj. The explicit form
    # stays because it is the shape Python has.
    ("M24", "a non-dict processes value is kept rather than replaced",
     "\t\tif o, isObj := asDict(v); isObj {\n\t\t\tprocesses = o\n\t\t}",
     "\t\tprocesses, _ = asDict(v)"),
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
     "\t\t\tif o, isObj := asDict(v); isObj {\n\t\t\t\tprior = o\n\t\t\t}",
     "\t\t\tprior, _ = asDict(v)"),

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
    # `if wentSilent {` alone leaves `recovered` declared and unused, which
    # does not compile — and a mutant that does not compile is reported as
    # caught while proving nothing. Consume it instead. (M50 does not need
    # this: `wentSilent` is read again two lines down.)
    ("M49", "recoveries are never narrated", "if wentSilent || recovered {",
     "if wentSilent || (recovered && false) {"),
    ("M50", "silences are never narrated", "if wentSilent || recovered {",
     "if recovered {"),
    ("M51", "the narrated flag is inverted",
     '\t\t\t\tentry.Set("narrated", "silent")\n\t\t\t} else {\n\t\t\t\tentry.Set("narrated", "ok")',
     '\t\t\t\tentry.Set("narrated", "ok")\n\t\t\t} else {\n\t\t\t\tentry.Set("narrated", "silent")'),
    ("M52", "last_transition records the new status as its 'from'",
     'lt.Set("from", prevStatus)', 'lt.Set("from", status)\n\t\t\t_ = prevStatus'),
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
    # Three Clip(..., 200) sites exist since the restructure (the cycle
    # abort, the config raise, the failed write), so each mutant carries
    # enough context to match exactly one. A bare "pyval.Clip(err.Error(),
    # 200)" matched all three and mutated the file in three places at once,
    # which is a battery bug however green the result looks.
    ("M60", "the cycle error is not clipped",
     "summary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn nil, nil, summary",
     "summary.Error = err.Error()\n\t\treturn nil, nil, summary"),
    ("M61", "the cycle error clip is off by one",
     "summary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn nil, nil, summary",
     "summary.Error = pyval.Clip(err.Error(), 199)\n\t\treturn nil, nil, summary"),
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
    # EQUIVALENT-BY-CONSTRUCTION, and it took the compile-failure check to
    # find that out: this mutant was reported as CAUGHT for three rounds
    # while proving nothing, because dropping `!ok` left `ok` unused and the
    # package would not build. It compiles now, and it survives — `Get` on an
    # absent key returns a nil value, and `Truthy(nil)` is already false, so
    # `!ok` is a statement of intent rather than a decision. Kept, per L8.
    ("M65", "an absent counter raises instead of restarting",
     "if !ok || !pyval.Truthy(v) {", "if _ = ok; !pyval.Truthy(v) {"),
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
    # r2 moved this site: the arm gained []string. M77 keeps its own job —
    # dropping the DICT spellings — and M108 covers the []string one, so the
    # two do not overlap. The stale site cost a battery-bug row, which is the
    # detector working: a mutant whose site has moved matches zero times and
    # is reported as a bug rather than silently counted as caught.
    ("M77", "an unhashable DICT status falls to rank 3 instead of raising",
     "\t\tcase pyval.List, pyval.Obj, []any, []string, map[string]any:",
     "\t\tcase pyval.List, []any, []string:"),
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
     "\t\t\tif o, isObj := asDict(lt); isObj {",
     "\t\t\tif o, isObj := asDict(lt); !isObj {"),
    ("M91", "the blank line between processes is dropped",
     '\t\tlines = append(lines, "")\n\t}\n\treturn strings.Join(lines, "\\n"), nil',
     "\t}\n\treturn strings.Join(lines, \"\\n\"), nil"),
    ("M92", "the report joins with CRLF",
     '\t}\n\treturn strings.Join(lines, "\\n"), nil\n}\n\n// getOr is',
     '\t}\n\treturn strings.Join(lines, "\\r\\n"), nil\n}\n\n// getOr is'),
    ("M93", "getOr returns its default for a present-but-null value",
     "\tv, ok := o.Get(key)\n\tif !ok {\n\t\treturn def\n\t}\n\treturn pyval.Str(v)",
     "\tv, ok := o.Get(key)\n\tif !ok || v == nil {\n\t\treturn def\n\t}\n\treturn pyval.Str(v)"),

    # --- RunAndPersist: the three lanes the r1 restructure added ------------
    # Everything below mutates code that did not exist in round 1, because
    # RunCycle owned only the middle of Python's try. These are the mutants
    # that would have caught the missing half.
    ("M95", "the config error is swallowed and reads as 'probes are off'",
     "\tenabled, err := cfg()\n\tif err != nil {\n\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, nil\n\t}",
     "\tenabled, err := cfg()\n\tif err != nil {\n\t\tenabled = false\n\t}"),
    ("M96", "the config error is not clipped at 200",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, nil\n\t}\n\tif !pyval.Truthy(enabled) {",
     "\t\tsummary.Error = err.Error()\n\t\treturn summary, nil\n\t}\n\tif !pyval.Truthy(enabled) {"),
    ("M97", "a config read that raises still counts as a cycle that ran",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, nil\n\t}\n\tif !pyval.Truthy(enabled) {",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\tsummary.Ran = 1\n\t\treturn summary, nil\n\t}\n\tif !pyval.Truthy(enabled) {"),
    ("M98", "a failed write is swallowed: nothing persists but the summary "
     "reports a clean cycle",
     "\tif err := WriteSnapshot(ws, snap); err != nil {",
     "\tif err := WriteSnapshot(ws, snap); false {"),
    # THE r1 FINDING, as a mutant. This is what the port did before the
    # restructure, expressed as one line moved above the write.
    ("M99", "transitions is assigned BEFORE the write, so a failed write "
     "still reports narrations that never happened",
     "\tif err := WriteSnapshot(ws, snap); err != nil {",
     "\tsummary.Transitions = len(pending)\n\tif err := WriteSnapshot(ws, snap); err != nil {"),
    ("M100", "a failed write still hands the narrations back to be logged",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, nil\n\t}\n\tsummary.Transitions = len(pending)",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, pending\n\t}\n\tsummary.Transitions = len(pending)"),
    ("M101", "the write error is not clipped at 200",
     "\t\tsummary.Error = pyval.Clip(err.Error(), 200)\n\t\treturn summary, nil\n\t}\n\tsummary.Transitions = len(pending)",
     "\t\tsummary.Error = err.Error()\n\t\treturn summary, nil\n\t}\n\tsummary.Transitions = len(pending)"),
    ("M102", "an aborted cycle is written to disk anyway",
     "\tif snap == nil {\n\t\t// The cycle aborted", "\tif false {\n\t\t// The cycle aborted"),

    # --- asDict / asList ---------------------------------------------------
    # M103/M104/M105 are EQUIVALENT-BY-CONSTRUCTION and labelled rather than
    # deleted: pyval's decoder never produces a Go map, so no fixture can
    # reach asDict's map arm at all. They exist because every pyval helper
    # this package calls in the same breath accepts both spellings (r1 F9).
    #
    # r2 added a hand-built caller (knowngap_test.go), and these three still
    # survive — which is the point of re-stating the reason rather than
    # leaving the old one standing. That caller hands in a []string and a
    # pyval.Obj; it reaches asList's NEW arm (M107 catches it) and the rank
    # switch (M108), and it does not touch asDict's map arm or asList's
    # []any arm, both of which the type switch above them already matches
    # directly. "There is no hand-built caller yet" would now be false, and
    # a survivor's label is only worth what its reason is worth.
    #
    # r2 falsified the sentence that used to end this comment — "the day a
    # caller appears the arms are already right". They were not: []string was
    # missing, and pyval calls a []string a list in Truthy, TypeName and
    # Repr. M107/M108 are the guards for the arms that closed it, and they
    # are expected CAUGHT, not equivalent, because knowngap_test.go is the
    # hand-built caller. A label is a claim; this one was checkable and
    # wrong.
    ("M103", "asDict rejects a plain map",
     "\tcase map[string]any:\n\t\tkeys := make([]string, 0, len(t))",
     "\tcase map[string]any:\n\t\treturn nil, len(t) < 0\n\tcase map[string]string:\n\t\tkeys := make([]string, 0, len(t))"),
    ("M104", "asList rejects a plain slice",
     "\tcase []any:\n\t\treturn pyval.List(t), true", "\tcase []any:\n\t\treturn nil, len(t) < 0"),
    ("M106", "the memory dir is created 0o755 instead of Path.mkdir's "
     "0o777-narrowed-by-umask",
     "os.MkdirAll(filepath.Dir(path), record.NewDirMode)",
     "os.MkdirAll(filepath.Dir(path), 0o755)"),
    ("M105", "asDict does not sort a plain map's keys, so its order is "
     "whatever the runtime feels like",
     "\t\tsort.Strings(keys)", "\t\t_ = sort.Strings"),

    # --- r2 -----------------------------------------------------------------
    ("M107", "asList drops its []string arm, so pyval calls a []string a "
     "list and this package does not",
     "\tcase []string:\n\t\tout := make(pyval.List, len(t))",
     "\tcase []int:\n\t\tout := make(pyval.List, len(t))"),
    ("M108", "the unhashable-key arm drops []string, so a []string status "
     "falls to default and RANKS 3 where CPython raises",
     "\t\tcase pyval.List, pyval.Obj, []any, []string, map[string]any:",
     "\t\tcase pyval.List, pyval.Obj, []any, map[string]any:"),
    ("M109", "the cycle increment is not range-checked, so a counter at "
     "MaxInt64 WRAPS and a negative cycle is written",
     "\tif n == math.MaxInt {\n\t\treturn 0, pyval.ErrIntTooLarge\n\t}",
     "\tif n == math.MaxInt {\n\t\t_ = pyval.ErrIntTooLarge\n\t}"),
    # EQUIVALENT-BY-CONSTRUCTION: pyval.Int already refuses anything ABOVE
    # MaxInt, so `>=` and `==` select the same set. Kept because the guard
    # reads as if it were a bounds check and the next reader will wonder.
    ("M110", "the range check uses >= instead of ==",
     "\tif n == math.MaxInt {", "\tif n >= math.MaxInt {"),
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
        out = r.stdout + r.stderr
        if r.returncode == 0:
            survived.append((mid, note))
            print("%-5s SURVIVED     %s" % (mid, note), flush=True)
        elif "[build failed]" in out or "[setup failed]" in out:
            # A mutant that does not COMPILE is reported as caught by a
            # returncode check, and it proves nothing: no test observed it.
            # Same false-positive class as a site matching zero times, and
            # it was live for a round (M8 left an import unused). Rewrite
            # the mutant so it compiles, or the row is a lie.
            broken.append((mid, note, "does not compile: %s"
                           % out.strip().splitlines()[-1][:120]))
            print("%-5s BATTERY-BUG  %s (does not compile)"
                  % (mid, note), flush=True)
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
