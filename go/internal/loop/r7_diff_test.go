package loop

import (
	"encoding/json"
	"os/exec"
	"regexp"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// splitCorpus reaches both branches of the rebuilt bareAndSep and the
// two `;`/`and then` separators beside them.
var splitCorpus = []string{
	"do the thing and Then check it",
	"do the thing and check it",
	"build the app and Deploy it",
	"build the app; Deploy it",
	"build the app and then deploy it",
	// The finding: a non-ASCII letter immediately before "and". Python's
	// `\b` does not fire between 研 and 'a'; Go's ASCII one did, so the
	// two runtimes produced a DIFFERENT NUMBER of sub-steps for the same
	// stuck-step text — and that count is what a split-and-retry
	// re-plans from (adversarial mission-r7 LOW).
	"研究and Deploy it",
	"fürand Deploy it",
	// The ASCII controls: a word character before "and" (no split in
	// either) and a punctuation one (split in both).
	"xand Deploy it",
	"x)and Deploy it",
	"(and Deploy it",
	// Leading position, and no trailing space before the capital.
	"and Deploy it",
	"do it andDeploy it",
	// Multiple separators, and none.
	"a and Bee and Cee",
	"just one step",
	"",
}

// TestHeuristicSplitPartsMatchesCPython drives Python's own re.split for
// loop_blocked._generate_timeout_split's fallback. The Go emulation
// hand-rolls the `(?=[A-Z])` lookahead RE2 does not have, so the boundary
// SET is the thing to compare, and until r7 nothing tested it at all.
func TestHeuristicSplitPartsMatchesCPython(t *testing.T) {
	in, err := json.Marshal(splitCorpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,re,sys\n"+
			"p = r'\\s*;\\s*|\\s+and\\s+then\\s+|\\s*\\band\\b\\s*(?=[A-Z])'\n"+
			"print(json.dumps([re.split(p, s) for s in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][]string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	// Anti-vacuity: r6's spelling — Go's ASCII `\b`, whole-match offsets —
	// replayed over the same corpus and required to lose.
	oldLost := 0
	for i, s := range splitCorpus {
		if !sameStrings(oldSplitParts(s), want[i]) {
			oldLost++
		}
	}
	if oldLost == 0 {
		t.Fatal("the pre-fix separator splits this corpus exactly as CPython " +
			"does: these fixtures could not have caught the finding")
	}

	for i, s := range splitCorpus {
		if got := heuristicSplitParts(s); !sameStrings(got, want[i]) {
			t.Errorf("split(%q):\n go %q\n py %q", s, got, want[i])
		}
	}
}

// oldBareAndSep is r6's spelling, kept in the test so the corpus above is
// PROVED to discriminate rather than assumed to.
var oldBareAndSep = regexp.MustCompile(
	pytext.SpaceClass + `*\band\b` + pytext.SpaceClass + `*`)

func oldSplitParts(stepText string) []string {
	var parts []string
	for _, seg := range timeoutSplitSeps.Split(stepText, -1) {
		start := 0
		for _, lc := range oldBareAndSep.FindAllStringIndex(seg, -1) {
			rest := seg[lc[1]:]
			if rest != "" && rest[0] >= 'A' && rest[0] <= 'Z' {
				parts = append(parts, seg[start:lc[0]])
				start = lc[1]
			}
		}
		parts = append(parts, seg[start:])
	}
	return parts
}

func sameStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
