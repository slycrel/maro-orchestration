package metrics

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// metrics.py's SystemMetrics half: compute_metrics, get_metrics,
// identify_expensive_patterns and format_metrics_report.
//
// The hard part of this chunk is NOT the arithmetic. It is that every value
// arrives from a JSONL store with no coercion whatsoever, and the two
// runtimes must agree about which rows are impossible as precisely as they
// agree about the answers. Six decisions below were made from measurement
// before a line was written; each names the fixture that fails if it is
// wrong (see sysmetrics_diff_test.go).

// ---------------------------------------------------------------------------
// The row view
// ---------------------------------------------------------------------------

// outcomeRow is one loaded row, and it is a map rather than a struct ON
// PURPOSE.
//
// `load_outcomes` builds `Outcome(**{k: d[k] for k in fields if k in d})`,
// which applies the dataclass DEFAULTS and no conversions at all. A typed
// struct loses three things this chunk depends on:
//
//   - `goal_achieved` is `Optional[bool]` tested with `is True` / `is False`,
//     so a JSON `1` or `0` is UNJUDGED. A Go `bool` collapses that.
//   - `tokens_in` typed `int` invents a coercion CPython does not do; the
//     store can hold `"5"`, and CPython raises rather than parsing.
//   - `model` typed `string` cannot hold the `0`, `[]` or `nil` that all
//     collapse to "unknown", nor the `[1]` that raises.
//
// record.LoadOutcomes already types numbers CPython's way (int for an
// integral literal, float64 otherwise) and already applies the dataclass
// FILTER, so a row missing one of the six required fields never arrives.
type outcomeRow map[string]any

// dataclass defaults for the fields this chunk reads. Absence is a distinct
// input from a zero value ONLY until this is applied; afterwards `{}` and
// `{"tokens_in": 0}` must be indistinguishable, which is what CPython's
// dataclass does.
func (r outcomeRow) field(name string) any {
	if v, ok := r[name]; ok {
		return v
	}
	switch name {
	case "tokens_in", "tokens_out", "elapsed_ms":
		return 0
	case "model":
		return ""
	case "goal_achieved":
		return nil
	}
	return nil
}

// ---------------------------------------------------------------------------
// An ordered dict keyed by anything hashable
// ---------------------------------------------------------------------------

// pyDict is a Python dict: insertion-ordered, keyed by any hashable value.
//
// pyval.Obj is ordered but STRING-keyed, and that is not enough here.
// `by_task_type` is keyed by whatever the store put in `task_type`, and a
// measured fixture proves an int key survives compute and only kills the
// RENDER. An unhashable key raises at the setdefault, which is where CPython
// raises it, not at some later normalisation.
//
// Kept in this package because it is the first consumer. The second one is
// where it moves to pyval.
type pyDict[T any] struct {
	keys  []any
	canon []string
	vals  []T
	idx   map[string]int
}

func newPyDict[T any]() *pyDict[T] {
	return &pyDict[T]{idx: map[string]int{}}
}

func (d *pyDict[T]) get(key any) (T, bool, error) {
	var zero T
	c, ok := pyval.HashKey(key)
	if !ok {
		return zero, false, &pyval.PyErr{Class: "TypeError",
			Msg: pyval.UnhashableKeyMsg(key)}
	}
	i, hit := d.idx[c]
	if !hit {
		return zero, false, nil
	}
	return d.vals[i], true, nil
}

// set is `d[key] = val`.
//
// UNREACHABLE-GUARD NOTE (battery M40): every caller does a get() first, and
// get() raises on an unhashable key, so this raise cannot currently fire. It
// stays because it states the RULE — a dict refuses an unhashable key at the
// assignment, not somewhere downstream — where deleting it would make the
// type's contract depend on the discipline of its callers. A mutant that
// removes it survives, and that is honest, not a test gap.
func (d *pyDict[T]) set(key any, val T) error {
	c, ok := pyval.HashKey(key)
	if !ok {
		return &pyval.PyErr{Class: "TypeError",
			Msg: pyval.UnhashableKeyMsg(key)}
	}
	if i, hit := d.idx[c]; hit {
		d.vals[i] = val
		return nil
	}
	d.idx[c] = len(d.keys)
	d.keys = append(d.keys, key)
	d.canon = append(d.canon, c)
	d.vals = append(d.vals, val)
	return nil
}

func (d *pyDict[T]) Len() int { return len(d.keys) }

