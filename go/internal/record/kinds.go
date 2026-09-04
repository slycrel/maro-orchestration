package record

import "reflect"

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
