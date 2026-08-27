package loopparallel

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The probe drives the REAL _run_parallel_batch and _run_parallel_path with
// the schedulers, the ledger, metrics and loop_post_step replaced. What is
// compared is everything either fold produces: the returned tuple, the
// StepOutcomes it appended (as `dataclasses.asdict`, so a field added on
// one side only is a length mismatch), the three lists it mutated in
// place, every outbound call in order with its arguments, every log line
// with its level, the stderr an operator reads, and the exception class
// and message when one escapes.
//
// NO FIELD IN A SCENARIO CARRIES `omitempty` — internal/portguard enforces
// it. Defaults belong in the builder below, where both languages read the
// same value.

type behSpec struct {
	SchedRaise     string `json:"sched_raise"`
	MarkRaise      string `json:"mark_raise"`
	AppendRaise    string `json:"append_raise"`
	AppendResult   []int  `json:"append_result"`
	CostRaise      string `json:"cost_raise"`
	DecisionsRaise string `json:"decisions_raise"`
	FactsRaise     string `json:"facts_raise"`
	Callback       bool   `json:"callback"`
	CallbackRaise  string `json:"callback_raise"`
}

type foldScenario struct {
	Kind    string `json:"kind"`
	Name    string `json:"name"`
	Why     string `json:"-"`
	Goal    string `json:"goal"`
	Project string `json:"project"`
	LoopID  string `json:"loop_id"`
	Verbose bool   `json:"verbose"`
	// Ancestry is ctx.ancestry_context. pending_context is None in every
	// scenario: the drain has its own differential, and both sides run the
	// REAL DrainPendingContext over that None.
	Ancestry  string           `json:"ancestry"`
	ModelKey  string           `json:"model_key"`
	StartedAt float64          `json:"started_at"`
	Ticks     []float64        `json:"ticks"`
	Tools     []map[string]any `json:"tools"`
	Outcomes  []map[string]any `json:"outcomes"`
	Beh       *behSpec         `json:"beh"`
	FanOut    int              `json:"fan_out"`
	ArtDir    string           `json:"artifact_dir"`

	// batch
	StepText         string   `json:"step_text"`
	Peers            []string `json:"peers"`
	CompletedContext []string `json:"completed_context"`
	RemainingSteps   []string `json:"remaining_steps"`
	RemainingIndices []int    `json:"remaining_indices"`
	Iteration        int      `json:"iteration"`
	StepIdx          int      `json:"step_idx"`
	// ItemIndices is Optional[List[int]]: nil marshals to null, which is
	// Python's None, and an EMPTY list is falsy there too.
	ItemIndices []int `json:"item_indices"`

	// path
	Steps          []string         `json:"steps"`
	CleanSteps     []string         `json:"clean_steps"`
	Deps           map[string][]int `json:"deps"`
	Levels         *[]any           `json:"levels"`
	ParallelLevels []any            `json:"parallel_levels"`
	UseDAG         bool             `json:"use_dag"`
}

func oc(kv ...any) map[string]any {
	m := map[string]any{}
	for i := 0; i+1 < len(kv); i += 2 {
		m[kv[i].(string)] = kv[i+1]
	}
	return m
}

