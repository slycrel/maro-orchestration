package pytext

import (
	"go/ast"
	"go/parser"
	"go/token"
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The sibling of TestNoProductionPatternTranscribesAPythonRegexEscape, for
// the class that one cannot see: `(?i)`.
//
// CPython's re.IGNORECASE folds U+0130 and U+0131 onto ASCII `i`. Go's
// (?i) folds through unicode.SimpleFold, which does not. Measured
// exhaustively in foldi_diff_test.go, `i` is the ONLY character of the 36
// ASCII letters and digits the two engines disagree about — `k` and `s`
// already agree because U+212A and U+017F have fold orbits reaching ASCII.
//
// So every `(?i)` pattern carrying a literal `i` or `I` matches a
// different language than the Python it was ported from. r12 of
// internal/artifactcheck found that live in the FABRICATION detector,
// failing open on one homoglyph.
//
// WHY THIS TEST EXISTS AND NOT A NUMBER IN THE BACKLOG. That entry
// reported the rollout surface as "16 of 40 (?i) patterns across 9
// files". Re-measuring the same question a second way answered 35 of 47
// across 10 — disagreeing on the count AND on the file list. Both were
// greps over a shape that needs parsing. And the one file whose truth is
// independently known was undercounted by both: artifactcheck needed FOUR
// wraps, and the mutation battery proves each kills rows nothing else
// kills. An enumeration can be wrong at BIRTH (L28), and this one was
// wrong twice before it was ever wrong by drifting.
//
// THE PREDICATE IS PyFoldI ITSELF, which is the point. A separate parser
// for "does this pattern carry a foldable i" would be a second opinion
// about the same question, free to disagree with the transform — and a
// census that disagrees with its own remedy is how a pattern gets
// certified as safe and stays broken. Here, exposure is DEFINED as "the
// remedy would change this pattern", so the two cannot drift.
func TestNoProductionPatternFoldsAnUnwrappedI(t *testing.T) {
	// Keyed by module-relative path, valued by the number of exposed
	// MustCompile calls that are NOT wrapped. This allowlist is the
	// rollout queue: every entry is a pattern whose Python folds the
	// Turkish i and whose Go does not. An entry leaves when the pattern is
	// wrapped and a differential proves it.
	//
	// The COUNT is part of the entry, so a new exposed pattern in an
	// already-listed file has to move the number.
	allowed := map[string]struct {
		count  int
		reason string
	}{
		"internal/closure/modality.go": {2, "" +
			"wsPattern and testRunnerPattern, which are NOT exposed and are " +
			"unwrapped on purpose. They are named vars built from " +
			"wordBounded/urlBounded, so this scan cannot read the literal and " +
			"refuses to certify it -- correctly. The claim is made mechanical " +
			"where the patterns are instead: " +
			"closure.TestTheTwoUnwrappedPatternsReallyCarryNoI asserts " +
			"PyFoldI(p) == p against the SAME strings MustCompile receives, so " +
			"adding an alternative with an i to either fails there and names " +
			"it. The other seven (?i) patterns in the package ARE wrapped, " +
			"and the mutation sweep proves each kills a row nothing else " +
			"kills."},
		"internal/persona/routing.go": {1, "" +
			"regexp.QuoteMeta(kw) around a RUNTIME keyword. QuoteMeta emits " +
			"escaped literal text and can never emit a `(?i)`, so the call " +
			"is case-SENSITIVE by construction -- but this scan reads no " +
			"literal here at all, and the honest report for a pattern it " +
			"cannot see is `I cannot see it`, not silence."},
		"internal/scrub/scrub.go": {1, "" +
			"regexp.QuoteMeta(p.needle), same shape and same reason as " +
			"internal/persona/routing.go above."},
		"internal/evolver/store.go": {1, "" +
			"NOT the same shape as the others and must not be `fixed` the " +
			"same way. `regexp.Compile(\"(?i)\" + pattern)` where pattern " +
			"is a RUNTIME string from an LLM guardrail suggestion, so there " +
			"is no literal to wrap at build time -- the equivalent fix is " +
			"to fold at the call, on the incoming pattern, which changes " +
			"what a suggestion means and wants its own decision. Filed, not " +
			"deferred by oversight."},
	}

	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}

	type site struct {
		rel     string
		line    int
		wrapped bool
		inClass bool     // PyFoldI panicked: a bare i inside a character class
		opaque  []string // operands the scan cannot read
		text    string   // the literal halves; empty means nothing readable
	}
	var sites []site
	files, calls, insensitive := 0, 0, 0

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
		// This package IS the remedy. Its own classes are deliberately
		// spelled `(?-i:[...])` — fold explicitly off — which is the
		// construction that makes every helper safe to splice into a
		// `(?i)` pattern, and is why the scan below can ignore the
		// identifier halves of every pattern it reads.
		if strings.HasPrefix(rel, "internal/pytext"+string(filepath.Separator)) {
			return nil
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
		ast.Inspect(file, func(n ast.Node) bool {
			call, ok := n.(*ast.CallExpr)
			if !ok || !isRegexpCompile(call.Fun) {
				return true
			}
			calls++
			var sb strings.Builder
			var opaque []string
			for _, a := range call.Args {
				collectStringLits(a, &sb)
				collectOpaque(a, &opaque)
			}
			text := sb.String()
			// A call with NO readable literal at all cannot be judged even
			// on the flag: `regexp.MustCompile(wsPattern)` carries its
			// `(?i)` inside the named var, so caseInsensitive("") is false
			// and the bail below drops the site in silence. That is the
			// opaque-operand hole below, one level up, and NAMING a pattern
			// is how you fall into it — hoisting closure/modality.go's two
			// unwrapped patterns into vars removed them from this census
			// while the allowlist still claimed two exposed sites there,
			// and the only symptom was the empty-entry arm firing.
			if text == "" && len(opaque) > 0 {
				sites = append(sites, site{
					rel:     rel,
					line:    fset.Position(call.Pos()).Line,
					wrapped: wrapsWithPyFoldI(call),
					opaque:  opaque,
				})
				return true
			}
			// Only patterns that actually turn the flag ON. A literal `i`
			// under no fold flag means itself in both engines.
			if !caseInsensitive(text) {
				return true
			}
			insensitive++
			changed, panicked := foldChanges(text)
			// An opaque operand is reported even when the literal half is
			// clean. THIS IS THE HOLE THE FIRST DRAFT HAD, and it was not
			// hypothetical: artifactcheck builds nine of its stdout branches
			// through a lit(name, body) helper, so `printing`, `verified`,
			// `running it` and the rest reach MustCompile as a PARAMETER.
			// The census read `(?i)` and nothing else and called the file
			// two-exposed. A scan that cannot see a pattern must say so
			// rather than certify it — the same false-NEGATIVE rule the
			// escape census states about stripping comments.
			if !changed && !panicked && len(opaque) == 0 {
				return true
			}
			sites = append(sites, site{
				rel:     rel,
				line:    fset.Position(call.Pos()).Line,
				wrapped: wrapsWithPyFoldI(call),
				inClass: panicked,
				opaque:  opaque,
				text:    text,
			})
			return true
		})
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}

	unwrapped := map[string]int{}
	classCases := map[string]int{}
	at := map[string][]string{}
	for _, s := range sites {
		if s.wrapped {
			continue
		}
		unwrapped[s.rel]++
		if s.inClass {
			classCases[s.rel]++
		}
		// The line number is the whole point of failing here rather than
		// in a report: the reader should not have to re-run the census by
		// hand to find out which pattern is meant.
		mark := ":" + itoa(s.line)
		if s.inClass {
			mark += " (i inside a character class)"
		}
		if len(s.opaque) > 0 {
			mark += " (pattern built from " + strings.Join(s.opaque, "/") +
				" — the scan cannot read it)"
		}
		at[s.rel] = append(at[s.rel], mark)
	}

	for _, rel := range sortedKeys(unwrapped) {
		entry, ok := allowed[rel]
		if !ok {
			extra := ""
			if classCases[rel] > 0 {
				extra = "\n\t" + itoa(classCases[rel]) + " of them put a bare " +
					"i/I inside a character class, where PyFoldI cannot help " +
					"(a class holds no group). Those need the class body " +
					"rewritten by hand, or an entry here saying why the " +
					"difference cannot matter."
			}
			t.Errorf("%s: %d (?i) pattern(s) carry a literal i/I and are not "+
				"wrapped in pytext.PyFoldI, with no entry in this test's "+
				"allowlist.\n"+
				"\tCPython's re.IGNORECASE also matches U+0130 and U+0131 "+
				"there; Go's (?i) does not.\n"+
				"\tWrap the pattern in pytext.PyFoldI, or add an entry here "+
				"saying WHY the divergence cannot matter — the entry is the "+
				"review.%s",
				rel, unwrapped[rel], extra+"\n\tat "+
					strings.Join(at[rel], ", "))
			continue
		}
		if entry.count != unwrapped[rel] {
			t.Errorf("%s has %d exposed unwrapped (?i) pattern(s) (at %s), "+
				"allowlisted for %d.\n\ton record: %s",
				rel, unwrapped[rel], strings.Join(at[rel], ", "),
				entry.count, entry.reason)
		}
	}
	for rel, entry := range allowed {
		if unwrapped[rel] == 0 {
			t.Errorf("%s is allowlisted for %d exposed (?i) pattern(s) and has "+
				"none. The entry describes a divergence that no longer "+
				"exists, which is a lie about this codebase — delete it.\n"+
				"\ton record: %s", rel, entry.count, entry.reason)
		}
	}

	// L1: a scan that found nothing to scan passes vacuously, and this
	// project keeps re-finding that. The floors sit under what this scan
	// actually measures on 2026-08-27 — 140 production files, 81
	// regexp.MustCompile calls, 32 of them case-insensitive, 22 carrying a
	// literal i/I — so ordinary churn does not trip them.
	//
	// That "32" was written as 40 in the first draft, copied from the grep
	// this test exists to replace, and the test's own t.Logf is what caught
	// it. L28 inside the file written for L28: count every number a comment
	// states, including in the comment explaining why the counts were wrong.
	if files < 100 || calls < 55 || insensitive < 25 {
		t.Fatalf("scan covered %d production files / %d regexp.MustCompile "+
			"calls / %d case-insensitive; too few to be measuring anything",
			files, calls, insensitive)
	}
	wrapped, unreadable := 0, 0
	for _, s := range sites {
		if s.wrapped {
			wrapped++
		}
		if s.text == "" {
			// Reported for being unreadable, not for folding an i, and
			// counted apart so the line below does not claim a literal it
			// never saw.
			unreadable++
		}
	}
	t.Logf("%d production files, %d regexp.MustCompile calls, %d "+
		"case-insensitive, %d of those carry a literal i/I; %d more are "+
		"unreadable (%d of %d reported sites wrapped)",
		files, calls, insensitive, len(sites)-unreadable, unreadable,
		wrapped, len(sites))
}

