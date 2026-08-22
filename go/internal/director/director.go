// Package director ports the plan/delegate/review layer of
// src/director.py — the Director produces a SPEC + worker tickets from
// a directive, dispatches each ticket to a persona-framed worker,
// reviews every output (rejecting on parse failure — auto-accepting
// hides bad output), requests up to MaxReviewRounds revisions, and
// compiles the accepted results into one report.
//
// Ported lessons: bounded completed-context accumulator (worker results
// re-sent to every later worker grow quadratically in ticket count —
// budget.Accumulator, honest about eviction); report-echo check on the
// compile pass (MH #6 subagent edge: a DONE worker whose content made
// no lexical contact with the compiled report was DROPPED on the way to
// the parent — LLM-compile path only, the verbatim-concat fallbacks
// could not fail the check so it proves nothing there); delegation-gap
// candidates scoped to worker-authored reasons only (MH #13); review
// exhaustion proceeds best-effort rather than blocking (Python parity);
// judge-window clip at 4000 with a visible marker before the review
// call; spec-parse fallback is a single ticket for the whole directive
// (a director that can't plan still delegates).
//
// Deliberately unported, named: worker_slice memory injection +
// slice_echo (sqlite memory store unported); lat.md knowledge-graph
// injection into the spec prompt; the skip-if-simple direct route (the
// Go CLI's intent lane already owns that decision); check-in /
// escalation machinery and the evaluate_closure decision layer (they
// return with restart machinery, per PORT.md); the pre-plan challenger
// runs on the SAME adapter (Python builds a MODEL_CHEAP adapter — the
// model-role registry is unported); captains-log event names are
// emitted through record.Event into events.jsonl (Python's log_event
// sink for this workspace shape).
package director

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/scrub"
	"github.com/slycrel/maro-orchestration/go/internal/workers"
)

// MaxReviewRounds: the Director reviews each worker output up to this
// many times (Python MAX_REVIEW_ROUNDS).
const MaxReviewRounds = 2

// Ticket is one unit of work dispatched to a worker.
type Ticket struct {
	TicketID   string
	WorkerType string
	Task       string
	RevisionOf string // ticket_id this is a revision of, "" otherwise
}

// ReviewDecision is the Director's verdict on one worker output.
// ReviewDecisions is an append-shaped AUDIT TRAIL (revision rounds add
// entries) — TicketID is the correlation key; positional pairing with
// WorkerResults misattributes verdicts after any revision. Review is a
// revision TRIGGER and audit record, not a report-inclusion gate: a
// rejected result still reaches the report and downstream context
// (Python parity), it just does so on the record.
type ReviewDecision struct {
	TicketID        string
	Accepted        bool
	Reason          string
	RevisionRequest string
}

// Result ports DirectorResult.
type Result struct {
	DirectorID      string
	Directive       string
	PlanAcceptance  string // "explicit" | "inferred"
	Status          string // "done" | "stuck"
	Spec            string
	Tickets         []Ticket
	WorkerResults   []workers.Result
	ReviewDecisions []ReviewDecision
	Report          string
	TokensIn        int
	TokensOut       int
	Elapsed         time.Duration
	LogPath         string
	Warnings        []string
}

// Summary renders the operator-facing one-block summary (Python
// DirectorResult.summary).
func (r Result) Summary() string {
	done := 0
	for _, w := range r.WorkerResults {
		if w.Status == "done" {
			done++
		}
	}
	lines := []string{
		"director_id=" + r.DirectorID,
		fmt.Sprintf("directive=%q", r.Directive),
		"plan_acceptance=" + r.PlanAcceptance,
		"status=" + r.Status,
		fmt.Sprintf("tickets=%d workers_done=%d/%d", len(r.Tickets), done, len(r.WorkerResults)),
		fmt.Sprintf("tokens=%din+%dout elapsed=%s", r.TokensIn, r.TokensOut, r.Elapsed.Round(time.Millisecond)),
	}
	if r.LogPath != "" {
		lines = append(lines, "log="+r.LogPath)
	}
	return strings.Join(lines, "\n")
}

// explicitTriggers ports _EXPLICIT_TRIGGERS.
var explicitTriggers = []string{
	"post", "tweet", "publish", "send", "email", "delete", "remove",
	"deploy", "push to production", "merge to main", "drop", "wipe",
	"transfer", "pay", "purchase", "buy", "sell", "execute trade",
}

// RequiresExplicitAcceptance: does this directive need explicit user
// confirmation before the plan runs? (Recorded on the result; the
// approval SURFACE is unported with the escalation machinery — the
// field must not silently read as "approved".)
func RequiresExplicitAcceptance(directive string) bool {
	lower := strings.ToLower(directive)
	for _, t := range explicitTriggers {
		if strings.Contains(lower, t) {
			return true
		}
	}
	return false
}

