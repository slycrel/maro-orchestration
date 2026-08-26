package metrics

import (
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The SystemMetrics differential.
//
// Every fixture below was written and its CPython answer captured BEFORE
// internal/metrics/sysmetrics.go existed (scratchpad sysmetrics_fixtures.py,
// 2026-08-26), which is the L49 tripwire: ground truth that cannot have been
// derived from the port's own output. The probe source here is that same
// script verbatim, so the capture and this test ask CPython the identical
// question — the frozen capture is not consulted at runtime, CPython is.
//
// The comparison is TYPE-TAGGED on both sides. A differential that renders
// 1 and 1.0 the same way agrees with a port that typed success_rate as an
// integer, and this chunk's whole difficulty is that no value is coerced
// anywhere: `success_rate` is a true division and is 1.0 for one done row,
// `avg_tokens_in` of a `true` is 1.0, and `total_tokens_in` starts an int
// and becomes a float on first float row — which changes what `{:,}` prints.

const sysPySrc = `
import json, sys
import metrics
from memory import Outcome


def enc(v):
    """Type-tagged rendering. A comparison that cannot see int-vs-float
    agrees with a port that typed success_rate as an integer."""
    if v is None:
        return "n"
    if isinstance(v, bool):
        return "b:1" if v else "b:0"
    if isinstance(v, int):
        return "i:%d" % v
    if isinstance(v, float):
        return "f:%.17g" % v
    if isinstance(v, str):
        return "s:" + v
    if isinstance(v, list):
        return ["L"] + [enc(x) for x in v]
    if isinstance(v, dict):
        return ["D"] + [[enc(k), enc(x)] for k, x in v.items()]
    return "?:" + type(v).__name__


def enc_obj(o, fields):
    return ["D"] + [[enc(f), enc(getattr(o, f))] for f in fields]


GM = ["task_type", "total_runs", "success_rate", "avg_elapsed_ms",
      "avg_tokens_in", "avg_tokens_out", "estimated_cost_usd"]
MM = ["model", "total_runs", "total_cost_usd", "total_tokens_in",
      "total_tokens_out"]


def enc_metrics(m):
    return {
        # computed_at is a live clock; pin its SHAPE, not its value.
        "computed_at_len": len(m.computed_at),
        "computed_at_tz": m.computed_at[-6:],
        "total_goals": enc(m.total_goals),
        "overall_success_rate": enc(m.overall_success_rate),
        "by_task_type": ["D"] + [[enc(k), enc_obj(v, GM)]
                                 for k, v in m.by_task_type.items()],
        "most_expensive_goals": enc(m.most_expensive_goals),
        "slowest_goals": enc(m.slowest_goals),
        "failure_patterns": enc(m.failure_patterns),
        "by_model": ["D"] + [[enc(k), enc_obj(v, MM)]
                             for k, v in m.by_model.items()],
        "achieved_count": enc(m.achieved_count),
        "not_achieved_count": enc(m.not_achieved_count),
        "unjudged_count": enc(m.unjudged_count),
        "goal_achieved_rate": enc(m.goal_achieved_rate),
    }


def mk(d):
    """Exactly load_outcomes' construction: known fields only, NO coercion."""
    return Outcome(**{k: d[k] for k in Outcome.__dataclass_fields__ if k in d})


def mk_gm(d):
    return metrics.GoalMetrics(**d)


def mk_mm(d):
    return metrics.ModelMetrics(**d)


def mk_metrics(d):
    """Build a SystemMetrics directly — the report fixtures need control of
    computed_at, and of field types compute_metrics could never produce."""
    kw = dict(d)
    kw["by_task_type"] = {_key(k): mk_gm(v) for k, v in d.get("by_task_type", [])}
    kw["by_model"] = {_key(k): mk_mm(v) for k, v in d.get("by_model", [])}
    return metrics.SystemMetrics(**kw)


def _key(k):
    """JSON has no int keys, so report fixtures spell a key as ["i", 2]."""
    if isinstance(k, list):
        return int(k[1]) if k[0] == "i" else k[1]
    return k


_cases = json.loads(sys.argv[1])
_out = []
for _c in _cases:
    try:
        kind = _c["kind"]
        if kind == "compute":
            _out.append({"ok": enc_metrics(
                metrics.compute_metrics([mk(x) for x in _c["outcomes"]]))})
        elif kind == "patterns":
            _out.append({"ok": enc(
                metrics.identify_expensive_patterns([mk(x) for x in _c["outcomes"]]))})
        elif kind == "report":
            _out.append({"ok": {"report": metrics.format_metrics_report(
                mk_metrics(_c["metrics"]))}})
        elif kind == "compute_report":
            # The two-phase failure: compute must SUCCEED and only the
            # render may die, so they are reported separately.
            m = metrics.compute_metrics([mk(x) for x in _c["outcomes"]])
            r = {"computed": enc_metrics(m)}
            try:
                r["report"] = metrics.format_metrics_report(m)
            except Exception as e:
                r["report_err"] = type(e).__name__ + ": " + str(e)
            _out.append({"ok": r})
        else:
            _out.append({"err": "unknown kind " + kind})
    except Exception as e:
        _out.append({"err": type(e).__name__ + ": " + str(e)})
print(json.dumps(_out))
`

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

type sysCase struct {
	name    string
	payload map[string]any
}

// outcome builds one row. Only the fields a fixture NAMES are sent: absence
// is a distinct input from a zero value at this boundary, and the dataclass
// default is what closes the gap on the far side. The six required fields
// are always present because load_outcomes would otherwise exclude the row
// entirely (record.keepLoadableOutcomes, ported from _rows_as).
func outcome(kv ...any) map[string]any {
	d := map[string]any{"outcome_id": "x", "goal": "g", "task_type": "t",
		"status": "done", "summary": "", "lessons": []any{}}
	for i := 0; i+1 < len(kv); i += 2 {
		d[kv[i].(string)] = kv[i+1]
	}
	return d
}

func rows(os ...map[string]any) []any {
	out := make([]any, 0, len(os))
	for _, o := range os {
		out = append(out, o)
	}
	return out
}

func sysFixtures() []sysCase {
	var cs []sysCase
	compute := func(name string, os ...map[string]any) {
		cs = append(cs, sysCase{name, map[string]any{
			"kind": "compute", "outcomes": rows(os...)}})
	}
	patterns := func(name string, os ...map[string]any) {
		cs = append(cs, sysCase{name, map[string]any{
			"kind": "patterns", "outcomes": rows(os...)}})
	}

	// --- compute_metrics ---------------------------------------------------

	compute("1 success_rate 1 of 2 is 0.5 not 0",
		outcome("status", "done"), outcome("status", "stuck"))
	compute("2 success_rate 1 of 1 is 1.0 not 1", outcome("status", "done"))
	// Costs [1,2,2,1] by token count; ties must keep INSERTION order -> b,c,a,d.
	compute("3 most_expensive tie order is insertion order",
		outcome("goal", "a", "tokens_in", 1000),
		outcome("goal", "b", "tokens_in", 2000),
		outcome("goal", "c", "tokens_in", 2000),
		outcome("goal", "d", "tokens_in", 1000))
	compute("4 goal_achieved is an IDENTITY check",
		outcome("goal_achieved", true), outcome("goal_achieved", false),
		outcome("goal_achieved", 1), outcome("goal_achieved", 0),
		outcome("goal_achieved", "yes"), outcome())
	compute("5 no judged rows means rate None not 0.0", outcome(), outcome())
	compute("6 empty outcomes takes the early return")
	compute("7 goal[:80] is code points not bytes",
		outcome("goal", strings.Repeat("あ", 100)))
	compute("8 model or unknown catches every falsy value",
		outcome("model", ""), outcome("model", nil), outcome(),
		outcome("model", 0), outcome("model", []any{}),
		outcome("model", " "), outcome("model", "x"))
	compute("9 by_task_type and by_model price the same rows differently",
		outcome("model", "claude-opus-4-6", "tokens_in", 1000000,
			"tokens_out", 1000000))
	var six []map[string]any
	for i := 0; i < 6; i++ {
		six = append(six, outcome("goal", fmt.Sprintf("g%d", i),
			"tokens_in", 1000*(i+1), "elapsed_ms", i+1))
	}
	compute("10 both top lists truncate at 5", six...)
	// Fourteen rows in TWO interleaved cost groups — the only shape that
	// exposes Go's unstable sort, MEASURED rather than assumed:
	//
	//	n:              4    12    13    16    40
	//	all equal:    keeps keeps keeps keeps keeps   <- never exposes it
	//	two groups:   keeps keeps SCRAM SCRAM SCRAM
	//
	// Below thirteen elements sort.Slice insertion-sorts, so the four-row
	// tie above cannot tell SliceStable from Slice. And an ALL-EQUAL list of
	// any size cannot either: pdqsort detects the duplicates and takes an
	// equal-partition path that happens to preserve order. The first draft
	// of this fixture was thirteen identical rows and could not fail — the
	// same class of mistake as round 4's still-open M101, and the reason
	// that one needs a two-group fixture too, not merely a bigger one.
	//
	// Both top lists are covered at once: the cost groups and the elapsed
	// groups are the same rows, and only the top five survive, so a
	// scrambled high group changes the rendered list.
	var tied []map[string]any
	for i := 0; i < 14; i++ {
		tokens, elapsed := 1000, 10
		if i%2 == 0 {
			tokens, elapsed = 2000, 20
		}
		tied = append(tied, outcome("goal", fmt.Sprintf("t%02d", i),
			"tokens_in", tokens, "elapsed_ms", elapsed))
	}
	compute("3b two interleaved groups of fourteen keep insertion order",
		tied...)
	// Re-setting an existing key must NOT move it to the end of the dict.
	// Every row calls setdefault on its task_type, so a two-type store with
	// an interleaved third row is enough — and by_task_type renders SORTED,
	// which would hide it, so this leans on by_model (unsorted at equal
	// cost) and on the compute-side encoding, which walks insertion order.
	compute("3c a re-set key keeps its ordinal, it does not move to the end",
		outcome("task_type", "a", "model", "opus"),
		outcome("task_type", "b", "model", "haiku"),
		outcome("task_type", "a", "model", "opus"))
	compute("11 an unhashable task_type raises at COMPUTE",
		outcome("task_type", map[string]any{"a": 1}))
	// Sonnet input is 3.00/1e6, so a count of n costs 3n/1e6. These eight
	// rows are where the compensated sum() in by_task_type and the naive
	// `+=` in by_model land on different floats for the same money.
	var eight []map[string]any
	for _, n := range []int{16667, 3333, 3333, 23333, 1, 1, 1, 1} {
		eight = append(eight, outcome("task_type", "t", "tokens_in", n))
	}
	compute("12 per-type cost is a compensated sum", eight...)

	// --- identify_expensive_patterns ---------------------------------------

	patterns("13 all zero tokens hits the live avg_cost gate",
		outcome(), outcome(), outcome())
	patterns("14 one type at 3x the mean fires the cost rule",
		outcome("task_type", "cheap", "tokens_in", 1000),
		outcome("task_type", "cheap", "tokens_in", 1000),
		outcome("task_type", "pricey", "tokens_in", 6000))
	// avg = (1000+1000+2000)/3; b's avg 2000 is exactly 1.5x it, and `>`
	// must refuse it.
	patterns("15 exactly 1.5x must NOT fire",
		outcome("task_type", "a", "tokens_in", 1000),
		outcome("task_type", "a", "tokens_in", 1000),
		outcome("task_type", "b", "tokens_in", 2000))
	patterns("16 three rows two stuck fires the stuck rule",
		outcome("task_type", "s", "status", "stuck", "tokens_in", 9000),
		outcome("task_type", "s", "status", "stuck", "tokens_in", 1),
		outcome("task_type", "s", "status", "done", "tokens_in", 1))
	patterns("17 two rows both stuck is under the floor of 3",
		outcome("task_type", "s", "status", "stuck", "tokens_in", 9000),
		outcome("task_type", "s", "status", "stuck", "tokens_in", 1))
	patterns("18 exactly half stuck must NOT fire",
		outcome("task_type", "s", "status", "stuck", "tokens_in", 9000),
		outcome("task_type", "s", "status", "stuck", "tokens_in", 1),
		outcome("task_type", "s", "status", "done", "tokens_in", 1),
		outcome("task_type", "s", "status", "done", "tokens_in", 1))
	patterns("19 both rules fire cost lines first then stuck lines",
		outcome("task_type", "hot", "status", "stuck", "tokens_in", 90000),
		outcome("task_type", "hot", "status", "stuck", "tokens_in", 90000),
		outcome("task_type", "hot", "status", "done", "tokens_in", 90000),
		outcome("task_type", "cool", "tokens_in", 1),
		outcome("task_type", "cool", "tokens_in", 1),
		outcome("task_type", "cool", "tokens_in", 1))
	// A negative count makes the mean negative, so the CHEAP type is below
	// 1.5x a negative number and the NORMAL one is above it: the rule fires
	// on the wrong type and prints a negative multiplier.
	patterns("20 a negative token count inverts the ratio",
		outcome("task_type", "cheap", "tokens_in", -5000),
		outcome("task_type", "norm", "tokens_in", 1000),
		outcome("task_type", "norm", "tokens_in", 1000))
	// estimate_cost survives the min() clamp on a str tokens_out and dies
	// at the MULTIPLY, with a message that names neither operand. Only
	// reachable through patterns: compute_metrics sums tokens_out into
	// avg_out first and raises the addition error instead.
	patterns("21c a str tokens_out dies at the multiply, not the clamp",
		outcome("tokens_out", "5"))
	compute("21d the same row through compute dies in sum() instead",
		outcome("tokens_out", "5"))
	// The zero-average gate returns BEFORE both rules, so an all-zero-token
	// store of mostly-stuck runs produces no suggestion at all — even
	// though the stuck rule would otherwise fire. Deleting the gate is
	// invisible without this shape: with any nonzero cost the gate is not
	// reached, and with all-zero costs and no stuck rows both rules are
	// silent anyway.
	patterns("13b the zero-average gate pre-empts the stuck rule",
		outcome("status", "stuck"), outcome("status", "stuck"),
		outcome("status", "done"))
	patterns("21 a NaN cost poisons nothing here but the sort",
		outcome("task_type", "a", "tokens_in", 1000))

	// --- the no-coercion seam: the MESSAGE names which loop ran first ------

	compute("31a a str tokens_in dies in sum(), not in estimate_cost",
		outcome("tokens_in", "5"))
	patterns("31b the same row through patterns dies in min()",
		outcome("tokens_in", "5"))
	// Both fields are bad in DIFFERENT ways, so the message names which
	// line ran first. Two str fields raise the same text either way and pin
	// nothing — a guard that cannot fail (feedback_mutation_from_file).
	compute("32 elapsed_ms (line 553) raises BEFORE tokens_in (line 554)",
		outcome("elapsed_ms", nil, "tokens_in", "5"))
	compute("32b and tokens_in (554) raises before goal[:80] (585)",
		outcome("goal", nil, "tokens_in", "5"))
	compute("33 a None goal is not subscriptable", outcome("goal", nil))
	compute("34 a truthy unhashable model dies at the by_model grouping",
		outcome("model", []any{1}))
	compute("35 a falsy unhashable model is just unknown",
		outcome("model", map[string]any{}))
	compute("36 bools are ints all the way through",
		outcome("tokens_in", true, "tokens_out", true))

	// --- format_metrics_report ---------------------------------------------

	gm := func(kv ...any) map[string]any {
		d := map[string]any{"task_type": "t", "total_runs": 1,
			"success_rate": 1.0, "avg_elapsed_ms": 0.0, "avg_tokens_in": 0.0,
			"avg_tokens_out": 0.0, "estimated_cost_usd": 0.0}
		for i := 0; i+1 < len(kv); i += 2 {
			d[kv[i].(string)] = kv[i+1]
		}
		return d
	}
	mm := func(kv ...any) map[string]any {
		d := map[string]any{"model": "m", "total_runs": 1,
			"total_cost_usd": 0.0, "total_tokens_in": 0, "total_tokens_out": 0}
		for i := 0; i+1 < len(kv); i += 2 {
			d[kv[i].(string)] = kv[i+1]
		}
		return d
	}
	sm := func(name string, kv ...any) {
		d := map[string]any{
			"computed_at": "2026-08-26T12:34:56.789012+00:00", "total_goals": 0,
			"overall_success_rate": 0.0, "by_task_type": []any{},
			"most_expensive_goals": []any{}, "slowest_goals": []any{},
			"failure_patterns": []any{}, "by_model": []any{}}
		for i := 0; i+1 < len(kv); i += 2 {
			d[kv[i].(string)] = kv[i+1]
		}
		cs = append(cs, sysCase{name, map[string]any{
			"kind": "report", "metrics": d}})
	}

	sm("22a computed_at WITH microseconds")
	sm("22b computed_at WITHOUT microseconds",
		"computed_at", "2026-08-26T12:34:56+00:00")
	sm("23 an empty computed_at renders a bare Z", "computed_at", "")
	// computed_at[:19] is nineteen CODE POINTS. Every real stamp is ASCII,
	// so a byte slice agrees on all of them — this is the only fixture that
	// can tell the two apart, and it exists because the mutation battery
	// showed the byte spelling surviving everything else.
	sm("23b computed_at is clipped in code points, not bytes",
		"computed_at", "20２6-08-26T12:34:56+00:00")
	sm("24a elapsed_ms int", "slowest_goals",
		[]any{map[string]any{"elapsed_ms": 1234, "goal": "g"}})
	sm("24b elapsed_ms float", "slowest_goals",
		[]any{map[string]any{"elapsed_ms": 1234.0, "goal": "g"}})
	sm("24c elapsed_ms None", "slowest_goals",
		[]any{map[string]any{"elapsed_ms": nil, "goal": "g"}})
	sm("25 a str cost_usd RAISES where elapsed_ms would not",
		"most_expensive_goals",
		[]any{map[string]any{"cost_usd": "3", "goal": "g"}})
	cs = append(cs, sysCase{"26 mixed key types raise at RENDER only",
		map[string]any{"kind": "compute_report", "outcomes": rows(
			outcome("task_type", "a"), outcome("task_type", 2))}})
	sm("27a the token sum is a thousands-grouped int",
		"by_model", []any{[]any{"m", mm("total_tokens_in", 1234567)}})
	sm("27b a float token sum groups differently",
		"by_model", []any{[]any{"m", mm("total_tokens_in", 1234.5)}})
	sm("28 every optional section empty")
	for _, r := range []float64{1.0, 0.0, -0.25} {
		sm(fmt.Sprintf("29 overall_success_rate %v", r),
			"overall_success_rate", r)
	}
	sm("30 equal model costs keep insertion order", "by_model", []any{
		[]any{"first", mm("total_cost_usd", 5.0)},
		[]any{"second", mm("total_cost_usd", 5.0)}})
	// The by_model sort needs the same two-group shape as fixture 3b for
	// the same measured reason: two tied entries are below Go's
	// insertion-sort threshold and cannot expose an unstable sort.
	var manyModels []any
	for i := 0; i < 14; i++ {
		cost := 1.0
		if i%2 == 0 {
			cost = 5.0
		}
		manyModels = append(manyModels, []any{fmt.Sprintf("m%02d", i),
			mm("total_cost_usd", cost)})
	}
	sm("30b fourteen models in two cost groups keep insertion order",
		"by_model", manyModels)
	sm("26b an int task_type key alone renders fine",
		"by_task_type", []any{[]any{[]any{"i", 2}, gm("task_type", 2)}})
	// A float token total wide enough that repr() goes scientific — the
	// lane GroupedF(f, -1) would have grouped into twenty-one digits.
	// 1e21, not 1e20: encoding/json writes 1e20 as twenty-one digits, which
	// CPython loads as an INT and groups. 1e21 is the first magnitude Go
	// writes in exponent form, so both sides get a float and the float
	// lane's scientific branch is actually reached.
	sm("27c a float token sum big enough for scientific notation",
		"by_model", []any{[]any{"m", mm("total_tokens_in", 1e21)}})
	sm("27d a float token sum small enough for scientific notation",
		"by_model", []any{[]any{"m", mm("total_tokens_in", 1e-7)}})
	// A magnitude encoding/json writes without an exponent, so BOTH sides
	// see an integer literal — and one that fits int64, which the >2^63
	// case deliberately does not (see TestWideIntegerTokenTotalIsANamed
	// Divergence).
	sm("27e a wide integer token total still groups",
		"by_model", []any{[]any{"m", mm("total_tokens_in",
			1234567890123456789)}})
	return cs
}

// sysNaNFixture is fixture 21b, held out of the shared list: it is a NAMED
// DIVERGENCE, not an agreement. CPython's list.sort leaves a NaN key where
// the partition happens to put it — mc, ma, mb for costs 5.0, nan, 1.0 —
// and no cheap Go spelling reproduces that (measured: sort.Float64s
// disagrees with list.sort on 136 of 153 NaN-bearing lists, SliceStable
// with a `<` comparator on 78). Porting timsort into pyval is filed at the
// top of BACKLOG.md with this as one of its two named waiting consumers.
//
// Pinned rather than skipped: the assertion below goes RED the day the
// timsort lands, which is exactly when this test should be revisited.
func TestByModelNaNSortIsANamedDivergence(t *testing.T) {
	nan := math.NaN()
	m := &SystemMetrics{
		ComputedAt: "2026-08-26T12:34:56.789012+00:00",
		ByTaskType: newPyDict[*GoalMetrics](),
		ByModel:    newPyDict[*ModelMetrics](),
	}
	for _, r := range []struct {
		name string
		cost float64
	}{{"ma", nan}, {"mb", 1.0}, {"mc", 5.0}} {
		if err := m.ByModel.set(r.name, &ModelMetrics{Model: r.name,
			TotalRuns: 1, TotalCostUSD: r.cost,
			TotalTokensIn: 0, TotalTokensOut: 0}); err != nil {
			t.Fatal(err)
		}
	}
	out, err := FormatMetricsReport(m)
	if err != nil {
		t.Fatal(err)
	}
	var got []string
	for _, ln := range strings.Split(out, "\n") {
		if i := strings.Index(ln, ":"); strings.HasPrefix(ln, "  m") && i > 0 {
			got = append(got, strings.TrimSpace(ln[:i]))
		}
	}
	// MEASURED, both sides, not predicted: CPython prints mc, ma, mb
	// (`sorted(key=lambda x: -x[1])` over nan, 1.0, 5.0), and this port
	// prints the line below. Neither is "right"; the divergence is the
	// fact, and the first draft of this test guessed the Go side wrong.
	want := []string{"ma", "mc", "mb"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("the named divergence moved: got %v, want %v.\n"+
			"If pyval grew a real list.sort, delete this test and fold the "+
			"NaN case back into sysFixtures as fixture 21b.", got, want)
	}
}

// ---------------------------------------------------------------------------
// The Go side of the encoder
// ---------------------------------------------------------------------------

// fmt17py is `"%.17g" % f`. Go's %.17g agrees digit for digit EXCEPT on the
// non-finites, where it spells "NaN"/"+Inf"/"-Inf" against Python's
// "nan"/"inf"/"-inf" — measured over the fixture floats, not assumed.
func fmt17py(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	}
	return fmt.Sprintf("%.17g", f)
}

