// Package experiment is the measured loop's unit (§8a, §9): an immutable
// protocol in the control envelope, assignments of units to it, the arm
// runs' evidence in production, one closure, one attestation, and a
// measurement that is a deterministic fold over the attestation — the ONE
// thing a lifecycle transition may cite as measurement evidence.
//
// Two assignment kinds. Paired replay: every unit (a past production goal
// of the population family) is re-run in both arms, which differ by
// exactly the hypothesis; the oracle is a deterministic fixture per unit.
// Randomized live: production goals of the population are admitted at
// intake, one arm each by keyed randomization in permuted blocks of two,
// and a blinded evaluator scores each unit's deliverable against its goal
// (step 11). Shadow arms are not in v1.
package experiment

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"reflect"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

const (
	KindExperiment  record.Kind = "experiment"
	KindAssignment  record.Kind = "assignment"
	KindClosed      record.Kind = "cohort_closed"
	KindEvidence    record.Kind = "unit_evidence"
	KindCommitment  record.Kind = "cohort_commitment"
	KindAttestation record.Kind = "effect_attestation"
	KindMeasurement record.Kind = "effect_measurement"
)

// Relation fixes the sign of the contrast: the treatment adds the item
// (apply_item) or removes it (ablate_item).
type Relation string

const (
	ApplyItem  Relation = "apply_item"
	AblateItem Relation = "ablate_item"
)

var relations = map[Relation]bool{ApplyItem: true, AblateItem: true}

// AssignmentKind: paired_replay | randomized_live.
type AssignmentKind string

const (
	PairedReplay   AssignmentKind = "paired_replay"
	RandomizedLive AssignmentKind = "randomized_live"
)

var assignmentKinds = map[AssignmentKind]bool{PairedReplay: true, RandomizedLive: true}

// OracleClass: deterministic_fixture | blinded_evaluator. §8a: the
// historical closure verdict is not one. Paired replay always scores by
// fixture (one per unit). Randomized live chooses at open — the oracle is
// part of the hypothesis: a lesson that supplies a fact needs an oracle
// that can check the fact (a fixture the protocol carries, matched against
// every unit's deliverable), while a lesson that shapes an answer is judged
// by the blinded evaluator, which says `unjudgeable` when the text alone
// cannot settle it rather than guessing. The acceptance run tombstoned two
// fact lessons the evaluator could not verify; that was the oracle's
// competence, not the lessons' effect.
type OracleClass string

const (
	DeterministicFixture OracleClass = "deterministic_fixture"
	BlindedEvaluator     OracleClass = "blinded_evaluator"
)

var oracles = map[OracleClass]bool{DeterministicFixture: true, BlindedEvaluator: true}

const (
	Treatment = "treatment"
	Control   = "control"
	// The paired-replay vocabulary: one outcome dimension (the fixture
	// matched), the paired mean difference, the fixture oracle.
	Dimension = "fixture_match"
	Estimator = "paired_diff/1"
	Evaluator = "fixture/1"
	// The randomized-live vocabulary: the goal achieved (as the blinded
	// judge scores it), the difference of arm means, the judge oracle.
	DimensionAchieved = "goal_achieved"
	EstimatorArms     = "arm_diff/1"
	// The evaluator is versioned by name on the attestation: judge/1 asked
	// two answers, judge/2 asks three (unjudgeable). The verifier renders
	// the prompt and reads the answer by the version the attestation
	// names, never by whatever the current code would ask — interpretation
	// code changing under immutable evidence is not a refusal of history.
	EvaluatorJudge   = "judge/2"
	EvaluatorJudgeV1 = "judge/1"
)

// ArmSpec is one arm's forced sets: the two arms of a protocol differ by
// exactly the hypothesis (the door checks it).
type ArmSpec struct {
	Arm      string          `json:"arm"`
	Apply    []learn.ItemRev `json:"apply,omitempty"`
	Withhold []learn.ItemRev `json:"withhold,omitempty"`
}

// OutcomeSpec is predeclared: the dimension, its direction, and the
// equivalence margin.
type OutcomeSpec struct {
	Dimension string  `json:"dimension"`
	Direction string  `json:"direction"` // higher
	Margin    float64 `json:"margin"`    // |delta| within it is "no difference"
}

// AnalysisSpec is predeclared: the estimator; for paired replay how many
// discordant pairs a direction needs and how many exposed pairs an
// equivalence needs; for randomized live how many exposed units per arm a
// direction needs (min_per_arm) and an equivalence needs (min_equivalent);
// and what a missing outcome does (v1: excluded from analysis). v1 has no
// interval estimate: these minimums ARE the uncertainty control, fixed
// before the first unit is assigned.
type AnalysisSpec struct {
	Estimator     string `json:"estimator"`
	MinDiscordant int    `json:"min_discordant,omitempty"`
	MinPerArm     int    `json:"min_per_arm,omitempty"`
	MinEquivalent int    `json:"min_equivalent"`
	Missing       string `json:"missing"` // exclude
}

