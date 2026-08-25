package graduation

import (
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyGraduationWindowSrc drives scan_candidates and _already_proposed over a
// seeded pair of ledgers and reports what each decided.
//
// It drives the two PUBLIC gates, not the line reader they share. A probe of
// the reader would be evidence about the call I made; these two are what
// decide whether the system proposes a permanent change to its own
// behaviour, and the lookback window is an argument to both.
const pyGraduationWindowSrc = `
import json, sys
import graduation

_argv = json.loads(sys.argv[1])
cands = graduation.scan_candidates(min_count=_argv["min_count"],
                                   lookback=_argv["lookback"])
print(json.dumps({
    "candidates": [{"failure_class": c.failure_class, "count": c.count,
                    "loop_ids": c.loop_ids, "evidence": c.evidence_samples}
                   for c in cands],
    "already": {fc: graduation._already_proposed(fc, _argv["dedup"])
                for fc in _argv["classes"]},
}))
`

// Real template classes: scan_candidates drops any failure_class not in
// _GRADUATION_TEMPLATES, so a fixture built on invented class names would
// count zero rows in BOTH runtimes and agree about nothing (lens 1).
const (
	fcMain  = "retry_churn"
	fcOther = "token_explosion"
)

// seedGraduationLedgers writes the two ledgers this package reads.
//
// The separators are built from explicit escapes rather than typed: a raw
// control byte is invisible in a diff and in a terminal, and an editor that
// strips one silently turns the fixture into one that pins nothing. Last
// round a stray C0 byte in a fixture produced a false finding this way.
//
// The diagnoses ledger holds six PHYSICAL rows, arranged so the two
// runtimes disagree about how many LINES that is:
//
//	A  a plain row of fcMain
//	B  a row carrying \x0b inside a JSON string — splitlines() breaks on
//	   \x0b, so CPython sees TWO lines here, neither of them valid JSON,
//	   and loses the row; a "\n" split sees one row and counts it
//	D  a plain row of fcMain
//	E  a plain row of fcMain
//	×3 three rows that are ONLY \x1f — str.strip() removes them (blank,
//	   skipped), strings.TrimSpace does not (kept, unparseable, skipped).
//	   Same outcome per line, and two DIFFERENT effects on the window:
//	   they shift the line count, and — because Python slices BEFORE
//	   dropping blanks — they consume window budget. Three CONSECUTIVE
//	   blanks, positioned so a tight window straddles them, is what makes
//	   slice-before-filter distinguishable from slice-after-filter; a
//	   fixture with its blanks spread out has both orders land on the same
//	   real rows and pins neither
//	F  a row of a DIFFERENT template class, so the count-desc ordering and
//	   the class filter are both exercised
//	G  a valid row with a TRAILING \x1f — Python strips it and parses;
//	   Go's json rejects the trailing byte (its whitespace set is space,
//	   tab, \n, \r), so TrimSpace loses a row Python keeps
//
// So B moves the window by a line, the blanks move it by three more and
// decide whether the slice happens first, and G changes what parses inside.
func seedGraduationLedgers(t *testing.T, ws string) {
	t.Helper()
	row := func(fc, loop, ev string) string {
		return `{"failure_class":"` + fc + `","loop_id":"` + loop +
			`","evidence":["` + ev + `"]}`
	}
	writeLedger(t, ws, "diagnoses.jsonl",
		row(fcMain, "l1", "oldest")+"\n"+
			row(fcMain, "l2", "a\x0bb")+"\n"+
			row(fcMain, "l3", "third")+"\n"+
			row(fcMain, "l4", "fourth")+"\n"+
			"\x1f\n\x1f\n\x1f\n"+
			row(fcOther, "l5", "te")+"\n"+
			row(fcMain, "l6", "sixth")+"\x1f\n")

	// The dedup ledger runs the same two mechanisms in the opposite
	// direction: the first row's \x0b HIDES a proposal from a "\n" split
	// (the valid JSON is the SECOND fragment, which only splitlines()
	// produces), while the last row's trailing \x1f hides one from
	// TrimSpace. The three blanks give this window the same slice-order
	// sensitivity as the diagnoses one — it is a separate argument on a
	// separate file, so a fixture that only exercised the scan window would
	// be pinning one caller and reporting on two.
	writeLedger(t, ws, "suggestions.jsonl",
		"garbage\x0b"+`{"failure_pattern":"graduation:cost_spike"}`+"\n"+
			`{"failure_pattern":"graduation:budget_exhaustion"}`+"\n"+
			"\x1f\n\x1f\n\x1f\n"+
			`{"failure_pattern":"graduation:`+fcMain+`"}`+"\x1f\n")
}

func writeLedger(t *testing.T, ws, name, body string) {
	t.Helper()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

type pyCandidate struct {
	FailureClass string   `json:"failure_class"`
	Count        int      `json:"count"`
	LoopIDs      []string `json:"loop_ids"`
	Evidence     []string `json:"evidence"`
}

type pyGradOut struct {
	Candidates []pyCandidate   `json:"candidates"`
	Already    map[string]bool `json:"already"`
}

var dedupClasses = []string{fcMain, "cost_spike", "budget_exhaustion", "adapter_timeout"}

func runGradBoth(t *testing.T, seed func(*testing.T, string), minCount, lookback, dedup int) (pyGradOut, []Candidate, map[string]bool) {
	t.Helper()
	pyWS, goWS := t.TempDir(), t.TempDir()
	seed(t, pyWS)
	seed(t, goWS)

	var want pyGradOut
	pyprobe.Probe{Marker: "graduation.py", Workspace: pyWS}.RunJSON(
		t, pyGraduationWindowSrc, &want, pyprobe.Arg(t, map[string]any{
			"min_count": minCount, "lookback": lookback,
			"dedup": dedup, "classes": dedupClasses}))

	got := ScanCandidates(goWS, minCount, lookback)
	already := map[string]bool{}
	for _, fc := range dedupClasses {
		already[fc] = AlreadyProposed(goWS, fc, dedup)
	}
	return want, got, already
}

func cmpGrad(t *testing.T, want pyGradOut, got []Candidate, already map[string]bool) {
	t.Helper()
	if len(got) != len(want.Candidates) {
		t.Fatalf("%d candidates %+v, CPython %d %+v",
			len(got), got, len(want.Candidates), want.Candidates)
	}
	for i := range got {
		w := want.Candidates[i]
		if got[i].FailureClass != w.FailureClass || got[i].Count != w.Count {
			t.Errorf("candidate %d: %s×%d, CPython %s×%d", i,
				got[i].FailureClass, got[i].Count, w.FailureClass, w.Count)
		}
		cmpStrs(t, "loop_ids", got[i].LoopIDs, w.LoopIDs)
		cmpStrs(t, "evidence", got[i].EvidenceSamples, w.Evidence)
	}
	for _, fc := range dedupClasses {
		if already[fc] != want.Already[fc] {
			t.Errorf("AlreadyProposed(%q) = %v, CPython %v",
				fc, already[fc], want.Already[fc])
		}
	}
}

// TestGraduationWindowMatchesCPython pins both gates over a ledger whose
// line COUNT differs between the two spellings of "split into lines".
//
// `lines[-lookback:]` is applied to the result of splitlines(), so a row
// that Python counts as two lines pushes an older row out of the window
// there and not here — a DIFFERENT SET of diagnoses reaching a gate whose
// job is to propose permanent changes to the system's own behaviour. A test
// that only compared the rows both runtimes parsed would watch them agree
// on every row they both looked at, and never see the ones only one of them
// looked at (lens 1).
//
// Several widths, because a limit with no case at its own boundary is a
// limit nothing pins (lens 11): 3 excludes the exotic rows entirely, 6
// straddles them, and 8/100 cover the whole file. The last matters most —
// it shows the divergence is not a knife-edge artifact of a small window.
func TestGraduationWindowMatchesCPython(t *testing.T) {
	for _, lookback := range []int{3, 5, 6, 8, 100} {
		t.Run("lookback"+strconv.Itoa(lookback), func(t *testing.T) {
			want, got, already := runGradBoth(t, seedGraduationLedgers, 3, lookback, 2)
			cmpGrad(t, want, got, already)
		})
	}
}

// TestGraduationDedupWindowMatchesCPython varies the DEDUP window
// independently: it is a separate argument on a separate ledger, and
// RunGraduation passes a constant (proposeDedupWindow) that no
// scan-candidates test would ever exercise.
func TestGraduationDedupWindowMatchesCPython(t *testing.T) {
	for _, dedup := range []int{1, 2, 3, 200} {
		t.Run("dedup"+strconv.Itoa(dedup), func(t *testing.T) {
			want, got, already := runGradBoth(t, seedGraduationLedgers, 3, 100, dedup)
			cmpGrad(t, want, got, already)
		})
	}
}

// TestGraduationRefusesUndecodableLedger pins read_text's OTHER rule.
//
// Both call sites wrap the read in a bare `except`, so one bad byte
// anywhere in the file means Python proposes nothing and dedups nothing.
// `os.ReadFile` + `string(raw)` carried neither half: encoding/json
// substitutes U+FFFD per bad byte, so the port would build a candidate out
// of content nobody wrote and propose a permanent rule from it.
//
// The bad byte is placed in the LAST row on purpose. A decoder that gave up
// only at the point of failure would still have produced the earlier rows,
// and this fixture would pass while the port kept three quarters of a file
// Python refused whole.
func TestGraduationRefusesUndecodableLedger(t *testing.T) {
	seed := func(t *testing.T, ws string) {
		t.Helper()
		seedGraduationLedgers(t, ws)
		p := filepath.Join(ws, "memory", "diagnoses.jsonl")
		raw, err := os.ReadFile(p)
		if err != nil {
			t.Fatal(err)
		}
		// 0xC3 with no continuation: "invalid continuation byte" mid-buffer.
		row := `{"failure_class":"` + fcMain +
			`","loop_id":"l7","evidence":["b` + "\xc3" + `d"]}`
		body := string(raw) + row + "\n"
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, got, already := runGradBoth(t, seed, 3, 100, 200)
	if len(want.Candidates) != 0 {
		t.Fatalf("fixture is not exercising the refusal: CPython returned %d "+
			"candidates from an undecodable ledger", len(want.Candidates))
	}
	cmpGrad(t, want, got, already)
	// The dedup ledger is still clean, so its answers must NOT be blanked
	// by the diagnoses refusal — a reader that returned nil for everything
	// would satisfy the assertion above while breaking this one.
	if !already["cost_spike"] {
		t.Error("the clean suggestions ledger stopped answering; the refusal " +
			"is not scoped to the file that is actually undecodable")
	}
}

// pyWindowSrc is the window rule alone: splitlines, slice, strip, drop
// blanks — the four spellings scan_candidates and _already_proposed each
// write out inline.
const pyWindowSrc = `
import json, sys
_argv = json.loads(sys.argv[1])
text, n = _argv["text"], _argv["n"]
lines = text.splitlines()
if n > 0:
    lines = lines[-n:]
print(json.dumps([s for s in (l.strip() for l in lines) if s]))
`

// windowTexts are the inputs both helpers get asked about. Assembled from
// escapes, never typed: the whole point of each case is a byte that renders
// as nothing.
func windowTexts() map[string]string {
	return map[string]string{
		"empty":          "",
		"no-trailing-nl": "a\nb\nc",
		"trailing-nl":    "a\nb\nc\n",
		"vt-splits":      "a\x0bb\nc\n",
		"fs-gs-rs":       "a\x1cb\x1dc\x1ed\n",
		"us-does-not":    "a\x1fb\n",
		"us-alone-blank": "x\n\x1f\n\x1f\ny\n",
		"trailing-us":    "row\x1f\nnext\n",
		"crlf":           "a\r\nb\r\n",
		"lone-cr":        "a\rb\r",
		// U+0085, U+2028 and U+2029 are the three non-ASCII separators
		// splitlines() breaks on. Spelled \u, never \x: a raw 0x85 byte is
		// not UTF-8, and the probe's own JSON arg channel substitutes U+FFFD
		// before Python ever sees it, so the fixture would be measuring the
		// transport rather than the rule. It read as agreement for exactly
		// one run before this comment existed.
		"nel-and-para":     "a\u0085b c d\n",
		"blanks-then-rows": "1\n2\n3\n \n\t\n\x1f\n4\n5\n",
		"only-blanks":      "\n \n\x1f\n\n",
		"nbsp-is-space":    "a \nb\n",
	}
}

// TestWindowHelpersMatchCPython pins pyWindowLines against Python directly,
// and then pins lastLines and tailLines to it.
//
// lastLines is otherwise UNREACHABLE from a differential: it is the in-lock
// half of a dedup re-check that CPython does not have at all (Python's
// run_graduation appends under the lock without re-checking), and with no
// concurrent writer it always sees the file the pre-check already read. So
// the only way it can be wrong in production is by disagreeing with
// tailLines — which is exactly what it was doing, and exactly what no test
// through the gate could have shown. Asserting the two against each other
// AND against CPython is the available evidence, and it is the evidence
// that matters.
func TestWindowHelpersMatchCPython(t *testing.T) {
	for name, text := range windowTexts() {
		for _, n := range []int{0, 1, 2, 3, 4, 5, 100} {
			t.Run(name+"/n"+strconv.Itoa(n), func(t *testing.T) {
				var want []string
				pyprobe.Probe{Stdlib: true}.RunJSON(t, pyWindowSrc, &want,
					pyprobe.Arg(t, map[string]any{"text": text, "n": n}))

				cmpStrs(t, "pyWindowLines", pyWindowLines(text, n), want)
				cmpStrs(t, "lastLines", lastLines(text, n), want)

				// tailLines reaches the same rule through a file. Its own
				// read is the part CPython's read_text covers; the window
				// below it must not differ by having come off disk.
				ws := t.TempDir()
				writeLedger(t, ws, "diagnoses.jsonl", text)
				cmpStrs(t, "tailLines", tailLines(
					filepath.Join(ws, "memory", "diagnoses.jsonl"), n), want)
			})
		}
	}
}

// TestLastLinesRefusesUndecodableTail pins the decode half of lastLines,
// which likewise has no path through the gate.
//
// Refusing yields an empty window, so the write PROCEEDS — the same
// direction as Python's `except: pass` → False, and the safe one: a
// corrupted tail must not be allowed to parse into a suppression of a
// suggestion that was never actually proposed.
func TestLastLinesRefusesUndecodableTail(t *testing.T) {
	good := `{"failure_pattern":"graduation:` + fcMain + `"}`
	if got := lastLines(good+"\n", 200); len(got) != 1 {
		t.Fatalf("the clean control does not parse: %q", got)
	}
	if got := lastLines(good+"\n"+"bad\xc3\n", 200); got != nil {
		t.Errorf("lastLines(undecodable) = %q, want nil — a lenient read "+
			"substitutes U+FFFD and lets a corrupted row suppress a write", got)
	}
	if proposedIn(lastLines(good+"\n"+"bad\xc3\n", 200), fcMain) {
		t.Error("an undecodable tail still suppressed a proposal")
	}
}

func cmpStrs(t *testing.T, what string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Errorf("%s: %d entries %v, CPython %d %v", what, len(got), got,
			len(want), want)
		return
	}
	for i := range got {
		if got[i] != want[i] {
			t.Errorf("%s[%d] = %q, CPython %q", what, i, got[i], want[i])
		}
	}
}
