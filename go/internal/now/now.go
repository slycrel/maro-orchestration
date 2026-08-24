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
//   - The memory-provenance advisory marker (_mark_memory_provenance:
//     an answer claiming "saved to memory" gets checked against the
//     actual stores; run 9c8d0a43 lost a convention silently) — its
//     stores don't exist here yet; it returns with the memory tranche.
//
// dryRun stamps the row only; the CALLER supplies the canned adapter —
// Python parity: handle() swaps in _DryRunAdapter at the same boundary,
// so neither runtime gates individual calls inside the NOW lane.
package now

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
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
// verify judge, outcome row, verdict stamp, finalize. seedIn/seedOut
// carry spend the caller made BEFORE this run (the routing classify
// call) so the outcome row reports the goal's full real cost, not just
// the lane's share (adversarial routing r1 2026-08-22: classify usage
// vanished from every record).
func Run(ctx context.Context, a llm.Adapter, rec *record.Recorder, goal string, dryRun bool, model string, seedIn, seedOut int) (Result, error) {
	start := time.Now()
	loopID := record.NewID()
	// The NOW lane's loop_id_scope — see the twin in loop.Run, which
	// carries the full reasoning. Same reachable effect here: this lane's
	// own emitters all pass loopID explicitly, so the scope's live payload
	// is the LOG_ROTATED row that rotation writes during this run's
	// appends (adversarial r7 HIGH).
	rec = rec.WithLoopID(loopID)
	res := Result{Status: "done", LoopID: loopID,
		TokensIn: seedIn, TokensOut: seedOut}

	runDir, rerr := runs.Create(rec.WorkspaceDir, loopID, goal)
	if rerr != nil {
		res.Warnings = append(res.Warnings, "run dir create failed: "+rerr.Error())
		runDir = ""
	}

	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: nowSystem},
		{Role: "user", Content: goal},
	}, llm.Options{MaxTokens: 2048, Temperature: 0.4, Purpose: "now"})
	if err == nil && resp == nil {
		err = errors.New("adapter returned nil response with nil error")
	}
	if err != nil {
		res.Status = "error"
		res.Answer = "NOW lane error: " + err.Error()
		// A refused-but-billed call still spent tokens — salvage them
		// (llm.ResultError doctrine; exec.go/loop.go do the same).
		var re *llm.ResultError
		if errors.As(err, &re) {
			res.TokensIn += re.TokensIn
			res.TokensOut += re.TokensOut
			res.Warnings = append(res.Warnings, re.Warnings...)
		}
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

	// Verdict-source taxonomy on the DURABLE row (Python handle.py
	// parity, go_-prefixed): judged → the judge's version tag; errored
	// judge → the error family with goal_achieved absent — review F7's
	// broken-pipe/unjudged distinction must survive an unwatched run.
	// An unparseable verdict carries NO source: that is Python's
	// "failed open, no clear verdict" state, honestly unjudged.
	verdictSource := ""
	switch {
	case res.GoalAchieved != nil:
		verdictSource = record.SourceNowVerify
	case res.NowVerifyError != "":
		verdictSource = record.SourceNowVerifyError
	}
	// res.VerdictSummary arrives already scrubbed at verifyNow's
	// boundary (closure doctrine: scrub where the field is SET, so
	// every consumer — row, stamp, terminal — inherits clean text).
	summary := budget.StepResult.Clip(res.Answer)
	_, outWarns, werr := rec.WriteOutcomeWithLog(record.Outcome{
		Goal: goal, Status: res.Status, Summary: summary,
		TaskType: "now", Model: model, LoopID: loopID, DryRun: dryRun,
		TokensIn: res.TokensIn, TokensOut: res.TokensOut,
		ElapsedMS:         res.Elapsed.Milliseconds(),
		GoalAchieved:      res.GoalAchieved,
		GoalVerdictSource: verdictSource,
		VerdictSummary:    budget.VerdictProse.Clip(res.VerdictSummary),
	})
	res.Warnings = append(res.Warnings, outWarns...)
	if werr != nil {
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
			// confidence nil: the NOW judge measures none, and nil POPS
			// the key — a fabricated 0 on a judged-true run would read
			// as "verified with zero confidence" (Python parity:
			// confidence=None). Summary pre-scrubbed at verifyNow.
			if serr := runs.StampVerdict(runDir, res.GoalAchieved, record.SourceNowVerify,
				res.VerdictSummary, nil, "", nil); serr != nil {
				res.Warnings = append(res.Warnings, "verdict stamp failed: "+serr.Error())
			}
		}
		if ferr := runs.Finalize(runDir, res.Status); ferr != nil {
			res.Warnings = append(res.Warnings, "run metadata finalize failed: "+ferr.Error())
		}
	}
	return res, nil
}

