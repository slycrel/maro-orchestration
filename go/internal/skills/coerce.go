package skills

import (
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
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

func getInt(d map[string]any, key string, dst *int) {
	if v, ok := d[key]; ok && v != nil {
		if f, isNum := numberOf(v); isNum && !math.IsNaN(f) && !math.IsInf(f, 0) {
			*dst = int(f) // int() truncates toward zero, like Python's
			return
		}
		if s, isStr := v.(string); isStr { // Python int("7")
			if n, err := strconv.ParseInt(strings.TrimSpace(s), 10, 64); err == nil {
				*dst = int(n)
			}
		}
	}
}

func getFloat(d map[string]any, key string, dst *float64) {
	if v, ok := d[key]; ok && v != nil {
		if f, isNum := numberOf(v); isNum {
			*dst = f
			return
		}
		if s, isStr := v.(string); isStr { // Python float("0.5")
			if f, err := strconv.ParseFloat(strings.TrimSpace(s), 64); err == nil {
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

// isCleanText reports whether a string may be written to the store: valid
// UTF-8 with no surrogate code points. Go strings can carry arbitrary
// bytes, so this is the writer-side twin of LoadsClean's reader-side check.
func isCleanText(s string) bool {
	if !utf8.ValidString(s) {
		return false
	}
	for _, r := range s {
		if r >= 0xD800 && r <= 0xDFFF {
			return false
		}
		if r == utf8.RuneError {
			// A literal U+FFFD is legal text, but it is also what a
			// silent decode-failure leaves behind. The store has no
			// legitimate producer of it, so refusing is the safe side.
			return false
		}
	}
	return true
}

// isISOTimestamp is datetime.fromisoformat's acceptance, used only as a
// validity PREDICATE (the value is never re-rendered from the parse).
// Named divergence, carried from the port's existing parseISO: Python's
// fromisoformat also accepts a bare date ("2026-08-23"), which this
// accepts too, but Go's parser is stricter about odd offsets. A stricter
// timestamp check can only strand a row, never admit a bad one.
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

// jsonString encodes one string as a JSON literal with Python-compatible
// escaping (HTML escaping off — Python's json.dumps does not escape < > &).
func jsonString(s string) (string, error) {
	if !isCleanText(s) {
		return "", fmt.Errorf("byte-tainted text refused")
	}
	var buf strings.Builder
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(s); err != nil {
		return "", err
	}
	return strings.TrimSuffix(buf.String(), "\n"), nil
}

// orEmpty turns a nil slice into an empty one so JSON emits [] not null —
// Python's dataclass default_factory=list semantics.
func orEmpty(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}
