// Package process is the always-on maro-go: one lease, one supervisor, and
// the lanes that make a goal submitted from outside into a delivered
// answer (design note §10, D12). The submission path is a Unix socket in
// the workspace: a client writes a Goal into the running process's
// journal through the intake lane, the executor lane drives it, and the
// presentation goes back over the client's own connection.
package process

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/experiment"
	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/projector"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/run"
	"github.com/slycrel/maro-orchestration/go/internal/sheriff"
	"github.com/slycrel/maro-orchestration/go/internal/supervise"
	"github.com/slycrel/maro-orchestration/go/internal/tail"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// SocketName is the socket's file name under the workspace root.
const SocketName = "maro.sock"

// Options configures a process.
type Options struct {
	Root    *workspace.Announced
	Backend invoke.Backend // the executor (tool-bearing)
	Judge   invoke.Backend // tool-less; nil ⇒ Backend
	Timeout time.Duration
	Log     io.Writer
	// Sheriff thresholds; zero = the sheriff's defaults.
	StallAfter time.Duration
	// Poll is the executor's and projector's idle interval. Why 2s: the
	// intake lane wakes the executor directly; the poll only catches a
	// restart's leftovers and the projector's publish cadence.
	Poll time.Duration
	// TailEvery is the tail lane's pass interval; zero = the tail's default.
	TailEvery time.Duration
}

// Server is a running process.
type Server struct {
	opts     Options
	lease    *workspace.Lease
	j        *journal.Journal
	store    *thought.Store
	sup      *supervise.Supervisor
	ln       net.Listener
	wake     chan struct{}
	conns    *conns
	stopOnce sync.Once
	stopped  chan struct{}
	stopErr  error
}

// Request is one client message.
type Request struct {
	Op     string `json:"op"` // submit | interrupt | status | ack
	Text   string `json:"text,omitempty"`
	Lane   string `json:"lane,omitempty"`
	Ack    bool   `json:"ack,omitempty"` // policy: user_acknowledged
	Handle string `json:"handle,omitempty"`
	Why    string `json:"why,omitempty"`
	// ack
	Delivery string `json:"delivery,omitempty"`
	Token    string `json:"token,omitempty"`
}

// Event is one server message: a stream of them answers a submit.
type Event struct {
	Type         string             `json:"type"` // accepted | presentation | done | error | status | acked | interrupt
	Run          string             `json:"run,omitempty"`
	Handle       string             `json:"handle,omitempty"`
	Goal         string             `json:"goal,omitempty"`
	Payload      string             `json:"payload,omitempty"`
	Token        string             `json:"token,omitempty"`
	Delivery     string             `json:"delivery,omitempty"`
	Closure      string             `json:"closure,omitempty"`
	Terminal     string             `json:"terminal,omitempty"`
	Mission      string             `json:"mission,omitempty"`
	Health       []string           `json:"health,omitempty"`
	MayDuplicate int                `json:"may_duplicate,omitempty"`
	Error        string             `json:"error,omitempty"`
	Lanes        []supervise.Status `json:"lanes,omitempty"`
	Runs         []run.Mission      `json:"runs,omitempty"`
	Replayed     bool               `json:"replayed,omitempty"`
	Result       string             `json:"result,omitempty"`
}

