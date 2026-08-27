package pytext

import (
	"encoding/json"
	"os/exec"
	"testing"
)

// Head is `s[:n]`, so CPython is the oracle rather than a table of
// expectations typed out by the same reading that wrote the function.
// The negative-n arm has no caller in this tree yet and is the reason
// this test exists at all: an unexercised branch in a helper seven
// packages now share is a liability, not generality.
func TestHeadMatchesCPython(t *testing.T) {
	type pair struct {
		S string `json:"s"`
		N int    `json:"n"`
	}
	cases := []pair{
		{"", 0}, {"", 5}, {"", -1},
		{"abc", 0}, {"abc", 1}, {"abc", 3}, {"abc", 4}, {"abc", 100},
		{"abc", -1}, {"abc", -3}, {"abc", -4}, {"abc", -100},
		// Code points, not bytes: each of these is multi-byte, and a
		// byte slice would cut one in half.
		{"héllo wörld", 5}, {"héllo wörld", 1}, {"héllo wörld", -2},
		{"🙂🙂🙂", 1}, {"🙂🙂🙂", 2}, {"🙂🙂🙂", 3}, {"🙂🙂🙂", 4},
		{"🙂🙂🙂", -1}, {"🙂🙂🙂", -3}, {"🙂🙂🙂", -5},
		{"a\nb\tc", 3}, {"  padded  ", 4},
		// A lone surrogate cannot appear in a Go string, but the
		// combining-mark case can, and it is the one a "just count
		// characters" reading gets wrong in the other direction: Python
		// counts code points, not grapheme clusters, so this cuts the
		// mark off its base letter exactly as Go does.
		{"éx", 1}, {"éx", 2},
	}
	blob, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	script := `
import json, sys
out = [c["s"][:c["n"]] for c in json.loads(sys.argv[1])]
print(json.dumps(out))
`
	raw, err := exec.Command("python3", "-c", script, string(blob)).Output()
	if err != nil {
		t.Fatalf("python3: %v", err)
	}
	var want []string
	if err := json.Unmarshal(raw, &want); err != nil {
		t.Fatal(err)
	}
	if len(want) != len(cases) {
		t.Fatalf("got %d answers for %d cases", len(want), len(cases))
	}
	for i, c := range cases {
		if got := Head(c.S, c.N); got != want[i] {
			t.Errorf("Head(%q, %d) = %q, want %q", c.S, c.N, got, want[i])
		}
	}
}
