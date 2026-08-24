package director

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/budget"
	"github.com/slycrel/maro-orchestration/go/internal/config"
	"github.com/slycrel/maro-orchestration/go/internal/jsonx"
	"github.com/slycrel/maro-orchestration/go/internal/llm"
	"github.com/slycrel/maro-orchestration/go/internal/notify"
	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/runs"
	"github.com/slycrel/maro-orchestration/go/internal/tasks"
)

// handle_escalation is the closure mechanism for the dynamic tree
// traversal: a task that has been through several continuation passes
// without completing gets a reasoned decision from a higher layer instead
// of accumulating silently.
//
// Four actions, and each one is a different KIND of durable write:
//
//	continue  enqueues a deeper pass (queue)
//	narrow    enqueues a rewritten, smaller pass (queue)
//	close     stamps a stop verdict on the escalated run AND its outcome
//	          row (metadata + ledger), then writes an operator artifact
//	surface   writes an operator artifact and stamps NOTHING
//
// The close/surface asymmetry is the load-bearing one. A judged close is
// the single seam where reachable-but-not-worth-it gets decided; a
// surface means the operator has not decided anything yet, so recording a
// verdict there would put a judgment in the store that no one made.

const escalationSystem = `You are the Director for Maro, an autonomous orchestration system.
A task has been through multiple continuation passes without completing.
Your job: decide what happens next. You are not an executor — you are a judge.

You will receive:
- The original goal
- What has been accomplished (completed steps)
- What remains (incomplete steps)
- The continuation depth (how many passes have been attempted)

DECISION TAXONOMY — classify your decision before choosing an action:

MECHANICAL: The right move is obvious from the evidence. No human judgment needed.
  Auto-decide. Log reasoning but do not surface. Examples: scope is clearly bounded,
  completed work clearly answers the core question, goal is unambiguously too large.

TASTE: A reasonable person could disagree with this call, but you have a defensible position.
  Auto-decide. Surface your reasoning prominently in summary_for_user so the operator
  can override if they disagree. Examples: close vs. continue is a judgment call,
  narrowing strategy has trade-offs, partial result quality is debatable.

USER_CHALLENGE: This requires human judgment. Cannot be auto-decided.
  Always output action="surface". Provide a clear framing of the decision the operator
  needs to make. Examples: contradictory signals, ethical/policy questions, scope ambiguity
  that depends on unstated operator preferences, risk of destroying prior work.

ACTIONS:
- "continue": remaining work is valid and worth pursuing; spawn another focused pass
- "narrow": scope is still too broad; rewrite the goal to a smaller, achievable target
  (provide a revised_goal in your response)
- "close": partial result is sufficient; accept what's been done
- "surface": requires human judgment; escalate to the operator with a summary

Rules:
- "continue" only if the remaining work is distinct and bounded (not the same breadth as the original)
- "narrow" when the original goal was genuinely too large but a smaller slice would be valuable
- "close" when the completed work already answers the core question even if incomplete
- "surface" when there is no clear automated path forward (USER_CHALLENGE cases always surface)
- Never "continue" indefinitely — prefer "close" or "surface" over a fifth+ continuation

CONFIDENCE SCORE (1–10):
Rate your confidence in this decision. Be calibrated — not all decisions are equally clear.
- 8–10: Mechanical decisions with strong evidence. Act without caveat.
- 5–7: Taste decisions. Flag uncertainty in summary_for_user.
- 1–4: Genuine uncertainty. Override to "surface" regardless of your action choice.

ANTI-SYCOPHANCY RULES (non-negotiable):
- Take a position. State your decision clearly — never answer with "it depends" alone.
- If the escalation context contains a bad assumption, name it.
- State what information would change this decision.
- Never open with affirmations: no "Great!", "Certainly!", "Of course!", "Happy to help!".
- Prefer honest uncertainty over false confidence. If you don't know, score low and surface.

Respond with a JSON object:
{
  "action": "continue" | "narrow" | "close" | "surface",
  "decision_class": "mechanical" | "taste" | "user_challenge",
  "confidence": <integer 1-10>,
  "reasoning": "one or two sentences explaining the decision",
  "revised_goal": "narrowed goal string (only if action == 'narrow')",
  "summary_for_user": "brief status summary for operator/user (always include)"
}`

// EscalationDecision is what handle_escalation returns. FollowupTaskID is
// empty when no task was enqueued, standing in for Python's Optional[str]
// — the only consumer treats None and "" identically (`followup or
// 'none'`), so a pointer would buy nothing but a nil dereference.
type EscalationDecision struct {
	Action         string // continue | narrow | close | surface
	Reasoning      string
	FollowupTaskID string
	SummaryForUser string
	DecisionClass  string // mechanical | taste | user_challenge
	Confidence     int    // 1–10
}

// LowConfidenceNotifier is the ONE method this path uses from Python's
// ConversationChannel. Narrowing it to the method actually called keeps a
// caller from having to build a whole channel to exercise the escalation
// lane, and makes the coupling legible: the escalation does not converse,
// it files one advisory.
type LowConfidenceNotifier interface {
	NotifyLowConfidence(decision string, confidence float64, reasoning string)
}

