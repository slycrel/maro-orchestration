package pyval

import (
	"fmt"
	"os"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// DecodeUTF8Strict is `bytes.decode("utf-8")` — and, through it,
// `Path.read_text(encoding="utf-8")`, which is how every Python module in
// this system reads a file.
//
// It exists because Go's default is the opposite of Python's. Converting
// []byte to string keeps whatever bytes were there, and encoding/json
// silently substitutes U+FFFD for each undecodable one; the document parses,
// the caller gets an ordinary object, and the next write re-encodes those
// replacement characters to disk. Python never gets that far: it raises
// before any caller sees a value. A port that reads leniently and writes
// back does not merely disagree about what the file SAYS — it destroys what
// the file HELD, and the Python runtime then reads content nobody wrote.
//
// The exception's class and sentence are reproduced, not approximated,
// because a class is part of a function's behaviour: `except
// UnicodeDecodeError` and `except Exception` are different programs, and a
// message that names the offending byte and its offset is how the operator
// finds it.
//
// # The rules, measured on CPython 3.14.3 rather than read off a table
//
//   - A byte that cannot begin a sequence — 0x80–0xC1 (continuations, and
//     the two overlong leads) or 0xF5–0xFF — is "invalid start byte", one
//     byte wide. So 0xC0 0xAF, the classic overlong slash, is reported at
//     0xC0 as a bad START, not as a bad continuation.
//   - A lead byte whose sequence runs off the END of the input is
//     "unexpected end of data", spanning what is there. This is why the
//     same truncated 0xC3 reports differently depending on whether another
//     byte follows it: mid-buffer it is a bad continuation, at EOF it is
//     unexpected end of data.
//   - Anything else bad in the middle of a sequence is "invalid
//     continuation byte", spanning the lead plus the continuations that
//     were valid. The first continuation carries a NARROWER range for four
//     leads (E0, ED, F0, F4), which is what rejects overlong encodings,
//     UTF-16 surrogates and code points above U+10FFFF — each reported at
//     width one because only the lead had been accepted.
//   - The width decides the SENTENCE: one byte names the byte, more than
//     one names a position range.
func DecodeUTF8Strict(b []byte) (string, error) {
	for i := 0; i < len(b); {
		c := b[i]
		if c < 0x80 {
			i++
			continue
		}
		var need int
		switch {
		case c >= 0xC2 && c <= 0xDF:
			need = 1
		case c >= 0xE0 && c <= 0xEF:
			need = 2
		case c >= 0xF0 && c <= 0xF4:
			need = 3
		default:
			return "", utf8DecodeErr(b, i, i+1, "invalid start byte")
		}
		for k := 1; k <= need; k++ {
			if i+k >= len(b) {
				return "", utf8DecodeErr(b, i, len(b), "unexpected end of data")
			}
			cc := b[i+k]
			ok := cc >= 0x80 && cc <= 0xBF
			if k == 1 {
				// The narrowed first-continuation ranges. Without these the
				// decoder would accept overlong forms, surrogates, and code
				// points past U+10FFFF — all of which CPython refuses.
				switch c {
				case 0xE0:
					ok = cc >= 0xA0 && cc <= 0xBF
				case 0xED:
					ok = cc >= 0x80 && cc <= 0x9F
				case 0xF0:
					ok = cc >= 0x90 && cc <= 0xBF
				case 0xF4:
					ok = cc >= 0x80 && cc <= 0x8F
				}
			}
			if !ok {
				return "", utf8DecodeErr(b, i, i+k, "invalid continuation byte")
			}
		}
		i += need + 1
	}
	return string(b), nil
}

// utf8DecodeErr spells CPython's UnicodeDecodeError message. `end` is
// exclusive, matching the exception's own .end attribute, and the reported
// range is inclusive — position 6-7 for end-start == 2.
func utf8DecodeErr(b []byte, start, end int, reason string) error {
	if end-start == 1 {
		return &PyErr{Class: "UnicodeDecodeError", Msg: fmt.Sprintf(
			"'utf-8' codec can't decode byte 0x%02x in position %d: %s",
			b[start], start, reason)}
	}
	return &PyErr{Class: "UnicodeDecodeError", Msg: fmt.Sprintf(
		"'utf-8' codec can't decode bytes in position %d-%d: %s",
		start, end-1, reason)}
}

// ReadText is `Path.read_text(encoding="utf-8")`, whole.
//
// Two rules, and the port had been missing the second at every call site
// that keeps the text rather than immediately splitting it:
//
//   - STRICT decoding. Invalid UTF-8 raises there, so it fails here.
//     DecodeUTF8Strict has carried this half for a while; `os.ReadFile` +
//     `string(raw)` carries neither, and encoding/json then substitutes
//     U+FFFD for each bad byte, so the port silently ships content nobody
//     wrote where CPython refuses to ship anything at all.
//   - UNIVERSAL NEWLINES. `read_text` opens with `newline=None`, so "\r\n"
//     and a lone "\r" are "\n" before the caller sees them.
//
// The second rule is invisible to a caller that splits immediately and
// decisive for one that does not: pack's artifacts are hashed into a
// manifest and shipped as tar members, so a CRLF skill file exported by Go
// produced different member bytes, a different sha256, a different
// REVIEW.md and a different payload digest than the same file exported by
// Python. Neither pack could verify in the other runtime.
//
// This is the fourth lens again — a helper you did not look for is a helper
// you will write again — and the shape it took here is worse than a rewrite:
// every site had HALF of read_text, spelled a different way, and the halves
// looked complete on their own.
func ReadText(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	s, derr := DecodeUTF8Strict(raw)
	if derr != nil {
		return "", fmt.Errorf("%s: %w", path, derr)
	}
	return pytext.TranslateNewlines(s), nil
}
