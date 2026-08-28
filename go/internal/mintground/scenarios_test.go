package mintground

// The text-level scenarios: everything the two engines can be asked about
// without a filesystem. The tree-level ones live in scenarios_run_test.go.
//
// Read these as the port's argument list. Each group below is one claim
// the Go file makes about the Python — that a `\b` was safe to drop, that
// a window counts code points, that an alternation's order is or is not
// load-bearing — and the scenario is what makes the claim falsifiable.

import "strings"

// The one-line spellings. A scenario is read as (name, input), and the
// kind is carried by which helper built it.
func sen(name, text string) mgSpec { return mgSpec{Name: name, Kind: "sentences", Text: text} }
func ins(name, text string) mgSpec { return mgSpec{Name: name, Kind: "instruction", Text: text} }
func ret(name, text string) mgSpec {
	return mgSpec{Name: name, Kind: "retrospective", Text: text}
}
func ctl(name, text string) mgSpec { return mgSpec{Name: name, Kind: "clause_tail", Text: text} }
func cla(name, text string) mgSpec { return mgSpec{Name: name, Kind: "claims", Text: text} }
func tie(name, text string) mgSpec { return mgSpec{Name: name, Kind: "tie_tokens", Text: text} }
func spc(name, text string) mgSpec {
	return mgSpec{Name: name, Kind: "specific_tokens", Text: text}
}
func evp(name, event string) mgSpec {
	return mgSpec{Name: name, Kind: "event_preds", Event: event}
}
func grt(name, text, events string) mgSpec {
	return mgSpec{Name: name, Kind: "ground_text", Text: text, Events: events}
}
func smy(name, grounding string) mgSpec {
	return mgSpec{Name: name, Kind: "summary", Grounding: grounding}
}
func mrk(name, grounding string) mgSpec {
	return mgSpec{Name: name, Kind: "marker", Grounding: grounding}
}
func hus(name, grounding string) mgSpec {
	return mgSpec{Name: name, Kind: "has_unsupported", Grounding: grounding}
}

// ev is one event dict as JSON source. The five keys are always present
// because ground_text and the predicates SUBSCRIPT them.
func ev(name, input string) string {
	return `{"ref":"r/` + name + `","name":"` + name + `","input":"` + input +
		`","output":"","is_error":false}`
}

func textScenarios() []mgSpec {
	return concat(
		sentenceScenarios(),
		instructionScenarios(),
		retrospectiveScenarios(),
		claimScenarios(),
		tokenScenarios(),
		predicateScenarios(),
		groundTextScenarios(),
		renderScenarios(),
	)
}

func concat(groups ...[]mgSpec) []mgSpec {
	out := []mgSpec{}
	for _, g := range groups {
		out = append(out, g...)
	}
	return out
}

// --- the sentence split ----------------------------------------------------
//
// `re.split(r"(?<=[.;!?])\s+|\n+", text)`. The lookbehind is why this is
// hand-written, so every part of the lookbehind is asked about: which
// characters open it, that whitespace alone does not split, that a
// newline splits without one, and that the two alternatives are tried in
// the order the pattern writes them.
func sentenceScenarios() []mgSpec {
	return []mgSpec{
		sen("split-on-a-period", "one. two"),
		sen("split-on-a-semicolon", "one; two"),
		sen("split-on-a-bang", "one! two"),
		sen("split-on-a-question", "one? two"),
		sen("a-comma-is-not-a-sentence-end", "one, two"),
		sen("a-colon-is-not-a-sentence-end", "one: two"),
		sen("no-space-no-split", "one.two"),
		sen("the-whitespace-run-is-greedy", "one.   two"),
		sen("a-tab-is-whitespace", "one.\ttwo"),
		sen("a-nbsp-is-whitespace-to-python", "one. two"),
		sen("a-form-feed-is-whitespace", "one.\ftwo"),
		sen("an-information-separator-is-whitespace", "one.\x1ctwo"),
		sen("a-line-separator-is-whitespace", "one. two"),
		sen("a-newline-splits-without-punctuation", "one\ntwo"),
		sen("a-newline-run-is-one-split", "one\n\n\ntwo"),
		sen("both-alternatives-at-one-position", "one.\n\ntwo"),
		sen("a-leading-newline-emits-an-empty-part", "\none"),
		sen("a-trailing-newline-emits-an-empty-part", "one\n"),
		sen("a-trailing-period-space-emits-an-empty-part", "one. "),
		sen("the-empty-text-is-one-empty-part", ""),
		sen("a-lone-period-does-not-split", "."),
		sen("a-crlf-splits-on-the-newline-alone", "one\r\ntwo"),
		sen("a-carriage-return-alone-does-not-split", "one\rtwo"),
		sen("a-carriage-return-after-a-period-does", "one.\rtwo"),
		sen("a-lookbehind-cannot-fire-at-position-zero", " one"),
		sen("an-astral-character-before-the-split", "\U0001F600. two"),
		// The two alternatives are tried in the pattern's order, so a run
		// that is BOTH — a newline opening a whitespace run after a period
		// — belongs whole to the first one.
		sen("a-punctuated-break-swallows-a-run-that-starts-with-a-newline",
			"one.\n \ntwo"),
	}
}

