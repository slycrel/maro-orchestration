// Package judgment is the typed-judgment seam: a question with a declared
// answer type, asked of a provider, answered with a distribution.
//
// The wire shape is TypeSafe's System One API exactly (POST
// /v1/systemone: {"model", "state", "questions"} in, {"model", "answers",
// "usage"} out), so a local sidecar that mimics it works through the same
// code. Three providers speak it: `llm` (an existing generative backend,
// rendered into prose and parsed strictly), `jev` (TypeSafe), and `pcd`
// (the local sidecar).
//
// This file is pure: types, validation, and a byte-deterministic codec.
// Determinism is load-bearing — the run fold re-derives every judge
// request byte-for-byte from the facts, so the same Request must always
// encode to the same bytes.
package judgment

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"
)

// ErrWire is a malformed judgment request or response. It is the only
// error class this package's codec produces: a provider that answers
// something this package refuses has failed, and is recorded as failed —
// never guessed at.
var ErrWire = errors.New("judgment: wire")

func wireErr(format string, a ...any) error {
	return fmt.Errorf("%w: %s", ErrWire, fmt.Sprintf(format, a...))
}

// Kind is a question's answer type.
type Kind string

const (
	Noul   Kind = "noul"   // a probability that a statement holds
	Choice Kind = "choice" // one option from a named set, with a distribution
	Score  Kind = "score"  // a position on an ordered ladder of levels
)

var kinds = map[Kind]bool{Noul: true, Choice: true, Score: true}

// Option is one choice option and the description the model is given for
// it. An empty description is a JSON null on the wire (the API's "no
// description" spelling), never an empty string.
type Option struct {
	Name        string
	Description string
}

// Question is one typed question about the state. Fields outside the
// question's own Kind must be empty; Validate says so.
type Question struct {
	Type         Kind
	Instructions string
	Options      []Option // choice: ordered, at least two
	True         string   // noul: what a high probability means
	False        string   // noul: what a low probability means
	Levels       []string // score: ordered, at least two
	// Falsifiers asks the answerer to name what would refute its answer.
	// It is the LLM arm's extension: a System One provider has no such
	// field, so it is dropped from the wire request (and the provider
	// simply never answers it). Never a silent difference — the judgment
	// report shows which arm produced falsifiers.
	Falsifiers bool
}

// Validate refuses a question no provider could answer honestly.
func (q Question) Validate() error {
	if !kinds[q.Type] {
		return wireErr("question type %q out of vocabulary (noul|choice|score)", q.Type)
	}
	if strings.TrimSpace(q.Instructions) == "" {
		return wireErr("a %s question carries its instructions", q.Type)
	}
	switch q.Type {
	case Choice:
		if len(q.Options) < 2 {
			return wireErr("a choice question needs two or more options")
		}
		seen := map[string]bool{}
		for _, o := range q.Options {
			if strings.TrimSpace(o.Name) == "" {
				return wireErr("a choice option needs a name")
			}
			if seen[o.Name] {
				return wireErr("choice option %q appears twice", o.Name)
			}
			seen[o.Name] = true
		}
		if len(q.Levels) > 0 || q.True != "" || q.False != "" {
			return wireErr("a choice question carries only options")
		}
	case Score:
		if len(q.Levels) < 2 {
			return wireErr("a score question needs two or more levels")
		}
		for _, l := range q.Levels {
			if strings.TrimSpace(l) == "" {
				return wireErr("a score level needs a description")
			}
		}
		if len(q.Options) > 0 || q.True != "" || q.False != "" {
			return wireErr("a score question carries only levels")
		}
	case Noul:
		if (q.True == "") != (q.False == "") {
			return wireErr("noul criteria are both or neither")
		}
		if len(q.Options) > 0 || len(q.Levels) > 0 {
			return wireErr("a noul question carries only its criteria")
		}
	}
	return nil
}

// Names lists a choice question's option names in order.
func (q Question) Names() []string {
	out := make([]string, 0, len(q.Options))
	for _, o := range q.Options {
		out = append(out, o.Name)
	}
	return out
}

