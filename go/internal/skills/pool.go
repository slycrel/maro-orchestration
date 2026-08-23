package skills

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// IDSet is a named set of skill ids, used to make a bulk rewrite's INTENT
// explicit.
type IDSet map[string]bool

// NewIDSet builds a set from ids.
func NewIDSet(ids ...string) IDSet {
	s := IDSet{}
	for _, id := range ids {
		s[id] = true
	}
	return s
}

func (s IDSet) sorted() []string {
	out := make([]string, 0, len(s))
	for id := range s {
		out = append(out, id)
	}
	sortStrings(out)
	return out
}

// SaveSkills overwrites the live pool from a caller's list, carrying
// strandees, and returns what the rewrite ACTUALLY did.
//
// This is the destructive rewrite of the skill pool, and it carries six
// adversarial rounds of Python's arc (r16–r21). The contract is the whole
// point:
//
//   - A deliberate DROP must be NAMED. Every caller builds its list from an
//     UNLOCKED load, and reading "proven row absent from the list" as
//     "deliberately deleted" silently destroyed any skill a concurrent
//     process saved between the caller's read and this rewrite — with no
//     archive copy. Absence now means CARRY.
//   - A deliberate WRITE must be NAMED too. Naming only drops still let a
//     row present in the caller's STALE snapshot replace the live row
//     wholesale, so a concurrent save was reverted by any unrelated caller
//     that loaded before it and saved after it. Only an id in updatedIDs
//     takes the caller's version; every other live row is carried verbatim,
//     IN PLACE.
//   - Naming is not creation. A named id with no live row is a lost race
//     with a deliberate drop (cull, retirement, rollback); appending it
//     resurrected the retired row, with none of the retirement's reasoning.
//     Creation is SaveSkill's job, so a named-but-absent write is dropped
//     and ANNOUNCED, and the deletion stands.
//   - The caller's list came from a load, which cannot represent a row it
//     could not parse — so a naive full rewrite from that list DELETES
//     every torn line in the store. The store is re-read under the lock and
//     every line the list cannot account for rides through verbatim.
//   - Ordinals are held. This store is read last-row-wins by id, so
//     appending rewritten rows after survivors could promote a carried row
//     over a live skill purely by moving it.
//
// Residual, recorded: an id in droppedIDs or updatedIDs whose row was
// revised after the caller's snapshot still loses that revision — naming an
// id claims it. Upgrade edge: a transform-style primitive that re-derives
// the mutation inside this lock.
//
// Announcements are returned rather than logged so the caller's warning
// rail carries them, and they describe what the WRITE did, only after its
// commit.
func SaveSkills(ws string, skills []Skill, droppedIDs, updatedIDs IDSet) ([]string, error) {
	if droppedIDs == nil {
		droppedIDs = IDSet{}
	}
	if updatedIDs == nil {
		updatedIDs = IDSet{}
	}
	// Contradictory intent is a caller bug — refused BEFORE the lock, store
	// untouched. An id both dropped and updated, an id "updated" the
	// caller's own list does not hold, or an id dropped while still in that
	// list has no honest interpretation.
	listIDs := IDSet{}
	for _, s := range skills {
		listIDs[s.ID] = true
	}
	if both := intersect(updatedIDs, droppedIDs); len(both) > 0 {
		return nil, fmt.Errorf("SaveSkills: id(s) named both updated and dropped: %v",
			both.sorted())
	}
	if missing := subtract(updatedIDs, listIDs); len(missing) > 0 {
		return nil, fmt.Errorf("SaveSkills: updated id(s) absent from the caller's list: %v",
			missing.sorted())
	}
	if still := intersect(droppedIDs, listIDs); len(still) > 0 {
		return nil, fmt.Errorf("SaveSkills: dropped id(s) still present in the caller's list: %v",
			still.sorted())
	}

	byID := map[string]Skill{}
	for _, s := range skills {
		byID[s.ID] = s
	}
	path := skillsPath(ws)
	if err := os.MkdirAll(filepath.Dir(path), record.NewDirMode); err != nil {
		return nil, err
	}

	// require semantics: this is THE destructive rewrite of the pool, and a
	// fail-open lock would let two writers race it.
	var announce []string
	err := record.Locked(path, func() error {
		var innerErr error
		announce, innerErr = saveSkillsInLock(path, skills, byID, droppedIDs, updatedIDs)
		return innerErr
	})
	if err != nil {
		// Name the store and RETURN the error: the warn-and-return-nothing
		// shape let a cull report "retired" while every skill remained live.
		return announce, fmt.Errorf("skills pool rewrite NOT performed (%s): %w",
			path, err)
	}
	return announce, nil
}

