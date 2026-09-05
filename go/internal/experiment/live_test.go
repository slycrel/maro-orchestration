package experiment

import (
	"context"
	"errors"
	"io"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/tail"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// judgeFor is a blinded judge: achieved iff the prompt (goal + deliverable
// and nothing else) contains the key.
func judgeFor(key string) *invoke.Keyed {
	return &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
		{Key: key, Answer: `{"outcome":"achieved","confidence":0.9,"why":"matched"}`},
	}, Def: `{"outcome":"not_achieved","confidence":0.9,"why":"no match"}`}
}

// live drives one production goal through an intake that admits (the CLI
// and the process both wire Admit) and returns its run.
func (h *harness) live(text string, admit run.AdmitFunc) *run.RunState {
	return h.liveOn(h.exec, text, admit)
}

func (h *harness) liveOn(b invoke.Backend, text string, admit run.AdmitFunc) *run.RunState {
	h.t.Helper()
	if admit == nil {
		admit = Admit(h.j, h.st)
	}
	d := &run.Driver{J: h.j, Store: h.st, Backend: b, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute, Admit: admit}
	rep, err := d.Run(ctxBg, []byte(text), run.DeliveryPolicy{Required: run.TransportAccepted})
	if err != nil {
		h.t.Fatalf("live run: %v", err)
	}
	return h.state().RunOf(rep.Goal)
}

func (h *harness) openLive(hyp learn.ItemRev, n int) *Experiment {
	h.t.Helper()
	x, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: hyp, Relation: ApplyItem, Live: true, Population: "answer", N: n, Why: "live"})
	if err != nil {
		h.t.Fatal(err)
	}
	return x
}

// Every goal asks for meters: a judge keyed on "8,849 meters" scores
// achievement, and the control's feet answer genuinely misses.
var everestGoals = []string{"What is the height of Mount Everest in meters?", "How high is Everest above sea level, in meters?", "What elevation in meters does Everest reach?", "How many meters tall is Mount Everest?"}

// failOn fails the run when the request carries the key: a treatment that
// breaks execution.
type failOn struct {
	inner invoke.Backend
	key   string
}

func (f *failOn) Capabilities() invoke.Capabilities { return f.inner.Capabilities() }
func (f *failOn) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	if strings.Contains(string(req.Prompt), f.key) {
		return &invoke.Result{Response: []byte("boom"), Terminal: invoke.TerminalFailed, Reason: "keyed failure"}, nil
	}
	return f.inner.Complete(ctx, req, sink)
}

