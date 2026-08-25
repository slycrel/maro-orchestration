package pytext

import (
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyFnMatchSrc answers fnmatchcase for every (name, pattern) pair. It uses
// fnmatchcase rather than fnmatch because fnmatch normalises case through
// os.path.normcase, which is identity on POSIX and would silently make this
// test platform-dependent.
const pyFnMatchSrc = `
import fnmatch, json, sys

out = []
for name, pat in json.loads(sys.argv[1]):
    out.append(fnmatch.fnmatchcase(name, pat))
print(json.dumps(out))
`

// TestFnMatchMatchesCPython pins the matcher against CPython over a CROSS
// PRODUCT rather than a hand-picked list.
//
// Hand-picked tables are how a matcher passes while disagreeing on the
// cases nobody thought of; the pairs here are every name against every
// pattern, so the table's coverage does not depend on my imagination about
// which combinations are interesting. The names and patterns are chosen to
// concentrate on the four places Go and Python disagree: the backslash, an
// unclosed bracket, a `]` in first position, and a `-` at either end of a
// class.
func TestFnMatchMatchesCPython(t *testing.T) {
	names := []string{
		"", "a", "ab", "abc", "axb", "a[b]", "a*b", "a?b", "a-b", "a]b",
		`a\b`, `a\`, "[", "]", "-", "A", "aB", "a.b", "aa", "aaa",
		"skill_x", "skill_x_20260101.json", "café", "日本",
	}
	patterns := []string{
		"", "a", "*", "?", "a*", "*a", "a*b", "a?b", "??", "***",
		"[abc]", "[!abc]", "[a-c]", "[!a-c]", "[]a]", "[!]a]", "[-a]",
		"[a-]", "[]]", "[!]]", "[", "a[b", "]", "a]b", "[[]",
		`a\b`, `a\*b`, `\`, "*_*.json", "skill_x_*.json",
		"[A-Za-z]*", "caf?", "日*",
	}

	var pairs [][2]string
	for _, n := range names {
		for _, p := range patterns {
			pairs = append(pairs, [2]string{n, p})
		}
	}

	var want []bool
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyFnMatchSrc, &want,
		pyprobe.Arg(t, pairs))
	if len(want) != len(pairs) {
		t.Fatalf("CPython answered %d of %d pairs", len(want), len(pairs))
	}

	// Anti-vacuity. A matcher that answered false for everything would
	// agree with a table dominated by non-matches, and one that answered
	// true for everything would agree with the opposite. Both halves have
	// to be substantial before the comparison below means anything.
	trues := 0
	for _, w := range want {
		if w {
			trues++
		}
	}
	if trues < len(want)/10 || trues > len(want)*9/10 {
		t.Fatalf("CPython answered true %d of %d times; the corpus is too "+
			"one-sided to distinguish a real matcher from a constant",
			trues, len(want))
	}

	bad := 0
	for i, pr := range pairs {
		got := FnMatch(pr[0], pr[1])
		if got != want[i] {
			bad++
			if bad <= 20 {
				t.Errorf("FnMatch(%q, %q) = %v, CPython says %v",
					pr[0], pr[1], got, want[i])
			}
		}
	}
	if bad > 20 {
		t.Errorf("... and %d more disagreements", bad-20)
	}
}
