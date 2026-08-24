package skills

import (
	"encoding/json"
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// Coercion helpers reproducing what dict_to_skill's constructors do to a
// stored value: str(), int(), float(), and the list-or-empty reads. Rows
// arrive decoded with UseNumber, so an int literal stays distinguishable
// from a float one — Python's json makes that distinction and its validator
// depends on it.

// pyStr is Python's str() for the value shapes a JSONL row can hold. It
// exists so a coercing READ path stays tolerant in the same places Python's
// is; the proving path never calls it.
func pyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return t
	case bool:
		if t {
			return "True"
		}
		return "False"
	case json.Number:
		return t.String()
	case float64:
		return strconv.FormatFloat(t, 'g', -1, 64)
	}
	return fmt.Sprintf("%v", v)
}

func getStr(d map[string]any, key string, dst *string) {
	if v, ok := d[key]; ok && v != nil {
		if s, isStr := v.(string); isStr {
			*dst = s
			return
		}
		*dst = pyStr(v)
	}
}

// numberOf reads a JSON number (UseNumber-decoded or already float64).
// Booleans are NOT numbers here — Python's validator explicitly excludes
// bool because `isinstance(True, int)` is True and a bool must not pass for
// a ranking input.
func numberOf(v any) (float64, bool) {
	switch t := v.(type) {
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case float64:
		return t, true
	case int:
		return float64(t), true
	}
	return 0, false
}

// isIntValue reports whether the STORED literal was an integer — "7" yes,
// "7.0" no, true no. Python's json.loads produces int vs float from the
// literal, and the validator's isinstance(v, int) reads that distinction.
func isIntValue(v any) bool {
	switch t := v.(type) {
	case json.Number:
		_, err := strconv.ParseInt(t.String(), 10, 64)
		return err == nil
	case int:
		return true
	}
	return false
}

// getInt coerces one stored value to an int, Python int()'s way.
//
// An integer LITERAL is parsed as an integer, never routed through float64
// first. Python reads `9007199254740993` exactly; a float64 round trip
// returns ...992, and `9223372036854775807` overflows on the way back to
// int — Go leaves out-of-range float→int conversions implementation-
// defined, and it produced a NEGATIVE counter. These fields are decision
// inputs (consecutive_failures drives the circuit breaker, variant_wins
// drives A/B), and a validator that ADMITS the row must not hand its
// consumer a different number than the one on disk.
func getInt(d map[string]any, key string, dst *int) {
	v, ok := d[key]
	if !ok || v == nil {
		return
	}
	// Integer literals first, exactly.
	switch t := v.(type) {
	case int:
		*dst = t
		return
	case json.Number:
		if n, err := strconv.ParseInt(t.String(), 10, 64); err == nil {
			*dst = int(n)
			return
		}
	case string: // Python int("7")
		if n, err := strconv.ParseInt(pyStrip(t), 10, 64); err == nil {
			*dst = int(n)
		}
		return
	}
	// A non-integer literal truncates toward zero, like Python's int().
	// Out of int64 range there is no faithful answer — Python has
	// bignums and Go does not — so the field keeps its default rather
	// than taking an implementation-defined one. Every validator in this
	// package refuses a float literal in an int field, so this path is
	// reachable only from the tolerant constructor on legacy rows.
	if f, isNum := numberOf(v); isNum && !math.IsNaN(f) && !math.IsInf(f, 0) &&
		f >= -9223372036854775808.0 && f < 9223372036854775808.0 {
		*dst = int(f)
	}
}

func getFloat(d map[string]any, key string, dst *float64) {
	if v, ok := d[key]; ok && v != nil {
		if f, isNum := numberOf(v); isNum {
			*dst = f
			return
		}
		if s, isStr := v.(string); isStr { // Python float("0.5")
			// pyval.ParseFloat, not pyStrip + strconv: pyStrip is
			// str.strip()'s 29 code points and float() strips 25, so
			// getFloat("\x1c0.5") set 0.5 where CPython raises — verbatim
			// the mission-r6 HIGH at a site its sweep did not reach. It
			// also took hex literals and refused Unicode digits. The value
			// lands in skills.jsonl as utility_score / success_rate
			// (adversarial mission-r7 MEDIUM).
			if f, ok := pyval.ParseFloat(s); ok {
				*dst = f
			}
		}
	}
}

// strList reads a list-of-strings field the way dict_to_skill does: the
// stored list verbatim when it is one, otherwise the default. Non-string
// elements are str()-coerced (the constructor is tolerant; the validator is
// the one that refuses them).
func strList(d map[string]any, key string, dst *[]string) {
	v, ok := d[key]
	if !ok || v == nil {
		return
	}
	list, isList := v.([]any)
	if !isList {
		return
	}
	out := make([]string, 0, len(list))
	for _, e := range list {
		out = append(out, pyStr(e))
	}
	*dst = out
}