// Section is one named part of the state.
type Section struct {
	Key  string
	Text string
}

// State is the engine's state shape: ordered named sections, so the JSON
// object and the prose rendering agree and both are deterministic. A
// caller with arbitrary JSON hands Request.State a json.RawMessage
// instead.
type State struct{ Sections []Section }

// Sect builds a state from alternating key/text pairs.
func Sect(kv ...string) State {
	var s State
	for i := 0; i+1 < len(kv); i += 2 {
		s.Sections = append(s.Sections, Section{Key: kv[i], Text: kv[i+1]})
	}
	return s
}

// Request is one call: a state, and questions evaluated independently
// against it. Order is the question order on the wire and in the prose
// rendering; it is the Request's own, never a map iteration.
type Request struct {
	Model     string
	State     any
	Questions map[string]Question
	Order     []string
}

// Ask1 builds a single-question request (the shape every judge in this
// engine makes).
func Ask1(model string, state any, id string, q Question) Request {
	return Request{Model: model, State: state, Questions: map[string]Question{id: q}, Order: []string{id}}
}

// Validate refuses a request that is not answerable as written.
func (r Request) Validate() error {
	if strings.TrimSpace(r.Model) == "" {
		return wireErr("a request names its model")
	}
	if r.State == nil {
		return wireErr("a request carries its state")
	}
	if len(r.Questions) == 0 {
		return wireErr("a request asks at least one question")
	}
	if len(r.Order) != len(r.Questions) {
		return wireErr("order lists %d ids for %d questions", len(r.Order), len(r.Questions))
	}
	seen := map[string]bool{}
	for _, id := range r.Order {
		if strings.TrimSpace(id) == "" {
			return wireErr("a question id may not be empty")
		}
		if seen[id] {
			return wireErr("question id %q appears twice in the order", id)
		}
		seen[id] = true
		q, ok := r.Questions[id]
		if !ok {
			return wireErr("order names %q, which is not a question", id)
		}
		if err := q.Validate(); err != nil {
			return fmt.Errorf("question %q: %w", id, err)
		}
	}
	return nil
}

// Answer is one question's answer with its distribution. The value field
// that carries meaning is the one its Type names.
// The JSON tags are the RECORD encoding (a shadow_judgment carries the
// whole answer); the System One WIRE encoding is wireAnswer's, below.
type Answer struct {
	Type          Kind               `json:"type"`
	Noul          float64            `json:"noul,omitempty"`   // noul: P(true)
	Choice        string             `json:"choice,omitempty"` // choice: the option
	Score         float64            `json:"score,omitempty"`  // score: the position on the ladder
	Confidence    float64            `json:"confidence,omitempty"`
	Probabilities map[string]float64 `json:"probabilities,omitempty"` // option → p, or level index → p; may be absent
	Legend        map[string]string  `json:"legend,omitempty"`        // score: level index → description
	// Why and Falsifiers are the LLM arm's extensions: a System One
	// provider returns neither, and an answer without them is not
	// lesser — it is a different instrument.
	Why        string   `json:"why,omitempty"`
	Falsifiers []string `json:"falsifiers,omitempty"`
}

// Value is the answer's own number: the noul, the score, or the chosen
// option's probability when one was reported (else the confidence).
func (a Answer) Value() float64 {
	switch a.Type {
	case Noul:
		return a.Noul
	case Score:
		return a.Score
	default:
		if p, ok := a.Probabilities[a.Choice]; ok {
			return p
		}
		return a.Confidence
	}
}

// Outcome is the answer as a vocabulary word where it has one (choice),
// else the empty string.
func (a Answer) Outcome() string {
	if a.Type == Choice {
		return a.Choice
	}
	return ""
}

func inUnit(name string, v float64) error {
	if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v > 1 {
		return wireErr("%s %v out of [0,1]", name, v)
	}
	return nil
}

