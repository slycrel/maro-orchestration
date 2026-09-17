package run

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

const (
	deadLink = "https://account.example/security"
	liveLink = "https://docs.example/mail"
)

// probe is the test's link probe: the dead link 404s, everything else
// resolves; it counts what it was asked.
func probe(asked *[]string) func(context.Context, string) string {
	return func(_ context.Context, u string) string {
		*asked = append(*asked, u)
		if u == deadLink {
			return "returns HTTP 404"
		}
		return ""
	}
}

func writeAsk(t *testing.T, path, body string) func(invoke.Request) {
	return func(invoke.Request) {
		if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

const (
	askBad   = `{"question":"Open ` + deadLink + ` and give me the 6-digit code from your phone.","why":"the login challenge wants it","no_input_alternative":"tried the app password; the store has none (see ` + liveLink + `)","tried":true}`
	askGood  = `{"question":"What is the 6-digit code Yahoo just texted you?","why":"the login challenge wants it","no_input_alternative":"tried the app password; the store has none","tried":true,"sent":"chose Text me on the challenge page; Yahoo showed: code sent to ***-1234"}`
	askPlain = `{"question":"Which of the two mailboxes is yours?","why":"both match","tried":true}`
)

// The checks themselves: links are found in every field once, trailing
// punctuation dropped; a code request is recognised by the Python
// engine's words; the gate bounces a dead link and a code request with
// no `sent`, and notes the lane on every code request; at most five
// links are probed; the bounce block names every problem.
func TestGroundingChecks(t *testing.T) {
	ask := &Ask{Question: "Open " + deadLink + ". and give me the 6-digit code", Why: "see " + liveLink + ", then " + deadLink, Sent: "(" + liveLink + ")"}
	if got := linksOf(ask); len(got) != 2 || got[0] != deadLink || got[1] != liveLink {
		t.Fatalf("links: %v", got)
	}
	for q, want := range map[string]bool{
		"What is the 6-digit code Yahoo texted you?": true,
		"give me the OTP":                      false, // no "code": the Python rule's second half
		"give me the OTP code":                 true,
		"the verification code, please":        true,
		"enter your 2FA code":                  true,
		"which is the source code repository?": false,
		"the passcode for the door":            true,
		"the one-time link":                    false,
		"Which mailbox is yours?":              false,
	} {
		if asksForCode(q) != want {
			t.Fatalf("asksForCode(%q) = %v", q, !want)
		}
	}
	var asked []string
	d := &Driver{ProbeURL: probe(&asked)}
	ask.Sent = "" // a code request that does not say how it was sent
	hard, soft := d.ground(ctxBg, ask)
	if len(hard) != 2 || hard[0].Check != CheckLink || hard[0].Link != deadLink || hard[1].Check != CheckCodeUnsent || len(soft) != 1 || soft[0].Check != CheckCodeLane {
		t.Fatalf("hard=%+v soft=%+v", hard, soft)
	}
	if len(asked) != 2 {
		t.Fatalf("probed %v", asked)
	}
	// with `sent`: the code request is not bounced, the lane note stays
	ask.Sent = "chose Text me; Yahoo showed: code sent"
	ask.Question = "give me the 6-digit code"
	if hard, soft = d.ground(ctxBg, ask); len(hard) != 1 || hard[0].Check != CheckLink || len(soft) != 1 {
		t.Fatalf("with sent: hard=%+v soft=%+v", hard, soft)
	}
	// a plain question with live links bounces nothing and notes nothing
	if hard, soft = d.ground(ctxBg, &Ask{Question: "Which mailbox? see " + liveLink}); len(hard) != 0 || len(soft) != 0 {
		t.Fatalf("plain: hard=%+v soft=%+v", hard, soft)
	}
	// six links: five probed
	asked = nil
	many := &Ask{Question: "a https://a.example/1 https://a.example/2 https://a.example/3 https://a.example/4 https://a.example/5 https://a.example/6"}
	d.ground(ctxBg, many)
	if len(asked) != linkProbeMax {
		t.Fatalf("probed %d links: %v", len(asked), asked)
	}
	b := &QuestionBounce{Problems: []AskProblem{{Check: CheckLink, Link: deadLink, Detail: "the link is dead"}, {Check: CheckCodeUnsent, Detail: "no sent"}}}
	if blk := string(bounceBlock(b)); !strings.HasPrefix(blk, "\n\n## Your question to the operator was NOT sent\n") || !strings.Contains(blk, "- the link is dead\n- no sent\n") || !strings.Contains(blk, "This is the one re-run") {
		t.Fatalf("block %q", blk)
	}
	if bounceBlock(nil) != nil {
		t.Fatal("no bounce, no block")
	}
	if !strings.Contains(AskInstructions("/x/ask.json"), "every link in it must resolve") || !strings.Contains(AskInstructions("/x"), `say in "sent" how YOU triggered its delivery`) {
		t.Fatal("the frame must state both rules")
	}
	// two archives in one second are two files
	dir := t.TempDir()
	p := filepath.Join(dir, AskName)
	os.WriteFile(p, []byte(askBad), 0o600)
	first, err := ArchiveAsk(p)
	if err != nil {
		t.Fatal(err)
	}
	os.WriteFile(p, []byte(askGood), 0o600)
	second, err := ArchiveAsk(p)
	if err != nil || second == first {
		t.Fatalf("second archive %q (first %q): %v", second, first, err)
	}
	if b1, _ := os.ReadFile(first); !strings.Contains(string(b1), deadLink) {
		t.Fatal("the first archive was overwritten")
	}
}

// A NOW worker whose ask fails the gate is bounced: the execute runs once
// more with the problems at the top of its context, the bounce is a
// record naming the consumed call, both ask files are archived, and the
// question the re-run writes ends the attempt carrying what could not be
// verified (the lane note) with `sent`; the reason says so; the journal
// folds again from disk. A second failure passes through unverified.
func TestBouncedAskRunsTheStepOnceMore(t *testing.T) {
	setup := func(t *testing.T, second string) (*harness, *invoke.Scripted, string) {
		h := open(t)
		dir := t.TempDir()
		askPath := filepath.Join(dir, AskName)
		exec := scripted(outward,
			invoke.ScriptedCall{Response: []byte("asked the operator for the code"), Do: writeAsk(t, askPath, askBad)},
			invoke.ScriptedCall{Response: []byte("asked again"), Do: writeAsk(t, askPath, second)})
		d := h.driver(exec, nil)
		var asked []string
		d.AskPath, d.ProbeURL = askPath, probe(&asked)
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
			t.Fatal(err)
		}
		return h, exec, dir
	}
	h, exec, dir := setup(t, askGood)
	rs := h.only()
	a := rs.Latest()
	if len(exec.Seen) != 2 || len(a.Bounces) != 1 || a.Question == nil {
		t.Fatalf("calls=%d bounces=%d question=%v", len(exec.Seen), len(a.Bounces), a.Question)
	}
	var execs []*invoke.State
	for _, is := range a.Invocations {
		if is.Invocation.Purpose == invoke.PurposeExecute {
			execs = append(execs, is)
		}
	}
	if len(execs) != 2 {
		t.Fatalf("execute invocations: %d", len(execs))
	}
	b := a.Bounces[0]
	if b.Step != 0 || b.Invocation != execs[0].Invocation.ID || len(b.Problems) != 2 || b.Problems[0].Check != CheckLink || b.Problems[0].Link != deadLink || b.Problems[1].Check != CheckCodeUnsent || b.Ask.Question != "Open "+deadLink+" and give me the 6-digit code from your phone." {
		t.Fatalf("bounce %+v", b)
	}
	q := a.Question
	if q.Invocation != execs[1].Invocation.ID || q.Sent == "" || len(q.Unverified) != 1 || q.Unverified[0].Check != CheckCodeLane || q.Question != "What is the 6-digit code Yahoo just texted you?" {
		t.Fatalf("question %+v", q)
	}
	first, second := string(exec.Seen[0].Prompt), string(exec.Seen[1].Prompt)
	if strings.Contains(first, "NOT sent") || !strings.Contains(second, "## Your question to the operator was NOT sent") || !strings.Contains(second, "the link "+deadLink+" returns HTTP 404") || !strings.Contains(second, `put the confirmation you saw in "sent"`) {
		t.Fatalf("prompts:\n%s\n----\n%s", first, second)
	}
	if !strings.HasPrefix(second, strings.SplitN(first, goalFlaky, 2)[0]+goalFlaky+"\n\n## Your question") {
		t.Fatalf("the bounce is not at the top of the re-run's context:\n%s", second)
	}
	rec := a.Has(Recorded)
	if rec == nil || rec.Outcome.Invocation != execs[1].Invocation.ID || !strings.HasPrefix(rec.Outcome.Reason, "needs answer: What is the 6-digit code Yahoo just texted you? [unverified: a code is consumed by the session") {
		t.Fatalf("outcome %+v", rec)
	}
	if !Stopped(rs) {
		t.Fatal("a run that ended on a question is stopped")
	}
	archived, _ := filepath.Glob(filepath.Join(dir, "ask-operator.*.asked.json"))
	if len(archived) != 2 {
		t.Fatalf("archived %v", archived)
	}
	if _, err := os.Stat(filepath.Join(dir, AskName)); err == nil {
		t.Fatal("the ask file was not archived")
	}
	var stages []string
	for _, e := range h.events {
		if e.Stage == "ask_bounced" || e.Stage == "ask" {
			stages = append(stages, e.Stage)
		}
	}
	if strings.Join(stages, ",") != "ask_bounced,ask" {
		t.Fatalf("events %v", stages)
	}
	h.restart()
	rs2 := h.only()
	if len(rs2.Latest().Bounces) != 1 || rs2.Latest().Question == nil || rs2.Latest().Question.ID != q.ID || !Bounced(rs2.Latest(), 0) {
		t.Fatal("the bounce and the question did not fold again from disk")
	}
	if s := strings.Join(Inspect(rs2), "\n"); !strings.Contains(s, "unverified: a code is consumed") || !strings.Contains(s, "bounced once") || !strings.Contains(s, "sent: chose Text me") {
		t.Fatalf("inspect:\n%s", s)
	}
	// the second failure passes through: the same problems, now the
	// operator's to read
	h, exec, _ = setup(t, askBad)
	a = h.only().Latest()
	if len(exec.Seen) != 2 || len(a.Bounces) != 1 || a.Question == nil {
		t.Fatalf("calls=%d bounces=%d question=%v", len(exec.Seen), len(a.Bounces), a.Question)
	}
	if u := a.Question.Unverified; len(u) != 3 || u[0].Check != CheckLink || u[1].Check != CheckCodeUnsent || u[2].Check != CheckCodeLane {
		t.Fatalf("unverified %+v", u)
	}
	if r := a.Has(Recorded).Outcome.Reason; !strings.Contains(r, "[unverified: the link "+deadLink+" returns HTTP 404") {
		t.Fatalf("reason %q", r)
	}
	h.restart()
	if q := h.only().Latest().Question; q == nil || len(q.Unverified) != 3 {
		t.Fatal("the pass-through did not fold again from disk")
	}
	// a plain question is neither bounced nor annotated
	h = open(t)
	askPath := filepath.Join(t.TempDir(), AskName)
	exec = scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, askPlain)})
	d := h.driver(exec, nil)
	d.AskPath = askPath
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a = h.only().Latest()
	if len(exec.Seen) != 1 || len(a.Bounces) != 0 || a.Question == nil || len(a.Question.Unverified) != 0 || a.Has(Recorded).Outcome.Reason != "needs answer: Which of the two mailboxes is yours?" {
		t.Fatalf("plain: calls=%d bounces=%d question=%+v", len(exec.Seen), len(a.Bounces), a.Question)
	}
}

