package pyval

import (
	"encoding/json"
	"errors"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Python's str() and repr() over a decoded JSON value.
//
// This exists because LLM-derived JSON reaches string fields through
// `str(x).strip()` all over this codebase — mission titles, feature
// titles, validation criteria — and `str` is NOT a cast there. A model
// that answers `"title": {"a": 1}` produces the eight-character string
// `{'a': 1}` in Python, and a model that answers `"title": null`
// produces `None`, not "". Both are then written to a shared store as
// the thing a human reads and a later run keys on.
//
// The two differ only at the top level, and only for strings: `str("x")`
// is `x` while `repr("x")` is `'x'`. Inside a container both use repr,
// which is why Str delegates to Repr for everything except a bare
// string.
//
// NOTE the ordered types. `str({'b': 1, 'a': 2})` keeps INSERTION order,
// so a Go map cannot produce it — decode with LoadsOrdered and pass the
// resulting Obj/List.

// Str is Python's str(v).
//
// Note what that means for a MISSING value: str(None) is the
// four-character string "None", so Str(nil) is "None" and not "". That is
// correct, and it is also a trap, because the Python source this port
// reads almost never writes a bare str(x) over a value that might be
// absent — it writes `str(x or "")` or `d.get(k, "")` first. See
// StrOrEmpty and Obj.GetString, which are the two spellings that actually
// appear, and reach for one of those unless the Python line really is a
// bare str().
func Str(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return Repr(v)
}

// StrOrEmpty is Python's `str(v or "")` — the defaulting idiom that
// guards nearly every str() over a value read out of a dict.
//
// It is a TRUTHINESS gate, not a nil check: None, "", 0, False and every
// empty container all become "". A value of 0 therefore vanishes, which
// is Python's actual behaviour at these sites and occasionally surprising
// — an id of integer 0 is dropped, an id of 5 is spelled "5".
//
// This exists because writing Str(v) where Python wrote str(v or "") was
// got wrong three separate times in one tranche (mission-r10), in three
// different packages, each time producing the literal string "None" in a
// field that should have been empty — once as an index key, so every run
// without a loop_id collided on one file. Three private wrong copies of
// one idiom is the exact shape the r9 lens named; this is the shared
// right one, and it has a test.
func StrOrEmpty(v any) string {
	if !Truthy(v) {
		return ""
	}
	return Str(v)
}

// Repr is Python's repr(v).
func Repr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case bool:
		if t {
			return "True"
		}
		return "False"
	case string:
		return pytext.Repr(t)
	case json.Number:
		return reprNumber(t)
	case float64:
		return pyjson.FloatRepr(t)
	case int:
		return itoa(t)
	case int64:
		return itoa64(t)
	case Obj:
		return reprObj(t)
	case List:
		return reprList(t)
	case map[string]any:
		// A Go map has no insertion order and Python's repr does, so
		// there is no honest answer. Refuse loudly rather than emit a
		// plausible-looking string in an arbitrary order.
		return "<unordered map: decode with LoadsOrdered>"
	case []any:
		return reprList(List(t))
	case []string:
		// Same missing arm as Truthy's: a Go-native string slice reprs as
		// the Python list it stands for rather than as
		// "<unrepresentable>".
		out := make(List, len(t))
		for i, x := range t {
			out[i] = x
		}
		return reprList(out)
	}
	return "<unrepresentable>"
}

