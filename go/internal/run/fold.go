package run

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
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
}

// Fold folds the production population into per-run state and REFUSES any
// history the driver could not have written: the journal door executes each
// record's own vocabulary; the fold executes the cross-record rules (an
// attempt that was never started, a second assessment for a goal, a
// delivery record scoped to a different owner, an ack without a start or
// with a foreign token, a `delivered` transition without the evidence it
// claims, a recorded outcome that names evidence from another run or a
// resolution that does not re-derive). A reader must not paper over these:
// the mission and the shared ledger are folds of exactly this.
func Fold(pr *journal.ProductionReader) (*Ledger, error) {
	goals := map[record.RecordID]*Goal{}
	var goalOrder []*Goal
	started := map[record.RecordID]bool{}
	fams := map[record.RecordID]*FamilyAssessment{}
	runs := map[record.RunID]*RunState{}
	deliveries := map[record.RecordID]*Delivery{}
	resolutions := map[record.RecordID]*verdict.Resolution{}
	verdicts := map[record.RecordID]*verdict.Verdict{}
	observations := map[record.RecordID]*verdict.Observation{}
	// invocation states are folded up front so transitions can be checked
	// against evidence in one pass
	inv, err := invoke.Fold(pr)
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
		case *verdict.Verdict:
			verdicts[x.ID] = x
		case *verdict.Observation:
			observations[x.ID] = x
		case *verdict.Resolution:
			resolutions[x.ID] = x
		case *invoke.Invocation:
			// invocations are folded up front (receipts, terminals, reconciliation);
			// attach each to the attempt it ran under as its record arrives
			st := inv[x.ID]
			rs := runs[x.RunID]
			if st != nil && rs != nil && x.Attempt > 0 && int(x.Attempt) <= len(rs.Attempts) {
				rs.Attempts[x.Attempt-1].Invocations = append(rs.Attempts[x.Attempt-1].Invocations, st)
			}
		case *RunAttempt:
			rs := get(x.RunID)
			started[x.Goal] = true
			if int(x.Attempt) != len(rs.Attempts)+1 {
				return fmt.Errorf("run: %s attempt %d started but %d attempts exist", x.RunID, x.Attempt, len(rs.Attempts))
			}
			if rs.Goal == nil {
				rs.Goal, rs.Family = goals[x.Goal], fams[x.Goal]
				if rs.Goal == nil || rs.Family == nil || rs.Family.ID != x.Family {
					return fmt.Errorf("run: %s attempt %d cites goal %s / family %s that were not committed first", x.RunID, x.Attempt, x.Goal, x.Family)
				}
			} else if rs.Goal.ID != x.Goal || rs.Family.ID != x.Family {
				return fmt.Errorf("run: %s attempt %d cites a different goal or assessment than attempt 1", x.RunID, x.Attempt)
			}
			if x.Attempt > 1 && rs.Attempts[x.Attempt-2].Current() != Recoverable {
				return fmt.Errorf("run: %s attempt %d started but attempt %d is at %q, not recoverable", x.RunID, x.Attempt, x.Attempt-1, rs.Attempts[x.Attempt-2].Current())
			}
			a := &AttemptState{Attempt: x}
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
			if err := checkTransition(rs, a, x, inv, resolutions, verdicts, observations); err != nil {
				return err
			}
			a.Transitions = append(a.Transitions, x)
		case *DeliveryPrepared:
			rs := get(x.RunID)
			a, err := attempt(rs, x.Attempt, "delivery")
			if err != nil {
				return err
			}
			if a.Delivery != nil {
				return fmt.Errorf("run: %s attempt %d prepared two deliveries", x.RunID, x.Attempt)
			}
			if a.Has(Recorded) == nil {
				return fmt.Errorf("run: %s attempt %d prepared a delivery before recorded", x.RunID, x.Attempt)
			}
			if x.Origin != rs.Goal.Origin || x.Required != rs.Goal.Delivery.Required {
				return fmt.Errorf("run: delivery %s (origin %s, required %s) disagrees with the goal (%s, %s)", x.ID, x.Origin, x.Required, rs.Goal.Origin, rs.Goal.Delivery.Required)
			}
			a.Delivery = &Delivery{Prepared: x}
			deliveries[x.ID] = a.Delivery
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
	led := &Ledger{Runs: runs, Families: fams}
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

// checkTransition executes the cross-record rules a transition claims.
func checkTransition(rs *RunState, a *AttemptState, x *Transition, inv map[record.RecordID]*invoke.State, resolutions map[record.RecordID]*verdict.Resolution, verdicts map[record.RecordID]*verdict.Verdict, observations map[record.RecordID]*verdict.Observation) error {
	switch x.To {
	case Recorded:
		o := x.Outcome
		if o.GoalText != rs.Goal.Text {
			return fmt.Errorf("run: %s attempt %d recorded an outcome for a different goal thought", rs.Run, x.Attempt)
		}
		if o.Invocation != "" {
			st := inv[o.Invocation]
			if st == nil || st.Invocation.RunID != rs.Run || st.Invocation.Attempt != o.Produced || o.Produced > x.Attempt {
				return fmt.Errorf("run: %s attempt %d recorded invocation %s that this run's attempt %d did not make", rs.Run, x.Attempt, o.Invocation, o.Produced)
			}
			if st.Invocation.Backend.Model != o.Model {
				return fmt.Errorf("run: %s attempt %d recorded model %q but the invocation ran %q", rs.Run, x.Attempt, o.Model, st.Invocation.Backend.Model)
			}
			if o.Receipt != "" {
				if st.Receipt == nil || st.Receipt.ID != o.Receipt || o.Response == nil || *o.Response != st.Receipt.Response || o.Usage != st.Receipt.Usage {
					return fmt.Errorf("run: %s attempt %d recorded receipt %s that disagrees with invocation %s", rs.Run, x.Attempt, o.Receipt, o.Invocation)
				}
			}
		} else if o.Model != "" {
			return fmt.Errorf("run: %s attempt %d recorded a model with no invocation", rs.Run, x.Attempt)
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
