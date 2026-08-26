package metrics

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// seedCosts writes rows to <ws>/memory/step-costs.jsonl and returns ws.
//
// Rows are written VERBATIM, newline-joined, with no trailing newline unless
// a row asks for it — because the last line's framing is exactly what
// _reverse_readline's leftover flush is about, and a helper that normalised
// it would make that branch untestable.
func seedCosts(t *testing.T, rows []string, trailingNewline bool) string {
	t.Helper()
	ws := t.TempDir()
	dir := filepath.Join(ws, "memory")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	body := strings.Join(rows, "\n")
	if trailingNewline && len(rows) > 0 {
		body += "\n"
	}
	if err := os.WriteFile(filepath.Join(dir, "step-costs.jsonl"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return ws
}

// --- _reverse_readline -----------------------------------------------------

const pyReverseSrc = `
import json, os, sys
import metrics
from pathlib import Path

cases = json.loads(sys.argv[1])
out = []
for c in cases:
    p = Path(c["ws"]) / "memory" / "step-costs.jsonl"
    out.append(list(metrics._reverse_readline(p, buf_size=c["buf"])))
print(json.dumps(out))
`

func TestReverseReadlineMatchesCPython(t *testing.T) {
	// Chunk sizes are chosen to land boundaries INSIDE lines, exactly on a
	// newline, and past the whole file, because the leftover carry is the
	// only logic here and a single 64KB read exercises none of it.
	shapes := []struct {
		name  string
		rows  []string
		trail bool
	}{
		{name: "empty file", rows: nil, trail: false},
		{name: "one line no trailing newline", rows: []string{"alpha"}},
		{name: "one line with trailing newline", rows: []string{"alpha"}, trail: true},
		{name: "three lines", rows: []string{"alpha", "beta", "gamma"}, trail: true},
		{name: "three lines unterminated",
			rows: []string{"alpha", "beta", "gamma"}},
		// Blank lines are DROPPED by this reader (`if line:`), including a
		// run of them, and including one at the very start of the file.
		{name: "blank lines throughout",
			rows: []string{"", "alpha", "", "", "beta", ""}, trail: true},
		{name: "only blank lines", rows: []string{"", "", ""}, trail: true},
		// A file that is nothing but newlines has an empty leftover at the
		// end, so the trailing flush must NOT emit one.
		{name: "single newline", rows: []string{""}, trail: true},
		// Multibyte content across a chunk boundary: splitting on '\n' can
		// never cut a rune, and this proves it rather than asserting it.
		{name: "multibyte lines",
			rows:  []string{"アルファ", "ベータ", "ガンマ"},
			trail: true},
		{name: "a long line and a short one",
			rows:  []string{strings.Repeat("x", 40), "y"},
			trail: true},
	}
	bufs := []int{1, 2, 3, 5, 7, 8, 64, 65536}

	type reverseCase struct {
		WS  string `json:"ws"`
		Buf int    `json:"buf"`
	}
	var cases []reverseCase
	var labels []string
	for _, s := range shapes {
		for _, b := range bufs {
			cases = append(cases, reverseCase{
				WS: seedCosts(t, s.rows, s.trail), Buf: b})
			labels = append(labels, s.name+" @buf="+itoa(b))
		}
	}

	var want [][]string
	probe := pyprobe.Probe{Marker: "metrics.py"}
	probe.RunJSON(t, pyReverseSrc, &want, pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	// L1: if EVERY case came back empty the comparison proves nothing. At
	// least the multi-line shapes must have yielded something.
	nonEmpty := 0
	for _, w := range want {
		if len(w) > 0 {
			nonEmpty++
		}
	}
	if nonEmpty == 0 {
		t.Fatal("cpython yielded no lines for any case — the fixture cannot " +
			"tell two implementations apart")
	}

	for i, c := range cases {
		t.Run(labels[i], func(t *testing.T) {
			var got []string
			err := ReverseReadline(filepath.Join(c.WS, "memory", "step-costs.jsonl"),
				c.Buf, func(line string) bool {
					got = append(got, line)
					return true
				})
			if err != nil {
				t.Fatalf("ReverseReadline: %v", err)
			}
			if len(got) == 0 && len(want[i]) == 0 {
				return
			}
			if !equalStrings(got, want[i]) {
				t.Errorf("lines differ\ncpython: %q\ngo:      %q", want[i], got)
			}
		})
	}
}

// TestReverseReadlineStopsEarly pins the callback's stop signal, which has no
// CPython counterpart to diff against — a generator's consumer just stops
// iterating. Without this the `return false` path is dead weight and
// spend_today's whole reason for using this reader goes untested.
func TestReverseReadlineStopsEarly(t *testing.T) {
	ws := seedCosts(t, []string{"a", "b", "c", "d"}, true)
	var got []string
	err := ReverseReadline(filepath.Join(ws, "memory", "step-costs.jsonl"), 3,
		func(line string) bool {
			got = append(got, line)
			return len(got) < 2
		})
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != "d" || got[1] != "c" {
		t.Errorf("expected the last two lines newest-first, got %q", got)
	}
}

// --- spend_today -----------------------------------------------------------

const pySpendTodaySrc = `
import json, os, sys
from datetime import datetime, timezone
import metrics

cases = json.loads(sys.argv[1])
out = []
for c in cases:
    os.environ["MARO_WORKSPACE"] = c["ws"]
    import importlib, orch_items
    importlib.reload(orch_items)
    importlib.reload(metrics)
    # Freeze "today" the way the Go side takes it as a parameter.
    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(c["now"])
    metrics.datetime = _FrozenDT
    out.append("%.17g" % metrics.spend_today())
print(json.dumps(out))
`

func TestSpendTodayMatchesCPython(t *testing.T) {
	const now = "2026-08-26T12:00:00+00:00"
	row := func(at string, cost any) string {
		b, _ := json.Marshal(map[string]any{
			"recorded_at": at, "cost_usd": cost, "id": "abcdef",
		})
		return string(b)
	}
	today := "2026-08-26T"
	yday := "2026-08-25T"

	cases := []struct {
		name string
		rows []string
	}{
		{name: "no rows"},
		{name: "one row today", rows: []string{row(today+"01:00:00+00:00", 0.5)}},
		{name: "today after yesterday", rows: []string{
			row(yday+"23:00:00+00:00", 9.0),
			row(today+"01:00:00+00:00", 0.25),
			row(today+"02:00:00+00:00", 0.75)}},
		// THE SCAN STOPS AT THE FIRST PRE-MIDNIGHT ROW. A today-row sitting
		// BELOW a yesterday-row is unreachable by design, and a port that
		// scanned the whole file forward would find it and answer larger.
		// This is the fixture that tells a backward scan from a full one.
		{name: "a today row stranded under a yesterday row", rows: []string{
			row(today+"01:00:00+00:00", 5.0),
			row(yday+"23:00:00+00:00", 1.0),
			row(today+"02:00:00+00:00", 0.25)}},
		// A null cost_usd: the `or 0.0` truthiness gate keeps float(None)
		// from raising, so the row contributes nothing and the scan goes on.
		{name: "a null cost", rows: []string{
			row(today+"01:00:00+00:00", nil),
			row(today+"02:00:00+00:00", 1.5)}},
		{name: "a zero cost", rows: []string{
			row(today+"01:00:00+00:00", 0),
			row(today+"02:00:00+00:00", 1.5)}},
		// A malformed row is skipped WITHOUT ending the scan, but only if it
		// still carries today's date in its first 60 characters — otherwise
		// the cheap prefix check breaks first. Both halves are here.
		{name: "a torn row carrying the date", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			`{"recorded_at": "` + today + `03:00:00+00:00", "cost_usd":`,
			row(today+"02:00:00+00:00", 0.5)}},
		{name: "a torn row without the date", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			`{"garbage`,
			row(today+"02:00:00+00:00", 0.5)}},
		// The 60-CHARACTER WINDOW. `recorded_at` is not first here, and the
		// timestamp is pushed past character 60 by a long leading field — so
		// the cheap check misses a row that the strict check would have
		// accepted, and the scan ends early. That is the behaviour, not a
		// bug, and nothing else in this list has the shape to show it.
		{name: "the date pushed past the sixtieth character", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			`{"goal_preview": "` + strings.Repeat("p", 70) + `", "recorded_at": "` +
				today + `03:00:00+00:00", "cost_usd": 4.0}`,
			row(today+"02:00:00+00:00", 0.5)}},
		// A MULTIBYTE prefix: line[:60] is 60 code points, so a row whose
		// first field is Japanese pushes the timestamp far past byte 60 while
		// staying inside the character window. A byte-sliced port breaks here
		// and answers smaller.
		{name: "a multibyte preview inside the character window", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			`{"g": "` + strings.Repeat("あ", 20) + `", "recorded_at": "` +
				today + `03:00:00+00:00", "cost_usd": 4.0}`,
			row(today+"02:00:00+00:00", 0.5)}},
		// A cost that only sums correctly in one order would expose a port
		// that accumulated forward; float addition is not associative.
		{name: "costs that do not associate", rows: []string{
			row(today+"01:00:00+00:00", 0.1),
			row(today+"02:00:00+00:00", 0.2),
			row(today+"03:00:00+00:00", 0.3)}},
	}

	type spendCase struct {
		WS  string `json:"ws"`
		Now string `json:"now"`
	}
	var payload []spendCase
	for _, c := range cases {
		payload = append(payload, spendCase{WS: seedCosts(t, c.rows, true), Now: now})
	}

	var want []string
	probe := pyprobe.Probe{Marker: "metrics.py"}
	probe.RunJSON(t, pySpendTodaySrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	nowT, err := time.Parse(time.RFC3339, now)
	if err != nil {
		t.Fatal(err)
	}
	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := fmt17(SpendToday(payload[i].WS, nowT))
			if got != want[i] {
				t.Errorf("spend_today = %s, cpython = %s", got, want[i])
			}
		})
	}
}