// The live loop (§8a "randomized live is where D11 is established"): a
// candidate lesson is measured over four production goals admitted at
// intake — arms balanced by permuted blocks, the arm forced on the goal
// and its selections, the deliverable presented to the user as always —
// scored by a judge that sees only goal and deliverable, and the
// measurement's transition changes the next production run.
func TestLiveRandomizedAssignment(t *testing.T) {
	s := build(t)
	h := s.h
	x := h.openLive(s.helpful, 4)
	// a goal of another family is not admitted, whatever the room
	if rs := h.live("Write a script file named tally.sh that counts lines.", nil); rs.Goal.Arm != nil || rs.Family.Family != run.FamilyWriteLocal {
		t.Fatalf("write_local goal admitted: %+v", rs.Goal.Arm)
	}
	var runs []*run.RunState
	arms := map[string]int{}
	for i, q := range everestGoals {
		rs := h.live(q, nil)
		if rs.Goal.Arm == nil {
			t.Fatalf("goal %d not admitted", i)
		}
		st := h.state()
		as := st.Assignments[rs.Goal.Arm.Assignment]
		if as == nil || as.Experiment != x.ID || as.Ordinal != i || as.Arm != rs.Goal.Arm.Arm || as.Unit != rs.Goal.ID {
			t.Fatalf("goal %d: assignment %+v vs arm %+v", i, as, rs.Goal.Arm)
		}
		arms[as.Arm]++
		req, del := h.request(rs), h.deliverable(rs)
		exposed := strings.Contains(req, "Answer in meters.")
		if as.Arm == Treatment && (!exposed || del != "8,849 meters" || len(rs.Goal.Arm.Apply) != 1) {
			t.Fatalf("treatment %d: exposed %v deliverable %q", i, exposed, del)
		}
		if as.Arm == Control && (exposed || del != "29,032 feet" || len(rs.Goal.Arm.Apply) != 0) {
			t.Fatalf("control %d: exposed %v deliverable %q", i, exposed, del)
		}
		if rs.Latest().Recall.Arm == nil || !rs.Latest().Recall.Arm.Equal(rs.Goal.Arm) {
			t.Fatalf("goal %d: recall arm %+v", i, rs.Latest().Recall.Arm)
		}
		runs = append(runs, rs)
	}
	if arms[Treatment] != 2 || arms[Control] != 2 {
		t.Fatalf("permuted blocks of two did not balance: %v", arms)
	}
	// the cohort is full: the next goal of the family runs plain
	if rs := h.live("What is the height of K2?", nil); rs.Goal.Arm != nil {
		t.Fatal("a fifth goal was admitted to a cohort of four")
	}
	// the runner does not drive a live experiment
	if err := h.runner().Run(ctxBg, x.ID); !errors.Is(err, ErrRefused) {
		t.Fatalf("runner on a live experiment: %v", err)
	}
	// the evaluator lane: evidence, then closure with the blinded judge
	l := &Lane{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}
	if _, err := l.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	st := h.state()
	m := st.Measurements[x.ID]
	if m == nil || m.Verdict != TreatmentHelpful || m.ItemEffect != learn.ItemHelpful || m.Assigned != 4 || m.Analyzed != 4 || m.Exposed != 4 || m.TreatmentN != 2 || m.ControlN != 2 || m.DeltaPP != 1 || m.DeltaITT != 1 {
		t.Fatalf("measurement %+v", m)
	}
	if it := st.Runs.Learned.Items[s.helpful.Item]; it.StageOf(s.helpful.Revision) != learn.Effective {
		t.Fatalf("stage %s", it.StageOf(s.helpful.Revision))
	}
	// every scored row cites an evaluate call that saw the goal and the
	// deliverable and NOT the lesson
	att := st.Attestations[x.ID]
	for i, row := range att.Units {
		if row.Missing != "" || row.Evaluation == "" || row.Evidence == "" {
			t.Fatalf("row %d %+v", i, row)
		}
		rs := st.Runs.Arms[row.Assignment][row.Arm]
		var seen bool
		for _, is := range rs.Latest().Invocations {
			if is.Invocation.ID != row.Evaluation {
				continue
			}
			seen = true
			b, _ := h.st.Get(is.Invocation.Request)
			if strings.Contains(string(b), "Answer in meters.") || !strings.Contains(string(b), h.deliverable(rs)) || is.Invocation.Tools {
				t.Fatalf("row %d evaluation request is not blinded: %q", i, b)
			}
		}
		if !seen {
			t.Fatalf("row %d cites an evaluation that is not the unit run's", i)
		}
	}
	// the consequence: the next production run's recall includes the now
	// effective lesson, without any arm
	rs := h.live("How high is Everest, really?", nil)
	if rs.Goal.Arm != nil || !strings.Contains(h.request(rs), "Answer in meters.") || h.deliverable(rs) != "8,849 meters" {
		t.Fatalf("after the measurement: arm %v request %q deliverable %q", rs.Goal.Arm, h.request(rs), h.deliverable(rs))
	}
	// idempotent: another pass changes nothing
	before := h.j.Head()
	if _, err := l.Pass(ctxBg); err != nil || h.j.Head() != before {
		t.Fatalf("second pass: %v, head %d → %d", err, before, h.j.Head())
	}
	// the tail never learns from a live arm: the treatment request carried
	// the hypothesis, and a lesson minted from it would be the hypothesis
	// by the back door
	tl := &tail.Tail{J: h.j, Store: h.st}
	for i := 0; i < 8; i++ {
		if _, err := tl.Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
	}
	tled, err := tail.Fold(h.j.Production(), h.st)
	if err != nil {
		t.Fatal(err)
	}
	for _, rs := range runs {
		done := tled.Done[string(rs.Run)+"/1"]
		if done == nil || done.Skipped != tail.SkipReplay || len(done.Proposals) != 0 {
			t.Fatalf("arm %s tail: %+v", rs.Run, done)
		}
	}
	if plain := h.state().RunOf(s.everest[0]); tled.Done[string(plain.Run)+"/1"] == nil || tled.Done[string(plain.Run)+"/1"].Skipped != "" {
		t.Fatalf("a plain production run was skipped by the tail: %+v", tled.Done[string(plain.Run)+"/1"])
	}
}

