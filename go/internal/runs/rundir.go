package runs

import (
	"context"
	"github.com/slycrel/maro-orchestration/go/internal/pypath"
	"github.com/slycrel/maro-orchestration/go/internal/record"
	"os"
	"strings"
)

// The active run-dir, ported from runs._current_run_dir.
//
// # Why this is a context value and not a package variable
//
// Python spells it `contextvars.ContextVar`, and the comment above the
// declaration says exactly why: concurrent loops in one process
// (run_parallel_loops, DAG step fan-out) must each see their OWN run-dir
// rather than share a last-writer-wins global.
//
// A package-level `var currentRunDir string` in Go would be that global,
// with the same bug and a data race on top. Go has no goroutine-local
// storage, and that is not an omission to work around — `context.Context`
// IS the mechanism, and it happens to match ContextVar's semantics closely:
//
//   - a value set on a derived context is invisible to the parent, which is
//     `ContextVar.set` inside a `copy_context()`;
//   - the derived context goes out of scope at the end of the block, which
//     is what `scoped_run_dir`'s `reset(token)` does by hand;
//   - a goroutine handed the derived context inherits the value, which is
//     what `contextvars.copy_context().run` gives a ThreadPoolExecutor
//     worker.
//
// The one place they differ is the FAN-OUT DEFAULT, and it differs in the
// safe direction. Python's ThreadPoolExecutor workers do NOT inherit the
// submitting thread's context — the comment in runs.py warns that fan-out
// sites must remember `copy_context().run`, and forgetting is silent. A Go
// goroutine has no ambient context at all, so it can only see the run-dir a
// caller explicitly handed it. There is nothing to forget.
type runDirKey struct{}

// runDirValue distinguishes UNSET from CLEARED from SET-TO-EMPTY, which
// Python distinguishes too and by accident.
//
// `set_current_run_dir(None)` clears — handle_queue uses it for drain-batch
// hygiene between resumed handles, and if it did not clear, run N+1 would
// write its artifacts into run N's build dir. But `set_current_run_dir("")`
// does NOT clear: `Path("")` is `PosixPath('.')`, which is a perfectly good
// truthy Path, so `current_run_dir()` answers `.` and `artifact_dir` then
// creates `./build` under whatever the process CWD happens to be.
//
// Measured:
//
//	>>> from pathlib import Path
//	>>> Path(""), Path("").name
//	(PosixPath('.'), '')
//
// A single `string` in the context could not hold both answers — "" would
// have to mean one of them. So the value is a pointer: nil is None, and a
// non-nil empty string is Python's `.`, normalised at the door so every
// reader downstream sees the path pathlib would have produced.
type runDirValue struct{ dir string }

// WithRunDir is `set_current_run_dir(path)` scoped to the returned context.
//
// The empty string is NOT a clear — see runDirValue. It is normalised to
// "." here, at the one place that constructs the value, so the divergence
// cannot leak into a reader that spells its own check.
func WithRunDir(ctx context.Context, dir string) context.Context {
	if dir == "" {
		dir = "."
	}
	return context.WithValue(ctx, runDirKey{}, &runDirValue{dir: dir})
}

// WithoutRunDir is `set_current_run_dir(None)` — the clear.
//
// It must store a nil value rather than simply not setting one: an inner
// scope that declined to set anything would inherit the OUTER run-dir,
// where Python's `set(None)` genuinely blanks it for everything downstream.
func WithoutRunDir(ctx context.Context) context.Context {
	return context.WithValue(ctx, runDirKey{}, (*runDirValue)(nil))
}

// CurrentRunDir is `current_run_dir()`. The empty string is None.
//
// Python returns Optional[Path] and every caller tests `is not None`. A Go
// caller tests `!= ""`, which is the same test because WithRunDir refuses to
// store an empty string — the only way to get "" back is an unset or
// cleared context.
func CurrentRunDir(ctx context.Context) string {
	v, _ := ctx.Value(runDirKey{}).(*runDirValue)
	if v == nil {
		return ""
	}
	return v.dir
}

