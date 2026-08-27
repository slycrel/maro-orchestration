package record

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

// pyMemoryIndexOrderSrc seeds a memory directory with daily-log files given
// as EXACT BYTES and reports, per case, which seven _update_memory_index
// selected, whether it wrote the index at all, and what it wrote.
//
// The byte-list transport is not decoration. A daily-log name that is not
// valid UTF-8 is the only shape that can tell a code-point sort from a byte
// sort, and it cannot ride a JSON string: Go's encoder would replace it
// with U+FFFD before CPython ever saw it.
const pyMemoryIndexOrderSrc = `
import json, os, shutil, sys, tempfile
from pathlib import Path
spec = json.loads(sys.argv[1])
ws = Path(tempfile.mkdtemp(prefix="m1order-"))
assert str(ws).startswith("/tmp/"), ws
os.environ["MARO_WORKSPACE"] = str(ws)
import memory_ledger as ml
md = ml._memory_dir()
assert str(md).startswith("/tmp/"), md
out = {}
for case in spec["cases"]:
    # One workspace serves every case, so each one starts by clearing the
    # store. Without this a later case reads the earlier case's files and
    # the fixture measures a union nobody wrote.
    assert "/.maro/" not in str(md), md
    shutil.rmtree(md, ignore_errors=True)
    md.mkdir(parents=True, exist_ok=True)
    ml._outcomes_path().write_text(json.dumps(spec["row"]) + "\n", encoding="utf-8")
    for d in case["days"]:
        (md / d).write_text("x", encoding="utf-8")
    for b in case["byte_days"]:
        (md / os.fsdecode(bytes(b))).write_bytes(b"x")
    sel = sorted(md.glob("????-??-??.md"), reverse=True)[:7]
    ml._update_memory_index()
    ip = ml._memory_index_path()
    out[case["name"]] = {
        "selected": [list(os.fsencode(p.name)) for p in sel],
        "written": ip.exists(),
        "index": ip.read_text(encoding="utf-8") if ip.exists() else None,
    }
print(json.dumps(out))
`

