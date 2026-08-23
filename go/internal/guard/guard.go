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

// exfilURLShape is the URL half without Python's lookahead, ANCHORED so
// it can be tested at each scheme occurrence (see the scan loop). Matches
// are re-checked against allowedURLHosts before they count as findings.
// The scheme tolerates ANY run of leading slashes/backslashes
// (`https:host/x`, `https:/host/x`, `https://host/x`, `https:///host/x`,
// `https:/\host/x`, …) because WHATWG special schemes do: after the colon
// the parser enters the "special authority ignore slashes state", which
// consumes an UNBOUNDED run of `/` and `\` before the authority. A real
// client fetches all of them identically, so the run must be `[/\\]*`, not
// a fixed count. r5 capped it at `/{0,2}`, which SILENTLY un-matched the
// 3+-slash forms — a parity regression that made Go WEAKER than Python
// (whose `://`-anchored regex absorbs the extra slashes into its host
// group and still flags them). r6 restores the unbounded grammar and the
// Python parity. (Python still misses the 0/1-slash forms — backport
// candidate #10 stands.) The candidate is 512-byte-bounded before matching
// and RE2 is linear-time, so the unbounded `*` carries no DoS risk.
// The host group's first char is [^/\\\s] so the `[/\\]*` prefix consumes
// the whole slash/backslash run, never leaving one absorbed into the host
// — otherwise a nested `https://x.com/...` would match with a slash as a
// host char, defeating the short-inner-host exclusion that keeps the legit
// r.jina.ai proxy shape clean (r5 regression guard, kept in r6).
// The host-span upper bound is urlCandidateMax (512), NOT a short ~50 (r12):
// the span between the first host char and the TLD also covers USERINFO
// (`user@`, discarded by every client) and long subdomain labels. A ~50 cap
// let an attacker starve the real registrable host out of the shape's window
// with free padding — `https://r.jina.ai:tok<23×A>@evil-collector.com/leak`
// (userinfo) or `https://<60×a>.evil-collector.com/leak` (subdomain) — so the
// shape missed it and `urlHostAllowed` (which DOES parse userinfo, via the
// last `@`) was never consulted because of the `||` short-circuit. Widening
// the MAX to the candidate window lets the shape match the padded span and
// hand off to urlHostAllowed for the authoritative allowlist decision; the
// MIN stays {2} so the short-inner-host exclusion (`https://x.com/...` clean)
// is untouched. RE2's bounded repeat is still linear, and the cand is already
// capped to skip+512, so the shape can never scan past the window. (r12 opus
// review; Python's {3,50} bound has the SAME blind spot — backport #11. The
// residual >512-byte-userinfo case is a documented known-gap, since resolving
// it needs an unbounded authority scan = the O(n²) the cap exists to prevent.)
// The payload anchor is a DELIMITER `[/?#]` (optionally after a trailing FQDN
// dot `\.?`), not a bare `/` (r13 opus review): a real client terminates the
// authority on `?` and `#` too, so `https://evil.com?data=SECRET` (query) and
// `https://evil.com#data=SECRET` (fragment) — the canonical exfil carriers —
// and `https://evil.com./leak` (root-anchored FQDN) all fetch to a non-
// allowlisted host but scanned CLEAN under the old `.tld/` anchor. Requiring
// 5+ payload bytes after the delimiter keeps bare `https://github.com[/]`
// clean. (Reach the shape does NOT cover — IP-literal hosts, TLDs outside
// com/io/net, ≤2-char labels, <5-byte payloads — is a documented heuristic
// limit shared with Python, pinned by TestURLExfilHeuristicReachKnownGap.)
var exfilURLShape = regexp.MustCompile(`(?i)^https?:[/\\]*[^/\\\s][^\s]{2,512}\.(com|io|net)\.?[/?#][^\s]{5,}`)

// schemeRe locates every URL scheme occurrence (slash count irrelevant —
// the shape and host parser handle the slashes). Python's re.search scans
// ALL start positions, so a nested URL like
// `https://r.jina.ai/https://evil.com/leak` is flagged on its INNER host
// even though the outer host is allowlisted; Go's FindAllString returns
// one leftmost-longest match and would allowlist on the outer host (the
// r2-review bypass). Testing each scheme position restores the parity.
var schemeRe = regexp.MustCompile(`(?i)https?:`)

