package run

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Operator questions — the rare exception, built as a lane (decision
// 1d1ad8b0, Jeremy 2026-09-06: "maro answers its own questions as much as
// possible... asking should be the rare exception, not the norm"). The
// contract is the Python engine's (src/operator_ask.py), word for word
// where a worker reads it:
//
//   - the execute frame names $MARO_ASK, a file path; a worker that cannot
//     proceed without something only the operator has writes ONE JSON
//     object there and ends its step — after first trying a path that
//     needs no input, which it records (NoInputAlternative, Tried);
//   - the driver reads the file after the execute (never the worker's
//     prose: a claim "I asked" without the file is not an ask), commits a
//     Question record with a time box, archives the file (never deletes
//     it), and ends the attempt as an honest failed terminal, "needs
//     answer: …" — the same shape as "needs clarification";
//   - `maro-go answer <handle> <text>` commits an Answer record against the
//     run and runs the goal again in its lane as a follow-up of that run
//     (lineage --after), with the answer as operator context;
//   - `maro-go asks` lists every question: pending, answered, or expired.

const (
	AskName = "ask-operator.json"
	AskEnv  = "MARO_ASK"
	// AskTimebox is how long a question waits before `asks` reports it
	// expired. Expiry destroys nothing: a late answer still runs the
	// follow-up, marked late.
	AskTimebox = 24 * time.Hour

	KindQuestion record.Kind = "question"
	KindAnswer   record.Kind = "answer"

	needsAnswerPrefix = "needs answer: "
)

// Ask is what a worker writes to $MARO_ASK.
type Ask struct {
	Question           string `json:"question"`
	Why                string `json:"why,omitempty"`
	NoInputAlternative string `json:"no_input_alternative,omitempty"`
	Tried              bool   `json:"tried"`
}

// AskInstructions is the `## Asking the operator` paragraph of the execute
// frame. Same wording as operator_ask.instructions on the Python side.
func AskInstructions(path string) string {
	return "## Asking the operator\n" +
		"The owner is not present. Asking them is the rare exception, not a " +
		"step: first try a path that needs no input from them, and prefer " +
		"finishing with a stated gap over asking for a decision, a judgement " +
		"or permission. Ask only when the goal cannot proceed without " +
		"something only the operator has — a code sent to them, a choice " +
		"that is theirs alone, a credential absent from the secrets store. " +
		"To ask, write ONE JSON object to " + path + " — " +
		`{"question": "...", "why": "...", "no_input_alternative": "what you ` +
		`tried without them, or why none exists", "tried": true} — then end ` +
		"the step saying you asked. The run pauses until the answer arrives " +
		"and resumes with the answer in your next step's context; the " +
		"question is a counted, reviewed event."
}

// ReadAsk parses a worker's ask file: nil, nil when absent; an error when
// present but unusable (left in place for the operator to see).
func ReadAsk(path string) (*Ask, error) {
	if path == "" {
		return nil, nil
	}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var raw struct {
		Question    string          `json:"question"`
		Why         string          `json:"why"`
		NoInput     string          `json:"no_input_alternative"`
		Alternative string          `json:"alternative"`
		Tried       json.RawMessage `json:"tried"`
	}
	if err := json.Unmarshal(b, &raw); err != nil {
		return nil, fmt.Errorf("ask file %s: %w", path, err)
	}
	q := strings.TrimSpace(raw.Question)
	if q == "" {
		return nil, fmt.Errorf("ask file %s: no question", path)
	}
	alt := strings.TrimSpace(raw.NoInput)
	if alt == "" {
		alt = strings.TrimSpace(raw.Alternative)
	}
	tried := false
	switch strings.TrimSpace(string(raw.Tried)) {
	case "true":
		tried = true
	case "", "false", "null":
	default:
		tried = strings.TrimSpace(string(raw.Tried)) != `""`
	}
	return &Ask{Question: clip(q, 800), Why: clip(strings.TrimSpace(raw.Why), 600), NoInputAlternative: clip(alt, 600), Tried: tried}, nil
}

func clip(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

// ArchiveAsk moves a consumed ask file aside (part of the run's record,
// never deleted) so the follow-up cannot re-trigger the same question.
func ArchiveAsk(path string) (string, error) {
	if path == "" {
		return "", nil
	}
	if _, err := os.Stat(path); err != nil {
		return "", nil
	}
	target := filepath.Join(filepath.Dir(path), "ask-operator."+time.Now().UTC().Format("20060102T150405Z")+".asked.json")
	return target, os.Rename(path, target)
}

// Question is the run's recorded ask: what the worker needs, why, what it
// tried first, and the time box. One per attempt; the attempt ends on it.
type Question struct {
	record.ProductionRecord
	record.Header      `json:"header"`
	Step               int       `json:"step,omitempty"` // the AGENDA step ordinal; 0 for a NOW execute
	Question           string    `json:"question"`
	Why                string    `json:"why,omitempty"`
	NoInputAlternative string    `json:"no_input_alternative,omitempty"`
	Tried              bool      `json:"tried"`
	Deadline           time.Time `json:"deadline"`
}

func (r *Question) Head() *record.Header { return &r.Header }
func (r *Question) Kind() record.Kind    { return KindQuestion }
func (r *Question) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Subject != runRef(r.RunID) {
		return errors.New("question: subject must be its run")
	}
	if strings.TrimSpace(r.Question) == "" {
		return errors.New("question: empty")
	}
	if r.Deadline.IsZero() {
		return errors.New("question: no deadline")
	}
	return nil
}

// Answer is the operator's reply, committed against the run that asked.
// The follow-up run is the goal run again in its lane, lineage --after the
// asked run, with the answer as operator context; its goal's Parent is
// the asked run's goal, which is how the two are found together.
type Answer struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Target        record.RunID    `json:"target"`
	Question      record.RecordID `json:"question,omitempty"` // the Question record; empty for a clarity-gate question (the intent record carries it)
	Text          string          `json:"text"`
	Source        string          `json:"source"` // cli | hermes-ssh | …
	Late          bool            `json:"late"`   // past the question's time box
}