func foldScenarios() []foldScenario {
	var sc []foldScenario
	add := func(kind, name, why string, mut func(*foldScenario)) {
		s := foldScenario{Kind: kind, Name: name, Why: why, Beh: &behSpec{}}
		mut(&s)
		sc = append(sc, s)
	}

	done := func(text string) map[string]any {
		return oc("status", "done", "result", "R:"+text, "tokens_in", 3.0,
			"tokens_out", 4.0, "cache_read_tokens", 1.0, "confidence", "strong",
			"summary", "S:"+text, "call_record", "/calls/"+text+".json")
	}
	blocked := func(text string) map[string]any {
		return oc("status", "blocked", "stuck_reason", "why "+text,
			"tokens_in", 1.0, "tokens_out", 2.0)
	}

	// ---- batch --------------------------------------------------------
	add("batch", "batch-one-done", "the plain shape", func(s *foldScenario) {
		s.StepText = "lead"
		s.Outcomes = []map[string]any{done("lead")}
	})
	add("batch", "batch-lead-and-two-peers",
		"iteration is bumped ONCE for the whole batch, before the fold",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Peers = []string{"p1", "p2"}
			s.Outcomes = []map[string]any{done("lead"), blocked("p1"), done("p2")}
		})
	add("batch", "batch-verbose", "every [maro] line the batch writes",
		func(s *foldScenario) {
			s.Verbose = true
			s.StepText = "lead"
			s.Peers = []string{"p1"}
			s.Outcomes = []map[string]any{done("lead"), blocked("p1")}
		})
	add("batch", "batch-fan-out-caps-at-batch-size",
		"max_workers is min(parallel_fan_out, len(batch))",
		func(s *foldScenario) {
			s.FanOut = 9
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-fan-out-below-batch-size", "and the other way",
		func(s *foldScenario) {
			s.FanOut = 1
			s.StepText = "lead"
			s.Peers = []string{"p1", "p2"}
			s.Outcomes = []map[string]any{done("lead"), done("p1"), done("p2")}
		})
	add("batch", "batch-item-indices-mark-done",
		"a done step with a real ledger index marks it in NEXT.md",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Peers = []string{"p1"}
			s.ItemIndices = []int{4, 7}
			s.Outcomes = []map[string]any{done("lead"), done("p1")}
		})
	add("batch", "batch-item-indices-short",
		"fewer indices than steps: the tail falls to the -1 sentinel",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Peers = []string{"p1", "p2"}
			s.ItemIndices = []int{4}
			s.Outcomes = []map[string]any{done("lead"), done("p1"), done("p2")}
		})
	add("batch", "batch-item-indices-empty-is-falsy",
		"an EMPTY list is falsy in Python, so it is not 'indices I have'",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.ItemIndices = []int{}
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-item-index-zero-is-a-real-item",
		"the mark gate is >= 0, and item 0 is the FIRST row of NEXT.md",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.ItemIndices = []int{0}
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-negative-item-index-skips-mark",
		"item_idx >= 0 gates the ledger write, and -1 is the sentinel",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.ItemIndices = []int{-1}
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-blocked-does-not-mark",
		"only a done step reaches mark_item",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.ItemIndices = []int{4}
			s.Outcomes = []map[string]any{blocked("lead")}
		})
	add("batch", "batch-unknown-status",
		"neither done nor blocked: no excerpt, no decisions, no verbose line",
		func(s *foldScenario) {
			s.Verbose = true
			s.StepText = "lead"
			s.Outcomes = []map[string]any{oc("status", "skipped", "result", "R")}
		})
	add("batch", "batch-missing-status-defaults-blocked",
		"an outcome with no status at all",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{oc("result", "R")}
		})
	add("batch", "batch-empty-result-skips-excerpt",
		"`_b_result[:800] if _b_result else \"\"` — the guard, not the slice",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{oc("status", "done", "result", "")}
		})
	add("batch", "batch-long-result-and-label",
		"800 and 80 are CODE POINTS, and the text is not ASCII",
		func(s *foldScenario) {
			s.StepText = strings.Repeat("é", 120)
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", strings.Repeat("ü", 900))}
		})
	add("batch", "batch-zip-truncates-on-short-outcomes",
		"fewer outcomes than steps: the tail is silently unprocessed",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Peers = []string{"p1", "p2"}
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-zip-truncates-on-extra-outcomes",
		"more outcomes than steps: the extras still count in the cost line",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead"), done("ghost")}
		})
	add("batch", "batch-provider-cost-string",
		"float() takes a numeric STRING",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "done", "provider_cost_usd", "1.25")}
		})
	add("batch", "batch-provider-cost-null-is-zero",
		"the `or 0.0` runs before the float(), so None never converts",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "done", "provider_cost_usd", nil)}
		})
	add("batch", "batch-provider-cost-garbage-raises",
		"a TRUTHY non-number is the one case float() refuses",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "done", "provider_cost_usd", "later")}
		})
	add("batch", "batch-cost-failure-is-non-critical",
		"record_step_cost raising is a debug line and nothing else",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
			s.Beh.CostRaise = "ledger down"
		})
	add("batch", "batch-mark-failure-is-non-critical",
		"and so is mark_item",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.ItemIndices = []int{2}
			s.Outcomes = []map[string]any{done("lead")}
			s.Beh.MarkRaise = "no such item"
		})
	add("batch", "batch-decisions-failure-propagates",
		"the DECISION fan-out is deliberately not wrapped",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
			s.Beh.DecisionsRaise = "decisions exploded"
		})
	add("batch", "batch-world-facts-failure-propagates", "same for the other",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
			s.Beh.FactsRaise = "facts exploded"
		})
	add("batch", "batch-scheduler-raises",
		"_run_steps_parallel is not wrapped either",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
			s.Beh.SchedRaise = "pool died"
		})
	add("batch", "batch-inject",
		"injected steps are shaped, mirrored and PREPENDED",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.RemainingSteps = []string{"old1", "old2"}
			s.RemainingIndices = []int{9, 10}
			s.Beh.AppendResult = []int{21, 22}
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R",
					"inject_steps", []any{"a", "b"})}
		})
	add("batch", "batch-inject-strips-and-drops-empties",
		"`.strip()` filtered for emptiness; the RAW list still reaches the outcome",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Beh.AppendResult = []int{1, 2}
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R",
					"inject_steps", []any{"  a  ", "", "   ", "b"})}
		})
	add("batch", "batch-inject-caps-at-six",
		"the cap is on the COLLECTED list, before shaping",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Peers = []string{"p1"}
			s.Beh.AppendResult = []int{1, 2, 3, 4, 5, 6}
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R", "inject_steps",
					[]any{"a", "b", "c", "d"}),
				oc("status", "done", "result", "R", "inject_steps",
					[]any{"e", "f", "g"}),
			}
		})
	add("batch", "batch-inject-no-project-skips-mirror",
		"an empty project leaves every index at the -1 sentinel",
		func(s *foldScenario) {
			s.Project = ""
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R", "inject_steps", []any{"a"})}
		})
	add("batch", "batch-inject-mirror-fails",
		"a mirror failure degrades to the sentinel and WARNS",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R", "inject_steps", []any{"a"})}
			s.Beh.AppendRaise = "ledger locked"
		})
	add("batch", "batch-inject-mirror-wrong-length",
		"Python rebinds the whole list, so a short answer is carried as-is",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Beh.AppendResult = []int{5}
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R",
					"inject_steps", []any{"a", "b"})}
		})
	add("batch", "batch-inject-verbose", "one [maro] line per shaped step",
		func(s *foldScenario) {
			s.Verbose = true
			s.StepText = "lead"
			s.Beh.AppendResult = []int{1}
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", "R", "inject_steps", []any{"a"})}
		})
	add("batch", "batch-blocked-does-not-inject",
		"only a done step contributes injections",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{
				oc("status", "blocked", "inject_steps", []any{"a"})}
		})
	add("batch", "batch-ancestry-flows-to-scheduler",
		"the drain's second value is what the scheduler receives",
		func(s *foldScenario) {
			s.Ancestry = "ANCESTRY"
			s.StepText = "lead"
			s.Outcomes = []map[string]any{done("lead")}
		})
	add("batch", "batch-elapsed-is-batch-wide",
		"every member records the time since the BATCH started",
		func(s *foldScenario) {
			s.Ticks = []float64{100, 100.25, 100.5, 100.75}
			s.StepText = "lead"
			s.Peers = []string{"p1"}
			s.Outcomes = []map[string]any{done("lead"), done("p1")}
		})
	add("batch", "batch-step-idx-continues",
		"step_idx counts on from where the caller left it",
		func(s *foldScenario) {
			s.StepIdx = 40
			s.Iteration = 7
			s.StepText = "lead"
			s.Peers = []string{"p1"}
			s.Outcomes = []map[string]any{done("lead"), done("p1")}
		})
	add("batch", "batch-empty-outcomes",
		"a scheduler that returned nothing: no steps, still a cost line",
		func(s *foldScenario) {
			s.StepText = "lead"
			s.Outcomes = []map[string]any{}
		})

	// ---- path ---------------------------------------------------------
	add("path", "path-fanout-all-done", "the plain fan-out shape",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2"}
			s.Outcomes = []map[string]any{done("s1"), done("s2")}
		})
	add("path", "path-fanout-verbose", "the fan-out banner",
		func(s *foldScenario) {
			s.Verbose = true
			s.Steps = []string{"s1", "s2"}
			s.Outcomes = []map[string]any{done("s1"), done("s2")}
		})
	add("path", "path-dag-verbose", "the dag banner, with all four numbers",
		func(s *foldScenario) {
			s.Verbose = true
			s.UseDAG = true
			s.Steps = []string{"ignored"}
			s.CleanSteps = []string{"c1", "c2", "c3"}
			s.Levels = &[]any{"L0", "L1"}
			s.ParallelLevels = []any{"L1"}
			s.Outcomes = []map[string]any{done("c1"), done("c2"), done("c3")}
		})
	add("path", "path-dag-uses-clean-steps",
		"the dag folds clean_steps and the fan-out folds steps",
		func(s *foldScenario) {
			s.UseDAG = true
			s.Steps = []string{"raw1", "raw2"}
			s.CleanSteps = []string{"c1"}
			s.Levels = &[]any{}
			s.Outcomes = []map[string]any{done("c1")}
		})
	add("path", "path-dag-quiet-survives-none-levels",
		"len(None) is only reached on the VERBOSE dag line",
		func(s *foldScenario) {
			s.UseDAG = true
			s.CleanSteps = []string{"c1"}
			s.Levels = nil
			s.Outcomes = []map[string]any{done("c1")}
		})
	add("path", "path-dag-verbose-none-levels-raises",
		"and a verbose one dies on it",
		func(s *foldScenario) {
			s.Verbose = true
			s.UseDAG = true
			s.CleanSteps = []string{"c1"}
			s.Levels = nil
			s.Outcomes = []map[string]any{done("c1")}
		})
	add("path", "path-blocked-makes-the-run-stuck", "one blocked step is enough",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2"}
			s.Outcomes = []map[string]any{done("s1"), blocked("s2")}
		})
	add("path", "path-last-blocked-wins",
		"stuck_reason is ASSIGNED, so the last block is the one reported",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2", "s3"}
			s.Outcomes = []map[string]any{
				blocked("first"), done("s2"), blocked("last")}
		})
	add("path", "path-missing-status-defaults-blocked",
		"an outcome with no status at all makes the whole run stuck",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Outcomes = []map[string]any{oc("result", "R")}
		})
	add("path", "path-blocked-without-a-reason",
		"the default sentence names the step number",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2"}
			s.Outcomes = []map[string]any{done("s1"), oc("status", "blocked")}
		})
	add("path", "path-blocked-with-null-reason",
		"a key PRESENT and null is a different stop from the default",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Outcomes = []map[string]any{
				oc("status", "blocked", "stuck_reason", nil)}
		})
	add("path", "path-callback-sees-clipped-result",
		"the callback gets result[:120] by code point",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Beh.Callback = true
			s.Outcomes = []map[string]any{
				oc("status", "done", "result", strings.Repeat("ö", 200))}
		})
	add("path", "path-callback-raise-is-swallowed",
		"a raising callback is a debug line and the fold continues",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2"}
			s.Beh.Callback = true
			s.Beh.CallbackRaise = "callback exploded"
			s.Outcomes = []map[string]any{done("s1"), done("s2")}
		})
	add("path", "path-callback-runs-for-blocked-too",
		"the callback is outside the done branch",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Beh.Callback = true
			s.Outcomes = []map[string]any{blocked("s1")}
		})
	add("path", "path-decisions-failure-propagates",
		"unwrapped here as well",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Beh.DecisionsRaise = "decisions exploded"
			s.Outcomes = []map[string]any{done("s1")}
		})
	add("path", "path-scheduler-raises", "not wrapped",
		func(s *foldScenario) {
			s.Steps = []string{"s1"}
			s.Beh.SchedRaise = "pool died"
			s.Outcomes = []map[string]any{done("s1")}
		})
	add("path", "path-elapsed-spans-the-whole-run",
		"elapsed is measured from ctx.started_at, not from the fan-out",
		func(s *foldScenario) {
			s.StartedAt = 10
			s.Ticks = []float64{93.5}
			s.Steps = []string{"s1"}
			s.Outcomes = []map[string]any{done("s1")}
		})
	add("path", "path-empty-steps",
		"nothing to run: status stays done and the result is empty",
		func(s *foldScenario) {
			s.Steps = []string{}
			s.Outcomes = []map[string]any{}
		})
	add("path", "path-zip-truncates",
		"fewer outcomes than steps, same silent truncation",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2", "s3"}
			s.Outcomes = []map[string]any{done("s1")}
		})
	add("path", "path-item-index-is-the-counter",
		"the fan-out claims ledger indices 1..n whatever the project holds",
		func(s *foldScenario) {
			s.Steps = []string{"s1", "s2", "s3"}
			s.Outcomes = []map[string]any{done("s1"), done("s2"), done("s3")}
		})

	// Defaults, in ONE place, on the Go side. Both languages then read the
	// value a fixture chose rather than a default each invented.
	for i := range sc {
		s := &sc[i]
		if s.Goal == "" && s.Name != "batch-inject-no-project-skips-mirror" {
			s.Goal = "the goal"
		}
		if s.Project == "" && s.Name != "batch-inject-no-project-skips-mirror" {
			s.Project = "proj"
		}
		if s.LoopID == "" {
			s.LoopID = "loop-1"
		}
		if s.ModelKey == "" {
			s.ModelKey = "anthropic/x"
		}
		if s.FanOut == 0 {
			s.FanOut = 3
		}
		if s.ArtDir == "" {
			s.ArtDir = "/art"
		}
		if s.Ticks == nil {
			// Enough readings for any scenario here; the clock repeats its
			// last value once exhausted, on both sides.
			s.Ticks = []float64{100, 101, 102, 103, 104, 105, 106, 107}
		}
		if s.Beh == nil {
			s.Beh = &behSpec{}
		}
		if s.Beh.AppendResult == nil {
			s.Beh.AppendResult = []int{}
		}
		if s.Tools == nil {
			s.Tools = []map[string]any{{"name": "read"}}
		}
		if s.Outcomes == nil {
			s.Outcomes = []map[string]any{}
		}
		if s.Peers == nil {
			s.Peers = []string{}
		}
		if s.CompletedContext == nil {
			s.CompletedContext = []string{}
		}
		if s.RemainingSteps == nil {
			s.RemainingSteps = []string{}
		}
		if s.RemainingIndices == nil {
			s.RemainingIndices = []int{}
		}
		if s.Steps == nil {
			s.Steps = []string{}
		}
		if s.CleanSteps == nil {
			s.CleanSteps = []string{}
		}
		if s.Deps == nil {
			s.Deps = map[string][]int{}
		}
		if s.ParallelLevels == nil {
			s.ParallelLevels = []any{}
		}
	}
	return sc
}

