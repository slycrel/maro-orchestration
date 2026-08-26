package introspect

import (
	"strconv"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyAggregateSrc builds LensResult objects directly rather than running
// lenses, because the rules under test are about ORDER, TIES and the
// evidence cap — and the five built-in lenses between them can produce
// only five confidences and never a tie the fixtures could control.
//
// `action` arrives as JSON null for Python's None. The port carries "" as
// None (see LensResult), so the two cases the probe can express are the
// two the port can distinguish.
const pyAggregateSrc = `
import json, sys
import introspect

out = []
for c in json.loads(sys.argv[1]):
    d = introspect.LoopDiagnosis(
        loop_id=c["diag"].get("loop_id", "L"),
        failure_class=c["diag"].get("failure_class", "healthy"),
        severity=c["diag"].get("severity", "info"),
        evidence=c["diag"].get("evidence", []),
        recommendation=c["diag"].get("recommendation", ""))
    results = [introspect.LensResult(
        lens_name=r.get("lens_name", ""), findings=r.get("findings", []),
        action=r.get("action"), confidence=r.get("confidence", 0.0),
        cost=r.get("cost", "free")) for r in c["results"]]
    a = introspect.aggregate_lenses(d, results)
    out.append({"loop_id": a.loop_id, "failure_class": a.failure_class,
                "severity": a.severity, "confidence": a.confidence,
                "primary_action": a.primary_action,
                "supporting_evidence": list(a.supporting_evidence),
                "lens_agreement": a.lens_agreement,
                "all_actions": list(a.all_actions),
                "summary": a.summary()})
print(json.dumps(out))
`

type pyLensInput struct {
	LensName   string   `json:"lens_name"`
	Findings   []string `json:"findings"`
	Action     *string  `json:"action"`
	Confidence float64  `json:"confidence"`
}

type pyAggAnswer struct {
	LoopID       string   `json:"loop_id"`
	FailureClass string   `json:"failure_class"`
	Severity     string   `json:"severity"`
	Confidence   float64  `json:"confidence"`
	PrimaryAct   string   `json:"primary_action"`
	Evidence     []string `json:"supporting_evidence"`
	Agreement    int      `json:"lens_agreement"`
	AllActions   []string `json:"all_actions"`
	Summary      string   `json:"summary"`
}

// res is the shorthand for a lens result with an action; noAct for one
// without. Confidence is always spelled explicitly — it is the tie-break
// half of the sort key, and a fixture that leaves it at the zero value
// cannot tell the tuple key from a count-only one.
func res(name, action string, conf float64, findings ...string) pyLensInput {
	a := action
	return pyLensInput{LensName: name, Findings: findings, Action: &a,
		Confidence: conf}
}

func noAct(name string, conf float64, findings ...string) pyLensInput {
	return pyLensInput{LensName: name, Findings: findings, Confidence: conf}
}

func aggCases() []struct {
	name    string
	diag    pyDiag
	results []pyLensInput
} {
	// Twenty-five evidence lines, so the cap at 20 and the ABSENCE of a cap
	// on the other branch are both visible. Numbered, because a cap that
	// took the wrong end would otherwise produce a list of the same length.
	long := make([]string, 25)
	for i := range long {
		long[i] = "evidence line " + strconv.Itoa(i)
	}

	return []struct {
		name    string
		diag    pyDiag
		results []pyLensInput
	}{
		// --- the no-action branch ---------------------------------------
		{name: "no lens results at all"},
		{name: "no lens results but a recommendation",
			diag: pyDiag{Recommend: "do the thing"}},
		{name: "results that carry no action",
			diag: pyDiag{Recommend: "do the thing", Evidence: []string{"e0"}},
			results: []pyLensInput{
				noAct("execution", 0.3, "f0", "f1"),
				noAct("cost", 0.8, "f2"),
			}},
		// An action of None is skipped. This is the only case that
		// separates "no lens spoke" from "a lens spoke with no action",
		// and the two branches differ in confidence, agreement and cap.
		{name: "an explicit none action", diag: pyDiag{Recommend: "fallback"},
			results: []pyLensInput{{LensName: "cost", Confidence: 0.9,
				Findings: []string{"f0"}}}},
		// THE ASYMMETRY. The no-action branch returns evidence UNCAPPED
		// while the branch below truncates at 20. Same 25 lines, two
		// different answers — which is the whole point of the fixture.
		{name: "long evidence with no action", diag: pyDiag{Evidence: long},
			results: []pyLensInput{noAct("cost", 0.5)}},
		{name: "long evidence with an action", diag: pyDiag{Evidence: long},
			results: []pyLensInput{res("cost", "Split the loop", 0.5)}},
		// Evidence is diag's first, then each lens's findings IN LENS
		// ORDER. Every string is distinct so a concatenation in the other
		// order, or a lens's findings dropped, changes the list.
		{name: "evidence order across three lenses",
			diag: pyDiag{Evidence: []string{"d0", "d1"}},
			results: []pyLensInput{
				res("execution", "Split alpha", 0.5, "x0", "x1"),
				noAct("cost", 0.4, "c0"),
				res("operator", "Split beta", 0.6, "o0"),
			}},

		// --- category grouping ------------------------------------------
		{name: "a single action", results: []pyLensInput{
			res("cost", "Split step 3 into substeps", 0.8)}},
		// "Split step 3" and "Split the loop" share the key `split`, so
		// they agree — the whole reason the key is the first MEANINGFUL
		// word rather than the first word.
		{name: "two actions in one category", results: []pyLensInput{
			res("cost", "Split step 3 into substeps", 0.8),
			res("architecture", "Split the loop in half", 0.6)}},
		// The stopword list is {the, a, an, to, into, for}. "To" leads
		// here and must be skipped for the keys to match.
		{name: "a leading stopword decides the key", results: []pyLensInput{
			res("cost", "To split the work, split step 3", 0.8),
			res("architecture", "Split the loop", 0.6)}},
		// The key splits with Python's str.split(), which treats \x1c as
		// whitespace where Go's strings.Fields does not. Under Python both
		// actions key to `split` and agree; under Fields the first keys to
		// the glued token "to\x1csplit" and they land in separate
		// categories — a different agreement count AND a different boosted
		// confidence. The battery cannot spell this mutant (aggregate.go
		// does not import strings, so the mutation would not build), which
		// is exactly why the fixture has to carry it.
		{name: "an action split by a file separator", results: []pyLensInput{
			res("cost", "to\x1csplit the loop", 0.5),
			res("architecture", "Split step 3", 0.5)}},
		// An action of ENTIRELY stopwords keys to "" — a real category
		// that other empty-keyed actions join. Two of them outnumber the
		// single real one, so the empty category WINS and its first member
		// is what an operator is told to do.
		{name: "two actions made only of stopwords", results: []pyLensInput{
			res("cost", "the a an", 0.4),
			res("architecture", "to into for", 0.4),
			res("operator", "Split the loop", 0.9)}},

		// --- the (count, confidence) tuple key --------------------------
		// COUNT DOMINATES. Two weak actions beat one strong one, and the
		// confidences are far apart so a confidence-only key would pick
		// the other category.
		{name: "count beats confidence", results: []pyLensInput{
			res("cost", "Split alpha", 0.1),
			res("architecture", "Split beta", 0.1),
			res("operator", "Rebalance gamma", 0.9)}},
		// Tied on count, so the max confidence inside each category
		// decides. Without this case the second half of the tuple is
		// unmeasured.
		{name: "confidence breaks a count tie", results: []pyLensInput{
			res("cost", "Split alpha", 0.5),
			res("operator", "Rebalance beta", 0.7)}},
		// Tied on count AND on max confidence: `max` returns the FIRST
		// maximum, so the category seen first — the lens registered
		// earliest — wins. This is the case a Go map would randomise.
		{name: "a full tie goes to the first category", results: []pyLensInput{
			res("cost", "Split alpha", 0.5),
			res("operator", "Rebalance beta", 0.5)}},
		// The same tie with the two lenses SWAPPED. One of these two
		// fixtures fails for any implementation that sorts by key, by
		// confidence alone, or by anything but insertion order.
		{name: "a full tie with the categories swapped",
			results: []pyLensInput{
				res("operator", "Rebalance beta", 0.5),
				res("cost", "Split alpha", 0.5)}},
		// Category tied on count where the LATER one holds the higher max
		// but a lower first member — so a max taken over the wrong element
		// of the tuple picks differently.
		{name: "the later category holds the higher maximum",
			results: []pyLensInput{
				res("cost", "Split alpha", 0.5),
				res("cost2", "Split beta", 0.2),
				res("operator", "Rebalance gamma", 0.9),
				res("operator2", "Rebalance delta", 0.1)}},

		// --- primary action inside the winning category -----------------
		// `max(best_cat, key=lambda x: x[1])` — first maximum again, so
		// two equal confidences resolve to the earlier lens.
		{name: "a tie inside the winning category", results: []pyLensInput{
			res("cost", "Split alpha", 0.5),
			res("architecture", "Split beta", 0.5)}},
		{name: "the later action in the category is stronger",
			results: []pyLensInput{
				res("cost", "Split alpha", 0.5),
				res("architecture", "Split beta", 0.7)}},

		// --- the confidence boost ---------------------------------------
		// round(x, 2) is HALF-TO-EVEN on the exact double. 0.125 goes DOWN
		// to 0.12 and 0.375 goes UP to 0.38 — the pair that separates it
		// from half-away-from-zero. Agreement of 1 means no boost, so the
		// rounding is the only thing acting.
		{name: "a confidence that rounds half to even down",
			results: []pyLensInput{res("cost", "Split alpha", 0.125)}},
		{name: "a confidence that rounds half to even up",
			results: []pyLensInput{res("cost", "Split alpha", 0.375)}},
		// The boost is (agreement - 1) * 0.1 on the AVERAGE, not the max.
		// These two differ only in how the same total is distributed, so a
		// port that boosted the max instead would answer alike for one.
		{name: "a boost over an average of two", results: []pyLensInput{
			res("cost", "Split alpha", 0.2),
			res("architecture", "Split beta", 0.8)}},
		{name: "the same average distributed evenly", results: []pyLensInput{
			res("cost", "Split alpha", 0.5),
			res("architecture", "Split beta", 0.5)}},
		// min(1.0, ...) — three at 0.9 average to 0.9 and boost to 1.1.
		{name: "a boost that exceeds one", results: []pyLensInput{
			res("cost", "Split alpha", 0.9),
			res("architecture", "Split beta", 0.9),
			res("operator", "Split gamma", 0.9)}},
		// Exactly 1.0 after the boost: the cap must not change it, and it
		// is the only case that separates `min` from a strict clamp that
		// subtracts an epsilon.
		{name: "a boost that lands exactly on one", results: []pyLensInput{
			res("cost", "Split alpha", 0.9),
			res("architecture", "Split beta", 0.9)}},

		// --- all_actions -------------------------------------------------
		// The docstring says "every UNIQUE action recommended" and the code
		// appends every one without dedup. Two identical actions prove
		// which of the two is true.
		{name: "two identical actions", results: []pyLensInput{
			res("cost", "Split alpha", 0.5),
			res("architecture", "Split alpha", 0.5)}},
		// all_actions holds EVERY action in lens order, not just the
		// winning category's — so a losing category must appear in it.
		{name: "a losing category still reaches all actions",
			results: []pyLensInput{
				res("cost", "Split alpha", 0.5),
				res("architecture", "Split beta", 0.5),
				res("operator", "Rebalance gamma", 0.9)}},

		// --- the passthrough fields --------------------------------------
		// loop_id, failure_class and severity come from the diagnosis and
		// nothing else touches them. Distinct values, because three empty
		// strings agree with any wiring at all.
		{name: "the diagnosis fields pass through",
			diag: pyDiag{LoopID: "loop-77", FailureClass: "token_explosion",
				Severity: "critical", Recommend: "unused here"},
			results: []pyLensInput{res("cost", "Split alpha", 0.5)}},
		{name: "the diagnosis fields pass through the no-action branch",
			diag: pyDiag{LoopID: "loop-78", FailureClass: "retry_churn",
				Severity: "warning"},
			results: []pyLensInput{noAct("cost", 0.5)}},
	}
}

func TestAggregateLensesMatchesCPython(t *testing.T) {
	cases := aggCases()

	payload := make([]map[string]any, 0, len(cases))
	for _, c := range cases {
		rs := make([]map[string]any, 0, len(c.results))
		for _, r := range c.results {
			f := r.Findings
			if f == nil {
				f = []string{}
			}
			m := map[string]any{"lens_name": r.LensName,
				"findings": f, "confidence": r.Confidence}
			if r.Action != nil {
				m["action"] = *r.Action
			} else {
				m["action"] = nil
			}
			rs = append(rs, m)
		}
		// A nil slice marshals to JSON null, and Python's dataclass takes
		// the null over its default_factory — so an absent evidence list
		// reaches `list(None)` and raises. Coerced here rather than in the
		// probe, because the probe defaulting it would hide the same shape
		// arriving from a real store.
		ev := c.diag.Evidence
		if ev == nil {
			ev = []string{}
		}
		payload = append(payload, map[string]any{
			"diag": map[string]any{
				"loop_id": c.diag.LoopID, "failure_class": c.diag.FailureClass,
				"severity": c.diag.Severity, "evidence": ev,
				"recommendation": c.diag.Recommend},
			"results": rs})
	}

	var want []pyAggAnswer
	probe := pyprobe.Probe{Marker: "introspect.py"}
	probe.RunJSON(t, pyAggregateSrc, &want, pyprobe.Arg(t, payload))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	for i, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			diag := LoopDiagnosis{LoopID: c.diag.LoopID,
				FailureClass: c.diag.FailureClass, Severity: c.diag.Severity,
				Evidence: c.diag.Evidence, Recommendation: c.diag.Recommend}
			results := make([]LensResult, 0, len(c.results))
			for _, r := range c.results {
				action := ""
				if r.Action != nil {
					action = *r.Action
				}
				results = append(results, LensResult{LensName: r.LensName,
					Findings: r.Findings, Action: action,
					Confidence: r.Confidence})
			}

			got := AggregateLenses(diag, results)
			w := want[i]

			if got.LoopID != w.LoopID {
				t.Errorf("loop_id: cpython %q, go %q", w.LoopID, got.LoopID)
			}
			if got.FailureClass != w.FailureClass {
				t.Errorf("failure_class: cpython %q, go %q", w.FailureClass,
					got.FailureClass)
			}
			if got.Severity != w.Severity {
				t.Errorf("severity: cpython %q, go %q", w.Severity, got.Severity)
			}
			if got.Confidence != w.Confidence {
				t.Errorf("confidence: cpython %v, go %v", w.Confidence,
					got.Confidence)
			}
			if got.PrimaryAction != w.PrimaryAct {
				t.Errorf("primary_action\ncpython %q\n     go %q", w.PrimaryAct,
					got.PrimaryAction)
			}
			if got.LensAgreement != w.Agreement {
				t.Errorf("lens_agreement: cpython %d, go %d", w.Agreement,
					got.LensAgreement)
			}
			assertStrings(t, "supporting_evidence", w.Evidence,
				got.SupportingEvidence)
			assertStrings(t, "all_actions", w.AllActions, got.AllActions)
			// summary() renders confidence through `{:.2f}`, which is a
			// different formatter from the round above — a value that
			// rounds one way and formats another shows up only here.
			if s := got.Summary(); s != w.Summary {
				t.Errorf("summary\ncpython %q\n     go %q", w.Summary, s)
			}
		})
	}
}

// assertStrings compares two string lists element by element, and treats
// nil and empty as equal only after reporting the LENGTHS — a cap that
// took the wrong end of a list produces the right length and the wrong
// contents, which a length-only check would pass.
func assertStrings(t *testing.T, field string, want, got []string) {
	t.Helper()
	if len(want) != len(got) {
		t.Errorf("%s length: cpython %d, go %d\ncpython %q\n     go %q",
			field, len(want), len(got), want, got)
		return
	}
	for i := range want {
		if want[i] != got[i] {
			t.Errorf("%s[%d]: cpython %q, go %q", field, i, want[i], got[i])
		}
	}
}
