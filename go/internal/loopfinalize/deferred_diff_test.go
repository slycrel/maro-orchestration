package loopfinalize

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// finalize_deferred_learning is four halves separated by three returns,
// and the returns are the specification: which half a given loop reaches
// is decided by the skip set, by dry-run, by the status, by whether there
// are steps at all, and by two verdict gates that fail OPEN. None of that
// is legible from the call graph — every one of them is legible in the
// call log.
//
// The row is a REAL dataclass on the Python side and a pyval.Obj here,
// and both are built from the SAME fixture key list, in order: the field
// SET is load-bearing (`"success_class" in outcome` asks whether the
// field was declared, not whether it is set), so a fixture that omits a
// key is a different row, not the same row with a None in it.

type defStep struct {
	Index  int    `json:"index"`
	Text   string `json:"text"`
	Status string `json:"status"`
	Result string `json:"result"`
}

type defSpec struct {
	Name    string `json:"name"`
	Adapter string `json:"adapter"`

	LoopID   string `json:"loop_id"`
	NoLoopID bool   `json:"no_loop_id"`
	// ResultProject is loop_result.project; Project is the keyword
	// argument. `project or loop_result.project or ""` prefers the
	// keyword, so both have to be settable independently and one has to
	// be ABSENT-able — the read is a getattr with a default.
	ResultProject   string `json:"result_project"`
	NoResultProject bool   `json:"no_result_project"`
	Project         string `json:"project"`

	Goal          string    `json:"goal"`
	Status        string    `json:"status"`
	Steps         []defStep `json:"steps"`
	DryRun        bool      `json:"dry_run"`
	Verbose       bool      `json:"verbose"`
	HadNoMatching bool      `json:"had_no_matching"`

	ExtraLoopIDs     []string `json:"extra_loop_ids"`
	SkipLoopIDs      []string `json:"skip_loop_ids"`
	UnstampedLoopIDs []string `json:"unstamped_loop_ids"`

	// DeferredRaise is keyed by loop id: the per-loop handler is inside
	// the loop, so one id blowing up must not cost the ones after it.
	DeferredRaise    map[string]string `json:"deferred_raise"`
	StepLessonsRaise string            `json:"step_lessons_raise"`
	LoadRaise        string            `json:"load_raise"`

	// Row is an ordered [key, value] list, or nil for a missing row.
	Row       [][]any  `json:"row"`
	Extracted []string `json:"extracted"`

	DropNames []string `json:"drop_names"`
}

