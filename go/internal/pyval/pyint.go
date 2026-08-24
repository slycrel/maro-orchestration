package pyval

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// PyErr is a CPython exception with its CLASS, not just its message.
//
// The class is load-bearing wherever Python catches a NARROW tuple, and
// this port has one such site that decides whether an entire escalation is
// written or nothing is:
//
//	try:
//	    confidence = int(data.get("confidence", 5))
//	except (TypeError, ValueError):
//	    confidence = 5
//
// `int(nan)` raises ValueError and is caught — confidence becomes 5 and the
// escalation completes. `int(inf)` raises **OverflowError**, which is NOT in
// that tuple: it propagates out of handle_escalation entirely, so the
// calibration row, the artifact, the stop-verdict stamp and every branch
// below it never happen, and the drain fails the task instead of completing
// it. Both values are reachable from one LLM reply — `json.loads` admits the
// bare tokens `NaN` and `Infinity`, and any float literal past 1e308 decodes
// to inf as well.
//
// A helper that answered only "did it work" collapses those two into one,
// which is how the port answered `surface` with a full artifact set where
// CPython wrote nothing at all (adversarial r11 round 3, HIGH).
type PyErr struct {
	Class string // "TypeError" | "ValueError" | "OverflowError"
	Msg   string
}

// Error is Python's `str(e)`, which is the MESSAGE alone — not
// "TypeError: ...". The class lives in the field, and the difference is
// observable: every `except Exception as exc: log(..., exc)` in the ported
// source renders `%s` of the exception, so an Error() that prepended the
// class would write a log line CPython never writes. `repr(e)` is the
// spelling that carries the class, and nothing here needs it yet.
func (e *PyErr) Error() string { return e.Msg }

// Is lets errors.Is match on class alone, so a caller writing Python's
// `except (TypeError, ValueError)` can say exactly that.
func (e *PyErr) Is(target error) bool {
	t, ok := target.(*PyErr)
	return ok && t.Msg == "" && t.Class == e.Class
}

// The three classes as sentinels, for errors.Is.
var (
	TypeError     = &PyErr{Class: "TypeError"}
	ValueError    = &PyErr{Class: "ValueError"}
	OverflowError = &PyErr{Class: "OverflowError"}
)

// ErrIntTooLarge is NOT a Python exception. CPython's int is arbitrary
// precision, so `int(1e19)` is 10000000000000000000 and nothing raises;
// Go's int cannot hold it, and `int(f)` for a float past the int64 range is
// implementation-defined — on amd64 it yields MinInt64, which is how a
// magnitude of 1e19 became a durable `"depth": -9223372036854775808`.
//
// Callers must decide what to do with it, and the two in this port decide
// differently on purpose:
//
//   - a value that is immediately clamped (`max(1, min(10, confidence))`)
//     may SATURATE, which is exactly equivalent to arbitrary precision
//     there — the clamp erases the difference, provably.
//   - a value that is WRITTEN (the reopen payload's depth) may not. It
//     refuses and skips the write rather than emitting a number CPython
//     would never produce.
var ErrIntTooLarge = errors.New("pyval: integer past the int64 range")

// Int is CPython's int(v), with the exception CLASS when it raises.
//
// Truncation is toward zero for floats (int(2.9) == 2, int(-2.9) == -2),
// which is Go's float→int conversion, and a bool is an int (True is 1).
// A string is stripped and parsed in base 10, refusing the spellings
// CPython refuses.
func Int(v any) (int, error) {
	switch t := v.(type) {
	case nil:
		return 0, intTypeErr(v)
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case int:
		return t, nil
	case int64:
		return int(t), nil
	case float64:
		return intFromFloat(t)
	case json.Number:
		// An INTEGER literal decodes to a Python int of arbitrary
		// precision, so Int64 first: it is exact where it succeeds and it
		// keeps `3` from taking the float path at all. Only when the
		// literal is a float — or an integer past int64 — does the float
		// rule apply, and it is the same rule the float64 arm uses.
		if i, err := t.Int64(); err == nil {
			return int(i), nil
		}
		f, err := t.Float64()
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			// Not a number at all. json.Number holds whatever text the
			// decoder accepted, and this port's masking pass admits the
			// bare NaN/Infinity tokens CPython admits — those DO parse,
			// so reaching here means something else.
			return 0, &PyErr{Class: "ValueError",
				Msg: fmt.Sprintf("invalid literal for int() with base 10: %s",
					Repr(t.String()))}
		}
		// ErrRange already produced the correctly-signed ±Inf, which is
		// what CPython's float() gives for 1e400 — and int(inf) is an
		// OverflowError, which intFromFloat says.
		return intFromFloat(f)
	case string:
		return intFromString(t)
	}
	return 0, intTypeErr(v)
}

func intFromFloat(f float64) (int, error) {
	if math.IsNaN(f) {
		return 0, &PyErr{Class: "ValueError",
			Msg: "cannot convert float NaN to integer"}
	}
	if math.IsInf(f, 0) {
		return 0, &PyErr{Class: "OverflowError",
			Msg: "cannot convert float infinity to integer"}
	}
	// float64(MinInt64) is exactly -2^63 and IS representable; float64 has
	// no exact 2^63-1, so the positive bound is the first unrepresentable
	// value rather than MaxInt64. Measured against CPython: int(-(2.0**63))
	// is -9223372036854775808 and int(2.0**63) is 9223372036854775808,
	// which is one past what a Go int holds.
	if f >= 9223372036854775808.0 || f < -9223372036854775808.0 {
		return 0, ErrIntTooLarge
	}
	return int(f), nil
}

