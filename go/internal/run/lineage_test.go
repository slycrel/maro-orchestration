package run

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// scopedLesson commits a provisional lesson at a lineage scope.
func (h *harness) scopedLesson(t *testing.T, text string, scope learn.ScopePath) learn.ItemRev {
	t.Helper()
	ref, err := h.st.Put(thought.LessonText, []byte(text))
	if err != nil {
		t.Fatal(err)
	}
	item := learn.LearnedID(record.NewID())
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, LearnedKind: learn.Lesson, Scope: scope, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	x := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, Revision: r.ID, From: learn.Candidate, To: learn.Provisional, Actor: learn.ActorOperator, Why: "test"}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "lesson/" + string(item), Epoch: h.j.Epoch(), Records: []record.Record{r, x}}); err != nil {
		t.Fatal(err)
	}
	return learn.ItemRev{Item: item, Revision: r.ID}
}

// newestRun is the run the last Run call made.
func (h *harness) newestRun(t *testing.T) *RunState {
	t.Helper()
	var newest *RunState
	for _, rs := range h.ledger().Runs {
		if newest == nil || rs.Latest().Attempt.Seq > newest.Latest().Attempt.Seq {
			newest = rs
		}
	}
	if newest == nil {
		t.Fatal("no runs")
	}
	return newest
}

