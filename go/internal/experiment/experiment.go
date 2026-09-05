package experiment

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

var (
	ErrConfig  = errors.New("experiment: configuration")
	ErrRefused = errors.New("experiment: refused")
)

// Spec is what Open takes: the hypothesis, the relation, and either the
// units (paired replay: past production goals of one family, with their
// fixtures) or a population and a cohort size (randomized live: the units
// are the next N production goals of that family, admitted at intake).
// Everything else in the protocol is the vocabulary or derived.
type Spec struct {
	Hypothesis learn.ItemRev
	Relation   Relation
	Units      []UnitSpec
	// Live selects randomized live assignment over Population with N units.
	Live       bool
	Population string
	N          int
	// Expect, live only, chooses the deterministic fixture oracle over the
	// blinded evaluator: the one expected answer (a fixture thought) every
	// unit's deliverable is matched against. The oracle is part of the
	// hypothesis — a lesson that supplies a fact is measured by an oracle
	// that can check the fact.
	Expect *thought.Ref
	// Margin, MinDiscordant, MinPerArm, MinEquivalent: zero ⇒ defaults
	// (0, 1, 2, 2).
	Margin        float64
	MinDiscordant int
	MinPerArm     int
	MinEquivalent int
	Why           string
}

// Open commits the protocol: the units are checked against the production
// fold (terminal, non-replay, non-fork, never themselves an arm, all of
// one family — the population), the hypothesis against the learned fold
// (an existing revision), and the fishing guard against the experiment
// fold (one open experiment per hypothesis and population; a re-open is
// Version+1 citing the prior attestation).
func Open(ctx context.Context, j *journal.Journal, store *thought.Store, spec Spec) (*Experiment, error) {
	st, err := Fold(j, store)
	if err != nil {
		return nil, err
	}
	if spec.MinDiscordant == 0 {
		spec.MinDiscordant = 1
	}
	if spec.MinEquivalent == 0 {
		spec.MinEquivalent = 2
	}
	if spec.MinPerArm == 0 {
		spec.MinPerArm = 2
	}
	id := record.NewID()
	x := &Experiment{Header: record.Header{ID: id, Schema: "experiment/1", Subject: record.Ref{Kind: "experiment", ID: string(id)}, At: now()}, Why: spec.Why}
	x.Protocol = Protocol{Experiment: id, Version: 1, Hypothesis: spec.Hypothesis, Relation: spec.Relation, Arms: Arms(spec.Hypothesis, spec.Relation),
		Outcome: OutcomeSpec{Direction: "higher", Margin: spec.Margin}, Analysis: AnalysisSpec{MinEquivalent: spec.MinEquivalent, Missing: "exclude"}}
	family := ""
	if spec.Live {
		if len(spec.Units) != 0 {
			return nil, fmt.Errorf("%w: a live experiment's units arrive at intake", ErrConfig)
		}
		if !run.KnownFamily(run.FamilyKey(spec.Population)) {
			return nil, fmt.Errorf("%w: population %q is not a family", ErrConfig, spec.Population)
		}
		family = spec.Population
		x.Assignment, x.Oracle, x.Outcome.Dimension, x.N = RandomizedLive, BlindedEvaluator, DimensionAchieved, spec.N
		x.Analysis.Estimator, x.Analysis.MinPerArm = EstimatorArms, spec.MinPerArm
		if spec.Expect != nil {
			// the fold re-executes this check on the record (a forged
			// experiment naming a fixture the store does not hold)
			if err := checkFixture(store, *spec.Expect); err != nil {
				return nil, fmt.Errorf("%w: expect: %v", ErrConfig, err)
			}
			ref := *spec.Expect
			x.Oracle, x.Outcome.Dimension, x.Fixture = DeterministicFixture, Dimension, &ref
		}
	} else {
		if len(spec.Units) == 0 {
			return nil, fmt.Errorf("%w: at least one unit", ErrConfig)
		}
		if spec.Expect != nil {
			return nil, fmt.Errorf("%w: paired replay scores by each unit's fixture, not one expectation", ErrConfig)
		}
		texts := map[string]bool{}
		for _, u := range spec.Units {
			rs := st.RunOf(u.Goal)
			if rs == nil {
				return nil, fmt.Errorf("%w: unit %s is not a run's goal", ErrConfig, u.Goal)
			}
			if texts[rs.Goal.Text.Hash] {
				return nil, fmt.Errorf("%w: unit %s repeats another unit's goal text (one observation, not two)", ErrConfig, u.Goal)
			}
			texts[rs.Goal.Text.Hash] = true
			if err := checkFixture(store, u.Fixture); err != nil {
				return nil, fmt.Errorf("%w: unit %s: %v", ErrConfig, u.Goal, err)
			}
			if rs.Goal.Origin == run.OriginReplay || rs.Goal.Origin == run.OriginFork || rs.Goal.Arm != nil {
				return nil, fmt.Errorf("%w: unit %s is not a plain production goal (origin %s, arm %v)", ErrConfig, u.Goal, rs.Goal.Origin, rs.Goal.Arm != nil)
			}
			if !rs.Terminal() {
				return nil, fmt.Errorf("%w: unit %s is not terminal", ErrConfig, u.Goal)
			}
			f := string(rs.Family.Family)
			if f == string(run.FamilyNone) {
				return nil, fmt.Errorf("%w: unit %s is of no family", ErrConfig, u.Goal)
			}
			if family != "" && f != family {
				return nil, fmt.Errorf("%w: units span families %s and %s", ErrConfig, family, f)
			}
			family = f
		}
		x.Assignment, x.Oracle, x.Outcome.Dimension, x.N, x.Units = PairedReplay, DeterministicFixture, Dimension, len(spec.Units), spec.Units
		x.Analysis.Estimator, x.Analysis.MinDiscordant = Estimator, spec.MinDiscordant
	}
	x.Population = family
	it := st.Runs.Learned.Items[spec.Hypothesis.Item]
	if it == nil || !hasRevision(it, spec.Hypothesis.Revision) {
		return nil, fmt.Errorf("%w: hypothesis %s/%s is not a learned revision", ErrConfig, spec.Hypothesis.Item, spec.Hypothesis.Revision)
	}
	if err := armsDiffer(st.Runs.Learned, spec.Hypothesis, spec.Relation, family); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrConfig, err)
	}
	// the fishing guard, as the fold applies it
	if prior := st.openFor(spec.Hypothesis, family); prior != nil {
		return nil, fmt.Errorf("%w: experiment %s on %s/%s over %s is open; close it first", ErrRefused, prior.ID, spec.Hypothesis.Item, spec.Hypothesis.Revision, family)
	}
	if last := st.lastClosedFor(spec.Hypothesis, family); last != nil {
		att := st.Attestations[last.ID]
		if att == nil {
			return nil, fmt.Errorf("%w: experiment %s is closed but not attested; finish its close first", ErrRefused, last.ID)
		}
		x.Version = last.Version + 1
		x.Prior = att.ID
	}
	if err := x.ValidateWire(); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrConfig, err)
	}
	if _, err := j.Submit(ctx, journal.Command{IdempotencyKey: "experiment/" + string(id) + "/open", Epoch: j.Epoch(), Records: []record.Record{x}}); err != nil {
		return nil, err
	}
	return x, nil
}

