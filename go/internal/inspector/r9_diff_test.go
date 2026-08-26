package inspector

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The friction-summary line, measured against CPython.
//
// This differential exists because the both-engines comparison
// (go/tools/engine-compare.py) found the divergence, not a test: the
// seventh row, `inspector-status`, was the first row to differ, and the
// live workspace's `count` happened to be 2 — a value on which the two
// renderings AGREE. The fixtures below are chosen so agreement on 2
// cannot carry the test.
//
//	count      Python     Go before this fix (%v over float64)
//	2          2          2            agree
//	1000000    1000000    1e+06        DIVERGE
//	1e21       1e+21      1e+21        agree by accident
//
// The cause is the DECODE, not the format verb: `top_friction_signals` is
// a []map[string]any, and a plain json.Unmarshal types every number in it
// as float64. json.loads types `1000000` as an int. Fixing the verb alone
// would have moved the wrongness, not removed it — a float64 has already
// forgotten it was written without a decimal point.

// frictionPySrc writes ONE inspection row per case into a fresh workspace
// and asks CPython what get_friction_summary() prints.
//
// Backtick-free on purpose: a backtick in this source would terminate the
// Go raw string literal that carries it. The `inspector` import sits
// INSIDE the loop, after the first _pyprobe_use — importing it while
// MARO_WORKSPACE is still unset would resolve module-level paths against
// the operator's live workspace.
const frictionPySrc = `
import json, os, sys

rows = json.loads(sys.argv[1])
out = []
for i, row in enumerate(rows):
    ws = _pyprobe_use(os.path.join(sys.argv[2], "ws%d" % i))
    mem = os.path.join(ws, "memory")
    os.makedirs(mem, exist_ok=True)
    with open(os.path.join(mem, "inspection-log.jsonl"), "w") as f:
        f.write(json.dumps(row) + "\n")
    from inspector import get_friction_summary
    out.append(get_friction_summary())
print(json.dumps(out))
`

// frictionRow builds a fixture row. Every field is spelled out rather than
// defaulted: from_dict is all `.get(k, default)`, so an omitted key asks a
// DIFFERENT question (what the default renders as) and belongs in its own
// case rather than smuggled into this one.
func frictionRow(runID string, sessions int, top []map[string]any) map[string]any {
	return map[string]any{
		"run_id":               runID,
		"inspected_sessions":   sessions,
		"quality_distribution": map[string]any{"good": 4, "fair": 29, "poor": 17},
		"top_friction_signals": top,
		"alignment_score_avg":  0.43,
		"patterns":             []any{},
		"suggestions":          []any{},
		"threshold_breaches":   []any{},
		"elapsed_ms":           12,
		"generated_at":         "2026-08-26T00:00:00+00:00",
	}
}

func frictionSig(kind string, count, sev any) []map[string]any {
	return []map[string]any{{"signal_type": kind, "count": count, "severity": sev}}
}

