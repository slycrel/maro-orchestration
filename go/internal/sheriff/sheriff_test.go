package sheriff

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var ctxBg = context.Background()

func open(t *testing.T) (*journal.Journal, *thought.Store) {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, _ := r.Announce(io.Discard)
	a.Ensure()
	l, err := workspace.Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Release() })
	j, err := journal.Open(l)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	st, _ := thought.Open(a)
	return j, st
}

func stuckVerdicts(t *testing.T, j *journal.Journal) []*verdict.Verdict {
	t.Helper()
	var out []*verdict.Verdict
	j.Production().Scan(0, func(r record.Record) error {
		if v, ok := r.(*verdict.Verdict); ok && v.VerdictKind == verdict.KindStuck {
			out = append(out, v)
		}
		return nil
	})
	return out
}

// An attempt with no committed activity past StallAfter gets exactly one
// deterministic stuck verdict naming its last evidence, resolved to
// `stuck`; ticks and restarts do not repeat it; a progressing or recorded
// attempt gets none; the mission fold reports it.
func TestStuckIsEvidenceNamedOnce(t *testing.T) {
	j, st := open(t)
	// an attempt that died mid-execute (crash seam), so nothing moves it
	d := &run.Driver{J: j, Store: st, Backend: &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "m"}, Calls: []invoke.ScriptedCall{{Response: []byte("x")}}}, Origin: run.CLIOrigin{W: io.Discard}}
	d.CrashAt = "invoke:dispatched"
	if _, err := d.Run(ctxBg, []byte("q?"), run.DeliveryPolicy{Required: run.TransportAccepted}); !errors.Is(err, invoke.ErrCrashed) {
		t.Fatal(err)
	}
	clock := time.Now().UTC()
	s := &Sheriff{J: j, Store: st, StallAfter: 10 * time.Minute, Clock: func() time.Time { return clock }}
	if _, err := s.Evaluate(ctxBg); err != nil {
		t.Fatal(err)
	}
	if n := len(stuckVerdicts(t, j)); n != 0 {
		t.Fatalf("fresh attempt called stuck: %d", n)
	}
	clock = clock.Add(11 * time.Minute)
	var emitted []*verdict.Verdict
	s.Emitted = func(v *verdict.Verdict) { emitted = append(emitted, v) }
	for i := 0; i < 3; i++ { // repeated ticks
		if _, err := s.Evaluate(ctxBg); err != nil {
			t.Fatal(err)
		}
	}
	vs := stuckVerdicts(t, j)
	if len(vs) != 1 || len(emitted) != 1 || vs[0].Source.Standing != verdict.StandingDeterministic || vs[0].Outcome != "stuck" {
		t.Fatalf("stuck verdicts: %d emitted %d %+v", len(vs), len(emitted), vs)
	}
	if len(vs[0].Basis) != 1 || vs[0].Basis[0].Kind != invoke.KindDispatched && vs[0].Basis[0].Kind != invoke.KindInvocation {
		t.Fatalf("basis does not name the last evidence: %+v", vs[0].Basis)
	}
	led, err := run.Fold(j.Production(), st)
	if err != nil {
		t.Fatal(err)
	}
	for _, rs := range led.Runs {
		m := run.MissionOf(rs)
		if rs.Latest().Stuck == nil || m.Stuck == "" || rs.Latest().Stuck.Outcome != "stuck" || rs.Latest().Stuck.Rule != "standing:deterministic" {
			t.Fatalf("mission does not carry the stuck resolution: %+v", m)
		}
	}
	// a recovered, delivered run is never called stuck, however old
	d = &run.Driver{J: j, Store: st, Backend: &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "m"}, Calls: []invoke.ScriptedCall{{Response: []byte("x")}}}, Origin: run.CLIOrigin{W: io.Discard}}
	if _, err := d.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	clock = clock.Add(24 * time.Hour)
	if _, err := s.Evaluate(ctxBg); err != nil {
		t.Fatal(err)
	}
	if n := len(stuckVerdicts(t, j)); n != 1 {
		t.Fatalf("a delivered run was called stuck: %d", n)
	}
}

// Under the supervisor the sheriff is a lane: it evaluates on ticks,
// heartbeats its watermark, and quiesces cleanly.
func TestSheriffIsASupervisedLane(t *testing.T) {
	j, st := open(t)
	tick := make(chan struct{})
	s := &Sheriff{J: j, Store: st, Tick: tick, Every: time.Second}
	sup := supervise.New(j)
	sup.MinBeat = 0
	if err := sup.Register(s); err != nil {
		t.Fatal(err)
	}
	if err := sup.Start(ctxBg); err != nil {
		t.Fatal(err)
	}
	tick <- struct{}{}
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) && sup.Lanes()[0].LastBeat.IsZero() {
		time.Sleep(5 * time.Millisecond)
	}
	if err := sup.Stop(ctxBg); err != nil {
		t.Fatal(err)
	}
	if h := sup.Health(); len(h) != 1 || h[0] != "lane sheriff down (generation 1)" {
		t.Fatalf("health after stop: %v", h)
	}
}

// The sheriff's "last committed activity" includes stage records: an
// AGENDA attempt whose newest evidence is a StepDone is measured from it,
// and the stuck verdict's basis names it.
func TestSheriffBasisNamesTheNewestStageRecord(t *testing.T) {
	j, st := open(t)
	exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("r1")}, {Response: []byte("r2")}}}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{
		{Response: []byte(`{"clear": true, "interpretation": "do it", "question": ""}`)},
		{Response: []byte(`{"steps": ["one", "two"]}`)},
		{Response: []byte(`{"outcome": "done", "confidence": 0.9, "why": "ok"}`)},
	}}
	d := &run.Driver{J: j, Store: st, Backend: exec, Judge: judge, Lane: run.LaneAgenda, Origin: run.CLIOrigin{W: io.Discard}}
	d.CrashAt = "after_step" // step 1 done; nothing after
	if _, err := d.Run(ctxBg, []byte("two steps"), run.DeliveryPolicy{Required: run.TransportAccepted}); !errors.Is(err, run.ErrCrashed) {
		t.Fatal(err)
	}
	clock := time.Now().UTC().Add(time.Hour)
	s := &Sheriff{J: j, Store: st, StallAfter: 10 * time.Minute, Clock: func() time.Time { return clock }}
	if _, err := s.Evaluate(ctxBg); err != nil {
		t.Fatal(err)
	}
	vs := stuckVerdicts(t, j)
	if len(vs) != 1 || vs[0].Basis[0].Kind != run.KindStepDone {
		t.Fatalf("basis: %+v", vs[0].Basis)
	}
}
