package experiment

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Lane is the evaluator lane (§9, stage 2): the one reader with the
// evaluator capability. Each pass it folds the experiments and, for every
// open randomized-live cohort, commits the evidence its terminal units are
// owed and — once the cohort is full and evidenced — closes it: the
// commitment, the blinded judge's scores into the attestation, the
// measurement, and the lifecycle transition the measurement derives. A
// cohort closed but not yet measured (a crash mid-close) is finished the
// same way. Every commit is keyed; a pass repeats nothing.
type Lane struct {
	J     *journal.Journal
	Store *thought.Store
	// Judge is the tool-less backend the blinded evaluation runs on.
	Judge   invoke.Backend
	Every   time.Duration // pass interval; zero = 20s (as the tail's)
	Timeout time.Duration
	Tick    <-chan struct{}
	Events  func(string)
	// EvaluatorVersion is forwarded to the closer (empty = EvaluatorJudge).
	EvaluatorVersion string
}

func (l *Lane) Name() string          { return "evaluator" }
func (l *Lane) Stage() int            { return 2 }
func (l *Lane) Expect() time.Duration { return 3 * l.every() }

func (l *Lane) every() time.Duration {
	if l.Every == 0 {
		return 20 * time.Second
	}
	return l.Every
}

func (l *Lane) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	var ticks <-chan time.Time
	if l.Tick == nil {
		tk := time.NewTicker(l.every())
		defer tk.Stop()
		ticks = tk.C
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticks:
		case <-l.Tick:
		}
		head, err := l.Pass(ctx)
		if err != nil {
			return err
		}
		hb.Progress(ctx, head)
	}
}

// Pass: evidence for every live cohort's terminal units; close what is
// ready. Returns the head the pass read.
func (l *Lane) Pass(ctx context.Context) (uint64, error) {
	st, err := Fold(l.J, l.Store)
	if err != nil {
		return 0, err
	}
	c := &Closer{J: l.J, Store: l.Store, Judge: l.Judge, Timeout: l.Timeout, Events: l.Events, EvaluatorVersion: l.EvaluatorVersion}
	for _, id := range st.Order {
		x := st.Experiments[id]
		if x.Assignment != RandomizedLive || st.settled(id) {
			continue
		}
		if st.Closed[id] == nil {
			owed, err := c.Evidence(ctx, id)
			if err != nil {
				return 0, err
			}
			if owed > 0 {
				continue
			}
		}
		m, err := c.Close(ctx, id)
		if errors.Is(err, ErrConfig) {
			// a cohort this process cannot score (no judge): say so and
			// keep the lane; the cohort waits for a process that can
			if l.Events != nil {
				l.Events(fmt.Sprintf("evaluator: %s waits: %v", id, err))
			}
			continue
		}
		if err != nil {
			return 0, err
		}
		if l.Events != nil {
			l.Events(fmt.Sprintf("evaluator: %s closed: %s → %s (assigned %d, analyzed %d, exposed %d/%d, delta_pp %.3f)", id, m.Verdict, m.ItemEffect, m.Assigned, m.Analyzed, m.TreatmentN, m.ControlN, m.DeltaPP))
		}
	}
	return st.Head, nil
}

// settled reports whether an experiment's measurement has reached the
// item: a crash between the measurement and its transition leaves a
// measured experiment the next pass still owes a transition (or a
// no-transition verdict, which cites nothing and is settled by itself).
func (st *State) settled(id record.RecordID) bool {
	m := st.Measurements[id]
	if m == nil {
		return false
	}
	it := st.Runs.Learned.Items[m.Hypothesis.Item]
	if it == nil || cited(it, m.ID) {
		return true
	}
	_, ok := learn.StageFor(it.StageOf(m.Hypothesis.Revision), m.ItemEffect)
	return !ok
}

// Live lists the open live experiments (for the operator surface).
func (st *State) Live() []record.RecordID {
	var out []record.RecordID
	for _, id := range st.Order {
		if st.Experiments[id].Assignment == RandomizedLive && st.Closed[id] == nil {
			out = append(out, id)
		}
	}
	return out
}
