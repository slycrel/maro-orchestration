package pydifflib

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The subject is a number a caller thresholds at `>= 0.85`. That makes
// this differential unusual in one respect: there is no tolerance under
// which a disagreement is acceptable, because the only disagreement that
// matters is the one that lands on the far side of 0.85, and a one-ulp
// error does that as readily as a large one. So every comparison below is
// on the IEEE-754 bit pattern, obtained on the CPython side with
// struct.pack and on the Go side with math.Float64bits. Comparing
// `float64 == float64` would already be exact; the bits are compared
// instead because they are also exact for the values `==` gets wrong
// (NaN, and -0.0 versus 0.0), and because a failure prints something a
// reader can act on.
//
// difflib is stdlib, so the probe sets Stdlib and no Marker: there is no
// repo module whose absence would make a skip here honest.
const pyRatioSrc = `
import json, struct, sys, difflib

def bits(x):
    # The exact 64-bit pattern, as a decimal string. A JSON number would
    # be a float on the way back and the whole point is to not go through
    # one.
    return str(struct.unpack('<Q', struct.pack('<d', x))[0])

with open(sys.argv[1], encoding="utf-8") as fh:
    cases = json.load(fh)

out = []
for c in cases:
    junk = c["junk"]
    isjunk = None if junk is None else (lambda ch, s=junk: ch in s)
    sm = difflib.SequenceMatcher(isjunk, c["a"], c["b"], c["autojunk"])
    r = sm.ratio()
    out.append({
        "name": c["name"],
        "bits": bits(r),
        "repr": repr(r),
        "blocks": [[t.a, t.b, t.size] for t in sm.get_matching_blocks()],
        "npopular": len(sm.bpopular),
        "njunk": len(sm.bjunk),
        "la": len(c["a"]),
        "lb": len(c["b"]),
    })
print(json.dumps(out))
`

// diffCase is one (a, b) fixture plus the two SequenceMatcher knobs.
//
// junk is a *string rather than a string so that "no isjunk at all"
// (Python's None, under which __chain_b skips the purge loop entirely)
// stays distinct from "an isjunk that matches nothing" (a predicate that
// runs and finds none). The caller's contract is the first of those, and
// collapsing them is exactly the absence-versus-zero mistake this port is
// supposed to avoid.
type diffCase struct {
	name     string
	a, b     string
	junk     *string
	autojunk bool
}

type wireCase struct {
	Name     string  `json:"name"`
	A        string  `json:"a"`
	B        string  `json:"b"`
	Junk     *string `json:"junk"`
	Autojunk bool    `json:"autojunk"`
}

type pyResult struct {
	Name     string  `json:"name"`
	Bits     string  `json:"bits"`
	Repr     string  `json:"repr"`
	Blocks   [][]int `json:"blocks"`
	NPopular int     `json:"npopular"`
	NJunk    int     `json:"njunk"`
	LA       int     `json:"la"`
	LB       int     `json:"lb"`
}

func junkOf(s string) *string { return &s }

// latin1 widens every BYTE of s to the code point with the same value.
//
// Ratio over two latin1-widened strings is precisely what a byte-indexed
// port of difflib would have computed for the originals: same sequence
// length, same element equalities, same denominator. It is the control
// that makes the "a byte-based port diverges" claim measurable without
// keeping a second, deliberately wrong implementation around.
func latin1(s string) string {
	rs := make([]rune, 0, len(s))
	for i := 0; i < len(s); i++ {
		rs = append(rs, rune(s[i]))
	}
	return string(rs)
}

func byteRatio(a, b string) float64 { return Ratio(latin1(a), latin1(b)) }

// rng is a fixed-seed LCG. Deterministic on purpose: a corpus that
// changes between runs turns a real divergence into a flake and an
// unreachable branch into luck.
type rng struct{ s uint64 }

func (r *rng) next() uint64 {
	r.s = r.s*6364136223846793005 + 1442695040888963407
	return r.s >> 33
}

func (r *rng) pick(alphabet []rune) rune {
	return alphabet[int(r.next()%uint64(len(alphabet)))]
}

func (r *rng) str(alphabet []rune, n int) string {
	out := make([]rune, n)
	for i := range out {
		out[i] = r.pick(alphabet)
	}
	return string(out)
}

// Code points named rather than pasted, for the same reason the
// provenance table does it: a fixture whose subject is WHICH code point it
// carries cannot be reviewed if the answer is "look closely".
var (
	eacute  = string(rune(0x00E9))  // é  LATIN SMALL LETTER E WITH ACUTE (2 bytes)
	combAcc = string(rune(0x0301))  // ́  COMBINING ACUTE ACCENT (2 bytes)
	cjkHan  = string(rune(0x6F22))  // 漢 CJK UNIFIED IDEOGRAPH-6F22 (3 bytes)
	cjkZi   = string(rune(0x5B57))  // 字 CJK UNIFIED IDEOGRAPH-5B57 (3 bytes)
	grin    = string(rune(0x1F600)) // 😀 GRINNING FACE (4 bytes, non-BMP)
	joy     = string(rune(0x1F602)) // 😂 FACE WITH TEARS OF JOY (4 bytes, non-BMP)
	nbsp    = string(rune(0x00A0))  // NO-BREAK SPACE (2 bytes)
)