// The same machinery measures a harmful lesson harmful (quarantined), a
// neutral one equivalent (tombstoned), and gives up honestly when the
// judge never answers (every row unevaluated → insufficient).
func TestLiveVerdicts(t *testing.T) {
	t.Run("harmful", func(t *testing.T) {
		s := build(t)
		h := s.h
		x := h.openLive(s.harmful, 4)
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("29,032 feet"), Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != TreatmentHarmful || m.ItemEffect != learn.ItemHarmful || m.DeltaPP != -1 {
			t.Fatalf("measurement %+v", m)
		}
		if st.Runs.Learned.Items[s.harmful.Item].StageOf(s.harmful.Revision) != learn.Quarantined {
			t.Fatal("not quarantined")
		}
	})
	t.Run("equivalent", func(t *testing.T) {
		s := build(t)
		h := s.h
		neutral := h.lesson("Be concise.", learn.Candidate)
		x := h.openLive(neutral, 4)
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("29,032 feet"), Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != Equivalent || m.ItemEffect != learn.ItemRedundant || m.DeltaPP != 0 || m.TreatmentN != 2 {
			t.Fatalf("measurement %+v", m)
		}
		if st.Runs.Learned.Items[neutral.Item].StageOf(neutral.Revision) != learn.Tombstone {
			t.Fatal("not tombstoned")
		}
	})
	t.Run("form only is equivalent", func(t *testing.T) {
		// meters and feet both achieve a goal that names no unit: the
		// helpful lesson changes the form, the blinded judge says so
		s := build(t)
		h := s.h
		x := h.openLive(s.helpful, 4)
		for _, q := range []string{"What is the height of Mount Everest?", "How high is Everest?", "What elevation does Everest reach?", "How tall is Mount Everest?"} {
			h.live(q, nil)
		}
		either := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
			{Key: "meters", Answer: `{"outcome":"achieved","confidence":0.9,"why":"a height"}`},
			{Key: "feet", Answer: `{"outcome":"achieved","confidence":0.9,"why":"a height"}`},
		}, Def: `{"outcome":"not_achieved","confidence":0.9,"why":"no height"}`}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: either, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != Equivalent || m.TreatmentN != 2 || m.ControlN != 2 || m.DeltaPP != 0 {
			t.Fatalf("measurement %+v", m)
		}
		if st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision) != learn.Tombstone {
			t.Fatal("not tombstoned")
		}
	})
	t.Run("a treatment that breaks execution is harmful", func(t *testing.T) {
		s := build(t)
		h := s.h
		x := h.openLive(s.helpful, 4)
		broken := &failOn{inner: h.exec, key: "Answer in meters."}
		for _, q := range everestGoals {
			h.liveOn(broken, q, nil)
		}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("feet"), Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != TreatmentHarmful || m.Analyzed != 4 || m.TreatmentN != 2 || m.ControlN != 2 || m.DeltaPP != -1 {
			t.Fatalf("measurement %+v", m)
		}
		for i, row := range st.Attestations[x.ID].Units {
			ev := st.Evidence[row.Assignment][row.Arm]
			if row.Arm == Treatment && (ev.Missing != MissingNotComplete || row.Missing != "" || row.Score != 0 || row.Evaluation != "" || !row.Exposed) {
				t.Fatalf("failed treatment row %d %+v evidence %+v", i, row, ev)
			}
			if row.Arm == Control && (row.Score != 1 || row.Evaluation == "") {
				t.Fatalf("control row %d %+v", i, row)
			}
		}
		if st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision) != learn.Quarantined {
			t.Fatal("not quarantined")
		}
	})
	t.Run("a mixed cohort", func(t *testing.T) {
		// treatment [1,1], control [1,0]: one discordant unit decides at
		// margin 0 — the stated sensitivity of arm_diff/1 at n=4
		s := build(t)
		h := s.h
		x := h.openLive(s.helpful, 4)
		armOf := func(rs *run.RunState) string { return rs.Goal.Arm.Arm }
		// K2 is answered in meters in either arm (the executor's K2 rule
		// precedes the lesson); Everest only under the lesson. Block one:
		// Everest then K2. Block two is chosen so that exactly one Everest
		// lands in control.
		a1 := armOf(h.live("What is the height of Mount Everest in meters?", nil))
		h.live("How tall is K2 in meters?", nil)
		if a1 == Control {
			h.live("What is the height of K2 in meters?", nil)
			h.live("How many meters tall is K2?", nil)
		} else {
			a2 := armOf(h.live("How high is Everest in meters?", nil))
			if a2 == Control {
				h.live("How many meters tall is K2?", nil)
			} else {
				h.live("How many meters tall is Mount Everest?", nil)
			}
		}
		// the goals name meters themselves, so the judge keys on the two
		// correct answers, not on the word
		correct := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
			{Key: "8,849 meters", Answer: `{"outcome":"achieved","confidence":0.9,"why":"correct"}`},
			{Key: "8,611 meters", Answer: `{"outcome":"achieved","confidence":0.9,"why":"correct"}`},
		}, Def: `{"outcome":"not_achieved","confidence":0.9,"why":"not in meters"}`}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: correct, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != TreatmentHelpful || m.TreatmentN != 2 || m.ControlN != 2 || m.DeltaPP != 0.5 || m.DeltaITT != 0.5 {
			t.Fatalf("measurement %+v", m)
		}
	})
	t.Run("judge never answers", func(t *testing.T) {
		s := build(t)
		h := s.h
		x := h.openLive(s.helpful, 4)
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		dead := &invoke.Scripted{Caps: invoke.Capabilities{Name: "dead-judge", Model: "none"}}
		for i := 0; i < 4*EvaluatorTries; i++ {
			dead.Calls = append(dead.Calls, invoke.ScriptedCall{FailBefore: true})
		}
		l := &Lane{J: h.j, Store: h.st, Judge: dead, Timeout: time.Minute}
		if _, err := l.Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != Insufficient || m.Analyzed != 0 || m.Exposed != 0 {
			t.Fatalf("measurement %+v", m)
		}
		for i, row := range st.Attestations[x.ID].Units {
			if row.Missing != MissingUnevaluated || row.Evaluation != "" {
				t.Fatalf("row %d %+v", i, row)
			}
			n := 0
			for _, is := range st.Runs.Arms[row.Assignment][row.Arm].Latest().Invocations {
				if is.Invocation.Purpose == invoke.PurposeEvaluate {
					n++
				}
			}
			if n != EvaluatorTries {
				t.Fatalf("row %d: %d evaluate calls", i, n)
			}
		}
		if st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision) != learn.Candidate {
			t.Fatal("insufficient moved the item")
		}
	})
}

