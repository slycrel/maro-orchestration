package record

import "testing"

// r4 LOW pins: coerceFloat must match Python float() on bool and
// out-of-range numeric-string confidences — both previously flipped §4
// trust in the unsafe direction (malformed row read at FULL trust).
func TestVerdictTrustPythonFloatEdges(t *testing.T) {
	judged := func(conf any) map[string]any {
		return map[string]any{"goal_achieved": true,
			"goal_verdict_confidence": conf}
	}
	cases := []struct {
		name string
		conf any
		want string
	}{
		// float(False)=0.0 < 0.7 → directional; float(True)=1.0 → full.
		{"bool false", false, VerdictTrustDirectional},
		{"bool true", true, VerdictTrustFull},
		// float("-1e999") = -inf < 0.7 → directional.
		{"overflow negative", "-1e999", VerdictTrustDirectional},
		// float("1e999") = +inf ≥ 0.7 → full.
		{"overflow positive", "1e999", VerdictTrustFull},
		// float("1e-999") = 0.0 (underflow) → directional.
		{"underflow", "1e-999", VerdictTrustDirectional},
		// Hex still rejected (Python float() raises) → full via error pass.
		{"hex stays full", "0x1p-2", VerdictTrustFull},
		// Non-numeric string → Python ValueError pass → full.
		{"garbage stays full", "abc", VerdictTrustFull},
	}
	for _, c := range cases {
		if got := VerdictTrust(judged(c.conf)); got != c.want {
			t.Errorf("%s: got %s want %s", c.name, got, c.want)
		}
	}
}