// Validate checks one answer against the question it answers.
func (a Answer) Validate(q Question) error {
	if a.Type != q.Type {
		return wireErr("answer type %q does not answer a %s question", a.Type, q.Type)
	}
	switch q.Type {
	case Noul:
		if err := inUnit("noul", a.Noul); err != nil {
			return err
		}
		if a.Choice != "" || a.Score != 0 {
			return wireErr("a noul answer carries only its probability")
		}
	case Choice:
		ok := false
		for _, o := range q.Options {
			if o.Name == a.Choice {
				ok = true
			}
		}
		if !ok {
			return wireErr("choice %q not in %v", a.Choice, q.Names())
		}
		if err := inUnit("confidence", a.Confidence); err != nil {
			return err
		}
		for k, v := range a.Probabilities {
			known := false
			for _, o := range q.Options {
				if o.Name == k {
					known = true
				}
			}
			if !known {
				return wireErr("probability for %q, which is not an option", k)
			}
			if err := inUnit("probability", v); err != nil {
				return err
			}
		}
		if err := distributionSums(a.Probabilities); err != nil {
			return err
		}
	case Score:
		top := float64(len(q.Levels) - 1)
		if math.IsNaN(a.Score) || a.Score < 0 || a.Score > top {
			return wireErr("score %v out of [0,%v]", a.Score, top)
		}
		if err := inUnit("confidence", a.Confidence); err != nil {
			return err
		}
		for k, v := range a.Probabilities {
			i, err := strconv.Atoi(k)
			if err != nil || i < 0 || i >= len(q.Levels) {
				return wireErr("probability key %q is not a level index", k)
			}
			if err := inUnit("probability", v); err != nil {
				return err
			}
		}
		if err := distributionSums(a.Probabilities); err != nil {
			return err
		}
	}
	return nil
}

// Usage is what a provider reported about the call.
type Usage struct {
	InputTokens  int64 `json:"input_tokens"`
	OutputTokens int64 `json:"output_tokens"`
}

// Response is one provider's whole answer set.
type Response struct {
	Model   string
	Answers map[string]Answer
	Order   []string
	Usage   Usage
}

// Validate checks the response answers exactly the request's questions.
func (r Response) Validate(req Request) error {
	for _, id := range req.Order {
		a, ok := r.Answers[id]
		if !ok {
			return wireErr("no answer for question %q", id)
		}
		if err := a.Validate(req.Questions[id]); err != nil {
			return fmt.Errorf("answer %q: %w", id, err)
		}
	}
	for id := range r.Answers {
		if _, ok := req.Questions[id]; !ok {
			return wireErr("answer for %q, which was not asked", id)
		}
	}
	return nil
}

// One returns the single answer of a single-question response.
func (r Response) One() (Answer, error) {
	if len(r.Answers) != 1 {
		return Answer{}, wireErr("expected one answer, got %d", len(r.Answers))
	}
	for _, a := range r.Answers {
		return a, nil
	}
	return Answer{}, wireErr("unreachable")
}

// ---- codec: byte-deterministic, strict -----------------------------------

func mustJSON(v any) []byte {
	b, err := json.Marshal(v)
	if err != nil {
		// only unencodable values reach here; the caller validated first
		b, _ = json.Marshal(fmt.Sprint(v))
	}
	return b
}

// EncodeRequest renders the request as the System One wire body. The
// bytes are a pure function of the Request: question order is the
// Request's, and every nested object is written in a declared order.
func EncodeRequest(r Request) ([]byte, error) {
	if err := r.Validate(); err != nil {
		return nil, err
	}
	state, err := encodeState(r.State)
	if err != nil {
		return nil, err
	}
	var b bytes.Buffer
	b.WriteString(`{"model":`)
	b.Write(mustJSON(r.Model))
	b.WriteString(`,"state":`)
	b.Write(state)
	b.WriteString(`,"questions":{`)
	for i, id := range r.Order {
		if i > 0 {
			b.WriteByte(',')
		}
		b.Write(mustJSON(id))
		b.WriteByte(':')
		b.Write(encodeQuestion(r.Questions[id]))
	}
	b.WriteString("}}")
	return b.Bytes(), nil
}

