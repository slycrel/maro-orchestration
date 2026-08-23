package record

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
	"testing"
	"unicode"
)

// goal_verdict_confidence is a JSON field that a judge — or a human, or
// another runtime — writes as a string often enough that coerceFloat already
// carries three prior parity fixes for it. Python's float() folds EVERY
// Unicode decimal digit to ASCII before parsing, so "٠.٥" is 0.5 there.
// Go's ParseFloat is ASCII-only, so before this fix such a value was
// unparseable here — and an unparseable confidence never downgrades a judged
// verdict, so a below-floor confidence read as FULL trust. The divergence is
// in the unsafe direction, which is why it gets a whole-table sweep and not
// a spot check.

// pythonDecimalMap returns, for every code point, the ASCII digit CPython
// folds it to, or -1. The map is DERIVED from CPython rather than
// transcribed, so a Unicode-table update on either side shows up as a test
// failure instead of as silent drift.
func pythonDecimalMap(t *testing.T) []int8 {
	t.Helper()
	out, err := exec.Command("python3", "-c",
		"import sys,unicodedata as u;"+
			"sys.stdout.write(''.join(chr(48+u.decimal(chr(c),-1)) "+
			"if u.decimal(chr(c),-1)>=0 else '.' for c in range(0x110000)))").Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	if len(out) != 0x110000 {
		t.Fatalf("got %d flags, want %d", len(out), 0x110000)
	}
	m := make([]int8, 0x110000)
	for c, b := range out {
		if b == '.' {
			m[c] = -1
		} else {
			m[c] = int8(b - '0')
		}
	}
	return m
}

// The sweep. Both directions are failures and for different reasons: a code
// point Go folds and CPython does not would make Go parse a confidence
// CPython rejects (Go downgrades, Python trusts), and one CPython folds and
// Go does not is the original bug.
func TestEveryUnicodeDecimalDigitFoldsTheWayCPythonDoes(t *testing.T) {
	want := pythonDecimalMap(t)
	var missing, extra, wrong int
	var firstWrong string
	for c := 0; c < 0x110000; c++ {
		got, ok := asciiDigit(rune(c))
		switch {
		case want[c] >= 0 && !ok:
			missing++
		case want[c] < 0 && ok:
			extra++
		case ok && int8(got-'0') != want[c]:
			wrong++
			if firstWrong == "" {
				firstWrong = fmt.Sprintf("U+%04X folds to %q here, %d in CPython",
					c, got, want[c])
			}
		}
	}
	if missing != 0 {
		t.Errorf("%d code points CPython folds and this runtime does not — a "+
			"below-floor confidence written in them reads as FULL trust here "+
			"and DIRECTIONAL there; extend digitSupplement", missing)
	}
	if extra != 0 {
		t.Errorf("%d code points this runtime folds and CPython does not — Go "+
			"would parse a confidence CPython rejects, flipping trust the "+
			"other way", extra)
	}
	if wrong != 0 {
		t.Errorf("%d code points fold to the WRONG digit: %s", wrong, firstWrong)
	}
}

// The walk-back assumes a run's first code point is digit zero and that the
// value advances by one per code point. That is a property of Go's table, not
// a law, so it is asserted against Go's table directly — if a future Go
// release adds a run that breaks it, this fails before the sweep does and
// says WHY.
func TestTheDigitWalkBackMatchesGosOwnTable(t *testing.T) {
	want := pythonDecimalMap(t)
	runs, longest, checked := 0, 0, 0
	for c := 0; c < 0x110000; c++ {
		if !unicode.IsDigit(rune(c)) {
			continue
		}
		checked++
		if c == 0 || !unicode.IsDigit(rune(c-1)) {
			runs++
		}
		start := c
		for start > 0 && unicode.IsDigit(rune(start-1)) {
			start--
		}
		if n := c - start + 1; n > longest {
			longest = n
		}
		if want[c] >= 0 && int8((c-start)%10) != want[c] {
			t.Fatalf("U+%04X: run starts at U+%04X so the walk-back says %d, "+
				"CPython says %d", c, start, (c-start)%10, want[c])
		}
	}
	if checked == 0 {
		t.Fatal("Go's digit table is empty; this test proved nothing")
	}
	t.Logf("Go unicode %s: %d digit code points in %d runs, longest %d",
		unicode.Version, checked, runs, longest)
}

