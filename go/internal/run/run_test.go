package run

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/projector"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var ctxBg = context.Background()

var toolless = invoke.Capabilities{Name: "scripted", Model: "m"}
var outward = invoke.Capabilities{Name: "scripted-agent", Model: "m", ActsOutward: true, OutwardReconcilable: true}

// harness holds one workspace across simulated restarts.
type harness struct {
	t      *testing.T
	a      *workspace.Announced
	l      *workspace.Lease
	j      *journal.Journal
	st     *thought.Store
	out    *bytes.Buffer
	events []Event
}

func open(t *testing.T) *harness {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	a.Ensure()
	h := &harness{t: t, a: a, out: &bytes.Buffer{}}
	h.restart()
	return h
}

// restart closes and reopens the journal under the same lease: a process
// restart, as far as the records can tell.
func (h *harness) restart() {
	h.t.Helper()
	if h.j != nil {
		h.j.Close()
		h.l.Release()
	}
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
	h.t.Cleanup(func() { j.Close(); l.Release() })
}

func (h *harness) driver(b invoke.Backend, o Origin) *Driver {
	if o == nil {
		o = CLIOrigin{W: h.out}
	}
	return &Driver{J: h.j, Store: h.st, Backend: b, Origin: o, Events: func(e Event) { h.events = append(h.events, e) }, Timeout: time.Minute}
}

func (h *harness) ledger() *Ledger {
	h.t.Helper()
	led, err := Fold(h.j.Production())
	if err != nil {
		h.t.Fatal(err)
	}
	return led
}

func (h *harness) only() *RunState {
	h.t.Helper()
	led := h.ledger()
	if len(led.Runs) != 1 {
		h.t.Fatalf("want exactly one run, have %d (+%d unstarted)", len(led.Runs), len(led.Unstarted))
	}
	for _, rs := range led.Runs {
		return rs
	}
	return nil
}

func (h *harness) count(kind record.Kind) int {
	n := 0
	h.j.Production().Scan(0, func(r record.Record) error {
		if r.Kind() == kind {
			n++
		}
		return nil
	})
	return n
}

func scripted(caps invoke.Capabilities, calls ...invoke.ScriptedCall) *invoke.Scripted {
	return &invoke.Scripted{Caps: caps, Calls: calls}
}

func trail(a *AttemptState) string {
	var s []string
	for _, t := range a.Transitions {
		x := string(t.To)
		if t.Delivery != "" {
			x += ":" + string(t.Delivery)
		}
		s = append(s, x)
	}
	return strings.Join(s, " ")
}

