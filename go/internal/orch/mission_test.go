package orch

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Every expected byte string below was MEASURED against CPython on this
// box, not derived from the json module's documentation.

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// --- the renderer ----------------------------------------------------------

// json.dumps escapes every code point from 0x7f up — DEL included, which
// IS ASCII — and emits astral planes as a UTF-16 surrogate PAIR. It does
// NOT escape < > & or /, which is where Go's encoder differs in the other
// direction.
func TestPyStringMatchesEnsureAscii(t *testing.T) {
	cases := map[string]string{
		"\u0000\u0007\b\t\n\u000b\f\r\u001f": `"\u0000\u0007\b\t\n\u000b\f\r\u001f"`,
		"\u007f":                             `"\u007f"`,
		"~\u0080\u00a0":                      `"~\u0080\u00a0"`,
		"\U0001F600":                         `"\ud83d\ude00"`,
		"\"\\/":                              `"\"\\/"`,
		"<>&":                                `"<>&"`,
		"\uffff\ud7ff":                       `"\uffff\ud7ff"`,
		"caf\u00e9 \u2192 done":              `"caf\u00e9 \u2192 done"`,
	}
	for in, want := range cases {
		got, err := pyString(in)
		if err != nil {
			t.Fatalf("%q: %v", in, err)
		}
		if got != want {
			t.Errorf("pyString(%q)\n got %s\nwant %s", in, got, want)
		}
	}
	if _, err := pyString("\xff\xfe"); err == nil {
		t.Error("byte-tainted text must be refused, not silently replaced")
	}
}

// json.dumps(..., indent=2) renders an EMPTY container inline and carries a
// space after every key. The item separator loses its trailing space here
// because a newline follows it — which is why the indent-2 sidecars matched
// byte-for-byte in earlier rounds while the single-line lane's ", " did not.
func TestDumpsIndent2MatchesPython(t *testing.T) {
	got, err := DumpsIndent2(pyObj{{Key: "a", Val: pyList{}}, {Key: "b", Val: pyObj{}}})
	if err != nil {
		t.Fatal(err)
	}
	if want := "{\n  \"a\": [],\n  \"b\": {}\n}"; got != want {
		t.Errorf("empty containers:\n got %q\nwant %q", got, want)
	}

	got, err = DumpsIndent2(pyObj{{Key: "a", Val: pyObj{{Key: "b", Val: pyObj{{Key: "c",
		Val: pyList{1, pyList{2, 3}, pyObj{{Key: "d", Val: nil}}}}}}}}})
	if err != nil {
		t.Fatal(err)
	}
	want := "{\n  \"a\": {\n    \"b\": {\n      \"c\": [\n        1,\n" +
		"        [\n          2,\n          3\n        ],\n        {\n" +
		"          \"d\": null\n        }\n      ]\n    }\n  }\n}"
	if got != want {
		t.Errorf("nesting:\n got %q\nwant %q", got, want)
	}
	// No trailing newline: dumps does not add one and both callers write
	// the result verbatim.
	if strings.HasSuffix(got, "\n") {
		t.Error("dumps must not add a trailing newline")
	}
}

