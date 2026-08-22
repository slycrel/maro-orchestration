package loop

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/recall"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// TestRunWritesRunMetadataAndFeedsNextRecall pins the closure tranche's
// composition seam: the loop now WRITES run metadata (the half that was
// named-unported through the recall tranche), its own run is excluded
// from its own recall, and a SECOND run's recall reads the first as a
// prior attempt — pure-Go workspaces stop degrading to zero priors.
func TestRunWritesRunMetadataAndFeedsNextRecall(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{`["do the thing"]`, "done it"}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "polish the widget onboarding flow", MaxSteps: 1, DryRun: true})
	if err != nil || res.Status != "done" {
		t.Fatalf("run: %v %+v", err, res)
	}
	// Metadata on disk, finalized.
	metaPath := filepath.Join(ws, "runs", res.LoopID, "metadata.json")
	raw, rerr := os.ReadFile(metaPath)
	if rerr != nil {
		t.Fatal(rerr)
	}
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["status"] != "done" || meta["prompt"] != "polish the widget onboarding flow" ||
		meta["ended_at"] == nil {
		t.Fatalf("metadata: %v", meta)
	}
	if _, present := meta["goal_achieved"]; present {
		t.Fatalf("tool-less run must not carry a goal_achieved stamp: %v", meta)
	}
	// Self-exclusion: the run's own recall saw ZERO prior attempts even
	// though its metadata existed before the recall call.
	events, _ := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	if !strings.Contains(string(events), "recall slice=loop: 0 prior attempts.") {
		t.Fatalf("run read its own metadata as a prior attempt:\n%s", events)
	}
	// A second look at the store sees the finished run.
	rr := recall.Recall(ws, "polish the widget onboarding flow", "")
	if len(rr.PriorAttempts) != 1 || rr.PriorAttempts[0].HandleID != res.LoopID {
		t.Fatalf("Go-written metadata invisible to recall: %+v", rr.PriorAttempts)
	}
	if rr.PriorAttempts[0].GoalAchieved != nil {
		t.Fatalf("unstamped run must read as unjudged tri-state: %+v", rr.PriorAttempts[0])
	}
}

// TestRunClosureStampsGoalAchieved: exec lane, healthy run → closure
// plan + mechanical check + verdict → goal_achieved stamped true, the
// verdict row durable in build/closure_verdicts.jsonl, the
// CLOSURE_VERDICT event logged, and the NEXT run's recall reads the
// tri-state (done ≠ successful made visible end-to-end).
func TestRunClosureStampsGoalAchieved(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["write the greeting file"]`,
		`{"tool": "complete_step", "result": "wrote greeting.txt", "summary": "wrote", "confidence": "strong"}`,
		`{"checks":[{"failure_mode":"file missing","description":"greeting exists","command":"true"}]}`,
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"Goal achieved. Greeting present."}`,
	}, AgentToolsOK: true}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "write a greeting file", MaxSteps: 2, DryRun: false, Exec: true})
	if err != nil || res.Status != "done" {
		t.Fatalf("run: %v %+v", err, res)
	}
	if res.Closure == nil || !res.Closure.Judged || !res.Closure.Complete {
		t.Fatalf("closure verdict missing from result: %+v", res.Closure)
	}
	raw, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["goal_achieved"] != true || meta["goal_verdict_source"] != "go_closure_v1" {
		t.Fatalf("verdict stamp: %v", meta)
	}
	rows, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "build", "closure_verdicts.jsonl"))
	if rerr != nil || !strings.Contains(string(rows), `"checks_run":1`) {
		t.Fatalf("durable verdict row: %v %s", rerr, rows)
	}
	events, _ := os.ReadFile(filepath.Join(ws, "memory", "captains_log.jsonl"))
	li := strings.Index(string(events), "LOOP_FINISHED")
	ci := strings.Index(string(events), "CLOSURE_VERDICT")
	if ci < 0 || ci < li {
		t.Fatalf("CLOSURE_VERDICT event missing or before LOOP_FINISHED:\n%s", events)
	}
	rr := recall.Recall(ws, "write a greeting file", "")
	if len(rr.PriorAttempts) != 1 || rr.PriorAttempts[0].GoalAchieved == nil ||
		!*rr.PriorAttempts[0].GoalAchieved {
		t.Fatalf("stamped tri-state not visible to recall: %+v", rr.PriorAttempts)
	}
	// The OUTCOME ROW carries the verdict too — it is written at loop
	// finalization, BEFORE closure judges, so it needs the post-hoc
	// stamp (Python stamp_outcome_verdict; adversarial routing r2, both
	// lenses: without it every closure-judged loop run read as
	// permanently unjudged on the cross-runtime ledger).
	oraw, oerr := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	if oerr != nil {
		t.Fatal(oerr)
	}
	lines := strings.Split(strings.TrimSpace(string(oraw)), "\n")
	var row map[string]any
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &row); err != nil {
		t.Fatal(err)
	}
	if row["goal_achieved"] != true || row["goal_verdict_source"] != "go_closure_v1" ||
		row["goal_verdict_confidence"] != 0.9 {
		t.Fatalf("closure verdict must land on the outcome row post-hoc: %v", row)
	}
}