// A NOW run: intake → one attempt → judged → recorded → delivered, with the
// state machine, the events, the mission fold, and the shared B6 row all
// agreeing. A self claim of achievement resolves to `unknown` (self cannot
// promote), so the ledger row is honestly UNJUDGED — done ≠ achieved.
func TestNowRunDeliversAndRecords(t *testing.T) {
	h := open(t)
	b := scripted(toolless, invoke.ScriptedCall{Response: []byte("The capital of France is Paris."), Usage: invoke.Usage{InputTokens: 10, OutputTokens: 7, CostUSD: 0.01, CostReported: true, WallMillis: 40}})
	d := h.driver(b, nil)
	rep, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if string(rep.Payload) != "The capital of France is Paris." || rep.Mission.Outcome != MissionDelivered || len(rep.Handle) != 8 {
		t.Fatalf("%+v", rep)
	}
	rs := h.only()
	if got := trail(rs.Latest()); got != "created executing judged recorded delivered:transport_accepted" {
		t.Fatalf("states: %s", got)
	}
	if rs.Family.Family != FamilyAnswer || rs.Family.Rule != FamilyRule {
		t.Fatalf("family: %+v", rs.Family)
	}
	rec := rs.Latest().Has(Recorded).Outcome
	if rec.Terminal != invoke.TerminalComplete || rec.ClosureOut != "unknown" || rec.Usage.CostUSD != 0.01 || rs.Closure == nil || rs.Closure.Rule != "self_cannot_promote" {
		t.Fatalf("outcome: %+v closure %+v", rec, rs.Closure)
	}
	if !strings.Contains(h.out.String(), "The capital of France is Paris.") || strings.Contains(h.out.String(), "acknowledge with") {
		t.Fatalf("presentation: %q", h.out.String())
	}
	var stages []string
	for _, e := range h.events {
		if e.Run != rs.Run && e.Stage != "intake" {
			t.Fatalf("event for a foreign run: %+v", e)
		}
		stages = append(stages, e.Stage)
	}
	if got := strings.Join(stages, " "); got != "intake attempt executing execute judged recorded prepared presented delivered" {
		t.Fatalf("events: %s", got)
	}
	m := MissionOf(rs)
	if m.Outcome != MissionDelivered || m.Delivery != TransportAccepted || m.Closure != "unknown" || m.Terminal != "complete" {
		t.Fatalf("mission: %+v", m)
	}
	// the B6 row
	row := h.outcomesRow(t, d)
	want := []string{"outcome_id", "goal", "summary", "task_type", "status", "lessons", "tokens_in", "tokens_out", "elapsed_ms", "cost_usd", "model", "recorded_at", "handle_id", "lane"}
	var keys []string
	for k := range row {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	sort.Strings(want)
	if strings.Join(keys, ",") != strings.Join(want, ",") {
		t.Fatalf("B6 keys: %v", keys)
	}
	if row["goal"] != "What is the capital of France?" || row["status"] != "done" || row["lane"] != "now" || row["model"] != "m" || row["handle_id"] != rep.Handle || row["tokens_in"].(float64) != 10 || row["cost_usd"].(float64) != 0.01 {
		t.Fatalf("B6 row: %v", row)
	}
	if _, present := row["goal_achieved"]; present {
		t.Fatal("unjudged row must have goal_achieved ABSENT (rule A6)")
	}
}

func (h *harness) outcomesRow(t *testing.T, d *Driver) map[string]any {
	t.Helper()
	p, err := projector.New(h.j, OutcomesView{Store: h.st})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(filepath.Join(projector.Current(h.a), "outcomes.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := bytes.Split(bytes.TrimSpace(b), []byte("\n"))
	if len(lines) != 1 {
		t.Fatalf("want one outcome row, have %d", len(lines))
	}
	var row map[string]any
	if err := json.Unmarshal(lines[0], &row); err != nil {
		t.Fatal(err)
	}
	return row
}

// A failed execution is delivered honestly and is a failed MISSION even
// though delivery succeeded; the self demotion stands, so the row is judged
// not_achieved with the self source.
func TestFailedExecutionIsAnHonestFailedMission(t *testing.T) {
	h := open(t)
	b := scripted(toolless, invoke.ScriptedCall{Terminal: invoke.TerminalFailed, Reason: "backend exited 1"})
	d := h.driver(b, nil)
	rep, err := d.Run(ctxBg, []byte("Write a haiku"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionFailedExec || !strings.Contains(string(rep.Payload), "backend exited 1") || rep.Mission.Delivery != TransportAccepted {
		t.Fatalf("%+v %q", rep.Mission, rep.Payload)
	}
	rs := h.only()
	if got := trail(rs.Latest()); got != "created executing judged recorded delivered:transport_accepted" {
		t.Fatalf("states: %s", got)
	}
	if rs.Closure.Outcome != "not_achieved" || rs.Closure.Rule != "standing:self" {
		t.Fatalf("closure: %+v", rs.Closure)
	}
	row := h.outcomesRow(t, d)
	if row["status"] != "stuck" || row["goal_achieved"] != false || row["goal_verdict_source"] != "now_self_verdict" || row["goal_verdict_confidence"].(float64) != 0.9 {
		t.Fatalf("B6 row: %v", row)
	}
}

// The ack protocol: the token is client-presented and bound to delivery +
// payload; wrong token, wrong-payload token, ack-before-presentation, and
// unknown delivery are refused; a repeat replays; the mission moves from
// accepted_unacknowledged to delivered only on the ack.
func TestAckProtocol(t *testing.T) {
	h := open(t)
	b := scripted(toolless, invoke.ScriptedCall{Response: []byte("42")})
	d := h.driver(b, nil)
	rep, err := d.Run(ctxBg, []byte("What is six times seven?"), DeliveryPolicy{Required: UserAcknowledged})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionUnacknowledged || rep.Token == "" || !strings.Contains(h.out.String(), "maro-go ack "+string(rep.Delivery)+" "+rep.Token) {
		t.Fatalf("%+v\n%s", rep.Mission, h.out.String())
	}
	rs := h.only()
	dl := rs.Latest().Delivery
	if _, _, err := Ack(ctxBg, h.j, rep.Delivery, strings.Repeat("0", 32)); !errors.Is(err, ErrBadToken) {
		t.Fatalf("wrong token: %v", err)
	}
	other := TokenFor(rep.Delivery, "s256v1:"+strings.Repeat("ff", 32), dl.Prepared.Nonce)
	if _, _, err := Ack(ctxBg, h.j, rep.Delivery, other); !errors.Is(err, ErrBadToken) {
		t.Fatalf("wrong-payload token: %v", err)
	}
	if _, _, err := Ack(ctxBg, h.j, record.NewID(), rep.Token); !errors.Is(err, ErrNoDelivery) {
		t.Fatalf("unknown delivery: %v", err)
	}
	if h.count(KindDeliveryAcked) != 0 {
		t.Fatal("a refused ack wrote a record")
	}
	ack, replayed, err := Ack(ctxBg, h.j, rep.Delivery, rep.Token)
	if err != nil || replayed || ack.PayloadHash != dl.Prepared.Payload.Hash {
		t.Fatalf("ack: %v %v %+v", err, replayed, ack)
	}
	ack2, replayed, err := Ack(ctxBg, h.j, rep.Delivery, rep.Token)
	if err != nil || !replayed || ack2.ID != ack.ID {
		t.Fatalf("duplicate ack: %v %v", err, replayed)
	}
	rs = h.only()
	if got := trail(rs.Latest()); got != "created executing judged recorded delivered:transport_accepted delivered:user_acknowledged" {
		t.Fatalf("states: %s", got)
	}
	if m := MissionOf(rs); m.Outcome != MissionDelivered || m.Delivery != UserAcknowledged {
		t.Fatalf("mission: %+v", m)
	}
	// crash-before-display: prepared, never presented → nothing to acknowledge
	h2 := open(t)
	d2 := h2.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
	d2.CrashAt = "after_prepared"
	if _, err := d2.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: UserAcknowledged}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("seam: %v", err)
	}
	rs2 := h2.only()
	p := rs2.Latest().Delivery.Prepared
	if _, _, err := Ack(ctxBg, h2.j, p.ID, TokenFor(p.ID, p.Payload.Hash, p.Nonce)); !errors.Is(err, ErrNotPresented) {
		t.Fatalf("ack before presentation: %v", err)
	}
}

// Kill matrix: a crash after every stage commit, then Resume on a fresh
// process, ends with exactly one delivered run, one goal, one recorded
// outcome, and no blind replay of an invocation whose effect is unknown.
func TestKillMatrixResumesExactlyOnce(t *testing.T) {
	type seam struct {
		at        string
		caps      invoke.Capabilities
		attempts  int    // attempts after resume
		dispatch  int    // backend Complete calls in total
		terminal  string // execution terminal after resume
		presented int    // presentations in total
	}
	seams := []seam{
		{"after_intake", toolless, 1, 1, "complete", 1},
		{"after_start", toolless, 2, 1, "complete", 1},
		{"after_executing", toolless, 2, 1, "complete", 1},
		{"invoke:prepared", toolless, 2, 1, "complete", 1},   // invocation prepared, never dispatched: run again
		{"invoke:dispatched", toolless, 2, 1, "complete", 1}, // died after `dispatched` landed, before the backend ran; tool-less: abandoned → run again
		{"invoke:dispatched", outward, 2, 0, "failed", 1},    // outward-capable: indeterminate → honest failure, NO replay
		{"invoke:terminal", toolless, 2, 1, "complete", 1},   // terminal without receipt: reconcile finalizes, the work is reused
		{"after_execute", toolless, 2, 1, "complete", 1},
		{"after_judged", toolless, 2, 1, "complete", 1},
		{"after_recorded", toolless, 1, 1, "complete", 1},
		{"after_prepared", toolless, 1, 1, "complete", 1},
		{"after_present", toolless, 1, 1, "complete", 2}, // presented but unrecorded: presented again, honestly
		{"after_attempted", toolless, 1, 1, "complete", 1},
	}
	for _, s := range seams {
		t.Run(s.at+"/"+s.caps.Name, func(t *testing.T) {
			h := open(t)
			b := scripted(s.caps, invoke.ScriptedCall{Response: []byte("answer")}, invoke.ScriptedCall{Response: []byte("answer")})
			d := h.driver(b, nil)
			d.CrashAt = s.at
			_, err := d.Run(ctxBg, []byte("What is the answer?"), DeliveryPolicy{Required: TransportAccepted})
			if !errors.Is(err, ErrCrashed) && !errors.Is(err, invoke.ErrCrashed) {
				t.Fatalf("seam did not fire: %v", err)
			}
			h.restart()
			d = h.driver(b, nil)
			reps, err := d.Resume(ctxBg)
			if err != nil || len(reps) != 1 {
				t.Fatalf("resume: %v (%d reports)", err, len(reps))
			}
			rs := h.only()
			if !rs.Terminal() || rs.Latest().Current() != Delivered {
				t.Fatalf("not delivered: %s", trail(rs.Latest()))
			}
			if h.count(KindGoal) != 1 || len(rs.Attempts) != s.attempts || len(b.Seen) != s.dispatch || h.count(KindTransition+"") == 0 {
				t.Fatalf("goals=%d attempts=%d dispatches=%d", h.count(KindGoal), len(rs.Attempts), len(b.Seen))
			}
			recorded := 0
			for _, a := range rs.Attempts {
				if a.Has(Recorded) != nil {
					recorded++
				}
			}
			if recorded != 1 {
				t.Fatalf("recorded outcomes: %d", recorded)
			}
			if got := string(rs.Latest().Has(Recorded).Outcome.Terminal); got != s.terminal {
				t.Fatalf("terminal %s, want %s (reason %s)", got, s.terminal, rs.Latest().Has(Recorded).Outcome.Reason)
			}
			if n := strings.Count(h.out.String(), "\n---\n"); n != s.presented {
				t.Fatalf("presented %d times, want %d", n, s.presented)
			}
			if s.attempts == 2 {
				first := rs.Attempts[0]
				if first.Current() != Recoverable || rs.Attempts[1].Attempt.RecoversFrom != 1 {
					t.Fatalf("recovery not recorded: %s / %+v", trail(first), rs.Attempts[1].Attempt)
				}
			}
			// a second resume is a no-op
			head := h.j.Head()
			if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
				t.Fatalf("second resume wrote: %v %d head %d→%d", err, len(reps), head, h.j.Head())
			}
		})
	}
}

type failingOrigin struct{ n int }

func (failingOrigin) Name() GoalOrigin { return OriginCLI }
func (o *failingOrigin) Present(context.Context, Presentation) error {
	o.n++
	return fmt.Errorf("pipe closed (%d)", o.n)
}

// The outbox is bounded: a dead origin becomes delivery_failed with a
// reason, the mission is failed(delivery) despite a complete execution, a
// later resume does not retry, and nothing can be acknowledged.
func TestOutboxIsBounded(t *testing.T) {
	h := open(t)
	o := &failingOrigin{}
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), o)
	rep, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionFailedDelivery || o.n != 3 || !strings.Contains(rep.Mission.Reason, "3 presentation(s) failed") {
		t.Fatalf("%+v n=%d", rep.Mission, o.n)
	}
	rs := h.only()
	if got := trail(rs.Latest()); got != "created executing judged recorded delivery_failed" {
		t.Fatalf("states: %s", got)
	}
	head := h.j.Head()
	if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || o.n != 3 || h.j.Head() != head {
		t.Fatalf("resume retried a failed delivery: %v %d %d", err, len(reps), o.n)
	}
	p := rs.Latest().Delivery.Prepared
	if _, _, err := Ack(ctxBg, h.j, p.ID, TokenFor(p.ID, p.Payload.Hash, p.Nonce)); !errors.Is(err, ErrNotPresented) {
		t.Fatalf("ack on a failed delivery: %v", err)
	}
}

