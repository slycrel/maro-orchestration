// Package verdict holds the judging layer's records (design note §6):
// Observations from deterministic checks, Verdicts from judges/self/
// operators, and Resolutions — the effective verdict for a subject, computed
// by a versioned resolver as a pure fold over the candidate SET, never over
// arrival order.
package verdict

import (
	"fmt"
	"reflect"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// CheckKind names a deterministic check.
type CheckKind string

const (
	CheckPathExists      CheckKind = "path_exists"
	CheckSymbolExists    CheckKind = "symbol_exists"
	CheckFabricationDiff CheckKind = "fabrication_diff"
	CheckReceiptComplete CheckKind = "receipt_complete"
	CheckClaimProbe      CheckKind = "claim_probe"
)

var checks = set(CheckPathExists, CheckSymbolExists, CheckFabricationDiff, CheckReceiptComplete, CheckClaimProbe)

// applicability is the registered table of which verdict kinds a check can
// settle. An observation joins only those groups; a refutation for one kind
// never becomes a failure for another.
var applicability = map[CheckKind][]VerdictKind{
	CheckPathExists:      {KindClosure, KindProvenance},
	CheckSymbolExists:    {KindClosure, KindProvenance},
	CheckClaimProbe:      {KindProvenance},
	CheckFabricationDiff: {KindFabrication},
	CheckReceiptComplete: {KindFabrication},
}

// Applies reports whether a check can settle a verdict kind.
func Applies(c CheckKind, k VerdictKind) bool {
	for _, kk := range applicability[c] {
		if kk == k {
			return true
		}
	}
	return false
}

// ObsResult is what a check found. could_not_observe is distinct from
// refuted: a check that could not run proves nothing either way.
type ObsResult string

const (
	Refuted         ObsResult = "refuted"
	Supported       ObsResult = "supported"
	CouldNotObserve ObsResult = "could_not_observe"
)

var obsResults = set(Refuted, Supported, CouldNotObserve)

// Observation is one deterministic check against one claim about a subject.
type Observation struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Check         CheckKind    `json:"check"`
	Claim         thought.Ref  `json:"claim"` // the claim text the check looked at
	Result        ObsResult    `json:"result"`
	Confidence    float64      `json:"confidence"`
	Evidence      []record.Ref `json:"evidence,omitempty"`
}

func (r *Observation) Head() *record.Header { return &r.Header }
func (r *Observation) Kind() record.Kind    { return KindObservation }
func (r *Observation) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if !checks[string(r.Check)] {
		return fmt.Errorf("observation: check %q out of vocabulary", r.Check)
	}
	if !obsResults[string(r.Result)] {
		return fmt.Errorf("observation: result %q out of vocabulary", r.Result)
	}
	if err := confidenceOK(r.Confidence); err != nil {
		return err
	}
	return r.Claim.Validate()
}

// VerdictKind names what is being judged.
type VerdictKind string

const (
	KindStep        VerdictKind = "step"
	KindClosure     VerdictKind = "closure"
	KindProvenance  VerdictKind = "provenance"
	KindFabrication VerdictKind = "fabrication"
	KindStuck       VerdictKind = "stuck"
	KindDelivery    VerdictKind = "delivery"
)

// Outcome vocabularies are closed PER KIND. Success outcomes are the ones a
// self-verdict may never establish and an operator or judge must.
var outcomes = map[VerdictKind]map[string]bool{
	KindStep:        set("done", "blocked", "unclear"),
	KindClosure:     set("achieved", "not_achieved", "unknown"),
	KindProvenance:  set("grounded", "ungrounded", "unknown"),
	KindFabrication: set("consistent", "fabricated", "unknown"),
	KindStuck:       set("stuck", "progressing", "unknown"),
	KindDelivery:    set("delivered", "undeliverable", "unknown"),
}

var successOutcomes = map[VerdictKind]string{
	KindStep: "done", KindClosure: "achieved", KindProvenance: "grounded", KindFabrication: "consistent", KindStuck: "progressing", KindDelivery: "delivered",
}

// failureOutcome is what a sufficient refuting observation settles.
var failureOutcome = map[VerdictKind]string{
	KindClosure: "not_achieved", KindProvenance: "ungrounded", KindFabrication: "fabricated",
}

// unknownOutcome is what an unresolved, contested, or unpromotable set
// yields: never a failure the evidence did not establish.
var unknownOutcome = map[VerdictKind]string{
	KindStep: "unclear", KindClosure: "unknown", KindProvenance: "unknown", KindFabrication: "unknown", KindStuck: "unknown", KindDelivery: "unknown",
}

