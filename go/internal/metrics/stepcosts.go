package metrics

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The step-costs ledger: the READ half of metrics.py's cost store.
//
// `record_step_cost` — the writer — is deliberately not here yet. It mints a
// uuid and a wall-clock stamp and appends under a lock, and it belongs with
// the store slice that carries the other writers. What this file ports is
// everything that ASKS the ledger a question, because those are what the
// budget gate and the run cards read, and they are pure given a file.

// StepCostsPath is metrics._step_costs_path().
func StepCostsPath(ws string) string {
	return filepath.Join(orch.MemoryDir(ws), "step-costs.jsonl")
}

// reverseReadlineBufSize is `_reverse_readline`'s default buf_size. Exposed
// as a constant for the same reason Python exposes `_TAIL_CHUNK_BYTES`: the
// chunk-boundary logic is the only interesting part, and pinning it needs
// fixtures smaller than 64KB.
const reverseReadlineBufSize = 65536

// ReverseReadline is metrics._reverse_readline: the lines of `path` from EOF
// backward, decoded with errors="replace", EMPTY LINES DROPPED.
//
// This is NOT jsonl_utils._iter_lines_reverse, and the difference is worth
// naming because the two sit in one codebase doing the same job. That one
// yields every part including empties and holds `parts[0]` as a fragment
// only while `position > 0`; this one drops falsy lines inside the loop and
// keeps a `leftover` that it flushes AFTER the loop. The flush is reachable
// here where the equivalent block in jsonl_utils was proved dead: this loop
// assigns `leftover = lines[0]` unconditionally, so the final iteration —
// the one that reads down to offset 0 — leaves the file's FIRST line sitting
// in `leftover` instead of yielding it. Without the trailing flush the first
// line of every file would be lost. Same shape, opposite reachability, and
// only one of them is a guard that cannot fire.
//
// The callback returns false to stop early, which is the whole point of the
// function: `spend_today` breaks at the first pre-midnight row and must not
// pay for the file's lifetime.
func ReverseReadline(path string, bufSize int, yield func(line string) bool) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return err
	}
	remaining := st.Size()
	var leftover []byte
	for remaining > 0 {
		readSize := int64(bufSize)
		if remaining < readSize {
			readSize = remaining
		}
		remaining -= readSize
		buf := make([]byte, readSize)
		if _, err := f.ReadAt(buf, remaining); err != nil {
			return err
		}
		chunk := append(buf, leftover...)
		// Splitting on the single byte '\n' can never land inside a
		// multi-byte rune: every UTF-8 continuation byte is >= 0x80. A chunk
		// boundary only ever splits BETWEEN lines, so no line is corrupted
		// by the chunking, only by bytes that were already invalid.
		lines := strings.Split(string(chunk), "\n")
		leftover = []byte(lines[0])
		for i := len(lines) - 1; i >= 1; i-- {
			if lines[i] == "" {
				continue
			}
			if !yield(decodeReplace(lines[i])) {
				return nil
			}
		}
	}
	if len(leftover) > 0 {
		yield(decodeReplace(string(leftover)))
	}
	return nil
}

// decodeReplace is `bytes.decode("utf-8", errors="replace")`. Converting
// []byte to string in Go keeps invalid bytes VERBATIM rather than
// substituting U+FFFD, so a crash-torn append would compare equal here and
// unequal in CPython — the exact case this reader exists to survive.
//
// ToValidUTF8 is not quite CPython's replacement policy: it collapses a RUN
// of invalid bytes into one U+FFFD where Python's replace emits one per
// undecodable byte (or per maximal subpart). The difference is only visible
// on a torn row, which then fails json.Unmarshal on both sides and is
// skipped — but it is a difference, and it belongs in a comment rather than
// in a claim that the two decoders agree.
func decodeReplace(s string) string {
	if utf8.ValidString(s) {
		return s
	}
	return strings.ToValidUTF8(s, "�")
}

