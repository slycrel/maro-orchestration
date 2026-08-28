package claimverify

import "strconv"

// The filesystem scenarios: root inference, the bounded tree walk, the
// symbol index, and the two verifiers built on them.
//
// Every scenario that does not deliberately exercise the `llm` seam sets
// LlmImportFails, because the real adapter suite is on the probe's
// sys.path and importing it would be a side effect no differential wants.

// ti indexes the scenario's own fixture directory.
func ti(name string, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "tree_index", Tree: tree,
		LlmImportFails: true}
}

// si builds the symbol index over the fixture directory.
func si(name string, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "symbol_index", Tree: tree,
		LlmImportFails: true}
}

func vf(name, text string, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "verify_files", Text: text, Tree: tree,
		LlmImportFails: true}
}

func vs(name, text string, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "verify_symbols", Text: text, Tree: tree,
		LlmImportFails: true}
}

// ir runs root inference from the fixture directory with the `llm` import
// unavailable; irLLM installs the seam instead.
func ir(name string, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "infer_root", Tree: tree,
		LlmImportFails: true}
}

func an(name, text string, onlyIf, symbols bool, tree ...cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "annotate", Text: text, Tree: tree,
		OnlyIfHallucinations: onlyIf, CheckSymbols: symbols,
		LlmImportFails: true}
}

// chainTree is a strictly nested chain of directories, one file each.
//
// The 2000-directory cap is the one place the WALK ORDER is observable,
// and a fixture of 2001 sibling directories could not prove anything:
// `os.walk` yields in scandir order, which is the filesystem's business
// and not the same twice. A chain has exactly one order.
func capped(name string, cap int, tree []cvEntry) cvSpec {
	return cvSpec{Name: name, Kind: "tree_index", Tree: tree,
		CapOverride: cap, CapOverrideSet: true, LlmImportFails: true}
}

func chainTree(depth int) []cvEntry {
	out := []cvEntry{}
	dir := ""
	for i := 0; i < depth; i++ {
		out = append(out, f(dir+"f"+strconv.Itoa(i)+".txt", "x"))
		if dir == "" {
			dir = "d/"
		} else {
			dir += "d/"
		}
	}
	return out
}