const specSystem = `You are the Director for Maro, an autonomous orchestration system.
Your job: take a directive and produce a structured work plan.
You PLAN and REVIEW. You do NOT execute.

Worker types available:
- research: information gathering, analysis, synthesis
- build: code, scripts, configurations, structured artifacts
- ops: infrastructure, automation, diagnostics, system tasks
- general: everything else

Respond with a JSON object:
{
  "spec": "one paragraph describing the overall approach",
  "tickets": [
    {"worker_type": "research|build|ops|general", "task": "specific task for this worker"}
  ]
}

Rules:
- 1-4 tickets maximum. Each must be independently executable.
- Worker tickets must be concrete and specific (not vague meta-tasks).
- Order tickets so each one can use previous results as context.
- Pick the right worker type for each ticket.
- Take a position on scope and approach. If the directive is ambiguous, name the
  assumption you're making rather than hedging. State what would change your plan.`

const largeScopeSpecSystem = `You are the Director for Maro, an autonomous orchestration system.
Your job: take a large-scope review directive and produce a staged work plan.
You PLAN and REVIEW. You do NOT execute.

The goal is too large to complete in a single pass. Split it into 4-6 domain-area
worker tickets, each covering a bounded, independently-reviewable slice.
Order tickets so a final synthesis ticket can draw on all prior results.

Worker types available:
- research: information gathering, analysis, synthesis
- build: code, scripts, configurations, structured artifacts
- ops: infrastructure, automation, diagnostics, system tasks
- general: everything else

Respond with a JSON object:
{
  "spec": "one paragraph describing the staged approach",
  "tickets": [
    {"worker_type": "research|build|ops|general", "task": "specific bounded task for this domain area"}
  ]
}

Rules:
- 4-6 tickets. Each covers one domain area (e.g. docs/architecture, core execution, tests, integrations, security).
- Last ticket is always synthesis: "Compile findings from all prior passes into a structured report with severity ratings."
- Each ticket must be independently executable with a bounded file/scope set.
- Concrete file names or module areas are better than vague descriptions.
- Pick the right worker type for each ticket.
- Take a position on which domain areas matter most. Don't produce equal-weight coverage
  if the directive implies a specific concern — lead with the riskiest area.
- State what evidence would change the staged decomposition (e.g. if the repo is small,
  fewer passes; if security is the stated concern, security gets its own early ticket).`

const reviewSystem = `You are the Director reviewing a worker's output.
Your job: decide whether the output meets the requirements.
Accept if it's complete, relevant, and useful.
Reject ONLY if it's clearly incomplete, off-topic, or failed.

Respond with a JSON object:
{
  "accepted": true or false,
  "reason": "one sentence",
  "revision_request": "specific request if rejected, null if accepted"
}`

const compileSystem = `You are the Director compiling a final report for Maro's Handle.
Synthesize the worker outputs into a polished, structured report.
The report will be relayed to the user — make it direct and useful.
Lead with the key findings/deliverables. Include relevant details.
No hedging. No "I" statements. Just the work product.`

const challengerSystem = `You are a skeptical plan reviewer. Your job: challenge a proposed work plan
before it is locked in. Find gaps, risks, and wrong assumptions.

Given a directive and a proposed plan, identify 2-3 specific failure modes:
- Steps that are vague, unverifiable, or assume access not guaranteed
- Missing steps that are obviously needed to achieve the goal
- Steps that will produce noise, not signal (e.g. raw dumps instead of insights)

Respond with a JSON object:
{
  "critiques": ["specific issue 1", "specific issue 2"],
  "revised_spec": "one paragraph: the improved approach that addresses these critiques"
}

Be specific. If the plan is solid, say so briefly and keep revised_spec identical.`

// Scope keyword sets — planner.py's estimate_goal_scope heuristics
// (zero-LLM), ported here with the director because the large-scope
// spec gate is its only Go consumer so far.
var (
	narrowScopeKeywords = []string{
		"what is", "what are", "list the", "show me", "find the", "look up",
		"check if", "does the", "is there", "how many", "which file",
		"what value", "what's the", "print the", "get the", "read the config",
		"check the", "what does", "who is",
	}
	wideScopeKeywords = []string{
		"entire codebase", "whole codebase", "full codebase",
		"entire repo", "whole repo", "full repo",
		"adversarial review", "comprehensive review", "complete review",
		"codebase review", "code review of", "full audit", "complete audit",
		"review the codebase", "review the repo", "audit the codebase",
		"audit the repo", "review all", "review every", "all modules",
		"all files", "every module",
		"research and analyze", "research and build", "research and implement",
		"weeks of", "months of", "long-term", "multi-day", "multi-week",
	}
	deepScopeKeywords = []string{
		"build a complete", "build a full", "design and implement", "architect and build",
		"from scratch", "production-ready", "enterprise-grade",
		"self-improving", "autonomous system", "learn everything about",
	}
)

