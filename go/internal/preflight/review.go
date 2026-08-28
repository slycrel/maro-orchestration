package preflight

import (
	"errors"
	"fmt"
	"io"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// reviewSystem is `_REVIEW_SYSTEM`, copied byte-for-byte out of the Python
// rather than retyped, and compared against the Python constant by the
// differential. Prompt text is a CONTENT KEY: the recurring bug family in
// this port is a piece of prose that diverges by a character nobody reads,
// and a critic prompt that has quietly lost its fifth dimension still
// returns valid-looking JSON forever.
//
// Python builds it with `textwrap.dedent(...).strip()`, so what is stored
// is the dedented body with no leading or trailing whitespace. That is
// what is written out here — the dedent is not a runtime step in the port,
// because its input is a literal and its output cannot change.
const reviewSystem = `You are a plan critic. A planning agent has decomposed a goal into steps.
Your job: find what's wrong BEFORE execution wastes budget on it.

Assess the plan on four dimensions:

1. SCOPE: Does the step count reflect the true size of the work?
   - "narrow": goal is simple, plan looks complete (3-5 steps, no hidden depth)
   - "medium": goal is moderate, plan looks roughly right (6-12 steps)
   - "wide": plan is likely incomplete — the goal is bigger than it looks,
     or key sub-problems are bundled into single steps that will explode
   - Flag "wide" when you see: "read all X", "analyze the entire Y",
     any step that would require knowing things we haven't discovered yet.

2. ASSUMPTIONS: What does this plan assume that could be wrong?
   Especially: steps that depend on prior steps producing specific output,
   steps that assume access/credentials/state that isn't guaranteed,
   steps that assume the goal is well-specified when it might not be.

3. MILESTONE CANDIDATES: Which steps look like sub-goals that need their
   own planning pass? Flag any step that is really "run a whole project"
   in disguise — these should be sub-loops, not single steps.

4. UNKNOWN UNKNOWNS: What does this plan not know that it should?
   Things the agent will discover mid-execution that will require replanning.

5. CLASS COVERAGE: When the goal names a CLASS of targets ("every X",
   "all writers of Y", "wherever Z happens") but the steps touch one
   or two named members, flag it — name the class and the kind of
   member the plan never enumerates. A plan that starts with an
   enumeration/census step, or a goal about one specific target, is
   fine (emit nothing). This is the instance-vs-class failure: fixing
   the member you happened to look at while siblings keep the defect.

Be terse. One sentence per flag. Don't pad.

Respond ONLY with this JSON structure (no prose, no markdown):
{
  "scope": "narrow" | "medium" | "wide",
  "scope_note": "<one sentence explanation>",
  "assumptions": [{"step": <1-based int or 0 for whole plan>, "issue": "<string>"}],
  "milestone_candidates": [{"step": <1-based int>, "reason": "<string>"}],
  "unknown_unknowns": ["<string>", ...],
  "class_gaps": [{"step": <1-based int or 0 for whole plan>, "issue": "<string>"}]
}`

// Message is llm.LLMMessage, which this file only ever constructs.
type Message struct {
	Role    string
	Content string
}

// Reviewer is one candidate yielded by `_build_reviewers`: a name for the
// log line, and the call that may or may not answer.
type Reviewer struct {
	Name string
	// Complete is `_reviewer.complete(messages, **kw)` FOLLOWED BY the
	// `resp.content` access, folded into one call because Python's inner
	// `except Exception` covers both and cannot tell them apart. It
	// returns the content attribute; an error is either the call raising
	// or the response having no such attribute.
	Complete func(messages []Message, kw pyval.Obj) (content any, err error)
}

// ReviewDeps is everything ReviewPlan reaches outside itself.
type ReviewDeps struct {
	// ImportLLMMessage is `from llm import LLMMessage`, which sits INSIDE
	// the try — so a failure here lands on the outer handler and comes
	// back as a heuristic estimate carrying the exception in its note,
	// not as an import error the caller sees.
	ImportLLMMessage func() error
	// NextReviewer is `_build_reviewers()` as CPython iterates it: one
	// candidate at a time. The laziness is load-bearing and is why this is
	// not a slice — the generator does not build the paid adapter until
	// the free one has failed, and a generator that raises on its third
	// yield raises only after two calls have already been made.
	NextReviewer func() (Reviewer, bool, error)
	Log          Logger
	// Stderr receives the `verbose` prints. Python writes them with
	// flush=True, one print per line.
	Stderr io.Writer
}

// ReviewPlan is `review_plan`.
//
// It never returns an error. When no reviewer answers — no keys, dead
// keys, garbled output — it degrades to the heuristic estimate rather
// than to "unknown", and that distinction is the whole reason the
// function has its current shape: a dead OPENROUTER_API_KEY that BUILT
// fine and failed every call returned scope="unknown" for months, and
// wrote 488 calibration entries with zero flags and zero milestone
// candidates.
//
// The `adapter` parameter of the Python signature is NOT here. It is
// dead: the body names it zero times, because `_build_reviewers` resolves
// its own candidates. Carrying it across would have been the port
// inventing a dependency the original does not have.
//
// DIVERGENCE, named not pinned: Python's outer handler wraps its
// `_heuristic_scope` call in a second try and answers scope="unknown" if
// THAT raises. Reaching it needs a `steps` that is not a list of strings —
// `" ".join(steps)` is the only statement in there that can fail — which
// the Python signature says cannot happen and the Go signature enforces.
// The branch is unreachable here because []string is not List[str]; it is
// omitted rather than faked, and this comment is the record.
func ReviewPlan(goal string, steps []string, verbose bool, d ReviewDeps) Review {
	if len(steps) == 0 {
		return Review{Scope: "unknown", ScopeNote: "no steps to review",
			Flags: []Flag{}, MilestoneStepIndices: []int{}}
	}

	rev, err := reviewBody(goal, steps, verbose, d)
	if err == nil {
		return *rev
	}
	// `except Exception` — the review is advisory, so nothing above it
	// gets to fail the loop.
	d.Log.Debug("pre_flight review failed (non-blocking): %s", err.Error())
	return Review{
		Scope:     HeuristicScope(steps),
		ScopeNote: fmt.Sprintf("heuristic estimate (review failed: %s)", err.Error()),
		Flags:     []Flag{}, MilestoneStepIndices: []int{},
	}
}

// reviewBody is the try block; a non-nil error is an exception reaching
// the outer handler.
func reviewBody(goal string, steps []string, verbose bool,
	d ReviewDeps) (*Review, error) {
	if err := d.ImportLLMMessage(); err != nil {
		return nil, err
	}

	numbered := make([]string, len(steps))
	for i, s := range steps {
		numbered[i] = fmt.Sprintf("%d. %s", i+1, s)
	}
	stepsText := strings.Join(numbered, "\n")
	userMsg := "Goal: " + goal + "\n\nProposed plan:\n" + stepsText
	messages := []Message{
		{Role: "system", Content: reviewSystem},
		{Role: "user", Content: userMsg},
	}

	var review *Review
	for {
		r, ok, err := d.NextReviewer()
		if err != nil {
			// The generator raised. This is NOT the inner handler's
			// business — that one only wraps the call — so it goes
			// straight to the outer one and the answer is a heuristic
			// estimate naming the exception.
			return nil, err
		}
		if !ok {
			break
		}
		// The keyword order is the call's, and it is observable: the
		// probe records the kwargs the reviewer was handed.
		content, cerr := r.Complete(messages, pyval.Obj{
			{Key: "max_tokens", Val: 512},
			{Key: "temperature", Val: 0.1},
			{Key: "timeout", Val: 30},
			{Key: "no_tools", Val: true},
			{Key: "purpose", Val: "plan review"},
		})
		if cerr == nil {
			var text string
			// `(resp.content or "").strip()` is INSIDE the same try as
			// the call, so its failures are the call's failures as far as
			// this loop can tell.
			text, cerr = contentText(content)
			if cerr == nil {
				review, _ = ParseReview(text, d.Log)
			}
		}
		if cerr != nil {
			d.Log.Info("pre_flight: reviewer %s failed at call time (%s); "+
				"trying next", r.Name, cerr.Error())
			// EQUIVALENT MUTANT (kept, marked `equivalent`): this reset
			// can never have work to do. The loop breaks the moment
			// `review` is non-nil, so it is nil at the top of every
			// iteration. It is here because it is in the original, and
			// the original is right to write it — the invariant it
			// depends on is one `break` away from being false.
			review = nil
		}
		if review != nil {
			break
		}
	}

	if review == nil {
		// No reviewer answered usably. Heuristic beats "unknown": the
		// scope signal survives even when milestone detection cannot.
		scope := HeuristicScope(steps)
		d.Log.Info("pre_flight: no working reviewer, heuristic scope "+
			"estimate: %s", scope)
		return &Review{
			Scope:     scope,
			ScopeNote: "heuristic estimate (no working reviewer for LLM review)",
			Flags:     []Flag{}, MilestoneStepIndices: []int{},
		}, nil
	}

	level := "INFO"
	if review.HasConcerns() {
		level = "WARNING"
	}
	// Python passes format_for_log() as the whole message with no args, so
	// logging never %-formats it and a percent sign in a reviewer's prose
	// reaches the handler intact.
	d.Log.Log(level, "%s", review.FormatForLog())
	if verbose {
		fmt.Fprintf(d.Stderr, "[maro] pre-flight: %s\n", review.Summary())
		if review.Scope == "wide" {
			fmt.Fprintf(d.Stderr, "[maro] pre-flight: scope WARNING — %s\n",
				pyval.Str(review.ScopeNote))
		}
		for _, f := range review.Flags {
			if f.Severity != "warn" {
				continue
			}
			stepStr := "plan"
			if f.Step != 0 {
				stepStr = fmt.Sprintf("step %d", f.Step)
			}
			fmt.Fprintf(d.Stderr, "[maro] pre-flight [%s] %s: %s\n",
				f.Kind, stepStr, pyval.Str(f.Message))
		}
	}
	return review, nil
}

// contentText is `(resp.content or "").strip()`.
//
// The `or` is a truthiness test, so None, "" and 0 all become the empty
// string. What survives it is whatever the reviewer put there, and
// `.strip()` is a STRING method — a truthy non-string content is an
// AttributeError, not a coercion, and lands on the same handler as a
// reviewer that never answered.
func contentText(content any) (string, error) {
	if !pyval.Truthy(content) {
		return "", nil
	}
	s, ok := content.(string)
	if !ok {
		return "", attrErr(content, "strip")
	}
	return pytext.Strip(s), nil
}

// errNoMemoryDir keeps the stats function's one non-exception failure
// legible; see CalibrationStats.
var errNoMemoryDir = errors.New("cannot locate memory_dir")
