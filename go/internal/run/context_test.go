package run

import (
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

const operatorDocs = "USER CONTEXT (CONTEXT.md):\nThe maro box is the 2014 Mac Mini running Ubuntu; the mainline repo lives at ~/claude/maro-orchestration."

// Operator context is a recorded input: handed in with the goal, stored as
// a thought, cited by the goal record, and rendered into every request
// the goal's runs make — the NOW execute request here — so the engine
// answers with the operator's docs in view (the same docs Python's
// planner injects) instead of asking what "the maro box" is. The fold
// re-derives the request from the recorded context, so a journal that
// carried it verifies.
func TestOperatorContextRidesIntoTheNowRequest(t *testing.T) {
	h := open(t)
	b := scripted(toolless, invoke.ScriptedCall{Response: []byte("It is the Mac Mini.")})
	d := h.driver(b, nil)
	d.Fresh, d.Context = true, []byte(operatorDocs)
	if _, err := d.Run(ctxBg, []byte("What is the maro box?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.newestRun(t)
	if rs.Goal.Context == nil || rs.Goal.Context.Kind != thought.Context {
		t.Fatalf("goal must cite its context thought: %+v", rs.Goal.Context)
	}
	body, err := h.st.Get(*rs.Goal.Context)
	if err != nil || string(body) != operatorDocs {
		t.Fatalf("stored context: %q %v", body, err)
	}
	want := "## Operator context\n" + operatorDocs
	if !strings.Contains(string(rs.Context), want) {
		t.Fatalf("rendered context: %q", rs.Context)
	}
	req := h.requestOf(t, rs.Latest())
	if !strings.Contains(string(req), want) || !strings.Contains(string(req), "What is the maro box?") {
		t.Fatalf("the execute request must carry the operator context:\n%s", req)
	}
	if !strings.Contains(string(b.Seen[0].Prompt), want) {
		t.Fatalf("the backend must have seen the context:\n%s", b.Seen[0].Prompt)
	}
	if s := Summarize(rs); s.Context != rs.Goal.Context.Hash || s.Context == "" {
		t.Fatalf("summary context: %q", s.Context)
	}
	// a goal given no context records none and renders none
	c := scripted(toolless, invoke.ScriptedCall{Response: []byte("no idea")})
	dc := h.driver(c, nil)
	dc.Fresh = true
	if _, err := dc.Run(ctxBg, []byte("What is the maro box, again?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	plain := h.newestRun(t)
	if plain.Goal.Context != nil || len(plain.Context) != 0 || strings.Contains(string(h.requestOf(t, plain.Latest())), "## Operator context") || Summarize(plain).Context != "" {
		t.Fatalf("a goal without context must not render one: %+v %q", plain.Goal.Context, plain.Context)
	}
	// whitespace-only context is no context
	w := scripted(toolless, invoke.ScriptedCall{Response: []byte("still no idea")})
	dw := h.driver(w, nil)
	dw.Fresh, dw.Context = true, []byte("  \n\t")
	if _, err := dw.Run(ctxBg, []byte("What is the maro box, once more?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if ws := h.newestRun(t); ws.Goal.Context != nil || len(ws.Context) != 0 {
		t.Fatalf("blank context must be no context: %+v", ws.Goal.Context)
	}
	// a goal whose lineage the operator chose (--after) never reads the
	// landscape: its context is loaded at the attempt, and the fold still
	// verifies the request it rendered
	lin, err := LineageOf(h.ledger(), HandleOf(rs.Run))
	if err != nil {
		t.Fatal(err)
	}
	f := scripted(toolless, invoke.ScriptedCall{Response: []byte("Still the Mac Mini.")})
	df := h.driver(f, nil)
	df.After, df.Context = lin, []byte(operatorDocs)
	if _, err := df.Run(ctxBg, []byte("And where does its repo live?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	follow := h.newestRun(t)
	if follow.Landscape != nil || follow.Parent != rs.Goal.ID || !strings.Contains(string(follow.Context), want) || !strings.Contains(string(h.requestOf(t, follow.Latest())), want) {
		t.Fatalf("followed goal's context: landscape=%v parent=%s %q", follow.Landscape, follow.Parent, follow.Context)
	}
}

// On the AGENDA lane the context rides into the intent and plan requests
// (the ones that decide whether the goal is clear and what to do), and
// into nothing else: the step executions and judges see their steps.
func TestOperatorContextRidesIntoIntentAndPlan(t *testing.T) {
	h := open(t)
	exec, judge := agendaBackends(
		[]string{"Collected 12 rows of numbers", "Summary written: revenue flat"},
		[]string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	d := h.agenda(exec, judge)
	d.Fresh, d.Context = true, []byte(operatorDocs)
	if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	want := "## Operator context\n" + operatorDocs
	if len(judge.Seen) != 5 {
		t.Fatalf("judge calls: %d", len(judge.Seen))
	}
	for i, purpose := range []string{"intent", "plan"} {
		if !strings.Contains(string(judge.Seen[i].Prompt), want) {
			t.Fatalf("the %s request must carry the operator context:\n%s", purpose, judge.Seen[i].Prompt)
		}
	}
	for i := 2; i < 5; i++ {
		if strings.Contains(string(judge.Seen[i].Prompt), "## Operator context") {
			t.Fatalf("judge call %d must not carry the operator context", i)
		}
	}
	for i, call := range exec.Seen {
		if strings.Contains(string(call.Prompt), "## Operator context") {
			t.Fatalf("step %d must not carry the operator context", i)
		}
	}
	rs := h.newestRun(t)
	if rs.Goal.Context == nil || !strings.Contains(string(rs.Context), want) {
		t.Fatalf("agenda goal context: %+v %q", rs.Goal.Context, rs.Context)
	}
}

// The goal record refuses a context ref of the wrong kind: context is
// its own thought kind, never a goal or a prompt wearing its slot.
func TestGoalContextMustBeAContextThought(t *testing.T) {
	h := open(t)
	ref, err := h.st.Put(thought.Goal, []byte("a goal"))
	if err != nil {
		t.Fatal(err)
	}
	g, _ := Intake([]byte("a goal"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
	wrong := ref
	g.Context = &wrong
	if err := g.ValidateWire(); err == nil || !strings.Contains(err.Error(), "context must be a context thought") {
		t.Fatalf("wrong-kind context: %v", err)
	}
	cref, err := h.st.Put(thought.Context, []byte(operatorDocs))
	if err != nil {
		t.Fatal(err)
	}
	g.Context = &cref
	if err := g.ValidateWire(); err != nil {
		t.Fatalf("context-kind ref: %v", err)
	}
}
