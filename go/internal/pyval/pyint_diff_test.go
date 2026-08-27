package pyval_test

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os/exec"
	"strings"
	"testing"
	"unicode"

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

		// int(str) reads DECIMAL DIGITS, not ASCII ones, and skips a
		// whitespace set of its own. Both were wrong here until
		// loopparallel's MARO_STEP_TIMEOUT differential asked -- and the
		// fix is in this file, so the rows that pin it belong in this
		// file too. A guard that lives only in the caller that found it
		// is the same shape as the finding (L15).
		{"an arabic-indic numeric string", "\u0660\u0666\u0660",
			"'\\u0660\\u0666\\u0660'", false},
		{"a fullwidth numeric string", "\uff16\uff10", "'\\uff16\\uff10'", false},
		{"two scripts in one number", "6\u0660", "'6\\u0660'", false},
		{"mathematical bold digits, five blocks in one Go range",
			"\U0001d7d2\U0001d7d0", "'\\U0001d7d2\\U0001d7d0'", false},
		// U+1D7CE..U+1D7FF is the ONE Nd range Go packs more than one
		// alphabet into — 50 code points, five zero-to-nines. Bold sits at
		// offset 0..9 and cannot separate `(c-Lo)%10` from `c-Lo`; these
		// are DOUBLE-STRUCK (offset 10..19) and MONOSPACE (offset 40..49),
		// where the two spellings answer 12 and 2, 42 and 2.
		{"double-struck digits, the SECOND block in that range",
			"\U0001d7da\U0001d7d8", "'\\U0001d7da\\U0001d7d8'", false},
		{"monospace digits, the LAST block in it",
			"\U0001d7fa\U0001d7f8", "'\\U0001d7fa\\U0001d7f8'", false},
		// The SUPPLEMENT: Go ships Unicode 15.0.0 and this interpreter has
		// 16.0.0, so these are digits to int() and absent from Go's Nd
		// table entirely. Reading them takes pytext's seven hand-carried
		// ranges — a census over all 760 decimal digits found this on its
		// first run, after a version of decimalValue that walked Go's
		// table alone had already passed everything else in this file.
		{"an OL ONAL digit, Unicode 16 and not in Go's table",
			"\U0001e5f4\U0001e5f1", "'\\U0001e5f4\\U0001e5f1'", false},
		{"a GARAY digit, the first supplement block",
			"\U00010d44", "'\\U00010d44'", false},
		{"an OUTLINED digit", "\U0001ccf7", "'\\U0001ccf7'", false},
		{"a supplement digit next to an ASCII one",
			"4\U00016d75", "'4\\U00016d75'", false},
		{"an underscore between digits of DIFFERENT scripts",
			"\u0660_\u0666", "'\\u0660_\\u0666'", false},
		{"superscript two is a digit to isdigit() and not to int()",
			"\u00b2", "'\\u00b2'", false},
		{"a NO-BREAK SPACE is skipped", "\u00a07", "'\\u00a07'", false},
		{"a LINE SEPARATOR is skipped", "\u20287", "'\\u20287'", false},
		// THE four. str.strip() removes them and int() does not, which is
		// why reaching for pytext.Strip here was wrong.
		{"FILE SEPARATOR is NOT skipped", "\u001c7", "'\\u001c7'", false},
		{"GROUP SEPARATOR is NOT skipped", "\u001d7", "'\\u001d7'", false},
		{"RECORD SEPARATOR is NOT skipped", "\u001e7", "'\\u001e7'", false},
		{"UNIT SEPARATOR is NOT skipped", "\u001f7", "'\\u001f7'", false},
		{"...and one TRAILING, which strip() would also have taken",
			"7\u001f", "'7\\u001f'", false},
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

		// The int64 boundary on the STRING arm, which the json.Number
		// rows above cannot reach — they take Int64() and never touch the
		// digit accumulator. It was conservative by a whole decade, so
		// every value from 922337203685477580 up was refused (adversarial
		// r11 round 5, LOW). Pinned from both sides and on both signs,
		// because the negative side reaches one further than the
		// positive one.
		{"the exact positive bound as a string", "9223372036854775807",
			"'9223372036854775807'", false},
		{"one under the positive bound as a string", "9223372036854775806",
			"'9223372036854775806'", false},
		{"one past the positive bound as a string", "9223372036854775808",
			"'9223372036854775808'", true},
		{"the exact negative bound as a string", "-9223372036854775808",
			"'-9223372036854775808'", false},
		{"one past the negative bound as a string", "-9223372036854775809",
			"'-9223372036854775809'", true},
		// The decade the old guard rejected wholesale.
		{"the first value the conservative guard refused",
			"922337203685477580", "'922337203685477580'", false},

		// CPython caps int(str) at 4300 DIGITS and raises ValueError past
		// it — a limit that has nothing to do with the value's magnitude,
		// so the arbitrary-precision residual cannot stand in for it: one
		// is a ValueError an `except ValueError` catches and the other is
		// this port's own sentinel, which it does not. The boundary is
		// pinned from both sides.
		{"a numeric string at the digit limit",
			strings.Repeat("1", 4300), "'1'*4300", true},
		{"a numeric string one digit past the limit",
			strings.Repeat("1", 4301), "'1'*4301", false},
		// The count EXCLUDES underscores and the sign: a limit counted off
		// len(s) would report 8601 digits here and 4302 on the next row.
		{"underscores do not count toward the digit limit",
			strings.Join(strings.Split(strings.Repeat("1", 4301), ""), "_"),
			`"_".join("1"*4301)`, false},
		{"the sign does not count toward the digit limit",
			"-" + strings.Repeat("1", 4301), "'-' + '1'*4301", false},
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
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyIntSrc, &want, string(arg))
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

	// The digit-limit ValueError is IN that tuple; the port's own
	// arbitrary-precision sentinel is not, and a caller that folds the two
	// together defaults on one and propagates the other.
	if n, raised := pyval.IntCaught(strings.Repeat("1", 4301), 5); raised != nil ||
		n != 5 {
		t.Errorf("int('1'*4301) under (TypeError, ValueError) = (%d, %v), "+
			"want (5, nil) — the digit limit is a ValueError", n, raised)
	}
	if _, raised := pyval.IntCaught(strings.Repeat("1", 4300), 5); raised == nil {
		t.Error("int('1'*4300) was caught by (TypeError, ValueError); " +
			"4300 digits is UNDER CPython's limit, so this is the port's " +
			"own int64 residual and no Python except sees it")
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
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pySliceSrc, &want,
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
			got, sliceErr := pyval.SliceHead(r.goVal, r.n)
			if (sliceErr == nil) != w.OK {
				if w.OK {
					t.Fatalf("SliceHead refused with %v; CPython sliced to %s",
						sliceErr, w.Value)
				}
				t.Fatalf("SliceHead answered %#v; CPython raises %s: %s",
					got, w.Cls, w.Msg)
			}
			if !w.OK {
				// The CLASS and the MESSAGE, because one call site logs the
				// exception rather than swallowing it — and a dict and an
				// int do not fail the same way.
				pe, ok := sliceErr.(*pyval.PyErr)
				if !ok {
					t.Fatalf("SliceHead refused with %v, which carries no "+
						"exception class; CPython raises %s", sliceErr, w.Cls)
				}
				if pe.Class != w.Cls {
					t.Errorf("v[:%d] raises %s, CPython raises %s",
						r.n, pe.Class, w.Cls)
				}
				if pe.Msg != w.Msg {
					t.Errorf("v[:%d] message = %q, CPython says %q",
						r.n, pe.Msg, w.Msg)
				}
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

// pyPercentDSrc asks CPython what `logging` does with a `%d` argument —
// which is not what `"%d" % v` alone does. `log.info("… depth=%d", v)`
// defers the formatting into `LogRecord.getMessage`, and
// `logging.Handler.emit` is DEFINED to route a formatting error to
// `handleError` rather than let it out. So a `%d` that raises does not
// crash the caller: it silently produces NO RECORD, and the operator's
// log is missing a line nobody will ever notice is missing.
//
// Both halves are measured here, because the port has to reproduce both:
// the rendered text when it works, and the vanished record when it does
// not.
const pyPercentDSrc = `
import json, logging, sys

lines = []

class _Cap(logging.Handler):
    def emit(self, record):
        try:
            lines.append(record.getMessage())
        except Exception:
            self.handleError(record)

_log = logging.getLogger("probe")
_log.addHandler(_Cap())
_log.setLevel(logging.DEBUG)
_log.propagate = False

out = []
for expr in json.loads(sys.argv[1]):
    before = len(lines)
    _log.info("v=%d", eval(expr))
    if len(lines) == before:
        out.append({"ok": False})
    else:
        out.append({"ok": True, "value": lines[-1][2:]})
print(json.dumps(out))
`

// TestPercentDMatchesCPython pins pyval.PercentD against the interpreter.
//
// Why its own table rather than a few more escalation fixtures: the three
// call sites can only be fed values a task file can carry, and a task file
// is JSON — which cannot spell NaN, cannot spell Infinity through Go's
// encoder, and reaches `-0.0` only by accident. The operator itself has no
// such limit, and the values it gets wrong are exactly the ones the
// round-4 battery could not reach: a nan, an inf, a negative zero, a bool,
// and an integer literal too wide for float64.
func TestPercentDMatchesCPython(t *testing.T) {
	type row struct {
		name   string
		goVal  any
		pyExpr string
	}
	rows := []row{
		{"a small int", 7, "7"},
		{"zero", 0, "0"},
		{"a negative int", -7, "-7"},
		{"an int64", int64(-9223372036854775808), "-2**63"},

		// %d is not str(): a float renders as the integer it truncates to.
		{"a float that is exactly an int", 2.0, "2.0"},
		{"a fractional float truncates toward zero", 2.9, "2.9"},
		{"a negative fractional float truncates toward zero", -2.9, "-2.9"},
		// Python has no negative zero INTEGER, so `%d` of -0.5 is "0" and
		// not "-0" — the one row that pins the sign-stripping arm.
		{"a float that truncates to negative zero", -0.5, "-0.5"},
		{"negative zero itself", math.Copysign(0, -1), "-0.0"},

		// A bool IS an int in Python, and `%d` of True is 1. Dropping the
		// bool arm loses the record instead.
		{"true", true, "True"},
		{"false", false, "False"},

		// The decoded shapes. An integer literal is EXACT at any width in
		// CPython, so routing it through float64 is not a rounding
		// nuisance — it prints a number the source never contained.
		{"a decoded int", json.Number("2"), "2"},
		{"a decoded float", json.Number("2.0"), "2.0"},
		{"a decoded int literal past float64's exact range",
			json.Number("9007199254740993"), "9007199254740993"},
		{"a decoded int literal past int64", json.Number("99999999999999999999"),
			"99999999999999999999"},
		{"a decoded float in exponent form", json.Number("1e3"), "1e3"},

		// The records that never exist. Each of these raises inside
		// getMessage, and the handler swallows it.
		{"nan", math.NaN(), "float('nan')"},
		{"inf", math.Inf(1), "float('inf')"},
		{"negative inf", math.Inf(-1), "float('-inf')"},
		{"a decoded nan", json.Number("NaN"), "float('nan')"},
		{"a string", "deep", "'deep'"},
		{"a numeric string", "2", "'2'"},
		{"none", nil, "None"},
		{"a list", []any{1}, "[1]"},
		{"a dict", map[string]any{"a": 1}, "{'a': 1}"},
		{"an Obj", pyval.Obj{{Key: "a", Val: 1}}, "{'a': 1}"},
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
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyPercentDSrc, &want, string(arg))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}

	// The table's own floor. If every row rendered, the second return
	// value would be testing nothing — and the whole point of this
	// operator is that some arguments DELETE the line.
	rendered, lost := 0, 0
	for _, w := range want {
		if w.OK {
			rendered++
		} else {
			lost++
		}
	}
	if rendered == 0 || lost == 0 {
		t.Fatalf("the table is one-sided: %d rendered, %d lost — a %%d "+
			"differential has to exercise both", rendered, lost)
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			w := want[i]
			got, recorded := pyval.PercentD(r.goVal)
			if recorded != w.OK {
				if w.OK {
					t.Fatalf("%%d of %s wrote no record; CPython logs %q",
						r.pyExpr, w.Value)
				}
				t.Fatalf("%%d of %s logged %q; CPython writes NO record at all",
					r.pyExpr, got)
			}
			if w.OK && got != w.Value {
				t.Errorf("%%d of %s = %q, CPython says %q",
					r.pyExpr, got, w.Value)
			}
		})
	}
}

