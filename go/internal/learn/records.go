// Package learn is memory: learned items as immutable revisions, their
// efficacy standing as a lifecycle per exact revision, one recall query, and
// the Application that proves a lesson reached a request (design note §7).
//
// Two folds, kept apart: standing is per ItemRev (evidence about one
// revision never moves another's stage); the current revision is a fold
// over Predecessor per LearnedID. Selection takes the current revision only
// if THAT revision's standing is selectable.
package learn

import (
	"errors"
	"fmt"
	"reflect"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

const (
	KindRevision    record.Kind = "learned_revision"
	KindTransition  record.Kind = "learned_transition"
	KindApplication record.Kind = "application"
	KindRecall      record.Kind = "recall_selection"
)

// LearnedID is the stable identity of an item across revisions: minted once
// (a ULID), never reused.
type LearnedID string

// ItemRev names an item at an exact revision — the ONLY way an item is
// named where content matters.
type ItemRev struct {
	Item     LearnedID       `json:"item"`
	Revision record.RecordID `json:"revision"`
}

// LearnedKind: a lesson reaches a request by recall injection; a policy is
// orchestration data consumed at the driver's policy boundary (its apply
// surface arrives with step 10; recall never injects a policy).
type LearnedKind string

const (
	Lesson LearnedKind = "lesson"
	Policy LearnedKind = "policy"
)

var kinds = map[LearnedKind]bool{Lesson: true, Policy: true}

// Stage is the single authoritative lifecycle vocabulary (§7).
type Stage string

const (
	Candidate   Stage = "candidate"   // proposed or imported; never selected
	Observed    Stage = "observed"    // tenure only; never selected
	Provisional Stage = "provisional" // measured or restamped; selectable
	Effective   Stage = "effective"   // item_helpful; selectable
	Canon       Stage = "canon"       // selectable
	Contested   Stage = "contested"   // conflicting evidence; selectable, flagged
	Quarantined Stage = "quarantined" // item_harmful at threshold; never selected
	Tombstone   Stage = "tombstone"   // redundant, expired, or removed; never selected
)

var stages = map[Stage]bool{Candidate: true, Observed: true, Provisional: true, Effective: true, Canon: true, Contested: true, Quarantined: true, Tombstone: true}

// Selectable is what recall may inject: exactly provisional | effective |
// canon, plus contested (flagged).
var Selectable = map[Stage]bool{Provisional: true, Effective: true, Canon: true, Contested: true}

// legal is the transition table (§7). Actor rows say who may make each edge
// in v1; measurement and tenure actors arrive with their producers
// (steps 9–11), additively.
var legal = map[Stage]map[Stage]bool{
	Candidate:   {Observed: true, Provisional: true, Effective: true, Quarantined: true, Tombstone: true},
	Observed:    {Provisional: true, Effective: true, Quarantined: true, Tombstone: true},
	Provisional: {Effective: true, Contested: true, Quarantined: true},
	Effective:   {Canon: true, Contested: true, Quarantined: true},
	Canon:       {Contested: true, Quarantined: true},
	Contested:   {Provisional: true, Effective: true, Quarantined: true, Tombstone: true},
	Quarantined: {Tombstone: true, Provisional: true},
}

// Legal reports whether from→to is in the table.
func Legal(from, to Stage) bool { return legal[from][to] }

// Actor is who made a transition: the operator (CLI restamp), tenure (the
// timers lane: candidate → observed after enough applications, or expiry
// → tombstone); `measurement` arrives with the experiments.
type Actor string

const (
	ActorOperator Actor = "operator"
	ActorTenure   Actor = "tenure"
)

var actors = map[Actor]bool{ActorOperator: true, ActorTenure: true}

// ScopePath is where an item applies: the workspace, or one goal's subtree.
// Recall walks a run's goal ancestry (own → parents → root → workspace).
type ScopePath string

const ScopeWorkspace ScopePath = "workspace"

// ScopeGoal names a goal's scope.
func ScopeGoal(goal record.RecordID) ScopePath { return ScopePath("goal:" + string(goal)) }

func validScope(s ScopePath) error {
	if s == ScopeWorkspace {
		return nil
	}
	if strings.HasPrefix(string(s), "goal:") {
		return record.ValidateID(record.RecordID(strings.TrimPrefix(string(s), "goal:")))
	}
	return fmt.Errorf("scope %q is neither workspace nor goal:<id>", s)
}

// Provenance says where a revision came from: the operator, or the tail's
// proposal over a recorded run (Ref = that run's diagnosis).
type Provenance struct {
	Source string          `json:"source"`        // operator
	Ref    record.RecordID `json:"ref,omitempty"` // the run or receipt it was learned from, when any
	Why    string          `json:"why"`
}

var sources = map[string]bool{"operator": true, "tail": true}

// LearnedRevision is one immutable revision of an item. The first revision
// has no predecessor; every later one names the item's then-current
// revision. Text is a whole thought (lesson_text).
type LearnedRevision struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Item          LearnedID       `json:"item"`
	Predecessor   record.RecordID `json:"predecessor,omitempty"`
	LearnedKind   LearnedKind     `json:"kind"`
	Scope         ScopePath       `json:"scope"`
	Family        string          `json:"family,omitempty"` // a FamilyKey; empty = any family
	Text          thought.Ref     `json:"text,omitempty"`   // a lesson's whole text (lesson_text thought)
	Policy        *PolicyRule     `json:"policy,omitempty"` // a policy's rule: process data, declared, never a thought
	Provenance    Provenance      `json:"provenance"`
}

