package skills

import (
	"context"
	"fmt"

	"github.com/slycrel/maro-orchestration/go/internal/llm"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Lifecycle thresholds (skills.py module level).
const (
	AutoPromoteMinUses = 5    // minimum uses before auto-promotion considered
	AutoPromoteMinRate = 0.70 // utility floor for auto-promotion
	RewriteTriggerRate = 0.40 // utility below this triggers rewrite/demote
	RewriteMinUses     = 3    // minimum uses before either fires
)

// rewriteTriggerRateStr is how Python's f-string spells the constant inside
// a stored provenance reason ("< 0.4", not "< 0.400000"). The reason is
// prose in a shared store, so the spelling is part of the row.
const rewriteTriggerRateStr = "0.4"

// observedUses is the uses gate both sweeps share: the live SkillStats
// counter, max'd with the legacy Skill.use_count.
//
// use_count alone is a dead gate — its only writer was removed as dead code
// in Python on 2026-07-29, which left this reading a permanently-zero
// counter: no skill was promoted for eight weeks while the store grew to
// 376 provisionals, 134 of them use-eligible on real stats. The max() keeps
// old stores with real counts working.
func observedUses(s Skill, st SkillStats, haveStats bool) int {
	uses := s.UseCount
	if haveStats && st.TotalUses > uses {
		uses = st.TotalUses
	}
	return uses
}

// PromotionReport is one auto-promotion sweep's outcome.
type PromotionReport struct {
	PromotedIDs []string
	Held        map[string]string // id → why it was held
	Warnings    []string
}

// MaybeAutoPromoteSkills promotes provisional skills that meet the quality
// bar to established, with no LLM validation harness.
//
// This is Python's adapter=None path, spelled as its own function because
// that is what every caller in the port passes today. The harness lives in
// MaybeAutoPromoteSkillsWithAdapter, which this delegates to — one body, so
// the two cannot drift the way two copies of one sweep would.
//
// The `limit` caps CANDIDATES, not successes: a pool of never-passing
// provisionals must not turn the cap into unbounded work every sweep.
//
// Verdict-grounded evidence can VETO. The legacy counters credit
// keyword-matched bystanders at a ~1.0 base rate, so a skill whose
// actually-injected runs fail is held even when the inflated counters look
// good.
//
// The tier change lands on a FRESH reload inside the store lock. Writing
// this sweep's pre-sweep list back would revert any concurrent mutation
// since the load, and the lock has to span reload→save because SaveSkills'
// own lock guards only the write — a mutation landing inside that window
// would be dropped by the full rewrite.
func MaybeAutoPromoteSkills(ws string, limit int, rec *record.Recorder) (PromotionReport, error) {
	return MaybeAutoPromoteSkillsWithAdapter(context.Background(), ws, limit,
		DefaultMaxRepairAttempts, rec, nil)
}

// DefaultMaxRepairAttempts is Python's max_repair_attempts default.
const DefaultMaxRepairAttempts = 3

// MaybeAutoPromoteSkillsWithAdapter is the sweep with the Voyager-style
// validation harness wired in.
//
// For each candidate that clears the numeric gates, the harness validates,
// and on a failure sends the skill through evolver.rewrite_skill (in place,
// so the repair persists) and validates again — up to maxRepairAttempts
// times. A skill that never validates stays provisional.
//
// # Three things about the loop that are easy to get wrong
//
// The LAST attempt still spends a rewrite whose result is never validated:
// the loop is `for attempt in range(n)` with the rewrite at the bottom, so
// attempt n-1 validates, fails, rewrites, and falls out. That is real LLM
// spend on a skill this sweep has already given up on, and it is Python's
// behaviour, so it is this port's.
//
// maxRepairAttempts <= 0 with an adapter present promotes NOTHING — the loop
// body never runs, `valid` stays false, and every candidate is held. It is
// not clamped to 1 here, because a caller that passes 0 gets the same answer
// from both runtimes.
//
// A rewrite that RAISES and a rewrite that returns None both stop the loop,
// but they are different events: Python catches the raise in a bare except
// around the call and breaks. The port's RewriteSkill returns an error for
// exactly the cases Python raises, and this treats both as break.
func MaybeAutoPromoteSkillsWithAdapter(ctx context.Context, ws string, limit,
	maxRepairAttempts int, rec *record.Recorder,
	adapter llm.Adapter) (PromotionReport, error) {
	rep := PromotionReport{Held: map[string]string{}}
	if limit <= 0 {
		limit = 10
	}
	load := LoadSkills(ws)
	rep.Warnings = append(rep.Warnings, load.Announce()...)

	statsByID := map[string]SkillStats{}
	for _, st := range GetAllSkillStats(ws) {
		statsByID[st.SkillID] = st
	}

	type promotion struct {
		skill      Skill
		uses       int
		evidence   string
		validation string
	}
	var chosen []promotion
	examined := 0
	for _, s := range load.Skills {
		if examined >= limit {
			break
		}
		if s.Tier != "provisional" {
			continue
		}
		st, haveStats := statsByID[s.ID]
		uses := observedUses(s, st, haveStats)
		if uses < AutoPromoteMinUses {
			continue
		}
		if s.UtilityScore < AutoPromoteMinRate {
			continue
		}
		evidence := "legacy-only"
		if haveStats && st.InjectedRuns > 0 {
			if st.InjectedSuccessRate < AutoPromoteMinRate {
				rep.Held[s.ID] = fmt.Sprintf("injected evidence (%d runs, rate %.2f) "+
					"contradicts legacy counters", st.InjectedRuns, st.InjectedSuccessRate)
				continue
			}
			evidence = "injected-confirmed"
		}
		examined++

		// "skipped" is the honest word for "no adapter, so validation never
		// ran" — distinct from "passed" (the model judged it) and from
		// "unjudged" (the fail-open default let it through). The promotion
		// event carries whichever one happened.
		validation := "skipped"
		if adapter != nil {
			candidate := s
			valid := false
			for attempt := 0; attempt < maxRepairAttempts; attempt++ {
				res := ValidateSkillForPromotion(ctx, candidate, adapter)
				if res.Valid {
					valid = true
					validation = "passed"
					if !res.Judged {
						validation = "unjudged"
					}
					break
				}
				rep.Warnings = append(rep.Warnings, fmt.Sprintf(
					"skill %s failed validation (attempt %d/%d): %s — rewriting",
					s.ID, attempt+1, maxRepairAttempts, res.Reason))
				repaired, rwarns, rerr := RewriteSkill(ctx, ws, candidate, adapter,
					RewriteOptions{InPlace: true, Rec: rec})
				rep.Warnings = append(rep.Warnings, rwarns...)
				if rerr != nil || repaired == nil {
					break
				}
				candidate = *repaired
			}
			if !valid {
				rep.Held[s.ID] = fmt.Sprintf(
					"held at provisional after %d repair attempt(s)", maxRepairAttempts)
				continue
			}
		}

		chosen = append(chosen, promotion{skill: s, uses: uses,
			evidence: evidence, validation: validation})
	}
	if len(chosen) == 0 {
		return rep, nil
	}

	wanted := NewIDSet()
	for _, p := range chosen {
		wanted[p.skill.ID] = true
		rep.PromotedIDs = append(rep.PromotedIDs, p.skill.ID)
	}
	path := skillsPath(ws)
	// Captured in-lock, exported after the lock is released: the export
	// writes a DIFFERENT file, so holding the pool lock across it would
	// widen the store's critical section for no protection at all. Python
	// exports outside its `with _locked_write(...)` block for the same
	// reason, reusing the `fresh` list it built inside.
	promotedFresh := map[string]Skill{}
	err := record.Locked(path, func() error {
		fresh := LoadSkills(ws).Skills
		named := NewIDSet()
		byID := map[string]Skill{}
		for i := range fresh {
			if wanted[fresh[i].ID] {
				fresh[i].Tier = "established"
				fresh[i].ContentHash = ComputeSkillHash(fresh[i])
				named[fresh[i].ID] = true
				promotedFresh[fresh[i].ID] = fresh[i]
			}
			byID[fresh[i].ID] = fresh[i]
		}
		// The IN-LOCK core: record.Locked is not reentrant (flock is
		// per-open-file-description, so nesting blocks against itself), and
		// the lock has to span reload→rewrite or a concurrent mutation
		// landing in that window is dropped by the full rewrite.
		// `named` is intersected with the FRESH pool by construction, so a
		// skill retired since the sweep's read is not resurrected.
		warns, err := saveSkillsInLock(path, fresh, byID, NewIDSet(), named)
		rep.Warnings = append(rep.Warnings, warns...)
		return err
	})
	if err != nil {
		return rep, err
	}

	// The SKILL.md overlay export. Python runs this over the same `fresh`
	// pool right after the tier write, and swallows every failure — the
	// export is optional and must never block a promotion that already
	// landed in the store. Same posture here, except the failure becomes a
	// warning rather than nothing: a silently-absent overlay file is
	// exactly the divergence this was added to close (adversarial r4, M3).
	//
	// The skill is exported at its POST-promotion tier, so the markdown
	// reads "tier: established" — it is written from the fresh pool copy,
	// not from the pre-promotion candidate.
	for _, p := range chosen {
		s, ok := promotedFresh[p.skill.ID]
		if !ok {
			continue
		}
		if _, exErr := ExportSkillAsMarkdown(ws, s, false); exErr != nil {
			rep.Warnings = append(rep.Warnings,
				"SKILL.md export failed for "+p.skill.ID+": "+exErr.Error())
		}
	}

	for _, p := range chosen {
		if rec == nil {
			break
		}
		summary := fmt.Sprintf("Promoted provisional -> established. Utility: %.2f over %d uses.",
			p.skill.UtilityScore, p.uses)
		if evErr := rec.EventRelated("SKILL_PROMOTED", p.skill.Name, summary, map[string]any{
			"skill_id": p.skill.ID, "utility": round3(p.skill.UtilityScore),
			"use_count": p.uses, "validation": p.validation, "evidence": p.evidence,
		}, "", []string{"skill:" + p.skill.ID}); evErr != nil {
			rep.Warnings = append(rep.Warnings,
				"captain's log write failed (SKILL_PROMOTED): "+evErr.Error())
		}
	}
	return rep, nil
}

// DemotionReport is one demotion sweep's outcome.
type DemotionReport struct {
	DemotedIDs []string
	Warnings   []string
}

// MaybeDemoteSkills sends established skills with persistently low utility
// back to provisional.
//
// Demotion needs SUSTAINED evidence, not a blip: enough observed uses to
// trust the score, and then either a tripped circuit (consecutive failures)
// or an EMA that has settled below the rewrite trigger.
func MaybeDemoteSkills(ws string, rec *record.Recorder) (DemotionReport, error) {
	rep := DemotionReport{}
	load := LoadSkills(ws)
	rep.Warnings = append(rep.Warnings, load.Announce()...)
	pool := load.Skills

	statsByID := map[string]SkillStats{}
	for _, st := range GetAllSkillStats(ws) {
		statsByID[st.SkillID] = st
	}

	demoted := NewIDSet()
	var records []Skill
	for i := range pool {
		s := pool[i]
		if s.Tier != "established" {
			continue
		}
		st, haveStats := statsByID[s.ID]
		if observedUses(s, st, haveStats) < RewriteMinUses {
			continue
		}
		if s.CircuitState != "open" && s.UtilityScore >= RewriteTriggerRate {
			continue
		}
		pool[i].Tier = "provisional"
		pool[i].ContentHash = ComputeSkillHash(pool[i])
		demoted[s.ID] = true
		rep.DemotedIDs = append(rep.DemotedIDs, s.ID)
		records = append(records, pool[i])
	}
	if len(demoted) == 0 {
		return rep, nil
	}
	warns, err := SaveSkills(ws, pool, NewIDSet(), demoted)
	rep.Warnings = append(rep.Warnings, warns...)
	if err != nil {
		return rep, err
	}

	for _, s := range records {
		reason := demotionReason(s)
		if rec != nil {
			if evErr := rec.EventRelated("SKILL_DEMOTED", s.Name,
				"Demoted established -> provisional. "+reason+".", map[string]any{
					"skill_id": s.ID, "utility": round3(s.UtilityScore),
					"circuit_state": s.CircuitState,
				}, "", []string{"skill:" + s.ID}); evErr != nil {
				rep.Warnings = append(rep.Warnings,
					"captain's log write failed (SKILL_DEMOTED): "+evErr.Error())
			}
		}
		if perr := WriteSkillProvenance(ws, ProvenanceRecord{
			SkillName: s.Name, Decision: "demote", Reason: reason,
			SuccessRate: s.SuccessRate, EfficiencyScore: 0.0,
			SourceLoopIDs: s.SourceLoopIDs,
			Extra: []ProvenanceField{
				{"utility_score", s.UtilityScore},
				{"circuit_state", s.CircuitState},
			},
		}); perr != nil {
			rep.Warnings = append(rep.Warnings,
				"skill provenance write failed for "+s.ID+": "+perr.Error())
		}
	}
	return rep, nil
}

// demotionReason is the stored prose, spelled exactly as Python's f-string
// spells it — this text lands in a shared provenance store.
func demotionReason(s Skill) string {
	if s.CircuitState == "open" {
		return "circuit breaker open (sustained failures)"
	}
	return fmt.Sprintf("utility_score=%.3f < %s", s.UtilityScore, rewriteTriggerRateStr)
}

// SkillsNeedingRewrite returns skills eligible for LLM rewriting.
//
// The circuit must be OPEN — sustained consecutive failures, or a failure
// during recovery — so a transient error cannot spend a rewrite. Then
// either the EMA confirms persistent underperformance OR the failure streak
// is structural, because the EMA lags a sudden break.
func SkillsNeedingRewrite(ws string) []Skill {
	statsByID := map[string]SkillStats{}
	for _, st := range GetAllSkillStats(ws) {
		statsByID[st.SkillID] = st
	}
	var out []Skill
	for _, s := range LoadSkills(ws).Skills {
		if s.CircuitState != "open" {
			continue
		}
		st, haveStats := statsByID[s.ID]
		if observedUses(s, st, haveStats) < RewriteMinUses {
			continue
		}
		if s.UtilityScore < RewriteTriggerRate ||
			s.ConsecutiveFailures >= CircuitOpenThreshold {
			out = append(out, s)
		}
	}
	return out
}
