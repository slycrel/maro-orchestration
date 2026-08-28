package claimverify

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// cvEntry is one fixture node in the project tree.
type cvEntry struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Data string `json:"data"`
}

// cvSpec is one scenario.
//
// No `omitempty` anywhere (L6): the probe subscripts these, so an omitted
// field would be a KeyError rather than a zero value, and a probe that
// fills its own default cannot tell "the scenario said empty" from "the
// scenario forgot".
type cvSpec struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
	Text string `json:"text"`
	// Root is the project_root argument, RELATIVE to the scenario's own
	// fixture directory — the JSON cannot name a path neither engine
	// knows yet. Empty means the fixture directory itself.
	Root string `json:"root"`
	// RootIsNone passes None/nil instead, which sends both engines
	// through the inference path.
	RootIsNone bool `json:"root_is_none"`
	// Cwd is where the scenario runs, relative to the fixture directory.
	Cwd string `json:"cwd"`
	// RunCwd is what the llm seam answers, relative to the fixture
	// directory; empty means it answers None.
	RunCwd         string `json:"run_cwd"`
	RunCwdRaises   bool   `json:"run_cwd_raises"`
	LlmImportFails bool   `json:"llm_import_fails"`

	OnlyIfHallucinations bool `json:"only_if_hallucinations"`
	CheckSymbols         bool `json:"check_symbols"`

	// Report is JSON SOURCE for a report a summary scenario hands in
	// ready-made, so the rendering can be driven past what the verifiers
	// happen to produce.
	// CapOverride drives _INDEX_MAX_DIRS for the scenarios about the
	// walk cap; 0 leaves the shipped value alone.
	CapOverride    int       `json:"cap_override"`
	CapOverrideSet bool      `json:"cap_override_set"`
	Report         string    `json:"report"`
	Tree           []cvEntry `json:"tree"`
}

