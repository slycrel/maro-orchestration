package skills

import (
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// MinVariantUses is MIN_VARIANT_USES: the minimum wins+losses per side
// before a pair is eligible for retirement.
const MinVariantUses = 5

// CreateSkillVariant marks a rewritten skill as a challenger variant of the
// original. The variant competes against the original in live routing, and
// NEITHER is discarded until RetireLosingVariants has enough evidence.
//
// The id check is not a formality. A skill marked as its own variant makes
// the A/B armless, and "retiring" that challenger would archive-and-delete
// the parent — which HAPPENED: rewrite_skill used to mutate in place and
// return the parent, so every pre-2026-08-06 variant was self-referential.
// RetireLosingVariants still heals those rows on sight; this refuses to mint
// new ones.
//
// rec may be nil. Python wraps the captain's-log write in a bare except, so
// a logging failure never costs the caller its variant.
//
// rewritten is a POINTER because Python mutates the caller's object and
// returns that same object — `rewritten.variant_of = original.id` is an
// attribute assignment on the argument, not a copy. Taking it by value made
// the return the only channel, so a caller that followed Python's shape and
// used its own `rewritten` afterwards held an UNMARKED skill: variant_of
// nil, wins and losses whatever they were. It would then be saved into the
// pool as a plain skill, and RetireLosingVariants — which groups on
// variant_of — would never see the A/B pair at all. Nothing in the port
// calls this yet, which is why the signature could still be fixed rather
// than worked around.
func CreateSkillVariant(original Skill, rewritten *Skill,
	rec *record.Recorder) (Skill, error) {
	if rewritten.ID == original.ID {
		return Skill{}, &pyval.PyErr{Class: "ValueError", Msg: fmt.Sprintf(
			"challenger id equals parent id (%s); pass a distinct rewritten "+
				"skill (rewrite_skill in_place=False)", original.ID)}
	}
	parentID := original.ID
	rewritten.VariantOf = &parentID
	rewritten.VariantWins = 0
	rewritten.VariantLosses = 0
	if rec != nil {
		// `rewritten.id[:8]` — a slice, not a fixed width, so an id shorter
		// than eight characters is itself rather than a padded one.
		_ = rec.EventRelated("SKILL_VARIANT_CREATED", original.Name,
			fmt.Sprintf("A/B challenger %s created for parent %s.",
				pyval.Clip(rewritten.ID, 8), pyval.Clip(original.ID, 8)),
			map[string]any{"parent_id": original.ID,
				"challenger_id": rewritten.ID}, "",
			[]string{"skill:" + original.ID, "skill:" + rewritten.ID})
	}
	return *rewritten, nil
}

// RetireReport is retire_losing_variants' return dict.
type RetireReport struct {
	Promoted []string `json:"promoted"`
	Retired  []string `json:"retired"`
}

// RetireLosingVariants evaluates every active A/B pair and retires the
// losers. Retired variants are ARCHIVED, never deleted — the retention
// decree: retirement moves a skill out of the live pool, it does not destroy
// the record.
//
// For each (parent, challengers) group: act only when BOTH sides have at
// least minUses trials; the challenger wins on a strictly higher rate, so a
// TIE goes to the parent; a winning challenger's content is copied into the
// parent and the challenger is retired either way.
//
// # The parent's trial count comes from two places
//
// Skill.use_count is legacy-frozen (its writer was removed 2026-07-29), so
// the live count lives in SkillStats and the two are max'd for stores
// written before the change. A port that read only use_count would find
// every modern parent at zero trials and never retire anything.
//
// # An ordering divergence, named rather than papered over
//
// Python groups by `{s.variant_of for s in skills if s.variant_of}` — a SET,
// whose iteration order depends on str hashing and therefore on
// PYTHONHASHSEED. The `promoted` and `retired` lists it returns are in that
// order, so CPython does not agree with ITSELF across runs. This port
// iterates parents in first-appearance order, which is deterministic and
// inside the set of orders CPython can produce. The differential compares
// the two as sets for that reason.
//
// It used to end "and the archive/save effects do not depend on order at
// all". That sentence was false, and the case it is false for is a VARIANT
// CHAIN — p1 <- c1 <- c2, where both links win:
//
//	group p1 first: p1 takes c1's ORIGINAL content, then c1 takes c2's.
//	group c1 first: c1 takes c2's content, then p1 takes THAT.
//
// The challengers are pointers into one pool, so the second group sees the
// first group's write. p1 is the row that SURVIVES, and its content — and
// therefore its content_hash, which is recomputed from it — depends on
// which parent id the set happened to yield first. Measured on this box:
// 3 of 8 CPython runs disagreed with the other 5 on p1's stored content.
//
// c1's archived row does NOT depend on order (its own group always runs),
// and neither does either id list once compared as a set. The surviving
// root's CONTENT is the whole of the exposure, and both runtimes have it.
//
// This is not a defect the port can fix on its own side: CPython is the
// runtime being ported and CPython is ambiguous here. So the port picks the
// deterministic order and the differential PROVES the containment claim
// rather than asserting it — for any fixture where a skill appears in both
// `promoted` and `retired` (the structural signature of a chain), the test
// runs CPython under several PYTHONHASHSEEDs, collects the distinct answers,
// and requires the port's answer to be one of them. See the seed sweep in
// variant_diff_test.go. Lens 19: the sentence claiming order-independence
// was the thing to check, not the code under it.
func RetireLosingVariants(ws string, dryRun bool, minUses int,
	rec *record.Recorder) (RetireReport, error) {
	res := LoadSkills(ws)
	pool := res.Skills

	// Heal self-referential variants before anything reads variant_of: a
	// self-variant can never be A/B'd, and acting on one archives the
	// parent. Clear the corrupt marker instead.
	healed := IDSet{}
	for i := range pool {
		if pool[i].VariantOf != nil && *pool[i].VariantOf == pool[i].ID {
			pool[i].VariantOf = nil
			healed[pool[i].ID] = true
		}
	}
	if len(healed) > 0 && !dryRun {
		if _, err := SaveSkills(ws, pool, nil, healed); err != nil {
			return RetireReport{}, err
		}
	}

	statsUses := map[string]int{}
	for _, st := range GetAllSkillStats(ws) {
		statsUses[st.SkillID] = st.TotalUses
	}

	byID := map[string]*Skill{}
	for i := range pool {
		byID[pool[i].ID] = &pool[i]
	}

	// First-appearance order — see the ordering note above.
	var parentIDs []string
	seen := map[string]bool{}
	for i := range pool {
		if v := pool[i].VariantOf; v != nil && *v != "" && !seen[*v] {
			seen[*v] = true
			parentIDs = append(parentIDs, *v)
		}
	}

	promoted := []string{}
	retired := []string{}
	for _, pid := range parentIDs {
		parent, ok := byID[pid]
		if !ok {
			continue // the parent was already removed
		}
		var challengers []*Skill
		for i := range pool {
			if v := pool[i].VariantOf; v != nil && *v == pid {
				challengers = append(challengers, &pool[i])
			}
		}
		if len(challengers) == 0 {
			continue
		}

		// The parent's rate is its utility_score — an EMA of real outcomes
		// — because parent wins/losses are not tracked separately.
		parentRate := parent.UtilityScore
		parentTrials := parent.UseCount
		if u := statsUses[parent.ID]; u > parentTrials {
			parentTrials = u
		}

		for _, ch := range challengers {
			total := ch.VariantWins + ch.VariantLosses
			if total < minUses || parentTrials < minUses {
				continue // not enough data yet
			}
			// `max(c_total, 1)` guards a zero denominator that the check
			// above already makes unreachable for minUses >= 1 — kept
			// because minUses is a PARAMETER and a caller may pass 0.
			denom := total
			if denom < 1 {
				denom = 1
			}
			cRate := float64(ch.VariantWins) / float64(denom)
			if cRate > parentRate {
				if !dryRun {
					parent.Description = ch.Description
					parent.StepsTemplate = ch.StepsTemplate
					parent.TriggerPatterns = ch.TriggerPatterns
					parent.OptimizationObjective = ch.OptimizationObjective
					parent.ContentHash = ComputeSkillHash(*parent)
				}
				promoted = append(promoted, parent.ID)
				retired = append(retired, ch.ID)
			} else {
				// A TIE goes to the parent: the comparison is strictly
				// greater, so an equal challenger is retired.
				retired = append(retired, ch.ID)
			}
		}
	}

	if dryRun || len(retired) == 0 {
		return RetireReport{Promoted: promoted, Retired: retired}, nil
	}

	retiredSet := map[string]bool{}
	for _, id := range retired {
		retiredSet[id] = true
	}
	var losers []Skill
	var kept []Skill
	for _, s := range pool {
		if retiredSet[s.ID] {
			losers = append(losers, s)
		} else {
			kept = append(kept, s)
		}
	}
	// ARCHIVE FIRST, then rewrite the pool without them. The order is the
	// retention decree: a crash between the two loses nothing, where the
	// reverse order loses the row.
	if err := ArchiveSkills(ws, losers, "ab_variant_retired"); err != nil {
		return RetireReport{}, err
	}
	dropped := IDSet{}
	for id := range retiredSet {
		dropped[id] = true
	}
	// updated_ids: a winning challenger's content was copied into its
	// PARENT above, and those parents are writes this save must name. A
	// parent that was ITSELF retired this pass (a variant chain) is a drop,
	// not a write — set difference, not union.
	updated := IDSet{}
	for _, id := range promoted {
		if !retiredSet[id] {
			updated[id] = true
		}
	}
	if _, err := SaveSkills(ws, kept, dropped, updated); err != nil {
		return RetireReport{}, err
	}

	for _, s := range losers {
		// Python's `f"...parent {s.variant_of}"` renders None as "None"
		// for a loser whose marker was cleared, which cannot happen here
		// (a healed row is no longer a variant) but is spelled the same
		// way rather than left to differ if it ever does.
		reason := "A/B variant lost against parent " + pyval.Str(pyOptStr(s.VariantOf))
		_ = WriteSkillProvenance(ws, ProvenanceRecord{
			SkillName: s.Name, Decision: "retire", Reason: reason,
			Extra: []ProvenanceField{
				{"skill_id", s.ID},
				{"variant_of", pyOptStr(s.VariantOf)},
				{"archived_to", "skills_archive.jsonl"},
			},
		})
	}

	if rec != nil {
		related := make([]string, 0, len(promoted)+len(retired))
		for _, id := range append(append([]string{}, promoted...), retired...) {
			related = append(related, "skill:"+id)
		}
		_ = rec.EventRelated("AB_RETIRED", joinComma(retired),
			fmt.Sprintf("A/B resolved: %d promoted, %d retired.",
				len(promoted), len(retired)),
			map[string]any{"promoted": promoted, "retired": retired},
			"", related)
	}
	return RetireReport{Promoted: promoted, Retired: retired}, nil
}

// pyOptStr renders an Optional[str] the way Python holds it: the string, or
// None. Returning `any` rather than "" keeps the None spelling available to
// pyval.Str and to a JSON writer, where "" would be a different value.
func pyOptStr(p *string) any {
	if p == nil {
		return nil
	}
	return *p
}

// joinComma is `", ".join(retired)`.
func joinComma(ss []string) string {
	out := ""
	for i, s := range ss {
		if i > 0 {
			out += ", "
		}
		out += s
	}
	return out
}
