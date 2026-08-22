// Package closure ports the director closure check — goal-level
// completion verification (closure_verify.py + the deterministic half of
// director.evaluate_closure).
//
// The pipeline: plan checks by INVERSION (LLM) → run them MECHANICALLY
// (shell, no LLM judgment on exit codes) → verdict (LLM) → verdict
// integrity (ungrounded-False confidence cap, behavioral-gap downgrade)
// → deterministic decision mapping. The lessons ported with the code:
//
//   - done ≠ successful: the loop's "done" says the steps drained; only
//     closure says the GOAL was achieved. The two stamps stay separate.
//   - Absence means NOT JUDGED, never failed: a verdict resting on the
//     verifier's own failures (missing tool, cwd unresolved, timeout)
//     is missing data, not disproof (Python 2026-07-09 dogfood batch:
//     4/5 known-good runs false-negatived by verifier failures).
//   - Evidence is required for a confident False: a complete=false that
//     contradicts every executed probe, with no file content in front
//     of the judge, rests on the work summary's narration — its
//     confidence is capped below the trust floor (run 2738d9c0).
//   - The skip verdict is unjudged, not complete=true: "treating as
//     complete" while closure crashes silently is the exact
//     partial-masquerading-as-result failure closure exists to catch
//     (Python 2026-07-29, four days of dead closure).
//   - Every closure outcome — full verdict or named skip — leaves a
//     durable row (persist-the-artifacts decree, Jeremy 2026-07-29).
//
// Deliberately unported with their subsystems (see PORT.md): scope
// failure modes, resolved-intent deliverables + precondition preflight,
// stated-guarantee claim coverage, the verdict audit (second-opinion
// pass), NEXT.md ledger gap, failed-check file evidence, behavioral-gap
// Signals 2/3, and the restart machinery that CONSUMES the decision
// actions (the director recommends; callers dispose — the Go caller
// records the recommendation it cannot yet act on).
package closure

