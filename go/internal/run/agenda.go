package run

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// AGENDA is the second configuration of the one driver (§5): plan
// cardinality > 1 and a model judge. Its interpretation boundaries —
// Intent, Plan, Judge — are typed functions from a response thought to a
// process artifact, validated ONCE here; a model call is such a boundary
// and owns the error translation for what it produces (§1b).

const (
	LaneAgenda Lane           = "agenda"
	JudgeModel JudgeSelection = "model"

	KindIntentAssessment record.Kind = "intent_assessment"
	KindPlan             record.Kind = "plan"
	KindStepDone         record.Kind = "step_done"
)

func init() {
	lanes[LaneAgenda] = true
	judges[JudgeModel] = true
}

// IntentAssessment is the model's interpretation of the goal, made AFTER
// the treatment-blind family assessment and never revising it (§5).
type IntentAssessment struct {
	record.ProductionRecord
	record.Header  `json:"header"`
	Invocation     record.RecordID `json:"invocation"`
	Clear          bool            `json:"clear"`
	Interpretation string          `json:"interpretation,omitempty"`
	Question       string          `json:"question,omitempty"` // what the model needs to know, when not clear
}

func (r *IntentAssessment) Head() *record.Header { return &r.Header }
func (r *IntentAssessment) Kind() record.Kind    { return KindIntentAssessment }
func (r *IntentAssessment) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("intent_assessment: %w", err)
	}
	if err := record.ValidateID(r.Invocation); err != nil {
		return fmt.Errorf("intent_assessment: invocation: %w", err)
	}
	if r.Clear && strings.TrimSpace(r.Interpretation) == "" {
		return errors.New("intent_assessment: a clear goal carries its interpretation")
	}
	if !r.Clear && strings.TrimSpace(r.Question) == "" {
		return errors.New("intent_assessment: an unclear goal carries the question")
	}
	return nil
}

// Plan is the model's decomposition: each step's text is a whole `step`
// thought. Cardinality is what the model produced (≥ 1); nothing caps it.
type Plan struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Invocation    record.RecordID `json:"invocation"`
	Steps         []thought.Ref   `json:"steps"`
	Parallel      []ParallelStep  `json:"parallel,omitempty"` // steps that fork: their sub-goals and join policy
	Edges         []StepEdge      `json:"edges,omitempty"`    // declared prerequisites (plan/2): step k is executed only when every step it names ended done
}

// StepEdge is a prerequisite the PLANNER declared: step Ordinal is executed
// only when every step in After ended `done`. The plan's own edges are an
// execution contract, not a hint (LoopsBench item 1): sequential order
// stays soft (an unclear step does not stop the next one), a declared edge
// is hard (a step whose prerequisite did not end done is gated — recorded,
// not executed, and its dependents gate in turn).
type StepEdge struct {
	Ordinal int   `json:"ordinal"`
	After   []int `json:"after"` // ascending, distinct, each an earlier step
}

// EdgesAt returns step k's declared prerequisites (nil without any).
func (r *Plan) EdgesAt(k int) []int {
	for i := range r.Edges {
		if r.Edges[i].Ordinal == k {
			return r.Edges[i].After
		}
	}
	return nil
}

// planAfter lists every step's declared prerequisites by index (nil for a
// step without any) — the shape the prompts render.
func planAfter(plan *Plan) [][]int {
	out := make([][]int, len(plan.Steps))
	for _, e := range plan.Edges { // one pass: a plan's cardinality is uncapped
		if e.Ordinal >= 1 && e.Ordinal <= len(out) {
			out[e.Ordinal-1] = e.After
		}
	}
	return out
}

// validAfter checks a prerequisite list for step k: ascending, distinct,
// each an earlier step.
func validAfter(k int, after []int) error {
	for i, j := range after {
		if j < 1 || j >= k {
			return fmt.Errorf("step %d cannot follow step %d (a prerequisite is an earlier step)", k, j)
		}
		if i > 0 && j <= after[i-1] {
			return fmt.Errorf("step %d prerequisites must be ascending and distinct", k)
		}
	}
	return nil
}

// ParallelAt returns the parallel spec of step k, if it forks.
func (r *Plan) ParallelAt(k int) *ParallelStep {
	for i := range r.Parallel {
		if r.Parallel[i].Ordinal == k {
			return &r.Parallel[i]
		}
	}
	return nil
}

