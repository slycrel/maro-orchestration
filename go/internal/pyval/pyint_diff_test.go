package pyval_test

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyIntSrc asks CPython what `int(v)` does — the VALUE when it works and
// the exception CLASS and message when it does not.
//
// The class is the whole reason this probe exists. `int(nan)` and
// `int(inf)` both fail, and a port that only recorded "it failed" gets one
// of them right by accident: the first is a ValueError and lands in
// director.py's `except (TypeError, ValueError)`, the second is an
// OverflowError and leaves handle_escalation entirely.
const pyIntSrc = `
import json, sys
out = []
for expr in json.loads(sys.argv[1]):
    try:
        out.append({"ok": True, "value": str(int(eval(expr)))})
    except Exception as e:
        out.append({"ok": False, "cls": type(e).__name__, "msg": str(e)})
print(json.dumps(out))
`

// TestIntMatchesCPython pins pyval.Int against the interpreter, value by
// value and CLASS by class.
//
// Every row's Go value is written the way the decoder that feeds it would
// produce: json.Number for anything that came out of an LLM reply or a
// task file, float64 for anything yaml.v3 parsed, and a plain Go type only
// where a Go caller constructs it.
func TestIntMatchesCPython(t *testing.T) {
	type row struct {
		name   string
		goVal  any
		pyExpr string
		// tooLarge marks the rows where CPython succeeds with an
		// arbitrary-precision int this port cannot hold. They are listed
		// rather than omitted: the residual is real, and a table that left
		// them out would read as if the two runtimes agreed everywhere.
		tooLarge bool
	}
	rows := []row{
		{"a small int", 7, "7", false},
		{"zero", 0, "0", false},
		{"a negative int", -7, "-7", false},
		{"true", true, "True", false},
		{"false", false, "False", false},
		{"a float truncates toward zero", 7.9, "7.9", false},
		{"a negative float truncates toward zero", -7.9, "-7.9", false},
		{"a float that is exactly an int", 7.0, "7.0", false},

		// The decoded shapes. json.Number is what LoadsOrdered hands over,
		// and the integer/float distinction inside it is not cosmetic.
		{"a decoded int literal", json.Number("7"), "7", false},
		{"a decoded float literal", json.Number("2.0"), "2.0", false},
		{"a decoded exponent literal", json.Number("2e2"), "2e2", false},
		{"a decoded negative float literal", json.Number("-2.9"), "-2.9", false},

		// The two that separate the classes. json.loads admits both bare
		// tokens, so both are one LLM reply away.
		{"a NaN token", json.Number("NaN"), "float('nan')", false},
		{"an Infinity token", json.Number("Infinity"), "float('inf')", false},
		{"a -Infinity token", json.Number("-Infinity"), "float('-inf')", false},
		{"a float literal that overflows to inf", json.Number("1e400"), "1e400", false},
		{"a yaml .inf", mustInf(1), "float('inf')", false},
		{"a yaml .nan", mustNaN(), "float('nan')", false},

		// Strings: int() strips, takes a sign, and allows PEP 515
		// underscores BETWEEN digits and nowhere else.
		{"a numeric string", "7", "'7'", false},
		{"a padded numeric string", "  7  ", "'  7  '", false},
		{"a signed numeric string", "+7", "'+7'", false},
		{"a negatively signed numeric string", "-7", "'-7'", false},
		{"an underscored numeric string", "1_0", "'1_0'", false},
		{"prose", "high", "'high'", false},
		{"a decimal string", "7.5", "'7.5'", false},
		{"an exponent string", "7e2", "'7e2'", false},
		{"a leading underscore", "_7", "'_7'", false},
		{"a trailing underscore", "7_", "'7_'", false},
		{"a doubled underscore", "7__0", "'7__0'", false},
		{"an empty string", "", "''", false},
		{"whitespace", "  ", "'  '", false},

		// The raising types.
		{"a null", nil, "None", false},
		{"a list", pyval.List{}, "[]", false},
		{"an object", pyval.Obj{}, "{}", false},

		// The arbitrary-precision residual, from both directions.
		{"a float past int64", json.Number("1e19"), "1e19", true},
		{"a negative float past int64", json.Number("-1e19"), "-1e19", true},
		{"an int literal past int64", json.Number("99999999999999999999"),
			"99999999999999999999", true},
		{"a numeric string past int64", "99999999999999999999",
			"'99999999999999999999'", true},
		// And the boundary itself, on the side that DOES fit. float64 has
		// no exact 2^63-1, so the largest representable magnitude below
		// the bound is what a limit with a case at its own boundary looks
		// like here.
		{"a float just inside int64", json.Number("9.2e18"), "9.2e18", false},
		{"the exact negative bound", json.Number("-9223372036854775808"),
			"-9223372036854775808", false},
		// The bound itself, on the FLOAT path — the json.Number rows above
		// never reach it, because an integer literal that fits takes the
		// exact Int64 arm and one that does not is already past the bound.
		// A limit with no case at its own boundary is a limit nothing pins:
		// with only 9.2e18 in the table, both `f >= 2^63` and `f > 2^63`
		// passed, and so did `f <= -2^63`.
		{"the exact positive bound as a float", 9223372036854775808.0,
			"2.0**63", true},
		{"the exact negative bound as a float", -9223372036854775808.0,
			"-(2.0**63)", false},
	}

	exprs := make([]string, len(rows))
	for i, r := range rows {
		exprs[i] = r.pyExpr
	}
	arg, err := json.Marshal(exprs)
	if err != nil {
		t.Fatal(err)
	}
	var want []struct {
		OK    bool   `json:"ok"`
		Value string `json:"value"`
		Cls   string `json:"cls"`
		Msg   string `json:"msg"`
	}
	pyprobe.Probe{Marker: "director.py"}.RunJSON(t, pyIntSrc, &want, string(arg))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}

	// The table's own guard. If every row agreed on "it worked", the class
	// comparison below would be testing nothing at all, and this file
	// exists BECAUSE two classes that both fail are not interchangeable.
	classes := map[string]int{}
	for _, w := range want {
		if !w.OK {
			classes[w.Cls]++
		}
	}
	for _, c := range []string{"TypeError", "ValueError", "OverflowError"} {
		if classes[c] == 0 {
			t.Fatalf("no fixture makes CPython raise %s — the class "+
				"distinction this test exists for is not being exercised "+
				"(classes seen: %v)", c, classes)
		}
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			w := want[i]
			got, gotErr := pyval.Int(r.goVal)

			if r.tooLarge {
				if w.OK == false {
					t.Fatalf("row is marked as the arbitrary-precision "+
						"residual, but CPython RAISED %s here — the mark "+
						"is wrong", w.Cls)
				}
				if gotErr == nil {
					t.Fatalf("this port now holds %s (CPython says %s); "+
						"drop the tooLarge mark and the residual note",
						r.pyExpr, w.Value)
				}
				if !strings.Contains(gotErr.Error(), "past the int64 range") {
					t.Errorf("int(%s) refused with %v, want the "+
						"arbitrary-precision residual", r.pyExpr, gotErr)
				}
				return
			}

			if w.OK {
				if gotErr != nil {
					t.Fatalf("int(%s) raised %v; CPython says %s",
						r.pyExpr, gotErr, w.Value)
				}
				if itoa(got) != w.Value {
					t.Errorf("int(%s) = %s, CPython says %s",
						r.pyExpr, itoa(got), w.Value)
				}
				return
			}
			if gotErr == nil {
				t.Fatalf("int(%s) = %d; CPython raises %s: %s",
					r.pyExpr, got, w.Cls, w.Msg)
			}
			pe, ok := gotErr.(*pyval.PyErr)
			if !ok {
				t.Fatalf("int(%s) refused with %v, which carries no "+
					"exception class; CPython raises %s", r.pyExpr, gotErr, w.Cls)
			}
			if pe.Class != w.Cls {
				t.Errorf("int(%s) raises %s, CPython raises %s — the two "+
					"are caught by different excepts", r.pyExpr, pe.Class, w.Cls)
			}
			if pe.Msg != w.Msg {
				t.Errorf("int(%s) message = %q, CPython says %q",
					r.pyExpr, pe.Msg, w.Msg)
			}
		})
	}
}

