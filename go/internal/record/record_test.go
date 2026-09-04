package record

import (
	"errors"
	"reflect"
	"sort"
	"testing"
	"time"
)

func TestULIDShapeAndOrder(t *testing.T) {
	a := NewID()
	b := NewID()
	if err := ValidateID(a); err != nil {
		t.Fatal(err)
	}
	if !(a < b) {
		t.Fatalf("ids must be monotonic in allocation order: %s !< %s", a, b)
	}
	// Same millisecond: still strictly increasing.
	now := time.UnixMilli(1_800_000_000_000)
	x, _ := newIDAt(now)
	y, _ := newIDAt(now)
	if !(x < y) {
		t.Fatalf("same-ms ids must increase: %s %s", x, y)
	}
	if _, err := newIDAt(time.UnixMilli(-1)); !errors.Is(err, ErrTimeRange) {
		t.Fatalf("pre-epoch accepted: %v", err)
	}
	if _, err := newIDAt(time.UnixMilli(maxULIDms + 1)); !errors.Is(err, ErrTimeRange) {
		t.Fatalf("48-bit overflow accepted: %v", err)
	}
	if z, err := newIDAt(time.UnixMilli(maxULIDms)); err != nil || ValidateID(z) != nil {
		t.Fatalf("max timestamp: %v %v", z, err)
	}
	if err := ValidateID("not-an-id"); !errors.Is(err, ErrBadID) {
		t.Fatal("malformed id accepted")
	}
	if err := ValidateID(RecordID("8" + string(a[1:]))); !errors.Is(err, ErrBadID) {
		t.Fatal("overflowing leading char accepted")
	}
}

func TestSchemaVerParse(t *testing.T) {
	k, n, err := SchemaVer("outcome/2").Parse()
	if err != nil || k != "outcome" || n != 2 {
		t.Fatalf("%v %v %v", k, n, err)
	}
	for _, bad := range []SchemaVer{"outcome", "outcome/", "/2", "outcome/0", "outcome/x", "outcome/01", "outcome/+1", "Outcome/1", "out-come/1"} {
		if _, _, err := bad.Parse(); err == nil {
			t.Fatalf("%q parsed", bad)
		}
	}
}

// Every registered kind: exactly one envelope marker, marker agrees with the
// registry, census row complete. This is the compile-time capability matrix's
// runtime twin — it fails the suite if a kind is mis-enveloped.
func TestRegistryIsTheAuthority(t *testing.T) {
	specs := All()
	if len(specs) < 2 {
		t.Fatalf("step-1 kinds missing: %d", len(specs))
	}
	for _, s := range specs {
		if countMarkers(s.Type) != 1 {
			t.Fatalf("%s embeds %d markers", s.Kind, countMarkers(s.Type))
		}
		v := reflect.New(s.Type).Elem().Interface()
		if MarkerOf(v) != s.Envelope {
			t.Fatalf("%s marker %s != registry %s", s.Kind, MarkerOf(v), s.Envelope)
		}
		if e, ok := EnvelopeOf(s.Kind); !ok || e != s.Envelope {
			t.Fatalf("EnvelopeOf(%s)", s.Kind)
		}
		if s.Retention == AuditOnly && s.Reader == "" {
			t.Fatalf("%s audit-only without a read surface", s.Kind)
		}
	}
	// sorted by kind
	ks := make([]string, len(specs))
	for i, s := range specs {
		ks[i] = string(s.Kind)
	}
	if !sort.StringsAreSorted(ks) {
		t.Fatal("All() not sorted")
	}
}

type bogusTwoMarkers struct {
	ProductionRecord
	ControlRecord
	Header
}

func (b *bogusTwoMarkers) Head() *Header { return &b.Header }
func (b *bogusTwoMarkers) Kind() Kind    { return "bogus" }

type bogusNoMarker struct{ Header }

func (b *bogusNoMarker) Head() *Header { return &b.Header }
func (b *bogusNoMarker) Kind() Kind    { return "bogus2" }

func mustPanic(t *testing.T, name string, f func()) {
	t.Helper()
	defer func() {
		if recover() == nil {
			t.Fatalf("%s: expected panic", name)
		}
	}()
	f()
}

