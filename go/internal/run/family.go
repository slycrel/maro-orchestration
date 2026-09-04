package run

import (
	"regexp"
	"strings"
)

// FamilyRule is the registered classifier version. The classifier reads the
// goal text ONLY — never the treatment, never the model's interpretation —
// so the population label cannot leak the arm (§5, §8a). Changing the rule
// is a new version; assessments carry the version they were made under.
const FamilyRule = "family/1"

const (
	FamilyAnswer     FamilyKey = "answer"      // a question answered in prose, read-only
	FamilyWriteLocal FamilyKey = "write_local" // produces or changes local files
)

var families = map[FamilyKey]bool{FamilyNone: true, FamilyAnswer: true, FamilyWriteLocal: true}

// KnownFamily reports whether f is a registered population (none is not).
func KnownFamily(f FamilyKey) bool { return f != FamilyNone && families[f] }

var (
	writeWords = regexp.MustCompile(`(?i)\b(write|create|edit|modify|update|refactor|implement|add|remove|delete|rename|fix|patch|generate)\b`)
	fileWords  = regexp.MustCompile(`(?i)\b(file|files|directory|folder|repo|repository|script|code|function|module|package|test|tests)\b|\.[a-z]{1,4}\b`)
	askWords   = regexp.MustCompile(`(?i)^\s*(what|why|how|when|where|who|which|is|are|does|do|can|should|explain|summari[sz]e|describe|compare|list)\b`)
)

// Classify is deterministic and total. Ambiguous goals (both a question and
// a file verb, or neither shape) are `none`: ineligible rather than
// guessed, because a wrong population label corrupts every experiment that
// uses it.
func Classify(goalText string) (FamilyKey, string) {
	t := strings.TrimSpace(goalText)
	if t == "" {
		return FamilyNone, "empty goal"
	}
	write := writeWords.MatchString(t) && fileWords.MatchString(t)
	ask := askWords.MatchString(t) || strings.HasSuffix(t, "?")
	switch {
	case write && ask:
		return FamilyNone, "ambiguous: question shape and file-mutation shape"
	case write:
		return FamilyWriteLocal, "file-mutation verb with a file noun"
	case ask:
		return FamilyAnswer, "question shape"
	}
	return FamilyNone, "no registered shape matched"
}