func (d *pyDict[T]) At(i int) (any, T) { return d.keys[i], d.vals[i] }

// sortedKeys is `sorted(d.items())`, which compares the ORIGINAL keys under
// Python's `<` and raises on a mixed-type set. That raise happens at RENDER
// time, after compute_metrics has already returned a valid object — the two
// phases fail independently and a port must let the first one succeed.
func (d *pyDict[T]) sortedIndices() ([]int, error) {
	order := make([]int, len(d.keys))
	for i := range order {
		order[i] = i
	}
	var failure error
	// A str key and an int key have no ordering in Python. Detect it up
	// front rather than from inside the comparator, because a sort whose
	// comparator "returns false on error" silently produces an order.
	var sawStr, sawNum bool
	for _, k := range d.keys {
		switch k.(type) {
		case string:
			sawStr = true
		default:
			if isNumber(k) {
				sawNum = true
			}
		}
	}
	if sawStr && sawNum {
		return nil, &pyval.PyErr{Class: "TypeError",
			Msg: "'<' not supported between instances of 'int' and 'str'"}
	}
	sort.SliceStable(order, func(a, b int) bool {
		ka, kb := d.keys[order[a]], d.keys[order[b]]
		if sa, ok := ka.(string); ok {
			if sb, ok := kb.(string); ok {
				return sa < sb
			}
		}
		if isNumber(ka) && isNumber(kb) {
			fa, _ := pyval.Float(ka)
			fb, _ := pyval.Float(kb)
			return fa < fb
		}
		if failure == nil {
			failure = &pyval.PyErr{Class: "TypeError",
				Msg: fmt.Sprintf(
					"'<' not supported between instances of '%s' and '%s'",
					pyval.TypeName(kb), pyval.TypeName(ka))}
		}
		return false
	})
	if failure != nil {
		return nil, failure
	}
	return order, nil
}

// isNumber reports whether a value is a Python NUMBER — the question every
// gate in this file is really asking.
//
// It deliberately does NOT delegate to pyval.Float, which is `float(x)` and
// so answers TRUE for the string "5". That is the correct answer to a
// different question: `float("5")` is 5.0, but `0 + "5"` raises and
// `min("5", 0)` raises, and this file's entire error surface is those
// raises. Routing a gate through Float would silently coerce exactly the
// rows the fixtures exist to catch.
//
// bool IS a number here, because it is one in Python: a row with
// `tokens_in: true` gives avg_tokens_in == 1.0, measured.
func isNumber(v any) bool {
	switch t := v.(type) {
	case bool, int, int64, float64:
		return true
	case json.Number:
		if _, ok := pyval.IsInt(t); ok {
			return true
		}
		_, err := t.Float64()
		return err == nil || errors.Is(err, strconv.ErrRange)
	}
	return false
}

// pyIntOf reports whether a value is a Python INT (as opposed to a float),
// which decides which lane `{:,}` takes. json.Number carries the literal, so
// `1000.0` stays a float even though it is integral — same rule pyval.IsInt
// states, extended to the Go-native ints an accumulator produces.
func pyIntOf(v any) (int64, bool) {
	switch t := v.(type) {
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case int:
		return int64(t), true
	case int64:
		return t, true
	case json.Number:
		if i, ok := pyval.IsInt(t); ok {
			return int64(i), true
		}
	}
	return 0, false
}

// ---------------------------------------------------------------------------
// Result types
// ---------------------------------------------------------------------------

type GoalMetrics struct {
	TaskType         any
	TotalRuns        int
	SuccessRate      float64
	AvgElapsedMS     float64
	AvgTokensIn      float64
	AvgTokensOut     float64
	EstimatedCostUSD float64
}

type ModelMetrics struct {
	Model         any
	TotalRuns     int
	TotalCostUSD  float64
	TotalTokensIn any
	// TotalTokensIn/Out are `any` and not int because they START as int 0
	// and become floats the moment a float row is added — and the report
	// renders their SUM with `{:,}`, whose int lane prints `1,234,567` and
	// whose float lane prints `1,234.5`.
	TotalTokensOut any
}