// saveSkillsInLock is SaveSkills' body, with the store lock ALREADY HELD.
//
// It is separate because record.Locked is NOT reentrant: it flocks a fresh
// descriptor per call, and POSIX flock is per open-file-description, so a
// nested call from the same process blocks against itself until the lock
// times out. Callers that must span reload→rewrite atomically (the
// promotion sweep) take the lock themselves and call this.
func saveSkillsInLock(path string, skills []Skill, byID map[string]Skill,
	droppedIDs, updatedIDs IDSet) ([]string, error) {
	var announce []string
	err := func() error {
		var out []*string // nil marks a slot a named write will fill
		slot := map[string]int{}
		droppedSeen := IDSet{}
		divergent := IDSet{}
		strandIDs := IDSet{}
		droppedRows, compacted, tainted, unprovable, unprovableUnnamed := 0, 0, 0, 0, 0

		raw, err := os.ReadFile(path)
		if err != nil && !os.IsNotExist(err) {
			return err
		}
		// Split the RAW text: a strip can make a copy parse when the row
		// does not, and "verbatim" that strips is not verbatim.
		for _, line := range strings.Split(string(raw), "\n") {
			if record.IsFrameBlank(line) {
				continue
			}
			carried := line
			d, err := record.LoadsClean(line)
			if err != nil {
				tainted++
				out = append(out, &carried)
				continue
			}
			row, err := ValidateSkillRow(d)
			if err != nil {
				// The row failed the PROOF, but its declared id may still
				// parse — recovered so a NAMED write against it can be
				// announced as "present but unprovable" rather than
				// "concurrently removed", which would send an operator
				// hunting a deletion that never happened.
				unprovable++
				if sid, ok := d["id"].(string); ok && sid != "" {
					strandIDs[sid] = true
				} else {
					// No recoverable id — this row could be ANY named id,
					// so every completeness claim below must hedge on it.
					unprovableUnnamed++
				}
				out = append(out, &carried)
				continue
			}
			switch {
			case droppedIDs[row.ID]:
				// A NAMED deliberate drop — that IS a decision. Physical
				// rows are counted apart from ids: a legacy store holding
				// duplicate rows for a dropped id used to announce fewer
				// removals than it performed.
				droppedSeen[row.ID] = true
				droppedRows++
			case updatedIDs[row.ID]:
				if _, seen := slot[row.ID]; seen {
					compacted++
				}
				slot[row.ID] = len(out)
				out = append(out, nil)
			default:
				// Absent from the caller's list, or present but NOT named:
				// the live row is at least as fresh as the caller's stale
				// copy, so it is carried verbatim, holding its ordinal.
				//
				// A caller that MUTATED an unnamed copy gets a warning, not
				// silence — but divergence has TWO causes this function
				// cannot tell apart: a forgotten edit, and a concurrent
				// named write that legitimately moved the live row after
				// the snapshot. The announcement names both; asserting "the
				// caller's edit" would be a lie under load, and a lying
				// warning trains operators to ignore the honest one.
				// content_hash is excluded: it is derived, and a
				// not-yet-backfilled empty hash is not an edit.
				if cand, ok := byID[row.ID]; ok && !divergent[row.ID] {
					if skillDiffersIgnoringHash(cand, row) {
						divergent[row.ID] = true
					}
				}
				out = append(out, &carried)
			}
		}

		// A writer must not emit a row it would itself refuse: content_hash
		// is one of the fields ValidateSkillRow requires, and a Skill built
		// in memory (a cull pool, say) carries none — without this the store
		// would fill with rows that can never be removed again. Scoped to
		// NAMED writes: everything else is carried verbatim from disk, so
		// backfilling an unnamed copy would mutate an object whose hash the
		// store will never hold.
		for i := range skills {
			if updatedIDs[skills[i].ID] && skills[i].ContentHash == "" {
				skills[i].ContentHash = ComputeSkillHash(skills[i])
				byID[skills[i].ID] = skills[i]
			}
		}
		for sid, i := range slot {
			line, err := proveLine(byID[sid])
			if err != nil {
				return err
			}
			out[i] = &line
		}

		var strandedNamed, ghostIDs []string
		for _, sid := range updatedIDs.sorted() {
			if _, placed := slot[sid]; placed {
				continue
			}
			if strandIDs[sid] {
				strandedNamed = append(strandedNamed, sid)
			} else {
				ghostIDs = append(ghostIDs, sid)
			}
		}
		var strandedDropped, partiallyDropped, unaccountedDropped []string
		for _, sid := range droppedIDs.sorted() {
			switch {
			case strandIDs[sid] && !droppedSeen[sid]:
				// The drop twin: the dropped branch is only reachable for
				// PROVABLE rows, so a named drop whose live row fails the
				// proof used to silently no-op — the cull returned clean and
				// the row survived.
				strandedDropped = append(strandedDropped, sid)
			case strandIDs[sid] && droppedSeen[sid]:
				partiallyDropped = append(partiallyDropped, sid)
			case !droppedSeen[sid]:
				unaccountedDropped = append(unaccountedDropped, sid)
			}
		}

		// Python writes `"\n".join(live) + "\n"`, so a rewrite that drops
		// the LAST skill leaves a store holding a single newline, not an
		// empty file (adversarial r4, L4). Both read back as an empty
		// pool, but the bytes are what a cross-runtime differential
		// compares, and a one-byte diff on an empty store is the kind of
		// thing that gets chased for an hour on the day it appears.
		var sb strings.Builder
		wrote := false
		for _, l := range out {
			if l == nil {
				continue // a named write with no live row: the deletion stands
			}
			if wrote {
				sb.WriteByte('\n')
			}
			sb.WriteString(*l)
			wrote = true
		}
		sb.WriteByte('\n')
		if err := record.AtomicWrite(path, []byte(sb.String())); err != nil {
			return err
		}

		// Announce AFTER the commit: the carried-verbatim warning used to
		// precede the write, so a failed rewrite left a claim that rows were
		// carried through a rewrite that never happened.
		unreadable := tainted + unprovableUnnamed
		hedge := ""
		if unreadable > 0 {
			hedge = fmt.Sprintf("; %d row(s) carried verbatim whose id could "+
				"not be read may still hold copies", unreadable)
		}
		if tainted > 0 || unprovable > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d "+
				"unparseable/byte-tainted and %d unprovable row(s) carried "+
				"through the rewrite verbatim (%s)", tainted, unprovable, path))
		}
		if compacted > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d older "+
				"duplicate row(s) for updated id(s) compacted by this "+
				"rewrite — last row per id won (%s)", compacted, path))
		}
		if len(droppedSeen) > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d physical "+
				"row(s) for %d named id(s) removed by this rewrite%s (%s): %v",
				droppedRows, len(droppedSeen), hedge, path, droppedSeen.sorted()))
		}
		if len(strandedDropped) > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d named "+
				"drop(s) NOT applied — the live row(s) for these id(s) are "+
				"present but unprovable, carried verbatim; the row was NOT "+
				"removed; repair, then confirm the drop (%s): %v",
				len(strandedDropped), path, strandedDropped))
		}
		if len(partiallyDropped) > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d named "+
				"drop(s) removed the provable row(s), but unprovable "+
				"duplicate row(s) for these id(s) remain in the store, "+
				"carried verbatim (%s): %v",
				len(partiallyDropped), path, partiallyDropped))
		}
		if len(unaccountedDropped) > 0 && unreadable > 0 {
			// With no unreadable rows, absence IS proven and the drop is
			// vacuously satisfied — silence is honest.
			announce = append(announce, fmt.Sprintf("skills pool: %d named "+
				"drop(s) could NOT be verified — no parseable live row holds "+
				"these id(s), but %d row(s) carried verbatim whose id could "+
				"not be read may still hold them; if one does, that row was "+
				"NOT removed — repair, then confirm the drop (%s): %v",
				len(unaccountedDropped), unreadable, path, unaccountedDropped))
		}
		if len(strandedNamed) > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d named "+
				"write(s) NOT applied — the live row(s) for these id(s) are "+
				"present but unprovable, carried verbatim; repair and retry "+
				"(%s): %v", len(strandedNamed), path, strandedNamed))
		}
		if len(ghostIDs) > 0 {
			ghostHedge := ""
			if unreadable > 0 {
				ghostHedge = fmt.Sprintf(", or held by one of the %d row(s) "+
					"carried verbatim whose id could not be read", unreadable)
			}
			announce = append(announce, fmt.Sprintf("skills pool: %d named "+
				"write(s) NOT applied — no parseable live row holds these "+
				"id(s) (concurrently removed or never created%s); nothing was "+
				"written for them and nothing was removed by this refusal "+
				"(%s): %v", len(ghostIDs), ghostHedge, path, ghostIDs))
		}
		if len(divergent) > 0 {
			announce = append(announce, fmt.Sprintf("skills pool: %d unnamed "+
				"row(s) in the caller's list differ from the live store — "+
				"either an unnamed edit was discarded, or a concurrent write "+
				"legitimately moved the row after the caller's snapshot; the "+
				"live row was carried either way (%s): %v",
				len(divergent), path, divergent.sorted()))
		}
		return nil
	}()
	return announce, err
}

// skillDiffersIgnoringHash compares two skills the way the divergence check
// does: everything the row says the skill DOES, minus the derived hash.
func skillDiffersIgnoringHash(a, b Skill) bool {
	a.ContentHash, b.ContentHash = "", ""
	al, _, err1 := proveRecordLine(a)
	bl, _, err2 := proveRecordLine(b)
	if err1 != nil || err2 != nil {
		return true // unrenderable is different by definition
	}
	return al != bl
}

func intersect(a, b IDSet) IDSet {
	out := IDSet{}
	for id := range a {
		if b[id] {
			out[id] = true
		}
	}
	return out
}

func subtract(a, b IDSet) IDSet {
	out := IDSet{}
	for id := range a {
		if !b[id] {
			out[id] = true
		}
	}
	return out
}
