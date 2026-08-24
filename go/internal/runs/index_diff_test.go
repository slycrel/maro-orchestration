package runs

import (
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// The run-ref index is the first thing this port has written whose whole
// job is to be READ BY THE OTHER RUNTIME. Everything before it wrote a
// store Python could read; this decides whether Python can FIND a run at
// all — and the failure is invisible from inside either runtime, because
// each one finds its own runs perfectly well.
//
// So the tests here are deliberately weighted toward crossing the seam:
// Go writes and CPython resolves, CPython writes and Go resolves. The
// same-runtime tests exist to localize a failure once a crossing test has
// caught one, not to stand in for it.

func srcDir(t *testing.T) string {
	t.Helper()
	p, err := filepath.Abs(filepath.Join("..", "..", "..", "src"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(p, "runs.py")); err != nil {
		t.Skipf("python source tree unavailable: %v", err)
	}
	return p
}

// runPyIn runs a probe with MARO_WORKSPACE pointed at ws.
//
// The guard is not decoration. MARO_WORKSPACE is the store override, and
// a probe that writes with it unset or wrong lands in
// ~/.maro/workspace/ — the live ledgers this box actually runs on. That
// has happened here before (2026-08-16, a live ledger overwritten by a
// test probe), and the rule that came out of it is to assert the RESOLVED
// path before any write, on the Python side, where the resolution
// actually happens.
func runPyIn(t *testing.T, ws, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	guard := `
import os, sys
_ws = os.environ.get("MARO_WORKSPACE", "")
_home = os.path.expanduser("~/.maro")
if not _ws or os.path.commonpath([os.path.realpath(_ws), os.path.realpath(_home)]) == os.path.realpath(_home):
    raise SystemExit("refusing to run: MARO_WORKSPACE is %r, which is inside the live workspace" % _ws)
import runs
if not runs.runs_root().is_relative_to(_ws):
    raise SystemExit("refusing to run: runs_root() resolved to %s, outside %s" % (runs.runs_root(), _ws))
`
	cmd := exec.Command("python3", append([]string{"-c", guard + src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t), "MARO_WORKSPACE="+ws)
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

// --- nickname -----------------------------------------------------------

const pyNicknameSrc = `
import json, sys
from runs import nickname, run_dir
out = []
for h in json.loads(sys.argv[1]):
    out.append([nickname(h), run_dir(h).name])
sys.stdout.write(json.dumps(out))
`

// The nickname is a LOOKUP KEY, not a label: Python's run_dir builds the
// directory name from it, and that name is the first thing resolve_run_dir
// tries and the thing create_run_dir uses to decide a run dir already
// exists. A nickname that disagrees by one word means a Python
// create_run_dir makes a SECOND directory beside the Go one for the same
// run.
//
// The corpus walks the modulus rather than sampling: handle ids picked so
// the sha1 first two bytes land on 0, on len-1, and on values that
// separate a `% len` from a truncation.
func TestNicknameAndDirNameMatchCPython(t *testing.T) {
	ids := []string{
		"", "a", "abc123", "20260822-deadbeef",
		"handle-0000", "handle-0001", "handle-0002", "handle-0003",
		"20260824T000000-aaaaaaaa", "20260824T000000-bbbbbbbb",
		// Non-ASCII: the digest is over UTF-8 BYTES in both runtimes, and
		// a port that hashed runes or UTF-16 would diverge only here.
		"café-run", "日本語", "smile-😀",
		// A handle id that itself contains a dash, so a reader splitting
		// the dir name on "-" cannot be relied on either way.
		"a-b-c-d-e",
		// Long, to catch any accidental truncation before hashing.
		strings.Repeat("z", 300),
	}
	in, err := json.Marshal(ids)
	if err != nil {
		t.Fatal(err)
	}
	var want [][]string
	if err := json.Unmarshal([]byte(runPy(t, pyNicknameSrc, string(in))), &want); err != nil {
		t.Fatal(err)
	}
	ws := t.TempDir()
	for i, id := range ids {
		if got := Nickname(id); got != want[i][0] {
			t.Errorf("Nickname(%q) = %q, CPython says %q", id, got, want[i][0])
		}
		if got := filepath.Base(Dir(ws, id)); got != want[i][1] {
			t.Errorf("Dir(%q) names %q, CPython's run_dir names %q", id, got, want[i][1])
		}
	}
}

// Anti-vacuity: the corpus has to REACH more than one adjective and more
// than one noun, or a hardcoded "amber-alder" would pass every case
// above. A differential over a corpus that exercises one value is a test
// that reports agreement about nothing (the r6 lens).
func TestTheNicknameCorpusSpreads(t *testing.T) {
	adj, noun := map[string]bool{}, map[string]bool{}
	for i := 0; i < 200; i++ {
		parts := strings.SplitN(Nickname(strings.Repeat("x", i)+"-id"), "-", 2)
		adj[parts[0]] = true
		noun[parts[1]] = true
	}
	if len(adj) < 20 || len(noun) < 20 {
		t.Errorf("200 ids reached only %d adjectives and %d nouns; the hash is "+
			"not spreading and the differential above proves little", len(adj), len(noun))
	}
}

func runPy(t *testing.T, src string, args ...string) string {
	t.Helper()
	if _, err := exec.LookPath("python3"); err != nil {
		t.Skipf("python3 unavailable: %v", err)
	}
	cmd := exec.Command("python3", append([]string{"-c", src}, args...)...)
	cmd.Env = append(cmd.Environ(), "PYTHONPATH="+srcDir(t))
	out, err := cmd.Output()
	if err != nil {
		if ee, ok := err.(*exec.ExitError); ok {
			t.Fatalf("CPython probe failed: %v\n%s", err, ee.Stderr)
		}
		t.Fatalf("CPython probe failed: %v", err)
	}
	return string(out)
}

// --- the traversal guard ------------------------------------------------

const pyBareNameSrc = `
import json, sys
from pathlib import Path
out = []
for name in json.loads(sys.argv[1]):
    out.append(Path(name).name == name)
sys.stdout.write(json.dumps(out))
`

// pathDotNameIsSelf must reproduce Python's `Path(name).name == name`
// EXACTLY — including on the inputs where that predicate is wrong.
//
// Keeping the faithful predicate as its own tested function, with the
// hardening layered on top in isBareName, is what makes the divergence
// legible: this test proves the port knows what Python does, and
// TestTheTraversalGuardIsHarderThanPythons below states where it
// deliberately does something else. Folding them together would leave a
// reader unable to tell an intended divergence from a porting error —
// which is the whole reason the r1 lens exists.
func TestPathDotNameIsSelfMatchesCPython(t *testing.T) {
	names := []string{
		"20260824-abc-swift-heron", "a", "",
		".", "..", "...", "/", "//",
		"a/b", "a/", "/a", "./a", "a/.", "a/..", "a//b",
		"../../etc/passwd", "..\\windows",
		".hidden", "café-run", "日本語",
		" ", "a b", "a\tb",
	}
	in, err := json.Marshal(names)
	if err != nil {
		t.Fatal(err)
	}
	var want []bool
	if err := json.Unmarshal([]byte(runPy(t, pyBareNameSrc, string(in))), &want); err != nil {
		t.Fatal(err)
	}
	for i, n := range names {
		if got := pathDotNameIsSelf(n); got != want[i] {
			t.Errorf("pathDotNameIsSelf(%q) = %v, CPython's Path(%q).name == %q is %v",
				n, got, n, n, want[i])
		}
	}
	// The naive implementation, replayed and required to LOSE. Keeping it
	// here is what stops someone "simplifying" the predicate back into it
	// during a later cleanup, which is exactly how this class of guard
	// regresses.
	naive := func(name string) bool { return filepath.Base(name) == name }
	lost := false
	for i, n := range names {
		if naive(n) != want[i] {
			lost = true
			break
		}
	}
	if !lost {
		t.Error("filepath.Base(name) == name agrees with Python on every case " +
			"in this corpus, so the corpus does not exercise the guard")
	}
}

// The named divergence, pinned from both sides: Python ACCEPTS "" and
// "..", this port refuses them, and the reason is that both resolve to a
// real directory that is not a run.
//
// The second half of this test is the part that matters — it demonstrates
// the consequence rather than asserting the rule. A workspace is built,
// the two names are resolved against it the way _indexed_run_dir would,
// and the resulting paths are shown to be the runs root and the workspace
// root. Without that, "we refuse '..'" is a rule with no stated cost, and
// a later reader has no way to judge whether restoring parity is safe.
func TestTheTraversalGuardIsHarderThanPythons(t *testing.T) {
	for _, name := range []string{"", ".."} {
		if !pathDotNameIsSelf(name) {
			t.Errorf("the faithful predicate should ACCEPT %q — CPython does; "+
				"if this changed, the divergence note is now wrong", name)
		}
		if isBareName(name) {
			t.Errorf("isBareName(%q) accepts a name that resolves outside the "+
				"run it claims to name", name)
		}
	}
	// "." is refused by BOTH, so it is not part of the divergence — it is
	// here to keep the three degenerate names from being read as one rule.
	if pathDotNameIsSelf(".") || isBareName(".") {
		t.Error(`"." should be refused by both the predicate and the guard`)
	}

	// What Python's acceptance actually costs.
	ws := t.TempDir()
	root := RunsRoot(ws)
	if err := os.MkdirAll(root, 0o755); err != nil {
		t.Fatal(err)
	}
	if got := filepath.Clean(filepath.Join(root, "")); got != filepath.Clean(root) {
		t.Fatalf(`root/"" is %q, not the runs root — the divergence note's premise is wrong`, got)
	}
	if got := filepath.Clean(filepath.Join(root, "..")); got != filepath.Clean(ws) {
		t.Fatalf(`root/".." is %q, not the workspace root — the divergence note's premise is wrong`, got)
	}
	if !isDir(filepath.Join(root, "")) || !isDir(filepath.Join(root, "..")) {
		t.Fatal("both resolve to real directories, which is why is_dir() does not " +
			"catch them; if that stopped being true the hardening could be dropped")
	}
}

// --- metadata refs ------------------------------------------------------

const pyRefsSrc = `
import json, sys
from runs import _metadata_refs
out = []
for meta in json.loads(sys.argv[1]):
    out.append(sorted(_metadata_refs(meta)))
sys.stdout.write(json.dumps(out))
`

// The v1→v2 bug, pinned. v1's refs omitted loop_ids and loops[].loop_id,
// so every indexed run was unreachable by loop_id — which is the key the
// outcome ledger and the verdict seam join on, so every loop_id-keyed
// consumer silently no-op'd.
func TestMetadataRefsMatchCPython(t *testing.T) {
	metas := []map[string]any{
		{"handle_id": "h1"},
		{"handle_id": "h1", "loop_id": "l1"},
		// The v2 additions, each on its own so a missing one is named.
		{"handle_id": "h1", "loop_ids": []any{"l1", "l2"}},
		{"handle_id": "h1", "loops": []any{
			map[string]any{"loop_id": "l3"},
			map[string]any{"loop_id": "l4"},
		}},
		{"handle_id": "h1", "origin": map[string]any{"resumed_from": "l5"}},
		// Everything at once, with duplicates across fields — the result
		// is a SET, so a port that returned a list would show its
		// duplicates here.
		{
			"handle_id": "h1", "loop_id": "l1",
			"loop_ids": []any{"l1", "l2", "l2"},
			"loops": []any{
				map[string]any{"loop_id": "l2"},
				map[string]any{"loop_id": "l6"},
			},
			"origin": map[string]any{"resumed_from": "h1"},
		},
		// Empty and null members are DROPPED, not indexed as "".
		{"handle_id": "", "loop_id": nil, "loop_ids": []any{"", nil, "l7"}},
		// Wrong TYPES on the container fields: Python's isinstance guards
		// skip them rather than raising, and a run whose metadata has a
		// string where a list belongs must still index its handle id.
		{"handle_id": "h2", "loop_ids": "not-a-list", "loops": "not-a-list",
			"origin": "not-a-dict"},
		// A loops entry that is not a dict, beside one that is.
		{"handle_id": "h3", "loops": []any{"nope", map[string]any{"loop_id": "l8"}}},
		// A loops entry that is a dict with no loop_id.
		{"handle_id": "h4", "loops": []any{map[string]any{"status": "done"}}},
		{},
	}
	in, err := json.Marshal(metas)
	if err != nil {
		t.Fatal(err)
	}
	var want [][]string
	if err := json.Unmarshal([]byte(runPy(t, pyRefsSrc, string(in))), &want); err != nil {
		t.Fatal(err)
	}
	for i, meta := range metas {
		got := make([]string, 0, 4)
		for r := range metadataRefs(meta) {
			got = append(got, r)
		}
		sort.Strings(got)
		w := want[i]
		if w == nil {
			w = []string{}
		}
		if strings.Join(got, "\x00") != strings.Join(w, "\x00") {
			t.Errorf("metadataRefs(%v) = %v, CPython says %v", meta, got, w)
		}
	}
}

// --- crossing the seam --------------------------------------------------

const pyResolveSrc = `
import json, sys
from runs import resolve_run_dir
out = []
for ref in json.loads(sys.argv[1]):
    rd = resolve_run_dir(ref)
    out.append(rd.name if rd is not None else None)
sys.stdout.write(json.dumps(out))
`

// THE interop test: a run this port created and indexed, found by CPython
// under every reference it should be findable under.
//
// This is the assertion the bare-id run dir would have failed on the
// handle id and — before the index was ported at all — on every other
// ref, since nothing on this side wrote an index and Python's own
// migration only indexes runs it can already parse.
func TestCPythonResolvesARunThisPortWrote(t *testing.T) {
	ws := t.TempDir()
	handleID := "20260824T101112-abcdef01"
	rd, err := Create(ws, handleID, "port the escalation lane")
	if err != nil {
		t.Fatal(err)
	}
	// A realistic run: several loops, a resume ancestry, the plural
	// ledger — i.e. every ref class metadataRefs knows about.
	if err := WriteMetadata(rd, pyval.Obj{
		{Key: "loop_id", Val: "loop-aaa"},
		{Key: "loop_ids", Val: pyval.List{"loop-aaa", "loop-bbb"}},
		{Key: "loops", Val: pyval.List{
			pyval.Obj{{Key: "loop_id", Val: "loop-bbb"}},
			pyval.Obj{{Key: "loop_id", Val: "loop-ccc"}},
		}},
		{Key: "origin", Val: pyval.Obj{{Key: "resumed_from", Val: "loop-old"}}},
	}); err != nil {
		t.Fatal(err)
	}
	// Note there is no explicit IndexRunDir call: WriteMetadata publishes,
	// the way every Python writer of this file does. A test that indexed by
	// hand here would pass on a port whose ordinary writes never index —
	// which is the shape this port actually had.

	refs := []string{handleID, "loop-aaa", "loop-bbb", "loop-ccc", "loop-old", "loop-missing"}
	in, err := json.Marshal(refs)
	if err != nil {
		t.Fatal(err)
	}
	var got []*string
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyResolveSrc, string(in))), &got); err != nil {
		t.Fatal(err)
	}
	wantName := filepath.Base(rd)
	for i, ref := range refs {
		if ref == "loop-missing" {
			if got[i] != nil {
				t.Errorf("CPython resolved the absent ref %q to %q", ref, *got[i])
			}
			continue
		}
		if got[i] == nil {
			t.Errorf("CPython could not find the run by %q — this run is "+
				"invisible to the Python runtime", ref)
			continue
		}
		if *got[i] != wantName {
			t.Errorf("CPython resolved %q to %q, want %q", ref, *got[i], wantName)
		}
	}
}

const pyCreateSrc = `
import json, sys
from runs import create_run_dir, index_run_dir, run_dir
import json as _json
handle_id, loop_id = json.loads(sys.argv[1])
rd = create_run_dir(handle_id, prompt="written by CPython")
meta = _json.loads((rd / "metadata.json").read_text(encoding="utf-8"))
meta["loop_id"] = loop_id
(rd / "metadata.json").write_text(_json.dumps(meta, indent=2), encoding="utf-8")
index_run_dir(rd)
sys.stdout.write(_json.dumps(rd.name))
`

// The other direction, and the one that catches the duplicate-directory
// bug directly: CPython creates the run, then this port must find it —
// by handle id (the direct name path, which is what the nickname is for)
// and by loop id (the index path).
func TestThisPortResolvesARunCPythonWrote(t *testing.T) {
	ws := t.TempDir()
	handleID, loopID := "20260824T131415-fedcba98", "loop-from-python"
	in, err := json.Marshal([]string{handleID, loopID})
	if err != nil {
		t.Fatal(err)
	}
	var pyName string
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyCreateSrc, string(in))), &pyName); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(ws, "runs", pyName)

	// By handle id: this must hit the DIRECT name path, with no index
	// involved. Before Nickname was ported, Dir built a different name and
	// this fell through to a scan — and worse, a Go Create for the same
	// handle id would have made a second directory beside CPython's.
	if got := Dir(ws, handleID); got != want {
		t.Errorf("Dir names %q; CPython created %q — the two runtimes would "+
			"make two directories for one run", filepath.Base(got), pyName)
	}
	if got := ResolveRunDir(ws, handleID); got != want {
		t.Errorf("ResolveRunDir(%q) = %q, want %q", handleID, got, want)
	}
	// By loop id: through the index CPython published.
	if got := ResolveRunDir(ws, loopID); got != want {
		t.Errorf("ResolveRunDir(%q) = %q, want %q — the index CPython wrote "+
			"is not readable here", loopID, got, want)
	}
	if got := ResolveRunDir(ws, "no-such-ref"); got != "" {
		t.Errorf("ResolveRunDir found %q for an absent ref", got)
	}
}

