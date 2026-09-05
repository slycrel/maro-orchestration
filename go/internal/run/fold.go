package run

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Delivery is the folded state of one DeliveryPrepared.
type Delivery struct {
	Prepared *DeliveryPrepared
	Started  []*DeliveryStarted   // by N; a start without a result = outcome unknown
	Attempts []*DeliveryAttempted // by N
	Ack      *DeliveryAcked
}

// Accepted reports whether the payload provably reached the transport: an
// accepted attempt, or an ack (a token only reaches a client through a
// presentation).
func (d *Delivery) Accepted() bool {
	if d.Ack != nil {
		return true
	}
	for _, a := range d.Attempts {
		if a.Result == TransportAccepted {
			return true
		}
	}
	return false
}

// Unknown counts presentations the process died inside: each may have
// reached the user, so a later presentation may be a duplicate.
func (d *Delivery) Unknown() int {
	n := 0
	for _, a := range d.Attempts {
		if a.Result == DeliveryUnknown {
			n++
		}
	}
	return n
}

// Pending reports a start with no result yet.
func (d *Delivery) Pending() bool { return len(d.Started) > len(d.Attempts) }

// checkAck is the one binding rule, shared by Ack (the writer) and Fold (the
// reader): the delivery must have been started at least once, the token
// must be the one bound to this delivery and payload, the hash must be the
// payload's, and the record must be scoped to the delivery's owner.
func (d *Delivery) checkAck(x *DeliveryAcked) error {
	p := d.Prepared
	if len(d.Started) == 0 {
		return ErrNotPresented
	}
	if x.Token != TokenFor(p.ID, p.Payload.Hash, p.Nonce) {
		return ErrBadToken
	}
	if x.PayloadHash != p.Payload.Hash {
		return fmt.Errorf("%w: payload hash", ErrBadToken)
	}
	if x.RunID != p.RunID || x.Attempt != p.Attempt {
		return fmt.Errorf("run: ack for delivery %s is scoped to %s/%d, the delivery to %s/%d", p.ID, x.RunID, x.Attempt, p.RunID, p.Attempt)
	}
	return nil
}

// AttemptState is one attempt generation of a run.
type AttemptState struct {
	Attempt     *RunAttempt
	Transitions []*Transition // in Seq order
	Delivery    *Delivery     // at most one per attempt
	Invocations []*invoke.State
	Recall      *learn.RecallSelection // the attempt's recall selection, when it reached that stage
	Policy      *learn.PolicySelection // the attempt's policy selection (always: same command as the attempt)
	Stuck       *verdict.Resolution    // the sheriff's stuck resolution, when one exists
	Overage     *Overage               // the attempt's overage against the goal's target, when its recorded usage exceeded it
	// AGENDA stages, as committed
	Intent *IntentAssessment
	Plan   *Plan
	Steps  []*StepDone // by ordinal, dense from 1
	// Verdicts this attempt committed (run-scoped), in Seq order.
	Verdicts []*verdict.Verdict
	// LastAt/LastRef: the newest attempt-scoped record the fold attached —
	// the sheriff's "last committed activity", with invocation-side records
	// considered by the sheriff itself.
	LastAt  time.Time
	LastRef record.Ref
}

// touch records an attempt-scoped record as activity: the ref is the
// newest record by journal order (the scan's order), the time the latest
// observation time seen — a record built before a call and committed after
// it carries an earlier At than the call's own records.
func (a *AttemptState) touch(r record.Record) {
	h := r.Head()
	a.LastRef = record.Ref{Kind: r.Kind(), ID: string(h.ID)}
	if h.At.After(a.LastAt) {
		a.LastAt = h.At
	}
}

// Current is the attempt's latest state.
func (a *AttemptState) Current() State {
	if len(a.Transitions) == 0 {
		return ""
	}
	return a.Transitions[len(a.Transitions)-1].To
}

// Has reports the first transition into s.
func (a *AttemptState) Has(s State) *Transition {
	for _, t := range a.Transitions {
		if t.To == s {
			return t
		}
	}
	return nil
}

// deliveryState is the delivery state the attempt's transitions claim.
func (a *AttemptState) deliveryState() DeliveryState {
	for i := len(a.Transitions) - 1; i >= 0; i-- {
		if a.Transitions[i].To == Delivered {
			return a.Transitions[i].Delivery
		}
	}
	return ""
}

// RunState is the fold of every record about one run.
type RunState struct {
	Run      record.RunID
	Goal     *Goal
	Family   *FamilyAssessment
	Attempts []*AttemptState // by attempt number, dense from 1
	Closure  *verdict.Resolution
	Target   *MeteringTarget // the goal's metering target (§11), when one was committed with it
}

// Latest is the newest attempt.
func (r *RunState) Latest() *AttemptState {
	if len(r.Attempts) == 0 {
		return nil
	}
	return r.Attempts[len(r.Attempts)-1]
}

// Terminal reports whether the run reached delivered or delivery_failed.
func (r *RunState) Terminal() bool {
	a := r.Latest()
	if a == nil {
		return false
	}
	s := a.Current()
	return s == Delivered || s == DeliveryFailedS
}

// invocation finds an invocation state anywhere in the run.
func (r *RunState) invocation(id record.RecordID) (*invoke.State, uint32) {
	for _, a := range r.Attempts {
		for _, st := range a.Invocations {
			if st.Invocation.ID == id {
				return st, a.Attempt.Attempt
			}
		}
	}
	return nil, 0
}

// Ledger is the fold of the production population: every run, plus goals
// that were taken in but whose run never started (a crash after intake).
type Ledger struct {
	Runs      map[record.RunID]*RunState
	Unstarted []*Goal                               // in Seq order
	Families  map[record.RecordID]*FamilyAssessment // by goal; exactly one per goal
	Targets   map[record.RecordID]*MeteringTarget   // by goal; at most one per goal (§11)
	Learned   *learn.Ledger                         // the learned population, folded alongside
	// Interrupts by target run, with their acks; a pending one has no ack.
	Interrupts map[record.RunID][]*Interrupt
	Acks       map[record.RecordID]*InterruptAck
	// Forks by fork id; goals by id (a child goal may not have a run yet).
	Forks map[record.RecordID]*ForkState
	goals map[record.RecordID]*Goal
	// Arms: the experiment arm runs (replay arms and live-admitted goals)
	// by assignment id, then arm (one each).
	Arms map[record.RecordID]map[string]*RunState
}

// Goal returns a folded goal by id.
func (led *Ledger) Goal(id record.RecordID) *Goal { return led.goals[id] }