// TestClassOfAnswersOnlyForErrorsThatClaimAClass is the negative half of
// the class layer. `CaughtBy` now routes through `ClassOf`, so an
// `except (TypeError, ValueError)` that started swallowing plain Go errors
// — a *os.PathError from a failed write, say — would be a silent widening
// of every except tuple in the port.
func TestClassOfAnswersOnlyForErrorsThatClaimAClass(t *testing.T) {
	plain := errors.New("disk on fire")
	if got := pyval.ClassOf(plain); got != "" {
		t.Errorf("ClassOf(a plain Go error) = %q, want \"\"", got)
	}
	for _, c := range []string{"TypeError", "ValueError", "OverflowError", ""} {
		if pyval.CaughtBy(plain, c) {
			t.Errorf("a plain Go error was caught by except %q", c)
		}
	}
	if pyval.CaughtBy(nil, "ValueError") {
		t.Error("a nil error was caught by an except tuple")
	}

	// Wrapped, because that is how a class survives a call stack: Python's
	// exception propagates unchanged, and Go's has to be found through
	// %w.
	wrapped := fmt.Errorf("writing the row: %w",
		&pyval.PyErr{Class: "KeyError", Msg: "'status'"})
	if got := pyval.ClassOf(wrapped); got != "KeyError" {
		t.Errorf("ClassOf(a wrapped PyErr) = %q, want KeyError", got)
	}
	if !pyval.CaughtBy(wrapped, "TypeError", "KeyError") {
		t.Error("a wrapped KeyError escaped except (TypeError, KeyError)")
	}

	// And a Go-typed error that names its class: the whole reason
	// PyClasser exists rather than turning every raise into a *PyErr.
	if got := pyval.ClassOf(&classedErr{}); got != "RuntimeError" {
		t.Errorf("ClassOf(a PyClasser) = %q, want RuntimeError", got)
	}
	if !pyval.CaughtBy(fmt.Errorf("wrapped: %w", &classedErr{}), "RuntimeError") {
		t.Error("a wrapped PyClasser escaped except RuntimeError")
	}
}

