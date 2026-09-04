package verdict

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// ResolverVer names the resolver's rules as data; a change is a new version
// and every Resolution says which one decided it.
const ResolverVer = "resolver/1"

// Thresholds are the registered numbers the resolver consults, each with a
// Why (D13: reported, never magic).
type Thresholds struct {
	Refute    float64 // an observation at or above this confidence settles failure without a judge
	RefuteWhy string
}

// DefaultThresholds is the v1 registration.
var DefaultThresholds = Thresholds{
	Refute:    0.9,
	RefuteWhy: "a deterministic check that is 90%+ sure a claim is false outranks a model judge that has not seen the check; below that the judge decides (v1 registration; re-measure against observation accuracy once live)",
}

// Candidates is the input to a resolution: every verdict and observation
// for one (subject, kind), in ANY order.
type Candidates struct {
	Subject      record.Ref
	VerdictKind  VerdictKind
	Verdicts     []*Verdict
	Observations []*Observation
}

// Resolve is the pure fold. It is order-independent by construction: inputs
// are sorted by record ID (never Seq); supersession is applied as a set
// operation; the result depends only on the set. Rules, in order:
//  1. supersession — a verdict named by another's Supersedes is dropped.
//  2. refutation — for kinds with a failure outcome, a refuting observation
//     at/above the threshold settles the failure outcome unless an OPERATOR
//     verdict exists (operators outrank checks).
//  3. standing — the highest standing rank wins; within a rank the highest
//     confidence; equal rank and confidence with DIFFERENT outcomes is
//     contested (no effective verdict; outcome = the kind's unknown, or the
//     agreed outcome when they agree).
//  4. self cannot promote — if the winner is a self-verdict claiming the
//     kind's success outcome, the resolution's outcome is the kind's unknown
//     (the verdict stands as a claim; success needs a judge or an operator).
func Resolve(c Candidates, th Thresholds) Resolution {
	res := Resolution{VerdictKind: c.VerdictKind, ResolverVer: ResolverVer}
	vs := append([]*Verdict{}, c.Verdicts...)
	sort.Slice(vs, func(i, j int) bool { return vs[i].ID < vs[j].ID })
	superseded := map[record.RecordID]bool{}
	for _, v := range vs {
		if v.Supersedes != "" {
			superseded[v.Supersedes] = true
		}
	}
	var live []*Verdict
	for _, v := range vs {
		res.Candidates = append(res.Candidates, v.ID)
		if !superseded[v.ID] && v.VerdictKind == c.VerdictKind {
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
	// rule 2: refutation settles failure without a judge
	if fail, ok := failureOutcome[c.VerdictKind]; ok && !hasOperator {
		var best *Observation
		for _, o := range obs {
			if o.Result == Refuted && o.Confidence >= th.Refute && (best == nil || o.Confidence > best.Confidence) {
				best = o
			}
		}
		if best != nil {
			res.Outcome, res.Confidence, res.Rule = fail, best.Confidence, "refuted_by_observation:"+string(best.Check)
			return res
		}
	}
	if len(live) == 0 {
		res.Outcome, res.Rule = unknownFor(c.VerdictKind), "no_candidates"
		return res
	}
	// rule 3: standing, then confidence
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
	if !agree {
		res.Contested, res.Outcome, res.Confidence, res.Rule = true, unknownFor(c.VerdictKind), top.Confidence, fmt.Sprintf("contested:%d_peers_at_%s", len(peers), top.Source.Standing)
		return res
	}
	res.Effective, res.Outcome, res.Confidence = top.ID, top.Outcome, top.Confidence
	res.Rule = "standing:" + string(top.Source.Standing)
	if len(peers) > 1 {
		res.Rule += fmt.Sprintf("+agree:%d", len(peers))
	}
	// rule 4: self cannot promote
	if top.Source.Standing == StandingSelf && top.Outcome == successOutcomes[c.VerdictKind] {
		res.Effective, res.Outcome, res.Rule = "", unknownFor(c.VerdictKind), "self_cannot_promote"
	}
	return res
}

func unknownFor(k VerdictKind) string {
	if u, ok := unknownOutcome[k]; ok {
		return u
	}
	// kinds with no unknown (stuck, delivery) fall to their non-success outcome
	switch k {
	case KindStuck:
		return "stuck"
	case KindDelivery:
		return "undeliverable"
	}
	return ""
}

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
// grouped by (subject, kind).
func Fold(pr *journal.ProductionReader) (map[string]*Candidates, error) {
	groups := map[string]*Candidates{}
	key := func(s record.Ref, k VerdictKind) string { return string(s.Kind) + "/" + s.ID + "/" + string(k) }
	get := func(s record.Ref, k VerdictKind) *Candidates {
		g, ok := groups[key(s, k)]
		if !ok {
			g = &Candidates{Subject: s, VerdictKind: k}
			groups[key(s, k)] = g
		}
		return g
	}
	err := pr.Scan(0, func(r record.Record) error {
		switch v := r.(type) {
		case *Verdict:
			get(v.Subject, v.VerdictKind).Verdicts = append(get(v.Subject, v.VerdictKind).Verdicts, v)
		case *Observation:
			// an observation is about a subject for every kind it can settle
			for k := range failureOutcome {
				get(v.Subject, k).Observations = append(get(v.Subject, k).Observations, v)
			}
		}
		return nil
	})
	return groups, err
}

// Commit writes a Resolution for a candidate set, idempotent by (subject,
// kind, candidate set): the same set resolves once.
func Commit(ctx context.Context, j *journal.Journal, run record.RunID, attempt uint32, c Candidates, th Thresholds) (*Resolution, error) {
	res := Resolve(c, th)
	res.Header = record.Header{ID: record.NewID(), Schema: "resolution/1", RunID: run, Attempt: attempt, Subject: c.Subject, At: time.Now().UTC()}
	h := sha256.New()
	h.Write([]byte(string(c.Subject.Kind) + "/" + c.Subject.ID + "/" + string(c.VerdictKind)))
	for _, id := range res.Candidates {
		h.Write([]byte(id))
	}
	for _, id := range res.Observations {
		h.Write([]byte(id))
	}
	key := "resolution:" + hex.EncodeToString(h.Sum(nil))
	ack, err := j.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: []record.Record{&res}})
	if err != nil {
		return nil, err
	}
	if ack.Replayed {
		return &res, ErrAlreadyResolved
	}
	return &res, nil
}

// ErrAlreadyResolved: this exact candidate set was resolved before; the
// returned Resolution is what it would compute again.
var ErrAlreadyResolved = errors.New("verdict: candidate set already resolved")