// An AGENDA step's ask is grounded before the step is judged: the bounce
// re-runs the step with the block after "Your step", the step record
// cites the re-run (never the bounced call), the re-run's question ends
// the attempt, and the fold re-derives both requests.
func TestBouncedAskRerunsTheAgendaStep(t *testing.T) {
	h := open(t)
	askPath := filepath.Join(t.TempDir(), AskName)
	exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{
		{Response: []byte("Collected 12 rows")},
		{Response: []byte("asked"), Do: writeAsk(t, askPath, askBad)},
		{Response: []byte("asked again"), Do: writeAsk(t, askPath, askGood)},
	}}
	_, judge := agendaBackends(nil, []string{intentClear, planTwo, judgeDone, judgeDone})
	d := h.agenda(exec, judge)
	var asked []string
	d.AskPath, d.ProbeURL, d.Frame = askPath, probe(&asked), agendaFrame
	if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	// every execute request begins with the attempt's frame (the ask
	// instructions live there; review r1: AGENDA workers never saw them)
	for i, c := range exec.Seen {
		if !strings.HasPrefix(string(c.Prompt), agendaFrame+"\n\n") {
			t.Fatalf("execute %d does not begin with the frame:\n%s", i, c.Prompt)
		}
	}
	rs := h.only()
	a := rs.Latest()
	var execs []*invoke.State
	for _, is := range a.Invocations {
		if is.Invocation.Purpose == invoke.PurposeExecute {
			execs = append(execs, is)
		}
	}
	if len(exec.Seen) != 3 || len(execs) != 3 || len(a.Bounces) != 1 || a.Question == nil || len(a.Steps) != 2 {
		t.Fatalf("calls=%d execs=%d bounces=%d question=%v steps=%d", len(exec.Seen), len(execs), len(a.Bounces), a.Question, len(a.Steps))
	}
	if b := a.Bounces[0]; b.Step != 2 || b.Invocation != execs[1].Invocation.ID {
		t.Fatalf("bounce %+v", b)
	}
	if sd := a.Steps[1]; sd.Ordinal != 2 || sd.Invocation != execs[2].Invocation.ID || sd.Outcome != StepDoneOK {
		t.Fatalf("step 2 %+v", sd)
	}
	if q := a.Question; q.Step != 2 || q.Invocation != execs[2].Invocation.ID || len(q.Unverified) != 1 || q.Unverified[0].Check != CheckCodeLane {
		t.Fatalf("question %+v", q)
	}
	third := string(exec.Seen[2].Prompt)
	if i, j := strings.Index(third, "## Your step (2 of 2)"), strings.Index(third, "## Your question to the operator was NOT sent"); i < 0 || j < i || strings.Contains(string(exec.Seen[1].Prompt), "NOT sent") {
		t.Fatalf("re-run prompt:\n%s", third)
	}
	if r := a.Has(Recorded).Outcome.Reason; !strings.HasPrefix(r, "needs answer: What is the 6-digit code") {
		t.Fatalf("reason %q", r)
	}
	h.restart()
	if a2 := h.only().Latest(); len(a2.Bounces) != 1 || a2.Question == nil || len(a2.Steps) != 2 {
		t.Fatal("did not fold again from disk")
	}
}

