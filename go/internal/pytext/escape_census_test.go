package pytext

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"testing"
)

// This package exists because Go's regexp and Python's re disagree about
// what `\s`, `\S`, `\w`, `\W`, `\b` and `\B` MEAN. Python's are Unicode —
// 29 space code points, 142,940 word runes, a Unicode-aware boundary;
// Go's are ASCII — 5, 63, and an ASCII boundary. A pattern transcribed
// character-for-character from a Python source therefore matches a
// different language, and every one of those has decided something real:
// an execution LANE, a redaction, a quarantine.
//
// The port has now found that class FIVE separate times, one escape at a
// time, each by a different route:
//
//	mission-r6  MEDIUM  intent      `\w` — "save the summary to café.md"
//	                                 routed to a different lane
//	mission-r6  LOW     scrub       `\S` — Go REDACTING content CPython keeps
//	mission-r7  HIGH    intent      r6's own fix, on its most ordinary input
//	2026-08-27  HIGH    provenance  `\s` — one NBSP walks an
//	                                 instruction-derived lesson past the
//	                                 quarantine gate
//	2026-08-27          intent      `\S` — the one escape r6 did not rebuild,
//	                                 in the pattern r6 and r7 both edited
//
// The last of those is the argument for this test. r6 rebuilt three
// escapes in `fileOutputRe` and left a fourth; its doc comment then
// ENUMERATED the three it had fixed, so the file read as complete. Two
// review rounds edited that exact pattern without noticing. No amount of
// looking harder finds the fifth one — the reviewer has to already
// suspect the escape is there.
//
// So: a census, with an allowlist that has to be written by hand. A new
// `\s` in a production pattern fails this test with the file, the escape
// and the helper that replaces it. That does not make the mistake
// impossible, but it makes it impossible to make SILENTLY, which is the
// difference between a lens we watch for and a shape that cannot reach a
// commit (docs/REVIEW_PATTERNS.md, "Closing a lens by construction").
//
// Test files are deliberately out of scope. A fixture's regex is Go-side
// scaffolding for reading a Go tree, not a ported Python pattern, and
// pulling them in buys a 20-entry allowlist that nobody would read.
func TestNoProductionPatternTranscribesAPythonRegexEscape(t *testing.T) {
	// Keyed by module-relative path. The COUNT is part of the entry: a
	// new escape in an already-listed file has to move the number, which
	// is the same tripwire discipline the provenance divergence rows
	// use. An entry that stops matching anything is a lie about the
	// codebase and fails too.
	allowed := map[string]struct {
		count  int
		reason string
	}{
		"internal/provenance/provenance.go": {3, "" +
			"The three patterns are kept BYTE-IDENTICAL to the ones in " +
			"lesson_provenance.py on purpose — that is what porting them " +
			"one-for-one means, and TestRegexSourceMatchesCPython pins it " +
			"— and the `\\s` is rewritten to SpaceClass at the call by " +
			"pySpace(). The `\\b` is a filed, fail-closed residual: two of " +
			"scaffoldingRe's three are INTERIOR to a `[^.]{0,60}` window, " +
			"where WordStart/WordEnd cannot be substituted without redoing " +
			"the window arithmetic."},
		"internal/scrub/scrub.go": {1, "" +
			"`\\b` around a QuoteMeta'd needle, in a pattern used for " +
			"ReplaceAllString — the offsets matter, so a consuming " +
			"WordStart/WordEnd is wrong here (see WordStart's doc, rule 2). " +
			"Named residual since mission-r7: a needle with non-ASCII " +
			"letters bounds differently across runtimes, a potential " +
			"export-side leak for a non-ASCII username or hostname."},
	}

	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}

	// The escapes, and what replaces each one here. Named in the failure
	// so the reader does not have to come find this table.
	remedy := map[string]string{
		`\s`: "pytext.SpaceClass",
		`\S`: `pytext.NotClass("")`,
		`\w`: "pytext.WordClass (or WordClassBody spliced inside (?-i: ))",
		`\W`: "pytext.NotWordClassPlus(\"\")",
		`\b`: "pytext.WordStart / pytext.WordEnd, at the pattern ENDS only",
		`\B`: "pytext.WordStart / pytext.WordEnd, negated by the caller",
	}

	found := map[string]int{}
	escapesIn := map[string]map[string]bool{}
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
		// This package IS the remedy; its own patterns are the ones being
		// compared against CPython's classes.
		if strings.HasPrefix(rel, "internal/pytext"+string(filepath.Separator)) {
			return nil
		}
		src, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		files++
		// Parsed rather than text-scanned, and that is not fastidiousness:
		// a comment INSIDE the call is the common case here (the reasons
		// are written next to the escapes they explain), and a text scan
		// counts the prose. Stripping `//` to end-of-line instead would
		// truncate any pattern containing `://` and hide whatever follows
		// — a false NEGATIVE, in a test whose whole job is to not have
		// one. The AST carries the string literals and nothing else.
		fset := token.NewFileSet()
		file, perr := parser.ParseFile(fset, p, src, 0)
		if perr != nil {
			return perr
		}
		ast.Inspect(file, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok || !isRegexpCompile(call.Fun) {
				return true
			}
			calls++
			var sb strings.Builder
			for _, a := range call.Args {
				collectStringLits(a, &sb)
			}
			text := sb.String()
			hits := map[string]bool{}
			for esc := range remedy {
				if strings.Contains(text, esc) {
					hits[esc] = true
				}
			}
			if len(hits) == 0 {
				return true
			}
			found[rel]++
			if escapesIn[rel] == nil {
				escapesIn[rel] = map[string]bool{}
			}
			for e := range hits {
				escapesIn[rel][e] = true
			}
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	for _, rel := range sortedKeys(found) {
		entry, ok := allowed[rel]
		if !ok {
			t.Errorf("%s: %d regexp.MustCompile call(s) carry a Python regex "+
				"escape %v with no entry in this test's allowlist.\n"+
				"\tGo's classes are not Python's: %s.\n"+
				"\tIf the pattern is ported from Python, use the pytext "+
				"helper. If it is Go-side and the difference cannot matter, "+
				"add an entry here saying WHY — the entry is the review.",
				rel, found[rel], sortedKeys(escapesIn[rel]),
				remedyList(remedy, escapesIn[rel]))
			continue
		}
		if entry.count != found[rel] {
			t.Errorf("%s carries %d escaped call(s), allowlisted for %d.\n"+
				"\tescapes present: %v\n"+
				"\ton record: %s",
				rel, found[rel], entry.count,
				sortedKeys(escapesIn[rel]), entry.reason)
		}
	}
	for rel, entry := range allowed {
		if found[rel] == 0 {
			t.Errorf("%s is allowlisted for %d escaped call(s) and has none. "+
				"The entry describes a divergence that no longer exists, "+
				"which is a lie about this codebase — delete it.\n\ton "+
				"record: %s", rel, entry.count, entry.reason)
		}
	}

	// A scan that found nothing to scan passes vacuously, which is the
	// failure mode this project keeps re-finding (L1). The floors are set
	// well under the 2026-08-27 census — 140 production files, 81
	// regexp.MustCompile calls, 4 of them carrying an escape — so
	// ordinary churn does not trip them, and well over zero.
	if files < 100 || calls < 55 {
		t.Fatalf("scan covered %d production files / %d regexp.MustCompile "+
			"calls; too few to be measuring anything", files, calls)
	}
	t.Logf("%d production files, %d regexp.MustCompile calls, %d carrying a "+
		"Python-divergent escape", files, calls, sum(found))
}