// Admit is the intake command's assignment capability (§5, §8a, §9): the
// one production-side reader of the control plane, and it reads it only
// to decide, for a goal being taken in, whether an open live experiment
// of the goal's family claims it. If one does — the first opened, with
// room, whose hypothesis is still the item's current revision — the goal
// is stamped with the arm the keyed randomization names and the
// assignment commits IN THE GOAL'S INTAKE COMMAND at the head this
// decision was folded at. Mutual exclusion is by construction: a goal is
// admitted once, and the fold refuses a second claim on it.
func Admit(j *journal.Journal, store *thought.Store) run.AdmitFunc {
	return func(ctx context.Context, g *run.Goal, fam *run.FamilyAssessment) ([]record.Record, *uint64, error) {
		// NOW-lane roots only: an AGENDA deliverable is a closure rendering,
		// not the answer shape the evaluator scores against the goal
		if !run.KnownFamily(fam.Family) || g.Parent != "" || g.Origin == run.OriginReplay || g.Origin == run.OriginFork || g.Lane != run.LaneNow {
			return nil, nil, nil
		}
		st, err := Fold(j, store)
		if err != nil {
			return nil, nil, err
		}
		head := st.Head
		for _, id := range st.Order {
			x := st.Experiments[id]
			if x.Assignment != RandomizedLive || x.Population != string(fam.Family) || st.Closed[id] != nil || len(st.byUnit[id]) >= x.N {
				continue
			}
			if it := st.Runs.Learned.Items[x.Hypothesis.Item]; it == nil || it.Current.ID != x.Hypothesis.Revision {
				continue // stale: production intake is never blocked by an experiment
			}
			if armsDiffer(st.Runs.Learned, x.Hypothesis, x.Relation, x.Population) != nil {
				continue // degenerate since it opened (staged, overridden): the arms would not differ
			}
			ordinal := len(st.byUnit[id])
			first := ""
			if ordinal%2 == 1 {
				first = st.ordinal(id, ordinal-1).Arm
			}
			seed := Seed(id, g.ID)
			as := &Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(id)}, At: now()},
				Experiment: id, Unit: g.ID, Ordinal: ordinal, Seed: seed, Arm: ArmFor(seed, ordinal, first)}
			g.Arm = x.armRef(as.ID, as.Arm)
			return []record.Record{as}, &head, nil
		}
		// nothing admits: no precondition — a plain goal never waits on
		// unrelated journal traffic
		return nil, nil, nil
	}
}

// armsDiffer is the condition under which an experiment measures anything:
// apply forces a revision production does not select, so the revision
// must not be selectable; ablate withholds one production does select, so
// it must be selectable AND, for a policy item, deciding — withholding it
// must change its mechanism's snapshot over the population (a seed that an
// operator policy already overrides decides nothing, and tombstoning it
// on an "equivalent" would be evidence about nothing).
func armsDiffer(led *learn.Ledger, hyp learn.ItemRev, rel Relation, population string) error {
	it := led.Items[hyp.Item]
	if it == nil {
		return fmt.Errorf("hypothesis %s is not in the learned population", hyp.Item)
	}
	stage := it.StageOf(hyp.Revision)
	switch rel {
	case ApplyItem:
		if learn.Selectable[stage] {
			return fmt.Errorf("apply forces a revision production does not select; %s/%s is %s, so both arms would run with it", hyp.Item, hyp.Revision, stage)
		}
	case AblateItem:
		if !learn.Selectable[stage] {
			return fmt.Errorf("ablate withholds a selectable revision; %s/%s is %s, so both arms would run without it", hyp.Item, hyp.Revision, stage)
		}
		if rev := revisionOf(it, hyp.Revision); rev != nil && rev.LearnedKind == learn.Policy && rev.Policy != nil {
			q := learn.Query{Scope: []learn.ScopePath{learn.ScopeWorkspace}, Family: population, Standing: learn.Selectable}
			base := learn.SelectPolicy(led, q)
			q.Arm = &learn.ArmRef{Arm: Treatment, Withhold: []learn.ItemRev{hyp}}
			without := learn.SelectPolicy(led, q)
			m := rev.Policy.Mechanism
			if base.Snapshot[m] == without.Snapshot[m] {
				return fmt.Errorf("ablate withholds a deciding policy; %s/%s does not decide %s over %s (another selectable policy does), so both arms would run the same", hyp.Item, hyp.Revision, m, population)
			}
		}
	}
	return nil
}

