package orch

import (
	"fmt"
	"os"
	"testing"
)

// TestDriveProbe is the Go half of the cross-runtime differential: it runs
// the same fixed script of ledger operations as
// scratchpad/diffledger/py_drive.py so the two workspaces can be diffed
// byte for byte. It is a PROBE, not a pin — it only runs when
// MARO_ORCH_DRIVE_WS names a workspace, so the ordinary suite skips it.
//
// Kept in the tree rather than in the scratchpad because the interop
// claim ("both runtimes write one ledger") is the reason this package
// exists, and a claim that can only be re-checked by rebuilding a
// throwaway harness stops being re-checked.
func TestDriveProbe(t *testing.T) {
	ws := os.Getenv("MARO_ORCH_DRIVE_WS")
	if ws == "" {
		t.Skip("set MARO_ORCH_DRIVE_WS to run the cross-runtime drive")
	}
	must := func(err error) {
		t.Helper()
		if err != nil {
			t.Fatal(err)
		}
	}
	if _, err := EnsureProject(ws, "demo", "ship the ledger port", 3); err != nil {
		t.Fatal(err)
	}
	idx, err := AppendNextItems(ws, "demo", []string{
		"read the spec",
		"  indented child",
		"write the code",
	})
	must(err)
	fmt.Println("APPENDED", idx)

	must(MarkItem(ws, "demo", idx[0], StateDone))
	must(MarkItem(ws, "demo", idx[2], StateDoing))
	must(MarkItem(ws, "demo", 8, StateBlocked))

	must(AppendDecision(ws, "demo", []string{
		"chose the ledger-first slice", "deferred run records"}))
	if _, err := AppendRisk(ws, "demo",
		[]string{"NEXT.md is line-addressed [risk-1]"}, "[risk-1]"); err != nil {
		t.Fatal(err)
	}
	if _, err := AppendRisk(ws, "demo",
		[]string{"duplicate attempt [risk-1]"}, "[risk-1]"); err != nil {
		t.Fatal(err)
	}
	must(AppendProvenance(ws, "demo", []string{"ported from orch_items.py"}))

	counts, err := ItemCounts(ws, "demo")
	must(err)
	status, err := ProjectStatus(ws, "demo")
	must(err)
	slug, next, err := SelectGlobalNext(ws)
	must(err)
	stranded, err := StrandedDoingItems(ws, "demo")
	must(err)
	_, items, err := ParseNext(ws, "demo")
	must(err)

	fmt.Println("COUNTS", [][2]any{{"blocked", counts.Blocked},
		{"doing", counts.Doing}, {"done", counts.Done}, {"todo", counts.Todo}})
	nextIdx := any(nil)
	if status.NextItem != nil {
		nextIdx = status.NextItem.Index
	}
	fmt.Println("STATUS", status.Slug, status.Priority, status.Todo,
		status.Doing, status.Blocked, status.Done, nextIdx)
	fmt.Println("GLOBAL", slug, next.Index, fmt.Sprintf("%q", next.Text))
	var st [][2]any
	for _, s := range stranded {
		st = append(st, [2]any{s.Index, s.Text})
	}
	fmt.Println("STRANDED", st)
	var rows [][4]any
	for _, i := range items {
		rows = append(rows, [4]any{i.Index, i.State, i.Text, i.Indent})
	}
	fmt.Println("ITEMS", rows)
}
