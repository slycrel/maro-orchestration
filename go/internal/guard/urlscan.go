// URL-exfiltration scanning, spec-grounded (r15 introduced the parser; r16
// unified the candidate model after an adversarial review of the swap). The
// r10–r14 review arc proved the hand-rolled shape-regex + string-substitution
// detector minted defects as fast as it closed them (full ledger in
// go/REVIEW.md); r15 replaced its INTERNALS with the WHATWG URL parser
// (github.com/nlnwa/whatwg-url — passes web-platform-tests) so "what host
// would a real client fetch?" is answered by the fetch standard's own
// algorithm. r16 fixed the two HIGHs that review found in r15's NEW code: the
// nested decode-and-rescan was a SECOND caller of the candidate evaluator with
// invariants written for only the first (unbounded work → DoS; blind to the
// byte cap → truncation false-allow). The fix is structural, not another
// patch: ONE entry point (evalRawCandidate) with ONE set of invariants, used
// by both the top-level scan and the nested rescan, plus a global work budget.
//
// The POLICY is unchanged and stays ours:
//   - only http/https candidates are considered;
//   - the true host (as the WHATWG parser resolves it — UTS-46 mapped,
//     punycoded, lowercased, port excluded, exactly what a client fetches)
//     must EXACTLY match an allowlisted host to clear;
//   - a non-allowlisted host flags only within the heuristic's documented
//     reach: a .com/.io/.net host with a >=3-char stem and a 5+-byte payload
//     (measured on the PARSED path/query/fragment, r16 — not the raw
//     space-delimited tail, which a `%20` could starve); Python parity —
//     injection_guard.py's {3,50} + (com|io|net); the reach pin
//     TestURLExfilHeuristicReachKnownGap keeps the ledger honest;
//   - encoded laundering through ANY host that itself clears (allowlisted
//     proxy, out-of-reach proxy, in-reach-with-short-payload) is caught by a
//     bounded, additive decode-and-rescan (r16 runs it for every cleared
//     host, closing the review's MEDIUM-4; it can only mint findings, never
//     clear one, so no false-ALLOW class is reachable through it);
//   - fail-closed on what cannot be resolved (oversized delimiter-free
//     authorities, unparseable truncated candidates, exhausted work budget).
//
// A note on IDN/homoglyph hosts (correcting an r12/r15 comment the review
// flagged as false): the parser applies the SAME UTS-46 mapping a browser
// does, so `evil-collector。com` (ideographic dot) and fullwidth `ｃｏｍ` map to
// their ASCII forms and are flagged, while fullwidth `ａｐｉ.anthropic.com` maps
// to the allowlisted host and is a GENUINE allowlisted fetch (clean). The
// safety property is NOT "non-ASCII can never collapse into the allowlist" (it
// can, and correctly so); it is "the host we compare is exactly the host a
// client fetches." Both directions are pinned (TestURLExfilIDNMappedDotFlagged
// + the fullwidth-api negative control).
package guard

import (
	"strings"

	whatwg "github.com/nlnwa/whatwg-url/url"
)

var allowedURLHosts = []string{"r.jina.ai", "api.anthropic.com"}

// nestedDecodeDepth bounds the decode-and-rescan recursion for proxy-nested
// candidates: a double-encoded inner scheme needs two decode passes, so a
// small depth covers realistic laundering (a real proxy also stops decoding).
const nestedDecodeDepth = 3

// Global per-ScanContent work budgets, fail-closed on exhaustion (r16, the
// review's HIGH-1 fix). WHATWG parsing costs ~65µs/call, so an unbounded fan-
// out of nested candidates was quadratic-plus (measured: 32KB → 83s). These
// bound total work no matter the nesting shape or content size:
//   - maxScanParses caps whatwg.Parse calls; 4096 × ~65µs ≈ 270ms worst case.
//   - maxScanDecodeBytes caps percent-decode allocation across all layers.
// Exhausting either flags FAIL-CLOSED (an input carrying 4096+ distinct URL
// candidates is pathological for an evolver suggestion; manual review is the
// safe verdict). The prefilter (urlCandidateNeedsParse) means a benign blob of
// non-URL text spends zero budget, so only genuinely URL-dense input can
// approach these. Neither is a starvation cap: they bound WORK, and every
// path that stops early on exhaustion flags rather than clears.
const (
	maxScanParses      = 4096
	maxScanDecodeBytes = 1 << 20
)

type scanBudget struct {
	parses      int
	decodeBytes int
}

