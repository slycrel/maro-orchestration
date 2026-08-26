package introspect

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyLoadDiagnosesSrc drives load_diagnoses over a diagnoses.jsonl the test
// writes byte for byte, and returns the two renderings that drift
// independently: the loop_ids in order, and summary() per row.
//
// summary() is the assertion that matters. The loop_id list would pass for
// a port that coerced every mistyped field to a zero value, because the ids
// in these fixtures are all clean strings — it is `tokens=many` vs
// `tokens=0` in the summary that tells a faithful rehydration from a
// tidying one.
const pyLoadDiagnosesSrc = `
import json, sys
import introspect

lim = json.loads(sys.argv[1])["limit"]
rows = introspect.load_diagnoses(limit=lim)
print(json.dumps({
    "ids": [r.loop_id for r in rows],
    "summaries": [r.summary() for r in rows],
    "evidence": [list(r.evidence) if isinstance(r.evidence, list) else None
                 for r in rows],
}))
`

// pyLoopEventsSrc drives the two event-side readers together, because
// diagnose_latest is their composition and a port can get either one right
// on its own.
const pyLoopEventsSrc = `
import json, sys
import introspect

arg = json.loads(sys.argv[1])
out = {"latest": None, "latest_error": None, "matched": None, "match_error": None}
try:
    out["latest"] = introspect._load_latest_loop_id()
except Exception as e:
    out["latest_error"] = type(e).__name__
try:
    out["matched"] = [e.get("step_idx") for e in
                      introspect._load_loop_events(arg["loop_id"])]
except Exception as e:
    out["match_error"] = type(e).__name__
print(json.dumps(out))
`

