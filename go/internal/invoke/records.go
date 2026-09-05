// Package invoke is the invocation boundary (design note §4): the one place
// a backend is called. The run shell owns a state machine whose every
// transition is a journal record — prepared → dispatched → attempts and tool
// effects as the stream reports them → terminal_observed → receipt — so a
// crash at any point leaves evidence, never a guess. Adapters are effectful
// boundary components; they never record.
package invoke

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"path/filepath"
	"reflect"
	"regexp"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Purpose says why a call exists; it is a receipt enum, queried later.
type Purpose string

const (
	PurposeExecute   Purpose = "execute"
	PurposeJudge     Purpose = "judge"
	PurposePlan      Purpose = "plan"
	PurposeIntent    Purpose = "intent"
	PurposeRender    Purpose = "render"
	PurposeDiagnose  Purpose = "diagnose"  // the tail's model lens over a recorded run
	PurposeEvaluate  Purpose = "evaluate"  // the experiment evaluator's blinded score of a unit's deliverable
	PurposeLandscape Purpose = "landscape" // the run's relation to prior runs, decided before its first attempt
)

var purposes = map[Purpose]bool{PurposeExecute: true, PurposeJudge: true, PurposePlan: true, PurposeIntent: true, PurposeRender: true, PurposeDiagnose: true, PurposeEvaluate: true, PurposeLandscape: true}

// Capabilities is what a backend declares about itself, snapshotted into the
// Invocation at decision time so the decision and its receipt agree.
type Capabilities struct {
	Name                string `json:"name"`
	Model               string `json:"model"`
	ActsOutward         bool   `json:"acts_outward"`          // can this backend act on the world (tools, network)?
	OutwardReconcilable bool   `json:"outward_reconcilable"`  // does it announce effects BEFORE acting (key handshake)?
	ReadsByReference    bool   `json:"reads_by_reference"`    // can it read an artifact handed by path?
	MaxInputBytes       int64  `json:"max_input_bytes"`       // 0 = unknown/unbounded as far as this engine knows
	ToolPolicy          string `json:"tool_policy,omitempty"` // the operator's tool policy, canonical (ToolPolicy.String); "" = the backend's whole set
}

// Invocation is the `prepared` state: the exact backend-visible request, the
// backend snapshot, the purpose, and the effect-token namespace, all
// committed BEFORE dispatch.
type Invocation struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Purpose       Purpose      `json:"purpose"`
	Request       thought.Ref  `json:"request"`
	Backend       Capabilities `json:"backend"`
	Tools         bool         `json:"tools,omitempty"` // the request offered tools; false = confined: any reported effect is refused
	Cwd           string       `json:"cwd,omitempty"`   // the working directory an agentic backend ran in (absolute); "" = the process's own
	EffectToken   string       `json:"effect_token"`    // hex, the namespace per-effect keys derive from
	TargetName    string       `json:"target_name,omitempty"`
	TargetLimit   int64        `json:"target_limit,omitempty"`
	TargetWhy     string       `json:"target_why,omitempty"`
	// Lens: the persona lens a judge/render request was rendered under
	// (§13). Absent = neutral. Present ⇒ the request body begins with the
	// lens text (the run fold checks the bytes).
	Lens *Lens `json:"lens,omitempty"`
}

// Lens names a persona lens and the exact text it prefixes a request with.
type Lens struct {
	Name string      `json:"name"`
	Text thought.Ref `json:"text"`
}

// lensPurposes: the purposes a lens may be applied to. Execute, plan,
// intent, diagnose, and evaluate requests are never lensed — a persona
// colours judgement and rendering, not what the work is.
var lensPurposes = map[Purpose]bool{PurposeJudge: true, PurposeRender: true}

// LensAllowed says whether a lens may ride a request of this purpose.
func LensAllowed(p Purpose) bool { return lensPurposes[p] }

// lensName: a lens is named like an identifier, never free text.
var lensName = regexp.MustCompile(`^[a-z][a-z0-9_-]*$`)

