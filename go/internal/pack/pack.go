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
// an offer: the importer vouches for it by FOLDING it under the learn
// ledger's own rules, and the receiving side never adopts a stage from it.
package pack

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
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

// Carried is the kinds a pack carries: the whole learn ledger (everything
// learn.Fold reads, so the history folds on the receiving side) and the
// experiment evidence that produced its stages. Run records are not
// carried (they are this workspace's thoughts, not learning).
var Carried = map[record.Kind]bool{
	learn.KindRevision: true, learn.KindTransition: true, learn.KindApplication: true, learn.KindRecall: true,
	learn.KindPolicySelection: true, learn.KindPolicyApplication: true,
	experiment.KindExperiment: true, experiment.KindAssignment: true, experiment.KindClosed: true, experiment.KindEvidence: true,
	experiment.KindCommitment: true, experiment.KindAttestation: true, experiment.KindMeasurement: true,
}

var (
	ErrFormat   = errors.New("pack: not a " + Format + " file")
	ErrTampered = errors.New("pack: thought body does not match its ref")
	ErrHistory  = errors.New("pack: carried history does not fold")
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
	Already  int            // the same source row (origin) is here already
	Skipped  map[string]int // reason → count
	Records  map[record.Kind]int
	Thoughts int
	Items    []Imported
	Ack      journal.Ack // the ONE command that entered everything (zero when nothing entered)
}

