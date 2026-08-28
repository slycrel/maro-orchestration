package claimverify

// The extraction scenarios: the two hand-written matchers, the three
// symbol patterns, the synthesis keyword scan, and the two report
// renderings. None of these touch the filesystem.

func fp(name, text string) cvSpec { return cvSpec{Name: name, Kind: "file_path_re", Text: text} }
func fc(name, text string) cvSpec { return cvSpec{Name: name, Kind: "file_claims", Text: text} }
func sy(name, text string) cvSpec { return cvSpec{Name: name, Kind: "symbol_claims", Text: text} }
func sn(name, text string) cvSpec { return cvSpec{Name: name, Kind: "synthesis", Text: text} }

func cs(name, report string) cvSpec {
	return cvSpec{Name: name, Kind: "claim_summary", Report: report}
}
func ss(name, report string) cvSpec {
	return cvSpec{Name: name, Kind: "symbol_summary", Report: report}
}

// jl renders a Go string slice as a JSON array literal for a report
// fixture. Written out rather than %q'd because a report fixture is read
// as data, and json.Marshal is the only escaper that agrees with
// Python's json.loads on every character.
func jl(ss ...string) string {
	blob := "["
	for i, s := range ss {
		if i > 0 {
			blob += ","
		}
		blob += jq(s)
	}
	return blob + "]"
}

func jq(s string) string {
	out := []byte{'"'}
	for _, c := range []byte(s) {
		switch c {
		case '"':
			out = append(out, '\\', '"')
		case '\\':
			out = append(out, '\\', '\\')
		case '\n':
			out = append(out, '\\', 'n')
		case '\t':
			out = append(out, '\\', 't')
		default:
			if c < 0x20 {
				const hex = "0123456789abcdef"
				out = append(out, '\\', 'u', '0', '0',
					hex[c>>4], hex[c&0xf])
				continue
			}
			out = append(out, c)
		}
	}
	return string(append(out, '"'))
}

func claimRep(raw, ver, nf, unres, sm string) string {
	return `{"raw_claims":` + raw + `,"verified":` + ver +
		`,"not_found":` + nf + `,"unresolvable":` + unres +
		`,"suffix_matched":` + sm + `}`
}

func symRep(raw, ver, nf string) string {
	return `{"raw_claims":` + raw + `,"verified":` + ver +
		`,"not_found":` + nf + `}`
}

