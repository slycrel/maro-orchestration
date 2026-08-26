package pyval

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
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
func AddOne(v any) (any, error) { return addN(v, 1, "+") }

// IAddOne is Python's `v += 1`.
//
// It is a separate spelling because the ERROR is: an unsupported operand
// under an augmented assignment names the augmented operator, so
// `task["attempt"] += 1` on a null row says "for +=" where `depth + 1`
// says "for +". Both messages reach a log line, and one of them is a queue
// row's only explanation for why a task would not claim.
func IAddOne(v any) (any, error) { return addN(v, 1, "+=") }

// AddN is Python's `v + n` for an integer n, with AddOne's typing rules.
func AddN(v any, n int) (any, error) { return addN(v, n, "+") }

func addN(v any, n int, op string) (any, error) {
	switch v.(type) {
	case string:
		return nil, &PyErr{Class: "TypeError",
			Msg: `can only concatenate str (not "int") to str`}
	case List, []any, []string:
		return nil, &PyErr{Class: "TypeError",
			Msg: `can only concatenate list (not "int") to list`}
	}
	i, f, isFloat, ok := numOf(v)
	if !ok {
		return nil, &PyErr{Class: "TypeError",
			Msg: fmt.Sprintf(
				"unsupported operand type(s) for %s: '%s' and 'int'",
				op, TypeName(v))}
	}
	if isFloat {
		return f + float64(n), nil
	}
	return i + n, nil
}

// Add is Python's binary `+` over two arbitrary values, restricted to the
// numeric lane and raising the way CPython does everywhere else.
//
// It exists because `AddN` only takes a Go int on the right, and
// `mm.total_tokens_in += o.tokens_in` adds two values that both arrive
// untyped from a JSON store. The INT/FLOAT distinction is the point: the
// running total starts as int 0 and stays an int until a float row arrives,
// and the report renders it with `{:,}`, which has an int lane
// (`1,234,567`) and a float lane (`1,234.5`).
//
// `op` names the operator for the error message, because Python's augmented
// assignment says "for +=" where a bare sum says "for +", and both spellings
// exist in metrics.py within a few lines of each other.
//
// NOT compensated, deliberately: this is the `+=` fold, and CPython's `+=`
// is a fold. `Sum` is the compensated one. compute_metrics uses BOTH over
// the same costs — `sum()` for by_task_type and `+=` for by_model — and
// they disagree on ordinary inputs, so collapsing them into one helper is
// a divergence in whichever field loses.
func Add(a, b any, op string) (any, error) {
	ai, af, aFloat, aOK := numOf(a)
	bi, bf, bFloat, bOK := numOf(b)
	if !aOK || !bOK {
		return nil, &PyErr{Class: "TypeError",
			Msg: fmt.Sprintf(
				"unsupported operand type(s) for %s: '%s' and '%s'",
				op, TypeName(a), TypeName(b))}
	}
	if aFloat || bFloat {
		if !aFloat {
			af = float64(ai)
		}
		if !bFloat {
			bf = float64(bi)
		}
		return af + bf, nil
	}
	return ai + bi, nil
}

// Sum is Python's builtin `sum(iterable)`, RAISES AND ALL.
//
// It exists because `sum(e.get("cost_usd", 0.0) for e in entries)` is a
// different function from `sum(float(e.get("cost_usd", 0.0) or 0.0) ...)`,
// and metrics.py contains both — four lines apart in one case. The second
// coerces and can only produce a number; the first adds whatever the store
// holds, so one row carrying `"cost_usd": null` takes down the caller. A
// port that used the coercing spelling for both fails OPEN: the crash
// becomes a plausible small number and the corrupt row is never noticed.
//
// Two typing rules travel with it. The accumulator STARTS AS INT 0, so an
// empty sum is `0` and not `0.0`, and the error message names the
// accumulator's type at the moment it failed — `'int' and 'NoneType'` for a
// null in the first position, `'float' and 'NoneType'` for one after a
// float has been added. And a bool is an int: `sum([True, True])` is 2.
//
// The result is `any` — int or float64 — because Python's is. Collapsing it
// to float64 would spell an integer token total `2.0` wherever it reaches a
// string, and collapsing it to int would truncate.
//
// IT IS NOT A NAIVE FOLD. Since 3.12, CPython's sum() switches to a
// compensated float loop — the improved Kahan–Babuška algorithm by Neumaier
// — the moment the accumulator becomes a float, and the difference is not
// academic:
//
//	sum([1e100, 1.0, -1e100])   -> 1.0     (a fold gives 0.0)
//	sum([0.05, 0.01, 0.01, -0.07])
//	                            -> -3.469446951953614e-18  (a fold gives 0.0)
//
// The second one is the shape that reaches this port: four ordinary cost
// rows, and `round(total, 6)` turns CPython's residue into -0.0 while a
// folded 0.0 stays +0.0. Two runtimes writing `-0` and `0` into one ledger
// is exactly the divergence class this package exists to close. MEASURED on
// python3 3.14.3, and pinned by TestSumMatchesCPython.
//
// Once the running total goes non-finite the compensation term is
// meaningless — it goes NaN and would poison an honest infinity — so it is
// dropped at the end rather than added: sum([1e308, 1e308, -1e308]) is inf,
// not nan.
//
// The integer lane stays exact, and Go's int is 64 bits where Python's has
// no bound. The limit is NAMED rather than worked around — but the reason
// once given for that was false and is worth correcting rather than deleting:
// "json.Number already bounds" it. json.Number is a STRING; it bounds
// nothing. What actually happens to an out-of-range literal is that IsInt
// rejects it and numOf falls through to Float64, so a token count past
// 2^63 arrives here as a float and takes the compensated lane.
//
// Inside the int lane, `acc += i` wraps SILENTLY, and the wrap flips the
// sign: two rows of math.MaxInt64 sum to 18446744073709551614 in CPython and
// to -2 here. A negative total then changes which types clear the `> 0`
// median filter and which land in expensive_types — a wrong answer rather
// than a crash. Reaching it needs absurd token counts, which is why this is
// a named limit and not a defect, but "absurd" is not "impossible" for a
// field a foreign writer controls.
func Sum(vals []any) (any, error) {
	// The integer lane, exact, until the first float arrives.
	acc := 0
	idx := 0
	for ; idx < len(vals); idx++ {
		i, f, isFloat, ok := numOf(vals[idx])
		if !ok {
			return nil, sumTypeErr(acc, vals[idx])
		}
		if isFloat {
			// CPython's generic `result + item` performs this one crossing,
			// and the compensated loop starts AFTER it with c = 0.
			return sumFloats(float64(acc)+f, vals[idx+1:])
		}
		acc += i
	}
	return acc, nil
}