// caseInsensitive reports whether the pattern turns the `i` flag on at
// top level: `(?i)`, `(?is)`, `(?si)`. A scoped `(?i:` would count too,
// though this port does not use one. `(?-i:` is the OFF form pytext's own
// classes use and must not match — hence the explicit minus check rather
// than a substring search for "i".
func caseInsensitive(pattern string) bool {
	for i := 0; i+2 < len(pattern); i++ {
		if pattern[i] != '(' || pattern[i+1] != '?' {
			continue
		}
		j := i + 2
		on := true
		for ; j < len(pattern); j++ {
			c := pattern[j]
			if c == '-' {
				on = false
				continue
			}
			if c == 'i' && on {
				return true
			}
			if c == ')' || c == ':' || !isFlagLetter(c) {
				break
			}
		}
	}
	return false
}

func isFlagLetter(c byte) bool {
	switch c {
	case 'i', 'm', 's', 'U':
		return true
	}
	return false
}

// foldChanges asks the REMEDY whether this pattern is exposed. A panic is
// not a failure of the census: PyFoldI panics on a bare i inside a
// character class, which is exactly the exposed-and-unfixable case, and
// it must be reported rather than crash the scan.
func foldChanges(pattern string) (changed, panicked bool) {
	defer func() {
		if r := recover(); r != nil {
			changed, panicked = false, true
		}
	}()
	return PyFoldI(pattern) != pattern, false
}

