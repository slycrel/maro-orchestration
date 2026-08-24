package pyval

import (
	"encoding/json"
	"testing"
)

// Truthy had no test. It is now the single implementation of Python's
// bool() for four packages — evolver, graduation, skills and notify, each
// of which carried a private copy until mission-r9 — and a shared helper
// with no differential is the exact shape r8 named as a single point of
// silent, distributed failure.
//
// The table is CPython's rule stated directly rather than shelled out to,
// because bool() is a language guarantee rather than a library behaviour:
// None, False, zero of any numeric type, and every empty container are
// false; everything else, INCLUDING an object of an unrecognized type, is
// true. What needs a test is not the rule — it is which Go types this
// function remembers to recognize, and the failure mode is a missing case
// falling through to the default.
func TestTruthyIsPythonsBool(t *testing.T) {
	cases := []struct {
		name string
		v    any
		want bool
	}{
		{"nil is None", nil, false},
		{"false", false, false},
		{"true", true, true},

		{"empty string", "", false},
		{"a space is not empty", " ", true},
		// The two that catch a "parse it as a bool" implementation:
		// Python's bool("false") is True, and so is bool("0").
		{`the string "false"`, "false", true},
		{`the string "0"`, "0", true},

		// The `case int` gap that all three private copies had. Every
		// value arriving from encoding/json is a float64, so this is
		// invisible on the read path and live the moment a value is built
		// in Go — a struct field, a count, a len(). bool(0) came back TRUE.
		{"int zero", int(0), false},
		{"int nonzero", int(7), true},
		{"int negative", int(-1), true},
		{"int64 zero", int64(0), false},
		{"int64 nonzero", int64(7), true},

		{"float zero", float64(0), false},
		{"negative float zero", float64(-0.0), false},
		{"float nonzero", float64(0.5), true},

		{"json.Number zero", json.Number("0"), false},
		{"json.Number float zero", json.Number("0.0"), false},
		{"json.Number nonzero", json.Number("3"), true},
		// Past float64's range: still a number, still not zero. Falling
		// back to false on a parse failure would make bool(10**400) False,
		// which Python does not say.
		{"json.Number beyond float64", json.Number("1e400"), true},

		{"empty slice", []any{}, false},
		{"nil slice", []any(nil), false},
		{"nonempty slice", []any{0}, true},
		{"empty map", map[string]any{}, false},
		{"nonempty map", map[string]any{"k": nil}, true},
		{"empty Obj", Obj{}, false},
		{"nonempty Obj", Obj{{Key: "k", Val: nil}}, true},
		{"empty List", List{}, false},
		{"nonempty List", List{nil}, true},

		// An unrecognized type is TRUE in Python (no __bool__, no __len__).
		// graduation's private copy ended `return v != nil`, which is a
		// different rule AND a Go trap: a typed nil in an interface is not
		// nil, so it would have read true there and false under Python's.
		{"an unrecognized type", struct{ A int }{}, true},
		{"a typed nil pointer", (*int)(nil), true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := Truthy(c.v); got != c.want {
				t.Fatalf("Truthy(%#v) = %v, want %v", c.v, got, c.want)
			}
		})
	}
}

// Truthy and Bool answer DIFFERENT questions, and the whole reason both
// exist is that using the wrong one flips a gate silently. Bool is
// `v is True` for a field our own writer stores as a real bool; Truthy is
// `bool(v)` for a field anything else supplies.
//
// This test exists because that distinction was got wrong in mission-r9:
// notify aliased its truthiness helper to Bool under a comment claiming
// bool(), and a `goal_verdict_source: "judge"` read as false. The two must
// keep disagreeing on exactly these inputs — if they ever agree
// everywhere, one of them has silently become the other.
func TestTruthyAndBoolDisagreeWhereTheyShould(t *testing.T) {
	disagree := []any{
		"judge", // the r9 bug, verbatim
		"false", // truthy string, not a real bool
		float64(1), int(1), json.Number("1"),
		[]any{0}, map[string]any{"k": nil},
	}
	for _, v := range disagree {
		if !Truthy(v) {
			t.Errorf("Truthy(%#v) is false; this case no longer separates them", v)
		}
		if Bool(v) {
			t.Errorf("Bool(%#v) is true; it is supposed to be `v is True`", v)
		}
	}
	// And they must AGREE on real bools, or Truthy has broken the callers
	// that legitimately hold one.
	for _, v := range []any{true, false} {
		if Truthy(v) != Bool(v) {
			t.Errorf("Truthy and Bool disagree on the real bool %v", v)
		}
	}
}

// StrOrEmpty is `str(v or "")`, and it exists because Str alone was
// wrong at three sites in one tranche. The table is the two halves of
// that expression checked separately: the truthiness gate (which is
// Truthy's table again, so only the surprising members are repeated) and
// the str() that runs on what survives it.
func TestStrOrEmptyIsPythonsStrOfXOrEmpty(t *testing.T) {
	cases := []struct {
		name string
		v    any
		want string
	}{
		// The three bugs this helper was written for: str(None) is the
		// four-character string "None", not "".
		{"nil", nil, ""},
		{"empty string", "", ""},
		{"false", false, ""},
		// Falsy numbers and containers go the same way. An id of integer
		// zero really does vanish in Python; that is the behaviour, not a
		// rounding of it.
		{"int zero", 0, ""},
		{"float zero", float64(0), ""},
		{"json.Number zero", json.Number("0"), ""},
		{"empty slice", []any{}, ""},
		{"empty map", map[string]any{}, ""},
		{"empty Obj", Obj{}, ""},
		// What survives the gate is spelled with str(), so a number
		// becomes its digits rather than being dropped for not being a
		// string.
		{"a string passes through", "loop-1", "loop-1"},
		{"the string zero is truthy", "0", "0"},
		{"int", 5, "5"},
		{"json.Number", json.Number("7"), "7"},
		{"true", true, "True"},
		{"float", 1.5, "1.5"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if got := StrOrEmpty(c.v); got != c.want {
				t.Fatalf("StrOrEmpty(%#v) = %q, want %q", c.v, got, c.want)
			}
		})
	}
	// The distinction that makes the helper necessary, asserted directly:
	// if Str ever starts answering "" for nil, this helper is redundant
	// and the comments pointing at it are wrong.
	if Str(nil) != "None" {
		t.Errorf("Str(nil) = %q; the whole reason StrOrEmpty exists is that "+
			"Python's str(None) is \"None\"", Str(nil))
	}
}
