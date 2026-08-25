package record

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
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
// depend on it (7 vs 7.0). NaN/Infinity are refused at this same door in
// both runtimes: Go's decoder rejects the tokens outright, and Python
// passes json.loads a parse_constant hook that raises on them
// (jsonl_utils._refuse_constant) — a row does not have to be laundered to
// do damage, it only has to be ADMITTED into a removal decision.
//
// Named divergences that remain, both in the strand-safe direction except
// where noted:
//
//   - Nesting depth. Go refuses at encoding/json's maxNestingDepth (10000);
//     CPython's parser goes deeper before its own stack gives out (measured:
//     Python admits depth 20000, refuses ~2M). Both strand eventually, at
//     different depths.
//   - Integer literals longer than 4300 digits. CPython's int() conversion
//     is capped there and raises, so loads_clean strands the row; Go would
//     admit it. Refused explicitly below, because this is the direction that
//     matters — a row Go admits and Python strands is a row only one runtime
//     will act on.
func LoadsClean(line string) (map[string]any, error) {
	v, err := LoadsCleanValue(line)
	if err != nil {
		return nil, err
	}
	m, isObj := v.(map[string]any)
	if !isObj {
		return nil, ErrNotAnObject
	}
	return m, nil
}

// ErrNotAnObject is what LoadsClean returns for a line that IS clean JSON
// and is not an object.
//
// A sentinel rather than a fresh fmt.Errorf because the difference matters
// to exactly one caller and matters a lot: jsonl_utils._classify counts a
// non-dict line in its OWN bucket ("non-dict"), separate from "malformed",
// and an operator reading `dropped 3 line(s): 3 non-dict` is being told
// something different from `3 malformed` — the first says a writer is
// emitting the wrong SHAPE, the second says bytes are torn. Folding the
// two would have been invisible: both are dropped, both are counted, and
// the total is the same.
var ErrNotAnObject = errors.New("not a JSON object")

// ErrByteTainted is what the admission predicate returns for a line that is
// not UTF-8 at all — a crash-torn append, typically.
//
// A sentinel for the same reason as ErrNotAnObject: the reader's report puts
// it in its own bucket ("undecodable"), and the alternative was matching on
// the message text. A message is prose, prose gets reworded, and the day
// someone improves this sentence the reader would silently start calling
// torn bytes malformed — a miscount no test that only checks "the row was
// dropped" can see.
var ErrByteTainted = errors.New("byte-tainted line (raw non-UTF-8 bytes)")

// LoadsCleanValue is LoadsClean without the object requirement: the same
// four refusals (invalid UTF-8, lone surrogates, duplicate names, oversized
// int literals) and the same trailing-data check, returning whatever JSON
// value the line holds.
//
// It exists because the reader needs to tell a line that is NOT JSON from a
// line that is JSON of the wrong shape, and LoadsClean answers "no" to both.
// LoadsClean is now this function plus one type assertion, so the two cannot
// drift into disagreeing about what a clean line is — which is the whole
// point of there being a single admission predicate.
func LoadsCleanValue(line string) (any, error) {
	if !utf8.ValidString(line) {
		return nil, ErrByteTainted
	}
	if err := RefuseLoneSurrogates([]byte(line)); err != nil {
		return nil, err
	}
	if err := refuseDuplicateNames(line); err != nil {
		return nil, err
	}
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()
	var v any
	if err := dec.Decode(&v); err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, fmt.Errorf("trailing data after JSON value")
	}
	return v, nil
}

