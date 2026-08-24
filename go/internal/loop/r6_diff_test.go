package loop

import (
	"crypto/md5"
	"encoding/json"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// TestErrorFingerprintPythonParity used to live in exec_test.go with two
// md5 constants "computed with CPython". It could not fail on either of
// the two heads this file's fix closes: its corpus was
// strings.Repeat("x", 300) and ASCII prose, where a byte slice and a
// rune slice are the same slice and strings.Fields and str.split() are
// the same split. Its third assertion compared a pure function's output
// against itself, which no implementation can fail (adversarial
// mission-r6, LENS 1 heads 1, 3 and 4 in one test).
//
// This is the differential that replaces it. The fingerprints are
// written into the captain's-log METACOGNITIVE_DECISION event and read
// back by isConverging to choose retry vs split vs replan, so a fork is
// both a byte difference and a control-flow difference.

func srcDirLoop(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestErrorFingerprintMatchesCPython(t *testing.T) {
	type fpCase struct {
		Name   string `json:"-"`
		Reason string `json:"stuck_reason"`
		Result string `json:"result"`
	}
	long := ""
	for i := 0; i < 300; i++ {
		long += "x"
	}
	wide := ""
	for i := 0; i < 120; i++ {
		wide += "é"
	}
	cases := []fpCase{
		// THE split head: str.split() reads 29 code points where
		// strings.Fields reads 25, so a captured stderr carrying an
		// information separator normalises differently.
		{"a file separator in the reason", "pytest failed\u001cassert x", "out"},
		{"the same text with a space", "pytest failed assert x", "out"},
		{"a unit separator in the result", "pytest failed", "assert\u001ffailed"},
		{"a record separator", "boom\u001ehere", "out"},
		// THE slice head: [:200] is 200 CODE POINTS in Python and was
		// 200 BYTES here, so any multi-byte character before the cut
		// moved it.
		{"120 accented characters", wide, "out"},
		{"an accented tail past the cut", wide + " tail", "out"},
		// The ASCII lane the old test had, which already agreed.
		{"the ordinary shape", "failed to fetch: connection refused", "partial output text"},
		{"a long ASCII reason", long + "   tail", ""},
		{"empty", "", ""},
		{"whitespace only", "   ", "  "},
		{"a non-breaking space", "pytest\u00a0failed", "out"},
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"sys.path.insert(0, sys.argv[2])\n"+
			"import loop_blocked as lb\n"+
			"print(json.dumps([lb._error_fingerprint(c)\n"+
			"                  for c in json.loads(sys.argv[1])]))",
		string(in), srcDirLoop(t)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var splitHead, sliceHead int
	for i, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			if got := errorFingerprint(c.Reason, c.Result); got != want[i] {
				t.Errorf("the CONVERGENCE fingerprint diverges — one runtime "+
					"declares the error unchanged and the other calls it "+
					"progress\n  reason %q\n  result %q\n  go %s\n  py %s",
					c.Reason, c.Result, got, want[i])
			}
		})
		for _, r := range c.Reason + c.Result {
			if r >= 0x1c && r <= 0x1f {
				splitHead++
				break
			}
		}
		// A byte cut and a rune cut differ exactly when the normalised
		// text is multi-byte AND longer than 200 bytes.
		norm := strings.Join(strings.Fields(c.Reason), " ")
		if len(norm) > 200 && len(norm) != len([]rune(norm)) {
			sliceHead++
		}
	}
	if splitHead == 0 {
		t.Fatal("no case carries U+001C..U+001F: strings.Fields and " +
			"str.split() agree on everything else, so the split head is unpinned")
	}
	if sliceHead == 0 {
		t.Fatal("no case is multi-byte and long enough to move a 200-BYTE " +
			"cut off a 200-RUNE one: the slice head is unpinned")
	}

	// The old test's third assertion was errorFingerprint("a","b") ==
	// errorFingerprint("a","b"), which is a pure function compared with
	// itself. The property actually worth asserting is that DIFFERENT
	// failures differ, which is what isConverging reads.
	if errorFingerprint("a", "b") == errorFingerprint("a", "c") {
		t.Error("two different failures fingerprint identically — " +
			"isConverging would call a changing error a stuck one")
	}

	// The anti-vacuity guards above count fixtures; this one proves the
	// corpus can actually SEPARATE the old implementation from the new,
	// by running the old one over it. Counting the right shape of
	// fixture and having a fixture that discriminates are not the same
	// claim, and mission-r5's battery was misled by exactly that gap.
	var oldDiffers int
	for i, c := range cases {
		if oldErrorFingerprint(c.Reason, c.Result) != want[i] {
			oldDiffers++
		}
	}
	if oldDiffers < 2 {
		t.Fatalf("the pre-fix implementation matches CPython on all but %d "+
			"of %d cases: this corpus could not have caught the finding",
			oldDiffers, len(cases))
	}
	t.Logf("corpus separates the pre-fix implementation on %d of %d cases",
		oldDiffers, len(cases))
}

// oldErrorFingerprint is the implementation mission-r6 replaced —
// strings.Fields and a BYTE slice. It exists only so the test above can
// prove its own corpus discriminates, and must never return to
// production.
func oldErrorFingerprint(stuckReason, result string) string {
	norm := func(s string) string {
		s = strings.Join(strings.Fields(s), " ")
		if len(s) > 200 {
			s = s[:200]
		}
		return s
	}
	sum := md5.Sum([]byte(norm(stuckReason) + "|" + norm(result)))
	return fmt.Sprintf("%x", sum)[:12]
}
