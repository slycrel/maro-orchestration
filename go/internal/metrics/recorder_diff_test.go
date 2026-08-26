package metrics

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyRecordSrc calls record_step_cost for each case in its own workspace and
// hands back the RAW LINE the writer appended, not a re-serialization of the
// returned dict.
//
// The distinction is the whole point: the row on disk is what the other
// runtime reads back, so the thing to compare is bytes in a file. A
// differential over the returned dict would agree with a port that wrote
// the keys in a different order, or wrote nothing at all.
const pyRecordSrc = `
import io, json, os, sys, importlib

args = json.loads(sys.argv[1])
out = []
for c in args:
    # _pyprobe_use is pyprobe's door: it realpaths BOTH sides before it
    # sets the variable. The hand-rolled assert "/.maro/" not in ws that
    # used to stand here compared unresolved strings, which a symlinked
    # temp dir walks straight past (metrics r1, MEDIUM).
    ws = _pyprobe_use(c["ws"])
    import metrics
    importlib.reload(metrics)
    entry = metrics.record_step_cost(
        c["step_text"], c["tokens_in"], c["tokens_out"], c["status"],
        goal=c["goal"], model=c["model"], elapsed_ms=c["elapsed_ms"],
        cache_read_tokens=c["cache_read_tokens"], loop_id=c["loop_id"],
        provider_cost_usd=c["provider_cost_usd"])
    path = metrics._step_costs_path()
    raw = path.read_text(encoding="utf-8").splitlines()[-1]
    # json.dumps with ITS defaults — ensure_ascii=True — because that is
    # what the writer used one line earlier. Passing ensure_ascii=False here
    # would compare the port against a spelling of the row that nothing in
    # the system ever writes.
    out.append({"line": raw, "returned": json.dumps(entry),
                "path": str(path)})
print(json.dumps(out))
`

// pyP90Src seeds nothing itself: the Go side writes the run cards into a
// workspace CPython then reads, so both runtimes see one directory tree and
// the mtime ordering is a property of that tree rather than of two
// independently created ones.
const pyP90Src = `
import io, json, os, sys, importlib

args = json.loads(sys.argv[1])
out = []
for c in args:
    _pyprobe_use(c["ws"])
    import metrics
    importlib.reload(metrics)
    metrics._run_cost_p90_cache.clear()
    v = metrics.successful_run_cost_p90(limit=c["limit"])
    out.append({"none": v is None, "value": (0.0 if v is None else v)})
print(json.dumps(out))
`

