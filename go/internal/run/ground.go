package run

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"reflect"
	"regexp"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The grounding gate (audit §3 item 3, §4.4; Python's OPERATOR_ASK_DESIGN
// §7): an ask is a CLAIM, and the engine checks it before the operator
// sees it. Run 084d3c1f's fourth question sent Jeremy to a link that
// 404'd for a code nobody had sent; the resumed run asked it again. Two
// deterministic checks, run on the host after the execute that wrote the
// ask:
//
//   - every link in the ask resolves (HEAD, then GET; < 400);
//   - a request for a code says in `sent` how the worker triggered its
//     delivery and what confirmation it saw.
//
// A failing ask is BOUNCED: a question_bounce record names the execute
// whose worker wrote it and the problems, the ask file is archived beside
// the next one, and the step runs ONCE more with the problems at the top
// of its context. A second failure passes through: the Question carries
// the problems as `unverified` — the gate tells the operator what Maro
// could not verify about its own question; it never blocks them. The
// bounce is a record because the re-run's request is derived from it (the
// fold re-derives every execute request), and because a bounced call is
// consumed: no outcome, step or later attempt may cite it as the step's.
//
// Go's own road (D1): the Python engine's third rule — a code ask must be
// LIVE, because the code is consumed by the session that asked and the
// pause ends it — cannot be a bounce here: this lane has no live window
// (the attempt ends on the question by design, pattern 109), so nothing
// the worker does fixes it. It rides to the operator as `unverified`
// instead: they can judge whether the code they hold is still usable.
// The live window itself is the design decision still owed.

// AskCheck names one check of the gate.
type AskCheck string

const (
	// CheckLink: a link in the ask did not resolve (bounced).
	CheckLink AskCheck = "link"
	// CheckCodeUnsent: the ask requests a code and `sent` does not say how
	// its delivery was triggered (bounced).
	CheckCodeUnsent AskCheck = "code_unsent"
	// CheckCodeLane: the ask requests a code, which the session that asked
	// consumes, and this lane ends the attempt on the question (the
	// operator's to judge; never bounced).
	CheckCodeLane AskCheck = "code_lane"
)

var bounceChecks = map[AskCheck]bool{CheckLink: true, CheckCodeUnsent: true}

// The deterministic checks say one thing each; the fold holds a record to
// the exact words (review r1: a problem's detail is what the worker and
// the operator read — a forged record could carry any prose under an
// honest check). A link problem's detail is the probe's evidence, which
// the fold cannot re-run; it is held to naming the link.
const (
	codeUnsentDetail = "you ask for a code but do not say how it was sent: trigger the delivery yourself first (choose the SMS or authenticator option on the challenge page), then put the confirmation you saw in \"sent\" and ask again"
	codeLaneDetail   = "a code is consumed by the session that asked for it, and this run ended on the question: the follow-up run may have to request a fresh one"
)

// AskProblem is one failed check: which, the link when it is one, and
// the words the worker (a bounce) or the operator (unverified) reads.
type AskProblem struct {
	Check  AskCheck `json:"check"`
	Link   string   `json:"link,omitempty"`
	Detail string   `json:"detail"`
}

func (p AskProblem) validate(ask *Ask, soft bool) error {
	switch p.Check {
	case CheckLink:
		if p.Link == "" {
			return errors.New("a link problem names no link")
		}
		found := false
		for _, u := range linksOf(ask) {
			if u == p.Link {
				found = true
			}
		}
		if !found {
			return fmt.Errorf("names link %q, which the ask does not carry", p.Link)
		}
		if !strings.Contains(p.Detail, p.Link) {
			return errors.New("a link problem whose words do not name the link")
		}
	case CheckCodeUnsent:
		if codeUnsent(ask) == nil {
			return errors.New("claims a code request without `sent`, but the ask is not one")
		}
		if p.Detail != codeUnsentDetail {
			return errors.New("a code_unsent problem in other words than the gate's")
		}
	case CheckCodeLane:
		if !soft {
			return errors.New("code_lane is the operator's note, never a bounce")
		}
		if !asksForCode(ask.Question) {
			return errors.New("claims a code request, but the ask is not one")
		}
		if p.Detail != codeLaneDetail {
			return errors.New("a code_lane note in other words than the gate's")
		}
	default:
		return fmt.Errorf("unknown check %q", p.Check)
	}
	if strings.TrimSpace(p.Detail) == "" {
		return errors.New("a problem with no words")
	}
	return nil
}

// problemsText renders problems for a reason line or a row.
func problemsText(ps []AskProblem) string {
	parts := make([]string, len(ps))
	for i, p := range ps {
		parts[i] = p.Detail
	}
	return strings.Join(parts, "; ")
}

var (
	urlRE  = regexp.MustCompile(`https?://[^\s"'<>)\]]+`)
	codeRE = regexp.MustCompile(`(?i)\b(2fa|otp|one[- ]time|verification code|6[- ]digit|passcode|security code|code (?:sent|from|that was sent|yahoo|google|apple)|backup code|authenticat\w+ code)\b`)
)

// linksOf lists the links an ask carries, in order of first appearance,
// trailing punctuation dropped, deduplicated.
func linksOf(ask *Ask) []string {
	var out []string
	for _, field := range []string{ask.Question, ask.Why, ask.NoInputAlternative, ask.Sent} {
		for _, u := range urlRE.FindAllString(field, -1) {
			u = strings.TrimRight(u, ".,;:!?")
			dup := false
			for _, seen := range out {
				if seen == u {
					dup = true
				}
			}
			if !dup {
				out = append(out, u)
			}
		}
	}
	return out
}

// asksForCode: the question requests a code (2FA / OTP / a 6-digit code /
// a verification code …). Same words as the Python engine's.
func asksForCode(question string) bool {
	return codeRE.MatchString(question) && strings.Contains(strings.ToLower(question), "code")
}

// codeUnsent is the bounce for a code request that does not say how the
// worker triggered its delivery; nil when the ask is not one, or says.
func codeUnsent(ask *Ask) *AskProblem {
	if !asksForCode(ask.Question) || strings.TrimSpace(ask.Sent) != "" {
		return nil
	}
	return &AskProblem{Check: CheckCodeUnsent, Detail: codeUnsentDetail}
}

// codeLane is the operator's note on every code request in this lane.
func codeLane(ask *Ask) *AskProblem {
	if !asksForCode(ask.Question) {
		return nil
	}
	return &AskProblem{Check: CheckCodeLane, Detail: codeLaneDetail}
}

// linkProbeTimeout bounds one probe; five links at most are probed.
const (
	linkProbeTimeout = 8 * time.Second
	linkProbeMax     = 5
)

// probeURL is the real probe: "" when the link answers < 400 to HEAD (or
// to GET when HEAD is refused), otherwise a short reason. The run's
// context bounds it: a cancelled run does not hold the executor on a
// slow host (review r1).
func probeURL(ctx context.Context, u string) string {
	client := &http.Client{Timeout: linkProbeTimeout}
	last := ""
	for _, method := range []string{http.MethodHead, http.MethodGet} {
		req, err := http.NewRequestWithContext(ctx, method, u, nil)
		if err != nil {
			return "could not be reached (" + clip(err.Error(), 80) + ")"
		}
		req.Header.Set("User-Agent", "Mozilla/5.0 (maro ask-grounding)")
		resp, err := client.Do(req)
		if err != nil {
			return "could not be reached (" + clip(err.Error(), 80) + ")"
		}
		resp.Body.Close()
		if resp.StatusCode < 400 {
			return ""
		}
		last = fmt.Sprintf("HTTP %d", resp.StatusCode)
		if method == http.MethodHead && (resp.StatusCode == http.StatusForbidden || resp.StatusCode == http.StatusMethodNotAllowed) {
			continue
		}
		return "returns " + last
	}
	return "returns " + last
}

// ground runs the gate over an ask: hard problems bounce to the worker,
// soft ones ride to the operator as unverified.
func (d *Driver) ground(ctx context.Context, ask *Ask) (hard, soft []AskProblem) {
	probe := d.ProbeURL
	if probe == nil {
		probe = probeURL
	}
	links := linksOf(ask)
	if len(links) > linkProbeMax {
		links = links[:linkProbeMax]
	}
	for _, u := range links {
		if why := probe(ctx, u); why != "" {
			hard = append(hard, AskProblem{Check: CheckLink, Link: u, Detail: "the link " + u + " " + why + " — a question must not send the operator to a page that does not exist; fix the link or drop it"})
		}
	}
	if p := codeUnsent(ask); p != nil {
		hard = append(hard, *p)
	}
	if p := codeLane(ask); p != nil {
		soft = append(soft, *p)
	}
	return hard, soft
}

// QuestionBounce is the gate's refusal of an ask: the execute whose worker
// wrote it, the ask as written, and the checks it failed. The step runs
// once more with the problems at the top of its context; the bounced call
// is consumed (no outcome or step may cite it). At most one per step of
// an attempt: the second failure passes through as unverified.
type QuestionBounce struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Step          int             `json:"step,omitempty"` // the AGENDA step ordinal; 0 for a NOW execute
	Invocation    record.RecordID `json:"invocation"`
	Ask           Ask             `json:"ask"`
	Problems      []AskProblem    `json:"problems"`
}