// Serve takes the lease, opens the journal, and starts the lanes. It
// returns once the socket is listening; Stop quiesces in stage order.
func Serve(ctx context.Context, opts Options) (*Server, error) {
	if opts.Root == nil || opts.Backend == nil {
		return nil, errors.New("process: root and backend are required")
	}
	if opts.Judge == nil {
		opts.Judge = opts.Backend
	}
	if opts.Log == nil {
		opts.Log = io.Discard
	}
	if opts.Poll < 0 || opts.Timeout < 0 || opts.StallAfter < 0 {
		return nil, errors.New("process: durations must be positive (0 = default)")
	}
	if opts.Poll == 0 {
		opts.Poll = 2 * time.Second
	}
	if err := opts.Root.Ensure(); err != nil {
		return nil, err
	}
	lease, err := workspace.Acquire(opts.Root)
	if err != nil {
		return nil, err
	}
	j, err := journal.Open(lease)
	if err != nil {
		lease.Release()
		return nil, err
	}
	store, err := thought.Open(opts.Root)
	if err != nil {
		j.Close()
		lease.Release()
		return nil, err
	}
	sockPath := opts.Root.Path(SocketName)
	_ = os.Remove(sockPath) // a stale socket from a dead process; the lease says we own the root
	ln, err := net.Listen("unix", sockPath)
	if err != nil {
		j.Close()
		lease.Release()
		return nil, err
	}
	s := &Server{opts: opts, lease: lease, j: j, store: store, ln: ln, wake: make(chan struct{}, 1), conns: newConns(), stopped: make(chan struct{})}
	s.sup = supervise.New(j)
	s.sup.Events = func(e supervise.LaneEvent) {
		fmt.Fprintf(opts.Log, "lane %s %s gen=%d %s\n", e.Lane, e.Event, e.Generation, e.Reason)
	}
	judge := opts.Judge // nil ⇒ Backend, as Options says: the evaluator is held out in inputs, not in weights
	if judge == nil {
		judge = opts.Backend
	}
	lanes := []supervise.Lane{
		&intake{s: s},
		&tail.Timers{J: j, Events: func(l string) { fmt.Fprintln(opts.Log, l) }},
		&sheriff.Sheriff{J: j, Store: store, StallAfter: opts.StallAfter},
		&tail.Tail{J: j, Store: store, Lens: opts.Judge, Timeout: opts.Timeout, Every: opts.TailEvery, Events: func(l string) { fmt.Fprintln(opts.Log, l) }},
		&experiment.Lane{J: j, Store: store, Judge: judge, Timeout: opts.Timeout, Every: opts.TailEvery, Events: func(l string) { fmt.Fprintln(opts.Log, l) }},
		&executor{s: s},
		&publisher{s: s},
	}
	for _, l := range lanes {
		if err := s.sup.Register(l); err != nil {
			s.teardown()
			return nil, err
		}
	}
	if err := s.sup.Start(ctx); err != nil {
		s.teardown()
		return nil, err
	}
	fmt.Fprintf(opts.Log, "maro-go serving %s on %s (pid %d epoch %d)\n", opts.Root, sockPath, lease.PID, lease.Epoch)
	return s, nil
}

// Socket is the path clients dial.
func (s *Server) Socket() string { return s.ln.Addr().String() }

// Health is the supervisor's degraded line.
func (s *Server) Health() []string { return s.sup.Health() }

// Stop is the quiesce DAG (§2): lanes by stage (intake → sheriff →
// executor → publisher), then a final publish through the frozen head,
// then the journal closes and the lease is released.
func (s *Server) Stop(ctx context.Context) error {
	s.stopOnce.Do(func() {
		// the listener closes when the intake lane is cancelled (stage 1),
		// so the lane's return is a quiesce, not a failure
		s.stopErr = s.sup.Stop(ctx)
		if perr := s.publish(); perr != nil && s.stopErr == nil {
			s.stopErr = perr
		}
		s.teardown()
		close(s.stopped)
	})
	return s.stopErr // the same answer on every call: a failed quiesce stays failed
}

func (s *Server) teardown() {
	s.ln.Close()
	s.conns.closeAll()
	s.j.Close()
	s.lease.Release()
	_ = os.Remove(s.opts.Root.Path(SocketName))
}

// publish runs the projector once (every view), through the current head.
func (s *Server) publish() error {
	p, err := projector.New(s.j, projector.ThoughtsView{}, run.OutcomesView{Store: s.store}, learn.LessonsView{Store: s.store})
	if err != nil {
		return err
	}
	_, err = p.Publish()
	return err
}

// ---- lanes ----

// intake accepts client connections and serves each in its own goroutine
// under the lane's context. Stage 1: it stops accepting first.
type intake struct{ s *Server }

