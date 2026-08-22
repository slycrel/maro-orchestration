package loop

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func execFake(script ...string) *llm.Fake {
	return &llm.Fake{Script: script, AgentToolsOK: true}
}

func TestExecLaneEndToEnd(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["inspect the repo", "write the report"]`,
		`{"tool": "complete_step", "result": "repo has 3 modules", "summary": "inspected", "confidence": "strong"}`,
		`{"tool": "complete_step", "result": "report written to report.md", "summary": "wrote report", "confidence": "weak"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "audit the widget repo", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 2 {
		t.Fatalf("status=%s steps=%d", res.Status, len(res.Steps))
	}
	wantDir := filepath.Join(ws, "projects", "audit-the-widget-repo")
	if res.ProjectDir != wantDir {
		t.Fatalf("project dir %q, want %q", res.ProjectDir, wantDir)
	}
	if st, err := os.Stat(wantDir); err != nil || !st.IsDir() {
		t.Fatalf("project dir not created: %v", err)
	}
	if res.Steps[0].Summary != "inspected" || res.Steps[0].Confidence != "strong" {
		t.Fatalf("step 0 metadata: %+v", res.Steps[0])
	}
	if res.Steps[1].Result != "report written to report.md" {
		t.Fatalf("step 1 result: %q", res.Steps[1].Result)
	}

	// The executor calls (after the planner's) must carry the full
	// contract: agent tools on, cwd bound, tools offered, transcript
	// path under the project's artifacts dir.
	if len(fake.Opts) != 3 {
		t.Fatalf("expected 3 calls, got %d", len(fake.Opts))
	}
	plan := fake.Opts[0]
	if plan.AgentTools || len(plan.Tools) != 0 {
		t.Fatalf("planning call must stay in utility mode: %+v", plan)
	}
	for i, o := range fake.Opts[1:] {
		if !o.AgentTools {
			t.Errorf("step %d: agent tools off", i)
		}
		if o.Cwd != wantDir {
			t.Errorf("step %d: cwd %q, want %q", i, o.Cwd, wantDir)
		}
		if len(o.Tools) != 2 || o.Tools[0].Name != "complete_step" || o.Tools[1].Name != "flag_stuck" {
			t.Errorf("step %d: tool contract %+v", i, o.Tools)
		}
		wantTr := filepath.Join(wantDir, "artifacts",
			// Step numbering is 1-based and global.
			fmt.Sprintf("step-%d-transcript.jsonl", i+1))
		if o.TranscriptPath != wantTr {
			t.Errorf("step %d: transcript %q, want %q", i, o.TranscriptPath, wantTr)
		}
		if o.Purpose != "step-execute" {
			t.Errorf("step %d: purpose %q", i, o.Purpose)
		}
	}

	// Executor prompts advertise the workspace and the tool protocol.
	stepPrompt := fake.Prompts[1]
	for _, want := range []string{
		"WORKSPACE: Save deliverables to " + wantDir,
		"--- AVAILABLE TOOLS ---",
		"complete_step",
		"autonomous execution agent",
	} {
		if !strings.Contains(stepPrompt, want) {
			t.Errorf("step prompt missing %q", want)
		}
	}
}

func TestExecLaneFlagStuckBlocksWithReason(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["find the dataset"]`,
		`{"tool": "flag_stuck", "reason": "NEED_INFO: dataset URL not provided", "attempted": "searched the workspace"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "analyze the dataset", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("status %s", res.Status)
	}
	got := res.Steps[0].Result
	if !strings.Contains(got, "NEED_INFO: dataset URL not provided") ||
		!strings.Contains(got, "attempted: searched the workspace") {
		t.Fatalf("stuck reason mangled: %q", got)
	}
	// The reason reaches the outcome row's failure chain.
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	if len(chain) == 0 || !strings.Contains(chain[0].(string), "dataset URL not provided") {
		t.Fatalf("failure chain: %+v", chain)
	}
}

func TestExecLaneInjectedStepsRunNextInOrder(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["main step", "final step"]`,
		`{"tool": "complete_step", "result": "found missing dep", "summary": "s",
		  "inject_steps": ["install the dep", "verify the dep"]}`,
		`{"tool": "complete_step", "result": "dep installed", "summary": "s"}`,
		`{"tool": "complete_step", "result": "dep verified", "summary": "s"}`,
		`{"tool": "complete_step", "result": "all done", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "ship it", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 4 {
		t.Fatalf("status=%s steps=%d", res.Status, len(res.Steps))
	}
	wantOrder := []string{"main step", "install the dep", "verify the dep", "final step"}
	for i, w := range wantOrder {
		if res.Steps[i].Step != w {
			t.Fatalf("step %d = %q, want %q (injection must splice at the front)",
				i, res.Steps[i].Step, w)
		}
	}
	if len(res.Steps[0].Injected) != 2 {
		t.Fatalf("injected record: %+v", res.Steps[0].Injected)
	}
}

func TestExecLaneInjectCapAndBlankFiltering(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["one step"]`,
		`{"tool": "complete_step", "result": "r", "summary": "s",
		  "inject_steps": ["  ", "a", "b", "c", "d", "e"]}`,
		`{"tool": "complete_step", "result": "r", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "cap test", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	// Blank filtered, then capped at 3 (Python [:3] parity — note the
	// Python cap slices BEFORE the emptiness filter; this port filters
	// first, which can only keep MORE real steps, never fewer).
	if len(res.Steps[0].Injected) != 3 ||
		res.Steps[0].Injected[0] != "a" || res.Steps[0].Injected[2] != "c" {
		t.Fatalf("inject cap: %+v", res.Steps[0].Injected)
	}
}

func TestExecLaneStepBudgetCapMarksRunStuck(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	// Every step injects more work: the 2x-planned cap must end the run
	// as stuck with the remainder named — never an infinite loop.
	fake := execFake(
		`["seed step"]`,
		`{"tool": "complete_step", "result": "r", "summary": "s",
		  "inject_steps": ["more work", "even more"]}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "runaway", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("status %s — a budget-exhausted run must not report done", res.Status)
	}
	if len(res.Steps) != 2 { // 2 x 1 planned step
		t.Fatalf("expected the 2x cap to stop at 2 executed, got %d", len(res.Steps))
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	found := false
	for _, c := range chain {
		if strings.Contains(c.(string), "step budget exhausted") {
			found = true
		}
	}
	if !found {
		t.Fatalf("budget exhaustion missing from failure chain: %+v", chain)
	}
}

func TestExecLaneNoToolCallFallsBackToContent(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["one step"]`,
		"plain prose answer with no tool call",
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "fallback", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || res.Steps[0].Result != "plain prose answer with no tool call" {
		t.Fatalf("fallback: %+v", res.Steps[0])
	}
}

func TestExecLaneEmptyCompleteStepResultBlocks(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["one step"]`,
		`{"tool": "complete_step", "result": "   ", "summary": "claims done"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "empty result", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" || !strings.Contains(res.Steps[0].Result, "empty result") {
		t.Fatalf("empty complete_step must block: %+v", res.Steps[0])
	}
}

func TestExecRequestedOnIncapableBackendDegradesLoudly(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{ // AgentToolsOK deliberately false
		`["one step"]`,
		"tool-less result",
	}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "degrade", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.ProjectDir != "" {
		t.Fatalf("tool-less path must not claim a project dir: %q", res.ProjectDir)
	}
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "cannot run agent tools") {
			found = true
		}
	}
	if !found {
		t.Fatalf("silent mode degrade — warnings: %+v", res.Warnings)
	}
	if fake.Opts[1].AgentTools || len(fake.Opts[1].Tools) != 0 {
		t.Fatalf("degraded step still requested tools: %+v", fake.Opts[1])
	}
}

func TestGoalSlugParity(t *testing.T) {
	cases := map[string]string{
		"Audit the Widget Repo, thoroughly & fast!": "audit-the-widget-repo-thoroughly",
		"":            "unnamed-goal",
		"!!! ???":     "unnamed-goal",
		"one two three four five six": "one-two-three-four-five",
	}
	for in, want := range cases {
		if got := goalSlug(in); got != want {
			t.Errorf("goalSlug(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestClassifyStepTimeout(t *testing.T) {
	if d := classifyStepTimeout("summarize the findings"); d != 600*time.Second {
		t.Errorf("default: %v", d)
	}
	if d := classifyStepTimeout("run pytest tests/test_foo.py"); d != 1800*time.Second {
		t.Errorf("long-running: %v", d)
	}
	if d := classifyStepTimeout("run pytest tests/ -q"); d != 3600*time.Second {
		t.Errorf("full suite: %v", d)
	}
	t.Setenv("MARO_LONG_RUNNING_TIMEOUT", "900")
	if d := classifyStepTimeout("git clone the repo"); d != 900*time.Second {
		t.Errorf("env override: %v", d)
	}
	if d := classifyStepTimeout("run the full test suite"); d != 1800*time.Second {
		t.Errorf("full suite 2x override: %v", d)
	}
}
