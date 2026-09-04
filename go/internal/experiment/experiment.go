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

// Spec is what Open takes: the hypothesis, the relation, and the units
// (past production goals of one family) with their fixtures. Everything
// else in the protocol is the v1 vocabulary or derived.
type Spec struct {
	Hypothesis learn.ItemRev
	Relation   Relation
	Units      []UnitSpec
	// Margin, MinDiscordant, MinEquivalent: zero ⇒ defaults (0, 1, 2).
	Margin        float64
	MinDiscordant int
	MinEquivalent int
	Why           string
}

// Open commits the protocol: the units are checked against the production
// fold (terminal, non-replay, non-fork, all of one family — the
// population), the hypothesis against the learned fold (an existing
// revision), and the fishing guard against the experiment fold (one open
// experiment per hypothesis and population; a re-open is Version+1 citing
// the prior attestation).
func Open(ctx context.Context, j *journal.Journal, store *thought.Store, spec Spec) (*Experiment, error) {
	st, err := Fold(j, store)
	if err != nil {
		return nil, err
	}
	if len(spec.Units) == 0 {
		return nil, fmt.Errorf("%w: at least one unit", ErrConfig)
	}
	family := ""
	for _, u := range spec.Units {
		rs := st.RunOf(u.Goal)
		if rs == nil {
			return nil, fmt.Errorf("%w: unit %s is not a run's goal", ErrConfig, u.Goal)
		}
		if rs.Goal.Origin == run.OriginReplay || rs.Goal.Origin == run.OriginFork {
			return nil, fmt.Errorf("%w: unit %s is a %s goal, not production", ErrConfig, u.Goal, rs.Goal.Origin)
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
	it := st.Runs.Learned.Items[spec.Hypothesis.Item]
	if it == nil || !hasRevision(it, spec.Hypothesis.Revision) {
		return nil, fmt.Errorf("%w: hypothesis %s/%s is not a learned revision", ErrConfig, spec.Hypothesis.Item, spec.Hypothesis.Revision)
	}
	if spec.MinDiscordant == 0 {
		spec.MinDiscordant = 1
	}
	if spec.MinEquivalent == 0 {
		spec.MinEquivalent = 2
	}
	id := record.NewID()
	x := &Experiment{Header: record.Header{ID: id, Schema: "experiment/1", Subject: record.Ref{Kind: "experiment", ID: string(id)}, At: now()}, Why: spec.Why}
	x.Protocol = Protocol{Experiment: id, Version: 1, Hypothesis: spec.Hypothesis, Relation: spec.Relation, Population: family, Assignment: PairedReplay,
		Arms: Arms(spec.Hypothesis, spec.Relation), Outcome: OutcomeSpec{Dimension: Dimension, Direction: "higher", Margin: spec.Margin}, N: len(spec.Units),
		Analysis: AnalysisSpec{Estimator: Estimator, MinDiscordant: spec.MinDiscordant, MinEquivalent: spec.MinEquivalent, Missing: "exclude"}, Oracle: DeterministicFixture, Units: spec.Units}
	// the fishing guard, as the fold applies it
	if prior := st.openFor(spec.Hypothesis, family); prior != nil {
		return nil, fmt.Errorf("%w: experiment %s on %s/%s over %s is open; close it first", ErrRefused, prior.ID, spec.Hypothesis.Item, spec.Hypothesis.Revision, family)
	}
	if last := st.lastClosedFor(spec.Hypothesis, family); last != nil {
		x.Version = last.Version + 1
		x.Prior = st.Attestations[last.ID].ID
	}
	if err := x.ValidateWire(); err != nil {
		return nil, err
	}
	if _, err := j.Submit(ctx, journal.Command{IdempotencyKey: "experiment/" + string(id) + "/open", Epoch: j.Epoch(), Records: []record.Record{x}}); err != nil {
		return nil, err
	}
	return x, nil
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
	d := &run.Driver{J: r.J, Store: r.Store, Backend: r.Backend, Judge: r.Judge, Lane: unit.Goal.Lane, Origin: run.ReplayOrigin{}, Events: r.Events, Timeout: r.Timeout, CrashAt: r.CrashAt,
		Replay: &run.ReplayContext{Assignment: as.ID, Arm: arm.Arm, Unit: as.Unit, Root: unit.Goal.Root, Apply: arm.Apply, Withhold: arm.Withhold}}
	if err := d.Validate(); err != nil {
		return err
	}
	rs := st.Runs.Replays[as.ID][arm.Arm]
	switch {
	case rs != nil && rs.Terminal():
	case rs != nil:
		if _, err := d.ResumeRun(ctx, rs); err != nil {
			return err
		}
	default:
		var g *run.Goal
		for _, u := range st.Runs.Unstarted {
			if u.Replay != nil && u.Replay.Assignment == as.ID && u.Replay.Arm == arm.Arm {
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
	rs = led.Replays[as.ID][arm.Arm]
	if rs == nil || !rs.Terminal() {
		return fmt.Errorf("%w: arm %s of %s did not reach terminal", ErrRefused, arm.Arm, as.ID)
	}
	ev := EvidenceOf(rs, led.Learned, x.Hypothesis, x.ID, as.ID, as.Unit, arm.Arm)
	_, err = r.J.Submit(ctx, journal.Command{IdempotencyKey: fmt.Sprintf("experiment/%s/evidence/%s/%s", x.ID, as.ID, arm.Arm), Epoch: r.J.Epoch(), Records: []record.Record{ev}})
	return err
}

// EvidenceOf derives an arm run's evidence from the run fold (§19.1): the
// hypothesis revision's exposure (an application or policy application in
// the terminal attempt's run), the deliverable the run prepared, and the
// artifact root. The verifier recomputes it the same way.
func EvidenceOf(rs *run.RunState, learned *learn.Ledger, hyp learn.ItemRev, exp, as, unit record.RecordID, arm string) *UnitEvidence {
	a := rs.Latest()
	n := a.Attempt.Attempt
	ev := &UnitEvidence{Header: record.Header{ID: record.NewID(), Schema: "unit_evidence/1", RunID: rs.Run, Attempt: n, Subject: record.Ref{Kind: "run", ID: string(rs.Run)}, At: now()},
		Assignment: as, Experiment: exp, Unit: unit, Arm: arm, Exposed: exposed(rs, learned, hyp)}
	if a.Delivery != nil && a.Delivery.Prepared != nil {
		p := a.Delivery.Prepared.Payload
		ev.Deliverable = &p
	} else {
		ev.Missing = MissingNoDeliverable
	}
	ev.ArtifactRoot = artifactRoot(rs, a, ev)
	return ev
}

// exposed: the hypothesis revision reached the run — an Application on any
// of its invocations, or a PolicyApplication on any of its policy
// selections.
func exposed(rs *run.RunState, learned *learn.Ledger, hyp learn.ItemRev) bool {
	for _, a := range rs.Attempts {
		for _, st := range a.Invocations {
			for _, ap := range learned.Applications[st.Invocation.ID] {
				if ap.Item == hyp.Item && ap.Revision == hyp.Revision {
					return true
				}
			}
		}
		if a.Policy != nil {
			for _, pa := range learned.PolicyApps[a.Policy.ID] {
				if pa.Item == hyp.Item && pa.Revision == hyp.Revision {
					return true
				}
			}
		}
	}
	return false
}

func artifactRoot(rs *run.RunState, a *run.AttemptState, ev *UnitEvidence) string {
	var term, rec record.RecordID
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
		Deliverable string          `json:"deliverable"`
		Exposed     bool            `json:"exposed"`
	}{rs.Run, a.Attempt.Attempt, term, rec, o.Invocation, o.Receipt, o.Closure, deliverable, ev.Exposed})
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
		_, err := j.Submit(ctx, journal.Command{IdempotencyKey: "experiment/" + string(exp) + "/" + key, Epoch: j.Epoch(), Records: recs})
		return err
	}
	refold := func() error {
		st, err = Fold(j, store)
		return err
	}
	if st.Closed[exp] == nil {
		units := make([]AssignedUnit, 0, x.N)
		for i, u := range x.Units {
			as := st.assignment(exp, u.Goal)
			if as == nil {
				return nil, fmt.Errorf("%w: unit %d (%s) is not assigned", ErrRefused, i, u.Goal)
			}
			for _, arm := range x.Arms {
				if st.Evidence[as.ID][arm.Arm] == nil {
					return nil, fmt.Errorf("%w: unit %d (%s) has no %s evidence", ErrRefused, i, u.Goal, arm.Arm)
				}
			}
			units = append(units, AssignedUnit{Unit: u.Goal, Assignment: as.ID, Ordinal: i, Seed: as.Seed})
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
		for i, u := range cm.Units {
			row, err := st.row(store, cm.Protocol, i, u)
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
