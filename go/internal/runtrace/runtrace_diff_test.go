package runtrace

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

func srcDirRT(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func probeRT(t *testing.T, body string, args ...string) []byte {
	t.Helper()
	argv := append([]string{"-c",
		"import json,sys\nsys.path.insert(0, sys.argv[1])\nimport run_trace as rt\n" + body,
		srcDirRT(t)}, args...)
	out, err := exec.Command("python3", argv...).Output()
	if err != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("the CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("the CPython probe could not run: %v", err)
	}
	return out
}

// safeRunDir returns a scratch run dir and refuses to hand back anything
// under the live workspace.
//
// This module APPENDS to build/trace.jsonl, and a probe that resolves a
// real run dir would write into it. ~/.maro/workspace holds live ledgers
// and one of them was destroyed by a probe once (2026-08-16). t.TempDir is
// already safe; the assertion is here so it stays safe when somebody later
// parameterises this helper.
func safeRunDir(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	abs, err := filepath.Abs(dir)
	if err != nil {
		t.Fatal(err)
	}
	home, _ := os.UserHomeDir()
	for _, forbidden := range []string{filepath.Join(home, ".maro"), filepath.Join(home, ".openclaw")} {
		if forbidden != "" && strings.HasPrefix(abs+string(filepath.Separator),
			forbidden+string(filepath.Separator)) {
			t.Fatalf("refusing to run a WRITING probe inside %s (resolved %s)",
				forbidden, abs)
		}
	}
	return abs
}

// ---------------------------------------------------------------------
// the node vocabulary
// ---------------------------------------------------------------------

// NODES decides which edges are flagged `unknown_node`, and that flag is
// what a test asserting "the vocabulary holds" reads. A set transcribed by
// hand from ten frozensets is exactly the shape that goes one entry short
// and stays that way, so it is compared wholesale rather than sampled.
func TestTheNodeVocabularyMatchesCPython(t *testing.T) {
	out := probeRT(t, ""+
		"names = ['PHASE_NODES','INTAKE_NODES','ROUTE_NODES','PLAN_NODES',\n"+
		"         'EXEC_NODES','FIN_NODES','VERIFY_NODES','GATE_NODES',\n"+
		"         'CLOSE_NODES','TERM_NODES','META_NODES','NODES']\n"+
		"print(json.dumps({n: sorted(getattr(rt, n)) for n in names}))")
	var want map[string][]string
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	got := map[string]map[string]bool{
		"PHASE_NODES": PhaseNodes, "INTAKE_NODES": IntakeNodes,
		"ROUTE_NODES": RouteNodes, "PLAN_NODES": PlanNodes,
		"EXEC_NODES": ExecNodes, "FIN_NODES": FinNodes,
		"VERIFY_NODES": VerifyNodes, "GATE_NODES": GateNodes,
		"CLOSE_NODES": CloseNodes, "TERM_NODES": TermNodes,
		"META_NODES": MetaNodes, "NODES": Nodes,
	}
	if len(got) != len(want) {
		t.Fatalf("comparing %d sets against CPython's %d", len(got), len(want))
	}
	total := 0
	for name, wantNames := range want {
		set, ok := got[name]
		if !ok {
			t.Errorf("%s exists in run_trace.py and not in the port", name)
			continue
		}
		var gotNames []string
		for n := range set {
			gotNames = append(gotNames, n)
		}
		sort.Strings(gotNames)
		if !eqStrs(gotNames, wantNames) {
			t.Errorf("%s differs — an id missing here is recorded as an "+
				"unknown_node, and an id present here that CPython lacks is "+
				"silently accepted\n  go  %v\n  py  %v\n  only-go %v\n  only-py %v",
				name, gotNames, wantNames, minus(gotNames, wantNames),
				minus(wantNames, gotNames))
		}
		total += len(wantNames)
	}
	if total < 60 {
		t.Fatalf("the twelve sets hold %d names between them; too few to be "+
			"reading the real vocabulary", total)
	}
}

func eqStrs(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func minus(a, b []string) []string {
	in := map[string]bool{}
	for _, s := range b {
		in[s] = true
	}
	var out []string
	for _, s := range a {
		if !in[s] {
			out = append(out, s)
		}
	}
	return out
}

// ---------------------------------------------------------------------
// _clip
// ---------------------------------------------------------------------

// The cap is on CODE POINTS (Python len() over a str), so the boundary is
// where a byte-length reading and a rune-length reading part company —
// which for a 3-byte character is 3x off, not 1 off.
func TestClipAttrMatchesCPython(t *testing.T) {
	corpus := []any{
		"short", "",
		strings.Repeat("a", EvidenceCap-1),
		strings.Repeat("a", EvidenceCap),
		strings.Repeat("a", EvidenceCap+1),
		strings.Repeat("é", EvidenceCap-1),
		strings.Repeat("é", EvidenceCap),
		strings.Repeat("é", EvidenceCap+1),
		strings.Repeat("研", EvidenceCap+1),
		// Non-strings pass through untouched — including the ones a naive
		// `len()` would accept.
		42.0, true, nil, []any{"a"},
	}
	in, err := json.Marshal(corpus)
	if err != nil {
		t.Fatal(err)
	}
	out := probeRT(t,
		"print(json.dumps([rt._clip(v) for v in json.loads(sys.argv[2])]))",
		string(in))
	var want []any
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	var clipped, untouched int
	for i, v := range corpus {
		gotB, _ := json.Marshal(clipAttr(v))
		wantB, _ := json.Marshal(want[i])
		if string(gotB) != string(wantB) {
			t.Errorf("_clip differs on element %d — the clipped value is what "+
				"lands in the trace row\n  go %s\n  py %s", i, gotB, wantB)
		}
		if s, ok := v.(string); ok && string(gotB) != string(mustJSON(s)) {
			clipped++
		} else {
			untouched++
		}
	}
	if clipped == 0 || untouched == 0 {
		t.Fatalf("corpus reaches only one answer: clipped=%d untouched=%d",
			clipped, untouched)
	}
}

func mustJSON(v any) []byte {
	b, _ := json.Marshal(v)
	return b
}

// ---------------------------------------------------------------------
// record_edge / record_path — the written bytes
// ---------------------------------------------------------------------

// One recording scenario: a list of edges, each with its own ordered
// attrs. What is compared is the CONTENT of build/trace.jsonl, line for
// line, because that file is the artifact — a downstream reader that sees
// a different row sees a run that went somewhere else.
//
// `ts` is pinned on both sides (Go through SetNowForTest, Python by
// replacing rt._now) so the only thing left to differ is what this module
// decides to write.
const traceScenarios = `[
  {"name": "one known edge, no attrs",
   "edges": [["loop.start", "loop.decompose", {}]]},
  {"name": "an unknown node is recorded AND flagged",
   "edges": [["loop.start", "not.a.real.node", {}]]},
  {"name": "both ends unknown",
   "edges": [["nope.one", "nope.two", {}]]},
  {"name": "attrs keep the caller's key ORDER",
   "edges": [["loop.start", "loop.decompose", {"z": 1, "a": 2, "m": 3}]]},
  {"name": "attr values of every JSON shape",
   "edges": [["loop.start", "loop.decompose",
              {"s": "text", "n": 4, "f": 1.5, "b": true, "nul": null,
               "lst": [1, "two"], "obj": {"k": "v"}}]]},
  {"name": "a long attr is clipped, and says so",
   "edges": [["loop.start", "loop.decompose", {"evidence": "LONGSTRING"}]]},
  {"name": "a secret in an attr is scrubbed",
   "edges": [["loop.start", "loop.decompose",
              {"note": "token sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}]]},
  {"name": "a loop_id rides every row",
   "edges": [["loop.start", "loop.decompose", {}],
             ["loop.decompose", "loop.execute", {}]]},
  {"name": "several edges append in order",
   "edges": [["loop.start", "loop.decompose", {}],
             ["loop.decompose", "loop.execute", {}],
             ["loop.execute", "loop.finalize", {}]]}
]`

func TestRecordEdgeWritesWhatCPythonWrites(t *testing.T) {
	pyDir := safeRunDir(t)
	goDir := safeRunDir(t)
	const fixedTS = "2026-08-27T00:00:00+00:00"

	out := probeRT(t, ""+
		"rt._now = lambda: sys.argv[3]\n"+
		"import os\n"+
		"root = sys.argv[4]\n"+
		"res = []\n"+
		"for i, sc in enumerate(json.loads(sys.argv[2])):\n"+
		"    rd = os.path.join(root, 'sc%d' % i)\n"+
		"    os.makedirs(os.path.join(rd, 'build'), exist_ok=True)\n"+
		"    wrote = []\n"+
		"    for frm, to, attrs in sc['edges']:\n"+
		"        wrote.append(rt.record_edge(frm, to, loop_id='L1',\n"+
		"                                    run_dir=rd, **attrs))\n"+
		"    p = os.path.join(rd, 'build', 'trace.jsonl')\n"+
		"    text = open(p, encoding='utf-8').read() if os.path.exists(p) else ''\n"+
		"    res.append({'wrote': wrote, 'text': text})\n"+
		"print(json.dumps(res))",
		strings.Replace(traceScenarios, "LONGSTRING",
			strings.Repeat("x", EvidenceCap+50), 1),
		fixedTS, pyDir)
	var want []struct {
		Wrote []bool `json:"wrote"`
		Text  string `json:"text"`
	}
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	restore := SetNowForTest(func() string { return fixedTS })
	defer restore()
	ResetDropsForTest()

	raw, lerr := pyval.LoadsOrdered(strings.Replace(traceScenarios, "LONGSTRING",
		strings.Repeat("x", EvidenceCap+50), 1))
	if lerr != nil {
		t.Fatal(lerr)
	}
	scs, ok := raw.(pyval.List)
	if !ok {
		t.Fatalf("the scenario blob decoded as %T, not a list", raw)
	}
	if len(scs) != len(want) {
		t.Fatalf("probe returned %d scenarios for %d", len(want), len(scs))
	}

	var rows, flagged, scrubbed, clippedRows int
	for i, item := range scs {
		sc := item.(pyval.Obj)
		nameV, _ := sc.Get("name")
		name, _ := nameV.(string)
		edgesV, _ := sc.Get("edges")
		edges, _ := edgesV.(pyval.List)

		rd := filepath.Join(goDir, "sc"+itoaRT(i))
		if err := os.MkdirAll(filepath.Join(rd, "build"), 0o777); err != nil {
			t.Fatal(err)
		}
		var wrote []bool
		for _, e := range edges {
			tri := e.(pyval.List)
			frm, _ := tri[0].(string)
			to, _ := tri[1].(string)
			attrs, _ := tri[2].(pyval.Obj)
			rdCopy := rd
			wrote = append(wrote, RecordEdge(context.Background(), nil, "",
				frm, to, EdgeOpts{LoopID: "L1", RunDir: &rdCopy, Attrs: attrs}))
		}
		text := ""
		if b, err := os.ReadFile(filepath.Join(rd, "build", "trace.jsonl")); err == nil {
			text = string(b)
		}

		for j := range wrote {
			if wrote[j] != want[i].Wrote[j] {
				t.Errorf("[%s] edge %d: record_edge returned go %v py %v",
					name, j, wrote[j], want[i].Wrote[j])
			}
		}
		if text != want[i].Text {
			t.Errorf("[%s] build/trace.jsonl is not byte-identical — this "+
				"file IS the trace\n  go %q\n  py %q", name, text, want[i].Text)
		}

		rows += strings.Count(want[i].Text, "\n")
		if strings.Contains(want[i].Text, "unknown_node") {
			flagged++
		}
		if strings.Contains(want[i].Text, "REDACTED") {
			scrubbed++
		}
		if strings.Contains(want[i].Text, "truncated") ||
			strings.Contains(want[i].Text, "clipped") ||
			strings.Contains(want[i].Text, "\\u2026") {
			clippedRows++
		}
	}

	if rows < 10 {
		t.Fatalf("only %d rows were written across every scenario; the "+
			"corpus is not exercising the writer", rows)
	}
	if flagged == 0 {
		t.Fatal("no scenario produced an unknown_node flag, so the vocabulary " +
			"check inside record_edge is untested")
	}
	if scrubbed == 0 {
		t.Fatal("no scenario produced a redaction, so the scrub pass — which " +
			"runs between building the row and writing it — is untested")
	}
	if clippedRows == 0 {
		t.Fatal("no scenario produced a clipped attr, so _clip's announcement " +
			"is untested at the WRITER (as opposed to in isolation)")
	}
}

func itoaRT(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}

// ---------------------------------------------------------------------
// read_trace -- the no-silent-drop contract
// ---------------------------------------------------------------------

// read_trace's whole reason for existing is that a trace quietly returning
// 40 of 41 edges is indistinguishable from a run that took 40. So the
// SKIPPED COUNT is compared as hard as the rows are: a port that reads the
// same rows but counts differently has broken the contract while looking
// correct.
var traceFiles = []struct {
	name string
	body string
}{
	{"empty file", ""},
	{"one good row", `{"ts":"t","from":"a","to":"b"}` + "\n"},
	{"blank lines are skipped, not counted",
		"\n" + `{"ts":"t","from":"a","to":"b"}` + "\n\n   \n"},
	{"no trailing newline", `{"ts":"t","from":"a","to":"b"}`},
	{"a malformed row costs only itself",
		`{"ts":"t","from":"a","to":"b"}` + "\n" +
			"{not json\n" +
			`{"ts":"t","from":"c","to":"d"}` + "\n"},
	{"several malformed rows",
		"{oops\n[1,2\n" + `{"ts":"t","from":"a","to":"b"}` + "\nnope\n"},
	{"a row that is valid JSON but not an object",
		"[1,2,3]\n" + `{"ts":"t","from":"a","to":"b"}` + "\n42\n\"str\"\n"},
	// A NUL byte, which is the TAINT loads_clean exists to refuse rather
	// than launder into legitimate-looking content. Written as an escape:
	// a literal control character in a source file is invisible to every
	// reviewer of it.
	{"a NUL byte in a row",
		"{\"ts\":\"t\",\"from\":\"a\x00b\",\"to\":\"c\"}\n" +
			`{"ts":"t","from":"a","to":"b"}` + "\n"},
	// U+2028 LINE SEPARATOR, INSIDE a JSON string value. Python's
	// str.splitlines() breaks on it and a "\n" split does not, so CPython
	// sees two unparseable fragments where a transcribed Go reader sees one
	// good row. The divergence is in the SKIPPED COUNT as much as the rows:
	// one engine calls the trace incomplete and the other calls it healthy.
	{"a line separator inside a string value",
		"{\"ts\":\"t\",\"from\":\"a\u2028b\",\"to\":\"c\"}\n" +
			`{"ts":"t","from":"a","to":"b"}` + "\n"},
	// U+001F UNIT SEPARATOR as padding. Python's str.strip() removes it
	// (it is one of the 29 whitespace code points) and Go's
	// strings.TrimSpace does not, because unicode.IsSpace says no. It is
	// also the ONE of those that splitlines does NOT break on, which is
	// what makes it a clean test of Strip alone.
	{"padded with whitespace only Python recognises",
		"\x1f" + `{"ts":"t","from":"a","to":"b"}` + "\x1f\n"},
	{"unicode survives the round trip",
		`{"ts":"t","from":"café","to":"研究","attrs":{"k":"ıİ"}}` + "\n"},
	{"a row with nested attrs",
		`{"ts":"t","from":"a","to":"b","attrs":{"z":1,"a":[1,{"k":"v"}]}}` + "\n"},
}

func TestReadTraceMatchesCPython(t *testing.T) {
	pyRoot := safeRunDir(t)
	goRoot := safeRunDir(t)

	type spec struct {
		Name string `json:"name"`
		Body string `json:"body"`
	}
	var in []spec
	for _, f := range traceFiles {
		in = append(in, spec{f.name, f.body})
	}
	blob, err := json.Marshal(in)
	if err != nil {
		t.Fatal(err)
	}

	out := probeRT(t, ""+
		"import os\n"+
		"root = sys.argv[3]\n"+
		"res = []\n"+
		"for i, sc in enumerate(json.loads(sys.argv[2])):\n"+
		"    rd = os.path.join(root, 'f%d' % i)\n"+
		"    os.makedirs(os.path.join(rd, 'build'), exist_ok=True)\n"+
		"    with open(os.path.join(rd, 'build', 'trace.jsonl'), 'w',\n"+
		"              encoding='utf-8') as fh:\n"+
		"        fh.write(sc['body'])\n"+
		"    rows, skipped = rt.read_trace(rd, counted=True)\n"+
		"    res.append({'rows': rows, 'skipped': skipped})\n"+
		"missing = os.path.join(root, 'nope')\n"+
		"rows, skipped = rt.read_trace(missing, counted=True)\n"+
		"res.append({'rows': rows, 'skipped': skipped})\n"+
		"print(json.dumps(res))",
		string(blob), pyRoot)
	var want []struct {
		// []any, not []map[string]any: CPython's read_trace appends
		// whatever loads_clean returns, and a trace file can hold a row
		// that is valid JSON and not an object.
		Rows    []any `json:"rows"`
		Skipped int   `json:"skipped"`
	}
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}
	if len(want) != len(traceFiles)+1 {
		t.Fatalf("probe returned %d results for %d files (+1 missing)",
			len(want), len(traceFiles))
	}

	var totalRows, totalSkipped int
	for i, f := range traceFiles {
		rd := filepath.Join(goRoot, "f"+itoaRT(i))
		if err := os.MkdirAll(filepath.Join(rd, "build"), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(rd, "build", "trace.jsonl"),
			[]byte(f.body), 0o666); err != nil {
			t.Fatal(err)
		}
		rows, skipped, _ := ReadTrace(rd)
		if skipped != want[i].Skipped {
			t.Errorf("[%s] SKIPPED count: go %d py %d — an undercount here is "+
				"exactly the silence this function exists to break",
				f.name, skipped, want[i].Skipped)
		}
		if len(rows) != len(want[i].Rows) {
			t.Errorf("[%s] row count: go %d py %d", f.name, len(rows), len(want[i].Rows))
		} else {
			for j := range rows {
				gb, _ := json.Marshal(rows[j])
				wb, _ := json.Marshal(want[i].Rows[j])
				if !eqJSON(gb, wb) {
					t.Errorf("[%s] row %d\n  go %s\n  py %s", f.name, j, gb, wb)
				}
			}
		}
		totalRows += len(want[i].Rows)
		totalSkipped += want[i].Skipped
	}

	rows, skipped, _ := ReadTrace(filepath.Join(goRoot, "nope"))
	last := want[len(want)-1]
	if len(rows) != len(last.Rows) || skipped != last.Skipped {
		t.Errorf("a MISSING trace file: go (%d rows, %d skipped) "+
			"py (%d rows, %d skipped)", len(rows), skipped,
			len(last.Rows), last.Skipped)
	}

	if totalRows == 0 || totalSkipped == 0 {
		t.Fatalf("the corpus reaches only one answer: rows=%d skipped=%d",
			totalRows, totalSkipped)
	}
}

func eqJSON(a, b []byte) bool {
	var x, y any
	if json.Unmarshal(a, &x) != nil || json.Unmarshal(b, &y) != nil {
		return false
	}
	xb, _ := json.Marshal(x)
	yb, _ := json.Marshal(y)
	return string(xb) == string(yb)
}

// record_path is `sum(1 for a,b in zip(seq, seq[1:]) if record_edge(...))`
// over `[n for n in nodes if n]`. Three things there are easy to get wrong
// and none is visible in the return value alone: the falsy FILTER runs
// BEFORE the pairing (so a hole closes the gap rather than breaking the
// chain), the pairing is consecutive rather than all-pairs, and the count
// is of rows WRITTEN rather than pairs attempted.
func TestRecordPathMatchesCPython(t *testing.T) {
	pyRoot := safeRunDir(t)
	goRoot := safeRunDir(t)
	const fixedTS = "2026-08-27T00:00:00+00:00"

	paths := [][]string{
		{},
		{"loop.start"},
		{"loop.start", "loop.decompose"},
		{"loop.start", "loop.decompose", "loop.execute", "loop.finalize"},
		{"loop.start", "", "loop.execute"},
		{"", "loop.start", "loop.execute", ""},
		{"", "", ""},
		{"loop.start", "loop.start"},
		{"nope.one", "loop.start", "nope.two"},
	}
	in, err := json.Marshal(paths)
	if err != nil {
		t.Fatal(err)
	}

	out := probeRT(t, ""+
		"rt._now = lambda: sys.argv[4]\n"+
		"import os\n"+
		"root = sys.argv[3]\n"+
		"res = []\n"+
		"for i, nodes in enumerate(json.loads(sys.argv[2])):\n"+
		"    rd = os.path.join(root, 'p%d' % i)\n"+
		"    os.makedirs(os.path.join(rd, 'build'), exist_ok=True)\n"+
		"    n = rt.record_path(nodes, loop_id='L1', run_dir=rd)\n"+
		"    p = os.path.join(rd, 'build', 'trace.jsonl')\n"+
		"    text = open(p, encoding='utf-8').read() if os.path.exists(p) else ''\n"+
		"    res.append({'n': n, 'text': text})\n"+
		"print(json.dumps(res))",
		string(in), pyRoot, fixedTS)
	var want []struct {
		N    int    `json:"n"`
		Text string `json:"text"`
	}
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("probe output was not JSON: %v\n%s", err, out)
	}

	restore := SetNowForTest(func() string { return fixedTS })
	defer restore()
	ResetDropsForTest()

	var wrote, empty int
	for i, nodes := range paths {
		rd := filepath.Join(goRoot, "p"+itoaRT(i))
		if err := os.MkdirAll(filepath.Join(rd, "build"), 0o777); err != nil {
			t.Fatal(err)
		}
		rdCopy := rd
		n := RecordPath(context.Background(), nil, "", nodes,
			EdgeOpts{LoopID: "L1", RunDir: &rdCopy})
		text := ""
		if b, rerr := os.ReadFile(filepath.Join(rd, "build", "trace.jsonl")); rerr == nil {
			text = string(b)
		}
		if n != want[i].N {
			t.Errorf("record_path%v returned go %d py %d", nodes, n, want[i].N)
		}
		if text != want[i].Text {
			t.Errorf("record_path%v wrote a different file\n  go %q\n  py %q",
				nodes, text, want[i].Text)
		}
		if want[i].N > 0 {
			wrote++
		} else {
			empty++
		}
	}
	if wrote == 0 || empty == 0 {
		t.Fatalf("paths reach only one answer: wrote=%d empty=%d", wrote, empty)
	}
}
