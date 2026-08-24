package record

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

// StampOutcomeStopVerdict rewrites ONE line of a shared append-only
// ledger, in place, in a file the other runtime appends to concurrently.
// Almost every way of getting it slightly wrong is a whole-file edit:
// a miss that still writes, a splitlines/join round trip that normalizes
// separators, a map round trip that alphabetizes every key, a refused row
// that gets re-serialized anyway.
//
// So the tests below check what the file looks like AFTER a stamp — every
// other line byte-identical to what it was — rather than only checking
// that the stamped row got its field.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "memory_ledger.py")); err != nil {
		t.Skipf("python source tree unavailable: %v", err)
	}
	return p
}

// runPyIn runs a probe with MARO_WORKSPACE pointed at ws, refusing to run
// if that resolves anywhere near the live workspace. See the matching
// guard in internal/runs — the rule comes from a real overwrite of a live
// ledger by a test probe (2026-08-16).
func runPyIn(t *testing.T, ws, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	guard := `
import os, sys
_ws = os.environ.get("MARO_WORKSPACE", "")
_home = os.path.realpath(os.path.expanduser("~/.maro"))
if not _ws or os.path.commonpath([os.path.realpath(_ws), _home]) == _home:
    raise SystemExit("refusing to run: MARO_WORKSPACE is %r" % _ws)
import memory_ledger
_p = memory_ledger._outcomes_path()
if not str(_p).startswith(os.path.realpath(_ws)) and not str(_p).startswith(_ws):
    raise SystemExit("refusing to run: outcomes path resolved to %s, outside %s" % (_p, _ws))
`
	cmd := exec.Command("python3", append([]string{"-c", guard + src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t), "MARO_WORKSPACE="+ws)
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

const pyLedgerStampSrc = `
import json, sys
from memory_ledger import stamp_outcome_stop_verdict
from pathlib import Path
import os
loop_id, verdict, evidence = json.loads(sys.argv[1])
hit = stamp_outcome_stop_verdict(loop_id, verdict, evidence)
p = Path(os.environ["MARO_WORKSPACE"]) / "memory" / "outcomes.jsonl"
sys.stdout.write(json.dumps([hit, p.read_text(encoding="utf-8") if p.exists() else None]))
`

// seedLedger writes the store VERBATIM on both sides. The rows are spelled
// as raw text rather than built from a Go value and serialized, because
// the thing under test is what happens to bytes that were already there:
// a fixture that went through this port's own encoder on the way in could
// not detect an encoder difference on the way out.
func seedLedger(t *testing.T, ws string, lines []string) string {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "outcomes.jsonl")
	body := strings.Join(lines, "\n")
	if len(lines) > 0 {
		body += "\n"
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

// The rows a real store holds: several loops, the same loop twice
// (superseded attempt then current), a row already carrying a stop
// verdict, a row whose keys are in a non-alphabetical order that a map
// round trip would destroy, and prose containing the characters
// encoding/json and json.dumps disagree about.
func ledgerFixture() []string {
	return []string{
		`{"ts": "2026-08-20T10:00:00Z", "loop_id": "loop-a", "status": "done", "goal": "first"}`,
		`{"ts": "2026-08-20T11:00:00Z", "loop_id": "loop-b", "status": "stuck", "goal": "prefer a > b & c < d", "note": "caf\u00e9 \u2192 retry"}`,
		`{"ts": "2026-08-20T12:00:00Z", "loop_id": "loop-b", "status": "stuck", "goal": "second attempt", "iterations": 7, "cost": 1.5}`,
		`{"ts": "2026-08-20T13:00:00Z", "loop_id": "loop-c", "status": "stuck", "stop_verdict": "out-of-budget", "stop_evidence": "cap"}`,
	}
}

func TestLedgerStampMatchesCPython(t *testing.T) {
	cases := []struct {
		name     string
		loopID   string
		verdict  string
		evidence string
	}{
		{"newest row for a repeated loop", "loop-b", "reachable-but-not-worth-it",
			"director escalation close at depth 2 (confidence 8/10): cost exceeds value"},
		{"a row that already carries a verdict", "loop-c", "reachable-but-not-worth-it", "later judgment"},
		{"no evidence leaves the existing one", "loop-c", "thesis-refuted", ""},
		{"a row whose prose needs ensure_ascii", "loop-b", "lost-the-plot",
			"answered a > b in the café path → not the ask"},
		{"evidence past the 800 cap", "loop-a", "external-interrupt", strings.Repeat("stopped ", 200)},
		{"a loop that is not in the store", "loop-missing", "thesis-refuted", "e"},
		{"an off-vocabulary verdict", "loop-a", "made-up-verdict", "e"},
		{"an empty verdict", "loop-a", "", "e"},
		{"an empty loop id", "", "thesis-refuted", "e"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			goWS, pyWS := t.TempDir(), t.TempDir()
			goPath := seedLedger(t, goWS, ledgerFixture())
			seedLedger(t, pyWS, ledgerFixture())

			gotHit, err := StampOutcomeStopVerdict(goWS, c.loopID, c.verdict, c.evidence)
			if err != nil {
				t.Fatal(err)
			}
			gotRaw, err := os.ReadFile(goPath)
			if err != nil {
				t.Fatal(err)
			}

			arg, err := json.Marshal([]string{c.loopID, c.verdict, c.evidence})
			if err != nil {
				t.Fatal(err)
			}
			var want []any
			if err := json.Unmarshal([]byte(runPyIn(t, pyWS, pyLedgerStampSrc, string(arg))), &want); err != nil {
				t.Fatal(err)
			}
			wantHit, _ := want[0].(bool)
			wantBody, _ := want[1].(string)

			if gotHit != wantHit {
				t.Errorf("returned %v, CPython returned %v", gotHit, wantHit)
			}
			if string(gotRaw) != wantBody {
				t.Errorf("the store is not CPython's:\n--- go ---\n%s\n--- py ---\n%s",
					gotRaw, wantBody)
			}
		})
	}
}

// A MISS must not rewrite the file at all. The splitlines/join round trip
// normalizes separators and appends a trailing newline, so a lookup that
// found nothing would still edit the whole store — turning a failed
// search into a silent whole-file write on a file the other runtime is
// appending to.
//
// Checked by INODE as well as by content, because a byte-identical
// rewrite is still a rewrite: it takes the lock, replaces the file, and
// races an appender. (This assertion was written against mtime first,
// which on this filesystem cannot fail — see the note at the assertion.)
func TestAMissRewritesNothing(t *testing.T) {
	ws := t.TempDir()
	path := seedLedger(t, ws, ledgerFixture())
	before, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	beforeBody, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}

	for _, c := range []struct{ loop, verdict string }{
		{"loop-missing", "thesis-refuted"},
		{"loop-a", "not-a-verdict"},
		{"", "thesis-refuted"},
		{"loop-a", ""},
	} {
		hit, err := StampOutcomeStopVerdict(ws, c.loop, c.verdict, "e")
		if err != nil {
			t.Fatal(err)
		}
		if hit {
			t.Fatalf("(%q, %q) reported a hit", c.loop, c.verdict)
		}
	}

	after, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	afterBody, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(afterBody) != string(beforeBody) {
		t.Error("a miss changed the store's contents")
	}
	// The INODE, not the mtime. Measured on this box: seeding the store
	// with os.WriteFile and then immediately replacing it via AtomicWrite
	// leaves both stats reporting the same nanosecond, so an mtime
	// assertion here cannot fail — it reported PASS against a mutant that
	// printed "MUTANT WRITING 479 matched false" as it wrote. AtomicWrite
	// always renames a fresh temp over the target, so the inode number is
	// the thing that necessarily changes, and it is also the thing that
	// actually matters: an appender holding this file open keeps writing
	// into the replaced one.
	if inode(t, after) != inode(t, before) {
		t.Error("a miss rewrote the file — byte-identical, but it still replaced " +
			"the inode and raced anyone appending to it")
	}
}

func inode(t *testing.T, fi os.FileInfo) uint64 {
	t.Helper()
	st, ok := fi.Sys().(*syscall.Stat_t)
	if !ok {
		t.Skip("no inode available on this platform")
	}
	return uint64(st.Ino)
}

// A missing store is a miss, and must NOT be created. Python reads first
// and returns False on FileNotFoundError, before any writer is involved;
// a read-modify-write would materialize an empty outcomes.jsonl in a
// workspace that has none — a store appearing out of a failed lookup.
func TestAMissingStoreIsNotCreated(t *testing.T) {
	ws := t.TempDir()
	hit, err := StampOutcomeStopVerdict(ws, "loop-a", "thesis-refuted", "e")
	if err != nil {
		t.Fatal(err)
	}
	if hit {
		t.Error("reported a hit against a store that does not exist")
	}
	if _, err := os.Stat(filepath.Join(ws, "memory", "outcomes.jsonl")); err == nil {
		t.Error("the lookup CREATED the store")
	}
}

// The stamped row keeps its original key ORDER, and the untouched rows
// keep their original BYTES.
//
// The key order is the half that a map-based port loses silently: no
// consumer parses these bytes, so nothing breaks — the store just stops
// being one both runtimes wrote the same way, and every stamped row shows
// up rewritten end to end in any diff of the workspace.
func TestOnlyTheMatchedRowChangesAndItKeepsItsKeyOrder(t *testing.T) {
	ws := t.TempDir()
	fixture := ledgerFixture()
	path := seedLedger(t, ws, fixture)

	hit, err := StampOutcomeStopVerdict(ws, "loop-b", "lost-the-plot", "off-ask")
	if err != nil {
		t.Fatal(err)
	}
	if !hit {
		t.Fatal("no row was stamped")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != len(fixture) {
		t.Fatalf("the store has %d rows, was %d", len(lines), len(fixture))
	}
	// Row index 2 is the NEWEST loop-b row; 0, 1 and 3 must be untouched
	// byte for byte — including row 1, which is the OLDER loop-b row and
	// is the one a forward scan would have hit instead.
	for _, i := range []int{0, 1, 3} {
		if lines[i] != fixture[i] {
			t.Errorf("row %d changed:\n was %s\n now %s", i, fixture[i], lines[i])
		}
	}
	// The stamped row: original keys first, in their original order, with
	// the two new keys appended.
	want := `{"ts": "2026-08-20T12:00:00Z", "loop_id": "loop-b", "status": "stuck", ` +
		`"goal": "second attempt", "iterations": 7, "cost": 1.5, ` +
		`"stop_verdict": "lost-the-plot", "stop_evidence": "off-ask"}`
	if lines[2] != want {
		t.Errorf("the stamped row is not the original row plus two keys:\n got %s\nwant %s",
			lines[2], want)
	}
}

// A row this port REFUSES to parse must be carried verbatim, not
// laundered by the rewrite. LoadsCleanOrdered's four refusals are the
// admission predicate; a refused row is skipped, and the scan keeps
// looking backwards past it.
func TestARefusedRowIsCarriedVerbatimAndSkipped(t *testing.T) {
	ws := t.TempDir()
	fixture := []string{
		`{"ts": "1", "loop_id": "loop-a", "status": "done"}`,
		// Duplicate name: both runtimes' decoders keep the last value, so
		// re-serializing would destroy the other one.
		`{"loop_id": "loop-a", "applied": false, "applied": true}`,
		`{"not json at all`,
	}
	path := seedLedger(t, ws, fixture)
	hit, err := StampOutcomeStopVerdict(ws, "loop-a", "thesis-refuted", "converged")
	if err != nil {
		t.Fatal(err)
	}
	if !hit {
		t.Fatal("the scan did not reach the parseable loop-a row past the refused ones")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	for _, i := range []int{1, 2} {
		if lines[i] != fixture[i] {
			t.Errorf("a refused row was rewritten:\n was %s\n now %s", fixture[i], lines[i])
		}
	}
	if !strings.Contains(lines[0], `"stop_verdict": "thesis-refuted"`) {
		t.Errorf("the stamp did not land on row 0: %s", lines[0])
	}
}

// Python compares `row.get("loop_id") == loop_id` against a str, so a row
// whose loop_id decoded as a NUMBER does not match — 5 == "5" is False.
// Spelling the number and comparing strings would stamp a row Python
// skips, which is two runtimes disagreeing about which run a judgment
// belongs to.
func TestANumericLoopIDDoesNotMatchItsSpelling(t *testing.T) {
	ws := t.TempDir()
	path := seedLedger(t, ws, []string{
		`{"loop_id": 5, "status": "stuck"}`,
	})
	hit, err := StampOutcomeStopVerdict(ws, "5", "thesis-refuted", "e")
	if err != nil {
		t.Fatal(err)
	}
	if hit {
		raw, _ := os.ReadFile(path)
		t.Errorf("stamped a row whose loop_id is the number 5 for the id \"5\": %s", raw)
	}
}

// LoadsCleanOrdered and LoadsClean are the SAME admission predicate with
// two return shapes, and the whole point of building it that way is that
// a rewriting reader must not admit a line the removing reader refuses.
// If they ever disagree, the two readers disagree about which rows exist.
//
// The corpus is one line per refusal LoadsClean documents, plus a clean
// line so the test cannot pass by refusing everything.
func TestTheOrderedLoaderRefusesEverythingItsSiblingRefuses(t *testing.T) {
	cases := []struct {
		name string
		line string
	}{
		{"clean", `{"a": 1, "b": "x"}`},
		{"duplicate names", `{"applied": false, "applied": true}`},
		{"trailing data", `{"a": 1}{"b": 2}`},
		{"lone surrogate escape", `{"a": "\udcff"}`},
		{"raw non-utf8", "{\"a\": \"\xff\xfe\"}"},
		{"not an object", `[1, 2, 3]`},
		{"not json", `{nope`},
		{"NaN", `{"a": NaN}`},
		{"Infinity", `{"a": Infinity}`},
		{"nested duplicate names", `{"a": {"k": 1, "k": 2}}`},
		{"a bare scalar", `"just a string"`},
		{"empty", ``},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			_, mapErr := LoadsClean(c.line)
			_, objErr := LoadsCleanOrdered(c.line)
			if (mapErr == nil) != (objErr == nil) {
				t.Fatalf("the two loaders disagree about admitting this line — "+
					"LoadsClean err=%v, LoadsCleanOrdered err=%v", mapErr, objErr)
			}
		})
	}
	// Anti-vacuity: the corpus has to REACH both answers, or a loader that
	// refused (or admitted) everything would pass every case above.
	admitted, refused := 0, 0
	for _, c := range cases {
		if _, err := LoadsCleanOrdered(c.line); err == nil {
			admitted++
		} else {
			refused++
		}
	}
	if admitted == 0 || refused == 0 {
		t.Errorf("the corpus reached %d admits and %d refusals; it must reach both",
			admitted, refused)
	}
}

// Python's str.splitlines() breaks on eight separators Go's strings.Split
// does not, and this store is APPEND-ONLY from more than one writer — so
// a foreign row carrying a raw U+2028 or a vertical tab decides how many
// rows each runtime thinks the file has. Disagree here and the two
// runtimes stamp different rows, or the Go side rewrites a line the
// Python side had already split in two.
//
// The rejoin is Python's own lossy normalization and has to be matched
// including the loss: every one of those separators comes back as \n.
// That is a real edit to a foreign writer's bytes, and the only thing
// worse than making it is making it differently.
//
// ledgerFixture deliberately does NOT carry these — the tests that index
// rows by position would then be indexing something else — so this store
// is spelled out here.
func TestAForeignSeparatorSplitsRowsThePythonWay(t *testing.T) {
	fixture := []string{
		`{"ts": "1", "loop_id": "loop-a", "status": "done"}`,
		// U+2028 raw, inside a string. Legal JSON on both sides, which is
		// the point: a plain newline split leaves this row parseable and
		// STAMPABLE, where splitlines cuts it into two unparseable halves
		// that get skipped and carried.
		"{\"ts\": \"2\", \"loop_id\": \"loop-a\", \"note\": \"a\u2028b\"}",
		// A vertical tab, which json.loads refuses in strict mode either
		// way — so this one is not about which row gets stamped but about
		// what the REJOIN writes back.
		"{\"ts\": \"3\", \"loop_id\": \"loop-z\", \"note\": \"c\vd\"}",
	}
	goWS, pyWS := t.TempDir(), t.TempDir()
	goPath := seedLedger(t, goWS, fixture)
	seedLedger(t, pyWS, fixture)

	hit, err := StampOutcomeStopVerdict(goWS, "loop-a", "thesis-refuted", "converged")
	if err != nil {
		t.Fatal(err)
	}
	arg, err := json.Marshal([]string{"loop-a", "thesis-refuted", "converged"})
	if err != nil {
		t.Fatal(err)
	}
	var want []any
	if err := json.Unmarshal([]byte(runPyIn(t, pyWS, pyLedgerStampSrc, string(arg))), &want); err != nil {
		t.Fatal(err)
	}
	if wantHit, _ := want[0].(bool); hit != wantHit {
		t.Errorf("hit = %v, CPython says %v — the two runtimes disagree about "+
			"which row is the newest one for this loop", hit, wantHit)
	}
	raw, err := os.ReadFile(goPath)
	if err != nil {
		t.Fatal(err)
	}
	wantBody, _ := want[1].(string)
	if string(raw) != wantBody {
		t.Errorf("the store is not CPython's:\n--- go ---\n%q\n--- py ---\n%q",
			raw, wantBody)
	}
}
