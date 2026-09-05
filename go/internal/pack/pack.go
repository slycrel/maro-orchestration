// Package pack is the native pack envelope: the carrier for Go-only causal
// history between workspaces, and the quarantine through which learning
// from elsewhere — another Go workspace or a Python store — enters this
// one. Design §13: shared edges are exact to the wire contract; what the
// contract cannot carry (revisions, transitions, exposures, experiments,
// attestations) rides the pack; and every import enters at candidate under
// `import` provenance, to earn standing here by this workspace's own
// evidence. No round trip is claimed.
//
// A pack is JSON lines: a header, then the cited lesson_text thoughts, then
// the carried records exactly as the source journal framed them (kind and
// envelope from the registry, body verbatim). It is a causal history, not
// an offer: the receiving side never adopts a stage from it.
package pack

import (
	"bufio"
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/experiment"
	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/learn"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/thought"
)

// Format names the envelope. A reader refuses any other value: a format it
// does not understand is never read best-effort.
const Format = "maro-go-pack/1"

// Carried is the kinds a pack carries: the learn ledger and the experiment
// evidence that produced its stages. Run records are not carried (they are
// this workspace's thoughts, not learning); policy revisions ride along as
// learned_revision but are not imported (process data of the source).
var Carried = map[record.Kind]bool{
	learn.KindRevision: true, learn.KindTransition: true, learn.KindApplication: true, learn.KindPolicyApplication: true,
	experiment.KindExperiment: true, experiment.KindAssignment: true, experiment.KindCommitment: true,
	experiment.KindAttestation: true, experiment.KindMeasurement: true,
}

var (
	ErrFormat   = errors.New("pack: not a " + Format + " file")
	ErrTampered = errors.New("pack: thought body does not match its ref")
)

// Header is the first line.
type Header struct {
	Format   string              `json:"format"`
	Head     uint64              `json:"head"`
	At       time.Time           `json:"at"`
	Records  map[record.Kind]int `json:"records"`
	Thoughts int                 `json:"thoughts"`
	Source   string              `json:"source,omitempty"` // a label the exporter chose; informational
}

type line struct {
	Line   string           `json:"line"` // header | thought | record
	Header *Header          `json:"header,omitempty"`
	Ref    *thought.Ref     `json:"ref,omitempty"`
	Body   string           `json:"body,omitempty"` // thought: base64 of the bytes
	Record *journal.Encoded `json:"record,omitempty"`
}

// Export writes the pack of j at its current head. Thoughts are the
// lesson_text bodies the carried revisions cite; a revision whose text is
// absent from the store is carried without it and the importer skips it.
func Export(j *journal.Journal, st *thought.Store, source string, w io.Writer) (*Header, error) {
	head := j.Head()
	var recs []record.Record
	keep := func(r record.Record) error {
		if Carried[r.Kind()] {
			recs = append(recs, r)
		}
		return nil
	}
	if err := j.Production().PinAt(head).ScanThrough(0, head, keep); err != nil {
		return nil, err
	}
	if err := j.Control().ScanThrough(0, head, keep); err != nil {
		return nil, err
	}
	sort.SliceStable(recs, func(a, b int) bool { return recs[a].Head().Seq < recs[b].Head().Seq })
	h := &Header{Format: Format, Head: head, At: time.Now().UTC(), Records: map[record.Kind]int{}, Source: source}
	var thoughts []line
	seen := map[string]bool{}
	for _, r := range recs {
		h.Records[r.Kind()]++
		rev, ok := r.(*learn.LearnedRevision)
		if !ok || rev.Text.Hash == "" || seen[rev.Text.Hash] {
			continue
		}
		body, err := st.Get(rev.Text)
		if err != nil {
			if errors.Is(err, thought.ErrAbsent) {
				continue
			}
			return nil, err
		}
		seen[rev.Text.Hash] = true
		ref := rev.Text
		thoughts = append(thoughts, line{Line: "thought", Ref: &ref, Body: base64.StdEncoding.EncodeToString(body)})
	}
	h.Thoughts = len(thoughts)
	bw := bufio.NewWriter(w)
	enc := json.NewEncoder(bw)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(line{Line: "header", Header: h}); err != nil {
		return nil, err
	}
	for _, t := range thoughts {
		if err := enc.Encode(t); err != nil {
			return nil, err
		}
	}
	for _, r := range recs {
		env, _ := record.EnvelopeOf(r.Kind())
		body, err := json.Marshal(r)
		if err != nil {
			return nil, err
		}
		if err := enc.Encode(line{Line: "record", Record: &journal.Encoded{Kind: r.Kind(), Envelope: env.String(), Seq: r.Head().Seq, Body: body}}); err != nil {
			return nil, err
		}
	}
	return h, bw.Flush()
}

