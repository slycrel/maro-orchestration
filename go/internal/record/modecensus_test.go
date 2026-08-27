package record

import (
	"bytes"
	"go/ast"
	"go/parser"
	"go/printer"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
)

// Every mode handed to a creating syscall, censused.
//
// WHY THIS IS A TEST AND NOT A CONVENTION. A mode does not come back.
// os.MkdirAll returns error, the directory appears, the tests assert on
// its CONTENTS, and the mode sits in the argument list doing whatever it
// likes. `go/tools/mutate-modes.py` flipped 0o044 into all 103 mode
// arguments in this tree and, outside internal/persona, killed NOTHING:
// line coverage reported every one of them as executed and no assertion
// anywhere read one. That is L57, and it is not a discipline problem --
// no amount of care makes an unobservable value observable.
//
// So the rule is enforced on the SPELLING, which is checkable, rather
// than on the resulting mode, which mostly is not:
//
//	directories  record.NewDirMode  (0o777; MkdirAll applies the umask,
//	                                 exactly as Path.mkdir() does)
//	files        0o666              (what CPython hands open(2); the
//	                                 kernel applies the umask)
//	after create record.NewFileMode() (pre-masked, because Chmod is NOT
//	                                 umask-filtered -- see AtomicWrite)
//
// A literal 0o755 or 0o644 is the bug this catches. It produces that
// mode REGARDLESS of umask, so the two runtimes agree only on a host
// whose umask happens to be 022 -- and on this box it is 002, where
// CPython creates 0o775/0o664 and those sites created 0o755/0o644.
// Fifty of them were found by censusing rather than by review, after two
// build agents independently found free modes on the same day.
//
// os.CreateTemp is in scope with no mode argument at all, and that is
// the point: it creates 0600, and a Rename publishes it. Two sites
// (knowledge.atomicRewrite, pack.writeQuarantine) shipped shared
// workspace stores at 0600 that way. Every CreateTemp site is listed
// below with what makes it safe.

type modeAllow struct {
	count  int
	reason string
}

func TestEveryModeArgumentIsSpelledTheWayCPythonSpellsIt(t *testing.T) {
	// Keyed by module-relative path. The COUNT is part of the entry: a
	// new exception in an already-listed file must move the number, and
	// an entry that stops matching anything is a lie about the codebase
	// and fails too.
	allowed := map[string]modeAllow{
		"internal/record/outcomes.go": {1, "" +
			"AtomicWrite's os.CreateTemp, which Chmods the handle before " +
			"publishing it. This is the site the other two were missing."},
		"internal/loop/project.go": {1, "" +
			"recordProjectMission's os.CreateTemp, left at 0600 " +
			"DELIBERATELY: `.mission` is this runtime's stand-in for " +
			"NEXT.md and has no Python reader whose access a narrow mode " +
			"could break. The reasoning is a comment at the site."},
		"internal/llm/subprocess.go": {1, "" +
			"a capture file in the process's own temp dir, removed on the " +
			"way out. CPython's twin is a tempfile too; 0600 agrees."},
		"internal/tasks/store.go": {1, "" +
			"os.CreateTemp matching task_store.py's tempfile.mkstemp, " +
			"which ALSO creates 0600. Agreeing, not diverging."},
	}

	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}

	// callee -> index of the mode argument, or -1 for "no mode argument,
	// report every occurrence".
	modeArg := map[string]int{
		"os.Mkdir":      1,
		"os.MkdirAll":   1,
		"os.WriteFile":  2,
		"os.OpenFile":   2,
		"os.Chmod":      1,
		"os.CreateTemp": -1,
	}
	dirCall := map[string]bool{"os.Mkdir": true, "os.MkdirAll": true}

	sanctionedDir := map[string]bool{
		"record.NewDirMode": true, "NewDirMode": true,
	}
	sanctionedFile := map[string]bool{
		"0o666": true, "record.NewFileMode()": true, "NewFileMode()": true,
	}

	found := map[string]int{}
	detail := map[string][]string{}
	files, calls := 0, 0

	err = filepath.WalkDir(root, func(p string, d fs.DirEntry, werr error) error {
		if werr != nil {
			return werr
		}
		if d.IsDir() {
			if d.Name() == ".git" || d.Name() == "testdata" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.HasSuffix(p, ".go") || strings.HasSuffix(p, "_test.go") {
			return nil
		}
		rel, rerr := filepath.Rel(root, p)
		if rerr != nil {
			return rerr
		}
		src, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		files++
		fset := token.NewFileSet()
		file, perr := parser.ParseFile(fset, p, src, 0)
		if perr != nil {
			return perr
		}
		render := func(e ast.Expr) string {
			var b bytes.Buffer
			if err := printer.Fprint(&b, fset, e); err != nil {
				return "<unrenderable>"
			}
			return b.String()
		}
		ast.Inspect(file, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			name := render(call.Fun)
			idx, watched := modeArg[name]
			if !watched {
				return true
			}
			calls++
			if idx < 0 {
				found[rel]++
				detail[rel] = append(detail[rel],
					fset.Position(call.Pos()).String()+" "+name+
						" (creates 0600; a Rename publishes it)")
				return true
			}
			if idx >= len(call.Args) {
				return true
			}
			got := render(call.Args[idx])
			okSpelling := sanctionedFile[got]
			if dirCall[name] {
				okSpelling = sanctionedDir[got]
			}
			if okSpelling {
				return true
			}
			found[rel]++
			detail[rel] = append(detail[rel],
				fset.Position(call.Pos()).String()+" "+name+" ... "+got)
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	// Vacuity floors. A walk that silently stopped finding files, or a
	// renamed stdlib call, would otherwise report a clean census.
	if files < 100 || calls < 90 {
		t.Fatalf("the census barely ran: %d files, %d mode-taking calls. "+
			"It found 103 calls across ~180 files when it was written; a "+
			"number this low means the WALK broke, not that the tree did",
			files, calls)
	}

	for _, rel := range sortedModeKeys(found) {
		a, listed := allowed[rel]
		if !listed {
			t.Errorf("%s has %d mode argument(s) this census does not "+
				"sanction:\n    %s\n  Directories take record.NewDirMode, "+
				"files take 0o666, and a Chmod after create takes "+
				"record.NewFileMode(). A literal 0o755/0o644 ignores the "+
				"umask and diverges from CPython on any host whose umask "+
				"is not 022.",
				rel, found[rel], strings.Join(detail[rel], "\n    "))
			continue
		}
		if a.count != found[rel] {
			t.Errorf("%s: allowlisted for %d, census found %d:\n    %s\n  "+
				"Reason on file: %s\n  Move the number and say why, or fix "+
				"the new site.",
				rel, a.count, found[rel],
				strings.Join(detail[rel], "\n    "), a.reason)
		}
	}
	for _, rel := range sortedModeKeys2(allowed) {
		if found[rel] == 0 {
			t.Errorf("%s is allowlisted for %d but the census found none. "+
				"An allowlist entry that matches nothing is a false claim "+
				"about the codebase; delete it.", rel, allowed[rel].count)
		}
	}
}

func sortedModeKeys(m map[string]int) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sortedModeKeys2(m map[string]modeAllow) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
