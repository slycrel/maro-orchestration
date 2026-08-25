package pyval

import (
	"go/parser"
	"go/token"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
)

// writerLayout matches a call that RENDERS a time with a layout, which is
// the thing no package outside this one may do.
//
// Deliberately not matching bare mentions of a layout constant: the READ
// side legitimately names RFC3339Nano, because a store holds timestamps
// written by both runtimes and a parser has to accept both spellings
// (scans.parseISO, skills/coerce, notify/escalation_context, recall). Only
// `.Format(...)` is a writer, and only a writer can put the wrong string
// into a shared ledger.
var writerLayout = regexp.MustCompile(
	`\.Format\(\s*(time\.RFC3339[A-Za-z]*|"[^"]*2006[^"]*")`)

// stripComments blanks every comment before the census reads the file, so
// the tripwire measures CODE.
//
// It scanned raw bytes for one round too long. The r2 fixes rewrote
// inspector.go's doc comment to describe the four RFC3339Nano calls it
// actually replaced — naming `Format(time.RFC3339Nano)` in a sentence — and
// the census reported the sentence as a tenth offender. Nothing about the
// timestamps had changed.
//
// That direction is only embarrassing, but the same blindness runs the other
// way and would not be: a site could be exempted by writing prose that
// happens to match, or a real writer could be argued about on the strength
// of a comment near it. The detector's subject is the code, so it reads the
// code.
//
// Comment spans are replaced with spaces rather than deleted so that offsets
// and line counts survive, in case a future version wants to report them.
func stripComments(path string, src []byte) (string, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, src, parser.ParseComments)
	if err != nil {
		return "", err
	}
	base := fset.File(f.Pos()).Base()
	out := []byte(string(src))
	for _, group := range f.Comments {
		lo, hi := int(group.Pos())-base, int(group.End())-base
		if lo < 0 || hi > len(out) {
			continue
		}
		for i := lo; i < hi; i++ {
			if out[i] != '\n' {
				out[i] = ' '
			}
		}
	}
	return string(out), nil
}

