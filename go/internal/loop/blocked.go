// Blocked-step recovery ladder — the port of Python loop_blocked's
// Phase 62 zoom-metacognition decision algorithm (_handle_blocked_step)
// plus the split helpers it leans on (loop_planning._split_exec_analyze,
// step_exec.generate_refinement_hint, _generate_timeout_split). The
// executor lane previously HALTED on the first non-done step precisely
// because none of this existed; the halt stays as the terminal verdict,
// but retry / split / re-decompose now get their evidence-driven chance
// first, in Python's exact decision order.
//
// Ported verbatim: the three thresholds, error-fingerprint convergence
// (md5-head parity), sibling failure rate, NEED_INFO research splits,
// missing-input honest-fail (never fabricate an absent input), combined
// exec+analyze structural splits, timeout LLM-split with heuristic
// fallback, round-1 generic / round-2 LLM refinement hints, adapter-hung
// bail after 3 consecutive ceiling timeouts.
//
// Deliberately NOT ported, named (each returns with its machinery):
//   - token_runaway abandonment — the per-call ingest brake is unported.
//   - escape-pattern demotion hints (async/env-claim/deliverable-path) —
//     the step_exec detectors that stamp those tags are unported.
//   - mid-loop diagnosis consult (introspect.diagnose_loop, Phase 44).
//   - navigator shadow (decide-only A/B machinery).
//   - _shape_steps' boundary/recon tag guards — the Go planner mints no
//     such tags yet; shaping here is the plain exec+analyze pass.
//   - (stop_verdict grew its typed outcome column in r2 2026-08-22 —
//     Result.StopVerdict/StuckReason + the outcome row; the chain text
//     is the prose view, no longer the only carrier.)
package loop