func (l *intake) Name() string          { return "intake" }
func (l *intake) Stage() int            { return 1 }
func (l *intake) Expect() time.Duration { return time.Hour } // idle is normal; the accept loop beats on every connection
func (l *intake) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	var wg sync.WaitGroup
	var mu sync.Mutex
	open := map[net.Conn]bool{}
	defer wg.Wait()
	go func() {
		<-ctx.Done()
		l.s.ln.Close() // cancellation closes the listener; Accept returns
		mu.Lock()
		for c := range open {
			c.Close() // and every accepted connection: an idle client cannot hold the quiesce
		}
		mu.Unlock()
	}()
	for {
		c, err := l.s.ln.Accept()
		if err != nil {
			if ctx.Err() != nil {
				return nil
			}
			return err
		}
		hb.Progress(ctx, l.s.j.Head())
		mu.Lock()
		open[c] = true
		mu.Unlock()
		wg.Add(1)
		go func() {
			defer wg.Done()
			defer func() {
				mu.Lock()
				delete(open, c)
				mu.Unlock()
			}()
			l.s.serveConn(ctx, c)
		}()
	}
}

// executor drives every unstarted goal and every non-terminal run, one at
// a time (D6: one heavy job at a time), waking on intake or on the poll.
type executor struct{ s *Server }

func (l *executor) Name() string          { return "executor" }
func (l *executor) Stage() int            { return 3 }
func (l *executor) Expect() time.Duration { return 4 * time.Hour } // a single subprocess turn can be long; the heartbeat moves per run
func (l *executor) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	t := time.NewTicker(l.s.opts.Poll)
	defer t.Stop()
	lastErr, repeats := "", 0
	for {
		d := &run.Driver{J: l.s.j, Store: l.s.store, Backend: l.s.opts.Backend, Judge: l.s.opts.Judge, Origin: l.s.conns, Timeout: l.s.opts.Timeout, Health: l.s.sup.Health,
			Events: func(e run.Event) {
				if e.Stage == "attempt" && e.Goal != "" {
					l.s.conns.bind(e.Goal, e.Run) // the run's presentation goes to the client that submitted its goal
				}
				fmt.Fprintf(l.s.opts.Log, "event %s attempt=%d %s %s\n", e.Handle, e.Attempt, e.Stage, e.Detail)
			}}
		reps, err := d.Resume(ctx)
		for _, r := range reps {
			l.s.conns.done(r)
		}
		if err != nil && ctx.Err() == nil {
			fmt.Fprintf(l.s.opts.Log, "executor: %v\n", err)
			if errors.Is(err, run.ErrIntegrity) {
				return err // journal evidence the driver cannot act on: a lane failure, restarted bounded, in the health line
			}
			// the same failure on consecutive passes is not transient: make
			// it the lane's failure so the bound and the health line apply
			// (why 3: one retry covers a backend blip, two an unlucky pair)
			if err.Error() == lastErr {
				repeats++
				if repeats >= 3 {
					return fmt.Errorf("repeated %d times: %w", repeats, err)
				}
			} else {
				lastErr, repeats = err.Error(), 1
			}
		} else {
			lastErr, repeats = "", 0
		}
		hb.Progress(ctx, l.s.j.Head())
		select {
		case <-ctx.Done():
			return nil
		case <-l.s.wake:
		case <-t.C:
		}
	}
}

// publisher publishes the views when the head moved. Stage 4.
type publisher struct{ s *Server }

func (l *publisher) Name() string          { return "publisher" }
func (l *publisher) Stage() int            { return 4 }
func (l *publisher) Expect() time.Duration { return time.Hour }
func (l *publisher) Run(ctx context.Context, hb *supervise.Heartbeat) error {
	t := time.NewTicker(l.s.opts.Poll)
	defer t.Stop()
	var last uint64
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
		if head := l.s.j.Head(); head != last {
			if err := l.s.publish(); err != nil {
				return err
			}
			last = head
		}
		hb.Progress(ctx, last)
	}
}

// ---- the socket protocol ----

