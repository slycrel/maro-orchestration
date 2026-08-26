package metrics

import (
	"fmt"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyCostSrc drives metrics.estimate_cost and renders each answer at 17
// significant digits — enough to round-trip a float64 exactly.
//
// The comparison is on the RENDERED digits, not on a tolerance. This
// number is tested against named dollar thresholds (_STEP_COST_WARN_USD),
// and a comparison with slack in it agrees with any port that regrouped
// the arithmetic while still letting the two runtimes disagree about
// whether a step tripped the alarm.
const pyCostSrc = `
import json, sys
import metrics

_cases = json.loads(sys.argv[1])
out = []
for c in _cases:
    v = metrics.estimate_cost(c["tin"], c["tout"], c["model"] or None,
                              cache_read_tokens=c["cache"])
    out.append("%.17g" % v)
print(json.dumps(out))
`

type costCase struct {
	TIn   int    `json:"tin"`
	TOut  int    `json:"tout"`
	Model string `json:"model"`
	Cache int    `json:"cache"`
}

// TestEstimateCostMatchesCPython walks every key in the pricing table plus
// the edges around the cache clamp.
//
// Every model key is swept rather than sampled: the table is three naming
// systems for the same models (versioned IDs, adapter short forms, tier
// constants), and a key that was mistyped in the port does not fail — it
// falls back to Sonnet and prices an Opus step at a fifth. An enumeration
// is not a class, so the sweep is over the CPython table, not the Go one.
func TestEstimateCostMatchesCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}

	// The keys come from CPython, so a key the port never heard of shows up
	// as a mismatch instead of simply never being tested.
	const pyKeysSrc = `
import json
import metrics
print(json.dumps(sorted(metrics.COST_BY_MODEL)))
`
	var keys []string
	p.RunJSON(t, pyKeysSrc, &keys)
	if len(keys) == 0 {
		t.Fatal("CPython reported an empty pricing table")
	}

	var cases []costCase
	for _, k := range keys {
		cases = append(cases,
			costCase{TIn: 1_000_000, TOut: 1_000_000, Model: k},
			costCase{TIn: 12_345, TOut: 678, Model: k, Cache: 4_321},
			// The whole input served from cache — the multiplier's own
			// boundary, where fresh_in is exactly zero.
			costCase{TIn: 9_999, TOut: 1, Model: k, Cache: 9_999},
		)
	}
	cases = append(cases,
		// An unknown model and an EMPTY one take the same branch.
		costCase{TIn: 1_000_000, TOut: 1_000_000, Model: "no-such-model"},
		costCase{TIn: 1_000_000, TOut: 1_000_000, Model: ""},
		// The clamp: more cache reads than input must not credit money.
		costCase{TIn: 100, TOut: 0, Model: "opus", Cache: 1_000_000},
		// A negative cache stamp — max(0, ...) is the other half of the
		// clamp, and a port that wrote only min() would overcharge.
		costCase{TIn: 100, TOut: 0, Model: "opus", Cache: -50},
		// A NEGATIVE input count. Nothing sane writes one, but the clamp is
		// `max(0, min(cache, tokens_in))` and the two clamps only commute
		// while tokens_in >= 0 — so without a case on the wrong side of
		// that, swapping them is a mutation no fixture can see. CPython
		// answers with a negative cost here, and so must the port: this
		// pins the ORDER, not a defence.
		costCase{TIn: -100, TOut: 0, Model: "opus", Cache: 50},
		costCase{TIn: -100, TOut: 0, Model: "opus", Cache: -50},
		// Zero everywhere: the answer is 0.0, not -0.0 or NaN.
		costCase{TIn: 0, TOut: 0, Model: "sonnet"},
		// A single token on the priciest row — the smallest non-zero the
		// arithmetic can produce, where regrouping shows up first.
		costCase{TIn: 1, TOut: 1, Model: "claude-opus-4-6"},
		costCase{TIn: 1, TOut: 0, Model: "claude-opus-4-6", Cache: 1},
		// A realistic large step: the shape cost_spike actually judges.
		costCase{TIn: 4_000_000, TOut: 12_000, Model: "claude-opus-4-6", Cache: 3_900_000},
		// Awkward magnitudes, to catch a port that multiplied before
		// dividing (or the reverse) and lost the last bits.
		costCase{TIn: 3, TOut: 7, Model: "grok-4.5", Cache: 1},
		costCase{TIn: 999_999_999, TOut: 999_999_999, Model: "haiku", Cache: 333_333_333},
	)

	var want []string
	p.RunJSON(t, pyCostSrc, &want, pyprobe.Arg(t, cases))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}
	for i, c := range cases {
		got := fmt.Sprintf("%.17g", EstimateCost(c.TIn, c.TOut, c.Model, c.Cache))
		if got != want[i] {
			t.Errorf("estimate_cost(%d, %d, %q, cache=%d)\ncpython: %s\n     go: %s",
				c.TIn, c.TOut, c.Model, c.Cache, want[i], got)
		}
	}
}