// Fold folds the production population into per-run state and REFUSES any
// history the driver could not have written: the journal door executes each
// record's own vocabulary; the fold executes the cross-record rules (an
// attempt that was never started, a second assessment for a goal, a
// delivery record scoped to a different owner, an ack without a start or
// with a foreign token, a `delivered` transition without the evidence it
// claims, a recorded outcome that names evidence from another run or a
// resolution that does not re-derive). A reader must not paper over these:
// the mission and the shared ledger are folds of exactly this.
func Fold(pr *journal.ProductionReader, store *thought.Store) (*Ledger, error) {
	pr = pr.Pin() // one prefix for every scan this fold composes
	goals := map[record.RecordID]*Goal{}
	var goalOrder []*Goal
	started := map[record.RecordID]bool{}
	fams := map[record.RecordID]*FamilyAssessment{}
	targets := map[record.RecordID]*MeteringTarget{}
	runs := map[record.RunID]*RunState{}
	deliveries := map[record.RecordID]*Delivery{}
	resolutions := map[record.RecordID]*verdict.Resolution{}
	verdicts := map[record.RecordID]*verdict.Verdict{}
	observations := map[record.RecordID]*verdict.Observation{}
	interrupts := map[record.RunID][]*Interrupt{}
	acks := map[record.RecordID]*InterruptAck{}
	forks := map[record.RecordID]*ForkState{}
	memberOf := map[record.RunID]*ForkState{}
	replays := map[record.RecordID]map[string]*RunState{}
	forkedGoals := map[record.RecordID]bool{}
	// invocation states are folded up front so transitions can be checked
	// against evidence in one pass
	inv, err := invoke.Fold(pr)
	if err != nil {
		return nil, err
	}
	learned, err := learn.Fold(pr)
	if err != nil {
		return nil, err
	}
	get := func(id record.RunID) *RunState {
		rs := runs[id]
		if rs == nil {
			rs = &RunState{Run: id}
			runs[id] = rs
		}
		return rs
	}
	attempt := func(rs *RunState, n uint32, what string) (*AttemptState, error) {
		if n == 0 || int(n) > len(rs.Attempts) {
			return nil, fmt.Errorf("run: %s for %s attempt %d, which was never started", what, rs.Run, n)
		}
		return rs.Attempts[n-1], nil
	}
	owned := func(d *Delivery, h *record.Header, what string) error {
		if h.RunID != d.Prepared.RunID || h.Attempt != d.Prepared.Attempt {
			return fmt.Errorf("run: %s for delivery %s is scoped to %s/%d but the delivery belongs to %s/%d", what, d.Prepared.ID, h.RunID, h.Attempt, d.Prepared.RunID, d.Prepared.Attempt)
		}
		return nil
	}
	err = pr.Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *Goal:
			if x.Origin == OriginReplay {
				unit := goals[x.Parent]
				if unit == nil || unit.Origin == OriginReplay || unit.Origin == OriginFork || x.Root != unit.Root {
					return fmt.Errorf("run: replay goal %s replays %s, which is not an earlier production unit of the same root", x.ID, x.Parent)
				}
			}
			goals[x.ID] = x
			goalOrder = append(goalOrder, x)
		case *FamilyAssessment:
			if goals[x.Goal] == nil {
				return fmt.Errorf("run: assessment %s for goal %s that was not committed first", x.ID, x.Goal)
			}
			if fams[x.Goal] != nil {
				return fmt.Errorf("run: goal %s assessed twice (%s, %s) — an assessment is never revised", x.Goal, fams[x.Goal].ID, x.ID)
			}
			fams[x.Goal] = x
		case *MeteringTarget:
			if goals[x.Goal] == nil {
				return fmt.Errorf("run: metering target %s for goal %s that was not committed first", x.ID, x.Goal)
			}
			if targets[x.Goal] != nil {
				return fmt.Errorf("run: goal %s targeted twice (%s, %s) — one envelope per goal", x.Goal, targets[x.Goal].ID, x.ID)
			}
			targets[x.Goal] = x
		case *Overage:
			rs := get(x.RunID)
			a, err := attempt(rs, x.Attempt, "overage")
			if err != nil {
				return err
			}
			rec := a.Has(Recorded)
			if rec == nil {
				return fmt.Errorf("run: %s attempt %d overage %s before the attempt is recorded", x.RunID, x.Attempt, x.ID)
			}
			if a.Overage != nil {
				return fmt.Errorf("run: %s attempt %d overage twice (%s, %s)", x.RunID, x.Attempt, a.Overage.ID, x.ID)
			}
			t := rs.Target
			if t == nil || x.Target != t.ID || x.Goal != rs.Goal.ID || x.Dimension != t.Dimension || x.Limit != t.Limit {
				return fmt.Errorf("run: %s attempt %d overage %s does not cite the goal's target", x.RunID, x.Attempt, x.ID)
			}
			if m := MeasuredOn(rec.Outcome.Usage, t.Dimension); x.Measured != m {
				return fmt.Errorf("run: %s attempt %d overage %s measures %s but the recorded usage is %s", x.RunID, x.Attempt, x.ID, num(x.Measured), num(m))
			}
			a.Overage = x
			a.touch(x)
		case *Fork:
			a, err := attempt(get(x.RunID), x.Attempt, "fork")
			if err != nil {
				return err
			}
			if a.Plan == nil || x.Step != len(a.Steps)+1 || a.Plan.ParallelAt(x.Step) == nil || len(a.Plan.ParallelAt(x.Step).Goals) != len(x.Members) {
				return fmt.Errorf("run: %s attempt %d fork %s at step %d, which is not its plan's next parallel step", x.RunID, x.Attempt, x.ID, x.Step)
			}
			for _, f := range forks {
				if f.Fork.RunID == x.RunID && f.Fork.Step == x.Step {
					return fmt.Errorf("run: %s attempt %d fork %s: step %d already forked (%s)", x.RunID, x.Attempt, x.ID, x.Step, f.Fork.ID)
				}
			}
			for i, gid := range x.Goals {
				g := goals[gid]
				if g == nil || g.Parent != get(x.RunID).Goal.ID || g.Origin != OriginFork || g.Lane != LaneNow || runs[x.Members[i].Run] != nil || memberOf[x.Members[i].Run] != nil || forkedGoals[gid] {
					return fmt.Errorf("run: %s attempt %d fork %s member %d: not a fresh child goal of this run", x.RunID, x.Attempt, x.ID, i)
				}
				started[gid], forkedGoals[gid] = true, true // the fork starts it; Unstarted must not list it
			}
			fs := &ForkState{Fork: x, Cancelled: map[record.RunID]*CancellationIssued{}, Terminals: map[record.RunID]*ChildTerminal{}}
			forks[x.ID] = fs
			for _, m := range x.Members {
				memberOf[m.Run] = fs
			}
			a.touch(x)
		case *JoinDecision:
			fs := forks[x.Fork]
			if fs == nil || fs.Decision != nil || fs.Fork.RunID != x.RunID {
				return fmt.Errorf("run: join decision %s for a fork that does not exist, is decided, or is not this run's", x.ID)
			}
			// a decision is a DERIVED record: it must equal the join rule over
			// the fork as it stood (terminals and closures at this point)
			want := decideWith(fs, func(r record.RunID) string {
				crs := runs[r]
				if crs == nil || crs.Latest() == nil {
					return ""
				}
				if rec := crs.Latest().Has(Recorded); rec != nil {
					if res := resolutions[rec.Outcome.Closure]; res != nil {
						return res.Outcome
					}
				}
				return ""
			})
			if want == nil || !sameDecision(want, x) {
				return fmt.Errorf("run: join decision %s does not re-derive from the join rule over the fork's state (recorded selected=%v cancel=%v; rule says %s)", x.ID, x.Selected, x.Cancel, describeDecision(want))
			}
			fs.Decision = x
			if a := attemptNoErr(runs, x.RunID, x.Attempt); a != nil {
				a.touch(x)
			}
		case *CancellationIssued:
			fs := forks[x.Fork]
			if fs == nil || fs.Decision == nil || fs.Fork.RunID != x.RunID || fs.Cancelled[x.Child.Run] != nil {
				return fmt.Errorf("run: cancellation %s without a decision, or repeated", x.ID)
			}
			named := false
			for _, c := range fs.Decision.Cancel {
				if c == x.Child {
					named = true
				}
			}
			if !named {
				return fmt.Errorf("run: cancellation %s of %s, which the decision did not cancel", x.ID, x.Child.Run)
			}
			if fs.Terminals[x.Child.Run] != nil {
				return fmt.Errorf("run: cancellation %s of %s, which is already terminal", x.ID, x.Child.Run)
			}
			fs.Cancelled[x.Child.Run] = x
		case *ChildTerminal:
			fs := forks[x.Fork]
			crs := runs[x.RunID]
			if fs == nil || crs == nil || !fs.isMember(x.RunID) || fs.Terminals[x.RunID] != nil {
				return fmt.Errorf("run: child terminal %s for a non-member, unknown, or already terminal child", x.ID)
			}
			a := crs.Latest()
			if a == nil || a.Attempt.Attempt != x.Attempt || a.Has(Recorded) == nil {
				return fmt.Errorf("run: child terminal %s from attempt %d, which is not the child's recorded attempt", x.ID, x.Attempt)
			}
			rec := a.Has(Recorded).Outcome
			switch x.State {
			case ChildCompleted, ChildCompletedLate:
				if rec.Terminal == invoke.TerminalFailed {
					return fmt.Errorf("run: child terminal %s says %s but the child's execution failed", x.ID, x.State)
				}
				if (x.State == ChildCompletedLate) != (fs.Decision != nil && !selected(fs.Decision, x.RunID)) {
					return fmt.Errorf("run: child terminal %s: completed_late is exactly a completion after a decision that did not select it", x.ID)
				}
			case ChildCancelled:
				if fs.Cancelled[x.RunID] == nil || !strings.Contains(rec.Reason, "cancelled by join") {
					return fmt.Errorf("run: child terminal %s says cancelled without a cancellation the child consumed", x.ID)
				}
			case ChildFailed:
				if rec.Terminal != invoke.TerminalFailed || strings.Contains(rec.Reason, "cancelled by join") {
					return fmt.Errorf("run: child terminal %s says failed but the child did not", x.ID)
				}
			}
			fs.Terminals[x.RunID] = x
			a.touch(x)
		case *JoinSettled:
			fs := forks[x.Fork]
			if fs == nil || fs.Decision == nil || fs.Settled != nil || fs.Fork.RunID != x.RunID || !fs.Complete() {
				return fmt.Errorf("run: join settled %s before every member is terminal (or repeated)", x.ID)
			}
			fs.Settled = x
			if a := attemptNoErr(runs, x.RunID, x.Attempt); a != nil {
				a.touch(x)
			}
		case *Interrupt:
			if runs[x.Target] == nil {
				return fmt.Errorf("run: interrupt %s targets unknown run %s", x.ID, x.Target)
			}
			interrupts[x.Target] = append(interrupts[x.Target], x)
		case *InterruptAck:
			var it *Interrupt
			for _, list := range interrupts {
				for _, i := range list {
					if i.ID == x.Interrupt {
						it = i
					}
				}
			}
			if it == nil || acks[x.Interrupt] != nil {
				return fmt.Errorf("run: ack %s for an unknown or already acknowledged interrupt %s", x.ID, x.Interrupt)
			}
			target := runs[it.Target]
			switch x.Result {
			case "consumed":
				a := attemptNoErr(runs, x.RunID, x.Attempt)
				if a == nil || x.RunID != it.Target || a.Current() != Executing {
					return fmt.Errorf("run: ack %s consumed outside an executing attempt of its target", x.ID)
				}
				// the boundary must be the one the attempt was AT: NOW before its
				// execute; AGENDA before the next undone step of its plan
				if !boundaryPossible(a, x.Boundary) {
					return fmt.Errorf("run: ack %s consumed at %q, a boundary attempt %d was not at", x.ID, x.Boundary, x.Attempt)
				}
				a.touch(x)
			case "expired":
				// expired only when the execution is settled: the target's
				// latest attempt is recorded or beyond
				if target.Latest() == nil {
					return fmt.Errorf("run: ack %s expired an interrupt of a run that never started", x.ID)
				}
				switch target.Latest().Current() {
				case Recorded, Delivered, DeliveryFailedS:
				default:
					return fmt.Errorf("run: ack %s expired an interrupt while its target is at %s", x.ID, target.Latest().Current())
				}
			}
			acks[x.Interrupt] = x
		case *verdict.Verdict:
			verdicts[x.ID] = x
			if x.RunID != "" {
				if a := attemptNoErr(runs, x.RunID, x.Attempt); a != nil {
					if err := checkJudgeVerdict(runs[x.RunID], a, x, inv, learned, store, forks, runs); err != nil {
						return err
					}
					a.Verdicts = append(a.Verdicts, x)
					a.touch(x)
				}
			}
		case *verdict.Observation:
			observations[x.ID] = x
		case *verdict.Resolution:
			// every resolution is a derived record: it must re-derive from
			// the candidates it names, whoever wrote it
			if err := verdict.Check(x, verdicts, observations); err != nil {
				return err
			}
			resolutions[x.ID] = x
			if x.VerdictKind == verdict.KindStuck && x.Outcome == "stuck" && x.RunID != "" {
				// only a deterministic (the sheriff's) or operator stuck verdict
				// counts as "called stuck"; a self or judge opinion is evidence,
				// not the sheriff's decision, and must not silence it
				if a := attemptNoErr(runs, x.RunID, x.Attempt); a != nil && x.Effective != "" && x.Subject == runRef(x.RunID) {
					if eff := verdicts[x.Effective]; eff != nil && (eff.Source.Standing == verdict.StandingDeterministic || eff.Source.Standing == verdict.StandingOperator) && len(eff.Basis) > 0 {
						a.Stuck = x
					}
				}
			}
		case *invoke.Invocation:
			// invocations are folded up front (receipts, terminals, reconciliation);
			// attach each to the attempt it ran under as its record arrives
			st := inv[x.ID]
			rs := runs[x.RunID]
			if st != nil && rs != nil && x.Attempt > 0 && int(x.Attempt) <= len(rs.Attempts) {
				a := rs.Attempts[x.Attempt-1]
				// the lens rule executes as the invocation arrives (§13):
				// a lensed request cites the attempt's lens binding exactly
				if err := checkLens(rs, a, st, store); err != nil {
					return err
				}
				a.Invocations = append(a.Invocations, st)
			}
		case *RunAttempt:
			rs := get(x.RunID)
			started[x.Goal] = true
			if int(x.Attempt) != len(rs.Attempts)+1 {
				return fmt.Errorf("run: %s attempt %d started but %d attempts exist", x.RunID, x.Attempt, len(rs.Attempts))
			}
			if rs.Goal == nil {
				rs.Goal, rs.Family, rs.Target = goals[x.Goal], fams[x.Goal], targets[x.Goal]
				if rs.Goal == nil || rs.Family == nil || rs.Family.ID != x.Family {
					return fmt.Errorf("run: %s attempt %d cites goal %s / family %s that were not committed first", x.RunID, x.Attempt, x.Goal, x.Family)
				}
			} else if rs.Goal.ID != x.Goal || rs.Family.ID != x.Family {
				return fmt.Errorf("run: %s attempt %d cites a different goal or assessment than attempt 1", x.RunID, x.Attempt)
			}
			if x.Config.Lane != rs.Goal.Lane {
				return fmt.Errorf("run: %s attempt %d ran lane %s but the goal is routed to %s", x.RunID, x.Attempt, x.Config.Lane, rs.Goal.Lane)
			}
			if rs.Goal.Origin == OriginFork {
				fs := memberOf[x.RunID]
				if fs == nil || !x.Config.Confined {
					return fmt.Errorf("run: %s attempt %d: a fork child must be a fork's member and run confined", x.RunID, x.Attempt)
				}
			} else if x.Config.Confined {
				return fmt.Errorf("run: %s attempt %d ran confined but is not a fork child", x.RunID, x.Attempt)
			}
			if x.Attempt > 1 && rs.Attempts[x.Attempt-2].Current() != Recoverable {
				return fmt.Errorf("run: %s attempt %d started but attempt %d is at %q, not recoverable", x.RunID, x.Attempt, x.Attempt-1, rs.Attempts[x.Attempt-2].Current())
			}
			// the policy boundary: the attempt's config carries exactly the
			// policy selection committed with it (same command), and the
			// judge backend follows the snapshot
			pol := learned.Policies[learn.PolicyKey(x.RunID, x.Attempt)]
			if pol == nil || pol.ID != x.Config.Policy {
				return fmt.Errorf("run: %s attempt %d names policy selection %s, which is not the attempt's", x.RunID, x.Attempt, x.Config.Policy)
			}
			if len(pol.Snapshot) != len(x.Config.Mechanisms) {
				return fmt.Errorf("run: %s attempt %d config mechanisms disagree with its policy selection", x.RunID, x.Attempt)
			}
			for m, on := range pol.Snapshot {
				if x.Config.Mechanisms[m] != on {
					return fmt.Errorf("run: %s attempt %d config says %s=%v, its policy selection says %v", x.RunID, x.Attempt, m, x.Config.Mechanisms[m], on)
				}
			}
			// an arm's selections carry its assignment; a production run's carry none
			if err := armMatches(rs.Goal, pol.Arm); err != nil {
				return fmt.Errorf("run: %s attempt %d policy selection: %w", x.RunID, x.Attempt, err)
			}
			if rec := learned.Recalls[learn.RecallKey(x.RunID, x.Attempt)]; rec != nil {
				if err := armMatches(rs.Goal, rec.Arm); err != nil {
					return fmt.Errorf("run: %s attempt %d recall selection: %w", x.RunID, x.Attempt, err)
				}
			}
			if x.Attempt == 1 && rs.Goal.Arm != nil {
				arms := replays[rs.Goal.Arm.Assignment]
				if arms == nil {
					arms = map[string]*RunState{}
					replays[rs.Goal.Arm.Assignment] = arms
				}
				if arms[rs.Goal.Arm.Arm] != nil {
					return fmt.Errorf("run: %s is a second run of assignment %s arm %s", x.RunID, rs.Goal.Arm.Assignment, rs.Goal.Arm.Arm)
				}
				arms[rs.Goal.Arm.Arm] = rs
			}
			a := &AttemptState{Attempt: x, Recall: learned.Recalls[learn.RecallKey(x.RunID, x.Attempt)], Policy: pol}
			if x.Attempt > 1 {
				// a recovered attempt starts from the last committed idempotent
				// stage: the earlier attempt's intent, plan, and done steps are
				// this attempt's too (they are the run's evidence, not the
				// attempt's), and it may not remake them
				p := rs.Attempts[x.Attempt-2]
				a.Intent, a.Plan, a.Steps = p.Intent, p.Plan, append([]*StepDone{}, p.Steps...)
			}
			rs.Attempts = append(rs.Attempts, a)
		case *Transition:
			rs := get(x.RunID)
			a, err := attempt(rs, x.Attempt, "transition")
			if err != nil {
				return err
			}
			if cur := a.Current(); cur != x.From {
				return fmt.Errorf("run: %s attempt %d transition %s→%s but the attempt is at %q", x.RunID, x.Attempt, x.From, x.To, cur)
			}
			if err := checkTransition(rs, a, x, inv, learned, store, resolutions, verdicts, observations); err != nil {
				return err
			}
			a.Transitions = append(a.Transitions, x)
		case *IntentAssessment:
			a, err := attempt(get(x.RunID), x.Attempt, "intent")
			if err != nil {
				return err
			}
			if a.Intent != nil || a.Attempt.Config.Lane != LaneAgenda || a.Current() != Executing {
				return fmt.Errorf("run: %s attempt %d intent out of place (lane %s, state %s, prior intent %v)", x.RunID, x.Attempt, a.Attempt.Config.Lane, a.Current(), a.Intent != nil)
			}
			st := inv[x.Invocation]
			if st == nil || st.Invocation.RunID != x.RunID || st.Invocation.Attempt > x.Attempt || st.Invocation.Purpose != invoke.PurposeIntent || st.Receipt == nil {
				return fmt.Errorf("run: %s attempt %d intent cites invocation %s that is not its intent call", x.RunID, x.Attempt, x.Invocation)
			}
			// the interpretation boundary, re-executed on read: the record
			// must be what the cited response parses to, and the request
			// must be the intent prompt over this goal
			goal, err := store.Get(get(x.RunID).Goal.Text)
			if err != nil {
				return err
			}
			if st.Invocation.Request != thought.Address(thought.Prompt, intentPrompt(goal)) {
				return fmt.Errorf("run: %s attempt %d intent invocation %s was not asked the intent prompt", x.RunID, x.Attempt, x.Invocation)
			}
			resp, err := store.Get(st.Receipt.Response)
			if err != nil {
				return err
			}
			ir, perr := ParseIntent(resp)
			if perr != nil || ir.Clear != x.Clear || ir.Interpretation != x.Interpretation || ir.Question != x.Question {
				return fmt.Errorf("run: %s attempt %d intent record does not re-derive from response %s (%v)", x.RunID, x.Attempt, st.Receipt.Response.Hash, perr)
			}
			a.Intent = x
			a.touch(x)
		case *Plan:
			a, err := attempt(get(x.RunID), x.Attempt, "plan")
			if err != nil {
				return err
			}
			if a.Plan != nil || a.Intent == nil || !a.Intent.Clear {
				return fmt.Errorf("run: %s attempt %d plan without a clear intent before it (or a second plan)", x.RunID, x.Attempt)
			}
			st := inv[x.Invocation]
			if st == nil || st.Invocation.RunID != x.RunID || st.Invocation.Attempt > x.Attempt || st.Invocation.Purpose != invoke.PurposePlan || st.Receipt == nil {
				return fmt.Errorf("run: %s attempt %d plan cites invocation %s that is not its plan call", x.RunID, x.Attempt, x.Invocation)
			}
			resp, err := store.Get(st.Receipt.Response)
			if err != nil {
				return err
			}
			planned, perr := ParsePlan(resp)
			if perr != nil || len(planned) != len(x.Steps) {
				return fmt.Errorf("run: %s attempt %d plan does not re-derive from response %s (%v)", x.RunID, x.Attempt, st.Receipt.Response.Hash, perr)
			}
			parallels := 0
			for i, ps := range planned {
				if x.Steps[i] != thought.Address(thought.Step, []byte(ps.Text)) {
					return fmt.Errorf("run: %s attempt %d plan step %d is not the response's step", x.RunID, x.Attempt, i+1)
				}
				par := x.ParallelAt(i + 1)
				if (len(ps.Parallel) > 0) != (par != nil) {
					return fmt.Errorf("run: %s attempt %d plan step %d: parallel in one of record and response only", x.RunID, x.Attempt, i+1)
				}
				if par != nil {
					parallels++
					if par.Policy != ps.Policy || len(par.Goals) != len(ps.Parallel) {
						return fmt.Errorf("run: %s attempt %d plan step %d: parallel spec is not the response's", x.RunID, x.Attempt, i+1)
					}
					for j, g := range ps.Parallel {
						if par.Goals[j] != thought.Address(thought.Step, []byte(g)) {
							return fmt.Errorf("run: %s attempt %d plan step %d sub-goal %d is not the response's", x.RunID, x.Attempt, i+1, j+1)
						}
					}
				}
			}
			if parallels != len(x.Parallel) {
				return fmt.Errorf("run: %s attempt %d plan carries parallel steps the response does not", x.RunID, x.Attempt)
			}
			a.Plan = x
			a.touch(x)
		case *StepDone:
			a, err := attempt(get(x.RunID), x.Attempt, "step")
			if err != nil {
				return err
			}
			if a.Plan == nil || x.Ordinal != len(a.Steps)+1 || x.Ordinal > len(a.Plan.Steps) || x.Step != a.Plan.Steps[x.Ordinal-1] {
				return fmt.Errorf("run: %s attempt %d step %d out of order or not the plan's step", x.RunID, x.Attempt, x.Ordinal)
			}
			if len(a.Steps) > 0 && a.Steps[len(a.Steps)-1].Outcome == StepBlocked {
				return fmt.Errorf("run: %s attempt %d step %d after a blocked step", x.RunID, x.Attempt, x.Ordinal)
			}
			if x.Fork != "" {
				// a fork step: its result is the composition of the settled
				// fork's selected members, re-derived
				fs := forks[x.Fork]
				if fs == nil || fs.Fork.RunID != x.RunID || fs.Fork.Step != x.Ordinal || fs.Settled == nil || a.Plan.ParallelAt(x.Ordinal) == nil {
					return fmt.Errorf("run: %s attempt %d step %d cites fork %s that is not its settled fork", x.RunID, x.Attempt, x.Ordinal, x.Fork)
				}
				composed, err := composeFork(fs, &Ledger{Runs: runs}, store)
				if err != nil {
					return err
				}
				if x.Result != thought.Address(thought.Response, composed) || x.Terminal != invoke.TerminalComplete {
					return fmt.Errorf("run: %s attempt %d step %d result is not the fork's composition", x.RunID, x.Attempt, x.Ordinal)
				}
			} else {
				st := inv[x.Invocation]
				if st == nil || st.Invocation.RunID != x.RunID || st.Invocation.Attempt > x.Attempt || st.Invocation.Purpose != invoke.PurposeExecute || st.Receipt == nil || st.Receipt.Response != x.Result || st.Terminal == nil || st.Terminal.State != x.Terminal {
					return fmt.Errorf("run: %s attempt %d step %d cites invocation %s that is not an execute call with this result and terminal", x.RunID, x.Attempt, x.Ordinal, x.Invocation)
				}
				if a.Plan.ParallelAt(x.Ordinal) != nil {
					return fmt.Errorf("run: %s attempt %d step %d is a parallel step but was executed", x.RunID, x.Attempt, x.Ordinal)
				}
				// the execute request must be the step prompt over this goal,
				// plan, and prior results, with the recall block the attempt's
				// selection renders — the interpretation of "which step ran"
				// is re-derived, never trusted
				want, err := stepRequest(get(x.RunID), a, x.Ordinal, learned, store)
				if err != nil {
					return err
				}
				if st.Invocation.Request != want {
					return fmt.Errorf("run: %s attempt %d step %d invocation %s was not asked step %d's prompt", x.RunID, x.Attempt, x.Ordinal, x.Invocation, x.Ordinal)
				}
			}
			if x.Verdict != "" {
				v := verdicts[x.Verdict]
				if v == nil || v.VerdictKind != verdict.KindStep || string(v.Outcome) != string(x.Outcome) || v.RunID != x.RunID || v.Attempt > x.Attempt || v.Subject != stepRef(x.RunID, v.Attempt, x.Ordinal) {
					return fmt.Errorf("run: %s attempt %d step %d cites verdict %s that does not judge it as %s", x.RunID, x.Attempt, x.Ordinal, x.Verdict, x.Outcome)
				}
			}
			a.Steps = append(a.Steps, x)
			a.touch(x)
		case *DeliveryPrepared:
			rs := get(x.RunID)
			a, err := attempt(rs, x.Attempt, "delivery")
			if err != nil {
				return err
			}
			if a.Delivery != nil {
				return fmt.Errorf("run: %s attempt %d prepared two deliveries", x.RunID, x.Attempt)
			}
			if t, rec := rs.Target, a.Has(Recorded); t != nil && rec != nil && a.Overage == nil && MeasuredOn(rec.Outcome.Usage, t.Dimension) > t.Limit {
				// the overage is an event the delivery carries: a delivery
				// prepared over target without it is refused (D13)
				return fmt.Errorf("run: %s attempt %d prepared a delivery over its target without the overage committed", x.RunID, x.Attempt)
			}
			if a.Has(Recorded) == nil {
				return fmt.Errorf("run: %s attempt %d prepared a delivery before recorded", x.RunID, x.Attempt)
			}
			if x.Origin != rs.Goal.Origin || x.Required != rs.Goal.Delivery.Required {
				return fmt.Errorf("run: delivery %s (origin %s, required %s) disagrees with the goal (%s, %s)", x.ID, x.Origin, x.Required, rs.Goal.Origin, rs.Goal.Delivery.Required)
			}
			a.Delivery = &Delivery{Prepared: x}
			deliveries[x.ID] = a.Delivery
			a.touch(x)
		case *DeliveryStarted:
			d := deliveries[x.Delivery]
			if d == nil {
				return fmt.Errorf("run: start on unknown delivery %s", x.Delivery)
			}
			if err := owned(d, &x.Header, "start"); err != nil {
				return err
			}
			if d.Accepted() {
				return fmt.Errorf("run: delivery %s started again after acceptance", x.Delivery)
			}
			if x.N != len(d.Started)+1 || d.Pending() {
				return fmt.Errorf("run: delivery %s start %d out of order (%d started, %d resolved)", x.Delivery, x.N, len(d.Started), len(d.Attempts))
			}
			d.Started = append(d.Started, x)
		case *DeliveryAttempted:
			d := deliveries[x.Delivery]
			if d == nil {
				return fmt.Errorf("run: attempt on unknown delivery %s", x.Delivery)
			}
			if err := owned(d, &x.Header, "attempt"); err != nil {
				return err
			}
			if x.N != len(d.Attempts)+1 || !d.Pending() {
				return fmt.Errorf("run: delivery %s attempt %d without its start (%d started, %d resolved)", x.Delivery, x.N, len(d.Started), len(d.Attempts))
			}
			if d.Accepted() {
				return fmt.Errorf("run: delivery %s attempted again after acceptance", x.Delivery)
			}
			d.Attempts = append(d.Attempts, x)
			if a := attemptNoErr(runs, x.RunID, x.Attempt); a != nil {
				a.touch(x)
			}
		case *DeliveryAcked:
			d := deliveries[x.Delivery]
			if d == nil {
				return fmt.Errorf("run: ack on unknown delivery %s", x.Delivery)
			}
			if d.Ack != nil {
				return fmt.Errorf("run: delivery %s acked twice", x.Delivery)
			}
			if err := d.checkAck(x); err != nil {
				return fmt.Errorf("run: ack %s on delivery %s: %w", x.ID, x.Delivery, err)
			}
			d.Ack = x
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	led := &Ledger{Runs: runs, Families: fams, Targets: targets, Learned: learned, Interrupts: interrupts, Acks: acks, Forks: forks, goals: goals, Arms: replays}
	for _, g := range goalOrder {
		if !started[g.ID] {
			led.Unstarted = append(led.Unstarted, g)
		}
	}
	for id, rs := range runs {
		if rs.Goal == nil {
			return nil, fmt.Errorf("run: %s has records but no attempt", id)
		}
		for _, a := range rs.Attempts {
			if t := a.Has(Recorded); t != nil {
				rs.Closure = resolutions[t.Outcome.Closure]
			}
		}
	}
	return led, nil
}

