// Package pyurl ports CPython's urllib.parse far enough to reproduce
// `urlparse(u).hostname` exactly, for a `str` argument.
//
// THE CONTRACT IS THE EXCEPTIONS AS MUCH AS THE VALUE. The one caller in
// the Python tree is terrain.py:_host_of —
//
//	try:
//	    return (urlparse(m.group(0)).hostname or "").lower()
//	except ValueError:
//	    return ""
//
// — so "which inputs raise" decides whether a blocked host is recorded as
// terrain at all. A port that gets every hostname right and raises on a
// different set of inputs is wrong in exactly the way that goes unnoticed:
// both runtimes answer "" and only the ledger diverges.
//
// Three distinctions this file keeps that a rewrite would flatten:
//
//   - `hostname` returns None for an empty host, and None is not "".
//     Hostname's `found` bool is that None. The caller's `or ""` collapses
//     them, but the collapse belongs to the CALLER, not here.
//   - Every slice is by CODE POINT where CPython's is. Where a Go byte
//     index is provably identical (the separator is ASCII and the offset
//     is a count of ASCII characters), it is used and said so.
//   - Statement ORDER. The bracket ValueErrors are raised inside urlsplit
//     before _checknetloc runs, so a netloc that is BOTH an invalid
//     bracketed host and an NFKC violation reports the bracket message.
//
// Ported from CPython 3.14.3's Lib/urllib/parse.py (and the parts of
// Lib/ipaddress.py that _check_bracketed_host reaches through
// ipaddress.ip_address), read on this box rather than from memory.
//
// NOT PORTED, deliberately:
//
//   - `@functools.lru_cache(typed=True)` on urlsplit. A cache on a pure
//     function of its arguments has no observable behaviour; there is
//     nothing here for a port to be right or wrong about. (It is not even
//     observable through clear_cache(), which only drops entries.)
//   - urlparse's params split. `_urlparse` calls `_urlsplit` and then may
//     split `;params` off the PATH. It never touches netloc, so
//     urlparse(u).hostname == urlsplit(u).hostname for every input, and
//     the ValueErrors all come from _urlsplit. Split() below is therefore
//     the five-tuple, and Hostname reads its netloc.
//   - bytes input (SplitResultBytes / _NetlocResultMixinBytes). The caller
//     passes str.
//   - the `scheme` and `allow_fragments` arguments. The caller uses the
//     defaults, which are `""` and True; `_urlsplit` then strips and
//     scrubs the `""` (a no-op on empty) and does `bool(True)`. Both are
//     spelled out at the call site below rather than parameterised.
//   - username/password/port. `port` is the other property that raises
//     ValueError, and it raises on inputs `hostname` accepts — but nothing
//     reads it here, and a half-ported property is worse than an absent
//     one.
//
// INVALID UTF-8 IS OUT OF SCOPE. A Python `str` is a sequence of code
// points; a Go `string` is a sequence of bytes and can hold sequences no
// `str` can represent. Every rune-level operation here decodes with Go's
// usual RuneError substitution, which is A behaviour but not a MEASURED
// one — no CPython input corresponds. The differential corpus is valid
// UTF-8 throughout.
package pyurl

import (
	"fmt"
	"regexp"
	"strings"
	"unicode/utf8"

	"golang.org/x/text/unicode/norm"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
)

// ValueError is a CPython ValueError. Msg is the exception's str(), byte
// for byte, because the port's evidence that it raises for the same
// REASON is that it raises with the same words.
type ValueError struct{ Msg string }

func (e *ValueError) Error() string { return e.Msg }

func valueErr(msg string) error { return &ValueError{Msg: msg} }

// _WHATWG_C0_CONTROL_OR_SPACE, quoted from the source on this box:
//
//	'\x00\x01…\x1f '   == "".join([chr(i) for i in range(0, 0x20 + 1)])
//
// It is a CONTIGUOUS range U+0000..U+0020 inclusive — 33 code points, and
// U+0020 SPACE is in it while U+00A0 and U+2028 are not. It is not
// str.isspace()'s set (which has neither NUL nor U+001C..U+001F's
// company) and not `\s`'s, so pytext.Strip and pytext.IsSpace are the
// WRONG helpers here and are deliberately not used.
func isC0ControlOrSpace(r rune) bool { return r >= 0x00 && r <= 0x20 }

