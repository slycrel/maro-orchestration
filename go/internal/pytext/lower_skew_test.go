package pytext

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
	"unicode"
)

// str.lower() feeds Slugify, which produces a FILENAME, and the audience
// stamps, which are compared as strings. Both are places where two runtimes
// disagreeing about one rune means two records where there should be one.
// So the map is derived from CPython, never transcribed by hand.

// pythonLowerMap returns, for every code point CPython lowercases to
// something else, the result.
func pythonLowerMap(t *testing.T) map[rune]string {
	t.Helper()
	out, err := exec.Command("python3", "-c",
		"import json,sys;print(json.dumps({c:chr(c).lower() "+
			"for c in range(0x110000) if chr(c).lower()!=chr(c)}))").Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
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
