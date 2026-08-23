package record

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
)

// H1. Re-stamping a verdict must not emit a key twice.
//
// The row's on-disk key order is recovered from its source text and the
// verdict keys are appended to it — so on any row that ALREADY carries a
// verdict, those keys were named twice and emitted twice. Python's dict
// assignment updates in place and can only produce one, and Go's own
// LoadsClean refuses a duplicate name by design, so the ledger grew ~2x per
// stamp until the runtime that wrote it could no longer read it back.
func TestReStampingAVerdictNeverDuplicatesAKey(t *testing.T) {
	ws := t.TempDir()
	r := New(ws)
	if _, err := r.WriteOutcome(Outcome{Goal: "g", Status: "done", LoopID: "loopA"}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(ws, "memory", "outcomes.jsonl")
	yes := true
	conf := 0.9

	var lens []int
	for i := 0; i < 4; i++ {
		if err := r.StampOutcomeVerdict("loopA", &yes, SourceClosure, &conf); err != nil {
			t.Fatal(err)
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		line := strings.TrimSuffix(string(raw), "\n")
		// The runtime's own admission predicate must still admit it.
		if _, err := LoadsClean(line); err != nil {
			t.Fatalf("stamp %d produced a row this runtime refuses: %v", i+1, err)
		}
		// And say it directly, so the failure names the cause rather than
		// leaving "LoadsClean refused" to be diagnosed.
		if dup := duplicateKeyIn(t, line); dup != "" {
			t.Fatalf("stamp %d emitted %q twice", i+1, dup)
		}
		lens = append(lens, len(line))
	}
	// A re-stamp appends ONE verdict_history entry, so the row grows by a
	// roughly constant amount each time. The bug made it grow by a copy of
	// everything already there: 497 -> 886 -> 1729, deltas 389 then 843.
	// Linear growth is the invariant; a fixed byte ceiling would only pin
	// today's field set.
	d1, d3 := lens[1]-lens[0], lens[3]-lens[2]
	if d3 > d1*3/2 {
		t.Fatalf("row growth is compounding, not linear: lengths %v, deltas %d then %d",
			lens, d1, d3)
	}
}

// The same bug reached the FIRST stamp of any row already carrying a
// verdict — a Python-written row, or Go's own NOW lane, which sets
// goal_achieved and goal_verdict_source before the agenda lane stamps.
func TestStampingAnAlreadyJudgedRowEmitsEachKeyOnce(t *testing.T) {
	ws := t.TempDir()
	path := filepath.Join(ws, "memory", "outcomes.jsonl")
	if err := os.MkdirAll(filepath.Dir(path), NewDirMode); err != nil {
		t.Fatal(err)
	}
	seed := `{"outcome_id":"o1","loop_id":"loopA","goal":"g",` +
		`"goal_achieved":true,"goal_verdict_source":"now_v1",` +
		`"goal_verdict_at":"2026-08-01T00:00:00+00:00"}`
	if err := os.WriteFile(path, []byte(seed+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	no := false
	if err := New(ws).StampOutcomeVerdict("loopA", &no, SourceClosure, nil); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	line := strings.TrimSuffix(string(raw), "\n")
	if dup := duplicateKeyIn(t, line); dup != "" {
		t.Fatalf("first stamp of a judged row emitted %q twice: %s", dup, line)
	}
	if _, err := LoadsClean(line); err != nil {
		t.Fatalf("row refused by its own writer: %v", err)
	}
	// The prior verdict must have been SUPERSEDED, not shadowed by a
	// second copy of the key: the new value is the one that survives.
	row, err := LoadsClean(line)
	if err != nil {
		t.Fatal(err)
	}
	if row["goal_achieved"] != false {
		t.Fatalf("goal_achieved is %v, want false", row["goal_achieved"])
	}
	if row["goal_verdict_source"] != SourceClosure {
		t.Fatalf("source is %v", row["goal_verdict_source"])
	}
}

// The de-duplication belongs in the renderer, not at the one call site
// that happened to trip over it — a dict cannot hold a key twice, so no
// caller should be able to ask for that. First position wins, which is
// what a dict's insertion order gives.
func TestOrderedEmitsARepeatedModeledKeyOnceAtItsFirstPosition(t *testing.T) {
	out, err := pyjson.Ordered(
		map[string]any{"a": 1, "b": 2, "c": 3},
		[]string{"a", "b", "a", "c", "b", "a"})
	if err != nil {
		t.Fatal(err)
	}
	if out != `{"a":1,"b":2,"c":3}` {
		t.Fatalf("got %s", out)
	}
}

// orderedKeysOf faithfully reports a duplicate key it finds in a FOREIGN
// row, and must not turn that into a duplicate on the way back out.
func TestAForeignDuplicateKeyRowIsRewrittenWithOneCopy(t *testing.T) {
	keys := orderedKeysOf(`{"a":1,"b":2,"a":3}`)
	if len(keys) != 3 {
		t.Fatalf("orderedKeysOf should report what it sees: %v", keys)
	}
	out, err := pyjson.Ordered(map[string]any{"a": 3, "b": 2}, keys)
	if err != nil {
		t.Fatal(err)
	}
	if out != `{"a":3,"b":2}` {
		t.Fatalf("got %s", out)
	}
}

// duplicateKeyIn returns the first key appearing more than once at the top
// level of a JSON object, or "".
func duplicateKeyIn(t *testing.T, line string) string {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(line))
	tok, err := dec.Token()
	if err != nil || tok != json.Delim('{') {
		t.Fatalf("not a JSON object: %q", line)
	}
	seen := map[string]bool{}
	depth := 0
	for dec.More() || depth > 0 {
		tk, err := dec.Token()
		if err != nil {
			t.Fatal(err)
		}
		switch v := tk.(type) {
		case json.Delim:
			switch v {
			case '{', '[':
				depth++
			case '}', ']':
				depth--
				if depth < 0 {
					return ""
				}
			}
		case string:
			if depth == 0 {
				if seen[v] {
					return v
				}
				seen[v] = true
				// Skip this key's value wholesale.
				var discard json.RawMessage
				if err := dec.Decode(&discard); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	return ""
}

// Package-level so the addition happens at RUNTIME in float64, not at
// compile time in exact arithmetic.
var pointOne, pointTwo = 0.1, 0.2

// L5. Python's float.__repr__ switches to exponent notation at a different
// magnitude than Go's shortest-'g' does, and avg_latency_ms — milliseconds,
// so ~16 minutes of average step — crosses that boundary. The expectations
// below were all measured against CPython.
func TestFloatSpellingMatchesPythonsRepr(t *testing.T) {
	cases := []struct {
		in   float64
		want string
	}{
		{999999.9, "999999.9"},   // Go's 'g' agrees here
		{1000000.0, "1000000.0"}, // Go's 'g' says "1e+06"
		{1234567.8, "1234567.8"}, // Go's 'g' says "1.2345678e+06"
		{1e15, "1000000000000000.0"},
		{1e16, "1e+16"}, // Python's threshold, not Go's
		{1.5e16, "1.5e+16"},
		{0.0001, "0.0001"},
		{1e-5, "1e-05"}, // two exponent digits
		{9.9e-5, "9.9e-05"},
		{1.0, "1.0"}, // the ".0" rule this replaced
		{0.0, "0.0"},
		// math.Copysign, not the literal -0.0: Go folds the negation of an
		// untyped zero constant back to +0, so the literal would have
		// tested nothing.
		{math.Copysign(0, -1), "-0.0"},
		{-1234567.8, "-1234567.8"},
		{1e22, "1e+22"},
		{5e-324, "5e-324"}, // three exponent digits, denormal
		{1.7976931348623157e308, "1.7976931348623157e+308"},
		// Likewise through variables: `0.1 + 0.2` as a constant expression
		// is folded at exact precision and comes out 0.3.
		{pointOne + pointTwo, "0.30000000000000004"},
	}
	for _, c := range cases {
		if got := pyjson.FloatRepr(c.in); got != c.want {
			t.Errorf("FloatRepr(%v) = %s, want %s", c.in, got, c.want)
		}
	}
	// And through the writer, where it actually matters.
	out, err := pyjson.Ordered(map[string]any{"avg_latency_ms": 1234567.8}, []string{"avg_latency_ms"})
	if err != nil {
		t.Fatal(err)
	}
	if out != `{"avg_latency_ms":1234567.8}` {
		t.Fatalf("through the writer: %s", out)
	}
}

// M2's classifier. Every expectation measured against
// captains_log.classify_input_type.
func TestInputTypeClassifierMatchesPython(t *testing.T) {
	long := strings.Repeat("prose ", 60) // > 200 code points
	cases := []struct{ in, want string }{
		{"", "plain_text"},
		{"fetch https://example.com/page", "url"},
		{"https://a.example https://b.example " + long, "url"}, // 2+ URLs, length irrelevant
		{"https://example.com " + long, "plain_text"},          // 1 URL in LONG text is not
		{"def f():\n    import os\n    return 1", "code"},
		{"just one keyword: return ", "plain_text"}, // 1 hit < 2
		{`{"a": 1}`, "structured_data"},             // {, }, ":  -> 3 hits
		{"{ and } only", "plain_text"},              // 2 hits < 3
		{"ordinary sentence", "plain_text"},
	}
	for _, c := range cases {
		if got := ClassifyInputType(c.in); got != c.want {
			t.Errorf("ClassifyInputType(%.40q) = %s, want %s", c.in, got, c.want)
		}
	}
	// Python's \S stops a URL at UNICODE whitespace, so the ideographic
	// space below ENDS the first URL rather than gluing the two into one —
	// two URLs, not one, which is what flips the classification.
	if got := ClassifyInputType("https://a.example　https://b.example " + long); got != "url" {
		t.Errorf("unicode whitespace must terminate a URL match: got %s", got)
	}
	// len() is code points, so a string of 199 astral characters plus a
	// URL is SHORT by Python's count and long by a byte count.
	short := strings.Repeat("𝄞", 150)
	if got := ClassifyInputType("https://e.example " + short); got != "url" {
		t.Errorf("length threshold must count code points: got %s", got)
	}
}