type SystemMetrics struct {
	ComputedAt         string
	TotalGoals         int
	OverallSuccessRate float64
	ByTaskType         *pyDict[*GoalMetrics]
	MostExpensiveGoals []pyval.Obj
	SlowestGoals       []pyval.Obj
	FailurePatterns    []string
	ByModel            *pyDict[*ModelMetrics]
	AchievedCount      int
	NotAchievedCount   int
	UnjudgedCount      int
	// GoalAchievedRate is a POINTER because Python's is Optional[float] and
	// None is not 0.0: "nothing in the window was judged" and "everything
	// judged failed" are different facts, and the report prints neither.
	GoalAchievedRate *float64
}

// ---------------------------------------------------------------------------
// estimate_cost, in the untyped lane
// ---------------------------------------------------------------------------

// estimateCostAny is estimate_cost over values that arrive from the store.
//
// The typed EstimateCost stays for typed callers and is NOT reimplemented in
// terms of this one; a differential sweeps the whole pricing table to prove
// they agree, because two spellings of one formula is the hazard this file
// warns about elsewhere.
//
// Three DISTINCT errors, chosen by which parameter took the bad value —
// measured, not assumed:
//
//	tokens_in  bad -> "'<' not supported between instances of 'X' and 'int'"
//	tokens_out bad -> "can't multiply sequence by non-int of type 'float'"
//	cache_read bad -> "'<' not supported between instances of 'int' and 'X'"
//
// The cache_read lane is UNREACHABLE from this file: every call site passes
// a literal 0. It is carried for the same reason as ratesFor's collapse —
// estimate_cost is public in Python and the step-cost recorder passes a
// stored cache count.
//
// tokens_in dies inside `min(cache_read_tokens, tokens_in)`, which compares
// `tokens_in < cache_read_tokens` and so names the operands in that order;
// cache_read_tokens dies in the same min with the operands the other way
// round; tokens_out survives the clamp entirely and dies at the multiply. A
// single generic TypeError matches none of them.
func estimateCostAny(tokensIn, tokensOut, model, cacheRead any) (float64, error) {
	costIn, costOut, err := ratesFor(model)
	if err != nil {
		return 0, err
	}
	// `max(0, min(cache_read_tokens, tokens_in))`. min compares b < a.
	if !isNumber(tokensIn) {
		return 0, &pyval.PyErr{Class: "TypeError",
			Msg: fmt.Sprintf(
				"'<' not supported between instances of '%s' and 'int'",
				pyval.TypeName(tokensIn))}
	}
	if !isNumber(cacheRead) {
		return 0, &pyval.PyErr{Class: "TypeError",
			Msg: fmt.Sprintf(
				"'<' not supported between instances of 'int' and '%s'",
				pyval.TypeName(cacheRead))}
	}
	if !isNumber(tokensOut) {
		return 0, &pyval.PyErr{Class: "TypeError",
			Msg: "can't multiply sequence by non-int of type 'float'"}
	}
	in, _ := pyval.Float(tokensIn)
	out, _ := pyval.Float(tokensOut)
	cache, _ := pyval.Float(cacheRead)
	if cache > in {
		cache = in
	}
	if cache < 0 {
		cache = 0
	}
	fresh := in - cache
	return (fresh * costIn / 1_000_000) +
		(cache * costIn * CacheReadMultiplier / 1_000_000) +
		(out * costOut / 1_000_000), nil
}

// ratesFor is `COST_BY_MODEL.get(model or "", {})`.
//
// A FALSY model — `""`, `0`, `[]`, `{}`, None — becomes `""` and prices at
// the default. A TRUTHY UNHASHABLE one reaches the dict lookup and raises.
// Measured both ways: estimate_cost(1000, 0, []) is 0.003 and
// estimate_cost(1000, 0, [1]) raises.
//
// UNREACHABLE-GUARD NOTE (battery M26/M27): from THIS file's callers the
// collapse is dead. compute_metrics has already turned every falsy model
// into "unknown" and then passes nil, and nil and "" price identically —
// so a mutant deleting the collapse survives. It stays because
// estimate_cost is a PUBLIC function in Python with callers outside this
// module, and reproducing it only for the paths one caller happens to
// take is how a port acquires a divergence the day a second caller lands.
func ratesFor(model any) (float64, float64, error) {
	key := model
	if !pyval.Truthy(model) {
		key = ""
	}
	if _, ok := pyval.HashKey(key); !ok {
		return 0, 0, &pyval.PyErr{Class: "TypeError",
			Msg: pyval.UnhashableKeyMsg(key)}
	}
	name, _ := key.(string)
	if r, ok := CostByModel[name]; ok {
		return r.Input, r.Output, nil
	}
	return CostPerMInput, CostPerMOutput, nil
}