// Admission is atomic with intake: an admission decided at a head that
// moved before the command is refused by the sequencer and decided again,
// so two intakes can never take the same ordinal; a superseded hypothesis
// stops admitting without blocking production; and the closer resumes at
// its seams.
func TestLiveAdmissionIsAtomicAndResumes(t *testing.T) {
	s := build(t)
	h := s.h
	x := h.openLive(s.helpful, 4)
	inner := Admit(h.j, h.st)
	bumped := 0
	racing := func(ctx context.Context, g *run.Goal, fam *run.FamilyAssessment) ([]record.Record, *uint64, error) {
		recs, head, err := inner(ctx, g, fam)
		if bumped < 1 { // the head moves under the first decision
			bumped++
			h.lesson("bump "+string(record.NewID()), learn.Candidate)
		}
		return recs, head, err
	}
	rs := h.live(everestGoals[0], racing)
	if bumped != 1 || rs.Goal.Arm == nil {
		t.Fatalf("bumped %d, arm %v", bumped, rs.Goal.Arm)
	}
	// an admission that loses the head every time is dropped, never the
	// user's goal: the last try commits the goal plain
	always := func(ctx context.Context, g *run.Goal, fam *run.FamilyAssessment) ([]record.Record, *uint64, error) {
		recs, head, err := inner(ctx, g, fam)
		h.lesson("bump "+string(record.NewID()), learn.Candidate)
		return recs, head, err
	}
	if rs := h.live(everestGoals[1], always); rs.Goal.Arm != nil || h.deliverable(rs) != "29,032 feet" {
		t.Fatalf("a goal that lost the head every time: arm %v, %q", rs.Goal.Arm, h.deliverable(rs))
	}
	if len(h.state().byUnit[x.ID]) != 1 {
		t.Fatal("the dropped admission was recorded")
	}
	h.live(everestGoals[1], nil)
	// when nothing admits, nothing waits on the head: a goal of another
	// family commits first time under any traffic
	calls := 0
	noisy := func(ctx context.Context, g *run.Goal, fam *run.FamilyAssessment) ([]record.Record, *uint64, error) {
		calls++
		recs, head, err := inner(ctx, g, fam)
		h.lesson("bump "+string(record.NewID()), learn.Candidate)
		return recs, head, err
	}
	if rs := h.live("Write a script file named tally.sh that counts lines.", noisy); calls != 1 || rs.Goal.Arm != nil {
		t.Fatalf("plain goal: %d admission calls, arm %v", calls, rs.Goal.Arm)
	}
	// two intakes racing for the same ordinal: the sequencer refuses one,
	// which decides again and takes the next
	{
		s2 := build(t)
		h2 := s2.h
		x2, err := Open(ctxBg, h2.j, h2.st, Spec{Hypothesis: s2.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 2, MinPerArm: 1, MinEquivalent: 1, Why: "race"})
		if err != nil {
			t.Fatal(err)
		}
		errs := make(chan error, 2)
		for i := 0; i < 2; i++ {
			go func(i int) {
				text := []byte(everestGoals[i])
				ref, err := h2.st.Put(thought.Goal, text)
				if err != nil {
					errs <- err
					return
				}
				goal, fam := run.Intake(text, ref, run.OriginCLI, run.LaneNow, run.DeliveryPolicy{Required: run.TransportAccepted})
				errs <- run.IntakeCommand(ctxBg, h2.j, Admit(h2.j, h2.st), goal, fam)
			}(i)
		}
		for i := 0; i < 2; i++ {
			if err := <-errs; err != nil {
				t.Fatal(err)
			}
		}
		st2 := h2.state()
		if len(st2.byOrdinal[x2.ID]) != 2 || st2.byOrdinal[x2.ID][0].Ordinal != 0 || st2.byOrdinal[x2.ID][1].Ordinal != 1 || st2.byOrdinal[x2.ID][0].Arm == st2.byOrdinal[x2.ID][1].Arm {
			t.Fatalf("racing intakes: %+v", st2.byOrdinal[x2.ID])
		}
	}
	// the hypothesis is superseded: production goes on, unadmitted
	h.revise(s.helpful, "Answer in metres.")
	if rs := h.live(everestGoals[2], nil); rs.Goal.Arm != nil {
		t.Fatal("admitted to a stale experiment")
	}
	if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}).Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	if st := h.state(); st.Closed[x.ID] != nil || len(st.byUnit[x.ID]) != 2 {
		t.Fatalf("a stale cohort of 2/4 closed or grew")
	}
	// a fresh cohort on the new revision, killed at every closer seam
	s2 := build(t)
	h2 := s2.h
	x2 := h2.openLive(s2.helpful, 4)
	for _, q := range everestGoals {
		h2.live(q, nil)
	}
	for _, seam := range []string{"close", "attest", "measure"} {
		c := &Closer{J: h2.j, Store: h2.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute, CrashAt: seam}
		if _, err := c.Close(ctxBg, x2.ID); !errors.Is(err, run.ErrCrashed) {
			t.Fatalf("seam %s: %v", seam, err)
		}
		if _, err := Fold(h2.j, h2.st); err != nil {
			t.Fatalf("after %s: %v", seam, err)
		}
		if seam == "close" {
			if _, err := Open(ctxBg, h2.j, h2.st, Spec{Hypothesis: s2.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 4, Why: "again"}); !errors.Is(err, ErrRefused) {
				t.Fatalf("re-open mid-close: %v", err)
			}
		}
	}
	l := &Lane{J: h2.j, Store: h2.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}
	if _, err := l.Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	st := h2.state()
	if st.Measurements[x2.ID] == nil || st.Runs.Learned.Items[s2.helpful.Item].StageOf(s2.helpful.Revision) != learn.Effective {
		t.Fatalf("after the seams: %+v", st.Measurements[x2.ID])
	}
	n := 0
	for _, row := range st.Attestations[x2.ID].Units {
		for _, is := range st.Runs.Arms[row.Assignment][row.Arm].Latest().Invocations {
			if is.Invocation.Purpose == invoke.PurposeEvaluate {
				n++
			}
		}
	}
	if n != 4 {
		t.Fatalf("%d evaluate calls for 4 units: the seams re-judged", n)
	}
}

