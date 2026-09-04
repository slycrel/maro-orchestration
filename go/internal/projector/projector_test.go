package projector

import (
	"context"
	"io"
	"os"
	"path/filepath"
	"strings"
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
	j, err := journal.Open(a, l)
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

// Committed and published are two durable states: nothing is visible at
// the view edge until Publish, and after it the watermark equals the head.
func TestCommittedVsPublished(t *testing.T) {
	a, j := setup(t)
	p, err := New(a, j, ThoughtsView{})
	if err != nil {
		t.Fatal(err)
	}
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "a", Epoch: j.Epoch(), Records: []record.Record{thought(1), thought(2)}})
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "c", Epoch: j.Epoch(), Records: []record.Record{lease()}})
	if w, _ := Published(a); w != 0 {
		t.Fatalf("published before Publish: %d", w)
	}
	if _, err := os.Stat(Current(a)); err == nil {
		t.Fatal("current view exists before any publish")
	}
	w, err := p.Publish()
	if err != nil || w != 3 {
		t.Fatalf("publish: %d %v", w, err)
	}
	if got, _ := Published(a); got != 3 {
		t.Fatalf("watermark %d", got)
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
	// idempotent republish at the same head
	if w2, err := p.Publish(); err != nil || w2 != 3 {
		t.Fatalf("republish: %d %v", w2, err)
	}
}

// A crash during generation leaves the previous generation current: the
// building directory is never the target of `current`.
func TestPublishIsAtomicAtTheViewEdge(t *testing.T) {
	a, j := setup(t)
	p, _ := New(a, j, ThoughtsView{})
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "a", Epoch: j.Epoch(), Records: []record.Record{thought(1)}})
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	first, _ := os.Readlink(Current(a))
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "b", Epoch: j.Epoch(), Records: []record.Record{thought(2)}})
	// simulate a crash mid-build: a leftover .building dir from a dead process
	os.MkdirAll(a.Path("views", "gen-00000000000000000002.building"), 0o755)
	os.WriteFile(a.Path("views", "gen-00000000000000000002.building", "thoughts.jsonl"), []byte("half"), 0o644)
	if cur, _ := os.Readlink(Current(a)); cur != first {
		t.Fatal("current moved without a publish")
	}
	if w, _ := Published(a); w != 1 {
		t.Fatalf("watermark moved without a publish: %d", w)
	}
	// the next publish replaces the leftover and completes
	if w, err := p.Publish(); err != nil || w != 2 {
		t.Fatalf("%d %v", w, err)
	}
	raw, _ := os.ReadFile(filepath.Join(Current(a), "thoughts.jsonl"))
	if strings.Count(string(raw), "\n") != 2 {
		t.Fatalf("second generation incomplete:\n%s", raw)
	}
}

// A view may only be handed the reader of the population it declares: a
// production view over a log holding control rows never sees them, even if
// its Accepts is permissive.
type greedyView struct{}

func (greedyView) Name() string                         { return "greedy.jsonl" }
func (greedyView) Population() record.Envelope          { return record.Production }
func (greedyView) Accepts(record.Record) bool           { return true }
func (greedyView) Line(r record.Record) ([]byte, error) { return []byte(string(r.Kind())), nil }

func TestViewCannotEscapeItsPopulation(t *testing.T) {
	a, j := setup(t)
	p, _ := New(a, j, greedyView{})
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "c", Epoch: j.Epoch(), Records: []record.Record{lease()}})
	j.Submit(context.Background(), journal.Command{IdempotencyKey: "p", Epoch: j.Epoch(), Records: []record.Record{thought(1)}})
	if _, err := p.Publish(); err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(filepath.Join(Current(a), "greedy.jsonl"))
	if strings.TrimSpace(string(raw)) != "thought_stored" {
		t.Fatalf("greedy production view saw: %q", raw)
	}
}

func TestBadViewNameRefused(t *testing.T) {
	a, j := setup(t)
	if _, err := New(a, j, badName{}); err == nil {
		t.Fatal("path-traversing view name accepted")
	}
}

type badName struct{ greedyView }

func (badName) Name() string { return "../escape" }