func corpus() []diffCase {
	var cs []diffCase
	add := func(name, a, b string) {
		cs = append(cs, diffCase{name: name, a: a, b: b, autojunk: true})
	}

	// --- the shapes the caller actually feeds it ----------------------
	// Short, lowercase, whitespace-normalised sentences differing by
	// punctuation, a word, a number, or nothing. These are the rows whose
	// answers straddle the 0.85 threshold.
	callerPairs := [][2]string{
		{"archive x is blocked", "archive x is blocked."},
		{"archive x is blocked", "archive y is blocked"},
		{"archive x is blocked", "archive x is blocked now"},
		{"archive x is blocked", "the archive x is blocked"},
		{"archive x is blocked", "archive x is unblocked"},
		{"archive x is blocked", "archive x was blocked"},
		{"archive x is blocked", "archives x are blocked"},
		{"archive x is blocked", "archive x is blocke"},
		{"archive x is blocked", "rchive x is blocked"},
		{"archive x is blocked", "archive x is blocked "},
		{"archive x is blocked", "blocked is archive x"},
		{"archive x is blocked", "completely different text"},
		{"run 12 failed on step 3", "run 12 failed on step 4"},
		{"run 12 failed on step 3", "run 13 failed on step 3"},
		{"run 12 failed on step 3", "run 12 failed at step 3"},
		{"run 12 failed on step 3", "run 12 failed on step 3."},
		{"the probe ran and failed", "the probe ran and failed"},
		{"the probe ran and failed", "the probe ran and passed"},
		{"lesson mints carry receipts", "lesson mints carry receipts."},
		{"lesson mints carry receipts", "lessons mint carry receipt"},
		{"caps are circuit breakers", "caps are circuit-breakers"},
		{"caps are circuit breakers", "caps are circuit breakers not truncators"},
		{"do not escalate on a link", "do not escalate on a linked page"},
		{"do not escalate on a link", "never escalate on a link"},
		{"verify before you fix", "verify before you fix it"},
		{"verify before you fix", "verify the claim before you fix"},
		{"a fix without a fixture", "a fix without a fixture is a guess"},
		{"a fix without a fixture", "a fixture without a fix"},
		{"worker push guard", "worker push guard."},
		{"worker push guard", "worker pull guard"},
		{"shadow lane is on", "shadow lane is off"},
		{"shadow lane is on", "shadow lane is on"},
	}
	for i, p := range callerPairs {
		add(fmt.Sprintf("caller/%02d", i), p[0], p[1])
	}

	// --- degenerate and one-character --------------------------------
	// The controls for everything exotic below, and the only route to
	// _calculate_ratio's `if length:` false arm.
	add("degenerate/both empty", "", "")
	add("degenerate/empty vs one", "", "a")
	add("degenerate/one vs empty", "a", "")
	add("degenerate/empty vs long", "", strings.Repeat("ab", 60))
	add("degenerate/long vs empty", strings.Repeat("ab", 60), "")
	add("degenerate/one same", "a", "a")
	add("degenerate/one differs", "a", "b")
	add("degenerate/one vs two", "a", "ab")
	add("degenerate/two vs one", "ab", "a")
	add("degenerate/nothing in common", "abc", "xyz")
	add("degenerate/identical short", "abcd", "abcd")
	add("degenerate/identical long", strings.Repeat("hello world ", 20), strings.Repeat("hello world ", 20))
	add("degenerate/space vs empty", " ", "")
	add("degenerate/space vs space", " ", " ")
	add("degenerate/doc abcd bcde", "abcd", "bcde")
	add("degenerate/doc abxcd abcd", "abxcd", "abcd")
	// The docstring case for find_longest_match, whose whole point is
	// that the leftmost-in-a, then leftmost-in-b tie-break is observable.
	add("degenerate/doc leading space", " abcd", "abcd abcd")
	add("degenerate/doc ab acab", "ab", "acab")
	add("degenerate/ab vs c", "ab", "c")

	// --- repetitive, where j2len does real work -----------------------
	for _, n := range []int{3, 7, 16, 33, 64} {
		add(fmt.Sprintf("repeat/a%d vs a%d", n, n), strings.Repeat("a", n), strings.Repeat("a", n))
		add(fmt.Sprintf("repeat/a%d vs a%d+1", n, n), strings.Repeat("a", n), strings.Repeat("a", n+1))
		add(fmt.Sprintf("repeat/ab%d vs ba%d", n, n), strings.Repeat("ab", n), strings.Repeat("ba", n))
		add(fmt.Sprintf("repeat/abc%d vs abd%d", n, n), strings.Repeat("abc", n), strings.Repeat("abd", n))
	}
	// A block that is adjacent after sorting, which is the only thing the
	// merge pass in get_matching_blocks reacts to.
	add("merge/split then rejoin", "abcdefghij", "abXcdefghij")
	add("merge/two inserts", "abcdefghijkl", "abXcdefYghijkl")
	add("merge/interleaved", "aXbXcXdXe", "abcde")
	add("merge/reversed halves", "abcdefghij", "fghijabcde")

	// --- the autojunk boundary, from both sides -----------------------
	//
	// n >= 200 is the gate and ntest = n//100 + 1 the threshold, so at
	// n == 199 nothing is ever popular, at n == 200 an element is popular
	// above 3 occurrences, and at n == 300 above 4. Each length is built
	// with an alphabet dense enough to cross the threshold and with one
	// that is not, so the boundary is bracketed on both axes rather than
	// only on length.
	for _, n := range []int{150, 198, 199, 200, 201, 250, 299, 300, 301, 400} {
		// Dense: 5 distinct characters over n positions, so every one of
		// them appears about n/5 times, far above ntest.
		dense := []rune("abcde")
		r := &rng{s: uint64(n)*7919 + 13}
		bDense := r.str(dense, n)
		aDense := r.str(dense, n/2)
		add(fmt.Sprintf("autojunk/dense n=%d", n), aDense, bDense)
		add(fmt.Sprintf("autojunk/dense n=%d self", n), bDense, bDense)
		// Sparse: an alphabet as large as n, so no element repeats and
		// nothing can be popular even above the length gate.
		var sb strings.Builder
		for i := 0; i < n; i++ {
			sb.WriteRune(rune(0x0100 + i))
		}
		bSparse := sb.String()
		add(fmt.Sprintf("autojunk/sparse n=%d", n), bSparse, bSparse)
		add(fmt.Sprintf("autojunk/sparse n=%d edited", n), bSparse, bSparse[:len(bSparse)/2]+"zz"+bSparse[len(bSparse)/2:])
		// Exactly at the threshold: one character appearing ntest times
		// (not popular) and another appearing ntest+1 times (popular),
		// padded with unique filler. This is the pair that separates
		// `>` from `>=` in the popularity test.
		ntest := n/100 + 1
		var tb strings.Builder
		tb.WriteString(strings.Repeat("Q", ntest))
		tb.WriteString(strings.Repeat("Z", ntest+1))
		for i := tb.Len(); i < n; i++ {
			tb.WriteRune(rune(0x0400 + i))
		}
		bThresh := []rune(tb.String())
		if len(bThresh) > n {
			bThresh = bThresh[:n]
		}
		add(fmt.Sprintf("autojunk/threshold n=%d", n), "QQZZ", string(bThresh))
		add(fmt.Sprintf("autojunk/threshold n=%d self", n), string(bThresh), string(bThresh))
	}
	// A long b whose popular element sits between two matchable runs, so
	// that removing it from b2j forces find_longest_match to reach the
	// match through its non-junk extension loops instead of the scan.
	{
		filler := strings.Repeat("e", 40)
		b := "prefixblock" + filler + "suffixblock" + strings.Repeat("q", 200)
		a := "prefixblock" + filler + "suffixblock"
		add("autojunk/extension through popular", a, b)
		add("autojunk/extension through popular shifted", "X"+a, b)
	}

	// --- popular alphabets, where pruning bites ------------------------
	for i, alpha := range []string{"ab", "abc", "abcd", "aeiou", "01"} {
		r := &rng{s: uint64(i)*104729 + 7}
		al := []rune(alpha)
		for _, n := range []int{210, 260, 340} {
			b := r.str(al, n)
			add(fmt.Sprintf("popular/%s n=%d", alpha, n), r.str(al, n), b)
			add(fmt.Sprintf("popular/%s n=%d near", alpha, n), b[:len(b)-1]+"Z", b)
		}
	}

	// --- non-ASCII, each with an ASCII control ------------------------
	//
	// Every row here is one a byte-indexed port answers differently,
	// because the elements are multi-byte; the control immediately after
	// it is the same shape in ASCII, where the two ports agree.
	nonASCII := [][3]string{
		{"accented one char", "caf" + eacute, "cafe"},
		{"accented control", "cafx", "cafe"},
		{"accented both", "r" + eacute + "sum" + eacute, "r" + eacute + "sume"},
		{"combining vs precomposed", "cafe" + combAcc, "caf" + eacute},
		{"combining control", "cafee", "cafe"},
		{"cjk pair", cjkHan + cjkZi, cjkHan + cjkZi + cjkHan},
		{"cjk control", "ab", "aba"},
		{"cjk sentence", cjkHan + cjkZi + "is blocked", cjkHan + cjkZi + "is blocked."},
		{"emoji nonbmp", grin + " ok", grin + " ok."},
		{"emoji nonbmp swap", grin + joy + grin, joy + grin + joy},
		{"emoji control", "ab a", "ba b"},
		{"nbsp vs space", "archive" + nbsp + "x", "archive x"},
		{"nbsp control", "archiveXx", "archive x"},
		{"mixed widths", "a" + eacute + cjkHan + grin, "a" + eacute + cjkHan + joy},
		{"mixed widths control", "abcd", "abce"},
		{"all cjk long", strings.Repeat(cjkHan+cjkZi, 30), strings.Repeat(cjkHan+cjkZi, 29) + cjkHan},
		{"all cjk control", strings.Repeat("ab", 30), strings.Repeat("ab", 29) + "a"},
		{"emoji run", strings.Repeat(grin, 20), strings.Repeat(grin, 19) + joy},
		{"emoji run control", strings.Repeat("g", 20), strings.Repeat("g", 19) + "j"},
		{"combining run", strings.Repeat("e"+combAcc, 20), strings.Repeat("e"+combAcc, 19) + "e"},
		{"combining run control", strings.Repeat("ez", 20), strings.Repeat("ez", 19) + "e"},
	}
	for _, row := range nonASCII {
		add("unicode/"+row[0], row[1], row[2])
	}
	// A long non-ASCII b that also crosses the autojunk gate, so the two
	// hazards meet: 240 code points is over the gate, 240 BYTES of the
	// same string is a different sequence entirely.
	{
		r := &rng{s: 991}
		al := []rune(cjkHan + cjkZi + eacute + grin + "a")
		b := r.str(al, 240)
		add("unicode/long over gate", r.str(al, 240), b)
		add("unicode/long over gate self", b, b)
		// The byte length is well over 200 while the code-point length is
		// under it: a byte-indexed port turns autojunk ON here and a
		// correct one leaves it off.
		bShort := r.str(al, 120)
		add("unicode/under gate by runes over by bytes", r.str(al, 120), bShort)
	}

	// --- case-only differences ----------------------------------------
	caseRows := [][2]string{
		{"Archive X Is Blocked", "archive x is blocked"},
		{"ARCHIVE", "archive"},
		{"aB", "Ab"},
		{"Hello World", "hello world"},
		{"MixedCase", "mixedcase"},
		{"CamelCaseWord", "camelcaseword"},
		{eacute + "TE", eacute + "te"},
		{"STRASSE", "strasse"},
	}
	for i, p := range caseRows {
		add(fmt.Sprintf("case/%02d", i), p[0], p[1])
	}

	// --- pseudorandom bulk, four alphabets ----------------------------
	//
	// The bulk of the denominator. Small alphabets make long matches and
	// heavy j2len traffic; the wide one makes short ones; the unicode one
	// keeps the code-point hazard live across the whole length range
	// instead of only in the hand-written rows.
	alphabets := []struct {
		name string
		al   []rune
	}{
		{"bin", []rune("ab")},
		{"quad", []rune("abcd")},
		{"wide", []rune("abcdefghijklmnopqrstuvwxyz0123456789")},
		{"uni", []rune("ab" + eacute + cjkHan + grin + combAcc)},
	}
	for _, ab := range alphabets {
		r := &rng{s: 4242}
		for i := 0; i < 30; i++ {
			na := 1 + int(r.next()%90)
			nb := 1 + int(r.next()%90)
			add(fmt.Sprintf("random/%s/%02d", ab.name, i), r.str(ab.al, na), r.str(ab.al, nb))
		}
	}

	// --- isjunk, which the exported Ratio never passes ----------------
	//
	// Ratio is isjunk=None, under which bjunk is empty and
	// find_longest_match's two junk-extension loops are unreachable. They
	// are ported, so they get a witness: these rows drive NewMatcher with
	// a real predicate and compare against CPython doing the same. The
	// space-is-junk predicate is difflib's own docstring example.
	junkRows := [][3]string{
		{"doc space junk", " abcd", "abcd abcd"},
		{"space junk sentence", "archive x is blocked", "archive x is blocked."},
		{"space junk shifted", "the archive x is blocked", "archive x is blocked"},
		{"space junk both ends", " abc ", " abc "},
		{"space junk runs", "a   b   c", "a b c"},
		{"space junk none present", "abcdef", "abcdeg"},
		{"vowel junk", "the archive is blocked", "the archives are blocked"},
		{"vowel junk long", strings.Repeat("abcde ", 20), strings.Repeat("abcde ", 19) + "abcdz "},
		{"unicode junk", "caf" + eacute + " x", "caf" + eacute + " y"},
		{"cjk junk", cjkHan + " " + cjkZi, cjkHan + "  " + cjkZi},
	}
	for _, row := range junkRows {
		for _, j := range []string{" ", "aeiou", " " + cjkHan, "zq"} {
			cs = append(cs, diffCase{
				name: fmt.Sprintf("junk/%s/[%s]", row[0], j),
				a:    row[1], b: row[2],
				junk: junkOf(j), autojunk: true,
			})
		}
	}
	// isjunk together with a b long enough for the popularity purge, so
	// the "junk keys are already gone when the popular loop runs" seam in
	// __chain_b is exercised rather than assumed.
	{
		r := &rng{s: 31337}
		b := r.str([]rune("abc de"), 320)
		a := r.str([]rune("abc de"), 300)
		for _, j := range []string{" ", "a", " a"} {
			cs = append(cs, diffCase{
				name: fmt.Sprintf("junkpopular/[%s]", j),
				a:    a, b: b, junk: junkOf(j), autojunk: true,
			})
		}
	}

	// --- autojunk=False twins -----------------------------------------
	//
	// Paired with their autojunk=True selves above by name, so the test
	// can assert that at least one pair actually DISAGREES — i.e. that
	// the heuristic changed an answer somewhere in this corpus rather
	// than merely running.
	var twins []diffCase
	for _, c := range cs {
		if c.junk != nil || len([]rune(c.b)) < 200 {
			continue
		}
		twins = append(twins, diffCase{
			name: "noautojunk/" + c.name, a: c.a, b: c.b, junk: c.junk, autojunk: false,
		})
	}
	cs = append(cs, twins...)

	return cs
}

