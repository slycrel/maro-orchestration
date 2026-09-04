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
	"fmt"
	"reflect"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Purpose says why a call exists; it is a receipt enum, queried later.
type Purpose string

const (
	PurposeExecute Purpose = "execute"
	PurposeJudge   Purpose = "judge"
	PurposePlan    Purpose = "plan"
	PurposeIntent  Purpose = "intent"
	PurposeRender  Purpose = "render"
)

var purposes = map[Purpose]bool{PurposeExecute: true, PurposeJudge: true, PurposePlan: true, PurposeIntent: true, PurposeRender: true}

// Capabilities is what a backend declares about itself, snapshotted into the
// Invocation at decision time so the decision and its receipt agree.
type Capabilities struct {
	Name                string `json:"name"`
	Model               string `json:"model"`
	ActsOutward         bool   `json:"acts_outward"`         // can this backend act on the world (tools, network)?
	OutwardReconcilable bool   `json:"outward_reconcilable"` // does it announce effects BEFORE acting (key handshake)?
	ReadsByReference    bool   `json:"reads_by_reference"`   // can it read an artifact handed by path?
	MaxInputBytes       int64  `json:"max_input_bytes"`      // 0 = unknown/unbounded as far as this engine knows
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
	EffectToken   string       `json:"effect_token"` // hex, the namespace per-effect keys derive from
	TargetName    string       `json:"target_name,omitempty"`
	TargetLimit   int64        `json:"target_limit,omitempty"`
	TargetWhy     string       `json:"target_why,omitempty"`
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
	if r.Backend.Name == "" {
		return fmt.Errorf("invocation: backend name empty")
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

// ToolEffect is one tool action the backend performed, as evidence. Key is
// derived from the invocation's token and the ordinal. Announced says whether
// the key was requested BEFORE the action (the handshake) or derived after
// the fact from the stream (post-hoc evidence).
type ToolEffect struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Ordinal       int             `json:"ordinal"`
	Op            string          `json:"op"`    // the tool name as the backend reported it
	Class         OperationClass  `json:"class"` // from the registered operation table; unknown ops are irreversible
	Key           string          `json:"key"`   // derive(effect_token, ordinal)
	Announced     bool            `json:"announced"`
	IsError       bool            `json:"is_error"`
	Evidence      thought.Ref     `json:"evidence"` // the tool's input+output as the backend reported them
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
	return r.Evidence.Validate()
}

// TerminalState is how the backend's stream ended.
type TerminalState string

const (
	TerminalComplete TerminalState = "complete" // a result arrived and the stream closed cleanly
	TerminalPartial  TerminalState = "partial"  // a result arrived but later frames were malformed
	TerminalFailed   TerminalState = "failed"   // no usable result (backend error, timeout, kill)
)

// TerminalObserved is committed the moment the shell knows how the stream
// ended, BEFORE the receipt: a crash between the two leaves a terminal fact.
type TerminalObserved struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Attempt       int             `json:"attempt"`
	State         TerminalState   `json:"state"`
	Reason        string          `json:"reason,omitempty"`
	Transcript    *thought.Ref    `json:"transcript,omitempty"` // the raw captured stream, when kept
}

func (r *TerminalObserved) Head() *record.Header { return &r.Header }
func (r *TerminalObserved) Kind() record.Kind    { return KindTerminalObserved }
func (r *TerminalObserved) ValidateWire() error {
	if err := validRef(r.Header, r.Invocation); err != nil {
		return err
	}
	switch r.State {
	case TerminalComplete, TerminalPartial, TerminalFailed:
	default:
		return fmt.Errorf("terminal_observed: state %q out of vocabulary", r.State)
	}
	if r.Attempt < 1 {
		return fmt.Errorf("terminal_observed: attempt must be >= 1")
	}
	return nil
}

// Usage is what the backend reported about the call. Zero means "not
// reported", never "free".
type Usage struct {
	InputTokens  int64   `json:"input_tokens"`
	OutputTokens int64   `json:"output_tokens"`
	CacheRead    int64   `json:"cache_read_tokens"`
	CostUSD      float64 `json:"cost_usd"`
	WallMillis   int64   `json:"wall_ms"`
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
		Writer: "invoke.Shell (as the backend stream reports)", Reader: "judges (provenance/fabrication); Reconcile; the receipts view",
		Decision: "what the backend actually did; per-effect keys for reconciliation", Retention: record.Forever})
	record.Register(record.Spec{Kind: KindTerminalObserved, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(TerminalObserved{}),
		Writer: "invoke.Shell", Reader: "invoke.State fold; Reconcile", Decision: "how the stream ended, before any receipt exists", Retention: record.Forever})
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
