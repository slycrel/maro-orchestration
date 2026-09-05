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

const (
	goalQuarterly = "Summarize the quarterly numbers into a short report"
	goalFollowUp  = "Follow-up on the quarterly numbers report: which line moved most?"
	goalHaiku     = "Write a haiku about autumn leaves"
	landRelated   = `{"relation": "related", "run": 1, "reason": "the same report, one question deeper"}`
	landRerun     = `{"relation": "rerun", "run": 1, "reason": "the same ask, word for word"}`
)

// now drives a NOW-lane run on a scripted executor whose calls are, in
// order, the landscape judge answer (when candidates exist) and the answer.
func (h *harness) now(t *testing.T, goal string, fresh bool, calls ...string) (*RunState, *invoke.Scripted) {
	t.Helper()
	var sc []invoke.ScriptedCall
	for _, c := range calls {
		sc = append(sc, invoke.ScriptedCall{Response: []byte(c)})
	}
	b := scripted(toolless, sc...)
	d := h.driver(b, nil)
	d.Fresh = fresh
	if _, err := d.Run(ctxBg, []byte(goal), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	return h.newestRun(t), b
}

// The landscape (feature-related-runs): before its first attempt a goal
// with no operator-chosen lineage looks at the prior runs and DECIDES —
// fresh, related, or rerun — by one recorded judge call over deterministic
// candidates. A follow-up worded like a prior run follows it (lineage, so
// scoped memory reaches it; the prior's answer rides into its requests);
// unrelated wording is fresh with no call; the same wording is a rerun that
// offers the prior's plan to the planner; --fresh skips the look.
func TestLandscapeDecidesTheRelation(t *testing.T) {
	h := open(t)
	// A: the first run of the workspace — nothing to relate to, no call
	exec, judge := agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{intentClear, planTwo, judgeDone, judgeDone, closureYes})
	if _, err := h.agenda(exec, judge).Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	if ls := a.Landscape; ls == nil || ls.Rule != LandscapeNoCandidates || ls.Relation != RelationFresh || ls.Scanned != 0 || ls.Judge != "" || a.Parent != "" || a.Root != a.Goal.ID {
		t.Fatalf("first run's landscape: %+v parent=%s root=%s", a.Landscape, a.Parent, a.Root)
	}
	if len(judge.Seen) != 5 {
		t.Fatalf("a landscape with no candidates made a call: %d judge calls", len(judge.Seen))
	}
	// B: a follow-up worded like A, no --after: the judge says related
	b, bb := h.now(t, goalFollowUp, false, landRelated, "The revenue line")
	ls := b.Landscape
	if ls == nil || ls.Rule != LandscapeJudge || ls.Relation != RelationRelated || ls.Chosen != a.Run || len(ls.Candidates) != 1 || ls.Candidates[0].Run != a.Run || ls.Scanned != 1 || ls.BelowFloor != 0 || ls.Judge == "" || ls.Reason != "the same report, one question deeper" {
		t.Fatalf("follow-up landscape: %+v", ls)
	}
	if b.Parent != a.Goal.ID || b.Root != a.Goal.ID {
		t.Fatalf("follow-up did not join A's lineage: parent=%s root=%s", b.Parent, b.Root)
	}
	if ls.PromptVer != LandscapePromptVer {
		t.Fatalf("prompt version: %d", ls.PromptVer)
	}
	if req := bb.Seen[0]; req.Tools || !strings.Contains(string(req.Prompt), "Candidate 1 (run "+HandleOf(a.Run)+", similarity") || !strings.Contains(string(req.Prompt), "New goal:\n"+goalFollowUp) || !strings.Contains(string(req.Prompt), landscapeContract[LandscapePromptVer]) {
		t.Fatalf("judge request: tools=%v %q", req.Tools, req.Prompt)
	}
	want := "## Related prior run (" + HandleOf(a.Run) + ", related)\nIts goal: " + goalQuarterly + "\nIts answer:\n"
	if !strings.Contains(string(b.Related), want) || !strings.Contains(string(b.Related), "Summary: revenue flat") || strings.Contains(string(b.Related), "Its plan") {
		t.Fatalf("related context: %q", b.Related)
	}
	if req := h.requestOf(t, b.Latest()); !strings.Contains(string(req), string(b.Related)) {
		t.Fatalf("the NOW request does not carry the related context: %q", req)
	}
	if sc := b.Latest().Recall.Scope; len(sc) != 3 || sc[0] != learn.ScopeGoal(b.Goal.ID) || sc[1] != learn.ScopeGoal(a.Goal.ID) || sc[2] != learn.ScopeWorkspace {
		t.Fatalf("recall scope did not walk to A: %v", sc)
	}
	if lines := strings.Join(Inspect(b), "\n"); !strings.Contains(lines, "landscape: related run "+HandleOf(a.Run)+" (judge; 1 candidate(s) of 1 scanned, 0 below the floor): the same report") || !strings.Contains(lines, "follows "+string(a.Goal.ID)) {
		t.Fatalf("inspect: %s", lines)
	}
	// C: unrelated wording — nothing above the floor: fresh, no call
	c, cb := h.now(t, goalHaiku, false, "leaves let go")
	if ls := c.Landscape; ls.Rule != LandscapeNoCandidates || ls.Relation != RelationFresh || ls.Scanned != 2 || ls.BelowFloor != 2 || len(ls.Candidates) != 0 || ls.Judge != "" || c.Root != c.Goal.ID || len(c.Related) != 0 || len(cb.Seen) != 1 {
		t.Fatalf("unrelated landscape: %+v calls=%d", ls, len(cb.Seen))
	}
	// D: A's wording again, on the agenda lane: a rerun — follows A, and
	// the planner is offered A's plan; the intent call sees the context too
	exec, judge = agendaBackends([]string{"Collected 12 rows", "Summary: revenue flat"}, []string{landRerun, intentClear, planTwo, judgeDone, judgeDone, closureYes})
	if _, err := h.agenda(exec, judge).Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	d := h.newestRun(t)
	if ls := d.Landscape; ls.Rule != LandscapeJudge || ls.Relation != RelationRerun || ls.Chosen != a.Run || len(ls.Candidates) != 2 || ls.Candidates[0].Run != a.Run || ls.Candidates[0].Similarity != 1 || ls.Candidates[1].Run != b.Run || ls.Scanned != 3 || ls.BelowFloor != 1 {
		t.Fatalf("rerun landscape: %+v", ls)
	}
	if d.Parent != a.Goal.ID || d.Root != a.Goal.ID {
		t.Fatalf("rerun lineage: parent=%s root=%s", d.Parent, d.Root)
	}
	plan := "Its plan (reuse or revise):\n1. Collect the numbers\n2. Write the summary\n"
	if !strings.Contains(string(d.Related), "## Related prior run ("+HandleOf(a.Run)+", rerun)") || !strings.Contains(string(d.Related), plan) {
		t.Fatalf("rerun context: %q", d.Related)
	}
	if !strings.Contains(string(judge.Seen[1].Prompt), string(d.Related)) || !strings.Contains(string(judge.Seen[2].Prompt), plan) {
		t.Fatal("intent/plan requests do not carry the rerun context")
	}
	// E: --fresh skips the look: recorded, no scan, no call
	e, eb := h.now(t, goalQuarterly, true, "fresh answer")
	if ls := e.Landscape; ls.Rule != LandscapeFreshOverride || ls.Relation != RelationFresh || ls.Scanned != 0 || ls.Judge != "" || e.Root != e.Goal.ID || len(eb.Seen) != 1 {
		t.Fatalf("fresh override: %+v calls=%d", ls, len(eb.Seen))
	}
	// F: a judge answer outside the contract decides nothing: fresh, the call recorded
	f, _ := h.now(t, goalQuarterly, false, "I think they're related?", "answer")
	if ls := f.Landscape; ls.Rule != LandscapeUnreadable || ls.Relation != RelationFresh || ls.Chosen != "" || ls.Judge == "" || len(ls.Candidates) != 3 || f.Root != f.Goal.ID || len(f.Related) != 0 {
		t.Fatalf("unreadable judge: %+v", ls)
	}
	// the operator override still wins: --after names the lineage, no landscape
	g := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	g.After = &Lineage{Goal: c.Goal.ID, Root: c.Goal.ID}
	if _, err := g.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	if gr := h.newestRun(t); gr.Landscape != nil || gr.Parent != c.Goal.ID || gr.Root != c.Goal.ID {
		t.Fatalf("--after run: landscape=%v parent=%s", gr.Landscape, gr.Parent)
	}
	// H: a follow-up to the follow-up: follows B, and the root is still A
	hh, _ := h.now(t, goalFollowUp+" And why?", false, landRelated, "Costs rose")
	if ls := hh.Landscape; ls.Chosen != b.Run || ls.Candidates[0].Run != b.Run || hh.Parent != b.Goal.ID || hh.Root != a.Goal.ID {
		t.Fatalf("chained follow-up: chosen=%s parent=%s root=%s", ls.Chosen, hh.Parent, hh.Root)
	}
	if sc := hh.Latest().Recall.Scope; len(sc) != 4 || sc[1] != learn.ScopeGoal(b.Goal.ID) || sc[2] != learn.ScopeGoal(a.Goal.ID) {
		t.Fatalf("chained recall scope: %v", sc)
	}
	if n := h.count(KindLandscape); n != 7 {
		t.Fatalf("landscape records: %d", n)
	}
	// every history above folds; the whole ledger re-derives each decision
	h.restart()
	h.ledger()
}

