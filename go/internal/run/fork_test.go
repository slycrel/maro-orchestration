package run

import (
	"context"
	"errors"
	"strings"
	"sync"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// keyed answers by ordered rules over the prompt (the first matching rule
// wins; a prefix rule matches the prompt's start, a plain rule any
// substring), so concurrent children get deterministic responses whatever
// their order and however much of the plan a prompt quotes. A blocking rule
// holds the call until released or cancelled; an effect rule reports a
// tool effect first.
type rule struct {
	key    string
	answer string
	prefix bool
	block  chan struct{}
	effect *invoke.ScriptedEffect
}

type keyed struct {
	caps  invoke.Capabilities
	rules []rule
	def   string
	mu    sync.Mutex
	seen  []invoke.Request
	usage int64
}

func (r rule) matches(prompt string) bool {
	if r.prefix {
		return strings.HasPrefix(prompt, r.key)
	}
	return strings.Contains(prompt, r.key)
}

func (k *keyed) Capabilities() invoke.Capabilities { return k.caps }
func (k *keyed) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	k.mu.Lock()
	k.seen = append(k.seen, req)
	k.usage++
	u := k.usage
	k.mu.Unlock()
	prompt := string(req.Prompt)
	for _, r := range k.rules {
		if !r.matches(prompt) {
			continue
		}
		if r.block != nil {
			select {
			case <-r.block:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
		if r.effect != nil {
			if _, _, err := sink.Observe(invoke.EffectEvent{Op: r.effect.Op, Input: r.effect.Input}); err != nil {
				return nil, err
			}
		}
		if r.answer != "" {
			return &invoke.Result{Response: []byte(r.answer), Terminal: invoke.TerminalComplete, Usage: invoke.Usage{InputTokens: u}}, nil
		}
	}
	return &invoke.Result{Response: []byte(k.def), Terminal: invoke.TerminalComplete, Usage: invoke.Usage{InputTokens: u}}, nil
}

func (k *keyed) calls() int {
	k.mu.Lock()
	defer k.mu.Unlock()
	return len(k.seen)
}

const planFork = `{"steps": ["Warm up", {"parallel": ["sub-goal A: name a prime", "sub-goal B: name a square"], "join": "%s"}, "Wrap up"]}`

// The two-level scenario: a parent AGENDA run whose middle step forks two
// confined NOW children, joined under a policy. Under `all` both members
// complete and the step's result composes both; under `first_verdict` the
// member whose closure a judge finds achieved is selected, the other is
// cancelled at its next boundary (or completes late), and the join settles
// only when every member has a terminal. Every transition survives a kill
// between it and the next, on the parent and on the children.
func TestTwoLevelScenarioSurvivesEveryKill(t *testing.T) {
	type seam struct {
		at     string
		policy JoinPolicy
	}
	var seams []seam
	for _, p := range []JoinPolicy{JoinAll, JoinFirstVerdict} {
		for _, at := range []string{"", "after_plan", "after_fork", "child:after_start", "child:after_executing", "child:after_execute", "child:after_judged", "child:after_recorded", "child:after_child_terminal", "after_join_decision", "after_join_settled", "after_step"} {
			seams = append(seams, seam{at, p}) // after_cancellation needs a running loser: TestFirstVerdictCancelsARunningLoser
		}
	}
	for _, s := range seams {
		name := string(s.policy) + "/" + s.at
		if s.at == "" {
			name = string(s.policy) + "/no-kill"
		}
		t.Run(name, func(t *testing.T) {
			h := open(t)
			exec := &keyed{caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, rules: execRules(), def: "?"}
			judge := &keyed{caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, rules: judgeRules(s.policy), def: judgeDone}
			d := h.agenda(exec, judge)
			d.CrashAt = s.at
			rep, err := d.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted})
			if s.at != "" {
				if !errors.Is(err, ErrCrashed) && !errors.Is(err, invoke.ErrCrashed) {
					t.Fatalf("seam did not fire: %v (%+v)", err, rep)
				}
				h.restart()
				d = h.agenda(exec, judge)
				reps, err := d.Resume(ctxBg)
				if err != nil || len(reps) != 1 {
					t.Fatalf("resume: %v (%d)", err, len(reps))
				}
				rep = reps[0]
			} else if err != nil {
				t.Fatal(err)
			}
			if rep.Mission.Outcome != MissionDelivered || rep.Mission.Closure != "achieved" {
				t.Fatalf("%+v", rep.Mission)
			}
			led := h.ledger()
			parent := led.Runs[rep.Run]
			if len(led.Forks) != 1 {
				t.Fatalf("forks: %d", len(led.Forks))
			}
			var fs *ForkState
			for _, f := range led.Forks {
				fs = f
			}
			if fs.Settled == nil || fs.Decision == nil || !fs.Complete() || fs.Fork.Step != 2 || len(fs.Fork.Members) != 2 {
				t.Fatalf("fork not settled: %+v", fs)
			}
			for _, m := range fs.Fork.Members {
				crs := led.Runs[m.Run]
				if crs == nil || !crs.Terminal() || crs.Goal.Parent != parent.Goal.ID || crs.Goal.Origin != OriginFork || !crs.Latest().Attempt.Config.Confined {
					t.Fatalf("child %s: %+v", m.Run, crs)
				}
				for _, a := range crs.Attempts {
					for _, is := range a.Invocations {
						if is.Invocation.Tools {
							t.Fatal("a child invocation offered tools")
						}
					}
				}
			}
			step2 := parent.Latest().Steps[1]
			composed, _ := h.st.Get(step2.Result)
			switch s.policy {
			case JoinAll:
				if len(fs.Decision.Selected) != 2 || len(fs.Decision.Cancel) != 0 || !strings.Contains(string(composed), "7 is prime") || !strings.Contains(string(composed), "9 is a square") {
					t.Fatalf("all: %+v\n%s", fs.Decision, composed)
				}
			case JoinFirstVerdict:
				if len(fs.Decision.Selected) != 1 || !strings.Contains(string(composed), "7 is prime") || strings.Contains(string(composed), "9 is a square") {
					t.Fatalf("first_verdict: %+v\n%s", fs.Decision, composed)
				}
				sel := led.Runs[fs.Decision.Selected[0].Run]
				if sel.Closure == nil || sel.Closure.Outcome != "achieved" || sel.Closure.Rule != "standing:judge" {
					t.Fatalf("selected child closure: %+v", sel.Closure)
				}
				for _, c := range fs.Decision.Cancel {
					other := fs.Terminals[c.Run]
					if other == nil || (other.State != ChildCancelled && other.State != ChildCompletedLate) {
						t.Fatalf("loser terminal: %+v", other)
					}
				}
			}
			if step2.Fork != fs.Fork.ID || step2.Invocation != "" || !strings.Contains(string(rep.Payload), "wrapped") {
				t.Fatalf("step 2: fork=%s inv=%q payload=%q", step2.Fork, step2.Invocation, rep.Payload)
			}
			// a second resume is a no-op
			head := h.j.Head()
			if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
				t.Fatalf("second resume: %v %d", err, len(reps))
			}
		})
	}
}

