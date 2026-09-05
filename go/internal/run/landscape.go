package run

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"unicode"

	"github.com/slycrel/maro-orchestration/go/internal/invoke"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// The landscape (feature-related-runs, 2026-09-05): before its first
// attempt, a goal with no operator-chosen lineage looks at the workspace's
// prior runs and DECIDES its relation to them — the run decides, not the
// operator (Jeremy: "the orchestrator doesn't have the data to make a
// better decision than maro"). Candidate selection is deterministic and
// re-derived by the fold; the relation is one recorded judge call, made
// only when candidates exist. The decision is a record on the run:
//
//   fresh   — nothing here bears on the goal; it is the root of its lineage
//   related — a prior run bears on it: the goal FOLLOWS that run (its
//             lineage, so scoped memory walks to it) and the prior's
//             delivered answer rides into the goal's requests as context
//   rerun   — the prior asked the same thing: follows it, its answer is
//             context, and its plan is offered to the planner to reuse or
//             revise; a rerun still runs
//
// `--after` (Driver.After) is the operator override: the goal record
// carries the lineage and no landscape is read. `--fresh` (Driver.Fresh)
// records a landscape that was skipped, with no call.

const KindLandscape record.Kind = "landscape"

// Relation is the decided relation of a goal to a prior run.
type Relation string

const (
	RelationFresh   Relation = "fresh"
	RelationRelated Relation = "related"
	RelationRerun   Relation = "rerun"
)

var relations = map[Relation]bool{RelationFresh: true, RelationRelated: true, RelationRerun: true}

// The landscape rule names how the relation was decided.
const (
	LandscapeJudge         = "judge"            // candidates existed; the judge decided
	LandscapeNoCandidates  = "no_candidates"    // nothing above the floor: fresh, no call
	LandscapeUnreadable    = "judge_unreadable" // the judge answered outside the contract: fresh, the call recorded
	LandscapeFreshOverride = "fresh_override"   // the operator skipped the landscape (--fresh)
)

var landscapeRules = map[string]bool{LandscapeJudge: true, LandscapeNoCandidates: true, LandscapeUnreadable: true, LandscapeFreshOverride: true}

// Candidate selection parameters — recorded on every landscape so the day
// lexical overlap is the wrong instrument, the record shows what it did.
const (
	LandscapeFloor = 0.2 // token-overlap floor (Jaccard over goal words)
	LandscapeTopK  = 3
	// RelatedHead bounds the delivered answer that rides as context.
	RelatedHead = 2000
	// LandscapePromptVer is the judge prompt template the driver renders.
	// The fold re-derives a landscape's request byte for byte, so a
	// template change is a new version, recorded on the landscape; the
	// old text stays so history renders as it was asked. (An absent
	// version on the record is 1: the template at the record's birth.)
	LandscapePromptVer = 2
)

var landscapeContract = map[int]string{
	1: `{"relation": "fresh" | "related" | "rerun", "run": "<candidate number, or 0 for fresh>", "reason": "<one sentence>"}`,
	2: `{"relation": "fresh" | "related" | "rerun", "run": <the candidate's number (1, 2, …) or its run id, or 0 for fresh>, "reason": "<one sentence>"}`,
}

// LandscapeCandidate is a prior run the judge was shown.
type LandscapeCandidate struct {
	Run        record.RunID    `json:"run"`
	Goal       record.RecordID `json:"goal"`
	Similarity float64         `json:"similarity"`
}