// EscalationOptions are handle_escalation's keyword arguments.
type EscalationOptions struct {
	Adapter llm.Adapter
	DryRun  bool
	Verbose bool
	Channel LowConfidenceNotifier

	// Config is the merged config map the check-in cadence reads. A nil
	// map yields every default, which is what config.Get does with a
	// missing key — so a caller that has not loaded config gets Python's
	// documented defaults rather than a panic.
	Config map[string]any

	// Notify is handed to the recursion check-in's emit.
	//
	// It exists because the zero value was NOT safe: notify.Options with a
	// nil Cfg falls back to config.Load(), which reads the OPERATOR'S
	// ~/.maro/config.yml, and on this box that registers a notify.command
	// that messages Telegram and ssh's to another host. `go test
	// ./internal/director/` was firing it — five check-in cases across the
	// differential plus the unit tests, on every round (adversarial r11
	// round 2, HIGH).
	//
	// Passing it in rather than defaulting is the same choice this port
	// makes for `ws` and `Config`: one resolution order, and it comes from
	// the caller. A caller that genuinely wants the ambient hook passes
	// notify.Options{} on purpose, which is a different thing from getting
	// it by not thinking about it.
	Notify notify.Options

	// Log receives the same lines Python's `log` does. Optional.
	Log func(format string, args ...any)
}

func (o EscalationOptions) logf(format string, args ...any) {
	if o.Log != nil {
		o.Log(format, args...)
	}
}

