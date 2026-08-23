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
// A/B routing (slice 3b): each match is routed through
// SelectVariantForTask with a stable key, so a parent can be swapped for
// its active challenger. The manifest records the ROUTED skill — that is
// the point of recording it — while the match tier/score come from the
// MATCHED parent, which a routed challenger inherits as its selection
// evidence.
//
// NAMED GAP: Python passes a `project` slug for isolation. The Go loop
// resolves its project slug only in exec mode and only AFTER planning, so
// this passes the empty slug — which is Python's own documented legacy
// behaviour (filtering disabled), not a silent difference in the filter
// itself. FindMatchingSkills implements the isolation and is pinned; only
// the caller's slug is missing.
func injectSkills(workspaceDir, runDir, goal string) (block string, warns []string) {
	// ONE read, and its losses are announced. A half-tainted library
	// otherwise looks exactly like "nothing matched" — which is also what a
	// healthy cold store looks like, so the operator's only signal would be
	// the absence of a signal.
	load := skills.LoadSkills(workspaceDir)
	warns = append(warns, load.Announce()...)
	pool := load.Skills
	matched, tel := skills.FindMatchingSkillsIn(pool, goal, skills.MatchOptions{})

	// Stable routing key: Python hashes the goal because loop_id is not yet
	// assigned at this point.
	sum := sha1.Sum([]byte(goal))
	routingKey := hex.EncodeToString(sum[:])[:8]

	routed := make([]skills.Skill, 0, len(matched))
	for _, m := range matched {
		routed = append(routed, skills.SelectVariantForTask(m, routingKey, pool))
	}
	block = skills.FormatSkillsForPrompt(routed)

	// Per-skill match info lives on the MATCHED parent; a routed challenger
	// inherits its parent's selection evidence.
	type tier struct {
		method string
		score  float64
	}
	byID := map[string]tier{}
	for _, m := range matched {
		byID[m.ID] = tier{m.MatchMethod, m.MatchScore}
	}
	entries := make([]runs.SkillManifestEntry, 0, len(routed))
	for _, s := range routed {
		t, ok := byID[s.ID]
		if !ok && s.VariantOf != nil {
			t = byID[*s.VariantOf]
		}
		entries = append(entries, runs.SkillManifestEntry{
			ID: s.ID, Name: s.Name, ContentHash: s.ContentHash,
			VariantOf: s.VariantOf, Tier: s.Tier, RoutingKey: routingKey,
			MatchMethod: t.method, MatchScore: t.score,
		})
	}
	meta := &runs.SkillManifestMeta{Method: tel.Method,
		NCandidates: tel.NCandidates, TopScore: tel.TopScore}
	if err := runs.AppendSkillsManifest(runDir, entries, "decompose", meta,
		time.Now().UTC().Format("2006-01-02T15:04:05.000000-07:00")); err != nil {
		warns = append(warns, "skills manifest write failed: "+err.Error())
	}
	return block, warns
}