func sysEnc(v any) any {
	switch t := v.(type) {
	case nil:
		return "n"
	case bool:
		if t {
			return "b:1"
		}
		return "b:0"
	case int:
		return fmt.Sprintf("i:%d", t)
	case int64:
		return fmt.Sprintf("i:%d", t)
	case float64:
		return "f:" + fmt17py(t)
	case string:
		return "s:" + t
	case []any:
		out := []any{"L"}
		for _, x := range t {
			out = append(out, sysEnc(x))
		}
		return out
	case []string:
		out := []any{"L"}
		for _, x := range t {
			out = append(out, sysEnc(x))
		}
		return out
	case []pyval.Obj:
		out := []any{"L"}
		for _, x := range t {
			out = append(out, sysEnc(x))
		}
		return out
	case pyval.Obj:
		out := []any{"D"}
		for _, f := range t {
			out = append(out, []any{sysEnc(f.Key), sysEnc(f.Val)})
		}
		return out
	case json.Number:
		// Only reachable if a row's number escaped pyval.Plain, which would
		// itself be the bug. Tag it distinctly rather than silently
		// resolving it to whichever of int/float looks right.
		return "?:json.Number"
	}
	return "?:" + pyval.TypeName(v)
}

// sysEncMetrics mirrors the probe's enc_metrics field for field, INCLUDING
// the order of the two dataclass field lists — a port that renamed a field
// would otherwise pass by encoding a different one.
func sysEncMetrics(m *SystemMetrics) map[string]any {
	byType := []any{"D"}
	for i := 0; i < m.ByTaskType.Len(); i++ {
		k, gm := m.ByTaskType.At(i)
		byType = append(byType, []any{sysEnc(k), []any{"D",
			[]any{"s:task_type", sysEnc(gm.TaskType)},
			[]any{"s:total_runs", sysEnc(gm.TotalRuns)},
			[]any{"s:success_rate", sysEnc(gm.SuccessRate)},
			[]any{"s:avg_elapsed_ms", sysEnc(gm.AvgElapsedMS)},
			[]any{"s:avg_tokens_in", sysEnc(gm.AvgTokensIn)},
			[]any{"s:avg_tokens_out", sysEnc(gm.AvgTokensOut)},
			[]any{"s:estimated_cost_usd", sysEnc(gm.EstimatedCostUSD)},
		}})
	}
	byModel := []any{"D"}
	for i := 0; i < m.ByModel.Len(); i++ {
		k, mm := m.ByModel.At(i)
		byModel = append(byModel, []any{sysEnc(k), []any{"D",
			[]any{"s:model", sysEnc(mm.Model)},
			[]any{"s:total_runs", sysEnc(mm.TotalRuns)},
			[]any{"s:total_cost_usd", sysEnc(mm.TotalCostUSD)},
			[]any{"s:total_tokens_in", sysEnc(mm.TotalTokensIn)},
			[]any{"s:total_tokens_out", sysEnc(mm.TotalTokensOut)},
		}})
	}
	var rate any
	if m.GoalAchievedRate != nil {
		rate = *m.GoalAchievedRate
	}
	// failure_patterns is []string in Go and a list in Python; nil and an
	// empty list encode alike (["L"]), which is correct — Python's is [].
	fp := []any{"L"}
	for _, p := range m.FailurePatterns {
		fp = append(fp, sysEnc(p))
	}
	tz := m.ComputedAt
	if len(tz) > 6 {
		tz = tz[len(tz)-6:]
	}
	return map[string]any{
		"computed_at_len":      float64(len([]rune(m.ComputedAt))),
		"computed_at_tz":       tz,
		"total_goals":          sysEnc(m.TotalGoals),
		"overall_success_rate": sysEnc(m.OverallSuccessRate),
		"by_task_type":         byType,
		"most_expensive_goals": sysEnc(m.MostExpensiveGoals),
		"slowest_goals":        sysEnc(m.SlowestGoals),
		"failure_patterns":     fp,
		"by_model":             byModel,
		"achieved_count":       sysEnc(m.AchievedCount),
		"not_achieved_count":   sysEnc(m.NotAchievedCount),
		"unjudged_count":       sysEnc(m.UnjudgedCount),
		"goal_achieved_rate":   sysEnc(rate),
	}
}

