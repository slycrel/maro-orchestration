package run

import (
	"fmt"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// Delivery is the folded state of one DeliveryPrepared.
type Delivery struct {
	Prepared *DeliveryPrepared
	Attempts []*DeliveryAttempted // by N
	Ack      *DeliveryAcked
}

// State reached by this delivery: "" (nothing presented yet), transport_accepted,
// user_acknowledged, or failed (every attempt failed and the outbox gave up —
// the transition says so; here it is "last attempt failed").
func (d *Delivery) Accepted() bool {
	for _, a := range d.Attempts {
		if a.Result == TransportAccepted {
			return true
		}
	}
	return false
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

// Has reports whether the attempt passed through s.
func (a *AttemptState) Has(s State) *Transition {
	for _, t := range a.Transitions {
		if t.To == s {
			return t
		}
	}
	return nil
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

// Ledger is the fold of the production population: every run, plus goals
// that were taken in but whose run never started (a crash after intake).
type Ledger struct {
	Runs      map[record.RunID]*RunState
	Unstarted []*Goal // in Seq order
}

// Fold folds the production population into per-run state. A record that
// violates the state machine (a transition for an attempt that was never
// started, two attempts with one number, two deliveries for one attempt) is
// an error: the journal admitted something the driver could not have
// written, and a reader must not paper over it.
func Fold(pr *journal.ProductionReader) (*Ledger, error) {
	goals := map[record.RecordID]*Goal{}
	var goalOrder []*Goal
	started := map[record.RecordID]bool{}
	fams := map[record.RecordID]*FamilyAssessment{}
	runs := map[record.RunID]*RunState{}
	deliveries := map[record.RecordID]*Delivery{}
	resolutions := map[record.RunID][]*verdict.Resolution{}
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
	err := pr.Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *Goal:
			goals[x.ID] = x
			goalOrder = append(goalOrder, x)
		case *FamilyAssessment:
			fams[x.Goal] = x
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
			}
			rs.Attempts = append(rs.Attempts, &AttemptState{Attempt: x})
		case *Transition:
			a, err := attempt(get(x.RunID), x.Attempt, "transition")
			if err != nil {
				return err
			}
			if cur := a.Current(); cur != x.From {
				return fmt.Errorf("run: %s attempt %d transition %s→%s but the attempt is at %q", x.RunID, x.Attempt, x.From, x.To, cur)
			}
			a.Transitions = append(a.Transitions, x)
		case *DeliveryPrepared:
			a, err := attempt(get(x.RunID), x.Attempt, "delivery")
			if err != nil {
				return err
			}
			if a.Delivery != nil {
				return fmt.Errorf("run: %s attempt %d prepared two deliveries", x.RunID, x.Attempt)
			}
			if a.Has(Recorded) == nil {
				return fmt.Errorf("run: %s attempt %d prepared a delivery before recorded", x.RunID, x.Attempt)
			}
			a.Delivery = &Delivery{Prepared: x}
			deliveries[x.ID] = a.Delivery
		case *DeliveryAttempted:
			d := deliveries[x.Delivery]
			if d == nil {
				return fmt.Errorf("run: attempt on unknown delivery %s", x.Delivery)
			}
			if x.N != len(d.Attempts)+1 {
				return fmt.Errorf("run: delivery %s attempt %d out of order (%d seen)", x.Delivery, x.N, len(d.Attempts))
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
			d.Ack = x
		case *verdict.Resolution:
			if x.VerdictKind == verdict.KindClosure && x.RunID != "" {
				resolutions[x.RunID] = append(resolutions[x.RunID], x)
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	inv, err := invoke.Fold(pr)
	if err != nil {
		return nil, err
	}
	ids := make([]record.RecordID, 0, len(inv))
	for id := range inv {
		ids = append(ids, id)
	}
	sort.Slice(ids, func(i, j int) bool { return inv[ids[i]].Invocation.Seq < inv[ids[j]].Invocation.Seq })
	for _, id := range ids {
		st := inv[id]
		rs := runs[st.Invocation.RunID]
		if rs == nil || int(st.Invocation.Attempt) > len(rs.Attempts) || st.Invocation.Attempt == 0 {
			continue // an invocation outside any run (judges at workspace scope arrive later)
		}
		a := rs.Attempts[st.Invocation.Attempt-1]
		a.Invocations = append(a.Invocations, st)
	}
	led := &Ledger{Runs: runs}
	for _, g := range goalOrder {
		if !started[g.ID] {
			led.Unstarted = append(led.Unstarted, g)
		}
	}
	for id, rs := range runs {
		if rs.Goal == nil {
			return nil, fmt.Errorf("run: %s has records but no attempt", id)
		}
		// the closure resolution named by the latest recorded outcome
		for _, a := range rs.Attempts {
			if t := a.Has(Recorded); t != nil {
				for _, res := range resolutions[id] {
					if res.ID == t.Outcome.Closure {
						rs.Closure = res
					}
				}
			}
		}
	}
	return led, nil
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
	Run       record.RunID
	Handle    string
	Attempt   uint32
	Execution State          // the latest attempt's state
	Terminal  string         // execution terminal at recorded ("" before)
	Closure   string         // closure resolution outcome ("" before recorded)
	Delivery  DeliveryState  // best state any delivery of the latest attempt reached ("" = none)
	Required  DeliveryState  // the policy
	Outcome   MissionOutcome // the label
	Reason    string
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
		if m.Delivery == "" || (m.Delivery == DeliveryFailed) {
			m.Reason += "; failure line not yet delivered"
		}
	case m.Delivery == UserAcknowledged, m.Delivery == TransportAccepted && m.Required == TransportAccepted:
		m.Outcome = MissionDelivered
	case m.Delivery == TransportAccepted && m.Required == UserAcknowledged:
		m.Outcome = MissionUnacknowledged
	}
	return m
}
