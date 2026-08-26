package pyval_test

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pySumSrc asks CPython what `sum(list)` answers — the REPR when it works,
// the class and message when it does not.
//
// repr, not a float format: the int/float distinction is half of what is
// being pinned (`sum([])` is `0`, not `0.0`) and a numeric formatter erases
// it. The other half is the residue a compensated sum leaves behind, which
// repr's shortest-roundtrip spelling carries exactly.
const pySumSrc = `
import json, sys
out = []
for expr in json.loads(sys.argv[1]):
    try:
        out.append({"ok": True, "value": repr(sum(eval(expr)))})
    except Exception as e:
        out.append({"ok": False, "cls": type(e).__name__, "msg": str(e)})
print(json.dumps(out))
`

// TestSumMatchesCPython pins pyval.Sum against the interpreter.
//
// The case that made this file necessary is "four ordinary cost rows":
// CPython's sum() has used Neumaier compensation since 3.12, so
// `sum([0.05, 0.01, 0.01, -0.07])` is -3.469446951953614e-18 where a fold
// gives 0.0 — and `round(x, 6)` turns the first into -0.0 and the second
// into 0.0. That is two runtimes writing different bytes into one shared
// ledger for the same four rows, which is the whole reason this package
// exists (adversarial metrics r1 follow-on, 2026-08-26).
func TestSumMatchesCPython(t *testing.T) {
	inf, nan := math.Inf(1), math.NaN()
	type row struct {
		name   string
		goVals []any
		pyExpr string
	}
	rows := []row{
		// The int lane, which stays exact and stays an int.
		{"empty", nil, "[]"},
		{"ints", []any{1, 2, 3}, "[1, 2, 3]"},
		{"ints cancelling", []any{1, -1}, "[1, -1]"},
		{"bools are ints", []any{true, true}, "[True, True]"},
		{"a false is a zero", []any{false, 3}, "[False, 3]"},

		// THE COMPENSATION. Each of these answers differently under a naive
		// left fold, and the first is the shape a real step-costs file has.
		{"four cost rows", []any{0.05, 0.01, 0.01, -0.07},
			"[0.05, 0.01, 0.01, -0.07]"},
		{"a large term cancelling a small one", []any{1e100, 1.0, -1e100},
			"[1e100, 1.0, -1e100]"},
		{"cancellation at 1e16", []any{1e16, 1.0, -1e16},
			"[1e16, 1.0, -1e16]"},
		{"ten tenths", []any{0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1},
			"[0.1] * 10"},

		// THE CROSSING from the int lane to the float lane, in both orders.
		// CPython performs one generic `int + float` and starts compensating
		// after it, so the crossing itself is uncompensated.
		{"int then float", []any{1, 2.5}, "[1, 2.5]"},
		{"float then int", []any{2.5, 1}, "[2.5, 1]"},
		// THE OTHER COMPENSATION ARM. Neumaier picks which operand to
		// recover the lost low bits from by comparing MAGNITUDES, and every
		// cancelling fixture above leads with the large term — so |result|
		// was always >= |x| and the second arm never ran. Collapsing the
		// branch to one arm survived the battery on that list (M121). Here
		// the running total is 1.0 when 1e100 arrives, which is the case the
		// comparison exists for; taking the wrong arm cancels the 1.0 away
		// and answers 0.0.
		{"a small term before the large cancelling pair",
			[]any{1.0, 1e100, -1e100}, "[1.0, 1e100, -1e100]"},
		{"a tiny term before a large cancelling pair",
			[]any{1e-10, 1e10, -1e10}, "[1e-10, 1e10, -1e10]"},
		// Compensation surviving ACROSS a second cancellation, so the
		// carried c is used rather than merely computed.
		{"a term on either side of a cancelling pair",
			[]any{1.0, 1e16, -1e16, 1.0}, "[1.0, 1e16, -1e16, 1.0]"},
		{"ints then a cancelling pair", []any{3, 1e100, 1.0, -1e100},
			"[3, 1e100, 1.0, -1e100]"},

		// SIGNED ZERO. The accumulator starts as int 0, so `0 + -0.0` is
		// +0.0 and the sign is lost at the first step — a port that started
		// at 0.0 and folded would answer -0.0 here.
		{"a lone negative zero", []any{math.Copysign(0, -1)}, "[-0.0]"},
		{"two negative zeros", []any{math.Copysign(0, -1), math.Copysign(0, -1)},
			"[-0.0, -0.0]"},

		// NON-FINITE. Once the running total is inf or nan the compensation
		// term is meaningless and is DISCARDED; adding it would answer nan
		// for the first two.
		{"overflow to infinity", []any{1e308, 1e308, -1e308},
			"[1e308, 1e308, -1e308]"},
		{"infinity then a cancelling pair", []any{inf, 1e100, 1.0, -1e100},
			"[float('inf'), 1e100, 1.0, -1e100]"},
		{"infinities cancelling", []any{inf, math.Inf(-1)},
			"[float('inf'), float('-inf')]"},
		{"a nan poisons the total", []any{nan, 1.0},
			"[float('nan'), 1.0]"},

		// THE RAISE LANE, and the accumulator's type in the message.
		{"a null first", []any{nil, 1.0}, "[None, 1.0]"},
		{"a null after an int", []any{1, nil}, "[1, None]"},
		{"a null after a float", []any{1.5, nil}, "[1.5, None]"},
		{"a numeric-looking string", []any{"1.5"}, `["1.5"]`},
		{"a string after a float", []any{1.5, "x"}, `[1.5, "x"]`},
		{"a list", []any{[]any{1}}, "[[1]]"},
		{"a dict after a float", []any{1.5, map[string]any{"a": 1}},
			`[1.5, {"a": 1}]`},
	}

	exprs := make([]string, len(rows))
	for i, r := range rows {
		exprs[i] = r.pyExpr
	}
	type answer struct {
		OK    bool   `json:"ok"`
		Value string `json:"value"`
		Cls   string `json:"cls"`
		Msg   string `json:"msg"`
	}
	var want []answer
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pySumSrc, &want,
		pyprobe.Arg(t, exprs))
	if len(want) != len(rows) {
		t.Fatalf("probe returned %d answers for %d rows", len(want), len(rows))
	}
	// L1: a corpus that never raised would leave the whole error lane
	// unmeasured, and one that always raised would leave the arithmetic
	// unmeasured. Both halves must be present.
	var raised, returned int
	for _, w := range want {
		if w.OK {
			returned++
		} else {
			raised++
		}
	}
	if raised == 0 || returned == 0 {
		t.Fatalf("the corpus reaches only one lane (%d raised, %d returned)",
			raised, returned)
	}

	for i, r := range rows {
		t.Run(r.name, func(t *testing.T) {
			got, err := pyval.Sum(r.goVals)
			w := want[i]
			if !w.OK {
				if err == nil {
					t.Fatalf("cpython raised %s: %s; go returned %v",
						w.Cls, w.Msg, got)
				}
				var pe *pyval.PyErr
				if !errors.As(err, &pe) {
					t.Fatalf("raised %T (%v), not a *pyval.PyErr", err, err)
				}
				if pe.Class != w.Cls || pe.Msg != w.Msg {
					t.Errorf("raised %s: %s\ncpython  %s: %s",
						pe.Class, pe.Msg, w.Cls, w.Msg)
				}
				return
			}
			if err != nil {
				t.Fatalf("go raised %v; cpython answered %s", err, w.Value)
			}
			// repr on both sides, so `0` and `0.0` are different answers and
			// so are `0.0` and `-0.0`.
			if r := pyval.Repr(got); r != w.Value {
				t.Errorf("sum = %s, cpython %s", r, w.Value)
			}
		})
	}
}