// argFile writes v as JSON to a file under the test's own temp dir and
// returns the path, for the probe to read as argv[1].
//
// pyprobe.Arg would put the whole corpus on the command line, and a
// 300-case corpus is past this box's ARG_MAX — the first run of this test
// died with "argument list too long" from fork/exec, which is a probe
// that never ran and would have been a skip under a weaker harness. The
// probe is still read-only: it opens this file and writes nothing, so it
// sets no Workspace and trips no live-workspace guard.
func argFile(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	p := filepath.Join(t.TempDir(), "cases.json")
	if err := os.WriteFile(p, b, 0o600); err != nil {
		t.Fatal(err)
	}
	return p
}

// runProbe hands the whole corpus to one interpreter. There is no
// per-case state in SequenceMatcher's constructor path, so 300+ python3
// spawns would buy nothing.
func runProbe(t *testing.T, cs []diffCase) map[string]pyResult {
	t.Helper()
	wire := make([]wireCase, 0, len(cs))
	for _, c := range cs {
		wire = append(wire, wireCase{Name: c.name, A: c.a, B: c.b, Junk: c.junk, Autojunk: c.autojunk})
	}
	probe := pyprobe.Probe{Stdlib: true}
	var got []pyResult
	probe.RunJSON(t, pyRatioSrc, &got, argFile(t, wire))
	if len(got) != len(cs) {
		t.Fatalf("probe returned %d results for %d cases", len(got), len(cs))
	}
	out := make(map[string]pyResult, len(got))
	for _, r := range got {
		out[r.Name] = r
	}
	return out
}