// UnitSpec is one unit: a past production goal and its fixture (the
// expected answer, a thought the oracle reads).
type UnitSpec struct {
	Goal    record.RecordID `json:"goal"`
	Fixture thought.Ref     `json:"fixture"`
}

// Protocol is the immutable protocol as data: it lives on the Experiment
// (control) and is embedded, canonical, on the cohort commitment and the
// attestation (production) so a production verifier never reads control.
type Protocol struct {
	Experiment record.RecordID `json:"experiment"`
	Version    int             `json:"version"`
	Hypothesis learn.ItemRev   `json:"hypothesis"`
	Relation   Relation        `json:"relation"`
	Population string          `json:"population"` // a FamilyKey
	Assignment AssignmentKind  `json:"assignment"`
	Arms       []ArmSpec       `json:"arms"`
	Outcome    OutcomeSpec     `json:"outcome"`
	N          int             `json:"n"`
	Analysis   AnalysisSpec    `json:"analysis"`
	Oracle     OracleClass     `json:"oracle"`
	Units      []UnitSpec      `json:"units"`
	Fixture    *thought.Ref    `json:"fixture,omitempty"` // randomized live under deterministic_fixture: the one expected answer every unit's deliverable is matched against
}

func (p *Protocol) validate() error {
	if err := record.ValidateID(p.Experiment); err != nil {
		return fmt.Errorf("experiment: %w", err)
	}
	if p.Version < 1 {
		return errors.New("version starts at 1")
	}
	if err := record.ValidateID(record.RecordID(p.Hypothesis.Item)); err != nil {
		return fmt.Errorf("hypothesis item: %w", err)
	}
	if err := record.ValidateID(p.Hypothesis.Revision); err != nil {
		return fmt.Errorf("hypothesis revision: %w", err)
	}
	if !relations[p.Relation] {
		return fmt.Errorf("relation %q out of vocabulary", p.Relation)
	}
	if p.Population == "" || p.Population == "none" {
		return errors.New("population must be a family (never none)")
	}
	if !assignmentKinds[p.Assignment] {
		return fmt.Errorf("assignment %q out of vocabulary", p.Assignment)
	}
	if !oracles[p.Oracle] {
		return fmt.Errorf("oracle %q out of vocabulary", p.Oracle)
	}
	if p.Outcome.Direction != "higher" || p.Outcome.Margin < 0 || p.Outcome.Margin >= 1 {
		return errors.New("outcome: direction higher, margin in [0,1)")
	}
	if p.Analysis.Missing != "exclude" || p.Analysis.MinEquivalent < 1 {
		return errors.New("analysis: missing exclude, min_equivalent ≥ 1")
	}
	switch p.Assignment {
	case PairedReplay:
		if p.Oracle != DeterministicFixture || p.Outcome.Dimension != Dimension || p.Analysis.Estimator != Estimator {
			return errors.New("paired replay: oracle deterministic_fixture, dimension fixture_match, estimator paired_diff/1")
		}
		if p.Analysis.MinDiscordant < 1 || p.Analysis.MinPerArm != 0 {
			return errors.New("paired replay: min_discordant ≥ 1, no min_per_arm")
		}
		if p.N < 1 || len(p.Units) != p.N {
			return fmt.Errorf("n is %d but %d units are fixed", p.N, len(p.Units))
		}
		if p.Fixture != nil {
			return errors.New("paired replay: the fixtures are the units'")
		}
	case RandomizedLive:
		if p.Analysis.Estimator != EstimatorArms {
			return errors.New("randomized live: estimator arm_diff/1")
		}
		switch p.Oracle {
		case BlindedEvaluator:
			if p.Outcome.Dimension != DimensionAchieved || p.Fixture != nil {
				return errors.New("randomized live under the blinded evaluator: dimension goal_achieved, no fixture")
			}
		case DeterministicFixture:
			if p.Outcome.Dimension != Dimension || p.Fixture == nil {
				return errors.New("randomized live under the fixture oracle: dimension fixture_match and the protocol's fixture")
			}
			if err := p.Fixture.Validate(); err != nil {
				return fmt.Errorf("fixture: %w", err)
			}
			if p.Fixture.Kind != thought.Fixture || p.Fixture.Bytes == 0 {
				return errors.New("fixture: a non-empty fixture thought")
			}
		}
		if p.Analysis.MinPerArm < 1 || p.Analysis.MinDiscordant != 0 {
			return errors.New("randomized live: min_per_arm ≥ 1, no min_discordant")
		}
		if p.N < 2 || len(p.Units) != 0 {
			return errors.New("randomized live: n ≥ 2 and the units arrive at intake")
		}
		// permuted blocks of two fill both arms exactly n/2 each; every
		// declared verdict must be reachable from that split
		if p.N%2 != 0 {
			return fmt.Errorf("randomized live: n is %d; blocks of two need an even n", p.N)
		}
		if p.N/2 < p.Analysis.MinPerArm || p.N/2 < p.Analysis.MinEquivalent {
			return fmt.Errorf("randomized live: n %d gives %d per arm, below min_per_arm %d / min_equivalent %d: no verdict could be reached", p.N, p.N/2, p.Analysis.MinPerArm, p.Analysis.MinEquivalent)
		}
	}
	seen := map[record.RecordID]bool{}
	for _, u := range p.Units {
		if err := record.ValidateID(u.Goal); err != nil {
			return fmt.Errorf("unit: %w", err)
		}
		if seen[u.Goal] {
			return fmt.Errorf("unit %s fixed twice", u.Goal)
		}
		seen[u.Goal] = true
		if err := u.Fixture.Validate(); err != nil {
			return fmt.Errorf("unit %s fixture: %w", u.Goal, err)
		}
		if u.Fixture.Kind != thought.Fixture || u.Fixture.Bytes == 0 {
			return fmt.Errorf("unit %s fixture must be a non-empty fixture thought", u.Goal)
		}
	}
	// the arms: exactly treatment and control, differing by exactly the
	// hypothesis in the relation's direction
	want := Arms(p.Hypothesis, p.Relation)
	if len(p.Arms) != 2 || !reflect.DeepEqual(p.Arms, want) {
		return fmt.Errorf("arms must be exactly %v", want)
	}
	return nil
}