// Report is what an import did, by count; every skipped row names why.
type Report struct {
	Label    string
	Imported int
	Already  int            // idempotent replays: the same source row imported before
	Skipped  map[string]int // reason → count
	Records  map[record.Kind]int
	Thoughts int
	Items    []Imported
}

// Imported is one candidate revision this import created (or found).
type Imported struct {
	Item     learn.LearnedID
	Revision record.RecordID
	From     string // the source row: a pack revision id or a Python lesson_id
	Replayed bool
}

func (r *Report) skip(why string) {
	if r.Skipped == nil {
		r.Skipped = map[string]int{}
	}
	r.Skipped[why]++
}

// String is the operator-facing summary.
func (r *Report) String() string {
	var b strings.Builder
	fmt.Fprintf(&b, "imported %d at candidate (%d already present)", r.Imported, r.Already)
	if len(r.Skipped) > 0 {
		keys := make([]string, 0, len(r.Skipped))
		for k := range r.Skipped {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		parts := make([]string, 0, len(keys))
		for _, k := range keys {
			parts = append(parts, fmt.Sprintf("%s %d", k, r.Skipped[k]))
		}
		fmt.Fprintf(&b, "; skipped: %s", strings.Join(parts, ", "))
	}
	return b.String()
}

// Import reads a pack and enters each current lesson of the source at
// candidate here: a fresh item and revision, the text re-stored by hash,
// provenance `import` citing the source revision. The source's stage is
// reported in Why and otherwise ignored — tombstoned and quarantined
// lessons are not offered at all. Idempotent per source revision.
func Import(ctx context.Context, j *journal.Journal, st *thought.Store, label string, r io.Reader) (*Report, error) {
	rep := &Report{Label: label, Records: map[record.Kind]int{}}
	// Read and validate the whole file before the first write: a pack that
	// fails anywhere enters nothing (content-addressed thoughts are the
	// harmless exception — a body stored is a body stored).
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 1<<20), 64<<20)
	var hdr *Header
	var thoughts, records []line
	n := 0
	for sc.Scan() {
		n++
		var l line
		if err := json.Unmarshal(sc.Bytes(), &l); err != nil {
			return nil, fmt.Errorf("%w: line %d: %v", ErrFormat, n, err)
		}
		switch {
		case n == 1:
			if l.Line != "header" || l.Header == nil || l.Header.Format != Format {
				return nil, fmt.Errorf("%w: header line %q", ErrFormat, strings.TrimSpace(string(sc.Bytes())))
			}
			hdr = l.Header
		case l.Line == "thought" && l.Ref != nil:
			thoughts = append(thoughts, l)
		case l.Line == "record" && l.Record != nil:
			if !Carried[l.Record.Kind] {
				return nil, fmt.Errorf("%w: line %d: kind %s is not carried", ErrFormat, n, l.Record.Kind)
			}
			records = append(records, l)
		default:
			return nil, fmt.Errorf("%w: line %d: unknown line %q", ErrFormat, n, l.Line)
		}
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	if hdr == nil {
		return nil, fmt.Errorf("%w: empty", ErrFormat)
	}
	for i, l := range thoughts {
		body, err := base64.StdEncoding.DecodeString(l.Body)
		if err != nil {
			return nil, fmt.Errorf("%w: thought %d: %v", ErrFormat, i+1, err)
		}
		ref, err := st.Put(l.Ref.Kind, body)
		if err != nil {
			return nil, fmt.Errorf("thought %d: %w", i+1, err)
		}
		if ref.Hash != l.Ref.Hash {
			return nil, fmt.Errorf("%w: thought %d: declared %s, body is %s", ErrTampered, i+1, l.Ref.Hash, ref.Hash)
		}
		rep.Thoughts++
	}
	revs := map[learn.LearnedID][]*learn.LearnedRevision{}
	stage := map[record.RecordID]learn.Stage{}
	var trs []*learn.LifecycleTransition
	for i, l := range records {
		rec, err := journal.Decode(*l.Record)
		if err != nil {
			return nil, fmt.Errorf("%w: record %d: %v", ErrFormat, i+1, err)
		}
		rep.Records[rec.Kind()]++
		switch x := rec.(type) {
		case *learn.LearnedRevision:
			revs[x.Item] = append(revs[x.Item], x)
			stage[x.ID] = learn.Candidate
		case *learn.LifecycleTransition:
			trs = append(trs, x)
		}
	}
	// The source's own history decides which revision is current and what
	// it had reached — read off Seqs, in order; nothing here re-executes
	// the source's rules, and nothing here adopts its verdicts.
	sort.SliceStable(trs, func(a, b int) bool { return trs[a].Seq < trs[b].Seq })
	for _, t := range trs {
		if _, ok := stage[t.Revision]; ok {
			stage[t.Revision] = t.To
		}
	}
	ids := make([]string, 0, len(revs))
	for id := range revs {
		ids = append(ids, string(id))
	}
	sort.Strings(ids)
	for _, id := range ids {
		rs := revs[learn.LearnedID(id)]
		sort.SliceStable(rs, func(a, b int) bool { return rs[a].Seq < rs[b].Seq })
		cur := rs[len(rs)-1]
		if cur.LearnedKind != learn.Lesson {
			rep.skip("policy")
			continue
		}
		switch stage[cur.ID] {
		case learn.Tombstone, learn.Quarantined:
			rep.skip(string(stage[cur.ID]))
			continue
		}
		if ok, err := st.Has(cur.Text); err != nil {
			return nil, err
		} else if !ok {
			rep.skip("text absent")
			continue
		}
		why := fmt.Sprintf("pack %s: item %s revision %s was %s at head %d", label, cur.Item, cur.ID, stage[cur.ID], hdr.Head)
		if err := enter(ctx, j, rep, "import/pack/"+string(cur.ID), string(cur.ID), cur.Text, cur.Family, learn.Provenance{Source: learn.SourceImport, Ref: cur.ID, Why: why}); err != nil {
			return nil, err
		}
	}
	return rep, nil
}

