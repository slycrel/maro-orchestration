package verdict

import (
	"context"
	"errors"
	"io"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

var subj = record.Ref{Kind: "run", ID: "run-1"}
var claim = thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Deliverable, Bytes: 3, Encoding: thought.UTF8}

func hdr() record.Header {
	return record.Header{ID: record.NewID(), Subject: subj, At: time.Now().UTC(), Seq: 1}
}

func v(kind VerdictKind, outcome string, st Standing, conf float64) *Verdict {
	src := Source{Standing: st}
	if st == StandingJudge {
		src.Ref = record.NewID()
	}
	return &Verdict{Header: hdr(), VerdictKind: kind, Outcome: outcome, Confidence: conf, Source: src, Direction: directionFor[st]}
}

func o(res ObsResult, conf float64) *Observation {
	return &Observation{Header: hdr(), Check: CheckPathExists, Claim: claim, Result: res, Confidence: conf}
}

func perms(n int) [][]int {
	var out [][]int
	var rec func(cur []int, used []bool)
	rec = func(cur []int, used []bool) {
		if len(cur) == n {
			out = append(out, append([]int{}, cur...))
			return
		}
		for i := 0; i < n; i++ {
			if !used[i] {
				used[i] = true
				rec(append(cur, i), used)
				used[i] = false
			}
		}
	}
	rec(nil, make([]bool, n))
	return out
}

// The resolution is a function of the SET: every arrival order of the same
// verdicts and observations yields an identical Resolution.
func TestResolutionIsOrderIndependent(t *testing.T) {
	sets := [][]*Verdict{
		{v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingSelf, 0.9), v(KindClosure, "achieved", StandingOperator, 1)},
		{v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingJudge, 0.8)}, // contested
		{v(KindStep, "done", StandingSelf, 0.9), v(KindStep, "blocked", StandingJudge, 0.5), v(KindStep, "done", StandingJudge, 0.7), v(KindStep, "unclear", StandingDeterministic, 0.6)},
	}
	obsSets := [][]*Observation{{o(Refuted, 0.95), o(Supported, 0.99)}, {}, {o(CouldNotObserve, 1)}}
	for si, set := range sets {
		var first *Resolution
		for _, p := range perms(len(set)) {
			ordered := make([]*Verdict, len(set))
			for i, ix := range p {
				ordered[i] = set[ix]
			}
			obs := append([]*Observation{}, obsSets[si]...)
			for i, j := 0, len(obs)-1; i < j; i, j = i+1, j-1 {
				obs[i], obs[j] = obs[j], obs[i]
			}
			r := Resolve(Candidates{Subject: subj, VerdictKind: set[0].VerdictKind, Verdicts: ordered, Observations: obs}, DefaultThresholds)
			if first == nil {
				first = &r
				continue
			}
			if r.Outcome != first.Outcome || r.Effective != first.Effective || r.Rule != first.Rule || r.Contested != first.Contested || strings.Join(ids(r.Candidates), ",") != strings.Join(ids(first.Candidates), ",") {
				t.Fatalf("set %d: order changed the resolution:\n%+v\n%+v", si, r, first)
			}
		}
	}
}

func ids(rs []record.RecordID) []string {
	out := make([]string, len(rs))
	for i, r := range rs {
		out[i] = string(r)
	}
	return out
}

func TestStandingAndConfidenceRank(t *testing.T) {
	judge := v(KindClosure, "not_achieved", StandingJudge, 0.99)
	op := v(KindClosure, "achieved", StandingOperator, 0.6)
	r := Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{judge, op}}, DefaultThresholds)
	if r.Effective != op.ID || r.Outcome != "achieved" || !strings.HasPrefix(r.Rule, "standing:operator") {
		t.Fatalf("operator must outrank a more confident judge: %+v", r)
	}
	a, b := v(KindClosure, "achieved", StandingJudge, 0.7), v(KindClosure, "not_achieved", StandingJudge, 0.9)
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{a, b}}, DefaultThresholds)
	if r.Effective != b.ID {
		t.Fatalf("within a rank, confidence decides: %+v", r)
	}
}