// objGet is `d.get(key, default)` over an ordered row.
func objGet(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

// SpendToday is metrics.spend_today(): recorded USD since UTC midnight.
//
// `now` is a parameter where Python reads the clock, because a function whose
// answer depends on the wall clock cannot be differentially tested against
// CPython without one of the two runtimes lying about the time. SpendTodayNow
// is the production spelling.
func SpendToday(ws string, now time.Time) float64 {
	path := StepCostsPath(ws)
	if _, err := os.Stat(path); err != nil {
		return 0.0
	}
	today := now.UTC().Format("2006-01-02")
	total := 0.0
	// `if today not in line[:60]` — a CODE POINT slice, not a byte slice, and
	// a substring test rather than a prefix test. A row whose first 60 runes
	// happen to contain today's date anywhere (a goal_preview quoting it, an
	// id that collides) passes this gate and is then rejected by the
	// startswith below. The cheap check is allowed false positives; it is
	// the false NEGATIVE that would end the scan early, and that is why the
	// window is 60 rather than the length of the timestamp.
	_ = ReverseReadline(path, reverseReadlineBufSize, func(line string) bool {
		if !strings.Contains(pyval.Clip(line, 60), today) {
			return false
		}
		// pyval.LoadsMap, not encoding/json: a JSON integer must stay an
		// integer. Go's decoder makes every number a float64, and `str()` of
		// a float64 12 is "12.0" where Python's str of an int 12 is "12" —
		// which silently unmatches every numeric id in the store.
		e, err := pyval.LoadsMap(line)
		if err != nil {
			return true // `except: continue` — a torn row is not the end
		}
		if strings.HasPrefix(pyval.Str(pyval.GetOr(e, "recorded_at", "")), today) {
			total += costUSDOf(e)
		}
		return true
	})
	return total
}

// SpendTodayNow is spend_today() with the real clock.
func SpendTodayNow(ws string) float64 { return SpendToday(ws, time.Now()) }

// costUSDOf is `float(e.get("cost_usd", 0.0) or 0.0)`.
//
// The `or 0.0` is a TRUTHINESS gate standing between the get and the float,
// so a present null, a present 0, an empty string and `false` all become 0.0
// without ever reaching float() — which is what keeps a null row from
// raising. A non-numeric truthy value (the string "abc") WOULD raise in
// Python and take the whole function's bare except, returning 0.0 for the
// entire file. Go cannot reproduce a mid-loop abort by returning a float, so
// that case is named here and left as a known divergence: a corrupt
// cost_usd string zeroes one row in Go and the whole day in Python.
func costUSDOf(e map[string]any) float64 {
	v := pyval.GetOr(e, "cost_usd", 0.0)
	if !pyval.Truthy(v) {
		return 0.0
	}
	f, ok := pyval.Float(v)
	if !ok {
		return 0.0
	}
	return f
}

// SpendForLoops is metrics.spend_for_loops().
//
// Forward scan, not reverse: a loop's rows are anywhere in the file, so
// there is no suffix to stop at. The `any(l in line ...)` pre-filter is a
// substring test over the WHOLE line — it can match a loop id appearing in
// some other field — and the exact comparison below is what makes it sound.
func SpendForLoops(ws string, loopIDs []string) float64 {
	wanted := map[string]bool{}
	var order []string
	for _, l := range loopIDs {
		s := pyval.Str(l)
		// `{str(l) for l in (loop_ids or []) if l}` — the truthiness filter
		// drops "" before str() is applied, so an empty id never joins.
		if l == "" {
			continue
		}
		if !wanted[s] {
			order = append(order, s)
		}
		wanted[s] = true
	}
	if len(wanted) == 0 {
		return 0.0
	}
	path := StepCostsPath(ws)
	data, err := os.ReadFile(path)
	if err != nil {
		return 0.0
	}
	// `path.open(encoding="utf-8")` is a STRICT TEXT read, and the whole
	// scan sits inside the function's bare except. So one non-UTF-8 byte
	// anywhere in the file — a crash-torn append is the realistic way that
	// happens — makes CPython answer 0.0 for the ENTIRE file, not skip the
	// line. The port read bytes and split them, which answered with every
	// other row's spend: a run's attributed cost that CPython reports as
	// zero, reported here as five dollars.
	//
	// Validity of the whole file is the right test rather than of the lines
	// reached, because the `for line in fh` loop has no break: it decodes
	// through to EOF even after the last wanted row (adversarial metrics r1,
	// MEDIUM — L12).
	if _, derr := pyval.DecodeUTF8Strict(data); derr != nil {
		return 0.0
	}
	total := 0.0
	for _, line := range strings.Split(string(data), "\n") {
		hit := false
		for _, w := range order {
			if strings.Contains(line, w) {
				hit = true
				break
			}
		}
		if !hit {
			continue
		}
		e, err := pyval.LoadsMap(line)
		if err != nil {
			continue
		}
		// `str(e.get("loop_id", ""))` — the STRINGIFICATION is the join. A
		// row carrying the integer 12 matches a wanted "12", which is only
		// true if the decoder kept it an integer (see SpendToday).
		if wanted[pyval.Str(pyval.GetOr(e, "loop_id", ""))] {
			total += costUSDOf(e)
		}
	}
	return total
}

// LoadStepCosts is metrics.load_step_costs(): recent entries, NEWEST FIRST.
//
// Python reads a byte-bounded tail and then reverses it. This reads the file
// and takes the same suffix, which is the same ANSWER at a different I/O
// cost — the distinction the port has drawn everywhere else it met
// read_jsonl_tail, and the reason it is spelled out rather than assumed: a
// bounded read that stops early can return fewer rows than an unbounded one
// on a file with a torn prefix, and this one cannot.
func LoadStepCosts(ws string, limit int) []pyval.Obj {
	rows, _ := record.ReadAllCountedOrdered(StepCostsPath(ws))
	if limit > 0 && len(rows) > limit {
		rows = rows[len(rows)-limit:]
	}
	out := make([]pyval.Obj, 0, len(rows))
	for i := len(rows) - 1; i >= 0; i-- {
		out = append(out, rows[i])
	}
	return out
}

// TypeStat is one row of analyze_step_costs()'s by_type map.
//
// AvgTokens and TotalTokens are `any` — int or float64 — because Python's
// are. `total_tokens` is summed straight off the store with no coercion, so
// one row carrying `2.5` makes the type's total a float and `total_tok //
// count` a float too. Typing them `int` truncated that silently.
type TypeStat struct {
	Count       int
	AvgTokens   any
	TotalTokens any
	AvgCostUSD  float64
}

// numTokens reads AvgTokens/TotalTokens as a float for COMPARISON only.
// Every comparison analyze_step_costs makes on them (`> 0`, `> 2 * median`,
// and the sort) is numeric, and Python compares an int against a float
// numerically too. Token counts are far below 2^53, so the widening is
// exact — this is a reading of the value, never a replacement for it.
func numTokens(v any) float64 {
	f, _ := pyval.Float(v)
	return f
}

// StepCostAnalysis is analyze_step_costs()'s return dict.
//
// ByTypeOrder carries the dict's insertion order — FIRST APPEARANCE of each
// step_type in `entries` — because ExpensiveTypes is derived by iterating it
// and a Go map would randomise that list on every call.
//
// The keys are `any`, not `string`, and that is not over-engineering. Python
// groups on `e.get("step_type", "general")` — the VALUE, whatever it is. The
// default is a string and `classify_step_type` only ever writes strings, so
// in practice every key is one; but a row carrying an explicit `null` groups
// under `None`, and a dict happily holds that key. Stringifying it to "None"
// would merge it with a row whose step_type is literally the text "None",
// and ExpensiveTypes would then name a type nothing can look up. ByType is
// keyed by pyval.HashKey so the two stay distinct, and StatFor does the
// lookup so callers never see the encoding.
type StepCostAnalysis struct {
	ByType         map[string]TypeStat
	ByTypeOrder    []any
	ExpensiveTypes []any
	TotalCostUSD   float64
}

// StatFor is `by_type.get(key, {})` — the zero TypeStat stands in for the
// empty dict, whose every `.get(_, 0.0)` is zero anyway.
func (a StepCostAnalysis) StatFor(key any) TypeStat {
	h, ok := pyval.HashKey(key)
	if !ok {
		return TypeStat{}
	}
	return a.ByType[h]
}

// AnalyzeStepCosts is metrics.analyze_step_costs().
//
// IT RETURNS AN ERROR, and the error is the point. Python sums the store's
// raw values — `sum(e.get("cost_usd", 0.0) for e in type_entries)`, with no
// `or 0.0` and no try anywhere in the function — so a single row carrying
// `"cost_usd": null` or `"cost_usd": "1.5"` raises TypeError straight out to
// the caller. The port used costUSDOf's COERCING expression here, which
// belongs to spend_for_loops four hundred lines away and has an `or 0.0`
// that this one does not. That is failing open twice over: the crash became
// a number, and the number was wrong in a way nothing would ever surface.
//
// Three sites raise, and their ORDER is observable because only the first
// one fires: an unhashable step_type in the grouping pass, then per group
// total_tokens, then per group cost_usd — Python sums each field over the
// whole group before starting the next, so a group with both a bad token
// count and a bad cost reports the token count (adversarial metrics r1,
// MEDIUM — L38).
func AnalyzeStepCosts(entries []pyval.Obj) (StepCostAnalysis, error) {
	if len(entries) == 0 {
		return StepCostAnalysis{ByType: map[string]TypeStat{}, TotalCostUSD: 0.0}, nil
	}

	groups := map[string][]pyval.Obj{}
	var order []any
	for _, e := range entries {
		// `e.get("step_type", "general")` — the default applies to ABSENCE
		// only, so an explicit null is a key of its own and not the default.
		var st any = "general"
		if v, ok := e.Get("step_type"); ok {
			st = pyval.Plain(v)
		}
		h, ok := pyval.HashKey(st)
		if !ok {
			// An unhashable step_type (a list, a dict) raises TypeError in
			// Python and takes no except — analyze_step_costs has none — so
			// the whole call propagates. It propagates here too now: the port
			// used to DROP the row and name the divergence, which was the
			// best it could do while the signature returned no error.
			return StepCostAnalysis{}, &pyval.PyErr{Class: "TypeError",
				Msg: fmt.Sprintf("unhashable type: '%s'", pyval.TypeName(st))}
		}
		if _, seen := groups[h]; !seen {
			order = append(order, st)
		}
		groups[h] = append(groups[h], e)
	}

	stats := map[string]TypeStat{}
	for _, key := range order {
		st, _ := pyval.HashKey(key)
		typeEntries := groups[st]
		// Two separate passes, in Python's order: `sum(... total_tokens ...)`
		// completes before `sum(... cost_usd ...)` begins. Interleaving them
		// in one loop, as the port did, reports the wrong field first when a
		// group holds two bad values.
		totalTok, err := pyval.Sum(fieldsOf(typeEntries, "total_tokens", 0))
		if err != nil {
			return StepCostAnalysis{}, err
		}
		totalCost, err := pyval.Sum(fieldsOf(typeEntries, "cost_usd", 0.0))
		if err != nil {
			return StepCostAnalysis{}, err
		}
		count := len(typeEntries)
		// `total_tok // count if count else 0` — count is len() of a list
		// that exists because something was appended to it, so the else is
		// unreachable. Kept as Python spells it; FloorDivAny rather than `/`
		// because a negative total (a refunded row) floors toward -inf in
		// Python and truncates toward zero in Go.
		var avgTok any = 0
		if count > 0 {
			if avgTok, err = pyval.FloorDivAny(totalTok, count); err != nil {
				return StepCostAnalysis{}, err
			}
		}
		avgCost := 0.0
		if count > 0 {
			avgCost = pyval.Round(pyval.FloatOf(totalCost)/float64(count), 8)
		}
		stats[st] = TypeStat{Count: count, AvgTokens: avgTok,
			TotalTokens: totalTok, AvgCostUSD: avgCost}
	}

	// `[s["avg_tokens"] for s in type_stats.values() if s["avg_tokens"] > 0]`
	// — dict values, so insertion order again, though the list is sorted
	// immediately after and only its LENGTH and contents matter.
	var avgs []float64
	for _, key := range order {
		st, _ := pyval.HashKey(key)
		if numTokens(stats[st].AvgTokens) > 0 {
			avgs = append(avgs, numTokens(stats[st].AvgTokens))
		}
	}
	medianAvg := 0.0
	if len(avgs) > 0 {
		sorted := append([]float64(nil), avgs...)
		sort.Float64s(sorted)
		// LOWER median: floor((n-1)/2), not the mean of the middle two. At
		// n=2 that is the SMALLER value, so the bar is set by the cheaper of
		// two types — which is the documented intent ("expensive types are
		// above 2x the cheaper half") and not an off-by-one.
		medianAvg = sorted[max0((len(avgs)-1)/2)]
	}
	var expensive []any
	for _, key := range order {
		st, _ := pyval.HashKey(key)
		if medianAvg > 0 && numTokens(stats[st].AvgTokens) > 2*medianAvg {
			expensive = append(expensive, key)
		}
	}

	// The FOURTH sum, over every entry rather than per type — and the fourth
	// place a null cost_usd raises. It runs AFTER expensive_types, so a store
	// whose only bad row is in a group that already summed cleanly still
	// reaches here to fail.
	total, err := pyval.Sum(fieldsOf(entries, "cost_usd", 0.0))
	if err != nil {
		return StepCostAnalysis{}, err
	}
	return StepCostAnalysis{
		ByType:         stats,
		ByTypeOrder:    order,
		ExpensiveTypes: expensive,
		// Six places here, EIGHT in avg_cost_usd above. Not a typo in either
		// direction; the two round to different precision in the source.
		TotalCostUSD: pyval.Round(pyval.FloatOf(total), 6),
	}, nil
}

// fieldsOf is the generator expression `(e.get(key, def) for e in entries)`,
// materialised. The default applies to ABSENCE only — a present null is a
// null, which is exactly the value that makes the sum raise.
func fieldsOf(entries []pyval.Obj, key string, def any) []any {
	out := make([]any, 0, len(entries))
	for _, e := range entries {
		out = append(out, objGet(e, key, def))
	}
	return out
}

func max0(n int) int {
	if n < 0 {
		return 0
	}
	return n
}

// costUSDOfObj — costUSDOf over an ordered row — used to live here, and its
// only callers were in analyze_step_costs, which does not coerce. Deleting
// it rather than leaving it unused is the point: it is the WRONG expression
// for every remaining reader of an ordered row, and a helper that reads
// right and is wrong is how it got used there in the first place. spend_today
// and spend_for_loops keep costUSDOf, whose `or 0.0` they actually have.

// EstimateLoopCost is metrics.estimate_loop_cost().
//
// Note what the fallback does: when a step's own type has no recorded
// average, Python recomputes the global average INSIDE the loop, once per
// such step, from the same unchanging by_type map. It is O(n·m) for an
// answer that cannot change between iterations. Ported as written — the
// result is identical and the cost is not the port's to fix — but named,
// because a reader who "cleans it up" by hoisting is right and should know
// they are changing nothing but the clock.
func EstimateLoopCost(ws string, numSteps int, stepTexts []string) (float64, error) {
	// No try here either, so analyze_step_costs's TypeError leaves this
	// function too rather than becoming a 0.0 estimate — which the budget
	// gate would read as "this loop is free".
	analysis, err := AnalyzeStepCosts(LoadStepCosts(ws, 500))
	if err != nil {
		return 0, err
	}
	if len(analysis.ByType) == 0 {
		return 0.0, nil
	}

	globalAvg := func() float64 {
		var costs []float64
		for _, key := range analysis.ByTypeOrder {
			if c := analysis.StatFor(key).AvgCostUSD; c > 0 {
				costs = append(costs, c)
			}
		}
		if len(costs) == 0 {
			return 0.0
		}
		s := 0.0
		for _, c := range costs {
			s += c
		}
		return s / float64(len(costs))
	}

	// `if step_texts:` is a TRUTHINESS test, so an EMPTY list takes the
	// num_steps branch rather than returning 0 — a caller passing [] gets
	// the same answer as one passing nothing at all.
	if len(stepTexts) > 0 {
		total := 0.0
		for _, text := range stepTexts {
			stype := ClassifyStepType(text)
			avg := analysis.StatFor(stype).AvgCostUSD
			if avg > 0 {
				total += avg
			} else {
				total += globalAvg()
			}
		}
		return pyval.Round(total, 6), nil
	}
	return pyval.Round(float64(numSteps)*globalAvg(), 6), nil
}
