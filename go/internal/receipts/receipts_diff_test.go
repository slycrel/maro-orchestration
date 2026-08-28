package receipts

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// rcEntry is one fixture file in the call record.
type rcEntry struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Data string `json:"data"`
	Size int64  `json:"size"`
}

// rcSpec is one scenario.
//
// No `omitempty` anywhere (L6): the probe subscripts these, so an omitted
// field would be a KeyError rather than a zero value, and a probe that
// fills its own default cannot tell "the scenario said empty" from "the
// scenario forgot".
type rcSpec struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
	Text string `json:"text"`
	// Limit is _clip's second argument.
	Limit int `json:"limit"`
	// Value, Cap, Loaded, CheckResults and RunDirValue are JSON SOURCE,
	// decoded on both sides. That is how a scenario carries a value whose
	// TYPE is the point — a string count, a list where a mapping belongs,
	// a cap that is not an int.
	Value        string `json:"value"`
	Cap          string `json:"cap"`
	Loaded       string `json:"loaded"`
	CheckResults string `json:"check_results"`
	RunDirValue  string `json:"run_dir_value"`
	// RunDirIsPath selects the scenario's own fixture directory over
	// RunDirValue; the JSON cannot name a path neither engine knows yet.
	RunDirIsPath bool      `json:"run_dir_is_path"`
	Tree         []rcEntry `json:"tree"`

	RunsImportFails bool `json:"runs_import_fails"`
	RunDirNone      bool `json:"run_dir_none"`
	RunDirRaises    bool `json:"run_dir_raises"`
}