// --- the instruction gate --------------------------------------------------
func instructionScenarios() []mgSpec {
	return []mgSpec{
		ins("a-bare-opener-is-an-order", "Verify the mount before running"),
		ins("a-past-tense-opener-is-not", "Verified the mount before running"),
		ins("the-opener-match-is-case-folded", "VERIFY the mount"),
		ins("a-non-opener-first-word-is-not-an-order", "Nothing verified here"),
		ins("an-auxiliary-in-the-next-three-tokens-cancels",
			"Build 42 was verified against the manifest"),
		ins("the-auxiliary-window-stops-at-the-fourth-token",
			"Commit abc def was verified against the manifest"),
		ins("the-auxiliary-window-clamps-on-a-short-sentence", "Verify was"),
		ins("a-sequencer-fronts-an-order", "Then record the date each price was checked"),
		ins("sequencers-stack", "Then also finally record the date"),
		ins("a-sentence-of-only-sequencers-orders-nothing", "Then also finally"),
		ins("a-leading-dash-tag-is-stripped", "- Verify the mount"),
		ins("a-leading-star-tag-is-stripped", "* Verify the mount"),
		ins("a-leading-bullet-tag-is-stripped", "• Verify the mount"),
		ins("a-leading-bracket-tag-is-stripped", "[recovery] Verify the mount"),
		ins("a-forty-character-bracket-tag-is-stripped",
			"["+"0123456789012345678901234567890123456789"+"] Verify it"),
		ins("a-forty-one-character-bracket-tag-is-not",
			"["+"01234567890123456789012345678901234567890"+"] Verify it"),
		ins("a-numbered-tag-is-stripped", "1. Verify the mount"),
		ins("a-parenthesised-number-tag-is-stripped", "2) Verify the mount"),
		ins("a-step-tag-is-stripped", "Step 3: Verify the mount"),
		ins("a-step-tag-is-case-folded", "STEP 3. Verify the mount"),
		ins("tags-repeat", "- 1. [x] Verify the mount"),
		ins("a-unicode-digit-numbers-a-tag", "٥. Verify the mount"),
		ins("a-subordinator-hands-the-order-to-its-comma",
			"For each question, state the answer"),
		ins("a-subordinator-with-no-comma-orders-nothing",
			"For each question the answer was verified"),
		ins("a-subordinator-whose-clause-does-not-order",
			"For each question, the answer was verified"),
		ins("a-sequencer-after-the-comma-is-skipped",
			"Before finalizing, then verify the totals"),
		ins("a-comma-inside-quotes-is-not-a-clause-break",
			"For an agenda task scoped as 'summarize a command, save to file',"+
				" the plan allocated 7 steps but the goal was verified achieved"),
		ins("a-comma-inside-parentheses-is-not-a-clause-break",
			"For a run (one, save) the plan allocated 7 steps"),
		ins("a-comma-inside-backticks-is-not-a-clause-break",
			"For a run `one, save` the plan allocated 7 steps"),
		ins("a-comma-inside-double-quotes-is-not-a-clause-break",
			"For a run \"one, save\" the plan allocated 7 steps"),
		ins("a-comma-inside-brackets-is-not-a-clause-break",
			"For a run [one, save] the plan allocated 7 steps"),
		ins("the-word-window-stops-at-120-characters",
			"When the operator has finished reviewing the whole of the "+
				"generated manifest and every attached artifact, verify it"),
		ins("the-clause-window-stops-at-60-characters",
			"For each of the many separate questions the operator asked in "+
				"the earlier session, verify the totals"),
		ins("an-astral-character-costs-one-of-the-120",
			"\U0001F600 For each question, state the answer"),
		ins("a-word-carries-an-apostrophe", "Don't verify the mount"),
		ins("an-empty-sentence-orders-nothing", ""),

		// The two windows the clause branch cuts, at the code point where
		// each one's edge changes the answer.
		ins("the-first-comma-is-the-clause-break",
			"If the tool, run the check, quickly."),
		ins("the-auxiliary-window-stops-at-three-tokens",
			"Check the first two was fine"),
		ins("the-auxiliary-is-read-case-folded", "Check the tool WAS broken"),
		ins("a-word-window-edge-truncates-an-auxiliary",
			"Run "+strings.Repeat("y", 113)+" was fine"),
		ins("the-word-window-counts-code-points",
			"Run x"+strings.Repeat("\u00e9", 80)+" was fine"),
		ins("the-clause-window-opens-after-the-comma",
			"If, run "+strings.Repeat("y", 51)+" was tail"),
		ins("the-clause-window-stops-at-sixty-code-points",
			"If, run "+strings.Repeat("y", 52)+" was tail"),
	}
}