// ---------------------------------------------------------------------------
// compute_metrics
// ---------------------------------------------------------------------------

// ComputeMetrics is metrics.compute_metrics.
//
// STATEMENT ORDER IS PART OF THE CONTRACT (lens P9). The per-type loop
// computes total, done_count, avg_elapsed, avg_in, avg_out and only THEN
// total_cost, and identify_expensive_patterns runs after the whole map is
// built. That order is observable: one row with `tokens_in: "5"` raises
// `+: 'int' and 'str'` here (the sum) and `'<' not supported` through
// identify_expensive_patterns (which prices first). A port that priced
// before averaging would return every correct value and every wrong error.
func ComputeMetrics(outcomes []map[string]any) (*SystemMetrics, error) {
	now := pyval.NowISO(time.Now().UTC())

	if len(outcomes) == 0 {
		// The early return builds a SystemMetrics with the DATACLASS
		// defaults for everything after failure_patterns — by_model empty,
		// the tri-state counts zero, and goal_achieved_rate None (not 0.0).
		return &SystemMetrics{
			ComputedAt: now,
			ByTaskType: newPyDict[*GoalMetrics](),
			ByModel:    newPyDict[*ModelMetrics](),
		}, nil
	}

	// Group by task_type. setdefault raises here on an unhashable key,
	// BEFORE anything is priced.
	byType := newPyDict[[]outcomeRow]()
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		key := o["task_type"]
		cur, _, err := byType.get(key)
		if err != nil {
			return nil, err
		}
		if err := byType.set(key, append(cur, o)); err != nil {
			return nil, err
		}
	}

	typeMetrics := newPyDict[*GoalMetrics]()
	for i := 0; i < byType.Len(); i++ {
		taskType, rows := byType.At(i)
		total := len(rows)
		doneCount := 0
		for _, o := range rows {
			if pyval.Eq(o["status"], "done") {
				doneCount++
			}
		}
		avgElapsed, err := avgOf(rows, "elapsed_ms", total)
		if err != nil {
			return nil, err
		}
		avgIn, err := avgOf(rows, "tokens_in", total)
		if err != nil {
			return nil, err
		}
		avgOut, err := avgOf(rows, "tokens_out", total)
		if err != nil {
			return nil, err
		}
		// `sum(estimate_cost(...) for o in type_outcomes)` — the
		// COMPENSATED sum. by_model below uses `+=` on the same numbers and
		// gets a different answer; see the note there.
		var costs []any
		for _, o := range rows {
			c, cerr := estimateCostAny(o.field("tokens_in"), o.field("tokens_out"), nil, 0)
			if cerr != nil {
				return nil, cerr
			}
			costs = append(costs, c)
		}
		totalCost, serr := pyval.Sum(costs)
		if serr != nil {
			return nil, serr
		}
		tc, _ := pyval.Float(totalCost)

		if err := typeMetrics.set(taskType, &GoalMetrics{
			TaskType:         taskType,
			TotalRuns:        total,
			SuccessRate:      float64(doneCount) / float64(total),
			AvgElapsedMS:     avgElapsed,
			AvgTokensIn:      avgIn,
			AvgTokensOut:     avgOut,
			EstimatedCostUSD: tc,
		}); err != nil {
			return nil, err
		}
	}

	totalGoals := len(outcomes)
	totalDone := 0
	achieved, notAchieved := 0, 0
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		if pyval.Eq(o["status"], "done") {
			totalDone++
		}
		// `is True` / `is False` are IDENTITY checks. A JSON 1 or 0 is
		// neither, and stays UNJUDGED — measured: a row set carrying
		// True, False, 1, 0, "yes" and an absent key judges exactly two.
		switch v := o.field("goal_achieved").(type) {
		case bool:
			if v {
				achieved++
			} else {
				notAchieved++
			}
		}
	}
	unjudged := totalGoals - achieved - notAchieved
	judged := achieved + notAchieved
	var rate *float64
	if judged > 0 {
		r := float64(achieved) / float64(judged)
		rate = &r
	}

	expensive, err := goalRows(outcomes, true)
	if err != nil {
		return nil, err
	}
	slowest, err := goalRows(outcomes, false)
	if err != nil {
		return nil, err
	}

	patterns, err := IdentifyExpensivePatterns(outcomes)
	if err != nil {
		return nil, err
	}

	byModel := newPyDict[*ModelMetrics]()
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		// `getattr(o, "model", "") or "unknown"` — every FALSY value
		// collapses, including 0, [] and None. A single space does not.
		m := o.field("model")
		if !pyval.Truthy(m) {
			m = "unknown"
		}
		mm, hit, gerr := byModel.get(m)
		if gerr != nil {
			return nil, gerr
		}
		if !hit {
			mm = &ModelMetrics{Model: m, TotalTokensIn: 0, TotalTokensOut: 0}
			if serr := byModel.set(m, mm); serr != nil {
				return nil, serr
			}
		}
		mm.TotalRuns++
		var aerr error
		if mm.TotalTokensIn, aerr = pyval.Add(mm.TotalTokensIn, o.field("tokens_in"), "+="); aerr != nil {
			return nil, aerr
		}
		if mm.TotalTokensOut, aerr = pyval.Add(mm.TotalTokensOut, o.field("tokens_out"), "+="); aerr != nil {
			return nil, aerr
		}
		// `model=m if m != "unknown" else None` — a row whose model
		// collapsed to "unknown" is priced at the DEFAULT rate, and a row
		// that named a model is priced at that model's. This is why the
		// same rows total 18.0 in by_task_type and 90.0 in by_model on
		// opus at 1M/1M.
		//
		// And this `+=` is a NAIVE FOLD where by_task_type above uses the
		// compensated sum(). They disagree on ordinary inputs — eight rows
		// give 0.14001 there and 0.14001000000000002 here, in one returned
		// object — and the report prints both at :.6f, so the divergence is
		// invisible in the output and live in the data. Unifying them is
		// wrong in whichever field loses.
		var priceModel any
		if !pyval.Eq(m, "unknown") {
			priceModel = m
		}
		c, cerr := estimateCostAny(o.field("tokens_in"), o.field("tokens_out"), priceModel, 0)
		if cerr != nil {
			return nil, cerr
		}
		mm.TotalCostUSD += c
	}

	return &SystemMetrics{
		ComputedAt:         now,
		TotalGoals:         totalGoals,
		OverallSuccessRate: float64(totalDone) / float64(totalGoals),
		ByTaskType:         typeMetrics,
		MostExpensiveGoals: expensive,
		SlowestGoals:       slowest,
		FailurePatterns:    patterns,
		ByModel:            byModel,
		AchievedCount:      achieved,
		NotAchievedCount:   notAchieved,
		UnjudgedCount:      unjudged,
		GoalAchievedRate:   rate,
	}, nil
}

