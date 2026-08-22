// Package now ports handle.py's NOW lane: a single tool-less LLM call
// for trivial asks, followed by the now-verify judge — the NOW lane's
// own "done ≠ successful" (Python _verify_now_outcome). The verdict is
// TRI-STATE: fulfilled → goal_achieved true; not fulfilled → status
// incomplete + goal_achieved false + the judge's why; unparseable or
// errored judge → fail OPEN, no goal_achieved key, and the error MARKED
// (now_verify_error) so a broken verdict pipe never looks like a
// deliberately unjudged run (Python review F7).
//
// Ported lessons (each pinned):
//   - The judge gets 160 tokens, not 64: `{"fulfilled": false}` fits in
//     64 with no room for a why, which shipped an inert propagation fix
//     (run 2113a608, 2026-08-02) — the rationale is the only
//     explanation a person sees.
//   - Non-answers are failures: the verify prompt demotes generic
//     how-to-find-it guidance and answered-a-different-question
//     responses, not only explicit "I couldn't".
//   - Empty model content records "[no response]", never an empty
//     result masquerading as an answer.
//
// Deliberately unported, NAMED (each returns with its subsystem):
//   - URL pre-fetch enrichment (web_fetch; the conversational-compute
//     link-read lane) — intent's classifier correspondingly does not
//     route link-triage NOW (see internal/intent's package doc).
//   - The deterministic provenance guard (provenance.py: claimed
//     inputs/outputs verified on disk before the text judge) — its
//     path machinery is a subsystem; until it ports, a NOW response
//     claiming files is caught only by the text judge. Note the NOW
//     lane cannot WRITE files here (tool-less), and file-deliverable
//     goals are routed AGENDA by intent's capability override.
//   - The interactive-lane variants (adapter=None judge-skip, NOW
//     artifact write, navigator dispatch, deferred learning).
package now

import (
	"context"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
	"github.com/slycrel/maro-orchestration/go/internal/scrub"
)

const nowSystem = `You are an autonomous AI assistant.
Answer the user's request directly and completely. Be thorough but concise.
If the request is a question, answer it. If it's a task, complete it.
Do not hedge or defer — just do the work.
`

const nowVerifySystem = "You judge whether a response fulfilled a request. Reply with JSON only: " +
	`{"fulfilled": true} or {"fulfilled": false, "why": "<one short sentence>"}. ` +
	"On false, `why` must name the SPECIFIC missing thing (what was claimed " +
	"vs what is absent) — not a restatement of the verdict. It is the only " +
	"explanation a person will see for the run. Omit `why` when fulfilled. " +
	"fulfilled=false when the response states the task could not be done, is " +
	"incomplete or impossible, or only explains why it failed. " +
	"fulfilled=false also when the response is a NON-ANSWER: it answers a " +
	"different question than asked, offers generic how-to-find-it guidance " +
	"instead of the asked-for answer, or lacks the specific information " +
	"requested (e.g. the request asks WHERE and the response names no " +
	"place, or asks WHICH and the response picks nothing). " +
	"fulfilled=true when the response delivers what was asked."

// Result is the NOW run's outcome for the CLI.
type Result struct {
	Status  string // "done" | "incomplete" | "error"
	Answer  string
	LoopID  string
	Elapsed time.Duration
	// GoalAchieved tri-state; VerdictSummary is the judge's why on
	// demotion (empty otherwise). NowVerifyError marks a judge that
	// errored (fail-open, unjudged).
	GoalAchieved   *bool
	VerdictSummary string
	NowVerifyError string
	TokensIn       int
	TokensOut      int
	Warnings       []string
}

