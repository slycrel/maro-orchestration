// Package guard ports src/injection_guard.py — DEFENSIVE prompt-
// injection hardening for content the framework ingests and may act on
// (evolver suggestions, personas, skills). It scans OUR OWN stored text
// for known instruction-override, tool-call-injection, and exfiltration
// shapes before an auto-apply path is allowed to act on it; it produces
// findings and blocks, never exploit tooling.
//
// The evolver's apply gate is the consumer this tranche ports it for:
// Python apply_suggestion scans the SUGGESTION-TEXT field FAIL-CLOSED
// before any category action runs (a guard that throws blocks the apply
// rather than silently passing content through). Note the scope: the
// gate scans the `suggestion` text, NOT every action-specific field — a
// new_guardrail action's `pattern` field is validated separately (RE2
// compile) but is not guard-scanned, and reaches a durable rule Python
// executes only under opt-in auto-apply (backport candidate; r12).
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

// schemeRe locates every URL scheme occurrence. Python's re.search scans
// ALL start positions, so a nested URL like
// `https://r.jina.ai/https://evil.com/leak` is flagged on its INNER host
// even though the outer host is allowlisted; Go's FindAllString returns
// one leftmost-longest match and would allowlist on the outer host (the
// r2-review bypass). Testing each scheme position restores the parity.
//
// Each candidate's VERDICT comes from the spec-grounded evaluation in
// urlscan.go (WHATWG parse → true host → allowlist/reach/payload policy).
// The r10–r14 shape-regex detector this replaces, and the fix history that
// motivated the replacement, live in go/REVIEW.md.
var schemeRe = regexp.MustCompile(`(?i)https?:`)

// urlNormalizer removes ASCII tab/CR/LF ONCE over a URL-only copy of the
// scanned text, before per-scheme slicing (whole-string, not
// per-candidate — doing it after the byte cap left a starvation hole,
// r8). WHATWG strips these whole-string too, but the strip must happen
// HERE, before the space-delimit and the byte cap, so a mid-host pad
// larger than the candidate window (`evil<600×TAB>collector.com`) cannot
// end the candidate or starve the window before the parser would have
// glued the host back together. No percent-decoding happens here (the
// r13/r14 lesson): the parser decodes the host component itself, after
// literal delimiters fix the authority boundary.
var urlNormalizer = strings.NewReplacer("\t", "", "\r", "", "\n", "")