// nowVerifyCut is Python's _NOW_VERIFY_CUT: the judge window's cap,
// with the cut VISIBLE — a judge shown `Response:` cannot tell a whole
// answer from its first 2000 characters, and reports what it cannot
// see as not delivered (the last unmarked judge window in the Python
// codebase, fixed 2026-08-03 alongside three siblings).
const nowVerifyCut = 2000

// verifyPayload ports _now_verify_payload: request + response, each
// truncated at the cut with a marker that tells the judge exactly what
// it is not seeing.
func verifyPayload(goal, answer string) string {
	seg := func(label, text string) string {
		// Rune counts and rune cuts — Python str slicing is codepoint-
		// based, and a byte cut can split UTF-8 mid-rune (corrupting the
		// judge's window) while the marker lies about how much was cut
		// (adversarial routing r2, Skeptic; same class closure.go's
		// cutRunes fixed).
		r := []rune(text)
		if len(r) > nowVerifyCut {
			return fmt.Sprintf("%s [TRUNCATED — first %d of %d characters; "+
				"the rest was NOT shown to you]:\n%s",
				label, nowVerifyCut, len(r), string(r[:nowVerifyCut]))
		}
		return label + ":\n" + text
	}
	return seg("Request", goal) + "\n\n" + seg("Response", answer)
}

