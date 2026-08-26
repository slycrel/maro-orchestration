package metrics

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The r4 round's fixtures. Each one pins ONE finding, and each was written
// after measuring CPython rather than from the review's prose — three of
// the six findings named a mechanism correctly and an example that did not
// reproduce, so the fixture is the adjudication, not the report.

// --- finding 2: Go's unicode tables are a version behind CPython's -------

// TestWordRuneSetMatchesCPython sweeps the ENTIRE rune range rather than
// sampling, because the failure is a version skew and a sample drawn from
// the code points a developer can think of is drawn from the version they
// know. The existing classify differential probes `fixé`, `fix٠`, `fix中`,
// `fix́` — all Unicode 15 or older, so none of them could see this.
//
// It also asserts the delta is one-directional. A supplement TABLE is only
// a correct shape while Go's set is a strict subset of CPython's; the day
// Go's tables move past CPython's, the additions must become a diff and
// this assertion is what says so.
func TestWordRuneSetMatchesCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}
	const pySrc = `
import json, re
w = re.compile(r"\w")
# Emit RANGES, not points: the point list is 143k entries and the ranges
# are 700-odd, and the comparison is exact either way.
out, start, prev = [], None, None
for c in range(0x110000):
    if 0xD800 <= c <= 0xDFFF:
        continue
    if w.match(chr(c)):
        if start is None:
            start = c
        elif prev is not None and c != prev + 1:
            out.append([start, prev]); start = c
        prev = c
    else:
        if start is not None and prev is not None:
            out.append([start, prev])
        start, prev = None, None
if start is not None:
    out.append([start, prev])
print(json.dumps(out))
`
	var ranges [][2]int
	p.RunJSON(t, pySrc, &ranges)
	if len(ranges) == 0 {
		t.Fatal("CPython reported no word-character ranges")
	}
	inPy := func(r rune) bool {
		lo, hi := 0, len(ranges)-1
		for lo <= hi {
			mid := (lo + hi) / 2
			switch {
			case int(r) < ranges[mid][0]:
				hi = mid - 1
			case int(r) > ranges[mid][1]:
				lo = mid + 1
			default:
				return true
			}
		}
		return false
	}

	pyOnly, goOnly, total := 0, 0, 0
	var firstPy, firstGo rune = -1, -1
	for r := rune(0); r < 0x110000; r++ {
		if r >= 0xD800 && r <= 0xDFFF {
			continue
		}
		py, gg := inPy(r), isWordRune(r)
		if py {
			total++
		}
		switch {
		case py && !gg:
			pyOnly++
			if firstPy < 0 {
				firstPy = r
			}
		case gg && !py:
			goOnly++
			if firstGo < 0 {
				firstGo = r
			}
		}
	}
	// Anti-vacuity: a probe that returned a tiny range set would agree with
	// almost anything. CPython matched 142940 code points when this was
	// written; the floor is far below that and still far above an accident.
	if total < 100000 {
		t.Fatalf("CPython word set is implausibly small (%d) — probe is broken", total)
	}
	if pyOnly != 0 || goOnly != 0 {
		t.Errorf("word-rune sets differ: %d py-only (first U+%04X), %d go-only (first U+%04X)."+
			"\nIf go-only is nonzero the supplement TABLE is the wrong shape and it must become a diff."+
			"\nIf py-only is nonzero, regenerate wordSupplement.", pyOnly, firstPy, goOnly, firstGo)
	}
}

// TestClassifyStepTypeOverUnicode16 is the behavioural half: the predicate
// above is internal, and what the store actually records is the bucket.
func TestClassifyStepTypeOverUnicode16(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}
	// One representative from each supplement range, plus the two the
	// sweep found that AGREE anyway (str.lower folds them into a rune Go
	// already knows) — a fixture set drawn only from the disagreements
	// would pass on a port that hardcoded "these all become general".
	var texts []string
	for _, rg := range wordSupplement {
		texts = append(texts, "find"+string(rg[0]), "find"+string(rg[1]))
	}
	texts = append(texts, "find", "findé", "fix中", "research now")

	const pySrc = `
import json, sys
import metrics
print(json.dumps([metrics.classify_step_type(t) for t in json.loads(sys.argv[1])]))
`
	var want []string
	p.RunJSON(t, pySrc, &want, pyprobe.Arg(t, texts))
	if len(want) != len(texts) {
		t.Fatalf("probe returned %d answers for %d inputs", len(want), len(texts))
	}
	general := 0
	for i, tx := range texts {
		if got := ClassifyStepType(tx); got != want[i] {
			t.Errorf("classify(%q): python %q, go %q", tx, want[i], got)
		}
		if want[i] == "general" {
			general++
		}
	}
	// Anti-vacuity, two ways: the set must contain both buckets, or an
	// implementation that answered one constant would pass.
	if general == 0 || general == len(texts) {
		t.Fatalf("fixture set is one-sided: %d/%d general", general, len(texts))
	}
}

