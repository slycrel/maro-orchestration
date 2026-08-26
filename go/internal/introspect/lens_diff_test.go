package introspect

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyLensSrc builds a LoopDiagnosis and a list of StepProfile directly and
// runs each named lens through the DEFAULT REGISTRY, so the cost overwrite
// and the registration order are part of what is measured rather than
// bypassed by calling the functions.
const pyLensSrc = `
import json, sys
import introspect

out = []
for c in json.loads(sys.argv[1]):
    d = introspect.LoopDiagnosis(
        loop_id=c["diag"].get("loop_id", "L"),
        failure_class=c["diag"].get("failure_class", "healthy"),
        severity=c["diag"].get("severity", "info"),
        evidence=c["diag"].get("evidence", []),
        recommendation=c["diag"].get("recommendation", ""),
        steps_done=c["diag"].get("steps_done", 0),
        steps_blocked=c["diag"].get("steps_blocked", 0),
        steps_total=c["diag"].get("steps_total", 0),
        project=c["diag"].get("project", ""))
    profiles = [introspect.StepProfile(
        step_idx=p.get("step_idx", 0), text=p.get("text", ""),
        status=p.get("status", ""), tokens=p.get("tokens", 0),
        elapsed_ms=p.get("elapsed_ms", 0),
        cache_read_tokens=p.get("cache_read_tokens", 0),
        tokens_in=p.get("tokens_in", 0), tokens_out=p.get("tokens_out", 0),
        model=p.get("model", "")) for p in c["profiles"]]
    reg = introspect.get_lens_registry()
    r = reg.run(c["lens"], d, profiles)
    out.append({"lens_name": r.lens_name, "findings": list(r.findings),
                "action": r.action, "confidence": r.confidence,
                "cost": r.cost})
print(json.dumps(out))
`

// pyRunLensesSrc measures the whole free-lens sweep: which lenses survive
// the has-findings filter, and in what ORDER they come back.
const pyRunLensesSrc = `
import json, sys
import introspect

out = []
for c in json.loads(sys.argv[1]):
    d = introspect.LoopDiagnosis(
        loop_id=c["diag"].get("loop_id", "L"),
        failure_class=c["diag"].get("failure_class", "healthy"),
        severity=c["diag"].get("severity", "info"),
        evidence=c["diag"].get("evidence", []),
        recommendation=c["diag"].get("recommendation", ""),
        steps_done=c["diag"].get("steps_done", 0),
        steps_blocked=c["diag"].get("steps_blocked", 0),
        steps_total=c["diag"].get("steps_total", 0))
    profiles = [introspect.StepProfile(
        step_idx=p.get("step_idx", 0), text=p.get("text", ""),
        status=p.get("status", ""), tokens=p.get("tokens", 0),
        elapsed_ms=p.get("elapsed_ms", 0),
        cache_read_tokens=p.get("cache_read_tokens", 0),
        tokens_in=p.get("tokens_in", 0), tokens_out=p.get("tokens_out", 0),
        model=p.get("model", "")) for p in c["profiles"]]
    out.append([{"lens_name": r.lens_name, "findings": list(r.findings),
                 "action": r.action, "confidence": r.confidence,
                 "cost": r.cost}
                for r in introspect.run_lenses(d, profiles)])
print(json.dumps(out))
`

type pyProfile struct {
	StepIdx         int    `json:"step_idx"`
	Text            string `json:"text"`
	Status          string `json:"status"`
	Tokens          int    `json:"tokens"`
	ElapsedMS       int    `json:"elapsed_ms"`
	CacheReadTokens int    `json:"cache_read_tokens"`
	TokensIn        int    `json:"tokens_in"`
	TokensOut       int    `json:"tokens_out"`
	Model           string `json:"model"`
}

type pyDiag struct {
	LoopID       string   `json:"loop_id"`
	FailureClass string   `json:"failure_class"`
	Severity     string   `json:"severity"`
	Evidence     []string `json:"evidence"`
	Recommend    string   `json:"recommendation"`
	StepsDone    int      `json:"steps_done"`
	StepsBlocked int      `json:"steps_blocked"`
	StepsTotal   int      `json:"steps_total"`
	Project      string   `json:"project"`
}

// pyLensAnswer mirrors what the probe returns. `action` is Optional[str] in
// Python, so it decodes to a *string and a nil pointer IS None — a plain
// string field would have made None and "" indistinguishable, which is the
// one divergence this file exists to pin.
type pyLensAnswer struct {
	LensName   string   `json:"lens_name"`
	Findings   []string `json:"findings"`
	Action     *string  `json:"action"`
	Confidence float64  `json:"confidence"`
	Cost       string   `json:"cost"`
}