// wrapsWithPyFoldI walks the call's arguments for a pytext.PyFoldI (or,
// inside this package, a bare PyFoldI) application. Structural rather
// than textual: a comment mentioning PyFoldI must not count as a fix.
func wrapsWithPyFoldI(call *ast.CallExpr) bool {
	found := false
	for _, a := range call.Args {
		ast.Inspect(a, func(n ast.Node) bool {
			inner, ok := n.(*ast.CallExpr)
			if !ok {
				return true
			}
			switch fn := inner.Fun.(type) {
			case *ast.SelectorExpr:
				if pkg, ok := fn.X.(*ast.Ident); ok &&
					pkg.Name == "pytext" && fn.Sel.Name == "PyFoldI" {
					found = true
				}
			case *ast.Ident:
				if fn.Name == "PyFoldI" {
					found = true
				}
			}
			return !found
		})
	}
	return found
}

// collectOpaque records every operand of a pattern expression that the
// literal scan cannot read: an identifier, a parameter, a function result.
//
// `pytext.X` is deliberately NOT opaque. Every exported class in that
// package is spelled `(?-i:[...])` — the fold flag turned off around the
// body — so splicing one into a `(?i)` pattern contributes no foldable
// letter by construction. That is the whole reason the helpers are
// written that way, and it is what lets this scan ignore them instead of
// drowning in them.
func collectOpaque(e ast.Expr, out *[]string) {
	switch t := e.(type) {
	case *ast.BasicLit:
		return
	case *ast.BinaryExpr:
		collectOpaque(t.X, out)
		collectOpaque(t.Y, out)
	case *ast.ParenExpr:
		collectOpaque(t.X, out)
	case *ast.CallExpr:
		if sel, ok := t.Fun.(*ast.SelectorExpr); ok {
			if pkg, ok := sel.X.(*ast.Ident); ok && pkg.Name == "pytext" {
				// A pytext function's own arguments still get read.
				for _, a := range t.Args {
					collectOpaque(a, out)
				}
				return
			}
		}
		*out = append(*out, exprName(t.Fun)+"()")
	case *ast.SelectorExpr:
		if pkg, ok := t.X.(*ast.Ident); ok && pkg.Name == "pytext" {
			return
		}
		*out = append(*out, exprName(t))
	case *ast.Ident:
		*out = append(*out, t.Name)
	default:
		*out = append(*out, "?")
	}
}

func exprName(e ast.Expr) string {
	switch t := e.(type) {
	case *ast.Ident:
		return t.Name
	case *ast.SelectorExpr:
		return exprName(t.X) + "." + t.Sel.Name
	}
	return "?"
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b []byte
	for n > 0 {
		b = append([]byte{byte('0' + n%10)}, b...)
		n /= 10
	}
	return string(b)
}
