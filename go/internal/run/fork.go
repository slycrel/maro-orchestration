package run

import (
	"context"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"sync"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Fork/join (design note §3): a parent attempt's plan step may run several
// child goals as their own runs, joined under a policy. The order is causal
// and each step survives a crash between it and the next:
//
//	Fork (members fixed) → children run → JoinDecision → CancellationIssued
//	per loser → ChildTerminal per member (by its own driver) → JoinSettled →
//	the parent step continues.
//
// v1 children are CONFINED: they run tool-less (the shell refuses any
// reported effect), so a fork is replayable from the journal, never from
// scheduler timing. Children with a working copy of their own arrive with a
// backend that can be confined to one (a Finding).

const (
	KindFork               record.Kind = "fork"
	KindJoinDecision       record.Kind = "join_decision"
	KindCancellationIssued record.Kind = "cancellation_issued"
	KindChildTerminal      record.Kind = "child_terminal"
	KindJoinSettled        record.Kind = "join_settled"
	OriginFork             GoalOrigin  = "fork" // a child's deliverable is its parent's step result
)

func init() { origins[OriginFork] = true }

// JoinPolicy: `all` waits for every member; `first_verdict` selects the
// first member whose effective closure verdict is achieved and cancels the
// rest (kept because the two-level scenario needs one early return).
type JoinPolicy string

const (
	JoinAll          JoinPolicy = "all"
	JoinFirstVerdict JoinPolicy = "first_verdict"
)

var joinPolicies = map[JoinPolicy]bool{JoinAll: true, JoinFirstVerdict: true}

// Fork fixes the member set of one parent step. The barrier is over
// exactly this set.
type Fork struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Step          int                 `json:"step"` // the parent's plan step (ordinal)
	Goals         []record.RecordID   `json:"goals"`
	Members       []record.AttemptRef `json:"members"` // one run per goal, attempt 1
	Policy        JoinPolicy          `json:"policy"`
}

func (r *Fork) Head() *record.Header { return &r.Header }
func (r *Fork) Kind() record.Kind    { return KindFork }
func (r *Fork) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("fork: %w", err)
	}
	if r.Subject.Kind != "fork" || r.Subject.ID != string(r.ID) {
		return errors.New("fork: subject must be the fork itself")
	}
	if r.Step < 1 {
		return errors.New("fork: step starts at 1")
	}
	if len(r.Members) < 2 || len(r.Goals) != len(r.Members) {
		return errors.New("fork: needs at least two members, one goal each")
	}
	seen := map[record.RunID]bool{}
	for i, m := range r.Members {
		if m.Run == "" || m.Attempt != 1 || seen[m.Run] || m.Run == r.RunID {
			return fmt.Errorf("fork: member %d must be a distinct child run at attempt 1", i)
		}
		seen[m.Run] = true
		if err := record.ValidateID(r.Goals[i]); err != nil {
			return fmt.Errorf("fork: goal %d: %w", i, err)
		}
	}
	if !joinPolicies[r.Policy] {
		return fmt.Errorf("fork: policy %q out of vocabulary", r.Policy)
	}
	return nil
}

// JoinDecision fixes the selection: `all` selects every member and cancels
// none (written when every member has a terminal); `first_verdict` selects
// one and cancels the rest.
type JoinDecision struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Fork          record.RecordID     `json:"fork"`
	Selected      []record.AttemptRef `json:"selected"`
	Cancel        []record.AttemptRef `json:"cancel,omitempty"`
}

func (r *JoinDecision) Head() *record.Header { return &r.Header }
func (r *JoinDecision) Kind() record.Kind    { return KindJoinDecision }
func (r *JoinDecision) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := forkScoped(&r.Header, r.Fork, "join_decision"); err != nil {
		return err
	}
	if len(r.Selected) == 0 {
		return errors.New("join_decision: selects at least one member")
	}
	return nil
}

// CancellationIssued executes a decision for one loser; idempotent by
// Fork+Child. The child's driver consumes it at its next boundary.
type CancellationIssued struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Fork          record.RecordID   `json:"fork"`
	Child         record.AttemptRef `json:"child"`
	Reason        string            `json:"reason"`
}

