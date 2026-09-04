package verdict

import (
	"context"
	"errors"
	"io"
	"math"
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
var other = record.Ref{Kind: "run", ID: "run-2"}
var claim = thought.Ref{Hash: "s256v1:" + strings.Repeat("ab", 32), Kind: thought.Deliverable, Bytes: 3, Encoding: thought.UTF8}

var seqCounter uint64

func hdr(s record.Ref) record.Header {
	seqCounter++
	return record.Header{ID: record.NewID(), Schema: "verdict/1", Subject: s, At: time.Now().UTC(), Seq: seqCounter}
}

func v(kind VerdictKind, outcome string, st Standing, conf float64) *Verdict {
	src := Source{Standing: st}
	if st == StandingJudge {
		src.Ref = record.NewID()
	}
	return &Verdict{Header: hdr(subj), VerdictKind: kind, Outcome: outcome, Confidence: conf, Source: src, Direction: directionFor[st]}
}

func o(check CheckKind, res ObsResult, conf float64) *Observation {
	h := hdr(subj)
	h.Schema = "observation/1"
	return &Observation{Header: h, Check: check, Claim: claim, Result: res, Confidence: conf}
}

func must(t *testing.T, c Candidates) Resolution {
	t.Helper()
	r, err := Resolve(c, DefaultThresholds)
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func cands(kind VerdictKind, vs []*Verdict, os ...*Observation) Candidates {
	return Candidates{Subject: subj, VerdictKind: kind, Verdicts: vs, Observations: os}
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

func same(a, b Resolution) bool {
	return a.Outcome == b.Outcome && a.Effective == b.Effective && a.Rule == b.Rule && a.Contested == b.Contested && a.Confidence == b.Confidence &&
		join(a.Candidates) == join(b.Candidates) && join(a.Observations) == join(b.Observations) && join(a.Decisive) == join(b.Decisive)
}

func join(rs []record.RecordID) string {
	s := ""
	for _, r := range rs {
		s += string(r) + ","
	}
	return s
}

// The resolution is a function of the SET: every arrival order of verdicts
// AND observations, and every renumbering of Seq that keeps supersession
// order, yields an identical Resolution.
func TestResolutionIsOrderIndependent(t *testing.T) {
	old := v(KindClosure, "achieved", StandingJudge, 0.9)
	sup := v(KindClosure, "not_achieved", StandingJudge, 0.5) // later Seq than old
	sup.Supersedes = old.ID
	sets := []struct {
		kind VerdictKind
		vs   []*Verdict
		os   []*Observation
	}{
		{KindClosure, []*Verdict{v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingSelf, 0.9), v(KindClosure, "achieved", StandingOperator, 1)}, []*Observation{o(CheckPathExists, Refuted, 0.95), o(CheckPathExists, Supported, 0.99)}},
		{KindClosure, []*Verdict{v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingJudge, 0.8)}, nil},
		{KindStep, []*Verdict{v(KindStep, "done", StandingSelf, 0.9), v(KindStep, "blocked", StandingJudge, 0.5), v(KindStep, "done", StandingJudge, 0.7), v(KindStep, "unclear", StandingDeterministic, 0.6)}, nil},
		{KindClosure, []*Verdict{old, sup, v(KindClosure, "achieved", StandingJudge, 0.5)}, []*Observation{o(CheckSymbolExists, Refuted, 0.9), o(CheckPathExists, Refuted, 0.9)}},
	}
	for si, set := range sets {
		var first *Resolution
		for _, p := range perms(len(set.vs)) {
			ordered := make([]*Verdict, len(set.vs))
			for i, ix := range p {
				ordered[i] = set.vs[ix]
			}
			for _, op := range perms(len(set.os)) {
				obs := make([]*Observation, len(set.os))
				for i, ix := range op {
					obs[i] = set.os[ix]
				}
				r := must(t, cands(set.kind, ordered, obs...))
				if first == nil {
					first = &r
					continue
				}
				if !same(r, *first) {
					t.Fatalf("set %d: order changed the resolution:\n%+v\n%+v", si, r, *first)
				}
			}
		}
	}
	// Seq renumbering that keeps supersession order changes nothing
	old.Seq, sup.Seq = 100, 200
	a := must(t, cands(KindClosure, []*Verdict{old, sup}))
	old.Seq, sup.Seq = 7, 9000
	b := must(t, cands(KindClosure, []*Verdict{old, sup}))
	if !same(a, b) || a.Effective != sup.ID {
		t.Fatalf("seq renumbering changed the resolution: %+v %+v", a, b)
	}
}

func TestStandingAndConfidenceRank(t *testing.T) {
	judge := v(KindClosure, "not_achieved", StandingJudge, 1.0)
	op := v(KindClosure, "achieved", StandingOperator, 0)
	r := must(t, cands(KindClosure, []*Verdict{judge, op}))
	if r.Effective != op.ID || r.Outcome != "achieved" || r.Rule != "standing:operator" {
		t.Fatalf("operator at confidence 0 must outrank a judge at 1.0: %+v", r)
	}
	a, b := v(KindClosure, "achieved", StandingJudge, 0.7), v(KindClosure, "not_achieved", StandingJudge, 0.9)
	if r := must(t, cands(KindClosure, []*Verdict{a, b})); r.Effective != b.ID {
		t.Fatalf("within a rank, confidence decides: %+v", r)
	}
	// two operators disagreeing at equal confidence: contested, not id-picked
	o1, o2 := v(KindClosure, "achieved", StandingOperator, 1), v(KindClosure, "not_achieved", StandingOperator, 1)
	if r := must(t, cands(KindClosure, []*Verdict{o1, o2})); !r.Contested || r.Outcome != "unknown" {
		t.Fatalf("%+v", r)
	}
}

// Equal maxima with different outcomes ⇒ contested; equal maxima that agree
// ⇒ the outcome with NO effective verdict named (no id picks one).
func TestTiesAreNeverBrokenByID(t *testing.T) {
	a, b := v(KindClosure, "achieved", StandingJudge, 0.8), v(KindClosure, "not_achieved", StandingJudge, 0.8)
	r := must(t, cands(KindClosure, []*Verdict{a, b}))
	if !r.Contested || r.Effective != "" || r.Outcome != "unknown" || len(r.Decisive) != 0 {
		t.Fatalf("%+v", r)
	}
	c := v(KindClosure, "achieved", StandingJudge, 0.8)
	r = must(t, cands(KindClosure, []*Verdict{a, c}))
	if r.Contested || r.Outcome != "achieved" || r.Effective != "" || len(r.Decisive) != 2 || !strings.HasPrefix(r.Rule, "agreed_maxima:2") {
		t.Fatalf("agreeing maxima must name both and no effective: %+v", r)
	}
}

func TestSelfCannotPromote(t *testing.T) {
	self := v(KindStep, "done", StandingSelf, 0.99)
	r := must(t, cands(KindStep, []*Verdict{self}))
	if r.Effective != "" || r.Outcome != "unclear" || r.Rule != "self_cannot_promote" {
		t.Fatalf("%+v", r)
	}
	selfBlocked := v(KindStep, "blocked", StandingSelf, 0.5)
	if r := must(t, cands(KindStep, []*Verdict{selfBlocked})); r.Effective != selfBlocked.ID || r.Outcome != "blocked" {
		t.Fatalf("self may demote: %+v", r)
	}
	judge := v(KindStep, "done", StandingJudge, 0.6)
	if r := must(t, cands(KindStep, []*Verdict{self, judge})); r.Effective != judge.ID || r.Outcome != "done" {
		t.Fatalf("a judge establishes success over a self claim: %+v", r)
	}
	// delivery/stuck: a lone self success claim yields UNKNOWN, never a manufactured failure
	sd := v(KindDelivery, "delivered", StandingSelf, 1)
	if r := must(t, cands(KindDelivery, []*Verdict{sd})); r.Outcome != "unknown" {
		t.Fatalf("%+v", r)
	}
	if r := must(t, cands(KindStuck, nil)); r.Outcome != "unknown" || r.Rule != "no_candidates" {
		t.Fatalf("no candidates must be unknown, not stuck: %+v", r)
	}
}

// Supersession is a validated graph: present target, later Seq, standing
// not lower; anything else is an error and nothing is dropped.
func TestSupersessionIsValidated(t *testing.T) {
	old := v(KindClosure, "achieved", StandingJudge, 0.9)
	newer := v(KindClosure, "not_achieved", StandingJudge, 0.5)
	newer.Supersedes = old.ID
	r := must(t, cands(KindClosure, []*Verdict{old, newer}))
	if r.Effective != newer.ID || len(r.Candidates) != 2 {
		t.Fatalf("%+v", r)
	}
	// dangling target
	dangling := v(KindClosure, "achieved", StandingJudge, 0.5)
	dangling.Supersedes = record.NewID()
	if _, err := Resolve(cands(KindClosure, []*Verdict{dangling}), DefaultThresholds); !errors.Is(err, ErrSupersession) {
		t.Fatalf("dangling: %v", err)
	}
	// backwards in Seq
	back := v(KindClosure, "achieved", StandingJudge, 0.5)
	later := v(KindClosure, "not_achieved", StandingJudge, 0.5)
	back.Supersedes = later.ID // back has the lower Seq
	if _, err := Resolve(cands(KindClosure, []*Verdict{back, later}), DefaultThresholds); !errors.Is(err, ErrSupersession) {
		t.Fatalf("earlier superseding later: %v", err)
	}
	// cycle (impossible under the Seq rule, and refused)
	x, y := v(KindClosure, "achieved", StandingJudge, 0.5), v(KindClosure, "not_achieved", StandingJudge, 0.5)
	x.Supersedes, y.Supersedes = y.ID, x.ID
	if _, err := Resolve(cands(KindClosure, []*Verdict{x, y}), DefaultThresholds); !errors.Is(err, ErrSupersession) {
		t.Fatalf("cycle: %v", err)
	}
	// lower standing cannot erase higher
	op := v(KindClosure, "achieved", StandingOperator, 1)
	self := v(KindClosure, "not_achieved", StandingSelf, 1)
	self.Supersedes = op.ID
	if _, err := Resolve(cands(KindClosure, []*Verdict{op, self}), DefaultThresholds); !errors.Is(err, ErrSupersession) {
		t.Fatalf("self superseding operator: %v", err)
	}
	jj := v(KindClosure, "achieved", StandingJudge, 1)
	s2 := v(KindClosure, "not_achieved", StandingSelf, 1)
	s2.Supersedes = jj.ID
	if _, err := Resolve(cands(KindClosure, []*Verdict{jj, s2}), DefaultThresholds); !errors.Is(err, ErrSupersession) {
		t.Fatalf("self superseding judge: %v", err)
	}
}

// A refuting observation at the threshold settles failure without a judge;
// all decisive observations are named; could_not_observe / supported / weak
// settle nothing; an operator outranks it; kinds the check does not apply
// to refuse the observation.
func TestRefutationSettlesFailure(t *testing.T) {
	judge := v(KindClosure, "achieved", StandingJudge, 0.9)
	r := must(t, cands(KindClosure, []*Verdict{judge}, o(CheckPathExists, Refuted, 0.9), o(CheckSymbolExists, Refuted, 0.9)))
	if r.Outcome != "not_achieved" || r.Effective != "" || !strings.HasPrefix(r.Rule, "refuted_by_observation:") || len(r.Decisive) != 2 {
		t.Fatalf("exactly at threshold, both decisive: %+v", r)
	}
	r = must(t, cands(KindClosure, []*Verdict{judge}, o(CheckPathExists, Refuted, 0.8999), o(CheckPathExists, CouldNotObserve, 1), o(CheckPathExists, Supported, 1)))
	if r.Outcome != "achieved" {
		t.Fatalf("just below / unobservable / supporting must not refute: %+v", r)
	}
	op := v(KindClosure, "achieved", StandingOperator, 1)
	if r := must(t, cands(KindClosure, []*Verdict{op}, o(CheckPathExists, Refuted, 1))); r.Outcome != "achieved" || r.Effective != op.ID {
		t.Fatalf("an operator outranks a check: %+v", r)
	}
	det := v(KindClosure, "achieved", StandingDeterministic, 1)
	if r := must(t, cands(KindClosure, []*Verdict{det}, o(CheckPathExists, Refuted, 1))); r.Outcome != "not_achieved" {
		t.Fatalf("a deterministic-standing verdict does not outrank a refuting check: %+v", r)
	}
	// applicability: a path check cannot refute fabrication; a stuck kind takes no observations
	if _, err := Resolve(cands(KindFabrication, []*Verdict{v(KindFabrication, "consistent", StandingJudge, 0.7)}, o(CheckPathExists, Refuted, 1)), DefaultThresholds); !errors.Is(err, ErrCandidates) {
		t.Fatalf("inapplicable observation accepted: %v", err)
	}
	if _, err := Resolve(cands(KindStuck, []*Verdict{v(KindStuck, "progressing", StandingJudge, 0.7)}, o(CheckPathExists, Refuted, 1)), DefaultThresholds); !errors.Is(err, ErrCandidates) {
		t.Fatalf("observation on stuck accepted: %v", err)
	}
	// an observation for another subject is refused
	ob := o(CheckPathExists, Refuted, 1)
	ob.Subject = other
	if _, err := Resolve(cands(KindClosure, []*Verdict{judge}, ob), DefaultThresholds); !errors.Is(err, ErrCandidates) {
		t.Fatalf("foreign-subject observation accepted: %v", err)
	}
	if _, err := Resolve(cands(KindClosure, []*Verdict{judge}), Thresholds{Refute: math.NaN(), RefuteWhy: "x"}); !errors.Is(err, ErrCandidates) {
		t.Fatal("NaN threshold accepted")
	}
	if _, err := Resolve(cands(KindClosure, []*Verdict{judge}), Thresholds{Refute: 0.5}); !errors.Is(err, ErrCandidates) {
		t.Fatal("threshold without why accepted")
	}
}

// Candidates are validated before any rule runs.
func TestCandidatesAreValidated(t *testing.T) {
	good := v(KindClosure, "achieved", StandingJudge, 0.5)
	cases := []struct {
		name string
		c    Candidates
	}{
		{"empty subject", Candidates{VerdictKind: KindClosure, Verdicts: []*Verdict{good}}},
		{"unknown kind", Candidates{Subject: subj, VerdictKind: "mood", Verdicts: []*Verdict{good}}},
		{"mixed subject", func() Candidates {
			x := v(KindClosure, "achieved", StandingJudge, 0.5)
			x.Subject = other
			return cands(KindClosure, []*Verdict{good, x})
		}()},
		{"mixed kind", cands(KindClosure, []*Verdict{good, v(KindStep, "done", StandingJudge, 0.5)})},
		{"duplicate id", cands(KindClosure, []*Verdict{good, good})},
		{"nan confidence", cands(KindClosure, []*Verdict{v(KindClosure, "achieved", StandingJudge, math.NaN())})},
		{"foreign outcome", cands(KindClosure, []*Verdict{v(KindClosure, "done", StandingJudge, 0.5)})},
		{"unsequenced", func() Candidates {
			x := v(KindClosure, "achieved", StandingJudge, 0.5)
			x.Seq = 0
			return cands(KindClosure, []*Verdict{x})
		}()},
		{"nil", cands(KindClosure, []*Verdict{nil})},
	}
	for _, c := range cases {
		if _, err := Resolve(c.c, DefaultThresholds); !errors.Is(err, ErrCandidates) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
}

func TestWireValidation(t *testing.T) {
	good := v(KindClosure, "achieved", StandingJudge, 0.5)
	if err := good.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	for name, mut := range map[string]func(*Verdict){
		"foreign outcome":   func(b *Verdict) { b.Outcome = "done" },
		"wrong direction":   func(b *Verdict) { b.Direction = MayPromote },
		"judge without ref": func(b *Verdict) { b.Source = Source{Standing: StandingJudge} },
		"confidence > 1":    func(b *Verdict) { b.Confidence = 1.5 },
		"nan":               func(b *Verdict) { b.Confidence = math.NaN() },
		"falsifiers on step": func(b *Verdict) {
			b.VerdictKind, b.Outcome, b.Falsifiers = KindStep, "done", []thought.Ref{claim}
		},
	} {
		bad := *good
		mut(&bad)
		if err := bad.ValidateWire(); err == nil {
			t.Fatalf("%s accepted", name)
		}
	}
	ob := o(CheckPathExists, Refuted, 0.5)
	if err := ob.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	ob.Check = "vibes"
	if err := ob.ValidateWire(); err == nil {
		t.Fatal("unknown check accepted")
	}
	// resolution consistency
	cid := record.NewID()
	base := Resolution{Header: hdr(subj), VerdictKind: KindClosure, Outcome: "achieved", Effective: cid, Candidates: []record.RecordID{cid}, ResolverVer: ResolverVer, Thresholds: DefaultThresholds, Rule: "standing:judge", Confidence: 0.5}
	base.Schema = "resolution/1"
	if err := base.ValidateWire(); err != nil {
		t.Fatal(err)
	}
	for name, mut := range map[string]func(*Resolution){
		"effective not a candidate": func(r *Resolution) { r.Effective = record.NewID() },
		"contested with effective":  func(r *Resolution) { r.Contested = true },
		"contested wrong outcome": func(r *Resolution) {
			r.Contested, r.Effective, r.Rule = true, "", "contested:2_peers_at_judge"
		},
		"foreign resolver":   func(r *Resolution) { r.ResolverVer = "resolver/0" },
		"unknown rule":       func(r *Resolution) { r.Rule = "vibes" },
		"agreed no decisive": func(r *Resolution) { r.Rule, r.Effective = "agreed_maxima:2_at_judge", "" },
		"self promote named": func(r *Resolution) { r.Rule = "self_cannot_promote" },
		"bad candidate id":   func(r *Resolution) { r.Candidates = []record.RecordID{"junk"} },
		"no why":             func(r *Resolution) { r.Thresholds.RefuteWhy = "" },
	} {
		bad := base
		bad.Candidates = append([]record.RecordID{}, base.Candidates...)
		mut(&bad)
		if err := bad.ValidateWire(); err == nil {
			t.Fatalf("%s accepted", name)
		}
	}
	// stuck/delivery have a real unknown
	if !outcomes[KindStuck]["unknown"] || !outcomes[KindDelivery]["unknown"] {
		t.Fatal("stuck/delivery need an unknown outcome")
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

func put(t *testing.T, j *journal.Journal, key string, r record.Record) {
	t.Helper()
	r.Head().Schema = record.SchemaVer(string(r.Kind()) + "/1")
	r.Head().Seq = 0
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: []record.Record{r}}); err != nil {
		t.Fatal(err)
	}
}

// Committed resolutions: journaled, folded by (subject, kind) with
// observations attached by applicability, idempotent per (set, thresholds)
// with the COMMITTED resolution returned on replay, different sets never
// colliding, and Current picking the maximal set.
func TestCommitFoldAndCurrent(t *testing.T) {
	j := openJ(t)
	ctx := context.Background()
	a := v(KindClosure, "achieved", StandingJudge, 0.8)
	put(t, j, "a", a)
	ob := o(CheckFabricationDiff, Refuted, 0.5) // applies to fabrication only
	put(t, j, "o", ob)
	groups, err := Fold(j.Production())
	if err != nil {
		t.Fatal(err)
	}
	g := groups["run/run-1/closure"]
	if g == nil || len(g.Verdicts) != 1 || len(g.Observations) != 0 {
		t.Fatalf("closure group: %+v", g)
	}
	if fg := groups["run/run-1/fabrication"]; fg == nil || len(fg.Observations) != 1 {
		t.Fatalf("fabrication group: %+v", fg)
	}
	res, err := Commit(ctx, j, "run-1", 1, *g, DefaultThresholds)
	if err != nil || res.Outcome != "achieved" {
		t.Fatalf("%v %+v", err, res)
	}
	again, err := Commit(ctx, j, "run-1", 1, *g, DefaultThresholds)
	if !errors.Is(err, ErrAlreadyResolved) || again.ID != res.ID {
		t.Fatalf("replay must return the committed resolution: %v %v/%v", err, again.ID, res.ID)
	}
	// a different threshold is a different resolution
	th := DefaultThresholds
	th.Refute = 0.95
	if _, err := Commit(ctx, j, "run-1", 1, *g, th); err != nil {
		t.Fatalf("threshold change must be a new resolution: %v", err)
	}
	// [A]+obs[B] vs [A,B]+[] must not collide: build two sets whose id lists concatenate identically
	op := v(KindClosure, "not_achieved", StandingOperator, 1)
	put(t, j, "op", op)
	groups, _ = Fold(j.Production())
	res2, err := Commit(ctx, j, "run-1", 1, *groups["run/run-1/closure"], DefaultThresholds)
	if err != nil || res2.Outcome != "not_achieved" || len(res2.Candidates) != 2 {
		t.Fatalf("%v %+v", err, res2)
	}
	// Current: the maximal set under the default thresholds wins; the
	// 0.95-threshold resolution over the smaller set is dominated
	cur, err := Current(j.Production())
	if err != nil {
		t.Fatal(err)
	}
	if c := cur["run/run-1/closure"]; c == nil || c.ID != res2.ID {
		t.Fatalf("current: %+v", c)
	}
	// two resolutions over the SAME set with different thresholds are incomparable when both are maximal
	put(t, j, "b", v(KindClosure, "achieved", StandingJudge, 0.1))
	groups, _ = Fold(j.Production())
	Commit(ctx, j, "run-1", 1, *groups["run/run-1/closure"], DefaultThresholds)
	Commit(ctx, j, "run-1", 1, *groups["run/run-1/closure"], th)
	if _, err := Current(j.Production()); err == nil || !strings.Contains(err.Error(), "incomparable") {
		t.Fatalf("equal maximal sets must be reported, not picked: %v", err)
	}
}

// The journal executes wire validation: a verdict with a foreign outcome is
// refused at submit, and a hand-forged frame is refused at decode.
func TestJournalExecutesWireRules(t *testing.T) {
	j := openJ(t)
	bad := v(KindClosure, "done", StandingOperator, 1)
	bad.Schema = "verdict/1"
	bad.Seq = 0
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "bad", Epoch: j.Epoch(), Records: []record.Record{bad}}); err == nil || !strings.Contains(err.Error(), "wire") {
		t.Fatalf("foreign outcome accepted by the journal: %v", err)
	}
	nan := v(KindClosure, "achieved", StandingJudge, math.NaN())
	nan.Schema, nan.Seq = "verdict/1", 0
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "nan", Epoch: j.Epoch(), Records: []record.Record{nan}}); err == nil {
		t.Fatal("NaN confidence accepted by the journal")
	}
	if j.Head() != 0 {
		t.Fatal("refused records were written")
	}
}

