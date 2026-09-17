package run

import (
	"context"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/judgment"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// fakeWire is a wire judgment provider that always answers the same
// thing, at confidence 1.0. It is the adversary of the guarantee this
// file exists to pin: whatever it says, the run resolves as if it had
// never been asked.
type fakeWire struct {
	name    string
	outcome string
	fail    bool
	first   bool // answer the FIRST option (the happy one) instead of the last
	calls   int
	models  []string // the model each request was asked in
}

func (f *fakeWire) Name() string { return f.name }
func (f *fakeWire) Wire() bool   { return true }
func (f *fakeWire) Capabilities() invoke.Capabilities {
	return invoke.Capabilities{Name: f.name, Model: "fake-" + f.name}
}
func (f *fakeWire) Render(r judgment.Request) ([]byte, error) { return judgment.EncodeRequest(r) }
func (f *fakeWire) Parse(b []byte) (judgment.Response, error) { return judgment.DecodeResponse(b) }
func (f *fakeWire) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	f.calls++
	if f.fail {
		return &invoke.Result{Terminal: invoke.TerminalFailed, Reason: "HTTP 503: the sidecar is down"}, nil
	}
	// answer the wrong way, loudly: the LAST option of whatever
	// vocabulary the question offers (never the one a happy run's judge
	// picks), at confidence 1.0
	parsed, err := judgment.DecodeRequest(req.Prompt)
	if err != nil {
		return nil, err
	}
	f.models = append(f.models, parsed.Model)
	opts := parsed.Questions[QOutcome].Options
	choice := opts[len(opts)-1].Name
	if f.first {
		choice = opts[0].Name
	}
	if f.outcome != "" {
		choice = f.outcome
	}
	body := `{"model":"fake","answers":{"outcome":{"type":"choice","choice":"` + choice + `","confidence":1.0,"probabilities":{"` + choice + `":1.0}}},"usage":{"input_tokens":10,"output_tokens":3}}`
	return &invoke.Result{Response: []byte(body), Terminal: invoke.TerminalComplete, Usage: invoke.Usage{InputTokens: 10, OutputTokens: 3, WallMillis: 4}}, nil
}