// The fold refuses every live history Admit and the closer could not have
// written.
func TestLiveFoldRefusesForgeries(t *testing.T) {
	s := build(t)
	h := s.h
	x := h.openLive(s.helpful, 4)
	other := h.lesson("Always answer in one sentence.", learn.Candidate)
	rs0 := h.live(everestGoals[0], nil)
	as0 := h.state().Assignments[rs0.Goal.Arm.Assignment]
	sub := record.Ref{Kind: "experiment", ID: string(x.ID)}
	hd := func() record.Header {
		return record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: sub, At: time.Now().UTC()}
	}
	// intake writes a goal, its assessment, and an assignment in one command
	intake := func(g *harness, text string, lane run.Lane, mutate func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record) {
		ref, _ := g.st.Put(thought.Goal, []byte(text))
		goal, fam := run.Intake([]byte(text), ref, run.OriginCLI, lane, run.DeliveryPolicy{Required: run.TransportAccepted})
		seed := Seed(x.ID, goal.ID)
		as := &Assignment{Header: hd(), Experiment: x.ID, Unit: goal.ID, Ordinal: 1, Seed: seed, Arm: ArmFor(seed, 1, as0.Arm)}
		goal.Arm = x.armRef(as.ID, as.Arm)
		extra := mutate(goal, fam, as)
		g.submit("goal/"+string(goal.ID), append([]record.Record{goal, fam, as}, extra...)...)
	}
	cases := []struct {
		name string
		do   func(g *harness)
		want string
	}{
		{"an assignment outside the goal's intake command", func(g *harness) {
			rs := g.state().RunOf(s.everest[0])
			g.submit("forge/late", &Assignment{Header: hd(), Experiment: x.ID, Unit: rs.Goal.ID, Ordinal: 1, Seed: Seed(x.ID, rs.Goal.ID), Arm: Control})
		}, "not the goal's intake command"},
		{"the wrong arm", func(g *harness) {
			intake(g, everestGoals[1], run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record {
				wrong := Treatment
				if as.Arm == Treatment {
					wrong = Control
				}
				as.Arm, goal.Arm = wrong, x.armRef(as.ID, wrong)
				return nil
			})
		}, "the randomization names"},
		{"an ordinal out of order", func(g *harness) {
			intake(g, everestGoals[1], run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record {
				as.Ordinal = 2
				as.Arm, goal.Arm = ArmFor(as.Seed, 2, ""), x.armRef(as.ID, ArmFor(as.Seed, 2, ""))
				return nil
			})
		}, "takes ordinal"},
		{"a goal whose arm forces more than the protocol", func(g *harness) {
			intake(g, everestGoals[1], run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record {
				goal.Arm.Apply = append(goal.Arm.Apply, other)
				return nil
			})
		}, "does not carry the"},
		{"a goal of another family", func(g *harness) {
			intake(g, "Write a file named notes.txt with the height of Everest.", run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record { return nil })
		}, "admits a write_local goal"},
		{"a forged assessment", func(g *harness) {
			// the assessment says answer; the goal's text does not
			intake(g, "Write a file named notes.txt with the height of Everest.", run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record {
				fam.Family, fam.Reason = run.FamilyAnswer, "question shape"
				return nil
			})
		}, "classifies as write_local"},
		{"an AGENDA-lane goal", func(g *harness) {
			intake(g, everestGoals[1], run.LaneAgenda, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record { return nil })
		}, "agenda-lane goal"},
		{"a goal admitted to two experiments", func(g *harness) {
			y, err := Open(ctxBg, g.j, g.st, Spec{Hypothesis: other, Relation: ApplyItem, Live: true, Population: "answer", N: 2, MinPerArm: 1, MinEquivalent: 1, Why: "second"})
			if err != nil {
				t.Fatal(err)
			}
			intake(g, everestGoals[1], run.LaneNow, func(goal *run.Goal, fam *run.FamilyAssessment, as *Assignment) []record.Record {
				seed := Seed(y.ID, goal.ID)
				return []record.Record{&Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(y.ID)}, At: time.Now().UTC()}, Experiment: y.ID, Unit: goal.ID, Ordinal: 0, Seed: seed, Arm: ArmFor(seed, 0, "")}}
			})
		}, "admitted to"},
		{"evidence for the arm the unit did not run", func(g *harness) {
			rs := g.state().Runs.Arms[as0.ID][as0.Arm]
			ev := EvidenceOf(rs, x.Hypothesis, x.ID, as0.ID, as0.Unit, as0.Arm)
			if ev.Arm == Treatment {
				ev.Arm = Control
			} else {
				ev.Arm = Treatment
			}
			g.submit("forge/evidence", ev)
		}, "which runs"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			g := h.snapshot()
			defer g.close()
			c.do(g)
			_, err := Fold(g.j, g.st)
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("want %q, got %v", c.want, err)
			}
		})
	}
	// the attestation: forged at the seam before it
	for _, q := range everestGoals[1:] {
		h.live(q, nil)
	}
	c := &Closer{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute, CrashAt: "close"}
	if _, err := c.Close(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatal(err)
	}
	attest := func(g *harness, mutate func(att *EffectAttestation)) error {
		st := g.state()
		cm := st.Commitments[x.ID]
		att := &EffectAttestation{Header: record.Header{ID: record.NewID(), Schema: "effect_attestation/1", Subject: sub, At: time.Now().UTC()}, Experiment: x.ID, Cohort: cm.ID, Closure: st.Closed[x.ID].ID, Protocol: cm.Protocol, Evaluator: EvaluatorJudge, Estimator: EstimatorArms}
		cl := &Closer{J: g.j, Store: g.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}
		for i, u := range cm.Units {
			row, err := cl.liveRow(ctxBg, st, cm.Protocol, i, u)
			if err != nil {
				t.Fatal(err)
			}
			att.Units = append(att.Units, row)
		}
		st = g.state() // the judge calls are committed
		mutate(att)
		if err := att.ValidateWire(); err != nil {
			return err
		}
		g.submit("forge/attest", att)
		_, err := Fold(g.j, g.st)
		return err
	}
	forged := []struct {
		name   string
		mutate func(att *EffectAttestation)
		want   string
	}{
		{"a flipped score", func(att *EffectAttestation) { att.Units[0].Score = 1 - att.Units[0].Score }, "not what the cited evaluation"},
		{"unevaluated with a usable evaluation", func(att *EffectAttestation) {
			att.Units[0].Missing, att.Units[0].Score, att.Units[0].Evaluation = MissingUnevaluated, 0, ""
		}, "unevaluated after"},
		{"a row citing another unit's evaluation", func(att *EffectAttestation) {
			att.Units[0].Evaluation, att.Units[0].Score = att.Units[1].Evaluation, att.Units[1].Score
		}, "not an invocation of the unit's run"},
		{"exposure flipped", func(att *EffectAttestation) { att.Units[0].Exposed = !att.Units[0].Exposed }, "does not match its evidence"},
		{"a completed run scored zero without an evaluation", func(att *EffectAttestation) {
			att.Units[0].Score, att.Units[0].Evaluation = 0, ""
		}, "not an invocation of the unit's run"},
		{"the pair fields under a live protocol", func(att *EffectAttestation) { att.Units[0].Treatment = att.Units[0].Evidence }, "carries pair fields"},
	}
	for _, f := range forged {
		t.Run(f.name, func(t *testing.T) {
			g := h.snapshot()
			defer g.close()
			err := attest(g, f.mutate)
			if err == nil || !strings.Contains(err.Error(), f.want) {
				t.Fatalf("want %q, got %v", f.want, err)
			}
		})
	}
	// and the honest one closes
	if m, err := Close(ctxBg, h.j, h.st, x.ID); err == nil || !errors.Is(err, ErrConfig) {
		t.Fatalf("a live close without a judge: %v %v", m, err)
	}
	if _, err := (&Closer{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}).Close(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
}