func encodeQuestion(q Question) []byte {
	var b bytes.Buffer
	b.WriteString(`{"type":`)
	b.Write(mustJSON(string(q.Type)))
	b.WriteString(`,"instructions":`)
	b.Write(mustJSON(q.Instructions))
	switch q.Type {
	case Choice:
		b.WriteString(`,"criteria":{`)
		for i, o := range q.Options {
			if i > 0 {
				b.WriteByte(',')
			}
			b.Write(mustJSON(o.Name))
			b.WriteByte(':')
			if o.Description == "" {
				b.WriteString("null")
			} else {
				b.Write(mustJSON(o.Description))
			}
		}
		b.WriteString(`}`)
	case Score:
		b.WriteString(`,"criteria":[`)
		for i, l := range q.Levels {
			if i > 0 {
				b.WriteByte(',')
			}
			b.Write(mustJSON(l))
		}
		b.WriteString(`]`)
	case Noul:
		if q.True != "" {
			b.WriteString(`,"criteria":{"true":`)
			b.Write(mustJSON(q.True))
			b.WriteString(`,"false":`)
			b.Write(mustJSON(q.False))
			b.WriteString(`}`)
		}
	}
	b.WriteString("}")
	return b.Bytes()
}

// encodeState writes the state: an ordered State as an ordered object,
// raw JSON verbatim, anything else through encoding/json (which sorts map
// keys, so it too is deterministic).
func encodeState(s any) ([]byte, error) {
	switch v := s.(type) {
	case State:
		var b bytes.Buffer
		b.WriteByte('{')
		for i, sec := range v.Sections {
			if i > 0 {
				b.WriteByte(',')
			}
			b.Write(mustJSON(sec.Key))
			b.WriteByte(':')
			b.Write(mustJSON(sec.Text))
		}
		b.WriteByte('}')
		return b.Bytes(), nil
	case json.RawMessage:
		if !json.Valid(v) {
			return nil, wireErr("state is not valid JSON")
		}
		return v, nil
	default:
		b, err := json.Marshal(s)
		if err != nil {
			return nil, wireErr("state: %v", err)
		}
		return b, nil
	}
}

func strictDecode(b []byte, into any) error {
	if err := noDuplicateKeys(b); err != nil {
		return err
	}
	dec := json.NewDecoder(bytes.NewReader(b))
	dec.DisallowUnknownFields()
	if err := dec.Decode(into); err != nil {
		return wireErr("%v", err)
	}
	// EOF, not More(): More() is false at a stray closing delimiter, so a
	// reply followed by an extra `}` or `]` would pass as clean
	if _, err := dec.Token(); err != io.EOF {
		return wireErr("trailing content after the JSON object")
	}
	return nil
}

// noDuplicateKeys refuses an object (at any depth) that names a key twice:
// encoding/json keeps the LAST value, so {"choice":"a","choice":"b"} would
// resolve to b with a straight face. A judgement is never last-wins.
func noDuplicateKeys(b []byte) error {
	dec := json.NewDecoder(bytes.NewReader(b))
	type frame struct {
		object bool
		seen   map[string]bool
		key    bool // an object frame is expecting a key next
	}
	var stack []*frame
	for {
		t, err := dec.Token()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return wireErr("%v", err)
		}
		switch v := t.(type) {
		case json.Delim:
			switch v {
			case '{':
				stack = append(stack, &frame{object: true, seen: map[string]bool{}, key: true})
				continue
			case '[':
				stack = append(stack, &frame{})
				continue
			default:
				stack = stack[:len(stack)-1]
			}
		case string:
			if n := len(stack); n > 0 && stack[n-1].object && stack[n-1].key {
				if stack[n-1].seen[v] {
					return wireErr("key %q appears twice", v)
				}
				stack[n-1].seen[v] = true
				stack[n-1].key = false
				continue
			}
		}
		// a value was consumed: an object frame expects a key again
		if n := len(stack); n > 0 && stack[n-1].object {
			stack[n-1].key = true
		}
	}
}