// HandleEscalation processes a loop_escalation task and decides what
// happens next.
//
// ws is explicit where Python reads the workspace from config — the same
// choice the rest of this port makes, and for the same reason (one
// resolution order, passed in).
func HandleEscalation(ctx context.Context, ws string, task pyval.Obj,
	o EscalationOptions) EscalationDecision {

	// Bare `.get` with a default, NOT safe_str: Python does no coercion
	// on these four, so a non-string field survives as whatever it is and
	// is spelled by str() at each interpolation. taskGet reproduces that
	// — the coercion happens where Python's f-string does it.
	reason := taskGet(task, "reason", "")
	// RAW, like reason and the two ids beside it. Python holds
	// `continuation_depth` untouched and spells it with str() at each
	// interpolation, so a float depth — which is what ANY foreign JSON
	// writer produces, jq and JavaScript included — stays "2.0" in the
	// calibration row, the event detail, the operator artifact and the stop
	// evidence. Reading it as an int here loses the ".0" in five places at
	// once and nothing raises to say so.
	depth := taskGet(task, "continuation_depth", 0)
	depthStr := pyval.Str(depth)
	// jobIDRaw is what Python holds. Every f-string spells it with str()
	// at the point of use, and that is what jobID is for — but two places
	// need the raw value: write_event's loop_id, which is not coerced, and
	// the artifact path's `job_id[:8]`, which RAISES for a non-string and
	// so writes no artifact at all.
	jobIDRaw := taskGet(task, "job_id", "unknown")
	jobID := pyval.Str(jobIDRaw)
	// RAW as well, and the reason is a lock file. The close branch hands
	// this straight to the stop-verdict stamp, whose first line is
	// `if not loop_id: return` — a TRUTHINESS gate. Spelling a JSON null as
	// "None" first walks past that gate, creates memory/ and takes
	// memory/outcomes.jsonl.lock that CPython never takes, and would stamp a
	// row keyed on the literal string "None".
	parentID := taskGet(task, "parent_job_id", "")

	o.logf("escalation_start job_id=%s depth=%s", jobID, depthStr)
	if o.Verbose {
		fmt.Fprintf(os.Stderr, "[maro:director:escalation] job=%s depth=%s\n", jobID, depthStr)
	}

	// Dry-run: close the escalation without further work. Note this close
	// does NOT stamp a stop verdict — it returns before the branch that
	// would, which is correct: nothing was judged.
	if o.DryRun || o.Adapter == nil {
		return EscalationDecision{
			Action:         "close",
			Reasoning:      "[dry-run] escalation acknowledged, closing",
			SummaryForUser: fmt.Sprintf("Dry-run escalation for job %s at depth %s", jobID, depthStr),
			DecisionClass:  "mechanical",
			Confidence:     5,
		}
	}

	// ObjectOrdered, not Object. `Object` flattens through pyval.Plain, so a
	// nested object arrives as a Go map and pyval.Str answers the
	// "<unordered map: decode with LoadsOrdered>" sentinel — where CPython's
	// safe_str is `str(value).strip()` and gives `{'why': '...'}`. That
	// sentinel would land in the operator artifact, the events detail,
	// escalations.jsonl, and both stop-evidence stores (adversarial r11
	// round 2, MEDIUM). Ordered values render through Repr, which is the
	// repr Python writes.
	var data pyval.Obj
	resp, err := o.Adapter.Complete(ctx, []llm.Message{
		{Role: "system", Content: escalationSystem},
		{Role: "user", Content: fmt.Sprintf(
			"Escalation context:\n\n%s\n\n"+
				"Continuation depth: %s\n\n"+
				"What should happen next? Respond with JSON only.",
			pyval.Str(reason), depthStr)},
	}, llm.Options{
		MaxTokens:   512,
		Temperature: 0.1,
		Purpose:     "escalation decision",
		// no_tools=True: no Tools, so nothing is injected into the prompt.
	})
	if err != nil {
		o.logf("escalation LLM call failed, defaulting to surface: %v", err)
	} else if obj, perr := jsonx.ObjectOrdered(llm.ContentOrEmpty(resp)); perr == nil {
		data = obj
	}

	if len(data) == 0 {
		// Python's guard is `if not data`, which is falsy for None AND for
		// an empty dict — a model that replied `{}` gets the same
		// treatment as one that failed to reply at all.
		return EscalationDecision{
			Action:    "surface",
			Reasoning: "LLM call failed during escalation processing",
			SummaryForUser: fmt.Sprintf(
				"Escalation for job %s (depth %s) could not be processed automatically.",
				jobID, depthStr),
			DecisionClass: "mechanical",
			Confidence:    5,
		}
	}

	action := pytext.Lower(pytext.Strip(pyval.SafeStr(dataGet(data, "action", "surface"), "")))
	if !escalationActions[action] {
		action = "surface"
	}
	reasoning := pyval.SafeStr(dataGet(data, "reasoning", ""), "")
	summaryForUser := pyval.SafeStr(dataGet(data, "summary_for_user", ""), "")
	revisedGoal := pyval.SafeStr(dataGet(data, "revised_goal", ""), "")
	decisionClass := pytext.Lower(pytext.Strip(
		pyval.SafeStr(dataGet(data, "decision_class", "mechanical"), "")))
	if !escalationClasses[decisionClass] {
		decisionClass = "mechanical"
	}
	confidence := pyIntOr(dataGet(data, "confidence", 5), 5)
	if confidence < 1 {
		confidence = 1
	}
	if confidence > 10 {
		confidence = 10
	}

	// Confidence-gated enforcement: low confidence overrides to surface.
	if confidence < 5 {
		o.logf("escalation confidence=%d < 5, overriding action=%s to surface", confidence, action)
		action = "surface"
		summaryForUser = fmt.Sprintf(
			"[Low confidence (%d/10) — escalating to operator] ", confidence) + summaryForUser
	} else if confidence <= 6 {
		// Taste-level uncertainty: add caveat.
		summaryForUser = fmt.Sprintf("[Confidence %d/10] ", confidence) + summaryForUser
	}

	// User-challenge decisions always surface regardless of LLM action choice.
	if decisionClass == "user_challenge" && action != "surface" {
		o.logf("escalation decision_class=user_challenge, overriding action=%s to surface", action)
		action = "surface"
	}

	// Notify the channel of risky judgment calls (confidence <= 7 = < 70% sure).
	// Deliberately AFTER both overrides: the advisory is about a decision
	// that is actually going to be acted on, and a surfaced decision needs
	// no advisory because the operator is already in the loop.
	if o.Channel != nil && confidence <= 7 && action != "surface" {
		func() {
			// Channel notifications must never block escalation logic —
			// Python's bare `except: pass` around this call.
			defer func() { _ = recover() }()
			o.Channel.NotifyLowConfidence(
				fmt.Sprintf("%s: %s", action, pyval.Clip(summaryForUser, 120)),
				float64(confidence)/10.0,
				pyval.Clip(reasoning, 200),
			)
		}()
	}

	logCalibration(ws, jobIDRaw, depth, action, decisionClass, confidence, o)

	o.logf("escalation_decision job_id=%s action=%s reasoning=%q", jobID, action,
		pyval.Clip(reasoning, 80))

	followupTaskID := ""

	switch {
	case action == "continue":
		// Everything from `depth + 1` to the enqueue sits inside ONE try in
		// Python, so a raise anywhere in it — a non-numeric depth, a
		// malformed origin, a non-numeric cadence field, the enqueue itself
		// — lands on the same except and produces the same surface. Go has
		// no try, so the sequence is written out and every step reports into
		// the same `terr`.
		var (
			newDepth      any
			origin        pyval.Obj
			shouldCheckin bool
			t             tasks.Task
		)
		newDepth, terr := pyval.AddOne(depth)
		if terr == nil {
			// Deep-recursion check-in cadence (non-blocking) + carry ancestry.
			origin, shouldCheckin, terr = AdvanceOriginWithCheckin(task, newDepth, o.Config)
		}
		if terr == nil {
			t, terr = tasks.Enqueue(ws, tasks.Options{
				Lane:   "agenda",
				Source: "loop_continuation",
				// The original escalation context becomes the continuation
				// context — RAW, both of them. Python passes the task's own
				// `reason` and `job_id` straight into make_task, which writes
				// what it was given. Spelling them here makes a dict reason a
				// Python-repr string and an integer id a quoted number, and
				// `reason` IS the continuation's goal: the next pass would
				// read a different goal than CPython's.
				Reason:            reason,
				ParentJobID:       jobIDRaw,
				ContinuationDepth: newDepth,
				Origin:            origin,
			})
		}
		if terr != nil {
			o.logf("escalation continue: failed to enqueue continuation: %v", terr)
			// Suppressing the misleading check-in is not enough — the chain
			// is now dead with no continuation and no operator signal beyond
			// a warning log. Fall back to "surface" so the queue's
			// action=="surface" notify path actually tells someone.
			action = "surface"
			summaryForUser = fmt.Sprintf(
				"This recursive goal chain stopped: the continuation could "+
					"not be enqueued (%v). No follow-up task exists — "+
					"original reasoning was: %s", terr, summaryForUser)
			reasoning = fmt.Sprintf("enqueue failed: %v", terr)
			break
		}
		followupTaskID = t.GetString("job_id")
		o.logf("escalation_continue: enqueued %s depth=%v", followupTaskID, newDepth)
		// Fire only after the continuation is CONFIRMED enqueued — a failed
		// enqueue must never tell the user "still running".
		if shouldCheckin {
			FireCheckin(ws, task, newDepth, action, reasoning, summaryForUser, origin, o)
		}

	case action == "narrow" && revisedGoal == "":
		// The model chose narrow but forgot to provide a revised goal.
		o.logf("escalation narrow: no revised_goal from LLM, falling back to surface")
		action = "surface"

	case action == "narrow":
		// One try in Python; see the continue branch above.
		var (
			newDepth      any
			origin        pyval.Obj
			shouldCheckin bool
			t             tasks.Task
		)
		newDepth, terr := pyval.AddOne(depth)
		if terr == nil {
			origin, shouldCheckin, terr = AdvanceOriginWithCheckin(task, newDepth, o.Config)
		}
		if terr == nil {
			t, terr = tasks.Enqueue(ws, tasks.Options{
				Lane:   "agenda",
				Source: "loop_continuation",
				// An f-string in Python, so a Go string is exactly right
				// here — unlike the continue branch's raw pass-through one
				// case up. The ID beside it is still raw.
				Reason:            fmt.Sprintf("NARROWED from escalation %s:\n\n%s", jobID, revisedGoal),
				ParentJobID:       jobIDRaw,
				ContinuationDepth: newDepth,
				Origin:            origin,
			})
		}
		if terr != nil {
			o.logf("escalation narrow: failed to enqueue narrowed task: %v", terr)
			// Same rationale as the continue branch: a dead chain must
			// surface to an operator, not disappear behind a warning log.
			action = "surface"
			summaryForUser = fmt.Sprintf(
				"This recursive goal chain stopped: the narrowed continuation "+
					"could not be enqueued (%v). No follow-up task exists — "+
					"revised goal was: %s", terr, pyval.Clip(revisedGoal, 200))
			reasoning = fmt.Sprintf("enqueue failed: %v", terr)
			break
		}
		followupTaskID = t.GetString("job_id")
		o.logf("escalation_narrow: enqueued %s with revised goal %q",
			followupTaskID, pyval.Clip(revisedGoal, 60))
		if shouldCheckin {
			FireCheckin(ws, task, newDepth, action, reasoning, summaryForUser, origin, o)
		}

	case action == "close" || action == "surface":
		if action == "close" {
			// Judged close = reachable-but-not-worth-it, stamped onto the
			// escalated run + its outcome row. Surface stays unstamped: the
			// operator has not decided anything yet.
			StampCloseStopVerdict(ws, parentID, depth, confidence, reasoning, o)
		}
		writeEscalationSummary(ws, jobIDRaw, action, depth, reasoning, summaryForUser, reason, o)
	}

	// A narrow that fell through to surface above does NOT get an artifact,
	// because Python's elif chain has already been taken — the missing
	// artifact is a real consequence of the fallback, not an omission here.
	// The same is true of an enqueue failure in either spawn branch.

	if o.Verbose {
		fmt.Fprintf(os.Stderr, "[maro:director:escalation] %s: %s\n",
			action, pyval.Clip(reasoning, 80))
	}

	// Observable event for dashboard visibility into escalation decisions.
	followupLabel := followupTaskID
	if followupLabel == "" {
		followupLabel = "none"
	}
	// `goal=task.get("reason", "")[:80]` — a bare slice on the RAW value.
	// A non-string reason makes that slice raise TypeError, and the
	// blanket except around the whole emit swallows it, so NO event is
	// written at all. That is a real observable difference (a missing row
	// in the feed maro-observe tails), so it is reproduced rather than
	// smoothed over with str().
	if eventGoal, sliceable := pySliceHead(taskGet(task, "reason", ""), 80); sliceable {
		notify.WriteEvent(ws, "escalation_processed", notify.EventFields{
			Goal: eventGoal,
			// project and loop_id are the RAW task values, unsliced and
			// uncoerced — write_event does not touch either, so a task
			// carrying an int parent_job_id writes a JSON number and one
			// carrying a null writes null. goal above would already have
			// raised on the same value, which is why it needs a gate and
			// these two do not.
			//
			// An earlier cut spelled both with pyval.Str under a comment
			// that already said "RAW"; the row then carried "4242" against
			// CPython's 4242 and "None" against its null. The comment was
			// right and the type made it impossible to honour.
			Project: taskGet(task, "parent_job_id", ""),
			LoopID:  jobIDRaw,
			Status:  action,
			Detail: fmt.Sprintf("depth=%s followup=%s | %s",
				depthStr, followupLabel, pyval.Clip(reasoning, 100)),
		})
	}

	return EscalationDecision{
		Action:         action,
		Reasoning:      reasoning,
		FollowupTaskID: followupTaskID,
		SummaryForUser: summaryForUser,
		DecisionClass:  decisionClass,
		Confidence:     confidence,
	}
}