func (s *Server) serveConn(ctx context.Context, c net.Conn) {
	defer c.Close()
	r := bufio.NewReader(c)
	enc := json.NewEncoder(c)
	// the request is one line; a client that sends nothing is dropped.
	// Why 30s: a human typing a goal into a client does not take longer
	// than that to send it; the process must not keep idle sockets.
	c.SetReadDeadline(time.Now().Add(30 * time.Second))
	line, err := r.ReadBytes('\n')
	if err != nil {
		return
	}
	c.SetReadDeadline(time.Time{})
	var req Request
	if err := json.Unmarshal(line, &req); err != nil {
		enc.Encode(Event{Type: "error", Error: "malformed request: " + err.Error()})
		return
	}
	switch req.Op {
	case "submit":
		s.submit(ctx, c, enc, req)
	case "interrupt":
		s.interrupt(ctx, enc, req)
	case "status":
		s.status(enc)
	case "ack":
		ack, replayed, err := run.Ack(ctx, s.j, s.store, record.RecordID(req.Delivery), req.Token)
		if err != nil {
			enc.Encode(Event{Type: "error", Error: err.Error()})
			return
		}
		enc.Encode(Event{Type: "acked", Delivery: string(ack.Delivery), Replayed: replayed})
	default:
		enc.Encode(Event{Type: "error", Error: "unknown op " + req.Op})
	}
}

