package preflight

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/pyos"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// StatsDeps is the one thing CalibrationStats reaches outside itself.
type StatsDeps struct {
	// MemoryDir is `from orch_items import memory_dir` FOLLOWED BY
	// `memory_dir()`. They are folded into one call because both sit
	// inside the same try and produce the same answer — the function
	// cannot tell a missing module from a raising call, and neither can
	// its caller.
	MemoryDir func() (string, error)
}

// ScopeBucket is one entry of `scope_breakdown`.
//
// Scope is `any`, not string, because the key comes from a VALUE in the
// reviewer's calibration record rather than from a JSON object key:
// `e.get("scope_predicted", "unknown")` hands back whatever was written,
// and a numeric or boolean scope becomes a numeric or boolean dict key in
// Python without complaint.
type ScopeBucket struct {
	Scope any
	// The three counters are Python's `{"count": 0, "stuck": 0, "done": 0}`
	// and their DECLARATION ORDER is that dict's key order. Nothing in
	// this package serialises a bucket, so the order is carried by
	// whatever renders one — currently only the differential.
	Count, Stuck, Done int
}

// ScopeBreakdown is the insertion-ordered dict Python builds with
// setdefault. First arrival fixes both the position and the displayed key.
//
// A non-string key is kept AS the value it was, not coerced: json.dumps
// would spell an int key "1", but the only caller that serialises this
// dict is `_preflight_stats_main --json`, which is not ported. Coercing
// here would be the port answering a question nothing asks.
type ScopeBreakdown []ScopeBucket

// dictKey is Python's hashing of a scope value into a dict slot.
//
// It answers two questions the loop needs: whether the value can be a key
// at all (a list or a dict cannot — `setdefault` raises TypeError:
// unhashable type), and which values SHARE a slot. `1`, `1.0` and `True`
// are one key in Python because they compare equal and hash equal, so
// three records spelling the scope three ways land in one bucket, under
// whichever spelling arrived first.
func dictKey(v any) (string, bool) {
	switch t := v.(type) {
	case string:
		return "s:" + t, true
	case nil:
		return "n:", true
	case bool:
		if t {
			return "f:1", true
		}
		return "f:0", true
	// There is no `int` or `float64` arm: every value reaching here came
	// out of pyval.LoadsOrdered, which types every JSON number as a
	// json.Number. Arms for the Go-native spellings would be code no
	// input can run, and unreachable code is where a wrong answer hides.
	case json.Number:
		f, err := t.Float64()
		if err != nil {
			return "s:" + t.String(), true
		}
		return "f:" + fmt.Sprint(f), true
	}
	return "", false
}