// TestPricingTableMatchesCPython compares the table itself, so a rate that
// drifts on the Python side (Anthropic repricing, a new Grok row) fails
// here rather than becoming a silent per-step disagreement about spend.
func TestPricingTableMatchesCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}
	const pyTableSrc = `
import json
import metrics
print(json.dumps({
    "table": metrics.COST_BY_MODEL,
    "in": metrics.COST_PER_M_INPUT,
    "out": metrics.COST_PER_M_OUTPUT,
    "cache": metrics.CACHE_READ_MULTIPLIER,
}))
`
	var want struct {
		Table map[string]map[string]float64 `json:"table"`
		In    float64                       `json:"in"`
		Out   float64                       `json:"out"`
		Cache float64                       `json:"cache"`
	}
	p.RunJSON(t, pyTableSrc, &want)

	got := map[string]map[string]float64{}
	for k, r := range CostByModel {
		got[k] = map[string]float64{"input": r.Input, "output": r.Output}
	}
	if !reflect.DeepEqual(want.Table, got) {
		for k, v := range want.Table {
			g, ok := got[k]
			if !ok {
				t.Errorf("missing model %q", k)
			} else if !reflect.DeepEqual(v, g) {
				t.Errorf("model %q: cpython %v, go %v", k, v, g)
			}
		}
		for k := range got {
			if _, ok := want.Table[k]; !ok {
				t.Errorf("extra model %q not in CPython", k)
			}
		}
		t.FailNow()
	}
	if want.In != CostPerMInput || want.Out != CostPerMOutput ||
		want.Cache != CacheReadMultiplier {
		t.Fatalf("fallback rates differ: cpython %v/%v/%v, go %v/%v/%v",
			want.In, want.Out, want.Cache,
			CostPerMInput, CostPerMOutput, CacheReadMultiplier)
	}
}

const pyStepTypeSrc = `
import json, sys
import metrics
print(json.dumps([metrics.classify_step_type(t)
                  for t in json.loads(sys.argv[1])]))
`

