package record

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// A ported package with no test file has no differential, and a
// differential is the only thing in this port that distinguishes code that
// matches CPython from code that merely compiles.
//
// This was a fact somebody had to remember. On 2026-08-27 a delegated
// tranche arrived with six new packages, four of them with zero tests, and
// nothing in the tree would have said so — the suite prints `[no test
// files]` on a line that also says `ok`, in a run whose exit status is 0.
// The port has closed two lenses this way already (the mode census, the
// fold census); this is the third, and it is the cheapest of them.
//
// The allowlist is the queue. An entry is a package that is KNOWN to have
// no differential and says why; it leaves when the differential lands.
func TestEveryPackageHasADifferential(t *testing.T) {
	allowed := map[string]string{
		"internal/pyprobe": "" +
			"the probe harness itself — it has no CPython counterpart to " +
			"differ from, and every diff test in the tree is its test.",
		"internal/missionrun": "" +
			"ported, unreferenced, and never given a differential. Predates " +
			"this census and is the reason it is scoped to `no tests at " +
			"all` rather than to `new packages`.",

		// The 2026-08-27 delegated tranche. All four are inert — nothing
		// outside internal/looptypes imports any of them — so they change
		// no behavior while they sit here, and each leaves this list when
		// its differential lands rather than when someone remembers it.
		"internal/looptypes": "" +
			"loop_types.py: LoopContext, the stamp_* vocabulary gates and " +
			"ResolveLogLevel. Differential owed.",
		"internal/runtrace": "" +
			"run_trace.py. Differential owed.",
		"internal/terrain": "" +
			"terrain.py. Differential owed.",
		"internal/worldfacts": "" +
			"world_facts.py. Differential owed.",
	}

	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(filepath.Join(root, "internal"))
	if err != nil {
		t.Fatal(err)
	}

	var bare []string
	seen := map[string]bool{}
	pkgs := 0
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		rel := "internal/" + e.Name()
		files, rerr := os.ReadDir(filepath.Join(root, rel))
		if rerr != nil {
			t.Fatal(rerr)
		}
		var hasGo, hasTest bool
		for _, f := range files {
			switch {
			case strings.HasSuffix(f.Name(), "_test.go"):
				hasTest = true
			case strings.HasSuffix(f.Name(), ".go"):
				hasGo = true
			}
		}
		if !hasGo && !hasTest {
			continue
		}
		pkgs++
		seen[rel] = true
		if !hasTest {
			bare = append(bare, rel)
		}
	}
	sort.Strings(bare)

	for _, rel := range bare {
		if _, ok := allowed[rel]; !ok {
			t.Errorf("%s has no _test.go at all. A ported package without a "+
				"differential is code that compiles, not code that matches "+
				"CPython — write one, or add an allowlist entry here saying "+
				"why it cannot have one.", rel)
		}
	}
	// The other arm, and the one that makes the list a QUEUE rather than a
	// growing pile of permissions: an entry for a package that now has
	// tests is a stale claim, and an entry for a package that no longer
	// exists is a dangling one.
	bareSet := map[string]bool{}
	for _, rel := range bare {
		bareSet[rel] = true
	}
	for rel := range allowed {
		if !seen[rel] {
			t.Errorf("%s is allowlisted as untested and does not exist — "+
				"delete the entry.", rel)
			continue
		}
		if !bareSet[rel] {
			t.Errorf("%s is allowlisted as having no differential and now "+
				"has one. Delete the entry; that is what finishing looks "+
				"like.", rel)
		}
	}

	// Vacuity floor. A census that walked the wrong directory would find
	// no packages, report nothing, and pass.
	if pkgs < 40 {
		t.Fatalf("census saw only %d packages under internal/; too few to "+
			"be walking the tree", pkgs)
	}
	t.Logf("%d packages, %d without a differential (all allowlisted)",
		pkgs, len(bare))
}