// CalibrationStats is `preflight_calibration_stats`: read
// memory/preflight_calibration.jsonl and return accuracy metrics.
//
// calPath is Python's `cal_path` parameter and takes Python's values: nil
// for None, a string for a path. Anything else is `Path(123)` — a
// TypeError, returned as an error rather than a dict, because that is
// what the caller sees.
//
// Two failure modes are DIFFERENT here and it matters. A file that cannot
// be located answers with a dict — {"error": ...} — and a file that
// cannot be READ raises. The function guards its json.loads per line and
// nothing else: a record that is valid JSON but is not an object reaches
// `e.get` and raises AttributeError from inside the sum, and that
// exception leaves the function.
func CalibrationStats(calPath any, d StatsDeps) (pyval.Obj, error) {
	var path string
	switch t := calPath.(type) {
	case nil:
		dir, err := d.MemoryDir()
		if err != nil {
			// `except Exception` around both the import and the call.
			return pyval.Obj{{Key: "error", Val: errNoMemoryDir.Error()}}, nil
		}
		path = strings.TrimSuffix(dir, "/") + "/preflight_calibration.jsonl"
	case string:
		// `Path("")` is `PosixPath('.')`, which exists and then fails to
		// read as a directory. An empty string is not "no path given".
		path = t
		if path == "" {
			path = "."
		}
	default:
		// `Path(123)`. The message names the class it was handed, and
		// this port's `any` parameter is the only reason a caller can
		// still get here — Python's signature does not stop it either.
		return nil, &pyval.PyErr{Class: "TypeError", Msg: fmt.Sprintf(
			"argument should be a str or an os.PathLike object where "+
				"__fspath__ returns a str, not <class '%s'>",
			pyval.TypeName(calPath))}
	}

	if _, err := os.Stat(path); err != nil {
		// Path.exists() answers False for anything that fails to stat,
		// including a broken symlink and a permission-denied parent.
		return pyval.Obj{{Key: "total", Val: 0},
			{Key: "note", Val: "no calibration data yet"}}, nil
	}

	raw, err := os.ReadFile(path)
	if err != nil {
		// read_text() is unguarded: a directory here is an
		// IsADirectoryError that leaves the function.
		return nil, &pyval.PyErr{Class: pyos.ErrorClass(err),
			Msg: pyos.ErrorText(path, err)}
	}
	if err := decodeError(raw); err != nil {
		// read_text() DECODES; on this box the preferred encoding is
		// UTF-8, so undecodable bytes raise rather than round-trip.
		return nil, err
	}

	entries := []any{}
	for _, line := range pytext.SplitLines(string(raw)) {
		line = pytext.Strip(line)
		if line == "" {
			continue
		}
		v, err := pyval.LoadsOrdered(line)
		if err != nil {
			// `except Exception: pass` — a garbled line is dropped
			// silently and does not reduce the denominator, because the
			// denominator is len(entries).
			continue
		}
		entries = append(entries, v)
	}

	if len(entries) == 0 {
		return pyval.Obj{{Key: "total", Val: 0},
			{Key: "note", Val: "file exists but no valid entries"}}, nil
	}

	// Four separate passes in the original, so the FIRST non-object entry
	// raises on "true_positive" no matter which counter would have wanted
	// it. The order of the exception is part of the observable behaviour.
	tp, err := countTruthy(entries, "true_positive")
	if err != nil {
		return nil, err
	}
	fp, err := countTruthy(entries, "false_positive")
	if err != nil {
		return nil, err
	}
	fn, err := countTruthy(entries, "false_negative")
	if err != nil {
		return nil, err
	}
	tn, err := countTruthy(entries, "true_negative")
	if err != nil {
		return nil, err
	}

	// None, not 0, when the denominator is empty: "no data" and "never
	// right" are different answers and the CLI prints "n/a" for one.
	var precision, recall any
	if tp+fp > 0 {
		precision = pyval.Round(float64(tp)/float64(tp+fp), 3)
	}
	if tp+fn > 0 {
		recall = pyval.Round(float64(tp)/float64(tp+fn), 3)
	}

	breakdown := ScopeBreakdown{}
	index := map[string]int{}
	for _, e := range entries {
		o, ok := e.(pyval.Obj)
		if !ok {
			return nil, attrErr(e, "get")
		}
		sc, found := o.Get("scope_predicted")
		if !found {
			sc = "unknown"
		}
		key, hashable := dictKey(sc)
		if !hashable {
			// CPython 3.14's wording, which wraps the classic
			// "unhashable type" in the operation that hit it. Earlier
			// interpreters say only the parenthesised half; the class is
			// the same and it is the class every caller branches on.
			return nil, &pyval.PyErr{Class: "TypeError",
				Msg: fmt.Sprintf(
					"cannot use '%s' as a dict key (unhashable type: '%s')",
					pyval.TypeName(sc), pyval.TypeName(sc))}
		}
		pos, seen := index[key]
		if !seen {
			breakdown = append(breakdown, ScopeBucket{Scope: sc})
			pos = len(breakdown) - 1
			index[key] = pos
		}
		breakdown[pos].Count++
		status, _ := o.Get("actual_status")
		if pyval.Eq(status, "stuck") {
			breakdown[pos].Stuck++
		} else {
			// Everything that is not the string "stuck" counts as done,
			// including a missing key and a still-running loop.
			breakdown[pos].Done++
		}
	}

	return pyval.Obj{
		{Key: "total", Val: len(entries)},
		{Key: "true_positive", Val: tp},
		{Key: "false_positive", Val: fp},
		{Key: "false_negative", Val: fn},
		{Key: "true_negative", Val: tn},
		{Key: "precision", Val: precision},
		{Key: "recall", Val: recall},
		{Key: "scope_breakdown", Val: breakdown},
	}, nil
}

