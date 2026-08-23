package orch

import (
	"fmt"
	"os"
	"testing"
)

// The Go half of the mission-store cross-runtime differential. It is
// skipped unless MARO_MISSION_DRIVE_WS names a workspace, so `go test`
// never runs it by accident, and it lives in-tree for the reason the
// project-ledger probe does: an interop claim that can only be re-checked
// by rebuilding a throwaway harness stops being re-checked.
//
// The Python half is scripts-side (scratchpad/mission/py_drive.py). Each
// runtime writes the same mission into its own workspace and the driver
// diffs the bytes, then each reads the OTHER's files and re-saves, which
// is the part that catches a divergence a fresh-write comparison misses:
// a reader that drops a field writes a smaller file on the way back out.
func TestMissionDriveProbe(t *testing.T) {
	ws := os.Getenv("MARO_MISSION_DRIVE_WS")
	if ws == "" {
		t.Skip("set MARO_MISSION_DRIVE_WS to run the cross-runtime probe")
	}
	mode := os.Getenv("MARO_MISSION_DRIVE_MODE")

	sess := "w9"
	res := "ok"
	valres := "n/a"
	m := &Mission{
		ID: "mi1", Goal: "Build a thing — properly", Project: "proj-a",
		Status: "running", CreatedAt: "2026-08-23T00:00:00+00:00",
		Milestones: []Milestone{
			{
				ID: "m1", Title: "First", Status: "running",
				ValidationCriteria: []string{"it works", "it is fast"},
				DependsOn:          []string{},
				Features: []Feature{
					{ID: "f1", Title: "Feature one", Status: "pending"},
					{ID: "f2", Title: "Café ☕", Status: "done",
						WorkerSessionID: &sess, ResultSummary: &res, ElapsedMS: 1234},
				},
			},
			{
				ID: "m2", Title: "Second", Status: "pending",
				ValidationCriteria: []string{}, ValidationResult: &valres,
				DependsOn: []string{"m1"},
			},
		},
	}

	switch mode {
	case "write":
		if err := SaveMission(ws, m, "proj-a"); err != nil {
			t.Fatal(err)
		}
		if _, err := GenerateFeatureManifest(ws, m, "proj-a"); err != nil {
			t.Fatal(err)
		}
		if err := WriteMissionLog(ws, MissionResult{
			MissionID: "mi1", Project: "proj-a", Goal: "Build a thing — properly",
			Status: "done", MilestonesDone: 1, MilestonesTotal: 2,
			FeaturesDone: 1, FeaturesTotal: 2, ElapsedMS: 5000,
		}, m); err != nil {
			t.Fatal(err)
		}
	case "reread":
		// Read whatever is on disk (written by either runtime) and write it
		// straight back. A byte-identical result proves the reader lost
		// nothing and the writer spells it the same way.
		loaded := LoadMission(ws, "proj-a")
		if loaded == nil {
			t.Fatal("LoadMission refused a store Python wrote")
		}
		if err := SaveMission(ws, loaded, "proj-a"); err != nil {
			t.Fatal(err)
		}
	case "derive":
		// The derived answers both runtimes must agree on.
		for _, s := range ListMissions(ws) {
			fmt.Printf("SUMMARY\t%s\t%s\t%s\t%s\t%d\t%d\t%d\t%d\t%s\n",
				s.Project, s.MissionID, s.Goal, s.Status, s.MilestonesTotal,
				s.MilestonesDone, s.FeaturesDone, s.FeaturesTotal, s.CreatedAt)
		}
		for _, p := range PendingMissions(ws) {
			fmt.Printf("PENDING\t%s\t%d\n", p.Project, p.MilestonesPending)
		}
		// Rendered through this package's own ensure_ascii escaper, which is
		// exactly json.dumps(str) — so the Python half's json.dumps output is
		// directly comparable instead of being diffed against Go's %q, which
		// escapes a different set.
		esc, err := pyString(MorningBriefing(ws, 5))
		if err != nil {
			t.Fatal(err)
		}
		fmt.Printf("BRIEFING\t%s\n", esc)
	default:
		t.Fatalf("set MARO_MISSION_DRIVE_MODE to write|reread|derive, got %q", mode)
	}
}