// --- finding 6: a short read is not an error ----------------------------

// TestReverseReadlineShortRead pins BOTH halves of the distinction r3 got
// wrong. Without the seam neither case is constructible: both need a
// concurrent writer to lose a race at a chosen offset.
func TestReverseReadlineShortRead(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "ledger.jsonl")
	// Three chunks' worth, so the truncation lands mid-read the way a real
	// writer's would.
	var b strings.Builder
	for i := 0; i < 4000; i++ {
		fmt.Fprintf(&b, `{"n": %d}`+"\n", i)
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}

	orig := reverseReadAt
	defer func() { reverseReadAt = orig }()

	t.Run("a short read yields what arrived and keeps going", func(t *testing.T) {
		calls := 0
		reverseReadAt = func(f *os.File, buf []byte, off int64) (int, error) {
			calls++
			if calls == 1 {
				return orig(f, buf, off)
			}
			// The file was truncated under us: read() would return b"".
			return 0, io.EOF
		}
		var got []string
		err := ReverseReadline(path, 4096, func(line string) bool {
			got = append(got, line)
			return true
		})
		if err != nil {
			t.Fatalf("a short read must not be an error, got %v", err)
		}
		if len(got) == 0 {
			t.Fatal("expected the lines read before the truncation")
		}
		// Python keeps looping and flushes `leftover` at the end, so the
		// partial first fragment is yielded too. What matters here is that
		// the call SUCCEEDS with a partial answer rather than failing.
		if calls < 2 {
			t.Fatalf("fixture never reached the short read (calls=%d)", calls)
		}
	})

	t.Run("a real io error still propagates", func(t *testing.T) {
		boom := errors.New("disk went away")
		calls := 0
		reverseReadAt = func(f *os.File, buf []byte, off int64) (int, error) {
			calls++
			if calls == 1 {
				return orig(f, buf, off)
			}
			return 0, boom
		}
		err := ReverseReadline(path, 4096, func(line string) bool { return true })
		if !errors.Is(err, boom) {
			t.Fatalf("a real I/O error must propagate, got %v", err)
		}
	})
}

// TestSpendTodayShortReadAnswersPartial is the consumer-level statement:
// r3's fix made this answer 0.0, and CPython answers the partial sum.
func TestSpendTodayShortReadAnswersPartial(t *testing.T) {
	ws := t.TempDir()
	path := StepCostsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 8, 26, 12, 0, 0, 0, time.UTC)
	today := now.Format("2006-01-02")
	var b strings.Builder
	// Enough rows to span several buffers at the size used below.
	for i := 0; i < 3000; i++ {
		fmt.Fprintf(&b, `{"recorded_at": "%sT00:00:%02dZ", "cost_usd": 0.001}`+"\n",
			today, i%60)
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}

	full := SpendToday(ws, now)
	if full <= 0 {
		t.Fatalf("baseline is not a live reader: SpendToday returned %v", full)
	}

	orig := reverseReadAt
	defer func() { reverseReadAt = orig }()
	calls := 0
	reverseReadAt = func(f *os.File, buf []byte, off int64) (int, error) {
		calls++
		if calls == 1 {
			return orig(f, buf, off)
		}
		return 0, io.EOF
	}
	partial := SpendToday(ws, now)
	if calls < 2 {
		t.Fatalf("fixture never reached the short read (calls=%d)", calls)
	}
	if partial == 0.0 {
		t.Fatal("a short read answered 0.0 — this is exactly the r3 regression")
	}
	if partial >= full {
		t.Fatalf("expected a PARTIAL sum below the full %v, got %v", full, partial)
	}
}

