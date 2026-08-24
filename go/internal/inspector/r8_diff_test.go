package inspector

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// TestSignalCountsRenderInPythonsInsertionOrder: the `Signal counts:`
// line is PROMPT TEXT — inspector.py:776 is
// `f"Signal counts: {json.dumps(signal_counts)}"` over a plain dict.
//
// The Go side held a `map[string]int` and rendered it with
// `json.Marshal`, which sorts. Two runtimes looking at the same fleet
// therefore asked the model a differently-worded question, and no test
// could see it because the prompt is not a store.
//
// Driven against CPython so the claim is measured, not transcribed.
func TestSignalCountsRenderInPythonsInsertionOrder(t *testing.T) {
	c := newOrderedCounts()
	// Deliberately NOT alphabetical, so sorted and insertion order differ,
	// and one key carries `>` so HTML escaping is exercised too. A signal
	// type is a code-ish token in practice, but the escaping fork is a
	// property of the ENCODER, not of the fixture's realism.
	c.add("timeout", 3)
	c.add("a > b mismatch", 1)
	c.add("retry", 2)
	c.add("timeout", 4) // an existing key must NOT move to the end
	c.add("café_signal", 1)

	// The PRODUCTION render, not a replay of it: the first version of this
	// test called pyval.DumpsCompactPy itself, and the mutation battery
	// showed it survived reverting the production line to a sorted map.
	// A guard that re-implements what it guards agrees with anything.
	got := c.render()
	want := pythonDumps(t, [][2]any{
		{"timeout", 7},
		{"a > b mismatch", 1},
		{"retry", 2},
		{"café_signal", 1},
	})
	if got != want {
		t.Errorf("signal counts are not json.dumps' bytes:\n go %s\n py %s", got, want)
	}

	// Anti-vacuity: the PRE-FIX implementation, replayed over the same
	// fixture and required to lose. Without this the test would report
	// agreement on any fixture that happened to be already-sorted, plain
	// ASCII and gap-free — which is what most real fixtures are.
	plain := map[string]int{}
	for _, k := range c.keys {
		plain[k] = c.get(k)
	}
	old, err := json.Marshal(plain)
	if err != nil {
		t.Fatal(err)
	}
	if string(old) == want {
		t.Fatal("encoding/json over a map already produces json.dumps' bytes " +
			"for this fixture: it cannot show the fork it was written for")
	}
	// And name the three forks explicitly, so a future edit that removes
	// one of them from the fixture fails loudly instead of going quiet.
	for _, marker := range []string{
		`a \u003e b`,       // HTML-escaped: the six literal chars json.dumps never writes
		"caf\u00e9_signal", // raw UTF-8, where ensure_ascii writes the escape
		`{"a `,             // SORTED: the fixture's first key is "timeout"
	} {
		if !strings.Contains(string(old), marker) {
			t.Fatalf("the pre-fix encoder does not exhibit %s here, so one of "+
				"the forks is untested:\n%s", marker, old)
		}
	}
}

// TestOrderedCountsIsAPythonDict: first-seen order, `get(k, 0) + n`
// accumulation, and no reordering on update.
func TestOrderedCountsIsAPythonDict(t *testing.T) {
	c := newOrderedCounts()
	c.add("b", 1)
	c.add("a", 2)
	c.add("b", 5)
	if got := strings.Join(c.keys, ","); got != "b,a" {
		t.Fatalf("re-adding an existing key must not move it: %s", got)
	}
	if c.get("b") != 6 || c.get("a") != 2 {
		t.Fatalf("accumulation is d[k] = d.get(k,0)+n: b=%d a=%d", c.get("b"), c.get("a"))
	}
	if c.get("missing") != 0 {
		t.Fatalf("a missing key reads 0, like dict.get(k, 0)")
	}
	if c.len() != 2 {
		t.Fatalf("len is the key count: %d", c.len())
	}
}