func (l *Lens) validate(p Purpose) error {
	if l == nil {
		return nil
	}
	if l.Name == "" {
		return errors.New("invocation: lens without a name")
	}
	if !lensName.MatchString(l.Name) {
		return fmt.Errorf("invocation: lens name %q is not a name (lowercase letters, digits, _ -)", l.Name)
	}
	if err := l.Text.Validate(); err != nil {
		return fmt.Errorf("invocation: lens text: %w", err)
	}
	if l.Text.Bytes == 0 {
		return errors.New("invocation: a lens with no text is the neutral lens — carry none")
	}
	if l.Text.Kind != thought.LensText {
		return fmt.Errorf("invocation: lens text is a %s thought, not lens_text", l.Text.Kind)
	}
	if !lensPurposes[p] {
		return fmt.Errorf("invocation: a %s request cannot carry a lens", p)
	}
	return nil
}

func (r *Invocation) Head() *record.Header { return &r.Header }
func (r *Invocation) Kind() record.Kind    { return KindInvocation }
func (r *Invocation) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if !purposes[r.Purpose] {
		return fmt.Errorf("invocation: purpose %q out of vocabulary", r.Purpose)
	}
	if err := r.Request.Validate(); err != nil {
		return err
	}
	if len(r.EffectToken) != 32 {
		return fmt.Errorf("invocation: effect_token must be 32 hex chars")
	}
	if err := r.Backend.Validate(); err != nil {
		return fmt.Errorf("invocation: backend: %w", err)
	}
	if r.Cwd != "" && !filepath.IsAbs(r.Cwd) {
		return fmt.Errorf("invocation: cwd %q is not absolute", r.Cwd)
	}
	if err := r.Lens.validate(r.Purpose); err != nil {
		return err
	}
	if (r.TargetName == "") != (r.TargetWhy == "") {
		return fmt.Errorf("invocation: a target needs both a name and a why")
	}
	return nil
}

// Dispatched marks that the backend was handed the request. From here until
// TerminalObserved the invocation is presumed effectful.
type Dispatched struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
}

func (r *Dispatched) Head() *record.Header { return &r.Header }
func (r *Dispatched) Kind() record.Kind    { return KindDispatched }
func (r *Dispatched) ValidateWire() error  { return validRef(r.Header, r.Invocation) }

// OperationClass is the registered class of a tool operation.
type OperationClass string

const (
	OpQuery        OperationClass = "query"
	OpWriteLocal   OperationClass = "write_local"
	OpReversible   OperationClass = "reversible"
	OpIrreversible OperationClass = "irreversible"
)

// ToolEffect is one tool action OBSERVED as the backend announced it (a
// tool_use frame), committed at that moment — before any result exists.
// Key is derived from the invocation's token and the ordinal. Announced says
// whether the key was requested BEFORE the action (the handshake) or the
// action was merely observed on the stream (post-hoc evidence).
type ToolEffect struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Ordinal       int             `json:"ordinal"`
	Op            string          `json:"op"`    // the tool name as the backend reported it
	Class         OperationClass  `json:"class"` // from the registered operation table; unknown ops are irreversible
	Key           string          `json:"key"`   // derive(effect_token, ordinal)
	Announced     bool            `json:"announced"`
	Refused       bool            `json:"refused,omitempty"` // reported on a tool-less request: recorded as evidence, and the invocation fails
	Input         thought.Ref     `json:"input"`             // the raw input bytes as the backend reported them
}

func (r *ToolEffect) Head() *record.Header { return &r.Header }
func (r *ToolEffect) Kind() record.Kind    { return KindToolEffect }
func (r *ToolEffect) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	switch r.Class {
	case OpQuery, OpWriteLocal, OpReversible, OpIrreversible:
	default:
		return fmt.Errorf("tool_effect: class %q out of vocabulary", r.Class)
	}
	if r.Ordinal < 0 || r.Op == "" || len(r.Key) != 64 {
		return fmt.Errorf("tool_effect: ordinal/op/key malformed")
	}
	if r.Class != ClassOf(r.Op) {
		return fmt.Errorf("tool_effect: class %q disagrees with the operation table for %q", r.Class, r.Op)
	}
	return r.Input.Validate()
}

