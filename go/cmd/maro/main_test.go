package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The literal composition point where user input becomes loop.Opts —
// the field this file wires (dry_run) is the one r1/r2 each caught
// mis-recorded once already (adversarial r3, both lenses).
func TestRunDryBackendWritesHonestDryRunRow(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	if err := run([]string{"run", "-backend", "dry", "smoke goal"}); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(string(raw))), &row); err != nil {
		t.Fatal(err)
	}
	if row["dry_run"] != true {
		t.Fatalf("-backend dry wrote dry_run=%v", row["dry_run"])
	}
}

func TestRunRefusesOutOfRangeMaxStepsBeforeAnyWrite(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	if err := run([]string{"run", "-backend", "dry", "-max-steps", "40", "goal"}); err == nil {
		t.Fatal("out-of-range -max-steps accepted")
	}
	if _, err := os.Stat(filepath.Join(ws, "memory")); !os.IsNotExist(err) {
		t.Fatal("refused run still wrote to the workspace")
	}
}

func TestRunRefusesFlagsAfterGoal(t *testing.T) {
	t.Setenv("MARO_WORKSPACE", t.TempDir())
	t.Setenv("MARO_USER_DIR", t.TempDir())
	err := run([]string{"run", "goal text", "-backend", "dry"})
	if err == nil || !strings.Contains(err.Error(), "put flags before the goal") {
		t.Fatalf("flag-after-goal footgun not refused: %v", err)
	}
}