// idShape is `str(uuid.uuid4())[:12]` — eight hex, a hyphen, three hex.
// Written out because the slice is taken from the HYPHENATED spelling and a
// generator that produced twelve hex characters would look right in every
// eyeball check and match nothing.
var idShape = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{3}$`)

func TestRecordStepCostMatchesCPython(t *testing.T) {
	cases := []StepCostInput{
		// The plain estimate lane.
		{StepText: "run the test suite", TokensIn: 1200, TokensOut: 300,
			Status: "done", Goal: "ship the thing", Model: "claude-sonnet-4-6",
			ElapsedMS: 4100, LoopID: "loop-a"},
		// A provider figure DISPLACES the estimate and adds the conditional
		// key — the one row shape that carries fifteen fields, not fourteen.
		{StepText: "look up the adapter docs", TokensIn: 9000, TokensOut: 40,
			Status: "done", Model: "claude-opus-4-1", ProviderCostUSD: 0.4237,
			LoopID: "loop-b"},
		// A NEGATIVE provider cost is clamped to zero and therefore does NOT
		// displace the estimate. Clamping after the test would write a
		// negative cost_usd into the ledger the budget gate sums.
		{StepText: "write the report", TokensIn: 100, TokensOut: 100,
			Status: "blocked", ProviderCostUSD: -5, LoopID: "loop-c"},
		// ZERO TOKENS WITH A REAL PROVIDER COST — the row that made
		// estimated_cost_usd a pointer in the first place. llm.py:834's
		// `input_tokens or 0` estimates 0.0 for a row the provider still
		// billed for, so the two fields disagree and the estimate must still
		// be PRESENT and spelled "0.0", not omitted and not the int 0. Every
		// other case here carries non-zero tokens, so the reachable row that
		// motivated the pointer was not in the differential.
		{StepText: "cached completion", TokensIn: 0, TokensOut: 0,
			Status: "done", Model: "claude-sonnet-4-6",
			ProviderCostUSD: 0.4237, LoopID: "loop-z"},
		// A provider cost of exactly zero is the estimate lane: the test is
		// `> 0`, not `is not None`.
		{StepText: "check the mail", TokensIn: 10, TokensOut: 10,
			Status: "done", ProviderCostUSD: 0},
		// CACHE READS are a subset of tokens_in priced at a tenth, so this
		// row's cost is not derivable from the token counts alone.
		{StepText: "re-read the plan", TokensIn: 50000, TokensOut: 500,
			Status: "done", Model: "claude-sonnet-4-6", CacheReadTokens: 48000},
		// PREVIEW CLIPPING, at the boundary and past it, in code points.
		// A byte slice would cut a multibyte rune in half and write invalid
		// UTF-8 into a store CPython decodes strictly.
		{StepText: strings.Repeat("a", 119) + "b", TokensIn: 1, Status: "done"},
		{StepText: strings.Repeat("a", 120) + "TAIL", TokensIn: 1, Status: "done"},
		{StepText: strings.Repeat("é", 130), TokensIn: 1, Status: "done",
			Goal: strings.Repeat("レ", 90)},
		// The goal preview has its OWN width (80, not 120), and two
		// different widths in one row is exactly the shape a port
		// accidentally unifies.
		{StepText: "short", Goal: strings.Repeat("g", 81), TokensIn: 1,
			Status: "done"},
		// An UNKNOWN model falls through to the default rate rather than to
		// zero, and the model string is still recorded verbatim.
		{StepText: "ask the oracle", TokensIn: 1000, TokensOut: 1000,
			Status: "done", Model: "gpt-9-turbo-imaginary"},
		// Empty everything: the row still has all fourteen keys, and the
		// step type still classifies (to "general").
		{},
		// A step text that trips the classifier's word-boundary rules, so
		// the recorded step_type is a real classification and not "general"
		// for every case above it.
		{StepText: "debug the failing parser", TokensIn: 5, Status: "done"},
		// Rounding at the eighth decimal, where the estimate has more
		// digits than that.
		{StepText: "one token", TokensIn: 1, TokensOut: 1, Status: "done",
			Model: "claude-haiku-4-5"},
	}

	type payload struct {
		WS              string  `json:"ws"`
		StepText        string  `json:"step_text"`
		TokensIn        int     `json:"tokens_in"`
		TokensOut       int     `json:"tokens_out"`
		Status          string  `json:"status"`
		Goal            string  `json:"goal"`
		Model           string  `json:"model"`
		ElapsedMS       int     `json:"elapsed_ms"`
		CacheReadTokens int     `json:"cache_read_tokens"`
		LoopID          string  `json:"loop_id"`
		ProviderCostUSD float64 `json:"provider_cost_usd"`
	}
	pyArgs := make([]payload, len(cases))
	for i, c := range cases {
		pyArgs[i] = payload{WS: t.TempDir(), StepText: c.StepText,
			TokensIn: c.TokensIn, TokensOut: c.TokensOut, Status: c.Status,
			Goal: c.Goal, Model: c.Model, ElapsedMS: c.ElapsedMS,
			CacheReadTokens: c.CacheReadTokens, LoopID: c.LoopID,
			ProviderCostUSD: c.ProviderCostUSD}
	}

	type answer struct {
		Line     string `json:"line"`
		Returned string `json:"returned"`
		Path     string `json:"path"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(pyArgs)}
	probe.RunJSON(t, pyRecordSrc, &want, pyprobe.Arg(t, pyArgs))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		t.Run(caseName(i, c), func(t *testing.T) {
			w := want[i]
			// LIVE-STORE DISCIPLINE: assert the resolved path before
			// trusting anything written through it.
			if !strings.HasPrefix(w.Path, pyArgs[i].WS) {
				t.Fatalf("cpython wrote outside the fixture: %s", w.Path)
			}
			// The returned dict and the written line must be the same
			// object. Python returns the entry it serialized, so a
			// disagreement means the writer transformed something on the
			// way out — which is the half of this that a reader-only
			// differential could never see.
			if w.Returned != w.Line {
				t.Fatalf("cpython's returned dict and written line differ:\n%s\n%s",
					w.Returned, w.Line)
			}

			goWS := t.TempDir()
			row := RecordStepCost(goWS, c)
			gotPath, gpErr := StepCostsPath(goWS)
			if gpErr != nil {
				t.Fatal(gpErr)
			}
			raw, err := os.ReadFile(gotPath)
			if err != nil {
				t.Fatalf("go wrote no ledger: %v", err)
			}
			lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
			got := lines[len(lines)-1]

			// The id and the stamp are the two fields that cannot match by
			// construction. Each is checked for its SHAPE and then
			// normalised, rather than dropped — a port that wrote no id at
			// all would otherwise pass.
			if !idShape.MatchString(row.ID) {
				t.Errorf("id %q is not str(uuid4())[:12]", row.ID)
			}
			if _, err := time.Parse(time.RFC3339Nano, row.RecordedAt); err != nil {
				t.Errorf("recorded_at %q does not parse: %v", row.RecordedAt, err)
			}
			if !strings.HasSuffix(row.RecordedAt, "+00:00") {
				// isoformat() on an aware UTC datetime ends in +00:00, not
				// "Z". CPython before 3.11 refuses to read a "Z" back.
				t.Errorf("recorded_at %q is not isoformat's UTC spelling",
					row.RecordedAt)
			}
			if got, want := normalizeVolatile(got), normalizeVolatile(w.Line); got != want {
				t.Errorf("row differs\n--- cpython ---\n%s\n--- go      ---\n%s",
					want, got)
			}
		})
	}
}