// ToolEffectResult is the tool's answer, committed when the stream reports
// it. An observed effect with no result is exactly that: observed, outcome
// unknown — never "error", never "done".
type ToolEffectResult struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Ordinal       int             `json:"ordinal"`
	IsError       bool            `json:"is_error"`
	Output        thought.Ref     `json:"output"` // raw output bytes; JSON when the backend gave JSON
}

func (r *ToolEffectResult) Head() *record.Header { return &r.Header }
func (r *ToolEffectResult) Kind() record.Kind    { return KindToolEffectResult }
func (r *ToolEffectResult) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	if r.Ordinal < 0 {
		return fmt.Errorf("tool_effect_result: negative ordinal")
	}
	return r.Output.Validate()
}

// TerminalState is how the backend's stream ended.
type TerminalState string

const (
	TerminalComplete TerminalState = "complete" // a result arrived and the stream closed cleanly
	TerminalPartial  TerminalState = "partial"  // a result arrived but later frames were malformed
	TerminalFailed   TerminalState = "failed"   // no usable result (backend error, timeout, kill)
)

// TerminalObserved is committed the moment the shell knows how the stream
// ended AND has the response and transcript safely in the thought store —
// it carries their refs and the usage, so a Receipt is DERIVABLE from it: a
// crash between terminal and receipt is finalized on restart, never lost.
type TerminalObserved struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Attempt       int             `json:"attempt"`
	State         TerminalState   `json:"state"`
	Reason        string          `json:"reason,omitempty"`
	Response      *thought.Ref    `json:"response,omitempty"`   // present iff state is complete|partial
	Transcript    *thought.Ref    `json:"transcript,omitempty"` // the raw captured stream, when kept
	Usage         Usage           `json:"usage"`
}

func (r *TerminalObserved) Head() *record.Header { return &r.Header }
func (r *TerminalObserved) Kind() record.Kind    { return KindTerminalObserved }
func (r *TerminalObserved) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	switch r.State {
	case TerminalComplete, TerminalPartial:
		if r.Response == nil {
			return fmt.Errorf("terminal_observed: %s without a response ref", r.State)
		}
		if err := r.Response.Validate(); err != nil {
			return err
		}
	case TerminalFailed:
		if r.Response != nil {
			return fmt.Errorf("terminal_observed: failed with a response ref")
		}
	default:
		return fmt.Errorf("terminal_observed: state %q out of vocabulary", r.State)
	}
	if r.Attempt < 1 {
		return fmt.Errorf("terminal_observed: attempt must be >= 1")
	}
	if r.Transcript != nil {
		if err := r.Transcript.Validate(); err != nil {
			return err
		}
	}
	return r.Usage.Validate()
}

// Usage is what the backend reported about the call. CostReported says
// whether the backend reported a cost at all: an absent cost is unknown,
// never free.
type Usage struct {
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	CacheRead    int64   `json:"cache_read_tokens"`
	CostUSD      float64 `json:"cost_usd"`
	CostReported bool    `json:"cost_reported"`
	WallMillis   int64   `json:"wall_ms"`
}

// Validate refuses negative or non-finite usage.
func (u Usage) Validate() error {
	if u.InputTokens < 0 || u.OutputTokens < 0 || u.CacheRead < 0 || u.WallMillis < 0 {
		return fmt.Errorf("usage: negative count")
	}
	if u.CostUSD < 0 || math.IsNaN(u.CostUSD) || math.IsInf(u.CostUSD, 0) {
		return fmt.Errorf("usage: cost %v is not a non-negative finite number", u.CostUSD)
	}
	if !u.CostReported && u.CostUSD != 0 {
		return fmt.Errorf("usage: cost present but not marked reported")
	}
	return nil
}

// Receipt is the last state: the response as a thought, and usage.
type Receipt struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Attempt       int             `json:"attempt"`
	Response      thought.Ref     `json:"response"`
	Usage         Usage           `json:"usage"`
}