// Landscape is the decision record: run-scoped, before attempt 1 (attempt
// 0), subject the run. It carries the goal so a run that died between the
// landscape and its first attempt still binds to its goal on the fold.
type Landscape struct {
	record.ProductionRecord
	record.Header `json:"header"`
	Goal          record.RecordID      `json:"goal"`
	AsOf          uint64               `json:"as_of"` // the journal head the scan read; a run terminal after it was not seen
	Rule          string               `json:"rule"`
	Floor         float64              `json:"floor"`
	TopK          int                  `json:"top_k"`
	Scanned       int                  `json:"scanned"`     // prior production runs the scan saw
	BelowFloor    int                  `json:"below_floor"` // of those, excluded by the floor
	Candidates    []LandscapeCandidate `json:"candidates,omitempty"`
	Relation      Relation             `json:"relation"`
	Chosen        record.RunID         `json:"chosen,omitempty"`
	Reason        string               `json:"reason,omitempty"`
	Judge         record.RecordID      `json:"judge,omitempty"`      // the judge invocation; absent when no call was made
	PromptVer     int                  `json:"prompt_ver,omitempty"` // the judge prompt template (0 = 1, the first)
}

func (r *Landscape) Head() *record.Header { return &r.Header }
func (r *Landscape) Kind() record.Kind    { return KindLandscape }
func (r *Landscape) ValidateWire() error {
	if err := r.Header.ValidateWire(); err != nil {
		return err
	}
	if r.RunID == "" || r.Attempt != 0 || r.Subject.Kind != "run" || r.Subject.ID != string(r.RunID) {
		return errors.New("landscape: subject must be the run, before its first attempt")
	}
	if err := record.ValidateID(r.Goal); err != nil {
		return fmt.Errorf("landscape: goal: %w", err)
	}
	if !landscapeRules[r.Rule] {
		return fmt.Errorf("landscape: rule %q out of vocabulary", r.Rule)
	}
	if !relations[r.Relation] {
		return fmt.Errorf("landscape: relation %q out of vocabulary", r.Relation)
	}
	if r.Floor < 0 || r.Floor > 1 || r.TopK <= 0 || r.Scanned < 0 || r.BelowFloor < 0 || r.BelowFloor > r.Scanned {
		return errors.New("landscape: floor in [0,1], top_k positive, below_floor within scanned")
	}
	if len(r.Candidates) > r.TopK {
		return errors.New("landscape: more candidates than top_k")
	}
	seen := map[record.RunID]bool{}
	for _, c := range r.Candidates {
		if c.Run == "" || record.ValidateID(c.Goal) != nil || c.Similarity < r.Floor || c.Similarity > 1 || seen[c.Run] {
			return errors.New("landscape: a candidate names a run and goal, sits at or above the floor, and appears once")
		}
		seen[c.Run] = true
	}
	switch r.Rule {
	case LandscapeJudge, LandscapeUnreadable:
		if len(r.Candidates) == 0 || record.ValidateID(r.Judge) != nil {
			return errors.New("landscape: a judged landscape has candidates and names its judge invocation")
		}
	default:
		if len(r.Candidates) != 0 || r.Judge != "" || r.Relation != RelationFresh {
			return fmt.Errorf("landscape: rule %s is fresh with no candidates and no call", r.Rule)
		}
	}
	if r.Rule == LandscapeUnreadable && r.Relation != RelationFresh {
		return errors.New("landscape: an unreadable judge decides nothing: fresh")
	}
	if (r.Relation == RelationFresh) != (r.Chosen == "") {
		return errors.New("landscape: related/rerun name the chosen run; fresh names none")
	}
	if r.Chosen != "" && !seen[r.Chosen] {
		return errors.New("landscape: the chosen run is not a candidate")
	}
	if r.Judge == "" && r.PromptVer != 0 {
		return errors.New("landscape: a prompt version names a call that was not made")
	}
	if r.Judge != "" && landscapeContract[promptVer(r.PromptVer)] == "" {
		return fmt.Errorf("landscape: prompt version %d is not one the engine ever rendered", r.PromptVer)
	}
	return nil
}

// promptVer reads the recorded template version: absent is the first.
func promptVer(v int) int {
	if v == 0 {
		return 1
	}
	return v
}

