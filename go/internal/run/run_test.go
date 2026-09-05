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
	"regexp"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
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
	evMu   sync.Mutex
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
	return &Driver{J: h.j, Store: h.st, Backend: b, Origin: o, Events: func(e Event) {
		h.evMu.Lock()
		h.events = append(h.events, e)
		h.evMu.Unlock()
	}, Timeout: time.Minute}
}

func (h *harness) ledger() *Ledger {
	h.t.Helper()
	led, err := Fold(h.j.Production(), h.st)
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
	if got := strings.Join(stages, " "); got != "intake landscape policy attempt executing recall execute judged recorded prepared presented delivered" {
		t.Fatalf("events: %s", got)
	}
	m := MissionOf(rs)
	if m.Outcome != MissionDelivered || m.Delivery != TransportAccepted || m.Closure != "unknown" || m.Terminal != "complete" {
		t.Fatalf("mission: %+v", m)
	}
	// the B6 row
	row := h.outcomesRow(t, d)
	want := []string{"outcome_id", "goal", "summary", "task_type", "status", "lessons", "tokens_in", "tokens_out", "elapsed_ms", "cost_usd", "model", "recorded_at", "handle_id"}
	var keys []string
	for k := range row {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	sort.Strings(want)
	if strings.Join(keys, ",") != strings.Join(want, ",") {
		t.Fatalf("B6 keys: %v", keys)
	}
	if row["goal"] != "What is the capital of France?" || row["status"] != "done" || row["task_type"] != "now" || row["model"] != "m" || row["handle_id"] != rep.Handle || row["tokens_in"].(float64) != 10 || row["cost_usd"].(float64) != 0.01 {
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
	if _, _, err := Ack(ctxBg, h.j, h.st, rep.Delivery, strings.Repeat("0", 32)); !errors.Is(err, ErrBadToken) {
		t.Fatalf("wrong token: %v", err)
	}
	other := TokenFor(rep.Delivery, "s256v1:"+strings.Repeat("ff", 32), dl.Prepared.Nonce)
	if _, _, err := Ack(ctxBg, h.j, h.st, rep.Delivery, other); !errors.Is(err, ErrBadToken) {
		t.Fatalf("wrong-payload token: %v", err)
	}
	if _, _, err := Ack(ctxBg, h.j, h.st, record.NewID(), rep.Token); !errors.Is(err, ErrNoDelivery) {
		t.Fatalf("unknown delivery: %v", err)
	}
	if h.count(KindDeliveryAcked) != 0 {
		t.Fatal("a refused ack wrote a record")
	}
	ack, replayed, err := Ack(ctxBg, h.j, h.st, rep.Delivery, rep.Token)
	if err != nil || replayed || ack.PayloadHash != dl.Prepared.Payload.Hash {
		t.Fatalf("ack: %v %v %+v", err, replayed, ack)
	}
	ack2, replayed, err := Ack(ctxBg, h.j, h.st, rep.Delivery, rep.Token)
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
	if _, _, err := Ack(ctxBg, h2.j, h2.st, p.ID, TokenFor(p.ID, p.Payload.Hash, p.Nonce)); !errors.Is(err, ErrNotPresented) {
		t.Fatalf("ack before presentation: %v", err)
	}
	// crash-after-display, before the result landed: the start is the
	// evidence the token could have reached the client — the ack from the
	// first display is accepted, and no second presentation happens
	h3 := open(t)
	d3 := h3.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
	d3.CrashAt = "after_present"
	if _, err := d3.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: UserAcknowledged}); !errors.Is(err, ErrCrashed) {
		t.Fatalf("seam: %v", err)
	}
	shown := regexp.MustCompile(`maro-go ack (\S+) (\S+)`).FindStringSubmatch(h3.out.String())
	if shown == nil {
		t.Fatal("token not shown")
	}
	h3.restart()
	if _, replayed, err := Ack(ctxBg, h3.j, h3.st, record.RecordID(shown[1]), shown[2]); err != nil || replayed {
		t.Fatalf("ack from the first display: %v", err)
	}
	d3 = h3.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
	if _, err := d3.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	rs3 := h3.only()
	if m := MissionOf(rs3); m.Outcome != MissionDelivered || m.Delivery != UserAcknowledged || strings.Count(h3.out.String(), "\n---\n") != 1 {
		t.Fatalf("acked delivery was presented again or not delivered: %+v\n%s", m, h3.out.String())
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
		unknown   int    // presentations the process died inside (may have reached the user)
	}
	seams := []seam{
		{"after_intake", toolless, 1, 1, "complete", 1, 0},
		{"after_start", toolless, 2, 1, "complete", 1, 0},
		{"after_executing", toolless, 2, 1, "complete", 1, 0},
		{"after_recall", toolless, 2, 1, "complete", 1, 0},       // selection committed, no request: run again (a new selection for the new attempt)
		{"after_applications", toolless, 2, 1, "complete", 1, 0}, // invocation + applications landed, receipt not: reconcile finalizes, reused
		{"invoke:prepared", toolless, 2, 1, "complete", 1, 0},    // invocation prepared, never dispatched: run again
		{"invoke:dispatched", toolless, 2, 1, "complete", 1, 0},  // died after `dispatched` landed, before the backend ran; tool-less: abandoned → run again
		{"invoke:dispatched", outward, 2, 0, "failed", 1, 0},     // outward-capable: indeterminate → honest failure, NO replay
		{"invoke:terminal", toolless, 2, 1, "complete", 1, 0},    // terminal without receipt: reconcile finalizes, the work is reused
		{"after_execute", toolless, 2, 1, "complete", 1, 0},
		{"after_judged", toolless, 2, 1, "complete", 1, 0},
		{"after_recorded", toolless, 1, 1, "complete", 1, 0},
		{"after_prepared", toolless, 1, 1, "complete", 1, 0},
		{"after_started", toolless, 1, 1, "complete", 1, 1}, // start on record, nothing shown: resolved unknown, presented again and SAID so
		{"after_present", toolless, 1, 1, "complete", 2, 1}, // shown, result not recorded: same
		{"after_attempted", toolless, 1, 1, "complete", 1, 0},
	}
	for _, s := range seams {
		t.Run(s.at+"/"+s.caps.Name, func(t *testing.T) {
			h := open(t)
			h.lesson(t, "always cite the file", learn.Provisional)
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
			m := MissionOf(rs)
			if m.MayDuplicate != s.unknown {
				t.Fatalf("may_duplicate %d, want %d: %+v", m.MayDuplicate, s.unknown, m)
			}
			if s.unknown > 0 && (!strings.Contains(h.out.String(), "re-presented: 1 earlier") || rs.Latest().Delivery.Attempts[0].Result != DeliveryUnknown) {
				t.Fatalf("duplicate presentation not visible: %+v\n%s", m, h.out.String())
			}
			if got := rs.Latest().Has(Recorded).Outcome; got.Invocation != "" && (got.Produced == 0 || got.Model != "m" || got.Recall == "") {
				t.Fatalf("provenance: %+v", got)
			}
			if got := rs.Latest().Has(Recorded).Outcome; got.Invocation != "" && len(h.ledger().Learned.Applications[got.Invocation]) != 1 {
				t.Fatalf("the promoted lesson's application did not survive the seam: %+v", got)
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
	if _, _, err := Ack(ctxBg, h.j, h.st, p.ID, TokenFor(p.ID, p.Payload.Hash, p.Nonce)); !errors.Is(err, ErrNotPresented) {
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
		return &Goal{Header: record.Header{ID: gid, Schema: "goal/1", Subject: record.Ref{Kind: "goal", ID: string(gid)}, At: time.Now().UTC()}, Root: gid, Text: goalRef, Origin: OriginCLI, Lane: LaneNow, Delivery: DeliveryPolicy{Required: TransportAccepted}}
	}
	goodAttempt := func() *RunAttempt {
		x := &RunAttempt{Header: hd(runRef(run)), Goal: gid, Family: record.NewID(), Config: ConfigSnapshot{Lane: LaneNow, Backend: toolless, Judge: JudgeSelf, PlanCardinality: 1, Policy: record.NewID(), Mechanisms: learn.Defaults()}}
		x.Schema = "run_attempt/1"
		return x
	}
	tr := func(from, to State) *Transition {
		x := &Transition{Header: hd(runRef(run)), From: from, To: to}
		x.Schema = "run_transition/1"
		return x
	}
	goodLandscape := func() *Landscape {
		x := &Landscape{Header: hd(runRef(run)), Goal: gid, AsOf: 1, Rule: LandscapeJudge, Floor: LandscapeFloor, TopK: LandscapeTopK, Scanned: 2, BelowFloor: 1, Candidates: []LandscapeCandidate{{Run: "run-2", Goal: gid, Similarity: 0.5}}, Relation: RelationRelated, Chosen: "run-2", Judge: record.NewID()}
		x.Schema, x.Attempt = "landscape/1", 0
		return x
	}
	cases := []struct {
		name string
		rec  record.Record
		want string
	}{
		{"goal wrong subject", func() record.Record { g := goodGoal(); g.Subject.ID = "other"; return g }(), "subject must be the goal"},
		{"goal foreign policy", func() record.Record { g := goodGoal(); g.Delivery.Required = "endpoint_accepted"; return g }(), "out of vocabulary"},
		{"goal foreign lane", func() record.Record { g := goodGoal(); g.Lane = "later"; return g }(), "lane"},
		{"landscape foreign rule", func() record.Record { x := goodLandscape(); x.Rule = "vibes"; return x }(), "rule"},
		{"landscape foreign relation", func() record.Record { x := goodLandscape(); x.Relation = "cousin"; return x }(), "relation"},
		{"landscape chosen not a candidate", func() record.Record { x := goodLandscape(); x.Chosen = "other"; return x }(), "not a candidate"},
		{"landscape fresh with a chosen run", func() record.Record { x := goodLandscape(); x.Relation, x.Chosen = RelationFresh, "run-2"; return x }(), "fresh names none"},
		{"landscape judged without candidates", func() record.Record {
			x := goodLandscape()
			x.Candidates = nil
			x.Relation, x.Chosen = RelationFresh, ""
			return x
		}(), "names its judge"},
		{"landscape no call with candidates", func() record.Record { x := goodLandscape(); x.Rule = LandscapeNoCandidates; return x }(), "no candidates and no call"},
		{"landscape unreadable that decided", func() record.Record { x := goodLandscape(); x.Rule = LandscapeUnreadable; return x }(), "decides nothing"},
		{"landscape below the floor", func() record.Record { x := goodLandscape(); x.Candidates[0].Similarity = 0.1; return x }(), "floor"},
		{"landscape after attempt 1", func() record.Record { x := goodLandscape(); x.Attempt = 1; return x }(), "before its first attempt"},
		{"landscape unknown prompt version", func() record.Record { x := goodLandscape(); x.PromptVer = 9; return x }(), "prompt version"},
		{"landscape prompt version without a call", func() record.Record {
			x := goodLandscape()
			x.Rule, x.Candidates, x.Judge, x.Relation, x.Chosen, x.PromptVer = LandscapeNoCandidates, nil, "", RelationFresh, "", 2
			return x
		}(), "call that was not made"},
		{"landscape more than top_k", func() record.Record {
			x := goodLandscape()
			x.Candidates = []LandscapeCandidate{{Run: "run-2", Goal: gid, Similarity: 0.5}, {Run: "run-3", Goal: gid, Similarity: 0.5}, {Run: "run-4", Goal: gid, Similarity: 0.5}, {Run: "run-5", Goal: gid, Similarity: 0.5}}
			return x
		}(), "top_k"},
		{"interrupt foreign action", &Interrupt{Header: record.Header{ID: record.NewID(), Schema: "interrupt/1", Subject: runRef(run), At: time.Now().UTC()}, Target: run, Action: "pause", Why: "x"}, "action"},
		{"interrupt without why", &Interrupt{Header: record.Header{ID: record.NewID(), Schema: "interrupt/1", Subject: runRef(run), At: time.Now().UTC()}, Target: run, Action: "cancel", Why: " "}, "why"},
		{"interrupt ack foreign result", &InterruptAck{Header: record.Header{ID: record.NewID(), Schema: "interrupt_ack/1", Subject: record.Ref{Kind: "interrupt", ID: string(gid)}, At: time.Now().UTC()}, Interrupt: gid, Result: "ignored"}, "out of vocabulary"},
		{"interrupt ack consumed without boundary", &InterruptAck{Header: record.Header{ID: record.NewID(), Schema: "interrupt_ack/1", RunID: run, Attempt: 1, Subject: record.Ref{Kind: "interrupt", ID: string(gid)}, At: time.Now().UTC()}, Interrupt: gid, Result: "consumed"}, "boundary"},
		{"goal text not a goal thought", func() record.Record { g := goodGoal(); g.Text.Kind = thought.Prompt; return g }(), "goal thought"},
		{"root goal must be its own root", func() record.Record { g := goodGoal(); g.Root = record.NewID(); return g }(), "root"},
		{"attempt foreign lane", func() record.Record { a := goodAttempt(); a.Config.Lane = "later"; return a }(), "lane"},
		{"agenda without a judge backend", func() record.Record {
			a := goodAttempt()
			a.Config.Lane, a.Config.Judge, a.Config.PlanCardinality = LaneAgenda, JudgeModel, 0
			return a
		}(), "named backend"},
		{"attempt cardinality 2", func() record.Record { a := goodAttempt(); a.Config.PlanCardinality = 2; return a }(), "one execute"},
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
			x.Outcome = &Outcome{Lane: LaneNow, Terminal: "hung", GoalText: goalRef, Closure: record.NewID(), ClosureOut: "unknown"}
			return x
		}(), "terminal"},
		{"recorded complete without receipt", func() record.Record {
			x := tr(Judged, Recorded)
			x.Outcome = &Outcome{Lane: LaneNow, Terminal: invoke.TerminalComplete, GoalText: goalRef, Closure: record.NewID(), ClosureOut: "unknown"}
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
		{"attempted unknown without reason", func() record.Record {
			id := record.NewID()
			x := &DeliveryAttempted{Header: hd(record.Ref{Kind: "delivery", ID: string(id)}), Delivery: id, N: 1, Result: DeliveryUnknown}
			x.Schema = "delivery_attempted/1"
			return x
		}(), "carries a reason"},
		{"outcome with invocation but no producer", func() record.Record {
			x := tr(Judged, Recorded)
			x.Outcome = &Outcome{Lane: LaneNow, Terminal: invoke.TerminalFailed, Reason: "r", Invocation: record.NewID(), GoalText: goalRef, Closure: record.NewID(), ClosureOut: "unknown"}
			return x
		}(), "produced"},
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
	if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), "never started") {
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
	re := &Outcome{Lane: LaneNow, Terminal: inv[0].Terminal.State, Invocation: inv[0].Invocation.ID, Produced: 1, Recall: rs.Latest().Recall.ID, Receipt: rc.ID, Response: &resp, Usage: rc.Usage, Model: rs.Latest().Attempt.Config.Backend.Model, GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence}
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

// forge submits hand-built records straight to the journal (they pass the
// door: each is wire-valid on its own) and returns Fold's verdict.
func forge(t *testing.T, h *harness, key string, recs ...record.Record) error {
	t.Helper()
	for _, r := range recs {
		if r.Head().Schema == "" {
			spec, _ := record.Lookup(r.Kind())
			r.Head().Schema = record.SchemaVer(fmt.Sprintf("%s/%d", r.Kind(), spec.Version))
		}
	}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: key, Epoch: h.j.Epoch(), Records: recs}); err != nil {
		t.Fatalf("forged record refused at the door (fixture bug): %v", err)
	}
	_, err := Fold(h.j.Production(), h.st)
	return err
}

// Must-detect fixtures for the fold: every cross-record rule the journal
// door cannot execute. Each forged history is wire-valid record by record
// and must still be refused, with the reason named.
func TestFoldRefusesForgedHistories(t *testing.T) {
	type fix struct {
		name string
		mk   func(h *harness, rs *RunState) []record.Record
		want string
	}
	hd := func(rs *RunState, subj record.Ref) record.Header {
		return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: rs.Latest().Attempt.Attempt, Subject: subj, At: time.Now().UTC()}
	}
	fixes := []fix{
		{"ack without a start", func(h *harness, rs *RunState) []record.Record {
			p := rs.Latest().Delivery.Prepared
			return []record.Record{&DeliveryAcked{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, Token: TokenFor(p.ID, p.Payload.Hash, p.Nonce), PayloadHash: p.Payload.Hash}}
		}, "no presentation was started"},
		{"ack with a foreign token", func(h *harness, rs *RunState) []record.Record {
			p := rs.Latest().Delivery.Prepared
			return []record.Record{&DeliveryStarted{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, N: 1},
				&DeliveryAcked{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, Token: strings.Repeat("0", 32), PayloadHash: p.Payload.Hash}}
		}, "not bound"},
		{"ack with a foreign payload hash", func(h *harness, rs *RunState) []record.Record {
			p := rs.Latest().Delivery.Prepared
			other := "s256v1:" + strings.Repeat("ff", 32)
			return []record.Record{&DeliveryStarted{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, N: 1},
				&DeliveryAcked{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, Token: TokenFor(p.ID, p.Payload.Hash, p.Nonce), PayloadHash: other}}
		}, "not bound"},
		{"attempt from another run", func(h *harness, rs *RunState) []record.Record {
			p := rs.Latest().Delivery.Prepared
			x := &DeliveryAttempted{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, N: 1, Result: TransportAccepted}
			x.RunID = record.RunID(record.NewID())
			return []record.Record{&DeliveryStarted{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, N: 1}, x}
		}, "scoped to"},
		{"attempt without its start", func(h *harness, rs *RunState) []record.Record {
			p := rs.Latest().Delivery.Prepared
			return []record.Record{&DeliveryAttempted{Header: hd(rs, deliveryRef(p.ID)), Delivery: p.ID, N: 1, Result: TransportAccepted}}
		}, "without its start"},
		{"delivered with no accepted presentation", func(h *harness, rs *RunState) []record.Record {
			return []record.Record{&Transition{Header: hd(rs, runRef(rs.Run)), From: Recorded, To: Delivered, Delivery: TransportAccepted}}
		}, "no accepted presentation"},
		{"user_acknowledged with no ack", func(h *harness, rs *RunState) []record.Record {
			return []record.Record{&Transition{Header: hd(rs, runRef(rs.Run)), From: Recorded, To: Delivered, Delivery: UserAcknowledged}}
		}, "no ack"},
		{"delivery_failed after nothing", func(h *harness, rs *RunState) []record.Record {
			return []record.Record{&Transition{Header: hd(rs, runRef(rs.Run)), From: Recorded, To: DeliveryFailedS, Reason: "gave up"}}
		}, "exhausted"},
		{"delivery for another policy", func(h *harness, rs *RunState) []record.Record {
			id := record.NewID()
			x := &DeliveryPrepared{Header: hd(rs, deliveryRef(id)), Payload: rs.Latest().Delivery.Prepared.Payload, Origin: OriginCLI, Required: UserAcknowledged, Nonce: strings.Repeat("ab", 16)}
			x.ID = id
			x.Attempt = 99 // an attempt that does not exist would fail earlier; use attempt 1 on a fresh run below instead
			return []record.Record{x}
		}, "never started"},
		{"second assessment", func(h *harness, rs *RunState) []record.Record {
			return []record.Record{&FamilyAssessment{Header: record.Header{ID: record.NewID(), Subject: record.Ref{Kind: "goal", ID: string(rs.Goal.ID)}, At: time.Now().UTC()}, Goal: rs.Goal.ID, Family: FamilyWriteLocal, Rule: FamilyRule, Reason: "revised"}}
		}, "assessed twice"},
	}
	for _, f := range fixes {
		t.Run(f.name, func(t *testing.T) {
			h := open(t)
			d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
			d.CrashAt = "after_prepared" // recorded + prepared, nothing presented
			if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs := h.only()
			err := forge(t, h, "forge", f.mk(h, rs)...)
			if err == nil || !strings.Contains(err.Error(), f.want) {
				t.Fatalf("forged history folded: %v (want %q)", err, f.want)
			}
		})
	}
	// delivered → delivered may only promote; a repeat or a downgrade is refused
	t.Run("delivered repeat and downgrade", func(t *testing.T) {
		h := open(t)
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
		rep, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: UserAcknowledged})
		if err != nil {
			t.Fatal(err)
		}
		rs := h.only()
		if err := forge(t, h, "repeat", &Transition{Header: hd(rs, runRef(rs.Run)), From: Delivered, To: Delivered, Delivery: TransportAccepted}); err == nil || !strings.Contains(err.Error(), "only promote") {
			t.Fatalf("repeat folded: %v", err)
		}
		h2 := open(t)
		d2 := h2.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
		rep, err = d2.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: UserAcknowledged})
		if err != nil {
			t.Fatal(err)
		}
		if _, _, err := Ack(ctxBg, h2.j, h2.st, rep.Delivery, rep.Token); err != nil {
			t.Fatal(err)
		}
		rs = h2.only()
		if err := forge(t, h2, "downgrade", &Transition{Header: hd(rs, runRef(rs.Run)), From: Delivered, To: Delivered, Delivery: TransportAccepted}); err == nil || !strings.Contains(err.Error(), "only promote") {
			t.Fatalf("downgrade folded: %v", err)
		}
	})
	// a recorded outcome that lies: wrong goal thought, wrong model, a
	// closure that does not re-derive, a source the resolution does not name
	t.Run("recorded outcome lies", func(t *testing.T) {
		mk := func(t *testing.T, mut func(o *Outcome, rs *RunState, h *harness)) error {
			h := open(t)
			d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
			d.CrashAt = "after_judged"
			if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs := h.only()
			a := rs.Latest()
			inv := a.Invocations[0]
			// the honest outcome, then the lie
			res, err := verdict.Commit(ctxBg, h.j, rs.Run, 1, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: h.verdicts(t, rs.Run)}, verdict.DefaultThresholds)
			if err != nil {
				t.Fatal(err)
			}
			resp := inv.Receipt.Response
			o := &Outcome{Lane: LaneNow, Terminal: inv.Terminal.State, Invocation: inv.Invocation.ID, Produced: 1, Recall: a.Recall.ID, Receipt: inv.Receipt.ID, Response: &resp, Usage: inv.Receipt.Usage, Model: "m", GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence}
			mut(o, rs, h)
			return forge(t, h, "lie", &Transition{Header: hd(rs, runRef(rs.Run)), From: Judged, To: Recorded, Outcome: o})
		}
		otherGoal := thought.Ref{Hash: "s256v1:" + strings.Repeat("ee", 32), Kind: thought.Goal, Bytes: 2, Encoding: thought.UTF8}
		cases := []struct {
			name string
			mut  func(o *Outcome, rs *RunState, h *harness)
			want string
		}{
			{"honest", func(o *Outcome, rs *RunState, h *harness) {}, ""},
			{"other goal", func(o *Outcome, rs *RunState, h *harness) { o.GoalText = otherGoal }, "different goal"},
			{"other model", func(o *Outcome, rs *RunState, h *harness) { o.Model = "gpt-9" }, "recorded model"},
			{"other usage", func(o *Outcome, rs *RunState, h *harness) { o.Usage.InputTokens = 999 }, "usage"},
			{"other lane", func(o *Outcome, rs *RunState, h *harness) { o.Lane = LaneAgenda }, "recorded lane"},
			{"promoted closure", func(o *Outcome, rs *RunState, h *harness) { o.ClosureOut = "achieved" }, "resolution says"},
			{"invented source", func(o *Outcome, rs *RunState, h *harness) { o.ClosureSrc = "operator" }, "effective verdict"},
			{"foreign closure", func(o *Outcome, rs *RunState, h *harness) { o.Closure = record.NewID() }, "not this attempt's closure"},
			{"foreign recall", func(o *Outcome, rs *RunState, h *harness) { o.Recall = record.NewID() }, "did not make"},
		}
		for _, c := range cases {
			err := mk(t, c.mut)
			if c.want == "" {
				if err != nil {
					t.Fatalf("honest outcome refused: %v", err)
				}
				continue
			}
			if err == nil || !strings.Contains(err.Error(), c.want) {
				t.Fatalf("%s: folded: %v (want %q)", c.name, err, c.want)
			}
		}
	})
	// a resolution that does not re-derive from its named candidates
	t.Run("forged resolution", func(t *testing.T) {
		h := open(t)
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
		d.CrashAt = "after_judged"
		if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		vs := h.verdicts(t, rs.Run)
		inv := rs.Latest().Invocations[0]
		res := &verdict.Resolution{Header: hd(rs, runRef(rs.Run)), VerdictKind: verdict.KindClosure, Outcome: "achieved", Effective: vs[0].ID, Candidates: []record.RecordID{vs[0].ID}, ResolverVer: verdict.ResolverVer, Thresholds: verdict.DefaultThresholds, Rule: "standing:self", Confidence: 0.5}
		resp := inv.Receipt.Response
		o := &Outcome{Lane: LaneNow, Terminal: inv.Terminal.State, Invocation: inv.Invocation.ID, Produced: 1, Recall: rs.Latest().Recall.ID, Receipt: inv.Receipt.ID, Response: &resp, Usage: inv.Receipt.Usage, Model: "m", GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: "achieved", ClosureCnf: 0.5, ClosureSrc: "self"}
		if err := forge(t, h, "forged-res", res, &Transition{Header: hd(rs, runRef(rs.Run)), From: Judged, To: Recorded, Outcome: o}); err == nil || !strings.Contains(err.Error(), "disagrees with its recompute") {
			t.Fatalf("forged resolution folded: %v", err)
		}
	})
}

