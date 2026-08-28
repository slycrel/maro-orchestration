package pathrewrite

import (
	"encoding/base64"
	"strings"
)

func bs(s string) string { return base64.StdEncoding.EncodeToString([]byte(s)) }

const (
	prWS    = "/home/clawd/.maro/workspace"
	prNewWS = "/Users/jeremy/.maro/workspace"
	prMU    = "/home/clawd/.maro"
	prNewMU = "/Users/jeremy/.maro"
)

// prPairs is the ordinary two-root map, already in the order build_map
// would produce (longest source first).
func prPairs() [][]string {
	return [][]string{{prWS, prNewWS}, {prMU, prNewMU}}
}

func val(name, kind, v string, strict bool) prSpec {
	return prSpec{Name: name, Kind: "validate", ValueKind: kind, Value: v,
		Strict: strict}
}

func sub(name string, pairs [][]string, data string) prSpec {
	return prSpec{Name: name, Kind: "substitute", Pairs: pairs,
		Data: bs(data)}
}

func file(path, data string) prEntry {
	return prEntry{Path: path, Kind: "file", Data: bs(data)}
}

func skip(name, rel string, tree []prEntry) prSpec {
	return prSpec{Name: name, Kind: "skip", Rel: rel, Tree: tree}
}

// prScenarios is the fixture set. Every screen in the module gets both a
// case that trips it and a case that clears it by one step, because a
// screen tested only from the tripping side cannot tell a correct bound
// from a bound that refuses everything.
func prScenarios() []prSpec {
	scs := []prSpec{
		// ---- validate_root: the shape rules -------------------------
		val("a-good-root-normalizes-to-itself", "str", prWS, true),
		val("the-same-root-under-a-loose-screen", "str", prWS, false),
		val("an-integer-is-not-a-string", "int", "0", true),
		val("none-is-not-a-string", "none", "", true),
		val("a-list-is-not-a-string", "list", "/srv/ws", true),
		val("a-bool-is-not-a-string", "bool", "True", true),
		val("a-float-is-not-a-string", "float", "1.5", true),
		val("an-empty-string-is-empty", "str", "", true),
		val("whitespace-only-is-empty-after-stripping", "str", "   ", true),
		val("surrounding-whitespace-is-stripped", "str",
			"  /opt/maro/ws  ", true),
		// Python's str.strip removes every Unicode space, NBSP included,
		// which Go's strings.TrimSpace does not — the reason the port
		// routes through pytext.Strip.
		val("a-non-breaking-space-is-stripped-too", "str",
			"\u00a0/opt/maro/ws\u00a0", true),
		// …and Python strips the four INFORMATION SEPARATORS as well,
		// which Go's unicode.IsSpace does not consider space at all. This
		// is the row that separates pytext.Strip from strings.TrimSpace;
		// the NBSP above does not, because Go agrees about that one.
		val("an-information-separator-is-stripped-too", "str",
			"\x1c/opt/maro/ws\x1f", true),
		val("a-tab-and-a-newline-are-stripped", "str",
			"\t/opt/maro/ws\n", true),
		val("a-nul-byte-is-refused", "str", "/srv/w\x00s", true),
		val("a-relative-root-is-refused", "str", "srv/ws", true),
		// The refusal message is a repr, so the quoting rule is part of
		// the contract: CPython prefers single quotes and switches to
		// double when the value contains one.
		val("the-not-absolute-message-is-a-repr", "str", "srv'ws", true),
		val("a-repr-with-a-backslash", "str", "srv\\ws", true),
		val("a-repr-with-a-newline", "str", "srv\nws", true),
		val("doubled-and-trailing-slashes-collapse", "str",
			"//srv//maro//ws//", false),
		val("bare-slash-resolves-to-filesystem-root", "str", "/", true),
		val("four-slashes-resolve-to-filesystem-root", "str", "////", true),
		val("a-listed-system-directory", "str", "/usr", true),
		val("a-listed-two-component-system-directory", "str",
			"/private/var", true),
		val("one-component-is-too-shallow", "str", "/srv2", true),
		val("a-parent-segment-is-refused", "str", "/a/../b", true),
		// Purely lexical: a "." component is NOT normalized away, so the
		// returned root still contains it.
		val("a-dot-segment-survives-lexically", "str", "/a/./b", true),
		val("two-below-a-shared-home-is-refused", "str",
			"/home/clawd", true),
		val("two-below-a-shared-home-clears-a-loose-screen", "str",
			"/home/clawd", false),
		val("three-below-a-shared-home-is-allowed", "str",
			"/home/clawd/ws", true),
		val("a-generic-subdirectory-of-usr-is-refused", "str",
			"/usr/lib", true),
		val("one-deeper-under-usr-is-allowed", "str", "/usr/lib/maro", true),
		val("srv-ws-is-refused-as-a-source", "str", "/srv/ws", true),
		val("srv-ws-is-accepted-as-a-destination", "str", "/srv/ws", false),
		val("a-deep-path-under-private-tmp", "str", "/private/tmp/x", true),
		val("one-deeper-under-private-tmp", "str", "/private/tmp/x/y", true),
		val("a-non-ascii-root", "str", "/hõme/maro/ws", true),
		val("a-root-that-is-only-slashes-and-dots", "str", "/./.", true),

		// ---- build_map ---------------------------------------------
		{Name: "two-roles-map-longest-source-first", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS},
				{"maro_user_dir", "str", prMU}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS},
				{"maro_user_dir", "str", prNewMU}}},
		{Name: "a-role-the-source-never-recorded-is-skipped", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS},
				{"repo_root", "str", "/Users/jeremy/repo"}}},
		{Name: "an-empty-source-root-is-skipped", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", ""}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "a-none-source-root-is-skipped", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "none", ""}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "no-local-counterpart-is-recorded", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{}},
		{Name: "an-empty-destination-is-no-counterpart", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", ""}}},
		// False is neither None nor "", so it reaches validate_root and
		// is refused by TYPE — the arm a `not raw_src` test would miss.
		{Name: "a-false-source-root-reaches-the-type-check", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "bool", "False"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "a-zero-source-root-reaches-the-type-check", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "int", "0"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "a-list-source-root-is-rendered-with-str", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "list", "/srv/ws"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "a-bad-source-root-is-rejected-with-its-reason",
			Kind:        "build",
			SourceRoots: [][]string{{"workspace_root", "str", "/usr"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "a-bad-destination-root-is-rejected-with-its-reason",
			Kind:        "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", "/"}}},
		{Name: "a-long-bad-root-is-clipped-to-two-hundred", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str",
				"/usr/" + strings.Repeat("x", 400) + "\x00"}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS}}},
		{Name: "an-unclipped-no-counterpart-value", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str",
				"/srv/" + strings.Repeat("y", 400)}},
			DestRoots: [][]string{}},
		{Name: "importing-on-the-exporting-machine-maps-nothing",
			Kind:        "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", prWS}}},
		{Name: "two-roles-recording-one-source-dedupe", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS},
				{"repo_root", "str", prWS}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS},
				{"repo_root", "str", "/Users/jeremy/repo"}}},
		{Name: "equal-length-sources-break-the-tie-on-the-string",
			Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", "/srv/bbb/ws"},
				{"repo_root", "str", "/srv/aaa/ws"}},
			DestRoots: [][]string{{"workspace_root", "str", "/x/bbb"},
				{"repo_root", "str", "/x/aaa"}}},
		// A source gets the STRICT screen: a bare home directory is
		// exactly the generic fragment the depth rule exists for, and it
		// is refused here while being a legitimate destination.
		{Name: "a-source-that-only-a-loose-screen-would-accept",
			Kind:        "build",
			SourceRoots: [][]string{{"workspace_root", "str", "/home/clawd"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}}},
		// A destination gets the LOOSE screen, and this is the pair that
		// says so: /srv/ws fails the depth-below-a-shared-directory rule
		// as a source and is a perfectly ordinary install root as a
		// destination.
		{Name: "a-destination-that-only-a-loose-screen-accepts",
			Kind:        "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", "/srv/ws"}}},
		{Name: "an-empty-pair-of-maps-is-falsy", Kind: "build",
			SourceRoots: [][]string{}, DestRoots: [][]string{}},
		{Name: "a-custom-role-list-is-honored", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS},
				{"other", "str", "/srv/maro/other"}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS},
				{"other", "str", "/x/other"}},
			Roles: []string{"other"}},
		{Name: "a-role-nobody-recorded-produces-nothing", Kind: "build",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}},
			Roles:       []string{"nonexistent"}},
		// A source whose length differs in BYTES but not code points from
		// its sibling: the sort key is len(), which counts code points.
		// "/data/abcde" is 11 runes and 11 bytes; "/data/ééé" is 9 runes
		// and 12. Counting bytes puts them in the other order, and this
		// is the only pair shape that can say so.
		{Name: "the-sort-length-is-in-code-points-not-bytes", Kind: "build",
			SourceRoots: [][]string{
				{"workspace_root", "str", "/data/abcde"},
				{"repo_root", "str", "/data/ééé"}},
			DestRoots: [][]string{{"workspace_root", "str", "/x/one"},
				{"repo_root", "str", "/x/two"}}},
	}
	scs = append(scs, prSubstituteScenarios()...)
	scs = append(scs, prSkipScenarios()...)
	scs = append(scs, prRewriteScenarios()...)
	return scs
}
