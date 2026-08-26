#!/usr/bin/env python3
"""Mutation battery for internal/sheriff, derived from the FILE.

Every mutant below is a decision sheriff.go makes, read off the source
rather than off the diff: if a test cannot tell the mutant from the
original, the guard for that decision does not exist (feedback:
mutation-from-file, "a guard that cannot fail is worse than no guard").

P4: the run owns the ENTIRE working tree — a Go module builds through its
import graph, so "a different package" is not an exemption. Everything
happens in a COPY under /tmp; the real worktree is never touched.

Each mutant's `site` must appear EXACTLY ONCE in the target file. A site
that matches zero times is a battery bug (the mutant never applied and is
reported as a false CATCH-less survivor); a site that matches twice mutates
two places at once and the result means nothing.

Usage:  python3 go/internal/sheriff/mutants.py            # all mutants
        python3 go/internal/sheriff/mutants.py M12 M13    # named subset

Converged 2026-08-26 at 80 CAUGHT of 86, with six survivors each labelled
EQUIVALENT or NEAR-EQUIVALENT at its entry below — kept rather than deleted
so the next round does not re-derive them.
"""
import os
import shutil
import subprocess
import sys
import tempfile

# The module root, resolved from THIS file rather than hard-coded: the
# battery lives beside the package it mutates so the claim in PORT.md is
# re-checkable by anyone with the repo. A path pointing at one machine's
# worktree is the same defect as a differential whose Python half was
# deleted (adversarial tasks-r1 LOW).
SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TARGET = "internal/sheriff/sheriff.go"
PKG = "./internal/sheriff/"