type lensCase struct {
	name     string
	lens     string
	diag     pyDiag
	profiles []pyProfile
}

func (c lensCase) goDiag() LoopDiagnosis {
	return LoopDiagnosis{
		LoopID: c.diag.LoopID, FailureClass: c.diag.FailureClass,
		Severity: c.diag.Severity, Evidence: c.diag.Evidence,
		Recommendation: c.diag.Recommend, StepsDone: c.diag.StepsDone,
		StepsBlocked: c.diag.StepsBlocked, StepsTotal: c.diag.StepsTotal,
		Project: c.diag.Project,
	}
}

func (c lensCase) goProfiles() []StepProfile {
	out := make([]StepProfile, 0, len(c.profiles))
	for _, p := range c.profiles {
		out = append(out, StepProfile{
			StepIdx: p.StepIdx, Text: p.Text, Status: p.Status,
			Tokens: p.Tokens, ElapsedMS: p.ElapsedMS,
			CacheReadTokens: p.CacheReadTokens, TokensIn: p.TokensIn,
			TokensOut: p.TokensOut, Model: p.Model,
		})
	}
	return out
}

// prof is the shorthand every fixture below is built from. Named prof
// rather than step because diagnose_diff_test.go already owns `step` for a
// JSON event line — one package, one name. Token volume and
// tokens_in are set together because a profile with `tokens` but no
// `tokens_in` has a cost of zero, which silently disables the cost lens —
// the kind of confound L41 names.
func prof(idx int, status, text string, tokens, out, ms int) pyProfile {
	return pyProfile{StepIdx: idx, Text: text, Status: status,
		Tokens: tokens + out, ElapsedMS: ms, TokensIn: tokens,
		TokensOut: out, Model: "grok-4.5"}
}