func mustBits(t *testing.T, s string) uint64 {
	t.Helper()
	u, err := strconv.ParseUint(s, 10, 64)
	if err != nil {
		t.Fatalf("probe emitted a bit pattern Go cannot parse (%q): %v", s, err)
	}
	return u
}

func TestRatioAgainstCPython(t *testing.T) {
	cs := corpus()

	// Rule: a corpus is only worth what it separates, so its own shape is
	// checked before anything is compared.
	if len(cs) < 300 {
		t.Fatalf("corpus has %d cases, floor is 300", len(cs))
	}
	seenName := map[string]bool{}
	for _, c := range cs {
		if seenName[c.name] {
			t.Fatalf("duplicate case name %q — one row is silently shadowing another", c.name)
		}
		seenName[c.name] = true
	}

	py := runProbe(t, cs)

	// Counters for the anti-vacuity floors. Every one of these is
	// asserted below with a stated floor; a count of zero means the
	// corpus cannot distinguish a working implementation from a broken
	// one on that axis, which is a failure of the test, not of the code.
	var (
		distinct    = map[uint64]bool{}
		above85     int
		below85     int
		exactlyOne  int
		exactlyZero int
		lenB        = map[int]int{}
		popular     int
		junky       int
		byteDiffers int
		nonASCII    int
		extend      int
		junkExtend  int
		merged      int
		multiBlock  int
		emptyBoth   int
		junkCases   int
		noAutojunk  int
	)
	// ratios by name, so the autojunk twins can be compared to their
	// originals afterwards, using CPython's numbers on both sides.
	pyBits := map[string]uint64{}

	for _, c := range cs {
		c := c
		want, ok := py[c.name]
		if !ok {
			t.Fatalf("no CPython result for case %q", c.name)
		}
		var isjunk func(rune) bool
		if c.junk != nil {
			j := *c.junk
			isjunk = func(r rune) bool { return strings.ContainsRune(j, r) }
			junkCases++
		}
		if !c.autojunk {
			noAutojunk++
		}
		m := NewMatcher(isjunk, c.a, c.b, c.autojunk)
		gotRatio := m.Ratio()
		gotBits := math.Float64bits(gotRatio)
		wantBits := mustBits(t, want.Bits)
		pyBits[c.name] = wantBits

		if gotBits != wantBits {
			t.Errorf("%s: ratio bits differ\n  a=%q\n  b=%q\n  junk=%v autojunk=%v\n"+
				"  CPython %s (bits %d)\n  Go      %v (bits %d)",
				c.name, c.a, c.b, c.junk, c.autojunk, want.Repr, wantBits, gotRatio, gotBits)
		}

		// The sequence lengths CPython saw. A byte-indexed port fails
		// here first and with a message that names the cause, before the
		// ratio comparison turns it into an arithmetic mystery.
		if want.LA != len(m.a) || want.LB != len(m.b) {
			t.Errorf("%s: code-point lengths differ — CPython len(a)=%d len(b)=%d, Go %d/%d "+
				"(a byte-indexed port reads %d/%d)",
				c.name, want.LA, want.LB, len(m.a), len(m.b), len(c.a), len(c.b))
		}

		// The matching blocks in their own right: the adjacent-merge pass
		// moves size between triples without changing their sum, so no
		// ratio can witness it.
		blocks := m.MatchingBlocks()
		if len(blocks) != len(want.Blocks) {
			t.Errorf("%s: %d matching blocks, CPython %d\n  Go      %v\n  CPython %v",
				c.name, len(blocks), len(want.Blocks), blocks, want.Blocks)
		} else {
			for i, wb := range want.Blocks {
				g := blocks[i]
				if g.A != wb[0] || g.B != wb[1] || g.Size != wb[2] {
					t.Errorf("%s: block %d is (%d,%d,%d), CPython (%d,%d,%d)",
						c.name, i, g.A, g.B, g.Size, wb[0], wb[1], wb[2])
				}
			}
		}
		// __chain_b's two purges, compared directly rather than only
		// through their effect on the ratio.
		if len(m.bpopular) != want.NPopular {
			t.Errorf("%s: %d popular elements, CPython %d", c.name, len(m.bpopular), want.NPopular)
		}
		if len(m.bjunk) != want.NJunk {
			t.Errorf("%s: %d junk elements, CPython %d", c.name, len(m.bjunk), want.NJunk)
		}

		// The exported one-call contract must be the isjunk=None,
		// autojunk=True matcher and nothing else.
		if c.junk == nil && c.autojunk {
			if fb := math.Float64bits(Ratio(c.a, c.b)); fb != wantBits {
				t.Errorf("%s: package Ratio bits %d, CPython %d (%s)", c.name, fb, wantBits, want.Repr)
			}
		}

		distinct[wantBits] = true
		w := math.Float64frombits(wantBits)
		if w >= 0.85 {
			above85++
		} else {
			below85++
		}
		if w == 1.0 {
			exactlyOne++
		}
		if w == 0.0 {
			exactlyZero++
		}
		lenB[want.LB]++
		if want.NPopular > 0 {
			popular++
		}
		if want.NJunk > 0 {
			junky++
		}
		if len(blocks) >= 4 {
			multiBlock++
		}
		if c.a == "" && c.b == "" {
			emptyBoth++
		}
		if m.extendSteps > 0 {
			extend++
		}
		if m.junkExtendSteps > 0 {
			junkExtend++
		}
		if m.mergeSteps > 0 {
			merged++
		}
		if len(c.a) != len([]rune(c.a)) || len(c.b) != len([]rune(c.b)) {
			nonASCII++
			if math.Float64bits(byteRatio(c.a, c.b)) != math.Float64bits(Ratio(c.a, c.b)) &&
				c.junk == nil && c.autojunk {
				byteDiffers++
			}
		}
	}

	// --- anti-vacuity floors ------------------------------------------
	floors := []struct {
		what  string
		count int
		floor int
		why   string
	}{
		{"distinct ratio values", len(distinct), 40,
			"a corpus that reaches few values cannot separate implementations"},
		{"cases at or above the caller's 0.85 threshold", above85, 5,
			"the caller's comparison is >= 0.85; both sides of it must be reached"},
		{"cases below 0.85", below85, 5, "ditto, from the other side"},
		{"cases whose ratio is exactly 1.0", exactlyOne, 5,
			"identical sequences, and _calculate_ratio's length==0 arm"},
		{"cases whose ratio is exactly 0.0", exactlyZero, 1,
			"no matching block at all is its own arm of the queue loop"},
		{"cases where CPython pruned popular elements", popular, 5,
			"the autojunk heuristic must actually engage, not merely be reachable"},
		{"cases where CPython found junk elements", junky, 5,
			"__chain_b's isjunk purge is skipped entirely under isjunk=None"},
		{"cases running the non-junk extension loops", extend, 1,
			"those loops are how a match reaches through a pruned popular element"},
		{"cases running the junk extension loops", junkExtend, 1,
			"unreachable under isjunk=None, so only the isjunk rows can witness them"},
		{"cases collapsing adjacent blocks", merged, 1,
			"the merge pass cannot be witnessed by any ratio; only by MatchingBlocks"},
		{"cases with four or more matching blocks", multiBlock, 5,
			"a single-block corpus never exercises the queue's recursion"},
		{"non-ASCII cases", nonASCII, 20, "code-point indexing is the headline hazard"},
		{"non-ASCII cases where a byte-indexed port would differ", byteDiffers, 5,
			"proves the hazard is observable in this corpus, not merely present"},
		{"cases with an isjunk predicate", junkCases, 20, "isjunk is a separate code path"},
		{"cases with autojunk=False", noAutojunk, 5,
			"the heuristic's off-state is the control for its on-state"},
		{"empty-vs-empty cases", emptyBoth, 1, "the only route to `return 1.0`"},
		{"cases with len(b) == 198", lenB[198], 1, "below the autojunk gate"},
		{"cases with len(b) == 199", lenB[199], 1, "one below the autojunk gate"},
		{"cases with len(b) == 200", lenB[200], 1, "exactly at the autojunk gate"},
		{"cases with len(b) == 201", lenB[201], 1, "one above the autojunk gate"},
	}
	for _, f := range floors {
		if f.count < f.floor {
			t.Errorf("ANTI-VACUITY: %s = %d, floor %d — %s", f.what, f.count, f.floor, f.why)
		}
	}

	// The strongest autojunk floor: somewhere in this corpus the
	// heuristic must have CHANGED an answer. Both numbers come from
	// CPython, so this is a statement about difflib, not about the port.
	changed := 0
	for name, bits := range pyBits {
		orig, ok := pyBits[strings.TrimPrefix(name, "noautojunk/")]
		if !ok || !strings.HasPrefix(name, "noautojunk/") {
			continue
		}
		if bits != orig {
			changed++
		}
	}
	if changed < 1 {
		t.Errorf("ANTI-VACUITY: the autojunk heuristic changed %d answers in this corpus; "+
			"with no such case, deleting the whole popularity purge would still pass", changed)
	}

	t.Logf("corpus=%d distinct=%d above85=%d below85=%d popular=%d junky=%d "+
		"extend=%d junkExtend=%d merged=%d nonASCII=%d byteDiffers=%d autojunkChanged=%d",
		len(cs), len(distinct), above85, below85, popular, junky,
		extend, junkExtend, merged, nonASCII, byteDiffers, changed)
}

