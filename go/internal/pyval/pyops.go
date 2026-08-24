package pyval

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
)

// This file is three CPython operations that RAISE, spelled out because the
// exception's MESSAGE reaches a store.
//
// The director's spawn branches interpolate `{exc}` straight into
// `output/escalations.jsonl`'s `reasoning` and into an operator-facing
// summary, so a port that swallows the failure writes a different row AND
// takes a different branch — it enqueues a task CPython never enqueues. The
// text is part of the contract at that seam, not an implementation detail.

// TypeName is `type(v).__name__` for the value shapes LoadsOrdered produces.
//
// It exists because three call sites had already hand-spelled 'NoneType' into
// their own error strings (internal/orch/mission_plan.go), each one a private
// copy of the same table. This is the shared one.
func TypeName(v any) string {
	switch t := v.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case json.Number:
		// The LITERAL decides, exactly as json.loads does: `1` is an int and
		// `1.0` is a float, and the two spell different exception messages.
		if strings.ContainsAny(string(t), ".eE") {
			return "float"
		}
		return "int"
	case int:
		return "int"
	case int64:
		return "int"
	case float64:
		return "float"
	case Obj, map[string]any:
		return "dict"
	case List, []any, []string:
		return "list"
	}
	return fmt.Sprintf("%T", v)
}

// numOf reads v as Python would read a number: (int, isFloat, ok). A bool is
// an int in Python — True is 1 — and that is not a quirk this port may skip,
// because `origin["checkins_sent"] = True` then advances to 2.
func numOf(v any) (i int, f float64, isFloat, ok bool) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, 0, false, true
		}
		return 0, 0, false, true
	case int:
		return t, 0, false, true
	case int64:
		return int(t), 0, false, true
	case float64:
		return 0, t, true, true
	case json.Number:
		if n, isInt := IsInt(t); isInt {
			return n, 0, false, true
		}
		g, err := t.Float64()
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return 0, 0, false, false
		}
		// A RANGE error is not a parse failure — strconv has already
		// produced the correctly-signed ±Inf, and CPython's json.loads
		// gives `inf` for 1e400 rather than raising. Reporting it
		// non-numeric made `2 >= inf` answer with the nonsense error
		// "'>=' not supported between instances of 'int' and 'float'",
		// which then goes into escalations.jsonl. reprNumber, one file
		// over, already had this right (adversarial r11 round 2, LOW).
		return 0, g, true, true
	}
	return 0, 0, false, false
}

// GE is Python's `lhs >= rhs` over numbers.
//
// The FLOAT case is the one a naive port loses: `2 >= 2.5` is false, and a
// port that read either side through IntOf would truncate 2.5 to 2 and fire
// a check-in CPython does not fire. Nothing raises there, so it is invisible
// to any test that only drives bad types.
func GE(lhs, rhs any) (bool, error) {
	li, lf, lIsFloat, lok := numOf(lhs)
	ri, rf, rIsFloat, rok := numOf(rhs)
	if !lok || !rok {
		return false, fmt.Errorf(
			"'>=' not supported between instances of '%s' and '%s'",
			TypeName(lhs), TypeName(rhs))
	}
	if lIsFloat || rIsFloat {
		if !lIsFloat {
			lf = float64(li)
		}
		if !rIsFloat {
			rf = float64(ri)
		}
		return lf >= rf, nil
	}
	return li >= ri, nil
}

// AddOne is Python's `v + 1`, keeping the operand's type: an int stays an
// int, a float stays a float (so `2.0 + 1` is `3.0` and is written back into
// the queue row as `3.0`), and a bool becomes an int.
func AddOne(v any) (any, error) { return AddN(v, 1) }

// AddN is Python's `v + n` for an integer n, with AddOne's typing rules.
func AddN(v any, n int) (any, error) {
	switch v.(type) {
	case string:
		return nil, fmt.Errorf(`can only concatenate str (not "int") to str`)
	case List, []any, []string:
		return nil, fmt.Errorf(`can only concatenate list (not "int") to list`)
	}
	i, f, isFloat, ok := numOf(v)
	if !ok {
		return nil, fmt.Errorf(
			"unsupported operand type(s) for +: '%s' and 'int'", TypeName(v))
	}
	if isFloat {
		return f + float64(n), nil
	}
	return i + n, nil
}

