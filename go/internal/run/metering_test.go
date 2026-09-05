package run

import (
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// §11 / D13: a target is committed with the goal, measured on the recorded
// usage, reported on every delivery, and never enforced. Under: the line
// says under. Over: an Overage is committed before the delivery and the
// line names it; the mission is still delivered. No target: no line.
func TestMeteringTargetIsMeasuredNeverEnforced(t *testing.T) {
	h := open(t)
	under := invoke.Usage{InputTokens: 10, OutputTokens: 5, CostUSD: 0.5, CostReported: true, WallMillis: 40}
	over := invoke.Usage{InputTokens: 900, OutputTokens: 300, CostUSD: 2.5, CostReported: true, WallMillis: 9000}
	exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("one"), Usage: under}, invoke.ScriptedCall{Response: []byte("two"), Usage: over}, invoke.ScriptedCall{Response: []byte("three"), Usage: over})
	spec, err := ParseTarget("cost_usd=2.00", "Manti envelope")
	if err != nil || spec.Dimension != DimCostUSD || spec.Limit != 2 || spec.Name != "cost_usd" {
		t.Fatalf("parse: %+v %v", spec, err)
	}
	run := func(h *harness, exec invoke.Backend, target *TargetSpec) (*Report, *RunState) {
		t.Helper()
		d := h.driver(exec, nil)
		d.Target = target
		rep, err := d.Run(ctxBg, []byte("Where can I get non-ethanol gas in or around Manti, Utah?"), DeliveryPolicy{Required: TransportAccepted})
		if err != nil {
			t.Fatal(err)
		}
		led := h.ledger()
		for _, rs := range led.Runs {
			if HandleOf(rs.Run) == rep.Handle {
				return rep, rs
			}
		}
		t.Fatal("run not folded")
		return nil, nil
	}
	rep1, rs1 := run(h, exec, spec)
	a1 := rs1.Latest()
	if rs1.Target == nil || rs1.Target.Why != "Manti envelope" || a1.Overage != nil || rep1.Mission.Outcome != MissionDelivered {
		t.Fatalf("under: target %+v overage %+v mission %s", rs1.Target, a1.Overage, rep1.Mission.Outcome)
	}
	wantLine := "metering: cost_usd — cost_usd measured 0.5, target 2 (Manti envelope): under"
	if !strings.HasSuffix(string(rep1.Payload), "one\n\n"+wantLine) || !strings.Contains(h.out.String(), wantLine) {
		t.Fatalf("under payload: %q", rep1.Payload)
	}
	// the target rides the goal's command: goal, assessment, target, adjacent
	var kinds []record.Kind
	h.j.Production().Scan(0, func(r record.Record) error { kinds = append(kinds, r.Kind()); return nil })
	if len(kinds) < 3 || kinds[0] != KindGoal || kinds[1] != KindFamilyAssessment || kinds[2] != KindMeteringTarget {
		t.Fatalf("intake order: %v", kinds[:3])
	}

	rep2, rs2 := run(h, exec, spec)
	a2 := rs2.Latest()
	if a2.Overage == nil || a2.Overage.Measured != 2.5 || a2.Overage.Limit != 2 || a2.Overage.Target != rs2.Target.ID || a2.Overage.Goal != rs2.Goal.ID || rep2.Mission.Outcome != MissionDelivered {
		t.Fatalf("over: overage %+v mission %s", a2.Overage, rep2.Mission.Outcome)
	}
	if !strings.Contains(string(rep2.Payload), "measured 2.5, target 2 (Manti envelope): OVER by 0.5 (overage "+string(a2.Overage.ID)+")") {
		t.Fatalf("over payload: %q", rep2.Payload)
	}
	if h.count(KindOverage) != 1 || h.count(KindMeteringTarget) != 2 {
		t.Fatalf("records: overage %d target %d", h.count(KindOverage), h.count(KindMeteringTarget))
	}
	seen := false
	for _, e := range h.events {
		if e.Run == rs2.Run && e.Stage == "overage" && strings.Contains(e.Detail, "cost_usd 2.5 over target 2") {
			seen = true
		}
	}
	if !seen {
		t.Fatal("no overage event")
	}
	if lines := strings.Join(Inspect(rs2), "\n"); !strings.Contains(lines, "OVER by 0.5") {
		t.Fatalf("inspect: %s", lines)
	}

	rep3, rs3 := run(h, exec, nil)
	if rs3.Target != nil || rs3.Latest().Overage != nil || strings.Contains(string(rep3.Payload), "metering:") {
		t.Fatalf("no target: %q", rep3.Payload)
	}

	// dimensions
	if MeasuredOn(over, DimTokens) != 1200 || MeasuredOn(over, DimWallMS) != 9000 || MeasuredOn(over, DimCostUSD) != 2.5 || MeasuredOn(over, "beans") != 0 {
		t.Fatal("MeasuredOn")
	}
	unreported := invoke.Usage{CostUSD: 0}
	if l := MeteringLine(rs1.Target, unreported, nil); !strings.Contains(l, "cost not reported by the backend") {
		t.Fatalf("unreported: %s", l)
	}

	// the operator's spec: refused before a goal exists
	for _, c := range [][2]string{{"cost_usd", "w"}, {"beans=2", "w"}, {"cost_usd=0", "w"}, {"cost_usd=-1", "w"}, {"cost_usd=nan", "w"}, {"cost_usd=inf", "w"}, {"cost_usd=x", "w"}, {"cost_usd=2", ""}, {"", "w"}} {
		if _, err := ParseTarget(c[0], c[1]); !errors.Is(err, ErrConfig) {
			t.Fatalf("%q/%q: %v", c[0], c[1], err)
		}
	}

	// forged records at the door: refused, nothing written
	head := h.j.Head()
	door := func(name, want string, recs ...record.Record) {
		t.Helper()
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/" + name, Epoch: h.j.Epoch(), Records: recs})
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("%s: want %q, got %v", name, want, err)
		}
		if h.j.Head() != head {
			t.Fatalf("%s: written", name)
		}
	}
	tgt := func(goal record.RecordID, f func(*MeteringTarget)) *MeteringTarget {
		x := (&TargetSpec{Name: "cost_usd", Dimension: DimCostUSD, Limit: 2, Why: "w"}).Record(goal)
		if f != nil {
			f(x)
		}
		return x
	}
	door("target limit 0", "finite and positive", tgt(rs3.Goal.ID, func(x *MeteringTarget) { x.Limit = 0 }))
	door("target limit inf", "finite and positive", tgt(rs3.Goal.ID, func(x *MeteringTarget) { x.Limit = math.Inf(1) }))
	door("target dimension", "out of vocabulary", tgt(rs3.Goal.ID, func(x *MeteringTarget) { x.Dimension = "beans" }))
	door("target no why", "name and a why", tgt(rs3.Goal.ID, func(x *MeteringTarget) { x.Why = " " }))
	door("target subject", "subject must be its goal", tgt(rs3.Goal.ID, func(x *MeteringTarget) { x.Subject = runRef(rs3.Run) }))
	ov := func(rs *RunState, f func(*Overage)) *Overage {
		x := &Overage{Header: header(runRef(rs.Run), rs.Run, 1, "overage/1"), Goal: rs.Goal.ID, Target: rs.Target.ID, Dimension: DimCostUSD, Measured: 3, Limit: 2}
		if f != nil {
			f(x)
		}
		return x
	}
	door("overage not over", "must exceed the limit", ov(rs1, func(x *Overage) { x.Measured = 2 }))
	door("overage nan", "must be finite", ov(rs1, func(x *Overage) { x.Measured = math.NaN() }))
	door("overage unscoped", "run-scoped", ov(rs1, func(x *Overage) { x.RunID = "" }))
	door("overage dimension", "out of vocabulary", ov(rs1, func(x *Overage) { x.Dimension = "beans" }))

	// forged histories: well-formed records the driver could not have
	// written; each on a fresh replica of the three runs, and the fold
	// refuses the history
	type trio struct{ rs1, rs2, rs3 *RunState }
	fold := func(name, want string, forge func(x trio) record.Record) {
		t.Helper()
		h := open(t)
		exec := scripted(toolless, invoke.ScriptedCall{Response: []byte("one"), Usage: under}, invoke.ScriptedCall{Response: []byte("two"), Usage: over}, invoke.ScriptedCall{Response: []byte("three"), Usage: over})
		_, r1 := run(h, exec, spec)
		_, r2 := run(h, exec, spec)
		_, r3 := run(h, exec, nil)
		if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged/" + name, Epoch: h.j.Epoch(), Records: []record.Record{forge(trio{r1, r2, r3})}}); err != nil {
			t.Fatalf("%s: the door refused a well-formed record: %v", name, err)
		}
		if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("%s: want fold refusal %q, got %v", name, want, err)
		}
	}
	fold("target for no goal", "not committed first", func(x trio) record.Record { return tgt(record.NewID(), nil) })
	fold("second target", "targeted twice", func(x trio) record.Record { return tgt(x.rs1.Goal.ID, nil) })
	fold("overage measures what was not recorded", "measures 3 but the recorded usage is 0.5", func(x trio) record.Record { return ov(x.rs1, nil) })
	fold("overage twice", "overage twice", func(x trio) record.Record { return ov(x.rs2, func(o *Overage) { o.Measured = 2.5 }) })
	fold("overage cites another target", "does not cite the goal's target", func(x trio) record.Record {
		return ov(x.rs1, func(o *Overage) { o.Target = x.rs2.Target.ID })
	})
	fold("overage on a run without a target", "does not cite the goal's target", func(x trio) record.Record {
		return ov(x.rs2, func(o *Overage) {
			o.Measured, o.RunID, o.Subject, o.Goal = 2.5, x.rs3.Run, runRef(x.rs3.Run), x.rs3.Goal.ID
		})
	})
	fold("overage on an attempt that does not exist", "attempt 2", func(x trio) record.Record {
		return ov(x.rs2, func(o *Overage) { o.Measured, o.Attempt = 2.5, 2 })
	})
}