// enter submits one candidate revision under key; a replayed ack means the
// same source row entered before, and nothing is written twice.
func enter(ctx context.Context, j *journal.Journal, rep *Report, key, from string, text thought.Ref, family string, prov learn.Provenance) error {
	item := learn.LearnedID(record.NewID())
	rev := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: time.Now().UTC()},
		Item: item, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Family: family, Text: text, Provenance: prov}
	ack, err := j.Submit(ctx, journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: []record.Record{rev}})
	if err != nil {
		return err
	}
	if ack.Replayed {
		rep.Already++
		rep.Items = append(rep.Items, Imported{From: from, Replayed: true})
		return nil
	}
	rep.Imported++
	rep.Items = append(rep.Items, Imported{Item: item, Revision: rev.ID, From: from})
	return nil
}

// Python store import (wire contract B7, all three tiers).

// tiers in precedence order: a lesson_id present in a higher tier is the
// same lesson further along the Python ladder; the highest copy wins.
var tiers = []struct{ name, rel string }{
	{"flat", "memory/lessons.jsonl"},
	{"medium", "memory/medium/lessons.jsonl"},
	{"long", "memory/long/lessons.jsonl"},
}

type pyRow struct {
	tier   string
	rank   int
	id     string
	text   string
	task   string
	fields map[string]json.RawMessage
}

