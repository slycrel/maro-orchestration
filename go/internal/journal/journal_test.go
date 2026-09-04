package journal

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"hash/crc32"
	"io"
	"os"
	"path/filepath"
	"strings"
	"sync"
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
	j, err := Open(l)
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

func logPath(a *workspace.Announced) string { return a.Path("journal", logFile) }

// rawFrame builds a valid frame around an arbitrary payload (tests forge
// on-disk states with it).
func rawFrame(payload []byte) []byte {
	var hdr [frameHeader]byte
	copy(hdr[:4], magic[:])
	binary.BigEndian.PutUint32(hdr[4:8], uint32(len(payload)))
	binary.BigEndian.PutUint32(hdr[8:12], crc32.ChecksumIEEE(payload))
	return append(hdr[:], payload...)
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
	if c := submit(t, j, "tx-2", thoughtRec(4)); c.FirstSeq != 3 {
		t.Fatalf("%+v", c)
	}
}

// A refused command writes nothing AND touches no record: a valid first
// record followed by an invalid second leaves the first's Seq at zero.
func TestRefusedSubmitWritesNothingAndMutatesNothing(t *testing.T) {
	j, _, _ := openJournal(t)
	ctx := context.Background()
	good, bad := thoughtRec(1), thoughtRec(2)
	bad.Subject = record.Ref{}
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch(), Records: []record.Record{good, bad}}); !errors.Is(err, record.ErrSubject) {
		t.Fatalf("invalid record accepted: %v", err)
	}
	if good.Seq != 0 {
		t.Fatalf("refused command mutated an earlier record: seq %d", good.Seq)
	}
	if j.Head() != 0 {
		t.Fatal("a refused command must write nothing")
	}
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "k", Epoch: j.Epoch(), Records: []record.Record{good}}); err != nil {
		t.Fatalf("retry with the corrected command must succeed: %v", err)
	}
	for _, c := range []struct {
		name string
		cmd  Command
		want error
	}{
		{"no key", Command{Epoch: j.Epoch(), Records: []record.Record{thoughtRec(1)}}, ErrNoKey},
		{"empty", Command{IdempotencyKey: "e", Epoch: j.Epoch()}, ErrEmptyCommand},
		{"stale epoch", Command{IdempotencyKey: "s", Epoch: j.Epoch() + 1, Records: []record.Record{thoughtRec(1)}}, ErrStaleEpoch},
		{"epoch zero", Command{IdempotencyKey: "z", Epoch: 0, Records: []record.Record{thoughtRec(1)}}, ErrStaleEpoch},
	} {
		if _, err := j.Submit(ctx, c.cmd); !errors.Is(err, c.want) {
			t.Fatalf("%s: %v", c.name, err)
		}
	}
	pre := thoughtRec(1)
	pre.Seq = 9
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "p", Epoch: j.Epoch(), Records: []record.Record{pre}}); err == nil || !strings.Contains(err.Error(), "already carries Seq") {
		t.Fatalf("lane-supplied Seq accepted: %v", err)
	}
	zero := uint64(0)
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "b", Epoch: j.Epoch(), ExpectHead: &zero, Records: []record.Record{thoughtRec(2)}}); !errors.Is(err, ErrPrecondition) {
		t.Fatalf("precondition: %v", err)
	}
	one := uint64(1)
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "b", Epoch: j.Epoch(), ExpectHead: &one, Records: []record.Record{thoughtRec(2)}}); err != nil {
		t.Fatal(err)
	}
}

