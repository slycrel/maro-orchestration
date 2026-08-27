package pytext

import (
	"encoding/json"
	"regexp"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The claim IClass rests on is not "the Turkish i is special" — it is that
// the Turkish i is the ONLY thing special, for every ASCII letter and
// digit a ported pattern can contain. That is a claim about 36 characters
// times 1,114,112 code points, and it is checked here rather than
// asserted, because the port has twice now filed this class as having no
// remedy on the strength of a reading.
//
// Both directions matter. A code point CPython folds and Go does not makes
// the port MISS a match CPython makes (which is how `wrote İnto
// absent.txt` walked past the fabrication detector — the write claim was
// never extracted, so nothing was checked: a detector failing OPEN). A
// code point Go folds and CPython does not makes the port fire where
// CPython stays quiet.
func TestIGNORECASEDivergesOnlyAtTheLetterI(t *testing.T) {
	const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"

	// CPython's answer, measured, one probe for the whole alphabet: 36
	// interpreter spawns buys nothing over one.
	const src = `
import json, re, sys
out = {}
for ch in sys.argv[1]:
    p = re.compile(ch, re.IGNORECASE)
    out[ch] = [c for c in range(0x110000) if p.fullmatch(chr(c))]
print(json.dumps(out))
`
	var py map[string][]int
	raw := pyprobe.Probe{Stdlib: true}.Run(t, src, alphabet)
	if err := json.Unmarshal([]byte(raw), &py); err != nil {
		t.Fatalf("probe output was not JSON: %v", err)
	}
	if len(py) != len(alphabet) {
		t.Fatalf("the probe answered for %d characters, not %d — the sweep "+
			"below would be measuring a subset without saying so",
			len(py), len(alphabet))
	}

	// Stated before the comparison runs: `i` is the only character whose
	// two engines disagree, and it disagrees by exactly these two code
	// points. If CPython's own table ever stops matching this, every
	// conclusion below it is unmeasured.
	wantExtra := map[rune][]rune{
		'i': {0x0130, 0x0131},
	}

	for _, ch := range alphabet {
		pyset := map[rune]bool{}
		for _, c := range py[string(ch)] {
			pyset[rune(c)] = true
		}
		// A letter has two cases; a digit has one. Both floors are stated,
		// because "at least one" would let a letter whose probe answered
		// with a single case through, and that is a broken probe rather
		// than a finding.
		floor := 2
		if ch >= '0' && ch <= '9' {
			floor = 1
		}
		if len(pyset) < floor {
			t.Fatalf("CPython's IGNORECASE set for %q has %d member(s), "+
				"fewer than the %d it must have", ch, len(pyset), floor)
		}

		goRE := regexp.MustCompile(`(?i)^` + string(ch) + `$`)
		var onlyPy, onlyGo []rune
		for c := rune(0); c <= 0x10FFFF; c++ {
			if c >= 0xD800 && c <= 0xDFFF {
				continue
			}
			g := goRE.MatchString(string(c))
			p := pyset[c]
			switch {
			case p && !g:
				onlyPy = append(onlyPy, c)
			case g && !p:
				onlyGo = append(onlyGo, c)
			}
		}
		if len(onlyGo) > 0 {
			t.Errorf("%q: Go's (?i) matches %d code point(s) CPython's does "+
				"not: %s — the port would fire where CPython stays quiet",
				ch, len(onlyGo), fmtRunes(onlyGo))
		}
		want := wantExtra[ch]
		if !sameRunes(onlyPy, want) {
			t.Errorf("%q: CPython's IGNORECASE reaches %s where Go's reaches "+
				"%s — this test's premise says the gap here is %s",
				ch, fmtRunes(onlyPy), "nothing extra", fmtRunes(want))
		}

		// ...and IClass closes the gap it just measured, for real.
		if len(want) == 0 {
			continue
		}
		folded := regexp.MustCompile(`(?i)^` + FoldI(string(ch)) + `$`)
		for c := rune(0); c <= 0x10FFFF; c++ {
			if c >= 0xD800 && c <= 0xDFFF {
				continue
			}
			if folded.MatchString(string(c)) != pyset[c] {
				t.Fatalf("FoldI(%q) disagrees with CPython at U+%04X: go=%v "+
					"py=%v", ch, c, !pyset[c], pyset[c])
			}
		}
	}
}

// FoldI is spelled `(?-i:...)`, so it must mean the same thing in a
// pattern that sets (?i) and one that does not. A class spliced into an
// ignore-case pattern can fold-GROW in Go (see WordClass and U+0345),
// which is the failure this spelling exists to prevent — so it is checked
// rather than trusted.
func TestFoldIMeansTheSameWithAndWithoutIgnoreCase(t *testing.T) {
	with := regexp.MustCompile(`(?i)^` + FoldI("file") + `$`)
	without := regexp.MustCompile(`^` + FoldI("file") + `$`)
	hits := 0
	for c := rune(0); c <= 0x10FFFF; c++ {
		if c >= 0xD800 && c <= 0xDFFF {
			continue
		}
		s := "f" + string(c) + "le"
		a, b := with.MatchString(s), without.MatchString(s)
		if a != b {
			t.Fatalf("U+%04X: (?i) changes what FoldI matches — with=%v "+
				"without=%v", c, a, b)
		}
		if a {
			hits++
		}
	}
	// The four CPython matches for `i`, and nothing else. A sweep that
	// found none would agree vacuously.
	if hits != 4 {
		t.Fatalf("FoldI(\"file\") matched %d code points in the i position, "+
			"not the 4 CPython's IGNORECASE reaches", hits)
	}
	// The rest of the word must stay case-insensitive: FoldI only touches
	// the i, and a caller relying on (?i) for the other letters would be
	// silently broken if it did more.
	if !with.MatchString("F" + "i" + "LE") {
		t.Fatal("FoldI turned off case folding for the letters it did not " +
			"rewrite")
	}
	if without.MatchString("F" + "i" + "LE") {
		t.Fatal("FoldI is folding the letters it did not rewrite, which " +
			"means it is not the identity outside the i position")
	}
}

func sameRunes(a, b []rune) bool {
	if len(a) != len(b) {
		return false
	}
	seen := map[rune]bool{}
	for _, r := range a {
		seen[r] = true
	}
	for _, r := range b {
		if !seen[r] {
			return false
		}
	}
	return true
}

func fmtRunes(rs []rune) string {
	if len(rs) == 0 {
		return "[]"
	}
	out := "["
	for i, r := range rs {
		if i > 0 {
			out += " "
		}
		if i == 8 {
			out += "..."
			break
		}
		out += string(rune('U')) + "+" + hex4(r)
	}
	return out + "]"
}

func hex4(r rune) string {
	const d = "0123456789ABCDEF"
	out := ""
	for shift := 20; shift >= 0; shift -= 4 {
		v := (r >> uint(shift)) & 0xF
		if out == "" && v == 0 && shift > 12 {
			continue
		}
		out += string(d[v])
	}
	return out
}

// PyFoldI rewrites patterns, so what it must not break is more interesting
// than what it must change. Each row is a construct that actually appears
// in this port's patterns, and the negative rows are the ones that matter:
// a rewriter that mangles `(?-i:` or eats a backslash's partner produces a
// pattern that either fails to compile or, worse, compiles into something
// else.
func TestPyFoldISkipsWhatItMustNotTouch(t *testing.T) {
	const I = IClass
	for _, tc := range []struct{ name, in, want string }{
		{"a bare literal", "into", I + "nto"},
		{"upper too, because (?i) folds both", "INTO", I + "NTO"},
		{"the inline flag itself survives", `(?i)file`, `(?i)f` + I + `le`},
		{"a negated flag group survives", `(?-i:x)i`, `(?-i:x)` + I},
		{"multi-flag groups survive", `(?is)i`, `(?is)` + I},
		{"a named capture's prefix survives", `(?P<path>i)`, `(?P<path>` + I + `)`},
		{"a non-capturing group survives", `(?:if)`, `(?:` + I + `f)`},
		{"an escape keeps its partner", `\.i\.i`, `\.` + I + `\.` + I},
		{"an escaped class shorthand survives", `\d+i`, `\d+` + I},
		{"a class is copied whole", `[a-z]i`, `[a-z]` + I},
		{"a negated class too", `[^a-z]i`, `[^a-z]` + I},
		{"a class with an escaped bracket", `[\]x]i`, `[\]x]` + I},
		{"nothing to do", `[0-9]+\.[0-9]+`, `[0-9]+\.[0-9]+`},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := PyFoldI(tc.in)
			if got != tc.want {
				t.Fatalf("PyFoldI(%q)\n got %q\nwant %q", tc.in, got, tc.want)
			}
			if _, err := regexp.Compile(got); err != nil {
				t.Fatalf("PyFoldI(%q) produced a pattern that does not "+
					"compile: %v", tc.in, err)
			}
		})
	}
}