// The door and Open refuse malformed live protocols.
func TestOpenRefusesBadLiveProtocols(t *testing.T) {
	s := build(t)
	h := s.h
	for _, c := range []struct {
		name string
		spec Spec
	}{
		{"units with live", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 2, Units: []UnitSpec{{Goal: s.everest[0], Fixture: h.fixture("x")}}, Why: "w"}},
		{"population none", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "none", N: 2, Why: "w"}},
		{"unknown population", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "poetry", N: 2, Why: "w"}},
		{"n of one", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 1, Why: "w"}},
		{"odd n", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 3, MinPerArm: 1, MinEquivalent: 1, Why: "w"}},
		{"n of two under the default floors", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 2, Why: "w"}},
		{"min_per_arm above n/2", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 4, MinPerArm: 3, Why: "w"}},
		{"min_equivalent above n/2", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 4, MinEquivalent: 3, Why: "w"}},
		{"no why", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 2}},
	} {
		if _, err := Open(ctxBg, h.j, h.st, c.spec); !errors.Is(err, ErrConfig) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	// a live-admitted goal is never a paired unit
	x := h.openLive(s.helpful, 4)
	rs := h.live(everestGoals[0], nil)
	if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.harmful, Relation: ApplyItem, Units: []UnitSpec{{Goal: rs.Goal.ID, Fixture: h.fixture("8,849 meters")}}, Why: "w"}); !errors.Is(err, ErrConfig) {
		t.Fatalf("an arm as a paired unit: %v", err)
	}
	_ = x
	// ArmFor: blocks of two balance
	for _, seed := range []string{"00", "01", "0a", "0b"} {
		first := ArmFor(seed, 0, "")
		if second := ArmFor(seed, 1, first); second == first {
			t.Fatalf("seed %s: block not balanced", seed)
		}
	}
	if ArmFor("00", 0, "") != Treatment || ArmFor("01", 0, "") != Control {
		t.Fatal("parity")
	}
}