// TestTopFrictionSignalTiesKeepInsertionOrder: Python sorts these by
// count alone, and Python's sort is STABLE — `reverse=True` does not
// reverse ties (inspector.py:896-903). So two signal types with the same
// count come out in the order the dict first saw them.
//
// Go carried an alphabetical name tie-break. It was added honestly —
// ranging a map is randomised and the report had to be deterministic —
// but once the counts became insertion-ordered the hardening WAS the
// divergence, and `top_friction_signals[0]` is the report headline
// (inspector.py:1030). A hardening is a divergence (r1's lens, still
// paying out at r8).
func TestTopFrictionSignalTiesKeepInsertionOrder(t *testing.T) {
	// Same count, reverse-alphabetical insertion: the two orders disagree.
	c := newOrderedCounts()
	c.add("zebra", 2)
	c.add("alpha", 2)
	c.add("middle", 9)

	// The production sort, driven — not replayed (mission-r8 battery).
	var got []string
	for _, r := range topFrictionRows(c) {
		got = append(got, r.name)
	}
	want := pythonSortedByCount(t, [][2]any{{"zebra", 2}, {"alpha", 2}, {"middle", 9}})
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("tie order is not CPython's stable sort:\n go %v\n py %v", got, want)
	}

	// Anti-vacuity: the pre-fix comparator, replayed HERE on purpose —
	// a replay is the right shape for the losing side, and the wrong shape
	// for the winning one.
	oldRows := make([]sigRow, 0, len(c.keys))
	for _, k := range c.keys {
		oldRows = append(oldRows, sigRow{k, c.get(k)})
	}
	sort.Slice(oldRows, func(i, j int) bool {
		if oldRows[i].count != oldRows[j].count {
			return oldRows[i].count > oldRows[j].count
		}
		return oldRows[i].name < oldRows[j].name
	})
	var old []string
	for _, r := range oldRows {
		old = append(old, r.name)
	}
	if strings.Join(old, ",") == strings.Join(want, ",") {
		t.Fatal("the alphabetical tie-break agrees with CPython on this " +
			"fixture: the test cannot show the fork it was written for")
	}
}

// pythonSortedByCount runs the real `sorted(..., key=count, reverse=True)`.
func pythonSortedByCount(t *testing.T, pairs [][2]any) []string {
	t.Helper()
	in, err := json.Marshal(pairs)
	if err != nil {
		t.Fatal(err)
	}
	out := runPython(t,
		"import json,sys\n"+
			"pairs = json.loads(sys.argv[1])\n"+
			"d = {}\n"+
			"for k, v in pairs:\n"+
			"    d[k] = d.get(k, 0) + v\n"+
			"rows = [{'signal_type': k, 'count': v} for k, v in d.items()]\n"+
			"rows = sorted(rows, key=lambda x: x['count'], reverse=True)[:5]\n"+
			"print('\\n'.join(r['signal_type'] for r in rows))",
		string(in))
	return strings.Split(strings.TrimRight(out, "\n"), "\n")
}

// pythonDumps builds the dict from ordered pairs the way inspector.py
// does and renders it with the real json.dumps.
func pythonDumps(t *testing.T, pairs [][2]any) string {
	t.Helper()
	in, err := json.Marshal(pairs)
	if err != nil {
		t.Fatal(err)
	}
	out := runPython(t,
		"import json,sys\n"+
			"pairs = json.loads(sys.argv[1])\n"+
			"d = {}\n"+
			"for k, v in pairs:\n"+
			"    d[k] = d.get(k, 0) + v\n"+
			"print(json.dumps(d))",
		string(in))
	return strings.TrimRight(out, "\n")
}

func runPython(t *testing.T, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	out, err := exec.Command("python3", append([]string{"-c", src}, args...)...).Output()
	if err != nil {
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return string(out)
}

// TestInspectCadenceStateIsJSONDumps: the cadence counter file is read by
// BOTH runtimes to decide when the next inspection fires. A shared store
// written with encoding/json was writing bytes no CPython writer produces
// (adversarial mission-r8).
func TestInspectCadenceStateIsJSONDumps(t *testing.T) {
	ws := t.TempDir()
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	// cadence=5 so the counter lands non-zero: the steady-state shape.
	if _, err := CadenceTick(ws, 5, 3); err != nil {
		t.Fatal(err)
	}
	if _, err := CadenceTick(ws, 5, 3); err != nil {
		t.Fatal(err)
	}

	raw, err := os.ReadFile(cadencePath(ws))
	if err != nil {
		t.Fatalf("cadence state was not written: %v", err)
	}
	line := strings.TrimRight(string(raw), "\n")
	assertPythonSpelled(t, line, []string{"runs_since_inspect", "firings_since_deep", "updated_at"})
}

// assertPythonSpelled checks the two things that separate json.dumps
// from encoding/json on an object with these keys: the `", "` / `": "`
// separators, and the key ORDER being the writer's rather than sorted.
func assertPythonSpelled(t *testing.T, line string, order []string) {
	t.Helper()
	if !strings.HasPrefix(line, `{"`+order[0]+`": `) {
		t.Errorf("first key must be %q with json.dumps' separator:\n%s", order[0], line)
	}
	at := -1
	for _, k := range order {
		i := strings.Index(line, `"`+k+`": `)
		if i < 0 {
			t.Fatalf("key %q missing or unspaced:\n%s", k, line)
		}
		if i < at {
			t.Errorf("keys are not in the writer's order (%v):\n%s", order, line)
		}
		at = i
	}
	if strings.Contains(line, `","`) || strings.Contains(line, `":"`) {
		t.Errorf("compact separators are encoding/json's, not json.dumps':\n%s", line)
	}
}
