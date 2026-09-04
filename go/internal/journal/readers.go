package journal

import (
	"errors"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The three reader capabilities. Each is a distinct type; a function that
// needs production records takes a *ProductionReader and cannot be handed
// anything else. The filter is the REGISTRY's envelope for the record's
// kind (stamped into the frame at commit and re-checked at decode).

type ProductionReader struct {
	j *Journal
	// pin is the head a pinned reader reads through, whatever the journal
	// does meanwhile: a fold composed of several scans (runs over
	// invocations over learned items, a tail over runs) must see ONE
	// prefix, or a record can appear in a later scan whose causes the
	// earlier one never saw. Found live at step 9: a diagnosis seen by the
	// tail's scan citing a receipt the run fold had not yet read.
	pin    uint64
	pinned bool
}
type ControlReader struct{ j *Journal }
type ExperimentalReader struct{ j *Journal }

// ErrBeyondPin is a scan past a pinned reader's head: the caller mixed a
// pinned reader with an explicit range, the split-prefix bug by another door.
var ErrBeyondPin = errors.New("journal: scan beyond the reader's pin")

// Pin returns a reader fixed at the current head; pinning a pinned reader
// is the identity, so every fold may pin without re-pinning its callers'.
func (r *ProductionReader) Pin() *ProductionReader {
	if r.pinned {
		return r
	}
	return &ProductionReader{j: r.j, pin: r.j.Head(), pinned: true}
}

func (j *Journal) Production() *ProductionReader     { return &ProductionReader{j: j} }
func (j *Journal) Control() *ControlReader           { return &ControlReader{j} }
func (j *Journal) Experimental() *ExperimentalReader { return &ExperimentalReader{j} }

func scanEnvelope(j *Journal, want record.Envelope, after, through uint64, fn func(uint64, record.Record) error) error {
	return j.scanThrough(after, through, func(e Encoded) error {
		env, ok := record.EnvelopeOf(e.Kind)
		if !ok || env != want {
			return nil // not this population: invisible to this reader by construction
		}
		r, err := Decode(e)
		if err != nil {
			return err
		}
		return fn(e.Epoch, r)
	})
}

func dropEpoch(fn func(record.Record) error) func(uint64, record.Record) error {
	return func(_ uint64, r record.Record) error { return fn(r) }
}

// ScanEpochs is Scan with each record's committing lease epoch: the one
// fact a fold needs to tell a call this process is still making from one
// a dead process left behind.
func (r *ProductionReader) ScanEpochs(after uint64, fn func(epoch uint64, rec record.Record) error) error {
	return scanEnvelope(r.j, record.Production, after, r.Head(), fn)
}

// ScanThrough yields committed records of this population with Seq in
// (after, through], proving coverage through `through` or failing.
func (r *ProductionReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	if r.pinned && through > r.pin {
		return fmt.Errorf("%w: through %d exceeds the reader's pin %d", ErrBeyondPin, through, r.pin)
	}
	return scanEnvelope(r.j, record.Production, after, through, dropEpoch(fn))
}
func (r *ControlReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Control, after, through, dropEpoch(fn))
}
func (r *ExperimentalReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Experimental, after, through, dropEpoch(fn))
}

// Scan is ScanThrough to the head as of the call (the pin, if pinned).
func (r *ProductionReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.Head(), fn)
}
func (r *ControlReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.j.Head(), fn)
}
func (r *ExperimentalReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.j.Head(), fn)
}

func (r *ProductionReader) Head() uint64 {
	if r.pinned {
		return r.pin
	}
	return r.j.Head()
}
func (r *ControlReader) Head() uint64      { return r.j.Head() }
func (r *ExperimentalReader) Head() uint64 { return r.j.Head() }
