package knowledge

import (
	"encoding/json"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// TestTieredLessonRowMatchesJSONDumps: the tiered-lessons file is THE
// shared store — "lessons are data" is the whole reason this port
// exists — and every row of it was written by `json.Marshal(tl)` until
// r8. Python is `locked_append(path, json.dumps(asdict(tl)))`.
//
// r7 converted the eight writers it ENUMERATED, and an enumeration is
// not a class. A struct writer looks safe because encoding/json emits
// declaration order, so the key order is already right — order was never
// the fork. Three other things were:
//
//	`>`      HTML-escaped by encoding/json, plain in json.dumps. Every
//	         "A -> B" lesson this system mints contains one.
//	é        raw from encoding/json, `\uXXXX` from json.dumps.
//	1.0      json.Marshal writes `1`; json.dumps writes `1.0`. Confidence,
//	         Score and Novelty are float64 and routinely whole.
//
// Driven against CPython's json.dumps rather than a golden string, so the
// claim is measured rather than transcribed.
func TestTieredLessonRowMatchesJSONDumps(t *testing.T) {
	ws := t.TempDir()
	s := NewStore(ws)
	tl := TieredLesson{
		LessonID: "l1",
		TaskType: "build",
		Outcome:  "success",
		// Both escaping hazards in one field, and the arrow is not
		// decoration: it is the shape lessons are actually minted in.
		Lesson:     "prefer a > b when the café path is hot",
		SourceGoal: "ship it",
		// The three whole floats.
		Confidence:        1.0,
		Score:             0.0,
		Novelty:           2.0,
		LastReinforced:    "2026-08-23T00:00:00Z",
		SessionsValidated: 3,
		RecordedAt:        "2026-08-23T00:00:00Z",
		LessonType:        "heuristic",
	}
	if err := s.AppendMediumLesson(tl); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(s.TieredLessonsPath("medium"))
	if err != nil {
		t.Fatal(err)
	}
	got := strings.TrimRight(string(raw), "\n")
	if strings.Contains(got, "\n") {
		t.Fatalf("one lesson, one line:\n%s", got)
	}

	// CPython renders the SAME field/value pairs, in the order this row
	// carries them, so the comparison is byte-for-byte over the whole
	// line rather than a spot check on three substrings.
	pairs, err := orderedPairs(got)
	if err != nil {
		t.Fatalf("row does not parse: %v\n%s", err, got)
	}
	in, err := json.Marshal(pairs)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"pairs = json.loads(sys.argv[1])\n"+
			"d = {}\n"+
			"for k, v in pairs:\n"+
			"    d[k] = float(v['f']) if isinstance(v, dict) and 'f' in v else v\n"+
			"print(json.dumps(d))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	want := strings.TrimRight(string(out), "\n")
	if got != want {
		t.Errorf("tiered-lesson row is not json.dumps' bytes:\n go %s\n py %s", got, want)
	}

	// Anti-vacuity: encoding/json over the same struct, required to lose.
	// Without this the test would report agreement on a row that happened
	// to contain nothing escapable and no whole float.
	old, err := json.Marshal(tl)
	if err != nil {
		t.Fatal(err)
	}
	if string(old) == want {
		t.Fatal("encoding/json already produces json.dumps' bytes for this " +
			"fixture: it cannot show the fork it was written for")
	}
	// These describe the PRE-FIX writer's output, so the escaping runs the
	// other way round from the assertions above: encoding/json HTML-escapes
	// `>` into the six literal characters \u003e, leaves é RAW (no
	// ensure_ascii), and writes the whole float 1.0 as 1.
	for _, marker := range []string{"\\u003e", "caf\u00e9", `"confidence":1,`} {
		if !strings.Contains(string(old), marker) {
			t.Fatalf("the pre-fix writer does not exhibit %s on this fixture, "+
				"so one of the three forks is untested:\n%s", marker, old)
		}
	}
}

// orderedPairs re-reads a rendered row as [[key, value], ...] so the
// probe receives Python's insertion order rather than a sorted map — and
// tags floats, because encoding/json would hand `1.0` to Python as an int
// and the probe would answer `1`, which reads exactly like a real
// divergence (the transport hazard r7 named).
func orderedPairs(line string) ([]any, error) {
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	if d, ok := tok.(json.Delim); !ok || d != '{' {
		return nil, errNotObject
	}
	var out []any
	for dec.More() {
		kt, err := dec.Token()
		if err != nil {
			return nil, err
		}
		var v any
		if err := dec.Decode(&v); err != nil {
			return nil, err
		}
		out = append(out, []any{kt, tagFloats(v, line, kt.(string))})
	}
	return out, nil
}

var errNotObject = errStr("row is not a JSON object")

type errStr string

func (e errStr) Error() string { return string(e) }

// tagFloats wraps a value the SOURCE LINE spells with a decimal point,
// so a whole float survives the transport as a float.
func tagFloats(v any, line, key string) any {
	if n, ok := v.(json.Number); ok {
		if strings.Contains(n.String(), ".") || strings.Contains(n.String(), "e") {
			return map[string]any{"f": n.String()}
		}
		return n
	}
	_ = line
	_ = key
	return v
}

// TestHypothesisRowMatchesJSONDumps: the same class, one file over. The
// hypotheses store is read by the Python promotion pass.
func TestHypothesisRowMatchesJSONDumps(t *testing.T) {
	ws := t.TempDir()
	s := NewStore(ws)
	h := Hypothesis{
		HypID:           "h1",
		Lesson:          "prefer a > b in the café path",
		Domain:          "build",
		Confirmations:   2,
		SourceLessonIDs: []string{"l1"},
		FirstSeen:       "2026-08-23T00:00:00Z",
		LastSeen:        "2026-08-23T00:00:00Z",
	}
	if err := s.AppendHypothesis(h); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(s.HypothesesPath())
	if err != nil {
		t.Fatal(err)
	}
	got := strings.TrimRight(string(raw), "\n")
	for _, want := range []string{
		`"hyp_id": "h1"`,   // json.dumps' key separator
		`prefer a > b`,     // NOT HTML-escaped
		`caf\u00e9`,        // ensure_ascii IS on: the six literal chars
		`"imported": null`, // a nil map is null in both
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("hypothesis row is not json.dumps-shaped (missing %s):\n%s",
				want, got)
		}
	}
	// Declaration order, which is asdict()'s order.
	if !strings.HasPrefix(got, `{"hyp_id": "h1", "lesson": `) {
		t.Fatalf("field order is not the struct's declaration order:\n%s", got)
	}
}
