package pyval

import (
	"encoding/json"
	"errors"
	"math"
	"strconv"
)

// HashKey is Python's hash identity for a JSON-decodable value: the key a
// dict would file it under, and equivalently the element a set would
// compare it as.
//
// Two facts drive it, and neither is spelling:
//
//   - `True`, `1` and `1.0` are ONE key, because Python hashes numbers by
//     VALUE and `True == 1`. `{"queued": 1}[True]` is a KeyError only
//     because "queued" is a different value, not because `True` is a
//     different type.
//   - a list and a dict have no hash at all. The second return is false
//     for those, and the caller decides which TypeError its operation
//     raises — a dict subscript and a set membership do not word it the
//     same way.
//
// The prefixes keep the namespaces apart, because Python does too:
// `4242 in {"4242"}` is False, and a bare string key would collide.
//
// This is the ONE implementation. `status_summary` buckets with it and
// `drain_task_store`'s job-id set filters with it; a second copy that
// disagreed would be a divergence nothing in either caller could see.
func HashKey(v any) (string, bool) {
	switch t := v.(type) {
	case string:
		return "s:" + t, true
	case nil:
		return "n:", true
	case bool:
		if t {
			return "i:1", true
		}
		return "i:0", true
	case int:
		return "i:" + strconv.Itoa(t), true
	case int64:
		return "i:" + strconv.FormatInt(t, 10), true
	case float64:
		return floatHashKey(t), true
	case json.Number:
		// An integer literal past float64's exact range still hashes as
		// the integer it is: json.Number is the only shape that can carry
		// one, and rounding it through a float would merge two distinct
		// keys (r11 round 4 — int-vs-overflowed-float decides more than
		// one answer here).
		if lit, ok := IntLiteral(t.String()); ok {
			return "i:" + lit, true
		}
		f, err := t.Float64()
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return "s:" + t.String(), true
		}
		return floatHashKey(f), true
	case Obj, List, map[string]any, []any, []string:
		return "", false
	}
	return "o:" + Str(v), true
}

// floatHashKey folds a float that is exactly an integer onto the integer's
// key, which is what makes 1.0 and 1 the same bucket — at any magnitude,
// because Python's numeric hashing has no bound. The NaN arm is measured
// rather than reasoned; see below.
func floatHashKey(f float64) string {
	if math.IsNaN(f) {
		// ONE key for every NaN, and the reason is identity rather than
		// equality. `nan != nan`, so two SEPARATELY CONSTRUCTED NaNs are
		// two dict keys — but CPython's JSON scanner hands back the same
		// cached object for every bare `NaN` token, and dict lookup
		// short-circuits on `x is y` before it ever compares. Measured:
		//
		//	a = json.loads("NaN"); b = json.loads("NaN")
		//	a is b                     -> True
		//	{} counting a then b       -> {nan: 2}, len 1
		//	{} counting two float("nan") -> {nan: 1, nan: 1}, len 2
		//
		// Every value that reaches this function came off a decoder, so the
		// cached-object case is the only reachable one. The first cut keyed
		// each NaN by a fresh pointer and reported two buckets of one where
		// CPython reports one bucket of two (adversarial r11 round 7, found
		// while pinning the fold's upper end).
		return "nan:"
	}
	if math.IsInf(f, 0) || f != math.Trunc(f) {
		return "f:" + strconv.FormatFloat(f, 'g', -1, 64)
	}
	// -0.0 and 0 are ONE key in Python (`-0.0 == 0` is True), and 'f'
	// spells a negative zero "-0", which would split them.
	if f == 0 {
		return "i:0"
	}
	// 'f' with precision -1 spells an integral float as its EXACT integer
	// digits at any magnitude, so 1e19 keys as "10000000000000000000" and
	// meets the JSON integer literal of the same value.
	//
	// The first cut folded only within ±9.2e18 and fell through to the
	// float spelling above that, which split `1e19` from `10**19` — two
	// buckets of one where CPython reports one bucket of two. The bound was
	// the upper-end sibling of the fold that makes True/1/1.0 agree, and it
	// was arbitrary in both directions: 9.2e18 is under 2^63-1, so
	// 9.22e18 and 9220000000000000000 split as well (adversarial r11 round
	// 7, LOW). There is no bound now, which is what Python's numeric
	// hashing has.
	return "i:" + strconv.FormatFloat(f, 'f', -1, 64)
}
