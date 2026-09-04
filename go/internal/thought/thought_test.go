package thought

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func newStore(t *testing.T) (*Store, *workspace.Announced) {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, err := workspace.Resolve()
	if err != nil {
		t.Fatal(err)
	}
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Ensure(); err != nil {
		t.Fatal(err)
	}
	s, err := Open(a)
	if err != nil {
		t.Fatal(err)
	}
	return s, a
}

func TestPutGetRoundTrip_WholeBody(t *testing.T) {
	s, _ := newStore(t)
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
	s, _ := newStore(t)
	for _, c := range []struct {
		name string
		body []byte
		enc  Encoding
	}{{"8MiB", bytes.Repeat([]byte("x"), 8<<20), UTF8}, {"empty", []byte{}, UTF8}, {"invalid-utf8", []byte{0xff, 0xfe, 0x00, 0x80}, Bytes}} {
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
	s, _ := newStore(t)
	a, _ := s.Put(Goal, []byte("same bytes"))
	b, _ := s.Put(Response, []byte("same bytes"))
	if a.Hash == b.Hash {
		t.Fatal("identical bytes under different kinds must not share an address")
	}
}

func TestPutIsIdempotent(t *testing.T) {
	s, _ := newStore(t)
	a, _ := s.Put(Prompt, []byte("p"))
	b, _ := s.Put(Prompt, []byte("p"))
	if a != b {
		t.Fatalf("%+v != %+v", a, b)
	}
}

// Put never vouches for bytes it did not check: a corrupt body already at
// the address is refused, not silently "already stored".
func TestPutRefusesExistingCorruptBody(t *testing.T) {
	s, _ := newStore(t)
	body := []byte("original result")
	ref, _ := s.Put(StepResult, body)
	d, _ := digestOf(ref.Hash)
	if err := os.WriteFile(s.pathFor(ref.Kind, d), []byte("original resulT"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Put(StepResult, body); !errors.Is(err, ErrTampered) {
		t.Fatalf("Put over a corrupt body must refuse: %v", err)
	}
	if _, err := s.Get(ref); !errors.Is(err, ErrTampered) {
		t.Fatalf("want ErrTampered, got %v", err)
	}
}

func TestGetRefusesLies(t *testing.T) {
	s, _ := newStore(t)
	ref, _ := s.Put(StepResult, []byte("abc"))
	lie := ref
	lie.Bytes = 2
	if _, err := s.Get(lie); !errors.Is(err, ErrTampered) {
		t.Fatalf("length lie: %v", err)
	}
	lie = ref
	lie.Encoding = Bytes
	if _, err := s.Get(lie); !errors.Is(err, ErrTampered) {
		t.Fatalf("encoding lie: %v", err)
	}
}

// Refs decoded from JSON are untrusted: every malformed shape is ErrRef from
// every public operation, never a panic and never "absent".
func TestMalformedRefsAreRefusedNotConflated(t *testing.T) {
	s, _ := newStore(t)
	good, _ := s.Put(Goal, []byte("g"))
	bad := []string{
		`{"hash":"","kind":"goal","bytes":1,"encoding":"utf8"}`,
		`{"hash":"s256v1:00","kind":"goal","bytes":1,"encoding":"utf8"}`,
		`{"hash":"md5:` + strings.Repeat("a", 64) + `","kind":"goal","bytes":1,"encoding":"utf8"}`,
		`{"hash":"s256v1:` + strings.Repeat("A", 64) + `","kind":"goal","bytes":1,"encoding":"utf8"}`,
		`{"hash":"s256v1:../` + strings.Repeat("a", 61) + `","kind":"goal","bytes":1,"encoding":"utf8"}`,
		`{"hash":"` + good.Hash + `","kind":"chunk","bytes":1,"encoding":"utf8"}`,
		`{"hash":"` + good.Hash + `","kind":"goal","bytes":-1,"encoding":"utf8"}`,
		`{"hash":"` + good.Hash + `","kind":"goal","bytes":1,"encoding":"latin1"}`,
	}
	for _, raw := range bad {
		var ref Ref
		if err := json.Unmarshal([]byte(raw), &ref); err != nil {
			t.Fatal(err)
		}
		if _, err := s.Get(ref); !errors.Is(err, ErrRef) {
			t.Fatalf("Get %s: want ErrRef, got %v", raw, err)
		}
		if _, err := s.Has(ref); !errors.Is(err, ErrRef) {
			t.Fatalf("Has %s: want ErrRef, got %v", raw, err)
		}
	}
}

// A Ref survives a process boundary: marshal, reopen the store on the same
// root, unmarshal, Get.
func TestRefJSONRoundTripAcrossReopen(t *testing.T) {
	s, a := newStore(t)
	body := []byte("cross-process")
	ref, _ := s.Put(Response, body)
	raw, _ := json.Marshal(ref)
	s2, err := Open(a)
	if err != nil {
		t.Fatal(err)
	}
	var back Ref
	if err := json.Unmarshal(raw, &back); err != nil {
		t.Fatal(err)
	}
	ok, err := s2.Has(back)
	if err != nil || !ok {
		t.Fatalf("Has: %v %v", ok, err)
	}
	got, err := s2.Get(back)
	if err != nil || !bytes.Equal(got, body) {
		t.Fatalf("Get: %v", err)
	}
}

func TestAbsentAndUnknownKind(t *testing.T) {
	s, _ := newStore(t)
	ref, _ := s.Put(Goal, []byte("g"))
	d, _ := digestOf(ref.Hash)
	os.Remove(s.pathFor(ref.Kind, d))
	if _, err := s.Get(ref); !errors.Is(err, ErrAbsent) {
		t.Fatalf("want ErrAbsent, got %v", err)
	}
	if ok, err := s.Has(ref); ok || err != nil {
		t.Fatalf("Has absent: %v %v", ok, err)
	}
	if _, err := s.Put(Kind("chunk"), []byte("x")); !errors.Is(err, ErrKind) {
		t.Fatalf("chunk is not a kind in v1 (no engine chunking): got %v", err)
	}
}
