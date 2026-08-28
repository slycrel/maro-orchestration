package mintground

import "strings"

// The tree-level scenarios: collect_run_tool_events and the
// ground_lessons_for_run path around it.
//
// The shape under test is a run directory — `<run>/build/calls/call-*.json`
// — and most of what can go wrong with it is a call record that is not the
// dict the reader assumes. Upstream wraps only `json.loads` in its try, so
// half of these RAISE, and which half is the contract.

func col(name string, tree []mgEntry) mgSpec {
	return mgSpec{Name: name, Kind: "collect", Tree: tree}
}

func colIn(name, runDir string, tree []mgEntry) mgSpec {
	return mgSpec{Name: name, Kind: "collect", RunDir: runDir, Tree: tree}
}

func colCap(name string, cap int, tree []mgEntry) mgSpec {
	return mgSpec{Name: name, Kind: "collect", Tree: tree,
		CapOverride: cap, CapOverrideSet: true}
}

// gl is the ground_lessons_for_run spelling: a run reference, the lessons,
// and where the runs seam points.
func gl(name, runRef string, lessons []string, resolveTo string,
	tree []mgEntry) mgSpec {
	return mgSpec{Name: name, Kind: "ground_lessons", RunRef: runRef,
		Lessons: lessons, ResolveTo: resolveTo, Tree: tree}
}

// rec is a call record holding the given tool_events JSON.
func rec(events string) string { return `{"tool_events": ` + events + `}` }

// oneEvent is a well-formed tool event with the given name and input.
func oneEvent(name, input string) string {
	return `{"name":"` + name + `","input":"` + input + `","output":"o",` +
		`"is_error":false}`
}