// Recovery: a bounced call is consumed. A crash after the bounce resumes
// with the step run again (the bounced call is not reused; the new
// attempt's call carries no bounce — a bounce is its attempt's); a crash
// after the question is committed resumes to the question the journal
// holds, not to the archived file.
func TestBouncedCallIsNotReusedOnResume(t *testing.T) {
	h := open(t)
	askPath := filepath.Join(t.TempDir(), AskName)
	exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, askBad)})
	d := h.driver(exec, nil)
	var asked []string
	d.AskPath, d.ProbeURL, d.CrashAt = askPath, probe(&asked), "after_bounce"
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	if a := h.only().Latest(); len(a.Bounces) != 1 || a.Question != nil {
		t.Fatalf("after the crash: bounces=%d question=%v", len(a.Bounces), a.Question)
	}
	h.restart()
	exec2 := scripted(outward, invoke.ScriptedCall{Response: []byte("asked well"), Do: writeAsk(t, askPath, askGood)})
	dr := h.driver(exec2, nil)
	dr.AskPath, dr.ProbeURL = askPath, probe(&asked)
	if _, err := dr.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	rs := h.only()
	if len(rs.Attempts) != 2 || len(exec2.Seen) != 1 || strings.Contains(string(exec2.Seen[0].Prompt), "NOT sent") {
		t.Fatalf("attempts=%d calls=%d calls=%+v", len(rs.Attempts), len(exec2.Seen), exec2.Seen)
	}
	a2 := rs.Attempts[1]
	if a2.Question == nil || len(a2.Bounces) != 0 || rs.bounced(a2.Question.Invocation) || !strings.HasPrefix(a2.Has(Recorded).Outcome.Reason, "needs answer: What is the 6-digit code") {
		t.Fatalf("attempt 2: question=%+v bounces=%d", a2.Question, len(a2.Bounces))
	}
	// the question is the journal's: a crash between the question and the
	// outcome resumes to "needs answer", with the call reused
	h = open(t)
	askPath = filepath.Join(t.TempDir(), AskName)
	exec = scripted(outward, invoke.ScriptedCall{Response: []byte("asked well"), Do: writeAsk(t, askPath, askGood)})
	d = h.driver(exec, nil)
	d.AskPath, d.CrashAt = askPath, "after_execute"
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
		t.Fatal(err)
	}
	if _, err := os.Stat(askPath); err == nil {
		t.Fatal("the ask file was not archived before the crash")
	}
	h.restart()
	exec2 = scripted(outward, okCall)
	dr = h.driver(exec2, nil)
	dr.AskPath = askPath
	if _, err := dr.Resume(ctxBg); err != nil {
		t.Fatal(err)
	}
	rs = h.only()
	if len(exec2.Seen) != 0 || len(rs.Attempts) != 2 || rs.Attempts[0].Question == nil || rs.Attempts[1].Question != nil {
		t.Fatalf("resume: calls=%d attempts=%d", len(exec2.Seen), len(rs.Attempts))
	}
	if r := rs.Attempts[1].Has(Recorded).Outcome.Reason; !strings.HasPrefix(r, "needs answer: What is the 6-digit code") {
		t.Fatalf("resumed reason %q", r)
	}
}

