package record

import (
	"encoding/json"
	"fmt"
	"math"
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
	// is normalized away.
	//
	// What this comparison can and cannot see, stated honestly. Both sides
	// go through json.Marshal on a map, which SORTS keys and emits compact
	// separators — so the comparison is deliberately blind to key order and
	// to separator spacing. What it pins is the VALUES: subject, summary
	// prose, audience, and every context field.
	//
	// Separator spacing used to be called a "named accepted divergence"
	// here. It was not a divergence anyone had measured — it was pyjson
	// emitting the wrong thing — and the raw-byte assertion below now
	// covers it against CPython directly (mission-r8).
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
	// The comparison above normalizes separators away, which means it
	// would stay green if this writer stopped using pyjson entirely and
	// fell back to encoding/json — the output would canonicalize to the
	// same map. The recurring bug family in this port is an emitted string
	// drifting by a character, so the raw bytes get their own assertion.
	//
	// That assertion used to read: "if the row has Python-style separators,
	// this writer left pyjson" — which was circular. It asserted the
	// package's behaviour rather than CPython's, and pyjson's behaviour was
	// wrong: captains_log.py:619 is a bare `json.dumps(entry)`, whose
	// defaults are `, ` and `: `. So a test with a real CPython-written row
	// sitting in a variable three lines up was pinning the port to the one
	// spelling CPython does not use (mission-r8).
	//
	// It now measures. Python's OWN raw line is the expectation, with the
	// two wall-clock values normalized out — nothing else.
	// One thing stays blind, and it is named rather than normalized away
	// silently: the NESTED context object's key order. Python emits a dict
	// in insertion order; the Go writer holds a map[string]any, which has
	// none, so pyjson sorts it. That is a real residual (PORT.md), it is
	// not fixable at this writer without an ordered-context Event API, and
	// it is NOT the separator/escaping class this test is for. So the
	// comparison is byte-exact up to `"context": ` and the context's own
	// separators are checked separately.
	pyRaw := findRotatedRawLine(t, py["active"].(string))
	goRaw := findRotatedRawLine(t, readFileString(t, path))
	pyHead, pyCtx := splitAtContext(t, normalizeRotatedLine(t, pyRaw))
	goHead, goCtx := splitAtContext(t, normalizeRotatedLine(t, goRaw))
	if pyHead != goHead {
		t.Errorf("the audit row's BYTES differ from the one CPython wrote "+
			"before the context\n go %s\n py %s", goHead, pyHead)
	}
	// The context fragment must still be spelled json.dumps' way — only its
	// key ORDER is allowed to differ.
	if strings.Contains(goCtx, `","`) || strings.Contains(goCtx, `":"`) ||
		!strings.Contains(goCtx, `": `) {
		t.Errorf("the nested context is not json.dumps-spelled:\n go %s\n py %s",
			goCtx, pyCtx)
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

// A non-finite or enormous rotate_mb. `.inf` is what an operator writes to
// mean "never rotate", and Go's int64 conversion turns it into MinInt64 —
// so `size < maxBytes` is false and the log rotates EVERY time, the exact
// inversion of the request, on a file Python considers untouched.
//
// Differentials, so the expectation is CPython's behaviour rather than my
// reading of int()'s overflow rules.
func TestANonFiniteRotationThresholdIsRefusedTheWayPythonRefusesIt(t *testing.T) {
	const rows = 60
	for _, tc := range []struct {
		name string
		cfg  string
	}{
		{"infinity", "captains_log:\n  rotate_mb: .inf\n  rotate_keep: 10\n"},
		{"nan", "captains_log:\n  rotate_mb: .nan\n  rotate_keep: 10\n"},
		{"overflows int64 but finite", "captains_log:\n  rotate_mb: 1.0e300\n  rotate_keep: 10\n"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			py := pythonRotateRawCfg(t, t.TempDir(), tc.cfg, rows)
			if got := int(py["n_archives"].(float64)); got != 0 {
				t.Fatalf("CPython produced %d archives on %s; the premise of "+
					"this case is wrong", got, tc.name)
			}

			_, mem, path := rotWorkspaceRawCfg(t, tc.cfg)
			seedLog(t, path, rows)
			before := readFileString(t, path)
			New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

			if got := len(ArchivePaths(mem)); got != 0 {
				t.Errorf("rotated %d archive(s) on %s where CPython rotated "+
					"none — the shared log now disagrees about which rows "+
					"are live", got, tc.name)
			}
			if after := readFileString(t, path); after != before {
				t.Errorf("the active file was rewritten on %s (%d -> %d bytes); "+
					"CPython left it alone", tc.name, len(before), len(after))
			}
		})
	}
}

// The re-entrancy guard. The LOG_ROTATED append lands in the FRESH active
// file and re-enters the rotation check; without the guard, a threshold
// below the retained tail cascades — one archive per row, until the
// process dies.
//
// The retention is the whole test, and the first version of this pin got
// it exactly backwards. It used retention 0, calling that "the sharpest
// version"; it is the one retention at which the bug CANNOT happen. With
// no tail the fresh active file holds only the ~250-byte audit row, the
// re-entered call returns at the size gate having never consulted the
// guard, and the pin passes against a runtime with the guard deleted —
// verified (adversarial r8 MEDIUM). Retention 10 leaves ~2.5 KB against a
// 1,048-byte gate, so the re-entry reaches the guard and the guard is
// what stops it.
//
// Retention 0 is kept as a second case, labelled for what it actually
// tests: that an empty tail rotates at all.
func TestTheAuditAppendDoesNotReenterRotation(t *testing.T) {
	for _, tc := range []struct {
		name       string
		keep       int
		wantActive int
		guarded    bool
	}{
		{"retained tail exceeds the threshold (the cascade case)", 10, 11, true},
		{"empty tail (rotates, but cannot cascade)", 0, 1, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			_, mem, path := rotWorkspace(t, 0.001, tc.keep)
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
				t.Fatal("rotation did not return within 30s — the audit " +
					"append re-entered rotation and did not stop")
			}

			archives := ArchivePaths(mem)
			if len(archives) != 1 {
				t.Fatalf("got %d archives, want exactly 1 — a cascade wrote "+
					"more: %v", len(archives), archives)
			}
			active := readLines(t, path)
			if len(active) != tc.wantActive {
				t.Errorf("active file holds %d rows, want %d (%d retained + "+
					"the LOG_ROTATED audit row)", len(active), tc.wantActive,
					tc.keep)
			}
			// Says out loud which case is actually exercising the guard, so
			// a future edit to the fixture cannot silently disarm it again.
			if tc.guarded {
				size := int64(0)
				for _, l := range active {
					size += int64(len(l)) + 1
				}
				if size < 1024*1024/1000 {
					t.Fatalf("the post-rotation active file is %d bytes, "+
						"under the %d-byte gate — this case can no longer "+
						"reach the guard and has stopped testing it",
						size, 1024*1024/1000)
				}
			}
		})
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
	return pythonRotateRawCfg(t, ws, fmt.Sprintf(
		"captains_log:\n  rotate_mb: %v\n  rotate_keep: %d\n", rotateMB, keep), rows)
}

