// Package run is the spine: goals, attempts, the run state machine, and
// delivery. Stages are pure decision functions; the Driver is the one shell
// that makes invocations, submits commits, and emits lifecycle events
// (design note §3, §5, §5a, §12).
//
// Two outcomes, both folds (§5a): the EXECUTION outcome settles at
// `recorded`; the MISSION outcome is execution ⊗ delivery state under the
// goal's DeliveryPolicy. Nothing below a client-generated, payload-bound ack
// is ever labelled user delivery (§12).
package run

import (
	"crypto/sha256"
	"encoding/hex"
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
	KindGoal              record.Kind = "goal"
	KindFamilyAssessment  record.Kind = "family_assessment"
	KindRunAttempt        record.Kind = "run_attempt"
	KindTransition        record.Kind = "run_transition"
	KindDeliveryPrepared  record.Kind = "delivery_prepared"
	KindDeliveryStarted   record.Kind = "delivery_started"
	KindDeliveryAttempted record.Kind = "delivery_attempted"
	KindDeliveryAcked     record.Kind = "delivery_acked"
)

// Lane is the driver configuration name. The shared spec registers `lane`
// as a vocabulary on the outcomes ledger (CONTRACTS B3/B6: now | agenda);
// the engine keeps the vocabulary, not the prototype's code split. Only
// `now` has a producer in step 5; `agenda` is added with its driver
// configuration (step 7), additively.
type Lane string

const LaneNow Lane = "now"

var lanes = map[Lane]bool{LaneNow: true}

// GoalOrigin says where a goal entered. v1: the CLI.
type GoalOrigin string

const (
	OriginCLI    GoalOrigin = "cli"    // an in-process verb: the payload is written to the verb's stdout
	OriginSocket GoalOrigin = "socket" // a client of the always-on process: the payload goes back over its connection
)

var origins = map[GoalOrigin]bool{OriginCLI: true, OriginSocket: true}

// DeliveryState names what a delivery step PROVED (§12). `endpoint_accepted`
// has no v1 producer (it needs a program origin) and is not in the
// vocabulary until one exists.
type DeliveryState string

const (
	TransportAccepted DeliveryState = "transport_accepted" // the transport took the payload
	UserAcknowledged  DeliveryState = "user_acknowledged"  // a client-generated, payload-bound ack arrived
	DeliveryFailed    DeliveryState = "failed"             // this attempt did not get the payload out
	DeliveryUnknown   DeliveryState = "unknown"            // the process died after the presentation started; the user may have seen it
)

// requiredStates is what a DeliveryPolicy may demand in v1 (CLI origin).
var requiredStates = map[DeliveryState]bool{TransportAccepted: true, UserAcknowledged: true}
var attemptResults = map[DeliveryState]bool{TransportAccepted: true, DeliveryFailed: true, DeliveryUnknown: true}

// DeliveryPolicy is captured at intake per origin: the state that counts as
// delivered for THIS goal.
type DeliveryPolicy struct {
	Required DeliveryState `json:"required"`
}

// Goal is the intake record: the text is a thought (unconstrained, whole);
// the policy is process data. Parent/Root are set by fork (step 8); a root
// goal names itself as Root.
type Goal struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Parent        record.RecordID `json:"parent,omitempty"`
	Root          record.RecordID `json:"root"`
	Text          thought.Ref     `json:"text"`
	Origin        GoalOrigin      `json:"origin"`
	Lane          Lane            `json:"lane"` // the driver configuration this goal is routed to (explicit in v1)
	Delivery      DeliveryPolicy  `json:"delivery"`
}