func TestRegisterRefusesDefects(t *testing.T) {
	// Work on a scratch registry so the real one is untouched.
	saveK, saveT := regByK, regByTy
	resetForTest()
	defer func() { regByK, regByTy = saveK, saveT }()
	row := Spec{Kind: "x", Envelope: Production, Version: 1, Writer: "w", Reader: "r", Decision: "d", Retention: Forever}
	mustPanic(t, "two markers", func() { s := row; s.Type = reflect.TypeOf(bogusTwoMarkers{}); Register(s) })
	mustPanic(t, "no marker", func() { s := row; s.Type = reflect.TypeOf(bogusNoMarker{}); Register(s) })
	mustPanic(t, "marker/envelope disagree", func() {
		s := row
		s.Type = reflect.TypeOf(ThoughtStored{})
		s.Envelope = Control
		Register(s)
	})
	mustPanic(t, "census incomplete", func() {
		s := row
		s.Type = reflect.TypeOf(ThoughtStored{})
		s.Decision = ""
		Register(s)
	})
	ok := row
	ok.Type = reflect.TypeOf(ThoughtStored{})
	Register(ok)
	mustPanic(t, "duplicate kind", func() { s := ok; s.Type = reflect.TypeOf(LeaseRecord{}); s.Envelope = Control; Register(s) })
	mustPanic(t, "duplicate type", func() { s := ok; s.Kind = "y"; Register(s) })
}

func TestValidateHeader(t *testing.T) {
	r := &ThoughtStored{Header: Header{ID: NewID(), Schema: "thought_stored/1", Seq: 1, Subject: Ref{Kind: "thought", ID: "s256v1:00"}, At: time.Now()}}
	if err := Validate(r, true); err != nil {
		t.Fatal(err)
	}
	r.Schema = "thought_stored/9"
	if err := Validate(r, true); !errors.Is(err, ErrSchemaMismatch) {
		t.Fatalf("future version accepted: %v", err)
	}
	r.Schema = "lease/1"
	if err := Validate(r, true); !errors.Is(err, ErrSchemaMismatch) {
		t.Fatalf("wrong kind accepted: %v", err)
	}
	r.Schema = "thought_stored/1"
	r.Seq = 0
	if err := Validate(r, true); err == nil {
		t.Fatal("stored record with Seq 0 accepted")
	}
	if err := Validate(r, false); err != nil {
		t.Fatalf("pre-sequencer record must validate without Seq: %v", err)
	}
	r.Supersedes = "junk"
	if err := Validate(r, false); !errors.Is(err, ErrBadID) {
		t.Fatalf("bad Supersedes accepted: %v", err)
	}
	type unreg struct {
		ProductionRecord
		Header
	}
	u := &struct {
		unreg
	}{}
	_ = u
	bogus := &bogusNoMarker{Header: r.Header}
	if err := Validate(bogus, false); !errors.Is(err, ErrUnregisteredKind) {
		t.Fatalf("unregistered kind accepted: %v", err)
	}
	// An impostor: a registered type whose Kind() claims another kind.
	imp := &impostor{ThoughtStored: ThoughtStored{Header: r.Header}}
	if err := Validate(imp, false); !errors.Is(err, ErrUnregisteredKind) {
		t.Fatalf("a wrapper type is not the registered type: %v", err)
	}
	// Kind derived from the concrete type, pointer or value.
	if k, ok := KindOf(&ThoughtStored{}); !ok || k != KindThoughtStored {
		t.Fatalf("KindOf pointer: %v %v", k, ok)
	}
	if k, ok := KindOf(ThoughtStored{}); !ok || k != KindThoughtStored {
		t.Fatalf("KindOf value: %v %v", k, ok)
	}
	// Subject is required.
	r2 := *r
	r2.Supersedes = ""
	r2.Subject = Ref{}
	if err := Validate(&r2, false); !errors.Is(err, ErrSubject) {
		t.Fatalf("empty subject accepted: %v", err)
	}
}

// impostor wraps a registered type; it is NOT the registered type.
type impostor struct{ ThoughtStored }

func (i *impostor) Kind() Kind { return KindLease }