// --- the retrospective gate ------------------------------------------------
func retrospectiveScenarios() []mgSpec {
	return []mgSpec{
		ret("a-sentence-initial-subject-is-not-embedded",
			"It was fetched from the CDN"),
		ret("a-complementizer-two-words-back-embeds",
			"Download the source and confirm it was retrieved in full"),
		ret("which-embeds", "Log which values were tested"),
		ret("each-embeds", "Record the date each price was checked"),
		ret("that-embeds", "The report says that the page was fetched"),
		ret("whether-embeds", "The report says whether the page was fetched"),
		ret("a-complementizer-three-words-back-does-not-embed",
			"The report says that a page was fetched"),
		ret("a-subordinator-four-words-back-embeds",
			"Retry the fetch until the page was fetched successfully"),
		ret("a-coordinator-does-not-embed",
			"The plan allocated 7 steps but the goal was verified achieved"),
		ret("a-clause-break-resets-the-subordinator-window",
			"Until the retry, the page was fetched from the CDN"),
		ret("as-if-is-hypothetical",
			"It treats a partial response as if it were a full fetch"),
		ret("as-though-is-hypothetical",
			"It reads as though the page was fetched"),
		ret("would-have-is-hypothetical",
			"The run would have fetched the page"),
		ret("had-it-is-hypothetical",
			"Had it been fetched, the totals were checked"),
		ret("a-retro-finite-verb-takes-an-object",
			"The vendor supplied the data"),
		ret("a-retro-finite-verb-takes-a-digit",
			"The vendor supplied 5 rows"),
		ret("a-retro-finite-verb-takes-a-unicode-digit",
			"The vendor supplied ٥ rows"),
		ret("a-four-letter-ed-verb-is-too-short", "The run used the data"),
		ret("a-five-letter-ed-verb-is-long-enough", "The run typed the data"),
		ret("the-retro-aux-covers-was", "The page was current"),
		ret("the-retro-aux-covers-were", "The pages were current"),
		ret("the-retro-aux-covers-wasnt", "The page wasn't current"),
		ret("the-retro-aux-covers-werent", "The pages weren't current"),
		ret("the-retro-aux-covers-did", "The run did correlate"),
		ret("the-retro-aux-covers-didnt", "The run didn't correlate"),
		ret("the-retro-aux-covers-had-been", "The page had been current"),
		ret("the-retro-aux-covers-has-already-been",
			"The page has already been current"),
		ret("the-retro-aux-covers-have-just-verbed",
			"The runs have just correlated"),
		ret("the-retro-aux-covers-having-only-been",
			"The page having only been current"),
		ret("was-must-end-on-a-boundary", "The page waso current"),
		ret("wasnt-wins-over-was-when-the-boundary-fails",
			"The page wasn't"),
		ret("the-aux-is-case-folded", "The page WAS current"),
		ret("no-marker-is-not-retrospective", "The page is current"),
		ret("an-instruction-with-an-embedded-marker-is-not-a-report",
			"Verify the mount, which was checked yesterday"),
		ret("an-empty-sentence-reports-nothing", ""),

		// The embedding net: how much of the prefix it reads, and where.
		ret("a-two-word-prefix-can-still-embed", "Notes that were tested."),
		ret("a-subordinator-before-the-clause-break-does-not-embed",
			"While ready, the page was fetched."),
		ret("an-order-with-a-main-clause-report-is-still-an-order",
			"Run the check now, the page was fetched."),
	}
}