// A run that dies between its landscape and its first attempt resumes
// from the landscape: the decision is not re-made (one record), and the
// resumed attempt carries the lineage and context the landscape decided.
func TestResumeAfterLandscape(t *testing.T) {
	h := open(t)
	a, _ := h.now(t, goalQuarterly, false, "Summary: revenue flat")
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte(landRelated)}, invoke.ScriptedCall{Response: []byte("never")}), nil)
	d.CrashAt = "after_landscape"
	if _, err := d.Run(ctxBg, []byte(goalFollowUp), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("no crash: %v", err)
	}
	led := h.ledger()
	var dead *RunState
	for _, rs := range led.Runs {
		if rs.Latest() == nil {
			dead = rs
		}
	}
	if dead == nil || dead.Landscape == nil || dead.Landscape.Relation != RelationRelated || dead.Parent != a.Goal.ID || len(led.Unstarted) != 0 {
		t.Fatalf("landscaped run not in the ledger as unstarted-with-landscape: %+v unstarted=%d", dead, len(led.Unstarted))
	}
	// a run that is not terminal is not a candidate for anyone (its wording matches)
	x, _ := h.now(t, goalFollowUp, false, landRelated, "meanwhile")
	if ls := x.Landscape; len(ls.Candidates) != 1 || ls.Candidates[0].Run != a.Run || ls.Scanned != 1 {
		t.Fatalf("a dead run was a candidate: %+v", ls)
	}
	h.restart()
	d2 := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("The revenue line")}), nil)
	reps, err := d2.Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("resume: %v %d", err, len(reps))
	}
	b := h.ledger().Runs[dead.Run]
	if !b.Terminal() || b.Parent != a.Goal.ID || !strings.Contains(string(h.requestOf(t, b.Latest())), "## Related prior run ("+HandleOf(a.Run)) || h.count(KindLandscape) != 3 {
		t.Fatalf("resumed run: terminal=%v parent=%s landscapes=%d", b.Terminal(), b.Parent, h.count(KindLandscape))
	}
}

