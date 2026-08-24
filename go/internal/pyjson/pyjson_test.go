package pyjson

import (
	"encoding/json"
	"strings"
	"testing"
)

// The FIVE ways encoding/json differs from json.dumps, each of which
// produces a byte-different row for an identical value.
//
// This test used to name three, and pinned the compact spelling the
// package then emitted. Two forks — json.dumps' `, ` / `: ` separators and
// ensure_ascii — were neither implemented nor tested, so every store
// routed through this package wrote them wrong and this test reported
// agreement (mission-r8). The fixture now carries a non-ASCII rune so the
// fifth fork cannot go quiet again.
func TestOrderedFixesTheFiveEncodingJSONDifferences(t *testing.T) {
	d := map[string]any{
		"z_last": "café", "a_first": 1.0, "html": `<b>&"'</b>`,
	}
	got, err := Ordered(d, []string{"z_last", "a_first", "html"})
	if err != nil {
		t.Fatal(err)
	}
	want := `{"z_last": "caf\u00e9", "a_first": 1.0, "html": "<b>&\"'</b>"}`
	if got != want {
		t.Fatalf("\nwant %s\ngot  %s", want, got)
	}
	// For contrast, what the stdlib would have written — the exact bytes
	// this package exists to avoid.
	raw, _ := json.Marshal(d)
	if string(raw) == want {
		t.Fatal("the stdlib now matches; this package's reason to exist changed")
	}
}

// Unknown keys ride AFTER the modeled ones, sorted, so a forward-version
// field or an operator's note survives a rewrite deterministically rather
// than landing in map order.
func TestOrderedCarriesUnknownKeysSortedAtTheEnd(t *testing.T) {
	d := map[string]any{"b": 1.0, "a": 2.0, "zz_note": "hand", "mm_extra": "x"}
	got, err := Ordered(d, []string{"b", "a"})
	if err != nil {
		t.Fatal(err)
	}
	if got != `{"b": 1.0, "a": 2.0, "mm_extra": "x", "zz_note": "hand"}` {
		t.Fatalf("got %s", got)
	}
	// A modeled key the row does not carry is skipped, not emitted as null:
	// stats rows are sparse upserts.
	got, err = Ordered(map[string]any{"a": 1.0}, []string{"a", "absent"})
	if err != nil {
		t.Fatal(err)
	}
	if got != `{"a": 1.0}` {
		t.Fatalf("an absent modeled key must be skipped: %s", got)
	}
}

// The key-order literals callers pass are package-level slices. Appending
// the extras onto the CALLER's slice would write into its backing array
// whenever it has spare capacity, so two concurrent marshals could corrupt
// each other's key order — and today's literals only avoid it by having
// len == cap.
func TestOrderedDoesNotWriteIntoTheCallersKeyOrder(t *testing.T) {
	modeled := make([]string, 2, 8) // deliberate spare capacity
	modeled[0], modeled[1] = "a", "b"
	if _, err := Ordered(map[string]any{"a": 1.0, "b": 2.0, "extra": "x"},
		modeled); err != nil {
		t.Fatal(err)
	}
	if got := modeled[:cap(modeled)]; got[2] != "" {
		t.Fatalf("the caller's backing array was written: %q", got)
	}
}

// json.dumps spells a whole float with its ".0" (float.__repr__), and
// success_rate is inside Python's doctor dedup identity — a Go row and a
// Python row describing the same skill must compare equal there.
func TestValueSpellsFloatsPythonsWay(t *testing.T) {
	for in, want := range map[float64]string{
		1: "1.0", 0: "0.0", -1: "-1.0", 0.5: "0.5", 0.1: "0.1",
	} {
		got, err := Value(in)
		if err != nil {
			t.Fatal(err)
		}
		if got != want {
			t.Errorf("%v → %s, want %s", in, got, want)
		}
	}
	// A stored LITERAL keeps its own spelling: 7 stays 7, not 7.0.
	if got, _ := Value(json.Number("7")); got != "7" {
		t.Errorf("literal: %s", got)
	}
}

// A number Python would parse to inf or nan must be refused wherever it
// sits: Python's json.dumps(allow_nan=False) refuses the whole write, and
// an emitter that only checks the top level lets a nested one through.
func TestRefuseNonFiniteReachesNestedValues(t *testing.T) {
	for _, v := range []any{
		json.Number("1e400"),
		map[string]any{"a": map[string]any{"b": json.Number("-1e400")}},
		[]any{json.Number("1"), json.Number("1e999")},
	} {
		if err := RefuseNonFinite(v); err == nil {
			t.Errorf("%#v must be refused", v)
		}
	}
	// An integer literal is never non-finite however long it is: Python's
	// ints are unbounded, json.loads keeps it exact, json.dumps re-emits it.
	big := json.Number(strings.Repeat("9", 400))
	if err := RefuseNonFinite(big); err != nil {
		t.Errorf("a big integer literal is exact in Python: %v", err)
	}
	if err := RefuseNonFinite(map[string]any{"n": big}); err != nil {
		t.Errorf("nested big integer: %v", err)
	}
}

func TestIsCleanTextRefusesWhatCannotRoundTrip(t *testing.T) {
	if IsCleanText("\xff") {
		t.Error("raw non-UTF-8 must be refused")
	}
	// A surrogate's UTF-8 encoding (CESU-8 style) — refused by the validity
	// check, since no well-formed UTF-8 encodes one. Go cannot even build a
	// string holding a decoded surrogate: the rune conversion substitutes
	// U+FFFD. The explicit surrogate scan in IsCleanText is belt-and-braces
	// for that reason, not a second reachable door.
	if IsCleanText("\xed\xa0\x80") {
		t.Error("a surrogate encoding must be refused")
	}
	// U+FFFD is admitted: web-fetched text carries it legitimately, and a
	// writer stricter than its own reader FREEZES good rows rather than
	// preventing bad ones.
	if !IsCleanText("caf�") {
		t.Error("U+FFFD must be admitted")
	}
	if _, err := String("\xff"); err == nil {
		t.Error("String must refuse byte-tainted text")
	}
}