// boundaryPossible says whether an attempt at `executing` could have been
// at the named boundary: NOW has one (before its execute, which must not
// have happened yet); AGENDA has one per undone step, in order.
func boundaryPossible(a *AttemptState, boundary string) bool {
	switch a.Attempt.Config.Lane {
	case LaneNow:
		return boundary == "before_execute" && !invoked(a)
	case LaneAgenda:
		if a.Plan == nil {
			return false
		}
		var k int
		if _, err := fmt.Sscanf(boundary, "before_step_%d", &k); err != nil {
			return false
		}
		if len(a.Steps) > 0 && a.Steps[len(a.Steps)-1].Outcome == StepBlocked {
			return false
		}
		return k == len(a.Steps)+1 && k <= len(a.Plan.Steps)
	}
	return false
}

// goal returns a goal record by id (a child goal may have no run yet).
func (led *Ledger) goal(id record.RecordID) *Goal { return led.goals[id] }

// forkAt returns the fork a run's latest attempt made at step k, if any.
func (led *Ledger) forkAt(run record.RunID, k int) *ForkState {
	for _, fs := range led.Forks {
		if fs.Fork.RunID == run && fs.Fork.Step == k {
			return fs
		}
	}
	return nil
}

// attemptNoErr finds an attempt or nil.
func attemptNoErr(runs map[record.RunID]*RunState, run record.RunID, n uint32) *AttemptState {
	rs := runs[run]
	if rs == nil || n == 0 || int(n) > len(rs.Attempts) {
		return nil
	}
	return rs.Attempts[n-1]
}