func (r *Plan) Head() *record.Header { return &r.Header }
func (r *Plan) Kind() record.Kind    { return KindPlan }
func (r *Plan) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("plan: %w", err)
	}
	if err := record.ValidateID(r.Invocation); err != nil {
		return fmt.Errorf("plan: invocation: %w", err)
	}
	if len(r.Steps) == 0 {
		return errors.New("plan: needs at least one step")
	}
	for i, s := range r.Steps {
		if err := s.Validate(); err != nil {
			return fmt.Errorf("plan: step %d: %w", i+1, err)
		}
		if s.Kind != thought.Step || s.Bytes == 0 {
			return fmt.Errorf("plan: step %d must be a non-empty step thought", i+1)
		}
	}
	seen := map[int]bool{}
	for _, p := range r.Parallel {
		if p.Ordinal < 1 || p.Ordinal > len(r.Steps) || seen[p.Ordinal] {
			return fmt.Errorf("plan: parallel step %d is not a distinct step of the plan", p.Ordinal)
		}
		seen[p.Ordinal] = true
		if len(p.Goals) < 2 || !joinPolicies[p.Policy] {
			return fmt.Errorf("plan: parallel step %d needs two or more sub-goals and a registered join policy", p.Ordinal)
		}
		for j, g := range p.Goals {
			if err := g.Validate(); err != nil || g.Kind != thought.Step || g.Bytes == 0 {
				return fmt.Errorf("plan: parallel step %d sub-goal %d must be a non-empty step thought", p.Ordinal, j+1)
			}
		}
	}
	edged := map[int]bool{}
	for _, e := range r.Edges {
		if e.Ordinal < 1 || e.Ordinal > len(r.Steps) || edged[e.Ordinal] {
			return fmt.Errorf("plan: edge for step %d is not a distinct step of the plan", e.Ordinal)
		}
		edged[e.Ordinal] = true
		if len(e.After) == 0 {
			return fmt.Errorf("plan: edge for step %d names no prerequisite", e.Ordinal)
		}
		if err := validAfter(e.Ordinal, e.After); err != nil {
			return fmt.Errorf("plan: %w", err)
		}
	}
	return nil
}

// StepOutcome is the per-step verdict vocabulary as the driver acts on it:
// the judge's `done | blocked | unclear`, or `unjudged` when no verdict
// could be made (a judge output the boundary refused).
type StepOutcome string

const (
	StepDoneOK   StepOutcome = "done"
	StepBlocked  StepOutcome = "blocked"
	StepUnclear  StepOutcome = "unclear"
	StepUnjudged StepOutcome = "unjudged"
	StepGated    StepOutcome = "gated" // a declared prerequisite did not end done: not executed, not judged (step_done/2)
)

var stepOutcomes = map[StepOutcome]bool{StepDoneOK: true, StepBlocked: true, StepUnclear: true, StepUnjudged: true, StepGated: true}

// StepDone is one settled step: its invocation, its whole result, and
// the judge's verdict on it (when one was made) — or, for a gated step,
// neither invocation nor verdict: the prerequisites that did not end done
// and a result that says so. The driver continues after
// done/unclear/unjudged/gated and stops at blocked.
type StepDone struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Ordinal       int                  `json:"ordinal"`
	Step          thought.Ref          `json:"step"`
	Invocation    record.RecordID      `json:"invocation,omitempty"` // the execute call; absent for a fork or gated step
	Fork          record.RecordID      `json:"fork,omitempty"`       // the settled fork; absent for an executed or gated step
	Terminal      invoke.TerminalState `json:"terminal"`             // complete | partial — how the executor's stream ended (a failed one is never a done step; a gated step is complete)
	Result        thought.Ref          `json:"result"`
	Verdict       record.RecordID      `json:"verdict,omitempty"`
	Outcome       StepOutcome          `json:"outcome"`
	GatedBy       []int                `json:"gated_by,omitempty"` // outcome gated only: the declared prerequisites that did not end done
}

