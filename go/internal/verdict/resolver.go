package verdict

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// ResolverVer names the resolver's rules as data; a change is a new version
// and every Resolution says which one decided it.
const ResolverVer = "resolver/1"

// Thresholds are the registered numbers the resolver consults, each with a
// Why (D13: reported, never magic). Validated before any fold.
type Thresholds struct {
	Refute     float64 `json:"refute"`
	RefuteWhy  string  `json:"refute_why"`
	Promote    float64 `json:"promote"`
	PromoteWhy string  `json:"promote_why"`
}

// DefaultThresholds is the v1 registration.
var DefaultThresholds = Thresholds{
	Refute:     0.9,
	RefuteWhy:  "a deterministic check that is 90%+ sure a claim is false outranks a model judge that has not seen the check; below that the judge decides (v1 registration; re-measure against observation accuracy once live)",
	Promote:    0.5,
	PromoteWhy: "a judge that claims success at under even odds is abstaining, not judging: standing alone must not turn a zero-confidence 'achieved' into a settled success; demotions stand at any confidence (v1 registration; re-measure against closure accuracy once live)",
}

func (t Thresholds) validate() error {
	if math.IsNaN(t.Refute) || t.Refute < 0 || t.Refute > 1 {
		return fmt.Errorf("%w: refute threshold %v not in [0,1]", ErrCandidates, t.Refute)
	}
	if t.RefuteWhy == "" {
		return fmt.Errorf("%w: refute threshold has no why", ErrCandidates)
	}
	if math.IsNaN(t.Promote) || t.Promote < 0 || t.Promote > 1 {
		return fmt.Errorf("%w: promote threshold %v not in [0,1]", ErrCandidates, t.Promote)
	}
	if t.PromoteWhy == "" {
		return fmt.Errorf("%w: promote threshold has no why", ErrCandidates)
	}
	return nil
}

// Candidates is the input to a resolution: every verdict and observation
// for one (subject, kind), in ANY order.
type Candidates struct {
	Subject      record.Ref
	VerdictKind  VerdictKind
	Verdicts     []*Verdict
	Observations []*Observation
}

var (
	ErrCandidates   = errors.New("verdict: invalid candidates")
	ErrSupersession = errors.New("verdict: invalid supersession")
)

// validate refuses a candidate collection the fold must not touch: empty
// subject, a verdict or observation for another subject or kind, an
// unknown kind, duplicate ids, any record failing its wire validation
// (NaN confidence, foreign vocabulary), a verdict without a Seq (the
// supersession rule needs order), and a bad threshold.
func (c Candidates) validate(th Thresholds) error {
	if err := th.validate(); err != nil {
		return err
	}
	if c.Subject.Kind == "" || c.Subject.ID == "" {
		return fmt.Errorf("%w: empty subject", ErrCandidates)
	}
	if _, ok := outcomes[c.VerdictKind]; !ok {
		return fmt.Errorf("%w: kind %q", ErrCandidates, c.VerdictKind)
	}
	seen := map[record.RecordID]bool{}
	for _, v := range c.Verdicts {
		if v == nil {
			return fmt.Errorf("%w: nil verdict", ErrCandidates)
		}
		if seen[v.ID] {
			return fmt.Errorf("%w: duplicate id %s", ErrCandidates, v.ID)
		}
		seen[v.ID] = true
		if v.Subject != c.Subject || v.VerdictKind != c.VerdictKind {
			return fmt.Errorf("%w: verdict %s is for %v/%s, not %v/%s", ErrCandidates, v.ID, v.Subject, v.VerdictKind, c.Subject, c.VerdictKind)
		}
		if v.Seq == 0 {
			return fmt.Errorf("%w: verdict %s has no Seq — resolve only committed verdicts", ErrCandidates, v.ID)
		}
		if err := v.ValidateWire(); err != nil {
			return fmt.Errorf("%w: %v", ErrCandidates, err)
		}
	}
	for _, o := range c.Observations {
		if o == nil {
			return fmt.Errorf("%w: nil observation", ErrCandidates)
		}
		if seen[o.ID] {
			return fmt.Errorf("%w: duplicate id %s", ErrCandidates, o.ID)
		}
		seen[o.ID] = true
		if o.Subject != c.Subject {
			return fmt.Errorf("%w: observation %s is for %v, not %v", ErrCandidates, o.ID, o.Subject, c.Subject)
		}
		if !Applies(o.Check, c.VerdictKind) {
			return fmt.Errorf("%w: observation %s (%s) does not apply to %s", ErrCandidates, o.ID, o.Check, c.VerdictKind)
		}
		if err := o.ValidateWire(); err != nil {
			return fmt.Errorf("%w: %v", ErrCandidates, err)
		}
	}
	return nil
}