func defScenarios() []defSpec {
	var out []defSpec
	add := func(s defSpec) {
		if s.Adapter == "" {
			s.Adapter = "truthy"
		}
		if s.LoopID == "" && !s.NoLoopID {
			s.LoopID = "L1"
		}
		if s.Goal == "" {
			s.Goal = "ship the thing"
		}
		if s.Status == "" {
			s.Status = "done"
		}
		if s.Steps == nil {
			s.Steps = []defStep{{Index: 0, Text: "step one",
				Status: "done", Result: "did it"}}
		}
		if s.ExtraLoopIDs == nil {
			s.ExtraLoopIDs = []string{}
		}
		if s.SkipLoopIDs == nil {
			s.SkipLoopIDs = []string{}
		}
		if s.UnstampedLoopIDs == nil {
			s.UnstampedLoopIDs = []string{}
		}
		if s.DeferredRaise == nil {
			s.DeferredRaise = map[string]string{}
		}
		if s.Extracted == nil {
			s.Extracted = []string{}
		}
		if s.DropNames == nil {
			s.DropNames = []string{}
		}
		out = append(out, s)
	}
	// judged returns a row shaped like the ledger's: the key ORDER is
	// the ledger's own, and every fixture that wants a different verdict
	// edits values rather than reshuffling keys, so a divergence points
	// at the gate and not at the row builder.
	judged := func(achieved any, source string, conf any) [][]any {
		return [][]any{
			{"loop_id", "L1"}, {"status", "done"},
			{"goal_achieved", achieved},
			{"goal_verdict_source", source},
			{"goal_verdict_confidence", conf},
		}
	}

	// --- the whole pipeline, end to end ---------------------------------
	add(defSpec{Name: "every-half-runs",
		Row: judged(true, "closure_check", 0.9), Extracted: []string{"s1"}})
	add(defSpec{Name: "no-row-at-all-still-crystallizes"})
	add(defSpec{Name: "falsy-adapter-is-not-none", Adapter: "none"})
	add(defSpec{Name: "verbose-reaches-crystallization", Verbose: true,
		Extracted: []string{"s1"}})
	add(defSpec{Name: "synthesis-rides-on-had-no-matching",
		HadNoMatching: true})

	// --- half one: the lesson loop --------------------------------------
	add(defSpec{Name: "extras-first-then-this-loop",
		ExtraLoopIDs: []string{"E1", "E2"}})
	add(defSpec{Name: "empty-loop-id-is-skipped-not-extracted",
		NoLoopID: true, ExtraLoopIDs: []string{"E1"}})
	add(defSpec{Name: "blank-extra-id-is-skipped-too",
		ExtraLoopIDs: []string{"", "E2"}})
	add(defSpec{Name: "a-skipped-extra-still-lets-the-others-run",
		ExtraLoopIDs: []string{"E1", "E2"}, SkipLoopIDs: []string{"E1"}})
	add(defSpec{Name: "one-extra-blowing-up-does-not-cost-the-next",
		ExtraLoopIDs:  []string{"E1", "E2"},
		DeferredRaise: map[string]string{"E1": "extractor died"}})
	add(defSpec{Name: "this-loop-blowing-up-warns-and-continues",
		DeferredRaise: map[string]string{"L1": "extractor died"}})
	add(defSpec{Name: "import-warning-not-the-per-loop-one",
		ExtraLoopIDs: []string{"E1"},
		DropNames:    []string{"extract_deferred_lessons"}})

	// --- the skip return, which sits BETWEEN the halves ------------------
	add(defSpec{Name: "skipped-loop-gets-extras-lessons-and-nothing-else",
		ExtraLoopIDs: []string{"E1"}, SkipLoopIDs: []string{"L1"},
		Row: judged(true, "closure_check", 0.9)})
	add(defSpec{Name: "unstamped-is-an-alias-for-skip",
		UnstampedLoopIDs: []string{"L1"}})
	add(defSpec{Name: "the-two-skip-lists-are-unioned",
		ExtraLoopIDs: []string{"E1", "E2"}, SkipLoopIDs: []string{"E1"},
		UnstampedLoopIDs: []string{"E2"}})

	// --- half two: the mint, and the project expression -----------------
	add(defSpec{Name: "keyword-project-wins", Project: "kw",
		ResultProject: "res"})
	add(defSpec{Name: "result-project-when-the-keyword-is-blank",
		ResultProject: "res"})
	add(defSpec{Name: "no-project-anywhere-is-the-empty-string",
		NoResultProject: true})
	add(defSpec{Name: "mint-runs-before-the-step-lessons",
		Row: judged(false, "closure_check", 0.9)})

	// --- half three: post-verdict step lessons --------------------------
	add(defSpec{Name: "unlearnable-row-extracts-step-lessons",
		Row: judged(false, "closure_check", 0.9)})
	add(defSpec{Name: "learnable-row-extracts-nothing",
		Row: judged(true, "closure_check", 0.9)})
	add(defSpec{Name: "missing-row-extracts-nothing-either"})
	add(defSpec{Name: "dry-run-skips-the-whole-step-half", DryRun: true,
		Row: judged(false, "closure_check", 0.9)})
	add(defSpec{Name: "no-steps-skips-the-whole-step-half",
		Steps: []defStep{}, Row: judged(false, "closure_check", 0.9)})
	add(defSpec{Name: "step-lesson-import-failure-is-a-debug-line",
		Row:       judged(false, "closure_check", 0.9),
		DropNames: []string{"extract_step_lessons"}})
	add(defSpec{Name: "step-lesson-extractor-raising-is-a-debug-line",
		Row:              judged(false, "closure_check", 0.9),
		StepLessonsRaise: "step extractor died"})
	add(defSpec{Name: "load-raising-costs-both-halves",
		LoadRaise: "ledger unreadable"})
	add(defSpec{Name: "ledger-import-failure-costs-both-halves",
		DropNames: []string{"load_outcome_by_loop_id"}})
	// The learnability gate is the REAL outcome_policy on both sides, so
	// these rows are the gate's own branches driven through this seam
	// rather than a re-test of it: what is under test here is that the
	// port hands it the same row.
	add(defSpec{Name: "audit-incomplete-is-unlearnable",
		Row: [][]any{{"status", "done"}, {"audit_incomplete", true}}})
	add(defSpec{Name: "success-class-declared-and-unknown",
		Row: [][]any{{"status", "done"}, {"success_class", "mystery"}}})
	add(defSpec{Name: "success-class-declared-and-learnable",
		Row: [][]any{{"status", "done"}, {"success_class", "verified"}}})
	add(defSpec{Name: "out-of-budget-without-a-verdict-is-unlearnable",
		Row: [][]any{{"status", "done"},
			{"stop_verdict", "out_of_budget"}}})

	// --- half four: the two verdict gates, both failing open ------------
	add(defSpec{Name: "judged-not-achieved-blocks-crystallization",
		Row: judged(false, "closure_check", 0.9)})
	add(defSpec{Name: "judged-achieved-and-fully-trusted-crystallizes",
		Row: judged(true, "closure_check", 0.9), Extracted: []string{"s1"}})
	add(defSpec{Name: "judged-achieved-but-directional-is-blocked",
		Row: judged(true, "closure_check", 0.1)})
	add(defSpec{Name: "judged-achieved-but-excluded-is-blocked",
		Row: judged(true, "closure_unverifiable", 0.9)})
	add(defSpec{Name: "unjudged-row-keeps-pre-fix-behaviour",
		Row: [][]any{{"loop_id", "L1"}, {"status", "done"}}})
	// `is False` and `is True` are identity against the singletons: a
	// row rehydrated from JSON can carry a 0 or a 1 where a bool was
	// meant, and neither gate fires on those.
	add(defSpec{Name: "numeric-zero-is-not-the-False-singleton",
		Row: judged(float64(0), "closure_check", 0.9)})
	add(defSpec{Name: "numeric-one-is-not-the-True-singleton",
		Row: judged(float64(1), "closure_check", 0.1)})
	add(defSpec{Name: "goal-achieved-none-is-neutral-trust",
		Row: judged(nil, "closure_check", 0.9)})
	// A row that never declares the field: attribute access raises, the
	// blanket handler swallows it, and the run crystallizes with NO log
	// line at all. Reproduced, not repaired.
	add(defSpec{Name: "row-without-goal-achieved-fails-open",
		Row: [][]any{{"loop_id", "L1"}, {"status", "done"},
			{"goal_verdict_source", "closure_check"}}})
	add(defSpec{Name: "not-achieved-without-a-source-fails-open-silently",
		Row: [][]any{{"status", "done"}, {"goal_achieved", false}}})
	add(defSpec{Name: "achieved-untrusted-without-a-source-fails-open",
		Row: [][]any{{"status", "done"}, {"goal_achieved", true},
			{"goal_verdict_confidence", 0.1}}})

	// --- the skill gate ---------------------------------------------------
	add(defSpec{Name: "dry-run-never-crystallizes", DryRun: true})
	add(defSpec{Name: "a-status-that-is-not-done-never-crystallizes",
		Status: "blocked", Row: judged(true, "closure_check", 0.9)})
	add(defSpec{Name: "no-steps-never-crystallizes", Steps: []defStep{}})
	add(defSpec{Name: "the-crystallize-status-is-hardcoded-done",
		Status: "done", Extracted: []string{"s1", "s2"}})

	return out
}

