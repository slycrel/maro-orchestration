package experiment

import (
	"fmt"
	"reflect"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// State is the fold of every experiment record over one journal prefix,
// composed over the run fold (which carries the learned fold).
type State struct {
	Runs         *run.Ledger
	Experiments  map[record.RecordID]*Experiment
	Assignments  map[record.RecordID]*Assignment                     // by id
	Closed       map[record.RecordID]*Closed                         // by experiment
	Evidence     map[record.RecordID]map[string]*UnitEvidence        // by assignment, then arm
	Commitments  map[record.RecordID]*CohortCommitment               // by experiment
	Attestations map[record.RecordID]*EffectAttestation              // by experiment
	Measurements map[record.RecordID]*EffectMeasurement              // by experiment
	Order        []record.RecordID                                   // experiments in Seq order
	byUnit       map[record.RecordID]map[record.RecordID]*Assignment // experiment → unit → assignment
	byGoal       map[record.RecordID]*run.RunState
}

// RunOf returns the run whose goal is id.
func (st *State) RunOf(goal record.RecordID) *run.RunState { return st.byGoal[goal] }

func (st *State) assignment(exp, unit record.RecordID) *Assignment { return st.byUnit[exp][unit] }

// openFor: an experiment on this hypothesis and population that is not closed.
func (st *State) openFor(h learn.ItemRev, pop string) *Experiment {
	for _, id := range st.Order {
		x := st.Experiments[id]
		if x.Hypothesis == h && x.Population == pop && st.Closed[id] == nil {
			return x
		}
	}
	return nil
}

// lastClosedFor: the highest-version closed experiment on this hypothesis
// and population (nil when none).
func (st *State) lastClosedFor(h learn.ItemRev, pop string) *Experiment {
	var last *Experiment
	for _, id := range st.Order {
		x := st.Experiments[id]
		if x.Hypothesis == h && x.Population == pop && st.Closed[id] != nil && (last == nil || x.Version > last.Version) {
			last = x
		}
	}
	return last
}

func terminalSeq(rs *run.RunState) uint64 {
	if rs == nil || !rs.Terminal() {
		return 0
	}
	for _, t := range rs.Latest().Transitions {
		if t.To == run.Delivered || t.To == run.DeliveryFailedS {
			return t.Seq
		}
	}
	return 0
}

// Fold folds the control and production experiment records over ONE
// journal prefix and REFUSES any history Open, the Runner, and Close could
// not have written: a second open experiment on the same hypothesis and
// population (fishing), a re-open that does not cite the prior attestation
// at Version+1, a unit that is not a terminal production run of the
// population, an assignment of a unit not in the protocol or after
// closure, evidence whose arm run is missing or not terminal or whose
// exposure, deliverable, or root do not recompute, a commitment whose
// protocol or cohort differ from what was opened and assigned, an
// attestation whose rows or scores do not recompute from the evidence and
// the fixtures, and a measurement that is not the estimator's fold over
// its attestation.
func Fold(j *journal.Journal, store *thought.Store) (*State, error) {
	pr := j.Production().Pin()
	led, err := run.Fold(pr, store)
	if err != nil {
		return nil, err
	}
	st := &State{Runs: led, Experiments: map[record.RecordID]*Experiment{}, Assignments: map[record.RecordID]*Assignment{}, Closed: map[record.RecordID]*Closed{},
		Evidence: map[record.RecordID]map[string]*UnitEvidence{}, Commitments: map[record.RecordID]*CohortCommitment{}, Attestations: map[record.RecordID]*EffectAttestation{},
		Measurements: map[record.RecordID]*EffectMeasurement{}, byUnit: map[record.RecordID]map[record.RecordID]*Assignment{}, byGoal: map[record.RecordID]*run.RunState{}}
	for _, rs := range led.Runs {
		st.byGoal[rs.Goal.ID] = rs
	}
	head := pr.Head()
	// both envelopes, in ONE Seq order: production evidence cites control
	// ids and a re-opened experiment cites a production attestation
	var recs []record.Record
	collect := func(r record.Record) error {
		switch r.(type) {
		case *Experiment, *Assignment, *Closed, *UnitEvidence, *CohortCommitment, *EffectAttestation, *EffectMeasurement:
			recs = append(recs, r)
		}
		return nil
	}
	if err := j.Control().ScanThrough(0, head, collect); err != nil {
		return nil, err
	}
	if err := pr.Scan(0, collect); err != nil {
		return nil, err
	}
	sort.SliceStable(recs, func(a, b int) bool { return recs[a].Head().Seq < recs[b].Head().Seq })
	for _, r := range recs {
		var err error
		switch x := r.(type) {
		case *Experiment:
			err = st.experiment(x)
		case *Assignment:
			err = st.assign(x)
		case *Closed:
			err = st.closed(x)
		case *UnitEvidence:
			err = st.evidence(x)
		case *CohortCommitment:
			err = st.commitment(x)
		case *EffectAttestation:
			err = st.attestation(x, store)
		case *EffectMeasurement:
			err = st.measurement(x)
		}
		if err != nil {
			return nil, err
		}
	}
	for exp, cl := range st.Closed {
		cm := st.Commitments[exp]
		if cm == nil || cm.ID != cl.Commitment {
			return nil, fmt.Errorf("experiment: closure of %s names commitment %s, which is not the experiment's", exp, cl.Commitment)
		}
	}
	return st, nil
}

func (st *State) closed(x *Closed) error {
	x0 := st.Experiments[x.Experiment]
	if x0 == nil {
		return fmt.Errorf("experiment: closure %s names experiment %s, which is not an earlier record", x.ID, x.Experiment)
	}
	if st.Closed[x.Experiment] != nil {
		return fmt.Errorf("experiment: %s closed twice", x.Experiment)
	}
	if x.Count != x0.N || len(st.byUnit[x.Experiment]) != x0.N {
		return fmt.Errorf("experiment: %s closes at %d with %d of %d units assigned", x.Experiment, x.Count, len(st.byUnit[x.Experiment]), x0.N)
	}
	st.Closed[x.Experiment] = x
	return nil
}

func (st *State) experiment(x *Experiment) error {
	// the units: terminal production runs of the population, before this
	for i, u := range x.Units {
		rs := st.byGoal[u.Goal]
		if rs == nil || rs.Goal.Origin == run.OriginReplay || rs.Goal.Origin == run.OriginFork {
			return fmt.Errorf("experiment: %s unit %d (%s) is not a production run's goal", x.ID, i, u.Goal)
		}
		if ts := terminalSeq(rs); ts == 0 || ts > x.Seq {
			return fmt.Errorf("experiment: %s unit %d (%s) was not terminal when the experiment opened", x.ID, i, u.Goal)
		}
		if string(rs.Family.Family) != x.Population {
			return fmt.Errorf("experiment: %s unit %d (%s) is of family %s, not the population %s", x.ID, i, u.Goal, rs.Family.Family, x.Population)
		}
	}
	// the hypothesis: a revision the learned population has
	it := st.Runs.Learned.Items[x.Hypothesis.Item]
	if it == nil || !hasRevision(it, x.Hypothesis.Revision) {
		return fmt.Errorf("experiment: %s hypothesis %s/%s is not a learned revision", x.ID, x.Hypothesis.Item, x.Hypothesis.Revision)
	}
	// the fishing guard (§19.4): one open experiment per hypothesis and
	// population; a re-open is the next version citing the prior attestation
	if open := st.openFor(x.Hypothesis, x.Population); open != nil {
		return fmt.Errorf("experiment: %s opens on %s/%s over %s while %s is open", x.ID, x.Hypothesis.Item, x.Hypothesis.Revision, x.Population, open.ID)
	}
	last := st.lastClosedFor(x.Hypothesis, x.Population)
	switch {
	case last == nil && x.Version != 1:
		return fmt.Errorf("experiment: %s is version %d with no prior experiment", x.ID, x.Version)
	case last != nil && (x.Version != last.Version+1 || st.Attestations[last.ID] == nil || x.Prior != st.Attestations[last.ID].ID):
		return fmt.Errorf("experiment: %s must be version %d citing attestation of %s", x.ID, last.Version+1, last.ID)
	}
	st.Experiments[x.ID] = x
	st.Order = append(st.Order, x.ID)
	st.byUnit[x.ID] = map[record.RecordID]*Assignment{}
	return nil
}

func (st *State) assign(x *Assignment) error {
	x0 := st.Experiments[x.Experiment]
	if x0 == nil {
		return fmt.Errorf("experiment: assignment %s names experiment %s, which is not an earlier record", x.ID, x.Experiment)
	}
	if st.Closed[x.Experiment] != nil {
		return fmt.Errorf("experiment: assignment %s after %s closed", x.ID, x.Experiment)
	}
	if x.Ordinal >= len(x0.Units) || x0.Units[x.Ordinal].Goal != x.Unit {
		return fmt.Errorf("experiment: assignment %s puts %s at ordinal %d, which is not the protocol's", x.ID, x.Unit, x.Ordinal)
	}
	if st.byUnit[x.Experiment][x.Unit] != nil {
		return fmt.Errorf("experiment: unit %s assigned twice to %s", x.Unit, x.Experiment)
	}
	st.Assignments[x.ID] = x
	st.byUnit[x.Experiment][x.Unit] = x
	return nil
}

func (st *State) evidence(x *UnitEvidence) error {
	as := st.Assignments[x.Assignment]
	if as == nil || as.Seq > x.Seq || as.Experiment != x.Experiment || as.Unit != x.Unit {
		return fmt.Errorf("experiment: evidence %s names assignment %s, which is not an earlier assignment of %s to %s", x.ID, x.Assignment, x.Unit, x.Experiment)
	}
	x0 := st.Experiments[x.Experiment]
	if st.Evidence[as.ID][x.Arm] != nil {
		return fmt.Errorf("experiment: evidence for %s arm %s twice", as.ID, x.Arm)
	}
	rs := st.Runs.Replays[as.ID][x.Arm]
	if rs == nil || rs.Run != x.RunID {
		return fmt.Errorf("experiment: evidence %s is about run %s, which is not the %s arm of %s", x.ID, x.RunID, x.Arm, as.ID)
	}
	if ts := terminalSeq(rs); ts == 0 || ts > x.Seq {
		return fmt.Errorf("experiment: evidence %s before its arm run %s was terminal", x.ID, rs.Run)
	}
	want := EvidenceOf(rs, st.Runs.Learned, x0.Hypothesis, x.Experiment, as.ID, as.Unit, x.Arm)
	if x.Attempt != want.Attempt || x.Exposed != want.Exposed || !reflect.DeepEqual(x.Deliverable, want.Deliverable) || x.Missing != want.Missing || x.ArtifactRoot != want.ArtifactRoot {
		return fmt.Errorf("experiment: evidence %s does not re-derive from run %s (exposed %v/%v, deliverable %v/%v, missing %q/%q, root %s/%s)", x.ID, rs.Run,
			x.Exposed, want.Exposed, x.Deliverable != nil, want.Deliverable != nil, x.Missing, want.Missing, x.ArtifactRoot[:8], want.ArtifactRoot[:8])
	}
	if st.Evidence[as.ID] == nil {
		st.Evidence[as.ID] = map[string]*UnitEvidence{}
	}
	st.Evidence[as.ID][x.Arm] = x
	return nil
}

func (st *State) commitment(x *CohortCommitment) error {
	x0 := st.Experiments[x.Experiment]
	if x0 == nil {
		return fmt.Errorf("experiment: commitment %s names experiment %s, which is not an earlier record", x.ID, x.Experiment)
	}
	if st.Commitments[x.Experiment] != nil {
		return fmt.Errorf("experiment: %s committed twice", x.Experiment)
	}
	if !reflect.DeepEqual(x.Protocol, x0.Protocol) {
		return fmt.Errorf("experiment: commitment %s carries a protocol that is not %s's", x.ID, x.Experiment)
	}
	for i, u := range x.Units {
		as := st.byUnit[x.Experiment][u.Unit]
		if as == nil || as.ID != u.Assignment || as.Ordinal != u.Ordinal || as.Seq > x.Seq {
			return fmt.Errorf("experiment: commitment %s row %d is not the assignment of %s", x.ID, i, u.Unit)
		}
		for _, arm := range x0.Arms {
			if ev := st.Evidence[as.ID][arm.Arm]; ev == nil || ev.Seq > x.Seq {
				return fmt.Errorf("experiment: commitment %s closes over unit %d without its %s evidence", x.ID, i, arm.Arm)
			}
		}
	}
	st.Commitments[x.Experiment] = x
	return nil
}

func (st *State) attestation(x *EffectAttestation, store *thought.Store) error {
	cm := st.Commitments[x.Experiment]
	cl := st.Closed[x.Experiment]
	if cm == nil || cl == nil || cm.ID != x.Cohort || cl.ID != x.Closure || cm.Seq > x.Seq {
		return fmt.Errorf("experiment: attestation %s does not cite the closure and commitment of %s", x.ID, x.Experiment)
	}
	if st.Attestations[x.Experiment] != nil {
		return fmt.Errorf("experiment: %s attested twice", x.Experiment)
	}
	if !reflect.DeepEqual(x.Protocol, cm.Protocol) {
		return fmt.Errorf("experiment: attestation %s carries a protocol that is not the commitment's", x.ID)
	}
	for i, u := range cm.Units {
		want, err := st.row(store, cm.Protocol, i, u)
		if err != nil {
			return err
		}
		if x.Units[i] != want {
			return fmt.Errorf("experiment: attestation %s row %d does not recompute from the evidence and the fixture (%+v vs %+v)", x.ID, i, x.Units[i], want)
		}
	}
	st.Attestations[x.Experiment] = x
	return nil
}

func (st *State) measurement(x *EffectMeasurement) error {
	att := st.Attestations[x.Experiment]
	if att == nil || att.ID != x.Attestation {
		return fmt.Errorf("experiment: measurement %s does not cite the attestation of %s", x.ID, x.Experiment)
	}
	if st.Measurements[x.Experiment] != nil {
		return fmt.Errorf("experiment: %s measured twice", x.Experiment)
	}
	want := Measure(att)
	want.Header = x.Header
	if !reflect.DeepEqual(x, want) {
		return fmt.Errorf("experiment: measurement %s is not the estimator's fold over %s (%s/%s vs %s/%s)", x.ID, att.ID, x.Verdict, x.ItemEffect, want.Verdict, want.ItemEffect)
	}
	st.Measurements[x.Experiment] = x
	return nil
}
