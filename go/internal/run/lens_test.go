package run

import (
	"errors"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// §13: a lens is a prefix over judgement, never over the work. Two NOW runs
// of the same goal with the same response, one neutral and one under the
// skeptic lens: the execute requests hash identically; the judge requests
// differ by exactly the lens text; the invocation carries the lens by name
// and content-addressed text; the fold re-derives the lensed verdict. A
// lens on an execute request, a lens whose text is not a lens_text
// thought, and an unknown lens name are refused before anything is written.
func TestLensSwapOnTheSameFacts(t *testing.T) {
	h := open(t)
	h.policy(t, learn.MechModelJudge, true, learn.Provisional)
	exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("Paris.")}, invoke.ScriptedCall{Response: []byte("Paris.")})
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{{Response: []byte(closureYes)}, {Response: []byte(closureYes)}}}
	goal := []byte("What is the capital of France?")

	d1 := h.driver(exec, nil)
	d1.Judge, d1.ModelJudge = judge, true
	if _, err := d1.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	d2 := h.driver(exec, nil)
	d2.Judge, d2.ModelJudge, d2.Lens = judge, true, LensSkeptic
	if _, err := d2.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	led := h.ledger()
	if len(led.Runs) != 2 {
		t.Fatalf("runs: %d", len(led.Runs))
	}
	var neutral, skeptic *AttemptState
	for _, rs := range led.Runs {
		a := rs.Latest()
		if a.Attempt.Config.Lens == "" {
			neutral = a
		} else {
			skeptic = a
		}
	}
	if neutral == nil || skeptic == nil || skeptic.Attempt.Config.Lens != LensSkeptic {
		t.Fatalf("configs: neutral %v skeptic %v", neutral != nil, skeptic != nil)
	}
	pick := func(a *AttemptState, p invoke.Purpose) *invoke.Invocation {
		for _, is := range a.Invocations {
			if is.Invocation.Purpose == p {
				return is.Invocation
			}
		}
		t.Fatalf("no %s invocation", p)
		return nil
	}
	// the work is untouched: same execute request under both lenses
	if pick(neutral, invoke.PurposeExecute).Request != pick(skeptic, invoke.PurposeExecute).Request {
		t.Fatal("the lens changed the execute request")
	}
	nj, sj := pick(neutral, invoke.PurposeJudge), pick(skeptic, invoke.PurposeJudge)
	if nj.Lens != nil || sj.Lens == nil || sj.Lens.Name != LensSkeptic || sj.Lens.Text.Kind != thought.LensText || nj.Request == sj.Request {
		t.Fatalf("judge invocations: neutral %+v skeptic %+v", nj.Lens, sj.Lens)
	}
	nb, err := h.st.Get(nj.Request)
	if err != nil {
		t.Fatal(err)
	}
	sb, err := h.st.Get(sj.Request)
	if err != nil {
		t.Fatal(err)
	}
	lt, err := h.st.Get(sj.Lens.Text)
	if err != nil {
		t.Fatal(err)
	}
	if string(lt) != Lenses[LensSkeptic] || string(sb) != string(Lensed(lt, nb)) {
		t.Fatalf("the skeptic judge request is not the lens over the neutral one:\n%q\n%q", sb, nb)
	}
	if string(Lensed(nil, nb)) != string(nb) {
		t.Fatal("the neutral lens is a prefix")
	}
	// both closures were judged and re-derived by the fold under their lens
	for _, a := range []*AttemptState{neutral, skeptic} {
		if a.Has(Recorded).Outcome.ClosureOut != "achieved" || a.Has(Recorded).Outcome.ClosureSrc != "judge" {
			t.Fatalf("closure: %+v", a.Has(Recorded).Outcome)
		}
	}
	// the operator surface says which lens decided
	var lines []string
	for _, rs := range led.Runs {
		if rs.Latest() == skeptic {
			lines = Inspect(rs)
		}
	}
	joined := strings.Join(lines, "\n")
	if !strings.Contains(joined, "lens: skeptic") || !strings.Contains(joined, "lens=skeptic") || !strings.Contains(joined, "request="+sj.Request.Hash) {
		t.Fatalf("inspect:\n%s", joined)
	}
	if names := LensNames(); len(names) != 2 || names[0] != LensNeutral || names[1] != LensSkeptic {
		t.Fatalf("lenses: %v", names)
	}

	// refusals: nothing written
	head := h.j.Head()
	d3 := h.driver(exec, nil)
	d3.Lens = "poet"
	if _, err := d3.Run(ctxBg, goal, DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrConfig) || !strings.Contains(err.Error(), "unknown lens") {
		t.Fatalf("unknown lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("an unknown lens wrote records")
	}
	sh := &invoke.Shell{J: h.j, Store: h.st, Run: record.RunID(record.NewID()), Attempt: 1}
	lensRef := thought.Address(thought.LensText, []byte(skepticLens))
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeExecute, Prompt: []byte("x"), Lens: &invoke.Lens{Name: LensSkeptic, Text: lensRef}}, nil); err == nil || !strings.Contains(err.Error(), "cannot carry a lens") {
		t.Fatalf("lens on execute: %v", err)
	}
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: LensSkeptic, Text: nj.Request}}, nil); err == nil || !strings.Contains(err.Error(), "not lens_text") {
		t.Fatalf("lens over a prompt thought: %v", err)
	}
	if _, err := sh.Invoke(ctxBg, exec, invoke.Request{Purpose: invoke.PurposeJudge, Prompt: []byte("x"), Lens: &invoke.Lens{Name: "", Text: lensRef}}, nil); err == nil || !strings.Contains(err.Error(), "without a name") {
		t.Fatalf("nameless lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("a refused lens wrote records")
	}
	// the door: the same shapes as records
	forged := &invoke.Invocation{Header: record.Header{ID: record.NewID(), Schema: "invocation/1", RunID: sh.Run, Attempt: 1, Subject: record.Ref{Kind: "prompt", ID: nj.Request.Hash}, At: now()},
		Purpose: invoke.PurposeExecute, Request: nj.Request, Backend: toolless, EffectToken: strings.Repeat("ab", 16), Lens: &invoke.Lens{Name: LensSkeptic, Text: lensRef}}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged-lens", Epoch: h.j.Epoch(), Records: []record.Record{forged}}); err == nil || !strings.Contains(err.Error(), "cannot carry a lens") {
		t.Fatalf("forged execute lens: %v", err)
	}
	if h.j.Head() != head {
		t.Fatal("a forged lens was written")
	}
}