func seedStore(t *testing.T, name string, lines []string) string {
	t.Helper()
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := ""
	if len(lines) > 0 {
		body = strings.Join(lines, "\n") + "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, name), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

// TestLoadDiagnosesMatchesCPython pins the rehydration rules, the
// newest-first order and the off-by-one that makes limit=0 return a row.
func TestLoadDiagnosesMatchesCPython(t *testing.T) {
	rows := []string{
		`{"loop_id": "a", "failure_class": "healthy", "severity": "info"}`,
		`{"loop_id": "b", "failure_class": "healthy", "severity": "info", ` +
			`"evidence": ["one", "two"], "recommendation": "do it", ` +
			`"total_tokens": 12, "steps_done": 3, "steps_total": 4}`,
		// Missing loop_id: the constructor raises and the row is SKIPPED,
		// not defaulted. This is the one row whose absence proves the
		// difference between "limit valid rows" and "limit raw lines".
		`{"failure_class": "healthy", "severity": "info"}`,
		// ...and one row missing EACH of the other two required fields,
		// because a required-field list that is short by one is a list
		// that still passes every test built from a single omission.
		`{"loop_id": "x", "severity": "info"}`,
		`{"loop_id": "y", "failure_class": "healthy"}`,
		// A present null in a REQUIRED field is still present, so the row
		// rehydrates with None and renders "severity=None".
		`{"loop_id": "c", "failure_class": "healthy", "severity": null}`,
		// Typed fields with untyped contents: the divergence the port
		// names rather than hides.
		`{"loop_id": "d", "failure_class": "healthy", "severity": "info", ` +
			`"total_tokens": "many"}`,
		`{"loop_id": "e", "failure_class": "healthy", "severity": "info", ` +
			`"total_tokens": 5.0}`,
		// evidence that is not a list at all, and a list with a non-string.
		`{"loop_id": "f", "failure_class": "healthy", "severity": "info", ` +
			`"evidence": "not a list"}`,
		`{"loop_id": "g", "failure_class": "healthy", "severity": "info", ` +
			`"evidence": ["real", 5, null]}`,
		// A line that is not JSON at all, and one that is JSON but not an
		// object: both are dropped by the reader, before rehydration.
		`{not json`,
		`[1, 2, 3]`,
		`{"loop_id": "h", "failure_class": "token_explosion", "severity": ` +
			`"warning", "total_elapsed_ms": 900, "project": "p", ` +
			`"recorded_at": "2026-08-26T00:00:00+00:00"}`,
	}
	ws := seedStore(t, "diagnoses.jsonl", rows)

	// limit=0 and limit=-1 are the interesting ones: Python appends before
	// it checks, so both return exactly ONE row.
	for _, limit := range []int{0, -1, 1, 3, 50} {
		t.Run(pyval.Str(limit), func(t *testing.T) {
			probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
			// `evidence` decodes as [][]any, not [][]string: a fixture row
			// carries the integer 5 and a null inside the list, and a
			// []string field makes the whole probe fail to decode. The
			// channel has opinions about what it carries, and narrowing
			// the Go type here would have hidden the very rows the
			// fixture exists for.
			var want struct {
				IDs       []string `json:"ids"`
				Summaries []string `json:"summaries"`
				Evidence  [][]any  `json:"evidence"`
			}
			probe.RunJSON(t, pyLoadDiagnosesSrc, &want,
				pyprobe.Arg(t, map[string]any{"limit": limit}))

			got := LoadDiagnoses(ws, limit)
			if len(got) != len(want.IDs) {
				t.Fatalf("row count: cpython %d %v, go %d", len(want.IDs),
					want.IDs, len(got))
			}
			if len(want.IDs) == 0 {
				t.Fatal("fixture produced no rows on either side; the " +
					"comparison below cannot fail")
			}
			for i, d := range got {
				if d.LoopID != want.IDs[i] {
					t.Errorf("row %d loop_id: cpython %q, go %q",
						i, want.IDs[i], d.LoopID)
				}
				// Rows "d" and "e" carry a string and a float in an INT
				// field, which no Go struct can hold. Their summaries are
				// asserted — as a DIVERGENCE, with both exact strings —
				// by TestMistypedNumericFieldsAreANamedDivergence. They
				// stay in this fixture because the rest of the reader
				// (order, skipping, limit) must still handle them.
				if d.LoopID == "d" || d.LoopID == "e" {
					continue
				}
				if s := d.Summary(); s != want.Summaries[i] {
					t.Errorf("row %d summary:\ncpython %s\n     go %s",
						i, want.Summaries[i], s)
				}
			}
		})
	}
}

// TestLoadDiagnosesEvidenceDivergence is the KNOWN GAP, pinned rather than
// fixed: CPython carries a non-list `evidence` through unchanged, and a Go
// []string cannot hold the bare string "not a list".
func TestLoadDiagnosesEvidenceDivergence(t *testing.T) {
	ws := seedStore(t, "diagnoses.jsonl", []string{
		`{"loop_id": "f", "failure_class": "healthy", "severity": "info", ` +
			`"evidence": "not a list"}`,
		`{"loop_id": "g", "failure_class": "healthy", "severity": "info", ` +
			`"evidence": ["real", 5, null]}`,
	})
	got := LoadDiagnoses(ws, 50)
	if len(got) != 2 {
		t.Fatalf("want 2 rows, got %d", len(got))
	}
	// Newest first: g then f.
	if want := []string{"real", "5", "None"}; !equalStrings(got[0].Evidence, want) {
		t.Errorf("non-string members render like Python's f-string: want %v, got %v",
			want, got[0].Evidence)
	}
	if len(got[1].Evidence) != 0 {
		t.Errorf("a non-list evidence is dropped, not wrapped: got %v",
			got[1].Evidence)
	}
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// TestLoopEventReadersMatchCPython covers the prefix match and the
// last-loop_id scan, including the two rows where CPython RAISES.
func TestLoopEventReadersMatchCPython(t *testing.T) {
	ev := func(idx int, loopID string) string {
		return `{"event_type": "step_done", "loop_id": ` + loopID +
			`, "step_idx": ` + pyval.Str(idx) + `}`
	}
	cases := []struct {
		name   string
		loopID string
		lines  []string
	}{
		{name: "an exact id", loopID: "L1", lines: []string{
			ev(1, `"L1"`), ev(2, `"L2"`), ev(3, `"L1"`),
		}},
		// The match is a PREFIX, so "L1" also selects "L10".
		{name: "a prefix selects the longer id too", loopID: "L1", lines: []string{
			ev(1, `"L1"`), ev(2, `"L10"`), ev(3, `"L2"`),
		}},
		// ...and a prefix is not a substring. "L1" appears INSIDE "xL1y"
		// and must not select it. Without this case the whole difference
		// between HasPrefix and Contains is invisible.
		{name: "a substring that is not a prefix", loopID: "L1", lines: []string{
			ev(1, `"xL1y"`), ev(2, `"L1tail"`), ev(3, `"zzL1"`),
		}},
		// An empty id selects EVERYTHING, in both runtimes.
		{name: "an empty id matches every event", loopID: "", lines: []string{
			ev(1, `"L1"`), ev(2, `"L2"`),
		}},
		{name: "an id that matches nothing", loopID: "ZZ", lines: []string{
			ev(1, `"L1"`),
		}},
		// Rows with no loop_id at all: `.get(...,"")` makes them "" and
		// only an empty prefix selects them.
		{name: "events with no loop_id", loopID: "L1", lines: []string{
			`{"event_type": "step_done", "step_idx": 1}`,
			ev(2, `"L1"`),
		}},
		{name: "the latest id skips trailing rows without one", loopID: "L1",
			lines: []string{
				ev(1, `"L1"`), ev(2, `"L9"`),
				`{"event_type": "loop_done", "step_idx": 3}`,
			}},
		{name: "an empty string loop_id is not a loop_id", loopID: "L1",
			lines: []string{ev(1, `"L1"`), ev(2, `""`)}},
		{name: "no events at all", loopID: "L1", lines: nil},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			ws := seedStore(t, "events.jsonl", tc.lines)
			probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
			var want struct {
				Latest     *string `json:"latest"`
				LatestErr  *string `json:"latest_error"`
				Matched    []any   `json:"matched"`
				MatchedErr *string `json:"match_error"`
			}
			probe.RunJSON(t, pyLoopEventsSrc, &want,
				pyprobe.Arg(t, map[string]any{"loop_id": tc.loopID}))
			if want.LatestErr != nil || want.MatchedErr != nil {
				t.Fatalf("cpython raised on a fixture that should not raise: "+
					"latest=%v match=%v", want.LatestErr, want.MatchedErr)
			}

			gotID, ok := LatestLoopID(ws)
			if want.Latest == nil {
				if ok {
					t.Errorf("latest loop id: cpython None, go %q", gotID)
				}
			} else if !ok || gotID != *want.Latest {
				t.Errorf("latest loop id: cpython %q, go %q (found=%v)",
					*want.Latest, gotID, ok)
			}

			got := LoadLoopEvents(ws, tc.loopID)
			if len(got) != len(want.Matched) {
				t.Fatalf("matched %d events, cpython matched %d",
					len(got), len(want.Matched))
			}
			for i, e := range got {
				g := pyval.Str(pyval.Plain(evGet(e, "step_idx", nil)))
				w := normNum(want.Matched[i])
				if g != w {
					t.Errorf("match %d step_idx: cpython %s, go %s", i, w, g)
				}
			}
		})
	}
}

// TestNumericLoopIDIsANamedDivergence pins the one shape where CPython
// raises and the port answers.
//
// A row stamped with a NUMBER for loop_id is selected by Python's
// truthiness gate, returned from _load_latest_loop_id as an int, and then
// blows up in `.startswith(5)`. The port skips it and reports the previous
// string id. Recorded as a divergence rather than replicated: crashing the
// whole loader on one malformed row is the Tier-0 defect jsonl_utils was
// written to remove, and re-introducing it here would be a regression
// dressed as fidelity.
func TestNumericLoopIDIsANamedDivergence(t *testing.T) {
	lines := []string{
		`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1}`,
		`{"event_type": "step_done", "loop_id": 5, "step_idx": 2}`,
	}
	ws := seedStore(t, "events.jsonl", lines)

	probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
	// `latest` is `any`: CPython returns the INTEGER 5 here, which is the
	// whole point of the fixture, and a *string field would fail to decode
	// rather than report it.
	var want struct {
		Latest     any     `json:"latest"`
		LatestErr  *string `json:"latest_error"`
		MatchedErr *string `json:"match_error"`
	}
	probe.RunJSON(t, pyLoopEventsSrc, &want,
		pyprobe.Arg(t, map[string]any{"loop_id": "L1"}))
	if want.LatestErr != nil {
		t.Fatalf("cpython raised inside _load_latest_loop_id: %v", *want.LatestErr)
	}
	if want.MatchedErr == nil {
		t.Fatal("cpython did NOT raise on `.startswith` over a numeric " +
			"loop_id; the divergence this test pins no longer exists")
	}
	if *want.MatchedErr != "AttributeError" {
		t.Errorf("cpython raised %s, expected AttributeError", *want.MatchedErr)
	}
	// The scan itself does not raise — it hands back the integer.
	if f, ok := want.Latest.(float64); !ok || f != 5 {
		t.Errorf("cpython _load_latest_loop_id returned %#v, expected the "+
			"integer 5; the divergence this test pins has moved", want.Latest)
	}

	gotID, ok := LatestLoopID(ws)
	if !ok || gotID != "L1" {
		t.Errorf("port should skip the numeric id and report L1, got %q (%v)",
			gotID, ok)
	}
	if n := len(LoadLoopEvents(ws, "L1")); n != 1 {
		t.Errorf("port should match the one string-id event, matched %d", n)
	}
}

// TestSaveDiagnosisRoundTrips pins the persistence contract: the row is the
// same bytes MarshalRow produces, recorded_at is stamped IN PLACE at save
// time, and a caller-supplied stamp is never overwritten.
func TestSaveDiagnosisRoundTrips(t *testing.T) {
	ws := t.TempDir()
	stamp := time.Date(2026, 8, 26, 12, 0, 0, 0, time.UTC)

	d := LoopDiagnosis{LoopID: "L1", FailureClass: "healthy", Severity: "info"}
	if err := SaveDiagnosis(ws, &d, stamp); err != nil {
		t.Fatal(err)
	}
	if d.RecordedAt != "2026-08-26T12:00:00+00:00" {
		t.Errorf("recorded_at is stamped on the CALLER's diagnosis: got %q",
			d.RecordedAt)
	}

	// A second save with a stamp already on it must not re-stamp.
	d2 := LoopDiagnosis{LoopID: "L2", FailureClass: "healthy", Severity: "info",
		RecordedAt: "1999-01-01T00:00:00+00:00"}
	if err := SaveDiagnosis(ws, &d2, stamp); err != nil {
		t.Fatal(err)
	}
	if d2.RecordedAt != "1999-01-01T00:00:00+00:00" {
		t.Errorf("a caller-supplied recorded_at was overwritten: %q", d2.RecordedAt)
	}

	dp, dperr := DiagnosesPath(ws)
	if dperr != nil {
		t.Fatal(dperr)
	}
	raw, err := os.ReadFile(dp)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != 2 {
		t.Fatalf("want 2 appended rows, got %d: %q", len(lines), raw)
	}
	for i, src := range []LoopDiagnosis{d, d2} {
		want, err := src.MarshalRow()
		if err != nil {
			t.Fatal(err)
		}
		if lines[i] != want {
			t.Errorf("row %d bytes:\nwant %s\n got %s", i, want, lines[i])
		}
	}

	// ...and the rows read back, newest first, WITH their stamps.
	//
	// recorded_at appears in no summary() and in no other assertion, so
	// dropping it from rehydration is invisible to every test that only
	// compares rendered text — the mutant that does exactly that survived
	// the first battery. It is the field the per-class rate windows sort
	// on, which makes it the worst one to lose quietly.
	back := LoadDiagnoses(ws, 50)
	if len(back) != 2 || back[0].LoopID != "L2" || back[1].LoopID != "L1" {
		t.Fatalf("round trip: %+v", back)
	}
	if back[0].RecordedAt != "1999-01-01T00:00:00+00:00" {
		t.Errorf("newest row recorded_at: %q", back[0].RecordedAt)
	}
	if back[1].RecordedAt != "2026-08-26T12:00:00+00:00" {
		t.Errorf("oldest row recorded_at: %q", back[1].RecordedAt)
	}
}

// TestDiagnoseLatestComposesTheReaders — the composition is where a port
// that got both halves right can still hand the wrong id to the second.
func TestDiagnoseLatestComposesTheReaders(t *testing.T) {
	ws := seedStore(t, "events.jsonl", []string{
		`{"event_type": "step_done", "loop_id": "L1", "step_idx": 1, ` +
			`"status": "done", "tokens_in": 100, "tokens_out": 10, "elapsed_ms": 900}`,
		`{"event_type": "step_done", "loop_id": "L2", "step_idx": 1, ` +
			`"status": "blocked", "tokens_in": 0, "tokens_out": 0, "elapsed_ms": 400}`,
	})
	got, ok := DiagnoseLatest(ws)
	if !ok {
		t.Fatal("want a diagnosis")
	}
	if got.LoopID != "L2" {
		t.Errorf("diagnosed %q, want the LATEST loop L2", got.LoopID)
	}
	// L2's only step is blocked with 0 tokens in under 5s — setup_failure.
	// A port that passed the WRONG id would diagnose L1 as healthy, so this
	// assertion is what makes the id check above non-redundant.
	if got.FailureClass != "setup_failure" {
		t.Errorf("failure class %q, want setup_failure", got.FailureClass)
	}
	// diagnose_latest() calls diagnose_loop(loop_id) with project at its
	// default. A port that filled in a plausible slug here would stamp
	// every unattributed diagnosis with a project it was never told, and
	// project is a RANKING input in find_relevant_failure_notes.
	if got.Project != "" {
		t.Errorf("project %q: diagnose_latest passes no project", got.Project)
	}

	if _, ok := DiagnoseLatest(t.TempDir()); ok {
		t.Error("an empty workspace names no loop, so there is no diagnosis")
	}
}

// TestMistypedNumericFieldsAreANamedDivergence pins the one thing a typed
// struct cannot do.
//
// Python's dataclass constructor type-checks nothing, so a diagnoses.jsonl
// row carrying `"total_tokens": "many"` rehydrates with a STRING in an int
// field and `summary()` renders `tokens=many`; a JSON float 5.0 renders
// `tokens=5.0` because it is still a float after rehydration. The port's
// field is an int, so it reads 0 and 5.
//
// Recorded rather than chased. Making LoopDiagnosis hold `any` in five
// numeric fields would push the untypedness through Diagnose, Summary and
// every caller, to be faithful about rows that no writer in either runtime
// produces. The containment argument is what makes that safe: nothing
// rehydrated by LoadDiagnoses is ever written back, so a mistyped row in
// the shared store cannot be REWRITTEN by this runtime into a differently
// mistyped one. If a writer ever appears, this test is where the decision
// gets revisited.
func TestMistypedNumericFieldsAreANamedDivergence(t *testing.T) {
	ws := seedStore(t, "diagnoses.jsonl", []string{
		`{"loop_id": "d", "failure_class": "healthy", "severity": "info", ` +
			`"total_tokens": "many"}`,
		`{"loop_id": "e", "failure_class": "healthy", "severity": "info", ` +
			`"total_tokens": 5.0}`,
	})
	probe := pyprobe.Probe{Marker: "introspect.py", Workspace: ws}
	var want struct {
		IDs       []string `json:"ids"`
		Summaries []string `json:"summaries"`
		Evidence  [][]any  `json:"evidence"`
	}
	probe.RunJSON(t, pyLoadDiagnosesSrc, &want,
		pyprobe.Arg(t, map[string]any{"limit": 50}))

	// Newest first: e, then d.
	cpython := []string{
		"[healthy] severity=info steps=0/0done tokens=5.0 elapsed=0ms | ",
		"[healthy] severity=info steps=0/0done tokens=many elapsed=0ms | ",
	}
	port := []string{
		"[healthy] severity=info steps=0/0done tokens=5 elapsed=0ms | ",
		"[healthy] severity=info steps=0/0done tokens=0 elapsed=0ms | ",
	}
	if len(want.Summaries) != 2 {
		t.Fatalf("probe returned %d rows, want 2", len(want.Summaries))
	}
	for i, w := range cpython {
		if want.Summaries[i] != w {
			t.Errorf("cpython row %d now renders %q, not %q — the "+
				"divergence this test pins has moved", i, want.Summaries[i], w)
		}
	}
	got := LoadDiagnoses(ws, 50)
	if len(got) != 2 {
		t.Fatalf("port returned %d rows, want 2", len(got))
	}
	for i, w := range port {
		if s := got[i].Summary(); s != w {
			t.Errorf("port row %d renders %q, want %q", i, s, w)
		}
	}
}
