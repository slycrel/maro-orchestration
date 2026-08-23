package orch

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyjson"
	"github.com/slycrel/maro-orchestration/go/internal/pytext"
	"github.com/slycrel/maro-orchestration/go/internal/record"
)

// The DOING-PID sidecar. Every flip to DOING stamps the marking process's
// pid under NEXT.md's lock; every other transition drops the entry. A
// surviving entry whose pid is dead therefore marks an item whose owner
// died holding it — a leaked lock, which callers revert to TODO.
//
// The file is an ordered object and this port keeps it ordered. Python's
// dict preserves insertion order, so its writes are stable across
// rewrites; ranging a Go map instead would reshuffle the file on every
// mark. No consumer reads the order — but a store that churns on every
// write is unreviewable, and "deterministic output for deterministic
// input" is the rule this port has already been bitten by twice.

type pidEntry struct {
	pid int64
	at  string
	// raw carries an entry this runtime did not write, verbatim, so a
	// forward-version field added by the other runtime survives a rewrite
	// instead of being silently dropped.
	raw string
}

type pidSidecar struct {
	keys    []string
	entries map[string]pidEntry
}

// readDoingPIDs is Python _read_doing_pids: best-effort, and ANY failure
// reads as empty. That tolerance is deliberate — the sidecar is
// forensics, and refusing to mark an item because its forensics file is
// corrupt would trade a real capability for a diagnostic one.
func readDoingPIDs(ws, slug string) *pidSidecar {
	out := &pidSidecar{entries: map[string]pidEntry{}}
	raw, err := os.ReadFile(doingPIDsPath(ws, slug))
	if err != nil {
		return out
	}
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	tok, err := dec.Token()
	if err != nil {
		return out
	}
	if d, ok := tok.(json.Delim); !ok || d != '{' {
		return out
	}
	for dec.More() {
		keyTok, err := dec.Token()
		if err != nil {
			return &pidSidecar{entries: map[string]pidEntry{}}
		}
		key, ok := keyTok.(string)
		if !ok {
			return &pidSidecar{entries: map[string]pidEntry{}}
		}
		var val json.RawMessage
		if err := dec.Decode(&val); err != nil {
			return &pidSidecar{entries: map[string]pidEntry{}}
		}
		if _, dup := out.entries[key]; !dup {
			out.keys = append(out.keys, key)
		}
		out.entries[key] = pidEntry{raw: string(val)}
	}
	return out
}

// get returns the stored pid for one item index, and whether an entry
// exists at all. An entry whose pid cannot be read as an integer is an
// ERROR rather than a default, matching Python: there, int() raises
// straight out of stranded_doing_items. Softening it to "treat an
// unreadable pid as dead" would revert a LIVE item to TODO and hand its
// work to a second executor.
func (s *pidSidecar) get(key string) (int64, bool, error) {
	e, ok := s.entries[key]
	if !ok {
		return 0, false, nil
	}
	var obj map[string]any
	dec := json.NewDecoder(strings.NewReader(e.raw))
	dec.UseNumber()
	if err := dec.Decode(&obj); err != nil {
		// Python's rec.get() on a non-dict raises AttributeError, which
		// also escapes stranded_doing_items. Same direction: loud.
		return 0, true, fmt.Errorf("doing-pid entry %s is not an object: %s",
			pytext.Repr(key), e.raw)
	}
	pid, err := pyIntOfPIDField(obj["pid"])
	if err != nil {
		return 0, true, fmt.Errorf("doing-pid entry %s: %w", pytext.Repr(key), err)
	}
	return pid, true, nil
}

// pyIntOfPIDField is `int(rec.get("pid", 0) or 0)`. The `or 0` turns a
// missing, null, zero or empty-string pid into 0 — and 0 is not a
// sentinel for "unknown" here: kill(0, sig) addresses the caller's
// process GROUP and succeeds, so Python reads such an entry as ALIVE.
// Reproduced rather than corrected, because the two runtimes must agree
// on which items a drain loop is allowed to steal.
func pyIntOfPIDField(v any) (int64, error) {
	switch t := v.(type) {
	case nil:
		return 0, nil
	case bool:
		if t {
			return 1, nil
		}
		return 0, nil
	case string:
		if t == "" {
			return 0, nil
		}
		n, err := strconv.ParseInt(pytext.Strip(t), 10, 64)
		if err != nil {
			return 0, fmt.Errorf("pid %s is not an integer", pytext.Repr(t))
		}
		return n, nil
	case json.Number:
		if n, err := t.Int64(); err == nil {
			return n, nil
		}
		f, err := t.Float64()
		if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, fmt.Errorf("pid %q is not an integer", t.String())
		}
		return int64(math.Trunc(f)), nil
	}
	return 0, fmt.Errorf("pid has an unusable type %T", v)
}

