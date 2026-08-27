package pyurl

import (
	"fmt"
	"sort"
	"strings"
	"testing"

	"golang.org/x/text/unicode/norm"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The NFKC table skew, measured rather than assumed.
//
// _checknetloc is the one place in this port that asks a UNICODE
// DATABASE a question, and the two runtimes are reading different
// editions of it: CPython 3.14.3's unicodedata is Unicode 16.0.0, and
// golang.org/x/text v0.21.0's tables are 15.0.0. Both numbers are
// asserted below rather than quoted, so a dependency bump that closes
// (or widens) the gap fails here instead of drifting.
//
// A skew in NFKC is not cosmetic the way the printability skew in
// pytext.Repr is. It decides whether a URL RAISES: a netloc containing a
// code point whose compatibility decomposition contains '/' is refused,
// and one whose decomposition does not is accepted with that host
// recorded as terrain.
//
// WHAT THIS SWEEPS, exactly: NFKC of every single code point, as a
// one-character string, over the whole assigned range. It does NOT
// sweep multi-character sequences, so a change to a COMPOSITION
// EXCLUSION or to a canonical ordering that is only visible across two
// adjacent code points would not be caught here. That is a stated limit
// of the measurement, not a claim that no such difference exists; the
// corpus in pyurl_diff_test.go is what covers real netlocs.
const pyNFKCProbe = `
import json, sys, unicodedata

changed = {}
for c in range(0x110000):
    if 0xD800 <= c <= 0xDFFF:   # surrogates are not str-able one at a time
        continue
    ch = chr(c)
    n = unicodedata.normalize('NFKC', ch)
    if n != ch:
        changed[str(c)] = n
print(json.dumps({"unidata": unicodedata.unidata_version, "changed": changed}))
`

type nfkcProbeOut struct {
	Unidata string            `json:"unidata"`
	Changed map[string]string `json:"changed"`
}

func TestNFKCTableSkewAgainstCPython(t *testing.T) {
	var got nfkcProbeOut
	pyprobe.Probe{Stdlib: true}.RunJSON(t, pyNFKCProbe, &got)

	if got.Unidata != "16.0.0" {
		t.Errorf("CPython's unidata_version is %q; this file's divergence "+
			"ledger was measured against 16.0.0 and needs re-measuring",
			got.Unidata)
	}
	if norm.Version != "15.0.0" {
		t.Errorf("x/text's unicode/norm.Version is %q; this file's divergence "+
			"ledger was measured against 15.0.0 and needs re-measuring",
			norm.Version)
	}
	if len(got.Changed) < 4000 {
		t.Fatalf("CPython reported only %d code points whose NFKC differs from "+
			"themselves; the sweep did not run", len(got.Changed))
	}

	// Both directions in one pass: walk every code point, ask each
	// engine, and record disagreements. Reading only CPython's list
	// would miss a code point Go normalizes and CPython leaves alone.
	pyOf := func(r rune) string {
		if v, ok := got.Changed[fmt.Sprint(int(r))]; ok {
			return v
		}
		return string(r)
	}
	type diff struct {
		r       rune
		py, go_ string
	}
	var diffs []diff
	for r := rune(0); r < 0x110000; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		g := norm.NFKC.String(string(r))
		if p := pyOf(r); p != g {
			diffs = append(diffs, diff{r, p, g})
		}
	}
	sort.Slice(diffs, func(i, j int) bool { return diffs[i].r < diffs[j].r })

	// Of those, the ones that MATTER to _checknetloc: a code point whose
	// normalization introduces one of /?#@: in one engine and not the
	// other flips a URL between "raises" and "has a host".
	gateChars := "/?#@:"
	var gateRelevant []diff
	for _, d := range diffs {
		if strings.ContainsAny(d.py, gateChars) != strings.ContainsAny(d.go_, gateChars) {
			gateRelevant = append(gateRelevant, d)
		}
	}

	// THE PINNED DIVERGENCE. Measured 2026-08-27 on this box, CPython
	// 3.14.3 (unidata 16.0.0) vs x/text v0.21.0 (unicode/norm 15.0.0):
	//
	//	single-code-point NFKC answers that differ:  36
	//	of which change the _checknetloc verdict:     0
	//
	// The 36 are exactly the CONTIGUOUS range U+1CCD6..U+1CCF9 — the
	// Outlined Latin letters and digits, a block Unicode 16.0 ADDED.
	// Both engines' answers, from the run that produced this row:
	//
	//	U+1CCD6  CPython NFKC -> "A"   Go NFKC -> "\U0001ccd6" (unchanged)
	//	U+1CCEF  CPython NFKC -> "Z"   Go NFKC -> "\U0001ccef" (unchanged)
	//	U+1CCF0  CPython NFKC -> "0"   Go NFKC -> "\U0001ccf0" (unchanged)
	//	U+1CCF9  CPython NFKC -> "9"   Go NFKC -> "\U0001ccf9" (unchanged)
	//
	// CPython 16 knows their compatibility decompositions; x/text 15 has
	// never heard of them and returns an unknown code point unchanged.
	// It is a real, measured, one-directional divergence and it is NOT
	// papered over.
	//
	// WHY IT DOES NOT REACH THE PORT'S ANSWER, and this is the part that
	// had to be checked rather than asserted: all 36 decompose to ASCII
	// LETTERS AND DIGITS, and none to one of `/?#@:`. _checknetloc's two
	// branches are (a) `n == netloc2` -> return, and (b) normalized text
	// contains a gate character -> raise. Go takes (a); CPython falls
	// through (a) and then finds no gate character and returns anyway.
	// Same verdict, by two different routes — which is why the
	// gate-relevant count, not the raw count, is the number that would
	// force a divergence row in the port itself. The URL-level fixture
	// in the corpus ("http://ex\U0001CCD6ample.com/") is the direct
	// evidence: both engines answer with a host, not a raise.
	//
	// Both numbers are pinned as EXACT rather than as ceilings. A future
	// x/text bump is expected to take the 36 to 0; that FAILS here, and
	// the row gets deleted deliberately rather than silently going
	// stale. A gate-relevant count that ever leaves 0 is a real port
	// divergence and has to be reported, not re-pinned.
	const wantDiffs, wantGateRelevant = 36, 0

	// The pinned 36 are a contiguous block; assert the SHAPE too, so a
	// bump that removes those 36 and introduces 36 different ones does
	// not pass on the count alone.
	if len(diffs) == wantDiffs && wantDiffs > 0 {
		lo, hi := diffs[0].r, diffs[len(diffs)-1].r
		if lo != 0x1CCD6 || hi != 0x1CCF9 || int(hi-lo)+1 != len(diffs) {
			t.Errorf("the NFKC skew is still %d code points but is no longer "+
				"the contiguous block U+1CCD6..U+1CCF9: it now runs "+
				"U+%04X..U+%04X", len(diffs), lo, hi)
		}
	}

	if len(diffs) != wantDiffs || len(gateRelevant) != wantGateRelevant {
		t.Errorf("NFKC skew changed: %d differing code points (pinned %d), "+
			"%d of them gate-relevant (pinned %d)",
			len(diffs), wantDiffs, len(gateRelevant), wantGateRelevant)
		shown := diffs
		if len(shown) > 40 {
			shown = shown[:40]
		}
		for _, d := range shown {
			t.Logf("  U+%04X: CPython NFKC -> %q, Go NFKC -> %q", d.r, d.py, d.go_)
		}
		if len(diffs) > len(shown) {
			t.Logf("  … and %d more", len(diffs)-len(shown))
		}
	}

	// Anti-vacuity for the sweep itself: the loop must have found the
	// decompositions the corpus relies on, or it measured nothing.
	for _, r := range []rune{0x2100, 0x2105, 0xFF1F, 0xFF03, 0xFF20, 0xFF1A} {
		if norm.NFKC.String(string(r)) == string(r) {
			t.Errorf("U+%04X normalizes to itself in Go — the corpus fixture "+
				"built on it is measuring nothing", r)
		}
		if pyOf(r) == string(r) {
			t.Errorf("U+%04X normalizes to itself in CPython — same", r)
		}
	}
}