// EstimateGoalScope ports planner.estimate_goal_scope: classify a goal
// as narrow / medium / wide / deep with zero-LLM heuristics.
func EstimateGoalScope(goal string) string {
	low := strings.ToLower(goal)
	wordCount := len(strings.Fields(goal))
	contains := func(kws []string) bool {
		for _, k := range kws {
			if strings.Contains(low, k) {
				return true
			}
		}
		return false
	}
	if contains(deepScopeKeywords) {
		return "deep"
	}
	if contains(wideScopeKeywords) {
		return "wide"
	}
	if wordCount <= 8 && contains(narrowScopeKeywords) {
		return "narrow"
	}
	if wordCount <= 12 && !contains([]string{"research", "analyze", "implement", "build", "create", "design"}) {
		return "narrow"
	}
	return "medium"
}

func isLargeScopeReview(goal string) bool {
	s := EstimateGoalScope(goal)
	return s == "wide" || s == "deep"
}

// Run drives the full plan → delegate → review → compile pass.
func Run(ctx context.Context, adapter llm.Adapter, rec *record.Recorder, directive string, dry bool) (Result, error) {
	directive = strings.TrimSpace(directive)
	if directive == "" {
		return Result{}, fmt.Errorf("director: empty directive")
	}
	started := time.Now()
	res := Result{
		DirectorID: newID(),
		Directive:  directive,
		Status:     "done",
	}
	res.PlanAcceptance = "inferred"
	if RequiresExplicitAcceptance(directive) {
		res.PlanAcceptance = "explicit"
	}

	// Phase 1: SPEC + tickets.
	spec, tickets, in, out := produceSpec(ctx, adapter, directive, dry, &res)
	res.TokensIn += in
	res.TokensOut += out

	// Phase 1b: pre-plan challenger — one skeptic critique before
	// locking. Non-fatal by contract.
	if !dry && adapter != nil {
		spec2, cin, cout := challengeSpec(ctx, adapter, directive, spec, tickets, &res)
		res.TokensIn += cin
		res.TokensOut += cout
		spec = spec2
	}
	res.Spec = spec
	res.Tickets = tickets

	// Phase 2: dispatch + review + revise. Completed results feed later
	// workers through the bounded accumulator.
	completed := budget.NewAccumulator()
	for _, ticket := range tickets {
		extra := strings.TrimSpace(completed.Render())

		wres := workers.Dispatch(ctx, adapter, ticket.WorkerType, ticket.Task, extra, dry)
		res.TokensIn += wres.TokensIn
		res.TokensOut += wres.TokensOut

		review, rin, rout := reviewWorkerOutput(ctx, adapter, directive, ticket, wres, dry)
		res.TokensIn += rin
		res.TokensOut += rout
		res.ReviewDecisions = append(res.ReviewDecisions, review)

		if !review.Accepted && review.RevisionRequest == "" && !dry {
			// A rejection with no revision request is otherwise a silent
			// no-op — the result still ships (Python parity) but must do
			// so ON THE RECORD (adversarial director r1, QA HIGH: same
			// report, same DONE, zero durable trace).
			res.Warnings = append(res.Warnings, fmt.Sprintf(
				"ticket %s review rejected with no revision request — result kept best-effort",
				ticket.TicketID))
		}
		if !review.Accepted && review.RevisionRequest != "" && !dry {
			accepted := false
			for round := 0; round < MaxReviewRounds-1; round++ {
				revised := Ticket{
					TicketID:   newID(),
					WorkerType: ticket.WorkerType,
					Task:       ticket.Task + "\n\nRevision request: " + review.RevisionRequest,
					RevisionOf: ticket.TicketID,
				}
				// The revised ticket is PART OF THE RECORD — without it
				// the revision decision's TicketID resolves to nothing
				// in the persisted log, an orphaned correlation key on
				// exactly the multi-round case the key exists for
				// (adversarial director r2, both lenses' HIGH).
				res.Tickets = append(res.Tickets, revised)
				wres = workers.Dispatch(ctx, adapter, revised.WorkerType, revised.Task, extra, dry)
				res.TokensIn += wres.TokensIn
				res.TokensOut += wres.TokensOut
				review, rin, rout = reviewWorkerOutput(ctx, adapter, directive, revised, wres, dry)
				res.TokensIn += rin
				res.TokensOut += rout
				res.ReviewDecisions = append(res.ReviewDecisions, review)
				if review.Accepted {
					accepted = true
					break
				}
				if review.RevisionRequest == "" {
					// No guidance left — a further round would be vacuous,
					// so break regardless. The TERMINAL round is already
					// covered by the exhaustion warning below; warn here
					// only when the break cuts remaining rounds short, so
					// one incident never writes two warnings (adversarial
					// director r3, Skeptic HIGH: the r2 guard fired on the
					// sole iteration and doubled up — its "unreachable"
					// comment was false).
					if round+1 < MaxReviewRounds-1 {
						res.Warnings = append(res.Warnings, fmt.Sprintf(
							"ticket %s revision round %d rejected with no revision request — stopping revisions",
							revised.TicketID, round+1))
					}
					break
				}
			}
			if !accepted {
				// Exhausted rounds: proceed best-effort with the last
				// revision rather than blocking (Python parity).
				res.Warnings = append(res.Warnings, fmt.Sprintf(
					"review exhausted %d rounds for ticket %s — best-effort result kept",
					MaxReviewRounds, ticket.TicketID))
			}
		}

		res.WorkerResults = append(res.WorkerResults, wres)
		if wres.Status == "done" && wres.Result != "" {
			completed.Add(fmt.Sprintf("[%s] %s:\n%s", ticket.WorkerType, ticket.Task, wres.Result))
		}
	}

	// Phase 3: compile the report, then stamp report-echo on the LLM
	// path (compileReport owns the stamping asymmetry).
	report, pin, pout := compileReport(ctx, adapter, directive, spec, res.WorkerResults, dry)
	res.TokensIn += pin
	res.TokensOut += pout
	res.Report = report

	// MH candidate events — advisory only, never control flow. Event
	// write failures are warnings, not errors: candidates are telemetry.
	if rec != nil {
		for i := range res.WorkerResults {
			w := &res.WorkerResults[i]
			if w.Status == "done" && w.ReportEchoed != nil && !*w.ReportEchoed {
				if err := rec.Event("WORKER_REPORT_OMISSION", "director_compile",
					fmt.Sprintf("%s worker output (%d chars) shows no lexical contact with the compiled report", w.WorkerType, len(w.Result)),
					map[string]any{
						"director_id": res.DirectorID,
						"worker_type": w.WorkerType,
						"mh_edge":     "subagent",
						"mh_class":    "communication_failure_candidate",
					}, ""); err != nil {
					res.Warnings = append(res.Warnings, "report-omission event write failed: "+err.Error())
				}
			}
			// Worker-authored reasons ONLY (2026-08-11): adapter failures
			// pattern-match the provision keywords and would contaminate
			// the candidate corpus.
			if isDelegationGap(*w) {
				if err := rec.Event("WORKER_DELEGATION_GAP", "director_dispatch",
					fmt.Sprintf("%s worker blocked on a provision-shaped reason — the ticket may have under-specified the work", w.WorkerType),
					map[string]any{
						"director_id": res.DirectorID,
						"worker_type": w.WorkerType,
						// Scrubbed BEFORE the clip: captains_log.jsonl is a
						// durable sink read by dev-recall/viz/learning and
						// record.Event never rescrubs (adversarial director
						// r1, three lenses: the sibling writeLog scrubbed
						// this exact string, this sink didn't). Python's
						// log_event doesn't scrub either — Go-stricter,
						// backport candidate.
						"ticket_preview": budget.Clip(scrub.Secrets(w.Ticket), 120),
						"reason_preview": budget.Clip(scrub.Secrets(w.StuckReason), 200),
						"mh_edge":        "subagent",
						"mh_class":       "delegation_failure_candidate",
					}, ""); err != nil {
					res.Warnings = append(res.Warnings, "delegation-gap event write failed: "+err.Error())
				}
			}
		}
	}

	// Overall status: every worker done, or stuck.
	for _, w := range res.WorkerResults {
		if w.Status != "done" {
			res.Status = "stuck"
			break
		}
	}
	res.Elapsed = time.Since(started)

	// Durable log — failure is a warning, never fatal (Python returns
	// None and moves on).
	if path, err := writeLog(rec, res); err != nil {
		res.Warnings = append(res.Warnings, "director log write failed: "+err.Error())
	} else {
		res.LogPath = path
	}
	return res, nil
}

