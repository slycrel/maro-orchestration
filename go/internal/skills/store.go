package skills

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Store paths. The skill library lives in the shared workspace memory dir,
// so both runtimes read and write the same rows.
func skillsPath(ws string) string {
	return filepath.Join(ws, "memory", "skills.jsonl")
}

func skillsArchivePath(ws string) string {
	return filepath.Join(ws, "memory", "skills_archive.jsonl")
}

// LoadResult carries what a load threw away, so a short list is
// distinguishable from a short store (the announced-read posture: loss is
// reported, never silent).
type LoadResult struct {
	Skills       []Skill
	Drifted      int      // JSON, but not admissible as a Skill
	Unparseable  int      // not JSON at all, or byte-tainted
	HashMismatch []string // ids whose content no longer matches their stamp
	Unreadable   bool     // the store exists but could not be read at all
	Path         string
}

// Announce renders what the load LOST, for a caller that has a warning rail.
// An unannounced loss is the failure this whole posture exists to prevent:
// half a library going byte-tainted looks exactly like "nothing matched",
// which is also what a healthy cold store looks like.
func (r LoadResult) Announce() []string {
	var out []string
	if r.Unreadable {
		return append(out, fmt.Sprintf("skills: store exists but could not "+
			"be read; treating as empty for this run (%s)", r.Path))
	}
	if r.Unparseable > 0 || r.Drifted > 0 {
		out = append(out, fmt.Sprintf("skills: %d unparseable and %d "+
			"JSON-but-not-loadable row(s) excluded from this read; they "+
			"remain in the store verbatim (%s)",
			r.Unparseable, r.Drifted, r.Path))
	}
	if len(r.HashMismatch) > 0 {
		out = append(out, fmt.Sprintf("skills: content_hash mismatch on "+
			"%d skill(s) — possible tampering: %s",
			len(r.HashMismatch), strings.Join(r.HashMismatch, ", ")))
	}
	return out
}

// LoadSkills ports load_skills: every admissible skill in the store, oldest
// first, with the LAST row for an id winning.
//
// Three properties this shares with Python, each one a probed finding:
//
//   - The reader admits exactly what the writer proves (ValidateSkillRow,
//     not the tolerant constructor). A coercible-but-unprovable row used to
//     become a live Skill, and the next save then stranded the raw row AND
//     appended a normalized clone — the launder twin, minted from the gap
//     between two predicates.
//   - An id is claimed AFTER the proof, never before. A schema-drifted row
//     that claimed its id on the way past hid the newest WORKING row for
//     that id; worse, with the id claimed and the row skipped, the older
//     VALID row was in no caller's list and the next save deleted it as a
//     deliberate drop.
//   - A torn byte costs its row, not the load. Python's strict decode used
//     to raise UnicodeDecodeError into every caller of the skill library.
func LoadSkills(ws string) LoadResult {
	var res LoadResult
	res.Skills = []Skill{}
	res.Path = skillsPath(ws)
	raw, err := os.ReadFile(res.Path)
	if err != nil {
		// A missing store is an empty store; an UNREADABLE one is not.
		// Returning the same empty result for both made a permissions or
		// EIO failure byte-for-byte indistinguishable from a cold install,
		// so a library that had gone unreachable read as "no skills yet".
		res.Unreadable = !os.IsNotExist(err)
		return res
	}
	lines := strings.Split(string(raw), "\n")
	seen := map[string]bool{}
	// Reverse: the last version of each id wins.
	for i := len(lines) - 1; i >= 0; i-- {
		line := lines[i]
		if record.IsFrameBlank(line) {
			continue
		}
		row, err := record.LoadsClean(line)
		if err != nil {
			res.Unparseable++
			continue
		}
		sid, _ := row["id"].(string)
		if sid != "" && seen[sid] {
			continue
		}
		skill, err := ValidateSkillRow(row)
		if err != nil {
			res.Drifted++
			continue
		}
		seen[skill.ID] = true
		if stored := skill.ContentHash; stored != "" {
			if !VerifySkillHash(skill, stored) {
				res.HashMismatch = append(res.HashMismatch, skill.ID)
			}
		}
		res.Skills = append([]Skill{skill}, res.Skills...) // restore order
	}
	return res
}

