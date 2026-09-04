// Package thought is the content-addressed, immutable store for the second
// population (design note §1b). Process code stores, hashes, routes, and hands
// thoughts on; it never parses or slices one. Construction is private to the
// store: bytes in → Ref out, hash computed and verified at this boundary,
// re-verified on read.
package thought

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// Kind names what a thought is to the process (goal, prompt, response, ...).
// It is part of the hash domain so identical bytes as a goal and as a
// response do not collide into one address.
type Kind string

const (
	Goal        Kind = "goal"
	Prompt      Kind = "prompt"
	Response    Kind = "response"
	StepResult  Kind = "step_result"
	Deliverable Kind = "deliverable"
	LessonText  Kind = "lesson_text"
)

var kinds = map[Kind]bool{Goal: true, Prompt: true, Response: true, StepResult: true, Deliverable: true, LessonText: true}

// Encoding says what a text backend may be handed (utf8) versus what only a
// file transport may carry (bytes). Derived at store time, never declared by
// a caller.
type Encoding string

const (
	UTF8  Encoding = "utf8"
	Bytes Encoding = "bytes"
)

// HashAlgo is the versioned, domain-separated hash. "s256v1" = SHA-256 over
// "maro-thought/v1\x00<kind>\x00<body>". SHA-256 from the standard library
// rather than a third-party blake3: the swipe-over-deps rule.
const HashAlgo = "s256v1"

const digestHex = 64

// Ref is what every other record carries. Body bytes never travel in a Ref.
// A Ref decoded from JSON is untrusted until Validate passes; every public
// store operation validates first.
type Ref struct {
	Hash     string   `json:"hash"` // "s256v1:<64 lowercase hex>"
	Kind     Kind     `json:"kind"`
	Bytes    int64    `json:"bytes"`    // derived at store time
	Encoding Encoding `json:"encoding"` // derived at store time
}

var (
	ErrKind     = errors.New("thought: unknown kind")
	ErrTampered = errors.New("thought: stored body does not match its ref")
	ErrAbsent   = errors.New("thought: no body at that address")
	ErrRef      = errors.New("thought: malformed ref")
)

// Validate checks a Ref's shape strictly: known kind, exact algorithm prefix,
// 64 lowercase hex digits, non-negative length, closed encoding vocabulary.
func (r Ref) Validate() error {
	if !kinds[r.Kind] {
		return fmt.Errorf("%w: kind %q", ErrRef, r.Kind)
	}
	d, ok := digestOf(r.Hash)
	if !ok {
		return fmt.Errorf("%w: hash %q", ErrRef, r.Hash)
	}
	_ = d
	if r.Bytes < 0 {
		return fmt.Errorf("%w: negative bytes", ErrRef)
	}
	if r.Encoding != UTF8 && r.Encoding != Bytes {
		return fmt.Errorf("%w: encoding %q", ErrRef, r.Encoding)
	}
	return nil
}

// digestOf returns the hex digest if hash is exactly "<algo>:<64 lowercase hex>".
func digestOf(hash string) (string, bool) {
	pre := HashAlgo + ":"
	if len(hash) != len(pre)+digestHex || hash[:len(pre)] != pre {
		return "", false
	}
	d := hash[len(pre):]
	for i := 0; i < len(d); i++ {
		c := d[i]
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return "", false
		}
	}
	return d, true
}

func hashOf(k Kind, body []byte) string {
	h := sha256.New()
	h.Write([]byte("maro-thought/v1\x00"))
	h.Write([]byte(k))
	h.Write([]byte{0})
	h.Write(body)
	return HashAlgo + ":" + hex.EncodeToString(h.Sum(nil))
}

func encodingOf(body []byte) Encoding {
	if utf8.Valid(body) {
		return UTF8
	}
	return Bytes
}

// Store is a directory of content-addressed bodies: <root>/thoughts/<kind>/<hex>.
type Store struct{ dir string }

// Open prepares the store under an ANNOUNCED workspace root. There is no way
// to open a store on an arbitrary path: the announce-before-write guarantee
// composes into every writer by type.
func Open(root *workspace.Announced) (*Store, error) {
	dir := root.Path("thoughts")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &Store{dir: dir}, nil
}

func (s *Store) pathFor(k Kind, digest string) string {
	return filepath.Join(s.dir, string(k), digest)
}

// Put stores a body whole and returns its Ref. Idempotent: the same bytes
// under the same kind land at the same address. If a body is already present
// at the address it is READ AND VERIFIED before Put reports success — a Put
// never vouches for bytes it did not check. Any size, any bytes.
func (s *Store) Put(k Kind, body []byte) (Ref, error) {
	if !kinds[k] {
		return Ref{}, fmt.Errorf("%w: %q", ErrKind, k)
	}
	h := hashOf(k, body)
	d, _ := digestOf(h)
	ref := Ref{Hash: h, Kind: k, Bytes: int64(len(body)), Encoding: encodingOf(body)}
	p := s.pathFor(k, d)
	if existing, err := os.ReadFile(p); err == nil {
		if !bytes.Equal(existing, body) {
			return Ref{}, fmt.Errorf("%w: existing body at %s differs from the bytes being stored", ErrTampered, p)
		}
		return ref, nil
	} else if !errors.Is(err, os.ErrNotExist) {
		return Ref{}, err
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return Ref{}, err
	}
	if err := workspace.WriteFileDurable(p, body, 0o644); err != nil {
		return Ref{}, err
	}
	// A concurrent identical Put may have won the rename; the winner's bytes
	// must be ours by content-addressing — verify rather than assume.
	got, err := os.ReadFile(p)
	if err != nil {
		return Ref{}, err
	}
	if !bytes.Equal(got, body) {
		return Ref{}, fmt.Errorf("%w: post-write verification failed at %s", ErrTampered, p)
	}
	return ref, nil
}

// Get returns the whole body for a Ref, re-verifying hash, length, and
// encoding. A mismatch is ErrTampered; there is no partial read.
func (s *Store) Get(ref Ref) ([]byte, error) {
	if err := ref.Validate(); err != nil {
		return nil, err
	}
	d, _ := digestOf(ref.Hash)
	body, err := os.ReadFile(s.pathFor(ref.Kind, d))
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrAbsent
	}
	if err != nil {
		return nil, err
	}
	if hashOf(ref.Kind, body) != ref.Hash || int64(len(body)) != ref.Bytes || encodingOf(body) != ref.Encoding {
		return nil, ErrTampered
	}
	return body, nil
}

// Has reports presence without reading the body. A malformed Ref is an
// error, never "absent": malformed and absent must not be conflated.
func (s *Store) Has(ref Ref) (bool, error) {
	if err := ref.Validate(); err != nil {
		return false, err
	}
	d, _ := digestOf(ref.Hash)
	_, err := os.Stat(s.pathFor(ref.Kind, d))
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}