// avgOf is `sum(o.<field> for o in rows) / total` — the compensated sum, and
// a TRUE division that always yields a float. `1/1` is `1.0`, not `1`.
func avgOf(rows []outcomeRow, field string, total int) (float64, error) {
	vals := make([]any, 0, len(rows))
	for _, o := range rows {
		vals = append(vals, o.field(field))
	}
	s, err := pyval.Sum(vals)
	if err != nil {
		return 0, err
	}
	f, _ := pyval.Float(s)
	return f / float64(total), nil
}

// goalRows builds and sorts the two top-5 lists.
//
// `sorted(..., reverse=True)` is STABLE and does NOT reverse ties: CPython
// reverses, sorts, and reverses back, so equal-cost rows keep INSERTION
// order. Four rows costing 1,2,2,1 come out b,c,a,d — which is why this is
// SliceStable on a strict `>` and never sort.Slice.
func goalRows(outcomes []map[string]any, byCost bool) ([]pyval.Obj, error) {
	rows := make([]pyval.Obj, 0, len(outcomes))
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		// `o.goal[:80]` — 80 CODE POINTS, and a non-string goal is not
		// subscriptable at all. This happens AFTER the per-type loop, so a
		// row with both a bad goal and a bad tokens_in reports the token
		// failure.
		g, ok := o["goal"].(string)
		if !ok {
			return nil, &pyval.PyErr{Class: "TypeError",
				Msg: fmt.Sprintf("'%s' object is not subscriptable",
					pyval.TypeName(o["goal"]))}
		}
		goal := pyval.Clip(g, 80)
		if byCost {
			c, err := estimateCostAny(o.field("tokens_in"), o.field("tokens_out"), nil, 0)
			if err != nil {
				return nil, err
			}
			row := pyval.Obj{}
			row.Set("goal", goal)
			row.Set("task_type", o["task_type"])
			row.Set("cost_usd", c)
			row.Set("tokens_in", o.field("tokens_in"))
			row.Set("tokens_out", o.field("tokens_out"))
			rows = append(rows, row)
			continue
		}
		row := pyval.Obj{}
		row.Set("goal", goal)
		row.Set("task_type", o["task_type"])
		row.Set("elapsed_ms", o.field("elapsed_ms"))
		row.Set("status", o["status"])
		rows = append(rows, row)
	}
	key := "elapsed_ms"
	if byCost {
		key = "cost_usd"
	}
	sort.SliceStable(rows, func(i, j int) bool {
		a, _ := rows[i].Get(key)
		b, _ := rows[j].Get(key)
		// numeric-only, and NOT via pyval.Float, which would order the
		// string "5" as 5.0. A non-numeric key is unreachable here — a
		// string elapsed_ms raises in avg_elapsed long before, and
		// cost_usd is this file's own float — but the gate states the
		// rule rather than relying on that reachability argument.
		if !isNumber(a) || !isNumber(b) {
			return false
		}
		fa, _ := pyval.Float(a)
		fb, _ := pyval.Float(b)
		return fa > fb
	})
	if len(rows) > 5 {
		rows = rows[:5]
	}
	return rows, nil
}

