package run

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/judgment"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// The engine's judges, as judgment Requests (§6 through the §4 seam). A
// judge asks ONE typed question — `outcome`, a choice over the kind's
// vocabulary — about a state made of the goal, what was done, and what
// came back. The same Request goes to whichever provider is configured:
// `llm` renders it as prose and parses the reply strictly, `jev` and
// `pcd` post it as the System One wire body. Nothing about the verdict
// that results depends on which arm answered.

// QOutcome is the question id every judge asks under.
const QOutcome = "outcome"

// stepOptions / closureOptions are the two vocabularies, with the
// descriptions the answerer is given. The option ORDER is part of the
// request bytes; it is the vocabulary's order, never a map's.
func stepOptions() []judgment.Option {
	return []judgment.Option{
		{Name: string(StepDoneOK), Description: "this step's own part is done"},
		{Name: string(StepBlocked), Description: "this step could not be done and the plan cannot continue past it"},
		{Name: string(StepUnclear), Description: "the result does not say whether the step is done"},
	}
}

func closureOptions() []judgment.Option {
	return []judgment.Option{
		{Name: "achieved", Description: "the goal was achieved"},
		{Name: "not_achieved", Description: "the goal was not achieved"},
		{Name: "unknown", Description: "the material shown does not say either way"},
	}
}

const stepInstructions = "Given the goal, one planned step, the executor's result for that step, and the recorded evidence of what the executor actually did, decide whether THIS STEP is done. " +
	"The result is the executor's own report; the evidence is the execution record (the tools it ran and what they returned). A report the evidence contradicts, or that claims work the evidence does not show, is not done. " +
	"Judge only this step: later steps of the plan handle the rest of the goal, and a step that does its own part is done even when the goal is not yet complete."

const closureInstructions = "Given the goal, every step's result, and each step's recorded evidence of what the executor actually did, decide whether the GOAL was achieved — not whether work happened. " +
	"A result the evidence contradicts, or that claims work the evidence does not show, does not count toward the goal."

// StepJudgeRequest is the per-step judge's question about one step.
func StepJudgeRequest(model string, goal []byte, step string, result []byte, terminal invoke.TerminalState, fork bool, evidence string) judgment.Request {
	var notes []string
	if terminal == invoke.TerminalPartial {
		notes = append(notes, "the executor's stream ended PARTIAL — the result may be truncated")
	}
	if fork {
		notes = append(notes, "this step ran its sub-goals in parallel; the result lists each member's whole answer under a '### Member' heading, and the step is done when the sub-goals were answered")
	}
	if evidence == "" {
		evidence = invoke.EvidenceUnavailable
	}
	state := judgment.Sect("goal", string(goal), "step", step, "result", string(result), "evidence", evidence)
	if len(notes) > 0 {
		state.Sections = append(state.Sections, judgment.Section{Key: "notes", Text: strings.Join(notes, "\n")})
	}
	return judgment.Ask1(model, state, QOutcome, judgment.Question{Type: judgment.Choice, Instructions: stepInstructions, Options: stepOptions()})
}

// ClosureJudgeRequest is the closure judge's question about the run. It
// asks for falsifiers: the arm that can name them does, and the verdict
// carries them exactly as before.
func ClosureJudgeRequest(model string, goal []byte, steps []string, results [][]byte, partial []bool, evidence []string) judgment.Request {
	state := judgment.Sect("goal", string(goal))
	for i, s := range steps {
		note := ""
		if i < len(partial) && partial[i] {
			note = "\n(the executor's stream ended PARTIAL; this result may be truncated)"
		}
		res := ""
		if i < len(results) {
			res = string(results[i])
		}
		ev := invoke.EvidenceUnavailable
		if i < len(evidence) && evidence[i] != "" {
			ev = evidence[i]
		}
		state.Sections = append(state.Sections,
			judgment.Section{Key: fmt.Sprintf("step %d", i+1), Text: s},
			judgment.Section{Key: fmt.Sprintf("result %d", i+1), Text: res + note},
			judgment.Section{Key: fmt.Sprintf("evidence %d", i+1), Text: ev})
	}
	return judgment.Ask1(model, state, QOutcome, judgment.Question{Type: judgment.Choice, Instructions: closureInstructions, Options: closureOptions(), Falsifiers: true})
}

