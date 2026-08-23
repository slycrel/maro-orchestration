package pyjson

import (
	"encoding/json"
	"testing"
)

// A nil slice and an empty one must render IDENTICALLY, as `[]`.
//
// Python has no nil-vs-empty distinction: a list comprehension that
// matches nothing yields `[]`, and every consumer of these rows iterates
// the field. Go's encoding/json renders a nil slice as `null`, which is a
// different VALUE — a Python reader gets a type error instead of an empty
// loop.
//
// This is pinned here rather than at the call sites because it is what
// lets those call sites stop worrying about it: internal/playbook's
// curation stats carry a possibly-empty list of expired alarm keys
// straight into a captain's-log row, and its reasoning cites this
// behaviour by name.
func TestANilSliceRendersAsAnEmptyListAndNotNull(t *testing.T) {
	var nilStrings []string
	var nilAnys []any

	for _, tc := range []struct {
		name string
		v    any
	}{
		{"nil []string", nilStrings},
		{"empty []string", []string{}},
		{"nil []any", nilAnys},
		{"empty []any", []any{}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := Ordered(map[string]any{"k": tc.v}, []string{"k"})
			if err != nil {
				t.Fatal(err)
			}
			// Compact separators, not Python's `", "` / `": "` — the
			// separator spelling is a separate NAMED divergence in this
			// port and is not what this pin is about. What is asserted
			// here is the VALUE: `[]`, never `null`.
			if want := `{"k":[]}`; string(got) != want {
				t.Errorf("pyjson rendered %s\n got: %s\nwant: %s",
					tc.name, got, want)
			}
		})
	}

	// The control that gives the test its point: the generic encoder does
	// NOT do this, so the assertion above is reporting pyjson's own rule
	// and not something every Go encoder happens to do.
	generic, err := json.Marshal(map[string]any{"k": nilStrings})
	if err != nil {
		t.Fatal(err)
	}
	if string(generic) != `{"k":null}` {
		t.Fatalf("encoding/json no longer renders a nil slice as null "+
			"(%s) — the divergence this pin exists to cover has moved",
			generic)
	}
}
