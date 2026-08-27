package pyurl

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The CPython differential for the whole package.
//
// urlparse(u).hostname is a THREE-VALUED answer — a ValueError with a
// message, or None, or a string — and a corpus that reaches only one of
// them cannot tell a working port from `return "", false, nil`. The
// floors at the bottom of TestHostnameMatchesCPython are what keeps this
// from happening quietly; every one of them is counted from what CPYTHON
// answered, never from what the fixture author expected.
//
// The expected values are NEVER written down here. There is one place in
// this file where a claim about CPython appears (the `note` field), and
// it is prose for the reader, not an assertion.
//
// The probe asks urllib.parse three questions per URL:
//
//   - `urlparse(u).hostname`, which IS the contract;
//   - the five components from `_urlsplit(u, "", True)`, which is the
//     PRIVATE function and is deliberately what the probe calls: it is
//     the only way to see the None-vs-empty-string distinction that SplitResult's
//     constructor collapses, and Split() claims to reproduce it. If a
//     future CPython renames it the probe FAILS rather than skips, which
//     is pyprobe's whole design point;
//   - which ValueError, if any, comes out first.
//
// urllib.parse is stdlib, so the probe sets Stdlib and takes no marker
// and no workspace: it imports nothing from the repo and writes nothing.
const pyURLProbe = `
import json, sys
import urllib.parse as p

out = []
for u in json.loads(sys.argv[1]):
    try:
        # urlparse first: it is the caller's actual expression, and
        # asking it before _urlsplit is what makes "urlparse and urlsplit
        # raise on the same inputs" a measured claim rather than an
        # assumption inherited from reading the source.
        h = p.urlparse(u).hostname
        s, n, path, q, f = p._urlsplit(u, '', True)
    except ValueError as e:
        out.append({"k": "raise", "msg": str(e)})
        continue
    out.append({"k": "ok", "host": h, "scheme": s, "netloc": n,
                "path": path, "query": q, "fragment": f})
print(json.dumps(out))
`

// pyAnswer is one row of the probe's output. The pointers are Python's
// Nones; a nil pointer and a pointer to "" are different answers and the
// whole port turns on keeping them apart.
type pyAnswer struct {
	K        string  `json:"k"`
	Msg      string  `json:"msg"`
	Host     *string `json:"host"`
	Scheme   *string `json:"scheme"`
	Netloc   *string `json:"netloc"`
	Path     string  `json:"path"`
	Query    *string `json:"query"`
	Fragment *string `json:"fragment"`
}

// ucase is one corpus entry. `note` is a comment, not an expectation —
// nothing in the test reads it. It exists because a URL like
// "http://[::1]]/" is unreadable without one, and a fixture nobody can
// read is a fixture nobody notices has stopped exercising its branch.
type ucase struct {
	u    string
	note string
}

// Characters built from code points rather than typed as literals, for
// the same reason internal/provenance does it: a fixture whose subject is
// WHICH invisible character it carries cannot be reviewed otherwise.
var (
	nul     = string(rune(0x0000)) // NULL — in the C0-or-space strip set
	sohChr  = string(rune(0x0001)) // START OF HEADING — likewise
	tabChr  = string(rune(0x0009)) // TAB — stripped AND removed everywhere
	lfChr   = string(rune(0x000A)) // LINE FEED — likewise
	vtChr   = string(rune(0x000B)) // LINE TABULATION — stripped, NOT removed
	ffChr   = string(rune(0x000C)) // FORM FEED — stripped, NOT removed
	crChr   = string(rune(0x000D)) // CARRIAGE RETURN — stripped AND removed
	usChr   = string(rune(0x001F)) // UNIT SEPARATOR — last C0 in the set
	spChr   = string(rune(0x0020)) // SPACE — the last member of the set
	delChr  = string(rune(0x007F)) // DELETE — NOT in the set (0x20 is the top)
	nbspChr = string(rune(0x00A0)) // NO-BREAK SPACE — not in the set either
	acctOf  = string(rune(0x2100)) // ℀ ACCOUNT OF — NFKC-expands to "a/c"
	careOf  = string(rune(0x2105)) // ℅ CARE OF — expands to "c/o"
	ffQuest = string(rune(0xFF1F)) // ？FULLWIDTH QUESTION MARK — NFKC '?'
	ffHash  = string(rune(0xFF03)) // ＃FULLWIDTH NUMBER SIGN — NFKC '#'
	ffAt    = string(rune(0xFF20)) // ＠FULLWIDTH COMMERCIAL AT — NFKC '@'
	ffColon = string(rune(0xFF1A)) // ：FULLWIDTH COLON — NFKC ':'
	ffE     = string(rune(0xFF25)) // Ｅ FULLWIDTH LATIN CAPITAL E — NFKC 'E'
	dotI    = string(rune(0x0130)) // İ — str.lower() gives TWO code points
	sigma   = string(rune(0x03A3)) // Σ — lowers to ς word-finally, σ else
	eacute  = string(rune(0x00E9)) // é precomposed — NFKC-stable
	combAcc = string(rune(0x0301)) // COMBINING ACUTE — "e"+this is not stable
	auml    = string(rune(0x00C4)) // Ä
	// The NFKC table skew itself, as a fixture. CPython ships Unicode
	// 16.0.0 and golang.org/x/text ships 15.0.0, and the whole of their
	// disagreement is the Outlined block U+1CCD6..U+1CCF9 -- 36 code
	// points that NFKC to a plain ASCII letter or digit in CPython and to
	// themselves in Go (nfkc_skew_test.go measures and pins this).
	//
	// Neither normalizes to one of `/?#@:`, so the netloc gate cannot see
	// the difference -- which is the CLAIM, and these two rows are what
	// make it a measurement. If the skew ever grows a gate character, the
	// pin fails there and these rows change answer here.
	outlA = string(rune(0x1CCD6)) // OUTLINED LATIN CAPITAL LETTER A
	outl9 = string(rune(0x1CCF9)) // OUTLINED DIGIT NINE — the block's end
)