// intFromString is int(s) for a str: surrounding whitespace is stripped
// with Python's own rule (str.strip removes more than Go's TrimSpace), an
// optional sign is allowed, PEP 515 underscores may separate digits — and a
// decimal point or an exponent is a ValueError, unlike float(). A model
// that answers "7" is read as 7; one that answers "7.5" or "high" is not
// read at all.
//
// Named residual: CPython also accepts non-ASCII decimal digits, so
// int("\uff17") is 7 there and a ValueError here.
func intFromString(s string) (int, error) {
	bad := func() (int, error) {
		return 0, &PyErr{Class: "ValueError",
			Msg: "invalid literal for int() with base 10: " + Repr(s)}
	}
	t := pytext.Strip(s)
	if t == "" {
		return bad()
	}
	i := 0
	neg := false
	if t[0] == '+' || t[0] == '-' {
		neg = t[0] == '-'
		i++
	}
	digits := 0
	n := 0
	prevUnderscore := false
	for ; i < len(t); i++ {
		c := t[i]
		switch {
		case c == '_':
			// An underscore must sit BETWEEN digits: leading, trailing and
			// doubled underscores are all ValueError in CPython.
			if digits == 0 || prevUnderscore {
				return bad()
			}
			prevUnderscore = true
		case c >= '0' && c <= '9':
			prevUnderscore = false
			digits++
			if n > (math.MaxInt64-9)/10 {
				// CPython keeps going with an arbitrary-precision int.
				return 0, ErrIntTooLarge
			}
			n = n*10 + int(c-'0')
		default:
			return bad()
		}
	}
	if digits == 0 || prevUnderscore {
		return bad()
	}
	if neg {
		return -n, nil
	}
	return n, nil
}

func intTypeErr(v any) error {
	return &PyErr{Class: "TypeError",
		Msg: "int() argument must be a string, a bytes-like object or a " +
			"real number, not " + Repr(TypeName(v))}
}

// IntCaught is Python's
//
//	try:  n = int(v)
//	except (TypeError, ValueError):  n = def
//
// — the NARROW catch. It returns the default for those two classes and
// reports `raised` for anything else, which the caller must let propagate.
// ErrIntTooLarge is neither: it is this port's inability to hold the value,
// and it comes back as `raised` so no caller silently defaults on it.
func IntCaught(v any, def int) (n int, raised error) {
	got, err := Int(v)
	if err == nil {
		return got, nil
	}
	if CaughtBy(err, "TypeError", "ValueError") {
		return def, nil
	}
	return def, err
}

// CaughtBy is the Go spelling of an `except (A, B)` tuple: it reports
// whether err is one of the named CPython exception classes.
//
// It exists so a caller can write the tuple its Python writes instead of
// re-deriving which classes a helper decided were "expected". A helper that
// swallows the tuple ITSELF cannot express the sites where the same call
// appears under a different tuple — and this file has three such sites with
// two different tuples between them.
func CaughtBy(err error, classes ...string) bool {
	for _, c := range classes {
		if errors.Is(err, &PyErr{Class: c}) {
			return true
		}
	}
	return false
}

// IntClamped is Int for a value whose very next operation is a clamp into
// [lo, hi]. Saturating on ErrIntTooLarge is not an approximation there: a
// CPython arbitrary-precision int past the int64 range clamps to exactly
// the same bound.
func IntClamped(v any, lo, hi int) (int, error) {
	got, err := Int(v)
	if errors.Is(err, ErrIntTooLarge) {
		// The sign is the only thing that survives the clamp, and Int
		// discarded it — recover it from the value itself.
		if f, ok := Float(v); ok && f < 0 {
			return lo, nil
		}
		return hi, nil
	}
	if err != nil {
		return 0, err
	}
	if got < lo {
		return lo, nil
	}
	if got > hi {
		return hi, nil
	}
	return got, nil
}

// SliceHead is Python's `v[:n]` on a RAW value, and the second return is
// whether the slice would have RAISED.
//
// It is not a string helper. A str slices to a str and a LIST slices to a
// list — Python does not care which, and the difference reaches a durable
// store: `write_event(goal=task.get("reason", "")[:80])` writes a JSON
// array when the queue row's reason is one. A helper that answered only for
// strings dropped the whole event row instead (adversarial r11 round 3).
//
// The three raising shapes are all reachable from a task file, and they do
// not raise the same way: an int, a float, a bool and None give TypeError
// ('int' object is not subscriptable), while a DICT gives KeyError
// (slice(None, 80, None)) — a dict lookup with a slice for a key. Every
// call site swallows both, so only the fact of the raise is reproduced.
func SliceHead(v any, n int) (any, bool) {
	switch t := v.(type) {
	case string:
		return Clip(t, n), true
	case List:
		return t[:sliceBound(len(t), n)], true
	case []any:
		return List(t)[:sliceBound(len(t), n)], true
	case []string:
		out := List{}
		for _, e := range t[:sliceBound(len(t), n)] {
			out = append(out, e)
		}
		return out, true
	}
	return nil, false
}

func sliceBound(length, n int) int {
	if n < 0 {
		if length+n < 0 {
			return 0
		}
		return length + n
	}
	if n > length {
		return length
	}
	return n
}