// --- the claim lexicon and the token vetoes --------------------------------
func claimScenarios() []mgSpec {
	return []mgSpec{
		cla("an-auth-claim", "The fetch was authenticated with a key"),
		cla("authenticat-takes-any-suffix", "The authentication was performed"),
		cla("authenticat-needs-a-suffix", "It was authenticat"),
		cla("logged-in-with-a-hyphen", "The client was logged-in already"),
		cla("logged-in-with-a-space", "The client was logged in already"),
		cla("with-credentials", "The run was performed with credentials"),
		cla("a-fetch-claim", "The page was fetched from the CDN"),
		cla("a-download-claim", "The page was downloaded from the CDN"),
		cla("a-retrieve-claim", "The page was retrieved from the CDN"),
		cla("a-scrape-claim", "The page was scraped from the CDN"),
		cla("a-test-claim", "The module was tested end to end"),
		cla("tests-passed", "The run confirmed tests passed cleanly"),
		cla("test-passed-singular", "The run confirmed test passed cleanly"),
		cla("tests-ran", "The run confirmed tests ran cleanly"),
		cla("pytest-alone", "The run was green: pytest reported nothing"),
		cla("an-execute-claim", "The script was executed on the box"),
		cla("ran-alone", "The script ran on the box"),
		cla("a-probe-claim-verified", "The totals were verified against source"),
		cla("a-probe-claim-confirmed", "The totals were confirmed against source"),
		cla("a-probe-claim-probed", "The totals were probed against source"),
		cla("a-probe-claim-validated", "The totals were validated against source"),
		cla("a-probe-claim-measured", "The totals were measured against source"),
		cla("a-probe-claim-checked", "The totals were checked against source"),
		cla("one-sentence-mints-one-claim-per-family",
			"The fetch was authenticated and the page was fetched and "+
				"the page was fetched again"),
		cla("two-families-in-one-sentence",
			"The authenticated fetch was completed and the page was fetched"),
		cla("two-sentences-mint-separately",
			"The page was fetched. The totals were verified."),
		cla("the-claim-excerpt-stops-at-160-characters",
			"The page was fetched from the content delivery network after "+
				"the operator supplied a token, and the totals were then "+
				"verified against the vendor's own published figures twice"),
		cla("the-excerpt-counts-code-points",
			"\U0001F600\U0001F600\U0001F600\U0001F600\U0001F600 The page "+
				"was fetched from the content delivery network after the "+
				"operator supplied a token, and the totals were verified "+
				"against the vendor's own figures"),

		// Veto 1: the hyphenated compound.
		cla("a-hyphen-before-the-token-vetoes-it",
			"The recovery-verified tag was applied to the row"),
		cla("the-re-prefix-survives-the-veto",
			"The totals were re-verified against source"),
		cla("the-re-prefix-is-case-sensitive",
			"The totals were Re-verified against source"),
		cla("a-hyphen-after-the-token-vetoes-it",
			"The verified-output file was applied to the row"),
		cla("a-word-before-the-re-prefix-vetoes-it",
			"The totals were prere-verified against source"),

		// Veto 2: the modal.
		cla("a-modal-vetoes", "The mount must be checked before the run"),
		cla("a-negated-modal-vetoes", "The mount cannot be fetched by the run"),
		cla("a-modal-with-an-adverb-vetoes",
			"The mount can each be independently checked by the run"),
		cla("a-modal-perfect-vetoes", "The run could have fetched the page"),
		cla("a-modal-perfect-passive-vetoes",
			"The totals should have been verified by the run"),
		cla("an-infinitive-vetoes", "The operator wanted to verify the totals"),
		cla("a-modal-outside-the-48-character-window-does-not-veto",
			"The mount must and then a longer stretch of prose intervenes "+
				"here before the totals were verified"),
		cla("the-48-character-window-counts-code-points",
			"The mount must \U0001F600\U0001F600\U0001F600\U0001F600"+
				"\U0001F600\U0001F600\U0001F600\U0001F600\U0001F600"+
				"\U0001F600 be checked"),

		// Veto 3: polarity.
		cla("a-negator-vetoes", "The fetch was not authenticated by the run"),
		cla("never-vetoes", "The page was never fetched by the run"),
		cla("nt-vetoes", "The page wasn't fetched by the run"),
		cla("without-vetoes", "The page was fetched without credentials"),
		cla("a-negator-four-words-back-vetoes",
			"The page was not a b c d fetched by the run"),
		cla("a-negator-five-words-back-does-not",
			"The page was not a b c d e fetched by the run"),
		cla("a-colon-ends-the-negator-clause",
			"The run did not produce uniform confidence: 12/14 ideas "+
				"confirmed as STRONG matches"),
		cla("an-em-dash-ends-the-negator-clause",
			"It was not treated as evidence — a grep was run to corroborate "+
				"it, and in this case confirmed the finding"),
		cla("a-double-hyphen-ends-the-negator-clause",
			"It was not evidence -- the totals were verified anyway"),
		cla("a-spaced-hyphen-ends-the-negator-clause",
			"It was not evidence - the totals were verified anyway"),

		// The gate as a whole.
		cla("an-instruction-mints-nothing", "Verify the totals against source"),
		cla("a-non-retrospective-sentence-mints-nothing",
			"The totals are verified against source"),
		cla("a-bare-measurement-report-mints-nothing",
			"Measured correction (A/B run e0bbc289): a separate turn per "+
				"probe is the cost driver"),
		cla("the-empty-text-mints-nothing", ""),
		cla("whitespace-only-mints-nothing", "   \n  "),

		// The modal window is 48 CODE POINTS ending at the token, and
		// these three put its far edge, its units and its existence each
		// on their own falsifier. No "not" in any of them: the negator
		// veto would answer first and the modal window would go unread.
		cla("the-modal-window-reaches-exactly-48-code-points",
			"the page must have been "+strings.Repeat("a", 30)+"ly fetched."),
		cla("the-modal-window-counts-code-points",
			"the page must have been "+strings.Repeat("\u00e9", 30)+
				"ly fetched."),
		cla("a-modal-beyond-the-window-does-not-veto",
			"the page must have been "+strings.Repeat("a", 60)+"ly fetched."),
		cla("the-negator-veto-is-clause-local",
			"the page - not yet - was fetched."),
		cla("a-python-space-is-stripped-off-the-sentence",
			"\u001cThe page was fetched."),
		cla("a-lexicon-hit-inside-a-word-is-not-a-hit",
			"The page was ready and prefetched the data."),
		cla("a-lexicon-hit-may-open-the-sentence",
			"Fetched the page and the run was clean."),
	}
}