func (h *harness) verdicts(t *testing.T, run record.RunID) []*verdict.Verdict {
	t.Helper()
	var vs []*verdict.Verdict
	h.j.Production().Scan(0, func(r record.Record) error {
		if v, ok := r.(*verdict.Verdict); ok && v.RunID == run {
			vs = append(vs, v)
		}
		return nil
	})
	return vs
}

// A refusal the shell makes before writing (input over the backend's
// maximum) is deterministic: the attempt records it as an honest failure
// and delivers it; Resume does not start a fourth, fifth, ... attempt.
func TestPreDispatchRefusalIsRecordedNotRetried(t *testing.T) {
	h := open(t)
	tiny := &invoke.Scripted{Caps: invoke.Capabilities{Name: "tiny", Model: "t", MaxInputBytes: 8}, Calls: []invoke.ScriptedCall{{Response: []byte("x")}}}
	d := h.driver(tiny, nil)
	rep, err := d.Run(ctxBg, []byte("a goal far longer than eight bytes"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	if rep.Mission.Outcome != MissionFailedExec || !strings.Contains(rep.Mission.Reason, "backend_incapable") || len(tiny.Seen) != 0 {
		t.Fatalf("%+v seen=%d", rep.Mission, len(tiny.Seen))
	}
	rs := h.only()
	if o := rs.Latest().Has(Recorded).Outcome; o.Invocation != "" || o.Model != "" {
		t.Fatalf("a refusal with no invocation must carry no provenance: %+v", o)
	}
	head := h.j.Head()
	if reps, err := d.Resume(ctxBg); err != nil || len(reps) != 0 || h.j.Head() != head {
		t.Fatalf("resume retried a recorded refusal: %v %d", err, len(reps))
	}
}

// A crash that repeats on every restart is bounded: past MaxAttempts the
// next attempt records an honest failure naming the loop and delivers it.
func TestRecoveryIsBounded(t *testing.T) {
	h := open(t)
	b := scripted(toolless, invoke.ScriptedCall{Panic: true}, invoke.ScriptedCall{Panic: true}, invoke.ScriptedCall{Panic: true}, invoke.ScriptedCall{Panic: true})
	d := h.driver(b, nil)
	d.CrashAt = "after_executing"
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		h.restart()
		d = h.driver(b, nil)
		d.CrashAt = "after_executing"
		if _, err := d.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
			t.Fatalf("restart %d: %v", i, err)
		}
	}
	h.restart()
	d = h.driver(b, nil)
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("%v %d", err, len(reps))
	}
	rs := h.only()
	if len(rs.Attempts) != 4 || reps[0].Mission.Outcome != MissionFailedExec || !strings.Contains(reps[0].Mission.Reason, "attempt bound 3 reached") || len(b.Seen) != 0 {
		t.Fatalf("attempts=%d %+v seen=%d", len(rs.Attempts), reps[0].Mission, len(b.Seen))
	}
}