// Only a LIVE lease opens or writes a journal, and it fixes the root.
func TestLeaseIsTheOnlyDoor(t *testing.T) {
	j, _, l := openJournal(t)
	if _, err := Open(&workspace.Lease{Epoch: 0}); !errors.Is(err, ErrLease) {
		t.Fatalf("fabricated lease accepted: %v", err)
	}
	if _, err := Open(nil); !errors.Is(err, ErrLease) {
		t.Fatalf("nil lease accepted: %v", err)
	}
	submit(t, j, "a", thoughtRec(1))
	l.Release()
	if _, err := j.Submit(context.Background(), Command{IdempotencyKey: "b", Epoch: j.Epoch(), Records: []record.Record{thoughtRec(2)}}); !errors.Is(err, ErrLease) {
		t.Fatalf("submit on a released lease accepted: %v", err)
	}
	if _, err := Open(l); !errors.Is(err, ErrLease) {
		t.Fatalf("open on a released lease accepted: %v", err)
	}
}

func TestReadersSeeOnlyTheirPopulation(t *testing.T) {
	j, _, _ := openJournal(t)
	submit(t, j, "p", thoughtRec(1))
	submit(t, j, "c", leaseRec())
	submit(t, j, "p2", thoughtRec(2))
	var prod, ctl, exp []record.Kind
	must := func(err error) {
		if err != nil {
			t.Fatal(err)
		}
	}
	must(j.Production().Scan(0, func(r record.Record) error { prod = append(prod, r.Kind()); return nil }))
	must(j.Control().Scan(0, func(r record.Record) error { ctl = append(ctl, r.Kind()); return nil }))
	must(j.Experimental().Scan(0, func(r record.Record) error { exp = append(exp, r.Kind()); return nil }))
	if len(prod) != 2 || prod[0] != record.KindThoughtStored || len(ctl) != 1 || ctl[0] != record.KindLease || len(exp) != 0 {
		t.Fatalf("prod=%v ctl=%v exp=%v", prod, ctl, exp)
	}
	var n int
	must(j.Production().Scan(1, func(record.Record) error { n++; return nil }))
	if n != 1 {
		t.Fatalf("after=1 yielded %d", n)
	}
	var seqs []uint64
	must(j.Production().ScanThrough(0, 2, func(r record.Record) error { seqs = append(seqs, r.Head().Seq); return nil }))
	if len(seqs) != 1 || seqs[0] != 1 {
		t.Fatalf("through=2 (a control record at 2): %v", seqs)
	}
	if err := j.Production().ScanThrough(0, 99, func(record.Record) error { return nil }); !errors.Is(err, ErrIncomplete) {
		t.Fatalf("through past head accepted: %v", err)
	}
}

// Decode binds the body to the frame: seq and kind must agree, and the stamp
// must agree with the registry.
func TestDecodeBindsBodyToFrame(t *testing.T) {
	r := thoughtRec(1)
	r.Seq = 7
	body, _ := json.Marshal(r)
	if _, err := Decode(Encoded{Kind: record.KindThoughtStored, Envelope: "production", Seq: 7, Body: body}); err != nil {
		t.Fatal(err)
	}
	if _, err := Decode(Encoded{Kind: record.KindThoughtStored, Envelope: "production", Seq: 8, Body: body}); !errors.Is(err, ErrEnvelope) {
		t.Fatalf("body/frame seq mismatch accepted: %v", err)
	}
	if _, err := Decode(Encoded{Kind: record.KindLease, Envelope: "control", Seq: 7, Body: body}); !errors.Is(err, ErrEnvelope) {
		t.Fatalf("body/frame kind mismatch accepted: %v", err)
	}
	if _, err := Decode(Encoded{Kind: record.KindLease, Envelope: "production", Seq: 7, Body: body}); !errors.Is(err, ErrEnvelope) {
		t.Fatalf("mis-stamped envelope accepted: %v", err)
	}
}

