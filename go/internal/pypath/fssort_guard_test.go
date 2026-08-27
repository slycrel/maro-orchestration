package pypath

import (
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// fsSortAllowlist is every `sort.Strings` call in the module's non-test
// source, with the reason it is allowed to sort BYTES rather than go
// through FSLess. The count is per file.
//
// Two questions decide a site, and the second is the one that matters:
//
//  1. Does the Python sort filenames here at all — or is the sort the
//     PORT's own determinism guarantee over something CPython iterates as
//     a set? The second kind must NOT be converted;
//     artifactcheck.pythonCandidates is the worked example and carries the
//     reasoning at its site.
//  2. Can the strings be non-UTF-8? Anything read from a directory can. A
//     key that arrives inside JSON cannot, because json.dumps could not
//     have written it.
//
// A site that sorts filenames AND reproduces a Python `sorted()` belongs
// on FSLess and not in this table.
var fsSortAllowlist = map[string]struct {
	n      int
	reason string
}{
	// --- JSON object keys and values: valid UTF-8 by construction -------
	"internal/pyjson/pyjson.go":       {2, "sort_keys over decoded JSON object keys"},
	"internal/pyval/pyval.go":         {2, "repr/serialisation over decoded mapping keys"},
	"internal/knowledge/tiered.go":    {1, "decoded record keys"},
	"internal/skills/testgate.go":     {1, "decoded record keys"},
	"internal/syshealth/syshealth.go": {1, "decoded record keys"},
	"internal/pack/canonical.go":      {2, "archive paths carried inside the manifest JSON"},
	"internal/pack/export.go":         {1, "artifact class names pulled out of JSON"},

	// --- program-built strings, never read from a directory -------------
	"internal/introspect/lens.go":      {2, "lens names, registered as Go constants"},
	"internal/graduation/templates.go": {1, "intervention class names"},
	"internal/closure/closure.go":      {1, "normalised claim text"},
	"internal/recall/recall.go":        {1, "status literals"},
	"internal/scans/scans.go":          {1, "scan type literals"},
	"internal/inspector/inspector.go":  {1, "breach names; Python's set() has no order at all"},
	"internal/skills/coerce.go":        {1, "a generic helper over caller-supplied strings"},
	"internal/pack/adopt.go":           {1, "the not-found error message's name list, from argv"},

	// --- filenames, but the ORDER is the port's own guarantee ----------
	"internal/tasks/tasks.go": {2, "a glob the Python does not sort; the port sorts " +
		"for determinism and the differential deliberately does not pin order"},
	"internal/artifactcheck/artifactcheck.go": {2, "the walk's subdir order (os.walk does " +
		"not sort dirnames) and pythonCandidates' fresh files (CPython iterates a set)"},
}

// TestNoNewByteWiseFilenameSortAppears is the class guard for the
// surrogateescape ordering rule.
//
// Python holds every filename surrogateescape-decoded and `sorted()`
// orders by code point; `sort.Strings` orders by raw byte. The two agree
// for all valid UTF-8 and for a bad byte against ASCII or an astral
// character, which is why the divergence survived three review rounds of
// artifactcheck and was then found ALREADY SHIPPED in sheriff, orch,
// dispatch and pack.
//
// A per-file count is a blunt instrument and it is deliberately blunt, for
// the same reason pytext's fold guard is: a false positive costs one line
// in the table above plus the sentence saying why. The false NEGATIVE it
// replaces is a dormancy verdict that two engines disagree about.
func TestNoNewByteWiseFilenameSortAppears(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	counts := map[string]int{}
	scanned, total := 0, 0
	err = filepath.WalkDir(root, func(p string, d os.DirEntry, werr error) error {
		if werr != nil {
			return werr
		}
		if d.IsDir() || !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		src, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		scanned++
		n := strings.Count(string(src), "sort.Strings(")
		if n == 0 {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		counts[rel] += n
		total += n
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// A scan that found nothing to scan passes vacuously, which is the
	// failure mode this project keeps re-finding (L1).
	if scanned < 100 || total < 15 {
		t.Fatalf("scan covered %d files / %d sort.Strings calls; too few to "+
			"be measuring anything", scanned, total)
	}

	var files []string
	for rel := range counts {
		files = append(files, rel)
	}
	for rel := range fsSortAllowlist {
		if _, seen := counts[rel]; !seen {
			files = append(files, rel)
		}
	}
	sort.Strings(files) // these are repo-relative source paths, not data
	for _, rel := range files {
		want, listed := fsSortAllowlist[rel]
		got := counts[rel]
		switch {
		case !listed:
			t.Errorf("%s has %d sort.Strings call(s) and is not in "+
				"fsSortAllowlist. If it sorts FILENAMES to reproduce a "+
				"Python sorted(), it belongs on pypath.FSLess — byte order "+
				"and code-point order part for any name that is not valid "+
				"UTF-8. If it does not, add it to the table with the "+
				"reason.", rel, got)
		case got != want.n:
			t.Errorf("%s has %d sort.Strings call(s), the allowlist says %d "+
				"(%s). A new one needs the two questions answered before "+
				"the number is bumped.", rel, got, want.n, want.reason)
		}
	}
	t.Logf("%d files scanned, %d sort.Strings calls across %d files",
		scanned, total, len(counts))
}
