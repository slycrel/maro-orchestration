package budget

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// Clip's marker is written into records BOTH runtimes read, and until this
// file existed every test in the package was the port agreeing with itself:
// each expectation was transcribed from `context_budget.clip`'s source by
// the same person who wrote the port. That is the shape the whole chunk
// keeps finding — a test that reports agreement while measuring nothing
// outside its own runtime.
//
// So the expectation comes from CPython, over a table built from the
// FUNCTION rather than from the Go diff: the cap boundary from both sides,
// the idempotence short-circuit and both guards that bound it, the rune-vs-
// byte axis, marker-shaped payload text, and the anchor.
const pyClipSrc = `
import json, sys
import context_budget

out = []
for text, cap in json.loads(sys.argv[1]):
    out.append(context_budget.clip(text, cap))
print(json.dumps(out))
`

func TestClipIsByteIdenticalToPythons(t *testing.T) {
	marker := " … [truncated: first 2000 of 9999 characters]"
	cases := [][2]any{
		// Under, at, and one past the cap.
		{"short", 10},
		{strings.Repeat("a", 10), 10},
		{strings.Repeat("a", 11), 10},

		// len() is code points on both sides, so a wide-but-short string
		// must pass through untouched where a byte count would cut it.
		{strings.Repeat("é", 250), 400},
		{strings.Repeat("é", 500), 400},
		{strings.Repeat("😀", 300), 100},

		// The idempotence short-circuit: an already-clipped value keeps its
		// TRUE source length instead of being re-cut to "first N of N".
		{strings.Repeat("y", 2000) + marker, 2000},
		// ...at a WIDER cap (still passes through)...
		{strings.Repeat("y", 2000) + marker, 3000},
		// ...and at a strictly TIGHTER one, where it genuinely does not fit.
		{strings.Repeat("y", 2000) + marker, 500},

		// THE ANCHOR. Python's `$` (no re.MULTILINE) matches at end of
		// string OR before a single trailing newline; Go's `$` is `\z`. A
		// value ending in "marker\n" therefore passed through CPython's
		// idempotence check and got RE-CUT here, stamped with a source
		// length taken from the already-clipped text (r11 round 8, MEDIUM).
		{strings.Repeat("y", 2000) + marker + "\n", 2000},
		// TWO trailing newlines match neither — the bound of the fix, and
		// the case that fails if `\n?\z` is loosened to `(?m)$`.
		{strings.Repeat("y", 2000) + marker + "\n\n", 2000},
		// A newline BEFORE the marker is ordinary content either way.
		{strings.Repeat("y", 2000) + "\n" + marker, 2000},

		// Marker-shaped payload text, which the two guards exist for: the
		// forged tail is too long to ride the short-circuit, so both
		// runtimes cut.
		{strings.Repeat("z", 3000) + " … [truncated: first 1 of 2 characters]", 100},
		// A forged marker INSIDE the text is not at the anchor at all.
		{"head … [truncated: first 1 of 2 characters] tail", 10},
		// Digit runs past the {1,9} bound are not markers on either side.
		{strings.Repeat("y", 100) + " … [truncated: first 1234567890 of 9 characters]", 50},

		// str(text or "") — falsy values become the empty string BEFORE
		// the length test, which is why the Python signature is untyped.
		{"", 10},
	}

	var want []string
	pyprobe.Probe{Marker: "context_budget.py"}.RunJSON(t, pyClipSrc, &want,
		pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("CPython returned %d results for %d cases", len(want), len(cases))
	}

	cut := 0
	for i, c := range cases {
		text, cap := c[0].(string), c[1].(int)
		if got := Clip(text, cap); got != want[i] {
			t.Errorf("case %d Clip(len=%d, cap=%d)\n got %q\nwant %q (CPython)",
				i, len([]rune(text)), cap, tail(got), tail(want[i]))
		}
		if want[i] != text {
			cut++
		}
	}
	// Without this the whole table could be pass-throughs and every
	// assertion would still hold.
	if cut < 5 {
		t.Fatalf("only %d of %d cases actually changed under CPython; the "+
			"table is not exercising the cut", cut, len(cases))
	}
}

// tail keeps a failure message readable when the value is thousands of
// runes: the marker and the bytes around the cut are the whole story.
func tail(s string) string {
	r := []rune(s)
	if len(r) <= 70 {
		return s
	}
	return "…" + string(r[len(r)-70:])
}

// A KNOWN GAP, pinned rather than fixed. CPython's clip has no lower guard
// on its cap: the only test is `len(text) <= cap`, so a cap of 0 cuts every
// non-empty string to a bare marker, and a NEGATIVE cap slices from the END
// (`text[:-3]`) and then reports "first -3 of N characters" — a sentence
// describing something that did not happen.
//
// This port refuses instead: `limit <= 0` returns the text unchanged. That
// follows the standing caps decree (2026-07-29, strengthened 2026-08-21) —
// a cap is a circuit breaker, not a truncator, and destroying the only copy
// of a value because a config field was left at its zero value is the
// failure the decree exists to prevent. Zero is also the Go zero value,
// which CPython's keyword-only call site cannot produce by accident.
//
// The divergence is therefore deliberate and is recorded here so the next
// reader finds a measurement rather than an assumption. If a caller is ever
// found that depends on CPython's behaviour, this test is the place the
// decision gets revisited.
func TestAZeroOrNegativeCapDivergesFromCPythonOnPurpose(t *testing.T) {
	cases := [][2]any{
		{"hello", 0},
		{"hello", -3},
		{"", 0},
		{"", -3},
	}

	var want []string
	pyprobe.Probe{Marker: "context_budget.py"}.RunJSON(t, pyClipSrc, &want,
		pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("CPython returned %d results for %d cases", len(want), len(cases))
	}

	// What CPython actually does, measured — not transcribed. If a future
	// CPython grows a lower guard of its own, this half fails first and the
	// gap closes rather than silently inverting.
	wantPy := []string{
		"" + " … [truncated: first 0 of 5 characters]",
		"he" + " … [truncated: first -3 of 5 characters]",
		"",
		// NOT "": the guard is `len(text) <= cap`, and 0 is not <= -3, so
		// even the EMPTY string falls through and is stamped with a marker
		// claiming "first -3 of 0 characters". Transcribing this row from
		// the source got it wrong; the interpreter did not.
		" … [truncated: first -3 of 0 characters]",
	}
	for i := range wantPy {
		if want[i] != wantPy[i] {
			t.Errorf("CPython case %d = %q, this pin recorded %q — the gap "+
				"described below has moved", i, want[i], wantPy[i])
		}
	}

	// And what this port does instead: nothing.
	for _, c := range cases {
		text, cap := c[0].(string), c[1].(int)
		if got := Clip(text, cap); got != text {
			t.Errorf("Clip(%q, %d) = %q, want the input back unchanged; the "+
				"caps decree says a non-positive cap does not truncate",
				text, cap, got)
		}
	}
}
