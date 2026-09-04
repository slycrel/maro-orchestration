// Package projector materializes the shared-spec views from the journal
// (design note §2). It consumes committed records through ONE captured
// head, writes a complete view GENERATION into a unique staging directory
// together with a manifest (the head, the views, their hashes), renames it
// into place, and then swaps `views/current` — the ONLY commit point. The
// published watermark is derived from the current generation's manifest;
// `views/published` is a convenience copy validated against it. Nothing that
// `current` points at is ever deleted.
package projector

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

// View is one materialized file: the population it reads (the projector
// hands it only that reader), which kinds it projects, and the mapping of a
// record to its wire line. Mapping rows live in contracts/VIEWS.md.
type View interface {
	Name() string
	Population() record.Envelope
	Accepts(r record.Record) bool
	Line(r record.Record) ([]byte, error)
}

// Manifest is written inside a generation before it is renamed into place.
type Manifest struct {
	Head  uint64            `json:"head"`
	Views map[string]string `json:"views"` // name → sha256 of the file
	At    time.Time         `json:"at"`
}

// Projector owns the projector lane's cursor and publication.
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
	manifestFile  = "manifest.json"
)

var (
	ErrPublished = errors.New("projector: published state inconsistent")
)

// New opens the projector over a journal with the given views.
func New(j *journal.Journal, views ...View) (*Projector, error) {
	seen := map[string]bool{}
	for _, v := range views {
		n := v.Name()
		if n == "" || strings.ContainsAny(n, "/\\") || n == manifestFile {
			return nil, fmt.Errorf("projector: bad view name %q", n)
		}
		if seen[n] {
			return nil, fmt.Errorf("projector: duplicate view name %q", n)
		}
		seen[n] = true
	}
	c, err := j.OpenCursor(laneName)
	if err != nil {
		return nil, err
	}
	root := j.Root()
	if err := os.MkdirAll(root.Path("views"), 0o755); err != nil {
		return nil, err
	}
	return &Projector{j: j, root: root, views: views, cur: c}, nil
}

// Current is the path of the published generation.
func Current(root *workspace.Announced) string { return root.Path("views", currentLink) }

// Published resolves `current`, validates its manifest and files, checks the
// watermark copy against it, and returns the head it reflects. Zero only
// when nothing has ever been published; any inconsistency is an error, never
// a confident number.
func Published(root *workspace.Announced) (uint64, error) {
	link := Current(root)
	target, err := os.Readlink(link)
	if errors.Is(err, os.ErrNotExist) {
		if _, werr := os.Stat(root.Path("views", watermarkFile)); werr == nil {
			return 0, fmt.Errorf("%w: watermark present but no current generation", ErrPublished)
		}
		return 0, nil
	}
	if err != nil {
		return 0, fmt.Errorf("%w: current is not a symlink: %v", ErrPublished, err)
	}
	if strings.ContainsAny(target, "/\\") || !strings.HasPrefix(target, "gen-") {
		return 0, fmt.Errorf("%w: current points outside views (%q)", ErrPublished, target)
	}
	gen := root.Path("views", target)
	st, err := os.Stat(gen) // follows the one link level; a loop or a dangling link fails here
	if err != nil || !st.IsDir() {
		return 0, fmt.Errorf("%w: current → %q is not a generation directory: %v", ErrPublished, target, err)
	}
	m, err := readManifest(gen)
	if err != nil {
		return 0, err
	}
	for name, sum := range m.Views {
		got, err := fileSum(filepath.Join(gen, name))
		if err != nil || got != sum {
			return 0, fmt.Errorf("%w: view %s in %s does not match its manifest", ErrPublished, name, target)
		}
	}
	if raw, err := os.ReadFile(root.Path("views", watermarkFile)); err == nil {
		n, perr := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
		if perr != nil || n != m.Head {
			return 0, fmt.Errorf("%w: watermark %q disagrees with current manifest head %d", ErrPublished, strings.TrimSpace(string(raw)), m.Head)
		}
	}
	return m.Head, nil
}

func readManifest(gen string) (Manifest, error) {
	var m Manifest
	raw, err := os.ReadFile(filepath.Join(gen, manifestFile))
	if err != nil {
		return m, fmt.Errorf("%w: %s has no manifest: %v", ErrPublished, filepath.Base(gen), err)
	}
	if err := json.Unmarshal(raw, &m); err != nil {
		return m, fmt.Errorf("%w: manifest: %v", ErrPublished, err)
	}
	return m, nil
}