import (
	"context"
	"crypto/md5"
	"errors"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// Phase 62 thresholds (zoom-metacognition research) — Python verbatim.
const (
	retryThreshold         = 3   // retries before considering redecompose
	siblingThreshold       = 0.5 // >50% sibling failure → redecompose
	redecomposeThreshold   = 2   // max re-decompositions before stuck
	maxConsecutiveTimeouts = 3   // ceiling timeouts before adapter-hung bail
	needInfoPrefix         = "NEED_INFO:"
)

// blockDecision mirrors Python _BlockDecision minus `advance` (its only
// producer is the unported token_runaway branch).
type blockDecision struct {
	retry       bool
	hint        string
	stuckReason string   // non-empty → terminal
	splitInto   []string // non-empty → replace the step with these
	redecompose bool
	metaReason  string
	stopVerdict string // → Result.StopVerdict + the outcome row's typed stop_verdict column (grown r2 2026-08-22)
	// Usage from the timeout-split / refinement-hint LLM calls — the
	// caller adds it to the run totals. Python's adapters record spend
	// centrally in metrics; Go's only ledger is the outcome row, so
	// dropping this made recovery calls invisible spend (adversarial
	// ladder review 2026-08-22, Expert QA HIGH — same class as the
	// exec-r2 failed-turn salvage fix).
	tokensIn  int
	tokensOut int
}

// errorFingerprint ports _error_fingerprint byte-for-byte: md5 of the
// whitespace-normalized 200-char HEADS of reason|result, first 12 hex.
// Head-only is deliberate hash-input normalization, not evidence
// truncation: tails vary with noise and would make identical failures
// look like progress.
func errorFingerprint(stuckReason, result string) string {
	norm := func(s string) string {
		s = strings.Join(strings.Fields(s), " ")
		if len(s) > 200 {
			s = s[:200]
		}
		return s
	}
	sum := md5.Sum([]byte(norm(stuckReason) + "|" + norm(result)))
	return fmt.Sprintf("%x", sum)[:12]
}

// isConverging ports _is_converging: >50% unique fingerprints means the
// error landscape is changing — retries are progress.
func isConverging(fps []string) bool {
	if len(fps) < 2 {
		return true
	}
	uniq := map[string]bool{}
	for _, f := range fps {
		uniq[f] = true
	}
	return float64(len(uniq))/float64(len(fps)) > 0.5
}

// siblingFailureRate ports _sibling_failure_rate: fraction of executed
// steps that blocked — most siblings failing means the decomposition
// itself is wrong.
func siblingFailureRate(steps []StepOutcome) float64 {
	if len(steps) == 0 {
		return 0
	}
	blocked := 0
	for _, s := range steps {
		if s.Status == "blocked" {
			blocked++
		}
	}
	return float64(blocked) / float64(len(steps))
}

// _MISSING_INPUT_SIGNALS / _INPUT_CONSUMING_KEYWORDS, Python verbatim.
var missingInputSignals = []string{
	"no such file or directory", "no such file", "no such path",
	"filenotfounderror", "errno 2", "enoent", "does not exist",
	"doesn't exist", "cannot find", "could not find", "couldn't find",
	"404", "not found",
}

var inputConsumingKeywords = []string{
	"read ", "open ", "load ", "parse ", "fetch ", "download ", "import ",
	"ingest ", "contents of", "from the file", "from the url", "cat ",
}

func looksLikeMissingInput(text string) bool {
	low := strings.ToLower(text)
	for _, sig := range missingInputSignals {
		if strings.Contains(low, sig) {
			return true
		}
	}
	return false
}

func isInputConsumingStep(step string) bool {
	low := strings.ToLower(step)
	for _, kw := range inputConsumingKeywords {
		if strings.Contains(low, kw) {
			return true
		}
	}
	return false
}

// _EXEC_KEYWORDS / _ANALYZE_KEYWORDS from loop_planning, verbatim.
var execKeywords = []string{
	"pytest", "python", "run ", "execute", "make ", "npm ", "yarn ", "docker",
	"git ", "bash ", "sh ", "cargo ", "go test", "mvn ", "gradle",
	"install ", "build ", "compile", "lint ", "mypy ", "ruff ",
	"grep ", "find ", "curl ", "fetch", "rg ", "wget ", "cat ",
	"invoke ", "launch ", "trigger ", "call ", "exec ",
}

var analyzeKeywords = []string{
	"analyz", "summariz", "review", "identify failure", "check result",
	"interpret", "categoriz", "parse output", "parse result",
	"count pass", "count fail", "report on", "describe result",
	"judge", "critique", "conclude", "evaluate", "assess",
	"examine", "determine", "count the", "verify result",
	"see if", "check if", "identify", "inspect result",
	"inspect output", "look at result",
}

func containsAny(low string, kws []string) bool {
	for _, kw := range kws {
		if strings.Contains(low, kw) {
			return true
		}
	}
	return false
}

// minIndexOf returns the smallest index at which any keyword occurs, or
// -1 — Python's min(low.find(kw) for kw in kws if kw in low).
func minIndexOf(low string, kws []string) int {
	min := -1
	for _, kw := range kws {
		if i := strings.Index(low, kw); i >= 0 && (min < 0 || i < min) {
			min = i
		}
	}
	return min
}

// isCombinedExecAnalyze ports _is_combined_exec_analyze: steps that run
// a command AND analyze its output routinely fail — the executor can't
// fit both the command timeout and the analysis into one call.
func isCombinedExecAnalyze(step string) bool {
	low := strings.ToLower(step)
	return containsAny(low, execKeywords) && containsAny(low, analyzeKeywords)
}

const defaultAnalysisPart = "analyze the captured output for errors, results, and next actions"

// splitExecAnalyze ports _split_exec_analyze: [run step, analyze step],
// with the analysis half sanitized so it cannot re-trigger the compound
// detector (splits must strictly converge).
func splitExecAnalyze(step string) []string {
	low := strings.ToLower(step)
	runPart := step
	for _, sep := range []string{" and ", " then ", ", then ", " to ", "; "} {
		if idx := strings.Index(low, sep); idx >= 0 {
			cand := strings.TrimSpace(step[:idx])
			if containsAny(strings.ToLower(cand), execKeywords) {
				runPart = cand
				break
			}
		}
	}
	analysisPart := defaultAnalysisPart
	if idx := minIndexOf(low, analyzeKeywords); idx >= 0 {
		if cand := strings.Trim(step[idx:], " ,;:-"); cand != "" {
			analysisPart = cand
		}
	}
	if containsAny(strings.ToLower(analysisPart), execKeywords) {
		analysisPart = defaultAnalysisPart
	}
	rpLow := strings.ToLower(runPart)
	if idx := minIndexOf(rpLow, analyzeKeywords); idx > 0 {
		runPart = strings.Trim(runPart[:idx], " ,;:-")
	}
	rp := strings.TrimSpace(runPart)
	if len(rp) >= 4 && strings.EqualFold(rp[:4], "run ") {
		rp = rp[4:]
	}
	return []string{
		"Run " + clipHead(rp, 120) + " and save output to a file",
		"Read the captured output and " + clipHead(analysisPart, 120),
	}
}

// clipHead is Python's bare [:n] slice — used where the original does
// plain head-truncation for prompt assembly (not record-bound text, so
// no marked-clip budget applies).
func clipHead(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

// shapeSteps ports _shape_steps minus the boundary/recon tag guards
// (the Go planner mints no such tags — named in the package comment):
// one exec+analyze pass over every plan-mutation product.
func shapeSteps(steps []string) []string {
	shaped := make([]string, 0, len(steps))
	for _, s := range steps {
		if isCombinedExecAnalyze(s) {
			shaped = append(shaped, splitExecAnalyze(s)...)
		} else {
			shaped = append(shaped, s)
		}
	}
	return shaped
}

// Heuristic fallback split boundaries. Python uses one regex with a
// lookahead (`\band\b\s*(?=[A-Z])`); Go's RE2 has no lookahead, so the
// bare-"and" boundary (split only before a Capitalized clause) is
// emulated in heuristicSplitParts — same accepted boundaries.
var timeoutSplitSeps = regexp.MustCompile(`\s*;\s*|\s+and\s+then\s+`)
var bareAndSep = regexp.MustCompile(`\s*\band\b\s*`)

func heuristicSplitParts(stepText string) []string {
	var parts []string
	for _, seg := range timeoutSplitSeps.Split(stepText, -1) {
		start := 0
		for _, lc := range bareAndSep.FindAllStringIndex(seg, -1) {
			rest := seg[lc[1]:]
			if rest != "" && rest[0] >= 'A' && rest[0] <= 'Z' {
				parts = append(parts, seg[start:lc[0]])
				start = lc[1]
			}
		}
		parts = append(parts, seg[start:])
	}
	return parts
}

// generateTimeoutSplit ports _generate_timeout_split: ask the model to
// rewrite a timed-out step as 2-4 atomic steps (short 45s timeout so a
// struggling adapter doesn't compound the delay), falling back to the
// sentence-boundary heuristic, [] when neither yields ≥2 usable parts.
// Divergence, named: Python routes the call through per-role model
// assignment (conductor.assign_model_by_role); Go has no role routing
// yet and uses the run's adapter directly.
func generateTimeoutSplit(ctx context.Context, a llm.Adapter, stepText string) (split []string, tokensIn, tokensOut int) {
	if a != nil {
		prompt := fmt.Sprintf(
			"An autonomous agent step timed out because it was too large to complete in time.\n\n"+
				"Timed-out step: %s\n\n"+
				"Rewrite this as 2-4 smaller, atomic steps that together accomplish the same goal. "+
				"Each step must be self-contained and completable independently. "+
				"Return ONLY a numbered list, one step per line, no explanation.", stepText)
		resp, err := a.Complete(ctx, []llm.Message{{Role: "user", Content: prompt}},
			llm.Options{MaxTokens: 300, Temperature: 0.2,
				Timeout: 45 * time.Second, Purpose: "timeout-split"})
		if resp != nil {
			tokensIn, tokensOut = resp.TokensIn, resp.TokensOut
		} else if re := (*llm.ResultError)(nil); errors.As(err, &re) {
			// A failed recovery call still spent tokens (exec-r2 salvage
			// pattern).
			tokensIn, tokensOut = re.TokensIn, re.TokensOut
		}
		if err == nil {
			var steps []string
			for _, ln := range strings.Split(strings.TrimSpace(resp.Content), "\n") {
				ln = strings.TrimSpace(ln)
				if ln == "" || strings.HasPrefix(ln, "#") {
					continue
				}
				ln = strings.TrimSpace(strings.TrimLeft(ln, "0123456789.-) "))
				if len(ln) > 10 {
					steps = append(steps, ln)
				}
			}
			if len(steps) >= 2 {
				return steps, tokensIn, tokensOut
			}
		}
	}
	var parts []string
	for _, p := range heuristicSplitParts(stepText) {
		p = strings.TrimRight(strings.TrimSpace(p), ",")
		if len(p) > 10 {
			parts = append(parts, p)
		}
	}
	if len(parts) >= 2 {
		return parts, tokensIn, tokensOut
	}
	return nil, tokensIn, tokensOut
}

// generateRefinementHint ports step_exec.generate_refinement_hint: a
// cheap targeted one-sentence fix suggestion for the second retry, with
// the Python fallback text when the adapter is missing or errors. Same
// role-routing divergence as generateTimeoutSplit.
func generateRefinementHint(ctx context.Context, a llm.Adapter, stepText, blockReason, partial string) (hint string, tokensIn, tokensOut int) {
	fallback := fmt.Sprintf(
		"[Refinement attempt 2 — blocked: %s] "+
			"Analyze the failure carefully. Try a completely different approach: "+
			"decompose this step further, use only information already available, "+
			"or produce a partial result and mark the step complete.",
		clipHead(blockReason, 100))
	if a == nil {
		return fallback, 0, 0
	}
	prompt := fmt.Sprintf(
		"A step in an autonomous agent loop failed twice.\n\nStep: %s\nFailure reason: %s\n",
		stepText, clipHead(blockReason, 200))
	if partial != "" {
		prompt += fmt.Sprintf("Partial result so far: %s\n", clipHead(partial, 300))
	}
	prompt += "\nIn ONE sentence, suggest the most likely fix or alternative approach. " +
		"Be specific and actionable. Do not suggest giving up."
	resp, err := a.Complete(ctx, []llm.Message{{Role: "user", Content: prompt}},
		llm.Options{MaxTokens: 150, Temperature: 0.3,
			Timeout: 45 * time.Second, Purpose: "refinement-hint"})
	if resp != nil {
		tokensIn, tokensOut = resp.TokensIn, resp.TokensOut
	} else if re := (*llm.ResultError)(nil); errors.As(err, &re) {
		tokensIn, tokensOut = re.TokensIn, re.TokensOut
	}
	if err != nil || strings.TrimSpace(resp.Content) == "" {
		return fallback, tokensIn, tokensOut
	}
	return fmt.Sprintf("[Refinement — previous attempts blocked: %s] Suggested approach: %s",
		clipHead(blockReason, 200), strings.TrimSpace(resp.Content)), tokensIn, tokensOut
}

// handleBlockedStep ports _handle_blocked_step: decide retry / split /
// redecompose / terminal from the evidence. Mutates no loop state — the
// caller applies the decision — but it is NOT side-effect-free: the
// timeout-split and refinement-hint branches make live, billed LLM
// calls (up to 45s each), and their usage rides back on the decision.
// Branch order is Python's exactly; the unported branches
// (token_runaway, escape-pattern hints, diagnosis consult) are noted in
// place so the seams stay visible. Also unported: retry model-tier
// escalation (loop_blocked.py:235-242, cheap→mid→power via
// step_tier_overrides) — Go has no model-tier registry yet, so retries
// re-run on the same adapter (adversarial ladder review 2026-08-22,
// Skeptic — was silently dropped, now named).
func handleBlockedStep(ctx context.Context, a llm.Adapter, stepText string,
	out StepOutcome, priorRetries int, fingerprints []string,
	siblings []StepOutcome, replanCount int) blockDecision {

	// StuckReason is Python's separate outcome field. Every production
	// producer (the four blocked branches in executeExecStep) sets it
	// unconditionally, and only the exec lane calls this function — the
	// fallback is a defensive backstop for hand-built outcomes (tests),
	// not a live path (adversarial ladder r2 2026-08-22, both lenses:
	// the earlier comment wrongly credited it to the tool-less lane,
	// which never reaches the ladder).
	blockReason := out.StuckReason
	if blockReason == "" {
		blockReason = strings.TrimPrefix(out.Result, "flag_stuck: ")
	}
	reason := blockReason

	// (token_runaway abandonment would sit here — brake unported.)

	// NEED_INFO: the step explicitly requests context — research it,
	// then retry the original (research first, original second). Prefix
	// check, Python parity: a reason merely MENTIONING the marker is not
	// a request.
	if strings.HasPrefix(reason, needInfoPrefix) {
		infoNeeded := strings.TrimSpace(reason[len(needInfoPrefix):])
		// The exec.go composition appends " (attempted: …)" — that is
		// retry forensics, not part of what to research.
		if i := strings.LastIndex(infoNeeded, " (attempted: "); i >= 0 {
			infoNeeded = infoNeeded[:i]
		}
		return blockDecision{
			splitInto:  []string{"Research: " + infoNeeded, stepText},
			metaReason: "step requested info: " + clipHead(infoNeeded, 100),
		}
	}

	// (escape-pattern demotion hints would sit here — detectors unported.)

	// Missing external input: retrying won't make it appear, splitting
	// won't help, and re-decomposing FABRICATES a synthetic stand-in and
	// fakes success (the fabricated-input false-success bug). Honest
	// terminal instead. (Python excludes "[ralph verify]"-prefixed
	// reasons — no ralph verifier here yet; the guard returns with it.)
	// Both signal sources, Python parity (_looks_like_missing_input on
	// block_reason OR step_result — the attempted text names the missing
	// resource when the reason doesn't). Honest scope (r2, Skeptic): the
	// second signal is live for flag_stuck ONLY — the other blocked
	// producers have no Attempted, and Go's llm.ResultError carries no
	// partial-output field, so Python's killed-subprocess tail (last
	// 2000 chars, step_exec.py:1659, which routinely contains the
	// "not found" text) is structurally unavailable until the adapter
	// grows a partial-output carrier. Named in PORT.md.
	if isInputConsumingStep(stepText) &&
		(looksLikeMissingInput(blockReason) || looksLikeMissingInput(out.Attempted)) {
		return blockDecision{
			stuckReason: fmt.Sprintf(
				// clip(block_reason, 1000) — Python's runaway bound above
				// measured max. The old FailureChainEntry (600) inner clip
				// spent the ENTIRE chain-entry budget on the raw reason, so
				// the outer clip in loop.go always dropped the trailing
				// do-not-fabricate instruction (adversarial ladder review
				// 2026-08-22, Architect HIGH). The nested inner+outer clip
				// itself is Python parity (clip(_stuck_reason, 600) at the
				// chain append).
				"MISSING_INPUT: a required input appears absent — %s. "+
					"A missing external input cannot be retried, split, or manufactured; "+
					"escalate for the real input rather than fabricating one.",
				budget.BlockReason.Clip(blockReason)),
			metaReason:  "missing external input — honest fail, do not fabricate",
			stopVerdict: "external-interrupt",
		}
	}

	// Combined exec+analyze steps are structurally wrong — split on the
	// first block regardless of reason; retrying the shape won't fix it.
	// (Reaching this reactively should now be rare: Run shapes the
	// initial plan too, Python's label="initial-plan" pass.)
	if isCombinedExecAnalyze(stepText) {
		return blockDecision{
			splitInto:  splitExecAnalyze(stepText),
			metaReason: "combined exec+analyze step shape — structural split",
		}
	}

	// Timeouts must not be retried identically — the subprocess just
	// times out again. Split smaller, or stop honestly if splitting fails.
	if strings.Contains(strings.ToLower(blockReason), "timed out") {
		split, tin, tout := generateTimeoutSplit(ctx, a, stepText)
		if len(split) > 0 {
			return blockDecision{
				splitInto:  split,
				metaReason: "timeout — decomposed into smaller steps",
				tokensIn:   tin, tokensOut: tout,
			}
		}
		return blockDecision{
			stuckReason: fmt.Sprintf(
				"TIMEOUT and split-recovery failed: %s. "+
					"Consider narrowing the step scope or switching to an API adapter.",
				blockReason),
			metaReason:  "timeout and split-recovery failed — terminal",
			stopVerdict: "out-of-budget",
			tokensIn:    tin, tokensOut: tout,
		}
	}

	// (mid-loop diagnosis consult would sit here — introspect unported.)

	converging := isConverging(fingerprints)
	siblingRate := siblingFailureRate(siblings)
	metaCtx := fmt.Sprintf("retries=%d, converging=%v, sibling_fail_rate=%.0f%%, replan_count=%d",
		priorRetries, converging, siblingRate*100, replanCount)

	// Sibling failure correlation first: most siblings failing means the
	// decomposition is wrong — redecompose, don't retry harder.
	if siblingRate > siblingThreshold && len(siblings) >= 3 && replanCount < redecomposeThreshold {
		return blockDecision{
			redecompose: true,
			metaReason: fmt.Sprintf(
				"sibling failure rate %.0f%% exceeds %.0f%% threshold — decomposition is likely wrong (%s)",
				siblingRate*100, siblingThreshold*100, metaCtx),
		}
	}

	// Standard retry: under threshold AND the errors are converging.
	if priorRetries < retryThreshold && converging {
		var hint string
		var hintTin, hintTout int
		if priorRetries == 0 {
			// The retry acts on WHY it was blocked — the reason travels
			// wide (Python's [:120] cut 93% of live reasons; 1000 is the
			// runaway bound above measured max, caps sweep 2026-08-21).
			hint = fmt.Sprintf(
				"[Previous attempt blocked: %s] "+
					"Try an alternative approach: use a different tool, rephrase the request, "+
					"work around the obstacle, or summarize what you know so far and mark complete. "+
					"If you lack required information, say NEED_INFO: [what's missing] instead of guessing.",
				clipHead(blockReason, 1000))
		} else {
			// partial_result=step_result in Python — the attempted text
			// for a flag_stuck block, not the (always-empty-on-blocked)
			// Summary.
			hint, hintTin, hintTout = generateRefinementHint(ctx, a, stepText, blockReason, out.Attempted)
		}
		return blockDecision{
			retry:      true,
			hint:       hint,
			metaReason: fmt.Sprintf("retry — errors converging, under threshold (%s)", metaCtx),
			tokensIn:   hintTin, tokensOut: hintTout,
		}
	}

	// Not converging or threshold exceeded — re-decompose while budget lasts.
	if replanCount < redecomposeThreshold {
		return blockDecision{
			redecompose: true,
			metaReason: fmt.Sprintf(
				"not converging after %d retries — re-decomposing step (%s)", priorRetries, metaCtx),
		}
	}

	// Exhausted — terminal, with the convergence evidence riding along.
	return blockDecision{
		stuckReason: blockReason,
		metaReason: fmt.Sprintf(
			"exhausted: %d retries, %d re-decompositions, converging=%v, sibling_rate=%.0f%%",
			priorRetries, replanCount, converging, siblingRate*100),
		stopVerdict: "thesis-refuted",
	}
}