// forge writes a valid-CRC frame with an arbitrary envelope to the log.
func forge(t *testing.T, a *workspace.Announced, e Envelope) {
	t.Helper()
	payload, _ := json.Marshal(e)
	f, err := os.OpenFile(logPath(a), os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	f.Write(rawFrame(payload))
	f.Close()
}

func encodedOf(t *testing.T, r record.Record, seq uint64) Encoded {
	t.Helper()
	r.Head().Seq = seq
	body, _ := json.Marshal(r)
	kind, _ := record.KindOf(r)
	env, _ := record.EnvelopeOf(kind)
	return Encoded{Kind: kind, Envelope: env.String(), Seq: seq, Body: body}
}

// Recovery must-detects: every forged-but-valid-CRC shape is refused WITHOUT
// modifying the log, and the good prefix is still readable after a reopen
// of an unforged copy.
func TestRecoveryRefusesForgedEnvelopes(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "one", thoughtRec(1))
	j.Close()
	clean, _ := os.ReadFile(logPath(a))
	cases := []struct {
		name string
		env  Envelope
		want string
	}{
		{"zero records", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 100}, "no records"},
		{"last_seq mismatch", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 5, Records: []Encoded{encodedOf(t, thoughtRec(2), 2)}}, "last_seq"},
		{"out of order", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 3, Records: []Encoded{encodedOf(t, thoughtRec(3), 3), encodedOf(t, thoughtRec(2), 2)}}, "expected 2"},
		{"duplicate tx_id", Envelope{TxID: "one", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{encodedOf(t, thoughtRec(2), 2)}}, "duplicate tx_id"},
		{"epoch zero", Envelope{TxID: "x", Epoch: 0, FirstSeq: 2, LastSeq: 2, Records: []Encoded{encodedOf(t, thoughtRec(2), 2)}}, "epoch 0"},
		{"seq gap", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 3, LastSeq: 3, Records: []Encoded{encodedOf(t, thoughtRec(3), 3)}}, "expected 2"},
		{"body seq disagrees", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{func() Encoded {
			e := encodedOf(t, thoughtRec(2), 2)
			e.Body = bytes.Replace(e.Body, []byte(`"seq":2`), []byte(`"seq":700`), 1)
			return e
		}()}}, "body seq"},
		{"wrong stamp", Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{func() Encoded { e := encodedOf(t, leaseRec(), 2); e.Envelope = "production"; return e }()}}, "registry says"},
		{"empty tx_id", Envelope{TxID: "", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{encodedOf(t, thoughtRec(2), 2)}}, "empty tx_id"},
	}
	for _, c := range cases {
		os.WriteFile(logPath(a), clean, 0o644)
		forge(t, a, c.env)
		before, _ := os.ReadFile(logPath(a))
		_, err := Open(l)
		if !errors.Is(err, ErrCorrupt) || !strings.Contains(err.Error(), c.want) {
			t.Fatalf("%s: want ErrCorrupt containing %q, got %v", c.name, c.want, err)
		}
		after, _ := os.ReadFile(logPath(a))
		if !bytes.Equal(before, after) {
			t.Fatalf("%s: refusal modified the log", c.name)
		}
	}
	// a first frame at seq 2 on an empty log
	os.WriteFile(logPath(a), nil, 0o644)
	forge(t, a, Envelope{TxID: "x", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{encodedOf(t, thoughtRec(2), 2)}})
	if _, err := Open(l); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("first frame at seq 2 accepted: %v", err)
	}
}

