// Executor lane: tool-bearing worker steps (port tranche 1 after the
// pack tranche — Python step_exec.py's subprocess path).
//
// The worker is the claude CLI's own agent: its Bash/Read/Write tools do
// the real work bound to the run's project dir, then it reports through
// the simulated tool-call protocol (complete_step / flag_stuck). Ported
// subset, stated honestly: no container lane, no executor sessions, no
// constraint tiers, no URL pre-fetch, no artifact/decision/world-fact
// channels — the prompt deliberately does NOT advertise fields this loop
// would drop silently (see PORT.md for the full unported list).
package loop

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
)

// execSystem is the executor system prompt — the sections of Python
// EXECUTE_SYSTEM whose machinery exists in this runtime, near-verbatim.
// Dropped sections (ask-may-be-wrong, also-noticed, artifacts, decisions,
// world facts, pre-fetched URL protocol) are named in PORT.md; each
// referenced a channel this loop does not consume yet, and advertising a
// field that gets silently dropped is worse than omitting it.
const execSystem = `You are an autonomous execution agent.
Complete the given step and call exactly one tool:
  - complete_step: successfully completed
  - flag_stuck: genuinely blocked (explain precisely)
Do NOT flag_stuck for solvable problems — work through them first.

SYNCHRONOUS EXECUTION — the step's work happens INSIDE the step:
Run commands to completion in the foreground and report their observed
output. NEVER start a background job, monitor, watcher, or timer and
return saying you are "waiting" or "will be notified when it completes" —
your session ends the moment you return, so nothing survives to receive
that notification and the work simply never happens. A result that
promises future completion ("started X, it will finish later") is a
FAILED step. If an operation is too large to finish here, execute a
bounded subset now and report its actual results.
(Long-lived servers/daemons are the one exception: background-spawn,
then probe readiness and report the observed probe result in this same
step — that is finished work, not a promise.)

ANTI-HALLUCINATION:
If you cannot verify a claim from code or data you have directly read in
this step, do NOT state it as fact. Mark unverified claims as [UNVERIFIED]
or use inject_steps to add a verification sub-step.
NEVER guess file paths, line numbers, function names, or variable names.
If the step requires information you don't have, use NEED_INFO (see below).

NEED_INFO — WHEN YOU LACK REQUIRED INFORMATION:
If a step requires data, code, or context that you do not have access to
in this execution context, you have two options:
1. Use inject_steps in your complete_step call to add 1-3 research/verification
   sub-steps that will gather the missing information.
2. Call flag_stuck with reason "NEED_INFO: [describe what's missing]".
Do NOT guess or fabricate information to fill gaps.

URL FETCHING:
NEVER curl a web page into context: raw HTML is 20-100x larger than the
markdown (one retailer page ~= 190k tokens) and will blow the step's budget.
curl a JSON API only when the response is known-small — cap it anyway
(curl ... | head -c 20000) or save it to a file (curl -o resp.json ...)
and extract just the fields you need with jq/python; a multi-megabyte JSON
body blows the budget exactly like raw HTML.
Oversized command output does not reach you either way: the harness
truncates it and saves the full output to a file whose path appears in
the result — plan around that file instead of re-running the command.
EXCEPTION: Goal-level CLI/SDK/tool instructions override this — use those tools.

FILE EDITS:
Before writing to a file that already exists, read it (or the relevant
slice) first — file-editing tools refuse to overwrite a file you have
not read this session, and a blind overwrite destroys content you never
saw. Writing a brand-new file needs no prior read.

TOKEN EFFICIENCY:
1. Extract 2-3 key facts from sources; never quote long passages verbatim.
2. Output: bullet points or structured JSON. No preamble, no sign-offs.
3. Work with partial information rather than flagging stuck.
4. Target under 500 tokens for complete_step. More = you're quoting, not summarizing.`

// stepTools is the executor tool contract — Python EXECUTE_TOOLS trimmed
// to the fields this loop actually consumes (result, summary, confidence,
// inject_steps; flag_stuck's reason + attempted). The dropped fields
// (artifacts, decisions, world_facts, create_team_worker) return with
// their consumers in later tranches.
func stepTools() []llm.Tool {
	return []llm.Tool{
		{
			Name:        "complete_step",
			Description: "Mark this step as complete and record the result.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"result": map[string]any{
						"type":        "string",
						"description": "The work product, findings, or output of this step.",
					},
					"summary": map[string]any{
						"type":        "string",
						"description": "One-sentence summary of what was accomplished.",
					},
					"confidence": map[string]any{
						"type": "string",
						"enum": []any{"strong", "weak", "inferred", "unverified"},
						"description": "How confident you are in this result. " +
							"strong = verified/cited; weak = partial/indirect; " +
							"inferred = reasoned from context; unverified = not independently confirmed.",
					},
					"inject_steps": map[string]any{
						"type":  "array",
						"items": map[string]any{"type": "string"},
						"description": "Optional: list of additional steps to insert immediately after this step. " +
							"Use when this step reveals unexpected work that must happen before " +
							"the planned next step (e.g. a dependency is missing, a file needs " +
							"fetching, a subtask was discovered mid-execution). " +
							"Injected steps run in order before the original remaining plan resumes. " +
							"Maximum 3 injected steps. Keep each under 20 words.",
					},
				},
				"required": []any{"result", "summary"},
			},
		},
		{
			Name:        "flag_stuck",
			Description: "Signal that this step cannot be completed, with a precise reason.",
			Parameters: map[string]any{
				"type": "object",
				"properties": map[string]any{
					"reason": map[string]any{
						"type":        "string",
						"description": "Why this step cannot be completed.",
					},
					"attempted": map[string]any{
						"type":        "string",
						"description": "What was tried before giving up.",
					},
				},
				"required": []any{"reason"},
			},
		},
	}
}

