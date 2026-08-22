package loop

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestRunInjectsRecallIntoDecomposePrompt pins the recall tranche's
// composition end-to-end on the literal Run path: a tiered lesson in
// the workspace store reaches the DECOMPOSE prompt (not a step prompt),
// and the read is instrumented as a RECALL_PERFORMED event before
// LOOP_STARTED — Python's ordering, where recall runs pre-planning.
func TestRunInjectsRecallIntoDecomposePrompt(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	memDir := filepath.Join(ws, "memory", "medium")
	if err := os.MkdirAll(memDir, 0o755); err != nil {
		t.Fatal(err)
	}
	lesson := "always smoke the staging index before a widget deploy"
	row := `{"lesson_id":"wire1","task_type":"agenda","outcome":"done","lesson":"` +
		lesson + `","score":1.0,"last_reinforced":"` +
		time.Now().UTC().Format("2006-01-02") + `","times_reinforced":2}` + "\n"
	if err := os.WriteFile(filepath.Join(memDir, "lessons.jsonl"), []byte(row), 0o644); err != nil {
		t.Fatal(err)
	}

	fake := &llm.Fake{Script: []string{
		`["inspect the staging index"]`,
		"index inspected",
	}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "deploy the widget service after checking the staging index",
		MaxSteps: 1, DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" {
		t.Fatalf("run: %+v", res)
	}
	if len(fake.Prompts) < 2 {
		t.Fatalf("prompts: %d", len(fake.Prompts))
	}
	decomposePrompt := fake.Prompts[0]
	if !strings.Contains(decomposePrompt, "## Lessons from Prior Runs (weigh by their receipts)") ||
		!strings.Contains(decomposePrompt, lesson+" (reinforced 2x)") {
		t.Fatalf("lesson block missing from decompose prompt:\n%s", decomposePrompt)
	}
	// The lesson block must not leak into the step-execution prompt —
	// recall feeds PLANNING; step context is the executor's own concern.
	if strings.Contains(fake.Prompts[1], "## Lessons from Prior Runs") {
		t.Fatalf("lesson block leaked into step prompt:\n%s", fake.Prompts[1])
	}

	events, err := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(events)
	ri := strings.Index(text, "RECALL_PERFORMED")
	li := strings.Index(text, "LOOP_STARTED")
	if ri < 0 {
		t.Fatalf("RECALL_PERFORMED not logged:\n%s", text)
	}
	if li >= 0 && ri > li {
		t.Fatalf("recall event after LOOP_STARTED — recall must precede planning:\n%s", text)
	}
	if !strings.Contains(text, `"goal_preview"`) || !strings.Contains(text, `"prior_attempts"`) {
		t.Fatalf("recall event context missing instrumentation:\n%s", text)
	}
}

// TestRunRecallDegradesWithoutStore: a workspace with no memory store
// plans exactly as before — the seam degrades to "knows nothing" and
// the decompose prompt carries no recall block.
func TestRunRecallDegradesWithoutStore(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{`["do the thing"]`, "done it"}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "a goal with no memory behind it", MaxSteps: 1, DryRun: true})
	if err != nil || res.Status != "done" {
		t.Fatalf("degrade broke the run: %v %+v", err, res)
	}
	if strings.Contains(fake.Prompts[0], "Lessons from Prior Runs") ||
		strings.Contains(fake.Prompts[0], "== Recall") {
		t.Fatalf("phantom recall block on empty store:\n%s", fake.Prompts[0])
	}
}