func fileSum(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	s := sha256.Sum256(raw)
	return hex.EncodeToString(s[:]), nil
}

// Publish projects every committed record through ONE captured head into a
// new generation and commits it by swapping `current`. Idempotent at the
// same head (and repairs a lagging watermark or cursor). Full rebuild each
// time in v1. Serialized per journal by the projector lane lock.
func (p *Projector) Publish() (uint64, error) {
	lock := p.j.LaneLock(laneName)
	lock.Lock()
	defer lock.Unlock()

	head := p.j.Head()
	pub, err := Published(p.root)
	if err != nil {
		return 0, err
	}
	if pub == head && head > 0 {
		return head, p.finish(head) // repair watermark/cursor if a crash interrupted them
	}
	final := p.root.Path("views", fmt.Sprintf("gen-%020d", head))
	// A leftover directory for this head is never referenced by current
	// (Published would have returned it): it is debris; rebuild it.
	if cur, err := os.Readlink(Current(p.root)); err == nil && cur == filepath.Base(final) {
		return 0, fmt.Errorf("%w: current points at gen-%d but Published disagrees", ErrPublished, head)
	}
	staging := fmt.Sprintf("%s.%d.%d.building", final, os.Getpid(), time.Now().UnixNano())
	if err := os.MkdirAll(staging, 0o755); err != nil {
		return 0, err
	}
	ok := false
	defer func() {
		if !ok {
			os.RemoveAll(staging)
		}
	}()
	m := Manifest{Head: head, Views: map[string]string{}, At: time.Now().UTC()}
	for _, v := range p.views {
		if err := p.writeView(staging, v, head); err != nil {
			return 0, err
		}
		sum, err := fileSum(filepath.Join(staging, v.Name()))
		if err != nil {
			return 0, err
		}
		m.Views[v.Name()] = sum
	}
	mraw, _ := json.MarshalIndent(m, "", "  ")
	if err := workspace.WriteFileDurable(filepath.Join(staging, manifestFile), append(mraw, '\n'), 0o644); err != nil {
		return 0, err
	}
	if err := workspace.SyncDir(staging); err != nil {
		return 0, err
	}
	os.RemoveAll(final) // never current (checked above); debris from a crash before the swap
	if err := os.Rename(staging, final); err != nil {
		return 0, err
	}
	ok = true
	// THE commit: swap current.
	link := Current(p.root)
	tmpLink := fmt.Sprintf("%s.%d.%d.tmp", link, os.Getpid(), time.Now().UnixNano())
	if err := os.Symlink(filepath.Base(final), tmpLink); err != nil {
		return 0, err
	}
	if err := os.Rename(tmpLink, link); err != nil {
		os.Remove(tmpLink)
		return 0, err
	}
	if err := workspace.SyncDir(p.root.Path("views")); err != nil {
		return 0, err
	}
	return head, p.finish(head)
}

// finish writes the watermark copy and advances the cursor; both are
// derivable from the committed generation, so a crash between them is
// repaired by the next Publish at the same head.
func (p *Projector) finish(head uint64) error {
	if err := workspace.WriteFileDurable(p.root.Path("views", watermarkFile), []byte(strconv.FormatUint(head, 10)+"\n"), 0o644); err != nil {
		return err
	}
	if p.cur.Seq() < head {
		return p.cur.Advance(head)
	}
	return nil
}

func (p *Projector) writeView(staging string, v View, head uint64) (err error) {
	f, err := os.Create(filepath.Join(staging, v.Name()))
	if err != nil {
		return err
	}
	defer func() {
		if cerr := f.Close(); err == nil && cerr != nil {
			err = cerr
		}
	}()
	w := bufio.NewWriter(f)
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
	switch v.Population() {
	case record.Production:
		err = p.j.Production().ScanThrough(0, head, emit)
	case record.Control:
		err = p.j.Control().ScanThrough(0, head, emit)
	case record.Experimental:
		err = p.j.Experimental().ScanThrough(0, head, emit)
	default:
		err = fmt.Errorf("view %s declares no population", v.Name())
	}
	if err != nil {
		return err
	}
	if err := w.Flush(); err != nil {
		return err
	}
	return f.Sync()
}

// ThoughtsView is the first-slice view: one line per stored thought.
// Production population only; mapping row in contracts/VIEWS.md.
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
