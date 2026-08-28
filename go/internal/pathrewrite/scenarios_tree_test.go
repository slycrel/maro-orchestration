package pathrewrite

import "strings"

const prMtime = 1234567890

// prSkipScenarios covers the path-shaped screens. Each screen gets a row
// that trips it and a row that clears it by one step.
func prSkipScenarios() []prSpec {
	plain := []prEntry{file("notes.md", "hello")}
	return []prSpec{
		skip("an-ordinary-file-is-not-skipped", "notes.md", plain),
		skip("a-file-under-a-git-directory", ".git/objects/ab/cd",
			[]prEntry{file(".git/objects/ab/cd", "x")}),
		skip("a-git-directory-nested-deep", "runs/r1/repo/.git/HEAD",
			[]prEntry{file("runs/r1/repo/.git/HEAD", "x")}),
		skip("an-hg-directory", ".hg/store", []prEntry{file(".hg/store", "x")}),
		skip("an-svn-directory", ".svn/wc.db",
			[]prEntry{file(".svn/wc.db", "x")}),
		// `.git` must be a whole COMPONENT: a file merely named after it
		// is ordinary text.
		skip("a-gitignore-file-is-not-a-git-directory", ".gitignore",
			[]prEntry{file(".gitignore", "*.pyc")}),
		skip("a-directory-whose-name-merely-starts-with-git",
			"gitlab/notes.md", []prEntry{file("gitlab/notes.md", "x")}),
		skip("a-leftover-rewrite-temp-file", "notes.md"+TmpSuffix,
			[]prEntry{file("notes.md"+TmpSuffix, "x")}),
		skip("a-sqlite-database", "store.db", []prEntry{file("store.db", "x")}),
		// pathlib's suffix is everything after the LAST dot, so a
		// hyphenated sidecar extension is one suffix.
		skip("a-sqlite-write-ahead-log", "store.db-wal",
			[]prEntry{file("store.db-wal", "x")}),
		skip("a-double-extension-takes-the-last-one", "archive.tar.gz",
			[]prEntry{file("archive.tar.gz", "x")}),
		skip("a-tar-without-compression", "archive.tar",
			[]prEntry{file("archive.tar", "x")}),
		// The suffix test is lowercased, so an uppercase extension is
		// still screened.
		skip("an-uppercase-extension-is-lowercased", "shot.PNG",
			[]prEntry{file("shot.PNG", "x")}),
		// …and the lowercasing is Python's FULL case mapping. CPython
		// folds U+0130 to "i" plus a combining dot, which is NOT ".ico",
		// while a simple per-rune lowering yields ".ico" and screens the
		// file. The row exists to keep the port on pytext.Lower.
		skip("a-dotted-capital-i-does-not-fold-to-ico", "shot.İCO",
			[]prEntry{file("shot.İCO", "x")}),
		// The suffix comes from the NAME, not the path: a directory with
		// a screened extension does not screen the text file inside it.
		skip("a-screened-extension-on-a-directory-not-the-file",
			"shots.png/README", []prEntry{file("shots.png/README", "x")}),
		// …and the NAME is what pathlib lstrips its leading dots from. A
		// dotfile named for a binary extension has no suffix at all,
		// while the same string read as a whole path does.
		skip("a-dotfile-named-after-an-extension", "sub/.png",
			[]prEntry{file("sub/.png", "x")}),
		skip("an-unknown-extension-is-not-screened", "notes.md", plain),
		skip("a-file-with-no-extension", "README",
			[]prEntry{file("README", "x")}),
		skip("a-trailing-dot-is-a-suffix-of-one-character", "notes.",
			[]prEntry{file("notes.", "x")}),
		skip("a-dotfile-has-no-suffix", ".bashrc",
			[]prEntry{file(".bashrc", "x")}),
		skip("a-file-that-is-not-there", "gone.md", plain),
		skip("a-symlink-to-a-real-file", "link.md",
			[]prEntry{file("real.md", "x"),
				{Path: "link.md", Kind: "symlink", Target: "real.md"}}),
		skip("a-dangling-symlink", "link.md",
			[]prEntry{{Path: "link.md", Kind: "symlink", Target: "nope.md"}}),
		skip("a-directory-is-not-a-file", "sub",
			[]prEntry{{Path: "sub", Kind: "dir"}}),
		// Not a directory and not a symlink, and still not a regular
		// file. os.path.isfile is what answers here, and a mode test that
		// only asks "is it a directory" says the wrong thing.
		skip("a-fifo-is-not-a-file", "pipe",
			[]prEntry{{Path: "pipe", Kind: "fifo"}}),
		skip("an-empty-file", "empty.md", []prEntry{file("empty.md", "")}),
		// Order: the vcs screen runs before the stat, so a missing file
		// under .git reports vcs-internal and never touches the disk.
		skip("the-vcs-screen-runs-before-the-stat", ".git/gone",
			[]prEntry{file("notes.md", "x")}),
		// …and the leftover screen runs before the suffix screen, which
		// is why a leftover named after a binary still reports its own
		// reason.
		skip("the-leftover-screen-runs-before-the-suffix-screen",
			"shot.png"+TmpSuffix,
			[]prEntry{file("shot.png"+TmpSuffix, "x")}),
	}
}