// A recovered attempt that reuses the earlier attempt's receipt keeps the
// PRODUCING invocation's provenance, whatever backend the resuming process
// was configured with.
func TestReusedEvidenceKeepsItsProvenance(t *testing.T) {
	h := open(t)
	d := h.driver(scripted(invoke.Capabilities{Name: "scripted", Model: "original"}, invoke.ScriptedCall{Response: []byte("answer")}), nil)
	d.CrashAt = "after_execute"
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	h.restart()
	other := scripted(invoke.Capabilities{Name: "scripted", Model: "resumer"}, invoke.ScriptedCall{Response: []byte("different")})
	d = h.driver(other, nil)
	reps, err := d.Resume(ctxBg)
	if err != nil || len(reps) != 1 {
		t.Fatalf("%v", err)
	}
	rs := h.only()
	o := rs.Latest().Has(Recorded).Outcome
	if o.Model != "original" || o.Produced != 1 || len(other.Seen) != 0 || string(reps[0].Payload) != "answer" || rs.Latest().Attempt.Config.Backend.Model != "resumer" {
		t.Fatalf("provenance: %+v payload=%q seen=%d", o, reps[0].Payload, len(other.Seen))
	}
	row := h.outcomesRow(t, d)
	if row["model"] != "original" {
		t.Fatalf("B6 model: %v", row["model"])
	}
}

