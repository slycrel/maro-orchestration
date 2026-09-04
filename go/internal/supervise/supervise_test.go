package supervise

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func openJ(t *testing.T) *journal.Journal {
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
	return j
}

// fake is a scriptable lane.
type fake struct {
	name   string
	stage  int
	expect time.Duration
	runs   int32
	body   func(ctx context.Context, hb *Heartbeat, gen int) error
}

func (f *fake) Name() string          { return f.name }
func (f *fake) Stage() int            { return f.stage }
func (f *fake) Expect() time.Duration { return f.expect }
func (f *fake) Run(ctx context.Context, hb *Heartbeat) error {
	gen := int(atomic.AddInt32(&f.runs, 1))
	return f.body(ctx, hb, gen)
}

func events(t *testing.T, j *journal.Journal, lane string) []string {
	t.Helper()
	var out []string
	j.Control().Scan(0, func(r record.Record) error {
		if e, ok := r.(*LaneEvent); ok && e.Lane == lane {
			out = append(out, string(e.Event))
		}
		return nil
	})
	return out
}

func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for %s", what)
}

// A panicking lane is restarted with its generation counted, up to the
// bound; past it the lane stays down with a reason, the health line says
// so, and every event is a CONTROL record (production readers never see
// them).
func TestPanicIsContainedAndRestartIsBounded(t *testing.T) {
	j := openJ(t)
	s := New(j)
	s.MaxRestarts, s.Watch = 2, 10*time.Millisecond
	boom := &fake{name: "boom", stage: 2, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		panic("record 7 is cursed")
	}}
	steady := &fake{name: "steady", stage: 3, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		<-ctx.Done()
		return nil
	}}
	if err := s.Register(boom); err != nil {
		t.Fatal(err)
	}
	if err := s.Register(steady); err != nil {
		t.Fatal(err)
	}
	if err := s.Register(&fake{name: "boom", stage: 1, expect: time.Second}); err == nil {
		t.Fatal("duplicate lane registered")
	}
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "give-up", func() bool {
		for _, st := range s.Lanes() {
			if st.Lane == "boom" && st.GaveUp {
				return true
			}
		}
		return false
	})
	got := events(t, j, "boom")
	want := "started panicked restarted panicked restarted panicked gave_up"
	if strings.Join(got, " ") != want {
		t.Fatalf("events: %v", got)
	}
	if atomic.LoadInt32(&boom.runs) != 3 {
		t.Fatalf("runs: %d", boom.runs)
	}
	h := s.Health()
	if len(h) != 1 || !strings.Contains(h[0], "lane boom DOWN") || !strings.Contains(h[0], "restart bound 2 reached") || !strings.Contains(h[0], "cursed") {
		t.Fatalf("health: %v", h)
	}
	n := 0
	j.Production().Scan(0, func(r record.Record) error { n++; return nil })
	if n != 0 {
		t.Fatalf("%d lane records leaked into production", n)
	}
	if err := s.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := events(t, j, "steady"); strings.Join(got, " ") != "started stopped" {
		t.Fatalf("steady: %v", got)
	}
	if h := s.Health(); len(h) != 2 {
		t.Fatalf("health after stop: %v", h)
	}
}

// A lane silent past its declared silence is reported stalled (never
// killed); heartbeats clear it; heartbeat records are written once per
// watermark move and carry the watermark.
func TestStallIsReportedNotEnforced(t *testing.T) {
	j := openJ(t)
	s := New(j)
	s.Watch, s.MinBeat = 10*time.Millisecond, 0
	var beat atomic.Bool
	var mark atomic.Uint64
	lane := &fake{name: "slow", stage: 2, expect: 50 * time.Millisecond, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		for {
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(5 * time.Millisecond):
				if beat.Load() {
					hb.Progress(ctx, mark.Load())
				}
			}
		}
	}}
	s.Register(lane)
	s.Start(context.Background())
	waitFor(t, "stall", func() bool { return len(s.Health()) == 1 && strings.Contains(s.Health()[0], "stalled") })
	if got := events(t, j, "slow"); strings.Join(got, " ") != "started stalled" {
		t.Fatalf("events: %v", got)
	}
	if st := s.Lanes()[0]; !st.Up || st.GaveUp {
		t.Fatalf("a stalled lane was killed: %+v", st)
	}
	mark.Store(42)
	beat.Store(true)
	waitFor(t, "recovery", func() bool { return len(s.Health()) == 0 })
	mark.Store(43)
	waitFor(t, "watermark", func() bool { return s.Lanes()[0].Watermark == 43 })
	// the in-memory watermark moves before its record is committed: wait
	// on the journal, not the gauge (a load flake, 2026-09-05)
	beats := func() (n int) {
		j.Control().Scan(0, func(r record.Record) error {
			if h, ok := r.(*LaneHeartbeat); ok && h.Lane == "slow" {
				n++
				if h.Watermark != 42 && h.Watermark != 43 {
					t.Fatalf("heartbeat watermark %d", h.Watermark)
				}
			}
			return nil
		})
		return n
	}
	waitFor(t, "heartbeat records", func() bool { return beats() == 2 })
	if hbs := beats(); hbs != 2 {
		t.Fatalf("heartbeat records: %d (one per watermark move)", hbs)
	}
	s.Stop(context.Background())
}