// The fold's lens rules over a recorded attempt: a lensed invocation names
// the attempt's lens and its request begins with the lens text; a lensed
// attempt has every judge request under the lens; the neutral attempt
// carries none.
func TestFoldLensRules(t *testing.T) {
	h := open(t)
	text := []byte(skepticLens)
	lensRef, err := h.st.Put(thought.LensText, text)
	if err != nil {
		t.Fatal(err)
	}
	good, _ := h.st.Put(thought.Prompt, Lensed(text, []byte("judge this")))
	bare, _ := h.st.Put(thought.Prompt, []byte("judge this"))
	rs := &RunState{Run: record.RunID(record.NewID())}
	inv := func(p invoke.Purpose, req thought.Ref, l *invoke.Lens) *invoke.State {
		return &invoke.State{Invocation: &invoke.Invocation{Header: record.Header{ID: record.NewID()}, Purpose: p, Request: req, Lens: l}}
	}
	skeptic := &invoke.Lens{Name: LensSkeptic, Text: lensRef}
	cases := []struct {
		name string
		lens string
		invs []*invoke.State
		want string
	}{
		{"neutral, no lenses", "", []*invoke.State{inv(invoke.PurposeExecute, bare, nil), inv(invoke.PurposeJudge, bare, nil)}, ""},
		{"skeptic, judge under it", LensSkeptic, []*invoke.State{inv(invoke.PurposeExecute, bare, nil), inv(invoke.PurposeJudge, good, skeptic)}, ""},
		{"skeptic, judge without it", LensSkeptic, []*invoke.State{inv(invoke.PurposeJudge, bare, nil)}, "carries none"},
		{"neutral, judge claims skeptic", "", []*invoke.State{inv(invoke.PurposeJudge, good, skeptic)}, "ran under"},
		{"skeptic, other name", LensSkeptic, []*invoke.State{inv(invoke.PurposeJudge, good, &invoke.Lens{Name: "poet", Text: lensRef})}, "ran under"},
		{"skeptic, request lacks the prefix", LensSkeptic, []*invoke.State{inv(invoke.PurposeJudge, bare, skeptic)}, "does not begin with the lens text"},
		{"skeptic, text absent", LensSkeptic, []*invoke.State{inv(invoke.PurposeJudge, good, &invoke.Lens{Name: LensSkeptic, Text: thought.Address(thought.LensText, []byte("never stored"))})}, "lens text"},
	}
	for _, c := range cases {
		a := &AttemptState{Attempt: &RunAttempt{Config: ConfigSnapshot{Lens: c.lens}}, Invocations: c.invs}
		err := checkLenses(rs, a, h.st)
		if (c.want == "") != (err == nil) || (err != nil && !strings.Contains(err.Error(), c.want)) {
			t.Fatalf("%s: want %q, got %v", c.name, c.want, err)
		}
	}
}
