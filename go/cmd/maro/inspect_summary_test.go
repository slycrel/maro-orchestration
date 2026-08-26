package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// `maro inspect -summary` against `cli.py inspector-status`, byte for byte.
//
// This is the comparison row that found the bug, promoted to a test. The
// both-engines harness (go/tools/engine-compare.py) ran the two commands
// over a copy of the live workspace and got:
//
//	+workspace: /.../cmp_ws
//	 Inspector (10857e8a): 50 sessions — good=4 fair=29 poor=17 …
//
// The summary CONTENT was already identical; the divergence was a line
// the Go CLI printed above it, from a "print the workspace before any
// write" discipline in a lane that performs no write. A comparison row is
// not a test — it runs against whatever the live workspace happens to
// hold, and it disappears the moment nobody runs the tool. This does not.
//
// The empty lane is here for the second thing the same row could not see:
// the live workspace HAS a report, so the two engines' empty-state strings
// ("No inspection report available. Run maro-inspector first." vs "no
// inspection recorded yet") never met.
const inspectorStatusPySrc = `
import contextlib, io, json, os, sys

out = []
for i in range(int(sys.argv[2])):
    _pyprobe_use(os.path.join(sys.argv[1], "ws%d" % i))
    import cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["inspector-status"])
    out.append(buf.getvalue())
print(json.dumps(out))
`

func TestInspectSummaryMatchesInspectorStatusByteForByte(t *testing.T) {
	report := map[string]any{
		"run_id":               "10857e8a",
		"inspected_sessions":   50,
		"quality_distribution": map[string]any{"good": 4, "fair": 29, "poor": 17},
		"top_friction_signals": []map[string]any{
			{"signal_type": "stuck_loop", "count": 7, "severity": "high"},
		},
		"alignment_score_avg": 0.43,
		"patterns":            []any{},
		"suggestions":         []any{},
		"threshold_breaches":  []any{"alignment_avg 0.43 below 0.60"},
		"elapsed_ms":          12,
		"generated_at":        "2026-08-26T00:00:00+00:00",
	}

	cases := []struct {
		name string
		// row nil means: no inspection-log.jsonl at all, which is the
		// empty lane. An empty memory/ directory is NOT the same input and
		// would not reach the same branch.
		row map[string]any
	}{
		{"a workspace with a report", report},
		{"a workspace with no inspection log", nil},
	}

	root := t.TempDir()
	spaces := make([]string, len(cases))
	for i := range cases {
		spaces[i] = filepath.Join(root, "ws"+strconv.Itoa(i))
		if err := os.MkdirAll(filepath.Join(spaces[i], "memory"), 0o775); err != nil {
			t.Fatal(err)
		}
		if cases[i].row == nil {
			continue
		}
		raw, err := json.Marshal(cases[i].row)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(spaces[i], "memory", "inspection-log.jsonl"),
			append(raw, '\n'), 0o664); err != nil {
			t.Fatal(err)
		}
	}

	var want []string
	pyprobe.Probe{Marker: "cli.py", Workspaces: spaces}.
		RunJSON(t, inspectorStatusPySrc, &want,
			root, strconv.Itoa(len(cases)))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	// Anti-vacuity. Two independent ways this test could report agreement
	// while proving nothing: CPython printing nothing at all (an exception
	// swallowed into an empty buffer would still compare equal to a Go
	// lane that also printed nothing), and the populated case degenerating
	// into the empty one.
	if strings.TrimSpace(want[0]) == "" {
		t.Fatalf("CPython printed nothing for the populated workspace — the "+
			"probe is not exercising the lane this test is about: %q", want)
	}
	if want[0] == want[1] {
		t.Fatalf("both fixtures produced the same CPython output (%q) — the "+
			"populated and empty lanes are not being told apart", want[0])
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv("MARO_WORKSPACE", spaces[i])
			got, err := captureStdout(t, func() error { return runInspect([]string{"-summary"}) })
			if err != nil {
				t.Fatalf("inspect -summary: %v", err)
			}
			if got != want[i] {
				t.Errorf("inspect -summary differs from cli.py inspector-status:\n"+
					" go %q\n py %q", got, want[i])
			}
		})
	}
}
