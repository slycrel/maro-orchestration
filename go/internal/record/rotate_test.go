package record

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// Rotation is a whole-store REWRITE of a file the Python runtime reads and
// appends to, so every pin here is about the two runtimes agreeing on what
// the file contains afterwards — not about disk.

// rotWorkspace builds a workspace with a config that makes rotation fire on
// a small file, and returns (workspace, memoryDir, activeLogPath).
func rotWorkspace(t *testing.T, rotateMB float64, keep int) (string, string, string) {
	t.Helper()
	ws := t.TempDir()
	mem := filepath.Join(ws, "memory")
	if err := os.MkdirAll(mem, 0o755); err != nil {
		t.Fatal(err)
	}
	userDir := t.TempDir()
	body := fmt.Sprintf("captains_log:\n  rotate_mb: %v\n  rotate_keep: %d\n",
		rotateMB, keep)
	if err := os.WriteFile(filepath.Join(mem, "..", "config.yml"),
		[]byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", userDir)
	return ws, mem, filepath.Join(mem, "captains_log.jsonl")
}

// seedLog writes n synthetic rows straight to the active file.
func seedLog(t *testing.T, path string, n int) {
	t.Helper()
	var b strings.Builder
	for i := 0; i < n; i++ {
		b.WriteString(fmt.Sprintf(
			`{"timestamp": "2026-08-23T00:00:%02d+00:00", "event_type": "SEED", `+
				`"subject": "row-%d", "summary": "padding %s", "audience": "system"}`,
			i%60, i, strings.Repeat("x", 200)))
		b.WriteString("\n")
	}
	if err := os.WriteFile(path, []byte(b.String()), 0o644); err != nil {
		t.Fatal(err)
	}
}

func readLines(t *testing.T, path string) []string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var out []string
	for _, l := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(l) != "" {
			out = append(out, l)
		}
	}
	return out
}

// The point of rotation, and the thing that makes it not a deletion: after
// it runs, every entry that was in the active file is still readable, just
// split across the archive and the tail. This test counts them.
func TestRotationMovesEntriesAndLosesNone(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10) // ~1 KiB threshold
	const seeded = 60
	seedLog(t, path, seeded)
	before := readLines(t, path)

	r := New(filepath.Dir(mem))
	if err := r.Event("SEED", "trigger", "the append that trips the size gate",
		nil, ""); err != nil {
		t.Fatal(err)
	}

	archives := ArchivePaths(mem)
	if len(archives) != 1 {
		t.Fatalf("got %d archives, want 1: %v", len(archives), archives)
	}
	archived := readLines(t, archives[0])
	active := readLines(t, path)

	// The tail retention is honoured...
	if len(active) < 10 {
		t.Errorf("active file kept %d rows, want at least the retained 10",
			len(active))
	}
	// ...and NOTHING was dropped. seeded + the trigger row + the LOG_ROTATED
	// audit row must all be present across the two files.
	total := len(archived) + len(active)
	if total < seeded+2 {
		t.Errorf("%d rows survive rotation (%d archived + %d active); seeded %d "+
			"plus the trigger and the audit row is %d — rotation DELETED data",
			total, len(archived), len(active), seeded, seeded+2)
	}
	// Every original row is still findable somewhere.
	have := map[string]bool{}
	for _, l := range append(append([]string{}, archived...), active...) {
		have[l] = true
	}
	for _, l := range before {
		if !have[l] {
			t.Fatalf("a seeded row vanished in rotation: %s", l[:80])
		}
	}
}