// corpus. Organised so that every exotic fixture sits next to the
// ORDINARY one it is exotic relative to — a bracket error next to the
// bracket that works, a stripped control character next to the same URL
// without it. A finding that fires on both halves of a pair is a broken
// harness; a finding that fires on one is a real difference.
var corpus = []ucase{
	// --- 1. The hot path: what terrain.py's regex actually produces ---
	{"http://example.com", "the control for everything below"},
	{"https://example.com", ""},
	{"http://example.com/", ""},
	{"https://example.com/path", ""},
	{"https://example.com/path?a=1", ""},
	{"https://example.com/path?a=1#frag", ""},
	{"https://example.com/a/b/c.html", ""},
	{"http://example.com:8080/x", "port"},
	{"https://example.com:443", "port, no path"},
	{"http://sub.domain.example.co.uk/x", ""},
	{"https://api.github.com/repos/x/y", ""},
	{"http://127.0.0.1:5000/", "dotted quad, unbracketed"},
	{"http://localhost", ""},
	{"http://localhost:8000/", ""},
	{"https://example.com?", "empty query"},
	{"https://example.com#", "empty fragment"},

	// --- 2. Case folding of the host ---
	{"http://EXAMPLE.COM/", "ASCII upper -> lower"},
	{"http://example.com/", "its control"},
	{"HTTP://EXAMPLE.COM/", "the scheme lowers too"},
	{"HtTp://ExAmPlE.cOm/PATH", "path case is NOT touched"},
	{"http://" + dotI + ".com/", "str.lower() emits i + COMBINING DOT ABOVE"},
	{"http://i.com/", "its control"},
	{"http://" + sigma + sigma + ".com/", "final sigma is contextual"},
	{"http://" + strings.ToLower(sigma) + strings.ToLower(sigma) + ".com/", "control"},
	{"http://" + auml + "BC.com/", "non-ASCII upper"},
	{"http://" + ffE + ffE + ".com/", "fullwidth capitals lower to fullwidth"},

	// --- 3. Leading C0-or-space strip (lstrip, NOT strip) ---
	{spChr + "http://example.com/", ""},
	{spChr + spChr + spChr + "http://example.com/", ""},
	{nul + "http://example.com/", ""},
	{sohChr + "http://example.com/", ""},
	{usChr + "http://example.com/", "U+001F, the last C0"},
	{vtChr + "http://example.com/", "U+000B is in the strip set"},
	{ffChr + "http://example.com/", "U+000C is in the strip set"},
	{tabChr + "http://example.com/", ""},
	{lfChr + "http://example.com/", ""},
	{crChr + "http://example.com/", ""},
	{spChr + nul + vtChr + tabChr + "http://example.com/", "mixed prefix"},
	{delChr + "http://example.com/", "U+007F is NOT in the set — no scheme"},
	{nbspChr + "http://example.com/", "U+00A0 is NOT in the set — no scheme"},
	{"http://example.com/" + spChr, "TRAILING is not stripped"},
	{"http://example.com" + spChr, "trailing space lands IN the netloc"},
	{"http://example.com" + nul, "trailing NUL lands in the netloc"},
	{"http://example.com" + vtChr, "so does a vertical tab"},
	{"http://example.com" + delChr, "and a DEL"},

	// --- 4. \t \r \n removed from the WHOLE url, not just the ends ---
	{"http://exa" + tabChr + "mple.com/", "tab in the middle of the host"},
	{"http://exa" + lfChr + "mple.com/", "newline likewise"},
	{"http://exa" + crChr + "mple.com/", "carriage return likewise"},
	{"http://exa" + vtChr + "mple.com/", "U+000B is NOT removed — it stays"},
	{"http://exa" + ffChr + "mple.com/", "nor is U+000C"},
	{"http://example.com/", "the control for all five"},
	{"ht" + tabChr + "tp://example.com/", "removal happens BEFORE scheme parse"},
	{"http" + tabChr + "://example.com/", ""},
	{"http:" + lfChr + "//example.com/", ""},
	{"http" + vtChr + "://example.com/", "U+000B survives -> illegal scheme char"},
	{"http://example.com/pa" + tabChr + "th", "removed from the path too"},

	// --- 5. Scheme parsing ---
	{"example.com/path", "no scheme, no // -> no netloc"},
	{"a://example.com/", "one-letter scheme"},
	{"h+t-t.p://example.com/", "+ - . are legal scheme characters"},
	{"h_t://example.com/", "_ is NOT — the whole scheme is refused"},
	{"h~t://example.com/", "nor is ~"},
	{"h t://example.com/", "nor is a space"},
	{"1http://example.com/", "must start with a LETTER"},
	{"+http://example.com/", "not a letter"},
	{"-http://example.com/", "not a letter"},
	{".http://example.com/", "not a letter"},
	{":http://example.com/", "colon at 0 — i > 0 fails"},
	{"://example.com/", "same"},
	{"http://example.com/", "the control"},
	{"HTTP://example.com/", "uppercase scheme is legal"},
	{"httpS://example.com/", "mixed"},
	{"h1://example.com/", "digits are legal after the first letter"},
	{"mailto:user@example.com", "scheme, but no // -> no netloc"},
	{"file:///etc/passwd", "empty netloc"},
	{"ftp://ftp.example.com/pub", ""},
	{"//example.com/x", "scheme-relative: netloc without a scheme"},
	{"//EXAMPLE.com/x", "and it still lowers"},
	{"/example.com/x", "single slash is a path"},
	{"///example.com/x", "empty netloc, path /example.com/x"},
	{"", "empty string"},
	{"/", ""},
	{"//", "netloc is the empty string, not None"},
	{"http:", "scheme only"},
	{"http:/", ""},
	{"http://", "netloc is '' -> hostname is None"},
	{"http:///", ""},
	{"http:example.com", "no // -> no netloc"},

	// --- 6. _splitnetloc: the FIRST of / ? # ends the netloc ---
	{"http://example.com/a?b#c", "all three, / first"},
	{"http://example.com?a/b#c", "? first"},
	{"http://example.com#a/b?c", "# first"},
	{"http://exam?ple.com/", "the netloc is 'exam'"},
	{"http://exam#ple.com/", "the netloc is 'exam'"},
	{"http://exam/ple.com/", "the netloc is 'exam'"},
	{"http://?x", "netloc is '' -> None"},
	{"http://#x", "netloc is '' -> None"},
	{"http:///x", "netloc is '' -> None"},
	{"http://example.com", "no delimiter at all"},
	{"http://a?b?c", ""},
	{"http://a#b#c", ""},
	{"http://a/b/c", ""},

	// --- 7. userinfo: rpartition('@'), so the LAST @ wins ---
	{"http://user@example.com/", ""},
	{"http://user:pass@example.com/", ""},
	{"http://user:pass@example.com:8080/", "userinfo AND port"},
	{"http://a@b@example.com/", "two @ — the last one splits"},
	{"http://a@b@c@example.com/", "three"},
	{"http://@example.com/", "empty userinfo"},
	{"http://user@/", "empty host -> None"},
	{"http://user@:8080/", "empty host with a port -> None"},
	{"http://example.com/", "the control"},
	{"http://us%40er@example.com/", "%-encoded @ is not an @"},
	{"http://user@EXAMPLE.COM/", "userinfo does not stop the lowering"},
	{"http://USER@example.com/", "the userinfo case is discarded, not lowered"},

	// --- 8. ports ---
	{"http://example.com:8080/", ""},
	{"http://example.com:/", "empty port"},
	{"http://example.com:", ""},
	{"http://example.com:0/", ""},
	{"http://example.com:99999/", "out of range, but hostname does not care"},
	{"http://example.com:abc/", "not a number, but hostname does not care"},
	{"http://example.com:80:90/", "partition -> the first colon wins"},
	{"http://:8080/", "no host at all -> None"},
	{"http://:/", ""},
	{"http://example.com/", "the control"},

	// --- 9. The '%' zone id: only the part BEFORE it is lowered ---
	{"http://[fe80::822a:a8ff:fe49:470c%tESt]:1234/keys", "the docstring's own example"},
	{"http://[FE80::1%tESt]/", "the address lowers, the zone does not"},
	{"http://[fe80::1%25eth0]/", "%25 is the encoded form and is just a zone"},
	{"http://[fe80::1%eth0]/", ""},
	{"http://[fe80::1]/", "the control — no zone"},
	{"http://%zz/", "an unbracketed host that is ALL zone"},
	{"http://A%B/", "'A' lowers, 'B' does not"},
	{"http://A%/", "trailing % with an empty zone"},
	{"http://%/", "the host is just '%' — truthy, so not None"},
	{"http://A%B%C/", "the FIRST % splits; the rest stays in the zone"},
	{"http://ab/", "the control"},

	// --- 10. Unbalanced brackets -> "Invalid IPv6 URL" ---
	{"http://[::1]/", "the control: this one is fine"},
	{"http://[::1", "'[' with no ']'"},
	{"http://::1]/", "']' with no '['"},
	{"http://[/", ""},
	{"http://]/", ""},
	{"http://a[b/", "the bracket does not have to be at the start"},
	{"http://a]b/", ""},
	{"http://user[@example.com/", "in the userinfo — still unbalanced"},
	{"http://user]@example.com/", ""},
	{"http://[::1/]/", "the '/' ends the netloc first -> unbalanced"},
	{"http://[::1?]/", "so does the '?'"},
	{"http://[::1#]/", "and the '#'"},

	// --- 11. _check_bracketed_netloc: data around the brackets ---
	{"http://a[::1]/", "data BEFORE the bracket"},
	{"http://[::1]/", "its control"},
	{"http://[::1]x/", "data after ']' that is not a port"},
	{"http://[::1]:80/", "its control"},
	{"http://[::1]]/", "the second ']' is data after the first"},
	{"http://[::1]:/", "empty port is fine"},
	{"http://[::1]", "nothing after the bracket at all"},
	{"http://[[::1]/", "'[' inside the bracketed part"},
	{"http://user@[::1]/", "userinfo before a bracketed host"},
	{"http://us[er]@example.com/", "brackets INSIDE the userinfo"},
	{"http://us[er]@1.2.3.4/", "same, with a v4 host behind it"},
	{"http://us[er]@[::1]/", "same, with a v6 host behind it"},
	{"http://[::1]@example.com/", "the bracket is the userinfo"},
	{"http://[]/", "empty bracketed host"},
	{"http://[]:80/", ""},

	// --- 12. IPvFuture ---
	{"http://[v1.x]/", "the minimal legal IPvFuture"},
	{"http://[v7.aB-c~d]/", "anything at all after the dot"},
	{"http://[vFF.x]/", "hex digits, upper and lower"},
	{"http://[vff.x]/", ""},
	{"http://[v.x]/", "no hex digits -> invalid"},
	{"http://[vz.x]/", "'z' is not a hex digit"},
	{"http://[v1x]/", "no dot"},
	{"http://[v1.]/", "nothing after the dot"},
	{"http://[v]/", "just the v"},
	{"http://[v1." + spChr + "]/", "a space satisfies '.+'"},
	{"http://[V1.x]/", "capital V is NOT the IPvFuture branch"},
	{"http://[v1.x.y]/", ""},

	// --- 13. IPv4 in brackets, and the near-misses that are not IPv4 ---
	{"http://[1.2.3.4]/", "a legal v4 address, illegally bracketed"},
	{"http://1.2.3.4/", "its control — unbracketed is fine"},
	{"http://[0.0.0.0]/", ""},
	{"http://[255.255.255.255]/", ""},
	{"http://[256.1.1.1]/", "256 is not an octet -> not v4, not v6"},
	{"http://[1.2.3]/", "three octets"},
	{"http://[1.2.3.4.5]/", "five"},
	{"http://[1.2.3.]/", "empty last octet"},
	{"http://[.1.2.3]/", "empty first octet"},
	{"http://[01.2.3.4]/", "leading zero is refused (bpo-36384)"},
	{"http://[0.2.3.4]/", "a bare 0 is fine"},
	{"http://[00.2.3.4]/", "'00' is not"},
	{"http://[1.2.3.0004]/", "four characters"},
	{"http://[1.2.3.004]/", "three characters, but a leading zero"},
	{"http://[1.2.3.4a]/", "not all digits"},
	{"http://[1.2.3.٤]/", "an ARABIC-INDIC digit — isdigit yes, isascii no"},
	{"http://[1.2.3.4/]/", "the '/' ends the netloc first"},

	// --- 14. IPv6 shapes ---
	{"http://[::]/", "the all-zeros form"},
	{"http://[::1]/", ""},
	{"http://[1::]/", ""},
	{"http://[1:2:3:4:5:6:7:8]/", "the full eight"},
	{"http://[1:2:3:4:5:6:7:8:9]/", "nine"},
	{"http://[1:2:3:4:5:6:7]/", "seven, without a ::"},
	{"http://[1:2:3:4:5:6:7:8:9:a:b]/", "eleven — past the maxsplit"},
	{"http://[1::2::3]/", "two '::'"},
	{"http://[:::]/", "three colons"},
	{"http://[::::]/", "four"},
	{"http://[:1:2:3:4:5:6:7]/", "a leading ':' that is not a '::'"},
	{"http://[1:2:3:4:5:6:7:]/", "a trailing ':' that is not a '::'"},
	{"http://[:1::2]/", "leading ':' with a '::' elsewhere"},
	{"http://[1::2:]/", "trailing ':' with a '::' elsewhere"},
	{"http://[1:2]/", "two parts — fewer than the three a v6 needs"},
	{"http://[1]/", "one part"},
	{"http://[::1:2:3:4:5:6:7:8]/", "a '::' that skips nothing"},
	{"http://[1:2:3:4:5:6:7::]/", "'::' at the end skipping one"},
	{"http://[12345::1]/", "a five-character hextet"},
	{"http://[ffff::1]/", "four is the limit and this is it"},
	{"http://[g::1]/", "'g' is not a hex digit"},
	{"http://[FFFF::AAAA]/", "uppercase hex, which then LOWERS"},
	{"http://[::ffff:1.2.3.4]/", "the v4-mapped form"},
	{"http://[::ffff:1.2.3.256]/", "with a bad octet"},
	{"http://[::ffff:01.2.3.4]/", "with a leading zero"},
	{"http://[1:2:3:4:5:6:1.2.3.4]/", "six hextets plus a dotted quad"},
	{"http://[1:2:3:4:5:6:7:1.2.3.4]/", "seven — one too many"},
	{"http://[::x.y]/", "a dot in the last part that is not a v4 address"},
	{"http://[1111:2222:3333:4444:5555:6666:255.255.255.255]/", "45 characters — the longest legal v6"},
	{"http://[0000:0000:0000:0000:0000:0000:0000:0000:0000:0000]/", "far past 45"},
	{"http://[fe80::1%]/", "'%' with an empty zone id"},
	{"http://[fe80::1%a%b]/", "two '%' in the zone id"},
	{"http://[%eth0]/", "a zone id with no address"},
	{"http://[fe80::1%eth0]/", "its control"},
	{"http://[::1%25]/", ""},
	{"http://[::/1]/", "a '/' — but the netloc is cut at it first"},
	{"http://[" + vtChr + "]/", "a bare control character as the host"},
	{"http://[" + eacute + "]/", "a non-ASCII bracketed host"},
	{"http://[ ]/", "a bare space"},
	{"http://[::1]:8080/", "the control, with a port"},

	// --- 15. _checknetloc: the NFKC gate ---
	// It only runs for a NON-ASCII, non-empty netloc, so every entry
	// here needs a non-ASCII character; the ASCII controls above are
	// what prove the gate is skipped otherwise.
	{"http://exam" + acctOf + "ple.com/", "U+2100 NFKC-expands to a/c"},
	{"http://exam" + careOf + "ple.com/", "U+2105 expands to c/o"},
	{"http://example.com/", "the control for both"},
	{"http://exam" + ffQuest + "ple.com/", "fullwidth ? normalizes to ?"},
	{"http://exam" + ffHash + "ple.com/", "fullwidth # normalizes to #"},
	{"http://exam" + ffAt + "ple.com/", "fullwidth @ normalizes to @"},
	{"http://exam" + ffColon + "ple.com/", "fullwidth : normalizes to :"},
	{"http://u@exam" + acctOf + "ple.com/", "an '@' beside an offender — raises either way"},
	{"http://exam" + acctOf + "ple.com:80/", "and a ':' beside one"},
	// The two rows above are what the strip looks like, and they do not
	// TEST it: `\u2100` normalizes to "a/c" and the `/` raises whether or
	// not the '@' was removed first, so deleting the four ReplaceAll
	// lines leaves them green (found by mutation, not by reading).
	//
	// The strip can only ever turn a RAISE into a pass, so reaching it
	// needs a netloc that (a) literally contains one of `@:#?` and (b) is
	// NFKC-UNSTABLE for some unrelated reason. Then the gate character is
	// present in netloc2 through the LITERAL, not through normalization,
	// and CPython removes it before looking. Decomposed "café" supplies
	// the instability; each row below carries a different gate character.
	{"http://u@cafe" + combAcc + ".com/", "the '@' really is stripped first"},
	{"http://u:p@cafe" + combAcc + ".com/", "...and the ':' with it"},
	{"http://cafe" + combAcc + ".com:8080/", "a port, same shape"},
	{"http://cafe" + combAcc + ".com/", "the control: unstable, no gate char"},
	{"http://caf" + eacute + ".com/", "non-ASCII and NFKC-STABLE -> no raise"},
	{"http://cafe" + combAcc + ".com/", "decomposed: NFKC changes it, harmlessly"},
	{"http://" + ffE + "xample.com/", "fullwidth E normalizes to E, harmlessly"},
	{"http://" + acctOf + "/", "the whole netloc is the offender"},
	{"http://u@" + acctOf + "/", ""},
	{"http://" + acctOf + "@example.com/", "in the userinfo — still the netloc"},
	{"http://exam" + acctOf + "ple.com?x", "the netloc is cut at the ? first"},
	{"http://exam" + acctOf + "ple.com#x", "and at the #"},
	{"http://exam" + acctOf + "ple.com/path", "and at the /"},
	{"//exam" + acctOf + "ple.com/", "no scheme, same gate"},
	{"http://exam" + acctOf + "ple.com" + tabChr + "/", "the tab goes first"},
	{"http://[" + acctOf + "]/", "brackets are checked BEFORE the NFKC gate"},
	{"http://[1.2.3.4]" + acctOf + "/", "the bracket error wins here too"},
	{"http://" + nbspChr + "example.com/", "NBSP is non-ASCII but NFKC-safe"},
	{"http://ex" + outlA + "ample.com/", "the two engines' NFKC tables DISAGREE here"},
	{"http://ex" + outl9 + "ample.com/", "same block, a digit"},
	{"http://exAample.com/", "the control for both"},

	// --- 16. Schemes that carry no netloc at all -> hostname is None ---
	{"http:?x", "a query with no //"},
	{"http:#x", "a fragment with no //"},
	{"https:example.com", ""},
	{"data:text/plain,hello", ""},
	{"tel:+1-555-0100", ""},
	{"about:blank", ""},
	{"urn:isbn:0451450523", ""},
	{"javascript:alert(1)", ""},
	{"news:comp.lang.python", ""},
	{"http://@", "netloc is '@' — the host is empty"},
	{"http://:", "netloc is ':' — likewise"},

	// --- 17. Hosts that are empty, and hosts that only look empty ---
	{"http://:80/", "None"},
	{"http://@/", "None"},
	{"http://@:80/", "None"},
	{"http:// /", "a single space IS a host"},
	{"http://" + nul + "/", "a single NUL IS a host"},
	{"http://./", "a single dot"},
	{"http://-/", ""},
	{"http://0/", ""},

	// --- 18. Long and ordinary, to keep the corpus from being all edges ---
	{"https://www.google.com/search?q=urllib+parse&hl=en", ""},
	{"https://en.wikipedia.org/wiki/URL#History", ""},
	{"http://192.168.0.55:8080/api/v1/status", ""},
	{"https://raw.githubusercontent.com/slycrel/x/main/README.md", ""},
	{"https://example.com/a%20b/c?d=e%26f#g", ""},
	{"https://user:p%40ss@example.com:8443/x", ""},
	{"http://xn--bcher-kva.example/", "punycode is just ASCII here"},
	{"https://example.com./", "trailing dot in the host"},
	{"https://.example.com/", "leading dot"},
	{"https://example..com/", "doubled dot"},
	{"https://ex_ample.com/", "underscore in a host is fine for the parser"},
	{"https://ex-ample.com/", ""},
	{"https://EXAMPLE.com:8080/PATH?Q=V#F", "everything at once"},
}