type classedErr struct{}

func (e *classedErr) Error() string   { return "already claimed" }
func (e *classedErr) PyClass() string { return "RuntimeError" }

// pyPercentFSrc asks CPython what `"%.*f" % v` renders. Unlike PercentD's
// probe this one does NOT go through logging: every call site is a `%.0f`
// inside a log ARGUMENT that has already been rendered by the port, and
// float formatting does not raise for any float — so there is no vanished
// record to model here, only text.
//
// The two runtimes disagree on exactly one class of input, and it is the
// one a config file can carry: Python spells the non-finites lowercase
// ("nan", "inf", "-inf") where Go's strconv spells them "NaN", "+Inf" and
// "-Inf". The rest of the table is here so that agreement is a claim
// about the whole operator rather than about three special cases.
const pyPercentFSrc = `
import json, sys
args = json.loads(sys.argv[1])
out = []
for prec, expr in args:
    out.append(("%." + str(prec) + "f") % eval(expr))
print(json.dumps(out))
`

// TestPercentFMatchesCPython pins pyval.PercentF against the interpreter.
//
// Its own table rather than more notify fixtures, and the reason is the
// finding that produced it: the round-6 battery mutated PercentF's NaN arm
// and NOTHING failed. notify refuses a NaN timeout one branch BEFORE the
// log line that would format it, so the arm is unreachable from the only
// live caller — a helper arm with no case at it, which is the same shape
// as Float's json.Number overflow arm. Pinned at the helper and labelled,
// rather than left as an arm nothing can fire.
func TestPercentFMatchesCPython(t *testing.T) {
	type row struct {
		name   string
		goVal  float64
		prec   int
		pyExpr string
	}
	rows := []row{
		{"a whole number", 3, 0, "3.0"},
		{"a negative whole number", -3, 0, "-3.0"},
		{"zero", 0, 0, "0.0"},
		// Python has a negative zero FLOAT, and %.0f keeps its sign — the
		// opposite of %d, which has no negative zero integer to print.
		{"negative zero", math.Copysign(0, -1), 0, "-0.0"},

		// Half-way cases: both runtimes round half to EVEN here, which is
		// C printf's rule and not the schoolbook one. A port that reached
		// for math.Round would answer 1, 2 and 3.
		{"a half that rounds down to even", 0.5, 0, "0.5"},
		{"a half that rounds up to even", 1.5, 0, "1.5"},
		{"another half that rounds down to even", 2.5, 0, "2.5"},
		{"a negative half", -0.5, 0, "-0.5"},

		// The hook timeout's own boundary, at the precision the log uses.
		// These two rows render IDENTICALLY — "2147484" both times — and
		// round 8 read their names as a claim they do not make: at
		// precision 0 the poll boundary is invisible, and the pair was
		// two spellings of one case. The boundary itself is asserted where
		// it is observable (notify's hookconfig table, which checks
		// whether the hook runs at all); what belongs HERE is that the
		// millisecond survives at a precision that can show it.
		{"the largest timeout poll accepts", 2147483.647, 0, "2147483.647"},
		{"the same value at millisecond precision", 2147483.647, 3,
			"2147483.647"},
		{"one millisecond past it, where %.0f cannot tell", 2147483.648, 0,
			"2147483.648"},
		{"one millisecond past it, at a precision that can", 2147483.648, 3,
			"2147483.648"},
		{"the never-time-out idiom", 3e6, 0, "3e6"},

		// A value with no short decimal form. Python expands it in full,
		// and so does Go — 301 digits of agreement, which is the point.
		{"a value with no short decimal form", -1e300, 0, "-1e300"},

		// The three the port had to spell for itself.
		{"nan", math.NaN(), 0, "float('nan')"},
		{"inf", math.Inf(1), 0, "float('inf')"},
		{"negative inf", math.Inf(-1), 0, "float('-inf')"},

		// A precision other than zero, so the argument is not a constant
		// the helper could ignore.
		{"two places", 1.005, 2, "1.005"},
		{"two places on a non-finite", math.Inf(-1), 2, "float('-inf')"},
		{"six places", 1.0 / 3.0, 6, "1.0 / 3.0"},
	}

	args := make([][2]any, len(rows))
	for i, r := range rows {
		args[i] = [2]any{r.prec, r.pyExpr}
	}
	arg, err := json.Marshal(args)
	if err != nil {
		t.Fatal(err)
	}
	var want []string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyPercentFSrc, &want, string(arg))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}

	// The table's own floor. Every row here renders — this operator never
	// deletes a record — so the claim that could go quietly false is that
	// some row is a NON-FINITE, which is the only class the two runtimes
	// spell differently. A table of ordinary numbers would agree whatever
	// the helper did with a nan.
	nonFinite := 0
	for _, r := range rows {
		if math.IsNaN(r.goVal) || math.IsInf(r.goVal, 0) {
			nonFinite++
		}
	}
	if nonFinite < 4 {
		t.Fatalf("only %d non-finite rows; the divergence this helper "+
			"exists for would not be under test", nonFinite)
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			if got := pyval.PercentF(r.goVal, r.prec); got != want[i] {
				t.Errorf("PercentF(%v, %d) = %q, CPython says %q",
					r.goVal, r.prec, got, want[i])
			}
		})
	}
}

