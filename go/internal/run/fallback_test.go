package run

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/judgment"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// The ladder: a wire primary that fails, or answers under the recorded
// escalate bar, hands the same judgment to the fallback provider, whose
// answer is the verdict of record; the fold admits that verdict because
// the record shows why; the default (llm) arm never escalates.

func judgePurposes(t *testing.T, h *harness, a *AttemptState) map[string]int {
	t.Helper()
	inv, err := invoke.Fold(h.j.Production())
	if err != nil {
		t.Fatal(err)
	}
	out := map[string]int{}
	for _, v := range a.Verdicts {
		if v.Source.Standing != verdict.StandingJudge {
			continue
		}
		st := inv[v.Source.Ref]
		if st == nil {
			t.Fatalf("verdict %s cites an unknown invocation", v.ID)
		}
		out[string(st.Invocation.Purpose)]++
	}
	return out
}

func stages(h *harness, stage string) int {
	n := 0
	for _, e := range h.events {
		if e.Stage == stage {
			n++
		}
	}
	return n
}

func laddered(h *harness, p *fakeWire, exec, judge *invoke.Scripted, escalate float64) *Driver {
	d := h.agenda(exec, judge)
	d.JudgeProvider, d.JudgeFallback, d.JudgeEscalate = p.Name(), judgment.ProviderLLM, escalate
	d.Providers = map[string]judgment.Provider{p.Name(): p}
	return d
}

func TestAFailedWirePrimaryEscalatesToTheFallback(t *testing.T) {
	h := open(t)
	p := &fakeWire{name: judgment.ProviderJev, fail: true}
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := laddered(h, p, exec, judge, judgment.DefaultEscalate)
	rep, err := d.Run(ctxBg, []byte("Collect the numbers, then summarize them."), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	// three judgments (two steps, closure): the primary failed each time,
	// the llm fallback answered each, and every verdict cites the fallback
	if p.calls != 3 || len(judge.Seen) != 5 || stages(h, "judge_escalated") != 3 || stages(h, "judge_fallback_failed") != 0 {
		t.Fatalf("primary calls %d, llm judge calls %d, escalations %d, fallback failures %d", p.calls, len(judge.Seen), stages(h, "judge_escalated"), stages(h, "judge_fallback_failed"))
	}
	if got := judgePurposes(t, h, a); got["judge_fallback"] != 3 || got["judge"] != 0 {
		t.Fatalf("verdict sources: %v", got)
	}
	if rep.Mission.Closure != "achieved" || a.Steps[0].Outcome != StepDoneOK || a.Steps[1].Outcome != StepDoneOK {
		t.Fatalf("outcome: closure %s steps %+v", rep.Mission.Closure, a.Steps)
	}
	if cfg := a.Attempt.Config; cfg.JudgmentFallback != judgment.ProviderLLM || cfg.JudgmentEscalate != judgment.DefaultEscalate || cfg.JudgmentFallbackBackend != nil {
		t.Fatalf("config did not record the ladder: %+v", cfg)
	}
	// the fold admits each fallback verdict: the record shows the failed primary
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatalf("fold: %v", err)
	}
	// the escalation is a recorded reason, not a silent switch
	for _, e := range h.events {
		if e.Stage == "judge_escalated" && !strings.Contains(e.Detail, "primary failed") {
			t.Fatalf("escalation detail: %q", e.Detail)
		}
	}
}

func TestAnUnderBarWirePrimaryEscalatesAndAnOverBarOneDoesNot(t *testing.T) {
	for _, tc := range []struct {
		conf      float64
		escalated int
	}{{0.3, 3}, {0.95, 0}} {
		h := open(t)
		p := &fakeWire{name: judgment.ProviderJev, first: true, conf: tc.conf}
		exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
		d := laddered(h, p, exec, judge, judgment.DefaultEscalate)
		if _, err := d.Run(ctxBg, []byte("Collect the numbers, then summarize them."), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		a := h.only().Latest()
		got := judgePurposes(t, h, a)
		if stages(h, "judge_escalated") != tc.escalated || got["judge_fallback"] != tc.escalated || got["judge"] != 3-tc.escalated || len(judge.Seen) != 2+tc.escalated {
			t.Fatalf("conf %.2f: escalations %d, verdict sources %v, llm judge calls %d", tc.conf, stages(h, "judge_escalated"), got, len(judge.Seen))
		}
		if _, err := Fold(h.j.Production(), h.st); err != nil {
			t.Fatalf("conf %.2f: fold: %v", tc.conf, err)
		}
	}
}

func TestTheDefaultArmNeverEscalates(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	d.JudgeFallback, d.JudgeEscalate = judgment.ProviderLLM, 0.99 // set, but the primary is the llm arm
	if _, err := d.Run(ctxBg, []byte("Collect the numbers, then summarize them."), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if cfg := a.Attempt.Config; cfg.JudgmentFallback != "" || cfg.JudgmentEscalate != 0 {
		t.Fatalf("the default arm recorded a ladder: %+v", cfg)
	}
	if got := judgePurposes(t, h, a); got["judge_fallback"] != 0 || stages(h, "judge_escalated") != 0 {
		t.Fatalf("the default arm escalated: %v", got)
	}
}

func TestAFallbackThatAlsoFailsLeavesTheStepUnjudged(t *testing.T) {
	h := open(t)
	p := &fakeWire{name: judgment.ProviderJev, fail: true}
	// the llm judge answers intent and plan, then refuses every judgment
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, "not json", "not json", "not json"})
	d := laddered(h, p, exec, judge, judgment.DefaultEscalate)
	if _, err := d.Run(ctxBg, []byte("Collect the numbers, then summarize them."), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if a.Steps[0].Outcome != StepUnjudged || a.Steps[0].Verdict != "" {
		t.Fatalf("a refused fallback answer must leave the step unjudged, got %+v", a.Steps[0])
	}
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatalf("fold: %v", err)
	}
}

func TestANowClosureEscalatesToo(t *testing.T) {
	h := open(t)
	p := &fakeWire{name: judgment.ProviderJev, fail: true}
	b := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris")})
	judge := scripted(toolless, invoke.ScriptedCall{Response: []byte(closureYes)})
	d := h.driver(b, nil)
	d.ModelJudge, d.Judge = true, judge
	d.JudgeProvider, d.JudgeFallback, d.JudgeEscalate = p.Name(), judgment.ProviderLLM, judgment.DefaultEscalate
	d.Providers = map[string]judgment.Provider{p.Name(): p}
	rep, err := d.Run(ctxBg, []byte("Capital of France?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if p.calls != 1 || stages(h, "judge_escalated") != 1 || rep.Mission.Closure != "achieved" {
		t.Fatalf("primary calls %d, escalations %d, closure %s", p.calls, stages(h, "judge_escalated"), rep.Mission.Closure)
	}
	if got := judgePurposes(t, h, a); got["judge_fallback"] != 1 {
		t.Fatalf("verdict sources: %v", got)
	}
	if _, err := Fold(h.j.Production(), h.st); err != nil {
		t.Fatalf("fold: %v", err)
	}
}