// Only a genuinely SHORT tail is truncated; interior corruption (bad magic,
// bad checksum, a bad length) refuses to open without touching the log.
func TestRecoveryTruncatesOnlyAShortTail(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "one", thoughtRec(1))
	submit(t, j, "two", thoughtRec(2), thoughtRec(3))
	j.Close()
	clean, _ := os.ReadFile(logPath(a))
	// short tail: a partial header, then a partial frame
	for _, tail := range [][]byte{[]byte("MJ"), append([]byte("MJL1\x00\x00\x00\x40"), []byte("garb")...)} {
		os.WriteFile(logPath(a), append(clean, tail...), 0o644)
		j2, err := Open(l)
		if err != nil {
			t.Fatalf("short tail must recover: %v", err)
		}
		if j2.Recovered().Frames != 2 || j2.Head() != 3 || j2.Recovered().Discarded != int64(len(tail)) {
			t.Fatalf("recovery %+v", j2.Recovered())
		}
		if st, _ := os.Stat(logPath(a)); st.Size() != int64(len(clean)) {
			t.Fatal("tail not truncated")
		}
		ack, err := j2.Submit(context.Background(), Command{IdempotencyKey: "two", Epoch: j2.Epoch(), Records: []record.Record{thoughtRec(9)}})
		if err != nil || !ack.Replayed || ack.FirstSeq != 2 {
			t.Fatalf("index not rebuilt: %+v %v", ack, err)
		}
		if ack, err := j2.Submit(context.Background(), Command{IdempotencyKey: "three", Epoch: j2.Epoch(), Records: []record.Record{thoughtRec(4)}}); err != nil || ack.FirstSeq != 4 {
			t.Fatalf("continue after recovery: %+v %v", ack, err)
		}
		j2.Close()
	}
	// interior corruption: a flipped byte inside frame 1, frame 2 intact
	bad := append([]byte{}, clean...)
	bad[20] ^= 0xff
	os.WriteFile(logPath(a), bad, 0o644)
	if _, err := Open(l); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("interior checksum corruption must refuse: %v", err)
	}
	if after, _ := os.ReadFile(logPath(a)); !bytes.Equal(after, bad) {
		t.Fatal("refusal modified the log")
	}
	// bad magic mid-file, valid suffix
	bad = append([]byte{}, clean...)
	copy(bad[0:4], "XXXX")
	os.WriteFile(logPath(a), bad, 0o644)
	if _, err := Open(l); !errors.Is(err, ErrCorrupt) {
		t.Fatalf("bad magic must refuse: %v", err)
	}
	// length bounds: zero and huge
	for _, ln := range []uint32{0, 0xFFFFFFFF, MaxPayload + 1} {
		hdr := append([]byte("MJL1"), 0, 0, 0, 0, 0, 0, 0, 0)
		binary.BigEndian.PutUint32(hdr[4:8], ln)
		os.WriteFile(logPath(a), append(clean, hdr...), 0o644)
		_, err := Open(l)
		if ln == 0 || ln > MaxPayload {
			if !errors.Is(err, ErrCorrupt) {
				t.Fatalf("length %d accepted: %v", ln, err)
			}
		}
	}
}

// A scan that cannot prove coverage through its head FAILS; it never returns
// a short result as success.
func TestScanRefusesToLieAboutCoverage(t *testing.T) {
	j, a, _ := openJournal(t)
	submit(t, j, "one", thoughtRec(1))
	submit(t, j, "two", thoughtRec(2))
	submit(t, j, "three", thoughtRec(3))
	raw, _ := os.ReadFile(logPath(a))
	raw[len(raw)/2] ^= 0xff // corrupt a frame BELOW the head while the journal is open
	os.WriteFile(logPath(a), raw, 0o644)
	var seen int
	err := j.Production().Scan(0, func(record.Record) error { seen++; return nil })
	if !errors.Is(err, ErrIncomplete) {
		t.Fatalf("scan over a corrupt committed frame returned %v after %d records", err, seen)
	}
}