// The classifier is deterministic, total, and reads only the text.
func TestClassifierIsDeterministicAndBlind(t *testing.T) {
	cases := map[string]FamilyKey{
		"What is the capital of France?":                FamilyAnswer,
		"explain how flock differs from fcntl locks":    FamilyAnswer,
		"Write a script that renames every .txt file":   FamilyWriteLocal,
		"refactor the journal package to use io.Reader": FamilyWriteLocal,
		"Can you write the test file for me?":           FamilyNone, // question shape AND mutation shape: ambiguous
		"ponder":                                        FamilyNone,
		"":                                              FamilyNone,
	}
	for text, want := range cases {
		got, why := Classify(text)
		got2, _ := Classify(text)
		if got != want || got2 != got || why == "" {
			t.Fatalf("%q → %s (%s), want %s", text, got, why, want)
		}
	}
}

// The journal executes the run vocabulary: illegal transitions, a recorded
// transition without its outcome, foreign lanes, plan cardinalities with no
// driver, malformed tokens, and mis-subjected goals are refused unwritten.
func TestJournalExecutesRunVocabulary(t *testing.T) {
	h := open(t)
	run := record.RunID(record.NewID())
	hd := func(subj record.Ref) record.Header {
		return record.Header{ID: record.NewID(), RunID: run, Attempt: 1, Subject: subj, At: time.Now().UTC()}
	}
	goalRef := thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Goal, Bytes: 1, Encoding: thought.UTF8}
	gid := record.NewID()
	goodGoal := func() *Goal {
		return &Goal{Header: record.Header{ID: gid, Schema: "goal/1", Subject: record.Ref{Kind: "goal", ID: string(gid)}, At: time.Now().UTC()}, Root: gid, Text: goalRef, Origin: OriginCLI, Delivery: DeliveryPolicy{Required: TransportAccepted}}
	}
	goodAttempt := func() *RunAttempt {
		x := &RunAttempt{Header: hd(runRef(run)), Goal: gid, Family: record.NewID(), Config: ConfigSnapshot{Lane: LaneNow, Backend: toolless, Judge: JudgeSelf, PlanCardinality: 1}}
		x.Schema = "run_attempt/1"
		return x
	}
	tr := func(from, to State) *Transition {
		x := &Transition{Header: hd(runRef(run)), From: from, To: to}
		x.Schema = "run_transition/1"
		return x
	}
	cases := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"goal wrong subject", func() record.Record { g := goodGoal(); g.Subject.ID = "other"; return g }(), "subject must be the goal"},
		{"goal foreign policy", func() record.Record { g := goodGoal(); g.Delivery.Required = "endpoint_accepted"; return g }(), "out of vocabulary"},
		{"goal text not a goal thought", func() record.Record { g := goodGoal(); g.Text.Kind = thought.Prompt; return g }(), "goal thought"},
		{"root goal must be its own root", func() record.Record { g := goodGoal(); g.Root = record.NewID(); return g }(), "root"},
		{"attempt foreign lane", func() record.Record { a := goodAttempt(); a.Config.Lane = "agenda"; return a }(), "lane"},
		{"attempt cardinality 2", func() record.Record { a := goodAttempt(); a.Config.PlanCardinality = 2; return a }(), "cardinality"},
		{"attempt 1 recovers", func() record.Record { a := goodAttempt(); a.RecoversFrom = 1; return a }(), "recovers"},
		{"attempt no run scope", func() record.Record { a := goodAttempt(); a.RunID = ""; return a }(), "run_id"},
		{"illegal transition", tr(Created, Recorded), "not a legal"},
		{"recorded without outcome", tr(Judged, Recorded), "carries the outcome"},
		{"executing without predecessor", tr("", Executing), "no predecessor"},
		{"backwards", tr(Executing, Created), "not a legal"},
		{"delivered without state", tr(Recorded, Delivered), "delivered must name"},
		{"delivery state on executing", func() record.Record { x := tr(Created, Executing); x.Delivery = TransportAccepted; return x }(), "only delivered"},
		{"recoverable without reason", tr(Executing, Recoverable), "needs a reason"},
		{"unknown state", tr(Created, "paused"), "out of vocabulary"},
		{"recorded outcome foreign terminal", func() record.Record {
			x := tr(Judged, Recorded)
			x.Outcome = &Outcome{Terminal: "hung", GoalText: goalRef, Closure: record.NewID(), ClosureOut: "unknown"}
			return x
		}(), "terminal"},
		{"recorded complete without receipt", func() record.Record {
			x := tr(Judged, Recorded)
			x.Outcome = &Outcome{Terminal: invoke.TerminalComplete, GoalText: goalRef, Closure: record.NewID(), ClosureOut: "unknown"}
			return x
		}(), "names its receipt"},
		{"ack bad token", func() record.Record {
			id := record.NewID()
			x := &DeliveryAcked{Header: hd(record.Ref{Kind: "delivery", ID: string(id)}), Delivery: id, Token: "abc", PayloadHash: goalRef.Hash}
			x.Schema = "delivery_acked/1"
			return x
		}(), "token"},
		{"prepared short nonce", func() record.Record {
			id := record.NewID()
			x := &DeliveryPrepared{Header: hd(record.Ref{Kind: "delivery", ID: string(id)}), Payload: thought.Ref{Hash: goalRef.Hash, Kind: thought.Deliverable, Bytes: 1, Encoding: thought.UTF8}, Origin: OriginCLI, Required: TransportAccepted, Nonce: "ab"}
			x.Schema, x.ID = "delivery_prepared/1", id
			return x
		}(), "nonce"},
		{"attempted n zero", func() record.Record {
			id := record.NewID()
			x := &DeliveryAttempted{Header: hd(record.Ref{Kind: "delivery", ID: string(id)}), Delivery: id, N: 0, Result: TransportAccepted}
			x.Schema = "delivery_attempted/1"
			return x
		}(), "n starts"},
	}
	for _, c := range cases {
		_, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: c.name, Epoch: h.j.Epoch(), Records: []record.Record{c.rec}})
		if err == nil || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: want refusal containing %q, got %v", c.name, c.want, err)
		}
	}
	if h.j.Head() != 0 {
		t.Fatal("a refused record was written")
	}
	// the good shapes are accepted (so the refusals above are the rules, not the fixtures)
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "good", Epoch: h.j.Epoch(), Records: []record.Record{goodGoal(), goodAttempt(), tr("", Created)}}); err != nil {
		t.Fatal(err)
	}
}