func (r *LearnedRevision) Head() *record.Header { return &r.Header }
func (r *LearnedRevision) Kind() record.Kind    { return KindRevision }
func (r *LearnedRevision) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := record.ValidateID(record.RecordID(r.Item)); err != nil {
		return fmt.Errorf("learned_revision: item: %w", err)
	}
	if r.Subject.Kind != "learned" || r.Subject.ID != string(r.Item) {
		return errors.New("learned_revision: subject must be the item")
	}
	if r.Predecessor != "" {
		if err := record.ValidateID(r.Predecessor); err != nil {
			return fmt.Errorf("learned_revision: predecessor: %w", err)
		}
	}
	if !kinds[r.LearnedKind] {
		return fmt.Errorf("learned_revision: kind %q out of vocabulary", r.LearnedKind)
	}
	if err := validScope(r.Scope); err != nil {
		return fmt.Errorf("learned_revision: %w", err)
	}
	switch r.LearnedKind {
	case Lesson:
		if r.Policy != nil {
			return errors.New("learned_revision: a lesson carries text, not a policy rule")
		}
		if err := r.Text.Validate(); err != nil {
			return fmt.Errorf("learned_revision: text: %w", err)
		}
		if r.Text.Kind != thought.LessonText {
			return fmt.Errorf("learned_revision: text must be a lesson_text thought, got %q", r.Text.Kind)
		}
		if r.Text.Bytes == 0 {
			return errors.New("learned_revision: text is empty")
		}
	case Policy:
		if r.Text != (thought.Ref{}) {
			return errors.New("learned_revision: a policy carries its rule, not text")
		}
		if err := r.Policy.validate(); err != nil {
			return fmt.Errorf("learned_revision: policy: %w", err)
		}
	}
	if !sources[r.Provenance.Source] {
		return fmt.Errorf("learned_revision: provenance source %q out of vocabulary", r.Provenance.Source)
	}
	if strings.TrimSpace(r.Provenance.Why) == "" {
		return errors.New("learned_revision: provenance needs a why")
	}
	if r.Family == "none" {
		return errors.New("learned_revision: family \"none\" is the unassessed goal, not a family; leave family empty for any")
	}
	return nil
}

// LifecycleTransition moves ONE revision's standing. Evidence is required
// unless the actor is the operator, whose Why is the evidence.
type LifecycleTransition struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Item          LearnedID       `json:"item"`
	Revision      record.RecordID `json:"revision"`
	From          Stage           `json:"from"`
	To            Stage           `json:"to"`
	Actor         Actor           `json:"actor"`
	Evidence      record.RecordID `json:"evidence,omitempty"`
	Why           string          `json:"why"`
}

func (r *LifecycleTransition) Head() *record.Header { return &r.Header }
func (r *LifecycleTransition) Kind() record.Kind    { return KindTransition }
func (r *LifecycleTransition) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "learned" || r.Subject.ID != string(r.Item) {
		return errors.New("learned_transition: subject must be the item")
	}
	if err := record.ValidateID(r.Revision); err != nil {
		return fmt.Errorf("learned_transition: revision: %w", err)
	}
	if !stages[r.From] || !stages[r.To] {
		return fmt.Errorf("learned_transition: stage %q/%q out of vocabulary", r.From, r.To)
	}
	if !Legal(r.From, r.To) {
		return fmt.Errorf("learned_transition: %s → %s is not a legal transition", r.From, r.To)
	}
	if !actors[r.Actor] {
		return fmt.Errorf("learned_transition: actor %q out of vocabulary", r.Actor)
	}
	if r.Actor == ActorOperator {
		// the operator's why IS the evidence; a cited record would only look
		// authoritative without being checked (v1 has no typed evidence yet)
		if r.Evidence != "" {
			return errors.New("learned_transition: an operator transition carries a why, not evidence")
		}
	} else if r.Evidence == "" {
		return errors.New("learned_transition: a non-operator transition names its evidence")
	}
	if r.Actor == ActorTenure && !tenureLegal[r.From][r.To] {
		return fmt.Errorf("learned_transition: tenure may not move %s → %s (candidate→observed, or expiry → tombstone)", r.From, r.To)
	}
	if r.From == Candidate && r.To == Observed && r.Actor != ActorTenure {
		return errors.New("learned_transition: candidate → observed is tenure's alone")
	}
	if r.Evidence != "" {
		if err := record.ValidateID(r.Evidence); err != nil {
			return fmt.Errorf("learned_transition: evidence: %w", err)
		}
	}
	if strings.TrimSpace(r.Why) == "" {
		return errors.New("learned_transition: needs a why")
	}
	return nil
}

