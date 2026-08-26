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
			gotPath := StepCostsPath(goWS)
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

	card := func(cost any, class string) map[string]any {
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

	cases := []struct {
		name  string
		cards []map[string]any
		limit int
	}{
		// Below the sample floor: the answer is None, not a small-sample
		// p90, and a caller must be able to tell those apart.
		{"seven successes is too thin", n(7, 1.0, "success"), 200},
		// Exactly at the floor. int(0.9*(8-1)) is 6, so this is the SEVENTH
		// smallest of eight — the index a rounding p90 would get wrong.
		{"eight successes is exactly enough", n(8, 1.0, "success"), 200},
		{"nine successes", n(9, 1.0, "success"), 200},
		{"twenty successes", n(20, 0.5, "success"), 200},
		// done-unverified counts; anything else does not.
		{"done-unverified counts", n(9, 2.0, "done-unverified"), 200},
		{"failures do not count", n(20, 2.0, "failed"), 200},
		// A zero cost is FALSY and drops out of the distribution entirely,
		// so eight successes with one zero is seven samples: too thin.
		{"a zero cost is not a sample",
			append(n(7, 1.0, "success"), card(0.0, "success")), 200},
		// A missing cost key, and a null one.
		{"a missing cost is not a sample",
			append(n(7, 1.0, "success"), card(nil, "success")), 200},
		// The LIMIT truncates by recency, so a window that excludes the
		// cheap tail moves the answer.
		{"the limit cuts the older half",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), 10},
		{"the limit below the sample floor",
			append(n(10, 0.10, "success"), n(10, 9.00, "success")...), 7},
		// Mixed classes interleaved, so the filter runs against a real mix
		// rather than a homogeneous list.
		{"mixed classes",
			append(append(n(6, 1.0, "success"), n(6, 50.0, "failed")...),
				n(6, 3.0, "done-unverified")...), 200},
		// No runs directory at all.
		{"an empty workspace", nil, 200},
	}

	type payload struct {
		WS    string `json:"ws"`
		Limit int    `json:"limit"`
	}
	pyArgs := make([]payload, len(cases))
	wss := make([]string, len(cases))
	for i, c := range cases {
		wss[i] = seed(t, c.cards)
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
	for _, n := range []int64{0, 1, 1 << 31, 1<<62 + 12345, 1756123456789012345} {
		got := fmt.Sprintf("%08x-%03x", uint32(n), uint16(n>>32)&0xFFF)
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
