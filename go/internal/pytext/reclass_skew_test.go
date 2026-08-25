package pytext

import (
	"encoding/json"
	"regexp"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pythonClassMembers returns every code point CPython's `re` matches with
// the given one-character class pattern.
func pythonClassMembers(t *testing.T, pat string) map[rune]bool {
	t.Helper()
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import json,re,sys;p=re.compile(sys.argv[1]);"+
			"print(json.dumps([c for c in range(0x110000) "+
			"if p.fullmatch(chr(c))]))", pat))
	var cs []int
	if err := json.Unmarshal(out, &cs); err != nil {
		t.Fatalf("decoding CPython output: %v", err)
	}
	if len(cs) == 0 {
		t.Fatalf("CPython's %s matches nothing; the probe is broken", pat)
	}
	m := make(map[rune]bool, len(cs))
	for _, c := range cs {
		m[rune(c)] = true
	}
	return m
}

// The sweep, both directions, for both classes. A code point Python
// matches and we do not is as much a failure as the reverse: the first
// makes Go miss an alarm it should replace, the second makes Go rewrite
// an entry Python would have left alone.
func TestTheRegexClassesMatchCPythonAtEveryCodePoint(t *testing.T) {
	for _, tc := range []struct {
		name    string
		pyPat   string
		goClass string
	}{
		{`\s`, `\s`, SpaceClass},
		{`\d`, `\d`, DigitClass},
	} {
		t.Run(tc.name, func(t *testing.T) {
			want := pythonClassMembers(t, tc.pyPat)
			re := regexp.MustCompile(`^` + tc.goClass + `$`)
			var missed, extra, checked int
			for c := 0; c < 0x110000; c++ {
				r := rune(c)
				if !utf8Valid(r) {
					continue
				}
				checked++
				got := re.MatchString(string(r))
				if got == want[r] {
					continue
				}
				if want[r] {
					missed++
					if missed <= 8 {
						t.Errorf("CPython's %s matches U+%04X and %s does not",
							tc.pyPat, c, tc.name)
					}
				} else {
					extra++
					if extra <= 8 {
						t.Errorf("%s matches U+%04X and CPython's %s does not",
							tc.name, c, tc.pyPat)
					}
				}
			}
			if checked < 0x100000 {
				t.Fatalf("only %d code points reached the comparison; the "+
					"sweep is not sweeping", checked)
			}
			if missed+extra > 0 {
				t.Fatalf("%s: %d missed, %d extra of %d", tc.name,
					missed, extra, checked)
			}
			t.Logf("%s: %d code points, agreed at all %d checked",
				tc.name, len(want), checked)
		})
	}
}

// The digit supplement is a snapshot of a version skew, so it goes inert
// when Go's tables catch up — and an inert supplement reads as coverage.
// This fails when Go's own \p{Nd} already covers a supplemented range, so
// the literal is DELETED rather than left to rot.
func TestTheDigitSupplementIsStillCarryingWeight(t *testing.T) {
	goNd := regexp.MustCompile(`^\p{Nd}$`)
	supp := regexp.MustCompile(`^[` + digitSupplementBody + `]$`)
	var covered, total int
	for c := 0; c < 0x110000; c++ {
		r := rune(c)
		if !utf8Valid(r) || !supp.MatchString(string(r)) {
			continue
		}
		total++
		if goNd.MatchString(string(r)) {
			covered++
		}
	}
	if total == 0 {
		t.Fatal("the supplement matches nothing; this detector is dead")
	}
	if covered > 0 {
		t.Errorf("Go's \\p{Nd} now covers %d of the %d supplemented code "+
			"points — trim digitSupplementBody to what is still missing",
			covered, total)
	}
	t.Logf("supplement carries %d code points Go's \\p{Nd} lacks", total)
}

// The class is only useful if it is actually different from what a
// transcribed Python pattern would compile to in Go. If Go ever adopts
// Python's reading of \s and \d, these classes become dead weight and
// this says so instead of leaving them in place forever.
func TestTheGoDefaultsAreStillTheWrongAnswer(t *testing.T) {
	for _, tc := range []struct{ name, goDefault, ours string }{
		{`\s`, `\s`, SpaceClass},
		{`\d`, `\d`, DigitClass},
	} {
		def := regexp.MustCompile(`^` + tc.goDefault + `$`)
		ours := regexp.MustCompile(`^` + tc.ours + `$`)
		var differ int
		for c := 0; c < 0x110000; c++ {
			r := rune(c)
			if utf8Valid(r) && def.MatchString(string(r)) != ours.MatchString(string(r)) {
				differ++
			}
		}
		if differ == 0 {
			t.Errorf("Go's own %s now agrees with ours at every code point; "+
				"the class is dead weight and should be deleted", tc.name)
		}
		t.Logf("%s: ours differs from Go's default at %d code points",
			tc.name, differ)
	}
}

// NotClass splices its argument into a character class, so the escaping
// is load-bearing: an unescaped `]` would end the class early and compile
// a different pattern that still compiles.
func TestNotClassExcludesWhitespaceAndItsExtras(t *testing.T) {
	re := regexp.MustCompile(`^` + NotClass(")·") + `+$`)
	for _, tc := range []struct {
		in   string
		want bool
	}{
		{"cost:observation", true},
		{"a-b", true},
		{"a]b", true},
		{"a b", false}, // ASCII space
		{"a b", false}, // NBSP — in Python's \s, not in Go's
		{"ab", false}, // file separator — the four that differ
		{"a)b", false}, // explicit extra
		{"a·b", false}, // the middle dot the alarm syntax uses
		{" ", false},   // line separator
	} {
		if got := re.MatchString(tc.in); got != tc.want {
			t.Errorf("NotClass(%q) on %q = %v, want %v", ")·", tc.in, got, tc.want)
		}
	}
	// Escaping check: the class must not be terminable from the argument.
	if !regexp.MustCompile(`^` + NotClass("]") + `+$`).MatchString("ab") {
		t.Error("NotClass(\"]\") did not compile to a class that matches " +
			"ordinary text; the ] escaped the class")
	}
}
