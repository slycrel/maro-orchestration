package experiment

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/tail"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var ctxBg = context.Background()

type harness struct {
	t    *testing.T
	dir  string
	a    *workspace.Announced
	l    *workspace.Lease
	j    *journal.Journal
	st   *thought.Store
	exec *invoke.Keyed
}

// The executor is a blinded discriminator: the answer depends on exactly
// which lesson text reached the request. Rule order matters — the harmful
// lesson wins over the K2 goal, the K2 goal over the helpful lesson.
func keyed() *invoke.Keyed {
	return &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-exec", Model: "exec"}, Rules: []invoke.Rule{
		{Key: "Answer in feet.", Answer: "28,251 feet"},
		{Key: "K2", Answer: "8,611 meters"},
		{Key: "Answer in meters.", Answer: "8,849 meters"},
		{Key: "Everest", Answer: "29,032 feet"},
	}, Def: "?"}
}

func open(t *testing.T) *harness {
	t.Helper()
	return openAt(t, filepath.Join(t.TempDir(), "ws"))
}

func openAt(t *testing.T, dir string) *harness {
	t.Helper()
	t.Setenv(workspace.EnvOverride, dir)
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	a.Ensure()
	h := &harness{t: t, dir: dir, a: a, exec: keyed()}
	h.restart()
	return h
}

func (h *harness) restart() {
	h.t.Helper()
	h.close()
	l, err := workspace.Acquire(h.a)
	if err != nil {
		h.t.Fatal(err)
	}
	j, err := journal.Open(l)
	if err != nil {
		h.t.Fatal(err)
	}
	st, err := thought.Open(h.a)
	if err != nil {
		h.t.Fatal(err)
	}
	h.l, h.j, h.st = l, j, st
}

func (h *harness) close() {
	if h.j != nil {
		h.j.Close()
		h.l.Release()
		h.j = nil
	}
}

// snapshot copies the closed workspace to a fresh directory and opens it:
// a forgery is committed to the copy, so every case starts from the same
// honest history.
func (h *harness) snapshot() *harness {
	h.t.Helper()
	h.close()
	dir := filepath.Join(h.t.TempDir(), "ws")
	if err := os.CopyFS(dir, os.DirFS(h.dir)); err != nil {
		h.t.Fatal(err)
	}
	h.restart()
	return openAt(h.t, dir)
}

func (h *harness) lesson(text string, stage learn.Stage) learn.ItemRev {
	h.t.Helper()
	ref, err := h.st.Put(thought.LessonText, []byte(text))
	if err != nil {
		h.t.Fatal(err)
	}
	item := learn.LearnedID(record.NewID())
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	recs := []record.Record{r}
	if stage != learn.Candidate {
		recs = append(recs, &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, Revision: r.ID, From: learn.Candidate, To: stage, Actor: learn.ActorOperator, Why: "test"})
	}
	h.submit("lesson/"+string(item), recs...)
	return learn.ItemRev{Item: item, Revision: r.ID}
}

func (h *harness) submit(key string, recs ...record.Record) {
	h.t.Helper()
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: key, Epoch: h.j.Epoch(), Records: recs}); err != nil {
		h.t.Fatalf("%s: %v", key, err)
	}
}

// production runs one NOW goal through the CLI origin and returns its goal id.
func (h *harness) production(text string) record.RecordID {
	h.t.Helper()
	d := &run.Driver{J: h.j, Store: h.st, Backend: h.exec, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute}
	rep, err := d.Run(ctxBg, []byte(text), run.DeliveryPolicy{Required: run.TransportAccepted})
	if err != nil || rep.Mission.Outcome != run.MissionDelivered {
		h.t.Fatalf("production run: %v %+v", err, rep)
	}
	return rep.Goal
}

func (h *harness) fixture(text string) thought.Ref {
	h.t.Helper()
	ref, err := h.st.Put(thought.Fixture, []byte(text))
	if err != nil {
		h.t.Fatal(err)
	}
	return ref
}

func (h *harness) state() *State {
	h.t.Helper()
	st, err := Fold(h.j, h.st)
	if err != nil {
		h.t.Fatal(err)
	}
	return st
}

func (h *harness) runner() *Runner {
	return &Runner{J: h.j, Store: h.st, Backend: h.exec, Timeout: time.Minute}
}

// deliverable returns the bytes a run delivered.
func (h *harness) deliverable(rs *run.RunState) string {
	h.t.Helper()
	a := rs.Latest()
	if a.Delivery == nil || a.Delivery.Prepared == nil {
		h.t.Fatalf("run %s has no delivery", rs.Run)
	}
	b, err := h.st.Get(a.Delivery.Prepared.Payload)
	if err != nil {
		h.t.Fatal(err)
	}
	return string(b)
}

func (h *harness) request(rs *run.RunState) string {
	h.t.Helper()
	for _, st := range rs.Latest().Invocations {
		if st.Invocation.Purpose == invoke.PurposeExecute {
			b, err := h.st.Get(st.Invocation.Request)
			if err != nil {
				h.t.Fatal(err)
			}
			return string(b)
		}
	}
	h.t.Fatal("no execute invocation")
	return ""
}

// scenario is the design's discrimination test (§8a, §19): a candidate
// lesson that changes the answer is measured helpful over three Everest
// units and becomes effective; one that breaks the answer is measured
// harmful over three K2 units and is quarantined. Both verdicts come from
// a blinded oracle that reads only deliverables and fixtures.
type scenario struct {
	h                *harness
	helpful, harmful learn.ItemRev
	everest, k2      []record.RecordID
	exp1, exp2       *Experiment
}