// DictOf is Python's `dict(v)` — the constructor, not a cast.
//
// A TypedDict subclass like `Origin` is `dict` at runtime, so `Origin(x)` is
// exactly this, and it RAISES for most non-mappings. That raise is the whole
// finding: the director builds an origin inside a try whose except flips the
// action to `surface`, so a malformed origin makes CPython refuse to enqueue
// while a port that quietly substituted an empty object enqueues a task,
// fires a check-in and reports `continue`.
//
// The sequence lane is not decoration either. `dict(["ab", "cd"])` is
// `{"a": "b", "c": "d"}` — two-character strings ARE valid pairs — and
// `dict([["a", 1]])` is `{"a": 1}`, which is how a JSON array of pairs
// becomes a perfectly good origin.
func DictOf(v any) (Obj, error) {
	switch t := v.(type) {
	case nil:
		return nil, fmt.Errorf("'NoneType' object is not iterable")
	case Obj:
		out := make(Obj, len(t))
		copy(out, t)
		return out, nil
	case map[string]any:
		return FromPlain(t).(Obj), nil
	case string:
		return pairsOf(len(t), func(i int) any {
			return string([]rune(t)[i])
		}, len([]rune(t)))
	case List:
		return pairsOf(len(t), func(i int) any { return t[i] }, len(t))
	case []any:
		return pairsOf(len(t), func(i int) any { return t[i] }, len(t))
	case []string:
		return pairsOf(len(t), func(i int) any { return t[i] }, len(t))
	}
	return nil, fmt.Errorf("'%s' object is not iterable", TypeName(v))
}

// pairsOf walks an iterable of would-be key/value pairs the way dict() does,
// with dict()'s own two refusals: an element that is not a sequence at all,
// and one whose length is not 2.
func pairsOf(_ int, at func(int) any, n int) (Obj, error) {
	var out Obj
	for i := 0; i < n; i++ {
		el := at(i)
		items, ok := seqOf(el)
		if !ok {
			// CPython omits the type name here, unlike the message above.
			//
			// VERSION-DEPENDENT, and pyproject.toml says >=3.10: 3.13+
			// says "object is not iterable" while 3.10-3.12 says
			// "cannot convert dictionary update sequence element #0 to a
			// sequence". This box runs 3.14, which is what the whole
			// justification for this file rests on (the message reaches
			// output/escalations.jsonl) — so a 3.10-3.12 deployment
			// writes a different row. NAMED, not fixed: guessing the
			// deployment's interpreter would be worse than one true
			// answer plus this note.
			return nil, fmt.Errorf("object is not iterable")
		}
		if len(items) != 2 {
			return nil, fmt.Errorf(
				"dictionary update sequence element #%d has length %d; 2 is required",
				i, len(items))
		}
		key, hashable := dictKey(items[0])
		if !hashable {
			return nil, fmt.Errorf(
				"cannot use '%s' as a dict key (unhashable type: '%s')",
				TypeName(items[0]), TypeName(items[0]))
		}
		out.Set(key, items[1])
	}
	return out, nil
}

// dictKey folds two CPython steps that this port cannot keep apart, and
// says so rather than pretending it is one.
//
// dict() refuses an unhashable key at CONSTRUCTION (a list or a dict), and
// json.dumps spells a surviving non-string key at WRITE time — `True`
// becomes "true", `None` becomes "null", `1.5` becomes "1.5". pyval.Obj's
// keys are Go strings, so the spelling has to happen here; the only
// consumer of an origin built this way is json.dumps, so the two agree on
// what lands on disk. `Str` was used before and is a THIRD spelling:
// str(True) is "True", which is neither.
func dictKey(v any) (string, bool) {
	switch t := v.(type) {
	case nil:
		return "null", true
	case bool:
		if t {
			return "true", true
		}
		return "false", true
	case string:
		return t, true
	case List, []any, Obj, map[string]any, []string:
		return "", false
	}
	if _, _, _, isNum := numOf(v); isNum {
		// json.dumps writes a numeric key with repr()'s spelling, which is
		// what Repr already produces for both int and float.
		return Repr(v), true
	}
	return Str(v), true
}