// Arms derives the two arms from the hypothesis and relation: the
// treatment applies (apply_item) or withholds (ablate_item) the hypothesis;
// the control forces nothing.
func Arms(h learn.ItemRev, rel Relation) []ArmSpec {
	t := ArmSpec{Arm: Treatment}
	if rel == ApplyItem {
		t.Apply = []learn.ItemRev{h}
	} else {
		t.Withhold = []learn.ItemRev{h}
	}
	return []ArmSpec{t, {Arm: Control}}
}

// Experiment is the immutable protocol (control). Nothing in it accrues:
// counts are derived from assignments; closure is its own record.
type Experiment struct {
	record.ControlRecord
	record.Header `json:"header"`
	Protocol      `json:"protocol"`
	Prior         record.RecordID `json:"prior,omitempty"` // the attestation of the prior version, when re-opened (fishing guard §19.4)
	Why           string          `json:"why"`
}

func (r *Experiment) Head() *record.Header { return &r.Header }
func (r *Experiment) Kind() record.Kind    { return KindExperiment }
func (r *Experiment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.ID) {
		return errors.New("experiment: subject must be the experiment itself")
	}
	if r.Protocol.Experiment != r.ID {
		return errors.New("experiment: the protocol names its experiment")
	}
	if err := r.Protocol.validate(); err != nil {
		return fmt.Errorf("experiment: %w", err)
	}
	if (r.Version > 1) != (r.Prior != "") {
		return errors.New("experiment: version 1 cites no prior; a later version cites the prior attestation")
	}
	if r.Prior != "" {
		if err := record.ValidateID(r.Prior); err != nil {
			return fmt.Errorf("experiment: prior: %w", err)
		}
	}
	if strings.TrimSpace(r.Why) == "" {
		return errors.New("experiment: needs a why")
	}
	return nil
}

// Assignment (control): one unit's admission, keyed deterministically by
// (experiment, unit). Ordinal is the unit's position (the protocol's, for
// paired replay; admission order, for randomized live); Seed is
// hash(experiment, unit). With paired replay both arms run and Arm is
// empty; with randomized live Arm is the one arm the unit runs, decided
// by ArmFor over the seed in permuted blocks of two, and committed in the
// SAME command as the unit's goal (the sequencer-enforced intake).
type Assignment struct {
	record.ControlRecord
	record.Header `json:"header"`
	Experiment    record.RecordID `json:"experiment"`
	Unit          record.RecordID `json:"unit"`
	Ordinal       int             `json:"ordinal"`
	Seed          string          `json:"seed"`
	Arm           string          `json:"arm,omitempty"`
}

