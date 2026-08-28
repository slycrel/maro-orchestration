package pathrewrite

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// prEntry is one fixture filesystem entry. The tree is built in the SAME
// order on both sides because a symlink entry may name a target an
// earlier entry created.
type prEntry struct {
	Path   string `json:"path"`
	Kind   string `json:"kind"`
	Data   string `json:"data"`
	Target string `json:"target"`
	Mode   string `json:"mode"`
	Mtime  int64  `json:"mtime"`
}

// prSpec is one scenario. It is deliberately one flat struct rather than
// six: the probe dispatches on Kind and reads only the fields that kind
// needs, so a field left zero is a field that branch never touches.
//
// NOT ONE `omitempty`, on purpose (L6): the probe subscripts these, so an
// omitted field would be a KeyError rather than a zero value — and a
// probe that fills its own default is a probe that cannot tell "the
// scenario said empty" from "the scenario forgot".
type prSpec struct {
	Name        string     `json:"name"`
	Kind        string     `json:"kind"`
	Value       string     `json:"value"`
	ValueKind   string     `json:"value_kind"`
	Strict      bool       `json:"strict"`
	SourceRoots [][]string `json:"source_roots"`
	DestRoots   [][]string `json:"dest_roots"`
	Roles       []string   `json:"roles"`
	Pairs       [][]string `json:"pairs"`
	Data        string     `json:"data"`
	Text        string     `json:"text"`
	Tree        []prEntry  `json:"tree"`
	Rel         string     `json:"rel"`
	RelNames    []string   `json:"rel_names"`
	MaxBytes    int64      `json:"max_bytes"`
}

func b64e(b []byte) string { return base64.StdEncoding.EncodeToString(b) }

func b64d(t *testing.T, s string) []byte {
	t.Helper()
	b, err := base64.StdEncoding.DecodeString(s)
	if err != nil {
		t.Fatalf("bad fixture base64 %q: %v", s, err)
	}
	return b
}

// valueOf rebuilds the argument a scenario names, mirroring the probe's
// value_of. The non-string kinds are the point: validate_root's first
// test is isinstance(value, str) and its message carries the type name.
func valueOf(t *testing.T, kind, raw string) any {
	t.Helper()
	switch kind {
	case "str":
		return raw
	case "none":
		return nil
	case "int":
		n, err := strconv.Atoi(raw)
		if err != nil {
			t.Fatal(err)
		}
		return n
	case "float":
		f, err := strconv.ParseFloat(raw, 64)
		if err != nil {
			t.Fatal(err)
		}
		return f
	case "bool":
		return raw == "True"
	case "list":
		return []any{raw}
	}
	t.Fatalf("unknown value kind %s", kind)
	return nil
}

// pv renders a Go value the way the probe's json.dumps renders the
// Python one: a pyval.Obj becomes a list of [key, value] pairs, which is
// what obj_pairs produces on the other side.
func pv(v any) any {
	switch t := v.(type) {
	case pyval.Obj:
		out := []any{}
		for _, f := range t {
			out = append(out, []any{f.Key, pv(f.Val)})
		}
		return out
	case []any:
		out := []any{}
		for _, x := range t {
			out = append(out, pv(x))
		}
		return out
	}
	return v
}

func rolesOrDefault(r []string) []string {
	if r == nil {
		return Roles
	}
	return r
}

func mapFromPairs(pairs [][]string) RewriteMap {
	ps := []Pair{}
	for _, p := range pairs {
		ps = append(ps, Pair{p[0], p[1]})
	}
	return RewriteMap{Pairs: ps}
}

func rootsFrom(t *testing.T, triples [][]string) pyval.Obj {
	t.Helper()
	o := pyval.Obj{}
	for _, tr := range triples {
		o = append(o, pyval.Field{Key: tr[0], Val: valueOf(t, tr[1], tr[2])})
	}
	return o
}