const KindQuestionBounce record.Kind = "question_bounce"

func (r *QuestionBounce) Head() *record.Header { return &r.Header }
func (r *QuestionBounce) Kind() record.Kind    { return KindQuestionBounce }
func (r *QuestionBounce) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Subject != runRef(r.RunID) {
		return errors.New("question_bounce: subject must be its run")
	}
	if r.Invocation == "" {
		return errors.New("question_bounce: names no invocation")
	}
	if strings.TrimSpace(r.Ask.Question) == "" {
		return errors.New("question_bounce: the ask has no question")
	}
	if len(r.Problems) == 0 {
		return errors.New("question_bounce: bounces nothing")
	}
	for i, p := range r.Problems {
		if !bounceChecks[p.Check] {
			return fmt.Errorf("question_bounce: problem %d: check %q does not bounce", i, p.Check)
		}
		if err := p.validate(&r.Ask, false); err != nil {
			return fmt.Errorf("question_bounce: problem %d: %v", i, err)
		}
	}
	return nil
}

func init() {
	record.Register(record.Spec{Kind: KindQuestionBounce, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(QuestionBounce{}), Retention: record.Forever,
		Writer:   "the driver after an execute whose worker wrote an $MARO_ASK that failed the grounding gate (a dead link; a code request with no `sent`)",
		Reader:   "the run fold (the re-run's request is rendered from it; the bounced call is consumed); `maro-go asks` (bounced once)",
		Decision: "that the worker's question did not reach the operator and the step ran once more with the problems in its context"})
}