func lensCases() []lensCase {
	// Reused shapes. Each fixture below names which rule it is the only
	// discriminator for.
	threeDone := []pyProfile{
		prof(1, "done", "fetch the widget index", 1000, 100, 900),
		prof(2, "done", "parse lexer output", 1000, 100, 900),
		prof(3, "done", "render report tables", 1000, 100, 900),
	}
	return []lensCase{
		// --- cost -------------------------------------------------------
		{name: "cost with no profiles", lens: "cost"},
		{name: "cost when every step is free", lens: "cost", profiles: []pyProfile{
			{StepIdx: 1, Text: "a", Status: "done"},
		}},
		// A single step over half the loop's dollars, which is the only
		// path to the dominance finding and its action.
		{name: "cost dominated by one step", lens: "cost", profiles: []pyProfile{
			prof(1, "done", "the expensive one", 900000, 1000, 900),
			prof(2, "done", "the cheap one", 1000, 10, 900),
		}},
		// Exactly at 50%: the threshold is `> 50`, so two identical steps
		// must NOT trip it.
		{name: "cost split exactly in half", lens: "cost", profiles: []pyProfile{
			prof(1, "done", "one", 100000, 1000, 900),
			prof(2, "done", "two", 100000, 1000, 900),
		}},
		// Ties go to the FIRST maximum, so the two steps must be
		// distinguishable in the rendered line.
		{name: "cost tie goes to the first step", lens: "cost", profiles: []pyProfile{
			prof(7, "done", "first", 100000, 1000, 900),
			prof(9, "done", "second", 100000, 1000, 900),
		}},
		// The cheap-keyword scan: over 10K tokens AND a keyword.
		{name: "a cheap-looking step over the token bar", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "Verify the parser output is well formed",
					20000, 100, 900),
				prof(2, "done", "write the thing", 1000, 10, 900),
			}},
		// Exactly 10000 tokens is NOT over the bar.
		{name: "a cheap-looking step exactly at the bar", lens: "cost",
			profiles: []pyProfile{
				{StepIdx: 1, Text: "verify things", Status: "done",
					Tokens: 10000, TokensIn: 9000, TokensOut: 1000,
					Model: "grok-4.5", ElapsedMS: 900},
			}},
		// The keyword match is on LOWERCASED, PYTHON-SPLIT words, so a
		// capitalised keyword must hit and a keyword glued to punctuation
		// must not.
		{name: "a keyword that is part of a longer word", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "reclassify everything", 20000, 100, 900),
			}},
		// Escaped rather than written raw: an invisible separator in a Go
		// source line is a fixture nobody can review by reading it.
		{name: "a keyword separated by a unicode space", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "please\u00a0Check\u3000this", 20000, 100, 900),
			}},
		// The average line renders through `{:,.0f}`, so it needs a value
		// with a half in it and one wide enough to group.
		{name: "an average that rounds half to even", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "a", 1000, 0, 900),
				prof(2, "done", "b", 1001, 0, 900),
				prof(3, "blocked", "c", 500000, 0, 900),
			}},
		{name: "an average wide enough to group", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "a", 1234567, 0, 900),
			}},
		// No done steps at all: the average line must be absent.
		{name: "cost with no done steps", lens: "cost", profiles: []pyProfile{
			prof(1, "blocked", "a", 100000, 100, 900),
		}},
		// NEGATIVE COST. `estimate_cost` is linear in its token counts, so
		// a store carrying a negative tokens_in sums below zero — which
		// passes the `total == 0` return and lands on the `total > 0`
		// guard that looks dead above it. With one such step the guard is
		// the whole answer: it holds topPct at 0, where dropping it would
		// divide a negative by itself and report "100% of loop cost".
		{name: "a single step with a negative cost", lens: "cost",
			profiles: []pyProfile{
				{StepIdx: 4, Text: "a refunded step", Status: "done",
					TokensIn: -50000, Model: "grok-4.5", ElapsedMS: 900},
			}},
		// A tie at the maximum can only RENDER when topPct > 50, and two
		// tied positive steps can never exceed half of a total that
		// contains them both. A third step with a negative cost shrinks
		// the total below either one, which is the only way to get a tie
		// and a dominance line at once — so this is the only fixture that
		// can see which of the two tied steps `max` picks.
		{name: "a tie at the maximum with a negative third step", lens: "cost",
			profiles: []pyProfile{
				prof(7, "done", "first", 100000, 1000, 900),
				prof(9, "done", "second", 100000, 1000, 900),
				prof(11, "done", "refund", -190000, 0, 900),
			}},
		// The keyword scan splits with Python's str.split(), which treats
		// \x1c (FILE SEPARATOR) as whitespace where Go's strings.Fields
		// does not. Without this the two splitters agree on every fixture.
		{name: "a keyword behind a file separator", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "please\x1cverify things", 20000, 100, 900),
			}},
		// "count" is the LAST entry of cheap_keywords and the only one no
		// other fixture uses, so dropping it from the set is invisible
		// without a step whose sole keyword is this one.
		{name: "a step whose only cheap keyword is count", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "count the rows", 20000, 100, 900),
			}},
		// The cheap-keyword line clips the step text at 50 RUNES. A text
		// under the limit cannot see the number, and an ASCII text cannot
		// see runes-versus-bytes, so it has to be both long and multibyte.
		{name: "a cheap step with a long multibyte text", lens: "cost",
			profiles: []pyProfile{
				prof(1, "done", "verify "+strings.Repeat("漢", 60),
					20000, 100, 900),
			}},

		// --- architecture -----------------------------------------------
		{name: "architecture under the three-step floor", lens: "architecture",
			profiles: threeDone[:2]},
		{name: "three independent done steps", lens: "architecture",
			profiles: threeDone},
		// One shared content word is still "independent" (`<= 1`); two is
		// not. These two cases are the boundary.
		{name: "pairs sharing exactly one word", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "parse the widget", 100, 10, 900),
				prof(2, "done", "render the widget", 100, 10, 900),
				prof(3, "done", "ship the widget", 100, 10, 900),
			}},
		{name: "pairs sharing two words", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "parse widget index", 100, 10, 900),
				prof(2, "done", "reparse widget index", 100, 10, 900),
				prof(3, "done", "recheck widget index", 100, 10, 900),
			}},
		// Filler words must not count as overlap.
		{name: "pairs sharing only filler", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "the alpha and the beta", 100, 10, 900),
				prof(2, "done", "the gamma and the delta", 100, 10, 900),
				prof(3, "done", "the epsilon and the zeta", 100, 10, 900),
			}},
		// A blocked step followed by a done one — the reordering finding,
		// which must appear AFTER every independence finding.
		{name: "a blocked step before a done one", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 900),
				prof(2, "blocked", "beta", 100, 10, 900),
				prof(3, "done", "gamma", 100, 10, 900),
			}},
		{name: "a stuck step before a done one", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 900),
				prof(2, "stuck", "beta", 100, 10, 900),
				prof(3, "done", "gamma", 100, 10, 900),
			}},
		// THE OUTLIER RULE IS VACUOUS AT ITS OWN THRESHOLD, and every
		// fixture here is shaped around that. The gate admits three
		// token-bearing steps, but with exactly three no step can be an
		// outlier: the bar is 3*floor(S/3), and 3*floor(S/3) > S-3, so for
		// any step t with S >= t the bar is at least t. Four steps is the
		// first width where the rule can fire, and only when the other
		// three are small.
		//
		// This was found by a mutation battery. The three fixtures that
		// used to sit here were named "an outlier over both bars" and so
		// on, all three had three steps, and NONE of them ever entered the
		// finding — so six separate mutations of the rule survived while
		// the cases that were supposed to cover it passed. A fixture that
		// cannot reach its rule reads exactly like one that does.
		{name: "an outlier over both bars", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 1, 0, 900),
				prof(2, "done", "beta", 1, 0, 900),
				prof(3, "done", "gamma", 1, 0, 900),
				prof(4, "done", "delta", 900000, 0, 900),
			}},
		// Over the 3x ratio but UNDER 50000, so only the token floor
		// stops it. Kills both "drop the floor" and "the floor is 40000".
		{name: "an outlier over the ratio but under 50000", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 1, 0, 900),
				prof(2, "done", "beta", 1, 0, 900),
				prof(3, "done", "gamma", 1, 0, 900),
				prof(4, "done", "delta", 45000, 0, 900),
			}},
		// Over 50000 but under the ratio — the mirror gate.
		{name: "a step over 50000 but under the ratio", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 60000, 0, 900),
				prof(2, "done", "beta", 60000, 0, 900),
				prof(3, "done", "gamma", 60000, 0, 900),
				prof(4, "done", "delta", 70000, 0, 900),
			}},
		// EXACTLY at the bar: 80000/4 floors to 20000 and the step carries
		// 3*20000. The comparison is `>`, so this must NOT fire, and it is
		// the only case that separates `>` from `>=`.
		{name: "a step exactly at three times the average", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 10000, 0, 900),
				prof(2, "done", "beta", 9999, 0, 900),
				prof(3, "done", "gamma", 1, 0, 900),
				prof(4, "done", "delta", 60000, 0, 900),
			}},
		// The average FLOORS. 80003/4 is 20000.75, which floors to 20000
		// (bar 60000) and rounds to 20001 (bar 60003) — and the heavy step
		// carries 60001, between the two. Any fixture whose sum divides
		// evenly cannot tell the two divisions apart.
		{name: "an average that floors below the rounded one",
			lens: "architecture", profiles: []pyProfile{
				prof(1, "done", "alpha", 10000, 0, 900),
				prof(2, "done", "beta", 10001, 0, 900),
				prof(3, "done", "gamma", 1, 0, 900),
				prof(4, "done", "delta", 60001, 0, 900),
			}},
		// Only two steps carry tokens, so the `len(tokens) >= 3` gate
		// fails even though there are three profiles. Note that relaxing
		// that gate to 2 would still find nothing — with two values the
		// bar 3*floor((a+b)/2) is at least 1.5x the larger of them — so
		// this fixture pins the gate's SHAPE, not a reachable difference.
		{name: "only two steps carry tokens", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 1000, 0, 900),
				prof(2, "done", "beta", 1000, 0, 900),
				{StepIdx: 3, Text: "gamma", Status: "done", ElapsedMS: 900},
			}},
		// A ZERO-token step among two others. The filter is `> 0`, so the
		// list holds one value and the gate fails; an inclusive filter
		// would admit all three, drop the average to a third, and report
		// the heavy step as an outlier.
		{name: "a zero-token step beside a heavy one", lens: "architecture",
			profiles: []pyProfile{
				{StepIdx: 1, Text: "alpha", Status: "done", ElapsedMS: 900},
				{StepIdx: 2, Text: "beta", Status: "done", ElapsedMS: 900},
				prof(3, "done", "gamma", 900001, 0, 900),
			}},
		// BOTH rules fire. The parallel action is set first and the
		// outlier action is guarded by `if action == ""`, so the answer
		// says "Enable parallel_fan_out" — an overwrite would silently
		// swap the recommendation an operator acts on.
		{name: "an outlier among independent steps", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 1, 0, 900),
				prof(2, "done", "beta", 1, 0, 900),
				prof(3, "done", "gamma", 1, 0, 900),
				prof(4, "done", "delta", 900000, 0, 900),
			}},
		// "from" is the only filler no other fixture exercises. Each pair
		// shares "widget" AND "from"; with "from" dropped the overlap is
		// one word and the pairs read independent, without it the overlap
		// is two and none of them do.
		{name: "pairs sharing a word and the filler from", lens: "architecture",
			profiles: []pyProfile{
				prof(1, "done", "alpha from widget", 100, 10, 900),
				prof(2, "done", "beta from widget", 100, 10, 900),
				prof(3, "done", "gamma from widget", 100, 10, 900),
			}},
		// The word set LOWERCASES before comparing. These pairs share two
		// words only if case is folded; compared as written they share
		// none, which flips every pair from dependent to independent.
		{name: "pairs sharing two words in different cases",
			lens: "architecture", profiles: []pyProfile{
				prof(1, "done", "Widget Parser alpha", 100, 10, 900),
				prof(2, "done", "widget parser beta", 100, 10, 900),
				prof(3, "done", "widget PARSER gamma", 100, 10, 900),
			}},
		// And it splits with Python's str.split(). Glued by \x1c the two
		// shared words are one token to Go's Fields and two to Python's
		// split, which is the difference between overlap 1 and overlap 2.
		{name: "pairs sharing two words behind a file separator",
			lens: "architecture", profiles: []pyProfile{
				prof(1, "done", "widget\x1cparser alpha", 100, 10, 900),
				prof(2, "done", "widget\x1cparser beta", 100, 10, 900),
				prof(3, "done", "widget\x1cparser gamma", 100, 10, 900),
			}},

		// --- operator ---------------------------------------------------
		{name: "operator with no profiles", lens: "operator"},
		{name: "operator with no elapsed time at all", lens: "operator",
			profiles: []pyProfile{
				{StepIdx: 1, Text: "a", Status: "done"},
				{StepIdx: 2, Text: "b", Status: "done"},
			}},
		// Waste over 30%.
		{name: "a third of the time blocked", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 6000),
				prof(2, "blocked", "beta", 100, 10, 4000),
			}},
		// Exactly 30% must NOT trip it: the threshold is `> 30`.
		{name: "exactly thirty percent blocked", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 7000),
				prof(2, "blocked", "beta", 100, 10, 3000),
			}},
		// The seconds in the waste line are FLOOR-divided.
		{name: "waste seconds that floor down", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 1999),
				prof(2, "blocked", "beta", 100, 10, 5999),
			}},
		// Slow steps: strictly over 120000ms.
		{name: "a step exactly at two minutes", lens: "operator",
			profiles: []pyProfile{prof(1, "done", "alpha", 100, 10, 120000)}},
		{name: "a step over two minutes", lens: "operator",
			profiles: []pyProfile{prof(1, "done", "alpha", 100, 10, 120001)}},
		// The slow-step line clips its text at 60 RUNES.
		{name: "a slow step with a long multibyte text", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done",
					"漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字"+
						"漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字漢字",
					100, 10, 130000),
			}},
		// The progress-rate line, gated on total > 60000ms, renders at one
		// decimal — so a rate with a half in it is the discriminating case.
		{name: "a progress rate that rounds half to even", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 40000),
				prof(2, "done", "beta", 100, 10, 40000),
				prof(3, "blocked", "gamma", 100, 10, 80000),
			}},
		{name: "exactly one minute of wall clock", lens: "operator",
			profiles: []pyProfile{prof(1, "done", "alpha", 100, 10, 60000)}},
		// A slow step also supplies the action, but only when the waste
		// line did not — the `if action is None` arm.
		{name: "both a waste line and a slow step", lens: "operator",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 130000),
				prof(2, "blocked", "beta", 100, 10, 130000),
			}},

		// --- forensics --------------------------------------------------
		{name: "forensics with no profiles", lens: "forensics"},
		{name: "forensics with no blocked step", lens: "forensics",
			profiles: threeDone},
		// LAST done and FIRST block, which a single scan gets wrong.
		{name: "two done steps around a block", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 900),
				prof(2, "blocked", "beta", 100, 10, 900),
				prof(3, "done", "gamma", 100, 10, 900),
				prof(4, "blocked", "delta", 100, 10, 900),
			}},
		// first_block_idx > 0 is strict, so a loop that blocks on step one
		// reports no token delta.
		{name: "the first step is the block", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "blocked", "alpha", 0, 0, 900),
				prof(2, "done", "beta", 100, 10, 900),
			}},
		{name: "a zero-token failure after a good step", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 900),
				{StepIdx: 2, Text: "beta", Status: "blocked", ElapsedMS: 900},
			}},
		// The zero-token arm is checked FIRST, and the two arms can both
		// be true at once: a preceding step with NEGATIVE tokens makes
		// `fail_tokens > pre_tokens` true while `fail_tokens == 0` is also
		// true. Order decides which line is rendered and whether the
		// action is set at all. With non-negative tokens the arms are
		// mutually exclusive and the order is invisible.
		{name: "a zero-token failure after a negative-token step",
			lens: "forensics", profiles: []pyProfile{
				{StepIdx: 1, Text: "alpha", Status: "done", Tokens: -100,
					TokensIn: -100, Model: "grok-4.5", ElapsedMS: 900},
				{StepIdx: 2, Text: "beta", Status: "blocked", ElapsedMS: 900},
			}},
		{name: "a token jump at the failure", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "done", "alpha", 1000, 0, 900),
				prof(2, "blocked", "beta", 500000, 0, 900),
			}},
		// The jump ratio renders at one decimal and divides by
		// max(pre, 1) — so a pre-total of zero is its own case.
		{name: "a token jump from zero", lens: "forensics",
			profiles: []pyProfile{
				{StepIdx: 1, Text: "alpha", Status: "done", ElapsedMS: 900},
				prof(2, "blocked", "beta", 3, 0, 900),
			}},
		// Shared keywords across blocked steps: two or more AFTER the
		// stopwords come off, sorted, capped at five.
		{name: "blocked steps sharing two keywords", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "blocked", "parse the widget index", 100, 10, 900),
				prof(2, "blocked", "render the widget index", 100, 10, 900),
			}},
		{name: "blocked steps sharing only one keyword", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "blocked", "parse the widget", 100, 10, 900),
				prof(2, "blocked", "render the widget", 100, 10, 900),
			}},
		// "step" is in the forensics stopword list and in no other set.
		{name: "blocked steps sharing the word step", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "blocked", "step widget alpha", 100, 10, 900),
				prof(2, "blocked", "step widget beta", 100, 10, 900),
			}},
		// More than five shared keywords: sorted, then the first five.
		{name: "blocked steps sharing seven keywords", lens: "forensics",
			profiles: []pyProfile{
				prof(1, "blocked", "zeta epsilon delta gamma beta alpha omega x",
					100, 10, 900),
				prof(2, "blocked", "omega alpha beta gamma delta epsilon zeta y",
					100, 10, 900),
			}},

		// --- execution --------------------------------------------------
		{name: "execution on a healthy loop", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", Recommend: "keep going"}},
		{name: "execution on a failed loop", lens: "execution",
			diag: pyDiag{FailureClass: "token_explosion",
				Evidence:  []string{"first", "second"},
				Recommend: "narrow the step"}},
		// A non-healthy diagnosis with an EMPTY recommendation: the action
		// is falsy, so the confidence drops to 0.3 even though the class is
		// a failure. This is the only case that separates "" from None.
		{name: "a failed loop with no recommendation", lens: "execution",
			diag: pyDiag{FailureClass: "token_explosion",
				Evidence: []string{"only evidence"}}},
		// The block-rate line: `> 0.3`, rendered as a percentage that
		// multiplies BEFORE rounding.
		{name: "a block rate over thirty percent", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", StepsDone: 6,
				StepsBlocked: 4, StepsTotal: 10}},
		{name: "a block rate at exactly thirty percent", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", StepsDone: 7,
				StepsBlocked: 3, StepsTotal: 10}},
		// An exact half-percent after the multiply, rounding to even.
		{name: "a block rate that is an exact half percent", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", StepsDone: 5,
				StepsBlocked: 3, StepsTotal: 8}},
		// steps_total is what is PRINTED and done+blocked is what is
		// COMPUTED, so a total that disagrees is a real rendered line.
		{name: "a steps_total that disagrees with the ratio", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", StepsDone: 1,
				StepsBlocked: 1, StepsTotal: 10}},
		{name: "no done steps at all", lens: "execution",
			diag: pyDiag{FailureClass: "healthy", StepsBlocked: 4,
				StepsTotal: 4}},
		// `{:.0%}` multiplies by 100 and THEN rounds. 39/40 is 0.975, and
		// the nearest double to it is a hair below — so the format prints
		// "98%" while rounding the ratio to two places first gives 0.97
		// and "97%". Every smaller fixture here (3/8 is the closest) gives
		// the same answer both ways, which is why this one is written out
		// as forty steps rather than eight.
		{name: "a block rate that rounds differently before multiplying",
			lens: "execution", diag: pyDiag{FailureClass: "healthy",
				StepsDone: 1, StepsBlocked: 39, StepsTotal: 40}},
	}
}