func newScanBudget() *scanBudget {
	return &scanBudget{parses: maxScanParses, decodeBytes: maxScanDecodeBytes}
}

func urlHostIsAllowed(host string) bool {
	for _, h := range allowedURLHosts {
		if host == h {
			return true
		}
	}
	return false
}

// hostInHeuristicReach reports whether a non-allowlisted host is inside the
// exfil heuristic's documented reach: .com/.io/.net with a >=3-char stem.
// Deliberately NOT widened (IP literals, other TLDs, short stems like x.com
// stay clean) — the r.jina.ai proxy pattern legitimately nests short inner
// URLs, and widening the reach is a one-line policy decision, not a parser
// question. TestURLExfilHeuristicReachKnownGap flips if this changes.
func hostInHeuristicReach(host string) bool {
	for _, tld := range []string{".com", ".io", ".net"} {
		if strings.HasSuffix(host, tld) {
			return len(host)-len(tld) >= 3
		}
	}
	return false
}

// schemeSkip returns the index past the scheme and its WHATWG "special
// authority ignore slashes" run (an UNBOUNDED run of `/` and `\`), or ok=false
// if raw does not start with an http(s) scheme. No allocation (hot path).
func schemeSkip(raw string) (int, bool) {
	var skip int
	switch {
	case asciiHasPrefixFold(raw, "https:"):
		skip = len("https:")
	case asciiHasPrefixFold(raw, "http:"):
		skip = len("http:")
	default:
		return 0, false
	}
	for skip < len(raw) && (raw[skip] == '/' || raw[skip] == '\\') {
		skip++
	}
	return skip, true
}

// asciiHasPrefixFold reports whether s starts with prefix, case-insensitively
// on ASCII A-Z. prefix must be lowercase. No allocation.
func asciiHasPrefixFold(s, prefix string) bool {
	if len(s) < len(prefix) {
		return false
	}
	for i := 0; i < len(prefix); i++ {
		c := s[i]
		if 'A' <= c && c <= 'Z' {
			c += 'a' - 'A'
		}
		if c != prefix[i] {
			return false
		}
	}
	return true
}

// evalRawCandidate is the SINGLE candidate evaluator — the r16 unification.
// raw starts at an http(s) scheme and runs to wherever the caller delimited it
// (the top level cuts at the first space, per WHATWG URL termination; the
// nested rescan passes the decoded remainder uncut, letting the parser
// percent-encode any interior space into the path). It returns a blocked-
// pattern label, or "" for clean. Every safety property lives HERE, so it
// holds identically for both callers — the review's root finding was that r15
// had two callers with divergent invariants.
func evalRawCandidate(raw string, b *scanBudget, depth int) string {
	skip, ok := schemeSkip(raw)
	if !ok {
		return ""
	}
	// Bound the parse INPUT (per-candidate) so a single huge candidate can't
	// make one Parse call O(n). The nested rescan still sees past this cap
	// because it operates on the full raw payload (see scanNested), and the
	// oversized branch below fail-closes the shape this cap would otherwise
	// hide.
	cand := raw
	truncated := len(raw) > skip+urlCandidateMax
	if truncated {
		cand = raw[:skip+urlCandidateMax]
	}
	// Oversized unresolvable authority: truncated with NO in-window terminator
	// means the real host sits past the cap behind delimiter-free padding
	// (userinfo/subdomain). No legitimate URL has a >512-byte delimiter-free
	// authority (DNS names cap at 253), so fail closed. O(1) over the window.
	if truncated && strings.IndexAny(cand[skip:], "/\\?#") < 0 {
		return "oversized-unresolvable-authority"
	}
	// Prefilter (performance, provably additive) — applied ONLY when the
	// candidate is untruncated, so `cand` IS the whole raw and the check
	// covers everything. For a pure-ASCII, percent-free candidate, WHATWG host
	// parsing is byte-preserving except for ASCII lowercasing, so a reach TLD
	// implies the lowercased text literally contains ".com"/".io"/".net" and an
	// allowlisted host implies it literally contains the host token; and with
	// no '%' or non-ASCII byte, no decode or IDNA mapping can materialize a
	// scheme/TLD that isn't already visible — so nested laundering is
	// impossible too. A truncated candidate always parses (it is already
	// suspicious and its tail is unexamined). The review's 400k-case
	// differential fuzz found zero counterexamples to this skip.
	if !truncated && !urlCandidateNeedsParse(cand) {
		return ""
	}
	if b.parses <= 0 {
		return "scan-budget-exhausted"
	}
	b.parses--
	u, err := whatwg.Parse(cand)
	if err != nil {
		// Unfetchable by a conformant client. Clean UNLESS the cap cut it:
		// then the unexamined tail may hide the real authority, so fail closed.
		if truncated {
			return "unparseable-truncated-url"
		}
		return ""
	}
	if s := u.Scheme(); s != "http" && s != "https" {
		return ""
	}
	host := strings.TrimSuffix(u.Hostname(), ".")
	// Direct exfil: a non-allowlisted reach host with a real payload. Payload
	// is measured on the PARSED, decoded components (r16), so a `%20`-starved
	// or space-delimited tail can't drop it under the floor while the parser
	// still resolves a full path.
	if !urlHostIsAllowed(host) && hostInHeuristicReach(host) {
		payload := strings.TrimPrefix(u.Pathname(), "/") + u.Search() + u.Hash()
		if len(payload) >= 5 {
			return "non-allowlisted-url-host"
		}
	}
	// Laundering: additive, for ANY host that cleared above (closes the
	// review's MEDIUM-4 — a non-allowlisted, out-of-reach proxy laundering an
	// encoded inner URL). Operates on the FULL raw payload, not the capped
	// cand, so an inner authority past the per-candidate cap is still seen
	// (closes the review's HIGH-2). Bounded by depth and the shared budget.
	if depth > 0 {
		if f := scanNested(raw, skip, b, depth-1); f != "" {
			return f
		}
	}
	return ""
}