func build(t *testing.T) *scenario {
	t.Helper()
	h := open(t)
	s := &scenario{h: h}
	s.helpful = h.lesson("Answer in meters.", learn.Candidate)
	s.harmful = h.lesson("Answer in feet.", learn.Candidate)
	for _, q := range []string{"What is the height of Everest?", "How tall is Everest?", "What is Everest's elevation?"} {
		s.everest = append(s.everest, h.production(q))
	}
	for _, q := range []string{"What is the height of K2?", "How tall is K2?", "What is K2's elevation?"} {
		s.k2 = append(s.k2, h.production(q))
	}
	// the production answers are what the units recorded: Everest in feet
	// (wrong against the fixture), K2 in meters (right)
	st := h.state()
	for _, g := range s.everest {
		if d := h.deliverable(st.RunOf(g)); !strings.Contains(d, "29,032 feet") {
			t.Fatalf("everest production deliverable %q", d)
		}
	}
	return s
}

func (s *scenario) open(t *testing.T, hyp learn.ItemRev, units []record.RecordID, fixture string) *Experiment {
	t.Helper()
	spec := Spec{Hypothesis: hyp, Relation: ApplyItem, Why: "test"}
	for _, g := range units {
		spec.Units = append(spec.Units, UnitSpec{Goal: g, Fixture: s.h.fixture(fixture)})
	}
	x, err := Open(ctxBg, s.h.j, s.h.st, spec)
	if err != nil {
		t.Fatal(err)
	}
	return x
}

func TestBlindedDiscrimination(t *testing.T) {
	s := build(t)
	h := s.h
	// the helpful experiment: apply "Answer in meters." over the Everest units
	s.exp1 = s.open(t, s.helpful, s.everest, "8,849 meters")
	if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: s.exp1.Units, Why: "again"}); !errors.Is(err, ErrRefused) {
		t.Fatalf("fishing: second open on the same hypothesis and population: %v", err)
	}
	if err := h.runner().Run(ctxBg, s.exp1.ID); err != nil {
		t.Fatal(err)
	}
	st := h.state()
	if n := len(st.Runs.Replays); n != 3 {
		t.Fatalf("assignments with arm runs: %d", n)
	}
	for _, as := range st.Assignments {
		evs := st.Evidence[as.ID]
		tr, ct := evs[Treatment], evs[Control]
		if tr == nil || ct == nil || !tr.Exposed || ct.Exposed || tr.Deliverable == nil || ct.Deliverable == nil {
			t.Fatalf("evidence %+v %+v", tr, ct)
		}
		if d := h.deliverable(st.Runs.Replays[as.ID][Treatment]); d != "8,849 meters" {
			t.Fatalf("treatment deliverable %q", d)
		}
		if d := h.deliverable(st.Runs.Replays[as.ID][Control]); d != "29,032 feet" {
			t.Fatalf("control deliverable %q", d)
		}
		// the arm's selection carries the arm; the goal replays the unit
		rs := st.Runs.Replays[as.ID][Treatment]
		if rs.Goal.Parent != as.Unit || rs.Goal.Replay == nil || rs.Goal.Replay.Assignment != as.ID || rs.Latest().Recall.Arm == nil || rs.Latest().Recall.Arm.Arm != Treatment {
			t.Fatalf("treatment run: goal %+v recall arm %+v", rs.Goal, rs.Latest().Recall.Arm)
		}
	}
	m, err := Close(ctxBg, h.j, h.st, s.exp1.ID)
	if err != nil {
		t.Fatal(err)
	}
	if m.Verdict != TreatmentHelpful || m.ItemEffect != learn.ItemHelpful || m.Assigned != 3 || m.Analyzed != 3 || m.Exposed != 3 || m.Discordant != 3 || m.DeltaPP != 1 || m.DeltaITT != 1 {
		t.Fatalf("measurement %+v", m)
	}
	st = h.state()
	if got := st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision); got != learn.Effective {
		t.Fatalf("helpful lesson is %s, want effective", got)
	}
	// closing again is idempotent: same measurement, no second transition
	m2, err := Close(ctxBg, h.j, h.st, s.exp1.ID)
	if err != nil || m2.ID != m.ID {
		t.Fatalf("re-close: %v %s/%s", err, m2.ID, m.ID)
	}
	if n := len(h.state().Runs.Learned.Items[s.helpful.Item].Transitions[s.helpful.Revision]); n != 1 {
		t.Fatalf("transitions after re-close: %d", n)
	}
	if err := h.runner().Run(ctxBg, s.exp1.ID); !errors.Is(err, ErrRefused) {
		t.Fatalf("run after close: %v", err)
	}
	// the harmful experiment: apply "Answer in feet." over the K2 units; the
	// control arm now carries the effective meters lesson (production would)
	s.exp2 = s.open(t, s.harmful, s.k2, "8,611 meters")
	if err := h.runner().Run(ctxBg, s.exp2.ID); err != nil {
		t.Fatal(err)
	}
	m, err = Close(ctxBg, h.j, h.st, s.exp2.ID)
	if err != nil {
		t.Fatal(err)
	}
	if m.Verdict != TreatmentHarmful || m.ItemEffect != learn.ItemHarmful || m.DeltaPP != -1 || m.Discordant != 3 {
		t.Fatalf("measurement %+v", m)
	}
	st = h.state()
	if got := st.Runs.Learned.Items[s.harmful.Item].StageOf(s.harmful.Revision); got != learn.Quarantined {
		t.Fatalf("harmful lesson is %s, want quarantined", got)
	}
	// production after both: the effective lesson reaches the request, the
	// quarantined one does not, and the answer changed
	g := h.production("What is the height of Everest, please?")
	st = h.state()
	req := h.request(st.RunOf(g))
	if !strings.Contains(req, "Answer in meters.") || strings.Contains(req, "Answer in feet.") {
		t.Fatalf("production request after learning: %q", req)
	}
	if d := h.deliverable(st.RunOf(g)); d != "8,849 meters" {
		t.Fatalf("production deliverable after learning %q", d)
	}
	// the tail never learns from an arm: every replay attempt is closed
	// skipped, no proposal comes from one
	tl := &tail.Tail{J: h.j, Store: h.st}
	for i := 0; i < 8; i++ { // MaxPerPass bounds one pass; the backlog drains over several
		if _, err := tl.Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
	}
	tled, err := tail.Fold(h.j.Production(), h.st)
	if err != nil {
		t.Fatal(err)
	}
	arms := 0
	for _, byArm := range st.Runs.Replays {
		for _, rs := range byArm {
			arms++
			done := tled.Done[string(rs.Run)+"/1"]
			if done == nil || done.Skipped != tail.SkipReplay || len(done.Proposals) != 0 || done.Diagnosis != "" {
				t.Fatalf("arm %s tail: %+v", rs.Run, done)
			}
		}
	}
	if arms != 12 {
		t.Fatalf("arm runs: %d", arms)
	}
	// the experiment records are what the census says they are: the
	// control envelope holds protocol, assignment, closure; production the rest
	for k, env := range map[record.Kind]record.Envelope{KindExperiment: record.Control, KindAssignment: record.Control, KindClosed: record.Control, KindEvidence: record.Production, KindCommitment: record.Production, KindAttestation: record.Production, KindMeasurement: record.Production} {
		if got, _ := record.EnvelopeOf(k); got != env {
			t.Fatalf("%s is %s, want %s", k, got, env)
		}
	}
}