// pyIntLiteralSrc asks CPython what an integer JSON literal decodes to and
// how it prints — the two things IntLiteral claims to answer.
const pyIntLiteralSrc = `
import json, sys
out = []
for lit in json.loads(sys.argv[1]):
    try:
        v = json.loads(lit)
    except Exception as e:
        out.append(["raise", type(e).__name__])
        continue
    out.append(["int", str(v)] if isinstance(v, int) and not isinstance(v, bool)
               else ["other", str(v)])
print(json.dumps(out))
`

// TestIntLiteralMatchesCPython pins the helper the %d path and the hash path
// both lean on. It had NO test of its own: round 8 found its zero-collapsing
// arm ("-0 and 0000 are both the integer 0") reachable from neither, which
// is the same helper-arm-with-no-case shape PercentF's NaN arm turned out to
// be. `-0` is legal JSON and decodes to the int 0, so half of that arm is
// live; `0000` is not, and is pinned below as the port's own answer rather
// than as a claim about CPython.
func TestIntLiteralMatchesCPython(t *testing.T) {
	lits := []string{
		"0", "-0", "1", "-1", "42", "-42",
		// Wider than int64 in both directions: still an int in Python, and
		// IntLiteral's whole reason for existing is that it does not
		// narrow.
		"9223372036854775807", "9223372036854775808",
		"-9223372036854775808", "-9223372036854775809",
		"1000000000000000000000000000000",
		"-1000000000000000000000000000000",
		// NOT integer literals. Each must be refused, and each is a
		// different way of not being one.
		"1.0", "-1.0", "1e3", "1E3", "0.5", "-0.5",
		"", "-", "+1", "0x10", "abc", "null", "true",
	}
	// NOT in the table above: " 1" and "1 ". json.loads accepts surrounding
	// whitespace and answers the int 1, so CPython-via-json is the wrong
	// oracle for them — the input IntLiteral actually receives is a
	// json.Number's String(), which the decoder never pads. They are pinned
	// below with the other shapes no decoder produces.

	var pyAns [][]string
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyIntLiteralSrc, &pyAns,
		pyprobe.Arg(t, lits))
	if len(pyAns) != len(lits) {
		t.Fatalf("CPython answered %d of %d literals", len(pyAns), len(lits))
	}

	ints := 0
	for i, lit := range lits {
		gotStr, gotOK := pyval.IntLiteral(lit)
		kind, val := pyAns[i][0], pyAns[i][1]
		wantOK := kind == "int"
		if gotOK != wantOK {
			t.Errorf("pyval.IntLiteral(%q) = (%q, %v); CPython decodes it as %s(%s)",
				lit, gotStr, gotOK, kind, val)
			continue
		}
		if wantOK {
			ints++
			if gotStr != val {
				t.Errorf("pyval.IntLiteral(%q) prints %q, CPython prints %q",
					lit, gotStr, val)
			}
		}
	}
	// Without this the table could be all refusals and every assertion
	// above would still hold.
	if ints < 10 {
		t.Fatalf("only %d literals decoded as ints; the table is not "+
			"exercising the accepting arm", ints)
	}

	// The other half of the zero-collapsing arm, which JSON cannot reach:
	// a leading-zero run is a decode error in both runtimes, so this is a
	// pin on the PORT's answer for a hand-built json.Number, not a claim
	// about CPython. It is recorded so the arm is not mistaken for tested
	// behaviour, and so deleting it is a visible decision.
	for lit, want := range map[string]string{
		"0000": "0", "-0000": "0", "007": "007",
		// Padded forms are refused outright — the empty want below means
		// "not an integer literal", which is right for a helper whose
		// input is a decoder's own token.
		" 1": "", "1 ": "",
	} {
		got, ok := pyval.IntLiteral(lit)
		if ok != (want != "") {
			t.Errorf("pyval.IntLiteral(%q) = (%q, %v); this pin records %q",
				lit, got, ok, want)
			continue
		}
		if got != want {
			t.Errorf("pyval.IntLiteral(%q) = %q, this pin records %q", lit, got, want)
		}
	}
}

