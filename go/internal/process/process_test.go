package process

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var ctxBg = context.Background()

func root(t *testing.T) *workspace.Announced {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	return a
}

// gated is an executor whose calls block until released, so a test can
// act between steps.
type gated struct {
	inner   *invoke.Scripted
	release chan struct{}
	mu      sync.Mutex
	calls   int
}

func (g *gated) Capabilities() invoke.Capabilities { return g.inner.Capabilities() }
func (g *gated) Complete(ctx context.Context, req invoke.Request, sink invoke.Sink) (*invoke.Result, error) {
	g.mu.Lock()
	g.calls++
	g.mu.Unlock()
	select {
	case <-g.release:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	return g.inner.Complete(ctx, req, sink)
}

func serve(t *testing.T, a *workspace.Announced, exec, judge invoke.Backend) *Server {
	t.Helper()
	s, err := Serve(ctxBg, Options{Root: a, Backend: exec, Judge: judge, Timeout: time.Minute, Poll: 50 * time.Millisecond, StallAfter: time.Hour})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(ctxBg, 10*time.Second)
		defer cancel()
		s.Stop(ctx)
	})
	return s
}

func events(t *testing.T, s *Server, req Request) []Event {
	t.Helper()
	cl, err := Dial(s.Socket())
	if err != nil {
		t.Fatal(err)
	}
	defer cl.Close()
	var out []Event
	ctx, cancel := context.WithTimeout(ctxBg, 20*time.Second)
	defer cancel()
	if err := cl.Submit(ctx, req, func(e Event) { out = append(out, e) }); err != nil {
		t.Fatalf("submit: %v (events %+v)", err, out)
	}
	return out
}

// A goal submitted over the socket is taken in by the process, driven by
// its executor, and presented back on the submitting connection; the run
// is a socket-origin run; a second submission while the first runs is
// queued (one heavy job at a time); status shows the lanes and runs; an
// ack over the socket acknowledges.
func TestSubmitDeliversOverTheSocket(t *testing.T) {
	a := root(t)
	exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "m"}, Calls: []invoke.ScriptedCall{{Response: []byte("Paris.")}, {Response: []byte("Rome.")}}}
	s := serve(t, a, exec, nil)
	evs := events(t, s, Request{Text: "Capital of France?", Ack: true})
	if len(evs) != 3 || evs[0].Type != "accepted" || evs[1].Type != "presentation" || evs[1].Payload != "Paris." || evs[1].Token == "" || evs[2].Type != "done" || evs[2].Mission != string(run.MissionUnacknowledged) {
		t.Fatalf("events: %+v", evs)
	}
	cl, _ := Dial(s.Socket())
	if ev, err := cl.One(Request{Op: "ack", Delivery: evs[1].Delivery, Token: "0000"}); err == nil {
		t.Fatalf("bad token acknowledged: %+v", ev)
	}
	cl.Close()
	cl, _ = Dial(s.Socket())
	ev, err := cl.One(Request{Op: "ack", Delivery: evs[1].Delivery, Token: evs[1].Token})
	cl.Close()
	if err != nil || ev.Type != "acked" || ev.Replayed {
		t.Fatalf("ack: %v %+v", err, ev)
	}
	evs2 := events(t, s, Request{Text: "Capital of Italy?", Lane: "now"})
	if evs2[1].Payload != "Rome." || evs2[2].Mission != string(run.MissionDelivered) {
		t.Fatalf("second: %+v", evs2)
	}
	cl, _ = Dial(s.Socket())
	st, err := cl.One(Request{Op: "status"})
	cl.Close()
	if err != nil || st.Type != "status" || len(st.Lanes) != 4 || len(st.Runs) != 2 || len(st.Health) != 0 {
		t.Fatalf("status: %v %+v", err, st)
	}
	for _, m := range st.Runs {
		if m.Outcome != run.MissionDelivered {
			t.Fatalf("run not delivered: %+v", m)
		}
	}
	// the runs are socket-origin in the journal; every lane quiesced (a
	// quiesce is "stopped", never "failed"); the socket file is gone
	if err := s.Stop(ctxBg); err != nil {
		t.Fatal(err)
	}
	if _, err := Dial(s.Socket()); err == nil {
		t.Fatal("socket still accepting after Stop")
	}
	l, _ := workspace.Acquire(a)
	j, _ := journal.Open(l)
	st2, _ := thought.Open(a)
	led, err := run.Fold(j.Production(), st2)
	if err != nil {
		t.Fatal(err)
	}
	for _, rs := range led.Runs {
		if rs.Goal.Origin != run.OriginSocket || rs.Latest().Delivery.Prepared.Origin != run.OriginSocket {
			t.Fatalf("origin: %+v", rs.Goal)
		}
	}
	laneEvents := map[string][]string{}
	j.Control().Scan(0, func(r record.Record) error {
		if e, ok := r.(*supervise.LaneEvent); ok {
			laneEvents[e.Lane] = append(laneEvents[e.Lane], string(e.Event))
		}
		return nil
	})
	j.Close()
	l.Release()
	for _, lane := range []string{"intake", "sheriff", "executor", "publisher"} {
		if got := strings.Join(laneEvents[lane], " "); got != "started stopped" {
			t.Fatalf("lane %s events: %s", lane, got)
		}
	}
}