// When Go's table catches up to CPython's, digitSupplement becomes dead code
// that still LOOKS load-bearing. This says so out loud rather than leaving it
// to be discovered.
func TestTheDigitSupplementIsStillCarryingWeight(t *testing.T) {
	live := 0
	for _, rg := range digitSupplement {
		for r := rg[0]; r <= rg[1]; r++ {
			if !unicode.IsDigit(r) {
				live++
			}
		}
	}
	if live == 0 {
		t.Errorf("Go unicode %s now knows every code point in digitSupplement; "+
			"the table is dead code and its doc comment is wrong",
			unicode.Version)
	}
	t.Logf("digitSupplement covers %d code points Go's table still misses", live)
}

// The consequence, stated as trust rather than as parsing: this is the only
// reason the fold matters. Each case is a confidence BELOW the floor spelled
// in a different script, and each must land on directional.
func TestABelowFloorConfidenceInNonASCIIDigitsIsDirectional(t *testing.T) {
	cases := []struct {
		name string
		conf string
	}{
		{"arabic-indic", "٠.٥"}, // ٠.٥
		{"devanagari", "०.५"},   // ०.५
		{"fullwidth", "０.５"},    // ０.５
		{"mathematical bold (50-long run)", "\U0001D7CE.\U0001D7D3"},
		{"unicode-16 only (supplement)", "\U0001E5F1.\U0001E5F6"},
		{"mixed with ascii", "0.٥"},
		{"padded with a unicode space", " ٠.٥　"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got, ok := coerceFloat(c.conf); !ok || got != 0.5 {
				t.Fatalf("coerceFloat(%q) = %v, %v; want 0.5, true", c.conf, got, ok)
			}
			row := map[string]any{
				"goal_achieved":           true,
				"goal_verdict_confidence": c.conf,
			}
			if got := VerdictTrust(row); got != VerdictTrustDirectional {
				t.Errorf("VerdictTrust with confidence %q = %s, want %s — a "+
					"below-floor verdict earned the trusted path",
					c.conf, got, VerdictTrustDirectional)
			}
		})
	}
}

// A fold that produced the right ASCII but a different NUMBER than CPython
// would pass every test above. This one asks CPython for the value.
func TestTheFoldedValueIsTheValueCPythonParses(t *testing.T) {
	inputs := []string{
		"٠.٥", "٩٩", "۷.۵", "１２３",
		"\U0001D7CE.\U0001D7FF", "\U0001D7F9", "०१२३",
		"-٥.٠", "+١e٢", "\U0001E5FA",
		"\U000116D0", "\U000116DA", // the 20-long supplement run, both halves
	}
	payload, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c",
		"import json,sys;print(json.dumps([float(s) for s in json.load(sys.stdin)]))")
	cmd.Stdin = strings.NewReader(string(payload))
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 unavailable or refused an input: %v", err)
	}
	var want []float64
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	for i, in := range inputs {
		got, ok := coerceFloat(in)
		if !ok {
			t.Errorf("coerceFloat(%q) refused; CPython reads %v", in, want[i])
			continue
		}
		if got != want[i] {
			t.Errorf("coerceFloat(%q) = %v, CPython float() = %v", in, got, want[i])
		}
	}
}

// The fold must not invent digits out of things that merely look numeric.
// Roman numerals, circled digits and superscripts all have a numeric VALUE in
// the Unicode database and none of them is a decimal digit; CPython's float()
// rejects every one, and so must this.
func TestNumericLookingNonDigitsAreStillRefused(t *testing.T) {
	for _, in := range []string{
		"Ⅰ",   // Ⅰ ROMAN NUMERAL ONE (Nl)
		"①",   // ① CIRCLED DIGIT ONE (No)
		"²",   // ² SUPERSCRIPT TWO (No)
		"½",   // ½ VULGAR FRACTION ONE HALF (No)
		"〇",   // 〇 IDEOGRAPHIC NUMBER ZERO (Nl)
		"一",   // 一 (Lo)
		"0.①", // a real digit next to a fake one
	} {
		if got, ok := coerceFloat(in); ok {
			t.Errorf("coerceFloat(%q) = %v, accepted; CPython float() raises "+
				"ValueError and the row classifies FULL", in, got)
		}
	}
}
