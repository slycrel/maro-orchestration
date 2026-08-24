package inspector

import (
	"os"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// TestReportRowKeyOrderIsToDicts pins the two order claims the renderer
// itself cannot check: InspectionReport.to_dict()'s field sequence, and
// quality_distribution's good/fair/poor. The second is the one that was
// wrong — a Go map has no order, so it came out of encoding/json sorted
// as fair/good/poor, and inspection-log.jsonl is read by both runtimes.
//
// A golden string rather than a CPython diff on purpose: CPython can say
// how a dict RENDERS (pyval's TestDumpsMatchesJSONDumps does that over
// this exact machinery) but not what order inspector.py builds it in.
// That claim comes from reading inspector.py:313-322 and 879, and this
// is where it is written down so a reordering fails a test.
func TestReportRowKeyOrderIsToDicts(t *testing.T) {
	got, err := pyval.DumpsCompactPy(reportRow(InspectionReport{
		RunID:               "insp-1",
		InspectedSessions:   2,
		QualityDistribution: map[string]int{"poor": 3, "good": 1, "fair": 2},
		TopFrictionSignals: []map[string]any{
			{"signal_type": "retry_loop", "count": 4, "severity": "high"},
		},
		AlignmentScoreAvg: 0.75,
		Patterns:          []string{"p"},
		Suggestions:       []string{"s > t"},
		ThresholdBreaches: []string{"b"},
		ElapsedMS:         12,
		GeneratedAt:       "2026-08-23T00:00:00Z",
	}))
	if err != nil {
		t.Fatal(err)
	}
	want := `{"run_id": "insp-1", "inspected_sessions": 2, ` +
		`"quality_distribution": {"good": 1, "fair": 2, "poor": 3}, ` +
		`"top_friction_signals": [{"count": 4, "severity": "high", ` +
		`"signal_type": "retry_loop"}], "alignment_score_avg": 0.75, ` +
		`"patterns": ["p"], "suggestions": ["s > t"], ` +
		`"threshold_breaches": ["b"], "elapsed_ms": 12, ` +
		`"generated_at": "2026-08-23T00:00:00Z"}`
	if got != want {
		t.Fatalf("report row:\n got %s\nwant %s", got, want)
	}
	// The `>` is in the fixture because it is the character encoding/json
	// escapes and json.dumps does not; without one in the corpus this
	// test would pass against the pre-fix writer for everything but the
	// key order.
	if !strings.Contains(got, "s > t") {
		t.Fatal("suggestion text must survive unescaped")
	}
	// NAMED RESIDUAL, pinned so it is not mistaken for parity:
	// top_friction_signals is a []map[string]any and comes back
	// key-SORTED (count, severity, signal_type) where Python builds it
	// signal_type, count, severity. Closing it means an ordered row at
	// the builder — inspector.go:764 — which is a change to the analysis
	// path, not the writer, and is left for the round that touches it.
	if !strings.Contains(got, `[{"count": 4, "severity": "high", "signal_type": "retry_loop"}]`) {
		t.Fatal("the sorted-signal-row residual changed shape; update the note")
	}
}

// TestSaveSuggestionsWritesJSONDumpsLines: suggestions.jsonl is read by
// the evolver in BOTH runtimes, and Python writes it with a bare
// json.dumps — ONE line, `", "` and `": "` separators, ensure_ascii on,
// no HTML escaping. Nothing read these bytes, so r7's battery swapped the
// writer to indent-2 and the whole inspector package stayed green.
func TestSaveSuggestionsWritesJSONDumpsLines(t *testing.T) {
	ws := t.TempDir()
	// The suggestion text carries both hazards at once: `>` (which
	// encoding/json escapes and json.dumps does not) and a non-ASCII
	// letter (which json.dumps escapes and encoding/json does not).
	if err := saveSuggestions(ws, []string{"prefer a > b in the café path"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(suggestionsPath(ws))
	if err != nil {
		t.Fatal(err)
	}
	line := strings.TrimRight(string(raw), "\n")
	if strings.Contains(line, "\n") {
		t.Fatalf("a suggestion row must be ONE line:\n%s", line)
	}
	for _, want := range []string{
		`"category": "inspection_finding"`, // json.dumps' key separator
		`"applied": false`,                 // ...and its item separator
		`prefer a > b`,                     // NOT HTML-escaped
		`caf\u00e9`,                        // ensure_ascii IS on
	} {
		if !strings.Contains(line, want) {
			t.Fatalf("suggestion row is not json.dumps-shaped (missing %s):\n%s",
				want, line)
		}
	}
	// And the 9-field Python schema, in Python's order — the same claim
	// TestReportRowKeyOrderIsToDicts makes for the report row.
	parsed, err := pyval.LoadsOrdered(line)
	if err != nil {
		t.Fatalf("row is not loadable: %v", err)
	}
	o, ok := parsed.(pyval.Obj)
	if !ok {
		t.Fatalf("row is not an object: %s", line)
	}
	want := []string{"suggestion_id", "category", "target", "suggestion",
		"failure_pattern", "confidence", "outcomes_analyzed", "generated_at",
		"applied"}
	if len(o) != len(want) {
		t.Fatalf("row has %d fields, want the 9-field schema:\n%s", len(o), line)
	}
	for i, k := range want {
		if o[i].Key != k {
			t.Fatalf("field %d is %q, want %q:\n%s", i, o[i].Key, k, line)
		}
	}
}