// The audit row is the durable record that a rotation happened, and its
// prose is a CONTENT KEY: Python renders it and this port's recurring bug
// family is emitted strings drifting from Python's. So it is compared
// against the row PYTHON'S OWN _maybe_rotate wrote — not a reconstructed
// f-string, which would pin this port to my reading of the source instead
// of to the source.
func TestTheAuditRowIsSpelledTheWayPythonSpellsIt(t *testing.T) {
	pyWS := t.TempDir()
	py := pythonRotate(t, pyWS, 0.001, 10, 60)
	pyRow := findRotatedRow(t, py["active"].(string))

	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)
	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)
	goRow := findRotatedRow(t, readFileString(t, path))

	// The archive NAME carries a wall-clock stamp, so each side's own name
	// is normalized away; everything else must match exactly.
	norm := func(row map[string]any) map[string]any {
		ctx, _ := row["context"].(map[string]any)
		name, _ := ctx["archive"].(string)
		if name == "" {
			t.Fatal("the audit row carries no archive name")
		}
		out := map[string]any{}
		for k, v := range row {
			if str, ok := v.(string); ok {
				v = strings.ReplaceAll(str, name, "<ARCHIVE>")
			}
			out[k] = v
		}
		delete(out, "timestamp")
		c2 := map[string]any{}
		for k, v := range ctx {
			if str, ok := v.(string); ok {
				v = strings.ReplaceAll(str, name, "<ARCHIVE>")
			}
			c2[k] = v
		}
		out["context"] = c2
		return out
	}
	gotJSON, _ := json.Marshal(norm(goRow))
	wantJSON, _ := json.Marshal(norm(pyRow))
	if string(gotJSON) != string(wantJSON) {
		t.Errorf("LOG_ROTATED row differs from the one CPython wrote\n got %s\nwant %s",
			gotJSON, wantJSON)
	}
	// The audience is already covered by the whole-row comparison above.
	// Stated again on its own because it is the one field a reader is most
	// likely to "fix" on intuition: rotation feels like news, and Python's
	// registry says it is not.
	if got, _ := goRow["audience"].(string); got != "system" {
		t.Errorf("audience = %q, want \"system\" — LOG_ROTATED is in Python's "+
			"EVENT_TYPES and NOT in USER_SURFACED_EVENTS", got)
	}
}

func findRotatedRow(t *testing.T, content string) map[string]any {
	t.Helper()
	for _, l := range strings.Split(content, "\n") {
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			continue
		}
		if m["event_type"] == "LOG_ROTATED" {
			return m
		}
	}
	t.Fatal("no LOG_ROTATED row was written; the rotation is silent")
	return nil
}

// The archive name is what makes a lexicographic sort chronological. If the
// stamp stopped being fixed-width UTC, ArchivePaths would still return
// something and it would silently be in the wrong order.
func TestArchiveNamesSortChronologicallyAndNeverMatchTheActiveFile(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)
	r := New(filepath.Dir(mem))

	// Two rotations in the same second exercise the collision suffix. The
	// clock is frozen so they ARE in the same second — left to the wall
	// clock this test still passes when the rotations land in different
	// seconds, and then it never touches the path it is named for.
	frozen := time.Date(2026, 8, 23, 14, 30, 0, 0, time.UTC)
	oldNow := utcNow
	utcNow = func() time.Time { return frozen }
	defer func() { utcNow = oldNow }()

	for i := 0; i < 2; i++ {
		if err := r.Event("SEED", "trigger", strings.Repeat("y", 200), nil, ""); err != nil {
			t.Fatal(err)
		}
		seedLog(t, path, 60)
	}
	if err := r.Event("SEED", "trigger", "final", nil, ""); err != nil {
		t.Fatal(err)
	}

	archives := ArchivePaths(mem)
	if len(archives) < 2 {
		t.Fatalf("got %d archives, want at least 2 (the same-second collision "+
			"path never ran, so this proved nothing): %v", len(archives), archives)
	}
	for _, a := range archives {
		if filepath.Base(a) == "captains_log.jsonl" {
			t.Fatal("ArchivePaths returned the ACTIVE file; the archaeology " +
				"readers would double-count every live row")
		}
	}
	if !sortedAscending(archives) {
		t.Errorf("archives are not in lexicographic order: %v", archives)
	}
	suffixed := 0
	for _, a := range archives {
		if strings.Contains(filepath.Base(a), "-1.jsonl") ||
			strings.Contains(filepath.Base(a), "-2.jsonl") {
			suffixed++
		}
	}
	if suffixed == 0 {
		t.Error("no archive carries a collision suffix, so the same-second " +
			"path never ran and this test proved nothing about it")
	}
	all := AllLogPaths(mem)
	if got := filepath.Base(all[len(all)-1]); got != "captains_log.jsonl" {
		t.Errorf("AllLogPaths ends with %q, want the active file last", got)
	}
	if len(all) != len(archives)+1 {
		t.Errorf("AllLogPaths has %d entries, want %d", len(all), len(archives)+1)
	}
}

