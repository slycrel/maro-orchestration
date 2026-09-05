package experiment

import (
	"fmt"
	"reflect"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// State is the fold of every experiment record over one journal prefix,
// composed over the run fold (which carries the learned fold).
type State struct {
	Head         uint64 // the journal head this state was folded through
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
	byOrdinal    map[record.RecordID][]*Assignment                   // experiment → assignments in ordinal order (dense for live)
	claimed      map[record.RecordID]*Assignment                     // unit → its live assignment (mutual exclusion)
	byGoal       map[record.RecordID]*run.RunState
}

// ordinal returns the experiment's assignment at ordinal i (nil if none).
func (st *State) ordinal(exp record.RecordID, i int) *Assignment {
	for _, as := range st.byOrdinal[exp] {
		if as.Ordinal == i {
			return as
		}
	}
	return nil
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
		Measurements: map[record.RecordID]*EffectMeasurement{}, byUnit: map[record.RecordID]map[record.RecordID]*Assignment{}, byOrdinal: map[record.RecordID][]*Assignment{},
		claimed: map[record.RecordID]*Assignment{}, byGoal: map[record.RecordID]*run.RunState{}}
	for _, rs := range led.Runs {
		st.byGoal[rs.Goal.ID] = rs
	}
	head := pr.Head()
	st.Head = head
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
			err = st.experiment(x, store)
		case *Assignment:
			err = st.assign(x, j, store)
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
		if !j.SameCommand(cl.Seq, cm.Seq) {
			return nil, fmt.Errorf("experiment: closure of %s and its commitment were not one command (seq %d, %d)", exp, cl.Seq, cm.Seq)
		}
	}
	// every arm run, evidence or not: its assignment is known and its
	// goal and selections force exactly the protocol's sets
	for asID, byArm := range led.Arms {
		as := st.Assignments[asID]
		if as == nil {
			return nil, fmt.Errorf("experiment: arm runs cite assignment %s, which is not a record", asID)
		}
		for arm, rs := range byArm {
			if err := st.checkArm(as, arm, rs, j); err != nil {
				return nil, err
			}
		}
	}
	// a live unit assigned but never started (a crash between intake and
	// the run) is still an arm: its goal carries the arm
	for _, g := range led.Unstarted {
		if g.Arm != nil {
			if as := st.Assignments[g.Arm.Assignment]; as == nil || !g.Arm.Equal(st.Experiments[as.Experiment].armRef(as.ID, g.Arm.Arm)) {
				return nil, fmt.Errorf("experiment: unstarted goal %s carries arm %s/%s, which is not an assignment's protocol arm", g.ID, g.Arm.Assignment, g.Arm.Arm)
			}
		}
	}
	return st, nil
}

// checkArm executes the arm's contract on an arm run. A replay arm's goal
// was taken in after the assignment; a live unit's goal was taken in IN
// THE SAME COMMAND as its assignment (the sequencer-enforced intake), in
// the assigned arm. Either way the hypothesis was still the item's
// current revision when the goal was taken in (recall forces an item at
// its current revision, so a stale arm administers nothing), the goal
// carries EXACTLY the protocol's forced sets for the arm, and so does
// every attempt's recall and policy selection — the two arms differ by
// the hypothesis and nothing else.
func (st *State) checkArm(as *Assignment, arm string, rs *run.RunState, j *journal.Journal) error {
	x := st.Experiments[as.Experiment]
	switch x.Assignment {
	case PairedReplay:
		if rs.Goal.Origin != run.OriginReplay || rs.Goal.Seq < as.Seq {
			return fmt.Errorf("experiment: arm run %s is not a replay taken in after its assignment %s", rs.Run, as.ID)
		}
	case RandomizedLive:
		if arm != as.Arm || !j.SameCommand(rs.Goal.Seq, as.Seq) {
			return fmt.Errorf("experiment: live unit %s runs arm %s, but its assignment %s (arm %s) was not its intake command", rs.Goal.ID, arm, as.ID, as.Arm)
		}
	}
	it := st.Runs.Learned.Items[x.Hypothesis.Item]
	if cur := currentAt(it, rs.Goal.Seq); cur == nil || cur.ID != x.Hypothesis.Revision {
		return fmt.Errorf("experiment: arm run %s started after hypothesis %s/%s was superseded", rs.Run, x.Hypothesis.Item, x.Hypothesis.Revision)
	}
	want := x.armRef(as.ID, arm)
	if want == nil {
		return fmt.Errorf("experiment: arm %q is not in the protocol of %s", arm, x.ID)
	}
	if !rs.Goal.Arm.Equal(want) {
		return fmt.Errorf("experiment: arm run %s's goal forces %s, not the protocol's %s arm", rs.Run, describeArm(rs.Goal.Arm), arm)
	}
	for _, a := range rs.Attempts {
		if a.Policy != nil && !a.Policy.Arm.Equal(want) {
			return fmt.Errorf("experiment: arm run %s attempt %d policy selection forces %s, not the protocol's %s arm", rs.Run, a.Attempt.Attempt, describeArm(a.Policy.Arm), arm)
		}
		if a.Recall != nil && !a.Recall.Arm.Equal(want) {
			return fmt.Errorf("experiment: arm run %s attempt %d recall selection forces %s, not the protocol's %s arm", rs.Run, a.Attempt.Attempt, describeArm(a.Recall.Arm), arm)
		}
	}
	return nil
}