// TestFindLongestMatchWindowsAgainstCPython drives find_longest_match
// directly over sub-windows.
//
// MatchingBlocks only ever calls it on windows the recursion produced, so
// three of its guards — `j < blo`'s continue, `j >= bhi`'s break, and the
// four extension loops' `> alo` / `< ahi` bounds — are reached there only
// incidentally. Calling it on windows chosen to straddle those bounds is
// what makes each one separable.
func TestFindLongestMatchWindowsAgainstCPython(t *testing.T) {
	const src = `
import json, sys, difflib
with open(sys.argv[1], encoding="utf-8") as fh:
    cases = json.load(fh)
out = []
for c in cases:
    junk = c["junk"]
    isjunk = None if junk is None else (lambda ch, s=junk: ch in s)
    sm = difflib.SequenceMatcher(isjunk, c["a"], c["b"], c["autojunk"])
    m = sm.find_longest_match(c["alo"], c["ahi"], c["blo"], c["bhi"])
    out.append({"name": c["name"], "a": m.a, "b": m.b, "size": m.size})
print(json.dumps(out))
`
	// The wire form is a map rather than a struct: four int fields with
	// distinct json tags would be four separate lines of boilerplate, and
	// the map reads as the JSON the probe parses.
	type wire = map[string]any
	type winLoc struct {
		name               string
		a, b               string
		junk               *string
		autojunk           bool
		alo, ahi, blo, bhi int
	}

	var (
		cases []wire
		locs  []winLoc
	)
	addWin := func(name, a, b string, junk *string, autojunk bool, alo, ahi, blo, bhi int) {
		cases = append(cases, wire{
			"name": name, "a": a, "b": b, "junk": junk, "autojunk": autojunk,
			"alo": alo, "ahi": ahi, "blo": blo, "bhi": bhi,
		})
		locs = append(locs, winLoc{name, a, b, junk, autojunk, alo, ahi, blo, bhi})
	}

	base := "abcabcabcxyzabc"
	other := "zzabcabcqqabcxyzabcww"
	for _, w := range [][4]int{
		{0, len(base), 0, len(other)},
		{0, 5, 0, 5},
		{3, 12, 2, 14},
		{1, 4, 5, 9},
		{0, 1, 0, 1},
		{2, 2, 0, 5},    // empty a window
		{0, 5, 3, 3},    // empty b window
		{6, 15, 10, 21}, // both windows shifted right
		{0, len(base), 8, 12},
		{5, 9, 0, len(other)},
	} {
		addWin(fmt.Sprintf("win/base %v", w), base, other, nil, true, w[0], w[1], w[2], w[3])
	}
	sp := junkOf(" ")
	docA, docB := " abcd", "abcd abcd"
	for _, w := range [][4]int{
		{0, 5, 0, 9},
		{0, 5, 4, 9},
		{1, 5, 0, 4},
		{0, 3, 0, 9},
	} {
		addWin(fmt.Sprintf("win/junk %v", w), docA, docB, sp, true, w[0], w[1], w[2], w[3])
		addWin(fmt.Sprintf("win/nojunk %v", w), docA, docB, nil, true, w[0], w[1], w[2], w[3])
	}
	// A window over a long, popularity-pruned b, where the only way to a
	// match is the non-junk extension loops.
	longB := "prefix" + strings.Repeat("e", 30) + "suffix" + strings.Repeat("q", 220)
	longA := "prefix" + strings.Repeat("e", 30) + "suffix"
	for _, w := range [][4]int{
		{0, len(longA), 0, len(longB)},
		{0, len(longA), 0, 42},
		{3, len(longA), 3, 40},
	} {
		addWin(fmt.Sprintf("win/popular %v", w), longA, longB, nil, true, w[0], w[1], w[2], w[3])
		addWin(fmt.Sprintf("win/popular-off %v", w), longA, longB, nil, false, w[0], w[1], w[2], w[3])
	}
	// Unicode, so the window bounds themselves are code-point indices.
	uniA := cjkHan + eacute + grin + cjkZi + "ab"
	uniB := "x" + cjkHan + eacute + grin + cjkZi + "y" + grin
	for _, w := range [][4]int{
		{0, 6, 0, 7},
		{1, 4, 1, 6},
		{0, 3, 2, 7},
	} {
		addWin(fmt.Sprintf("win/uni %v", w), uniA, uniB, nil, true, w[0], w[1], w[2], w[3])
	}

	// An exhaustive sweep of every window over a small overlapping pair.
	// The hand-picked windows above are chosen to straddle a named guard;
	// this sweep is the control for them, and it is what reaches the
	// combinations nobody thought to name — in particular every empty
	// window and every window that clips a match at exactly one end.
	sweepA, sweepB := "abcab", "bcabcb"
	for alo := 0; alo <= len(sweepA); alo++ {
		for ahi := alo; ahi <= len(sweepA); ahi++ {
			for blo := 0; blo <= len(sweepB); blo++ {
				for bhi := blo; bhi <= len(sweepB); bhi++ {
					addWin(fmt.Sprintf("win/sweep %d-%d/%d-%d", alo, ahi, blo, bhi),
						sweepA, sweepB, nil, true, alo, ahi, blo, bhi)
				}
			}
		}
	}

	probe := pyprobe.Probe{Stdlib: true}
	var got []struct {
		Name string `json:"name"`
		A    int    `json:"a"`
		B    int    `json:"b"`
		Size int    `json:"size"`
	}
	probe.RunJSON(t, src, &got, argFile(t, cases))
	if len(got) != len(cases) {
		t.Fatalf("probe returned %d results for %d windows", len(got), len(cases))
	}

	nonzero, zero, junkExt, plainExt := 0, 0, 0, 0
	for i, want := range got {
		l := locs[i]
		if want.Name != l.name {
			t.Fatalf("result %d is %q, expected %q — the probe reordered its output", i, want.Name, l.name)
		}
		var isjunk func(rune) bool
		if l.junk != nil {
			j := *l.junk
			isjunk = func(r rune) bool { return strings.ContainsRune(j, r) }
		}
		m := NewMatcher(isjunk, l.a, l.b, l.autojunk)
		g := m.FindLongestMatch(l.alo, l.ahi, l.blo, l.bhi)
		if g.A != want.A || g.B != want.B || g.Size != want.Size {
			t.Errorf("%s: Go (%d,%d,%d), CPython (%d,%d,%d)",
				l.name, g.A, g.B, g.Size, want.A, want.B, want.Size)
		}
		if want.Size > 0 {
			nonzero++
		} else {
			zero++
		}
		if m.junkExtendSteps > 0 {
			junkExt++
		}
		if m.extendSteps > 0 {
			plainExt++
		}
	}
	if len(cases) < 30 {
		t.Errorf("ANTI-VACUITY: %d windows, floor 30", len(cases))
	}
	if nonzero < 10 {
		t.Errorf("ANTI-VACUITY: %d windows found a match, floor 10", nonzero)
	}
	if zero < 2 {
		t.Errorf("ANTI-VACUITY: %d windows found no match, floor 2 — "+
			"the `if no blocks match, return (alo, blo, 0)` path", zero)
	}
	if junkExt < 1 {
		t.Errorf("ANTI-VACUITY: the junk extension loops ran in %d windows, floor 1", junkExt)
	}
	if plainExt < 1 {
		t.Errorf("ANTI-VACUITY: the non-junk extension loops ran in %d windows, floor 1", plainExt)
	}
	t.Logf("windows=%d nonzero=%d zero=%d junkExt=%d plainExt=%d",
		len(cases), nonzero, zero, junkExt, plainExt)
}
