package introspect

import (
	"strconv"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// AggregatedDiagnosis is introspect.AggregatedDiagnosis: what several
// lenses, taken together, say to do next.
type AggregatedDiagnosis struct {
	LoopID             string
	FailureClass       string
	Severity           string
	Confidence         float64
	PrimaryAction      string
	SupportingEvidence []string
	LensAgreement      int
	AllActions         []string
}

// Summary is AggregatedDiagnosis.summary().
func (a AggregatedDiagnosis) Summary() string {
	return "[" + a.FailureClass + "] confidence=" +
		pyval.PercentF(a.Confidence, 2) + " agreement=" +
		strconv.Itoa(a.LensAgreement) + " lenses | " + a.PrimaryAction
}

// actionKeyStopwords is the FOURTH word set in this module, and the
// smallest. It is not `archFiller` minus entries — it contains "into",
// which no other set has, and lacks "and", "or", "on", "with" and "from",
// which archFiller does. See costCheapKeywords for why these stay apart.
var actionKeyStopwords = map[string]bool{
	"the": true, "a": true, "an": true, "to": true, "into": true, "for": true,
}

// actionKey is Python's `_action_key`: an action's category is its first
// meaningful word, so "Split step 3 ..." and "Split the loop ..." are the
// same category and vote together.
//
// An action made ENTIRELY of stopwords keys to the empty string, which is a
// real category that other empty-keyed actions join. Not a guard to add:
// the empty key is how Python groups them and two of them genuinely do
// agree with each other.
func actionKey(a string) string {
	for _, w := range pytext.Split(pytext.Lower(a)) {
		if !actionKeyStopwords[w] {
			return w
		}
	}
	return ""
}

// scoredAction is Python's `(action, conf, lens)` tuple. It carries `lens`
// even though nothing reads it, because Python's tuple does and the field
// is what makes the "which lens voted" question answerable without
// rebuilding the list — the same reason the step pattern keeps its groups.
type scoredAction struct {
	action     string
	confidence float64
	lens       string
}

// maxConfidence is the inner `max(c for _, c, _ in v)` of the category key.
// A category is never empty: it exists because something was appended to it.
func maxConfidence(v []scoredAction) float64 {
	best := v[0].confidence
	for _, sa := range v[1:] {
		if sa.confidence > best {
			best = sa.confidence
		}
	}
	return best
}

// AggregateLenses is introspect.aggregate_lenses: synthesise several lens
// results into one diagnosis and one action.
//
// Confidence is BOOSTED by agreement — the category with the most lenses
// behind it wins, and each extra lens adds 0.1 up to a ceiling of 1.0.
//
// Four orderings decide the answer and every one of them is insertion
// order in Python:
//
//  1. `all_evidence` starts as a copy of the diagnosis's own evidence and
//     then extends with each lens's findings IN LENS ORDER.
//  2. `all_actions` is appended in lens order, and is returned whole.
//  3. `categories` is a dict keyed by first-meaningful-word, so its values
//     come back in the order each category was FIRST seen.
//  4. `max()` returns the FIRST maximum, so a tie on (count, best
//     confidence) goes to the category seen first — that is, to the lens
//     registered earliest.
//
// A Go map would randomise 3 and 4, and the result is what an operator is
// told to do.
func AggregateLenses(diag LoopDiagnosis, results []LensResult) AggregatedDiagnosis {
	allEvidence := append([]string(nil), diag.Evidence...)
	var allActions []scoredAction
	for _, lr := range results {
		allEvidence = append(allEvidence, lr.Findings...)
		// `if lr.action:` — truthiness, so an empty action is no action.
		if lr.Action != "" {
			allActions = append(allActions, scoredAction{lr.Action, lr.Confidence, lr.LensName})
		}
	}

	if len(allActions) == 0 {
		primary := diag.Recommendation
		if primary == "" {
			primary = "No action recommended"
		}
		// NOTE THE ASYMMETRY, which is Python's: this branch returns
		// `all_evidence` UNCAPPED while the branch below truncates at 20.
		// A loop with no actionable lens can therefore return a longer
		// evidence list than one with several. Faithful, and named because
		// it reads like an oversight — moving the cap up here would change
		// what a caller renders today.
		return AggregatedDiagnosis{
			LoopID:             diag.LoopID,
			FailureClass:       diag.FailureClass,
			Severity:           diag.Severity,
			Confidence:         0.3,
			PrimaryAction:      primary,
			SupportingEvidence: allEvidence,
			LensAgreement:      0,
			AllActions:         nil,
		}
	}

	var catOrder []string
	cats := map[string][]scoredAction{}
	for _, sa := range allActions {
		k := actionKey(sa.action)
		if _, seen := cats[k]; !seen {
			catOrder = append(catOrder, k)
		}
		cats[k] = append(cats[k], sa)
	}

	// `max(categories.values(), key=lambda v: (len(v), max(c for _, c, _ in v)))`
	// — a TUPLE key, so count dominates and best-confidence breaks the tie,
	// and the FIRST maximum wins any remaining tie.
	best := cats[catOrder[0]]
	bestCount, bestConf := len(best), maxConfidence(best)
	for _, k := range catOrder[1:] {
		v := cats[k]
		n, c := len(v), maxConfidence(v)
		if n > bestCount || (n == bestCount && c > bestConf) {
			best, bestCount, bestConf = v, n, c
		}
	}

	// `max(best_cat, key=lambda x: x[1])[0]` — again the first maximum, so
	// two actions in the winning category with equal confidence resolve to
	// the one whose lens ran first.
	primary := best[0].action
	topConf := best[0].confidence
	for _, sa := range best[1:] {
		if sa.confidence > topConf {
			primary, topConf = sa.action, sa.confidence
		}
	}

	sum := 0.0
	for _, sa := range best {
		sum += sa.confidence
	}
	avgConf := sum / float64(len(best))
	boosted := avgConf + float64(len(best)-1)*0.1
	// `min(1.0, x)`, spelled as a NEGATED less-than rather than a
	// greater-than. Python's min returns its first argument unless the
	// second compares strictly less, so `min(1.0, nan)` is 1.0 — every
	// comparison against NaN is False. `if boosted > 1.0` would let a NaN
	// through unchanged. Not reachable from the built-in lenses, whose
	// confidences are five finite literals; spelled correctly because the
	// cheap spelling and the right one are the same length.
	if !(boosted < 1.0) {
		boosted = 1.0
	}

	if len(allEvidence) > 20 {
		allEvidence = allEvidence[:20]
	}
	actions := make([]string, 0, len(allActions))
	for _, sa := range allActions {
		actions = append(actions, sa.action)
	}

	return AggregatedDiagnosis{
		LoopID:       diag.LoopID,
		FailureClass: diag.FailureClass,
		Severity:     diag.Severity,
		// `round(x, 2)` is HALF-TO-EVEN on the exact double, not
		// half-away-from-zero: round(0.125, 2) is 0.12 and round(0.375, 2)
		// is 0.38. pyval.Round is that; math.Round is not.
		Confidence:         pyval.Round(boosted, 2),
		PrimaryAction:      primary,
		SupportingEvidence: allEvidence,
		LensAgreement:      len(best),
		AllActions:         actions,
	}
}
