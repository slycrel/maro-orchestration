package pypath

import (
	"go/ast"
	"go/parser"
	"go/token"
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

// TestNoNewByteWiseFilenameSortAppears is the SPELLING half of the
// surrogateescape ordering guard: it counts `sort.Strings` calls, wherever
// they appear, including in functions that never touch a directory.
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
//
// It is only half the guard, and adversarial review r6 said why: the
// CLASS is byte-wise ordering of filenames and it has at least four
// spellings in this tree. This test cannot see the other three.
// TestNoDirectoryListingIsSortedByRawByte below is the other half, and
// the two overlap on purpose — each covers a gap the other has.
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

// ---------------------------------------------------------------------
// The class half: functions that LIST A DIRECTORY and then SORT.
// ---------------------------------------------------------------------

// dirListingCalls are the ways this module gets names out of a directory.
// Matched on the selector/identifier NAME only, so `os.ReadDir`,
// `f.ReadDir` and a future `xfs.ReadDir` all count.
var dirListingCalls = map[string]bool{
	"ReadDir": true, "Readdirnames": true, "Readdir": true,
	"ReadDirNames": true, "Glob": true, "Walk": true, "WalkDir": true,
	"readdirOrder": true,
}

// sortingCalls are the calls that actually PERFORM a sort. Deliberately
// not `sort.Reverse` or `sort.StringSlice`: those are adapters, and they
// only ever run inside a `sort.Sort` that is already on this list.
// Counting them too would report one sort twice.
var sortingCalls = map[string]bool{
	"sort.Sort": true, "sort.Stable": true, "sort.Strings": true,
	"sort.Ints": true, "sort.Float64s": true,
	"sort.Slice": true, "sort.SliceStable": true,
	"slices.Sort": true, "slices.SortFunc": true,
	"slices.SortStableFunc": true, "slices.SortedFunc": true,
}

// dirSortAllowlist is every function in the module that both lists a
// directory and sorts WITHOUT pypath.FSLess, keyed `<rel path>:<func>`,
// with the reason the raw-byte order is correct there.
//
// Every entry must still be OBSERVED by the scan. An entry that stops
// matching fails the test rather than passing quietly — that is what
// stops the detector from rotting into a scan that finds nothing (L1).
var dirSortAllowlist = map[string]string{
	"internal/sheriff/sheriff.go:CheckProject": "sorts by MODIFICATION TIME, " +
		"not by name; the names are only carried along",
	"internal/recall/recall.go:FindPriorAttempts": "two sorts, neither on a " +
		"filename: run dirs by mtime, attempts by the raw started_at string",
	"internal/tasks/tasks.go:List": "a glob the Python does not sort at all; " +
		"the port sorts for determinism and the differential does not pin order",
	"internal/artifactcheck/artifactcheck.go:SnapshotDir": "os.walk does not " +
		"sort dirnames, so this order is the port's own, not a Python sorted()",
	"internal/closure/inventory.go:projectFileInventory": "os.walk does not " +
		"sort, so this order is the port's own guarantee over a prompt listing",
	"internal/pack/adopt.go:Adopt": "sorts the NOT-FOUND name list for the " +
		"error message, built from argv rather than from the directory read",
}

// TestNoDirectoryListingIsSortedByRawByte is the CLASS half of the
// surrogateescape ordering guard.
//
// The class is: a function reads names out of a directory and then puts
// them in order. If that order reproduces a Python `sorted()`, it must go
// through pypath.FSLess, because Python compares surrogateescape-decoded
// CODE POINTS and every Go sort compares raw BYTES. The two part for any
// name that is not valid UTF-8 — and when the sorted list is then
// TRUNCATED (a `[:7]`, a cap, a `[0]`), the two runtimes do not merely
// order differently, they name DIFFERENT FILES.
//
// The predecessor guard above looked for the string `sort.Strings(`. That
// is one spelling of four in this tree, and the two sites it could not see
// — skills.LoadSkillProvenance and record.updateMemoryIndex, both
// `sort.Sort(sort.Reverse(sort.StringSlice(...)))`, both reproducing a
// real Python `sorted(..., reverse=True)` over a glob — were real bugs
// that had shipped. This test is written from the class instead: parse
// the file, find the functions that do both things, and require a reason
// for each one that does not use FSLess.
//
// Known gap, named rather than papered over: the scan is per-FUNCTION, so
// a listing in a helper whose result the CALLER sorts is invisible to it.
// The spelling guard above partially covers that case, which is why both
// tests are kept.
func TestNoDirectoryListingIsSortedByRawByte(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	fset := token.NewFileSet()

	type site struct {
		key  string
		call string
		line int
	}
	var found []site
	listers, scanned := 0, 0

	err = filepath.WalkDir(root, func(p string, d os.DirEntry, werr error) error {
		if werr != nil {
			return werr
		}
		if d.IsDir() || !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		f, perr := parser.ParseFile(fset, p, nil, 0)
		if perr != nil {
			return perr
		}
		scanned++
		rel, _ := filepath.Rel(root, p)
		for _, decl := range f.Decls {
			fn, ok := decl.(*ast.FuncDecl)
			if !ok || fn.Body == nil {
				continue
			}
			lists := false
			var sorts []*ast.CallExpr
			ast.Inspect(fn.Body, func(n ast.Node) bool {
				call, ok := n.(*ast.CallExpr)
				if !ok {
					return true
				}
				switch fun := call.Fun.(type) {
				case *ast.SelectorExpr:
					if dirListingCalls[fun.Sel.Name] {
						lists = true
					}
					if pkg, ok := fun.X.(*ast.Ident); ok &&
						sortingCalls[pkg.Name+"."+fun.Sel.Name] {
						sorts = append(sorts, call)
					}
				case *ast.Ident:
					if dirListingCalls[fun.Name] {
						lists = true
					}
				}
				return true
			})
			if !lists {
				continue
			}
			listers++
			for _, call := range sorts {
				if usesFSLess(call) {
					continue
				}
				found = append(found, site{
					key:  rel + ":" + fn.Name.Name,
					call: exprText(call.Fun),
					line: fset.Position(call.Pos()).Line,
				})
			}
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	// L1 again: a detector that matched nothing passes vacuously. The
	// floors are the two things that must both still be working — the
	// parse, and the "lists a directory" half of the predicate.
	if scanned < 100 || listers < 20 {
		t.Fatalf("scan parsed %d files and found %d directory-listing "+
			"functions; too few to be measuring anything", scanned, listers)
	}

	seen := map[string]bool{}
	sort.Slice(found, func(i, j int) bool { return found[i].key < found[j].key })
	for _, s := range found {
		seen[s.key] = true
		if _, listed := dirSortAllowlist[s.key]; listed {
			continue
		}
		t.Errorf("%s (line %d) lists a directory and then calls %s without "+
			"pypath.FSLess. If that order reproduces a Python sorted() over "+
			"FILENAMES it is a divergence: Python compares "+
			"surrogateescape-decoded code points, every Go sort compares raw "+
			"bytes, and a truncated list then names different files. Convert "+
			"it, or add it to dirSortAllowlist with the reason it is not a "+
			"filename order.", s.key, s.line, s.call)
	}
	for key, reason := range dirSortAllowlist {
		if !seen[key] {
			t.Errorf("dirSortAllowlist has %q (%s) but the scan no longer "+
				"finds it. Either the site was fixed — delete the row — or "+
				"the detector stopped seeing it, which would make this whole "+
				"test pass without measuring anything.", key, reason)
		}
	}
	t.Logf("%d files parsed, %d directory-listing functions, %d raw-byte "+
		"sorts inside them", scanned, listers, len(found))
}

// usesFSLess reports whether pypath.FSLess appears anywhere inside the
// call — as the comparator of a sort.Slice, inside the Less method a
// sort.Sort is handed, or wrapped in a reverse closure.
func usesFSLess(call *ast.CallExpr) bool {
	hit := false
	ast.Inspect(call, func(n ast.Node) bool {
		switch id := n.(type) {
		case *ast.SelectorExpr:
			if id.Sel.Name == "FSLess" {
				hit = true
			}
		case *ast.Ident:
			if id.Name == "FSLess" {
				hit = true
			}
		}
		return !hit
	})
	return hit
}

// exprText renders a call's function expression for the failure message.
func exprText(e ast.Expr) string {
	switch v := e.(type) {
	case *ast.SelectorExpr:
		return exprText(v.X) + "." + v.Sel.Name
	case *ast.Ident:
		return v.Name
	}
	return "a sort"
}