// Application proves a revision reached a backend request: the
// representation is the exact bytes that appear in the request thought.
type Application struct {
	record.ProductionRecord
	record.Header  `json:"header"`
	Item           LearnedID       `json:"item"`
	Revision       record.RecordID `json:"revision"`
	Invocation     record.RecordID `json:"invocation"`
	Representation thought.Ref     `json:"representation"`
}

func (r *Application) Head() *record.Header { return &r.Header }
func (r *Application) Kind() record.Kind    { return KindApplication }
func (r *Application) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 {
		return errors.New("application: needs run_id and attempt")
	}
	if r.Subject.Kind != "invocation" || r.Subject.ID != string(r.Invocation) {
		return errors.New("application: subject must be the invocation")
	}
	for _, id := range []record.RecordID{r.Revision, r.Invocation, record.RecordID(r.Item)} {
		if err := record.ValidateID(id); err != nil {
			return fmt.Errorf("application: %w", err)
		}
	}
	if err := r.Representation.Validate(); err != nil {
		return fmt.Errorf("application: representation: %w", err)
	}
	if r.Representation.Kind != thought.LessonText {
		return errors.New("application: representation must be a lesson_text thought")
	}
	return nil
}

// Excluded is one exclusion in the bounded projection.
type Excluded struct {
	ItemRev
	Reason string `json:"reason"`
}

// TenureBound is how many applications move candidate → observed. Why 3:
// one application proves the item was recalled once; three prove it keeps
// being selected — and observed is still not selectable, so the cost of
// being wrong here is nil. A constant so the fold re-derives the edge.
const TenureBound = 3

// ExpiryIdle tombstones a candidate|observed revision with no application
// for this long. Why 30 days: an item nobody recalled in a month of runs is
// noise in the recall projection; tombstone is reversible by a new
// revision. A constant so the fold re-derives the edge.
const ExpiryIdle = 30 * 24 * time.Hour

// LastActivity is a revision's last application, else its own birth.
func LastActivity(rev *LearnedRevision, exps []Exposure) time.Time {
	last := rev.At
	for _, e := range exps {
		if e.At.After(last) {
			last = e.At
		}
	}
	return last
}

// Exposure is one proof that a revision reached a run: an application (a
// lesson in a request) or a policy application (a policy at the boundary).
// Tenure and expiry count both — a policy revision is "used" when an
// attempt ran under it.
type Exposure struct {
	ID       record.RecordID
	Revision record.RecordID
	Seq      uint64
	At       time.Time
}

// tenureLegal are the edges tenure may take: candidate→observed, and
// observed|candidate→tombstone (expiry).
var tenureLegal = map[Stage]map[Stage]bool{Candidate: {Observed: true, Tombstone: true}, Observed: {Tombstone: true}}

// RecallSelection is the recorded result of one recall query: what was
// considered, what was included in deterministic order, why the rest was
// not (counts by reason plus the top-k), and the projected size — a
// number reported, never a cap (D13).
type RecallSelection struct {
	record.ProductionRecord
	record.Header  `json:"header"`
	Purpose        invoke.Purpose `json:"purpose"`
	Scope          []ScopePath    `json:"scope"`
	Family         string         `json:"family,omitempty"`
	Standing       []Stage        `json:"standing"` // the selectable set the query used
	Considered     int            `json:"considered"`
	Included       []ItemRev      `json:"included"`
	ExcludedCounts map[string]int `json:"excluded_counts"`
	ExcludedSample []Excluded     `json:"excluded_sample,omitempty"` // the first SampleK excluded, in item order
	ProjectedBytes int64          `json:"projected_bytes"`
	// Continues names an earlier attempt's selection of the same run that
	// this attempt executes unchanged: the recovered attempt committed its
	// decision and died before any request existed, so the new attempt
	// starts from that committed stage instead of deciding again over a
	// ledger that may have moved (§5a). The fold checks equality with it
	// instead of recomputing.
	Continues record.RecordID `json:"continues,omitempty"`
	// Policy names the attempt's policy selection this recall obeyed: with
	// MechRecall off every item is excluded (reason policy:recall_off) and
	// the request is the goal alone.
	Policy record.RecordID `json:"policy"`
}

