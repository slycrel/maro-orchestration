package receipts

import (
	"encoding/json"
	"fmt"
	"strings"
)

// call is one call-record file under `build/calls/`.
func call(name, body string) rcEntry {
	return rcEntry{Path: "build/calls/" + name, Kind: "file", Data: bs(body)}
}

// jq quotes a string as JSON. Not %q: Go's quoting is GO syntax, and the
// two disagree on exactly the characters these scenarios are about.
func jq(s string) string {
	b, err := json.Marshal(s)
	if err != nil {
		panic(err)
	}
	return string(b)
}

// ev builds one tool_events entry.
func ev(name, cmd, out string, isErr bool) string {
	return fmt.Sprintf(
		`{"name": %s, "input": {"command": %s}, "output": %s, "is_error": %v}`,
		jq(name), jq(cmd), jq(out), isErr)
}

// rec builds a whole call record.
func rec(backend string, events ...string) string {
	return fmt.Sprintf(`{"backend": %s, "tool_events": [%s]}`,
		jq(backend), strings.Join(events, ", "))
}

// rcLoadScenarios covers load_receipts.
//
// Every count this function keeps exists to stop a PARTIAL record from
// collapsing into "the record shows nothing ran", so the scenarios are
// weighted toward the failure shapes: a file that will not parse, a
// record of the wrong shape, an event of the wrong type, a command that
// is not a string, a backend that relays nothing.
func rcLoadScenarios() []rcSpec {
	ld := func(name string, cap string, tree ...rcEntry) rcSpec {
		return rcSpec{Name: name, Kind: "load", Cap: cap,
			RunDirIsPath: true, Tree: tree}
	}

	// A directory of 1001 junk files: the islice bound stops DISCOVERY,
	// not just collection, and the overflow sets `truncated`.
	var many []rcEntry
	for i := 0; i < 1001; i++ {
		many = append(many, call(fmt.Sprintf("call-%04d.json", i),
			rec("subprocess", ev("Bash", "pytest -q", "ok", false))))
	}

	var noSlots []rcEntry
	for i := 0; i < 3; i++ {
		noSlots = append(noSlots, call(fmt.Sprintf("call-%d.json", i),
			rec("subprocess",
				ev("Bash", fmt.Sprintf("cmd-%d-a", i), "", false),
				ev("Bash", fmt.Sprintf("cmd-%d-b", i), "", false))))
	}

	return []rcSpec{
		// The run dir itself.
		{Name: "a-run-dir-that-does-not-exist", Kind: "load", Cap: "400",
			RunDirIsPath: false, RunDirValue: `"/nonexistent/run/dir"`},
		{Name: "a-run-dir-that-is-none", Kind: "load", Cap: "400",
			RunDirIsPath: false, RunDirValue: `null`},
		{Name: "a-run-dir-that-is-an-int", Kind: "load", Cap: "400",
			RunDirIsPath: false, RunDirValue: `5`},
		{Name: "a-run-dir-that-is-a-list", Kind: "load", Cap: "400",
			RunDirIsPath: false, RunDirValue: `["a"]`},
		ld("a-run-dir-with-no-build-directory", "400",
			rcEntry{Path: "notes.md", Kind: "file", Data: bs("hi")}),
		ld("a-calls-directory-with-nothing-in-it", "400",
			rcEntry{Path: "build/calls", Kind: "dir"}),
		ld("a-calls-directory-of-files-that-do-not-match", "400",
			rcEntry{Path: "build/calls/notes.json", Kind: "file",
				Data: bs("{}")},
			rcEntry{Path: "build/calls/call-1.txt", Kind: "file",
				Data: bs("{}")}),
		// glob matches FILES and directories alike; a directory named
		// call-*.json is read as a file and fails.
		ld("a-directory-named-like-a-call-file", "400",
			rcEntry{Path: "build/calls/call-dir.json", Kind: "dir"}),

		// The happy path and the row fields.
		ld("one-recorded-shell-command", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "pytest -q", "8266 passed", false)))),
		ld("a-command-with-surrounding-whitespace", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "  pytest -q\n", "ok", false)))),
		ld("an-output-longer-than-the-head", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "pytest", strings.Repeat("x", 300), false)))),
		ld("an-output-exactly-at-the-head", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "pytest", strings.Repeat("x", 240), false)))),
		// The head is a CODE POINT slice, so a multi-byte output cuts at
		// a different byte offset in each engine.
		ld("an-output-of-multibyte-characters", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "pytest", strings.Repeat("é", 300), false)))),
		ld("an-error-flagged-command", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "pytest", "boom", true)))),
		ld("an-is-error-that-is-a-truthy-string", "400",
			call("call-1.json",
				`{"backend": "subprocess", "tool_events": [{"name": "Bash",`+
					` "input": {"command": "pytest"}, "output": "",`+
					` "is_error": "yes"}]}`)),
		ld("an-is-error-that-is-absent", "400",
			call("call-1.json",
				`{"backend": "subprocess", "tool_events": [{"name": "Bash",`+
					` "input": {"command": "pytest"}}]}`)),

		// Files that do not survive the read.
		ld("a-call-file-that-is-not-json", "400",
			call("call-1.json", "{not json")),
		ld("a-call-file-that-is-empty", "400",
			call("call-1.json", "")),
		// read_text DECODES before json.loads ever runs, so an otherwise
		// perfect record with one bad byte is UNREADABLE. Go's
		// json.Unmarshal would happily substitute U+FFFD and read it.
		{Name: "a-call-file-that-is-not-utf8", Kind: "load", Cap: "400",
			RunDirIsPath: true, Tree: []rcEntry{{
				Path: "build/calls/call-1.json", Kind: "file",
				Data: "eyJiYWNrZW5kIjogInN1YnByb2Nlc3MiLCAidG9vbF9ldmVudHMiOiBbeyJuYW1lIjogIkJhc2giLCAiaW5wdXQiOiB7ImNvbW1hbmQiOiAicHn/dGVzdCJ9fV19"}}},
		ld("a-call-file-over-the-size-bound", "400",
			rcEntry{Path: "build/calls/call-1.json", Kind: "sparse",
				Size: MaxFileBytes + 1}),
		ld("a-call-file-exactly-at-the-size-bound", "400",
			rcEntry{Path: "build/calls/call-1.json", Kind: "sparse",
				Size: MaxFileBytes}),

		// Valid JSON, wrong shape. The recorder always writes a
		// tool_events LIST, so each of these is a corrupt or foreign
		// record and must be COUNTED, not skipped.
		ld("a-record-that-is-a-json-list", "400",
			call("call-1.json", `[1, 2]`)),
		ld("a-record-that-is-a-json-scalar", "400",
			call("call-1.json", `7`)),
		ld("a-record-with-no-tool-events-key", "400",
			call("call-1.json", `{"backend": "subprocess"}`)),
		ld("a-record-whose-tool-events-is-a-mapping", "400",
			call("call-1.json", `{"tool_events": {"a": 1}}`)),
		ld("a-record-whose-tool-events-is-null", "400",
			call("call-1.json", `{"tool_events": null}`)),
		ld("a-record-whose-tool-events-is-empty", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": []}`)),

		// Events that do not survive the screens.
		ld("an-event-that-is-not-a-mapping", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [1, "x", null]}`)),
		ld("an-event-with-no-name", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"input": {"command": "pytest"}}]}`)),
		ld("an-event-with-an-explicit-null-name", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": null,`+
				` "input": {"command": "pytest"}}]}`)),
		// A non-shell tool is not a command execution even when its input
		// carries a `command` argument — counting it fabricated process
		// evidence. It is skipped WITHOUT being counted malformed.
		ld("a-read-tool-event-carrying-a-command", "400",
			call("call-1.json", rec("subprocess",
				ev("Read", "pytest -q", "", false)))),
		ld("a-tool-name-that-is-a-list", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": ["Bash"],`+
				` "input": {"command": "pytest"}}]}`)),
		ld("a-tool-name-that-differs-only-in-case", "400",
			call("call-1.json", rec("subprocess",
				ev("bash", "pytest -q", "", false)))),
		ld("a-shell-event-whose-input-is-a-list", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": "Bash", "input": ["pytest"]}]}`)),
		ld("a-shell-event-whose-input-is-null", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": "Bash", "input": null}]}`)),
		ld("a-shell-event-with-no-input-at-all", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": "Bash"}]}`)),
		ld("a-shell-event-whose-command-is-an-int", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": "Bash",`+
				` "input": {"command": 5}}]}`)),
		ld("a-shell-event-whose-command-is-empty", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "", "", false)))),
		ld("a-shell-event-whose-command-is-all-whitespace", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "  \t\n", "", false)))),
		// Python's str.strip() also removes the four INFORMATION
		// SEPARATORS (U+001C..U+001F); Go's unicode.IsSpace does not, so
		// a command made of them strips to empty in one engine only.
		ld("a-command-of-information-separators", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "\x1c\x1d\x1e\x1f", "", false)))),
		ld("a-command-padded-with-information-separators", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "\x1c\x1d\x1e\x1fpytest\x1c\x1d\x1e\x1f", "", false)))),
		ld("a-shell-event-whose-output-is-an-int", "400",
			call("call-1.json", `{"backend": "subprocess",`+
				` "tool_events": [{"name": "Bash",`+
				` "input": {"command": "pytest"}, "output": 5}]}`)),

		// The capture denominator: which calls a receipt could even see.
		ld("a-record-on-a-non-capturing-backend", "400",
			call("call-1.json", rec("anthropic",
				ev("Bash", "pytest", "", false)))),
		ld("a-record-with-no-backend-key", "400",
			call("call-1.json", `{"tool_events": [{"name": "Bash",`+
				` "input": {"command": "pytest"}}]}`)),
		// An error-stamped record is a FAILED attempt whose tool_events
		// are known-incomplete, so it counts as blind, not as clean
		// capture coverage.
		ld("an-error-stamped-capturing-record", "400",
			call("call-1.json", `{"backend": "subprocess", "error": "boom",`+
				` "tool_events": []}`)),
		ld("an-error-stamped-record-with-a-falsy-error", "400",
			call("call-1.json", `{"backend": "subprocess", "error": "",`+
				` "tool_events": []}`)),
		ld("a-mixed-record-of-capturing-and-blind-calls", "400",
			call("call-1.json", rec("subprocess",
				ev("Bash", "echo ok", "ok", false))),
			call("call-2.json", rec("codex",
				ev("Bash", "pytest -q", "passed", false)))),

		// Ordering. The kept files are SORTED, and the sort is over the
		// name as a string, so call-10 lands before call-2.
		ld("call-files-that-sort-unlike-their-numbers", "400",
			call("call-10.json", rec("subprocess",
				ev("Bash", "second", "", false))),
			call("call-2.json", rec("subprocess",
				ev("Bash", "third", "", false))),
			call("call-1.json", rec("subprocess",
				ev("Bash", "first", "", false)))),
		ld("call-file-names-with-unicode", "400",
			call("call-é.json", rec("subprocess",
				ev("Bash", "accented", "", false))),
			call("call-z.json", rec("subprocess",
				ev("Bash", "plain", "", false)))),

		// The caps.
		ld("a-cap-that-cuts-between-files", "1",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false))),
			call("call-2.json", rec("subprocess",
				ev("Bash", "two", "", false)))),
		ld("a-cap-that-cuts-between-events-in-one-file", "1",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false),
				ev("Bash", "two", "", false)))),
		// The cap is checked BEFORE each file and each event, so a
		// collection that exactly fills the cap still reports truncated
		// only when the loop comes back around.
		ld("a-cap-reached-with-nothing-left-to-scan", "2",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false),
				ev("Bash", "two", "", false)))),
		ld("a-cap-of-zero-falls-back-to-the-default", "0",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false)))),
		ld("a-negative-cap-falls-back-to-the-default", "-3",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false)))),
		ld("a-cap-that-is-not-a-number-falls-back", `"400"`,
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false)))),
		ld("a-cap-that-is-a-float-falls-back", "2.0",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false),
				ev("Bash", "two", "", false)))),
		ld("a-cap-that-is-null-falls-back", "null",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false)))),
		// A bool IS an int in Python and True is 1, so this collects
		// exactly one row. That is not a quirk to round off.
		ld("a-cap-that-is-true", "true",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false),
				ev("Bash", "two", "", false)))),
		ld("a-cap-that-is-false-falls-back", "false",
			call("call-1.json", rec("subprocess",
				ev("Bash", "one", "", false)))),
		// The row cap is reached while files remain, and no row is added
		// from the last file scanned.
		{Name: "a-cap-reached-with-files-still-queued", Kind: "load",
			Cap: "2", RunDirIsPath: true, Tree: noSlots},
		// Discovery itself is bounded: 1001 files, 1000 scanned.
		{Name: "more-call-files-than-the-scan-bound", Kind: "load",
			Cap: "400", RunDirIsPath: true, Tree: many},
	}
}