// Must-detect fixtures: histories the driver cannot write, refused with
// the reason named — a bounce on an attempt that asked, a second bounce
// of a step, a bounce citing a call that is not a landed execute, a
// question citing the bounced call, unverified problems with no bounce
// before them, a code request with no `sent` passed silently, a lane note
// that disagrees with the question, a question that names no invocation
// after the journal shows grounded ones — and the door's own vocabulary.
func TestForgedBounceIsRefused(t *testing.T) {
	// executing attempt with one landed execute whose worker wrote an ask
	// the driver never read (crash before the read)
	mk := func(t *testing.T, body string) (*harness, *RunState, *invoke.State) {
		h := open(t)
		askPath := filepath.Join(t.TempDir(), AskName)
		exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, body)})
		d := h.driver(exec, nil)
		d.AskPath, d.CrashAt = askPath, "after_applications"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		return h, rs, rs.Latest().Invocations[0]
	}
	bad := Ask{Question: "Open " + deadLink + " and give me the 6-digit code from your phone.", Tried: true}
	linkProblem := AskProblem{Check: CheckLink, Link: deadLink, Detail: "the link " + deadLink + " returns HTTP 404"}
	codeProblem := AskProblem{Check: CheckCodeUnsent, Detail: codeUnsentDetail}
	laneProblem := AskProblem{Check: CheckCodeLane, Detail: codeLaneDetail}
	bounce := func(rs *RunState, inv record.RecordID, ask Ask, ps ...AskProblem) *QuestionBounce {
		return &QuestionBounce{Header: header(runRef(rs.Run), rs.Run, 1, "question_bounce/1"), Invocation: inv, Ask: ask, Problems: ps}
	}
	question := func(rs *RunState, inv record.RecordID, ask Ask, ps ...AskProblem) *Question {
		return &Question{Header: header(runRef(rs.Run), rs.Run, 1, "question/1"), Invocation: inv, Question: ask.Question, Tried: ask.Tried, Sent: ask.Sent, Unverified: ps, Deadline: now().Add(AskTimebox)}
	}
	// one history per forgery (pattern 137): a refused record stays in the
	// journal and refuses every fold after it
	t.Run("control: the honest bounce folds", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g1", bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)); err != nil {
			t.Fatal(err)
		}
	})
	t.Run("a second bounce of the step", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g1", bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h, "forge/g1b", bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)); err == nil || !strings.Contains(err.Error(), "bounced step 0 twice") {
			t.Fatalf("second bounce: %v", err)
		}
	})
	t.Run("a question citing the bounced call", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g1", bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h, "forge/g1c", question(rs, is.Invocation.ID, bad, linkProblem, codeProblem, laneProblem)); err == nil || !strings.Contains(err.Error(), "which the gate bounced") {
			t.Fatalf("question on the bounced call: %v", err)
		}
	})
	t.Run("a bounce citing a call that is not a landed execute", func(t *testing.T) {
		h, rs, _ := mk(t, askBad)
		if err := forge(t, h, "forge/g2", bounce(rs, record.NewID(), bad, linkProblem, codeProblem)); err == nil || !strings.Contains(err.Error(), "not a landed execute") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a bounce that leaves out the code request with no sent", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g3", bounce(rs, is.Invocation.ID, bad, linkProblem)); err == nil || !strings.Contains(err.Error(), "leaves out the code request") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a bounce on an attempt that asked", func(t *testing.T) {
		h, rs, is := mk(t, askPlain)
		if err := forge(t, h, "forge/g4", question(rs, is.Invocation.ID, Ask{Question: "Which of the two mailboxes is yours?", Tried: true})); err != nil {
			t.Fatal(err)
		}
		if err := forge(t, h, "forge/g4b", bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)); err == nil || !strings.Contains(err.Error(), "bounce out of place") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("unverified problems with no bounce before them", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g5", question(rs, is.Invocation.ID, bad, linkProblem, codeProblem, laneProblem)); err == nil || !strings.Contains(err.Error(), "passes a link problem through with no bounce") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a code request with no sent passed silently", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		if err := forge(t, h, "forge/g6", question(rs, is.Invocation.ID, bad, laneProblem)); err == nil || !strings.Contains(err.Error(), "asks for a code with no `sent` and does not say so") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a lane note that disagrees with the question", func(t *testing.T) {
		good := Ask{Question: "What is the 6-digit code Yahoo just texted you?", Tried: true, Sent: "chose Text me"}
		h, rs, is := mk(t, askGood)
		if err := forge(t, h, "forge/g7", question(rs, is.Invocation.ID, good)); err == nil || !strings.Contains(err.Error(), "lane note disagrees") {
			t.Fatalf("missing note: %v", err)
		}
		h, rs, is = mk(t, askGood)
		if err := forge(t, h, "forge/g7b", question(rs, is.Invocation.ID, good, laneProblem)); err != nil {
			t.Fatalf("the honest question: %v", err)
		}
	})
	t.Run("a question that names no invocation after the journal shows grounded ones", func(t *testing.T) {
		h, rs, is := mk(t, askPlain)
		if err := forge(t, h, "forge/g8", question(rs, is.Invocation.ID, Ask{Question: "Which of the two mailboxes is yours?", Tried: true})); err != nil {
			t.Fatal(err)
		}
		// a second run, crashed the same way, asks the old way
		askPath := filepath.Join(t.TempDir(), AskName)
		exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, askPlain)})
		d := h.driver(exec, nil)
		d.AskPath, d.CrashAt = askPath, "after_applications"
		if _, err := d.Run(ctxBg, []byte(goalFlaky+" again"), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		var rs2 *RunState
		for _, r := range h.ledger().Runs {
			if r.Run != rs.Run {
				rs2 = r
			}
		}
		old := question(rs2, "", Ask{Question: "Which of the two mailboxes is yours?", Tried: true})
		if err := forge(t, h, "forge/g8b", old); err == nil || !strings.Contains(err.Error(), "names no invocation after the journal shows grounded ones") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a bounce naming a step the attempt does not have", func(t *testing.T) {
		h, rs, is := mk(t, askBad)
		b := bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)
		b.Step = 1
		if err := forge(t, h, "forge/g9", b); err == nil || !strings.Contains(err.Error(), "not a step of the attempt") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a question naming a step the attempt does not have", func(t *testing.T) {
		h, rs, is := mk(t, askGood)
		q := question(rs, is.Invocation.ID, Ask{Question: "What is the 6-digit code Yahoo just texted you?", Tried: true, Sent: "chose Text me"}, laneProblem)
		q.Step = 1
		if err := forge(t, h, "forge/g10", q); err == nil || !strings.Contains(err.Error(), "not a step of the attempt") {
			t.Fatalf("err=%v", err)
		}
	})
	t.Run("a bounce naming a step an AGENDA attempt does not have", func(t *testing.T) {
		for _, step := range []int{0, 3} {
			h := open(t)
			askPath := filepath.Join(t.TempDir(), AskName)
			exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("asked"), Do: writeAsk(t, askPath, askBad)}}}
			_, judge := agendaBackends(nil, []string{intentClear, planTwo})
			d := h.agenda(exec, judge)
			d.CrashAt = "after_step_execute"
			if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs := h.only()
			var is *invoke.State
			for _, x := range rs.Latest().Invocations {
				if x.Invocation.Purpose == invoke.PurposeExecute {
					is = x
				}
			}
			b := bounce(rs, is.Invocation.ID, bad, linkProblem, codeProblem)
			b.Step = step
			if err := forge(t, h, "forge/g11", b); err == nil || !strings.Contains(err.Error(), "not a step of the attempt") {
				t.Fatalf("step %d: err=%v", step, err)
			}
		}
	})
	t.Run("the door", func(t *testing.T) {
		run := record.RunID(record.NewID())
		ok := &QuestionBounce{Header: header(runRef(run), run, 1, "question_bounce/1"), Invocation: record.NewID(), Ask: bad, Problems: []AskProblem{linkProblem, codeProblem}}
		if err := ok.ValidateWire(); err != nil {
			t.Fatal(err)
		}
		for name, mut := range map[string]func(*QuestionBounce){
			"no invocation":       func(x *QuestionBounce) { x.Invocation = "" },
			"no problems":         func(x *QuestionBounce) { x.Problems = nil },
			"a lane note bounced": func(x *QuestionBounce) { x.Problems = []AskProblem{laneProblem, codeProblem} },
			"a link not in the ask": func(x *QuestionBounce) {
				x.Problems = []AskProblem{{Check: CheckLink, Link: liveLink, Detail: "dead"}, codeProblem}
			},
			"a link with no link": func(x *QuestionBounce) { x.Problems = []AskProblem{{Check: CheckLink, Detail: "dead"}, codeProblem} },
			"no words":            func(x *QuestionBounce) { x.Problems = []AskProblem{{Check: CheckLink, Link: deadLink}, codeProblem} },
			"an unknown check":    func(x *QuestionBounce) { x.Problems = []AskProblem{{Check: "vibes", Detail: "off"}} },
			"code_unsent in other words": func(x *QuestionBounce) {
				x.Problems = []AskProblem{linkProblem, {Check: CheckCodeUnsent, Detail: "no sent"}}
			},
			"a link problem that does not name the link": func(x *QuestionBounce) {
				x.Problems = []AskProblem{{Check: CheckLink, Link: deadLink, Detail: "the link is dead"}, codeProblem}
			},
			"code_unsent on a non-code ask": func(x *QuestionBounce) {
				x.Ask = Ask{Question: "Which mailbox?"}
				x.Problems = []AskProblem{codeProblem}
			},
			"an empty ask":    func(x *QuestionBounce) { x.Ask = Ask{} },
			"off-run subject": func(x *QuestionBounce) { x.Subject = record.Ref{Kind: "goal", ID: "x"} },
		} {
			x := *ok
			mut(&x)
			if err := x.ValidateWire(); err == nil {
				t.Fatalf("%s: passed the door", name)
			}
		}
		q := &Question{Header: header(runRef(run), run, 1, "question/1"), Question: "Which mailbox?", Unverified: []AskProblem{laneProblem}, Deadline: now().Add(AskTimebox)}
		if err := q.ValidateWire(); err == nil || !strings.Contains(err.Error(), "claims a code request, but the ask is not one") {
			t.Fatalf("lane note on a plain question: %v", err)
		}
		q = &Question{Header: header(runRef(run), run, 1, "question/1"), Question: "What is the 6-digit code?", Unverified: []AskProblem{{Check: CheckCodeLane, Detail: "the lane"}}, Deadline: now().Add(AskTimebox)}
		if err := q.ValidateWire(); err == nil || !strings.Contains(err.Error(), "other words than the gate's") {
			t.Fatalf("lane note in other words: %v", err)
		}
	})
}

