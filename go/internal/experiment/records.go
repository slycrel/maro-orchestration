// Package experiment is the measured loop's unit (§8a, §9): an immutable
// protocol in the control envelope, assignments of units to it, the arm
// runs' evidence in production, one closure, one attestation, and a
// measurement that is a deterministic fold over the attestation — the ONE
// thing a lifecycle transition may cite as measurement evidence.
//
// v1 assignment kind is paired replay: every unit (a past production goal
// of the population family) is re-run in both arms, which differ by
// exactly the hypothesis; the oracle is a deterministic fixture per unit.
// Randomized live assignment, shadow arms, and the blinded evaluator are
// step 11.
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

// AssignmentKind: v1 = paired_replay only.
type AssignmentKind string

const PairedReplay AssignmentKind = "paired_replay"

var assignmentKinds = map[AssignmentKind]bool{PairedReplay: true}

// OracleClass: v1 = deterministic_fixture only (§8a: the historical
// closure verdict is not an oracle).
type OracleClass string

const DeterministicFixture OracleClass = "deterministic_fixture"

var oracles = map[OracleClass]bool{DeterministicFixture: true}

const (
	Treatment = "treatment"
	Control   = "control"
	// Dimension and Estimator are the v1 vocabulary: one outcome dimension
	// (the fixture matched), one estimator (paired mean difference).
	Dimension = "fixture_match"
	Estimator = "paired_diff/1"
	Evaluator = "fixture/1"
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

// AnalysisSpec is predeclared: the estimator, how many discordant pairs a
// direction needs, how many exposed pairs an equivalence needs, and what
// a missing outcome does (v1: excluded from analysis).
type AnalysisSpec struct {
	Estimator     string `json:"estimator"`
	MinDiscordant int    `json:"min_discordant"`
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
	if p.Outcome.Dimension != Dimension || p.Outcome.Direction != "higher" || p.Outcome.Margin < 0 || p.Outcome.Margin >= 1 {
		return errors.New("outcome: dimension fixture_match, direction higher, margin in [0,1)")
	}
	if p.Analysis.Estimator != Estimator || p.Analysis.MinDiscordant < 1 || p.Analysis.MinEquivalent < 1 || p.Analysis.Missing != "exclude" {
		return errors.New("analysis: estimator paired_diff/1, min_discordant ≥ 1, min_equivalent ≥ 1, missing exclude")
	}
	if p.N < 1 || len(p.Units) != p.N {
		return fmt.Errorf("n is %d but %d units are fixed", p.N, len(p.Units))
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
// (experiment, unit). Ordinal is the unit's position in the protocol; Seed
// is hash(experiment, unit) — with paired replay both arms run, so the
// seed decides nothing yet and is recorded for the randomized kind.
type Assignment struct {
	record.ControlRecord
	record.Header `json:"header"`
	Experiment    record.RecordID `json:"experiment"`
	Unit          record.RecordID `json:"unit"`
	Ordinal       int             `json:"ordinal"`
	Seed          string          `json:"seed"`
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
	return nil
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
// pair from analysis.
const (
	MissingNoDeliverable = "no_deliverable"
	MissingNotComplete   = "not_complete"
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
		if u.Ordinal != i || u.Unit != r.Protocol.Units[i].Goal || u.Seed != Seed(r.Experiment, u.Unit) {
			return fmt.Errorf("cohort_commitment: unit %d is not the protocol's unit %d with its seed", i, i)
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

// UnitRow is one attestation row: the pair's evidence ids and the
// evaluator's scores, plus whether the arms' exposure held as the
// protocol intended (per-protocol membership).
type UnitRow struct {
	Unit             record.RecordID `json:"unit"`
	Assignment       record.RecordID `json:"assignment"`
	Treatment        record.RecordID `json:"treatment"` // unit_evidence
	Control          record.RecordID `json:"control"`   // unit_evidence
	TreatmentScore   float64         `json:"treatment_score"`
	ControlScore     float64         `json:"control_score"`
	TreatmentMissing string          `json:"treatment_missing,omitempty"`
	ControlMissing   string          `json:"control_missing,omitempty"`
	Exposed          bool            `json:"exposed"` // both arms' exposure as intended
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
	if r.Evaluator != Evaluator || r.Estimator != Estimator {
		return errors.New("effect_attestation: evaluator fixture/1 and estimator paired_diff/1 (v1)")
	}
	if len(r.Units) != r.Protocol.N {
		return errors.New("effect_attestation: one row per cohort unit")
	}
	for i, u := range r.Units {
		if u.Unit != r.Protocol.Units[i].Goal {
			return fmt.Errorf("effect_attestation: row %d is not the protocol's unit %d", i, i)
		}
		for name, id := range map[string]record.RecordID{"assignment": u.Assignment, "treatment": u.Treatment, "control": u.Control} {
			if err := record.ValidateID(id); err != nil {
				return fmt.Errorf("effect_attestation: row %d %s: %w", i, name, err)
			}
		}
		for _, sc := range []float64{u.TreatmentScore, u.ControlScore} {
			if sc != 0 && sc != 1 {
				return fmt.Errorf("effect_attestation: row %d: a fixture score is 0 or 1", i)
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
	Analyzed      int              `json:"analyzed"` // both arms present (ITT denominator)
	Exposed       int              `json:"exposed"`  // analyzed and exposed as intended (per-protocol denominator)
	Discordant    int              `json:"discordant"`
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
	if r.Assigned < r.Analyzed || r.Analyzed < r.Exposed || r.Exposed < r.Discordant || r.Discordant < 0 {
		return errors.New("effect_measurement: assigned ≥ analyzed ≥ exposed ≥ discordant ≥ 0")
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
	reg(KindExperiment, record.Control, Experiment{}, "Open (operator CLI in v1; the tail's proposals in step 11)",
		"experiment.Fold (control side: fishing guard, assignments, closure); the runner",
		"the immutable protocol: hypothesis, relation, population, arms, outcome, n, analysis, oracle, units")
	reg(KindAssignment, record.Control, Assignment{}, "the runner, one per unit, keyed by (experiment, unit)",
		"experiment.Fold (the denominator); the runner (which arms are owed)",
		"that a unit entered the cohort, once, at this ordinal")
	reg(KindClosed, record.Control, Closed{}, "Close, with the commitment in one command",
		"experiment.Fold (no assignment after it); the attestation cites it",
		"that the cohort is closed at n: further assignments are refused")
	reg(KindEvidence, record.Production, UnitEvidence{}, "the runner, when an arm run is terminal (derived over the run fold)",
		"experiment.Fold (recomputed from the run fold); the attestation's rows",
		"the arm's terminal evidence: exposure, deliverable, artifact root, missingness")
	reg(KindCommitment, record.Production, CohortCommitment{}, "Close (the sequencer's closure)",
		"experiment.Fold (protocol projection and cohort root the attestation is checked against); production verifiers",
		"the authenticated denominator and the canonical protocol")
	reg(KindAttestation, record.Production, EffectAttestation{}, "Close (the evaluator's one production write)",
		"experiment.Fold (every row recomputed: evidence, scores, exposure); Measure",
		"the scored cohort the measurement folds over")
	reg(KindMeasurement, record.Production, EffectMeasurement{}, "Close (Measure over the attestation)",
		"experiment.Fold (recomputed); learn.Fold (the evidence a measurement transition cites: item and effect)",
		"whether THIS revision helped, harmed, was redundant, or the cohort was insufficient")
}