// countTruthy is one of the four `sum(1 for e in entries if e.get(k))`
// generators. The test is TRUTHINESS, so a false_positive of 0, "" or an
// empty list does not count, and only a mapping survives the `.get`.
func countTruthy(entries []any, key string) (int, error) {
	n := 0
	for _, e := range entries {
		o, ok := e.(pyval.Obj)
		if !ok {
			// EQUIVALENT MUTANT (kept, marked `equivalent`): skipping
			// here instead of raising changes no output, because the
			// breakdown loop is a second guard over the same entries and
			// raises the same AttributeError a few lines later. The
			// raise belongs here anyway — it is where CPython's raise
			// is, and the second guard is not guaranteed to stay.
			return 0, attrErr(e, "get")
		}
		v, _ := o.Get(key)
		if pyval.Truthy(v) {
			n++
		}
	}
	return n, nil
}

// decodeError is UnicodeDecodeError for `Path.read_text()`.
//
// Go reads bytes and CPython decodes them, so a calibration file with a
// stray byte is processed happily on one side and raises on the other —
// a divergence with no symptom until the day it matters. The message is
// CPython's, which reports the START of a bad sequence rather than the
// byte that actually failed: b"\xe2(" names 0xe2 at position 0, not the
// parenthesis.
//
// Kept local, not shared: this is the FIRST site in the port that
// decodes a whole file's bytes as text. The second one is where it moves
// to pytext, per the lens-closing rule.
func decodeError(raw []byte) error {
	for i := 0; i < len(raw); {
		b := raw[i]
		if b < 0x80 {
			i++
			continue
		}
		n := seqLen(b)
		if n == 0 {
			return &pyval.PyErr{Class: "UnicodeDecodeError", Msg: fmt.Sprintf(
				"'utf-8' codec can't decode byte 0x%02x in position %d: "+
					"invalid start byte", b, i)}
		}
		if i+n > len(raw) {
			// A truncated sequence at the very end is a different
			// message from a wrong byte in the middle.
			if allContinuation(raw[i+1:]) {
				return &pyval.PyErr{Class: "UnicodeDecodeError", Msg: fmt.Sprintf(
					"'utf-8' codec can't decode bytes in position %d-%d: "+
						"unexpected end of data", i, len(raw)-1)}
			}
		}
		r, size := utf8.DecodeRune(raw[i:])
		if r == utf8.RuneError && size <= 1 {
			return &pyval.PyErr{Class: "UnicodeDecodeError", Msg: fmt.Sprintf(
				"'utf-8' codec can't decode byte 0x%02x in position %d: "+
					"invalid continuation byte", b, i)}
		}
		i += size
	}
	return nil
}

// seqLen is the length UTF-8 promises for a leading byte, or 0 if the
// byte cannot lead a sequence at all. 0xC0 and 0xC1 could only ever
// encode an overlong ASCII character, and 0xF5..0xFF are past the last
// code point, so CPython rejects all of them as start bytes.
func seqLen(b byte) int {
	switch {
	case b >= 0xC2 && b <= 0xDF:
		return 2
	case b >= 0xE0 && b <= 0xEF:
		return 3
	case b >= 0xF0 && b <= 0xF4:
		return 4
	}
	return 0
}

func allContinuation(bs []byte) bool {
	for _, b := range bs {
		if b < 0x80 || b >= 0xC0 {
			return false
		}
	}
	return true
}