const agendaFrame = "FRAME: carry out the goal on behalf of its owner."

// Recovery, the other window (review r1): a crash after the execute landed
// and before its ask was read leaves the file where the call put it. The
// resumed attempt grounds it as that call's — a good ask is the question
// (zero new calls); a bad one is bounced BY THE RESUMED ATTEMPT, the
// recovered call consumed and the step run once more there, and the
// attempt's usage counts both calls.
func TestRecoveredCallIsGroundedOnResume(t *testing.T) {
	first := func(rs *RunState) record.RecordID {
		for _, is := range rs.Attempts[0].Invocations {
			if is.Invocation.Purpose == invoke.PurposeExecute {
				return is.Invocation.ID
			}
		}
		return ""
	}
	u1, u2 := invoke.Usage{InputTokens: 10, CostUSD: 0.6, CostReported: true}, invoke.Usage{InputTokens: 5, CostUSD: 0.4, CostReported: true}
	t.Run("a good ask is the recovered call's question", func(t *testing.T) {
		h := open(t)
		askPath := filepath.Join(t.TempDir(), AskName)
		exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked well"), Do: writeAsk(t, askPath, askGood), Usage: u1})
		d := h.driver(exec, nil)
		d.AskPath, d.CrashAt = askPath, "after_applications"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		if _, err := os.Stat(askPath); err != nil {
			t.Fatal("the crash came before the read: the file should be there")
		}
		h.restart()
		exec2 := scripted(outward, okCall)
		dr := h.driver(exec2, nil)
		dr.AskPath = askPath
		if _, err := dr.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		rs := h.only()
		a2 := rs.Attempts[1]
		if len(exec2.Seen) != 0 || len(rs.Attempts) != 2 || a2.Question == nil || a2.Question.Invocation != first(rs) || len(a2.Bounces) != 0 {
			t.Fatalf("resume: calls=%d attempts=%d question=%+v", len(exec2.Seen), len(rs.Attempts), a2.Question)
		}
		if o := a2.Has(Recorded).Outcome; !strings.HasPrefix(o.Reason, "needs answer: What is the 6-digit code") || o.Invocation != first(rs) || o.Usage != u1 {
			t.Fatalf("outcome %+v", o)
		}
		if _, err := os.Stat(askPath); err == nil {
			t.Fatal("the ask file was not archived")
		}
		h.restart()
		if h.only().Attempts[1].Question == nil {
			t.Fatal("did not fold again from disk")
		}
	})
	t.Run("a bad ask is bounced by the resumed attempt", func(t *testing.T) {
		h := open(t)
		askPath := filepath.Join(t.TempDir(), AskName)
		exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, askBad), Usage: u1})
		d := h.driver(exec, nil)
		var asked []string
		d.AskPath, d.ProbeURL, d.CrashAt = askPath, probe(&asked), "after_applications"
		if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		h.restart()
		exec2 := scripted(outward, invoke.ScriptedCall{Response: []byte("asked well"), Do: writeAsk(t, askPath, askGood), Usage: u2})
		dr := h.driver(exec2, nil)
		dr.AskPath, dr.ProbeURL = askPath, probe(&asked)
		if _, err := dr.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		rs := h.only()
		a2 := rs.Attempts[1]
		if len(exec2.Seen) != 1 || !strings.Contains(string(exec2.Seen[0].Prompt), "## Your question to the operator was NOT sent") {
			t.Fatalf("re-run: %+v", exec2.Seen)
		}
		if len(a2.Bounces) != 1 || a2.Bounces[0].Invocation != first(rs) || !rs.bounced(first(rs)) || a2.Question == nil || a2.Question.Invocation == first(rs) || rs.bounced(a2.Question.Invocation) {
			t.Fatalf("attempt 2: bounces=%+v question=%+v", a2.Bounces, a2.Question)
		}
		o := a2.Has(Recorded).Outcome
		if !strings.HasPrefix(o.Reason, "needs answer: What is the 6-digit code") || o.Usage.InputTokens != 15 || o.Usage.CostUSD < 0.999 || o.Usage.CostUSD > 1.001 {
			t.Fatalf("outcome %+v", o)
		}
		h.restart()
		if a := h.only().Attempts[1]; len(a.Bounces) != 1 || a.Question == nil {
			t.Fatal("did not fold again from disk")
		}
	})
}