func (r *Goal) Head() *record.Header { return &r.Header }
func (r *Goal) Kind() record.Kind    { return KindGoal }
func (r *Goal) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "goal" || r.Subject.ID != string(r.ID) {
		return errors.New("goal: subject must be the goal itself")
	}
	if r.Root == "" {
		return errors.New("goal: root is required")
	}
	if r.Parent == "" && r.Root != r.ID {
		return errors.New("goal: a root goal names itself as root")
	}
	if err := r.Text.Validate(); err != nil {
		return fmt.Errorf("goal: text: %w", err)
	}
	if r.Text.Kind != thought.Goal {
		return fmt.Errorf("goal: text must be a goal thought, got %q", r.Text.Kind)
	}
	if !origins[r.Origin] {
		return fmt.Errorf("goal: origin %q out of vocabulary", r.Origin)
	}
	if !lanes[r.Lane] {
		return fmt.Errorf("goal: lane %q out of vocabulary", r.Lane)
	}
	if !requiredStates[r.Delivery.Required] {
		return fmt.Errorf("goal: delivery.required %q out of vocabulary", r.Delivery.Required)
	}
	return nil
}

// FamilyKey is the treatment-blind population label an experiment is
// scoped to (§5, §8a). `none` = unmatched or ambiguous: ineligible.
type FamilyKey string

const FamilyNone FamilyKey = "none"

// FamilyAssessment is produced at intake by a deterministic classifier over
// the goal text only (never the treatment, never the model's intent), and
// is never revised for the current run.
type FamilyAssessment struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Goal          record.RecordID `json:"goal"`
	Family        FamilyKey       `json:"family"`
	Rule          string          `json:"rule"` // registered rule version
	Reason        string          `json:"reason"`
}

func (r *FamilyAssessment) Head() *record.Header { return &r.Header }
func (r *FamilyAssessment) Kind() record.Kind    { return KindFamilyAssessment }
func (r *FamilyAssessment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "goal" || r.Subject.ID != string(r.Goal) {
		return errors.New("family_assessment: subject must be the assessed goal")
	}
	if err := record.ValidateID(r.Goal); err != nil {
		return fmt.Errorf("family_assessment: goal: %w", err)
	}
	if !families[r.Family] {
		return fmt.Errorf("family_assessment: family %q out of vocabulary", r.Family)
	}
	if r.Rule != FamilyRule {
		return fmt.Errorf("family_assessment: rule %q is not the registered rule %s", r.Rule, FamilyRule)
	}
	return nil
}

// JudgeSelection is the driver's judge parameter. v1 NOW: the executing
// agent's own claim plus deterministic observations; a model judge arrives
// with the AGENDA configuration (step 7), additively.
type JudgeSelection string

const JudgeSelf JudgeSelection = "self_only"

var judges = map[JudgeSelection]bool{JudgeSelf: true}

// ConfigSnapshot is what the attempt ran under: enough to re-run it (step 6)
// and to attribute it (§8). Backend identity is the backend's own declared
// capabilities, snapshotted.
type ConfigSnapshot struct {
	Lane            Lane                `json:"lane"`
	Backend         invoke.Capabilities `json:"backend"`
	Judge           JudgeSelection      `json:"judge"`
	JudgeBackend    invoke.Capabilities `json:"judge_backend,omitempty"` // the tool-less backend judges run on (model judge)
	PlanCardinality int                 `json:"plan_cardinality"`        // 1 for now; 0 for agenda (the plan decides)
	TimeoutMillis   int64               `json:"timeout_ms"`
	FamilyRule      string              `json:"family_rule"`
	ResolverVer     string              `json:"resolver_ver"`
}

// RunAttempt starts an attempt generation of a goal. Attempt 1 is the first
// execution; a later attempt cites the earlier one it recovers from.
type RunAttempt struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Goal          record.RecordID `json:"goal"`
	Family        record.RecordID `json:"family"` // the FamilyAssessment (a reference, never a copy: the record is already journaled)
	Config        ConfigSnapshot  `json:"config"`
	RecoversFrom  uint32          `json:"recovers_from,omitempty"` // the attempt marked recoverable, when >1
}

