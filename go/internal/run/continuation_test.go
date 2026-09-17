package run

import (
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

const goalFlaky = "Fix the flaky login test in the auth package"

// stoppedNow drives a NOW run whose backend fails: a terminal run that did
// not achieve its goal.
func (h *harness) stoppedNow(t *testing.T, goal string) *RunState {
	t.Helper()
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	d.Fresh = true
	if _, err := d.Run(ctxBg, []byte(goal), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.newestRun(t)
	if !Stopped(rs) || rs.TerminalAt == 0 {
		t.Fatalf("not a stopped run: terminal_at=%d", rs.TerminalAt)
	}
	return rs
}

// after drives a NOW run that follows prior (--after), with the scripted
// calls given; crashAt is the driver's crash seam ("" = none).
func (h *harness) after(t *testing.T, prior *RunState, goal string, crashAt string, calls ...invoke.ScriptedCall) (*RunState, error) {
	t.Helper()
	led := h.ledger()
	lin, err := LineageOf(led, HandleOf(prior.Run))
	if err != nil {
		t.Fatal(err)
	}
	d := h.driver(scripted(toolless, calls...), nil)
	d.After, d.CrashAt = lin, crashAt
	_, err = d.Run(ctxBg, []byte(goal), DeliveryPolicy{Required: TransportAccepted})
	return h.newestRun(t), err
}

var okCall = invoke.ScriptedCall{Response: []byte("fixed: the test waits for the session cookie")}

// A run that follows a stopped run claims it before its first attempt, in
// a record the fold checks; its requests carry where the source stopped;
// its own end settles the source (derived: live / achieved / stopped); a
// second continuation of the same source is refused and ends as an honest
// failed run; a follow of an achieved run claims nothing.
func TestContinuationClaimsAStoppedRunAndSettlesIt(t *testing.T) {
	h := open(t)
	a := h.stoppedNow(t, goalFlaky)
	b, err := h.after(t, a, goalFlaky+" (retry)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	c := b.Continuation
	if c == nil || c.Source != a.Run || c.How != ContinuedAfter || c.Refused != "" || c.Goal != b.Goal.ID || c.AsOf == 0 {
		t.Fatalf("continuation: %+v", c)
	}
	if b.Parent != a.Goal.ID || b.Root != a.Goal.ID {
		t.Fatalf("lineage: parent=%s root=%s", b.Parent, b.Root)
	}
	block := "\n\n## Continues prior run (" + HandleOf(a.Run) + ", after)\nIt stopped: execution failed: backend exited 1\nIts goal: " + goalFlaky + "\nIt recorded no answer.\n"
	if string(b.Related) != block {
		t.Fatalf("related block:\n%q\nwant\n%q", b.Related, block)
	}
	if req := h.requestOf(t, b.Latest()); !strings.Contains(string(req), block) {
		t.Fatalf("the execute request does not carry the block:\n%s", req)
	}
	led := h.ledger()
	if got := led.Continued[a.Run]; got == nil || got.RunID != b.Run {
		t.Fatalf("continued index: %+v", got)
	}
	if Stopped(b) || ContinuationState(led, led.Continued[a.Run], Now) != "finished" {
		t.Fatalf("b did not finish: %+v", MissionOf(b))
	}
	if by := ContinuedBy(led, a); by != "continued by "+HandleOf(b.Run)+": finished" {
		t.Fatalf("continued-by line: %q", by)
	}
	if lines := strings.Join(Inspect(b), "\n"); !strings.Contains(lines, "continues "+HandleOf(a.Run)+" (after)") {
		t.Fatalf("inspect: %s", lines)
	}
	// the source is done through B (finished: complete, closure unknown —
	// the self claim cannot promote it, nothing says it fell short): a second continuation of A is refused
	// — before intake by the CLI's pre-check, and by the driver as a
	// recorded refusal the run ends on
	want := "run " + HandleOf(a.Run) + " was continued by " + HandleOf(b.Run) + ", which finished: follow " + HandleOf(b.Run) + " instead"
	if _, err := Continuable(led, a.Run, Now); err == nil || err.Error() != want {
		t.Fatalf("pre-check: %v", err)
	}
	c2, err := h.after(t, a, goalFlaky+" (again)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if c2.Continuation == nil || c2.Continuation.Refused != want || c2.Continuation.Source != a.Run {
		t.Fatalf("refusal record: %+v", c2.Continuation)
	}
	if m := MissionOf(c2); m.Outcome != MissionFailedExec || !strings.HasPrefix(m.Reason, "continuation refused: "+want) || c2.TerminalAt == 0 {
		t.Fatalf("a refused continuation ends like a finished run: %+v", m)
	}
	if len(c2.Related) != 0 {
		t.Fatalf("a refused continuation carries no block: %q", c2.Related)
	}
	if lines := strings.Join(Inspect(c2), "\n"); !strings.Contains(lines, "continuation refused: "+want) {
		t.Fatalf("inspect: %s", lines)
	}
	led = h.ledger()
	if led.Continued[a.Run].RunID != b.Run {
		t.Fatal("the refusal displaced the claim")
	}
	// a follow of the run that finished is a plain follow: nothing to continue
	d, err := h.after(t, b, "Now make the auth suite run in CI", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if d.Continuation != nil || d.Parent != b.Goal.ID || len(d.Related) != 0 {
		t.Fatalf("plain follow: cont=%+v parent=%s related=%q", d.Continuation, d.Parent, d.Related)
	}
	if h.count(KindContinuation) != 2 {
		t.Fatalf("continuation records: %d", h.count(KindContinuation))
	}
	// a restart re-derives all of it
	h.restart()
	led = h.ledger()
	if rb := led.Runs[b.Run]; string(rb.Related) != block || rb.Continuation == nil || rb.Continuation.ID != c.ID {
		t.Fatalf("after restart: %q %+v", rb.Related, rb.Continuation)
	}
	if by := ContinuedBy(led, led.Runs[a.Run]); by != "continued by "+HandleOf(b.Run)+": finished" {
		t.Fatalf("after restart: %q", by)
	}
}

// One continuation per source: while the first is live (its process died
// mid-attempt) a second is refused; a run that has not stopped cannot be
// continued at all.
func TestContinuationRefusesWhileTheFirstIsLive(t *testing.T) {
	h := open(t)
	a := h.stoppedNow(t, goalFlaky)
	b, err := h.after(t, a, goalFlaky+" (retry)", "after_executing", okCall)
	if !errors.Is(err, ErrCrashed) {
		t.Fatalf("err=%v", err)
	}
	h.restart()
	led := h.ledger()
	if ContinuationState(led, led.Continued[a.Run], Now) != "live" {
		t.Fatal("b is not live")
	}
	want := "run " + HandleOf(a.Run) + " is being continued by " + HandleOf(b.Run) + " (live)"
	c, err := h.after(t, a, goalFlaky+" (again)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if c.Continuation == nil || c.Continuation.Refused != want || MissionOf(c).Outcome != MissionFailedExec {
		t.Fatalf("refusal: %+v %+v", c.Continuation, MissionOf(c))
	}
	// the live one resumes and achieves; then the source is done through it
	if _, err := h.driver(scripted(toolless, okCall), nil).Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	led = h.ledger()
	if Stopped(led.Runs[b.Run]) {
		t.Fatalf("b after resume: %+v", MissionOf(led.Runs[b.Run]))
	}
	if _, err := Continuable(led, a.Run, Now); err == nil || !strings.Contains(err.Error(), "which finished") {
		t.Fatalf("after b achieved: %v", err)
	}
	// a run that has not stopped: nothing to continue yet
	d := h.driver(scripted(toolless, okCall), nil)
	d.Fresh, d.CrashAt = true, "after_executing"
	if _, err := d.Run(ctxBg, []byte("Rotate the API key"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("err=%v", err)
	}
	h.restart()
	live := h.newestRun(t)
	e, err := h.after(t, live, "Rotate the API key (again)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if e.Continuation == nil || e.Continuation.Refused != "run "+HandleOf(live.Run)+" has not stopped (attempt 1 executing)" {
		t.Fatalf("live source: %+v", e.Continuation)
	}
}

// The chain moves forward: a source whose continuation stopped is
// continued THROUGH the continuation, never re-claimed.
func TestContinuationChainMovesForward(t *testing.T) {
	h := open(t)
	a := h.stoppedNow(t, goalFlaky)
	b, err := h.after(t, a, goalFlaky+" (retry)", "", invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 2"})
	if err != nil {
		t.Fatal(err)
	}
	if b.Continuation == nil || b.Continuation.Refused != "" || !Stopped(b) {
		t.Fatalf("b: %+v stopped=%v", b.Continuation, Stopped(b))
	}
	led := h.ledger()
	if by := ContinuedBy(led, a); by != "continued by "+HandleOf(b.Run)+": stopped" {
		t.Fatalf("a: %q", by)
	}
	want := "run " + HandleOf(a.Run) + " was continued by " + HandleOf(b.Run) + ", which stopped: continue " + HandleOf(b.Run) + " instead"
	c, err := h.after(t, a, goalFlaky+" (third)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if c.Continuation == nil || c.Continuation.Refused != want {
		t.Fatalf("re-claim: %+v", c.Continuation)
	}
	// C ended on a refusal, not on work: it cannot be continued either
	e, err := h.after(t, c, goalFlaky+" (after the refused)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if e.Continuation == nil || e.Continuation.Refused != "run "+HandleOf(c.Run)+" is a refused continuation ("+want+")" {
		t.Fatalf("after a refused run: %+v", e.Continuation)
	}
	d, err := h.after(t, b, goalFlaky+" (through b)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if d.Continuation == nil || d.Continuation.Source != b.Run || d.Continuation.Refused != "" || Stopped(d) {
		t.Fatalf("d: %+v", d.Continuation)
	}
	if !strings.Contains(string(d.Related), "It stopped: execution failed: backend exited 2") {
		t.Fatalf("d's block: %q", d.Related)
	}
	led = h.ledger()
	if by := ContinuedBy(led, led.Runs[b.Run]); by != "continued by "+HandleOf(d.Run)+": finished" {
		t.Fatalf("b: %q", by)
	}
}

// The landscape's own rerun decision on a stopped run is a continuation
// too (how = rerun): the block says where the source stopped and where
// its steps ended, without repeating the goal, answer and plan the rerun
// block already carries. A rerun the judge chooses of a source already
// continued is refused the same way — Maro's choice ends as an honest
// failed run, never a silent second claim.
func TestLandscapeRerunOfAStoppedRunContinuesIt(t *testing.T) {
	h := open(t)
	// A: an AGENDA run whose step 2 was blocked — a partial execution, stopped
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeBlocked, closureUnsure})
	d := h.agenda(exec, judge)
	d.Fresh = true
	if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	if !Stopped(a) {
		t.Fatalf("a: %+v", MissionOf(a))
	}
	// D: the same wording, no --after: the judge says rerun
	dd, _ := h.now(t, goalQuarterly, false, landRerun, "Summary: revenue flat, costs up")
	c := dd.Continuation
	if c == nil || c.How != ContinuedRerun || c.Source != a.Run || c.Refused != "" {
		t.Fatalf("rerun continuation: %+v", c)
	}
	rel := string(dd.Related)
	if !strings.Contains(rel, "## Related prior run ("+HandleOf(a.Run)+", rerun)") || !strings.Contains(rel, "Its plan (reuse or revise):\n1. Collect the numbers\n2. Write the summary\n") {
		t.Fatalf("landscape block: %q", rel)
	}
	if !strings.Contains(rel, "\n\n## Continues prior run ("+HandleOf(a.Run)+", rerun)\nIt stopped: execution failed: blocked at step 2: Write the summary\nWhere its steps ended: 1 done, 2 blocked\n") {
		t.Fatalf("continuation block: %q", rel)
	}
	if strings.Count(rel, "Its goal:") != 1 || strings.Count(rel, "Its plan") != 1 {
		t.Fatalf("the goal and plan ride once: %q", rel)
	}
	if req := h.requestOf(t, dd.Latest()); !strings.Contains(string(req), rel) {
		t.Fatalf("request lacks the blocks:\n%s", req)
	}
	if Stopped(dd) {
		t.Fatalf("dd: %+v", MissionOf(dd))
	}
	// E: the same wording again; the judge names A (candidate 2: D is
	// newer and listed first) — A is done through D, so E is refused
	e, _ := h.now(t, goalQuarterly, false, `{"relation": "rerun", "run": "`+HandleOf(a.Run)+`", "reason": "the original"}`, "unused")
	want := "run " + HandleOf(a.Run) + " was continued by " + HandleOf(dd.Run) + ", which finished: follow " + HandleOf(dd.Run) + " instead"
	if e.Landscape == nil || e.Landscape.Chosen != a.Run || e.Continuation == nil || e.Continuation.Refused != want || e.Continuation.How != ContinuedRerun {
		t.Fatalf("e: landscape=%+v continuation=%+v", e.Landscape, e.Continuation)
	}
	if m := MissionOf(e); m.Outcome != MissionFailedExec || m.Reason[:len("continuation refused: ")] != "continuation refused: " {
		t.Fatalf("e ends on the refusal: %+v", m)
	}
	// F: a related decision on a stopped run is a tangent, not a continuation
	f, _ := h.now(t, goalFollowUp, false, `{"relation": "related", "run": "`+HandleOf(a.Run)+`", "reason": "one question deeper"}`, "The revenue line")
	if f.Continuation != nil || f.Parent != a.Goal.ID {
		t.Fatalf("related: %+v parent=%s", f.Continuation, f.Parent)
	}
	h.restart()
	h.ledger()
}

// The claim survives the kill at every seam around it: after the claim
// (resume claims nothing twice), and between the landscape and the claim
// (resume claims before attempt 1).
func TestContinuationSurvivesTheKill(t *testing.T) {
	h := open(t)
	a := h.stoppedNow(t, goalFlaky)
	if _, err := h.after(t, a, goalFlaky+" (retry)", "after_continuation", okCall); !errors.Is(err, ErrCrashed) {
		t.Fatalf("err=%v", err)
	}
	h.restart()
	if h.count(KindContinuation) != 1 || h.count(KindRunAttempt) != 1 {
		t.Fatalf("records: continuation=%d attempts=%d", h.count(KindContinuation), h.count(KindRunAttempt))
	}
	reps, err := h.driver(scripted(toolless, okCall), nil).Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("resume: %v %d", err, len(reps))
	}
	led := h.ledger()
	b := led.Runs[reps[0].Run]
	if h.count(KindContinuation) != 1 || b.Continuation == nil || Stopped(b) || !strings.Contains(string(h.requestOf(t, b.Latest())), "## Continues prior run ("+HandleOf(a.Run)+", after)") {
		t.Fatalf("resumed continuation: n=%d %+v %+v", h.count(KindContinuation), b.Continuation, MissionOf(b))
	}
	// the rerun path, killed after the landscape: the resume claims
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeBlocked, closureUnsure})
	d := h.agenda(exec, judge)
	d.Fresh = true
	if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	src := h.newestRun(t)
	sc := scripted(toolless, invoke.ScriptedCall{Response: []byte(landRerun)}, invoke.ScriptedCall{Response: []byte("Summary: flat")})
	dd := h.driver(sc, nil)
	dd.CrashAt = "after_landscape"
	if _, err := dd.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("err=%v", err)
	}
	h.restart()
	if h.count(KindContinuation) != 1 {
		t.Fatalf("the crash landed after the claim: %d", h.count(KindContinuation))
	}
	reps, err = h.driver(sc, nil).Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("resume: %v %d", err, len(reps))
	}
	led = h.ledger()
	e := led.Runs[reps[0].Run]
	if h.count(KindContinuation) != 2 || e.Continuation == nil || e.Continuation.Source != src.Run || e.Continuation.How != ContinuedRerun || Stopped(e) {
		t.Fatalf("resumed rerun continuation: n=%d %+v %+v", h.count(KindContinuation), e.Continuation, MissionOf(e))
	}
}