// pyErrText is `type(e).__name__ + ": " + str(e)`, the probe's own spelling.
func pyErrText(err error) string {
	if c := pyval.ClassOf(err); c != "" {
		return c + ": " + err.Error()
	}
	return "GoError: " + err.Error()
}

// sysRows decodes a fixture's outcome dicts through the SAME typing
// record.LoadOutcomes applies — pyval.Plain over an ordered decode, so an
// integral literal is an int and everything else a float64. Round-tripping
// through encoding/json instead would make every number a float64 and hand
// the port `1200.0` where CPython has the int `1200`.
func sysRows(t *testing.T, raw []any) []map[string]any {
	t.Helper()
	out := make([]map[string]any, 0, len(raw))
	for _, r := range raw {
		b, err := json.Marshal(r)
		if err != nil {
			t.Fatal(err)
		}
		v, err := pyval.LoadsOrdered(string(b))
		if err != nil {
			t.Fatal(err)
		}
		m, ok := pyval.Plain(v).(map[string]any)
		if !ok {
			t.Fatalf("fixture row is not an object: %s", b)
		}
		out = append(out, m)
	}
	return out
}

// sysMetricsFromFixture builds a SystemMetrics directly, for the report
// fixtures — they need control of computed_at and of field types
// compute_metrics could never produce (a str cost_usd, a None elapsed_ms).
func sysMetricsFromFixture(t *testing.T, fixture map[string]any) *SystemMetrics {
	t.Helper()
	// Route the WHOLE fixture through the store's own typing before reading
	// it, exactly as sysRows does for outcome rows. Reading the Go literals
	// directly is what fixture 27c caught on its first run: `1e20` is a Go
	// float64, but encoding/json writes it as `100000000000000000000` and
	// CPython loads that as an INT — so the two sides were handed different
	// values and the harness reported it as a port bug. A differential's
	// inputs have to cross the same wire its subject does.
	b, err := json.Marshal(fixture)
	if err != nil {
		t.Fatal(err)
	}
	dec, err := pyval.LoadsOrdered(string(b))
	if err != nil {
		t.Fatal(err)
	}
	d, ok := pyval.Plain(dec).(map[string]any)
	if !ok {
		t.Fatalf("report fixture is not an object: %s", b)
	}
	key := func(k any) any {
		// JSON has no int keys, so a report fixture spells one as ["i", 2].
		if l, ok := k.([]any); ok && len(l) == 2 {
			if l[0] == "i" {
				f, _ := pyval.Float(l[1])
				return int(f)
			}
			return l[1]
		}
		return k
	}
	num := func(v any) float64 { f, _ := pyval.Float(v); return f }
	iv := func(v any) int { f, _ := pyval.Float(v); return int(f) }

	m := &SystemMetrics{
		ComputedAt:         d["computed_at"].(string),
		TotalGoals:         iv(d["total_goals"]),
		OverallSuccessRate: num(d["overall_success_rate"]),
		ByTaskType:         newPyDict[*GoalMetrics](),
		ByModel:            newPyDict[*ModelMetrics](),
	}
	for _, e := range d["by_task_type"].([]any) {
		p := e.([]any)
		g := p[1].(map[string]any)
		if err := m.ByTaskType.set(key(p[0]), &GoalMetrics{
			TaskType: g["task_type"], TotalRuns: iv(g["total_runs"]),
			SuccessRate:      num(g["success_rate"]),
			AvgElapsedMS:     num(g["avg_elapsed_ms"]),
			AvgTokensIn:      num(g["avg_tokens_in"]),
			AvgTokensOut:     num(g["avg_tokens_out"]),
			EstimatedCostUSD: num(g["estimated_cost_usd"]),
		}); err != nil {
			t.Fatal(err)
		}
	}
	for _, e := range d["by_model"].([]any) {
		p := e.([]any)
		mo := p[1].(map[string]any)
		if err := m.ByModel.set(key(p[0]), &ModelMetrics{
			Model: mo["model"], TotalRuns: iv(mo["total_runs"]),
			TotalCostUSD:   num(mo["total_cost_usd"]),
			TotalTokensIn:  mo["total_tokens_in"],
			TotalTokensOut: mo["total_tokens_out"],
		}); err != nil {
			t.Fatal(err)
		}
	}
	// The rows are already store-typed by the whole-fixture decode above;
	// re-decoding here would be a second, differently-ordered pass. Keys
	// come back in the order the fixture wrote them because LoadsOrdered
	// kept them, and the ORDER is what the render walks.
	toObjs := func(v any, ordered []any) []pyval.Obj {
		var out []pyval.Obj
		for i := range v.([]any) {
			o := pyval.Obj{}
			for _, f := range ordered[i].(pyval.Obj) {
				o.Set(f.Key, pyval.Plain(f.Val))
			}
			out = append(out, o)
		}
		return out
	}
	orderedList := func(name string) []any {
		var out []any
		for _, e := range dec.(pyval.Obj) {
			if e.Key == name {
				for _, x := range e.Val.(pyval.List) {
					out = append(out, x)
				}
			}
		}
		return out
	}
	m.MostExpensiveGoals = toObjs(d["most_expensive_goals"],
		orderedList("most_expensive_goals"))
	m.SlowestGoals = toObjs(d["slowest_goals"], orderedList("slowest_goals"))
	for _, p := range d["failure_patterns"].([]any) {
		m.FailurePatterns = append(m.FailurePatterns, p.(string))
	}
	return m
}