// Standing is who asserted a verdict; it ranks candidates.
type Standing string

const (
	StandingSelf          Standing = "self"          // the executing agent's own claim
	StandingJudge         Standing = "judge"         // a model judge (an invocation)
	StandingDeterministic Standing = "deterministic" // a check-derived verdict
	StandingOperator      Standing = "operator"      // a human restamp
)

var standingRank = map[Standing]int{StandingSelf: 1, StandingJudge: 2, StandingDeterministic: 3, StandingOperator: 4}

// Source is the standing plus what produced it.
type Source struct {
	Standing Standing        `json:"standing"`
	Ref      record.RecordID `json:"ref,omitempty"` // the judge invocation, when standing is judge
}

// Direction says which way a verdict may move the effective outcome.
type Direction string

const (
	MayDemote  Direction = "may_demote"
	MayPromote Direction = "may_promote"
	Both       Direction = "both"
)

// directionFor is fixed by standing: a self-verdict may only demote (D13,
// 08-02 recovery over correctness: "a verdict layer that admits no overrule
// must earn that standing, and most should not have it").
var directionFor = map[Standing]Direction{StandingSelf: MayDemote, StandingJudge: Both, StandingDeterministic: Both, StandingOperator: Both}

// Verdict is one assertion about a subject. Append-only: overruling is a
// new Verdict with Supersedes set.
type Verdict struct {
	record.ProductionRecord
	record.Header `json:"header"`
	VerdictKind   VerdictKind   `json:"verdict_kind"`
	Outcome       string        `json:"outcome"`
	Confidence    float64       `json:"confidence"`
	Source        Source        `json:"source"`
	Basis         []record.Ref  `json:"basis,omitempty"`
	Falsifiers    []thought.Ref `json:"falsifiers,omitempty"` // closure only
	Direction     Direction     `json:"direction"`
}

func (r *Verdict) Head() *record.Header { return &r.Header }
func (r *Verdict) Kind() record.Kind    { return KindVerdict }
func (r *Verdict) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	vocab, ok := outcomes[r.VerdictKind]
	if !ok {
		return fmt.Errorf("verdict: kind %q out of vocabulary", r.VerdictKind)
	}
	if !vocab[r.Outcome] {
		return fmt.Errorf("verdict: outcome %q out of vocabulary for %s", r.Outcome, r.VerdictKind)
	}
	if err := confidenceOK(r.Confidence); err != nil {
		return err
	}
	if _, ok := standingRank[r.Source.Standing]; !ok {
		return fmt.Errorf("verdict: standing %q out of vocabulary", r.Source.Standing)
	}
	if r.Source.Standing == StandingJudge && r.Source.Ref == "" {
		return fmt.Errorf("verdict: a judge verdict must name its invocation")
	}
	if r.Direction != directionFor[r.Source.Standing] {
		return fmt.Errorf("verdict: direction %q disagrees with standing %s (fixed: %s)", r.Direction, r.Source.Standing, directionFor[r.Source.Standing])
	}
	if len(r.Falsifiers) > 0 && r.VerdictKind != KindClosure {
		return fmt.Errorf("verdict: falsifiers only on closure")
	}
	for _, f := range r.Falsifiers {
		if err := f.Validate(); err != nil {
			return err
		}
	}
	return nil
}

// Resolution is the effective verdict for (subject, kind) as of a candidate
// set, with every candidate named and the resolver version, thresholds, and
// rule that decided it. Effective is set only when ONE verdict won on
// standing; agreed maxima and refutations name their Decisive records
// instead; contested and unpromotable sets name none.
type Resolution struct {
	record.ProductionRecord
	record.Header `json:"header"`
	VerdictKind   VerdictKind       `json:"verdict_kind"`
	Outcome       string            `json:"outcome"`
	Effective     record.RecordID   `json:"effective,omitempty"`
	Decisive      []record.RecordID `json:"decisive,omitempty"` // agreed peers, or the refuting observations
	Candidates    []record.RecordID `json:"candidates"`
	Observations  []record.RecordID `json:"observations,omitempty"`
	ResolverVer   string            `json:"resolver_ver"`
	Thresholds    Thresholds        `json:"thresholds"`
	Rule          string            `json:"rule"`
	Contested     bool              `json:"contested"`
	Confidence    float64           `json:"confidence"`
}