func (r *RunAttempt) Head() *record.Header { return &r.Header }
func (r *RunAttempt) Kind() record.Kind    { return KindRunAttempt }
func (r *RunAttempt) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("run_attempt: %w", err)
	}
	if err := record.ValidateID(r.Goal); err != nil {
		return fmt.Errorf("run_attempt: goal: %w", err)
	}
	if err := record.ValidateID(r.Family); err != nil {
		return fmt.Errorf("run_attempt: family: %w", err)
	}
	if !lanes[r.Config.Lane] {
		return fmt.Errorf("run_attempt: lane %q out of vocabulary", r.Config.Lane)
	}
	if !judges[r.Config.Judge] {
		return fmt.Errorf("run_attempt: judge %q out of vocabulary", r.Config.Judge)
	}
	switch r.Config.Lane {
	case LaneNow:
		if r.Config.PlanCardinality != 1 || r.Config.Judge != JudgeSelf {
			return errors.New("run_attempt: now = one execute with the self judge")
		}
	case LaneAgenda:
		if r.Config.PlanCardinality != 0 || r.Config.Judge != JudgeModel || r.Config.JudgeBackend.Name == "" {
			return errors.New("run_attempt: agenda = the plan's cardinality with the model judge on a named backend")
		}
	}
	if r.Config.Backend.Name == "" {
		return errors.New("run_attempt: backend snapshot has no name")
	}
	if (r.Attempt == 1) != (r.RecoversFrom == 0) {
		return errors.New("run_attempt: attempt 1 recovers from nothing; later attempts name what they recover from")
	}
	if r.RecoversFrom != 0 && r.RecoversFrom >= r.Attempt {
		return errors.New("run_attempt: recovers_from must be an earlier attempt")
	}
	return nil
}

// State is the run state machine (§5a).
type State string

const (
	Created         State = "created"
	Executing       State = "executing"
	Judged          State = "judged"
	Recorded        State = "recorded"
	Delivered       State = "delivered"
	DeliveryFailedS State = "delivery_failed"
	Recoverable     State = "recoverable" // marked on restart for an attempt that died before recorded
)

var states = map[State]bool{Created: true, Executing: true, Judged: true, Recorded: true, Delivered: true, DeliveryFailedS: true, Recoverable: true}

// next is the legal transition table. Recoverable is legal from any
// pre-recorded state; delivered/delivery_failed only after recorded.
var next = map[State]map[State]bool{
	Created:   {Executing: true, Recoverable: true},
	Executing: {Judged: true, Recoverable: true},
	Judged:    {Recorded: true, Recoverable: true},
	Recorded:  {Delivered: true, DeliveryFailedS: true},
	Delivered: {Delivered: true}, // transport_accepted → user_acknowledged
}

// Outcome is the execution outcome, a fold the driver computes from the
// records it committed and stamps on the `recorded` transition. A test
// recomputes it from the journal alone.
type Outcome struct {
	Lane       Lane                 `json:"lane"`     // the configuration the attempt ran (equals the attempt's config)
	Terminal   invoke.TerminalState `json:"terminal"` // complete | partial | failed
	Reason     string               `json:"reason,omitempty"`
	Invocation record.RecordID      `json:"invocation,omitempty"`  // the execute invocation (absent when none was made)
	Produced   uint32               `json:"produced_by,omitempty"` // the attempt whose invocation produced the evidence (a recovered attempt reuses an earlier one's)
	Receipt    record.RecordID      `json:"receipt,omitempty"`
	Response   *thought.Ref         `json:"response,omitempty"`
	Usage      invoke.Usage         `json:"usage"`
	Model      string               `json:"model,omitempty"`  // the model of the invocation that produced the evidence (never the recovering attempt's config)
	Recall     record.RecordID      `json:"recall,omitempty"` // the RecallSelection the invocation's request was rendered from
	Steps      int                  `json:"steps,omitempty"`  // AGENDA: steps executed (usage is the sum over the attempt's invocations)
	GoalText   thought.Ref          `json:"goal"`             // the goal thought this attempt ran (whole)
	Closure    record.RecordID      `json:"closure"`          // the closure Resolution
	ClosureOut string               `json:"closure_outcome"`
	ClosureSrc string               `json:"closure_source,omitempty"` // standing of the effective verdict ("" when none)
	ClosureCnf float64              `json:"closure_confidence"`
}

