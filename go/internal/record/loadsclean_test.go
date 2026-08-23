package record

import (
	"encoding/json"
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