// bounceBlock renders the bounce at the top of the re-run's context: the
// problems, and what to do (fix and ask again, or finish with a stated
// gap). The fold renders the same bytes.
func bounceBlock(b *QuestionBounce) []byte {
	if b == nil {
		return nil
	}
	var sb strings.Builder
	sb.WriteString("\n\n## Your question to the operator was NOT sent\nIt failed a check:\n")
	for _, p := range b.Problems {
		sb.WriteString("- " + p.Detail + "\n")
	}
	sb.WriteString("Fix the question and ask again by writing the file — or finish with a stated gap. This is the one re-run: a question that fails again reaches the operator marked unverified.\n")
	return []byte(sb.String())
}

// Bounced reports whether the attempt bounced the worker's ask for the
// step before the question it holds (the `asks` surface).
func Bounced(a *AttemptState, step int) bool { return a != nil && a.bounceFor(step) != nil }

// ProblemsText renders problems for a row.
func ProblemsText(ps []AskProblem) string { return problemsText(ps) }

// bounceFor is the attempt's bounce for a step, when it made one.
func (a *AttemptState) bounceFor(step int) *QuestionBounce {
	for _, b := range a.Bounces {
		if b.Step == step {
			return b
		}
	}
	return nil
}

// bounced reports whether some attempt of the run bounced the invocation:
// a consumed call.
func (r *RunState) bounced(inv record.RecordID) bool {
	for _, a := range r.Attempts {
		for _, b := range a.Bounces {
			if b.Invocation == inv {
				return true
			}
		}
	}
	return false
}