// The oracle is part of the hypothesis (post-v1 item 2). A lesson that
// supplies a fact is measured by the fixture oracle over a live cohort —
// the protocol carries the one expected answer, no evaluator is called,
// the verifier recomputes every score; a lesson judged by the blinded
// evaluator gets `unjudgeable` as a counted missingness when the text
// alone cannot settle it, so an oracle out of its competence yields an
// insufficient measurement and no transition — never the acceptance run's
// tombstone of a lesson that had changed behavior.
func TestLiveOracleIsPartOfTheHypothesis(t *testing.T) {
	openWith := func(h *harness, hyp learn.ItemRev, n int, expect *thought.Ref, minPerArm int) *Experiment {
		t.Helper()
		x, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: hyp, Relation: ApplyItem, Live: true, Population: "answer", N: n, Expect: expect, MinPerArm: minPerArm, MinEquivalent: minPerArm, Why: "oracle"})
		if err != nil {
			t.Fatal(err)
		}
		return x
	}
	t.Run("fixture oracle on a live cohort", func(t *testing.T) {
		s := build(t)
		h := s.h
		fx := h.fixture("8,849 meters")
		x := openWith(h, s.helpful, 4, &fx, 0)
		if x.Oracle != DeterministicFixture || x.Outcome.Dimension != Dimension || x.Fixture == nil || x.Fixture.Hash != fx.Hash {
			t.Fatalf("protocol: %+v", x.Protocol)
		}
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		// no judge: the fixture scores
		if _, err := (&Lane{J: h.j, Store: h.st, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != TreatmentHelpful || m.ItemEffect != learn.ItemHelpful || m.DeltaPP != 1 || m.Unjudgeable != 0 {
			t.Fatalf("measurement %+v", m)
		}
		att := st.Attestations[x.ID]
		if att.Evaluator != Evaluator {
			t.Fatalf("evaluator %q", att.Evaluator)
		}
		for i, row := range att.Units {
			if row.Evaluation != "" || row.Missing != "" || (row.Arm == Treatment) != (row.Score == 1) {
				t.Fatalf("row %d %+v", i, row)
			}
		}
		for _, rs := range st.Runs.Runs {
			for _, a := range rs.Attempts {
				for _, is := range a.Invocations {
					if is.Invocation.Purpose == invoke.PurposeEvaluate {
						t.Fatal("an evaluate call under the fixture oracle")
					}
				}
			}
		}
		if st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision) != learn.Effective {
			t.Fatal("not promoted")
		}
		// the verifier's rule on the attestation's own facts: a row whose
		// score is not the fixture's over the deliverable is refused
		cm := st.Commitments[x.ID]
		row := att.Units[0]
		row.Score = 1 - row.Score
		if err := st.checkLiveRow(h.st, cm.Protocol, att.Evaluator, 0, cm.Units[0], row, att.Seq); err == nil || !strings.Contains(err.Error(), "does not recompute") {
			t.Fatalf("flipped fixture score accepted: %v", err)
		}
	})
	t.Run("unjudgeable is counted, not scored", func(t *testing.T) {
		// the acceptance run's shape: a fact lesson, an evaluator that
		// cannot verify the fact from the text — it says so
		s := build(t)
		h := s.h
		fact := h.lesson("The code word is juniper.", learn.Candidate)
		x := openWith(h, fact, 4, nil, 0)
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		cannot := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Def: `{"outcome":"unjudgeable","confidence":0.9,"why":"the text alone cannot settle it"}`}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: cannot, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != Insufficient || m.Unjudgeable != 4 || m.Analyzed != 0 || m.TreatmentN != 0 {
			t.Fatalf("measurement %+v", m)
		}
		for i, row := range st.Attestations[x.ID].Units {
			if row.Missing != MissingUnjudgeable || row.Score != 0 || row.Evaluation == "" {
				t.Fatalf("row %d %+v", i, row)
			}
		}
		if st.Runs.Learned.Items[fact.Item].StageOf(fact.Revision) != learn.Candidate {
			t.Fatal("an oracle out of its competence moved the lesson")
		}
		if len(cannot.Seen) != 4 {
			t.Fatalf("%d evaluate calls; unjudgeable is a usable answer, not a retry", len(cannot.Seen))
		}
		// the verifier: a row claiming a judged score while the cited
		// evaluation said unjudgeable is refused, and so is one claiming
		// unjudgeable that cites nothing the run made
		att, cm := st.Attestations[x.ID], st.Commitments[x.ID]
		row := att.Units[0]
		row.Missing, row.Score = "", 1
		if err := st.checkLiveRow(h.st, cm.Protocol, att.Evaluator, 0, cm.Units[0], row, att.Seq); err == nil || !strings.Contains(err.Error(), "not what the cited evaluation") {
			t.Fatalf("scored over an unjudgeable answer: %v", err)
		}
		row = att.Units[0]
		row.Evaluation = record.NewID()
		if err := st.checkLiveRow(h.st, cm.Protocol, att.Evaluator, 0, cm.Units[0], row, att.Seq); err == nil || !strings.Contains(err.Error(), "not an invocation of the unit's run") {
			t.Fatalf("unjudgeable citing nothing: %v", err)
		}
	})
	t.Run("a partly judgeable cohort", func(t *testing.T) {
		s := build(t)
		h := s.h
		x := openWith(h, s.helpful, 4, nil, 1)
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		mixed := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
			{Key: everestGoals[0], Answer: `{"outcome":"unjudgeable","confidence":0.9,"why":"cannot"}`},
			{Key: "8,849 meters", Answer: `{"outcome":"achieved","confidence":0.9,"why":"matched"}`},
		}, Def: `{"outcome":"not_achieved","confidence":0.9,"why":"no match"}`}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: mixed, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Unjudgeable != 1 || m.Analyzed != 3 || m.Verdict != TreatmentHelpful {
			t.Fatalf("measurement %+v", m)
		}
	})
	t.Run("the door", func(t *testing.T) {
		s := build(t)
		h := s.h
		blank := h.fixture(" ")
		if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 4, Expect: &blank, Why: "w"}); !errors.Is(err, ErrConfig) {
			t.Fatalf("blank expectation: %v", err)
		}
		fx := h.fixture("x")
		if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: []UnitSpec{{Goal: s.everest[0], Fixture: fx}}, Expect: &fx, Why: "w"}); !errors.Is(err, ErrConfig) {
			t.Fatalf("expectation on paired replay: %v", err)
		}
		x := openWith(h, s.helpful, 4, &fx, 0)
		bad := *x
		bad.Fixture = nil
		if err := bad.ValidateWire(); err == nil || !strings.Contains(err.Error(), "fixture") {
			t.Fatalf("fixture oracle without a fixture: %v", err)
		}
		bad = *x
		bad.Oracle, bad.Outcome.Dimension = BlindedEvaluator, DimensionAchieved
		if err := bad.ValidateWire(); err == nil || !strings.Contains(err.Error(), "no fixture") {
			t.Fatalf("blinded evaluator with a fixture: %v", err)
		}
		bad = *x
		bad.Outcome.Dimension = DimensionAchieved
		if err := bad.ValidateWire(); err == nil {
			t.Fatal("fixture oracle scoring goal_achieved")
		}
	})
}

