package projector

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/journal"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func setup(t *testing.T) (*workspace.Announced, *journal.Journal) {
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
	return a, j
}

func thought(i int) *record.ThoughtStored {
	return &record.ThoughtStored{Header: record.Header{ID: record.NewID(), Schema: "thought_stored/1", Subject: record.Ref{Kind: "thought", ID: "x"}, At: time.Now().UTC()},
		Hash: "s256v1:" + strings.Repeat("ab", 32), Thought: "goal", Bytes: int64(i), Encoding: "utf8"}
}

func lease() *record.LeaseRecord {
	return &record.LeaseRecord{Header: record.Header{ID: record.NewID(), Schema: "lease/1", Subject: record.Ref{Kind: "workspace", ID: "root"}, At: time.Now().UTC()}, PID: 1, Epoch: 1}
}

func put(t *testing.T, j *journal.Journal, key string, recs ...record.Record) {
	t.Helper()
	if _, err := j.Submit(context.Background(), journal.Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: recs}); err != nil {
		t.Fatal(err)
	}
}

func TestCommittedVsPublished(t *testing.T) {
	a, j := setup(t)
	p, err := New(j, ThoughtsView{})
	if err != nil {
		t.Fatal(err)
	}
	put(t, j, "a", thought(1), thought(2))
	put(t, j, "c", lease())
	if w, err := Published(a); w != 0 || err != nil {
		t.Fatalf("published before Publish: %d %v", w, err)
	}
	if _, err := os.Stat(Current(a)); err == nil {
		t.Fatal("current view exists before any publish")
	}
	w, err := p.Publish()
	if err != nil || w != 3 {
		t.Fatalf("publish: %d %v", w, err)
	}
	if got, err := Published(a); got != 3 || err != nil {
		t.Fatalf("watermark %d %v", got, err)
	}
	raw, err := os.ReadFile(filepath.Join(Current(a), "thoughts.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 || !strings.Contains(lines[0], `"seq":1`) || !strings.Contains(lines[1], `"seq":2`) {
		t.Fatalf("view:\n%s", raw)
	}
	if strings.Contains(string(raw), "lease") {
		t.Fatal("a control record leaked into a production view")
	}
	if w2, err := p.Publish(); err != nil || w2 != 3 {
		t.Fatalf("republish: %d %v", w2, err)
	}
}

// A generation is built through ONE captured head: records committed while
// a publish is in flight never appear in that generation.
func TestGenerationIsCutAtOneHead(t *testing.T) {
	a, j := setup(t)
	put(t, j, "a", thought(1))
	slow := newSlow()
	p, _ := New(j, slow)
	done := make(chan error)
	go func() { _, err := p.Publish(); done <- err }()
	<-slow.started
	put(t, j, "b", thought(2)) // commits Seq 2 while the view is mid-scan
	close(slow.gate)
	if err := <-done; err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(Current(a), "thoughts.jsonl"))
	if strings.Count(string(raw), "\n") != 1 || strings.Contains(string(raw), `"seq":2`) {
		t.Fatalf("generation crossed its head:\n%s", raw)
	}
	if w, _ := Published(a); w != 1 {
		t.Fatalf("watermark %d", w)
	}
}

type slowView struct {
	ThoughtsView
	gate    chan struct{}
	started chan struct{}
	once    sync.Once
}

func (s *slowView) Line(r record.Record) ([]byte, error) {
	s.once.Do(func() { close(s.started) })
	<-s.gate
	return s.ThoughtsView.Line(r)
}

func newSlow() *slowView {
	return &slowView{gate: make(chan struct{}), started: make(chan struct{})}
}