func sortedAscending(xs []string) bool {
	for i := 1; i < len(xs); i++ {
		if xs[i-1] > xs[i] {
			return false
		}
	}
	return true
}

// rotate_mb: 0 disables rotation. An operator who turns it off and still
// gets their log rewritten has lost the setting, not gained a feature.
func TestZeroRotateMBDisablesRotationEntirely(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0, 10)
	seedLog(t, path, 200)
	before := len(readLines(t, path))

	r := New(filepath.Dir(mem))
	if err := r.Event("SEED", "trigger", "trip nothing", nil, ""); err != nil {
		t.Fatal(err)
	}
	if archives := ArchivePaths(mem); len(archives) != 0 {
		t.Errorf("rotate_mb: 0 still rotated: %v", archives)
	}
	if after := len(readLines(t, path)); after != before+1 {
		t.Errorf("active file has %d rows, want %d — it was rewritten with "+
			"rotation disabled", after, before+1)
	}
}

// A file with fewer entries than the retention keeps every one of them. The
// guard matters because without it head is empty and the archive would be a
// zero-entry file that ArchivePaths then hands to every reader.
func TestAFileShorterThanTheRetentionIsLeftAlone(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10_000)
	seedLog(t, path, 60)
	before := len(readLines(t, path))

	r := New(filepath.Dir(mem))
	if err := r.Event("SEED", "trigger", "over the size gate, under the tail",
		nil, ""); err != nil {
		t.Fatal(err)
	}
	if archives := ArchivePaths(mem); len(archives) != 0 {
		t.Errorf("rotated with fewer entries than the retention: %v", archives)
	}
	if after := len(readLines(t, path)); after != before+1 {
		t.Errorf("active file has %d rows, want %d", after, before+1)
	}
}

// The re-entrancy guard. The LOG_ROTATED append lands in the fresh active
// file and re-enters the rotation check; without the guard it takes the
// same non-reentrant flock and deadlocks, and with a threshold below the
// retained tail it would cascade. A retention of 0 makes the fresh file
// EMPTY, which is the sharpest version of both.
func TestTheAuditAppendDoesNotReenterRotation(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 0)
	seedLog(t, path, 60)

	done := make(chan struct{})
	go func() {
		defer close(done)
		r := New(filepath.Dir(mem))
		if err := r.Event("SEED", "trigger", "trip the gate", nil, ""); err != nil {
			t.Error(err)
		}
	}()
	select {
	case <-done:
	case <-timeoutAfter(30):
		t.Fatal("rotation did not return within 30s — the audit append " +
			"re-entered Locked, which is not reentrant, and blocked on itself")
	}

	archives := ArchivePaths(mem)
	if len(archives) != 1 {
		t.Fatalf("got %d archives, want exactly 1 — a cascade wrote more: %v",
			len(archives), archives)
	}
	active := readLines(t, path)
	if len(active) != 1 {
		t.Errorf("active file holds %d rows, want just the LOG_ROTATED audit "+
			"row: %v", len(active), active)
	}
}

// Concurrent appenders must not tear the store. One rotates; the others
// keep appending, and every row they wrote must be readable afterwards.
func TestConcurrentAppendsDuringRotationDoNotTearTheStore(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)

	const n = 12
	var wg sync.WaitGroup
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r := New(filepath.Dir(mem))
			if err := r.Event("SEED", fmt.Sprintf("writer-%d", i),
				"concurrent append", nil, ""); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()

	seen := 0
	for _, p := range AllLogPaths(mem) {
		for _, l := range readLines(t, p) {
			var m map[string]any
			if err := json.Unmarshal([]byte(l), &m); err != nil {
				t.Fatalf("%s holds an unparseable row after concurrent "+
					"rotation: %s", filepath.Base(p), truncate(l))
			}
			if s, _ := m["subject"].(string); strings.HasPrefix(s, "writer-") {
				seen++
			}
		}
	}
	if seen != n {
		t.Errorf("%d of %d concurrent appends survive; the rest were lost in "+
			"the rewrite window", seen, n)
	}
}