// CurrentHandleID is `current_handle_id()` — the handle id carved out of
// the pinned run-dir's NAME, which is `<handle_id>-<nickname>`.
//
// Python: `rd.name.split("-", 1)[0]`, and None when no run-dir is pinned.
// Two edges that a naive `strings.Split(name, "-")[0]` also gets right but
// for the wrong reason, and one it does not:
//
//	"abc123-clever-otter" -> "abc123"   (maxsplit=1 stops at the first dash)
//	"abc123"              -> "abc123"   (no dash: the whole name)
//	"-x"                  -> ""         (leading dash: an EMPTY handle id,
//	                                     which is not the same as None)
//
// The third is why the empty return is not folded into the unset return:
// a run-dir named "-x" pins a handle id of "", and a caller that treats ""
// as "no run pinned" would resume the wrong run rather than fail.
// SplitN with n=2 is Python's maxsplit=1; Cut is the same thing spelled for
// two parts, and is used because it says so.
func CurrentHandleID(ctx context.Context) (string, bool) {
	rd := CurrentRunDir(ctx)
	if rd == "" {
		return "", false
	}
	name := pypath.Name(rd)
	head, _, _ := strings.Cut(name, "-")
	return head, true
}

// ArtifactDir is `runs.artifact_dir(project, project_root_fn)` — where
// per-loop artifacts (PARTIAL files, scratchpad, step outputs) are written.
//
// With a run-dir pinned it is `<run-dir>/build`; otherwise it falls back to
// `projectRoot()/<project>/artifacts`, which is the path every call site
// used to compute inline. Either way the directory is CREATED, because
// Python mkdirs both arms and callers write into the result without
// checking.
//
// projectRoot is the injected `project_root_fn`. Python allows it to be
// None and then reads MARO_WORKSPACE (or the legacy OPENCLAW_WORKSPACE)
// itself, falling back to ~/.maro/workspace/projects — that arm is ported
// verbatim rather than made an error, because it is the one that runs when
// a helper is called outside a loop.
//
// The mkdir error is RETURNED, not swallowed. Python's `mkdir(parents=True,
// exist_ok=True)` raises, and every caller here is inside a try that logs
// and degrades; a Go caller that ignores the error gets the same path back
// and fails at the write instead, one step later and with a worse message.
func ArtifactDir(ctx context.Context, project string, projectRoot func() string) (string, error) {
	if rd := CurrentRunDir(ctx); rd != "" {
		out := pypath.Join(rd, "build")
		if err := os.MkdirAll(out, record.NewDirMode); err != nil {
			return out, err
		}
		return out, nil
	}
	var root string
	if projectRoot == nil {
		// No run-dir AND no fallback — punt to the workspace default.
		ws := os.Getenv("MARO_WORKSPACE")
		if ws == "" {
			ws = os.Getenv("OPENCLAW_WORKSPACE")
		}
		if ws != "" {
			root = pypath.Join(ws, "projects")
		} else {
			// pypath.ExpandUser, not os.UserHomeDir: Path.home() is
			// os.path.expanduser("~"), which falls back to the PASSWORD
			// DATABASE when HOME is unset. os.UserHomeDir returns an error
			// there instead, so a process started without HOME (a systemd
			// unit without the environment set) would fail here and find
			// the directory in Python.
			home, herr := pypath.ExpandUser("~")
			if herr != nil {
				return "", herr
			}
			root = pypath.Join(pypath.Join(pypath.Join(home, ".maro"),
				"workspace"), "projects")
		}
	} else {
		root = projectRoot()
	}
	// pypath.Join, not filepath.Join: `project` reaches here as a slug and an
	// ABSOLUTE one replaces the root outright in pathlib, where filepath.Join
	// would append it under the root. CPython lets the absolute slug escape,
	// so a port that clamps it disagrees about which directory the artifacts
	// of that run live in.
	out := pypath.Join(pypath.Join(root, project), "artifacts")
	if err := os.MkdirAll(out, record.NewDirMode); err != nil {
		return out, err
	}
	return out, nil
}