// Imported is one candidate revision this import created (or found).
type Imported struct {
	Item     learn.LearnedID
	Revision record.RecordID
	Origin   string
	Revised  bool // entered as a new revision of an item an earlier import created
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

// offer is one lesson the source offers: its text (already vouched), its
// family, the origin it will be known by here, the origins of its earlier
// texts (so a re-import of a revised source lesson revises the same local
// item), and the why.
type offer struct {
	origin   string
	earlier  []string
	body     []byte
	family   string
	why      string
	sourceID record.RecordID
}

// Import reads a pack and enters each current lesson of the source at
// candidate here. The whole file is parsed and vouched for before the
// first write: the header's counts and head against the body; every
// thought a cited lesson_text whose bytes hash to its ref; every record
// decoded from the registry; and the production records FOLDED under the
// learn ledger's rules (predecessor chains, legal edges, evidence, seeds)
// — that fold, not the records one by one, decides what the source's
// current revisions are and what they had reached. Then everything that
// enters enters in ONE journal command. Tombstoned and quarantined lessons
// and policies are not offered; a source lesson already here (by origin)
// is reported, not re-entered; a source lesson whose text changed since
// it entered becomes a new revision of the same local item.
func Import(ctx context.Context, j *journal.Journal, st *thought.Store, label string, r io.Reader) (*Report, error) {
	rep := &Report{Label: label, Records: map[record.Kind]int{}}
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
	// records: decoded, unique, within the announced head, counted as announced
	var decoded []record.Record
	counts := map[record.Kind]int{}
	seqs, ids := map[uint64]bool{}, map[record.RecordID]bool{}
	for i, l := range records {
		rec, err := journal.Decode(*l.Record)
		if err != nil {
			return nil, fmt.Errorf("%w: record %d: %v", ErrFormat, i+1, err)
		}
		h := rec.Head()
		if h.Seq > hdr.Head || seqs[h.Seq] || ids[h.ID] {
			return nil, fmt.Errorf("%w: record %d (%s seq %d) is beyond the head or a duplicate", ErrFormat, i+1, h.ID, h.Seq)
		}
		seqs[h.Seq], ids[h.ID] = true, true
		counts[rec.Kind()]++
		decoded = append(decoded, rec)
	}
	if len(counts) != len(hdr.Records) {
		return nil, fmt.Errorf("%w: header announces %d kinds, body carries %d", ErrFormat, len(hdr.Records), len(counts))
	}
	for k, c := range hdr.Records {
		if counts[k] != c {
			return nil, fmt.Errorf("%w: header announces %d %s, body carries %d", ErrFormat, c, k, counts[k])
		}
	}
	if hdr.Thoughts != len(thoughts) {
		return nil, fmt.Errorf("%w: header announces %d thoughts, body carries %d", ErrFormat, hdr.Thoughts, len(thoughts))
	}
	sort.SliceStable(decoded, func(a, b int) bool { return decoded[a].Head().Seq < decoded[b].Head().Seq })
	rep.Records = counts
	// the source history, vouched for by folding it under the ledger's rules
	src, err := learn.FoldRecords(func(fn func(record.Record) error) error {
		for _, rec := range decoded {
			if env, _ := record.EnvelopeOf(rec.Kind()); env != record.Production {
				continue
			}
			if err := fn(rec); err != nil {
				return err
			}
		}
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrHistory, err)
	}
	// thoughts: lesson_text only, each cited by a carried revision, each
	// hashing to what it declares — checked without storing anything
	cited := map[string]bool{}
	for _, it := range src.Items {
		for _, rev := range it.Revisions {
			if rev.Text.Hash != "" {
				cited[rev.Text.Hash] = true
			}
		}
	}
	bodies := map[string][]byte{}
	for i, l := range thoughts {
		if l.Ref.Kind != thought.LessonText || !cited[l.Ref.Hash] {
			return nil, fmt.Errorf("%w: thought %d (%s %s) is not a cited lesson text", ErrFormat, i+1, l.Ref.Kind, l.Ref.Hash)
		}
		body, err := base64.StdEncoding.DecodeString(l.Body)
		if err != nil {
			return nil, fmt.Errorf("%w: thought %d: %v", ErrFormat, i+1, err)
		}
		if got := thought.HashOf(l.Ref.Kind, body); got != l.Ref.Hash {
			return nil, fmt.Errorf("%w: thought %d: declared %s, body is %s", ErrTampered, i+1, l.Ref.Hash, got)
		}
		bodies[l.Ref.Hash] = body
	}
	rep.Thoughts = len(bodies)
	// the offers, from the folded source
	ids2 := make([]string, 0, len(src.Items))
	for id := range src.Items {
		ids2 = append(ids2, string(id))
	}
	sort.Strings(ids2)
	var offers []offer
	for _, id := range ids2 {
		it := src.Items[learn.LearnedID(id)]
		cur := it.Current
		if cur.LearnedKind != learn.Lesson {
			rep.skip("policy")
			continue
		}
		stage := it.StageOf(cur.ID)
		switch stage {
		case learn.Tombstone, learn.Quarantined:
			rep.skip(string(stage))
			continue
		}
		body, ok := bodies[cur.Text.Hash]
		if !ok {
			rep.skip("text absent")
			continue
		}
		o := offer{origin: "pack:" + string(cur.ID), body: body, family: cur.Family, sourceID: cur.ID,
			why: fmt.Sprintf("pack %s: item %s revision %s was %s at head %d", label, cur.Item, cur.ID, stage, hdr.Head)}
		for _, rev := range it.Revisions {
			if rev.ID != cur.ID {
				o.earlier = append(o.earlier, "pack:"+string(rev.ID))
			}
		}
		offers = append(offers, o)
	}
	return rep, enter(ctx, j, st, rep, "pack", offers)
}

// enter matches the offers against what is here (by origin), stores the
// texts, and submits every new revision in ONE command keyed by the set
// it enters: nothing enters twice, nothing enters partially.
func enter(ctx context.Context, j *journal.Journal, st *thought.Store, rep *Report, kind string, offers []offer) error {
	led, err := learn.Fold(j.Production())
	if err != nil {
		return err
	}
	// origin → the local item that carries it (any revision), and each item's current revision
	byOrigin := map[string]*learn.Item{}
	for _, it := range led.Items {
		for _, rev := range it.Revisions {
			if rev.Provenance.Origin != "" {
				byOrigin[rev.Provenance.Origin] = it
			}
		}
	}
	var recs []record.Record
	var keys []string
	now := time.Now().UTC()
	for _, o := range offers {
		if it := byOrigin[o.origin]; it != nil {
			rep.Already++
			rep.Items = append(rep.Items, Imported{Item: it.ID, Revision: it.Current.ID, Origin: o.origin, Replayed: true})
			continue
		}
		var prior *learn.Item
		for _, e := range o.earlier {
			if it := byOrigin[e]; it != nil {
				prior = it
				break
			}
		}
		ref, err := st.Put(thought.LessonText, o.body) // content-addressed: the ref is the body's, whatever the source declared
		if err != nil {
			return err
		}
		item, pred := learn.LearnedID(record.NewID()), record.RecordID("")
		if prior != nil {
			item, pred = prior.ID, prior.Current.ID
		}
		rev := &learn.LearnedRevision{Header: record.Header{ID: record.NewID(), Schema: "learned_revision/1", Subject: record.Ref{Kind: "learned", ID: string(item)}, At: now},
			Item: item, Predecessor: pred, LearnedKind: learn.Lesson, Scope: learn.ScopeWorkspace, Family: o.family, Text: ref,
			Provenance: learn.Provenance{Source: learn.SourceImport, Ref: o.sourceID, Why: o.why, Origin: o.origin}}
		recs = append(recs, rev)
		keys = append(keys, o.origin)
		rep.Imported++
		rep.Items = append(rep.Items, Imported{Item: item, Revision: rev.ID, Origin: o.origin, Revised: prior != nil})
	}
	if len(recs) == 0 {
		return nil
	}
	sum := sha256.Sum256([]byte(strings.Join(keys, "\n")))
	ack, err := j.Submit(ctx, journal.Command{IdempotencyKey: "import/" + kind + "/" + hex.EncodeToString(sum[:16]), Epoch: j.Epoch(), Records: recs})
	if err != nil {
		return err
	}
	rep.Ack = ack
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

// flatRequired are the `Lesson` dataclass fields without defaults: a row
// missing one fails Lesson(**kwargs) and the Python reader skips it.
var flatRequired = []string{"lesson_id", "task_type", "outcome", "lesson", "source_goal", "confidence"}

type pyRow struct {
	tier   string
	rank   int
	id     string
	text   string
	task   string
	fields map[string]json.RawMessage
}

// truthy is Python truth over a JSON value: what `if row.contested:` sees.
func truthy(raw json.RawMessage) bool {
	raw = bytes.TrimSpace(raw)
	switch {
	case len(raw) == 0, string(raw) == "null", string(raw) == "false", string(raw) == "0", string(raw) == `""`, string(raw) == "{}", string(raw) == "[]":
		return false
	}
	return true
}

// DefaultLabel is the label a Python import gets when none is given: the
// store's absolute path — two workspaces with one basename are two sources.
func DefaultLabel(dir string) string {
	abs, err := filepath.Abs(dir)
	if err != nil {
		return filepath.Clean(dir)
	}
	return filepath.Clean(abs)
}

// ImportPython reads a Python workspace's lesson stores (read-only) and
// enters each lesson its readers would inject at candidate here. Skipped,
// and reported by reason, exactly as the Python readers skip: rows the
// flat reader cannot build (a required field missing), prompt-minted rows
// (quarantined at the source), rows with a truthy `contested`, and tiered
// rows marked provisional (the flat reader has no such field and injects
// them; the tiered reader does not). Idempotent per (label, lesson_id,
// text): a changed text under a known lesson_id enters as a new revision
// of the same local item.
func ImportPython(ctx context.Context, j *journal.Journal, st *thought.Store, label, dir string) (*Report, error) {
	if label == "" {
		label = DefaultLabel(dir)
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
			missing := false
			for _, k := range flatRequired {
				if _, ok := fields[k]; !ok {
					missing = true
				}
			}
			var id, text, task string
			if missing || json.Unmarshal(fields["lesson_id"], &id) != nil || json.Unmarshal(fields["lesson"], &text) != nil || strings.TrimSpace(id) == "" {
				rep.skip("malformed")
				continue
			}
			json.Unmarshal(fields["task_type"], &task)
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
	var offers []offer
	for _, id := range ids {
		row := best[id]
		body := []byte(strings.TrimSpace(row.text))
		if len(body) == 0 {
			rep.skip("empty")
			continue
		}
		var minted string
		json.Unmarshal(row.fields["minted_from"], &minted)
		if minted == "prompt" {
			rep.skip("minted_from=prompt")
			continue
		}
		if truthy(row.fields["contested"]) {
			rep.skip("contested")
			continue
		}
		if row.tier != "flat" && truthy(row.fields["provisional"]) {
			rep.skip("provisional")
			continue
		}
		hash := thought.HashOf(thought.LessonText, body)
		digest := strings.TrimPrefix(hash, thought.HashAlgo+":")
		var reinforced float64
		json.Unmarshal(row.fields["times_reinforced"], &reinforced)
		offers = append(offers, offer{origin: "python:" + label + ":" + id + ":" + digest, earlier: []string{"python:" + label + ":" + id + ":*"}, body: body,
			why: fmt.Sprintf("python %s: lesson %s tier %s task_type %q times_reinforced %d", label, id, row.tier, row.task, int(reinforced))})
	}
	return rep, enterPython(ctx, j, st, rep, offers)
}

// enterPython is enter with the "same lesson_id, other text" match: the
// earlier-origin wildcard resolves against every origin here.
func enterPython(ctx context.Context, j *journal.Journal, st *thought.Store, rep *Report, offers []offer) error {
	led, err := learn.Fold(j.Production())
	if err != nil {
		return err
	}
	var origins []string
	for _, it := range led.Items {
		for _, rev := range it.Revisions {
			if strings.HasPrefix(rev.Provenance.Origin, "python:") {
				origins = append(origins, rev.Provenance.Origin)
			}
		}
	}
	for i := range offers {
		prefix := strings.TrimSuffix(offers[i].earlier[0], "*")
		offers[i].earlier = nil
		for _, o := range origins {
			if strings.HasPrefix(o, prefix) && o != offers[i].origin {
				offers[i].earlier = append(offers[i].earlier, o)
			}
		}
	}
	return enter(ctx, j, st, rep, "python", offers)
}
