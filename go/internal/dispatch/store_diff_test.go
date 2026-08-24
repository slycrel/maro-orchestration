package dispatch

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// storeGuard is the caller's half of the live-store rule: assert the path
// the PYTHON resolver answers, not the one the test meant. config.output_dir
// is the resolver every function under test goes through.
const storeGuard = `
import os as _o
from config import output_dir as _od
_r = str(_od())
if not _r.startswith(_o.path.realpath(_o.environ["MARO_WORKSPACE"])) and \
   not _r.startswith(_o.environ["MARO_WORKSPACE"]):
    raise SystemExit("refusing to run: output_dir() resolved to %s" % _r)
`

const pyStoreSrc = `
import json, sys
from dispatch_envelope import DispatchEnvelope, store_attachments, operator_block

out = []
for case in json.loads(sys.argv[1]):
    env = DispatchEnvelope(
        user_ask="x",
        operator_context=case.get("context", ""),
        operator_constraints=tuple(case.get("constraints", [])),
        attached_artifacts=tuple(case["artifacts"] or []))
    rows = store_attachments(env, key=case["key"])
    out.append({"rows": rows, "block": operator_block(env, rows)})
sys.stdout.write(json.dumps(out))
`

// storeCase is one call to store_attachments plus the operator block it
// feeds. Artifacts are decoded objects, so the fixture can carry the shapes
// the parser would have refused — store_attachments defends against them
// with str() and those defenses are live code.
type storeCase struct {
	Key         any              `json:"key"`
	Artifacts   []map[string]any `json:"artifacts"`
	Context     string           `json:"context,omitempty"`
	Constraints []string         `json:"constraints,omitempty"`
}

// TestStoreAttachmentsMatchesCPython compares the returned rows, the
// rendered operator block, AND the whole workspace tree the call left
// behind — three surfaces, because each one hides a different mistake.
//
// The rows alone would miss the sidecar's contents. The tree alone would
// miss that the row reports `str(art.get("name"))` — a THIRD spelling of the
// name, different from both the filename and the sidecar field.
func TestStoreAttachmentsMatchesCPython(t *testing.T) {
	cases := []storeCase{
		{
			Key: "job-1",
			Artifacts: []map[string]any{
				{"name": "notes.md", "content": "hello\n", "source": "https://x/1"},
				{"name": "notes.md", "content": "second copy", "source": ""},
				{"name": "notes.md", "content": "third copy"},
			},
			Context:     "framing from the dispatcher",
			Constraints: []string{"stay off the network", "no writes outside the run dir"},
		},
		{
			// Every name the sanitiser has an opinion about.
			Key: "job-2",
			Artifacts: []map[string]any{
				{"name": "../../etc/passwd", "content": "traversal"},
				{"name": "a/./b", "content": "dot component"},
				{"name": "C:\\Users\\x\\f.txt", "content": "windows"},
				{"name": ".gitignore", "content": "leading dot"},
				{"name": "日本語.md", "content": "unicode name"},
				{"name": "..", "content": "dot dot"},
				{"name": "_", "content": "underscore only"},
				{"name": strings.Repeat("n", 200) + ".txt", "content": "long"},
			},
		},
		{
			// The defensive branches: a missing name, an explicit null name,
			// a non-string content, a falsy source. None survive the parser;
			// all three str() calls in the loop are reachable from a caller
			// that built the envelope itself.
			Key: "job-3",
			Artifacts: []map[string]any{
				{"content": "no name at all"},
				{"name": nil, "content": "null name"},
				{"name": "n.txt", "content": 42},
				{"name": "s.txt", "content": "", "source": nil},
				{"name": "z.txt", "content": "unicode ✓ content", "source": 0},
			},
		},
		// Keys the sanitiser rewrites, and one that collides with job-1's
		// directory so the second call overwrites the first's files.
		{Key: "..", Artifacts: []map[string]any{{"name": "k.txt", "content": "dotdot key"}}},
		{Key: "", Artifacts: []map[string]any{{"name": "k.txt", "content": "empty key"}}},
		{Key: "a/b", Artifacts: []map[string]any{{"name": "k.txt", "content": "slash key"}}},
		{Key: nil, Artifacts: []map[string]any{{"name": "k.txt", "content": "null key"}}},
		{Key: 42, Artifacts: []map[string]any{{"name": "k.txt", "content": "int key"}}},
		{Key: "job-1", Artifacts: []map[string]any{{"name": "notes.md", "content": "overwrites"}}},
		// An empty artifact list is the early return: no directory at all.
		{Key: "job-empty", Artifacts: nil},
	}

	pyWS := t.TempDir()
	raw := pyprobe.Probe{
		Marker:    "dispatch_envelope.py",
		Workspace: pyWS,
		Guard:     storeGuard,
	}.Run(t, pyStoreSrc, pyprobe.Arg(t, cases))

	var want []struct {
		Rows []struct {
			Name   string `json:"name"`
			Path   string `json:"path"`
			Source string `json:"source"`
		} `json:"rows"`
		Block string `json:"block"`
	}
	if err := json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
	}

	goWS := t.TempDir()
	now := time.Date(2026, 8, 24, 3, 4, 5, 123456000, time.UTC)
	for i, c := range cases {
		env := &Envelope{
			UserAsk:             "x",
			OperatorContext:     c.Context,
			OperatorConstraints: c.Constraints,
			AttachedArtifacts:   objsOf(t, c.Artifacts),
		}
		rows, err := StoreAttachments(goWS, env, c.Key, now)
		if err != nil {
			t.Fatalf("case %d: StoreAttachments: %v", i, err)
		}
		if len(rows) != len(want[i].Rows) {
			t.Fatalf("case %d: %d rows, CPython wrote %d", i, len(rows), len(want[i].Rows))
		}
		for j, got := range rows {
			w := want[i].Rows[j]
			if got.Name != w.Name {
				t.Errorf("case %d row %d name: %q, CPython %q", i, j, got.Name, w.Name)
			}
			if got.Source != w.Source {
				t.Errorf("case %d row %d source: %q, CPython %q", i, j, got.Source, w.Source)
			}
			if gp, wp := strings.TrimPrefix(got.Path, goWS), strings.TrimPrefix(w.Path, pyWS); gp != wp {
				t.Errorf("case %d row %d path: %q, CPython %q", i, j, gp, wp)
			}
		}
		if block := maskWS(OperatorBlock(env, rows), goWS); block != maskWS(want[i].Block, pyWS) {
			t.Errorf("case %d operator block:\n got: %q\nwant: %q",
				i, block, maskWS(want[i].Block, pyWS))
		}
	}

	compareTrees(t, goWS, pyWS)
}

