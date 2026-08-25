package skills

import (
	"context"
	"fmt"
	"os"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// This file ports skill_lifecycle.rewrite_skill and its two helpers, plus
// skills.validate_skill_for_promotion — the LLM half of the skill lifecycle
// that MaybeAutoPromoteSkills' doc comment named as a gap.
//
// The two parse paths here are DELIBERATELY different, because Python's are:
// validate_skill_for_promotion goes through llm_parse.extract_json (fences
// stripped, prose carved away, `{}` on failure) and rewrite_skill hand-rolls
// its own fence strip and calls json.loads directly (leading prose is fatal).
// An idiom is not a defect — the defect is a spelling that does not match the
// spelling at its own site, and porting both to one reader would change which
// model replies each accepts.

// mapGetOr is `d.get(key, default)` over a PLAIN decoded map — the sibling of
// this package's getOr, which takes a pyval.Obj.
//
// Two spellings exist because the two Python call sites parse differently:
// rewrite_skill's json.loads yields an ordered object and
// validate_skill_for_promotion's extract_json yields a plain dict. The rule
// they share is the one worth stating twice: a PRESENT null yields nil, not
// the default, so `str(parsed.get("description", skill.description))` on a
// `{"description": null}` reply stores the four-character string "None".
func mapGetOr(m map[string]any, key string, def any) any {
	if v, ok := m[key]; ok {
		return v
	}
	return def
}

// (compactnessAdjustedScore lives in island.go — the cull path and the peer
// ranking are the SAME Python helper, _compactness_adjusted_score, and one of
// them was ported first. A second copy here would have been a second chance
// to get the code-point count wrong.)

// topPeerSkills returns up to k healthy peers with the highest
// compactness-adjusted score, for the ranked-candidate context a rewrite
// prompt carries (FunSearch: show the model v0 and v1 with their scores and
// ask for v2).
//
// SliceStable, not Slice: `sorted(..., reverse=True)` reverses the COMPARISON
// and stays stable, so equal-scoring peers keep pool order. Go's sort.Slice is
// explicitly unstable, and a tie between two peers is not exotic here — the
// score is a ratio of two rounded-ish floats and a fresh pool has every skill
// at utility 1.0.
func topPeerSkills(ws string, failing Skill, k int) ([]Skill, []string) {
	load := LoadSkills(ws)
	var candidates []Skill
	for _, s := range load.Skills {
		if s.ID != failing.ID && s.CircuitState != "open" && s.UtilityScore > 0.5 {
			candidates = append(candidates, s)
		}
	}
	if len(candidates) == 0 {
		return nil, load.Announce()
	}
	sort.SliceStable(candidates, func(i, j int) bool {
		return compactnessAdjustedScore(candidates[i]) >
			compactnessAdjustedScore(candidates[j])
	})
	if len(candidates) > k {
		candidates = candidates[:k]
	}
	return candidates, load.Announce()
}

// buildRewritePrompt is the prompt text, kept as its own function so the
// differential can compare it without an adapter in the way.
//
// The text is a shared artifact in the sense that matters here: it is what
// the model sees, so a divergence changes the reply and therefore the stored
// skill. Every slice is a CODE-POINT slice and every strip is Python's.
func buildRewritePrompt(skill Skill, peers []Skill) string {
	failureSummary := "(no specific failure reasons recorded)"
	if len(skill.FailureNotes) > 0 {
		notes := make([]string, len(skill.FailureNotes))
		for i, n := range skill.FailureNotes {
			notes[i] = "- " + n
		}
		failureSummary = strings.Join(notes, "\n")
	}

	numbered := make([]string, len(skill.StepsTemplate))
	for i, s := range skill.StepsTemplate {
		numbered[i] = fmt.Sprintf("%d. %s", i+1, s)
	}

	peerContext := ""
	if len(peers) > 0 {
		lines := []string{
			"Top-performing peer skills for reference (compactness-adjusted):"}
		for i, peer := range peers {
			head := peer.StepsTemplate
			if len(head) > 3 {
				head = head[:3]
			}
			lines = append(lines, fmt.Sprintf(
				"  v%d (score=%.2f): %s — %s\n    Steps: %s",
				i, peer.UtilityScore, peer.Name,
				pyval.Clip(peer.Description, 100), strings.Join(head, "; ")))
		}
		peerContext = "\n" + strings.Join(lines, "\n") + "\n"
	}

	return fmt.Sprintf(`You are improving a skill definition for an autonomous agent system.

The skill "%s" has a tripped circuit breaker (consecutive_failures=%d,
utility_score=%.2f). Here are the recorded failure reasons:

%s

Current skill description:
%s

Current steps template:
%s
%s
Based on the failure pattern, rewrite the skill. Output ONLY valid JSON with these keys:
{
  "description": "<revised one-sentence description of what this skill does>",
  "steps_template": ["<step 1>", "<step 2>", "..."],
  "trigger_patterns": ["<keyword or phrase that should trigger this skill>", "..."]
}

Rules:
- Keep steps concrete and actionable (not vague)
- Address the failure reasons directly
- Do not add steps that require external network access if failures were network-related
- 2-5 steps maximum
- trigger_patterns: 3-6 short keyword phrases`,
		skill.Name, skill.ConsecutiveFailures, skill.UtilityScore,
		failureSummary, skill.Description, strings.Join(numbered, "\n"),
		peerContext)
}

// stripRewriteFence is rewrite_skill's OWN fence strip, which is not
// llm_parse's and does not behave like it.
//
//	raw = resp.content.strip()
//	if raw.startswith("```"):
//	    raw = "\n".join(raw.split("\n")[1:])
//	    if raw.endswith("```"):
//	        raw = raw[: raw.rfind("```")]
//
// Three things follow that the regex-based helper does not do. A fenced reply
// on ONE line ("```json {...}```") loses everything, because split("\n")[1:]
// is empty. The closing fence is found with rfind, so a ``` inside the JSON
// truncates at the LAST one rather than the first. And leading prose before
// an unfenced object is never carved away — json.loads sees it and raises.
//
// pytext.Strip, not TrimSpace: str.strip() removes U+001C-U+001F and the
// other separators Go's unicode.IsSpace does not call space, and a model
// reply is exactly the kind of text that can carry one.
func stripRewriteFence(content string) string {
	raw := pyStrip(content)
	if !strings.HasPrefix(raw, "```") {
		return raw
	}
	parts := strings.Split(raw, "\n")
	raw = strings.Join(parts[1:], "\n")
	if strings.HasSuffix(raw, "```") {
		raw = raw[:strings.LastIndex(raw, "```")]
	}
	return raw
}

// RewriteSkill LLM-rewrites a skill whose circuit breaker is OPEN, analysing
// its failure notes and current body into a revised description and steps.
//
// Returns the updated skill, or nil when the rewrite could not be used — an
// absent adapter, a reply that did not parse, or a candidate that failed the
// pre-save sanity gate. nil is not an error: Python returns None for all of
// those and the callers treat it as "no rewrite this round".
//
// # in_place decides which lane this is
//
// inPlace=true (the circuit lane) rewrites the skill row itself and SAVES it,
// whether or not the caller uses the return value. The row goes to half_open
// — probationary, not trusted — rather than closed, so one good run does not
// restore full standing.
//
// inPlace=false (the frontier A/B lane) leaves the pool untouched and returns
// a NEW skill with a fresh id carrying the rewritten content, unsaved, for
// CreateSkillVariant to stamp as a challenger. Before 2026-08-06 the frontier
// lane used the in-place path, so every "challenger" was the mutated parent
// marked as its own variant and the A/B never had two arms. CreateSkillVariant
// refuses that shape by id now; this is the function that supplies the shape
// it wants.
//
// # The error return is narrower than "something went wrong"
//
// It carries exactly the two things Python RAISES rather than swallows: a
// non-mapping reply (`parsed.get` on a list is an AttributeError, and the
// field reads sit OUTSIDE rewrite_skill's try), and a store write that fails.
// Both reach the caller in Python too — MaybeAutoPromoteSkills' repair loop
// catches them and stops trying, which is a different outcome from a nil
// return, because a nil return also stops the loop but a raise does so
// through the `except`.
// RewriteOptions carries the two keyword arguments Python's rewrite_skill
// takes plus the port's id seam.
//
// InPlace has NO safe zero value — false is the frontier lane and Python's
// default is true — so every caller states it. That is deliberate: the two
// lanes write to different places, and a forgotten flag would silently turn
// a challenger mint into a pool write.
type RewriteOptions struct {
	Verbose bool
	InPlace bool
	Rec     *record.Recorder
	// NewID substitutes `str(uuid.uuid4())[:8]` for the challenger mint.
	// nil means record.NewID.
	NewID func() string
}

func RewriteSkill(ctx context.Context, ws string, skill Skill, adapter llm.Adapter,
	opts RewriteOptions) (*Skill, []string, error) {
	if adapter == nil {
		return nil, nil, nil
	}

	peers, warns := topPeerSkills(ws, skill, 2)
	prompt := buildRewritePrompt(skill, peers)

	// Everything to the json.loads is inside Python's try/except, which
	// prints under verbose and returns None.
	resp, err := adapter.Complete(ctx, []llm.Message{{Role: "user", Content: prompt}},
		// Python passes no_tools=True; utility mode is this adapter's
		// default (no Tools, AgentTools false), so the flag has no spelling
		// here rather than being dropped. No temperature either — the
		// adapter default is what Python leaves in place.
		llm.Options{MaxTokens: 600, Purpose: "skill rewrite"})
	if err != nil {
		rewriteParseWarn(opts.Verbose, skill.ID, err)
		return nil, warns, nil
	}
	// resp.content, NOT llm.ContentOrEmpty(resp).
	//
	// The comment here said that already and the code did the opposite, and
	// the two were indistinguishable because ContentOrEmpty's own body is
	// pytext.Strip — so the strip inside stripRewriteFence became a dead
	// second copy, and reverting it to strings.TrimSpace passed the entire
	// suite including the two fixtures written to catch exactly that. A
	// duplicated guard is what makes a wrong one unobservable.
	//
	// Python's rewrite_skill reaches for `resp.content.strip()` directly
	// while its module imports content_or_empty for other functions, so the
	// single strip belongs at the one site that performs it.
	if resp == nil {
		rewriteParseWarn(opts.Verbose, skill.ID, fmt.Errorf("no response"))
		return nil, warns, nil
	}
	raw := stripRewriteFence(resp.Content)
	parsedAny, perr := pyval.LoadsOrdered(raw)
	if perr != nil {
		rewriteParseWarn(opts.Verbose, skill.ID, perr)
		return nil, warns, nil
	}
	parsed, isObj := parsedAny.(pyval.Obj)
	if !isObj {
		// `parsed.get(...)` on a list or a number is an AttributeError, and
		// the field reads below are OUTSIDE the try that would have caught
		// it. It leaves this function.
		return nil, warns, &pyval.PyErr{Class: "AttributeError", Msg: fmt.Sprintf(
			"'%s' object has no attribute 'get'", pyval.TypeName(parsedAny))}
	}

	newDesc := pyStrip(pyval.Str(getOr(parsed, "description", skill.Description)))
	newSteps, serr := strippedStrings(getOr(parsed, "steps_template", skill.StepsTemplate))
	if serr != nil {
		return nil, warns, serr
	}
	newTriggers, terr := strippedStrings(getOr(parsed, "trigger_patterns", skill.TriggerPatterns))
	if terr != nil {
		return nil, warns, terr
	}

	// Pre-save sanity gate (FunSearch: discard invalid candidates before
	// storing). Silent in Python beyond a debug log — a bad rewrite is a
	// no-op, never a stored bad skill.
	if len(newSteps) == 0 || newDesc == "" {
		return nil, warns, nil
	}
	if len([]rune(newDesc)) > 400 {
		// len() over a str is code points here too, and a description is the
		// field most likely to carry them.
		return nil, warns, nil
	}
	if len(newSteps) > 10 {
		return nil, warns, nil
	}
	if len(newTriggers) == 0 {
		// Inherit rather than discard.
		newTriggers = skill.TriggerPatterns
	}

	if !opts.InPlace {
		challenger := deepCopySkill(skill)
		newID := opts.NewID
		if newID == nil {
			newID = record.NewID
		}
		challenger.ID = newID()
		challenger.Description = newDesc
		challenger.StepsTemplate = newSteps
		challenger.TriggerPatterns = newTriggers
		challenger.ConsecutiveFailures = 0
		challenger.ConsecutiveSuccesses = 0
		challenger.CircuitState = "half_open"
		challenger.FailureNotes = lastN(skill.FailureNotes, 2)
		challenger.VariantOf = nil
		challenger.ContentHash = ComputeSkillHash(challenger)
		if opts.Verbose {
			fmt.Fprintf(os.Stderr,
				"[evolver] minted challenger %s for skill %s (%s)\n",
				challenger.ID, skill.ID, skill.Name)
		}
		// No save and no SKILL_REWRITE event: the caller stamps variant_of
		// and persists, and SKILL_VARIANT_CREATED covers the mint.
		return &challenger, warns, nil
	}

	// The in-place lane. failures_before is snapshotted BEFORE the reload
	// because the target may be this same object in Python — the caller
	// often holds a reference into the pool it loaded.
	failuresBefore := skill.ConsecutiveFailures
	pool := LoadSkills(ws)
	warns = append(warns, pool.Announce()...)
	idx := -1
	for i := range pool.Skills {
		if pool.Skills[i].ID == skill.ID {
			idx = i
			break
		}
	}
	if idx < 0 {
		return nil, warns, nil
	}
	target := &pool.Skills[idx]
	target.Description = newDesc
	target.StepsTemplate = newSteps
	target.TriggerPatterns = newTriggers
	target.ConsecutiveFailures = 0
	target.ConsecutiveSuccesses = 0
	target.CircuitState = "half_open"
	target.ContentHash = ComputeSkillHash(*target)
	// The FRESH row's notes, not the caller's copy — a note appended since
	// the caller loaded its skill is the one worth keeping.
	target.FailureNotes = lastN(target.FailureNotes, 2)

	saveWarns, serr2 := SaveSkills(ws, pool.Skills, NewIDSet(), NewIDSet(target.ID))
	warns = append(warns, saveWarns...)
	if serr2 != nil {
		// Python's _save_skills call is NOT inside a try here, so a write
		// failure leaves this function.
		return nil, warns, serr2
	}

	if opts.Rec != nil {
		// utility_score is the CALLER's skill, not the reloaded target:
		// Python reads `skill.utility_score` in a context dict built from
		// `target` everywhere else. Copying the target's value here would
		// change the recorded number whenever a concurrent write moved it.
		if evErr := opts.Rec.EventRelated("SKILL_REWRITE", target.Name,
			fmt.Sprintf("Circuit-open skill '%s' rewritten → half_open (probation).",
				target.Name),
			map[string]any{
				"skill_id":        target.ID,
				"failures_before": failuresBefore,
				"utility_score":   skill.UtilityScore,
				"new_description": pyval.Clip(newDesc, 200),
				"steps_count":     len(newSteps),
			}, "", []string{"skill:" + target.ID}); evErr != nil {
			warns = append(warns,
				"captain's log write failed (SKILL_REWRITE): "+evErr.Error())
		}
	}

	if opts.Verbose {
		fmt.Fprintf(os.Stderr, "[evolver] rewrote skill %s (%s) → half_open\n",
			skill.ID, skill.Name)
	}
	out := *target
	return &out, warns, nil
}

func rewriteParseWarn(verbose bool, id string, err error) {
	if verbose {
		fmt.Fprintf(os.Stderr, "[evolver] rewrite_skill parse error for %s: %v\n",
			id, err)
	}
}

// strippedStrings is `[str(x).strip() for x in v if str(x).strip()]` as a
// []string. It routes through strippedNonEmpty so the ITERATION rule has one
// port: a string yields its characters, a mapping its keys, and anything else
// is a TypeError — which for these two fields leaves rewrite_skill, because
// the comprehensions sit outside its try.
func strippedStrings(v any) ([]string, error) {
	items, err := strippedNonEmpty(v)
	if err != nil {
		return nil, err
	}
	out := make([]string, 0, len(items))
	for _, it := range items {
		out = append(out, it.(string))
	}
	return out, nil
}

// lastN is Python's `xs[-n:]`: the last n elements, or all of them when
// there are fewer. It always returns a FRESH slice — `list(...)` in Python,
// and here because the challenger must not share a backing array with the
// parent it was copied from.
func lastN(xs []string, n int) []string {
	if len(xs) > n {
		xs = xs[len(xs)-n:]
	}
	out := make([]string, len(xs))
	copy(out, xs)
	return out
}

// deepCopySkill is Python's `dict_to_skill(json.loads(json.dumps(skill_to_dict(s))))`
// — a round trip whose only purpose is to stop the challenger sharing list
// objects with its parent.
//
// Go's struct copy is shallow, so every slice and the imported map are copied
// explicitly. A shared backing array here would make a later append to the
// challenger's failure_notes visible on the parent, in a pool where both rows
// are live and both get saved.
func deepCopySkill(s Skill) Skill {
	out := s
	out.TriggerPatterns = append([]string(nil), s.TriggerPatterns...)
	out.StepsTemplate = append([]string(nil), s.StepsTemplate...)
	out.SourceLoopIDs = append([]string(nil), s.SourceLoopIDs...)
	out.FailureNotes = append([]string(nil), s.FailureNotes...)
	out.Tags = append([]string(nil), s.Tags...)
	if s.VariantOf != nil {
		v := *s.VariantOf
		out.VariantOf = &v
	}
	if s.Imported != nil {
		m := make(map[string]any, len(s.Imported))
		for k, v := range s.Imported {
			m[k] = v
		}
		out.Imported = m
	}
	return out
}

// skillValidationSystem is the quality-gate system prompt, verbatim.
const skillValidationSystem = "You are a skill quality gate for an AI orchestration system. " +
	"Evaluate whether a skill definition is ready for promotion to 'established' tier. " +
	"A valid skill has: (1) a clear, specific description of what it does; " +
	"(2) step templates that are concrete and actionable — not vague or self-referential; " +
	"(3) trigger patterns that genuinely distinguish this skill from general instructions. " +
	"Respond with JSON: {\"valid\": true|false, \"reason\": \"one sentence\", " +
	"\"repair_hint\": \"brief suggestion if invalid, empty string if valid\"}"

// SkillValidation is validate_skill_for_promotion's return dict.
//
// Judged separates "the model said yes" from "we never asked" — the fail-open
// default lets a skill through when validation could not run at all, and the
// promotion event has to be able to say the pass was never a judgment.
type SkillValidation struct {
	Valid      bool
	Reason     string
	RepairHint string
	Judged     bool
}

// ValidateSkillForPromotion is the LLM quality gate a skill passes before it
// is promoted to established (Voyager steal).
//
// # Fail-open, and narrower than it looks
//
// When the gate cannot run, it PASSES the skill: a broken adapter must not
// silently freeze the promotion lane. But that path is reached only by an
// exception — an adapter that raised. A reply the model got WRONG is not it:
// llm_parse.extract_json returns `{}` on any parse failure, `{}` is still a
// dict, and `bool({}.get("valid", False))` is False. So a garbled reply is a
// FAILED validation that goes to the repair loop, not a free pass. The port
// keeps that split by reading the empty map jsonx.Object leaves behind rather
// than treating its error as the fail-open case.
//
// valid is Python TRUTHINESS, not a bool cast: `{"valid": "no"}` passes and
// `{"valid": 0}` does not.
func ValidateSkillForPromotion(ctx context.Context, skill Skill,
	adapter llm.Adapter) SkillValidation {
	failOpen := SkillValidation{
		Valid: true, Reason: "validation unavailable (fail-open)",
		RepairHint: "", Judged: false,
	}
	if adapter == nil {
		// `adapter.complete` on None is an AttributeError into the except.
		return failOpen
	}

	triggers := skill.TriggerPatterns
	if len(triggers) > 5 {
		triggers = triggers[:5]
	}
	steps := skill.StepsTemplate
	if len(steps) > 6 {
		steps = steps[:6]
	}
	stepLines := make([]string, len(steps))
	for i, s := range steps {
		stepLines[i] = "  - " + s
	}
	skillText := fmt.Sprintf("Name: %s\nDescription: %s\nTrigger patterns: %s\nSteps:\n%s",
		skill.Name, skill.Description, strings.Join(triggers, ", "),
		strings.Join(stepLines, "\n"))

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: skillValidationSystem},
		{Role: "user", Content: "Validate this skill for promotion:\n\n" + skillText},
	}, llm.Options{MaxTokens: 150, Temperature: 0.1,
		Purpose: "skill promotion validation"})
	if err != nil {
		return failOpen
	}
	// The error is DISCARDED on purpose: extract_json answers `{}` where this
	// answers (nil, err), and a nil map reads exactly like an empty dict.
	// Mapping the error to failOpen instead would turn every unparseable
	// reply into a promotion.
	parsed, _ := jsonx.Object(llm.ContentOrEmpty(resp))
	return SkillValidation{
		Valid:      pyval.Truthy(mapGetOr(parsed, "valid", false)),
		Reason:     pyval.Str(mapGetOr(parsed, "reason", "")),
		RepairHint: pyval.Str(mapGetOr(parsed, "repair_hint", "")),
		Judged:     true,
	}
}