func revisionOf(it *learn.Item, id record.RecordID) *learn.LearnedRevision {
	for _, r := range it.Revisions {
		if r.ID == id {
			return r
		}
	}
	return nil
}

// armRef is the arm's forced sets as the goal and the selections carry them.
func (p *Protocol) armRef(as record.RecordID, arm string) *learn.ArmRef {
	for _, a := range p.Arms {
		if a.Arm == arm {
			return &learn.ArmRef{Assignment: as, Arm: arm, Apply: a.Apply, Withhold: a.Withhold}
		}
	}
	return nil
}

// checkFixture reads the fixture and refuses one the oracle could never
// match: a blank fixture scores every arm 0, and 0 = 0 is "equivalent".
func checkFixture(store *thought.Store, ref thought.Ref) error {
	b, err := store.Get(ref)
	if err != nil {
		return fmt.Errorf("fixture: %w", err)
	}
	if len(bytes.TrimSpace(b)) == 0 {
		return errors.New("fixture is blank")
	}
	return nil
}

// currentAt is the item's current revision at a journal prefix: the last
// revision committed before seq.
func currentAt(it *learn.Item, seq uint64) *learn.LearnedRevision {
	var cur *learn.LearnedRevision
	for _, r := range it.Revisions {
		if r.Seq < seq {
			cur = r
		}
	}
	return cur
}

func hasRevision(it *learn.Item, rev record.RecordID) bool {
	for _, r := range it.Revisions {
		if r.ID == rev {
			return true
		}
	}
	return false
}

// Runner drives the arm runs. Sequential (D6): one arm at a time, in
// protocol order, treatment then control; every commit is keyed, so a
// killed runner resumes exactly where it died and never runs an arm twice.
type Runner struct {
	J       *journal.Journal
	Store   *thought.Store
	Backend invoke.Backend
	Judge   invoke.Backend
	Timeout time.Duration
	Work    string // the replay arms' working directory (run.Driver.Work)
	Events  func(run.Event)
	// CrashAt is forwarded to every arm driver (the kill matrix's seam);
	// production never sets it.
	CrashAt string
}

// Run assigns every unit not yet assigned and drives every arm without
// evidence. It returns when the cohort's evidence is complete, or with the
// first error (the next Run continues).
func (r *Runner) Run(ctx context.Context, exp record.RecordID) error {
	st, err := Fold(r.J, r.Store)
	if err != nil {
		return err
	}
	x := st.Experiments[exp]
	if x == nil {
		return fmt.Errorf("%w: no experiment %s", ErrConfig, exp)
	}
	if st.Closed[exp] != nil {
		return fmt.Errorf("%w: experiment %s is closed", ErrRefused, exp)
	}
	if x.Assignment != PairedReplay {
		return fmt.Errorf("%w: experiment %s is %s: its arms are production runs admitted at intake; the evaluator lane closes it", ErrRefused, exp, x.Assignment)
	}
	for i, u := range x.Units {
		as := st.assignment(exp, u.Goal)
		if as == nil {
			a := &Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(exp)}, At: now()}, Experiment: exp, Unit: u.Goal, Ordinal: i, Seed: Seed(exp, u.Goal)}
			if _, err := r.J.Submit(ctx, journal.Command{IdempotencyKey: fmt.Sprintf("experiment/%s/assign/%d", exp, i), Epoch: r.J.Epoch(), Records: []record.Record{a}}); err != nil {
				return err
			}
			if st, err = Fold(r.J, r.Store); err != nil {
				return err
			}
			as = st.assignment(exp, u.Goal)
		}
		for _, arm := range x.Arms {
			if st.Evidence[as.ID][arm.Arm] != nil {
				continue
			}
			if err := r.arm(ctx, st, x, as, arm); err != nil {
				return err
			}
			if st, err = Fold(r.J, r.Store); err != nil {
				return err
			}
		}
	}
	return nil
}

// arm drives one arm to terminal and commits its evidence: a run already
// terminal only needs the evidence; a non-terminal one is resumed; an
// unstarted replay goal is started; otherwise the arm's goal is taken in.
func (r *Runner) arm(ctx context.Context, st *State, x *Experiment, as *Assignment, arm ArmSpec) error {
	unit := st.RunOf(as.Unit)
	if unit == nil {
		return fmt.Errorf("%w: unit %s has no run", ErrConfig, as.Unit)
	}
	// the hypothesis must still be the item's current revision: recall
	// forces an item at its current revision, so a superseded hypothesis
	// would run an arm that administers nothing (the fold refuses one)
	if it := st.Runs.Learned.Items[x.Hypothesis.Item]; it == nil || it.Current.ID != x.Hypothesis.Revision {
		return fmt.Errorf("%w: hypothesis %s/%s is no longer the item's current revision; the experiment is stale", ErrRefused, x.Hypothesis.Item, x.Hypothesis.Revision)
	}
	// the arm replays the unit under the unit's own execute frame (read
	// from the unit's attempt config, not from this process): a replay
	// whose request differs from the unit's by anything but the arm's
	// applied set measures the difference in framing, not the hypothesis
	frame, err := run.FrameOf(unit.Latest(), r.Store)
	if err != nil {
		return err
	}
	d := &run.Driver{J: r.J, Store: r.Store, Backend: r.Backend, Judge: r.Judge, Lane: unit.Goal.Lane, Origin: run.ReplayOrigin{}, Events: r.Events, Timeout: r.Timeout, CrashAt: r.CrashAt, Frame: frame, Work: r.Work,
		Replay: &run.ReplayContext{Assignment: as.ID, Arm: arm.Arm, Unit: as.Unit, Root: unit.Root, Apply: arm.Apply, Withhold: arm.Withhold}}
	if err := d.Validate(); err != nil {
		return err
	}
	rs := st.Runs.Arms[as.ID][arm.Arm]
	switch {
	case rs != nil && rs.Terminal():
	case rs != nil:
		if _, err := d.ResumeRun(ctx, rs); err != nil {
			return err
		}
	default:
		var g *run.Goal
		for _, u := range st.Runs.Unstarted {
			if u.Arm != nil && u.Arm.Assignment == as.ID && u.Arm.Arm == arm.Arm {
				g = u
			}
		}
		if g != nil {
			if _, err := d.StartGoal(ctx, st.Runs, g); err != nil {
				return err
			}
		} else {
			text, err := r.Store.Get(unit.Goal.Text)
			if err != nil {
				return err
			}
			if _, err := d.Run(ctx, text, unit.Goal.Delivery); err != nil {
				return err
			}
		}
	}
	// evidence, derived from the fold as it now stands
	led, err := run.Fold(r.J.Production(), r.Store)
	if err != nil {
		return err
	}
	rs = led.Arms[as.ID][arm.Arm]
	if rs == nil || !rs.Terminal() {
		return fmt.Errorf("%w: arm %s of %s did not reach terminal", ErrRefused, arm.Arm, as.ID)
	}
	if r.CrashAt == "before_evidence" {
		return fmt.Errorf("%w: before_evidence", run.ErrCrashed)
	}
	ev := EvidenceOf(rs, x.Hypothesis, x.ID, as.ID, as.Unit, arm.Arm)
	_, err = r.J.Submit(ctx, journal.Command{IdempotencyKey: fmt.Sprintf("experiment/%s/evidence/%s/%s", x.ID, as.ID, arm.Arm), Epoch: r.J.Epoch(), Records: []record.Record{ev}})
	return err
}