func (r *CancellationIssued) Head() *record.Header { return &r.Header }
func (r *CancellationIssued) Kind() record.Kind    { return KindCancellationIssued }
func (r *CancellationIssued) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := forkScoped(&r.Header, r.Fork, "cancellation_issued"); err != nil {
		return err
	}
	if r.Child.Run == "" || r.Child.Attempt == 0 || strings.TrimSpace(r.Reason) == "" {
		return errors.New("cancellation_issued: names the child attempt and a reason")
	}
	return nil
}

// ChildState is how a member ended, as its own driver saw it.
type ChildState string

const (
	ChildCompleted     ChildState = "completed"
	ChildCompletedLate ChildState = "completed_late" // finished after the decision; evidence, never selected, never learned from
	ChildCancelled     ChildState = "cancelled"
	ChildFailed        ChildState = "failed"
)

var childStates = map[ChildState]bool{ChildCompleted: true, ChildCompletedLate: true, ChildCancelled: true, ChildFailed: true}

// ChildTerminal is written by the child attempt's OWN driver (run-scoped to
// the child); a terminal from any other generation is refused by the fold.
type ChildTerminal struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Fork          record.RecordID   `json:"fork"`
	Child         record.AttemptRef `json:"child"`
	State         ChildState        `json:"state"`
}

func (r *ChildTerminal) Head() *record.Header { return &r.Header }
func (r *ChildTerminal) Kind() record.Kind    { return KindChildTerminal }
func (r *ChildTerminal) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("child_terminal: %w", err)
	}
	if err := record.ValidateID(r.Fork); err != nil {
		return fmt.Errorf("child_terminal: fork: %w", err)
	}
	if r.Subject.Kind != "fork" || r.Subject.ID != string(r.Fork) {
		return errors.New("child_terminal: subject must be the fork")
	}
	if r.Child.Run != r.RunID || r.Child.Attempt != r.Attempt {
		return errors.New("child_terminal: written by the child attempt it names, no other")
	}
	if !childStates[r.State] {
		return fmt.Errorf("child_terminal: state %q out of vocabulary", r.State)
	}
	return nil
}

// JoinSettled: every member has a ChildTerminal; the parent step may continue.
type JoinSettled struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Fork          record.RecordID `json:"fork"`
}

func (r *JoinSettled) Head() *record.Header { return &r.Header }
func (r *JoinSettled) Kind() record.Kind    { return KindJoinSettled }
func (r *JoinSettled) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	return forkScoped(&r.Header, r.Fork, "join_settled")
}

func forkScoped(h *record.Header, fork record.RecordID, what string) error {
	if err := runScoped(h); err != nil {
		return fmt.Errorf("%s: %w", what, err)
	}
	if err := record.ValidateID(fork); err != nil {
		return fmt.Errorf("%s: fork: %w", what, err)
	}
	if h.Subject.Kind != "fork" || h.Subject.ID != string(fork) {
		return fmt.Errorf("%s: subject must be the fork", what)
	}
	return nil
}

// ForkState is the fold of one fork. The parent's child goroutines share
// one while they run: every field access goes through the mutex.
type ForkState struct {
	Fork      *Fork
	Decision  *JoinDecision
	Cancelled map[record.RunID]*CancellationIssued
	Terminals map[record.RunID]*ChildTerminal
	Settled   *JoinSettled
	mu        sync.Mutex
	// order serializes this process's fork commits that the fold reads as
	// a causal order: a child's terminal and the (fold → decide → commit)
	// of a decision. Without it a sibling's terminal can land between the
	// decision's fold and its commit, and the decision then cancels a
	// member the journal shows terminal before it — a history the fold
	// refuses. Found by the kill matrix once folds pinned their prefix
	// (step 9): the fold got slower, the window wider.
	order sync.Mutex
}

// Complete reports whether every member has a terminal.
func (f *ForkState) Complete() bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.complete()
}

func (f *ForkState) complete() bool {
	for _, m := range f.Fork.Members {
		if f.Terminals[m.Run] == nil {
			return false
		}
	}
	return true
}