// The class arm cannot do the right thing, so it must not do the wrong one
// quietly. This is the only failure mode of PyFoldI that a caller could
// otherwise ship: a pattern that looks rewritten and is not.
func TestPyFoldIRefusesABareIInsideAClass(t *testing.T) {
	// The last row is IClass itself. PyFoldI is deliberately NOT
	// idempotent: IClass's own class body holds a literal `i`, so applying
	// the transform to an already-transformed pattern panics rather than
	// producing a nested one. Composition order therefore matters, and it
	// failing loudly is how a caller finds that out at init instead of at
	// match time.
	for _, in := range []string{`[i]`, `[abci]`, `[^i]`, `(?i)x[Ij]y`, IClass} {
		t.Run(in, func(t *testing.T) {
			defer func() {
				if recover() == nil {
					t.Fatalf("PyFoldI(%q) returned instead of panicking; a "+
						"class it cannot rewrite is a divergence it must "+
						"not hide", in)
				}
			}()
			_ = PyFoldI(in)
		})
	}
}

// The end-to-end claim, against the real engine on both sides: a pattern
// put through PyFoldI matches exactly what CPython's re.IGNORECASE matches
// for the same source, over a corpus built from the words this port's
// patterns actually contain.
func TestPyFoldIPatternsAgreeWithCPython(t *testing.T) {
	pats := []string{
		`(?i)(?:to|into|at|as)`,
		`(?i)^(?:file|to|into|path|dir)`,
		`(?i)exit(?: code)? 0`,
		`(?i)fail(?:ed|s|ing|ure)?`,
		`(?i)(?:all )?(?:tests? )?pass(?:ed|es|ing)?`,
		`(?i)writ[a-z]*`,
		`(?i)did not|didn'?t`,
	}
	dotI, dotless := string(rune(0x0130)), string(rune(0x0131))
	var subjects []string
	for _, base := range []string{
		"into", "file", "dir", "exit code 0", "failing", "failure",
		"all tests passing", "writing", "did not", "didn't", "at", "as",
		"path", "to", "tomorrow", "tolerances", "quickly", "",
	} {
		subjects = append(subjects, base)
		for _, sub := range []string{dotI, dotless, "I"} {
			// Every i-position variant, so the corpus is not one lucky
			// substitution.
			for k, r := range base {
				if r != 'i' {
					continue
				}
				subjects = append(subjects, base[:k]+sub+base[k+1:])
			}
		}
	}

	const src = `
import json, re, sys
pats, subs = json.loads(sys.argv[1]), json.loads(sys.argv[2])
print(json.dumps([[bool(re.search(p, s)) for s in subs] for p in pats]))
`
	var want [][]bool
	pyprobe.Probe{Stdlib: true}.RunJSON(t, src, &want,
		pyprobe.Arg(t, pats), pyprobe.Arg(t, subjects))

	// A corpus of all-false or all-true rows would agree with anything.
	trues, falses, folded := 0, 0, 0
	for i, p := range pats {
		re := regexp.MustCompile(PyFoldI(p))
		plain := regexp.MustCompile(p)
		for j, s := range subjects {
			if got := re.MatchString(s); got != want[i][j] {
				t.Errorf("PyFoldI(%q) on %q: go %v, cpython %v",
					p, s, got, want[i][j])
			}
			if want[i][j] {
				trues++
			} else {
				falses++
			}
			// ...and count the cases the UNFOLDED pattern would have got
			// wrong, so a corpus that stopped reaching the Turkish i fails
			// instead of certifying the fix vacuously.
			if plain.MatchString(s) != want[i][j] {
				folded++
			}
		}
	}
	if trues == 0 || falses == 0 {
		t.Fatalf("the corpus produced %d matches and %d non-matches; one of "+
			"them is zero, so it agrees with anything", trues, falses)
	}
	if folded < 5 {
		t.Fatalf("only %d case(s) in this corpus distinguish the folded "+
			"pattern from the bare one — it is not exercising what it "+
			"exists to check", folded)
	}
	t.Logf("%d subjects x %d patterns; %d cases the unfolded pattern gets "+
		"wrong", len(subjects), len(pats), folded)
}