func TestLensesMatchCPython(t *testing.T) {
	cases := lensCases()

	type payloadCase struct {
		Lens     string      `json:"lens"`
		Diag     pyDiag      `json:"diag"`
		Profiles []pyProfile `json:"profiles"`
	}
	payload := make([]payloadCase, 0, len(cases))
	for _, c := range cases {
		ps := c.profiles
		if ps == nil {
			ps = []pyProfile{}
		}
		payload = append(payload, payloadCase{c.lens, c.diag, ps})
	}

	var want []pyLensAnswer
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyLensSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	registry := DefaultLensRegistry()
	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, ok := registry.Run(c.lens, c.goDiag(), c.goProfiles())
			if !ok {
				t.Fatalf("lens %q not registered", c.lens)
			}
			w := want[i]
			if got.LensName != w.LensName {
				t.Errorf("lens_name: cpython %q, go %q", w.LensName, got.LensName)
			}
			if got.Cost != w.Cost {
				t.Errorf("cost: cpython %q, go %q", w.Cost, got.Cost)
			}
			if got.Confidence != w.Confidence {
				t.Errorf("confidence: cpython %v, go %v", w.Confidence, got.Confidence)
			}
			wantAction := ""
			if w.Action != nil {
				wantAction = *w.Action
			}
			if got.Action != wantAction {
				t.Errorf("action: cpython %q, go %q", wantAction, got.Action)
			}
			if len(got.Findings) != len(w.Findings) {
				t.Fatalf("findings: cpython %d %q, go %d %q",
					len(w.Findings), w.Findings, len(got.Findings), got.Findings)
			}
			for j := range w.Findings {
				if got.Findings[j] != w.Findings[j] {
					t.Errorf("finding %d\ncpython %q\n     go %q",
						j, w.Findings[j], got.Findings[j])
				}
			}
		})
	}
}

