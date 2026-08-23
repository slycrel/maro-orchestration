package skills

import (
	"fmt"
	"math"
	"sort"
	"strings"
)

// IslandDefault is the island a skill lands in when no keyword matched.
const IslandDefault = "general"

// AssignIsland classifies a skill into one of four islands
// (research | build | analysis | general) by keyword scoring over its
// trigger patterns, name, description, domain and tags. No LLM, no deps.
//
// The island with the most matching keywords wins; a tie goes to the first
// island in islandOrder — Python's max() over a dict returns the FIRST
// maximum in insertion order, and Go's map iteration is randomized, so the
// order is fixed explicitly or the same skill lands differently per run.
func AssignIsland(s Skill) string {
	parts := append([]string{}, s.TriggerPatterns...)
	parts = append(parts, s.Name, s.Description, s.Domain)
	parts = append(parts, s.Tags...)
	text := pyLower(strings.Join(parts, " "))

	best, bestScore := "", 0
	for _, isl := range islandOrder {
		score := 0
		for _, kw := range islandKeywords[isl] {
			if strings.Contains(text, kw) {
				score++
			}
		}
		if score > bestScore { // strict >: first max wins, like Python's max()
			best, bestScore = isl, score
		}
	}
	if bestScore > 0 {
		return best
	}
	return IslandDefault
}

// SkillsByIsland groups skills by island, assigning one to any skill that
// lacks it. The assignment is in-memory only — persisting it is the
// caller's decision, because persisting is a WRITE to a shared store.
func SkillsByIsland(pool []Skill) map[string][]Skill {
	out := map[string][]Skill{}
	for _, s := range pool {
		isl := s.Island
		if isl == "" {
			isl = AssignIsland(s)
		}
		out[isl] = append(out[isl], s)
	}
	return out
}

// compactnessAdjustedScore is skill_lifecycle._compactness_adjusted_score:
// utility penalized by length, so a compact skill beats a verbose one of
// equal utility. The max(penalty, 1.0) floor means a short skill is never
// boosted above its own utility.
func compactnessAdjustedScore(s Skill) float64 {
	chars := len(s.Description)
	for _, step := range s.StepsTemplate {
		chars += len(step)
	}
	penalty := math.Log(1.0 + float64(chars)/200.0)
	return s.UtilityScore / math.Max(penalty, 1.0)
}

// CullReport is what one island cull did, or would do.
type CullReport struct {
	CulledIDs []string
	DryRun    bool
	Warnings  []string
}