func describeArm(a *learn.ArmRef) string {
	if a == nil {
		return "nothing"
	}
	return fmt.Sprintf("apply %v withhold %v", a.Apply, a.Withhold)
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

func (st *State) experiment(x *Experiment, store *thought.Store) error {
	// the units: terminal production runs of the population, before this,
	// no two with the same goal text, each with a fixture an oracle can match
	texts := map[string]bool{}
	for i, u := range x.Units {
		rs := st.byGoal[u.Goal]
		if rs == nil || rs.Goal.Origin == run.OriginReplay || rs.Goal.Origin == run.OriginFork || rs.Goal.Arm != nil {
			return fmt.Errorf("experiment: %s unit %d (%s) is not a plain production run's goal", x.ID, i, u.Goal)
		}
		if texts[rs.Goal.Text.Hash] {
			return fmt.Errorf("experiment: %s unit %d (%s) repeats another unit's goal text", x.ID, i, u.Goal)
		}
		texts[rs.Goal.Text.Hash] = true
		if err := checkFixture(store, u.Fixture); err != nil {
			return fmt.Errorf("experiment: %s unit %d (%s): %w", x.ID, i, u.Goal, err)
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
	// ablate withholds: the revision must have been selectable when the
	// experiment opened, or the arms did not differ (the learned ledger is
	// folded whole, so the stage at open is read off the transition seqs)
	// (apply, symmetrically: the revision must NOT have been selectable, or
	// both arms ran with it. Whether an ablated policy was DECIDING at open
	// is checked at open and at every admission, not here: it needs the
	// ledger as of the open, which this fold does not keep — a residual)
	stage := learn.Candidate
	for _, tr := range it.Transitions[x.Hypothesis.Revision] {
		if tr.Seq < x.Seq {
			stage = tr.To
		}
	}
	switch {
	case x.Relation == AblateItem && !learn.Selectable[stage]:
		return fmt.Errorf("experiment: %s ablates %s/%s, which was %s, not selectable, when it opened", x.ID, x.Hypothesis.Item, x.Hypothesis.Revision, stage)
	case x.Relation == ApplyItem && learn.Selectable[stage]:
		return fmt.Errorf("experiment: %s applies %s/%s, which was already %s, selectable, when it opened", x.ID, x.Hypothesis.Item, x.Hypothesis.Revision, stage)
	}
	st.Experiments[x.ID] = x
	st.Order = append(st.Order, x.ID)
	st.byUnit[x.ID] = map[record.RecordID]*Assignment{}
	return nil
}

func (st *State) assign(x *Assignment, j *journal.Journal, store *thought.Store) error {
	x0 := st.Experiments[x.Experiment]
	if x0 == nil {
		return fmt.Errorf("experiment: assignment %s names experiment %s, which is not an earlier record", x.ID, x.Experiment)
	}
	if st.Closed[x.Experiment] != nil {
		return fmt.Errorf("experiment: assignment %s after %s closed", x.ID, x.Experiment)
	}
	if st.byUnit[x.Experiment][x.Unit] != nil {
		return fmt.Errorf("experiment: unit %s assigned twice to %s", x.Unit, x.Experiment)
	}
	switch x0.Assignment {
	case PairedReplay:
		if x.Ordinal >= len(x0.Units) || x0.Units[x.Ordinal].Goal != x.Unit || x.Arm != "" {
			return fmt.Errorf("experiment: assignment %s puts %s at ordinal %d, which is not the protocol's", x.ID, x.Unit, x.Ordinal)
		}
	case RandomizedLive:
		// admission order, dense, under n; the unit is a plain production
		// goal of the population taken in by THIS command, claimed by no
		// other experiment, while the hypothesis is current, in the arm
		// the keyed randomization names
		if x.Ordinal != len(st.byOrdinal[x.Experiment]) || x.Ordinal >= x0.N {
			return fmt.Errorf("experiment: assignment %s takes ordinal %d of %s; %d assigned of %d", x.ID, x.Ordinal, x.Experiment, len(st.byOrdinal[x.Experiment]), x0.N)
		}
		g := st.Runs.Goal(x.Unit)
		fam := st.Runs.Families[x.Unit]
		if g == nil || fam == nil || g.Origin == run.OriginReplay || g.Origin == run.OriginFork || g.Parent != "" || !j.SameCommand(g.Seq, x.Seq) {
			return fmt.Errorf("experiment: live assignment %s of %s was not the goal's intake command", x.ID, x.Unit)
		}
		if string(fam.Family) != x0.Population {
			return fmt.Errorf("experiment: live assignment %s admits a %s goal to a %s experiment", x.ID, fam.Family, x0.Population)
		}
		// the assessment is an assertion in the goal's command; the
		// population is what the goal's text classifies as
		text, err := store.Get(g.Text)
		if err != nil {
			return err
		}
		if fk, _ := run.Classify(string(text)); string(fk) != x0.Population {
			return fmt.Errorf("experiment: live assignment %s admits goal %s, whose text classifies as %s, not %s", x.ID, g.ID, fk, x0.Population)
		}
		if g.Lane != run.LaneNow {
			return fmt.Errorf("experiment: live assignment %s admits a %s-lane goal; live cohorts are NOW-lane goals (one deliverable shape)", x.ID, g.Lane)
		}
		if prior := st.claimed[x.Unit]; prior != nil {
			return fmt.Errorf("experiment: goal %s admitted to %s and to %s", x.Unit, prior.Experiment, x.Experiment)
		}
		it := st.Runs.Learned.Items[x0.Hypothesis.Item]
		if cur := currentAt(it, x.Seq); cur == nil || cur.ID != x0.Hypothesis.Revision {
			return fmt.Errorf("experiment: live assignment %s made after hypothesis %s/%s was superseded", x.ID, x0.Hypothesis.Item, x0.Hypothesis.Revision)
		}
		first := ""
		if x.Ordinal%2 == 1 {
			first = st.ordinal(x.Experiment, x.Ordinal-1).Arm
		}
		if x.Arm != ArmFor(x.Seed, x.Ordinal, first) {
			return fmt.Errorf("experiment: live assignment %s names arm %s, the randomization names %s", x.ID, x.Arm, ArmFor(x.Seed, x.Ordinal, first))
		}
		if !g.Arm.Equal(x0.armRef(x.ID, x.Arm)) {
			return fmt.Errorf("experiment: goal %s does not carry the %s arm of assignment %s", g.ID, x.Arm, x.ID)
		}
		st.claimed[x.Unit] = x
	}
	st.Assignments[x.ID] = x
	st.byUnit[x.Experiment][x.Unit] = x
	st.byOrdinal[x.Experiment] = append(st.byOrdinal[x.Experiment], x)
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
	if x0.Assignment == RandomizedLive && x.Arm != as.Arm {
		return fmt.Errorf("experiment: evidence %s is for arm %s of live assignment %s, which runs %s", x.ID, x.Arm, as.ID, as.Arm)
	}
	rs := st.Runs.Arms[as.ID][x.Arm]
	if rs == nil || rs.Run != x.RunID {
		return fmt.Errorf("experiment: evidence %s is about run %s, which is not the %s arm of %s", x.ID, x.RunID, x.Arm, as.ID)
	}
	if ts := terminalSeq(rs); ts == 0 || ts > x.Seq {
		return fmt.Errorf("experiment: evidence %s before its arm run %s was terminal", x.ID, rs.Run)
	}
	want := EvidenceOf(rs, x0.Hypothesis, x.Experiment, as.ID, as.Unit, x.Arm)
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
		if as == nil || as.ID != u.Assignment || as.Ordinal != u.Ordinal || as.Ordinal != i || as.Seq > x.Seq {
			return fmt.Errorf("experiment: commitment %s row %d is not the assignment of %s", x.ID, i, u.Unit)
		}
		for _, arm := range x0.armsOf(as) {
			if ev := st.Evidence[as.ID][arm]; ev == nil || ev.Seq > x.Seq {
				return fmt.Errorf("experiment: commitment %s closes over unit %d without its %s evidence", x.ID, i, arm)
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
		if cm.Protocol.Assignment == RandomizedLive {
			if err := st.checkLiveRow(store, cm.Protocol, i, u, x.Units[i], x.Seq); err != nil {
				return fmt.Errorf("experiment: attestation %s row %d: %w", x.ID, i, err)
			}
			continue
		}
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

// checkLiveRow executes a live row's contract: it is the unit's one arm
// and evidence with the evidence's missingness and exposure; a scored row
// cites an evaluate invocation of the unit's run, committed before the
// attestation, tool-less, whose request is EXACTLY the blinded prompt
// re-rendered from the unit's goal and the evidence's deliverable, and
// whose receipt parses to the row's score; an unevaluated row has at
// least EvaluatorTries evaluate calls and no usable one.
func (st *State) checkLiveRow(store *thought.Store, p Protocol, i int, u AssignedUnit, row UnitRow, before uint64) error {
	as := st.Assignments[u.Assignment]
	ev := st.Evidence[u.Assignment][as.Arm]
	want := UnitRow{Unit: u.Unit, Assignment: u.Assignment, Arm: as.Arm, Evidence: ev.ID, Missing: ev.Missing, Exposed: ev.Exposed == intended(p.Relation, as.Arm)}
	if ev.Missing == MissingNotComplete {
		want.Missing = "" // scored 0 without an evaluation: not achieved
		if row != want {
			return fmt.Errorf("a unit whose run did not complete scores 0 unevaluated (%+v vs %+v)", row, want)
		}
		return nil
	}
	if ev.Missing != "" {
		if row != want {
			return fmt.Errorf("does not carry the evidence's missingness (%+v vs %+v)", row, want)
		}
		return nil
	}
	rs := st.Runs.Arms[as.ID][as.Arm]
	goal, err := store.Get(rs.Goal.Text)
	if err != nil {
		return err
	}
	deliverable, err := store.Get(*ev.Deliverable)
	if err != nil {
		return err
	}
	prompt := thought.Address(thought.Prompt, EvaluatorPrompt(goal, deliverable))
	if row.Missing == MissingUnevaluated {
		want.Missing = MissingUnevaluated
		if row != want {
			return fmt.Errorf("unevaluated row does not match its evidence (%+v vs %+v)", row, want)
		}
		id, _, tries, err := st.evaluation(store, rs, prompt)
		if err != nil {
			return err
		}
		if id != "" || tries < EvaluatorTries {
			return fmt.Errorf("unevaluated after %d evaluate calls (usable: %v); the bound is %d", tries, id != "", EvaluatorTries)
		}
		return nil
	}
	want.Score, want.Evaluation = row.Score, row.Evaluation
	if row != want {
		return fmt.Errorf("does not match its evidence (%+v vs %+v)", row, want)
	}
	for _, a := range rs.Attempts {
		for _, is := range a.Invocations {
			if is.Invocation.ID != row.Evaluation {
				continue
			}
			if is.Invocation.Purpose != invoke.PurposeEvaluate || is.Invocation.Tools || is.Invocation.Seq > before {
				return fmt.Errorf("cites %s, which is not an earlier tool-less evaluate call", row.Evaluation)
			}
			if is.Invocation.Request != prompt {
				return fmt.Errorf("cites an evaluation asked something other than the blinded prompt over the goal and the deliverable")
			}
			if is.Receipt == nil {
				return fmt.Errorf("cites an evaluation with no receipt")
			}
			b, err := store.Get(is.Receipt.Response)
			if err != nil {
				return err
			}
			sc, perr := ParseEvaluation(b)
			if perr != nil || sc != row.Score {
				return fmt.Errorf("score %v is not what the cited evaluation's receipt says (%v, %v)", row.Score, sc, perr)
			}
			return nil
		}
	}
	return fmt.Errorf("cites evaluation %s, which is not an invocation of the unit's run", row.Evaluation)
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
