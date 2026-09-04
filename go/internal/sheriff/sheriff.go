// Package sheriff is the first supervised lane: its own evaluator over
// committed records, emitting `stuck` verdicts that NAME their evidence
// (design note §6, §10). Thresholds are configuration with a why and are
// reported, never enforced: a stuck verdict is evidence for operators and
// judges, not a kill.
package sheriff

import (
	"context"
	"fmt"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Sheriff evaluates every non-terminal run attempt on each tick.
type Sheriff struct {
	J     *journal.Journal
	Store *thought.Store
	// Every is the evaluation interval. Why 30s: a stall is measured in
	// minutes; evaluating faster burns folds for no earlier decision.
	Every time.Duration
	// StallAfter is how long an attempt may show no committed activity
	// before it is called stuck. Why 15m: the longest legitimate silence
	// observed on this box is a single tool-heavy subprocess turn (~10m);
	// anything past that has, so far, always been a dead backend. Reported
	// in the verdict's basis, re-measured from live data (D13).
	StallAfter time.Duration
	// Clock is the time source (tests inject one).
	Clock func() time.Time
	// Tick, when set, replaces the interval: one evaluation per receive.
	Tick <-chan struct{}
	// Emitted, when set, receives every stuck verdict as it is committed.
	Emitted func(*verdict.Verdict)
}

func (s *Sheriff) Name() string          { return "sheriff" }
func (s *Sheriff) Stage() int            { return 2 } // a derived-work producer quiesces before the executor
func (s *Sheriff) Expect() time.Duration { return 3 * s.every() }

func (s *Sheriff) every() time.Duration {
	if s.Every == 0 {
		return 30 * time.Second
	}
	return s.Every
}

func (s *Sheriff) stallAfter() time.Duration {
	if s.StallAfter == 0 {
		return 15 * time.Minute
	}
	return s.StallAfter
}

func (s *Sheriff) now() time.Time {
	if s.Clock != nil {
		return s.Clock()
	}
	return time.Now().UTC()
}

// Run is the lane body: evaluate on every tick, report the head evaluated.
func (s *Sheriff) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	var ticks <-chan time.Time
	if s.Tick == nil {
		t := time.NewTicker(s.every())
		defer t.Stop()
		ticks = t.C
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticks:
		case <-s.Tick:
		}
		head, err := s.Evaluate(ctx)
		if err != nil {
			return err
		}
		hb.Progress(ctx, head)
	}
}

// Evaluate is one pass: for every non-terminal attempt whose last committed
// activity is older than StallAfter and that carries no stuck verdict yet,
// commit a deterministic `stuck` verdict naming the evidence and resolve
// it. Idempotent per (run, attempt). Returns the head it evaluated.
func (s *Sheriff) Evaluate(ctx context.Context) (uint64, error) {
	pr := s.J.Production()
	head := pr.Head()
	led, err := run.Fold(pr, s.Store)
	if err != nil {
		return 0, err
	}
	for _, rs := range led.Runs {
		if rs.Terminal() {
			continue
		}
		a := rs.Latest()
		if a.Stuck != nil {
			continue
		}
		last, basis := lastActivity(a)
		silent := s.now().Sub(last)
		if silent <= s.stallAfter() {
			continue
		}
		n := a.Attempt.Attempt
		v := &verdict.Verdict{Header: record.Header{ID: record.NewID(), Schema: "verdict/1", RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: s.now()},
			VerdictKind: verdict.KindStuck, Outcome: "stuck", Confidence: 1, Source: verdict.Source{Standing: verdict.StandingDeterministic}, Direction: verdict.Both, Basis: basis}
		key := fmt.Sprintf("sheriff/%s/%d", rs.Run, n)
		if _, err := s.J.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: s.J.Epoch(), Records: []record.Record{v}}); err != nil {
			return 0, err
		}
		if _, err := verdict.Commit(ctx, s.J, rs.Run, n, verdict.Candidates{Subject: v.Subject, VerdictKind: verdict.KindStuck, Verdicts: []*verdict.Verdict{v}}, verdict.DefaultThresholds); err != nil {
			return 0, err
		}
		if s.Emitted != nil {
			s.Emitted(v)
		}
	}
	return head, nil
}

// lastActivity is the newest committed record about the attempt — the
// fold's last attached attempt-scoped record (transition, stage, delivery,
// verdict), or the last invocation-side record if later — and the ref a
// stuck verdict names as its basis.
func lastActivity(a *run.AttemptState) (time.Time, []record.Ref) {
	last := a.Attempt.At
	basis := []record.Ref{{Kind: run.KindRunAttempt, ID: string(a.Attempt.ID)}}
	if !a.LastAt.IsZero() && !a.LastAt.Before(last) {
		last, basis = a.LastAt, []record.Ref{a.LastRef}
	}
	for _, st := range a.Invocations {
		at, ref := st.Invocation.At, record.Ref{Kind: st.Invocation.Kind(), ID: string(st.Invocation.ID)}
		if st.Terminal != nil {
			at, ref = st.Terminal.At, record.Ref{Kind: st.Terminal.Kind(), ID: string(st.Terminal.ID)}
		} else if n := len(st.Effects); n > 0 {
			at, ref = st.Effects[n-1].At, record.Ref{Kind: st.Effects[n-1].Kind(), ID: string(st.Effects[n-1].ID)}
		}
		if at.After(last) {
			last, basis = at, []record.Ref{ref}
		}
	}
	return last, basis
}