// Transition is one committed step of the run state machine for an attempt.
type Transition struct {
	record.ProductionRecord
	record.Header `json:"header"`
	From          State         `json:"from,omitempty"`
	To            State         `json:"to"`
	Reason        string        `json:"reason,omitempty"`
	Delivery      DeliveryState `json:"delivery,omitempty"` // on delivered: the state reached
	Outcome       *Outcome      `json:"outcome,omitempty"`  // on recorded only
	Evidence      []record.Ref  `json:"evidence,omitempty"`
}

func (r *Transition) Head() *record.Header { return &r.Header }
func (r *Transition) Kind() record.Kind    { return KindTransition }
func (r *Transition) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("run_transition: %w", err)
	}
	if !states[r.To] {
		return fmt.Errorf("run_transition: to %q out of vocabulary", r.To)
	}
	if r.From == "" {
		if r.To != Created {
			return fmt.Errorf("run_transition: only created has no predecessor (got %s)", r.To)
		}
	} else if !states[r.From] || !next[r.From][r.To] {
		return fmt.Errorf("run_transition: %s → %s is not a legal transition", r.From, r.To)
	}
	if (r.To == Recorded) != (r.Outcome != nil) {
		return errors.New("run_transition: recorded carries the outcome; nothing else does")
	}
	if r.Outcome != nil {
		if err := r.Outcome.validate(); err != nil {
			return fmt.Errorf("run_transition: outcome: %w", err)
		}
	}
	if r.To == Delivered {
		if r.Delivery != TransportAccepted && r.Delivery != UserAcknowledged {
			return fmt.Errorf("run_transition: delivered must name transport_accepted or user_acknowledged, got %q", r.Delivery)
		}
	} else if r.Delivery != "" {
		return errors.New("run_transition: only delivered carries a delivery state")
	}
	if (r.To == DeliveryFailedS || r.To == Recoverable) && r.Reason == "" {
		return fmt.Errorf("run_transition: %s needs a reason", r.To)
	}
	return nil
}

func (o *Outcome) validate() error {
	if !lanes[o.Lane] {
		return fmt.Errorf("lane %q out of vocabulary", o.Lane)
	}
	switch o.Terminal {
	case invoke.TerminalComplete, invoke.TerminalPartial, invoke.TerminalFailed:
	default:
		return fmt.Errorf("terminal %q out of vocabulary", o.Terminal)
	}
	if o.Terminal != invoke.TerminalFailed && (o.Receipt == "" || o.Response == nil) {
		return errors.New("a non-failed terminal names its receipt and response")
	}
	if o.Terminal == invoke.TerminalFailed && o.Reason == "" {
		return errors.New("a failed terminal carries a reason")
	}
	if o.Invocation != "" {
		if err := record.ValidateID(o.Invocation); err != nil {
			return err
		}
		if o.Produced == 0 {
			return errors.New("an invocation names the attempt that produced it")
		}
	} else if o.Produced != 0 || o.Receipt != "" {
		return errors.New("produced_by and receipt need an invocation")
	}
	if o.Invocation != "" && o.Recall == "" {
		return errors.New("an invocation names the recall its request was rendered from")
	}
	if o.Recall != "" {
		if err := record.ValidateID(o.Recall); err != nil {
			return fmt.Errorf("recall: %w", err)
		}
	}
	if o.Receipt != "" {
		if err := record.ValidateID(o.Receipt); err != nil {
			return err
		}
	}
	if o.Response != nil {
		if err := o.Response.Validate(); err != nil {
			return err
		}
	}
	if err := o.Usage.Validate(); err != nil {
		return err
	}
	if err := record.ValidateID(o.Closure); err != nil {
		return fmt.Errorf("closure: %w", err)
	}
	if o.ClosureOut == "" {
		return errors.New("closure outcome is required")
	}
	if err := o.GoalText.Validate(); err != nil {
		return fmt.Errorf("goal: %w", err)
	}
	if o.GoalText.Kind != thought.Goal {
		return errors.New("goal must be a goal thought")
	}
	if o.ClosureCnf < 0 || o.ClosureCnf > 1 || o.ClosureCnf != o.ClosureCnf {
		return errors.New("closure confidence out of [0,1]")
	}
	return nil
}