// pyIntMsgSrc asks CPython for the invalid-literal ValueError MESSAGE, and
// nothing else. It is separate from pyIntSrc because the values it needs are
// hundreds of characters long and `eval` is the wrong door for them: these
// are strings handed to int() verbatim, not expressions.
const pyIntMsgSrc = `
import json, sys
out = []
for s in json.loads(sys.argv[1]):
    try:
        int(s)
        out.append({"raised": False, "msg": ""})
    except ValueError as e:
        out.append({"raised": True, "msg": str(e)})
print(json.dumps(out))
`

// TestIntInvalidLiteralMessageIsTruncatedTheWayCPythonTruncatesIt pins the
// `%.200R` in CPython's PyErr_Format.
//
// The port built the message from an untruncated repr, which agrees with
// CPython for every value short enough to matter and diverges at 199
// characters — where CPython's message stops at 240 characters and drops
// the repr's own closing quote. r2 of syshealth found it while checking a
// fixture comment that asserted the opposite ("the int() message embeds the
// repr of the whole offending string"). It was not observable there, since
// every clip that module takes is 120 or 200 and the two messages agree on
// their first 240 characters — which is exactly why it needed a test HERE,
// in the shared helper, rather than a fixture in the module that found it.
//
// The truncation is by CODE POINT, not by byte and not by UTF-16 unit: the
// astral row is the one that tells those three apart.
func TestIntInvalidLiteralMessageIsTruncatedTheWayCPythonTruncatesIt(t *testing.T) {
	rows := []struct {
		name string
		s    string
	}{
		{"short enough that nothing truncates", strings.Repeat("z", 100)},
		{"exactly at the boundary", strings.Repeat("z", 198)},
		{"one past the boundary loses the closing quote", strings.Repeat("z", 199)},
		{"well past the boundary", strings.Repeat("z", 300)},
		{"latin-1 truncates by code point, not byte", strings.Repeat("é", 250)},
		{"astral truncates by code point, not UTF-16 unit",
			strings.Repeat("\U0001F600", 250)},
		{"an apostrophe flips repr to double quotes first",
			"a'" + strings.Repeat("z", 250)},
	}
	vals := make([]string, len(rows))
	for i, r := range rows {
		vals[i] = r.s
	}
	var want []struct {
		Raised bool   `json:"raised"`
		Msg    string `json:"msg"`
	}
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyIntMsgSrc, &want, pyprobe.Arg(t, vals))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}

	// Anti-vacuity: a row CPython accepts would make the comparison below
	// trivially pass, and at least one row must actually be truncated —
	// otherwise this test is a restatement of the untruncated behaviour.
	truncated := 0
	for i, w := range want {
		if !w.Raised {
			t.Fatalf("row %q did not raise in CPython; every row here is "+
				"meant to be an invalid literal", rows[i].name)
		}
		if len([]rune(w.Msg)) == 240 {
			truncated++
		}
	}
	if truncated < 4 {
		t.Fatalf("only %d rows reached CPython's 240-character cap; this "+
			"test exists for the cap and is not exercising it", truncated)
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			_, err := pyval.Int(r.s)
			var pe *pyval.PyErr
			if !errors.As(err, &pe) {
				t.Fatalf("pyval.Int returned %v; CPython raises ValueError", err)
			}
			if pe.Class != "ValueError" {
				t.Fatalf("class %q, CPython says ValueError", pe.Class)
			}
			if pe.Msg != want[i].Msg {
				t.Errorf("message differs\n go: %q (%d runes)\n py: %q (%d runes)",
					pe.Msg, len([]rune(pe.Msg)),
					want[i].Msg, len([]rune(want[i].Msg)))
			}
		})
	}
}