// produceSpec ports _produce_spec: the dry path yields one inferred
// ticket; the LLM path parses {spec, tickets}, coercing unknown worker
// types through InferType; every failure falls back to a single ticket
// for the whole directive.
func produceSpec(ctx context.Context, adapter llm.Adapter, directive string, dry bool, res *Result) (string, []Ticket, int, int) {
	if dry || adapter == nil {
		return "[dry-run spec] Plan for: " + clipRunes(directive, 80),
			[]Ticket{{TicketID: newID(), WorkerType: workers.InferType(directive), Task: "[dry-run] " + clipRunes(directive, 60)}},
			0, 0
	}

	system := specSystem
	maxTickets := 4
	if isLargeScopeReview(directive) {
		system = largeScopeSpecSystem
		maxTickets = 6
	}

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: system},
		{Role: "user", Content: "Directive: " + directive},
	}, llm.Options{MaxTokens: 1024, Temperature: 0.2, Purpose: "director spec"})
	in, out := usage(resp, err)
	if err == nil && resp != nil {
		if data, jerr := jsonx.Object(resp.Content); jerr == nil {
			spec, _ := data["spec"].(string)
			var tickets []Ticket
			malformed := 0
			if raw, ok := data["tickets"].([]any); ok {
				for _, item := range raw {
					if len(tickets) >= maxTickets {
						break
					}
					t, tok := item.(map[string]any)
					if !tok {
						// Non-object entries (strings, numbers, null) are
						// the most LLM-plausible schema confusion and were
						// evading the malformed counter entirely
						// (adversarial director r3, both lenses).
						malformed++
						continue
					}
					// A forged/malformed shape must not silently become an
					// EMPTY ticket dispatched to a real worker (adversarial
					// director r1, three lenses): skip it with a warning;
					// if every entry is malformed the single-ticket
					// fallback below fires. Go-stricter than Python
					// (t.get("task","") keeps non-strings).
					task, taskOK := t["task"].(string)
					if !taskOK || strings.TrimSpace(task) == "" {
						malformed++
						continue
					}
					wtype, _ := t["worker_type"].(string)
					if !typeValid(wtype) {
						wtype = workers.InferType(task)
					}
					tickets = append(tickets, Ticket{TicketID: newID(), WorkerType: wtype, Task: task})
				}
			} else if v, present := data["tickets"]; present {
				// A present-but-non-list tickets field means the model's
				// whole plan is being discarded — that must reach the
				// record, not silently fall through to the single-ticket
				// fallback (adversarial director r3, QA HIGH).
				res.Warnings = append(res.Warnings, fmt.Sprintf(
					"spec tickets field was %T, not a list — single-ticket fallback", v))
			}
			if malformed > 0 {
				// ONE summary warning, not one per entry — a hostile
				// spec reply with thousands of garbage entries must not
				// inflate the warning slice and the durable log
				// (adversarial director r2, Skeptic: the good path was
				// capped at maxTickets, the reject path wasn't).
				res.Warnings = append(res.Warnings, fmt.Sprintf(
					"%d malformed spec ticket entries (non-object, or missing/non-string task) skipped", malformed))
			}
			if len(tickets) == 0 {
				tickets = []Ticket{{TicketID: newID(), WorkerType: workers.InferType(directive), Task: directive}}
			}
			return spec, tickets, in, out
		}
		res.Warnings = append(res.Warnings, "spec parse failed — single-ticket fallback")
	} else {
		res.Warnings = append(res.Warnings, "spec LLM call failed — single-ticket fallback")
	}
	return "Single-worker fallback for: " + clipRunes(directive, 80),
		[]Ticket{{TicketID: newID(), WorkerType: workers.InferType(directive), Task: directive}},
		in, out
}

