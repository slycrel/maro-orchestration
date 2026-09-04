package supervise

import (
	"context"
	"errors"
	"fmt"
	"runtime/debug"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Lane is a supervised goroutine. Run returns when its context is
// cancelled (nil) or when it cannot continue (error); it reports progress
// through the Heartbeat. Stage is the quiesce order at shutdown (§2):
// lower stages stop first — intake/timers (1), derived-work producers (2),
// executor (3), delivery (4); the sequencer and projector are the journal's
// own close. Expect is the longest silence between heartbeats the lane
// declares normal; longer is a stall (reported, never enforced).
type Lane interface {
	Name() string
	Stage() int
	Expect() time.Duration
	Run(ctx context.Context, hb *Heartbeat) error
}

// Heartbeat is a lane generation's progress reporter.
type Heartbeat struct {
	s    *Supervisor
	lane string
	gen  int
	mu   sync.Mutex
	last time.Time
	mark uint64
}

// Progress records that the lane has processed through the watermark (or
// simply that it is alive, when the watermark is unchanged). A heartbeat
// record is written when the watermark moves and at most once per
// MinBeat; liveness itself is in memory.
func (h *Heartbeat) Progress(ctx context.Context, watermark uint64) {
	h.mu.Lock()
	moved := watermark > h.mark
	if moved {
		h.mark = watermark
	}
	h.last = now()
	h.mu.Unlock()
	if !moved || ctx.Err() != nil {
		return // a cancelled lane's progress is liveness only; the journal may be closing
	}
	h.s.mu.Lock()
	write := h.last.Sub(h.s.lastBeat[h.lane]) >= h.s.MinBeat
	if write {
		h.s.lastBeat[h.lane] = h.last
	}
	h.s.mu.Unlock()
	if write {
		h.s.commit(ctx, fmt.Sprintf("heartbeat/%s/%d/%d", h.lane, h.gen, watermark), &LaneHeartbeat{Header: h.s.header(h.lane), Lane: h.lane, Generation: h.gen, Watermark: watermark})
	}
}

func (h *Heartbeat) seen() (time.Time, uint64) {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.last, h.mark
}

// Status is one lane's current state for the health line.
type Status struct {
	Lane       string
	Generation int
	Up         bool
	Stalled    bool
	GaveUp     bool
	Reason     string
	Watermark  uint64
	LastBeat   time.Time
}

type laneState struct {
	lane    Lane
	gen     int
	hb      *Heartbeat
	up      bool
	gaveUp  bool
	stalled bool
	reason  string
	cancel  context.CancelFunc
	done    chan struct{}
}

// Supervisor owns the lanes of one process under one root context.
type Supervisor struct {
	J *journal.Journal
	// MaxRestarts bounds restarts per lane. Why 3: a lane that panics on
	// the same record restarts into the same panic; the bound turns a loop
	// into a down lane with a reason in every delivery, which is the state
	// an operator can act on.
	MaxRestarts int
	// MinBeat rate-limits heartbeat records. Why 5s: a heartbeat is
	// evidence of progress, not a clock; a lane advancing its watermark
	// thousands of times a second would otherwise write more heartbeats
	// than work.
	MinBeat time.Duration
	// Watch is how often stalls are checked. Why 1s: stall thresholds are
	// declared in seconds or minutes; checking faster buys nothing.
	Watch time.Duration
	// Events, when set, receives every lane event as it is committed.
	Events func(LaneEvent)

	mu       sync.Mutex
	lanes    map[string]*laneState
	order    []string
	lastBeat map[string]time.Time
	root     context.Context
	stop     context.CancelFunc
	watching chan struct{}
	stopping bool
	// journalErr is the last refusal of a control record; surfaced in
	// Health because a heartbeat that did not commit is not evidence.
	journalErr string
}

// New prepares a supervisor over the journal; nothing runs until Start.
func New(j *journal.Journal) *Supervisor {
	return &Supervisor{J: j, MaxRestarts: 3, MinBeat: 5 * time.Second, Watch: time.Second, lanes: map[string]*laneState{}, lastBeat: map[string]time.Time{}}
}

// Register adds a lane. Names are unique; registration after Start is
// refused (the lane set is the process's declaration, not a runtime pool).
func (s *Supervisor) Register(l Lane) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.root != nil {
		return errors.New("supervise: register before start")
	}
	if l.Name() == "" || strings.ContainsAny(l.Name(), "/\\ ") {
		return fmt.Errorf("supervise: bad lane name %q", l.Name())
	}
	if _, dup := s.lanes[l.Name()]; dup {
		return fmt.Errorf("supervise: lane %q registered twice", l.Name())
	}
	if l.Stage() < 1 || l.Expect() <= 0 {
		return fmt.Errorf("supervise: lane %q needs a stage ≥ 1 and a positive expected silence", l.Name())
	}
	s.lanes[l.Name()] = &laneState{lane: l}
	s.order = append(s.order, l.Name())
	return nil
}

