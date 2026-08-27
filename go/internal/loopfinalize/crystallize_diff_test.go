package loopfinalize

import (
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// _crystallize_and_synthesize returns nothing and raises nothing, so a
// test that checked its return value would pass against a body that had
// been deleted. Everything it does is a CALL, so the differential records
// the calls in order with their arguments, plus the warnings and the
// stderr lines, and compares those.
//
// The argument that matters most is the outcome dict handed to
// extract_skills: it is built from four slices and two joins, and it is
// the only place the run's text reaches the skill library.

type crStep struct {
	Text   string `json:"text"`
	Status string `json:"status"`
	Result string `json:"result"`
}

type crSpec struct {
	Name          string   `json:"name"`
	LoopID        string   `json:"loop_id"`
	Goal          string   `json:"goal"`
	Project       string   `json:"project"`
	LoopStatus    string   `json:"loop_status"`
	Steps         []crStep `json:"steps"`
	Adapter       string   `json:"adapter"`
	Verbose       bool     `json:"verbose"`
	HadNoMatching bool     `json:"had_no_matching"`
	Existing      []string `json:"existing"`
	Extracted     []string `json:"extracted"`
	LoadRaise     string   `json:"load_raise"`
	ExtractRaise  string   `json:"extract_raise"`
	SaveRaiseOn   string   `json:"save_raise_on"`
	SynthRaise    string   `json:"synth_raise"`
	MissingSkills bool     `json:"missing_skills"`
	MissingEvol   bool     `json:"missing_evolver"`
}

// crPairs is the probe's to_pairs: an ordered [[key, value], ...] rendering
// so the outcome dict's INSERTION ORDER is compared and not just its
// contents. Python dicts preserve it and extract_skills is a caller's
// function that may iterate.
func crPairs(v any) any {
	switch t := v.(type) {
	case pyval.Obj:
		out := make([]any, 0, len(t))
		for _, f := range t {
			out = append(out, []any{f.Key, crPairs(f.Val)})
		}
		return out
	case pyval.List:
		out := make([]any, 0, len(t))
		for _, x := range t {
			out = append(out, crPairs(x))
		}
		return out
	case []any:
		out := make([]any, 0, len(t))
		for _, x := range t {
			out = append(out, crPairs(x))
		}
		return out
	}
	return v
}

func crTag(a any) string {
	if a == nil {
		return "none"
	}
	if pyval.Truthy(a) {
		return "truthy"
	}
	return "falsy"
}

func crScenarios() []crSpec {
	var out []crSpec
	add := func(s crSpec) {
		if s.LoopID == "" {
			s.LoopID = "loop-cccc3333"
		}
		if s.Goal == "" {
			s.Goal = "ship the thing"
		}
		if s.Project == "" {
			s.Project = "proj"
		}
		if s.LoopStatus == "" {
			s.LoopStatus = "done"
		}
		if s.Adapter == "" {
			s.Adapter = "truthy"
		}
		if s.Existing == nil {
			s.Existing = []string{}
		}
		if s.Extracted == nil {
			s.Extracted = []string{}
		}
		if s.Steps == nil {
			s.Steps = []crStep{}
		}
		out = append(out, s)
	}
	done := func(text, result string) crStep {
		return crStep{Text: text, Status: "done", Result: result}
	}
	blocked := func(text, result string) crStep {
		return crStep{Text: text, Status: "blocked", Result: result}
	}

	// --- the shape of the outcome dict ------------------------------
	add(crSpec{Name: "no-steps"})
	add(crSpec{Name: "one-done-step",
		Steps: []crStep{done("do it", "did it")}})
	add(crSpec{Name: "blocked-steps-are-in-steps-not-in-summary",
		Steps: []crStep{done("a", "ra"), blocked("b", "rb"),
			crStep{Text: "c", Status: "skipped", Result: "rc"}}})
	// The summary filter is `status == "done" AND result` — an empty
	// result drops out of the join but stays in the steps list.
	add(crSpec{Name: "done-step-with-empty-result",
		Steps: []crStep{done("a", ""), done("b", "rb")}})
	add(crSpec{Name: "every-result-empty",
		Steps: []crStep{done("a", ""), done("b", "")}})
	add(crSpec{Name: "summary-takes-first-four-of-six",
		Steps: []crStep{done("1", "r1"), done("2", "r2"), done("3", "r3"),
			done("4", "r4"), done("5", "r5"), done("6", "r6")}})
	add(crSpec{Name: "summary-takes-exactly-four",
		Steps: []crStep{done("1", "r1"), done("2", "r2"), done("3", "r3"),
			done("4", "r4")}})
	// The [:4] counts DONE summaries, not steps, so five steps with two
	// done contribute two.
	add(crSpec{Name: "four-counts-done-not-steps",
		Steps: []crStep{blocked("1", "r1"), done("2", "r2"), blocked("3", "r3"),
			done("4", "r4"), blocked("5", "r5")}})
	add(crSpec{Name: "result-clipped-at-200",
		Steps: []crStep{done("long", strings.Repeat("x", 260))}})
	add(crSpec{Name: "result-clip-boundary-200",
		Steps: []crStep{done("edge", strings.Repeat("y", 200))}})
	add(crSpec{Name: "result-clip-boundary-201",
		Steps: []crStep{done("edge", strings.Repeat("z", 201))}})
	// A code-point clip, not a byte one: 150 astral runes are 600 bytes
	// and a byte slice would cut one in half.
	add(crSpec{Name: "result-clip-non-ascii",
		Steps: []crStep{done("emoji", strings.Repeat("🙂", 150))}})
	// The join is ". " and nothing strips the pieces, so a result that
	// already ends in a period doubles it — and one with a newline keeps
	// the newline, unlike the risk mint's gaps.
	add(crSpec{Name: "join-does-not-normalise",
		Steps: []crStep{done("a", "one."), done("b", "two\nlines"),
			done("c", "  spaced  ")}})
	add(crSpec{Name: "goal-and-project-are-passed-through",
		Goal:    "a goal with: colons, \"quotes\" and \n a newline",
		Project: "proj-with-dashes",
		Steps:   []crStep{done("a", "ra")}})
	add(crSpec{Name: "status-is-not-done",
		LoopStatus: "blocked", Steps: []crStep{done("a", "ra")}})

	// --- the adapter asymmetry ---------------------------------------
	add(crSpec{Name: "adapter-none", Adapter: "none",
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "adapter-truthy", Adapter: "truthy",
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})
	// The one that matters: falsy-but-not-None reaches the SYNTHESISER
	// and does not reach the EXTRACTOR.
	add(crSpec{Name: "adapter-falsy-splits-the-two-calls", Adapter: "falsy",
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})

	// --- the save loop ----------------------------------------------
	add(crSpec{Name: "extracted-none",
		Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "extracted-two-saved",
		Extracted: []string{"alpha", "beta"},
		Steps:     []crStep{done("a", "ra")}})
	add(crSpec{Name: "existing-skill-is-skipped",
		Existing: []string{"alpha"}, Extracted: []string{"alpha", "beta"},
		Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "all-extracted-already-exist",
		Existing:  []string{"alpha", "beta"},
		Extracted: []string{"alpha", "beta"},
		Steps:     []crStep{done("a", "ra")}})
	// existing_skills is computed ONCE and not updated as skills save, so
	// a duplicate name in the extracted list is saved twice.
	add(crSpec{Name: "duplicate-extracted-name-saves-twice",
		Extracted: []string{"alpha", "alpha"},
		Steps:     []crStep{done("a", "ra")}})
	add(crSpec{Name: "verbose-prints-each-save", Verbose: true,
		Extracted: []string{"alpha", "beta"},
		Steps:     []crStep{done("a", "ra")}})
	add(crSpec{Name: "verbose-prints-nothing-for-a-skipped-skill",
		Verbose: true, Existing: []string{"alpha"},
		Extracted: []string{"alpha"}, Steps: []crStep{done("a", "ra")}})

	// --- the failures, and which half they take down ------------------
	add(crSpec{Name: "load-raises", LoadRaise: "load blew up",
		HadNoMatching: true, Extracted: []string{"alpha"},
		Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "extract-raises", ExtractRaise: "extract blew up",
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})
	// A save that raises aborts the REST of the extracted skills: the try
	// wraps the whole loop, not each iteration.
	add(crSpec{Name: "save-raises-aborts-the-rest",
		Extracted: []string{"alpha", "beta", "gamma"}, SaveRaiseOn: "beta",
		Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "save-raises-on-the-first",
		Extracted: []string{"alpha", "beta"}, SaveRaiseOn: "alpha",
		Verbose: true, Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "extraction-failure-does-not-skip-synthesis",
		ExtractRaise: "boom", HadNoMatching: true,
		Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "synth-raises", SynthRaise: "synth blew up",
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})
	// The import failure. The MESSAGE is fixture-chosen on both sides —
	// see the probe's DeadModule — because CPython's real text names the
	// module and its path, which is the probe's mechanism and not this
	// function's behaviour.
	add(crSpec{Name: "skills-module-missing", MissingSkills: true,
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "evolver-module-missing", MissingEvol: true,
		HadNoMatching: true, Steps: []crStep{done("a", "ra")}})

	// --- the synthesis half -------------------------------------------
	add(crSpec{Name: "no-synthesis-when-a-skill-matched",
		HadNoMatching: false, Steps: []crStep{done("a", "ra")}})
	add(crSpec{Name: "synth-summary-takes-three",
		HadNoMatching: true,
		Steps: []crStep{done("1", "r1"), done("2", "r2"), done("3", "r3"),
			done("4", "r4")}})
	add(crSpec{Name: "synth-clips-at-120-then-joins",
		HadNoMatching: true,
		Steps: []crStep{done("1", strings.Repeat("a", 130)),
			done("2", strings.Repeat("b", 130))}})
	add(crSpec{Name: "synth-clip-boundary-120",
		HadNoMatching: true,
		Steps:         []crStep{done("1", strings.Repeat("c", 120))}})
	add(crSpec{Name: "synth-clip-boundary-121",
		HadNoMatching: true,
		Steps:         []crStep{done("1", strings.Repeat("d", 121))}})
	// Falls back only when the join is EMPTY, which needs no done step
	// with a non-empty result.
	add(crSpec{Name: "synth-fallback-no-done-steps",
		HadNoMatching: true, Steps: []crStep{blocked("a", "ra")}})
	add(crSpec{Name: "synth-fallback-no-steps-at-all",
		HadNoMatching: true})
	add(crSpec{Name: "synth-fallback-all-results-empty",
		HadNoMatching: true, Steps: []crStep{done("a", ""), done("b", "")}})
	add(crSpec{Name: "synth-verbose-is-passed-through",
		HadNoMatching: true, Verbose: true,
		Steps: []crStep{done("a", "ra")}})
	// [:3] FIRST, then [:120] on each — three long results give three
	// clipped ones, not one clipped join.
	add(crSpec{Name: "synth-three-of-five-each-clipped",
		HadNoMatching: true,
		Steps: []crStep{done("1", strings.Repeat("a", 200)),
			done("2", strings.Repeat("b", 200)),
			done("3", strings.Repeat("c", 200)),
			done("4", strings.Repeat("d", 200)),
			done("5", strings.Repeat("e", 200))}})
	add(crSpec{Name: "synth-non-ascii-clip",
		HadNoMatching: true,
		Steps:         []crStep{done("1", strings.Repeat("é", 130))}})

	return out
}

func goCrystallizeRecord(s crSpec) map[string]any {
	calls := []map[string]any{}
	warns := []string{}
	var stderr strings.Builder

	rec := func(kv map[string]any) { calls = append(calls, kv) }

	d := CrystallizeDeps{
		Warn:   func(m string) { warns = append(warns, m) },
		Stderr: func(l string) { stderr.WriteString(l + "\n") },
	}
	if !s.MissingSkills {
		d.LoadSkills = func() ([]Skill, error) {
			rec(map[string]any{"call": "load_skills"})
			if s.LoadRaise != "" {
				return nil, errors.New(s.LoadRaise)
			}
			out := make([]Skill, 0, len(s.Existing))
			for _, n := range s.Existing {
				out = append(out, Skill{Name: n})
			}
			return out, nil
		}
		d.ExtractSkills = func(outcomes []pyval.Obj, adapter any) ([]Skill, error) {
			raw := make([]any, 0, len(outcomes))
			for _, o := range outcomes {
				raw = append(raw, o)
			}
			rec(map[string]any{"call": "extract_skills",
				"outcomes": crPairs(pyval.List(raw)),
				"adapter":  crTag(adapter)})
			if s.ExtractRaise != "" {
				return nil, errors.New(s.ExtractRaise)
			}
			out := make([]Skill, 0, len(s.Extracted))
			for _, n := range s.Extracted {
				out = append(out, Skill{Name: n})
			}
			return out, nil
		}
		d.SaveSkill = func(sk Skill) error {
			rec(map[string]any{"call": "save_skill", "name": sk.Name})
			if s.SaveRaiseOn != "" && sk.Name == s.SaveRaiseOn {
				return errors.New("save failed for " + sk.Name)
			}
			return nil
		}
	}
	if !s.MissingEvol {
		d.SynthesizeSkill = func(goal, summary, loopID string, adapter any,
			verbose bool) error {
			rec(map[string]any{"call": "synthesize_skill", "goal": goal,
				"summary": summary, "loop_id": loopID,
				"adapter": crTag(adapter), "verbose": verbose})
			if s.SynthRaise != "" {
				return errors.New(s.SynthRaise)
			}
			return nil
		}
	}

	var adapter any
	switch s.Adapter {
	case "truthy":
		adapter = "an adapter"
	case "falsy":
		// The probe's FalsyAdapter: not None, not truthy. An empty
		// pyval.List is the value pyval.Truthy answers "no" to, which is
		// the same answer CPython gives an object whose __bool__ says no.
		// A NAMED Go type with []any underneath would not do: pyval's
		// type switch matches exact types, so it would read as truthy and
		// the fixture would silently test the truthy path.
		adapter = pyval.List{}
	}

	outcomes := make([]looptypes.StepOutcome, 0, len(s.Steps))
	for i, st := range s.Steps {
		outcomes = append(outcomes, looptypes.StepOutcome{Index: i,
			Text: st.Text, Status: st.Status, Result: st.Result})
	}

	CrystallizeAndSynthesize(CrystallizeIn{LoopID: s.LoopID, Goal: s.Goal,
		Project: s.Project, LoopStatus: s.LoopStatus,
		StepOutcomes: outcomes, Adapter: adapter, Verbose: s.Verbose,
		HadNoMatching: s.HadNoMatching}, d)

	return map[string]any{"name": s.Name, "calls": calls,
		"warnings": warns, "stderr": stderr.String()}
}

func runCrystallizeProbe(t *testing.T, dir string, scs []crSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	specPath := filepath.Join(dir, "cr-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "crystallize_probe.py.tpl", srcDirLF(t),
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

func TestCrystallizeMatchesCPython(t *testing.T) {
	scs := crScenarios()
	pyRecs := runCrystallizeProbe(t, t.TempDir(), scs)
	if len(pyRecs) != len(scs) {
		t.Fatalf("probe returned %d records for %d scenarios",
			len(pyRecs), len(scs))
	}
	for i, s := range scs {
		t.Run(s.Name, func(t *testing.T) {
			got, want := canonMint(goCrystallizeRecord(s)), canonMint(pyRecs[i])
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

func TestCrystallizeScenarioNamesAreUnique(t *testing.T) {
	seen := map[string]bool{}
	for _, s := range crScenarios() {
		if seen[s.Name] {
			t.Errorf("duplicate scenario name %q", s.Name)
		}
		seen[s.Name] = true
	}
}

// Nothing this function does may escape it. Python's two try blocks
// swallow everything, and a caller that grew a panic here would break
// handle delivery for a skill write — the exact failure the swallowing
// exists to prevent.
func TestCrystallizeSwallowsEverything(t *testing.T) {
	panicky := CrystallizeDeps{
		LoadSkills: func() ([]Skill, error) { return nil, errors.New("x") },
		SynthesizeSkill: func(string, string, string, any, bool) error {
			return errors.New("y")
		},
		// ExtractSkills and SaveSkill left nil, and Warn/Stderr too: a nil
		// reporter must not be a nil dereference.
	}
	CrystallizeAndSynthesize(CrystallizeIn{LoopID: "l", HadNoMatching: true},
		panicky)
}
