// Package scrub ports src/secret_scrub.py — single-source secret scrubbing
// for anything a pack ships.
//
// Two guarantees, deliberately distinct (same split as the Python module):
// Secrets() catches secret-SHAPED strings; Identifiers() catches KNOWN
// local identifiers ($HOME, username, hostname, caller deny-list). Neither
// is anonymization — the mandatory human review gate (REVIEW.md / seal) is
// what actually backstops both. Conservative by design: a false redaction
// is harmless, a leaked key is not.
package scrub

import (
	"os"
	"regexp"
	"sort"
)

// Patterns ported one-for-one from secret_scrub._SECRET_RES; keep the two
// files in sync — the shapes are the shared contract, not per-runtime taste.
var secretRes = []*regexp.Regexp{
	regexp.MustCompile(`sk-ant-[A-Za-z0-9_\-]{16,}`),
	regexp.MustCompile(`sk-[A-Za-z0-9_\-]{16,}`),
	regexp.MustCompile(`gh[pousr]_[A-Za-z0-9]{20,}`),
	regexp.MustCompile(`xox[baprs]-[A-Za-z0-9\-]{10,}`),
	regexp.MustCompile(`AKIA[0-9A-Z]{16}`),
	regexp.MustCompile(`(?i)(bearer|authorization|api[_-]?key|token|secret|password)\s*[:=]\s*\S{8,}`),
}

// Secrets redacts secret-shaped substrings from s.
func Secrets(s string) string {
	for _, rx := range secretRes {
		s = rx.ReplaceAllString(s, "[REDACTED]")
	}
	return s
}

// Identifiers holds the known-local-identifier replacement set for one
// export. Build once per export (BuildIdentifiers), apply to every string.
type Identifiers struct {
	patterns []struct {
		rx    *regexp.Regexp
		token string
	}
}

// BuildIdentifiers mirrors scrub_identifiers()'s pattern assembly: $HOME
// as a literal (stable [HOME] token so exported text stays executable in
// spirit), username/hostname/deny-list word-bounded, longest needle first
// so the full $HOME path is redacted before a bare username substring
// would chew into what's left of it. Empty home/hostname arguments derive
// from this machine; tests pin them for deterministic assertions.
func BuildIdentifiers(home, hostname string, denylist []string) *Identifiers {
	if home == "" {
		home, _ = os.UserHomeDir()
	}
	if hostname == "" {
		hostname, _ = os.Hostname()
	}
	username := ""
	if home != "" {
		trimmed := home
		for len(trimmed) > 1 && trimmed[len(trimmed)-1] == '/' {
			trimmed = trimmed[:len(trimmed)-1]
		}
		for i := len(trimmed) - 1; i >= 0; i-- {
			if trimmed[i] == '/' {
				username = trimmed[i+1:]
				break
			}
		}
		if username == "" {
			username = trimmed
		}
	}

	type pair struct{ needle, token string }
	var literal []pair
	if home != "" {
		literal = append(literal, pair{home, "[HOME]"})
	}
	var bounded []pair
	if username != "" {
		bounded = append(bounded, pair{username, "[USER]"})
	}
	if hostname != "" {
		bounded = append(bounded, pair{hostname, "[HOST]"})
	}
	for _, item := range denylist {
		if item != "" {
			bounded = append(bounded, pair{item, "[REDACTED]"})
		}
	}
	sort.SliceStable(bounded, func(i, j int) bool {
		return len(bounded[i].needle) > len(bounded[j].needle)
	})

	id := &Identifiers{}
	for _, p := range literal {
		id.patterns = append(id.patterns, struct {
			rx    *regexp.Regexp
			token string
		}{regexp.MustCompile(regexp.QuoteMeta(p.needle)), p.token})
	}
	for _, p := range bounded {
		id.patterns = append(id.patterns, struct {
			rx    *regexp.Regexp
			token string
		}{regexp.MustCompile(`\b` + regexp.QuoteMeta(p.needle) + `\b`), p.token})
	}
	return id
}

// Apply redacts every known identifier from s.
func (id *Identifiers) Apply(s string) string {
	for _, p := range id.patterns {
		s = p.rx.ReplaceAllString(s, p.token)
	}
	return s
}

// Walk applies fn to every string in a decoded-JSON value (keys and
// values), the same recursive coverage the Python scrubbers have — a
// secret inside a nested list-of-dicts must not survive because only
// top-level strings were walked.
func Walk(v any, fn func(string) string) any {
	switch t := v.(type) {
	case string:
		return fn(t)
	case []any:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = Walk(e, fn)
		}
		return out
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, e := range t {
			out[fn(k)] = Walk(e, fn)
		}
		return out
	default:
		return v
	}
}
