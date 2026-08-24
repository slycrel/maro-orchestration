package dispatch

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
)

const pyOperatorSrc = `
import json, sys
from dispatch_envelope import (store_operator_attachments, EnvelopeError,
                               operator_attachment_block)

out = []
for case in json.loads(sys.argv[1]):
    try:
        rows = store_operator_attachments(case["paths"] or [], key=case["key"])
    except EnvelopeError as exc:
        out.append({"outcome": "refused", "message": str(exc)})
        continue
    except Exception as exc:
        out.append({"outcome": "raised", "type": type(exc).__name__,
                    "message": str(exc)})
        continue
    out.append({"outcome": "stored", "rows": rows,
                "block": operator_attachment_block(rows)})
sys.stdout.write(json.dumps(out))
`

type operatorCase struct {
	Key   any      `json:"key"`
	Paths []string `json:"paths"`
}

// TestStoreOperatorAttachmentsMatchesCPython drives the copy lane, its
// disambiguation, and every one of its refusals.
//
// The refusal MESSAGES are compared, not just the fact of refusal, and so is
// the EXCEPTION CLASS. The CLI catches EnvelopeError and prints one
// actionable line while anything else prints a traceback, so "which error"
// is a user-facing contract and not an implementation detail — `~nosuchuser`
// raises RuntimeError from expanduser and must NOT arrive wearing the lane's
// own refusal type.
//
// Two of the messages interpolate `str(src)`, which is a pathlib Path and
// therefore NORMALISED: `./a//b.txt` prints as `a/b.txt`. A port that
// reported the raw string would refuse one path and name another.
func TestStoreOperatorAttachmentsMatchesCPython(t *testing.T) {
	// One source tree, read by BOTH runtimes. Attachments come from outside
	// the workspace by construction — that is what makes them operator
	// attachments — so sharing the sources is what the real lane does.
	src := t.TempDir()
	mkdir := func(name string) string {
		p := filepath.Join(src, name)
		if err := os.Mkdir(p, 0o755); err != nil {
			t.Fatal(err)
		}
		return p
	}
	dirA, dirB := mkdir("a"), mkdir("b")
	write := func(dir, name, body string) string {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		return p
	}
	paperA := write(dirA, "paper.txt", "the paper\n")
	paperB := write(dirB, "paper.txt", "a DIFFERENT paper\n")
	paperCopy := write(dirB, "paper-copy.txt", "the paper\n")
	tarA := write(dirA, "bundle.tar.gz", "not really a tarball\n")
	tarB := write(dirB, "bundle.tar.gz", "also not a tarball\n")
	// `.hidden` is the suffix rule's version-sensitive shape: on this
	// interpreter its suffix is "" and its stem is the whole name, so the
	// disambiguated sibling is `.hidden-2` and not `-2.hidden`.
	hidA := write(dirA, ".hidden", "leading dot\n")
	hidB := write(dirB, ".hidden", "a different leading dot\n")
	unicode := write(dirA, "日本語.md", "unicode name\n")
	empty := write(dirA, "empty.txt", "")

	link := filepath.Join(src, "link.txt")
	if err := os.Symlink(paperA, link); err != nil {
		t.Fatal(err)
	}
	// A DANGLING link is the second realpath case: os.path.realpath does not
	// refuse one, it answers the path the link points at, and that path is
	// interpolated into the refusal message.
	dangling := filepath.Join(src, "dangling.txt")
	if err := os.Symlink(filepath.Join(src, "nope.txt"), dangling); err != nil {
		t.Fatal(err)
	}
	// Sparse: the size check reads stat(), never the bytes, so this costs no
	// disk and still exercises the only branch that inspects a file's length.
	huge := filepath.Join(src, "huge.bin")
	f, err := os.Create(huge)
	if err != nil {
		t.Fatal(err)
	}
	if err := f.Truncate(MaxAttachmentBytes + 1); err != nil {
		t.Fatal(err)
	}
	f.Close()

	cases := []operatorCase{
		{Key: "job-1", Paths: []string{paperA}},
		// Two files whose sanitised names COLLIDE and whose bytes differ: the
		// second lands under a name built from the stem and suffix.
		{Key: "job-2", Paths: []string{paperA, paperB}},
		// The same collision where the suffix is the interesting half.
		{Key: "job-3", Paths: []string{tarA, tarB}},
		{Key: "job-4", Paths: []string{hidA, hidB}},
		// Identical bytes under a colliding name are a re-store, not a
		// second file — and the same path twice is the same thing again.
		{Key: "job-5", Paths: []string{paperA, paperA}},
		{Key: "job-6", Paths: []string{unicode, empty}},
		// A stored row still points at the ORIGINAL name in `source`, so a
		// disambiguated pair must not lose which file came from where.
		{Key: "job-7", Paths: []string{paperA, paperCopy}},
		// The refusals.
		{Key: "job-8", Paths: []string{link}},
		{Key: "job-9", Paths: []string{dangling}},
		{Key: "job-10", Paths: []string{filepath.Join(src, "absent.txt")}},
		{Key: "job-11", Paths: []string{dirA}},
		{Key: "job-12", Paths: []string{huge}},
		// A path pathlib NORMALISES before it ever reaches the message.
		{Key: "job-13", Paths: []string{src + "/./no//such.txt"}},
		// NOT an EnvelopeError: expanduser raises RuntimeError, which the CLI
		// does not catch.
		{Key: "job-14", Paths: []string{"~nosuchuser-maro-goport/x.txt"}},
		// A refusal on the SECOND path, after the first already landed: the
		// partial write stays, which the tree comparison is what sees.
		{Key: "job-15", Paths: []string{unicode, link}},
		// Keys the sanitiser rewrites, and an empty path list.
		{Key: "..", Paths: []string{paperA}},
		{Key: nil, Paths: []string{paperA}},
		{Key: 42, Paths: []string{paperA}},
		{Key: "job-empty", Paths: nil},
	}

	pyWS := t.TempDir()
	raw := pyprobe.Probe{
		Marker:    "dispatch_envelope.py",
		Workspace: pyWS,
		Guard:     storeGuard,
	}.Run(t, pyOperatorSrc, pyprobe.Arg(t, cases))

	var want []struct {
		Outcome string `json:"outcome"`
		Message string `json:"message"`
		Type    string `json:"type"`
		Rows    []struct {
			Name   string `json:"name"`
			Path   string `json:"path"`
			Source string `json:"source"`
			SHA256 string `json:"sha256"`
			Bytes  int    `json:"bytes"`
		} `json:"rows"`
		Block string `json:"block"`
	}
	if err := json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
	}

	// The fixture's own guard. This table exists for the refusals and the
	// disambiguation; a version of it that stopped reaching either would
	// still agree with the port on everything it did reach.
	var refused, raised, disambiguated int
	for _, w := range want {
		switch w.Outcome {
		case "refused":
			refused++
		case "raised":
			raised++
		}
		for _, r := range w.Rows {
			// Counted off the PATH, not the name: the row's `name` is the
			// sanitised SOURCE name and stays that whatever the file ends up
			// being called, so two rows in one call can share a name and
			// differ only in path. A guard reading `name` would report zero
			// disambiguation on a table that disambiguates three times —
			// which is exactly what the first version of it did.
			if strings.Contains(filepath.Base(r.Path), "-2") {
				disambiguated++
			}
		}
	}
	if refused < 6 || raised < 1 || disambiguated < 3 {
		t.Fatalf("the fixture reaches %d refusal(s), %d non-EnvelopeError "+
			"raise(s) and %d disambiguated name(s); it is meant to drive at "+
			"least 6, 1 and 3", refused, raised, disambiguated)
	}

	goWS := t.TempDir()
	for i, c := range cases {
		rows, err := StoreOperatorAttachments(goWS, c.Paths, c.Key)
		w := want[i]
		if w.Outcome != "stored" {
			if err == nil {
				t.Errorf("case %d stored %d row(s); CPython %s with %q",
					i, len(rows), w.Outcome, w.Message)
				continue
			}
			if err.Error() != w.Message {
				t.Errorf("case %d %s:\n got: %q\nwant: %q",
					i, w.Outcome, err.Error(), w.Message)
			}
			// EnvelopeError is caught by the CLI and printed as one line;
			// everything else is a traceback. Getting the class wrong turns
			// a bug report into a shrug, or the reverse.
			var lane *Error
			if isLane := errors.As(err, &lane); isLane != (w.Outcome == "refused") {
				t.Errorf("case %d: port raised lane-error=%v; CPython raised %s (%s)",
					i, isLane, w.Outcome, w.Type)
			}
			continue
		}
		if err != nil {
			t.Errorf("case %d: %v; CPython stored %d row(s)", i, err, len(w.Rows))
			continue
		}
		if len(rows) != len(w.Rows) {
			t.Errorf("case %d: %d rows, CPython wrote %d", i, len(rows), len(w.Rows))
			continue
		}
		for j, got := range rows {
			r := w.Rows[j]
			if got.Name != r.Name {
				t.Errorf("case %d row %d name: %q, CPython %q", i, j, got.Name, r.Name)
			}
			if got.SHA256 != r.SHA256 {
				t.Errorf("case %d row %d sha256: %q, CPython %q", i, j, got.SHA256, r.SHA256)
			}
			if got.Bytes != r.Bytes {
				t.Errorf("case %d row %d bytes: %d, CPython %d", i, j, got.Bytes, r.Bytes)
			}
			if got.Source != r.Source {
				t.Errorf("case %d row %d source: %q, CPython %q", i, j, got.Source, r.Source)
			}
			if gp, wp := strings.TrimPrefix(got.Path, goWS), strings.TrimPrefix(r.Path, pyWS); gp != wp {
				t.Errorf("case %d row %d path: %q, CPython %q", i, j, gp, wp)
			}
		}
		if block := maskWS(OperatorAttachmentBlock(rows), goWS); block != maskWS(w.Block, pyWS) {
			t.Errorf("case %d attachment block:\n got: %q\nwant: %q",
				i, block, maskWS(w.Block, pyWS))
		}
	}

	compareTrees(t, goWS, pyWS)
}