// JudgmentResult turns a provider's answer into the judge boundary's
// product. A choice outside the kind's vocabulary, a confidence out of
// range, or a missing why is refused by the judgment package before it
// gets here; this is the last shape check.
func JudgmentResult(res judgment.Response) (JudgeResult, error) {
	a, err := res.One()
	if err != nil {
		return JudgeResult{}, fmt.Errorf("%w: %v", ErrBoundary, err)
	}
	if a.Type != judgment.Choice {
		return JudgeResult{}, fmt.Errorf("%w: a judge answer is a choice, not a %s", ErrBoundary, a.Type)
	}
	return JudgeResult{Outcome: a.Choice, Confidence: a.Confidence, Why: a.Why, Falsifiers: a.Falsifiers}, nil
}

// modelOf is the model name a request carries: the provider's, or the
// provider name when the backend does not name a model. Deterministic,
// because the fold re-derives the request from the recorded snapshot.
func modelOf(caps invoke.Capabilities, name string) string {
	if strings.TrimSpace(caps.Model) == "" {
		return name
	}
	return caps.Model
}

// primary is the attempt's primary judgment provider. `llm` (the
// default) wraps whichever backend the attempt's policy chose to judge
// on, so the existing judge is a provider like any other.
func (d *Driver) primary(a *AttemptState) (judgment.Provider, error) {
	return d.provider(a.Attempt.Config.Judgment, a)
}

func (d *Driver) provider(name string, a *AttemptState) (judgment.Provider, error) {
	if name == "" || name == judgment.ProviderLLM {
		return &judgment.LLM{B: d.judge(a)}, nil
	}
	p := d.Providers[name]
	if p == nil {
		return nil, fmt.Errorf("%w: no judgment provider %q is wired (known: %v)", ErrConfig, name, judgment.Known())
	}
	return p, nil
}

// judgmentBinding is the attempt's recorded judgment arm: the provider
// name its judges asked through, and the capabilities of the backend
// they ran on. It is read from the CONFIG, never from a live driver
// field, so the driver and the fold derive the same bytes from the same
// record.
func judgmentBinding(cfg ConfigSnapshot) (string, invoke.Capabilities) {
	caps := cfg.Backend
	if cfg.Judge == JudgeModel && cfg.JudgeBackend.Name != "" {
		caps = cfg.JudgeBackend
	}
	name := cfg.Judgment
	if name == "" {
		name = judgment.ProviderLLM
	}
	if cfg.JudgmentBackend != nil {
		caps = *cfg.JudgmentBackend
	}
	return name, caps
}

// RenderJudgeRequest builds a judgment request under an attempt's
// binding and renders it to the exact bytes the judge is asked: the wire
// body for a System One provider, the versioned prose template for the
// llm arm. The driver asks with these bytes and the fold re-derives them
// through this same function — one renderer, no second spelling.
func RenderJudgeRequest(cfg ConfigSnapshot, build func(model string) judgment.Request) (judgment.Request, []byte, error) {
	name, caps := judgmentBinding(cfg)
	req := build(modelOf(caps, name))
	if judgment.IsWire(name) {
		b, err := judgment.EncodeRequest(req)
		return req, b, err
	}
	b, err := judgment.RenderPrompt(req)
	return req, b, err
}