// Start validates the configuration, launches every lane under ctx, and
// begins the stall watch. Zero values mean the defaults New sets; negative
// bounds are refused; a supervisor not made by New is refused.
func (s *Supervisor) Start(ctx context.Context) error {
	s.mu.Lock()
	if s.root != nil {
		s.mu.Unlock()
		return errors.New("supervise: already started")
	}
	if s.lanes == nil || s.J == nil {
		s.mu.Unlock()
		return errors.New("supervise: use New")
	}
	if s.MaxRestarts < 0 || s.MinBeat < 0 || s.Watch < 0 {
		s.mu.Unlock()
		return errors.New("supervise: bounds must be positive (0 = default)")
	}
	if s.MaxRestarts == 0 {
		s.MaxRestarts = 3
	}
	if s.Watch == 0 {
		s.Watch = time.Second
	}
	s.root, s.stop = context.WithCancel(ctx)
	names := append([]string{}, s.order...)
	s.watching = make(chan struct{})
	s.mu.Unlock()
	for _, n := range names {
		s.launch(n, 1, "")
	}
	go s.watch()
	return nil
}

func (s *Supervisor) launch(name string, gen int, why string) {
	s.mu.Lock()
	st := s.lanes[name]
	ctx, cancel := context.WithCancel(s.root)
	st.gen, st.up, st.stalled, st.reason, st.cancel, st.done = gen, true, false, "", cancel, make(chan struct{})
	st.hb = &Heartbeat{s: s, lane: name, gen: gen, last: now()}
	s.mu.Unlock()
	ev := LaneStarted
	if gen > 1 {
		ev = LaneRestarted
	}
	s.event(ctx, name, ev, gen, why)
	go s.supervise(ctx, st)
}

// supervise runs one generation and decides what its end means.
func (s *Supervisor) supervise(ctx context.Context, st *laneState) {
	s.mu.Lock()
	name, gen, done := st.lane.Name(), st.gen, st.done // this generation's own channel; launch replaces st.done
	s.mu.Unlock()
	var err error
	func() {
		defer func() {
			if r := recover(); r != nil {
				err = fmt.Errorf("panic: %v | %s", r, firstLines(string(debug.Stack()), 6))
				s.event(s.root, name, LanePanicked, gen, err.Error())
			}
		}()
		err = st.lane.Run(ctx, st.hb)
	}()
	s.mu.Lock()
	st.up = false
	quiesced := ctx.Err() != nil
	s.mu.Unlock()
	if err == nil && !quiesced {
		// an always-on lane that returns without being asked to has
		// failed, whatever it returned: restart it, bounded
		err = errors.New("returned without cancellation")
	}
	// the generation's last event lands BEFORE done is closed: whoever
	// waits on done (Stop) sees the record, not a promise of it
	restart := false
	switch {
	case quiesced:
		s.mu.Lock()
		st.reason = ""
		s.mu.Unlock()
		s.event(s.root, name, LaneStopped, gen, "")
	default:
		if !strings.HasPrefix(err.Error(), "panic:") {
			s.event(s.root, name, LaneFailed, gen, err.Error())
		}
		if gen > s.MaxRestarts {
			s.mu.Lock()
			st.gaveUp, st.reason = true, fmt.Sprintf("restart bound %d reached; last: %s", s.MaxRestarts, firstLines(err.Error(), 1))
			reason := st.reason
			s.mu.Unlock()
			s.event(s.root, name, LaneGaveUp, gen, reason)
		} else {
			s.mu.Lock()
			restart = s.root.Err() == nil && !s.stopping
			s.mu.Unlock()
		}
	}
	if restart {
		// the next generation is registered BEFORE this one's done closes,
		// so Stop, which re-reads the lane after every wait, sees it
		s.launch(name, gen+1, firstLines(err.Error(), 1))
	}
	close(done)
}

// watch marks lanes whose heartbeat is older than their declared silence.
func (s *Supervisor) watch() {
	defer close(s.watching)
	t := time.NewTicker(s.Watch)
	defer t.Stop()
	for {
		select {
		case <-s.root.Done():
			return
		case <-t.C:
			type stall struct {
				name, reason string
				gen          int
			}
			var stalls []stall
			s.mu.Lock()
			for _, name := range s.order {
				st := s.lanes[name]
				if !st.up || st.hb == nil {
					continue
				}
				last, _ := st.hb.seen()
				silent := now().Sub(last)
				if silent > st.lane.Expect() && !st.stalled {
					st.stalled, st.reason = true, fmt.Sprintf("no heartbeat for %s (declared %s)", silent.Round(time.Millisecond), st.lane.Expect())
					stalls = append(stalls, stall{name, st.reason, st.gen})
				} else if silent <= st.lane.Expect() && st.stalled {
					st.stalled, st.reason = false, ""
				}
			}
			s.mu.Unlock()
			for _, x := range stalls {
				s.event(s.root, x.name, LaneStalled, x.gen, x.reason)
			}
		}
	}
}

