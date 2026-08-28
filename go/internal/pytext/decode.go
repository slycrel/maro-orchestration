package pytext

import (
	"strings"
	"unicode/utf8"
)

// DecodeReplace is `bytes.decode("utf-8", errors="replace")`.
//
// Go's []byte->string conversion keeps ill-formed bytes VERBATIM; CPython
// substitutes U+FFFD. Anywhere a port reads a file the way Python reads it
// and then compares, hashes, counts or slices the result, the two runtimes
// answer differently on a torn or binary file — and "it will fail to parse
// on both sides anyway" has already been wrong once (metrics: the row was
// skipped, but its LENGTH still moved a `line[:60]` window and ended a
// day's scan early).
//
// THE COUNT OF REPLACEMENTS MATTERS, which is why this is not
// strings.ToValidUTF8: that collapses a RUN of bad bytes into one U+FFFD,
// and CPython emits one per MAXIMAL SUBPART. b"\xff\xfe" is two characters
// to Python and one to ToValidUTF8.
//
// This is the canonical home. internal/metrics and internal/artifactcheck
// each still carry their own copy from before there was one; migrating
// them is BACKLOG'd rather than done here, because both are covered by
// batteries that would have to move with them.
func DecodeReplace(b []byte) string {
	// A fast path, and nothing else: the loop below returns the same
	// string for valid input.
	if utf8.Valid(b) {
		return string(b)
	}
	var sb strings.Builder
	sb.Grow(len(b))
	for i := 0; i < len(b); {
		r, size := utf8.DecodeRune(b[i:])
		if r == utf8.RuneError && size == 1 {
			sb.WriteRune(utf8.RuneError)
			i += maximalSubpart(b[i:])
			continue
		}
		sb.Write(b[i : i+size])
		i += size
	}
	return sb.String()
}

// maximalSubpart returns the length of the ill-formed prefix of b that
// CPython replaces with a SINGLE U+FFFD: the longest initial subsequence
// that could still have become a well-formed sequence, or 1 when the first
// byte cannot start one at all. Unicode 15.0 section 3.9, "U+FFFD
// Substitution of Maximal Subparts", which is the rule CPython's decoder
// implements. Always at least 1, so DecodeReplace cannot loop.
func maximalSubpart(b []byte) int {
	if len(b) == 0 {
		return 1
	}
	var want int
	var lo, hi byte
	switch b0 := b[0]; {
	case b0 >= 0xC2 && b0 <= 0xDF:
		want, lo, hi = 2, 0x80, 0xBF
	case b0 == 0xE0:
		want, lo, hi = 3, 0xA0, 0xBF
	case b0 >= 0xE1 && b0 <= 0xEC:
		want, lo, hi = 3, 0x80, 0xBF
	case b0 == 0xED:
		// The surrogate range is not "a valid sequence CPython rejects
		// later"; it is ill-formed at the SECOND byte, which is why
		// b"\xed\xa0\x80" is three replacements and not one.
		want, lo, hi = 3, 0x80, 0x9F
	case b0 >= 0xEE && b0 <= 0xEF:
		want, lo, hi = 3, 0x80, 0xBF
	case b0 == 0xF0:
		want, lo, hi = 4, 0x90, 0xBF
	case b0 >= 0xF1 && b0 <= 0xF3:
		want, lo, hi = 4, 0x80, 0xBF
	case b0 == 0xF4:
		want, lo, hi = 4, 0x80, 0x8F
	default:
		// 0x80-0xC1 and 0xF5-0xFF cannot begin a sequence.
		return 1
	}
	n := 1
	for ; n < want && n < len(b); n++ {
		if b[n] < lo || b[n] > hi {
			break
		}
		lo, hi = 0x80, 0xBF
	}
	return n
}