// EvidenceOf derives an arm run's evidence from the run fold (§19.1), all
// of it over the TERMINAL attempt: exposure (the attempt's recall selection
// included the hypothesis revision, or its policy selection enabled it —
// both selections are what the fold verified the request against), the
// deliverable (only a complete outcome's: a failed or partial run's
// payload is the framework's envelope, which no oracle may score), and
// the artifact root. The verifier recomputes it the same way.
func EvidenceOf(rs *run.RunState, hyp learn.ItemRev, exp, as, unit record.RecordID, arm string) *UnitEvidence {
	a := rs.Latest()
	n := a.Attempt.Attempt
	ev := &UnitEvidence{Header: record.Header{ID: record.NewID(), Schema: "unit_evidence/1", RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()},
		Assignment: as, Experiment: exp, Unit: unit, Arm: arm, Exposed: exposed(a, hyp)}
	rec := a.Has(run.Recorded)
	switch {
	case rec == nil || rec.Outcome == nil || rec.Outcome.Terminal != invoke.TerminalComplete:
		ev.Missing = MissingNotComplete
	case a.Delivery != nil && a.Delivery.Prepared != nil:
		p := a.Delivery.Prepared.Payload
		ev.Deliverable = &p
	default:
		ev.Missing = MissingNoDeliverable
	}
	ev.ArtifactRoot = artifactRoot(rs, a, ev)
	return ev
}

// exposed: the terminal attempt ran under a recall selection that included
// the hypothesis revision, or a policy selection that enabled it.
func exposed(a *run.AttemptState, hyp learn.ItemRev) bool {
	if a.Recall != nil {
		for _, ir := range a.Recall.Included {
			if ir == hyp {
				return true
			}
		}
	}
	if a.Policy != nil {
		for _, ir := range a.Policy.Enabled {
			if ir == hyp {
				return true
			}
		}
	}
	return false
}

func artifactRoot(rs *run.RunState, a *run.AttemptState, ev *UnitEvidence) string {
	var term, rec, recall, policy record.RecordID
	for _, t := range a.Transitions {
		if t.To == run.Delivered || t.To == run.DeliveryFailedS {
			term = t.ID
			break
		}
	}
	var o run.Outcome
	if t := a.Has(run.Recorded); t != nil && t.Outcome != nil {
		rec, o = t.ID, *t.Outcome
	}
	if a.Recall != nil {
		recall = a.Recall.ID
	}
	if a.Policy != nil {
		policy = a.Policy.ID
	}
	deliverable := ""
	if ev.Deliverable != nil {
		deliverable = ev.Deliverable.Hash
	}
	raw, _ := json.Marshal(struct {
		Run         record.RunID    `json:"run"`
		Attempt     uint32          `json:"attempt"`
		Terminal    record.RecordID `json:"terminal"`
		Recorded    record.RecordID `json:"recorded"`
		Invocation  record.RecordID `json:"invocation"`
		Receipt     record.RecordID `json:"receipt"`
		Closure     record.RecordID `json:"closure"`
		Recall      record.RecordID `json:"recall"`
		Policy      record.RecordID `json:"policy"`
		Deliverable string          `json:"deliverable"`
		Exposed     bool            `json:"exposed"`
		Missing     string          `json:"missing"`
	}{rs.Run, a.Attempt.Attempt, term, rec, o.Invocation, o.Receipt, o.Closure, recall, policy, deliverable, ev.Exposed, ev.Missing})
	return digest("artifact", string(raw))
}

// Score is the deterministic fixture oracle (fixture/1): 1 when the
// deliverable contains the fixture text (case-insensitive, whitespace
// trimmed), else 0. It sees the deliverable and the fixture and nothing
// else — the hypothesis text is not an input, so it is blinded by
// construction.
func Score(deliverable, fixture []byte) float64 {
	f := bytes.ToLower(bytes.TrimSpace(fixture))
	if len(f) == 0 {
		return 0
	}
	if bytes.Contains(bytes.ToLower(deliverable), f) {
		return 1
	}
	return 0
}

