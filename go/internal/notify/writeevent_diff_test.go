package notify

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// TestEventRowMatchesCPython next door drives write_event through Emit,
// which fills exactly three of its fourteen fields. The other eleven —
// step, step_idx, the three token counts, model, elapsed_ms, project,
// loop_id and tool_pathologies — arrived when the escalation lane widened
// EventFields, and until this file nothing compared them to anything.
//
// The gap is the shape r6 named: a test that reports AGREEMENT may be
// testing nothing. Emit's three fields agreed; the eleven it never set
// agreed the way two empty strings agree.

const pyWriteEventSrc = `
import json, os, sys
ws = sys.argv[1]
os.environ["MARO_WORKSPACE"] = ws
import observe
assert str(observe._events_path()).startswith(ws), observe._events_path()
kwargs = json.loads(sys.argv[2])
observe.write_event(kwargs.pop("event_type"), **kwargs)
p = observe._events_path()
# write_event NEVER raises — it returns False and writes nothing when a
# keyword it slices is not sliceable. An absent file IS that answer, and a
# differential has to be able to see it rather than die on it.
sys.stdout.write(open(p, encoding="utf-8").read() if os.path.exists(p) else "")
`

// The fields are compared against CPython for the shapes that decide a
// branch: a slice at, under and over its bound; a cut that must NOT
// announce itself next to one that must; a pathology list that is empty,
// short, exactly at the cap and over it; and a pathology dict missing the
// keys the comprehension reads.
func TestWriteEventFieldsMatchCPython(t *testing.T) {
	long := strings.Repeat("g", 500)
	// Runes, not bytes: Python slices by code point, and a port reaching
	// for a byte slice cuts a multi-byte character in half — which json
	// then escapes as a replacement, so the divergence is visible in the
	// row rather than only in the length.
	wide := strings.Repeat("é😀ル", 60)

	cases := []struct {
		name   string
		event  string
		fields EventFields
		// rawGoal opts a row out of the table default below. Goal is `any`
		// so write_event can carry a raw task field, which means its Go
		// zero is nil and spells `goal=None` — and `goal[:80]` RAISES for
		// None, so CPython writes NO ROW. Every row here that is about
		// something else therefore gets Goal: "" filled in, or it would
		// compare two absences and test none of its own subject.
		rawGoal bool
	}{
		{"every field carries", "step_done", EventFields{
			Goal: "ship the port", Project: "goport", LoopID: "loop-7",
			Step: "run the battery", StepIdx: 3, Status: "ok",
			TokensIn: 1200, TokensOut: 340, CacheReadTokens: 9000,
			Model: "claude-opus-5", ElapsedMs: 15432, Detail: "76/0/0",
		}, false},
		{"the zero value", "loop_start", EventFields{}, false},
		// The three shapes of a RAW goal, which is what the field became
		// when the drain lane needed to hand write_event a task's own
		// `reason`. A str slices; a LIST slices to a list and rides into
		// the row as a JSON array; everything else raises inside
		// write_event's own try, which means no row at all — from either
		// runtime (adversarial r11 round 3).
		{"a null goal writes no row", "step_done",
			EventFields{Goal: nil, Project: "", LoopID: "", Model: ""}, true},
		{"a numeric goal writes no row", "step_done",
			EventFields{Goal: 4242, Project: "", LoopID: "", Model: ""}, true},
		{"a list goal rides in as a list", "step_done",
			EventFields{Goal: []any{"a", 2, nil}, Project: "", LoopID: "", Model: ""}, true},
		{"a list goal past 80 elements is cut by ELEMENT", "step_done",
			EventFields{Goal: longList(100), Project: "", LoopID: "", Model: ""}, true},
		{"goal at its bound", "step_done", EventFields{Goal: strings.Repeat("g", 80)}, false},
		{"goal one past it", "step_done", EventFields{Goal: strings.Repeat("g", 81)}, false},
		{"goal far past it, silently", "step_done", EventFields{Goal: long}, false},
		{"step at its bound", "step_done", EventFields{Step: strings.Repeat("s", 120)}, false},
		{"step one past it", "step_done", EventFields{Step: strings.Repeat("s", 121)}, false},
		// detail is the ONE announcing cut in the row. If a port clips all
		// three the same way, this case is the only one that says so.
		{"detail at its bound", "step_done", EventFields{Detail: strings.Repeat("d", 200)}, false},
		{"detail one past it", "step_done", EventFields{Detail: strings.Repeat("d", 201)}, false},
		{"detail far past it, loudly", "step_done", EventFields{Detail: long}, false},
		{"multi-byte at every bound", "step_done", EventFields{
			Goal: wide, Step: wide, Detail: wide}, false},
		{"negative and huge counters", "step_done", EventFields{
			StepIdx: -1, TokensIn: -5, TokensOut: 1 << 40, ElapsedMs: -12}, false},
		// ensure_ascii=True: every non-ASCII rune leaves as \uXXXX, and an
		// astral one as a surrogate PAIR. Go's encoder does neither by
		// default.
		{"non-ASCII in every string", "step_done", EventFields{
			Goal: "the café path → 😀", Project: "проект", LoopID: "ループ",
			Step: "prefer a > b </script>", Status: "ok done",
			Model: "modèle", Detail: "a\tb\\c\"d\ne",
		}, false},
		// project and loop_id ride RAW: write_event slices goal, step and
		// detail and touches nothing else, so these two keep their JSON
		// type. A port that spelled them with str() writes "4242" against
		// CPython's 4242 — two rows that look alike in a terminal and
		// compare equal to nothing in a reader.
		{"a numeric project and loop id stay numeric", "step_done", EventFields{
			Project: 4242, LoopID: 7, Goal: "g"}, false},
		{"a null project and loop id stay null", "step_done", EventFields{
			Project: nil, LoopID: nil, Goal: "g"}, false},
		// Fractional only. A whole float cannot ride this fixture: the
		// transport is JSON, Go marshals 1.0 as `1`, and Python reads that
		// back as an int — so the case would compare Go's float against
		// CPython's int and report the harness, not the port. The `1.0`
		// spelling is asserted directly below instead.
		{"a fractional project keeps its fraction", "step_done", EventFields{
			Project: 2.5, LoopID: -0.5, Goal: "g"}, false},
		{"a boolean project stays a boolean", "step_done", EventFields{
			Project: true, LoopID: false, Goal: "g"}, false},
		{"a structured project rides through whole", "step_done", EventFields{
			Project: map[string]any{"b": 2, "a": 1}, LoopID: []any{"x", 3}, Goal: "g"}, false},

		{"one pathology", "step_stuck", EventFields{
			Status: "stuck",
			ToolPathologies: []ToolPathology{
				{Cls: "repeat_read", Evidence: "read the same file 4 times"}},
		}, false},
		{"exactly the cap", "step_stuck", EventFields{
			ToolPathologies: []ToolPathology{
				{Cls: "a", Evidence: "1"}, {Cls: "b", Evidence: "2"}, {Cls: "c", Evidence: "3"}},
		}, false},
		// The 4th must be DROPPED, and the drop must be of the tail, not
		// of an arbitrary three.
		{"one over the cap", "step_stuck", EventFields{
			ToolPathologies: []ToolPathology{
				{Cls: "a", Evidence: "1"}, {Cls: "b", Evidence: "2"},
				{Cls: "c", Evidence: "3"}, {Cls: "d", Evidence: "4"}},
		}, false},
		{"pathology fields at and past their bounds", "step_stuck", EventFields{
			ToolPathologies: []ToolPathology{
				{Cls: strings.Repeat("c", 40), Evidence: strings.Repeat("e", 160)},
				{Cls: strings.Repeat("c", 41), Evidence: strings.Repeat("e", 161)},
				{Cls: wide, Evidence: wide}},
		}, false},
		{"empty pathology strings", "step_stuck", EventFields{
			ToolPathologies: []ToolPathology{{}}}, false},
	}

	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			f := c.fields
			if !c.rawGoal {
				f.Goal = ""
			}
			pyWS := t.TempDir()
			want := strings.TrimRight(
				runPy(t, pyWriteEventSrc, pyWS, string(pyKwargs(t, c.event, f))), "\n")

			goWS := t.TempDir()
			WriteEvent(goWS, c.event, f)
			raw, err := os.ReadFile(filepath.Join(goWS, "memory", "events.jsonl"))
			if err != nil {
				// No row on this side is a LEGAL outcome, not a harness
				// failure — but only if CPython agreed, which is the half
				// a bare "no events row" fatal never asked about.
				if want == "" {
					return
				}
				t.Fatalf("no events row, but CPython wrote one: %s", want)
			}
			got := strings.TrimRight(string(raw), "\n")
			if want == "" {
				t.Fatalf("wrote a row CPython refused to write: %s", got)
			}

			if maskEventTS(t, got) != maskEventTS(t, want) {
				t.Fatalf("event row diverges:\n go: %s\n py: %s", got, want)
			}
		})
	}
}