// distributionSums refuses a reported distribution that is not one: every
// value in range but a total of 3.0 (or 0.2) is not a distribution, and
// Value() would read a number off it as if it were. A small tolerance
// covers rounding in an llm-written reply.
func distributionSums(p map[string]float64) error {
	if len(p) == 0 {
		return nil
	}
	sum := 0.0
	for _, v := range p {
		sum += v
	}
	if math.Abs(sum-1) > 0.05 {
		return wireErr("probabilities sum to %.2f, not 1", sum)
	}
	return nil
}

// objectOrder lists an object's keys in the order they appear in the
// bytes (encoding/json gives a map, which has none).
func objectOrder(raw []byte) ([]string, error) {
	dec := json.NewDecoder(bytes.NewReader(raw))
	t, err := dec.Token()
	if err != nil || t != json.Delim('{') {
		return nil, wireErr("expected a JSON object")
	}
	var order []string
	depth := 0
	for dec.More() {
		t, err := dec.Token()
		if err != nil {
			return nil, wireErr("%v", err)
		}
		k, ok := t.(string)
		if !ok || depth != 0 {
			return nil, wireErr("expected an object key")
		}
		order = append(order, k)
		var skip json.RawMessage
		if err := dec.Decode(&skip); err != nil {
			return nil, wireErr("%v", err)
		}
	}
	return order, nil
}

type wireQuestion struct {
	Type         string          `json:"type"`
	Instructions string          `json:"instructions"`
	Criteria     json.RawMessage `json:"criteria,omitempty"`
}

// DecodeRequest reads a wire request back. It preserves question order,
// so a decoded request re-encodes to the same bytes.
func DecodeRequest(b []byte) (Request, error) {
	var w struct {
		Model     string                     `json:"model"`
		State     json.RawMessage            `json:"state"`
		Questions map[string]json.RawMessage `json:"questions"`
	}
	if err := strictDecode(b, &w); err != nil {
		return Request{}, err
	}
	var qraw struct {
		Questions json.RawMessage `json:"questions"`
	}
	_ = json.Unmarshal(b, &qraw)
	order, err := objectOrder(qraw.Questions)
	if err != nil {
		return Request{}, fmt.Errorf("questions: %w", err)
	}
	r := Request{Model: w.Model, State: w.State, Questions: map[string]Question{}, Order: order}
	for id, raw := range w.Questions {
		q, err := decodeQuestion(raw)
		if err != nil {
			return Request{}, fmt.Errorf("question %q: %w", id, err)
		}
		r.Questions[id] = q
	}
	if err := r.Validate(); err != nil {
		return Request{}, err
	}
	return r, nil
}

func decodeQuestion(raw []byte) (Question, error) {
	var w wireQuestion
	if err := strictDecode(raw, &w); err != nil {
		return Question{}, err
	}
	q := Question{Type: Kind(w.Type), Instructions: w.Instructions}
	switch q.Type {
	case Choice:
		order, err := objectOrder(w.Criteria)
		if err != nil {
			return q, fmt.Errorf("criteria: %w", err)
		}
		var m map[string]*string
		if err := strictDecode(w.Criteria, &m); err != nil {
			return q, fmt.Errorf("criteria: %w", err)
		}
		for _, name := range order {
			d := ""
			if m[name] != nil {
				d = *m[name]
			}
			q.Options = append(q.Options, Option{Name: name, Description: d})
		}
	case Score:
		if err := strictDecode(w.Criteria, &q.Levels); err != nil {
			return q, fmt.Errorf("criteria: %w", err)
		}
	case Noul:
		if len(w.Criteria) > 0 {
			var m struct {
				True  string `json:"true"`
				False string `json:"false"`
			}
			if err := strictDecode(w.Criteria, &m); err != nil {
				return q, fmt.Errorf("criteria: %w", err)
			}
			q.True, q.False = m.True, m.False
		}
	}
	if err := q.Validate(); err != nil {
		return q, err
	}
	return q, nil
}