// goalWords tokenizes goal text for the overlap measure: lower-cased runs
// of letters/digits of length ≥ 3.
func goalWords(text []byte) map[string]bool {
	words := map[string]bool{}
	var cur []rune
	flush := func() {
		if len(cur) >= 3 {
			words[string(cur)] = true
		}
		cur = cur[:0]
	}
	for _, r := range string(text) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			cur = append(cur, unicode.ToLower(r))
		} else {
			flush()
		}
	}
	flush()
	return words
}

// Similarity is the Jaccard overlap of two goals' word sets.
func Similarity(a, b []byte) float64 {
	wa, wb := goalWords(a), goalWords(b)
	if len(wa) == 0 || len(wb) == 0 {
		return 0
	}
	inter := 0
	for w := range wa {
		if wb[w] {
			inter++
		}
	}
	return float64(inter) / float64(len(wa)+len(wb)-inter)
}

// landscapeCandidates scans the ledger's prior production runs (terminal,
// origin cli/socket, not the goal's own run) and returns the top-K at or
// above the floor, by similarity then by run id (deterministic), with the
// scanned and below-floor counts. The fold re-executes this exactly.
func landscapeCandidates(runs map[record.RunID]*RunState, goal *Goal, goalText []byte, asOf uint64, get func(thought.Ref) ([]byte, error)) (cands []LandscapeCandidate, scanned, below int, err error) {
	var all []LandscapeCandidate
	for id, rs := range runs {
		if rs.Goal == nil || rs.Goal.ID == goal.ID || rs.TerminalAt == 0 || rs.TerminalAt > asOf || rs.Goal.Origin == OriginFork || rs.Goal.Origin == OriginReplay {
			continue
		}
		text, gerr := get(rs.Goal.Text)
		if gerr != nil {
			return nil, 0, 0, fmt.Errorf("landscape: goal text of run %s: %w", id, gerr)
		}
		scanned++
		sim := Similarity(goalText, text)
		if sim < LandscapeFloor {
			below++
			continue
		}
		all = append(all, LandscapeCandidate{Run: id, Goal: rs.Goal.ID, Similarity: sim})
	}
	sort.Slice(all, func(i, j int) bool {
		if all[i].Similarity != all[j].Similarity {
			return all[i].Similarity > all[j].Similarity
		}
		return all[i].Run > all[j].Run // newer id first
	})
	if len(all) > LandscapeTopK {
		all = all[:LandscapeTopK]
	}
	return all, scanned, below, nil
}

// deliveredHead is the head of a run's delivered answer ("" when the run
// recorded no response).
func deliveredHead(rs *RunState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	a := rs.Latest()
	if a == nil {
		return nil, nil
	}
	rec := a.Has(Recorded)
	if rec == nil || rec.Outcome.Response == nil {
		return nil, nil
	}
	b, err := get(*rec.Outcome.Response)
	if err != nil {
		return nil, err
	}
	if len(b) > RelatedHead {
		b = append(append([]byte{}, b[:RelatedHead]...), []byte("…")...)
	}
	return b, nil
}

// landscapePrompt is the judge's request: the goal and every candidate,
// with the answer contract. The fold re-derives it byte for byte.
func landscapePrompt(ver int, goalText []byte, cands []LandscapeCandidate, runs map[record.RunID]*RunState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	var b bytes.Buffer
	b.WriteString("You decide how a new goal relates to prior runs in this workspace. Answer with one JSON object and nothing else:\n")
	b.WriteString(landscapeContract[promptVer(ver)] + "\n")
	b.WriteString("fresh: no prior run bears on the goal. related: a prior run bears on it (a follow-up, an angle, a tangent) and its answer is useful context. rerun: a prior run asked the same thing.\n\n")
	b.WriteString("New goal:\n")
	b.Write(goalText)
	b.WriteString("\n")
	for i, c := range cands {
		rs := runs[c.Run]
		text, err := get(rs.Goal.Text)
		if err != nil {
			return nil, err
		}
		head, err := deliveredHead(rs, get)
		if err != nil {
			return nil, err
		}
		fmt.Fprintf(&b, "\nCandidate %d (run %s, similarity %.2f, outcome %s):\nGoal: %s\n", i+1, HandleOf(c.Run), c.Similarity, MissionOf(rs).Outcome, text)
		if len(head) > 0 {
			fmt.Fprintf(&b, "Answer: %s\n", head)
		}
	}
	return b.Bytes(), nil
}

