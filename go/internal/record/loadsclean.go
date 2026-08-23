package record

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

// LoadsClean is jsonl_utils.loads_clean: a JSON-object decode that REFUSES
// byte-tainted and structurally ambiguous lines instead of laundering them.
// It is the shared admission predicate for keyed stores that rewrite
// themselves — a line this refuses must be carried VERBATIM by the rewrite,
// never re-serialized, so the corruption keeps announcing itself.
//
// Four refusals, each one a probed Python finding this port inherits:
//
//   - Raw bytes that are not valid UTF-8. Python carries them as lone
//     surrogates via surrogateescape; Go's decoder would silently rewrite
//     them to U+FFFD inside the very text a consumer trusts.
//   - A lone surrogate arriving as a JSON *escape* (`"\udcff"`): a
//     pure-ASCII line whose bytes are valid UTF-8, so the check above
//     cannot see it, and Go's decoder turns it into U+FFFD just the same.
//   - Trailing data after the object (`{...}{...}`), which Python's
//     json.loads raises "Extra data" on but Decoder.Decode ignores.
//   - Duplicate names in one object. Both runtimes' decoders silently keep
//     the LAST value, so `{"applied": false, "applied": true}` reads as
//     applied and a rewrite destroys the other value. Two values where one
//     is discarded by an implementation detail is a corrupt row, and
//     corrupt rows strand.
//
// Numbers decode as json.Number so an int stays distinguishable from a
// float — Python's json.loads makes that distinction and its validators
// depend on it (7 vs 7.0). Named divergence: Python's json.loads ACCEPTS
// the non-standard NaN/Infinity tokens and its validators then reject the
// row for non-finiteness; Go's decoder refuses the line outright. Same
// outcome (the row strands), different door.
func LoadsClean(line string) (map[string]any, error) {
	if !utf8.ValidString(line) {
		return nil, fmt.Errorf("byte-tainted line (raw non-UTF-8 bytes)")
	}
	if err := RefuseLoneSurrogates([]byte(line)); err != nil {
		return nil, err
	}
	if err := refuseDuplicateNames(line); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var m map[string]any
	if err := dec.Decode(&m); err != nil {
		return nil, err
	}
	if m == nil {
		return nil, fmt.Errorf("not a JSON object")
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, fmt.Errorf("trailing data after JSON value")
	}
	return m, nil
}

// IsFrameBlank ports jsonl_utils.is_frame_blank: a fragment is FRAMING only
// when it is empty. Deliberately not a whitespace trim — Python's str.strip
// removes Unicode whitespace that JSON forbids, so " "+row trimmed into
// something that parses (laundering the row's bytes) and a line of " "
// alone counted as blank and was dropped from a rewrite entirely.
func IsFrameBlank(raw string) bool { return raw == "" }

// refuseDuplicateNames walks the token stream and fails any object that
// names the same key twice, at any depth.
func refuseDuplicateNames(line string) error {
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var walk func() error
	walk = func() error {
		tok, err := dec.Token()
		if err != nil {
			return err
		}
		delim, ok := tok.(json.Delim)
		if !ok {
			return nil // scalar
		}
		switch delim {
		case '{':
			seen := map[string]bool{}
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return err
				}
				key, ok := kt.(string)
				if !ok {
					return fmt.Errorf("non-string object key")
				}
				if seen[key] {
					return fmt.Errorf("duplicate name %q in one object", key)
				}
				seen[key] = true
				if err := walk(); err != nil {
					return err
				}
			}
		case '[':
			for dec.More() {
				if err := walk(); err != nil {
					return err
				}
			}
		}
		if _, err := dec.Token(); err != nil { // closing delimiter
			return err
		}
		return nil
	}
	if err := walk(); err != nil && err != io.EOF {
		return err
	}
	return nil
}

// RefuseLoneSurrogates scans raw JSON text for \uD800–\uDFFF escapes that do
// not form a valid high+low surrogate pair. It tracks the backslash escape
// state so \\u1234 (a literal backslash) is not misread as an escape.
func RefuseLoneSurrogates(raw []byte) error {
	hexVal := func(at int) (int, bool) {
		if at+4 > len(raw) {
			return 0, false
		}
		v := 0
		for _, b := range raw[at : at+4] {
			v <<= 4
			switch {
			case b >= '0' && b <= '9':
				v |= int(b - '0')
			case b >= 'a' && b <= 'f':
				v |= int(b-'a') + 10
			case b >= 'A' && b <= 'F':
				v |= int(b-'A') + 10
			default:
				return 0, false
			}
		}
		return v, true
	}
	for i := 0; i < len(raw); i++ {
		if raw[i] != '\\' {
			continue
		}
		if i+1 >= len(raw) {
			break
		}
		if raw[i+1] != 'u' {
			i++ // consume the escaped char (covers \\, \", \n, ...)
			continue
		}
		v, ok := hexVal(i + 2)
		if !ok {
			i++ // malformed escape; the JSON decoder will refuse it
			continue
		}
		switch {
		case v >= 0xDC00 && v <= 0xDFFF:
			return fmt.Errorf("lone low surrogate \\u%04x — refused", v)
		case v >= 0xD800 && v <= 0xDBFF:
			lo, ok := hexVal(i + 8)
			if !ok || i+8 > len(raw) || raw[i+6] != '\\' || raw[i+7] != 'u' ||
				lo < 0xDC00 || lo > 0xDFFF {
				return fmt.Errorf("lone high surrogate \\u%04x — refused", v)
			}
			i += 11 // consume the whole pair
			continue
		}
		i += 5
	}
	return nil
}