func runScenarios() []mgSpec {
	long := strings.Repeat("x", 2100)
	astral := strings.Repeat("\U0001F600", 2100)

	return []mgSpec{
		// --- is there ground truth at all ---
		col("no-build-calls-directory-is-absent",
			[]mgEntry{f("notes.md", "hi")}),
		col("an-empty-calls-directory-is-present",
			[]mgEntry{d("build/calls")}),
		col("a-file-where-calls-should-be-is-absent",
			[]mgEntry{f("build/calls", "not a directory")}),
		col("a-symlink-to-the-calls-directory-is-followed",
			[]mgEntry{f("shadow/call-1.json", rec(`[`+oneEvent("bash", "ls")+`]`)),
				ln("build/calls", "../shadow")}),
		col("a-dangling-calls-symlink-is-absent",
			[]mgEntry{ln("build/calls", "../nowhere")}),
		colIn("the-run-dir-may-be-nested", "runs/r1",
			[]mgEntry{f("runs/r1/build/calls/call-1.json",
				rec(`[`+oneEvent("bash", "ls")+`]`))}),

		// --- which files, in which order ---
		col("the-records-are-read-in-sorted-order",
			[]mgEntry{
				f("build/calls/call-2.json", rec(`[`+oneEvent("bash", "two")+`]`)),
				f("build/calls/call-10.json", rec(`[`+oneEvent("bash", "ten")+`]`)),
				f("build/calls/call-1.json", rec(`[`+oneEvent("bash", "one")+`]`)),
			}),
		col("a-non-matching-name-is-ignored",
			[]mgEntry{
				f("build/calls/notes.json", rec(`[`+oneEvent("bash", "no")+`]`)),
				f("build/calls/call-1.txt", rec(`[`+oneEvent("bash", "no")+`]`)),
				f("build/calls/xcall-1.json", rec(`[`+oneEvent("bash", "no")+`]`)),
				f("build/calls/call-1.json", rec(`[`+oneEvent("bash", "yes")+`]`)),
			}),
		col("an-empty-suffix-name-still-matches",
			[]mgEntry{f("build/calls/call-.json",
				rec(`[`+oneEvent("bash", "yes")+`]`))}),
		col("a-directory-named-like-a-record-is-skipped",
			[]mgEntry{d("build/calls/call-9.json"),
				f("build/calls/call-1.json", rec(`[`+oneEvent("bash", "ok")+`]`))}),
		col("unparseable-json-is-skipped",
			[]mgEntry{f("build/calls/call-1.json", "{not json"),
				f("build/calls/call-2.json", rec(`[`+oneEvent("bash", "ok")+`]`))}),
		col("an-empty-record-file-is-skipped",
			[]mgEntry{f("build/calls/call-1.json", "")}),

		// --- the record is not a dict: these RAISE ---
		col("a-list-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `[1, 2]`)}),
		col("a-string-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `"hello"`)}),
		col("an-int-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `5`)}),
		col("a-float-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `5.5`)}),
		col("a-bool-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `true`)}),
		col("a-null-record-raises-an-attribute-error",
			[]mgEntry{f("build/calls/call-1.json", `null`)}),
		col("the-raise-happens-on-the-first-bad-record",
			[]mgEntry{f("build/calls/call-1.json", `[1]`),
				f("build/calls/call-2.json", rec(`[`+oneEvent("bash", "ok")+`]`))}),

		// --- tool_events is falsy: these are empty ---
		col("a-missing-tool-events-key-is-empty",
			[]mgEntry{f("build/calls/call-1.json", `{"other": 1}`)}),
		col("a-null-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`null`))}),
		col("an-empty-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`[]`))}),
		col("an-empty-string-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`""`))}),
		col("a-zero-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`0`))}),
		col("a-false-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`false`))}),
		col("an-empty-dict-tool-events-is-empty",
			[]mgEntry{f("build/calls/call-1.json", rec(`{}`))}),

		// --- tool_events is truthy but not a list ---
		col("a-dict-tool-events-enumerates-its-keys",
			[]mgEntry{f("build/calls/call-1.json", rec(`{"a": 1}`))}),
		col("a-string-tool-events-enumerates-its-characters",
			[]mgEntry{f("build/calls/call-1.json", rec(`"ab"`))}),
		col("a-number-tool-events-raises-a-type-error",
			[]mgEntry{f("build/calls/call-1.json", rec(`5`+`.5`))}),
		col("an-int-tool-events-raises-a-type-error",
			[]mgEntry{f("build/calls/call-1.json", rec(`7`))}),
		col("a-true-tool-events-raises-a-type-error",
			[]mgEntry{f("build/calls/call-1.json", rec(`true`))}),

		// --- one event's fields ---
		col("a-non-dict-item-is-skipped-but-keeps-its-index",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[1, `+oneEvent("bash", "ls")+`]`))}),
		col("the-ref-names-the-file-and-the-index",
			[]mgEntry{f("build/calls/call-7.json",
				rec(`[`+oneEvent("a", "1")+`,`+oneEvent("b", "2")+`]`))}),
		col("a-missing-name-is-the-empty-string",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"input":"i"}]`))}),
		col("a-null-name-stringifies-to-none",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"name":null}]`))}),
		col("a-numeric-name-stringifies",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"name":12}]`))}),
		col("a-float-name-stringifies",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"name":1.5}]`))}),
		col("a-bool-name-stringifies",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"name":true}]`))}),
		col("a-list-input-stringifies",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"input":[1,"a"]}]`))}),
		col("a-dict-input-stringifies",
			[]mgEntry{f("build/calls/call-1.json", rec(`[{"input":{"a":1}}]`))}),
		col("the-input-is-capped-at-2000-characters",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","input":"`+long+`"}]`))}),
		col("the-input-cap-counts-code-points",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","input":"`+astral+`"}]`))}),
		col("the-output-is-capped-at-2000-characters",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","output":"`+long+`"}]`))}),
		col("the-name-is-not-capped",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"`+long+`"}]`))}),

		// --- the is_error flag ---
		col("the-literal-true-is-an-error",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":true}]`))}),
		col("the-string-true-is-an-error",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":"true"}]`))}),
		col("the-string-true-is-case-folded",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":"TRUE"}]`))}),
		col("a-one-is-not-an-error",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":1}]`))}),
		col("a-yes-is-not-an-error",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":"yes"}]`))}),
		col("a-missing-is-error-is-false",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash"}]`))}),
		col("a-null-is-error-is-false",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":null}]`))}),
		col("a-false-is-error-is-false",
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[{"name":"bash","is_error":false}]`))}),

		// --- the event cap ---
		colCap("the-cap-stops-inside-one-record", 2,
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[`+oneEvent("a", "1")+`,`+oneEvent("b", "2")+`,`+
					oneEvent("c", "3")+`]`))}),
		colCap("the-cap-stops-between-records", 2,
			[]mgEntry{
				f("build/calls/call-1.json",
					rec(`[`+oneEvent("a", "1")+`,`+oneEvent("b", "2")+`]`)),
				f("build/calls/call-2.json", rec(`[`+oneEvent("c", "3")+`]`)),
			}),
		colCap("a-cap-of-one-keeps-one", 1,
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[`+oneEvent("a", "1")+`,`+oneEvent("b", "2")+`]`))}),
		colCap("a-cap-of-zero-still-keeps-one", 0,
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[`+oneEvent("a", "1")+`,`+oneEvent("b", "2")+`]`))}),
		colCap("the-cap-is-not-reached", 9,
			[]mgEntry{f("build/calls/call-1.json",
				rec(`[`+oneEvent("a", "1")+`]`))}),

		// --- read_text(errors="replace") ---
		col("a-lone-ill-formed-byte-becomes-one-replacement",
			[]mgEntry{fb("build/calls/call-1.json",
				append(append([]byte(`{"tool_events":[{"name":"`), 0xFF),
					[]byte(`"}]}`)...))}),
		col("two-unpaired-bytes-become-two-replacements",
			[]mgEntry{fb("build/calls/call-1.json",
				append(append([]byte(`{"tool_events":[{"name":"`), 0xFF, 0xFE),
					[]byte(`"}]}`)...))}),
		col("a-truncated-sequence-becomes-one-replacement",
			[]mgEntry{fb("build/calls/call-1.json",
				append(append([]byte(`{"tool_events":[{"name":"`), 0xE2, 0x82),
					[]byte(`"}]}`)...))}),

		// --- ground_lessons_for_run ---
		gl("no-lessons-grounds-nothing", "r1", []string{}, "run",
			[]mgEntry{f("run/build/calls/call-1.json",
				rec(`[`+oneEvent("web_fetch", "https://a.b")+`]`))}),
		gl("an-empty-run-ref-grounds-nothing", "",
			[]string{"The page was fetched"}, "run",
			[]mgEntry{f("run/build/calls/call-1.json",
				rec(`[`+oneEvent("web_fetch", "https://a.b")+`]`))}),
		gl("an-unresolvable-run-grounds-nothing", "r1",
			[]string{"The page was fetched"}, "", nil),
		gl("a-run-without-call-records-grounds-nothing", "r1",
			[]string{"The page was fetched"}, "run",
			[]mgEntry{f("run/notes.md", "hi")}),
		gl("a-raising-record-grounds-nothing", "r1",
			[]string{"The page was fetched"}, "run",
			[]mgEntry{f("run/build/calls/call-1.json", `[1]`)}),
		gl("the-happy-path-stamps-each-lesson", "r1",
			[]string{"The page was fetched", "Verify the mount",
				"The totals were verified"}, "run",
			[]mgEntry{f("run/build/calls/call-1.json",
				rec(`[`+oneEvent("web_fetch", "https://a.b")+`]`))}),
		{Name: "a-raising-resolver-grounds-nothing", Kind: "ground_lessons",
			RunRef: "r1", Lessons: []string{"The page was fetched"},
			ResolveTo: "run", ResolveRaises: true,
			Tree: []mgEntry{f("run/build/calls/call-1.json",
				rec(`[`+oneEvent("web_fetch", "https://a.b")+`]`))}},
		{Name: "a-failed-import-grounds-nothing", Kind: "ground_lessons",
			RunRef: "r1", Lessons: []string{"The page was fetched"},
			ResolveTo: "run", ImportFails: true,
			Tree: []mgEntry{f("run/build/calls/call-1.json",
				rec(`[`+oneEvent("web_fetch", "https://a.b")+`]`))}},
	}
}

// mgScenarios is the whole set, with the two fields the probe SUBSCRIBES
// to normalised: a nil slice would marshal as JSON null and the probe
// would iterate None.
func mgScenarios() []mgSpec {
	out := concat(textScenarios(), runScenarios())
	for i := range out {
		if out[i].Tree == nil {
			out[i].Tree = []mgEntry{}
		}
		if out[i].Lessons == nil {
			out[i].Lessons = []string{}
		}
	}
	return out
}