func planForPolicy(p JoinPolicy) string {
	return strings.Replace(planFork, "%s", string(p), 1)
}

// execRules: the parent's steps by their ordinal marker (a step prompt
// quotes the whole plan, so sub-goal texts alone would be ambiguous), the
// children by their goal text at the prompt's start.
func execRules(extra ...rule) []rule {
	base := []rule{{key: "## Your step (1 of", answer: "warmed"}, {key: "## Your step (3 of", answer: "wrapped"}}
	base = append(base, extra...)
	return append(base, rule{key: "sub-goal A", answer: "7 is prime", prefix: true}, rule{key: "sub-goal B", answer: "9 is a square", prefix: true})
}

func judgeRules(policy JoinPolicy) []rule {
	return []rule{
		{key: "intake of an orchestration engine", answer: intentClear},
		{key: "planner", answer: planForPolicy(policy)},
		{key: "## Step 1: sub-goal A", answer: `{"outcome": "achieved", "confidence": 0.9, "why": "prime named", "falsifiers": []}`},
		{key: "## Step 1: sub-goal B", answer: `{"outcome": "not_achieved", "confidence": 0.9, "why": "no square", "falsifiers": []}`},
		{key: "closure judge", answer: closureYes},
	}
}

// Confinement is structural: a child that reports a tool effect (even a
// query) has it refused by the shell and fails; under `all` the other
// member is selected and the failure is named in the composition; the
// fold refuses a child attempt that is not confined.
func TestForkChildrenAreConfined(t *testing.T) {
	h := open(t)
	exec := &keyed{caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec", ActsOutward: true}, rules: execRules(rule{key: "sub-goal B", prefix: true, answer: "wrote a file", effect: &invoke.ScriptedEffect{Op: "Write", Input: []byte(`{"path":"x"}`)}}), def: "?"}
	judge := &keyed{caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, rules: judgeRules(JoinAll), def: judgeDone}
	d := h.agenda(exec, judge)
	rep, err := d.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil || rep.Mission.Outcome != MissionDelivered {
		t.Fatalf("%v %+v", err, rep)
	}
	led := h.ledger()
	var fs *ForkState
	for _, f := range led.Forks {
		fs = f
	}
	var failed, completed int
	for _, m := range fs.Fork.Members {
		switch fs.Terminals[m.Run].State {
		case ChildFailed:
			failed++
			crs := led.Runs[m.Run]
			rec := crs.Latest().Has(Recorded).Outcome
			if !strings.Contains(rec.Reason, "confined") {
				t.Fatalf("child failure reason: %s", rec.Reason)
			}
			refused := 0
			for _, is := range crs.Latest().Invocations {
				for _, e := range is.Effects {
					if e.Refused {
						refused++
					}
				}
			}
			if refused != 1 {
				t.Fatalf("refused effects: %d", refused)
			}
		case ChildCompleted:
			completed++
		}
	}
	if failed != 1 || completed != 1 || len(fs.Decision.Selected) != 1 {
		t.Fatalf("terminals: failed=%d completed=%d decision=%+v", failed, completed, fs.Decision)
	}
	// the parent's step 1 (not a fork) ran with tools on the outward backend
	if !exec.seen[0].Tools {
		t.Fatal("the parent's own step lost its tools")
	}
}

// The journal executes the fork vocabulary and the fold the fork's causal
// order: a fork off the plan, a decision for `all` before every terminal,
// a cancellation the decision did not name, a child terminal from another
// attempt, a settled join before the barrier.
func TestJournalExecutesForkVocabulary(t *testing.T) {
	h := open(t)
	exec := &keyed{caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, rules: execRules(), def: "?"}
	judge := &keyed{caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, rules: judgeRules(JoinAll), def: judgeDone}
	d := h.agenda(exec, judge)
	d.CrashAt = "after_fork"
	if _, err := d.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	led := h.ledger()
	var fs *ForkState
	for _, f := range led.Forks {
		fs = f
	}
	rs := led.Runs[fs.Fork.RunID]
	hd := func(subj record.Ref) record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: subj, At: now()}
	}
	fref := record.Ref{Kind: "fork", ID: string(fs.Fork.ID)}
	mkFork := func(goals int, policy JoinPolicy) *Fork {
		id := record.NewID()
		f := &Fork{Header: record.Header{ID: id, RunID: rs.Run, Attempt: 1, Subject: record.Ref{Kind: "fork", ID: string(id)}, At: now()}, Step: 2, Policy: policy}
		for i := 0; i < goals; i++ {
			f.Goals = append(f.Goals, record.NewID())
			f.Members = append(f.Members, record.AttemptRef{Run: record.RunID(record.NewID()), Attempt: 1})
		}
		return f
	}
	door := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"fork one member", mkFork(1, JoinAll), "two members"},
		{"fork foreign policy", mkFork(2, "quorum"), "policy"},
		{"decision selects none", &JoinDecision{Header: hd(fref), Fork: fs.Fork.ID}, "at least one"},
		{"terminal from another attempt", &ChildTerminal{Header: hd(fref), Fork: fs.Fork.ID, Child: fs.Fork.Members[0], State: ChildCompleted}, "written by the child attempt"},
		{"terminal foreign state", &ChildTerminal{Header: record.Header{ID: record.NewID(), RunID: fs.Fork.Members[0].Run, Attempt: 1, Subject: fref, At: now()}, Fork: fs.Fork.ID, Child: fs.Fork.Members[0], State: "lost"}, "out of vocabulary"},
	}
	for _, c := range door {
		c.rec.Head().Schema = record.SchemaVer(string(c.rec.Kind()) + "/1")
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.rec}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	fold := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"all decided before terminals", &JoinDecision{Header: hd(fref), Fork: fs.Fork.ID, Selected: fs.Fork.Members}, "every member is terminal"},
		{"settled before the barrier", &JoinSettled{Header: hd(fref), Fork: fs.Fork.ID}, "before every member"},
		{"cancellation without decision", &CancellationIssued{Header: hd(fref), Fork: fs.Fork.ID, Child: fs.Fork.Members[1], Reason: "x"}, "without a decision"},
		{"second fork at the step", mkFork(2, JoinAll), "already forked"},
	}
	for _, c := range fold {
		h2 := open(t)
		exec2 := &keyed{caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, rules: execRules(), def: "?"}
		judge2 := &keyed{caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, rules: judgeRules(JoinAll), def: judgeDone}
		d2 := h2.agenda(exec2, judge2)
		d2.CrashAt = "after_fork"
		if _, err := d2.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		led2 := h2.ledger()
		var f2 *ForkState
		for _, f := range led2.Forks {
			f2 = f
		}
		r := c.rec
		r.Head().RunID = f2.Fork.RunID
		switch x := r.(type) {
		case *JoinDecision:
			x.Fork, x.Selected = f2.Fork.ID, f2.Fork.Members
			x.Subject = record.Ref{Kind: "fork", ID: string(f2.Fork.ID)}
		case *JoinSettled:
			x.Fork = f2.Fork.ID
			x.Subject = record.Ref{Kind: "fork", ID: string(f2.Fork.ID)}
		case *CancellationIssued:
			x.Fork, x.Child = f2.Fork.ID, f2.Fork.Members[1]
			x.Subject = record.Ref{Kind: "fork", ID: string(f2.Fork.ID)}
		case *Fork:
			x.Goals = f2.Fork.Goals // the same child goals again: not fresh
		}
		if err := forge(t, h2, c.name, r); err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: folded: %v (want %q)", c.name, err, c.want)
		}
	}
}