// --- the tie and specificity tokens ----------------------------------------
func tokenScenarios() []mgSpec {
	return []mgSpec{
		tie("a-three-character-token-is-too-short", "the abc abcd"),
		tie("stopwords-are-dropped", "session step task goal lesson vendor"),
		tie("lexicon-verbs-are-dropped", "fetched verified vendor.example"),
		tie("the-token-class-carries-punctuation", "src/mint_grounding.py"),
		tie("uppercase-is-lowered", "VENDOR.EXAMPLE"),
		tie("the-turkish-dotted-i-lowers-to-two-code-points", "SYNTHESİZE"),
		tie("a-space-breaks-a-token", "vendor example"),
		tie("digits-are-tokens", "12345"),
		tie("the-empty-text-has-no-tokens", ""),
		spc("a-bare-word-is-not-specific", "content data vendor"),
		spc("a-dotted-name-is-specific", "vendor.example"),
		spc("a-trailing-period-is-not-shape", "fetch. data."),
		spc("a-leading-slash-is-not-shape", "/data /content"),
		spc("a-hyphenated-name-is-specific", "api-beta"),
		spc("an-underscored-name-is-specific", "api_beta"),
		spc("a-digit-makes-a-name-specific", "vendor2000"),
		spc("a-unicode-digit-makes-a-name-specific", "vendor٥٥٥"),
		spc("an-inner-slash-is-specific", "src/module"),
	}
}

