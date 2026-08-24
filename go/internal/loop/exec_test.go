package loop

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
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
	// Blank filtered, then capped at 3 — SAME order as Python
	// (loop_post_step.py: the `if str(s).strip()` filter runs inside the
	// comprehension, before the [:3] slice). No divergence. This comment
	// previously claimed the opposite; three review lenses refuted it
	// against the Python source (adversarial exec review 2026-08-22).
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
	row := rows[len(rows)-1]
	chain, _ := row["failure_chain"].([]any)
	found := false
	for _, c := range chain {
		if strings.Contains(c.(string), "step budget exhausted") {
			found = true
		}
	}
	if !found {
		t.Fatalf("budget exhaustion missing from failure chain: %+v", chain)
	}
	// The cap halt is a typed terminal too — Python stamps this exact
	// halt out-of-budget (loop_execute.py:492-503); r2 stamped the three
	// verdict terminals and missed this fourth site (ladder r3, QA HIGH).
	if row["stop_verdict"] != "out-of-budget" {
		t.Fatalf("cap halt not typed: stop_verdict=%v", row["stop_verdict"])
	}
	if sr, _ := row["stuck_reason"].(string); !strings.Contains(sr, "step budget exhausted") {
		t.Fatalf("cap halt stuck_reason: %q", sr)
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
		"":                            "unnamed-goal",
		"!!! ???":                     "unnamed-goal",
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

func TestResolveProjectSlugDisambiguation(t *testing.T) {
	root := t.TempDir()
	goalA := "tell me about the book Systemantics"
	goalB := "tell me about the book Alexander"

	// First goal: fresh dir, base slug, no reuse warning.
	slugA, warnA := resolveProjectSlug(root, goalA)
	if slugA != "tell-me-about-the-book" || warnA != "" {
		t.Fatalf("slugA = %q warn = %q", slugA, warnA)
	}
	dirA := filepath.Join(root, slugA)
	if err := os.MkdirAll(dirA, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := recordProjectMission(dirA, goalA); err != nil {
		t.Fatal(err)
	}

	// Unrelated goal, same generic phrasing: must NOT inherit A's dir.
	slugB, _ := resolveProjectSlug(root, goalB)
	if slugB != "tell-me-about-the-book-2" {
		t.Fatalf("collision not disambiguated: slugB = %q", slugB)
	}

	// Same goal re-entered: continuity — same dir, mission unchanged,
	// and the tail-matched reuse is NOT flagged as weak evidence.
	if again, warn := resolveProjectSlug(root, goalA); again != slugA || warn != "" {
		t.Fatalf("continuity broken: %q warn=%q", again, warn)
	}
	if err := recordProjectMission(dirA, "some other goal"); err != nil {
		t.Fatal(err)
	}
	if got := recordedMission(dirA); got != goalA {
		t.Fatalf("mission rewritten by second writer: %q", got)
	}

	// A slug with two subject words is specific: collision means SAME
	// project, reuse without any mission check.
	spec := "research the chlorination of water"
	slugS, _ := resolveProjectSlug(root, spec)
	if err := os.MkdirAll(filepath.Join(root, slugS), 0o755); err != nil {
		t.Fatal(err)
	}
	if again, warn := resolveProjectSlug(root, spec); again != slugS || warn != "" {
		t.Fatalf("specific slug should reuse silently: %q vs %q warn=%q", again, slugS, warn)
	}

	// No temp files left behind by the atomic mission writes above.
	entries, err := os.ReadDir(dirA)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.Contains(e.Name(), ".tmp") {
			t.Fatalf("mission temp file left behind: %s", e.Name())
		}
	}
}

func TestExecLaneCollidingGoalsGetDistinctProjectDirs(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	rec := record.New(ws)
	runOne := func(goal string) *Result {
		fake := execFake(`["one step"]`,
			`{"tool": "complete_step", "result": "r", "summary": "s"}`)
		res, err := Run(context.Background(), fake, rec, Opts{
			Goal: goal, MaxSteps: 2, DryRun: true, Exec: true})
		if err != nil {
			t.Fatal(err)
		}
		return res
	}
	a := runOne("summarize the report about glaciers")
	b := runOne("summarize the report about volcanoes")
	if a.ProjectDir == b.ProjectDir {
		t.Fatalf("unrelated goals share a live project dir: %s", a.ProjectDir)
	}
	if got := recordedMission(a.ProjectDir); got != "summarize the report about glaciers" {
		t.Fatalf("mission not recorded: %q", got)
	}
}

func TestExecLaneTerminalVerdictHaltsWithRemainder(t *testing.T) {
	// A MISSING_INPUT block is immediately terminal on the ladder (an
	// absent input cannot be retried, split, or manufactured): exec mode
	// must halt with the verdict, the reason, and the unexecuted
	// remainder NAMED — later steps hold a live Bash-capable agent that
	// must not act on a failed premise. (This test previously pinned the
	// pre-ladder flat first-block halt; the ladder is its consumer.)
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["read the config file", "modify the code", "push the branch"]`,
		`{"tool": "flag_stuck", "reason": "config.yml does not exist"}`,
		`{"tool": "complete_step", "result": "MUST NOT RUN", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "terminal halt case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" || len(res.Steps) != 1 {
		t.Fatalf("exec mode must halt on a terminal verdict: status=%s executed=%d",
			res.Status, len(res.Steps))
	}
	// Only planner + one step call reached the adapter.
	if len(fake.Opts) != 2 {
		t.Fatalf("later steps still executed: %d adapter calls", len(fake.Opts))
	}
	// The remainder is NAMED — contents, not just a count — and the
	// verdict + honest-fail doctrine ride the chain.
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	joined := ""
	for _, c := range chain {
		joined += c.(string) + "\n"
	}
	for _, want := range []string{"MISSING_INPUT", "config.yml does not exist",
		"halted on terminal verdict", "[stop: external-interrupt]",
		"modify the code", "push the branch"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("failure chain missing %q:\n%s", want, joined)
		}
	}
}

func TestToollessLaneKeepsRunThroughOnBlocked(t *testing.T) {
	// The v0 tool-less lane deliberately keeps run-through (steps hold no
	// tools; partial completion still yields the summary) — pin the
	// asymmetry so a future edit changes it on purpose, not by accident.
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := &llm.Fake{Script: []string{
		`["s1", "s2"]`,
		"", // empty content -> blocked
		"second step still ran",
	}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "toolless runthrough", MaxSteps: 2, DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(res.Steps) != 2 || res.Steps[1].Result != "second step still ran" {
		t.Fatalf("tool-less lane should run through: %+v", res.Steps)
	}
}

func TestExecLaneProjectDirFailureStillRecordsOutcome(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	// A stray FILE where the project dir must go makes MkdirAll fail.
	if err := os.MkdirAll(filepath.Join(ws, "projects"), 0o755); err != nil {
		t.Fatal(err)
	}
	blocker := filepath.Join(ws, "projects", goalSlug("blocked dirsetup goalcase"))
	if err := os.WriteFile(blocker, []byte("in the way"), 0o644); err != nil {
		t.Fatal(err)
	}
	fake := execFake(`["one step"]`, "unused")
	rec := record.New(ws)
	_, err := Run(context.Background(), fake, rec, Opts{
		Goal: "blocked dirsetup goalcase", MaxSteps: 2, DryRun: true, Exec: true})
	if err == nil {
		t.Fatal("expected setup error")
	}
	// The failed run still left a stuck outcome carrying the planning
	// spend — a run that leaves no record did not happen.
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	last := rows[len(rows)-1]
	if last["status"] != "stuck" {
		t.Fatalf("no stuck outcome recorded: %+v", last)
	}
	if !strings.Contains(fmt.Sprint(last["summary"]), "project dir setup failed") {
		t.Fatalf("summary: %+v", last["summary"])
	}
	if last["tokens_in"].(float64) <= 0 {
		t.Fatalf("planning spend lost: %+v", last["tokens_in"])
	}
}

func TestExecLaneStepTimeoutsReachTheAdapter(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["run pytest tests/test_foo.py", "write the summary"]`,
		`{"tool": "complete_step", "result": "tests pass", "summary": "s"}`,
		`{"tool": "complete_step", "result": "done", "summary": "s"}`,
	)
	rec := record.New(ws)
	if _, err := Run(context.Background(), fake, rec, Opts{
		Goal: "timeout wiring", MaxSteps: 2, DryRun: true, Exec: true}); err != nil {
		t.Fatal(err)
	}
	if got := fake.Opts[1].Timeout; got != 1800*time.Second {
		t.Fatalf("long-running step timeout not wired: %v", got)
	}
	if got := fake.Opts[2].Timeout; got != 600*time.Second {
		t.Fatalf("default step timeout not wired: %v", got)
	}
}

func TestExecLaneWrongTypeInjectStepsWarnsLoudly(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["one step"]`,
		`{"tool": "complete_step", "result": "r", "summary": "s", "inject_steps": "install foo"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "bad inject type", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, w := range res.Warnings {
		if strings.Contains(w, "inject_steps present but not an array") {
			found = true
		}
	}
	if !found {
		t.Fatalf("dropped inject_steps must warn: %+v", res.Warnings)
	}
}

func TestExecLaneInjectBlanksInterleaved(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["one step"]`,
		`{"tool": "complete_step", "result": "r", "summary": "s",
		  "inject_steps": ["a", "  ", "b", "", "c", "d"]}`,
		`{"tool": "complete_step", "result": "r", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "interleaved blanks", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	got := res.Steps[0].Injected
	if len(got) != 3 || got[0] != "a" || got[1] != "b" || got[2] != "c" {
		t.Fatalf("interleaved blanks mishandled: %+v", got)
	}
}

func TestExecLaneInjectedStepsAreTagged(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["main step"]`,
		`{"tool": "complete_step", "result": "r", "summary": "s", "inject_steps": ["extra"]}`,
		`{"tool": "complete_step", "result": "r2", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "tag injected", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Steps[0].WasInjected || !res.Steps[1].WasInjected {
		t.Fatalf("injected tagging wrong: %+v", res.Steps)
	}
}

func TestAcquireProjectSlotRefusesSecondHolder(t *testing.T) {
	mem := t.TempDir()
	rel1, warn, err := acquireProjectSlot(mem, "some-slug", "loop-a", "goal a")
	if err != nil || warn != "" || rel1 == nil {
		t.Fatalf("first acquire: rel-nil=%v warn=%q err=%v", rel1 == nil, warn, err)
	}
	// flock is per-open-file-description, so a second acquire in this
	// process (fresh open) models a second process faithfully.
	rel2, warn2, err2 := acquireProjectSlot(mem, "some-slug", "loop-b", "goal b")
	if err2 == nil || rel2 != nil {
		t.Fatalf("second acquire must refuse: rel-nil=%v warn=%q err=%v", rel2 == nil, warn2, err2)
	}
	if !strings.Contains(err2.Error(), "busy") || !strings.Contains(err2.Error(), "loop-a") {
		t.Fatalf("refusal must name the holder: %v", err2)
	}
	// Distinct slugs never contend.
	rel3, _, err3 := acquireProjectSlot(mem, "other-slug", "loop-c", "goal c")
	if err3 != nil || rel3 == nil {
		t.Fatalf("distinct slug blocked: %v", err3)
	}
	rel3()
	// Release frees the slot for the next run.
	rel1()
	rel4, _, err4 := acquireProjectSlot(mem, "some-slug", "loop-d", "goal d")
	if err4 != nil || rel4 == nil {
		t.Fatalf("acquire after release failed: %v", err4)
	}
	rel4()
}

func TestExecLaneBusyProjectRefusesAndRecordsStuck(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	// Another "run" already holds the slot this goal resolves to.
	hold, warn, err := acquireProjectSlot(filepath.Join(ws, "memory"),
		goalSlug("contended goalslot case"), "loop-holder", "the same goal")
	if err != nil || warn != "" {
		t.Fatalf("pre-hold: %v %q", err, warn)
	}
	defer hold()
	fake := execFake(`["one step"]`, "MUST NOT RUN")
	rec := record.New(ws)
	_, runErr := Run(context.Background(), fake, rec, Opts{
		Goal: "contended goalslot case", MaxSteps: 2, DryRun: true, Exec: true})
	if runErr == nil || !strings.Contains(runErr.Error(), "busy") {
		t.Fatalf("expected busy refusal, got %v", runErr)
	}
	// No step reached the adapter; the refusal is still a recorded outcome.
	if len(fake.Opts) != 1 {
		t.Fatalf("worker ran despite busy slot: %d calls", len(fake.Opts))
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	last := rows[len(rows)-1]
	if last["status"] != "stuck" {
		t.Fatalf("busy refusal must record stuck: %+v", last)
	}
	// The refused run created this dir on the way in (its Stat saw
	// nothing), but the slot holder may be a racing winner that just
	// MkdirAll'd the same path and hasn't written .mission yet — a
	// busy-refused loser must NEVER remove it (adversarial exec r3
	// 2026-08-22, Expert QA HIGH: the r2 cleanup rmdir'd the winner's
	// still-empty dir, failing both runs).
	dir := filepath.Join(ws, "projects", goalSlug("contended goalslot case"))
	if _, err := os.Stat(dir); err != nil {
		t.Fatalf("busy-refused run removed the slot holder's dir: %v", err)
	}
}

// gatedAdapter holds the first executor step in flight until released,
// so a test can act while a Run provably holds its project slot.
type gatedAdapter struct {
	*llm.Fake
	proceed chan struct{}
	entered chan struct{}
	once    sync.Once
}

func (g *gatedAdapter) Complete(ctx context.Context, msgs []llm.Message, opts llm.Options) (*llm.Response, error) {
	if opts.Purpose == "step-execute" {
		g.once.Do(func() { close(g.entered) })
		<-g.proceed
	}
	return g.Fake.Complete(ctx, msgs, opts)
}

func TestConcurrentRunsSameSlugWinnerSurvivesLoser(t *testing.T) {
	// Two Run invocations contending for one fresh slug — the gate/
	// cleanup INTERACTION the unit tests cannot see (adversarial exec
	// r3 2026-08-22, Expert QA finding 2).
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	goal := "concurrent winner keeps dir"
	gated := &gatedAdapter{
		Fake: execFake(`["one step"]`,
			`{"tool": "complete_step", "result": "winner result", "summary": "s"}`),
		proceed: make(chan struct{}),
		entered: make(chan struct{}),
	}
	type runOut struct {
		res *Result
		err error
	}
	winner := make(chan runOut, 1)
	go func() {
		r, e := Run(context.Background(), gated, record.New(ws), Opts{
			Goal: goal, MaxSteps: 2, DryRun: true, Exec: true})
		winner <- runOut{r, e}
	}()
	<-gated.entered // winner now holds the slot with a step in flight

	loser := execFake(`["one step"]`, "MUST NOT RUN")
	_, lerr := Run(context.Background(), loser, record.New(ws), Opts{
		Goal: goal, MaxSteps: 2, DryRun: true, Exec: true})
	if lerr == nil || !strings.Contains(lerr.Error(), "busy") {
		t.Fatalf("loser must be refused busy, got %v", lerr)
	}
	dir := filepath.Join(ws, "projects", goalSlug(goal))
	if got := recordedMission(dir); got != goal {
		t.Fatalf("loser damaged the winner's project state: mission=%q", got)
	}

	close(gated.proceed)
	w := <-winner
	if w.err != nil || w.res.Status != "done" {
		t.Fatalf("winner must complete untouched: err=%v res=%+v", w.err, w.res)
	}
}

func TestRecordedMissionFallsBackToPythonNextMD(t *testing.T) {
	root := t.TempDir()
	// A project the PYTHON runtime created: NEXT.md mission line, no
	// .mission file. Disambiguation must still see its mission.
	dir := filepath.Join(root, "tell-me-about-the-book")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	next := "# tell-me-about-the-book\n\nMission:\n\n> tell me about the book Systemantics\n\n## Now\n"
	if err := os.WriteFile(filepath.Join(dir, "NEXT.md"), []byte(next), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := recordedMission(dir); got != "tell me about the book Systemantics" {
		t.Fatalf("NEXT.md fallback: %q", got)
	}
	slug, _ := resolveProjectSlug(root, "tell me about the book Alexander")
	if slug != "tell-me-about-the-book-2" {
		t.Fatalf("python-created project not disambiguated: %q", slug)
	}
}

func TestGenericReuseWithoutEvidenceWarns(t *testing.T) {
	root := t.TempDir()
	// Existing generic-slug dir with NO recorded mission anywhere: the
	// reuse decision rests on missing evidence and must say so.
	if err := os.MkdirAll(filepath.Join(root, "tell-me-about-the-book"), 0o755); err != nil {
		t.Fatal(err)
	}
	slug, warn := resolveProjectSlug(root, "tell me about the book Systemantics")
	if slug != "tell-me-about-the-book" {
		t.Fatalf("slug = %q", slug)
	}
	if !strings.Contains(warn, "weak evidence") || !strings.Contains(warn, "no recorded mission") {
		t.Fatalf("evidence-free reuse must warn: %q", warn)
	}
}

func TestFailureChainEntriesRespectBudget(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	long := strings.Repeat("investigate the throughput regression in subsystem alpha ", 8)
	fake := execFake(
		`["`+long+` one", "`+long+` two", "`+long+` three", "`+long+` four"]`,
		`{"tool": "flag_stuck", "reason": "blocked immediately"}`,
	)
	rec := record.New(ws)
	if _, err := Run(context.Background(), fake, rec, Opts{
		Goal: "budget chain case", MaxSteps: 4, DryRun: true, Exec: true}); err != nil {
		t.Fatal(err)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	if len(chain) == 0 {
		t.Fatal("no failure chain")
	}
	max := budget.FailureChainEntry.Limit + 64 // limit + marker allowance
	for i, c := range chain {
		if n := len(c.(string)); n > max {
			t.Fatalf("failure chain entry %d is %d chars, budget is %d + marker: %q",
				i, n, budget.FailureChainEntry.Limit, c)
		}
	}
}

// TestErrorFingerprintPythonParity is GONE. Its two frozen md5 literals
// are a strict subset of what r6_diff_test.go's
// TestErrorFingerprintMatchesCPython now derives live from CPython over a
// corpus that includes the multi-byte cases the literals could not
// contain — and its last assertion was
//
//	errorFingerprint("a", "b") != errorFingerprint("a", "b")
//
// which is a pure function compared with itself: it cannot fail, and r6
// cited it as the worked example of "an assertion that cannot fire"
// while leaving it in place (adversarial mission-r7 LOW).

func TestIsConvergingThresholds(t *testing.T) {
	cases := []struct {
		fps  []string
		want bool
	}{
		{[]string{"a"}, true},                // too few to judge
		{[]string{"a", "a"}, false},          // 1/2 unique = .5, not > .5
		{[]string{"a", "b"}, true},           // all unique
		{[]string{"a", "a", "a"}, false},     // stuck loop
		{[]string{"a", "b", "c", "a"}, true}, // 3/4 unique
	}
	for i, c := range cases {
		if got := isConverging(c.fps); got != c.want {
			t.Fatalf("case %d: isConverging(%v)=%v", i, c.fps, got)
		}
	}
}

func TestSplitExecAnalyzeConverges(t *testing.T) {
	parts := splitExecAnalyze("Run pytest tests/ and summarize the failures")
	if len(parts) != 2 ||
		parts[0] != "Run pytest tests/ and save output to a file" ||
		parts[1] != "Read the captured output and summarize the failures" {
		t.Fatalf("split: %+v", parts)
	}
	// Neither half may re-trigger the compound detector — a re-split
	// loop at the plan-mutation seam would never converge.
	for _, p := range parts {
		if isCombinedExecAnalyze(p) {
			t.Fatalf("split half re-triggers the detector: %q", p)
		}
	}
}

func TestHeuristicTimeoutSplitEmulatesLookahead(t *testing.T) {
	// The Python regex splits bare " and " only BEFORE a Capitalized
	// clause (RE2 has no lookahead; Go emulates it).
	parts, _, _ := generateTimeoutSplit(context.Background(), nil,
		"download the dataset; clean the records and Compute the summary statistics")
	want := []string{"download the dataset", "clean the records", "Compute the summary statistics"}
	if len(parts) != 3 || parts[0] != want[0] || parts[1] != want[1] || parts[2] != want[2] {
		t.Fatalf("heuristic split: %+v", parts)
	}
	// Lowercase after "and" is NOT a boundary — and one part is no split.
	if got, _, _ := generateTimeoutSplit(context.Background(), nil, "read the file and analyze it thoroughly"); got != nil {
		t.Fatalf("lowercase and must not split: %+v", got)
	}
}

func TestHandleBlockedStepTimeoutTerminalWhenUnsplittable(t *testing.T) {
	out := StepOutcome{Step: "shortstep", Status: "blocked",
		Result: "claude CLI timed out after 10m0s (purpose=step-execute)"}
	d := handleBlockedStep(context.Background(), nil, "shortstep", out, 0, nil, nil, 0)
	if d.retry || d.redecompose || len(d.splitInto) != 0 {
		t.Fatalf("unsplittable timeout must be terminal: %+v", d)
	}
	if !strings.Contains(d.stuckReason, "TIMEOUT and split-recovery failed") ||
		d.stopVerdict != "out-of-budget" {
		t.Fatalf("terminal shape: %+v", d)
	}
}

func TestHandleBlockedStepSiblingFailureTriggersRedecompose(t *testing.T) {
	siblings := []StepOutcome{
		{Status: "blocked"}, {Status: "blocked"}, {Status: "done"},
	}
	out := StepOutcome{Step: "assemble the pieces", Status: "blocked", Result: "some error"}
	d := handleBlockedStep(context.Background(), nil, "assemble the pieces", out, 0, []string{"f1"}, siblings, 0)
	if !d.redecompose {
		t.Fatalf("67%% sibling failure must redecompose: %+v", d)
	}
	// Replan budget exhausted: falls through to the converging retry.
	d2 := handleBlockedStep(context.Background(), nil, "assemble the pieces", out, 0, []string{"f1"}, siblings, redecomposeThreshold)
	if !d2.retry {
		t.Fatalf("replan-exhausted sibling case must fall through to retry: %+v", d2)
	}
}

func TestExecLaneNeedInfoSpawnsResearchStep(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["find the dataset", "write the report"]`,
		`{"tool": "flag_stuck", "reason": "NEED_INFO: dataset URL", "attempted": "searched"}`,
		`{"tool": "complete_step", "result": "url is example.com/d.csv", "summary": "s"}`,
		`{"tool": "complete_step", "result": "dataset found", "summary": "s"}`,
		`{"tool": "complete_step", "result": "report done", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "need info case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 4 {
		t.Fatalf("status=%s steps=%d", res.Status, len(res.Steps))
	}
	// Research first, then the ORIGINAL step retried, then the plan.
	if res.Steps[1].Step != "Research: dataset URL" {
		t.Fatalf("research step: %q", res.Steps[1].Step)
	}
	if res.Steps[2].Step != "find the dataset" || res.Steps[2].Status != "done" {
		t.Fatalf("original step must rerun after research: %+v", res.Steps[2])
	}
	// A recovered run is DONE despite the blocked attempt in its record.
	if res.Steps[0].Status != "blocked" {
		t.Fatalf("blocked attempt must stay recorded: %+v", res.Steps[0])
	}
}

func TestExecLaneRetryHintReachesPrompt(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["assemble the summary"]`,
		`{"tool": "complete_step", "result": "", "summary": "s"}`, // empty → blocked
		`{"tool": "complete_step", "result": "summary assembled", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "hint threading", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 2 {
		t.Fatalf("status=%s steps=%d", res.Status, len(res.Steps))
	}
	retryPrompt := fake.Prompts[2]
	for _, want := range []string{
		"[Previous attempt blocked:",
		"RETRY REMINDER — ORIGINAL GOAL: hint threading",
		"NEED_INFO: [what's missing]",
	} {
		if !strings.Contains(retryPrompt, want) {
			t.Fatalf("retry prompt missing %q", want)
		}
	}
	// The first attempt's prompt must NOT carry a hint.
	if strings.Contains(fake.Prompts[1], "RETRY REMINDER") {
		t.Fatal("hint leaked into the first attempt")
	}
}

func TestExecLaneRedecomposeOnNonConvergingErrors(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["assemble the widget", "step b", "step c"]`,
		`{"tool": "flag_stuck", "reason": "identical error X"}`,
		`{"tool": "flag_stuck", "reason": "identical error X"}`, // same fp → not converging
		`["sub one", "sub two"]`,                                // re-decompose plan
		`{"tool": "complete_step", "result": "sub one done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "sub two done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "b done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "c done", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "redecompose case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 6 {
		t.Fatalf("status=%s steps=%d %+v", res.Status, len(res.Steps), res.Steps)
	}
	wantOrder := []string{"assemble the widget", "assemble the widget",
		"sub one", "sub two", "step b", "step c"}
	for i, w := range wantOrder {
		if res.Steps[i].Step != w {
			t.Fatalf("step %d = %q, want %q", i, res.Steps[i].Step, w)
		}
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	joined := ""
	for _, c := range chain {
		joined += c.(string) + "\n"
	}
	if !strings.Contains(joined, "re-decomposing into 2 sub-steps") {
		t.Fatalf("redecompose missing from chain:\n%s", joined)
	}
}

func TestExecLaneAdapterHungBailsAfterConsecutiveTimeouts(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	splitResp := "1. handle the first portion carefully\n2. handle the second portion carefully"
	fake := execFake(
		`["download alpha corpus fully", "download beta corpus fully", "download gamma corpus fully"]`,
		`{"tool": "flag_stuck", "reason": "operation timed out at the ceiling"}`,
		splitResp,
		`{"tool": "flag_stuck", "reason": "operation timed out at the ceiling"}`,
		splitResp,
		`{"tool": "flag_stuck", "reason": "operation timed out at the ceiling"}`,
		splitResp,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "hung adapter case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" {
		t.Fatalf("hung adapter must end stuck: %s", res.Status)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	joined := ""
	for _, c := range chain {
		joined += c.(string) + "\n"
	}
	if !strings.Contains(joined, "adapter appears hung") ||
		!strings.Contains(joined, "[stop: external-interrupt]") {
		t.Fatalf("hung bail missing from chain:\n%s", joined)
	}
}

// --- Ladder r1 fix-layer pins (adversarial ladder review 2026-08-22) ---

// Sibling-rate evidence must EXCLUDE the current attempt (Python decides
// before appending to step_outcomes) — self-counting fired premature
// redecompose on small plans (Skeptic + Expert QA HIGH).
func TestExecLaneSiblingRateExcludesCurrentAttempt(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["alpha probe task", "beta probe task", "gamma probe task"]`,
		`{"tool": "flag_stuck", "reason": "transient error A"}`,
		`{"tool": "complete_step", "result": "alpha done", "summary": "s"}`,
		`{"tool": "flag_stuck", "reason": "transient error B"}`,
		`{"tool": "complete_step", "result": "beta done", "summary": "s"}`,
		`{"tool": "flag_stuck", "reason": "transient error C"}`,
		`{"tool": "complete_step", "result": "gamma done", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "sibling exclusion case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	// Every block must resolve as a plain retry: with self-counting, the
	// beta block already sees 2/3 blocked (>50%, >=3) and redecomposes.
	if res.Status != "done" || len(res.Steps) != 6 {
		t.Fatalf("status=%s steps=%d %+v", res.Status, len(res.Steps), res.Steps)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	joined := ""
	for _, c := range chain {
		joined += c.(string) + "\n"
	}
	if strings.Contains(joined, "sibling failure rate") {
		t.Fatalf("self-counted sibling redecompose fired:\n%s", joined)
	}
	if !strings.Contains(joined, "step 4 retry 1 with hint") {
		t.Fatalf("gamma block did not resolve as a retry:\n%s", joined)
	}
}

// MISSING_INPUT keeps the wide reason: the inner clip is Python's
// clip(block_reason, 1000), not the 600-char chain budget that spent the
// whole entry on the raw reason and dropped the do-not-fabricate
// instruction (Architect HIGH).
func TestHandleBlockedStepMissingInputKeepsWideReason(t *testing.T) {
	reason := "config.yml does not exist; " +
		strings.Repeat("context detail ", 40) + "TAIL-MARKER-SURVIVES"
	out := StepOutcome{Step: "read the config file", Status: "blocked",
		Result: "flag_stuck: " + reason, StuckReason: reason}
	d := handleBlockedStep(context.Background(), nil, "read the config file",
		out, 0, nil, nil, 0)
	if d.stopVerdict != "external-interrupt" ||
		!strings.HasPrefix(d.stuckReason, "MISSING_INPUT") {
		t.Fatalf("expected missing-input terminal, got %+v", d)
	}
	if !strings.Contains(d.stuckReason, "TAIL-MARKER-SURVIVES") {
		t.Fatalf("647-char reason was clipped below Python's 1000 bound: %q", d.stuckReason)
	}
	if !strings.Contains(d.stuckReason, "fabricating one") {
		t.Fatalf("do-not-fabricate instruction lost: %q", d.stuckReason)
	}
}

// Python checks BOTH signal sources (_looks_like_missing_input on
// block_reason OR step_result); only the attempted text names the
// missing resource here (Skeptic).
func TestHandleBlockedStepMissingInputSeesAttemptedSignal(t *testing.T) {
	out := StepOutcome{Step: "read the customer data file", Status: "blocked",
		Result:      "flag_stuck: the schema validation step reported an unspecified failure",
		StuckReason: "the schema validation step reported an unspecified failure",
		Attempted:   "searched the workspace but data.csv does not exist"}
	d := handleBlockedStep(context.Background(), nil, "read the customer data file",
		out, 0, nil, nil, 0)
	if !strings.HasPrefix(d.stuckReason, "MISSING_INPUT") ||
		d.stopVerdict != "external-interrupt" {
		t.Fatalf("attempted-text signal missed, got %+v", d)
	}
}

// The INITIAL plan is shaped too (Python _prepare_execution,
// label="initial-plan") — a combined exec+analyze step must be split
// before it ever burns a worker call (Minimalist HIGH).
func TestExecLaneInitialPlanIsShaped(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	combined := "run the full test suite and analyze the failure output"
	fake := execFake(
		`["`+combined+`", "write the final summary"]`,
		`{"tool": "complete_step", "result": "suite ran", "summary": "s"}`,
		`{"tool": "complete_step", "result": "failures analyzed", "summary": "s"}`,
		`{"tool": "complete_step", "result": "summary written", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "initial shaping case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 3 {
		t.Fatalf("combined step not pre-split: status=%s steps=%d %+v",
			res.Status, len(res.Steps), res.Steps)
	}
	for i, s := range res.Steps {
		if s.Step == combined {
			t.Fatalf("step %d executed in its combined form: %q", i, s.Step)
		}
	}
}

// Timeout-split / refinement-hint LLM calls are real spend — their usage
// must land in the outcome totals (Expert QA HIGH; same class as the
// exec-r2 failed-turn salvage).
func TestExecLaneRecoveryCallsCountTokens(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	fake := execFake(
		`["download the huge corpus entirely", "compose final note"]`,
		`{"tool": "flag_stuck", "reason": "operation timed out at the ceiling"}`,
		"1. fetch corpus part one now\n2. fetch corpus part two now",
		`{"tool": "complete_step", "result": "part one done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "part two done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "note composed", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "recovery spend case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" {
		t.Fatalf("status=%s %+v", res.Status, res.Steps)
	}
	// Fake bills 10/5 per call: plan + 4 step attempts + 1 split call.
	if res.TokensIn != 60 || res.TokensOut != 30 {
		t.Fatalf("recovery-call usage dropped: in=%d out=%d (want 60/30)",
			res.TokensIn, res.TokensOut)
	}
}

// A successful step resets the consecutive-timeout streak (Python
// loop_execute.py:1884) — without it, non-consecutive timeouts
// accumulate to a false adapter-hung bail (Expert QA HIGH).
func TestExecLaneTimeoutStreakResetsOnSuccess(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	tblock := `{"tool": "flag_stuck", "reason": "request timed out at the ceiling"}`
	fake := execFake(
		`["download alpha corpus fully", "tiny middle task", "download beta corpus fully"]`,
		tblock,
		"1. fetch alpha shard one now\n2. fetch alpha shard two now",
		`{"tool": "complete_step", "result": "shard one done", "summary": "s"}`,
		tblock,
		"1. fetch beta shard one now\n2. fetch beta shard two now",
		tblock,
		"1. fetch gamma shard one now\n2. fetch gamma shard two now",
		`{"tool": "complete_step", "result": "gamma one done", "summary": "s"}`,
		`{"tool": "complete_step", "result": "gamma two done", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "timeout streak case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	chain, _ := rows[len(rows)-1]["failure_chain"].([]any)
	joined := ""
	for _, c := range chain {
		joined += c.(string) + "\n"
	}
	// Streak with the success in between is 1,0,1,2 — never 3. Without
	// the reset it reads 1,1,2,3 and bails "adapter appears hung".
	if strings.Contains(joined, "adapter appears hung") {
		t.Fatalf("false adapter-hung bail despite interleaved success:\n%s", joined)
	}
	if len(res.Steps) != 6 {
		t.Fatalf("run bailed early: %d steps %+v", len(res.Steps), res.Steps)
	}
}

// flag_stuck carries the typed (StuckReason, Attempted) pair mirroring
// Python's separate fields — folding both into Result gave the
// fingerprint ONE shared 200-char head, so long-reason retries that
// differed only in attempted collapsed to one fingerprint (three lenses).
func TestFlagStuckCarriesTypedReasonAndAttempted(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	reason := strings.TrimSpace(
		strings.Repeat("the widget assembler rejected the payload shape ", 5)) // ~240 chars
	fake := execFake(
		`["examine the widget data"]`,
		`{"tool": "flag_stuck", "reason": "`+reason+`", "attempted": "tried parsing with the old schema"}`,
		`{"tool": "complete_step", "result": "widget examined", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "typed fields case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	b := res.Steps[0]
	if b.Status != "blocked" || b.StuckReason != reason ||
		b.Attempted != "tried parsing with the old schema" {
		t.Fatalf("typed fields not populated: %+v", b)
	}
	if !strings.HasPrefix(b.Result, "flag_stuck: ") {
		t.Fatalf("folded Result form lost: %q", b.Result)
	}
	// Independent 200-char heads: same >200-char reason, different
	// attempted → DIFFERENT fingerprints (the folded form collapsed them).
	if errorFingerprint(reason, "attempt one") == errorFingerprint(reason, "attempt two") {
		t.Fatal("attempted text no longer discriminates fingerprints")
	}
}

// --- Ladder r2 fix-layer pins (adversarial ladder r2 2026-08-22) ---

// The verdict tag and the whole stuck reason must survive to the
// PERSISTED record: the tag rides after the chain entry's single clip,
// and the typed stop_verdict/stuck_reason columns carry the full
// evidence (both r2 lenses' HIGH — the r1 pin asserted the decision
// struct, not the flow, and the outer clip ate the tag on long reasons).
func TestExecLaneTerminalVerdictSurvivesPersistedRecord(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	reason := "the ledger file does not exist: " +
		strings.Repeat("checked location detail ", 19) + "TAIL-MARKER-XYZ" // ~500 chars
	fake := execFake(
		`["read the ledger file"]`,
		`{"tool": "flag_stuck", "reason": "`+reason+`"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "persisted verdict case", MaxSteps: 2, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "stuck" || res.StopVerdict != "external-interrupt" {
		t.Fatalf("status=%s verdict=%q", res.Status, res.StopVerdict)
	}
	rows := readJSONL(t, filepath.Join(ws, "memory", "outcomes.jsonl"))
	row := rows[len(rows)-1]
	if row["stop_verdict"] != "external-interrupt" {
		t.Fatalf("typed stop_verdict column: %v", row["stop_verdict"])
	}
	sr, _ := row["stuck_reason"].(string)
	if !strings.Contains(sr, "TAIL-MARKER-XYZ") || !strings.Contains(sr, "fabricating one") {
		t.Fatalf("typed stuck_reason lost evidence or instruction: %q", sr)
	}
	chain, _ := row["failure_chain"].([]any)
	tagged := false
	for _, c := range chain {
		if strings.Contains(c.(string), "halted on terminal verdict") &&
			strings.HasSuffix(strings.TrimSpace(c.(string)), "[stop: external-interrupt]") {
			tagged = true
		}
	}
	if !tagged {
		t.Fatalf("verdict tag clipped from the persisted chain entry: %+v", chain)
	}
}

// Worker-injected steps are shaped like every other plan-mutation
// surface (Python loop_post_step.py label="inject") — a combined
// exec+analyze injection must not execute in its broken form
// (r2 Skeptic HIGH: three of four shaping sites were ported).
func TestExecLaneInjectedStepsAreShaped(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	combined := "run the linter and analyze the results"
	fake := execFake(
		`["main step", "closing step"]`,
		`{"tool": "complete_step", "result": "found follow-up", "summary": "s",
		  "inject_steps": ["`+combined+`"]}`,
		`{"tool": "complete_step", "result": "linter ran", "summary": "s"}`,
		`{"tool": "complete_step", "result": "results analyzed", "summary": "s"}`,
		`{"tool": "complete_step", "result": "closed", "summary": "s"}`,
	)
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "inject shaping case", MaxSteps: 4, DryRun: true, Exec: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 4 {
		t.Fatalf("status=%s steps=%d %+v", res.Status, len(res.Steps), res.Steps)
	}
	for i, s := range res.Steps {
		if s.Step == combined {
			t.Fatalf("step %d executed the combined injection raw: %q", i, s.Step)
		}
	}
	// The shaped children keep the worker-injected audit mark.
	if !res.Steps[1].WasInjected || !res.Steps[2].WasInjected {
		t.Fatalf("injected mark lost on shaped children: %+v", res.Steps)
	}
}

// Initial-plan shaping is UNCONDITIONAL (Python _prepare_execution runs
// before any lane branch) — the tool-less lane splits combined steps
// too (r2 Skeptic: the r1 fix gated it to exec mode unnamed).
func TestToollessLaneInitialPlanIsShaped(t *testing.T) {
	ws := t.TempDir()
	t.Setenv("MARO_WORKSPACE", ws)
	combined := "run the benchmark suite and analyze the timing results"
	fake := &llm.Fake{Script: []string{
		`["` + combined + `"]`,
		"benchmark output captured",
		"timings look flat",
	}}
	rec := record.New(ws)
	res, err := Run(context.Background(), fake, rec, Opts{
		Goal: "tool-less shaping case", MaxSteps: 2, DryRun: true})
	if err != nil {
		t.Fatal(err)
	}
	if res.Status != "done" || len(res.Steps) != 2 {
		t.Fatalf("combined step not split on tool-less lane: status=%s steps=%d %+v",
			res.Status, len(res.Steps), res.Steps)
	}
	for i, s := range res.Steps {
		if s.Step == combined {
			t.Fatalf("step %d ran combined: %q", i, s.Step)
		}
	}
}
