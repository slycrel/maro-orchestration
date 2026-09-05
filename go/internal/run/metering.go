package run

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Metering (§11, D13). A target is an optional envelope with a Why,
// recorded at intake against the goal. It is measured, never enforced: an
// attempt that exceeds it commits an Overage — an event the delivery
// carries — and continues. Nothing here can stop a run.

// Dimension is what a target measures.
type Dimension string

const (
	DimCostUSD Dimension = "cost_usd" // reported cost, USD
	DimTokens  Dimension = "tokens"   // input + output tokens
	DimWallMS  Dimension = "wall_ms"  // backend wall time, ms
)

var dimensions = map[Dimension]bool{DimCostUSD: true, DimTokens: true, DimWallMS: true}

const (
	KindMeteringTarget record.Kind = "metering_target"
	KindOverage        record.Kind = "overage"
)

// MeteringTarget is the goal's envelope: one per goal, committed with it.
type MeteringTarget struct {
	record.ProductionRecord
	record.Header `json:"header"` // subject: the goal
	Goal          record.RecordID `json:"goal"`
	Name          string          `json:"name"`
	Dimension     Dimension       `json:"dimension"`
	Limit         float64         `json:"limit"`
	Why           string          `json:"why"`
}

func (r *MeteringTarget) Head() *record.Header { return &r.Header }
func (r *MeteringTarget) Kind() record.Kind    { return KindMeteringTarget }
func (r *MeteringTarget) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Subject.Kind != "goal" || record.RecordID(r.Subject.ID) != r.Goal || record.ValidateID(r.Goal) != nil {
		return errors.New("metering_target: subject must be its goal")
	}
	if strings.TrimSpace(r.Name) == "" || strings.TrimSpace(r.Why) == "" {
		return errors.New("metering_target: a target has a name and a why")
	}
	if !dimensions[r.Dimension] {
		return fmt.Errorf("metering_target: dimension %q out of vocabulary", r.Dimension)
	}
	if !(r.Limit > 0) || math.IsInf(r.Limit, 0) {
		return errors.New("metering_target: limit must be finite and positive")
	}
	return nil
}

// Overage is the event: an attempt's recorded usage on the target's
// dimension exceeded the limit. Run-scoped; at most one per attempt; only
// after the attempt is recorded (the measurement is the recorded usage).
type Overage struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Goal          record.RecordID `json:"goal"`
	Target        record.RecordID `json:"target"`
	Dimension     Dimension       `json:"dimension"`
	Measured      float64         `json:"measured"`
	Limit         float64         `json:"limit"`
}

func (r *Overage) Head() *record.Header { return &r.Header }
func (r *Overage) Kind() record.Kind    { return KindOverage }
func (r *Overage) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt == 0 || r.Subject != runRef(r.RunID) {
		return errors.New("overage: must be run-scoped with the run as subject")
	}
	if record.ValidateID(r.Goal) != nil || record.ValidateID(r.Target) != nil {
		return errors.New("overage: goal and target ids required")
	}
	if !dimensions[r.Dimension] {
		return fmt.Errorf("overage: dimension %q out of vocabulary", r.Dimension)
	}
	if math.IsNaN(r.Measured) || math.IsInf(r.Measured, 0) || math.IsNaN(r.Limit) || math.IsInf(r.Limit, 0) {
		return errors.New("overage: measured and limit must be finite")
	}
	if !(r.Measured > r.Limit) {
		return errors.New("overage: measured must exceed the limit")
	}
	return nil
}

// TargetSpec is a target as the operator states it (before a goal exists).
type TargetSpec struct {
	Name      string
	Dimension Dimension
	Limit     float64
	Why       string
}