// Equal standing and confidence with different outcomes is CONTESTED: no
// effective verdict, the kind's unknown, never a tie broken by id or order.
func TestContestedTie(t *testing.T) {
	a, b := v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingJudge, 0.8)
	r := Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{a, b}}, DefaultThresholds)
	if !r.Contested || r.Effective != "" || r.Outcome != "unknown" || !strings.HasPrefix(r.Rule, "contested") {
		t.Fatalf("%+v", r)
	}
	// agreeing peers are not contested
	c := v(KindClosure, "achieved", StandingJudge, 0.8)
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{a, c}}, DefaultThresholds)
	if r.Contested || r.Outcome != "achieved" || !strings.Contains(r.Rule, "agree:2") {
		t.Fatalf("%+v", r)
	}
}

// A self-verdict can demote but never establish success.
func TestSelfCannotPromote(t *testing.T) {
	self := v(KindStep, "done", StandingSelf, 0.99)
	r := Resolve(Candidates{Subject: subj, VerdictKind: KindStep, Verdicts: []*Verdict{self}}, DefaultThresholds)
	if r.Effective != "" || r.Outcome != "unclear" || r.Rule != "self_cannot_promote" {
		t.Fatalf("%+v", r)
	}
	selfBlocked := v(KindStep, "blocked", StandingSelf, 0.5)
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindStep, Verdicts: []*Verdict{selfBlocked}}, DefaultThresholds)
	if r.Effective != selfBlocked.ID || r.Outcome != "blocked" {
		t.Fatalf("self may demote: %+v", r)
	}
	judge := v(KindStep, "done", StandingJudge, 0.6)
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindStep, Verdicts: []*Verdict{self, judge}}, DefaultThresholds)
	if r.Effective != judge.ID || r.Outcome != "done" {
		t.Fatalf("a judge establishes success over a self claim: %+v", r)
	}
}

// Supersession is a set operation on Supersedes links, not on Seq.
func TestSupersession(t *testing.T) {
	old := v(KindClosure, "achieved", StandingJudge, 0.9)
	newer := v(KindClosure, "not_achieved", StandingJudge, 0.5)
	newer.Supersedes = old.ID
	r := Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{old, newer}}, DefaultThresholds)
	if r.Effective != newer.ID || r.Outcome != "not_achieved" {
		t.Fatalf("superseded verdict still won: %+v", r)
	}
	if len(r.Candidates) != 2 {
		t.Fatal("superseded verdicts remain named as candidates")
	}
}

// A refuting observation at the threshold settles failure without a judge;
// could_not_observe and supported settle nothing; an operator outranks it.
func TestRefutationSettlesFailure(t *testing.T) {
	judge := v(KindClosure, "achieved", StandingJudge, 0.9)
	r := Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{judge}, Observations: []*Observation{o(Refuted, 0.95)}}, DefaultThresholds)
	if r.Outcome != "not_achieved" || r.Effective != "" || !strings.HasPrefix(r.Rule, "refuted_by_observation") {
		t.Fatalf("%+v", r)
	}
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{judge}, Observations: []*Observation{o(Refuted, 0.5), o(CouldNotObserve, 1), o(Supported, 1)}}, DefaultThresholds)
	if r.Outcome != "achieved" {
		t.Fatalf("weak/unobservable/supporting must not refute: %+v", r)
	}
	op := v(KindClosure, "achieved", StandingOperator, 1)
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindClosure, Verdicts: []*Verdict{op}, Observations: []*Observation{o(Refuted, 1)}}, DefaultThresholds)
	if r.Outcome != "achieved" || r.Effective != op.ID {
		t.Fatalf("an operator outranks a check: %+v", r)
	}
	// kinds without a failure outcome ignore observations
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindStep, Verdicts: []*Verdict{v(KindStep, "done", StandingJudge, 0.7)}, Observations: []*Observation{o(Refuted, 1)}}, DefaultThresholds)
	if r.Outcome != "done" {
		t.Fatalf("%+v", r)
	}
	// no candidates at all
	r = Resolve(Candidates{Subject: subj, VerdictKind: KindClosure}, DefaultThresholds)
	if r.Outcome != "unknown" || r.Rule != "no_candidates" {
		t.Fatalf("%+v", r)
	}
	if DefaultThresholds.RefuteWhy == "" {
		t.Fatal("a threshold without a why")
	}
}