// --- spend_for_loops -------------------------------------------------------

const pySpendLoopsSrc = `
import json, os, sys
import metrics

cases = json.loads(sys.argv[1])
out = []
for c in cases:
    os.environ["MARO_WORKSPACE"] = c["ws"]
    import importlib, orch_items
    importlib.reload(orch_items)
    importlib.reload(metrics)
    out.append("%.17g" % metrics.spend_for_loops(c["ids"]))
print(json.dumps(out))
`

func TestSpendForLoopsMatchesCPython(t *testing.T) {
	row := func(loopID string, cost any, extra string) string {
		s := `{"loop_id": ` + mustJSON(loopID) + `, "cost_usd": ` + mustJSON(cost)
		if extra != "" {
			s += ", " + extra
		}
		return s + "}"
	}
	cases := []struct {
		name string
		ids  []string
		rows []string
	}{
		{name: "no ids wanted", ids: nil, rows: []string{row("a", 1.0, "")}},
		// An empty-string id is dropped by the set comprehension's truthiness
		// filter, so a list of only "" is the same as no list at all — and
		// must NOT match rows whose loop_id is "".
		{name: "only an empty id", ids: []string{""},
			rows: []string{row("", 1.0, ""), row("a", 2.0, "")}},
		{name: "one id", ids: []string{"a"},
			rows: []string{row("a", 1.0, ""), row("b", 2.0, "")}},
		{name: "two ids", ids: []string{"a", "b"},
			rows: []string{row("a", 1.0, ""), row("b", 2.0, ""), row("c", 4.0, "")}},
		{name: "an id matching nothing", ids: []string{"zzz"},
			rows: []string{row("a", 1.0, "")}},
		// THE PRE-FILTER IS A SUBSTRING TEST ON THE WHOLE LINE. This row's
		// loop_id is "b", but the wanted id "a" appears in its goal_preview —
		// so it passes the cheap check and is then correctly rejected. A port
		// that treated the pre-filter as the answer would add 9.0.
		{name: "a wanted id appearing in another field", ids: []string{"a"},
			rows: []string{
				row("a", 1.0, `"goal_preview": "nothing"`),
				row("b", 9.0, `"goal_preview": "a"`)}},
		// And the converse: an id that is a PREFIX of another id. "a" is a
		// substring of "abc", so the row passes the pre-filter, and only the
		// exact comparison keeps it out.
		{name: "a wanted id that prefixes another", ids: []string{"a"},
			rows: []string{row("a", 1.0, ""), row("abc", 9.0, "")}},
		{name: "a null cost", ids: []string{"a"},
			rows: []string{row("a", nil, ""), row("a", 2.0, "")}},
		{name: "a torn row", ids: []string{"a"},
			rows: []string{row("a", 1.0, ""), `{"loop_id": "a", "cost`,
				row("a", 0.5, "")}},
		// A NON-STRING loop_id. `str(e.get("loop_id",""))` stringifies before
		// comparing, so an integer 12 in the file matches the wanted "12".
		{name: "a numeric loop id in the file", ids: []string{"12"},
			rows: []string{`{"loop_id": 12, "cost_usd": 3.0}`}},
	}

	type loopsCase struct {
		WS  string   `json:"ws"`
		IDs []string `json:"ids"`
	}
	var payload []loopsCase
	for _, c := range cases {
		ids := c.ids
		if ids == nil {
			ids = []string{}
		}
		payload = append(payload, loopsCase{
			WS: seedCosts(t, c.rows, true), IDs: ids})
	}

	var want []string
	probe := pyprobe.Probe{Marker: "metrics.py"}
	probe.RunJSON(t, pySpendLoopsSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := fmt17(SpendForLoops(payload[i].WS, c.ids))
			if got != want[i] {
				t.Errorf("spend_for_loops = %s, cpython = %s", got, want[i])
			}
		})
	}
}

