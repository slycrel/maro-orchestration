package pytext

import (
	"encoding/json"
	"strings"
	"testing"
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// str.lower() feeds Slugify, which produces a FILENAME, and the audience
// stamps, which are compared as strings. Both are places where two runtimes
// disagreeing about one rune means two records where there should be one.
// So the map is derived from CPython, never transcribed by hand.

// pythonLowerMap returns, for every code point CPython lowercases to
// something else, the result.
func pythonLowerMap(t *testing.T) map[rune]string {
	t.Helper()
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import json,sys;print(json.dumps({c:chr(c).lower() "+
			"for c in range(0x110000) if chr(c).lower()!=chr(c)}))"))
	var raw map[string]string
	if err := json.Unmarshal(out, &raw); err != nil {
		t.Fatalf("decoding CPython output: %v", err)
	}
	m := make(map[rune]string, len(raw))
	for k, v := range raw {
		var c int
		if _, err := jsonNumber(k, &c); err != nil {
			t.Fatalf("bad key %q: %v", k, err)
		}
		m[rune(c)] = v
	}
	if len(m) == 0 {
		t.Fatal("CPython lowercases nothing; the probe is broken")
	}
	return m
}

func jsonNumber(s string, out *int) (int, error) {
	var n int
	if err := json.Unmarshal([]byte(s), &n); err != nil {
		return 0, err
	}
	*out = n
	return n, nil
}

// The sweep. Every code point, both directions, one rune at a time — which
// is how Lower is actually reached (Slugify passes a whole name, but a
// single differing rune is what changes the slug).
func TestLowerAgreesWithCPythonOnEveryCodePoint(t *testing.T) {
	want := pythonLowerMap(t)
	var missed, extra int
	var firstMissed, firstExtra string
	for c := 0; c < 0x110000; c++ {
		if !utf8Valid(rune(c)) {
			continue
		}
		in := string(rune(c))
		got := Lower(in)
		exp, lowered := want[rune(c)]
		if !lowered {
			exp = in
		}
		switch {
		case got == exp:
		case got == in:
			missed++
			if firstMissed == "" {
				firstMissed = shortf(c, got, exp)
			}
		default:
			extra++
			if firstExtra == "" {
				firstExtra = shortf(c, got, exp)
			}
		}
	}
	if missed != 0 {
		t.Errorf("%d code points CPython lowercases and this runtime leaves "+
			"alone (first: %s) — extend lowerSupplement", missed, firstMissed)
	}
	if extra != 0 {
		t.Errorf("%d code points lowercase DIFFERENTLY here (first: %s)",
			extra, firstExtra)
	}
}

// U+0130 is the one unconditional multi-rune lowercase mapping, and it is
// handled by hand rather than by the supplement. It gets its own assertion
// so a regression names itself.
func TestTheDottedCapitalIStillLowercasesToTwoRunes(t *testing.T) {
	if got := Lower("İstanbul"); got != "i̇stanbul" {
		t.Errorf("Lower(\"İstanbul\") = %q, want %q", got, "i̇stanbul")
	}
	// And it composes with the supplement rather than short-circuiting past
	// it: the early return for "no U+0130" must not be the only path that
	// applies the supplement.
	in := "İ\U00010D50"
	if got, want := Lower(in), "i̇\U00010D70"; got != want {
		t.Errorf("Lower(%q) = %q, want %q — the two fixes do not compose",
			in, got, want)
	}
}

// When Go's table catches up, lowerSupplement becomes dead code that still
// looks load-bearing.
func TestTheLowerSupplementIsStillCarryingWeight(t *testing.T) {
	live := 0
	for r := range lowerSupplement {
		if strings.ToLower(string(r)) != string(r) {
			continue // Go's own table now handles it
		}
		live++
	}
	if live == 0 {
		t.Errorf("Go unicode %s now lowercases every rune in lowerSupplement; "+
			"the table is dead code and its doc comment is wrong",
			unicode.Version)
	}
	t.Logf("lowerSupplement covers %d runes Go's table still misses of %d",
		live, len(lowerSupplement))
}

func utf8Valid(r rune) bool { return r < 0xd800 || r > 0xdfff }

func shortf(c int, got, want string) string {
	return "U+" + strings.ToUpper(strconv16(c)) + ": got " + got + ", want " + want
}

func strconv16(c int) string {
	const hex = "0123456789abcdef"
	if c == 0 {
		return "0"
	}
	var b []byte
	for c > 0 {
		b = append([]byte{hex[c%16]}, b...)
		c /= 16
	}
	return string(b)
}

// The single-rune sweep above CANNOT see a context rule, and said so in its
// own comment while being wrong about it ("a single differing rune is what
// changes the slug"). U+03A3 lowercases to ς or σ depending on what
// surrounds it, so it is invisible one rune at a time — the pin was green
// against broken code for exactly as long as it existed (adversarial r6).
//
// These two sweeps put every code point on BOTH sides of a sigma, which is
// where its Cased / Case_Ignorable classification decides the answer.
//
// The after-sweep is spelled "aΣ"+c and not "Σ"+c+"a" on purpose. The
// obvious spelling is VACUOUS: a sigma at index 0 has nothing cased before
// it, so Final_Sigma is false for every c and the assertion compares false
// to false 1.1M times. It was written that way first and reported a clean
// zero while the after-side was entirely unexamined; the anti-vacuity
// guards below exist so that cannot recur silently.
func TestTheSigmaContextRuleAgreesWithCPythonAtEveryCodePoint(t *testing.T) {
	// Three arms, not two. The "alone" arm exists because a rune can be
	// BOTH Cased and Case_Ignorable — the ~267 modifier letters in
	// Lm ∩ Other_Lowercase — and the rule resolves that by testing
	// ignorability FIRST and continuing the scan. With a cased "a" already
	// in front, both orderings agree, so the leading-"a" arm cannot see the
	// difference: 'ʰΣ'.lower() is 'ʰσ' (skip ʰ, find nothing) while an
	// implementation that tested cased first would say 'ʰς'. That mutant
	// survived the two-arm version of this sweep.
	arms := []struct {
		name string
		py   string
		go_  func(ch string) bool
	}{
		{"BEFORE (cased context)",
			"('a'+chr(c)+'\\u03a3').lower().endswith('\\u03c2')",
			func(ch string) bool { return strings.HasSuffix(Lower("a"+ch+"Σ"), "ς") }},
		{"AFTER",
			"('a\\u03a3'+chr(c)).lower()[1]=='\\u03c2'",
			func(ch string) bool { return []rune(Lower("aΣ" + ch))[1] == 'ς' }},
		{"BEFORE (alone)",
			"(chr(c)+'\\u03a3').lower().endswith('\\u03c2')",
			func(ch string) bool { return strings.HasSuffix(Lower(ch+"Σ"), "ς") }},
	}
	n := len(arms)
	var expr []string
	for _, a := range arms {
		expr = append(expr, "('1' if "+a.py+" else '0')")
	}
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import sys;sys.stdout.write(''.join("+strings.Join(expr, "+")+
			"for c in range(0x110000)))"))
	if len(out) != n*0x110000 {
		t.Fatalf("got %d flags, want %d", len(out), n*0x110000)
	}
	bad := make([]int, n)
	first := make([]string, n)
	final := make([]int, n)
	checked := 0
	for c := 0; c < 0x110000; c++ {
		if c >= 0xd800 && c <= 0xdfff {
			continue
		}
		checked++
		ch := string(rune(c))
		for k, a := range arms {
			want := out[n*c+k] == '1'
			if want {
				final[k]++
			}
			if a.go_(ch) != want {
				bad[k]++
				if first[k] == "" {
					first[k] = "U+" + strings.ToUpper(strconv16(c))
				}
			}
		}
	}
	if checked == 0 {
		t.Fatal("nothing was checked; this test proved nothing")
	}
	for k, a := range arms {
		// Each arm must see the rule come out BOTH ways, or it is only
		// asserting a constant.
		if final[k] == 0 || final[k] == checked {
			t.Fatalf("the %s arm is vacuous: %d of %d are final",
				a.name, final[k], checked)
		}
		if bad[k] != 0 {
			t.Errorf("%d code points are classified differently in the %s arm "+
				"(first %s) — cased/caseIgnorable disagrees with CPython",
				bad[k], a.name, first[k])
		}
	}
}

// The dead-table detector the other three supplements each have. When Go's
// unicode tables reach 16.0.0 these three become no-ops that still read as
// load-bearing, and the comments above them become wrong.
func TestTheSigmaSupplementsAreStillCarryingWeight(t *testing.T) {
	goCased := func(r rune) bool {
		return unicode.In(r, unicode.Lu, unicode.Ll, unicode.Lt,
			unicode.Other_Lowercase, unicode.Other_Uppercase)
	}
	goIgnorable := func(r rune) bool {
		return wordBreakIgnorable[r] || unicode.In(r,
			unicode.Mn, unicode.Me, unicode.Cf, unicode.Lm, unicode.Sk)
	}
	live := 0
	for _, rg := range casedSupplement {
		for r := rg[0]; r <= rg[1]; r++ {
			if !goCased(r) {
				live++
			}
		}
	}
	if live == 0 {
		t.Errorf("Go unicode %s now knows every rune in casedSupplement; "+
			"the table is dead code", unicode.Version)
	}
	t.Logf("casedSupplement covers %d code points Go's table still misses", live)

	live = 0
	for _, rg := range caseIgnorableSupplement {
		for r := rg[0]; r <= rg[1]; r++ {
			if !goIgnorable(r) {
				live++
			}
		}
	}
	if live == 0 {
		t.Errorf("Go unicode %s now knows every rune in "+
			"caseIgnorableSupplement; the table is dead code", unicode.Version)
	}
	t.Logf("caseIgnorableSupplement covers %d code points Go's table still misses", live)

	// The exclusion is the reverse direction: it only does work while Go
	// still calls U+1171E case-ignorable.
	if !goIgnorable(caseIgnorableExclusion) {
		t.Errorf("Go unicode %s no longer treats U+1171E as case-ignorable; "+
			"caseIgnorableExclusion is dead code", unicode.Version)
	}
}

// End to end on real strings, because the rule is about whole words and the
// sweeps above only ever look at one neighbour.
func TestLowerMatchesCPythonOnWordsWithSigma(t *testing.T) {
	words := []string{
		"ΟΔΟΣ",         // the finding: word-final sigma
		"ΟΔΟΣ ΜΕΓΑΣ",   // two of them
		"Σ",            // alone: nothing cased before it
		"ΑΣ",           // cased before, nothing after
		"aΣb",          // cased after: stays σ
		"aΣ'",          // trailing case-ignorable does not un-finalize it
		"a'Σ",          // leading case-ignorable does not break the lookback
		"áΣ",          // combining acute is case-ignorable
		"a Σ",          // a SPACE is neither cased nor ignorable
		"ʰΣ",           // BOTH cased and ignorable: ignorable wins, stays σ
		"aʰΣ",          // ...and skipping it still finds the "a": ς
		"ʰʰΣ",          // a run of them, still nothing cased behind
		"ΣΣ",           // first is medial, second final
		"ΑΣΣ",          //
		"1Σ",           // a digit is not cased
		"ΟΔΟΣ.md",      // the shape Slugify actually sees
		"İΣ",           // composes with the U+0130 expansion
		"ΑΣ\U00010D50", // composes with the unicode-16 supplement
		"στίγμαΣ",      // already-lowercase context
		"ΜΆΪΟΣ",        // accents and diaeresis
	}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t,
		"import json,sys;print(json.dumps([s.lower() for s in json.loads(sys.argv[1])]))",
		&want, pyprobe.Arg(t, words))
	sawFinal := false
	for i, w := range words {
		if strings.ContainsRune(want[i], 'ς') {
			sawFinal = true
		}
		if got := Lower(w); got != want[i] {
			t.Errorf("Lower(%q) = %q, CPython gives %q", w, got, want[i])
		}
	}
	if !sawFinal {
		t.Fatal("no case in this table produces a final sigma; the table " +
			"does not exercise the rule it is named for")
	}
}