// makeTree builds the fixture filesystem, in spec order.
func makeTree(t *testing.T, base string, entries []prEntry) {
	t.Helper()
	if err := os.MkdirAll(base, 0o755); err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		p := filepath.Join(base, e.Path)
		if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
			t.Fatal(err)
		}
		switch e.Kind {
		case "dir":
			if err := os.MkdirAll(p, 0o755); err != nil {
				t.Fatal(err)
			}
		case "fifo":
			if err := syscall.Mkfifo(p, 0o644); err != nil {
				t.Fatal(err)
			}
		case "symlink":
			if err := os.Symlink(e.Target, p); err != nil {
				t.Fatal(err)
			}
		default:
			if err := os.WriteFile(p, b64d(t, e.Data), 0o644); err != nil {
				t.Fatal(err)
			}
			if e.Mode != "" {
				m, err := strconv.ParseUint(e.Mode, 8, 32)
				if err != nil {
					t.Fatal(err)
				}
				if err := syscall.Chmod(p, uint32(m)); err != nil {
					t.Fatal(err)
				}
			}
			if e.Mtime != 0 {
				tv := []syscall.Timeval{
					{Sec: e.Mtime}, {Sec: e.Mtime},
				}
				if err := syscall.Utimes(p, tv); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
}

// fileState is what the tree looks like AFTER the call. The bytes are the
// point, but the mode and the mtime are the two things a port silently
// loses: an atomic swap through a fresh temp file gets the umask's mode
// and today's mtime unless it is asked not to.
func fileState(base string, entries []prEntry) ([]any, []any) {
	out := []any{}
	for _, e := range entries {
		if e.Kind == "dir" {
			// Reported because a directory sitting on the temp path is how
			// the failed-swap branch is reached, and whether it SURVIVES
			// is the whole question there.
			state := "<gone>"
			if fi, err := os.Stat(filepath.Join(base, e.Path)); err == nil &&
				fi.IsDir() {
				state = "<dir>"
			}
			out = append(out, []any{e.Path, state, "", 0})
			continue
		}
		if e.Kind != "file" {
			continue
		}
		p := filepath.Join(base, e.Path)
		fi, err := os.Lstat(p)
		if err != nil {
			out = append(out, []any{e.Path, "<gone>", "", 0})
			continue
		}
		// A mode-000 fixture is deliberate — it is how RewriteFile is
		// driven to "unreadable" — so the reader must survive it.
		enc := "<unreadable>"
		if data, err := os.ReadFile(p); err == nil {
			enc = b64e(data)
		}
		st, _ := fi.Sys().(*syscall.Stat_t)
		mode := ""
		mtime := int64(0)
		if st != nil {
			mode = pyOct(st.Mode & 0o7777)
			mtime = st.Mtim.Sec
		}
		out = append(out, []any{e.Path, enc, mode, mtime})
	}
	leftovers := []any{}
	_ = filepath.Walk(base, func(p string, fi os.FileInfo, err error) error {
		if err != nil || fi.IsDir() {
			return nil
		}
		if strings.HasSuffix(fi.Name(), TmpSuffix) {
			rel, _ := filepath.Rel(base, p)
			leftovers = append(leftovers, rel)
		}
		return nil
	})
	sort.Slice(leftovers, func(i, j int) bool {
		return leftovers[i].(string) < leftovers[j].(string)
	})
	return out, leftovers
}

// pyOct is Python's oct(n) — "0o644", not Go's "0644".
func pyOct(n uint32) string { return "0o" + strconv.FormatUint(uint64(n), 8) }

// goRun mirrors the probe's run_one over the Go package.
func goRun(t *testing.T, sc prSpec, root string) map[string]any {
	t.Helper()
	out := map[string]any{"name": sc.Name, "cls": "", "msg": ""}
	base := filepath.Join(root, sc.Name)
	fail := func(err error) {
		out["cls"] = pyval.ClassOf(err)
		out["msg"] = err.Error()
	}
	switch sc.Kind {
	case "validate":
		v, err := ValidateRoot(valueOf(t, sc.ValueKind, sc.Value), sc.Strict)
		if err != nil {
			fail(err)
			break
		}
		out["value"] = v
	case "build":
		m := BuildMap(rootsFrom(t, sc.SourceRoots),
			rootsFrom(t, sc.DestRoots), rolesOrDefault(sc.Roles))
		pairs := []any{}
		for _, p := range m.Pairs {
			pairs = append(pairs, []any{p.Src, p.Dst})
		}
		rej := []any{}
		for _, r := range m.Rejected {
			rej = append(rej, []any{r.Role, r.Value, r.Reason})
		}
		desc := []any{}
		for _, d := range m.Describe() {
			desc = append(desc, pv(d))
		}
		out["pairs"] = pairs
		out["rejected"] = rej
		out["describe"] = desc
		out["truthy"] = m.Bool()
	case "substitute":
		data, n := mapFromPairs(sc.Pairs).Substitute(b64d(t, sc.Data))
		out["data"] = b64e(data)
		out["count"] = n
	case "substitute_text":
		text, n := mapFromPairs(sc.Pairs).SubstituteText(sc.Text)
		out["text"] = text
		out["count"] = n
	case "skip":
		makeTree(t, base, sc.Tree)
		out["value"] = SkipReason(sc.Rel, filepath.Join(base, sc.Rel))
	case "rewrite_file":
		makeTree(t, base, sc.Tree)
		status, n := RewriteFile(filepath.Join(base, sc.Rel),
			mapFromPairs(sc.Pairs), sc.MaxBytes)
		out["value"] = status
		out["count"] = n
		out["files"], out["leftovers"] = fileState(base, sc.Tree)
	case "rewrite_tree":
		makeTree(t, base, sc.Tree)
		m := BuildMap(rootsFrom(t, sc.SourceRoots),
			rootsFrom(t, sc.DestRoots), Roles)
		rep := RewriteTree(base, sc.RelNames, m, sc.MaxBytes)
		out["record"] = pv(rep.AsRecord())
		out["summary"] = rep.Summary()
		out["files_after"], out["leftovers"] = fileState(base, sc.Tree)
	default:
		t.Fatalf("unknown kind %s", sc.Kind)
	}
	return out
}

// TestPathRewriteMatchesCPython drives both engines over the same
// scenarios and compares the records byte for byte.
//
// The fixture filesystem is described once, in the spec, and built by
// each side in its OWN temp directory. Nothing in a record names an
// absolute path, so there is nothing to canonicalise: a record that
// differs, differs about behaviour.
func TestPathRewriteMatchesCPython(t *testing.T) {
	scs := prScenarios()

	goRoot := t.TempDir()
	goRecs := make([]map[string]any, 0, len(scs))
	for _, sc := range scs {
		goRecs = append(goRecs, goRun(t, sc, goRoot))
	}

	pyRecs := runPathRewriteProbe(t, scs)
	if len(pyRecs) != len(goRecs) {
		t.Fatalf("probe returned %d records, want %d", len(pyRecs),
			len(goRecs))
	}
	for i := range scs {
		want := canon(pyRecs[i])
		got := canon(goRecs[i])
		if !reflect.DeepEqual(want, got) {
			t.Errorf("scenario %q diverges\n  cpython: %s\n  go:      %s",
				scs[i].Name, mustJSON(t, want), mustJSON(t, got))
		}
	}
}

// canon round-trips a record through JSON so both sides are compared as
// the same shapes — a Go int and a CPython int are both json.Number here,
// and a nil slice and an empty list are both [].
func canon(rec map[string]any) any {
	blob, err := json.Marshal(rec)
	if err != nil {
		return map[string]any{"marshal-error": err.Error()}
	}
	var out any
	if err := json.Unmarshal(blob, &out); err != nil {
		return map[string]any{"unmarshal-error": err.Error()}
	}
	return out
}

func mustJSON(t *testing.T, v any) string {
	t.Helper()
	blob, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	return string(blob)
}

func runPathRewriteProbe(t *testing.T, scs []prSpec) []map[string]any {
	t.Helper()
	blob, err := json.Marshal(scs)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	specPath := filepath.Join(dir, "pathrewrite-scenarios.json")
	if err := os.WriteFile(specPath, blob, 0o644); err != nil {
		t.Fatal(err)
	}
	src, err := os.ReadFile("pathrewrite_probe.py.tpl")
	if err != nil {
		t.Fatal(err)
	}
	// The probe WRITES: it builds a fixture filesystem per scenario. The
	// root it is handed is this test's own temp directory, and no
	// scenario names a path outside it — the one that tries
	// (`/etc/hostname`) is the containment fixture, which is refused
	// before anything opens it.
	pyRoot := t.TempDir()
	out := pyprobe.Probe{Marker: "path_rewrite.py", Workspace: t.TempDir()}.
		Run(t, string(src), pyprobe.SrcDir(t, "path_rewrite.py"), specPath,
			pyRoot)
	var recs []map[string]any
	if err := json.Unmarshal([]byte(out), &recs); err != nil {
		t.Fatalf("probe output: %v\n%s", err, out)
	}
	return recs
}

func TestPathRewriteScenarioNamesAreUnique(t *testing.T) {
	// Every scenario names a DIRECTORY, so a duplicate would not merely
	// confuse a failure message — the second run would inherit the first
	// one's rewritten files and agree with the probe for the wrong reason.
	seen := map[string]bool{}
	for _, sc := range prScenarios() {
		if seen[sc.Name] {
			t.Errorf("duplicate scenario name %q", sc.Name)
		}
		seen[sc.Name] = true
	}
}
