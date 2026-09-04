package journal

import (
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The three reader capabilities. Each is a distinct type; a function that
// needs production records takes a *ProductionReader and cannot be handed
// anything else. The filter is the REGISTRY's envelope for the record's
// kind (stamped into the frame at commit and re-checked at decode).

type ProductionReader struct{ j *Journal }
type ControlReader struct{ j *Journal }
type ExperimentalReader struct{ j *Journal }

func (j *Journal) Production() *ProductionReader     { return &ProductionReader{j} }
func (j *Journal) Control() *ControlReader           { return &ControlReader{j} }
func (j *Journal) Experimental() *ExperimentalReader { return &ExperimentalReader{j} }

func scanEnvelope(j *Journal, want record.Envelope, after, through uint64, fn func(record.Record) error) error {
	return j.scanThrough(after, through, func(e Encoded) error {
		env, ok := record.EnvelopeOf(e.Kind)
		if !ok || env != want {
			return nil // not this population: invisible to this reader by construction
		}
		r, err := Decode(e)
		if err != nil {
			return err
		}
		return fn(r)
	})
}

// ScanThrough yields committed records of this population with Seq in
// (after, through], proving coverage through `through` or failing.
func (r *ProductionReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Production, after, through, fn)
}
func (r *ControlReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Control, after, through, fn)
}
func (r *ExperimentalReader) ScanThrough(after, through uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Experimental, after, through, fn)
}

// Scan is ScanThrough to the head as of the call.
func (r *ProductionReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.j.Head(), fn)
}
func (r *ControlReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.j.Head(), fn)
}
func (r *ExperimentalReader) Scan(after uint64, fn func(record.Record) error) error {
	return r.ScanThrough(after, r.j.Head(), fn)
}

func (r *ProductionReader) Head() uint64   { return r.j.Head() }
func (r *ControlReader) Head() uint64      { return r.j.Head() }
func (r *ExperimentalReader) Head() uint64 { return r.j.Head() }