import (
	"context"
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os/exec"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// Verdict mirrors Python ClosureVerdict — the evidence record.
type Verdict struct {
	Complete          bool
	Confidence        float64
	Gaps              []string
	Summary           string
	ChecksRun         int
	ChecksPassed      int
	InconclusiveCount int
	// Judged is false when the verdict hinges entirely on inconclusive
	// probes — no check ran cleanly and disproved the work. An unjudged
	// verdict must never be recorded as goal_achieved=false: absence of
	// the stamp means "not judged".
	Judged bool
	// DowngradeReason says why a complete=true LLM verdict was flipped
	// to false by the deterministic downgrade branch; "" when none
	// fired. Surfaced beside the stamp so a false next to a positive
	// narrative reads as cause, not contradiction.
	DowngradeReason string
	// FailedChecks holds signatures of hard-FAILED checks (outcome ==
	// "fail" only — inconclusive is a verifier failure, not goal
	// evidence). Feeds Fingerprint so restart convergence can be judged
	// structurally (§9.3).
	FailedChecks []string
	// SkipReason names why verification did not run ("" = it ran).
	SkipReason string
}

// CheckResult is one mechanically-executed probe.
type CheckResult struct {
	Description string `json:"description"`
	Command     string `json:"command"`
	Modality    string `json:"modality"`
	ExitCode    int    `json:"exit_code"`
	Stdout      string `json:"stdout"`
	Stderr      string `json:"stderr"`
	Passed      bool   `json:"passed"`
	Outcome     string `json:"outcome"`
}

// CheckOutcome classifies a probe outcome as pass, fail, or
// inconclusive — ported branch-for-branch from _check_outcome. The
// inconclusive lanes all mean "the probe's own tooling failed": the
// command never ran to a clean true/false answer, so it can neither
// prove nor disprove the work. AssertionError-class failures stay
// "fail" — the check RAN and the asserted fact was false.
func CheckOutcome(exitCode int, stderr string) string {
	if exitCode == 0 {
		return "pass"
	}
	err := strings.ToLower(stderr)
	if exitCode == -1 || exitCode == 126 || exitCode == 127 {
		return "inconclusive"
	}
	if strings.Contains(err, "command not found") || strings.Contains(err, "not on path") ||
		strings.Contains(err, "no such file or directory") {
		return "inconclusive"
	}
	if strings.Contains(err, "timed out") || strings.Contains(err, "timeout") {
		return "inconclusive"
	}
	// Verifier-authored syntax errors: a python -c one-liner that didn't
	// parse reports File "<string>"/"<stdin>" (Python witty-spruce run: a
	// format-validation probe scored as a goal failure off exactly this);
	// a SyntaxError pointing at a real file is the WORK failing to parse
	// and stays "fail".
	if strings.Contains(err, "syntaxerror") &&
		(strings.Contains(err, `"<string>"`) || strings.Contains(err, `"<stdin>"`)) {
		return "inconclusive"
	}
	if strings.Contains(err, "syntax error near unexpected token") ||
		strings.Contains(err, "syntax error: ") {
		return "inconclusive"
	}
	if strings.Contains(err, "permission denied") || strings.Contains(err, "operation not permitted") {
		return "inconclusive"
	}
	// The verifier's view lacks the host's .git (containerized runs mount
	// source + records only) — an environment-blind probe proves nothing.
	if strings.Contains(err, "not a git repository") {
		return "inconclusive"
	}
	return "fail"
}

// FailedCheckSignature is the fingerprint material for one hard-failed
// check (§9.3): command + exit code + a bounded slice of failure output.
// Output is included so a broad command (`pytest -q`) failing on
// DIFFERENT tests across attempts fingerprints differently — the
// identity being matched is the failure, not the probe name.
// Nondeterministic output (timestamps, tmp paths) can only make
// fingerprints DIFFER, which fails open to a normal restart.
func FailedCheckSignature(r CheckResult) string {
	cmd := r.Command
	if len(cmd) > 200 {
		cmd = cmd[:200]
	}
	out := strings.Join(strings.Fields(r.Stderr+" "+r.Stdout), " ")
	if out != "" {
		if len(out) > 200 {
			out = out[:200]
		}
		return fmt.Sprintf("%s => exit %d: %s", cmd, r.ExitCode, out)
	}
	return fmt.Sprintf("%s => exit %d", cmd, r.ExitCode)
}

// Fingerprint is the stable fingerprint of a verdict's hard failures
// (§9.3) — the plan-level twin of the step ladder's error fingerprint:
// two verdicts with the same fingerprint mean the second attempt failed
// IDENTICALLY, so the restart made zero map edits and restarting again
// is evidence-free. Deterministic, zero LLM calls, order-insensitive.
// Returns "" when the verdict has no hard-failed checks; callers must
// treat "" as no-signal, never as a match. Byte-parity with CPython
// closure_fingerprint (md5, first 12 hex chars) so cross-runtime
// verdict rows compare.
func Fingerprint(v Verdict) string {
	if len(v.FailedChecks) == 0 {
		return ""
	}
	norm := make([]string, 0, len(v.FailedChecks))
	for _, c := range v.FailedChecks {
		s := strings.Join(strings.Fields(c), " ")
		if len([]rune(s)) > 500 {
			s = string([]rune(s)[:500])
		}
		norm = append(norm, s)
	}
	sort.Strings(norm)
	sum := md5.Sum([]byte(strings.Join(norm, "|")))
	return hex.EncodeToString(sum[:])[:12]
}

var verdictOpenerRe = regexp.MustCompile(`(?i)^\s*(?:the\s+)?goal\s+(?:was\s+|is\s+)?(?:not\s+)?(?:fully\s+)?achieved[.!:,]?\s*`)

// VerdictFirstSummary opens the stored summary with the FLAG's verdict,
// deterministically. Every surface showing the summary bounds it, and
// the LLM's prose opener has already contradicted the flag once (Python
// run d2f4e2f4: goal_achieved=false beside a visible prefix reading
// "Goal achieved."). The flag writes the opener; the prose can only
// elaborate, never contradict, in any truncated view. Summaries the
// downgrade branch already rewrote are left alone — that opener is
// code-written and verdict-first.
func VerdictFirstSummary(summary string, complete, judged bool) string {
	if strings.HasPrefix(summary, "Downgraded to not-achieved") {
		return summary
	}
	body := strings.TrimSpace(verdictOpenerRe.ReplaceAllString(summary, ""))
	prefix := "Not achieved"
	if !judged {
		prefix = "Not judged (verification evidence inconclusive)"
	} else if complete {
		prefix = "Achieved"
	}
	if body == "" {
		return prefix + "."
	}
	return prefix + ": " + body
}

// Work-summary window, Python-parity literals (closure_verify.py,
// measured 2026-08-03 over 268 loop payloads: at 300 the verdict saw
// 23% of the evidence; 4000 shows 93% of payloads whole). The
// truncation markers are byte-parity with Python — they are
// JUDGE-facing honesty text ("the rest was NOT shown to you"), and the
// judge prompt's TRUNCATED doctrine keys off them; the budget package's
// generic marker is the wrong vocabulary here.
const (
	workSummaryResultCut = 4000
	workSummaryTextCut   = 300
	workSummarySteps     = 6
)

// RenderStepForClosure renders one step for the closure work summary,
// truncation VISIBLE: a judge told "Result: …" cannot tell a whole
// answer from its first quarter, and will report what it cannot see as
// missing (Python run 2738d9c0).
func RenderStepForClosure(text, result string, index int) string {
	head := text
	if len(head) > workSummaryTextCut {
		head = head[:workSummaryTextCut] +
			fmt.Sprintf("… [step text truncated at %d]", workSummaryTextCut)
	}
	if len(result) > workSummaryResultCut {
		return fmt.Sprintf("Step %d: %s\nResult [TRUNCATED — showing the first %d of %d characters; the rest was NOT shown to you]: %s",
			index, head, workSummaryResultCut, len(result), result[:workSummaryResultCut])
	}
	return fmt.Sprintf("Step %d: %s\nResult: %s", index, head, result)
}

// Ungrounded-False cap. The floor is the trust line above which a
// judged verdict gates learning and demotes a run (Python
// VERDICT_CONFIDENCE_FLOOR); the capped value sits just below it —
// still recorded, still visible, directional-only.
const (
	ungroundedFalseFloor      = 0.7
	ungroundedFalseConfidence = 0.65
)

// StepView is the slice of a loop step closure needs.
type StepView struct {
	Text   string
	Result string
}

// Options tune Verify.
type Options struct {
	// WorkspacePath is where checks run — the project dir the executor
	// actually wrote into. The cwd contract (Python 2026-07-02 burn-in):
	// falling back to the launcher's cwd made every artifact check probe
	// the wrong directory (3/3 verdicts false-negative on fully
	// successful work). Empty = checks are refused as env-unresolved
	// inconclusive rows, never run somewhere arbitrary.
	WorkspacePath string
	DryRun        bool
	// TimeoutPerCheck bounds one probe (zero = 30s, the Python default).
	TimeoutPerCheck time.Duration
	LoopID          string
	// PersistRow, when non-nil, receives every closure outcome row —
	// full verdict or named skip — for the durable
	// build/closure_verdicts.jsonl record (persist-the-artifacts
	// decree). Best-effort: errors are the callback's concern.
	PersistRow func(row map[string]any)
}

const closurePlanSystem = `You are the Director performing a closure check after an agent loop completed.

You verify by INVERSION: given the goal and what was done, your job is to probe
whether any of the ways this work could be silently wrong actually happened.

How to reason:
1. Do your own inversion first. Given this specific goal and this specific work
   summary, enumerate 3-5 ways a claim of "done" could be hiding a silent
   failure. Then derive checks from those.
2. Reason from the actual work done, not from goal type templates. The right
   check for "build a server" depends on whether the work stopped at compiling
   (probe: does it actually respond?), at starting (probe: does it handle a
   real request?), or at integration (probe: does the documented client path
   work?). Let the work steer the check.
3. Behavioral probes: when the work summary suggests a running artifact,
   service, CLI, endpoint, websocket, or UI flow, prefer at least one
   behavioral probe that actually exercises it (http/ws/process — not just a
   static file check). A runtime-shaped deliverable "verified" only by a
   static check (file exists, code compiles) is unverified. Cheap scaffolding
   is encouraged when it makes runtime probing mechanical, for example:
   - start a server in background with cleanup: tmp=$(mktemp); (python app.py >$tmp 2>&1 &) ; pid=$!; trap 'kill $pid' EXIT; sleep 2; curl -fsS http://127.0.0.1:8000/health
   - exercise a CLI or built binary directly: ./bin/tool --help >/tmp/tool.out && grep -q 'usage' /tmp/tool.out

Output rules:
- Generate 2-5 checks. Each must be a single shell command.
- Each check MUST name which failure mode (or inversion hypothesis) it probes.
- When a file inventory of the working directory is provided, probe those
  exact paths — do NOT invent expected filenames. A deliverable saved under
  a different name than you'd guess is still delivered; a check against a
  guessed name that fails proves nothing about the goal.
- When probing the content of a file you have not read, prefer predicates
  over the whole file (e.g. grep -qiE 'urgent|deadline' file) to
  position/format-specific pipelines — numbered-list or quote-prefix
  assumptions break on tables and code fences and fail work that is fine.
- Static checks (grep, file existence, compile-only) must be fast (<15s).
  Behavioral probes (server start, CLI invocation) may take up to
  __TIMEOUT_PER_CHECK__s if they need brief startup time. All checks must be
  safe (read-only or self-cleaning) and exit 0 on success. Wrap background
  processes with timeout and always clean up PIDs.
- Prefer robust checks over brittle string-matching theater. If a grep
  pattern would be sensitive to log formatting or harmless wording changes,
  prefer a stronger structural predicate (jq, exact JSON field checks,
  endpoint status codes, process exit codes, or grep -E patterns that only
  encode the essential invariant).
- Working directory provided — use relative paths from there.
- Do NOT assume the working directory is a git checkout: containerized runs
  see a partial mount (no .git), so plan git-based probes only when the work
  summary itself shows git commands succeeding.
- If the goal produces no executable artifact (research, writing, analysis),
  return an empty list. If a failure mode cannot be mechanically probed in
  this environment (missing port, external service, credential needed), skip
  that failure mode rather than fabricate a weak check.

Respond with JSON only:
{"checks": [{"failure_mode": "...", "description": "...", "command": "..."}]}`

const closureVerdictSystem = `You are the Director reviewing verification results after an agent loop completed.

Given the original goal, the agent's work summary, and the results of executable
verification checks, decide whether the goal was genuinely achieved.

Be honest. If checks failed or were skipped, say so. If any probe was
inconclusive (missing tool, command not found, timeout, probe could not run),
do not treat that as evidence the goal works — but do not treat it as
evidence of failure either. An inconclusive probe is missing data: judge
completeness from the checks that did run. If the passing checks cover the
goal's deliverables and no check failed, complete=true is the honest
verdict even with an inconclusive probe in the mix.

A step's Result may be marked TRUNCATED. When it is, you are reading the
beginning of that step's output and the rest was withheld from you. Do
not report as missing anything you simply cannot find in a truncated
Result — that is a fact about your window, not about the work.

You may only assert what a file CONTAINS when that content is in front of
you — in a check's stdout. If no check surfaced a deliverable's content,
you do not know what it holds, and you must not describe it. Say the
verification did not cover it, and give a confidence below 0.7. A verdict
that contradicts every passing check while resting only on the work
summary's narration is how correct runs get failed: the summary quotes and
explains its own output, and those quotations are not the file.

Respond with JSON only:
{
  "complete": true|false,
  "confidence": 0.0-1.0,
  "gaps": ["specific gap 1", "specific gap 2"],
  "summary": "one or two sentences, opening with the verdict ('Goal achieved.' or 'Goal not achieved.') matching the complete flag"
}`

// nullVerdict is the skip verdict: UNJUDGED, not complete — verification
// never ran, so it carries no evidence in either direction.
func nullVerdict(reason string) Verdict {
	return Verdict{
		Complete: false, Confidence: 0, Judged: false,
		Summary: "Verification did not run.", SkipReason: reason,
	}
}

// runCheck executes one probe under bash -c with the per-check timeout.
func runCheck(ctx context.Context, cmd, cwd string, timeout time.Duration) (exitCode int, stdout, stderr string) {
	cctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	c := exec.CommandContext(cctx, "bash", "-c", cmd)
	c.Dir = cwd
	var out, errb strings.Builder
	c.Stdout = &out
	c.Stderr = &errb
	err := c.Run()
	if cctx.Err() == context.DeadlineExceeded {
		return -1, out.String(), "timed out"
	}
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			return ee.ExitCode(), out.String(), errb.String()
		}
		return -1, out.String(), err.Error()
	}
	return 0, out.String(), errb.String()
}

