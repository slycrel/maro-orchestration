package metrics

import (
	"encoding/json"
	"errors"
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
    _pyprobe_use(c["ws"])
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

// datedAt builds a step-costs row whose `recorded_at` DATE begins at exactly
// byte `start`, by sizing a leading pad field to put it there — and then
// asserting that it did.
//
// The assertion is the reason this is a function instead of a literal. The
// boundary being pinned is `line[:60]`, ten characters wide, so the two
// fixtures that matter differ by ONE character of padding. Hand-counting the
// braces and quotes of a JSON prefix is exactly the kind of arithmetic that
// looks right, lands at 49 or 52, and leaves the boundary untested while the
// case name claims otherwise.
func datedAt(t *testing.T, start int, cost float64) string {
	t.Helper()
	const stamp = "2026-08-26T03:00:00+00:00"
	head, tail := `{"g": "`, `", "recorded_at": "`
	pad := start - len(head) - len(tail)
	if pad < 0 {
		t.Fatalf("start=%d is inside the row's own framing", start)
	}
	line := head + strings.Repeat("p", pad) + tail + stamp +
		fmt.Sprintf(`", "cost_usd": %g}`, cost)
	if got := strings.Index(line, stamp[:10]); got != start {
		t.Fatalf("date landed at %d, wanted %d: %s", got, start, line)
	}
	return line
}

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
		// THE WINDOW'S EXACT EDGE. `today` is ten characters, so the cheap
		// check sees it when its last character lands at index 59 and misses
		// it at 60. Both fixtures below are built by measuring, not by
		// counting braces: the padding is sized so the date occupies exactly
		// [50,60) in the first and [51,61) in the second. Mutating the clip
		// to 59 and to 61 BOTH survived the earlier list, which had cases on
		// either side of the boundary but none at it (metrics r1, L23).
		{name: "the date ending exactly at the sixtieth character", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			datedAt(t, 50, 4.0),
			row(today+"02:00:00+00:00", 0.5)}},
		{name: "the date ending one character past the window", rows: []string{
			row(today+"01:00:00+00:00", 1.0),
			datedAt(t, 51, 4.0),
			row(today+"02:00:00+00:00", 0.5)}},
		// THE TWO CHECKS DISAGREEING. The cheap check finds today's date in a
		// goal_preview while recorded_at is YESTERDAY, so the strict check
		// rejects the row's cost — and, crucially, does NOT break: the scan
		// goes on to the older rows below it. Every other fixture here has
		// the two checks agreeing, so replacing the strict one with `if true`
		// survived the battery (metrics r1, L41).
		{name: "todays date in a preview beside a yesterday timestamp", rows: []string{
			row(today+"01:00:00+00:00", 1.5),
			`{"goal_preview": "ship ` + strings.TrimSuffix(today, "T") +
				`", "recorded_at": "` + yday + `23:00:00+00:00", "cost_usd": 8.0}`}},
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
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(payload)}
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
    _pyprobe_use(c["ws"])
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
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(payload)}
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
    _pyprobe_use(c["ws"])
    import importlib, orch_items
    importlib.reload(orch_items)
    importlib.reload(metrics)
    entries = metrics.load_step_costs(limit=c["limit"])
    # analyze_step_costs and estimate_loop_cost have NO try of their own, so
    # a store row that will not sum takes the caller down. Which exception,
    # and with which message, is the answer being compared — recording only
    # "it failed" would let the port raise for a different reason.
    try:
        a = metrics.analyze_step_costs(entries)
    except Exception as exc:
        out.append({"entries": entries,
                    "raised": "%s: %s" % (type(exc).__name__, exc)})
        continue
    try:
        plain = "%.17g" % metrics.estimate_loop_cost(c["nsteps"])
        texts = "%.17g" % metrics.estimate_loop_cost(
            c["nsteps"], c["texts"] or None)
    except Exception as exc:
        out.append({"entries": entries,
                    "raised": "%s: %s" % (type(exc).__name__, exc)})
        continue
    out.append({
        "entries": entries,
        "by_type_order": list(a["by_type"].keys()),
        # repr(), not "%.17g", for the TOKEN fields: they are the two values
        # analyze_step_costs does not coerce, so 2 and 2.0 are different
        # answers and a formatter that spelled both "2" would hide the one
        # divergence these fields can have.
        "by_type": {k: {kk: ("%.17g" % vv if kk == "avg_cost_usd"
                             else repr(vv) if kk.endswith("_tokens") else vv)
                        for kk, vv in v.items()}
                    for k, v in a["by_type"].items()},
        "expensive": a["expensive_types"],
        "total": "%.17g" % a["total_cost_usd"],
        "loop_cost_plain": plain,
        "loop_cost_texts": texts,
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
		// limit=0 is the boundary of `if limit > 0`. read_jsonl_tail takes
		// its FULL-SCAN path for a non-positive limit, so zero means "all
		// rows" and not "no rows" — `limit >= 0` would slice rows[len:] and
		// answer with an empty analysis. Nothing else in this list sits at
		// the boundary, and the mutant survived (metrics r1, L23).
		{name: "a limit of zero loads everything", limit: 0, nsteps: 2,
			rows: []string{row("research", 1000, 0.01), row("verify", 3000, 0.03)}},
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
		// The case above says it separates FLOOR division from truncation and
		// does not: -7000 + 1000 is -6000, which divides evenly by 2, so `//`
		// and `/` agree. Three rows summing to -5500 do not — Python floors to
		// -1834 and Go's `/` truncates to -1833. Replacing pyval.FloorDiv with
		// `/` survived the whole battery until this row existed (adversarial
		// metrics r1, MEDIUM — L28: a fixture whose comment names a boundary
		// it does not reach).
		{name: "a negative group total that does not divide evenly",
			limit: 100, nsteps: 2,
			rows: []string{row("refund", -7000, -0.07), row("refund", 1000, 0.01),
				row("refund", 500, 0.01), row("research", 5000, 0.05)}},
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

		// --- the sums are RAW, and these are the shapes that prove it ------
		//
		// analyze_step_costs sums the store's values with no coercion and no
		// try. Every case below crashed CPython and answered a plausible
		// number in the port, which is the worst possible pair of behaviours
		// (adversarial metrics r1, MEDIUM).
		{name: "a null cost_usd raises", limit: 100, nsteps: 3,
			rows: []string{row("research", 1000, 0.01), row("research", 2000, nil)}},
		{name: "a string cost_usd raises even though it looks numeric",
			limit: 100, nsteps: 3,
			rows: []string{row("research", 1000, "1.5")}},
		{name: "a null total_tokens raises", limit: 100, nsteps: 3,
			rows: []string{row("research", nil, 0.01)}},
		// ORDER: both fields are bad in the same group, and total_tokens is
		// summed first, so ITS message is the one that escapes. A port that
		// interleaved the two sums in one loop reports cost_usd here.
		{name: "total_tokens is summed before cost_usd", limit: 100, nsteps: 3,
			rows: []string{row("research", nil, nil)}},
		// The accumulator's type is in the message: a float has already been
		// added by the time the null arrives, so this says 'float' where the
		// case above says 'int'. Note the ROW ORDER — load_step_costs hands
		// entries over NEWEST FIRST, so the file's LAST row is summed first
		// and the null has to sit at the TOP of the file to be summed last.
		{name: "the accumulator type reaches the message", limit: 100, nsteps: 3,
			rows: []string{row("research", 2000, nil), row("research", 1000, 0.5)}},
		// A bool sums as an int — True is 1 — and does NOT raise.
		{name: "a bool cost sums as one", limit: 100, nsteps: 3,
			rows: []string{row("research", 1000, true), row("research", 2000, 0.5)}},
		// A FLOAT total_tokens makes total_tokens and avg_tokens floats, so
		// they render "3000.0" and "1500.0". The port typed both int and
		// truncated silently.
		{name: "a float total_tokens keeps the whole type float",
			limit: 100, nsteps: 3,
			rows: []string{row("research", 1000.5, 0.01), row("research", 2000, 0.03)}},
		// And the floor is a FLOOR: 2001.5 // 2 is 1000.0, not 1000.75.
		{name: "a float token total floors rather than truncating",
			limit: 100, nsteps: 3,
			rows: []string{row("verify", -1000.5, 0.01), row("verify", -1000, 0.03)}},
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
		Raised      string                    `json:"raised"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(payload)}
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
			got, err := AnalyzeStepCosts(entries)
			// The raise lane. A store row that will not sum is the one
			// answer a coercing port cannot give, so it is compared by
			// MESSAGE — an error for a different reason is not a match.
			// Checked BEFORE the L1 tripwire below, which reads a by_type
			// map a raising case never produced.
			if w.Raised != "" {
				if err == nil {
					t.Fatalf("cpython raised %q; go returned %+v", w.Raised, got)
				}
				// `type(exc).__name__ + ": " + str(exc)`, spelled here
				// rather than in Error(), which is str(exc) ALONE on
				// purpose — every ported `except ... as exc` renders it
				// with %s and would gain a class prefix CPython does not
				// print. The type assertion is part of the assertion: a
				// plain fmt.Errorf with the right words is not the same
				// answer, because no caller could except on it.
				var pe *pyval.PyErr
				if !errors.As(err, &pe) {
					t.Fatalf("raised %T (%v), not a *pyval.PyErr", err, err)
				}
				if got := pe.Class + ": " + pe.Msg; got != w.Raised {
					t.Errorf("raised %q, cpython %q", got, w.Raised)
				}
				if _, lerr := EstimateLoopCost(payload[i].WS, c.nsteps, nil); lerr == nil {
					t.Error("estimate_loop_cost swallowed the TypeError")
				}
				return
			}
			if err != nil {
				t.Fatalf("go raised %v; cpython did not", err)
			}
			// L1 tripwire: a case with rows whose analysis came back empty on
			// both sides would pass every assertion below without measuring.
			if len(c.rows) > 0 && len(w.ByTypeOrder) == 0 {
				t.Fatalf("cpython grouped nothing from %d rows", len(c.rows))
			}
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
				// repr, not an int: `2` and `2.0` are different answers,
				// and the int comparison this replaced could not tell them
				// apart in either direction.
				if r := pyval.Repr(gs.AvgTokens); r != strOfAny(ws["avg_tokens"]) {
					t.Errorf("%s avg_tokens = %s, cpython %s",
						k, r, strOfAny(ws["avg_tokens"]))
				}
				if r := pyval.Repr(gs.TotalTokens); r != strOfAny(ws["total_tokens"]) {
					t.Errorf("%s total_tokens = %s, cpython %s",
						k, r, strOfAny(ws["total_tokens"]))
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

			plain, perr := EstimateLoopCost(payload[i].WS, c.nsteps, nil)
			if perr != nil {
				t.Fatalf("estimate_loop_cost(n) raised %v", perr)
			}
			if s := fmt17(plain); s != w.LoopPlain {
				t.Errorf("estimate_loop_cost(n) = %s, cpython %s", s, w.LoopPlain)
			}
			texts, terr := EstimateLoopCost(payload[i].WS, c.nsteps, c.texts)
			if terr != nil {
				t.Fatalf("estimate_loop_cost(n, texts) raised %v", terr)
			}
			if s := fmt17(texts); s != w.LoopTexts {
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

// wsOf pulls the `ws` field out of any probe payload, so the workspaces a
// probe will write to are DECLARED to pyprobe rather than only appearing
// inside the Python snippet.
//
// Generic over the payload type on purpose: each probe here has its own
// case struct, and five hand-written loops is five chances for one of them
// to drift out of sync with the snippet it feeds — which is the shape of
// the finding this exists to close. It reads the json tag rather than a Go
// field name because the tag is what the snippet indexes by.
func wsOf[T any](payload []T) []string {
	raw, err := json.Marshal(payload)
	if err != nil {
		panic(err)
	}
	var rows []struct {
		WS string `json:"ws"`
	}
	if err := json.Unmarshal(raw, &rows); err != nil {
		panic(err)
	}
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		out = append(out, r.WS)
	}
	return out
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