func runFoldProbe(t *testing.T, scs []foldScenario) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	spec := filepath.Join(t.TempDir(), "s.json")
	if err := os.WriteFile(spec, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("python3", "folds_probe.py.tpl",
		srcDirLP(t), spec).Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Fatalf("probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("probe failed: %v", err)
	}
	var recs []map[string]any
	if err := json.Unmarshal(out, &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

// recorder mirrors the probe's call log, log lines and stderr buffer.
type recorder struct {
	calls  []any
	logs   []any
	stderr strings.Builder
	ticks  []float64
	i      int
}

func (r *recorder) monotonic() float64 {
	v := r.ticks[len(r.ticks)-1]
	if r.i < len(r.ticks) {
		v = r.ticks[r.i]
	}
	r.i++
	return v
}

func (r *recorder) call(name string, kw map[string]any) {
	r.calls = append(r.calls, []any{name, kw})
}

func (r *recorder) log(level, msg string) { r.logs = append(r.logs, []any{level, msg}) }

// asObj builds pyval's ORDERED dict from a fixture map. Key order is
// sorted because a Go map has none and json.Marshal picks that order
// anyway; nothing in either fold reads a dict positionally.
func asObj(m map[string]any) pyval.Obj {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	o := make(pyval.Obj, 0, len(keys))
	for _, k := range keys {
		o = append(o, pyval.Field{Key: k, Val: m[k]})
	}
	return o
}

func asMap(o pyval.Obj) map[string]any {
	m := map[string]any{}
	for _, f := range o {
		m[f.Key] = f.Val
	}
	return m
}

func objsOf(rows []map[string]any) []pyval.Obj {
	out := make([]pyval.Obj, 0, len(rows))
	for _, m := range rows {
		out = append(out, asObj(m))
	}
	return out
}

func toolKW(tools []pyval.Obj) []any {
	out := make([]any, 0, len(tools))
	for _, t := range tools {
		out = append(out, asMap(t))
	}
	return out
}

func boom(msg string) error {
	if msg == "" {
		return nil
	}
	return errors.New(msg)
}

func schedFn(s foldScenario, r *recorder, name string) func([]string, int,
	string, string, string, []pyval.Obj) ([]pyval.Obj, error) {
	return func(steps []string, maxWorkers int, projectDir, ancestry,
		incremental string, tools []pyval.Obj) ([]pyval.Obj, error) {
		r.call(name, map[string]any{
			"steps": steps, "max_workers": maxWorkers,
			"project_dir": projectDir, "ancestry_context": ancestry,
			"incremental_context": incremental, "tools": toolKW(tools)})
		if err := boom(s.Beh.SchedRaise); err != nil {
			return nil, err
		}
		return objsOf(s.Outcomes), nil
	}
}

func goBatch(s foldScenario) map[string]any {
	r := &recorder{ticks: s.Ticks}
	var outs []looptypes.StepOutcome
	completed := append([]string{}, s.CompletedContext...)
	remaining := append([]string{}, s.RemainingSteps...)
	indices := append([]int{}, s.RemainingIndices...)

	d := BatchDeps{
		Monotonic:        r.monotonic,
		ResolveTools:     func() []pyval.Obj { return objsOf(s.Tools) },
		RunStepsParallel: schedFn(s, r, "run_steps_parallel"),
		RecordStepCost: func(in StepCostIn) error {
			r.call("record_step_cost", map[string]any{
				"step_text": in.StepText, "tokens_in": in.TokensIn,
				"tokens_out": in.TokensOut, "status": in.Status,
				"goal": in.Goal, "model": in.Model,
				"elapsed_ms":        in.ElapsedMS,
				"cache_read_tokens": in.CacheReadTokens,
				"loop_id":           in.LoopID,
				"provider_cost_usd": in.ProviderCostUSD})
			return boom(s.Beh.CostRaise)
		},
		MarkItemDone: func(project string, index int) error {
			r.call("mark_item", map[string]any{
				"project": project, "index": index, "state": "done"})
			return boom(s.Beh.MarkRaise)
		},
		AppendNextItems: func(project string, items []string) ([]int, error) {
			r.call("append_next_items", map[string]any{
				"project": project, "items": items})
			if err := boom(s.Beh.AppendRaise); err != nil {
				return nil, err
			}
			return s.Beh.AppendResult, nil
		},
		RecordStepDecisions: func(idx string, _ pyval.Obj) error {
			r.call("record_step_decisions", map[string]any{"step_idx": idx})
			return boom(s.Beh.DecisionsRaise)
		},
		RecordStepWorldFacts: func(idx string, _ pyval.Obj) error {
			r.call("record_step_world_facts", map[string]any{"step_idx": idx})
			return boom(s.Beh.FactsRaise)
		},
		ShapeSteps: func(steps []string, label string) []string {
			r.call("shape_steps", map[string]any{
				"steps": steps, "label": label})
			out := make([]string, len(steps))
			for i, v := range steps {
				out[i] = strings.ToUpper(v)
			}
			return out
		},
		Stderr:  func(line string) { r.stderr.WriteString(line + "\n") },
		Info:    func(m string) { r.log("info", m) },
		Debug:   func(m string) { r.log("debug", m) },
		Warning: func(m string) { r.log("warning", m) },
	}

	rec := map[string]any{"name": s.Name}
	got, err := RunParallelBatch(BatchCtx{
		Goal: s.Goal, Project: s.Project, LoopID: s.LoopID,
		Verbose: s.Verbose, ModelKey: s.ModelKey, Ancestry: s.Ancestry,
	}, BatchIn{
		StepText: s.StepText, Peers: s.Peers,
		StepOutcomes: &outs, CompletedContext: &completed,
		RemainingSteps: &remaining, RemainingIndices: &indices,
		ParallelFanOut: s.FanOut, ProjArtifactDir: s.ArtDir,
		Iteration: s.Iteration, StepIdx: s.StepIdx,
		BatchItemIndices: s.ItemIndices,
	}, d)
	if err != nil {
		rec["error"] = pyExcName(err) + ": " + err.Error()
	} else {
		rec["ret"] = []any{got.Iteration, got.StepIdx, got.TokensIn,
			got.TokensOut, got.CacheRead, got.ProviderCost}
	}
	rec["step_outcomes"] = dictsOf(outs)
	rec["completed_context"] = completed
	rec["remaining_steps"] = remaining
	rec["remaining_indices"] = indices
	rec["calls"] = orEmptyAny(r.calls)
	rec["log"] = orEmptyAny(r.logs)
	rec["stderr"] = r.stderr.String()
	return rec
}

func goPath(s foldScenario) map[string]any {
	r := &recorder{ticks: s.Ticks}
	var cb []any
	ctx := PathCtx{
		Goal: s.Goal, Project: s.Project, LoopID: s.LoopID,
		Verbose: s.Verbose, Ancestry: s.Ancestry, StartedAt: s.StartedAt,
	}
	if s.Beh.Callback {
		ctx.StepCallback = func(i int, text, result, status string) error {
			cb = append(cb, []any{i, text, result, status})
			return boom(s.Beh.CallbackRaise)
		}
	}
	deps := map[int][]int{}
	for k, v := range s.Deps {
		var n int
		fmt.Sscanf(k, "%d", &n)
		deps[n] = v
	}
	d := PathDeps{
		Monotonic:        r.monotonic,
		ResolveTools:     func() []pyval.Obj { return objsOf(s.Tools) },
		RunStepsParallel: schedFn(s, r, "run_steps_parallel"),
		RunStepsDAG: func(steps []string, _ map[int][]int, maxWorkers int,
			projectDir, ancestry, incremental string,
			tools []pyval.Obj) ([]pyval.Obj, error) {
			return schedFn(s, r, "run_steps_dag")(steps, maxWorkers,
				projectDir, ancestry, incremental, tools)
		},
		RecordStepDecisions: func(idx string, _ pyval.Obj) error {
			r.call("record_step_decisions", map[string]any{"step_idx": idx})
			return boom(s.Beh.DecisionsRaise)
		},
		RecordStepWorldFacts: func(idx string, _ pyval.Obj) error {
			r.call("record_step_world_facts", map[string]any{"step_idx": idx})
			return boom(s.Beh.FactsRaise)
		},
		Stderr: func(line string) { r.stderr.WriteString(line + "\n") },
		Debug:  func(m string) { r.log("debug", m) },
	}

	rec := map[string]any{"name": s.Name}
	res, err := RunParallelPath(ctx, PathIn{
		Steps: s.Steps, CleanSteps: s.CleanSteps, Deps: deps,
		Levels: s.Levels, ParallelLevels: s.ParallelLevels,
		ParallelFanOut: s.FanOut, ProjFanoutDir: s.ArtDir,
		UseDAG: s.UseDAG,
	}, d)
	if err != nil {
		rec["error"] = pyExcName(err) + ": " + err.Error()
	} else {
		var reason any
		if res.StuckReason != nil {
			reason = *res.StuckReason
		}
		rec["result"] = map[string]any{
			"loop_id": res.LoopID, "project": res.Project, "goal": res.Goal,
			"status": res.Status, "total_tokens_in": res.TotalTokensIn,
			"total_tokens_out": res.TotalTokensOut,
			"elapsed_ms":       res.ElapsedMS, "stuck_reason": reason,
			"audit_learning_allowed": res.AuditLearningAllowed,
			"steps":                  dictsOf(res.Steps),
		}
	}
	rec["callback"] = orEmptyAny(cb)
	rec["calls"] = orEmptyAny(r.calls)
	rec["log"] = orEmptyAny(r.logs)
	rec["stderr"] = r.stderr.String()
	return rec
}

// pyExcName is the exception CLASS CPython raises for each error this port
// returns. Comparing only the message would let a ValueError pass for a
// TypeError, and a caller that catches one and not the other is the whole
// reason the distinction exists (L22).
func pyExcName(err error) string {
	msg := err.Error()
	switch {
	case strings.HasPrefix(msg, "could not convert string to float"),
		strings.HasPrefix(msg, "float() argument must be"):
		if strings.HasPrefix(msg, "float() argument") {
			return "TypeError"
		}
		return "ValueError"
	case strings.Contains(msg, "has no len()"):
		return "TypeError"
	}
	return "Boom"
}

func dictsOf(outs []looptypes.StepOutcome) []map[string]any {
	rows := make([]map[string]any, 0, len(outs))
	for _, o := range outs {
		rows = append(rows, o.AsDict())
	}
	return rows
}

func orEmptyAny(v []any) []any {
	if v == nil {
		return []any{}
	}
	return v
}

func canonFold(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var round any
	if err := json.Unmarshal(b, &round); err != nil {
		t.Fatal(err)
	}
	b2, err := json.MarshalIndent(round, "", " ")
	if err != nil {
		t.Fatal(err)
	}
	return string(b2)
}

func firstDiffFold(want, got string) string {
	w, g := strings.Split(want, "\n"), strings.Split(got, "\n")
	n := len(w)
	if len(g) > n {
		n = len(g)
	}
	var b strings.Builder
	shown := 0
	for i := 0; i < n && shown < 8; i++ {
		var wl, gl string
		if i < len(w) {
			wl = w[i]
		}
		if i < len(g) {
			gl = g[i]
		}
		if wl != gl {
			fmt.Fprintf(&b, "line %d:\n  py: %s\n  go: %s\n", i+1, wl, gl)
			shown++
		}
	}
	return b.String()
}

func TestFoldsMatchCPython(t *testing.T) {
	scs := foldScenarios()
	py := runFoldProbe(t, scs)
	if len(py) != len(scs) {
		t.Fatalf("probe answered %d of %d scenarios", len(py), len(scs))
	}
	for i, s := range scs {
		var got map[string]any
		if s.Kind == "batch" {
			got = goBatch(s)
		} else {
			got = goPath(s)
		}
		gs, ws := canonFold(t, got), canonFold(t, py[i])
		if gs != ws {
			t.Errorf("scenario %q (%s) diverges:\n%s",
				s.Name, s.Why, firstDiffFold(ws, gs))
		}
	}
}

func TestFoldScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range foldScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q: one of the two is not "+
				"being compared against what its name says", s.Name)
		}
		seen[s.Name] = true
		if s.Why == "" {
			t.Errorf("scenario %q has no reason recorded", s.Name)
		}
	}
}