// CullIslandBottomHalf retires the bottom-performing half of one island's
// OPEN-CIRCUIT skills (FunSearch selection pressure).
//
// Two gates, both deliberate: an island smaller than minIslandSize is left
// alone (selection pressure on a tiny pool is just deletion), and only
// circuit_state=="open" skills are eligible — a skill still on probation
// (half_open) or never tripped (closed) has not been proven
// underperforming.
//
// Retention decree: culled skills are ARCHIVED, never deleted, and the
// archive lands BEFORE the pool rewrite. A crash between the two leaves a
// harmless duplicate; the other order loses the skill. The archive's error
// aborts the cull for the same reason — a retention copy that did not land
// must not be followed by a removal.
func CullIslandBottomHalf(ws, islandName string, minIslandSize int, dryRun bool) (CullReport, error) {
	rep := CullReport{DryRun: dryRun}
	if minIslandSize <= 0 {
		minIslandSize = 4
	}
	load := LoadSkills(ws)
	rep.Warnings = append(rep.Warnings, load.Announce()...)
	all := load.Skills

	var island []Skill
	for _, s := range all {
		if s.Island == islandName || (s.Island == "" && AssignIsland(s) == islandName) {
			island = append(island, s)
		}
	}
	if len(island) < minIslandSize {
		return rep, nil
	}
	var open []Skill
	for _, s := range island {
		if s.CircuitState == "open" {
			open = append(open, s)
		}
	}
	if len(open) == 0 {
		return rep, nil
	}
	// Worst first. Stable so equal scores keep store order in both
	// runtimes — Python's sorted() is stable and this decides DELETIONS.
	scored := append([]Skill{}, open...)
	sort.SliceStable(scored, func(i, j int) bool {
		return compactnessAdjustedScore(scored[i]) < compactnessAdjustedScore(scored[j])
	})
	cullCount := len(open) / 2
	if cullCount < 1 {
		cullCount = 1
	}
	for _, s := range scored[:cullCount] {
		rep.CulledIDs = append(rep.CulledIDs, s.ID)
	}
	if dryRun || len(rep.CulledIDs) == 0 {
		return rep, nil
	}

	cullSet := NewIDSet(rep.CulledIDs...)
	var culled, surviving []Skill
	for _, s := range all {
		if cullSet[s.ID] {
			culled = append(culled, s)
		} else {
			surviving = append(surviving, s)
		}
	}
	if err := ArchiveSkills(ws, culled, "island_cull"); err != nil {
		return rep, fmt.Errorf("island cull aborted — retention copy not written: %w", err)
	}
	warns, err := SaveSkills(ws, surviving, cullSet, NewIDSet())
	rep.Warnings = append(rep.Warnings, warns...)
	if err != nil {
		return rep, err
	}
	for _, s := range culled {
		if perr := WriteSkillProvenance(ws, ProvenanceRecord{
			SkillName: s.Name, Decision: "retire",
			Reason: fmt.Sprintf("island cull: bottom-half of open-circuit pool in %s",
				pyRepr(islandName)),
			EfficiencyScore: s.UtilityScore,
			// source_loop_ids is deliberately EMPTY here: the cull's Python
			// twin does not pass it, and this record is read as a peer of
			// Python's by the same auditor.
			Extra: []ProvenanceField{
				{"skill_id", s.ID},
				{"island", islandName},
				{"archived_to", "skills_archive.jsonl"},
			},
		}); perr != nil {
			// The provenance row is an audit record, not the decision: the
			// cull has already committed, so a failed write is announced,
			// never a rollback of a retention-safe removal.
			rep.Warnings = append(rep.Warnings,
				"skill provenance write failed for "+s.ID+": "+perr.Error())
		}
	}
	return rep, nil
}

// IslandCycleReport is one full cycle's outcome.
type IslandCycleReport struct {
	Assigned    int
	Culled      map[string][]string
	TotalCulled int
	Warnings    []string
}

// RunIslandCycle assigns islands to skills that lack one, then culls the
// bottom half of every island that holds an open-circuit skill.
//
// The assignment write NAMES exactly the ids it changed: a bulk rewrite
// that does not name its writes reverts any concurrent save, and this
// function's whole list comes from an unlocked read.
func RunIslandCycle(ws string, minIslandSize int, dryRun bool) (IslandCycleReport, error) {
	rep := IslandCycleReport{Culled: map[string][]string{}}
	load := LoadSkills(ws)
	rep.Warnings = append(rep.Warnings, load.Announce()...)
	pool := load.Skills

	assigned := NewIDSet()
	for i := range pool {
		if pool[i].Island == "" {
			pool[i].Island = AssignIsland(pool[i])
			assigned[pool[i].ID] = true
		}
	}
	rep.Assigned = len(assigned)
	if len(assigned) > 0 && !dryRun {
		warns, err := SaveSkills(ws, pool, NewIDSet(), assigned)
		rep.Warnings = append(rep.Warnings, warns...)
		if err != nil {
			return rep, err
		}
	}

	// Deterministic island order: this drives culls, and a map range would
	// make which island is culled first vary run to run.
	withOpen := map[string]bool{}
	for _, s := range pool {
		if s.CircuitState == "open" && s.Island != "" {
			withOpen[s.Island] = true
		}
	}
	names := make([]string, 0, len(withOpen))
	for n := range withOpen {
		names = append(names, n)
	}
	sortStrings(names)

	for _, name := range names {
		cr, err := CullIslandBottomHalf(ws, name, minIslandSize, dryRun)
		rep.Warnings = append(rep.Warnings, cr.Warnings...)
		if err != nil {
			return rep, err
		}
		if len(cr.CulledIDs) > 0 {
			rep.Culled[name] = cr.CulledIDs
			rep.TotalCulled += len(cr.CulledIDs)
		}
	}
	return rep, nil
}