// stepRef is the subject of a step verdict.
func stepRef(run record.RunID, attempt uint32, ordinal int) record.Ref {
	return record.Ref{Kind: "step", ID: fmt.Sprintf("%s/%d/%d", run, attempt, ordinal)}
}

// planTexts and results read an attempt's plan and done results whole.
func planTexts(a *AttemptState, store *thought.Store) ([]string, [][]byte, []bool, error) {
	steps := make([]string, len(a.Plan.Steps))
	for i, ref := range a.Plan.Steps {
		b, err := store.Get(ref)
		if err != nil {
			return nil, nil, nil, err
		}
		steps[i] = string(b)
	}
	var results [][]byte
	var partial []bool
	for _, sd := range a.Steps {
		b, err := store.Get(sd.Result)
		if err != nil {
			return nil, nil, nil, err
		}
		results = append(results, b)
		partial = append(partial, sd.Terminal == invoke.TerminalPartial)
	}
	return steps, results, partial, nil
}

// recallBlock renders the attempt's selection (continued or own).
func recallBlock(rs *RunState, a *AttemptState, learned *learn.Ledger, store *thought.Store) ([]byte, error) {
	if a.Recall == nil {
		return nil, fmt.Errorf("run: %s attempt %d has no recall selection", rs.Run, a.Attempt.Attempt)
	}
	block, _, err := learn.Render(a.Recall, func(ir learn.ItemRev) ([]byte, error) {
		it := learned.Items[ir.Item]
		if it == nil {
			return nil, fmt.Errorf("recall names unknown item %s", ir.Item)
		}
		for _, r := range it.Revisions {
			if r.ID == ir.Revision {
				return store.Get(r.Text)
			}
		}
		return nil, fmt.Errorf("recall names unknown revision %s", ir.Revision)
	})
	return block, err
}