# (id, note, site, replacement)
MUTANTS = [
    # --- SheriffReport.format --------------------------------------------
    ("M1", "the json mode test is inverted",
     'if mode == "json" {\n\t\to := pyval.Obj{}\n\t\to.Set("project"',
     'if mode != "json" {\n\t\to := pyval.Obj{}\n\t\to.Set("project"'),
    ("M2", "the json key order changes",
     'o.Set("project", r.Project)\n\t\to.Set("status", r.Status)',
     'o.Set("status", r.Status)\n\t\to.Set("project", r.Project)'),
    ("M3", "an absent action renders as an empty string, not null",
     'o.Set("recommended_action", nil)', 'o.Set("recommended_action", "")'),
    ("M4", "every action renders, so None becomes \"\"",
     "if r.HasAction {", "if true {"),
    ("M5", "the evidence indent is one space, not two",
     '"  evidence: "+e', '" evidence: "+e'),
    ("M6", "an EMPTY action prints an action line",
     'if r.Action != "" {', "if r.HasAction {"),
    ("M7", "the action label loses its space", '"action: "+r.Action',
     '"action:"+r.Action'),
    ("M8", "the text form joins with CRLF",
     'return strings.Join(lines, "\\n"), nil\n}\n\n// Health is sheriff.SystemHealth.',
     'return strings.Join(lines, "\\r\\n"), nil\n}\n\n// Health is sheriff.SystemHealth.'),
    ("M9", "the project label gains spaces", '"project=" + r.Project',
     '"project = " + r.Project'),
    ("M10", "evidence is dropped from the json form",
     'o.Set("evidence", strList(r.Evidence))', 'o.Set("evidence", pyval.List{})'),

    # --- SystemHealth.format ---------------------------------------------
    ("M11", "health text says status= instead of health=",
     '"health=" + h.Status', '"status=" + h.Status'),
    ("M12", "the per-check separator is = not :",
     '"  "+f.Key+": "+pyval.Str(f.Val)', '"  "+f.Key+"="+pyval.Str(f.Val)'),
    ("M13", "the health json key order changes",
     'o.Set("status", h.Status)\n\t\to.Set("checks", h.Checks)',
     'o.Set("checks", h.Checks)\n\t\to.Set("status", h.Status)'),

    # --- RollupStatus ------------------------------------------------------
    ("M14", "the fail test becomes case-insensitive-ish (uppercase)",
     'strings.HasPrefix(s, "fail")', 'strings.HasPrefix(s, "FAIL")'),
    ("M15", "the fail prefix is the whole word, so 'failure' misses",
     'if strings.HasPrefix(s, "fail") {', 'if s == "fail" {'),
    ("M16", "the warn prefix is the whole word",
     'if strings.HasPrefix(s, "warn") {', 'if s == "warn" {'),
    ("M17", "a fail reports degraded", 'return "critical"', 'return "degraded"'),
    ("M18", "a warn never registers", "warn = true", "warn = warn"),
    ("M19", "an all-ok map reports degraded",
     'if warn {\n\t\treturn "degraded"\n\t}\n\treturn "healthy"',
     'if warn {\n\t\treturn "degraded"\n\t}\n\treturn "degraded"'),
    ("M20", "prefix becomes substring", 'strings.HasPrefix(s, "warn")',
     'strings.Contains(s, "warn")'),

    # --- DetectNoProgress ---------------------------------------------------
    ("M21", "the window shrinks to two",
     "if len(fingerprints) < StuckRepetitionThreshold {",
     "if len(fingerprints) < 2 {"),
    ("M22", "the length guard is off by one",
     "len(fingerprints) < StuckRepetitionThreshold",
     "len(fingerprints) <= StuckRepetitionThreshold"),
    ("M23", "an all-empty run counts as stuck",
     'return recent[0] != ""', "return true"),
    ("M24", "the window is the FIRST three, not the last",
     "recent := fingerprints[len(fingerprints)-StuckRepetitionThreshold:]",
     "recent := fingerprints[:StuckRepetitionThreshold]"),
    ("M25", "the equality scan starts at the wrong index",
     "for _, f := range recent[1:] {", "for _, f := range recent[2:] {"),

    # --- FingerprintProjectState --------------------------------------------
    ("M26", "the parts join with nothing instead of a newline",
     'strings.Join(parts, "\\n")', 'strings.Join(parts, "")'),
    ("M27", "DECISIONS.md is not tail-sliced",
     "text = clipTail(text, 2000)", "text = text"),
    ("M28", "the tail slice becomes a HEAD slice",
     "text = clipTail(text, 2000)", "text = pyval.Clip(text, 2000)"),
    ("M29", "NEXT.md gets the slice instead of DECISIONS.md",
     "if i == 1 {", "if i == 0 {"),
    ("M30", "the file order flips",
     'for i, name := range []string{"NEXT.md", "DECISIONS.md"} {',
     'for i, name := range []string{"DECISIONS.md", "NEXT.md"} {'),
    ("M31", "an absent file is an ERROR, not a skip",
     "if _, serr := os.Stat(path); serr != nil {\n\t\t\tcontinue\n\t\t}",
     'if _, serr := os.Stat(path); serr != nil {\n\t\t\treturn ""\n\t\t}'),
    ("M32", "an unreadable file is a SKIP, not an error",
     "\t\tb, rerr := os.ReadFile(path)\n\t\tif rerr != nil {\n\t\t\treturn \"\"\n\t\t}",
     "\t\tb, rerr := os.ReadFile(path)\n\t\tif rerr != nil {\n\t\t\tcontinue\n\t\t}"),

    # --- clipTail -----------------------------------------------------------
    ("M33", "the tail becomes a head",
     "return string(runes[len(runes)-n:])", "return string(runes[:n])"),
    # EQUIVALENT, proved by hand: at len(runes) == n the mutant takes the
    # slice branch and `runes[len-n:]` is `runes[0:]`, the whole string —
    # the same value the guard returns. A mutant that cannot change an
    # answer is a bad mutant, not a test gap (L8).
    ("M34", "the short-string guard is off by one",
     "if len(runes) <= n {", "if len(runes) < n {"),
    # EQUIVALENT BY CONSTRUCTION, and a battery bug of the author's own
    # making: pre-trimming to n*4 BYTES can never drop a rune the tail of
    # n runes needs, because no UTF-8 rune exceeds four bytes. It measures
    # nothing. Kept labelled rather than deleted, as the record that this
    # decision was checked.
    ("M35", "the slice counts BYTES",
     "func clipTail(s string, n int) string {\n\trunes := []rune(s)",
     "func clipTail(s string, n int) string {\n\trunes := []rune(string([]byte(s)[:min(len(s), n*4)]))"),

    # --- ProjectLifecycleState ----------------------------------------------
    ("M36", "paused is checked before failed",
     'if _, err := os.Stat(filepath.Join(dir, failedMarker)); err == nil {\n\t\treturn "failed"\n\t}\n\tif _, err := os.Stat(filepath.Join(dir, pausedMarker)); err == nil {\n\t\treturn "paused"\n\t}',
     'if _, err := os.Stat(filepath.Join(dir, pausedMarker)); err == nil {\n\t\treturn "paused"\n\t}\n\tif _, err := os.Stat(filepath.Join(dir, failedMarker)); err == nil {\n\t\treturn "failed"\n\t}'),
    ("M37", "the failed marker is misspelled",
     'failedMarker = ".maro-failed"', 'failedMarker = ".maro-fail"'),
    ("M38", "the paused marker is misspelled",
     'pausedMarker = ".maro-paused"', 'pausedMarker = ".maro-pause"'),
    ("M39", "the default lifecycle is failed",
     'return "active"\n}', 'return "failed"\n}'),

    # --- ProjectActivityAgeDays ---------------------------------------------
    ("M40", "the artifacts sample cap drops to 49", "names = names[:50]",
     "names = names[:49]"),
    # EQUIVALENT BY CONTRACT: os.ReadDir is documented to return entries
    # sorted by filename, and these names share one parent, so sorting the
    # full paths is the same order. The call stays because Python spells
    # the sort explicitly and this port states requirements rather than
    # inheriting them from a library promise (L48).
    ("M41", "the entries are not sorted before the cap", "sort.Strings(names)",
     "_ = names"),
    ("M42", "the project dir itself is not a candidate",
     "candidates := []string{dir,\n\t\tfilepath.Join(dir",
     "candidates := []string{\n\t\tfilepath.Join(dir"),
    ("M43", "NEXT.md is not a candidate",
     'filepath.Join(dir, "NEXT.md"), filepath.Join(dir, "DECISIONS.md")}',
     'filepath.Join(dir, "DECISIONS.md")}'),
    ("M44", "the newest becomes the oldest",
     "if m := pySeconds(st.ModTime()); m > newest {",
     "if m := pySeconds(st.ModTime()); m < newest {"),
    ("M45", "the nothing-statted guard flips", "if newest <= 0 {",
     "if newest < 0 {"),
    ("M46", "the age is in hours, not days",
     "return (pySeconds(now) - newest) / 86400.0, true",
     "return (pySeconds(now) - newest) / 3600.0, true"),
    # NEAR-EQUIVALENT, measured rather than argued: the two spellings agree
    # on every whole-second timestamp (which is what utime writes here) and
    # differ by ~1e-12 DAYS on sub-second ones — below what %.0f or a
    # day-resolution threshold can see. Reaching it would need a fixture
    # with a sub-second mtime AND a comparison at nanosecond resolution,
    # which this surface does not have. Kept as the record of the check.
    ("M46b", "the mtime is read through UnixNano, losing precision",
     "return float64(t.Unix()) + float64(t.Nanosecond())/1e9",
     "return float64(t.UnixNano()) / 1e9"),
    ("M47", "an unstattable candidate aborts instead of being skipped",
     "\t\tif err != nil {\n\t\t\tcontinue // Python's `except OSError: continue`\n\t\t}",
     "\t\tif err != nil {\n\t\t\treturn 0, false\n\t\t}"),
    ("M48", "the artifacts directory is never scanned",
     'if _, err := os.Stat(artifacts); err == nil {\n\t\tcandidates = append(candidates, artifacts)',
     'if false {\n\t\tcandidates = append(candidates, artifacts)'),

    # --- ResolveDormantDays ---------------------------------------------------
    ("M49", "a falsy setting takes the default instead of disabling",
     "\tif !pyval.Truthy(v) {\n\t\t// The `or 0`, which runs BEFORE float() and cannot raise.\n\t\treturn 0\n\t}",
     "\tif !pyval.Truthy(v) {\n\t\treturn DormantDaysDefault\n\t}"),
    ("M50", "an unparseable setting disables instead of defaulting",
     "\tf, ok := pyval.Float(v)\n\tif !ok {\n\t\treturn DormantDaysDefault\n\t}",
     "\tf, ok := pyval.Float(v)\n\tif !ok {\n\t\treturn 0\n\t}"),
    ("M51", "only None is falsy", "if !pyval.Truthy(v) {", "if v == nil {"),
    ("M52", "a failing config read disables the check",
     "\tv, err := cfg()\n\tif err != nil {\n\t\treturn DormantDaysDefault\n\t}",
     "\tv, err := cfg()\n\tif err != nil {\n\t\treturn 0\n\t}"),

    # --- CheckProject ---------------------------------------------------------
    ("M53", "a missing directory and a missing NEXT.md share one message",
     '"Project directory does not exist"',
     '"Sheriff check failed: project does not exist"'),
    ("M54", "failed and paused swap their reports",
     '\tcase "failed":\n\t\treturn Report{Project: slug, Status: "failed",',
     '\tcase "failed":\n\t\treturn Report{Project: slug, Status: "paused",'),
    ("M55", "dormancy is checked with >= so a zero threshold catches all",
     "if dormantDays > 0 {", "if dormantDays >= 0 {"),
    ("M56", "the dormancy comparison is inclusive",
     "ok && age > dormantDays", "ok && age >= dormantDays"),
    ("M57", "the dormancy age renders with a decimal", "pyval.PercentF(age, 0)",
     "pyval.PercentF(age, 1)"),
    ("M58", "the threshold renders through %f, not %g",
     "pyval.FormatG(dormantDays)", "pyval.PercentF(dormantDays, 0)"),
    ("M59", "the doing list truncates at two", "firstN(doing, 3)",
     "firstN(doing, 2)"),
    ("M60", "blocked items become a problem",
     '\t\tevidence = append(evidence, fmt.Sprintf("%d blocked item(s): %s",\n\t\t\tlen(blocked), pyval.ReprStrings(firstN(blocked, 3))))',
     '\t\tevidence = append(evidence, fmt.Sprintf("%d blocked item(s): %s",\n\t\t\tlen(blocked), pyval.ReprStrings(firstN(blocked, 3))))\n\t\tproblems["items_stuck_doing"] = true'),
    ("M61", "the complete test is an OR",
     "if len(todo) == 0 && len(doing) == 0 {\n\t\tevidence = append(evidence, \"No TODO items remaining",
     "if len(todo) == 0 || len(doing) == 0 {\n\t\tevidence = append(evidence, \"No TODO items remaining"),
    ("M62", "doing and blocked states swap",
     "\t\tcase orch.StateDoing:\n\t\t\tdoing = append(doing, it.Text)\n\t\tcase orch.StateBlocked:\n\t\t\tblocked = append(blocked, it.Text)",
     "\t\tcase orch.StateDoing:\n\t\t\tblocked = append(blocked, it.Text)\n\t\tcase orch.StateBlocked:\n\t\t\tdoing = append(doing, it.Text)"),
    ("M63", "the repeat threshold is strictly greater",
     "if counts[s] >= StuckRepetitionThreshold {", "if counts[s] > StuckRepetitionThreshold {"),
    # EQUIVALENT: at len(recent) == DecisionWindow the mutant slices
    # recent[0:], which is recent. The bound is only observable above it,
    # and C17 covers that.
    ("M64", "the decision window is inclusive by one",
     "if len(recent) > DecisionWindow {", "if len(recent) >= DecisionWindow {"),
    ("M65", "the decision window is the FIRST twenty",
     "recent = recent[len(recent)-DecisionWindow:]", "recent = recent[:DecisionWindow]"),
    ("M66", "blank lines are not dropped before the window",
     'if strings.TrimSpace(l) != "" {\n\t\t\t\tnonBlank = append(nonBlank, l)\n\t\t\t}',
     "nonBlank = append(nonBlank, l)"),
    ("M67", "the repeated text is not clipped at 60",
     "pyval.Repr(pyval.Clip(repeated[0], 60))", "pyval.Repr(repeated[0])"),
    ("M68", "the repeat count reports the pattern count",
     "counts[repeated[0]]", "len(repeated)"),
    ("M69", "the repeated patterns are reported sorted, not first-seen",
     "\t\tvar repeated []string\n\t\tfor _, s := range order {",
     "\t\tvar repeated []string\n\t\tsort.Strings(order)\n\t\tfor _, s := range order {"),
    ("M70", "the artifact sort is unstable",
     "sort.SliceStable(es, func(i, j int) bool {", "sort.Slice(es, func(i, j int) bool {"),
    ("M71", "the artifact sort picks the OLDEST",
     "return es[i].mt.After(es[j].mt)", "return es[i].mt.Before(es[j].mt)"),
    ("M72", "the artifacts listing is sorted by name",
     "files, gerr := readdirOrder(artifactsDir)",
     "files, gerr := filepath.Glob(filepath.Join(artifactsDir, \"*\"))"),
    ("M73", "the stale-artifact comparison is inclusive",
     "if ageMin > float64(windowMinutes) && len(doing) > 0 {",
     "if ageMin >= float64(windowMinutes) && len(doing) > 0 {"),
    ("M74", "a stale artifact is stale regardless of doing items",
     "if ageMin > float64(windowMinutes) && len(doing) > 0 {",
     "if ageMin > float64(windowMinutes) {"),
    ("M75", "an empty artifacts dir is a problem even with no doing items",
     "} else if gerr == nil && len(doing) > 0 {", "} else if gerr == nil {"),
    ("M76", "nil evidence is left nil, so it renders as null",
     "\tif len(evidence) == 0 {\n\t\tevidence = []string{}\n\t}", "\t_ = evidence"),
    ("M77", "the healthy counts are swapped",
     '"Project healthy: %d todo, %d doing",\n\t\t\tlen(todo), len(doing))',
     '"Project healthy: %d todo, %d doing",\n\t\t\tlen(doing), len(todo))'),
    # EQUIVALENT, and the Python has the same redundancy: this branch is
    # only reached when `problems` is empty, and a non-empty doing list
    # always records items_stuck_doing. So len(doing) == 0 already holds
    # and the second conjunct cannot change the answer. Ported anyway,
    # because the shape is the original's (L48).
    ("M78", "the complete diagnosis needs only an empty todo list",
     "\t\tif len(todo) == 0 && len(doing) == 0 {\n\t\t\tdiagnosis = ",
     "\t\tif len(todo) == 0 {\n\t\t\tdiagnosis = "),
    ("M79", "items_stuck_doing loses to the stall branch",
     'case problems["repeated_decisions"] || problems["items_stuck_doing"]:',
     'case problems["repeated_decisions"]:'),
    ("M80", "the stuck action loses its slug",
     '"Force-complete or skip stuck items: orch done " + slug',
     '"Force-complete or skip stuck items: orch done"'),
    ("M81", "a problem-free project reports stuck", "if len(problems) == 0 {",
     "if len(problems) != 0 {"),
    ("M82", "the doing-state evidence loses its count",
     '"%d item(s) stuck in \'doing\' state: %s",\n\t\t\tlen(doing), pyval.ReprStrings(firstN(doing, 3))',
     '"%d item(s) stuck in \'doing\' state: %s",\n\t\t\tlen(firstN(doing, 3)), pyval.ReprStrings(firstN(doing, 3))'),
    ("M83", "the DECISIONS read is skipped entirely",
     "if _, serr := os.Stat(decisionsPath); serr == nil {", "if false {"),
    ("M84", "the artifacts block is skipped entirely",
     "if _, serr := os.Stat(artifactsDir); serr == nil {", "if false {"),
    ("M85", "the lifecycle short-circuit is dropped",
     "\tswitch ProjectLifecycleState(ws, slug) {", "\tswitch \"\" {"),
]


ENV = dict(os.environ, MARO_PYPROBE_REQUIRED="1")


def run(cmd, cwd, timeout=900):
    # MARO_PYPROBE_REQUIRED is not optional here. Without it a copy that
    # cannot see ../../../src skips every differential and `go test` prints
    # ok, so EVERY mutant survives and the battery reads as a total test
    # gap. That is exactly what run 1 of this battery reported — the env was
    # built and never passed — and it is the failure pyprobe.SrcDir's own
    # comment was written about.
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env=ENV)


def main():
    wanted = set(sys.argv[1:])
    mutants = [m for m in MUTANTS if not wanted or m[0] in wanted]

    tree = tempfile.mkdtemp(prefix="shmut-")
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
        open(target, "w").write(original.replace(site, repl, 1))
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