// generated extends the corpus mechanically so the floors are not
// resting on one fixture each, and so every character of the
// C0-or-space set is actually exercised rather than sampled. Hand-written
// fixtures are where the interesting shapes are; a sweep is where
// "the set is 0x00..0x20 inclusive" stops being a claim about a comment.
func generatedCases() []ucase {
	var out []ucase
	// Every code point from U+0000 to U+0021 as a LEADING character: the
	// 33 that are stripped, plus U+0021 which is not, so the boundary is
	// measured from both sides.
	for r := rune(0); r <= 0x21; r++ {
		out = append(out, ucase{string(r) + "http://example.com/",
			fmt.Sprintf("leading U+%04X", r)})
	}
	// The same code points INSIDE the host, where only \t \r \n are
	// removed and everything else survives into the hostname.
	for r := rune(0); r <= 0x21; r++ {
		out = append(out, ucase{"http://ex" + string(r) + "ample.com/",
			fmt.Sprintf("interior U+%04X", r)})
	}
	// And as a TRAILING character, which lstrip does not touch.
	for r := rune(0); r <= 0x21; r++ {
		out = append(out, ucase{"http://example.com" + string(r),
			fmt.Sprintf("trailing U+%04X", r)})
	}
	return out
}

func fullCorpus() []ucase { return append(append([]ucase{}, corpus...), generatedCases()...) }

