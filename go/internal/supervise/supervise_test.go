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
	hbs := 0
	j.Control().Scan(0, func(r record.Record) error {
		if h, ok := r.(*LaneHeartbeat); ok && h.Lane == "slow" {
			hbs++
			if h.Watermark != 42 && h.Watermark != 43 {
				t.Fatalf("heartbeat watermark %d", h.Watermark)
			}
		}
		return nil
	})
	if hbs != 2 {
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
