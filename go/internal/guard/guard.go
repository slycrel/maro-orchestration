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

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// Patterns that look like attempts to override system instructions
// (Python _OVERRIDE_PATTERNS).
//
// "Verbatim" is what this used to say, and verbatim is exactly the bug:
// every `\s` below was Go's, which is five code points and does not even
// include U+000B, where Python's re reads its full 29. pytext.SpaceClass
// exists for this and mission-r5 rebuilt closure's verdict opener from
// it; these thirteen patterns were never swept.
//
// This is the gate, not a field. Measured on this box, ScanContent vs
// injection_guard.scan_content, six payloads pass here and are caught
// there — "ignore<U+00A0>all previous instructions",
// "ignore<U+000B>all previous instructions", the U+001C variant,
// "you<U+2003>are<U+2003>now<U+2003>a helpful pirate",
// "leak<U+00A0>the credentials", "DAN<U+00A0>mode". evolver/store.go's
// fail-closed apply gate is byte-for-byte evolver_store.py's, so on
// identical suggestions.jsonl content CPython writes
// status:"injection_risk_blocked" and stops while Go writes
// applied:true and performs the guardrail/lesson write: two runtimes,
// one row, opposite durable outcomes (adversarial mission-r6 HIGH).
//
// `\b` is left as Go's here on purpose and is NOT the same class of
// hole: an ASCII-only boundary is STRICTER at a non-ASCII neighbour
// (it declines to fire), so it can only fail to match text Python also
// has to reach through a letter run. The `\s` direction was the one that
// let payloads through. The Unicode-boundary residual is named in
// PORT.md alongside intent.go's, where it flips a lane rather than a
// gate.
var overridePatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bignore` + pytext.SpaceClass + `+` +
		`(all` + pytext.SpaceClass + `+` +
		`)?previous` + pytext.SpaceClass + `+` +
		`(instructions?|context|above)\b`),
	regexp.MustCompile(`(?i)\bforget` + pytext.SpaceClass + `+` +
		`(all` + pytext.SpaceClass + `+` +
		`)?previous\b`),
	regexp.MustCompile(`(?i)\bsystem` + pytext.SpaceClass + `*` +
		`:` + pytext.SpaceClass + `*` +
		`you` + pytext.SpaceClass + `+` +
		`are\b`),
	regexp.MustCompile(`(?i)\bnew` + pytext.SpaceClass + `+` +
		`instructions?` + pytext.SpaceClass + `*` +
		`:`),
	regexp.MustCompile(`(?i)\bdisregard` + pytext.SpaceClass + `+` +
		`(all` + pytext.SpaceClass + `+` +
		`)?previous\b`),
	regexp.MustCompile(`(?i)\boverride` + pytext.SpaceClass + `+` +
		`your` + pytext.SpaceClass + `+` +
		`(instructions?|rules|guidelines)\b`),
	regexp.MustCompile(`(?i)\byou` + pytext.SpaceClass + `+` +
		`are` + pytext.SpaceClass + `+` +
		`now` + pytext.SpaceClass + `+` +
		`a\b`),
	regexp.MustCompile(`(?i)\bact` + pytext.SpaceClass + `+` +
		`as` + pytext.SpaceClass + `+` +
		`(?:an?` + pytext.SpaceClass + `+` +
		`)?(?:different|new|unrestricted|jailbroken)\b`),
	regexp.MustCompile(`(?i)\bDAN` + pytext.SpaceClass + `*` +
		`mode\b`),
	regexp.MustCompile(`(?i)\bjailbreak\b`),
}

// Raw tool invocation strings outside expected fields (Python
// _TOOL_CALL_PATTERNS, verbatim).
var toolCallPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)<tool_use>`),
	regexp.MustCompile(`(?i)"tool_name"` + pytext.SpaceClass + `*` +
		`:` + pytext.SpaceClass + `*` +
		`"`),
	regexp.MustCompile(`(?i)\btool_call` + pytext.SpaceClass + `*` +
		`\(`),
	regexp.MustCompile(`(?i)<function[_` + pytext.SpaceClassBody + `]call>`),
}

// Exfiltration / redirect patterns (Python _EXFIL_PATTERNS). The URL
// pattern differs from Python's in ONE named way: Go's regexp (RE2) has
// no negative lookahead, so the Python allowlist exclusion
// `(?!r\.jina\.ai|api\.anthropic\.com)` is enforced post-match in
// scanExfil instead — same accept/reject behavior, different mechanism.
var exfilPatterns = []*regexp.Regexp{
	regexp.MustCompile(`(?i)\bsend` + pytext.SpaceClass + `+` +
		`(all` + pytext.SpaceClass + `+` +
		`)?secrets?` + pytext.SpaceClass + `+` +
		`to\b`),
	regexp.MustCompile(`(?i)\bexfiltrat[ei]\b`),
	regexp.MustCompile(`(?i)\bleak` + pytext.SpaceClass + `+` +
		`(the` + pytext.SpaceClass + `+` +
		`)?(credentials?|api` + pytext.SpaceClass + `*` +
		`keys?|tokens?)\b`),
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
			findings = append(findings, "override attempt: "+pyRepr(clipMatch(m)))
			blocked = append(blocked, pat.String())
		}
	}
	for _, pat := range toolCallPatterns {
		if m := pat.FindString(target); m != "" {
			findings = append(findings, "tool call injection: "+pyRepr(clipMatch(m)))
			blocked = append(blocked, pat.String())
		}
	}
	hasExfil := false
	for _, pat := range exfilPatterns {
		if m := pat.FindString(target); m != "" {
			findings = append(findings, "exfiltration pattern: "+pyRepr(clipMatch(m)))
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
	// Each candidate runs from its scheme to the next space (a space ends a
	// URL per WHATWG; tab/CR/LF are already gone from the whole-string strip
	// above). Its verdict — parse, host resolution, allowlist/reach/payload
	// policy, oversized/unparseable fail-closed, and bounded nested-launder
	// rescan — is decided by the single entry point evalRawCandidate
	// (urlscan.go), shared with the nested rescan so both carry identical
	// invariants. A shared work budget bounds total parse/decode effort across
	// all candidates and their nested layers (r16, fail-closed on exhaustion).
	urlTarget := urlNormalizer.Replace(content)
	budget := newScanBudget()
	for _, loc := range schemeRe.FindAllStringIndex(urlTarget, -1) {
		raw := urlTarget[loc[0]:]
		if i := strings.IndexByte(raw, ' '); i >= 0 {
			raw = raw[:i]
		}
		rule := evalRawCandidate(raw, budget, nestedDecodeDepth)
		if rule == "" {
			continue
		}
		findings = append(findings, "exfiltration pattern: "+pyRepr(clipMatch(raw)))
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
// pyRepr renders a matched substring the way Python's f"{...!r}" does.
// It was `"'" + s + "'"`, which is repr() only for a string of printable
// ASCII with no quote in it: Python escapes a non-printable, so a match
// containing U+00A0 is 'ignore\xa0all previous instructions' there and
// carried a raw byte here. The finding string is operator-facing AND
// stored on the scan report, so the two runtimes wrote different rows for
// the same blocked payload — found by the differential written for the
// mission-r6 HIGH, not by the round itself.
func pyRepr(s string) string { return pytext.Repr(s) }
