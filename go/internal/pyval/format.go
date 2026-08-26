package pyval

import (
	"fmt"
	"math"
	"strings"
)

// groupDigits inserts Python's thousands separators into a run of DECIMAL
// DIGITS, which is the half of `{:,}` that has nothing to do with the type
// being formatted.
//
// It takes a string rather than a number on purpose. Grouped's int64 is
// wide enough for every integer a Python int can reach in practice, but
// `{:,.0f}` of 1e308 renders a 309-digit expansion and groups every one of
// them — no fixed-width integer can carry that, and truncating it would be
// a silent wrong answer in an operator-facing line.
//
// Python groups from the RIGHT, so the leading group is 1-3 digits.
func groupDigits(s string) string {
	if len(s) <= 3 {
		return s
	}
	out := make([]byte, 0, len(s)+len(s)/3)
	lead := len(s) % 3
	if lead == 0 {
		lead = 3
	}
	out = append(out, s[:lead]...)
	for i := lead; i < len(s); i += 3 {
		out = append(out, ',')
		out = append(out, s[i:i+3]...)
	}
	return string(out)
}

// splitSigned peels a leading "-" off, because Python never groups the sign
// with the digits: -1234 is "-1,234", never "-,1234".
func splitSigned(s string) (sign, rest string) {
	if strings.HasPrefix(s, "-") {
		return "-", s[1:]
	}
	return "", s
}

// GroupedF is Python's `{:,.<prec>f}` for a float.
//
// Three behaviours that a `fmt.Sprintf("%,.0f")` cannot express (Go has no
// grouping verb at all) and that a hand-rolled version gets wrong:
//
//  1. Only the INTEGER part is grouped. `{:,.3f}` of 1234567.891 is
//     "1,234,567.891" — the fraction keeps its digits ungrouped.
//  2. Rounding is round-half-to-EVEN on the exact double, not
//     half-away-from-zero: 0.5 renders "0", 1.5 renders "2", 2.5 renders
//     "2" and 1234.5 renders "1,234". Go's strconv agrees, which is why
//     the rounding is delegated rather than spelled.
//  3. NEGATIVE ZERO SURVIVES. `{:,.0f}` of -0.5 is "-0", not "0" — the
//     rounding produces -0.0 and the sign is printed. A port that
//     normalised it would differ on every rate that rounds down from
//     below zero.
//
// Non-finites go through PercentF, so they render Python's lowercase
// "inf" / "-inf" / "nan" rather than Go's "+Inf" / "-Inf" / "NaN".
func GroupedF(f float64, prec int) string {
	s := PercentF(f, prec)
	// PercentF has already spelled the non-finites Python's way, and there
	// are no digits in them to group.
	if s == "inf" || s == "-inf" || s == "nan" {
		return s
	}
	sign, rest := splitSigned(s)
	intPart, frac := rest, ""
	if i := strings.IndexByte(rest, '.'); i >= 0 {
		intPart, frac = rest[:i], rest[i:]
	}
	return sign + groupDigits(intPart) + frac
}

// PercentFmt is Python's `{:.<prec>%}` for a float: multiply by 100, format
// at `prec` decimals, append a literal percent sign.
//
// The multiplication happens on the DOUBLE and before the rounding, which
// is the whole reason this cannot be `PercentF(f, prec) `-with-a-sign-glued
// -on: `{:.0%}` of 0.125 is "12%" and of 0.375 is "38%", because 12.5 and
// 37.5 are exact halves and round to even in opposite directions. Doing the
// rounding first would answer "13%" and "38%".
//
// The percent sign is appended to NON-FINITES too — Python renders
// `{:.0%}` of infinity as "inf%", not "inf" — which looks like a bug and is
// the measured behaviour. A rate computed as a quotient reaches it whenever
// its denominator is zero, and the lens surfaces that string to an
// operator, so the port renders what CPython renders.
func PercentFmt(f float64, prec int) string {
	return PercentF(f*100, prec) + "%"
}

// FloorDiv is Python's `//` for two ints.
//
// Go's `/` TRUNCATES toward zero and Python's floors toward negative
// infinity, so the two agree on every non-negative pair and part company on
// the first negative one: `-1 // 1000` is -1 in Python and 0 in Go, and
// `-1500 // 1000` is -2 against -1. Every call site in the lenses divides a
// millisecond count that a foreign writer could have stamped negative, so
// this is reachable from the store rather than only in theory.
//
// A zero divisor PANICS, deliberately. Python raises ZeroDivisionError, and
// a helper that answered 0 instead would turn an impossible input into a
// plausible number — the failing-open shape the port has already been bitten
// by. No call site can reach it: every divisor is a positive constant.
func FloorDiv(a, b int) int {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// FloorDivAny is `a // b` where `a` came out of a store and may be a float.
//
// Python's `//` KEEPS THE WIDER TYPE while flooring: `5 // 2` is the int 2
// and `5.0 // 2` is the float 2.0, which are different objects that render
// as different text. metrics.analyze_step_costs computes `total_tok //
// count` where total_tok is a `sum()` over a JSON field, so a row carrying
// `"total_tokens": 2.5` makes avg_tokens a float in CPython — and a port
// that coerced to int would answer 2 where CPython answers 2.0.
//
// A non-numeric `a` is the caller's to reject before getting here; it
// returns the same TypeError Sum would.
// RoundAny is `round(x, n)` KEEPING PYTHON'S TYPE.
//
// round() of an int is an INT, at every ndigits — `round(2, 6)` is 2, not
// 2.0 — because rounding an integer to decimal places cannot change it and
// CPython short-circuits rather than converting. That matters wherever the
// result is rendered or written back: metrics.analyze_step_costs returns
// `round(sum(costs), 6)` as total_cost_usd, and a store whose cost_usd
// values are integers makes that field an int the report spells "2".
//
// A non-numeric value is returned unchanged; the callers here have already
// summed it, which is the operation that would have raised.
func RoundAny(v any, n int) any {
	i, f, isFloat, ok := numOf(v)
	if !ok {
		return v
	}
	if !isFloat {
		return i
	}
	return Round(f, n)
}

func FloorDivAny(a any, b int) (any, error) {
	i, f, isFloat, ok := numOf(a)
	if !ok {
		return nil, &PyErr{Class: "TypeError",
			Msg: fmt.Sprintf(
				"unsupported operand type(s) for //: '%s' and 'int'",
				TypeName(a))}
	}
	if isFloat {
		return math.Floor(f / float64(b)), nil
	}
	return FloorDiv(i, b), nil
}
