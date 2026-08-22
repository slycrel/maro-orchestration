package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/closure"
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

// The goal-verdict line is the tranche's headline guarantee made
// operator-visible — it gets a pin, not just a comment (adversarial
// closure r2 2026-08-22, Skeptic: the r1 fix shipped untested). NOTE
// (closure r3): the first version of this file's closure-era edit
// OVERWROTE the three tests above instead of appending — restored in
// the r3 fix layer; a test file is append-to, never cat-over.
func TestClosureLine(t *testing.T) {
	if got := closureLine(nil); got != "" {
		t.Fatalf("nil verdict must print nothing: %q", got)
	}
	judged := &closure.Verdict{Summary: "Achieved: all probes passed.",
		Confidence: 0.95, ChecksPassed: 4, ChecksRun: 4, Judged: true, Complete: true}
	got := closureLine(judged)
	if !strings.Contains(got, "Achieved: all probes passed.") ||
		!strings.Contains(got, "confidence 0.95") ||
		!strings.Contains(got, "4/4 checks passed") {
		t.Fatalf("judged line: %q", got)
	}
	skipped := &closure.Verdict{Summary: "Verification did not run.",
		SkipReason: "exception"}
	got = closureLine(skipped)
	if !strings.Contains(got, "[skipped: exception]") {
		t.Fatalf("skip path must name its reason: %q", got)
	}
	if strings.Contains(got, "checks passed") {
		t.Fatalf("skip line must not fake check counts: %q", got)
	}
}

// Routing reaches the CLI end-to-end (director tranche slice 1): a
// NOW-shaped goal on the dry backend classifies heuristically and runs
// the single-call lane; -lane agenda forces the loop for the same goal.
func TestRunLaneRoutingEndToEnd(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	if err := run([]string{"run", "-backend", "dry", "what time is it?"}); err != nil {
		t.Fatal(err)
	}
	rows := readOutcomeRows(t, ws)
	nowRow := rows[len(rows)-1]
	if nowRow["task_type"] != "now" {
		t.Fatalf("NOW-shaped goal must route now: %v", nowRow)
	}
	// Flow assertions, not just the routing tag (r1 Skeptic: the one
	// CLI-level NOW test asserted only task_type). The dry Fake's
	// loop-shaped script means the judge sees non-JSON prose — an
	// honest dry row is dry_run:true, non-empty summary, UNJUDGED.
	if nowRow["dry_run"] != true {
		t.Fatalf("dry NOW row must be fenced dry_run: %v", nowRow)
	}
	if s, _ := nowRow["summary"].(string); s == "" {
		t.Fatalf("NOW row must carry the answer summary: %v", nowRow)
	}
	if _, has := nowRow["goal_achieved"]; has {
		t.Fatalf("dry judge prose is unparseable — row must stay unjudged: %v", nowRow)
	}
	if err := run([]string{"run", "-backend", "dry", "-lane", "agenda", "what time is it?"}); err != nil {
		t.Fatal(err)
	}
	rows = readOutcomeRows(t, ws)
	if rows[len(rows)-1]["task_type"] != "loop" {
		t.Fatalf("-lane agenda must force the loop: %v", rows[len(rows)-1])
	}
}

func readOutcomeRows(t *testing.T, ws string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var row map[string]any
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
		rows = append(rows, row)
	}
	return rows
}
