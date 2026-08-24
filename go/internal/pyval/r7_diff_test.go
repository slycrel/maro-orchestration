package pyval

import (
	"encoding/json"
	"os/exec"
	"strconv"
	"strings"
	"testing"
)

// tagged renders a pyval tree into the transport this file's CPython
// probe reads. An Obj becomes {"__obj__": [[key, val], ...]}, because a
// plain encoding/json encoding of it would SORT the keys — which is the
// exact loss this round is about, and a corpus that arrives pre-sorted
// could not show it.
func tagged(v any) any {
	switch t := v.(type) {
	case Obj:
		pairs := make([]any, 0, len(t))
		for _, f := range t {
			pairs = append(pairs, []any{f.Key, tagged(f.Val)})
		}
		return map[string]any{"__obj__": pairs}
	case List:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = tagged(e)
		}
		return out
	case float64:
		// Floats are tagged too, and for the same reason: encoding/json
		// writes 3.0 as "3", so an untagged whole float arrived at the
		// probe as a Python int and the probe answered "3" — a
		// transport artifact that reads exactly like a real divergence.
		return map[string]any{"__float__": strconv.FormatFloat(t, 'g', -1, 64)}
	}
	return v
}

// dumpsCorpus is the shared fixture: every shape the eight writers this
// round converted actually put on disk, plus the three characters that
// separate json.dumps from encoding/json.
func dumpsCorpus() []Obj {
	return []Obj{
		// The FailedCheckSignature shape. "%s => exit %d: %s" means EVERY
		// failed-check entry either runtime writes contains "=>", so this
		// is not a synthetic edge — it is the common case on the rail.
		{
			{Key: "complete", Val: false},
			{Key: "confidence", Val: 0.8},
			{Key: "failed_checks", Val: List{
				"greeting exists => exit 3: no such file",
				"tests pass => exit 1: 2 failed",
			}},
		},
		// HTML's three, a shell redirect, and an ampersand in prose.
		{
			{Key: "summary", Val: "wrote <index.html> & ran `a > b`"},
			{Key: "command", Val: "grep -c '<div>' out.html > n.txt"},
		},
		// ensure_ascii: CPython escapes, encoding/json does not.
		{
			{Key: "gaps", Val: List{"研究 was not run", "no café was tested"}},
			{Key: "summary", Val: "résumé — done ✅"},
			{Key: "研究", Val: "a non-ASCII KEY escapes too"},
		},
		// Key order: reverse-alphabetical, so a sorted writer is visibly
		// wrong on the very first key.
		{
			{Key: "ts", Val: "2026-08-23T00:00:00Z"},
			{Key: "loop_id", Val: "go-abc123"},
			{Key: "complete", Val: true},
			{Key: "aardvark", Val: 1},
		},
		// Floats: whole ones keep their ".0" in Python and lose it in a
		// marshal/reparse round trip; large ints must not widen.
		{
			{Key: "alignment_score_avg", Val: 3.0},
			{Key: "confidence", Val: 0.7},
			{Key: "elapsed_ms", Val: int64(9007199254740993)},
			{Key: "checks_run", Val: 0},
		},
		// Empty containers render inline even under indent, and nesting
		// has to keep both properties at depth.
		{
			{Key: "check_results", Val: List{
				Obj{
					{Key: "description", Val: "does it build?"},
					{Key: "command", Val: "go build ./... 2>&1"},
					{Key: "exit_code", Val: 0},
					{Key: "outcome", Val: "pass"},
					{Key: "stdout", Val: ""},
					{Key: "stderr", Val: "note: <none>"},
					{Key: "plan_index", Val: 0},
				},
			}},
			{Key: "gaps", Val: List{}},
			{Key: "modality_distribution", Val: Obj{}},
			{Key: "downgrade_reason", Val: nil},
		},
		// Control characters and quotes: the escape table itself. The
		// second key is not decoration — with only the first, indent-mode
		// encoding/json AGREED with json.dumps on this case and the
		// anti-vacuity guard below caught the corpus not discriminating.
		{
			{Key: "stderr", Val: "line1\nline2\ttabbed\x07bell \"quoted\" \\slash"},
			{Key: "command", Val: "a > b"},
		},
	}
}