// Stop quiesces in stage order: every lane of the lowest stage is
// cancelled and awaited before the next stage; no restart is launched once
// stopping began, and a generation that replaced a finished one is awaited
// in its turn. Returns when all lanes have returned or ctx expires.
func (s *Supervisor) Stop(ctx context.Context) error {
	s.mu.Lock()
	if s.root == nil || s.stopping {
		s.mu.Unlock()
		return nil
	}
	s.stopping = true
	byStage := map[int][]*laneState{}
	var stages []int
	for _, name := range s.order {
		st := s.lanes[name]
		if _, ok := byStage[st.lane.Stage()]; !ok {
			stages = append(stages, st.lane.Stage())
		}
		byStage[st.lane.Stage()] = append(byStage[st.lane.Stage()], st)
	}
	sort.Ints(stages)
	s.mu.Unlock()
	for _, stage := range stages {
		for _, st := range byStage[stage] {
			for {
				s.mu.Lock()
				cancel, done, up := st.cancel, st.done, st.up
				s.mu.Unlock()
				if !up || done == nil {
					break
				}
				cancel()
				select {
				case <-done:
				case <-ctx.Done():
					return fmt.Errorf("supervise: lane %s did not quiesce: %w", st.lane.Name(), ctx.Err())
				}
			}
		}
	}
	s.stop()
	<-s.watching
	return nil
}

// Health lists every lane that is not healthy: down, stalled, or given
// up, with the reason. Empty = healthy. Every delivery carries this line
// while it is non-empty (§10).
func (s *Supervisor) Health() []string {
	var out []string
	s.mu.Lock()
	if s.journalErr != "" {
		out = append(out, "control journal refused a lane record: "+s.journalErr)
	}
	s.mu.Unlock()
	for _, st := range s.Lanes() {
		switch {
		case st.GaveUp:
			out = append(out, fmt.Sprintf("lane %s DOWN: %s", st.Lane, st.Reason))
		case !st.Up:
			out = append(out, fmt.Sprintf("lane %s down (generation %d)", st.Lane, st.Generation))
		case st.Stalled:
			out = append(out, fmt.Sprintf("lane %s stalled: %s", st.Lane, st.Reason))
		}
	}
	return out
}

// Lanes reports every lane's status in registration order.
func (s *Supervisor) Lanes() []Status {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Status
	for _, name := range s.order {
		st := s.lanes[name]
		x := Status{Lane: name, Generation: st.gen, Up: st.up, Stalled: st.stalled, GaveUp: st.gaveUp, Reason: st.reason}
		if st.hb != nil {
			x.LastBeat, x.Watermark = st.hb.seen()
		}
		out = append(out, x)
	}
	return out
}

func (s *Supervisor) header(lane string) record.Header {
	return record.Header{ID: record.NewID(), Subject: record.Ref{Kind: "lane", ID: lane}, At: now()}
}

func (s *Supervisor) event(ctx context.Context, lane string, ev LaneEventKind, gen int, reason string) {
	e := &LaneEvent{Header: s.header(lane), Lane: lane, Event: ev, Generation: gen, Reason: reason}
	s.commit(ctx, fmt.Sprintf("lane/%s/%d/%s/%s", lane, gen, ev, e.ID), e)
	if s.Events != nil {
		s.Events(*e)
	}
}

// commit writes a control record. A refusal is not an error the lane can
// act on, but it is not swallowed either: it is the health line's first
// item until a later record commits.
func (s *Supervisor) commit(ctx context.Context, key string, r record.Record) {
	spec, _ := record.Lookup(r.Kind())
	r.Head().Schema = record.SchemaVer(fmt.Sprintf("%s/%d", r.Kind(), spec.Version))
	if ctx == nil || ctx.Err() != nil {
		ctx = context.Background()
	}
	_, err := s.J.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: s.J.Epoch(), Records: []record.Record{r}})
	s.mu.Lock()
	if err != nil {
		s.journalErr = err.Error()
	} else {
		s.journalErr = ""
	}
	s.mu.Unlock()
}

func firstLines(s string, n int) string {
	lines := strings.Split(strings.TrimSpace(s), "\n")
	if len(lines) > n {
		lines = lines[:n]
	}
	return strings.Join(lines, " | ")
}
