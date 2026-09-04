// Package tail is the learning half of the spine (design note §8): after a
// run is `recorded` it is OBSERVED (its committed receipts, verdicts,
// usage, friction), DIAGNOSED (deterministic classifiers, then a model
// lens naming one registered failure class), and a PROPOSAL may follow —
// a LearnedRevision at `candidate` that must earn its standing. The timers
// lane carries tenure: candidate → observed after enough applications,
// expiry → tombstone. Nothing here reaches `effective`; that is measured
// (steps 10–11).
package tail

import (
	"errors"
	"fmt"
	"reflect"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

const (
	KindDiagnosis record.Kind = "diagnosis"
	KindTailDone  record.Kind = "tail_done"
)

// Signal is a deterministic classifier's finding about a recorded run:
// what the records show, no model in the loop.
type Signal string

const (
	SignalBackendFailed  Signal = "backend_failed"  // the execution's terminal was failed at the backend
	SignalBlockedStep    Signal = "blocked_step"    // an AGENDA step was judged blocked
	SignalUnclearGoal    Signal = "unclear_goal"    // intent said the goal was not clear
	SignalPartialOutput  Signal = "partial_output"  // a stream ended partial
	SignalUnjudged       Signal = "unjudged"        // closure resolved unknown (no judge, or the judge abstained)
	SignalNotAchieved    Signal = "not_achieved"    // closure resolved not_achieved
	SignalDeliveryFailed Signal = "delivery_failed" // the mission failed at delivery
	SignalInterrupted    Signal = "interrupted"     // stopped by an interrupt or a join cancellation
	SignalRecovered      Signal = "recovered"       // more than one attempt (a restart)
	SignalStuck          Signal = "stuck"           // the sheriff called it stuck
	SignalConfined       Signal = "confined_effect" // a confined child reported an effect (refused)
)

var signals = map[Signal]bool{SignalBackendFailed: true, SignalBlockedStep: true, SignalUnclearGoal: true, SignalPartialOutput: true, SignalUnjudged: true, SignalNotAchieved: true, SignalDeliveryFailed: true, SignalInterrupted: true, SignalRecovered: true, SignalStuck: true, SignalConfined: true}

// FailureClass is the one registered class the lens names for a run. The
// deterministic signals bound what the lens may say: a run with no
// signals is `none` unless the lens finds the ANSWER wrong or incomplete.
type FailureClass string

const (
	ClassNone             FailureClass = "none"
	ClassBackendFailure   FailureClass = "backend_failure"
	ClassBlockedStep      FailureClass = "blocked_step"
	ClassUnclearGoal      FailureClass = "unclear_goal"
	ClassPartialOutput    FailureClass = "partial_output"
	ClassUnjudged         FailureClass = "unjudged"
	ClassNotAchieved      FailureClass = "not_achieved"
	ClassDeliveryFailed   FailureClass = "delivery_failed"
	ClassInterrupted      FailureClass = "interrupted"
	ClassStuck            FailureClass = "stuck"
	ClassWrongAnswer      FailureClass = "wrong_answer"      // lens-only: the delivered answer is wrong
	ClassIncompleteAnswer FailureClass = "incomplete_answer" // lens-only: the delivered answer misses part of the goal
)

var classes = map[FailureClass]bool{ClassNone: true, ClassBackendFailure: true, ClassBlockedStep: true, ClassUnclearGoal: true, ClassPartialOutput: true, ClassUnjudged: true, ClassNotAchieved: true, ClassDeliveryFailed: true, ClassInterrupted: true, ClassStuck: true, ClassWrongAnswer: true, ClassIncompleteAnswer: true}

// ClassNames lists the vocabulary for prompts and validation.
func ClassNames() []string {
	return []string{"none", "backend_failure", "blocked_step", "unclear_goal", "partial_output", "unjudged", "not_achieved", "delivery_failed", "interrupted", "stuck", "wrong_answer", "incomplete_answer"}
}

// Diagnosis is one attempt's diagnosis: the deterministic signals (a
// bounded projection, §14), the lens's class with its why, and the lens
// invocation when one was made.
type Diagnosis struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Signals       []Signal        `json:"signals"`
	Class         FailureClass    `json:"class"`
	Why           string          `json:"why"`
	Lens          record.RecordID `json:"lens,omitempty"` // the diagnose invocation; absent when no lens ran
	LensRule      string          `json:"lens_rule"`      // "lens" | "no_lens:<reason>" | "signals_only"
}

func (r *Diagnosis) Head() *record.Header { return &r.Header }
func (r *Diagnosis) Kind() record.Kind    { return KindDiagnosis }
func (r *Diagnosis) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 || r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("diagnosis: subject must be the run, attempt-scoped")
	}
	seen := map[Signal]bool{}
	for _, s := range r.Signals {
		if !signals[s] || seen[s] {
			return fmt.Errorf("diagnosis: signal %q out of vocabulary or repeated", s)
		}
		seen[s] = true
	}
	if !classes[r.Class] {
		return fmt.Errorf("diagnosis: class %q out of vocabulary", r.Class)
	}
	if strings.TrimSpace(r.Why) == "" {
		return errors.New("diagnosis: needs a why")
	}
	if r.Lens != "" {
		if err := record.ValidateID(r.Lens); err != nil {
			return fmt.Errorf("diagnosis: lens: %w", err)
		}
		if r.LensRule != "lens" {
			return errors.New("diagnosis: a lens invocation means lens_rule lens")
		}
	} else if r.LensRule != "signals_only" && !strings.HasPrefix(r.LensRule, "no_lens:") {
		return errors.New("diagnosis: without a lens, lens_rule is signals_only or no_lens:<reason>")
	}
	return nil
}

// TailDone closes the tail for an attempt: what it proposed, or why it
// skipped. One per recorded attempt.
type TailDone struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Diagnosis     record.RecordID   `json:"diagnosis,omitempty"`
	Proposals     []record.RecordID `json:"proposals,omitempty"` // learned revisions at candidate, provenance tail
	Skipped       string            `json:"skipped,omitempty"`   // why nothing was learned (a cancelled arm, a child of a fork)
}

func (r *TailDone) Head() *record.Header { return &r.Header }
func (r *TailDone) Kind() record.Kind    { return KindTailDone }
func (r *TailDone) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 || r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("tail_done: subject must be the run, attempt-scoped")
	}
	if (r.Diagnosis == "") != (r.Skipped != "") {
		return errors.New("tail_done: exactly one of a diagnosis or a skip reason")
	}
	if r.Diagnosis != "" {
		if err := record.ValidateID(r.Diagnosis); err != nil {
			return fmt.Errorf("tail_done: diagnosis: %w", err)
		}
	}
	if r.Skipped != "" && len(r.Proposals) > 0 {
		return errors.New("tail_done: a skipped tail proposes nothing")
	}
	for _, p := range r.Proposals {
		if err := record.ValidateID(p); err != nil {
			return fmt.Errorf("tail_done: proposal: %w", err)
		}
	}
	return nil
}

func now() time.Time { return time.Now().UTC() }

func init() {
	reg := func(k record.Kind, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindDiagnosis, Diagnosis{}, "the tail lane, once per recorded attempt (signals deterministic; class from the lens)",
		"the tail fold (re-derived); operators (`maro-go runs`); experiments (failure class as an outcome dimension, step 10)",
		"what went wrong, as a registered class with the signals that bound it")
	reg(KindTailDone, TailDone{}, "the tail lane, after the diagnosis and any proposals",
		"the tail lane (skip attempts already done); the learned fold (provenance of tail proposals)",
		"that an attempt has been learned from, or why not")
}