// submit takes the goal in (the intake stage, on the client's connection),
// wakes the executor, and keeps the connection until the run is terminal
// so the presentation reaches the client that asked.
func (s *Server) submit(ctx context.Context, c net.Conn, enc *json.Encoder, req Request) {
	lane := run.Lane(req.Lane)
	if lane == "" {
		lane = run.LaneNow
	}
	policy := run.DeliveryPolicy{Required: run.TransportAccepted}
	if req.Ack {
		policy.Required = run.UserAcknowledged
	}
	text := []byte(req.Text)
	// everything cheap is validated BEFORE anything durable is written
	if err := run.ValidateIntake(text, lane, policy); err != nil {
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	ref, err := s.store.Put(thought.Goal, text)
	if err != nil {
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	goal, fam := run.Intake(text, ref, run.OriginSocket, lane, policy)
	// the client is registered BEFORE the goal is visible in the journal:
	// the executor may pick it up on its own poll the instant it commits
	waiter := s.conns.register(goal.ID, enc)
	// one command: the goal, its assessment, and — if an open live
	// experiment of its family claims it — its assignment (§5)
	if err := run.IntakeCommand(ctx, s.j, experiment.Admit(s.j, s.store), goal, fam); err != nil {
		s.conns.unregister(goal.ID)
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	enc.Encode(Event{Type: "accepted", Goal: string(goal.ID)})
	select {
	case s.wake <- struct{}{}:
	default:
	}
	select {
	case <-waiter:
	case <-ctx.Done():
	}
}

func (s *Server) interrupt(ctx context.Context, enc *json.Encoder, req Request) {
	led, err := run.Fold(s.j.Production(), s.store)
	if err != nil {
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	var target *run.RunState
	for _, rs := range led.Runs {
		if run.HandleOf(rs.Run) == req.Handle {
			target = rs
		}
	}
	if target == nil {
		enc.Encode(Event{Type: "error", Error: "no run with handle " + req.Handle})
		return
	}
	it := &run.Interrupt{Header: record.Header{ID: record.NewID(), Schema: "interrupt/1", Subject: record.Ref{Kind: "run", ID: string(target.Run)}, At: time.Now().UTC()}, Target: target.Run, Action: "cancel", Why: req.Why}
	recs := []record.Record{it}
	result := "pending"
	if target.Terminal() {
		// nothing to stop: acknowledged as expired, in the same command
		recs = append(recs, &run.InterruptAck{Header: record.Header{ID: record.NewID(), Schema: "interrupt_ack/1", Subject: record.Ref{Kind: "interrupt", ID: string(it.ID)}, At: time.Now().UTC()}, Interrupt: it.ID, Result: "expired"})
		result = "expired"
	}
	if _, err := s.j.Submit(ctx, journal.Command{IdempotencyKey: "interrupt/" + string(it.ID), Epoch: s.j.Epoch(), Records: recs}); err != nil {
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	enc.Encode(Event{Type: "interrupt", Handle: req.Handle, Run: string(target.Run), Result: result})
}

func (s *Server) status(enc *json.Encoder) {
	led, err := run.Fold(s.j.Production(), s.store)
	if err != nil {
		enc.Encode(Event{Type: "error", Error: err.Error()})
		return
	}
	ev := Event{Type: "status", Lanes: s.sup.Lanes(), Health: s.sup.Health()}
	for _, rs := range led.Runs {
		ev.Runs = append(ev.Runs, run.MissionOf(rs))
	}
	enc.Encode(ev)
}

// ---- the socket origin: one presentation per connection ----

// conns maps a goal (then its run) to the client connection that asked,
// and presents on it. A client that has gone is a failed presentation:
// the outbox retries within its bound and the run ends delivery_failed —
// the payload stays in the store for `runs`.
type conns struct {
	mu     sync.Mutex
	byGoal map[record.RecordID]*client
	byRun  map[record.RunID]*client
}

type client struct {
	enc  *json.Encoder
	done chan struct{}
	mu   sync.Mutex
}

func newConns() *conns {
	return &conns{byGoal: map[record.RecordID]*client{}, byRun: map[record.RunID]*client{}}
}

func (c *conns) register(goal record.RecordID, enc *json.Encoder) <-chan struct{} {
	c.mu.Lock()
	defer c.mu.Unlock()
	cl := &client{enc: enc, done: make(chan struct{})}
	c.byGoal[goal] = cl
	return cl.done
}

func (c *conns) unregister(goal record.RecordID) {
	c.mu.Lock()
	defer c.mu.Unlock()
	delete(c.byGoal, goal)
}

func (c *conns) Name() run.GoalOrigin { return run.OriginSocket }

// Present writes the presentation to the run's client. The run is bound
// to its goal's client on first presentation.
func (c *conns) Present(ctx context.Context, p run.Presentation) error {
	c.mu.Lock()
	cl := c.byRun[p.Run]
	c.mu.Unlock()
	if cl == nil {
		return errors.New("socket origin: no client for this run (submitted by a client that is gone, or before this process)")
	}
	cl.mu.Lock()
	defer cl.mu.Unlock()
	return cl.enc.Encode(Event{Type: "presentation", Run: string(p.Run), Handle: p.Handle, Payload: string(p.Payload), Token: p.Token, Delivery: string(p.Delivery), Closure: p.Closure, Terminal: p.Terminal, Health: p.Health, MayDuplicate: p.MayDuplicate})
}

// bind attaches a run to the client of its goal (called by the executor's
// event stream when an attempt starts).
func (c *conns) bind(goal record.RecordID, r record.RunID) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if cl := c.byGoal[goal]; cl != nil {
		c.byRun[r] = cl
	}
}

// done tells the client its run is terminal and releases the connection.
func (c *conns) done(r *run.Report) {
	c.mu.Lock()
	cl := c.byRun[r.Run]
	if cl == nil {
		cl = c.byGoal[r.Goal]
	}
	delete(c.byRun, r.Run)
	delete(c.byGoal, r.Goal)
	c.mu.Unlock()
	if cl == nil {
		return
	}
	cl.mu.Lock()
	cl.enc.Encode(Event{Type: "done", Run: string(r.Run), Handle: r.Handle, Mission: string(r.Mission.Outcome), Closure: r.Mission.Closure, Terminal: r.Mission.Terminal, Delivery: string(r.Delivery), Token: r.Token})
	cl.mu.Unlock()
	close(cl.done)
}

// closeAll tells every waiting client the process is stopping — their
// goal stays journaled and continues on the next serve — and releases them.
func (c *conns) closeAll() {
	c.mu.Lock()
	defer c.mu.Unlock()
	for g, cl := range c.byGoal {
		select {
		case <-cl.done:
		default:
			cl.mu.Lock()
			cl.enc.Encode(Event{Type: "stopping", Goal: string(g), Error: "the process is stopping; the goal stays journaled and continues on the next serve — find it with `maro-go runs`"})
			cl.mu.Unlock()
			close(cl.done)
		}
		delete(c.byGoal, g)
	}
	c.byRun = map[record.RunID]*client{}
}