// sumFloats is CPython's float fast path: Neumaier compensation over the
// remaining items, whatever their numeric type.
func sumFloats(start float64, rest []any) (any, error) {
	result, c := start, 0.0
	for _, v := range rest {
		i, f, isFloat, ok := numOf(v)
		if !ok {
			return nil, sumTypeErr(result, v)
		}
		x := f
		if !isFloat {
			x = float64(i)
		}
		t := result + x
		if math.Abs(result) >= math.Abs(x) {
			c += (result - t) + x
		} else {
			c += (x - t) + result
		}
		result = t
	}
	if math.IsInf(result, 0) || math.IsNaN(result) {
		return result, nil
	}
	return result + c, nil
}

// sumTypeErr names the ACCUMULATOR's type, not the sum's eventual type:
// `sum([1.5, None])` says 'float' where `sum([None])` says 'int', because
// the message is built from the object the addition was attempted on.
func sumTypeErr(acc any, item any) error {
	return &PyErr{Class: "TypeError",
		Msg: fmt.Sprintf("unsupported operand type(s) for +: '%s' and '%s'",
			TypeName(acc), TypeName(item))}
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
			return nil, fmt.Errorf("%s", UnhashableKeyMsg(items[0]))
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
	case []string:
		// Iterable, like every other sequence shape this package accepts.
		// Without this arm a []string was "not iterable" to `in`, to the
		// list coercions and to everything built on them, while Str and
		// TypeName happily named it a list (adversarial r11 round 9, LOW).
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = x
		}
		return out, true
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
		// ErrRange is the SAME case numOf tolerates twenty lines up:
		// strconv has already produced the correctly-signed ±Inf, and
		// CPython's float("1e400") is inf rather than a raise. The two arms
		// were written apart and only one was fixed (adversarial r11 round
		// 3, LOW) — latent, since Float's only non-test caller is fed by
		// yaml.v3 and never sees a json.Number, but it is the third
		// instance of the same pattern in this file.
		return f, err == nil || errors.Is(err, strconv.ErrRange)
	case string:
		// float() strips surrounding whitespace and accepts the spellings
		// Python accepts, which is what ParseFloat already reproduces.
		return ParseFloat(t)
	}
	return 0, false
}

// UnhashableKeyMsg is CPython's message for using an unhashable value as a
// dict key.
//
// THE WORDING IS INTERPRETER-VERSION DEPENDENT, and both spellings are live
// on this machine. Measured:
//
//	3.14.3  "cannot use 'dict' as a dict key (unhashable type: 'dict')"
//	3.12.3  "unhashable type: 'dict'"
//
// The port matches whichever interpreter `python3` resolves to on PATH,
// which here is linuxbrew's 3.14.3 — the same posture, and the same reason,
// as the negative-modulo note in introspect/cli.go. Two call sites had
// hand-written this string and they had drifted to DIFFERENT versions of
// CPython, which is the argument for one spelling rather than for either
// wording: analyze_step_costs raised the 3.12 message while Dict() raised
// the 3.14 one, and no test compared them (metrics r1 battery, M131).
//
// If this ever goes red on an unhashable-key case, check `python3 --version`
// before checking the code.
func UnhashableKeyMsg(v any) string {
	return fmt.Sprintf("cannot use '%s' as a dict key (unhashable type: '%s')",
		TypeName(v), TypeName(v))
}