// --- the event predicates --------------------------------------------------
func predicateScenarios() []mgSpec {
	return []mgSpec{
		evp("bash-is-exec", ev("bash", "ls")),
		evp("shell-is-exec", ev("shell", "ls")),
		evp("run-command-is-exec", ev("run_command", "ls")),
		evp("the-tool-name-is-case-folded", ev("BASH", "ls")),
		evp("an-unknown-tool-is-not-exec", ev("edit", "ls")),
		evp("a-name-containing-fetch-is-a-fetch", ev("web_fetch", "x")),
		evp("a-name-containing-search-is-a-fetch", ev("web_search", "x")),
		evp("curl-in-an-exec-input-is-a-fetch", ev("bash", "curl https://a.b")),
		evp("wget-in-an-exec-input-is-a-fetch", ev("bash", "wget a")),
		evp("a-bare-url-in-an-exec-input-is-a-fetch", ev("bash", "open http://a.b")),
		evp("a-url-needs-a-boundary-on-the-left", ev("bash", "xhttp://a.b")),
		evp("curl-needs-a-boundary", ev("bash", "curly a")),
		evp("a-url-in-a-non-exec-input-is-not-a-fetch", ev("edit", "https://a.b")),
		evp("an-authorization-header-is-auth",
			ev("bash", "curl -H 'Authorization: x' https://a.b")),
		evp("a-bearer-token-is-auth", ev("bash", "curl -H 'bearer q' https://a.b")),
		evp("a-bare-bearer-is-not-auth", ev("bash", "curl bearer https://a.b")),
		evp("an-x-api-key-header-is-auth", ev("bash", "curl -H x-api-key https://a.b")),
		evp("an-eight-character-api-key-is-auth",
			ev("bash", "curl https://a.b?api_key=abcdefgh")),
		evp("a-seven-character-api-key-is-not",
			ev("bash", "curl https://a.b?api_key=abcdefg")),
		evp("the-api-key-separator-is-optional",
			ev("bash", "curl https://a.b?apikey=abcdefgh")),
		evp("the-api-key-separator-may-be-a-hyphen",
			ev("bash", "curl https://a.b?api-key=abcdefgh")),
		evp("an-eight-character-token-is-auth",
			ev("bash", "curl https://a.b?token=abcdefgh")),
		evp("the-pinned-dummy-token-is-not-auth",
			ev("bash", "curl https://a.b?token=a")),
		evp("eight-turkish-dotless-i-is-a-token-value",
			ev("bash", "curl https://a.b?token=ıııı"+
				"ıııı")),
		evp("eight-turkish-dotted-i-is-a-token-value",
			ev("bash", "curl https://a.b?token=İİİİ"+
				"İİİİ")),
		evp("eight-kelvin-signs-are-a-token-value",
			ev("bash", "curl https://a.b?token=KKKKKKKK")),
		evp("the-user-flag-is-auth", ev("bash", "curl --user a:b https://a.b")),
		evp("the-user-flag-needs-a-boundary",
			ev("bash", "curl --username https://a.b")),
		evp("the-short-user-flag-is-auth", ev("bash", "curl -u a:b https://a.b")),
		evp("the-short-user-flag-needs-a-colon", ev("bash", "curl -u ab https://a.b")),
		evp("the-cookie-flag-is-auth", ev("bash", "curl --cookie k=v https://a.b")),
		evp("the-short-cookie-flag-is-auth", ev("bash", "curl -b k https://a.b")),
		evp("an-assigned-password-is-auth", ev("bash", "curl https://a.b -d password=x")),
		evp("a-bare-password-word-is-not-auth", ev("bash", "curl https://a.b/password")),
		evp("assigned-credentials-are-auth", ev("bash", "curl https://a.b -d credentials=x")),
		evp("a-login-url-is-not-auth", ev("bash", "curl https://a.b/login")),
		evp("auth-material-without-a-fetch-is-not-auth",
			ev("bash", "echo 'Authorization: x'")),
		evp("pytest-is-a-test", ev("bash", "pytest -q")),
		evp("test-safe-is-a-test", ev("bash", "bash scripts/test-safe.sh")),
		evp("test_safe-is-a-test", ev("bash", "bash test_safe")),
		evp("npm-test-is-a-test", ev("bash", "npm test")),
		evp("go-test-is-a-test", ev("bash", "go test ./...")),
		evp("unittest-is-a-test", ev("bash", "python -m unittest")),
		evp("make-test-is-a-test", ev("bash", "make test")),
		evp("a-test-command-in-a-non-exec-tool-is-not-a-test",
			ev("edit", "pytest -q")),
		evp("read-is-a-probe", ev("read", "a.py")),
		evp("grep-is-a-probe", ev("grep", "a")),
		evp("glob-is-a-probe", ev("glob", "*.py")),
		evp("ls-is-a-probe", ev("ls", ".")),
		evp("an-edit-is-not-a-probe", ev("edit", "a.py")),
	}
}

