package loopfinalize

// finalize_deferred_learning: the learning finalize deferred, run now that
// closure has judged (data-r2-01 — lessons and skills must not be
// extracted verdict-blind).
//
// Called AFTER stamp_outcome_verdict has written the verdict onto the
// outcomes row. Four halves, and the ORDER of the guards between them is
// the specification:
//
//  1. lessons, per loop id, extras first and this loop last;
//  2. the risk mint;
//  3. per-step lessons, but ONLY for a row the learnability gate refuses;
//  4. crystallisation, behind two verdict gates that both fail OPEN.
//
// It never raises: deferred learning must not break result delivery.

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/looptypes"
	"github.com/slycrel/maro-orchestration/go/internal/outcomepolicy"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// LoopResult is the subset of the run's result this function reads. Every
// read is `getattr(loop_result, name, default) or default`, and every
// default is the zero value, so plain fields are exact.
type LoopResult struct {
	LoopID             string
	Project            string
	Goal               string
	Status             string
	Steps              []looptypes.StepOutcome
	HadNoMatchingSkill bool
}

// DeferredArgs is finalize_deferred_learning's parameter list.
type DeferredArgs struct {
	Result  LoopResult
	Adapter any
	Project string
	DryRun  bool
	Verbose bool
	// ExtraLoopIDs are earlier attempts this handle ran (director/closure
	// restarts). They get LESSON extraction only: their steps are gone
	// and they were superseded, so no skill writes.
	ExtraLoopIDs []string
	// SkipLoopIDs are outcome rows whose verdict audit is unresolved.
	// They stay deferred while other independently-audited loops in the
	// same handle may learn. UnstampedLoopIDs is a compatibility alias
	// from the first EXT-AUDIT-2 implementation, unioned with it.
	SkipLoopIDs      []string
	UnstampedLoopIDs []string
}

// DeferredDeps is every module this function reaches.
type DeferredDeps struct {
	ExtractDeferredLessons func(loopID string, adapter any,
		dryRun bool) error
	// ExtractStepLessons here takes dry_run, which the finalize-path call
	// of the SAME function does not pass. Two call sites, two signatures;
	// the port keeps both rather than unifying them.
	ExtractStepLessons func(goal string, steps []looptypes.StepOutcome,
		taskType string, adapter any, loopID string, dryRun bool) error
	// LoadOutcomeByLoopID returns the row and whether there was one. A
	// missing row is `None`, and every gate below treats it as "not
	// judged" rather than as a refusal.
	LoadOutcomeByLoopID func(loopID string) (pyval.Obj, bool, error)

	// MintRunRisks is _mint_run_risks_to_project, called OUTSIDE any try
	// here because its own body swallows. Production wires it straight to
	// MintRunRisksToProject; it is a field rather than a direct call so
	// the differential can observe the call without seeding a workspace
	// on both sides — the mint has its own differential, and re-driving
	// it through this one would test the seeding, not the seam.
	//
	// nil is NOT a state Python can be in: _mint_run_risks_to_project is
	// a module-level function in this same file, not an import. A caller
	// that leaves this nil silently skips the mint, and no differential
	// fixture can say so, because there is no Python spelling of it. Wire
	// it (same shape as FinalizeDeps.DeferMaintenance).
	MintRunRisks func(project, loopID string) int

	Crystallize CrystallizeDeps

	Info  func(string)
	Warn  func(string)
	Debug func(string)
}

func (d DeferredDeps) info(format string, a ...any) {
	if d.Info != nil {
		d.Info(fmt.Sprintf(format, a...))
	}
}

func (d DeferredDeps) warnf(format string, a ...any) {
	if d.Warn != nil {
		d.Warn(fmt.Sprintf(format, a...))
	}
}

func (d DeferredDeps) debugf(format string, a ...any) {
	if d.Debug != nil {
		d.Debug(fmt.Sprintf(format, a...))
	}
}