// A workspace with runs but NO index is the shape every existing
// installation has on the day this ships. The first miss has to migrate
// it, and the migration has to be readable by the other runtime — a
// second migration marker with a different meaning would leave one
// runtime believing the index was complete when it was not.
func TestAnUnindexedWorkspaceMigratesAndCPythonAgrees(t *testing.T) {
	ws := t.TempDir()
	handleID := "20260824T161718-11223344"
	rd, err := Create(ws, handleID, "an unindexed run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "loop-unindexed"}}); err != nil {
		t.Fatal(err)
	}
	// The legacy shape: runs on disk, no index at all. Every writer here
	// publishes refs, so it has to be taken away rather than merely not
	// created — which is also exactly how a real installation arrives at
	// this state, having been written entirely by a runtime that predates
	// the index.
	if err := os.RemoveAll(filepath.Join(ws, indexDirName)); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(ws, indexDirName)); err == nil {
		t.Fatal("the index directory survived being removed")
	}

	if got := ResolveRunDir(ws, "loop-unindexed"); got != rd {
		t.Fatalf("ResolveRunDir did not migrate-and-find: got %q want %q", got, rd)
	}
	marker := filepath.Join(ws, indexDirName, indexMarker)
	if !isFile(marker) {
		t.Fatal("the migration left no marker, so every future miss pays for a full scan")
	}
	if !migrationComplete(marker) {
		raw, _ := os.ReadFile(marker)
		t.Fatalf("the migration reported incomplete: %s", raw)
	}
	// And CPython reads the same index without re-migrating.
	in, _ := json.Marshal([]string{"loop-unindexed"})
	var got []*string
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyResolveSrc, string(in))), &got); err != nil {
		t.Fatal(err)
	}
	if got[0] == nil || *got[0] != filepath.Base(rd) {
		t.Errorf("CPython did not resolve through the index this port migrated: %v", got[0])
	}
}