func TestHostnameMatchesCPython(t *testing.T) {
	cases := fullCorpus()
	if len(cases) < 300 {
		t.Fatalf("the corpus is %d cases; the floor is 300", len(cases))
	}
	urls := make([]string, len(cases))
	for i, c := range cases {
		urls[i] = c.u
	}
	var want []pyAnswer
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyURLProbe, &want, pyprobe.Arg(t, urls))
	if len(want) != len(cases) {
		t.Fatalf("probe returned %d answers for %d cases", len(want), len(cases))
	}

	// Counters for the anti-vacuity floors. Every one is incremented
	// from CPython's answer.
	var (
		nHost, nNone, nRaise     int
		nNonASCIIHost            int
		nUpperLowered            int
		nSimpleLowerInsufficient int
		nZone, nUserinfo, nPort  int
		nIPv6Host                int
		nC0Stripped, nUnsafeGone int
		nNetlocEmptyButNotNone   int
		nNetlocNone              int
		nSchemeSet, nSchemeEmpty int
		nQuerySet, nFragmentSet  int
		msgs                     = map[string]int{}
		reprMsgs                 = map[string]int{}
		mismatches               int
	)

	for i, c := range cases {
		w := want[i]
		label := fmt.Sprintf("case %d %q", i, c.u)
		if c.note != "" {
			label += " (" + c.note + ")"
		}

		gotHost, gotFound, gotErr := Hostname(c.u)

		switch w.K {
		case "raise":
			nRaise++
			msgs[w.Msg]++
			if strings.HasSuffix(w.Msg, "does not appear to be an IPv4 or IPv6 address") {
				reprMsgs[w.Msg]++
			}
			if gotErr == nil {
				mismatches++
				t.Errorf("%s: CPython raised ValueError(%q); Go returned host=%q found=%v",
					label, w.Msg, gotHost, gotFound)
				continue
			}
			if gotErr.Error() != w.Msg {
				mismatches++
				t.Errorf("%s: message text differs\n  CPython: %q\n  Go:      %q",
					label, w.Msg, gotErr.Error())
			}
			if gotHost != "" || gotFound {
				t.Errorf("%s: Go raised but also returned host=%q found=%v",
					label, gotHost, gotFound)
			}
		case "ok":
			if gotErr != nil {
				mismatches++
				t.Errorf("%s: CPython returned host=%v; Go raised %v",
					label, showPtr(w.Host), gotErr)
				continue
			}
			if w.Host == nil {
				nNone++
				if gotFound {
					mismatches++
					t.Errorf("%s: CPython hostname is None; Go found %q", label, gotHost)
				}
				if gotHost != "" {
					t.Errorf("%s: Go reported found=false with a non-empty host %q",
						label, gotHost)
				}
			} else {
				nHost++
				if !gotFound || gotHost != *w.Host {
					mismatches++
					t.Errorf("%s: hostname differs\n  CPython: %q\n  Go:      %q (found=%v)",
						label, *w.Host, gotHost, gotFound)
				}
				h := *w.Host
				if !isASCII(h) {
					nNonASCIIHost++
				}
				if up := strings.ToUpper(h); up != h && strings.Contains(c.u, up) {
					nUpperLowered++
				}
				// Would Go's own strings.ToLower have sufficed? Strip the
				// three characters urlsplit deletes first, so this counts
				// case-mapping and zone preservation rather than
				// tab removal.
				stripped := c.u
				for _, b := range unsafeURLBytesToRemove {
					stripped = strings.ReplaceAll(stripped, b, "")
				}
				if !strings.Contains(strings.ToLower(stripped), h) {
					nSimpleLowerInsufficient++
				}
				if strings.Contains(h, "%") {
					nZone++
				}
				if strings.Contains(h, ":") {
					nIPv6Host++
				}
				if strings.Contains(c.u, "@") && !strings.Contains(h, "@") {
					nUserinfo++
				}
				if strings.Contains(c.u, h+":") {
					nPort++
				}
				if r := []rune(c.u); len(r) > 0 && r[0] <= 0x20 {
					nC0Stripped++
				}
				for _, b := range unsafeURLBytesToRemove {
					if strings.Contains(c.u, b) && !strings.Contains(h, b) {
						nUnsafeGone++
						break
					}
				}
			}
			// The five-tuple, including the Nones. Split() claims to
			// reproduce _urlsplit and this is the only thing that checks
			// the claim.
			got, err := Split(c.u)
			if err != nil {
				t.Errorf("%s: Split raised %v where CPython did not", label, err)
				continue
			}
			// scheme is the one component with no None — see
			// SplitResult.Scheme's comment, which this asserts rather
			// than assumes.
			if w.Scheme == nil {
				t.Errorf("%s: CPython's _urlsplit returned scheme=None. "+
					"SplitResult.Scheme is modelled as a plain string on the "+
					"measured claim that it never does; the claim is now false",
					label)
			} else if *w.Scheme != got.Scheme {
				t.Errorf("%s: scheme differs\n  CPython: %q\n  Go:      %q",
					label, *w.Scheme, got.Scheme)
			}
			cmpOpt(t, label, "netloc", w.Netloc, got.Netloc, got.NetlocSet)
			if got.Path != w.Path {
				t.Errorf("%s: path differs\n  CPython: %q\n  Go:      %q",
					label, w.Path, got.Path)
			}
			cmpOpt(t, label, "query", w.Query, got.Query, got.QuerySet)
			cmpOpt(t, label, "fragment", w.Fragment, got.Fragment, got.FragmentSet)

			if w.Netloc == nil {
				nNetlocNone++
			} else if *w.Netloc == "" {
				nNetlocEmptyButNotNone++
			}
			if w.Scheme != nil && *w.Scheme == "" {
				nSchemeEmpty++
			} else {
				nSchemeSet++
			}
			if w.Query != nil {
				nQuerySet++
			}
			if w.Fragment != nil {
				nFragmentSet++
			}
		default:
			t.Fatalf("%s: probe returned an unknown kind %q", label, w.K)
		}
	}

	if mismatches > 0 {
		t.Logf("%d of %d cases disagree", mismatches, len(cases))
	}

	// ---- ANTI-VACUITY ----
	//
	// A corpus that reaches one outcome cannot tell a working port from a
	// broken one. Each floor below names what a zero would MEAN, because
	// a bare number tells a later reader nothing about why it is there.
	floor := func(name string, got, min int, why string) {
		t.Helper()
		if got < min {
			t.Errorf("ANTI-VACUITY: %s = %d, floor %d — %s", name, got, min, why)
		}
	}
	floor("non-empty hostnames", nHost, 120,
		"without these the corpus cannot see a wrong host string")
	floor("None hostnames", nNone, 40,
		"without these `return \"\", false, nil` passes every case")
	floor("ValueErrors", nRaise, 60,
		"without these the whole exception contract is untested")
	floor("non-ASCII hostnames", nNonASCIIHost, 3,
		"the NFKC gate and pytext.Lower both only matter above ASCII")
	floor("ASCII hosts that were lowered", nUpperLowered, 4,
		"the .lower() call would otherwise be unobserved")
	floor("hosts strings.ToLower would get wrong", nSimpleLowerInsufficient, 3,
		"this is the whole reason pytext.Lower is used instead")
	floor("hosts with a zone id", nZone, 4,
		"the %-partition and the un-lowered zone")
	floor("IPv6 hostnames", nIPv6Host, 8,
		"the bracket branch of _hostinfo")
	floor("hosts behind userinfo", nUserinfo, 6,
		"the rpartition('@'), whose direction is easy to get backwards")
	floor("hosts with a port", nPort, 6,
		"the partition(':') that must not reach the hostname")
	floor("hosts after a C0/space strip", nC0Stripped, 20,
		"the lstrip set")
	floor("hosts after a tab/CR/LF removal", nUnsafeGone, 4,
		"the whole-string replace, which is not the same as the strip")
	floor("netloc == '' (not None)", nNetlocEmptyButNotNone, 4,
		"the None-vs-empty distinction Split claims to keep")
	floor("netloc is None", nNetlocNone, 8, "the other side of it")
	floor("scheme found", nSchemeSet, 100, "the scheme branch")
	floor("scheme is ''", nSchemeEmpty, 8,
		"the other side of it — including the cases where a scheme-looking "+
			"prefix is REFUSED, which is where the schemeChars set is decided")
	floor("query is not None", nQuerySet, 4, "")
	floor("fragment is not None", nFragmentSet, 4, "")

	// Every message text this port can emit must have been PRODUCED BY
	// CPYTHON at least once in this corpus. A literal in pyurl.go that
	// nothing reaches is a string nobody has ever compared.
	for _, m := range []string{
		"Invalid IPv6 URL",
		"IPvFuture address is invalid",
		"An IPv4 address cannot be in brackets",
	} {
		if msgs[m] == 0 {
			t.Errorf("ANTI-VACUITY: no fixture makes CPython raise %q", m)
		}
	}
	if len(reprMsgs) < 6 {
		t.Errorf("ANTI-VACUITY: only %d distinct \"does not appear to be an "+
			"IPv4 or IPv6 address\" messages; the repr() half of that message "+
			"needs several", len(reprMsgs))
	}
	nfkc := 0
	for m := range msgs {
		if strings.HasSuffix(m, "contains invalid characters under NFKC normalization") {
			nfkc++
		}
	}
	if nfkc < 5 {
		t.Errorf("ANTI-VACUITY: only %d distinct NFKC messages; the gate has "+
			"five trigger characters", nfkc)
	}

	if t.Failed() {
		keys := make([]string, 0, len(msgs))
		for k := range msgs {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			t.Logf("  CPython message x%d: %q", msgs[k], k)
		}
	}
}

// cmpOpt compares one Optional[str] component against the port's
// (value, set) pair. The three-way mismatch — None vs "" vs a value —
// gets three different failure texts, because "netloc differs" with two
// empty strings printed is the failure that wastes an afternoon.
func cmpOpt(t *testing.T, label, name string, want *string, got string, gotSet bool) {
	t.Helper()
	switch {
	case want == nil && gotSet:
		t.Errorf("%s: %s — CPython says None, Go says %q (set)", label, name, got)
	case want == nil && !gotSet:
		if got != "" {
			t.Errorf("%s: %s — Go says unset but carries %q", label, name, got)
		}
	case want != nil && !gotSet:
		t.Errorf("%s: %s — CPython says %q, Go says None", label, name, *want)
	case want != nil && *want != got:
		t.Errorf("%s: %s differs\n  CPython: %q\n  Go:      %q", label, name, *want, got)
	}
}

func showPtr(p *string) string {
	if p == nil {
		return "None"
	}
	return fmt.Sprintf("%q", *p)
}