func (r *Assignment) Head() *record.Header { return &r.Header }
func (r *Assignment) Kind() record.Kind    { return KindAssignment }
func (r *Assignment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := record.ValidateID(r.Experiment); err != nil {
		return fmt.Errorf("assignment: experiment: %w", err)
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.Experiment) {
		return errors.New("assignment: subject must be the experiment")
	}
	if err := record.ValidateID(r.Unit); err != nil {
		return fmt.Errorf("assignment: unit: %w", err)
	}
	if r.Ordinal < 0 {
		return errors.New("assignment: ordinal is a position")
	}
	if r.Seed != Seed(r.Experiment, r.Unit) {
		return errors.New("assignment: seed is not hash(experiment, unit)")
	}
	if r.Arm != "" && !learn.Arms[r.Arm] {
		return fmt.Errorf("assignment: arm %q out of vocabulary", r.Arm)
	}
	return nil
}

// ArmFor is the keyed randomization in permuted blocks of two: the first
// unit of a block takes the arm its seed's low bit names, the second takes
// the other — so every two admissions balance the arms and a fixed-n
// cohort with even n is exactly half and half. `first` is the arm of the
// block's first unit (ignored for a block's first).
func ArmFor(seed string, ordinal int, first string) string {
	if ordinal%2 == 1 {
		if first == Treatment {
			return Control
		}
		return Treatment
	}
	if len(seed) > 0 && (seed[len(seed)-1]-'0')%2 == 0 { // hex: the last nibble's parity
		return Treatment
	}
	return Control
}

// Seed is the keyed deterministic randomization: hex sha256 over a
// domain-separated (experiment, unit).
func Seed(exp, unit record.RecordID) string {
	return digest("seed", string(exp), string(unit))
}

func digest(parts ...string) string {
	h := sha256.New()
	h.Write([]byte("maro-experiment/v1"))
	for _, p := range parts {
		h.Write([]byte{0})
		h.Write([]byte(p))
	}
	return hex.EncodeToString(h.Sum(nil))
}

// Closed (control) marks the cohort closed: further assignments are
// refused. Committed in the same command as the commitment.
type Closed struct {
	record.ControlRecord
	record.Header `json:"header"`
	Experiment    record.RecordID `json:"experiment"`
	Commitment    record.RecordID `json:"commitment"`
	Count         int             `json:"count"`
}

func (r *Closed) Head() *record.Header { return &r.Header }
func (r *Closed) Kind() record.Kind    { return KindClosed }
func (r *Closed) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := record.ValidateID(r.Experiment); err != nil {
		return fmt.Errorf("cohort_closed: experiment: %w", err)
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.Experiment) {
		return errors.New("cohort_closed: subject must be the experiment")
	}
	if err := record.ValidateID(r.Commitment); err != nil {
		return fmt.Errorf("cohort_closed: commitment: %w", err)
	}
	if r.Count < 1 {
		return errors.New("cohort_closed: an empty cohort does not close")
	}
	return nil
}

// UnitEvidence (production) is one arm run's terminal evidence, derived
// by the fold over the attempt (§19.1) and committed by the runner when
// the arm is terminal: exposure (the hypothesis revision provably reached
// the run), the deliverable the oracle scores, the artifact root over the
// attempt's terminal evidence, and missingness. No scores.
type UnitEvidence struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Assignment    record.RecordID `json:"assignment"` // a control id, opaque here
	Experiment    record.RecordID `json:"experiment"`
	Unit          record.RecordID `json:"unit"`
	Arm           string          `json:"arm"`
	Exposed       bool            `json:"exposed"`
	Deliverable   *thought.Ref    `json:"deliverable,omitempty"`
	ArtifactRoot  string          `json:"artifact_root"`
	Missing       string          `json:"missing,omitempty"` // no_deliverable
}

func (r *UnitEvidence) Head() *record.Header { return &r.Header }
func (r *UnitEvidence) Kind() record.Kind    { return KindEvidence }
func (r *UnitEvidence) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 || r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("unit_evidence: is about one run attempt")
	}
	for name, id := range map[string]record.RecordID{"assignment": r.Assignment, "experiment": r.Experiment, "unit": r.Unit} {
		if err := record.ValidateID(id); err != nil {
			return fmt.Errorf("unit_evidence: %s: %w", name, err)
		}
	}
	if !learn.Arms[r.Arm] {
		return fmt.Errorf("unit_evidence: arm %q out of vocabulary", r.Arm)
	}
	if (r.Deliverable == nil) != (r.Missing != "") {
		return errors.New("unit_evidence: a deliverable or a missing reason, exactly one")
	}
	if r.Deliverable != nil {
		if err := r.Deliverable.Validate(); err != nil {
			return fmt.Errorf("unit_evidence: deliverable: %w", err)
		}
		if r.Deliverable.Kind != thought.Deliverable {
			return errors.New("unit_evidence: deliverable must be a deliverable thought")
		}
	}
	if r.Missing != "" && r.Missing != MissingNoDeliverable && r.Missing != MissingNotComplete {
		return fmt.Errorf("unit_evidence: missing %q out of vocabulary", r.Missing)
	}
	if len(r.ArtifactRoot) != 64 {
		return errors.New("unit_evidence: artifact root is a hex sha256")
	}
	return nil
}