func bs(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

// pv mirrors the probe's pv(): a pyval.Obj becomes a list of [key, value]
// pairs, because the key ORDER is part of the answer.
func pv(v any) any {
	switch t := v.(type) {
	case pyval.Obj:
		out := []any{}
		for _, f := range t {
			out = append(out, []any{f.Key, pv(f.Val)})
		}
		return out
	case []any:
		out := []any{}
		for _, x := range t {
			out = append(out, pv(x))
		}
		return out
	case pyval.List:
		return pv([]any(t))
	}
	return v
}

func rcDecode(t *testing.T, src string) any {
	t.Helper()
	v, err := pyval.LoadsOrdered(src)
	if err != nil {
		t.Fatalf("bad fixture JSON %q: %v", src, err)
	}
	return v
}

func rcList(t *testing.T, src string) []any {
	t.Helper()
	v := rcDecode(t, src)
	if v == nil {
		return nil
	}
	switch l := v.(type) {
	case []any:
		return l
	case pyval.List:
		return []any(l)
	}
	t.Fatalf("fixture %q is not a list", src)
	return nil
}

func rcMakeTree(t *testing.T, base string, entries []rcEntry) {
	t.Helper()
	if err := os.MkdirAll(base, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		p := filepath.Join(base, e.Path)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		switch e.Kind {
		case "dir":
			if err := os.MkdirAll(p, 0o755); err != nil {
				t.Fatal(err)
			}
		case "sparse":
			fh, err := os.Create(p)
			if err != nil {
				t.Fatal(err)
			}
			if err := fh.Truncate(e.Size); err != nil {
				t.Fatal(err)
			}
			fh.Close()
		default:
			raw, err := base64.StdEncoding.DecodeString(e.Data)
			if err != nil {
				t.Fatal(err)
			}
			if err := os.WriteFile(p, raw, 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
}

// goRun mirrors the probe's run_one over the Go package.
func goRun(t *testing.T, sc rcSpec, root string) map[string]any {
	t.Helper()
	out := map[string]any{"name": sc.Name, "cls": "", "msg": ""}
	base := filepath.Join(root, sc.Name)
	fail := func(err error) {
		out["cls"] = pyval.ClassOf(err)
		out["msg"] = err.Error()
	}
	switch sc.Kind {
	case "clip":
		out["text"] = clip(sc.Text, sc.Limit)
	case "neutralize":
		out["text"] = NeutralizeFenceText(sc.Text)
	case "display":
		out["text"] = display(rcDecode(t, sc.Value))
	case "path_token":
		out["tokens"] = strAny(pathToken(sc.Text))
	case "process_markers":
		out["hit"] = processMarkers.MatchString(sc.Text)
	case "check_tokens":
		toks, err := checkPathTokens(rcDecode(t, sc.CheckResults))
		if err != nil {
			fail(err)
			break
		}
		out["tokens"] = strAny(toks)
	case "load":
		rcMakeTree(t, base, sc.Tree)
		var arg any = base
		if !sc.RunDirIsPath {
			arg = rcDecode(t, sc.RunDirValue)
		}
		loaded, err := LoadReceipts(arg, rcDecode(t, sc.Cap))
		if err != nil {
			fail(err)
			break
		}
		out["loaded"] = pv(loaded)
	case "render":
		loaded, _ := rcDecode(t, sc.Loaded).(pyval.Obj)
		text, err := RenderReceiptEvidence(loaded,
			rcDecode(t, sc.CheckResults))
		if err != nil {
			fail(err)
			break
		}
		out["text"] = text
	case "audit":
		rcMakeTree(t, base, sc.Tree)
		logged := []any{}
		d := Deps{
			Debug: func(format string, args ...any) {
				logged = append(logged, sprintfPy(format, args...))
			},
		}
		if !sc.RunsImportFails {
			d.CurrentRunDir = func() (any, error) {
				if sc.RunDirRaises {
					return nil, errRunDir
				}
				if sc.RunDirNone {
					return nil, nil
				}
				return base, nil
			}
		}
		out["text"] = AuditReceiptBlock(rcDecode(t, sc.CheckResults), d)
		out["logged"] = logged
	default:
		t.Fatalf("unknown kind %s", sc.Kind)
	}
	return out
}

func strAny(ss []string) []any {
	out := make([]any, 0, len(ss))
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}

// sprintfPy renders the one `log.debug` call this module makes. The
// format is a literal with a single %s, so the probe's `fmt % args` and
// this agree by construction.
func sprintfPy(format string, args ...any) string {
	out := format
	for _, a := range args {
		out = strings.Replace(out, "%s", pyval.Str(a), 1)
	}
	return out
}

var errRunDir = &pyval.PyErr{Class: "RuntimeError", Msg: "no run"}

func TestReceiptsMatchCPython(t *testing.T) {
	scs := rcScenarios()

	goRoot := t.TempDir()
	goRecs := make([]map[string]any, 0, len(scs))
	for _, sc := range scs {
		goRecs = append(goRecs, goRun(t, sc, goRoot))
	}

	pyRecs := runReceiptsProbe(t, scs)
	if len(pyRecs) != len(goRecs) {
		t.Fatalf("probe returned %d records, want %d", len(pyRecs), len(goRecs))
	}
	for i := range scs {
		want := canon(pyRecs[i])
		got := canon(goRecs[i])
		if !reflect.DeepEqual(want, got) {
			t.Errorf("scenario %q diverges\n  cpython: %s\n  go:      %s",
				scs[i].Name, mustJSON(t, want), mustJSON(t, got))
		}
	}
}

func canon(rec map[string]any) any {
	blob, err := json.Marshal(rec)
	if err != nil {
		return map[string]any{"marshal-error": err.Error()}
	}
	var out any
	if err := json.Unmarshal(blob, &out); err != nil {
		return map[string]any{"unmarshal-error": err.Error()}
	}
	return out
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	blob, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(blob)
}

func runReceiptsProbe(t *testing.T, scs []rcSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	specPath := filepath.Join(dir, "receipts-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("receipts_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// The probe WRITES: it builds a call-record filesystem per scenario,
	// under this test's own temp directory and nowhere else.
	pyRoot := t.TempDir()
	out := pyprobe.Probe{Marker: "execution_receipts.py",
		Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "execution_receipts.py"),
			specPath, pyRoot)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestReceiptScenarioNamesAreUnique(t *testing.T) {
	// Every scenario names a DIRECTORY, so a duplicate would let the
	// second run inherit the first one's call files and agree with the
	// probe for the wrong reason.
	seen := map[string]bool{}
	for _, sc := range rcScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q", sc.Name)
		}
		seen[sc.Name] = true
	}
}
