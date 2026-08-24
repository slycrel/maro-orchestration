package evolver

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEvolveCadenceStateIsJSONDumps: the evolve counter is a shared
// state file — both runtimes read it to decide whether the next
// finalization fires an evolve pass — and it was written with
// `json.Marshal`, which sorts keys and uses compact separators. Neither
// value is escapable here, so the whole divergence was SHAPE: bytes no
// CPython writer produces, sitting in a file CPython reads
// (adversarial mission-r8: an enumeration is not a class — r7 converted
// the writers it listed, and this one was not on the list).
func TestEvolveCadenceStateIsJSONDumps(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	// cadence=5: two ticks leave the counter at 2, which is the steady
	// state rather than the reset shape.
	for i := 0; i < 2; i++ {
		if _, err := CadenceTick(ws, 5); err != nil {
			t.Fatal(err)
		}
	}

	raw, err := os.ReadFile(cadencePath(ws))
	if err != nil {
		t.Fatalf("cadence state was not written: %v", err)
	}
	line := strings.TrimRight(string(raw), "\n")

	if !strings.HasPrefix(line, `{"runs_since_evolve": 2, "updated_at": "`) {
		t.Errorf("cadence state is not json.dumps-shaped in the writer's order:\n%s", line)
	}
	if strings.Contains(line, `":"`) || strings.Contains(line, `","`) {
		t.Errorf("compact separators are encoding/json's, not json.dumps':\n%s", line)
	}

	// Anti-vacuity: the pre-fix encoder over the same fields, required to
	// lose.
	//
	// Note which fork is actually live here, because getting this wrong is
	// how a test ends up asserting nothing: `runs_since_evolve` sorts
	// BEFORE `updated_at`, so encoding/json's sorting happens to agree with
	// the writer's order on this file, and neither value contains an
	// escapable character. The ONLY divergence is the separators — which is
	// still a divergence, since these are shared bytes, but the test must
	// say so rather than claim three forks and check one.
	old, err := json.Marshal(map[string]any{
		"runs_since_evolve": 2, "updated_at": "2026-08-23T00:00:00Z",
	})
	if err != nil {
		t.Fatal(err)
	}
	if string(old) == line {
		t.Fatal("encoding/json already produces json.dumps' bytes for this " +
			"state: the test cannot show the fork it was written for")
	}
	if !strings.Contains(string(old), `":"`) {
		t.Fatalf("the pre-fix encoder does not exhibit the compact separator "+
			"here, so the one live fork is untested:\n%s", old)
	}
}