func truncate(s string) string {
	if len(s) > 120 {
		return s[:120] + "…"
	}
	return s
}

func timeoutAfter(seconds int) <-chan struct{} {
	ch := make(chan struct{})
	go func() {
		time.Sleep(time.Duration(seconds) * time.Second)
		close(ch)
	}()
	return ch
}

// pyRotateProducer runs Python's REAL _maybe_rotate over a seeded log and
// reports what it left behind. Rotation is a whole-store rewrite of a shared
// file, so "roughly the same" is not a standard: the two runtimes must leave
// byte-identical bytes.
const pyRotateProducer = `
import json, os, sys, pathlib
ws = pathlib.Path(sys.argv[1])
assert str(ws).startswith("/tmp/"), f"refusing to touch a non-tmp store: {ws}"
os.environ["MARO_WORKSPACE"] = str(ws)
os.environ["MARO_USER_DIR"] = str(ws / "userdir")
(ws / "userdir").mkdir(parents=True, exist_ok=True)
import captains_log as cl
active = ws / "memory" / "captains_log.jsonl"
cl.set_log_path(active)
cl._maybe_rotate()
archives = sorted(p for p in active.parent.glob("captains_log.*.jsonl"))
print(json.dumps({
    "archive": archives[0].read_text(encoding="utf-8") if archives else None,
    "n_archives": len(archives),
    "active": active.read_text(encoding="utf-8"),
}))
`