// Success needs confidence as well as standing: a judge (or check) that
// claims the success outcome below the promote threshold is abstaining and
// resolves to unknown; a demotion stands at any confidence; an operator is
// exempt; the boundary at exactly the threshold promotes.
func TestPromotionNeedsConfidence(t *testing.T) {
	th := DefaultThresholds
	low := v(KindClosure, "achieved", StandingJudge, 0.2)
	self := v(KindClosure, "not_achieved", StandingSelf, 0.9)
	r := must(t, cands(KindClosure, []*Verdict{low, self}))
	if r.Outcome != "unknown" || r.Effective != "" || !strings.HasPrefix(r.Rule, "below_promote_threshold") {
		t.Fatalf("a 0.2 judge promoted: %+v", r)
	}
	at := v(KindClosure, "achieved", StandingJudge, th.Promote)
	if r := must(t, cands(KindClosure, []*Verdict{at})); r.Outcome != "achieved" || r.Effective != at.ID {
		t.Fatalf("boundary: %+v", r)
	}
	demote := v(KindClosure, "not_achieved", StandingJudge, 0)
	if r := must(t, cands(KindClosure, []*Verdict{demote})); r.Outcome != "not_achieved" {
		t.Fatalf("a zero-confidence demotion did not stand: %+v", r)
	}
	op := v(KindClosure, "achieved", StandingOperator, 0)
	if r := must(t, cands(KindClosure, []*Verdict{op, self})); r.Outcome != "achieved" {
		t.Fatalf("operator at 0 did not promote: %+v", r)
	}
	step := v(KindStep, "done", StandingJudge, 0)
	if r := must(t, cands(KindStep, []*Verdict{step})); r.Outcome != "unclear" {
		t.Fatalf("a zero-confidence done completed a step: %+v", r)
	}
	bad := DefaultThresholds
	bad.Promote = 1.5
	if _, err := Resolve(cands(KindClosure, []*Verdict{at}), bad); err == nil {
		t.Fatal("promote threshold out of range accepted")
	}
}

