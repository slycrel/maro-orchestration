package runs

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// pyDumps renders an ordered object the way CPython would, by shipping it
// as a [[key, value], ...] list — a plain encoding/json encoding of a map
// would arrive SORTED, and key order is one of the three things under
// test here. `indent` selects json.dumps(d, indent=2) over bare
// json.dumps(d).
func pyDumps(t *testing.T, o pyval.Obj, indent bool) string {
	t.Helper()
	in, err := json.Marshal(tagged(o))
	if err != nil {
		t.Fatal(err)
	}
	mode := "json.dumps(d)"
	if indent {
		mode = "json.dumps(d, indent=2)"
	}
	cmd := exec.Command("python3", "-c", `
import json, sys
def rebuild(v):
    if isinstance(v, dict) and len(v) == 1 and "__obj__" in v:
        return {k: rebuild(x) for k, x in v["__obj__"]}
    if isinstance(v, dict) and len(v) == 1 and "__float__" in v:
        return float(v["__float__"])
    if isinstance(v, list):
        return [rebuild(x) for x in v]
    return v
d = rebuild(json.loads(sys.stdin.read()))
print(`+mode+`)`)
	cmd.Stdin = strings.NewReader(string(in))
	out, perr := cmd.Output()
	if perr != nil {
		if _, lookErr := exec.LookPath("python3"); lookErr != nil {
			t.Skipf("python3 unavailable: %v", lookErr)
		}
		t.Fatalf("the CPython probe could not run: %v", perr)
	}
	return strings.TrimRight(string(out), "\n")
}

// tagged is pyval's own probe transport, repeated here because the same
// hazard applies: an Obj shipped as a plain JSON object arrives SORTED,
// and a whole float64 arrives as an int (encoding/json writes 3.0 as
// "3"). Either one reads exactly like a real divergence.
func tagged(v any) any {
	switch t := v.(type) {
	case pyval.Obj:
		pairs := make([]any, 0, len(t))
		for _, f := range t {
			pairs = append(pairs, []any{f.Key, tagged(f.Val)})
		}
		return map[string]any{"__obj__": pairs}
	case pyval.List:
		out := make([]any, len(t))
		for i, e := range t {
			out[i] = tagged(e)
		}
		return out
	case json.Number:
		// LoadsOrdered hands numbers back as json.Number; Plain is what
		// decides int-vs-float, and its float arm needs the tag.
		if _, err := t.Int64(); err == nil {
			return pyval.Plain(t)
		}
		f, _ := t.Float64()
		return tagged(f)
	case float64:
		return map[string]any{"__float__": strconv.FormatFloat(t, 'g', -1, 64)}
	}
	return pyval.Plain(v)
}

func keysOf(o pyval.Obj) []string {
	out := make([]string, 0, len(o))
	for _, f := range o {
		out = append(out, f.Key)
	}
	return out
}

// TestMetadataJSONMatchesCPython: metadata.json is written with
// json.dumps(meta, indent=2) in Python and was written with
// json.MarshalIndent here, which differs three ways at once — sorted
// keys, HTML-escaped `<` `>` `&`, and raw UTF-8 where json.dumps defaults
// to ensure_ascii. The goal PROMPT lands in this file verbatim and
// closure summaries carry FailedCheckSignature's "=>", so both runtimes'
// readers saw a file the other would never have written (adversarial
// mission-r7 HIGH).
func TestMetadataJSONMatchesCPython(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "h-r7", "grep '<div>' index.html > n.txt (研究)")
	if err != nil {
		t.Fatal(err)
	}
	conf := 0.85
	if err := StampVerdict(rd, boolPtr(true), "go_closure_v1",
		"Achieved: tests pass => exit 0 & the café page renders.",
		&conf, "", []string{"no résumé check <yet>"}); err != nil {
		t.Fatal(err)
	}
	if err := Finalize(rd, "done"); err != nil {
		t.Fatal(err)
	}
	raw, rerr := os.ReadFile(filepath.Join(rd, "metadata.json"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	v, lerr := pyval.LoadsOrdered(string(raw))
	if lerr != nil {
		t.Fatalf("metadata.json is not loadable: %v\n%s", lerr, raw)
	}
	meta, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("metadata.json is not an object: %s", raw)
	}

	// The ORDER claim, asserted by hand against write_metadata's own
	// sequence for the subset this port writes (handle_id, prompt,
	// started_at, status, pid), then the verdict tuple in
	// _apply_verdict_tuple's assignment order, then ended_at from the
	// finalize. goal_verdict_downgrade_reason is absent because an empty
	// reason POPS rather than writing null.
	want := []string{
		"handle_id", "prompt", "started_at", "status", "pid",
		"goal_verdict_source", "goal_verdict_summary",
		"goal_verdict_confidence", "goal_verdict_gaps", "goal_achieved",
		"ended_at",
	}
	if got := keysOf(meta); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("key order:\n got %v\nwant %v", got, want)
	}

	// Anti-vacuity before the assertion: the pre-fix writer replayed over
	// the SAME content must lose. Without this the byte comparison below
	// would pass for a corpus with no `>`, no `&` and no non-ASCII in it.
	old, merr := json.MarshalIndent(pyval.Plain(meta), "", "  ")
	if merr != nil {
		t.Fatal(merr)
	}
	if string(old) == string(raw) {
		t.Fatalf("json.MarshalIndent already agrees with json.dumps on this "+
			"metadata: the fixture cannot have caught the finding\n%s", raw)
	}

	if got, want := string(raw), pyDumps(t, meta, true); got != want {
		t.Fatalf("metadata.json bytes:\n go:\n%s\n py:\n%s", got, want)
	}
}

