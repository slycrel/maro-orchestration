package journal

import (
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The three reader capabilities. Each is a distinct type; a function that
// needs production records takes a *ProductionReader and cannot be handed
// anything else. The filter is the REGISTRY's envelope for the record's
// kind (stamped into the frame at commit and re-checked at decode), never a
// field on the record.

type ProductionReader struct{ j *Journal }
type ControlReader struct{ j *Journal }
type ExperimentalReader struct{ j *Journal }

func (j *Journal) Production() *ProductionReader     { return &ProductionReader{j} }
func (j *Journal) Control() *ControlReader           { return &ControlReader{j} }
func (j *Journal) Experimental() *ExperimentalReader { return &ExperimentalReader{j} }

func scanEnvelope(j *Journal, want record.Envelope, after uint64, fn func(record.Record) error) error {
	return j.scan(after, func(e Encoded) error {
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

// Scan yields committed production records with Seq > after, in Seq order.
func (r *ProductionReader) Scan(after uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Production, after, fn)
}

// Scan yields committed control records with Seq > after, in Seq order.
func (r *ControlReader) Scan(after uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Control, after, fn)
}

// Scan yields committed experimental records with Seq > after, in Seq order.
func (r *ExperimentalReader) Scan(after uint64, fn func(record.Record) error) error {
	return scanEnvelope(r.j, record.Experimental, after, fn)
}

// Head is the committed watermark, the same for every population.
func (r *ProductionReader) Head() uint64   { return r.j.Head() }
func (r *ControlReader) Head() uint64      { return r.j.Head() }
func (r *ExperimentalReader) Head() uint64 { return r.j.Head() }