var allowedURLHosts = []string{"r.jina.ai", "api.anthropic.com"}

// urlNormalizer applies the WHATWG preprocessing a real client does before
// parsing, ONCE over a URL-only copy of the scanned text before per-scheme
// slicing (whole-string, not per-candidate — doing it after the byte cap left
// a starvation hole, r8):
//   - remove ASCII tab/CR/LF (they can't hide inside a hostname);
//   - percent-decode the STRUCTURAL delimiters `%2e`→`.`, `%2f`→`/`,
//     `%5c`→`\`, `%3a`→`:` (r13 opus review). A WHATWG client percent-decodes
//     these before host/scheme parsing, so without this an attacker hid the
//     TLD dot (`evil-collector%2Ecom/leak` → clean) or laundered a nested
//     scheme past the r2 per-scheme scan (`r.jina.ai/https%3A%2F%2Fevil.com`
//     → only the allowlisted outer scheme was seen). Decoding is safe-
//     direction: it only ever reveals MORE structure (more scheme positions,
//     a real host boundary), and urlHostAllowed's exact match still protects
//     allowlisted hosts. Hex letters are case-insensitive, digits fixed.
var urlNormalizer = strings.NewReplacer(
	"\t", "", "\r", "", "\n", "",
	"%2e", ".", "%2E", ".",
	"%2f", "/", "%2F", "/",
	"%5c", "\\", "%5C", "\\",
	"%3a", ":", "%3A", ":",
)

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
		// flag on input no real suggestion carries; TestURLScanStaysLinear
		// pins only the timing, which is unaffected.
		if truncated && strings.IndexAny(cand[skip:], "/\\?#") < 0 {
			findings = append(findings, "exfiltration pattern: "+strconv(clipMatch(cand)))
			blocked = append(blocked, "oversized-unresolvable-authority")
			hasExfil = true
			break
		}
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

func urlHostAllowed(url string) bool {
	rest := url
	for _, p := range []string{"https:", "http:"} {
		if strings.HasPrefix(asciiLower(rest), p) {
			rest = rest[len(p):]
			break
		}
	}
	// Strip the entire leading run of slashes/backslashes — WHATWG's
	// "special authority ignore slashes state" consumes an UNBOUNDED run of
	// `/` and `\` before the authority, so this loop must match exfilURLShape's
	// `[/\\]*` prefix exactly (r6: an `n < 2` cap here would mis-read the host
	// of a 3+-slash URL that the widened shape now lets through).
	for len(rest) > 0 && (rest[0] == '/' || rest[0] == '\\') {
		rest = rest[1:]
	}
	// Authority ends at the first '/', '\', '?', or '#'. Backslash is an
	// authority terminator for special schemes (http/https) in the WHATWG
	// URL Standard — a real client fetches `https://evil.com\@host/x` with
	// host `evil.com` (the '\' ends the authority BEFORE the '@'), so a
	// parser that split only on '@' would read the trailing host as the
	// target and wave it through (r4 review; Python's prefix-check happens
	// to flag this because the real host sits right after the scheme).
	authority := rest
	if i := strings.IndexAny(authority, "/\\?#"); i >= 0 {
		authority = authority[:i]
	}
	// Strip userinfo: everything up to and including the LAST '@'.
	if at := strings.LastIndexByte(authority, '@'); at >= 0 {
		authority = authority[at+1:]
	}
	// Strip an optional :port. ASCII-only fold (see asciiLower) — a Unicode
	// fold here would let an IDN homoglyph collapse into the allowlist.
	host := asciiLower(authority)
	if i := strings.IndexByte(host, ':'); i >= 0 {
		host = host[:i]
	}
	// Strip a single trailing FQDN dot: `api.anthropic.com.` is the same host
	// as `api.anthropic.com` to every client (r13 — the shape now matches the
	// root-anchored form, so this keeps the allowlisted variant clearing).
	host = strings.TrimSuffix(host, ".")
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