// isCleanText is pyjson.IsCleanText: valid UTF-8, no surrogate code points.
//
// It deliberately ADMITS a literal U+FFFD. An earlier version refused it on
// the grounds that the store has no legitimate producer of a replacement
// character — but skills are minted from run output that includes
// web-fetched text, where U+FFFD is routine and legitimate, and the READER
// admits it. A writer stricter than its own reader does not prevent bad
// rows; it FREEZES good ones: such a skill loads and injects but can never
// be updated again (every outcome and utility write fails permanently), and
// because archiving is all-or-nothing, one of them blocks an entire cull.
// The refusals that remain — raw non-UTF-8 and surrogate code points — are
// for text that cannot round-trip at all, which is a different thing.
func isCleanText(s string) bool { return pyjson.IsCleanText(s) }

// isISOTimestamp is datetime.fromisoformat's acceptance, used only as a
// validity PREDICATE (the value is never re-rendered from the parse).
//
// NAMED DIVERGENCE, and wider than "odd offsets" — measured against
// fromisoformat over 313 shapes, every difference is Go refusing what
// Python admits: ISO BASIC format ("20260823", "20260823T124500"), week
// dates ("2026-W34-1"), hour-only ("2026-08-23T00"), the lowercase
// separator ("2026-08-23t00:00:00"), hour 24 ("...T24:00:00"), seconds in
// the offset ("+00:00:00") and the compact offset ("-0700"). Nothing
// either runtime MINTS is affected — every writer emits the port-wide
// six-digit stamp — so this bites only a hand-edited or pack-imported row,
// which goes invisible to Go while staying live in Python. The direction
// is the safe one (strand, never admit), which is why it is documented
// rather than papered over with a longer layout list.
func isISOTimestamp(s string) bool {
	if s == "" {
		return false
	}
	layouts := []string{
		time.RFC3339Nano, time.RFC3339,
		"2006-01-02T15:04:05.999999999", "2006-01-02T15:04:05",
		"2006-01-02T15:04", "2006-01-02 15:04:05.999999999-07:00",
		"2006-01-02 15:04:05", "2006-01-02",
	}
	for _, l := range layouts {
		if _, err := time.Parse(l, s); err == nil {
			return true
		}
	}
	return false
}

// jsonString is pyjson.String: Python-compatible escaping, HTML escaping
// off (json.dumps does not escape < > &).
func jsonString(s string) (string, error) { return pyjson.String(s) }

// orEmpty turns a nil slice into an empty one so JSON emits [] not null —
// Python's dataclass default_factory=list semantics.
func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

// pyBool is Python's bool() for stored values — the truthiness the coercing
// stats constructor applies. Note the reader REFUSES non-bool
// needs_escalation before this runs (validateStatsRow); this exists for
// parity on paths Python leaves tolerant.
func pyBool(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case json.Number:
		f, err := t.Float64()
		return err == nil && f != 0
	case float64:
		return t != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

func sortStrings(s []string) { sort.Strings(s) }

// pyIsSpace is Python's str.isspace() for one rune. Go's unicode.IsSpace
// omits the four ASCII information separators (U+001C–U+001F) that Python
// counts as whitespace — the ONLY difference between the two sets over the
// whole rune range, measured.
//
// It matters because .strip() drives a REFUSAL: Python's validate_skill_row
// refuses an id, name or content_hash that is empty after stripping, so a
// row whose id is a lone U+001C is stranded there. With TrimSpace it was
// ADMITTED here — the unsafe direction, and squarely in this validator's
// threat model: an admitted row becomes eligible to be matched, replaced by
// SaveSkill, and archived-and-removed by a cull, while the other runtime
// carries it verbatim forever as unprovable.
func pyIsSpace(r rune) bool {
	return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
}

// pyStrip is Python's str.strip() — whitespace by pyIsSpace, both ends.
func pyStrip(s string) string { return strings.TrimFunc(s, pyIsSpace) }

// pyLower is Python's str.lower(). Go's strings.ToLower applies SIMPLE case
// mapping (one rune in, one rune out); Python applies the full mapping,
// where U+0130 (LATIN CAPITAL LETTER I WITH DOT ABOVE) lowercases to TWO
// runes, "i" + U+0307. Swept over the whole rune range, that is the only
// unconditional multi-rune lowercase mapping there is, so handling it
// outright is the whole fix — no case-folding dependency needed.
//
// It matters because this feeds both a STORED value (a normalized tag lands
// in a shared file) and the matching corpus: "İstanbul" tokenized one way
// in Go and another in Python meant the same skill scored differently in
// the two runtimes.
//
// This was a THIRD hand-rolled copy of the same helper — the exact shape
// that let M2's repr() bug survive in two places after being fixed in one.
// It delegates now, so the U+0130 mapping and the unicode-table supplement
// are defined once (adversarial r5, L4).
func pyLower(s string) string { return pytext.Lower(s) }

// pyRepr renders a string the way Python's !r does. Provenance reasons are
// stored prose in a shared file, so a differently-spelled reason is a
// differently stored row (the content-key prose divergence family).
//
// Delegates rather than reimplementing — see the note on scans.pyRepr:
// three copies of the old, broken spelling existed and a fix to one would
// not have reached the others.
func pyRepr(s string) string { return pytext.Repr(s) }