func isRegexpCompile(fn ast.Expr) bool {
	sel, ok := fn.(*ast.SelectorExpr)
	if !ok {
		return false
	}
	pkg, ok := sel.X.(*ast.Ident)
	if !ok || pkg.Name != "regexp" {
		return false
	}
	switch sel.Sel.Name {
	case "MustCompile", "Compile", "MustCompilePOSIX", "CompilePOSIX":
		return true
	}
	return false
}

// collectStringLits appends every string literal reachable from e, so a
// pattern spelled as a chain of concatenated literals and pytext
// identifiers arrives as just its literal halves — which is exactly the
// half this test is about. Unquoting normalizes the two spellings: `\s`
// in a raw literal and "\\s" in an interpreted one both become \s.
func collectStringLits(e ast.Expr, sb *strings.Builder) {
	switch t := e.(type) {
	case *ast.BasicLit:
		if t.Kind != token.STRING {
			return
		}
		s, err := strconv.Unquote(t.Value)
		if err != nil {
			return
		}
		sb.WriteString(s)
		// A separator, so two adjacent literals cannot form an escape
		// across the seam that neither one contains.
		sb.WriteByte('\x00')
	case *ast.BinaryExpr:
		collectStringLits(t.X, sb)
		collectStringLits(t.Y, sb)
	case *ast.ParenExpr:
		collectStringLits(t.X, sb)
	case *ast.CallExpr:
		for _, a := range t.Args {
			collectStringLits(a, sb)
		}
	}
}

func remedyList(remedy map[string]string, present map[string]bool) string {
	var parts []string
	for _, e := range sortedKeys(present) {
		parts = append(parts, e+" -> "+remedy[e])
	}
	return strings.Join(parts, "; ")
}

func sortedKeys[V any](m map[string]V) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}

func sum(m map[string]int) int {
	n := 0
	for _, v := range m {
		n += v
	}
	return n
}
