package record

import (
	"fmt"
	"reflect"
)

// Step-1 record kinds. Later steps register their own kinds in their own
// packages; the registry is one authority, populated at init.

// LeaseRecord is the workspace admission fact (design note §2): which process
// epoch holds the root. It is a control-plane fact about the workspace, not
// about any run.
type LeaseRecord struct {
	ControlRecord
	Header `json:"header"`
	PID    int    `json:"pid"`
	Epoch  uint64 `json:"epoch"`
	Host   string `json:"host"`
}

func (r *LeaseRecord) Head() *Header { return &r.Header }
func (r *LeaseRecord) Kind() Kind    { return KindLease }

// ValidateWire executes the declared vocabulary: pid and epoch are positive.
func (r *LeaseRecord) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.PID <= 0 {
		return fmt.Errorf("lease: pid %d out of vocabulary", r.PID)
	}
	if r.Epoch == 0 {
		return fmt.Errorf("lease: epoch 0 out of vocabulary")
	}
	return nil
}

// ThoughtStored records that a thought entered the store: the hash is the
// content address, the encoding says what a text backend may be handed. The
// body itself lives in the thought store, never in the journal.
type ThoughtStored struct {
	ProductionRecord
	Header   `json:"header"`
	Hash     string `json:"hash"`
	Thought  string `json:"thought_kind"`
	Bytes    int64  `json:"bytes"`
	Encoding string `json:"encoding"` // utf8 | bytes
}

func (r *ThoughtStored) Head() *Header { return &r.Header }
func (r *ThoughtStored) Kind() Kind    { return KindThoughtStored }

var thoughtKinds = map[string]bool{"goal": true, "prompt": true, "response": true, "step_result": true, "deliverable": true, "lesson_text": true, "step": true}

// ValidateWire executes the declared vocabulary: kind and encoding are closed
// sets, the hash carries the versioned algorithm prefix. The hash's CONTENT
// is unconstrained (it addresses an unconstrained body); its shape is not.
func (r *ThoughtStored) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if !thoughtKinds[r.Thought] {
		return fmt.Errorf("thought_stored: kind %q out of vocabulary", r.Thought)
	}
	if r.Encoding != "utf8" && r.Encoding != "bytes" {
		return fmt.Errorf("thought_stored: encoding %q out of vocabulary", r.Encoding)
	}
	if len(r.Hash) != len("s256v1:")+64 || r.Hash[:7] != "s256v1:" {
		return fmt.Errorf("thought_stored: hash %q is not s256v1:<64 hex>", r.Hash)
	}
	if r.Bytes < 0 {
		return fmt.Errorf("thought_stored: negative bytes")
	}
	return nil
}

const (
	KindLease         Kind = "lease"
	KindThoughtStored Kind = "thought_stored"
)

func init() {
	Register(Spec{
		Kind: KindLease, Envelope: Control, Version: 1, Type: reflect.TypeOf(LeaseRecord{}),
		Writer: "workspace.Lease.Acquire", Reader: "workspace.Lease (admission check on start)",
		Decision: "refuse a second process on the same root; take over a stale lease with epoch+1", Retention: Bounded,
	})
	Register(Spec{
		Kind: KindThoughtStored, Envelope: Production, Version: 1, Type: reflect.TypeOf(ThoughtStored{}),
		Writer: "thought.Store.Put", Reader: "thought.Store.Get (hash re-verified on read); receipts and edges resolve refs through it",
		Decision: "resolve a ThoughtRef to whole bytes or refuse (tamper/absent) — never a partial body", Retention: Forever,
	})
}