// --- the join --------------------------------------------------------------
func groundTextScenarios() []mgSpec {
	const noEvents = `[]`
	const oneBash = `[{"ref":"r1","name":"bash","input":"ls","output":"",` +
		`"is_error":false}]`
	const oneFetch = `[{"ref":"r1","name":"web_fetch","input":"https://a.b",` +
		`"output":"body","is_error":false}]`
	const erroredFetch = `[{"ref":"r1","name":"web_fetch",` +
		`"input":"https://a.b","output":"","is_error":true}]`
	const fetchOfVendor = `[{"ref":"r1","name":"web_fetch",` +
		`"input":"https://vendor.example/data","output":"","is_error":false}]`
	const fourTiedFetches = `[` +
		`{"ref":"r1","name":"web_fetch","input":"vendor.example","output":"","is_error":false},` +
		`{"ref":"r2","name":"web_fetch","input":"vendor.example","output":"","is_error":false},` +
		`{"ref":"r3","name":"web_fetch","input":"vendor.example","output":"","is_error":false},` +
		`{"ref":"r4","name":"web_fetch","input":"vendor.example","output":"","is_error":false}]`
	const rankedFetches = `[` +
		`{"ref":"r1","name":"web_fetch","input":"vendor.example","output":"","is_error":false},` +
		`{"ref":"r2","name":"web_fetch","input":"vendor.example/rows.json","output":"","is_error":false}]`
	const twoUntiedFetches = `[` +
		`{"ref":"r1","name":"web_fetch","input":"https://a.b","output":"","is_error":false},` +
		`{"ref":"r2","name":"web_fetch","input":"https://c.d","output":"","is_error":false}]`
	// A dotted capital I: Python lowercases it to "i" plus a combining
	// dot and Go lowercases it to a bare "i", so the tie token spans the
	// difference.
	const fetchOfDottedI = `[{"ref":"r1","name":"web_fetch",` +
		`"input":"https://x/ABC\u0130DEF","output":"","is_error":false}]`
	// 187 code points before the identifier, so the 160-point claim
	// excerpt cannot see it and the sentence can.
	longTiedClaim := "The rows were fetched from " +
		strings.Repeat("padword ", 20) + "vendor.example"

	return []mgSpec{
		grt("no-candidates-is-unsupported",
			"The page was fetched from the CDN", noEvents),
		grt("an-errored-event-is-not-a-candidate",
			"The page was fetched from the CDN", erroredFetch),
		grt("a-generic-claim-takes-a-family-level-receipt",
			"The content was fetched", oneFetch),
		grt("a-specific-claim-that-ties-is-supported",
			"The rows were fetched from vendor.example", fetchOfVendor),
		grt("a-specific-claim-that-does-not-tie-is-unprobed",
			"The rows were fetched from api-b.example", oneFetch),
		grt("a-probe-claim-that-does-not-tie-is-unprobed",
			"The totals were verified", oneBash),
		grt("a-probe-claim-that-ties-is-supported",
			"The totals in vendor.example were verified",
			`[{"ref":"r1","name":"bash","input":"cat vendor.example",`+
				`"output":"","is_error":false}]`),
		grt("only-three-receipts-survive",
			"The rows were fetched from vendor.example", fourTiedFetches),
		grt("the-receipts-are-ranked-by-score",
			"The rows.json rows were fetched from vendor.example",
			rankedFetches),
		grt("the-sort-is-stable-on-a-tie",
			"The rows were fetched from vendor.example", fourTiedFetches),
		grt("the-output-text-can-carry-the-tie",
			"The rows were fetched from vendor.example",
			`[{"ref":"r1","name":"web_fetch","input":"x",`+
				`"output":"vendor.example","is_error":false}]`),
		grt("the-tool-name-can-carry-the-tie",
			"The rows were fetched by vendor_tool",
			`[{"ref":"r1","name":"vendor_tool_fetch","input":"x",`+
				`"output":"","is_error":false}]`),
		grt("two-claims-two-stamps",
			"The page was fetched. The totals were verified.", oneFetch),
		grt("a-text-with-no-claims-has-no-stamps", "Verify the totals", oneFetch),
		grt("an-auth-claim-without-auth-material-is-unsupported",
			"The fetch was authenticated", oneFetch),

		grt("the-tie-tokens-come-from-the-whole-sentence",
			longTiedClaim, fetchOfVendor),
		grt("the-event-text-is-lowered-the-python-way",
			"The rows abcidef were fetched", fetchOfDottedI),
		grt("the-family-level-receipt-is-the-first-candidate",
			"The content was fetched", twoUntiedFetches),
	}
}

