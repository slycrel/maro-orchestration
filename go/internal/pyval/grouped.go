package pyval

import "strconv"

// Grouped is Python's `format(n, ",")` / the `{:,}` format spec for an
// integer: digits in groups of three, separated by commas, with the sign
// kept outside the grouping.
//
// It lives here rather than privately at its first call site because `{:,}`
// is operator-facing prose in evidence strings and reports, and the port
// keeps finding that a second private spelling of a rendering is how two
// runtimes start describing the same number differently.
//
// Python groups from the RIGHT, so the leading group is 1-3 digits:
// 1234567 renders "1,234,567" and 1000 renders "1,000". A negative sign is
// not grouped with the digits: -1234 is "-1,234", never "-,1234".
//
// The grouping itself lives in groupDigits, shared with GroupedF: `{:,}`
// and `{:,.2f}` are the same separator rule over different digit runs, and
// two spellings of one rendering is how two surfaces start disagreeing.
func Grouped(n int64) string {
	sign, digits := splitSigned(strconv.FormatInt(n, 10))
	return sign + groupDigits(digits)
}