type panickingOrigin struct{ calls int }

func (panickingOrigin) Name() GoalOrigin { return OriginCLI }
func (o *panickingOrigin) Present(context.Context, Presentation) error {
	o.calls++
	panic("writer gone")
}

// Driver misconfiguration and boundary inputs err closed: negative bounds
// and empty goals are refused before anything is written; an origin panic
// is a failed presentation, not a crash.
func TestDriverBoundaries(t *testing.T) {
	h := open(t)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), nil)
	d.MaxDeliveryAttempts = -1
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrConfig) {
		t.Fatalf("negative bound: %v", err)
	}
	d.MaxDeliveryAttempts = 0
	for _, g := range []string{"", "   \n\t"} {
		if _, err := d.Run(ctxBg, []byte(g), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrEmptyGoal) {
			t.Fatalf("empty goal %q: %v", g, err)
		}
	}
	if h.j.Head() != 0 {
		t.Fatal("a refused run wrote records")
	}
	o := &panickingOrigin{}
	d = h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("x")}), o)
	rep, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil || rep.Mission.Outcome != MissionFailedDelivery || o.calls != 3 || !strings.Contains(rep.Mission.Reason, "origin panicked") {
		t.Fatalf("%v %+v calls=%d", err, rep.Mission, o.calls)
	}
}