// DeliveryPrepared opens a delivery: the payload is stored BEFORE this
// record claims it; the nonce fixes the ack token (TokenFor) before anything
// is shown. One delivery per attempt; a later attempt's delivery makes the
// earlier one stale.
type DeliveryPrepared struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Payload       thought.Ref   `json:"payload"`
	Origin        GoalOrigin    `json:"origin"`
	Required      DeliveryState `json:"required"`
	Nonce         string        `json:"nonce"` // 32 hex; the ack token is bound to id+payload+nonce
}

func (r *DeliveryPrepared) Head() *record.Header { return &r.Header }
func (r *DeliveryPrepared) Kind() record.Kind    { return KindDeliveryPrepared }
func (r *DeliveryPrepared) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("delivery_prepared: %w", err)
	}
	if r.Subject.Kind != "delivery" || r.Subject.ID != string(r.ID) {
		return errors.New("delivery_prepared: subject must be the delivery itself")
	}
	if err := r.Payload.Validate(); err != nil {
		return fmt.Errorf("delivery_prepared: payload: %w", err)
	}
	if r.Payload.Kind != thought.Deliverable {
		return fmt.Errorf("delivery_prepared: payload must be a deliverable thought, got %q", r.Payload.Kind)
	}
	if !origins[r.Origin] {
		return fmt.Errorf("delivery_prepared: origin %q out of vocabulary", r.Origin)
	}
	if !requiredStates[r.Required] {
		return fmt.Errorf("delivery_prepared: required %q out of vocabulary", r.Required)
	}
	if len(r.Nonce) != 32 || !isHex(r.Nonce) {
		return errors.New("delivery_prepared: nonce must be 32 hex")
	}
	return nil
}

// DeliveryStarted is committed BEFORE the outward presentation (the same
// shape as a ToolEffect before its action): after a crash, a start without a
// result says "the user may have seen it", which the next presentation
// then says out loud. An ack is valid from the start on — the token only
// ever reaches a client through a presentation.
type DeliveryStarted struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Delivery      record.RecordID `json:"delivery"`
	N             int             `json:"n"`
}

func (r *DeliveryStarted) Head() *record.Header { return &r.Header }
func (r *DeliveryStarted) Kind() record.Kind    { return KindDeliveryStarted }
func (r *DeliveryStarted) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := deliveryScoped(&r.Header, r.Delivery, "delivery_started"); err != nil {
		return err
	}
	if r.N < 1 {
		return errors.New("delivery_started: n starts at 1")
	}
	return nil
}

// DeliveryAttempted is one outbox attempt's result.
type DeliveryAttempted struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Delivery      record.RecordID `json:"delivery"`
	N             int             `json:"n"`
	Result        DeliveryState   `json:"result"` // transport_accepted | failed
	Reason        string          `json:"reason,omitempty"`
}

func (r *DeliveryAttempted) Head() *record.Header { return &r.Header }
func (r *DeliveryAttempted) Kind() record.Kind    { return KindDeliveryAttempted }
func (r *DeliveryAttempted) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := deliveryScoped(&r.Header, r.Delivery, "delivery_attempted"); err != nil {
		return err
	}
	if r.N < 1 {
		return errors.New("delivery_attempted: n starts at 1")
	}
	if !attemptResults[r.Result] {
		return fmt.Errorf("delivery_attempted: result %q out of vocabulary", r.Result)
	}
	if r.Result != TransportAccepted && r.Reason == "" {
		return errors.New("delivery_attempted: a failure or unknown carries a reason")
	}
	return nil
}