// seqOf is the elements of a value dict() would accept as a pair, or false
// when the value is not a sequence at all. A dict counts: its length is its
// KEY count, which is why `dict([{"a": 1}])` reports "has length 1".
func seqOf(v any) ([]any, bool) {
	switch t := v.(type) {
	case string:
		var out []any
		for _, r := range t {
			out = append(out, string(r))
		}
		return out, true
	case List:
		return []any(t), true
	case []any:
		return t, true
	case []string:
		out := make([]any, len(t))
		for i, s := range t {
			out[i] = s
		}
		return out, true
	case Obj:
		out := make([]any, len(t))
		for i, f := range t {
			out[i] = f.Key
		}
		return out, true
	case map[string]any:
		out := make([]any, 0, len(t))
		for k := range t {
			out = append(out, k)
		}
		return out, true
	}
	return nil, false
}

// Eq is Python's `==` between two JSON-derived values.
//
// It exists because `row.get("loop_id") == loop_id` decides whether a
// durable ledger row gets stamped, and the two sides come from two
// different files: one decoded from outcomes.jsonl, the other read off a
// task. Go's `==` is not the same relation. The two that matter here:
//
//	5 == "5"      Python False, and a port that spells the number True
//	5 == 5.0      Python True,  and a port comparing `any` values False
//
// Containers are structural because Python's are, even though nothing in
// this port compares one yet — leaving them to Go's `==` would PANIC on a
// slice rather than answer, which is a worse kind of wrong than a rare one.
func Eq(a, b any) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	if sa, ok := a.(string); ok {
		sb, ok := b.(string)
		return ok && sa == sb
	}
	if _, ok := b.(string); ok {
		return false
	}
	// Numbers, bool included: True == 1 in Python, and both sides may be
	// any of int / float64 / bool / json.Number.
	if ia, fa, floatA, okA := numOf(a); okA {
		if ib, fb, floatB, okB := numOf(b); okB {
			if floatA || floatB {
				if !floatA {
					fa = float64(ia)
				}
				if !floatB {
					fb = float64(ib)
				}
				return fa == fb
			}
			return ia == ib
		}
		return false
	}
	if _, _, _, okB := numOf(b); okB {
		return false
	}
	if la, ok := seqList(a); ok {
		lb, ok := seqList(b)
		if !ok || len(la) != len(lb) {
			return false
		}
		for i := range la {
			if !Eq(la[i], lb[i]) {
				return false
			}
		}
		return true
	}
	if _, ok := seqList(b); ok {
		return false
	}
	oa, okA := asObj(a)
	ob, okB := asObj(b)
	if okA && okB {
		if len(oa) != len(ob) {
			return false
		}
		for _, f := range oa {
			v, present := ob.Get(f.Key)
			if !present || !Eq(f.Val, v) {
				return false
			}
		}
		return true
	}
	return false
}

// seqList is Eq's list view. A dict is NOT a list to Python's `==`, which
// is why this is separate from seqOf (whose job is dict()'s iteration).
func seqList(v any) ([]any, bool) {
	switch t := v.(type) {
	case List:
		return t, true
	case []any:
		return t, true
	}
	return nil, false
}

func asObj(v any) (Obj, bool) {
	switch t := v.(type) {
	case Obj:
		return t, true
	case map[string]any:
		out := make(Obj, 0, len(t))
		for k, val := range t {
			out = append(out, Field{Key: k, Val: val})
		}
		return out, true
	}
	return nil, false
}

// Float is Python's `float(v)`: the value, or false where CPython raises.
//
// Distinct from SafeFloat, which is this repo's own defaulting helper. The
// difference is not stylistic — `float(_config_get("notify.timeout_seconds",
// 30))` has no try around it, so a non-numeric setting propagates to emit's
// outer handler and the hook DOES NOT RUN. A defaulting read would run it at
// 30 seconds instead, which is a different outcome for the operator's
// substrate, not a tidier spelling of the same one.
func Float(v any) (float64, bool) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case float64:
		return t, true
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	case string:
		// float() strips surrounding whitespace and accepts the spellings
		// Python accepts, which is what ParseFloat already reproduces.
		return ParseFloat(t)
	}
	return 0, false
}
