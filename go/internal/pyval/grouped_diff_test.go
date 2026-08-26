package pyval

import (
	"encoding/json"
	"os/exec"
	"strconv"
	"strings"
	"testing"
)

// TestGroupedMatchesCPython sweeps the boundaries of the grouping rule
// rather than a sample of pretty numbers.
//
// The interesting inputs are the ones where an index-based `i%3` gets it
// wrong: lengths that are exact multiples of three (1000, 1000000), the
// first length that needs a separator at all (1000 vs 999), and the sign,
// which Python keeps OUTSIDE the grouping.
func TestGroupedMatchesCPython(t *testing.T) {
	var ns []int64
	// Every length from 1 to 19 digits, at both ends of each length, plus
	// the exact powers of ten where the leading group changes size.
	for d := 1; d <= 18; d++ {
		lo, err := strconv.ParseInt("1"+strings.Repeat("0", d-1), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		hi, err := strconv.ParseInt(strings.Repeat("9", d), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		ns = append(ns, lo, hi, -lo, -hi, lo+1, hi-1)
	}
	ns = append(ns, 0, -0, 1, -1, 12, 123, 1234, 12345, 123456, 1234567,
		1<<62, -(1 << 62), 9223372036854775807, -9223372036854775808)

	arg, err := json.Marshal(ns)
	if err != nil {
		t.Fatal(err)
	}
	const src = `
import json, sys
print(json.dumps([format(n, ",") for n in json.loads(sys.argv[1])]))
`
	out, err := exec.Command("python3", "-c", src, string(arg)).Output()
	if err != nil {
		// A missing interpreter skips; a probe that RAN and failed is fatal.
		if _, ok := err.(*exec.Error); ok {
			t.Skip("python3 unavailable")
		}
		t.Fatalf("probe failed: %v", err)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output is not JSON: %v (%q)", err, out)
	}
	if len(want) != len(ns) {
		t.Fatalf("probe returned %d answers for %d inputs", len(want), len(ns))
	}
	for i, n := range ns {
		if got := Grouped(n); got != want[i] {
			t.Errorf("Grouped(%d): cpython %q, go %q", n, want[i], got)
		}
	}
}