func pythonRotate(t *testing.T, ws string, rotateMB float64, keep, rows int) map[string]any {
	t.Helper()
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(src, "captains_log.py")); err != nil {
		t.Skipf("Python source tree not present: %v", err)
	}
	if err := os.MkdirAll(filepath.Join(ws, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	cfg := fmt.Sprintf("captains_log:\n  rotate_mb: %v\n  rotate_keep: %d\n",
		rotateMB, keep)
	if err := os.WriteFile(filepath.Join(ws, "config.yml"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	seedLog(t, filepath.Join(ws, "memory", "captains_log.jsonl"), rows)

	cmd := exec.Command("python3", "-c", pyRotateProducer, ws)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 / captains_log unavailable: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(out, &got); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	return got
}

// The differential. Both runtimes rotate the SAME seeded file with the SAME
// config, and the archive must come out byte for byte identical — it is a
// file the other runtime reads.
func TestRotationIsByteIdenticalToPythons(t *testing.T) {
	for _, c := range []struct {
		name string
		keep int
		rows int
	}{
		{"ordinary tail", 10, 60},
		{"retention of zero empties the active file", 0, 60},
		{"retention of one", 1, 60},
		{"retention just under the row count", 59, 60},
	} {
		t.Run(c.name, func(t *testing.T) {
			pyWS := t.TempDir()
			py := pythonRotate(t, pyWS, 0.001, c.keep, c.rows)

			_, mem, path := rotWorkspace(t, 0.001, c.keep)
			seedLog(t, path, c.rows)
			New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

			archives := ArchivePaths(mem)
			wantN := int(py["n_archives"].(float64))
			if len(archives) != wantN {
				t.Fatalf("Go wrote %d archives, CPython wrote %d",
					len(archives), wantN)
			}
			if wantN == 0 {
				return
			}
			gotArchive, err := os.ReadFile(archives[0])
			if err != nil {
				t.Fatal(err)
			}
			if want, _ := py["archive"].(string); string(gotArchive) != want {
				t.Errorf("archive contents differ\n got %d bytes\nwant %d bytes",
					len(gotArchive), len(want))
			}
			// Both runtimes append their own LOG_ROTATED audit row, and the
			// two rows legitimately differ (different archive stamp, different
			// wall clock). Drop that one row from each side; every remaining
			// byte must match — compared as BYTES, not as a list of rows,
			// because Python writes an EMPTY active file for a retention of
			// zero and a bare "\n" is a different file that a line-list
			// comparison cannot see.
			wantActive := withoutAuditRow(py["active"].(string))
			if got := withoutAuditRow(readFileString(t, path)); got != wantActive {
				t.Errorf("active file differs from CPython's byte for byte\n"+
					" got %q\nwant %q", truncate(got), truncate(wantActive))
			}
		})
	}
}

// withoutAuditRow removes the LOG_ROTATED line Go appends, preserving every
// other byte including the presence or absence of a trailing newline.
func withoutAuditRow(s string) string {
	parts := strings.Split(s, "\n")
	kept := parts[:0]
	for _, l := range parts {
		if strings.Contains(l, `"LOG_ROTATED"`) {
			continue
		}
		kept = append(kept, l)
	}
	return strings.Join(kept, "\n")
}

func readFileString(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

// The invariant that makes rotation not a deletion: the active file is
// truncated only AFTER the archive is durable. Driven by failing the
// ARCHIVE write specifically — with the two writes in the wrong order the
// entries would already be gone from the active file when the archive that
// was meant to hold them never lands.
func TestAFailedArchiveWriteLeavesTheActiveFileWhole(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)
	before := readFileString(t, path)

	oldWrite := atomicWrite
	atomicWrite = func(p string, data []byte) error {
		if strings.HasPrefix(filepath.Base(p), "captains_log.2") {
			return fmt.Errorf("injected archive failure")
		}
		return oldWrite(p, data)
	}
	defer func() { atomicWrite = oldWrite }()

	var said []string
	oldWarn := warn
	warn = func(format string, args ...any) {
		said = append(said, fmt.Sprintf(format, args...))
	}
	defer func() { warn = oldWarn }()

	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

	if got := readFileString(t, path); got != before {
		t.Errorf("the active file was rewritten even though the archive could "+
			"not be written: %d bytes before, %d after — the entries meant for "+
			"the archive are simply gone", len(before), len(got))
	}
	if archives := ArchivePaths(mem); len(archives) != 0 {
		t.Errorf("an archive exists after the write failed: %v", archives)
	}
	if len(said) == 0 {
		t.Error("a failed rotation said nothing; it must not be silent")
	}
}

// The collision search is bounded where Python's is not, so the bound has to
// fail LOUDLY and leave the store alone rather than spin or half-rotate.
func TestAnExhaustedArchiveNameSearchAbortsWithoutTouchingTheStore(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)
	before := readFileString(t, path)

	// Freeze the stamp: without this the setup below can straddle a second
	// boundary, freeArchivePath picks a fresh stamp whose names are all
	// free, and the test passes for the wrong reason.
	frozen := time.Date(2026, 8, 23, 14, 30, 0, 0, time.UTC)
	oldNow := utcNow
	utcNow = func() time.Time { return frozen }
	defer func() { utcNow = oldNow }()

	stamp := frozen.Format("20060102-150405")
	if err := os.WriteFile(filepath.Join(mem, "captains_log."+stamp+".jsonl"),
		nil, 0o644); err != nil {
		t.Fatal(err)
	}
	for n := 1; n <= maxArchiveCollisions+1; n++ {
		name := fmt.Sprintf("captains_log.%s-%d.jsonl", stamp, n)
		if err := os.WriteFile(filepath.Join(mem, name), nil, 0o644); err != nil {
			t.Fatal(err)
		}
	}

	var said []string
	oldWarn := warn
	warn = func(format string, args ...any) {
		said = append(said, fmt.Sprintf(format, args...))
	}
	defer func() { warn = oldWarn }()

	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

	if got := readFileString(t, path); got != before {
		t.Errorf("the store was rewritten with no archive name available")
	}
	if len(said) == 0 {
		t.Error("the exhausted search was silent")
	}
}

// Python splits this file with str.splitlines(), which breaks on ten
// separators. This port does not set ensure_ascii, so a Go-written row can
// carry a RAW U+2028 — and then the two runtimes disagree about how many
// rows the file holds. Rotation acts on that count, so the disagreement
// becomes a rewrite that cuts a row in half.
func TestARowHoldingARawLineSeparatorRotatesTheWayPythonRotatesIt(t *testing.T) {
	src, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(src, "captains_log.py")); err != nil {
		t.Skipf("Python source tree not present: %v", err)
	}

	seed := func(p string) {
		var b strings.Builder
		for i := 0; i < 60; i++ {
			if i == 45 {
				// A line that is NOTHING but U+001F. Python's splitlines does
				// not break on it, so it survives as a line, and Python's
				// str.strip() DOES drop it — where Go's strings.TrimSpace
				// leaves it, and the archive gains a junk row the other
				// runtime never wrote.
				b.WriteString("\u001f\n")
			}
			sep := ""
			if i == 30 {
				sep = " " // raw LINE SEPARATOR inside the summary
			}
			b.WriteString(fmt.Sprintf(
				`{"timestamp": "2026-08-23T00:00:%02d+00:00", "event_type": "SEED", `+
					`"subject": "row-%d", "summary": "padding%s%s", "audience": "system"}`,
				i%60, i, sep, strings.Repeat("x", 200)))
			b.WriteString("\n")
		}
		if err := os.WriteFile(p, []byte(b.String()), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	pyWS := t.TempDir()
	if err := os.MkdirAll(filepath.Join(pyWS, "memory"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(pyWS, "config.yml"),
		[]byte("captains_log:\n  rotate_mb: 0.001\n  rotate_keep: 10\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	seed(filepath.Join(pyWS, "memory", "captains_log.jsonl"))
	cmd := exec.Command("python3", "-c", pyRotateProducer, pyWS)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+src)
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 / captains_log unavailable: %v", err)
	}
	var py map[string]any
	if err := json.Unmarshal(out, &py); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}

	_, mem, path := rotWorkspace(t, 0.001, 10)
	seed(path)
	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

	archives := ArchivePaths(mem)
	if len(archives) != 1 {
		t.Fatalf("got %d archives, want 1", len(archives))
	}
	want, _ := py["archive"].(string)
	if got := readFileString(t, archives[0]); got != want {
		t.Errorf("archive differs from CPython's on a row holding a raw "+
			"U+2028 — the two runtimes disagree about where rows begin\n"+
			" got %d bytes\nwant %d bytes", len(got), len(want))
	}
}

// The size is checked twice: once cheaply before the lock, and again under
// it. The second check is not belt-and-braces — between the two, ANOTHER
// process holding the lock can rotate the file out from under this one, and
// without the re-check this runtime then rotates the small file that is
// left, archiving a handful of rows for no reason and cutting the retained
// window down twice.
//
// Observing that needs a real race, so this test IS one: it holds the lock,
// lets a rotation reach it, shrinks the file underneath, and releases.
func TestTheSizeIsRecheckedUnderTheLock(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 2)
	seedLog(t, path, 60)

	held := make(chan struct{})
	released := make(chan struct{})
	go func() {
		if err := Locked(path, func() error {
			close(held)
			<-released
			return nil
		}); err != nil {
			t.Error(err)
		}
	}()
	<-held

	rotDone := make(chan struct{})
	go func() {
		defer close(rotDone)
		New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)
	}()

	// Let the rotation take its pre-lock stat (it sees the big file) and
	// block on the lock we are holding.
	time.Sleep(300 * time.Millisecond)

	// Now be the other process: rotate it ourselves, badly — just shrink it.
	// Small enough to be UNDER the 1,048-byte threshold: five ~125-byte rows.
	// The first version of this test used twenty and the file was still over
	// the gate, so the rotation that followed was correct and the test failed
	// against working code.
	small := make([]string, 5)
	for i := range small {
		small[i] = fmt.Sprintf(
			`{"timestamp": "2026-08-23T01:00:%02d+00:00", "event_type": "SEED", `+
				`"subject": "small-%d", "summary": "s", "audience": "system"}`, i, i)
	}
	shrunk := strings.Join(small, "\n") + "\n"
	if err := os.WriteFile(path, []byte(shrunk), 0o644); err != nil {
		t.Fatal(err)
	}
	close(released)
	<-rotDone

	if archives := ArchivePaths(mem); len(archives) != 0 {
		t.Errorf("rotated a file that was already below the threshold when the "+
			"lock was finally acquired: %v", archives)
	}
	if got := readFileString(t, path); got != shrunk {
		t.Errorf("the shrunk file was rewritten: %d bytes, want %d",
			len(got), len(shrunk))
	}
}