// TestRunLensesMatchesCPython measures the sweep rather than one lens: the
// has-findings filter, and the ORDER the surviving lenses come back in,
// which is registration order and not name order.
func TestRunLensesMatchesCPython(t *testing.T) {
	cases := []struct {
		name     string
		diag     pyDiag
		profiles []pyProfile
	}{
		{name: "nothing at all"},
		// A shape that makes several lenses speak at once, so the order is
		// observable. Registration order is execution, cost, architecture,
		// operator, forensics — NOT the alphabetical order List() returns.
		{name: "a loop several lenses have something to say about",
			diag: pyDiag{FailureClass: "token_explosion",
				Evidence:  []string{"the widget parser exploded"},
				Recommend: "narrow the step", StepsDone: 2, StepsBlocked: 2,
				StepsTotal: 4},
			profiles: []pyProfile{
				prof(1, "done", "fetch the widget index", 1000, 100, 130000),
				prof(2, "done", "verify lexer output shape", 900000, 100, 900),
				prof(3, "blocked", "render report tables", 100, 10, 900),
				prof(4, "blocked", "render report totals", 100, 10, 900),
			}},
		// Only the execution lens has findings, so the filter must drop
		// the other four rather than return empty results.
		{name: "only the execution lens speaks",
			diag: pyDiag{FailureClass: "setup_failure",
				Evidence: []string{"no healthy profile"}, Recommend: "fix creds"}},
		// A healthy loop with profiles: the execution lens is silent and
		// the others may not be.
		{name: "a healthy loop with slow steps",
			diag: pyDiag{FailureClass: "healthy"},
			profiles: []pyProfile{
				prof(1, "done", "alpha", 100, 10, 130000),
				prof(2, "done", "beta", 100, 10, 130000),
				prof(3, "done", "gamma", 100, 10, 130000),
			}},
	}

	type payloadCase struct {
		Diag     pyDiag      `json:"diag"`
		Profiles []pyProfile `json:"profiles"`
	}
	payload := make([]payloadCase, 0, len(cases))
	for _, c := range cases {
		ps := c.profiles
		if ps == nil {
			ps = []pyProfile{}
		}
		payload = append(payload, payloadCase{c.diag, ps})
	}

	var want [][]pyLensAnswer
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyRunLensesSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			lc := lensCase{diag: c.diag, profiles: c.profiles}
			got := RunLenses(lc.goDiag(), lc.goProfiles())
			w := want[i]
			if len(got) != len(w) {
				gn := make([]string, len(got))
				for j, r := range got {
					gn[j] = r.LensName
				}
				wn := make([]string, len(w))
				for j, r := range w {
					wn[j] = r.LensName
				}
				t.Fatalf("active lenses: cpython %q, go %q", wn, gn)
			}
			for j := range w {
				if got[j].LensName != w[j].LensName {
					t.Errorf("lens %d: cpython %q, go %q",
						j, w[j].LensName, got[j].LensName)
				}
				if len(got[j].Findings) != len(w[j].Findings) {
					t.Fatalf("lens %s findings: cpython %q, go %q",
						w[j].LensName, w[j].Findings, got[j].Findings)
				}
				for k := range w[j].Findings {
					if got[j].Findings[k] != w[j].Findings[k] {
						t.Errorf("lens %s finding %d\ncpython %q\n     go %q",
							w[j].LensName, k, w[j].Findings[k], got[j].Findings[k])
					}
				}
			}
		})
	}
}