func (r *StepDone) Head() *record.Header { return &r.Header }
func (r *StepDone) Kind() record.Kind    { return KindStepDone }
func (r *StepDone) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if err := runScoped(&r.Header); err != nil {
		return fmt.Errorf("step_done: %w", err)
	}
	if r.Ordinal < 1 {
		return errors.New("step_done: ordinal starts at 1")
	}
	if r.Outcome == StepGated {
		if r.Invocation != "" || r.Fork != "" || r.Verdict != "" {
			return errors.New("step_done: a gated step has no invocation, fork or verdict — nothing ran")
		}
		if len(r.GatedBy) == 0 {
			return errors.New("step_done: a gated step names the prerequisites that gated it")
		}
		if err := validAfter(r.Ordinal, r.GatedBy); err != nil {
			return fmt.Errorf("step_done: gated_by: %w", err)
		}
	} else {
		if len(r.GatedBy) > 0 {
			return errors.New("step_done: only a gated step names gated_by")
		}
		if (r.Invocation == "") == (r.Fork == "") {
			return errors.New("step_done: exactly one of invocation (an executed step) or fork (a parallel step)")
		}
	}
	if r.Invocation != "" {
		if err := record.ValidateID(r.Invocation); err != nil {
			return fmt.Errorf("step_done: invocation: %w", err)
		}
	}
	if r.Fork != "" {
		if err := record.ValidateID(r.Fork); err != nil {
			return fmt.Errorf("step_done: fork: %w", err)
		}
	}
	if err := r.Step.Validate(); err != nil || r.Step.Kind != thought.Step {
		return errors.New("step_done: step must be a step thought")
	}
	if err := r.Result.Validate(); err != nil || r.Result.Kind != thought.Response {
		return errors.New("step_done: result must be a response thought")
	}
	if r.Terminal != invoke.TerminalComplete && r.Terminal != invoke.TerminalPartial {
		return fmt.Errorf("step_done: terminal %q is not complete|partial", r.Terminal)
	}
	if !stepOutcomes[r.Outcome] {
		return fmt.Errorf("step_done: outcome %q out of vocabulary", r.Outcome)
	}
	if r.Outcome != StepGated && (r.Outcome == StepUnjudged) != (r.Verdict == "") {
		return errors.New("step_done: unjudged has no verdict; every other outcome names one")
	}
	if r.Verdict != "" {
		if err := record.ValidateID(r.Verdict); err != nil {
			return fmt.Errorf("step_done: verdict: %w", err)
		}
	}
	return nil
}

// ---- interpretation boundaries: response thought → process artifact ----

// ErrBoundary is a model output the boundary refused: not a run failure by
// itself, but a typed refusal the driver turns into the honest next state.
var ErrBoundary = errors.New("run: model output refused at the boundary")

// unfence strips a ```json fence when the whole body is one.
func unfence(b []byte) []byte {
	s := strings.TrimSpace(string(b))
	if strings.HasPrefix(s, "```") {
		s = strings.TrimPrefix(s, "```json")
		s = strings.TrimPrefix(s, "```")
		s = strings.TrimSuffix(strings.TrimSpace(s), "```")
	}
	return []byte(strings.TrimSpace(s))
}

func decodeStrict(b []byte, into any) error {
	dec := json.NewDecoder(strings.NewReader(string(unfence(b))))
	dec.DisallowUnknownFields()
	if err := dec.Decode(into); err != nil {
		return fmt.Errorf("%w: %v", ErrBoundary, err)
	}
	if dec.More() {
		return fmt.Errorf("%w: trailing content after the JSON object", ErrBoundary)
	}
	return nil
}

// IntentResult is the Intent boundary's product.
type IntentResult struct {
	Clear          bool   `json:"clear"`
	Interpretation string `json:"interpretation"`
	Question       string `json:"question"`
}

// ParseIntent validates the intent response once.
func ParseIntent(response []byte) (IntentResult, error) {
	var r IntentResult
	if err := decodeStrict(response, &r); err != nil {
		return r, err
	}
	r.Interpretation, r.Question = strings.TrimSpace(r.Interpretation), strings.TrimSpace(r.Question)
	if r.Clear && r.Interpretation == "" {
		return r, fmt.Errorf("%w: clear without an interpretation", ErrBoundary)
	}
	if !r.Clear && r.Question == "" {
		return r, fmt.Errorf("%w: unclear without a question", ErrBoundary)
	}
	return r, nil
}

// PlannedStep is one parsed step: a text, or a parallel step (sub-goals
// run as child runs, joined under a policy) whose text is derived.
type PlannedStep struct {
	Text     string
	Parallel []string
	Policy   JoinPolicy
	After    []int // declared prerequisites (ascending, distinct, earlier steps); nil without any
}