// lesson adds a workspace-scoped lesson at the given stage (via the
// operator path) and returns its item id and revision.
func (h *harness) lesson(t *testing.T, text string, stage learn.Stage) learn.ItemRev {
	t.Helper()
	ref, err := h.st.Put(thought.LessonText, []byte(text))
	if err != nil {
		t.Fatal(err)
	}
	item := learn.LearnedID(record.NewID())
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	recs := []record.Record{r}
	if stage != learn.Candidate {
		recs = append(recs, &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, Revision: r.ID, From: learn.Candidate, To: stage, Actor: learn.ActorOperator, Why: "test"})
	}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "lesson/" + string(item), Epoch: h.j.Epoch(), Records: recs}); err != nil {
		t.Fatal(err)
	}
	return learn.ItemRev{Item: item, Revision: r.ID}
}

func (h *harness) stage(t *testing.T, ir learn.ItemRev, from, to learn.Stage) {
	t.Helper()
	x := &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(ir.Item)}, At: time.Now().UTC()}, Item: ir.Item, Revision: ir.Revision, From: from, To: to, Actor: learn.ActorOperator, Why: "test"}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "stage/" + string(x.ID), Epoch: h.j.Epoch(), Records: []record.Record{x}}); err != nil {
		t.Fatal(err)
	}
}

// requestOf returns the bytes of the attempt's execute request thought.
func (h *harness) requestOf(t *testing.T, a *AttemptState) []byte {
	t.Helper()
	for _, st := range a.Invocations {
		if st.Invocation.Purpose == invoke.PurposeExecute {
			b, err := h.st.Get(st.Invocation.Request)
			if err != nil {
				t.Fatal(err)
			}
			return b
		}
	}
	t.Fatal("no execute invocation")
	return nil
}