// pyKwargs renders EventFields as the keyword arguments write_event takes,
// omitting tool_pathologies when empty so the CPython side takes its `if
// tool_pathologies:` false branch exactly when the Go side does.
func pyKwargs(t *testing.T, event string, f EventFields) []byte {
	t.Helper()
	kw := map[string]any{
		"event_type": event, "goal": f.Goal, "project": f.Project,
		"loop_id": f.LoopID, "step": f.Step, "step_idx": f.StepIdx,
		"status": f.Status, "tokens_in": f.TokensIn, "tokens_out": f.TokensOut,
		"cache_read_tokens": f.CacheReadTokens, "model": f.Model,
		"elapsed_ms": f.ElapsedMs, "detail": f.Detail,
	}
	if len(f.ToolPathologies) > 0 {
		rows := []any{}
		for _, p := range f.ToolPathologies {
			rows = append(rows, map[string]any{"cls": p.Cls, "evidence": p.Evidence})
		}
		kw["tool_pathologies"] = rows
	}
	b, err := json.Marshal(kw)
	if err != nil {
		t.Fatal(err)
	}
	return b
}

// FIVE fields must be stated explicitly in Go — goal, project, loop_id,
// status and model — because each is `any` so it can carry a raw value, and a Go zero
// of nil spells `null` where Python's keyword default is "". That gap is
// the price of carrying a raw value at all, and this is the test that says
// so: a truly bare write_event(type) on the CPython side against the Go
// call that reproduces it. `goal` is the one that also decides whether a
// row exists, since None is not sliceable.
func TestABareCallMatchesCPythonsKeywordDefaults(t *testing.T) {
	want := strings.TrimRight(
		runPy(t, pyWriteEventSrc, t.TempDir(), `{"event_type": "loop_start"}`), "\n")

	ws := t.TempDir()
	WriteEvent(ws, "loop_start", EventFields{
		Goal: "", Project: "", LoopID: "", Status: "", Model: ""})
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if maskEventTS(t, strings.TrimRight(string(raw), "\n")) != maskEventTS(t, want) {
		t.Fatalf("a defaults-only row diverges:\n go: %s\n py: %s", raw, want)
	}

	// A whole float keeps its ".0", which json.dumps does and Go's own
	// encoder does not. The differential above cannot carry this case
	// (its JSON transport turns 1.0 into 1), so it is asserted here.
	fws := t.TempDir()
	WriteEvent(fws, "step_done", EventFields{
		Goal: "", Project: 1.0, LoopID: "", Status: "", Model: ""})
	fraw, err := os.ReadFile(filepath.Join(fws, "memory", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(fraw), `"project": 1.0`) {
		t.Errorf("a whole float lost its .0, which changes the type a reader parses:\n%s", fraw)
	}

	// The ts is UTC, which every comparison in this file masks away.
	// Python is datetime.now(timezone.utc).isoformat(); a row stamped in
	// local time is the same instant spelled differently, in a feed whose
	// readers sort and group on the string.
	if !strings.Contains(want, "+00:00") {
		t.Fatalf("the CPython probe did not stamp UTC; this check is meaningless: %s", want)
	}
	if !strings.Contains(string(raw), "+00:00") {
		t.Errorf("the event ts is not UTC:\n%s", raw)
	}

	// And the trap this documents: the Go ZERO VALUE is not that row. It
	// is not even A row — a nil Goal spells goal=None, `None[:80]` raises
	// inside write_event's own try, and CPython writes nothing at all.
	zero := t.TempDir()
	WriteEvent(zero, "loop_start", EventFields{})
	if _, err := os.Stat(filepath.Join(zero, "memory", "events.jsonl")); err == nil {
		t.Error("the zero value now writes a row; CPython's write_event " +
			"refuses goal=None, and the two explicit callers exist because " +
			"of it")
	}

	// The rest of the trap, with the one field that decides existence
	// stated: every OTHER unstated raw field spells null where Python's
	// keyword default is "".
	nulls := t.TempDir()
	WriteEvent(nulls, "loop_start", EventFields{Goal: ""})
	rawNulls, err := os.ReadFile(filepath.Join(nulls, "memory", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"project", "loop_id", "status", "model"} {
		if !strings.Contains(string(rawNulls), `"`+key+`": null`) {
			t.Errorf("%s no longer spells null when unstated; the doc "+
				"comment on EventFields and the two explicit callers need "+
				"revisiting:\n%s", key, rawNulls)
		}
	}
}

// The key ORDER is the row's contract — maro-observe renders the feed by
// position and the Python reader round-trips it — and it is the one
// property a field-wise comparison silently drops. The differential above
// compares whole lines so it already covers this; this pins the order by
// NAME so a reordering says which key moved instead of printing two
// 400-character lines.
func TestTheEventRowKeyOrderIsPinned(t *testing.T) {
	ws := t.TempDir()
	WriteEvent(ws, "step_stuck", EventFields{
		Goal: "g", ToolPathologies: []ToolPathology{{Cls: "c", Evidence: "e"}}})
	raw, err := os.ReadFile(filepath.Join(ws, "memory", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"event_type", "ts", "goal", "project", "loop_id", "step", "step_idx",
		"status", "tokens_in", "tokens_out", "cache_read_tokens", "model",
		"elapsed_ms", "detail", "tool_pathologies",
	}
	got := keysInOrder(t, string(raw))
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Errorf("event row keys:\n got %v\nwant %v", got, want)
	}
	// tool_pathologies is CONDITIONAL. A port that emitted it always would
	// pass every byte-for-byte case above that happens to carry one.
	ws2 := t.TempDir()
	WriteEvent(ws2, "step_done", EventFields{Goal: "g"})
	raw2, err := os.ReadFile(filepath.Join(ws2, "memory", "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(raw2), "tool_pathologies") {
		t.Errorf("an empty pathology list still emitted the key: %s", raw2)
	}
}

// keysInOrder reads the top-level keys off a compact JSON object in the
// order they appear, which encoding/json's map decode would destroy.
func keysInOrder(t *testing.T, line string) []string {
	t.Helper()
	dec := json.NewDecoder(strings.NewReader(strings.TrimSpace(line)))
	tok, err := dec.Token()
	if err != nil || tok != json.Delim('{') {
		t.Fatalf("not a JSON object: %q (%v)", line, err)
	}
	keys := []string{}
	for dec.More() {
		k, err := dec.Token()
		if err != nil {
			t.Fatal(err)
		}
		keys = append(keys, k.(string))
		var v any
		if err := dec.Decode(&v); err != nil {
			t.Fatal(err)
		}
	}
	return keys
}

// The one ledger in this package that Python writes UNLOCKED. Routing it
// through the locked appender is invisible in every row-level comparison
// above — the bytes in events.jsonl are identical either way — and shows
// up only as an extra file that harnesses skip by name.
func TestTheEventLedgerTakesNoLock(t *testing.T) {
	ws := t.TempDir()
	WriteEvent(ws, "step_done", EventFields{Goal: "g"})

	dir := filepath.Join(ws, "memory")
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	names := []string{}
	for _, e := range entries {
		names = append(names, e.Name())
	}
	if len(names) != 1 || names[0] != "events.jsonl" {
		t.Errorf("write_event left %v in memory/; CPython leaves only events.jsonl", names)
	}

	// And it appends onto a torn tail the way Python does — a bare
	// O_APPEND write, no repair LF. The locked appender frames the tear,
	// which is arguably better and is NOT what the Python reader of this
	// file will see.
	path := filepath.Join(dir, "events.jsonl")
	if err := os.WriteFile(path, []byte(`{"event_type": "torn"`), 0o644); err != nil {
		t.Fatal(err)
	}
	WriteEvent(ws, "step_done", EventFields{Goal: "g"})
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if strings.HasPrefix(string(raw), `{"event_type": "torn"`+"\n") {
		t.Error("write_event framed a torn tail; CPython fuses onto it")
	}
	if got := strings.Count(string(raw), "\n"); got != 1 {
		t.Errorf("the torn file has %d newlines, want 1 (one fused row)", got)
	}
}

// longList builds a goal long enough to exercise the LIST arm of the
// slice — the one a rune-oriented head gets wrong by cutting characters
// where Python cuts elements.
func longList(n int) []any {
	out := make([]any, n)
	for i := range out {
		out[i] = i
	}
	return out
}