// TestRunClosureUnjudgedStampsNothing: every closure probe inconclusive
// → judged=false → NO goal_achieved key (absence means not judged,
// never failed — the 4/5-false-negatives lesson made structural).
func TestRunClosureUnjudgedStampsNothing(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["do the work"]`,
		`{"tool": "complete_step", "result": "did it", "summary": "did", "confidence": "strong"}`,
		`{"checks":[{"failure_mode":"fm","description":"probe","command":"this_command_does_not_exist_xyzzy"}]}`,
		`{"complete":false,"confidence":0.8,"gaps":[],"summary":"Goal not achieved."}`,
	}, AgentToolsOK: true}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "do the inconclusive work", MaxSteps: 2, DryRun: false, Exec: true})
	if err != nil || res.Status != "done" {
		t.Fatalf("run: %v %+v", err, res)
	}
	if res.Closure == nil || res.Closure.Judged {
		t.Fatalf("all-inconclusive closure claims judged: %+v", res.Closure)
	}
	raw, _ := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if _, present := meta["goal_achieved"]; present {
		t.Fatalf("unjudged verdict stamped goal_achieved: %v", meta)
	}
	if meta["goal_verdict_summary"] == nil {
		t.Fatalf("verdict prose should still be recorded beside the absent stamp: %v", meta)
	}
	// Confidence is gated WITH the verdict: an unjudged closure measured
	// nothing — writing its zero Confidence would fabricate "verified
	// with zero confidence" (adversarial routing r2, Architect;
	// Go-stricter than Python, which writes 0.0 here).
	if _, present := meta["goal_verdict_confidence"]; present {
		t.Fatalf("unjudged closure must not stamp a confidence: %v", meta)
	}
	// The outcome row mirrors the tri-state: source lands (closure RAN),
	// goal_achieved stays absent, confidence stays absent.
	oraw, _ := os.ReadFile(filepath.Join(ws, "memory", "outcomes.jsonl"))
	lines := strings.Split(strings.TrimSpace(string(oraw)), "\n")
	var row map[string]any
	if err := json.Unmarshal([]byte(lines[len(lines)-1]), &row); err != nil {
		t.Fatal(err)
	}
	if row["goal_verdict_source"] != "go_closure_v1" {
		t.Fatalf("unjudged closure still names its source on the row: %v", row)
	}
	if _, has := row["goal_achieved"]; has {
		t.Fatalf("unjudged closure must not stamp the row's tri-state: %v", row)
	}
	if _, has := row["goal_verdict_confidence"]; has {
		t.Fatalf("unjudged closure must not fabricate row confidence: %v", row)
	}
}

// TestRunToolLessLaneWritesNamedSkipRow: the tool-less lane skips
// closure (it structurally writes no files) but the skip is a durable
// named row — "closure never ran" stays distinguishable from "closure
// ran and produced nothing".
func TestRunToolLessLaneWritesNamedSkipRow(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{`["think"]`, "thought"}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "think about things", MaxSteps: 1, DryRun: false})
	if err != nil || res.Status != "done" {
		t.Fatalf("run: %v %+v", err, res)
	}
	if res.Closure != nil {
		t.Fatalf("tool-less lane ran closure: %+v", res.Closure)
	}
	rows, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "build", "closure_verdicts.jsonl"))
	if rerr != nil || !strings.Contains(string(rows), `"skipped":"tool_less_lane"`) {
		t.Fatalf("named skip row missing: %v %s", rerr, rows)
	}
}

// TestRunStuckWithStepsStillGetsClosure: eligibility is "did any step
// run", not terminal status — Python's _closure_eligible_statuses spans
// done/partial/stuck/restart because a stuck run that wrote real files
// is exactly where the honest "what got delivered" signal matters most
// (adversarial closure r1 2026-08-22, three lenses independently).
func TestRunStuckWithStepsStillGetsClosure(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["write the file", "read the follow-up ledger"]`,
		`{"tool": "complete_step", "result": "wrote it", "summary": "ok", "confidence": "strong"}`,
		`{"tool": "flag_stuck", "reason": "the follow-up ledger file does not exist: checked both locations"}`,
		`{"checks":[{"failure_mode":"file missing","description":"file exists","command":"true"}]}`,
		`{"complete":false,"confidence":0.8,"gaps":["follow-up undone"],"summary":"Partial delivery."}`,
	}, AgentToolsOK: true}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "stuck run closure case", MaxSteps: 3, DryRun: false, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("expected stuck run, got %s", res.Status)
	}
	if res.Closure == nil || !res.Closure.Judged || res.Closure.Complete {
		t.Fatalf("stuck run with a done step must still get closure: %+v", res.Closure)
	}
	raw, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	var meta map[string]any
	if err := json.Unmarshal(raw, &meta); err != nil {
		t.Fatal(err)
	}
	if meta["goal_achieved"] != false || meta["status"] != "stuck" {
		t.Fatalf("stuck run stamp/finalize: %v", meta)
	}
}