// stepRequest re-derives the execute request for step k of the attempt.
func stepRequest(rs *RunState, a *AttemptState, k int, learned *learn.Ledger, store *thought.Store) (thought.Ref, error) {
	goal, err := store.Get(rs.Goal.Text)
	if err != nil {
		return thought.Ref{}, err
	}
	steps, results, _, err := planTexts(a, store)
	if err != nil {
		return thought.Ref{}, err
	}
	if k-1 > len(results) {
		return thought.Ref{}, fmt.Errorf("run: step %d before its predecessors", k)
	}
	block, err := recallBlock(rs, a, learned, store)
	if err != nil {
		return thought.Ref{}, err
	}
	return thought.Address(thought.Prompt, stepPrompt(goal, steps, k, results[:k-1], block)), nil
}

// checkJudgeVerdict re-executes the judge boundary for a judge-standing
// verdict of the run: its invocation is a tool-less judge call of the run
// whose request is the judge prompt for its subject and whose response
// parses to the verdict's outcome and confidence, with the receipt as basis.
func checkJudgeVerdict(rs *RunState, a *AttemptState, v *verdict.Verdict, inv map[record.RecordID]*invoke.State, learned *learn.Ledger, store *thought.Store, forks map[record.RecordID]*ForkState, runs map[record.RunID]*RunState) error {
	if v.Source.Standing != verdict.StandingJudge {
		return nil
	}
	st := inv[v.Source.Ref]
	if st == nil || st.Invocation.RunID != rs.Run || st.Invocation.Purpose != invoke.PurposeJudge || st.Receipt == nil {
		return fmt.Errorf("run: %s attempt %d judge verdict %s cites %s, which is not a judge call of the run with a receipt", rs.Run, v.Attempt, v.ID, v.Source.Ref)
	}
	if len(v.Basis) != 1 || v.Basis[0].ID != string(st.Receipt.ID) {
		return fmt.Errorf("run: %s attempt %d judge verdict %s does not cite its receipt as basis", rs.Run, v.Attempt, v.ID)
	}
	goal, err := store.Get(rs.Goal.Text)
	if err != nil {
		return err
	}
	resp, err := store.Get(st.Receipt.Response)
	if err != nil {
		return err
	}
	var want []byte
	var allowed []string
	switch v.VerdictKind {
	case verdict.KindStep:
		var n uint32
		var k int
		var runID string
		if _, err := fmt.Sscanf(v.Subject.ID, "%26s/%d/%d", &runID, &n, &k); err != nil || v.Subject.Kind != "step" || runID != string(rs.Run) || a.Plan == nil || k < 1 || k > len(a.Plan.Steps) {
			return fmt.Errorf("run: %s attempt %d step verdict %s has subject %v that is not a step of its plan", rs.Run, v.Attempt, v.ID, v.Subject)
		}
		steps, results, partial, err := planTexts(a, store)
		if err != nil {
			return err
		}
		// the judged result is step k's: committed as a StepDone already, or
		// (the verdict lands before its StepDone) the receipt of the execute
		// invocation whose request re-derives as step k's prompt
		var judged []byte
		var term invoke.TerminalState = invoke.TerminalComplete
		if k-1 < len(results) {
			judged = results[k-1]
			if partial[k-1] {
				term = invoke.TerminalPartial
			}
		} else if a.Plan.ParallelAt(k) != nil {
			var fs *ForkState
			for _, f := range forks {
				if f.Fork.RunID == rs.Run && f.Fork.Step == k {
					fs = f
				}
			}
			if fs == nil || fs.Settled == nil {
				return fmt.Errorf("run: %s attempt %d step verdict %s judges parallel step %d before its fork settled", rs.Run, v.Attempt, v.ID, k)
			}
			b, err := composeFork(fs, &Ledger{Runs: runs}, store)
			if err != nil {
				return err
			}
			judged = b
		} else {
			wantReq, err := stepRequest(rs, a, k, learned, store)
			if err != nil {
				return err
			}
			found := false
			for _, p := range rs.Attempts {
				for _, es := range p.Invocations {
					if es.Invocation.Purpose == invoke.PurposeExecute && es.Receipt != nil && es.Invocation.Request == wantReq {
						b, err := store.Get(es.Receipt.Response)
						if err != nil {
							return err
						}
						judged, term, found = b, es.Terminal.State, true
					}
				}
			}
			if !found {
				return fmt.Errorf("run: %s attempt %d step verdict %s judges step %d before any execute of it", rs.Run, v.Attempt, v.ID, k)
			}
		}
		want, allowed = stepJudgePrompt(goal, steps[k-1], judged, term, a.Plan.ParallelAt(k) != nil), []string{"done", "blocked", "unclear"}
	case verdict.KindClosure:
		allowed = []string{"achieved", "not_achieved", "unknown"}
		if a.Plan != nil {
			steps, results, partial, err := planTexts(a, store)
			if err != nil {
				return err
			}
			want = closurePrompt(goal, steps, results, partial)
		} else {
			// a NOW run with the model judge: the goal is its own one step and
			// the judged result is the newest execute receipt of the run
			var judged []byte
			var term invoke.TerminalState
			found := false
			for _, p := range rs.Attempts {
				if p.Attempt.Attempt > a.Attempt.Attempt {
					break
				}
				for _, es := range p.Invocations {
					if es.Invocation.Purpose == invoke.PurposeExecute && es.Receipt != nil {
						b, err := store.Get(es.Receipt.Response)
						if err != nil {
							return err
						}
						judged, term, found = b, es.Terminal.State, true
					}
				}
			}
			if !found {
				return fmt.Errorf("run: %s attempt %d closure verdict %s before any execute receipt", rs.Run, v.Attempt, v.ID)
			}
			want = closurePrompt(goal, []string{string(goal)}, [][]byte{judged}, []bool{term == invoke.TerminalPartial})
		}
	default:
		return nil
	}
	if lt := a.Attempt.Config.LensText; lt != nil {
		// the attempt judges under a lens: the prompt is the CONFIGURED lens
		// text over the same facts (§13) — the binding, not whatever the
		// invocation cites (checkLens holds them equal)
		lb, err := store.Get(*lt)
		if err != nil {
			return fmt.Errorf("run: %s attempt %d judge verdict %s: lens text: %w", rs.Run, v.Attempt, v.ID, err)
		}
		want = Lensed(lb, want)
	}
	if st.Invocation.Request != thought.Address(thought.Prompt, want) {
		return fmt.Errorf("run: %s attempt %d judge verdict %s: invocation %s was not asked this judgement's prompt", rs.Run, v.Attempt, v.ID, v.Source.Ref)
	}
	jr, perr := ParseJudge(resp, allowed...)
	if perr != nil || jr.Outcome != v.Outcome || jr.Confidence != v.Confidence {
		return fmt.Errorf("run: %s attempt %d judge verdict %s does not re-derive from response %s (%v)", rs.Run, v.Attempt, v.ID, st.Receipt.Response.Hash, perr)
	}
	return nil
}