// ParsePlan validates the plan response once: a non-empty list of steps,
// each a non-empty string, {"step": <text>, "after": [<earlier step numbers>]},
// or {"parallel": [<2+ sub-goals>], "join": "all"|"first_verdict"} (which
// may also carry "after").
func ParsePlan(response []byte) ([]PlannedStep, error) {
	var r struct {
		Steps []json.RawMessage `json:"steps"`
	}
	if err := decodeStrict(response, &r); err != nil {
		return nil, err
	}
	if len(r.Steps) == 0 {
		return nil, fmt.Errorf("%w: a plan needs at least one step", ErrBoundary)
	}
	var out []PlannedStep
	for i, raw := range r.Steps {
		var text string
		if err := json.Unmarshal(raw, &text); err == nil {
			text = strings.TrimSpace(text)
			if text == "" {
				return nil, fmt.Errorf("%w: step %d is empty", ErrBoundary, i+1)
			}
			out = append(out, PlannedStep{Text: text})
			continue
		}
		// an object is a text step XOR a parallel spec, by which key is
		// PRESENT (an empty list or text is present — refused as a conflict
		// or as empty, never read as the other shape; a JSON null is absent,
		// as encoding/json reads it)
		var par struct {
			Step     *string   `json:"step"`
			Parallel *[]string `json:"parallel"`
			Join     *string   `json:"join"`
			After    []int     `json:"after"`
		}
		if err := decodeStrict(raw, &par); err != nil {
			return nil, fmt.Errorf("%w: step %d is neither a text nor a parallel step: %v", ErrBoundary, i+1, err)
		}
		if err := validAfter(i+1, par.After); err != nil {
			return nil, fmt.Errorf("%w: %v", ErrBoundary, err)
		}
		if len(par.After) == 0 {
			par.After = nil
		}
		if par.Step != nil && (par.Parallel != nil || par.Join != nil) {
			return nil, fmt.Errorf("%w: step %d is both a step text and a parallel spec", ErrBoundary, i+1)
		}
		if par.Step == nil && par.Parallel == nil {
			return nil, fmt.Errorf("%w: step %d is neither a text nor a parallel step (one of step and parallel)", ErrBoundary, i+1)
		}
		if par.Step != nil {
			text := strings.TrimSpace(*par.Step)
			if text == "" {
				return nil, fmt.Errorf("%w: step %d is empty", ErrBoundary, i+1)
			}
			out = append(out, PlannedStep{Text: text, After: par.After})
			continue
		}
		goals := *par.Parallel
		if len(goals) < 2 {
			return nil, fmt.Errorf("%w: parallel step %d needs two or more sub-goals", ErrBoundary, i+1)
		}
		for j := range goals {
			goals[j] = strings.TrimSpace(goals[j])
			if goals[j] == "" {
				return nil, fmt.Errorf("%w: parallel step %d sub-goal %d is empty", ErrBoundary, i+1, j+1)
			}
		}
		join := ""
		if par.Join != nil {
			join = *par.Join
		}
		if !joinPolicies[JoinPolicy(join)] {
			return nil, fmt.Errorf("%w: parallel step %d join %q is not all|first_verdict", ErrBoundary, i+1, join)
		}
		out = append(out, PlannedStep{Text: parallelText(goals, JoinPolicy(join)), Parallel: goals, Policy: JoinPolicy(join), After: par.After})
	}
	return out, nil
}

// parallelText is the derived step text of a parallel step.
func parallelText(goals []string, policy JoinPolicy) string {
	return fmt.Sprintf("In parallel (%s): %s", policy, strings.Join(goals, " | "))
}

// JudgeResult is the Judge boundary's product for one verdict kind.
type JudgeResult struct {
	Outcome    string   `json:"outcome"`
	Confidence float64  `json:"confidence"`
	Why        string   `json:"why"`
	Falsifiers []string `json:"falsifiers,omitempty"`
}