// TestVerdictRowMatchesCPython: _persist_verdict_row writes
// json.dumps(_full, default=str) — default separators, so `", "` and
// `": "` — with ts and loop_id ahead of the row. Go wrote json.Marshal of
// a map: unspaced, sorted, HTML-escaped, and with NO loop_id at all,
// which is what tells a restarted run's parent verdict from its child in
// this append-only file.
func TestVerdictRowMatchesCPython(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "build"), 0o755); err != nil {
		t.Fatal(err)
	}
	row := pyval.Obj{
		{Key: "complete", Val: false},
		{Key: "confidence", Val: 0.6},
		{Key: "checks_run", Val: 2},
		{Key: "checks_passed", Val: 1},
		{Key: "inconclusive_count", Val: 0},
		{Key: "judged", Val: true},
		{Key: "downgrade_reason", Val: ""},
		{Key: "gaps", Val: []string{"研究 was not run"}},
		{Key: "summary", Val: "Not achieved: <index.html> missing & stale."},
		{Key: "failed_checks", Val: []string{
			"greeting exists => exit 3: no such file"}},
		{Key: "fingerprint", Val: "abc123"},
		{Key: "modality_distribution", Val: map[string]int{"static": 2}},
		{Key: "check_results", Val: pyval.List{pyval.Obj{
			{Key: "description", Val: "does it build?"},
			{Key: "command", Val: "go build ./... 2>&1"},
			{Key: "exit_code", Val: 1},
			{Key: "outcome", Val: "fail"},
			{Key: "stdout", Val: ""},
			{Key: "stderr", Val: "café: no such file"},
			{Key: "plan_index", Val: 1},
		}}},
	}
	if err := AppendVerdictRow(dir, "go-r7loop", row); err != nil {
		t.Fatal(err)
	}
	raw, rerr := os.ReadFile(filepath.Join(dir, "build", "closure_verdicts.jsonl"))
	if rerr != nil {
		t.Fatal(rerr)
	}
	line := strings.TrimRight(string(raw), "\n")
	v, lerr := pyval.LoadsOrdered(line)
	if lerr != nil {
		t.Fatalf("row is not loadable: %v\n%s", lerr, line)
	}
	got, ok := v.(pyval.Obj)
	if !ok {
		t.Fatalf("row is not an object: %s", line)
	}
	keys := keysOf(got)
	if len(keys) < 2 || keys[0] != "ts" || keys[1] != "loop_id" {
		t.Fatalf("ts and loop_id lead the row in Python; got %v", keys)
	}
	if lid, _ := got.Get("loop_id"); lid != "go-r7loop" {
		t.Fatalf("loop_id: %v — it was missing from every Go row before r7", lid)
	}
	// plan_index is the documented join key for claim_coverage.check_index
	// and rides each check row.
	crs, _ := got.Get("check_results")
	crList, _ := crs.(pyval.List)
	if len(crList) != 1 {
		t.Fatalf("check_results: %v", crs)
	}
	cr, _ := crList[0].(pyval.Obj)
	if pi, present := cr.Get("plan_index"); !present || pyval.IntOf(pi) != 1 {
		t.Fatalf("plan_index missing from the check row: %v", cr)
	}

	old, merr := json.Marshal(pyval.Plain(got))
	if merr != nil {
		t.Fatal(merr)
	}
	if string(old) == line {
		t.Fatalf("json.Marshal already agrees with json.dumps on this row: "+
			"the fixture cannot have caught the finding\n%s", line)
	}
	if want := pyDumps(t, got, false); line != want {
		t.Fatalf("verdict row bytes:\n go: %s\n py: %s", line, want)
	}
}

func boolPtr(b bool) *bool { return &b }