// bounceOf is the bounce that the request of an execute invocation was
// rendered under: its own attempt's bounce for the step, unless the
// invocation is the bounced call itself (rendered before it).
func bounceOf(rs *RunState, st *invoke.State, step int) *QuestionBounce {
	if st == nil {
		return nil
	}
	n := int(st.Invocation.Attempt)
	if n < 1 || n > len(rs.Attempts) {
		return nil
	}
	b := rs.Attempts[n-1].bounceFor(step)
	if b == nil || b.Invocation == st.Invocation.ID {
		return nil
	}
	return b
}

// askOf is the Question as the ask it was written from — what the gate's
// checks are re-derived over.
func askOf(q *Question) *Ask {
	return &Ask{Question: q.Question, Why: q.Why, NoInputAlternative: q.NoInputAlternative, Tried: q.Tried, Sent: q.Sent}
}

// stepOfAttempt reports whether a step ordinal is one of the attempt's:
// 0 for a NOW attempt (no plan), 1..n for an AGENDA attempt with an
// n-step plan (review r1: a bounce with a step this attempt does not have
// is checked against nothing, and a question naming that step can then
// claim a re-run that never saw the bounce).
func stepOfAttempt(a *AttemptState, step int) bool {
	if a.Attempt.Config.Lane != LaneAgenda {
		return step == 0
	}
	// an AGENDA attempt has steps only once it has a plan (r2: "no plan"
	// read as NOW let a planless AGENDA attempt carry step 0)
	return a.Plan != nil && step >= 1 && step <= len(a.Plan.Steps)
}

// checkQuestionBounce executes the bounce's rules as it folds: the attempt
// is executing and has not asked; one bounce per step; the bounced call
// is a landed execute of this run, from this attempt or a recovered
// earlier one, that no step or earlier bounce consumed; every problem is
// one the gate raises about THIS ask, and the deterministic one (a code
// request with no `sent`) is never left out.
func checkQuestionBounce(rs *RunState, a *AttemptState, x *QuestionBounce) error {
	if a.Question != nil || a.Current() != Executing {
		return fmt.Errorf("run: %s attempt %d bounce out of place (state %s, prior question %v)", x.RunID, x.Attempt, a.Current(), a.Question != nil)
	}
	if !stepOfAttempt(a, x.Step) {
		return fmt.Errorf("run: %s attempt %d bounce names step %d, which is not a step of the attempt", x.RunID, x.Attempt, x.Step)
	}
	if a.bounceFor(x.Step) != nil {
		return fmt.Errorf("run: %s attempt %d bounced step %d twice", x.RunID, x.Attempt, x.Step)
	}
	st, by := rs.invocation(x.Invocation)
	if st == nil || by > x.Attempt || st.Invocation.Purpose != invoke.PurposeExecute || st.Receipt == nil {
		return fmt.Errorf("run: %s attempt %d bounce cites invocation %s that is not a landed execute of this run", x.RunID, x.Attempt, x.Invocation)
	}
	if rs.bounced(x.Invocation) {
		return fmt.Errorf("run: %s attempt %d bounce cites invocation %s, which is already bounced", x.RunID, x.Attempt, x.Invocation)
	}
	for _, p := range rs.Attempts {
		for _, sd := range p.Steps {
			if sd.Invocation == x.Invocation {
				return fmt.Errorf("run: %s attempt %d bounce cites invocation %s, which step %d already cites", x.RunID, x.Attempt, x.Invocation, sd.Ordinal)
			}
		}
	}
	if p := codeUnsent(&x.Ask); p != nil {
		found := false
		for _, q := range x.Problems {
			found = found || q.Check == CheckCodeUnsent
		}
		if !found {
			return fmt.Errorf("run: %s attempt %d bounce leaves out the code request with no `sent`", x.RunID, x.Attempt)
		}
	}
	return nil
}