// --- the index's self-healing, which nothing above exercises ------------

// Two live run dirs claiming the same reference. The historical fallback
// was a sorted directory scan, so the alphabetically-FIRST one won; the
// index has to answer the same question the same way, or an optimization
// has changed a result.
func TestADuplicateRefKeepsTheAlphabeticallyFirstRun(t *testing.T) {
	ws := t.TempDir()
	root := RunsRoot(ws)
	// Named so the directory order is unambiguous regardless of nickname.
	first := filepath.Join(root, "aaa-run")
	second := filepath.Join(root, "zzz-run")
	for _, d := range []string{first, second} {
		if err := os.MkdirAll(d, 0o755); err != nil {
			t.Fatal(err)
		}
		if err := WriteMetadata(d, pyval.Obj{{Key: "loop_id", Val: "shared-loop"}}); err != nil {
			t.Fatal(err)
		}
	}
	// Publish the LATER one first, so keeping the first alphabetically
	// requires the read-back branch to actually fire.
	if err := writeIndexEntry("shared-loop", second); err != nil {
		t.Fatal(err)
	}
	if err := writeIndexEntry("shared-loop", first); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "shared-loop"); got != first {
		t.Errorf("resolved to %q, want the alphabetically first %q", got, first)
	}
	// And re-publishing the later one does not steal it back.
	if err := writeIndexEntry("shared-loop", second); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "shared-loop"); got != first {
		t.Errorf("a later publish took the ref: got %q, want %q", got, first)
	}
}