func (r *Receipt) Head() *record.Header { return &r.Header }
func (r *Receipt) Kind() record.Kind    { return KindReceipt }
func (r *Receipt) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	if r.Attempt < 1 {
		return fmt.Errorf("receipt: attempt must be >= 1")
	}
	if err := r.Usage.Validate(); err != nil {
		return err
	}
	return r.Response.Validate()
}

// Disposition is what restart reconciliation decided about an invocation
// found dispatched without a terminal.
type Disposition string

const (
	DispositionAbandoned     Disposition = "abandoned"                     // backend cannot act outward: safe to retry
	DispositionIndeterminate Disposition = "indeterminate_external_effect" // outward-capable, unreconcilable: escalate, never replay
)

// Reconciled is the restart verdict on one orphaned invocation.
type Reconciled struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Disposition   Disposition     `json:"disposition"`
	Evidence      string          `json:"evidence"` // why: capabilities, committed effects
}

func (r *Reconciled) Head() *record.Header { return &r.Header }
func (r *Reconciled) Kind() record.Kind    { return KindReconciled }
func (r *Reconciled) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	if r.Disposition != DispositionAbandoned && r.Disposition != DispositionIndeterminate {
		return fmt.Errorf("reconciled: disposition %q out of vocabulary", r.Disposition)
	}
	return nil
}

func validRef(h record.Header, inv record.RecordID) error {
	if err := h.ValidateWire(); err != nil {
		return err
	}
	return record.ValidateID(inv)
}

const (
	KindInvocation       record.Kind = "invocation"
	KindDispatched       record.Kind = "invocation_dispatched"
	KindToolEffect       record.Kind = "tool_effect"
	KindToolEffectResult record.Kind = "tool_effect_result"
	KindTerminalObserved record.Kind = "terminal_observed"
	KindReceipt          record.Kind = "receipt"
	KindReconciled       record.Kind = "invocation_reconciled"
)

func init() {
	record.Register(record.Spec{Kind: KindInvocation, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Invocation{}),
		Writer: "invoke.Shell.Invoke (prepared, before dispatch)", Reader: "invoke.State fold; Reconcile on restart; the receipts view",
		Decision: "what was asked of which backend under which purpose/target; the effect-token namespace", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindDispatched, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Dispatched{}),
		Writer: "invoke.Shell.Invoke", Reader: "invoke.Reconcile", Decision: "an invocation without a terminal is presumed effectful", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindToolEffect, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ToolEffect{}),
		Writer: "invoke.Shell (the moment the backend announces a tool_use)", Reader: "judges (provenance/fabrication); Reconcile; the receipts view",
		Decision: "what the backend set out to do; per-effect keys for reconciliation", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindToolEffectResult, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ToolEffectResult{}),
		Writer: "invoke.Shell (as the backend stream reports a tool_result)", Reader: "judges; Reconcile (an observed effect without a result is outcome-unknown)",
		Decision: "what the tool answered; is_error", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindTerminalObserved, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(TerminalObserved{}),
		Writer: "invoke.Shell (after response + transcript are stored)", Reader: "invoke.State fold; Reconcile (finalizes a missing receipt from it)", Decision: "how the stream ended; the receipt is derivable from it", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindReceipt, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Receipt{}),
		Writer: "invoke.Shell", Reader: "the run driver (response); metering; the receipts view; experiments (replay)",
		Decision: "the response and its cost", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindReconciled, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(Reconciled{}),
		Writer: "invoke.Reconcile (on restart)", Reader: "the run driver (retry vs escalate); the supervisor health line",
		Decision: "retry an abandoned call or escalate an indeterminate one — never blind replay", Retention: record.Forever})
}

// NewEffectToken allocates the per-invocation namespace.
func NewEffectToken() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(b[:]), nil
}

// DeriveKey is the per-effect idempotency key: sha256(token || ordinal).
func DeriveKey(token string, ordinal int) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s:%d", token, ordinal)))
	return hex.EncodeToString(h[:])
}