const pySizeSrc = `
import json, sys
from dispatch_envelope import store_operator_attachments, EnvelopeError

out = []
for path in json.loads(sys.argv[1]):
    try:
        rows = store_operator_attachments([path], key="size")
    except EnvelopeError as exc:
        out.append({"outcome": "refused", "message": str(exc)})
        continue
    out.append({"outcome": "stored", "bytes": rows[0]["bytes"],
                "sha256": rows[0]["sha256"]})
sys.stdout.write(json.dumps(out))
`

// TestTheAttachmentSizeLimitIsExclusiveAtTheBoundary pins the comparison at
// exactly _MAX_ATTACHMENT_BYTES, which is the one place `>` and `>=` differ.
//
// It is a separate test rather than two more rows in the table above because
// it is the only case here that costs anything: the accepted file is read,
// hashed and written on both sides, 32MiB at a time. The table's other rows
// are bytes long, and folding this into them would put 128MiB of I/O behind
// every run of a test whose real subject is filenames.
//
// It exists at all because a mutation battery read off the FILE — not off
// the diff — found `>` -> `>=` surviving the entire dispatch suite. A limit
// with no case at its own boundary is a limit nothing pins.
func TestTheAttachmentSizeLimitIsExclusiveAtTheBoundary(t *testing.T) {
	src := t.TempDir()
	sparse := func(name string, size int64) string {
		p := filepath.Join(src, name)
		f, err := os.Create(p)
		if err != nil {
			t.Fatal(err)
		}
		defer f.Close()
		if err := f.Truncate(size); err != nil {
			t.Fatal(err)
		}
		return p
	}
	at := sparse("at-limit.bin", MaxAttachmentBytes)
	over := sparse("over-limit.bin", MaxAttachmentBytes+1)
	// One byte under is the third point that makes this a boundary and not a
	// pair: a mutation to `<` would pass a two-point test.
	under := sparse("under-limit.bin", MaxAttachmentBytes-1)

	pyWS := t.TempDir()
	raw := pyprobe.Probe{
		Marker:    "dispatch_envelope.py",
		Workspace: pyWS,
		Guard:     storeGuard,
	}.Run(t, pySizeSrc, pyprobe.Arg(t, []string{under, at, over}))
	var want []struct {
		Outcome string `json:"outcome"`
		Message string `json:"message"`
		Bytes   int    `json:"bytes"`
		SHA256  string `json:"sha256"`
	}
	if err := json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
	}
	if want[0].Outcome != "stored" || want[1].Outcome != "stored" ||
		want[2].Outcome != "refused" {
		t.Fatalf("CPython answered %q/%q/%q for under/at/over; the boundary is "+
			"not where this test believes it is",
			want[0].Outcome, want[1].Outcome, want[2].Outcome)
	}

	goWS := t.TempDir()
	for i, p := range []string{under, at, over} {
		rows, err := StoreOperatorAttachments(goWS, []string{p}, "size")
		w := want[i]
		if w.Outcome == "refused" {
			if err == nil {
				t.Errorf("%s: stored; CPython refused with %q", pathName(p), w.Message)
			} else if err.Error() != w.Message {
				t.Errorf("%s refusal:\n got: %q\nwant: %q", pathName(p), err.Error(), w.Message)
			}
			continue
		}
		if err != nil {
			t.Errorf("%s: %v; CPython stored %d bytes", pathName(p), err, w.Bytes)
			continue
		}
		if rows[0].Bytes != w.Bytes {
			t.Errorf("%s: %d bytes, CPython %d", pathName(p), rows[0].Bytes, w.Bytes)
		}
		if rows[0].SHA256 != w.SHA256 {
			t.Errorf("%s: sha256 %q, CPython %q", pathName(p), rows[0].SHA256, w.SHA256)
		}
	}
}