// Missing reasons: the arm's recorded outcome was not complete (a failed
// or partial run delivers the framework's envelope, never a scoreable
// answer), or a complete run prepared no delivery. Either excludes the
// unit from analysis. A live unit's row may also be `unevaluated`: the
// blinded judge gave no usable score in EvaluatorTries calls.
const (
	MissingNoDeliverable = "no_deliverable"
	MissingNotComplete   = "not_complete"
	MissingUnevaluated   = "unevaluated"
	MissingUnjudgeable   = "unjudgeable" // the blinded evaluator said the text alone cannot settle it
)

// AssignedUnit is one cohort row: the unit, its assignment, its ordinal
// and seed — the authenticated denominator.
type AssignedUnit struct {
	Unit       record.RecordID `json:"unit"`
	Assignment record.RecordID `json:"assignment"`
	Ordinal    int             `json:"ordinal"`
	Seed       string          `json:"seed"`
}

// CohortCommitment (production) is the sequencer's closure of the cohort:
// the canonical protocol projection and every assigned unit, under a root
// the attestation is verified against. Same command as Closed.
type CohortCommitment struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Experiment    record.RecordID `json:"experiment"`
	Protocol      Protocol        `json:"protocol"`
	Units         []AssignedUnit  `json:"units"`
	Root          string          `json:"root"`
	Count         int             `json:"count"`
}

func (r *CohortCommitment) Head() *record.Header { return &r.Header }
func (r *CohortCommitment) Kind() record.Kind    { return KindCommitment }
func (r *CohortCommitment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.Experiment) {
		return errors.New("cohort_commitment: subject must be the experiment")
	}
	if r.Protocol.Experiment != r.Experiment {
		return errors.New("cohort_commitment: the protocol names another experiment")
	}
	if err := r.Protocol.validate(); err != nil {
		return fmt.Errorf("cohort_commitment: %w", err)
	}
	if r.Count != len(r.Units) || r.Count != r.Protocol.N {
		return errors.New("cohort_commitment: count = units = n (fixed-n closure)")
	}
	for i, u := range r.Units {
		if r.Protocol.Assignment == PairedReplay && u.Unit != r.Protocol.Units[i].Goal {
			return fmt.Errorf("cohort_commitment: unit %d is not the protocol's unit %d", i, i)
		}
		// a live protocol names no units: the fold checks each row's unit
		// against the assignment the intake command wrote
		if err := record.ValidateID(u.Unit); err != nil {
			return fmt.Errorf("cohort_commitment: unit %d: %w", i, err)
		}
		if u.Ordinal != i || u.Seed != Seed(r.Experiment, u.Unit) {
			return fmt.Errorf("cohort_commitment: unit %d is not ordinal %d with its seed", i, i)
		}
		if err := record.ValidateID(u.Assignment); err != nil {
			return fmt.Errorf("cohort_commitment: unit %d assignment: %w", i, err)
		}
	}
	if r.Root != CohortRoot(r.Units) {
		return errors.New("cohort_commitment: root does not recompute over the units")
	}
	return nil
}

// CohortRoot is the digest over the cohort rows in order.
func CohortRoot(units []AssignedUnit) string {
	raw, _ := json.Marshal(units)
	return digest("cohort", string(raw))
}

// UnitRow is one attestation row. Paired replay: the pair's evidence ids
// and the oracle's two scores. Randomized live: the unit's one arm, its
// evidence, the blinded judge's score and the evaluate invocation that
// scored it (the verifier recomputes the request from the evidence and
// reads the score off that invocation's receipt). Both: whether exposure
// held as the protocol intended (per-protocol membership).
type UnitRow struct {
	Unit       record.RecordID `json:"unit"`
	Assignment record.RecordID `json:"assignment"`
	// paired replay
	Treatment        record.RecordID `json:"treatment,omitempty"` // unit_evidence
	Control          record.RecordID `json:"control,omitempty"`   // unit_evidence
	TreatmentScore   float64         `json:"treatment_score"`
	ControlScore     float64         `json:"control_score"`
	TreatmentMissing string          `json:"treatment_missing,omitempty"`
	ControlMissing   string          `json:"control_missing,omitempty"`
	// randomized live
	Arm        string          `json:"arm,omitempty"`
	Evidence   record.RecordID `json:"evidence,omitempty"` // unit_evidence
	Score      float64         `json:"score"`
	Missing    string          `json:"missing,omitempty"`
	Evaluation record.RecordID `json:"evaluation,omitempty"` // the evaluate invocation
	Exposed    bool            `json:"exposed"`
}

