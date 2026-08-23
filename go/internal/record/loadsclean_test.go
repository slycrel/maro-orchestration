package record

import (
	"encoding/json"
	"strings"
	"testing"
)

// Each refusal below is a probed Python finding: a line the strict reader
// must STRAND, so the rewrite paths carry it verbatim and the corruption
// keeps announcing itself instead of being laundered into clean-looking
// content.
func TestLoadsCleanRefusals(t *testing.T) {
	cases := []struct {
		name string
		line string
	}{
		{"raw non-UTF-8 bytes", "{\"a\":\"\xff\xfe\"}"},
		{"lone low surrogate escape", `{"tier":"\udcff"}`},
		{"lone high surrogate escape", `{"tier":"\ud800"}`},
		{"duplicate names", `{"applied": false, "applied": true}`},
		{"duplicate names nested", `{"x":{"a":1,"a":2}}`},
		{"trailing data", `{"a":1}{"b":2}`},
		{"not an object", `[1,2,3]`},
		{"null", `null`},
		{"NaN token (Python parses, then its validator refuses)", `{"a": NaN}`},
	}
	for _, c := range cases {
		if _, err := LoadsClean(c.line); err == nil {
			t.Errorf("%s: must be refused, was admitted", c.name)
		}
	}
}

func TestLoadsCleanAdmitsWellFormedRows(t *testing.T) {
	cases := []string{
		`{"a":1}`,
		`{"a":"😀"}`,                  // a VALID surrogate pair (emoji)
		`{"a":"é","b":[1,2],"c":{}}`, // raw UTF-8, nested containers
		`{"a":[{"x":1},{"x":2}]}`,    // same key in SIBLING objects is fine
	}
	for _, line := range cases {
		if _, err := LoadsClean(line); err != nil {
			t.Errorf("%q: must be admitted, got %v", line, err)
		}
	}
}

// Numbers keep their source literal so an int stays distinguishable from a
// float — Python's json.loads makes that distinction and the skill
// validator reads it (7 is an int, 7.0 is not).
func TestLoadsCleanKeepsNumberLiterals(t *testing.T) {
	row, err := LoadsClean(`{"i":7,"f":7.0,"big":12345678901234567890}`)
	if err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]string{
		"i": "7", "f": "7.0", "big": "12345678901234567890",
	} {
		n, ok := row[key].(json.Number)
		if !ok {
			t.Fatalf("%s: want json.Number, got %T", key, row[key])
		}
		if n.String() != want {
			t.Fatalf("%s: literal %q, want %q", key, n.String(), want)
		}
	}
}

func TestIsFrameBlankIsEmptyOnly(t *testing.T) {
	if !IsFrameBlank("") {
		t.Fatal("the trailing-newline fragment is framing")
	}
	// Unicode whitespace is NOT framing: Python's str.strip() would remove
	// it, and JSON forbids it, so a stripped copy could parse when the row
	// does not — and a line of it alone must not be dropped from a rewrite.
	for _, s := range []string{" ", "\t", " ", " "} {
		if IsFrameBlank(s) {
			t.Fatalf("%q must be a row, not framing", s)
		}
	}
}

// A deeply nested line must STRAND, not kill the process. The walker runs
// before Decode, so nothing has bounded the nesting yet; a recursive
// version took an unrecoverable `fatal error: stack overflow` here — no
// recover, no strand, just death on every subsequent run of a shared store.
func TestLoadsCleanDeepNestingStrandsAndDoesNotCrash(t *testing.T) {
	for _, depth := range []int{5000, 20000, 2000000} {
		line := `{"a":` + strings.Repeat("[", depth) + strings.Repeat("]", depth) + "}"
		_, err := LoadsClean(line)
		if depth >= 20000 && err == nil {
			t.Errorf("depth %d: encoding/json's own max depth must refuse it", depth)
		}
		// The property under test is that the call RETURNS at all.
		_ = err
	}
}

// The iterative walker must still catch a duplicate name at any depth, in
// either container, and must not confuse an array element with a key.
func TestRefuseDuplicateNamesAtDepth(t *testing.T) {
	dup := []string{
		`{"a":1,"a":2}`,
		`{"x":{"a":1,"a":2}}`,
		`{"x":[{"a":1,"a":2}]}`,
		`{"x":[[[{"deep":{"a":1,"a":2}}]]]}`,
		`{"x":{"y":{"z":[1,2,{"a":1,"a":2}]}}}`,
	}
	for _, line := range dup {
		if _, err := LoadsClean(line); err == nil {
			t.Errorf("duplicate name not caught: %s", line)
		}
	}
	ok := []string{
		`{"a":1,"b":2}`,
		`{"x":["a","a","a"]}`,               // repeated array VALUES are fine
		`{"x":{"a":1},"y":{"a":2}}`,         // same name in sibling objects
		`{"x":[{"a":1},{"a":2}]}`,           // same name in sibling array elements
		`{"a":[1,[2,[3,{"b":{"c":[4]}}]]]}`, // mixed nesting, no duplicates
	}
	for _, line := range ok {
		if _, err := LoadsClean(line); err != nil {
			t.Errorf("false duplicate on %s: %v", line, err)
		}
	}
}

// A row Go admits and Python strands is a row only one runtime will act
// on. CPython's int() conversion is capped at 4300 digits and raises above
// it, so loads_clean strands the line; Go's decoder is happy to keep it.
func TestLoadsCleanRefusesOverlongIntegerLiterals(t *testing.T) {
	long := strings.Repeat("9", 4301)
	if _, err := LoadsClean(`{"n":` + long + `}`); err == nil {
		t.Error("a 4301-digit integer must strand, as it does in Python")
	}
	if _, err := LoadsClean(`{"n":-` + long + `}`); err == nil {
		t.Error("the sign is not a digit")
	}
	if _, err := LoadsClean(`{"n":` + strings.Repeat("9", 4300) + `}`); err != nil {
		t.Errorf("4300 digits is within the cap: %v", err)
	}
	// A float literal is not an int() conversion in either runtime.
	if _, err := LoadsClean(`{"n":1e400}`); err != nil {
		t.Errorf("float literals are unaffected: %v", err)
	}
	// Nested, and as an array element.
	if _, err := LoadsClean(`{"a":{"b":[1,` + long + `]}}`); err == nil {
		t.Error("the cap applies at any depth")
	}
}