// intended is the exposure each arm must show for its pair to count as
// per-protocol: under apply_item the treatment is exposed and the control
// is not; under ablate_item the treatment is not and the control is (an
// ablation of an item production would not have selected measures
// nothing).
func intended(rel Relation, arm string) bool {
	return (rel == ApplyItem) == (arm == Treatment)
}

// Close closes the cohort and measures it: one command commits Closed and
// the CohortCommitment; then the attestation; then the measurement; then
// the lifecycle transition StageFor derives (when the effect moves the
// revision). Every commit is keyed, so a Close killed midway finishes on
// the next call.
func Close(ctx context.Context, j *journal.Journal, store *thought.Store, exp record.RecordID) (*EffectMeasurement, error) {
	return (&Closer{J: j, Store: store}).Close(ctx, exp)
}

// Closer is Close with what a live cohort needs — the blinded judge that
// scores each unit (tool-less; its calls are the evaluator's own
// invocations) — and the kill matrix's seam: CrashAt stops it dead after
// the named commit (close | attest | measure). Production never sets it.
type Closer struct {
	J       *journal.Journal
	Store   *thought.Store
	Judge   invoke.Backend
	Timeout time.Duration
	Events  func(string)
	CrashAt string
	// EvaluatorVersion names the blinded evaluator this closer asks
	// (EvaluatorJudge when empty); the attestation carries it.
	EvaluatorVersion string
}

func (c *Closer) evaluator() string {
	if c.EvaluatorVersion == "" {
		return EvaluatorJudge
	}
	return c.EvaluatorVersion
}

// EvaluatorTries bounds the evaluate calls for one unit before its row is
// `unevaluated`. Why 3: as the tail's lens — one failure is a blip, three
// in a row is the backend's answer, and the unit leaves the analysis
// rather than holding the cohort open forever.
const EvaluatorTries = 3

// Evidence commits the evidence a live experiment's terminal units are
// owed (keyed, idempotent) and reports how many units still lack it.
func (c *Closer) Evidence(ctx context.Context, exp record.RecordID) (owed int, err error) {
	st, err := Fold(c.J, c.Store)
	if err != nil {
		return 0, err
	}
	x := st.Experiments[exp]
	if x == nil {
		return 0, fmt.Errorf("%w: no experiment %s", ErrConfig, exp)
	}
	for _, as := range st.byUnit[exp] {
		if st.Evidence[as.ID][as.Arm] != nil {
			continue
		}
		rs := st.Runs.Arms[as.ID][as.Arm]
		if rs == nil || !rs.Terminal() {
			owed++
			continue
		}
		ev := EvidenceOf(rs, x.Hypothesis, exp, as.ID, as.Unit, as.Arm)
		if _, err := c.J.Submit(ctx, journal.Command{IdempotencyKey: fmt.Sprintf("experiment/%s/evidence/%s/%s", exp, as.ID, as.Arm), Epoch: c.J.Epoch(), Records: []record.Record{ev}}); err != nil {
			return 0, err
		}
	}
	return owed + (x.N - len(st.byUnit[exp])), nil
}

// Close: see the package function.
func (c *Closer) Close(ctx context.Context, exp record.RecordID) (*EffectMeasurement, error) {
	j, store := c.J, c.Store
	st, err := Fold(j, store)
	if err != nil {
		return nil, err
	}
	x := st.Experiments[exp]
	if x == nil {
		return nil, fmt.Errorf("%w: no experiment %s", ErrConfig, exp)
	}
	sub := record.Ref{Kind: "experiment", ID: string(exp)}
	commit := func(key string, recs ...record.Record) error {
		if _, err := j.Submit(ctx, journal.Command{IdempotencyKey: "experiment/" + string(exp) + "/" + key, Epoch: j.Epoch(), Records: recs}); err != nil {
			return err
		}
		if c.CrashAt == key {
			return fmt.Errorf("%w: %s", run.ErrCrashed, key)
		}
		return nil
	}
	refold := func() error {
		st, err = Fold(j, store)
		return err
	}
	if st.Closed[exp] == nil {
		if x.Assignment == RandomizedLive {
			if owed, err := c.Evidence(ctx, exp); err != nil {
				return nil, err
			} else if owed > 0 {
				return nil, fmt.Errorf("%w: %d of %d live units are not yet assigned or terminal", ErrRefused, owed, x.N)
			}
			if err := refold(); err != nil {
				return nil, err
			}
		}
		units := make([]AssignedUnit, 0, x.N)
		for i := 0; i < x.N; i++ {
			as := st.ordinal(exp, i)
			if as == nil {
				return nil, fmt.Errorf("%w: unit %d is not assigned", ErrRefused, i)
			}
			for _, arm := range x.armsOf(as) {
				if st.Evidence[as.ID][arm] == nil {
					return nil, fmt.Errorf("%w: unit %d (%s) has no %s evidence", ErrRefused, i, as.Unit, arm)
				}
			}
			units = append(units, AssignedUnit{Unit: as.Unit, Assignment: as.ID, Ordinal: i, Seed: as.Seed})
		}
		cm := &CohortCommitment{Header: record.Header{ID: record.NewID(), Schema: "cohort_commitment/1", Subject: sub, At: now()}, Experiment: exp, Protocol: x.Protocol, Units: units, Root: CohortRoot(units), Count: len(units)}
		cl := &Closed{Header: record.Header{ID: record.NewID(), Schema: "cohort_closed/1", Subject: sub, At: now()}, Experiment: exp, Commitment: cm.ID, Count: len(units)}
		if err := commit("close", cl, cm); err != nil {
			return nil, err
		}
		if err := refold(); err != nil {
			return nil, err
		}
	}
	cm := st.Commitments[exp]
	if st.Attestations[exp] == nil {
		att := &EffectAttestation{Header: record.Header{ID: record.NewID(), Schema: "effect_attestation/1", Subject: sub, At: now()}, Experiment: exp, Cohort: cm.ID, Closure: st.Closed[exp].ID, Protocol: cm.Protocol, Evaluator: Evaluator, Estimator: Estimator}
		if cm.Protocol.Assignment == RandomizedLive {
			att.Evaluator, att.Estimator = c.evaluator(), EstimatorArms
			if cm.Protocol.Oracle == DeterministicFixture {
				att.Evaluator = Evaluator
			}
		}
		for i, u := range cm.Units {
			var row UnitRow
			var err error
			if cm.Protocol.Assignment == RandomizedLive {
				row, err = c.liveRow(ctx, st, cm.Protocol, i, u)
			} else {
				row, err = st.row(store, cm.Protocol, i, u)
			}
			if err != nil {
				return nil, err
			}
			att.Units = append(att.Units, row)
		}
		if err := commit("attest", att); err != nil {
			return nil, err
		}
		if err := refold(); err != nil {
			return nil, err
		}
	}
	att := st.Attestations[exp]
	if st.Measurements[exp] == nil {
		m := Measure(att)
		m.Header = record.Header{ID: record.NewID(), Schema: "effect_measurement/1", Subject: sub, At: now()}
		if err := commit("measure", m); err != nil {
			return nil, err
		}
		if err := refold(); err != nil {
			return nil, err
		}
	}
	m := st.Measurements[exp]
	// the transition the measurement derives, if the ledger has not seen it
	it := st.Runs.Learned.Items[m.Hypothesis.Item]
	if it == nil {
		return nil, fmt.Errorf("%w: hypothesis item %s is not in the learned population", ErrRefused, m.Hypothesis.Item)
	}
	if cited(it, m.ID) {
		return m, nil
	}
	from := it.StageOf(m.Hypothesis.Revision)
	to, ok := learn.StageFor(from, m.ItemEffect)
	if !ok {
		return m, nil
	}
	tr := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(m.Hypothesis.Item)}, At: now()},
		Item: m.Hypothesis.Item, Revision: m.Hypothesis.Revision, From: from, To: to, Actor: learn.ActorMeasurement, Evidence: m.ID,
		Why: fmt.Sprintf("experiment %s v%d: %s (%s; delta_pp %.3f over %d exposed pairs, %d discordant)", exp, x.Version, m.ItemEffect, m.Verdict, m.DeltaPP, m.Exposed, m.Discordant)}
	if err := commit("transition", tr); err != nil {
		return nil, err
	}
	return m, nil
}