// scanNested decodes a candidate's payload (everything past the first
// authority terminator) one layer at a time and re-evaluates every nested
// scheme it exposes, through the same evalRawCandidate entry point. Progressive
// decoding catches double-encoded inner schemes (`https%253A…`).
//
// Two budget interactions matter (r17, closing the r16 re-review's HIGH):
//   - A payload with no `%` left cannot hide an encoded scheme, so it is fully
//     examined after this pass and returns clean WITHOUT charging the decode
//     budget. (r16 charged every payload, so a large no-`%` payload drained the
//     budget as a setup move — the exploit's "drain" candidates.)
//   - Decode-budget exhaustion fails CLOSED, not open: if the budget is gone
//     while `%` (unexamined encoded content) remains, an encoded inner scheme
//     may be lurking that we declined to decode, so we flag rather than clear.
//     r16's decode() returned the input unchanged here, which read as clean —
//     a false-ALLOW: `https://r.jina.ai/https%3A%2F%2Fevil.com/…` is invisible
//     to schemeRe until decoded, so a spent decode budget cleared a real exfil.
//     This matches the parse-budget and oversized-authority fail-closed posture.
// Reaching the depth cap with `%` still present stays clean (a documented
// semantic bound — a one-hop proxy also stops decoding — not a resource
// exhaustion we chose to abandon mid-examination).
func scanNested(raw string, skip int, b *scanBudget, depth int) string {
	i := strings.IndexAny(raw[skip:], "/\\?#")
	if i < 0 {
		return ""
	}
	decoded := raw[skip+i:]
	for d := 0; d <= depth; d++ {
		for _, loc := range schemeRe.FindAllStringIndex(decoded, -1) {
			if f := evalRawCandidate(decoded[loc[0]:], b, depth-d); f != "" {
				return f
			}
		}
		if !strings.ContainsRune(decoded, '%') {
			return "" // nothing encoded remains — fully examined
		}
		if b.decodeBytes <= 0 {
			return "scan-budget-exhausted" // encoded content unexamined — fail closed
		}
		b.decodeBytes -= len(decoded)
		decoded = percentDecodeLenient(decoded)
	}
	return ""
}

// urlCandidateNeedsParse is a PERFORMANCE gate, not a detector: it returns
// false only when NO parse outcome could flag the candidate (soundness argued
// at its call site in evalRawCandidate — it is applied to untruncated
// candidates only, where cand is the whole raw). Non-ASCII or percent-bearing
// candidates ALWAYS parse (IDNA mapping / percent-decoding can reach a TLD or
// allowlisted host that no literal byte scan would see). Without it the 150k-
// candidate linearity blob would parse every candidate; with it, benign non-
// URL text spends zero budget.
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

// percentDecodeLenient decodes %XX byte pairs and passes malformed sequences
// through unchanged (a strict decoder would let one bad escape mask a nested
// URL). Output length <= input length, so bounds carry.
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