const dumpsProbe = `
import json, sys
def rebuild(v):
    if isinstance(v, dict) and len(v) == 1 and "__obj__" in v:
        return {k: rebuild(x) for k, x in v["__obj__"]}
    if isinstance(v, dict) and len(v) == 1 and "__float__" in v:
        return float(v["__float__"])
    if isinstance(v, list):
        return [rebuild(x) for x in v]
    return v
out = []
for c in json.loads(sys.stdin.read()):
    d = rebuild(c)
    out.append([json.dumps(d), json.dumps(d, indent=2)])
print(json.dumps(out))
`

// TestDumpsMatchesJSONDumps is the parity test for the machinery all
// eight of this round's converted writers now share. Before r7 they were
// on encoding/json, which differs from json.dumps three ways at once:
// sorted keys, HTML-escaped `<` `>` `&`, and raw UTF-8 where json.dumps
// defaults to ensure_ascii. Every one of the eight writes a file the
// other runtime reads.
func TestDumpsMatchesJSONDumps(t *testing.T) {
	corpus := dumpsCorpus()
	transport := make([]any, 0, len(corpus))
	for _, o := range corpus {
		transport = append(transport, tagged(o))
	}
	in, err := json.Marshal(transport)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c", dumpsProbe)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want [][2]string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output: %v (%s)", err, out)
	}
	if len(want) != len(corpus) {
		t.Fatalf("probe returned %d rows for %d cases", len(want), len(corpus))
	}

	// Anti-vacuity, run BEFORE the assertions: the pre-fix implementation
	// is replayed over this same corpus and must LOSE. Counting the right
	// SHAPE of fixture does not prove a corpus discriminates — only the
	// old code failing on it does.
	oldCompactLost, oldIndentLost := 0, 0
	for i, o := range corpus {
		plain := Plain(o)
		if raw, merr := json.Marshal(plain); merr != nil || string(raw) != want[i][0] {
			oldCompactLost++
		}
		if raw, merr := json.MarshalIndent(plain, "", "  "); merr != nil ||
			string(raw) != want[i][1] {
			oldIndentLost++
		}
	}
	if oldCompactLost < len(corpus) || oldIndentLost < len(corpus) {
		t.Fatalf("encoding/json already matched json.dumps on some of these "+
			"cases (compact lost %d/%d, indent lost %d/%d): this corpus "+
			"cannot have caught the finding",
			oldCompactLost, len(corpus), oldIndentLost, len(corpus))
	}

	for i, o := range corpus {
		got, gerr := DumpsCompactPy(o)
		if gerr != nil {
			t.Fatalf("case %d: DumpsCompactPy: %v", i, gerr)
		}
		if got != want[i][0] {
			t.Fatalf("case %d compact:\n go: %s\n py: %s", i, got, want[i][0])
		}
		gotI, gerr := DumpsIndent2(o)
		if gerr != nil {
			t.Fatalf("case %d: DumpsIndent2: %v", i, gerr)
		}
		if gotI != want[i][1] {
			t.Fatalf("case %d indent:\n go:\n%s\n py:\n%s", i, gotI, want[i][1])
		}
	}
}