// urlCandidateMax bounds per-scheme candidate work (DoS guard), in BYTES
// — a byte slice is deliberate so a huge candidate is never rune-copied
// (that would defeat the cap). The cap is applied AFTER the scheme + the
// unbounded WHATWG ignore-slashes/control prefix (see the scan loop), so it
// bounds the AUTHORITY window, not the raw offset from the scheme — a long
// slash/tab pad can't starve the host out of the window (r7). A mid-rune
// cut is harmless: the exfil shape's host sits in the first ~54 bytes of
// the authority, so anything past the cap is irrelevant to the match.
const urlCandidateMax = 512

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
	lower := asciiLower(source) // ASCII-only fold; a Unicode fold would let an
	// IDN homoglyph source (e.g. "skİlls") collapse into the allowlist.
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
	//
	// WHATWG removes ASCII tab/CR/LF from a URL as a WHOLE-STRING
	// preprocessing step, not a per-URL one — a real client deletes them
	// wherever they sit, including in the MIDDLE of a host. So strip them
	// ONCE, over a URL-only copy, BEFORE per-scheme slicing and the byte
	// cap. Doing it per-candidate AFTER the cap (as r7 did) left a
	// starvation hole: `https://evil` + 600 tabs + `collector.com/leak`
	// stops the leading-run skip at `evil`, so the cap truncates the tabs
	// and the TLD away before the strip could collapse them, and the URL
	// scanned clean while a real client fetches `evilcollector.com` (r8
	// review — the r3 tab-in-host evasion class at a scale past the cap).
	//
	// The URL scan reads the FULL `content`, NOT the scanMaxChars-clipped
	// `target` the keyword passes use: the 50k rune clip is itself a
	// starvable prefix budget — `https:` + 60k slashes/tabs + `evil.com/x`
	// clips to 50k of pure padding and loses the host, while a real client
	// fetches straight through it (r8 QA review; shared fork-point gap —
	// Python's content[:max_chars] has the same blind spot — backport
	// candidate #11). Scanning the full content adds no DoS class: the
	// keyword clip already does `[]rune(content)` (O(content) alloc), this
	// strip is the same order, RE2 is linear, and the inner skip+512 cap
	// bounds per-candidate work, so total URL-scan work is linear in
	// content. It is a SEPARATE copy so the override/tool-call/keyword loops
	// above keep reading `target` with original offsets and evidence.
	urlTarget := urlNormalizer.Replace(content)
	for _, loc := range schemeRe.FindAllStringIndex(urlTarget, -1) {
		cand := urlTarget[loc[0]:]
		// Skip the scheme + the WHATWG "special authority ignore slashes"
		// run BEFORE capping. That run (`/` and `\`, UNBOUNDED since r6) is
		// position-dependent and is NOT globally removed, so a long slash-
		// pad — `https:` + 600×`/` + `evil.com/leak` — could still fill the
		// whole 512-byte window and truncate the host away if the cap were
		// applied from the scheme (r6/r7; both Go and Python missed this —
		// Python's `{3,50}` host bound has the same blind spot, so closing
		// it makes Go MORE correct than Python — backport candidate #11).
		// Capping AFTER the run guarantees the window holds the authority.
		// tab/CR/LF are already gone (global strip above), so the run is
		// just slashes now. The skip is O(run) and each run belongs to one
		// scheme, so total work stays linear in the content.
		//
		// The scheme length comes from schemeRe's OWN match bounds
		// (loc[1]-loc[0], i.e. len("http:")|len("https:")), NOT a
		// strings.ToLower(cand)+HasPrefix — that lowercased the WHOLE
		// unbounded suffix before the cap, so `https:`×N with no whitespace
		// was O(N) matches × O(len) each = O(n²) CPU+alloc once r8 scanned
		// the full content (r9 review; this falsified r8's "no new DoS
		// class" note). loc[1]-loc[0] is O(1) and exact.
		skip := loc[1] - loc[0]
		for skip < len(cand) && (cand[skip] == '/' || cand[skip] == '\\') {
			skip++
		}
		// Bound the AUTHORITY window (in BYTES). Without this, a blob of
		// `https://` repeated with no whitespace is O(schemes × tail) per
		// ScanContent — each candidate would be the whole remaining target
		// (r4 review DoS). The exfil shape needs a scheme, an authority that
		// fits the window (host plus any userinfo/subdomain padding, up to
		// urlCandidateMax — r12), a TLD, and a short path, so the window past
		// the slash run is enough to decide a match.
		truncated := len(cand) > skip+urlCandidateMax
		if truncated {
			cand = cand[:skip+urlCandidateMax]
		}
		// Candidate ends at the next SPACE (tab/CR/LF already stripped, so a
		// bare line-end host has been glued to whatever followed — matching
		// how a real client normalizes it).
		if i := strings.IndexByte(cand, ' '); i >= 0 {
			cand = cand[:i]
			truncated = false // a space bounded it; the authority is whole
		}
		// Oversized unresolvable authority (r13 opus review, closing the r12
		// known-gap). If the candidate was truncated by the byte cap AND the
		// window holds NO authority terminator (`/ \ ? #`), the real host sits
		// past the window — e.g. `https://<600×A>@evil-collector.com/leak`,
		// where the host hides behind >512 bytes of userinfo. No legitimate
		// URL has a >512-byte delimiter-free authority (DNS names cap at 253),
		// so flag it FAIL-CLOSED rather than clear it. This is O(1) on already-
		// bounded data (one IndexAny over the window), NOT the unbounded scan
		// the earlier rationale wrongly claimed was required. It also flags a
		// pathological scheme-only/giant-token blob (`https:`×N) — a safe over-
		// flag on input no real suggestion carries; TestURLOversizedAuthority-
		// ShortCircuits pins that. Note the `break`: this path terminates the
		// scan on the first oversized candidate, so it is NOT a valid linearity
		// fixture — TestURLScanStaysLinear must use a blob whose candidates each
		// hold an in-window `/` so this branch never fires and all iterations
		// run (r14: the old `https:`×N linearity fixture went vacuous here).
		if truncated && strings.IndexAny(cand[skip:], "/\\?#") < 0 {
			findings = append(findings, "exfiltration pattern: "+strconv(clipMatch(cand)))
			blocked = append(blocked, "oversized-unresolvable-authority")
			hasExfil = true
			break
		}
		rule := evalURLCandidate(cand, skip, truncated, nestedDecodeDepth)
		if rule == "" {
			continue
		}
		findings = append(findings, "exfiltration pattern: "+strconv(clipMatch(cand)))
		blocked = append(blocked, rule)
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

// asciiLower lowercases ASCII A-Z byte-wise and leaves every other byte
// (including all bytes >= 0x80) untouched. It deliberately does NOT use
// strings.ToLower, whose Unicode simple case-folding maps non-ASCII runes
// onto ASCII letters — e.g. U+0130 'İ' folds to 'i', so `strings.ToLower`
// turned `api.anthropİc.com` into the allowlisted `api.anthropic.com` and
// waved an attacker host through (r12 opus review). A byte-wise ASCII fold
// can never collapse a non-ASCII byte into a pure-ASCII allowlist entry, so
// any IDN/homoglyph host fails the exact match and is flagged (safe
// direction). Python's str.lower folds U+0130 to two codepoints, so it never
// had this bypass — this keeps Go at parity.
func asciiLower(s string) string {
	var b []byte
	for i := 0; i < len(s); i++ {
		c := s[i]
		if 'A' <= c && c <= 'Z' {
			if b == nil {
				b = []byte(s)
			}
			b[i] = c + ('a' - 'A')
		}
	}
	if b == nil {
		return s
	}
	return string(b)
}

// strconv mirrors Python's %r finding rendering closely enough for the
// operator-facing message (quoted evidence).
func strconv(s string) string { return "'" + s + "'" }