// A production verifier that reads control never: the fold's production
// checks recompute rows from the commitment's embedded protocol.
func TestEstimatorAndOracle(t *testing.T) {
	p := Protocol{Relation: ApplyItem, Outcome: OutcomeSpec{Margin: 0}, Analysis: AnalysisSpec{MinDiscordant: 2, MinEquivalent: 2}}
	att := func(rows ...UnitRow) *EffectAttestation { return &EffectAttestation{Protocol: p, Units: rows} }
	cases := []struct {
		name    string
		att     *EffectAttestation
		verdict EffectVerdict
		effect  learn.ItemEffect
	}{
		{"helpful", att(UnitRow{TreatmentScore: 1, Exposed: true}, UnitRow{TreatmentScore: 1, Exposed: true}), TreatmentHelpful, learn.ItemHelpful},
		{"one discordant is insufficient", att(UnitRow{TreatmentScore: 1, Exposed: true}, UnitRow{TreatmentScore: 1, ControlScore: 1, Exposed: true}), Insufficient, learn.ItemInsufficient},
		{"harmful", att(UnitRow{ControlScore: 1, Exposed: true}, UnitRow{ControlScore: 1, Exposed: true}), TreatmentHarmful, learn.ItemHarmful},
		{"equivalent", att(UnitRow{TreatmentScore: 1, ControlScore: 1, Exposed: true}, UnitRow{Exposed: true}), Equivalent, learn.ItemRedundant},
		{"equivalent needs exposure", att(UnitRow{TreatmentScore: 1, ControlScore: 1, Exposed: true}, UnitRow{}), Insufficient, learn.ItemInsufficient},
		{"missing pairs are excluded", att(UnitRow{TreatmentScore: 1, Exposed: true, ControlMissing: MissingNoDeliverable}, UnitRow{TreatmentScore: 1, Exposed: true}, UnitRow{TreatmentScore: 1, Exposed: true}), TreatmentHelpful, learn.ItemHelpful},
		{"unexposed pairs count for itt only", att(UnitRow{TreatmentScore: 1}, UnitRow{TreatmentScore: 1}), Insufficient, learn.ItemInsufficient},
	}
	for _, c := range cases {
		m := Measure(c.att)
		if m.Verdict != c.verdict || m.ItemEffect != c.effect {
			t.Errorf("%s: %s/%s, want %s/%s (%+v)", c.name, m.Verdict, m.ItemEffect, c.verdict, c.effect, m)
		}
	}
	m := Measure(cases[5].att)
	if m.Assigned != 3 || m.Analyzed != 2 || m.Exposed != 2 || m.DeltaITT != 1 {
		t.Errorf("missing: %+v", m)
	}
	m = Measure(cases[6].att)
	if m.Analyzed != 2 || m.Exposed != 0 || m.DeltaITT != 1 || m.DeltaPP != 0 {
		t.Errorf("unexposed: %+v", m)
	}
	// ablation flips the sign of the item effect
	if Normalize(TreatmentHelpful, AblateItem) != learn.ItemHarmful || Normalize(TreatmentHarmful, AblateItem) != learn.ItemHelpful || Normalize(Equivalent, AblateItem) != learn.ItemRedundant {
		t.Error("ablation normalization")
	}
	// the oracle: substring, case-insensitive, never matches an empty fixture
	if Score([]byte("It is 8,849 Meters tall."), []byte("8,849 meters")) != 1 || Score([]byte("29,032 feet"), []byte("8,849 meters")) != 0 || Score([]byte("anything"), []byte("  ")) != 0 {
		t.Error("oracle")
	}
}