// agentToolCapable reports whether the backend can run tool-bearing
// executor steps (the subprocess CLI can; API/fake backends say so
// themselves). Structural gate, not a name check.
func agentToolCapable(a llm.Adapter) bool {
	c, ok := a.(llm.AgentToolsCapable)
	return ok && c.SupportsAgentTools()
}

// goalSlug ports Python _goal_to_slug: lowercase, strip everything
// outside [a-z0-9 ], first five words joined by dashes.
var slugStrip = regexp.MustCompile(`[^a-z0-9 ]`)

func goalSlug(goal string) string {
	words := strings.Fields(slugStrip.ReplaceAllString(strings.ToLower(goal), ""))
	if len(words) > 5 {
		words = words[:5]
	}
	slug := strings.Join(words, "-")
	if slug == "" {
		return "unnamed-goal"
	}
	return slug
}

// Long-running step classification, ported from Python step_exec: steps
// that spawn builds/test suites get more wall-clock than the default,
// full-suite runs get double (capped at an hour). Env override:
// MARO_LONG_RUNNING_TIMEOUT (seconds).
var longRunningKeywords = []string{
	"pytest", "test suite", "npm run", "make ", "docker ", "pip install",
	"git clone", "build ", "compile", "deploy", "cargo ", "mvn ",
}

// Trailing space in "tests/ " is deliberate: matches "pytest tests/ -q"
// but not "tests/test_foo.py" (Python parity).
var fullSuiteHints = []string{
	"tests/ ", "tests -q", "all tests", "full suite", "test suite",
}

const execStepDefaultTimeout = 600 * time.Second

func classifyStepTimeout(step string) time.Duration {
	lower := strings.ToLower(step)
	longRunning := false
	for _, kw := range longRunningKeywords {
		if strings.Contains(lower, kw) {
			longRunning = true
			break
		}
	}
	if !longRunning {
		return execStepDefaultTimeout
	}
	long := 1800
	if raw := os.Getenv("MARO_LONG_RUNNING_TIMEOUT"); raw != "" {
		if v, err := strconv.Atoi(raw); err == nil && v > 0 {
			long = v
		}
	}
	for _, h := range fullSuiteHints {
		if strings.Contains(lower, h) {
			if d := long * 2; d < 3600 {
				return time.Duration(d) * time.Second
			}
			return 3600 * time.Second
		}
	}
	return time.Duration(long) * time.Second
}

// maxInjectPerStep mirrors Python's [:3] cap on inject_steps.
const maxInjectPerStep = 3

