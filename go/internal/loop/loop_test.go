package loop

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

func readJSONL(t *testing.T, path string) []map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %s: %v", path, err)
	}
	var rows []map[string]any
	for _, line := range strings.Split(strings.TrimSpace(string(raw)), "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(line), &m); err != nil {
			t.Fatalf("bad jsonl line %q: %v", line, err)
		}
		rows = append(rows, m)
	}
	return rows
}

func TestRunEndToEndRecordsCompatibleRows(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws) // planner reads operator docs from here
	fake := &llm.Fake{Script: []string{
		`["gather the facts", "write the answer"]`,
		"facts: A and B",
		"final answer using A and B",
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "answer the question", "", 8)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 2 {
		t.Fatalf("status=%s steps=%d", res.Status, len(res.Steps))
	}

	outcomes := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if len(outcomes) != 1 {
		t.Fatalf("want 1 outcome row, got %d", len(outcomes))
	}
	row := outcomes[0]
	for _, key := range []string{"outcome_id", "goal", "status", "summary",
		"recorded_at", "task_type", "failure_chain", "measurement_class"} {
		if _, ok := row[key]; !ok {
			t.Errorf("outcome row missing python-compatible key %q", key)
		}
	}
	if row["measurement_class"] != "go-port" {
		t.Errorf("go rows must be fenceable: measurement_class=%v", row["measurement_class"])
	}
	if row["status"] != "done" {
		t.Errorf("status=%v", row["status"])
	}

	events := readJSONL(t, filepath.Join(ws, "memory", "captains_log.jsonl"))
	if len(events) != 2 {
		t.Fatalf("want LOOP_STARTED+LOOP_FINISHED, got %d rows", len(events))
	}
	if events[0]["event_type"] != "LOOP_STARTED" || events[1]["event_type"] != "LOOP_FINISHED" {
		t.Fatalf("event types: %v / %v", events[0]["event_type"], events[1]["event_type"])
	}
	for _, ev := range events {
		if ev["audience"] != "system" {
			t.Errorf("audience=%v", ev["audience"])
		}
		if _, ok := ev["timestamp"]; !ok {
			t.Errorf("event missing timestamp")
		}
	}
}

func TestBlockedStepReasonTravelsWholeToFailureChain(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	// Step 2's worker returns empty output -> blocked with a real reason.
	fake := &llm.Fake{Script: []string{
		`["step one", "step two"]`,
		"fine result",
		"   ",
	}}
	res, err := Run(context.Background(), fake, record.New(ws), "goal", "", 8)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("status=%s", res.Status)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, ok := rows[0]["failure_chain"].([]any)
	if !ok || len(chain) != 1 {
		t.Fatalf("failure_chain=%v", rows[0]["failure_chain"])
	}
	if !strings.Contains(chain[0].(string), "worker produced no output") {
		t.Fatalf("reason lost: %v", chain[0])
	}
}

func TestPriorEvidenceRidesMarkedBudgetNotBareSlice(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	long := strings.Repeat("E", 5000)
	fake := &llm.Fake{Script: []string{
		`["produce a huge result", "consume it"]`,
		long,
		"consumed",
	}}
	if _, err := Run(context.Background(), fake, record.New(ws), "goal", "", 8); err != nil {
		t.Fatal(err)
	}
	// The step-2 prompt (Prompts[2]: decompose, step1, step2) must carry
	// the clipped evidence WITH the marker — cut but honest.
	step2Prompt := fake.Prompts[2]
	if strings.Contains(step2Prompt, long) {
		t.Fatal("unbounded prior evidence reached the prompt")
	}
	if !strings.Contains(step2Prompt, "[truncated: first 4000 of 5000 characters]") {
		t.Fatal("prior evidence was cut without a marker (bare-slice smell)")
	}
}

func TestDecomposeFailureIsRecordedNotSilent(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{"no json array here at all"}}
	if _, err := Run(context.Background(), fake, record.New(ws), "goal", "", 8); err == nil {
		t.Fatal("decompose failure must surface as an error")
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if len(rows) != 1 || rows[0]["status"] != "stuck" {
		t.Fatalf("decompose failure left no stuck outcome: %v", rows)
	}
}

// erroringAdapter fails a chosen call with a typed ResultError carrying
// usage, exactly as the subprocess backend does on an is_error result.
type erroringAdapter struct {
	llm.Fake
	failCall int
	calls    int
}

func (e *erroringAdapter) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	e.calls++
	if e.calls == e.failCall {
		return nil, &llm.ResultError{Msg: "model refused: policy detail line 1\nline 2", TokensIn: 7, TokensOut: 3}
	}
	return e.Fake.Complete(ctx, msgs, opts)
}

// A blocked step still spent tokens; the outcome row must carry them
// (adversarial round 2026-08-22, Expert QA — the error branch dropped
// usage the CLI reports).
func TestBlockedStepSalvagesUsageFromTypedError(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	ad := &erroringAdapter{
		Fake:     llm.Fake{Script: []string{`["one", "two"]`, "ok"}},
		failCall: 3, // decompose, step1 ok, step2 fails
	}
	res, err := Run(context.Background(), ad, record.New(ws), "goal", "", 8)
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("status=%s", res.Status)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	// Fake spends 10/5 per successful call; the failed turn adds 7/3.
	if got := rows[0]["tokens_in"].(float64); got != 10+10+7 {
		t.Fatalf("blocked step's tokens_in dropped: %v", got)
	}
	if got := rows[0]["tokens_out"].(float64); got != 5+5+3 {
		t.Fatalf("blocked step's tokens_out dropped: %v", got)
	}
	// The multi-line reason travels whole (no first-line amputation).
	chain := rows[0]["failure_chain"].([]any)
	if !strings.Contains(chain[0].(string), "line 2") {
		t.Fatalf("diagnostic beyond the first line lost: %v", chain[0])
	}
}

// The row's model field records what the operator asked for — or says
// the backend defaulted — never just the backend name (adversarial
// round 2026-08-22, Expert QA: "subprocess" as a model misattributes
// spend in cross-runtime analyses).
func TestOutcomeRowRecordsRequestedModel(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{`["one"]`, "done"}}
	if _, err := Run(context.Background(), fake, record.New(ws), "goal", "sonnet", 8); err != nil {
		t.Fatal(err)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	if rows[0]["model"] != "sonnet" {
		t.Fatalf("model=%v, want the requested model", rows[0]["model"])
	}

	ws2 := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws2)
	fake2 := &llm.Fake{Script: []string{`["one"]`, "done"}}
	if _, err := Run(context.Background(), fake2, record.New(ws2), "goal", "", 8); err != nil {
		t.Fatal(err)
	}
	rows2 := readJSONL(t, filepath.Join(ws2, "memory", "outcomes.jsonl"))
	if rows2[0]["model"] != "fake-default" {
		t.Fatalf("model=%v, want backend-default marker", rows2[0]["model"])
	}
}

// loop_id must be a random join key, not a wall-clock derivative
// (adversarial round 2026-08-22, Architect: a 0.1s-modulus id collides
// across scripted runs and merges unrelated runs' events downstream).
func TestLoopIDsAreDistinctAcrossRuns(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	seen := map[string]bool{}
	for i := 0; i < 5; i++ {
		fake := &llm.Fake{Script: []string{`["one"]`, "done"}}
		res, err := Run(context.Background(), fake, record.New(ws), "goal", "", 8)
		if err != nil {
			t.Fatal(err)
		}
		if seen[res.LoopID] {
			t.Fatalf("loop_id %q repeated", res.LoopID)
		}
		seen[res.LoopID] = true
	}
}