// _UNSAFE_URL_BYTES_TO_REMOVE = ['\t', '\r', '\n'] — checked in the source
// on this box. Only these three; U+000B and U+000C are NOT removed (they
// are stripped from the ends by the C0 rule above, but survive in the
// middle).
var unsafeURLBytesToRemove = []string{"\t", "\r", "\n"}

// schemeChars, quoted from the source: lowercase, uppercase, digits, and
// exactly `+`, `-`, `.`. Not `_`, not `~`.
const schemeChars = "abcdefghijklmnopqrstuvwxyz" +
	"ABCDEFGHIJKLMNOPQRSTUVWXYZ" +
	"0123456789" +
	"+-."

// partition is Python's str.partition: (head, found, tail). `found` is
// Python's truthiness test on the returned separator — the middle element
// is the separator when found and "" when not, and every call site tests
// it as a bool.
func partition(s, sep string) (string, bool, string) {
	i := strings.Index(s, sep)
	if i < 0 {
		return s, false, ""
	}
	return s[:i], true, s[i+len(sep):]
}

// rpartition is Python's str.rpartition. Note the ASYMMETRY with
// partition when the separator is absent: rpartition puts the whole
// string in the TAIL ("", False, s), partition puts it in the head. Both
// _hostinfo and _check_bracketed_netloc read only [2], so getting this
// backwards would make every netloc without userinfo parse as empty.
func rpartition(s, sep string) (string, bool, string) {
	i := strings.LastIndex(s, sep)
	if i < 0 {
		return "", false, s
	}
	return s[:i], true, s[i+len(sep):]
}

// isASCII is Python's str.isascii(): true for the empty string, and for
// every string whose code points are all < 128. On a Go string this is
// exactly "no byte has the high bit set" — a byte >= 0x80 is either part
// of a multi-byte rune (non-ASCII) or invalid UTF-8 (not representable as
// a str at all, see the package doc).
func isASCII(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] >= 0x80 {
			return false
		}
	}
	return true
}

// SplitResult is CPython's SplitResult, with the None-ness kept.
//
// _urlsplit initialises netloc, query and fragment to None and only
// assigns them on the branches that find one, so "no netloc" and "empty
// netloc" (`http://?x`) are different results. urlparse's ParseResult
// constructor then collapses them with `netloc or ""` before `hostname`
// ever sees the value — which is why Hostname can ignore NetlocSet, and
// why this type keeps it anyway: the collapse is one line at one call
// site, and hiding it inside the splitter is how a later reader stops
// being able to tell that _urlsplit ever knew.
type SplitResult struct {
	// Scheme has NO None. This was a port bug the differential caught:
	// the field started out as `Scheme string; SchemeSet bool` by
	// symmetry with the other three, and CPython disagreed on 21 cases.
	//
	// `_urlsplit(url, scheme=None, …)` has None as its DEFAULT, but the
	// only two callers are `urlsplit` and `_urlparse`, and both pass the
	// PUBLIC default, which is `''`. The local is then reassigned only
	// when a scheme is found. So for every input reachable from
	// urlparse(u) the scheme is a str — `''` when there is none — and
	// modelling it as Optional invents a distinction CPython does not
	// make here. The differential asserts CPython never answers None,
	// so a future signature change fails loudly instead of quietly
	// re-opening the question.
	Scheme string

	Netloc    string
	NetlocSet bool // false is Python's None

	Path string // CPython calls this `url`; it is never None

	Query    string
	QuerySet bool // false is Python's None

	Fragment    string
	FragmentSet bool // false is Python's None
}