type landscapeAnswer struct {
	Relation string `json:"relation"`
	Run      any    `json:"run"`
	Reason   string `json:"reason"`
}

// ParseLandscape reads the judge's answer against the candidates, under
// the contract of the template version it was asked with (the first
// named candidates by number only; the second also by run id). An answer
// outside the contract is an error — the caller records fresh under the
// unreadable rule, never a guess.
func ParseLandscape(ver int, resp []byte, cands []LandscapeCandidate) (Relation, record.RunID, string, error) {
	s := strings.TrimSpace(string(resp))
	if i, j := strings.Index(s, "{"), strings.LastIndex(s, "}"); i >= 0 && j > i {
		s = s[i : j+1]
	}
	var a landscapeAnswer
	if err := json.Unmarshal([]byte(s), &a); err != nil {
		return "", "", "", fmt.Errorf("landscape: answer is not the JSON contract: %v", err)
	}
	rel := Relation(strings.ToLower(strings.TrimSpace(a.Relation)))
	if !relations[rel] {
		return "", "", "", fmt.Errorf("landscape: relation %q out of vocabulary", a.Relation)
	}
	if rel == RelationFresh {
		return rel, "", strings.TrimSpace(a.Reason), nil
	}
	// the candidate by number (1-based) or by run id — judges answer with
	// the handle the prompt showed them as often as with the number
	n := 0
	switch v := a.Run.(type) {
	case float64:
		n = int(v)
	case string:
		v = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(v), "run "))
		for i, c := range cands {
			if promptVer(ver) >= 2 && (v == HandleOf(c.Run) || v == string(c.Run)) {
				n = i + 1
			}
		}
		if n == 0 {
			fmt.Sscanf(v, "%d", &n)
		}
	}
	if n < 1 || n > len(cands) {
		return "", "", "", fmt.Errorf("landscape: %s names candidate %v, which is not one of %d", rel, a.Run, len(cands))
	}
	return rel, cands[n-1].Run, strings.TrimSpace(a.Reason), nil
}

// RelatedContext renders the chosen run's answer (and, for a rerun, its
// plan) as the block that rides into the goal's requests. Empty for a
// fresh landscape. The fold re-derives it.
func RelatedContext(rs *RunState, runs map[record.RunID]*RunState, get func(thought.Ref) ([]byte, error)) ([]byte, error) {
	ls := rs.Landscape
	if ls == nil || ls.Relation == RelationFresh {
		return nil, nil
	}
	prior := runs[ls.Chosen]
	if prior == nil || prior.Goal == nil {
		return nil, fmt.Errorf("landscape: chosen run %s is not in the ledger", ls.Chosen)
	}
	text, err := get(prior.Goal.Text)
	if err != nil {
		return nil, err
	}
	head, err := deliveredHead(prior, get)
	if err != nil {
		return nil, err
	}
	var b bytes.Buffer
	fmt.Fprintf(&b, "\n\n## Related prior run (%s, %s)\nIts goal: %s\n", HandleOf(ls.Chosen), ls.Relation, text)
	if len(head) > 0 {
		fmt.Fprintf(&b, "Its answer:\n%s\n", head)
	} else {
		b.WriteString("It recorded no answer.\n")
	}
	if ls.Relation == RelationRerun {
		if a := prior.Latest(); a != nil && a.Plan != nil {
			b.WriteString("Its plan (reuse or revise):\n")
			for i, ref := range a.Plan.Steps {
				st, err := get(ref)
				if err != nil {
					return nil, err
				}
				fmt.Fprintf(&b, "%d. %s\n", i+1, st)
			}
		}
	}
	return b.Bytes(), nil
}

