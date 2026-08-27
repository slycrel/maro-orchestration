package artifactcheck

import (
	"math"
	"math/rand"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The `datetime.fromisoformat` grammar is too large to pin with a fixture
// list, and a fixture list is the wrong instrument for it anyway: the
// defect this replaced was a port that answered confidently on eleven
// shapes and refused everything else, and any hand-written table would
// have been drawn from the same eleven shapes.
//
// So the corpus is GENERATED, and CPython answers it. Two generators, both
// deterministic:
//
//   - a structured sweep over the grammar's own axes — every date form
//     crossed with every time width, separator, fraction and offset shape,
//     including the ones measured to be errors;
//   - random strings over the grammar's alphabet, plus point mutations of
//     valid stamps, which is what found every rule the structured sweep
//     assumed rather than tested (the separator-less basic fraction, the
//     unconsumed trailing colon, the one-stray-character tolerance).
//
// The seed is fixed so a failure is reproducible, and `rand.New` is used
// rather than the global source so a parallel test cannot perturb it.
//
// This is the only test in the package that generates its own inputs. The
// justification is that the reference implementation is available to
// answer them, which makes generation free of the usual problem — there is
// no expected value to write down.
func TestTheISOGrammarAgreesWithCPythonAcrossAGeneratedCorpus(t *testing.T) {
	corpus := isoCorpus()
	if len(corpus) < 5000 {
		t.Fatalf("corpus collapsed to %d inputs; a small corpus passes for "+
			"the wrong reason", len(corpus))
	}

	// The corpus goes to the probe in CHUNKS. Everything else in this
	// package sends one argv and gets one answer, and that is what this
	// tried first: the whole corpus is a few megabytes of JSON and execve
	// returns E2BIG, which pyprobe reports as "argument list too long"
	// rather than as a failing comparison. Shrinking the corpus to fit
	// would have traded the coverage for the convenience, so the calls are
	// batched instead — a handful of interpreter starts, whole corpus.
	probe := pyprobe.Probe{Marker: "artifact_check.py", Workspace: t.TempDir()}
	const chunk = 4000
	var raw []any
	for start := 0; start < len(corpus); start += chunk {
		end := start + chunk
		if end > len(corpus) {
			end = len(corpus)
		}
		var got []map[string]any
		probe.RunJSON(t, acPySrc, &got, pyprobe.Arg(t,
			[]map[string]any{{"kind": "parse_iso", "values": corpus[start:end]}}))
		if len(got) != 1 {
			t.Fatalf("expected one answer for [%d,%d), got %d", start, end, len(got))
		}
		if e, bad := got[0]["err"]; bad {
			t.Fatalf("CPython could not answer [%d,%d): %v", start, end, e)
		}
		vals, _ := got[0]["ok"].([]any)
		if len(vals) != end-start {
			t.Fatalf("CPython answered %d of %d inputs in [%d,%d)",
				len(vals), end-start, start, end)
		}
		raw = append(raw, vals...)
	}
	if len(raw) != len(corpus) {
		t.Fatalf("CPython answered %d of %d inputs", len(raw), len(corpus))
	}

	// A corpus CPython rejects entirely would pass against a port that
	// rejects everything, so the accept count is asserted too.
	accepted, bad := 0, 0
	for i, want := range raw {
		haveV, haveOK := parseISO(corpus[i])
		if want == nil {
			if haveOK {
				bad++
				if bad <= 12 {
					t.Errorf("%q: CPython raises, the port answered %v",
						corpus[i], haveV)
				}
			}
			continue
		}
		accepted++
		wantV, isNum := want.(float64)
		if !isNum {
			t.Fatalf("%q: CPython returned a %T, not a float", corpus[i], want)
		}
		if !haveOK {
			bad++
			if bad <= 12 {
				t.Errorf("%q: CPython gives %v, the port refused", corpus[i], wantV)
			}
			continue
		}
		// Bit equality, not a tolerance: the two `.timestamp()` formulas
		// this port reproduces differ from each other by an ulp, so a
		// tolerance would erase the thing being tested (L51).
		if math.Float64bits(haveV) != math.Float64bits(wantV) {
			bad++
			if bad <= 12 {
				t.Errorf("%q: CPython %v (%016x), port %v (%016x)", corpus[i],
					wantV, math.Float64bits(wantV), haveV, math.Float64bits(haveV))
			}
		}
	}
	if accepted < 500 {
		t.Errorf("CPython accepted only %d of %d inputs; a corpus that is "+
			"almost all errors cannot tell a working parser from a closed "+
			"door", accepted, len(corpus))
	}
	if bad > 12 {
		t.Errorf("... and %d more divergences", bad-12)
	}
	t.Logf("%d inputs, %d accepted by CPython, %d divergences",
		len(corpus), accepted, bad)
}

// isoCorpus builds the generated corpus. Deterministic by construction.
func isoCorpus() []string {
	dates := []string{
		"2026-08-22", "20260822", "2026-W34", "2026-W34-1", "2026W341",
		"2026W34", "2026-235", "2026235", "2026-02-29", "2024-02-29",
		"2026-13-01", "2026-00-10", "2026-08-00", "2026-08-32", "2025-W53-1",
		"2026-W53-1", "2026-W53-7", "2026-W00-1", "2026-W34-8", "2026-8-22",
		"2026-08-2", "999-08-22", "0001-01-01", "0001-01-02", "9999-12-31",
		"8709033", "87090330",
	}
	times := []string{
		"12", "123", "12:34", "1234", "12345", "12:34:56", "123456", "1234567",
		"12345678", "123456789", "1234567890123", "12:34:56.1",
		"12:34:56.123456", "12:34:56.1234567890", "12:34:56,123", "12:34:56.",
		"12:34:56.123_", "12:34:56.123456_", "12:34:56.123456__", "12:34:56_",
		"12:34:56__", "12:34:", "12:", "12_", "1:04:05", "24:00:00", "24:00",
		"24", "24:00:01", "12:60:00", "12:34:60", "99:00:00", "12:34.5",
		"12:34.", "1234.5", "123456.5", "12:34:561", "12:34:5612",
	}
	// The separator is one CODE POINT, so two multi-byte ones are in the
	// list: the corpus was ASCII-only in both halves and could not reach
	// the byte-vs-rune advance at all (r2 named this blind spot; the port
	// was refusing "2026-08-22é12:34", which CPython accepts).
	seps := []string{"T", "t", " ", "_", "X", ":", "9", ".", "é", "\U0001F600"}
	offs := []string{
		"", "Z", "z", "+05", "+0530", "+05:30", "-05:30", "+05:30:15",
		"-053015", "+05:30:15.123456", "+5:30", "+05:3", "+24:00", "-00:00",
		"+05:60", "+05:99", "+23:59:59", "+23:60", "+000000", "+05,30",
		"+05.30", "+053015.5",
	}

	seen := map[string]bool{}
	var out []string
	add := func(s string) {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	for _, d := range dates {
		add(d)
		for _, tm := range times {
			for _, sp := range []string{"T", " "} {
				for _, o := range []string{"", "Z", "+05:30"} {
					add(d + sp + tm + o)
				}
			}
		}
	}
	for _, sp := range seps {
		for _, o := range offs {
			add("2026-08-22" + sp + "12:34:56" + o)
			add("20260822" + sp + "123456" + o)
		}
	}
	for _, tm := range times {
		for _, o := range offs {
			add("2026-08-22T" + tm + o)
			add("20260822T" + tm + o)
		}
	}

	// The random half. The alphabet is the grammar's own characters plus
	// the near-misses that separate its rules: lowercase z and t, W, and
	// two characters ("x", "/") that belong to no rule at all.
	// NUL, systematically: CPython's parser reads the C string that
	// PyUnicode_AsUTF8AndSize hands it, and several of its "is the input
	// exhausted?" checks cannot tell an EMBEDDED NUL from the buffer's own
	// terminator. So a NUL is not just another stray character — at three
	// points it reads as end-of-input, and `2026-08-22T12:34:56\x00` parses
	// where `..._` does not. No alphabet above contains \x00, so the whole
	// class was invisible to 95,312 inputs (r3 MEDIUM).
	//
	// Swept rather than sampled: 0-3 NULs at EVERY position of each base,
	// because the tolerance is position-dependent and count-dependent in
	// ways no random draw would separate (one trailing NUL parses, two do
	// not — except after a six-digit fraction, where any number does).
	nulBases := []string{
		"2026-08-22", "2026-08-22T12", "2026-08-22T1234",
		"2026-08-22T12:34", "2026-08-22T12:34:56", "2026-08-22T123456",
		"2026-08-22T12:34:56.1", "2026-08-22T12:34:56.12345",
		"2026-08-22T12:34:56.123456", "2026-08-22T12:34:56.1234567",
		"2026-08-22T12:34:56Z", "2026-08-22T12:34:56+01:00",
		"2026-08-22T12:34:56.123456Z", "2026-08-22T12:34:56.123456+01:00",
		"20260822T123456", "2026-W34-1T12:34", "2026-08-22T12:34:56-05:30:15",
	}
	for _, b := range nulBases {
		for pos := 0; pos <= len(b); pos++ {
			for n := 1; n <= 3; n++ {
				add(b[:pos] + strings.Repeat("\x00", n) + b[pos:])
			}
		}
	}

	const alpha = "0123456789-:.,+TZtWz _wXx/\x00"
	rng := rand.New(rand.NewSource(20260826))
	pick := func() byte { return alpha[rng.Intn(len(alpha))] }
	for i := 0; i < 40000; i++ {
		b := make([]byte, rng.Intn(31))
		for j := range b {
			b[j] = pick()
		}
		add(string(b))
	}
	// A second random half over runes rather than bytes. Everything after
	// the separator is byte-oriented in CPython too, so a multi-byte
	// character there must be REFUSED, and refusing for the right reason is
	// as much a claim as accepting: these rows drive both.
	runeAlpha := []rune("0123456789-:.,+TZ é\U0001F600\u0345\uFF11\u0661")
	for i := 0; i < 20000; i++ {
		r := make([]rune, rng.Intn(20))
		for j := range r {
			r[j] = runeAlpha[rng.Intn(len(runeAlpha))]
		}
		add(string(r))
	}
	// And the shapes the rune-random half is unlikely to hit by accident:
	// a multi-byte character in each position that a rule looks at.
	for _, mb := range []string{"é", "\U0001F600", "\uFF11", "\u0661"} {
		add("2026-08-22" + mb + "12:34:56")
		add("2026-08-22T12:34:56" + mb)
		add("2026-08-22T12:34:56." + mb)
		add("2026-08-22T12:34:56.123456" + mb + "Z")
		add("2026-08-22T12:34:56" + mb + "Z")
		add("2026-08-22T12:34:5" + mb)
		add("2026-08-2" + mb)
		add(mb + "026-08-22")
		add("2026-08-22T" + mb + "2:34:56")
		add("2026-08-22T12:34:56+05:3" + mb)
	}
	bases := []string{
		"2026-08-22T12:34:56.123456+05:30", "20260822T123456Z",
		"2026-W34-1T12:34", "2026-08-22", "1970-01-01T00:00:00Z",
		"2262-04-11T23:47:16.854775Z", "9999-12-31T23:59:59.999999+23:59:59",
		"20260822T12345678", "2026-W34", "0002-01-01T00:00:00",
	}
	for i := 0; i < 40000; i++ {
		b := []byte(bases[rng.Intn(len(bases))])
		for k := rng.Intn(4) + 1; k > 0; k-- {
			if len(b) == 0 {
				break
			}
			j := rng.Intn(len(b))
			switch n := rng.Intn(10); {
			case n < 4:
				b[j] = pick()
			case n < 7:
				b = append(b[:j], b[j+1:]...)
			default:
				b = append(b[:j], append([]byte{pick()}, b[j:]...)...)
			}
		}
		add(string(b))
	}
	return out
}

// tzTimestampSrc asks CPython for `fromisoformat(s).timestamp()` in the
// zone the environment names, with an explicit tzset() so the answer does
// not depend on whether the C library re-reads TZ on its own.
const tzTimestampSrc = `
import json, sys, time
from datetime import datetime
time.tzset()
out = []
for s in json.loads(sys.argv[1]):
    try:
        out.append(datetime.fromisoformat(s).timestamp())
    except Exception:
        out.append(None)
sys.stdout.write(json.dumps(out))
`

// TestTheNaiveTimestampAgreesAtBothENDSOfTheYearRangeInEveryZone pins the
// r4 LOW: `_mktime` raises through its local() probe, which rebuilds a
// datetime from localtime() and so rejects a year outside 1..9999. That
// has TWO ends, and the port modelled one of them as a hardcoded
// `y == 1 && mo == 1 && d == 1`.
//
// Every other test in this package runs in the box's own zone, which is
// west of UTC — and west of UTC only the LOW end is reachable, because the
// offset pushes 9999-12-31T23:59:59 further into 9999 and 0001-01-01
// backwards into year 0. That is why 96,582 generated inputs could not see
// it. The zone is the axis the corpus does not have, so this test varies
// it: time.Local for the Go side, TZ for the probe, one process each way.
//
// Asia/Kolkata and Pacific/Apia are east of UTC (one of them by a
// half-hour, which also exercises a non-integral-hour offset); Denver and
// Los_Angeles are west; UTC is the boundary where neither end fails.
func TestTheNaiveTimestampAgreesAtBothENDSOfTheYearRangeInEveryZone(t *testing.T) {
	var inputs []string
	for _, hm := range []string{
		"00:00:00", "00:00:01", "05:29:59", "05:30:00", "05:30:01",
		"06:59:56", "07:00:00", "10:59:59", "11:00:00", "12:00:00",
		"13:00:00", "18:29:59", "18:30:00", "18:30:01", "23:59:59",
	} {
		inputs = append(inputs,
			"0001-01-01T"+hm, "0001-01-02T"+hm,
			"9999-12-30T"+hm, "9999-12-31T"+hm,
			// An ordinary stamp in each zone, so a port that refused
			// everything could not pass this test.
			"2026-08-22T"+hm)
	}
	inputs = append(inputs, "0001-01-01T23:59:59.999999",
		"9999-12-31T23:59:59.999999", "0001-01-01", "9999-12-31")

	origLocal := time.Local
	t.Cleanup(func() { time.Local = origLocal })

	sawRefusal, sawAnswer := 0, 0
	for _, zone := range []string{
		"UTC", "America/Denver", "America/Los_Angeles",
		"Asia/Kolkata", "Pacific/Apia", "Australia/Lord_Howe",
	} {
		loc, err := time.LoadLocation(zone)
		if err != nil {
			t.Skipf("this box has no tzdata for %s: %v", zone, err)
		}
		t.Run(zone, func(t *testing.T) {
			time.Local = loc
			t.Setenv("TZ", zone)
			var want []*float64
			pyprobe.Probe{Stdlib: true}.RunJSON(t, tzTimestampSrc, &want,
				pyprobe.Arg(t, inputs))
			if len(want) != len(inputs) {
				t.Fatalf("probe answered %d rows for %d inputs", len(want), len(inputs))
			}
			for i, s := range inputs {
				have, ok := parseISO(s)
				if want[i] == nil {
					sawRefusal++
					if ok {
						t.Errorf("%s %q: CPython raises, the port answered %v", zone, s, have)
					}
					continue
				}
				sawAnswer++
				if !ok {
					t.Errorf("%s %q: CPython gives %v, the port refused", zone, s, *want[i])
					continue
				}
				if math.Float64bits(have) != math.Float64bits(*want[i]) {
					t.Errorf("%s %q: port %v, CPython %v", zone, s, have, *want[i])
				}
			}
		})
	}
	// The guard: if no zone produced a refusal, the table has stopped
	// reaching the rule and would pass against a port with no guard at all.
	if sawRefusal == 0 {
		t.Error("no input in any zone reached _mktime's ValueError; this " +
			"test would pass against a port that never refuses")
	}
	if sawAnswer == 0 {
		t.Error("no input in any zone produced a timestamp")
	}
}
