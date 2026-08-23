package record

import (
	"encoding/json"
	"os/exec"
	"strings"
	"testing"
)

// The three tests here stayed in record when the digit VALUE table moved
// to pytext: they are about what THIS package does with a folded digit —
// coerceFloat's parse and the verdict-trust downgrade it feeds — not
// about the table. The table's own sweep lives in
// internal/pytext/digits_skew_test.go.

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