// ParseJudge validates a judge response once against the kind's outcome
// vocabulary (allowed) and the confidence range.
func ParseJudge(response []byte, allowed ...string) (JudgeResult, error) {
	var r JudgeResult
	if err := decodeStrict(response, &r); err != nil {
		return r, err
	}
	ok := false
	for _, a := range allowed {
		if r.Outcome == a {
			ok = true
		}
	}
	if !ok {
		return r, fmt.Errorf("%w: outcome %q not in %v", ErrBoundary, r.Outcome, allowed)
	}
	if math.IsNaN(r.Confidence) || r.Confidence < 0 || r.Confidence > 1 {
		return r, fmt.Errorf("%w: confidence %v out of [0,1]", ErrBoundary, r.Confidence)
	}
	if strings.TrimSpace(r.Why) == "" {
		return r, fmt.Errorf("%w: a judgement carries its why", ErrBoundary)
	}
	return r, nil
}

// ---- prompts: the goal and every step result travel whole (D16) ----

func intentPrompt(goal, related []byte) []byte {
	return []byte("You are the intake of an orchestration engine named Maro (a goal that says \"maro\" means you). Read the goal and decide whether it is clear enough to plan and execute without asking the requester anything.\n" +
		"Reply with ONE JSON object and nothing else: {\"clear\": true|false, \"interpretation\": \"<one paragraph: what will be done>\", \"question\": \"<the single question to ask when not clear, else empty>\"}\n\n## Goal\n" + string(goal) + "\n" + string(related))
}

func planPrompt(goal []byte, interpretation string, related, block []byte) []byte {
	return []byte("You are the planner of an orchestration engine named Maro (a goal that says \"maro\" means you). Decompose the goal into the ordered steps an executor will carry out one at a time; each step must be self-contained and verifiable. " +
		"Independent sub-questions that need no tools may run in parallel as one step: {\"parallel\": [\"<sub-goal>\", ...], \"join\": \"all\"} (or \"first_verdict\" when any one good answer suffices). " +
		"A step whose result would be wrong or unsafe unless earlier steps succeeded declares them: {\"step\": \"<text>\", \"after\": [<earlier step numbers>]} — the executor skips a step whose declared prerequisite did not succeed, so declare only real prerequisites.\n" +
		"Reply with ONE JSON object and nothing else: {\"steps\": [\"<step 1>\", {\"step\": \"<step 2>\", \"after\": [1]}, {\"parallel\": [\"<a>\", \"<b>\"], \"join\": \"all\"}, ...]}\n\n## Goal\n" + string(goal) + "\n\n## Interpretation\n" + interpretation + "\n" + string(related) + string(block))
}

// stepPrompt renders step `ordinal`'s request. A step with declared
// prerequisites shows them ("(after 1, 3)"); a plan without any renders
// byte-for-byte as before edges existed, so every earlier record still
// re-derives.
func stepPrompt(goal []byte, steps []string, after [][]int, ordinal int, prior [][]byte, block []byte) []byte {
	var b strings.Builder
	b.WriteString("You are the executor of an orchestration engine named Maro (a goal that says \"maro\" means you), carrying out ONE step of a plan. Do the step and reply with its result; do not do later steps.\n\n## Goal\n")
	b.Write(goal)
	b.WriteString("\n\n## Plan\n")
	for i, s := range steps {
		if i < len(after) && len(after[i]) > 0 {
			fmt.Fprintf(&b, "%d. %s (after %s)\n", i+1, s, ordinals(after[i]))
			continue
		}
		fmt.Fprintf(&b, "%d. %s\n", i+1, s)
	}
	for i, r := range prior {
		fmt.Fprintf(&b, "\n## Result of step %d\n", i+1)
		b.Write(r)
		b.WriteString("\n")
	}
	fmt.Fprintf(&b, "\n## Your step (%d of %d)\n%s\n", ordinal, len(steps), steps[ordinal-1])
	b.Write(block)
	return []byte(b.String())
}

func stepJudgePrompt(goal []byte, step string, result []byte, terminal invoke.TerminalState, fork bool) []byte {
	note := ""
	if terminal == invoke.TerminalPartial {
		note = "\n\nNOTE: the executor's stream ended PARTIAL — the result below may be truncated.\n"
	}
	if fork {
		note += "\n\nNOTE: this step ran its sub-goals in parallel; the result lists each member's whole answer under a '### Member' heading. The step is done when the sub-goals were answered.\n"
	}
	return []byte("You are a judge. Given the goal, one planned step, and the executor's result for that step, decide whether THIS STEP is done. " +
		"Judge only this step: later steps of the plan handle the rest of the goal, and a step that does its own part is done even when the goal is not yet complete.\n" +
		"Reply with ONE JSON object and nothing else: {\"outcome\": \"done\"|\"blocked\"|\"unclear\", \"confidence\": <0..1>, \"why\": \"<one sentence>\"}\n\n## Goal\n" + string(goal) + "\n\n## Step\n" + step + "\n\n## Result" + note + "\n" + string(result) + "\n")
}

