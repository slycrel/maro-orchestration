// Package workers ports src/workers.py — the director's dispatch
// targets. A worker is one persona-framed LLM call speaking the
// simulated tool protocol (deliver_result / flag_blocked); the director
// reviews what comes back.
//
// Ported lessons: blocked_origin taxonomy (worker/adapter/empty —
// adapter failures must never contaminate the worker-authored
// delegation-gap corpus, adversarial review 2026-08-11); ResultError
// usage salvage on failed calls (this port's own r1 lesson, three
// prior sites); tool-call fallback ladder (deliver_result → flag_blocked
// → bare content >20 chars → blocked "no useful output") preserved
// order-exact from Python dispatch_worker.
//
// Deliberately unported, named: PersonaRegistry and the personas/
// directory resolution (returns with the persona subsystem — inline
// personas are the Python fallback tier and the only tier here);
// the container-exec backend contract (container lane unported);
// memory_slice injection fields (sqlite memory store unported).
package workers

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

const (
	Research = "research"
	Build    = "build"
	Ops      = "ops"
	General  = "general"
)

// Types lists the valid worker types in Python's declaration order —
// General last, the fallback slot (_produce_spec indexes WORKER_TYPES[-1]).
var Types = []string{Research, Build, Ops, General}

// Result ports WorkerResult. memory_slice_* fields are unported with
// their substrate; ReportEchoed is stamped by the director's compile
// pass (nil = not judged — dry-run/fallback reports are verbatim
// concatenations, an echo check there could not fail).
type Result struct {
	WorkerType  string
	Ticket      string
	Status      string // "done" | "blocked"
	Result      string
	StuckReason string
	TokensIn    int
	TokensOut   int
	// ReportEchoed: did the compiled report make lexical contact with
	// this worker's output? INVERSE asymmetry vs a memory echo —
	// compilers paraphrase, so true is weak evidence of coverage, but
	// false means the worker's content was DROPPED on the way to the
	// parent. nil = not judged.
	ReportEchoed *bool
	// BlockedOrigin: who authored a blocked result's StuckReason.
	// "worker" = the model's own flag_blocked call; "adapter" = the LLM
	// call itself failed; "empty" = no useful output. "" for done.
	BlockedOrigin string
}

// Inline personas — Python's _INLINE_PERSONAS verbatim (the fallback
// tier; the only tier in this port, see package doc).
const personaResearch = `You are a Research Worker for Maro, an autonomous agent system.
Your job: answer research questions with source-grounded, high-signal output.
Core traits:
- Context-first: understand the full task before researching.
- Multi-angle: pursue multiple hypotheses, not one narrative.
- Source-grounded: tie claims to sources; mark uncertainty explicitly.
- Synthesis over paste: compress and merge, don't just copy.
Deliver: structured findings with cited evidence and a clear "so what" conclusion.
You are a WORKER — do not plan or review. Execute the ticket and produce output.`

const personaBuild = `You are a Build Worker for Maro, an autonomous agent system.
Your job: implement code, scripts, configs, or structured artifacts.
Core traits:
- Implementation-first: produce working output, not plans.
- Minimal and correct: write only what's needed, avoid over-engineering.
- Documented: include inline comments for non-obvious logic.
- Testable: structure code so it can be verified.
You are a WORKER — do not plan or review. Execute the ticket and produce output.`

const personaOps = `You are an Ops Worker for Maro, an autonomous agent system.
Your job: handle automation, diagnostics, infrastructure, and system tasks.
Core traits:
- Safety-first: verify before executing; flag irreversible actions.
- Diagnostic: explain what you observe and why actions are safe.
- Idempotent: prefer operations that can be safely retried.
- Documented: log what was changed and why.
You are a WORKER — do not plan or review. Execute the ticket and produce output.`

const personaGeneral = `You are a General Worker for Maro, an autonomous agent system.
Your job: complete tasks that don't fit the specialist roles.
Core traits:
- Direct: produce the requested output without hedging.
- Complete: finish the whole task, not just part of it.
- Concise: say what needs to be said, nothing more.
You are a WORKER — do not plan or review. Execute the ticket and produce output.`

