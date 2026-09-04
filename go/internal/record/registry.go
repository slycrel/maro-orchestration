package record

import (
	"fmt"
	"reflect"
	"sort"
	"sync"
)

// Retention says what happens to a kind's rows over time. A kind with no
// consumer is not written (design note §14): AuditOnly must name the operator
// read surface; everything else names the decision its reader makes.
type Retention string

const (
	Forever   Retention = "forever"    // part of the record; compaction preserves semantics
	Bounded   Retention = "bounded"    // a bounded projection; older rows may be folded away
	AuditOnly Retention = "audit-only" // no decision consumer; read surface named in Consumer
)

// Spec is one registry row. It is also the record census (§14): writer,
// authoritative reader/query, the decision it affects, and retention.
type Spec struct {
	Kind      Kind
	Envelope  Envelope
	Version   int          // current schema version; readers must accept 1..Version per declared absence semantics
	Type      reflect.Type // the Go type; the generated contract is derived from it
	Writer    string       // which component commits it
	Reader    string       // the authoritative reader / query
	Decision  string       // the decision the reader makes with it (or the operator surface for audit-only)
	Retention Retention
}

var (
	regMu   sync.RWMutex
	regByK  = map[Kind]Spec{}
	regByTy = map[reflect.Type]Kind{}
)

// Register adds a kind. It panics on a duplicate kind, a duplicate type, a
// type whose embedded marker disagrees with the declared envelope, or an
// incomplete census row — registration happens at init, and a wrong row is a
// build defect, not a runtime condition.
func Register(s Spec) {
	regMu.Lock()
	defer regMu.Unlock()
	if s.Kind == "" || s.Type == nil || s.Version < 1 {
		panic(fmt.Sprintf("record: incomplete Spec for %q", s.Kind))
	}
	if s.Envelope == envelopeUnset {
		panic(fmt.Sprintf("record: kind %q has no envelope", s.Kind))
	}
	if s.Writer == "" || s.Reader == "" || s.Decision == "" || s.Retention == "" {
		panic(fmt.Sprintf("record: kind %q census row incomplete (writer/reader/decision/retention are required; audit-only must name its read surface)", s.Kind))
	}
	if _, dup := regByK[s.Kind]; dup {
		panic(fmt.Sprintf("record: kind %q registered twice", s.Kind))
	}
	if k, dup := regByTy[s.Type]; dup {
		panic(fmt.Sprintf("record: type %v already registered as %q", s.Type, k))
	}
	v := reflect.New(s.Type).Elem().Interface()
	if m := MarkerOf(v); m != s.Envelope {
		panic(fmt.Sprintf("record: kind %q declared %s but its type's marker says %s", s.Kind, s.Envelope, m))
	}
	if n := countMarkers(s.Type); n != 1 {
		panic(fmt.Sprintf("record: kind %q embeds %d envelope markers; exactly one is required", s.Kind, n))
	}
	regByK[s.Kind] = s
	regByTy[s.Type] = s.Kind
}

func countMarkers(t reflect.Type) int {
	n := 0
	for i := 0; i < t.NumField(); i++ {
		f := t.Field(i)
		if !f.Anonymous {
			continue
		}
		switch f.Type {
		case reflect.TypeOf(ProductionRecord{}), reflect.TypeOf(ControlRecord{}), reflect.TypeOf(ExperimentalRecord{}):
			n++
		}
	}
	return n
}

// Lookup returns the spec for a kind.
func Lookup(k Kind) (Spec, bool) {
	regMu.RLock()
	defer regMu.RUnlock()
	s, ok := regByK[k]
	return s, ok
}

// KindOf returns the registered kind of a record value's type.
func KindOf(r any) (Kind, bool) {
	regMu.RLock()
	defer regMu.RUnlock()
	t := reflect.TypeOf(r)
	for t != nil && t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	k, ok := regByTy[t]
	return k, ok
}

// EnvelopeOf is the authority readers use: the registry's envelope for the
// kind, never the value's marker.
func EnvelopeOf(k Kind) (Envelope, bool) {
	s, ok := Lookup(k)
	if !ok {
		return envelopeUnset, false
	}
	return s.Envelope, true
}

// All returns every registered spec, sorted by kind — the census in a stable
// order for the contracts generator.
func All() []Spec {
	regMu.RLock()
	defer regMu.RUnlock()
	out := make([]Spec, 0, len(regByK))
	for _, s := range regByK {
		out = append(out, s)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Kind < out[j].Kind })
	return out
}

// resetForTest clears the registry; only tests call it.
func resetForTest() {
	regMu.Lock()
	defer regMu.Unlock()
	regByK = map[Kind]Spec{}
	regByTy = map[reflect.Type]Kind{}
}
