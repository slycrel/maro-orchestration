// Package guard ports src/injection_guard.py — DEFENSIVE prompt-
// injection hardening for content the framework ingests and may act on
// (evolver suggestions, personas, skills). It scans OUR OWN stored text
// for known instruction-override, tool-call-injection, and exfiltration
// shapes before an auto-apply path is allowed to act on it; it produces
// findings and blocks, never exploit tooling.
//
// The evolver's apply gate is the consumer this tranche ports it for:
// Python apply_suggestion scans suggestion text FAIL-CLOSED before any
// category action runs (a guard that throws blocks the apply rather
// than silently passing content through).
//
// Pattern lists and the allowlist are verbatim ports. The Python
// module's scan_persona_yaml wrapper has no Go consumer yet and is not
// ported.
package guard

import (
	"crypto/sha1"
	"encoding/hex"
	"regexp"
	"strings"
)

// Patterns that look like attempts to override system instructions
// (Python _OVERRIDE_PATTERNS, verbatim).
var overridePatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bignore\s+(all\s+)?previous\s+(instructions?|context|above)\b`),
	regexp.MustCompile(`(?i)\bforget\s+(all\s+)?previous\b`),
	regexp.MustCompile(`(?i)\bsystem\s*:\s*you\s+are\b`),
	regexp.MustCompile(`(?i)\bnew\s+instructions?\s*:`),
	regexp.MustCompile(`(?i)\bdisregard\s+(all\s+)?previous\b`),
	regexp.MustCompile(`(?i)\boverride\s+your\s+(instructions?|rules|guidelines)\b`),
	regexp.MustCompile(`(?i)\byou\s+are\s+now\s+a\b`),
	regexp.MustCompile(`(?i)\bact\s+as\s+(?:an?\s+)?(?:different|new|unrestricted|jailbroken)\b`),
	regexp.MustCompile(`(?i)\bDAN\s*mode\b`),
	regexp.MustCompile(`(?i)\bjailbreak\b`),
}

// Raw tool invocation strings outside expected fields (Python
// _TOOL_CALL_PATTERNS, verbatim).
var toolCallPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)<tool_use>`),
	regexp.MustCompile(`(?i)"tool_name"\s*:\s*"`),
	regexp.MustCompile(`(?i)\btool_call\s*\(`),
	regexp.MustCompile(`(?i)<function[_\s]call>`),
}

// Exfiltration / redirect patterns (Python _EXFIL_PATTERNS). The URL
// pattern differs from Python's in ONE named way: Go's regexp (RE2) has
// no negative lookahead, so the Python allowlist exclusion
// `(?!r\.jina\.ai|api\.anthropic\.com)` is enforced post-match in
// scanExfil instead — same accept/reject behavior, different mechanism.
var exfilPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bsend\s+(all\s+)?secrets?\s+to\b`),
	regexp.MustCompile(`(?i)\bexfiltrat[ei]\b`),
	regexp.MustCompile(`(?i)\bleak\s+(the\s+)?(credentials?|api\s*keys?|tokens?)\b`),
}

// exfilURLShape is the URL half without Python's lookahead, ANCHORED so
// it can be tested at each scheme occurrence (see the scan loop). Matches
// are re-checked against allowedURLHosts before they count as findings.
var exfilURLShape = regexp.MustCompile(`(?i)^https?://[^\s]{3,50}\.(com|io|net)/[^\s]{5,}`)

// schemeRe locates every URL scheme occurrence. Python's re.search scans
// ALL start positions, so a nested URL like
// `https://r.jina.ai/https://evil.com/leak` is flagged on its INNER host
// even though the outer host is allowlisted; Go's FindAllString returns
// one leftmost-longest match and would allowlist on the outer host (the
// r2-review bypass). Testing each scheme position restores the parity.
var schemeRe = regexp.MustCompile(`(?i)https?://`)

var allowedURLHosts = []string{"r.jina.ai", "api.anthropic.com"}

// urlControlStripper removes ASCII tab/CR/LF from a URL candidate (the
// WHATWG normalization step) so they can't hide inside a hostname.
var urlControlStripper = strings.NewReplacer("\t", "", "\r", "", "\n", "")

// Allowed source locations — auto-apply permitted without manual review
// (Python _ALLOWED_SOURCE_DIRS, verbatim).
var allowedSourceDirs = map[string]bool{
	"skills":    true, // repo skills/
	"personas":  true, // repo personas/
	"workspace": true, // ~/.maro/workspace/skills/ or personas/
	"builtin":   true, // shipped default
	"internal":  true, // programmatic (evolver, graduation)
}

// ScanReport is the result of scanning content for injection risk
// (Python InjectionScanReport).
type ScanReport struct {
	ContentHash     string
	Source          string
	IsClean         bool
	Findings        []string
	RiskLevel       string // "low" | "medium" | "high"
	BlockedPatterns []string
}

// SafeToAutoApply is true only if content is clean AND source is
// allowlisted (Python's property of the same name).
func (r ScanReport) SafeToAutoApply() bool {
	return r.IsClean && sourceIsAllowed(r.Source)
}

// sourceIsAllowed: exact match for short programmatic tokens,
// path-COMPONENT match for file paths. Substring match is deliberately
// avoided — it allows keyword-stuffing like
// "github.com/evil/workspace-tools" matching "workspace" (Python
// _source_is_allowed, including its rationale).
func sourceIsAllowed(source string) bool {
	if source == "" {
		return false
	}
	lower := strings.ToLower(source)
	if allowedSourceDirs[lower] {
		return true
	}
	for _, part := range strings.FieldsFunc(lower, func(r rune) bool {
		return r == '/' || r == '\\'
	}) {
		if allowedSourceDirs[part] {
			return true
		}
	}
	return false
}

