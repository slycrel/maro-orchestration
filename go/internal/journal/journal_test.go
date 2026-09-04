package journal

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/record"
	"github.com/slycrel/maro-orchestration/go/internal/workspace"
)

func openJournal(t *testing.T) (*Journal, *workspace.Announced, *workspace.Lease) {
	t.Helper()
	t.Setenv(workspace.EnvOverride, filepath.Join(t.TempDir(), "ws"))
	r, err := workspace.Resolve()
	if err != nil {
		t.Fatal(err)
	}
	a, err := r.Announce(io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	if err := a.Ensure(); err != nil {
		t.Fatal(err)
	}
	l, err := workspace.Acquire(a)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Release() })
	j, err := Open(a, l)
	if err != nil {
		t.Fatal(err)
	}
	return j, a, l
}

func thoughtRec(i int) *record.ThoughtStored {
	return &record.ThoughtStored{Header: record.Header{ID: record.NewID(), Schema: "thought_stored/1", Subject: record.Ref{Kind: "thought", ID: strings.Repeat("a", 64)}, At: time.Now().UTC()},
		Hash: "s256v1:" + strings.Repeat("ab", 32), Thought: "goal", Bytes: int64(i), Encoding: "utf8"}
}

func leaseRec() *record.LeaseRecord {
	return &record.LeaseRecord{Header: record.Header{ID: record.NewID(), Schema: "lease/1", Subject: record.Ref{Kind: "workspace", ID: "root"}, At: time.Now().UTC()}, PID: 1, Epoch: 1, Host: "h"}
}

func submit(t *testing.T, j *Journal, key string, recs ...record.Record) Ack {
	t.Helper()
	a, err := j.Submit(context.Background(), Command{IdempotencyKey: key, Epoch: j.Epoch(), Records: recs})
	if err != nil {
		t.Fatalf("submit %s: %v", key, err)
	}
	return a
}

func TestSubmitAllocatesContiguousSeqAndIsIdempotent(t *testing.T) {
	j, _, _ := openJournal(t)
	a := submit(t, j, "tx-1", thoughtRec(1), thoughtRec(2))
	if a.FirstSeq != 1 || a.LastSeq != 2 || a.Replayed {
		t.Fatalf("%+v", a)
	}
	b := submit(t, j, "tx-1", thoughtRec(3))
	if !b.Replayed || b.FirstSeq != 1 || b.LastSeq != 2 {
		t.Fatalf("replay must return the original ack and write nothing: %+v", b)
	}
	if j.Head() != 2 {
		t.Fatalf("head %d", j.Head())
	}
	c := submit(t, j, "tx-2", thoughtRec(4))
	if c.FirstSeq != 3 {
		t.Fatalf("%+v", c)
	}
}

func TestSubmitRefusals(t *testing.T) {
	j, _, _ := openJournal(t)
	ctx := context.Background()
	if _, err := j.Submit(ctx, Command{Epoch: j.Epoch(), Records: []record.Record{thoughtRec(1)}}); !errors.Is(err, ErrNoKey) {
		t.Fatalf("no key: %v", err)
	}
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch()}); !errors.Is(err, ErrEmptyCommand) {
		t.Fatalf("empty: %v", err)
	}
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch() + 1, Records: []record.Record{thoughtRec(1)}}); !errors.Is(err, ErrStaleEpoch) {
		t.Fatalf("stale epoch: %v", err)
	}
	pre := thoughtRec(1)
	pre.Seq = 9
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch(), Records: []record.Record{pre}}); err == nil || !strings.Contains(err.Error(), "already carries Seq") {
		t.Fatalf("lane-supplied Seq accepted: %v", err)
	}
	bad := thoughtRec(1)
	bad.Subject = record.Ref{}
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch(), Records: []record.Record{bad}}); !errors.Is(err, record.ErrSubject) {
		t.Fatalf("invalid record accepted: %v", err)
	}
	if j.Head() != 0 {
		t.Fatal("a refused command must write nothing")
	}
	// precondition
	submit(t, j, "a", thoughtRec(1))
	zero := uint64(0)
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "b", Epoch: j.Epoch(), ExpectHead: &zero, Records: []record.Record{thoughtRec(2)}}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("precondition: %v", err)
	}
	one := uint64(1)
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "b", Epoch: j.Epoch(), ExpectHead: &one, Records: []record.Record{thoughtRec(2)}}); err != nil {
		t.Fatal(err)
	}
}

// Population separation by construction: three envelopes in one log, each
// reader sees exactly its own, and a frame whose stamp disagrees with the
// registry is refused at decode.
func TestReadersSeeOnlyTheirPopulation(t *testing.T) {
	j, _, _ := openJournal(t)
	submit(t, j, "p", thoughtRec(1))
	submit(t, j, "c", leaseRec())
	submit(t, j, "p2", thoughtRec(2))
	var prod, ctl, exp []record.Kind
	if err := j.Production().Scan(0, func(r record.Record) error { prod = append(prod, r.Kind()); return nil }); err != nil {
		t.Fatal(err)
	}
	if err := j.Control().Scan(0, func(r record.Record) error { ctl = append(ctl, r.Kind()); return nil }); err != nil {
		t.Fatal(err)
	}
	if err := j.Experimental().Scan(0, func(r record.Record) error { exp = append(exp, r.Kind()); return nil }); err != nil {
		t.Fatal(err)
	}
	if len(prod) != 2 || prod[0] != record.KindThoughtStored || len(ctl) != 1 || ctl[0] != record.KindLease || len(exp) != 0 {
		t.Fatalf("prod=%v ctl=%v exp=%v", prod, ctl, exp)
	}
	// poisoned frame: a lease body stamped as production
	e := Encoded{Kind: record.KindLease, Envelope: "production", Seq: 99, Body: []byte(`{}`)}
	if _, err := Decode(e); err == nil || !strings.Contains(err.Error(), "registry says") {
		t.Fatalf("mis-stamped frame decoded: %v", err)
	}
	// scan honours `after`
	var n int
	j.Production().Scan(1, func(record.Record) error { n++; return nil })
	if n != 1 {
		t.Fatalf("after=1 yielded %d", n)
	}
}

