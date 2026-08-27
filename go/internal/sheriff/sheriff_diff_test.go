package sheriff

import (
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/slycrel/maro-orchestration/go/internal/orch"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/pyprobe"
	"github.com/slycrel/maro-orchestration/go/internal/pyval"
)

// shPySrc drives the real sheriff.py. It is the same probe that captured
// this chunk's ground truth BEFORE any Go existed (scratchpad/sh_probe.py),
// carried here verbatim rather than paraphrased — the ground-truth pass and
// the differential must not be able to drift apart (L49).
//
// Two fixture shapes are worth reading before the cases:
//
//   - `files` and `mtimes` are ORDERED objects, not Go maps. A Go map
//     marshals sorted, which would apply the "." mtime FIRST, and writing
//     any file into a directory afterwards resets that directory's mtime —
//     silently un-ageing every dormancy fixture.
//   - `now` replaces sheriff.time.time for the duration of one case. Two
//     processes cannot agree on the wall clock, and check_project renders
//     it twice (the artifact age and the dormancy age), so the clock is a
//     fixture here and a parameter on the Go side. Masking those two lines
//     instead would have left the ".0f" rounding and the threshold
//     comparison untested — the whole reason C24 exists.
const shPySrc = `
import hashlib, json, os, pathlib, sys, time
import sheriff as sh
from orch import project_dir

def mkproj(root, slug, files):
    """Build a project tree and return its dir. files maps relpath -> text,
    or relpath -> None for a directory."""
    d = project_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        p = d / rel
        if text is None:
            p.mkdir(parents=True, exist_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d

out = []
for c in json.loads(sys.argv[1]):
    try:
        k = c["kind"]
        if k == "report_format":
            r = sh.SheriffReport(project=c["project"], status=c["status"],
                                 diagnosis=c["diagnosis"], evidence=c["evidence"],
                                 recommended_action=c["action"],
                                 checked_at=c["checked_at"])
            out.append({"ok": {"text": r.format("text"), "json": r.format("json"),
                               "other": r.format("zzz")}})
        elif k == "health_format":
            h = sh.SystemHealth(status=c["status"],
                                checks={a: b for a, b in c["checks"]},
                                checked_at=c["checked_at"])
            out.append({"ok": {"text": h.format("text"), "json": h.format("json")}})
        elif k == "rollup":
            # The checks -> status rule, lifted out of check_system_health so
            # the ENVIRONMENT probing does not have to be reproduced to test
            # the only part of it that is a pure function.
            checks = {a: b for a, b in c["checks"]}
            fails = [x for x, v in checks.items() if v.startswith("fail")]
            warns = [x for x, v in checks.items() if v.startswith("warn")]
            out.append({"ok": "critical" if fails else ("degraded" if warns else "healthy")})
        elif k == "no_progress":
            out.append({"ok": sh.detect_no_progress(c["fps"])})
        elif k == "fingerprint":
            slug = c["slug"]
            mkproj(None, slug, c["files"])
            out.append({"ok": sh.fingerprint_project_state(slug)})
        elif k == "lifecycle":
            slug = c["slug"]
            mkproj(None, slug, c["files"])
            out.append({"ok": sh.project_lifecycle_state(slug)})
        elif k == "check_project":
            slug = c["slug"]
            if c.get("nocreate"):
                d = project_dir(slug)
            else:
                d = mkproj(None, slug, c["files"])
            # Pin every artifact mtime so "Newest artifact" and its age are
            # reproducible; the differential masks the age but not the NAME,
            # and the name is chosen by a sort on mtime.
            for rel, mt in c.get("mtimes", {}).items():
                os.utime(d / rel, (mt, mt))
            # check_project reads the wall clock twice — the dormancy age and
            # the artifact age — and both reach the OUTPUT as rendered
            # numbers. Two processes cannot agree on time.time(), so the
            # clock is a fixture here and a parameter on the Go side; the
            # alternative is masking the two lines, which would leave the
            # ".0f" rounding and the dormancy threshold untested.
            _real_time = sh.time.time
            if "now" in c:
                sh.time.time = lambda: c["now"]
            # The dormancy threshold is a config read on the Python side and
            # a parameter on the Go side; patching it here is what lets the
            # DISABLED lane (0) be tested end to end rather than only through
            # _dormant_days in isolation.
            import config as _cfg
            _real_get = _cfg.get
            if "dormant_days" in c:
                _cfg.get = lambda key, default=None: (
                    c["dormant_days"] if key == "sheriff.dormant_days" else
                    _real_get(key, default))
            try:
                r = sh.check_project(slug, window_minutes=c.get("window", 30))
            finally:
                sh.time.time = _real_time
                _cfg.get = _real_get
            out.append({"ok": {"status": r.status, "diagnosis": r.diagnosis,
                               "evidence": r.evidence,
                               "action": r.recommended_action}})
        elif k == "fmt":
            # The two float spellings check_project interpolates.
            out.append({"ok": {"f0": "%.0f" % c["v"], "g": "%g" % c["v"],
                               "fmt0": format(c["v"], ".0f"),
                               "fmtg": format(c["v"], "g")}})
        elif k == "repr60":
            # The f-string spells it {text[:60]!r} — a 60-CODE-POINT slice,
            # then repr. (No backtick here: this source is carried verbatim
            # into a Go raw string literal, which a backtick terminates.)
            out.append({"ok": repr(c["s"][:60])})
        elif k == "listrepr":
            # The evidence lines interpolate a LIST of strings with str().
            out.append({"ok": "%s" % (c["items"][:3],)})
        elif k == "dormant_days":
            # _dormant_days is float(get(key, DEFAULT) or 0) inside a blanket
            # try. Three lanes with three different answers, and the middle
            # one is the trap: a FALSY setting collapses to 0 (the check
            # disabled), while an UNPARSEABLE one falls all the way back to
            # the 14-day default. A port that spells both as "the default"
            # turns "dormancy off" into "dormancy on".
            import config as _cfg
            _real = _cfg.get
            if c.get("missing"):
                _cfg.get = lambda key, default=None: default
            else:
                _cfg.get = lambda key, default=None: c["v"]
            try:
                out.append({"ok": sh._dormant_days()})
            finally:
                _cfg.get = _real
        elif k == "activity_fsnames":
            # project_activity_age_days over an artifacts/ directory whose
            # names are NOT all valid UTF-8.
            # Names ride as lists of BYTE VALUES in both directions, and
            # the reason is the RECEIVER, not the writer. MEASURED: with
            # ensure_ascii=True -- the default -- json.dumps happily writes
            # a surrogateescaped name as "\udc80b", and json.loads reads it
            # back unchanged. Go's encoding/json does NOT: it decodes
            # \udc80 to U+FFFD (ef bf bd) without an error, so a name that
            # survived the Python side arrives on the Go side as a
            # different name and the comparison passes or fails for the
            # wrong reason. (ensure_ascii=False is the case that raises,
            # and nothing here passes it.)
            #
            # sorted(artifacts.iterdir())[:50] TRUNCATES, so an ordering
            # difference is not cosmetic: the two runtimes stat a different
            # fifty files, and the newest mtime among them is the answer.
            d = project_dir(c["slug"])
            (d / "artifacts").mkdir(parents=True, exist_ok=True)
            ad = os.fsencode(str(d / "artifacts"))
            for vals, mt in zip(c["names"], c["mtimes"]):
                fp = os.path.join(ad, bytes(vals))
                with open(fp, "wb") as fh:
                    fh.write(b"x")
                os.utime(fp, (mt, mt))
            # The project dir AND artifacts/ itself are candidates too, and
            # creating sixty files stamps artifacts/ with the wall clock.
            # Pin both, artifacts/ last-created-into first, or the newest
            # mtime found is a DIRECTORY and the fifty names never matter
            # -- which is exactly how the first cut of this fixture passed
            # against a byte-sorting port.
            os.utime(d / "artifacts", (c["dir_mtime"], c["dir_mtime"]))
            os.utime(d, (c["dir_mtime"], c["dir_mtime"]))
            _real_time = sh.time.time
            sh.time.time = lambda: c["now"]
            try:
                age = sh.project_activity_age_days(c["slug"])
            finally:
                sh.time.time = _real_time
            out.append({"ok": None if age is None else int(age)})
        elif k == "all_projects":
            # check_all_projects over a projects/ dir seeded with entries
            # that are NOT all projects: dotted and underscored names, a
            # regular file, a dangling symlink, a symlink TO a directory,
            # and names that are not valid UTF-8. A surrogateescaped slug
            # is exactly what sorted() is being tested on.
            # Names ride as lists of BYTE VALUES in both directions, and
            # the reason is the RECEIVER, not the writer. MEASURED: with
            # ensure_ascii=True -- the default -- json.dumps happily writes
            # a surrogateescaped name as "\udc80b", and json.loads reads it
            # back unchanged. Go's encoding/json does NOT: it decodes
            # \udc80 to U+FFFD (ef bf bd) without an error, so a name that
            # survived the Python side arrives on the Go side as a
            # different name and the comparison passes or fails for the
            # wrong reason. (ensure_ascii=False is the case that raises,
            # and nothing here passes it.)
            import orch as _orch, shutil as _sh_util
            root = _orch.projects_root()
            # Every case shares one probe process and one workspace, so an
            # enumeration fixture must start from a KNOWN projects/ rather
            # than from whatever the check_project cases left. The guard is
            # not decoration: MARO_WORKSPACE is the store override and a
            # probe that resolved to the live one would rmtree the real
            # ledgers (2026-08-16). Assert the resolved path, then delete.
            assert "/.maro/" not in str(root), "refusing to clear " + str(root)
            _sh_util.rmtree(root, ignore_errors=True)
            # nocreate leaves projects/ ABSENT. Without it the "missing
            # directory" fixture tested an EMPTY one, which takes the other
            # branch entirely -- the first cut of A62 did exactly that and
            # a mutant returning the star report for a missing dir walked
            # straight through it.
            if not c.get("nocreate"):
                root.mkdir(parents=True, exist_ok=True)
            rb = os.fsencode(str(root))
            for vals in c.get("dirs", []):
                p = os.path.join(rb, bytes(vals))
                os.makedirs(p, exist_ok=True)
                with open(os.path.join(p, b"NEXT.md"), "wb") as fh:
                    fh.write(b"# NEXT\n\n- [ ] todo one\n")
                os.makedirs(os.path.join(p, b"artifacts"), exist_ok=True)
                with open(os.path.join(p, b"artifacts", b"a.txt"), "wb") as fh:
                    fh.write(b"x")
            for vals in c.get("plainfiles", []):
                with open(os.path.join(rb, bytes(vals)), "wb") as fh:
                    fh.write(b"not a project")
            for vals, target in c.get("links", []):
                os.symlink(target, os.path.join(rb, bytes(vals)))
            if c.get("unreadable"):
                os.chmod(root, 0o000)
            _real_time = sh.time.time
            if "now" in c:
                sh.time.time = lambda: c["now"]
            try:
                reps = sh.check_all_projects(window_minutes=c.get("window", 30))
            finally:
                sh.time.time = _real_time
                if c.get("unreadable"):
                    os.chmod(root, 0o755)
            # The diagnosis is elided to its PREFIX on both sides: the "*"
            # report interpolates str(exc), and Python spells an OSError
            # "[Errno 13] Permission denied: '<p>'" where Go spells it
            # "open <p>: permission denied". Named residual, not a bug --
            # so the fixture pins the shape and the prefix, which is what
            # a reader keys on, and elides the half the two disagree about.
            out.append({"ok": [[list(os.fsencode(r.project)), r.status,
                                r.diagnosis[:30]] for r in reps]})
        elif k == "archive":
            # archive_dormant_projects. The answer is the json.dumps TEXT of
            # the entry list, not the parsed value, because the entry's KEY
            # ORDER (project, age_days, moved, target-only-when-moved) is
            # half of what is being compared and a reparse would lose it.
            import orch as _orch, shutil as _sh_util
            root = _orch.projects_root()
            assert "/.maro/" not in str(root), "refusing to clear " + str(root)
            _sh_util.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True, exist_ok=True)
            rb = os.fsencode(str(root))
            for slug, mt in c["projects"]:
                # A slug rides as a STRING when it is valid UTF-8 and as a
                # list of byte values when it is not. See the note in the
                # all_projects arm for why the byte list is the receiver's
                # requirement rather than json.dumps'.
                nb = slug.encode() if isinstance(slug, str) else bytes(slug)
                db = os.path.join(rb, nb)
                os.makedirs(os.path.join(db, b"artifacts"), exist_ok=True)
                with open(os.path.join(db, b"NEXT.md"), "wb") as fh:
                    fh.write(b"# NEXT\n")
                for q in (os.path.join(db, b"NEXT.md"),
                          os.path.join(db, b"artifacts"), db):
                    os.utime(q, (mt, mt))
            for extra in c.get("premade", []):
                (root / "_archive" / extra).mkdir(parents=True, exist_ok=True)
            _real_time = sh.time.time
            sh.time.time = lambda: c["now"]
            try:
                entries = sh.archive_dormant_projects(days=c["days"],
                                                      apply=c["apply"])
            finally:
                sh.time.time = _real_time
            # The absolute target path differs between the two workspaces,
            # so it is reduced to the part under projects/ on both sides.
            for e in entries:
                if "target" in e:
                    e["target"] = str(pathlib.Path(e["target"]).relative_to(root))
            # The entry ORDER rides separately, as byte lists, so the sort
            # can be pinned on names json.dumps could never carry.
            order = [list(os.fsencode(e["project"])) for e in entries]
            text = None if c.get("notext") else json.dumps(entries)
            listing = [list(os.fsencode(x)) for x in sorted(os.listdir(root))]
            adir = root / "_archive"
            arch = [list(os.fsencode(x)) for x in sorted(os.listdir(adir))] if adir.exists() else None
            # The archive root's MODE. mkdir(exist_ok=True) takes Python's
            # default 0o777, so what lands is 0o777 & ~umask -- 0o775 here.
            # A port hard-coding 0o755 creates a group-read-only directory
            # where Python creates a group-writable one, and nothing else
            # in this suite can see that.
            mode = ("%o" % (adir.stat().st_mode & 0o7777)) if adir.exists() else None
            out.append({"ok": {"entries": text, "order": order,
                               "root": listing, "archive": arch, "mode": mode}})
        elif k == "heartbeat_write":
            # write_heartbeat_state, compared as the FILE'S BYTES. Three
            # json.dumps decisions ride in one call -- indent-2 drops the
            # item separator's trailing space, ensure_ascii escapes every
            # non-ASCII rune, and < and & stay raw -- and only the bytes
            # can tell whether all three landed.
            import orch as _orch
            _orch.memory_dir().mkdir(parents=True, exist_ok=True)
            h = sh.SystemHealth(status=c["status"],
                                checks={a: b for a, b in c["checks"]},
                                checked_at=c["checked_at"])
            reps = [sh.SheriffReport(project=p, status=s, diagnosis="",
                                     evidence=[]) for p, s in c["reports"]]
            path = sh.write_heartbeat_state(
                h, project_reports=(reps if c["with_reports"] else None))
            body = pathlib.Path(path).read_text(encoding="utf-8") if path else None
            out.append({"ok": {"wrote": path is not None, "body": body,
                               "name": os.path.basename(path) if path else None}})
        elif k == "heartbeat_read":
            import orch as _orch
            md = _orch.memory_dir()
            md.mkdir(parents=True, exist_ok=True)
            # One probe process, one workspace: the heartbeat_write cases
            # already wrote this file, so "absent" has to be MADE absent.
            # The first cut of this arm did not and the missing-file case
            # read back the previous case's payload -- a fixture that
            # tested the wrong thing while passing on the Go side because
            # the Go side gets a fresh workspace per case.
            assert "/.maro/" not in str(md), "refusing to unlink under " + str(md)
            state = md / "heartbeat-state.json"
            if state.exists():
                state.unlink()
            if c["body"] is not None:
                (md / "heartbeat-state.json").write_text(c["body"], encoding="utf-8")
            got = sh.read_heartbeat_state()
            # A non-object top level is the one shape the two runtimes
            # answer differently, so the comparison records the TYPE rather
            # than the value: Python hands back whatever json.loads made
            # (its annotation notwithstanding) and Go answers absent.
            kind = ("absent" if got is None else
                    ("object" if isinstance(got, dict) else "other"))
            out.append({"ok": {"kind": kind,
                               "text": json.dumps(got) if isinstance(got, dict) else None}})
        elif k == "md5":
            out.append({"ok": hashlib.md5(c["s"].encode()).hexdigest()})
        elif k == "slice2000":
            out.append({"ok": c["s"][-2000:]})
        else:
            out.append({"err": "unknown kind " + k})
    except Exception as e:
        out.append({"err": type(e).__name__ + ": " + str(e)})
print(json.dumps(out))
`

