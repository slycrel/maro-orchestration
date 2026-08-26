package metrics

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

const pyLoadSrc = `
import json, os, sys
import metrics

cases = json.loads(sys.argv[1])
out = []
for c in cases:
    _pyprobe_use(c["ws"])
    import importlib, orch_items
    importlib.reload(orch_items)
    importlib.reload(metrics)
    rows = metrics.load_step_costs(limit=c["limit"])
    # The MARKER field, verbatim and in order, is what identifies which rows
    # came back and in which direction. Comparing whole dicts would pass for
    # a loader that returned the right rows reversed.
    out.append([r.get("id") for r in rows])
print(json.dumps(out))
`

// TestLoadStepCostsMatchesCPython gives the loader its own differential.
//
// It had none: every assertion about it ran through
// TestAnalyzeStepCostsMatchesCPython, which consumes the rows as an
// unordered grouping and so could not see the two properties that make this
// function what it is —
//
//  1. rows come back NEWEST FIRST (`list(reversed(read_jsonl_tail(...)))`),
//     which is what puts the file's LAST row at the head of every sum and
//     therefore decides which type name lands in a TypeError message;
//  2. `limit <= 0` means EVERY row, not none, because read_jsonl_tail takes
//     its full-scan path for a non-positive limit.
//
// Both are the kind of rule a port inverts while staying plausible, and an
// analysis-level test agrees with the inversion.
func TestLoadStepCostsMatchesCPython(t *testing.T) {
	row := func(id string) string {
		return `{"id": "` + id + `", "step_type": "research", ` +
			`"total_tokens": 10, "cost_usd": 0.01}`
	}
	var many []string
	for i := 0; i < 12; i++ {
		many = append(many, row("r"+itoa(i)))
	}

	cases := []struct {
		name  string
		rows  []string
		limit int
	}{
		{"an empty store", nil, 100},
		{"one row", []string{row("only")}, 100},
		// Order, with a limit wider than the store.
		{"three rows newest first", []string{row("a"), row("b"), row("c")}, 100},
		// A limit that CUTS: the newest three of twelve, not the oldest.
		{"a limit inside the store", many, 3},
		{"a limit of one", many, 1},
		{"a limit equal to the store", many, 12},
		// ZERO AND NEGATIVE mean everything, not nothing. This is the rule
		// that reads backwards and the only place it is stated.
		{"a limit of zero loads every row", many, 0},
		{"a negative limit loads every row", many, -5},
		// A malformed row is SKIPPED, not fatal, and does not consume a slot
		// of the limit in a way that hides a good row below it.
		{"a torn row among good ones",
			[]string{row("a"), `{"id": "torn", "cost`, row("c")}, 100},
		{"a torn row at the end",
			[]string{row("a"), row("b"), `{"id": "torn`}, 100},
		// A blank line, which the tail reader drops before json ever sees it.
		{"blank lines", []string{row("a"), "", row("c")}, 100},
		// A row that is valid JSON but NOT an object. Python's loader keeps
		// whatever json.loads returned unless it filters, so this pins which.
		{"a non-object row", []string{row("a"), `[1, 2, 3]`, row("c")}, 100},
		// Multibyte content, so the tail reader's chunking is exercised on
		// something a byte-oriented port could split.
		{"multibyte rows", []string{
			`{"id": "` + strings.Repeat("あ", 5) + `", "cost_usd": 0.1}`,
			row("b")}, 100},
	}

	type loadCase struct {
		WS    string `json:"ws"`
		Limit int    `json:"limit"`
	}
	payload := make([]loadCase, len(cases))
	for i, c := range cases {
		payload[i] = loadCase{WS: seedCosts(t, c.rows, true), Limit: c.limit}
	}

	var want [][]any
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(payload)}
	probe.RunJSON(t, pyLoadSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	// L1: at least one case must come back with more than one row, or the
	// ORDER — the main subject here — was never compared.
	multi := 0
	for _, w := range want {
		if len(w) > 1 {
			multi++
		}
	}
	if multi == 0 {
		t.Fatal("no case returned more than one row — the fixtures cannot " +
			"tell newest-first from oldest-first")
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := LoadStepCosts(payload[i].WS, c.limit)
			var gotIDs []any
			for _, o := range got {
				gotIDs = append(gotIDs, objGet(o, "id", nil))
			}
			if len(gotIDs) != len(want[i]) {
				t.Fatalf("cpython returned %d rows %v, go returned %d %v",
					len(want[i]), want[i], len(gotIDs), gotIDs)
			}
			for j := range gotIDs {
				if pyval.Repr(gotIDs[j]) != pyval.Repr(want[i][j]) {
					t.Errorf("row %d: cpython %v, go %v",
						j, want[i][j], gotIDs[j])
				}
			}
		})
	}
}