// GetMetrics is metrics.get_metrics: load, then compute.
func GetMetrics(ws string, limit int) (*SystemMetrics, error) {
	rows, err := record.LoadOutcomes(ws, limit)
	if err != nil {
		return nil, err
	}
	return ComputeMetrics(rows)
}

// ---------------------------------------------------------------------------
// identify_expensive_patterns
// ---------------------------------------------------------------------------

// IdentifyExpensivePatterns is metrics.identify_expensive_patterns.
//
// It prices BEFORE it averages, which is the opposite of compute_metrics and
// is why the same bad row produces a different TypeError through each.
func IdentifyExpensivePatterns(outcomes []map[string]any) ([]string, error) {
	if len(outcomes) == 0 {
		return nil, nil
	}

	costs := make([]any, 0, len(outcomes))
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		c, err := estimateCostAny(o.field("tokens_in"), o.field("tokens_out"), nil, 0)
		if err != nil {
			return nil, err
		}
		costs = append(costs, c)
	}
	sumCosts, err := pyval.Sum(costs)
	if err != nil {
		return nil, err
	}
	sc, _ := pyval.Float(sumCosts)
	avgCost := sc / float64(len(costs))

	// A LIVE guard, unlike the two dead ones above it in Python: every-row
	// zero tokens is an ordinary store.
	if avgCost == 0.0 {
		return nil, nil
	}

	byType := newPyDict[[]float64]()
	for i, raw := range outcomes {
		o := outcomeRow(raw)
		c, _ := pyval.Float(costs[i])
		cur, _, gerr := byType.get(o["task_type"])
		if gerr != nil {
			return nil, gerr
		}
		if serr := byType.set(o["task_type"], append(cur, c)); serr != nil {
			return nil, serr
		}
	}

	var suggestions []string
	// ALL the cost lines first, then all the stuck lines. Python walks two
	// separate loops over two separately built maps, so a type triggering
	// both rules contributes in two different places in the list.
	for i := 0; i < byType.Len(); i++ {
		taskType, typeCosts := byType.At(i)
		vals := make([]any, len(typeCosts))
		for j, c := range typeCosts {
			vals[j] = c
		}
		s, serr := pyval.Sum(vals)
		if serr != nil {
			return nil, serr
		}
		sf, _ := pyval.Float(s)
		typeAvg := sf / float64(len(typeCosts))
		// NEAR-EQUIVALENT NOTE (battery M32): a naive fold here survives
		// the whole fixture set, and that is honest rather than a gap.
		// typeAvg has exactly two consumers — the `>` below and a `:.6f`
		// render — so a one-ULP difference is invisible except in a
		// measure-zero neighbourhood of the threshold. Searched 400,000
		// random 3-to-6-row pairs for a token set where the compensated
		// and naive averages land on opposite sides of avgCost*1.5: none.
		// pyval.Sum stays because Python's is sum(), not because a test
		// can currently tell.
		// `>` and not `>=`: a type sitting exactly at 1.5x must NOT fire.
		if typeAvg > avgCost*1.5 {
			suggestions = append(suggestions, fmt.Sprintf(
				"'%s' tasks cost %s USD avg (%sx the overall average). "+
					"Consider using MODEL_CHEAP or reducing max_tokens.",
				pyval.Str(taskType),
				pyval.PercentF(typeAvg, 6),
				pyval.PercentF(typeAvg/avgCost, 1)))
		}
	}

	byTypeOutcomes := newPyDict[[]outcomeRow]()
	for _, raw := range outcomes {
		o := outcomeRow(raw)
		cur, _, gerr := byTypeOutcomes.get(o["task_type"])
		if gerr != nil {
			return nil, gerr
		}
		if serr := byTypeOutcomes.set(o["task_type"], append(cur, o)); serr != nil {
			return nil, serr
		}
	}
	for i := 0; i < byTypeOutcomes.Len(); i++ {
		taskType, rows := byTypeOutcomes.At(i)
		stuck := 0
		for _, o := range rows {
			if pyval.Eq(o["status"], "stuck") {
				stuck++
			}
		}
		total := len(rows)
		// BOTH halves matter and both have a fixture at their own boundary:
		// two rows entirely stuck must not fire (the >= 3 floor), and four
		// rows exactly half stuck must not fire (`>` not `>=`).
		if total >= 3 && float64(stuck)/float64(total) > 0.5 {
			var vals []any
			for _, o := range rows {
				c, cerr := estimateCostAny(o.field("tokens_in"), o.field("tokens_out"), nil, 0)
				if cerr != nil {
					return nil, cerr
				}
				vals = append(vals, c)
			}
			s, serr := pyval.Sum(vals)
			if serr != nil {
				return nil, serr
			}
			sf, _ := pyval.Float(s)
			suggestions = append(suggestions, fmt.Sprintf(
				"'%s' has %d/%d stuck outcomes (total cost: $%s). "+
					"High failure rate indicates wasted spend.",
				pyval.Str(taskType), stuck, total, pyval.PercentF(sf, 6)))
		}
	}
	return suggestions, nil
}