func cited(it *learn.Item, evidence record.RecordID) bool {
	for _, trs := range it.Transitions {
		for _, tr := range trs {
			if tr.Evidence == evidence {
				return true
			}
		}
	}
	return false
}

// row builds one attestation row from the pair's evidence: the scores from
// the oracle over each deliverable and the unit's fixture; missingness and
// per-protocol exposure carried over.
func (st *State) row(store *thought.Store, p Protocol, i int, u AssignedUnit) (UnitRow, error) {
	evs := st.Evidence[u.Assignment]
	t, c := evs[Treatment], evs[Control]
	if t == nil || c == nil {
		return UnitRow{}, fmt.Errorf("%w: unit %d has no complete pair", ErrRefused, i)
	}
	row := UnitRow{Unit: u.Unit, Assignment: u.Assignment, Treatment: t.ID, Control: c.ID, TreatmentMissing: t.Missing, ControlMissing: c.Missing,
		Exposed: t.Exposed == intended(p.Relation, Treatment) && c.Exposed == intended(p.Relation, Control)}
	fixture, err := store.Get(p.Units[i].Fixture)
	if err != nil {
		return UnitRow{}, err
	}
	score := func(ev *UnitEvidence) (float64, error) {
		if ev.Deliverable == nil {
			return 0, nil
		}
		b, err := store.Get(*ev.Deliverable)
		if err != nil {
			return 0, err
		}
		return Score(b, fixture), nil
	}
	if row.TreatmentScore, err = score(t); err != nil {
		return UnitRow{}, err
	}
	if row.ControlScore, err = score(c); err != nil {
		return UnitRow{}, err
	}
	return row, nil
}

// Measure is the estimator paired_diff/1 as a pure fold over the
// attestation: analyzed = pairs with both outcomes; ITT = mean(T−C) over
// them; per-protocol over the analyzed pairs whose exposure held;
// discordant = per-protocol pairs with T≠C. The verdict is
// treatment_helpful when delta_pp exceeds the margin with enough discordant
// pairs, treatment_harmful when it falls below −margin likewise,
// equivalent when |delta_pp| is within the margin over enough exposed
// pairs with no discordance, else insufficient. The header is the
// caller's.
func Measure(att *EffectAttestation) *EffectMeasurement {
	p := att.Protocol
	m := &EffectMeasurement{Experiment: att.Experiment, Attestation: att.ID, Hypothesis: p.Hypothesis, Relation: p.Relation, Assigned: len(att.Units)}
	if p.Assignment == RandomizedLive {
		return measureLive(m, att)
	}
	var itt, pp float64
	for _, u := range att.Units {
		if u.TreatmentMissing != "" || u.ControlMissing != "" {
			continue
		}
		m.Analyzed++
		d := u.TreatmentScore - u.ControlScore
		itt += d
		if u.Exposed {
			m.Exposed++
			pp += d
			if d != 0 {
				m.Discordant++
			}
		}
	}
	if m.Analyzed > 0 {
		m.DeltaITT = itt / float64(m.Analyzed)
	}
	if m.Exposed > 0 {
		m.DeltaPP = pp / float64(m.Exposed)
	}
	margin := p.Outcome.Margin
	switch {
	case m.DeltaPP > margin && m.Discordant >= p.Analysis.MinDiscordant:
		m.Verdict = TreatmentHelpful
	case m.DeltaPP < -margin && m.Discordant >= p.Analysis.MinDiscordant:
		m.Verdict = TreatmentHarmful
	case math.Abs(m.DeltaPP) <= margin && m.Exposed >= p.Analysis.MinEquivalent && m.Discordant == 0:
		m.Verdict = Equivalent
	default:
		m.Verdict = Insufficient
	}
	m.ItemEffect = Normalize(m.Verdict, p.Relation)
	return m
}

