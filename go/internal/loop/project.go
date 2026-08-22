// Project-directory resolution for the executor lane — the port of
// Python resolve_project_slug (loop_artifacts.py), which exists because
// of a real incident: goals that open the way people actually open them
// ("tell me about the…", "write a summary of…") collide on phrasing
// alone, and the second goal inherits the first's artifacts as its own
// prior work. With tool-bearing steps that is no longer just confusing
// context — the second run's agent reads AND overwrites the first run's
// files (adversarial exec-tranche review 2026-08-22: all four lenses
// independently flagged the unported disambiguation).
//
// Divergence, named: Python reads the colliding project's recorded
// mission from its NEXT.md ledger; this runtime has no project ledger
// yet, so the creating goal is recorded in a `.mission` file inside the
// project dir at creation time and read back from there — with a
// fallback read of a Python-written NEXT.md "> goal" line, because the
// two runtimes share one workspace format and a Python-created project
// has no .mission file: without the fallback the guard is blind on the
// interop side of the boundary (adversarial exec r2 2026-08-22,
// Skeptic). Same decision procedure, different primary storage.
//
// Disambiguation alone only protects against SEQUENTIAL collisions —
// the concurrent case (two runs racing Stat→MkdirAll on the same slug)
// is closed by the run-lifetime admission flock in slot.go, the port of
// Python acquire_project_slot (same review, Skeptic HIGH).
package loop

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// genericWords ports Python _GENERIC_WORDS verbatim: words that carry no
// subject. Used only as a GUARD — a word missing from this set means
// resolveProjectSlug degrades to reusing the colliding project (today's
// behavior), never to a new hazard.
var genericWords = func() map[string]bool {
	const words = `
a an the this that these those it its my our your their his her
and or but not for from with without into onto over under about above
of on in to at by as is are was were be been being do does did done
i me we us you they them he she who what which when where why how
please can could should would may might will shall let lets need needs
tell told say said give given show shown ask asked find found get got
look looking see seen read reading write writing make making create
creating build building fix fixing check checking research researching
review reviewing analyze analyzing summarize summarizing explain
explaining update updating add adding run running test testing use using
help helping work working try trying take taking put putting
new old more most some any all every each other another same
summary report overview analysis notes note thing things stuff item items
book books article articles paper papers file files code repo repos
project projects doc docs document documents page pages site sites
thread threads post posts video videos story stories task tasks`
	m := map[string]bool{}
	for _, w := range strings.Fields(words) {
		m[w] = true
	}
	return m
}()

// slugDisambiguationCap mirrors Python _SLUG_DISAMBIGUATION_CAP.
const slugDisambiguationCap = 20

// missionFileName is this runtime's stand-in for Python's NEXT.md
// mission line (see the package comment).
const missionFileName = ".mission"

// subjectWords ports _subject_words: content words of a goal — drops
// punctuation, generic words, and anything under 3 characters.
func subjectWords(text string) map[string]bool {
	words := strings.Fields(slugStrip.ReplaceAllString(strings.ToLower(text), " "))
	out := map[string]bool{}
	for _, w := range words {
		if len(w) >= 3 && !genericWords[w] {
			out[w] = true
		}
	}
	return out
}

// slugIsGeneric ports _slug_is_generic: true when a slug's words name no
// subject ("tell-me-about-the-book"). Two or more subject words means a
// collision is two goals about the same thing — what slugs are for.
func slugIsGeneric(slug string) bool {
	return len(subjectWords(strings.ReplaceAll(slug, "-", " "))) <= 1
}

// recordedMission reads the goal a project recorded when it was created,
// "" if unreadable (matching Python's except → ""). Primary store is
// this runtime's .mission file; a project created by the PYTHON runtime
// has none, so fall back to its NEXT.md mission line (ensure_project
// writes "Mission:\n\n> <goal>\n" — the parse mirrors Python
// _recorded_mission: first ">"-prefixed line wins).
func recordedMission(projectDir string) string {
	raw, err := os.ReadFile(filepath.Join(projectDir, missionFileName))
	if err == nil {
		if m := strings.TrimSpace(string(raw)); m != "" {
			return m
		}
	}
	next, err := os.ReadFile(filepath.Join(projectDir, "NEXT.md"))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(next), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, ">") {
			return strings.TrimSpace(strings.TrimLeft(line, "> "))
		}
	}
	return ""
}