// challengeSpec ports _challenge_spec: one skeptic critique pass; any
// failure returns the original spec (non-fatal quality gate). Runs on
// the same adapter — Python's MODEL_CHEAP build is unported (package
// doc).
func challengeSpec(ctx context.Context, adapter llm.Adapter, directive, spec string, tickets []Ticket, res *Result) (string, int, int) {
	var lines []string
	for _, t := range tickets {
		lines = append(lines, fmt.Sprintf("  [%s] %s", t.WorkerType, t.Task))
	}
	userMsg := fmt.Sprintf(
		"Directive: %s\n\nProposed spec: %s\n\nProposed tickets:\n%s\n\nIdentify 2-3 failure modes, then provide a revised spec.",
		directive, spec, strings.Join(lines, "\n"))

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: challengerSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{MaxTokens: 512, Temperature: 0.3, Purpose: "spec challenge"})
	in, out := usage(resp, err)
	if err != nil || resp == nil {
		// Non-fatal by contract, but NAMED — a swallowed challenger
		// failure was strictly less visible than Python's debug log
		// (adversarial director r1, QA MED).
		res.Warnings = append(res.Warnings, "pre-plan challenger call failed — spec unchallenged")
		return spec, in, out
	}
	data, jerr := jsonx.Object(resp.Content)
	if jerr != nil {
		res.Warnings = append(res.Warnings, "pre-plan challenger reply unparseable — spec unchallenged")
		return spec, in, out
	}
	if revised, _ := data["revised_spec"].(string); strings.TrimSpace(revised) != "" {
		return revised, in, out
	}
	return spec, in, out
}