// DeliveryAcked records a client-generated acknowledgement. The token is
// validated against the fold (TokenFor) by Ack, which is the only writer.
type DeliveryAcked struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Delivery      record.RecordID `json:"delivery"`
	Token         string          `json:"token"`
	PayloadHash   string          `json:"payload_hash"`
}

func (r *DeliveryAcked) Head() *record.Header { return &r.Header }
func (r *DeliveryAcked) Kind() record.Kind    { return KindDeliveryAcked }
func (r *DeliveryAcked) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := deliveryScoped(&r.Header, r.Delivery, "delivery_acked"); err != nil {
		return err
	}
	if len(r.Token) != tokenLen || !isHex(r.Token) {
		return fmt.Errorf("delivery_acked: token must be %d hex", tokenLen)
	}
	if len(r.PayloadHash) != len(thought.HashAlgo)+1+64 || r.PayloadHash[:len(thought.HashAlgo)+1] != thought.HashAlgo+":" || !isHex(r.PayloadHash[len(thought.HashAlgo)+1:]) {
		return errors.New("delivery_acked: payload hash malformed")
	}
	return nil
}

// Interrupt asks the driver to stop a run at its next stage boundary (§5).
// It is consumed only there, acknowledged by a record, and expired when
// the target attempt is already terminal. v1 action: cancel.
type Interrupt struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Target        record.RunID `json:"target"`
	Action        string       `json:"action"` // cancel
	Why           string       `json:"why"`
}

func (r *Interrupt) Head() *record.Header { return &r.Header }
func (r *Interrupt) Kind() record.Kind    { return KindInterrupt }
func (r *Interrupt) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Target == "" || r.Subject != runRef(r.Target) {
		return errors.New("interrupt: subject must be the target run")
	}
	if r.RunID != "" || r.Attempt != 0 {
		return errors.New("interrupt: an interrupt is not scoped to an attempt; it names its target")
	}
	if r.Action != "cancel" {
		return fmt.Errorf("interrupt: action %q out of vocabulary", r.Action)
	}
	if strings.TrimSpace(r.Why) == "" {
		return errors.New("interrupt: needs a why")
	}
	return nil
}

// InterruptAck is the driver's answer: consumed at a boundary of the named
// attempt (the run stops there), or expired because the run was terminal.
type InterruptAck struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Interrupt     record.RecordID `json:"interrupt"`
	Result        string          `json:"result"`             // consumed | expired
	Boundary      string          `json:"boundary,omitempty"` // where it was consumed
}

func (r *InterruptAck) Head() *record.Header { return &r.Header }
func (r *InterruptAck) Kind() record.Kind    { return KindInterruptAck }
func (r *InterruptAck) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := record.ValidateID(r.Interrupt); err != nil {
		return fmt.Errorf("interrupt_ack: %w", err)
	}
	if r.Subject.Kind != "interrupt" || r.Subject.ID != string(r.Interrupt) {
		return errors.New("interrupt_ack: subject must be the interrupt")
	}
	switch r.Result {
	case "consumed":
		if r.RunID == "" || r.Attempt == 0 || r.Boundary == "" {
			return errors.New("interrupt_ack: consumed names the attempt and the boundary")
		}
	case "expired":
		if r.Boundary != "" {
			return errors.New("interrupt_ack: expired has no boundary")
		}
	default:
		return fmt.Errorf("interrupt_ack: result %q out of vocabulary", r.Result)
	}
	return nil
}

const (
	KindInterrupt    record.Kind = "interrupt"
	KindInterruptAck record.Kind = "interrupt_ack"
)

const tokenLen = 32

// TokenFor derives the ack token: bound to the delivery ID and the payload
// hash so an ack for a different payload, or a different delivery, cannot
// be replayed onto this one. The nonce keeps the token unguessable from
// the two public parts.
func TokenFor(delivery record.RecordID, payloadHash, nonce string) string {
	sum := sha256.Sum256([]byte("ack/1|" + string(delivery) + "|" + payloadHash + "|" + nonce))
	return hex.EncodeToString(sum[:])[:tokenLen]
}

