package persona

import (
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// This file closes what a never-mutated-FUNCTION sweep found.
//
// The earlier rounds mutated the code the differentials already drove, and 57
// of 60 mutants died. This sweep asked a different question: which helpers had
// nothing ever mutate them at all, where "never checked" and "guarded" look
// identical from the outside. Fourteen mutants went in; the two closed below
// are the ones that were real.
//
// Half the sweep's "survivors" were the sweep's own fault, and that is worth
// more than the finds. Three mutants were no-ops dressed as behaviour changes
// -- `if false { return nil }` in a function that already returned early, and
// an isSpaceStr mutant written as `!IsSpace(r) && r != NBSP`, which for NBSP
// evaluates false and so still treats it as whitespace. Each looked like an
// uncovered helper and was not. A mutation sweep needs its own mutants
// checked: a mutant that changes nothing reports a gap that is not there, and
// costs exactly as much to chase as a real one.

// TestCopyListDoesNotAliasItsSource pins the property copyList's doc comment
// claims: Python's `list(x)` builds a NEW list, so nothing downstream can
// reach back through the result and edit what it was built from.
//
// Nothing in this package mutates one of these slices today, which is exactly
// why the property was unmeasured -- measured: a copyList that returned its
// argument unchanged passed the entire suite. This is a direct assertion
// rather than a differential because `list()` copying is not a question about
// CPython; the only question is whether the port kept the guarantee.
func TestCopyListDoesNotAliasItsSource(t *testing.T) {
	src := []any{"a", "b"}
	out := copyList(src)
	if len(out) != 2 || out[0] != "a" || out[1] != "b" {
		t.Fatalf("copyList lost the contents: %#v", out)
	}
	out[0] = "MUTATED"
	if src[0] != "a" {
		t.Fatalf("copyList aliased its source: writing to the result changed "+
			"the input to %#v", src)
	}
	// The other direction too, which a copy-on-read implementation would miss.
	src[1] = "ALSO MUTATED"
	if out[1] != "b" {
		t.Fatalf("copyList result tracks later writes to its source: %#v", out)
	}
}

// TestRepoDirSegmentMatchesCPython pins the one thing RepoDir decides.
//
// RepoDir had NO test and no in-tree caller yet, so its directory name was
// free: measured, renaming "personas" to "persona" passed the entire suite.
// The name is a shared on-disk surface -- Python's PersonaRegistry reads the
// same directory -- so it is measured against the segment CPython joins
// rather than typed a second time here.
func TestRepoDirSegmentMatchesCPython(t *testing.T) {
	const root = "/tmp/does-not-exist-repo-root"
	var py struct {
		Segment string `json:"segment"`
		Full    string `json:"full"`
	}
	personaProbe(t).RunJSON(t, `
import json, sys
from pathlib import Path
root = Path(json.loads(sys.argv[1]))
# The expression PersonaRegistry.__init__ uses for the repo tier.
candidate = root / "personas"
print(json.dumps({"segment": candidate.name, "full": str(candidate)}))
`, &py, pyprobe.Arg(t, root))

	// CLAIM first, so a probe that stopped exercising this fails loudly
	// instead of agreeing with a port that drifted the same way.
	if py.Segment != "personas" {
		t.Fatalf("CLAIM moved: CPython's repo persona segment is %q", py.Segment)
	}
	got := RepoDir(root)
	if got != py.Full {
		t.Fatalf("RepoDir answered %q, CPython builds %q", got, py.Full)
	}
	if filepath.Base(got) != py.Segment {
		t.Fatalf("RepoDir segment %q != CPython's %q", filepath.Base(got), py.Segment)
	}
}

// EQUIVALENT AND UNREACHABLE MUTANTS from the same sweep, recorded so the
// next person to run one does not re-derive them as findings:
//
//   - isSpaceStr("") answering true instead of false. dedent's margin filter
//     short-circuits on `l == ""` before calling it, and its output loop
//     reaches the same "" either way (an empty line has margin >= len(rs)).
//     The statement is kept because it is Python's `str.isspace()`, whose
//     answer for "" is False, and a later caller could ask it directly.
//
//   - runesLess reversed, so dedent's l1/l2 become max/min instead of
//     min/max. The margin loop breaks on `c != l2[i] || c not in " \t"`,
//     whose first clause is symmetric, and whitespace-only lines are FILTERED
//     OUT before the scan -- so l1 always contains a non-space and the loop
//     always breaks at or before it. That same filter is what keeps `l2[i]`
//     in range when l1 is the longer string. Confirmed by measurement, not
//     just by the argument: the mutant survives the whole package.
//
//   - runesLess's `len(a) < len(b)` tie-break widened to `<=`. The two differ
//     only when a and b are identical, and both of dedent's uses then assign
//     a value equal to the one already there.
//
//   - clipRunes's `len(rs) <= n` narrowed to `<`. At exactly n runes,
//     string(rs[:n]) IS s.
//
//   - dedent's `margin >= len(rs)` narrowed to `>`. Equality needs a NON-BLANK
//     line whose length equals the margin, but the margin counts only leading
//     space/tab shared by every non-blank line -- a line that short would be
//     all whitespace, and blank lines never reach the branch. The empty line
//     does reach it and answers "" under both spellings.
//
// One thing the sweep DID produce here and this file deliberately does not
// keep: an adversarial dedent table (unicode blanks, C1 separators, mixed
// tabs, differing indents) was written, measured against the existing
// TestDedentMatchesCPython mutant-for-mutant, and DELETED. Over seven dedent
// mutations it caught nothing the existing table did not, and MISSED one the
// existing table catches (never updating l1). Keeping it would have added a
// second guard whose only real function was to look thorough.
