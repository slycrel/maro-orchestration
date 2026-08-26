package artifactcheck

import (
	"math"
	"math/rand"
	"testing"

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
	seps := []string{"T", "t", " ", "_", "X", ":", "9", "."}
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
	const alpha = "0123456789-:.,+TZtWz _wXx/"
	rng := rand.New(rand.NewSource(20260826))
	pick := func() byte { return alpha[rng.Intn(len(alpha))] }
	for i := 0; i < 40000; i++ {
		b := make([]byte, rng.Intn(31))
		for j := range b {
			b[j] = pick()
		}
		add(string(b))
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
