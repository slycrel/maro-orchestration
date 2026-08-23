package selfimprove

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func writeJSONL(t *testing.T, path string, rows []map[string]any) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	var b strings.Builder
	for _, r := range rows {
		raw, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		b.Write(raw)
		b.WriteByte('\n')
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func readRows(t *testing.T, path string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(string(raw), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var m map[string]any
		if json.Unmarshal([]byte(line), &m) == nil {
			rows = append(rows, m)
		}
	}
	return rows
}

// One full cycle over a seeded workspace: the LLM proposes nothing, the
// calibration scanner fires (its row must land in the SAME batch/save),
// graduation proposes from repeated diagnoses, and the verify pass runs.
func TestCycleComposesScansGraduationAndVerify(t *testing.T) {
	ws := t.TempDir()
	mem := func(n string) string { return filepath.Join(ws, "memory", n) }

	// Outcomes: enough to clear MinOutcomes.
	var outcomes []map[string]any
	for i := 0; i < 5; i++ {
		outcomes = append(outcomes, map[string]any{
			"loop_id": "l", "goal": "g", "status": "done",
			"recorded_at": "2026-08-20T10:00:00+00:00"})
	}
	writeJSONL(t, mem("outcomes.jsonl"), outcomes)

	// Calibration fixture → one scanner row.
	var cal []map[string]any
	for i := 0; i < 5; i++ {
		cal = append(cal, map[string]any{
			"decision_class": "escalate", "confidence": 3,
			"action_raw": "a", "action_final": "a"})
	}
	writeJSONL(t, mem("calibration.jsonl"), cal)

	// Repeated diagnoses → one graduation proposal.
	writeJSONL(t, mem("diagnoses.jsonl"), []map[string]any{
		{"failure_class": "token_explosion", "loop_id": "l1"},
		{"failure_class": "token_explosion", "loop_id": "l2"},
		{"failure_class": "token_explosion", "loop_id": "l3"},
	})

	fake := &llm.Fake{Script: []string{`{"patterns": [], "suggestions": []}`}}
	rec := record.New(ws)
	report, verify := Cycle(context.Background(), ws, rec, map[string]any{}, fake,
		CycleOptions{})

	if report.Skipped {
		t.Fatalf("cycle skipped: %+v", report)
	}
	rows := readRows(t, mem("suggestions.jsonl"))
	var calRow, gradRow map[string]any
	for _, r := range rows {
		id, _ := r["suggestion_id"].(string)
		if strings.HasPrefix(id, "cal-") {
			calRow = r
		}
		if strings.HasPrefix(id, "grad-") {
			gradRow = r
		}
	}
	if calRow == nil {
		t.Fatalf("scanner row missing from the cycle's save: %+v", rows)
	}
	if gradRow == nil {
		t.Fatalf("graduation proposal missing: %+v", rows)
	}
	if gradRow["applied"] != false {
		t.Fatalf("graduation row must stay pending: %+v", gradRow)
	}
	// The verify pass ran (no applied-unverified rows → zero candidates,
	// but the pass itself reports enabled).
	if !verify.Enabled {
		t.Fatalf("verify pass did not run: %+v", verify)
	}
	// Scanner row (0.75) is below the 0.8 auto-apply bar — stays pending.
	if calRow["applied"] != false {
		t.Fatalf("cal row auto-applied below bar: %+v", calRow)
	}
}

func TestCycleDryRunProposesOnly(t *testing.T) {
	ws := t.TempDir()
	writeJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"), []map[string]any{
		{"status": "done"}, {"status": "done"}, {"status": "done"},
	})
	rec := record.New(ws)
	report, verify := Cycle(context.Background(), ws, rec, map[string]any{}, nil,
		CycleOptions{DryRun: true})
	if report.Skipped {
		t.Fatalf("dry cycle skipped: %+v", report)
	}
	if verify.Enabled {
		t.Fatalf("dry run must not run the verify pass: %+v", verify)
	}
	if _, err := os.Stat(filepath.Join(ws, "memory", "suggestions.jsonl")); !os.IsNotExist(err) {
		t.Fatal("dry run persisted suggestions")
	}
}