func extractionScenarios() []cvSpec {
	return []cvSpec{
		// --- alternative one: the known directory prefixes -------------
		fp("a-src-prefixed-path", "wrote src/handle.py today"),
		fp("the-tests-prefix-plural", "see tests/test_a.py"),
		fp("the-test-prefix-singular", "see test/one.py"),
		fp("the-docs-prefix-plural", "see docs/INDEX.md"),
		fp("the-doc-prefix-singular", "see doc/one.md"),
		fp("the-lat-md-prefix", "see lat.md/node.md for it"),
		fp("the-scripts-prefix-plural", "see scripts/land.sh"),
		fp("the-script-prefix-singular", "see script/land.sh"),
		fp("the-personas-prefix-plural", "see personas/poe.md"),
		fp("the-persona-prefix-singular", "see persona/poe.md"),
		fp("the-memory-prefix", "see memory/lessons.json"),
		fp("the-deploy-prefix", "see deploy/unit.yml"),
		fp("a-prefix-that-is-not-listed", "see lib/thing.toml"),
		fp("a-prefixed-toml", "see src/pyproject.toml"),
		fp("a-prefixed-cfg", "see src/setup.cfg"),
		fp("a-prefixed-ini", "see src/tox.ini"),
		fp("a-prefixed-yaml", "see deploy/a.yaml"),
		fp("a-prefixed-yml", "see deploy/a.yml"),
		fp("a-prefixed-txt", "see docs/a.txt"),
		fp("a-prefixed-json", "see memory/a.json"),
		fp("a-prefixed-sh", "see scripts/a.sh"),
		fp("a-prefixed-extension-not-in-the-list", "see src/main.rs here"),
		fp("a-deep-prefixed-path", "see src/a/b/c/d.py"),
		fp("a-prefixed-path-with-a-doubled-slash", "see src//a.py"),
		fp("a-prefixed-path-with-dashes", "see src/my-file.py"),
		// The prefix alternation BACKTRACKS: `tests` fails on the slash,
		// `test` succeeds. A port that stops at the first prefix whose
		// letters match loses this.
		fp("the-plural-prefix-loses-to-the-singular", "see test/x.py"),
		// `scripts` matches before `script`, so a directory literally
		// named `scripts` never reaches the singular alternative.
		fp("a-prefix-that-is-a-strict-extension", "see scripts/x.sh"),
		fp("a-prefix-with-no-slash", "see srcthing.py"),
		fp("a-prefix-followed-by-a-word", "see srcs/a.py"),
		fp("an-uppercase-prefix", "see SRC/a.py"),

		// --- alternatives two and three: the bare names ---------------
		fp("a-bare-py", "we edited handle.py just now"),
		fp("a-bare-md", "we edited NOTES.md just now"),
		fp("a-bare-md-lowercase", "we edited notes.md just now"),
		fp("an-uppercase-extension", "we edited HANDLE.PY just now"),
		fp("a-bare-name-that-is-only-an-extension", "the .py file"),
		fp("a-leading-dot-name", "the .hidden.py file"),
		fp("an-underscore-name", "the _private.py file"),
		fp("a-name-with-digits", "the test_2.py file"),
		fp("a-bare-toml-is-not-a-claim", "the pyproject.toml file"),

		// --- the two boundaries ---------------------------------------
		fp("a-path-in-backticks", "we edited `handle.py` today"),
		fp("a-path-in-double-quotes", `we edited "handle.py" today`),
		fp("a-path-in-single-quotes", "we edited 'handle.py' today"),
		fp("a-path-in-parentheses", "we edited (handle.py) today"),
		fp("an-open-paren-on-the-left-only", "we edited (handle.py today"),
		fp("a-close-paren-on-the-right-only", "we edited handle.py) today"),
		// The paren is the ONE character the two boundaries disagree on:
		// open blocks on the left, close blocks on the right.
		fp("a-close-paren-on-the-left", "we edited )handle.py today"),
		fp("an-open-paren-on-the-right", "we edited handle.py( today"),
		fp("a-word-character-on-the-left", "we edited xsrc/handle.py today"),
		fp("a-word-character-on-the-right", "we edited handle.pyx today"),
		fp("a-trailing-period", "we edited src/handle.py."),
		fp("a-trailing-comma", "src/handle.py, src/other.py"),
		fp("a-path-at-the-very-start", "handle.py was edited"),
		fp("a-path-at-the-very-end", "we edited handle.py"),
		fp("two-adjacent-paths", "src/a.py src/b.py"),
		fp("the-empty-text", ""),
		fp("text-with-no-path-at-all", "nothing to see here"),

		// --- the greedy run and where its dot lands -------------------
		// `re` releases characters from the RIGHT, so the LAST dot that
		// carries a valid extension wins, not the first.
		fp("a-doubled-extension-py-then-md", "see notes.py.md here"),
		fp("a-doubled-extension-md-then-py", "see notes.md.py here"),
		fp("a-prefixed-doubled-extension", "see src/a.py.rs here"),
		fp("a-prefixed-path-whose-tail-fails", "see src/main.rs.txt here"),
		fp("a-dotted-name-with-many-dots", "see a.b.c.py here"),
		fp("a-run-that-ends-on-a-dot", "see handle.py.. here"),
		fp("a-run-of-only-dots", "see ... here"),
		fp("a-single-character-before-the-dot", "see a.py here"),
		fp("nothing-before-the-dot", "see .py here"),

		// --- unicode: Python's \w is not ASCII -----------------------
		fp("a-non-ascii-name", "we edited café.py today"),
		fp("a-non-ascii-boundary-on-the-left", "we edited écafé.py today"),
		fp("a-non-ascii-digit", "we edited a٣.py today"),
		fp("a-combining-mark-in-the-name", "we edited café.py today"),

		// --- extract_file_claims: strip, skip, dedup ------------------
		fc("a-claim-stripped-of-punctuation", "see handle.py; and more"),
		fc("a-claim-stripped-of-a-trailing-colon", "see handle.py: here"),
		fc("a-skip-name-stdlib", "we changed os.py and sys.py"),
		fc("a-skip-name-readme", "we changed README.md"),
		fc("a-skip-name-changelog", "we changed CHANGELOG.md"),
		fc("a-skip-name-license", "we changed LICENSE.md"),
		// _SKIP_NAMES is checked against the BASENAME, so a prefixed
		// path with a skipped name is skipped too.
		fc("a-skip-name-under-a-prefix", "we changed src/json.py"),
		// _SKIP_PATTERNS is checked against the WHOLE claim, so the same
		// name under a directory survives.
		fc("a-skip-pattern-bare", "we changed setup.py"),
		fc("a-skip-pattern-under-a-prefix", "we changed src/setup.py"),
		fc("a-skip-pattern-manage-py", "we changed manage.py"),
		fc("a-duplicate-claim", "handle.py and handle.py again"),
		fc("two-claims-that-differ-only-in-prefix", "src/a.py and a.py"),
		fc("a-claim-that-strips-to-a-skip-name", "see `os.py` there"),
		fc("claims-in-first-appearance-order", "z.py then a.py then m.py"),
		fc("no-claims-at-all", "nothing here"),

		// --- the symbol patterns -------------------------------------
		sy("a-backticked-name", "call `apply_suggestion` now"),
		sy("a-backticked-call", "call `apply_suggestion()` now"),
		sy("a-backticked-call-with-arguments", "call `apply_suggestion(a, b)` now"),
		sy("a-backticked-call-with-long-arguments",
			"call `apply_suggestion("+"x, "+"yyyyyyyyyy, zzzzzzzzzz, wwwwwwwwww, vvvvvvvvvv)` now"),
		sy("a-backticked-name-of-one-character", "call `a` now"),
		sy("a-backticked-name-of-four-characters", "call `abcd` now"),
		sy("a-backticked-name-of-five-characters", "call `abcde` now"),
		sy("a-backticked-dunder", "call `__init__` now"),
		sy("a-backticked-single-underscore", "call `_verify_post_apply` now"),
		sy("a-backticked-non-ascii-first-character", "call `énorme_thing` now"),
		sy("a-backticked-non-ascii-tail", "call `café_thing` now"),
		sy("a-def-declaration", "we saw def run_agent_loop here"),
		sy("a-class-declaration", "we saw class Director here"),
		sy("a-def-inside-a-word", "we saw undef run_agent_loop here"),
		sy("an-async-def-declaration", "we saw async def run_agent_loop here"),
		sy("a-def-with-no-space", "we saw defrun_agent_loop here"),
		sy("the-context-suffix-function", "the scan_outcomes function is fine"),
		sy("the-context-suffix-method", "the scan_outcomes method is fine"),
		sy("the-context-suffix-class", "the scan_outcomes class is fine"),
		sy("the-context-suffix-property", "the scan_outcomes property is fine"),
		sy("the-context-suffix-attribute", "the scan_outcomes attribute is fine"),
		sy("the-context-suffix-with-empty-parens", "the scan_outcomes() function is fine"),
		sy("the-context-suffix-with-arguments", "the scan_outcomes(a, b) function is fine"),
		sy("the-context-prefix-function", "the function scan_outcomes is fine"),
		sy("the-context-prefix-with-backticks", "the function `scan_outcomes` is fine"),
		sy("the-context-prefix-attribute-is-not-in-it",
			"the attribute scan_outcomes is fine"),
		sy("a-keyword-that-continues-into-a-word",
			"the alpha_one functionality is fine"),
		// The live shape scanBoundaried exists for: alternative one ends
		// on the space alternative two needs for its own boundary.
		sy("two-context-matches-sharing-a-boundary",
			"alpha_one function method beta_two"),
		sy("three-context-matches-in-a-row",
			"alpha_one function beta_two method gamma_three class"),
		sy("the-context-pattern-is-case-insensitive",
			"the alpha_one FUNCTION is fine"),
		sy("the-context-pattern-uppercased-prefix", "the METHOD beta_two is fine"),
		// CPython's IGNORECASE folds `i` to U+0130 and U+0131 as well as
		// to `I`; PyFoldI is what makes Go agree.
		sy("the-dotted-capital-i-in-a-keyword",
			"the alpha_one functİon is fine"),
		sy("the-dotless-i-in-a-keyword", "the alpha_one functıon is fine"),
		sy("a-symbol-in-the-skip-list", "call `continue` and `Optional` now"),
		sy("a-symbol-in-the-skip-list-but-long", "call `StopIteration` now"),
		sy("symbols-come-back-sorted", "`zebra_one` `alpha_two` `mango_six`"),
		sy("the-same-symbol-twice", "`alpha_two` and `alpha_two` again"),
		sy("no-symbols-at-all", "nothing here"),
		sy("the-empty-text-for-symbols", ""),
		sy("a-symbol-from-two-patterns-at-once",
			"def run_agent_loop and `run_agent_loop` too"),

		// --- is_synthesis_step ---------------------------------------
		sn("a-synthesis-keyword", "Synthesize the findings"),
		sn("a-synthesis-keyword-uppercased", "SYNTHESIS of the run"),
		sn("a-multiword-keyword", "compile findings from all steps"),
		sn("a-multiword-keyword-split-across-lines",
			"compile\nfindings from all steps"),
		sn("a-keyword-inside-a-word", "resynthesized the run"),
		sn("no-synthesis-keyword", "run the tests"),
		sn("the-empty-step", ""),
		sn("the-keyword-conclusion", "reach a Conclusion"),
		sn("the-keyword-aggregate", "AGGREGATE the numbers"),
		sn("the-keyword-final-summary", "write the final summary"),

		// --- the two report renderings -------------------------------
		cs("a-report-with-nothing-in-it",
			claimRep(jl(), jl(), jl(), jl(), "[]")),
		cs("a-report-with-only-verified",
			claimRep(jl("a.py"), jl("a.py"), jl(), jl(), "[]")),
		cs("a-report-with-only-not-found",
			claimRep(jl("a.py"), jl(), jl("a.py"), jl(), "[]")),
		cs("a-report-with-four-not-found",
			claimRep(jl(), jl(), jl("a.py", "b.py", "c.py", "d.py"), jl(), "[]")),
		cs("a-report-with-exactly-three-not-found",
			claimRep(jl(), jl(), jl("a.py", "b.py", "c.py"), jl(), "[]")),
		cs("a-report-with-only-unresolvable",
			claimRep(jl("a/b.py"), jl(), jl(), jl("a/b.py"), "[]")),
		cs("a-report-with-all-three",
			claimRep(jl(), jl("a.py"), jl("b.py"), jl("c/d.py"), "[]")),
		cs("a-report-whose-rate-is-a-third",
			claimRep(jl(), jl("a.py", "b.py"), jl("c.py"), jl(), "[]")),
		cs("a-report-whose-rate-is-one",
			claimRep(jl(), jl(), jl("c.py"), jl(), "[]")),
		cs("a-report-with-no-checkable-claims",
			claimRep(jl("x/y.py"), jl(), jl(), jl("x/y.py"), "[]")),
		cs("a-report-carrying-suffix-matches",
			claimRep(jl("t/a.py"), jl("t/a.py"), jl(), jl(),
				`[["t/a.py","pkg/t/a.py"]]`)),
		cs("a-report-whose-suffix-order-is-the-answer",
			claimRep(jl(), jl(), jl(), jl(),
				`[["z.py","q/z.py"],["a.py","q/a.py"]]`)),
		ss("a-symbol-report-with-nothing-in-it", symRep(jl(), jl(), jl())),
		ss("a-symbol-report-with-a-hallucination",
			symRep(jl("alpha_one"), jl(), jl("alpha_one"))),
		ss("a-symbol-report-fully-verified",
			symRep(jl("alpha_one"), jl("alpha_one"), jl())),
	}
}