// ParseTarget reads `<dimension>=<limit>` and a why. The name is the
// dimension unless the spec carries `name:` — `cost_usd=2.00` reads as the
// target "cost_usd"; the why is required (§11: every target has one).
func ParseTarget(spec, why string) (*TargetSpec, error) {
	dim, lim, ok := strings.Cut(strings.TrimSpace(spec), "=")
	if !ok {
		return nil, fmt.Errorf("%w: target %q is not <dimension>=<limit>", ErrConfig, spec)
	}
	d := Dimension(strings.TrimSpace(dim))
	if !dimensions[d] {
		return nil, fmt.Errorf("%w: target dimension %q (known: cost_usd, tokens, wall_ms)", ErrConfig, dim)
	}
	v, err := strconv.ParseFloat(strings.TrimSpace(lim), 64)
	if err != nil || !(v > 0) || math.IsInf(v, 0) {
		return nil, fmt.Errorf("%w: target limit %q must be a positive number", ErrConfig, lim)
	}
	if strings.TrimSpace(why) == "" {
		return nil, fmt.Errorf("%w: a target needs a why (--why)", ErrConfig)
	}
	return &TargetSpec{Name: string(d), Dimension: d, Limit: v, Why: strings.TrimSpace(why)}, nil
}

// Record is the target as committed against a goal.
func (t *TargetSpec) Record(goal record.RecordID) *MeteringTarget {
	subj := record.Ref{Kind: "goal", ID: string(goal)}
	return &MeteringTarget{Header: record.Header{ID: record.NewID(), Schema: "metering_target/1", Subject: subj, At: now()}, Goal: goal, Name: t.Name, Dimension: t.Dimension, Limit: t.Limit, Why: t.Why}
}

// MeasuredOn reads a usage on one dimension.
func MeasuredOn(u invoke.Usage, d Dimension) float64 {
	switch d {
	case DimCostUSD:
		return u.CostUSD
	case DimTokens:
		return float64(u.InputTokens + u.OutputTokens)
	case DimWallMS:
		return float64(u.WallMillis)
	}
	return 0
}

func num(v float64) string { return strconv.FormatFloat(v, 'f', -1, 64) }

// MeteringLine is the delivery's metering line for a recorded attempt:
// measured against the target, under or OVER, with the why — present
// whenever a target exists, so an under-target run says so too.
func MeteringLine(t *MeteringTarget, u invoke.Usage, ov *Overage) string {
	m := MeasuredOn(u, t.Dimension)
	verdict := "under"
	if m > t.Limit {
		verdict = "OVER by " + num(m-t.Limit)
		if ov != nil {
			verdict += " (overage " + string(ov.ID) + ")"
		}
	}
	rep := ""
	if t.Dimension == DimCostUSD && !u.CostReported {
		rep = ", cost not reported by the backend"
	}
	return fmt.Sprintf("metering: %s — %s measured %s, target %s (%s): %s%s", t.Name, t.Dimension, num(m), num(t.Limit), t.Why, verdict, rep)
}

// meter commits the overage for a recorded attempt whose usage exceeds the
// goal's target (idempotent: a folded overage is kept). The run continues
// regardless — the event is the whole response (D13).
func (d *Driver) meter(ctx context.Context, rs *RunState, a *AttemptState, rec *Transition) error {
	if rs.Target == nil || a.Overage != nil {
		return nil
	}
	t := rs.Target
	m := MeasuredOn(rec.Outcome.Usage, t.Dimension)
	if !(m > t.Limit) {
		return nil
	}
	n := a.Attempt.Attempt
	ov := &Overage{Header: header(runRef(rs.Run), rs.Run, n, "overage/1"), Goal: rs.Goal.ID, Target: t.ID, Dimension: t.Dimension, Measured: m, Limit: t.Limit}
	if err := d.commit(ctx, fmt.Sprintf("overage/%s/%d", rs.Run, n), ov); err != nil {
		return err
	}
	a.Overage = ov
	d.emit(rs, n, "overage", Recorded, fmt.Sprintf("%s %s over target %s (%s)", t.Dimension, num(m), num(t.Limit), t.Name))
	return nil
}