// Every cut of the file on a frame boundary or inside the last frame opens
// to a contiguous prefix; a cut inside an INTERIOR frame is a short tail
// too (everything after the cut is gone), so it also recovers.
func TestKillBetweenAndInsideFrames(t *testing.T) {
	j, a, l := openJournal(t)
	for i := 0; i < 5; i++ {
		submit(t, j, "k"+string(rune('a'+i)), thoughtRec(i))
	}
	j.Close()
	raw, _ := os.ReadFile(logPath(a))
	for cut := 1; cut < len(raw); cut += 7 {
		os.WriteFile(logPath(a), raw[:cut], 0o644)
		jj, err := Open(l)
		if err != nil {
			t.Fatalf("cut %d: %v", cut, err)
		}
		n := 0
		if err := jj.Production().Scan(0, func(r record.Record) error {
			n++
			if r.Head().Seq != uint64(n) {
				t.Fatalf("cut %d: seq %d at position %d", cut, r.Head().Seq, n)
			}
			return nil
		}); err != nil {
			t.Fatalf("cut %d: %v", cut, err)
		}
		if uint64(n) != jj.Head() {
			t.Fatalf("cut %d: scanned %d head %d", cut, n, jj.Head())
		}
		jj.Close()
	}
}

// A write failure poisons the journal: no further Submit can ack until a
// reopen recovers the truth.
func TestWriteFailurePoisons(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "one", thoughtRec(1))
	j.f.Close() // simulate the file becoming unwritable
	if _, err := j.Submit(context.Background(), Command{IdempotencyKey: "two", Epoch: j.Epoch(), Records: []record.Record{thoughtRec(2)}}); !errors.Is(err, ErrPoisoned) {
		t.Fatalf("write failure did not poison: %v", err)
	}
	if _, err := j.Submit(context.Background(), Command{IdempotencyKey: "three", Epoch: j.Epoch(), Records: []record.Record{thoughtRec(3)}}); !errors.Is(err, ErrPoisoned) {
		t.Fatalf("poisoned journal accepted a submit: %v", err)
	}
	j.Close()
	j2, err := Open(l)
	if err != nil {
		t.Fatal(err)
	}
	if j2.Head() != 1 {
		t.Fatalf("head after reopen %d", j2.Head())
	}
	_ = a
	j2.Close()
}