// measureLive is the estimator arm_diff/1: analyzed = units with a
// score; ITT delta = mean(treatment) − mean(control) over them (0 when an
// arm is empty); per-protocol likewise over the units whose exposure held;
// treatment_helpful when delta_pp exceeds the margin with at least
// min_per_arm exposed units in EACH arm, treatment_harmful when it falls
// below −margin likewise, equivalent when |delta_pp| is within the margin
// with at least min_equivalent per arm, else insufficient. No interval:
// the per-arm minimums are the predeclared uncertainty control (v1).
func measureLive(m *EffectMeasurement, att *EffectAttestation) *EffectMeasurement {
	p := att.Protocol
	var sum, sumPP [2]float64
	var n, nPP [2]int
	idx := func(arm string) int {
		if arm == Treatment {
			return 0
		}
		return 1
	}
	for _, u := range att.Units {
		if u.Missing == MissingUnjudgeable {
			m.Unjudgeable++
		}
		if u.Missing != "" {
			continue
		}
		m.Analyzed++
		i := idx(u.Arm)
		n[i]++
		sum[i] += u.Score
		if u.Exposed {
			m.Exposed++
			nPP[i]++
			sumPP[i] += u.Score
		}
	}
	m.TreatmentN, m.ControlN = nPP[0], nPP[1]
	mean := func(s float64, k int) float64 {
		if k == 0 {
			return 0
		}
		return s / float64(k)
	}
	if n[0] > 0 && n[1] > 0 {
		m.DeltaITT = mean(sum[0], n[0]) - mean(sum[1], n[1])
	}
	if nPP[0] > 0 && nPP[1] > 0 {
		m.DeltaPP = mean(sumPP[0], nPP[0]) - mean(sumPP[1], nPP[1])
	}
	least := nPP[0]
	if nPP[1] < least {
		least = nPP[1]
	}
	margin := p.Outcome.Margin
	switch {
	case least >= p.Analysis.MinPerArm && m.DeltaPP > margin:
		m.Verdict = TreatmentHelpful
	case least >= p.Analysis.MinPerArm && m.DeltaPP < -margin:
		m.Verdict = TreatmentHarmful
	case least >= p.Analysis.MinEquivalent && math.Abs(m.DeltaPP) <= margin:
		m.Verdict = Equivalent
	default:
		m.Verdict = Insufficient
	}
	m.ItemEffect = Normalize(m.Verdict, p.Relation)
	return m
}

// armsOf: the arms a unit runs — both for paired replay, its one assigned
// arm for randomized live.
func (p *Protocol) armsOf(as *Assignment) []string {
	if p.Assignment == RandomizedLive {
		return []string{as.Arm}
	}
	return []string{Treatment, Control}
}

// EvaluatorPrompt is the blinded judge's request, rendered from the unit's
// goal and the arm's deliverable and NOTHING else — no lesson text, no
// arm, no hypothesis — so the verifier can re-render it from the evidence
// and refuse an evaluation that was asked anything more. It has a third
// answer, unjudgeable: the evaluator is matched to what it can judge from
// the text, and says so when it cannot, instead of guessing not_achieved
// for a fact it was never shown (the acceptance run's tombstones).
func EvaluatorPrompt(goal, deliverable []byte) []byte {
	var b bytes.Buffer
	b.WriteString("You are a blinded evaluator. Decide whether the deliverable achieves the goal. Judge only what is written; do not assume context. ")
	b.WriteString("If achievement turns on a fact, file, or artifact you are not shown — so that the text alone cannot settle whether the goal was achieved — answer unjudgeable; never guess.\n\n")
	b.WriteString("Goal:\n")
	b.Write(bytes.TrimSpace(goal))
	b.WriteString("\n\nDeliverable:\n")
	b.Write(bytes.TrimSpace(deliverable))
	b.WriteString("\n\nReply with JSON only: {\"outcome\":\"achieved\"|\"not_achieved\"|\"unjudgeable\",\"confidence\":0..1,\"why\":\"one sentence\"}\n")
	return b.Bytes()
}

// EvaluatorPromptV1 is judge/1's request, frozen: two answers. Kept so an
// attestation that names judge/1 verifies against the bytes it was asked.
func EvaluatorPromptV1(goal, deliverable []byte) []byte {
	var b bytes.Buffer
	b.WriteString("You are a blinded evaluator. Decide whether the deliverable achieves the goal. Judge only what is written; do not assume context.\n\n")
	b.WriteString("Goal:\n")
	b.Write(bytes.TrimSpace(goal))
	b.WriteString("\n\nDeliverable:\n")
	b.Write(bytes.TrimSpace(deliverable))
	b.WriteString("\n\nReply with JSON only: {\"outcome\":\"achieved\"|\"not_achieved\",\"confidence\":0..1,\"why\":\"one sentence\"}\n")
	return b.Bytes()
}

// evaluatorPrompt renders the request for a named evaluator version.
func evaluatorPrompt(version string, goal, deliverable []byte) ([]byte, error) {
	switch version {
	case EvaluatorJudge:
		return EvaluatorPrompt(goal, deliverable), nil
	case EvaluatorJudgeV1:
		return EvaluatorPromptV1(goal, deliverable), nil
	}
	return nil, fmt.Errorf("evaluator %q is not a version this engine knows", version)
}

// EvalUnjudgeable is the evaluator's third answer.
const EvalUnjudgeable = "unjudgeable"