const pyLandSrc = `
import json, os, sys
from pathlib import Path
from dispatch_envelope import (DispatchEnvelope, store_attachments,
                               store_operator_attachments,
                               land_in_run_dir, land_operator_attachments)

ws = Path(os.environ["MARO_WORKSPACE"])
spec = json.loads(sys.argv[1])

env = DispatchEnvelope(user_ask="x",
                       attached_artifacts=tuple(spec["artifacts"]))
store_attachments(env, key=spec["key"])
store_operator_attachments(spec["operator_paths"], key=spec["key"])

run = ws / "output" / "runs" / "run-1"
run.mkdir(parents=True, exist_ok=True)

counts = []
counts.append(land_in_run_dir(run, spec["key"]))
counts.append(land_operator_attachments(run, spec["key"]))

# Perturb: overwrite the first landed file in each lane with DIFFERENT bytes
# and land again. This is where the two lanes stop agreeing.
for sub in ("dispatch", "operator"):
    d = run / "fetch-raw" / sub
    files = sorted(p for p in d.iterdir() if p.is_file())
    files[0].write_text("perturbed\n", encoding="utf-8")

counts.append(land_in_run_dir(run, spec["key"]))
counts.append(land_operator_attachments(run, spec["key"]))

# A key that resolves to a directory nothing ever wrote.
counts.append(land_in_run_dir(run, "no-such-key"))
counts.append(land_operator_attachments(run, "no-such-key"))

sys.stdout.write(json.dumps({"counts": counts}))
`