// An AGENDA attempt that asked ended on its question, whatever landed
// after it (the step's judge; the step itself): a resume ends there too,
// with no call made and no later step run (review r1: the reused execute
// read the archived file as "no question" and the plan went on). The
// resumed attempt runs under the run's frame, not the resuming driver's.
func TestAgendaQuestionSurvivesTheResume(t *testing.T) {
	for _, seam := range []string{"after_step_judge", "after_step"} {
		t.Run(seam, func(t *testing.T) {
			h := open(t)
			askPath := filepath.Join(t.TempDir(), AskName)
			exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("asked"), Do: writeAsk(t, askPath, askGood)}}}
			_, judge := agendaBackends(nil, []string{intentClear, planTwo, judgeDone})
			d := h.agenda(exec, judge)
			d.AskPath, d.CrashAt, d.Frame = askPath, seam, agendaFrame
			if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			a1 := h.only().Latest()
			if a1.Question == nil || a1.Question.Step != 1 {
				t.Fatalf("attempt 1: question=%+v", a1.Question)
			}
			h.restart()
			exec2 := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("must not run")}}}
			_, judge2 := agendaBackends(nil, []string{judgeDone, judgeDone})
			dr := h.agenda(exec2, judge2)
			dr.AskPath = askPath // no frame: the run's is inherited
			if _, err := dr.Resume(ctxBg); err != nil {
				t.Fatal(err)
			}
			rs := h.only()
			if len(rs.Attempts) != 2 || len(exec2.Seen) != 0 || len(judge2.Seen) != 0 {
				t.Fatalf("resume: attempts=%d execs=%d judges=%d", len(rs.Attempts), len(exec2.Seen), len(judge2.Seen))
			}
			a2 := rs.Attempts[1]
			o := a2.Has(Recorded).Outcome
			if o == nil || !strings.HasPrefix(o.Reason, "needs answer: What is the 6-digit code") || o.Invocation != a1.Question.Invocation {
				t.Fatalf("resumed outcome %+v", o)
			}
			if f, _ := FrameOf(a2, h.st); f != agendaFrame {
				t.Fatalf("attempt 2 frame %q", f)
			}
			h.restart()
			if h.only().Attempts[1].Has(Recorded) == nil {
				t.Fatal("did not fold again from disk")
			}
		})
	}
	// the question outranks the attempt bound (r2: `forced` came first and
	// the resumed run recorded "attempt bound reached" over its question)
	t.Run("under the attempt bound", func(t *testing.T) {
		h := open(t)
		askPath := filepath.Join(t.TempDir(), AskName)
		exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("asked"), Do: writeAsk(t, askPath, askGood)}}}
		_, judge := agendaBackends(nil, []string{intentClear, planTwo, judgeDone})
		d := h.agenda(exec, judge)
		d.AskPath, d.CrashAt, d.MaxAttempts = askPath, "after_step", 1
		if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		h.restart()
		exec2 := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}}
		_, judge2 := agendaBackends(nil, nil)
		dr := h.agenda(exec2, judge2)
		dr.AskPath, dr.MaxAttempts = askPath, 1
		if _, err := dr.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		rs := h.only()
		if o := rs.Latest().Has(Recorded).Outcome; len(exec2.Seen) != 0 || o == nil || !strings.HasPrefix(o.Reason, "needs answer: What is the 6-digit code") || o.Steps != 1 {
			t.Fatalf("bounded resume: calls=%d outcome=%+v", len(exec2.Seen), o)
		}
	})
	// a question from before the gate names no invocation; it still ends
	// the resumed attempt, which cites no call for it (r2)
	t.Run("a pre-gate question", func(t *testing.T) {
		h := open(t)
		exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("Collected 12 rows")}}}
		_, judge := agendaBackends(nil, []string{intentClear, planTwo})
		d := h.agenda(exec, judge)
		d.CrashAt = "after_step_execute"
		if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
			t.Fatal(err)
		}
		rs := h.only()
		old := &Question{Header: header(runRef(rs.Run), rs.Run, 1, "question/1"), Step: 1, Question: "Which of the two mailboxes is yours?", Tried: true, Deadline: now().Add(AskTimebox)}
		if err := forge(t, h, "forge/pregate", old); err != nil {
			t.Fatal(err)
		}
		h.restart()
		exec2 := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}}
		_, judge2 := agendaBackends(nil, nil)
		dr := h.agenda(exec2, judge2)
		if _, err := dr.Resume(ctxBg); err != nil {
			t.Fatal(err)
		}
		rs = h.only()
		if o := rs.Latest().Has(Recorded).Outcome; len(exec2.Seen) != 0 || o == nil || o.Reason != "needs answer: Which of the two mailboxes is yours?" || o.Invocation != "" {
			t.Fatalf("pre-gate resume: calls=%d outcome=%+v", len(exec2.Seen), o)
		}
	})
	// the outcome that cites no call is still re-derived (review r3: its
	// usage, steps and reason were unchecked): the honest one folds, a lie
	// about any of them is refused
	t.Run("a pre-gate question's outcome lies", func(t *testing.T) {
		mk := func(t *testing.T, mut func(o *Outcome)) error {
			h := open(t)
			// step 1 settled (its call carries usage) before the question
			exec := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}, Calls: []invoke.ScriptedCall{{Response: []byte("Collected 12 rows"), Usage: invoke.Usage{InputTokens: 10, CostUSD: 0.6, CostReported: true}}}}
			_, judge := agendaBackends(nil, []string{intentClear, planTwo, judgeDone})
			d := h.agenda(exec, judge)
			d.CrashAt = "after_step"
			if _, err := d.Run(ctxBg, []byte(goalQuarterly), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs := h.only()
			old := &Question{Header: header(runRef(rs.Run), rs.Run, 1, "question/1"), Step: 1, Question: "Which of the two mailboxes is yours?", Tried: true, Deadline: now().Add(AskTimebox)}
			if err := forge(t, h, "forge/pregate", old); err != nil {
				t.Fatal(err)
			}
			h.restart()
			exec2 := &invoke.Scripted{Caps: invoke.Capabilities{Name: "scripted-exec", Model: "exec"}}
			_, judge2 := agendaBackends(nil, nil)
			dr := h.agenda(exec2, judge2)
			dr.CrashAt = "after_judged"
			if _, err := dr.Resume(ctxBg); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs = h.only()
			a2 := rs.Latest()
			if a2.Attempt.Attempt != 2 || a2.Current() != Judged {
				t.Fatalf("attempt %d at %s", a2.Attempt.Attempt, a2.Current())
			}
			// the honest outcome, as the driver would record it
			var vs []*verdict.Verdict
			for _, v := range h.verdicts(t, rs.Run) {
				if v.VerdictKind == verdict.KindClosure && v.Attempt == 2 {
					vs = append(vs, v)
				}
			}
			res, err := verdict.Commit(ctxBg, h.j, rs.Run, 2, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: vs, Observations: a2.Observations}, verdict.DefaultThresholds)
			if err != nil {
				t.Fatal(err)
			}
			src := ""
			for _, v := range vs {
				if v.ID == res.Effective {
					src = string(v.Source.Standing)
				}
			}
			o := &Outcome{Lane: LaneAgenda, Terminal: invoke.TerminalFailed, Reason: NeedsAnswer(old), Usage: goalUsage(rs), Recall: a2.Recall.ID, Steps: len(a2.Steps), GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence, ClosureSrc: src}
			if o.Usage == (invoke.Usage{}) || o.Steps == 0 {
				t.Fatalf("the fixture accounts nothing: %+v", o)
			}
			mut(o)
			return forge(t, h, "lie", &Transition{Header: header(runRef(rs.Run), rs.Run, 2, "run_transition/1"), From: Judged, To: Recorded, Outcome: o})
		}
		resp := thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Response, Bytes: 2, Encoding: thought.UTF8}
		cases := []struct {
			name string
			mut  func(o *Outcome)
			want string
		}{
			{"honest", func(o *Outcome) {}, ""},
			{"invented steps", func(o *Outcome) { o.Steps = 7 }, "usage/steps"},
			{"no steps", func(o *Outcome) { o.Steps = 0 }, "usage/steps"},
			{"invented usage", func(o *Outcome) { o.Usage.InputTokens = 999 }, "usage/steps"},
			{"no usage", func(o *Outcome) { o.Usage = invoke.Usage{} }, "usage/steps"},
			{"another question", func(o *Outcome) { o.Reason = "needs answer: attacker-selected text" }, "no unrecorded question"},
			{"a question the run recorded in other words", func(o *Outcome) { o.Reason = "needs answer: which of the two mailboxes is yours?" }, "no unrecorded question"},
			{"a response with no call", func(o *Outcome) { o.Response = &resp }, "receipt with no invocation"},
		}
		for _, c := range cases {
			err := mk(t, c.mut)
			if c.want == "" && c.name == "honest" {
				if err != nil {
					t.Fatalf("honest outcome refused: %v", err)
				}
				continue
			}
			if err == nil || (c.want != "" && !strings.Contains(err.Error(), c.want)) {
				t.Fatalf("%s: folded: %v (want %q)", c.name, err, c.want)
			}
		}
	})
	// an attempt that asked records its question's reason, in those words
	t.Run("the asking attempt's outcome lies about its reason", func(t *testing.T) {
		mk := func(t *testing.T, reason func(q *Question) string, term invoke.TerminalState) error {
			h := open(t)
			askPath := filepath.Join(t.TempDir(), AskName)
			exec := scripted(outward, invoke.ScriptedCall{Response: []byte("asked"), Do: writeAsk(t, askPath, askPlain)})
			d := h.driver(exec, nil)
			d.AskPath, d.CrashAt = askPath, "after_judged"
			if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); !errors.Is(err, ErrCrashed) {
				t.Fatal(err)
			}
			rs := h.only()
			a := rs.Latest()
			if a.Question == nil {
				t.Fatal("no question")
			}
			inv := a.Invocations[0]
			vs := h.verdicts(t, rs.Run)
			res, err := verdict.Commit(ctxBg, h.j, rs.Run, 1, verdict.Candidates{Subject: runRef(rs.Run), VerdictKind: verdict.KindClosure, Verdicts: vs, Observations: a.Observations}, verdict.DefaultThresholds)
			if err != nil {
				t.Fatal(err)
			}
			src := ""
			for _, v := range vs {
				if v.ID == res.Effective {
					src = string(v.Source.Standing)
				}
			}
			r := inv.Receipt.Response
			o := &Outcome{Lane: LaneNow, Terminal: term, Reason: reason(a.Question), Invocation: inv.Invocation.ID, Produced: 1, Receipt: inv.Receipt.ID, Response: &r, Usage: inv.Receipt.Usage, Model: inv.Invocation.Backend.Model, Recall: a.Recall.ID, GoalText: rs.Goal.Text, Closure: res.ID, ClosureOut: res.Outcome, ClosureCnf: res.Confidence, ClosureSrc: src}
			return forge(t, h, "lie", &Transition{Header: header(runRef(rs.Run), rs.Run, 1, "run_transition/1"), From: Judged, To: Recorded, Outcome: o})
		}
		if err := mk(t, func(*Question) string { return "the worker finished" }, invoke.TerminalFailed); err == nil || !strings.Contains(err.Error(), "asked question") {
			t.Fatalf("a question with another reason folded: %v", err)
		}
		if err := mk(t, NeedsAnswer, invoke.TerminalComplete); err == nil || !strings.Contains(err.Error(), "asked question") {
			t.Fatalf("a question that succeeded folded: %v", err)
		}
		if err := mk(t, NeedsAnswer, invoke.TerminalFailed); err != nil {
			t.Fatalf("the honest reason refused: %v", err)
		}
	})
}

