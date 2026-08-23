package loop

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A SOURCE-LEVEL tripwire, deliberately, and the only kind available here.
//
// Python guarantees run-verdict skill attribution structurally:
// stamp_outcome_verdict ends by calling the attributor, so a caller cannot
// stamp without crediting. Go's import graph forbids that — internal/skills
// imports internal/record, so record cannot call skills — and the
// composition had to move up into skills.StampVerdictWithAttribution, which
// this package calls.
//
// That leaves record.StampOutcomeVerdict callable directly, and calling it
// directly is exactly the bug adversarial r5 found: both ends of attribution
// were live, nothing joined them, injected_runs sat at 0 forever, and the
// two gates behind it (the promotion veto and the A/B frontier) both failed
// OPEN with nothing anywhere saying so. It is the same shape as the dead
// use_count gate PORT.md records, and prose in a comment did not prevent
// that one either.
//
// So: a test that reads this package's own source. It is coarse and it is
// the honest instrument for the property — "no caller in this package uses
// the raw primitive" is a fact about the source, not about any single run.
func TestTheLoopNeverStampsAVerdictWithoutAttributingIt(t *testing.T) {
	entries, err := os.ReadDir(".")
	if err != nil {
		t.Fatal(err)
	}
	checked, found := 0, false
	for _, e := range entries {
		name := e.Name()
		if !strings.HasSuffix(name, ".go") || strings.HasSuffix(name, "_test.go") {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(".", name))
		if err != nil {
			t.Fatal(err)
		}
		checked++
		src := string(raw)
		if strings.Contains(src, ".StampOutcomeVerdict(") {
			t.Errorf("%s calls record.StampOutcomeVerdict directly. Use "+
				"skills.StampVerdictWithAttribution: the bare primitive stamps "+
				"the row and credits nothing, which leaves injected_runs at 0 "+
				"and every gate behind it failing open (adversarial r5, H1).", name)
		}
		if strings.Contains(src, "skills.StampVerdictWithAttribution(") {
			found = true
		}
	}
	// Without this the tripwire passes vacuously the moment the call is
	// renamed, moved or deleted — a guard that cannot fail is worse than
	// no guard.
	if checked == 0 {
		t.Fatal("the walk read no source files; this tripwire is dead")
	}
	if !found {
		t.Error("no file in this package calls skills.StampVerdictWithAttribution " +
			"— the composed stamp is not wired, so no verdict credits any skill")
	}
}