const scanMaxChars = 50_000 // Python max_chars: 50K cap prevents DOS

// clipMatch bounds a finding's echoed evidence like Python's
// m.group()[:60].
func clipMatch(m string) string {
	r := []rune(m)
	if len(r) > 60 {
		r = r[:60]
	}
	return string(r)
}

// ScanContent scans text for prompt-injection risk patterns (Python
// scan_content). Any exfiltration hit = HIGH regardless of count; 3+
// findings of any kind = HIGH; 1-2 findings = MEDIUM.
func ScanContent(content, source string) ScanReport {
	target := content
	if r := []rune(target); len(r) > scanMaxChars {
		target = string(r[:scanMaxChars])
	}
	sum := sha1.Sum([]byte(target))
	hash := hex.EncodeToString(sum[:])[:8]

	var findings, blocked []string
	for _, pat := range overridePatterns {
		if m := pat.FindString(target); m != "" {
			findings = append(findings, "override attempt: "+strconv(clipMatch(m)))
			blocked = append(blocked, pat.String())
		}
	}
	for _, pat := range toolCallPatterns {
		if m := pat.FindString(target); m != "" {
			findings = append(findings, "tool call injection: "+strconv(clipMatch(m)))
			blocked = append(blocked, pat.String())
		}
	}
	hasExfil := false
	for _, pat := range exfilPatterns {
		if m := pat.FindString(target); m != "" {
			findings = append(findings, "exfiltration pattern: "+strconv(clipMatch(m)))
			blocked = append(blocked, pat.String())
			hasExfil = true
		}
	}
	// URL half: evaluate EVERY scheme occurrence independently (Python
	// re.search parity — see schemeRe). Each candidate runs from its
	// scheme to the next whitespace; if it matches the exfil URL shape
	// and its host is NOT allowlisted, it fires. An allowlisted outer
	// host never launders a non-allowlisted inner one.
	for _, loc := range schemeRe.FindAllStringIndex(target, -1) {
		cand := target[loc[0]:]
		// Candidate ends at the next SPACE. ASCII tab/CR/LF are then
		// REMOVED from within it, mirroring the WHATWG URL "remove all
		// ASCII tab or newline" step — a real client fetches
		// `https://evil<TAB>collector.com/x` as `evilcollector.com`, so a
		// detector that let the tab terminate the host would miss it
		// (r3 review). The exfil shape requires a `.tld/path`, so gluing
		// a bare line-end host to the next line cannot forge a match.
		if i := strings.IndexByte(cand, ' '); i >= 0 {
			cand = cand[:i]
		}
		cand = urlControlStripper.Replace(cand)
		if !exfilURLShape.MatchString(cand) || urlHostAllowed(cand) {
			continue
		}
		findings = append(findings, "exfiltration pattern: "+strconv(clipMatch(cand)))
		blocked = append(blocked, exfilURLShape.String())
		hasExfil = true
		break
	}

	risk := "low"
	switch {
	case hasExfil || len(findings) >= 3:
		risk = "high"
	case len(findings) >= 1:
		risk = "medium"
	}
	return ScanReport{
		ContentHash:     hash,
		Source:          source,
		IsClean:         len(findings) == 0,
		Findings:        findings,
		RiskLevel:       risk,
		BlockedPatterns: blocked,
	}
}

// urlHostAllowed reports whether the URL's true host is EXACTLY an
// allowlisted host. It parses the RFC-3986 authority correctly, which
// closes two bypasses of Python's literal-prefix lookahead
// `(?!r\.jina\.ai|api\.anthropic\.com)` — both DELIBERATE HARDENING
// DIVERGENCES, flagged as backport-correction candidates in PORT.md:
//
//   - Lookalike domain: `https://r.jina.ai.evil.com/leak` — host merely
//     STARTS WITH the allowlisted string but is attacker-owned. Exact
//     match rejects it.
//   - Userinfo confusion: `https://r.jina.ai:tok@evil.com/leak` — the
//     real host is `evil.com` (everything before the last '@' in the
//     authority is userinfo, discarded by every HTTP client). Splitting
//     on '@' before reading the host rejects it.
//
// Exact-match (no subdomain acceptance) is deliberate: it keeps the
// accept set aligned with Python (whose lookahead also flags subdomains
// of an allowlisted apex) and still closes the lookalike bypass.
func urlHostAllowed(url string) bool {
	rest := url
	for _, p := range []string{"https://", "http://"} {
		if strings.HasPrefix(strings.ToLower(rest), p) {
			rest = rest[len(p):]
			break
		}
	}
	// Authority ends at the first '/', '?', or '#'.
	authority := rest
	if i := strings.IndexAny(authority, "/?#"); i >= 0 {
		authority = authority[:i]
	}
	// Strip userinfo: everything up to and including the LAST '@'.
	if at := strings.LastIndexByte(authority, '@'); at >= 0 {
		authority = authority[at+1:]
	}
	// Strip an optional :port.
	host := strings.ToLower(authority)
	if i := strings.IndexByte(host, ':'); i >= 0 {
		host = host[:i]
	}
	for _, h := range allowedURLHosts {
		if host == h {
			return true
		}
	}
	return false
}

// strconv mirrors Python's %r finding rendering closely enough for the
// operator-facing message (quoted evidence).
func strconv(s string) string { return "'" + s + "'" }
