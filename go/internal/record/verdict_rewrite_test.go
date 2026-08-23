package record

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// L3. The verdict stamp rewrites the WHOLE outcomes ledger to patch three
// keys on one row. Three things it must not do to the rows it did not come
// for — each one found by r3, each one silent:
//
//   - re-type every stored number (plain Unmarshal makes each a float64,
//     so a foreign `cost: 1.0` came back `1` and a counter `42` would have
//     come back `42.0`),
//   - alphabetize the row (pyjson.Ordered with no key list sorts, so a
//     patched foreign row is a whole-file reformat of someone else's data),
//   - widen the file's mode (temp+rename wrote a fresh 0644 file, so an
//     operator's 0600 ledger lost its permissions on every stamp).
//
// The fixture is deliberately a row this runtime did NOT write: the whole
// point is what happens to a peer's formatting.
func TestVerdictStampLeavesForeignRowsAlone(t *testing.T) {
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "outcomes.jsonl")

	// Key order here is NOT alphabetical, and the numbers cover all three
	// literal shapes Python can write.
	foreign := `{"zulu":"last","loop_id":"other","cost":1.0,"tokens":42,` +
		`"latency":0.500,"alpha":"first"}`
	target := `{"loop_id":"mine","goal":"g","status":"done","tokens":7}`
	if err := os.WriteFile(path, []byte(foreign+"\n"+target+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	yes := true
	conf := 0.85
	if err := New(ws).StampOutcomeVerdict("mine", &yes, SourceClosure, &conf); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 rows, got %d:\n%s", len(lines), raw)
	}

	// The row this stamp did not come for is untouched, byte for byte.
	if lines[0] != foreign {
		t.Errorf("a bystander row was rewritten:\n have %s\n want %s", lines[0], foreign)
	}

	// The patched row keeps its own disk order, with the new keys riding
	// after it in assignment order — what a Python dict does when
	// json.loads' order is extended by assignment.
	wantPrefix := `{"loop_id":"mine","goal":"g","status":"done","tokens":7,` +
		`"goal_achieved":true,"goal_verdict_source":"` + SourceClosure + `",`
	if !strings.HasPrefix(lines[1], wantPrefix) {
		t.Errorf("patched row lost its key order (alphabetizing puts "+
			"goal_achieved first):\n have %s\n want prefix %s", lines[1], wantPrefix)
	}
	// The pre-existing literal is preserved exactly — not re-typed to 7.0.
	if !strings.Contains(lines[1], `"tokens":7,`) {
		t.Errorf("a stored integer was re-typed by the rewrite:\n%s", lines[1])
	}
	if !strings.HasSuffix(lines[1], `"goal_verdict_confidence":0.85}`) {
		t.Errorf("the stamped keys must ride at the tail:\n%s", lines[1])
	}

	// The ledger keeps the mode it had. Python's atomic_write re-applies
	// the target's existing mode; a plain temp+rename writes a fresh 0644.
	st, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if got := st.Mode().Perm(); got != 0o600 {
		t.Errorf("the stamp widened an operator's 0600 ledger to %#o", got)
	}
}

// The same three properties on a row carrying a float that only survives
// as a literal: `1.0` re-typed through float64 comes back `1`, which
// json.loads then parses as an int, so a cross-runtime cost analysis
// silently changes type mid-ledger.
func TestVerdictStampPreservesWholeFloatLiterals(t *testing.T) {
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "outcomes.jsonl")
	row := `{"loop_id":"mine","cost":1.0,"steps":3,"score":0.250}`
	if err := os.WriteFile(path, []byte(row+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := New(ws).StampOutcomeVerdict("mine", nil, SourceNowVerify, nil); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	got := strings.TrimSpace(string(raw))
	for _, lit := range []string{`"cost":1.0`, `"steps":3`, `"score":0.250`} {
		if !strings.Contains(got, lit) {
			t.Errorf("literal %s did not survive the rewrite:\n%s", lit, got)
		}
	}
	// An unjudged stamp still records WHEN it landed and never fabricates
	// a verdict or a confidence.
	if strings.Contains(got, "goal_achieved") || strings.Contains(got, "confidence") {
		t.Errorf("an unjudged stamp invented a verdict:\n%s", got)
	}
	if !strings.Contains(got, `"goal_verdict_at":`) {
		t.Errorf("the verdict timestamp is the flow measurement:\n%s", got)
	}
}