// Must-detect fixtures for the fold: each forged history is wire-valid
// record by record and must still be refused, with the reason named. Each
// case forges against a snapshot of the same honest history.
func TestFoldRefusesForgedExperiments(t *testing.T) {
	s := build(t)
	s.exp1 = s.open(t, s.helpful, s.everest, "8,849 meters")
	if err := s.h.runner().Run(ctxBg, s.exp1.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := Close(ctxBg, s.h.j, s.h.st, s.exp1.ID); err != nil {
		t.Fatal(err)
	}
	base := s.h.state()
	exp := s.exp1.ID
	sub := record.Ref{Kind: "experiment", ID: string(exp)}
	hd := func(schema record.SchemaVer) record.Header {
		return record.Header{ID: record.NewID(), Schema: schema, Subject: sub, At: time.Now().UTC()}
	}
	// a fresh experiment record is its own subject
	self := func() record.Header {
		id := record.NewID()
		return record.Header{ID: id, Schema: "experiment/1", Subject: record.Ref{Kind: "experiment", ID: string(id)}, At: time.Now().UTC()}
	}
	var as0 *Assignment
	for _, as := range base.Assignments {
		if as.Ordinal == 0 {
			as0 = as
		}
	}
	att := base.Attestations[exp]
	cm := base.Commitments[exp]
	m := base.Measurements[exp]
	cases := []struct {
		name string
		mk   func(h *harness) []record.Record
		want string
	}{
		{"a second open experiment on the same hypothesis and population", func(h *harness) []record.Record {
			x := *s.exp1
			x.Header = self()
			x.Protocol.Experiment = x.ID
			// a closed one is not open: this forges a fresh open beside a closed one WITHOUT the version bump
			return []record.Record{&x}
		}, "must be version 2"},
		{"a re-open citing the wrong prior", func(h *harness) []record.Record {
			x := *s.exp1
			x.Header = self()
			x.Protocol.Experiment = x.ID
			x.Version, x.Prior = 2, cm.ID
			return []record.Record{&x}
		}, "must be version 2 citing attestation"},
		{"an assignment after closure", func(h *harness) []record.Record {
			return []record.Record{&Assignment{Header: hd("assignment/1"), Experiment: exp, Unit: as0.Unit, Ordinal: 0, Seed: Seed(exp, as0.Unit)}}
		}, "after"},
		{"evidence with altered exposure", func(h *harness) []record.Record {
			ev := *base.Evidence[as0.ID][Control]
			ev.Header = record.Header{ID: record.NewID(), Schema: "unit_evidence/1", RunID: ev.RunID, Attempt: ev.Attempt, Subject: ev.Subject, At: time.Now().UTC()}
			ev.Exposed = true
			return []record.Record{&ev}
		}, "twice"},
		{"an attestation with an altered score", func(h *harness) []record.Record {
			a := *att
			a.Header = hd("effect_attestation/1")
			a.Units = append([]UnitRow{}, att.Units...)
			a.Units[0].ControlScore = 1
			return []record.Record{&a}
		}, "attested twice"},
		{"a measurement with the wrong verdict", func(h *harness) []record.Record {
			x := *m
			x.Header = hd("effect_measurement/1")
			x.Verdict, x.ItemEffect = TreatmentHarmful, learn.ItemHarmful
			return []record.Record{&x}
		}, "measured twice"},
		{"a measurement transition moving another item", func(h *harness) []record.Record {
			return []record.Record{&learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(s.harmful.Item)}, At: time.Now().UTC()},
				Item: s.harmful.Item, Revision: s.harmful.Revision, From: learn.Candidate, To: learn.Effective, Actor: learn.ActorMeasurement, Evidence: m.ID, Why: "forged"}}
		}, "on evidence about"},
		{"a measurement transition to the wrong stage", func(h *harness) []record.Record {
			return []record.Record{&learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(s.helpful.Item)}, At: time.Now().UTC()},
				Item: s.helpful.Item, Revision: s.helpful.Revision, From: learn.Effective, To: learn.Quarantined, Actor: learn.ActorMeasurement, Evidence: m.ID, Why: "forged"}}
		}, "derives"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			h := s.h.snapshot()
			defer h.close()
			h.submit("forge/"+c.name, c.mk(h)...)
			_, err := Fold(h.j, h.st)
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("forged history folded: %v (want %q)", err, c.want)
			}
		})
	}
	// the first-write forgeries: the same shapes committed BEFORE the honest
	// record, against a snapshot taken before Close
	s2 := build(t)
	x := s2.open(t, s2.helpful, s2.everest, "8,849 meters")
	if err := s2.h.runner().Run(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
	pre := s2.h.state()
	var pas *Assignment
	for _, as := range pre.Assignments {
		if as.Ordinal == 0 {
			pas = as
		}
	}
	early := []struct {
		name string
		mk   func(h *harness) []record.Record
		want string
	}{
		{"a unit that is not in the protocol", func(h *harness) []record.Record {
			return []record.Record{&Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Unit: s2.k2[0], Ordinal: 0, Seed: Seed(x.ID, s2.k2[0])}}
		}, "not the protocol's"},
		{"an experiment with a blank fixture", func(h *harness) []record.Record {
			y := *x
			id := record.NewID()
			y.Header = record.Header{ID: id, Schema: "experiment/1", Subject: record.Ref{Kind: "experiment", ID: string(id)}, At: time.Now().UTC()}
			y.Protocol.Experiment = id
			y.Hypothesis = s2.harmful // another hypothesis: no fishing refusal first
			y.Arms = Arms(y.Hypothesis, y.Relation)
			y.Units = append([]UnitSpec{}, x.Units...)
			y.Units[2].Fixture = h.fixture(" ")
			return []record.Record{&y}
		}, "fixture is blank"},
		{"a unit assigned twice", func(h *harness) []record.Record {
			return []record.Record{&Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Unit: pas.Unit, Ordinal: 0, Seed: pas.Seed}}
		}, "assigned twice"},
		{"evidence with altered exposure", func(h *harness) []record.Record {
			// drop the honest control evidence by forging over a snapshot
			// taken before it existed is not possible (it is committed); so
			// forge a second-arm-shaped record for a unit whose arm is the
			// OTHER arm's run
			ev := *pre.Evidence[pas.ID][Control]
			ev.Header = record.Header{ID: record.NewID(), Schema: "unit_evidence/1", RunID: pre.Evidence[pas.ID][Treatment].RunID, Attempt: 1, Subject: record.Ref{Kind: "run", ID: string(pre.Evidence[pas.ID][Treatment].RunID)}, At: time.Now().UTC()}
			ev.Arm = Control
			return []record.Record{&ev}
		}, "twice"},
		{"a commitment before the cohort's evidence is complete", func(h *harness) []record.Record {
			units := []AssignedUnit{}
			for i, u := range x.Units {
				as := pre.assignment(x.ID, u.Goal)
				units = append(units, AssignedUnit{Unit: u.Goal, Assignment: as.ID, Ordinal: i, Seed: as.Seed})
			}
			cm := &CohortCommitment{Header: record.Header{ID: record.NewID(), Schema: "cohort_commitment/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Protocol: x.Protocol, Units: units, Root: CohortRoot(units), Count: len(units)}
			cl := &Closed{Header: record.Header{ID: record.NewID(), Schema: "cohort_closed/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Commitment: cm.ID, Count: 2}
			return []record.Record{cl, cm}
		}, "closes at 2"},
		{"a commitment with a foreign protocol", func(h *harness) []record.Record {
			units := []AssignedUnit{}
			for i, u := range x.Units {
				as := pre.assignment(x.ID, u.Goal)
				units = append(units, AssignedUnit{Unit: u.Goal, Assignment: as.ID, Ordinal: i, Seed: as.Seed})
			}
			p := x.Protocol
			p.Outcome.Margin = 0.5
			cm := &CohortCommitment{Header: record.Header{ID: record.NewID(), Schema: "cohort_commitment/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Protocol: p, Units: units, Root: CohortRoot(units), Count: len(units)}
			cl := &Closed{Header: record.Header{ID: record.NewID(), Schema: "cohort_closed/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Commitment: cm.ID, Count: 3}
			return []record.Record{cl, cm}
		}, "not " + string(x.ID) + "'s"},
	}
	for _, c := range early {
		t.Run("pre-close: "+c.name, func(t *testing.T) {
			h := s2.h.snapshot()
			defer h.close()
			h.submit("forge/"+c.name, c.mk(h)...)
			_, err := Fold(h.j, h.st)
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("forged history folded: %v (want %q)", err, c.want)
			}
		})
	}
}

// The runner is killed mid-arm and mid-cohort; the next Run finishes
// exactly what was owed: no arm runs twice, every arm gets one run, and
// Close measures the same cohort.
func TestRunnerResumesAfterKill(t *testing.T) {
	s := build(t)
	h := s.h
	x := s.open(t, s.helpful, s.everest, "8,849 meters")
	// kill 1: the driver dies after the first arm's execute is recorded but
	// before delivery — the arm run exists, non-terminal, no evidence
	r := h.runner()
	r.CrashAt = "after_recorded"
	if err := r.Run(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash, got %v", err)
	}
	st := h.state()
	if len(st.Runs.Replays) != 1 || len(st.Evidence) != 0 {
		t.Fatalf("after kill 1: %d replays, %d evidence", len(st.Runs.Replays), len(st.Evidence))
	}
	h.restart()
	// kill 2: dies after intake of the second arm (a goal with no run)
	r = h.runner()
	r.CrashAt = "after_intake"
	if err := r.Run(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash, got %v", err)
	}
	st = h.state()
	if len(st.Runs.Unstarted) != 1 || len(st.Evidence) != 1 {
		t.Fatalf("after kill 2: %d unstarted, %d evidence", len(st.Runs.Unstarted), len(st.Evidence))
	}
	h.restart()
	// a plain Resume must not start the arm's unstarted goal: it is the runner's
	d := &run.Driver{J: h.j, Store: h.st, Backend: h.exec, Origin: run.CLIOrigin{W: io.Discard}, Timeout: time.Minute}
	if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 {
		t.Fatalf("resume touched the arm: %v %d", err, len(reps))
	}
	if err := h.runner().Run(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
	st = h.state()
	runs := 0
	for _, byArm := range st.Runs.Replays {
		runs += len(byArm)
	}
	if runs != 6 || len(st.Assignments) != 3 || len(st.Runs.Unstarted) != 0 {
		t.Fatalf("after resume: %d arm runs, %d assignments, %d unstarted", runs, len(st.Assignments), len(st.Runs.Unstarted))
	}
	for _, as := range st.Assignments {
		if len(st.Evidence[as.ID]) != 2 {
			t.Fatalf("assignment %s has %d evidence", as.ID, len(st.Evidence[as.ID]))
		}
	}
	m, err := Close(ctxBg, h.j, h.st, x.ID)
	if err != nil || m.Verdict != TreatmentHelpful {
		t.Fatalf("%v %+v", err, m)
	}
	// the recovered arm's evidence is over its terminal attempt (2), and
	// its root re-derives on a restart
	h.restart()
	if _, err := Fold(h.j, h.st); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal([]byte(m.Verdict), []byte(TreatmentHelpful)) {
		t.Fatal("unreachable")
	}
}

// Open refuses what the protocol cannot hold: a unit of another family, a
// non-terminal unit, an arm goal as a unit, an unknown hypothesis.
func TestOpenRefusesBadUnits(t *testing.T) {
	s := build(t)
	h := s.h
	fx := h.fixture("x")
	bad := []struct {
		name string
		spec Spec
	}{
		{"mixed families", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: []UnitSpec{{Goal: s.everest[0], Fixture: fx}, {Goal: h.production("Write a script to list files"), Fixture: fx}}, Why: "w"}},
		{"unknown hypothesis", Spec{Hypothesis: learn.ItemRev{Item: learn.LearnedID(record.NewID()), Revision: record.NewID()}, Relation: ApplyItem, Units: []UnitSpec{{Goal: s.everest[0], Fixture: fx}}, Why: "w"}},
		{"no units", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Why: "w"}},
		{"not a goal", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: []UnitSpec{{Goal: record.NewID(), Fixture: fx}}, Why: "w"}},
		{"blank fixture", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: []UnitSpec{{Goal: s.everest[0], Fixture: h.fixture("  \n ")}}, Why: "w"}},
		{"two units with one goal text", Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: []UnitSpec{{Goal: s.everest[0], Fixture: fx}, {Goal: h.production("What is the height of Everest?"), Fixture: fx}}, Why: "w"}},
	}
	for _, c := range bad {
		if _, err := Open(ctxBg, h.j, h.st, c.spec); !errors.Is(err, ErrConfig) {
			t.Errorf("%s: %v", c.name, err)
		}
	}
	x := s.open(t, s.helpful, s.everest, "8,849 meters")
	if err := h.runner().Run(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
	st := h.state()
	var arm record.RecordID
	for _, byArm := range st.Runs.Replays {
		arm = byArm[Treatment].Goal.ID
	}
	if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.harmful, Relation: ApplyItem, Units: []UnitSpec{{Goal: arm, Fixture: fx}}, Why: "w"}); !errors.Is(err, ErrConfig) {
		t.Errorf("an arm as a unit: %v", err)
	}
	if _, err := Close(ctxBg, h.j, h.st, x.ID); err != nil {
		t.Fatal(err)
	}
	// a re-open is version 2 citing the prior attestation, and it runs
	x2 := s.open(t, s.helpful, s.everest, "8,849 meters")
	if x2.Version != 2 || x2.Prior != h.state().Attestations[x.ID].ID {
		t.Fatalf("re-open: %+v", x2)
	}
}