// ---------------------------------------------------------------------------
// format_metrics_report
// ---------------------------------------------------------------------------

// FormatMetricsReport is metrics.format_metrics_report.
//
// Every conversion here was measured. The three that a reasonable port gets
// wrong: `{x}ms` is `str()` and NEVER raises (None renders "Nonems"), while
// the adjacent `${x:.6f}` DOES raise on a str; `computed_at[:19]` is a code
// point slice and the "Z" is a LITERAL, so an empty stamp renders a bare
// "Z"; and `{:,}` has two lanes.
func FormatMetricsReport(m *SystemMetrics) (string, error) {
	lines := []string{
		"=== Maro System Metrics ===",
		"Computed: " + pyval.Clip(m.ComputedAt, 19) + "Z",
		fmt.Sprintf("Total goals: %d", m.TotalGoals),
		"Overall success rate: " + pyval.PercentFmt(m.OverallSuccessRate, 1),
		"",
	}

	if m.ByTaskType != nil && m.ByTaskType.Len() > 0 {
		lines = append(lines, "--- By Task Type ---")
		order, err := m.ByTaskType.sortedIndices()
		if err != nil {
			return "", err
		}
		for _, i := range order {
			taskType, gm := m.ByTaskType.At(i)
			lines = append(lines, fmt.Sprintf(
				"  %s: %d runs, %s success, avg %sms, $%s total",
				pyval.Str(taskType), gm.TotalRuns,
				pyval.PercentFmt(gm.SuccessRate, 0),
				pyval.PercentF(gm.AvgElapsedMS, 0),
				pyval.PercentF(gm.EstimatedCostUSD, 6)))
		}
		lines = append(lines, "")
	}

	if len(m.MostExpensiveGoals) > 0 {
		lines = append(lines, "--- Most Expensive Goals ---")
		for i, g := range m.MostExpensiveGoals {
			cost, _ := g.Get("cost_usd")
			// isNumber, NOT pyval.Float — Float is `float(x)` and answers
			// 3.0 for the string "3", so this gate would have rendered
			// "$3.000000" where CPython raises. Caught by fixture 25 on the
			// differential's first run, at a site whose own comment three
			// lines up says a str cost_usd raises. The same mistake this
			// file's isNumber doc warns about, made in this file.
			if !isNumber(cost) {
				return "", &pyval.PyErr{Class: "ValueError",
					Msg: fmt.Sprintf("Unknown format code 'f' for object of type '%s'",
						pyval.TypeName(cost))}
			}
			f, _ := pyval.Float(cost)
			goal, _ := g.Get("goal")
			lines = append(lines, fmt.Sprintf("  %d. $%s — %s",
				i+1, pyval.PercentF(f, 6), pyval.Str(goal)))
		}
		lines = append(lines, "")
	}

	if len(m.SlowestGoals) > 0 {
		lines = append(lines, "--- Slowest Goals ---")
		for i, g := range m.SlowestGoals {
			// Bare `{}` — str(), which never raises. An int64 field here
			// would render 1234.0 as "1234" and lose the fixture.
			el, _ := g.Get("elapsed_ms")
			goal, _ := g.Get("goal")
			lines = append(lines, fmt.Sprintf("  %d. %sms — %s",
				i+1, pyval.Str(el), pyval.Str(goal)))
		}
		lines = append(lines, "")
	}

	if len(m.FailurePatterns) > 0 {
		lines = append(lines, "--- Cost Optimization Suggestions ---")
		for _, p := range m.FailurePatterns {
			lines = append(lines, "  ! "+p)
		}
		lines = append(lines, "")
	}

	if m.ByModel != nil && m.ByModel.Len() > 0 {
		lines = append(lines, "--- By Model ---")
		order := make([]int, m.ByModel.Len())
		for i := range order {
			order[i] = i
		}
		// `key=lambda x: -x[1].total_cost_usd` — stable, so equal costs
		// keep insertion order. A NaN cost lands in the MIDDLE under
		// CPython's sort and this does not reproduce that; see the named
		// divergence filed with the timsort chunk.
		sort.SliceStable(order, func(a, b int) bool {
			_, ma := m.ByModel.At(order[a])
			_, mb := m.ByModel.At(order[b])
			return -ma.TotalCostUSD < -mb.TotalCostUSD
		})
		for _, i := range order {
			model, mm := m.ByModel.At(i)
			// EQUIVALENT-MUTANT NOTE (battery M39): pyval.Sum over these
			// two would give the same answer — a compensated sum of two
			// operands IS a plain addition. Add is used anyway because
			// the Python is `a + b`, and a Sum here would read as a claim
			// that the compensation matters, which is the confusion this
			// file's by_task_type / by_model split exists to keep clear.
			tot, err := pyval.Add(mm.TotalTokensIn, mm.TotalTokensOut, "+")
			if err != nil {
				return "", err
			}
			lines = append(lines, fmt.Sprintf(
				"  %s: %d runs, $%s total, %s tokens",
				pyval.Str(model), mm.TotalRuns,
				pyval.PercentF(mm.TotalCostUSD, 6),
				groupedAny(tot)))
		}
		lines = append(lines, "")
	}

	return strings.Join(lines, "\n"), nil
}

