package pyval

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

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
	tooLarge := false
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
			if n <= (math.MaxInt64-9)/10 {
				n = n*10 + int(c-'0')
			} else {
				// Past int64. CPython keeps going with an arbitrary-
				// precision int, so the loop keeps COUNTING — the digit
				// limit is checked first, and only a value inside it
				// becomes ErrIntTooLarge.
				tooLarge = true
			}
		default:
			return bad()
		}
	}
	if digits == 0 || prevUnderscore {
		return bad()
	}
	// CPython caps int()'s STRING conversion at 4300 digits and raises a
	// ValueError past it — which the escalation's `except (TypeError,
	// ValueError)` CATCHES, landing on the default. Answering
	// ErrIntTooLarge instead would propagate where CPython quietly
	// defaults. The count is of DIGITS: underscores and the sign do not
	// count toward it (measured: `"_".join(["1"*1000]*5)` reports 5000).
	if digits > intMaxStrDigits {
		return 0, &PyErr{Class: "ValueError", Msg: fmt.Sprintf(
			"Exceeds the limit (%d digits) for integer string conversion: "+
				"value has %d digits; use sys.set_int_max_str_digits() to "+
				"increase the limit", intMaxStrDigits, digits)}
	}
	if tooLarge {
		return 0, ErrIntTooLarge
	}
	if neg {
		return -n, nil
	}
	return n, nil
}

// intMaxStrDigits is CPython's sys.get_int_max_str_digits() default.
const intMaxStrDigits = 4300

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
	got := ClassOf(err)
	if got == "" {
		return false
	}
	for _, c := range classes {
		if got == c {
			return true
		}
	}
	return false
}

// PyClasser is a Go-typed error that knows which Python exception class it
// stands in for.
//
// Not every ported raise wants to become a *PyErr: task_store's
// ConflictError and CycleError are real Go types with real Go call sites,
// and collapsing them into a string-classed struct would lose the
// `errors.As` those call sites are written against. But the CLASS is still
// part of the behaviour — a differential that cannot ask "what would
// CPython have raised" can only compare messages, and two exceptions with
// the same message are still caught by different excepts.
type PyClasser interface{ PyClass() string }

// ClassOf answers the CPython exception class an error stands for, or ""
// for an error that carries no such claim (a plain Go os.PathError, say).
func ClassOf(err error) string {
	var pe *PyErr
	if errors.As(err, &pe) {
		return pe.Class
	}
	var pc PyClasser
	if errors.As(err, &pc) {
		return pc.PyClass()
	}
	return ""
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
// the exception it would have RAISED — nil when the slice succeeds.
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
// (slice(None, 80, None)) — a dict lookup with a slice for a key.
//
// Most call sites swallow the raise and only its FACT is observable — but
// one LOGS the exception, so the message is carried rather than invented:
// `log.warning("escalation %s: failed to write summary: %s", action, exc)`
// prints what CPython's slice said, and a port that printed its own
// sentence there forks the line an operator greps side by side
// (adversarial r11 round 4, LOW).
func SliceHead(v any, n int) (any, error) {
	switch t := v.(type) {
	case string:
		return Clip(t, n), nil
	case List:
		return t[:sliceBound(len(t), n)], nil
	case []any:
		return List(t)[:sliceBound(len(t), n)], nil
	case []string:
		out := List{}
		for _, e := range t[:sliceBound(len(t), n)] {
			out = append(out, e)
		}
		return out, nil
	case Obj, map[string]any:
		// A dict LOOKUP with a slice for a key, so the message is the
		// key's repr — and repr(slice(None, 80, None)) spells the slice
		// out rather than naming the number alone.
		return nil, &PyErr{Class: "KeyError",
			Msg: fmt.Sprintf("slice(None, %d, None)", n)}
	}
	return nil, &PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("'%s' object is not subscriptable", TypeName(v))}
}

// PercentD is Python's `"%d" % v` as a LOGGING argument, and the second
// return is whether the record survives.
//
// `log.info("… depth=%d", depth)` is not an f-string, and the difference is
// not cosmetic. `%d` of 2.0 is "2" where str() says "2.0", and `%d` of a
// str, a None or a dict raises inside logging's own formatter — which
// catches it, prints "--- Logging error ---" to stderr, and emits NO
// RECORD. So a false second return means the whole log call is skipped,
// not that some placeholder is written (adversarial r11 round 4, MEDIUM:
// every escalation's FIRST line diverged for a float depth, which is what
// any foreign JSON writer produces).
//
// NaN and infinity fail the same way, through int()'s own ValueError and
// OverflowError, and are equally recordless.
func PercentD(v any) (string, bool) {
	switch t := v.(type) {
	case bool:
		if t {
			return "1", true
		}
		return "0", true
	case int:
		return strconv.Itoa(t), true
	case int64:
		return strconv.FormatInt(t, 10), true
	case float64:
		return percentDFloat(t)
	case json.Number:
		// An INTEGER literal is exact at any width — %d of a Python int is
		// the int, however many digits it has — so the literal text is the
		// answer and the float path never sees it.
		if lit, ok := IntLiteral(t.String()); ok {
			return lit, true
		}
		f, err := t.Float64()
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return "", false
		}
		return percentDFloat(f)
	}
	return "", false
}

func percentDFloat(f float64) (string, bool) {
	if math.IsNaN(f) || math.IsInf(f, 0) {
		return "", false
	}
	out := strconv.FormatFloat(math.Trunc(f), 'f', 0, 64)
	if out == "-0" {
		// int(-0.5) is 0; Python has no negative zero integer.
		return "0", true
	}
	return out, true
}

// IntLiteral reports whether a decoded number was written as an integer,
// and returns it the way Python's int would print it.
//
// Exported because the distinction outlives %d: a json.Number too wide for
// int64 is either a Python int (arbitrary precision, and every integer
// operation on it still works) or a float64 that overflowed — and callers
// that guess get the wrong exception. task_store's pid read is the second
// one of those.
func IntLiteral(s string) (string, bool) {
	if s == "" {
		return "", false
	}
	body := s
	neg := false
	if body[0] == '-' {
		neg, body = true, body[1:]
	}
	if body == "" {
		return "", false
	}
	for i := 0; i < len(body); i++ {
		if body[i] < '0' || body[i] > '9' {
			return "", false
		}
	}
	if strings.Trim(body, "0") == "" {
		return "0", true // -0 and 0000 are both the integer 0
	}
	if neg {
		return "-" + body, true
	}
	return body, true
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