// ParseEvaluation reads judge/2's answer: 1 for achieved, 0 for
// not_achieved, both judged; unjudgeable is a usable answer that scores
// nothing (judged false); anything else is unusable.
func ParseEvaluation(response []byte) (score float64, judged bool, err error) {
	return parseEvaluation(EvaluatorJudge, response)
}

// parseEvaluation reads the answer by the evaluator's version: judge/1
// knows two answers, so an unjudgeable reply to it is unusable.
func parseEvaluation(version string, response []byte) (score float64, judged bool, err error) {
	allowed := []string{"achieved", "not_achieved", EvalUnjudgeable}
	if version == EvaluatorJudgeV1 {
		allowed = allowed[:2]
	}
	r, err := run.ParseJudge(response, allowed...)
	if err != nil {
		return 0, false, err
	}
	switch r.Outcome {
	case "achieved":
		return 1, true, nil
	case "not_achieved":
		return 0, true, nil
	}
	return 0, false, nil
}

// evaluation finds a usable evaluate invocation for the unit among the
// arm run's invocations: purpose evaluate, tool-less, asked EXACTLY the
// blinded prompt, with a receipt that parses. It also counts every
// evaluate call made (usable or not) so the tries bound and the
// unevaluated verdict re-derive.
func (st *State) evaluation(store *thought.Store, rs *run.RunState, version string, want thought.Ref) (id record.RecordID, score float64, judged bool, tries int, err error) {
	for _, a := range rs.Attempts {
		for _, is := range a.Invocations {
			if is.Invocation.Purpose != invoke.PurposeEvaluate {
				continue
			}
			tries++
			if id != "" || is.Invocation.Tools || is.Invocation.Request != want || is.Receipt == nil {
				continue
			}
			b, err := store.Get(is.Receipt.Response)
			if err != nil {
				return "", 0, false, tries, err
			}
			if sc, jd, perr := parseEvaluation(version, b); perr == nil {
				id, score, judged = is.Invocation.ID, sc, jd
			}
		}
	}
	return id, score, judged, tries, nil
}

// liveRow builds one live attestation row: the unit's evidence; if it has
// a deliverable, the blinded judge's score from a usable evaluate
// invocation — reused when one exists, made now otherwise, up to
// EvaluatorTries — else unevaluated.
func (c *Closer) liveRow(ctx context.Context, st *State, p Protocol, i int, u AssignedUnit) (UnitRow, error) {
	as := st.Assignments[u.Assignment]
	ev := st.Evidence[u.Assignment][as.Arm]
	if ev == nil {
		return UnitRow{}, fmt.Errorf("%w: unit %d has no evidence", ErrRefused, i)
	}
	row := UnitRow{Unit: u.Unit, Assignment: u.Assignment, Arm: as.Arm, Evidence: ev.ID, Missing: ev.Missing, Exposed: ev.Exposed == intended(p.Relation, as.Arm)}
	if ev.Missing == MissingNotComplete {
		// the run did not complete: the goal was not achieved. An outcome,
		// not a gap — a treatment that makes runs fail is harmful, and a
		// row that vanished would hide it (post-treatment selection)
		row.Missing = ""
		return row, nil
	}
	if ev.Missing != "" {
		return row, nil
	}
	rs := st.Runs.Arms[as.ID][as.Arm]
	goal, err := c.Store.Get(rs.Goal.Text)
	if err != nil {
		return UnitRow{}, err
	}
	deliverable, err := c.Store.Get(*ev.Deliverable)
	if err != nil {
		return UnitRow{}, err
	}
	if p.Oracle == DeterministicFixture {
		// the protocol's fixture over the deliverable: no evaluator, no
		// missingness beyond the evidence's
		fixture, err := c.Store.Get(*p.Fixture)
		if err != nil {
			return UnitRow{}, err
		}
		row.Score = Score(deliverable, fixture)
		return row, nil
	}
	prompt, err := evaluatorPrompt(c.evaluator(), goal, deliverable)
	if err != nil {
		return UnitRow{}, fmt.Errorf("%w: %v", ErrConfig, err)
	}
	want := thought.Address(thought.Prompt, prompt)
	id, score, judged, tries, err := st.evaluation(c.Store, rs, c.evaluator(), want)
	if err != nil {
		return UnitRow{}, err
	}
	for id == "" && tries < EvaluatorTries {
		if c.Judge == nil {
			return UnitRow{}, fmt.Errorf("%w: a live cohort needs a judge to score unit %d", ErrConfig, i)
		}
		sh := &invoke.Shell{J: c.J, Store: c.Store, Run: rs.Run, Attempt: ev.Attempt}
		o, err := sh.Invoke(ctx, c.Judge, invoke.Request{Purpose: invoke.PurposeEvaluate, Prompt: prompt, Tools: false, Timeout: c.Timeout}, nil)
		if err != nil && !(o != nil && o.Terminal == invoke.TerminalFailed && o.Err == nil) {
			return UnitRow{}, err
		}
		tries++
		if o.Terminal != invoke.TerminalFailed {
			if sc, jd, perr := parseEvaluation(c.evaluator(), o.Response); perr == nil {
				id, score, judged = o.Invocation, sc, jd
			} else if c.Events != nil {
				c.Events(fmt.Sprintf("evaluator: unit %d try %d unusable: %v", i, tries, perr))
			}
		} else if c.Events != nil {
			c.Events(fmt.Sprintf("evaluator: unit %d try %d failed: %s", i, tries, o.Reason))
		}
	}
	if id == "" {
		row.Missing = MissingUnevaluated
		return row, nil
	}
	if !judged {
		// the evaluator's competence, recorded: excluded from analysis,
		// counted by the measurement, cited so the verifier can re-read it
		row.Missing, row.Evaluation = MissingUnjudgeable, id
		return row, nil
	}
	row.Score, row.Evaluation = score, id
	return row, nil
}
