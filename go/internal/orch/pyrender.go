package orch

import (
	"encoding/json"
	"fmt"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
)

// This file is `json.dumps(obj, indent=2)` — the writer discipline every
// project-ledger sidecar uses (mission.json, feature_list.json, the
// DOING-PID sidecar, the provenance records). It is NOT the JSONL
// single-line lane; that one lives in internal/pyjson.
//
// WHERE THIS BELONGS: internal/pyjson, next to Ordered. It is parked here
// because pyjson was under adversarial review when the project ledger
// needed it, and moving a file someone is reviewing is how a round's
// findings stop landing against the thing that was reviewed. `pids.go`
// had already rolled its own indent renderer for one specific shape, so
// this is the second instance — which is the threshold at which the
// duplication stops being cheap. Both fold into pyjson together.
//
// Two things pyjson does not do yet, both measured against CPython on this
// box rather than read from documentation:
//
//   - ensure_ascii. Python's json.dumps escapes every code point >= 0x7f
//     to \uXXXX (astral planes as a surrogate PAIR), and DEL at 0x7f is
//     escaped even though it is ASCII. A writer that wants raw UTF-8 has
//     to pass ensure_ascii=False explicitly, and six Python writers do —
//     so the flag is a per-writer decision on that side and the Go twin
//     must mirror whichever writer it ports, never pick one globally.
//     Every writer in this file ports a bare json.dumps, so: escaped.
//   - Key separator. Python writes `": "` with a space, in indent mode and
//     compact mode alike. In indent mode the ITEM separator loses its
//     trailing space because a newline follows it, which is why the
//     indent-2 sidecars compared byte-identical in earlier rounds while
//     the single-line lane's `", "` divergence went unnoticed.

// pyField is one key/value pair, holding its position. Go maps have no
// order and Python dicts do, and a rewrite of someone else's file must
// give the keys back in the order they arrived.
type pyField struct {
	Key string
	Val any
}

// pyObj is an ordered JSON object. pyList is a JSON array. Values inside
// either are: nil, bool, string, json.Number, float64, int, pyObj, pyList.
type pyObj []pyField

type pyList []any

// Get returns the value for a key and whether it was present.
func (o pyObj) Get(key string) (any, bool) {
	for _, f := range o {
		if f.Key == key {
			return f.Val, true
		}
	}
	return nil, false
}

// Set replaces a key's value IN PLACE, keeping its ordinal, or appends it
// at the tail if it is new — which is what assigning to a Python dict
// does, and the reason a patched foreign file does not come back
// reordered.
func (o *pyObj) Set(key string, val any) {
	for i := range *o {
		if (*o)[i].Key == key {
			(*o)[i].Val = val
			return
		}
	}
	*o = append(*o, pyField{Key: key, Val: val})
}

// GetString reads a string field, defaulting to "" — Python's
// `d.get(k, "")` where a wrong TYPE is as absent as a missing key.
func (o pyObj) GetString(key string) string {
	v, _ := o.Get(key)
	s, _ := v.(string)
	return s
}

// DumpsIndent2 renders v as json.dumps(v, indent=2) with ensure_ascii.
// There is no trailing newline: Python's dumps does not add one, and both
// of this package's callers write the result verbatim.
func DumpsIndent2(v any) (string, error) {
	var sb strings.Builder
	if err := renderPy(&sb, v, 0); err != nil {
		return "", err
	}
	return sb.String(), nil
}

// DumpsCompactPy renders v the way a bare json.dumps(v) does: one line,
// `", "` between items and `": "` after each key. Python's DEFAULT
// separators carry those spaces; the compact `(",", ":")` form is
// something a caller has to ask for and none of these callers do.
func DumpsCompactPy(v any) (string, error) {
	var sb strings.Builder
	if err := renderPy(&sb, v, -1); err != nil {
		return "", err
	}
	return sb.String(), nil
}