// isTrue and isFalse are Python's `is True` / `is False`: identity against
// the singletons, so a numeric 1 is not True and a 0 is not False. Both
// gates below spell it that way and the difference is live — a ledger row
// rehydrated from JSON can hold a number where a bool was meant.
// EQUIVALENT MUTANT: isTrue's `ok &&` half is unobservable through this
// file. The first gate consumes every bool False on both of its arms, so
// isTrue is only ever reached with a value that is not the False
// singleton. It is spelled in full because it is the honest translation
// of `is True`, and because the next caller will not have that guard.
func isTrue(v any) bool {
	b, ok := v.(bool)
	return ok && b
}

func isFalse(v any) bool {
	b, ok := v.(bool)
	return ok && !b
}

// objToMap renders the row for record.VerdictTrust, which takes a mapping
// where outcomepolicy.IsLearnable takes the ordered Obj. That asymmetry is
// Python's: `is_learnable_outcome(dataclasses.asdict(row))` gets a dict and
// `verdict_trust(row)` gets the dataclass, and verdict_trust branches on
// isinstance to read either.
func objToMap(o pyval.Obj) map[string]any {
	m := make(map[string]any, len(o))
	for _, f := range o {
		m[f.Key] = f.Val
	}
	return m
}

// FinalizeDeferredLearning is finalize_deferred_learning.
func FinalizeDeferredLearning(a DeferredArgs, d DeferredDeps) {
	loopID := a.Result.LoopID
	skip := map[string]bool{}
	for _, id := range a.SkipLoopIDs {
		skip[id] = true
	}
	for _, id := range a.UnstampedLoopIDs {
		skip[id] = true
	}

	deferredLessons(a, d, loopID, skip)

	// The skip test sits BETWEEN the two halves: a loop whose audit is
	// unresolved still had its EXTRAS' lessons extracted above, and gets
	// nothing below.
	if skip[loopID] {
		return
	}

	project := a.Project
	if project == "" {
		project = a.Result.Project
	}
	// Outside every try: _mint_run_risks_to_project swallows its own.
	if d.MintRunRisks != nil {
		d.MintRunRisks(project, loopID)
	}

	steps := a.Result.Steps
	postVerdictStepLessons(a, d, loopID, steps)

	// Provenance demotion already downgraded status from "done", so a
	// provenance-failed run never reaches the skill branch via status
	// alone.
	if a.DryRun || a.Result.Status != "done" || len(steps) == 0 {
		return
	}
	if !crystallizationAllowed(d, loopID) {
		return
	}
	// EQUIVALENT MUTANT: `LoopStatus: "done"` and `a.Result.Status` cannot
	// be told apart from here — the gate above returns unless the status
	// IS "done". The literal is what Python writes, and the battery row
	// is kept and marked `equivalent` so a fixture that ever kills it
	// reports this note as stale.
	CrystallizeAndSynthesize(CrystallizeIn{
		LoopID:        loopID,
		Goal:          a.Result.Goal,
		Project:       project,
		LoopStatus:    "done",
		StepOutcomes:  steps,
		Adapter:       a.Adapter,
		Verbose:       a.Verbose,
		HadNoMatching: a.Result.HadNoMatchingSkill,
	}, d.Crystallize)
}

// deferredLessons is the lesson half: one call per loop id, extras first
// and THIS loop last.
//
// Two nested handlers with two different messages. The outer one is the
// import; the inner one is per loop, so one loop that blows up does not
// cost the others. Both are warnings.
func deferredLessons(a DeferredArgs, d DeferredDeps, loopID string,
	skip map[string]bool) {
	if d.ExtractDeferredLessons == nil {
		d.warnf("deferred lesson extraction unavailable: %s", errImport)
		return
	}
	ids := append(append([]string{}, a.ExtraLoopIDs...), loopID)
	for _, id := range ids {
		// `if _lid and _lid not in _skip` — the empty id is the common
		// case, not an edge one: a loop_result with no loop_id yields ""
		// and it must not reach the extractor.
		if id == "" || skip[id] {
			continue
		}
		if err := d.ExtractDeferredLessons(id, a.Adapter, a.DryRun); err != nil {
			d.warnf("deferred lesson extraction failed for loop %s: %s",
				id, err)
		}
	}
}