// --- finding 4: the loss report must reach the operator -----------------

func TestLoadStepCostsAnnouncesDroppedLines(t *testing.T) {
	ws := t.TempDir()
	path := StepCostsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(
		`{"id": "a"}`+"\n"+`{not json`+"\n"+`{"id": "b"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	orig := warn
	defer func() { warn = orig }()
	var said []string
	warn = func(format string, args ...any) {
		said = append(said, fmt.Sprintf(format, args...))
	}

	rows := LoadStepCosts(ws, 100)
	if len(rows) != 2 {
		t.Fatalf("expected the 2 good rows, got %d", len(rows))
	}
	if len(said) != 1 {
		t.Fatalf("expected exactly one announcement, got %d: %v", len(said), said)
	}
	// Python: "read_jsonl_tail(<path>): dropped 1 line(s): 1 malformed"
	for _, want := range []string{"read_jsonl_tail", "1", "malformed", path} {
		if !strings.Contains(said[0], want) {
			t.Errorf("announcement %q is missing %q", said[0], want)
		}
	}

	// Anti-vacuity: a CLEAN file must say nothing, or the assertion above
	// passes for an implementation that warns unconditionally.
	said = nil
	if err := os.WriteFile(path, []byte(`{"id": "a"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := LoadStepCosts(ws, 100); len(got) != 1 {
		t.Fatalf("expected 1 row, got %d", len(got))
	}
	if len(said) != 0 {
		t.Fatalf("a clean file announced %v", said)
	}
}

// --- finding 5: st_mtime is a float, and floats collapse ----------------

// TestMtimeSortKeyCollapsesLikeCPython pins the KEY, not the ordering.
//
// The reviewer's end-to-end demonstration was marked unverified, and it was
// right to be: which card falls off the `[:limit]` cut after a tie depends
// on directory iteration order, so a test asserting that would be asserting
// this box's readdir. The key's collapse is the part that is a fact about
// CPython, and it is the part the port got wrong.
func TestMtimeSortKeyCollapsesLikeCPython(t *testing.T) {
	p := pyprobe.Probe{Marker: "metrics.py"}
	// Pairs of nanosecond stamps: within a float64 ULP at 1.7e9 (~477ns),
	// and outside it. CPython decides which is which, not this test.
	// The three deltas that separate `sec + nsec/1e9` from the obvious
	// `UnixNano()/1e9` are 127, 128 and 2000 — a fixture without them
	// passes for BOTH spellings, which is how the first version of this
	// test could not fail.
	pairs := [][2]int64{
		{1700000000000000000, 1700000000000000100}, // collapses in both
		{1700000000000000000, 1700000000000000127}, // DISCRIMINATES
		{1700000000000000000, 1700000000000000128}, // DISCRIMINATES
		{1700000000000000000, 1700000000000000129}, // just past it
		{1700000000000000000, 1700000000000002000}, // DISCRIMINATES
		{1700000000000000000, 1700000000000000477}, // distinct in both
		{1700000000000000000, 1700000001000000000}, // a whole second
	}
	const pySrc = `
import json, os, sys, tempfile
pairs = json.loads(sys.argv[1])
out = []
d = tempfile.mkdtemp()
for i, (a, b) in enumerate(pairs):
    pa, pb = os.path.join(d, "a%d" % i), os.path.join(d, "b%d" % i)
    for p, ns in ((pa, a), (pb, b)):
        open(p, "w").close()
        os.utime(p, ns=(ns, ns))
    out.append(os.stat(pa).st_mtime == os.stat(pb).st_mtime)
print(json.dumps(out))
`
	var want []bool
	p.RunJSON(t, pySrc, &want, pyprobe.Arg(t, pairs))
	if len(want) != len(pairs) {
		t.Fatalf("probe returned %d answers for %d pairs", len(want), len(pairs))
	}
	// Anti-vacuity: the fixture must contain BOTH outcomes, or a key that
	// always ties and a key that never ties would each pass half of it.
	collapsed := 0
	for _, w := range want {
		if w {
			collapsed++
		}
	}
	if collapsed == 0 || collapsed == len(want) {
		t.Fatalf("fixture is one-sided: %d/%d collapse", collapsed, len(want))
	}
	for i, pr := range pairs {
		ka := mtimeSortKey(time.Unix(0, pr[0]))
		kb := mtimeSortKey(time.Unix(0, pr[1]))
		if got := ka == kb; got != want[i] {
			t.Errorf("mtime %d vs %d: python ties=%v, go ties=%v (keys %v, %v)",
				pr[0], pr[1], want[i], got, ka, kb)
		}
	}
}

// --- finding 1: a NAMED DIVERGENCE, not a fix ---------------------------

// TestRunCostP90NaNSortIsANamedDivergence records that the port does NOT
// match CPython when a run card carries a NaN cost, and pins the numbers
// both runtimes actually produce.
//
// Why it is not fixed here: matching needs CPython's list.sort, not a
// comparator tweak. Measured over 153 NaN-bearing lists — `sort.Float64s`
// disagreed on 136 and `sort.SliceStable` with a `<` comparator on 78, so
// neither Go sort is CPython's answer with an inconsistent comparator, and
// there is no cheap spelling that is. The fix is a `pyval` port of Python's
// timsort, which has a second waiting consumer (`format_metrics_report`
// sorts by_model on `-cost`, where a NaN lands in the MIDDLE) and is filed
// as its own chunk.
//
// This test goes RED when that lands. That is the point: delete it then,
// and do not "fix" it by loosening the assertion.
func TestRunCostP90NaNSortIsANamedDivergence(t *testing.T) {
	ws := t.TempDir()
	// Newest first by mtime; costs 1..7 then a NaN on the oldest.
	costs := []string{"1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "NaN"}
	base := time.Date(2026, 8, 26, 12, 0, 0, 0, time.UTC)
	for i, c := range costs {
		dir := filepath.Join(ws, "runs", fmt.Sprintf("run%d", i))
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		card := filepath.Join(dir, "run_card.json")
		body := fmt.Sprintf(`{"total_cost_usd": %s, "success_class": "success"}`, c)
		if err := os.WriteFile(card, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		// i=0 is the NEWEST.
		mt := base.Add(time.Duration(-i) * time.Second)
		if err := os.Chtimes(card, mt, mt); err != nil {
			t.Fatal(err)
		}
	}

	got, ok := SuccessfulRunCostP90(ws, 200)
	if !ok {
		t.Fatal("expected a p90 from 8 successful cards")
	}
	// CPython: list.sort() leaves a trailing NaN where it is, so the sorted
	// list is unchanged and vals[int(0.9*7)] == vals[6] == 7.0.
	// Go: sort.Float64s orders NaN FIRST, so vals[6] == 6.0.
	const cpython, thisPort = 7.0, 6.0
	if got != thisPort {
		t.Fatalf("the named divergence moved: expected Go's %v, got %v."+
			"\nIf a timsort port landed, CPython's answer is %v — delete this test.",
			thisPort, got, cpython)
	}
	if cpython == thisPort {
		t.Fatal("this test asserts a divergence and the two values are equal")
	}
}

// TestRunCostP90MtimeTieStraddlesTheLimit closes the half of finding 5 the
// key test cannot reach, and the battery's M101 ("the mtime sort is
// UNSTABLE, so ties reorder") with it.
//
// Pinning the KEY leaves the COMPARATOR free: reverting it to
// `mtime.After()` passes TestMtimeSortKeyCollapsesLikeCPython, because that
// test never sorts anything. The consequence only shows when a tie
// straddles the `[:limit]` cut, and which card then survives depends on
// directory iteration order — which is why the r4 report marked its own
// demonstration unverified.
//
// The order is not GUESSED here, it is READ: the fixture asks Go which
// directory comes first and gives the 127ns-later stamp to the one that
// comes SECOND. A stable sort on the collapsed float keeps readdir order
// and drops the second; `.After()` sees the nanoseconds, promotes it, and
// drops the FIRST — two different cost sets, two different answers,
// regardless of how the filesystem happens to order the names. CPython
// still supplies the expected value; Go's glob only chooses the fixture.
func TestRunCostP90MtimeTieStraddlesTheLimit(t *testing.T) {
	ws := t.TempDir()
	// Two floors decide the size. RUN_COST_MIN_SAMPLES is 8, so the kept
	// set must be at least that. And Go's sort.Slice INSERTION-SORTS small
	// slices, which is stable by accident — at nine cards the unstable-sort
	// mutant passed. Twenty fillers puts the sort past pdqsort's threshold
	// so instability is actually reachable.
	const fillers = 20
	costs := map[string]float64{}
	for i := 0; i < fillers; i++ {
		costs[fmt.Sprintf("run-fill%d", i)] = float64(i + 1)
	}
	// Far apart in value so which one survives moves vals[int(0.9*7)].
	costs["run-tieA"] = 0.5
	costs["run-tieB"] = 100.0
	for name, c := range costs {
		dir := filepath.Join(ws, "runs", name)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		body := fmt.Sprintf(`{"total_cost_usd": %v, "success_class": "success"}`, c)
		if err := os.WriteFile(filepath.Join(dir, "run_card.json"),
			[]byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	cards, err := globRunCards(runsRoot(ws))
	if err != nil {
		t.Fatal(err)
	}
	if len(cards) != fillers+2 {
		t.Fatalf("expected %d cards, got %d", fillers+2, len(cards))
	}
	var tied []string
	for _, c := range cards {
		if strings.Contains(c, "run-tie") {
			tied = append(tied, c)
		}
	}
	if len(tied) != 2 {
		t.Fatalf("expected 2 tied candidates, got %d", len(tied))
	}

	// The base is NOT arbitrary. Whether 127ns collapses into one float64
	// depends on the exponent: it does at 1.7e9 (2023-11-14) and does NOT
	// at a 2026 stamp, where the same delta lands in a different ULP. The
	// first version of this fixture used "now"-ish and quietly measured
	// nothing — both mutants below passed it.
	base := time.Unix(1700000000, 0).UTC()
	set := func(path string, d time.Duration) {
		mt := base.Add(d)
		if err := os.Chtimes(path, mt, mt); err != nil {
			t.Fatal(err)
		}
	}
	// The fillers are unambiguously newer; the tied pair fights for slot 8.
	fill := 0
	for _, c := range cards {
		if strings.Contains(c, "run-fill") {
			fill++
			set(c, time.Duration(10+fill)*time.Second)
		}
	}
	if fill != fillers {
		t.Fatalf("expected %d fillers, got %d", fillers, fill)
	}
	// 100ns apart, and the number is load-bearing twice over. It must be
	// small enough that CPython's `sec + nsec/1e9` COLLAPSES it (100 does at
	// this base; 127 does not — 127 is the delta that separates the two Go
	// spellings, which is a different question and a different test). And
	// the later stamp goes to whichever card the directory yields SECOND, so
	// a stable sort and an After() sort disagree whatever order the
	// filesystem chose.
	set(tied[0], 0)
	set(tied[1], 100)

	costOf := func(card string) float64 {
		for name, c := range costs {
			if strings.Contains(card, name+string(filepath.Separator)) {
				return c
			}
		}
		t.Fatalf("no cost for %s", card)
		return 0
	}
	if costOf(tied[0]) == costOf(tied[1]) {
		t.Fatal("the tied cards carry the same cost — fixture cannot discriminate")
	}

	probe := pyprobe.Probe{Marker: "metrics.py", Workspaces: []string{ws}}
	pyArgs := []map[string]any{{"ws": ws, "limit": fillers + 1}}
	var want []struct {
		None  bool    `json:"none"`
		Value float64 `json:"value"`
	}
	probe.RunJSON(t, pyP90Src, &want, pyprobe.Arg(t, pyArgs))
	if len(want) != 1 {
		t.Fatalf("probe returned %d answers", len(want))
	}
	if want[0].None {
		t.Fatal("CPython answered None — the fixture never reached the sort")
	}

	got, ok := SuccessfulRunCostP90(ws, fillers+1)
	if !ok {
		t.Fatal("go answered None where CPython answered a value")
	}
	if got != want[0].Value {
		t.Errorf("p90 over a straddling mtime tie: python %v, go %v"+
			"\n(readdir order was %v; the 100ns-later stamp went to %s)",
			want[0].Value, got, cards, tied[1])
	}
}
