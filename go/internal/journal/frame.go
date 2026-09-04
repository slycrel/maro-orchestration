// Package journal is the workspace's one append-only log of framed
// transactions (design note §2). Lanes submit Commands; one goroutine — the
// sequencer — validates preconditions, allocates Seq, writes ONE framed,
// checksummed envelope per command, fsyncs, and acknowledges. A torn tail on
// recovery is a partial frame with a bad checksum and is discarded; nothing
// inside an unacknowledged envelope is ever visible.
package journal

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"hash/crc32"
	"io"

	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// Envelope is the on-disk unit: one command's records, contiguous Seq.
type Envelope struct {
	TxID     string    `json:"tx_id"` // the command's idempotency key
	Epoch    uint64    `json:"epoch"` // the lease epoch that submitted it
	FirstSeq uint64    `json:"first_seq"`
	LastSeq  uint64    `json:"last_seq"`
	Records  []Encoded `json:"records"`
}

// Encoded is one record as it sits in the log: its kind and population are
// stamped from the REGISTRY at commit time, never taken from the body.
type Encoded struct {
	Kind     record.Kind     `json:"kind"`
	Envelope string          `json:"envelope"`
	Seq      uint64          `json:"seq"`
	Body     json.RawMessage `json:"body"`
}

// Frame layout: magic(4) | len(4, big-endian) | crc32(4, IEEE over payload) | payload(len).
var magic = [4]byte{'M', 'J', 'L', '1'}

const frameHeader = 12

var (
	ErrTorn     = errors.New("journal: torn frame")
	ErrChecksum = errors.New("journal: frame checksum mismatch")
	ErrBadMagic = errors.New("journal: bad frame magic")
	errNoFrame  = errors.New("journal: no frame")
)

func writeFrame(w io.Writer, payload []byte) error {
	var hdr [frameHeader]byte
	copy(hdr[:4], magic[:])
	binary.BigEndian.PutUint32(hdr[4:8], uint32(len(payload)))
	binary.BigEndian.PutUint32(hdr[8:12], crc32.ChecksumIEEE(payload))
	if _, err := w.Write(hdr[:]); err != nil {
		return err
	}
	_, err := w.Write(payload)
	return err
}

// readFrame reads one frame from r. io.EOF at a frame boundary is clean;
// anything shorter than a whole frame, or a checksum mismatch, is ErrTorn /
// ErrChecksum and the caller discards from this offset on.
func readFrame(r io.Reader) ([]byte, error) {
	var hdr [frameHeader]byte
	n, err := io.ReadFull(r, hdr[:])
	if n == 0 && errors.Is(err, io.EOF) {
		return nil, io.EOF
	}
	if err != nil {
		return nil, ErrTorn
	}
	if [4]byte(hdr[:4]) != magic {
		return nil, ErrBadMagic
	}
	ln := binary.BigEndian.Uint32(hdr[4:8])
	sum := binary.BigEndian.Uint32(hdr[8:12])
	payload := make([]byte, ln)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, ErrTorn
	}
	if crc32.ChecksumIEEE(payload) != sum {
		return nil, ErrChecksum
	}
	return payload, nil
}

func encodeEnvelope(e Envelope) ([]byte, error) { return json.Marshal(e) }

func decodeEnvelope(payload []byte) (Envelope, error) {
	var e Envelope
	if err := json.Unmarshal(payload, &e); err != nil {
		return e, fmt.Errorf("journal: envelope: %w", err)
	}
	return e, nil
}
