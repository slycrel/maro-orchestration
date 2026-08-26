package metrics

import (
	"encoding/json"
	"fmt"
	"math/rand"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The mutation battery leaves survivors that are NOT coverage gaps: mutants
// of guards that cannot fire, ported faithfully from Python guards that
// cannot fire either. Recording those as "equivalent" in a document is a
// CLAIM, and a claim about reachability is exactly the kind that reads as
// true and is false at one input nobody pictured.
//
// So the exemptions are executed. Each test below searches for the input
// that would make its guard matter, and fails if it finds one — which means
// a future change that makes the guard live turns these red rather than
// leaving a stale exemption in a file.
//
// If one of these DOES go red, the guard is no longer dead: delete the
// exemption, not the guard.

// randomStore builds step-cost rows across a few types with wide-ranging
// token counts — negative, zero, float and large — because the guards being
// probed are all sign or emptiness tests.
func randomStore(rnd *rand.Rand, n int) []pyval.Obj {
	types := []any{"research", "build", "ops", "general", "", nil}
	var out []pyval.Obj
	for i := 0; i < n; i++ {
		var tok any
		switch rnd.Intn(5) {
		case 0:
			tok = 0
		case 1:
			tok = -rnd.Intn(5000)
		case 2:
			tok = float64(rnd.Intn(5000)) + 0.5
		case 3:
			tok = rnd.Intn(50)
		default:
			tok = rnd.Intn(9000)
		}
		row := map[string]any{"total_tokens": tok,
			"cost_usd": float64(rnd.Intn(100)) / 100.0}
		if st := types[rnd.Intn(len(types))]; st != nil {
			row["step_type"] = st
		}
		raw, err := json.Marshal(row)
		if err != nil {
			panic(err)
		}
		o, err := pyval.LoadsOrdered(string(raw))
		if err != nil {
			panic(err)
		}
		out = append(out, o.(pyval.Obj))
	}
	return out
}

// TestMedianGuardsAreUnreachable executes the exemptions for two survivors
// in analyze_step_costs' median block.
//
//	M56  `max(0, (len(avgs)-1)//2)` — the max() never clamps.
//	M59  `median_avg > 0 and ...`   — the guard never changes the answer.
//
// Both hold for the same structural reason, and the reason is worth stating
// because it is what a future change would break: `avgs` is built by a
// filter that admits only values > 0. So it is either EMPTY — in which case
// no type has a positive average and nothing can exceed 2*0 — or non-empty,
// in which case its median is one of its own members and therefore positive,
// and (len-1)//2 is already >= 0.
//
// Remove the `> 0` from the avgs filter and both guards come alive at once.
func TestMedianGuardsAreUnreachable(t *testing.T) {
	rnd := rand.New(rand.NewSource(20260826))
	clampWouldMatter, guardWouldMatter := 0, 0
	for trial := 0; trial < 3000; trial++ {
		entries := randomStore(rnd, 1+rnd.Intn(12))
		got, err := AnalyzeStepCosts(entries)
		if err != nil {
			continue
		}
		var avgs []float64
		for _, key := range got.ByTypeOrder {
			if v := numTokens(got.StatFor(key).AvgTokens); v > 0 {
				avgs = append(avgs, v)
			}
		}
		// M56: the clamp fires only if the raw index is negative.
		if len(avgs) > 0 && (len(avgs)-1)/2 < 0 {
			clampWouldMatter++
		}
		// M59: the guard changes the answer only if the median is <= 0 while
		// some type would clear 2*median.
		if len(avgs) == 0 {
			for _, key := range got.ByTypeOrder {
				if numTokens(got.StatFor(key).AvgTokens) > 0 {
					guardWouldMatter++
				}
			}
		}
	}
	if clampWouldMatter > 0 {
		t.Errorf("the max(0, ...) clamp fired %d times — M56 is no longer an "+
			"equivalent mutant and needs a fixture", clampWouldMatter)
	}
	if guardWouldMatter > 0 {
		t.Errorf("the `median > 0` guard changed the answer %d times — M59 is "+
			"no longer an equivalent mutant and needs a fixture", guardWouldMatter)
	}
}

// TestGrandTotalRaiseIsUnreachable executes the exemption for M63, the
// swallowed error on analyze_step_costs' FOURTH sum.
//
// The per-type sums PARTITION the entries: every row lands in exactly one
// group, and both the group sum and the grand total read the same field with
// the same default. So any row whose cost_usd will not sum makes its own
// group's sum raise, and the group loop runs to completion before the grand
// total is reached. The grand total's error path is therefore dead — a
// faithful port of a Python line that is equally dead.
//
// This matters beyond the mutant: the comment above that sum used to claim
// the opposite, that "a store whose only bad row is in a group that already
// summed cleanly still reaches here to fail". No such store exists.
func TestGrandTotalRaiseIsUnreachable(t *testing.T) {
	rnd := rand.New(rand.NewSource(7))
	bad := []any{nil, "abc", "1.5", []any{1}, map[string]any{"a": 1}}
	reached := 0
	for trial := 0; trial < 2000; trial++ {
		n := 1 + rnd.Intn(10)
		var rows []string
		for i := 0; i < n; i++ {
			st := []string{"research", "build", "ops"}[rnd.Intn(3)]
			var cost any = float64(rnd.Intn(100)) / 100.0
			if rnd.Intn(3) == 0 {
				cost = bad[rnd.Intn(len(bad))]
			}
			raw, _ := json.Marshal(map[string]any{
				"step_type": st, "total_tokens": rnd.Intn(500), "cost_usd": cost})
			rows = append(rows, string(raw))
		}
		var entries []pyval.Obj
		for _, r := range rows {
			o, err := pyval.LoadsOrdered(r)
			if err != nil {
				t.Fatal(err)
			}
			entries = append(entries, o.(pyval.Obj))
		}
		// Does any GROUP sum raise?
		groupRaises := false
		groups := map[string][]any{}
		var order []string
		for _, e := range entries {
			var st any = "general"
			if v, ok := e.Get("step_type"); ok {
				st = pyval.Plain(v)
			}
			h, ok := pyval.HashKey(st)
			if !ok {
				continue
			}
			if _, seen := groups[h]; !seen {
				order = append(order, h)
			}
			groups[h] = append(groups[h], objGet(e, "cost_usd", 0.0))
		}
		for _, h := range order {
			if _, err := pyval.Sum(groups[h]); err != nil {
				groupRaises = true
				break
			}
		}
		// Does the GRAND sum raise?
		var all []any
		for _, e := range entries {
			all = append(all, objGet(e, "cost_usd", 0.0))
		}
		_, grandErr := pyval.Sum(all)
		if grandErr != nil && !groupRaises {
			reached++
			t.Errorf("found a store where the grand total raises and no group "+
				"does — M63's error path is live: %v", rows)
		}
	}
	if reached == 0 {
		// Anti-vacuity: the search must actually have produced raising
		// stores, or it proved nothing.
		if !sawARaise(t) {
			t.Fatal("no fixture in this search ever raised at all")
		}
	}
}

func sawARaise(t *testing.T) bool {
	t.Helper()
	o, err := pyval.LoadsOrdered(`{"step_type": "a", "total_tokens": 1, "cost_usd": null}`)
	if err != nil {
		t.Fatal(err)
	}
	_, aerr := AnalyzeStepCosts([]pyval.Obj{o.(pyval.Obj)})
	return aerr != nil
}

// TestEmptyByTypeShortCircuitIsRedundant executes the exemption for M66.
//
// estimate_loop_cost returns 0.0 early when by_type is empty. Removing the
// early return does not change the answer, because the global-average
// closure below it returns 0.0 for an empty cost list and every downstream
// branch multiplies by it. The guard is a statement of intent and a saved
// traversal, not a correctness rule — which is exactly what the port should
// keep, and exactly why the mutant survives.
func TestEmptyByTypeShortCircuitIsRedundant(t *testing.T) {
	ws := t.TempDir()
	got, err := EstimateLoopCost(ws, 5, []string{"research something"})
	if err != nil {
		t.Fatal(err)
	}
	if got != 0.0 {
		t.Fatalf("an empty store estimated %v, not 0.0 — M66's guard is now "+
			"load-bearing and needs a fixture rather than an exemption", got)
	}
	// And with no texts, the other branch.
	if got, err = EstimateLoopCost(ws, 5, nil); err != nil || got != 0.0 {
		t.Fatalf("no-texts branch: %v, %v", got, err)
	}
}

// TestReverseReadlineChunkSizeCannotChangeAnswers executes the exemption for
// M2, the mutant that shrinks the reader's default chunk from 64KB to 8KB.
//
// A chunk size is a read-pattern, not a rule: any correct reverse reader
// returns identical lines at every size. The differential already sweeps
// eight sizes, which is what makes this an exemption rather than a gap — but
// it sweeps them on SMALL files, so nothing there crosses the 64KB boundary
// the constant actually names. This crosses it in both directions.
func TestReverseReadlineChunkSizeCannotChangeAnswers(t *testing.T) {
	var rows []string
	for i := 0; i < 4000; i++ {
		rows = append(rows, fmt.Sprintf(
			`{"i": %d, "pad": "%s"}`, i, "0123456789012345678901234"))
	}
	path := StepCostsPath(seedCosts(t, rows, true))
	read := func(buf int) []string {
		var got []string
		if err := ReverseReadline(path, buf, func(l string) bool {
			got = append(got, l)
			return true
		}); err != nil {
			t.Fatal(err)
		}
		return got
	}
	base := read(reverseReadlineBufSize)
	if len(base) != len(rows) {
		t.Fatalf("read %d lines, wrote %d", len(base), len(rows))
	}
	for _, buf := range []int{1, 7, 8192, 65535, 65536, 65537, 1 << 20} {
		if got := read(buf); !equalStrings(got, base) {
			t.Errorf("buf=%d disagreed with the default chunk size", buf)
		}
	}
}

// TestMissingLedgerShortCircuitsAreRedundant executes the exemptions for M16
// and M32 — two early returns whose removal cannot change an answer.
//
//	M16  spend_today's `if not path.exists(): return 0.0`
//	M32  spend_for_loops' `if not wanted: return 0.0`
//
// Both are real guards in Python and both are unobservable. Falling through
// M16 opens a file that is not there, and the failure returns 0.0 anyway.
// Falling through M32 scans with an empty wanted set, and `any(l in line for
// l in wanted)` over an empty set is False for every line, so nothing sums.
//
// They are ported because Python has them — a port that "simplified" them
// away would be identical today and would diverge the moment either loop
// grew a side effect. The mutants survive because the guards save work, not
// because they decide anything.
func TestMissingLedgerShortCircuitsAreRedundant(t *testing.T) {
	// M16: no ledger at all, and a ledger present but unreadable-as-today.
	empty := t.TempDir()
	if got := SpendTodayNow(empty); got != 0.0 {
		t.Errorf("a workspace with no ledger spent %v", got)
	}
	// M32: an existing, populated ledger with an empty and an all-falsy id
	// list. If the guard were load-bearing these would differ from 0.0.
	ws := seedCosts(t, []string{
		`{"loop_id": "a", "cost_usd": 5.0}`,
		`{"loop_id": "", "cost_usd": 7.0}`}, true)
	for _, ids := range [][]string{nil, {}, {""}, {"", ""}} {
		if got := SpendForLoops(ws, ids); got != 0.0 {
			t.Errorf("an empty wanted set (%v) spent %v, not 0.0 — M32's "+
				"guard is load-bearing and needs a fixture", ids, got)
		}
	}
	// And the same call WITH a real id must be non-zero, or the loop above
	// proved only that the store was empty.
	if got := SpendForLoops(ws, []string{"a"}); got != 5.0 {
		t.Fatalf("the anti-vacuity call spent %v, not 5.0 — the fixture is "+
			"not being read at all", got)
	}
}