var personas = map[string]string{
	Research: personaResearch,
	Build:    personaBuild,
	Ops:      personaOps,
	General:  personaGeneral,
}

// tools is Python's _WORKER_TOOLS: the simulated tool protocol the
// worker replies through.
var tools = []llm.Tool{
	{
		Name:        "deliver_result",
		Description: "Deliver the completed work product for this ticket.",
		Parameters: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"result": map[string]any{
					"type":        "string",
					"description": "The complete work product, findings, or artifact.",
				},
				"summary": map[string]any{
					"type":        "string",
					"description": "One-sentence summary of what was produced.",
				},
			},
			"required": []any{"result", "summary"},
		},
	},
	{
		Name:        "flag_blocked",
		Description: "Signal that this ticket cannot be completed.",
		Parameters: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"reason": map[string]any{
					"type":        "string",
					"description": "Why this ticket cannot be completed.",
				},
				"partial": map[string]any{
					"type":        "string",
					"description": "Any partial work completed before getting blocked.",
				},
			},
			"required": []any{"reason"},
		},
	},
}

// Dispatch ports dispatch_worker: one persona-framed call, parsed
// through the tool ladder. dry short-circuits to the deterministic stub
// at the same boundary Python does.
func Dispatch(ctx context.Context, adapter llm.Adapter, workerType, ticket, extra string, dry bool) Result {
	if !validType(workerType) {
		workerType = General
	}
	if dry || adapter == nil {
		return dryWorker(workerType, ticket)
	}

	contextBlock := ""
	if extra != "" {
		contextBlock = "\n\nContext:\n" + extra
	}
	userMsg := fmt.Sprintf("Ticket: %s%s\n\nComplete this ticket. Call deliver_result when done.",
		ticket, contextBlock)

	resp, err := adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: personas[workerType]},
		{Role: "user", Content: userMsg},
	}, llm.Options{
		MaxTokens:   4096,
		Temperature: 0.3,
		Tools:       tools,
		Purpose:     "worker-ticket",
	})
	if err != nil {
		res := Result{
			WorkerType:    workerType,
			Ticket:        ticket,
			Status:        "blocked",
			StuckReason:   "LLM call failed: " + err.Error(),
			BlockedOrigin: "adapter",
		}
		// Salvage real spend from a failed call (llm.ResultError) — a
		// blocked worker still cost tokens and the director's totals
		// must carry them (this port's r1 lesson, 4th site).
		var re *llm.ResultError
		if errors.As(err, &re) {
			res.TokensIn = re.TokensIn
			res.TokensOut = re.TokensOut
		}
		return res
	}
	if resp == nil {
		return Result{WorkerType: workerType, Ticket: ticket, Status: "blocked",
			StuckReason: "adapter returned nil response without error", BlockedOrigin: "adapter"}
	}

	if tc := llm.ParseToolCall(resp.Content, tools); tc != nil {
		switch tc.Name {
		case "deliver_result":
			out := stringArg(tc.Arguments, "result", resp.Content)
			return Result{WorkerType: workerType, Ticket: ticket, Status: "done",
				Result: out, TokensIn: resp.TokensIn, TokensOut: resp.TokensOut}
		case "flag_blocked":
			return Result{WorkerType: workerType, Ticket: ticket, Status: "blocked",
				Result:      stringArg(tc.Arguments, "partial", ""),
				StuckReason: stringArg(tc.Arguments, "reason", "unknown"),
				TokensIn:    resp.TokensIn, TokensOut: resp.TokensOut,
				BlockedOrigin: "worker"}
		}
	}

	// Fallback: treat bare content as the result — Python's >20-char
	// bar, counted in RUNES (Python len() semantics): byte counting
	// passed 8 CJK characters as "useful output" where Python refuses
	// (adversarial director r1, QA — lenient-direction divergence on a
	// refusal gate).
	if content := resp.Content; len([]rune(content)) > 20 {
		return Result{WorkerType: workerType, Ticket: ticket, Status: "done",
			Result: content, TokensIn: resp.TokensIn, TokensOut: resp.TokensOut}
	}
	return Result{WorkerType: workerType, Ticket: ticket, Status: "blocked",
		Result:      resp.Content,
		StuckReason: "Worker produced no useful output",
		TokensIn:    resp.TokensIn, TokensOut: resp.TokensOut,
		BlockedOrigin: "empty"}
}