var escalationActions = map[string]bool{
	"continue": true, "narrow": true, "close": true, "surface": true,
}

var escalationClasses = map[string]bool{
	"mechanical": true, "taste": true, "user_challenge": true,
}

// --- the recursion check-in ---------------------------------------------
//
// The continue/narrow branches re-enqueue a fresh continuation with
// continuation_depth+1 — a chain of sequential DISTINCT goal executions,
// which is a different mechanism from a single loop's retry cap. At the
// 3rd goal pass (new_depth==2) and every jittered 4–7 passes after, a
// NON-BLOCKING progress check-in fires so the user can redirect or stop;
// the goal keeps running regardless. This is deliberately not the
// `escalate` navigator move, which parks the goal.

// CheckinFirstDepth is the depth at which the first check-in fires.
//
// The read is `int(config_get(...))` in a try, NOT a typed lookup. That
// matters because config values come from YAML written by a human: a quoted
// `"3"` is 3 to Python and would have been the default 2 to a float64/int
// type assertion, and `3.7` is 3 there and 2 here. This function decides
// whether an operator is notified at all (adversarial r11 round 2, MEDIUM).
func CheckinFirstDepth(cfg map[string]any) int {
	val, ok := pyInt(config.GetRaw(cfg, "recursion.checkin_first_depth", 2))
	if !ok {
		return 2
	}
	if val < 1 {
		return 1
	}
	return val
}