// executeExecStep runs one tool-bearing worker step and returns its
// outcome plus any steps the worker injected into the plan.
func executeExecStep(ctx context.Context, a llm.Adapter, goal, step, hint string,
	stepNum, totalSteps int, prior []StepOutcome, projectDir string) (StepOutcome, []string) {

	var sb strings.Builder
	fmt.Fprintf(&sb, "Overall goal: %s\n\nCurrent step (%d/%d): %s\n",
		goal, stepNum, totalSteps, step)
	// WORKSPACE block, Python step_exec verbatim.
	fmt.Fprintf(&sb, "\nWORKSPACE: Save deliverables to %s/ — this directory"+
		" exists and persists across steps. Scratch/temp files belong in /tmp."+
		" Do not write anywhere else.\n", projectDir)
	sb.WriteString(renderPrior(prior))
	if hint != "" {
		// Blocked-retry hint plus Python's retry reminder verbatim — the
		// retry must act on WHY it was blocked. (Divergence, named: Python
		// routes this through the pending_context injection seam; Go has
		// no context seam yet, so the hint rides the step prompt.)
		fmt.Fprintf(&sb, "\n%s\n\nRETRY REMINDER — ORIGINAL GOAL: %s\n"+
			"Focus only on completing the step above. "+
			"Use data already in context. Target <500 tokens.\n", hint, goal)
	}

	tools := stepTools()
	opts := llm.Options{
		// Advisory on the subprocess backend (it enforces no output cap
		// for agentic calls — Python parity, where max_tokens is likewise
		// advisory there and the runaway brake is unported, named in
		// PORT.md); enforced by the Anthropic backend if this lane ever
		// runs there.
		MaxTokens:   4096,
		Temperature: 0.3,
		Timeout:     classifyStepTimeout(step),
		Purpose:     "step-execute",
		Tools:       tools,
		AgentTools:  true,
		Cwd:         projectDir,
	}
	// The per-step transcript is the durable record of what the inner
	// agent's tools actually did. A failed mkdir degrades to no
	// transcript (the run matters more than the artifact) but is carried
	// as a warning, never swallowed.
	var mkdirWarn string
	artifactDir := filepath.Join(projectDir, "artifacts")
	if err := os.MkdirAll(artifactDir, 0o755); err == nil {
		opts.TranscriptPath = filepath.Join(artifactDir,
			fmt.Sprintf("step-%d-transcript.jsonl", stepNum))
	} else {
		mkdirWarn = "artifact dir creation failed (no step transcript): " + err.Error()
	}

	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: execSystem},
		{Role: "user", Content: sb.String()},
	}, opts)
	if err != nil {
		out := StepOutcome{Step: step, Status: "blocked", Result: err.Error(),
			StuckReason: err.Error()}
		var re *llm.ResultError
		if errors.As(err, &re) {
			out.TokensIn, out.TokensOut = re.TokensIn, re.TokensOut
			out.Warnings = re.Warnings
		}
		if mkdirWarn != "" {
			out.Warnings = append(out.Warnings, mkdirWarn)
		}
		return out, nil
	}

	out := StepOutcome{Step: step, TokensIn: resp.TokensIn,
		TokensOut: resp.TokensOut, Warnings: resp.Warnings}
	if mkdirWarn != "" {
		out.Warnings = append(out.Warnings, mkdirWarn)
	}

	var injected []string
	switch tc := llm.ParseToolCall(resp.Content, tools); {
	case tc == nil:
		// No tool call — treat content as result (Python parity: some
		// models don't always call tools). Empty content is still a
		// blocked step, not a silent success.
		if strings.TrimSpace(resp.Content) == "" {
			out.Status, out.Result = "blocked", "worker produced no output"
			out.StuckReason = out.Result
		} else {
			out.Status, out.Result = "done", resp.Content
		}
	case tc.Name == "flag_stuck":
		reason := strings.TrimSpace(argString(tc.Arguments, "reason"))
		if reason == "" {
			reason = "(no reason given)"
		}
		msg := "flag_stuck: " + reason
		att := strings.TrimSpace(argString(tc.Arguments, "attempted"))
		if att != "" {
			msg += " (attempted: " + att + ")"
		}
		out.Status, out.Result = "blocked", msg
		out.StuckReason, out.Attempted = reason, att
	default: // complete_step
		result := argString(tc.Arguments, "result")
		if strings.TrimSpace(result) == "" {
			out.Status, out.Result = "blocked",
				"worker called complete_step with an empty result"
			out.StuckReason = out.Result
			break
		}
		out.Status, out.Result = "done", result
		out.Summary = budget.Clip(argString(tc.Arguments, "summary"), 300)
		// Confidence is stored raw, unvalidated against the schema enum —
		// Python parity (step_exec reads outcome.get("confidence","")
		// unchecked). Nothing branches on it yet; a consumer that does
		// must validate at ITS boundary.
		out.Confidence = argString(tc.Arguments, "confidence")
		if rawInject, present := tc.Arguments["inject_steps"]; present && rawInject != nil {
			raw, ok := rawInject.([]any)
			if !ok {
				// A present-but-wrong-type inject_steps means the worker
				// TRIED to add steps and nothing happened — that must not
				// be silent (Python shares this silent drop; diverging in
				// the loud direction).
				out.Warnings = append(out.Warnings, fmt.Sprintf(
					"inject_steps present but not an array (%T) — ignored", rawInject))
			}
			for _, s := range raw {
				if len(injected) >= maxInjectPerStep {
					break
				}
				if t := strings.TrimSpace(argToString(s)); t != "" {
					// Model-authored text headed for later prompt headers
					// rides a marked runaway bound, like every other
					// prompt input.
					injected = append(injected, budget.InjectedStep.Clip(t))
				}
			}
		}
		out.Injected = injected
	}
	return out, injected
}

// argString returns a string argument, tolerating absence and non-string
// junk (the model wrote it; refusing the whole step over a numeric
// summary would err the expensive direction).
func argString(args map[string]any, key string) string {
	return argToString(args[key])
}

func argToString(v any) string {
	switch t := v.(type) {
	case nil:
		return ""
	case string:
		return t
	default:
		return fmt.Sprintf("%v", t)
	}
}