// sysRun answers one fixture the way the probe does: {"ok": ...} or
// {"err": "TypeError: ..."}.
func sysRun(t *testing.T, c sysCase) map[string]any {
	t.Helper()
	switch c.payload["kind"] {
	case "compute":
		raw, _ := c.payload["outcomes"].([]any)
		m, err := ComputeMetrics(sysRows(t, raw))
		if err != nil {
			return map[string]any{"err": pyErrText(err)}
		}
		return map[string]any{"ok": sysEncMetrics(m)}
	case "patterns":
		raw, _ := c.payload["outcomes"].([]any)
		ps, err := IdentifyExpensivePatterns(sysRows(t, raw))
		if err != nil {
			return map[string]any{"err": pyErrText(err)}
		}
		out := []any{"L"}
		for _, p := range ps {
			out = append(out, sysEnc(p))
		}
		return map[string]any{"ok": out}
	case "report":
		m := sysMetricsFromFixture(t, c.payload["metrics"].(map[string]any))
		r, err := FormatMetricsReport(m)
		if err != nil {
			return map[string]any{"err": pyErrText(err)}
		}
		return map[string]any{"ok": map[string]any{"report": r}}
	case "compute_report":
		raw, _ := c.payload["outcomes"].([]any)
		m, err := ComputeMetrics(sysRows(t, raw))
		if err != nil {
			return map[string]any{"err": pyErrText(err)}
		}
		res := map[string]any{"computed": sysEncMetrics(m)}
		r, rerr := FormatMetricsReport(m)
		if rerr != nil {
			res["report_err"] = pyErrText(rerr)
		} else {
			res["report"] = r
		}
		return map[string]any{"ok": res}
	}
	t.Fatalf("unknown fixture kind %v", c.payload["kind"])
	return nil
}

