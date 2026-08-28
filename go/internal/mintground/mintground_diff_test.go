package mintground

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// mgEntry is one fixture node in a run tree.
type mgEntry struct {
	Path string `json:"path"`
	Kind string `json:"kind"`
	Data string `json:"data"`
}

// mgSpec is one scenario.
//
// No `omitempty` anywhere (L6): the probe subscripts these, so an omitted
// field would be a KeyError rather than a zero value, and a probe that
// fills its own default cannot tell "the scenario said empty" from "the
// scenario forgot".
type mgSpec struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
	Text string `json:"text"`
	// Events is JSON SOURCE for the event list ground_text joins against,
	// so a scenario can hand in a shape no fixture tree would produce.
	Events string `json:"events"`
	// Grounding is JSON SOURCE for a stamp list the renderers are driven
	// with directly. `null` is None, which is a real input upstream.
	Grounding string `json:"grounding"`
	// Event is JSON SOURCE for the single event dict the five predicates
	// are asked about.
	Event string `json:"event"`

	RunRef  string   `json:"run_ref"`
	Lessons []string `json:"lessons"`
	// ResolveTo is what the runs seam answers, relative to the scenario's
	// fixture directory; empty means it answers None.
	ResolveTo     string `json:"resolve_to"`
	ResolveRaises bool   `json:"resolve_raises"`
	ImportFails   bool   `json:"import_fails"`
	// RunDir is the run directory collect_run_tool_events is pointed at,
	// relative to the fixture directory; empty means the directory itself.
	RunDir string `json:"run_dir"`

	// CapOverride drives _MAX_EVENTS for the scenarios about the event
	// cap; the flag is separate so that "cap at zero" stays sayable.
	CapOverride    int       `json:"cap_override"`
	CapOverrideSet bool      `json:"cap_override_set"`
	Tree           []mgEntry `json:"tree"`
}