// checkinRandInt is random.randint(lo, hi) — INCLUSIVE at both ends,
// unlike Go's rand.Intn. A package var so a test can make the jitter
// deterministic without reaching into the cadence logic.
var checkinRandInt = func(lo, hi int) int {
	return lo + rand.Intn(hi-lo+1)
}

// CheckinJitter is the random 4–7-goal cadence for check-ins after the
// first (jittered, not fixed).
func CheckinJitter(cfg map[string]any) int {
	// ONE try around BOTH reads in Python, which is not the same as two
	// independent defaults: a bad `checkin_jitter_max` resets the MINIMUM
	// too, so `min: 10, max: "abc"` draws from 4–7 there and would draw
	// from 7–10 here if each read defaulted on its own.
	lo, okLo := pyInt(config.GetRaw(cfg, "recursion.checkin_jitter_min", 4))
	hi, okHi := pyInt(config.GetRaw(cfg, "recursion.checkin_jitter_max", 7))
	if !okLo || !okHi {
		lo, hi = 4, 7
	}
	if hi < lo {
		lo, hi = hi, lo
	}
	if lo < 1 {
		lo = 1
	}
	if hi < lo {
		hi = lo
	}
	return checkinRandInt(lo, hi)
}

// AdvanceOriginWithCheckin returns (origin, shouldFire) — it advances the
// check-in cadence state on the origin ancestry object WITHOUT firing the
// notification itself.
//
// The split is the point. The caller enqueues the continuation first and
// fires the check-in only after that enqueue succeeds; otherwise a failed
// enqueue would still tell the user "still running" for a chain that just
// silently died.
// It returns an ERROR for the three CPython operations here that raise, and
// the error is not cosmetic: every one of them happens inside the spawn
// branch's own try, whose except flips the action to `surface` and enqueues
// NOTHING. A port that swallowed them would create a queue entry CPython
// never creates, fire a check-in saying "still running", and report
// `continue` where CPython reported `surface` — with the exception's own
// message in the ledger row.
func AdvanceOriginWithCheckin(task pyval.Obj, newDepth any, cfg map[string]any) (pyval.Obj, bool, error) {
	// `Origin(task.get("origin") or {})`. The `or` is a truthiness gate, so
	// None, {}, "" and 0 all become a fresh object — but a TRUTHY non-mapping
	// reaches dict(), which raises. `Origin` is a TypedDict, so `Origin(x)`
	// is `dict(x)` at runtime with none of a cast's forgiveness.
	//
	// The copy matters: Python builds a NEW dict from the task's, so
	// advancing the cadence must not mutate the task the caller still holds.
	// Aliasing here would advance next_checkin_depth on a task that was never
	// enqueued when the enqueue below fails.
	origin := pyval.Obj{}
	if raw := taskGet(task, "origin", nil); pyval.Truthy(raw) {
		built, err := pyval.DictOf(raw)
		if err != nil {
			return nil, false, err
		}
		origin = built
	}
	// The default is evaluated EAGERLY — it is an argument to .get(), so the
	// config read and its clamp happen even when the key is present.
	var nextCheckin any = CheckinFirstDepth(cfg)
	if v, ok := origin.Get("next_checkin_depth"); ok {
		nextCheckin = v
	}
	shouldFire, err := pyval.GE(newDepth, nextCheckin)
	if err != nil {
		return nil, false, err
	}
	if shouldFire {
		jittered, err := pyval.AddN(newDepth, CheckinJitter(cfg))
		if err != nil {
			return nil, false, err
		}
		origin.Set("next_checkin_depth", jittered)
		var sent any = 0
		if v, ok := origin.Get("checkins_sent"); ok {
			sent = v
		}
		next, err := pyval.AddOne(sent)
		if err != nil {
			return nil, false, err
		}
		origin.Set("checkins_sent", next)
	}
	return origin, shouldFire, nil
}

const checkinRedirectHint = "This goal is still running in the background. Reply on your " +
	"configured channel (Telegram / Slack / CLI) to redirect or stop " +
	"it — no reply means keep going."

