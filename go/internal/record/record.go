package record

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// SchemaVer is "<kind>/<n>": the contract version of a record kind (D3).
// Every record carries the version of the kind it was written under, so a
// reader can apply the declared absence semantics of that version.
type SchemaVer string

// Parse splits a SchemaVer into kind and version. Malformed → error, never a
// guessed default: a version we cannot read is a version we must not apply.
func (v SchemaVer) Parse() (Kind, int, error) {
	i := strings.LastIndexByte(string(v), '/')
	if i <= 0 || i == len(v)-1 {
		return "", 0, fmt.Errorf("record: malformed SchemaVer %q", string(v))
	}
	n, err := strconv.Atoi(string(v[i+1:]))
	if err != nil || n < 1 {
		return "", 0, fmt.Errorf("record: malformed SchemaVer %q", string(v))
	}
	return Kind(v[:i]), n, nil
}

// Kind names a record kind ("outcome", "verdict", ...). Kinds are registered
// (registry.go); an unregistered kind cannot be encoded or decoded.
type Kind string

// RunID identifies a run; zero for workspace-scope records.
type RunID string

// AttemptRef names one attempt of one run. A retried run is a new attempt.
type AttemptRef struct {
	Run     RunID  `json:"run"`
	Attempt uint32 `json:"attempt"`
}

// Ref points at a record or a thought by kind and id/hash.
type Ref struct {
	Kind Kind   `json:"kind"`
	ID   string `json:"id"`
}

// Header is the immutable identity, version, order, and subject every record
// carries (design note §1a). Status and standing are folds over records;
// no header field is ever mutated after the sequencer allocates Seq.
type Header struct {
	ID         RecordID  `json:"id"`
	Schema     SchemaVer `json:"schema"`
	Seq        uint64    `json:"seq"`                  // per-workspace monotonic; the ONLY ordering key
	RunID      RunID     `json:"run_id,omitempty"`     // zero for workspace scope
	Attempt    uint32    `json:"attempt,omitempty"`    // run attempt generation
	Subject    Ref       `json:"subject"`              // what this record is about
	Supersedes RecordID  `json:"supersedes,omitempty"` // set ONLY when replacing an earlier assertion of the same subject+kind
	At         time.Time `json:"at"`                   // observation time; never an ordering key
}

// Record is implemented by every durable record type. Envelope() is derived
// from the registry, not chosen by the record, so a type cannot lie about
// its population.
type Record interface {
	Head() *Header
	Kind() Kind
}

var (
	ErrUnregisteredKind = errors.New("record: unregistered kind")
	ErrSchemaMismatch   = errors.New("record: header schema does not match registered kind")
)

// Validate checks a header against the registry: the kind is registered, the
// schema names that kind at a version the registry knows, the ID is
// well-formed, Seq is allocated (non-zero), and a Supersedes, if set, is
// well-formed. Seq==0 is legal only before the sequencer has accepted the
// record; a stored record with Seq==0 is a defect.
func Validate(r Record, stored bool) error {
	h := r.Head()
	if h == nil {
		return errors.New("record: nil header")
	}
	spec, ok := Lookup(r.Kind())
	if !ok {
		return fmt.Errorf("%w: %q", ErrUnregisteredKind, r.Kind())
	}
	k, n, err := h.Schema.Parse()
	if err != nil {
		return err
	}
	if k != r.Kind() || n < 1 || n > spec.Version {
		return fmt.Errorf("%w: %q on kind %q (registry version %d)", ErrSchemaMismatch, h.Schema, r.Kind(), spec.Version)
	}
	if err := ValidateID(h.ID); err != nil {
		return err
	}
	if h.Supersedes != "" {
		if err := ValidateID(h.Supersedes); err != nil {
			return fmt.Errorf("record: Supersedes: %w", err)
		}
	}
	if stored && h.Seq == 0 {
		return errors.New("record: stored record has no Seq")
	}
	if h.At.IsZero() {
		return errors.New("record: At is zero")
	}
	return nil
}
