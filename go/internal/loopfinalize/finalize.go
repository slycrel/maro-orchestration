package loopfinalize

// _finalize_loop: every post-loop side effect, in the order Python runs
// them. Diagnosis and lenses, the two recovery-lesson writers, Reflexion,
// the portability cache, per-step lessons, crystallisation, the
// maintenance tail, and the restart-only Telegram ping.
//
// It returns nothing and it never raises: post-loop side effects must not
// break result delivery. Every phase is wrapped, and the WRAPPING IS THE
// SPEC — which failures are a warning, which are a debug line, and which
// take a whole phase down with them. Eight try blocks, five different log
// levels, and no two of them fail the same way.
//
// One line of this function is a monument. `_diagnose(loop_id,
// project=project or "")` reads the local param; it used to read
// `ctx.project`, which was a NameError that the outer except swallowed on
// every single run for six weeks (2026-04-26 → session 40). The whole
// introspection block was dead and nothing said so. That is what a bare
// `except Exception` around a phase buys, and it is why this port drives
// each phase into failure and asserts what comes out.

import (
	"fmt"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

const (
	// SourceGoalClip is `goal[:120]` on both lesson writers.
	SourceGoalClip = 120
	// FailureChainHeadClip is `failure_chain[0][:100]` in the
	// verified-recovery text.
	FailureChainHeadClip = 100
	// TelegramGoalClip is `goal[:40]` in the restart ping's fallback
	// label.
	TelegramGoalClip = 40
	// RecoveryPlanConfidence is 0.5 — suggested, not yet verified by a
	// completed run. VerifiedRecoveryConfidence is 0.7, because the run
	// finishing IS the verification. DiagnosisLessonConfidence is 0.8.
	RecoveryPlanConfidence     = 0.5
	VerifiedRecoveryConfidence = 0.7
	DiagnosisLessonConfidence  = 0.8
)

// Diagnosis, LensResult, Aggregate and Recovery are the introspect shapes
// this function reads. Only the fields it actually touches are modelled:
// Summary is a rendered string rather than a method because nothing here
// does anything with a diagnosis but log its summary.
type Diagnosis struct {
	FailureClass   string
	Recommendation string
	Summary        string
}

type LensResult struct {
	LensName string
	Action   string
}

type Aggregate struct {
	Confidence    float64
	LensAgreement int
	PrimaryAction string
}

type Recovery struct {
	AutoApply bool
	Risk      string
	Action    string
}

// OutcomeRec is reflect_and_record's return. Python's is None on some
// paths and the code tests for it, so this is a POINTER: a zero-valued
// struct and "no row was written" are different states and only one of
// them permits the step trace.
type OutcomeRec struct {
	OutcomeID string
}

// TieredLesson is record_tiered_lesson's keyword set.
type TieredLesson struct {
	LessonText      string
	TaskType        string
	Outcome         string
	SourceGoal      string
	Confidence      float64
	LessonType      string
	EvidenceSources []string
	Grounding       []any
}

// StoredLesson is memory._store_lesson's keyword set — a different
// function with a different shape, called for the diagnosis lesson.
type StoredLesson struct {
	TaskType   string
	Outcome    string
	Lesson     string
	SourceGoal string
	Confidence float64
}

// ReflectIn is reflect_and_record's keyword set, in Python's order.
type ReflectIn struct {
	Goal             string
	Status           string
	ResultSummary    string
	LessonEvidence   string
	TaskType         string
	Project          string
	TokensIn         int
	TokensOut        int
	ElapsedMS        int
	Model            string
	Adapter          any
	DryRun           bool
	FailureChain     []string
	RecoverySteps    int
	LoopID           string
	DeferLessons     bool
	MeasurementClass string
	HandleID         string
	StopVerdict      string
	StopEvidence     string
	PauseReason      string
}

// FinalizeArgs is _finalize_loop's own parameter list.
type FinalizeArgs struct {
	LoopID           string
	Goal             string
	Project          string
	LoopStatus       string
	StepOutcomes     []looptypes.StepOutcome
	Adapter          any
	DryRun           bool
	Verbose          bool
	TotalTokensIn    int
	TotalTokensOut   int
	ElapsedMS        int
	HadNoMatching    bool
	FailureChain     []string
	RecoverySteps    int
	DeferLearning    bool
	DeferMaintenance bool
	MeasurementClass string
	HandleID         string
	StopVerdict      string
	StopEvidence     string
	PauseReason      string
}

// FinalizeDeps is every module this function reaches. A nil func is the
// ImportError its inline import would raise, and lands in the same
// handler as any other failure of that phase.
type FinalizeDeps struct {
	// introspect
	DiagnoseLoop      func(loopID, project string) (Diagnosis, error)
	SaveDiagnosis     func(Diagnosis) error
	LoadLoopEvents    func(loopID string) (any, error)
	BuildStepProfiles func(events any) (any, error)
	RunLenses         func(d Diagnosis, profiles any) ([]LensResult, error)
	AggregateLenses   func(d Diagnosis, rs []LensResult) (Aggregate, error)
	// PlanRecovery returns nil for "no plan", which is a different answer
	// from an error and is the common one.
	PlanRecovery func(d Diagnosis, useAdvisor bool) (*Recovery, error)

	// memory
	RecordTieredLesson func(TieredLesson) error
	StoreLesson        func(StoredLesson) error
	ReflectAndRecord   func(ReflectIn) (*OutcomeRec, error)
	RecordStepTrace    func(outcomeID, goal string,
		steps []looptypes.StepOutcome, taskType string) error
	ExtractStepLessons func(goal string, steps []looptypes.StepOutcome,
		taskType string, adapter any, loopID string) error

	// GroundLessons is mint_grounding.ground_lessons_for_run. See
	// groundingFor: it is fail-open by construction.
	GroundLessons func(texts []string, loopID string) ([][]any, error)

	// StepEvidence and StepEvidenceBounded are loop.StepEvidence and
	// loop.StepEvidenceBounded, reached through Deps because that port
	// lives in a package with its own StepOutcome.
	StepEvidence        func(steps []looptypes.StepOutcome) string
	StepEvidenceBounded func(steps []looptypes.StepOutcome,
		totalBudget, entryCap int) string

	// portability
	WeightingEnabled func() (bool, error)
	RefreshCache     func() error

	// The maintenance tail, in the order it is tried: durable record,
	// then in-process registration, then inline.
	RecordMaintenance func(handleID, loopID string, adapter any,
		verbose bool) (bool, error)
	DeferMaintenance      func(handleID string, fn func() error) error
	RunPostRunMaintenance func(adapter any, verbose bool)
	// LoopIDScope is captains_log.loop_id_scope, re-entered by the
	// DEFERRED callable so maintenance emitters attribute to this loop
	// (BACKLOG #17). It returns the exit func.
	LoopIDScope func(loopID string) (func(), error)

	TelegramNotify func(msg string) error

	// ModelKey is `getattr(adapter, "model_key", "")`.
	ModelKey func(adapter any) string

	// Crystallize carries _crystallize_and_synthesize's own deps through
	// unchanged; the seam between the two functions is real and the
	// differential drives it.
	Crystallize CrystallizeDeps

	Info  func(string)
	Warn  func(string)
	Debug func(string)
}

func (d FinalizeDeps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

func (d FinalizeDeps) warnf(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d FinalizeDeps) debugf(format string, a ...any) {
	if d.Debug != nil {
		d.Debug(fmt.Sprintf(format, a...))
	}
}

// groundingFor is `_grounding_for`: mint-time grounding stamps for one
// lesson (R1-3, 2026-08-06).
//
// Fail-open BY CONSTRUCTION — any failure returns the empty list, and the
// absent-key discipline upstream keeps the row byte-identical. The empty
// list is not nil: `grounding=[]` and `grounding=None` are different
// values in the recorded call, and Python writes the former.
func groundingFor(lessonText, loopID string, d FinalizeDeps) []any {
	empty := []any{}
	if loopID == "" || d.GroundLessons == nil {
		return empty
	}
	out, err := d.GroundLessons([]string{lessonText}, loopID)
	if err != nil {
		return empty
	}
	// `ground_lessons_for_run(...)[0]` — an empty return is an IndexError,
	// which the same except turns into the empty list.
	if len(out) == 0 {
		return empty
	}
	// The subscript is the LAST thing Python does: whatever sits at [0]
	// goes into the lesson row unexamined. A None there is written as
	// None, not as []. Normalising it here would be the port improving
	// its source, and `grounding=None` vs `grounding=[]` is a difference
	// the recorded call can see.
	return out[0]
}

// evidenceSources is `[f"loop:{loop_id}"] if loop_id else []`.
func evidenceSources(loopID string) []string {
	if loopID == "" {
		return []string{}
	}
	return []string{"loop:" + loopID}
}

// FinalizeLoop is _finalize_loop.
func FinalizeLoop(a FinalizeArgs, d FinalizeDeps) {
	done, blocked := 0, 0
	for _, s := range a.StepOutcomes {
		switch s.Status {
		case "done":
			done++
		case "blocked":
			blocked++
		}
	}
	d.info("loop_end loop_id=%s status=%s steps=%d/%d(done/blocked) "+
		"tokens=%d elapsed=%dms", a.LoopID, a.LoopStatus, done, blocked,
		a.TotalTokensIn+a.TotalTokensOut, a.ElapsedMS)

	introspectPhase(a, d)
	verifiedRecoveryPhase(a, d)
	reflexionPhase(a, d)
	portabilityPhase(a, d)
	stepLessonPhase(a, d)

	// Deferred with the lessons (data-r2-01) when the caller runs closure:
	// a done-but-not-achieved run must not crystallise its pattern into
	// the skill library.
	if a.LoopStatus == "done" && !a.DryRun && len(a.StepOutcomes) > 0 &&
		!a.DeferLearning {
		CrystallizeAndSynthesize(CrystallizeIn{
			LoopID: a.LoopID, Goal: a.Goal, Project: a.Project,
			LoopStatus: a.LoopStatus, StepOutcomes: a.StepOutcomes,
			Adapter: a.Adapter, Verbose: a.Verbose,
			HadNoMatching: a.HadNoMatching}, d.Crystallize)
	}

	maintenancePhase(a, d)
	restartPingPhase(a, d)
}

// introspectPhase is Phase 44-45: auto-diagnose, lenses, recovery plan.
// Its outer handler is a DEBUG line — the quietest level in the function —
// which is exactly how a NameError in it stayed invisible for six weeks.
func introspectPhase(a FinalizeArgs, d FinalizeDeps) {
	if err := introspect(a, d); err != nil {
		d.debugf("introspect failed: %s", err)
	}
}

func introspect(a FinalizeArgs, d FinalizeDeps) error {
	// Python imports all SEVEN introspect names on one line at the top of
	// the try, so a module missing any one of them takes the whole phase
	// down before the diagnosis runs — not at the line that would have
	// used it. Guarding them lazily would turn one debug line into a
	// different, later one.
	if d.DiagnoseLoop == nil || d.SaveDiagnosis == nil ||
		d.RunLenses == nil || d.AggregateLenses == nil ||
		d.PlanRecovery == nil || d.BuildStepProfiles == nil ||
		d.LoadLoopEvents == nil {
		return errImport
	}
	// `project or ""` — the local param, not ctx.project.
	diag, err := d.DiagnoseLoop(a.LoopID, a.Project)
	if err != nil {
		return err
	}
	if err := d.SaveDiagnosis(diag); err != nil {
		return err
	}
	if diag.FailureClass != "healthy" {
		d.warnf("introspect: %s", diag.Summary)
		events, err := d.LoadLoopEvents(a.LoopID)
		if err != nil {
			return err
		}
		profiles, err := d.BuildStepProfiles(events)
		if err != nil {
			return err
		}
		lensResults, err := d.RunLenses(diag, profiles)
		if err != nil {
			return err
		}
		for _, lr := range lensResults {
			if lr.Action != "" {
				d.warnf("lens[%s]: %s", lr.LensName, lr.Action)
			}
		}
		if len(lensResults) > 0 {
			agg, err := d.AggregateLenses(diag, lensResults)
			if err != nil {
				return err
			}
			d.info("synthesis: confidence=%.0f%% agreement=%d action=%s",
				agg.Confidence*100, agg.LensAgreement, agg.PrimaryAction)
		}
		recovery, err := d.PlanRecovery(diag, true)
		if err != nil {
			return err
		}
		if recovery != nil {
			tag := "NEEDS-REVIEW"
			if recovery.AutoApply {
				tag = "AUTO-RECOVERABLE"
			}
			d.warnf("recovery[%s] risk=%s: %s", tag, recovery.Risk,
				recovery.Action)
			// M3 (session 40): the plan itself is a recovery insight,
			// recorded typed so the next similar run gets it injected at
			// decompose time instead of re-deriving it from a fresh
			// failure. Its own try, its own DEBUG line — a lesson that
			// fails to record does not stop the diagnosis lesson below.
			if !a.DryRun {
				if err := recoveryPlanLesson(a, d, diag,
					*recovery); err != nil {
					d.debugf("recovery-plan lesson record failed: %s", err)
				}
			}
		}
	}
	// Inject diagnosis-derived lessons directly into memory so the planner
	// sees them via inject_lessons_for_task on the next run.
	//
	// This re-tests failure_class rather than living in the block above,
	// and the difference is observable: a lens or recovery failure aborts
	// the outer try and this never runs, but a recovery-lesson failure —
	// swallowed at DEBUG — leaves it reachable.
	if diag.FailureClass != "healthy" {
		if err := storeLesson(d, StoredLesson{
			TaskType:   "agenda",
			Outcome:    diag.FailureClass,
			Lesson:     AutoDiagnosisLessonText(diag.FailureClass, diag.Recommendation),
			SourceGoal: pytext.Head(a.Goal, SourceGoalClip),
			Confidence: DiagnosisLessonConfidence,
		}); err != nil {
			d.warnf("failed to persist diagnosis lesson (learning data "+
				"lost): %s", err)
		} else {
			d.info("injected diagnosis lesson: %s", diag.FailureClass)
		}
	}
	return nil
}

// recoveryPlanLesson is the M3 recovery-plan write. The import guard is
// the FIRST thing it does and that is observable: Python imports
// record_tiered_lesson at the top of this try, so a memory module that
// cannot be imported never reaches _grounding_for — and _grounding_for
// calls out to mint_grounding. A guard placed at the call site instead
// would emit a grounding query for a lesson that was never going to be
// written.
func recoveryPlanLesson(a FinalizeArgs, d FinalizeDeps, diag Diagnosis,
	recovery Recovery) error {
	if d.RecordTieredLesson == nil {
		return errImport
	}
	text := RecoveryPlanLessonText(diag.FailureClass, recovery.Action)
	return d.RecordTieredLesson(TieredLesson{
		LessonText:      text,
		TaskType:        "agenda",
		Outcome:         a.LoopStatus,
		SourceGoal:      pytext.Head(a.Goal, SourceGoalClip),
		Confidence:      RecoveryPlanConfidence,
		LessonType:      "recovery",
		EvidenceSources: evidenceSources(a.LoopID),
		Grounding:       groundingFor(text, a.LoopID, d),
	})
}

func storeLesson(d FinalizeDeps, l StoredLesson) error {
	if d.StoreLesson == nil {
		return errImport
	}
	return d.StoreLesson(l)
}

// verifiedRecoveryPhase is M3 (session 40): a completed run that needed
// recovery actions is a VERIFIED recovery. Higher confidence than an
// LLM-extracted lesson, because the run finishing is the verification.
func verifiedRecoveryPhase(a FinalizeArgs, d FinalizeDeps) {
	if a.DryRun || a.LoopStatus != "done" || a.RecoverySteps <= 0 ||
		len(a.FailureChain) == 0 {
		return
	}
	// Same import-first ordering as recoveryPlanLesson: the kinds join and
	// the grounding query are both inside the try, BELOW the import.
	if d.RecordTieredLesson == nil {
		d.debugf("verified-recovery lesson record failed: %s", errImport)
		return
	}
	// The marker table maps a phrase in the failure-chain entry to a kind
	// name. A SET then sorted, so the kinds are alphabetical and each
	// appears once however many entries mention it.
	markers := []struct{ phrase, kind string }{
		{"re-decomposing", "re-decompose"},
		{"split", "step-split"},
		{"retry", "retry-with-hint"},
	}
	seen := map[string]bool{}
	var kinds []string
	for _, e := range a.FailureChain {
		for _, m := range markers {
			if strings.Contains(e, m.phrase) && !seen[m.kind] {
				seen[m.kind] = true
				kinds = append(kinds, m.kind)
			}
		}
	}
	sort.Strings(kinds)
	label := strings.Join(kinds, ", ")
	if label == "" {
		label = "recovery"
	}
	text := fmt.Sprintf("[recovery-verified] %s unblocked a run: %s", label,
		pytext.Head(a.FailureChain[0], FailureChainHeadClip))
	// EQUIVALENT MUTANT: `Outcome: "done"` and `Outcome: a.LoopStatus`
	// cannot be told apart from here. The guard four lines up returns
	// unless the status IS "done", so the literal and the field are the
	// same string on every input that reaches this line. The battery row
	// is KEPT and marked `equivalent`, so this note is a tripwire on
	// itself: if a fixture ever kills that row, the guard moved and this
	// comment is stale. The literal is what Python writes.
	if err := d.RecordTieredLesson(TieredLesson{
		LessonText:      text,
		TaskType:        "agenda",
		Outcome:         "done",
		SourceGoal:      pytext.Head(a.Goal, SourceGoalClip),
		Confidence:      VerifiedRecoveryConfidence,
		LessonType:      "recovery",
		EvidenceSources: evidenceSources(a.LoopID),
		Grounding:       groundingFor(text, a.LoopID, d),
	}); err != nil {
		d.debugf("verified-recovery lesson record failed: %s", err)
		return
	}
	d.info("recorded verified-recovery lesson (%d recovery steps)",
		a.RecoverySteps)
}

// reflexionPhase is Phase 5: record the outcome and extract lessons.
//
// The two evidence strings are priced differently and that is why there
// are two. `evidence` is prompt-only, free and wide; `summary` is
// persisted and re-read forever, so it is STORE-grade. The deferred
// extractor has only the row, so the stored one carries real evidence
// too — it is not merely a display string (2026-08-06 truncation audit).
func reflexionPhase(a FinalizeArgs, d FinalizeDeps) {
	if err := reflexion(a, d); err != nil {
		d.warnf("reflect_and_record failed — run %s produced no learning "+
			"data: %s", a.LoopID, err)
	}
}

func reflexion(a FinalizeArgs, d FinalizeDeps) error {
	// `from memory import reflect_and_record, record_step_trace` is ONE
	// import at the top of this try, so a missing record_step_trace fails
	// the whole phase with the WARNING — not the debug line its own call
	// site would have produced.
	if d.ReflectAndRecord == nil || d.RecordStepTrace == nil ||
		d.StepEvidence == nil || d.StepEvidenceBounded == nil {
		return errImport
	}
	doneSteps := 0
	for _, s := range a.StepOutcomes {
		if s.Status == "done" {
			doneSteps++
		}
	}
	head := fmt.Sprintf("Completed %d/%d steps.", doneSteps,
		len(a.StepOutcomes))
	summary := head + " " + d.StepEvidenceBounded(a.StepOutcomes,
		budget.StoreTotalBudget, budget.StoreEntryCap)
	lessonEvidence := head + "\n\n" + d.StepEvidence(a.StepOutcomes)

	// `adapter=adapter if not dry_run else None` — a dry run must not hand
	// the recorder a live client.
	var adapter any
	if !a.DryRun {
		adapter = a.Adapter
	}
	modelKey := ""
	if d.ModelKey != nil {
		modelKey = d.ModelKey(a.Adapter)
	}
	chain := a.FailureChain
	if chain == nil {
		chain = []string{}
	}

	rec, err := d.ReflectAndRecord(ReflectIn{
		Goal:           a.Goal,
		Status:         a.LoopStatus,
		ResultSummary:  summary,
		LessonEvidence: lessonEvidence,
		TaskType:       "agenda",
		Project:        a.Project,
		TokensIn:       a.TotalTokensIn,
		TokensOut:      a.TotalTokensOut,
		ElapsedMS:      a.ElapsedMS,
		Model:          modelKey,
		Adapter:        adapter,
		DryRun:         a.DryRun,
		FailureChain:   chain,
		RecoverySteps:  a.RecoverySteps,
		// Verdict tri-state (SF-2): closure judging runs AFTER
		// finalization, so the verdict is unknown here — the row is
		// written unjudged with its loop_id and stamped later.
		LoopID: a.LoopID,
		// data-r2-01: ALL statuses defer when the caller will run
		// closure, not just "done" (2026-07-27 adversarial review,
		// three-lens consensus): a stuck run later judged
		// goal_achieved=True is achieved-not-done, and extracting before
		// the verdict recorded its lessons failure-framed into confirmed
		// injection surfaces.
		DeferLessons:     a.DeferLearning,
		MeasurementClass: a.MeasurementClass,
		HandleID:         a.HandleID,
		StopVerdict:      a.StopVerdict,
		StopEvidence:     a.StopEvidence,
		PauseReason:      a.PauseReason,
	})
	if err != nil {
		return err
	}
	// Meta-Harness steal: persist step-level traces so the evolver
	// proposer sees full execution context, not just aggregate summaries.
	if !a.DryRun && len(a.StepOutcomes) > 0 && rec != nil {
		if err := d.RecordStepTrace(rec.OutcomeID, a.Goal, a.StepOutcomes,
			"agenda"); err != nil {
			d.debugf("record_step_trace failed (non-critical): %s", err)
		}
	}
	return nil
}

// portabilityPhase is §14a slice 2: refresh the portability evidence
// cache. Post-reflect so this run's frames are on disk; verdicts stamped
// later get picked up by the next run's refresh. Never raises.
func portabilityPhase(a FinalizeArgs, d FinalizeDeps) {
	if a.DryRun {
		return
	}
	if err := portability(d); err != nil {
		d.debugf("portability cache refresh skipped: %s", err)
	}
}

func portability(d FinalizeDeps) error {
	if d.WeightingEnabled == nil || d.RefreshCache == nil {
		return errImport
	}
	on, err := d.WeightingEnabled()
	if err != nil {
		return err
	}
	if !on {
		return nil
	}
	return d.RefreshCache()
}

// stepLessonPhase is per-step learning (2026-07-27): a failure-shaped
// ending fails the run-level learnability gate, but steps that
// individually verified still carry method evidence.
//
// "done" runs skip: learnable ones learned via the run-level path, and
// closure-lane ones are judged later.
func stepLessonPhase(a FinalizeArgs, d FinalizeDeps) {
	if a.DryRun || len(a.StepOutcomes) == 0 || a.LoopStatus == "done" {
		return
	}
	if d.ExtractStepLessons == nil {
		d.debugf("step-lesson extraction failed (non-critical): %s", errImport)
		return
	}
	if err := d.ExtractStepLessons(a.Goal, a.StepOutcomes, "agenda",
		a.Adapter, a.LoopID); err != nil {
		d.debugf("step-lesson extraction failed (non-critical): %s", err)
	}
}

// maintenancePhase is the async-tail decree (2026-08-11).
//
// Skill maintenance, health probes, statistical scans and the run-cadence
// evolver + inspector used to run inline HERE — before closure, the gate
// and the user-facing notify: ~6 minutes of the 8m34s post-work answer
// delay measured on 2a3b1f85-sunny-wren, about 70% of the tail.
//
// Three tiers, tried in order, and the ordering is the point. A
// registration failure falls back INLINE: maintenance may move in time,
// never silently drop.
//
// `defer_maintenance` is handle.py only, and is NOT implied by
// `defer_learning` — the direct-CLI lanes set that while draining nothing
// (Codex review of 6f58bf3, consensus HIGH). Both flags are separate
// fields here for the same reason.
func maintenancePhase(a FinalizeArgs, d FinalizeDeps) {
	if a.DryRun {
		return
	}
	deferred := false
	if a.DeferMaintenance && a.HandleID != "" {
		// Durable first (async-tail phase 3, 2026-08-20): recorded to the
		// run's tail_jobs store so a spawned `maro finalize-tail` child
		// can run it after this process has exited.
		ok, err := recordMaintenance(a, d)
		if err != nil {
			d.debugf("maintenance record failed — trying in-process "+
				"deferral: %s", err)
		} else {
			deferred = ok
		}
	}
	if a.DeferMaintenance && a.HandleID != "" && !deferred {
		if err := deferInProcess(a, d); err != nil {
			d.debugf("maintenance defer failed — running inline: %s", err)
		} else {
			deferred = true
		}
	}
	if !deferred && d.RunPostRunMaintenance != nil {
		d.RunPostRunMaintenance(a.Adapter, a.Verbose)
	}
}

func recordMaintenance(a FinalizeArgs, d FinalizeDeps) (bool, error) {
	if d.RecordMaintenance == nil {
		return false, errImport
	}
	return d.RecordMaintenance(a.HandleID, a.LoopID, a.Adapter, a.Verbose)
}

func deferInProcess(a FinalizeArgs, d FinalizeDeps) error {
	if d.DeferMaintenance == nil {
		return errImport
	}
	// The closure captures adapter, verbose and loop_id BY VALUE in
	// Python (default-argument binding), which is what makes it safe to
	// run after this frame is gone. Go's capture is by reference and the
	// values are already copies in `a`, so the same guarantee holds — but
	// the loop-id scope is not incidental: the drain runs after
	// run_agent_loop's own scope has exited, and maintenance emitters
	// attribute through the ambient id (BACKLOG #17).
	adapter, verbose, loopID := a.Adapter, a.Verbose, a.LoopID
	return d.DeferMaintenance(a.HandleID, func() error {
		// `from captains_log import loop_id_scope` is INSIDE the closure,
		// so it fails at DRAIN time — and it fails the drain, which means
		// the maintenance does not run at all. Treating a missing scope
		// as "carry on unscoped" would silently run maintenance that
		// Python skips, and the only report would be a warning nobody
		// emitted.
		if d.LoopIDScope == nil {
			return errImport
		}
		exit, err := d.LoopIDScope(loopID)
		if err != nil {
			return err
		}
		defer exit()
		if d.RunPostRunMaintenance != nil {
			d.RunPostRunMaintenance(adapter, verbose)
		}
		return nil
	})
}

// restartPingPhase is the loop-boundary Telegram ping — progress only, and
// only for a restart.
//
// Completion is announced ONCE, at run level, by notify.emit
// ("run_completed") with the curated card. This block used to send
// "Mission complete" per LOOP with per-loop totals, so a mid-run restart
// read as the run dying with understated numbers (2026-07-17,
// dapper-heron).
func restartPingPhase(a FinalizeArgs, d FinalizeDeps) {
	if a.DryRun || a.LoopStatus != "restart" {
		return
	}
	if d.TelegramNotify == nil {
		d.debugf("loop-boundary Telegram ping failed (non-critical): %s",
			errImport)
		return
	}
	done := 0
	for _, s := range a.StepOutcomes {
		if s.Status == "done" {
			done++
		}
	}
	label := a.Project
	if label == "" {
		label = pytext.Head(a.Goal, TelegramGoalClip)
	}
	msg := fmt.Sprintf("🔄 Replanning — `%s`\nLoop ended after %d/%d steps; "+
		"the run continues on a fresh plan.", label, done,
		len(a.StepOutcomes))
	if err := d.TelegramNotify(msg); err != nil {
		d.debugf("loop-boundary Telegram ping failed (non-critical): %s", err)
	}
}