// TestRunExecNoStepsRanWritesNamedSkipRow: an exec run whose only step
// blocked without running leaves a NAMED skip row — the persist-the-
// artifacts decree makes "closure never ran" distinguishable from a
// crash before the finish path.
func TestRunExecNoStepsRanWritesNamedSkipRow(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["read the ledger"]`,
		`{"tool": "flag_stuck", "reason": "ledger file does not exist"}`,
	}, AgentToolsOK: true}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "no steps ran case", MaxSteps: 2, DryRun: false, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" || res.Closure != nil {
		t.Fatalf("expected stuck run without closure: %s %+v", res.Status, res.Closure)
	}
	rows, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "build", "closure_verdicts.jsonl"))
	if rerr != nil || !strings.Contains(string(rows), `"skipped":"no_steps_ran"`) {
		t.Fatalf("named skip row missing: %v %s", rerr, rows)
	}
}

// TestRunClosureRowStampFailureWritesDurableMarker: when the post-hoc
// row stamp fails, the failure lands as a named run-dir row, not just
// a terminal warning — a silent failure recreates the row-unjudged bug
// behind a rarer trigger (adversarial routing r4: the marker path had
// zero coverage). Fault injection: a DIRECTORY squatting on the
// ledger's .tmp path makes the rewrite's WriteFile fail while plain
// appends still succeed.
func TestRunClosureRowStampFailureWritesDurableMarker(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	if err := os.MkdirAll(filepath.Join(ws, "memory", "outcomes.jsonl.tmp"), 0o755); err != nil {
		t.Fatal(err)
	}
	fake := &llm.Fake{Script: []string{
		`["write the greeting file"]`,
		`{"tool": "complete_step", "result": "wrote greeting.txt", "summary": "wrote", "confidence": "strong"}`,
		`{"checks":[{"failure_mode":"file missing","description":"greeting exists","command":"true"}]}`,
		`{"complete":true,"confidence":0.9,"gaps":[],"summary":"Goal achieved."}`,
	}, AgentToolsOK: true}
	res, err := Run(context.Background(), fake, record.New(ws), Opts{
		Goal: "write a greeting file", MaxSteps: 2, DryRun: false, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	warned := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "outcome-row verdict stamp failed") {
			warned = true
		}
	}
	if !warned {
		t.Fatalf("stamp failure must warn: %v", res.Warnings)
	}
	rows, rerr := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "build", "closure_verdicts.jsonl"))
	if rerr != nil || !strings.Contains(string(rows), "outcome_row_stamp_failed") {
		t.Fatalf("stamp failure must leave a durable marker: %v %s", rerr, rows)
	}
	// The metadata stamp (a different owner) must still have landed.
	meta, _ := os.ReadFile(filepath.Join(ws, "runs", res.LoopID, "metadata.json"))
	if !strings.Contains(string(meta), `"goal_achieved": true`) &&
		!strings.Contains(string(meta), `"goal_achieved":true`) {
		t.Fatalf("metadata stamp must survive a row-stamp failure: %s", meta)
	}
}
