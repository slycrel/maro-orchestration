package loopfinalize

// _crystallize_and_synthesize: skill crystallisation plus the Phase-32
// no-matching-skill synthesis, for a run that finished.
//
// Callers gate it — it runs at finalize on the immediate path, or from
// finalize_deferred_learning once closure has judged (data-r2-01). This
// function does not check status or dry_run; it does what it is told.
//
// Its whole contract is WHAT IT CALLS and WITH WHAT. The two halves are
// separately wrapped and separately swallowed: skill writes must never
// break finalization or handle delivery, so both failures are a log line
// and nothing else. That means the only way to test it is to record the
// calls, which is what the differential does.

import (
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

const (
	// ExtractResultClip is `s.result[:200]`, applied twice: once to the
	// done-step summaries that are joined into `summary`, and once to
	// EVERY step's result in the `steps` list. A plain code-point slice,
	// not a budget — nothing is appended.
	ExtractResultClip = 200
	// ExtractSummarySteps is `done_summaries[:4]`.
	ExtractSummarySteps = 4
	// SynthResultClip is `s.result[:120]` on the synthesis path, and
	// SynthSteps is its `done_steps[:3]`. Different numbers from the
	// extraction path's, on purpose: the synthesiser gets a shorter,
	// narrower sample.
	SynthResultClip = 120
	SynthSteps      = 3
	// SynthFallbackSummary is `_synth_summary or "completed successfully"`
	// — reached when no step is both done and non-empty, which is a real
	// state for a run whose steps all produced empty results.
	SynthFallbackSummary = "completed successfully"
)

// CrystallizeIn is the keyword set _crystallize_and_synthesize takes.
type CrystallizeIn struct {
	LoopID       string
	Goal         string
	Project      string
	LoopStatus   string
	StepOutcomes []looptypes.StepOutcome
	// Adapter is passed through to two different callees in two DIFFERENT
	// ways — see adapterOrNone — so it is held as an opaque value rather
	// than a typed client.
	Adapter       any
	Verbose       bool
	HadNoMatching bool
}

// Skill is what load_skills returns and save_skill takes. Only `.name` is
// read here, and Payload carries whatever else the skill is: a port that
// modelled the whole Skill dataclass would be claiming this function
// depends on fields it never touches.
type Skill struct {
	Name    string
	Payload any
}

// CrystallizeDeps are the four functions Python imports INSIDE the try
// blocks. A nil func stands in for the ImportError that would raise from
// that import, which is swallowed the same way any other failure is.
type CrystallizeDeps struct {
	// LoadSkills is called BEFORE ExtractSkills, and the order is
	// observable: a fixture that fails the load never reaches the
	// extraction.
	LoadSkills    func() ([]Skill, error)
	ExtractSkills func(outcomes []pyval.Obj, adapter any) ([]Skill, error)
	SaveSkill     func(Skill) error
	// SynthesizeSkill is evolver.synthesize_skill. It lives in the SECOND
	// try, so an extraction that blew up does not skip it.
	SynthesizeSkill func(goal, outcomeSummary, sourceLoopID string,
		adapter any, verbose bool) error

	// Stderr is `print(..., file=sys.stderr, flush=True)`.
	Stderr func(line string)
	Warn   func(msg string)
}

// CrystallizeAndSynthesize is the whole function. It returns nothing and
// raises nothing, exactly as Python's does.
func CrystallizeAndSynthesize(in CrystallizeIn, d CrystallizeDeps) {
	if err := crystallize(in, d); err != nil && d.Warn != nil {
		d.Warn("skill extraction failed — loop " + in.LoopID +
			" may not contribute to skill library: " + err.Error())
	}
	// Phase 32. The gate is on the CALLER's finding, not on whether
	// extraction produced anything: a run that matched no skill at start
	// synthesises one whether or not the extractor also found patterns.
	if in.HadNoMatching {
		if err := synthesize(in, d); err != nil && d.Warn != nil {
			d.Warn("skill synthesis failed — loop " + in.LoopID + ": " +
				err.Error())
		}
	}
}

// adapterOrNone is `adapter if adapter else None`.
//
// This appears on the EXTRACTION call and NOT on the synthesis call,
// which passes `adapter=adapter` raw. The asymmetry is real and it is
// observable with an adapter that is falsy but not None — an object whose
// __bool__ or __len__ says no. Both fixtures exist. Reproducing it is
// not pedantry: an adapter that reports itself empty reaches the
// synthesiser and does not reach the extractor, and only one of the two
// gets to decide what to do about it.
func adapterOrNone(a any) any {
	if !pyval.Truthy(a) {
		return nil
	}
	return a
}

func crystallize(in CrystallizeIn, d CrystallizeDeps) error {
	if d.LoadSkills == nil || d.ExtractSkills == nil || d.SaveSkill == nil {
		return errImport
	}

	var doneSummaries []string
	for _, s := range in.StepOutcomes {
		if s.Status == "done" && s.Result != "" {
			doneSummaries = append(doneSummaries,
				pytext.Head(s.Result, ExtractResultClip))
		}
	}

	// `steps` is EVERY outcome, not the done ones: the extractor is being
	// shown what the run did, including what it failed to do.
	steps := make([]any, 0, len(in.StepOutcomes))
	for _, s := range in.StepOutcomes {
		steps = append(steps, pyval.Obj{
			{Key: "step", Val: s.Text},
			{Key: "status", Val: s.Status},
			{Key: "result", Val: pytext.Head(s.Result, ExtractResultClip)},
		})
	}

	outcome := pyval.Obj{
		{Key: "goal", Val: in.Goal},
		{Key: "status", Val: in.LoopStatus},
		// task_type is hard-coded "agenda" — this path is only reached
		// from the agenda lane, and the extractor keys off it.
		{Key: "task_type", Val: "agenda"},
		{Key: "summary", Val: strings.Join(
			doneSummaries[:pyval.SliceStop(len(doneSummaries),
				ExtractSummarySteps)], ". ")},
		{Key: "steps", Val: pyval.List(steps)},
		{Key: "project", Val: in.Project},
	}

	existing := map[string]bool{}
	skills, err := d.LoadSkills()
	if err != nil {
		return err
	}
	for _, s := range skills {
		existing[s.Name] = true
	}

	extracted, err := d.ExtractSkills([]pyval.Obj{outcome},
		adapterOrNone(in.Adapter))
	if err != nil {
		return err
	}
	for _, sk := range extracted {
		// `existing_skills` is computed once and NOT updated as skills are
		// saved, so two extracted skills with the same name are BOTH
		// saved. Reproduced rather than fixed: the dedupe is against the
		// library on disk, and a second save of the same name is a
		// question for save_skill, not for this loop.
		if existing[sk.Name] {
			continue
		}
		// A save that raises aborts the REST of the extracted skills — it
		// is inside the one try, not per-skill. Fixtured.
		if err := d.SaveSkill(sk); err != nil {
			return err
		}
		if in.Verbose && d.Stderr != nil {
			d.Stderr("[maro] skill crystallised: " + sk.Name)
		}
	}
	return nil
}

func synthesize(in CrystallizeIn, d CrystallizeDeps) error {
	if d.SynthesizeSkill == nil {
		return errImport
	}
	var doneSteps []looptypes.StepOutcome
	for _, s := range in.StepOutcomes {
		if s.Status == "done" && s.Result != "" {
			doneSteps = append(doneSteps, s)
		}
	}
	// [:3] FIRST, then [:120] on each — so three long results give three
	// clipped ones, not one clipped join.
	parts := make([]string, 0, SynthSteps)
	for _, s := range doneSteps[:pyval.SliceStop(len(doneSteps), SynthSteps)] {
		parts = append(parts, pytext.Head(s.Result, SynthResultClip))
	}
	summary := strings.Join(parts, ". ")
	if summary == "" {
		summary = SynthFallbackSummary
	}
	// Note the adapter is passed RAW here — see adapterOrNone.
	return d.SynthesizeSkill(in.Goal, summary, in.LoopID, in.Adapter,
		in.Verbose)
}