// ImportPython reads a Python workspace's lesson stores (read-only) and
// enters each injectable lesson at candidate here. Skipped, and reported
// by reason: prompt-minted (quarantined at the source), contested, and
// provisional rows — the Python vocabulary for "not to be injected" — and
// rows the reader cannot parse. Idempotent per (label, lesson_id).
func ImportPython(ctx context.Context, j *journal.Journal, st *thought.Store, label, dir string) (*Report, error) {
	if label == "" {
		label = filepath.Base(filepath.Clean(dir))
	}
	rep := &Report{Label: label, Records: map[record.Kind]int{}}
	best := map[string]pyRow{}
	found := 0
	for rank, t := range tiers {
		f, err := os.Open(filepath.Join(dir, filepath.FromSlash(t.rel)))
		if err != nil {
			if errors.Is(err, os.ErrNotExist) {
				continue
			}
			return nil, err
		}
		found++
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 16<<20)
		for sc.Scan() {
			raw := bytes.TrimSpace(sc.Bytes())
			if len(raw) == 0 {
				continue
			}
			var fields map[string]json.RawMessage
			if err := json.Unmarshal(raw, &fields); err != nil {
				rep.skip("malformed")
				continue
			}
			var id, text, task string
			json.Unmarshal(fields["lesson_id"], &id)
			json.Unmarshal(fields["lesson"], &text)
			json.Unmarshal(fields["task_type"], &task)
			if strings.TrimSpace(id) == "" || strings.TrimSpace(text) == "" {
				rep.skip("malformed")
				continue
			}
			row := pyRow{tier: t.name, rank: rank, id: id, text: text, task: task, fields: fields}
			if b, ok := best[id]; !ok || row.rank >= b.rank {
				best[id] = row
			}
		}
		f.Close()
		if err := sc.Err(); err != nil {
			return nil, err
		}
	}
	if found == 0 {
		return nil, fmt.Errorf("pack: no lesson store under %s (expected memory/lessons.jsonl)", dir)
	}
	ids := make([]string, 0, len(best))
	for id := range best {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		row := best[id]
		var minted string
		json.Unmarshal(row.fields["minted_from"], &minted)
		if minted == "prompt" {
			rep.skip("minted_from=prompt")
			continue
		}
		var contested map[string]json.RawMessage
		if raw := row.fields["contested"]; len(raw) > 0 {
			if err := json.Unmarshal(raw, &contested); err != nil && string(raw) != "null" {
				rep.skip("contested")
				continue
			}
		}
		if len(contested) > 0 {
			rep.skip("contested")
			continue
		}
		var provisional bool
		if raw := row.fields["provisional"]; len(raw) > 0 {
			if err := json.Unmarshal(raw, &provisional); err != nil || provisional {
				rep.skip("provisional")
				continue
			}
		}
		ref, err := st.Put(thought.LessonText, []byte(strings.TrimSpace(row.text)))
		if err != nil {
			return nil, err
		}
		var reinforced float64
		json.Unmarshal(row.fields["times_reinforced"], &reinforced)
		why := fmt.Sprintf("python %s: lesson %s tier %s task_type %q times_reinforced %d", label, id, row.tier, row.task, int(reinforced))
		if err := enter(ctx, j, rep, "import/python/"+label+"/"+id, id, ref, "", learn.Provenance{Source: learn.SourceImport, Why: why}); err != nil {
			return nil, err
		}
	}
	return rep, nil
}