// TestGosSpaceSetIsIntsSpaceSet is the check that lets intStrip be
// strings.TrimSpace instead of a hand-copied table of 25 code points.
//
// It censuses BOTH sides over the whole code space — CPython for the
// characters int() skips, Go for unicode.IsSpace — and fails naming the
// code point if they ever part. The equality is a coincidence of two
// standards agreeing today, not a guarantee; a table would rot silently
// at the next Unicode revision, and this goes red instead.
//
// It also re-measures the FOUR that separate int()'s set from
// str.strip()'s, so the claim in intStrip's comment is a test rather
// than a sentence (L52: a rationale recorded as deliberate is still a
// claim).
func TestGosSpaceSetIsIntsSpaceSet(t *testing.T) {
	// The probe SURROUNDS the digit: `int(ch + "5" + ch) == 5` is true
	// for whitespace and for nothing else. `int(ch + "5")` alone also
	// accepts the sign characters and the digit zero (int("05") is 5),
	// which is how the first draft of this test reported U+002B as
	// whitespace CPython skips.
	out, perr := exec.Command("python3", "-c",
		"import json\n"+
			"skips, strips = [], []\n"+
			"for cp in range(0x110000):\n"+
			"    ch = chr(cp)\n"+
			"    if ch.strip() == '': strips.append(cp)\n"+
			"    try:\n"+
			"        if int(ch + '5' + ch) == 5: skips.append(cp)\n"+
			"    except Exception: pass\n"+
			"print(json.dumps([skips, strips]))").Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var got [][]int
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("probe output was not JSON: %v", err)
	}
	skips, strips := map[rune]bool{}, map[rune]bool{}
	for _, cp := range got[0] {
		skips[rune(cp)] = true
	}
	for _, cp := range got[1] {
		strips[rune(cp)] = true
	}
	if len(skips) == 0 || len(strips) == 0 {
		t.Fatal("the probe found no whitespace at all; it measured nothing")
	}

	for r := rune(0); r <= 0x10FFFF; r++ {
		if unicode.IsSpace(r) != skips[r] {
			t.Fatalf("U+%04X: go unicode.IsSpace=%v, int() skips it=%v — "+
				"intStrip is strings.TrimSpace ONLY while these agree. "+
				"Restore the explicit set in pyint.go and list this code "+
				"point in it.", r, unicode.IsSpace(r), skips[r])
		}
	}

	// The four, by name. If a future CPython starts skipping them too,
	// intStrip's whole reason to exist as a named thing is gone and the
	// comment above it is wrong.
	for _, r := range []rune{0x1c, 0x1d, 0x1e, 0x1f} {
		if !strips[r] || skips[r] {
			t.Errorf("U+%04X: str.strip removes it=%v, int() skips it=%v; "+
				"want true/false — the two predicates have converged and "+
				"intStrip's comment no longer describes CPython", r, strips[r], skips[r])
		}
	}
	if n := len(strips) - len(skips); n != 4 {
		t.Errorf("str.strip's set is %d wider than int()'s, want 4 "+
			"(strip=%d int=%d)", n, len(strips), len(skips))
	}
}