// An interrupt is consumed at the next stage boundary: an AGENDA run gated
// between its steps stops before step 2 with the interrupt acknowledged,
// delivers the done step honestly, and an interrupt for a terminal run is
// acknowledged as expired.
func TestInterruptStopsAtTheNextBoundary(t *testing.T) {
	a := root(t)
	release := make(chan struct{})
	exec := &gated{inner: &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("r1")}, {Response: []byte("r2")}}}, release: release}
	judge := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-judge", Model: "judge"}, Calls: []invoke.ScriptedCall{
		{Response: []byte(`{"clear": true, "interpretation": "two steps", "question": ""}`)},
		{Response: []byte(`{"steps": ["one", "two"]}`)},
		{Response: []byte(`{"outcome": "done", "confidence": 0.9, "why": "ok"}`)},
		{Response: []byte(`{"outcome": "done", "confidence": 0.9, "why": "ok"}`)},
		{Response: []byte(`{"outcome": "achieved", "confidence": 0.9, "why": "ok", "falsifiers": []}`)},
	}}
	s := serve(t, a, exec, judge)
	var evs []Event
	done := make(chan error, 1)
	go func() {
		cl, err := Dial(s.Socket())
		if err != nil {
			done <- err
			return
		}
		defer cl.Close()
		ctx, cancel := context.WithTimeout(ctxBg, 20*time.Second)
		defer cancel()
		done <- cl.Submit(ctx, Request{Text: "two steps", Lane: "agenda"}, func(e Event) { evs = append(evs, e) })
	}()
	// wait for the executor to be inside step 1 (the gate holds it)
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		exec.mu.Lock()
		c := exec.calls
		exec.mu.Unlock()
		if c == 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	// find the handle and interrupt, then let step 1 finish
	cl, _ := Dial(s.Socket())
	st, err := cl.One(Request{Op: "status"})
	cl.Close()
	if err != nil || len(st.Runs) != 1 {
		t.Fatalf("status: %v %+v", err, st)
	}
	cl, _ = Dial(s.Socket())
	iv, err := cl.One(Request{Op: "interrupt", Handle: st.Runs[0].Handle, Why: "operator changed their mind"})
	cl.Close()
	if err != nil || iv.Result != "pending" {
		t.Fatalf("interrupt: %v %+v", err, iv)
	}
	release <- struct{}{} // step 1 completes; step 2 must not run
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	last := evs[len(evs)-1]
	if last.Type != "done" || last.Mission != string(run.MissionFailedExec) || !strings.Contains(evs[1].Payload, "## Step 1: one\nr1") || !strings.Contains(evs[1].Payload, "(not executed)") || !strings.Contains(evs[1].Payload, "interrupted at before_step_2: operator changed their mind") {
		t.Fatalf("events: %+v", evs)
	}
	exec.mu.Lock()
	calls := exec.calls
	exec.mu.Unlock()
	if calls != 1 {
		t.Fatalf("step 2 ran after the interrupt: %d executor calls", calls)
	}
	// an interrupt for the now-terminal run expires
	cl, _ = Dial(s.Socket())
	iv, err = cl.One(Request{Op: "interrupt", Handle: st.Runs[0].Handle, Why: "again"})
	cl.Close()
	if err != nil || iv.Result != "expired" {
		t.Fatalf("expired: %v %+v", err, iv)
	}
	if err := s.Stop(ctxBg); err != nil {
		t.Fatal(err)
	}
	l, _ := workspace.Acquire(a)
	j, _ := journal.Open(l)
	st2, _ := thought.Open(a)
	led, err := run.Fold(j.Production(), st2)
	j.Close()
	l.Release()
	if err != nil {
		t.Fatal(err)
	}
	for r, its := range led.Interrupts {
		if len(its) != 2 || led.Acks[its[0].ID] == nil || led.Acks[its[0].ID].Result != "consumed" || led.Acks[its[0].ID].Boundary != "before_step_2" || led.Acks[its[1].ID].Result != "expired" {
			t.Fatalf("interrupts for %s: %+v", r, its)
		}
	}
}