// --- the three renderers ---------------------------------------------------
func renderScenarios() []mgSpec {
	const none = `null`
	const empty = `[]`
	const mixed = `[{"claim":"a","family":"fetch","status":"supported"},` +
		`{"claim":"b","family":"fetch","status":"unsupported"},` +
		`{"claim":"c","family":"probe","status":"unprobed"}]`
	const unknown = `[{"claim":"a","family":"fetch","status":"weird"}]`
	const oneBad = `[{"claim":"only one","family":"fetch","status":"unsupported"}]`
	const lopsided = `[{"claim":"a","family":"fetch","status":"supported"},` +
		`{"claim":"b","family":"fetch","status":"supported"},` +
		`{"claim":"c","family":"probe","status":"unsupported"},` +
		`{"claim":"d","family":"probe","status":"unprobed"},` +
		`{"claim":"e","family":"probe","status":"unprobed"},` +
		`{"claim":"f","family":"probe","status":"unprobed"}]`
	const unprobedOnly = `[{"claim":"a","family":"probe","status":"unprobed"}]`
	const twoBad = `[{"claim":"first","family":"fetch","status":"unsupported"},` +
		`{"claim":"second","family":"probe","status":"unsupported"}]`
	const threeBad = `[{"claim":"first","family":"fetch","status":"unsupported"},` +
		`{"claim":"second","family":"fetch","status":"unsupported"},` +
		`{"claim":"third","family":"fetch","status":"unsupported"}]`
	const longBad = `[{"claim":"` +
		`0123456789012345678901234567890123456789ABCDEF` +
		`","family":"fetch","status":"unsupported"}]`
	const astralBad = `[{"claim":"` +
		`😀😀😀😀😀` +
		`😀😀😀😀😀` +
		`😀😀😀😀😀` +
		`😀😀😀😀😀` +
		`😀😀` +
		`","family":"fetch","status":"unsupported"}]`

	return []mgSpec{
		smy("no-grounding-renders-nothing", none),
		smy("an-empty-list-renders-nothing", empty),
		smy("the-three-counts-render", mixed),
		smy("an-unknown-status-is-counted-nowhere", unknown),
		smy("the-three-counts-are-not-interchangeable", lopsided),
		mrk("no-grounding-marks-nothing", none),
		mrk("an-empty-list-marks-nothing", empty),
		mrk("a-supported-stamp-marks-nothing",
			`[{"claim":"a","family":"fetch","status":"supported"}]`),
		mrk("one-unsupported-claim-is-singular", oneBad),
		mrk("two-unsupported-claims-are-plural", twoBad),
		mrk("three-unsupported-claims-show-two-heads", threeBad),
		mrk("the-head-stops-at-40-characters", longBad),
		mrk("the-head-counts-code-points", astralBad),
		hus("no-grounding-has-nothing-unsupported", none),
		hus("an-empty-list-has-nothing-unsupported", empty),
		hus("a-mixed-list-has-one", mixed),
		hus("a-supported-only-list-has-none",
			`[{"claim":"a","family":"fetch","status":"supported"}]`),
		hus("an-unprobed-only-list-has-none", unprobedOnly),
	}
}