// A goal that follows a prior run joins its lineage: recall walks own →
// parent → root → workspace, so a lesson scoped to the lineage root reaches
// every goal in the lineage and no goal outside it; the recall record says
// scope excluded it. The lineage is on the goal record and the fold refuses
// a goal that follows a ghost, a foreign root, or a replay/fork goal.
func TestLineageScopesRecall(t *testing.T) {
	h := open(t)
	run := func(after *Lineage, goal string) *RunState {
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
		d.After = after
		if _, err := d.Run(ctxBg, []byte(goal), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		return h.newestRun(t)
	}
	a := run(nil, "first question")
	if a.Goal.Parent != "" || a.Goal.Root != a.Goal.ID {
		t.Fatalf("a root goal: %+v", a.Goal)
	}
	ir := h.scopedLesson(t, "The answer lives in the second paragraph.", learn.ScopeGoal(a.Goal.ID))

	lin, err := LineageOf(h.ledger(), HandleOf(a.Run))
	if err != nil || lin.Goal != a.Goal.ID || lin.Root != a.Goal.ID {
		t.Fatalf("lineage of a: %+v %v", lin, err)
	}
	b := run(lin, "follow-up question")
	ab := b.Latest()
	if b.Goal.Parent != a.Goal.ID || b.Goal.Root != a.Goal.ID {
		t.Fatalf("b's lineage: %+v", b.Goal)
	}
	wantScope := []learn.ScopePath{learn.ScopeGoal(b.Goal.ID), learn.ScopeGoal(a.Goal.ID), learn.ScopeWorkspace}
	if len(ab.Recall.Scope) != 3 || ab.Recall.Scope[0] != wantScope[0] || ab.Recall.Scope[1] != wantScope[1] || ab.Recall.Scope[2] != wantScope[2] {
		t.Fatalf("b's recall scope %v, want %v", ab.Recall.Scope, wantScope)
	}
	if len(ab.Recall.Included) != 1 || ab.Recall.Included[0] != ir || !strings.Contains(string(h.requestOf(t, ab)), "second paragraph") {
		t.Fatalf("lineage lesson did not reach b: %+v", ab.Recall)
	}

	// a stranger: the same lesson is excluded by scope, and the record says so
	c := run(nil, "unrelated question")
	ac := c.Latest()
	if len(ac.Recall.Included) != 0 || ac.Recall.ExcludedCounts["scope"] != 1 || strings.Contains(string(h.requestOf(t, ac)), "second paragraph") {
		t.Fatalf("lineage lesson leaked to a stranger: %+v", ac.Recall)
	}

	// two levels down: d follows b; its root is still a, so the lesson reaches it
	linB, err := LineageOf(h.ledger(), HandleOf(b.Run))
	if err != nil || linB.Goal != b.Goal.ID || linB.Root != a.Goal.ID {
		t.Fatalf("lineage of b: %+v %v", linB, err)
	}
	d := run(linB, "third question")
	ad := d.Latest()
	if d.Goal.Parent != b.Goal.ID || d.Goal.Root != a.Goal.ID || len(ad.Recall.Included) != 1 || len(ad.Recall.Scope) != 4 {
		t.Fatalf("d's lineage/recall: %+v %+v", d.Goal, ad.Recall)
	}
	for _, l := range Inspect(d) {
		if strings.HasPrefix(l, "goal ") && !strings.Contains(l, "follows "+string(b.Goal.ID)+", root "+string(a.Goal.ID)) {
			t.Fatalf("readout does not name the lineage: %s", l)
		}
	}
	if _, err := LineageOf(h.ledger(), "00000000"); err == nil || !strings.Contains(err.Error(), "no run with handle") {
		t.Fatalf("ghost handle resolved: %v", err)
	}

	// the fold refuses a goal that follows a ghost, or claims a root that is
	// not its parent's; the door refuses a goal that follows itself
	forge := func(parent, root record.RecordID) error {
		g := *a.Goal
		g.ID = record.NewID()
		g.Subject = record.Ref{Kind: "goal", ID: string(g.ID)}
		g.Seq = 0
		g.Parent, g.Root = parent, root
		fam := *a.Family
		fam.ID = record.NewID()
		fam.Seq = 0
		fam.Subject = g.Subject
		fam.Goal = g.ID
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forge/" + string(g.ID), Epoch: h.j.Epoch(), Records: []record.Record{&g, &fam}})
		if err != nil {
			return err
		}
		_, err = Fold(h.j.Production(), h.st)
		return err
	}
	if err := forge(record.NewID(), a.Goal.ID); err == nil || !strings.Contains(err.Error(), "follows") {
		t.Fatalf("goal following a ghost folded: %v", err)
	}
}

// Each forgery on its own journal: a foreign root, and a self-parent at the door.
func TestFoldRefusesForeignLineageRoot(t *testing.T) {
	h := open(t)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	if _, err := d.Run(ctxBg, []byte("first"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.newestRun(t)
	d2 := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	if _, err := d2.Run(ctxBg, []byte("second, unrelated"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	b := h.newestRun(t)
	// follows a but claims b's root
	g := *a.Goal
	g.ID = record.NewID()
	g.Subject = record.Ref{Kind: "goal", ID: string(g.ID)}
	g.Seq = 0
	g.Parent, g.Root = a.Goal.ID, b.Goal.ID
	fam := *a.Family
	fam.ID = record.NewID()
	fam.Seq = 0
	fam.Subject = g.Subject
	fam.Goal = g.ID
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forge/" + string(g.ID), Epoch: h.j.Epoch(), Records: []record.Record{&g, &fam}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), "follows") {
		t.Fatalf("foreign root folded: %v", err)
	}
	// the door: a goal cannot follow itself
	self := *a.Goal
	self.ID = record.NewID()
	self.Subject = record.Ref{Kind: "goal", ID: string(self.ID)}
	self.Seq = 0
	self.Parent, self.Root = self.ID, self.ID
	if err := self.ValidateWire(); err == nil || !strings.Contains(err.Error(), "follow itself") {
		t.Fatalf("self-parent passed the door: %v", err)
	}
	// a replay goal still needs its arm; a plain goal with a parent is no longer refused at the door
	plain := *a.Goal
	plain.ID = record.NewID()
	plain.Subject = record.Ref{Kind: "goal", ID: string(plain.ID)}
	plain.Seq = 0
	plain.Parent = a.Goal.ID
	if err := plain.ValidateWire(); err != nil {
		t.Fatalf("a following goal refused at the door: %v", err)
	}
	// a driver cannot both replay and follow
	d3 := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	d3.After = &Lineage{Goal: a.Goal.ID, Root: a.Goal.ID}
	d3.Replay = &ReplayContext{}
	if _, err := d3.Run(ctxBg, []byte("x"), DeliveryPolicy{Required: TransportAccepted}); err == nil || !strings.Contains(err.Error(), "replay") {
		t.Fatalf("replay+follow accepted: %v", err)
	}
}

// The fold binds a selection's recorded scope to the goal's lineage walk:
// the learn fold re-derives a selection from the scope it RECORDED, so a
// forged recall that omits the parent, reorders the walk, or names a
// foreign lineage re-derives consistently and only the run fold — which
// holds the goal — can refuse it. The rule is exercised directly on the
// honest facts with one field mutated (the honest driver satisfies it by
// construction, so a journal-level forgery would need a whole second run).
func TestFoldBindsSelectionScopeToTheLineage(t *testing.T) {
	h := open(t)
	run := func(after *Lineage, goal string) *RunState {
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
		d.After = after
		if _, err := d.Run(ctxBg, []byte(goal), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		return h.newestRun(t)
	}
	a := run(nil, "first question")
	lin, err := LineageOf(h.ledger(), HandleOf(a.Run))
	if err != nil {
		t.Fatal(err)
	}
	b := run(lin, "follow-up question")
	c := run(lin, "another follow-up") // b's sibling: a lineage member, but not on b's walk
	own, parent, ws := learn.ScopeGoal(b.Goal.ID), learn.ScopeGoal(a.Goal.ID), learn.ScopeWorkspace
	honest := b.Latest().Recall.Scope
	if err := scopeMatches(b.Goal, honest); err != nil {
		t.Fatalf("honest recall scope refused: %v", err)
	}
	if err := scopeMatches(b.Goal, b.Latest().Policy.Scope); err != nil {
		t.Fatalf("honest policy scope refused: %v", err)
	}
	for name, got := range map[string][]learn.ScopePath{
		"omits the parent":  {own, ws},
		"reordered":         {parent, own, ws},
		"foreign lineage":   {own, learn.ScopeGoal(c.Goal.ID), ws},
		"sibling appended":  {own, parent, learn.ScopeGoal(c.Goal.ID), ws},
		"workspace only":    {ws},
		"parent's own walk": a.Latest().Recall.Scope,
		"duplicated parent": {own, parent, parent, ws},
		"no workspace":      {own, parent},
	} {
		if err := scopeMatches(b.Goal, got); err == nil || !strings.Contains(err.Error(), "lineage walk") {
			t.Fatalf("%s: forged scope %v accepted: %v", name, got, err)
		}
	}
}

// A goal that follows a prior run is resumed after a crash like any
// production run: only fork children and replay arms are driven by
// something other than the resume sweep. Both sweeps — the admitted-but-
// unstarted goal and the started, non-terminal run — must take it.
func TestResumeDrivesAFollowedRun(t *testing.T) {
	for _, crashAt := range []string{"after_intake", "after_start"} {
		t.Run(crashAt, func(t *testing.T) {
			h := open(t)
			mk := func() *Driver {
				return h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
			}
			if _, err := mk().Run(ctxBg, []byte("first question"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
				t.Fatal(err)
			}
			a := h.newestRun(t)
			lin, err := LineageOf(h.ledger(), HandleOf(a.Run))
			if err != nil {
				t.Fatal(err)
			}
			d := mk()
			d.After, d.CrashAt = lin, crashAt
			if _, err := d.Run(ctxBg, []byte("follow-up question"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatalf("fixture: %v", err)
			}
			h.restart()
			reps, err := mk().Resume(ctxBg)
			if err != nil || len(reps) != 1 || reps[0].Mission.Outcome != MissionDelivered {
				t.Fatalf("resume did not drive the followed goal: %v %+v", err, reps)
			}
			b := h.newestRun(t)
			if b.Goal.Parent != a.Goal.ID || b.Goal.Root != a.Goal.ID || !b.Terminal() {
				t.Fatalf("resumed run: %+v terminal=%v", b.Goal, b.Terminal())
			}
			if len(h.ledger().Unstarted) != 0 {
				t.Fatal("a goal is still unstarted after resume")
			}
		})
	}
}