// TestCaughtByIsTheExceptTupleAndNotAllOfThem drives the discrimination
// that makes the class worth carrying: the same value, under the two
// different tuples this port's callers actually write.
func TestCaughtByIsTheExceptTupleAndNotAllOfThem(t *testing.T) {
	nan, inf := mustNaN(), mustInf(1)

	// `except (TypeError, ValueError)` — director.py's confidence read.
	if n, raised := pyval.IntCaught(nan, 5); raised != nil || n != 5 {
		t.Errorf("int(nan) under (TypeError, ValueError) = (%d, %v), "+
			"want (5, nil) — ValueError is IN that tuple", n, raised)
	}
	if _, raised := pyval.IntCaught(inf, 5); raised == nil {
		t.Fatal("int(inf) was caught by (TypeError, ValueError); " +
			"OverflowError is not in that tuple, and swallowing it is how " +
			"the port wrote a full escalation where CPython wrote nothing")
	} else if !pyval.CaughtBy(raised, "OverflowError") {
		t.Errorf("int(inf) raised %v, want an OverflowError", raised)
	}

	// And the negative half: CaughtBy must not answer yes to everything.
	_, raised := pyval.Int(nan)
	if pyval.CaughtBy(raised, "OverflowError") {
		t.Error("CaughtBy called a ValueError an OverflowError")
	}
	if pyval.CaughtBy(nil, "ValueError") {
		t.Error("CaughtBy said nil is a ValueError")
	}
}