// reprNumber renders a source literal the way Python's json.loads would
// have typed it. json.loads produces an int for `1` and a float for
// `1.0` or `1e2`, and their reprs differ: "1", "1.0", "100.0". Keeping
// the literal for ints is deliberate — Python has no integer width, so a
// value past int64 must not be forced through one.
func reprNumber(n json.Number) string {
	s := string(n)
	// The three literals unmaskPaired produces for CPython's bare NaN /
	// Infinity / -Infinity tokens. None of them contains '.', 'e' or 'E',
	// so without this they fall into the INTEGER arm below, fail
	// ParseInt, and get echoed verbatim — `NaN` and `Inf` where Python
	// prints `nan` and `inf`, straight into a milestone title.
	switch s {
	// The spellings unmaskPaired produces, which are CPython's own
	// (json.dumps writes Infinity, and json.loads refuses Inf). Without
	// this arm they fall into the integer arm below — no '.', 'e' or 'E'
	// — and a milestone title gets "Infinity" where Python renders "inf".
	case "NaN", "Infinity", "-Infinity":
		f, err := n.Float64()
		if err == nil {
			return pyjson.FloatRepr(f)
		}
		return s
	}
	if !strings.ContainsAny(s, ".eE") {
		// An integer literal. It is NOT the same string Python prints:
		// json.loads("-0") is the int 0, whose repr is "0". Round-trip
		// through ParseInt so the sign normalises the way int() does.
		if v, err := strconv.ParseInt(s, 10, 64); err == nil {
			return itoa64(v)
		}
		// Past int64. Python has no integer width, so the literal is the
		// answer — except for the same negative-zero case, which cannot
		// be reached through ParseInt here but is cheap to hold.
		if t := strings.TrimLeft(strings.TrimPrefix(s, "-"), "0"); t == "" {
			return "0"
		}
		return s
	}
	f, err := n.Float64()
	if err != nil {
		// A RANGE error is not a parse failure: strconv has already
		// produced the correctly-signed ±Inf, which is exactly what
		// CPython's json.loads yields for an overflowing literal. Falling
		// back to the source text printed `1e309` where Python prints
		// `inf` — and that string becomes a milestone title in a shared
		// mission.json (adversarial mission-r1 MEDIUM).
		//
		//	json.loads("1e309")  -> inf     json.loads("-4e323") -> -inf
		//
		// Any OTHER error means the literal is not a float at all, and
		// returning it verbatim stays the right answer there.
		if errors.Is(err, strconv.ErrRange) {
			return pyjson.FloatRepr(f)
		}
		return s
	}
	return pyjson.FloatRepr(f)
}

func reprObj(o Obj) string {
	var b strings.Builder
	b.WriteByte('{')
	for i, f := range o {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteString(pytext.Repr(f.Key))
		b.WriteString(": ")
		b.WriteString(Repr(f.Val))
	}
	b.WriteByte('}')
	return b.String()
}

func reprList(l List) string {
	var b strings.Builder
	b.WriteByte('[')
	for i, v := range l {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteString(Repr(v))
	}
	b.WriteByte(']')
	return b.String()
}

// ReprStrings is str()/repr() of a Python list of strings — the rendering
// a `"%s" % some_list` in a log line produces.
//
// It exists because Go's %v on a []string prints [a b], which is not a
// spelling Python can produce for any list: Python quotes each element
// and separates with ", ". Log lines that both runtimes write into the
// same operator-facing stream have to agree character for character, or
// a grep keyed on one silently misses the other's rows.
//
// A nil slice renders "[]", matching Python's empty list — not "null".
func ReprStrings(ss []string) string {
	var b strings.Builder
	b.WriteByte('[')
	for i, s := range ss {
		if i > 0 {
			b.WriteString(", ")
		}
		b.WriteString(pytext.Repr(s))
	}
	b.WriteByte(']')
	return b.String()
}

func itoa(n int) string { return itoa64(int64(n)) }
func itoa64(n int64) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	var buf [24]byte
	i := len(buf)
	u := uint64(n)
	if neg {
		u = uint64(-n)
	}
	for u > 0 {
		i--
		buf[i] = byte('0' + u%10)
		u /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// Truthy is Python's bool(x) over a decoded JSON value: everything is
// true except None, false, zero, and the empty string/list/dict.
//
// This is NOT pyval.Bool, and the difference is the point. Bool answers
// `v is True` for a field Python's own writer stores as a real bool;
// Truthy answers `bool(v)` for a field an LLM supplies, where the string
// "false" and the string "0" are both TRUE and only "" is false. Using
// the wrong one flips a gate.
func Truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case json.Number:
		f, err := t.Float64()
		return err != nil || f != 0
	case float64:
		return t != 0
	case int:
		return t != 0
	case int64:
		return t != 0
	case Obj:
		return len(t) > 0
	case List:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	case []any:
		return len(t) > 0
	case []string:
		// The sibling arm the sequence family was missing. Str, TypeName
		// and the task store's subscript errors all know this shape; here
		// it fell through to "unknown object", so an EMPTY []string
		// answered TRUE where every other empty sequence answers False —
		// a wrong answer, not a missing one (adversarial r11 round 9, LOW).
		return len(t) > 0
	}
	return true // an unknown object is truthy in Python
}

