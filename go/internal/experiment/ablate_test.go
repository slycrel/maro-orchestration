package experiment

import (
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

func (h *harness) stage(ir learn.ItemRev, from, to learn.Stage) {
	h.t.Helper()
	h.submit("stage/"+string(ir.Revision)+"/"+string(to), &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(ir.Item)}, At: time.Now().UTC()},
		Item: ir.Item, Revision: ir.Revision, From: from, To: to, Actor: learn.ActorOperator, Why: "test"})
}

// D17 inside the process: a mechanism is on because a seed says so, an
// `ablate(m)` experiment withholds the seed for its treatment arm, an
// equivalent measurement tombstones it, and the next production run's
// PolicySelection turns the mechanism off with the tombstone as its
// absence proof — the driver path changes because of evidence, through
// the same door a lesson goes through. A harmful ablation leaves the
// seed canon.
func TestAblateMechanismByEvidence(t *testing.T) {
	either := &invoke.Keyed{Caps: invoke.Capabilities{Name: "keyed-judge", Model: "judge"}, Rules: []invoke.Rule{
		{Key: "meters", Answer: `{"outcome":"achieved","confidence":0.9,"why":"a height"}`},
		{Key: "feet", Answer: `{"outcome":"achieved","confidence":0.9,"why":"a height"}`},
	}, Def: `{"outcome":"not_achieved","confidence":0.9,"why":"no height"}`}
	goals := []string{"What is the height of Mount Everest?", "How high is Everest?", "What elevation does Everest reach?", "How tall is Mount Everest?"}
	t.Run("equivalent ablation disables the mechanism", func(t *testing.T) {
		s := build(t)
		h := s.h
		h.stage(s.helpful, learn.Candidate, learn.Effective)
		// production with the seeds: recall on, the effective lesson reaches
		// the request
		rs := h.live(goals[0], nil)
		seed := h.state().Runs.Learned.Seed(learn.MechRecall)
		if seed == nil || seed.StageOf(seed.Current.ID) != learn.Canon || !strings.Contains(h.request(rs), "Answer in meters.") || h.deliverable(rs) != "8,849 meters" || !rs.Latest().Attempt.Config.Mechanisms[learn.MechRecall] {
			t.Fatalf("seeded production: seed %+v request %q", seed, h.request(rs))
		}
		hyp := learn.ItemRev{Item: seed.ID, Revision: seed.Current.ID}
		x, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: hyp, Relation: AblateItem, Live: true, Population: "answer", N: 4, Why: "ablate recall"})
		if err != nil {
			t.Fatal(err)
		}
		for i, q := range goals {
			rs := h.live(q, nil)
			a := rs.Latest()
			if rs.Goal.Arm == nil {
				t.Fatalf("goal %d not admitted", i)
			}
			switch rs.Goal.Arm.Arm {
			case Treatment:
				// the seed withheld: recall off for this run and nothing else
				if a.Attempt.Config.Mechanisms[learn.MechRecall] || !a.Attempt.Config.Mechanisms[learn.MechModelJudge] || h.request(rs) != q || h.deliverable(rs) != "29,032 feet" {
					t.Fatalf("treatment %d: config %+v request %q", i, a.Attempt.Config.Mechanisms, h.request(rs))
				}
				var withheld *learn.Exclusion
				for j := range a.Policy.Excluded {
					if a.Policy.Excluded[j].Item == seed.ID {
						withheld = &a.Policy.Excluded[j]
					}
				}
				if withheld == nil || withheld.Reason != learn.ExcludedWithheld || withheld.Basis != rs.Goal.Arm.Assignment || withheld.Stage != learn.Canon {
					t.Fatalf("treatment %d: excluded %+v", i, a.Policy.Excluded)
				}
				if a.Recall.ExcludedCounts["policy:recall_off"] == 0 {
					t.Fatalf("treatment %d: recall %+v", i, a.Recall)
				}
			case Control:
				if !a.Attempt.Config.Mechanisms[learn.MechRecall] || !strings.Contains(h.request(rs), "Answer in meters.") || h.deliverable(rs) != "8,849 meters" {
					t.Fatalf("control %d: request %q", i, h.request(rs))
				}
			}
		}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: either, Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != Equivalent || m.ItemEffect != learn.ItemRedundant || m.Exposed != 4 || m.TreatmentN != 2 || m.ControlN != 2 {
			t.Fatalf("measurement %+v", m)
		}
		seed = st.Runs.Learned.Seed(learn.MechRecall)
		trs := seed.Transitions[seed.Current.ID]
		if seed.StageOf(seed.Current.ID) != learn.Tombstone || trs[len(trs)-1].Actor != learn.ActorMeasurement || trs[len(trs)-1].Evidence != m.ID {
			t.Fatalf("seed after the measurement: %s %+v", seed.StageOf(seed.Current.ID), trs[len(trs)-1])
		}
		// the consequence: recall is off in production, the effective lesson
		// no longer reaches the request, and the selection says why
		rs = h.live("How high is Everest, really?", nil)
		a := rs.Latest()
		if rs.Goal.Arm != nil || a.Attempt.Config.Mechanisms[learn.MechRecall] || h.request(rs) != "How high is Everest, really?" || h.deliverable(rs) != "29,032 feet" {
			t.Fatalf("after the ablation: config %+v request %q", a.Attempt.Config.Mechanisms, h.request(rs))
		}
		if len(a.Policy.Excluded) != 1 || a.Policy.Excluded[0] != (learn.Exclusion{Item: seed.ID, Revision: seed.Current.ID, Stage: learn.Tombstone, Basis: trs[len(trs)-1].ID, Reason: learn.ExcludedStanding}) {
			t.Fatalf("absence proof: %+v", a.Policy.Excluded)
		}
		if a.Recall.ExcludedCounts["policy:recall_off"] == 0 || len(a.Recall.Included) != 0 {
			t.Fatalf("recall after the ablation: %+v", a.Recall)
		}
		// a seed is never re-opened as apply: the ledger has no seed to apply
		if _, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: hyp, Relation: AblateItem, Live: true, Population: "answer", N: 4, Why: "again"}); err == nil {
			t.Fatal("a tombstoned seed re-ablated")
		}
	})
	t.Run("harmful ablation keeps the seed", func(t *testing.T) {
		s := build(t)
		h := s.h
		h.stage(s.helpful, learn.Candidate, learn.Effective)
		h.live("What is the height of Mount Everest in meters?", nil)
		seed := h.state().Runs.Learned.Seed(learn.MechRecall)
		hyp := learn.ItemRev{Item: seed.ID, Revision: seed.Current.ID}
		x, err := Open(ctxBg, h.j, h.st, Spec{Hypothesis: hyp, Relation: AblateItem, Live: true, Population: "answer", N: 4, Why: "ablate recall"})
		if err != nil {
			t.Fatal(err)
		}
		for _, q := range everestGoals {
			h.live(q, nil)
		}
		if _, err := (&Lane{J: h.j, Store: h.st, Judge: judgeFor("8,849 meters"), Timeout: time.Minute}).Pass(ctxBg); err != nil {
			t.Fatal(err)
		}
		st := h.state()
		m := st.Measurements[x.ID]
		if m == nil || m.Verdict != TreatmentHarmful || m.ItemEffect != learn.ItemHelpful || m.DeltaPP != -1 {
			t.Fatalf("measurement %+v", m)
		}
		seed = st.Runs.Learned.Seed(learn.MechRecall)
		if seed.StageOf(seed.Current.ID) != learn.Canon {
			t.Fatalf("seed moved: %s", seed.StageOf(seed.Current.ID))
		}
		rs := h.live("How many meters tall is Everest, really?", nil)
		if rs.Goal.Arm != nil || !rs.Latest().Attempt.Config.Mechanisms[learn.MechRecall] || h.deliverable(rs) != "8,849 meters" {
			t.Fatalf("after a harmful ablation: %q", h.deliverable(rs))
		}
	})
}