// ---------------------------------------------------------------------------
// The differential
// ---------------------------------------------------------------------------

func TestSystemMetricsMatchesCPython(t *testing.T) {
	cases := sysFixtures()
	payload := make([]any, len(cases))
	for i, c := range cases {
		payload[i] = c.payload
	}
	p := pyprobe.Probe{Marker: "metrics.py"}
	var want []map[string]any
	p.RunJSON(t, sysPySrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("CPython answered %d fixtures, sent %d", len(want), len(cases))
	}

	// ANTI-VACUITY. A differential over fixtures that all raise, or all
	// return nothing, is green against a port that does nothing at all.
	// These counts are the floor the fixture list is built to clear.
	var raised, answered int
	for _, w := range want {
		if _, bad := w["err"]; bad {
			raised++
		} else {
			answered++
		}
	}
	if raised < 5 || answered < 20 {
		t.Fatalf("the fixture set stopped exercising both lanes: %d raises, "+
			"%d answers — a differential where one lane is empty passes "+
			"against a port that only implements the other", raised, answered)
	}

	mismatches := 0
	for i, c := range cases {
		got := sysRun(t, c)
		gotN := normalize(t, got)
		wantN := normalize(t, want[i])
		// computed_at is a live clock on the Go side and a live clock on
		// the CPython side, a millisecond apart. Its VALUE cannot match;
		// its shape must. isoformat drops the microseconds when they are
		// exactly zero, so both 25 and 32 are honest lengths and a
		// one-in-a-million tick would otherwise make this flaky.
		reconcileClock(gotN, wantN)
		if !reflect.DeepEqual(gotN, wantN) {
			mismatches++
			gb, _ := json.MarshalIndent(gotN, "", " ")
			wb, _ := json.MarshalIndent(wantN, "", " ")
			t.Errorf("fixture %q\n  go:     %s\n  python: %s",
				c.name, gb, wb)
		}
	}
	if mismatches > 0 {
		t.Logf("%d of %d fixtures disagree", mismatches, len(cases))
	}
}