// Kill points: a leftover staging dir; a completed generation renamed but
// current not yet swapped; current swapped but watermark/cursor not written.
// In every case the last good generation stays readable and the next
// Publish converges without deleting anything current points at.
func TestPublishSurvivesEveryKillPoint(t *testing.T) {
	a, j := setup(t)
	p, _ := New(j, ThoughtsView{})
	put(t, j, "a", thought(1))
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	first, _ := os.Readlink(Current(a))
	put(t, j, "b", thought(2))

	// 1. leftover staging dir from a dead process
	os.MkdirAll(a.Path("views", "gen-00000000000000000002.999.1.building"), 0o755)
	if cur, _ := os.Readlink(Current(a)); cur != first {
		t.Fatal("current moved without a publish")
	}
	if w, err := Published(a); w != 1 || err != nil {
		t.Fatalf("watermark moved without a publish: %d %v", w, err)
	}
	// 2. a complete generation renamed into place but current NOT swapped
	os.MkdirAll(a.Path("views", "gen-00000000000000000002"), 0o755)
	os.WriteFile(a.Path("views", "gen-00000000000000000002", "thoughts.jsonl"), []byte("stale\n"), 0o644)
	if w, _ := Published(a); w != 1 {
		t.Fatal("unreferenced generation counted as published")
	}
	if w, err := p.Publish(); err != nil || w != 2 {
		t.Fatalf("%d %v", w, err)
	}
	raw, _ := os.ReadFile(filepath.Join(Current(a), "thoughts.jsonl"))
	if strings.Count(string(raw), "\n") != 2 || strings.Contains(string(raw), "stale") {
		t.Fatalf("second generation wrong:\n%s", raw)
	}
	// 3. current swapped but watermark and cursor lost (crash between)
	put(t, j, "c", thought(3))
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	os.Remove(a.Path("views", watermarkFile))
	os.WriteFile(a.Path("cursors", laneName), []byte("2\n"), 0o644)
	if w, err := Published(a); w != 3 || err != nil {
		t.Fatalf("Published must derive from the current manifest: %d %v", w, err)
	}
	cur3, _ := os.Readlink(Current(a))
	if w, err := p.Publish(); err != nil || w != 3 {
		t.Fatalf("repair publish: %d %v", w, err)
	}
	if cur, _ := os.Readlink(Current(a)); cur != cur3 {
		t.Fatal("repair rebuilt a generation current pointed at")
	}
	if raw, _ := os.ReadFile(a.Path("views", watermarkFile)); strings.TrimSpace(string(raw)) != "3" {
		t.Fatalf("watermark not repaired: %q", raw)
	}
	if _, err := os.Stat(a.Path("views", cur3, "thoughts.jsonl")); err != nil {
		t.Fatal("current generation was deleted")
	}
}

// Published never returns a confident number for inconsistent state.
func TestPublishedRefusesInconsistency(t *testing.T) {
	a, j := setup(t)
	p, _ := New(j, ThoughtsView{})
	put(t, j, "a", thought(1))
	p.Publish()
	// watermark disagrees with the manifest
	os.WriteFile(a.Path("views", watermarkFile), []byte("42\n"), 0o644)
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("watermark/manifest disagreement accepted: %v", err)
	}
	os.WriteFile(a.Path("views", watermarkFile), []byte("1\n"), 0o644)
	// a view altered after publication
	cur, _ := os.Readlink(Current(a))
	os.WriteFile(a.Path("views", cur, "thoughts.jsonl"), []byte("tampered\n"), 0o644)
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("tampered view accepted: %v", err)
	}
	// dangling current
	os.Remove(Current(a))
	os.Symlink("gen-00000000000000000099", Current(a))
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("dangling current accepted: %v", err)
	}
	// symlink loop
	os.Remove(Current(a))
	os.Symlink(currentLink, Current(a))
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("loop accepted: %v", err)
	}
	// current pointing outside views
	os.Remove(Current(a))
	os.Symlink("../journal", Current(a))
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("escape accepted: %v", err)
	}
	// watermark without current
	os.Remove(Current(a))
	if _, err := Published(a); !errors.Is(err, ErrPublished) {
		t.Fatalf("watermark without current accepted: %v", err)
	}
}

type greedyView struct{}

func (greedyView) Name() string                         { return "greedy.jsonl" }
func (greedyView) Population() record.Envelope          { return record.Production }
func (greedyView) Accepts(record.Record) bool           { return true }
func (greedyView) Line(r record.Record) ([]byte, error) { return []byte(string(r.Kind())), nil }

func TestViewCannotEscapeItsPopulation(t *testing.T) {
	a, j := setup(t)
	p, _ := New(j, greedyView{})
	put(t, j, "c", lease())
	put(t, j, "p", thought(1))
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(Current(a), "greedy.jsonl"))
	if strings.TrimSpace(string(raw)) != "thought_stored" {
		t.Fatalf("greedy production view saw: %q", raw)
	}
}

type badName struct{ greedyView }

func (badName) Name() string { return "../escape" }

func TestViewNamesAreChecked(t *testing.T) {
	_, j := setup(t)
	if _, err := New(j, badName{}); err == nil {
		t.Fatal("path-traversing view name accepted")
	}
	if _, err := New(j, greedyView{}, greedyView{}); err == nil {
		t.Fatal("duplicate view name accepted")
	}
}

// Two publishers on one journal are serialized; both converge on one
// generation and current is never dangling.
func TestConcurrentPublishersSerialize(t *testing.T) {
	a, j := setup(t)
	p1, _ := New(j, ThoughtsView{})
	p2, _ := New(j, ThoughtsView{})
	put(t, j, "a", thought(1), thought(2))
	var wg sync.WaitGroup
	for _, p := range []*Projector{p1, p2, p1, p2} {
		wg.Add(1)
		go func(p *Projector) {
			defer wg.Done()
			if _, err := p.Publish(); err != nil {
				t.Error(err)
			}
		}(p)
	}
	wg.Wait()
	if w, err := Published(a); w != 2 || err != nil {
		t.Fatalf("%d %v", w, err)
	}
}
