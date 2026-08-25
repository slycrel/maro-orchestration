package skills

// AttributeFailureToSkills finds the skills that match a step which failed
// and records the failure against each of them, returning the ids it
// actually attributed.
//
// # The router gap does NOT apply here
//
// FindMatchingSkills carries a named gap: Python's Phase-17 trained-router
// tier is not ported, so the Go matcher is Python's keyword fall-through.
// This call site passes `use_router=False`, which means CPython takes that
// same fall-through path by construction — so this function is a full
// port, not a gapped one. Worth stating, because the gap is documented one
// call away and a reader would otherwise inherit it.
//
// # onlyIDs: three states, not two
//
// Python's `only_ids=None` keeps the legacy full-pool match; `only_ids=[]`
// restricts to nothing. Go cannot tell those apart from the slice alone —
// a slice built by appending over an empty manifest is nil, not empty — so
// the mode rides an explicit flag, the same way MatchOptions already
// spells it. Getting this wrong is not a near miss: a run with an empty
// manifest would silently score the ENTIRE library and record a failure
// against skills it never used.
//
// # The append is inside the try
//
// Python appends the id only after update_skill_utility RETURNS. A skill
// whose update raises is swallowed by the bare except and is NOT in the
// returned list, so the answer means "attributed", not "matched". The two
// differ exactly when a store write fails, which is when a caller most
// wants to know.
//
// The returned slice is empty rather than nil, matching the `[]` Python
// builds — a JSON writer downstream spells those differently.
func AttributeFailureToSkills(ws, stepText, failureReason, goal string,
	onlyIDs []string, restrictToIDs bool) []string {
	// `step_text + " " + goal` — the separator is unconditional, so an
	// empty goal still contributes a trailing space. That space reaches
	// the tokenizer, and matching it exactly is cheaper than proving it
	// cannot matter.
	matched, _ := FindMatchingSkills(ws, stepText+" "+goal, MatchOptions{
		OnlyIDs:       onlyIDs,
		RestrictToIDs: restrictToIDs,
	})
	attributed := []string{}
	for _, sk := range matched {
		if _, err := UpdateSkillUtility(ws, sk.ID, false, failureReason,
			stepText); err != nil {
			// `except Exception: pass` — one skill's failed write does not
			// abandon the rest of the attribution.
			continue
		}
		attributed = append(attributed, sk.ID)
	}
	return attributed
}