func closurePrompt(goal []byte, steps []string, results [][]byte, partial []bool) []byte {
	var b strings.Builder
	b.WriteString("You are the closure judge. Given the goal and every step's result, decide whether the GOAL was achieved — not whether work happened. Name what would prove you wrong.\n" +
		"Reply with ONE JSON object and nothing else: {\"outcome\": \"achieved\"|\"not_achieved\"|\"unknown\", \"confidence\": <0..1>, \"why\": \"<one sentence>\", \"falsifiers\": [\"<observation that would refute this verdict>\", ...]}\n\n## Goal\n")
	b.Write(goal)
	for i, s := range steps {
		fmt.Fprintf(&b, "\n## Step %d: %s\n", i+1, s)
		if i < len(partial) && partial[i] {
			b.WriteString("(the executor's stream ended PARTIAL; this result may be truncated)\n")
		}
		if i < len(results) {
			b.Write(results[i])
			b.WriteString("\n")
		}
	}
	return []byte(b.String())
}

// ordinals renders "1, 3".
func ordinals(ks []int) string {
	parts := make([]string, len(ks))
	for i, k := range ks {
		parts[i] = fmt.Sprint(k)
	}
	return strings.Join(parts, ", ")
}

// gatedBy lists step k's declared prerequisites that did not end done, in
// declaration (ascending) order, with how each ended; empty when k may run.
// A prerequisite not settled yet counts as not done — never as done.
func gatedBy(plan *Plan, k int, done []*StepDone) ([]int, map[int]StepOutcome) {
	var by []int
	outs := map[int]StepOutcome{}
	for _, j := range plan.EdgesAt(k) {
		o := StepOutcome("not settled")
		if j-1 < len(done) && done[j-1] != nil && done[j-1].Ordinal == j {
			o = done[j-1].Outcome
		}
		if o != StepDoneOK {
			by = append(by, j)
			outs[j] = o
		}
	}
	return by, outs
}

// gatedText is a gated step's recorded result: which declared prerequisites
// did not end done, and how each ended. Deterministic — the fold re-derives
// it from the plan and the earlier steps.
func gatedText(by []int, outs map[int]StepOutcome) []byte {
	parts := make([]string, len(by))
	for i, j := range by {
		parts[i] = fmt.Sprintf("step %d ended %s", j, outs[j])
	}
	return []byte("not executed: gated by a declared prerequisite that did not end done (" + strings.Join(parts, "; ") + ")")
}

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

// renderAgenda composes the deliverable from every step's whole result.
func renderAgenda(steps []string, results [][]byte) []byte {
	var b strings.Builder
	for i, s := range steps {
		fmt.Fprintf(&b, "## Step %d: %s\n", i+1, s)
		if i < len(results) {
			b.Write(results[i])
			b.WriteString("\n\n")
		} else {
			b.WriteString("(not executed)\n\n")
		}
	}
	return []byte(strings.TrimRight(b.String(), "\n") + "\n")
}

func init() {
	reg := func(k record.Kind, ty any, version int, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: version, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindIntentAssessment, IntentAssessment{}, 1, "the driver's Intent stage (AGENDA), from the intent invocation",
		"the driver (plan or ask); run fold (restart resumes after it)",
		"whether to plan or to deliver the question")
	// plan/2: declared prerequisites (edges); a plan/1 record has none
	reg(KindPlan, Plan{}, 2, "the driver's Plan stage (AGENDA), from the plan invocation",
		"the driver (the step loop, the gate); run fold (restart resumes at the next undone step); the deliverable",
		"which steps run, in what order, and which are gated on which; plan cardinality")
	// step_done/2: the gated outcome (no invocation, gated_by); a step_done/1 record is never gated
	reg(KindStepDone, StepDone{}, 2, "the driver after each step's execute (+ judge) invocations, or at the gate for a step whose declared prerequisite did not end done",
		"the driver (next step / stop at blocked / gate the dependents; restart continues after the last one); closure judge input; the deliverable",
		"continue or stop; which later steps gate; what the closure judge and the deliverable are made of")
}