func TestFrictionSummaryRendersCountsTheWayPythonDoes(t *testing.T) {
	cases := []struct {
		name string
		row  map[string]any
	}{
		// The value the live workspace happened to hold. Kept, so the test
		// still covers the case the comparison DID agree on.
		{"a small integer count", frictionRow("10857e8a", 50, frictionSig("stuck_loop", 2, "high"))},
		// The discriminating one. json.Marshal writes this as `1000000`,
		// which json.loads types as an int and a plain Unmarshal types as
		// float64 — whose default verb is %g.
		{"a count large enough to reach %g's exponent",
			frictionRow("r2", 50, frictionSig("stuck_loop", 1000000, "high"))},
		// Past float64's integer range: Python has no int width, so the
		// literal survives; anything routed through a float64 does not.
		{"a count past 2^53",
			frictionRow("r3", 50, frictionSig("stuck_loop", json.Number("9007199254740993"), "medium"))},
		// A genuinely fractional count. Nothing writes one, but the fix
		// must not turn every number into an integer either — the old
		// behaviour was wrong in one direction and an int-only fix is
		// wrong in the other.
		{"a fractional count", frictionRow("r4", 50, frictionSig("stuck_loop", 1.5, "low"))},
		// Zero is the absent-vs-zero edge: count=0 must print 0, not
		// vanish the way a truthiness-gated render would make it.
		{"a zero count", frictionRow("r5", 50, frictionSig("stuck_loop", 0, "low"))},
		// Non-ASCII in both string fields, so the render is not silently
		// ASCII-only.
		{"a non-ASCII signal type", frictionRow("r6", 50, frictionSig("café_signal", 3, "höch"))},
		// The next four separate `pyval.Str` from a bare `%v` over the
		// json.Number. UseNumber alone makes %v right for every literal
		// that is ALREADY spelled the way Python respells it, which is
		// most of them — so a fixture set without these reports agreement
		// on the decode fix and says nothing about the render. Each of
		// these is a literal json.loads RE-SPELLS:
		//   1e21 -> float 1e+21 -> "1e+21"   (%v: "1e21")
		{"an exponent literal without a sign",
			frictionRow("r9", 50, frictionSig("stuck_loop", json.Number("1e21"), "low"))},
		//   1.50 -> float 1.5   -> "1.5"     (%v: "1.50")
		{"a trailing-zero literal",
			frictionRow("r10", 50, frictionSig("stuck_loop", json.Number("1.50"), "low"))},
		//   -0   -> int 0       -> "0"       (%v: "-0")
		{"a negative-zero literal",
			frictionRow("r11", 50, frictionSig("stuck_loop", json.Number("-0"), "low"))},
		// And the non-number case: a null severity is str(None) == "None"
		// in Python and "<nil>" through Go's default verb. Nothing writes
		// one, but from_dict is all `.get`, so nothing stops one either.
		{"a null severity", frictionRow("r12", 50, frictionSig("stuck_loop", 3, nil))},
		// No signals at all: the second line must be absent, not empty.
		{"no friction signals", frictionRow("r7", 50, nil)},
		// inspected_sessions == 0 short-circuits before any of the above.
		{"no sessions inspected", frictionRow("r8", 0, frictionSig("stuck_loop", 2, "high"))},
	}

	root := t.TempDir()
	rows := make([]map[string]any, len(cases))
	spaces := make([]string, len(cases))
	for i, c := range cases {
		rows[i] = c.row
		spaces[i] = filepath.Join(root, "ws"+strconv.Itoa(i))
	}

	var want []string
	pyprobe.Probe{Marker: "inspector.py", Workspaces: spaces}.
		RunJSON(t, frictionPySrc, &want, pyprobe.Arg(t, rows), root)
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	// Anti-vacuity, derived from CPython's OWN answers rather than
	// asserted by eye: the fixture set is worthless unless it contains
	// counts that a float64 could not have produced. Both discriminating
	// digit strings must survive into the expected text.
	reached := 0
	for _, w := range want {
		if strings.Contains(w, "count=1000000 ") || strings.Contains(w, "count=9007199254740993 ") {
			reached++
		}
	}
	if reached < 2 {
		t.Fatalf("the fixtures no longer carry the divergence they were written "+
			"for — only %d of the 2 discriminating counts survived CPython's own "+
			"render:\n%v", reached, want)
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			ws := writeInspectionRow(t, c.row)
			if got := FrictionSummary(ws); got != want[i] {
				t.Errorf("friction summary differs from CPython:\n go %q\n py %q", got, want[i])
			}
		})
	}
}

// TestATornNewestRowIsStillTorn pins the strictness a json.Decoder does
// NOT inherit from json.Unmarshal. Switching to UseNumber required a
// Decoder, and a Decoder stops at the end of the first value — so
// `{...} junk` would have decoded cleanly where json.loads raises "Extra
// data" and from_dict never runs.
//
// Derived from the FILE, not from the diff: the behaviour that has to
// survive is "the newest row is torn -> None", and these are the inputs
// that separate the two spellings of it.
func TestATornNewestRowIsStillTorn(t *testing.T) {
	good := `{"run_id":"x","inspected_sessions":1,` +
		`"quality_distribution":{"good":1,"fair":0,"poor":0},"alignment_score_avg":0.5}`
	for _, tc := range []struct {
		name string
		line string
		want bool // want a report back
	}{
		{"a whole row", good, true},
		{"a row with trailing content", good + " junk", false},
		{"a row followed by a second value", good + " " + good, false},
		{"a truncated row", good[:len(good)-3], false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ws := writeInspectionLine(t, tc.line)
			if got := GetLatestInspection(ws) != nil; got != tc.want {
				t.Errorf("GetLatestInspection returned report=%v, want %v, for %q",
					got, tc.want, tc.line)
			}
		})
	}
}

func writeInspectionRow(t *testing.T, row map[string]any) string {
	t.Helper()
	raw, err := json.Marshal(row)
	if err != nil {
		t.Fatal(err)
	}
	return writeInspectionLine(t, string(raw))
}

func writeInspectionLine(t *testing.T, line string) string {
	t.Helper()
	ws := t.TempDir()
	mem := filepath.Join(ws, "memory")
	if err := os.MkdirAll(mem, 0o775); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(mem, "inspection-log.jsonl"),
		[]byte(line+"\n"), 0o664); err != nil {
		t.Fatal(err)
	}
	return ws
}