// stringArg extracts a string tool argument, JSON-encoding non-string
// shapes rather than dropping them (Python json.dumps parity).
//
// The comment already said json.dumps and the code said encoding/json,
// which is the whole r8 finding in one line. This value becomes PROMPT
// TEXT — a nested tool argument reached the model sorted, unspaced and
// with `>` HTML-escaped on this side, so the two runtimes asked the
// worker a different question (adversarial mission-r8).
func stringArg(args map[string]any, key, fallback string) string {
	v, ok := args[key]
	if !ok || v == nil {
		return fallback
	}
	if s, ok := v.(string); ok {
		return s
	}
	b, err := pyval.DumpsCompactPy(pyval.FromPlain(v))
	if err != nil {
		return fallback
	}
	return b
}

func dryWorker(workerType, ticket string) Result {
	if r := []rune(ticket); len(r) > 80 {
		ticket = string(r[:80])
	}
	return Result{
		WorkerType: workerType,
		Ticket:     ticket,
		Status:     "done",
		Result:     fmt.Sprintf("[dry-run:%s] Completed: %s", workerType, ticket),
		TokensIn:   60,
		TokensOut:  40,
	}
}

func validType(t string) bool {
	for _, w := range Types {
		if w == t {
			return true
		}
	}
	return false
}

// Keyword sets for InferType — Python's frozensets verbatim. Matching
// is SUBSTRING on the lowered ticket (Python `k in lower`), so
// "review" also fires inside "reviewing".
var (
	researchKeywords = []string{"research", "analyze", "investigate", "study", "find", "search", "look up", "review"}
	buildKeywords    = []string{"build", "implement", "write", "create", "code", "develop", "generate code", "script"}
	opsKeywords      = []string{"deploy", "monitor", "run", "execute", "configure", "set up", "install", "check status", "debug"}
)

// InferType ports infer_worker_type: keyword-score the ticket, ties
// break research > build > ops (Python's if-chain order), zero
// matches → general.
func InferType(ticket string) string {
	lower := strings.ToLower(ticket)
	score := func(kws []string) int {
		n := 0
		for _, k := range kws {
			if strings.Contains(lower, k) {
				n++
			}
		}
		return n
	}
	r, b, o := score(researchKeywords), score(buildKeywords), score(opsKeywords)
	best := max(r, max(b, o))
	if best == 0 {
		return General
	}
	if r == best {
		return Research
	}
	if b == best {
		return Build
	}
	return Ops
}

// DelegationGap ports attribution.delegation_gap: does a blocked
// worker's reason describe something the TICKET should have provided
// (input, referent, access, scope) rather than an execution failure?
// Pure keyword floor; candidate-grade, advisory only. Callers must
// scope it to worker-authored reasons (BlockedOrigin == "worker") —
// adapter failures pattern-match these keywords ("LLM call failed: no
// access…") and would contaminate the corpus (2026-08-11 review).
func DelegationGap(stuckReason string) bool {
	text := strings.ToLower(stuckReason)
	if strings.TrimSpace(text) == "" {
		return false
	}
	for _, kw := range delegationGapKeywords {
		if strings.Contains(text, kw) {
			return true
		}
	}
	return false
}

var delegationGapKeywords = []string{
	"not provided", "wasn't provided", "was not provided", "no such file",
	"not specified", "not included", "wasn't included", "was not included",
	"missing", "no access", "cannot access", "can't access", "unclear",
	"ambiguous", "which one", "not stated", "no context", "need more",
	"not given", "wasn't given", "was not given", "does not say",
	"doesn't say", "no url", "no path", "no input", "unspecified",
}
