package pyval

import (
	"encoding/json"
	"fmt"
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
		if err != nil {
			return 0, 0, false, false
		}
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
			return nil, fmt.Errorf("object is not iterable")
		}
		if len(items) != 2 {
			return nil, fmt.Errorf(
				"dictionary update sequence element #%d has length %d; 2 is required",
				i, len(items))
		}
		out.Set(Str(items[0]), items[1])
	}
	return out, nil
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
