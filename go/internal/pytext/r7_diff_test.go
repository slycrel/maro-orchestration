package pytext

import (
	"encoding/json"
	"os/exec"
	"regexp"
	"strings"
	"testing"
)

// compositionCorpus exercises the WINDOW between two boundaries, which
// is the shape r6's translation could not express. Every case in r6's own
// boundary corpus put the boundary at an END of the pattern — the only
// position where a consuming stand-in is safe — so it could not have
// separated on this.
var compositionCorpus = []string{
	// Zero window: the keyword and "to" adjacent. `\b[^.;\n]{0,40}\b`
	// matches the empty string here in Python; two consuming stand-ins
	// need at least two characters and matched nothing.
	"write to out.json",
	"save to notes.md",
	// One and two characters of window — either side of the boundary the
	// {0,38} arithmetic turns on.
	"write x to out.json",
	"write xy to out.json",
	"write xyz to out.json",
	// The cap itself: 40 characters of window, and 41.
	"write " + strings.Repeat("x", 39) + " to out.json",
	"write " + strings.Repeat("x", 40) + " to out.json",
	// A `.` and a `;` inside the window, which the class excludes.
	"write a. b to out.json",
	"write a; b to out.json",
	// Non-ASCII either side of each boundary.
	"研究write to out.json",
	"write 研究 to out.json",
	"write toほ out.json",
	"writeも to out.json",
	// No match at all.
	"read the file", "",
}

// TestBoundedWindowTranslationMatchesCPython pins the translation recipe
// WordStart/WordEnd's doc prescribes for an INTERIOR boundary:
//
//	Python  \bwrite\b[^.;\n]{0,40}\bto
//	RE2     WordStart write (?:NW|NW [^.;\n]{0,38} NW) to
//
// The arithmetic is the whole point — the two NotWordClassPlus runs
// CONSUME one character each, so the middle quantifier drops from 40 to
// 38, and the bare-NW alternative covers the one- and zero-character
// windows the paired form cannot reach. r6 wrote `WordStart write ...
// WordEnd to` instead, which silently required a two-character window
// and made intent._requires_file_output("write to out.json") false in Go
// and true in CPython (adversarial mission-r7 HIGH).
func TestBoundedWindowTranslationMatchesCPython(t *testing.T) {
	in, err := json.Marshal(compositionCorpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,re,sys\n"+
			"p = re.compile(r'\\bwrite\\b[^.;\\n]{0,40}\\bto')\n"+
			"print(json.dumps([bool(p.search(s)) for s in json.loads(sys.argv[1])]))",
		string(in)).Output()
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

	nw := NotWordClassPlus(".;\n")
	good := regexp.MustCompile(WordStart + `write` +
		`(?:` + nw + `|` + nw + `[^.;` + "\n" + `]{0,38}` + nw + `)` + `to`)
	// The PRE-FIX spelling, kept in the test so the corpus is proved to
	// discriminate rather than assumed to.
	bad := regexp.MustCompile(WordStart + `write` + `[^.;` + "\n" + `]{0,40}` +
		WordEnd + `to`)

	badLost := 0
	for i, s := range compositionCorpus {
		if bad.MatchString(s) != want[i] {
			badLost++
		}
	}
	if badLost == 0 {
		t.Fatal("r6's spelling agrees with CPython on every case here: " +
			"this corpus could not have caught the finding")
	}

	for i, s := range compositionCorpus {
		if got := good.MatchString(s); got != want[i] {
			t.Errorf("composed window on %q = %v, want CPython %v", s, got, want[i])
		}
	}
}

// TestNotWordClassPlusMatchesPythonsNegatedClass: the helper builds
// `[^\w.;\n]` the way Python's re reads it — a negated class whose \w is
// UNICODE. Go's own `[^\w.;\n]` is the complement of ASCII word
// characters, so it accepts every non-ASCII letter as a "non-word"
// character and the window walks straight through text CPython stops at.
func TestNotWordClassPlusMatchesPythonsNegatedClass(t *testing.T) {
	corpus := []string{
		"a", "研", "ü", "9", "_", ".", ";", "\n", " ", "-", " ", "é",
	}
	in, err := json.Marshal(corpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,re,sys\n"+
			"p = re.compile(r'^[^\\w.;\\n]$')\n"+
			"print(json.dumps([bool(p.match(s)) for s in json.loads(sys.argv[1])]))",
		string(in)).Output()
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
	good := regexp.MustCompile(`^` + NotWordClassPlus(".;\n") + `$`)
	bad := regexp.MustCompile(`^[^\w.;` + "\n" + `]$`)

	badLost := 0
	for i, s := range corpus {
		if bad.MatchString(s) != want[i] {
			badLost++
		}
	}
	if badLost == 0 {
		t.Fatal("Go's own [^\\w.;\\n] agrees with CPython on every case " +
			"here: this corpus could not have caught the finding")
	}
	for i, s := range corpus {
		if got := good.MatchString(s); got != want[i] {
			t.Errorf("NotWordClassPlus on %q = %v, want CPython %v", s, got, want[i])
		}
	}
}