// EffectAttestation (production) binds the cohort, the protocol, and one
// row per unit with the evaluator's scores. For the deterministic fixture
// oracle the verifier recomputes every score from the evidence's
// deliverable and the protocol's fixture; the hypothesis text is never an
// input (blinded by construction).
type EffectAttestation struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Experiment    record.RecordID `json:"experiment"`
	Cohort        record.RecordID `json:"cohort"`
	Closure       record.RecordID `json:"closure"` // the cohort_closed control id, opaque
	Protocol      Protocol        `json:"protocol"`
	Units         []UnitRow       `json:"units"`
	Evaluator     string          `json:"evaluator"`
	Estimator     string          `json:"estimator"`
}

func (r *EffectAttestation) Head() *record.Header { return &r.Header }
func (r *EffectAttestation) Kind() record.Kind    { return KindAttestation }
func (r *EffectAttestation) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.Experiment) {
		return errors.New("effect_attestation: subject must be the experiment")
	}
	for name, id := range map[string]record.RecordID{"cohort": r.Cohort, "closure": r.Closure} {
		if err := record.ValidateID(id); err != nil {
			return fmt.Errorf("effect_attestation: %s: %w", name, err)
		}
	}
	if r.Protocol.Experiment != r.Experiment {
		return errors.New("effect_attestation: the protocol names another experiment")
	}
	if err := r.Protocol.validate(); err != nil {
		return fmt.Errorf("effect_attestation: %w", err)
	}
	if len(r.Units) != r.Protocol.N {
		return errors.New("effect_attestation: one row per cohort unit")
	}
	for i, u := range r.Units {
		if err := record.ValidateID(u.Assignment); err != nil {
			return fmt.Errorf("effect_attestation: row %d assignment: %w", i, err)
		}
		for _, sc := range []float64{u.TreatmentScore, u.ControlScore, u.Score} {
			if sc != 0 && sc != 1 {
				return fmt.Errorf("effect_attestation: row %d: a score is 0 or 1", i)
			}
		}
		switch r.Protocol.Assignment {
		case PairedReplay:
			if r.Evaluator != Evaluator || r.Estimator != Estimator {
				return errors.New("effect_attestation: paired replay is evaluated by fixture/1 and estimated by paired_diff/1")
			}
			if u.Unit != r.Protocol.Units[i].Goal {
				return fmt.Errorf("effect_attestation: row %d is not the protocol's unit %d", i, i)
			}
			for name, id := range map[string]record.RecordID{"treatment": u.Treatment, "control": u.Control} {
				if err := record.ValidateID(id); err != nil {
					return fmt.Errorf("effect_attestation: row %d %s: %w", i, name, err)
				}
			}
			if u.Arm != "" || u.Evidence != "" || u.Score != 0 || u.Missing != "" || u.Evaluation != "" {
				return fmt.Errorf("effect_attestation: row %d carries live-arm fields under paired replay", i)
			}
		case RandomizedLive:
			judged := r.Evaluator == EvaluatorJudge || r.Evaluator == EvaluatorJudgeV1
			if r.Protocol.Oracle == DeterministicFixture {
				judged = r.Evaluator == Evaluator
			}
			if !judged || r.Estimator != EstimatorArms {
				return fmt.Errorf("effect_attestation: randomized live under %s is evaluated by %s (not %q) and estimated by arm_diff/1", r.Protocol.Oracle, map[bool]string{true: Evaluator, false: EvaluatorJudge + "|" + EvaluatorJudgeV1}[r.Protocol.Oracle == DeterministicFixture], r.Evaluator)
			}
			if r.Evaluator == EvaluatorJudgeV1 && u.Missing == MissingUnjudgeable {
				return fmt.Errorf("effect_attestation: row %d is unjudgeable under %s, which asked two answers", i, EvaluatorJudgeV1)
			}
			if r.Protocol.Oracle == DeterministicFixture && (u.Evaluation != "" || u.Missing == MissingUnjudgeable || u.Missing == MissingUnevaluated) {
				return fmt.Errorf("effect_attestation: row %d carries evaluator fields under the fixture oracle", i)
			}
			if err := record.ValidateID(u.Unit); err != nil {
				return fmt.Errorf("effect_attestation: row %d unit: %w", i, err)
			}
			if !learn.Arms[u.Arm] {
				return fmt.Errorf("effect_attestation: row %d arm %q out of vocabulary", i, u.Arm)
			}
			if err := record.ValidateID(u.Evidence); err != nil {
				return fmt.Errorf("effect_attestation: row %d evidence: %w", i, err)
			}
			if u.Treatment != "" || u.Control != "" || u.TreatmentScore != 0 || u.ControlScore != 0 || u.TreatmentMissing != "" || u.ControlMissing != "" {
				return fmt.Errorf("effect_attestation: row %d carries pair fields under randomized live", i)
			}
			switch u.Missing {
			case "":
				// under the evaluator a scored row cites its evaluation; the
				// one exception is the zero of a run that did not complete
				// (the fold checks that against the evidence). Under the
				// fixture oracle the fold recomputes every score
				if r.Protocol.Oracle == BlindedEvaluator && u.Evaluation == "" && u.Score != 0 {
					return fmt.Errorf("effect_attestation: row %d scores %v without an evaluation", i, u.Score)
				}
				if u.Evaluation != "" {
					if err := record.ValidateID(u.Evaluation); err != nil {
						return fmt.Errorf("effect_attestation: row %d evaluation: %w", i, err)
					}
				}
			case MissingNoDeliverable, MissingNotComplete, MissingUnevaluated:
				if u.Score != 0 || u.Evaluation != "" {
					return fmt.Errorf("effect_attestation: row %d is missing yet scored", i)
				}
			case MissingUnjudgeable:
				// the evaluator's own answer: the row cites it
				if u.Score != 0 {
					return fmt.Errorf("effect_attestation: row %d is unjudgeable yet scored", i)
				}
				if err := record.ValidateID(u.Evaluation); err != nil {
					return fmt.Errorf("effect_attestation: row %d unjudgeable cites no evaluation: %w", i, err)
				}
			default:
				return fmt.Errorf("effect_attestation: row %d missing %q out of vocabulary", i, u.Missing)
			}
		}
	}
	return nil
}