// postVerdictStepLessons is per-step learning after the verdict
// (2026-07-27): closure judged and the row FAILED the learnability gate,
// so the run-level lessons came out failure-flavoured — but individually
// verified steps still carry method evidence.
//
// Final loop only (earlier attempts' steps are gone), and idempotent via
// the row stamp, so the not-deferred loops this function also receives do
// not re-pay the call.
func postVerdictStepLessons(a DeferredArgs, d DeferredDeps, loopID string,
	steps []looptypes.StepOutcome) {
	if len(steps) == 0 || a.DryRun {
		return
	}
	if err := stepLessonsAfterVerdict(a, d, loopID, steps); err != nil {
		d.debugf("post-verdict step-lesson extraction failed "+
			"(non-critical): %s", err)
	}
}

func stepLessonsAfterVerdict(a DeferredArgs, d DeferredDeps, loopID string,
	steps []looptypes.StepOutcome) error {
	// Three imports at the top of the try — dataclasses, memory and
	// memory_ledger — plus outcome_policy. Any of them missing fails the
	// whole block at its debug line, before the row is read.
	if d.ExtractStepLessons == nil || d.LoadOutcomeByLoopID == nil {
		return errImport
	}
	row, found, err := d.LoadOutcomeByLoopID(loopID)
	if err != nil {
		return err
	}
	// `if _row is not None and not is_learnable_outcome(...)`. A missing
	// row extracts NOTHING: the gate is "judged and refused", and an
	// absent row is neither.
	if !found {
		return nil
	}
	learnable, err := outcomepolicy.IsLearnable(row)
	if err != nil {
		return err
	}
	if learnable {
		return nil
	}
	// EQUIVALENT MUTANT: `a.DryRun` here is provably false — the caller
	// returns on it — so passing a literal false reads the same on every
	// input. Python passes `dry_run=dry_run` at this call anyway, and the
	// port passes the field for the same reason: it is what the source
	// says, not what the source needs.
	return d.ExtractStepLessons(a.Result.Goal, steps, "agenda", a.Adapter,
		loopID, a.DryRun)
}

// crystallizationAllowed is the two verdict gates, and BOTH fail open:
// Python's whole block is wrapped in `except Exception: pass`, so an
// unreadable ledger returns to pre-fix behaviour — done is enough.
//
// An unjudged row keeps that behaviour too. Absence means "not judged",
// not "failed".
func crystallizationAllowed(d DeferredDeps, loopID string) bool {
	if d.LoadOutcomeByLoopID == nil {
		return true
	}
	row, found, err := d.LoadOutcomeByLoopID(loopID)
	if err != nil || !found {
		return true
	}
	// `_row.goal_achieved` is ATTRIBUTE access, not a dict lookup: a row
	// without the field raises AttributeError, which the outer
	// `except Exception: pass` swallows — so a malformed row fails open
	// through this gate rather than failing it.
	achieved, ok := row.Get("goal_achieved")
	if !ok {
		return true
	}
	if isFalse(achieved) {
		// The source is read to BUILD the log line, so a row that is
		// judged not-achieved but carries no source raises before the
		// line is emitted, and the swallow makes that a fail-open with
		// NO log at all. Reproduced, not repaired.
		source, ok := row.Get("goal_verdict_source")
		if !ok {
			return true
		}
		d.info("deferred skill crystallization skipped — loop %s judged "+
			"not-achieved (%s)", loopID, pyval.Str(source))
		return false
	}
	// Judged-True but not fully trusted — confidence below the floor (a
	// pass-audit refutation capping it, say) or an excluded source.
	// Crystallising "the strongest examples" from a verdict that learning
	// does not full-trust would launder the doubt away (adversarial review
	// 2026-08-10: the MH #1 cap must reach THIS gate, not only the
	// verdict_trust consumers).
	// Python calls verdict_trust TWICE here — once in the condition and
	// once to build the log line. It is pure, so one call is the same
	// answer; noting the difference rather than reproducing it.
	trust := record.VerdictTrust(objToMap(row))
	if isTrue(achieved) && trust != record.VerdictTrustFull {
		source, ok := row.Get("goal_verdict_source")
		if !ok {
			return true
		}
		d.info("deferred skill crystallization skipped — loop %s judged "+
			"achieved but verdict trust is %s (source %s)", loopID, trust,
			pyval.Str(source))
		return false
	}
	return true
}