// TestTheMemoryIndexSelectsAndRefusesLikeCPython pins two behaviours that
// only a non-UTF-8 daily-log filename can separate, and that the port had
// both wrong.
//
//  1. ORDER. `sorted(mem_dir.glob("????-??-??.md"), reverse=True)[:7]`
//     compares surrogateescape-decoded CODE POINTS. The port used
//     sort.Sort(sort.Reverse(sort.StringSlice(...))), which compares raw
//     BYTES. For the pair below the two disagree about which file is
//     largest, so the `[:7]` does not merely reorder the list — it keeps a
//     DIFFERENT FILE:
//
//     2026-08-0\x80.md      -> U+DC80 = 56448, byte 0x80 = 128
//     2026-08-0\xc3\xa9.md  -> U+00E9 =   233, byte 0xC3 = 195
//
//  2. ENCODABILITY. Once the surrogate name is in the kept seven it is
//     rendered into the index text, and `atomic_write` defaults to
//     errors="strict". The UnicodeEncodeError lands in the bare
//     `except: pass` around _update_memory_index's whole body, so CPython
//     writes NOTHING and MEMORY.md stays as it was. A Go string holds those
//     bytes happily and the port wrote the file.
//
// The two compose: the port's byte sort dropped the surrogate name, so
// every index it rendered happened to be encodable and the missing gate was
// invisible. Fixing only the sort would have made the port start writing
// indexes CPython refuses. That is why "order" and "gate" are separate
// cases below — one mutation each, rather than one case that fails for
// either reason and identifies neither.
func TestTheMemoryIndexSelectsAndRefusesLikeCPython(t *testing.T) {
	bytesOf := func(s string) []int {
		out := make([]int, 0, len(s))
		for i := 0; i < len(s); i++ {
			out = append(out, int(s[i]))
		}
		return out
	}
	// The two names the sorts disagree about, and a third that both sorts
	// agree is the smallest of the three.
	badByte := "2026-08-0\x80.md"
	eAcute := "2026-08-0\xc3\xa9.md"

	// Six ASCII days that both orders agree beat either contender: '1' at
	// index 8 beats '0'. With eight files the `[:7]` drops exactly one, and
	// which one it drops is the whole measurement.
	var six []string
	for d := 11; d <= 16; d++ {
		six = append(six, fmt.Sprintf("2026-08-%02d.md", d))
	}

	type caseSpec struct {
		Name     string   `json:"name"`
		Days     []string `json:"days"`
		ByteDays [][]int  `json:"byte_days"`
	}
	row := map[string]any{"outcome_id": "o", "goal": "g", "task_type": "loop",
		"status": "done", "summary": "s", "lessons": []any{},
		"tokens_in": 3, "tokens_out": 4}
	cases := []caseSpec{
		// EIGHT files: the six above plus both contenders. The dropped one
		// is the smallest, and the two sorts name different smallests.
		{Name: "order", Days: six,
			ByteDays: [][]int{bytesOf(badByte), bytesOf(eAcute)}},
		// THREE files, no truncation at all, one of them byte-tainted. The
		// selection is the same under either sort, so this case can only
		// fail on the encodability gate.
		{Name: "gate", Days: []string{"2026-08-11.md", "2026-08-12.md"},
			ByteDays: [][]int{bytesOf(badByte)}},
		// The control: eight files, none tainted. Both runtimes must write,
		// and write the same bytes. Without it a port that refused EVERY
		// write would pass the two cases above.
		// ByteDays is [][]int{} and not nil: a nil slice marshals to JSON
		// null, and `for b in None` is a TypeError, not an empty loop.
		{Name: "control", Days: append(append([]string{}, six...),
			"2026-08-09.md", "2026-08-17.md"), ByteDays: [][]int{}},
	}

	var want map[string]struct {
		Selected [][]int `json:"selected"`
		Written  bool    `json:"written"`
		Index    *string `json:"index"`
	}
	pyprobe.Probe{Marker: "memory_ledger.py"}.RunJSON(t, pyMemoryIndexOrderSrc,
		&want, pyprobe.Arg(t, map[string]any{"cases": cases, "row": row}))

	toStr := func(vs []int) string {
		b := make([]byte, 0, len(vs))
		for _, v := range vs {
			b = append(b, byte(v))
		}
		return string(b)
	}

	// Anti-vacuity: this whole test is written from the claim that CPython's
	// code-point order KEEPS the surrogate name and DROPS the é one. If that
	// claim is false, every assertion below still holds for reasons that
	// have nothing to do with the divergence, so check it explicitly.
	sel := map[string]bool{}
	for _, s := range want["order"].Selected {
		sel[toStr(s)] = true
	}
	if len(sel) != 7 {
		t.Fatalf("CPython selected %d files for the order case, want 7 — the "+
			"truncation this case exists for did not happen", len(sel))
	}
	if !sel[badByte] || sel[eAcute] {
		t.Fatalf("CPython's selection is %v; this fixture is written from the "+
			"claim that code-point order keeps %q and drops %q, and that "+
			"claim is what failed", sel, badByte, eAcute)
	}
	if want["order"].Written || want["gate"].Written {
		t.Fatalf("CPython WROTE the index with a surrogate filename in the "+
			"selection; the strict-encoder refusal this test pins did not "+
			"happen (order=%v gate=%v)", want["order"].Written, want["gate"].Written)
	}
	if !want["control"].Written {
		t.Fatal("CPython did not write the control index, so the two refusals " +
			"above are not evidence of anything")
	}

	stamp := regexp.MustCompile(`\*Auto-updated: [^*]*\*`)
	for _, c := range cases {
		t.Run(c.Name, func(t *testing.T) {
			mem := filepath.Join(t.TempDir(), "memory")
			if err := os.MkdirAll(mem, 0o777); err != nil {
				t.Fatal(err)
			}
			b, err := json.Marshal(row)
			if err != nil {
				t.Fatal(err)
			}
			write(t, filepath.Join(mem, "outcomes.jsonl"), string(b)+"\n")
			for _, d := range c.Days {
				write(t, filepath.Join(mem, d), "x")
			}
			// No decoding step on this side, which is the asymmetry under
			// test: a Go string already holds whatever bytes the name has.
			for _, bd := range c.ByteDays {
				write(t, filepath.Join(mem, toStr(bd)), "x")
			}

			err = updateMemoryIndex(mem)
			_, sErr := os.Stat(filepath.Join(mem, "MEMORY.md"))
			wrote := sErr == nil
			if wrote != want[c.Name].Written {
				t.Fatalf("index written=%v, CPython wrote=%v (go err: %v)",
					wrote, want[c.Name].Written, err)
			}
			if !want[c.Name].Written {
				// Python swallows; the port announces. Both decline to write,
				// which is the behaviour a shared store depends on.
				if err == nil {
					t.Errorf("the port declined to write but returned no error; " +
						"the named divergence in updateMemoryIndex's doc " +
						"comment is that a failure here is announced, not " +
						"passed over")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			raw, rerr := os.ReadFile(filepath.Join(mem, "MEMORY.md"))
			if rerr != nil {
				t.Fatal(rerr)
			}
			norm := func(s string) string { return stamp.ReplaceAllString(s, "*STAMP*") }
			got, py := norm(string(raw)), norm(*want[c.Name].Index)
			if got != py {
				t.Errorf("MEMORY.md differs from CPython's\n go:\n%s\nwant:\n%s",
					got, py)
			}
			// The control's whole job is proving the seven-file cut still
			// bites, so say which seven rather than trusting the byte match.
			if n := strings.Count(got, "\n- ["); n != 7 {
				t.Errorf("the index lists %d daily logs, want 7 — the "+
					"truncation did not happen and the order is unpinned", n)
			}
		})
	}
}