// EffectVerdict is the treatment's verdict before normalization.
type EffectVerdict string

const (
	TreatmentHelpful EffectVerdict = "treatment_helpful"
	TreatmentHarmful EffectVerdict = "treatment_harmful"
	Equivalent       EffectVerdict = "equivalent"
	Insufficient     EffectVerdict = "insufficient"
)

var verdicts = map[EffectVerdict]bool{TreatmentHelpful: true, TreatmentHarmful: true, Equivalent: true, Insufficient: true}

// EffectMeasurement (production) is a deterministic fold over its
// attestation (Measure recomputes it from the attestation alone): counts,
// both deltas, discordance, the verdict, and the item effect normalized by
// the relation. It is the evidence a measurement transition cites.
type EffectMeasurement struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Experiment    record.RecordID  `json:"experiment"`
	Attestation   record.RecordID  `json:"attestation"`
	Hypothesis    learn.ItemRev    `json:"hypothesis"`
	Relation      Relation         `json:"relation"`
	Assigned      int              `json:"assigned"`
	Analyzed      int              `json:"analyzed"`              // both arms present (ITT denominator)
	Exposed       int              `json:"exposed"`               // analyzed and exposed as intended (per-protocol denominator)
	Discordant    int              `json:"discordant"`            // paired replay
	TreatmentN    int              `json:"treatment_n,omitempty"` // randomized live: exposed units per arm
	ControlN      int              `json:"control_n,omitempty"`
	Unjudgeable   int              `json:"unjudgeable,omitempty"` // randomized live: units the blinded evaluator could not judge from the text (excluded, and the oracle's competence on this population)
	DeltaITT      float64          `json:"delta_itt"`
	DeltaPP       float64          `json:"delta_pp"`
	Verdict       EffectVerdict    `json:"verdict"`
	ItemEffect    learn.ItemEffect `json:"item_effect"`
}