// The fold re-derives every landscape: the candidates from the ledger as
// of the record's watermark, the judge call as this run's, its request as
// the prompt those candidates render, and the relation as what the answer
// parses to. A forged record — or a run that skips the stage — is refused.
func TestFoldRefusesForgedLandscapes(t *testing.T) {
	seed := func() (*harness, *RunState, *Goal, uint64) {
		h := open(t)
		a, _ := h.now(t, goalQuarterly, false, "Summary: revenue flat")
		ref, _ := h.st.Put(thought.Goal, []byte(goalFollowUp))
		g, fam := Intake([]byte(goalFollowUp), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "goal/" + string(g.ID), Epoch: h.j.Epoch(), Records: []record.Record{g, fam}}); err != nil {
			t.Fatal(err)
		}
		return h, a, g, h.j.Head()
	}
	forge := func(name string, want string, mut func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record) {
		t.Run(name, func(t *testing.T) {
			h, a, g, asOf := seed()
			run := record.RunID(record.NewID())
			ls := &Landscape{Header: header(runRef(run), run, 0, "landscape/1"), Goal: g.ID, AsOf: asOf, Rule: LandscapeNoCandidates, Floor: LandscapeFloor, TopK: LandscapeTopK, Relation: RelationFresh}
			recs := mut(h, a, g, ls)
			if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forge", Epoch: h.j.Epoch(), Records: recs}); err != nil {
				t.Fatalf("door refused what the fold should: %v", err)
			}
			if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), want) {
				t.Fatalf("forged landscape folded: %v", err)
			}
		})
	}
	forge("claims no candidates while one qualifies", "re-derive", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record { return []record.Record{ls} })
	forge("names a judge call that never happened", "judge invocation", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.Rule, ls.Relation, ls.Chosen, ls.Judge = LandscapeJudge, RelationRelated, a.Run, record.NewID()
		ls.Scanned, ls.Candidates = 1, []LandscapeCandidate{{Run: a.Run, Goal: a.Goal.ID, Similarity: Similarity([]byte(goalFollowUp), []byte(goalQuarterly))}}
		return []record.Record{ls}
	})
	forge("a watermark before the candidate was terminal", "re-derive", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.AsOf = a.Latest().Attempt.Seq // after the run began, before it was terminal
		ls.Scanned, ls.Candidates, ls.Rule, ls.Relation, ls.Chosen, ls.Judge = 1, []LandscapeCandidate{{Run: a.Run, Goal: a.Goal.ID, Similarity: Similarity([]byte(goalFollowUp), []byte(goalQuarterly))}}, LandscapeJudge, RelationRelated, a.Run, record.NewID()
		return []record.Record{ls}
	})
	forge("a skipped landscape that scanned", "scanned nothing", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.Rule, ls.Scanned, ls.BelowFloor = LandscapeFreshOverride, 1, 1
		return []record.Record{ls}
	})
	forge("the engine's parameters", "not the engine's", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.Floor = 0.9
		return []record.Record{ls}
	})
	forge("for a goal whose lineage was set at intake", "set at intake", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ref, _ := h.st.Put(thought.Goal, []byte(goalHaiku))
		g2, fam2 := Intake([]byte(goalHaiku), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		g2.Parent, g2.Root = a.Goal.ID, a.Goal.ID
		ls.Goal = g2.ID
		ls.AsOf = h.j.Head() + 2
		return []record.Record{g2, fam2, ls}
	})
	forge("a second landscape after the run started", "after the run started", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.Goal, ls.RunID, ls.Subject = a.Goal.ID, a.Run, runRef(a.Run)
		return []record.Record{ls}
	})
	forge("for a goal that already started", "already started", func(h *harness, a *RunState, g *Goal, ls *Landscape) []record.Record {
		ls.Goal = a.Goal.ID
		return []record.Record{ls}
	})
	// the honest record with one field mutated: every derived field is bound
	t.Run("one field mutated on the honest record", func(t *testing.T) {
		h := open(t)
		a, _ := h.now(t, goalQuarterly, false, "Summary: revenue flat")
		b, _ := h.now(t, goalFollowUp, false, landRelated, "The revenue line")
		led := h.ledger()
		inv, err := invoke.Fold(h.j.Production())
		if err != nil {
			t.Fatal(err)
		}
		// the fold sees only the runs terminal as of the watermark
		runs := map[record.RunID]*RunState{a.Run: led.Runs[a.Run]}
		if err := checkLandscape(b.Landscape, b.Goal, runs, inv, h.st); err != nil {
			t.Fatalf("honest landscape refused: %v", err)
		}
		for name, mut := range map[string]func(x *Landscape){
			"relation":   func(x *Landscape) { x.Relation = RelationRerun },
			"reason":     func(x *Landscape) { x.Reason = "because" },
			"rule":       func(x *Landscape) { x.Rule = LandscapeUnreadable; x.Relation, x.Chosen = RelationFresh, "" },
			"similarity": func(x *Landscape) { x.Candidates[0].Similarity += 0.01 },
			"scanned":    func(x *Landscape) { x.Scanned++ },
			"below":      func(x *Landscape) { x.BelowFloor++ },
			"top_k":      func(x *Landscape) { x.TopK++ },
			"as_of":      func(x *Landscape) { x.AsOf = a.Latest().Attempt.Seq },
			"judge":      func(x *Landscape) { x.Judge = record.NewID() },
			"prompt_ver": func(x *Landscape) { x.PromptVer = 1 }, // asked with the first template, claims the second
			"run":        func(x *Landscape) { x.RunID = a.Run },
		} {
			x := *b.Landscape
			x.Candidates = append([]LandscapeCandidate{}, b.Landscape.Candidates...)
			mut(&x)
			if err := checkLandscape(&x, b.Goal, runs, inv, h.st); err == nil {
				t.Errorf("%s mutated and accepted", name)
			}
		}
	})
	// the cited judge call is bound by run, attempt 0, purpose, and tool-lessness
	t.Run("the judge call mutated", func(t *testing.T) {
		h := open(t)
		a, _ := h.now(t, goalQuarterly, false, "Summary: revenue flat")
		b, _ := h.now(t, goalFollowUp, false, landRelated, "The revenue line")
		led := h.ledger()
		inv, err := invoke.Fold(h.j.Production())
		if err != nil {
			t.Fatal(err)
		}
		runs := map[record.RunID]*RunState{a.Run: led.Runs[a.Run]}
		for name, mut := range map[string]func(x *invoke.Invocation){
			"attempt": func(x *invoke.Invocation) { x.Attempt = 1 },
			"purpose": func(x *invoke.Invocation) { x.Purpose = invoke.PurposeJudge },
			"tools":   func(x *invoke.Invocation) { x.Tools = true },
			"run":     func(x *invoke.Invocation) { x.RunID = a.Run },
		} {
			st := *inv[b.Landscape.Judge]
			ic := *st.Invocation
			mut(&ic)
			st.Invocation = &ic
			forged := map[record.RecordID]*invoke.State{b.Landscape.Judge: &st}
			if err := checkLandscape(b.Landscape, b.Goal, runs, forged, h.st); err == nil {
				t.Errorf("judge %s mutated and accepted", name)
			}
		}
	})
	// a production goal cannot start without its landscape — once the
	// journal holds one; history written before the landscape existed
	// folds as it was written (each run its own root)
	attemptWithout := func(t *testing.T, h *harness, g *Goal) error {
		lled, _ := learn.Fold(h.j.Production())
		run := record.RunID(record.NewID())
		rs0 := &RunState{Run: run, Goal: g, Root: g.ID}
		pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs0), Standing: learn.Selectable})
		pol.Header = header(runRef(run), run, 1, "policy_selection/1")
		d := h.driver(scripted(toolless), nil)
		d.validate()
		cfg, _ := d.config(LaneNow, pol)
		fam := h.ledger().Families[g.ID]
		att := &RunAttempt{Header: header(runRef(run), run, 1, "run_attempt/1"), Goal: g.ID, Family: fam.ID, Config: cfg}
		recs := []record.Record{pol}
		for i, rule := range lled.PolicyRules(pol) {
			recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, 1, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
		}
		recs = append(recs, att, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), To: Created})
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/" + string(run), Epoch: h.j.Epoch(), Records: recs}); err != nil {
			t.Fatal(err)
		}
		_, err := Fold(h.j.Production(), h.st)
		return err
	}
	t.Run("an attempt with no landscape", func(t *testing.T) {
		h, _, g, _ := seed()
		if err := attemptWithout(t, h, g); err == nil || !strings.Contains(err.Error(), "no landscape") {
			t.Fatalf("attempt without a landscape folded: %v", err)
		}
	})
	t.Run("history before the first landscape", func(t *testing.T) {
		h := open(t)
		if _, err := learn.EnsureSeeds(ctxBg, h.j); err != nil {
			t.Fatal(err)
		}
		ref, _ := h.st.Put(thought.Goal, []byte(goalQuarterly))
		g, fam := Intake([]byte(goalQuarterly), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "goal/" + string(g.ID), Epoch: h.j.Epoch(), Records: []record.Record{g, fam}}); err != nil {
			t.Fatal(err)
		}
		if err := attemptWithout(t, h, g); err != nil {
			t.Fatalf("pre-landscape history refused: %v", err)
		}
		led := h.ledger()
		for _, rs := range led.Runs {
			if rs.Landscape != nil || rs.Parent != "" || rs.Root != rs.Goal.ID {
				t.Fatalf("pre-landscape run's lineage: %+v", rs)
			}
		}
		// the runs that come after decide as usual (the forged old run
		// never became terminal, so it is scanned past, not a candidate)
		b, _ := h.now(t, goalFollowUp, false, "The revenue line")
		if b.Landscape.Rule != LandscapeNoCandidates || b.Root != b.Goal.ID {
			t.Fatalf("after history: %+v", b.Landscape)
		}
		// from here the rule holds: a later attempt without a landscape is refused
		ref, _ = h.st.Put(thought.Goal, []byte(goalHaiku))
		g2, fam2 := Intake([]byte(goalHaiku), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "goal/" + string(g2.ID), Epoch: h.j.Epoch(), Records: []record.Record{g2, fam2}}); err != nil {
			t.Fatal(err)
		}
		if err := attemptWithout(t, h, g2); err == nil || !strings.Contains(err.Error(), "no landscape") {
			t.Fatalf("post-landscape attempt without one folded: %v", err)
		}
	})
	// the driver: --after and --fresh contradict
	t.Run("after and fresh contradict", func(t *testing.T) {
		h, a, _, _ := seed()
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
		d.After, d.Fresh = &Lineage{Goal: a.Goal.ID, Root: a.Goal.ID}, true
		if _, err := d.Run(ctxBg, []byte("x"), DeliveryPolicy{Required: TransportAccepted}); err == nil || !strings.Contains(err.Error(), "fresh") {
			t.Fatalf("after+fresh accepted: %v", err)
		}
	})
}

