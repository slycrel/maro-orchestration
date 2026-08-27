package intent

import (
	"context"
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

// Go's regexp reads `\w` as ASCII `[0-9A-Za-z_]` and `\b` as an
// ASCII-only boundary; Python's `re` on a str reads both as Unicode.
// Every regex in this file was transcribed character for character, so
// the two engines matched a different language — and these regexes pick
// the EXECUTION LANE, which decides whether a run writes a
// task_type:"now" outcome row or an agenda run dir and a mission
// (adversarial mission-r6 MEDIUM).
//
// Driven against the real _requires_file_output, so no arm is argued
// from a reading of the pattern.
// Built from code points: a fixture whose subject is WHICH separator it
// carries cannot be reviewed if the answer is "look closely at that space".
var (
	nbspIntent = string(rune(0x00A0)) // NO-BREAK SPACE
	vtabIntent = string(rune(0x000B)) // LINE TABULATION
)

var fileOutputCorpus = []string{
	// THE finding: a non-ASCII letter inside the filename stem, which
	// Python's [\w-]+ matches and Go's did not.
	"save the summary to café.md",
	"write the notes to résumé.txt",
	"export it to 研究.md",
	"save the summary to naïve-notes.md",

	// The same shape with an ASCII stem, so the corpus carries the pair
	// that separates the two classes.
	"save the summary to cafe.md",
	"save the summary to notes.md",

	// U+0345 COMBINING GREEK YPOGEGRAMMENI, the artifactcheck-r2 finding
	// arriving here. This pattern sets (?i) and splices pytext's class
	// body, and Go expands a class's case-folds BEFORE negating: folding
	// \p{L} pulls in U+0345, whose fold orbit contains iota. Python's re
	// never folds a class, so CPython's [\w-] does NOT match it.
	//
	// The stem has to be U+0345 ALONE. \S* is greedy over anything
	// non-space, so a U+0345 anywhere else in the filename is simply eaten
	// by \S* and [\w-]+ still finds its ASCII character -- both engines
	// say True and the row proves nothing. Only when the class itself is
	// the sole candidate for the stem do the two answers part.
	"save the summary to ͅ.md",  // CPython False; unwrapped Go True
	"export it to ͅͅ.md",        // two of them, same verdict
	"save the summary to aͅ.md", // \S* eats it: False in both
	"save the summary to n.md",  // the ASCII control: True in both

	// A non-ASCII letter adjacent to a keyword, where Go's ASCII \b
	// fires and Python's does not.
	"füsave the summary to notes.md",
	"研究save the summary to notes.md",

	// THE r7 HIGH, and it is ASCII. Every case above has at least two
	// words between the keyword and "to", so the {0,40} window was never
	// asked to match ZERO characters — which is exactly what r6's
	// `\bsave\b[^.;\n]{0,40}\bto` translation could no longer do once
	// both `\b`s became consuming stand-ins. A differential that runs
	// python3, compares real values and passes can still be blind: the
	// blindness is in the FIXTURES, not the plumbing.
	"write to out.json",
	"save to notes.md",
	"export to data.csv",
	// One character of window, and two: the boundary either side of the
	// zero case, so an off-by-one in the {0,38} arithmetic shows.
	"write x to out.json",
	"write xy to out.json",
	// The CAP, either side of it. Python's window is 40 characters between
	// two ZERO-WIDTH boundaries; the Go composition spends one character on
	// each consuming stand-in, so the middle quantifier must be 38. Keeping
	// r6's 40 buys a 42-character window and matches text CPython does not
	// — and no fixture sat anywhere near the cap, so r7's battery restored
	// the 40 with every case still passing.
	"write " + strings.Repeat("x", 37) + " to out.json",
	"write " + strings.Repeat("x", 38) + " to out.json",
	"write " + strings.Repeat("x", 39) + " to out.json",
	"write " + strings.Repeat("x", 40) + " to out.json",
	"write " + strings.Repeat("x", 41) + " to out.json",
	"write " + strings.Repeat("x", 42) + " to out.json",

	// The `\S` half of the same class, found 2026-08-27 by censusing the
	// regex literals rather than by review. The comment above reasons FROM
	// `\S*` being "greedy over anything non-space" without ever asking
	// whether the two engines agree on which characters those are: Go's
	// `\S` is the complement of five code points, Python's the complement
	// of 29, so a separator inside the filename stem is eaten by Go's
	// greedy run and stops Python's. Direction is the REVERSE of r6 — Go
	// forces lane=agenda where CPython leaves it now.
	//
	// Both need the separator INSIDE the stem, after the `to`: the
	// SpaceClass+ ahead of it already agrees, so a leading one proves
	// nothing.
	"write to a" + nbspIntent + "b.md",
	"write to a" + vtabIntent + "b.md",
	// U+001C, which is `\s` to Python and not even close for Go, and needs
	// no Unicode to type.
	"write to a" + string(rune(0x1C)) + "b.md",
	// The ASCII control: an ordinary space in the same position, which
	// both engines treat as a separator and both answer False.
	"write to a b.md",
	// ...and the same stem with nothing between, which both answer True.
	"write to ab.md",

	// The ordinary lanes, ASCII, which already agreed.
	"write it to a file",
	"export results as csv files",
	"put it in artifacts/report.md",
	"summarize the article",
	"review the file handling code",
	"",
}

func TestRequiresFileOutputMatchesCPython(t *testing.T) {
	in, err := json.Marshal(fileOutputCorpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import intent\n"+
			"print(json.dumps([bool(intent._requires_file_output(m))\n"+
			"                  for m in json.loads(sys.argv[1])]))",
		string(in), srcDirIntent(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []bool
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var trues, falses, nonASCIITrue int
	for i, msg := range fileOutputCorpus {
		if got := RequiresFileOutput(msg); got != want[i] {
			t.Errorf("the file-deliverable OVERRIDE disagrees — one runtime "+
				"forces lane=agenda and the other does not\n  in %q\n  go %v\n  py %v",
				msg, got, want[i])
		}
		if want[i] {
			trues++
			if !isASCII(msg) {
				nonASCIITrue++
			}
		} else {
			falses++
		}
	}
	if trues == 0 || falses == 0 {
		t.Fatalf("corpus reaches only one answer: true=%d false=%d", trues, falses)
	}
	if nonASCIITrue == 0 {
		t.Fatal("no MATCHING case carries a non-ASCII letter: Go's ASCII \\w " +
			"and Python's Unicode one agree on everything else, so the " +
			"corpus cannot separate them")
	}
}

func isASCII(s string) bool {
	for _, r := range s {
		if r > 127 {
			return false
		}
	}
	return true
}

// Python's `_llm_classify` guards on `if data:`, and an empty dict is
// FALSY, so `{}` falls through to the heuristic. jsonx.Object returns a
// NON-NIL empty map for the same input and the `obj == nil` guard missed
// it, so a model reply of `{}` produced a well-formed agenda verdict at
// 0.7 here and ('now', 0.65, ...) there (adversarial mission-r6 MEDIUM).
func TestAnEmptyObjectFallsThroughLikeCPython(t *testing.T) {
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[1])\n"+
			"import intent\n"+
			"lane, conf, reason = intent._heuristic_classify('what is the time')\n"+
			"print(json.dumps([str(lane), conf, reason, bool({}), bool({'a':1})]))",
		srcDirIntent(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if want[3] != false || want[4] != true {
		t.Fatalf("the falsy-empty-dict premise has moved: bool({})=%v bool({a:1})=%v",
			want[3], want[4])
	}

	for _, reply := range []string{`{}`, `{ }`, "```json\n{}\n```"} {
		res, ok := llmClassify(context.Background(), classifyStub{reply}, "what is the time")
		if ok {
			t.Errorf("an empty object must NOT classify (Python's `if data:` "+
				"is falsy): reply %q gave %+v", reply, res)
		}
	}

	// ...and the whole-path consequence: Classify falls through to the
	// heuristic and lands in CPython's lane.
	r := Classify(context.Background(), classifyStub{`{}`}, "what is the time", false)
	if r.Lane != want[0].(string) {
		t.Errorf("LANE after an empty reply: go %q py %q", r.Lane, want[0])
	}
}

// The r5 heuristic corpus reached the SEPARATOR half of the divergence
// (U+001C..U+001F in strip/split) but had no case whose non-ASCII
// content is a LETTER, which is what the \b/\w half needs. This asserts
// the corpus can still reach it, so a future trim cannot quietly make
// TestHeuristicClassifyMatchesCPython unable to fail.
func TestHeuristicCorpusReachesTheUnicodeBoundary(t *testing.T) {
	var letters int
	for _, c := range heuristicCorpus {
		for _, r := range c.msg {
			if r > 127 && !isSeparatorish(r) {
				letters++
				break
			}
		}
	}
	if letters == 0 {
		t.Fatal("the heuristic corpus has no case whose non-ASCII content is " +
			"a LETTER rather than a separator, so it cannot reach the " +
			"\\b/\\w divergence")
	}
}

func isSeparatorish(r rune) bool {
	switch r {
	case 0x00a0, 0x1680, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005,
		0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x2028, 0x2029, 0x202f,
		0x205f, 0x3000, 0x0085:
		return true
	}
	return false
}