// caseName keeps a subtest label short enough to read: several fixtures
// are 130 characters of the same rune on purpose.
func caseName(i int, c StepCostInput) string {
	name := c.StepText
	if len([]rune(name)) > 24 {
		name = string([]rune(name)[:24]) + "..."
	}
	if name == "" {
		name = "(empty)"
	}
	return itoa(i) + " " + name
}

// normalizeVolatile blanks the id and the timestamp — and ONLY those two —
// by rewriting their values in place, so the key order and every other
// field stay in the comparison.
func normalizeVolatile(line string) string {
	var re = regexp.MustCompile(
		`"id": "[^"]*"|"recorded_at": "[^"]*"`)
	return re.ReplaceAllStringFunc(line, func(m string) string {
		return strings.SplitN(m, ":", 2)[0] + `: "*"`
	})
}

func TestSuccessfulRunCostP90MatchesCPython(t *testing.T) {
	// card writes one run card and returns the workspace, staggering mtimes
	// so "the 200 most recent" is a real ordering rather than a tie.
	seed := func(t *testing.T, cards []map[string]any) string {
		t.Helper()
		ws := t.TempDir()
		base := time.Now().Add(-time.Duration(len(cards)) * time.Minute)
		for i, c := range cards {
			dir := filepath.Join(ws, "runs", "run-"+itoa(i))
			if err := os.MkdirAll(dir, 0o755); err != nil {
				t.Fatal(err)
			}
			raw, err := json.Marshal(c)
			if err != nil {
				t.Fatal(err)
			}
			p := filepath.Join(dir, "run_card.json")
			if err := os.WriteFile(p, raw, 0o644); err != nil {
				t.Fatal(err)
			}
			// Later index = newer.
			mt := base.Add(time.Duration(i) * time.Minute)
			if err := os.Chtimes(p, mt, mt); err != nil {
				t.Fatal(err)
			}
		}
		return ws
	}

	// class is `any`, not `string`, so a fixture can hand it a STRUCTURED
	// value. RUN_COST_SUCCESS_CLASSES is a tuple, so such a value is simply
	// not in it — and proving that it does not raise takes a fixture a
	// string-typed helper cannot express.
	card := func(cost any, class any) map[string]any {
		m := map[string]any{"success_class": class}
		if cost != nil {
			m["total_cost_usd"] = cost
		}
		return m
	}
	n := func(count int, cost float64, class string) []map[string]any {
		var out []map[string]any
		for i := 0; i < count; i++ {
			out = append(out, card(cost+float64(i)*0.01, class))
		}
		return out
	}

	// after reshapes the seeded workspace for the cases whose subject is the
	// FILESYSTEM rather than the card contents — a `runs` that is not a
	// directory, a run reached through a symlink, names whose lexical order
	// disagrees with their mtimes. Those are decision sites in
	// computeRunCostP90 (is_dir, glob, the stat-and-sort) that no card
	// payload can reach.
	cases := []struct {
		name  string
		cards []map[string]any
		limit int
		after func(*testing.T, string)
	}{
		// Below the sample floor: the answer is None, not a small-sample
		// p90, and a caller must be able to tell those apart.
		{"seven successes is too thin", n(7, 1.0, "success"), 200, nil},
		// Exactly at the floor. int(0.9*(8-1)) is 6, so this is the SEVENTH
		// smallest of eight — the index a rounding p90 would get wrong.
		{"eight successes is exactly enough", n(8, 1.0, "success"), 200, nil},
		{"nine successes", n(9, 1.0, "success"), 200, nil},
		{"twenty successes", n(20, 0.5, "success"), 200, nil},
		// done-unverified counts; anything else does not.
		{"done-unverified counts", n(9, 2.0, "done-unverified"), 200, nil},
		{"failures do not count", n(20, 2.0, "failed"), 200, nil},
		// A zero cost is FALSY and drops out of the distribution entirely,
		// so eight successes with one zero is seven samples: too thin.
		{"a zero cost is not a sample",
			append(n(7, 1.0, "success"), card(0.0, "success")), 200, nil},
		// A missing cost key, and a null one.
		{"a missing cost is not a sample",
			append(n(7, 1.0, "success"), card(nil, "success")), 200, nil},
		// The LIMIT truncates by recency, so a window that excludes the
		// cheap tail moves the answer.
		{"the limit cuts the older half",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), 10, nil},
		{"the limit below the sample floor",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), 7, nil},
		// Mixed classes interleaved, so the filter runs against a real mix
		// rather than a homogeneous list.
		{"mixed classes",
			append(append(n(6, 1.0, "success"), n(6, 50.0, "failed")...),
				n(6, 3.0, "done-unverified")...), 200, nil},
		// No runs directory at all.
		{"an empty workspace", nil, 200, nil},

		// ELEVEN. The index is `int(0.9 * (len - 1))`, and the plausible
		// off-by-one next to it is `int(0.9 * len) - 1`. Those two agree at
		// every sample size the cases above produce — 8, 9, 10, 12 and 20 —
		// so the whole list pinned the p90 without pinning its INDEX. At
		// eleven they part company for the first time: int(0.9*10) is 9 and
		// int(0.9*11)-1 is 8. The costs step by 0.01 so neighbouring indices
		// carry different values and the disagreement is visible.
		{"eleven successes, where the two index formulas diverge",
			n(11, 1.0, "success"), 200, nil},

		// A cost that is TRUTHY but floats to ZERO. Python gates on `if
		// cost` — the RAW value — and only then calls float(), so the string
		// "0.0" is a real sample worth 0.0. A port that gated on the FLOAT
		// would drop it and answer off a nine-sample distribution instead of
		// ten. Nothing else here can tell those apart, because every other
		// falsy cost is also zero after conversion.
		{"a truthy cost that floats to zero",
			append(n(9, 1.0, "success"), card("0.0", "success")), 200, nil},
		// The same seam from the other side: `True` is truthy and floats to
		// 1.0, and bools reach here because JSON has them.
		{"a boolean cost", append(n(9, 1.0, "success"), card(true, "success")), 200, nil},

		// A cost that float() REJECTS. Python has no per-card guard: the
		// ValueError escapes to the function's outer `except Exception`, so
		// ONE bad card discards the whole distribution and the answer is
		// None — not "the other nine". A port that skipped the bad card
		// would answer a number here, which is the failing-open shape.
		{"a non-numeric cost discards the whole sample",
			append(n(9, 1.0, "success"), card("abc", "success")), 200, nil},
		// A STRUCTURED class, which does NOT raise: RUN_COST_SUCCESS_CLASSES
		// is a tuple (metrics.py:249), so membership compares with `==` and
		// an unhashable value is merely absent. The card is skipped and the
		// other nine still answer. This port read the tuple as a set for a
		// round — guarded, commented, and green, because no fixture had a
		// non-string class — and answered None here. The cost must be truthy
		// or `and` short-circuits before the membership test ever runs.
		{"a structured success_class is skipped, not raised",
			append(n(9, 1.0, "success"), card(1.0, []any{"success"})), 200, nil},

		// A NEGATIVE limit. `cards[:-5]` drops the five most RECENT, which
		// is a real slice in Python and a panic in any port that reached for
		// `rows[:limit]`. The two halves differ by 90x so the window shows.
		{"a negative limit drops the newest",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), -5, nil},
		// A negative limit LARGER than the list. `cards[:-20]` over nine
		// cards is the empty list in Python — the floor at zero is a real
		// rule, not defensive padding — where an unfloored size+n is -11 and
		// slices backwards. The case above cannot see it: -5 against twenty
		// cards stays in range.
		{"a negative limit past the start of the list",
			n(9, 1.0, "success"), -20, nil},

		// `runs` EXISTS but is a regular file. `root.is_dir()` is False for
		// it exactly as it is for a missing path, so the answer is the same
		// None — but it is a different branch of the same `or`, and a port
		// that only checked for existence would walk on and glob a file.
		{"runs is a file, not a directory", n(9, 1.0, "success"), 200,
			func(t *testing.T, ws string) {
				runs := filepath.Join(ws, "runs")
				if err := os.RemoveAll(runs); err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(runs, []byte("not a directory"), 0o644); err != nil {
					t.Fatal(err)
				}
			}},

		// A run directory reached through a SYMLINK. `Path.glob("*/...")`
		// follows it, so the card counts. The natural Go spelling of "is
		// this a directory" during a hand-rolled walk is Lstat, which does
		// not follow and silently drops the run — a difference that only
		// appears once a workspace has an archived or relocated run linked
		// back in, which is what an operator does by hand at 2am.
		{"a run directory reached through a symlink", n(9, 1.0, "success"), 200,
			func(t *testing.T, ws string) {
				runs := filepath.Join(ws, "runs")
				real := filepath.Join(t.TempDir(), "elsewhere")
				if err := os.MkdirAll(real, 0o755); err != nil {
					t.Fatal(err)
				}
				raw, _ := json.Marshal(map[string]any{
					"total_cost_usd": 99.0, "success_class": "success"})
				if err := os.WriteFile(filepath.Join(real, "run_card.json"), raw, 0o644); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(real, filepath.Join(runs, "run-linked")); err != nil {
					t.Skipf("symlinks unavailable: %v", err)
				}
			}},

		// A DANGLING `run_card.json` SYMLINK — the other half of the pair
		// above, and the port had both halves the same way.
		//
		// The directory test follows symlinks (is_dir does). The CARD test
		// does NOT: measured on 3.14.3, `glob("*/run_card.json")` yields a
		// dead link, because the check is that the NAME is there, not that
		// the target is readable. CPython then reaches
		// `key=lambda p: p.stat().st_mtime`, which raises FileNotFoundError,
		// and the whole distribution comes back None — and is NOT cached.
		//
		// os.Stat on the card silently dropped it, which reads like the safe
		// choice and answers a confident p90 from the surviving ten cards,
		// then caches that for fifteen minutes. One dead link is the
		// difference between "no opinion" and a budget breaker computed from
		// a silently truncated sample (metrics r2, MEDIUM — M3).
		{"a dangling run card symlink", n(10, 1.0, "success"), 200,
			func(t *testing.T, ws string) {
				dir := filepath.Join(ws, "runs", "brokencard")
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(filepath.Join(ws, "no-such-target"),
					filepath.Join(dir, "run_card.json")); err != nil {
					t.Skipf("symlinks unavailable: %v", err)
				}
			}},

		// A CARD WITH ONE NON-UTF-8 BYTE, in a field nothing here reads.
		//
		// `card_path.read_text(encoding="utf-8")` decodes STRICTLY and runs
		// BEFORE json.loads, so the UnicodeDecodeError is caught by the
		// `except Exception: continue` and the card is not a sample. Go's
		// JSON decoder instead substitutes U+FFFD and hands back a perfectly
		// good map, so without an explicit strict decode the junk card is
		// ADMITTED.
		//
		// The cost is 500.0 deliberately: it fails OPEN in the direction that
		// matters, raising the p90, which raises both the warn line and the
		// 4x-p90 auto kill-line. SpendForLoops learned this in r1 and the fix
		// was never carried across the file (metrics r2, MEDIUM — M2).
		{"a run card with an invalid utf-8 byte", n(10, 1.0, "success"), 200,
			func(t *testing.T, ws string) {
				dir := filepath.Join(ws, "runs", "tornbyte")
				if err := os.MkdirAll(dir, 0o755); err != nil {
					t.Fatal(err)
				}
				raw := []byte("{\"total_cost_usd\": 500.0, " +
					"\"success_class\": \"success\", \"goal\": \"caf\xe9\"}")
				p := filepath.Join(dir, "run_card.json")
				if err := os.WriteFile(p, raw, 0o644); err != nil {
					t.Fatal(err)
				}
				// Newest, so a limit could not be what excludes it.
				mt := time.Now()
				if err := os.Chtimes(p, mt, mt); err != nil {
					t.Fatal(err)
				}
			}},

		// LEXICAL ORDER DISAGREES WITH MTIME. Every case above names its
		// runs run-0, run-1, … in the same order it stamps their mtimes, so
		// a port that sorted by NAME, or that never sorted at all and
		// relied on glob order, would agree with one that sorted by mtime.
		// Here the newest run is named run-a and the oldest run-z, and the
		// limit cuts the list in half, so only a real mtime sort keeps the
		// right ten. The costs differ by 90x across the cut.
		{"names whose lexical order reverses their mtimes",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), 10,
			func(t *testing.T, ws string) {
				runs := filepath.Join(ws, "runs")
				ents, err := os.ReadDir(runs)
				if err != nil {
					t.Fatal(err)
				}
				// run-N is the newest; give it the lexically FIRST name.
				for _, e := range ents {
					i := 0
					if _, err := fmt.Sscanf(e.Name(), "run-%d", &i); err != nil {
						t.Fatalf("unexpected run dir %q", e.Name())
					}
					dst := filepath.Join(runs, fmt.Sprintf("run-%c", 'z'-rune(i)))
					if err := os.Rename(filepath.Join(runs, e.Name()), dst); err != nil {
						t.Fatal(err)
					}
				}
			}},
	}

	type payload struct {
		WS    string `json:"ws"`
		Limit int    `json:"limit"`
	}
	pyArgs := make([]payload, len(cases))
	wss := make([]string, len(cases))
	for i, c := range cases {
		wss[i] = seed(t, c.cards)
		if c.after != nil {
			c.after(t, wss[i])
		}
		pyArgs[i] = payload{WS: wss[i], Limit: c.limit}
	}

	type answer struct {
		None  bool    `json:"none"`
		Value float64 `json:"value"`
	}
	var want []answer
	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: wsOf(pyArgs)}
	probe.RunJSON(t, pyP90Src, &want, pyprobe.Arg(t, pyArgs))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	// L1: if every case answered None the suite would agree while measuring
	// only the thin-history branch.
	somethingReal := false
	for _, w := range want {
		if !w.None {
			somethingReal = true
		}
	}
	if !somethingReal {
		t.Fatal("every case answered None — the fixtures never reach the p90")
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			// The cache is per-process and keyed by limit, so two cases
			// sharing a limit would serve each other's answer. Clearing is
			// what the Python probe does too.
			clearRunCostCache()
			got, ok := SuccessfulRunCostP90(wss[i], c.limit)
			if ok == want[i].None {
				t.Fatalf("cpython none=%v, go ok=%v (%v)", want[i].None, ok, got)
			}
			if ok && got != want[i].Value {
				t.Errorf("p90: cpython %v, go %v", want[i].Value, got)
			}
		})
	}
}