// checkQuestion executes the gate's rules over a Question as it folds. A
// question that names no invocation predates the gate and is held to
// nothing until the journal shows one that does (watermark). Otherwise:
// the invocation is a landed execute of this run (this attempt or a
// recovered one) that no bounce consumed; after a bounce of the step, the
// question comes from the re-run; the unverified problems are the gate's
// about this ask — a bounced kind only after a bounce, the code request
// with no `sent` never passed silently, the lane note on every code
// request.
func checkQuestion(rs *RunState, a *AttemptState, x *Question, firstGrounded uint64) error {
	if x.Invocation == "" {
		if firstGrounded != 0 && x.Seq > firstGrounded {
			return fmt.Errorf("run: %s attempt %d question names no invocation after the journal shows grounded ones", x.RunID, x.Attempt)
		}
		return nil
	}
	st, by := rs.invocation(x.Invocation)
	if st == nil || by > x.Attempt || st.Invocation.Purpose != invoke.PurposeExecute || st.Receipt == nil {
		return fmt.Errorf("run: %s attempt %d question cites invocation %s that is not a landed execute of this run", x.RunID, x.Attempt, x.Invocation)
	}
	if rs.bounced(x.Invocation) {
		return fmt.Errorf("run: %s attempt %d question cites invocation %s, which the gate bounced", x.RunID, x.Attempt, x.Invocation)
	}
	if !stepOfAttempt(a, x.Step) {
		return fmt.Errorf("run: %s attempt %d question names step %d, which is not a step of the attempt", x.RunID, x.Attempt, x.Step)
	}
	ask := askOf(x)
	b := a.bounceFor(x.Step)
	if b != nil && (by != x.Attempt || st.Invocation.Seq < b.Seq) {
		// after a bounce, the question is the re-run's: this attempt's own
		// call, made after the bounce
		return fmt.Errorf("run: %s attempt %d question after a bounce of step %d cites invocation %s, which is not the re-run", x.RunID, x.Attempt, x.Step, x.Invocation)
	}
	for i, p := range x.Unverified {
		if err := p.validate(ask, true); err != nil {
			return fmt.Errorf("run: %s attempt %d question unverified %d: %v", x.RunID, x.Attempt, i, err)
		}
		if bounceChecks[p.Check] && b == nil {
			return fmt.Errorf("run: %s attempt %d question passes a %s problem through with no bounce of step %d before it", x.RunID, x.Attempt, p.Check, x.Step)
		}
	}
	has := func(c AskCheck) bool {
		for _, p := range x.Unverified {
			if p.Check == c {
				return true
			}
		}
		return false
	}
	if codeUnsent(ask) != nil && !has(CheckCodeUnsent) {
		return fmt.Errorf("run: %s attempt %d question asks for a code with no `sent` and does not say so", x.RunID, x.Attempt)
	}
	if asksForCode(ask.Question) != has(CheckCodeLane) {
		return fmt.Errorf("run: %s attempt %d question's lane note disagrees with the question (code request %v)", x.RunID, x.Attempt, asksForCode(ask.Question))
	}
	return nil
}