// A torn tail is discarded on open: the committed prefix survives, the head
// is right, the idempotency index is rebuilt, and the next Submit continues.
func TestRecoveryTruncatesTornTailAndRebuildsIndex(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "one", thoughtRec(1))
	submit(t, j, "two", thoughtRec(2), thoughtRec(3))
	j.Close()
	path := a.Path("journal", logFile)
	raw, _ := os.ReadFile(path)
	// simulate a crash mid-frame: append a partial third frame
	os.WriteFile(path, append(raw, []byte("MJL1\x00\x00\x00\x40garbage")...), 0o644)
	j2, err := Open(a, l)
	if err != nil {
		t.Fatal(err)
	}
	rec := j2.Recovered()
	if rec.Frames != 2 || rec.Head != 3 || rec.Discarded == 0 {
		t.Fatalf("recovery %+v", rec)
	}
	if st, _ := os.Stat(path); st.Size() != int64(len(raw)) {
		t.Fatalf("torn tail not truncated: %d vs %d", st.Size(), len(raw))
	}
	ack, err := j2.Submit(context.Background(), Command{IdempotencyKey: "two", Epoch: j2.Epoch(), Records: []record.Record{thoughtRec(9)}})
	if err != nil || !ack.Replayed || ack.FirstSeq != 2 {
		t.Fatalf("index not rebuilt: %+v %v", ack, err)
	}
	ack, err = j2.Submit(context.Background(), Command{IdempotencyKey: "three", Epoch: j2.Epoch(), Records: []record.Record{thoughtRec(4)}})
	if err != nil || ack.FirstSeq != 4 {
		t.Fatalf("continue after recovery: %+v %v", ack, err)
	}
	// a corrupted byte INSIDE a committed frame is a checksum failure: that
	// frame and everything after it is discarded, never served
	j2.Close()
	raw, _ = os.ReadFile(path)
	raw[len(raw)-3] ^= 0xff
	os.WriteFile(path, raw, 0o644)
	j3, err := Open(a, l)
	if err != nil {
		t.Fatal(err)
	}
	if j3.Head() != 3 || !strings.Contains(j3.Recovered().Reason, "checksum") {
		t.Fatalf("checksum failure not handled: %+v", j3.Recovered())
	}
	j3.Close()
}

// Every kill point between the sequencer's writes leaves a readable log.
func TestKillBetweenFrames(t *testing.T) {
	j, a, l := openJournal(t)
	for i := 0; i < 5; i++ {
		submit(t, j, "k"+string(rune('a'+i)), thoughtRec(i))
	}
	j.Close()
	path := a.Path("journal", logFile)
	raw, _ := os.ReadFile(path)
	for cut := 1; cut < len(raw); cut += 7 {
		os.WriteFile(path, raw[:cut], 0o644)
		jj, err := Open(a, l)
		if err != nil {
			t.Fatalf("cut %d: %v", cut, err)
		}
		n := 0
		jj.Production().Scan(0, func(r record.Record) error {
			n++
			if r.Head().Seq != uint64(n) {
				t.Fatalf("cut %d: seq %d at position %d", cut, r.Head().Seq, n)
			}
			return nil
		})
		if uint64(n) != jj.Head() {
			t.Fatalf("cut %d: scanned %d head %d", cut, n, jj.Head())
		}
		jj.Close()
	}
}

func TestCursorIsDurableAndBounded(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "a", thoughtRec(1), thoughtRec(2))
	c, err := j.OpenCursor("projector")
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Advance(3); !errors.Is(err, ErrCursor) {
		t.Fatalf("past head accepted: %v", err)
	}
	if err := c.Advance(2); err != nil {
		t.Fatal(err)
	}
	if err := c.Advance(1); !errors.Is(err, ErrCursor) {
		t.Fatalf("backwards accepted: %v", err)
	}
	j.Close()
	j2, _ := Open(a, l)
	c2, err := j2.OpenCursor("projector")
	if err != nil || c2.Seq() != 2 {
		t.Fatalf("cursor not durable: %v %v", c2, err)
	}
	os.WriteFile(a.Path("cursors", "projector"), []byte("junk"), 0o644)
	if _, err := j2.OpenCursor("projector"); !errors.Is(err, ErrCursor) {
		t.Fatalf("malformed cursor accepted: %v", err)
	}
	os.WriteFile(a.Path("cursors", "projector"), []byte("99"), 0o644)
	if _, err := j2.OpenCursor("projector"); !errors.Is(err, ErrCursor) {
		t.Fatalf("cursor ahead of head accepted: %v", err)
	}
	if _, err := j2.OpenCursor("bad lane"); !errors.Is(err, ErrCursor) {
		t.Fatal("bad lane name accepted")
	}
}

func TestOpenRequiresLeaseAndClosedRefuses(t *testing.T) {
	j, a, _ := openJournal(t)
	if _, err := Open(a, nil); err == nil {
		t.Fatal("open without lease")
	}
	j.Close()
	if _, err := j.Submit(context.Background(), Command{IdempotencyKey: "x", Epoch: j.Epoch(), Records: []record.Record{thoughtRec(1)}}); !errors.Is(err, ErrClosed) {
		t.Fatalf("closed: %v", err)
	}
}