// TestRunCostP90CachesByLimit pins the three properties the TTL cache has
// that a plain memo would not:
//
//  1. a THIN-SAMPLE "no opinion" is cached — Python's `if _hit` is true for
//     the `(t, None)` tuple, so it does not recompute;
//  2. a MISSING-RUNS-DIR "no opinion" is NOT, because `if not
//     root.is_dir(): return None` returns before the cache write;
//  3. two different limits do not share an entry.
//
// Property 1 is what the earlier version of this test claimed to check, and
// it checked it on an EMPTY WORKSPACE — the one branch of the two that
// Python does not cache. So it asserted the port's flattened single exit was
// correct, and would have gone on doing that after the bug was found. Both
// no-opinion branches now appear here, separately, because "returns None"
// was never the property that distinguished them (adversarial metrics r1,
// HIGH — L1: a test whose fixture cannot reach the branch it names).
func TestRunCostP90CachesByLimit(t *testing.T) {
	ws := t.TempDir()
	runs := filepath.Join(ws, "runs")
	write := func(i int, cost float64) {
		dir := filepath.Join(runs, "run-"+itoa(i))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(map[string]any{
			"total_cost_usd": cost, "success_class": "success"})
		if err := os.WriteFile(filepath.Join(dir, "run_card.json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	// Property 2: no runs directory at all. The answer must NOT stick, or a
	// workspace's first run is invisible to the budget gate for 15 minutes.
	clearRunCostCache()
	if _, ok := SuccessfulRunCostP90(ws, 200); ok {
		t.Fatal("an empty workspace should have no opinion")
	}
	for i := 0; i < 12; i++ {
		write(i, 1.0+float64(i))
	}
	if _, ok := SuccessfulRunCostP90(ws, 200); !ok {
		t.Error("a missing runs dir was cached; the first run is invisible")
	}

	// Property 1: the directory exists but holds too few priced cards. More
	// cards are what would change that, and cards arrive per run, so this
	// answer IS remembered — the newly written twelve are not seen.
	clearRunCostCache()
	thin := t.TempDir()
	thinRuns := filepath.Join(thin, "runs")
	if err := os.MkdirAll(thinRuns, 0o755); err != nil {
		t.Fatal(err)
	}
	if _, ok := SuccessfulRunCostP90(thin, 200); ok {
		t.Fatal("an empty runs dir should have no opinion")
	}
	for i := 0; i < 12; i++ {
		dir := filepath.Join(thinRuns, "run-"+itoa(i))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(map[string]any{
			"total_cost_usd": 1.0 + float64(i), "success_class": "success"})
		if err := os.WriteFile(filepath.Join(dir, "run_card.json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	if _, ok := SuccessfulRunCostP90(thin, 200); ok {
		t.Error("a cached thin-sample answer was recomputed inside the TTL")
	}
	// Property 3: a different limit is a different question and recomputes.
	if _, ok := SuccessfulRunCostP90(thin, 100); !ok {
		t.Error("a different limit served another limit's cached answer")
	}

	// Property 4: the entry EXPIRES, and expires at 900 seconds.
	//
	// Nothing above can see this. Every call in this file happens within
	// microseconds of the last, so a cache with a 900-second TTL, a
	// 300-second TTL, and no expiry at all are indistinguishable — the
	// battery shortened the TTL and dropped the staleness test entirely, and
	// both mutants lived. The clock is a seam for exactly this reason.
	//
	// The pair of assertions is what pins the DURATION rather than merely
	// the existence of expiry: just under the TTL must still serve the
	// remembered answer, just over it must recompute.
	realNow := runCostNow
	defer func() { runCostNow = realNow }()
	base := time.Now()
	runCostNow = func() time.Time { return base }

	// Its own workspace: `thin` has twelve priced cards by now, so a fresh
	// compute there already has an opinion and the remembered one would be
	// indistinguishable from a recomputed one.
	aging := t.TempDir()
	agingRuns := filepath.Join(aging, "runs")
	if err := os.MkdirAll(agingRuns, 0o755); err != nil {
		t.Fatal(err)
	}
	clearRunCostCache()
	if _, ok := SuccessfulRunCostP90(aging, 200); ok {
		t.Fatal("an empty runs dir should have no opinion")
	}
	for i := 0; i < 12; i++ {
		dir := filepath.Join(agingRuns, "run-"+itoa(i))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		raw, _ := json.Marshal(map[string]any{
			"total_cost_usd": 1.0 + float64(i), "success_class": "success"})
		if err := os.WriteFile(filepath.Join(dir, "run_card.json"), raw, 0o644); err != nil {
			t.Fatal(err)
		}
	}
	// One second SHORT of the TTL: the remembered no-opinion still stands,
	// even though twelve priced cards are now on disk.
	runCostNow = func() time.Time { return base.Add(runCostCacheTTL - time.Second) }
	if _, ok := SuccessfulRunCostP90(aging, 200); ok {
		t.Error("the entry expired early — a cached answer was dropped inside the TTL")
	}
	// One second PAST it: recomputed, and now the twelve cards are visible.
	runCostNow = func() time.Time { return base.Add(runCostCacheTTL + time.Second) }
	if _, ok := SuccessfulRunCostP90(aging, 200); !ok {
		t.Error("a stale entry was served past the TTL — the gate never sees new runs")
	}
}

func TestTailCostScope(t *testing.T) {
	ctx := context.Background()
	if _, ok := TailCostScopeActive(ctx); ok {
		t.Error("a bare context reports an active scope")
	}
	// The no-op shape: a scope with no loop id has nothing to join to, and
	// reports inactive even though it was set.
	if _, ok := TailCostScopeActive(WithTailCostScope(ctx, "", "closure")); ok {
		t.Error("an empty loop id reports an active scope")
	}
	// `phase or "tail"` applies to the empty string, not only to a missing
	// argument.
	s, ok := TailCostScopeActive(WithTailCostScope(ctx, "loop-a", ""))
	if !ok || s.Phase != "tail" {
		t.Errorf("empty phase did not default: %+v ok=%v", s, ok)
	}
	// Nesting restores the outer scope, which is what the ContextVar token
	// reset does — and what a global would get wrong.
	outer := WithTailCostScope(ctx, "loop-a", "closure")
	inner := WithTailCostScope(outer, "loop-b", "quality")
	if s, _ := TailCostScopeActive(inner); s.LoopID != "loop-b" {
		t.Errorf("inner scope: %+v", s)
	}
	if s, _ := TailCostScopeActive(outer); s.LoopID != "loop-a" || s.Phase != "closure" {
		t.Errorf("outer scope did not survive the inner one: %+v", s)
	}
}

// TestRowIDFallbackKeepsTheShape drives newRowID's unreachable arm the only
// way a test can — by reproducing its formatting — because a fallback that
// emits an unreadable id is worse than no fallback, and nothing else in this
// package would ever notice.
//
// It exists because the fallback DID emit one for months: a leading 't' is
// not a hex digit, so every id it produced failed idShape, the regex the
// differential above uses to recognise a well-formed row id.
func TestRowIDFallbackKeepsTheShape(t *testing.T) {
	// rowIDFallback, not a local copy of its format string. The earlier
	// version of this loop re-typed the Sprintf here, which made it a test of
	// itself: it stayed green under every mutation of the real one.
	for _, n := range []int64{0, 1, 1 << 31, 1<<62 + 12345, 1756123456789012345} {
		got := rowIDFallback(n)
		if !idShape.MatchString(got) {
			t.Errorf("fallback id %q does not match %v", got, idShape)
		}
	}
	// And the real path, which is what actually runs.
	for i := 0; i < 64; i++ {
		if id := newRowID(); !idShape.MatchString(id) {
			t.Fatalf("newRowID produced %q, which does not match %v", id, idShape)
		}
	}
}

// pyConstantsSrc reads the tuning constants straight out of the module.
const pyConstantsSrc = `
import json, sys, inspect, re
import metrics
print(json.dumps({
    "min_samples": metrics.RUN_COST_MIN_SAMPLES,
    "card_limit": metrics.RUN_COST_CARD_LIMIT,
    "cache_ttl_s": metrics._RUN_COST_CACHE_TTL_S,
    "success_classes": list(metrics.RUN_COST_SUCCESS_CLASSES),
    "classes_is_tuple": isinstance(metrics.RUN_COST_SUCCESS_CLASSES, tuple),
    # The analysis window is a literal inside analyze_step_costs' own
    # entries-is-None default, not a named constant, so it is read back out
    # of the source rather than imported.
    "analysis_window": int(re.search(
        r"load_step_costs\(limit=(\d+)\)",
        inspect.getsource(metrics.analyze_step_costs)).group(1)),
}))
`

// TestTuningConstantsMatchCPython compares the numbers themselves, because
// no fixture can.
//
// A TTL, a card limit and an analysis window are only observable through
// inputs large enough or old enough to straddle them, and the differentials
// in this file are neither: the battery moved the card limit from 200 to
// 100, the TTL from 900s to 300s and the analysis window from 500 to 100,
// and all three survived every test in the package. Building fixtures with
// two hundred run cards and a fifteen-minute clock to catch a one-line
// transcription error is the expensive way to do what reading the constant
// does directly.
//
// This is the cheap half of covering a tuning constant. The expensive half —
// proving the constant is USED, and used at the right comparison — stays
// with the behavioural tests above; a constant that matches Python's and is
// then ignored would pass here.
func TestTuningConstantsMatchCPython(t *testing.T) {
	var want struct {
		MinSamples     int      `json:"min_samples"`
		CardLimit      int      `json:"card_limit"`
		CacheTTLS      float64  `json:"cache_ttl_s"`
		SuccessClasses []string `json:"success_classes"`
		ClassesIsTuple bool     `json:"classes_is_tuple"`
		AnalysisWindow int      `json:"analysis_window"`
	}
	pyprobe.Probe{Marker: "metrics.py"}.RunJSON(t, pyConstantsSrc, &want)

	if RunCostMinSamples != want.MinSamples {
		t.Errorf("min samples: cpython %d, go %d", want.MinSamples, RunCostMinSamples)
	}
	if RunCostCardLimit != want.CardLimit {
		t.Errorf("card limit: cpython %d, go %d", want.CardLimit, RunCostCardLimit)
	}
	if got := runCostCacheTTL.Seconds(); got != want.CacheTTLS {
		t.Errorf("cache ttl: cpython %vs, go %vs", want.CacheTTLS, got)
	}
	if analysisWindow != want.AnalysisWindow {
		t.Errorf("analysis window: cpython %d, go %d", want.AnalysisWindow, analysisWindow)
	}
	// The CONTAINER TYPE, not just its members. This port read the tuple as a
	// set for a round and raised TypeError on an unhashable class where
	// CPython skips the card; the members were right the whole time, so a
	// membership-only check would have agreed with the bug.
	if !want.ClassesIsTuple {
		t.Error("RUN_COST_SUCCESS_CLASSES is no longer a tuple — membership " +
			"may now raise on an unhashable class; re-read computeRunCostP90")
	}
	if len(runCostSuccessClasses) != len(want.SuccessClasses) {
		t.Errorf("success classes: cpython %v, go %v",
			want.SuccessClasses, runCostSuccessClasses)
	}
	for _, c := range want.SuccessClasses {
		if !runCostSuccessClasses[c] {
			t.Errorf("success class %q is missing from the port", c)
		}
	}
}