// The evaluator is versioned by name on the attestation: a cohort closed
// under judge/1 (two answers, the frozen V1 prompt) verifies against the
// bytes it was asked after the code moved to judge/2 — the fold renders
// and reads by the named version — and a judge/1 row cannot claim
// unjudgeable. The V1 bytes are pinned so no later "improvement" of the
// frozen prompt can strand a judge/1 attestation.
func TestEvaluatorVersionIsNamedOnTheAttestation(t *testing.T) {
	if got := string(EvaluatorPromptV1([]byte("g"), []byte("d"))); got != "You are a blinded evaluator. Decide whether the deliverable achieves the goal. Judge only what is written; do not assume context.\n\nGoal:\ng\n\nDeliverable:\nd\n\nReply with JSON only: {\"outcome\":\"achieved\"|\"not_achieved\",\"confidence\":0..1,\"why\":\"one sentence\"}\n" {
		t.Fatalf("judge/1 prompt drifted:\n%s", got)
	}
	s := build(t)
	h := s.h
	x := h.openLive(s.helpful, 4)
	for _, q := range everestGoals {
		h.live(q, nil)
	}
	if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute, EvaluatorVersion: EvaluatorJudgeV1}).Pass(ctxBg); err != nil {
		t.Fatal(err)
	}
	st := h.state() // folds under the current code: judge/1 verified by name
	att := st.Attestations[x.ID]
	if att == nil || att.Evaluator != EvaluatorJudgeV1 || st.Measurements[x.ID] == nil || st.Measurements[x.ID].Verdict != TreatmentHelpful {
		t.Fatalf("attestation %+v", att)
	}
	rs := st.Runs.Arms[st.Assignments[att.Units[0].Assignment].ID][att.Units[0].Arm]
	goal, _ := h.st.Get(rs.Goal.Text)
	ev := st.Evidence[att.Units[0].Assignment][att.Units[0].Arm]
	deliverable, _ := h.st.Get(*ev.Deliverable)
	if inv := invocationByID(rs, att.Units[0].Evaluation); inv == nil || inv.Request != thought.Address(thought.Prompt, EvaluatorPromptV1(goal, deliverable)) {
		t.Fatal("the cited evaluation was not asked the judge/1 prompt")
	}
	// the same row under judge/2's name does not verify: different bytes
	cm := st.Commitments[x.ID]
	if err := st.checkLiveRow(h.st, cm.Protocol, EvaluatorJudge, 0, cm.Units[0], att.Units[0], att.Seq); err == nil || !strings.Contains(err.Error(), "something other than the blinded prompt") {
		t.Fatalf("judge/1 evaluation accepted under judge/2: %v", err)
	}
	// the door: a judge/1 row claiming unjudgeable; an unknown evaluator
	bad := *att
	bad.Units = append([]UnitRow{}, att.Units...)
	bad.Units[0].Missing, bad.Units[0].Score = MissingUnjudgeable, 0
	if err := bad.ValidateWire(); err == nil || !strings.Contains(err.Error(), "asked two answers") {
		t.Fatalf("unjudgeable under judge/1: %v", err)
	}
	bad = *att
	bad.Evaluator = "judge/3"
	if err := bad.ValidateWire(); err == nil || !strings.Contains(err.Error(), "judge/3") {
		t.Fatalf("unknown evaluator: %v", err)
	}
	// a judge/1 evaluator refuses the third answer as unusable
	if _, _, err := parseEvaluation(EvaluatorJudgeV1, []byte(`{"outcome":"unjudgeable","confidence":0.5,"why":"w"}`)); err == nil {
		t.Fatal("judge/1 read unjudgeable")
	}
}

func invocationByID(rs *run.RunState, id record.RecordID) *invoke.Invocation {
	for _, a := range rs.Attempts {
		for _, is := range a.Invocations {
			if is.Invocation.ID == id {
				return is.Invocation
			}
		}
	}
	return nil
}

// A forged experiment naming a fixture the store does not hold is refused
// by the fold (the door sees only the reference's shape); the
// measurement door bounds the unjudgeable count.
func TestFoldRefusesLiveFixtureExperimentWhoseFixtureIsMissing(t *testing.T) {
	s := build(t)
	h := s.h
	fx := h.fixture("8,849 meters")
	x, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 4, Expect: &fx, Why: "w"})
	if err != nil {
		t.Fatal(err)
	}
	g := h.snapshot()
	defer g.close()
	y := *x
	y.ID, y.Seq = record.NewID(), 0
	y.Subject = record.Ref{Kind: "experiment", ID: string(y.ID)}
	y.Protocol.Experiment = y.ID
	ghost := thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Fixture, Bytes: 5, Encoding: thought.UTF8}
	y.Fixture = &ghost
	g.submit("forge/ghost", &y)
	if _, err := Fold(g.j, g.st); err == nil || !strings.Contains(err.Error(), "fixture") {
		t.Fatalf("ghost fixture folded: %v", err)
	}
	m := &EffectMeasurement{Header: record.Header{ID: record.NewID(), Schema: "effect_measurement/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Attestation: record.NewID(), Hypothesis: x.Hypothesis, Relation: ApplyItem, Assigned: 4, Analyzed: 3, Verdict: Insufficient, ItemEffect: Normalize(Insufficient, ApplyItem)}
	for _, bad := range []int{-1, 2} {
		m.Unjudgeable = bad
		if err := m.ValidateWire(); err == nil || !strings.Contains(err.Error(), "unjudgeable") {
			t.Fatalf("unjudgeable %d accepted: %v", bad, err)
		}
	}
	m.Unjudgeable = 1
	if err := m.ValidateWire(); err != nil {
		t.Fatalf("unjudgeable 1 of 1: %v", err)
	}
}