// ParseJudgeResponse reads a judge's answer under an attempt's binding.
// A refusal is a refusal: the caller records `unjudged`, never a guess.
func ParseJudgeResponse(cfg ConfigSnapshot, req judgment.Request, resp []byte) (JudgeResult, error) {
	name, caps := judgmentBinding(cfg)
	var res judgment.Response
	var err error
	if judgment.IsWire(name) {
		res, err = judgment.DecodeResponse(resp)
	} else {
		res, err = judgment.ParseAnswers(resp, modelOf(caps, name))
	}
	if err != nil {
		return JudgeResult{}, fmt.Errorf("%w: %v", ErrBoundary, err)
	}
	if err := res.Validate(req); err != nil {
		return JudgeResult{}, fmt.Errorf("%w: %v", ErrBoundary, err)
	}
	return JudgmentResult(res)
}

// judgeRequest renders a judgment request for the attempt's primary
// provider: the prompt bytes that are recorded and that the fold
// re-derives.
func (d *Driver) judgeRequest(a *AttemptState, build func(model string) judgment.Request) (judgment.Request, []byte, error) {
	return RenderJudgeRequest(a.Attempt.Config, build)
}

// judgeAnswer parses a primary judge response into the boundary product.
func (d *Driver) judgeAnswer(a *AttemptState, req judgment.Request, resp []byte) (JudgeResult, error) {
	return ParseJudgeResponse(a.Attempt.Config, req, resp)
}

// shadow asks every configured shadow provider the SAME request the
// primary was asked and commits each answer as a shadow_judgment. It can
// never change the run: the record is a control record the resolver
// cannot read, the call's usage is not the goal's, and every provider
// failure is recorded and stepped over. Only a journal failure stops it.
func (d *Driver) shadow(ctx context.Context, rs *RunState, a *AttemptState, v *verdict.Verdict, req judgment.Request) error {
	names := a.Attempt.Config.Shadow
	if len(names) == 0 || v == nil {
		return nil
	}
	n := a.Attempt.Attempt
	for _, name := range names {
		p, err := d.provider(name, a)
		if err != nil {
			d.emit(rs, n, "shadow_unavailable", Executing, err.Error())
			continue
		}
		// the same question, asked in the shadow's own name: a provider
		// answers as itself, and a wire provider refuses a model it does
		// not serve (jev: HTTP 400 "Unknown model: sonnet", seen live).
		ask := req
		ask.Model = modelOf(p.Capabilities(), name)
		sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: n}
		start := time.Now()
		// a shadow is measurement: it gets the judgment budget (the
		// registered judgment.timeout), never the executor's 20 minutes
		res, o, err := judgment.Ask(ctx, sh, p, invoke.PurposeShadowJudge, ask, judgment.DefaultTimeout)
		latency := time.Since(start).Milliseconds()
		sj := &judgment.ShadowJudgment{
			Header:        header(v.Subject, rs.Run, n, "shadow_judgment/1"),
			Provider:      name,
			Primary:       v.ID,
			Question:      QOutcome,
			LatencyMillis: latency,
		}
		if o != nil {
			sj.Invocation, sj.Usage = o.Invocation, o.Usage
		}
		if err != nil {
			sj.Failed, sj.Reason = true, err.Error()
			d.emit(rs, n, "shadow_failed", Executing, fmt.Sprintf("%s: %v", name, err))
		} else if ans, aerr := res.One(); aerr != nil {
			sj.Failed, sj.Reason = true, aerr.Error()
		} else {
			sj.Answer = &ans
		}
		if err := d.commit(ctx, fmt.Sprintf("shadow/%s/%d/%s/%s", rs.Run, n, v.ID, name), sj); err != nil {
			return err
		}
		if sj.Answer != nil {
			d.emit(rs, n, "shadow", Executing, fmt.Sprintf("%s %s conf %.2f vs %s", name, sj.Answer.Choice, sj.Answer.Confidence, v.Outcome))
		}
	}
	return nil
}

// asideOfTheGoal names the purposes whose cost is not the goal's: the
// tail's diagnosis, the experiment evaluator's score, and the shadow
// arm's measurement. They land beside a run, never inside its outcome.
func asideOfTheGoal(p invoke.Purpose) bool {
	return p == invoke.PurposeDiagnose || p == invoke.PurposeEvaluate || p == invoke.PurposeShadowJudge
}