// kv is one entry of an ORDERED JSON object. See the note on shPySrc.
type kv struct {
	K string
	V any
}

type odict []kv

func (o odict) MarshalJSON() ([]byte, error) {
	out := []byte("{")
	for i, e := range o {
		if i > 0 {
			out = append(out, ',')
		}
		k, err := json.Marshal(e.K)
		if err != nil {
			return nil, err
		}
		v, err := json.Marshal(e.V)
		if err != nil {
			return nil, err
		}
		out = append(out, k...)
		out = append(out, ':')
		out = append(out, v...)
	}
	return append(out, '}'), nil
}

type shCase struct {
	name string
	spec map[string]any
	// build, when set, makes this case's project tree inside the Go
	// workspace — the mirror of the probe's mkproj plus its utime pass.
	build func(t *testing.T, ws string)
	// run produces the Go answer in the shape the probe produces the
	// Python one.
	run func(ws string) any
}

// shMkProj mirrors the probe's mkproj: a value of nil means a DIRECTORY.
func shMkProj(t *testing.T, ws, slug string, files odict) string {
	t.Helper()
	d := orch.ProjectDir(ws, slug)
	if err := os.MkdirAll(d, 0o777); err != nil {
		t.Fatal(err)
	}
	for _, f := range files {
		p := filepath.Join(d, f.K)
		if f.V == nil {
			if err := os.MkdirAll(p, 0o777); err != nil {
				t.Fatal(err)
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(p, []byte(f.V.(string)), 0o666); err != nil {
			t.Fatal(err)
		}
	}
	return d
}

func shUtimes(t *testing.T, dir string, mtimes odict) {
	t.Helper()
	for _, m := range mtimes {
		sec := m.V.(float64)
		ts := time.Unix(int64(sec), int64((sec-float64(int64(sec)))*1e9))
		if err := os.Chtimes(filepath.Join(dir, m.K), ts, ts); err != nil {
			t.Fatal(err)
		}
	}
}

// shNow is the fixed clock a case pins, or the real one when it does not.
func shNow(spec map[string]any) time.Time {
	if v, ok := spec["now"]; ok {
		sec := v.(float64)
		return time.Unix(int64(sec), int64((sec-float64(int64(sec)))*1e9))
	}
	return time.Now()
}

func shCases() []shCase {
	var cs []shCase
	add := func(name string, spec map[string]any, run func(ws string) any) {
		cs = append(cs, shCase{name: name, spec: spec, run: run})
	}

	// --- SheriffReport.format ----------------------------------------------
	// The probe's R() defaults, spelled once here so a fixture that changes
	// one field reads as that one field.
	R := func(name string, r Report, action any) {
		r.Project, r.CheckedAt = "p", "2026-08-26T00:00:00+00:00"
		if r.Status == "" {
			r.Status = "healthy"
		}
		if r.Diagnosis == "" {
			r.Diagnosis = "fine"
		}
		if r.Evidence == nil {
			r.Evidence = []string{}
		}
		if s, ok := action.(string); ok {
			r = r.WithAction(s)
		}
		add(name, map[string]any{"kind": "report_format", "project": r.Project,
			"status": r.Status, "diagnosis": r.Diagnosis,
			"evidence": r.Evidence, "action": action,
			"checked_at": r.CheckedAt},
			func(string) any {
				text, err := r.Format("text")
				if err != nil {
					return err.Error()
				}
				js, err := r.Format("json")
				if err != nil {
					return err.Error()
				}
				other, err := r.Format("zzz")
				if err != nil {
					return err.Error()
				}
				return map[string]any{"text": text, "json": js, "other": other}
			})
	}
	R("F1 no evidence, no action", Report{}, nil)
	R("F2 evidence lines are indented", Report{Evidence: []string{"a", "b"}}, nil)
	R("F3 an action line only when one is set", Report{}, "do the thing")
	R("F4 an EMPTY action is falsy and prints no line", Report{}, "")
	R("F5 a non-ascii diagnosis",
		Report{Diagnosis: "café → naïve", Evidence: []string{"каф"}}, nil)
	R("F6 a mode that is neither text nor json falls through to TEXT",
		Report{Evidence: []string{"x"}}, "y")
	R("F7 a newline inside an evidence line",
		Report{Evidence: []string{"a\nb"}}, nil)

	// --- SystemHealth.format -----------------------------------------------
	H := func(name, status string, checks []kv) {
		o := pyval.Obj{}
		for _, c := range checks {
			o.Set(c.K, c.V)
		}
		h := Health{Status: status, Checks: o,
			CheckedAt: "2026-08-26T00:00:00+00:00"}
		pairs := [][]any{}
		for _, c := range checks {
			pairs = append(pairs, []any{c.K, c.V})
		}
		add(name, map[string]any{"kind": "health_format", "status": status,
			"checks": pairs, "checked_at": h.CheckedAt},
			func(string) any {
				text, _ := h.Format("text")
				js, _ := h.Format("json")
				return map[string]any{"text": text, "json": js}
			})
	}
	H("H1 checks render in insertion order", "degraded",
		[]kv{{"z", "ok"}, {"a", "warn: x"}, {"m", "fail: y"}})
	H("H2 no checks at all", "healthy", nil)

	// --- the checks -> status rollup ---------------------------------------
	K := func(name string, checks []kv) {
		o := pyval.Obj{}
		for _, c := range checks {
			o.Set(c.K, c.V)
		}
		pairs := [][]any{}
		for _, c := range checks {
			pairs = append(pairs, []any{c.K, c.V})
		}
		add(name, map[string]any{"kind": "rollup", "checks": pairs},
			func(string) any { return RollupStatus(o) })
	}
	K("K1 all ok", []kv{{"a", "ok"}, {"b", "ok: 5MB"}})
	K("K2 one warn", []kv{{"a", "ok"}, {"b", "warn: x"}})
	K("K3 one fail beats every warn", []kv{{"a", "warn: x"}, {"b", "fail: y"}})
	K("K4 no checks at all is HEALTHY", nil)
	K("K5 startswith, so 'failure' counts as fail", []kv{{"a", "failure"}})
	K("K6 'warning' counts as warn", []kv{{"a", "warning"}})
	K("K7 case matters: FAIL does not", []kv{{"a", "FAIL: x"}})
	K("K8 a check whose value is neither", []kv{{"a", "unknown"}})
	// startswith, not "in": a check that MENTIONS a failure is not one.
	K("K9 'fail' in the middle does not count", []kv{{"a", "ok: 0 failures"}})
	K("K10 'warn' in the middle does not count", []kv{{"a", "ok: no warnings"}})

	// --- detect_no_progress -------------------------------------------------
	N := func(name string, fps []string) {
		if fps == nil {
			fps = []string{}
		}
		add(name, map[string]any{"kind": "no_progress", "fps": fps},
			func(string) any { return DetectNoProgress(fps) })
	}
	N("N1 empty", nil)
	N("N2 one", []string{"A"})
	N("N3 two identical are below the threshold", []string{"A", "A"})
	N("N4 three identical", []string{"A", "A", "A"})
	N("N5 four, last three identical", []string{"B", "A", "A", "A"})
	N("N6 four, last three NOT identical", []string{"A", "A", "A", "B"})
	N("N7 three empty strings do NOT count", []string{"", "", ""})
	N("N8 three identical after two empties", []string{"", "", "A", "A", "A"})
	N("N9 A B B is not stuck", []string{"A", "B", "B"})
	N("N10 the window is exactly the last three",
		[]string{"A", "A", "A", "B", "C", "D"})
	// The MIDDLE of the window has to be compared too. A scan that only
	// checked the last against the first answers True here.
	N("N11 the middle of the window differs", []string{"X", "A", "B", "A"})

	// --- fingerprint_project_state ------------------------------------------
	P := func(name, slug string, files odict) {
		cs = append(cs, shCase{name: name,
			spec: map[string]any{"kind": "fingerprint", "slug": slug,
				"files": files},
			build: func(t *testing.T, ws string) { shMkProj(t, ws, slug, files) },
			run:   func(ws string) any { return FingerprintProjectState(ws, slug) },
		})
	}
	big := strings.Repeat("x", 2500) + "TAIL"
	P("P1 no files at all", "fp-none", odict{})
	P("P2 NEXT.md only", "fp-next", odict{{"NEXT.md", "hello\n"}})
	P("P3 DECISIONS.md only", "fp-dec", odict{{"DECISIONS.md", "d\n"}})
	P("P4 both, joined with a newline", "fp-both",
		odict{{"NEXT.md", "hello\n"}, {"DECISIONS.md", "d\n"}})
	P("P5 DECISIONS.md longer than 2000 chars is TAIL-sliced", "fp-big",
		odict{{"DECISIONS.md", big}})
	P("P6 a non-ascii DECISIONS.md — code points, not bytes", "fp-uni",
		odict{{"DECISIONS.md", strings.Repeat("é", 2500)}})
	P("P7 an empty NEXT.md still contributes an empty part", "fp-empty",
		odict{{"NEXT.md", ""}, {"DECISIONS.md", ""}})
	// exists() is True and read_text RAISES: the blanket except answers "",
	// which is a different answer from skipping the part. Nothing tested
	// that lane until the battery walked a `continue` through it (M32).
	P("P8 a DECISIONS.md that is a DIRECTORY is unreadable, not absent",
		"fp-dir", odict{{"NEXT.md", "hello\n"}, {"DECISIONS.md", nil}})
	P("P9 a NEXT.md that is a DIRECTORY is unreadable too", "fp-dir2",
		odict{{"NEXT.md", nil}})

	// --- project_lifecycle_state --------------------------------------------
	L := func(name, slug string, files odict) {
		cs = append(cs, shCase{name: name,
			spec: map[string]any{"kind": "lifecycle", "slug": slug,
				"files": files},
			build: func(t *testing.T, ws string) { shMkProj(t, ws, slug, files) },
			run:   func(ws string) any { return ProjectLifecycleState(ws, slug) },
		})
	}
	L("L1 no markers", "lc-none", odict{})
	L("L2 failed", "lc-f", odict{{".maro-failed", ""}})
	L("L3 paused", "lc-p", odict{{".maro-paused", ""}})
	L("L4 both — failed wins", "lc-b",
		odict{{".maro-failed", ""}, {".maro-paused", ""}})
	L("L5 a marker that is a DIRECTORY still counts", "lc-d",
		odict{{".maro-failed", nil}})

	// --- the two float spellings ---------------------------------------------
	for _, v := range []float64{0.0, 0.4, 0.5, 1.5, 2.5, 2.4999, 14.0, 30.0,
		61.0, 1e21, 0.05} {
		v := v
		add(fmt.Sprintf("V %s", pyval.Repr(v)),
			map[string]any{"kind": "fmt", "v": v},
			func(string) any {
				return map[string]any{
					"f0": pyval.PercentF(v, 0), "g": pyval.FormatG(v),
					"fmt0": pyval.PercentF(v, 0), "fmtg": pyval.FormatG(v),
				}
			})
	}

	// --- repr of a 60-code-point slice ---------------------------------------
	for _, s := range []string{"short", strings.Repeat("x", 100),
		"it's a quote", "a\\b", strings.Repeat("café", 30), "line\nbreak",
		strings.Repeat("к", 80)} {
		s := s
		add(fmt.Sprintf("Q %s", pyval.Repr(pyval.Clip(s, 20))),
			map[string]any{"kind": "repr60", "s": s},
			func(string) any { return pyval.Repr(pyval.Clip(s, 60)) })
	}

	// --- the list-repr embedded in evidence ----------------------------------
	for _, items := range [][]string{{}, {"a"}, {"a", "b"}, {"a", "b", "c"},
		{"a", "b", "c", "d"}, {"it's"}, {"café"}, {"a\nb"}} {
		items := items
		add(fmt.Sprintf("LR %s", pyval.ReprStrings(items)),
			map[string]any{"kind": "listrepr", "items": items},
			func(string) any { return pyval.ReprStrings(firstN(items, 3)) })
	}

	// --- check_project --------------------------------------------------------
	const (
		nextHealthy = "# NEXT\n\n- [ ] todo one\n- [ ] todo two\n"
		nextDoing   = "# NEXT\n\n- [~] doing one\n- [ ] todo one\n"
		nextDone    = "# NEXT\n\n- [x] finished\n"
		nextBlocked = "# NEXT\n\n- [!] blocked one\n- [ ] todo one\n"
	)
	repeated := strings.Repeat("the same line\n", 5)

	CP := func(name, slug string, files odict, opts ...func(map[string]any)) {
		spec := map[string]any{"kind": "check_project", "slug": slug,
			"files": files}
		for _, o := range opts {
			o(spec)
		}
		var mtimes odict
		if m, ok := spec["mtimes"]; ok {
			mtimes = m.(odict)
		}
		nocreate, _ := spec["nocreate"].(bool)
		window := 30
		if w, ok := spec["window"]; ok {
			window = w.(int)
		}
		dormant := DormantDaysDefault
		if d, ok := spec["dormant_days"]; ok {
			dormant = d.(float64)
		}
		cs = append(cs, shCase{name: name, spec: spec,
			build: func(t *testing.T, ws string) {
				d := orch.ProjectDir(ws, slug)
				if !nocreate {
					d = shMkProj(t, ws, slug, files)
				}
				shUtimes(t, d, mtimes)
			},
			run: func(ws string) any {
				return cpOut(CheckProject(ws, slug, window, shNow(spec), dormant))
			},
		})
	}
	nocreate := func(s map[string]any) { s["nocreate"] = true }
	mt := func(m odict) func(map[string]any) {
		return func(s map[string]any) { s["mtimes"] = m }
	}
	at := func(now float64) func(map[string]any) {
		return func(s map[string]any) { s["now"] = now }
	}
	win := func(n int) func(map[string]any) {
		return func(s map[string]any) { s["window"] = n }
	}
	dd := func(n float64) func(map[string]any) {
		return func(s map[string]any) { s["dormant_days"] = n }
	}

	CP("C1 a project dir that EXISTS but has no NEXT.md", "cp-missing", odict{})
	CP("C1b a project directory that genuinely does not exist", "cp-absent",
		odict{}, nocreate)
	CP("C2 healthy with todos", "cp-healthy", odict{{"NEXT.md", nextHealthy}})
	CP("C3 an item stuck in doing is STUCK", "cp-doing",
		odict{{"NEXT.md", nextDoing}})
	CP("C4 no todo and no doing reads as complete", "cp-done",
		odict{{"NEXT.md", nextDone}})
	CP("C5 blocked items are evidence but not a problem", "cp-blocked",
		odict{{"NEXT.md", nextBlocked}})
	CP("C6 repeated DECISIONS lines are STUCK", "cp-rep",
		odict{{"NEXT.md", nextHealthy}, {"DECISIONS.md", repeated}})
	CP("C7 a marker short-circuits every other check", "cp-failed",
		odict{{"NEXT.md", nextDoing}, {".maro-failed", ""}})
	CP("C8 paused short-circuits too", "cp-paused",
		odict{{"NEXT.md", nextDoing}, {".maro-paused", ""}})
	CP("C9 an empty artifacts dir with items doing is a WARNING", "cp-noart",
		odict{{"NEXT.md", nextDoing}, {"artifacts", nil}})
	CP("C10 an empty artifacts dir with NO doing items is fine", "cp-noart2",
		odict{{"NEXT.md", nextHealthy}, {"artifacts", nil}})
	CP("C11 a FUTURE artifact mtime gives a negative age", "cp-fresh",
		odict{{"NEXT.md", nextDoing}, {"artifacts/a.txt", "x"}},
		mt(odict{{"artifacts/a.txt", 4102444800.0}}), at(1756003600.0))
	CP("C12 more than three doing items truncates the list at three", "cp-many",
		odict{{"NEXT.md", "# NEXT\n\n" + doingLines(5)}})
	CP("C13 a non-ascii item text in the evidence list repr", "cp-uni",
		odict{{"NEXT.md", "# NEXT\n\n- [~] café → naïve\n"}})
	CP("C14 DECISIONS with exactly three repeats hits the threshold", "cp-three",
		odict{{"NEXT.md", nextHealthy}, {"DECISIONS.md", "a\nb\na\nc\na\n"}})
	CP("C15 DECISIONS with only two repeats does NOT", "cp-two",
		odict{{"NEXT.md", nextHealthy}, {"DECISIONS.md", "a\nb\na\nc\n"}})
	CP("C16 blank lines are dropped before the window", "cp-blank",
		odict{{"NEXT.md", nextHealthy}, {"DECISIONS.md", "a\n\na\n\na\n"}})
	CP("C17 the window is the last twenty NON-BLANK lines", "cp-window",
		odict{{"NEXT.md", nextHealthy}, {"DECISIONS.md", windowDecisions()}})
	CP("C18 no NEXT.md at all", "cp-nonext", odict{{"README.md", "x"}})
	CP("C19 a stale artifact with items doing is a WARNING", "cp-stale",
		odict{{"NEXT.md", nextDoing}, {"artifacts/old.txt", "x"}},
		mt(odict{{"artifacts/old.txt", 1756000000.0}}), at(1756003600.0))
	CP("C20 the same stale artifact with a wide window is not", "cp-stale2",
		odict{{"NEXT.md", nextDoing}, {"artifacts/old.txt", "x"}},
		mt(odict{{"artifacts/old.txt", 1756000000.0}}), at(1756003600.0), win(120))
	CP("C21 newest-by-mtime wins, not newest-by-name", "cp-order",
		odict{{"NEXT.md", nextDoing}, {"artifacts/aaa.txt", "x"},
			{"artifacts/zzz.txt", "y"}},
		mt(odict{{"artifacts/aaa.txt", 1756003000.0},
			{"artifacts/zzz.txt", 1756000000.0}}), at(1756003600.0))
	CP("C22 an old project dir is DORMANT, whatever its NEXT.md says", "cp-dorm",
		odict{{"NEXT.md", nextDoing}},
		mt(odict{{".", 1700000000.0}}), at(1756003600.0))
	CP("C23 dormancy is checked BEFORE the doing items", "cp-dorm2",
		odict{{"NEXT.md", nextDoing}, {"DECISIONS.md", repeated}},
		mt(odict{{".", 1700000000.0}}), at(1756003600.0))
	CP("C25 a project whose FILES are all old is DORMANT", "cp-dorm4",
		odict{{"NEXT.md", nextDoing}},
		mt(odict{{"NEXT.md", 1700000000.0}, {".", 1700000000.0}}),
		at(1756003600.0))
	CP("C26 dormancy short-circuits the repeated-decisions check too", "cp-dorm5",
		odict{{"NEXT.md", nextDoing}, {"DECISIONS.md", repeated}},
		mt(odict{{"NEXT.md", 1700000000.0}, {"DECISIONS.md", 1700000000.0},
			{".", 1700000000.0}}), at(1756003600.0))
	CP("C27 an artifacts entry keeps a project alive", "cp-dorm6",
		odict{{"NEXT.md", nextDoing}, {"artifacts/fresh.txt", "x"}},
		mt(odict{{"NEXT.md", 1700000000.0}, {"artifacts/fresh.txt", 1756003000.0},
			{"artifacts", 1700000000.0}, {".", 1700000000.0}}),
		at(1756003600.0))
	// 60 artifacts, all old except the one at sorted index 49 — the LAST
	// entry the [:50] cap admits. A cap of 49 excludes it and the project
	// reads dormant instead of stuck, so this is the fixture that makes the
	// bound testable; without it the cap was the one mutant of four that
	// walked through the differential untouched.
	capFiles := odict{{"NEXT.md", nextDoing}}
	capMT := odict{}
	for i := 0; i < 60; i++ {
		name := fmt.Sprintf("artifacts/a%02d.txt", i)
		capFiles = append(capFiles, kv{name, "x"})
		if i == 49 {
			capMT = append(capMT, kv{name, 1756000000.0})
		} else {
			capMT = append(capMT, kv{name, 1700000000.0})
		}
	}
	capMT = append(capMT, kv{"artifacts", 1700000000.0},
		kv{"NEXT.md", 1700000000.0}, kv{".", 1700000000.0})
	CP("C32 a fresh project DIRECTORY keeps an all-old project alive", "cp-dirmt",
		odict{{"NEXT.md", nextHealthy}},
		mt(odict{{"NEXT.md", 1700000000.0}, {".", 1756003600.0}}),
		at(1756003600.0))
	CP("C33 an epoch-zero mtime reads as UNKNOWN age, not as ancient", "cp-epoch",
		odict{{"NEXT.md", nextHealthy}},
		mt(odict{{"NEXT.md", 0.0}, {".", 0.0}}), at(1756003600.0))
	CP("C34 exactly the dormancy threshold is NOT dormant", "cp-exact",
		odict{{"NEXT.md", nextHealthy}},
		mt(odict{{"NEXT.md", 1756003600.0 - 14*86400},
			{".", 1756003600.0 - 14*86400}}), at(1756003600.0))
	CP("C35 a fractional threshold renders through :g, not %.0f", "cp-frac",
		odict{{"NEXT.md", nextHealthy}},
		mt(odict{{"NEXT.md", 1700000000.0}, {".", 1700000000.0}}),
		at(1756003600.0), dd(7.5))
	CP("C36 blank lines change WHICH twenty lines the window holds", "cp-blankwin",
		odict{{"NEXT.md", nextHealthy},
			{"DECISIONS.md", "z\nz\nz\n" + strings.Repeat("\n", 20)}})
	CP("C37 a repeated line longer than sixty code points is clipped", "cp-long",
		odict{{"NEXT.md", nextHealthy},
			{"DECISIONS.md", strings.Repeat(strings.Repeat("y", 80)+"\n", 3)}})
	CP("C38 a stale artifact with NO doing items is not a stall",
		"cp-stale-nodoing",
		odict{{"NEXT.md", nextHealthy}, {"artifacts/old.txt", "x"}},
		mt(odict{{"artifacts/old.txt", 1756000000.0}}), at(1756003600.0))
	CP("C39 an artifact exactly at the window is not yet stale", "cp-exactwin",
		odict{{"NEXT.md", nextDoing}, {"artifacts/x.txt", "x"}},
		mt(odict{{"artifacts/x.txt", 1756003600.0 - 30*60}}), at(1756003600.0))
	CP("C29 two repeated patterns report the FIRST-SEEN one, not the sorted one",
		"cp-two-pat", odict{{"NEXT.md", nextHealthy},
			{"DECISIONS.md", "z\nz\nz\na\na\na\n"}})
	CP("C30 equal mtimes keep GLOB order, because the sort is stable", "cp-tie",
		odict{{"NEXT.md", nextDoing}, {"artifacts/aaa.txt", "x"},
			{"artifacts/bbb.txt", "y"}},
		mt(odict{{"artifacts/aaa.txt", 1756000000.0},
			{"artifacts/bbb.txt", 1756000000.0}}), at(1756003600.0))
	CP("C31 dormant_days=0 disables the check, so an ancient project is STUCK",
		"cp-dorm-off", odict{{"NEXT.md", nextDoing}},
		mt(odict{{"NEXT.md", 1700000000.0}, {".", 1700000000.0}}),
		at(1756003600.0), dd(0))
	// Twenty artifacts tied at the newest mtime plus one older: the
	// tie-break is the ONLY thing that decides the answer. Twenty and not
	// two because Go's pdqsort delegates to insertion sort below thirteen
	// elements and is therefore accidentally stable at small sizes — the
	// threshold REVIEW_PATTERNS' P11 records. CPython answers t02.txt here,
	// which is neither the first nor the last by name: it is readdir order.
	tieFiles := odict{{"NEXT.md", nextDoing}}
	tieMT := odict{}
	for i := 0; i < 20; i++ {
		name := fmt.Sprintf("artifacts/t%02d.txt", i)
		tieFiles = append(tieFiles, kv{name, "x"})
		tieMT = append(tieMT, kv{name, 1756000000.0})
	}
	tieFiles = append(tieFiles, kv{"artifacts/older.txt", "x"})
	tieMT = append(tieMT, kv{"artifacts/older.txt", 1750000000.0})
	CP("C40 twenty artifacts tied at the newest mtime — the tie-break IS the answer",
		"cp-bigtie", tieFiles, mt(tieMT), at(1756003600.0))
	CP("C28 the activity scan admits exactly the first FIFTY artifacts entries",
		"cp-cap", capFiles, mt(capMT), at(1756003600.0))
	CP("C24 just inside the dormancy window is NOT dormant", "cp-dorm3",
		odict{{"NEXT.md", nextHealthy}},
		mt(odict{{".", 1756003600.0 - 13.5*86400}}), at(1756003600.0))

	// --- _dormant_days ------------------------------------------------------
	D := func(name string, spec map[string]any, cfg func() (any, error)) {
		add(name, spec, func(string) any { return ResolveDormantDays(cfg) })
	}
	val := func(v any) func() (any, error) {
		return func() (any, error) { return v, nil }
	}
	D("D0 an unset key takes the 14-day default",
		map[string]any{"kind": "dormant_days", "missing": true},
		val(DormantDaysDefault))
	for _, d := range []struct {
		name string
		spec any
		cfg  func() (any, error)
	}{
		{"D1 a plain number", 7, val(7)},
		{"D2 a float", 7.5, val(7.5)},
		{"D3 a numeric string", "7", val("7")},
		{"D4 a string with surrounding space", "  7  ", val("  7  ")},
		{"D5 an exponent string", "1e3", val("1e3")},
		{"D6 ZERO disables the check", 0, val(0)},
		{"D7 an EMPTY string is falsy, so it also disables", "", val("")},
		{"D8 None is falsy", nil, val(nil)},
		{"D9 False is falsy", false, val(false)},
		{"D10 True is 1.0, not the default", true, val(true)},
		{"D11 a negative value survives", -3, val(-3)},
		{"D12 an unparseable string falls back to the DEFAULT, not to 0",
			"abc", val("abc")},
		{"D13 a list is truthy and then unparseable", []any{1},
			val(pyval.List{1})},
		{"D14 an empty list is falsy first", []any{}, val(pyval.List{})},
		// pyval.Float folds non-ASCII decimal digits, so unlike the int
		// lane in heartbeat's cadence resolvers this one is NOT a named
		// divergence: CPython's float("\u0667") is 7.0 and so is Go's.
		{"D15 a non-ascii digit string", "\u0667", val("\u0667")},
	} {
		D(d.name, map[string]any{"kind": "dormant_days", "v": d.spec}, d.cfg)
	}

	// --- md5 / slicing primitives -------------------------------------------
	for _, s := range []string{"", "a", "café", "каф", "a\nb"} {
		s := s
		add(fmt.Sprintf("M %s", pyval.Repr(s)),
			map[string]any{"kind": "md5", "s": s},
			func(string) any {
				sum := md5.Sum([]byte(s))
				return hex.EncodeToString(sum[:])
			})
	}
	for _, s := range []string{"short", strings.Repeat("x", 2500),
		strings.Repeat("é", 2500)} {
		s := s
		add(fmt.Sprintf("S2000 len=%d", utf8.RuneCountInString(s)),
			map[string]any{"kind": "slice2000", "s": s},
			func(string) any { return clipTail(s, 2000) })
	}
	// --- project_activity_age_days over undecodable artifact names -------
	//
	// `sorted(artifacts.iterdir())[:50]` orders Path objects, which compare
	// by the surrogateescape DECODING; sort.Strings compares raw bytes. The
	// two agree for all valid UTF-8, which is why this sat in shipped code
	// unnoticed. They part in the two-byte range -- and because the list is
	// TRUNCATED at fifty, the two runtimes do not merely order the same
	// files differently, they STAT A DIFFERENT FIFTY.
	//
	// The tree is built so that difference is the whole answer: fifty
	// "é_NN" files (0xC3 0xA9, ancient) and ten "\x80_NN" files, one of
	// them RECENT. Byte order puts 0x80 first, so a byte-sorting port takes
	// the ten escapes plus forty é files and finds the recent one --
	// "active yesterday". Code-point order puts U+00E9 (233) before U+DC80
	// (56448), so CPython takes the fifty é files and never sees it --
	// "untouched for a hundred days", which is what decides dormancy.
	{
		const day = 86400.0
		const now = 1787000000.0
		var names []any
		var mtimes []any
		for i := 0; i < 50; i++ {
			nm := append([]any{0xC3, 0xA9, int('_')}, digitVals(i)...)
			names = append(names, nm)
			mtimes = append(mtimes, now-100.5*day)
		}
		for i := 0; i < 10; i++ {
			nm := append([]any{0x80, int('_')}, digitVals(i)...)
			names = append(names, nm)
			age := 100.5 * day
			if i == 9 {
				age = 1.5 * day // the one only a byte sort can reach
			}
			mtimes = append(mtimes, now-age)
		}
		const slug = "fsage"
		add("A50 the fifty artifacts sorted are chosen by CODE POINT",
			map[string]any{"kind": "activity_fsnames", "slug": slug,
				"names": names, "mtimes": mtimes,
				"dir_mtime": now - 100.5*day, "now": now},
			func(ws string) any {
				d := filepath.Join(ws, "projects", slug, "artifacts")
				if err := os.MkdirAll(d, 0o777); err != nil {
					panic(err)
				}
				for i, nm := range names {
					b := make([]byte, 0, len(nm.([]any)))
					for _, v := range nm.([]any) {
						b = append(b, byte(v.(int)))
					}
					fp := d + "/" + string(b)
					if err := os.WriteFile(fp, []byte("x"), 0o666); err != nil {
						panic(err)
					}
					mt := time.Unix(0, int64(mtimes[i].(float64)*1e9))
					if err := os.Chtimes(fp, mt, mt); err != nil {
						panic(err)
					}
				}
				// artifacts/ and the project dir are candidates in their
				// own right; sixty creates leave artifacts/ stamped NOW.
				dm := time.Unix(0, int64((now-100.5*day)*1e9))
				for _, p := range []string{d, filepath.Join(ws, "projects", slug)} {
					if err := os.Chtimes(p, dm, dm); err != nil {
						panic(err)
					}
				}
				age, ok := ProjectActivityAgeDays(ws, slug,
					time.Unix(0, int64(now*1e9)))
				if !ok {
					return nil
				}
				return int(age)
			})
	}

	// --- slice 2: check_all_projects -------------------------------------
	//
	// Each of these gets its OWN workspace. The rest of the file shares one
	// (each case builds its own project and reads it back), but an
	// ENUMERATION fixture is a function of everything in projects/, so a
	// shared workspace would make the answer depend on which cases ran
	// first. The probe clears its projects root for the same reason, behind
	// an assert that the resolved path is not the live one.
	AP := func(name string, spec map[string]any, window int) {
		var myWS string
		var root string
		unreadable, _ := spec["unreadable"].(bool)
		cs = append(cs, shCase{name: name, spec: spec,
			build: func(t *testing.T, _ string) {
				myWS = t.TempDir()
				root = orch.ProjectsRoot(myWS)
				if nocreate, _ := spec["nocreate"].(bool); nocreate {
					return
				}
				if err := os.MkdirAll(root, 0o777); err != nil {
					t.Fatal(err)
				}
				for _, d := range asVals(spec["dirs"]) {
					p := filepath.Join(root, byteName(d))
					mustMkdir(t, filepath.Join(p, "artifacts"))
					mustWrite(t, filepath.Join(p, "NEXT.md"), "# NEXT\n\n- [ ] todo one\n")
					mustWrite(t, filepath.Join(p, "artifacts", "a.txt"), "x")
				}
				for _, f := range asVals(spec["plainfiles"]) {
					mustWrite(t, filepath.Join(root, byteName(f)), "not a project")
				}
				for _, l := range asVals(spec["links"]) {
					pair := l.([]any)
					if err := os.Symlink(pair[1].(string),
						filepath.Join(root, byteName(pair[0]))); err != nil {
						t.Fatal(err)
					}
				}
			},
			run: func(_ string) any {
				if unreadable {
					if err := os.Chmod(root, 0o000); err != nil {
						panic(err)
					}
					defer os.Chmod(root, 0o755)
				}
				reps := CheckAllProjects(myWS, window, time.Now(), DormantDaysDefault)
				out := []any{}
				for _, r := range reps {
					out = append(out, []any{byteVals(r.Project), r.Status,
						clipTail3(r.Diagnosis, 30)})
				}
				return out
			},
		})
	}
	{
		// The ordering case, and the only one that can tell a byte sort from
		// a code-point sort. Byte order puts \x80 first; CPython's sorted()
		// puts it LAST, because surrogateescape lifts it to U+DC80 (56448)
		// while "é" is U+00E9 (233). Same class as A50 and the five shipped
		// sites the fssort guard now covers.
		dirs := []any{
			[]any{0x7A, 0x7A},             // "zz"
			[]any{0xC3, 0xA9, 0x61},       // "éa"
			[]any{0x80, 0x62},             // an undecodable lead byte
			[]any{0x61, 0x61},             // "aa"
			[]any{0xE3, 0x81, 0x82, 0x63}, // "あc" — three bytes, agrees either way
		}
		AP("A60 project slugs sort by CODE POINT, not by byte",
			map[string]any{"kind": "all_projects", "dirs": dirs}, 30)
	}
	{
		// What is NOT a project. A symlink TO a directory is one, because
		// is_dir() follows; a dangling one is not, because it answers False
		// rather than raising.
		AP("A61 dotted, underscored, plain files and links",
			map[string]any{"kind": "all_projects",
				"dirs": []any{
					[]any{0x6F, 0x6B}, // "ok"
					[]any{0x5F, 0x61}, // "_a" — the archive-shaped skip
					[]any{0x2E, 0x62}, // ".b" — hidden
				},
				"plainfiles": []any{[]any{0x66, 0x2E, 0x6D, 0x64}}, // "f.md"
				"links": []any{
					[]any{[]any{0x6C, 0x64}, "ok"},      // "ld" -> a real project
					[]any{[]any{0x6C, 0x78}, "nowhere"}, // "lx" -> dangling
				}}, 30)
	}
	AP("A62 a MISSING projects dir is an empty list, not the star report",
		map[string]any{"kind": "all_projects", "nocreate": true,
			"dirs": []any{}}, 30)
	AP("A62b an EMPTY projects dir is also an empty list",
		map[string]any{"kind": "all_projects", "dirs": []any{}}, 30)
	AP("A63 an unreadable projects dir is the star report",
		map[string]any{"kind": "all_projects", "unreadable": true,
			"dirs": []any{[]any{0x6F, 0x6B}}}, 30)

	// --- slice 2: archive_dormant_projects --------------------------------
	AR := func(name string, spec map[string]any) {
		var myWS string
		days := spec["days"].(float64)
		apply := spec["apply"].(bool)
		cs = append(cs, shCase{name: name, spec: spec,
			build: func(t *testing.T, _ string) {
				myWS = t.TempDir()
				root := orch.ProjectsRoot(myWS)
				mustMkdir(t, root)
				for _, p := range asVals(spec["projects"]) {
					pair := p.([]any)
					slug, mt := specName(pair[0]), pair[1].(float64)
					d := filepath.Join(root, slug)
					mustMkdir(t, filepath.Join(d, "artifacts"))
					mustWrite(t, filepath.Join(d, "NEXT.md"), "# NEXT\n")
					ts := time.Unix(int64(mt), int64((mt-float64(int64(mt)))*1e9))
					// Same order the probe uses: the file, then artifacts/,
					// then the project dir. Any other order re-stamps a
					// parent and un-ages the fixture.
					for _, q := range []string{filepath.Join(d, "NEXT.md"),
						filepath.Join(d, "artifacts"), d} {
						if err := os.Chtimes(q, ts, ts); err != nil {
							t.Fatal(err)
						}
					}
				}
				for _, e := range asVals(spec["premade"]) {
					mustMkdir(t, filepath.Join(root, "_archive", e.(string)))
				}
			},
			run: func(_ string) any {
				now := shNow(spec)
				entries, err := ArchiveDormantProjects(myWS, days, apply, now)
				if err != nil {
					return map[string]any{"err": err.Error()}
				}
				root := orch.ProjectsRoot(myWS)
				items := pyval.List{}
				for _, e := range entries {
					cp := pyval.Obj{}
					for _, f := range e {
						if f.Key == "target" {
							rel, _ := filepath.Rel(root, f.Val.(string))
							cp.Set("target", rel)
							continue
						}
						cp.Set(f.Key, f.Val)
					}
					items = append(items, cp)
				}
				order := []any{}
				for _, e := range entries {
					for _, f := range e {
						if f.Key == "project" {
							order = append(order, byteVals(f.Val.(string)))
						}
					}
				}
				var text any
				if notext, _ := spec["notext"].(bool); !notext {
					t, derr := pyval.DumpsCompactPy(items)
					if derr != nil {
						panic(derr)
					}
					text = t
				}
				res := map[string]any{"entries": text, "order": order,
					"root": listDir(root), "archive": nil, "mode": nil}
				adir := filepath.Join(root, "_archive")
				if st, serr := os.Stat(adir); serr == nil {
					res["archive"] = listDir(adir)
					res["mode"] = fmt.Sprintf("%o", st.Mode().Perm())
				}
				return res
			},
		})
	}
	{
		const now = 1787000000.0
		const day = 86400.0
		AR("A70 dry run: over, exactly at, and under the threshold",
			map[string]any{"kind": "archive", "now": now, "days": 30.0,
				"apply": false, "projects": []any{
					[]any{"old", now - 40*day},
					// EXACTLY at the threshold. `age <= days` skips it, and
					// the fixture exists because `<` is the natural mistake.
					[]any{"edge", now - 30*day},
					[]any{"fresh", now - day},
				}})
		// The skip in the archive sweep needs a dir that WOULD be archived
		// without it. `_archive` itself does not qualify: it is created by
		// the sweep, so its mtime is always now and it fails the age test
		// anyway -- which is why dropping the skip survived the first cut
		// of this battery.
		AR("A70b dotted and underscored dirs are skipped even when ancient",
			map[string]any{"kind": "archive", "now": now, "days": 30.0,
				"apply": false, "projects": []any{
					[]any{"real", now - 40*day},
					[]any{"_old", now - 40*day},
					[]any{".old", now - 40*day},
				}})
		AR("A71 apply moves it and leaves the rest alone",
			map[string]any{"kind": "archive", "now": now, "days": 30.0,
				"apply": true, "projects": []any{
					[]any{"old", now - 40*day},
					[]any{"fresh", now - day},
				}})
		// `int(time.time())` TRUNCATES toward zero. With a whole-second
		// clock a rounding port gives the same suffix, so this fixture's
		// clock carries a fraction -- without it the mutation survives.
		AR("A72 apply onto an existing target takes the timestamp suffix",
			map[string]any{"kind": "archive", "now": now + 0.7, "days": 30.0,
				"apply": true, "premade": []any{"old"},
				"projects": []any{[]any{"old", now - 40*day}}})
		// `age is None or age <= days`. The None arm is INVISIBLE at any
		// positive threshold -- a port that read absence as zero skips the
		// project either way -- so it takes a NEGATIVE days to separate
		// them, which the CLI can pass. The epoch-0 project reports an
		// unknown age (project_activity_age_days returns None when the
		// newest mtime is <= 0), and must still be skipped where the
		// ordinary one is archived.
		AR("A75 an UNKNOWN age is skipped even below a negative threshold",
			map[string]any{"kind": "archive", "now": now, "days": -1.0,
				"apply": false, "projects": []any{
					[]any{"epoch", 0.0},
					[]any{"normal", now - day},
				}})
		// round(age, 1) is half-to-EVEN on the BINARY double. 30.25 days
		// rounds to 30.2, not 30.3, and a port using math.Round answers
		// 30.3 — the entry is the function's whole output.
		// The archive sweep's own `sorted(projects_dir.iterdir())`. It is a
		// SECOND filename sort in the same file and the entry list's order
		// is its whole observable; the ASCII fixtures above cannot tell a
		// byte sort from a code-point one, which is how this class hid in
		// five shipped sites.
		//
		// `notext` elides the serialised entry list, and the reason is a
		// KNOWN residual rather than a transport limit. I expected
		// json.dumps to REFUSE a surrogateescaped slug; it does not. With
		// ensure_ascii=True — the default — it happily writes
		// `"\udc80b"`, a pure-ASCII file holding an escape no valid
		// string can decode to, while pyval refuses byte-tainted text
		// outright. That is the lone-surrogate item already in BACKLOG
		// ("an escaped lone surrogate is a STATE divergence, not a byte
		// one"), reached from a new direction: closing it needs a
		// surrogate-preserving decoder AND an encodeString that re-emits
		// `\udXXX`, which is not this slice's job. Eliding it here is the
		// honest move; comparing it would fail for a reason that has
		// nothing to do with archive_dormant_projects.
		AR("A74 the archive sweep sorts by code point too",
			map[string]any{"kind": "archive", "now": now, "days": 30.0,
				"apply": false, "notext": true, "projects": []any{
					[]any{[]any{0x7A, 0x7A}, now - 40*day},       // "zz"
					[]any{[]any{0xC3, 0xA9, 0x61}, now - 41*day}, // "éa"
					[]any{[]any{0x80, 0x62}, now - 42*day},       // undecodable
				}})
		AR("A73 age_days is Python's round, not half-away-from-zero",
			map[string]any{"kind": "archive", "now": now, "days": 30.0,
				"apply": false, "projects": []any{
					[]any{"quarter", now - 30.25*day},
					[]any{"threeeighths", now - 30.35*day},
				}})
	}

	// --- slice 2: the heartbeat state file --------------------------------
	HB := func(name string, spec map[string]any) {
		var myWS string
		cs = append(cs, shCase{name: name, spec: spec,
			build: func(t *testing.T, _ string) { myWS = t.TempDir() },
			run: func(_ string) any {
				checks := pyval.Obj{}
				for _, kv := range asVals(spec["checks"]) {
					pair := kv.([]any)
					checks.Set(pair[0].(string), pair[1])
				}
				h := Health{Status: spec["status"].(string), Checks: checks,
					CheckedAt: spec["checked_at"].(string)}
				var reps []Report
				if spec["with_reports"].(bool) {
					reps = []Report{}
					for _, r := range asVals(spec["reports"]) {
						pair := r.([]any)
						reps = append(reps, Report{Project: pair[0].(string),
							Status: pair[1].(string), Evidence: []string{}})
					}
				}
				path := WriteHeartbeatState(myWS, h, reps)
				res := map[string]any{"wrote": path != "", "body": nil, "name": nil}
				if path != "" {
					res["name"] = filepath.Base(path)
					b, err := os.ReadFile(path)
					if err != nil {
						panic(err)
					}
					res["body"] = string(b)
				}
				return res
			},
		})
	}
	HB("A80 the state file's BYTES: indent 2, ensure_ascii, raw html",
		map[string]any{"kind": "heartbeat_write", "status": "degraded",
			"checked_at": "2026-08-26T00:00:00+00:00",
			"checks": []any{
				[]any{"workspace_writable", "ok"},
				[]any{"pkg_requests", "warn: requests not installed"},
				// The three characters encoding/json escapes and json.dumps
				// does not, plus a rune ensure_ascii escapes and it does not.
				[]any{"disk_space", "ok: 12MB free — a > b & c < d, café"},
			},
			"with_reports": true,
			"reports": []any{
				[]any{"a", "stuck"}, []any{"b", "healthy"},
				[]any{"c", "warning"}, []any{"d", "dormant"},
			}})
	HB("A81 no reports at all still writes an empty list, never null",
		map[string]any{"kind": "heartbeat_write", "status": "healthy",
			"checked_at": "2026-08-26T00:00:00+00:00",
			"checks":     []any{}, "with_reports": false, "reports": []any{}})

	HR := func(name string, body any) {
		var myWS string
		spec := map[string]any{"kind": "heartbeat_read", "body": body}
		cs = append(cs, shCase{name: name, spec: spec,
			build: func(t *testing.T, _ string) {
				myWS = t.TempDir()
				if body == nil {
					return
				}
				md := orch.MemoryDir(myWS)
				mustMkdir(t, md)
				mustWrite(t, filepath.Join(md, HeartbeatStateName), body.(string))
			},
			run: func(_ string) any {
				o, ok := ReadHeartbeatState(myWS)
				res := map[string]any{"kind": "absent", "text": nil}
				if ok {
					res["kind"] = "object"
					text, err := pyval.DumpsCompactPy(o)
					if err != nil {
						panic(err)
					}
					res["text"] = text
				}
				return res
			},
		})
	}
	HR("A82 a missing state file is absent", nil)
	HR("A83 a state file round-trips with its key ORDER intact",
		`{"checked_at": "t", "system_status": "healthy", "checks": {"b": 1, "a": 2}, "stuck_projects": []}`)
	HR("A84 invalid JSON is absent", "{not json")

	return cs
}

// asVals reads a spec entry that may be absent as a list.
func asVals(v any) []any {
	if v == nil {
		return nil
	}
	return v.([]any)
}

// byteName turns a list of byte VALUES into the Go string holding those
// raw bytes — the spelling a filename takes on Linux, and the one a Go
// string literal cannot express when the bytes are not valid UTF-8.
func byteName(v any) string {
	vals := v.([]any)
	b := make([]byte, 0, len(vals))
	for _, x := range vals {
		b = append(b, byte(x.(int)))
	}
	return string(b)
}

// byteVals is byteName's inverse, for handing a name back to the probe.
func byteVals(s string) []any {
	out := make([]any, 0, len(s))
	for i := 0; i < len(s); i++ {
		out = append(out, int(s[i]))
	}
	return out
}

// clipTail3 is Python's `s[:n]` — n CODE POINTS, not n bytes.
func clipTail3(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

// listDir is `sorted(os.listdir(d))` handed back as byte lists, because a
// directory entry is bytes and sorted() orders the surrogateescape
// decoding by code point.
func listDir(dir string) []any {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	names := make([]string, 0, len(entries))
	for _, e := range entries {
		names = append(names, e.Name())
	}
	sort.Slice(names, func(i, j int) bool { return pypath.FSLess(names[i], names[j]) })
	out := make([]any, 0, len(names))
	for _, n := range names {
		out = append(out, byteVals(n))
	}
	return out
}

// specName reads a slug that rides as a string when it is valid UTF-8 and
// as a list of byte values when it is not.
func specName(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return byteName(v)
}

func mustMkdir(t *testing.T, p string) {
	t.Helper()
	if err := os.MkdirAll(p, 0o777); err != nil {
		t.Fatal(err)
	}
}

func mustWrite(t *testing.T, p, text string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(p), 0o777); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(text), 0o666); err != nil {
		t.Fatal(err)
	}
}

// digitVals spells a two-digit index as byte values, so a name can be built
// out of raw bytes rather than out of a Go string.
func digitVals(i int) []any {
	return []any{int('0' + i/10), int('0' + i%10)}
}

func doingLines(n int) string {
	var b strings.Builder
	for i := 0; i < n; i++ {
		fmt.Fprintf(&b, "- [~] doing %d\n", i)
	}
	return b.String()
}

func windowDecisions() string {
	lines := []string{"a", "a", "a"}
	for i := 0; i < 20; i++ {
		lines = append(lines, fmt.Sprintf("b%d", i))
	}
	return strings.Join(lines, "\n") + "\n"
}

// cpOut renders a Report the way the probe renders SheriffReport: an absent
// action is JSON null, and an EMPTY one is "".
func cpOut(r Report) any {
	var action any
	if r.HasAction {
		action = r.Action
	}
	// r.Evidence goes in AS IS, not copied into a fresh []any. A nil slice
	// marshals to `null` and an empty one to `[]`, and the copy erased that
	// difference — so `evidence = []string{}` could be deleted from the port
	// and the differential stayed green (battery M76). The test's own
	// renderer was the guard that failed, not the port.
	return map[string]any{"status": r.Status, "diagnosis": r.Diagnosis,
		"evidence": r.Evidence, "action": action}
}

// TestSheriffMatchesCPython is the differential. Every case is a question
// this port would otherwise have to guess, and the answers were captured
// from sheriff.py before any of this package existed.
func TestSheriffMatchesCPython(t *testing.T) {
	cases := shCases()
	specs := make([]any, len(cases))
	for i, c := range cases {
		specs[i] = c.spec
	}
	probe := pyprobe.Probe{Marker: "sheriff.py", Workspace: t.TempDir()}
	var got []map[string]json.RawMessage
	probe.RunJSON(t, shPySrc, &got, pyprobe.Arg(t, specs))
	if len(got) != len(cases) {
		t.Fatalf("the probe answered %d cases for %d fixtures", len(got), len(cases))
	}

	// The Go side gets its OWN workspace: the probe writes real project
	// trees into its own, and a shared one would let the Python pass seed
	// the files the Go pass then reads — a differential that compares one
	// runtime against itself.
	ws := t.TempDir()
	statuses := map[string]bool{}
	for i, c := range cases {
		py := got[i]
		if rawErr, isErr := py["err"]; isErr {
			// sheriff.py catches everything it can raise, so a raise here
			// is a fixture that stopped meaning what it said rather than a
			// behaviour to mirror. Fail loudly instead of matching it.
			t.Errorf("%s: the probe raised %s — the fixture is broken, not the port",
				c.name, rawErr)
			continue
		}
		if c.build != nil {
			c.build(t, ws)
		}
		goVal := c.run(ws)
		if m, ok := goVal.(map[string]any); ok {
			if s, ok := m["status"].(string); ok {
				statuses[s] = true
			}
		}
		wantJSON := string(py["ok"])
		gotJSON, err := json.Marshal(goVal)
		if err != nil {
			t.Fatal(err)
		}
		if !sameJSON(t, string(gotJSON), wantJSON) {
			t.Errorf("%s:\n  CPython: %s\n  Go:      %s", c.name, wantJSON, gotJSON)
		}
	}

	// Anti-vacuity. This surface raises nothing, so the raised/answered gate
	// the heartbeat differential uses does not apply; what it can go vacuous
	// on instead is BRANCH COVERAGE — a fixture set that drifts down to
	// healthy-only still passes every comparison.
	//
	// Six, not seven: "warning" is UNREACHABLE from check_project, and this
	// gate is where that is written down so a later fixture pass does not
	// spend an afternoon trying to reach it. Both problems that lead there —
	// artifact_stale and no_artifacts — are only recorded when doing_items
	// is non-empty, and a non-empty doing_items always also records
	// items_stuck_doing, whose branch is tested first. See BACKLOG.
	want := []string{"healthy", "stuck", "dormant", "failed", "paused", "unknown"}
	for _, s := range want {
		if !statuses[s] {
			t.Errorf("no fixture reaches status %q any more — the set went vacuous", s)
		}
	}
	if len(cases) < 90 {
		t.Fatalf("the fixture set shrank to %d cases", len(cases))
	}
}

// sameJSON compares two JSON documents structurally, so a Go int marshalled
// as 5 and a Python one decoded as 5.0 are not a false failure — while a
// STRING "5" still is.
func sameJSON(t *testing.T, a, b string) bool {
	t.Helper()
	var x, y any
	if err := json.Unmarshal([]byte(a), &x); err != nil {
		t.Fatalf("go side is not JSON: %v (%s)", err, a)
	}
	if err := json.Unmarshal([]byte(b), &y); err != nil {
		t.Fatalf("python side is not JSON: %v (%s)", err, b)
	}
	return fmt.Sprintf("%#v", x) == fmt.Sprintf("%#v", y)
}

// TestResolveDormantDaysFallsBackWhenTheConfigReadItselfFails covers the one
// lane of ResolveDormantDays the differential cannot reach: Python's `from
// config import get` is inside the try, so a config backend that RAISES —
// as opposed to returning a value — lands on the 14-day default, not on 0.
// The probe can only hand the resolver values, so the battery walked a
// version that disabled dormancy on a broken config straight through (M52).
func TestResolveDormantDaysFallsBackWhenTheConfigReadItselfFails(t *testing.T) {
	boom := func() (any, error) { return nil, errors.New("no config backend") }
	if got := ResolveDormantDays(boom); got != DormantDaysDefault {
		t.Errorf("a failing config read gave %v, want %v — a raise inside the "+
			"try is the except arm, and the except arm is the DEFAULT; "+
			"answering 0 would silently disable dormancy on a broken box",
			got, DormantDaysDefault)
	}
	// A nil thunk is "no config at all", which is the same arm.
	if got := ResolveDormantDays(nil); got != DormantDaysDefault {
		t.Errorf("no config gave %v, want %v", got, DormantDaysDefault)
	}
	// The literal 14, spelled out rather than as DormantDaysDefault: an
	// expected value written with the thing under test cannot fail when
	// that thing changes (P12), and this constant is the one the operator
	// docs quote.
	if DormantDaysDefault != 14.0 {
		t.Errorf("DormantDaysDefault is %v; sheriff.py's DORMANT_DAYS_DEFAULT "+
			"is 14.0 and the dormant diagnosis quotes it", DormantDaysDefault)
	}
}

// TestReadHeartbeatStateRefusesANonObjectDocument pins the one shape the
// differential CANNOT compare, because it is the one shape where the two
// runtimes deliberately answer differently.
//
// `json.loads("[]")` gives a LIST. read_heartbeat_state's annotation says
// Optional[Dict[str, Any]] and it returns the list anyway — the annotation
// is a lie, not a contract. Go cannot return a list through an
// object-shaped signature, so this port answers absent, and the caller
// that would have received the list does `state.get(...)` on it one line
// later, which raises. The Python "answer" is a crash; the port's is a
// miss.
//
// MEASURED on this box rather than reasoned:
//
//	json.loads("[]")                     -> []          (a list)
//	sh.read_heartbeat_state()            -> []          (returned as-is)
//
// A Go test rather than a differential row, because a row asserts the two
// sides are EQUAL and here they are not. What it can still do is fail: a
// port that dropped the type assertion answers found-with-nothing-in-it,
// which is the fail-open direction — a caller would read an empty checks
// map and roll it up as healthy.
func TestReadHeartbeatStateRefusesANonObjectDocument(t *testing.T) {
	for _, body := range []string{"[]", `[{"a": 1}]`, `"a string"`, "7", "null"} {
		ws := t.TempDir()
		md := orch.MemoryDir(ws)
		mustMkdir(t, md)
		mustWrite(t, filepath.Join(md, HeartbeatStateName), body)
		if got, ok := ReadHeartbeatState(ws); ok {
			t.Errorf("a top-level %s read back as an object: %v", body, got)
		}
	}
	// Anti-vacuity: the same helper on a real object must still find it,
	// or the loop above would pass against a reader that never finds
	// anything at all.
	ws := t.TempDir()
	md := orch.MemoryDir(ws)
	mustMkdir(t, md)
	mustWrite(t, filepath.Join(md, HeartbeatStateName), `{"a": 1}`)
	if _, ok := ReadHeartbeatState(ws); !ok {
		t.Fatal("the reader finds nothing at all, so the refusals above prove nothing")
	}
}