// renderPy writes v at nesting depth. depth < 0 means compact (one line);
// otherwise each level indents two more spaces.
func renderPy(sb *strings.Builder, v any, depth int) error {
	switch t := v.(type) {
	case nil:
		sb.WriteString("null")
		return nil
	case string:
		esc, err := pyString(t)
		if err != nil {
			return err
		}
		sb.WriteString(esc)
		return nil
	case pyObj:
		return renderPyContainer(sb, depth, '{', '}', len(t), func(i int, sb *strings.Builder, d int) error {
			key, err := pyString(t[i].Key)
			if err != nil {
				return err
			}
			sb.WriteString(key)
			sb.WriteString(": ") // Python's key separator carries a space
			return renderPy(sb, t[i].Val, d)
		})
	case pyList:
		return renderPyContainer(sb, depth, '[', ']', len(t), func(i int, sb *strings.Builder, d int) error {
			return renderPy(sb, t[i], d)
		})
	case []string:
		return renderPyContainer(sb, depth, '[', ']', len(t), func(i int, sb *strings.Builder, d int) error {
			esc, err := pyString(t[i])
			if err != nil {
				return err
			}
			sb.WriteString(esc)
			return nil
		})
	}
	// Everything else is a scalar pyjson already spells Python's way: a
	// json.Number keeps its source literal, a whole float keeps its ".0",
	// bools and ints go through the generic encoder unchanged.
	out, err := pyjson.Value(v)
	if err != nil {
		return err
	}
	// Guard rather than trust: a Go map or slice reaching here would be
	// rendered by pyjson's own (sorted, unspaced) shape, silently mixing
	// two spellings inside one file. Build a pyObj/pyList instead.
	if strings.HasPrefix(out, "{") || strings.HasPrefix(out, "[") {
		return fmt.Errorf("orch: %T must be built as pyObj/pyList to keep "+
			"key order and separators, not passed as a Go container", v)
	}
	sb.WriteString(out)
	return nil
}

func renderPyContainer(sb *strings.Builder, depth int, open, close byte, n int,
	item func(i int, sb *strings.Builder, d int) error) error {
	sb.WriteByte(open)
	if n == 0 {
		// json.dumps renders an EMPTY container inline even in indent
		// mode: "[]" and "{}", never "[\n]".
		sb.WriteByte(close)
		return nil
	}
	inner := depth
	if depth >= 0 {
		inner = depth + 1
	}
	for i := 0; i < n; i++ {
		if i > 0 {
			sb.WriteByte(',')
			if depth < 0 {
				sb.WriteByte(' ') // compact mode's ", " item separator
			}
		}
		if depth >= 0 {
			sb.WriteByte('\n')
			sb.WriteString(strings.Repeat("  ", inner))
		}
		if err := item(i, sb, inner); err != nil {
			return err
		}
	}
	if depth >= 0 {
		sb.WriteByte('\n')
		sb.WriteString(strings.Repeat("  ", depth))
	}
	sb.WriteByte(close)
	return nil
}

// pyString is one JSON string literal with ensure_ascii escaping.
//
// The table is measured, not assumed: the five short escapes Python uses
// (\b \t \n \f \r), \uXXXX for every other C0 control, \" and \\, and
// \uXXXX for everything from 0x7f up — DEL included, which is ASCII and
// escaped anyway. `/`, `<`, `>` and `&` are NOT escaped.
func pyString(s string) (string, error) {
	if !pyjson.IsCleanText(s) {
		return "", fmt.Errorf("byte-tainted text refused")
	}
	var sb strings.Builder
	sb.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			sb.WriteString(`\"`)
		case '\\':
			sb.WriteString(`\\`)
		case '\b':
			sb.WriteString(`\b`)
		case '\t':
			sb.WriteString(`\t`)
		case '\n':
			sb.WriteString(`\n`)
		case '\f':
			sb.WriteString(`\f`)
		case '\r':
			sb.WriteString(`\r`)
		default:
			switch {
			case r < 0x20 || r >= 0x7f:
				if r > 0xFFFF {
					// Astral planes are emitted as a UTF-16 surrogate
					// PAIR, matching Python — not as a single \U escape,
					// which JSON has no syntax for.
					hi, lo := utf16.EncodeRune(r)
					fmt.Fprintf(&sb, `\u%04x\u%04x`, hi, lo)
				} else {
					fmt.Fprintf(&sb, `\u%04x`, r)
				}
			default:
				sb.WriteRune(r)
			}
		}
	}
	sb.WriteByte('"')
	return sb.String(), nil
}

