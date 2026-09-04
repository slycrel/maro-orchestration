package tail

import (
	"context"
	"fmt"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
)

// Timers is the lane of in-process periodic sweeps (§10: intervals carry a
// why; no cron). Stage 1: it stops accepting first at quiesce. v1 sweeps:
// tenure (candidate → observed) and expiry (→ tombstone).
type Timers struct {
	J *journal.Journal
	// Every is the sweep interval. Why 1m: tenure and expiry are measured in
	// applications and days; a minute is already generous.
	Every time.Duration
	// The bounds are learn.TenureBound and learn.ExpiryIdle: constants, so
	// the learned fold re-derives every tenure transition.
	Clock  func() time.Time
	Tick   <-chan struct{}
	Events func(string)
}

func (t *Timers) Name() string          { return "timers" }
func (t *Timers) Stage() int            { return 1 }
func (t *Timers) Expect() time.Duration { return 3 * t.every() }

func (t *Timers) every() time.Duration {
	if t.Every == 0 {
		return time.Minute
	}
	return t.Every
}

func (t *Timers) now() time.Time {
	if t.Clock != nil {
		return t.Clock()
	}
	return time.Now().UTC()
}

func (t *Timers) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	var ticks <-chan time.Time
	if t.Tick == nil {
		tk := time.NewTicker(t.every())
		defer tk.Stop()
		ticks = tk.C
	}
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticks:
		case <-t.Tick:
		}
		head, err := t.Sweep(ctx)
		if err != nil {
			return err
		}
		hb.Progress(ctx, head)
	}
}

// Sweep runs tenure and expiry once over the learned fold. Idempotent:
// every transition is keyed by (revision, to).
func (t *Timers) Sweep(ctx context.Context) (uint64, error) {
	pr := t.J.Production()
	head := pr.Head()
	led, err := learn.Fold(pr)
	if err != nil {
		return 0, err
	}
	tenure, expire := learn.TenureBound, learn.ExpiryIdle
	ids := make([]string, 0, len(led.Items))
	for id := range led.Items {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	for _, id := range ids {
		it := led.Items[learn.LearnedID(id)]
		cur := it.Current
		stage := it.StageOf(cur.ID)
		exps := led.Exposures[cur.ID] // applications + policy applications, Seq order
		switch {
		case stage == learn.Candidate && len(exps) >= tenure:
			ev := exps[tenure-1]
			if err := t.transition(ctx, it, cur, learn.Observed, ev.ID, fmt.Sprintf("tenure: %d exposures (bound %d)", len(exps), tenure)); err != nil {
				return 0, err
			}
		case (stage == learn.Candidate || stage == learn.Observed) && t.now().Sub(learn.LastActivity(cur, exps)) > expire:
			if err := t.transition(ctx, it, cur, learn.Tombstone, cur.ID, fmt.Sprintf("expiry: no exposure in %s", expire)); err != nil {
				return 0, err
			}
		}
	}
	return head, nil
}

func (t *Timers) transition(ctx context.Context, it *learn.Item, rev *learn.LearnedRevision, to learn.Stage, evidence record.RecordID, why string) error {
	x := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(it.ID)}, At: t.now()},
		Item: it.ID, Revision: rev.ID, From: it.StageOf(rev.ID), To: to, Actor: learn.ActorTenure, Evidence: evidence, Why: why}
	if _, err := t.J.Submit(ctx, journal.Command{IdempotencyKey: fmt.Sprintf("tenure/%s/%s", rev.ID, to), Epoch: t.J.Epoch(), Records: []record.Record{x}}); err != nil {
		return err
	}
	if t.Events != nil {
		t.Events(fmt.Sprintf("tenure %s: %s → %s (%s)", it.ID, x.From, to, why))
	}
	return nil
}
