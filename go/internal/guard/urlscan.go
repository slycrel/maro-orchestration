// URL-exfiltration scanning, spec-grounded (r15). The r10–r14 review arc
// proved the hand-rolled shape-regex + string-substitution detector was
// minting defects as fast as it closed them (four consecutive opus rounds
// of HIGHs, each inside the previous round's fix — full ledger in
// go/REVIEW.md). This file replaces the detector's INTERNALS with the
// WHATWG URL parser (github.com/nlnwa/whatwg-url — passes web-platform-
// tests), so "what host would a real client fetch?" is answered by the
// fetch standard's own algorithm instead of an approximation of it.
//
// The POLICY is unchanged and stays ours:
//   - only http/https candidates are considered;
//   - the true host must EXACTLY match an allowlisted host to clear
//     (r.jina.ai fetch proxy, api.anthropic.com);
//   - a non-allowlisted host flags only within the heuristic's documented
//     reach: a .com/.io/.net host with a >=3-char stem and a 5+-byte
//     payload after the first authority terminator (Python parity —
//     injection_guard.py's {3,50} + (com|io|net); the reach pin
//     TestURLExfilHeuristicReachKnownGap keeps the ledger honest);
//   - fail-closed on what cannot be resolved (oversized delimiter-free
//     authorities, unparseable truncated candidates).
//
// What the parser buys over r14: userinfo, ports, trailing dots, percent-
// encoded host bytes, backslash authority terminators, IDN/homoglyph
// hosts (punycoded, so they can never collapse into the ASCII allowlist),
// and the encoded-nested-proxy laundering gap (#15) — closed below by a
// bounded decode-and-rescan that is safe HERE because it never rewrites
// the outer candidate's authority (the r13/r14 lesson: whole-string
// decoding invents authority terminators; per-component decoding after
// the parse cannot).
package guard

import (
	"strings"

	whatwg "github.com/nlnwa/whatwg-url/url"
)

var allowedURLHosts = []string{"r.jina.ai", "api.anthropic.com"}

// nestedDecodeDepth bounds the decode-and-rescan recursion for proxy-
// nested candidates. Each level percent-decodes one layer at most, over a
// candidate already capped at urlCandidateMax bytes, so total work per
// top-level candidate is O(depth × window) — constant.
const nestedDecodeDepth = 3

func urlHostIsAllowed(host string) bool {
	for _, h := range allowedURLHosts {
		if host == h {
			return true
		}
	}
	return false
}

// hostInHeuristicReach reports whether a non-allowlisted host is inside
// the exfil heuristic's documented reach: .com/.io/.net with a >=3-char
// stem. Deliberately NOT widened here (IP literals, other TLDs, short
// stems like x.com stay clean) — the r.jina.ai proxy pattern legitimately
// nests short inner URLs, and widening the reach is a policy decision,
// not a parser question. TestURLExfilHeuristicReachKnownGap flips if this
// changes.
func hostInHeuristicReach(host string) bool {
	for _, tld := range []string{".com", ".io", ".net"} {
		if strings.HasSuffix(host, tld) {
			return len(host)-len(tld) >= 3
		}
	}
	return false
}

// evalURLCandidate decides whether one URL candidate is an exfil finding.
// cand starts at its scheme and is already whitespace-delimited and
// byte-capped by the caller; skip is the index of the first byte past the
// scheme + WHATWG ignore-slashes run; truncated means the byte cap cut
// the candidate (no space bounded it). Returns a blocked-pattern label,
// or "" for clean.
func evalURLCandidate(cand string, skip int, truncated bool, depth int) string {
	if !urlCandidateNeedsParse(cand) {
		return ""
	}
	u, err := whatwg.Parse(cand)
	if err != nil {
		// A candidate no WHATWG client can parse is not fetchable — clean —
		// UNLESS the cap cut it: then the un-parseable tail may hide the
		// real authority, and fail-closed is the only honest verdict (the
		// same posture as the oversized-authority branch).
		if truncated {
			return "unparseable-truncated-url"
		}
		return ""
	}
	if s := u.Scheme(); s != "http" && s != "https" {
		return ""
	}
	// Hostname() is the post-parse host: percent-decoded, IDNA/punycoded,
	// ASCII-lowercased, port excluded. Strip the FQDN root dot — same host
	// to every client.
	host := strings.TrimSuffix(u.Hostname(), ".")
	if urlHostIsAllowed(host) {
		// Encoded-nested laundering through an allowlisted proxy (closes
		// known-gap #15): `https://r.jina.ai/https%3A%2F%2Fevil.com%2F...`
		// carries the attacker URL where only the proxy decodes it. Decode
		// ONE layer of everything past the authority and rescan for nested
		// schemes. This is additive-only: it can create findings, never
		// clear one, and it never touches the outer authority (the r14
		// false-ALLOW class is unreachable by construction).
		if depth > 0 {
			if i := strings.IndexAny(cand[skip:], "/\\?#"); i >= 0 {
				if f := scanNestedCandidates(percentDecodeLenient(cand[skip+i:]), depth-1); f != "" {
					return f
				}
			}
		}
		return ""
	}
	if !hostInHeuristicReach(host) {
		return ""
	}
	// Payload: 5+ non-space bytes after the first authority terminator in
	// the raw candidate (parity with the r13/r14 anchor semantics — keeps
	// bare `https://github.com/` clean).
	rest := cand[skip:]
	i := strings.IndexAny(rest, "/\\?#")
	if i < 0 || len(rest)-i-1 < 5 {
		return ""
	}
	return "non-allowlisted-url-host"
}