// HandleOf is the shared-spec 8-hex handle_id for a run (CONTRACTS B3/B6),
// derived, never authoritative: the RunID is the identity.
func HandleOf(run record.RunID) string {
	sum := sha256.Sum256([]byte("handle/1|" + string(run)))
	return hex.EncodeToString(sum[:])[:8]
}

func runScoped(h *record.Header) error {
	if h.RunID == "" || h.Attempt == 0 {
		return errors.New("run-scoped record needs run_id and attempt")
	}
	return nil
}

func deliveryScoped(h *record.Header, d record.RecordID, what string) error {
	if err := runScoped(h); err != nil {
		return fmt.Errorf("%s: %w", what, err)
	}
	if err := record.ValidateID(d); err != nil {
		return fmt.Errorf("%s: delivery: %w", what, err)
	}
	if h.Subject.Kind != "delivery" || h.Subject.ID != string(d) {
		return fmt.Errorf("%s: subject must be the delivery", what)
	}
	return nil
}

func isHex(s string) bool {
	_, err := hex.DecodeString(s)
	return err == nil && len(s)%2 == 0
}

func now() time.Time { return time.Now().UTC() }

func init() {
	reg := func(k record.Kind, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindGoal, Goal{}, "intake (the driver's Intake stage, from the CLI origin)",
		"the driver (attempt start, recovery); memory scope (goal ancestry, step 6); assignment (step 10)",
		"what the run is for and what counts as delivered; the randomization unit")
	reg(KindFamilyAssessment, FamilyAssessment{}, "intake (treatment-blind classifier, rule "+FamilyRule+")",
		"experiment population membership (step 10); attribution (§8)",
		"which experiments a goal is eligible for; none = ineligible")
	reg(KindRunAttempt, RunAttempt{}, "the driver (attempt start, recovery)",
		"run fold (Runs); re-run identity (step 6); the outcomes view",
		"the config an outcome is attributed to; which attempt a restart continues")
	reg(KindTransition, Transition{}, "the driver at every stage boundary; Ack; the outbox",
		"run fold (Runs, Mission); the outcomes view (B6 row from recorded); learning eligibility (recorded)",
		"where a restart resumes; whether the mission is delivered, unacknowledged, or failed")
	reg(KindDeliveryPrepared, DeliveryPrepared{}, "the driver's Deliver stage",
		"the outbox (what is owed); Ack (token derivation)",
		"which payload is owed to which origin under which policy; the ack token")
	reg(KindDeliveryStarted, DeliveryStarted{}, "the outbox, before each outward presentation",
		"the outbox (a start without a result = outcome unknown, re-present and say so); Ack (a start is the evidence a token could have reached the client)",
		"whether a presentation may have happened; whether an ack can be accepted")
	reg(KindDeliveryAttempted, DeliveryAttempted{}, "the outbox (result of a started presentation; `unknown` stamped on restart for a start the process died inside)",
		"the outbox (bounded retry); Mission fold; the run fold (delivered needs an accepted attempt)",
		"retry, or give up with delivery_failed; how many presentations may have duplicated")
	reg(KindInterrupt, Interrupt{}, "a client of the process (`maro-go interrupt`), via the intake lane",
		"the driver at every stage boundary; the run fold (pending vs acknowledged)",
		"whether the run stops at its next boundary")
	reg(KindInterruptAck, InterruptAck{}, "the driver (consumed at a boundary) or the intake lane (expired: the run was terminal)",
		"clients (`maro-go status`); the run fold",
		"that an interrupt was honoured, where, or why not")
	reg(KindDeliveryAcked, DeliveryAcked{}, "Ack (the CLI ack command, from a client-presented token)",
		"Mission fold; the outcomes view",
		"user_acknowledged — the only state labelled user delivery")
}