func TestCursorIsOnePerLaneLockedAndBounded(t *testing.T) {
	j, a, l := openJournal(t)
	submit(t, j, "a", thoughtRec(1), thoughtRec(2))
	c, err := j.OpenCursor("projector")
	if err != nil {
		t.Fatal(err)
	}
	c2, _ := j.OpenCursor("projector")
	if c2 != c {
		t.Fatal("two handles for one lane")
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
	// concurrent advances never regress the durable value
	submit(t, j, "b", thoughtRec(3), thoughtRec(4), thoughtRec(5))
	var wg sync.WaitGroup
	for _, s := range []uint64{5, 3, 4, 5, 3} {
		wg.Add(1)
		go func(s uint64) { defer wg.Done(); _ = c.Advance(s) }(s)
	}
	wg.Wait()
	raw, _ := os.ReadFile(a.Path("cursors", "projector"))
	if strings.TrimSpace(string(raw)) != "5" || c.Seq() != 5 {
		t.Fatalf("durable %q memory %d", raw, c.Seq())
	}
	j.Close()
	j2, _ := Open(l)
	c3, err := j2.OpenCursor("projector")
	if err != nil || c3.Seq() != 5 {
		t.Fatalf("cursor not durable: %v %v", c3, err)
	}
	os.WriteFile(a.Path("cursors", "other"), []byte("junk"), 0o644)
	if _, err := j2.OpenCursor("other"); !errors.Is(err, ErrCursor) {
		t.Fatalf("malformed cursor accepted: %v", err)
	}
	os.WriteFile(a.Path("cursors", "ahead"), []byte("99"), 0o644)
	if _, err := j2.OpenCursor("ahead"); !errors.Is(err, ErrCursor) {
		t.Fatalf("cursor ahead of head accepted: %v", err)
	}
	if _, err := j2.OpenCursor("bad lane"); !errors.Is(err, ErrCursor) {
		t.Fatal("bad lane name accepted")
	}
	j2.Close()
	if _, err := j2.OpenCursor("x"); !errors.Is(err, ErrClosed) {
		t.Fatal("cursor on a closed journal")
	}
	if err := c3.Advance(5); !errors.Is(err, ErrClosed) {
		t.Fatal("advance on a closed journal")
	}
}

func TestClosedRefusesAndCloseIsIdempotent(t *testing.T) {
	j, _, _ := openJournal(t)
	submit(t, j, "a", thoughtRec(1))
	if err := j.Close(); err != nil {
		t.Fatal(err)
	}
	if err := j.Close(); err != nil {
		t.Fatalf("second close: %v", err)
	}
	if _, err := j.Submit(context.Background(), Command{IdempotencyKey: "x", Epoch: j.Epoch(), Records: []record.Record{thoughtRec(1)}}); !errors.Is(err, ErrClosed) {
		t.Fatalf("closed: %v", err)
	}
	if err := j.Production().Scan(0, func(record.Record) error { return nil }); !errors.Is(err, ErrClosed) {
		t.Fatalf("scan on closed: %v", err)
	}
}

// The journal executes every registered type's declared vocabulary at BOTH
// doors: a wire-invalid record is refused at Submit (nothing written), and a
// forged frame whose body is structurally fine but out of vocabulary is
// refused at recovery without modifying the log. Before this round Validate
// never called ValidateWire, so "rejected" rows in the declared contracts
// were documentation only.
func TestJournalExecutesDeclaredVocabulary(t *testing.T) {
	j, a, l := openJournal(t)
	ctx := context.Background()
	submit(t, j, "one", thoughtRec(1))
	bad := thoughtRec(2)
	bad.Thought = "haiku"
	if _, err := j.Submit(ctx, Command{IdempotencyKey: "bad", Epoch: j.Epoch(), Records: []record.Record{bad}}); err == nil || !strings.Contains(err.Error(), "out of vocabulary") {
		t.Fatalf("wire-invalid record accepted at submit: %v", err)
	}
	if j.Head() != 1 {
		t.Fatalf("refused submit wrote something: head %d", j.Head())
	}
	j.Close()
	forged := thoughtRec(2)
	forged.Encoding = "ebcdic"
	forge(t, a, Envelope{TxID: "f", Epoch: l.Epoch, FirstSeq: 2, LastSeq: 2, Records: []Encoded{encodedOf(t, forged, 2)}})
	before, _ := os.ReadFile(logPath(a))
	if _, err := Open(l); !errors.Is(err, ErrCorrupt) || !strings.Contains(err.Error(), "out of vocabulary") {
		t.Fatalf("wire-invalid forged frame accepted at recovery: %v", err)
	}
	after, _ := os.ReadFile(logPath(a))
	if !bytes.Equal(before, after) {
		t.Fatal("refusal modified the log")
	}
}

// A pinned reader is fixed at its head: Scan and ScanEpochs read through
// the pin whatever lands after it, and an explicit range past the pin is
// refused rather than quietly read.
func TestPinnedReaderIsFixedAtItsHead(t *testing.T) {
	j, _, _ := openJournal(t)
	submit(t, j, "a", thoughtRec(1))
	pinned := j.Production().Pin()
	submit(t, j, "b", thoughtRec(2))
	if pinned.Head() != 1 || j.Production().Head() != 2 || pinned.Pin() != pinned {
		t.Fatalf("pin: %d live: %d", pinned.Head(), j.Production().Head())
	}
	n := 0
	if err := pinned.Scan(0, func(record.Record) error { n++; return nil }); err != nil || n != 1 {
		t.Fatalf("pinned scan saw %d (%v)", n, err)
	}
	n = 0
	if err := pinned.ScanEpochs(0, func(uint64, record.Record) error { n++; return nil }); err != nil || n != 1 {
		t.Fatalf("pinned epoch scan saw %d (%v)", n, err)
	}
	if err := pinned.ScanThrough(0, 2, func(record.Record) error { return nil }); !errors.Is(err, ErrBeyondPin) {
		t.Fatalf("scan past the pin: %v", err)
	}
}