func bs(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

// f, d and ln are the three fixture-node spellings, kept short because
// run trees are read as a shape, not as prose. fb seeds RAW BYTES, which
// is how a scenario reaches the errors="replace" path.
func f(path, body string) mgEntry { return mgEntry{Path: path, Kind: "file", Data: bs(body)} }
func fb(path string, body []byte) mgEntry {
	return mgEntry{Path: path, Kind: "file",
		Data: base64.StdEncoding.EncodeToString(body)}
}
func d(path string) mgEntry       { return mgEntry{Path: path, Kind: "dir"} }
func ln(path, tgt string) mgEntry { return mgEntry{Path: path, Kind: "symlink", Data: tgt} }

func mgMakeTree(t *testing.T, base string, entries []mgEntry) {
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

// stampJSON mirrors the probe's stamps_json, including the ABSENCE of the
// note key on a tied-supported stamp.
func stampJSON(s Stamp) map[string]any {
	out := map[string]any{
		"claim":    s.Claim,
		"family":   s.Family,
		"status":   s.Status,
		"receipts": strAny(s.Receipts),
	}
	if s.HasNote {
		out["note"] = s.Note
	}
	return out
}

func stampsJSON(ss []Stamp) []any {
	out := []any{}
	for _, s := range ss {
		out = append(out, stampJSON(s))
	}
	return out
}

func eventJSON(e Event) map[string]any {
	return map[string]any{"ref": e.Ref, "name": e.Name, "input": e.Input,
		"output": e.Output, "is_error": e.IsError}
}

func strAny(ss []string) []any {
	out := make([]any, 0, len(ss))
	for _, s := range ss {
		out = append(out, s)
	}
	return out
}

func claimJSON(c Claim) map[string]any {
	return map[string]any{"claim": c.Claim, "family": c.Family,
		"_sentence": c.Sentence}
}

// mgEvents decodes a scenario's `events` JSON into the Go event type.
func mgEvents(t *testing.T, src string) []Event {
	t.Helper()
	var raw []struct {
		Ref     string `json:"ref"`
		Name    string `json:"name"`
		Input   string `json:"input"`
		Output  string `json:"output"`
		IsError bool   `json:"is_error"`
	}
	if err := json.Unmarshal([]byte(src), &raw); err != nil {
		t.Fatalf("bad events fixture %q: %v", src, err)
	}
	out := []Event{}
	for _, r := range raw {
		out = append(out, Event{r.Ref, r.Name, r.Input, r.Output, r.IsError})
	}
	return out
}

// mgGrounding decodes a scenario's `grounding` JSON into stamps. `null`
// decodes to a nil slice, which is what None reaches the renderers as.
//
// The statuses in these fixtures are strings because the Go type says
// they are; a stamp whose status is a number is not reachable through
// this port's API and is not a shape the differential can honestly ask
// about.
func mgGrounding(t *testing.T, src string) []Stamp {
	t.Helper()
	var raw []struct {
		Claim  string `json:"claim"`
		Family string `json:"family"`
		Status string `json:"status"`
	}
	if err := json.Unmarshal([]byte(src), &raw); err != nil {
		t.Fatalf("bad grounding fixture %q: %v", src, err)
	}
	if raw == nil {
		return nil
	}
	out := []Stamp{}
	for _, r := range raw {
		out = append(out, Stamp{Claim: r.Claim, Family: r.Family,
			Status: r.Status})
	}
	return out
}

func mgEvent(t *testing.T, src string) Event {
	t.Helper()
	evs := mgEvents(t, "["+src+"]")
	return evs[0]
}

// goRun mirrors the probe's run_one over the Go package.
func goRun(t *testing.T, sc mgSpec, root string) map[string]any {
	t.Helper()
	out := map[string]any{"name": sc.Name, "cls": "", "msg": ""}
	base := filepath.Join(root, sc.Name)
	if sc.CapOverrideSet {
		saved := maxEvents
		maxEvents = sc.CapOverride
		defer func() { maxEvents = saved }()
	}
	switch sc.Kind {
	case "sentences":
		out["parts"] = strAny(splitSentences(sc.Text))
	case "instruction":
		out["hit"] = isInstruction(sc.Text)
	case "retrospective":
		out["hit"] = isRetrospective(sc.Text)
	case "clause_tail":
		out["tail"] = clauseTail(sc.Text)
	case "claims":
		cs := []any{}
		for _, c := range ExtractClaims(sc.Text) {
			cs = append(cs, claimJSON(c))
		}
		out["claims"] = cs
	case "tie_tokens":
		out["toks"] = strAny(tieTokens(sc.Text))
	case "specific_tokens":
		out["toks"] = strAny(specificTokens(tieTokens(sc.Text)))
	case "event_preds":
		ev := mgEvent(t, sc.Event)
		out["preds"] = map[string]any{
			"exec": isExec(ev), "fetch": isFetch(ev), "auth": isAuth(ev),
			"test": isTest(ev), "probe": isProbe(ev)}
	case "ground_text":
		out["stamps"] = stampsJSON(GroundText(sc.Text, mgEvents(t, sc.Events)))
	case "summary":
		out["text"] = GroundingSummary(mgGrounding(t, sc.Grounding))
	case "marker":
		out["text"] = GroundingMarker(mgGrounding(t, sc.Grounding))
	case "has_unsupported":
		out["hit"] = HasUnsupported(mgGrounding(t, sc.Grounding))
	case "collect":
		mgMakeTree(t, base, sc.Tree)
		target := base
		if sc.RunDir != "" {
			target = filepath.Join(base, sc.RunDir)
		}
		evs, present, err := CollectRunToolEvents(target)
		if err != nil {
			out["cls"] = pyval.ClassOf(err)
			out["msg"] = err.Error()
			break
		}
		out["present"] = present
		list := []any{}
		for _, e := range evs {
			list = append(list, eventJSON(e))
		}
		out["events"] = list
	case "ground_lessons":
		mgMakeTree(t, base, sc.Tree)
		lessons := sc.Lessons
		if lessons == nil {
			lessons = []string{}
		}
		gs := []any{}
		for _, g := range GroundLessonsForRun(lessons, sc.RunRef,
			depsFor(sc, base)) {
			gs = append(gs, stampsJSON(g))
		}
		out["stamps"] = gs
	default:
		t.Fatalf("unknown kind %s", sc.Kind)
	}
	return out
}

// depsFor mirrors the probe's install_runs. A nil ResolveRunDir is the
// import that did not resolve.
func depsFor(sc mgSpec, base string) Deps {
	if sc.ImportFails {
		return Deps{}
	}
	return Deps{ResolveRunDir: func(string) (string, bool, error) {
		if sc.ResolveRaises {
			return "", false, &pyval.PyErr{Class: "RuntimeError",
				Msg: "no such run"}
		}
		if sc.ResolveTo == "" {
			return "", false, nil
		}
		return filepath.Join(base, sc.ResolveTo), true, nil
	}}
}

func TestMintGroundingMatchesCPython(t *testing.T) {
	scs := mgScenarios()

	goRoot := t.TempDir()
	goRecs := make([]map[string]any, 0, len(scs))
	for _, sc := range scs {
		goRecs = append(goRecs, goRun(t, sc, goRoot))
	}

	pyRecs := runMintGroundProbe(t, scs)
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

func runMintGroundProbe(t *testing.T, scs []mgSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	specPath := filepath.Join(dir, "mintground-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("mintground_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// The probe WRITES: it builds a run tree per scenario, under this
	// test's own temp directory and nowhere else.
	pyRoot := t.TempDir()
	out := pyprobe.Probe{Marker: "mint_grounding.py",
		Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "mint_grounding.py"),
			specPath, pyRoot)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestMintGroundingScenarioNamesAreUnique(t *testing.T) {
	// Every scenario names a DIRECTORY, so a duplicate would let the
	// second run inherit the first one's tree and agree with the probe
	// for the wrong reason.
	seen := map[string]bool{}
	for _, sc := range mgScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q", sc.Name)
		}
		seen[sc.Name] = true
	}
}