// Split is CPython's `urlsplit(url)` — i.e. `_urlsplit(url, "", True)`
// with the SplitResult constructor's `or ""` collapses NOT applied, so
// the caller can still see the Nones.
//
// It returns every ValueError CPython raises, with the same message.
func Split(url string) (SplitResult, error) {
	var res SplitResult

	// Only lstrip url as some applications rely on preserving trailing
	// space. LSTRIP, not strip — a trailing C0/space character survives
	// into the netloc when nothing in /?# follows it, and _hostinfo does
	// not strip it either. Measured on this box:
	//
	//	urlparse('http://a ').hostname     == 'a '
	//	urlparse('http://a\x00').hostname  == 'a\x00'
	//
	// Both are fixtures.
	url = strings.TrimLeftFunc(url, isC0ControlOrSpace)
	for _, b := range unsafeURLBytesToRemove {
		url = strings.ReplaceAll(url, b, "")
	}
	// `if scheme is not None:` — the default scheme is '' and not None,
	// so the strip and the three replaces DO run, on the empty string,
	// and are no-ops. Spelled out here because the branch existing is
	// what makes `urlsplit(u, scheme='\thttp')` behave; it is dead for
	// this caller and left as a comment rather than as dead code.

	// allow_fragments = bool(allow_fragments) — True.

	// netloc = query = fragment = None
	// (res's zero value already says None for all three.)

	i := strings.Index(url, ":")
	// `i > 0 and url[0].isascii() and url[0].isalpha()`.
	//
	// The byte index is safe: ':' is ASCII, so strings.Index finds the
	// same separator str.find does, and `i > 0` is the same question as
	// "the code-point index is > 0" — a byte offset is 0 iff the code
	// point offset is. url[:i] and url[i+1:] then slice at that ASCII
	// boundary, which is a rune boundary.
	//
	// `url[0]` would IndexError on an empty url; Python's `and` short
	// circuits on `i > 0` first (find returns -1), which is why this is
	// safe and not merely lucky.
	if i > 0 {
		r, _ := utf8.DecodeRuneInString(url)
		// isascii() AND isalpha(). str.isalpha() is Unicode-aware, but
		// the isascii() conjunct makes every non-ASCII letter
		// unreachable, so this collapses to the ASCII letters exactly.
		// (This is a COLLAPSE, not a simplification: pytext has no
		// IsAlpha, and adding a Unicode one to feed a test that can
		// never see a Unicode letter would be the fabrication.)
		if r < 0x80 && ((r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z')) {
			// Python's for/else: the scheme is taken only if NO
			// character breaks out, i.e. every character is legal.
			legal := true
			for _, c := range url[:i] {
				if !strings.ContainsRune(schemeChars, c) {
					legal = false
					break
				}
			}
			if legal {
				res.Scheme = pytext.Lower(url[:i])
				url = url[i+1:]
			}
		}
	}

	// `url[:2] == '//'`. A Go byte prefix is the same test: '/' is ASCII,
	// so the first two BYTES being "//" is the first two CODE POINTS
	// being "//", and a url shorter than two bytes cannot match either
	// (Python's slice just returns what there is, and neither ""
	// nor "/" equals "//").
	if strings.HasPrefix(url, "//") {
		var netloc string
		netloc, url = splitNetloc(url, 2)
		res.Netloc, res.NetlocSet = netloc, true
		// The unbalanced-bracket check. Both directions, and it fires
		// on brackets ANYWHERE in the netloc including inside userinfo.
		if (strings.Contains(netloc, "[") && !strings.Contains(netloc, "]")) ||
			(strings.Contains(netloc, "]") && !strings.Contains(netloc, "[")) {
			return SplitResult{}, valueErr("Invalid IPv6 URL")
		}
		if strings.Contains(netloc, "[") && strings.Contains(netloc, "]") {
			if err := checkBracketedNetloc(netloc); err != nil {
				return SplitResult{}, err
			}
		}
	}
	// allow_fragments is True, so the `and` reduces to the `'#' in url`.
	if strings.Contains(url, "#") {
		var frag string
		url, frag = cut1(url, "#")
		res.Fragment, res.FragmentSet = frag, true
	}
	if strings.Contains(url, "?") {
		var q string
		url, q = cut1(url, "?")
		res.Query, res.QuerySet = q, true
	}
	res.Path = url
	// LAST, after the bracket checks — see the package doc on ordering.
	if err := checkNetloc(res.Netloc, res.NetlocSet); err != nil {
		return SplitResult{}, err
	}
	return res, nil
}

// cut1 is Python's `s.split(sep, 1)` for a separator known to be present:
// two pieces, split at the FIRST occurrence.
func cut1(s, sep string) (string, string) {
	i := strings.Index(s, sep)
	return s[:i], s[i+len(sep):]
}

// splitNetloc is _splitnetloc(url, start): the netloc runs to the EARLIEST
// of '/', '?' and '#' at or after start, or to the end.
//
// CPython finds each delimiter separately and takes the min, and its
// comment says "the order is NOT important" — which is true of the loop
// order and false of the min: taking the LAST would swallow a query into
// the netloc. start is a code-point offset in CPython; the only call
// passes 2, immediately after a verified "//" of two ASCII characters, so
// the byte offset is the same 2.
func splitNetloc(url string, start int) (string, string) {
	delim := len(url) // default is end
	for _, c := range "/?#" {
		wdelim := strings.IndexRune(url[start:], c)
		if wdelim >= 0 {
			wdelim += start
			if wdelim < delim {
				delim = wdelim
			}
		}
	}
	return url[start:delim], url[delim:]
}

// checkBracketedNetloc is _check_bracketed_netloc. Its own comment: "this
// function must mirror the splitting done in NetlocResultMixins._hostinfo()".
// It does not, quite — _hostinfo does not care what precedes the '[' and
// does not care what follows the ']' — and the two places it differs are
// where the ValueErrors live.
func checkBracketedNetloc(netloc string) error {
	_, _, hostnameAndPort := rpartition(netloc, "@")
	beforeBracket, haveOpenBr, bracketed := partition(hostnameAndPort, "[")
	var hostname string
	if haveOpenBr {
		// No data is allowed before a bracket.
		if beforeBracket != "" {
			return valueErr("Invalid IPv6 URL")
		}
		var port string
		hostname, _, port = partition(bracketed, "]")
		// No data is allowed after the bracket but before the port
		// delimiter. Note `port and not port.startswith(":")`: an EMPTY
		// tail is fine (there was no ']' at all, or nothing after it),
		// and a tail starting with ':' is fine however malformed the
		// rest is.
		if port != "" && !strings.HasPrefix(port, ":") {
			return valueErr("Invalid IPv6 URL")
		}
	} else {
		// Reachable: the caller only checked that '[' and ']' are
		// somewhere in the NETLOC, and rpartition('@') may have thrown
		// them away with the userinfo. `user[x]@example.com` lands here
		// and puts "example.com" through the IP check below.
		hostname, _, _ = partition(hostnameAndPort, ":")
	}
	return checkBracketedHost(hostname)
}

// ipvFutureRe is CPython's r"\Av[a-fA-F0-9]+\..+\z", transcribed
// unchanged. Both engines read `.` as "any code point except \n", `\A` as
// start-of-text and `\z`/`\Z` as end-of-text, and the class is explicit
// ASCII — so there is no Python-vs-Go escape divergence here and no
// pytext class to substitute. (`\n` cannot reach this anyway: urlsplit
// deletes every "\n" from the url before the netloc is cut.)
var ipvFutureRe = regexp.MustCompile(`\Av[a-fA-F0-9]+\..+\z`)

// checkBracketedHost is _check_bracketed_host, whose comment cites
// RFC 3986 p.49 and the WHATWG URL spec.
func checkBracketedHost(hostname string) error {
	// startswith('v') — lowercase only. "V1.x" goes down the ip_address
	// branch and fails there with the generic message instead.
	if strings.HasPrefix(hostname, "v") {
		if !ipvFutureRe.MatchString(hostname) {
			// f-string with no placeholders in the source; the message
			// is this literal.
			return valueErr("IPvFuture address is invalid")
		}
		return nil
	}
	// ipaddress.ip_address(hostname): IPv4Address first, then
	// IPv6Address, and both AddressValueError paths are SWALLOWED — so
	// every one of the ~20 distinct messages inside ipaddress.py is
	// unobservable here, and all this port needs from that module is two
	// predicates plus the one generic message.
	if isIPv4Address(hostname) {
		return valueErr("An IPv4 address cannot be in brackets")
	}
	if isIPv6Address(hostname) {
		return nil
	}
	return valueErr(fmt.Sprintf(
		"%s does not appear to be an IPv4 or IPv6 address", pytext.Repr(hostname)))
}

// checkNetloc is _checknetloc. netlocSet is Python's "netloc is not None";
// `not netloc` is true for both None and "".
func checkNetloc(netloc string, netlocSet bool) error {
	if !netlocSet || netloc == "" || isASCII(netloc) {
		return nil
	}
	// looking for characters like ℀ that expand to 'a/c'.
	// IDNA uses NFKC equivalence, so normalize for this check.
	n := strings.ReplaceAll(netloc, "@", "") // ignore characters already included
	n = strings.ReplaceAll(n, ":", "")       // but not the surrounding text
	// These two are DEAD, here and in CPython: urlsplit removes the
	// fragment and the query before it looks for a netloc, so neither
	// character can be in one. Kept because the transcription is the spec,
	// and mutation-proof-of-deadness lives in
	// TestNoNetlocCanCarryAFragmentOrQueryCharacter, which measures the
	// premise instead of asserting it (L8).
	n = strings.ReplaceAll(n, "#", "")
	n = strings.ReplaceAll(n, "?", "")
	netloc2 := norm.NFKC.String(n)
	if n == netloc2 {
		return nil
	}
	for _, c := range "/?#@:" {
		if strings.ContainsRune(netloc2, c) {
			// One message for all five characters; CPython builds it by
			// concatenation and it carries the ORIGINAL netloc, not the
			// stripped `n` and not `netloc2`.
			return valueErr("netloc '" + netloc + "' contains invalid " +
				"characters under NFKC normalization")
		}
	}
	return nil
}

// hostinfo is _NetlocResultMixinStr._hostinfo, returning (hostname, port,
// portSet). portSet false is Python's None — `if not port: port = None`
// maps BOTH "no colon" and "colon with nothing after it" to None.
func hostinfo(netloc string) (string, string, bool) {
	_, _, hi := rpartition(netloc, "@")
	_, haveOpenBr, bracketed := partition(hi, "[")
	var hostname, port string
	if haveOpenBr {
		hostname, _, port = partition(bracketed, "]")
		// The tail after ']' is re-partitioned on ':' and only the part
		// AFTER the colon is kept, so junk between ']' and ':' is
		// silently dropped here — _check_bracketed_netloc is what
		// refuses it, and only when the netloc had both brackets.
		_, _, port = partition(port, ":")
	} else {
		hostname, _, port = partition(hi, ":")
	}
	if port == "" {
		return hostname, "", false
	}
	return hostname, port, true
}

// Hostname is Python `urlparse(u).hostname`.
//
// found=false is Python's None, which the property returns for an EMPTY
// host — `if not hostname: return None`. err is non-nil exactly when
// CPython raises ValueError, carrying the same message text.
func Hostname(u string) (host string, found bool, err error) {
	res, err := Split(u)
	if err != nil {
		return "", false, err
	}
	// urlparse builds ParseResult(scheme or "", netloc or "", …), so the
	// property reads '' where _urlsplit said None. res.Netloc is already
	// "" in that case; NetlocSet is not consulted, and that is the
	// collapse, not an oversight.
	hostname, _, _ := hostinfo(res.Netloc)
	if hostname == "" {
		return "", false, nil
	}
	// Scoped IPv6 address may have zone info, which must not be
	// lowercased like http://[fe80::822a:a8ff:fe49:470c%tESt]:1234/keys.
	// partition, so the FIRST '%' splits and any later '%' stays in the
	// zone; and the separator is re-emitted, so a trailing '%' with an
	// empty zone survives as a trailing '%'.
	before, percent, zone := partition(hostname, "%")
	sep := ""
	if percent {
		sep = "%"
	}
	// pytext.Lower, not strings.ToLower: str.lower() is full (not
	// simple) case mapping, so U+0130 lowers to two code points and
	// final sigma is contextual. A host is not obviously a place that
	// happens, which is exactly why it is not worth being wrong about.
	return pytext.Lower(before) + sep + zone, true, nil
}

// ---------------------------------------------------------------------
// ipaddress.py, only as far as ip_address's two boolean questions.
//
// ip_address's contract:
//
//	try: return IPv4Address(address)
//	except (AddressValueError, NetmaskValueError): pass
//	try: return IPv6Address(address)
//	except (AddressValueError, NetmaskValueError): pass
//	raise ValueError(f'{address!r} does not appear to be …')
//
// AddressValueError subclasses ValueError, so every specific message
// inside those constructors is caught and discarded. Only VALIDITY
// escapes. NetmaskValueError cannot arise from a plain Address
// constructor at all.
// ---------------------------------------------------------------------

// isIPv4Address is `IPv4Address(s)` succeeding, for a str s.
func isIPv4Address(s string) bool {
	_, ok := ipv4IntFromString(s)
	return ok
}

// ipv4IntFromString is IPv4Address.__init__'s string path plus
// _BaseV4._ip_int_from_string, returning the integer the IPv6 parser
// needs from an embedded dotted-quad suffix.
func ipv4IntFromString(s string) (uint32, bool) {
	// `if '/' in addr_str: raise AddressValueError(f"Unexpected '/' …")`
	if strings.Contains(s, "/") {
		return 0, false
	}
	if s == "" { // 'Address cannot be empty'
		return 0, false
	}
	octets := strings.Split(s, ".")
	if len(octets) != 4 { // "Expected 4 octets in %r"
		return 0, false
	}
	var v uint32
	for _, o := range octets {
		n, ok := parseOctet(o)
		if !ok {
			return 0, false
		}
		v = v<<8 | uint32(n)
	}
	return v, true
}

// parseOctet is _BaseV4._parse_octet. The four rejections, in CPython's
// order (the order is a comment in the source — "we do the length check
// second, since the invalid character error is likely to be more
// informative" — and is unobservable here because every rejection is
// swallowed; it is kept so the two files read alike).
func parseOctet(o string) (int, bool) {
	if o == "" { // "Empty octet not permitted"
		return 0, false
	}
	// `octet_str.isascii() and octet_str.isdigit()`. str.isdigit() is
	// Unicode-aware and true for e.g. U+00B2 and U+0660, but the
	// isascii() conjunct makes every one of those unreachable, so the
	// pair collapses to "every character is an ASCII 0-9". pytext's
	// digit helpers answer a DIFFERENT question (the decimal VALUE fold)
	// and would be the wrong tool even if the conjunct were absent.
	for i := 0; i < len(o); i++ {
		if o[i] < '0' || o[i] > '9' {
			return 0, false
		}
	}
	if len(o) > 3 { // "At most 3 characters permitted in %r"
		// len() is code points in CPython; every character here is an
		// ASCII digit by the loop above, so bytes and code points agree.
		return 0, false
	}
	// Handle leading zeros as strict as glibc's inet_pton() (bpo-36384).
	// "0" itself is allowed; "00" and "01" are not.
	if o != "0" && o[0] == '0' {
		return 0, false
	}
	n := 0
	for i := 0; i < len(o); i++ {
		n = n*10 + int(o[i]-'0')
	}
	if n > 255 { // "Octet %d (> 255) not permitted"
		return 0, false
	}
	return n, true
}

// hexDigits is _BaseV6._HEX_DIGITS.
const hexDigits = "0123456789ABCDEFabcdef"

// isIPv6Address is `IPv6Address(s)` succeeding, for a str s.
func isIPv6Address(s string) bool {
	// `if '/' in addr_str: raise AddressValueError(f"Unexpected '/' …")`
	// — checked BEFORE the scope id is split off, so "fe80::1%eth/0" is
	// rejected here rather than becoming a scope id containing '/'.
	if strings.Contains(s, "/") {
		return false
	}
	// _split_scope_id: partition on the FIRST '%'. A separator with an
	// empty scope id, or a scope id containing another '%', is invalid.
	// No '%' at all is fine (scope_id = None).
	addr, sep, scopeID := partition(s, "%")
	if sep {
		if scopeID == "" || strings.Contains(scopeID, "%") {
			return false
		}
	}
	return ipv6IntFromStringOK(addr)
}

// ipv6IntFromStringOK is _BaseV6._ip_int_from_string, reduced to its
// success/failure answer. The arithmetic is dropped (nothing reads the
// integer); every CHECK is kept, in order, because each one is a
// different set of strings.
func ipv6IntFromStringOK(s string) bool {
	if s == "" { // 'Address cannot be empty'
		return false
	}
	// "At most 45 characters expected in %r" — len() is CODE POINTS.
	if utf8.RuneCountInString(s) > 45 {
		return false
	}
	// _max_parts = _HEXTET_COUNT + 1 = 9; split(':', maxsplit=9) yields
	// at most TEN parts, the tenth carrying every remaining colon. The
	// deliberate over-split is so that "too many parts combined with ::"
	// reports the right error; here it decides whether the >9 check
	// below fires.
	parts := strings.SplitN(s, ":", 10)
	// An IPv6 address needs at least 2 colons (3 parts).
	if len(parts) < 3 { // "At least 3 parts expected in %r"
		return false
	}
	// If the address has an IPv4-style suffix, convert it to hexadecimal.
	// The test is '.' ANYWHERE in the last part, not "the last part is a
	// dotted quad" — "::x.y" takes this branch and then fails as IPv4.
	if strings.Contains(parts[len(parts)-1], ".") {
		last := parts[len(parts)-1]
		parts = parts[:len(parts)-1] // parts.pop()
		v, ok := ipv4IntFromString(last)
		if !ok {
			return false
		}
		// Net effect on the count is +1: one part removed, two added.
		parts = append(parts,
			fmt.Sprintf("%x", (v>>16)&0xFFFF),
			fmt.Sprintf("%x", v&0xFFFF))
	}
	// An IPv6 address can't have more than 8 colons (9 parts).
	if len(parts) > 9 { // "At most 8 colons permitted in %r"
		return false
	}
	// Disregarding the endpoints, find '::' with nothing in between.
	skipIndex := -1
	for i := 1; i < len(parts)-1; i++ {
		if parts[i] == "" {
			if skipIndex != -1 { // "At most one '::' permitted in %r"
				return false
			}
			skipIndex = i
		}
	}
	var partsHi, partsLo int
	if skipIndex != -1 {
		partsHi = skipIndex
		partsLo = len(parts) - skipIndex - 1
		if parts[0] == "" {
			partsHi--
			if partsHi != 0 { // "^: requires ^::"
				return false
			}
		}
		if parts[len(parts)-1] == "" {
			partsLo--
			if partsLo != 0 { // ":$ requires ::$"
				return false
			}
		}
		partsSkipped := 8 - (partsHi + partsLo)
		if partsSkipped < 1 { // "Expected at most 7 other parts with '::'"
			return false
		}
	} else {
		if len(parts) != 8 { // "Exactly 8 parts expected without '::'"
			return false
		}
		if parts[0] == "" { // "^: requires ^::"
			return false
		}
		if parts[len(parts)-1] == "" { // ":$ requires ::$"
			return false
		}
		partsHi = len(parts)
		partsLo = 0
	}
	// range(parts_hi) then range(-parts_lo, 0): the first parts_hi
	// entries and the LAST parts_lo entries. The middle is skipped, so a
	// '::' can hide nothing — there is nothing between the two runs.
	for i := 0; i < partsHi; i++ {
		if !parseHextetOK(parts[i]) {
			return false
		}
	}
	for i := len(parts) - partsLo; i < len(parts); i++ {
		if !parseHextetOK(parts[i]) {
			return false
		}
	}
	return true
}

// parseHextetOK is _BaseV6._parse_hextet.
//
// The EMPTY hextet is the subtle one. CPython's two explicit checks both
// PASS on "": a frozenset is a superset of the empty string's characters,
// and len("") is not > 4. It fails on `int("", 16)`, whose ValueError the
// caller converts to AddressValueError. So "" is invalid, but only via
// the conversion — and the empty parts that a '::' produces never reach
// here, because they are inside the skipped middle.
func parseHextetOK(h string) bool {
	if h == "" {
		return false // int('', 16) -> ValueError
	}
	// `_HEX_DIGITS.issuperset(hextet_str)` — a set of 22 ASCII
	// characters, so any non-ASCII rune fails. Iterating BYTES is
	// equivalent: a multi-byte rune's bytes are all >= 0x80 and none of
	// them is in the set.
	for i := 0; i < len(h); i++ {
		if !strings.ContainsRune(hexDigits, rune(h[i])) {
			return false
		}
	}
	if len(h) > 4 { // code points == bytes, all ASCII by the loop above
		return false
	}
	// "Length check means we can skip checking the integer value" — four
	// hex digits cannot exceed 0xFFFF.
	return true
}