func (r *EffectMeasurement) Head() *record.Header { return &r.Header }
func (r *EffectMeasurement) Kind() record.Kind    { return KindMeasurement }
func (r *EffectMeasurement) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "experiment" || r.Subject.ID != string(r.Experiment) {
		return errors.New("effect_measurement: subject must be the experiment")
	}
	if err := record.ValidateID(r.Attestation); err != nil {
		return fmt.Errorf("effect_measurement: attestation: %w", err)
	}
	if err := record.ValidateID(record.RecordID(r.Hypothesis.Item)); err != nil {
		return fmt.Errorf("effect_measurement: hypothesis: %w", err)
	}
	if err := record.ValidateID(r.Hypothesis.Revision); err != nil {
		return fmt.Errorf("effect_measurement: hypothesis: %w", err)
	}
	if !relations[r.Relation] {
		return fmt.Errorf("effect_measurement: relation %q out of vocabulary", r.Relation)
	}
	if !verdicts[r.Verdict] {
		return fmt.Errorf("effect_measurement: verdict %q out of vocabulary", r.Verdict)
	}
	if !learn.ValidEffect(r.ItemEffect) || r.ItemEffect != Normalize(r.Verdict, r.Relation) {
		return fmt.Errorf("effect_measurement: item effect %q is not %s normalized by %s", r.ItemEffect, r.Verdict, r.Relation)
	}
	if r.Unjudgeable < 0 || r.Unjudgeable > r.Assigned-r.Analyzed {
		return fmt.Errorf("effect_measurement: unjudgeable %d is not within the %d units assigned and not analyzed", r.Unjudgeable, r.Assigned-r.Analyzed)
	}
	if r.Assigned < r.Analyzed || r.Analyzed < r.Exposed || r.Exposed < r.Discordant || r.Discordant < 0 {
		return errors.New("effect_measurement: assigned ≥ analyzed ≥ exposed ≥ discordant ≥ 0")
	}
	if r.TreatmentN < 0 || r.ControlN < 0 || (r.TreatmentN+r.ControlN != 0 && r.TreatmentN+r.ControlN != r.Exposed) {
		return errors.New("effect_measurement: the per-arm counts sum to exposed")
	}
	return nil
}

// Effect implements learn.EffectEvidence.
func (r *EffectMeasurement) Effect() (learn.ItemRev, learn.ItemEffect) {
	return r.Hypothesis, r.ItemEffect
}

// Normalize maps the treatment's verdict to the item's effect by the
// relation: ablating a harmful item helps, so treatment_helpful under
// ablate_item is item_harmful.
func Normalize(v EffectVerdict, rel Relation) learn.ItemEffect {
	switch v {
	case Equivalent:
		return learn.ItemRedundant
	case TreatmentHelpful:
		if rel == AblateItem {
			return learn.ItemHarmful
		}
		return learn.ItemHelpful
	case TreatmentHarmful:
		if rel == AblateItem {
			return learn.ItemHelpful
		}
		return learn.ItemHarmful
	}
	return learn.ItemInsufficient
}

func now() time.Time { return time.Now().UTC() }

func init() {
	reg := func(k record.Kind, env record.Envelope, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: env, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindExperiment, record.Control, Experiment{}, "Open (operator CLI)",
		"experiment.Fold (control side: fishing guard, assignments, closure); the runner; Admit (the intake command's assignment capability)",
		"the immutable protocol: hypothesis, relation, population, assignment kind, arms, outcome, n, analysis, oracle, units")
	reg(KindAssignment, record.Control, Assignment{}, "the runner, one per unit (paired replay); Admit, in the goal's intake command (randomized live)",
		"experiment.Fold (the denominator); the runner (which arms are owed); the evaluator lane (which units are owed evidence)",
		"that a unit entered the cohort, once, at this ordinal — and for a live unit, in which arm")
	reg(KindClosed, record.Control, Closed{}, "Close, with the commitment in one command",
		"experiment.Fold (no assignment after it); the attestation cites it",
		"that the cohort is closed at n: further assignments are refused")
	reg(KindEvidence, record.Production, UnitEvidence{}, "the runner (paired replay) or the evaluator lane / Close (randomized live), when an arm run is terminal (derived over the run fold)",
		"experiment.Fold (recomputed from the run fold); the attestation's rows",
		"the arm's terminal evidence: exposure, deliverable, artifact root, missingness")
	reg(KindCommitment, record.Production, CohortCommitment{}, "Close (the sequencer's closure)",
		"experiment.Fold (protocol projection and cohort root the attestation is checked against); production verifiers",
		"the authenticated denominator and the canonical protocol")
	reg(KindAttestation, record.Production, EffectAttestation{}, "Close (the evaluator's one production write; for a live cohort each scored row cites the evaluator's own evaluate invocation)",
		"experiment.Fold (every row recomputed: evidence, scores — from the fixture, or the cited evaluate receipt whose request re-renders from the evidence — exposure); Measure",
		"the scored cohort the measurement folds over")
	reg(KindMeasurement, record.Production, EffectMeasurement{}, "Close (Measure over the attestation)",
		"experiment.Fold (recomputed); learn.Fold (the evidence a measurement transition cites: item and effect)",
		"whether THIS revision helped, harmed, was redundant, or the cohort was insufficient")
}
