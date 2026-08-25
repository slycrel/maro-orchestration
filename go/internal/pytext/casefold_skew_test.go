package pytext

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// casefold is a dedup key on a shared file, so "close enough" is not a
// thing here: two runtimes that fold one code point differently keep two
// copies of one bullet, forever, and neither can see why.
//
// Every pin below derives its expectation from CPython on this box. None
// of them restates the table.

// pythonCaseFoldMap returns, for every code point CPython casefolds to
// something else, the result.
func pythonCaseFoldMap(t *testing.T) map[rune]string {
	t.Helper()
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import json,sys;print(json.dumps({c:chr(c).casefold() "+
			"for c in range(0x110000) if chr(c).casefold()!=chr(c)}))"))
	var raw map[string]string
	if err := json.Unmarshal(out, &raw); err != nil {
		t.Fatalf("decoding CPython output: %v", err)
	}
	m := make(map[rune]string, len(raw))
	for k, v := range raw {
		var c int
		if _, err := jsonNumber(k, &c); err != nil {
			t.Fatalf("bad key %q: %v", k, err)
		}
		m[rune(c)] = v
	}
	if len(m) == 0 {
		t.Fatal("CPython casefolds nothing; the probe is broken")
	}
	return m
}

// The sweep, both directions: a code point CPython folds that we do not,
// and a code point we fold that CPython leaves alone, are both failures.
func TestCaseFoldAgreesWithCPythonOnEveryCodePoint(t *testing.T) {
	want := pythonCaseFoldMap(t)
	var missed, extra, checked int
	for c := 0; c < 0x110000; c++ {
		r := rune(c)
		if !utf8Valid(r) {
			continue
		}
		checked++
		got := CaseFold(string(r))
		exp, folded := want[r]
		if !folded {
			exp = string(r)
		}
		if got == exp {
			continue
		}
		if folded {
			missed++
			if missed <= 8 {
				t.Errorf("missed fold %s", shortf(c, got, exp))
			}
		} else {
			extra++
			if extra <= 8 {
				t.Errorf("folded a code point CPython leaves alone %s",
					shortf(c, got, exp))
			}
		}
	}
	if checked < 0x100000 {
		t.Fatalf("only %d code points reached the comparison; the sweep is "+
			"not sweeping", checked)
	}
	if missed+extra > 0 {
		t.Fatalf("%d missed, %d extra out of %d code points", missed, extra, checked)
	}
}

// lowerRune duplicates Lower's per-rune behaviour by hand. That is a real
// liability — two implementations of one mapping drift, and the drift
// would be invisible because each has its own passing sweep. So this pin
// re-derives one from the other at every code point.
//
// Lower is the reference because it is the one with the CPython sweep
// behind it.
func TestLowerRuneStillAgreesWithLowerAtEveryCodePoint(t *testing.T) {
	var bad, checked int
	for c := 0; c < 0x110000; c++ {
		r := rune(c)
		if !utf8Valid(r) {
			continue
		}
		checked++
		// Lower's sigma pass needs a preceding cased rune, so a
		// single-rune input cannot reach it — which is exactly the
		// property lowerRune relies on, and exactly what this asserts.
		if got, want := lowerRune(r), Lower(string(r)); got != want {
			bad++
			if bad <= 8 {
				t.Errorf("lowerRune has drifted from Lower %s",
					shortf(c, got, want))
			}
		}
	}
	if checked < 0x100000 {
		t.Fatalf("only %d code points checked; the sweep is not sweeping",
			checked)
	}
	if bad > 0 {
		t.Fatalf("lowerRune and Lower disagree at %d of %d code points",
			bad, checked)
	}
}

// The table is a snapshot of a version skew. When Go's unicode tables
// catch up, an entry stops carrying weight, and a dead entry is worse
// than no entry: it looks like coverage. This fails when one goes inert
// so the literal gets DELETED rather than quietly rotting.
func TestTheCaseFoldTableIsStillCarryingWeight(t *testing.T) {
	if len(caseFoldTable) == 0 {
		t.Fatal("the table is empty; this detector is dead")
	}
	var inert []string
	for r, folded := range caseFoldTable {
		if lowerRune(r) == folded {
			inert = append(inert, strconv16(int(r)))
		}
	}
	if len(inert) > 0 {
		t.Errorf("%d caseFoldTable entries now match lowerRune and are dead "+
			"weight — delete them: %s", len(inert), strings.Join(inert, " "))
	}
}

// The reason this function exists at all. casefold has no context rules,
// so a final sigma folds to a PLAIN sigma — where Lower, correctly, gives
// ς. Getting this backwards means every Greek bullet ending in Σ dedups
// against itself in one runtime and not the other.
//
// The words are differentials too: the expectation comes from CPython,
// not from my reading of the rule.
func TestCaseFoldMatchesCPythonOnWordsWhereItDivergesFromLower(t *testing.T) {
	words := []string{
		"ΟΔΟΣ", "ΟΔΟΣ ΜΕΓΑΣ", "ΑΣΣ deploy", "Σ", "ς", "σ",
		"ß", "ẞ", "STRASSE", "Straße",
		"ﬄ", "ﬁle", "İstanbul", "ʰΣ",
		"- Prefer THE cheap path *(from evolver:x)*",
	}
	out := []byte(pyprobe.Probe{Stdlib: true}.Run(t,
		"import json,sys;print(json.dumps([w.casefold() for w in "+
			"json.loads(sys.argv[1])]))", mustJSON(t, words)))
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("decoding CPython output: %v", err)
	}
	if len(want) != len(words) {
		t.Fatalf("CPython returned %d results for %d words", len(want), len(words))
	}
	var divergent int
	for i, w := range words {
		if got := CaseFold(w); got != want[i] {
			t.Errorf("CaseFold(%q) = %q, CPython says %q", w, got, want[i])
		}
		if Lower(w) != want[i] {
			divergent++
		}
	}
	// Without this the whole table could be "CaseFold == Lower" and every
	// case above would still pass.
	if divergent == 0 {
		t.Fatal("not one word in this table distinguishes CaseFold from " +
			"Lower — the case that motivates this function is untested")
	}
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(b)
}