// reviewWorkerOutput ports _review_worker_output. Parse failure REJECTS
// — auto-accepting hides bad output (Python's explicit default).
func reviewWorkerOutput(ctx context.Context, adapter llm.Adapter, directive string, ticket Ticket, wres workers.Result, dry bool) (ReviewDecision, int, int) {
	if dry || adapter == nil {
		return ReviewDecision{TicketID: ticket.TicketID, Accepted: true, Reason: "[dry-run] auto-accepted"}, 0, 0
	}
	// Accept/reject is a verdict — judge-window rules apply: 4000 keeps
	// ~99% of worker outputs whole and Clip marks the remainder so the
	// reviewer knows its view is partial.
	userMsg := fmt.Sprintf("Directive: %s\n\nTicket (%s): %s\n\nWorker output:\n%s\n\nWorker status: %s",
		directive, ticket.WorkerType, ticket.Task, budget.WorkerJudgeWindow.Clip(wres.Result), wres.Status)
	if wres.StuckReason != "" {
		userMsg += "\nStuck reason: " + wres.StuckReason
	}

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: reviewSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{MaxTokens: 256, Temperature: 0.1, Purpose: "worker review"})
	in, out := usage(resp, err)
	if err == nil && resp != nil {
		if data, jerr := jsonx.Object(resp.Content); jerr == nil {
			// Trimmed at parse so every downstream branch compares real
			// content — a whitespace-only revision_request otherwise
			// dodges the no-revision warning AND buys a vacuous retry
			// (adversarial director r2, QA).
			reason := strings.TrimSpace(stringField(data, "reason"))
			revision := strings.TrimSpace(stringField(data, "revision_request"))
			// The safety direction applies to the FIELD, not just the
			// envelope: a verdict whose "accepted" is missing, null, or
			// mistyped rejects. Deliberately STRICTER than Python
			// (bool(data.get("accepted", True)) accepts an absent key
			// and coerces "false" truthy) — adversarial director r1,
			// three lenses independently. The model's own diagnostics
			// still travel (r2, Skeptic: the gate was discarding exactly
			// the revision_request that made the rejection recoverable),
			// and the malformed value's TYPE is named so distinct
			// failure shapes stay distinguishable in the audit trail.
			accepted, isBool := data["accepted"].(bool)
			if !isBool {
				why := fmt.Sprintf(
					"review verdict missing boolean accepted (was %T) — rejecting for safety", data["accepted"])
				if reason != "" {
					why += "; model reason: " + budget.Clip(reason, 300)
				}
				return ReviewDecision{TicketID: ticket.TicketID, Accepted: false,
					Reason: why, RevisionRequest: revision}, in, out
			}
			return ReviewDecision{TicketID: ticket.TicketID, Accepted: accepted,
				Reason: reason, RevisionRequest: revision}, in, out
		}
	}
	return ReviewDecision{TicketID: ticket.TicketID, Accepted: false, Reason: "review parse failed, rejecting for safety"}, in, out
}

