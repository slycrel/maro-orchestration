package syshealth

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// A KNOWN GAP, pinned rather than closed — the same convention
// internal/handlequeue uses for the same underlying residual.
//
// `cycle` is the one field in this snapshot that is GUARANTEED to grow: it
// is `int(snapshot.get("cycle", 0) or 0) + 1` on every pass. CPython's int is
// arbitrary precision, so a counter sitting at 9223372036854775807 simply
// becomes 9223372036854775808 and the cycle proceeds — the snapshot is
// written, the narrations are performed, the summary reports them. A Go int
// cannot hold that value.
//
// r2 found that the port's increment was UNGUARDED, so the value wrapped to
// -9223372036854775808 and was WRITTEN. That is the one outcome
// pyval.ErrIntTooLarge's doc rules out for a value that gets written, and it
// is worse than the refusal in the way that matters: a wrapped counter is
// durable and silent, while a refusal shows up in `summary.error` and stops
// the write. So the increment now takes the refusal, which is the lane a
// counter already PAST int64 has always taken through pyval.Int.
//
// The gap is the refusal itself, and it is asserted here so that closing it
// — a big-int carrier through pyval, which nothing in this port needs yet —
// fails a test rather than passing unnoticed.
//
// Note what does NOT diverge: RenderSnapshot prints such a counter fine,
// because pyval.reprNumber keeps an integer literal verbatim instead of
// forcing it through an int64. Only the WRITER is bounded, and the render
// case below pins that asymmetry so a future "fix" that clamps in the
// renderer is caught too.
func TestACycleCounterAtTheInt64CeilingIsRefusedWhereCPythonCountsOn(t *testing.T) {
	ceiling := "9223372036854775807"
	if math.MaxInt != math.MaxInt64 {
		t.Skipf("this pin assumes a 64-bit int (math.MaxInt = %d)", math.MaxInt)
	}

	for _, c := range []struct {
		name string
		raw  string
	}{
		{"the counter exactly at MaxInt64", ceiling},
		{"a counter already past int64", "99999999999999999999"},
		{"a float literal past int64", "1e19"},
		{"a decimal STRING at MaxInt64", `"` + ceiling + `"`},
	} {
		t.Run(c.name, func(t *testing.T) {
			var v any
			dec := json.NewDecoder(strings.NewReader(c.raw))
			dec.UseNumber()
			if err := dec.Decode(&v); err != nil {
				t.Fatal(err)
			}
			snap := pyval.Obj{{Key: "cycle", Val: v}}
			_, err := nextCycle(snap)
			if !errors.Is(err, pyval.ErrIntTooLarge) {
				t.Fatalf("nextCycle(%s) = %v; the known gap is that this "+
					"port REFUSES the increment CPython computes. If it now "+
					"succeeds the gap is CLOSED — delete this test and add "+
					"the agreeing behaviour to the cycle differential "+
					"instead", c.raw, err)
			}
		})
	}

	// The other half: the value the writer refuses, the renderer prints.
	// pyval.reprNumber keeps the literal, so this must NOT be clamped.
	var big any
	dec := json.NewDecoder(strings.NewReader("99999999999999999999"))
	dec.UseNumber()
	if err := dec.Decode(&big); err != nil {
		t.Fatal(err)
	}
	out, err := RenderSnapshot(pyval.Obj{
		{Key: "cycle", Val: big},
		{Key: "updated_at", Val: "2026-08-26T00:00:00+00:00"},
		// A process is required: an EMPTY snapshot renders the "no snapshot
		// yet" placeholder and never reaches the header carrying the count.
		{Key: "processes", Val: pyval.Obj{
			{Key: "p1", Val: pyval.Obj{{Key: "status", Val: "OK"}}},
		}},
	})
	if err != nil {
		t.Fatalf("RenderSnapshot raised %v on a counter it only prints", err)
	}
	if !strings.Contains(out, "99999999999999999999") {
		t.Fatalf("the renderer lost the arbitrary-precision counter; it "+
			"prints the literal verbatim and must keep doing so.\n%s", out)
	}
}

// TestAStringSliceIsALiSTEverywherePyvalSaysItIs closes the arm r2 found
// missing from asList.
//
// pyval knows exactly two Go-native container shapes — `map[string]any` for
// a dict and `[]any`/`[]string` for a list — and its Truthy, TypeName and
// Repr all agree that a []string is a list. asList did not, so a process
// `status` of []string fell past the rank switch's unhashable arm into
// `default` and quietly RANKED 3, where CPython raises. There is no
// differential fixture for this because there cannot be one: the probe feeds
// both runtimes through JSON, which never produces a []string. It is the
// hand-built caller's lane, and this is the only door it has.
func TestAStringSliceIsAListEverywherePyvalSaysItIs(t *testing.T) {
	for _, c := range []struct {
		name   string
		status any
	}{
		{"a pyval.List status", pyval.List{"OK"}},
		{"an []any status", []any{"OK"}},
		{"a []string status", []string{"OK"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			_, err := RenderSnapshot(pyval.Obj{
				{Key: "cycle", Val: 1},
				{Key: "updated_at", Val: "2026-08-26T00:00:00+00:00"},
				{Key: "processes", Val: pyval.Obj{
					{Key: "p1", Val: pyval.Obj{{Key: "status", Val: c.status}}},
				}},
			})
			var pe *pyval.PyErr
			if !errors.As(err, &pe) {
				t.Fatalf("RenderSnapshot returned %v; `order.get(a list)` is "+
					"a TypeError in CPython, not a rank-3 miss", err)
			}
			want := "cannot use 'list' as a dict key (unhashable type: 'list')"
			if pe.Class != "TypeError" || pe.Msg != want {
				t.Fatalf("raised %s: %q\nwant TypeError: %q",
					pe.Class, pe.Msg, want)
			}
		})
	}

	// asList itself, so the arm is pinned at the helper and not only through
	// the one caller that happened to expose it.
	got, ok := asList([]string{"a", "b"})
	if !ok || len(got) != 2 || got[0] != "a" || got[1] != "b" {
		t.Fatalf("asList([]string{a,b}) = %#v, %v", got, ok)
	}
}