// The fold refuses histories the driver could not have written.
func TestFoldRefusesIllegalHistory(t *testing.T) {
	h := open(t)
	run := record.RunID(record.NewID())
	x := &Transition{Header: record.Header{ID: record.NewID(), Schema: "run_transition/1", RunID: run, Attempt: 1, Subject: runRef(run), At: time.Now().UTC()}, To: Created}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "orphan", Epoch: h.j.Epoch(), Records: []record.Record{x}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Fold(h.j.Production()); err == nil || !strings.Contains(err.Error(), "never started") {
		t.Fatalf("orphan transition folded: %v", err)
	}
}

// The recorded outcome is a fold: recomputed from the receipt and the
// resolution alone, it matches what the driver stamped.
func TestRecordedOutcomeIsAFold(t *testing.T) {
	h := open(t)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("r"), Usage: invoke.Usage{InputTokens: 3, OutputTokens: 1, WallMillis: 9}}), nil)
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	stamped := rs.Latest().Has(Recorded).Outcome
	inv := rs.Latest().Invocations
	if len(inv) != 1 || inv[0].Receipt == nil {
		t.Fatalf("invocations: %d", len(inv))
	}
	cur, err := verdict.Current(h.j.Production())
	if err != nil {
		t.Fatal(err)
	}
	var res *verdict.Resolution
	for _, r := range cur {
		res = r
	}
	rc := inv[0].Receipt
	resp := rc.Response
	re := &Outcome{Terminal: inv[0].Terminal.State, Invocation: inv[0].Invocation.ID, Receipt: rc.ID, Response: &resp, Usage: rc.Usage, Model: rs.Latest().Attempt.Config.Backend.Model, GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence}
	a, _ := json.Marshal(stamped)
	b, _ := json.Marshal(re)
	if string(a) != string(b) {
		t.Fatalf("stamped outcome is not the fold:\n%s\n%s", a, b)
	}
}

// Live: the first real delivered answer through the subprocess path.
func TestLiveNowDelivery(t *testing.T) {
	if !invoke.LiveAvailable() {
		t.Skip("set MARO_GO_LIVE=1 with the claude CLI logged in")
	}
	h := open(t)
	b, err := invoke.NewSubprocess("haiku")
	if err != nil {
		t.Fatal(err)
	}
	d := h.driver(b, nil)
	d.Timeout = 3 * time.Minute
	rep, err := d.Run(ctxBg, []byte("Reply with exactly the word PONG and nothing else."), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionDelivered || !strings.Contains(strings.ToUpper(string(rep.Payload)), "PONG") {
		t.Fatalf("%+v %q", rep.Mission, rep.Payload)
	}
	t.Logf("live: handle %s usage %+v\n%s", rep.Handle, h.only().Latest().Has(Recorded).Outcome.Usage, h.out.String())
}