// TestRegistryOrderIsRegistrationNotName pins the two orderings apart. They
// differ for the built-in set — registration is execution, cost,
// architecture, operator, forensics — and a Go map would have made both of
// them random.
func TestRegistryOrderIsRegistrationNotName(t *testing.T) {
	r := DefaultLensRegistry()
	wantList := []string{"architecture", "cost", "execution", "forensics", "operator"}
	got := r.List()
	if len(got) != len(wantList) {
		t.Fatalf("List: %q", got)
	}
	for i := range wantList {
		if got[i] != wantList[i] {
			t.Fatalf("List is not sorted: %q", got)
		}
	}

	// Every built-in lens returns SOMETHING for this diagnosis, so the run
	// order is fully observable.
	diag := LoopDiagnosis{FailureClass: "token_explosion",
		Evidence: []string{"e"}, Recommendation: "r"}
	profiles := []StepProfile{
		{StepIdx: 1, Text: "alpha", Status: "done", Tokens: 100, ElapsedMS: 130000},
		{StepIdx: 2, Text: "beta", Status: "blocked", Tokens: 100, ElapsedMS: 130000},
		{StepIdx: 3, Text: "gamma", Status: "done", Tokens: 100, ElapsedMS: 130000},
	}
	wantRun := []string{"execution", "cost", "architecture", "operator", "forensics"}
	res := r.RunHeuristic(diag, profiles)
	if len(res) != len(wantRun) {
		t.Fatalf("RunHeuristic returned %d results", len(res))
	}
	for i := range wantRun {
		if res[i].LensName != wantRun[i] {
			t.Fatalf("run order %d: want %q, got %q", i, wantRun[i], res[i].LensName)
		}
	}
}