// supersession builds the tombstone set from a validated candidate set:
// every link must name a present verdict of this (subject, kind) with a
// lower Seq and a standing no higher than the replacement's; the graph must
// be acyclic. An invalid link is an error and NOTHING is dropped.
func supersession(vs []*Verdict) (map[record.RecordID]bool, error) {
	byID := map[record.RecordID]*Verdict{}
	for _, v := range vs {
		byID[v.ID] = v
	}
	dead := map[record.RecordID]bool{}
	for _, v := range vs {
		if v.Supersedes == "" {
			continue
		}
		t, ok := byID[v.Supersedes]
		if !ok {
			return nil, fmt.Errorf("%w: %s supersedes %s, which is not a candidate verdict of this subject and kind", ErrSupersession, v.ID, v.Supersedes)
		}
		if t.Seq >= v.Seq {
			return nil, fmt.Errorf("%w: %s (seq %d) supersedes %s (seq %d) — a replacement must come later", ErrSupersession, v.ID, v.Seq, t.ID, t.Seq)
		}
		if standingRank[v.Source.Standing] < standingRank[t.Source.Standing] {
			return nil, fmt.Errorf("%w: %s (%s) may not supersede %s (%s) — lower standing cannot erase higher", ErrSupersession, v.ID, v.Source.Standing, t.ID, t.Source.Standing)
		}
		dead[v.Supersedes] = true
	}
	// acyclic: follow links; Seq strictly decreasing along a chain guarantees it, so a cycle would have failed the Seq check above
	return dead, nil
}

// Resolve is the pure fold. Order-independent by construction: inputs are
// sorted by record ID for a stable walk, but no rule ever decides by ID or
// Seq except the supersession check (Seq strictly increasing along a link).
// Rules, in order:
//  1. supersession (validated, see above)
//  2. refutation — for kinds with a failure outcome, every refuting
//     observation at/above the threshold is decisive unless an OPERATOR
//     verdict exists; the rule names ALL decisive observations.
//  3. standing — highest rank, then highest confidence; equal maxima with
//     different outcomes ⇒ contested (no effective; the kind's unknown);
//     equal maxima that AGREE ⇒ the outcome, with NO effective verdict named
//     (agreed maxima are incomparable — no ID picks one).
//  4. self cannot promote — a lone self winner claiming success ⇒ unknown.
func Resolve(c Candidates, th Thresholds) (Resolution, error) {
	if err := c.validate(th); err != nil {
		return Resolution{}, err
	}
	res := Resolution{VerdictKind: c.VerdictKind, ResolverVer: ResolverVer, Thresholds: th}
	vs := append([]*Verdict{}, c.Verdicts...)
	sort.Slice(vs, func(i, j int) bool { return vs[i].ID < vs[j].ID })
	dead, err := supersession(vs)
	if err != nil {
		return Resolution{}, err
	}
	var live []*Verdict
	for _, v := range vs {
		res.Candidates = append(res.Candidates, v.ID)
		if !dead[v.ID] {
			live = append(live, v)
		}
	}
	obs := append([]*Observation{}, c.Observations...)
	sort.Slice(obs, func(i, j int) bool { return obs[i].ID < obs[j].ID })
	for _, o := range obs {
		res.Observations = append(res.Observations, o.ID)
	}
	hasOperator := false
	for _, v := range live {
		if v.Source.Standing == StandingOperator {
			hasOperator = true
		}
	}
	// rule 2
	if fail, ok := failureOutcome[c.VerdictKind]; ok && !hasOperator {
		var decisive []*Observation
		for _, o := range obs {
			if o.Result == Refuted && o.Confidence >= th.Refute {
				decisive = append(decisive, o)
			}
		}
		if len(decisive) > 0 {
			maxc := 0.0
			names := ""
			for _, o := range decisive {
				if o.Confidence > maxc {
					maxc = o.Confidence
				}
				names += string(o.Check) + "@" + string(o.ID) + ";"
				res.Decisive = append(res.Decisive, o.ID)
			}
			res.Outcome, res.Confidence, res.Rule = fail, maxc, "refuted_by_observation:"+names
			return res, nil
		}
	}
	if len(live) == 0 {
		res.Outcome, res.Rule = unknownFor(c.VerdictKind), "no_candidates"
		return res, nil
	}
	// rule 3
	top := live[0]
	for _, v := range live[1:] {
		if better(v, top) {
			top = v
		}
	}
	var peers []*Verdict
	for _, v := range live {
		if standingRank[v.Source.Standing] == standingRank[top.Source.Standing] && v.Confidence == top.Confidence {
			peers = append(peers, v)
		}
	}
	agree := true
	for _, p := range peers {
		if p.Outcome != top.Outcome {
			agree = false
		}
	}
	res.Confidence = top.Confidence
	switch {
	case !agree:
		res.Contested, res.Outcome, res.Rule = true, unknownFor(c.VerdictKind), fmt.Sprintf("contested:%d_peers_at_%s", len(peers), top.Source.Standing)
		return res, nil
	case len(peers) > 1:
		// agreed maxima: the outcome is settled, no single verdict is "the" effective one
		res.Outcome, res.Rule = top.Outcome, fmt.Sprintf("agreed_maxima:%d_at_%s", len(peers), top.Source.Standing)
		for _, p := range peers {
			res.Decisive = append(res.Decisive, p.ID)
		}
	default:
		res.Effective, res.Outcome, res.Rule = top.ID, top.Outcome, "standing:"+string(top.Source.Standing)
	}
	// rule 4: success needs standing — self cannot promote
	if top.Source.Standing == StandingSelf && top.Outcome == successOutcomes[c.VerdictKind] {
		res.Effective, res.Decisive, res.Outcome, res.Rule = "", nil, unknownFor(c.VerdictKind), "self_cannot_promote"
		return res, nil
	}
	// rule 5: success needs confidence — a judge or check claiming success
	// below the promote threshold is abstaining; an operator is not
	if top.Outcome == successOutcomes[c.VerdictKind] && top.Source.Standing != StandingOperator && top.Confidence < th.Promote {
		res.Effective, res.Decisive, res.Outcome, res.Rule = "", nil, unknownFor(c.VerdictKind), fmt.Sprintf("below_promote_threshold:%v<%v", top.Confidence, th.Promote)
	}
	return res, nil
}