func bs(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

// f, d and ln are the three fixture-node spellings, kept short because
// scenario trees are read as a shape, not as prose.
func f(path, body string) cvEntry { return cvEntry{Path: path, Kind: "file", Data: bs(body)} }
func d(path string) cvEntry       { return cvEntry{Path: path, Kind: "dir"} }
func ln(path, tgt string) cvEntry { return cvEntry{Path: path, Kind: "symlink", Data: tgt} }

func cvMakeTree(t *testing.T, base string, entries []cvEntry) {
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
		case "symlink":
			if err := os.Symlink(e.Data, p); err != nil {
				t.Fatal(err)
			}
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

// claimReportJSON mirrors the probe's claim_report_json, including the
// dict-as-pairs rendering of suffix_matched: the ORDER is part of the
// answer, because Python's dict preserves insertion order and the Go side
// keeps a slice for exactly that reason.
func claimReportJSON(r ClaimReport) map[string]any {
	pairs := []any{}
	for _, m := range r.SuffixMatched {
		pairs = append(pairs, []any{m.Claim, m.RelPath})
	}
	return map[string]any{
		"raw_claims":         strAny(r.RawClaims),
		"verified":           strAny(r.Verified),
		"not_found":          strAny(r.NotFound),
		"unresolvable":       strAny(r.Unresolvable),
		"suffix_matched":     pairs,
		"has_hallucinations": r.HasHallucinations(),
		"rate":               r.HallucinationRate(),
		"summary":            r.Summary(),
	}
}

func symbolReportJSON(r SymbolReport) map[string]any {
	return map[string]any{
		"raw_claims":         strAny(r.RawClaims),
		"verified":           strAny(r.Verified),
		"not_found":          strAny(r.NotFound),
		"has_hallucinations": r.HasHallucinations(),
		"summary":            r.Summary(),
	}
}

func strAny(ss []string) []any {
	out := make([]any, 0, len(ss))
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}

func sortedKeys(m map[string]bool) []any {
	out := []string{}
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return strAny(out)
}

func cvDecode(t *testing.T, src string) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal([]byte(src), &out); err != nil {
		t.Fatalf("bad fixture JSON %q: %v", src, err)
	}
	return out
}

func strList(t *testing.T, v any) []string {
	t.Helper()
	out := []string{}
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

// rootArg mirrors the probe's root_arg.
func rootArg(sc cvSpec, base string) *string {
	if sc.RootIsNone {
		return nil
	}
	p := base
	if sc.Root != "" {
		p = filepath.Join(base, sc.Root)
	}
	return &p
}

// depsFor mirrors the probe's install_llm. A nil DefaultSubprocessCwd is
// the import that did not resolve.
func depsFor(sc cvSpec, base string) Deps {
	if sc.LlmImportFails {
		return Deps{}
	}
	return Deps{DefaultSubprocessCwd: func() (string, error) {
		if sc.RunCwdRaises {
			return "", &pyval.PyErr{Class: "RuntimeError", Msg: "no run"}
		}
		if sc.RunCwd == "" {
			return "", nil
		}
		return filepath.Join(base, sc.RunCwd), nil
	}}
}

// rel mirrors the probe's rel(): a record carries paths relative to the
// scenario base, because the absolute one names a temp directory that
// differs between the two engines by construction.
func rel(t *testing.T, p, base string) string {
	t.Helper()
	r, err := filepath.Rel(base, p)
	if err != nil {
		return "<unrelated:" + p + ">"
	}
	return r
}

// goRun mirrors the probe's run_one over the Go package.
func goRun(t *testing.T, sc cvSpec, root string) map[string]any {
	t.Helper()
	out := map[string]any{"name": sc.Name, "cls": "", "msg": ""}
	base := filepath.Join(root, sc.Name)
	switch sc.Kind {
	case "file_claims":
		out["claims"] = strAny(ExtractFileClaims(sc.Text))
	case "file_path_re":
		out["claims"] = strAny(findFilePaths(sc.Text))
	case "symbol_claims":
		out["symbols"] = strAny(ExtractSymbolClaims(sc.Text))
	case "synthesis":
		out["hit"] = IsSynthesisStep(sc.Text)
	case "claim_summary":
		v := cvDecode(t, sc.Report)
		sm := []SuffixMatch{}
		for _, pair := range v["suffix_matched"].([]any) {
			kv := pair.([]any)
			sm = append(sm, SuffixMatch{Claim: kv[0].(string),
				RelPath: kv[1].(string)})
		}
		out["report"] = claimReportJSON(ClaimReport{
			RawClaims:     strList(t, v["raw_claims"]),
			Verified:      strList(t, v["verified"]),
			NotFound:      strList(t, v["not_found"]),
			Unresolvable:  strList(t, v["unresolvable"]),
			SuffixMatched: sm,
		})
	case "symbol_summary":
		v := cvDecode(t, sc.Report)
		out["report"] = symbolReportJSON(SymbolReport{
			RawClaims: strList(t, v["raw_claims"]),
			Verified:  strList(t, v["verified"]),
			NotFound:  strList(t, v["not_found"]),
		})
	default:
		cvMakeTree(t, base, sc.Tree)
		deps := depsFor(sc, base)
		prev, err := os.Getwd()
		if err != nil {
			t.Fatal(err)
		}
		target := base
		if sc.Cwd != "" {
			target = filepath.Join(base, sc.Cwd)
		}
		if err := os.Chdir(target); err != nil {
			t.Fatal(err)
		}
		defer func() {
			if err := os.Chdir(prev); err != nil {
				t.Fatal(err)
			}
		}()
		if sc.CapOverrideSet {
			saved := indexMaxDirs
			indexMaxDirs = sc.CapOverride
			defer func() { indexMaxDirs = saved }()
		}
		switch sc.Kind {
		case "infer_root":
			out["root"] = rel(t, InferProjectRoot(deps), base)
		case "tree_index":
			names, relpaths := treeIndex(*rootArg(sc, base))
			out["names"] = sortedKeys(names)
			out["relpaths"] = sortedKeys(relpaths)
		case "symbol_index":
			out["symbols"] = sortedKeys(buildSymbolIndex(*rootArg(sc, base)))
		case "verify_files":
			out["report"] = claimReportJSON(
				VerifyFileClaims(sc.Text, rootArg(sc, base), deps))
		case "verify_symbols":
			out["report"] = symbolReportJSON(
				VerifySymbolClaims(sc.Text, rootArg(sc, base), deps))
		case "annotate":
			out["text"] = AnnotateResult(sc.Text, rootArg(sc, base),
				sc.OnlyIfHallucinations, sc.CheckSymbols, deps)
		default:
			t.Fatalf("unknown kind %s", sc.Kind)
		}
	}
	return out
}

func TestClaimVerifyMatchesCPython(t *testing.T) {
	scs := cvScenarios()

	goRoot := t.TempDir()
	goRecs := make([]map[string]any, 0, len(scs))
	for _, sc := range scs {
		goRecs = append(goRecs, goRun(t, sc, goRoot))
	}

	pyRecs := runClaimVerifyProbe(t, scs)
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

func runClaimVerifyProbe(t *testing.T, scs []cvSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	specPath := filepath.Join(dir, "claimverify-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("claimverify_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// The probe WRITES: it builds a project tree per scenario, under this
	// test's own temp directory and nowhere else. It also CHDIRs, which is
	// why each scenario restores the previous cwd in a finally.
	pyRoot := t.TempDir()
	out := pyprobe.Probe{Marker: "claim_verifier.py",
		Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "claim_verifier.py"),
			specPath, pyRoot)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestClaimVerifyScenarioNamesAreUnique(t *testing.T) {
	// Every scenario names a DIRECTORY, so a duplicate would let the
	// second run inherit the first one's tree and agree with the probe
	// for the wrong reason.
	seen := map[string]bool{}
	for _, sc := range cvScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q", sc.Name)
		}
		seen[sc.Name] = true
	}
}