// IsInt is Python's `isinstance(v, int) and not isinstance(v, bool)` over
// a decoded JSON value, returning the value when it holds.
//
// Three rules meet here and each one is load-bearing somewhere:
//
//   - json.loads types `1` as int and `1.0` as float, so the LITERAL
//     decides. `[1.0]` where an index is expected is skipped; `[1]` is not.
//   - a bool IS an int in Python, so `True` passes a naive isinstance and
//     must be excluded by hand — which is exactly what the call sites do.
//   - a numeric STRING is not an int. `"1"` is skipped.
func IsInt(v any) (int, bool) {
	n, ok := v.(json.Number)
	if !ok {
		return 0, false // covers bool, string, nil, containers
	}
	// EQUIVALENT-MUTANT NOTE: deleting this check changes no verdict
	// today, because Int64() below rejects every literal containing
	// '.', 'e' or 'E' anyway (strconv.ParseInt's grammar). It stays
	// because it states the RULE — Python types the value from the
	// literal — where the fallthrough only states an accident of Go's
	// parser, and the two would come apart the moment this function
	// took a float64 instead of a json.Number.
	if strings.ContainsAny(string(n), ".eE") {
		return 0, false
	}
	i, err := n.Int64()
	if err != nil {
		return 0, false
	}
	return int(i), true
}

// SafeStr is llm_parse.safe_str(value, default=def).
//
// Read the Python, not its docstring: the docstring says "returning
// default if value is None/falsy", but the body is `if value is None`,
// so safe_str(0) is "0" and safe_str(False) is "False". Falsy is NOT
// the gate — only None is. A port that reads the docstring writes ""
// where CPython writes "0", into the same store row.
//
// Everything else goes through str(value).strip(), which is why a
// numeric or boolean field in a model reply survives as text on the
// Python side while a bare Go `.(string)` assertion turns it into ""
// (adversarial mission-r6 MEDIUM and LOW, several sites).
func SafeStr(v any, def string) string {
	if v == nil {
		return def
	}
	return pytext.Strip(Str(v))
}

// SafeStrList is llm_parse.safe_list with its DEFAULT element_type=str:
// a non-list is [], and non-string elements are DROPPED rather than
// coerced or rejecting the whole list. Each surviving element then goes
// through safe_str at the call sites that need it.
//
// The str default is the trap safe_list's own docstring warns about —
// a caller parsing a list of JSON objects must use SafeDictList or
// every item disappears.
func SafeStrList(v any) []string {
	raw, ok := v.([]any)
	if !ok {
		return nil
	}
	var out []string
	for _, e := range raw {
		if s, ok := e.(string); ok {
			out = append(out, s)
		}
	}
	return out
}

// SafeDictList is llm_parse.safe_list(value, element_type=dict).
func SafeDictList(v any) []map[string]any {
	raw, ok := v.([]any)
	if !ok {
		return nil
	}
	var out []map[string]any
	for _, e := range raw {
		if m, ok := e.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// GetOr is Python's `d.get(key, default)`: PRESENCE decides, not
// truthiness and not type. A key holding "" or 0 or nil returns that
// value, never the default.
//
// The Go idiom it replaces — `s, _ := d[k].(string); if s == "" { s =
// def }` — differs on two inputs at once: a present-but-empty value
// (Python keeps "", Go substituted the default) and a present-but-wrong
// type (Python keeps it for the caller's own coercion, Go silently
// zeroed it). Both reached change_log.jsonl through the evolver's apply
// path (adversarial mission-r6 MEDIUM).
func GetOr(d map[string]any, key string, def any) any {
	if v, ok := d[key]; ok {
		return v
	}
	return def
}