// revise commits a new revision of a lesson (predecessor = the current one).
func (h *harness) revise(ir learn.ItemRev, text string) learn.ItemRev {
	h.t.Helper()
	ref, err := h.st.Put(thought.LessonText, []byte(text))
	if err != nil {
		h.t.Fatal(err)
	}
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(ir.Item)}, At: time.Now().UTC()}, Item: ir.Item, Predecessor: ir.Revision, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	h.submit("revise/"+string(r.ID), r)
	return learn.ItemRev{Item: ir.Item, Revision: r.ID}
}

// assign commits an honest assignment of the protocol's unit i.
func (h *harness) assign(x *Experiment, i int) *Assignment {
	h.t.Helper()
	a := &Assignment{Header: record.Header{ID: record.NewID(), Schema: "assignment/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Unit: x.Units[i].Goal, Ordinal: i, Seed: Seed(x.ID, x.Units[i].Goal)}
	h.submit(fmt.Sprintf("experiment/%s/assign/%d", x.ID, i), a)
	return h.state().assignment(x.ID, x.Units[i].Goal)
}

// driveArm drives one arm run with the given forced sets, as an honest
// driver would — the records all re-derive; only the arm's contract with
// the protocol can refuse it.
func (h *harness) driveArm(as *Assignment, arm string, apply, withhold []learn.ItemRev) {
	h.t.Helper()
	unit := h.state().RunOf(as.Unit)
	text, _ := h.st.Get(unit.Goal.Text)
	d := &run.Driver{J: h.j, Store: h.st, Backend: h.exec, Origin: run.ReplayOrigin{}, Timeout: time.Minute,
		Replay: &run.ReplayContext{Assignment: as.ID, Arm: arm, Unit: as.Unit, Root: unit.Goal.Root, Apply: apply, Withhold: withhold}}
	if _, err := d.Run(ctxBg, text, unit.Goal.Delivery); err != nil {
		h.t.Fatal(err)
	}
}

// The arms differ by exactly the hypothesis: an arm run whose selections
// force anything else — an extra applied item on the treatment, a
// withheld item on the control, a swapped relation — is refused by the
// fold even though every one of its records re-derives (the request, the
// applications, the evidence are all honest about what it did).
func TestFoldRefusesArmsThatForceMoreThanTheHypothesis(t *testing.T) {
	s := build(t)
	h := s.h
	other := h.lesson("Always answer in one sentence.", learn.Candidate)
	x := s.open(t, s.helpful, s.everest, "8,849 meters")
	cases := []struct {
		name            string
		arm             string
		apply, withhold []learn.ItemRev
	}{
		{"treatment applying a second item", Treatment, []learn.ItemRev{s.helpful, other}, nil},
		{"treatment applying another item instead", Treatment, []learn.ItemRev{other}, nil},
		{"control withholding the hypothesis", Control, nil, []learn.ItemRev{s.helpful}},
		{"control applying the hypothesis", Control, []learn.ItemRev{s.helpful}, nil},
		{"treatment withholding instead of applying", Treatment, nil, []learn.ItemRev{s.helpful}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			g := h.snapshot()
			defer g.close()
			as := g.assign(x, 0)
			g.driveArm(as, c.arm, c.apply, c.withhold)
			if _, err := run.Fold(g.j.Production(), g.st); err != nil {
				t.Fatalf("the run fold refused an honest arm run: %v", err)
			}
			_, err := Fold(g.j, g.st)
			if err == nil || !strings.Contains(err.Error(), "not the protocol's") {
				t.Fatalf("arm forcing %v/%v folded: %v", c.apply, c.withhold, err)
			}
		})
	}
	// the honest arm folds
	as := h.assign(x, 0)
	h.driveArm(as, Treatment, []learn.ItemRev{s.helpful}, nil)
	if _, err := Fold(h.j, h.st); err != nil {
		t.Fatal(err)
	}
	// an arm run taken in before its assignment: refused
	g := h.snapshot()
	defer g.close()
	unit := g.state().RunOf(x.Units[1].Goal)
	fake := record.NewID()
	text, _ := g.st.Get(unit.Goal.Text)
	d := &run.Driver{J: g.j, Store: g.st, Backend: g.exec, Origin: run.ReplayOrigin{}, Timeout: time.Minute,
		Replay: &run.ReplayContext{Assignment: fake, Arm: Treatment, Unit: unit.Goal.ID, Root: unit.Goal.Root, Apply: []learn.ItemRev{s.helpful}}}
	if _, err := d.Run(ctxBg, text, unit.Goal.Delivery); err != nil {
		t.Fatal(err)
	}
	if _, err := Fold(g.j, g.st); err == nil || !strings.Contains(err.Error(), "not a record") {
		t.Fatalf("arm run citing a non-assignment folded: %v", err)
	}
}

// A superseded hypothesis is a stale experiment: the runner refuses to
// start an arm, and the fold refuses an arm that started anyway (recall
// forces an item at its current revision, so the arm would administer
// nothing and measure noise).
func TestStaleHypothesisIsRefused(t *testing.T) {
	s := build(t)
	h := s.h
	x := s.open(t, s.helpful, s.everest, "8,849 meters")
	as := h.assign(x, 0)
	h.revise(s.helpful, "Answer in metres.")
	if err := h.runner().Run(ctxBg, x.ID); !errors.Is(err, ErrRefused) || !strings.Contains(err.Error(), "stale") {
		t.Fatalf("runner on a stale experiment: %v", err)
	}
	h.driveArm(as, Treatment, []learn.ItemRev{s.helpful}, nil)
	if _, err := Fold(h.j, h.st); err == nil || !strings.Contains(err.Error(), "superseded") {
		t.Fatalf("stale arm folded: %v", err)
	}
}

// The first record of each production kind, forged: snapshots taken at the
// runner's and the closer's crash seams hold the honest history up to but
// not including that record, so the forgery is the first of its kind and
// the fold's re-derivation branches are what refuse it. The same seams
// prove Close resumes at every commit boundary.
func TestFoldRefusesFirstForgedRecordsAndCloseResumes(t *testing.T) {
	s := build(t)
	h := s.h
	x := s.open(t, s.helpful, s.everest, "8,849 meters")
	// seam 1: the treatment arm of unit 0 is terminal, its evidence not yet committed
	r := h.runner()
	r.CrashAt = "before_evidence"
	if err := r.Run(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash: %v", err)
	}
	{
		g := h.snapshot()
		st := g.state()
		if len(st.Evidence) != 0 || len(st.Runs.Replays) != 1 {
			t.Fatalf("seam 1: %d evidence, %d replays", len(st.Evidence), len(st.Runs.Replays))
		}
		as := st.assignment(x.ID, x.Units[0].Goal)
		rs := st.Runs.Replays[as.ID][Treatment]
		forged := EvidenceOf(rs, x.Hypothesis, x.ID, as.ID, as.Unit, Treatment)
		forged.Exposed = !forged.Exposed
		g.submit("forge/evidence", forged)
		if _, err := Fold(g.j, g.st); err == nil || !strings.Contains(err.Error(), "does not re-derive") {
			t.Fatalf("first forged evidence folded: %v", err)
		}
		g.close()
	}
	if err := h.runner().Run(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
	// seam 2: closed and committed, not attested
	// (snapshot reopens h's journal, so each seam builds its closer fresh)
	if _, err := (&Closer{J: h.j, Store: h.st, CrashAt: "close"}).Close(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash: %v", err)
	}
	if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: s.helpful, Relation: ApplyItem, Units: x.Units, Why: "re-open"}); !errors.Is(err, ErrRefused) || !strings.Contains(err.Error(), "not attested") {
		t.Fatalf("open after an unattested close: %v", err)
	}
	if err := h.runner().Run(ctxBg, x.ID); !errors.Is(err, ErrRefused) {
		t.Fatalf("run after close: %v", err)
	}
	{
		g := h.snapshot()
		st := g.state()
		cm := st.Commitments[x.ID]
		att := &EffectAttestation{Header: record.Header{ID: record.NewID(), Schema: "effect_attestation/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}, Experiment: x.ID, Cohort: cm.ID, Closure: st.Closed[x.ID].ID, Protocol: cm.Protocol, Evaluator: Evaluator, Estimator: Estimator}
		for i, u := range cm.Units {
			row, err := st.row(g.st, cm.Protocol, i, u)
			if err != nil {
				t.Fatal(err)
			}
			att.Units = append(att.Units, row)
		}
		att.Units[1].ControlScore = 1 // the evaluator "saw" the fixture in a control answer that lacks it
		g.submit("forge/attest", att)
		if _, err := Fold(g.j, g.st); err == nil || !strings.Contains(err.Error(), "does not recompute") {
			t.Fatalf("first forged attestation folded: %v", err)
		}
		g.close()
	}
	// seam 3: attested, not measured
	if _, err := (&Closer{J: h.j, Store: h.st, CrashAt: "attest"}).Close(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash: %v", err)
	}
	{
		g := h.snapshot()
		st := g.state()
		m := Measure(st.Attestations[x.ID])
		m.Header = record.Header{ID: record.NewID(), Schema: "effect_measurement/1", Subject: record.Ref{Kind: "experiment", ID: string(x.ID)}, At: time.Now().UTC()}
		m.Verdict, m.ItemEffect = TreatmentHarmful, learn.ItemHarmful
		g.submit("forge/measure", m)
		if _, err := Fold(g.j, g.st); err == nil || !strings.Contains(err.Error(), "not the estimator's fold") {
			t.Fatalf("first forged measurement folded: %v", err)
		}
		g.close()
	}
	// seam 4: measured, no transition; then the plain Close finishes
	if _, err := (&Closer{J: h.j, Store: h.st, CrashAt: "measure"}).Close(ctxBg, x.ID); !errors.Is(err, run.ErrCrashed) {
		t.Fatalf("want a crash: %v", err)
	}
	st := h.state()
	if st.Measurements[x.ID] == nil || len(st.Runs.Learned.Items[s.helpful.Item].Transitions[s.helpful.Revision]) != 0 {
		t.Fatal("seam 4: measurement without transition expected")
	}
	m, err := Close(ctxBg, h.j, h.st, x.ID)
	if err != nil || m.ID != st.Measurements[x.ID].ID || m.Verdict != TreatmentHelpful {
		t.Fatalf("%v %+v", err, m)
	}
	st = h.state()
	if n := len(st.Runs.Learned.Items[s.helpful.Item].Transitions[s.helpful.Revision]); n != 1 || st.Runs.Learned.Items[s.helpful.Item].StageOf(s.helpful.Revision) != learn.Effective {
		t.Fatalf("after resume: %d transitions", n)
	}
	// and a closure whose commitment was not in the same command is refused
	{
		s2 := build(t)
		x2 := s2.open(t, s2.helpful, s2.everest, "8,849 meters")
		if err := s2.h.runner().Run(ctxBg, x2.ID); err != nil {
			t.Fatal(err)
		}
		pre := s2.h.state()
		var units []AssignedUnit
		for i, u := range x2.Units {
			as := pre.assignment(x2.ID, u.Goal)
			units = append(units, AssignedUnit{Unit: u.Goal, Assignment: as.ID, Ordinal: i, Seed: as.Seed})
		}
		sub := record.Ref{Kind: "experiment", ID: string(x2.ID)}
		cm := &CohortCommitment{Header: record.Header{ID: record.NewID(), Schema: "cohort_commitment/1", Subject: sub, At: time.Now().UTC()}, Experiment: x2.ID, Protocol: x2.Protocol, Units: units, Root: CohortRoot(units), Count: len(units)}
		cl := &Closed{Header: record.Header{ID: record.NewID(), Schema: "cohort_closed/1", Subject: sub, At: time.Now().UTC()}, Experiment: x2.ID, Commitment: cm.ID, Count: 3}
		s2.h.submit("forge/close-1", cl)
		s2.h.submit("forge/close-2", cm)
		if _, err := Fold(s2.h.j, s2.h.st); err == nil || !strings.Contains(err.Error(), "not one command") {
			t.Fatalf("split closure folded: %v", err)
		}
	}
}

// A failed arm delivers the framework's envelope, which the oracle must
// never score (a fixture of "failed" would match it): the evidence marks
// the arm missing and the pair leaves the analysis.
func TestFailedArmIsMissingNotScored(t *testing.T) {
	s := build(t)
	h := s.h
	x := s.open(t, s.helpful, s.everest[:1], "failed")
	r := h.runner()
	r.Backend = &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "m"}, Calls: []invoke.ScriptedCall{{FailBefore: true}, {Response: []byte("29,032 feet")}}}
	if err := r.Run(ctxBg, x.ID); err != nil {
		t.Fatal(err)
	}
	st := h.state()
	as := st.assignment(x.ID, x.Units[0].Goal)
	tr, ct := st.Evidence[as.ID][Treatment], st.Evidence[as.ID][Control]
	if tr.Missing != MissingNotComplete || tr.Deliverable != nil || ct.Deliverable == nil {
		t.Fatalf("evidence %+v %+v", tr, ct)
	}
	// the envelope the treatment delivered does contain the fixture
	payload := h.deliverable(st.Runs.Replays[as.ID][Treatment])
	if Score([]byte(payload), []byte("failed")) != 1 {
		t.Fatalf("the failure envelope %q does not contain the fixture; the test no longer shows the hazard", payload)
	}
	m, err := Close(ctxBg, h.j, h.st, x.ID)
	if err != nil || m.Assigned != 1 || m.Analyzed != 0 || m.Verdict != Insufficient {
		t.Fatalf("%v %+v", err, m)
	}
	if row := h.state().Attestations[x.ID].Units[0]; row.TreatmentMissing != MissingNotComplete || row.TreatmentScore != 0 {
		t.Fatalf("row %+v", row)
	}
}