// TestIntClampedSaturatesOnlyWhereTheClampErasesTheDifference pins the one
// place this port is allowed to answer a number CPython would not compute
// the same way.
func TestIntClampedSaturatesOnlyWhereTheClampErasesTheDifference(t *testing.T) {
	for _, c := range []struct {
		name string
		in   any
		want int
	}{
		{"past int64, positive", json.Number("1e19"), 10},
		{"past int64, negative", json.Number("-1e19"), 1},
		{"inside the range, above the clamp", 42, 10},
		{"inside the range, below the clamp", -3, 1},
		{"inside the clamp", 6, 6},
	} {
		t.Run(c.name, func(t *testing.T) {
			got, err := pyval.IntClamped(c.in, 1, 10)
			if err != nil {
				t.Fatalf("raised %v", err)
			}
			if got != c.want {
				t.Errorf("= %d, want %d", got, c.want)
			}
		})
	}
	// The classes still propagate — saturation is for the residual, not
	// for a Python raise.
	if _, err := pyval.IntClamped(mustInf(1), 1, 10); err == nil {
		t.Error("IntClamped swallowed an OverflowError")
	}
}

func itoa(n int) string {
	b, _ := json.Marshal(n)
	return string(b)
}

// mustNaN and mustInf build the float64 values yaml.v3 produces for `.nan`
// and `.inf`, without importing math into a table.
func mustNaN() float64 {
	var z float64
	return z / z
}

func mustInf(sign int) float64 {
	var z float64
	if sign < 0 {
		return -1 / z
	}
	return 1 / z
}

// pySliceSrc asks CPython what `v[:n]` does. The head slice is not a
// string operation — a list slices to a list, a dict RAISES KeyError
// (a lookup with a slice for a key, not a TypeError), and everything
// else raises TypeError. Every one of those shapes is one task file away.
const pySliceSrc = `
import json, sys
out = []
for v, n in json.loads(sys.argv[1]):
    try:
        out.append({"ok": True, "value": v[:n]})
    except Exception as e:
        out.append({"ok": False, "cls": type(e).__name__, "msg": str(e)})
print(json.dumps(out))
`