// The judge names the chosen candidate by number or by the run id the
// prompt showed it; anything else, or a number outside the list, is an
// answer outside the contract.
func TestParseLandscapeReadsNumberOrHandle(t *testing.T) {
	cands := []LandscapeCandidate{{Run: "01AAAAAAAAAAAAAAAAAAAAAAAA"}, {Run: "01BBBBBBBBBBBBBBBBBBBBBBBB"}}
	for in, want := range map[string]record.RunID{
		`{"relation": "related", "run": 2, "reason": "x"}`:                                    cands[1].Run,
		`{"relation": "rerun", "run": "1", "reason": "x"}`:                                    cands[0].Run,
		`{"relation": "related", "run": "` + HandleOf(cands[1].Run) + `", "reason": "x"}`:     cands[1].Run,
		`{"relation": "related", "run": "run ` + HandleOf(cands[0].Run) + `", "reason": "x"}`: cands[0].Run,
		`{"relation": "related", "run": "` + string(cands[1].Run) + `", "reason": "x"}`:       cands[1].Run,
		"Sure! ```json\n" + `{"relation": "fresh", "run": 0, "reason": "x"}` + "\n```":        "",
	} {
		rel, run, _, err := ParseLandscape(LandscapePromptVer, []byte(in), cands)
		if err != nil || run != want || (rel == RelationFresh) != (want == "") {
			t.Errorf("%s: %s %s %v", in, rel, run, err)
		}
	}
	// the first template's contract was the number only: a handle under it is unreadable
	if _, _, _, err := ParseLandscape(1, []byte(`{"relation": "related", "run": "`+HandleOf(cands[1].Run)+`", "reason": "x"}`), cands); err == nil {
		t.Error("a handle parsed under the first template's contract")
	}
	if _, run, _, err := ParseLandscape(0, []byte(`{"relation": "related", "run": 2, "reason": "x"}`), cands); err != nil || run != cands[1].Run {
		t.Errorf("absent version is the first: %s %v", run, err)
	}
	for _, in := range []string{
		`{"relation": "related", "run": 3, "reason": "x"}`,
		`{"relation": "related", "run": "deadbeef", "reason": "x"}`,
		`{"relation": "related", "run": 0, "reason": "x"}`,
		`{"relation": "cousin", "run": 1, "reason": "x"}`,
		`related to 1`,
	} {
		if _, _, _, err := ParseLandscape(LandscapePromptVer, []byte(in), cands); err == nil {
			t.Errorf("%s: parsed", in)
		}
	}
}
