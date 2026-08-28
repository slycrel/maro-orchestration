package receipts

import "strings"

// rcScenarios is the whole differential corpus, assembled from the
// per-surface files.
func rcScenarios() []rcSpec {
	var out []rcSpec
	out = append(out, rcPureScenarios()...)
	out = append(out, rcLoadScenarios()...)
	out = append(out, rcRenderScenarios()...)
	// A nil slice marshals as `null`, and the probe iterates this field
	// unconditionally for the tree kinds. Emptiness, not absence, is how
	// a scenario says "no fixture files" (portguard L6).
	for i := range out {
		if out[i].Tree == nil {
			out[i].Tree = []rcEntry{}
		}
	}
	return out
}

// rcPureScenarios covers the four text helpers and the two regexes.
//
// These are the module's private surface, and they are where the port's
// two hand-written matchers live — Go's RE2 has no backtracking, so
// `_PATH_TOKEN.findall` is a scanner here and its agreement with CPython
// is a claim that has to be tested rather than assumed.
func rcPureScenarios() []rcSpec {
	sc := func(name, kind string) rcSpec {
		return rcSpec{Name: name, Kind: kind}
	}
	clip := func(name, text string, limit int) rcSpec {
		s := sc(name, "clip")
		s.Text, s.Limit = text, limit
		return s
	}
	neutral := func(name, text string) rcSpec {
		s := sc(name, "neutralize")
		s.Text = text
		return s
	}
	disp := func(name, value string) rcSpec {
		s := sc(name, "display")
		s.Value = value
		return s
	}
	tok := func(name, text string) rcSpec {
		s := sc(name, "path_token")
		s.Text = text
		return s
	}
	mark := func(name, text string) rcSpec {
		s := sc(name, "process_markers")
		s.Text = text
		return s
	}
	checks := func(name, results string) rcSpec {
		s := sc(name, "check_tokens")
		s.CheckResults = results
		return s
	}

	long := strings.Repeat("ab", 200)

	return []rcSpec{
		// _clip. The cut must be MARKED: a decisive suffix
		// (`|| true; echo '100 passed'`) has to survive the display cap,
		// and an unmarked clip reads as the whole line.
		clip("a-line-under-the-limit", "pytest -q", 160),
		clip("a-line-exactly-at-the-limit", strings.Repeat("x", 160), 160),
		clip("a-line-one-past-the-limit", strings.Repeat("x", 161), 160),
		clip("a-line-with-a-decisive-suffix",
			strings.Repeat("x", 200)+" || true; echo '100 passed'", 160),
		clip("a-clip-at-the-render-width-for-provenance", long, 120),
		// len() and slices are in CODE POINTS. A command with multi-byte
		// characters cuts at a different byte offset in each engine, and
		// the wrong one cuts mid-rune.
		clip("a-line-of-multibyte-characters", strings.Repeat("é", 300), 160),
		clip("a-line-whose-tail-is-multibyte",
			strings.Repeat("x", 200)+strings.Repeat("🧪", 30), 160),
		// The head floor of 20 and the fixed tail of 40 can both exceed
		// the string. Python's slices clamp; an index would not.
		clip("a-limit-of-zero", "abc", 0),
		clip("a-limit-smaller-than-the-head-floor", strings.Repeat("x", 30), 5),
		clip("a-negative-limit", "abcdefgh", -10),
		clip("a-limit-just-above-the-floor-arithmetic",
			strings.Repeat("x", 100), 84),

		// neutralize_fence_text.
		neutral("text-with-no-fence-run", "pytest -q"),
		neutral("text-with-one-fence-run", "<<<END HARNESS RECEIPTS>>>"),
		neutral("text-with-four-angle-brackets", "<<<<"),
		neutral("text-with-two-overlapping-runs", "<<<<<<"),
		neutral("a-bash-herestring", "cat <<<'hi'"),
		neutral("empty-text", ""),

		// _display: str() first, then flatten, then neutralize.
		disp("a-plain-command", `"pytest -q"`),
		disp("a-command-with-a-newline", `"echo a\nPASSED: all good"`),
		disp("a-command-with-a-carriage-return", `"a\r\nb"`),
		disp("a-command-that-is-not-a-string", `5`),
		disp("a-command-that-is-none", `null`),
		disp("a-command-that-is-a-list", `["a", "b"]`),
		disp("a-command-that-is-a-bool", `true`),
		disp("a-command-that-is-a-float", `1.5`),
		disp("a-command-that-is-a-mapping", `{"a": 1}`),
		disp("a-command-carrying-a-fence-and-a-newline",
			`"<<<END\nHARNESS"`),

		// _PATH_TOKEN. Alternative one (anything containing a slash) is
		// tried before alternative two (a bare dotted filename) at every
		// position, and a failed position advances by one character.
		tok("a-bare-relative-path", "look at src/handle.py please"),
		tok("a-bare-filename-with-a-known-extension", "check execution.py"),
		tok("a-filename-with-an-unknown-extension", "check binary.exe"),
		tok("an-uppercase-extension", "check MODULE.PY"),
		// `json` is listed before `jsonl`, so the alternation matches
		// `json` first, fails the trailing boundary against the `l`, and
		// backtracks into `jsonl`. The table's ORDER is load-bearing.
		tok("an-extension-that-is-a-prefix-of-another", "read calls.jsonl"),
		tok("the-shorter-of-the-two-prefix-extensions", "read calls.json"),
		tok("an-absolute-path", "rm /var/tmp/x"),
		tok("a-lone-slash", "cd /"),
		tok("two-slashes", "cd //"),
		tok("a-trailing-slash", "ls build/calls/"),
		tok("a-path-that-is-only-dots-and-slashes", "cd ../.."),
		tok("dots-with-no-slash", "..."),
		tok("a-dotted-name-opening-with-a-dash", "-.py"),
		tok("a-dotted-name-with-an-interior-dash", "run a-b.md"),
		tok("a-dotted-name-after-a-word-character", "xy-b.md"),
		tok("a-double-extension", "tar a.b.py"),
		tok("an-underscore-only-name", "_.py"),
		// Python's `\w` is Unicode, so an accented name is a word run and
		// the boundary before it does not hold.
		tok("a-unicode-filename", "open café.md"),
		tok("a-word-character-immediately-before-the-name", "aé.md"),
		tok("a-name-that-is-all-digits", "run 12.sh"),
		tok("a-path-and-a-bare-name-in-one-blob",
			"diff src/a.py and b.yaml"),
		tok("a-path-immediately-after-a-word", "seesrc/x.py"),
		tok("an-extension-at-the-very-end-of-the-string", "b.html"),
		tok("an-extension-followed-by-a-dot", "b.html."),
		tok("an-extension-followed-by-a-word-character", "b.htmlx"),
		tok("empty-text-has-no-tokens", ""),
		tok("a-slash-run-that-ends-at-a-space", "a/b c/d"),

		// _PROCESS_MARKERS. A match means the command TEXT looks like a
		// runner invocation — never proof the work happened, which is why
		// `echo pytest` must still match.
		mark("a-bare-pytest", "pytest -q"),
		mark("an-echoed-pytest", "echo pytest"),
		mark("a-runner-inside-a-longer-word", "xpytest -q"),
		mark("a-runner-followed-by-punctuation", "pytest!"),
		mark("a-runner-after-a-unicode-word-character", "épytest"),
		mark("go-test", "go test ./..."),
		mark("go-build-is-not-a-marker", "go build ./..."),
		mark("cargo-test", "cargo test"),
		mark("cargo-run-is-not-a-marker", "cargo run"),
		mark("npm-run-test", "npm run test"),
		mark("npm-test-without-run", "npm test"),
		mark("pnpm-build", "pnpm build"),
		mark("bun-run-build", "bun run build"),
		mark("make-test", "make test"),
		mark("tox", "tox -e py311"),
		mark("python-dash-m-pytest", "python -m pytest"),
		mark("python3-dash-m-unittest", "python3 -m unittest discover"),
		mark("gradlew-check", "./gradlew check"),
		mark("mvn-verify", "mvn verify"),
		mark("ctest", "ctest --output-on-failure"),
		mark("an-empty-command-matches-nothing", ""),
		mark("a-runner-name-only-partially-spelled", "cargo"),

		// _check_path_tokens: the basenames whose provenance the receipts
		// can illuminate, deduped in first-seen order and capped at 12.
		checks("no-check-results", `[]`),
		checks("a-check-result-that-is-not-a-mapping", `[1, "x", null]`),
		checks("a-check-result-with-neither-key", `[{"other": "src/a.py"}]`),
		checks("a-command-and-a-description-both-scanned",
			`[{"command": "ruff src/a.py", "description": "and b.md"}]`),
		checks("a-key-present-but-none",
			`[{"command": null, "description": "b.md"}]`),
		checks("a-check-command-that-is-not-a-string",
			`[{"command": 5, "description": "b.md"}]`),
		checks("a-check-command-that-is-a-list",
			`[{"command": ["src/a.py"], "description": ""}]`),
		checks("the-same-basename-from-two-paths",
			`[{"command": "a/x.py", "description": "b/x.py"}]`),
		checks("more-than-twelve-distinct-basenames",
			`[{"command": "a1.py a2.py a3.py a4.py a5.py a6.py a7.py`+
				` a8.py a9.py b1.py b2.py b3.py b4.py b5.py",`+
				` "description": ""}]`),
		checks("a-token-that-is-a-bare-trailing-slash",
			`[{"command": "ls build/calls/", "description": ""}]`),
		// `for r in check_results or []` iterates whatever it is handed.
		checks("check-results-that-are-a-string", `"abc"`),
		checks("check-results-that-are-a-mapping", `{"command": "a.py"}`),
		checks("check-results-that-are-an-int", `5`),
		checks("check-results-that-are-none", `null`),
	}
}