func defTag(a any) string {
	if a == nil {
		return "none"
	}
	if !pyval.Truthy(a) {
		return "falsy"
	}
	return "truthy"
}

func objGet(o pyval.Obj, key string) any {
	v, _ := o.Get(key)
	return v
}

func goDeferredRecord(s defSpec) map[string]any {
	calls := []map[string]any{}
	logs := []map[string]any{}
	var stderr bytes.Buffer
	rec := func(kv map[string]any) { calls = append(calls, kv) }
	at := func(level string) func(string) {
		return func(m string) {
			logs = append(logs, map[string]any{"level": level, "msg": m})
		}
	}
	dropped := map[string]bool{}
	for _, n := range s.DropNames {
		dropped[n] = true
	}

	var adapter any
	if s.Adapter == "truthy" {
		adapter = "an adapter"
	}

	steps := make([]looptypes.StepOutcome, 0, len(s.Steps))
	for _, st := range s.Steps {
		steps = append(steps, looptypes.StepOutcome{Index: st.Index,
			Text: st.Text, Status: st.Status, Result: st.Result})
	}

	row := pyval.Obj{}
	for _, kv := range s.Row {
		row = append(row, pyval.Field{Key: kv[0].(string), Val: kv[1]})
	}

	d := DeferredDeps{
		Info: at("info"), Warn: at("warning"), Debug: at("debug"),
		MintRunRisks: func(project, loopID string) int {
			rec(map[string]any{"call": "_mint_run_risks_to_project",
				"project": project, "loop_id": loopID})
			return 0
		},
	}
	if !dropped["extract_deferred_lessons"] {
		d.ExtractDeferredLessons = func(loopID string, a any,
			dryRun bool) error {
			rec(map[string]any{"call": "extract_deferred_lessons",
				"loop_id": loopID, "adapter": defTag(a),
				"dry_run": dryRun})
			if msg := s.DeferredRaise[loopID]; msg != "" {
				return errors.New(msg)
			}
			return nil
		}
	}
	if !dropped["extract_step_lessons"] {
		d.ExtractStepLessons = func(goal string,
			st []looptypes.StepOutcome, taskType string, a any,
			loopID string, dryRun bool) error {
			rec(map[string]any{"call": "extract_step_lessons",
				"goal": goal, "nsteps": len(st), "task_type": taskType,
				"adapter": defTag(a), "loop_id": loopID,
				"dry_run": dryRun})
			if s.StepLessonsRaise != "" {
				return errors.New(s.StepLessonsRaise)
			}
			return nil
		}
	}
	if !dropped["load_outcome_by_loop_id"] {
		d.LoadOutcomeByLoopID = func(loopID string) (pyval.Obj, bool,
			error) {
			rec(map[string]any{"call": "load_outcome_by_loop_id",
				"loop_id": loopID})
			if s.LoadRaise != "" {
				return nil, false, errors.New(s.LoadRaise)
			}
			if s.Row == nil {
				return nil, false, nil
			}
			return row, true, nil
		}
	}
	d.Crystallize = CrystallizeDeps{
		Warn:   at("warning"),
		Stderr: func(l string) { stderr.WriteString(l + "\n") },
		LoadSkills: func() ([]Skill, error) {
			rec(map[string]any{"call": "load_skills"})
			return nil, nil
		},
		ExtractSkills: func(outcomes []pyval.Obj, a any) ([]Skill, error) {
			rec(map[string]any{"call": "extract_skills",
				"n": len(outcomes), "adapter": defTag(a),
				"goal":    objGet(outcomes[0], "goal"),
				"status":  objGet(outcomes[0], "status"),
				"project": objGet(outcomes[0], "project")})
			var sk []Skill
			for _, n := range s.Extracted {
				sk = append(sk, Skill{Name: n})
			}
			return sk, nil
		},
		SaveSkill: func(sk Skill) error {
			rec(map[string]any{"call": "save_skill", "skill": sk.Name})
			return nil
		},
		SynthesizeSkill: func(goal, summary, loopID string, a any,
			verbose bool) error {
			rec(map[string]any{"call": "synthesize_skill", "goal": goal,
				"summary": summary, "loop_id": loopID,
				"adapter": defTag(a), "verbose": verbose})
			return nil
		},
	}

	FinalizeDeferredLearning(DeferredArgs{
		Result: LoopResult{LoopID: s.LoopID, Project: s.ResultProject,
			Goal: s.Goal, Status: s.Status, Steps: steps,
			HadNoMatchingSkill: s.HadNoMatching},
		Adapter: adapter, Project: s.Project, DryRun: s.DryRun,
		Verbose: s.Verbose, ExtraLoopIDs: s.ExtraLoopIDs,
		SkipLoopIDs:      s.SkipLoopIDs,
		UnstampedLoopIDs: s.UnstampedLoopIDs,
	}, d)

	return map[string]any{"name": s.Name, "calls": calls, "logs": logs,
		"stderr": stderr.String()}
}

func runDeferredProbe(t *testing.T, dir string,
	scs []defSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "deferred-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "deferred_probe.py.tpl", srcDirLF(t),
		specPath)
	out, err := cmd.Output()
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

func TestFinalizeDeferredLearningMatchesCPython(t *testing.T) {
	scs := defScenarios()
	pyRecs := runDeferredProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got := canonMint(goDeferredRecord(s))
			want := canonMint(pyRecs[i])
			if want["name"] != s.Name {
				t.Fatalf("record %d is %v, want %s", i, want["name"], s.Name)
			}
			a, _ := json.MarshalIndent(got, "", "  ")
			b, _ := json.MarshalIndent(want, "", "  ")
			if string(a) != string(b) {
				t.Errorf("go:\n%s\npy:\n%s", a, b)
			}
		})
	}
}

func TestDeferredScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range defScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}