// checkTransition executes the cross-record rules a transition claims.
func checkTransition(rs *RunState, a *AttemptState, x *Transition, inv map[record.RecordID]*invoke.State, learned *learn.Ledger, store *thought.Store, resolutions map[record.RecordID]*verdict.Resolution, verdicts map[record.RecordID]*verdict.Verdict, observations map[record.RecordID]*verdict.Observation) error {
	switch x.To {
	case Recorded:
		o := x.Outcome
		if o.Lane != a.Attempt.Config.Lane {
			return fmt.Errorf("run: %s attempt %d recorded lane %s but ran %s", rs.Run, x.Attempt, o.Lane, a.Attempt.Config.Lane)
		}
		if o.GoalText != rs.Goal.Text {
			return fmt.Errorf("run: %s attempt %d recorded an outcome for a different goal thought", rs.Run, x.Attempt)
		}
		if err := checkLenses(rs, a, store); err != nil {
			return err
		}
		if o.Invocation != "" {
			st := inv[o.Invocation]
			if st == nil || st.Invocation.RunID != rs.Run || st.Invocation.Attempt != o.Produced || o.Produced > x.Attempt {
				return fmt.Errorf("run: %s attempt %d recorded invocation %s that this run's attempt %d did not make", rs.Run, x.Attempt, o.Invocation, o.Produced)
			}
			if st.Invocation.Backend.Model != o.Model {
				return fmt.Errorf("run: %s attempt %d recorded model %q but the invocation ran %q", rs.Run, x.Attempt, o.Model, st.Invocation.Backend.Model)
			}
			if a.Attempt.Config.Confined {
				for _, p := range rs.Attempts {
					for _, is := range p.Invocations {
						if is.Invocation.Tools {
							return fmt.Errorf("run: %s attempt %d is confined but invocation %s offered tools", rs.Run, x.Attempt, is.Invocation.ID)
						}
					}
				}
			}
			if o.Receipt != "" {
				if st.Receipt == nil || st.Receipt.ID != o.Receipt || o.Response == nil || *o.Response != st.Receipt.Response {
					return fmt.Errorf("run: %s attempt %d recorded receipt %s that disagrees with invocation %s", rs.Run, x.Attempt, o.Receipt, o.Invocation)
				}
			}
			agenda := a.Attempt.Config.Lane == LaneAgenda
			if agenda {
				// usage is what the goal cost so far: every receipt of every
				// attempt — except the tail's diagnose calls, which are the
				// tail's cost, land after the outcome is recorded, and must
				// not change it
				var sum invoke.Usage
				for _, p := range rs.Attempts {
					for _, is := range p.Invocations {
						if is.Receipt != nil && is.Invocation.Purpose != invoke.PurposeDiagnose && is.Invocation.Purpose != invoke.PurposeEvaluate {
							sum = add(sum, is.Receipt.Usage)
						}
					}
				}
				if o.Usage != sum || o.Steps != len(a.Steps) {
					return fmt.Errorf("run: %s attempt %d recorded usage/steps that are not the sum of its receipts (%d steps)", rs.Run, x.Attempt, len(a.Steps))
				}
			} else if o.Receipt != "" && o.Usage != st.Receipt.Usage {
				return fmt.Errorf("run: %s attempt %d recorded usage that disagrees with receipt %s", rs.Run, x.Attempt, o.Receipt)
			}
			// exposure: the recall the request was rendered from is the
			// producing attempt's; NOW: the request is EXACTLY frame+goal+rendering;
			// AGENDA: every plan/execute request ENDS with the rendering and
			// carries the applications; judge/intent requests carry neither
			recallBy := o.Produced
			if agenda {
				recallBy = x.Attempt // AGENDA: the recording attempt's selection (continued from the recovered one)
			}
			sel := learned.Recalls[learn.RecallKey(rs.Run, recallBy)]
			if sel == nil || sel.ID != o.Recall {
				return fmt.Errorf("run: %s attempt %d recorded recall %s that attempt %d did not make", rs.Run, x.Attempt, o.Recall, recallBy)
			}
			if agenda {
				for _, p := range rs.Attempts {
					if p.Recall == nil || (p.Recall.ID != o.Recall && !continuesTo(learned, p.Recall, o.Recall)) {
						continue // an attempt whose requests were rendered from another decision (or none)
					}
					for _, is := range p.Invocations {
						if err := checkExposure(rs, sel, store.Get, learned, store.Get, is.Invocation.ID, is.Invocation, is.Invocation.Attempt, true); err != nil {
							return fmt.Errorf("run: %s attempt %d: %w", rs.Run, x.Attempt, err)
						}
					}
				}
			} else if err := checkExposure(rs, sel, store.Get, learned, store.Get, o.Invocation, st.Invocation, o.Produced, false); err != nil {
				return fmt.Errorf("run: %s attempt %d: %w", rs.Run, x.Attempt, err)
			}
		} else {
			if o.Model != "" {
				return fmt.Errorf("run: %s attempt %d recorded a model with no invocation", rs.Run, x.Attempt)
			}
			// a refusal after recall names this attempt's own selection
			if o.Recall != "" {
				sel := learned.Recalls[learn.RecallKey(rs.Run, x.Attempt)]
				if sel == nil || sel.ID != o.Recall {
					return fmt.Errorf("run: %s attempt %d recorded recall %s that it did not make", rs.Run, x.Attempt, o.Recall)
				}
			}
		}
		res := resolutions[o.Closure]
		if res == nil || res.RunID != rs.Run || res.Attempt != x.Attempt || res.VerdictKind != verdict.KindClosure || res.Subject != runRef(rs.Run) {
			return fmt.Errorf("run: %s attempt %d recorded closure %s that is not this attempt's closure resolution", rs.Run, x.Attempt, o.Closure)
		}
		if err := verdict.Check(res, verdicts, observations); err != nil {
			return fmt.Errorf("run: %s attempt %d: %w", rs.Run, x.Attempt, err)
		}
		if res.Outcome != o.ClosureOut || res.Confidence != o.ClosureCnf {
			return fmt.Errorf("run: %s attempt %d recorded closure %s/%v but the resolution says %s/%v", rs.Run, x.Attempt, o.ClosureOut, o.ClosureCnf, res.Outcome, res.Confidence)
		}
		src := ""
		if res.Effective != "" {
			src = string(verdicts[res.Effective].Source.Standing)
		}
		if src != o.ClosureSrc {
			return fmt.Errorf("run: %s attempt %d recorded closure source %q but the effective verdict's standing is %q", rs.Run, x.Attempt, o.ClosureSrc, src)
		}
	case Delivered:
		d := a.Delivery
		if d == nil {
			return fmt.Errorf("run: %s attempt %d delivered with no delivery prepared", rs.Run, x.Attempt)
		}
		switch x.Delivery {
		case TransportAccepted:
			if !d.Accepted() {
				return fmt.Errorf("run: %s attempt %d claims transport_accepted with no accepted presentation", rs.Run, x.Attempt)
			}
			if x.From == Delivered {
				return fmt.Errorf("run: %s attempt %d delivered→delivered may only promote to user_acknowledged", rs.Run, x.Attempt)
			}
		case UserAcknowledged:
			if d.Ack == nil {
				return fmt.Errorf("run: %s attempt %d claims user_acknowledged with no ack", rs.Run, x.Attempt)
			}
			if x.From == Delivered && a.deliveryState() != TransportAccepted {
				return fmt.Errorf("run: %s attempt %d delivered→delivered from %q", rs.Run, x.Attempt, a.deliveryState())
			}
		}
	case DeliveryFailedS:
		d := a.Delivery
		if d == nil || d.Accepted() || d.Pending() || len(d.Attempts) == 0 {
			return fmt.Errorf("run: %s attempt %d claims delivery_failed without exhausted, all-failed presentations", rs.Run, x.Attempt)
		}
	}
	return nil
}