// FireCheckin emits a non-blocking recursion_checkin notify event. Never
// raises, by contract: a notify failure must not affect whether the
// continuation was enqueued.
//
// Every field comes from data already in hand — no second LLM call. The
// director's own reasoning and summary_for_user from THIS escalation
// decision already explain how the work serves the original ask.
func FireCheckin(ws string, task pyval.Obj, newDepth any, action, reasoning,
	summaryForUser string, origin pyval.Obj, o EscalationOptions) {

	// Original ask: prefer the root goal carried in ancestry, fall back to
	// this chain's escalation reason. Don't block the check-in on being
	// able to reconstruct full lineage.
	//
	// The `or` gates on TRUTHINESS of the raw value, not on it being a
	// string. A parent_goal of 55 is truthy, so CPython carries "55" into
	// the ledger row where a type assertion reads "" and silently falls
	// through to a DIFFERENT string — the escalation reason.
	originalGoal, _ := origin.Get("parent_goal")
	if !pyval.Truthy(originalGoal) {
		originalGoal = taskGet(task, "reason", "")
	}
	// origin["checkins_sent"] was already advanced to include THIS
	// check-in by AdvanceOriginWithCheckin — adding another +1 here would
	// report one check-in ahead of the count actually carried in origin.
	//
	// This int() is BARE, with no local try, so a non-numeric count raises
	// into the blanket except wrapping this whole function: no notify at
	// all, no ledger row, no hook. Answering 0 instead would emit a row
	// CPython never writes.
	sentRaw := any(0)
	if v, ok := origin.Get("checkins_sent"); ok {
		sentRaw = v
	}
	checkinNumber, ok := pyInt(sentRaw)
	if !ok {
		o.logf("recursion check-in emit failed (non-fatal): int(%s)", pyval.Repr(sentRaw))
		return
	}
	// An ORDERED payload, in Python's dict-literal order, because two
	// surfaces write these keys out positionally: output/escalations.jsonl
	// and the notify hook's stdin. A Go map would have alphabetized them
	// — which is still valid JSON, still parses, and is why the
	// divergence sat as a named residual from r9 until this payload gave
	// a differential something to see.
	handleID, _ := origin.Get("parent_handle_id")
	// `new_depth + 1` cannot raise here: new_depth is already the result of
	// `depth + 1` upstream, so it is a number by construction.
	goalPass, err := pyval.AddOne(newDepth)
	if err != nil {
		o.logf("recursion check-in emit failed (non-fatal): %v", err)
		return
	}
	payload := pyval.Obj{
		{Key: "handle_id", Val: pyval.StrOrEmpty(handleID)},
		// Distinguishes this from a park-the-goal escalation.
		{Key: "blocking", Val: false},
		{Key: "goal", Val: pyval.Clip(pyval.Str(originalGoal), 400)},
		{Key: "reason", Val: pyval.Clip(pyval.Str(originalGoal), 400)},
		{Key: "continuation_depth", Val: pyval.FromPlain(newDepth)},
		// pass 1 == depth 0
		{Key: "goal_pass", Val: pyval.FromPlain(goalPass)},
		{Key: "checkin_number", Val: checkinNumber},
		{Key: "action", Val: action},
		{Key: "reasoning", Val: reasoning},
		{Key: "summary_for_user", Val: summaryForUser},
		// RAW, both of them: Python's dict literal carries the task's own
		// values and notify's writer passes them to json.dumps untouched, so
		// an integer job id stays an integer in the ledger row.
		{Key: "job_id", Val: pyval.FromPlain(taskGet(task, "job_id", ""))},
		{Key: "parent_job_id", Val: pyval.FromPlain(taskGet(task, "parent_job_id", ""))},
		{Key: "redirect_hint", Val: checkinRedirectHint},
		{Key: "status", Val: "running"},
	}
	notify.EmitOrdered(context.Background(), ws, "recursion_checkin", payload, o.Notify)
	o.logf("recursion_checkin fired: depth=%v pass=%v checkin=%d action=%s",
		newDepth, goalPass, checkinNumber, action)
}

// --- the judged close ----------------------------------------------------