// Forged continuations the fold refuses. Each forgery gets its own
// history (pattern 120).
func TestForgedContinuationIsRefused(t *testing.T) {
	// setup: A stopped, B continued it and achieved; G is a taken-in goal
	// that follows the given run (not yet started)
	setup := func(t *testing.T, follow func(a, b *RunState) *RunState) (*harness, *RunState, *RunState, *Goal) {
		t.Helper()
		h := open(t)
		a := h.stoppedNow(t, goalFlaky)
		b, err := h.after(t, a, goalFlaky+" (retry)", "", okCall)
		if err != nil {
			t.Fatal(err)
		}
		ref, err := h.st.Put(thought.Goal, []byte(goalFlaky+" (forged)"))
		if err != nil {
			t.Fatal(err)
		}
		g, fam := Intake([]byte(goalFlaky+" (forged)"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		p := follow(a, b)
		g.Parent, g.Root = p.Goal.ID, p.Root
		if err := IntakeCommand(ctxBg, h.j, nil, g, fam); err != nil {
			t.Fatal(err)
		}
		return h, a, b, g
	}
	claim := func(h *harness, g *Goal, source record.RunID, how string) *Continuation {
		run := record.RunID(record.NewID())
		return &Continuation{Header: header(runRef(run), run, 0, "continuation/1"), Goal: g.ID, Source: source, How: how, AsOf: h.j.Head()}
	}
	followA := func(a, b *RunState) *RunState { return a }
	followB := func(a, b *RunState) *RunState { return b }
	t.Run("the honest refusal is accepted (control)", func(t *testing.T) {
		h, a, b, g := setup(t, followA)
		c := claim(h, g, a.Run, ContinuedAfter)
		c.Refused = "run " + HandleOf(a.Run) + " was continued by " + HandleOf(b.Run) + ", which finished: follow " + HandleOf(b.Run) + " instead"
		if err := forge(t, h, "forge/c0", c); err != nil {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a claim on a source another run already continued", func(t *testing.T) {
		h, a, _, g := setup(t, followA)
		if err := forge(t, h, "forge/c1", claim(h, g, a.Run, ContinuedAfter)); err == nil || !strings.Contains(err.Error(), `records refusal "" but the source's state`) {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a refusal that is not the source's state", func(t *testing.T) {
		h, a, _, g := setup(t, followA)
		c := claim(h, g, a.Run, ContinuedAfter)
		c.Refused = "run " + HandleOf(a.Run) + " has not stopped (attempt 1 executing)"
		if err := forge(t, h, "forge/c2", c); err == nil || !strings.Contains(err.Error(), "records refusal") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a source the run does not follow", func(t *testing.T) {
		h, _, b, g := setup(t, followA)
		if err := forge(t, h, "forge/c3", claim(h, g, b.Run, ContinuedAfter)); err == nil || !strings.Contains(err.Error(), "but the run follows") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("how contradicts the lineage", func(t *testing.T) {
		h, a, _, g := setup(t, followA)
		if err := forge(t, h, "forge/c4", claim(h, g, a.Run, ContinuedRerun)); err == nil || !strings.Contains(err.Error(), "but the run follows") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a claim on a run that finished", func(t *testing.T) {
		h, _, b, g := setup(t, followB)
		if err := forge(t, h, "forge/c5", claim(h, g, b.Run, ContinuedAfter)); err == nil || !strings.Contains(err.Error(), "which finished: nothing to continue") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("decided over a head not its own", func(t *testing.T) {
		// after its own record, and over a stale prefix: the claim is
		// decided over the head it is appended to, nothing else
		for name, asOf := range map[string]uint64{"after": 1 << 62, "stale": 1} {
			h, a, b, g := setup(t, followA)
			c := claim(h, g, a.Run, ContinuedAfter)
			c.Refused = "run " + HandleOf(a.Run) + " was continued by " + HandleOf(b.Run) + ", which finished: follow " + HandleOf(b.Run) + " instead"
			c.AsOf = asOf
			if err := forge(t, h, "forge/c6", c); err == nil || !strings.Contains(err.Error(), "decided over head") {
				t.Fatalf("%s: err=%v", name, err)
			}
		}
	})
	t.Run("two claims on one source in one command", func(t *testing.T) {
		// a second stopped run, two goals following it, two claims decided
		// over the same head in one command: the second was not decided
		// over the first. Control: one claim alone is accepted.
		h, _, _, _ := setup(t, followA)
		a2 := h.stoppedNow(t, "Rotate the API key")
		mk := func(text string) *Goal {
			ref, _ := h.st.Put(thought.Goal, []byte(text))
			g, fam := Intake([]byte(text), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
			g.Parent, g.Root = a2.Goal.ID, a2.Root
			if err := IntakeCommand(ctxBg, h.j, nil, g, fam); err != nil {
				t.Fatal(err)
			}
			return g
		}
		g1, g2 := mk("Rotate the API key (x)"), mk("Rotate the API key (y)")
		if err := forge(t, h, "forge/c9", claim(h, g1, a2.Run, ContinuedAfter), claim(h, g2, a2.Run, ContinuedAfter)); err == nil || !strings.Contains(err.Error(), "decided over head") {
			t.Fatalf("err=%v", err)
		}
		h2, _, _, _ := setup(t, followA)
		a3 := h2.stoppedNow(t, "Rotate the API key")
		ref, _ := h2.st.Put(thought.Goal, []byte("Rotate the API key (z)"))
		g3, fam := Intake([]byte("Rotate the API key (z)"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		g3.Parent, g3.Root = a3.Goal.ID, a3.Root
		if err := IntakeCommand(ctxBg, h2.j, nil, g3, fam); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h2, "forge/c9b", claim(h2, g3, a3.Run, ContinuedAfter)); err != nil {
			t.Fatalf("the control claim was refused: %v", err)
		}
	})
	t.Run("a second run for a goal another run started", func(t *testing.T) {
		h, _, b, _ := setup(t, followA)
		// B's goal, started again by a fresh run: the attempt copies B's
		// (the landscape-less --after shape needs no landscape record)
		lled, _ := learn.Fold(h.j.Production())
		run := record.RunID(record.NewID())
		rs0 := &RunState{Run: run, Goal: b.Goal, Root: b.Root}
		pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs0), Standing: learn.Selectable})
		pol.Header = header(runRef(run), run, 1, "policy_selection/1")
		d := h.driver(scripted(toolless), nil)
		d.validate()
		cfg, _ := d.config(LaneNow, pol)
		att := &RunAttempt{Header: header(runRef(run), run, 1, "run_attempt/1"), Goal: b.Goal.ID, Family: b.Family.ID, Config: cfg}
		recs := []record.Record{pol}
		for i, rule := range lled.PolicyRules(pol) {
			recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, 1, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
		}
		recs = append(recs, att, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), To: Created})
		if err := forge(t, h, "forge/c10", recs...); err == nil || !strings.Contains(err.Error(), "already started") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a claim after the run started", func(t *testing.T) {
		h, _, b, _ := setup(t, followA)
		c := *b.Continuation
		c.ID, c.Seq = record.NewID(), 0
		if err := forge(t, h, "forge/c7", &c); err == nil || !strings.Contains(err.Error(), "after the run started") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("attempt 1 with no claim on a stopped source", func(t *testing.T) {
		h, _, b, _ := setup(t, followA)
		// a second stopped run, uncontinued; a goal following it; then an
		// attempt that starts without the claim the engine now records
		a2 := h.stoppedNow(t, "Rotate the API key")
		ref, _ := h.st.Put(thought.Goal, []byte("Rotate the API key (forged)"))
		g, fam := Intake([]byte("Rotate the API key (forged)"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		g.Parent, g.Root = a2.Goal.ID, a2.Root
		if err := IntakeCommand(ctxBg, h.j, nil, g, fam); err != nil {
			t.Fatal(err)
		}
		_ = b
		lled, _ := learn.Fold(h.j.Production())
		run := record.RunID(record.NewID())
		rs0 := &RunState{Run: run, Goal: g, Root: g.Root}
		pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs0), Standing: learn.Selectable})
		pol.Header = header(runRef(run), run, 1, "policy_selection/1")
		d := h.driver(scripted(toolless), nil)
		d.validate()
		cfg, _ := d.config(LaneNow, pol)
		att := &RunAttempt{Header: header(runRef(run), run, 1, "run_attempt/1"), Goal: g.ID, Family: fam.ID, Config: cfg}
		recs := []record.Record{pol}
		for i, rule := range lled.PolicyRules(pol) {
			recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, 1, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
		}
		recs = append(recs, att, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), To: Created})
		if err := forge(t, h, "forge/c8", recs...); err == nil || !strings.Contains(err.Error(), "started with no continuation record") {
			t.Fatalf("err=%v", err)
		}
	})
}

// A run's recorded outcome and its continuation record bind both ways: a
// refused claim ends on exactly its refusal, on whichever attempt records
// (a resumed one too); an outcome that says otherwise, and a refusal
// outcome on a run with no record, are forged. One history per forgery.
func TestContinuationRefusalBindsTheOutcome(t *testing.T) {
	// A stopped; B continued it and finished; C's claim was refused and C
	// died mid-attempt, then resumed: its attempt 2 records the refusal.
	// The returned transition is the honest recorded one, the template
	// the forgeries launder.
	setup := func(t *testing.T) (*harness, *RunState, *Transition) {
		t.Helper()
		h := open(t)
		a := h.stoppedNow(t, goalFlaky)
		if _, err := h.after(t, a, goalFlaky+" (retry)", "", okCall); err != nil {
			t.Fatal(err)
		}
		c, err := h.after(t, a, goalFlaky+" (again)", "after_executing", okCall)
		if !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		h.restart()
		if c.Continuation == nil || c.Continuation.Refused == "" {
			t.Fatalf("c: %+v", c.Continuation)
		}
		if _, err := h.driver(scripted(toolless, okCall), nil).Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		rc := h.ledger().Runs[c.Run]
		rec := rc.Latest().Has(Recorded)
		if rec == nil || rec.Outcome.Reason != "continuation refused: "+c.Continuation.Refused || rc.Latest().Attempt.Attempt != 2 {
			t.Fatalf("resumed refusal: %+v", rec)
		}
		return h, a, rec
	}
	t.Run("a refused run recording another outcome", func(t *testing.T) {
		h, a, recorded := setup(t)
		// D: refused, killed at the judged seam; then the recorded
		// transition with a laundered reason
		d, err := h.after(t, a, goalFlaky+" (third)", "after_judged", okCall)
		if !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		h.restart()
		if rd := h.ledger().Runs[d.Run]; rd.Continuation == nil || rd.Continuation.Refused == "" || rd.Latest().Current() != Judged {
			t.Fatalf("d: %+v at %s", rd.Continuation, rd.Latest().Current())
		}
		forged := *recorded
		forged.ID, forged.Seq, forged.RunID, forged.Attempt, forged.Subject = record.NewID(), 0, d.Run, 1, runRef(d.Run)
		out := *recorded.Outcome
		out.Reason = "backend exited 1"
		forged.Outcome = &out
		if err := forge(t, h, "forge/t1", &forged); err == nil || !strings.Contains(err.Error(), "without ending on it") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a refusal outcome on a run with no record", func(t *testing.T) {
		h, _, recorded := setup(t)
		// E: a plain fresh run killed at the judged seam; then C's refusal
		// outcome on it
		e := h.driver(scripted(toolless, okCall), nil)
		e.Fresh, e.CrashAt = true, "after_judged"
		if _, err := e.Run(ctxBg, []byte("Rotate the API key"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatalf("err=%v", err)
		}
		h.restart()
		re := h.newestRun(t)
		if re.Continuation != nil || re.Latest().Current() != Judged {
			t.Fatalf("e: %+v at %s", re.Continuation, re.Latest().Current())
		}
		twin := *recorded
		twin.ID, twin.Seq, twin.RunID, twin.Attempt, twin.Subject = record.NewID(), 0, re.Run, 1, runRef(re.Run)
		if err := forge(t, h, "forge/t2", &twin); err == nil || !strings.Contains(err.Error(), "refusal that was not recorded") {
			t.Fatalf("err=%v", err)
		}
	})
}

// A source whose execution completed but whose closure the judge resolved
// not_achieved stopped: the continuation claims it and says so.
func TestContinuationOfANotAchievedClosure(t *testing.T) {
	h := open(t)
	closureNo := "```json\n" + `{"outcome": {"type": "choice", "choice": "not_achieved", "confidence": 0.9, "why": "the summary omits the revenue line", "falsifiers": []}}` + "\n```"
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: costs up"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureNo})
	d := h.agenda(exec, judge)
	d.Fresh = true
	if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	if m := MissionOf(a); m.Terminal != "complete" || m.Closure != "not_achieved" || !Stopped(a) {
		t.Fatalf("a: %+v stopped=%v", m, Stopped(a))
	}
	b, err := h.after(t, a, goalQuarterly+" (with the revenue line)", "", okCall)
	if err != nil {
		t.Fatal(err)
	}
	if b.Continuation == nil || b.Continuation.Refused != "" || b.Continuation.Source != a.Run {
		t.Fatalf("b: %+v", b.Continuation)
	}
	if rel := string(b.Related); !strings.Contains(rel, "It stopped: execution complete, closure not_achieved (confidence 0.90)\n") || !strings.Contains(rel, "Where its steps ended: 1 done, 2 done\n") {
		t.Fatalf("block: %q", rel)
	}
}

// The terminal head is the FIRST terminal transition: a later ack
// (delivered → delivered, user_acknowledged) does not move it, so a claim
// decided between the two still re-derives.
func TestContinuationTerminalAtSurvivesTheAck(t *testing.T) {
	h := open(t)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"}), nil)
	d.Fresh = true
	rep, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: UserAcknowledged})
	if err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	at := a.TerminalAt
	if at == 0 || rep.Token == "" {
		t.Fatalf("terminal_at=%d token=%q", at, rep.Token)
	}
	head := h.j.Head()
	if _, _, err := Ack(ctxBg, h.j, h.st, rep.Delivery, rep.Token); err != nil {
		t.Fatal(err)
	}
	led := h.ledger()
	if got := led.Runs[a.Run].TerminalAt; got != at {
		t.Fatalf("terminal_at moved: %d → %d", at, got)
	}
	if stopped, err := Continuable(led, a.Run, head); err != nil || !stopped {
		t.Fatalf("as of the head before the ack: %v %v", stopped, err)
	}
}

func TestContinuationWire(t *testing.T) {
	run := record.RunID(record.NewID())
	good := func() *Continuation {
		return &Continuation{Header: header(runRef(run), run, 0, "continuation/1"), Goal: record.NewID(), Source: "other-run", How: ContinuedAfter, AsOf: 7}
	}
	if err := good().ValidateWire(); err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(good())
	if err != nil {
		t.Fatal(err)
	}
	var back Continuation
	if err := json.Unmarshal(b, &back); err != nil || back.ValidateWire() != nil || !reflect.DeepEqual(back.Source, record.RunID("other-run")) || back.AsOf != 7 {
		t.Fatalf("round trip: %v %+v", err, back)
	}
	for name, mut := range map[string]func(*Continuation){
		"attempt 1":           func(c *Continuation) { c.Attempt = 1 },
		"subject not the run": func(c *Continuation) { c.Subject = record.Ref{Kind: "goal", ID: "x"} },
		"no goal":             func(c *Continuation) { c.Goal = "" },
		"source is itself":    func(c *Continuation) { c.Source = run },
		"no source":           func(c *Continuation) { c.Source = "" },
		"how out of vocab":    func(c *Continuation) { c.How = "related" },
		"as_of zero":          func(c *Continuation) { c.AsOf = 0 },
		"refused untrimmed":   func(c *Continuation) { c.Refused = " x" },
	} {
		c := good()
		mut(c)
		if c.ValidateWire() == nil {
			t.Errorf("%s accepted", name)
		}
	}
	// the door executes the vocabulary too
	h := open(t)
	bad := good()
	bad.How = "related"
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "wire/1", Epoch: h.j.Epoch(), Records: []record.Record{bad}}); err == nil {
		t.Fatal("the door accepted a continuation out of vocabulary")
	}
}
