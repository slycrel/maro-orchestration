package recall

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// The probe calls precisely the ported prior-attempt reader.  Its workspace
// is a t.TempDir fixture, so pyprobe's live-store guard is active.
const recallPriorAttemptsProbe = `
import json, recall, sys

goal, project, excluded = sys.argv[1:4]
rows = recall.find_prior_attempts(
    goal, window_hours=24.0, project=project, exclude_handle_id=excluded)
print(json.dumps([{
    "goal": a.goal,
    "handle_id": a.handle_id,
    "status": a.status,
    "when": a.when,
    "match": a.match,
    "goal_achieved": a.goal_achieved,
    "stop_verdict": a.stop_verdict,
} for a in rows]))
`

type recallDiffAttempt struct {
	Goal         string `json:"goal"`
	HandleID     string `json:"handle_id"`
	Status       string `json:"status"`
	When         string `json:"when"`
	Match        string `json:"match"`
	GoalAchieved *bool  `json:"goal_achieved"`
	StopVerdict  string `json:"stop_verdict"`
}

func recallDiffBool(v bool) *bool { return &v }

func recallDiffWriteMetadata(t *testing.T, ws, dir string, row map[string]any) {
	t.Helper()
	p := filepath.Join(ws, "runs", dir)
	if err := os.MkdirAll(p, 0o755); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(row)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(p, "metadata.json"), b, 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestFindPriorAttemptsMatchesCPython covers the three ported match lanes,
// exclusion, verdict tri-state, the status-derived interrupt verdict, and
// newest-first ordering.  The expected rows are stated before either answer
// is compared, so CPython is checked rather than silently made the oracle.
func TestFindPriorAttemptsMatchesCPython(t *testing.T) {
	ws := t.TempDir()
	goal := "deploy the canary service"
	now := time.Now().UTC().Truncate(time.Second)
	stamp := func(ago time.Duration) string {
		return now.Add(-ago).Format("2006-01-02T15:04:05+00:00")
	}

	recallDiffWriteMetadata(t, ws, "exact-run", map[string]any{
		"handle_id": "exact", "started_at": stamp(time.Hour),
		"prompt": "  DEPLOY the canary service  ", "status": "done",
		"goal_achieved": true,
	})
	recallDiffWriteMetadata(t, ws, "near-run", map[string]any{
		"handle_id": "near", "started_at": stamp(2 * time.Hour),
		"prompt": "deploy, the canary service!", "status": "stuck",
		"goal_achieved": false,
	})
	recallDiffWriteMetadata(t, ws, "project-run", map[string]any{
		"handle_id": "project", "started_at": stamp(3 * time.Hour),
		"prompt": "inspect an unrelated dashboard", "status": "unknown",
		"project": "canary-program",
	})
	recallDiffWriteMetadata(t, ws, "interrupt-run", map[string]any{
		"handle_id": "interrupt", "started_at": stamp(4 * time.Hour),
		"prompt": goal, "status": "interrupted",
	})
	// The excluded row is deliberately an otherwise exact match: if the
	// exclusion guard disappears, it changes both count and ordering.
	recallDiffWriteMetadata(t, ws, "self-run", map[string]any{
		"handle_id": "self", "started_at": stamp(30 * time.Minute),
		"prompt": goal, "status": "done",
	})
	// Outside the window is a distinct non-match path, not fixture padding.
	recallDiffWriteMetadata(t, ws, "old-run", map[string]any{
		"handle_id": "old", "started_at": stamp(25 * time.Hour),
		"prompt": goal, "status": "done",
	})

	want := []recallDiffAttempt{
		{Goal: "  DEPLOY the canary service  ", HandleID: "exact", Status: "done",
			When: stamp(time.Hour), Match: "exact", GoalAchieved: recallDiffBool(true)},
		{Goal: "deploy, the canary service!", HandleID: "near", Status: "stuck",
			When: stamp(2 * time.Hour), Match: "near", GoalAchieved: recallDiffBool(false)},
		{Goal: "inspect an unrelated dashboard", HandleID: "project", Status: "unknown",
			When: stamp(3 * time.Hour), Match: "project"},
		{Goal: goal, HandleID: "interrupt", Status: "interrupted",
			When: stamp(4 * time.Hour), Match: "exact", StopVerdict: "external-interrupt"},
	}

	var py []recallDiffAttempt
	pyprobe.Probe{Marker: "recall.py", Workspace: ws}.RunJSON(t,
		recallPriorAttemptsProbe, &py, goal, "canary-program", "self")
	if !reflect.DeepEqual(py, want) {
		t.Fatalf("CPython returned %#v, want independently stated %#v", py, want)
	}

	got, skipped, err := FindPriorAttempts(ws, goal, 24.0, "canary-program", "self")
	if err != nil {
		t.Fatal(err)
	}
	if skipped != 0 {
		t.Fatalf("healthy fixture was counted as skipped: %d", skipped)
	}
	goRows := make([]recallDiffAttempt, 0, len(got))
	for _, a := range got {
		goRows = append(goRows, recallDiffAttempt{
			Goal: a.Goal, HandleID: a.HandleID, Status: a.Status, When: a.When,
			Match: a.Match, GoalAchieved: a.GoalAchieved, StopVerdict: a.StopVerdict,
		})
	}
	if !reflect.DeepEqual(goRows, py) {
		t.Errorf("FindPriorAttempts differs from CPython\n Go: %#v\n Py: %#v", goRows, py)
	}
}

// The loop function mixes the ported tiered retrieval with several explicitly
// unported substrates.  The probe leaves the ranked retrieval live but turns
// those other injectors and Python's receipt write-back inert, so its answer
// is exactly the read-only slice recall.go implements.
const recallLessonsProbe = `
import json, sys
import camera_log, captains_log, knowledge_web, memory, portability, recall

knowledge_web._USE_HYBRID = False
knowledge_web._increment_times_applied = lambda *a, **k: None
portability.load_cache = lambda: {}
portability.apply_portability = lambda scored, *a, **k: (scored, [])
memory.load_lessons = lambda *a, **k: []
memory.standing_rules_with_ids = lambda *a, **k: ("", [])
memory.inject_decisions = lambda *a, **k: ""
memory.search_graveyard = lambda *a, **k: []
recall.recent_learning_activity = lambda: ""
recall._project_artifact_context = lambda *a, **k: ""
camera_log.log_fork_frame = lambda *a, **k: False
captains_log.log_event = lambda *a, **k: None

r = recall.recall(sys.argv[1], slice="loop")
print(json.dumps({"lessons": r.lessons,
                  "lesson_ids_cited": r.sources.get("lesson_ids_cited", [])}))
`

func recallDiffWriteLesson(t *testing.T, ws string, row map[string]any) {
	t.Helper()
	p := filepath.Join(ws, "memory", "medium")
	if err := os.MkdirAll(p, 0o755); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(row)
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(filepath.Join(p, "lessons.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if _, err := f.Write(append(b, '\n')); err != nil {
		t.Fatal(err)
	}
}

func recallDiffLesson(id, taskType, outcome, text string, receipts map[string]any) map[string]any {
	row := map[string]any{
		"lesson_id": id, "task_type": taskType, "outcome": outcome,
		"lesson": text, "source_goal": "", "confidence": 1.0,
		"tier": "medium", "score": 1.0,
		"last_reinforced": time.Now().UTC().Format("2006-01-02"),
		// Citation evidence prevents the deliberately unported portability
		// lane from changing ranking and pins ordinary TF-IDF behaviour.
		"evidence_sources": []string{"fixture"},
	}
	for k, v := range receipts {
		row[k] = v
	}
	return row
}

// TestRecallLessonsMatchCPython pins agenda-first selection, untyped top-up
// and dedup by lesson id, plus icon and receipt spelling.  The score gaps are
// intentional: the three candidate texts have three, two, and one query
// terms, so a reversed or input-order rank cannot agree by accident.
func TestRecallLessonsMatchCPython(t *testing.T) {
	ws := t.TempDir()
	goal := "canary rollout deployment"
	recallDiffWriteLesson(t, ws, recallDiffLesson("agenda", "agenda", "done",
		"canary rollout deployment agenda", map[string]any{
			"times_reinforced": 2, "sessions_validated": 3, "times_applied": 1,
		}))
	recallDiffWriteLesson(t, ws, recallDiffLesson("general", "general", "stuck",
		"canary rollout general", nil))
	recallDiffWriteLesson(t, ws, recallDiffLesson("verify", "verify", "done",
		"canary verification", map[string]any{"sessions_validated": 4}))

	wantLessons := "## Lessons from Prior Runs (weigh by their receipts)\n" +
		"- ✓ canary rollout deployment agenda (reinforced 2x, 3 sessions, applied 1x)\n" +
		"- ✗ canary rollout general (observed once)\n" +
		"- ✓ canary verification (4 sessions)"
	wantIDs := []string{"agenda", "general", "verify"}
	var py struct {
		Lessons string   `json:"lessons"`
		IDs     []string `json:"lesson_ids_cited"`
	}
	pyprobe.Probe{Marker: "recall.py", Workspace: ws}.RunJSON(t,
		recallLessonsProbe, &py, goal)
	if py.Lessons != wantLessons || !reflect.DeepEqual(py.IDs, wantIDs) {
		t.Fatalf("CPython lessons = %#v / %#v, want %#v / %#v",
			py.Lessons, py.IDs, wantLessons, wantIDs)
	}

	got := Recall(ws, goal, "")
	ids, _ := got.Sources["lesson_ids_cited"].([]string)
	if got.Lessons != py.Lessons || !reflect.DeepEqual(ids, py.IDs) {
		t.Errorf("Recall lessons differ from CPython\n Go: %#v / %#v\n Py: %#v / %#v",
			got.Lessons, ids, py.Lessons, py.IDs)
	}
}

// This is a pure rendering probe.  A fixture workspace is still supplied so
// the harness has the same guarded environment as the disk-backed probes.
const recallContextProbe = `
import json, recall, sys

rows = json.loads(sys.argv[1])
attempts = [recall.PriorAttempt(**row) for row in rows]
r = recall.RecallResult(thread=None, prior_attempts=attempts)
print(json.dumps(r.as_context_block()))
`

// TestContextBlockMatchesCPython pins the only ported portion of Python's
// ancestry summary: sorted status wording, SF-2 verdict counts, either-channel
// interrupt counting, and the newest attempt sentence.
func TestContextBlockMatchesCPython(t *testing.T) {
	tr, fa := true, false
	rows := []recallDiffAttempt{
		{Status: "stuck", When: "2026-08-26T11:00:00+00:00", GoalAchieved: &fa},
		{Status: "done", When: "2026-08-26T10:00:00+00:00", GoalAchieved: &tr},
		{Status: "stranded", When: "2026-08-26T09:00:00+00:00"},
		{Status: "stuck", When: "2026-08-26T08:00:00+00:00", StopVerdict: "external-interrupt"},
	}
	want := "== Recall (what the system already knows) ==\n" +
		"Prior attempts at this goal (recent window): 4 runs — 1 done, 1 stranded, 2 stuck; " +
		"goal verdicts: 1 achieved, 1 NOT achieved, rest unjudged; " +
		"2 externally interrupted (not goal evidence). Newest: 2026-08-26T11:00:00+00:00 (stuck). " +
		"Do not repeat an approach that already failed; if every prior attempt failed the same way, " +
		"change the approach or surface the blocker instead of retrying."
	ws := t.TempDir()
	var py string
	pyprobe.Probe{Marker: "recall.py", Workspace: ws}.RunJSON(t, recallContextProbe,
		&py, pyprobe.Arg(t, rows))
	if py != want {
		t.Fatalf("CPython context = %q, want independently stated %q", py, want)
	}
	prior := make([]PriorAttempt, len(rows))
	for i, r := range rows {
		prior[i] = PriorAttempt{
			Status: r.Status, When: r.When, GoalAchieved: r.GoalAchieved,
			StopVerdict: r.StopVerdict,
		}
	}
	if got := (Result{PriorAttempts: prior}).ContextBlock(); got != py {
		t.Errorf("ContextBlock differs from CPython\n Go: %q\n Py: %q", got, py)
	}
}
