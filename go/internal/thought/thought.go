// Package thought is the content-addressed, immutable store for the second
// population (design note §1b). Process code stores, hashes, routes, and hands
// thoughts on; it never parses or slices one. Construction is private to the
// store: bytes in → ThoughtRef out, hash computed and verified at this
// boundary, re-verified on read.
package thought

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"unicode/utf8"
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
// rather than a third-party blake3: the swipe-over-deps rule, and nothing
// here needs blake3's speed.
const HashAlgo = "s256v1"

// Ref is what every other record carries. Body bytes never travel in a Ref.
type Ref struct {
	Hash     string   `json:"hash"` // "s256v1:<hex>"
	Kind     Kind     `json:"kind"`
	Bytes    int64    `json:"bytes"`    // derived at store time
	Encoding Encoding `json:"encoding"` // derived at store time
}

var (
	ErrKind     = errors.New("thought: unknown kind")
	ErrTampered = errors.New("thought: stored body does not match its hash")
	ErrAbsent   = errors.New("thought: no body at that address")
	ErrRef      = errors.New("thought: malformed ref")
)

func hashOf(k Kind, body []byte) string {
	h := sha256.New()
	h.Write([]byte("maro-thought/v1\x00"))
	h.Write([]byte(k))
	h.Write([]byte{0})
	h.Write(body)
	return HashAlgo + ":" + hex.EncodeToString(h.Sum(nil))
}

// Store is a directory of content-addressed bodies: <dir>/<kind>/<hex>.
type Store struct{ dir string }

// Open prepares a store under dir. The caller has already announced the
// workspace root (workspace.Root) — the store never resolves paths itself.
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &Store{dir: dir}, nil
}

func (s *Store) pathFor(k Kind, hash string) string {
	return filepath.Join(s.dir, string(k), hash[len(HashAlgo)+1:])
}

// Put stores a body whole and returns its Ref. Idempotent: the same bytes
// under the same kind land at the same address. Any size, any bytes; the
// contract declares thought bodies unconstrained.
func (s *Store) Put(k Kind, body []byte) (Ref, error) {
	if !kinds[k] {
		return Ref{}, fmt.Errorf("%w: %q", ErrKind, k)
	}
	h := hashOf(k, body)
	enc := Bytes
	if utf8.Valid(body) {
		enc = UTF8
	}
	ref := Ref{Hash: h, Kind: k, Bytes: int64(len(body)), Encoding: enc}
	p := s.pathFor(k, h)
	if _, err := os.Stat(p); err == nil {
		return ref, nil // already present; content-addressed, so identical
	}
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		return Ref{}, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(p), ".put-*")
	if err != nil {
		return Ref{}, err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(body); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return Ref{}, err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return Ref{}, err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return Ref{}, err
	}
	if err := os.Rename(tmpName, p); err != nil {
		os.Remove(tmpName)
		return Ref{}, err
	}
	return ref, nil
}

// Get returns the whole body for a Ref, re-verifying hash, length, and
// encoding. A mismatch is ErrTampered; there is no partial read.
func (s *Store) Get(ref Ref) ([]byte, error) {
	if !kinds[ref.Kind] || len(ref.Hash) <= len(HashAlgo)+1 || ref.Hash[:len(HashAlgo)+1] != HashAlgo+":" {
		return nil, ErrRef
	}
	body, err := os.ReadFile(s.pathFor(ref.Kind, ref.Hash))
	if errors.Is(err, os.ErrNotExist) {
		return nil, ErrAbsent
	}
	if err != nil {
		return nil, err
	}
	if hashOf(ref.Kind, body) != ref.Hash || int64(len(body)) != ref.Bytes {
		return nil, ErrTampered
	}
	enc := Bytes
	if utf8.Valid(body) {
		enc = UTF8
	}
	if enc != ref.Encoding {
		return nil, ErrTampered
	}
	return body, nil
}

// Has reports whether a body is present without reading it.
func (s *Store) Has(ref Ref) bool {
	_, err := os.Stat(s.pathFor(ref.Kind, ref.Hash))
	return err == nil
}