// Vocabularies are closed and direction is fixed by standing.
func TestWireValidation(t *testing.T) {
	good := v(KindClosure, "achieved", StandingJudge, 0.5)
	good.Schema = "verdict/1"
	if err := good.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	bad := *good
	bad.Outcome = "done"
	if err := bad.ValidateWire(); err == nil {
		t.Fatal("outcome from another kind accepted")
	}
	bad = *good
	bad.Direction = MayPromote
	if err := bad.ValidateWire(); err == nil {
		t.Fatal("direction disagreeing with standing accepted")
	}
	bad = *good
	bad.Source = Source{Standing: StandingJudge}
	if err := bad.ValidateWire(); err == nil {
		t.Fatal("judge without invocation accepted")
	}
	bad = *good
	bad.Confidence = 1.5
	if err := bad.ValidateWire(); err == nil {
		t.Fatal("confidence > 1 accepted")
	}
	bad = *good
	bad.VerdictKind = KindStep
	bad.Outcome = "done"
	bad.Falsifiers = []thought.Ref{claim}
	if err := bad.ValidateWire(); err == nil {
		t.Fatal("falsifiers on a non-closure verdict accepted")
	}
	ob := o(Refuted, 0.5)
	ob.Schema = "observation/1"
	if err := ob.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	ob.Check = "vibes"
	if err := ob.ValidateWire(); err == nil {
		t.Fatal("unknown check accepted")
	}
	res := Resolution{Header: hdr(), VerdictKind: KindClosure, Outcome: "unknown", ResolverVer: ResolverVer, Rule: "x", Contested: true}
	res.Schema = "resolution/1"
	if err := res.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	res.Effective = record.NewID()
	if err := res.ValidateWire(); err == nil {
		t.Fatal("contested with an effective verdict accepted")
	}
	res.Contested, res.ResolverVer = false, "resolver/0"
	if err := res.ValidateWire(); err == nil {
		t.Fatal("foreign resolver version accepted")
	}
}

func openJ(t *testing.T) *journal.Journal {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, _ := workspace.Resolve()
	a, _ := r.Announce(io.Discard)
	a.Ensure()
	l, err := workspace.Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Release() })
	j, err := journal.Open(l)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { j.Close() })
	return j
}

// Committed resolutions: journaled, folded by (subject, kind), idempotent
// per candidate set; a new candidate makes a new resolution that names the
// old candidates too.
func TestCommitAndFold(t *testing.T) {
	j := openJ(t)
	ctx := context.Background()
	put := func(key string, r record.Record) {
		t.Helper()
		spec, _ := record.Lookup(r.Kind())
		r.Head().Schema = record.SchemaVer(string(r.Kind()) + "/1")
		_ = spec
		r.Head().Seq = 0
		if _, err := j.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: []record.Record{r}}); err != nil {
			t.Fatal(err)
		}
	}
	a := v(KindClosure, "achieved", StandingJudge, 0.8)
	put("a", a)
	ob := o(Refuted, 0.5)
	put("o", ob)
	groups, err := Fold(j.Production())
	if err != nil {
		t.Fatal(err)
	}
	g := groups["run/run-1/closure"]
	if g == nil || len(g.Verdicts) != 1 || len(g.Observations) != 1 {
		t.Fatalf("%+v", groups)
	}
	res, err := Commit(ctx, j, "run-1", 1, *g, DefaultThresholds)
	if err != nil || res.Outcome != "achieved" {
		t.Fatalf("%v %+v", err, res)
	}
	if _, err := Commit(ctx, j, "run-1", 1, *g, DefaultThresholds); !errors.Is(err, ErrAlreadyResolved) {
		t.Fatalf("same set resolved twice: %v", err)
	}
	op := v(KindClosure, "not_achieved", StandingOperator, 1)
	put("op", op)
	groups, _ = Fold(j.Production())
	res2, err := Commit(ctx, j, "run-1", 1, *groups["run/run-1/closure"], DefaultThresholds)
	if err != nil || res2.Outcome != "not_achieved" || len(res2.Candidates) != 2 {
		t.Fatalf("%v %+v", err, res2)
	}
	// resolutions are themselves journaled and readable
	n := 0
	j.Production().Scan(0, func(r record.Record) error {
		if _, ok := r.(*Resolution); ok {
			n++
		}
		return nil
	})
	if n != 2 {
		t.Fatalf("%d resolutions journaled", n)
	}
}
