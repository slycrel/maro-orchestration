package scans

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestBaselineRowIsJSONDumps: evolver-baselines.jsonl is a shared store —
// the Python drift cadence reads these rows back — and it was written with
// `json.Marshal`, whose whole-float rendering is the fork that only a
// numeric row has: a clean window rounds `success_rate` to 1.0, which
// encoding/json writes as `1` and json.dumps writes as `1.0`
// (adversarial mission-r8).
func TestBaselineRowIsJSONDumps(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	// A perfect window: every outcome is "done", so the rate is exactly 1.0
	// and the mean cost is exactly 2.0 — both whole, which is the point.
	var outcomes []map[string]any
	for i := 0; i < 6; i++ {
		outcomes = append(outcomes, map[string]any{
			"status": "done", "cost_usd": 2.0,
		})
	}
	ScanQualityDrift(ws, outcomes, 0, 0)

	line := readOneJSONL(t, baselinesPath(ws))
	// json.dumps' separators, the writer's key order, and — the fork this
	// test exists for — floats that stayed floats.
	for _, want := range []string{
		`"ts": "`,
		`"success_rate": 1.0, "avg_cost_usd": 2.0`, // spans an item separator
		`"outcomes_count": 6`,
	} {
		if !strings.Contains(line, want) {
			t.Errorf("baseline row is not json.dumps-shaped (missing %s):\n%s", want, line)
		}
	}
	if !strings.HasPrefix(line, `{"ts": `) {
		t.Errorf("key order must be the writer's, not sorted:\n%s", line)
	}

	// Anti-vacuity: the pre-fix encoder over the same values, required to
	// lose — and to lose on the FLOAT, not just the separators.
	old, err := json.Marshal(map[string]any{
		"ts": "x", "success_rate": 1.0, "avg_cost_usd": 2.0, "outcomes_count": 6,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(old), `"success_rate":1,`) {
		t.Fatalf("the pre-fix encoder does not flatten the whole float here, "+
			"so the fork this test names is untested:\n%s", old)
	}
}

// TestSuggestionOutcomeRowIsJSONDumps: same class, same file family. The
// confidence carried here is a float that is whole whenever a suggestion
// was recorded at full confidence.
func TestSuggestionOutcomeRowIsJSONDumps(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	// A change-log row supplies the category and a WHOLE confidence.
	cl, err := json.Marshal(map[string]any{
		"suggestion_id": "s1", "category": "a > b tuning", "confidence": 1.0,
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(memoryDir(ws), "change_log.jsonl"),
		append(cl, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}

	RecordSuggestionOutcomes(ws, []string{"s1"}, true, "run-café")

	line := readOneJSONL(t, suggestionOutcomesPath(ws))
	for _, want := range []string{
		// Spans the ITEM separator as well as the key one: a needle that
		// stops at a closing quote survives a mutant that only compacts
		// `, ` (mission-r8 battery).
		`{"suggestion_id": "s1", "category": `,
		`"category": "a > b tuning"`, // NOT HTML-escaped
		`"confidence": 1.0`,          // the whole float stayed a float
		`"verified": true`,
		`"run_id": "run-caf\u00e9"`, // ensure_ascii IS on
	} {
		if !strings.Contains(line, want) {
			t.Errorf("outcome row is not json.dumps-shaped (missing %s):\n%s", want, line)
		}
	}

	// Anti-vacuity: all three forks must be visible in the pre-fix output.
	old, err := json.Marshal(map[string]any{
		"suggestion_id": "s1", "category": "a > b tuning", "confidence": 1.0,
		"verified": true, "run_id": "run-café", "verified_at": "x",
	})
	if err != nil {
		t.Fatal(err)
	}
	for _, marker := range []string{
		`a \u003e b`,      // HTML escaping
		"run-caf\u00e9",   // raw UTF-8
		`"confidence":1,`, // the flattened whole float
	} {
		if !strings.Contains(string(old), marker) {
			t.Fatalf("the pre-fix encoder does not exhibit %s here, so one of "+
				"the forks is untested:\n%s", marker, old)
		}
	}
}

func readOneJSONL(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("nothing was written to %s: %v", path, err)
	}
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	if len(lines) != 1 {
		t.Fatalf("expected exactly one row, got %d:\n%s", len(lines), raw)
	}
	return lines[0]
}