// urlCandidateNeedsParse is a PERFORMANCE gate, not a detector: it may
// only return false when NO parse outcome could flag the candidate, so
// skipping is provably additive. The soundness argument: every flag path
// needs either (a) a hidden authority (truncated with NO in-window
// terminator — but ScanContent's oversized branch flags that shape BEFORE
// this gate runs, so any candidate reaching here has its full authority
// inside the window), (b) a parsed host ending in .com/.io/.net, or (c)
// an allowlisted proxy host whose payload hides a nested URL. For a
// candidate that is pure ASCII and percent-free, WHATWG host parsing is
// byte-preserving except for ASCII lowercasing — the host is a contiguous
// lowercased byte run of the candidate — so (b) implies the lowercased
// candidate contains ".com"/".io"/".net" literally, and (c) implies it
// contains an allowlisted host literally (api.anthropic.com is covered by
// ".com"; r.jina.ai gets its own token). Any non-ASCII byte or '%' can
// reach a TLD through IDNA mapping (U+3002 → '.', fullwidth letters) or
// percent-decoding, so those ALWAYS parse. The skip also skips the
// unparseable-truncated fail-closed flag — sound for the same reason: a
// pure-ASCII parse failure with the authority in-window means no WHATWG
// client fetches it, and nothing is hidden past the cap. Without this
// gate the 150k-candidate linearity blob spends ~65µs/candidate in the
// parser and the 10s ceiling is consumed on this box (r15: 10s → <1s).
func urlCandidateNeedsParse(cand string) bool {
	for i := 0; i < len(cand); i++ {
		if cand[i] >= 0x80 || cand[i] == '%' {
			return true
		}
	}
	lower := asciiLower(cand)
	for _, tok := range []string{".com", ".io", ".net", "r.jina.ai"} {
		if strings.Contains(lower, tok) {
			return true
		}
	}
	return false
}

// scanNestedCandidates runs the candidate evaluation over a bounded slice
// of a proxy URL's path/query/fragment, decoding progressively: a double-
// encoded inner scheme (`https%253A…`) exposes its `:` only on the second
// decode, so each depth level decodes once more and rescans. The input is
// already one-decoded by the caller; total work is O(depth × window).
func scanNestedCandidates(decoded string, depth int) string {
	for d := 0; d <= depth; d++ {
		for _, loc := range schemeRe.FindAllStringIndex(decoded, -1) {
			inner := decoded[loc[0]:]
			if i := strings.IndexByte(inner, ' '); i >= 0 {
				inner = inner[:i]
			}
			skip := loc[1] - loc[0]
			for skip < len(inner) && (inner[skip] == '/' || inner[skip] == '\\') {
				skip++
			}
			if f := evalURLCandidate(inner, skip, false, depth-d); f != "" {
				return f
			}
		}
		next := percentDecodeLenient(decoded)
		if next == decoded {
			return ""
		}
		decoded = next
	}
	return ""
}

// percentDecodeLenient decodes %XX byte pairs and passes malformed
// sequences through unchanged (a strict decoder would let one bad escape
// mask a nested URL). Output length <= input length, so bounds carry.
func percentDecodeLenient(s string) string {
	if !strings.ContainsRune(s, '%') {
		return s
	}
	var b strings.Builder
	b.Grow(len(s))
	for i := 0; i < len(s); i++ {
		if s[i] == '%' && i+2 < len(s) {
			hi, ok1 := unhexByte(s[i+1])
			lo, ok2 := unhexByte(s[i+2])
			if ok1 && ok2 {
				b.WriteByte(hi<<4 | lo)
				i += 2
				continue
			}
		}
		b.WriteByte(s[i])
	}
	return b.String()
}

func unhexByte(c byte) (byte, bool) {
	switch {
	case '0' <= c && c <= '9':
		return c - '0', true
	case 'a' <= c && c <= 'f':
		return c - 'a' + 10, true
	case 'A' <= c && c <= 'F':
		return c - 'A' + 10, true
	}
	return 0, false
}
