// Package provenance ports src/lesson_provenance.py — the Tier-0
// deterministic gate that stamps prompt-derived lessons at storage choke
// points.
//
// Origin incident (db37d525): a dispatch prompt's anti-escalation
// scaffolding was generalized INTO a lesson and re-injected into an
// unrelated run — instruction text rewrote persistent state. The pack
// importer re-applies this gate to every arriving lesson: without it,
// export→import would launder a quarantined lesson into an injectable one
// (the same contamination class, via transport). Bias runs toward
// quarantine — a false positive sits visible but uninjected and decays; a
// false negative contaminates future runs.
package provenance

import "regexp"

const (
	MintedFromPrompt  = "prompt"
	MintedFromOutcome = "outcome"
)

// Regexes ported one-for-one from lesson_provenance.py; that file's pin
// tests (tests/test_lesson_provenance.py) against the four real incident
// lessons are the shared contract.
var promptAuthorityRe = regexp.MustCompile(
	`(?i)\b(?:the\s+|a\s+|your\s+)?(?:prompt|instructions?|directives?)\s+` +
		`(?:explicitly\s+|clearly\s+)?` +
		`(?:says?|said|states?|stated|instructs?|tells?|told|demands?|` +
		`forbids?|prohibits?)\b`)

var obedienceRe = regexp.MustCompile(
	`(?i)\btreat\s+(?:that|this|it|them)\s+as\s+(?:a\s+)?hard\s+constraints?\b` +
		`|\bmust\s+be\s+obeyed\b` +
		`|\bas\s+non-negotiable\b` +
		`|\bfollow\s+(?:it|them)\s+(?:exactly|to\s+the\s+letter)\b`)

var scaffoldingRe = regexp.MustCompile(
	`(?i)\bdo\s+not\s+(?:escalate|stop|abandon|refuse|give\s+up)\b` +
		`|\bcannot\s+use\b[^.]{0,60}\bas\s+an\s+excuse\b` +
		`|\bas\s+an\s+excuse\s+to\s+(?:stop|escalate)\b`)

// Classify returns MintedFromPrompt when the lesson generalizes
// instruction text (prompt-authority phrasing, obedience generalization,
// or an anti-escalation scaffolding echo the goal or evidence text also
// carries); MintedFromOutcome otherwise. Deterministic, no I/O.
func Classify(lessonText, goalText, evidenceText string) string {
	if promptAuthorityRe.MatchString(lessonText) {
		return MintedFromPrompt
	}
	if obedienceRe.MatchString(lessonText) {
		return MintedFromPrompt
	}
	if scaffoldingRe.MatchString(lessonText) {
		for _, source := range []string{goalText, evidenceText} {
			if source != "" && scaffoldingRe.MatchString(source) {
				return MintedFromPrompt
			}
		}
	}
	return MintedFromOutcome
}