// Run executes one NOW-lane goal end-to-end: run dir, single call,
// verify judge, outcome row, verdict stamp, finalize.
func Run(ctx context.Context, a llm.Adapter, rec *record.Recorder, goal string, dryRun bool, model string) (Result, error) {
	start := time.Now()
	loopID := record.NewID()
	res := Result{Status: "done", LoopID: loopID}

	runDir, rerr := runs.Create(rec.WorkspaceDir, loopID, goal)
	if rerr != nil {
		res.Warnings = append(res.Warnings, "run dir create failed: "+rerr.Error())
		runDir = ""
	}

	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: nowSystem},
		{Role: "user", Content: goal},
	}, llm.Options{MaxTokens: 2048, Temperature: 0.4, Purpose: "now"})
	if err != nil {
		res.Status = "error"
		res.Answer = "NOW lane error: " + err.Error()
	} else {
		res.TokensIn += resp.TokensIn
		res.TokensOut += resp.TokensOut
		res.Warnings = append(res.Warnings, resp.Warnings...)
		res.Answer = strings.TrimSpace(resp.Content)
		if res.Answer == "" {
			res.Answer = "[no response]"
		}
	}

	// The verify judge runs only on a successful call — an errored call
	// already carries its honest status.
	if err == nil {
		verifyNow(ctx, a, goal, &res)
	}
	res.Elapsed = time.Since(start)

	summary := budget.StepResult.Clip(res.Answer)
	if _, werr := rec.WriteOutcome(record.Outcome{
		Goal: goal, Status: res.Status, Summary: summary,
		TaskType: "now", Model: model, LoopID: loopID, DryRun: dryRun,
		TokensIn: res.TokensIn, TokensOut: res.TokensOut,
		ElapsedMS:    res.Elapsed.Milliseconds(),
		GoalAchieved: res.GoalAchieved,
		VerdictSummary: budget.VerdictProse.Clip(
			scrub.Secrets(res.VerdictSummary)),
	}); werr != nil {
		res.Warnings = append(res.Warnings, "outcome recording failed: "+werr.Error())
	}
	if evErr := rec.Event("NOW_ANSWERED", loopID,
		budget.Clip("NOW lane "+res.Status+": "+goal, 200),
		map[string]any{"status": res.Status, "tokens_in": res.TokensIn,
			"tokens_out": res.TokensOut, "judged": res.GoalAchieved != nil},
		loopID); evErr != nil {
		res.Warnings = append(res.Warnings, "captain's log write failed: "+evErr.Error())
	}
	if runDir != "" {
		if res.GoalAchieved != nil || res.VerdictSummary != "" {
			if serr := runs.StampVerdict(runDir, res.GoalAchieved, "go_now_verify_v1",
				scrub.Secrets(res.VerdictSummary), 0, "", nil); serr != nil {
				res.Warnings = append(res.Warnings, "verdict stamp failed: "+serr.Error())
			}
		}
		if ferr := runs.Finalize(runDir, res.Status); ferr != nil {
			res.Warnings = append(res.Warnings, "run metadata finalize failed: "+ferr.Error())
		}
	}
	return res, nil
}

// verifyNow runs the text judge and applies the tri-state verdict.
func verifyNow(ctx context.Context, a llm.Adapter, goal string, res *Result) {
	payload := "Request: " + goal + "\n\nResponse: " + res.Answer
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: nowVerifySystem},
		{Role: "user", Content: payload},
	}, llm.Options{MaxTokens: 160, Temperature: 0, Purpose: "now-verify"})
	if err != nil || resp == nil {
		// Fail-open stays fail-open, but MARKED (Python review F7: a
		// keyed provider outage used to look identical to no-keys).
		if err != nil {
			res.NowVerifyError = budget.Clip(err.Error(), 120)
		} else {
			res.NowVerifyError = "nil response"
		}
		return
	}
	res.TokensIn += resp.TokensIn
	res.TokensOut += resp.TokensOut
	obj, jerr := jsonx.Object(resp.Content)
	if jerr != nil || obj == nil {
		// No clear verdict: goal achievement stays unverified — absence
		// means "not judged", not "failed".
		return
	}
	switch v := obj["fulfilled"].(type) {
	case bool:
		achieved := v
		res.GoalAchieved = &achieved
		if !v {
			res.Status = "incomplete"
			why, _ := obj["why"].(string)
			res.VerdictSummary = strings.TrimSpace(why)
			if res.VerdictSummary == "" {
				res.VerdictSummary = "response reports non-fulfillment (judge gave no rationale)"
			}
		}
	}
}