// A bare json.dumps uses Python's DEFAULT separators: ", " between items
// and ": " after each key. The compact (",", ":") form is something a
// caller has to ask for, and none of the writers ported here do.
func TestDumpsCompactCarriesPythonsSeparators(t *testing.T) {
	got, err := DumpsCompactPy(pyObj{{Key: "a", Val: 1}, {Key: "b", Val: pyList{1, 2}}})
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"a": 1, "b": [1, 2]}`; got != want {
		t.Errorf("\n got %s\nwant %s", got, want)
	}
}

// A Go map reaching the renderer would be spelled by pyjson's sorted,
// unspaced shape, silently mixing two spellings inside one file. Refused
// rather than rendered, because the wrong bytes are worse than an error.
func TestRendererRefusesUnorderedContainers(t *testing.T) {
	if _, err := DumpsIndent2(pyObj{{Key: "a", Val: map[string]any{"x": 1}}}); err == nil {
		t.Error("a Go map must be refused, not rendered in pyjson's shape")
	}
	if _, err := DumpsIndent2(pyObj{{Key: "a", Val: []any{1}}}); err == nil {
		t.Error("a []any must be refused: build a pyList")
	}
}

// --- the ordered decoder ---------------------------------------------------

func TestLoadsOrderedKeepsOrderAndLiterals(t *testing.T) {
	v, err := LoadsOrdered(`{"zulu":1,"alpha":2,"cost":1.0,"n":42,"s":0.250}`)
	if err != nil {
		t.Fatal(err)
	}
	obj := v.(pyObj)
	var keys []string
	for _, f := range obj {
		keys = append(keys, f.Key)
	}
	if got := strings.Join(keys, ","); got != "zulu,alpha,cost,n,s" {
		t.Errorf("key order lost: %s", got)
	}
	// Re-rendering must give the literals back untouched: a stored 1.0
	// that comes back as 1 changes the TYPE json.loads parses on the
	// Python side, and 0.250 losing its zero is a byte diff on a file
	// nobody edited.
	out, err := DumpsCompactPy(obj)
	if err != nil {
		t.Fatal(err)
	}
	if want := `{"zulu": 1, "alpha": 2, "cost": 1.0, "n": 42, "s": 0.250}`; out != want {
		t.Errorf("\n got %s\nwant %s", out, want)
	}
}

func TestLoadsOrderedEdgeCases(t *testing.T) {
	// Python's json.loads keeps the LAST value for a duplicate key and the
	// dict has one slot for it, so the first position is what survives.
	v, err := LoadsOrdered(`{"a":1,"b":2,"a":3}`)
	if err != nil {
		t.Fatal(err)
	}
	out, _ := DumpsCompactPy(v)
	if want := `{"a": 3, "b": 2}`; out != want {
		t.Errorf("duplicate key: got %s want %s", out, want)
	}
	// Trailing content is an error in json.loads; accepting it would let a
	// torn write parse as a healthy one.
	if _, err := LoadsOrdered(`{}{}`); err == nil {
		t.Error("trailing content must be refused")
	}
	if _, err := LoadsOrdered(`{`); err == nil {
		t.Error("a truncated object must be refused")
	}
	// A top-level non-object is legal JSON and must decode.
	if v, err := LoadsOrdered(`[1,2]`); err != nil {
		t.Errorf("top-level array: %v", err)
	} else if _, ok := v.(pyList); !ok {
		t.Errorf("top-level array decoded as %T", v)
	}
}

// --- the mission store -----------------------------------------------------

func sampleMission() *Mission {
	sess, res, valres := "w9", "ok", "n/a"
	return &Mission{
		ID: "mi1", Goal: "Build a thing — properly", Project: "proj-a",
		Status: "running", CreatedAt: "2026-08-23T00:00:00+00:00",
		Milestones: []Milestone{
			{ID: "m1", Title: "First", Status: "running",
				ValidationCriteria: []string{"it works", "it is fast"},
				DependsOn:          []string{},
				Features: []Feature{
					{ID: "f1", Title: "Feature one", Status: "pending"},
					{ID: "f2", Title: "Café ☕", Status: "done",
						WorkerSessionID: &sess, ResultSummary: &res, ElapsedMS: 1234},
				}},
			{ID: "m2", Title: "Second", Status: "pending",
				ValidationCriteria: []string{}, ValidationResult: &valres,
				DependsOn: []string{"m1"}},
		},
	}
}

// The exact bytes Python's save_mission produced for this mission. An
// absent optional is a JSON null, NOT an omitted key — a reader that
// treated a missing key as "no value" would agree, but a byte diff of the
// two stores would not.
func TestSaveMissionBytes(t *testing.T) {
	ws := t.TempDir()
	if err := SaveMission(ws, sampleMission(), "proj-a"); err != nil {
		t.Fatal(err)
	}
	got := readFile(t, MissionPath(ws, "proj-a"))
	want := "{\n  \"id\": \"mi1\",\n  \"goal\": \"Build a thing \\u2014 properly\"," +
		"\n  \"project\": \"proj-a\",\n  \"milestones\": [\n    {\n      \"id\": \"m1\"," +
		"\n      \"title\": \"First\",\n      \"features\": [\n        {\n" +
		"          \"id\": \"f1\",\n          \"title\": \"Feature one\"," +
		"\n          \"status\": \"pending\",\n          \"worker_session_id\": null," +
		"\n          \"result_summary\": null,\n          \"elapsed_ms\": 0\n        }," +
		"\n        {\n          \"id\": \"f2\",\n          \"title\": \"Caf\\u00e9 \\u2615\"," +
		"\n          \"status\": \"done\",\n          \"worker_session_id\": \"w9\"," +
		"\n          \"result_summary\": \"ok\",\n          \"elapsed_ms\": 1234\n        }" +
		"\n      ],\n      \"validation_criteria\": [\n        \"it works\"," +
		"\n        \"it is fast\"\n      ],\n      \"status\": \"running\"," +
		"\n      \"validation_result\": null,\n      \"depends_on\": []\n    },\n    {" +
		"\n      \"id\": \"m2\",\n      \"title\": \"Second\",\n      \"features\": []," +
		"\n      \"validation_criteria\": [],\n      \"status\": \"pending\"," +
		"\n      \"validation_result\": \"n/a\",\n      \"depends_on\": [\n        \"m1\"" +
		"\n      ]\n    }\n  ],\n  \"status\": \"running\"," +
		"\n  \"created_at\": \"2026-08-23T00:00:00+00:00\",\n  \"completed_at\": null," +
		"\n  \"ancestry_context\": \"\"\n}"
	if got != want {
		t.Errorf("mission.json bytes differ from Python's:\n got %q\nwant %q", got, want)
	}
}

func TestSaveLoadMissionRoundTrips(t *testing.T) {
	ws := t.TempDir()
	m := sampleMission()
	if err := SaveMission(ws, m, "proj-a"); err != nil {
		t.Fatal(err)
	}
	before := readFile(t, MissionPath(ws, "proj-a"))
	loaded := LoadMission(ws, "proj-a")
	if loaded == nil {
		t.Fatal("load refused what save wrote")
	}
	if err := SaveMission(ws, loaded, "proj-a"); err != nil {
		t.Fatal(err)
	}
	if after := readFile(t, MissionPath(ws, "proj-a")); after != before {
		t.Errorf("a read-write round trip changed the file:\n%s\n%s", before, after)
	}
	if loaded.Milestones[1].ValidationResult == nil ||
		*loaded.Milestones[1].ValidationResult != "n/a" {
		t.Error("an optional string was lost")
	}
	if loaded.Milestones[0].Features[0].WorkerSessionID != nil {
		t.Error("a JSON null must load as absent, not as \"null\"")
	}
}

// Python's load does NO coercion on these two fields, so a value it round
// trips must survive a Go rewrite untouched. A typed int field alone would
// have written 1.5 back as 1 — a silent rewrite of a peer's row, which is
// the exact class of bug the skills pool rewrite was fixed for.
func TestLoadMissionCarriesLiteralsPythonRoundTrips(t *testing.T) {
	ws := t.TempDir()
	raw := `{
  "id": "m",
  "goal": "g",
  "project": "p",
  "milestones": [
    {
      "id": "m1",
      "title": "t",
      "features": [
        {
          "id": "f",
          "title": "t",
          "status": "pending",
          "elapsed_ms": 1.5
        }
      ],
      "validation_criteria": "not-a-list",
      "status": "pending",
      "depends_on": []
    }
  ],
  "status": "running",
  "created_at": "t",
  "completed_at": null,
  "ancestry_context": ""
}`
	writeFile(t, MissionPath(ws, "p"), raw)
	m := LoadMission(ws, "p")
	if m == nil {
		t.Fatal("Python loads this; so must we")
	}
	if got := m.Milestones[0].Features[0].ElapsedMS; got != 1 {
		t.Errorf("the typed view is int(1.5) == 1, got %d", got)
	}
	if got := m.Milestones[0].ValidationCriteria; len(got) != 0 {
		t.Errorf("the typed view of a non-list is empty, got %v", got)
	}
	if err := SaveMission(ws, m, "p"); err != nil {
		t.Fatal(err)
	}
	out := readFile(t, MissionPath(ws, "p"))
	if !strings.Contains(out, `"elapsed_ms": 1.5`) {
		t.Errorf("a float Python round-trips was re-typed:\n%s", out)
	}
	if !strings.Contains(out, `"validation_criteria": "not-a-list"`) {
		t.Errorf("a non-list Python round-trips was replaced:\n%s", out)
	}
	// Once the program CHANGES the field, the typed value wins — the carry
	// is "untouched", not "frozen".
	m.Milestones[0].Features[0].ElapsedMS = 99
	m.Milestones[0].ValidationCriteria = []string{"now a list"}
	if err := SaveMission(ws, m, "p"); err != nil {
		t.Fatal(err)
	}
	out = readFile(t, MissionPath(ws, "p"))
	if !strings.Contains(out, `"elapsed_ms": 99`) ||
		!strings.Contains(out, `"now a list"`) {
		t.Errorf("a changed field must be written:\n%s", out)
	}
}

// A mission.json predating the DAG has no depends_on, and Python
// reconstructs the sequential chain so an undecorated decomposition
// executes exactly as the old sequential walk did. Absent and non-list
// both take that branch; a list is normalized instead.
func TestLoadMissionReconstructsTheLegacyChain(t *testing.T) {
	ws := t.TempDir()
	writeFile(t, MissionPath(ws, "p"), `{"id":"m","goal":"g","project":"p",
	  "status":"running","created_at":"t","milestones":[
	    {"id":"a","title":"A","status":"pending","features":[]},
	    {"id":"b","title":"B","status":"pending","features":[],"depends_on":"nope"},
	    {"id":"c","title":"C","status":"pending","features":[],
	     "depends_on":["a","",7,"b"]}]}`)
	m := LoadMission(ws, "p")
	if m == nil {
		t.Fatal("load refused")
	}
	got := []string{
		strings.Join(m.Milestones[0].DependsOn, "+"),
		strings.Join(m.Milestones[1].DependsOn, "+"),
		strings.Join(m.Milestones[2].DependsOn, "+"),
	}
	want := []string{"", "a", "a+b"}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("milestone %d depends_on = %q, want %q", i, got[i], want[i])
		}
	}
}

// Python indexes the required keys with [], so a missing one raises
// KeyError into a bare `except Exception: return None`. A caller must not
// be able to tell "no mission" from "unreadable mission" — if it could and
// treated the second as the first, it would overwrite the broken file.
func TestLoadMissionRefusalsMatchPython(t *testing.T) {
	ws := t.TempDir()
	// Each required key ALONE, so a gate that quietly stops covering one of
	// them fails here. Testing only a wholesale-broken file would pass with
	// four of the five gates removed.
	full := map[string]string{"id": `"m"`, "goal": `"g"`, "project": `"p"`,
		"status": `"s"`, "created_at": `"t"`}
	for missing := range full {
		var parts []string
		for k, v := range full {
			if k != missing {
				parts = append(parts, `"`+k+`":`+v)
			}
		}
		writeFile(t, MissionPath(ws, "p"), "{"+strings.Join(parts, ",")+`,"milestones":[]}`)
		if m := LoadMission(ws, "p"); m != nil {
			t.Errorf("a mission with no %q loaded; Python raises KeyError "+
				"into its bare except and returns None", missing)
		}
		// ...and the same file WITH that key must load, so the case above is
		// failing for the reason it claims.
		parts = append(parts, `"`+missing+`":`+full[missing])
		writeFile(t, MissionPath(ws, "p"), "{"+strings.Join(parts, ",")+`,"milestones":[]}`)
		if m := LoadMission(ws, "p"); m == nil {
			t.Errorf("restoring %q did not make the mission loadable", missing)
		}
	}
	for _, c := range []struct{ name, body string }{
		{"missing required key", `{"id":"x"}`},
		{"torn write", `{"id":"x","goal":`},
		{"not an object", `[1,2]`},
		{"milestone missing id", `{"id":"m","goal":"g","project":"p","status":"s",
		   "created_at":"t","milestones":[{"title":"T","status":"p"}]}`},
		{"milestones is a string", `{"id":"m","goal":"g","project":"p","status":"s",
		   "created_at":"t","milestones":"abc"}`},
	} {
		writeFile(t, MissionPath(ws, "p"), c.body)
		if m := LoadMission(ws, "p"); m != nil {
			t.Errorf("%s: loaded %+v, Python returns None", c.name, m)
		}
	}
	// An absent file is the same answer, deliberately.
	if m := LoadMission(t.TempDir(), "nothing"); m != nil {
		t.Error("a missing mission must be nil")
	}
}

// --- the feature manifest --------------------------------------------------

// The manifest is the promise the run is graded against, so a
// re-decomposition partway through must not be able to change what "done"
// meant. An existing file is READ, never rebuilt.
func TestFeatureManifestIsNeverOverwritten(t *testing.T) {
	ws := t.TempDir()
	m := sampleMission()
	if _, err := GenerateFeatureManifest(ws, m, "proj-a"); err != nil {
		t.Fatal(err)
	}
	first := readFile(t, FeatureManifestPath(ws, "proj-a"))
	if !strings.Contains(first, `"passes": false`) ||
		!strings.Contains(first, `"milestone_id": "m1"`) {
		t.Errorf("manifest shape:\n%s", first)
	}

	// A second mission with entirely different features must not replace it.
	other := &Mission{ID: "x", Goal: "g", Project: "proj-a", Status: "running",
		CreatedAt: "t", Milestones: []Milestone{{ID: "zz", Title: "Z",
			Status: "pending", Features: []Feature{{ID: "zf", Title: "Z", Status: "pending"}}}}}
	got, err := GenerateFeatureManifest(ws, other, "proj-a")
	if err != nil {
		t.Fatal(err)
	}
	if readFile(t, FeatureManifestPath(ws, "proj-a")) != first {
		t.Error("a re-decomposition rewrote the promise it is graded against")
	}
	if _, ok := got.Get("features"); !ok {
		t.Error("the EXISTING manifest is what comes back, not the new one")
	}
	if strings.Contains(mustDump(t, got), "zf") {
		t.Error("the returned manifest is the stored one, not the rebuilt one")
	}
}

func mustDump(t *testing.T, v any) string {
	t.Helper()
	s, err := DumpsIndent2(v)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

// The patch preserves the file it did not come for: foreign keys, their
// order, and every literal. Bytes measured against Python's
// mark_feature_passing over the same fixture.
func TestMarkFeaturePassingPreservesTheFile(t *testing.T) {
	ws := t.TempDir()
	path := FeatureManifestPath(ws, "p")
	writeFile(t, path, `{
  "schema": 2,
  "features": [
    {
      "zulu": "keep",
      "id": "f1",
      "passes": false,
      "grade_score": null,
      "contract_id": "old",
      "note": "operator"
    },
    {
      "id": "f2",
      "passes": true,
      "grade_score": 0.5,
      "contract_id": null
    }
  ]
}`)
	// An EMPTY contract id is falsy in Python, so the stored one survives.
	if err := MarkFeaturePassing(ws, "p", "f1", &ContractGrade{
		Passed: true, Score: 0.75, ContractID: ""}); err != nil {
		t.Fatal(err)
	}
	want := `{
  "schema": 2,
  "features": [
    {
      "zulu": "keep",
      "id": "f1",
      "passes": true,
      "grade_score": 0.75,
      "contract_id": "old",
      "note": "operator"
    },
    {
      "id": "f2",
      "passes": true,
      "grade_score": 0.5,
      "contract_id": null
    }
  ]
}`
	if got := readFile(t, path); got != want {
		t.Errorf("patched manifest differs from Python's:\n got %s\nwant %s", got, want)
	}
}

// Monotonicity is the ONE failure that propagates. Everything else was an
// `except Exception: pass` before the lock went in, and turning those into
// errors now would change which callers abort.
func TestMarkFeaturePassingMonotonicity(t *testing.T) {
	ws := t.TempDir()
	path := FeatureManifestPath(ws, "p")
	body := `{"features": [{"id": "f2", "passes": true, "grade_score": 0.5, "contract_id": null}]}`
	writeFile(t, path, body)

	err := MarkFeaturePassing(ws, "p", "f2", &ContractGrade{Passed: false, Score: 0.1, ContractID: "c"})
	if !errors.Is(err, ErrManifestMonotonicity) {
		t.Fatalf("a downgrade must be refused, got %v", err)
	}
	if !strings.Contains(err.Error(), `'f2'`) {
		t.Errorf("the message carries Python's repr quoting: %v", err)
	}
	if got := readFile(t, path); got != body {
		t.Errorf("a refused downgrade must leave the file alone:\n%s", got)
	}

	// Re-passing an already-passing feature is fine — it is not a downgrade.
	if err := MarkFeaturePassing(ws, "p", "f2", &ContractGrade{Passed: true, Score: 0.9}); err != nil {
		t.Errorf("re-passing must be allowed: %v", err)
	}
	// Silent no-ops, all three.
	if err := MarkFeaturePassing(ws, "p", "unknown-id", &ContractGrade{Passed: true}); err != nil {
		t.Errorf("an unknown feature is a no-op, not an error: %v", err)
	}
	if err := MarkFeaturePassing(ws, "no-such-project", "f", &ContractGrade{}); err != nil {
		t.Errorf("a missing manifest is a no-op: %v", err)
	}
	writeFile(t, FeatureManifestPath(ws, "torn"), `{"features": [`)
	if err := MarkFeaturePassing(ws, "torn", "f", &ContractGrade{Passed: true}); err != nil {
		t.Errorf("an unparseable manifest is a no-op: %v", err)
	}
	if got := readFile(t, FeatureManifestPath(ws, "torn")); got != `{"features": [` {
		t.Errorf("an unparseable manifest must be left verbatim: %q", got)
	}
	// A nil grade is Python's "neither an object nor a dict" branch.
	writeFile(t, FeatureManifestPath(ws, "q"),
		`{"features": [{"id": "f", "passes": false, "grade_score": null, "contract_id": null}]}`)
	if err := MarkFeaturePassing(ws, "q", "f", nil); err != nil {
		t.Fatal(err)
	}
	if got := readFile(t, FeatureManifestPath(ws, "q")); !strings.Contains(got, `"passes": false`) ||
		!strings.Contains(got, `"grade_score": 0.0`) {
		t.Errorf("a nil grade is passes=false score=0.0:\n%s", got)
	}
}

// --- read-side summaries ---------------------------------------------------

// ListMissions answers "what is on disk" and LoadMission answers "what can
// be executed". They disagree on a broken mission.json, and that is
// Python's behaviour rather than an oversight — so a broken mission is
// visible to an operator and never triggers an autonomous drain.
func TestListAndPendingDisagreeOnABrokenMission(t *testing.T) {
	ws := t.TempDir()
	if err := SaveMission(ws, sampleMission(), "healthy"); err != nil {
		t.Fatal(err)
	}
	// Summarizable (every .get has a default) but not loadable (no id).
	writeFile(t, MissionPath(ws, "broken"),
		`{"goal":"g","milestones":[{"status":"done","features":[{"status":"done"}]}]}`)
	writeFile(t, filepath.Join(ProjectDir(ws, "no-mission"), "NEXT.md"), "- [ ] x\n")

	list := ListMissions(ws)
	if len(list) != 2 {
		t.Fatalf("expected both projects with a mission.json: %+v", list)
	}
	// Directory order, which is what sorted(iterdir()) gives.
	if list[0].Project != "broken" || list[1].Project != "healthy" {
		t.Errorf("order: %s, %s", list[0].Project, list[1].Project)
	}
	if list[0].MissionID != "?" || list[0].Status != "?" || list[0].CreatedAt != "" {
		t.Errorf("missing fields take Python's .get defaults: %+v", list[0])
	}
	if list[0].MilestonesDone != 1 || list[0].FeaturesDone != 1 || list[0].FeaturesTotal != 1 {
		t.Errorf("counts come from the RAW json, not a parsed Mission: %+v", list[0])
	}

	pending := PendingMissions(ws)
	if len(pending) != 1 || pending[0].Project != "healthy" {
		t.Fatalf("only the loadable mission can be drained: %+v", pending)
	}
	if pending[0].MilestonesPending != 2 {
		t.Errorf("pending milestones: %d", pending[0].MilestonesPending)
	}
	// A finished mission is not pending.
	done := sampleMission()
	done.Status = "done"
	if err := SaveMission(ws, done, "finished"); err != nil {
		t.Fatal(err)
	}
	for _, p := range PendingMissions(ws) {
		if p.Project == "finished" {
			t.Error("a done mission must not be pending")
		}
	}
}

func TestListMissionsOnAColdWorkspace(t *testing.T) {
	if got := ListMissions(t.TempDir()); len(got) != 0 {
		t.Errorf("a workspace with no projects/ dir lists nothing: %+v", got)
	}
}

// The briefing's shape is ported verbatim, double blank line included:
// Python appends an empty element AND prefixes each section header with
// "\n". It reads like a bug and it is what the operator's Telegram history
// has looked like for months, so changing it here would make the two
// runtimes' notifications differ.
func TestMorningBriefingShape(t *testing.T) {
	now := time.Date(2026, 8, 23, 10, 52, 0, 0, time.UTC)
	ws := t.TempDir()
	if got := morningBriefingAt(ws, 5, now); got !=
		"Morning briefing — 2026-08-23 10:52 UTC\n\nNo active missions." {
		t.Errorf("empty briefing:\n%q", got)
	}

	mk := func(project, status, goal string) {
		m := sampleMission()
		m.Status, m.Goal, m.Project = status, goal, project
		if err := SaveMission(ws, m, project); err != nil {
			t.Fatal(err)
		}
	}
	mk("a-run", "running", strings.Repeat("é", 80))
	mk("b-done", "done", "finished it")
	mk("c-queued", "pending", "not started")

	got := morningBriefingAt(ws, 5, now)
	want := "Morning briefing — 2026-08-23 10:52 UTC\n" +
		"\n" +
		"Completed (1):\n" +
		"  ✓ [b-done] finished it (0/2 milestones)\n" +
		"\nIn progress (1):\n" +
		"  → [a-run] " + strings.Repeat("é", 60) + " (0/2 milestones)\n" +
		"\nQueued (1):\n" +
		"  ○ [c-queued] not started"
	if got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
	// The goal is clipped to 60 CODE POINTS. On bytes it would be 30
	// characters and could split a rune.
	if !strings.Contains(got, strings.Repeat("é", 60)+" (0/2") {
		t.Error("the clip must count code points, not bytes")
	}
	if strings.Contains(got, "\uFFFD") {
		t.Error("a byte clip split a rune")
	}
}

// max_missions caps the RENDERED ROWS; the count in each header stays the
// full total, so an operator glancing at the phone sees that three are
// queued even when only two are listed. Checked on all three sections —
// they are three separate code paths and a cap that leaked into one
// header's count would otherwise hide in the two the test skipped.
func TestMorningBriefingCapsRowsNotCounts(t *testing.T) {
	ws := t.TempDir()
	for status, projects := range map[string][]string{
		"pending": {"q1", "q2", "q3"},
		"done":    {"d1", "d2", "d3"},
		"running": {"r1", "r2", "r3"},
	} {
		for _, p := range projects {
			m := sampleMission()
			m.Status, m.Project = status, p
			if err := SaveMission(ws, m, p); err != nil {
				t.Fatal(err)
			}
		}
	}
	got := morningBriefingAt(ws, 2, time.Date(2026, 8, 23, 0, 0, 0, 0, time.UTC))
	for _, section := range []struct{ header, bullet string }{
		{"Completed (3):", "  ✓ "},
		{"In progress (3):", "  → "},
		{"Queued (3):", "  ○ "},
	} {
		if !strings.Contains(got, section.header) {
			t.Errorf("the header COUNT is the full total, not the capped "+
				"list — want %q:\n%s", section.header, got)
		}
		if n := strings.Count(got, section.bullet); n != 2 {
			t.Errorf("%q rendered %d rows, max_missions is 2:\n%s",
				section.header, n, got)
		}
	}
}

// --- the log and the drain lock --------------------------------------------

func TestMissionLogRowShape(t *testing.T) {
	ws := t.TempDir()
	m := sampleMission()
	done := "2026-08-23T01:00:00+00:00"
	m.CompletedAt = &done
	r := MissionResult{MissionID: "mi1", Project: "proj-a", Goal: "g — é",
		Status: "done", MilestonesDone: 1, MilestonesTotal: 2,
		FeaturesDone: 1, FeaturesTotal: 2, ElapsedMS: 5000}
	// A COLD workspace: Python's locked_append mkdirs the parent and Go's
	// AppendRawLine does not, so this failed outright before the caller
	// took responsibility for the directory.
	if err := WriteMissionLog(ws, r, m); err != nil {
		t.Fatal(err)
	}
	got := strings.TrimSpace(readFile(t, MissionLogPath(ws)))
	want := `{"mission_id": "mi1", "project": "proj-a", "goal": "g \u2014 \u00e9", ` +
		`"status": "done", "milestones_done": 1, "milestones_total": 2, ` +
		`"features_done": 1, "features_total": 2, "elapsed_ms": 5000, ` +
		`"created_at": "2026-08-23T00:00:00+00:00", ` +
		`"completed_at": "2026-08-23T01:00:00+00:00"}`
	if got != want {
		t.Errorf("\n got %s\nwant %s", got, want)
	}
	// Append-only: a second result does not replace the first.
	if err := WriteMissionLog(ws, r, m); err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(readFile(t, MissionLogPath(ws)), "\n"); n != 2 {
		t.Errorf("expected 2 framed rows, got %d", n)
	}
}

func TestDrainLock(t *testing.T) {
	ws := t.TempDir()
	if IsDrainRunning(ws) {
		t.Error("a cold workspace holds no lock")
	}
	if !AcquireDrainLock(ws, "mi1") {
		t.Fatal("the first acquire must succeed")
	}
	if !IsDrainRunning(ws) {
		t.Error("the lock must be visible to another process")
	}
	if AcquireDrainLock(ws, "mi2") {
		t.Error("a second drain must be refused")
	}
	var row map[string]any
	if err := json.Unmarshal([]byte(readFile(t, DrainLockPath(ws))), &row); err != nil {
		t.Fatal(err)
	}
	if row["mission_id"] != "mi1" {
		t.Errorf("the lock names its holder: %+v", row)
	}
	// started_at is what an operator reads to judge a stale lock, so it has
	// to be there and has to be Python's isoformat spelling.
	at, _ := row["started_at"].(string)
	if _, err := time.Parse("2006-01-02T15:04:05.000000-07:00", at); err != nil {
		if _, err2 := time.Parse("2006-01-02T15:04:05-07:00", at); err2 != nil {
			t.Errorf("started_at %q is not datetime.isoformat(): %v", at, err)
		}
	}
	ReleaseDrainLock(ws)
	if IsDrainRunning(ws) {
		t.Error("release must clear the lock")
	}
	ReleaseDrainLock(ws) // missing_ok=True
	if !AcquireDrainLock(ws, "mi3") {
		t.Error("the lock must be re-acquirable after release")
	}
}

// Python omits the fractional part entirely when the microsecond is 0,
// where a fixed ".000000" layout always prints six digits. One in a
// million, and the kind that shows up once, in production, as an
// unparseable timestamp in someone else's reader.
func TestNowISOPyDropsAZeroFraction(t *testing.T) {
	base := time.Date(2026, 8, 23, 10, 52, 0, 0, time.UTC)
	if got := nowISOPy(base); got != "2026-08-23T10:52:00+00:00" {
		t.Errorf("zero microseconds: %s", got)
	}
	if got := nowISOPy(base.Add(123456 * time.Microsecond)); got !=
		"2026-08-23T10:52:00.123456+00:00" {
		t.Errorf("with microseconds: %s", got)
	}
	// Nanosecond precision below a microsecond truncates rather than
	// rounding up into a fraction Python could not have written.
	if got := nowISOPy(base.Add(999 * time.Nanosecond)); got !=
		"2026-08-23T10:52:00+00:00" {
		t.Errorf("sub-microsecond: %s", got)
	}
}

func TestMissionResultSummary(t *testing.T) {
	r := MissionResult{MissionID: "mi1", Project: "p", Goal: "it's a goal",
		Status: "done", MilestonesDone: 1, MilestonesTotal: 2,
		FeaturesDone: 3, FeaturesTotal: 4, ElapsedMS: 5}
	want := "mission_id=mi1\nproject=p\ngoal=\"it's a goal\"\nstatus=done\n" +
		"milestones=1/2\nfeatures=3/4\nelapsed_ms=5"
	if got := r.Summary(); got != want {
		t.Errorf("\n got %q\nwant %q", got, want)
	}
}