// The design's end-to-end test (§7): a candidate lesson never reaches a
// request; promoted, it does — proven by an Application whose exact
// representation is in the request thought and by the recall selection;
// quarantined, the next run's request hash no longer carries it and no
// Application is written.
func TestRecallReachesTheRequestOnlyWhenSelectable(t *testing.T) {
	h := open(t)
	ir := h.lesson(t, "Always cite the file and line.", learn.Candidate)
	run := func() (*AttemptState, []byte) {
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
		if _, err := d.Run(ctxBg, []byte("What is the capital of France?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		led := h.ledger()
		var newest *RunState
		for _, rs := range led.Runs {
			if newest == nil || rs.Latest().Attempt.Seq > newest.Latest().Attempt.Seq {
				newest = rs
			}
		}
		a := newest.Latest()
		return a, h.requestOf(t, a)
	}
	a1, req1 := run()
	if strings.Contains(string(req1), "cite the file") || len(a1.Recall.Included) != 0 || a1.Recall.ExcludedCounts["stage:candidate"] != 1 {
		t.Fatalf("candidate reached the request: %q %+v", req1, a1.Recall)
	}
	h.stage(t, ir, learn.Candidate, learn.Provisional)
	a2, req2 := run()
	apps := h.ledger().Learned.Applications[a2.Has(Recorded).Outcome.Invocation]
	if len(a2.Recall.Included) != 1 || a2.Recall.Included[0] != ir || len(apps) != 1 || apps[0].Revision != ir.Revision {
		t.Fatalf("promoted lesson not applied: %+v apps=%d", a2.Recall, len(apps))
	}
	rep, err := h.st.Get(apps[0].Representation)
	if err != nil || !bytes.Contains(req2, rep) || !strings.HasPrefix(string(req2), "What is the capital of France?") {
		t.Fatalf("representation %q not in request %q (%v)", rep, req2, err)
	}
	if !strings.Contains(string(rep), "Always cite the file and line.") {
		t.Fatalf("representation %q does not carry the lesson", rep)
	}
	h.stage(t, ir, learn.Provisional, learn.Quarantined)
	a3, req3 := run()
	if bytes.Contains(req3, rep) || len(a3.Recall.Included) != 0 || a3.Recall.ExcludedCounts["stage:quarantined"] != 1 || len(h.ledger().Learned.Applications[a3.Has(Recorded).Outcome.Invocation]) != 0 {
		t.Fatalf("quarantined lesson reached the request: %+v", a3.Recall)
	}
	if bytes.Equal(req2, req3) || !bytes.Equal(req1, req3) {
		t.Fatal("request hashes: the quarantined run must equal the candidate run and differ from the promoted one")
	}
	// the applications are exactly the selection: a forged extra application
	// on the promoted invocation is refused by the fold
	other := h.lesson(t, "another", learn.Provisional)
	inv := a2.Has(Recorded).Outcome.Invocation
	ref, _ := h.st.Put(thought.LessonText, []byte("- another\n"))
	forged := &learn.Application{Header: record.Header{ID: record.NewID(), Schema: "application/1", RunID: a2.Attempt.RunID, Attempt: 1, Subject: record.Ref{Kind: "invocation", ID: string(inv)}, At: time.Now().UTC()}, Item: other.Item, Revision: other.Revision, Invocation: inv, Representation: ref}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged-app", Epoch: h.j.Epoch(), Records: []record.Record{forged}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Fold(h.j.Production(), h.st); err == nil || !strings.Contains(err.Error(), "applications on invocation") {
		t.Fatalf("forged application folded: %v", err)
	}
}

// Re-run identity: two attempts with the same goal, config, applied
// revisions and request are replays of each other; a change in what
// reached the request changes the key.
func TestReplayKey(t *testing.T) {
	h := open(t)
	ir := h.lesson(t, "lesson", learn.Provisional)
	keys := func() []string {
		d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
		if _, err := d.Run(ctxBg, []byte("same goal"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		led := h.ledger()
		var out []string
		ids := []string{}
		for id := range led.Runs {
			ids = append(ids, string(id))
		}
		sort.Strings(ids)
		for _, id := range ids {
			rs := led.Runs[record.RunID(id)]
			k, err := ReplayKey(rs, rs.Latest())
			if err != nil {
				t.Fatal(err)
			}
			out = append(out, k)
		}
		return out
	}
	k := keys()
	k = keys()
	if k[0] != k[1] {
		t.Fatal("identical re-runs have different replay keys")
	}
	h.stage(t, ir, learn.Provisional, learn.Quarantined)
	k = keys()
	if k[2] == k[0] {
		t.Fatal("a different applied set has the same replay key")
	}
}

// A forged Application that names the right revision but the wrong bytes,
// or a request that is not exactly goal+recall, is refused by the fold: the
// exposure proof is re-derived from the goal thought, the selection's
// rendering, and the invocation's request thought.
func TestFoldRefusesForgedExposure(t *testing.T) {
	h := open(t)
	h.lesson(t, "cite the file", learn.Provisional)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	d.CrashAt = "after_execute" // invocation + honest applications landed; nothing recorded yet
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	rs := h.only()
	a := rs.Latest()
	inv := a.Invocations[0]
	record_ := func(t *testing.T, h *harness, rs *RunState, a *AttemptState, inv *invoke.State) error {
		res, err := verdict.Commit(ctxBg, h.j, rs.Run, 1, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: h.verdicts(t, rs.Run)}, verdict.DefaultThresholds)
		if err != nil {
			t.Fatal(err)
		}
		resp := inv.Receipt.Response
		o := &Outcome{Lane: LaneNow, Terminal: inv.Terminal.State, Invocation: inv.Invocation.ID, Produced: 1, Recall: a.Recall.ID, Receipt: inv.Receipt.ID, Response: &resp, Usage: inv.Receipt.Usage, Model: "m", GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence}
		hd := func() record.Header {
			return record.Header{ID: record.NewID(), RunID: rs.Run, Attempt: 1, Subject: runRef(rs.Run), At: time.Now().UTC()}
		}
		if err := forge(t, h, "judged", &Transition{Header: hd(), From: Executing, To: Judged}); err != nil {
			t.Fatalf("judged transition refused: %v", err)
		}
		return forge(t, h, "rec", &Transition{Header: hd(), From: Judged, To: Recorded, Outcome: o})
	}
	// honest: folds
	if err := record_(t, h, rs, a, inv); err != nil {
		t.Fatalf("honest exposure refused: %v", err)
	}
	// forged representation bytes on the right revision
	h2 := open(t)
	ir2 := h2.lesson(t, "cite the file", learn.Provisional)
	d2 := h2.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	d2.CrashAt = "invoke:terminal" // invocation exists, applications NOT yet committed
	if _, err := d2.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, invoke.ErrCrashed) {
		t.Fatal(err)
	}
	h2.restart()
	rs2 := h2.only()
	a2 := rs2.Latest()
	inv2 := a2.Invocations[0]
	ref, _ := h2.st.Put(thought.LessonText, []byte("- something else entirely\n"))
	forgedApp := &learn.Application{Header: record.Header{ID: record.NewID(), RunID: rs2.Run, Attempt: 1, Subject: record.Ref{Kind: "invocation", ID: string(inv2.Invocation.ID)}, At: time.Now().UTC()}, Item: ir2.Item, Revision: ir2.Revision, Invocation: inv2.Invocation.ID, Representation: ref}
	if err := forge(t, h2, "fapp", forgedApp); err != nil {
		t.Fatalf("an application alone must fold (the outcome carries the exposure claim): %v", err)
	}
	// finalize the receipt (reconcile) so an outcome can cite it, then record
	if _, _, err := invoke.Reconcile(ctxBg, &invoke.Shell{J: h2.j, Store: h2.st}); err != nil {
		t.Fatal(err)
	}
	rs2 = h2.only()
	a2 = rs2.Latest()
	inv2 = a2.Invocations[0]
	if err := record_(t, h2, rs2, a2, inv2); err == nil || !strings.Contains(err.Error(), "not the recall's rendering") {
		t.Fatalf("forged representation folded: %v", err)
	}
	// and Resume refuses to "repair" around it: the wrong application is
	// journal evidence the driver could not have written
	d2 = h2.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	if _, err := d2.Resume(ctxBg); !errors.Is(err, ErrIntegrity) && (err == nil || !strings.Contains(err.Error(), "not the recall's rendering")) {
		t.Fatalf("resume papered over forged evidence: %v", err)
	}
}

// A refusal before dispatch keeps the recall it was rendered from, and the
// replay key carries the selection: two refusals with different recalled
// revisions are not the same run.
func TestPreDispatchRefusalKeepsItsRecall(t *testing.T) {
	h := open(t)
	tiny := &invoke.Scripted{Caps: invoke.Capabilities{Name: "tiny", Model: "t", MaxInputBytes: 40}, Calls: []invoke.ScriptedCall{{Response: []byte("x")}}}
	key := func() string {
		d := h.driver(tiny, nil)
		d.Fresh = true // the landscape judge's prompt would not fit the tiny backend either; not this test's subject
		rep, err := d.Run(ctxBg, []byte("short goal"), DeliveryPolicy{Required: TransportAccepted})
		if err != nil || rep.Mission.Outcome != MissionFailedExec {
			t.Fatalf("%v %+v", err, rep)
		}
		led := h.ledger()
		var newest *RunState
		for _, rs := range led.Runs {
			if newest == nil || rs.Latest().Attempt.Seq > newest.Latest().Attempt.Seq {
				newest = rs
			}
		}
		o := newest.Latest().Has(Recorded).Outcome
		if o.Recall == "" || o.Recall != newest.Latest().Recall.ID || o.Invocation != "" {
			t.Fatalf("refusal lost its recall: %+v", o)
		}
		k, err := ReplayKey(newest, newest.Latest())
		if err != nil {
			t.Fatal(err)
		}
		return k
	}
	ir := h.lesson(t, strings.Repeat("a long lesson that overflows the tiny backend ", 3), learn.Provisional)
	k1 := key()
	h.stage(t, ir, learn.Provisional, learn.Quarantined)
	h.lesson(t, strings.Repeat("another long lesson that overflows the tiny backend ", 3), learn.Provisional)
	k2 := key()
	if k1 == k2 {
		t.Fatal("refusals with different recalled revisions share a replay key")
	}
}

// A crash after the recall decision, before any request: the next attempt
// CONTINUES the committed selection rather than deciding again, even when
// the ledger moved in between.
func TestRecoveredAttemptContinuesItsSelection(t *testing.T) {
	h := open(t)
	ir := h.lesson(t, "decided before the crash", learn.Provisional)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	d.CrashAt = "after_recall"
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	h.stage(t, ir, learn.Provisional, learn.Quarantined) // the ledger moves while the process is down
	h.restart()
	d = h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	if _, err := d.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	a := rs.Latest()
	if a.Attempt.Attempt != 2 || a.Recall == nil || a.Recall.Continues != rs.Attempts[0].Recall.ID || len(a.Recall.Included) != 1 || a.Recall.Included[0] != ir {
		t.Fatalf("attempt 2 did not continue attempt 1's selection: %+v", a.Recall)
	}
	if !bytes.Contains(h.requestOf(t, a), []byte("decided before the crash")) {
		t.Fatal("the continued selection was not rendered into the request")
	}
	if apps := h.ledger().Learned.Applications[a.Has(Recorded).Outcome.Invocation]; len(apps) != 1 || apps[0].Revision != ir.Revision {
		t.Fatalf("applications: %d", len(apps))
	}
}

// Scope: a lesson scoped to one goal is recalled for that goal only; a
// policy item at effective is never injected by the driver.
func TestScopeAndPolicyAreHonored(t *testing.T) {
	h := open(t)
	// first run makes a goal we can scope to
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	rep, err := d.Run(ctxBg, []byte("first?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	ref, _ := h.st.Put(thought.LessonText, []byte("only for the first goal"))
	item := learn.LearnedID(record.NewID())
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeGoal(rep.Goal), Text: ref, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	pitem := learn.LearnedID(record.NewID())
	p := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(pitem)}, At: time.Now().UTC()}, Item: pitem, LearnedKind: learn.Policy, Scope: learn.ScopeWorkspace, Policy: &learn.PolicyRule{Mechanism: learn.MechModelJudge, Enabled: true}, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "scoped", Epoch: h.j.Epoch(), Records: []record.Record{r, p}}); err != nil {
		t.Fatal(err)
	}
	h.stage(t, learn.ItemRev{Item: item, Revision: r.ID}, learn.Candidate, learn.Provisional)
	h.stage(t, learn.ItemRev{Item: pitem, Revision: p.ID}, learn.Candidate, learn.Effective)
	d = h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("ok")}), nil)
	if _, err := d.Run(ctxBg, []byte("second?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	led := h.ledger()
	var second *RunState
	for _, rs := range led.Runs {
		a := rs.Latest()
		req := h.requestOf(t, a)
		if bytes.Contains(req, []byte("only for the first goal")) || bytes.Contains(req, []byte("judge twice")) {
			t.Fatalf("scoped lesson or policy reached a request it must not: %q", req)
		}
		if second == nil || a.Attempt.Seq > second.Latest().Attempt.Seq {
			second = rs
		}
	}
	if c := second.Latest().Recall.ExcludedCounts; c["kind:policy"] != 3 || c["scope"] != 1 { // the two seeds and the test policy
		t.Fatalf("policy/scope not excluded as such: %+v", c)
	}
	// the goal-scoped lesson IS recalled for a resumed attempt of its own goal:
	// re-run the first goal through an unstarted-goal path is not possible;
	// instead check the query directly over the fold
	sel := learn.Recall(led.Learned, learn.Query{Purpose: "execute", Scope: scope(led.Runs[record.RunID(rep.Run)]), Family: "answer", Standing: learn.Selectable})
	if len(sel.Included) != 1 || sel.Included[0].Item != item {
		t.Fatalf("goal-scoped lesson not selected for its goal: %+v", sel)
	}
}

// The real subprocess adapter receives the composed request: a fake CLI
// captures its stdin, which must equal the invocation's request thought —
// goal bytes plus the rendered recall block.
func TestSubprocessReceivesGoalAndRecall(t *testing.T) {
	h := open(t)
	h.lesson(t, "Answer in one word.", learn.Provisional)
	dir := t.TempDir()
	capture := filepath.Join(dir, "stdin.txt")
	bin := filepath.Join(dir, "fake")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\ncat > "+capture+"\necho '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"result\":\"PARIS\",\"total_cost_usd\":0.0001,\"usage\":{\"input_tokens\":1,\"output_tokens\":1}}'\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	b := &invoke.Subprocess{Bin: bin, Model: "fake", DefaultTimeout: 10 * time.Second}
	d := h.driver(b, nil)
	rep, err := d.Run(ctxBg, []byte("Capital of France?"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil || string(rep.Payload) != "PARIS" {
		t.Fatalf("%v %q", err, rep.Payload)
	}
	got, err := os.ReadFile(capture)
	if err != nil {
		t.Fatal(err)
	}
	want := h.requestOf(t, h.only().Latest())
	if !bytes.Equal(got, want) || !bytes.HasPrefix(got, []byte("Capital of France?\n\n## Recalled lessons\n- Answer in one word.\n")) {
		t.Fatalf("subprocess stdin:\n%q\nrequest thought:\n%q", got, want)
	}
}

func (h *harness) policy(t *testing.T, mech learn.Mechanism, enabled bool, stage learn.Stage) learn.ItemRev {
	t.Helper()
	item := learn.LearnedID(record.NewID())
	r := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, LearnedKind: learn.Policy, Scope: learn.ScopeWorkspace, Policy: &learn.PolicyRule{Mechanism: mech, Enabled: enabled}, Provenance: learn.Provenance{Source: "operator", Why: "test"}}
	recs := []record.Record{r}
	if stage != learn.Candidate {
		recs = append(recs, &learn.LifecycleTransition{Header: record.Header{ID: record.NewID(), Schema: "learned_transition/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()}, Item: item, Revision: r.ID, From: learn.Candidate, To: stage, Actor: learn.ActorOperator, Why: "test"})
	}
	if _, err := h.j.Submit(ctxBg, journal.Command{IdempotencyKey: "policy/" + string(item), Epoch: h.j.Epoch(), Records: recs}); err != nil {
		t.Fatal(err)
	}
	return learn.ItemRev{Item: item, Revision: r.ID}
}

// The policy boundary (§7, D17): mechanisms are data the driver reads at
// the start of an attempt. A selectable `recall=off` policy makes the
// request the goal alone (no application, the recall selection says why),
// with a policy_application as the proof; a candidate policy changes
// nothing. A selectable `model_judge=off` policy runs AGENDA's judgments
// on the executor backend, and the attempt's snapshot says so. A config
// snapshot that disagrees with its policy selection is refused by the
// fold.
func TestPolicyBoundaryIsConsumed(t *testing.T) {
	h := open(t)
	h.lesson(t, "Cite sources.", learn.Effective)
	off := h.policy(t, learn.MechRecall, false, learn.Candidate)
	d := h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("42")}), nil)
	if _, err := d.Run(ctxBg, []byte("q?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if !bytes.Contains(h.requestOf(t, a), []byte("Cite sources.")) || len(a.Recall.Included) != 1 || len(a.Policy.Considered) != 3 || len(a.Policy.Enabled) != 2 || !a.Attempt.Config.Mechanisms[learn.MechRecall] || len(a.Policy.Excluded) != 1 || a.Policy.Excluded[0].Revision != off.Revision {
		t.Fatalf("candidate policy changed the attempt: recall %+v policy %+v", a.Recall, a.Policy)
	}
	h.stage(t, off, learn.Candidate, learn.Provisional)
	d = h.driver(scripted(toolless, invoke.ScriptedCall{Response: []byte("42")}), nil)
	if _, err := d.Run(ctxBg, []byte("q again?"), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	led := h.ledger()
	var rs *RunState
	for _, r := range led.Runs {
		if rs == nil || r.Goal.Seq > rs.Goal.Seq {
			rs = r
		}
	}
	a = rs.Latest()
	if got := h.requestOf(t, a); string(got) != "q again?" {
		t.Fatalf("request under recall=off: %q", got)
	}
	if a.Attempt.Config.Mechanisms[learn.MechRecall] || a.Attempt.Config.Policy != a.Policy.ID || len(a.Policy.Enabled) != 3 || a.Policy.Enabled[2] != off {
		t.Fatalf("config %+v policy %+v", a.Attempt.Config, a.Policy)
	}
	if len(a.Recall.Included) != 0 || a.Recall.ExcludedCounts["policy:recall_off"] != 4 || a.Recall.Policy != a.Policy.ID { // lesson, off, two seeds
		t.Fatalf("recall under recall=off: %+v", a.Recall)
	}
	if apps := led.Learned.PolicyApps[a.Policy.ID]; len(apps) != 3 || apps[2].Revision != off.Revision || apps[2].Rule.Enabled || apps[2].Rule.Mechanism != learn.MechRecall {
		t.Fatalf("policy application: %+v", apps)
	}
	for _, st := range a.Invocations {
		if len(led.Learned.Applications[st.Invocation.ID]) != 0 {
			t.Fatalf("an application under recall=off: %+v", led.Learned.Applications[st.Invocation.ID])
		}
	}
	// model_judge=off: every AGENDA call runs on the executor, in order
	h2 := open(t)
	h2.policy(t, learn.MechModelJudge, false, learn.Provisional)
	exec, judge := agendaBackends([]string{intentClear, planTwo, "Collected 12 rows", judgeDone, "Summary written", judgeDone, closureYes}, nil)
	d2 := h2.agenda(exec, judge)
	rep, err := d2.Run(ctxBg, []byte("Summarize the quarterly numbers into a short report"), DeliveryPolicy{Required: TransportAccepted})
	if err != nil {
		t.Fatal(err)
	}
	a2 := h2.only().Latest()
	if rep.Mission.Closure != "achieved" || len(exec.Seen) != 7 || len(judge.Seen) != 0 || a2.Attempt.Config.Mechanisms[learn.MechModelJudge] || a2.Attempt.Config.JudgeBackend.Name != "scripted-exec" || a2.Attempt.Config.Judge != JudgeModel {
		t.Fatalf("model_judge=off: exec %d judge %d config %+v", len(exec.Seen), len(judge.Seen), a2.Attempt.Config)
	}
	for _, st := range a2.Invocations {
		if st.Invocation.Backend.Name != "scripted-exec" || st.Invocation.Tools {
			t.Fatalf("invocation %s ran on %s tools=%v", st.Invocation.Purpose, st.Invocation.Backend.Name, st.Invocation.Tools)
		}
	}
	// forged: an attempt whose config disagrees with its policy selection
	h3 := open(t)
	if _, err := learn.EnsureSeeds(ctxBg, h3.j); err != nil {
		t.Fatal(err)
	}
	ref, _ := h3.st.Put(thought.Goal, []byte("q?"))
	goal, fam := Intake([]byte("q?"), ref, OriginCLI, LaneNow, DeliveryPolicy{Required: TransportAccepted})
	if _, err := h3.j.Submit(ctxBg, journal.Command{IdempotencyKey: "goal", Epoch: h3.j.Epoch(), Records: []record.Record{goal, fam}}); err != nil {
		t.Fatal(err)
	}
	run := record.RunID(record.NewID())
	lled, _ := learn.Fold(h3.j.Production())
	rs0 := &RunState{Run: run, Goal: goal, Root: goal.ID}
	pol := learn.SelectPolicy(lled, learn.Query{Scope: scope(rs0), Standing: learn.Selectable})
	pol.Header = header(runRef(run), run, 1, "policy_selection/1")
	ls := &Landscape{Header: header(runRef(run), run, 0, "landscape/1"), Goal: goal.ID, AsOf: h3.j.Head(), Rule: LandscapeNoCandidates, Floor: LandscapeFloor, TopK: LandscapeTopK, Relation: RelationFresh}
	d3 := h3.driver(scripted(toolless), nil)
	d3.validate()
	cfg, _ := d3.config(LaneNow, pol)
	cfg.Mechanisms[learn.MechRecall] = false // the snapshot says on
	att := &RunAttempt{Header: header(runRef(run), run, 1, "run_attempt/1"), Goal: goal.ID, Family: fam.ID, Config: cfg}
	recs := []record.Record{ls, pol}
	for i, rule := range lled.PolicyRules(pol) { // the seeds' applications, so the fold reaches the config
		recs = append(recs, &learn.PolicyApplication{Header: header(record.Ref{Kind: "policy_selection", ID: string(pol.ID)}, run, 1, "policy_application/1"), Item: pol.Enabled[i].Item, Revision: pol.Enabled[i].Revision, Selection: pol.ID, Rule: rule})
	}
	recs = append(recs, att, &Transition{Header: header(runRef(run), run, 1, "run_transition/1"), To: Created})
	if _, err := h3.j.Submit(ctxBg, journal.Command{IdempotencyKey: "forged", Epoch: h3.j.Epoch(), Records: recs}); err != nil {
		t.Fatal(err)
	}
	if _, err := Fold(h3.j.Production(), h3.st); err == nil || !strings.Contains(err.Error(), "config says recall=false") {
		t.Fatalf("forged config folded: %v", err)
	}
}