// TestReRegisterKeepsPosition pins the dict-assignment semantics the order
// slice has to reproduce: replacing a lens keeps its ORIGINAL position, so
// the run order and every tie-break downstream are unchanged.
func TestReRegisterKeepsPosition(t *testing.T) {
	r := NewLensRegistry()
	mark := func(name string) LensFn {
		return func(LoopDiagnosis, []StepProfile) LensResult {
			return LensResult{LensName: name, Findings: []string{name}}
		}
	}
	r.Register("a", mark("a"), "free")
	r.Register("b", mark("b"), "free")
	r.Register("c", mark("c"), "free")
	r.Register("a", mark("a2"), "free")

	res := r.RunHeuristic(LoopDiagnosis{}, nil)
	want := []string{"a2", "b", "c"}
	if len(res) != len(want) {
		t.Fatalf("got %d results", len(res))
	}
	for i := range want {
		if res[i].LensName != want[i] {
			t.Fatalf("position %d: want %q, got %q", i, want[i], res[i].LensName)
		}
	}

	// A re-registration that also changes the COST must take effect, or a
	// lens could be silently promoted out of the free sweep.
	r.Register("b", mark("b"), "cheap")
	res = r.RunHeuristic(LoopDiagnosis{}, nil)
	if len(res) != 2 || res[0].LensName != "a2" || res[1].LensName != "c" {
		t.Fatalf("after cost change: %v", res)
	}
}