// compileReport ports _compile_report. Side effect on the LLM path
// ONLY: stamps ReportEchoed on every worker result against the produced
// report — the dry-run and exception-fallback paths concatenate outputs
// verbatim, where an echo check could not fail and so proves nothing
// (the stamp stays nil there).
func compileReport(ctx context.Context, adapter llm.Adapter, directive, spec string, wresults []workers.Result, dry bool) (string, int, int) {
	concat := func() string {
		var parts []string
		for _, r := range wresults {
			parts = append(parts, fmt.Sprintf("**%s (%s)**\n%s", titleCase(r.WorkerType), r.Status, r.Result))
		}
		if len(parts) == 0 {
			return "[dry-run: no output]"
		}
		return strings.Join(parts, "\n\n---\n\n")
	}
	if dry || adapter == nil {
		return concat(), 0, 0
	}

	// One window per worker, used for BOTH the compile prompt and the
	// echo check below — echoing the full result against a report
	// compiled from a clipped view produced false DROPPED verdicts for
	// long outputs whose distinctive terms lived past the cut
	// (adversarial director r1, Skeptic MED; Python still compares
	// against the unclipped text — named divergence, honesty-direction).
	windows := make([]string, len(wresults))
	var b strings.Builder
	for i, r := range wresults {
		windows[i] = budget.WorkerJudgeWindow.Clip(r.Result)
		fmt.Fprintf(&b, "\n\n### Worker %d (%s)\nStatus: %s\n%s",
			i+1, r.WorkerType, r.Status, windows[i])
	}
	userMsg := fmt.Sprintf("Directive: %s\n\nSpec: %s\n\nWorker outputs:%s\n\nCompile a final report.",
		directive, spec, b.String())

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: compileSystem},
		{Role: "user", Content: userMsg},
	}, llm.Options{MaxTokens: 4096, Temperature: 0.3, Purpose: "report compile"})
	in, out := usage(resp, err)
	if err != nil || resp == nil {
		return concat(), in, out
	}
	report := strings.TrimSpace(resp.Content)
	for i := range wresults {
		wresults[i].ReportEchoed = reportEcho(windows[i], report)
	}
	return report, in, out
}

// Report-echo bar (MH #6): a worker result with at least echoMinTerms
// distinctive terms is judgeable; contact requires min(echoHits,
// len(terms)) of them in the compiled report. Deliberately LOW — false
// means fewer than 3 distinctive terms from the worker's whole output
// survived, which is a dropped worker, not a paraphrase.
const (
	echoMinTerms   = 5
	echoHits       = 3
	echoMinTermLen = 5
)

var echoTermRe = regexp.MustCompile(fmt.Sprintf(`[a-z0-9_\-./]{%d,}`, echoMinTermLen))

// echoStopwords — memory_bridge._ECHO_STOPWORDS verbatim: frequent
// boilerplate whose match proves nothing. Kept byte-identical to
// Python's table so a future memory-slice port doesn't reconcile two
// vocabularies — today this file is the only Go consumer.
var echoStopwords = map[string]bool{
	"before": true, "after": true, "should": true, "always": true, "never": true,
	"when": true, "instead": true, "prefer": true, "avoid": true, "check": true,
	"verify": true, "ensure": true, "using": true, "with": true, "without": true,
	"because": true, "lesson": true, "learned": true, "failed": true, "failure": true,
	"success": true, "successful": true, "worked": true, "working": true,
	"error": true, "errors": true, "file": true, "files": true, "step": true,
	"steps": true, "task": true, "tasks": true, "goal": true, "run": true,
	"runs": true, "output": true, "result": true, "results": true, "project": true,
	"which": true, "there": true, "their": true, "these": true, "those": true,
	"first": true, "second": true, "rather": true, "about": true,
}

// clipMarkerRe matches both marker formats in the budget package:
// budget.Clip's " … [truncated: first N of M characters]" and the
// Accumulator's "… [entry truncated: first N of M characters]".
var clipMarkerRe = regexp.MustCompile(`(?:… )?\[(?:entry )?truncated: first \d+ of \d+ characters\]`)

func distinctiveTerms(text string) map[string]bool {
	terms := map[string]bool{}
	for _, w := range echoTermRe.FindAllString(strings.ToLower(text), -1) {
		if !echoStopwords[w] {
			terms[w] = true
		}
	}
	return terms
}

// reportEcho ports _report_echo: did the compiled report make lexical
// contact with this worker's output? nil = nothing to judge (short or
// empty result, empty report) — consumers must keep nil distinct from
// false.
func reportEcho(resultText, reportText string) *bool {
	if strings.TrimSpace(resultText) == "" || strings.TrimSpace(reportText) == "" {
		return nil
	}
	// Clip markers are framework text, not worker content — strip the
	// whole marker (both budget.Clip's and the Accumulator's wording)
	// before term extraction, so neither the marker words nor 5+ digit
	// offsets count as worker-distinctive terms, while GENUINE content
	// that merely discusses truncation keeps its vocabulary
	// (adversarial director r2 Skeptic; r3 both lenses: the r2
	// word-deletes were unconditional, missed numeric offsets, and
	// missed the Accumulator's "entry truncated" variant).
	terms := distinctiveTerms(clipMarkerRe.ReplaceAllString(resultText, ""))
	if len(terms) < echoMinTerms {
		return nil
	}
	text := strings.ToLower(reportText)
	need := echoHits
	if len(terms) < need {
		need = len(terms)
	}
	hits := 0
	for t := range terms {
		if strings.Contains(text, t) {
			hits++
			if hits >= need {
				v := true
				return &v
			}
		}
	}
	v := false
	return &v
}