// TestSliceHeadMatchesCPython pins the helper itself, shape by shape.
//
// It exists because the port had SliceHead as a string helper: a list
// goal returned "not sliceable" and the caller dropped an event row
// CPython writes as a JSON array. The differential that caught it was two
// packages away, so the helper had no case of its own — and the arms it
// grew afterwards (List, []any, []string, the negative bound) had none
// either.
func TestSliceHeadMatchesCPython(t *testing.T) {
	type row struct {
		name  string
		goVal any
		// pyVal is the same value as JSON, so the probe reads it back with
		// json.loads and slices exactly what the Go arm was handed.
		pyVal any
		n     int
	}
	rows := []row{
		{"a string", "abcdef", "abcdef", 3},
		{"a string shorter than the bound", "ab", "ab", 80},
		{"an empty string", "", "", 80},
		{"a string, negative bound", "abcdef", "abcdef", -2},
		{"a string, negative past its length", "ab", "ab", -9},

		{"a list", pyval.List{"a", "b", "c"}, []any{"a", "b", "c"}, 2},
		{"a list shorter than the bound", pyval.List{1.0}, []any{1.0}, 80},
		{"an empty list", pyval.List{}, []any{}, 80},
		{"a list, negative bound", pyval.List{1.0, 2.0, 3.0},
			[]any{1.0, 2.0, 3.0}, -1},
		{"a list, negative past its length", pyval.List{1.0},
			[]any{1.0}, -9},
		{"a decoded []any", []any{"a", "b"}, []any{"a", "b"}, 1},
		{"a []string", []string{"a", "b"}, []any{"a", "b"}, 1},

		// The raising shapes, which do NOT raise the same way.
		{"a dict", pyval.Obj{{Key: "ask", Val: "why"}},
			map[string]any{"ask": "why"}, 80},
		{"an empty dict", pyval.Obj{}, map[string]any{}, 80},
		{"a null", nil, nil, 80},
		{"an int", 4242, 4242, 80},
		{"a float", 2.5, 2.5, 80},
		{"a bool", true, true, 80},
	}

	args := make([][2]any, len(rows))
	for i, r := range rows {
		args[i] = [2]any{r.pyVal, r.n}
	}
	var want []struct {
		OK    bool            `json:"ok"`
		Value json.RawMessage `json:"value"`
		Cls   string          `json:"cls"`
		Msg   string          `json:"msg"`
	}
	pyprobe.Probe{Marker: "notify.py"}.RunJSON(t, pySliceSrc, &want,
		pyprobe.Arg(t, args))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}

	// The table's own guard, in both directions: a table where everything
	// sliced would make the `sliceable` half testing-nothing, and one where
	// everything raised would make the VALUE half testing nothing.
	var ok, raised int
	for _, w := range want {
		if w.OK {
			ok++
		} else {
			raised++
		}
	}
	if ok == 0 || raised == 0 {
		t.Fatalf("the table is one-sided (%d sliced, %d raised) — one half "+
			"of this test is not being exercised", ok, raised)
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			w := want[i]
			got, sliceable := pyval.SliceHead(r.goVal, r.n)
			if sliceable != w.OK {
				if w.OK {
					t.Fatalf("SliceHead refused; CPython sliced to %s", w.Value)
				}
				t.Fatalf("SliceHead answered %#v; CPython raises %s: %s",
					got, w.Cls, w.Msg)
			}
			if !w.OK {
				return
			}
			gotJSON, err := json.Marshal(pyval.Plain(got))
			if err != nil {
				t.Fatal(err)
			}
			wantJSON, err := json.Marshal(json.RawMessage(w.Value))
			if err != nil {
				t.Fatal(err)
			}
			if string(gotJSON) != string(wantJSON) {
				t.Errorf("v[:%d] = %s, CPython says %s",
					r.n, gotJSON, wantJSON)
			}
		})
	}
}

// TestFloatReadsTheOverflowLiteral pins Float's json.Number arm at the
// one spelling that separates "not a number" from "a number too large to
// represent".
//
// Honestly labelled: NO call site reaches this today. Float has two
// callers, and both hand it either a YAML-parsed value or a decoded
// confidence that Int has already refused with an OverflowError before
// the sign recovery runs. It is pinned at the helper because the contract
// is the reason the arm exists — `json.loads("1e400")` is inf in CPython,
// not an error, and Go's ParseFloat returns the correctly-signed infinity
// ALONGSIDE strconv.ErrRange. A helper that reads any parse error as "not
// a number" answers TypeError where CPython compares happily.
func TestFloatReadsTheOverflowLiteral(t *testing.T) {
	for _, c := range []struct {
		name    string
		in      any
		want    float64
		numeric bool
	}{
		{"a positive overflow literal", json.Number("1e400"), mustInf(1), true},
		{"a negative overflow literal", json.Number("-1e400"), mustInf(-1), true},
		{"an ordinary literal", json.Number("2.5"), 2.5, true},
		{"not a number at all", json.Number("deep"), 0, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			got, numeric := pyval.Float(c.in)
			if numeric != c.numeric {
				t.Fatalf("numeric = %v, want %v (got %v)", numeric, c.numeric, got)
			}
			if !c.numeric {
				return
			}
			if got != c.want && !(got != got && c.want != c.want) {
				t.Errorf("= %v, want %v", got, c.want)
			}
		})
	}
}
