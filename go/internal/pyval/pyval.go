package pyval

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf16"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
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

// Field is one key/value pair, holding its position. Go maps have no
// order and Python dicts do, and a rewrite of someone else's file must
// give the keys back in the order they arrived.
type Field struct {
	Key string
	Val any
}

// Obj is an ordered JSON object. List is a JSON array. Values inside
// either are: nil, bool, string, json.Number, float64, int, Obj, List.
type Obj []Field

type List []any

// Get returns the value for a key and whether it was present.
func (o Obj) Get(key string) (any, bool) {
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
func (o *Obj) Set(key string, val any) {
	for i := range *o {
		if (*o)[i].Key == key {
			(*o)[i].Val = val
			return
		}
	}
	*o = append(*o, Field{Key: key, Val: val})
}

// Pop removes a key, reporting whether it was there — Python's
// `d.pop(k, None)`. The verdict tuple's "set or pop, never merge"
// doctrine needs the second half, and a Set(key, nil) would write a JSON
// null instead, which reads as a judged false to a sloppy consumer.
func (o *Obj) Pop(key string) bool {
	for i := range *o {
		if (*o)[i].Key == key {
			*o = append((*o)[:i], (*o)[i+1:]...)
			return true
		}
	}
	return false
}

// FromPlain converts the map[string]any / []any tree json.Unmarshal
// produces into the Obj/List tree the Dumps family renders, so a caller
// holding a decoded document can re-emit it Python's way without
// rebuilding it field by field.
//
// Keys come out SORTED, and that is a named LOSS, not a choice: a Go map
// has no insertion order to preserve, so the order Python would have
// written is already gone by the time a value reaches here. Sorted at
// least makes the output deterministic — encoding/json sorts too, so
// this changes nothing about key order and everything about the other
// two forks (HTML escaping and ensure_ascii). A caller that needs
// Python's insertion order must build the Obj itself; the two pack.json
// writers are the sites where this loss is live and they say so.
//
// Floats are NOT round-tripped through encoding/json on the way, which
// is the whole reason this is a walker and not a marshal-and-reparse:
// json.Marshal(3.0) is "3", so the reparse would hand back an int and
// json.dumps(3.0) is "3.0". The leaves are passed through untouched and
// pyjson renders them.
func FromPlain(v any) any {
	switch t := v.(type) {
	case Obj:
		out := make(Obj, len(t))
		for i, f := range t {
			out[i] = Field{Key: f.Key, Val: FromPlain(f.Val)}
		}
		return out
	case List:
		out := make(List, len(t))
		for i, e := range t {
			out[i] = FromPlain(e)
		}
		return out
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		out := make(Obj, 0, len(keys))
		for _, k := range keys {
			out = append(out, Field{Key: k, Val: FromPlain(t[k])})
		}
		return out
	case []any:
		out := make(List, len(t))
		for i, e := range t {
			out[i] = FromPlain(e)
		}
		return out
	case []map[string]any:
		out := make(List, len(t))
		for i, e := range t {
			out[i] = FromPlain(e)
		}
		return out
	case []string:
		out := make(List, len(t))
		for i, e := range t {
			out[i] = e
		}
		return out
	}
	// Reflection for every OTHER container spelling. The explicit cases
	// above are the common ones and are faster; this arm exists because
	// enumerating spellings is exactly the mistake that made this
	// necessary — a modality_distribution built as map[string]int is not
	// map[string]any, fell straight through to `return v`, and render
	// then REFUSED it as "must be built as Obj/List". The refusal is the
	// good outcome (a silent sorted-unspaced nest inside a Python-shaped
	// document is the bad one), but the row was dropped, so a whole
	// closure verdict went unpersisted. Named-spelling lists do not
	// close a type-shaped hole.
	rv := reflect.ValueOf(v)
	switch rv.Kind() {
	case reflect.Map:
		if rv.Type().Key().Kind() != reflect.String {
			break // json.dumps has no key for it either; let render refuse
		}
		keys := make([]string, 0, rv.Len())
		for _, k := range rv.MapKeys() {
			keys = append(keys, k.String())
		}
		sort.Strings(keys)
		out := make(Obj, 0, len(keys))
		for _, k := range keys {
			out = append(out, Field{
				Key: k,
				Val: FromPlain(rv.MapIndex(reflect.ValueOf(k).Convert(rv.Type().Key())).Interface()),
			})
		}
		return out
	case reflect.Slice, reflect.Array:
		// []byte is left alone: encoding/json base64s it and json.dumps
		// cannot serialise bytes at all, so there is no shared spelling
		// to converge on and turning it into a list of ints would invent
		// a third one.
		if rv.Type().Elem().Kind() == reflect.Uint8 {
			break
		}
		out := make(List, rv.Len())
		for i := 0; i < rv.Len(); i++ {
			out[i] = FromPlain(rv.Index(i).Interface())
		}
		return out
	}
	return v
}

// GetString reads a string field, defaulting to "" — Python's
// `d.get(k, "")` where a wrong TYPE is as absent as a missing key.
func (o Obj) GetString(key string) string {
	v, _ := o.Get(key)
	s, _ := v.(string)
	return s
}

// DumpsIndent2 renders v as json.dumps(v, indent=2) — ensure_ascii ON,
// which is json.dumps' default. There is no trailing newline: Python's
// dumps does not add one, and a caller that needs one adds it.
func DumpsIndent2(v any) (string, error) {
	var sb strings.Builder
	if err := render(&sb, v, 0, true); err != nil {
		return "", err
	}
	return sb.String(), nil
}

// DumpsIndent2Raw is json.dumps(v, indent=2, ensure_ascii=False): the same
// shape with non-ASCII written as raw UTF-8 rather than \uXXXX.
//
// ensure_ascii is a PER-WRITER decision in Python, not a global one, and
// the two spellings are both live in this workspace: mission.json takes the
// default and escapes, while task_store's _atomic_write passes
// ensure_ascii=False and does not. A port that picks one globally is wrong
// for half its callers, so the choice stays at the call site where Python
// puts it.
func DumpsIndent2Raw(v any) (string, error) {
	var sb strings.Builder
	if err := render(&sb, v, 0, false); err != nil {
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
	if err := render(&sb, v, -1, true); err != nil {
		return "", err
	}
	return sb.String(), nil
}

// render writes v at nesting depth. depth < 0 means compact (one line);
// otherwise each level indents two more spaces. ea is ensure_ascii.
func render(sb *strings.Builder, v any, depth int, ea bool) error {
	switch t := v.(type) {
	case nil:
		sb.WriteString("null")
		return nil
	case string:
		esc, err := encodeString(t, ea)
		if err != nil {
			return err
		}
		sb.WriteString(esc)
		return nil
	case Obj:
		return renderContainer(sb, depth, '{', '}', len(t), func(i int, sb *strings.Builder, d int) error {
			key, err := encodeString(t[i].Key, ea)
			if err != nil {
				return err
			}
			sb.WriteString(key)
			sb.WriteString(": ") // Python's key separator carries a space
			return render(sb, t[i].Val, d, ea)
		})
	case List:
		return renderContainer(sb, depth, '[', ']', len(t), func(i int, sb *strings.Builder, d int) error {
			return render(sb, t[i], d, ea)
		})
	case []string:
		return renderContainer(sb, depth, '[', ']', len(t), func(i int, sb *strings.Builder, d int) error {
			esc, err := encodeString(t[i], ea)
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
	// two spellings inside one file. Build a Obj/List instead.
	if strings.HasPrefix(out, "{") || strings.HasPrefix(out, "[") {
		return fmt.Errorf("pyval: %T must be built as Obj/List to keep "+
			"key order and separators, not passed as a Go container", v)
	}
	sb.WriteString(out)
	return nil
}

func renderContainer(sb *strings.Builder, depth int, open, close byte, n int,
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

// EncodeString is one JSON string literal with ensure_ascii escaping.
//
// The table is measured, not assumed: the five short escapes Python uses
// (\b \t \n \f \r), \uXXXX for every other C0 control, \" and \\, and
// \uXXXX for everything from 0x7f up — DEL included, which is ASCII and
// escaped anyway. `/`, `<`, `>` and `&` are NOT escaped.
func EncodeString(s string) (string, error) { return encodeString(s, true) }

// encodeString is EncodeString with ensure_ascii selectable. With ea
// false, every code point that is not a control or a mandatory escape is
// written as raw UTF-8 — which is what json.dumps(ensure_ascii=False)
// does. The control escapes are NOT optional in either mode: JSON has no
// syntax for a raw control character.
func encodeString(s string, ea bool) (string, error) {
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
			case r < 0x20 || (ea && r >= 0x7f):
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
//
// MEASURED RESIDUAL — lone surrogates (adversarial mission-r2 MEDIUM).
// A model that truncates an emoji escape at a token boundary emits a
// half pair, and the two runtimes keep DIFFERENT bytes:
//
//	{"title":"\ud800"}
//	  CPython  json.loads -> a 1-character str holding U+D800, and
//	           json.dumps writes it straight back as "\ud800"
//	  Go       encoding/json substitutes U+FFFD (3 bytes), and
//	           DumpsIndent2 then writes "\ufffd"
//
// It reaches the store: that is a milestone title in mission.json, and
// each runtime reads the other's file back to a different string.
// pyjson.IsCleanText accepts U+FFFD — it is valid UTF-8 — so nothing
// downstream refuses it.
//
// NOT fixed here, and the reason is scope rather than doubt. A real fix
// decodes string tokens with a surrogate-preserving decoder AND teaches
// encodeString to re-emit `\udXXX`, which is a rewrite of this file's
// two ends. Rejecting the document instead would be a THIRD behaviour,
// diverging from CPython in a new direction to avoid diverging in the old
// one. Written down and pinned by
// TestALoneSurrogateDivergesFromCPython so it cannot be rediscovered as
// a surprise.
func LoadsOrdered(text string) (any, error) {
	// CPython's json.loads accepts the bare tokens NaN, Infinity and
	// -Infinity by default; Go's decoder rejects them, and the rejection
	// kills the ENTIRE document rather than one field. One stray
	// non-finite float anywhere in a model reply — including in a key
	// nobody reads — turned the model's plan into the two-phase heuristic
	// on the Go side only (adversarial mission-r1 MEDIUM).
	maskedA, found := maskNonFinite(text, nonFiniteMarkerA)

	dec := json.NewDecoder(strings.NewReader(maskedA))
	dec.UseNumber()
	v, err := decodeOrdered(dec)
	if err != nil {
		return nil, err
	}
	if found {
		maskedB, _ := maskNonFinite(text, nonFiniteMarkerB)
		decB := json.NewDecoder(strings.NewReader(maskedB))
		decB.UseNumber()
		vb, errB := decodeOrdered(decB)
		if errB != nil {
			return nil, errB
		}
		v = unmaskPaired(v, vb)
	}
	// Refuse trailing content the way json.loads does — `{}{}` is an
	// error there, and accepting it would let a torn write parse.
	if _, err := dec.Token(); err == nil {
		return nil, fmt.Errorf("trailing content after JSON value")
	}
	return v, nil
}

// Plain flattens a LoadsOrdered tree into the shape `json.Unmarshal`
// into an `any` produces: Obj -> map[string]any, List -> []any,
// json.Number -> int for an integral literal, float64 otherwise. It
// exists so callers that only ever wanted a
// plain map can still route through LoadsOrdered and inherit its
// non-finite masking, WITHOUT changing the types eleven call sites
// already type-assert (adversarial mission-r4 HIGH).
//
// The number conversion deliberately keeps a `strconv.ErrRange` result
// rather than failing: `json.loads('1e309')` is `inf` in CPython, and
// Number.Float64 returns +Inf together with that error, so ignoring it
// is what matches. `json.Unmarshal` into an `any` instead REJECTS the
// whole document there — a fork this closes on the way past.
//
// It DOES restore int-vs-float, as of mission-r6: CPython's json.loads
// gives a real `int` for `1`, and so does this, up to int64. The gap was
// pinned as known for two rounds on the reasoning that it could not
// reach disk, and it stopped being inert the moment a plan check's
// description could be a number — str(42) is "42" and str(42.0) is
// "42.0", and the description rides the persisted check_results rows.
//
// (This paragraph said the opposite for one round after the code
// changed under it — r2's rule, on r6's own fix, caught by mission-r7.)
//
// The residual that IS still open: an integer past int64 falls through to
// float64, where CPython has arbitrary precision.
func Plain(v any) any {
	switch t := v.(type) {
	case Obj:
		m := make(map[string]any, len(t))
		for _, f := range t {
			m[f.Key] = Plain(f.Val)
		}
		return m
	case List:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = Plain(e)
		}
		return out
	case json.Number:
		// int for an integral literal, float otherwise — which is what
		// CPython's json.loads produces, and the reason matters: these
		// values are str()'d into persisted rows, and str(42) is "42"
		// while str(42.0) is "42.0". Plain was written to mimic Go's
		// json.Unmarshal-into-any (everything float64) and that shape was
		// pinned as a known loss for two rounds; it stopped being inert
		// the moment a check description could be a number (adversarial
		// mission-r6, found by its own new test).
		//
		// An integer too large for int64 still falls through to float64,
		// where CPython has arbitrary precision. That residual is real
		// and unfixed — named here, not silent.
		if i, err := t.Int64(); err == nil {
			return int(i)
		}
		f, _ := t.Float64()
		return f
	}
	return v
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
			obj := Obj{}
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
			list := List{}
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

// IntOf reads a JSON number as Python's int() would after a
// `d.get(k, 0)`: a missing key, a wrong type, or an unparseable literal
// all give 0, and a float literal TRUNCATES toward zero rather than
// failing, matching int(3.9) == 3.
func IntOf(v any) int {
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

// Clip is Python's s[:n] — n CODE POINTS, not n bytes. Every truncation
// in this package feeds a human-facing line, and slicing bytes both counts
// wrong and can split a rune into replacement characters.
// A NEGATIVE n counts from the end, as Python's does: "abc"[:-1] is
// "ab", not "". The first cut returned "" for every n <= 0 under a doc
// comment claiming to be s[:n] — the same overstated-helper shape that
// cost two r1 MEDIUMs and produced pySliceLen in the orch package, which
// gets it right. Two implementations of one Python operation disagreeing
// is the defect even when no live caller passes a negative (every one
// today passes a constant 200/60/40), because the doc comment is what
// the next caller reads (adversarial mission-r4 LOW).
func Clip(s string, n int) string {
	if n < 0 {
		if c := utf8.RuneCountInString(s) + n; c > 0 {
			n = c
		} else {
			return ""
		}
	}
	if n == 0 {
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

// Bool reads a JSON boolean the way `bool(d.get(k, False))` does: only a
// real `true` is true. A truthy STRING is deliberately not honoured —
// Python's manifest writer stores a real bool and the monotonicity gate
// compares `is True`, which no string satisfies either.
func Bool(v any) bool {
	b, _ := v.(bool)
	return b
}

// FloatOf is float(d.get(k, 0.0)) with the same forgiveness as IntOf.
func FloatOf(v any) float64 {
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

// NowISO is datetime.now(timezone.utc).isoformat().
//
// The one thing a naive format string gets wrong: Python OMITS the
// fractional part entirely when the microsecond is 0, where Go's
// ".000000" layout always prints six digits. It is a one-in-a-million
// case and it is the kind that shows up once, in production, as an
// unparseable timestamp in someone else's reader.
func NowISO(t time.Time) string {
	if t.Nanosecond()/1000 == 0 {
		return t.Format("2006-01-02T15:04:05-07:00")
	}
	return t.Format("2006-01-02T15:04:05.000000-07:00")
}

// Non-finite literals: CPython's json.loads accepts the bare tokens NaN,
// Infinity and -Infinity, and Go's decoder does not.
//
// The masking pass replaces those tokens — only where they appear OUTSIDE
// a string literal, so a milestone titled "NaN" is untouched — with a
// sentinel string the decoder accepts, then a walk over the decoded tree
// turns the sentinels back into json.Number values whose Float64 is the
// right non-finite. json.Number("NaN").Float64() is ParseFloat("NaN"),
// which succeeds, so Repr renders "nan"/"inf"/"-inf" the way Python does.
//
// The markers are ORDINARY JSON-safe text, and there are two of them.
//
// The first attempt used a NUL-prefixed sentinel, which Go's decoder
// rejects inside a string literal ("invalid character '\x00' in string
// literal") — so the masking never worked at all, and no test noticed
// because none covered a non-finite token. The second attempt lengthened
// a single sentinel until it did not occur in the raw text, which misses
// the spelling that matters: a string can encode the marker with \uXXXX
// escapes, so the DECODED value carries it even though the raw text does
// not.
//
// Two markers of the SAME LENGTH settle it exactly. The document is
// decoded twice, once masked with each; a string that came from the input
// decodes identically both times, and a string this package substituted
// differs in exactly the marker. So the pair of trees names the masked
// positions with no guessing and no collision, at the cost of one extra
// decode on the rare document that contains a bare non-finite token.
const (
	nonFiniteMarkerA = "__pyval_nonfiniteA__"
	nonFiniteMarkerB = "__pyval_nonfiniteB__"
)

// maskNonFinite rewrites bare non-finite tokens with marker + the token,
// leaving anything inside a string literal alone, and reports whether it
// changed anything.
func maskNonFinite(text, marker string) (masked string, found bool) {
	// Cheap reject: none of the three tokens can appear without an
	// uppercase N or I somewhere.
	if !strings.ContainsAny(text, "NI") {
		return text, false
	}

	var b strings.Builder
	b.Grow(len(text))
	inString, escaped := false, false
	for i := 0; i < len(text); {
		c := text[i]
		if inString {
			switch {
			case escaped:
				escaped = false
			case c == '\\':
				escaped = true
			case c == '"':
				inString = false
			}
			b.WriteByte(c)
			i++
			continue
		}
		if c == '"' {
			inString = true
			b.WriteByte(c)
			i++
			continue
		}
		// EQUIVALENT-MUTANT NOTE: this list used to be ordered "longest
		// first" under a comment claiming that otherwise "the leading
		// minus would be emitted and the token read as positive". That
		// is false, and the mutant which swaps the first two entries
		// survives: the scan reaches the '-' byte first, and
		// HasPrefix("-Infinity...", "Infinity") is false there, so both
		// orders match -Infinity at the same index. The order is kept
		// for readability, not for correctness.
		matched := ""
		for _, tok := range [...]string{"-Infinity", "Infinity", "NaN"} {
			if strings.HasPrefix(text[i:], tok) {
				matched = tok
				break
			}
		}
		if matched == "" {
			b.WriteByte(c)
			i++
			continue
		}
		// KEY POSITION IS NOT VALUE POSITION (adversarial mission-r2
		// LOW). In JSON's grammar a value is followed by ',', '}', ']'
		// or end of input — never by ':'. So a bare token with a colon
		// after it is standing where a property NAME must be, and CPython
		// rejects the whole document there:
		//
		//	{"milestones":[...], NaN: 1}
		//	  json.JSONDecodeError: Expecting property name enclosed in
		//	  double quotes -> extract_json returns {} -> the HEURISTIC
		//	  mission is written
		//
		// Masking it would quietly make the document parse, and
		// unmaskPaired only recurses into Obj values, never keys, so the
		// marker would survive into mission.json as a literal key. Emit
		// the token untouched and let the decoder refuse it, as CPython
		// does. The whole token, not its first byte: emitting just '-'
		// would leave "Infinity" to match on the next pass.
		if j := i + len(matched); nextNonJSONSpace(text, j) == ':' {
			b.WriteString(matched)
			i = j
			continue
		}
		b.WriteString(`"` + marker + matched + `"`)
		i += len(matched)
		found = true
	}
	return b.String(), found
}

// nextNonJSONSpace returns the first byte at or after i that is not JSON
// whitespace, or 0 at end of input. JSON's whitespace is exactly these
// four bytes (RFC 8259) — deliberately NOT Python's 29-code-point set,
// because what is being scanned here is JSON structure, not Python text.
func nextNonJSONSpace(text string, i int) byte {
	for ; i < len(text); i++ {
		switch text[i] {
		case ' ', '\t', '\n', '\r':
		default:
			return text[i]
		}
	}
	return 0
}

// unmaskPaired walks the two decodes together and turns the positions
// where they DIFFER — which are exactly the ones this package
// substituted — back into the json.Number literals CPython would have
// produced. json.Number("NaN").Float64() is ParseFloat("NaN"), which
// succeeds, so Repr renders "nan"/"inf"/"-inf" the way Python does.
func unmaskPaired(a, b any) any {
	switch ta := a.(type) {
	case string:
		tb, ok := b.(string)
		// EQUIVALENT-MUTANT NOTE: the `ta == tb` half is a fast path,
		// not a correctness requirement, and its mutant survives. A
		// string carrying BOTH markers is impossible — they differ at
		// one byte in the same position — so the marker-B check below
		// already rejects everything this line rejects. It stays because
		// it is the invariant in one line: agreement means the value
		// came from the input.
		if !ok || ta == tb {
			return a
		}
		restA, okA := strings.CutPrefix(ta, nonFiniteMarkerA)
		restB, okB := strings.CutPrefix(tb, nonFiniteMarkerB)
		if !okA || !okB || restA != restB {
			return a
		}
		// KEEP CPYTHON'S SPELLING. These literals are written back
		// VERBATIM by pyjson.Value ("keep the source literal"), so the
		// token chosen here is the token that lands in the shared file.
		//
		// This used to emit "Inf"/"-Inf", and CPython's json.loads
		// rejects those outright — not the field, the WHOLE document
		// (mission-r3 MEDIUM). Measured:
		//
		//	json.dumps({'a': float('inf')}) -> {"a": Infinity}   <- how it gets there
		//	json.loads('{"a": Inf}')        -> JSONDecodeError: Expecting value
		//
		// The reach is every read-modify-write path: MarkFeaturePassing
		// decodes all of feature_list.json, patches three fields on one
		// feature, and re-renders the whole document — so one Infinity
		// anywhere in that file, rewritten by Go, leaves the Python
		// runtime unable to parse the manifest at all.
		//
		// strconv.ParseFloat accepts "Infinity" and "-Infinity", so
		// json.Number.Float64 keeps working and nothing downstream
		// changes. NaN was never affected; only the two infinities were.
		switch restA {
		case "NaN":
			return json.Number("NaN")
		case "Infinity":
			return json.Number("Infinity")
		case "-Infinity":
			return json.Number("-Infinity")
		}
		return a
	case List:
		tb, ok := b.(List)
		if !ok || len(tb) != len(ta) {
			return a
		}
		for i := range ta {
			ta[i] = unmaskPaired(ta[i], tb[i])
		}
		return ta
	case Obj:
		tb, ok := b.(Obj)
		if !ok || len(tb) != len(ta) {
			return a
		}
		for i := range ta {
			ta[i].Val = unmaskPaired(ta[i].Val, tb[i].Val)
		}
		return ta
	}
	return a
}

// SafeFloat is llm_parse.safe_float: coerce an LLM-supplied value to a
// float, fall back to def on anything that will not convert, REFUSE
// non-finite results, then clamp.
//
// Python is four lines and every one of them matters:
//
//	if value is None: return default
//	try: result = float(value)
//	except (TypeError, ValueError): return default
//	if math.isnan(result) or math.isinf(result): return default
//	# then max(min_val, ...) / min(..., max_val)
//
// `float(value)` is a CONVERSION, not a type check — it accepts a
// numeric string ("0.9", the shape LLMs emit often enough that Python
// handles it) and a bool (True -> 1.0). The isnan/isinf line is the one
// this port kept losing.
//
// It exists because there were FOUR hand-written ports of this one
// function — closure.go, intent.go, evolver.go and the skills coercion
// — each missing a different subset of those lines, and one of them cost
// a HIGH (adversarial mission-r5). That is the same split the r4 HIGH
// was: a fix landing in one sibling and not the others. One
// implementation, four call sites.
//
// The non-finite guard is not cosmetic. Go's encoding/json REFUSES NaN
// and ±Inf, so a non-finite that reaches runs.WriteMetadata or
// evolver.SaveSuggestions does not write a wrong number — it destroys
// the ENTIRE record, while CPython's writer emits `NaN`/`Infinity` and
// the Python reader accepts them.
//
// Pass nil for min/max to skip that clamp, matching Python's Optional.
func SafeFloat(v any, def float64, min, max *float64) float64 {
	result, ok := toFloat(v)
	if !ok || math.IsNaN(result) || math.IsInf(result, 0) {
		return def
	}
	// math.Max/Min, not `<`/`>`, because Python's clamp is
	// `max(min_val, result)` / `min(max_val, result)` and those differ
	// from a comparison on SIGNED ZERO: -0.0 < 0.0 is false, so a
	// comparison keeps the negative zero, while max(0.0, -0.0) returns
	// +0.0 (the first of two equal arguments). Both writers spell the
	// difference — json.dumps gives "-0.0", FloatRepr gives "-0.0" —
	// so it reaches the shared store (adversarial mission-r6 LOW).
	if min != nil {
		result = math.Max(*min, result)
	}
	if max != nil {
		result = math.Min(*max, result)
	}
	return result
}

// SafeFloatUnit is SafeFloat clamped to [0, 1] — the confidence shape
// every call site in this port actually uses.
func SafeFloatUnit(v any, def float64) float64 {
	lo, hi := 0.0, 1.0
	return SafeFloat(v, def, &lo, &hi)
}

// toFloat is Python's `float(value)` over the types a decoded JSON
// document can hold. nil is Python's None (the early return), and a
// string goes through ParseFloat because float("0.9") does.
//
// ParseFloat accepts spellings Python's float() also accepts —
// "inf", "infinity", "nan", case-insensitively, with an optional sign —
// and every one of them is then refused by the non-finite guard in
// SafeFloat, exactly as Python refuses them one line later.
//
// Measured on this box, both runtimes accept PEP 515 underscores in a
// decimal literal: ParseFloat("1_000") is 1000 with a nil error and
// float("1_000") is 1000.0. (An earlier version of this comment claimed
// both REFUSED it, on the strength of the Go docs' base-prefix wording.
// Both halves were false and the outcome agreed anyway — a comment that
// states a measurement, adversarial mission-r6 LOW.)
func toFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case nil:
		return 0, false
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case uint64:
		return float64(t), true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case json.Number:
		f, err := t.Float64()
		// Float64 returns ±Inf together with ErrRange on overflow, and
		// Python's float("1e309") is inf too — so the value is right and
		// SafeFloat's guard is what rejects it, on both sides.
		if err != nil && !math.IsInf(f, 0) {
			return 0, false
		}
		return f, true
	case string:
		return ParseFloat(t)
	}
	return 0, false
}

// Round is Python's round(f, n) for a float — half-to-even on the EXACT
// value of the double, which is what CPython's _Py_dg_dtoa does and what
// no arithmetic spelling reproduces.
//
// The two wrong spellings both shipped in this port and both wrote to
// files the two runtimes share:
//
//	math.RoundToEven(f*1e4)/1e4      scans.go, under a comment claiming
//	                                 it matched round(); 682 divergences
//	                                 over round4(done/total) for every
//	                                 total <= 2000, e.g. 1/160 -> 0.0063
//	                                 in CPython and 0.0062 here
//	float64(int64(f*1000+0.5))/1000  inspector.go; half-UP, not even, so
//	                                 round(0.6675,3) is 0.667 there and
//	                                 0.668 here
//
// Scaling by 10^n carries the scaled value's own representation error
// into the decision, which is why RoundToEven-after-multiply is not
// round(). Formatting to n decimals and parsing back is: strconv's
// FormatFloat rounds the decimal expansion of the exact double, the same
// thing CPython does (adversarial mission-r6 MEDIUM).
//
// NaN and ±Inf are returned unchanged, matching round()'s behaviour of
// leaving them alone rather than raising.
func Round(f float64, n int) float64 {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return f
	}
	if n < 0 {
		// FormatFloat reads EVERY negative precision as "shortest", so the
		// round silently became a no-op: round(1234.5678, -2) is 1200.0 in
		// CPython and was 1234.5678 here. No call site passes a negative n
		// today, which is exactly why this needed fixing rather than
		// documenting — the doc says "Python's round(f, n)" with no domain,
		// and the next caller believes the doc (adversarial mission-r7
		// MEDIUM, latent).
		//
		// Python rounds to a multiple of 10**-n, half-to-even, on the
		// exact value. Scaling down, rounding to 0 decimals through the
		// same format-and-reparse, and scaling back up is that.
		scale := math.Pow(10, float64(-n))
		scaled, err := strconv.ParseFloat(
			strconv.FormatFloat(f/scale, 'f', 0, 64), 64)
		if err != nil {
			return f
		}
		return scaled * scale
	}
	out, err := strconv.ParseFloat(strconv.FormatFloat(f, 'f', n, 64), 64)
	if err != nil {
		return f
	}
	return out
}

// ParseFloat is CPython's float(str), and it is the ONE implementation of
// it in this port. Three differences from strconv.ParseFloat, each
// measured on this box and each previously hand-carried by a different
// subset of the four call sites that needed all three:
//
//	float()'s whitespace set is str.strip()'s MINUS U+001C..U+001F, so
//	  " 0.9" strips and "\x1c0.9" is a ValueError    (FloatStrip)
//	float() takes any Unicode decimal digit: float("٠.٥") is 0.5
//	  where ParseFloat is ASCII-only                 (FoldDecimals)
//	ParseFloat accepts HEX float literals and float() raises:
//	  float("0x1p-2") is a ValueError, ParseFloat gives 0.25
//
// It exists because those three lived in three places with three
// different subsets — record/verdict.go had the hex rejection and its
// own copy of FoldDecimals, pyval had the strip and the fold, and
// knowledge/pack/skills had none of them. That is the hand-ported-helper
// family, and consolidating one is only half the job if the SURVIVING
// copy is not the one that knew the most (adversarial mission-r7
// MEDIUM).
//
// The ±Inf-on-ErrRange arm is deliberate: json.loads('1e309') and
// float("1e309") are both inf in CPython, and ParseFloat reports the
// overflow as an error WITH the value, so ignoring the error is what
// matches. Callers that must refuse the non-finite (SafeFloat does)
// check after, where Python's own guard is.
func ParseFloat(s string) (float64, bool) {
	stripped := pytext.FoldDecimals(pytext.FloatStrip(s))
	if low := strings.TrimLeft(strings.ToLower(stripped), "+-"); strings.HasPrefix(low, "0x") {
		return 0, false
	}
	f, err := strconv.ParseFloat(stripped, 64)
	if err != nil {
		if math.IsInf(f, 0) {
			return f, true
		}
		return 0, false
	}
	return f, true
}
