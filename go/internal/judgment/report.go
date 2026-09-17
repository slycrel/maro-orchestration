package judgment

import (
	"fmt"
	"io"
	"sort"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/verdict"
)

// The judgment report is the whole point of the shadow arm: what would a
// second provider have said about the same question, and how often does
// it agree with the one that decided? It reads the journal and computes;
// it never writes, and it never feeds a number back into the engine.

// Pair is one primary verdict with the shadow answers about it.
type Pair struct {
	Primary *verdict.Verdict
	Shadows []*ShadowJudgment
}

// Bucket is one confidence band of the primary's verdict.
type Bucket struct {
	Low, High float64
	N, Agree  int
}

// Label is the band as a printable range.
func (b Bucket) Label() string { return fmt.Sprintf("%.1f–%.1f", b.Low, b.High) }

// Stats is one shadow provider's standing against the primary.
type Stats struct {
	Provider  string
	N         int // answers that arrived
	Agree     int // same outcome as the primary
	Failed    int // calls that did not answer
	Latencies []int64
	Buckets   []Bucket
}

// Agreement is the share of arrived answers that matched the primary.
func (s Stats) Agreement() float64 {
	if s.N == 0 {
		return 0
	}
	return float64(s.Agree) / float64(s.N)
}

// Median and P95 over the latencies that arrived (sorted in place by
// Summarize, so these are cheap).
func (s Stats) Median() int64 { return quantile(s.Latencies, 0.5) }
func (s Stats) P95() int64    { return quantile(s.Latencies, 0.95) }

func quantile(v []int64, q float64) int64 {
	if len(v) == 0 {
		return 0
	}
	i := int(q * float64(len(v)-1))
	return v[i]
}

// Disagreement is one row of the report's honest middle: the subject,
// what the primary said, and what the shadow said instead.
type Disagreement struct {
	Subject        record.Ref
	Provider       string
	PrimaryOutcome string
	PrimaryConf    float64
	ShadowOutcome  string
	ShadowConf     float64
	Why            string
}

// Summary is the whole report.
type Summary struct {
	Pairs         []Pair
	Stats         []Stats
	Disagreements []Disagreement
	Unpaired      int // shadow records whose primary verdict is not in the journal
	// Unshadowed counts the judge verdicts (step/closure, model-judged)
	// with NO shadow record at all — the population the report does not
	// measure. A shadow lost to a crash between the primary verdict and
	// its control record lands here, not nowhere.
	Unshadowed int
}

var bands = [][2]float64{{0, 0.5}, {0.5, 0.7}, {0.7, 0.9}, {0.9, 1.0001}}