// TestClassifyStepTypeMatchesCPython sweeps the classifier.
//
// The port does not hand these patterns to Go's regexp, and this test is
// the reason. Python's `\b` for a str pattern is UNICODE-aware — `\w` is
// str.isalnum() plus underscore — while RE2's `\b` is ASCII-only. So the
// step text "fixé" has a word boundary after "fix" in Go and none in
// Python: Go buckets the step as implement, CPython as general, and the
// evolver's per-step-type cost grouping in a SHARED store gets two
// different answers about the same step.
func TestClassifyStepTypeMatchesCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}

	texts := []string{
		"",
		"   ",
		"do the thing",
		// One per bucket, in the table's own order.
		"research the competitors",
		"summarize the findings",
		"analyze the results",
		"write the report",
		"verify the invariant",
		"implement the parser",
		"plan the migration",
		// Order IS the classification: the first entry that matches wins,
		// so a step naming two buckets is the earlier one.
		"research and implement the parser",
		"implement and research the parser",
		"plan, then verify, then write",
		"write a plan",
		// The alternation members that are easy to drop.
		"investigate the outage", "find the leak", "search the logs",
		"fetch the manifest", "retrieve the archive",
		"summarise the findings", "compile the corpus", "distill the notes",
		"condense the log", "aggregate the counts",
		"analyse the results", "assess the risk", "evaluate the options",
		"compare the runs", "review the diff", "examine the trace",
		"draft the note", "create the file", "generate the report",
		"compose the reply", "produce the artifact", "document the api",
		"check the output", "confirm the fix", "validate the schema",
		"test the parser", "ensure the invariant", "prove the bound",
		"build the image", "code the adapter", "develop the feature",
		"refactor the loop", "fix the bug", "add feature to the cli",
		"design the schema", "architect the system", "outline the doc",
		"decompose the goal", "structure the output",
		// look\s*up: zero, one, several, and a tab.
		"lookup the id", "look up the id", "look    up the id",
		"look\tup the id", "look\nup the id",
		// ...and a NON-BREAKING space, which Python's `\s` matches and
		// RE2's does not.
		"look up the id",
		// \x1c-\x1f are the four separators Python's str.isspace() calls
		// whitespace and Go's unicode.IsSpace does not — measured on this
		// box, re.match with a \\s* between the words matches all four. They
		// are why the port reaches for pytext.IsSpace, not the stdlib one.
		"look\x1cup the id", "look\x1fup the id",
		// An ideographic space, matched by both runtimes.
		"look　up the id",
		// A zero-width space is whitespace to NEITHER, so this one must
		// not classify as research — the negative side of the same claim.
		"look​up the id",
		// The extra matcher belongs to the RESEARCH row, so it has to be
		// consulted before any LATER row's literals. A port that ran every
		// row's words first and the look-up matcher afterwards would call
		// these implement.
		"look up and build the image",
		"look\tup and build the image",
		// The boundary itself. A substring inside a longer word must not
		// match, on either side.
		"prefind the leak", "findings from the run", "refined the query",
		"testing the parser", "latest results", "contest the claim",
		"planner tuning", "unplanned outage",
		// Punctuation and digits abutting the word — non-word runes on one
		// side, so the boundary HOLDS.
		"(fix) the bug", "fix-the-bug", "fix.the.bug", "fix/the/bug",
		"fix_the_bug", "fix2 the bug", "2fix the bug",
		// Uppercase: the text is lowered before matching.
		"FIX THE BUG", "Research The Competitors", "LOOK UP the id",
		// The Unicode boundary. In Go's ASCII-only `\b` every one of these
		// would match; in Python none does.
		"fixé the bug", "éfix the bug", "planeré",
		"testü the parser", "ütest the parser",
		// Digits and marks in other scripts are word characters too.
		"fix٠ the bug", "fix中 the bug",
		// A combining mark is NOT alnum, so it does not block the boundary
		// — the one place isWordRune must not simply say "is a letter".
		"fix́ the bug",
		// The dotted capital I lowercases to two runes, which moves every
		// index after it — the reason the port lowers with pytext.Lower.
		"İmplement the parser", "FİX the bug",
		// A step text with no letters at all.
		"1234 5678", "!!! ???",
	}

	var want []string
	p.RunJSON(t, pyStepTypeSrc, &want, pyprobe.Arg(t, texts))
	if len(want) != len(texts) {
		t.Fatalf("probe returned %d answers for %d texts", len(want), len(texts))
	}
	for i, txt := range texts {
		got := ClassifyStepType(txt)
		if got != want[i] {
			t.Errorf("classify_step_type(%q)\ncpython: %s\n     go: %s",
				txt, want[i], got)
		}
	}
}

// TestEveryStepTypeKeywordIsReachable walks the CPython patterns rather
// than the Go table, and asserts each alternative both matches on its own
// and is attributed to the right bucket.
//
// The sweep above can only ever exercise the words its fixtures name. A
// keyword that was mistyped in the port classifies as "general", which is
// a legitimate answer for a great many step texts — so nothing else here
// would notice.
func TestEveryStepTypeKeywordIsReachable(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}
	const pyPatternsSrc = `
import json
import metrics
print(json.dumps([[st, pat] for st, pat in metrics._STEP_TYPE_PATTERNS]))
`
	var pairs [][]string
	p.RunJSON(t, pyPatternsSrc, &pairs)
	if len(pairs) != len(stepTypePatterns) {
		t.Fatalf("cpython has %d pattern rows, go has %d",
			len(pairs), len(stepTypePatterns))
	}
	for i, pr := range pairs {
		st, pat := pr[0], pr[1]
		if stepTypePatterns[i].stepType != st {
			t.Fatalf("row %d: cpython bucket %q, go %q", i, st, stepTypePatterns[i].stepType)
		}
		// `\b(a|b|c)\b` — peel the fixed wrapper and split the alternation.
		body := strings.TrimSuffix(strings.TrimPrefix(pat, `\b(`), `)\b`)
		if body == pat {
			t.Fatalf("row %d pattern %q is not the expected shape", i, pat)
		}
		for _, alt := range strings.Split(body, "|") {
			// The one non-literal alternative is exercised by the sweep.
			if strings.Contains(alt, `\s`) {
				continue
			}
			// Each keyword ALONE must land in its own bucket. Where an
			// earlier row also claims the word, CPython is the authority on
			// which one wins, so ask it rather than asserting st.
			var expect []string
			p.RunJSON(t, pyStepTypeSrc, &expect, pyprobe.Arg(t, []string{alt}))
			if got := ClassifyStepType(alt); got != expect[0] {
				t.Errorf("keyword %q (row %q): cpython %s, go %s",
					alt, st, expect[0], got)
			}
		}
	}
}
