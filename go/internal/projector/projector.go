// Package projector materializes the shared-spec views from the journal
// (design note §2): it consumes committed records from its own durable
// cursor, writes a complete view GENERATION into a fresh directory, renames
// it into place atomically, then advances a durable published watermark.
// External readers read views and the watermark; the contract guarantee at
// the workspace edge is stated per view in terms of PUBLISHED, never
// committed.
package projector

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// View is one materialized file. A view declares which population it reads
// (the projector hands it the matching typed reader and nothing else) and
// how each record maps to its lines; the mapping table is part of the
// contract registry (design note §13).
type View interface {
	Name() string                         // file name under views/<generation>/
	Population() record.Envelope          // which reader it may be given
	Accepts(r record.Record) bool         // which kinds it projects
	Line(r record.Record) ([]byte, error) // one wire line per record, exactly the shared spec's shape
}

// Projector owns the projector lane's cursor and the published watermark.
type Projector struct {
	j     *journal.Journal
	root  *workspace.Announced
	views []View
	cur   *journal.Cursor
}

const (
	laneName      = "projector"
	watermarkFile = "published"
	currentLink   = "current"
)

// New opens the projector over a journal with the given views.
func New(root *workspace.Announced, j *journal.Journal, views ...View) (*Projector, error) {
	for _, v := range views {
		if strings.ContainsAny(v.Name(), "/\\") || v.Name() == "" {
			return nil, fmt.Errorf("projector: bad view name %q", v.Name())
		}
	}
	c, err := j.OpenCursor(laneName)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(root.Path("views"), 0o755); err != nil {
		return nil, err
	}
	return &Projector{j: j, root: root, views: views, cur: c}, nil
}

// Published is the watermark: the highest Seq the current generation
// reflects. Zero means nothing has been published.
func Published(root *workspace.Announced) (uint64, error) {
	raw, err := os.ReadFile(root.Path("views", watermarkFile))
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	n, err := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
	if err != nil {
		return 0, fmt.Errorf("projector: watermark %q does not parse", strings.TrimSpace(string(raw)))
	}
	return n, nil
}

// Current is the directory of the published generation.
func Current(root *workspace.Announced) string { return root.Path("views", currentLink) }

// Publish projects every committed record up to the journal head into a new
// generation and publishes it atomically. It is a full rebuild each time in
// v1 (views are small; incremental append is a later optimisation with a
// declared reason). Returns the published watermark.
func (p *Projector) Publish() (uint64, error) {
	head := p.j.Head()
	gen := p.root.Path("views", fmt.Sprintf("gen-%020d", head))
	// Idempotent at the same head: if the watermark already says head and
	// `current` points at this generation, there is nothing to do.
	if pub, err := Published(p.root); err == nil && pub == head {
		if cur, err := os.Readlink(Current(p.root)); err == nil && cur == filepath.Base(gen) {
			return head, nil
		}
	}
	// A generation dir for this head that is NOT published is debris from a
	// crash between rename and publish: rebuild it.
	os.RemoveAll(gen)
	tmp := gen + ".building"
	os.RemoveAll(tmp)
	if err := os.MkdirAll(tmp, 0o755); err != nil {
		return 0, err
	}
	files := map[string]*bufio.Writer{}
	handles := map[string]*os.File{}
	for _, v := range p.views {
		f, err := os.Create(filepath.Join(tmp, v.Name()))
		if err != nil {
			return 0, err
		}
		handles[v.Name()] = f
		files[v.Name()] = bufio.NewWriter(f)
	}
	for _, v := range p.views {
		w := files[v.Name()]
		emit := func(r record.Record) error {
			if !v.Accepts(r) {
				return nil
			}
			line, err := v.Line(r)
			if err != nil {
				return fmt.Errorf("view %s: record %s: %w", v.Name(), r.Head().ID, err)
			}
			if _, err := w.Write(line); err != nil {
				return err
			}
			if len(line) == 0 || line[len(line)-1] != '\n' {
				return w.WriteByte('\n')
			}
			return nil
		}
		var err error
		switch v.Population() {
		case record.Production:
			err = p.j.Production().Scan(0, emit)
		case record.Control:
			err = p.j.Control().Scan(0, emit)
		case record.Experimental:
			err = p.j.Experimental().Scan(0, emit)
		default:
			err = fmt.Errorf("view %s declares no population", v.Name())
		}
		if err != nil {
			return 0, err
		}
	}
	for name, w := range files {
		if err := w.Flush(); err != nil {
			return 0, err
		}
		if err := handles[name].Sync(); err != nil {
			return 0, err
		}
		if err := handles[name].Close(); err != nil {
			return 0, err
		}
	}
	if err := workspace.SyncDir(tmp); err != nil {
		return 0, err
	}
	if err := os.Rename(tmp, gen); err != nil {
		return 0, err
	}
	// publish: swap the `current` link, then the watermark, then the cursor
	link := Current(p.root)
	tmpLink := link + ".tmp"
	os.Remove(tmpLink)
	if err := os.Symlink(filepath.Base(gen), tmpLink); err != nil {
		return 0, err
	}
	if err := os.Rename(tmpLink, link); err != nil {
		return 0, err
	}
	if err := workspace.SyncDir(p.root.Path("views")); err != nil {
		return 0, err
	}
	if err := workspace.WriteFileDurable(p.root.Path("views", watermarkFile), []byte(strconv.FormatUint(head, 10)+"\n"), 0o644); err != nil {
		return 0, err
	}
	if err := p.cur.Advance(head); err != nil {
		return 0, err
	}
	return head, nil
}

// ThoughtsView is the first-slice view: one line per stored thought. It is
// the successor's own view (no Python twin yet); its mapping row lives in
// contracts/VIEWS.md. Production population only.
type ThoughtsView struct{}

func (ThoughtsView) Name() string                 { return "thoughts.jsonl" }
func (ThoughtsView) Population() record.Envelope  { return record.Production }
func (ThoughtsView) Accepts(r record.Record) bool { return r.Kind() == record.KindThoughtStored }
func (ThoughtsView) Line(r record.Record) ([]byte, error) {
	t, ok := r.(*record.ThoughtStored)
	if !ok {
		return nil, fmt.Errorf("not a ThoughtStored: %T", r)
	}
	return json.Marshal(map[string]any{
		"seq": t.Seq, "id": t.ID, "hash": t.Hash, "kind": t.Thought, "bytes": t.Bytes, "encoding": t.Encoding, "at": t.At,
	})
}