// --- analyze_step_costs / estimate_loop_cost -------------------------------

const pyAnalyzeSrc = `
import json, os, sys
import metrics

cases = json.loads(sys.argv[1])
out = []
for c in cases:
    os.environ["MARO_WORKSPACE"] = c["ws"]
    import importlib, orch_items
    importlib.reload(orch_items)
    importlib.reload(metrics)
    entries = metrics.load_step_costs(limit=c["limit"])
    a = metrics.analyze_step_costs(entries)
    out.append({
        "entries": entries,
        "by_type_order": list(a["by_type"].keys()),
        "by_type": {k: {kk: ("%.17g" % vv if isinstance(vv, float) else vv)
                        for kk, vv in v.items()}
                    for k, v in a["by_type"].items()},
        "expensive": a["expensive_types"],
        "total": "%.17g" % a["total_cost_usd"],
        "loop_cost_plain": "%.17g" % metrics.estimate_loop_cost(c["nsteps"]),
        "loop_cost_texts": "%.17g" % metrics.estimate_loop_cost(
            c["nsteps"], c["texts"] or None),
    })
print(json.dumps(out))
`

func TestAnalyzeStepCostsMatchesCPython(t *testing.T) {
	row := func(stype string, totalTokens any, cost any) string {
		m := map[string]any{"total_tokens": totalTokens, "cost_usd": cost}
		if stype != "" {
			m["step_type"] = stype
		}
		b, _ := json.Marshal(m)
		return string(b)
	}
	cases := []struct {
		name   string
		rows   []string
		limit  int
		nsteps int
		texts  []string
	}{
		{name: "no rows", limit: 100, nsteps: 3},
		{name: "one type", limit: 100, nsteps: 3,
			rows: []string{row("research", 1000, 0.01), row("research", 3000, 0.03)}},
		// INSERTION ORDER of by_type is first-appearance in the ENTRY list,
		// and load_step_costs hands them over NEWEST FIRST — so the last row
		// in the file names the first key. A port that grouped in file order
		// gets the same stats under a different order, and expensive_types
		// comes out permuted.
		{name: "three types interleaved", limit: 100, nsteps: 4,
			rows: []string{
				row("research", 1000, 0.01),
				row("build", 90000, 0.9),
				row("verify", 500, 0.005),
				row("research", 1200, 0.012),
				row("build", 80000, 0.8)}},
		// TWO TYPES: the lower median is the SMALLER average, so the bar is
		// 2x the cheaper type and the dearer one can clear it. With an upper
		// or mean median it could not, which is the whole point of the
		// floor((n-1)//2) index.
		{name: "two types with the dear one over twice the cheap one",
			limit: 100, nsteps: 2,
			rows: []string{row("cheap", 100, 0.001), row("dear", 300, 0.03)}},
		{name: "two types exactly at twice", limit: 100, nsteps: 2,
			rows: []string{row("cheap", 100, 0.001), row("dear", 200, 0.02)}},
		// A row with NO step_type takes the "general" default.
		{name: "an untyped row", limit: 100, nsteps: 2,
			rows: []string{row("", 1000, 0.01), row("research", 5000, 0.05)}},
		// An EXPLICIT NULL step_type is not absence: `.get` returns None and
		// the key becomes None, which renders as "None" once stringified.
		{name: "a null step_type", limit: 100, nsteps: 2,
			rows: []string{`{"step_type": null, "total_tokens": 10, "cost_usd": 0.1}`,
				row("research", 20, 0.2)}},
		// avg_tokens is FLOOR division. A total that does not divide evenly
		// separates floor from truncation only when negative, so both are
		// here: a refunded row makes the group total negative.
		{name: "a total that does not divide evenly", limit: 100, nsteps: 2,
			rows: []string{row("research", 10, 0.1), row("research", 11, 0.1),
				row("research", 12, 0.1)}},
		{name: "a negative group total", limit: 100, nsteps: 2,
			rows: []string{row("refund", -7000, -0.07), row("refund", 1000, 0.01),
				row("research", 5000, 0.05)}},
		// A cost that needs round(x, 8) to agree: half-to-even on the exact
		// double, not half-away-from-zero.
		{name: "an average that lands on a rounding boundary",
			limit: 100, nsteps: 2,
			rows: []string{row("research", 1, 0.000000125),
				row("research", 1, 0.000000125)}},
		// total_tokens absent entirely -> the .get default of 0.
		{name: "a row with no total_tokens", limit: 100, nsteps: 2,
			rows: []string{`{"step_type": "research", "cost_usd": 0.5}`,
				row("research", 100, 0.1)}},
		// THE LIMIT. Ten rows read at limit=3 must analyse only the last
		// three, and the two cheapest types live outside the window — so the
		// median, and therefore expensive_types, differ from the full read.
		{name: "a limit that cuts the window", limit: 3, nsteps: 2,
			rows: []string{
				row("tiny", 1, 0.001), row("tiny", 1, 0.001), row("tiny", 1, 0.001),
				row("tiny", 1, 0.001), row("tiny", 1, 0.001), row("tiny", 1, 0.001),
				row("tiny", 1, 0.001),
				row("mid", 100, 0.1), row("mid", 100, 0.1), row("big", 9000, 0.9)}},
		// estimate_loop_cost with texts: one text classifies to a type that
		// HAS an average and one to a type that does not, so both the direct
		// and the global-average fallback are exercised in a single answer.
		{name: "loop cost with texts hitting both lanes",
			limit: 100, nsteps: 2,
			texts: []string{"research the widget market", "deploy the release"},
			rows:  []string{row("research", 1000, 0.01), row("verify", 200, 0.002)}},
		// An EMPTY text list is falsy, so it takes the num_steps branch — the
		// same answer as passing none at all, which is not what a Go port
		// reading `len(texts) >= 0` would do.
		{name: "loop cost with an empty text list",
			limit: 100, nsteps: 5, texts: []string{},
			rows: []string{row("research", 1000, 0.01)}},
		// Every type averaging zero cost makes all_costs empty, so the
		// fallback divides nothing and the estimate is 0.0 — not a NaN.
		{name: "loop cost when every average is zero",
			limit: 100, nsteps: 3, texts: []string{"anything at all"},
			rows: []string{row("research", 1000, 0.0), row("verify", 200, 0.0)}},
	}

	type analyzeCase struct {
		WS     string   `json:"ws"`
		Limit  int      `json:"limit"`
		NSteps int      `json:"nsteps"`
		Texts  []string `json:"texts"`
	}
	var payload []analyzeCase
	for _, c := range cases {
		texts := c.texts
		if texts == nil {
			texts = []string{}
		}
		payload = append(payload, analyzeCase{
			WS: seedCosts(t, c.rows, true), Limit: c.limit,
			NSteps: c.nsteps, Texts: texts})
	}

	type answer struct {
		Entries     []map[string]any          `json:"entries"`
		ByTypeOrder []any                     `json:"by_type_order"`
		ByType      map[string]map[string]any `json:"by_type"`
		Expensive   []any                     `json:"expensive"`
		Total       string                    `json:"total"`
		LoopPlain   string                    `json:"loop_cost_plain"`
		LoopTexts   string                    `json:"loop_cost_texts"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "metrics.py"}
	probe.RunJSON(t, pyAnalyzeSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			w := want[i]
			entries := LoadStepCosts(payload[i].WS, c.limit)
			if len(entries) != len(w.Entries) {
				t.Fatalf("load_step_costs returned %d rows, cpython %d",
					len(entries), len(w.Entries))
			}
			// L1 tripwire: a case with rows whose analysis came back empty on
			// both sides would pass every assertion below without measuring.
			if len(c.rows) > 0 && len(w.ByTypeOrder) == 0 {
				t.Fatalf("cpython grouped nothing from %d rows", len(c.rows))
			}

			got := AnalyzeStepCosts(entries)
			if !equalAnys(got.ByTypeOrder, w.ByTypeOrder) {
				t.Errorf("by_type ORDER differs\ncpython: %q\ngo:      %q",
					w.ByTypeOrder, got.ByTypeOrder)
			}
			for _, k := range w.ByTypeOrder {
				gs := got.StatFor(k)
				ws, ok := w.ByType[pyDictKey(k)]
				if !ok {
					t.Errorf("cpython is missing type %v", k)
					continue
				}
				if n := intOfAny(ws["count"]); gs.Count != n {
					t.Errorf("%s count = %d, cpython %d", k, gs.Count, n)
				}
				if n := intOfAny(ws["avg_tokens"]); gs.AvgTokens != n {
					t.Errorf("%s avg_tokens = %d, cpython %d", k, gs.AvgTokens, n)
				}
				if n := intOfAny(ws["total_tokens"]); gs.TotalTokens != n {
					t.Errorf("%s total_tokens = %d, cpython %d", k, gs.TotalTokens, n)
				}
				if s := strOfAny(ws["avg_cost_usd"]); fmt17(gs.AvgCostUSD) != s {
					t.Errorf("%s avg_cost_usd = %s, cpython %s",
						k, fmt17(gs.AvgCostUSD), s)
				}
			}
			if !equalAnys(got.ExpensiveTypes, w.Expensive) {
				t.Errorf("expensive_types differ\ncpython: %q\ngo:      %q",
					w.Expensive, got.ExpensiveTypes)
			}
			if s := fmt17(got.TotalCostUSD); s != w.Total {
				t.Errorf("total_cost_usd = %s, cpython %s", s, w.Total)
			}

			if s := fmt17(EstimateLoopCost(payload[i].WS, c.nsteps, nil)); s != w.LoopPlain {
				t.Errorf("estimate_loop_cost(n) = %s, cpython %s", s, w.LoopPlain)
			}
			if s := fmt17(EstimateLoopCost(payload[i].WS, c.nsteps, c.texts)); s != w.LoopTexts {
				t.Errorf("estimate_loop_cost(n, texts) = %s, cpython %s",
					s, w.LoopTexts)
			}
		})
	}
}

// --- helpers ---------------------------------------------------------------

// fmt17 renders a float at 17 significant digits — enough to round-trip a
// float64 exactly, and the same spelling `%.17g` the CPython side uses. The
// comparison is on DIGITS rather than a tolerance for the reason the cost
// differential already gives: these numbers feed named dollar thresholds,
// and a comparison with slack agrees with a port that regrouped the
// arithmetic while still letting the two runtimes disagree about whether a
// budget line was crossed.
func fmt17(f float64) string { return fmt.Sprintf("%.17g", f) }

// equalAnys compares two key lists structurally. The keys are `any` because
// a None step_type is a real dict key in Python and stringifying it would
// merge it with the literal text "None" — see StepCostAnalysis.
func equalAnys(a, b []any) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if !pyval.Eq(a[i], b[i]) {
			return false
		}
	}
	return true
}

// pyDictKey is the string json.dumps() uses for a dict key. Python coerces a
// non-string key on the way out — None becomes "null", True becomes "true",
// 12 becomes "12" — so the probe's by_type map is keyed by these and not by
// the key values themselves.
func pyDictKey(k any) string {
	switch v := k.(type) {
	case nil:
		return "null"
	case string:
		return v
	case bool:
		if v {
			return "true"
		}
		return "false"
	}
	return pyval.Str(k)
}

func equalStrings(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func intOfAny(v any) int { return pyval.IntOf(v) }

func strOfAny(v any) string {
	s, _ := v.(string)
	return s
}

func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var d []byte
	for n > 0 {
		d = append([]byte{byte('0' + n%10)}, d...)
		n /= 10
	}
	if neg {
		return "-" + string(d)
	}
	return string(d)
}
