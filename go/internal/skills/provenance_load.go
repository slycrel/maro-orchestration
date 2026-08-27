package skills

import (
	"fmt"
	"os"
	"path/filepath"
	"sort"

	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// ProvenanceLoad is load_skill_provenance's answer plus what it lost.
//
// Python logs the unreadable count to a module logger and returns only the
// records. This port returns both, following the house rule that a loader
// announces its losses to the caller rather than to a stream nobody is
// reading — the same shape as LoadSkills. Warning holds Python's sentence
// verbatim when there were losses, and is empty when there were none.
type ProvenanceLoad struct {
	// Records holds WHATEVER json.loads produced, element by element —
	// not []pyval.Obj. Python appends the decoded value unconditionally,
	// so a sidecar holding `5` or `[]` is a RECORD in the returned list,
	// and its caller is documented as receiving dicts only because every
	// writer produces one.
	//
	// The first cut of this port typed the slice as []pyval.Obj and counted
	// a non-mapping sidecar with the malformed, under a comment asserting
	// "the count is what the operator sees either way". That was false —
	// CPython returns the value and this returned one record fewer — and
	// the differential caught it on the scalar fixture. Lens 19: the
	// sentence claiming the narrowing was harmless was the thing to check.
	Records []any
	Warning string
}

// LoadSkillProvenance returns every provenance sidecar for a skill, newest
// first.
//
// # The name is interpolated into a GLOB, unescaped
//
// Python builds `prov_dir.glob(f"{skill_name}_*.json")`. A skill whose name
// carries `*`, `?` or `[` therefore reads OTHER skills' records and can
// miss its own. Measured on this box against four sidecars:
//
//	'a*b'  -> ['ab_1.json', 'axb_1.json']   <- not this skill's at all
//	'a[b]' -> ['ab_1.json']                 <- and NOT a[b]_1.json
//
// The port reproduces this rather than fixing it. Two runtimes read one
// shared store, and an audit that answers differently depending on which
// one asked is worse than one that is wrong the same way in both — a
// divergence here would show up as "the Go runtime says this skill was
// never demoted" with nothing to explain it. pytext.FnMatch carries
// Python's matching rules, which are NOT Go's: filepath.Match would treat
// a backslash as an escape and reject an unclosed bracket outright.
//
// The port does add a WARNING when the name carries a metacharacter, since
// silently answering with another skill's history is the kind of thing an
// operator should hear about. That is additive — it changes no record and
// no ordering.
//
// # The read is DELIBERATELY lenient, unlike every other reader here
//
// `read_text(encoding="utf-8", errors="surrogateescape")`. Undecodable
// bytes become lone surrogates and the record still parses: measured,
// `{"a": "\xff"}` loads as `{'a': '\udcff'}` rather than raising. This is
// the opposite of internal/tasks, where the same operation is strict and
// round 9 had to make the port refuse. The difference is real and is in
// Python's source, not an oversight to normalise: provenance is an audit
// trail, and a byte-tainted record is still evidence.
//
// Go has no lone-surrogate string, so a tainted record's text cannot be
// held exactly. Such a file is counted as unreadable rather than laundered
// to U+FFFD — the port declines to invent characters in an audit record,
// and says so in the warning. NAMED DIVERGENCE: CPython returns the record
// with surrogates where this returns one fewer record and a louder count.
func LoadSkillProvenance(ws, skillName string) ProvenanceLoad {
	dir := filepath.Join(ws, "memory", "skill_provenance")
	ents, err := os.ReadDir(dir)
	if err != nil {
		// `if not prov_dir.exists(): return []` — and any other listing
		// failure lands in the same place Python's would, since it globs a
		// directory it has just decided exists.
		return ProvenanceLoad{Records: []any{}}
	}

	pattern := skillName + "_*.json"
	var names []string
	for _, e := range ents {
		// Directories are NOT skipped. Python globs paths, not files, so a
		// DIRECTORY whose name matches is selected — and `read_text` on it
		// raises IsADirectoryError straight into the bare except, which
		// counts it as unreadable. Skipping it here (the first cut) dropped
		// it from the count instead, so the two runtimes reported different
		// numbers of malformed records for the same directory. The mutation
		// battery found it by removing the skip and seeing nothing fail.
		if pytext.FnMatch(e.Name(), pattern) {
			names = append(names, e.Name())
		}
	}
	// `sorted(..., reverse=True)`. Python sorts the Path objects, which for
	// same-directory siblings compares their string parts — the filename.
	// The stamp is the filename's tail, so reverse-lexicographic is
	// newest-first, which is what the docstring promises.
	//
	// FSLess, not raw bytes: the names came out of a directory, so they can
	// be non-UTF-8, and Python compares the surrogateescape-DECODED code
	// points. `sort.StringSlice` compares bytes. This shipped as the byte
	// spelling and was found by widening the class guard from the one
	// spelling the last fix happened to touch (adversarial r6, MEDIUM).
	sort.SliceStable(names, func(i, j int) bool {
		return pypath.FSLess(names[j], names[i]) // reverse=True
	})

	records := []any{}
	unreadable := 0
	for _, n := range names {
		raw, rerr := os.ReadFile(filepath.Join(dir, n))
		if rerr != nil {
			unreadable++
			continue
		}
		// The lenient read, within what a Go string can hold: valid UTF-8
		// parses, anything else is counted rather than laundered. See the
		// named divergence above.
		text, derr := pyval.DecodeUTF8Strict(raw)
		if derr != nil {
			unreadable++
			continue
		}
		v, perr := pyval.LoadsOrdered(text)
		if perr != nil {
			unreadable++
			continue
		}
		// Appended whatever it decoded, mapping or not — Python's `records
		// .append(json.loads(...))` has no shape check, and a reader that
		// added one would return a shorter list than the other runtime.
		records = append(records, v)
	}

	var warning string
	if unreadable > 0 {
		// Python's sentence, verbatim, so an operator grepping either
		// runtime's output finds both.
		warning = fmt.Sprintf("[skills] load_skill_provenance: %d provenance "+
			"file(s) for %s are unreadable or malformed — skipped (%s)",
			unreadable, skillName, dir)
	}
	if pytext.HasGlobMeta(skillName) {
		// Additive, and deliberately not a refusal: the records returned
		// above are the ones Python returns.
		m := fmt.Sprintf("[skills] load_skill_provenance: skill name %s "+
			"carries a glob metacharacter, so this lookup may match other "+
			"skills' records and miss its own — Python interpolates the name "+
			"into the pattern unescaped and this port matches it",
			pyRepr(skillName))
		if warning == "" {
			warning = m
		} else {
			warning += "\n" + m
		}
	}
	return ProvenanceLoad{Records: records, Warning: warning}
}