// render is json.dumps(pids, indent=2): two-space indent, no trailing
// newline, insertion order preserved, "{}" when empty.
func (s *pidSidecar) render() (string, error) {
	if len(s.keys) == 0 {
		return "{}", nil
	}
	var b strings.Builder
	b.WriteString("{\n")
	for i, k := range s.keys {
		key, err := pyjson.String(k)
		if err != nil {
			return "", err
		}
		e := s.entries[k]
		body := e.raw
		if body == "" {
			at, err := pyjson.String(e.at)
			if err != nil {
				return "", err
			}
			body = "{\n    \"pid\": " + strconv.FormatInt(e.pid, 10) +
				",\n    \"at\": " + at + "\n  }"
		} else {
			body = reindentEntry(body)
		}
		b.WriteString("  " + key + ": " + body)
		if i < len(s.keys)-1 {
			b.WriteString(",")
		}
		b.WriteString("\n")
	}
	b.WriteString("}")
	return b.String(), nil
}

// reindentEntry re-renders a carried-through entry at the sidecar's
// indent. The value was stored as a raw fragment to keep unknown fields;
// re-indenting it is what json.dumps would do to the dict Python decoded
// from those same bytes.
func reindentEntry(raw string) string {
	var buf bytes.Buffer
	if err := json.Indent(&buf, []byte(raw), "  ", "  "); err != nil {
		return raw
	}
	return buf.String()
}

// stampDoingPID records or clears one item's owner. Best-effort by
// contract (see MarkItem); errors are swallowed exactly where Python's
// bare `except: pass` swallows them, and nowhere else.
func stampDoingPID(ws, slug string, itemIndex int, newState string) {
	pids := readDoingPIDs(ws, slug)
	key := strconv.Itoa(itemIndex)
	if newState == StateDoing {
		if _, exists := pids.entries[key]; !exists {
			pids.keys = append(pids.keys, key)
		}
		// %z on a naive localtime, the way Python's bare strftime call
		// renders it: local offset, no colon. A different spelling here
		// would be a differently-stored row in a shared file.
		pids.entries[key] = pidEntry{
			pid: int64(os.Getpid()),
			at:  time.Now().Format("2006-01-02T15:04:05-0700"),
		}
	} else {
		if _, exists := pids.entries[key]; !exists {
			return // pop(key, None) on an absent key rewrites nothing new
		}
		delete(pids.entries, key)
		kept := pids.keys[:0]
		for _, k := range pids.keys {
			if k != key {
				kept = append(kept, k)
			}
		}
		pids.keys = kept
	}
	body, err := pids.render()
	if err != nil {
		return
	}
	_ = record.AtomicWrite(doingPIDsPath(ws, slug), []byte(body))
}

// StrandedDoingItems returns DOING items whose recorded owner is dead —
// or that predate PID stamping at all.
//
// Both shapes are leaked locks. Since every DOING flip stamps a pid under
// the same lock, a missing entry means either the stamp era had not
// started or the sidecar was lost, and the item has no live owner either
// way. Callers revert these to TODO.
func StrandedDoingItems(ws, slug string) ([]NextItem, error) {
	_, items, err := ParseNext(ws, slug)
	if err != nil {
		return nil, err
	}
	var doing []NextItem
	for _, it := range items {
		if it.State == StateDoing {
			doing = append(doing, it)
		}
	}
	if len(doing) == 0 {
		return nil, nil
	}
	pids := readDoingPIDs(ws, slug)
	var stranded []NextItem
	for _, it := range doing {
		pid, ok, err := pids.get(strconv.Itoa(it.Index))
		if err != nil {
			return nil, err
		}
		if !ok || !pidAlive(pid) {
			stranded = append(stranded, it)
		}
	}
	return stranded, nil
}

// pidAlive is Python's kill(pid, 0) probe: no error means alive, EPERM
// means alive but not ours, ESRCH means gone.
func pidAlive(pid int64) bool {
	if pid > math.MaxInt32 || pid < math.MinInt32 {
		// Out of range for a pid; Python's os.kill raises OverflowError,
		// which escapes stranded_doing_items rather than reading as dead.
		// Go cannot raise here, and reading it as ALIVE is the direction
		// that refuses to steal an item on garbage evidence.
		return true
	}
	err := syscall.Kill(int(pid), 0)
	if err == nil {
		return true
	}
	return err == syscall.EPERM
}