// Stopping the process mid-run and serving again continues the run from
// its committed stages; the client that submitted is gone, so the
// presentation fails within its bound and the run ends delivery_failed
// with the payload kept; a fresh process serves new goals.
func TestRestartContinuesAndAnOrphanedClientIsHonest(t *testing.T) {
	a := root(t)
	release := make(chan struct{}, 8)
	exec := &gated{inner: &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted", Model: "m"}, Calls: []invoke.ScriptedCall{{Response: []byte("x")}, {Response: []byte("y")}}}, release: release}
	s := serve(t, a, exec, nil)
	go func() {
		cl, err := Dial(s.Socket())
		if err != nil {
			return
		}
		defer cl.Close()
		ctx, cancel := context.WithTimeout(ctxBg, 5*time.Second)
		defer cancel()
		cl.Submit(ctx, Request{Text: "q?"}, func(Event) {})
	}()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		exec.mu.Lock()
		c := exec.calls
		exec.mu.Unlock()
		if c == 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	ctx, cancel := context.WithTimeout(ctxBg, 5*time.Second)
	defer cancel()
	if err := s.Stop(ctx); err != nil { // the executor's invocation is cancelled by the quiesce
		t.Fatal(err)
	}
	release <- struct{}{}
	release <- struct{}{}
	s2 := serve(t, a, exec, nil)
	// the orphaned run is resumed and, with no client, ends delivery_failed
	deadline = time.Now().Add(10 * time.Second)
	var st Event
	for time.Now().Before(deadline) {
		cl, _ := Dial(s2.Socket())
		st, _ = cl.One(Request{Op: "status"})
		cl.Close()
		if len(st.Runs) == 1 && st.Runs[0].Outcome == run.MissionFailedDelivery {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	if len(st.Runs) != 1 || st.Runs[0].Outcome != run.MissionFailedDelivery || !strings.Contains(st.Runs[0].Reason, "no client") {
		t.Fatalf("orphaned run: %+v", st.Runs)
	}
	evs := events(t, s2, Request{Text: "again?"})
	if evs[len(evs)-1].Mission != string(run.MissionDelivered) {
		t.Fatalf("new goal on the new process: %+v", evs)
	}
	_ = errors.New
	_ = record.NewID
}