// Shutdown is a quiesce DAG: lower stages are cancelled and awaited before
// higher ones; a lane that returns an error while the process is up is
// restarted, one that returns nil is simply stopped; a lane that does not
// quiesce in time is named.
func TestStopQuiescesInStageOrder(t *testing.T) {
	j := openJ(t)
	s := New(j)
	var mu sync.Mutex
	var order []string
	mk := func(name string, stage int) *fake {
		return &fake{name: name, stage: stage, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
			<-ctx.Done()
			time.Sleep(20 * time.Millisecond)
			mu.Lock()
			order = append(order, name)
			mu.Unlock()
			return nil
		}}
	}
	for _, l := range []*fake{mk("delivery", 4), mk("intake", 1), mk("executor", 3), mk("sheriff", 2)} {
		if err := s.Register(l); err != nil {
			t.Fatal(err)
		}
	}
	var flakyRuns int32
	failing := &fake{name: "flaky", stage: 2, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		if atomic.AddInt32(&flakyRuns, 1) == 1 {
			return errors.New("transient")
		}
		<-ctx.Done()
		return nil
	}}
	s.Register(failing)
	s.Start(context.Background())
	waitFor(t, "flaky restart", func() bool { return atomic.LoadInt32(&flakyRuns) == 2 })
	if err := s.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	mu.Lock()
	got := strings.Join(order, " ")
	mu.Unlock()
	if got != "intake sheriff executor delivery" {
		t.Fatalf("quiesce order: %v", got)
	}
	if got := events(t, j, "flaky"); strings.Join(got, " ") != "started failed restarted stopped" {
		t.Fatalf("flaky: %v", got)
	}
	s2 := New(openJ(t))
	stuck := &fake{name: "stuck", stage: 1, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		<-ctx.Done()
		time.Sleep(time.Second)
		return nil
	}}
	s2.Register(stuck)
	s2.Start(context.Background())
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
	defer cancel()
	if err := s2.Stop(ctx); err == nil || !strings.Contains(err.Error(), "did not quiesce") {
		t.Fatalf("stop: %v", err)
	}
}

// A lane that returns nil without being cancelled has failed (an always-on
// lane does not finish): it is restarted, bounded. Configuration is
// validated at Start; Stop while a lane is mid-restart still quiesces it;
// a refused control record shows in Health.
func TestUnexpectedReturnRestartsAndConfigIsValidated(t *testing.T) {
	j := openJ(t)
	s := New(j)
	s.MaxRestarts, s.Watch = 1, 10*time.Millisecond
	quitter := &fake{name: "quitter", stage: 2, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		if gen == 1 {
			return nil // fell out of its loop
		}
		<-ctx.Done()
		return nil
	}}
	s.Register(quitter)
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	waitFor(t, "restart", func() bool { return atomic.LoadInt32(&quitter.runs) == 2 })
	if got := events(t, j, "quitter"); strings.Join(got, " ") != "started failed restarted" {
		t.Fatalf("events: %v", got)
	}
	if err := s.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	if got := events(t, j, "quitter"); strings.Join(got, " ") != "started failed restarted stopped" {
		t.Fatalf("events after stop: %v", got)
	}
	// validation
	bad := New(openJ(t))
	bad.MaxRestarts = -1
	if err := bad.Start(context.Background()); err == nil {
		t.Fatal("negative bound accepted")
	}
	if err := (&Supervisor{J: j}).Start(context.Background()); err == nil {
		t.Fatal("a supervisor not made by New started")
	}
	// Stop racing a restart: a lane that fails repeatedly while Stop runs
	// is not relaunched once stopping began, and Stop awaits the live one
	j3 := openJ(t)
	s3 := New(j3)
	s3.MaxRestarts, s3.Watch = 100, 10*time.Millisecond
	release := make(chan struct{})
	flapper := &fake{name: "flapper", stage: 1, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		select {
		case <-ctx.Done():
			return nil
		case <-release:
			return errors.New("flap")
		case <-time.After(5 * time.Millisecond):
			return errors.New("flap")
		}
	}}
	s3.Register(flapper)
	s3.Start(context.Background())
	waitFor(t, "a few flaps", func() bool { return atomic.LoadInt32(&flapper.runs) >= 3 })
	if err := s3.Stop(context.Background()); err != nil {
		t.Fatal(err)
	}
	runsAtStop := atomic.LoadInt32(&flapper.runs)
	time.Sleep(50 * time.Millisecond)
	if atomic.LoadInt32(&flapper.runs) != runsAtStop {
		t.Fatal("a lane was relaunched after Stop")
	}
	if st := s3.Lanes()[0]; st.Up {
		t.Fatalf("lane still up after Stop: %+v", st)
	}
	// a refused control record is in Health: close the journal under a live lane
	j4 := openJ(t)
	s4 := New(j4)
	s4.MinBeat, s4.Watch = 0, 10*time.Millisecond
	var mark atomic.Uint64
	beater := &fake{name: "beater", stage: 2, expect: time.Minute, body: func(ctx context.Context, hb *Heartbeat, gen int) error {
		for {
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(5 * time.Millisecond):
				hb.Progress(context.Background(), mark.Add(1))
			}
		}
	}}
	s4.Register(beater)
	s4.Start(context.Background())
	waitFor(t, "beats", func() bool { return mark.Load() > 3 })
	j4.Close()
	waitFor(t, "journal refusal in health", func() bool {
		for _, h := range s4.Health() {
			if strings.Contains(h, "control journal refused") {
				return true
			}
		}
		return false
	})
	s4.stop()
}
