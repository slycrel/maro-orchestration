package pyprobe

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// directProbeSites is how many `exec.Command("python3"` calls live in the
// module's tests OUTSIDE this package.
//
// This package's doc says there were eight hand-rolled probe runners and
// that consolidating them is what made a failing probe fatal, a writing
// probe guarded, and the operator's config isolated. What it does not say
// — and what a census run on 2026-08-27 found — is that the consolidation
// gave the eight a shared answer without ever replacing the callers.
// There are ninety-seven direct sites, and every one of them is outside
// the sandbox, the live-workspace refusal and the shared module Blocker.
//
// The damage from that has already happened once: a probe that declared
// itself read-only wrote 648 synthetic rows into the operator's live
// captain's log over three days. That one is fixed at the source, and a
// whole-suite run with HOME repointed showed it was the only site
// currently writing outside its own tree. The rest is latent.
//
// Converting ninety-seven sites is a sweep, not a fix, and it is filed
// rather than done. This number is what stops the class from GROWING
// while it waits: a new direct site fails here, and the fix is to use
// pyprobe.Probe rather than to bump the number. Bumping it needs a
// sentence saying why that site cannot.
const directProbeSites = 97

// TestNoNewHandRolledProbeRunnerAppears counts them.
func TestNoNewHandRolledProbeRunnerAppears(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	self, err := filepath.Abs(".")
	if err != nil {
		t.Fatal(err)
	}
	total, scanned := 0, 0
	worst := map[string]int{}
	err = filepath.WalkDir(root, func(p string, d os.DirEntry, werr error) error {
		if werr != nil {
			return werr
		}
		if d.IsDir() || !strings.HasSuffix(p, "_test.go") {
			return nil
		}
		if strings.HasPrefix(p, self+string(filepath.Separator)) {
			return nil
		}
		src, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		scanned++
		n := strings.Count(string(src), `exec.Command("python3"`)
		if n > 0 {
			rel, _ := filepath.Rel(root, p)
			worst[rel] = n
			total += n
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// A scan that found nothing to scan passes vacuously, which is the
	// failure this project keeps re-finding (L1).
	if scanned < 100 {
		t.Fatalf("scan covered %d test files; too few to be measuring "+
			"anything", scanned)
	}
	switch {
	case total > directProbeSites:
		t.Errorf("%d hand-rolled python3 runners, was %d. A new one is "+
			"outside the sandbox, the live-workspace refusal and the "+
			"shared module Blocker — use pyprobe.Probe. If it genuinely "+
			"cannot, raise the constant WITH the reason.\nfiles: %v",
			total, directProbeSites, worst)
	case total < directProbeSites:
		t.Errorf("%d hand-rolled python3 runners, was %d. Sites were "+
			"converted — lower the constant so the guard keeps measuring "+
			"the real population (L1).", total, directProbeSites)
	}
}