func unknownFor(k VerdictKind) string { return unknownOutcome[k] }

// better is the partial order: standing rank, then confidence. Equal on
// both is incomparable — never broken by ID or Seq.
func better(a, b *Verdict) bool {
	ra, rb := standingRank[a.Source.Standing], standingRank[b.Source.Standing]
	if ra != rb {
		return ra > rb
	}
	return a.Confidence > b.Confidence
}

// Fold reads every verdict and observation from the production journal,
// grouped by (subject, kind); an observation joins the groups its check
// applies to. Records failing decode (wire validation runs there) fail the
// fold: a corrupt journal is an error, not a plausible set.
func Fold(pr *journal.ProductionReader) (map[string]*Candidates, error) {
	groups := map[string]*Candidates{}
	get := func(s record.Ref, k VerdictKind) *Candidates {
		key := groupKey(s, k)
		g, ok := groups[key]
		if !ok {
			g = &Candidates{Subject: s, VerdictKind: k}
			groups[key] = g
		}
		return g
	}
	err := pr.Scan(0, func(r record.Record) error {
		switch v := r.(type) {
		case *Verdict:
			g := get(v.Subject, v.VerdictKind)
			g.Verdicts = append(g.Verdicts, v)
		case *Observation:
			for _, k := range applicability[v.Check] {
				g := get(v.Subject, k)
				g.Observations = append(g.Observations, v)
			}
		}
		return nil
	})
	return groups, err
}

func groupKey(s record.Ref, k VerdictKind) string {
	return string(s.Kind) + "/" + s.ID + "/" + string(k)
}

// keyOf is the idempotency key: canonical JSON of everything the fold
// depends on (version, subject, kind, thresholds, candidate and observation
// id lists — length-delimited by construction), so no two different sets
// collide and a threshold change is a different resolution.
func keyOf(c Candidates, res Resolution) string {
	payload, _ := json.Marshal(struct {
		Ver  string            `json:"ver"`
		Sub  record.Ref        `json:"subject"`
		Kind VerdictKind       `json:"kind"`
		Th   Thresholds        `json:"thresholds"`
		C    []record.RecordID `json:"candidates"`
		O    []record.RecordID `json:"observations"`
	}{ResolverVer, c.Subject, c.VerdictKind, res.Thresholds, res.Candidates, res.Observations})
	sum := sha256.Sum256(payload)
	return "resolution:" + hex.EncodeToString(sum[:])
}

// Commit writes a Resolution for a candidate set, idempotent by the key
// above. On replay it returns the resolution that was COMMITTED (read back
// from the journal), never a recomputation.
func Commit(ctx context.Context, j *journal.Journal, run record.RunID, attempt uint32, c Candidates, th Thresholds) (*Resolution, error) {
	res, err := Resolve(c, th)
	if err != nil {
		return nil, err
	}
	res.Header = record.Header{ID: record.NewID(), Schema: "resolution/1", RunID: run, Attempt: attempt, Subject: c.Subject, At: time.Now().UTC()}
	key := keyOf(c, res)
	ack, err := j.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: []record.Record{&res}})
	if err != nil {
		return nil, err
	}
	if ack.Replayed {
		var committed *Resolution
		scanErr := j.Production().ScanThrough(ack.FirstSeq-1, ack.LastSeq, func(r record.Record) error {
			if rr, ok := r.(*Resolution); ok {
				committed = rr
			}
			return nil
		})
		if scanErr != nil || committed == nil {
			return nil, fmt.Errorf("verdict: replayed resolution at seq %d could not be read back: %v", ack.FirstSeq, scanErr)
		}
		return committed, ErrAlreadyResolved
	}
	return &res, nil
}