func (r *Resolution) Head() *record.Header { return &r.Header }
func (r *Resolution) Kind() record.Kind    { return KindResolution }
func (r *Resolution) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	vocab, ok := outcomes[r.VerdictKind]
	if !ok {
		return fmt.Errorf("resolution: kind %q out of vocabulary", r.VerdictKind)
	}
	if !vocab[r.Outcome] {
		return fmt.Errorf("resolution: outcome %q out of vocabulary for %s", r.Outcome, r.VerdictKind)
	}
	if r.ResolverVer != ResolverVer {
		return fmt.Errorf("resolution: resolver %q is not %s", r.ResolverVer, ResolverVer)
	}
	if err := r.Thresholds.validate(); err != nil {
		return err
	}
	if err := confidenceOK(r.Confidence); err != nil {
		return err
	}
	if r.Rule == "" {
		return fmt.Errorf("resolution: no rule")
	}
	seen := map[record.RecordID]bool{}
	for _, id := range append(append([]record.RecordID{}, r.Candidates...), r.Observations...) {
		if err := record.ValidateID(id); err != nil {
			return fmt.Errorf("resolution: %w", err)
		}
		if seen[id] {
			return fmt.Errorf("resolution: duplicate id %s", id)
		}
		seen[id] = true
	}
	inCands := func(id record.RecordID) bool {
		for _, c := range r.Candidates {
			if c == id {
				return true
			}
		}
		return false
	}
	if r.Effective != "" && !inCands(r.Effective) {
		return fmt.Errorf("resolution: effective %s is not a candidate", r.Effective)
	}
	for _, d := range r.Decisive {
		if !seen[d] {
			return fmt.Errorf("resolution: decisive %s is neither a candidate nor an observation", d)
		}
	}
	rule := r.Rule
	switch {
	case r.Contested:
		if r.Effective != "" || len(r.Decisive) != 0 || r.Outcome != unknownOutcome[r.VerdictKind] || !hasPrefix(rule, "contested:") {
			return fmt.Errorf("resolution: contested must carry the kind's unknown, no effective, no decisive, rule contested:")
		}
	case hasPrefix(rule, "standing:"):
		if r.Effective == "" || len(r.Candidates) == 0 {
			return fmt.Errorf("resolution: a standing rule names one effective candidate")
		}
	case hasPrefix(rule, "agreed_maxima:"), hasPrefix(rule, "refuted_by_observation:"):
		if r.Effective != "" || len(r.Decisive) == 0 {
			return fmt.Errorf("resolution: %s names decisive records and no effective", rule)
		}
	case rule == "self_cannot_promote", rule == "no_candidates":
		if r.Effective != "" || len(r.Decisive) != 0 || r.Outcome != unknownOutcome[r.VerdictKind] {
			return fmt.Errorf("resolution: %s yields the kind's unknown and names nothing", rule)
		}
	default:
		return fmt.Errorf("resolution: rule %q out of vocabulary", rule)
	}
	return nil
}

func hasPrefix(s, p string) bool { return len(s) >= len(p) && s[:len(p)] == p }

func confidenceOK(c float64) error {
	if c < 0 || c > 1 || c != c {
		return fmt.Errorf("confidence %v not in [0,1]", c)
	}
	return nil
}

func set[T ~string](vs ...T) map[string]bool {
	m := map[string]bool{}
	for _, v := range vs {
		m[string(v)] = true
	}
	return m
}

const (
	KindObservation record.Kind = "observation"
	KindVerdict     record.Kind = "verdict"
	KindResolution  record.Kind = "resolution"
)

func init() {
	record.Register(record.Spec{Kind: KindObservation, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Observation{}),
		Writer: "deterministic checks (provenance, existence, fabrication diff, receipt completeness, claim probes)", Reader: "verdict.Resolve; judges (every claim is seen with its observations)",
		Decision: "a sufficient refutation settles failure without a judge; could_not_observe settles nothing", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindVerdict, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Verdict{}),
		Writer: "judges (invocations), the executing agent (self), operators (restamp), deterministic derivation", Reader: "verdict.Resolve; the run state machine; delivery rendering",
		Decision: "the candidate set the resolver folds; overruling is a new verdict with Supersedes", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindResolution, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Resolution{}),
		Writer: "verdict.Resolve (one per (subject, kind, candidate set))", Reader: "the run state machine (execution outcome); delivery; learning (outcome oracle)",
		Decision: "the effective verdict, with every candidate named and the rule that decided", Retention: record.Forever})
}