// terminal returns a member's terminal, if any.
func (f *ForkState) terminal(r record.RunID) *ChildTerminal {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Terminals[r]
}

// adopt takes the folded state of the same fork.
func (f *ForkState) adopt(g *ForkState) {
	if g == nil {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.Terminals, f.Cancelled, f.Decision, f.Settled = g.Terminals, g.Cancelled, g.Decision, g.Settled
}

func (f *ForkState) decision() *JoinDecision {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.Decision
}

func (f *ForkState) isMember(r record.RunID) bool {
	for _, m := range f.Fork.Members {
		if m.Run == r {
			return true
		}
	}
	return false
}

// ---- the parent's fork step ----

// ParallelStep is a plan step that forks: several sub-goals, one policy.
type ParallelStep struct {
	Ordinal int           `json:"ordinal"`
	Goals   []thought.Ref `json:"goals"` // step thoughts: the sub-goal texts
	Policy  JoinPolicy    `json:"policy"`
}

// forkOrigin is the child's delivery edge: its payload is consumed by the
// parent's join, so presenting is recording that it is available.
type forkOrigin struct{}

func (forkOrigin) Name() GoalOrigin                            { return OriginFork }
func (forkOrigin) Present(context.Context, Presentation) error { return nil }

// forkStep runs plan step k of the parent as a fork: creates the child
// goals and the Fork (once), drives the members concurrently under the
// parent's context until the join settles, and returns the composed
// result. Every transition is a keyed commit; a restart resumes from the
// fold's ForkState.
func (d *Driver) forkStep(ctx context.Context, rs *RunState, a *AttemptState, k int, ps *ParallelStep, existing *ForkState) (*ForkState, []byte, error) {
	n := a.Attempt.Attempt
	fs := existing
	if fs == nil {
		fk := &Fork{Header: header(record.Ref{}, rs.Run, n, "fork/1"), Step: k, Policy: ps.Policy}
		fk.Subject = record.Ref{Kind: "fork", ID: string(fk.ID)}
		recs := []record.Record{}
		for _, gref := range ps.Goals {
			text, err := d.Store.Get(gref)
			if err != nil {
				return nil, nil, err
			}
			goalRef, err := d.Store.Put(thought.Goal, text)
			if err != nil {
				return nil, nil, err
			}
			g, fam := Intake(text, goalRef, OriginFork, LaneNow, DeliveryPolicy{Required: TransportAccepted})
			g.Parent, g.Root = rs.Goal.ID, rs.Goal.Root
			child := record.RunID(record.NewID())
			fk.Goals = append(fk.Goals, g.ID)
			fk.Members = append(fk.Members, record.AttemptRef{Run: child, Attempt: 1})
			recs = append(recs, g, fam)
		}
		recs = append(recs, fk)
		if err := d.commit(ctx, fmt.Sprintf("fork/%s/%d/%d", rs.Run, n, k), recs...); err != nil {
			return nil, nil, err
		}
		fs = &ForkState{Fork: fk, Cancelled: map[record.RunID]*CancellationIssued{}, Terminals: map[record.RunID]*ChildTerminal{}}
		d.emit(rs, n, "fork", Executing, fmt.Sprintf("step %d: %d members, %s", k, len(fk.Members), fk.Policy))
		if err := d.crash("after_fork"); err != nil {
			return nil, nil, err
		}
	}
	// a restart after a decision: its consequences (the cancellations) are
	// repaired BEFORE any member is driven, or a loser would run again
	if dec := fs.decision(); dec != nil && len(dec.Cancel) > 0 {
		if err := d.commitDecision(ctx, rs, fs, dec); err != nil {
			return nil, nil, err
		}
	}
	// drive the members that are not terminal, concurrently, under the
	// parent's context; each child's driver writes its own ChildTerminal
	if err := d.driveChildren(ctx, rs, fs); err != nil {
		return nil, nil, err
	}
	// JoinDecision (once): under first_verdict it may already have landed
	// early; otherwise every member is terminal now
	if fs.decision() == nil {
		led, err := Fold(d.J.Production(), d.Store)
		if err != nil {
			return nil, nil, err
		}
		dec := decide(fs, led)
		if dec == nil {
			return nil, nil, fmt.Errorf("run: fork %s: no decision possible with every member terminal", fs.Fork.ID)
		}
		if err := d.commitDecision(ctx, rs, fs, dec); err != nil {
			return nil, nil, err
		}
	}
	if err := d.driveChildren(ctx, rs, fs); err != nil {
		return nil, nil, err
	}
	// JoinSettled: only when the drain barrier is provably met
	if fs.Settled == nil {
		if !fs.Complete() {
			return nil, nil, fmt.Errorf("run: fork %s: not every member is terminal after the drain", fs.Fork.ID)
		}
		js := &JoinSettled{Header: header(record.Ref{Kind: "fork", ID: string(fs.Fork.ID)}, rs.Run, n, "join_settled/1"), Fork: fs.Fork.ID}
		if err := d.commit(ctx, fmt.Sprintf("join/%s/settled", fs.Fork.ID), js); err != nil {
			return nil, nil, err
		}
		fs.Settled = js
		d.emit(rs, n, "join_settled", Executing, "")
		if err := d.crash("after_join_settled"); err != nil {
			return nil, nil, err
		}
	}
	result, err := d.forkResult(fs)
	return fs, result, err
}

// driveChildren runs every non-terminal member to its terminal, concurrently.
// Under first_verdict the decision is made as soon as one member's closure
// is achieved — while the others still run: they are cancelled (their
// contexts, then a CancellationIssued each) and end at their next boundary
// as cancelled, or completed_late if they were already done.
func (d *Driver) driveChildren(ctx context.Context, rs *RunState, fs *ForkState) error {
	led, err := Fold(d.J.Production(), d.Store)
	if err != nil {
		return err
	}
	type running struct {
		cancel context.CancelFunc
		run    record.RunID
	}
	var mu sync.Mutex
	var wg sync.WaitGroup
	live := map[record.RunID]running{}
	stopped := false // set when a child's error ends the pass: later cancellations are not "expected"
	errs := make(chan error, len(fs.Fork.Members))
	childState := func(i int, m record.AttemptRef) (*RunState, error) {
		crs := led.Runs[m.Run]
		if crs != nil {
			return crs, nil
		}
		g := led.goal(fs.Fork.Goals[i])
		fam := led.Families[fs.Fork.Goals[i]]
		if g == nil || fam == nil {
			return nil, fmt.Errorf("run: fork %s member %d: child goal not in the journal", fs.Fork.ID, i)
		}
		return &RunState{Run: m.Run, Goal: g, Family: fam}, nil
	}
	// early decision (first_verdict): after any child's terminal, decide;
	// cancel the members still running
	onTerminal := func() error {
		if fs.Fork.Policy != JoinFirstVerdict {
			return nil
		}
		mu.Lock()
		defer mu.Unlock()
		if fs.Decision != nil {
			return nil
		}
		fs.order.Lock()
		defer fs.order.Unlock()
		led2, err := Fold(d.J.Production(), d.Store)
		if err != nil {
			return err
		}
		fs.adopt(led2.Forks[fs.Fork.ID])
		dec := decide(fs, led2)
		if dec == nil || len(dec.Selected) != 1 {
			return nil
		}
		if err := d.commitDecision(ctx, rs, fs, dec); err != nil {
			return err
		}
		for _, c := range dec.Cancel {
			if r, ok := live[c.Run]; ok {
				r.cancel() // the loser's invocation ends; it is re-driven to its cancelled terminal below
			}
		}
		return nil
	}
	for i, m := range fs.Fork.Members {
		if fs.terminal(m.Run) != nil {
			continue
		}
		crs, err := childState(i, m)
		if err != nil {
			return err
		}
		cctx, cancel := context.WithCancel(ctx)
		mu.Lock()
		live[m.Run] = running{cancel: cancel, run: m.Run}
		mu.Unlock()
		wg.Add(1)
		go func(crs *RunState, cctx context.Context, member int) {
			defer wg.Done()
			var err error
			func() {
				defer func() {
					if r := recover(); r != nil {
						err = fmt.Errorf("run: fork %s member %d (%s) panicked: %v", fs.Fork.ID, member, crs.Run, r)
					}
				}()
				err = d.driveChild(cctx, crs, fs, member)
			}()
			mu.Lock()
			wasStopped := stopped
			mu.Unlock()
			if err != nil && cctx.Err() != nil && ctx.Err() == nil && !wasStopped {
				err = nil // cancelled by the decision: expected; re-driven below
			}
			if err == nil {
				err = onTerminal()
			}
			if err != nil {
				// one child's failure (or a crash seam) ends the parent's pass:
				// every other child is cancelled so the parent returns now —
				// their attempts are recoverable, and the next pass resumes them
				mu.Lock()
				stopped = true
				for _, r := range live {
					r.cancel()
				}
				mu.Unlock()
			}
			errs <- err
		}(crs, cctx, i+1)
	}
	wg.Wait()
	close(errs)
	for err := range errs {
		if err != nil {
			return err
		}
	}
	// the cancelled losers: re-driven with the parent's context, they see
	// the cancellation at their first boundary and end cancelled
	led, err = Fold(d.J.Production(), d.Store)
	if err != nil {
		return err
	}
	fs.adopt(led.Forks[fs.Fork.ID])
	for i, m := range fs.Fork.Members {
		if fs.terminal(m.Run) != nil {
			continue
		}
		crs, err := childState(i, m)
		if err != nil {
			return err
		}
		if err := d.driveChild(ctx, crs, fs, i+1); err != nil {
			return err
		}
	}
	led, err = Fold(d.J.Production(), d.Store)
	if err != nil {
		return err
	}
	fs.adopt(led.Forks[fs.Fork.ID])
	return nil
}

// commitDecision commits the decision and its cancellations (keyed, so a
// restart replays them).
func (d *Driver) commitDecision(ctx context.Context, rs *RunState, fs *ForkState, dec *JoinDecision) error {
	n := rs.Latest().Attempt.Attempt
	dec.Header = header(record.Ref{Kind: "fork", ID: string(fs.Fork.ID)}, rs.Run, n, "join_decision/1")
	if err := d.commit(ctx, fmt.Sprintf("join/%s/decision", fs.Fork.ID), dec); err != nil {
		return err
	}
	fs.mu.Lock()
	fs.Decision = dec
	fs.mu.Unlock()
	d.emit(rs, n, "join_decision", Executing, fmt.Sprintf("%d selected, %d cancelled", len(dec.Selected), len(dec.Cancel)))
	if err := d.crash("after_join_decision"); err != nil {
		return err
	}
	for _, c := range dec.Cancel {
		fs.mu.Lock()
		// a cancellation is for a member still running; one that reached
		// its terminal first (completed late, while the process died
		// between the decision and its cancellations) needs none, and the
		// fold refuses one
		done := fs.Cancelled[c.Run] != nil || fs.Terminals[c.Run] != nil
		fs.mu.Unlock()
		if done {
			continue
		}
		ci := &CancellationIssued{Header: header(record.Ref{Kind: "fork", ID: string(fs.Fork.ID)}, rs.Run, n, "cancellation_issued/1"), Fork: fs.Fork.ID, Child: c, Reason: "join decision: not selected under " + string(fs.Fork.Policy)}
		if err := d.commit(ctx, fmt.Sprintf("join/%s/cancel/%s", fs.Fork.ID, c.Run), ci); err != nil {
			return err
		}
		fs.mu.Lock()
		fs.Cancelled[c.Run] = ci
		fs.mu.Unlock()
		if err := d.crash("after_cancellation"); err != nil {
			return err
		}
	}
	return nil
}

// driveChild is the child's own driver: confined (tool-less), fork origin,
// NOW lane with the parent's judge when the policy needs a verdict. It
// writes the ChildTerminal from the child attempt's own scope.
func (d *Driver) driveChild(ctx context.Context, crs *RunState, fs *ForkState, member int) error {
	cd := &Driver{J: d.J, Store: d.Store, Backend: d.Backend, Judge: d.Judge, Lane: LaneNow, Origin: forkOrigin{}, Timeout: d.Timeout, Health: d.Health, Events: d.Events,
		Confined: true, ChildOf: fs.Fork.ID, ModelJudge: fs.Fork.Policy == JoinFirstVerdict, MaxAttempts: d.MaxAttempts, MaxDeliveryAttempts: d.MaxDeliveryAttempts}
	// crash seams: "child:<seam>" fires in every child, "child:<n>:<seam>"
	// only in member n (1-based)
	if strings.HasPrefix(d.CrashAt, "child:") {
		rest := strings.TrimPrefix(d.CrashAt, "child:")
		var n int
		var seam string
		if _, err := fmt.Sscanf(rest, "%d:%s", &n, &seam); err == nil {
			if n == member {
				cd.CrashAt = seam
			}
		} else {
			cd.CrashAt = rest
		}
	}
	if err := cd.validate(); err != nil {
		return err
	}
	var rep *Report
	var err error
	a := crs.Latest()
	switch {
	case a == nil:
		rep, err = cd.drive(ctx, crs, nil, nil)
	case crs.Terminal():
		rep, err = cd.report(crs, a)
	case a.Has(Recorded) != nil:
		rep, err = cd.deliver(ctx, crs, a)
	default:
		if a.Current() != Recoverable {
			if err := cd.transition(ctx, crs, a, Recoverable, "process restarted before recorded (was "+string(a.Current())+")", nil); err != nil {
				return err
			}
		}
		var forced *Outcome
		if len(crs.Attempts) >= cd.MaxAttempts {
			forced = &Outcome{Terminal: invoke.TerminalFailed, Reason: fmt.Sprintf("attempt bound %d reached", cd.MaxAttempts)}
		}
		rep, err = cd.drive(ctx, crs, a, forced)
	}
	if err != nil {
		return err
	}
	return cd.childTerminal(ctx, crs, rep, fs)
}

// childTerminal is the child's last word: written from the child attempt
// that reached the terminal, once.
func (d *Driver) childTerminal(ctx context.Context, crs *RunState, rep *Report, fs *ForkState) error {
	fs.order.Lock()
	defer fs.order.Unlock()
	if fs.terminal(crs.Run) != nil {
		return nil
	}
	dec := fs.decision()
	state := ChildCompleted
	switch {
	case strings.Contains(rep.Mission.Reason, "cancelled by join"):
		state = ChildCancelled
	case rep.Mission.Terminal != string(invoke.TerminalComplete) && rep.Mission.Terminal != string(invoke.TerminalPartial):
		state = ChildFailed
	case dec != nil && !selected(dec, crs.Run):
		state = ChildCompletedLate
	}
	ct := &ChildTerminal{Header: header(record.Ref{Kind: "fork", ID: string(fs.Fork.ID)}, crs.Run, rep.Attempt, "child_terminal/1"), Fork: fs.Fork.ID, Child: record.AttemptRef{Run: crs.Run, Attempt: rep.Attempt}, State: state}
	if err := d.commit(ctx, fmt.Sprintf("join/%s/terminal/%s", fs.Fork.ID, crs.Run), ct); err != nil {
		return err
	}
	fs.mu.Lock()
	fs.Terminals[crs.Run] = ct
	fs.mu.Unlock()
	d.emit(crs, rep.Attempt, "child_terminal", Recorded, string(state))
	return d.crash("after_child_terminal")
}

func selected(dec *JoinDecision, r record.RunID) bool {
	for _, s := range dec.Selected {
		if s.Run == r {
			return true
		}
	}
	return false
}

// decide is the pure join rule over the folded members. `all`: every
// member terminal ⇒ select every completed one, cancel none. `first_verdict`:
// the first member (by member order) whose closure resolved achieved ⇒
// select it, cancel the rest; if every member is terminal and none
// achieved ⇒ select the completed ones (the parent judges), cancel none.
func decide(fs *ForkState, led *Ledger) *JoinDecision {
	return decideWith(fs, func(r record.RunID) string {
		if crs := led.Runs[r]; crs != nil && crs.Closure != nil {
			return crs.Closure.Outcome
		}
		return ""
	})
}

// decideWith is the pure join rule with the closure outcome of each member
// supplied by the caller (the fold supplies it from the journal prefix).
func decideWith(fs *ForkState, closure func(record.RunID) string) *JoinDecision {
	fs.mu.Lock()
	defer fs.mu.Unlock()
	dec := &JoinDecision{Fork: fs.Fork.ID}
	switch fs.Fork.Policy {
	case JoinFirstVerdict:
		for _, m := range fs.Fork.Members {
			if closure(m.Run) == "achieved" {
				dec.Selected = []record.AttemptRef{m}
				for _, o := range fs.Fork.Members {
					if o.Run != m.Run && fs.Terminals[o.Run] == nil {
						dec.Cancel = append(dec.Cancel, o)
					}
				}
				return dec
			}
		}
		fallthrough
	case JoinAll:
		if !fs.complete() {
			return nil
		}
		for _, m := range fs.Fork.Members {
			if t := fs.Terminals[m.Run]; t != nil && t.State != ChildFailed && t.State != ChildCancelled {
				dec.Selected = append(dec.Selected, m)
			}
		}
		if len(dec.Selected) == 0 {
			dec.Selected = fs.Fork.Members // every member failed: the parent judges the failures
		}
		return dec
	}
	return nil
}

// sameDecision: the same members, in the same order, selected and cancelled.
func sameDecision(a, b *JoinDecision) bool {
	if len(a.Selected) != len(b.Selected) || len(a.Cancel) != len(b.Cancel) {
		return false
	}
	for i := range a.Selected {
		if a.Selected[i] != b.Selected[i] {
			return false
		}
	}
	for i := range a.Cancel {
		if a.Cancel[i] != b.Cancel[i] {
			return false
		}
	}
	return true
}

// forkResult composes the fork step's result from the selected members'
// recorded responses, whole, in member order.
func (d *Driver) forkResult(fs *ForkState) ([]byte, error) {
	led, err := Fold(d.J.Production(), d.Store)
	if err != nil {
		return nil, err
	}
	return composeFork(fs, led, d.Store)
}

// composeFork is pure over the fold; the run fold re-derives it.
func composeFork(fs *ForkState, led *Ledger, store *thought.Store) ([]byte, error) {
	var b strings.Builder
	for i, m := range fs.Decision.Selected {
		crs := led.Runs[m.Run]
		if crs == nil {
			return nil, fmt.Errorf("run: fork %s selected member %s that is not in the journal", fs.Fork.ID, m.Run)
		}
		goal, err := store.Get(crs.Goal.Text)
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(&b, "### Member %d: %s\n", i+1, goal)
		rec := crs.Latest().Has(Recorded)
		if rec == nil {
			return nil, fmt.Errorf("run: fork %s selected member %s that is not recorded", fs.Fork.ID, m.Run)
		}
		if rec.Outcome.Response != nil {
			r, err := store.Get(*rec.Outcome.Response)
			if err != nil {
				return nil, err
			}
			b.Write(r)
		} else {
			b.WriteString("(failed: " + rec.Outcome.Reason + ")")
		}
		b.WriteString("\n\n")
	}
	return []byte(strings.TrimRight(b.String(), "\n") + "\n"), nil
}

func init() {
	reg := func(k record.Kind, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindFork, Fork{}, "the parent driver at a parallel plan step (with the child goals, one command)",
		"the parent driver (resume at the step); the fold (member set = the barrier); child drivers (which fork they belong to)",
		"which child runs exist for the step and the join policy over exactly them")
	reg(KindJoinDecision, JoinDecision{}, "the parent driver, once, from the join rule over the folded members",
		"the parent driver (what to cancel, what to compose); child drivers (late completion)",
		"which members' results the step is made of")
	reg(KindCancellationIssued, CancellationIssued{}, "the parent driver, per loser, from the decision",
		"the loser's driver at its next boundary (consumed like an interrupt)",
		"that the loser stops at its next boundary")
	reg(KindChildTerminal, ChildTerminal{}, "the child attempt's OWN driver at its terminal",
		"the parent driver (the drain barrier); the fold (JoinSettled legality)",
		"whether the join may settle; completed_late is evidence, never selected, never learned from")
	reg(KindJoinSettled, JoinSettled{}, "the parent driver, only when every member has a terminal",
		"the parent driver (continue the step); the fold",
		"that the parent step may continue")
}
