package thought

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func newStore(t *testing.T) *Store {
	t.Helper()
	s, err := Open(filepath.Join(t.TempDir(), "thoughts"))
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestPutGetRoundTrip_WholeBody(t *testing.T) {
	s := newStore(t)
	body := []byte("where can I get non-ethanol gas near Manti, Utah?")
	ref, err := s.Put(Goal, body)
	if err != nil {
		t.Fatal(err)
	}
	if ref.Bytes != int64(len(body)) || ref.Encoding != UTF8 || ref.Kind != Goal {
		t.Fatalf("ref derived wrong: %+v", ref)
	}
	got, err := s.Get(ref)
	if err != nil || !bytes.Equal(got, body) {
		t.Fatalf("round trip: %v %q", err, got)
	}
}

// D16: thought bodies are unconstrained. Oversized, empty, and non-UTF-8
// bodies all flow whole through the store boundary.
func TestUnconstrainedBodies(t *testing.T) {
	s := newStore(t)
	big := bytes.Repeat([]byte("x"), 8<<20) // 8 MiB
	empty := []byte{}
	raw := []byte{0xff, 0xfe, 0x00, 0x80}
	for _, c := range []struct {
		name string
		body []byte
		enc  Encoding
	}{{"8MiB", big, UTF8}, {"empty", empty, UTF8}, {"invalid-utf8", raw, Bytes}} {
		ref, err := s.Put(Deliverable, c.body)
		if err != nil {
			t.Fatalf("%s: put: %v", c.name, err)
		}
		if ref.Encoding != c.enc || ref.Bytes != int64(len(c.body)) {
			t.Fatalf("%s: derived %+v", c.name, ref)
		}
		got, err := s.Get(ref)
		if err != nil || !bytes.Equal(got, c.body) {
			t.Fatalf("%s: get: %v len=%d", c.name, err, len(got))
		}
	}
}

func TestKindIsPartOfTheAddress(t *testing.T) {
	s := newStore(t)
	a, _ := s.Put(Goal, []byte("same bytes"))
	b, _ := s.Put(Response, []byte("same bytes"))
	if a.Hash == b.Hash {
		t.Fatal("identical bytes under different kinds must not share an address")
	}
}

func TestPutIsIdempotent(t *testing.T) {
	s := newStore(t)
	a, _ := s.Put(Prompt, []byte("p"))
	b, _ := s.Put(Prompt, []byte("p"))
	if a != b {
		t.Fatalf("%+v != %+v", a, b)
	}
}

// The store re-verifies on read: a body altered on disk is refused whole,
// never served partially or "fixed".
func TestTamperIsRefused(t *testing.T) {
	s := newStore(t)
	ref, _ := s.Put(StepResult, []byte("original result"))
	p := s.pathFor(ref.Kind, ref.Hash)
	if err := os.WriteFile(p, []byte("original resulT"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Get(ref); !errors.Is(err, ErrTampered) {
		t.Fatalf("want ErrTampered, got %v", err)
	}
	// A ref that lies about its length is also tampering.
	ref2, _ := s.Put(StepResult, []byte("abc"))
	ref2.Bytes = 2
	if _, err := s.Get(ref2); !errors.Is(err, ErrTampered) {
		t.Fatalf("want ErrTampered on length lie, got %v", err)
	}
}

func TestAbsentAndMalformed(t *testing.T) {
	s := newStore(t)
	ref, _ := s.Put(Goal, []byte("g"))
	os.Remove(s.pathFor(ref.Kind, ref.Hash))
	if _, err := s.Get(ref); !errors.Is(err, ErrAbsent) {
		t.Fatalf("want ErrAbsent, got %v", err)
	}
	if _, err := s.Get(Ref{Hash: "md5:00", Kind: Goal}); !errors.Is(err, ErrRef) {
		t.Fatalf("want ErrRef, got %v", err)
	}
	if _, err := s.Put(Kind("chunk"), []byte("x")); !errors.Is(err, ErrKind) {
		t.Fatalf("chunk is not a kind in v1 (no engine chunking): got %v", err)
	}
}