// LoadsOrdered is json.loads with the key order kept.
//
// Decoding into a Go map throws order away, and every rewrite path here
// reads a file, patches one field and writes the whole thing back — so
// without this a patch of a foreign file is a full reformat of someone
// else's data. Numbers keep their SOURCE LITERAL (json.Number), so a
// stored `1.0` does not come back `1` and a counter does not come back
// `42.0`.
func LoadsOrdered(text string) (any, error) {
	dec := json.NewDecoder(strings.NewReader(text))
	dec.UseNumber()
	v, err := decodeOrdered(dec)
	if err != nil {
		return nil, err
	}
	// Refuse trailing content the way json.loads does — `{}{}` is an
	// error there, and accepting it would let a torn write parse.
	if _, err := dec.Token(); err == nil {
		return nil, fmt.Errorf("trailing content after JSON value")
	}
	return v, nil
}

func decodeOrdered(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			obj := pyObj{}
			for dec.More() {
				keyTok, err := dec.Token()
				if err != nil {
					return nil, err
				}
				key, ok := keyTok.(string)
				if !ok {
					return nil, fmt.Errorf("object key is not a string")
				}
				val, err := decodeOrdered(dec)
				if err != nil {
					return nil, err
				}
				// A DUPLICATE key: Python's json.loads keeps the LAST
				// value and the FIRST position is irrelevant because the
				// dict has one slot. Set does exactly that.
				obj.Set(key, val)
			}
			if _, err := dec.Token(); err != nil { // closing brace
				return nil, err
			}
			return obj, nil
		case '[':
			list := pyList{}
			for dec.More() {
				val, err := decodeOrdered(dec)
				if err != nil {
					return nil, err
				}
				list = append(list, val)
			}
			if _, err := dec.Token(); err != nil { // closing bracket
				return nil, err
			}
			return list, nil
		}
		return nil, fmt.Errorf("unexpected delimiter %v", t)
	default:
		return tok, nil
	}
}

// pyIntOf reads a JSON number as Python's int() would after a
// `d.get(k, 0)`: a missing key, a wrong type, or an unparseable literal
// all give 0, and a float literal TRUNCATES toward zero rather than
// failing, matching int(3.9) == 3.
func pyIntOf(v any) int {
	switch t := v.(type) {
	case json.Number:
		if i, err := t.Int64(); err == nil {
			return int(i)
		}
		if f, err := t.Float64(); err == nil {
			return int(f)
		}
	case float64:
		return int(t)
	case int:
		return t
	}
	return 0
}

// pyClip is Python's s[:n] — n CODE POINTS, not n bytes. Every truncation
// in this package feeds a human-facing line, and slicing bytes both counts
// wrong and can split a rune into replacement characters.
func pyClip(s string, n int) string {
	if n <= 0 {
		return ""
	}
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	count := 0
	for i := range s {
		if count == n {
			return s[:i]
		}
		count++
	}
	return s
}

// pyBool reads a JSON boolean the way `bool(d.get(k, False))` does: only a
// real `true` is true. A truthy STRING is deliberately not honoured —
// Python's manifest writer stores a real bool and the monotonicity gate
// compares `is True`, which no string satisfies either.
func pyBool(v any) bool {
	b, _ := v.(bool)
	return b
}

// pyFloatOf is float(d.get(k, 0.0)) with the same forgiveness as pyIntOf.
func pyFloatOf(v any) float64 {
	switch t := v.(type) {
	case json.Number:
		if f, err := t.Float64(); err == nil {
			return f
		}
	case float64:
		return t
	case int:
		return float64(t)
	}
	return 0
}

// nowISOPy is datetime.now(timezone.utc).isoformat().
//
// The one thing a naive format string gets wrong: Python OMITS the
// fractional part entirely when the microsecond is 0, where Go's
// ".000000" layout always prints six digits. It is a one-in-a-million
// case and it is the kind that shows up once, in production, as an
// unparseable timestamp in someone else's reader.
func nowISOPy(t time.Time) string {
	if t.Nanosecond()/1000 == 0 {
		return t.Format("2006-01-02T15:04:05-07:00")
	}
	return t.Format("2006-01-02T15:04:05.000000-07:00")
}