// normalize round-trips a side through JSON so both are the same shape of
// decoded value (every number a float64, every list a []any) and DeepEqual
// compares content rather than Go types.
func normalize(t *testing.T, v any) any {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	var out any
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// reconcileClock replaces both sides' computed_at_len with a verdict, after
// checking each is one of the two lengths isoformat can produce. It never
// copies one side's value onto the other — a port that emitted a 40-char
// stamp still fails.
func reconcileClock(got, want any) {
	var fix func(v any)
	fix = func(v any) {
		m, ok := v.(map[string]any)
		if !ok {
			return
		}
		if inner, ok := m["ok"].(map[string]any); ok {
			fix(inner)
			if c, ok := inner["computed"].(map[string]any); ok {
				fix(c)
			}
		}
		n, ok := m["computed_at_len"].(float64)
		if !ok {
			return
		}
		// 32 with microseconds, 25 without. 0 is the report fixtures'
		// empty stamp, which is a fixed input rather than a clock.
		m["computed_at_len"] = n == 25 || n == 32
	}
	fix(got)
	fix(want)
}

// TestWideIntegerTokenTotalIsANamedDivergence pins the second divergence
// this chunk found, at the layer where it actually lives.
//
// CPython's int is arbitrary precision, so a stored `100000000000000000000`
// is an int and `{:,}` groups it into twenty-one digits. pyval.Plain resolves
// an integral literal through ParseInt, which is int64, so anything past
// 2^63 falls through to float64 — and the float lane renders repr(1e20),
// which is "1e+20". Same store, same field, two different report lines.
//
// It is NOT worked around here. The fix belongs in pyval (a big-int lane
// beside int/float64), it would change every reader in the port at once,
// and a local patch in the renderer would hide it from the other readers
// that have the same hole. Filed in BACKLOG.md.
//
// The assertion is on the DIVERGENT value on purpose: it goes red the day
// pyval grows big ints, which is when this comment stops being true.
func TestWideIntegerTokenTotalIsANamedDivergence(t *testing.T) {
	dec, err := pyval.LoadsOrdered(`{"n": 100000000000000000000}`)
	if err != nil {
		t.Fatal(err)
	}
	v := pyval.Plain(dec).(map[string]any)["n"]
	if _, isInt := pyIntOf(v); isInt {
		t.Fatalf("pyval now types a >2^63 literal as an int (%T) — the "+
			"divergence is fixed. Delete this test and fold the case back "+
			"into sysFixtures as 27e's big sibling.", v)
	}
	if got := groupedAny(v); got != "1e+20" {
		t.Fatalf("groupedAny(%v) = %q, want %q (CPython prints "+
			"%q)", v, got, "1e+20", "100,000,000,000,000,000,000")
	}
	// The anti-vacuity half: the SAME renderer must still group a total
	// that does fit, or this test would pass against a renderer that never
	// groups anything.
	if got := groupedAny(1234567890123456789); got != "1,234,567,890,123,456,789" {
		t.Fatalf("the int lane stopped grouping: %q", got)
	}
}