// ErrAlreadyResolved: this exact candidate set was resolved before; the
// returned Resolution is the one on record.
var ErrAlreadyResolved = errors.New("verdict: candidate set already resolved")

// Current picks, per (subject, kind), the resolution whose input set (candidates ∪
// observations) is MAXIMAL by inclusion among the journaled resolutions.
// Two resolutions with incomparable sets are contested and yield no current
// resolution for that key (an error naming both). Never decided by Seq or ID.
func Current(pr *journal.ProductionReader) (map[string]*Resolution, error) {
	byKey := map[string][]*Resolution{}
	verdicts := map[record.RecordID]*Verdict{}
	observations := map[record.RecordID]*Observation{}
	err := pr.Scan(0, func(r record.Record) error {
		switch x := r.(type) {
		case *Verdict:
			verdicts[x.ID] = x
		case *Observation:
			observations[x.ID] = x
		case *Resolution:
			// a resolution is trusted only when it re-derives from what it names
			if err := Check(x, verdicts, observations); err != nil {
				return err
			}
			k := groupKey(x.Subject, x.VerdictKind)
			byKey[k] = append(byKey[k], x)
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	out := map[string]*Resolution{}
	for k, rs := range byKey {
		var maximal []*Resolution
		for _, a := range rs {
			dominated := false
			for _, b := range rs {
				if a != b && includes(b, a) && !includes(a, b) {
					dominated = true
					break
				}
			}
			if !dominated {
				maximal = append(maximal, a)
			}
		}
		// equal sets (same inputs, e.g. different thresholds) are also incomparable
		if len(maximal) != 1 {
			ids := ""
			for _, m := range maximal {
				ids += string(m.ID) + " "
			}
			return nil, fmt.Errorf("verdict: %s has %d incomparable resolutions (%s) — no current resolution", k, len(maximal), ids)
		}
		out[k] = maximal[0]
	}
	return out, nil
}

func includes(a, b *Resolution) bool {
	set := map[record.RecordID]bool{}
	for _, id := range a.Candidates {
		set[id] = true
	}
	for _, id := range a.Observations {
		set[id] = true
	}
	for _, id := range b.Candidates {
		if !set[id] {
			return false
		}
	}
	for _, id := range b.Observations {
		if !set[id] {
			return false
		}
	}
	return true
}

// Check recomputes a journaled Resolution from the candidate and
// observation records it names and refuses any disagreement: a resolution
// that cannot be re-derived from committed standing is a record that lies.
// byID must hold every named record (a missing one is a refusal, not a
// skip). Pure; the run fold calls it on every recorded outcome's closure.
func Check(res *Resolution, verdicts map[record.RecordID]*Verdict, observations map[record.RecordID]*Observation) error {
	c := Candidates{Subject: res.Subject, VerdictKind: res.VerdictKind}
	for _, id := range res.Candidates {
		v := verdicts[id]
		if v == nil {
			return fmt.Errorf("verdict: resolution %s names candidate %s that is not committed", res.ID, id)
		}
		c.Verdicts = append(c.Verdicts, v)
	}
	for _, id := range res.Observations {
		o := observations[id]
		if o == nil {
			return fmt.Errorf("verdict: resolution %s names observation %s that is not committed", res.ID, id)
		}
		c.Observations = append(c.Observations, o)
	}
	if res.ResolverVer != ResolverVer {
		return fmt.Errorf("verdict: resolution %s is from %s; this resolver is %s", res.ID, res.ResolverVer, ResolverVer)
	}
	re, err := Resolve(c, res.Thresholds)
	if err != nil {
		return fmt.Errorf("verdict: resolution %s does not re-derive: %w", res.ID, err)
	}
	if re.Outcome != res.Outcome || re.Effective != res.Effective || re.Rule != res.Rule || re.Contested != res.Contested || re.Confidence != res.Confidence || !sameIDs(re.Decisive, res.Decisive) {
		return fmt.Errorf("verdict: resolution %s disagrees with its recompute (%s/%s/%s vs %s/%s/%s)", res.ID, res.Outcome, res.Effective, res.Rule, re.Outcome, re.Effective, re.Rule)
	}
	return nil
}

func sameIDs(a, b []record.RecordID) bool {
	if len(a) != len(b) {
		return false
	}
	seen := map[record.RecordID]int{}
	for _, x := range a {
		seen[x]++
	}
	for _, x := range b {
		if seen[x] == 0 {
			return false
		}
		seen[x]--
	}
	return true
}