func shadowRecords(t *testing.T, j *journal.Journal) []*judgment.ShadowJudgment {
	t.Helper()
	var out []*judgment.ShadowJudgment
	if err := j.Control().Scan(0, func(r record.Record) error {
		if s, ok := r.(*judgment.ShadowJudgment); ok {
			out = append(out, s)
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	// and the production population must NOT hold one: the resolver reads
	// production, so this is the guarantee stated as a scan
	if err := j.Production().Scan(0, func(r record.Record) error {
		if _, ok := r.(*judgment.ShadowJudgment); ok {
			t.Fatal("a shadow judgment appeared in the production population")
		}
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	return out
}

// The falsifier of the whole shadow arm: a shadow provider that answers
// the OPPOSITE outcome at confidence 1.0 on every question changes
// nothing — same closure outcome, same effective verdict, same rule.
func TestAShadowAnswerCannotChangeTheResolution(t *testing.T) {
	control := func(shadow judgment.Provider) (*RunState, *harness) {
		h := open(t)
		exec, judge := agendaBackends(
			[]string{"Collected 12 rows of numbers", "Summary written: revenue flat"},
			[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
		d := h.agenda(exec, judge)
		if shadow != nil {
			d.JudgeShadow = []string{shadow.Name()}
			d.Providers = map[string]judgment.Provider{shadow.Name(): shadow}
		}
		if _, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers into a short report"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		return h.only(), h
	}
	plain, _ := control(nil)
	loud := &fakeWire{name: judgment.ProviderJev}
	shadowed, h := control(loud)

	if plain.Closure.Outcome != shadowed.Closure.Outcome || plain.Closure.Rule != shadowed.Closure.Rule {
		t.Fatalf("the shadow arm changed the resolution: %s/%s vs %s/%s",
			plain.Closure.Outcome, plain.Closure.Rule, shadowed.Closure.Outcome, shadowed.Closure.Rule)
	}
	if shadowed.Closure.Outcome != "achieved" {
		t.Fatalf("closure %s", shadowed.Closure.Outcome)
	}
	// every judged verdict of the run was shadowed: two steps + closure
	recs := shadowRecords(t, h.j)
	if len(recs) != 3 || loud.calls != 3 {
		t.Fatalf("%d shadow records, %d calls", len(recs), loud.calls)
	}
	for _, s := range recs {
		if s.Provider != judgment.ProviderJev || s.Answer == nil || s.Answer.Confidence != 1 || s.Failed {
			t.Fatalf("shadow record %+v", s)
		}
		if s.Invocation == "" || s.Question != QOutcome {
			t.Fatalf("shadow record without its call: %+v", s)
		}
	}
	// and the verdicts it shadows are the run's own, untouched
	byID := map[record.RecordID]*verdict.Verdict{}
	for _, v := range h.verdicts(t, shadowed.Run) {
		byID[v.ID] = v
	}
	for _, s := range recs {
		v := byID[s.Primary]
		if v == nil {
			t.Fatalf("shadow cites %s, which is not a verdict of the run", s.Primary)
		}
		if v.Outcome == s.Answer.Choice {
			t.Fatalf("the adversary agreed with the primary — it is not an adversary")
		}
	}
	// the report pairs them and says so
	sum, err := judgment.Summarize(h.j.Production(), h.j.Control())
	if err != nil {
		t.Fatal(err)
	}
	if len(sum.Pairs) != 3 || len(sum.Stats) != 1 || sum.Stats[0].Agree != 0 || sum.Stats[0].N != 3 || len(sum.Disagreements) != 3 || sum.Unshadowed != 0 {
		t.Fatalf("summary %+v", sum)
	}
	// and the unshadowed control run's three judge verdicts are COUNTED
	// as unmeasured, not absent from the report (review r1: a shadow lost
	// between the primary verdict and its record vanished from the
	// denominator)
	_, ph := control(nil)
	psum, err := judgment.Summarize(ph.j.Production(), ph.j.Control())
	if err != nil {
		t.Fatal(err)
	}
	if psum.Unshadowed != 3 || len(psum.Pairs) != 0 {
		t.Fatalf("unshadowed run summary %+v", psum)
	}
	var b strings.Builder
	sum.Render(&b)
	if !strings.Contains(b.String(), "disagreements (3)") {
		t.Fatalf("report:\n%s", b.String())
	}
}

// A shadow provider that fails is recorded as failed and the run is
// unaffected: a measurement arm can never cost a mission.
func TestAFailingShadowNeverFailsTheRun(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"r1", "r2"},
		[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	dead := &fakeWire{name: judgment.ProviderPCD, fail: true}
	d.JudgeShadow = []string{dead.Name()}
	d.Providers = map[string]judgment.Provider{dead.Name(): dead}
	rep, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers into a short report"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionDelivered || rep.Mission.Closure != "achieved" {
		t.Fatalf("mission %+v", rep.Mission)
	}
	recs := shadowRecords(t, h.j)
	if len(recs) != 3 {
		t.Fatalf("%d shadow records", len(recs))
	}
	for _, s := range recs {
		if !s.Failed || s.Answer != nil || !strings.Contains(s.Reason, "503") {
			t.Fatalf("a failed shadow must say so: %+v", s)
		}
	}
	// the shadow's own spend is not the goal's: the recorded usage is the
	// sum over the goal's calls, and the fold re-derives it (a run whose
	// usage counted the shadow would not fold)
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatal(err)
	}
}

// The default arm is unchanged by the seam: no provider configured, no
// shadow, and the judge request is the llm template over the same facts.
func TestTheDefaultArmIsTheLLMOne(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"r1"},
		[]string{intentClear, `{"steps": ["one"]}`, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	if _, err := d.Run(ctxBg, []byte("do one thing"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	cfg := rs.Latest().Attempt.Config
	if cfg.Judgment != "" || cfg.JudgmentBackend != nil || len(cfg.Shadow) != 0 {
		t.Fatalf("the default arm recorded a binding it should not: %+v", cfg)
	}
	// the step judge saw the versioned prose template, not a wire body
	var seen string
	for _, r := range judge.Seen {
		if strings.Contains(string(r.Prompt), judgment.PromptVer) {
			seen = string(r.Prompt)
		}
	}
	if seen == "" || !strings.Contains(seen, "### outcome (choice)") {
		t.Fatal("the llm arm did not render the judgment template")
	}
	if len(shadowRecords(t, h.j)) != 0 {
		t.Fatal("a shadow answer was recorded with no shadow provider configured")
	}
}

// A wire provider and a prose persona lens cannot both be true; the
// driver refuses instead of mangling one of them.
func TestAWireProviderRefusesALens(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends([]string{"r1"}, []string{intentClear})
	d := h.agenda(exec, judge)
	d.Lens = LensSkeptic
	d.JudgeProvider = judgment.ProviderJev
	d.Providers = map[string]judgment.Provider{judgment.ProviderJev: &fakeWire{name: judgment.ProviderJev}}
	_, err := d.Run(ctxBg, []byte("do one thing"), DeliveryPolicy{Required: TransportAccepted})
	if err == nil || !strings.Contains(err.Error(), "cannot carry the prose lens") {
		t.Fatalf("want a refusal, got %v", err)
	}
}

// A judgment provider named but not wired is a misconfiguration, not a
// silent fallback to the default arm.
func TestAnUnwiredProviderIsRefused(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends([]string{"r1"}, []string{intentClear})
	d := h.agenda(exec, judge)
	d.JudgeShadow = []string{judgment.ProviderPCD}
	_, err := d.Run(ctxBg, []byte("do one thing"), DeliveryPolicy{Required: TransportAccepted})
	if err == nil || !strings.Contains(err.Error(), "no judgment provider") {
		t.Fatalf("want a refusal, got %v", err)
	}
}

// A shadow is asked in its OWN name. Found live: the arm forwarded the
// primary's model, and the wire provider answered HTTP 400 "Unknown
// model: sonnet" — the question travels, the model does not.
func TestAShadowIsAskedInItsOwnModel(t *testing.T) {
	sp := &fakeWire{name: judgment.ProviderJev}
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Collected 12 rows of numbers", "Summary written: revenue flat"},
		[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	d.JudgeShadow = []string{sp.Name()}
	d.Providers = map[string]judgment.Provider{sp.Name(): sp}
	if _, err := d.Run(ctxBg, []byte("two steps"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if len(sp.models) == 0 {
		t.Fatal("the shadow was never asked")
	}
	for i, m := range sp.models {
		if m != sp.Capabilities().Model {
			t.Fatalf("request %d asked in %q, not the provider's own %q", i, m, sp.Capabilities().Model)
		}
	}
	// and the recorded prompt is the request as asked, so the arm is
	// auditable without re-deriving it
	for _, s := range shadowRecords(t, h.j) {
		if s.Failed {
			t.Fatalf("shadow failed: %s", s.Reason)
		}
	}
}

// An AGENDA run configured with a non-default PRIMARY provider asks that
// provider — every step judge and the closure — and the incumbent judge
// backend is never asked a judgement. Review r1 (all four lenses): the
// AGENDA invocation closure sent every judge to d.judge(a) whatever
// --judge-provider said, so a jev primary received the wire body on the
// subprocess judge and the fold then refused its own history.
func TestAnAgendaPrimaryProviderIsTheOneAsked(t *testing.T) {
	h := open(t)
	// the judge backend scripts ONLY intent and plan: a judge call
	// reaching it exhausts the script and fails the run
	exec, judge := agendaBackends(
		[]string{"Collected 12 rows of numbers", "Summary written: revenue flat"},
		[]string{intentClear, planTwo})
	d := h.agenda(exec, judge)
	primary := &fakeWire{name: judgment.ProviderJev, first: true}
	d.JudgeProvider = primary.Name()
	d.Providers = map[string]judgment.Provider{primary.Name(): primary}
	rep, err := d.Run(ctxBg, []byte("Summarize the quarterly numbers into a short report"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionDelivered || rep.Mission.Closure != "achieved" {
		t.Fatalf("mission %+v", rep.Mission)
	}
	if primary.calls != 3 {
		t.Fatalf("the primary was asked %d times, want 3 (two steps + closure)", primary.calls)
	}
	for _, m := range primary.models {
		if m != "fake-jev" {
			t.Fatalf("asked in model %q, want the provider's own", m)
		}
	}
	if len(judge.Seen) != 2 {
		t.Fatalf("the incumbent judge backend saw %d calls, want 2 (intent + plan only)", len(judge.Seen))
	}
	// the history folds: the recorded binding and the invocations agree
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	for _, v := range h.verdicts(t, rs.Run) {
		if v.Source.Standing == verdict.StandingJudge && v.Outcome != "done" && v.Outcome != "achieved" {
			t.Fatalf("verdict %s %s: %s", v.VerdictKind, v.ID, v.Outcome)
		}
	}
}

// A fork child inherits the parent's judgment binding — primary, shadows
// and the wired providers — so a first_verdict child's closure judge is
// the provider the operator configured. Review r1: the child driver was
// hand-copied field by field and the binding was left out.
func TestAForkChildInheritsTheJudgmentBinding(t *testing.T) {
	p := &fakeWire{name: judgment.ProviderJev}
	d := &Driver{JudgeProvider: p.Name(), JudgeShadow: []string{judgment.ProviderHosted}, Providers: map[string]judgment.Provider{p.Name(): p}}
	fs := &ForkState{Fork: &Fork{Policy: JoinFirstVerdict}}
	cd := d.childDriver(fs)
	if cd.JudgeProvider != p.Name() || len(cd.JudgeShadow) != 1 || cd.Providers[p.Name()] != p || !cd.ModelJudge || !cd.Confined {
		t.Fatalf("child driver %+v", cd)
	}
}