// MissionOutcome is the mission fold's label (§5a): execution ⊗ delivery
// under the policy. `accepted_unacknowledged` is what a run carries when
// the transport took the payload but the policy wants the user's ack.
type MissionOutcome string

const (
	MissionPending        MissionOutcome = "pending"
	MissionDelivered      MissionOutcome = "delivered"
	MissionUnacknowledged MissionOutcome = "accepted_unacknowledged"
	MissionFailedDelivery MissionOutcome = "mission_failed(delivery)"
	MissionFailedExec     MissionOutcome = "mission_failed(execution)"
)

// Mission is the fold over one run.
type Mission struct {
	Run          record.RunID
	Handle       string
	Attempt      uint32
	Execution    State          // the latest attempt's state
	Terminal     string         // execution terminal at recorded ("" before)
	Closure      string         // closure resolution outcome ("" before recorded)
	Delivery     DeliveryState  // best state the latest attempt's delivery reached ("" = none)
	Required     DeliveryState  // the policy
	MayDuplicate int            // presentations the process died inside: each may have reached the user
	Stuck        string         // the sheriff's stuck resolution id, when the latest attempt was called stuck
	Outcome      MissionOutcome // the label
	Reason       string
}

// MissionOf is pure over the folded state. An execution that failed is a
// failed mission even when the failure was delivered honestly; a delivery
// short of the policy is not a delivered mission even when execution
// succeeded (§8 item 1).
func MissionOf(rs *RunState) Mission {
	m := Mission{Run: rs.Run, Handle: HandleOf(rs.Run), Required: rs.Goal.Delivery.Required, Outcome: MissionPending}
	a := rs.Latest()
	if a == nil {
		return m
	}
	m.Attempt, m.Execution = a.Attempt.Attempt, a.Current()
	if a.Stuck != nil {
		m.Stuck = string(a.Stuck.ID)
	}
	rec := a.Has(Recorded)
	if rec == nil {
		return m
	}
	m.Terminal, m.Closure = string(rec.Outcome.Terminal), rec.Outcome.ClosureOut
	if d := a.Delivery; d != nil {
		m.MayDuplicate = d.Unknown()
		switch {
		case d.Ack != nil:
			m.Delivery = UserAcknowledged
		case d.Accepted():
			m.Delivery = TransportAccepted
		case len(d.Attempts) > 0:
			m.Delivery = DeliveryFailed
		}
	}
	switch {
	case a.Current() == DeliveryFailedS:
		m.Outcome, m.Reason = MissionFailedDelivery, a.Has(DeliveryFailedS).Reason
	case rec.Outcome.Terminal == invoke.TerminalFailed:
		m.Outcome, m.Reason = MissionFailedExec, rec.Outcome.Reason
		if m.Delivery == "" || m.Delivery == DeliveryFailed {
			m.Reason += "; failure line not yet delivered"
		}
	case m.Delivery == UserAcknowledged, m.Delivery == TransportAccepted && m.Required == TransportAccepted:
		m.Outcome = MissionDelivered
	case m.Delivery == TransportAccepted && m.Required == UserAcknowledged:
		m.Outcome = MissionUnacknowledged
	}
	return m
}