func filesystemScenarios() []cvSpec {
	scs := []cvSpec{
		// --- _infer_project_root: the run-scoped cwd comes first -------
		{Name: "the-run-cwd-wins-over-the-walk", Kind: "infer_root",
			RunCwd: "proj",
			Tree:   []cvEntry{d("proj"), f("pyproject.toml", "")}},
		{Name: "the-run-cwd-is-not-a-directory", Kind: "infer_root",
			RunCwd: "proj",
			Tree:   []cvEntry{f("proj", "x"), f("pyproject.toml", "")}},
		{Name: "the-run-cwd-does-not-exist", Kind: "infer_root",
			RunCwd: "nowhere",
			Tree:   []cvEntry{f("pyproject.toml", "")}},
		{Name: "the-run-cwd-is-none", Kind: "infer_root",
			Tree: []cvEntry{f("pyproject.toml", "")}},
		{Name: "the-run-cwd-call-raises", Kind: "infer_root",
			RunCwdRaises: true,
			Tree:         []cvEntry{f("pyproject.toml", "")}},
		{Name: "the-run-cwd-is-a-symlink-to-a-directory", Kind: "infer_root",
			RunCwd: "link",
			Tree: []cvEntry{d("proj"), ln("link", "proj"),
				f("pyproject.toml", "")}},
		{Name: "the-run-cwd-is-a-dangling-symlink", Kind: "infer_root",
			RunCwd: "link",
			Tree:   []cvEntry{ln("link", "gone"), f("pyproject.toml", "")}},

		// --- _infer_project_root: the upward walk ---------------------
		ir("the-walk-finds-pyproject-at-the-base", f("pyproject.toml", "")),
		ir("the-walk-finds-src-at-the-base", d("src")),
		{Name: "the-walk-starts-at-the-cwd-itself", Kind: "infer_root",
			Cwd: "a/b", LlmImportFails: true,
			Tree: []cvEntry{d("a/b"), f("a/b/pyproject.toml", "")}},
		{Name: "the-walk-stops-at-the-nearest-ancestor", Kind: "infer_root",
			Cwd: "a/b", LlmImportFails: true,
			Tree: []cvEntry{d("a/b"), f("a/pyproject.toml", ""),
				f("pyproject.toml", "")}},
		// `Path.exists()` asks nothing about the TYPE, so a file named
		// `src` and a directory named `pyproject.toml` both answer yes.
		// The cwd is checked BEFORE its parents, so a marker in both
		// answers with the nearer one. Without the cwd in the list the
		// walk still terminates — on the parent — which is why the
		// answer, not the termination, is what this pins.
		{Name: "the-walk-prefers-the-cwd-over-its-parent", Kind: "infer_root",
			Cwd: "a/b", LlmImportFails: true,
			Tree: []cvEntry{d("a/b"), f("a/b/pyproject.toml", ""),
				f("a/pyproject.toml", "")}},
		// Either marker alone terminates the walk, so each needs an
		// ancestor to be found IN — a marker in the cwd is
		// indistinguishable from the `return cwd` fallback.
		{Name: "the-walk-finds-src-in-an-ancestor", Kind: "infer_root",
			Cwd: "a/b", LlmImportFails: true,
			Tree: []cvEntry{d("a/b"), d("src")}},
		ir("a-src-that-is-a-file", f("src", "not a package")),
		ir("a-pyproject-that-is-a-directory", d("pyproject.toml")),
		// …but it FOLLOWS symlinks, so a dangling one answers no.
		ir("a-src-that-is-a-dangling-symlink", ln("src", "gone"),
			f("pyproject.toml", "")),
		// Nothing anywhere: the walk runs off the top of the fixture and
		// answers with whatever ancestor it finds, which is why the
		// record reports a RELATIVE path.
		ir("nothing-to-infer-from", f("readme.txt", "")),

		// --- _tree_index ---------------------------------------------
		ti("an-empty-tree"),
		ti("a-flat-tree", f("a.py", ""), f("b.md", "")),
		ti("a-nested-tree", f("a.py", ""), f("src/b.py", ""),
			f("src/deep/c.py", "")),
		ti("a-tree-with-an-empty-directory", d("empty"), f("a.py", "")),
		ti("a-git-directory-is-skipped", f(".git/config", ""), f("a.py", "")),
		ti("a-pycache-directory-is-skipped", f("__pycache__/a.pyc", ""),
			f("a.py", "")),
		ti("a-node-modules-directory-is-skipped",
			f("node_modules/x/p.json", ""), f("a.py", "")),
		ti("a-venv-directory-is-skipped", f(".venv/lib/a.py", ""),
			f("a.py", "")),
		ti("a-tox-directory-is-skipped", f(".tox/a.py", ""), f("a.py", "")),
		ti("a-skipped-directory-nested-deep", f("src/.git/config", ""),
			f("src/a.py", "")),
		// A symlink to a directory is classified as a DIRECTORY (is_dir
		// follows), and `followlinks=False` then declines to descend — so
		// it contributes no name at all, neither its own nor its target's.
		ti("a-symlink-to-a-directory", d("real"), f("real/inside.py", ""),
			ln("link", "real")),
		// A symlink to a FILE is a file, and gets its own basename.
		ti("a-symlink-to-a-file", f("real.py", ""), ln("alias.py", "real.py")),
		// A dangling symlink stats as a failure, `is_dir()` answers False,
		// and it lands among the filenames.
		ti("a-dangling-symlink", ln("gone.py", "nowhere"), f("a.py", "")),
		ti("a-file-with-a-space-in-its-name", f("a file.py", "")),
		ti("a-non-ascii-filename", f("café.py", "")),
		// The cap is on DIRECTORIES VISITED, and a chain is the only
		// shape whose walk ORDER is forced — so the cap itself is driven
		// down rather than the fixture built up. See indexMaxDirs.
		capped("the-cap-cuts-a-chain-short", 3, chainTree(6)),
		capped("a-cap-that-admits-only-the-root", 1, chainTree(6)),
		capped("a-cap-of-zero-admits-nothing", 0, chainTree(6)),
		capped("a-cap-larger-than-the-tree", 99, chainTree(4)),
		// A skipped directory is pruned BEFORE it is walked, so it never
		// spends a slot against the cap.
		capped("a-skipped-directory-costs-no-slot", 2,
			[]cvEntry{f(".git/a.py", ""), f("d/b.py", ""), f("c.py", "")}),

		// --- _tree_index: the roots that yield nothing ----------------
		{Name: "an-index-of-a-missing-directory", Kind: "tree_index",
			Root: "gone", LlmImportFails: true,
			Tree: []cvEntry{f("a.py", "")}},
		{Name: "an-index-rooted-at-a-file", Kind: "tree_index",
			Root: "a.py", LlmImportFails: true,
			Tree: []cvEntry{f("a.py", "")}},

		// --- _build_symbol_index -------------------------------------
		si("a-symbol-index-of-nothing"),
		si("a-def-in-src", f("src/a.py", "def alpha_one():\n    pass\n")),
		si("a-class-in-src", f("src/a.py", "class Widget:\n    pass\n")),
		si("an-async-def", f("src/a.py", "async def beta_two():\n    pass\n")),
		si("an-indented-def", f("src/a.py", "class W:\n    def gamma_three(self):\n        pass\n")),
		si("a-def-with-no-parentheses", f("src/a.py", "def delta_four\n")),
		si("a-def-in-tests", f("tests/t.py", "def test_thing():\n    pass\n")),
		si("a-def-at-the-project-root", f("top.py", "def epsilon_five():\n    pass\n")),
		si("a-def-in-a-non-python-file", f("src/a.txt", "def zeta_six():\n    pass\n")),
		si("a-def-in-a-nested-package", f("src/pkg/deep/a.py", "def eta_seven():\n    pass\n")),
		si("a-line-that-is-not-a-declaration", f("src/a.py", "x = def_thing\n")),
		si("a-def-that-is-not-at-the-line-start", f("src/a.py", "x = 1; def theta_eight():\n")),
		si("a-name-that-is-one-character", f("src/a.py", "def a():\n")),
		si("a-name-starting-with-a-digit", f("src/a.py", "def 9bad():\n")),
		// `errors="ignore"` — the file is read, the bad bytes vanish, and
		// the declarations around them still count.
		// The bad byte inside the NAME: `errors="ignore"` drops it and the
		// two halves close up, so the symbol is `iotau` and not `io`.
		si("a-def-whose-name-carries-a-bad-byte",
			cvEntry{Path: "src/a.py", Kind: "file",
				Data: "ZGVmIGlv/3RhdSgpOgogICAgcGFzcwo="}),
		// `str.splitlines` breaks on a form feed and `split("\n")` does
		// not, and the declaration is only at a line start under the
		// first reading.
		si("a-form-feed-inside-a-line", f("src/a.py", "x\x0cdef sigma_16():\n")),
		si("a-file-with-invalid-utf8",
			cvEntry{Path: "src/a.py", Kind: "file",
				Data: "ZGVmIGlvdGFfbmluZSgpOgogICAg/wogICAgcGFzcwo="}),
		// A DIRECTORY named `*.py` is yielded by rglob and then raises
		// IsADirectoryError, which is an OSError, which is caught.
		si("a-directory-named-like-a-module", d("src/fake.py"),
			f("src/real.py", "def kappa_ten():\n")),
		// rglob does not descend into a symlinked directory, but it does
		// yield a symlink whose own NAME matches — and reading it follows.
		si("a-symlinked-python-file", f("src/real.py", "def lambda_11():\n"),
			ln("src/alias.py", "real.py")),
		si("a-symlinked-package-directory", d("real"),
			f("real/inside.py", "def mu_twelve():\n"), ln("src", "real")),
		si("a-symlinked-subdirectory-of-src", d("src"), d("other"),
			f("other/inside.py", "def nu_thirteen():\n"), ln("src/sub", "../other")),
		// A symlinked search dir whose target is INSIDE the root proves
		// nothing: the root pass rglobs the target directly. These two
		// point OUT of the project, which is the only place the follow
		// and the no-follow disagree.
		{Name: "a-src-symlinked-outside-the-project", Kind: "symbol_index",
			Root: "proj", LlmImportFails: true,
			Tree: []cvEntry{d("proj"), d("away"),
				f("away/x.py", "def upsilon_17():\n"),
				ln("proj/src", "../away")}},
		{Name: "a-tests-symlinked-outside-the-project", Kind: "symbol_index",
			Root: "proj", LlmImportFails: true,
			Tree: []cvEntry{d("proj"), d("away"),
				f("away/y.py", "def chi_19():\n"),
				ln("proj/tests", "../away")}},
		{Name: "a-symlinked-subdirectory-pointing-outside",
			Kind: "symbol_index", Root: "proj", LlmImportFails: true,
			Tree: []cvEntry{d("proj/src"), d("away"),
				f("away/x.py", "def phi_18():\n"),
				ln("proj/src/sub", "../../away")}},
		{Name: "a-symbol-index-of-a-missing-root", Kind: "symbol_index",
			Root: "gone", LlmImportFails: true,
			Tree: []cvEntry{f("src/a.py", "def xi_fourteen():\n")}},

		// --- verify_file_claims: the direct hit -----------------------
		vf("a-claim-that-exists", "we wrote handle.py", f("handle.py", "")),
		vf("a-claim-that-does-not-exist", "we wrote handle.py", f("other.py", "")),
		vf("a-prefixed-claim-that-exists", "we wrote src/handle.py",
			f("src/handle.py", "")),
		vf("no-claims-at-all-to-verify", "nothing here", f("a.py", "")),
		vf("a-claim-that-is-a-directory", "we wrote handle.py", d("handle.py")),

		// --- the bare-name fallback ----------------------------------
		vf("a-bare-claim-found-in-a-subdirectory", "we wrote cart.py",
			f("output/repro/cart.py", "")),
		vf("a-bare-claim-found-nowhere", "we wrote cart.py",
			f("output/repro/other.py", "")),
		vf("a-bare-claim-hidden-inside-a-skipped-directory", "we wrote cart.py",
			f(".git/cart.py", "")),
		// The bare fallback is NOT recorded in suffix_matched — only the
		// relative-path one is, and the distinction is the point.
		vf("a-bare-claim-is-not-a-suffix-match", "we wrote cart.py",
			f("a/b/cart.py", "")),

		// --- the unique-suffix fallback ------------------------------
		vf("a-relative-claim-matched-once", "we wrote tests/test_a.py",
			f("pkg/tests/test_a.py", "")),
		vf("a-relative-claim-matched-twice", "we wrote docs/report.md",
			f("pkg/docs/report.md", ""), f("vendor/x/docs/report.md", "")),
		vf("a-relative-claim-matched-nowhere", "we wrote docs/report.md",
			f("pkg/docs/other.md", "")),
		// The suffix carries a leading slash, so a longer directory name
		// that merely ENDS in the claimed one does not match.
		vf("a-suffix-that-is-not-on-a-path-boundary", "we wrote docs/a.md",
			f("xdocs/a.md", "")),
		// `rp == norm` is the other half of the test, and a DANGLING
		// symlink is how it becomes reachable: the walk lists the name,
		// `exists()` refuses to follow it.
		vf("a-relative-claim-whose-target-is-dangling", "we wrote docs/a.md",
			ln("docs/a.md", "nowhere")),
		vf("a-claim-that-normalises-above-the-root", "we wrote src/../../a.py",
			f("a.py", "")),
		vf("a-claim-that-normalises-back-into-the-tree", "we wrote src/../a.py",
			f("a.py", "")),

		// --- several claims at once ----------------------------------
		vf("a-hit-and-a-miss", "we wrote a.py and b.py", f("a.py", "")),
		vf("every-claim-hits-so-the-tree-is-never-walked",
			"we wrote a.py and b.py", f("a.py", ""), f("b.py", "")),
		vf("the-same-claim-twice", "we wrote a.py and a.py",
			f("out/a.py", "")),
		vf("a-verified-and-an-unresolvable-and-a-not-found",
			"a.py then docs/report.md then gone.py",
			f("a.py", ""), f("p/docs/report.md", ""), f("q/docs/report.md", "")),

		// --- verify_file_claims with an inferred root ----------------
		{Name: "a-claim-verified-against-an-inferred-root",
			Kind: "verify_files", Text: "we wrote handle.py",
			RootIsNone: true, LlmImportFails: true,
			Tree: []cvEntry{f("pyproject.toml", ""), f("handle.py", "")}},
		{Name: "a-claim-verified-against-the-run-cwd",
			Kind: "verify_files", Text: "we wrote handle.py",
			RootIsNone: true, RunCwd: "proj", Cwd: "elsewhere",
			Tree: []cvEntry{d("elsewhere"), f("proj/handle.py", ""),
				f("handle.py", "")}},

		// --- verify_symbol_claims ------------------------------------
		vs("a-symbol-that-exists", "call `alpha_one` now",
			f("src/a.py", "def alpha_one():\n")),
		vs("a-symbol-that-does-not-exist", "call `alpha_one` now",
			f("src/a.py", "def beta_two():\n")),
		vs("no-symbol-claims-at-all", "nothing here",
			f("src/a.py", "def alpha_one():\n")),
		vs("a-symbol-verified-and-one-not", "call `alpha_one` and `beta_two`",
			f("src/a.py", "def alpha_one():\n")),
		vs("a-symbol-declared-as-a-class", "the Widget class is fine",
			f("src/a.py", "class Widget:\n")),
		{Name: "a-symbol-verified-against-an-inferred-root",
			Kind: "verify_symbols", Text: "call `alpha_one` now",
			RootIsNone: true, LlmImportFails: true,
			Tree: []cvEntry{f("pyproject.toml", ""),
				f("src/a.py", "def alpha_one():\n")}},

		// --- annotate_result -----------------------------------------
		an("a-clean-result-is-left-alone", "we wrote a.py", true, true,
			f("a.py", "")),
		an("a-hallucinated-file-is-annotated", "we wrote gone.py", true, true,
			f("a.py", "")),
		an("a-hallucinated-symbol-is-annotated", "call `alpha_one` now",
			true, true, f("src/a.py", "def beta_two():\n")),
		an("a-hallucinated-symbol-with-symbols-off", "call `alpha_one` now",
			true, false, f("src/a.py", "def beta_two():\n")),
		an("both-halves-hallucinated", "we wrote gone.py and call `alpha_one`",
			true, true, f("src/a.py", "def beta_two():\n")),
		// A hallucination AND a verified claim under only_if=True: the
		// VERIFIED line is suppressed, and that suppression is the only
		// thing this scenario is about.
		an("an-annotated-result-that-also-verified-one",
			"we wrote a.py and gone.py", true, true, f("a.py", "")),
		an("a-clean-result-reported-anyway", "we wrote a.py", false, true,
			f("a.py", "")),
		// only_if_hallucinations=False also turns ON the VERIFIED line,
		// and it is the one place a list is truncated.
		an("more-than-five-verified", "a.py b.py c.py d.py e.py f.py g.py",
			false, true, f("a.py", ""), f("b.py", ""), f("c.py", ""),
			f("d.py", ""), f("e.py", ""), f("f.py", ""), f("g.py", "")),
		an("exactly-five-verified", "a.py b.py c.py d.py e.py",
			false, true, f("a.py", ""), f("b.py", ""), f("c.py", ""),
			f("d.py", ""), f("e.py", "")),
		// Reported-anyway with nothing to report: `parts` is empty and the
		// text comes back unchanged through the SECOND early return.
		an("nothing-at-all-reported-anyway", "no claims here", false, true,
			f("a.py", "")),
		an("unresolvable-alone-is-not-worth-a-note", "we wrote docs/a.md",
			false, true, f("p/docs/a.md", ""), f("q/docs/a.md", "")),
	}
	return scs
}

// cvScenarios is the whole differential corpus.
func cvScenarios() []cvSpec {
	out := extractionScenarios()
	out = append(out, filesystemScenarios()...)
	// A nil Tree marshals as JSON `null`, and the probe iterates it
	// unconditionally; the scenarios that carry no tree still name a
	// directory, so the empty slice is the honest spelling.
	for i := range out {
		if out[i].Tree == nil {
			out[i].Tree = []cvEntry{}
		}
	}
	return out
}