type wireAnswer struct {
	Type          string             `json:"type"`
	Noul          *float64           `json:"noul,omitempty"`
	Choice        string             `json:"choice,omitempty"`
	Score         *float64           `json:"score,omitempty"`
	Confidence    *float64           `json:"confidence,omitempty"`
	Probabilities map[string]float64 `json:"probabilities,omitempty"`
	Legend        map[string]string  `json:"legend,omitempty"`
	Why           string             `json:"why,omitempty"`
	Falsifiers    []string           `json:"falsifiers,omitempty"`
}

// EncodeResponse writes a response in the wire shape (what the pcd
// sidecar and the llm arm both produce for the report).
func EncodeResponse(r Response) ([]byte, error) {
	var b bytes.Buffer
	b.WriteString(`{"model":`)
	b.Write(mustJSON(r.Model))
	b.WriteString(`,"answers":{`)
	order := r.Order
	if len(order) == 0 {
		for id := range r.Answers {
			order = append(order, id)
		}
		sort.Strings(order)
	}
	for i, id := range order {
		if i > 0 {
			b.WriteByte(',')
		}
		b.Write(mustJSON(id))
		b.WriteByte(':')
		a := r.Answers[id]
		w := wireAnswer{Type: string(a.Type), Choice: a.Choice, Probabilities: a.Probabilities, Legend: a.Legend, Why: a.Why, Falsifiers: a.Falsifiers}
		switch a.Type {
		case Noul:
			n := a.Noul
			w.Noul = &n
		case Choice:
			c := a.Confidence
			w.Confidence = &c
		case Score:
			s, c := a.Score, a.Confidence
			w.Score, w.Confidence = &s, &c
		}
		b.Write(mustJSON(w))
	}
	b.WriteString(`},"usage":`)
	b.Write(mustJSON(r.Usage))
	b.WriteString("}")
	return b.Bytes(), nil
}

// DecodeResponse reads a wire response. Nothing is guessed: a missing
// typed value, an out-of-range number, or an unknown key is an error.
func DecodeResponse(b []byte) (Response, error) {
	var w struct {
		Model   string                     `json:"model"`
		Answers map[string]json.RawMessage `json:"answers"`
		Usage   Usage                      `json:"usage"`
	}
	if err := strictDecode(b, &w); err != nil {
		return Response{}, err
	}
	var araw struct {
		Answers json.RawMessage `json:"answers"`
	}
	_ = json.Unmarshal(b, &araw)
	order, err := objectOrder(araw.Answers)
	if err != nil {
		return Response{}, fmt.Errorf("answers: %w", err)
	}
	r := Response{Model: w.Model, Answers: map[string]Answer{}, Order: order, Usage: w.Usage}
	for id, raw := range w.Answers {
		a, err := decodeAnswer(raw)
		if err != nil {
			return Response{}, fmt.Errorf("answer %q: %w", id, err)
		}
		r.Answers[id] = a
	}
	return r, nil
}

func decodeAnswer(raw []byte) (Answer, error) {
	var w wireAnswer
	if err := strictDecode(raw, &w); err != nil {
		return Answer{}, err
	}
	a := Answer{Type: Kind(w.Type), Choice: w.Choice, Probabilities: w.Probabilities, Legend: w.Legend, Why: w.Why, Falsifiers: w.Falsifiers}
	if !kinds[a.Type] {
		return a, wireErr("answer type %q out of vocabulary", w.Type)
	}
	switch a.Type {
	case Noul:
		if w.Noul == nil {
			return a, wireErr("a noul answer carries its probability")
		}
		a.Noul = *w.Noul
	case Choice:
		if w.Choice == "" {
			return a, wireErr("a choice answer carries its choice")
		}
		if w.Confidence == nil {
			return a, wireErr("a choice answer carries its confidence")
		}
		a.Confidence = *w.Confidence
	case Score:
		if w.Score == nil || w.Confidence == nil {
			return a, wireErr("a score answer carries its score and confidence")
		}
		a.Score, a.Confidence = *w.Score, *w.Confidence
	}
	return a, nil
}