// writeLog ports _write_director_log: one JSON artifact under
// <workspace>/output/artifacts/director/, atomic temp+rename, prose
// scrubbed at this single write boundary (worker output and LLM prose
// land in a durable file here and nothing downstream rescrubs it).
func writeLog(rec *record.Recorder, res Result) (string, error) {
	if rec == nil {
		return "", nil
	}
	dir := filepath.Join(rec.WorkspaceDir, "output", "artifacts", "director")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return "", err
	}
	tickets := make([]map[string]any, 0, len(res.Tickets))
	for _, t := range res.Tickets {
		row := map[string]any{
			"ticket_id": t.TicketID, "worker_type": t.WorkerType,
			"task": scrub.Secrets(t.Task),
		}
		// The revision chain resolves in the log itself (r2).
		if t.RevisionOf != "" {
			row["revision_of"] = t.RevisionOf
		}
		tickets = append(tickets, row)
	}
	wrows := make([]map[string]any, 0, len(res.WorkerResults))
	for _, r := range res.WorkerResults {
		row := map[string]any{
			"worker_type":    r.WorkerType,
			"status":         r.Status,
			"result_length":  len(r.Result),
			"delegation_gap": isDelegationGap(r),
			"tokens_in":      r.TokensIn,
			"tokens_out":     r.TokensOut,
		}
		// nil stays a JSON null — "not judged" is a third state, not false.
		if r.ReportEchoed != nil {
			row["report_echoed"] = *r.ReportEchoed
		} else {
			row["report_echoed"] = nil
		}
		wrows = append(wrows, row)
	}
	// Review decisions and warnings are part of the durable record —
	// without them a run whose review loop was exhausted or whose
	// events failed to write reads as an unqualified success once
	// stderr is gone (adversarial director r1, QA HIGHs ×2). Python
	// persists neither — Go-stricter, backport candidate.
	drows := make([]map[string]any, 0, len(res.ReviewDecisions))
	for _, d := range res.ReviewDecisions {
		drows = append(drows, map[string]any{
			"ticket_id":        d.TicketID,
			"accepted":         d.Accepted,
			"reason":           scrub.Secrets(d.Reason),
			"revision_request": scrub.Secrets(d.RevisionRequest),
		})
	}
	warns := make([]string, 0, len(res.Warnings))
	for _, w := range res.Warnings {
		warns = append(warns, scrub.Secrets(w))
	}
	payload := map[string]any{
		"director_id":      res.DirectorID,
		"directive":        scrub.Secrets(res.Directive),
		"spec":             scrub.Secrets(res.Spec),
		"status":           res.Status,
		"elapsed_ms":       res.Elapsed.Milliseconds(),
		"tickets":          tickets,
		"worker_results":   wrows,
		"review_decisions": drows,
		"warnings":         warns,
	}
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return "", err
	}
	path := filepath.Join(dir, fmt.Sprintf("director-%s-log.json", res.DirectorID))
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(data, '\n'), 0o644); err != nil {
		return "", err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return "", err
	}
	return path, nil
}

func usage(resp *llm.Response, err error) (int, int) {
	if resp != nil {
		return resp.TokensIn, resp.TokensOut
	}
	var re *llm.ResultError
	if errors.As(err, &re) {
		return re.TokensIn, re.TokensOut
	}
	return 0, 0
}

// isDelegationGap is the ONE owner of the gap-candidate scoping rule —
// the event emission and the log row must never disagree about the same
// worker (adversarial director r1, QA: the rule was duplicated
// verbatim at both sites).
func isDelegationGap(w workers.Result) bool {
	return w.Status == "blocked" && w.BlockedOrigin == "worker" &&
		workers.DelegationGap(w.StuckReason)
}

// stringField reads a string JSON field, "" for absent or non-string.
func stringField(data map[string]any, key string) string {
	v, _ := data[key].(string)
	return v
}

func typeValid(t string) bool {
	for _, w := range workers.Types {
		if w == t {
			return true
		}
	}
	return false
}

func titleCase(s string) string {
	if s == "" {
		return s
	}
	return strings.ToUpper(s[:1]) + s[1:]
}

func clipRunes(s string, n int) string {
	if r := []rune(s); len(r) > n {
		return string(r[:n])
	}
	return s
}

func newID() string {
	b := make([]byte, 4)
	if _, err := rand.Read(b); err != nil {
		return fmt.Sprintf("%08x", time.Now().UnixNano()&0xffffffff)
	}
	return hex.EncodeToString(b)
}