// An entry whose stored ref does not match the one being looked up is a
// hash collision or a corrupted file, and must NOT be trusted just
// because it parsed. The ref is stored in the entry precisely so this can
// be checked.
func TestACollidingIndexEntryIsRepairedNotTrusted(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "20260824T000000-collide", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "real-loop"}}); err != nil {
		t.Fatal(err)
	}
	// Forge the entry real-loop hashes to, but store a DIFFERENT ref and
	// a different (also real) directory inside it.
	other := filepath.Join(RunsRoot(ws), "decoy-run")
	if err := os.MkdirAll(other, 0o755); err != nil {
		t.Fatal(err)
	}
	entry := indexEntryPath("real-loop", RunsRoot(ws))
	if err := os.MkdirAll(filepath.Dir(entry), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry,
		[]byte(`{"ref": "some-other-ref", "run_dir": "decoy-run"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "real-loop"); got != rd {
		t.Errorf("resolved to %q; a mismatched ref was trusted instead of "+
			"repaired (want %q)", got, rd)
	}
	// The repair republished a CORRECT entry, so the next lookup is O(1)
	// again rather than paying for the scan forever.
	if got := readIndexEntry(entry, "real-loop", RunsRoot(ws)); got != rd {
		t.Errorf("the entry was not republished: readIndexEntry gives %q", got)
	}
}

// An entry naming a directory that has since been pruned is stale, not
// authoritative. Returning it hands the caller a path that does not
// exist, and a stamp against it would CREATE the directory — resurrecting
// a deleted run as an empty shell.
func TestAnEntryNamingAVanishedRunSelfHeals(t *testing.T) {
	ws := t.TempDir()
	live, err := Create(ws, "20260824T000000-live", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(live, pyval.Obj{{Key: "loop_id", Val: "wandering-loop"}}); err != nil {
		t.Fatal(err)
	}
	IndexRunDir(live, nil)
	// Point the entry at a directory that never existed.
	entry := indexEntryPath("wandering-loop", RunsRoot(ws))
	if err := os.WriteFile(entry,
		[]byte(`{"ref": "wandering-loop", "run_dir": "pruned-run"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	// A marker saying the migration already finished. Without it the global
	// rebuild stands in for the local repair and this test passes whether
	// or not indexedRunDir heals anything — the whole point of the repair
	// is that it works on a workspace that has nothing left to migrate.
	if err := os.WriteFile(filepath.Join(indexDir(RunsRoot(ws)), indexMarker),
		[]byte(`{"version": 1, "complete": true, "failed_entries": 0}`), 0o644); err != nil {
		t.Fatal(err)
	}
	got := ResolveRunDir(ws, "wandering-loop")
	if got != live {
		t.Errorf("resolved to %q, want the live run %q", got, live)
	}
	if isDir(filepath.Join(RunsRoot(ws), "pruned-run")) {
		t.Error("the lookup CREATED the vanished directory")
	}
	// And the leaf was rewritten, so the next lookup is O(1) again.
	if got := readIndexEntry(entry, "wandering-loop", RunsRoot(ws)); got != live {
		raw, _ := os.ReadFile(entry)
		t.Errorf("the stale entry was not republished (%s); every future "+
			"lookup of this ref pays for a full scan", raw)
	}
}

// A migration marker written before the completeness flag existed did
// complete — absence of the key means complete, while an unreadable
// marker means NOT complete. Two different questions with two different
// defaults, which is easy to collapse into one.
func TestAMarkerWithoutTheFlagCountsAsComplete(t *testing.T) {
	dir := t.TempDir()
	marker := filepath.Join(dir, ".migrated")
	if err := os.WriteFile(marker, []byte(`{"version": 1}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if !migrationComplete(marker) {
		t.Error("an old marker with no flag read as incomplete, so every miss " +
			"pays for a full rescan forever")
	}
	if err := os.WriteFile(marker, []byte(`{"version": 1, "complete": false}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if migrationComplete(marker) {
		t.Error("an explicitly incomplete marker read as complete")
	}
	if err := os.WriteFile(marker, []byte(`not json`), 0o644); err != nil {
		t.Fatal(err)
	}
	if migrationComplete(marker) {
		t.Error("an unreadable marker read as complete; a miss would then be " +
			"reported as a real miss without ever scanning")
	}
	if migrationComplete(filepath.Join(dir, "absent")) {
		t.Error("a missing marker read as complete")
	}
}

// A migration that could not index everything must NOT turn a miss into a
// reported absence: historical reachability is preserved by falling
// through to the scan. This is the difference between "we could not index
// this run" and "this run does not exist".
func TestAnIncompleteMigrationStillFindsARunByScan(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "20260824T000000-scanme", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "scan-only-loop"}}); err != nil {
		t.Fatal(err)
	}
	// An index directory holding a marker that says the migration did NOT
	// finish, and no entries at all — the shape a partly-failed migration
	// leaves behind. Whatever the seed published has to go first, or the
	// entry answers and the fallback under test never runs.
	idx := indexDir(RunsRoot(ws))
	if err := os.RemoveAll(idx); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(idx, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(idx, indexMarker),
		[]byte(`{"version": 1, "complete": false, "failed_entries": 3}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "scan-only-loop"); got != rd {
		t.Errorf("resolved to %q; an incomplete migration made a live run "+
			"unreachable (want %q)", got, rd)
	}
	// And a ref that genuinely is not there still comes back empty rather
	// than resolving to something.
	if got := ResolveRunDir(ws, "genuinely-absent"); got != "" {
		t.Errorf("an absent ref resolved to %q", got)
	}
}

// A handle id resolves from the directory NAME, with no index involved at
// all. That is the path the nickname exists for, and it has to keep
// working in a workspace that has never been indexed — otherwise the
// first lookup on every fresh workspace pays for a migration it does not
// need.
func TestResolvingByHandleIDNeedsNoIndex(t *testing.T) {
	ws := t.TempDir()
	handleID := "20260824T000000-direct01"
	rd, err := Create(ws, handleID, "a run")
	if err != nil {
		t.Fatal(err)
	}
	// Take away everything Create published, so the index cannot answer
	// this at all. With it in place the handle_id entry resolves the ref
	// and the direct-name path could be missing entirely without anyone
	// noticing.
	if err := os.RemoveAll(indexDir(RunsRoot(ws))); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, handleID); got != rd {
		t.Fatalf("ResolveRunDir(%q) = %q, want %q", handleID, got, rd)
	}
	if isDir(indexDir(RunsRoot(ws))) {
		t.Error("resolving a handle id built the index; the direct name hit " +
			"should have answered before anything touched it")
	}
}

// --- the index paths themselves -----------------------------------------

const pyIndexPathsSrc = `
import json, sys
import runs
refs = json.loads(sys.argv[1])
sys.stdout.write(json.dumps({
    "dir": str(runs._index_dir()),
    "marker": runs._RUN_INDEX_MARKER,
    "entries": [str(runs._index_entry_path(r)) for r in refs],
}))
`

// Where the index LIVES and what each leaf is CALLED are the two facts
// both runtimes have to agree on before any of the behaviour above
// matters: a port that put the directory inside runs/, or salted the
// hash, would pass every same-runtime test in this file and share nothing
// with CPython. Both runtimes would simply migrate their own copy and
// answer correctly — from a cache the other never reads.
//
// Nothing else here can catch that, because every crossing test in this
// file tolerates a migration.
func TestTheIndexPathsMatchCPython(t *testing.T) {
	ws := t.TempDir()
	refs := []string{
		"20260824T101112-abcdef01", "loop-aaa", "",
		// Non-ASCII and a path separator: the digest is over UTF-8 bytes,
		// and a ref that contains a slash must still name ONE leaf.
		"réf-ünïcode", "a/b/c", strings.Repeat("x", 300),
	}
	in, err := json.Marshal(refs)
	if err != nil {
		t.Fatal(err)
	}
	var want struct {
		Dir     string   `json:"dir"`
		Marker  string   `json:"marker"`
		Entries []string `json:"entries"`
	}
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyIndexPathsSrc, string(in))), &want); err != nil {
		t.Fatal(err)
	}
	root := RunsRoot(ws)
	if got := indexDir(root); got != want.Dir {
		t.Errorf("indexDir = %q, CPython says %q — the two runtimes keep "+
			"separate indexes and neither can tell", got, want.Dir)
	}
	if indexMarker != want.Marker {
		t.Errorf("indexMarker = %q, CPython says %q", indexMarker, want.Marker)
	}
	for i, ref := range refs {
		if got := indexEntryPath(ref, root); got != want.Entries[i] {
			t.Errorf("indexEntryPath(%q) = %q, CPython says %q",
				ref, got, want.Entries[i])
		}
	}
}

// --- what the self-healing tests above could not see --------------------

// An entry naming a run dir that has been pruned must not survive on the
// alphabetical rule. The duplicate-ref preference only makes sense
// between two LIVE directories: applied to a dead one it pins the ref to
// a name nothing can resolve, permanently, since every republish reads
// the same stale entry and keeps it.
func TestAStaleEntryDoesNotBlockARepublish(t *testing.T) {
	ws := t.TempDir()
	root := RunsRoot(ws)
	live := filepath.Join(root, "zzz-live")
	if err := os.MkdirAll(live, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(live, pyval.Obj{{Key: "loop_id", Val: "moved-loop"}}); err != nil {
		t.Fatal(err)
	}
	// Sorts BEFORE the live dir, so the alphabetical branch prefers it —
	// and it does not exist.
	entry := indexEntryPath("moved-loop", root)
	if err := os.MkdirAll(filepath.Dir(entry), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry,
		[]byte(`{"ref": "moved-loop", "run_dir": "aaa-pruned"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := writeIndexEntry("moved-loop", live); err != nil {
		t.Fatal(err)
	}
	if got := readIndexEntry(entry, "moved-loop", root); got != live {
		raw, _ := os.ReadFile(entry)
		t.Errorf("the republish was refused in favour of a dead directory: "+
			"entry is %s, want it naming %q", raw, filepath.Base(live))
	}
}

// The legacy scan skips dot-prefixed directories. That is defence in
// depth — the index dir is a sibling of runs/ — but a stray cache or an
// editor scratch dir inside runs/ sorts FIRST, and the scan returns the
// first match.
func TestTheLegacyScanIgnoresDotDirectories(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "20260824T192021-realrun", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "contested-loop"}}); err != nil {
		t.Fatal(err)
	}
	// Written RAW, not through WriteMetadata: a stray directory is stray
	// precisely because no writer ever published it. (Going through the
	// writer would index it, and both runtimes would then honour the entry
	// — correctly, since an entry is a claim someone made on purpose.)
	decoy := filepath.Join(RunsRoot(ws), ".decoy-run")
	if err := os.MkdirAll(decoy, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(decoy, "metadata.json"),
		[]byte(`{"loop_id": "contested-loop"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	// Clear the index so BOTH runtimes have to answer from the scan; with
	// it in place this tests the entry Create published, not the scan.
	if err := os.RemoveAll(indexDir(RunsRoot(ws))); err != nil {
		t.Fatal(err)
	}
	if got := legacyRunDir("contested-loop", RunsRoot(ws)); got != rd {
		t.Errorf("the scan returned %q, want %q — a dot directory claimed a "+
			"live run's reference", got, rd)
	}
	// And CPython's scan agrees, which is the half that matters: the two
	// runtimes must not disagree about which directory owns a ref.
	in, _ := json.Marshal([]string{"contested-loop"})
	var got []*string
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyResolveSrc, string(in))), &got); err != nil {
		t.Fatal(err)
	}
	if got[0] == nil || *got[0] != filepath.Base(rd) {
		name := "<nil>"
		if got[0] != nil {
			name = *got[0]
		}
		t.Errorf("CPython resolved the contested ref to %s, want %q",
			name, filepath.Base(rd))
	}
}

// A migration that genuinely could not publish an entry has to say so.
// Reporting complete is worse than reporting nothing: a complete marker
// means a later miss is a REAL miss, so the run whose entry failed
// becomes unreachable rather than merely slow.
//
// The failure is produced, not simulated — a directory parked where the
// entry file belongs, which is what an interrupted migration or a
// hostile umask leaves behind.
func TestARealPartialMigrationStillFindsTheRunItMissed(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "20260824T222324-partial1", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "unwritable-loop"}}); err != nil {
		t.Fatal(err)
	}
	root := RunsRoot(ws)
	// Drop whatever Create/WriteMetadata published, so the migration is the
	// thing under test, then block one ref's leaf.
	if err := os.RemoveAll(indexDir(root)); err != nil {
		t.Fatal(err)
	}
	blocked := indexEntryPath("unwritable-loop", root)
	if err := os.MkdirAll(blocked, 0o755); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "unwritable-loop"); got != rd {
		t.Errorf("resolved to %q; a run the migration could not index became "+
			"unreachable (want %q)", got, rd)
	}
	marker := filepath.Join(indexDir(root), indexMarker)
	if !isFile(marker) {
		t.Fatal("the migration left no marker at all")
	}
	if migrationComplete(marker) {
		raw, _ := os.ReadFile(marker)
		t.Errorf("a migration that could not write an entry reported itself "+
			"complete: %s", raw)
	}
}

// An ordinary metadata write publishes the run's references. Python's
// `write_metadata` and `_stamp_metadata_at` both call index_run_dir from
// inside the merge, so a loop id that appears in metadata.json is
// findable the moment it lands. This port did not, and the gap is
// invisible from every angle except this one: a Python lookup MIGRATES on
// a miss, so it finds the run anyway — once, slowly, and only if nothing
// had already marked the migration complete.
//
// Which is what the complete marker below is for. With it in place there
// is nothing left to migrate, so the ref is reachable if and only if the
// write published it.
func TestAMetadataWritePublishesTheRefsItself(t *testing.T) {
	ws := t.TempDir()
	rd, err := Create(ws, "20260824T253000-publish1", "a run")
	if err != nil {
		t.Fatal(err)
	}
	if err := WriteMetadata(rd, pyval.Obj{{Key: "loop_id", Val: "published-loop"}}); err != nil {
		t.Fatal(err)
	}
	idx := indexDir(RunsRoot(ws))
	if err := os.MkdirAll(idx, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(idx, indexMarker),
		[]byte(`{"version": 1, "complete": true, "failed_entries": 0}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := ResolveRunDir(ws, "published-loop"); got != rd {
		t.Errorf("ResolveRunDir(%q) = %q, want %q — the metadata write did not "+
			"publish the ref, so this loop id is reachable only by a migration "+
			"that has already been marked done", "published-loop", got, rd)
	}
	// And CPython, which is the runtime that actually reads this index.
	in, _ := json.Marshal([]string{"published-loop"})
	var got []*string
	if err := json.Unmarshal([]byte(runPyIn(t, ws, pyResolveSrc, string(in))), &got); err != nil {
		t.Fatal(err)
	}
	if got[0] == nil || *got[0] != filepath.Base(rd) {
		t.Errorf("CPython could not find the run by the loop id this port wrote")
	}
}