// Current re-derives every resolution before choosing among them: a
// wire-valid resolution that claims what its candidates do not support is
// refused, not selected.
func TestCurrentRefusesResolutionsThatDoNotRederive(t *testing.T) {
	j := openJ(t)
	self := v(KindClosure, "not_achieved", StandingSelf, 0.9)
	self.Schema, self.Seq = "verdict/1", 0
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "v", Epoch: j.Epoch(), Records: []record.Record{self}}); err != nil {
		t.Fatal(err)
	}
	var committed *Verdict
	j.Production().Scan(0, func(r record.Record) error {
		if x, ok := r.(*Verdict); ok {
			committed = x
		}
		return nil
	})
	forged := &Resolution{Header: record.Header{ID: record.NewID(), Schema: "resolution/1", Subject: subj, At: time.Now().UTC()}, VerdictKind: KindClosure, Outcome: "achieved", Effective: committed.ID, Candidates: []record.RecordID{committed.ID}, ResolverVer: ResolverVer, Thresholds: DefaultThresholds, Rule: "standing:operator", Confidence: 0.9}
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: "forged", Epoch: j.Epoch(), Records: []record.Record{forged}}); err != nil {
		t.Fatal(err)
	}
	if _, err := Current(j.Production()); err == nil || !strings.Contains(err.Error(), "disagrees with its recompute") {
		t.Fatalf("Current selected a forged resolution: %v", err)
	}
}