func prRewriteScenarios() []prSpec {
	one := [][]string{{prMU, prNewMU}}
	body := "lesson at " + prMU + "/memory/l1.md"
	rewritten := func(name, rel string, tree []prEntry, pairs [][]string,
		max int64) prSpec {
		return prSpec{Name: name, Kind: "rewrite_file", Rel: rel, Tree: tree,
			Pairs: pairs, MaxBytes: max}
	}
	timed := func(path, data, mode string) prEntry {
		return prEntry{Path: path, Kind: "file", Data: bs(data), Mode: mode,
			Mtime: prMtime}
	}
	many := []prEntry{}
	names := []string{}
	for _, n := range []string{"a", "b", "c", "d", "e", "f", "g", "h", "i",
		"j", "k"} {
		many = append(many, prEntry{Path: n + ".md", Kind: "file",
			Data: bs(body), Mode: "0000", Mtime: prMtime})
		names = append(names, n+".md")
	}

	scs := []prSpec{
		rewritten("a-plain-rewrite-preserves-mode-and-mtime", "notes.md",
			[]prEntry{timed("notes.md", body, "0644")}, one,
			DefaultMaxFileBytes),
		rewritten("an-executable-file-keeps-its-bits", "run.sh",
			[]prEntry{timed("run.sh", body, "0755")}, one,
			DefaultMaxFileBytes),
		rewritten("a-private-file-keeps-its-bits", "secret", []prEntry{
			timed("secret", body, "0600")}, one, DefaultMaxFileBytes),
		rewritten("a-file-with-nothing-to-rewrite-is-unchanged", "notes.md",
			[]prEntry{timed("notes.md", "no roots here", "0644")}, one,
			DefaultMaxFileBytes),
		rewritten("an-empty-map-leaves-the-file-alone", "notes.md",
			[]prEntry{timed("notes.md", body, "0644")}, [][]string{},
			DefaultMaxFileBytes),
		rewritten("a-nul-in-the-first-page-is-binary", "blob",
			[]prEntry{timed("blob", body+"\x00tail", "0644")}, one,
			DefaultMaxFileBytes),
		// The head sniff is an EARLY EXIT, not the test: a NUL past the
		// first 8 KiB must still be caught.
		rewritten("a-nul-past-the-sniff-window-is-still-binary", "blob",
			[]prEntry{timed("blob",
				body+strings.Repeat("a", 9000)+"\x00", "0644")}, one,
			DefaultMaxFileBytes),
		rewritten("a-file-just-under-the-sniff-window", "blob",
			[]prEntry{timed("blob",
				body+strings.Repeat("a", 8192-len(body)), "0644")}, one,
			DefaultMaxFileBytes),
		rewritten("a-file-over-the-size-limit", "notes.md",
			[]prEntry{timed("notes.md", body, "0644")}, one, 5),
		rewritten("a-file-exactly-at-the-size-limit", "notes.md",
			[]prEntry{timed("notes.md", body, "0644")}, one,
			int64(len(body))),
		rewritten("a-file-one-byte-over-the-size-limit", "notes.md",
			[]prEntry{timed("notes.md", body, "0644")}, one,
			int64(len(body))-1),
		rewritten("rewriting-a-file-that-is-not-there", "gone.md",
			[]prEntry{timed("notes.md", body, "0644")}, one,
			DefaultMaxFileBytes),
		rewritten("a-file-that-cannot-be-opened", "notes.md",
			[]prEntry{timed("notes.md", body, "0000")}, one,
			DefaultMaxFileBytes),
		// The head sniff tests the FIRST byte too — a NUL at index 0 is
		// the case an off-by-one screen lets through.
		rewritten("a-nul-as-the-very-first-byte", "blob",
			[]prEntry{timed("blob", "\x00"+body, "0644")}, one,
			DefaultMaxFileBytes),
		// The sticky bit is a mode bit outside Go's nine-bit Perm(), and
		// shutil.copymode carries all twelve.
		rewritten("a-mode-bit-above-the-permission-nine", "notes.md",
			[]prEntry{timed("notes.md", body, "01644")}, one,
			DefaultMaxFileBytes),
		// The temp path is DERIVED from the file's name, and a leftover
		// from a killed process sits on exactly that path — so a rewrite
		// reuses it rather than accumulating a second one. Change the
		// suffix and the leftover survives untouched.
		rewritten("a-leftover-on-the-temp-path-is-reused", "notes.md",
			[]prEntry{timed("notes.md", body, "0644"),
				timed("notes.md"+TmpSuffix, "stale half-write", "0644")},
			one, DefaultMaxFileBytes),
		// …and a DIRECTORY on the temp path is the one failure inside the
		// swap a fixture can provoke. CPython's os.unlink refuses to
		// remove it; os.Remove would take an empty one away.
		rewritten("a-directory-on-the-temp-path-survives-the-failure",
			"notes.md",
			[]prEntry{timed("notes.md", body, "0644"),
				{Path: "notes.md" + TmpSuffix, Kind: "dir"}},
			one, DefaultMaxFileBytes),
		rewritten("a-rewrite-leaves-no-temp-file-behind", "notes.md",
			[]prEntry{timed("notes.md", body+" and "+prMU, "0644")}, one,
			DefaultMaxFileBytes),

		// ---- rewrite_tree -------------------------------------------
		{Name: "a-whole-tree-with-one-of-each-screen", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"workspace_root", "str", prWS},
				{"maro_user_dir", "str", prMU}},
			DestRoots: [][]string{{"workspace_root", "str", prNewWS},
				{"maro_user_dir", "str", prNewMU}},
			Tree: []prEntry{
				timed("a.md", "see "+prWS+"/memory and "+prWS, "0644"),
				timed("b.md", "nothing to see", "0644"),
				timed("c.png", "see "+prMU, "0644"),
				timed(".git/HEAD", "see "+prMU, "0644"),
				timed("empty.md", "", "0644"),
				timed("sub/d.md", "see "+prMU+"/config.yml", "0644"),
				timed("blob.out", "see "+prMU+"\x00", "0644"),
			},
			RelNames: []string{"a.md", "b.md", "c.png", ".git/HEAD",
				"empty.md", "sub/d.md", "blob.out", "gone.md"},
			MaxBytes: DefaultMaxFileBytes},
		{Name: "a-tree-with-nothing-to-map", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"workspace_root", "str", prWS}},
			DestRoots:   [][]string{{"workspace_root", "str", prWS}},
			Tree:        []prEntry{timed("a.md", "see "+prWS, "0644")},
			RelNames:    []string{"a.md"}, MaxBytes: DefaultMaxFileBytes},
		{Name: "a-tree-whose-only-root-was-rejected", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"workspace_root", "str", "/usr"}},
			DestRoots:   [][]string{{"workspace_root", "str", prNewWS}},
			Tree:        []prEntry{timed("a.md", "see /usr/x", "0644")},
			RelNames:    []string{"a.md"}, MaxBytes: DefaultMaxFileBytes},
		// Containment. The caller's list comes from archive member names,
		// which are untrusted even after the extractor's screens.
		{Name: "a-relative-name-that-escapes-the-root", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("a.md", body, "0644")},
			RelNames:    []string{"../escape.md", "a.md"},
			MaxBytes:    DefaultMaxFileBytes},
		{Name: "an-absolute-name-replaces-the-root-entirely",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("a.md", body, "0644")},
			RelNames:    []string{"/etc/hostname", "a.md"},
			MaxBytes:    DefaultMaxFileBytes},
		// A dot component is lexical noise that resolves to the same
		// file, so it must NOT be read as an escape.
		{Name: "a-dot-component-stays-inside-the-root",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("sub/a.md", body, "0644")},
			RelNames:    []string{"sub/./a.md"},
			MaxBytes:    DefaultMaxFileBytes},
		{Name: "a-name-that-climbs-and-returns", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("sub/a.md", body, "0644")},
			RelNames:    []string{"sub/../sub/a.md"},
			MaxBytes:    DefaultMaxFileBytes},
		{Name: "the-size-limit-reaches-every-file", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree: []prEntry{timed("a.md", body, "0644"),
				timed("b.md", body, "0644")},
			RelNames: []string{"a.md", "b.md"}, MaxBytes: 5},
		// Eleven unreadable files: `unreadable` is not a screen, so every
		// one is NAMED, and the summary clips the list at ten.
		{Name: "eleven-unreadable-files-clip-the-summary",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        many, RelNames: names,
			MaxBytes: DefaultMaxFileBytes},
		{Name: "one-unreadable-file-is-named-without-a-clip",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree: []prEntry{{Path: "a.md", Kind: "file",
				Data: bs(body), Mode: "0000", Mtime: prMtime},
				timed("b.md", body, "0644")},
			RelNames: []string{"a.md", "b.md"},
			MaxBytes: DefaultMaxFileBytes},
		{Name: "exactly-ten-unreadable-files-need-no-more-suffix",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        many[:10], RelNames: names[:10],
			MaxBytes: DefaultMaxFileBytes},
		// Containment is COMPONENT-WISE. A sibling directory whose name
		// merely extends the root's passes a string-prefix test and is a
		// different directory.
		{Name: "a-sibling-whose-name-extends-the-root",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree: []prEntry{timed("a.md", body, "0644"),
				timed("../a-sibling-whose-name-extends-the-root-evil/f.md",
					body, "0644")},
			RelNames: []string{
				"../a-sibling-whose-name-extends-the-root-evil/f.md",
				"a.md"},
			MaxBytes: DefaultMaxFileBytes},
		// …and a path is under ITSELF: relative_to on equal paths returns
		// "." rather than raising, so the root is screened by what it is
		// (a directory), not by where it is.
		{Name: "the-root-itself-is-inside-the-root", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("a.md", body, "0644")},
			RelNames:    []string{".", "a.md"},
			MaxBytes:    DefaultMaxFileBytes},
		{Name: "an-empty-name-list", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("a.md", body, "0644")},
			RelNames:    []string{}, MaxBytes: DefaultMaxFileBytes},
		{Name: "a-symlink-in-the-name-list", Kind: "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree: []prEntry{timed("real.md", body, "0644"),
				{Path: "link.md", Kind: "symlink", Target: "real.md"}},
			RelNames: []string{"link.md", "real.md"},
			MaxBytes: DefaultMaxFileBytes},
		{Name: "the-same-name-twice-is-scanned-twice",
			Kind:        "rewrite_tree",
			SourceRoots: [][]string{{"maro_user_dir", "str", prMU}},
			DestRoots:   [][]string{{"maro_user_dir", "str", prNewMU}},
			Tree:        []prEntry{timed("a.md", body, "0644")},
			RelNames:    []string{"a.md", "a.md"},
			MaxBytes:    DefaultMaxFileBytes},
	}
	return scs
}
