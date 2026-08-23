package record

import (
	"regexp"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// urlRE is Python's `re.compile(r"https?://\S+", re.IGNORECASE)`.
//
// `\S` is the complement of `\s`, and Python's `\s` on a str is the
// UNICODE whitespace set — so a non-breaking space or an ideographic space
// ENDS a URL match in Python, where Go's ASCII-only `\S` would swallow it
// and keep counting one URL where Python counted one that stops earlier.
// The count is what the classifier thresholds on, so the set has to match.
// pytext.IsSpace already carries Python's exact set, pinned against it;
// this spells the same set as a character class because the URL scan is
// hot enough that a per-rune callback would be the wrong shape.
var urlRE = regexp.MustCompile(`(?i)https?://[^\t\n\v\f\r \x1c-\x1f\x85\x{00a0}\x{1680}\x{2000}-\x{200a}\x{2028}\x{2029}\x{202f}\x{205f}\x{3000}]+`)

// Python iterates these as a frozenset, but membership is all that is
// asked of them and the hit COUNT is order-independent, so a slice is
// faithful and deterministic.
var codeIndicators = []string{"def ", "class ", "import ", "function ", "return ", "```"}

var structuredIndicators = []string{"{", "}", `":`, "]: "}

// ClassifyInputType returns the input domain of a goal or step text: one of
// "url", "code", "structured_data", or "plain_text".
//
// It is the second half of an announcement this port had ported only the
// first half of. Python's update_skill_utility, on the transition where a
// circuit OPENS, checks whether the step text contradicts the skill's own
// trigger vocabulary and — when it does — logs INPUT_MISMATCH alongside
// the trip, saying "treat this as INPUT_MISMATCH, not skill degradation".
// Go opened the circuit and said nothing further (adversarial r4, M2), so
// an operator reading a Go-driven store sees the trip with no qualifier
// and attributes structural failure to a skill that was fed the wrong
// kind of input.
func ClassifyInputType(text string) string {
	if text == "" {
		return "plain_text"
	}
	// A single URL only classifies as "url" in SHORT text, where it is
	// plausibly the whole subject; len() is Python's, so CODE POINTS.
	if n := len(urlRE.FindAllString(text, -1)); n >= 2 ||
		(n == 1 && len([]rune(text)) < 200) {
		return "url"
	}
	lower := pytext.Lower(text)
	hits := 0
	for _, kw := range codeIndicators {
		// Python lowercases the KEYWORD too. Every one is already
		// lowercase, so this matches; lowering both is what the source
		// does and what a future keyword with a capital would need.
		if strings.Contains(lower, pytext.Lower(kw)) {
			hits++
		}
	}
	if hits >= 2 {
		return "code"
	}
	// Note the asymmetry, carried verbatim: the structured indicators are
	// matched against the ORIGINAL text, not the lowercased copy.
	hits = 0
	for _, kw := range structuredIndicators {
		if strings.Contains(text, kw) {
			hits++
		}
	}
	if hits >= 3 {
		return "structured_data"
	}
	return "plain_text"
}