// TestTheLandingFunctionsMatchCPython drives both landing lanes twice each,
// with a perturbation in between.
//
// The perturbation is the whole point. The two functions look like one
// function with a parameter and are not: land_in_run_dir leaves an existing
// file alone WHATEVER its bytes, while the operator lane disambiguates
// differing bytes and keeps both — because dropping one of two distinct
// operator files is worse than a run tree holding an oddly-named pair. A
// port that shared one helper between them would pass a single-landing test
// and fail this one, and a test that landed only once would agree with
// either.
func TestTheLandingFunctionsMatchCPython(t *testing.T) {
	src := t.TempDir()
	opPath := filepath.Join(src, "notes.txt")
	if err := os.WriteFile(opPath, []byte("operator notes\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	opSecond := filepath.Join(src, "second.md")
	if err := os.WriteFile(opSecond, []byte("second file\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	artifacts := []map[string]any{
		{"name": "a.txt", "content": "alpha\n", "source": "https://x/a"},
		{"name": "b.txt", "content": "beta\n"},
	}
	spec := map[string]any{
		"key":            "job-land",
		"artifacts":      artifacts,
		"operator_paths": []string{opPath, opSecond},
	}

	pyWS := t.TempDir()
	raw := pyprobe.Probe{
		Marker:    "dispatch_envelope.py",
		Workspace: pyWS,
		Guard:     storeGuard,
	}.Run(t, pyLandSrc, pyprobe.Arg(t, spec))
	var want struct {
		Counts []int `json:"counts"`
	}
	if err := json.Unmarshal([]byte(raw), &want); err != nil {
		t.Fatalf("decoding the probe output: %v\nraw: %s", err, raw)
	}

	goWS := t.TempDir()
	now := time.Date(2026, 8, 24, 3, 4, 5, 123456000, time.UTC)
	env := &Envelope{UserAsk: "x", AttachedArtifacts: objsOf(t, artifacts)}
	if _, err := StoreAttachments(goWS, env, "job-land", now); err != nil {
		t.Fatal(err)
	}
	if _, err := StoreOperatorAttachments(goWS, []string{opPath, opSecond}, "job-land"); err != nil {
		t.Fatal(err)
	}
	run := filepath.Join(goWS, "output", "runs", "run-1")
	if err := os.MkdirAll(run, 0o755); err != nil {
		t.Fatal(err)
	}

	var got []int
	got = append(got, LandInRunDir(goWS, run, "job-land"))
	got = append(got, LandOperatorAttachments(goWS, run, "job-land"))
	for _, sub := range []string{"dispatch", "operator"} {
		names, err := sortedFileNames(filepath.Join(run, "fetch-raw", sub))
		if err != nil || len(names) == 0 {
			t.Fatalf("nothing landed in %s: %v", sub, err)
		}
		p := filepath.Join(run, "fetch-raw", sub, names[0])
		if err := os.WriteFile(p, []byte("perturbed\n"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	got = append(got, LandInRunDir(goWS, run, "job-land"))
	got = append(got, LandOperatorAttachments(goWS, run, "job-land"))
	got = append(got, LandInRunDir(goWS, run, "no-such-key"))
	got = append(got, LandOperatorAttachments(goWS, run, "no-such-key"))

	if len(got) != len(want.Counts) {
		t.Fatalf("%d counts, CPython reported %d", len(got), len(want.Counts))
	}
	// The counts must actually DIFFER across the two lanes on the re-land,
	// or this test is measuring two functions that happen to agree.
	if want.Counts[2] == want.Counts[3] {
		t.Fatalf("the re-land counts agree (%d and %d); the perturbation did "+
			"not reach the branch where the two lanes differ",
			want.Counts[2], want.Counts[3])
	}
	for i := range got {
		if got[i] != want.Counts[i] {
			t.Errorf("landing %d copied %d file(s), CPython copied %d",
				i, got[i], want.Counts[i])
		}
	}

	compareTrees(t, goWS, pyWS)
}