// landscape reads the landscape for a new run and commits the decision
// (idempotent by run). It is the run's first stage: the lineage every
// later selection is scoped over depends on it.
func (d *Driver) landscape(ctx context.Context, rs *RunState, goalText []byte) error {
	asOf := d.J.Head()
	led, err := Fold(d.J.Production(), d.Store)
	if err != nil {
		return err
	}
	if prior := led.Runs[rs.Run]; prior != nil && prior.Landscape != nil {
		rs.Landscape, rs.Related = prior.Landscape, prior.Related
		rs.Parent, rs.Root = prior.Parent, prior.Root
		return nil
	}
	ls := &Landscape{Header: header(runRef(rs.Run), rs.Run, 0, "landscape/1"), Goal: rs.Goal.ID, AsOf: asOf, Floor: LandscapeFloor, TopK: LandscapeTopK, Relation: RelationFresh}
	if d.Fresh {
		ls.Rule = LandscapeFreshOverride
	} else {
		cands, scanned, below, err := landscapeCandidates(led.Runs, rs.Goal, goalText, asOf, d.Store.Get)
		if err != nil {
			return err
		}
		ls.Candidates, ls.Scanned, ls.BelowFloor = cands, scanned, below
		if len(cands) == 0 {
			ls.Rule = LandscapeNoCandidates
		} else {
			ls.PromptVer = LandscapePromptVer
			prompt, err := landscapePrompt(LandscapePromptVer, goalText, cands, led.Runs, d.Store.Get)
			if err != nil {
				return err
			}
			b := d.Judge
			if b == nil {
				b = d.Backend
			}
			sh := &invoke.Shell{J: d.J, Store: d.Store, Run: rs.Run, Attempt: 0}
			o, err := sh.Invoke(ctx, b, invoke.Request{Purpose: invoke.PurposeLandscape, Prompt: prompt, Tools: false, Timeout: d.Timeout}, nil)
			if err != nil && !recordedFailure(o, err) {
				return err
			}
			ls.Judge = o.Invocation
			if o.Terminal == invoke.TerminalFailed {
				ls.Rule, ls.Reason = LandscapeUnreadable, firstLine("judge failed: "+o.Reason)
			} else if rel, chosen, why, perr := ParseLandscape(LandscapePromptVer, o.Response, cands); perr != nil {
				ls.Rule, ls.Reason = LandscapeUnreadable, firstLine(perr.Error())
			} else {
				ls.Rule, ls.Relation, ls.Chosen, ls.Reason = LandscapeJudge, rel, chosen, why
			}
		}
	}
	if err := d.commit(ctx, "landscape/"+string(rs.Run), ls); err != nil {
		return err
	}
	rs.Landscape = ls
	rs.Parent, rs.Root = lineageOf(rs.Goal, ls, led.Runs)
	if rs.Related, err = RelatedContext(rs, led.Runs, d.Store.Get); err != nil {
		return err
	}
	d.emit(rs, 0, "landscape", "", fmt.Sprintf("%s (%s): %d candidate(s) of %d scanned", ls.Relation, ls.Rule, len(ls.Candidates), ls.Scanned))
	return d.crash("after_landscape")
}

// lineageOf derives a run's lineage: the goal's own when the operator (or
// a fork/replay) set it, else the landscape's chosen run's, else its own.
func lineageOf(g *Goal, ls *Landscape, runs map[record.RunID]*RunState) (parent, root record.RecordID) {
	if g.Parent != "" {
		return g.Parent, g.Root
	}
	if ls != nil && ls.Relation != RelationFresh {
		if prior := runs[ls.Chosen]; prior != nil && prior.Goal != nil {
			return prior.Goal.ID, prior.Root
		}
	}
	return "", g.ID
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		s = s[:i]
	}
	return strings.TrimSpace(s)
}