// StampCloseStopVerdict records a judged escalation close as a typed stop
// verdict.
//
// "close" is the one seam where reachable-but-not-worth-it gets decided.
// Post-hoc refinement: the escalated run usually carries out-of-budget
// from its own cap break, and the director's close is a later,
// better-informed judgment ending the whole chain — so it overwrites,
// keeping the prior verdict visible in the evidence. The reopen condition
// is type-derived: the cost/value estimate moves.
func StampCloseStopVerdict(ws string, loopIDRaw, depth any, confidence int,
	reasoning string, o EscalationOptions) {

	if !pyval.Truthy(loopIDRaw) {
		return
	}
	// Composed RAW here; the one clip happens after the [refines: …] note
	// is appended. Clipping first and then appending strips the marker and
	// usually the note with it.
	evidence := fmt.Sprintf("director escalation close at depth %s (confidence %d/10): %s",
		pyval.Str(depth), confidence, reasoning)
	// Fallback if the metadata stamp fails; the owner below re-assigns
	// with the note included, clipped once at the same cap.
	rowEvidence := budget.Clip(evidence, 800)

	// `resolve_run_dir(loop_id)` is inside Python's try, and for a
	// non-string id it RAISES rather than missing: `run_dir()` builds
	// `f"{ref}-{nickname(ref)}"` and nickname calls `.encode`, so an
	// integer parent_job_id gives AttributeError and `_rd` is None. The
	// whole metadata half is skipped, and this port must skip it for the
	// same values — not find a run dir the Python never looked for.
	loopIDStr, loopIDIsStr := loopIDRaw.(string)
	if rd := ""; loopIDIsStr {
		rd = runs.ResolveRunDir(ws, loopIDStr)
		if rd != "" {
			// `int(depth)` is evaluated as an ARGUMENT to the stamp call,
			// so a non-numeric depth raises BEFORE anything is written and
			// the whole metadata half — the [refines: …] note included —
			// never happens. And for a numeric one, `2.0` is written as
			// `2` in the payload while the evidence string beside it says
			// "2.0", from the same variable. This is the one place `depth`
			// is NOT raw, which is precisely why it needed spelling out.
			depthInt, depthOK := pyInt(depth)
			if !depthOK {
				o.logf("close stop-verdict metadata stamp failed: %s",
					pyIntError(depth))
			} else {
				var evOut string
				_, err := runs.StampRunStopVerdict(runs.StopTupleOptions{
					StopVerdict:  "reachable-but-not-worth-it",
					StopEvidence: evidence,
					RunDir:       rd,
					RefineNote:   true,
					EvidenceOut:  &evOut,
					ReopenPayload: pyval.Obj{
						{Key: "kind", Val: "escalation-close"},
						{Key: "depth", Val: depthInt},
						{Key: "confidence", Val: confidence},
					},
				})
				if err != nil {
					o.logf("close stop-verdict metadata stamp failed: %v", err)
				} else if evOut != "" {
					// Python guards with `if _meta_path is not None and
					// _ev_out` then `_ev_out[0] or row_evidence` — an empty
					// written value keeps the fallback rather than clearing
					// the ledger row.
					rowEvidence = evOut
				}
			}
		}
	}

	// RAW: the ledger match is Python's `==`, so an integer id matches no
	// row at all rather than a row whose id decoded as the string "4242".
	if _, err := record.StampOutcomeStopVerdict(ws, loopIDRaw,
		"reachable-but-not-worth-it", rowEvidence); err != nil {
		o.logf("close stop-verdict outcome stamp failed: %v", err)
	}
}

// --- the operator artifact ----------------------------------------------

// writeEscalationSummary writes the human-readable record of a close or a
// surface. Best-effort: a failure here must not change the decision.
func writeEscalationSummary(ws string, jobIDRaw any, action string, depth any,
	reasoning, summaryForUser string, reason any, o EscalationOptions) {

	// `job_id[:8]` is a BARE slice inside the artifact's own try/except:
	// a non-string job_id raises there, so CPython writes no artifact and
	// logs a warning. Everything after this block still runs.
	short, sliceable := pySliceHead(jobIDRaw, 8)
	if !sliceable {
		o.logf("escalation %s: failed to write summary: job_id is not a string", action)
		return
	}
	jobID := pyval.Str(jobIDRaw)
	artDir := filepath.Join(orch.ProjectDir(ws, "escalation-"+short), "artifacts")
	if err := os.MkdirAll(artDir, record.NewDirMode); err != nil {
		o.logf("escalation %s: failed to write summary: %v", action, err)
		return
	}
	path := filepath.Join(artDir, fmt.Sprintf("escalation-%s-%s.md", short, action))
	body := fmt.Sprintf(
		"# Escalation %s — %s\n\n"+
			"**Depth:** %s\n"+
			"**Action:** %s\n"+
			"**Reasoning:** %s\n\n"+
			"## Summary for operator\n%s\n\n"+
			"## Full escalation context\n%s\n",
		titleCase(action), jobID, pyval.Str(depth), action, reasoning, summaryForUser, pyval.Str(reason))
	if err := record.AtomicWrite(path, []byte(body)); err != nil {
		o.logf("escalation %s: failed to write summary: %v", action, err)
		return
	}
	o.logf("escalation_%s: wrote summary to %s", action, path)
}

// --- the calibration row -------------------------------------------------

// logCalibration appends one escalation_decision row to
// memory/calibration.jsonl. Non-fatal by contract.
func logCalibration(ws string, jobIDRaw any, depth any, action, decisionClass string,
	confidence int, o EscalationOptions) {

	dir := orch.MemoryDir(ws)
	if err := os.MkdirAll(dir, record.NewDirMode); err != nil {
		o.logf("calibration log failed (non-fatal): %v", err)
		return
	}
	row := pyval.Obj{
		// time.time() is a FLOAT of seconds, not the ISO string every
		// other store in this port writes. Spelling it as a string here
		// would make a row Python's readers cannot compare against their
		// own (adversarial-prone: both are "a timestamp").
		{Key: "ts", Val: float64(time.Now().UnixNano()) / 1e9},
		{Key: "event", Val: "escalation_decision"},
		// RAW, like every other value in Python's dict literal here — a
		// task carrying a non-string job_id writes it as whatever it is.
		{Key: "job_id", Val: pyval.FromPlain(jobIDRaw)},
		{Key: "depth", Val: pyval.FromPlain(depth)},
		{Key: "action", Val: action},
		{Key: "decision_class", Val: decisionClass},
		{Key: "confidence", Val: confidence},
	}
	line, err := pyval.DumpsCompactPy(row)
	if err != nil {
		o.logf("calibration log failed (non-fatal): %v", err)
		return
	}
	if err := record.AppendRawLine(filepath.Join(dir, "calibration.jsonl"), []byte(line)); err != nil {
		o.logf("calibration log failed (non-fatal): %v", err)
	}
}

