package loop

import (
	"crypto/sha1"
	"encoding/hex"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/runs"
	"github.com/slycrel/maro-orchestration/go/internal/skills"
)

// injectSkills matches the goal against the skill library, returns the
// decompose prompt block, and records the run-keyed manifest of what
// actually entered the prompt.
//
// The manifest write is unconditional and its failure is a WARNING, never a
// run-killer: attribution reads this file, and a missing one must mean "the
// recorder did not run", not "nothing matched".
//
// NAMED GAP (slice 3b): Python passes a `project` slug for isolation. The Go
// loop resolves its project slug only in exec mode and only AFTER planning,
// so this passes the empty slug — which is Python's own documented
// legacy behaviour (filtering disabled), not a silent difference in the
// filter itself. FindMatchingSkills implements the isolation and is pinned;
// only the caller's slug is missing.
func injectSkills(workspaceDir, runDir, goal string) (block string, warns []string) {
	matched, tel := skills.FindMatchingSkills(workspaceDir, goal, skills.MatchOptions{})
	block = skills.FormatSkillsForPrompt(matched)

	// Stable routing key: Python hashes the goal because loop_id is not yet
	// assigned at this point. Carried verbatim so a manifest row means the
	// same thing in both runtimes even before variant routing lands.
	sum := sha1.Sum([]byte(goal))
	routingKey := hex.EncodeToString(sum[:])[:8]

	entries := make([]runs.SkillManifestEntry, 0, len(matched))
	for _, s := range matched {
		entries = append(entries, runs.SkillManifestEntry{
			ID: s.ID, Name: s.Name, ContentHash: s.ContentHash,
			VariantOf: s.VariantOf, Tier: s.Tier, RoutingKey: routingKey,
			MatchMethod: s.MatchMethod, MatchScore: s.MatchScore,
		})
	}
	meta := &runs.SkillManifestMeta{Method: tel.Method,
		NCandidates: tel.NCandidates, TopScore: tel.TopScore}
	if err := runs.AppendSkillsManifest(runDir, entries, "decompose", meta,
		time.Now().UTC().Format(time.RFC3339Nano)); err != nil {
		warns = append(warns, "skills manifest write failed: "+err.Error())
	}
	return block, warns
}
