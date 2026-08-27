package pytext

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// The skew these two tests exist for was invisible to
// wordclass_invariant_test.go for one reason: that file compiles WordClass
// and DigitClass WITHOUT `(?i)`, and every real consumer of them compiles
// WITH it, because the Python they port passes re.IGNORECASE. Go expands a
// class's case-folds before negating it; Python does not fold `\w` at all.
// So the class the tests measured and the class the callers ran were
// different classes, and nothing in the package could see the difference.
//
// Found by artifactcheck r2 (2026-08-26), in both directions at once.

// TestEveryExportedClassIsFoldInvariant sweeps the whole rune space rather
// than sampling, because the answer turned out to be ONE code point out of
// 1.1 million and no sample was going to contain it.
func TestEveryExportedClassIsFoldInvariant(t *testing.T) {
	cases := []struct{ name, pat string }{
		{"WordClass", WordClass},
		{"NotWordClass", NotWordClass},
		{"SpaceClass", SpaceClass},
		{"DigitClass", DigitClass},
		{"NotWordClassPlus(\"\")", NotWordClassPlus("")},
		{"NotWordClassPlus(\".;\\n\")", NotWordClassPlus(".;\n")},
		{"NotClass(\"\")", NotClass("")},
		{"WordStart", WordStart},
		{"WordEnd", WordEnd},
	}
	for _, c := range cases {
		plain := regexp.MustCompile(c.pat)
		fold := regexp.MustCompile(`(?i)` + c.pat)
		bad, first := 0, rune(-1)
		for r := rune(0); r <= 0x10FFFF; r++ {
			if r >= 0xD800 && r <= 0xDFFF {
				continue
			}
			s := string(r)
			if plain.MatchString(s) != fold.MatchString(s) {
				if bad == 0 {
					first = r
				}
				bad++
			}
		}
		if bad != 0 {
			t.Errorf("%s: %d code points match differently under (?i), "+
				"first U+%04X. Python's re does not fold character "+
				"classes; wrap the class in (?-i: ) so Go does not either.",
				c.name, bad, first)
		}
	}
}

// TestTheFoldedWordClassStillAgreesWithTheWordPredicate is the same claim
// wordclass_invariant_test.go makes, asked of the class the CALLERS
// compile. boundaryAt reaches IsWordChar while the pattern next to it
// reaches the compiled class, so the two disagreeing is a position that is
// simultaneously a word boundary and not one.
func TestTheFoldedWordClassStillAgreesWithTheWordPredicate(t *testing.T) {
	re := regexp.MustCompile(`(?i)^` + WordClass + `$`)
	bad := 0
	for r := rune(0); r <= 0x10FFFF; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		if re.MatchString(string(r)) != IsWordChar(r) {
			if bad < 5 {
				t.Errorf("U+%04X: (?i)WordClass=%v IsWordChar=%v",
					r, !IsWordChar(r), IsWordChar(r))
			}
			bad++
		}
	}
	if bad != 0 {
		t.Errorf("%d code points disagree", bad)
	}
}

// TestNoCallerSplicesAClassBodyIntoAnIgnoreCasePattern is the guard for the
// half this package cannot fix from inside: a caller that builds its OWN
// `[...]` out of one of the exported BODIES cannot be wrapped by pytext,
// and the wrapper is exactly what it is missing.
//
// A source scan is a blunt instrument and this one is deliberately blunt:
// it looks at every regexp.MustCompile call in the module, and complains
// when the call text contains both `(?i)` and a spliced class body without
// a `(?-i:` anywhere in it. A false positive is a comment away; the false
// NEGATIVE it replaces was a HIGH.
func TestNoCallerSplicesAClassBodyIntoAnIgnoreCasePattern(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	bodies := []string{"WordClassBody", "SpaceClassBody"}
	scanned, calls := 0, 0
	err = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(p, ".go") {
			return nil
		}
		if strings.Contains(p, "/internal/pytext/") {
			return nil
		}
		src, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		scanned++
		text := string(src)
		for _, span := range mustCompileCalls(text) {
			calls++
			if !strings.Contains(span, `(?i)`) || strings.Contains(span, `(?-i:`) {
				continue
			}
			for _, b := range bodies {
				if strings.Contains(span, b) {
					rel, _ := filepath.Rel(root, p)
					t.Errorf("%s: a regexp.MustCompile with (?i) splices "+
						"pytext.%s into a raw class and never turns folding "+
						"off. Go fold-grows \\p{L} by U+0345 before "+
						"negating; Python does not fold classes at all. "+
						"Wrap the class in (?-i: ).\n\t%s", rel, b, span)
					break
				}
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// A scan that found nothing to scan passes vacuously, which is the
	// failure mode this project keeps re-finding (L1).
	if scanned < 100 || calls < 50 {
		t.Fatalf("scan covered %d files / %d MustCompile calls; too few to "+
			"be measuring anything", scanned, calls)
	}
	t.Logf("%d files, %d regexp.MustCompile calls", scanned, calls)
}

// mustCompileCalls returns the source text of each regexp.MustCompile(...)
// call, paren-balanced so a multi-line concatenation arrives whole.
func mustCompileCalls(text string) []string {
	var out []string
	const key = "regexp.MustCompile("
	for i := 0; ; {
		j := strings.Index(text[i:], key)
		if j < 0 {
			return out
		}
		start := i + j
		depth, k := 0, start+len(key)-1
		for ; k < len(text); k++ {
			switch text[k] {
			case '(':
				depth++
			case ')':
				depth--
			}
			if depth == 0 {
				break
			}
		}
		if k >= len(text) {
			return out
		}
		out = append(out, text[start:k+1])
		i = k + 1
	}
}