// Summarize folds the journal into the report. Verdicts come from the
// production population, shadow answers from the control one — the two
// readers are different types, which is the same guarantee stated in the
// type system: a shadow answer cannot reach anything that resolves.
func Summarize(pr *journal.ProductionReader, cr *journal.ControlReader) (Summary, error) {
	var s Summary
	verdicts := map[record.RecordID]*verdict.Verdict{}
	if err := pr.Scan(0, func(r record.Record) error {
		if v, ok := r.(*verdict.Verdict); ok {
			verdicts[v.ID] = v
		}
		return nil
	}); err != nil {
		return s, err
	}
	byPrimary := map[record.RecordID][]*ShadowJudgment{}
	var order []record.RecordID
	if err := cr.Scan(0, func(r record.Record) error {
		sj, ok := r.(*ShadowJudgment)
		if !ok {
			return nil
		}
		if _, seen := byPrimary[sj.Primary]; !seen {
			order = append(order, sj.Primary)
		}
		byPrimary[sj.Primary] = append(byPrimary[sj.Primary], sj)
		return nil
	}); err != nil {
		return s, err
	}
	for id, v := range verdicts {
		if v.Source.Standing == verdict.StandingJudge && len(byPrimary[id]) == 0 {
			s.Unshadowed++
		}
	}
	stats := map[string]*Stats{}
	for _, id := range order {
		v := verdicts[id]
		if v == nil {
			s.Unpaired += len(byPrimary[id])
			continue
		}
		s.Pairs = append(s.Pairs, Pair{Primary: v, Shadows: byPrimary[id]})
		for _, sj := range byPrimary[id] {
			st := stats[sj.Provider]
			if st == nil {
				st = &Stats{Provider: sj.Provider, Buckets: newBuckets()}
				stats[sj.Provider] = st
			}
			if sj.Failed || sj.Answer == nil {
				st.Failed++
				continue
			}
			st.N++
			st.Latencies = append(st.Latencies, sj.LatencyMillis)
			agree := sj.Answer.Outcome() == v.Outcome
			if agree {
				st.Agree++
			} else {
				s.Disagreements = append(s.Disagreements, Disagreement{
					Subject: v.Subject, Provider: sj.Provider,
					PrimaryOutcome: v.Outcome, PrimaryConf: v.Confidence,
					ShadowOutcome: sj.Answer.Outcome(), ShadowConf: sj.Answer.Confidence, Why: sj.Answer.Why})
			}
			for i := range st.Buckets {
				b := &st.Buckets[i]
				if v.Confidence >= b.Low && v.Confidence < b.High {
					b.N++
					if agree {
						b.Agree++
					}
				}
			}
		}
	}
	for _, st := range stats {
		sort.Slice(st.Latencies, func(i, j int) bool { return st.Latencies[i] < st.Latencies[j] })
		s.Stats = append(s.Stats, *st)
	}
	sort.Slice(s.Stats, func(i, j int) bool { return s.Stats[i].Provider < s.Stats[j].Provider })
	return s, nil
}

func newBuckets() []Bucket {
	out := make([]Bucket, 0, len(bands))
	for _, b := range bands {
		out = append(out, Bucket{Low: b[0], High: b[1]})
	}
	return out
}

// Render writes the report for a person. Numbers about a provider's
// behaviour go to the operator's screen, never into a repo file.
func (s Summary) Render(w io.Writer) {
	if len(s.Pairs) == 0 {
		fmt.Fprintf(w, "judgment: no shadow answers recorded (judgment.shadow is empty by default); %d judge verdicts unshadowed\n", s.Unshadowed)
		return
	}
	fmt.Fprintf(w, "judgment report: %d primary verdicts with shadow answers, %d judge verdicts without any\n\n", len(s.Pairs), s.Unshadowed)
	fmt.Fprintln(w, "provider  answered  agree  agreement  failed  median ms  p95 ms")
	for _, st := range s.Stats {
		fmt.Fprintf(w, "%-9s %8d %6d %9.0f%% %7d %10d %7d\n", st.Provider, st.N, st.Agree, 100*st.Agreement(), st.Failed, st.Median(), st.P95())
	}
	for _, st := range s.Stats {
		fmt.Fprintf(w, "\n%s by the primary's confidence:\n", st.Provider)
		for _, b := range st.Buckets {
			if b.N == 0 {
				continue
			}
			fmt.Fprintf(w, "  %-9s n=%-4d agree %.0f%%\n", b.Label(), b.N, 100*float64(b.Agree)/float64(b.N))
		}
	}
	if len(s.Disagreements) > 0 {
		fmt.Fprintf(w, "\ndisagreements (%d):\n", len(s.Disagreements))
		for _, d := range s.Disagreements {
			fmt.Fprintf(w, "  %s %s: primary %s (%.2f) vs %s %s (%.2f)%s\n", d.Subject.Kind, short(d.Subject.ID), d.PrimaryOutcome, d.PrimaryConf, d.Provider, d.ShadowOutcome, d.ShadowConf, why(d.Why))
		}
	}
	if s.Unpaired > 0 {
		fmt.Fprintf(w, "\n%d shadow answers cite a verdict this journal does not hold\n", s.Unpaired)
	}
}

func why(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return ""
	}
	if len(s) > 120 {
		s = s[:120] + "…"
	}
	return " — " + s
}

func short(id string) string {
	if len(id) > 8 {
		return id[len(id)-8:]
	}
	return id
}