// TestNoPackageSpellsItsOwnTimestamp is a census tripwire, not a unit test.
//
// Python writes every one of these stamps as
// `datetime.now(timezone.utc).isoformat()`. The port had NINE spellings of
// that: five hand-written layouts that got the offset right but wrote
// ".000000" for a whole second where isoformat omits the fraction, and four
// inline RFC3339Nano calls that got the offset wrong too ("Z", nine
// digits) — one of them in a store CPython reads back, where
// `fromisoformat` rejected a "Z" suffix outright before CPython 3.11.
//
// A test of any one site could not have found that. The defect was the
// COUNT: nine copies of one decision, drifting independently, each looking
// locally reasonable. So the invariant is stated as a census — NowISO is
// the only place a timestamp layout is written — and this fails the moment
// a tenth appears.
//
// If a site legitimately needs a different rendering, add it to `allowed`
// with the reason. An exemption someone has to write down is the point; a
// silently divergent tenth copy is what this exists to prevent.
func TestNoPackageSpellsItsOwnTimestamp(t *testing.T) {
	// path -> the PYTHON SPELLING that justifies this writer having its
	// own layout. Every entry was read at the Python site before being
	// added; "it looked like a date" is not a reason, because that is
	// precisely the reasoning that produced nine copies of isoformat.
	//
	// Lens 20 applies here in full: an idiom is not a defect. The defect
	// is a spelling that does not match the spelling at ITS OWN site, and
	// several of these are correct BECAUSE their Python is not isoformat.
	allowed := map[string]string{
		"pyval/pyval.go": "NowISO itself — the one definition",

		// orch_items.now_utc_iso() is literally
		// `time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())` and
		// new_run_id() is `%Y%m%dT%H%M%SZ`. Second precision and a
		// LITERAL Z, deliberately unlike isoformat — the section files
		// and run ids carry this spelling. Verified at
		// src/orch_items.py:412-417.
		"orch/paths.go": "orch_items.now_utc_iso / new_run_id — strftime, not isoformat",

		// Human display and filename slugs, not machine-read stamps:
		// "2006-01-02" is a DAY key, "2006-01-02 15:04" and
		// "2006-01-02 15:04 UTC" render for a reader, "20060102-150405"
		// and "20060102T150405Z" name files. None is parsed by the other
		// runtime.
		"orch/mission.go":      "human display (%Y-%m-%d %H:%M)",
		"playbook/playbook.go": "day keys + a filename slug",
		"record/dailylog.go":   "day key + human display",
		"record/rotate.go":     "rotated-file name slug",

		// task_store.utc_now() is
		// `.replace(microsecond=0).isoformat().replace("+00:00","Z")` —
		// isoformat DELIBERATELY post-processed into second precision and
		// a literal Z, and new_job_id() is `%Y%m%dT%H%M%SZ`. Verified at
		// src/task_store.py:39-45. This is the case lens 20 is about: the
		// spelling that would be wrong three files over is right here.
		"tasks/tasks.go": "task_store.utc_now / new_job_id — second precision + literal Z",

		// skills.py:960 mints a variant name with
		// `strftime("%Y%m%dT%H%M%S%fZ")` — a NAME, not a stamp.
		"skills/utility.go": "skills.py variant name — %Y%m%dT%H%M%S%fZ",

		// UNCLASSIFIED — listed so the list is honest, not so the site is
		// blessed. Each still needs its Python read before it is touched
		// or exempted. Remove the entry when it is classified; do not
		// remove it to make this test green.
		"orch/pids.go": "UNCLASSIFIED: %z-style offset with no colon, no Python read yet",
	}

	root := ".."
	var offenders []string
	// Every writer the walk sees, exempt or not. stripComments is new
	// machinery between the file and the regex, and machinery that blanked
	// too much would make this census pass by seeing nothing — the exact
	// failure the regex probes below were written against, one layer up.
	var writersSeen int
	err := filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".go") ||
			strings.HasSuffix(path, "_test.go") {
			return nil
		}
		body, rerr := os.ReadFile(path)
		if rerr != nil {
			return rerr
		}
		rel := filepath.ToSlash(strings.TrimPrefix(path, root+string(filepath.Separator)))
		code, cerr := stripComments(path, body)
		if cerr != nil {
			return cerr
		}
		for _, m := range writerLayout.FindAllString(code, -1) {
			writersSeen++
			if _, ok := allowed[rel]; ok {
				continue
			}
			offenders = append(offenders, rel+": "+m)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) > 0 {
		t.Errorf("these write a timestamp with their own layout instead of "+
			"pyval.NowISO — Python spells all of them "+
			"datetime.now(timezone.utc).isoformat():\n  %s",
			strings.Join(offenders, "\n  "))
	}

	// The allowlist above names ten files that DO render a layout. If the
	// walk found none of them, it is not reading source any more.
	if writersSeen < len(allowed) {
		t.Errorf("the walk found %d layout writers but %d files are exempted "+
			"as having one — stripComments or the walk is eating real code",
			writersSeen, len(allowed))
	}

	// Anti-vacuity: a regex that matched nothing would pass this test
	// forever, including on the day it stopped compiling to what its
	// author meant. Prove it still recognizes both shapes it is for.
	for _, probe := range []string{
		`t.Format(time.RFC3339Nano)`,
		`t.Format("2006-01-02T15:04:05.000000-07:00")`,
		`x.Format( time.RFC3339 )`,
	} {
		if !writerLayout.MatchString(probe) {
			t.Errorf("the census regex no longer matches %q — it would pass "+
				"by seeing nothing", probe)
		}
	}
	// And that it does NOT match the read side, which is allowed to name
	// both spellings.
	for _, probe := range []string{
		`for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {`,
		`time.Parse(time.RFC3339Nano, ts)`,
	} {
		if writerLayout.MatchString(probe) {
			t.Errorf("the census regex matches the READ side (%q) — parsers "+
				"must be able to accept both runtimes' spellings", probe)
		}
	}
}
