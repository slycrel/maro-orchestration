package pyval

import (
	"encoding/json"
	"errors"
	"fmt"
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
// key, which is what makes 1.0 and 1 the same bucket. A NaN is deliberately
// NOT folded: Python hashes every NaN alike but compares them unequal, so
// each one is its own bucket.
func floatHashKey(f float64) string {
	if math.IsNaN(f) {
		return fmt.Sprintf("nan:%p", new(int))
	}
	if !math.IsInf(f, 0) && f == math.Trunc(f) &&
		f >= -9.2e18 && f <= 9.2e18 {
		return "i:" + strconv.FormatInt(int64(f), 10)
	}
	return "f:" + strconv.FormatFloat(f, 'g', -1, 64)
}