// sameSubject ports _same_subject: do two goals sharing a generic slug
// talk about the same thing? Compares the distinguishing tail — subject
// words outside the slug both share by construction. Missing evidence
// (no mission recorded, no tail on either side) reads as same — today's
// behavior, biasing toward continuity.
// The weak return names the evidence gap when the yes rests on missing
// evidence rather than a matched tail — the caller surfaces it as a
// warning so a silent merge is at least a VISIBLE merge (adversarial
// exec r2 2026-08-22, Skeptic: every other degrade path here is loud).
func sameSubject(goal, mission, slug string) (same bool, weak string) {
	if mission == "" {
		return true, "no recorded mission to compare against"
	}
	slugWords := map[string]bool{}
	for _, w := range strings.Split(slug, "-") {
		slugWords[w] = true
	}
	tail := func(text string) map[string]bool {
		t := map[string]bool{}
		for w := range subjectWords(text) {
			if !slugWords[w] {
				t[w] = true
			}
		}
		return t
	}
	tailA, tailB := tail(goal), tail(mission)
	if len(tailA) == 0 || len(tailB) == 0 {
		return true, "no distinguishing subject words on one side"
	}
	for w := range tailA {
		if tailB[w] {
			return true, ""
		}
	}
	return false, ""
}

// resolveProjectSlug ports Python resolve_project_slug: the naive slug,
// disambiguated with -2, -3, … (hash fallback past the cap) when a
// generic opening would otherwise merge the goal into an unrelated
// project. Same-mission collisions — the continuity mechanism — are
// deliberately untouched.
// The warn return is non-empty when an EXISTING generic-slug dir was
// reused on weak evidence (no mission, phrasing-only tails) — the
// operator's chance to catch a bad merge before the worker writes.
func resolveProjectSlug(projectsRoot, goal string) (slug, warn string) {
	base := goalSlug(goal)
	dir := func(slug string) string { return filepath.Join(projectsRoot, slug) }
	reuseWarn := func(slug, weak string) string {
		if weak == "" {
			return ""
		}
		return fmt.Sprintf("reusing existing project dir %q on weak evidence (%s) — "+
			"verify it is the same mission", slug, weak)
	}
	if _, err := os.Stat(dir(base)); err != nil {
		return base, ""
	}
	if !slugIsGeneric(base) {
		return base, ""
	}
	if same, weak := sameSubject(goal, recordedMission(dir(base)), base); same {
		return base, reuseWarn(base, weak)
	}
	for n := 2; n <= slugDisambiguationCap; n++ {
		cand := fmt.Sprintf("%s-%d", base, n)
		if _, err := os.Stat(dir(cand)); err != nil {
			return cand, ""
		}
		if same, weak := sameSubject(goal, recordedMission(dir(cand)), base); same {
			return cand, reuseWarn(cand, weak)
		}
	}
	// Cap reached: a hash still beats silently merging unrelated work.
	// Returned unclaimed and unchecked — PYTHON PARITY (loop_artifacts.py
	// returns the hash slug unconditionally too); a 32-bit collision here
	// merges, same as there.
	sum := sha256.Sum256([]byte(goal))
	return fmt.Sprintf("%s-%x", base, sum[:4]), ""
}

// recordProjectMission writes the creating goal into the project dir so
// later resolveProjectSlug calls can tell continuity from collision.
// First writer wins — the recorded mission describes the project's
// ORIGIN, and a same-mission re-entry must not rewrite it. The write is
// full-content-then-link, never open-then-write: a crash mid-write with
// O_EXCL left an existing-but-EMPTY .mission, which reads as "no
// evidence" and permanently defeats disambiguation for that dir
// (adversarial exec r2 2026-08-22, Expert QA). link(2) fails with
// EEXIST, preserving first-writer-wins atomically WITH its content.
func recordProjectMission(projectDir, goal string) error {
	path := filepath.Join(projectDir, missionFileName)
	tmp, err := os.CreateTemp(projectDir, missionFileName+".tmp-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.WriteString(goal + "\n"); err != nil {
		tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	if err := os.Link(tmp.Name(), path); err != nil {
		if os.IsExist(err) {
			return nil
		}
		return err
	}
	return nil
}
