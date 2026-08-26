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
func Grouped(n int64) string {
	s := strconv.FormatInt(n, 10)
	neg := false
	if s[0] == '-' {
		neg, s = true, s[1:]
	}
	// Walk from the right in threes. Building the groups first and joining
	// avoids the off-by-one that an index-based `i%3` gets wrong for
	// lengths that are exact multiples of three (1000 → ",1,000").
	out := make([]byte, 0, len(s)+len(s)/3+1)
	if neg {
		out = append(out, '-')
	}
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