// The two `inject_steps` shapes the differential CANNOT carry, and why.
//
// Python's StepOutcome.injected_steps is typed List[str] and populated
// with `_batch_oc.get("inject_steps", [])` — the raw dict value, whatever
// it is. A step body that answered a string, a dict, or a list with a
// number in it puts that object straight into the outcome record. Go's
// field is []string and cannot hold any of them, so a scenario built on
// one would diverge on the outcome ROW for a reason that has nothing to
// do with the branch it was written to test.
//
// The branch itself is still worth pinning, so it is pinned here: what
// the INJECTION path does with a malformed value, which is the half that
// changes the plan. The representational gap is filed in BACKLOG.md.
func TestMalformedInjectStepsAffectsOnlyTheOutcomeRow(t *testing.T) {
	run := func(injectSteps any) (shaped bool, remaining []string) {
		var outs []looptypes.StepOutcome
		completed, remain, idxs := []string{}, []string{}, []int{}
		body := map[string]any{"status": "done", "result": "R"}
		if injectSteps != nil {
			body["inject_steps"] = injectSteps
		}
		d := BatchDeps{
			Monotonic:    func() float64 { return 1 },
			ResolveTools: func() []pyval.Obj { return nil },
			RunStepsParallel: func([]string, int, string, string, string,
				[]pyval.Obj) ([]pyval.Obj, error) {
				return []pyval.Obj{asObj(body)}, nil
			},
			RecordStepCost:       func(StepCostIn) error { return nil },
			MarkItemDone:         func(string, int) error { return nil },
			AppendNextItems:      func(string, []string) ([]int, error) { return nil, nil },
			RecordStepDecisions:  func(string, pyval.Obj) error { return nil },
			RecordStepWorldFacts: func(string, pyval.Obj) error { return nil },
			ShapeSteps: func(steps []string, _ string) []string {
				shaped = true
				return steps
			},
			Stderr: func(string) {}, Info: func(string) {},
			Debug: func(string) {}, Warning: func(string) {},
		}
		if _, err := RunParallelBatch(BatchCtx{Project: "p"}, BatchIn{
			StepText: "lead", StepOutcomes: &outs, CompletedContext: &completed,
			RemainingSteps: &remain, RemainingIndices: &idxs,
		}, d); err != nil {
			t.Fatal(err)
		}
		return shaped, remain
	}

	// `isinstance(_bi_inject, list)`: a bare string names no steps, so
	// nothing is shaped and the plan is untouched.
	if shaped, remain := run("a"); shaped || len(remain) != 0 {
		t.Errorf("a string inject_steps injected %v (shaped=%v); Python's "+
			"isinstance guard refuses it", remain, shaped)
	}
	// `str(s).strip()`: a non-string element IS stringified before the
	// empty check, so it becomes a step.
	if _, remain := run([]any{"  a  ", "", "   ", 7.5}); len(remain) != 2 ||
		remain[0] != "a" || remain[1] != "7.5" {
		t.Errorf("got %q; Python strips each str(s) and drops the empties",
			remain)
	}
}