// SampleK bounds the exclusion projection (§14: counts by reason + a
// sample, not a row per excluded item). The sample is the first K excluded
// items in item-id order — a deterministic sample, not a ranking. Why 5:
// enough to see WHICH items an operator expected and did not get; the
// counts carry the rest.
const SampleK = 5

func (r *RecallSelection) Head() *record.Header { return &r.Header }
func (r *RecallSelection) Kind() record.Kind    { return KindRecall }
func (r *RecallSelection) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 {
		return errors.New("recall_selection: needs run_id and attempt")
	}
	if r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("recall_selection: subject must be the run")
	}
	switch r.Purpose {
	case invoke.PurposeExecute, invoke.PurposeJudge, invoke.PurposePlan, invoke.PurposeIntent, invoke.PurposeRender:
	default:
		return fmt.Errorf("recall_selection: purpose %q out of vocabulary", r.Purpose)
	}
	if len(r.Scope) == 0 {
		return errors.New("recall_selection: scope is empty")
	}
	for _, s := range r.Scope {
		if err := validScope(s); err != nil {
			return fmt.Errorf("recall_selection: %w", err)
		}
	}
	if len(r.Standing) == 0 {
		return errors.New("recall_selection: standing is empty")
	}
	for _, s := range r.Standing {
		if !stages[s] {
			return fmt.Errorf("recall_selection: standing %q out of vocabulary", s)
		}
	}
	if r.Considered < len(r.Included) {
		return errors.New("recall_selection: included exceeds considered")
	}
	excluded := 0
	for reason, n := range r.ExcludedCounts {
		if reason == "" || n < 1 {
			return errors.New("recall_selection: malformed exclusion count")
		}
		excluded += n
	}
	if r.Considered != len(r.Included)+excluded {
		return fmt.Errorf("recall_selection: considered %d ≠ included %d + excluded %d", r.Considered, len(r.Included), excluded)
	}
	if len(r.ExcludedSample) > SampleK || len(r.ExcludedSample) > excluded {
		return errors.New("recall_selection: excluded_sample exceeds its bound")
	}
	if r.Continues != "" {
		if err := record.ValidateID(r.Continues); err != nil {
			return fmt.Errorf("recall_selection: continues: %w", err)
		}
	}
	if err := record.ValidateID(r.Policy); err != nil {
		return fmt.Errorf("recall_selection: policy: %w", err)
	}
	for i := 1; i < len(r.Included); i++ {
		if r.Included[i-1].Item >= r.Included[i].Item {
			return errors.New("recall_selection: included is not in item order")
		}
	}
	if r.ProjectedBytes < 0 {
		return errors.New("recall_selection: negative projected bytes")
	}
	return nil
}

func now() time.Time { return time.Now().UTC() }

func init() {
	reg := func(k record.Kind, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindRevision, LearnedRevision{}, "Propose (operator CLI in v1; the tail in step 9; import at candidate in step 12)",
		"learn.Fold (current revision per item); Recall; experiments name ItemRevs (step 10)",
		"what a lesson says, where it applies; a new revision starts at candidate and must earn its standing")
	reg(KindTransition, LifecycleTransition{}, "operator restamp (v1); tenure (step 9); measurement (steps 10–11)",
		"learn.Fold (standing per revision); Recall (selectability); PolicySelection (step 10)",
		"whether THIS revision may be selected; quarantine/tombstone are never selected")
	reg(KindApplication, Application{}, "the driver, after the invocation the lesson was rendered into exists",
		"run.Fold (applications equal the recall's included set); exposure (ArmObservation, step 10)",
		"proof that a revision reached a request: its representation is in the request hash")
	reg(KindPolicySelection, PolicySelection{}, "the driver, in the attempt's own command (step 10)",
		"learn.Fold (re-derives it); run.Fold (the attempt's config snapshot equals it); operators (why is mechanism X off)",
		"which mechanisms this attempt runs with, and which policy revisions decided it")
	reg(KindPolicyApplication, PolicyApplication{}, "the driver, with the selection that enabled the revision (step 10)",
		"learn.Fold (one per enabled revision; exposure for tenure/expiry); evaluation of exposure (step 10)",
		"proof that a policy revision reached the boundary: its rule was in the snapshot")
	reg(KindRecall, RecallSelection{}, "the driver, before every recall-bearing invocation",
		"the driver on resume (re-derive applications for a reused invocation); operators (why was X not recalled); evaluation of exposure (step 10)",
		"which revisions reached the request, in what order, and why the rest did not")
}