// groupedAny is `{:,}` — an int lane and a float lane, which print
// `1,234,567` and `1,234.5` for the same magnitude.
//
// The float lane is NOT pyval.GroupedF, which is `{:,.<prec>f}` and needs a
// precision this format spec does not carry. `format(f, ",")` with no type
// code is `repr(f)` with the integer part grouped, and repr goes
// SCIENTIFIC outside [1e-4, 1e16): measured, `format(1e20, ",")` is
// "1e+20" and `format(1e-07, ",")` is "1e-07" — both ungrouped, both
// untouched. GroupedF(f, -1) would have rendered them as
// "100,000,000,000,000,000,000" and "0.0000001". A token total reaches the
// first of those from a single corrupt stamp.
func groupedAny(v any) string {
	if i, ok := pyIntOf(v); ok {
		return pyval.Grouped(i)
	}
	f, _ := pyval.Float(v)
	s := pyval.Repr(f) // Python's repr(float): "1e+20", "1234.5", "nan"
	if strings.ContainsAny(s, "eE") || !strings.ContainsAny(s, "0123456789") {
		return s
	}
	sign := ""
	if strings.HasPrefix(s, "-") {
		sign, s = "-", s[1:]
	}
	intPart, frac := s, ""
	if i := strings.IndexByte(s, '.'); i >= 0 {
		intPart, frac = s[:i], s[i:]
	}
	// Grouped over the digits alone; the int lane's own grouper takes an
	// int64 and this string can be wider than one.
	var b strings.Builder
	for i, c := range intPart {
		if i > 0 && (len(intPart)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(c)
	}
	return sign + b.String() + frac
}