// --- small helpers -------------------------------------------------------

// taskGet is `d.get(key, default)` over an ordered task object: the RAW
// value when the key is present (including a JSON null, which Python's
// .get also returns as None), and the default only when it is absent.
func taskGet(o pyval.Obj, key string, def any) any {
	if v, ok := o.Get(key); ok {
		return v
	}
	return def
}

func dataGet(d pyval.Obj, key string, def any) any {
	if v, ok := d.Get(key); ok {
		return v
	}
	return def
}

// pyIntOr is `try: int(v) except (TypeError, ValueError): def`.
//
// It is NOT pyval.IntOf, which is int() after a .get with a numeric
// default and answers 0 for everything it cannot read. The difference is
// visible on exactly the values a model reply produces: int("7") is 7 on
// both sides, but int("high") raises and must fall back to the CALLER's
// default of 5, where IntOf would silently answer 0 — and 0 then clamps
// to 1, i.e. maximum uncertainty, from a reply that said nothing about
// confidence at all.
func pyIntOr(v any, def int) int {
	if n, ok := pyInt(v); ok {
		return n
	}
	return def
}

// pyInt is bare `int(v)`: the value, or false where CPython raises.
//
// The two spellings are not interchangeable and both are live in this file.
// `confidence` wraps int() in its own try with a default of 5, so it wants
// pyIntOr. `checkin_number` does NOT — its int() is bare, and the raise
// travels to the blanket except around the whole check-in, which means no
// notification at all rather than a notification carrying a substituted
// number.
func pyInt(v any) (int, bool) {
	switch t := v.(type) {
	case nil:
		return 0, false // int(None) raises TypeError
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	case int:
		return t, true
	case float64:
		if t != t || t > 1e18 || t < -1e18 {
			// int(nan) and int(inf) raise; a magnitude past int64 would
			// be an arbitrary-precision int in CPython, which this port
			// does not carry (named residual, shared with pyval.Plain).
			return 0, false
		}
		return int(t), true
	case json.Number:
		if i, err := t.Int64(); err == nil {
			return int(i), true
		}
		if f, err := t.Float64(); err == nil {
			return int(f), true
		}
		return 0, false
	case string:
		return pyIntFromString(t)
	}
	return 0, false
}

// pyIntFromString is int(str): surrounding whitespace is stripped, an
// optional sign is allowed, PEP 515 underscores may separate digits — and
// a decimal point or an exponent is a ValueError, unlike float(). A model
// that answers "7" is read as 7; one that answers "7.5" or "high" is not
// read at all, and the caller's default stands.
func pyIntFromString(s string) (int, bool) {
	s = pytext.Strip(s)
	if s == "" {
		return 0, false
	}
	i := 0
	neg := false
	if s[0] == '+' || s[0] == '-' {
		neg = s[0] == '-'
		i++
	}
	digits := 0
	n := 0
	prevUnderscore := false
	for ; i < len(s); i++ {
		c := s[i]
		switch {
		case c == '_':
			// An underscore must sit BETWEEN digits: leading, trailing and
			// doubled underscores are all ValueError in CPython.
			if digits == 0 || prevUnderscore {
				return 0, false
			}
			prevUnderscore = true
		case c >= '0' && c <= '9':
			prevUnderscore = false
			digits++
			if n > (1<<62)/10 {
				// Past what this port carries. CPython would keep going
				// with an arbitrary-precision int; refusing is the safe
				// direction here, since the caller clamps to 1..10 anyway.
				return 0, false
			}
			n = n*10 + int(c-'0')
		default:
			return 0, false
		}
	}
	if digits == 0 || prevUnderscore {
		return 0, false
	}
	if neg {
		n = -n
	}
	return n, true
}

// pySliceHead is `value[:n]` on a RAW value: it succeeds only for a
// string, because Python raises TypeError slicing an int, a dict or None.
// The bool reports whether the slice would have raised — which matters at
// the one call site that has it, where the raise is caught by a blanket
// except that swallows the whole event write.
func pySliceHead(v any, n int) (string, bool) {
	s, ok := v.(string)
	if !ok {
		return "", false
	}
	return pyval.Clip(s, n), true
}

// pyIntError is the message CPython's `int(v)` raises with, for the two
// places this file logs a failed conversion.
//
// It is a LOG line, not a store, so the text is not load-bearing the way
// pyops' messages are. It is written out anyway because the alternative —
// a Go-shaped "cannot convert" — reads as a port bug to whoever greps the
// two runtimes' logs side by side, which is the exact comparison this
// port's debugging depends on.
func pyIntError(v any) string {
	if s, ok := v.(string); ok {
		return fmt.Sprintf("invalid literal for int() with base 10: %s",
			pyval.Repr(s))
	}
	if f, ok := v.(float64); ok {
		if f != f {
			return "cannot convert float NaN to integer"
		}
		return "cannot convert float infinity to integer"
	}
	return fmt.Sprintf("int() argument must be a string, a bytes-like object "+
		"or a real number, not '%s'", pyval.TypeName(v))
}