// objsOf turns the fixture's decoded maps into ordered objects. The fixture
// is written as Go maps for readability and Go maps have no order, so the
// keys are sorted — which is fine because the ORDER under test is the
// sidecar's, and that one is built by the port from a literal.
func objsOf(t *testing.T, ms []map[string]any) []pyval.Obj {
	t.Helper()
	var out []pyval.Obj
	for _, m := range ms {
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		var o pyval.Obj
		for _, k := range keys {
			o = append(o, pyval.Field{Key: k, Val: pyval.FromPlain(m[k])})
		}
		out = append(out, o)
	}
	return out
}

func maskWS(s, ws string) string { return strings.ReplaceAll(s, ws, "<WS>") }

var storedAtRe = regexp.MustCompile(`"stored_at": "[^"]*"`)

// compareTrees compares two workspaces file by file, by NAME as well as by
// content, with the one genuinely volatile field masked.
//
// The mask is on `stored_at`'s VALUE only. Dropping the key would also drop
// the question of whether both runtimes write it at all — the r11 lens,
// which came from a harness that skipped a whole class of file by name and
// thereby skipped the question of whether it existed.
func compareTrees(t *testing.T, goWS, pyWS string) {
	t.Helper()
	got, want := treeOf(t, goWS), treeOf(t, pyWS)
	for name, wantBody := range want {
		gotBody, ok := got[name]
		if !ok {
			t.Errorf("CPython wrote %s and the port did not", name)
			continue
		}
		if gotBody != wantBody {
			t.Errorf("%s differs\n got: %q\nwant: %q", name, gotBody, wantBody)
		}
	}
	for name := range got {
		if _, ok := want[name]; !ok {
			t.Errorf("the port wrote %s and CPython did not", name)
		}
	}
}

func treeOf(t *testing.T, root string) map[string]string {
	t.Helper()
	out := map[string]string{}
	err := filepath.Walk(root, func(p string, info os.FileInfo, err error) error {
		if err != nil || info.IsDir() {
			return err
		}
		body, rerr := os.ReadFile(p)
		if rerr != nil {
			return rerr
		}
		rel, rerr := filepath.Rel(root, p)
		if rerr != nil {
			return rerr
		}
		text := storedAtRe.ReplaceAllString(string(body), `"stored_at": "<TS>"`)
		out[rel] = maskWS(text, root)
		return nil
	})
	if err != nil {
		t.Fatalf("walking %s: %v", root, err)
	}
	return out
}
