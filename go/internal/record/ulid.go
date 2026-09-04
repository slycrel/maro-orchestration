// Package record defines the immutable, versioned, ordered record model every
// durable artifact in the successor is built on (design note §1a), the three
// disjoint envelopes (§1a, §9), and the kind registry that maps every record
// kind to exactly one envelope and doubles as the record census (§14).
package record

import (
	"crypto/rand"
	"errors"
	"sync"
	"time"
)

// RecordID is a ULID: 48 bits of millisecond time + 80 bits of randomness,
// Crockford base32, 26 chars, lexically time-ordered. Written from the spec
// (no dependency): the successor needs time-ordered unique IDs and nothing
// else a library would add.
type RecordID string

const crockford = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

var (
	ulidMu   sync.Mutex
	ulidLast int64
	ulidRand [10]byte
)

// NewID allocates a ULID. Within one millisecond IDs stay monotonic by
// incrementing the random tail, so allocation order is preserved even when
// the clock does not advance.
func NewID() RecordID { return newIDAt(time.Now()) }

func newIDAt(now time.Time) RecordID {
	ulidMu.Lock()
	defer ulidMu.Unlock()
	ms := now.UnixMilli()
	if ms <= ulidLast {
		ms = ulidLast
		for i := 9; i >= 0; i-- {
			ulidRand[i]++
			if ulidRand[i] != 0 {
				break
			}
		}
	} else {
		if _, err := rand.Read(ulidRand[:]); err != nil {
			panic("record: crypto/rand unavailable: " + err.Error())
		}
	}
	ulidLast = ms
	var b [16]byte
	for i := 5; i >= 0; i-- {
		b[i] = byte(ms)
		ms >>= 8
	}
	copy(b[6:], ulidRand[:])
	return RecordID(encodeULID(b))
}

func encodeULID(b [16]byte) string {
	// 128 bits → 26 base32 chars (the first char carries 3 bits).
	var out [26]byte
	var acc uint64
	var bits uint
	pos := 25
	for i := 15; i >= 0; i-- {
		acc |= uint64(b[i]) << bits
		bits += 8
		for bits >= 5 && pos >= 0 {
			out[pos] = crockford[acc&31]
			acc >>= 5
			bits -= 5
			pos--
		}
	}
	for pos >= 0 {
		out[pos] = crockford[acc&31]
		acc >>= 5
		pos--
	}
	return string(out[:])
}

// ErrBadID is returned by ValidateID for anything that is not a 26-char
// Crockford base32 string with a legal leading character.
var ErrBadID = errors.New("record: malformed RecordID")

// ValidateID checks shape only; it never consults the clock.
func ValidateID(id RecordID) error {
	if len(id) != 26 {
		return ErrBadID
	}
	if id[0] > '7' {
		return ErrBadID
	}
	for i := 0; i < len(id); i++ {
		c := id[i]
		ok := false
		for j := 0; j < len(crockford); j++ {
			if crockford[j] == c {
				ok = true
				break
			}
		}
		if !ok {
			return ErrBadID
		}
	}
	return nil
}