// Verify runs the closure evidence pipeline: plan → mechanical checks →
// verdict → integrity branches. Non-fatal by contract — it never
// returns an error; every failure lands as a named skip (unjudged) or
// as verdict evidence. The caller stamps goal_achieved from
// (Judged, Complete): unjudged verdicts stamp NOTHING.
func Verify(ctx context.Context, a llm.Adapter, goal string, steps []StepView, o Options) Verdict {
	persist := func(row map[string]any) {
		if o.PersistRow != nil {
			o.PersistRow(row)
		}
	}
	if o.DryRun || a == nil {
		// Intentional skips — no row, matching Python (dry_run/no-adapter
		// return before the skip-row machinery).
		return nullVerdict("dry_run")
	}
	timeout := o.TimeoutPerCheck
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	skip := func(reason string, detail string) Verdict {
		row := map[string]any{"skipped": reason}
		if detail != "" {
			if len(detail) > 300 {
				detail = detail[:300]
			}
			row["skip_detail"] = detail
		}
		persist(row)
		return nullVerdict(reason)
	}

	// Work summary from the last N steps — this string feeds BOTH the
	// plan call and the verdict call; it IS the evidence the verdict
	// reasons from whenever no probe surfaced the content directly.
	var parts []string
	for i, s := range steps {
		if s.Result != "" || s.Text != "" {
			parts = append(parts, RenderStepForClosure(s.Text, s.Result, i+1))
		}
	}
	if len(parts) > workSummarySteps {
		parts = parts[len(parts)-workSummarySteps:]
	}
	workSummary := "(no step detail available)"
	if len(parts) > 0 {
		workSummary = strings.Join(parts, "\n\n")
	}

	inventoryBlock := ""
	if o.WorkspacePath != "" {
		if inv := projectFileInventory(o.WorkspacePath, 120); inv != "" {
			inventoryBlock = "Files that actually exist under the working directory " +
				"(ground truth — probe these exact paths, do not invent names):\n" + inv + "\n\n"
		}
	}
	wd := o.WorkspacePath
	if wd == "" {
		wd = "(unspecified)"
	}

	// Phase 1: generate the verification plan.
	planResp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: strings.ReplaceAll(closurePlanSystem,
			"__TIMEOUT_PER_CHECK__", fmt.Sprintf("%d", int(timeout.Seconds())))},
		{Role: "user", Content: fmt.Sprintf(
			"Goal: %s\n\nWorking directory: %s\n\n%sWork done:\n%s",
			goal, wd, inventoryBlock, workSummary)},
	}, llm.Options{MaxTokens: 1024, Temperature: 0.1, Purpose: "closure plan"})
	if err != nil {
		return skip("plan_call_failed", err.Error())
	}
	planObj, perr := jsonx.Object(planResp.Content)
	type planCheck struct{ description, command string }
	var planChecks []planCheck
	if perr == nil {
		if raw, ok := planObj["checks"].([]any); ok {
			for _, e := range raw {
				m, ok := e.(map[string]any)
				if !ok {
					continue
				}
				desc, _ := m["description"].(string)
				cmd, _ := m["command"].(string)
				planChecks = append(planChecks, planCheck{desc, cmd})
			}
		}
	}
	if len(planChecks) == 0 {
		// Research/writing goal — no executable checks. A parse failure
		// lands in the same lane with its detail named.
		detail := ""
		if perr != nil {
			detail = perr.Error()
		}
		return skip("no_checks_generated", detail)
	}

	// Phase 2: run checks mechanically. No LLM judgment on exit codes.
	var results []CheckResult
	checks := planChecks
	if len(checks) > 5 {
		checks = checks[:5]
	}
	for _, c := range checks {
		if c.command == "" {
			continue
		}
		modality := ClassifyProbeModality(c.command)
		if o.WorkspacePath == "" {
			// B3(a) probe-env hardening: the cwd never resolved. Running
			// anyway would silently probe the launcher's own directory —
			// a confident-looking but meaningless pass/fail. Honestly
			// inconclusive instead.
			results = append(results, CheckResult{
				Description: c.description, Command: c.command,
				Modality: modality, ExitCode: -1,
				Stderr:  "cwd unresolved — check not run",
				Outcome: "inconclusive",
			})
			continue
		}
		code, stdout, stderr := runCheck(ctx, c.command, o.WorkspacePath, timeout)
		if len(stdout) > 500 {
			stdout = stdout[:500]
		}
		if len(stderr) > 300 {
			stderr = stderr[:300]
		}
		results = append(results, CheckResult{
			Description: c.description, Command: c.command,
			Modality: modality, ExitCode: code,
			Stdout: stdout, Stderr: stderr,
			Passed:  code == 0,
			Outcome: CheckOutcome(code, stderr),
		})
	}
	if len(results) == 0 {
		return skip("no_check_results", "")
	}

	checksRun := len(results)
	checksPassed := 0
	inconclusive := 0
	var failedSigs []string
	for _, r := range results {
		if r.Passed {
			checksPassed++
		}
		switch r.Outcome {
		case "inconclusive":
			inconclusive++
		case "fail":
			failedSigs = append(failedSigs, FailedCheckSignature(r))
		}
	}

	// Phase 3: the director interprets results.
	resultsJSON, _ := json.MarshalIndent(results, "", "  ")
	verdictResp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: closureVerdictSystem},
		{Role: "user", Content: fmt.Sprintf(
			"Goal: %s\n\nWork done:\n%s\n\nVerification results:\n%s",
			goal, workSummary, string(resultsJSON))},
	}, llm.Options{MaxTokens: 256, Temperature: 0.1, Purpose: "closure verdict"})
	if err != nil {
		return skip("verdict_call_failed", err.Error())
	}
	vdObj, verr := jsonx.Object(verdictResp.Content)
	if verr != nil {
		return skip("verdict_parse_failed", "")
	}
	// Go-stricter refusal (named in PORT.md): Python defaults a MISSING
	// "complete" key to true; here a verdict without its load-bearing
	// flag is not a verdict — same skip lane as unparseable JSON.
	complete, ok := vdObj["complete"].(bool)
	if !ok {
		return skip("verdict_parse_failed", "complete flag missing or non-bool")
	}
	confidence := 0.7
	if f, ok := vdObj["confidence"].(float64); ok {
		confidence = f
		if confidence < 0 {
			confidence = 0
		}
		if confidence > 1 {
			confidence = 1
		}
	}
	var gaps []string
	if raw, ok := vdObj["gaps"].([]any); ok {
		for _, g := range raw {
			if gs, ok := g.(string); ok && gs != "" {
				gaps = append(gaps, gs)
			}
		}
	}
	summary, _ := vdObj["summary"].(string)

	// Ungrounded-False cap: a complete=false that contradicts EVERY
	// executed probe has no evidence of failure behind it — only the work
	// summary's narration. This hides nothing (complete/gaps/summary are
	// recorded as the judge wrote them); it denies the verdict the
	// STANDING to demote a run or teach a failure. "No probe looked" is
	// insufficient coverage, not proof of failure — the same principle as
	// absence-means-not-judged. Note the Go check rows never carry file
	// content (failed-check file evidence is unported), so this cap
	// covers every all-passed confident-False.
	allPassed := checksPassed == checksRun
	if !complete && checksRun > 0 && allPassed && confidence >= ungroundedFalseFloor {
		confidence = ungroundedFalseConfidence
		summary = strings.TrimSpace(fmt.Sprintf(
			"%s [verdict confidence capped: all %d checks passed and no file content was in evidence, so this not-achieved rests on the work summary's narration rather than on probe output]",
			summary, checksRun))
	}

	modalityDist := map[string]int{}
	for _, r := range results {
		modalityDist[r.Modality]++
	}

	// Behavioral-evidence downgrade (Signal 1): the verdict claims
	// complete but its own prose admits runtime wasn't exercised and no
	// behavioral probe ran — flip to false so the stamp carries the gap.
	downgradeReason := DetectBehavioralGap(complete, summary, gaps, modalityDist)
	if downgradeReason != "" {
		complete = false
		summary = "Downgraded to not-achieved — " + downgradeReason + ". " + summary
	}

	// Judged tri-state: false when the verdict hinges entirely on
	// inconclusive probes — no check ran cleanly.
	judged := checksRun > inconclusive

	v := Verdict{
		Complete: complete, Confidence: confidence, Gaps: gaps,
		Summary:      VerdictFirstSummary(summary, complete, judged),
		ChecksRun:    checksRun,
		ChecksPassed: checksPassed, InconclusiveCount: inconclusive,
		Judged: judged, DowngradeReason: downgradeReason,
		FailedChecks: failedSigs,
	}
	cmds := make([]string, 0, len(results))
	for _, r := range results {
		cmds = append(cmds, r.Command)
	}
	persist(map[string]any{
		"complete": v.Complete, "confidence": v.Confidence,
		"gaps": v.Gaps, "summary": v.Summary,
		"checks_run": v.ChecksRun, "checks_passed": v.ChecksPassed,
		"inconclusive_count": v.InconclusiveCount, "judged": v.Judged,
		"downgrade_reason":      v.DowngradeReason,
		"failed_checks":         v.FailedChecks,
		"fingerprint":           Fingerprint(v),
		"modality_distribution": modalityDist,
		"commands":              cmds,
	})
	return v
}
