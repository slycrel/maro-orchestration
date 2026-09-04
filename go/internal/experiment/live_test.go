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
	h.t.Helper()
	if admit == nil {
		admit = Admit(h.j, h.st)
	}
	d := &run.Driver{J: h.j, Store: h.st, Backend: h.exec, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute, Admit: admit}
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

var everestGoals = []string{"What is the height of Mount Everest?", "How high is Everest above sea level?", "What elevation does Everest reach?", "How tall is Mount Everest, in its own units?"}

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
	t.Run("judge never answers", func(t *testing.T) {
		s := build(t)
		h := s.h
		x := h.openLive(s.helpful, 2)
		for _, q := range everestGoals[:2] {
			h.live(q, nil)
		}
		dead := &invoke.Scripted{Caps: invoke.Capabilities{Name: "dead-judge", Model: "none"}}
		for i := 0; i < 2*EvaluatorTries; i++ {
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
		if bumped < 2 { // the head moves under the first two decisions
			bumped++
			h.lesson("bump "+string(record.NewID()), learn.Candidate)
		}
		return recs, head, err
	}
	rs := h.live(everestGoals[0], racing)
	if bumped != 2 || rs.Goal.Arm == nil {
		t.Fatalf("bumped %d, arm %v", bumped, rs.Goal.Arm)
	}
	// a third refusal is the caller's error
	always := func(ctx context.Context, g *run.Goal, fam *run.FamilyAssessment) ([]record.Record, *uint64, error) {
		recs, head, err := inner(ctx, g, fam)
		h.lesson("bump "+string(record.NewID()), learn.Candidate)
		return recs, head, err
	}
	d := &run.Driver{J: h.j, Store: h.st, Backend: h.exec, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute, Admit: always}
	if _, err := d.Run(ctxBg, []byte(everestGoals[1]), run.DeliveryPolicy{Required: run.TransportAccepted}); err == nil {
		t.Fatal("an admission refused three times succeeded")
	}
	if _, err := Fold(h.j, h.st); err != nil {
		t.Fatal(err)
	}
	h.live(everestGoals[1], nil)
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
	intake := func(g *harness, text string, arm string, ordinal int, mutate func(goal *run.Goal, as *Assignment) []record.Record) {
		ref, _ := g.st.Put(thought.Goal, []byte(text))
		goal, fam := run.Intake([]byte(text), ref, run.OriginCLI, run.LaneNow, run.DeliveryPolicy{Required: run.TransportAccepted})
		seed := Seed(x.ID, goal.ID)
		as := &Assignment{Header: hd(), Experiment: x.ID, Unit: goal.ID, Ordinal: ordinal, Seed: seed, Arm: arm}
		goal.Arm = x.armRef(as.ID, arm)
		extra := mutate(goal, as)
		g.submit("goal/"+string(goal.ID), append([]record.Record{goal, fam, as}, extra...)...)
	}
	honestArm := func(g *harness, ordinal int) string {
		// the arm the randomization names for the NEXT unit is only known
		// once the goal id is: the mutate hook fixes it
		return ""
	}
	_ = honestArm
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
			intake(g, everestGoals[1], "", 1, func(goal *run.Goal, as *Assignment) []record.Record {
				wrong := Treatment
				if ArmFor(as.Seed, 1, as0.Arm) == Treatment {
					wrong = Control
				}
				as.Arm, goal.Arm = wrong, x.armRef(as.ID, wrong)
				return nil
			})
		}, "the randomization names"},
		{"an ordinal out of order", func(g *harness) {
			intake(g, everestGoals[1], "", 2, func(goal *run.Goal, as *Assignment) []record.Record {
				as.Arm, goal.Arm = ArmFor(as.Seed, 2, ""), x.armRef(as.ID, ArmFor(as.Seed, 2, ""))
				return nil
			})
		}, "takes ordinal"},
		{"a goal whose arm forces more than the protocol", func(g *harness) {
			intake(g, everestGoals[1], "", 1, func(goal *run.Goal, as *Assignment) []record.Record {
				as.Arm = ArmFor(as.Seed, 1, as0.Arm)
				goal.Arm = x.armRef(as.ID, as.Arm)
				goal.Arm.Apply = append(goal.Arm.Apply, other)
				return nil
			})
		}, "does not carry the"},
		{"a goal of another family", func(g *harness) {
			intake(g, "Write a file named notes.txt with the height of Everest.", "", 1, func(goal *run.Goal, as *Assignment) []record.Record {
				as.Arm = ArmFor(as.Seed, 1, as0.Arm)
				goal.Arm = x.armRef(as.ID, as.Arm)
				return nil
			})
		}, "admits a write_local goal"},
		{"a goal admitted to two experiments", func(g *harness) {
			y, err := Open(ctxBg, g.j, g.st, Spec{Hypothesis: other, Relation: ApplyItem, Live: true, Population: "answer", N: 2, Why: "second"})
			if err != nil {
				t.Fatal(err)
			}
			intake(g, everestGoals[1], "", 1, func(goal *run.Goal, as *Assignment) []record.Record {
				as.Arm = ArmFor(as.Seed, 1, as0.Arm)
				goal.Arm = x.armRef(as.ID, as.Arm)
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
		{"no why", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Live: true, Population: "answer", N: 2}},
	} {
		if _, err := Open(ctxBg, h.j, h.st, c.spec); !errors.Is(err, ErrConfig) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	// a live-admitted goal is never a paired unit
	x := h.openLive(s.helpful, 2)
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
