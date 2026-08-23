package orch

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// project seeds one project with a ledger, a priority and an mtime.
func project(t *testing.T, ws, slug, body string, priority int, mtime time.Time) {
	t.Helper()
	if err := os.MkdirAll(ProjectDir(ws, slug), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(NextPath(ws, slug), []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(PriorityPath(ws, slug),
		[]byte(itoa(priority)+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if !mtime.IsZero() {
		if err := os.Chtimes(NextPath(ws, slug), mtime, mtime); err != nil {
			t.Fatal(err)
		}
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	if neg {
		return "-" + string(b)
	}
	return string(b)
}

// Global selection is highest priority first, then the project neglected
// longest. The oldest-first tiebreak is anti-starvation: without it a
// busy project keeps winning and its equal-priority neighbours never run,
// which is a scheduler that quietly stops being fair rather than one that
// visibly breaks.
func TestGlobalSelectionPrefersPriorityThenTheMostNeglected(t *testing.T) {
	ws := t.TempDir()
	old := time.Now().Add(-72 * time.Hour)
	recent := time.Now().Add(-1 * time.Hour)
	project(t, ws, "low-but-ancient", "- [ ] a\n", 1, old.Add(-500*time.Hour))
	project(t, ws, "high-recent", "- [ ] a\n", 9, recent)
	project(t, ws, "high-ancient", "- [ ] a\n", 9, old)

	slug, it, err := SelectGlobalNext(ws)
	if err != nil {
		t.Fatal(err)
	}
	if slug != "high-ancient" || it == nil {
		t.Fatalf("priority wins, then age: got %q", slug)
	}

	// A project with no TODO left is skipped rather than returned empty,
	// so the sweep falls through to the next candidate.
	project(t, ws, "high-ancient", "- [x] a\n", 9, old)
	slug, _, err = SelectGlobalNext(ws)
	if err != nil {
		t.Fatal(err)
	}
	if slug != "high-recent" {
		t.Fatalf("a drained project must be skipped: got %q", slug)
	}
}

// The lifecycle markers are the operator's off switch. Porting selection
// without honouring them would make this runtime drain a project someone
// had explicitly pulled out of rotation — and the Python runtime, reading
// the same directory, would keep skipping it.
func TestGlobalSelectionSkipsFailedAndPausedProjects(t *testing.T) {
	ws := t.TempDir()
	project(t, ws, "aaa-paused", "- [ ] a\n", 9, time.Now().Add(-100*time.Hour))
	project(t, ws, "bbb-failed", "- [ ] a\n", 9, time.Now().Add(-90*time.Hour))
	project(t, ws, "ccc-live", "- [ ] a\n", 1, time.Now())
	touch(t, filepath.Join(ProjectDir(ws, "aaa-paused"), ".maro-paused"))
	touch(t, filepath.Join(ProjectDir(ws, "bbb-failed"), ".maro-failed"))

	if got := LifecycleState(ws, "aaa-paused"); got != "paused" {
		t.Fatalf("%q", got)
	}
	if got := LifecycleState(ws, "bbb-failed"); got != "failed" {
		t.Fatalf("%q", got)
	}
	if got := LifecycleState(ws, "ccc-live"); got != "active" {
		t.Fatalf("%q", got)
	}
	slug, _, err := SelectGlobalNext(ws)
	if err != nil {
		t.Fatal(err)
	}
	if slug != "ccc-live" {
		t.Fatalf("a paused or failed project must not be drained: got %q", slug)
	}
}

// Python sorts by (priority, -mtime) with reverse=True and its sort is
// stable, so a FULL tie falls back to the ascending-slug order
// list_projects produced — the slug is not in the sort key.
func TestGlobalSelectionBreaksAFullTieBySlugAscending(t *testing.T) {
	ws := t.TempDir()
	stamp := time.Unix(1700000000, 0)
	for _, slug := range []string{"zulu", "alpha", "mike"} {
		project(t, ws, slug, "- [ ] a\n", 5, stamp)
	}
	slug, _, err := SelectGlobalNext(ws)
	if err != nil {
		t.Fatal(err)
	}
	if slug != "alpha" {
		t.Fatalf("a full tie resolves to the first slug in sorted order: %q", slug)
	}
}

func touch(t *testing.T, path string) {
	t.Helper()
	if err := os.WriteFile(path, nil, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestListProjectsNeedsALedgerAndSortsByCodePoint(t *testing.T) {
	ws := t.TempDir()
	project(t, ws, "beta", "- [ ] a\n", 0, time.Time{})
	project(t, ws, "alpha", "- [ ] a\n", 0, time.Time{})
	// A directory without NEXT.md is not a project.
	if err := os.MkdirAll(ProjectDir(ws, "not-a-project"), 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := ListProjects(ws)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0] != "alpha" || got[1] != "beta" {
		t.Fatalf("%v", got)
	}
	// A missing projects root reads as no projects, not as an error: path
	// helpers here do not create directories as a side effect.
	if got, err := ListProjects(t.TempDir()); err != nil || got != nil {
		t.Fatalf("%v %v", got, err)
	}
}

func TestProjectPriorityFallsBackToZeroRatherThanFailing(t *testing.T) {
	ws := t.TempDir()
	project(t, ws, "p", "- [ ] a\n", 0, time.Time{})
	write := func(s string) { touchWith(t, PriorityPath(ws, "p"), s) }
	for raw, want := range map[string]int{
		"7\n": 7, "  -3  ": -3, "+4": 4, "1_0": 10, "0": 0,
		// Python catches ValueError and returns 0: a hand-edited word
		// deprioritizes the project, it does not stop the drain.
		"high": 0, "": 0, "3.5": 0, "_5": 0, "5_": 0, "1__0": 0,
	} {
		write(raw)
		if got := ProjectPriority(ws, "p"); got != want {
			t.Errorf("%q -> %d, want %d", raw, got, want)
		}
	}
	// A missing PRIORITY file is zero, not an error.
	if err := os.Remove(PriorityPath(ws, "p")); err != nil {
		t.Fatal(err)
	}
	if got := ProjectPriority(ws, "p"); got != 0 {
		t.Errorf("missing -> %d", got)
	}
}

func touchWith(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// The dedupe check happens INSIDE the lock. A caller-side pre-check alone
// is a TOCTOU: two finalizers can both observe absence and then serialize
// only their appends, which is how the same risk gets recorded twice.
func TestSectionAppendDedupesInsideTheLock(t *testing.T) {
	ws := t.TempDir()
	if _, err := EnsureProject(ws, "p", "ship it", 3); err != nil {
		t.Fatal(err)
	}
	wrote, err := AppendRisk(ws, "p", []string{"disk may fill [risk-7]"}, "[risk-7]")
	if err != nil || !wrote {
		t.Fatalf("%v %v", wrote, err)
	}
	wrote, err = AppendRisk(ws, "p", []string{"disk may fill [risk-7]"}, "[risk-7]")
	if err != nil {
		t.Fatal(err)
	}
	if wrote {
		t.Fatal("the second append must be refused by the token")
	}
	raw, err := os.ReadFile(RisksPath(ws, "p"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Count(string(raw), "[risk-7]") != 1 {
		t.Fatalf("%s", raw)
	}
	if !strings.HasPrefix(string(raw), "# RISKS\n\n") {
		t.Fatalf("the heading is written on first use: %q", raw)
	}
	// Without a token every append lands, stamped.
	if err := AppendDecision(ws, "p", []string{"chose A"}); err != nil {
		t.Fatal(err)
	}
	dec, _ := os.ReadFile(DecisionsPath(ws, "p"))
	if !strings.Contains(string(dec), "- chose A") ||
		!strings.Contains(string(dec), "\n## 20") {
		t.Fatalf("%s", dec)
	}
}

// EnsureProject is idempotent on the ledger — calling it again on a live
// project must not reset its plan — and deliberately does NOT pre-create
// RISKS.md or PROVENANCE.md. A "(fill in)" stub minted here outlives any
// run that has nothing to record, and because it counts as a file
// modified during the run, curation once served the stub as a run
// deliverable.
func TestEnsureProjectIsIdempotentAndMintsNoStubs(t *testing.T) {
	ws := t.TempDir()
	if _, err := EnsureProject(ws, "p", "ship the thing", 2); err != nil {
		t.Fatal(err)
	}
	want := "# NEXT — p\n\nMission:\n\n> ship the thing\n\n## Checklist\n\n" +
		"- [ ] Define success criteria\n- [ ] Create first-pass plan\n" +
		"- [ ] Execute next leaf task\n"
	raw, _ := os.ReadFile(NextPath(ws, "p"))
	if string(raw) != want {
		t.Fatalf("template drifted from Python's:\n%q", raw)
	}
	for _, p := range []string{RisksPath(ws, "p"), ProvenancePath(ws, "p")} {
		if _, err := os.Stat(p); err == nil {
			t.Fatalf("%s must not be pre-created", p)
		}
	}
	// Advance the plan, then re-ensure: the ledger and the recorded
	// decisions survive, the priority is rewritten.
	if err := MarkItem(ws, "p", 8, StateDone); err != nil {
		t.Fatal(err)
	}
	if _, err := EnsureProject(ws, "p", "ship the thing", 9); err != nil {
		t.Fatal(err)
	}
	raw, _ = os.ReadFile(NextPath(ws, "p"))
	if !strings.Contains(string(raw), "- [x] Define success criteria") {
		t.Fatalf("re-ensuring reset the plan:\n%s", raw)
	}
	if got := ProjectPriority(ws, "p"); got != 9 {
		t.Fatalf("priority is rewritten: %d", got)
	}
	dec, _ := os.ReadFile(DecisionsPath(ws, "p"))
	if strings.Count(string(dec), "Project created.") != 1 {
		t.Fatalf("the creation decision must be recorded once:\n%s", dec)
	}
}

func TestListBlockedProjectsRanksWorstFirst(t *testing.T) {
	ws := t.TempDir()
	project(t, ws, "clean", "- [ ] a\n", 9, time.Time{})
	project(t, ws, "one-blocked", "- [!] a\n", 5, time.Time{})
	project(t, ws, "two-blocked", "- [!] a\n- [!] b\n", 5, time.Time{})
	project(t, ws, "top", "- [!] a\n", 7, time.Time{})
	got, err := ListBlockedProjects(ws)
	if err != nil {
		t.Fatal(err)
	}
	var order []string
	for _, s := range got {
		order = append(order, s.Slug)
	}
	if len(order) != 3 || order[0] != "top" || order[1] != "two-blocked" ||
		order[2] != "one-blocked" {
		t.Fatalf("%v", order)
	}
}
