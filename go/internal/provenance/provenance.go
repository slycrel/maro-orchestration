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

import (
	"regexp"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

const (
	MintedFromPrompt  = "prompt"
	MintedFromOutcome = "outcome"
)

// pySpace rewrites Python's `\s` into the class Go actually needs to match
// it, so the patterns below can stay BYTE-IDENTICAL to the ones in
// lesson_provenance.py — which is the point of porting them one-for-one,
// and is lost the moment a human hand-expands the class differently in
// each of three places.
//
// Python's `\s` matches 29 code points; Go's matches 5. In this package
// that gap ran the UNSAFE way, because this is the quarantine gate — the
// thing that stops a dispatched instruction being laundered into an
// injectable lesson (the db37d525 class named in the package doc). Measured
// 2026-08-27, both engines, same input:
//
//	Classify("do not escalate", "do\u00a0not stop", "")
//	  CPython  "prompt"    (quarantined)
//	  Go       "outcome"   (injectable)
//
// One NO-BREAK SPACE in the untrusted dispatch prompt. `must\vbe obeyed`
// does it with a VERTICAL TAB and no Unicode at all, and the same input
// with an ordinary space classifies "prompt" on both — which is what makes
// it a real hole rather than a broken fixture.
//
// pytext.SpaceClass has been in this tree the whole time (reclass.go:61),
// used by guard, jsonx, scrub, intent, closure and playbook. This package
// transcribed `\s` instead of looking for it (L14), and a helper only fixes
// the callers that reach it (L15).
//
// NOT fixed here, and filed: the `\b`/`\w` half of the same gap (Python
// counts 142,940 word runes to Go's 63) runs FAIL-CLOSED — the Go answers
// "prompt" where CPython answers "outcome", so it quarantines MORE — and
// two of scaffoldingRe's `\b` are INTERIOR to a `[^.]{0,60}` window, where
// pytext.WordStart/WordEnd cannot be substituted without redoing the window
// arithmetic (reclass.go:227-271). Also filed: re.IGNORECASE folds the
// Turkish dotless i, which is a divergence of the whole port's `(?i)` usage
// and not of this package.
func pySpace(pattern string) string {
	return strings.ReplaceAll(pattern, `\s`, pytext.SpaceClass)
}

// Regexes ported one-for-one from lesson_provenance.py; that file's pin
// tests (tests/test_lesson_provenance.py) against the four real incident
// lessons are the shared contract.
var promptAuthorityRe = regexp.MustCompile(pySpace(
	`(?i)\b(?:the\s+|a\s+|your\s+)?(?:prompt|instructions?|directives?)\s+` +
		`(?:explicitly\s+|clearly\s+)?` +
		`(?:says?|said|states?|stated|instructs?|tells?|told|demands?|` +
		`forbids?|prohibits?)\b`))

var obedienceRe = regexp.MustCompile(pySpace(
	`(?i)\btreat\s+(?:that|this|it|them)\s+as\s+(?:a\s+)?hard\s+constraints?\b` +
		`|\bmust\s+be\s+obeyed\b` +
		`|\bas\s+non-negotiable\b` +
		`|\bfollow\s+(?:it|them)\s+(?:exactly|to\s+the\s+letter)\b`))

var scaffoldingRe = regexp.MustCompile(pySpace(
	`(?i)\bdo\s+not\s+(?:escalate|stop|abandon|refuse|give\s+up)\b` +
		`|\bcannot\s+use\b[^.]{0,60}\bas\s+an\s+excuse\b` +
		`|\bas\s+an\s+excuse\s+to\s+(?:stop|escalate)\b`))

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

// GateEnabled ports lesson_provenance.provenance_gate_enabled's
// normalization: the killswitch value arrives as a raw YAML node, so a
// quoted "false" is a truthy string unless normalized. bool and string
// are meaningful; anything else (including absent → the caller's true
// default) is Python's bool(val) — non-nil non-zero is on. Default ON.
func GateEnabled(val any) bool {
	switch t := val.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "false", "0", "no", "off":
			return false
		}
		return true
	case nil:
		return false // Python bool(None)
	case int:
		return t != 0
	case int64:
		return t != 0
	case uint64:
		return t != 0
	case float64:
		return t != 0
	default:
		return true
	}
}