func (r *Answer) Head() *record.Header { return &r.Header }
func (r *Answer) Kind() record.Kind    { return KindAnswer }
func (r *Answer) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.Target == "" || r.Subject != runRef(r.Target) || r.RunID != r.Target {
		return errors.New("answer: subject and run must be the target run")
	}
	if strings.TrimSpace(r.Text) == "" {
		return errors.New("answer: empty")
	}
	if r.Source == "" {
		return errors.New("answer: no source")
	}
	return nil
}

func init() {
	reg := func(k record.Kind, ty any, writer, reader, decision string) {
		record.Register(record.Spec{Kind: k, Envelope: record.Production, Version: 1, Type: reflect.TypeOf(ty), Writer: writer, Reader: reader, Decision: decision, Retention: record.Forever})
	}
	reg(KindQuestion, Question{}, "the driver after an execute whose worker wrote $MARO_ASK (NOW execute, AGENDA step)",
		"the run fold (the attempt's question); `maro-go asks`; `maro-go answer` (what is being answered; the time box)",
		"that the attempt ended on a question and what the operator must supply; whether an answer is late")
	reg(KindAnswer, Answer{}, "`maro-go answer <handle> <text>` (the CLI, for the operator or the Hermes gate)",
		"the run fold (the run's answer); `maro-go asks` (pending vs answered)",
		"that the question is answered and by whom; the follow-up run carries the text as operator context")
}

// NeedsAnswer renders the honest terminal reason for an attempt that ended
// on a question, and IsNeedsAnswer recognises it (tail signals).
func NeedsAnswer(q *Question) string { return needsAnswerPrefix + q.Question }
func IsNeedsAnswer(reason string) bool {
	return strings.HasPrefix(reason, needsAnswerPrefix)
}

// askAfterExecute is the driver's read of the ask file after an execute:
// nil when the driver has no ask path or the worker wrote nothing. A
// present file becomes a committed Question (the attempt's), and is
// archived. An unusable file is reported and left where it is.
func (d *Driver) askAfterExecute(ctx context.Context, rs *RunState, n uint32, step int) (*Question, error) {
	if d.AskPath == "" {
		return nil, nil
	}
	ask, err := ReadAsk(d.AskPath)
	if err != nil {
		d.emit(rs, n, "ask_unusable", Executing, err.Error())
		return nil, nil
	}
	if ask == nil {
		return nil, nil
	}
	h := header(runRef(rs.Run), rs.Run, n, "question/1")
	q := &Question{Header: h, Step: step, Question: ask.Question, Why: ask.Why, NoInputAlternative: ask.NoInputAlternative, Tried: ask.Tried, Deadline: h.At.Add(AskTimebox)}
	if err := d.commit(ctx, fmt.Sprintf("question/%s/%d/%d", rs.Run, n, step), q); err != nil {
		return nil, err
	}
	if _, err := ArchiveAsk(d.AskPath); err != nil {
		d.emit(rs, n, "ask_archive_failed", Executing, err.Error())
	}
	d.emit(rs, n, "ask", Executing, fmt.Sprintf("step %d: %s", step, ask.Question))
	return q, nil
}

// AnswerContext renders the answer as the operator context the follow-up
// run carries: the question, the answer, and the instruction not to ask
// again. Same shape as the Python continuation reason.
func AnswerContext(question, text string) []byte {
	return []byte("== Operator answer ==\n" +
		"The run paused to ask the operator: " + question + "\n" +
		"The operator answered: " + text + "\n" +
		"Continue from where the run paused, using this answer; do not ask it again.\n" +
		"== End operator answer ==")
}