// pythonRotateRawCfg is pythonRotate with the config written verbatim, so a
// test can hand Python the exact YAML an operator would — including the
// QUOTED forms that a typed config lookup silently ignores.
func pythonRotateRawCfg(t *testing.T, ws, cfg string, rows int) map[string]any {
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
// separators where a JSONL reader expects one. The reachable rune is
// U+0085 (NEL): Python's json.dumps escapes U+2028 and U+2029
// unconditionally, ensure_ascii or not, so those two can never reach the
// file raw — U+0085 is the only splitlines separator a writer emits
// verbatim. The fixture carries it at row 30 and keeps a U+2028 at row 31
// as labelled defence, so a future encoder change that stops escaping
// shows up here rather than in a rotated log.
//
// When the two runtimes disagree about how many rows the file holds,
// rotation acts on that count, and the disagreement becomes a rewrite
// that cuts a row in half.
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
				// U+0085 (NEL), not U+2028. Measured, U+0085 is the ONLY
				// separator splitlines() breaks on that pyjson emits RAW;
				// U+2028 and U+2029 are escaped unconditionally, so a
				// fixture built on U+2028 pins a case neither runtime's
				// writer can produce while the reachable one goes
				// untested (adversarial r6).
				sep = "\u0085"
			}
			if i == 31 {
				// Kept as well, one row later: the file is shared, and a
				// writer this port does not own could still put a raw
				// U+2028 in it. Defensive, and labelled as such.
				sep = "\u2028"
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
			"U+0085 — the two runtimes disagree about where rows begin\n"+
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

// rotWorkspaceRawCfg is rotWorkspace with the config written verbatim.
func rotWorkspaceRawCfg(t *testing.T, cfg string) (string, string, string) {
	t.Helper()
	ws := t.TempDir()
	mem := filepath.Join(ws, "memory")
	if err := os.MkdirAll(mem, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(ws, "config.yml"), []byte(cfg), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MARO_WORKSPACE", ws)
	t.Setenv("MARO_USER_DIR", t.TempDir())
	return ws, mem, filepath.Join(mem, "captains_log.jsonl")
}

// Python coerces the two rotation keys with float() and int(), so a QUOTED
// value is honoured there. A typed config lookup here is not the same thing:
// it saw a string where it wanted a float64 and fell back to the 5 MB
// default, so the two runtimes rotated the SHARED log at different
// thresholds and each considered rows active that the other had archived
// (adversarial r6).
//
// Both cases below are differentials against Python's own _maybe_rotate, so
// the assertion is "the file agrees", not "my reading of float() agrees".
func TestQuotedRotationConfigIsHonouredTheWayPythonHonoursIt(t *testing.T) {
	const cfg = "captains_log:\n  rotate_mb: \"0.001\"\n  rotate_keep: \"10\"\n"
	const rows = 60

	py := pythonRotateRawCfg(t, t.TempDir(), cfg, rows)
	if py["n_archives"].(float64) != 1 {
		t.Fatalf("CPython did not rotate on a quoted config (%v archives); "+
			"the premise of this test is wrong", py["n_archives"])
	}

	_, mem, path := rotWorkspaceRawCfg(t, cfg)
	seedLog(t, path, rows)
	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

	archives := ArchivePaths(mem)
	if len(archives) != 1 {
		t.Fatalf("got %d archives, want 1 — a quoted rotate_mb was ignored "+
			"and this runtime rotates at a different threshold than Python",
			len(archives))
	}
	if got, want := readFileString(t, archives[0]), py["archive"].(string); got != want {
		t.Errorf("archive differs from CPython's\n got %d bytes\nwant %d bytes",
			len(got), len(want))
	}
	if got, want := withoutAuditRow(readFileString(t, path)),
		withoutAuditRow(py["active"].(string)); got != want {
		t.Errorf("active file differs from CPython's\n got %q\nwant %q",
			truncate(got), truncate(want))
	}
}

// An EXPLICIT null is not an absent key. Python's _cfg_get hands None
// straight to int(), which raises, which resets both keys jointly — so
// `rotate_keep: null` disables a 0.001 MB threshold entirely. A config
// reader that folds absent and null together sees the 1000-line default
// instead, keeps the caller's 0.001, and rotates a log Python left alone:
// the two runtimes then disagree about which rows are still live on a
// SHARED file. Measured at (5.0, 1000) vs (5.0, 50) before config.Lookup
// existed (adversarial r7 LOW).
//
// The fixture must hold MORE rows than the 1000-line default retention,
// and that is not incidental. At 60 rows the folded reader also produces
// zero archives — not because it reset, but because keep=1000 exceeds the
// row count and the tail guard returns early. The first version of this
// test used 60 rows, passed, and passed just as green against the broken
// reader: right answer, wrong reason. Above the retention the two paths
// separate — Python archives nothing at its 5 MB threshold, the folded
// reader archives the overflow at 0.001 MB.
//
// The control arm is the other half: "0 archives" is only evidence if the
// same fixture with a coercible retention DOES rotate.
func TestAnExplicitNullRetentionIsNotAnAbsentOne(t *testing.T) {
	const rows = 1100 // > defaultRotateKeep, see above
	for _, tc := range []struct {
		name         string
		cfg          string
		wantArchives int
	}{
		{"explicit null resets jointly",
			"captains_log:\n  rotate_mb: 0.001\n  rotate_keep: null\n", 0},
		{"control: same fixture, coercible retention",
			"captains_log:\n  rotate_mb: 0.001\n  rotate_keep: 10\n", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			py := pythonRotateRawCfg(t, t.TempDir(), tc.cfg, rows)
			if got := int(py["n_archives"].(float64)); got != tc.wantArchives {
				t.Fatalf("CPython produced %d archives, want %d — the "+
					"premise of this case is wrong, not the port", got,
					tc.wantArchives)
			}

			_, mem, path := rotWorkspaceRawCfg(t, tc.cfg)
			seedLog(t, path, rows)
			New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

			if got := len(ArchivePaths(mem)); got != tc.wantArchives {
				t.Fatalf("got %d archives, want %d (CPython's count) — the "+
					"two runtimes disagree about which rows are still live",
					got, tc.wantArchives)
			}
			if got, want := withoutAuditRow(readFileString(t, path)),
				withoutAuditRow(py["active"].(string)); got != want {
				t.Errorf("active file differs from CPython's\n got %q\nwant %q",
					truncate(got), truncate(want))
			}
		})
	}
}

// The reset is JOINT. Python coerces both keys inside ONE try/except, so a
// rotate_keep it cannot int() sends rotate_mb back to 5 MB as well — and a
// 15 KB log then does not rotate at all. Reading the keys independently
// gets the threshold "right" and the behaviour wrong.
func TestAnUncoercibleRetentionResetsTheThresholdToo(t *testing.T) {
	const cfg = "captains_log:\n  rotate_mb: 0.001\n  rotate_keep: \"not a number\"\n"
	const rows = 60

	py := pythonRotateRawCfg(t, t.TempDir(), cfg, rows)
	if py["n_archives"].(float64) != 0 {
		t.Fatalf("CPython rotated despite the uncoercible retention (%v "+
			"archives); the premise of this test is wrong", py["n_archives"])
	}

	_, mem, path := rotWorkspaceRawCfg(t, cfg)
	seedLog(t, path, rows)
	before := readFileString(t, path)
	New(filepath.Dir(mem)).maybeRotateCaptainsLog(path)

	if n := len(ArchivePaths(mem)); n != 0 {
		t.Errorf("rotated into %d archives; CPython's joint reset put the "+
			"threshold back to 5 MB and left this 15 KB file alone", n)
	}
	if got := readFileString(t, path); got != before {
		t.Errorf("the active file was rewritten (%d bytes -> %d)",
			len(before), len(got))
	}
}

// Python's log_event fills loop_id from the _current_loop_id contextvar
// whenever the caller passes none, which is how rotation — five frames below
// any code that knows the loop id — still lands attributed. Every Go call
// site that passed "" was writing an unattributed row (adversarial r6).
func TestTheAmbientLoopIDReachesEventsThatPassNone(t *testing.T) {
	_, mem, path := rotWorkspace(t, 0.001, 10)
	seedLog(t, path, 60)

	r := New(filepath.Dir(mem)).WithLoopID("loop-abc123")
	r.maybeRotateCaptainsLog(path)

	row := findRotatedRow(t, readFileString(t, path))
	if got, _ := row["loop_id"].(string); got != "loop-abc123" {
		t.Errorf("LOG_ROTATED loop_id = %q, want %q — the ambient id did not "+
			"reach a call site that passes none", got, "loop-abc123")
	}

	// The delegation chain too: Event -> EventRelated -> EventNoted. Every
	// production emitter that passes "" (skills.MaybeAutoPromoteSkills,
	// LogCircuitTransition, evolver's EVOLVER_APPLIED, graduation's
	// GRADUATION_PROPOSED) goes through one of the two outer verbs, not
	// EventNoted directly, so a fallback that only worked on the inner one
	// would leave every real caller unattributed while the rotation pin
	// above stayed green (adversarial r7).
	for _, tc := range []struct {
		name    string
		subject string
		emit    func(subject string) error
	}{
		{"Event", "chain-event", func(sub string) error {
			return r.Event("SEED", sub, "x", nil, "")
		}},
		{"EventRelated", "chain-related", func(sub string) error {
			return r.EventRelated("SEED", sub, "x", nil, "", nil)
		}},
	} {
		if err := tc.emit(tc.subject); err != nil {
			t.Fatal(err)
		}
		var seen, attributed int
		for _, l := range strings.Split(readFileString(t, path), "\n") {
			var m map[string]any
			if json.Unmarshal([]byte(l), &m) != nil || m["subject"] != tc.subject {
				continue
			}
			seen++
			if m["loop_id"] == "loop-abc123" {
				attributed++
			}
		}
		if seen == 0 {
			t.Fatalf("%s wrote no row for %q; this case proved nothing",
				tc.name, tc.subject)
		}
		if attributed != seen {
			t.Errorf("%s: %d of %d rows carry the ambient loop id",
				tc.name, attributed, seen)
		}
	}

	// An explicit argument still wins, exactly as the kwarg does there...
	if err := r.Event("SEED", "s", "explicit wins", nil, "loop-explicit"); err != nil {
		t.Fatal(err)
	}
	// ...and WithLoopID COPIES, so the original is untouched and two
	// concurrent runs cannot see each other's id.
	base := New(filepath.Dir(mem))
	if base.WithLoopID("x").LoopID == base.LoopID {
		t.Error("WithLoopID mutated the receiver; concurrent runs would " +
			"cross-attribute")
	}

	var explicit, ambient bool
	for _, l := range strings.Split(readFileString(t, path), "\n") {
		var m map[string]any
		if json.Unmarshal([]byte(l), &m) != nil || m["subject"] != "s" {
			continue
		}
		switch m["loop_id"] {
		case "loop-explicit":
			explicit = true
		case "loop-abc123":
			ambient = true
		}
	}
	if !explicit {
		t.Error("an explicit loop_id was overridden by the ambient one")
	}
	if ambient {
		t.Error("the ambient id leaked onto a row that named its own")
	}
}

// coerceInt is int(), not float()-then-truncate: "10.5" is a ValueError
// there and would be a silent 10 under the easy reading. Derived from
// CPython so a disagreement in either direction fails.
func TestCoerceIntMatchesPythonsInt(t *testing.T) {
	inputs := []string{
		"10", " 10 ", "-3", "+7", "0", "1_000", "010",
		"10.5", "1e3", "abc", "", "0x10", "1__0", "_10", "10_",
		"١٠", "٠", "٠١",
	}
	payload, err := json.Marshal(inputs)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command("python3", "-c",
		"import json,sys\n"+
			"out=[]\n"+
			"for s in json.load(sys.stdin):\n"+
			"    try: out.append(int(s))\n"+
			"    except Exception: out.append(None)\n"+
			"print(json.dumps(out))")
	cmd.Stdin = strings.NewReader(string(payload))
	out, err := cmd.Output()
	if err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	var want []*int
	if err := json.Unmarshal(out, &want); err != nil {
		t.Fatalf("decoding CPython output: %v\n%s", err, out)
	}
	var accepted, refused int
	for i, s := range inputs {
		got, ok := coerceInt(s)
		if want[i] == nil {
			refused++
			if ok {
				t.Errorf("coerceInt(%q) = %d, CPython's int() raises", s, got)
			}
			continue
		}
		accepted++
		if !ok {
			t.Errorf("coerceInt(%q) refused; CPython's int() gives %d", s, *want[i])
		} else if got != *want[i] {
			t.Errorf("coerceInt(%q) = %d, CPython's int() gives %d", s, got, *want[i])
		}
	}
	if accepted == 0 || refused == 0 {
		t.Fatalf("the table is one-sided (%d accepted, %d refused); it cannot "+
			"catch a coercion that is too strict OR too lenient", accepted, refused)
	}
	// The non-string shapes YAML actually produces.
	for _, c := range []struct {
		in   any
		want int
		ok   bool
	}{
		{5, 5, true}, {5.0, 5, true}, {5.9, 5, true}, {-5.9, -5, true},
		{true, 1, true}, {false, 0, true}, {math.NaN(), 0, false},
		{math.Inf(1), 0, false}, {[]any{1}, 0, false}, {nil, 0, false},
	} {
		got, ok := coerceInt(c.in)
		if ok != c.ok || (ok && got != c.want) {
			t.Errorf("coerceInt(%#v) = (%d, %v), want (%d, %v)",
				c.in, got, ok, c.want, c.ok)
		}
	}
}

// The config read happens on EVERY captain's-log append, BEFORE the size
// gate, so a malformed config that warned raw would put a line on stderr
// forever — and a warning that fires on every event is one a reader learns
// to skip. Dropping the warnings entirely was the previous behaviour and is
// the worse failure: a config this runtime could not parse then moved the
// rotation threshold silently. So the pin has to see BOTH ends — at least
// one warning, and not one per event.
func TestAMalformedConfigWarnsOnceNotOnEveryAppend(t *testing.T) {
	_, mem, path := rotWorkspaceRawCfg(t, "captains_log:\n  rotate_mb: [unclosed\n")

	var lines []string
	oldWarn := warn
	warn = func(format string, args ...any) {
		lines = append(lines, fmt.Sprintf(format, args...))
	}
	defer func() { warn = oldWarn }()
	warnedConfig = sync.Map{} // the dedupe is process-wide; isolate this test
	defer func() { warnedConfig = sync.Map{} }()

	r := New(filepath.Dir(mem))
	const appends = 5
	for i := 0; i < appends; i++ {
		if err := r.Event("SEED", "s", "padding", nil, ""); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("no log was written, so the config was never read: %v", err)
	}
	if len(lines) == 0 {
		t.Fatal("a config this runtime could not parse produced NO warning; " +
			"the threshold moved silently")
	}
	if len(lines) >= appends {
		t.Errorf("%d warnings over %d appends — the warning fires per event, "+
			"which trains its reader to ignore stderr:\n%s",
			len(lines), appends, strings.Join(lines, "\n"))
	}
	seen := map[string]int{}
	for _, l := range lines {
		seen[l]++
	}
	for l, n := range seen {
		if n > 1 {
			t.Errorf("warning repeated %d times: %q", n, l)
		}
	}
}

// findRotatedRawLine returns the raw LOG_ROTATED line, unparsed, so a test
// can assert on bytes rather than on a decoded map.
func findRotatedRawLine(t *testing.T, content string) string {
	t.Helper()
	for _, l := range strings.Split(content, "\n") {
		if strings.Contains(l, `"LOG_ROTATED"`) {
			return l
		}
	}
	t.Fatal("no LOG_ROTATED row in the active file")
	return ""
}

// normalizeRotatedLine blanks the two values that cannot match across two
// independent rotations — the ISO timestamp and the archive filename,
// which carries a wall-clock stamp of its own — and leaves every other
// byte, separators included, exactly as written.
//
// Deliberately a STRING rewrite rather than a decode-and-re-encode: a
// round trip through any encoder would impose that encoder's spelling on
// both sides and re-create the blindness this replaced (mission-r8).
func normalizeRotatedLine(t *testing.T, line string) string {
	t.Helper()
	row, err := LoadsClean(line)
	if err != nil {
		t.Fatalf("rotated line does not parse: %v\n%s", err, line)
	}
	out := line
	if ts, _ := row["timestamp"].(string); ts != "" {
		out = strings.ReplaceAll(out, ts, "<TS>")
	}
	ctx, _ := row["context"].(map[string]any)
	name, _ := ctx["archive"].(string)
	if name == "" {
		t.Fatalf("the audit row carries no archive name:\n%s", line)
	}
	return strings.ReplaceAll(out, name, "<ARCHIVE>")
}

// splitAtContext returns the line up to and including `"context": ` and the
// remainder, so a comparison can be byte-exact on everything a Go map has
// not already reordered.
func splitAtContext(t *testing.T, line string) (head, ctx string) {
	t.Helper()
	const marker = `"context": `
	i := strings.Index(line, marker)
	if i < 0 {
		t.Fatalf("the audit row carries no context:\n%s", line)
	}
	return line[:i+len(marker)], line[i+len(marker):]
}