// ReplayKey is the re-run identity of an attempt: what would have to be
// equal for another attempt to be a replay of this one — the goal thought,
// the config it ran under, the exact revisions that reached the request,
// and the request thought itself. Derived from committed records only.
func ReplayKey(rs *RunState, a *AttemptState) (string, error) {
	rec := a.Has(Recorded)
	if rec == nil {
		return "", fmt.Errorf("run: %s attempt %d is not recorded", rs.Run, a.Attempt.Attempt)
	}
	o := rec.Outcome
	var included []learn.ItemRev
	request := ""
	if o.Invocation != "" {
		st, _ := rs.invocation(o.Invocation)
		if st == nil {
			return "", fmt.Errorf("run: invocation %s not in the run", o.Invocation)
		}
		request = st.Invocation.Request.Hash
	}
	for _, p := range rs.Attempts {
		if p.Recall != nil && p.Recall.ID == o.Recall {
			included = p.Recall.Included
		}
	}
	// inputs only: the outcome is what a replay compares, not what names it;
	// the policy selection's id is identity, its snapshot is the input
	cfg := a.Attempt.Config
	cfg.Policy = ""
	raw, err := json.Marshal(struct {
		Ver      string          `json:"ver"`
		Goal     string          `json:"goal"`
		Config   ConfigSnapshot  `json:"config"`
		Included []learn.ItemRev `json:"included"`
		Request  string          `json:"request"`
	}{"replay/1", rs.Goal.Text.Hash, cfg, included, request})
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}

// armMatches: a selection's arm is exactly the goal's — assignment, arm,
// and forced sets — and absent for a goal that is no arm. The goal is the
// production truth of what the arm forces; whether that is the protocol's
// arm is the experiment verifier's to check.
func armMatches(g *Goal, arm *learn.ArmRef) error {
	switch {
	case g.Arm == nil && arm != nil:
		return fmt.Errorf("carries arm %s/%s but the goal is no experiment arm", arm.Assignment, arm.Arm)
	case g.Arm != nil && arm == nil:
		return fmt.Errorf("carries no arm but the goal is arm %s/%s", g.Arm.Assignment, g.Arm.Arm)
	case g.Arm != nil && !arm.Equal(g.Arm):
		return fmt.Errorf("forces %v/%v as arm %s/%s but the goal's arm %s/%s forces %v/%v", arm.Apply, arm.Withhold, arm.Assignment, arm.Arm, g.Arm.Assignment, g.Arm.Arm, g.Arm.Apply, g.Arm.Withhold)
	}
	return nil
}

// checkExposure re-derives the request from committed evidence: the goal
// thought plus the selection's deterministic rendering must be BYTE-EQUAL
// to the invocation's request thought, and each application must be that
// rendering's bullet for its item, scoped to the producing attempt.
func checkExposure(rs *RunState, sel *learn.RecallSelection, get func(thought.Ref) ([]byte, error), learned *learn.Ledger, text func(thought.Ref) ([]byte, error), invID record.RecordID, invRec *invoke.Invocation, produced uint32, suffix bool) error {
	goal, err := get(rs.Goal.Text)
	if err != nil {
		return err
	}
	// NOW: the request is frame + goal + rendering, the frame being the one
	// the PRODUCING attempt's config binds (frame.go)
	var frame []byte
	if int(produced) >= 1 && int(produced) <= len(rs.Attempts) {
		if frame, err = frameText(rs.Attempts[produced-1], get); err != nil {
			return fmt.Errorf("invocation %s frame: %w", invID, err)
		}
	}
	apps := learned.Applications[invID]
	if suffix && invRec.Purpose != invoke.PurposeExecute && invRec.Purpose != invoke.PurposePlan {
		if len(apps) != 0 {
			return fmt.Errorf("%s invocation %s carries applications", invRec.Purpose, invID)
		}
		return nil
	}
	block, reps, err := learn.Render(sel, func(ir learn.ItemRev) ([]byte, error) {
		it := learned.Items[ir.Item]
		if it == nil {
			return nil, fmt.Errorf("recall names unknown item %s", ir.Item)
		}
		for _, r := range it.Revisions {
			if r.ID == ir.Revision {
				return text(r.Text)
			}
		}
		return nil, fmt.Errorf("recall names unknown revision %s", ir.Revision)
	})
	if err != nil {
		return err
	}
	if suffix {
		req, err := get(invRec.Request)
		if err != nil {
			return err
		}
		if len(block) > 0 && !bytesHasSuffix(req, block) {
			return fmt.Errorf("invocation %s request does not end with the recall rendering", invID)
		}
	} else {
		want := thought.Address(thought.Prompt, Lensed(frame, append(append([]byte{}, goal...), block...)))
		if invRec.Request != want {
			return fmt.Errorf("invocation %s request is not frame+goal+recall (%s vs %s)", invID, invRec.Request.Hash, want.Hash)
		}
	}
	if len(apps) != len(reps) {
		return fmt.Errorf("%d applications on invocation %s but the recall rendered %d", len(apps), invID, len(reps))
	}
	for i, r := range reps {
		a := apps[i]
		if a.Item != r.Item || a.Revision != r.Revision || a.Representation != thought.Address(thought.LessonText, r.Representation) || a.RunID != rs.Run || a.Attempt != produced {
			return fmt.Errorf("application %d on invocation %s is not the recall's rendering of %s/%s", i, invID, r.Item, r.Revision)
		}
	}
	return nil
}

// continuesTo reports whether the selection `from` is continued (directly
// or through a chain) by the selection named `to`.
func continuesTo(learned *learn.Ledger, from *learn.RecallSelection, to record.RecordID) bool {
	if from == nil {
		return false
	}
	cur := learned.Selection(to)
	for cur != nil && cur.Continues != "" {
		if cur.Continues == from.ID {
			return true
		}
		cur = learned.Selection(cur.Continues)
	}
	return false
}

func bytesHasSuffix(b, suf []byte) bool {
	return len(b) >= len(suf) && string(b[len(b)-len(suf):]) == string(suf)
}

func describeDecision(d *JoinDecision) string {
	if d == nil {
		return "no decision yet"
	}
	return fmt.Sprintf("selected=%v cancel=%v", d.Selected, d.Cancel)
}

// checkLens executes the lens rules (§13) over one invocation as it
// attaches to its attempt: a lensed invocation cites the attempt's lens
// binding — the name AND the content ref of the text in the attempt's
// config — and its request bytes begin with that text; an attempt under a
// lens has every judge and render request under it (the neutral lens is
// the absence of a prefix, so an unlensed judgement under a lensed attempt
// is a swap the attempt did not declare); a neutral attempt carries none.
func checkLens(rs *RunState, a *AttemptState, is *invoke.State, store *thought.Store) error {
	inv, cfg := is.Invocation, a.Attempt.Config
	n := a.Attempt.Attempt
	switch {
	case inv.Lens == nil:
		if cfg.Lens != "" && invoke.LensAllowed(inv.Purpose) {
			return fmt.Errorf("run: %s attempt %d is under lens %q but %s invocation %s carries none", rs.Run, n, cfg.Lens, inv.Purpose, inv.ID)
		}
	case cfg.Lens == "" || cfg.LensText == nil:
		return fmt.Errorf("run: %s attempt %d is neutral but invocation %s ran under %q", rs.Run, n, inv.ID, inv.Lens.Name)
	case inv.Lens.Name != cfg.Lens:
		return fmt.Errorf("run: %s attempt %d is under lens %q but invocation %s ran under %q", rs.Run, n, cfg.Lens, inv.ID, inv.Lens.Name)
	case inv.Lens.Text != *cfg.LensText:
		return fmt.Errorf("run: %s attempt %d invocation %s cites lens text %s, not the attempt's %s", rs.Run, n, inv.ID, inv.Lens.Text.Hash, cfg.LensText.Hash)
	default:
		text, err := store.Get(*cfg.LensText)
		if err != nil {
			return fmt.Errorf("run: %s attempt %d invocation %s lens text: %w", rs.Run, n, inv.ID, err)
		}
		body, err := store.Get(inv.Request)
		if err != nil {
			return fmt.Errorf("run: %s attempt %d invocation %s request: %w", rs.Run, n, inv.ID, err)
		}
		if len(text) == 0 || !bytes.HasPrefix(body, Lensed(text, nil)) {
			return fmt.Errorf("run: %s attempt %d invocation %s claims lens %q but its request does not begin with the lens text", rs.Run, n, inv.ID, inv.Lens.Name)
		}
	}
	return nil
}

// checkLenses re-executes checkLens over every invocation the attempt made,
// at Recorded: the whole set an outcome rests on, checked once more as one.
func checkLenses(rs *RunState, a *AttemptState, store *thought.Store) error {
	for _, is := range a.Invocations {
		if err := checkLens(rs, a, is, store); err != nil {
			return err
		}
	}
	return nil
}