// first_verdict returns early: with member B held in flight, member A's
// achieved closure decides the join while B still runs; B's invocation is
// cancelled, B is NOT re-executed, and B ends `cancelled` at its first
// boundary; the parent's step composes A alone and the join settles.
func TestFirstVerdictCancelsARunningLoser(t *testing.T) {
	for _, seam := range []string{"", "after_cancellation"} {
		t.Run("seam="+seam, func(t *testing.T) { firstVerdictRunningLoser(t, seam) })
	}
}

func firstVerdictRunningLoser(t *testing.T, seam string) {
	h := open(t)
	hold := make(chan struct{})
	exec := &keyed{caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, rules: execRules(rule{key: "sub-goal B", prefix: true, block: hold, answer: "9 is a square"}), def: "?"}
	judge := &keyed{caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, rules: judgeRules(JoinFirstVerdict), def: judgeDone}
	d := h.agenda(exec, judge)
	d.CrashAt = seam
	rep, err := d.Run(ctxBg, []byte("two-level"), DeliveryPolicy{Required: TransportAccepted})
	close(hold)
	if seam != "" {
		if !errors.Is(err, ErrCrashed) {
			t.Fatalf("seam did not fire: %v", err)
		}
		h.restart()
		d = h.agenda(exec, judge)
		reps, err := d.Resume(ctxBg)
		if err != nil || len(reps) != 1 {
			t.Fatalf("resume: %v", err)
		}
		rep = reps[0]
	} else if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionDelivered {
		t.Fatalf("%+v", rep.Mission)
	}
	led := h.ledger()
	var fs *ForkState
	for _, f := range led.Forks {
		fs = f
	}
	if len(fs.Decision.Selected) != 1 || len(fs.Decision.Cancel) != 1 {
		t.Fatalf("decision: %+v", fs.Decision)
	}
	loser := led.Runs[fs.Decision.Cancel[0].Run]
	if fs.Terminals[loser.Run].State != ChildCancelled || len(loser.Attempts) != 2 || loser.Attempts[0].Current() != Recoverable {
		t.Fatalf("loser: terminal %+v attempts %d", fs.Terminals[loser.Run], len(loser.Attempts))
	}
	bCalls := 0
	for _, r := range exec.seen {
		if strings.HasPrefix(string(r.Prompt), "sub-goal B") { // the child's own prompt; the parent's step prompts quote the plan
			bCalls++
		}
	}
	if bCalls != 1 {
		for _, a := range loser.Attempts {
			reason := ""
			if r := a.Has(Recorded); r != nil {
				reason = r.Outcome.Reason
			}
			t.Logf("loser attempt %d: %s | recorded: %q | invocations: %d", a.Attempt.Attempt, trail(a), reason, len(a.Invocations))
		}
		t.Fatalf("the loser was executed %d times", bCalls)
	}
	composed, _ := h.st.Get(led.Runs[rep.Run].Latest().Steps[1].Result)
	if !strings.Contains(string(composed), "7 is prime") || strings.Contains(string(composed), "9 is a square") {
		t.Fatalf("composition: %s", composed)
	}
}