// An ask file left from before the call (a failed call that wrote one; a
// bounce whose archive never landed) is archived before the call is made,
// never credited to it (review r1).
func TestStaleAskIsArchivedBeforeTheCall(t *testing.T) {
	h := open(t)
	dir := t.TempDir()
	askPath := filepath.Join(dir, AskName)
	writeAsk(t, askPath, askPlain)(invoke.Request{})
	exec := scripted(outward, okCall)
	d := h.driver(exec, nil)
	d.AskPath = askPath
	if _, err := d.Run(ctxBg, []byte(goalFlaky), DeliveryPolicy{Required: TransportAccepted}); err != nil {
		t.Fatal(err)
	}
	a := h.only().Latest()
	if a.Question != nil || len(a.Bounces) != 0 || len(exec.Seen) != 1 {
		t.Fatalf("credited with a question it did not ask: %+v", a.Question)
	}
	archives, _ := filepath.Glob(filepath.Join(dir, "ask-operator.*.asked.json"))
	stale := false
	for _, e := range h.events {
		stale = stale || e.Stage == "ask_stale_archived"
	}
	if len(archives) != 1 || !stale {
		t.Fatalf("archives=%d stale event=%v", len(archives), stale)
	}
}

// The real probe: HEAD first, GET when HEAD is refused, < 400 resolves;
// the run's context bounds it.
func TestProbeURL(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/ok":
			w.WriteHeader(http.StatusOK)
		case r.URL.Path == "/nohead" && r.Method == http.MethodHead:
			w.WriteHeader(http.StatusMethodNotAllowed)
		case r.URL.Path == "/nohead":
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	defer srv.Close()
	if why := probeURL(ctxBg, srv.URL+"/ok"); why != "" {
		t.Fatalf("ok: %q", why)
	}
	if why := probeURL(ctxBg, srv.URL+"/nohead"); why != "" {
		t.Fatalf("nohead: %q", why)
	}
	if why := probeURL(ctxBg, srv.URL+"/gone"); why != "returns HTTP 404" {
		t.Fatalf("gone: %q", why)
	}
	c, cancel := context.WithCancel(ctxBg)
	cancel()
	if why := probeURL(c, srv.URL+"/ok"); !strings.HasPrefix(why, "could not be reached") {
		t.Fatalf("cancelled: %q", why)
	}
	// the driver's default is the real probe
	d := &Driver{}
	if hard, _ := d.ground(ctxBg, &Ask{Question: "See " + srv.URL + "/gone and tell me"}); len(hard) != 1 || hard[0].Check != CheckLink || !strings.Contains(hard[0].Detail, "HTTP 404") {
		t.Fatalf("hard %+v", hard)
	}
}