// verdictRationale ports _now_verdict_rationale: the judge's prose
// reason, minus the JSON verdict it leads with. Found by run ea4ebe4a
// diagnosing ed7cf400: the judge replied `{"fulfilled": false}`
// followed by a specific, genuinely useful rationale, and only the
// boolean was ever read — the run presented as failed for no stated
// reason. The 160-token budget is HALF the fix; this is the other half.
// Prose-BEFORE-JSON falls through both branches and returns the whole
// text, JSON included — Python-parity residual, named (r2 Architect):
// the judge is instructed JSON-first, and a garbled recovery on a
// disobedient judge still beats the false "gave no rationale".
func verdictRationale(raw string) string {
	// Python: `text = (raw or "").strip()` (handle._now_verdict_rationale).
	// pytext.Strip, not strings.TrimSpace — str.strip() covers
	// U+001C..U+001F and TrimSpace does not.
	//
	// This USED TO pre-strip <think> traces, on the r3 reasoning that a
	// trace before the JSON would otherwise flow through the no-prefix
	// fallthrough and become the recovered "rationale" verbatim. That
	// reasoning was right about the symptom and wrong about the remedy,
	// and the comment saying so is what let it stand for two rounds
	// (mission-r2 MEDIUM). Python does no such strip, and the result
	// lands in res.VerdictSummary — a durable outcome field an operator
	// reads. Same judge reply, two different summaries in the store.
	//
	// If the strip is wanted, it goes into handle._now_verdict_rationale
	// first, where both runtimes inherit it.
	text := pytext.Strip(raw)
	if strings.HasPrefix(text, "```") {
		// ```json { ... } ``` preamble: prose is after the closing fence.
		parts := strings.Split(text, "```")
		if len(parts) > 2 {
			text = pytext.Strip(strings.Join(parts[2:], ""))
		} else {
			text = ""
		}
	} else if strings.HasPrefix(text, "{") {
		// Bare JSON, then prose: skip past the balanced object, exactly
		// as handle._now_verdict_rationale does it:
		//
		//	depth = 0
		//	for i, ch in enumerate(text):
		//	    depth += (ch == "{") - (ch == "}")
		//	    if depth == 0:
		//	        text = text[i + 1:].strip()
		//	        break
		//
		// A NAIVE counter, blind to string literals, and with no
		// unbalanced-input lane at all: if the object never closes, the
		// loop simply ends and `text` keeps the whole blob.
		//
		// THIRD REJECTED HARDENING IN THIS PORT, and the second in this
		// function (adversarial mission-r3 HIGH). The scan used to track
		// string literals and to `return ""` on a truncated object,
		// under a comment saying so — "Go-stricter than Python's naive
		// count" — four lines below the <think> pre-strip that was
		// removed for the same reason one round earlier. Measured:
		//
		//	{"fulfilled": false, "why": "missing } brace in file"} the file was never created
		//	  CPython -> `brace in file"} the file was never created`
		//	  Go      -> `the file was never created`
		//
		//	{"fulfilled": false, "why": "no write call     <- truncated at the token budget
		//	  CPython -> the whole blob back
		//	  Go      -> "" -> the caller then stores the STATIC string
		//	             "judge gave no rationale", which is false, and is
		//	             the exact regression this function exists to stop
		//
		// Both land in res.VerdictSummary, which is written to the
		// outcome row and stamped into the run dir. A `}` inside a `why`
		// string is not exotic: the judge is instructed to name the
		// SPECIFIC missing thing, and that is often a code fragment.
		//
		// If the string-awareness is wanted it belongs in
		// handle._now_verdict_rationale first, where both runtimes get
		// it. `}` is one byte, so text[i+1:] never splits a rune.
		depth := 0
		for i := 0; i < len(text); i++ {
			switch text[i] {
			case '{':
				depth++
			case '}':
				depth--
			}
			if depth == 0 {
				text = pytext.Strip(text[i+1:])
				break
			}
		}
	}
	// No clip here: the caller scrubs FIRST, then the sinks clip — a
	// clip-before-scrub could truncate a credential mid-string and slip
	// it past the fixed-length secret patterns (r2 Skeptic).
	// Python: `" ".join(text.split())`. str.split() with no argument
	// splits on the 29-code-point whitespace set; strings.Fields splits
	// on unicode.IsSpace, which is four code points narrower
	// (U+001C..U+001F) — and pytext's own package doc flags Split for
	// exactly this (mission-r3 MEDIUM). Those four arrive through pasted
	// terminal output, which is precisely what a NOW judge quotes back.
	return strings.Join(pytext.Split(text), " ")
}

// verifyNow runs the text judge and applies the tri-state verdict.
// res.VerdictSummary is scrubbed HERE, where it is set — the boundary
// discipline closure.Verify pinned: every consumer (outcome row,
// verdict stamp, the CLI's terminal print) inherits clean text, and a
// per-sink scrub that misses one sink ships the judge's quoted-back
// secrets to exactly the surface an operator reads (adversarial
// routing r1 2026-08-22, all three lenses independently).
func verifyNow(ctx context.Context, a llm.Adapter, goal string, res *Result) {
	resp, err := a.Complete(ctx, []llm.Message{
		{Role: "system", Content: nowVerifySystem},
		{Role: "user", Content: verifyPayload(goal, res.Answer)},
	}, llm.Options{MaxTokens: 160, Temperature: 0, Purpose: "now-verify"})
	if err != nil || resp == nil {
		// Fail-open stays fail-open, but MARKED (Python review F7: a
		// keyed provider outage used to look identical to no-keys).
		if err != nil {
			res.NowVerifyError = budget.Clip(err.Error(), 120)
			var re *llm.ResultError
			if errors.As(err, &re) {
				res.TokensIn += re.TokensIn
				res.TokensOut += re.TokensOut
			}
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
			res.VerdictSummary = scrub.Secrets(strings.TrimSpace(why))
			if res.VerdictSummary == "" {
				// The rationale often trails the JSON instead of riding
				// the why key — recover it before falling back to a
				// static placeholder that would falsely claim the judge
				// gave no reason.
				res.VerdictSummary = scrub.Secrets(verdictRationale(resp.Content))
			}
			if res.VerdictSummary == "" {
				res.VerdictSummary = "response reports non-fulfillment (judge gave no rationale)"
			}
		}
	}
}
