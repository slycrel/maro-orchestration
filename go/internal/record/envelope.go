package record

// Envelope is the population a record belongs to (design note §1a, §9).
// The three are disjoint; a reader capability is typed to exactly one, so a
// production query cannot return control or experimental rows by
// construction. The mapping kind → envelope lives in the registry and is the
// single authority for encoding, decoding, contracts, and the capability
// matrix.
type Envelope uint8

const (
	envelopeUnset Envelope = iota
	Production             // everything a production run writes and reads
	Control                // experiment protocol, assignment, stopping state
	Experimental           // shadow-arm runs and their artifacts
)

func (e Envelope) String() string {
	switch e {
	case Production:
		return "production"
	case Control:
		return "control"
	case Experimental:
		return "experimental"
	}
	return "unset"
}

// ProductionRecord, ControlRecord and ExperimentalRecord are the typed
// envelopes. A record type embeds exactly one marker; the registry test
// (registry_test.go) fails the build's test run if a registered type embeds
// none or more than one, or if its marker disagrees with its registered
// envelope.
type (
	ProductionRecord   struct{}
	ControlRecord      struct{}
	ExperimentalRecord struct{}
)

func (ProductionRecord) envelopeMarker() Envelope   { return Production }
func (ControlRecord) envelopeMarker() Envelope      { return Control }
func (ExperimentalRecord) envelopeMarker() Envelope { return Experimental }

type enveloped interface{ envelopeMarker() Envelope }

// MarkerOf reports the envelope a value's embedded marker claims, or unset.
// Used only by the registry check; readers trust the registry, never the
// marker alone.
func MarkerOf(r any) Envelope {
	if e, ok := r.(enveloped); ok {
		return e.envelopeMarker()
	}
	return envelopeUnset
}