// TestFromPlainWidensEveryContainerSpelling pins the reflection arm. The
// explicit type cases cover map[string]any and []any; a
// modality_distribution built as map[string]int is neither, fell through
// to `return v`, and render then REFUSED the whole document — the
// closure verdict row went unwritten, which is how this was found.
func TestFromPlainWidensEveryContainerSpelling(t *testing.T) {
	type named struct{ A, B int }
	cases := []struct {
		name string
		in   any
		want string
	}{
		{"map[string]int", map[string]int{"http": 2, "static": 1},
			`{"http": 2, "static": 1}`},
		// map[string]any is the EXPLICIT arm, not the reflect one, and
		// every case below that used it had a single key — so its
		// sort.Strings was never exercised and a battery mutant that
		// reversed it survived. Two keys, in reverse-of-sorted insertion
		// order, so the arm's own ordering is what is asserted.
		{"map[string]any multi-key", map[string]any{"zeta": 1, "alpha": 2},
			`{"alpha": 2, "zeta": 1}`},
		{"map[string]float64", map[string]float64{"b": 0.5, "a": 1.0},
			`{"a": 1.0, "b": 0.5}`},
		{"[]int", []int{3, 1}, `[3, 1]`},
		{"[]map[string]any", []map[string]any{{"k": "v"}}, `[{"k": "v"}]`},
		{"[][]string", [][]string{{"a"}, {}}, `[["a"], []]`},
		{"nested map[string]int", map[string]any{"d": map[string]int{"x": 1}},
			`{"d": {"x": 1}}`},
		{"named struct is NOT a container", named{1, 2}, ""},
	}
	for _, c := range cases {
		got, err := DumpsCompactPy(FromPlain(c.in))
		if c.want == "" {
			// A struct has no json.dumps spelling without reflection over
			// its tags, and inventing one silently would be worse than
			// the refusal. Pinned so the refusal is deliberate.
			if err == nil {
				t.Fatalf("%s: expected a refusal, got %q", c.name, got)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if got != c.want {
			t.Fatalf("%s: got %s, want %s", c.name, got, c.want)
		}
	}
}

// TestFromPlainDoesNotRoundTripFloatsThroughEncodingJSON: the tempting
// implementation of FromPlain is json.Marshal followed by LoadsOrdered,
// and it is wrong. json.Marshal(3.0) is "3", so the reparse hands back
// an int where json.dumps(3.0) writes "3.0" — and confidence,
// alignment_score_avg and every success rate are floats that are often
// whole.
func TestFromPlainDoesNotRoundTripFloatsThroughEncodingJSON(t *testing.T) {
	got, err := DumpsCompactPy(FromPlain(map[string]any{"confidence": 1.0}))
	if err != nil {
		t.Fatal(err)
	}
	if got != `{"confidence": 1.0}` {
		t.Fatalf("whole float lost its float-ness: %s", got)
	}
	// The falsifier for the rejected implementation, kept so the reason
	// is a measurement and not a claim in a comment.
	raw, _ := json.Marshal(map[string]any{"confidence": 1.0})
	if string(raw) != `{"confidence":1}` {
		t.Fatalf("encoding/json no longer drops the .0 (%s) — the comment "+
			"on FromPlain names this as its reason and must be revisited", raw)
	}
}

// TestRoundNegativePrecisionMatchesCPython: FormatFloat treats EVERY
// negative precision as "shortest", so Round's format-and-reparse was a
// silent no-op below zero — round(1234.5678, -2) is 1200.0 in CPython and
// was 1234.5678 here. No call site passes a negative n today, which is
// precisely why it needed fixing rather than documenting: the doc says
// "Python's round(f, n)" with no domain, and the next caller believes the
// doc (adversarial mission-r7 MEDIUM, latent).
func TestRoundNegativePrecisionMatchesCPython(t *testing.T) {
	type rc struct {
		V float64 `json:"v"`
		N int     `json:"n"`
	}
	var cases []rc
	for _, v := range []float64{1234.5678, 5.5, 15.0, 25.0, -1234.5678,
		0.0, 1e9 + 0.5, 149.0, 150.0, 250.0, 99999.9} {
		for n := -6; n <= 0; n++ {
			cases = append(cases, rc{v, n})
		}
	}
	in, err := json.Marshal(cases)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"print(json.dumps([repr(float(round(c['v'], c['n'])))\n"+
			"                  for c in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output: %v (%s)", err, out)
	}

	// Anti-vacuity: the pre-fix body — a single format-and-reparse for
	// every n — replayed and required to lose.
	oldLost := 0
	for i, c := range cases {
		got, rerr := strconv.ParseFloat(strconv.FormatFloat(c.V, 'f', c.N, 64), 64)
		if rerr != nil {
			oldLost++
			continue
		}
		if strconv.FormatFloat(got, 'g', -1, 64) != pyFloatRepr(want[i]) {
			oldLost++
		}
	}
	if oldLost == 0 {
		t.Fatal("the pre-fix Round already agrees with CPython on every case " +
			"here: this corpus could not have caught the finding")
	}

	for i, c := range cases {
		got := Round(c.V, c.N)
		if strconv.FormatFloat(got, 'g', -1, 64) != pyFloatRepr(want[i]) {
			t.Fatalf("Round(%v, %d) = %v, want CPython %s", c.V, c.N, got, want[i])
		}
	}
}