// LoadsCleanOrdered is LoadsClean for a caller that intends to REWRITE the
// row it just read.
//
// Same admission predicate, same four refusals — the guards are literally
// the same calls, not a second copy, because a rewriting reader that
// admitted a line the removing reader refuses is how the two disagree
// about which rows exist.
//
// The guards being shared is NOT the same as the decoders agreeing, and
// that gap was real: the four refusals above are shared calls, but the
// parse underneath is pyval.LoadsOrdered where LoadsClean uses a bare
// json.Decoder, and only one of them enforced encoding/json's nesting
// limit. `Decode` checks depth in the scanner; a `Token()` walk does not.
// So this reader admitted a document LoadsClean refuses and recursed on
// it — to a `fatal error: stack overflow` at the tail.
//
// pyval bounds the ordered walk at the same 10000 now, and it took two
// rounds to get the bound in the right PLACE: encoding/json counts open
// containers, and the first fix counted tokens, which is one more for any
// document whose outermost value is a container. `{"a":[[…]]}` with 10000
// arrays disagreed for a round after the fix, and the corpus row that
// would have shown it was the one value the corpus did not carry — it had
// 9999 and 10001, a neighbour on each side. Both true boundaries are
// fixtures now, for both container shapes.
//
// A shared guard list is evidence about the guards, not about everything
// downstream of them. What differs is only the shape handed back:
// a pyval.Obj that remembers key order, so a stamper can set one field
// and re-emit the row with every other key still where its original
// writer put it.
//
// Python gets this for free — json.loads gives back an insertion-ordered
// dict and json.dumps walks it in that order — which is exactly why it is
// easy to miss on this side. A Go stamper that round-trips through
// map[string]any re-emits every row alphabetized. No consumer parses the
// bytes (they all json.loads), so nothing breaks; the store just stops
// being one both runtimes wrote the same way, and the next person to diff
// it sees every stamped row rewritten end to end.
//
// The order the guards run in is load-bearing: refuseDuplicateNames walks
// the line with a PLAIN decoder, which is also what rejects the bare NaN
// and Infinity tokens json.loads refuses through its parse_constant hook.
// pyval.LoadsOrdered deliberately ADMITS those (it masks them so one stray
// non-finite float in a model reply cannot kill a whole document), so
// calling it first would let a row in here that LoadsClean strands.
func LoadsCleanOrdered(line string) (pyval.Obj, error) {
	if !utf8.ValidString(line) {
		return nil, ErrByteTainted
	}
	if err := RefuseLoneSurrogates([]byte(line)); err != nil {
		return nil, err
	}
	if err := refuseDuplicateNames(line); err != nil {
		return nil, err
	}
	v, err := pyval.LoadsOrdered(line)
	if err != nil {
		return nil, err
	}
	obj, ok := v.(pyval.Obj)
	if !ok {
		return nil, ErrNotAnObject
	}
	return obj, nil
}

// IsFrameBlank ports jsonl_utils.is_frame_blank: a fragment is FRAMING only
// when it is empty. Deliberately not a whitespace trim — Python's str.strip
// removes Unicode whitespace that JSON forbids, so " "+row trimmed into
// something that parses (laundering the row's bytes) and a line of " "
// alone counted as blank and was dropped from a rewrite entirely.
func IsFrameBlank(raw string) bool { return raw == "" }

// refuseDuplicateNames walks the token stream and fails any object that
// names the same key twice, at any depth.
//
// The walk is ITERATIVE, over an explicit stack. A recursive walker is the
// natural shape and it is the wrong one here: this runs BEFORE Decode, so
// nothing has bounded the nesting yet, and encoding/json's own
// maxNestingDepth lives in the scanner, which Token() does not drive for
// delimiters. A recursive version died of an unrecoverable `fatal error:
// stack overflow` on a ~4MB line (measured: depth 1.5M returned an error,
// 2M fataled) — no recover, no defer, no strand, just process death on
// every run until someone hand-edited the store. Python removed recursion
// from its equivalent scanner for exactly this reason (jsonl_utils r6), and
// a line that does not parse must STRAND, not kill the reader.
func refuseDuplicateNames(line string) error {
	dec := json.NewDecoder(strings.NewReader(line))
	dec.UseNumber()

	// One frame per open container. seen == nil marks an array frame; an
	// object frame alternates key/value via expectKey.
	type frame struct {
		seen      map[string]bool
		expectKey bool
	}
	var stack []frame
	for {
		tok, err := dec.Token()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if num, isNum := tok.(json.Number); isNum && !intFitsPython(num) {
			return fmt.Errorf("integer literal exceeds 4300 digits — refused")
		}
		if delim, isDelim := tok.(json.Delim); isDelim {
			switch delim {
			case '{':
				stack = append(stack, frame{seen: map[string]bool{}, expectKey: true})
				continue
			case '[':
				stack = append(stack, frame{})
				continue
			default: // '}' or ']'
				if len(stack) == 0 {
					return fmt.Errorf("unbalanced JSON delimiter")
				}
				stack = stack[:len(stack)-1]
			}
		} else if len(stack) > 0 {
			top := &stack[len(stack)-1]
			if top.seen != nil && top.expectKey {
				key, isStr := tok.(string)
				if !isStr {
					return fmt.Errorf("non-string object key")
				}
				if top.seen[key] {
					return fmt.Errorf("duplicate name %q in one object", key)
				}
				top.seen[key] = true
				top.expectKey = false
				continue
			}
		}
		// A value just closed or completed: the enclosing object expects
		// its next key.
		if len(stack) > 0 {
			if top := &stack[len(stack)-1]; top.seen != nil {
				top.expectKey = true
			}
		}
	}
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

// intFitsPython reports whether an integer literal is one CPython can
// convert. CPython caps int() at 4300 digits and raises above it, so
// loads_clean strands such a row; without this check Go would admit a row
// Python strands, and only one runtime would act on it. Float literals are
// unaffected in both runtimes.
func intFitsPython(n json.Number) bool {
	s := n.String()
	if strings.ContainsAny(s, ".eE") {
		return true // a float literal, not an int() conversion
	}
	digits := len(s)
	if len(s) > 0 && (s[0] == '-' || s[0] == '+') {
		digits--
	}
	return digits <= 4300
}