// TestSumIsNotAFold is the standing guard on the one property a reader is
// most likely to "simplify" away — and the only one with no differential
// fixture that fails loudly when they do, because a fold agrees with a
// compensated sum on every well-conditioned input.
//
// It is written against the ALGORITHM rather than an interpreter, on
// purpose: it must keep failing even if the probe is unavailable.
func TestSumIsNotAFold(t *testing.T) {
	got, err := pyval.Sum([]any{1e100, 1.0, -1e100})
	if err != nil {
		t.Fatal(err)
	}
	if got != 1.0 {
		t.Errorf("sum([1e100, 1.0, -1e100]) = %v, want 1 — a naive left fold "+
			"answers 0 here, which is what CPython stopped doing in 3.12", got)
	}
}

// TestSumRejectsUnhashableAndUnsummableAlike is a shape check the metrics
// caller depends on: analyze_step_costs sums `total_tokens` for a whole
// group BEFORE it sums `cost_usd`, so when both fields are bad the error
// the caller sees is the token one. Sum has no say in that ordering — but
// it must not swallow either.
func TestSumRejectsUnhashableAndUnsummableAlike(t *testing.T) {
	for _, bad := range []any{nil, "x", []any{1}, map[string]any{"a": 1},
		pyval.List{1}, pyval.Obj{}} {
		if _, err := pyval.Sum([]any{1, bad}); err == nil {
			t.Errorf("sum([1, %#v]) did not raise", bad)
		}
	}
}

// jsonNumbersSumLikeTheirLiterals pins the decoder seam. Every value the
// metrics readers hand to Sum came out of pyval's JSON reader, which keeps
// numbers as json.Number so the LITERAL decides int-ness — `2` is an int
// and `2.0` is a float even though both fit an int64.
func TestSumOverJSONNumbers(t *testing.T) {
	decode := func(s string) []any {
		var raw []any
		d := json.NewDecoder(strings.NewReader(s))
		d.UseNumber()
		if err := d.Decode(&raw); err != nil {
			t.Fatal(err)
		}
		return raw
	}
	for _, c := range []struct{ in, want string }{
		{"[1, 2, 3]", "6"},
		{"[2.0, 1]", "3.0"},
		{"[1, 2.0]", "3.0"},
		{"[1000, 2000]", "3000"},
	} {
		got, err := pyval.Sum(decode(c.in))
		if err != nil {
			t.Fatalf("%s: %v", c.in, err)
		}
		if r := pyval.Repr(got); r != c.want {
			t.Errorf("sum(%s) = %s, want %s", c.in, r, c.want)
		}
	}
}