// pyFloatRepr normalises CPython's repr() of a float to the shortest
// round-tripping form Go prints, so the two are comparable as strings
// without re-deriving either side's formatter.
func pyFloatRepr(s string) string {
	f, err := strconv.ParseFloat(s, 64)
	if err != nil {
		return s
	}
	return strconv.FormatFloat(f, 'g', -1, 64)
}

// TestParseFloatMatchesCPythonFloat: ParseFloat IS float(str), and nothing
// pinned its deltas from strconv.ParseFloat until r7's battery removed the
// hex rejection and every pyval test stayed green. The rejection has been
// in this port since r2 (in record/verdict.go) and did not travel when r6
// consolidated safe_float here — a consolidation is only half done if the
// surviving copy is not the one that knew the most.
func TestParseFloatMatchesCPythonFloat(t *testing.T) {
	corpus := []string{
		// Hex float literals: strconv takes them, float() raises.
		"0x1p-2", "0X1P-2", "-0x1p-2", "+0x10", "0x10",
		// Unicode decimal digits: float() takes them, strconv does not.
		"\u0660.\u0665", "\u0661\u0662\u0663",
		// The two strip sets differ by U+001C..U+001F, and \u00a0 is in
		// str.strip()'s set but not float()'s.
		" 0.9", "\u001c0.9", "0.9\u001f", "\t0.9\n", "\u00a00.9",
		// Overflow: CPython raises, strconv returns ErrRange WITH ±Inf.
		"1e309", "-1e309",
		// Underscores: both accept.
		"1_000", "1_000.5",
		// The named non-numbers CPython DOES accept.
		"nan", "inf", "-inf", "Infinity", "-Infinity", "NaN",
		// Ordinary and malformed.
		"0.5", "-0.5", ".5", "5.", "1e3", "", " ", "abc", "0x", "--1",
	}
	in, err := json.Marshal(corpus)
	if err != nil {
		t.Fatal(err)
	}
	out, perr := exec.Command("python3", "-c",
		"import json,sys\n"+
			"def f(s):\n"+
			"    try: return repr(float(s))\n"+
			"    except Exception: return None\n"+
			"print(json.dumps([f(s) for s in json.loads(sys.argv[1])]))",
		string(in)).Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	var want []*string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output: %v (%s)", err, out)
	}

	// Anti-vacuity: bare strconv.ParseFloat over the same corpus, required
	// to lose. Without this the corpus could be all-ordinary and report
	// agreement while pinning nothing.
	oldLost := 0
	for i, s := range corpus {
		var got *string
		if f, ferr := strconv.ParseFloat(s, 64); ferr == nil {
			r := strconv.FormatFloat(f, 'g', -1, 64)
			got = &r
		}
		if !samePyFloat(got, want[i]) {
			oldLost++
		}
	}
	if oldLost < 5 {
		t.Fatalf("bare strconv.ParseFloat differs from float() on only %d of "+
			"%d cases: this corpus barely discriminates", oldLost, len(corpus))
	}

	for i, s := range corpus {
		var got *string
		if f, ok := ParseFloat(s); ok {
			r := strconv.FormatFloat(f, 'g', -1, 64)
			got = &r
		}
		if !samePyFloat(got, want[i]) {
			t.Errorf("ParseFloat(%q) = %s, want float() %s",
				s, showFloat(got), showFloat(want[i]))
		}
	}
}

func samePyFloat(a, b *string) bool {
	if a == nil || b == nil {
		return a == nil && b == nil
	}
	return *a == pyFloatRepr(*b)
}

func showFloat(p *string) string {
	if p == nil {
		return "<ValueError>"
	}
	return *p
}