// SaveSkill appends or replaces one skill, under the store lock.
//
// Unmatched lines are re-emitted VERBATIM. Python's older shape re-dumped
// every row (laundering byte-tainted ones), DELETED lines it could not
// parse, and — because the read was a strict whole-file decode — raised out
// of every save once one torn byte landed, write-locking the skill library
// until hand repair.
//
// A row that cannot be PROVEN to be a Skill is not a version of this skill,
// so it cannot be the thing this save replaces: `{"id":"same","operator_
// note":"keep this row"}` must survive a save of skill `same`.
// The skill is taken by POINTER so the caller sees the content_hash this
// computed — Python's save_skill mutates the dataclass in place and its
// callers depend on it. Taking a copy meant a caller that saved a
// freshly-minted skill and then recorded a manifest entry from it wrote an
// EMPTY content_hash: a provenance record that cannot be checked against
// the row it claims to describe.
func SaveSkill(ws string, s *Skill) error {
	s.ContentHash = ComputeSkillHash(*s)
	line, err := proveLine(*s) // refuse BEFORE the store is touched
	if err != nil {
		return err
	}
	path := skillsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	return record.LockedRMW(path, func(old string) string {
		var out []string
		for _, existing := range strings.Split(old, "\n") {
			// Carry the line as it is: a trim would remove Unicode
			// whitespace that JSON forbids, so the trimmed copy could
			// parse when the row does not — and this loop WRITES what it
			// carries, so the row's bytes would be rewritten by a save
			// that never claimed to touch them.
			if record.IsFrameBlank(existing) {
				continue
			}
			row, err := record.LoadsClean(existing)
			if err != nil {
				out = append(out, existing) // unprovable: carried, never matched
				continue
			}
			prior, err := ValidateSkillRow(row)
			if err != nil {
				out = append(out, existing)
				continue
			}
			if prior.ID == s.ID {
				continue // replaced below
			}
			out = append(out, existing)
		}
		out = append(out, line)
		return strings.Join(out, "\n") + "\n"
	})
}

// ArchiveSkills appends skills leaving the live pool to the archive.
//
// Retention decree: selection pressure (island culls, A/B retirement)
// removes skills from the live pool but never destroys them. Append-only,
// full record plus archived_at/archived_reason.
//
// Two properties the Python arc paid for: every line is BUILT AND PROVEN
// before any append (a refusal aborts the archive before it starts, and the
// error must abort the caller's live-pool removal too), and the whole batch
// lands in ONE append (per-line appends let a mid-batch failure land a
// partial batch, and the caller's retry then duplicated what had landed).
//
// Named divergence, honestly stated: Python's archive append is fsynced
// (durable=True) so the retention copy is on disk BEFORE the live-pool
// removal is allowed. record.AppendRawLine does not fsync, so a power loss
// between the archive and the removal can keep the deletion and lose the
// copy. Narrower than it sounds — the removal path is a separate later
// write — but it is a real gap, and it belongs to the shared append
// primitive, not to this caller.
func ArchiveSkills(ws string, toArchive []Skill, reason string) error {
	if len(toArchive) == 0 {
		return nil
	}
	// The port-wide stamp (record/scans/graduation), not RFC3339Nano:
	// Go's nano layout strips trailing zeros, so two stamps of the same
	// instant sort differently from Python's always-six-digit isoformat,
	// and the port's own parser does not accept the "Z" that layout emits.
	now := nowISO()
	var lines []string
	for _, s := range toArchive {
		line, _, err := proveRecordLine(s)
		if err != nil {
			return fmt.Errorf("archive refused (nothing written): %w", err)
		}
		// Stamp the two archive fields by EXTENDING the proven line rather
		// than re-marshalling the row: a map round-trip would reorder every
		// key alphabetically and re-serialize bytes this function just
		// proved. The proof must still cover what actually lands, so the
		// stamped line is re-checked against the reader's predicate.
		at, err := jsonString(now)
		if err != nil {
			return fmt.Errorf("archive refused (nothing written): %w", err)
		}
		why, err := jsonString(reason)
		if err != nil {
			return fmt.Errorf("archive refused (nothing written): %w", err)
		}
		if !strings.HasSuffix(line, "}") {
			return fmt.Errorf("archive refused (nothing written): proven line is not an object")
		}
		stamped := line[:len(line)-1] +
			",\"archived_at\":" + at + ",\"archived_reason\":" + why + "}"
		if _, err := record.LoadsClean(stamped); err != nil {
			return fmt.Errorf("archive refused (nothing written): %w", err)
		}
		lines = append(lines, stamped)
	}
	path := skillsArchivePath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return err
	}
	return record.AppendRawLine(path, []byte(strings.Join(lines, "\n")))
}
